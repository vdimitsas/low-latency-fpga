"""Traffic that must come out exactly as it went in.

feed_buffer does not change a beat. It stores it, holds it while the arbiter
is busy, and presents it again. These tests check the plain case: every field
intact, order preserved, and feeds independent of one another.
"""

import cocotb

from feed_buffer_common import Beat, FeedBufferTB, N_FEEDS, beats_taken, drain


@cocotb.test()
async def test_single_beat_packet(dut):
    """A one beat packet carries both markers and every field survives."""
    tb = FeedBufferTB(dut)
    await tb.start()

    tb.present(0, data=0xCAFE, seq=0x1234, sop=1, eop=1)
    await tb.step()

    await drain(tb, 4)
    out = beats_taken(tb, 0)
    assert len(out) == 1, f"expected one beat out, got {len(out)}"
    assert out[0] == Beat(0xCAFE, 0x1234, 1, 1), f"beat corrupted: {out[0]}"


@cocotb.test()
async def test_multi_beat_packet_keeps_order(dut):
    """Five beats in, five beats out, in the order they arrived."""
    tb = FeedBufferTB(dut)
    await tb.start()

    beats = 5
    seq = 0x2000
    for i in range(beats):
        tb.present(
            0,
            data=0xA000 + i,
            seq=seq,
            sop=1 if i == 0 else 0,
            eop=1 if i == beats - 1 else 0,
        )
        await tb.step()

    await drain(tb, 8)
    out = beats_taken(tb, 0)
    assert len(out) == beats, f"expected {beats} beats out, got {len(out)}"
    for i, b in enumerate(out):
        assert b.data == 0xA000 + i, f"beat {i} out of order: {b}"
        assert b.seq == seq, f"beat {i} lost its seq: {b}"
    assert out[0].sop == 1 and out[0].eop == 0, "first beat markers wrong"
    assert out[-1].sop == 0 and out[-1].eop == 1, "last beat markers wrong"


@cocotb.test()
async def test_all_feeds_independent(dut):
    """Four packets streaming at once, each arriving intact on its own feed."""
    tb = FeedBufferTB(dut)
    await tb.start()

    beats = 4
    for i in range(beats):
        for f in range(N_FEEDS):
            tb.present(
                f,
                data=(f << 12) | i,
                seq=0x3000 + f,
                sop=1 if i == 0 else 0,
                eop=1 if i == beats - 1 else 0,
            )
        await tb.step()

    await drain(tb, 8)

    for f in range(N_FEEDS):
        out = beats_taken(tb, f)
        assert len(out) == beats, (
            f"feed {f}: expected {beats} beats, got {len(out)}"
        )
        for i, b in enumerate(out):
            assert b.data == (f << 12) | i, f"feed {f} beat {i} wrong: {b}"
            assert b.seq == 0x3000 + f, f"feed {f} beat {i} wrong seq: {b}"


@cocotb.test()
async def test_back_to_back_packets(dut):
    """Two packets on one feed with no gap. The boundary must stay put."""
    tb = FeedBufferTB(dut)
    await tb.start()

    for pkt, seq in enumerate((0x4444, 0x5555)):
        for i in range(3):
            tb.present(
                0,
                data=(pkt << 8) | i,
                seq=seq,
                sop=1 if i == 0 else 0,
                eop=1 if i == 2 else 0,
            )
            await tb.step()

    await drain(tb, 8)
    out = beats_taken(tb, 0)
    assert len(out) == 6, f"expected 6 beats, got {len(out)}"

    sops = [i for i, b in enumerate(out) if b.sop]
    eops = [i for i, b in enumerate(out) if b.eop]
    assert sops == [0, 3], f"SOP landed in the wrong place: {sops}"
    assert eops == [2, 5], f"EOP landed in the wrong place: {eops}"
    assert [b.seq for b in out] == [0x4444] * 3 + [0x5555] * 3, (
        "sequence numbers did not follow their packets"
    )
