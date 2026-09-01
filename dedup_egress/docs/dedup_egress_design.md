# dedup_egress Microarchitecture Specification

## 1. Purpose and scope

`dedup_egress` is the last stage of the parser before the output. It drops any
beat whose sequence number has already been confirmed complete by `CHECKSUM`.

The pipeline removes duplicates twice. `DEDUP_INGRESS` sits at the head and
stops most of them. `dedup_egress` sits at the tail and stops what got through.

What gets through is traffic that passed `DEDUP_INGRESS` before the completion
arrived. A copy of a packet is streaming through the pipeline. The completion
for an identical packet, same sequence number on a different feed, lands
afterwards. Two shapes come out of that:

- The completion lands while the copy is still crossing `DEDUP_INGRESS`. The
  rest of that copy is cut and a fragment carries on down the pipeline. The
  fragment has a SOP and no EOP.
- The completion lands after the copy has fully passed `DEDUP_INGRESS`. The
  whole copy carries on.

Neither can be stopped where it happened, because the completion is always
later than the decision to forward. `FEED_BUFFER` holds no table and takes no
completion, for the same reason. So both shapes arrive here, and both carry a
sequence number that is now in the table.

`dedup_egress` handles one stream, not several feeds. `MARKET_LINE_ARBITER`
has already serialised the feeds by the time the data reaches this block. That
makes it a much smaller block than `DEDUP_INGRESS`: one set of comparisons
instead of `N_FEEDS` sets.

This block does not extract the sequence number. Every beat arrives with one on
`in_seq`, put there by `DEDUP_INGRESS` and carried untouched through
`FEED_BUFFER` and `MARKET_LINE_ARBITER`.

## 2. Interface

### Parameters

| Parameter | Default | Meaning |
|---|---|---|
| `DATA_W` | 64 | Payload width of one beat, in bits |
| `SEQ_W` | 32 | Width of a sequence number, in bits |
| `CPT_DEPTH` | 8 | Number of entries in the completed packets table |

`CPT_DEPTH` must be at least 1. This is checked at elaboration.

### Ports

| Port | Direction | Width | Meaning |
|---|---|---|---|
| `clk` | in | 1 | Clock |
| `rst_n` | in | 1 | Active low asynchronous reset |
| `in_valid` | in | 1 | A beat is being offered |
| `in_ready` | out | 1 | The block accepts a beat this cycle |
| `in_data` | in | `DATA_W` | Beat payload |
| `in_seq` | in | `SEQ_W` | Sequence number of the packet this beat belongs to |
| `in_sop` | in | 1 | First beat of a packet |
| `in_eop` | in | 1 | Last beat of a packet |
| `out_valid` | out | 1 | A beat is being presented downstream |
| `out_ready` | in | 1 | Downstream accepts a beat this cycle |
| `out_data` | out | `DATA_W` | Beat payload |
| `out_seq` | out | `SEQ_W` | Sequence number, passed through |
| `out_sop` | out | 1 | First beat of a packet |
| `out_eop` | out | 1 | Last beat of a packet |
| `cmpl_valid` | in | 1 | A completion is being reported this cycle |
| `cmpl_seq` | in | `SEQ_W` | Sequence number of the completed packet |

### Environment assumption

Upstream holds `in_valid`, `in_data`, `in_seq`, `in_sop` and `in_eop` stable
while `in_ready` is low.

`cmpl_valid` and `cmpl_seq` are a report, not a handshake. There is no ready
signal back to `CHECKSUM`. This block never refuses a completion and never
stalls because of one.

Every beat carries a valid `in_seq`. Beats of the same packet carry the same
value.

## 3. Microarchitecture

The block is one pipeline stage. The comparisons run in the cycle a beat is
presented. The beat and the comparison results register together. The outputs
are combinational functions of that register.

### Completed packets table

The table is `cpt_seq`, `cpt_occupied` and `cpt_wr_ptr`. It holds up to
`CPT_DEPTH` sequence numbers that `CHECKSUM` has confirmed.

It is circular. A completion carrying a sequence number not already held is
written at `cpt_wr_ptr`, which then advances and wraps. Once the table has
wrapped, each new completion overwrites the oldest entry.

There is no full condition. The table is a bounded window of recent
completions, not a guaranteed record.

### Duplicate completion suppression

A packet can complete more than once. A copy that got past `DEDUP_INGRESS`
before its twin completed is still served by the arbiter and still checksummed,
so `CHECKSUM` reports the same sequence number a second time.

Writing it again would consume an entry and evict a different, still useful
one, shortening the window for no gain. So `cmpl_match` compares `cmpl_seq`
against every occupied entry, `cmpl_present` is the reduction of that, and the
write is suppressed when it is high.

This is a separate comparison from the drop decision. That one compares the
beat's `in_seq`. This one compares `cmpl_seq`. It costs `CPT_DEPTH` equality
comparisons of `SEQ_W` bits, and it sits on the completion path only.

### Comparison logic

`cpt_match` compares `in_seq` against every occupied entry. `bypass_match`
compares `in_seq` against `cmpl_seq` when `cmpl_valid` is high.

