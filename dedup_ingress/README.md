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

The datapath has one pipeline register. A beat and its comparison results are
registered together, so a beat presented on `in_data` appears on `out_data` one
cycle later. The only thing the block does to the stream is withhold
`out_valid` on a feed whose packet has already completed.

The register exists for timing. The comparator tree, the drop decision and the
ready path in one cycle did not close at 325 MHz. Section 6 of the design
document has the numbers.

The sequence number arrives only in the first beat of a packet. It is sliced out
combinationally, held for the rest of the packet, and re-emitted on `out_seq`,
so no downstream block has to parse the header again. Completions are kept in a
circular table and every feed is compared against all of it each cycle.

How each of those works is in
[`docs/dedup_ingress_design.md`](docs/dedup_ingress_design.md), sections 3 and
4.

## Timing

Closes at 325 MHz on a Xilinx Kintex-7 `xc7k160tffg676-3`, WNS **+0.240 ns**
post synthesis. The worst path, and why this block needs a synthesis harness to
be measured at all, are in
[`docs/dedup_ingress_design.md`](docs/dedup_ingress_design.md), section 6.

```bash
cd sta && vivado -mode batch -source run.tcl
```

## Verification

```bash
cd verification && make   # 24 tests
```

Directed tests cover passthrough, the drop, the same cycle bypass, the completed
packets table, the mid packet kill and flow control. Constrained random then
drives two regimes against a cycle accurate golden model that checks
`out_valid`, `in_ready` and `out_seq` on every cycle. What each test
establishes is in
[`docs/dedup_ingress_design.md`](docs/dedup_ingress_design.md), section 7.

Waveforms are off by default because tracing the long random tests segfaults
Verilator 5.036. Enable them on a short run with
`make TRACE=1 MODULE=test_midpacket`.
