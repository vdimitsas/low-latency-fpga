# dedup_ingress Microarchitecture Specification

## 1. Purpose and scope

This document describes the microarchitecture of `dedup_ingress`, the component within
the UDP market-data parser that drops redundant copies of packets already
confirmed downstream.

The system-level design document covers what this component does within the
parser and how it connects to the stages around it. This document goes one
level deeper: the internal structure, the key design decisions and the
reasoning behind them, the timing closure method and results, and the
verification approach used to validate it.

It is written for engineers reviewing or modifying this component directly:
readers who need to understand not just its behaviour at the interface, but why
the RTL is built the way it is.

The parser receives the same market data stream on several redundant feeds.
Every packet therefore arrives more than once, on different lines, at different
times. Only the first copy to get through is useful. Every later copy is dead
weight, and if it reaches the rest of the pipeline it wastes bandwidth in
`feed_buffer`, competes for the arbiter, and is checksummed for nothing.

DEDUP_INGRESS removes those copies. It sits at the front of the pipeline, between the
incoming feeds and `feed_buffer`, and it is the only block that knows a packet
has already been delivered.

A packet counts as delivered when CHECKSUM confirms it. That is the single
source of truth, and it is the only feedback DEDUP_INGRESS acts on. A copy whose
sequence number matches a confirmed packet is dropped. Everything else passes
through untouched.

DEDUP_INGRESS does not act on the arbiter's `invalidate_feed`. Invalidation means one
feed's copy was abandoned, not that the packet was delivered. If DEDUP_INGRESS dropped
that sequence number on every feed, a healthy copy on another line would be
discarded with it, and a packet that was still recoverable would be lost.
Invalidation stays scoped to the feed it happened on, and `feed_buffer` handles
it with a sticky per feed drop.

DEDUP_INGRESS does not reorder. It holds one beat per feed in a pipeline
register, added for timing. There is no FIFO and no queue.

The block never originates a stall. No internal condition, full table included,
holds up a feed. Every stall it applies comes from downstream: when `out_ready`
goes low on a feed that is holding a beat, that feed's `in_ready` follows and
upstream is held off. A feed holding nothing accepts regardless.

It has no view of packet order or gaps in the sequence. Detecting a missing
packet belongs to FIX_TRACKER and TIMER, not here.

## 2. Interface

### Parameters

| Parameter | Default | Meaning |
|---|---|---|
| `N_FEEDS` | 4 | Number of redundant feeds. Each has its own input and output port. |
| `DATA_W` | 64 | Datapath width in bits. |
| `SEQ_W` | 32 | Width of the sequence number field. |
| `SEQ_OFFSET` | 0 | Byte offset of the sequence number inside the first beat. |
| `CPT_DEPTH` | 8 | Number of completed packets held in the table. |

`SEQ_OFFSET` and `SEQ_W` exist because venues place the sequence number
differently. They must satisfy `SEQ_OFFSET*8 + SEQ_W <= DATA_W`, so the field
lands inside the first beat. A compile-time guard enforces this.

### Ports

| Port | Dir | Width | Meaning |
|---|---|---|---|
| `clk` | in | 1 | Clock. |
| `rst_n` | in | 1 | Active low asynchronous reset. |
| `in_valid` | in | `N_FEEDS` | Beat valid, per feed. |
| `in_ready` | out | `N_FEEDS` | Beat accepted, per feed. |
| `in_data` | in | `N_FEEDS` x `DATA_W` | Beat data, per feed. |
| `in_sop` | in | `N_FEEDS` | First beat of a packet, per feed. |
| `in_eop` | in | `N_FEEDS` | Last beat of a packet, per feed. |
| `out_valid` | out | `N_FEEDS` | Beat valid downstream, per feed. Low when the copy is dropped. |
| `out_ready` | in | `N_FEEDS` | Downstream has room, per feed. |
| `out_data` | out | `N_FEEDS` x `DATA_W` | Beat data, unchanged. |
| `out_sop` | out | `N_FEEDS` | First beat marker, unchanged. |
| `out_eop` | out | `N_FEEDS` | Last beat marker, unchanged. |
| `out_seq` | out | `N_FEEDS` x `SEQ_W` | Sequence number of the packet on that feed. |
| `cmpl_valid` | in | 1 | A packet has been confirmed by CHECKSUM. |
| `cmpl_seq` | in | `SEQ_W` | Sequence number of that packet. |

Each feed is an independent stream with its own handshake. There is no shared
port and no arbitration.

