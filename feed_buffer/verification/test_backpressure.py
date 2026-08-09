"""Flow control in both directions.

Downstream: the arbiter lowers out_ready and the output register holds its
contents, unchanged, until it is taken.

Upstream: in_ready comes from FIFO occupancy alone, so it only falls when that
feed's FIFO is genuinely full. The arbiter's readiness never reaches it, which
is what keeps feeds independent of one another.
"""

import cocotb

from feed_buffer_common import (
    Beat,
    FeedBufferTB,
    FIFO_DEPTH,
    N_FEEDS,
    beats_taken,
    drain,
)


@cocotb.test()
async def test_held_beat_is_stable(dut):
    """out_ready low: the presented beat does not change and is taken once."""
    tb = FeedBufferTB(dut)
    await tb.start()

    tb.out_ready = [0] * N_FEEDS
    tb.present(0, data=0xABCD, seq=0x1111, sop=1, eop=1)
    await tb.step()

    for i in range(5):
        tb.out_ready = [0] * N_FEEDS
        got = await tb.step()
        assert got["out_valid"][0] == 1, f"cycle {i}: out_valid dropped"
        assert tb.out_beat(got, 0) == Beat(0xABCD, 0x1111, 1, 1), (
            f"cycle {i}: the held beat changed"
        )

    tb.out_ready = [1] * N_FEEDS
    await drain(tb, 4)

    out = beats_taken(tb, 0)
    assert len(out) == 1, f"the held beat was taken {len(out)} times"
    assert out[0] == Beat(0xABCD, 0x1111, 1, 1), "beat corrupted"


@cocotb.test()
async def test_in_ready_falls_only_when_full(dut):
    """in_ready stays high until the FIFO is genuinely full.

    With the arbiter stalled one beat sits in the output register, so it takes
    FIFO_DEPTH+1 accepted beats before in_ready falls.
    """
    tb = FeedBufferTB(dut)
    await tb.start()

    capacity = FIFO_DEPTH + 1

    for i in range(capacity):
        tb.out_ready = [0] * N_FEEDS
        tb.present(0, data=0x2000 + i, seq=0x2222, sop=1 if i == 0 else 0)
        got = await tb.step()
        assert got["in_ready"][0] == 1, (
            f"in_ready fell at {i} beats, before the FIFO was full"
        )

    tb.out_ready = [0] * N_FEEDS
    got = await tb.step()
    assert got["in_ready"][0] == 0, (
        f"in_ready did not fall after {capacity} accepted beats"
    )


@cocotb.test()
async def test_a_full_feed_does_not_block_the_others(dut):
    """Feed 0 full, feeds 1 to 3 still accepting and still forwarding."""
    tb = FeedBufferTB(dut)
    await tb.start()

    # Fill feed 0 while every feed is stalled.
    for i in range(FIFO_DEPTH + 2):
        tb.out_ready = [0] * N_FEEDS
        tb.present(0, data=0x3000 + i, seq=0x3333, sop=1 if i == 0 else 0)
        await tb.step()

    got = await tb.step()
    assert got["in_ready"][0] == 0, "feed 0 should be full"
    for f in range(1, N_FEEDS):
        assert got["in_ready"][f] == 1, (
            f"feed {f} was held off because feed 0 filled up"
        )

    # The other feeds keep working while feed 0 stays full and stalled.
    for f in range(1, N_FEEDS):
        tb.out_ready = [0] + [1] * (N_FEEDS - 1)
        tb.present(f, data=0x40 + f, seq=0x4444, sop=1, eop=1)
        await tb.step()
        await tb.idle(2)

    for f in range(1, N_FEEDS):
        out = beats_taken(tb, f)
        assert len(out) == 1, (
            f"feed {f}: expected one beat through, got {len(out)}"
        )
        assert out[0] == Beat(0x40 + f, 0x4444, 1, 1), f"feed {f}: corrupted"

    assert len(beats_taken(tb, 0)) == 0, "feed 0 forwarded while stalled"


@cocotb.test()
async def test_full_feed_recovers_and_loses_nothing(dut):
    """Fill to full, then drain. Every beat comes out once, in order."""
    tb = FeedBufferTB(dut)
    await tb.start()

    capacity = FIFO_DEPTH + 1
    sent = []

    for i in range(capacity):
        tb.out_ready = [0] * N_FEEDS
        tb.present(0, data=0x5000 + i, seq=0x5555, sop=1 if i == 0 else 0)
        await tb.step()
        sent.append(0x5000 + i)

    # Confirm full, then release.
    tb.out_ready = [0] * N_FEEDS
    got = await tb.step()
    assert got["in_ready"][0] == 0, "expected the FIFO to be full"

    tb.out_ready = [1] * N_FEEDS
    await drain(tb, capacity + 8)

    got = await tb.step()
    assert got["in_ready"][0] == 1, "in_ready did not recover after draining"

    out = beats_taken(tb, 0)
    assert [b.data for b in out] == sent, (
        f"beats lost or reordered across a full FIFO:\n"
        f"  got  {len(out)} beats\n"
        f"  sent {len(sent)} beats"
    )


@cocotb.test()
async def test_beat_offered_while_full_is_not_accepted(dut):
    """in_ready low means the beat was not taken, so it must not appear.

    The beat presented here is never accepted, so it has to be presented
    again afterwards to get through. If the block took it anyway, the count
    at the end would be one too high.
    """
    tb = FeedBufferTB(dut)
    await tb.start()

    capacity = FIFO_DEPTH + 1

    for i in range(capacity):
        tb.out_ready = [0] * N_FEEDS
        tb.present(0, data=0x6000 + i, seq=0x6666, sop=1 if i == 0 else 0)
        await tb.step()

    # Offered while full. Not accepted, so it must never come out.
    tb.out_ready = [0] * N_FEEDS
    tb.present(0, data=0xDEAD, seq=0x6666)
    got = await tb.step()
    assert got["in_ready"][0] == 0, "expected the FIFO to be full"

    tb.out_ready = [1] * N_FEEDS
    await drain(tb, capacity + 8)

    out = beats_taken(tb, 0)
    assert len(out) == capacity, (
        f"expected {capacity} beats, got {len(out)}: a beat offered while "
        f"full was accepted anyway"
    )
    assert all(b.data != 0xDEAD for b in out), (
        "the beat offered while full came out"
    )
