"""Tests 5, 8 and 9: the completed packets table.

5. A completion is written whether or not it matched anything that cycle, and
   is visible from the next cycle.
8. The table is circular: past CPT_DEPTH completions the oldest entry is
   overwritten.
9. Chained: fill, wrap, evict a known seq, then present that exact seq and
   watch it pass through. That is the bounded window limitation, tested as
   intended behaviour rather than left as a surprise.
"""

import cocotb

from dedup_ingress_common import CPT_DEPTH, DedupIngressTB


@cocotb.test()
async def test_completion_is_written_without_a_match(dut):
    """5. No feed is active when the completion arrives, it still lands."""
    tb = DedupIngressTB(dut)
    await tb.start()

    seq = 0x1234

    # Completion with all feeds idle: nothing to match against.
    tb.complete(seq)
    await tb.step()

    # Next cycle it must already be in the table.
    tb.present(0, seq=seq, sop=1)
    got = await tb.step()
    assert got["out_valid"][0] == 0, (
        "a completion with no concurrent traffic was not written to the CPT"
    )

    await tb.idle(2)


@cocotb.test()
async def test_write_pointer_advances(dut):
    """5b. Consecutive completions occupy distinct entries."""
    tb = DedupIngressTB(dut)
    await tb.start()

    seqs = [0x300 + i for i in range(CPT_DEPTH)]
    for seq in seqs:
        tb.complete(seq)
        await tb.step()
    await tb.idle(2)

    # Every one of them must still be held: the table is exactly full.
    for seq in seqs:
        tb.present(0, seq=seq, sop=1)
        got = await tb.step()
        assert got["out_valid"][0] == 0, (
            f"seq {seq:#x} was lost, the write pointer is not advancing cleanly"
        )

    await tb.idle(2)


@cocotb.test()
async def test_table_wraps_and_evicts_oldest(dut):
    """8. One completion past full pushes the first entry out."""
    tb = DedupIngressTB(dut)
    await tb.start()

    first = 0x900
    seqs = [first + i for i in range(CPT_DEPTH)]
    for seq in seqs:
        tb.complete(seq)
        await tb.step()

    # One more completion evicts `first`.
    evictor = 0x9FF
    tb.complete(evictor)
    await tb.step()
    await tb.idle(2)

    tb.present(0, seq=first, sop=1)
    got = await tb.step()
    assert got["out_valid"][0] == 1, (
        "the oldest entry was not evicted on wrap"
    )

    # Everything newer is still held.
    for seq in seqs[1:] + [evictor]:
        tb.present(1, seq=seq, sop=1)
        got = await tb.step()
        assert got["out_valid"][1] == 0, (
            f"seq {seq:#x} should still be in the table after one eviction"
        )

    await tb.idle(2)


@cocotb.test()
async def test_straggler_outside_the_window(dut):
    """9. A copy whose seq has aged out of the table passes through.

    This is the documented limitation of a bounded window, not a bug. The test
    exists so that if the eviction policy ever changes, this changes with it
    deliberately rather than silently.
    """
    tb = DedupIngressTB(dut)
    await tb.start()

    straggler = 0xDEAD

    # The straggler's packet completes first.
    tb.complete(straggler)
    await tb.step()

    # Then enough traffic completes to push it entirely out of the window.
    for i in range(CPT_DEPTH):
        tb.complete(0xE000 + i)
        await tb.step()
    await tb.idle(2)

    # Its late copy now has nothing to match against.
    tb.present(3, seq=straggler, sop=1)
    got = await tb.step()
    assert got["out_valid"][3] == 1, (
        "expected the evicted straggler to pass through the bounded window"
    )

    await tb.idle(2)