`out_seq` is produced so that no block downstream has to parse the header
again. Every stage behind this one carries it through to `dedup_egress`, which
needs it to make the final drop decision.

### Environment assumption

`cmpl_valid` is not expected while a feed is blocked. If CHECKSUM cannot push
its output downstream it does not complete a packet, so it does not send
feedback. The RTL does not enforce this: a completion arriving while
`out_ready` is low would still be written to the table. This assumption holds
only while `out_ready` reaches CHECKSUM combinationally. If a register is ever
added to that path, it must be revisited.

## 3. Microarchitecture

DEDUP_INGRESS has three parts: sequence extraction with the per-feed context
registers, the completed packets table, and the comparator tree.

![DEDUP_INGRESS block diagram](dedup_ingress.svg)

### Sequence extraction and `seq_regs`

The sequence number appears once, in the first beat of a packet. It is sliced
out combinationally at `SEQ_OFFSET` with width `SEQ_W`, giving
`seq_extract[f]`.

Later beats of the same packet carry no sequence number, so the value has to be
kept. `seq_regs[f]` holds the sequence number of the packet currently on feed
f. It is written on any valid SOP beat, that is when
`in_valid[f] && in_sop[f]`, without consulting `in_ready`. Section 5 covers
why. It holds until the next SOP on that feed.

The value used for comparison in a given cycle is `seq_sel[f]`. On an SOP beat
it is the freshly extracted value, because `seq_regs[f]` has not been written
yet. On every other beat it is `seq_regs[f]`.

```
seq_sel[f] = in_sop[f] ? seq_extract[f] : seq_regs[f];
```

### Completed packets table

The CPT holds the sequence numbers of the last `CPT_DEPTH` confirmed packets.
It has three pieces of state: `cpt_seq`, the sequence numbers, `cpt_occupied`,
one bit per entry, and `cpt_wr_ptr`, the write pointer.

A completion writes unless its sequence number is already held. The pointer
advances and wraps at `CPT_DEPTH`, so the oldest entry is overwritten once the
table is full. There is no full condition, and nothing waits.

The suppression exists because a packet can complete more than once. A copy
that got past this block before its twin completed is still buffered, still
served by the arbiter, and still checksummed, so CHECKSUM raises a second
completion for a sequence number the table already holds. Writing it again
would consume an entry and evict a different, still useful value, shortening
the window for no gain. `cmpl_present` is a separate comparison from the drop
decision: that one compares each feed's `seq_sel`, this one compares
`cmpl_seq`.

```
cmpl_match[e] = cpt_occupied[e] && (cpt_seq[e] == cmpl_seq);
cmpl_present  = |cmpl_match;
```

When `cmpl_present` is high the write is skipped and `cpt_wr_ptr` does not
move.

### Comparator tree

The tree runs every cycle. For each feed, `seq_sel[f]` is compared against
every occupied CPT entry and against `cmpl_seq` if a completion is arriving
this cycle.

```
cpt_match[f][e] = cpt_occupied[e] && (cpt_seq[e] == seq_sel[f]);
bypass_match[f] = cmpl_valid && (cmpl_seq == seq_sel[f]);
```

The second term is the same-cycle bypass. A completion is not readable in the
table until the next cycle, so without it a copy arriving in the same cycle as
its own completion would pass through.

This is the widest logic in the block: `N_FEEDS * (CPT_DEPTH + 1)` equality
comparisons of `SEQ_W` bits each, all in parallel. Each feed's comparisons are
independent of every other feed's.

The results are registered. The drop decision is made in the next cycle, from
the registered results, and the subsection below covers that.

### The pipeline cut

The comparator results are registered together with the beat they belong to.

```
valid_q[f]        <= in_valid[f];   // enabled by in_ready
data_q[f]         <= in_data[f];    // enabled by in_valid && in_ready
seq_q[f]          <= seq_sel[f];
cpt_match_q[f]    <= cpt_match[f];
bypass_match_q[f] <= bypass_match[f];
```

The drop decision is then one OR reduction on the far side:

```
drop[f] = valid_q[f] && (|cpt_match_q[f] || bypass_match_q[f]);
```

This splits the block into two cycles. The comparators run in the cycle a beat
is presented. The OR reduction, the drop, the ready path and the outputs run in
the next. The wide equality comparisons and the logic that depends on them no
longer share a cycle.

`valid_q` is enabled by `in_ready` alone. When `in_ready` is high and
`in_valid` is low, `valid_q` takes a zero and the stage goes empty. The payload
and the match results are enabled by `in_valid && in_ready`, so a held beat and
its results stay stable together across a stall.

