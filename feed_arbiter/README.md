# feed_arbiter

The **feed arbiter** is the pipeline stage that decides, on every cycle, which of
several redundant input feeds advances downstream — holding each packet together
atomically, absorbing downstream backpressure, and recovering cleanly when the
selected feed stalls mid-packet.

## Context

The arbiter targets **redundant market-data feeds**: up to four input lines that
nominally carry the same stream (redundant copies for reliability and latency).
In practice arrival is unpredictable — the feeds can be out of order relative to
one another, and may momentarily diverge — so the arbiter must select and hand
off correctly without assuming the lines are identical or synchronised.

## Behaviour

- **Atomic packet serving** — once the arbiter locks onto a feed, it serves that
  feed's packet from SOP to EOP without interleaving another feed's data.
- **Skid-buffered backpressure** — a per-feed skid register handles `out_ready`
  deassertion without dropping or duplicating a beat, and exposes a per-feed
  `in_ready` back to the upstream feeds.
- **Starvation handling (hiccup / giveup)** — if the locked feed goes silent for
  a parameterised number of cycles, the arbiter arms, confirms, and gives up on
  it, releasing exactly one cycle after confirmation and handing off to the next
  feed that has data.
- **Recovery (fix) preference** — a `fix_avail` input lets a recovery/retransmit
  feed take priority when the arbiter is free to re-pick.

## Results

- **Timing closed at 325 MHz** on a Xilinx Kintex-7 target (positive WNS).
- **Full golden-model random verification** — 3000 constrained-random cycles
  checked cycle-by-cycle against an independent reference model, 0 mismatches.
- **Directed suite** covering mainstream serving, backpressure/skid behaviour,
  fix preference, hiccup/giveup, fix+hiccup interactions, and threshold timing.

## Running the tests

The verification suite uses [cocotb](https://www.cocotb.org/) with the
[Verilator](https://www.veripool.org/verilator/) simulator.

```bash
cd verification
make clean && make
```

To sweep the starvation threshold (any value ≥ 3):

```bash
make clean && make HICCUP_CYCLES=8
```

The threshold is passed to both the RTL (as a real Verilog parameter override)
and to the Python side (via an environment variable) from the same source, so
the DUT and the model can never drift apart.

## Layout

```
feed_arbiter/
├── README.md
├── docs/                            # detailed design notes & timing-closure report
├── rtl/
│   └── feed_arbiter.sv              # the arbiter RTL
└── verification/
    ├── Makefile                     # cocotb + Verilator test runner
    ├── feed_arbiter_tb_wrap.sv      # flat-port wrapper around the DUT
    ├── arb_common.py                # shared helpers / scoreboard
    ├── test_mainstream.py           # basic serving behaviour
    ├── test_backpressure.py         # out_ready / in_ready / skid
    ├── test_fix.py                  # fix_avail preference
    ├── test_hiccup.py               # starvation / giveup / resume
    ├── test_fix_hiccup.py           # fix + hiccup interactions
    ├── test_hiccup_threshold.py     # arm/confirm/giveup timing
    └── test_random.py               # constrained-random golden model
```