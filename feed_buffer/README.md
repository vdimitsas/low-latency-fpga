# feed_buffer

The **feed_buffer** block holds per feed FIFO storage between `dedup_ingress`
and `market_line_arbiter`, so that a feed waiting its turn does not lose data
and does not block any other feed.

## Context

The arbiter serves one feed at a time and holds that choice for a whole packet.
Without storage in front of it, every feed it is not currently serving would
immediately backpressure its source. Each feed therefore gets its own FIFO, its
own bypass path and its own output register, and nothing is shared between
them.

## Behaviour

Each feed gets its own FIFO, bypass path and output register, so a feed waiting
its turn keeps accepting data and no feed can block another. A beat arriving to
an empty FIFO with the output free skips the FIFO and is presented one cycle
later. When the arbiter gives up on a stalled feed, a sticky bit drops that
feed's incoming beats until the abandoned packet is over.

How each of those works is in
[`docs/feed_buffer_design.md`](docs/feed_buffer_design.md), sections 3 and 4.

## Timing

Closes at 325 MHz on a Xilinx Kintex-7 `xc7k160tffg676-3`, WNS **+0.688 ns**
post synthesis. The worst path and what drives it are in
[`docs/feed_buffer_design.md`](docs/feed_buffer_design.md), section 6.

```bash
cd sta && vivado -mode batch -source run.tcl
```

## Verification

Two suites, cocotb against Verilator. `sync_fifo` is generic and is verified on
its own before `feed_buffer` is built on top of it.

```bash
cd verification && make                  # 29 tests, feed_buffer
cd verification && make -f Makefile.fifo # 8 tests, sync_fifo
```

Directed tests cover the bypass, backpressure in both directions, and the sticky
invalidate. Constrained random then drives four regimes against a cycle accurate
golden model that checks `in_ready`, `out_valid` and every output field on every
cycle. What each test establishes is in
[`docs/feed_buffer_design.md`](docs/feed_buffer_design.md), section 7.