## 4. Behaviour

### One cycle datapath

One register sits between input and output. `out_data`, `out_sop` and
`out_eop` are the registered input signals, unchanged in value. `out_seq` is
`seq_q`. A beat presented on `in_data[f]` appears on `out_data[f]` one cycle
later.

The only thing DEDUP_INGRESS does to the stream is withhold `out_valid[f]` when that
feed's copy is being dropped:

```
out_valid[f] = valid_q[f] && !drop[f];
```

The other registers in the block, `seq_regs`, `cpt_seq`, `cpt_occupied` and
`cpt_wr_ptr`, hold state and are not in the datapath.

### The drop decision

A copy is dropped when its sequence number matches a packet already confirmed
by CHECKSUM. The match is against the table, or against a completion arriving
in the same cycle.

A drop is not a special output state. Downstream sees `out_valid[f]` low, which
is the same as an idle feed. The beat is discarded and nothing marks it.

### Same-cycle bypass

The CPT write happens on the clock edge, so a completion arriving in cycle N is
only readable in the table from cycle N+1. Without the bypass, a copy arriving
in cycle N alongside its own completion would be forwarded, and only the copies
from N+1 onwards would be dropped.

The bypass compares `seq_sel[f]` against `cmpl_seq` directly, in the same cycle
the completion arrives. The result is registered with the beat, so the drop
appears on `out_valid` one cycle later, along with the beat it applies to.

### Mid packet kill

The comparison runs on every beat, not only on SOP. So a packet can be killed
part way through.

For example, feed 0 is streaming packet 100. Beats 1 and 2 pass through and
land in `feed_buffer`. On beat 3, another feed's copy of packet 100 completes.
From that cycle `seq_sel[0]` matches, so beat 3 and everything after it is
dropped.

That leaves beats 1 and 2 sitting in `feed_buffer` with no EOP coming. They are
not recalled. `feed_buffer` holds no completed packets table and takes no
completion feedback, so those beats are served like any others. The arbiter
forwards them, then waits for an EOP that never comes, gives up after
`HICCUP_CYCLES`, and moves on. The beats themselves are dropped at
`dedup_egress`, which sees a sequence number that has already completed.

The cost of this is the arbiter's hiccup timeout, paid once per mid packet
kill. The alternative, acting on completions inside `feed_buffer`, was
rejected: a completion always arrives after the decision to forward has been
made, so `feed_buffer` would either have to cut a packet in half, leaving the
arbiter holding a fragment with nothing behind it to clean up, or commit at SOP
and let the rest through anyway. Section 5 of the system level document covers
this.

The arbiter cannot do this. `invalidate_feed` only fires for the feed the
arbiter is serving. Here it may never have selected feed 0 at all.

### Flow control

```
in_ready[f] = ~valid_q[f] | out_ready[f] | drop[f];
```

Per feed, combinational, three terms.

A feed holding nothing accepts, because there is nothing to release first. A
feed holding a beat releases it when downstream has room, or when the beat is
being dropped and therefore needs no room at all.

The drop term matters when downstream is full. A dropped beat never reaches
`feed_buffer` and never uses a FIFO slot, so holding it behind a full FIFO
would stall a feed for a beat that was going to be discarded. This puts the
drop on the ready path deliberately.

## 5. Design decisions

### Completions, not invalidation

DEDUP_INGRESS acts only on CHECKSUM completions. It ignores the arbiter's
`invalidate_feed`.

Invalidation means one feed's copy was abandoned. It does not mean the packet
was delivered. If DEDUP_INGRESS dropped that sequence number everywhere, a healthy copy
on another feed would be discarded with it, and a packet that could still have
been served would be lost.

A completion is the only signal that says a packet is truly finished.

### Evict oldest, not age out

Two policies were considered for removing entries from the CPT: overwrite the
oldest when a new completion arrives, or hold each entry for a fixed number of
cycles and then clear it.

Both answer the same question, how long a completed sequence number stays
protected. Evict-oldest measures that in completions, the timer measures it in
cycles. Completions is the better unit, because what matters is how many
packets can go by before a late copy shows up, and that is a packet count. It
is also cheaper: no counter per entry.

Removing an entry when the packet's EOP is seen was also considered and
rejected. Later copies arrive on other feeds after that point, which is the
whole reason the entry exists.

### No stalling on a full table

The table never signals full and never holds anything up. A completion always
writes.

Backpressuring CHECKSUM because a bookkeeping table is full would push
backpressure the wrong way through the pipeline and would stall the output path
for no useful reason.

