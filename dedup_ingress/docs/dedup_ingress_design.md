# dedup_ingress Microarchitecture Specification

## 1. Purpose and scope

This document describes the microarchitecture of `dedup_ingress`, the component
at the head of the UDP market-data parser that drops redundant copies of packets
already confirmed complete.

The system-level design document covers what this component does within the
parser and how it connects to the stages around it. This document goes one
level deeper: the internal structure, the key design decisions and the
reasoning behind them, the timing closure method and results, and the
verification approach used to validate it.

The parser receives the same stream on several redundant feeds, so every packet
arrives more than once, on different lines, at different times. Only the first
copy to get through is useful. Every later copy wastes bandwidth in
`feed_buffer`, competes for the arbiter, and is checksummed for nothing.

A packet counts as delivered when CHECKSUM confirms it. That is the only
feedback this block acts on. A copy whose sequence ID matches a confirmed
packet is dropped. Everything else passes through untouched.

DEDUP_INGRESS does not extract the sequence ID. `seq_extract` sits in front
of it and delivers the sequence ID on `in_seq`, marked by `in_seq_valid` on the beat
that carries it. This block only compares it.

That has a consequence worth stating plainly. The sequence ID does not arrive at SOP,
it sits behind the IP and UDP headers, so at 64 bits per beat it lands in beat 3
or later. The beats ahead of it carry no identity and are forwarded. A redundant
copy therefore always gets its leading beats out of the door. They carry no
sequence ID, so clearing them is handled downstream, once the parser asserts
the signal that marks the packet invalid.

It does not reorder and it never originates a stall. Every stall comes from
downstream: when `out_ready` goes low on a feed, that feed's output register
freezes and its `in_ready` follows. A feed offering nothing is ready regardless.

Every packet is assumed to carry a sequence ID. A frame too short to reach
its own sequence field is malformed, and what this block does with one is
undefined.

## 2. Interface

### Parameters

| Parameter | Default | Meaning |
|---|---|---|
| `N_FEEDS` | 4 | Number of redundant feeds. Each has its own input and output port. |
| `DATA_W` | 64 | Datapath width in bits. |
| `SEQ_W` | 32 | Width of the sequence ID. |
| `CPT_DEPTH` | 8 | Number of completed packets held in the table. |

There is no `SEQ_OFFSET`. Where the sequence ID sits in a packet is `seq_extract`'s
business, and this block never looks at `in_data`.

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
| `in_seq` | in | `N_FEEDS` x `SEQ_W` | Sequence ID from `seq_extract`, per feed. |
| `in_seq_valid` | in | `N_FEEDS` | High on the beat carrying the sequence ID, per feed. |
| `out_valid` | out | `N_FEEDS` | Beat valid downstream. Low when the copy is dropped. |
| `out_ready` | in | `N_FEEDS` | Downstream has room, per feed. |
| `out_data` | out | `N_FEEDS` x `DATA_W` | Beat data, unchanged. |
| `out_sop` | out | `N_FEEDS` | First beat marker, unchanged. |
| `out_eop` | out | `N_FEEDS` | Last beat marker, unchanged. |
| `out_seq` | out | `N_FEEDS` x `SEQ_W` | Sequence ID of the packet on that feed. |
| `out_seq_valid` | out | `N_FEEDS` | High once the sequence ID has arrived on this packet. |
| `cmpl_valid` | in | 1 | A packet has been confirmed by CHECKSUM. |
| `cmpl_seq` | in | `SEQ_W` | Sequence ID of that packet. |

Each feed is an independent stream with its own handshake. There is no shared
port and no arbitration.

`in_data` is registered and forwarded. Nothing here reads it.

`out_seq` is carried forward so no block downstream has to parse the header
again. `out_seq_valid` says whether it means anything on this beat: the beats
ahead of the sequence ID leave with a stale value and the valid low.

`cmpl_valid` is not expected while a feed is blocked. If CHECKSUM cannot push
its output downstream it does not complete a packet. The RTL does not enforce
this, and it holds only while `out_ready` reaches CHECKSUM combinationally.

## 3. Microarchitecture

Four parts: the per feed sequence context, the completed packets table, the
comparison, and the output register.

![DEDUP_INGRESS block diagram](dedup_ingress.svg)