That is `CPT_DEPTH + 1` equality comparisons of `SEQ_W` bits. One stream, so
this is `N_FEEDS` times narrower than the same logic in `DEDUP_INGRESS`.

Vivado maps wide equality comparisons onto CARRY4 carry chains rather than
LUTs, which is visible in the timing report for this block.

### The pipeline cut

`cpt_match` and `bypass_match` are registered together with the beat, into
`cpt_match_q` and `bypass_match_q`. The drop decision is made on the far side
of that register, from the registered results.

The registered beat is `valid_q`, `data_q`, `sop_q`, `eop_q` and `seq_q`.

`valid_q` is enabled by `in_ready` alone, with no `in_valid` qualifier. It has
to be able to take a zero, so that when the stage is open and no beat is
offered, `valid_q` clears. Qualifying it with `in_valid` would hold the last
beat asserted and present it again on the following cycle.

Everything else is enabled by `in_valid && in_ready`, the acceptance condition
of the handshake, so a held beat and its comparison results stay together
across a stall.

## 4. Behaviour

### One cycle of latency

A beat accepted on one clock edge appears on the outputs in the next cycle.

### The drop decision

```
drop = valid_q && (|cpt_match_q || bypass_match_q)
```

An OR reduction over `CPT_DEPTH + 1` registered bits. One level of logic. All
the comparison work happened in the previous cycle.

`out_valid` is `valid_q && !drop`. A dropped beat is simply not presented
downstream. Nothing else about the beat changes: `out_data`, `out_seq`,
`out_sop` and `out_eop` are the registered values passed straight through.

### Same cycle bypass

A completion is written into the table on a clock edge, so it is only visible
from the next cycle. A beat arriving in the same cycle as its own completion
would find nothing in the table.

`bypass_match` covers that cycle. It compares the beat against the completion
directly, in parallel with the table lookup.

### No decision is remembered from one beat to the next

There is no per packet flag. Each beat is judged on its own `in_seq`.

This works because every beat carries a sequence number. A fragment's beats all
carry the sequence number that is now in the table, so they all match and all
die. If the next packet's beats carry a different sequence number, they pass.

### Ordering

In the assembled pipeline a copy and its twin cannot overlap in this block.
`MARKET_LINE_ARBITER` serves one packet at a time and does not interleave, so
the twin has fully passed `CHECKSUM` before the copy starts. The completion is
therefore normally in the table before the copy's SOP arrives, and the whole
copy dies at its first beat.

The block does not rely on that. If a completion lands part way through a copy,
the remaining beats are dropped and a fragment reaches the output. That
behaviour is deliberate and is covered by the mid packet tests.

### Flow control

```
in_ready = ~valid_q | out_ready | drop
```

Three terms.

`~valid_q`: an empty stage always accepts, since there is nothing held to
release.

`out_ready`: a held beat that is passing follows downstream.

`drop`: a held beat that is being dropped needs no room downstream, so it is
released whatever `out_ready` says. Without this term a known redundant beat
would sit in the stage waiting for a consumer that will never receive it, and
would block everything behind it.

The path is combinational back to `in_ready`, so upstream sees the decision in
the same cycle. That puts the registered drop on the ready path deliberately.

## 5. Design decisions

### No sequence extraction

The sequence number arrives on `in_seq`. This block does not slice it out of
the payload and does not hold it across a packet.

Compared to `DEDUP_INGRESS` there is no `SEQ_OFFSET`, no slicing logic, no
`seq_regs`, and no multiplexer choosing between a freshly sliced value and a
held one.

### The register cut stays

An obvious question: the front end here is simpler than in `DEDUP_INGRESS`, so
why keep the register between the comparisons and the drop?

Because that is not what the cut was for. In `DEDUP_INGRESS` the failing path
was the comparison logic feeding the OR reduction. The sequence extraction was
not the problem. That structure is unchanged here, so the cut stays.

### FIFO eviction, no full condition

The write pointer overwrites the oldest entry on wrap. Entries carry no
timestamp and are never aged out.

The table has no full condition and this block never applies backpressure
because of it. A completion arriving when the table is full overwrites the
oldest entry and the pipeline keeps moving. The alternative, stalling until an
entry frees, would put the completion path in control of the datapath. Nothing
frees an entry on its own, so the stall would never end.

The consequence is a bounded window. A copy arriving after its completion has
been evicted passes through. That is a known limitation, and it is tested as
intended behaviour rather than left as a surprise.

### Skid buffer

`in_ready` is combinational from `out_ready`. The path is short: `out_ready`
and the registered `drop` into an OR. The stage already holds one beat, and the
three term ready equation lets it release that beat either downstream or into
the drop.

A skid buffer would break that path, at the cost of an extra beat of storage
and an extra cycle of latency on the drain side. It is left out at this stage.
The decision can be revisited once the pipeline is assembled and timed after
place and route, where the real load on `in_ready` is known.

### CPT_DEPTH of 8

