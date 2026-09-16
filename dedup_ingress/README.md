# dedup_ingress

The **dedup_ingress** block drops redundant copies of packets that have already been
confirmed complete downstream. It sits between `seq_extract` and `feed_buffer`
in the `udp_parser` pipeline. A second block, `dedup_egress`, makes the final
cut at the other end.

## Context

The parser takes several redundant market data feeds carrying the same stream.
The same packet therefore arrives more than once, on different lines, at
different times. Once one copy has made it through and passed its checksum,
every later copy of that packet is dead weight and must not reach the rest of
the pipeline.

## Behaviour

The datapath has one register, at the output. A beat presented on `in_data`
appears on `out_data` one cycle later, and every output port of the block comes
from a flop. The only thing the block does to the stream is withhold
`out_valid` on a feed whose packet has already completed.

The sequence ID is not extracted here. `seq_extract` delivers it on
`in_seq`, marked by `in_seq_valid` on the beat that carries it. This block holds
it for the rest of the packet and re-emits it on `out_seq`, with
`out_seq_valid` saying which beats carry a real value.

That sequence ID arrives some beats into the packet, not at SOP, so the leading
beats of a redundant copy are always forwarded before the drop can start. They
die at `dedup_egress`.

Completions are kept in a circular table and every feed is compared against all
of it each cycle.

How each of those works is in
[`docs/dedup_ingress_design.md`](docs/dedup_ingress_design.md), sections 3 and
4.

## Timing

Closes at 325 MHz on a Xilinx Kintex-7 `xc7k160tffg676-3`, WNS **+0.122 ns**
post synthesis. The worst path, the three steps that got the block here, and
why it needs a synthesis harness to be measured at all, are in
[`docs/dedup_ingress_design.md`](docs/dedup_ingress_design.md), section 6.

```bash
cd sta && vivado -mode batch -source run.tcl
```

## Verification

```bash
cd verification && make   # 29 tests
```

Directed tests cover passthrough, the window before the sequence ID
arrives, the drop, the same cycle bypass, the completed packets table, the mid
packet kill and flow control. Constrained random then drives three regimes
against a cycle accurate golden model that checks `in_ready`, `out_valid` and
the output beat on every cycle. What each test establishes is in
[`docs/dedup_ingress_design.md`](docs/dedup_ingress_design.md), section 7.

The beat the sequence ID arrives on is a parameter of the tests, not a
constant. The directed suites run over 3, 4 and 7; the random suite draws it per
packet.

Waveforms are off by default because tracing the long random tests segfaults
Verilator 5.036. Enable them on a short run with
`make TRACE=1 MODULE=test_midpacket`.
