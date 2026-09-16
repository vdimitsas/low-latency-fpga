"""Tests 5, 8 and 9: the completed packets table.

5. A completion is written whether or not it matched anything that cycle, and
   is visible from the next cycle.
8. The table is circular: past CPT_DEPTH completions the oldest entry is
   overwritten.
9. Chained: fill, wrap, push out a known seq, then present that exact seq and
   watch it pass through. That is the limit of a table this size, tested as
   intended behaviour rather than left as a surprise.

Probing the table means getting a sequence number in front of the comparators,
which takes a packet: the number arrives on its own beat, never at SOP. The
beat it arrives on has nothing to do with the table, but the tests run over
several values of it anyway.

step reads the DUT after the clock edge, so the beat driven in a call is on
the outputs when that call returns.
"""

import cocotb

from dedup_ingress_common import CPT_DEPTH, DedupIngressTB

# The beats the sequence number is made to arrive on. Fixed values, not random:
# a directed test has to fail the same way twice.
ARRIVAL_BEATS = [3, 4, 7]


async def probe(tb, feed, seq, seq_beat):
    """Put one sequence number in front of the comparators.

    Drives the beats ahead of it, then the beat carrying it, and returns the
    cycle that beat was on the outputs. A packet this short has no EOP, which
    the block does not read, so it costs nothing here.
    """
    for beat in range(seq_beat):
        tb.present(feed, data=0x90 + beat, sop=1 if beat == 0 else 0)
        await tb.step()

    tb.present(feed, seq=seq)
    got = await tb.step()

    await tb.idle(1)
    return got


@cocotb.test()
async def test_completion_is_written_without_a_match(dut):
    """5. No feed is active when the completion arrives, it still lands."""
    tb = DedupIngressTB(dut)
    await tb.start()

    for n, seq_beat in enumerate(ARRIVAL_BEATS):
        seq = 0x1234 + n

        # Completion with all feeds idle: nothing to match against.
        tb.complete(seq)
        await tb.step()

        got = await probe(tb, 0, seq, seq_beat)
        assert got["out_valid"][0] == 0, (
            f"arrival beat {seq_beat}: a completion with no concurrent "
            f"traffic was not written to the CPT"
        )


@cocotb.test()
async def test_write_pointer_advances(dut):
    """5b. Consecutive completions occupy distinct entries."""
    tb = DedupIngressTB(dut)
    await tb.start()

    for n, seq_beat in enumerate(ARRIVAL_BEATS):
        base = 0x300 + n * 0x100
        seqs = []
        for i in range(CPT_DEPTH):
            seqs.append(base + i)

        for seq in seqs:
            tb.complete(seq)
            await tb.step()

        # Every one of them must still be held: the table is exactly full.
        for seq in seqs:
            got = await probe(tb, 0, seq, seq_beat)
            assert got["out_valid"][0] == 0, (
                f"arrival beat {seq_beat}: seq {seq:#x} was lost, the write "
                f"pointer is not advancing cleanly"
            )


@cocotb.test()
async def test_table_wraps_and_pushes_out_the_oldest(dut):
    """8. One completion past full pushes the first entry out."""
    tb = DedupIngressTB(dut)
    await tb.start()

    for n, seq_beat in enumerate(ARRIVAL_BEATS):
        base = 0x900 + n * 0x1000
        first = base
        seqs = []
        for i in range(CPT_DEPTH):
            seqs.append(base + i)

        for seq in seqs:
            tb.complete(seq)
            await tb.step()

        # One more completion takes the slot `first` is in.
        newest = base + 0xFF
        tb.complete(newest)
        await tb.step()

        got = await probe(tb, 0, first, seq_beat)
        assert got["out_valid"][0] == 1, (
            f"arrival beat {seq_beat}: the oldest entry was not overwritten "
            f"on wrap"
        )

        # Everything newer is still held.
        for seq in seqs[1:] + [newest]:
            got = await probe(tb, 1, seq, seq_beat)
            assert got["out_valid"][1] == 0, (
                f"arrival beat {seq_beat}: seq {seq:#x} should still be in "
                f"the table after one overwrite"
            )


@cocotb.test()
async def test_late_copy_outside_the_table(dut):
    """9. A copy whose seq has been pushed out of the table passes through.

    This is what a table of this size can and cannot do, not a bug. The test
    exists so that if the write policy ever changes, this changes with it
    deliberately rather than silently.
    """
    tb = DedupIngressTB(dut)
    await tb.start()

    for n, seq_beat in enumerate(ARRIVAL_BEATS):
        late = 0xDEAD + n

        # The packet completes first.
        tb.complete(late)
        await tb.step()

        # Then enough traffic completes to take every slot in the table.
        for i in range(CPT_DEPTH):
            tb.complete(0xE000 + n * 0x100 + i)
            await tb.step()

        # Its late copy now has nothing to match against.
        got = await probe(tb, 3, late, seq_beat)
        assert got["out_valid"][3] == 1, (
            f"arrival beat {seq_beat}: expected the late copy to pass once "
            f"its entry had been overwritten"
        )


@cocotb.test()
async def test_repeated_completion_takes_a_slot(dut):
    """The same seq completing twice occupies two entries.

    Nothing suppresses a repeat, so the second write takes the next slot and
    pushes out whatever was in it. This cannot deliver a duplicate downstream:
    a copy that got past this block before its twin completed is dropped at
    dedup_egress, and a copy far enough behind to fall outside this table is
    beyond what a table of any size can catch.
    """
    tb = DedupIngressTB(dut)
    await tb.start()

    for n, seq_beat in enumerate(ARRIVAL_BEATS):
        base = 0xB00 + n * 0x100
        seqs = []
        for i in range(CPT_DEPTH):
            seqs.append(base + i)

        for seq in seqs:
            tb.complete(seq)
            await tb.step()

        # Complete the newest entry again. The table is full, so this takes the
        # slot the oldest entry is in.
        tb.complete(seqs[-1])
        await tb.step()

        got = await probe(tb, 0, seqs[0], seq_beat)
        assert got["out_valid"][0] == 1, (
            f"arrival beat {seq_beat}: the repeat did not take a slot, so the "
            f"oldest entry survived"
        )

        # The repeated number is of course still held.
        got = await probe(tb, 1, seqs[-1], seq_beat)
        assert got["out_valid"][1] == 0, (
            f"arrival beat {seq_beat}: the repeated number is not in the table"
        )