Eight entries is the default, not a fixed choice. It is deep enough to cover
the gap between a completion and a late copy under normal traffic, and small
enough that the comparison logic stays cheap.

The parameter is there so the depth can be raised if the assembled pipeline
shows copies arriving later than eight completions apart.

## 6. Timing

### Method

Synthesis and static timing analysis run from `sta/` with
`vivado -mode batch -source run.tcl`. Target part is a Xilinx Kintex-7
`xc7k160tffg676-3` at 325 MHz, a period of 3.077 ns.

The toplevel is `dedup_egress_sta_harness`, not `dedup_egress`. The harness
flops every input and every output of the block.

The harness is needed even though `dedup_egress` holds a register internally.
In a standalone synthesis every port of the block sits on the I/O boundary, and
no input or output delay is declared, so any path that starts or ends at a port
is not timed. Flopping every port turns those paths into register to register
paths and brings them into the report.

The harness flops are an artefact of the measurement and are not part of the
design.

### Result

**WNS +0.257 ns** at 325 MHz, post synthesis, zero warnings.

The worst path is not on the datapath. It is the completion write enable:

| | |
|---|---|
| Source | `cpt_seq_reg[4][4]/C` |
| Destination | `cpt_seq_reg[0][0]/CE` |
| Data path delay | 2.448 ns, logic 0.932 ns, route 1.516 ns |
| Logic levels | 6: three CARRY4, two LUT6, one LUT4 |

That path is `cpt_seq` into the equality comparison against `cmpl_seq`, into
`cmpl_match`, into `cmpl_present`, into the write enable of `cpt_seq`. It is
the duplicate completion suppression described in section 3, and the three
CARRY4 are the wide equality comparisons.

The beat path does not appear near the top of the report. The register cut did
its job.

### Utilisation

228 LUTs and 608 registers on the synthesised harness. Most of the registers
are the harness flops, not the block.

## 7. Verification

The suite is cocotb driving Verilator, in `verification/`. Run it with `make`.
A single file runs with `make MODULE=<name>`. Waveforms are off by default and
enabled with `make TRACE=1 MODULE=<name>`. Verilator 5.036 segfaults when
tracing the long random runs.

There is no wrapper module. `dedup_egress` has no packed two dimensional ports,
so cocotb drives it directly and it is the toplevel.

### Golden model

`GoldenDedupEgress` in `dedup_egress_common.py` is a cycle accurate model of
the block, written in Python. It has the same state: the table, the registered
beat, and the registered comparison results.

It has two methods. `drive` puts this cycle's inputs on the model, the way
wires hold them. `evaluate` computes `in_ready` and the match results from the
flops and the table as they stand, then loads the flops and writes the table,
then reports the outputs. The order matters: `in_ready` and the match results
are combinational, so they have to be computed before anything is written.

`DedupEgressTB.step` runs one cycle. It drives the staged stimulus to the DUT
and to the model at the same point, waits for the clock edge, reads the DUT at
the `ReadOnly` phase, and asserts the two agree on `out_valid`, `in_ready`,
and, when a beat is being presented, on `out_seq`, `out_data`, `out_sop` and
`out_eop`.

Reading after the edge is what makes the tests direct. The outputs `step`
returns belong to the beat driven in that same call, so a test presents a beat
and looks at the result of that one `step`. Reading before the edge would
return the previous beat, and a test would need a second `step` to see the one
it cares about. That second cycle drives nothing, so any check for a beat being
dropped would pass on an empty cycle whether the block worked or not.

`in_ready` is the exception. It is combinational from `valid_q`, `out_ready`
and `drop`, and it applies to whatever beat is being offered at that moment.
The stream never stops, so the value read after the edge is the one for the
beat about to be driven, not the one for the beat that has already been
accepted. `send_packet` holds it and uses it on the next pass.

The check in `step` runs on every cycle of every test, including the directed
ones. The directed tests then make their own claims on top of it.

### Directed coverage

| File | What it covers |
|---|---|
| `test_passthrough.py` | An empty table passes everything. A populated table does not drop unrelated sequence numbers. |
| `test_dedup_egress_core.py` | A later copy is dropped. The same cycle bypass. Table and bypass live in the same cycle. A live completion does not drop an unrelated beat. |
| `test_cpt.py` | A completion lands with no traffic present. The write pointer advances. The table wraps and evicts the oldest. A straggler outside the window passes. A repeated completion is not rewritten and does not move the pointer. |
| `test_midpacket.py` | A completion arriving part way through a packet drops the rest of it. The next packet is unaffected. |
| `test_backpressure.py` | An empty stage always accepts. `in_ready` follows `out_ready` when a beat is held. A dropped beat is accepted with the consumer closed. A whole packet survives backpressure. |

### Constrained random

`test_random.py` runs two configurations against the golden model. Both draw
sequence numbers from a small pool so duplicates and completions collide often,
and draw completions from packets that have actually been offered so the table
fills with plausible values rather than noise.

The second configuration uses a pool of six, so almost every packet is a
duplicate of something.

It is also the only test that drives beats at full rate under random
backpressure, so the pipeline register and the ready path are exercised hardest
here.
