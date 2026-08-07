"""Tests 2, 3 and 4: the drop decision itself.

2. A completion lands in the table, and a later copy of that packet on another
   feed is dropped.
3. The same-cycle bypass: a copy arriving in the very cycle its completion
   arrives, before the table write is visible.
4. Both paths exercised together: a populated table and a live completion, with
   one feed matching each, in the same cycle.
"""

import cocotb

from dedup_common import DedupTB, N_FEEDS, send_packet


@cocotb.test()
async def test_later_copy_is_dropped(dut):
    """2. Completion goes into the table, the next copy is dropped."""
    tb = DedupTB(dut)
    await tb.start()

    seq = 0x4321

    # Feed 0 carries the copy that gets through.
    samples = await send_packet(tb, 0, seq=seq, beats=3)
    assert all(s["out_valid"][0] == 1 for s in samples)

    # CHECKSUM confirms it.
    tb.complete(seq)
    await tb.step()
    await tb.idle(2)

    # Feed 1's copy of the same packet is now dead weight.
    samples = await send_packet(tb, 1, seq=seq, beats=3)
    for i, s in enumerate(samples):
        assert s["out_valid"][1] == 0, (
            f"beat {i} of the duplicate on feed 1 was forwarded"
        )

    await tb.idle(2)


@cocotb.test()
async def test_same_cycle_bypass(dut):
    """3. Completion and matching copy in the same cycle."""
    tb = DedupTB(dut)
    await tb.start()

    seq = 0x5555

    tb.present(1, seq=seq, sop=1)
    tb.complete(seq)
    got = await tb.step()

    assert got["out_valid"][1] == 0, (
        "the bypass did not catch a copy arriving with its own completion"
    )

    # And the entry is in the table from the next cycle on.
    tb.present(2, seq=seq, sop=1)
    got = await tb.step()
    assert got["out_valid"][2] == 0, "the completion did not persist into the CPT"

    await tb.idle(2)


@cocotb.test()
async def test_table_and_bypass_together(dut):
    """4. One feed matches the table, another matches the live completion."""
    tb = DedupTB(dut)
    await tb.start()

    old_seq = 0x0A0A
    new_seq = 0x0B0B
    other_seq = 0x0C0C

    # Populate the table with a few entries, old_seq among them.
    for seq in (0x0101, old_seq, 0x0202):
        tb.complete(seq)
        await tb.step()
    await tb.idle(2)

    # Feed 0 matches a table entry.
    # Feed 1 matches the completion arriving this very cycle.
    # Feed 2 matches nothing and must survive.
    tb.present(0, seq=old_seq, sop=1)
    tb.present(1, seq=new_seq, sop=1)
    tb.present(2, seq=other_seq, sop=1)
    tb.complete(new_seq)
    got = await tb.step()

    assert got["out_valid"][0] == 0, "table match on feed 0 was not dropped"
    assert got["out_valid"][1] == 0, "bypass match on feed 1 was not dropped"
    assert got["out_valid"][2] == 1, "feed 2 matched nothing but was dropped"

    await tb.idle(2)


@cocotb.test()
async def test_duplicate_on_every_feed(dut):
    """2b. Once completed, the copy is dropped on all feeds at once."""
    tb = DedupTB(dut)
    await tb.start()

    seq = 0x7777
    tb.complete(seq)
    await tb.step()
    await tb.idle(2)

    for feed in range(N_FEEDS):
        tb.present(feed, seq=seq, sop=1)
    got = await tb.step()

    assert got["out_valid"] == [0] * N_FEEDS, (
        f"expected every feed dropped, got {got['out_valid']}"
    )

    await tb.idle(2)
