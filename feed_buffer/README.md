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

- **Per feed storage.** `N_FEEDS` independent FIFOs, each `FIFO_DEPTH` beats
  deep. A beat carries data, sequence number, `sop` and `eop`,
  packed into one entry by a local struct. The struct does not leave the block:
  the ports either side are flat.
- **Bypass.** A beat arriving to an empty FIFO with the output register free
  goes straight into that register, so it is presented one cycle later instead
  of two. `fifo_empty` is part of the condition, so a beat can never overtake
  one already queued.
- **Registered output.** Both the bypass and the FIFO head feed the same output
  register. It holds its contents, unchanged, until the arbiter takes them.
  "Free" means it holds nothing, or the arbiter is taking what it holds this
  cycle, which is not the same as `out_ready` being high: a beat can bypass
  into an empty register while the arbiter is stalled, and wait there.
- **Flow control.** `in_ready` is `!fifo_full || out_reg_free`. A full FIFO
  does not hold off its source while a beat is leaving on the same edge, so a
  feed running at capacity sustains one beat in and one beat out every cycle.
- **Sticky invalidate.** When the arbiter gives up on a stalled feed it raises
  `invalidate_feed` for that feed. The bit is set combinationally, so a beat
  arriving in that same cycle is already dropped, and it stays set until
  cleared.

## Scope

The block makes no selection decisions and holds no completed packets table.
It does not see the completion feedback from `checksum` at all.

That is deliberate. A completion arrives after the arbiter has already served
the copy, so those beats are past this block and cannot be recalled here.
`dedup_egress` stops them at the tail of the pipeline, and since that block is
needed anyway, a table here would catch nothing it does not already catch.

`sync_fifo` does not qualify its write with `full`. Its contract is that the
caller must not assert `wr_en` on a full FIFO unless it is also reading, which
is what lets a full feed accept a beat while one is leaving.

There is no flush port on the FIFO, and none is needed. The arbiter only gives
up on a feed after that feed has gone silent, which happens only once its FIFO
has run empty. So when `invalidate_feed` arrives there is nothing stored to
throw away: the beats to discard are the tail of the abandoned packet, still
arriving.

The sticky bit clears on an EOP beat, the end of that tail, and on a SOP beat,
which is a fresh packet and is accepted normally. Under well formed traffic the
EOP condition is redundant, because the beat after an EOP is always a SOP,
which clears the bit anyway. It is kept so that the bit clears on whichever
comes first rather than waiting for a packet that may be a long way off.

Full detail, including the reasoning behind each decision, is in
[`docs/feed_buffer_design.md`](docs/feed_buffer_design.md).

## Timing

- Synthesised for **Xilinx Kintex-7 `xc7k160tffg676-3`** at **325 MHz**.
- **WNS +0.688 ns** post synthesis.
- Worst path runs from a FIFO write pointer bit to the distributed RAM write
  enable, 3 logic levels.

```
Source:       g_feed[3].u_fifo/wr_ptr_reg[1]/C
Destination:  g_feed[3].u_fifo/mem_reg_0_31_0_5/RAMA/WE
Data Path Delay: 1.920 ns  (logic 0.431 ns, route 1.489 ns)
```

The path is the pointer comparison that produces `full`, through `in_ready` and
the bypass decision, to the FIFO write enable. Only 0.431 ns of that is logic.
The rest is routing into the distributed RAM. Which feed it lands on is
placement, not structure: every slice is identical.

Unlike `dedup_ingress`, this block registers its output, so it contains real
register to register paths on its own and needs no synthesis harness.
Reproduce with:

```bash
cd sta && vivado -mode batch -source run.tcl
```

## Verification

Two suites, both run with cocotb against Verilator. `sync_fifo` is generic and
is verified on its own before `feed_buffer` is built on top of it.

```
cd verification && make                  # 29 tests, feed_buffer
cd verification && make -f Makefile.fifo # 8 tests, sync_fifo
```

Every test, directed and random alike, checks `in_ready`, `out_valid` and every
output field against a cycle accurate golden model on every cycle. The directed
tests only have to create the situation worth checking.

`sync_fifo`:

- Empty at reset, and `full` asserts at exactly `DEPTH` entries, not before.
- `rd_data` shows the head in the same cycle `empty` goes low. This is what
  makes the read path one cycle rather than two, so it is asserted rather than
  assumed.
- A read from an empty FIFO is refused without moving the read pointer.
- A write and a read together on a full FIFO are both accepted. Occupancy stays
  at `DEPTH`, the beat leaving is not corrupted by the beat arriving, and order
  holds across the drain afterwards.
- Read and write in the same cycle, at occupancy 1, 2 and `DEPTH-1`.
- Wrap: fill, drain, fill again past the wrap point, order preserved.
- Random traffic in three regimes against a deque.

`feed_buffer`:

- **Passthrough.** Single beat and multi beat packets arrive intact and in
  order, four feeds at once, and back to back packets keep their boundaries.
- **Bypass.** A bypassed beat is presented exactly one cycle later, on every
  feed. A beat bypasses into an empty register even while the arbiter is
  stalled. A beat does not bypass when the register is occupied, or when the
  FIFO is not empty. Latency at the port is used rather than reaching inside
  the block, since that is what the bypass exists for.
- **Backpressure.** A held beat is stable and taken exactly once. A full feed
  does not block the others, drains without losing or reordering a beat, and a
  beat offered while genuinely blocked is not quietly accepted. A full feed
  still accepts a beat on the cycle the arbiter takes one, and sustained one in
  one out runs for sixteen straight cycles.
- **Invalidate.** The drop is same cycle, sticky, and scoped to one feed. It
  clears on EOP, and on SOP with that beat accepted rather than consumed.
  Invalidate fires on a silent feed and the tail arriving much later still
  dies.
- **Constrained random.** Four regimes: light backpressure where the bypass
  carries almost everything, heavy backpressure where the FIFOs fill and
  `in_ready` falls, invalidate firing often against a background of stalls, and
  single beat packets only, which is where a FIFO holds the most distinct
  packets at once.

The suite was mutation checked: the RTL is broken deliberately, one change at a
time, to confirm the tests fail. Against 29 `feed_buffer` tests and 8
`sync_fifo` tests:

| Mutation | `feed_buffer` failed | `sync_fifo` failed |
|---|---|---|
| `out_reg_free` is just `out_ready` | 14 | 0 |
| Bypass ignores `fifo_empty` | 13 | 0 |
| FIFO read ignores `out_reg_free` | 12 | 0 |
| `in_ready` loses the `out_reg_free` term | 7 | 0 |
| Drop ignores the sticky bit | 5 | 0 |
| `sync_fifo` refuses a write when full | 5 | 2 |
| Drop ignores `in_sop`, so a `sop` is not accepted | 4 | 0 |
| Drop has no same cycle path | 3 | 0 |
| Sticky clears only on `eop`, so a `sop` never clears it | 2 | 0 |
| Sticky clears only on `sop`, so an `eop` never clears it | 0 | 0 |

The last one is not a coverage gap. Removing the `eop` condition changes nothing
observable, because the beat after a dropped `eop` is always a `sop`, which
clears the bit at the same moment. The Scope section above says why it is kept.

## Layout

```
feed_buffer/
├── README.md
├── rtl/
│   ├── feed_buffer.sv                # per feed FIFOs, bypass, sticky invalidate
│   └── sync_fifo.sv                  # generic single clock FIFO
├── sta/
│   └── run.tcl                       # synthesis and timing script
├── docs/
│   ├── feed_buffer_design.md         # detailed design notes
│   └── images/
│       ├── feed_buffer.svg
│       └── feed_buffer_slice.svg
└── verification/
    ├── Makefile                      # feed_buffer suite
    ├── Makefile.fifo                 # sync_fifo suite
    ├── feed_buffer_tb_wrap.sv        # flattens the packed ports for cocotb
    ├── feed_buffer_common.py         # driver, sampler and golden model
    ├── test_passthrough.py
    ├── test_bypass.py
    ├── test_backpressure.py
    ├── test_invalidate.py
    ├── test_random.py
    └── test_fifo.py
```
