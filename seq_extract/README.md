# seq_extract

The **seq_extract** block pulls the sequence ID out of each feed's beat stream
and hands it to the rest of the pipeline as sideband. It is the front end of the
`udp_parser` pipeline, sitting between the incoming feeds and `dedup_ingress`.

## Context

Every packet carries a sequence ID at a fixed byte offset from its start. Every
stage behind this one needs it: `dedup_ingress` compares it against the
completed packets table, `dedup_egress` makes the final drop decision with it,
and the stages in between carry it along. Extracting it once, at the front,
means no block downstream has to parse a header.

The MAC strips the Ethernet header and nothing else, so a beat stream starts at
the IP header: 20 bytes of IP, then 8 of UDP, then the exchange's own header at
byte 28. The sequence ID sits somewhere inside that, at an offset the venue
decides. At 64 bits per beat it lands in beat 3 or later, and depending on the
offset it may sit inside one beat or cross into the next.

## Behaviour

The datapath has one register stage. A beat presented on `in_data` appears on
`out_data` one cycle later, unchanged, along with its framing and byte count.
Nothing is ever dropped or reordered here.

What the block adds is `out_seq` and `out_seq_valid`. The valid is a one cycle
pulse on the beat that completed the sequence ID, so the consumer samples on the
pulse and holds the value itself. A packet that ends before the sequence ID is
complete produces no pulse at all.

`seq_extract` is a wrapper holding `N_FEEDS` copies of `seq_extract_lane`. Feeds
share no state, so a stall or a packet boundary on one has no effect on any
other. The lane is where all the logic lives: a beat counter, a flag that makes
the capture happen once per packet, and the capture registers, including the
join when the sequence ID spans two beats.

How each of those works is in
[`docs/seq_extract_design.md`](docs/seq_extract_design.md), sections 3 and 4.

## Timing

Closes at 325 MHz on a Xilinx Kintex-7 `xc7k160tffg676-3`, WNS **+1.258 ns**
post synthesis for the wrapper and **+1.509 ns** for the lane. Why this block
needs a synthesis harness to be measured at all is in
[`docs/seq_extract_design.md`](docs/seq_extract_design.md), section 6.

```bash
cd sta && vivado -mode batch -source run.tcl
cd lane/sta && vivado -mode batch -source run.tcl
```

## Verification

```bash
cd lane/verification && make all_suites
cd verification && make all_suites
```

The lane carries the coverage. Directed tests cover passthrough, extraction with
the sequence ID inside one beat and across two, flow control and the beat
counter, and three constrained random regimes then run against a cycle accurate
golden model that checks every output on every cycle.

The wrapper has no logic, so its suite is three tests proving feed f's beat,
sequence ID and ready all belong to feed f.

`SEQ_OFFSET` is fixed at compile time, so both suites build the block more than
once: 28 for the sequence ID inside one beat, 30 for it across two, and 33, 38
and 41 for the random suite. What each test establishes is in
[`docs/seq_extract_design.md`](docs/seq_extract_design.md), section 7.

Waveforms are off by default because tracing the long random tests segfaults
Verilator 5.036. Enable them on a short run with
`make TRACE=1 MODULE=test_seq_span SEQ_OFFSET=30`.
