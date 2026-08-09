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
  deep. A beat carries data, sequence number, and the two boundary markers,
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
- **Flow control from occupancy alone.** `in_ready` is that feed's FIFO not
  being full. The arbiter's `out_ready` is deliberately kept out of it, so no
  combinational path runs from the arbiter back through this block into
  `dedup_ingress`, which has no timing margin for one.
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

## Timing

- Synthesised for **Xilinx Kintex-7 `xc7k160tffg676-3`** at **325 MHz**.
- **WNS +0.697 ns** post synthesis.
- Worst path runs from a FIFO write pointer bit to the distributed RAM write
  enable, 3 logic levels.

```
Source:       g_feed[0].u_fifo/wr_ptr_reg[2]/C
Destination:  g_feed[0].u_fifo/mem_reg_0_31_0_5/RAMA/WE
Data Path Delay: 1.911 ns  (logic 0.431 ns, route 1.480 ns)
Logic Levels: 3  (LUT4=2, LUT6=1)
```

The path is the pointer comparison that produces `full`, through `in_ready` and
the bypass decision, to the FIFO write enable. Only 0.431 ns of that is logic.
The rest is routing into the distributed RAM.

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
cd verification && make                 # 27 tests, feed_buffer
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
- A write into a full FIFO and a read from an empty one are both refused
  without disturbing the pointers or the head.
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
- **Backpressure.** A held beat is stable and taken exactly once. `in_ready`
  falls only when the FIFO is genuinely full, and a full feed does not block
  the others. A full feed drains without losing or reordering a beat, and a
  beat offered while full is not quietly accepted.
- **Invalidate.** The drop is same cycle, sticky, and scoped to one feed. It
  clears on EOP, and on SOP with that beat accepted rather than consumed.
  Invalidate fires on a silent feed and the tail arriving much later still
  dies.
- **Constrained random.** Four regimes: light backpressure where the bypass
  carries almost everything, heavy backpressure where the FIFOs fill and
  `in_ready` falls, invalidate firing often against a background of stalls, and
  single beat packets only, which is where a FIFO holds the most distinct
  packets at once.

The suite was mutation checked. Nine mutations, eight caught:

| Mutation | Tests failed |
|---|---|
| Bypass ignores `fifo_empty` | 11 |
| `out_reg_free` is just `out_ready` | 12 |
| `in_ready` also depends on `out_ready` | 13 |
| FIFO read ignores `out_reg_free` | 10 |
| Drop ignores the sticky bit | 5 |
| Drop ignores `in_sop`, so a SOP is not accepted | 4 |
| Drop has no same cycle path | 3 |
| Sticky clears only on SOP, not EOP | 2 |
| Sticky clears only on EOP, not SOP | 0 |

The last one is not a coverage gap. Removing the EOP condition changes nothing
observable, because the beat after a dropped EOP is always a SOP, which clears
the bit at the same moment. The condition is defensive, and the Scope section
above says so.

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