The cost is that a late copy whose sequence number has been evicted passes
through. That is accepted, see section 7.

### No skid buffer

A skid buffer is needed when a beat can arrive that cannot be taken. That
happens when the ready path is registered, because upstream then acts on stale
information and commits a beat that has nowhere to go.

The pipeline register added for timing sits on the datapath only. The ready
path stays combinational: `in_ready` is computed from `valid_q`, `out_ready`
and `drop` with no register in the way, so upstream sees the decision in the
cycle it needs it. Upstream never commits a beat DEDUP_INGRESS cannot take, so
there is nothing to absorb.

If a register is ever added to the ready path, this has to be revisited.

### The sequence register is not part of the handshake

`seq_regs[f]` is written on `in_valid[f] && in_sop[f]`. There is no `in_ready`
term, so a SOP beat is captured whether or not it is accepted that cycle.

This is a timing decision. `in_ready` is driven by `drop`, which sits at the far
end of the comparator tree. Putting `in_ready` in this enable would put the
whole tree on a path ending at this register's clock enable, and that was the
path that failed STA at WNS -0.442 ns.

Writing before acceptance is safe. While `in_ready` is low the sender holds
`in_valid` and `in_data` unchanged, so the SOP beat stays on the bus. The write
fires every cycle of the stall and stores the same number every time.

Example. A SOP carrying sequence 100 arrives on feed 0 while `in_ready` is low.
`seq_regs[0]` is written with 100 straight away, before the beat is accepted.
The stall lasts three cycles, and the same 100 is written on each of them. On
the fourth cycle `in_ready` goes high and the beat is accepted. `seq_regs[0]`
already holds 100, which is the right value, so the beats that follow compare
against the right number.

The stall always ends. Nothing inside this block can hold a feed forever, and
the sender is not allowed to withdraw the beat, so the SOP that was written
early is always the SOP that eventually gets accepted.

`seq_regs` is not part of the acceptance protocol. Acceptance is still
`in_valid && in_ready` on the datapath. This register only observes the SOP beat
while it is present.

### CPT_DEPTH of 8, parameterised

The depth sets how long a completed sequence number stays protected. What it
needs to cover is the gap between the first and last copy of the same packet
arriving on different feeds.

Deeper is safer against late copies but costs timing, since the comparator tree
grows as `N_FEEDS * (CPT_DEPTH + 1)`. 8 is the starting point. The parameter is
there so the depth can be swept against STA.

## 6. Timing

Synthesised for Xilinx Kintex-7 `xc7k160tffg676-3` at 325 MHz, a 3.077 ns
period.

### Measuring a block with no registers in the datapath

The block now has a register on the datapath, so some paths through it are real
register to register paths and STA can time them. The ports are still
unconstrained, though. A path from `in_data` to the pipeline register starts at
an input port with no arrival time, and a path from the register to `out_data`
ends at an output port with no required time. Neither is timed, and the path
from `cmpl_seq` into the comparators is one of them.

The alternative is to constrain the ports with `set_input_delay` and
`set_output_delay`. That works, but it measures the block against a budget
chosen by hand, so the answer depends on the numbers picked.

The method used here is `sta/dedup_ingress_sta_harness.sv`. It instantiates `dedup_ingress` and
puts a register on every input and every output. The combinational datapath
becomes a real register to register path, so STA measures the logic depth of
the block itself with no assumed budget. The flops belong to the measurement,
not to the design, and the harness is not part of the pipeline.

### Result

WNS +0.240 ns post synthesis, with zero warnings.

Before the pipeline register the same design measured WNS -0.442 ns. The
failing path ran from `cmpl_seq` through a 32 bit equality, the OR reduction,
`drop` and `in_ready`, and ended on the clock enable of `seq_regs`:

```
Source:       cmpl_seq_q_reg[3]/C
Destination:  u_dedup_ingress/seq_regs_reg[0][0]/CE
Data Path Delay: 3.105 ns  (logic 0.975 ns, route 2.130 ns)
Logic Levels: 7  (CARRY4=3, LUT2=1, LUT4=1, LUT6=2)
```

Two thirds of that delay is routing. The three CARRY4s are the equality
comparison, which Vivado maps onto the carry chain rather than LUTs.

The cut removed that path in two ways. The comparator results are now
registered, so the OR reduction and the drop no longer share a cycle with the
comparison. And `seq_regs` no longer takes `in_ready` in its enable, so the tree
no longer ends on that clock enable at all.

### What the completion comparison cost