### Sequence context

The sequence ID appears once per packet, on the beat `in_seq_valid` marks. Every beat
after it carries none of its own, so the value has to be kept.

`seq_regs[f]` holds the sequence ID of the packet currently on feed f.
`seq_valid_regs[f]` says whether it has arrived yet. The pair is written on
`in_valid[f] && in_seq_valid[f]`, without consulting `in_ready`. Section 5
covers why. The valid is cleared on a valid SOP beat.

What gets compared in a given cycle:

```
seq_sel[f] = in_seq_valid[f] ? in_seq[f] : seq_regs[f];

if      (in_seq_valid[f]) seq_sel_valid[f] = 1'b1;
else if (in_sop[f])       seq_sel_valid[f] = 1'b0;
else                      seq_sel_valid[f] = seq_valid_regs[f];
```

The SOP term clears the valid combinationally. `seq_valid_regs` only clears at
the clock edge, so without it the first beat of a packet would be compared
against the sequence ID the previous packet left behind, and a healthy packet would
lose that beat.

### Completed packets table

The CPT holds the sequence IDs of the last `CPT_DEPTH` confirmed packets: `cpt_seq`,
`cpt_occupied` and `cpt_wr_ptr`.

Every completion writes. The pointer advances and wraps, so the oldest entry is
overwritten once the table is full. There is no full condition and nothing
waits. A repeat is not suppressed; section 5 covers why.

### Comparison

Every cycle, `seq_sel[f]` is compared against every occupied entry and against
`cmpl_seq` if a completion is arriving. Both are qualified by `seq_sel_valid[f]`,
so a feed with no sequence ID yet matches nothing.

```
cpt_match[f][e] = seq_sel_valid[f] && cpt_occupied[e] && (cpt_seq[e] == seq_sel[f]);
bypass_match[f] = seq_sel_valid[f] && cmpl_valid && (cmpl_seq == seq_sel[f]);

drop[f] = in_valid[f] && (|cpt_match[f] || bypass_match[f]);
```

The second term is the same-cycle bypass. A completion is not readable in the
table until the next cycle, so without it a copy arriving alongside its own
completion would pass through.

This is the widest logic in the block: `N_FEEDS * (CPT_DEPTH + 1)` equality
comparisons of `SEQ_W` bits, all in parallel and all independent per feed.

### Output register

The beat and its drop decision are registered at the boundary, so every output
port comes from a flop and the path into `feed_buffer` starts at a register.

```
if (out_ready[f])
    out_valid[f] <= in_valid[f] && !drop[f];

if (out_ready[f] && in_valid[f]) begin
    out_data[f] <= in_data[f];
    ...
end
```

The enable is `out_ready`. While downstream is closed that feed freezes.

`drop` is kept out of the payload enable deliberately. It is the slowest signal
in the block, and on the clock enable of the data register it would sit in front
of `DATA_W` flops per feed. It drives `out_valid` instead, one flop per feed.
The payload loads on a dropped beat too, which nothing can see.

## 4. Behaviour

### One cycle datapath

One register between input and output. `out_data`, `out_sop` and `out_eop` are
the registered input signals, unchanged. A beat presented on `in_data[f]`
appears on `out_data[f]` one cycle later.

The only thing this block does to the stream is withhold `out_valid[f]` when
that feed's copy is being dropped. A drop is not a special output state:
downstream sees `out_valid` low, the same as an idle feed.

The other registers hold state and are not in the datapath.

### The beats ahead of the sequence ID

On those beats `seq_sel_valid[f]` is low, nothing matches, and they are
forwarded whatever the table holds. They leave with `out_seq_valid[f]` low.

This is not a gap in the comparison, it is what the data allows. Those beats
carry no identity. The alternative is holding them until the sequence ID arrives,
which means a buffer and a different block.

### Mid packet kill

The comparison runs on every beat, so a packet can be killed part way through,
and in practice always is.

Feed 0 is streaming packet 100. Beats 0 to 2 carry no sequence ID and pass into
`feed_buffer`. On beat 3 the sequence ID arrives and matches. From there everything
is dropped.

