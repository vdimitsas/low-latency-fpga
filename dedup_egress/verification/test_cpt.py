"""Tests 5, 8, 9 and 13: the completed packets table.

5. A completion is written whether or not it matched anything that cycle, and
   is visible from the next cycle.
8. The table is circular: past CPT_DEPTH completions the oldest entry is
   overwritten.
9. Chained: fill, wrap, evict a known seq, then present that exact seq and
   watch it pass through. That is the bounded window limitation, tested as
   intended behaviour rather than left as a surprise.
13. A completion whose sequence number the table already holds is written
   again and takes the next slot.

step reads the DUT after the clock edge, so the beat presented in a call is
already on the outputs when that call returns. One step per probe.
"""

import cocotb

from dedup_egress_common import CPT_DEPTH, DedupEgressTB


async def probe(tb, seq):
    """Present one SOP beat carrying seq and return the cycle it appears in."""
    tb.present(seq=seq, sop=1)
    return await tb.step()


@cocotb.test()
async def test_completion_is_written_without_a_match(dut):
    """5. The stream is idle when the completion arrives, it still lands."""
    tb = DedupEgressTB(dut)
    await tb.start()

    seq = 0x1234

    tb.complete(seq)
    await tb.step()

    got = await probe(tb, seq)
    assert got["out_valid"] == 0, (
        "a completion with no concurrent traffic was not written to the CPT"
    )


@cocotb.test()
async def test_write_pointer_advances(dut):
    """5b. Consecutive completions occupy distinct entries."""
    tb = DedupEgressTB(dut)
    await tb.start()

    seqs = [0x300 + i for i in range(CPT_DEPTH)]
    for seq in seqs:
        tb.complete(seq)
        await tb.step()

    # Every one of them must still be held: the table is exactly full.
    for seq in seqs:
        got = await probe(tb, seq)
        assert got["out_valid"] == 0, (
            f"seq {seq:#x} was lost, the write pointer is not advancing cleanly"
        )


@cocotb.test()
async def test_table_wraps_and_evicts_oldest(dut):
    """8. One completion past full pushes the first entry out."""
    tb = DedupEgressTB(dut)
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

    got = await probe(tb, first)
    assert got["out_valid"] == 1, "the oldest entry was not evicted on wrap"

    # Everything newer is still held.
    for seq in seqs[1:] + [evictor]:
        got = await probe(tb, seq)
        assert got["out_valid"] == 0, (
            f"seq {seq:#x} should still be in the table after one eviction"
        )


@cocotb.test()
async def test_straggler_outside_the_window(dut):
    """9. A copy whose seq has aged out of the table passes through.

    This is the documented limitation of a bounded window, not a bug. The test
    exists so that if the eviction policy ever changes, this changes with it
    deliberately rather than silently.
    """
    tb = DedupEgressTB(dut)
    await tb.start()

    straggler = 0xDEAD

    # The straggler's packet completes first.
    tb.complete(straggler)
    await tb.step()

    # Then enough traffic completes to push it entirely out of the window.
    for i in range(CPT_DEPTH):
        tb.complete(0xE000 + i)
        await tb.step()

    got = await probe(tb, straggler)
    assert got["out_valid"] == 1, (
        "expected the evicted straggler to pass through the bounded window"
    )


@cocotb.test()
async def test_repeated_completion_takes_a_slot(dut):
    """13. The same seq completing twice occupies two entries.

    Nothing suppresses a repeat, so the second write takes the next slot and
    pushes out whatever was in it.
    """
    tb = DedupEgressTB(dut)
    await tb.start()

    seqs = []
    for i in range(CPT_DEPTH):
        seqs.append(0xB00 + i)

    for seq in seqs:
        tb.complete(seq)
        await tb.step()

    # Complete the newest entry again. The table is full, so this takes the
    # slot the oldest entry is in.
    tb.complete(seqs[-1])
    await tb.step()

    got = await probe(tb, seqs[0])
    assert got["out_valid"] == 1, (
        "the repeat did not take a slot, so the oldest entry survived"
    )

    # The repeated seq is still held.
    got = await probe(tb, seqs[-1])
    assert got["out_valid"] == 0, "the repeated seq is not in the table"
