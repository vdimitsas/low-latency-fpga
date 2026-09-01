# dedup_egress

The **dedup_egress** block drops any beat whose packet has already been
confirmed complete by `checksum`. It is the last stage of the `udp_parser`
pipeline, sitting between `checksum` and the output.

## Context

The pipeline removes duplicates twice. `dedup_ingress` stops most of them at
the head. What reaches this block is traffic that got past that point because
the completion arrived too late to stop it.

A copy of a packet is streaming through the pipeline, and the completion for an
identical packet, same sequence number on another feed, lands afterwards. If it
lands while the copy is still crossing `dedup_ingress`, a fragment carries on
down the pipeline. If it lands after the copy has fully passed, the whole copy
carries on. Both end up here.

## Behaviour

One stream, not several feeds. `market_line_arbiter` has already serialised
them, so this block holds one set of comparisons rather than one per feed.

Every beat arrives with its sequence number on `in_seq`, so nothing is parsed
out of the payload and nothing is held across a packet. Each beat is judged on
its own.

The datapath has one pipeline register. A beat and its comparison results are
registered together, so a beat presented on `in_data` appears on `out_data` one
cycle later. The only thing the block does to the stream is withhold
`out_valid` on a beat whose packet has already completed.

Completions are kept in a circular table with FIFO eviction. A beat is compared
against every occupied entry, and against a completion arriving in the same
cycle, which the table write has not yet caught.

How each of those works is in
[`docs/dedup_egress_design.md`](docs/dedup_egress_design.md), sections 3 and 4.

## Timing

Closes at 325 MHz on a Xilinx Kintex-7 `xc7k160tffg676-3`, WNS **+0.257 ns**
post synthesis. The worst path is not on the datapath, it is the completion
write enable. That path, and why this block needs a synthesis harness to be
measured, are in
[`docs/dedup_egress_design.md`](docs/dedup_egress_design.md), section 6.

```bash
cd sta && vivado -mode batch -source run.tcl
```

## Verification

```bash
cd verification && make   # 20 tests
```

Directed tests cover passthrough, the drop, the same cycle bypass, the
completed packets table, a completion arriving mid packet and flow control.
Constrained random then drives two regimes against a cycle accurate golden
model that checks `out_valid`, `in_ready` and the output beat on every cycle.
What each test establishes is in
[`docs/dedup_egress_design.md`](docs/dedup_egress_design.md), section 7.

Waveforms are off by default because tracing the long random tests segfaults
Verilator 5.036. Enable them on a short run with
`make TRACE=1 MODULE=test_midpacket`.