That leaves beats 0 to 2 in `feed_buffer` with no EOP coming. They are not
recalled. `feed_buffer` takes no completion feedback, so they are served like
any others. The arbiter forwards them, waits for an EOP that never comes, gives
up after `HICCUP_CYCLES`, and moves on. Those beats carry no sequence ID, so
no stage downstream can identify them from the stream alone.

The cost is that hiccup timeout, paid once per kill. Acting on completions
inside `feed_buffer` was rejected: a completion always arrives after the
decision to forward has been made. Section 5 of the system level document covers
this.

### Flow control

```
in_ready[f] = ~in_valid[f] | out_ready[f] | drop[f];
```

Per feed, combinational, three terms. A feed offering nothing is ready. A beat
is taken when downstream has room, or when it is being dropped and needs no room
at all.

The drop term matters when downstream is full. A dropped beat never uses a FIFO
slot, so holding it behind a full FIFO would stall a feed for a beat that was
going to be discarded. This puts the drop on the ready path deliberately.

While `out_ready[f]` is low and a beat is offered, `in_ready[f]` is low and the
register is frozen. The beat stays on the bus and the sender presents it again.

## 5. Design decisions

### Extraction belongs in its own block

Earlier versions sliced the sequence ID out of the first beat here, with a
`SEQ_OFFSET` parameter. That work now lives in `seq_extract`.

One block owns the packet format. This one holds less per feed state, has no
view of `in_data`, and the extraction is verified once rather than once per
consumer.

The cost is the window above, and it is not a consequence of the split. A sequence ID
sitting behind the IP and UDP headers was never readable at SOP. The old version
only appeared to manage it because its guard required the field to fit inside
the first beat, which real venue layouts do not.

### Overwrite the oldest, not age out

Both policies answer the same question: how long a completed sequence ID stays
protected. Overwriting measures that in completions, a timer measures it in
cycles. Completions is the better unit, because what matters is how many packets
can go by before a late copy shows up. It is also cheaper, with no counter per
entry.

Removing an entry on EOP was also rejected. Later copies arrive after that
point, which is the whole reason the entry exists.

### A repeated completion is not suppressed

An earlier version compared `cmpl_seq` against the table and skipped the write
when the sequence ID was already held. That comparison has been removed.

A second completion can only happen if a second copy got all the way through,
and that copy could only get through if the table no longer held the first
entry. By the time the repeat arrives there is nothing left to duplicate.

Two copies arriving close together both pass this block and the second dies at
`dedup_egress`. Copies far enough apart for the entry to have gone are outside
what a table of any size can catch.

The comparison was also the most expensive thing in the block, and removing it
is where most of the timing margin came from.

### No skid buffer

A skid buffer is needed when the ready path is registered, because upstream then
acts on stale information and commits a beat with nowhere to go.

The register sits on the datapath only. `in_ready` is computed from `in_valid`,
`out_ready` and `drop` with nothing in the way, so upstream sees the decision in
the cycle it needs it. If a register is ever added to the ready path, this has
to be revisited.

### The sequence register is not part of the handshake

`seq_regs[f]` and `seq_valid_regs[f]` are written on
`in_valid[f] && in_seq_valid[f]`, with no `in_ready` term.

This is a timing decision. `in_ready` is driven by `drop`, at the far end of the
comparison, so putting it in this enable would end that path on this register's
clock enable.

Writing before acceptance is safe. While `in_ready` is low the sender holds
`in_valid`, `in_seq` and `in_seq_valid` unchanged, so the write fires every
cycle of the stall and stores the same sequence ID every time. The stall always ends,
and the sender cannot withdraw the beat, so the sequence ID written early is the one
eventually accepted.

Acceptance is still `in_valid && in_ready` on the datapath. These registers only
observe the beat while it is present.

### An explicit valid on the outgoing sequence ID

`out_seq` carries a stale value on the beats ahead of the sequence ID, because the
register holds whatever the last packet left in it.

The alternative was leaving downstream to work it out from `out_sop`. That
works, but it makes every consumer reimplement the same rule, and one that got
it wrong would compare against another packet's sequence ID with nothing to catch it.
One output bit says it directly instead.

### CPT_DEPTH of 8, parameterised

The depth sets how long a completed sequence ID stays protected, and what it needs to
cover is the gap between the first and last copy of a packet arriving on
different feeds.

