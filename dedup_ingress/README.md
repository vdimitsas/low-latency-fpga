# dedup_ingress

The **dedup_ingress** block drops redundant copies of packets that have already been
confirmed complete downstream. It is the first stage of the `udp_parser`
pipeline, sitting between the incoming feeds and `feed_buffer`. A second block,
`dedup_egress`, makes the final cut at the other end of the pipeline.

## Context

The parser takes several redundant market data feeds carrying the same stream.
The same packet therefore arrives more than once, on different lines, at
different times. Once one copy has made it through and passed its checksum,
every later copy of that packet is dead weight and must not reach the rest of
the pipeline.

## Behaviour

- **Cut-through.** There is no register in the datapath. Data, boundary
  markers and the extracted sequence number pass from input port to output port
  in the same cycle. The only thing the block does to the stream is withhold
  `out_valid` on a feed whose packet has already completed.
- **Inline sequence extraction.** The sequence number appears only in the first
  beat of a packet, at a parameterised byte offset and width. It is sliced out
  combinationally at SOP, latched per feed in `seq_regs`, and held for the rest
  of the packet. It is re-emitted on `out_seq` so no downstream block has to
  parse the header again.
- **Completed packets table.** Completions arriving on `cmpl_valid` and
  `cmpl_seq` are written into a circular table of parameterised depth. The
  oldest entry is overwritten once the table has wrapped. There is no full
  condition and the block never stalls. A completion whose sequence number the
  table already holds is not written again: a packet can complete more than
  once, and rewriting it would evict a different, still useful entry.
- **Comparator tree.** Every cycle, each feed's current sequence number is
  compared against every table entry and against the completion arriving that
  same cycle. The same-cycle path catches a copy whose completion has not yet
  been written to the table.
- **Per feed flow control.** `in_ready` follows `out_ready` directly, per feed.
  The comparator tree is deliberately kept out of that path: a dropped copy
  consumes nothing downstream, so the drop decision has no bearing on whether
  upstream may push.

## Scope

The block acts only on completions. It does not act on the arbiter's
`invalidate_feed`, because an invalidated copy on one feed says nothing about
whether the packet itself was delivered, and dropping that sequence number
everywhere could discard a healthy copy on another line.

The table is a bounded window of recent completions, not a guaranteed record.
A straggling copy whose sequence number has already been evicted will pass
through. That is a known limit of the window size.

## Timing

- Synthesised for **Xilinx Kintex-7 `xc7k160tffg676-3`** at **325 MHz**.
- **WNS +0.179 ns** post synthesis.
- Worst path runs from the input data through the comparator tree to
  `out_valid`, 7 logic levels.

Because the datapath is cut-through, a standalone synthesis of `dedup_ingress` contains
no register to register path through the comparator tree and STA has nothing to
time. The number above is measured through `sta/dedup_ingress_sta_harness.sv`, which
flops every port of the block so that the combinational datapath becomes a real
timed path. Those flops exist only for the measurement and are not part of the
design.

## Verification

23 tests under `verification/`, run with cocotb against Verilator:

```
cd verification && make
```

Directed coverage:

- **Passthrough.** An empty table, and a populated table holding sequence
  numbers that never match, both leave every beat untouched.
- **Drop.** A completed packet's later copy is dropped, on one feed and on all
  feeds at once.
- **Same-cycle bypass.** A copy arriving in the very cycle its completion
  arrives is dropped before the table write is visible, and the entry then
  persists into the table.
- **Both paths together.** One feed matching a table entry and another matching
  the live completion in the same cycle, with a third feed matching neither.
- **Table.** A completion is written whether or not it matched anything that
  cycle, the write pointer advances cleanly across a full table, and one
  completion past full evicts the oldest entry.
- **Repeated completion.** A completion the table already holds evicts nothing
  and does not move the write pointer, checked separately so a write that is
  skipped but still advances the pointer is caught.
- **Bounded window.** A straggler whose sequence number has been evicted passes
  through, asserted as intended behaviour so a future change to the eviction
  policy has to be deliberate.
- **Mid packet kill.** A feed streaming a packet that completes elsewhere is
  cut off from that cycle on, its following packet is unaffected, and a second
  feed carrying a different packet is untouched.
- **Flow control.** `in_ready` follows `out_ready` per feed and does not move
  when a copy is dropped, stalling one feed leaves the others streaming, and a
  SOP presented while ready is low does not update that feed's `seq_regs`.

Constrained random runs two regimes against a cycle accurate golden model that
checks `out_valid`, `in_ready` and `out_seq` every cycle: a wide sequence pool
where most traffic passes, and a pool of six where nearly everything collides
and the table stays saturated.

The suite was mutation checked. Making the completion write unconditional fails
4 tests: both repeated completion tests and both random regimes.

Waveforms are off by default because tracing the long random tests segfaults
Verilator 5.036. Enable them on a short run with
`make TRACE=1 MODULE=test_midpacket`.

## Layout

```
dedup_ingress/
├── README.md
├── rtl/
│   └── dedup_ingress.sv                     # the dedup_ingress RTL
├── sta/
│   └── dedup_ingress_sta_harness.sv         # synthesis harness, not design RTL
├── docs/
│   └── dedup_ingress.svg                    # block diagram
└── verification/
    ├── Makefile
    ├── dedup_ingress_tb_wrap.sv             # flattens the packed ports for cocotb
    ├── dedup_ingress_common.py              # driver, sampler and golden model
    ├── test_passthrough.py
    ├── test_dedup_ingress_core.py
    ├── test_cpt.py
    ├── test_midpacket.py
    ├── test_backpressure.py
    └── test_random.py
```
