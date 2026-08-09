"""Flow control in both directions.

Downstream: the arbiter lowers out_ready and the output register holds its
contents, unchanged, until it is taken.

Upstream: in_ready is !fifo_full || out_reg_free. It falls when that feed's
FIFO is full and nothing is leaving. It does not fall when the FIFO is full but
the arbiter is taking a beat that cycle, because the whole feed shifts along by
one and a slot opens on the same edge.
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


@cocotb.test()
async def test_full_fifo_accepts_a_beat_while_draining(dut):
    """A full feed still accepts a beat on the cycle the arbiter takes one.

    Without the out_reg_free term in in_ready this is where a bubble appears.
    The FIFO pops when the arbiter takes the output register, but full is
    computed from the pointers as they stood and does not clear until the next
    cycle, so a beat arriving in between would be refused for nothing.
    """
    tb = FeedBufferTB(dut)
    await tb.start()

    capacity = FIFO_DEPTH + 1
    sent = []

    # Fill to full with the arbiter stalled.
    for i in range(capacity):
        tb.out_ready = [0] * N_FEEDS
        tb.present(0, data=0x8000 + i, seq=0x8888, sop=1 if i == 0 else 0)
        await tb.step()
        sent.append(0x8000 + i)

    tb.out_ready = [0] * N_FEEDS
    got = await tb.step()
    assert got["in_ready"][0] == 0, "expected the feed to be blocked while stalled"

    # Arbiter takes a beat and a new one arrives in the same cycle.
    tb.out_ready = [1] * N_FEEDS
    tb.present(0, data=0x8FFF, seq=0x8888)
    got = await tb.step()
    assert got["in_ready"][0] == 1, (
        "the feed refused a beat on the cycle one was leaving, so the bubble "
        "is still there"
    )
    sent.append(0x8FFF)

    tb.out_ready = [1] * N_FEEDS
    await drain(tb, capacity + 12)

    out = beats_taken(tb, 0)
    assert [b.data for b in out] == sent, (
        f"the accepted beat was lost or reordered: got {len(out)} beats, "
        f"sent {len(sent)}"
    )


@cocotb.test()
async def test_sustained_full_throughput(dut):
    """Full FIFO, arbiter ready every cycle, a beat in every cycle.

    One in and one out on every edge, sustained. Occupancy never changes and
    no beat is lost or reordered.
    """
    tb = FeedBufferTB(dut)
    await tb.start()

    capacity = FIFO_DEPTH + 1
    sent = []

    for i in range(capacity):
        tb.out_ready = [0] * N_FEEDS
        tb.present(0, data=0x9000 + i, seq=0x9999, sop=1 if i == 0 else 0)
        await tb.step()
        sent.append(0x9000 + i)

    for i in range(16):
        tb.out_ready = [1] * N_FEEDS
        tb.present(0, data=0x9500 + i, seq=0x9999)
        got = await tb.step()
        assert got["in_ready"][0] == 1, (
            f"cycle {i}: the feed stalled during sustained one in one out"
        )
        sent.append(0x9500 + i)

    tb.out_ready = [1] * N_FEEDS
    await drain(tb, capacity + 24)

    out = beats_taken(tb, 0)
    assert [b.data for b in out] == sent, (
        f"beats lost or reordered under sustained full throughput: "
        f"got {len(out)}, sent {len(sent)}"
    )
