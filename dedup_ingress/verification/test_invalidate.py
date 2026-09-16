"""The sticky invalidate.

The arbiter gives up on a feed only after that feed has gone silent, which
happens only once its FIFO has run empty. So the beats to discard are never
ones already held: they are the tail of the abandoned packet, arriving late.

The bit is set combinationally, so a beat arriving in the same cycle as
invalidate_feed is already dropped. It clears on EOP, the end of that tail,
and on SOP, a fresh packet.

Both clear conditions are needed and each has its own test. Without EOP a feed
that recovers mid packet stays dead for good. Without SOP a tail that never
arrives leaves the bit set and swallows the healthy packet behind it.
"""

import cocotb

from feed_buffer_common import Beat, FeedBufferTB, N_FEEDS, beats_taken, drain


@cocotb.test()
async def test_drop_is_same_cycle(dut):
    """A beat arriving with invalidate_feed is dropped, not stored."""
    tb = FeedBufferTB(dut)
    await tb.start()

    tb.invalidate(0)
    tb.present(0, data=0xBAD0, seq=0x7000, sop=0, eop=0)
    await tb.step()

    await drain(tb, 6)
    assert len(beats_taken(tb, 0)) == 0, (
        "a beat arriving in the same cycle as invalidate_feed was forwarded"
    )


@cocotb.test()
async def test_the_bit_is_sticky(dut):
    """The drop keeps going after invalidate_feed has gone away."""
    tb = FeedBufferTB(dut)
    await tb.start()

    tb.invalidate(0)
    await tb.step()

    # invalidate_feed is low from here on, but these must still be dropped.
    for i in range(4):
        tb.present(0, data=0xBAD1 + i, seq=0x7001, sop=0, eop=0)
        await tb.step()

    await drain(tb, 6)
    assert len(beats_taken(tb, 0)) == 0, (
        "the drop stopped once invalidate_feed went low, so the bit is not "
        "sticky"
    )


@cocotb.test()
async def test_clears_on_eop(dut):
    """The abandoned tail ends with an EOP, and the feed recovers.

    The EOP beat itself belongs to the dead packet and is dropped. The packet
    after it is accepted normally.
    """
    tb = FeedBufferTB(dut)
    await tb.start()

    tb.invalidate(0)
    await tb.step()

    # Tail of the abandoned packet, ending in an EOP.
    for i in range(2):
        tb.present(0, data=0xBAD2, seq=0x7002, sop=0, eop=0)
        await tb.step()
    tb.present(0, data=0xBAD3, seq=0x7002, sop=0, eop=1)
    await tb.step()

    # A fresh packet, which must get through.
    for i in range(3):
        tb.present(
            0,
            data=0x7100 + i,
            seq=0x7003,
            sop=1 if i == 0 else 0,
            eop=1 if i == 2 else 0,
        )
        await tb.step()

    await drain(tb, 8)

    out = beats_taken(tb, 0)
    assert [b.data for b in out] == [0x7100, 0x7101, 0x7102], (
        f"expected only the fresh packet, got "
        f"{[hex(b.data) for b in out]}"
    )


@cocotb.test()
async def test_clears_on_sop_and_that_beat_is_accepted(dut):
    """The tail never arrives. A SOP clears the bit and is itself accepted.

    Without this, a feed whose abandoned tail is lost would stay dead and the
    healthy packet behind it would be swallowed. The SOP beat carries real
    data, so it has to be accepted, not used up clearing the bit.
    """
    tb = FeedBufferTB(dut)
    await tb.start()

    tb.invalidate(0)
    await tb.step()

    # No tail at all. Straight to a fresh packet.
    for i in range(3):
        tb.present(
            0,
            data=0x7200 + i,
            seq=0x7004,
            sop=1 if i == 0 else 0,
            eop=1 if i == 2 else 0,
        )
        await tb.step()

    await drain(tb, 8)

    out = beats_taken(tb, 0)
    assert len(out) == 3, (
        f"expected the whole fresh packet, got {len(out)} beats: the SOP was "
        f"consumed clearing the bit instead of being accepted"
    )
    assert out[0] == Beat(0x7200, 0x7004, 1, 0), (
        f"the SOP beat was not accepted intact: {out[0]}"
    )
    assert [b.data for b in out] == [0x7200, 0x7201, 0x7202], (
        f"packet corrupted: {[hex(b.data) for b in out]}"
    )


@cocotb.test()
async def test_single_beat_packet_after_invalidate(dut):
    """A one beat packet carries both markers and is accepted."""
    tb = FeedBufferTB(dut)
    await tb.start()

    tb.invalidate(0)
    await tb.step()

    tb.present(0, data=0x7300, seq=0x7005, sop=1, eop=1)
    await tb.step()

    await drain(tb, 6)

    out = beats_taken(tb, 0)
    assert len(out) == 1, (
        f"expected the single beat packet through, got {len(out)} beats"
    )
    assert out[0] == Beat(0x7300, 0x7005, 1, 1), f"beat corrupted: {out[0]}"


@cocotb.test()
async def test_invalidate_with_no_traffic_then_a_late_tail(dut):
    """Invalidate on a silent feed. The tail arriving much later still dies.

    This is the real shape of the case: the arbiter gives up because the feed
    went quiet, so nothing is arriving when invalidate_feed fires. The beats
    to drop turn up afterwards.
    """
    tb = FeedBufferTB(dut)
    await tb.start()

    tb.invalidate(0)
    await tb.step()

    await tb.idle(10)

    for i in range(2):
        tb.present(0, data=0xBAD4 + i, seq=0x7006, sop=0, eop=0)
        await tb.step()
    tb.present(0, data=0xBAD6, seq=0x7006, sop=0, eop=1)
    await tb.step()

    await drain(tb, 6)
    assert len(beats_taken(tb, 0)) == 0, (
        "a late tail was forwarded, so the bit did not survive the quiet"
    )


@cocotb.test()
async def test_invalidate_is_scoped_to_one_feed(dut):
    """Invalidating feed 0 must not touch feeds 1 to 3."""
    tb = FeedBufferTB(dut)
    await tb.start()

    tb.invalidate(0)
    for f in range(N_FEEDS):
        tb.present(f, data=0x7400 + f, seq=0x7007, sop=0, eop=0)
    await tb.step()

    await drain(tb, 6)

    assert len(beats_taken(tb, 0)) == 0, "feed 0 was not dropped"
    for f in range(1, N_FEEDS):
        out = beats_taken(tb, f)
        assert len(out) == 1, (
            f"feed {f} was dropped alongside the invalidated feed 0"
        )
        assert out[0].data == 0x7400 + f, f"feed {f}: beat corrupted"


@cocotb.test()
async def test_two_feeds_invalidated_independently(dut):
    """Two feeds dead at once, the other two untouched."""
    tb = FeedBufferTB(dut)
    await tb.start()

    tb.invalidate(0)
    tb.invalidate(2)
    await tb.step()

    for f in range(N_FEEDS):
        tb.present(f, data=0x7500 + f, seq=0x7008, sop=0, eop=0)
    await tb.step()

    await drain(tb, 6)

    for f in (0, 2):
        assert len(beats_taken(tb, f)) == 0, f"feed {f} should be dropping"
    for f in (1, 3):
        assert len(beats_taken(tb, f)) == 1, f"feed {f} should be untouched"