Before `cmpl_present` was added the same path measured WNS +0.513 ns, with a
data path delay of 2.425 ns (logic 0.971 ns, route 1.454 ns). The logic barely
moved. The whole 0.334 ns went into routing.

The reason is fanout. Every `cpt_seq` bit used to drive `N_FEEDS` comparators.
It now drives `N_FEEDS + 1`, because `cmpl_present` reads the same registers.
More loads on a net means a longer estimated route, and those nets sit on the
critical path. The new comparison never becomes critical itself; it makes the
existing path more expensive to reach.

### If depth grows

The margin is 0.240 ns on a 3.077 ns period. Raising `CPT_DEPTH` widens the
tree and eats into it, both through the extra comparators and through the
higher fanout on `cpt_seq`.

The cut between the comparators and the OR reduction has already been taken,
and it cost one cycle of latency. The next one, if it is ever needed, splits the
equality itself: compare the low half of `SEQ_W` in one cycle and the high half
in the next, then AND the results. That halves the carry chain and costs another
cycle.

## 7. Verification

25 tests under `verification/`, run with cocotb against Verilator:

```
cd verification && make
```

The RTL was mutated to check the tests catch what they claim to.

### Golden model

`dedup_ingress_common.py` holds a cycle accurate model of the block: the CPT,
the write pointer, the per feed sequence registers, and the pipeline register
with its match results.

It has two methods. `drive` puts this cycle's inputs on the model, the way
wires hold them. `evaluate` computes `in_ready`, the sequence context and the
comparator results from the flops and the table as they stand, then loads the
flops and writes the table, then reports the outputs. The order matters: those
three are combinational, so they have to be computed before anything is
written.

`DedupIngressTB.step` runs one cycle. It drives the staged stimulus to the DUT
and to the model at the same point, waits for the clock edge, reads the DUT at
the `ReadOnly` phase, and asserts the two agree on `in_ready`, `out_valid` and,
when a beat is being presented, on `out_data`, `out_sop`, `out_eop` and
`out_seq`.

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

The check runs on every cycle of every test, directed and random alike, so a
directed test only has to set up its scenario and assert the one thing it is
about.

### Directed coverage

**Passthrough.** An empty table, and a populated table holding sequence numbers
that never match, both leave every beat untouched. Four packets in flight on
four feeds at once also pass.

**Drop.** A completed packet's later copy is dropped, on one feed and on all
feeds at once.

**Same-cycle bypass.** A copy arriving in the very cycle its completion arrives
is dropped, and the entry then persists into the table from the next cycle.

**Both paths together.** One feed matching a table entry and another matching
the live completion in the same cycle, with a third feed matching neither and
surviving.

**Table.** A completion is written whether or not it matched anything that
cycle. The write pointer advances cleanly across a full table. One completion
past full evicts the oldest entry, and everything newer is still held.

**Repeated completion.** A completion whose sequence number the table already
holds evicts nothing, and does not move the write pointer. The two are separate
failures and get a test each. The first fills the table, repeats the newest
entry three times, and checks every original is still held. The second fills the
table, repeats the newest entry once, then completes one genuinely new sequence
number, and checks that exactly one eviction happened rather than two. A write
that was skipped but still advanced the pointer passes the first test and fails
the second.

**Late copy.** A copy whose sequence number has been evicted passes through.
Asserted as intended behaviour, so a future change to the eviction policy has
to be deliberate.

**Mid packet kill.** A feed streaming a packet that completes elsewhere is cut
off from that cycle on. Its next packet is unaffected, and a second feed
carrying a different packet is untouched.

**Flow control.** A stage holding nothing accepts even with downstream closed.
A feed follows `out_ready` once it is holding a beat. A dropped copy is
accepted even when downstream is closed. A passing copy is not. Stalling one
feed leaves the others streaming. A SOP held on the bus during a stall still
lands in `seq_regs`, checked on every feed, and all four feeds hold their own
number at the same time.

### Constrained random

Two regimes, both checked against the golden model every cycle.

The first uses a 64 value sequence pool, so duplicates are occasional and most
traffic passes. It checks the block over 3000 cycles of normal traffic.

The second uses a pool of 6, so nearly everything collides and the table stays
saturated. It hammers the comparator and the eviction path, which the first
barely touches.

Both apply independent per feed backpressure and keep SOP and EOP coherent per
feed.

### What this block cannot catch

The table only remembers the last 8 completed packets. If a copy arrives very
late, after 8 more packets have completed, its sequence number is gone from the
table and the copy passes through.

This is expected. It comes from the fixed table size. The straggler test checks
it, so if the table ever changes, someone has to change that test on purpose.
