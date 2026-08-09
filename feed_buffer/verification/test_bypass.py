"""The bypass path.

A beat arriving to an empty FIFO with the output register free goes straight
into that register, so it is presented one cycle later. A beat that has to be
stored is presented two cycles later at the earliest. The tests here use that
difference rather than reaching inside the block: latency at the port is what
the bypass is for, and it is what a change to the bypass would break.

"Free" means the register holds nothing, or the arbiter is taking what it
holds this cycle. It is not the same as out_ready being high: a beat can
bypass into an empty register while the arbiter is stalled, and wait there.
"""

import cocotb

from feed_buffer_common import (
    Beat,
    FeedBufferTB,
    N_FEEDS,
    beats_taken,
    cycles_taken,
    drain,
)


@cocotb.test()
async def test_bypass_costs_one_cycle(dut):
    """Empty FIFO, free register: presented on the very next cycle."""
    tb = FeedBufferTB(dut)
    await tb.start()

    sent_on = tb.cycle
    tb.present(0, data=0x11, seq=0x900, sop=1, eop=1)
    got = await tb.step()
    assert got["out_valid"][0] == 0, "the beat appeared in its own cycle"

    await drain(tb, 4)
    out = beats_taken(tb, 0)
    at = cycles_taken(tb, 0)

    assert len(out) == 1, f"expected one beat out, got {len(out)}"
    assert out[0] == Beat(0x11, 0x900, 1, 1), f"beat corrupted: {out[0]}"
    assert at[0] == sent_on + 1, (
        f"bypass latency wrong: presented on cycle {at[0]}, "
        f"sent on {sent_on}, expected {sent_on + 1}"
    )


@cocotb.test()
async def test_bypass_works_on_every_feed(dut):
    """Each feed has its own bypass path, not just feed 0."""
    tb = FeedBufferTB(dut)
    await tb.start()

    sent_on = {}
    for f in range(N_FEEDS):
        sent_on[f] = tb.cycle
        tb.present(f, data=0x30 + f, seq=0x910 + f, sop=1, eop=1)
        await tb.step()
        await tb.idle(2)

    await drain(tb, 4)

    for f in range(N_FEEDS):
        out = beats_taken(tb, f)
        at = cycles_taken(tb, f)
        assert len(out) == 1, f"feed {f}: expected one beat, got {len(out)}"
        assert out[0] == Beat(0x30 + f, 0x910 + f, 1, 1), (
            f"feed {f}: beat corrupted: {out[0]}"
        )
        assert at[0] == sent_on[f] + 1, (
            f"feed {f}: did not bypass, presented on cycle {at[0]} "
            f"instead of {sent_on[f] + 1}"
        )


@cocotb.test()
async def test_bypass_into_an_empty_register_while_stalled(dut):
    """out_ready low, register empty: the beat still bypasses and waits.

    This is the case that makes "free" different from "ready". The arbiter is
    taking nothing, but the register holds nothing either, so the beat goes
    straight there. It is then held, unchanged, until the arbiter is ready.
    """
    tb = FeedBufferTB(dut)
    await tb.start()

    tb.out_ready = [0] * N_FEEDS
    tb.present(0, data=0x44, seq=0x902, sop=1, eop=1)
    await tb.step()

    # Presented from the next cycle, and held while the arbiter stays stalled.
    for i in range(3):
        tb.out_ready = [0] * N_FEEDS
        got = await tb.step()
        assert got["out_valid"][0] == 1, (
            f"cycle {i}: the beat was not presented while stalled, so it went "
            f"to the FIFO instead of bypassing into a free register"
        )
        assert tb.out_beat(got, 0) == Beat(0x44, 0x902, 1, 1), (
            f"cycle {i}: the held beat changed while the arbiter was stalled"
        )

    tb.out_ready = [1] * N_FEEDS
    await drain(tb, 4)

    out = beats_taken(tb, 0)
    assert len(out) == 1, (
        f"the held beat was taken {len(out)} times, expected once"
    )


@cocotb.test()
async def test_no_bypass_when_the_register_is_occupied(dut):
    """Register full and arbiter stalled: the next beat has to be stored."""
    tb = FeedBufferTB(dut)
    await tb.start()

    # First beat bypasses into the register, then the arbiter stalls.
    tb.out_ready = [0] * N_FEEDS
    tb.present(0, data=0x55, seq=0x903, sop=1, eop=0)
    await tb.step()

    # Second beat: the register is occupied, so this one is stored.
    tb.out_ready = [0] * N_FEEDS
    sent_on = tb.cycle
    tb.present(0, data=0x66, seq=0x903, sop=0, eop=1)
    await tb.step()

    tb.out_ready = [1] * N_FEEDS
    await drain(tb, 6)

    out = beats_taken(tb, 0)
    at = cycles_taken(tb, 0)
    assert [b.data for b in out] == [0x55, 0x66], (
        f"order broken across the stall: {[hex(b.data) for b in out]}"
    )
    assert at[1] >= sent_on + 2, (
        f"the second beat bypassed while the register was occupied: "
        f"presented on cycle {at[1]}, sent on {sent_on}"
    )


@cocotb.test()
async def test_no_bypass_when_the_fifo_is_not_empty(dut):
    """A beat must never overtake one already queued.

    Fill the FIFO behind a stalled register, then release the arbiter and
    present another beat in that same cycle. The register frees up, but the
    FIFO still holds beats, so the new one has to queue behind them.
    """
    tb = FeedBufferTB(dut)
    await tb.start()

    for i in range(3):
        tb.out_ready = [0] * N_FEEDS
        tb.present(0, data=0x70 + i, seq=0x904, sop=1 if i == 0 else 0)
        await tb.step()

    tb.out_ready = [1] * N_FEEDS
    tb.present(0, data=0x73, seq=0x904, eop=1)
    await tb.step()

    await drain(tb, 8)

    out = beats_taken(tb, 0)
    assert [b.data for b in out] == [0x70, 0x71, 0x72, 0x73], (
        f"a beat overtook one already queued: {[hex(b.data) for b in out]}"
    )


@cocotb.test()
async def test_alternating_bypass_and_buffered(dut):
    """Backpressure coming and going on one feed. Order must hold throughout.

    Some of these beats take the bypass, some go through the FIFO. Which is
    which is not asserted, only that every one comes out exactly once, in the
    order it went in.
    """
    tb = FeedBufferTB(dut)
    await tb.start()

    ready_pattern = [1, 0, 0, 1, 1, 0, 1, 0, 0, 0, 1, 1]
    sent = []

    for i, ready in enumerate(ready_pattern):
        tb.out_ready = [ready] + [1] * (N_FEEDS - 1)
        tb.present(0, data=0x80 + i, seq=0x905, sop=1 if i == 0 else 0)
        sent.append(0x80 + i)
        await tb.step()

    tb.out_ready = [1] * N_FEEDS
    await drain(tb, 24)

    out = beats_taken(tb, 0)
    assert [b.data for b in out] == sent, (
        f"order broken across mixed bypass and buffered traffic:\n"
        f"  got  {[hex(b.data) for b in out]}\n"
        f"  sent {[hex(d) for d in sent]}"
    )
