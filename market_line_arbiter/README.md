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

Once the arbiter locks onto a feed it serves that packet from SOP to EOP without
interleaving another feed's data. A per feed skid register absorbs `out_ready`
deassertion without dropping or duplicating a beat. If the locked feed goes
silent for `HICCUP_CYCLES` the arbiter gives up on it, raising `invalidate_feed`
so `feed_buffer` can discard the abandoned packet's tail, and hands off to the
next feed with data. A `fix_avail` input lets a retransmit feed take priority
when the arbiter is free to re-pick.

How each of those works is in
[`docs/market_line_arbiter_design.md`](docs/market_line_arbiter_design.md),
sections 3 and 4.

## Timing

Closes at 325 MHz on a Xilinx Kintex-7 `xc7k160tffg676-3`, WNS **+0.298 ns**
post synthesis. The worst path, the resource numbers, and the caveats on both
are in
[`docs/market_line_arbiter_design.md`](docs/market_line_arbiter_design.md),
section 6.

```bash
cd sta && vivado -mode batch -source run.tcl
```

## Verification

```bash
cd verification && make   # 32 tests
```

Directed tests cover mainstream serving, backpressure and the skid buffers, fix
preference, the arm, confirm and giveup sequence, and fix and hiccup together.
Constrained random then drives 3000 cycles against an independent golden model.
The suite is swept across `HICCUP_CYCLES` values from the structural minimum of
3 upward:

```bash
make clean && make HICCUP_CYCLES=8
```

The threshold reaches both the RTL, as a real Verilog parameter override, and
the Python model, through an environment variable, from the same source, so the
two can never drift apart. What each test establishes is in
[`docs/market_line_arbiter_design.md`](docs/market_line_arbiter_design.md),
section 7.
