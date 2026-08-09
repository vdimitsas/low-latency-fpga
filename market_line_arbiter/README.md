# market_line_arbiter

The **market line arbiter** decides which of several redundant input feeds
advances downstream, holds each packet together atomically, absorbs downstream
backpressure, and recovers cleanly when the selected feed stalls mid-packet.

## Context

The arbiter targets **redundant market-data feeds**: up to four input lines that
nominally carry the same stream. In practice arrival is unpredictable, the feeds
can be out of order relative to one another, and may momentarily diverge, so the
arbiter must select and hand off correctly without assuming the lines are
identical or synchronised.

## Behaviour

- **Atomic packet serving.** Once the arbiter locks onto a feed, it serves that
  feed's packet from SOP to EOP without interleaving another feed's data.
- **Skid-buffered backpressure.** A per feed skid register handles `out_ready`
  deassertion without dropping or duplicating a beat, and exposes a per feed
  `in_ready` back to the upstream feeds.
- **Starvation handling.** If the locked feed goes silent for `HICCUP_CYCLES`,
  the arbiter arms, confirms, and gives up on it, releasing exactly one cycle
  after confirmation and handing off to the next feed that has data. Giving up
  raises `invalidate_feed` for that feed, which tells `feed_buffer` to discard
  the abandoned packet's tail.
- **Recovery preference.** A `fix_avail` input lets a retransmit feed take
  priority when the arbiter is free to re-pick.

## Timing

- Synthesised for **Xilinx Kintex-7 `xc7k160tffg676-3`** at **325 MHz**.
- **WNS +0.298 ns** post synthesis.
- Worst path runs from a skid valid bit to a skid data register clock enable.

```
Source:       skid_valid_reg[0]/C
Destination:  skid_data_reg[1][0]/CE
Data Path Delay: 2.407 ns  (logic 0.474 ns, route 1.933 ns)
```

Only 0.474 ns of that is logic. The rest is routing, which is what fans a
per feed decision out across every feed's skid register.

The block registers its outputs, so it contains real register to register paths
on its own and needs no synthesis harness. Reproduce with:

```bash
cd sta && vivado -mode batch -source run.tcl
```

## Verification

The suite uses [cocotb](https://www.cocotb.org/) with the
[Verilator](https://www.veripool.org/verilator/) simulator.

```bash
cd verification
make clean && make
```

Directed tests cover mainstream serving, backpressure and skid behaviour, fix
preference, hiccup and giveup, fix and hiccup together, and the arm, confirm,
giveup timing. Constrained random then drives 3000 cycles and checks every
output on every cycle against an independent golden model.

To sweep the starvation threshold, any value of 3 or more:

```bash
make clean && make HICCUP_CYCLES=8
```

The threshold reaches both the RTL, as a real Verilog parameter override, and
the Python model, through an environment variable, from the same source, so the
two can never drift apart.

## Layout

```
market_line_arbiter/
├── README.md
├── rtl/
│   └── market_line_arbiter.sv             # the arbiter RTL
├── sta/
│   └── run.tcl                            # synthesis and timing script
├── docs/
│   ├── market_line_arbiter_design.md      # detailed design notes
│   └── images/
│       ├── market_line_arb_microarch.svg
│       └── market_line_arb_giveup_wave.svg
└── verification/
    ├── Makefile                           # cocotb + Verilator test runner
    ├── market_line_arbiter_tb_wrap.sv     # flat-port wrapper around the DUT
    ├── arb_common.py                      # shared helpers and golden model
    ├── test_mainstream.py                 # basic serving behaviour
    ├── test_backpressure.py               # out_ready, in_ready, skid
    ├── test_fix.py                        # fix_avail preference
    ├── test_hiccup.py                     # starvation, giveup, resume
    ├── test_fix_hiccup.py                 # fix and hiccup interactions
    ├── test_hiccup_threshold.py           # arm, confirm, giveup timing
    └── test_random.py                     # constrained random golden model
```