Deeper is safer but costs timing, since the comparison grows as
`N_FEEDS * (CPT_DEPTH + 1)`. 8 is the starting point, and the parameter is there
so the depth can be swept against STA.

## 6. Timing

Synthesised for Xilinx Kintex-7 `xc7k160tffg676-3` at 325 MHz, a 3.077 ns
period.

### Measuring with the ports flopped

The block has a register on the datapath, so paths inside it are real register
to register paths. The ports are not, so `sta/dedup_ingress_sta_harness.sv`
instantiates the block and puts a flop on every input and output. Every path
then starts and ends at a flop, and STA measures the logic depth of the block
with no assumed budget. The flops belong to the measurement, not the design.

### Result

WNS **+0.122 ns** post synthesis, with zero warnings.

The worst path runs from `in_seq` through the comparison and the drop, and ends
at `out_valid`.

### If depth grows

Raising `CPT_DEPTH` widens the comparison and eats into the margin, both through
the extra comparators and through the higher fanout on `cpt_seq`.

## 7. Verification

29 tests under `verification/`, cocotb against Verilator.

```bash
cd verification && make
```

### The beat the sequence ID arrives on

This block does not fix that beat, `seq_extract` does, and it depends on the
venue's header layout. So it is a parameter of the tests. The directed suites
run over 3, 4 and 7, fixed so a failure reproduces without chasing a seed. The
random suite draws it per packet.

### Golden model

`dedup_ingress_common.py` models the block cycle by cycle: the table, the write
pointer, the per feed sequence registers and their valids, and the output
register.

`step` drives the DUT and the model together, reads after the clock edge, and
asserts they agree on every output. `out_seq` is checked only when
`out_seq_valid` is high, since outside that it holds whatever the last packet
left behind.

Reading after the edge is what makes the tests direct: the outputs `step`
returns belong to the beat driven in that call. `in_ready` is the exception,
being combinational, so `send_packet` holds it and uses it on the next pass.

The check runs on every cycle of every test, so a directed test only asserts the
one thing it is about.

### Coverage

**Passthrough.** Three tests. With an empty table, every beat passes. With a
table holding entries that no arriving sequence ID matches, every beat still passes.
With four feeds streaming four different packets at once, none is dropped.

**The window before the sequence ID.** The beats ahead of it are forwarded with
`out_seq_valid` low and `in_ready` high, even when the sequence ID the packet is
about to declare is already in the table. The beat the sequence ID lands on dies.

**Drop.** A completed packet's later copy is dropped, on one feed and on all at
once. The leading beats pass and the tail dies, asserted separately. Once the
sequence ID has matched, every later beat dies from `seq_regs` alone.

**Same-cycle bypass.** A copy arriving in the very cycle its completion arrives
is dropped, and the entry persists into the table from the next cycle. A third
feed matching neither path survives.

**Table.** A completion is written whether or not it matched. The pointer
advances cleanly across a full table. One completion past full takes the oldest
slot. A repeat takes a slot like any other. A copy whose sequence ID has been
overwritten passes through, asserted as intended behaviour.

**Mid packet kill.** A feed streaming a packet that completes elsewhere is cut
off from that cycle on, and the kill lands on the completion's own cycle. Its
next packet is unaffected and a second feed is untouched.

**Flow control.** `in_ready` stays high, even if `out_ready` goes to zero, when
there is no valid input on the feed. A feed
follows `out_ready` once it is offering a beat. A held beat does not move for
the whole stall. `in_ready` stays high when the beat matches a completed packet,
even if `out_ready` is zero, since that beat never goes downstream. Otherwise
`in_ready` follows `out_ready`. Stalling one feed leaves the others streaming. A beat held on the
bus during a stall still lands in `seq_regs`, and all four feeds hold their own
sequence ID at once.

**Random.** Three regimes: normal traffic with a 64 value pool, a pool of 6 so
nearly everything collides, and downstream closed 70% of the time. All checked
against the model every cycle.

### What this block cannot catch

The table remembers the last 8 completed packets. A copy arriving after 8 more
have completed finds its sequence ID gone and passes through.

The leading beats of every duplicate are forwarded, because the sequence ID has not
arrived when they go past. They carry no sequence ID, so nothing downstream
can identify them from the stream alone. Clearing them is the consumer's job,
once the parser asserts the signal that marks the packet invalid.
