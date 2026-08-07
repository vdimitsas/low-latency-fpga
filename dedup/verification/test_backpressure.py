"""Tests 10 and 11: flow control.

10. in_ready follows out_ready per feed, and the comparator tree is not in that
    path: a feed's ready must not change just because its packet is being
    dropped.
11. Feeds are independent. Stalling or dropping one must not disturb another.
"""

import cocotb

from dedup_common import DedupTB, N_FEEDS, seq_into_beat


@cocotb.test()
async def test_in_ready_follows_out_ready(dut):
    """10. Per feed, and nothing else feeds into it."""
    tb = DedupTB(dut)
    await tb.start()

    patterns = [
        [1, 1, 1, 1],
        [0, 1, 1, 1],
        [1, 0, 1, 0],
        [0, 0, 0, 0],
        [1, 1, 0, 0],
    ]

    for pattern in patterns:
        tb.out_ready = list(pattern)
        got = await tb.step()
        assert got["in_ready"] == pattern, (
            f"in_ready {got['in_ready']} did not follow out_ready {pattern}"
        )

    await tb.idle(2)


@cocotb.test()
async def test_ready_is_independent_of_the_drop(dut):
    """10b. A dropped copy must not change that feed's ready.

    The drop decision consumes nothing downstream, so it has no business
    reaching in_ready. If the comparator tree ever gets wired into the ready
    path this fails.
    """
    tb = DedupTB(dut)
    await tb.start()

    seq = 0xAB
    tb.complete(seq)
    await tb.step()
    await tb.idle(2)

    # A copy that will be dropped, with ready high.
    tb.out_ready = [1] * N_FEEDS
    tb.present(0, seq=seq, sop=1)
    got = await tb.step()
    assert got["out_valid"][0] == 0, "the duplicate should have been dropped"
    assert got["in_ready"][0] == 1, "in_ready fell because the copy was dropped"

    # Same copy, ready low. Still only out_ready decides.
    tb.out_ready = [0] * N_FEEDS
    tb.present(0, seq=seq, sop=1)
    got = await tb.step()
    assert got["in_ready"][0] == 0, "in_ready did not follow out_ready low"

    await tb.idle(2)


@cocotb.test()
async def test_stalled_feed_does_not_disturb_others(dut):
    """11. Feed 0 held off, feeds 1 to 3 stream normally."""
    tb = DedupTB(dut)
    await tb.start()

    for beat in range(4):
        tb.out_ready = [0] + [1] * (N_FEEDS - 1)
        for feed in range(N_FEEDS):
            tb.present(
                feed,
                data=seq_into_beat(0xF000 + feed) if beat == 0 else (0x30 + beat),
                sop=1 if beat == 0 else 0,
                eop=1 if beat == 3 else 0,
            )
        got = await tb.step()

        assert got["in_ready"][0] == 0, "feed 0 should be held off"
        for feed in range(1, N_FEEDS):
            assert got["in_ready"][feed] == 1, f"feed {feed} was wrongly held off"
            assert got["out_valid"][feed] == 1, f"feed {feed} beat {beat} was dropped"

    await tb.idle(2)


@cocotb.test()
async def test_drop_on_one_feed_does_not_disturb_others(dut):
    """11b. A drop is scoped to the feed it happens on."""
    tb = DedupTB(dut)
    await tb.start()

    doomed = 0x1A1A
    tb.complete(doomed)
    await tb.step()
    await tb.idle(2)

    tb.present(0, seq=doomed, sop=1)
    for feed in range(1, N_FEEDS):
        tb.present(feed, seq=0x2B00 + feed, sop=1)
    got = await tb.step()

    assert got["out_valid"][0] == 0, "the duplicate on feed 0 survived"
    for feed in range(1, N_FEEDS):
        assert got["out_valid"][feed] == 1, (
            f"feed {feed} was dropped alongside the duplicate on feed 0"
        )

    await tb.idle(2)


@cocotb.test()
async def test_seq_latched_only_on_accepted_sop(dut):
    """A SOP presented while ready is low must not update the seq register.

    The RTL qualifies the seq_regs write with in_valid && in_ready && in_sop,
    so a beat the block never accepted leaves the context alone.
    """
    tb = DedupTB(dut)
    await tb.start()

    held = 0x3C3C
    stalled = 0x4D4D

    # Accept a SOP so the register holds `held`.
    tb.out_ready = [1] * N_FEEDS
    tb.present(0, seq=held, sop=1)
    await tb.step()

    # Present a different SOP while feed 0 is stalled.
    tb.out_ready = [0] + [1] * (N_FEEDS - 1)
    tb.present(0, seq=stalled, sop=1)
    await tb.step()

    # Mid packet beat: the register must still hold `held`.
    tb.out_ready = [1] * N_FEEDS
    tb.present(0, data=0x55, sop=0)
    got = await tb.step()
    assert got["out_seq"][0] == held, (
        f"seq register moved on a stalled SOP: got {got['out_seq'][0]:#x} "
        f"expected {held:#x}"
    )

    await tb.idle(2)
