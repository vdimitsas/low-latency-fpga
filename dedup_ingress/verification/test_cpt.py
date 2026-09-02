"""Tests 5, 8, 9 and 13: the completed packets table.

5. A completion is written whether or not it matched anything that cycle, and
   is visible from the next cycle.
8. The table is circular: past CPT_DEPTH completions the oldest entry is
   overwritten.
9. Chained: fill, wrap, evict a known seq, then present that exact seq and
   watch it pass through. That is the bounded window limitation, tested as
   intended behaviour rather than left as a surprise.
13. A completion whose sequence number the table already holds is not written
   again. A packet can complete twice, because a copy that got past this block
   before its twin completed is still served and still checksummed. Writing it
   a second time would evict a different, still useful entry for no gain.

step reads the DUT after the clock edge, so the beat presented in a call is
already on the outputs when that call returns. One step per probe.
"""

import cocotb

from dedup_ingress_common import CPT_DEPTH, DedupIngressTB


async def probe(tb, feed, seq):
    """Present one SOP beat carrying seq and return the cycle it appears in."""
    tb.present(feed, seq=seq, sop=1)
    return await tb.step()


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
    got = await probe(tb, 0, seq)
    assert got["out_valid"][0] == 0, (
        "a completion with no concurrent traffic was not written to the CPT"
    )


@cocotb.test()
async def test_write_pointer_advances(dut):
    """5b. Consecutive completions occupy distinct entries."""
    tb = DedupIngressTB(dut)
    await tb.start()

    seqs = [0x300 + i for i in range(CPT_DEPTH)]
    for seq in seqs:
        tb.complete(seq)
        await tb.step()

    # Every one of them must still be held: the table is exactly full.
    for seq in seqs:
        got = await probe(tb, 0, seq)
        assert got["out_valid"][0] == 0, (
            f"seq {seq:#x} was lost, the write pointer is not advancing cleanly"
        )


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

    got = await probe(tb, 0, first)
    assert got["out_valid"][0] == 1, "the oldest entry was not evicted on wrap"

    # Everything newer is still held.
    for seq in seqs[1:] + [evictor]:
        got = await probe(tb, 1, seq)
        assert got["out_valid"][1] == 0, (
            f"seq {seq:#x} should still be in the table after one eviction"
        )


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

    # Its late copy now has nothing to match against.
    got = await probe(tb, 3, straggler)
    assert got["out_valid"][3] == 1, (
        "expected the evicted straggler to pass through the bounded window"
    )


@cocotb.test()
async def test_repeated_completion_is_not_rewritten(dut):
    """13. The same seq completing twice must occupy only one entry.

    Fill the table to exactly full, then complete the newest entry again. If
    the repeat were written it would advance the pointer and evict the oldest,
    so the oldest surviving is the proof.
    """
    tb = DedupIngressTB(dut)
    await tb.start()

    first = 0xB00
    seqs = [first + i for i in range(CPT_DEPTH)]
    for seq in seqs:
        tb.complete(seq)
        await tb.step()

    # Complete the newest entry again, several times over.
    repeat = seqs[-1]
    for _ in range(3):
        tb.complete(repeat)
        await tb.step()

    # Nothing was evicted: every original entry is still held.
    for seq in seqs:
        got = await probe(tb, 0, seq)
        assert got["out_valid"][0] == 0, (
            f"seq {seq:#x} was evicted by a repeated completion"
        )


@cocotb.test()
async def test_repeated_completion_leaves_the_pointer_alone(dut):
    """13b. A suppressed write must not advance cpt_wr_ptr.

    Fill the table, repeat the newest entry, then add one genuinely new seq.
    With the write suppressed the pointer stayed put, so the new seq costs
    exactly one eviction and the second oldest survives. If the pointer had
    moved on the repeat, the new seq lands one slot further on and the second
    oldest is evicted too.
    """
    tb = DedupIngressTB(dut)
    await tb.start()

    seqs = [0xC00 + i for i in range(CPT_DEPTH)]
    for seq in seqs:
        tb.complete(seq)
        await tb.step()

    # Repeat the newest. This must consume no slot.
    tb.complete(seqs[-1])
    await tb.step()

    # One genuinely new seq: exactly one eviction, the oldest.
    fresh = 0xCFF
    tb.complete(fresh)
    await tb.step()

    got = await probe(tb, 0, seqs[0])
    assert got["out_valid"][0] == 1, (
        "the oldest entry should have been evicted by the new completion"
    )

    # The second oldest must survive. It only falls if the repeat moved the
    # pointer and cost an extra eviction.
    for seq in seqs[1:] + [fresh]:
        got = await probe(tb, 1, seq)
        assert got["out_valid"][1] == 0, (
            f"seq {seq:#x} was lost, the pointer moved on a suppressed write"
        )
