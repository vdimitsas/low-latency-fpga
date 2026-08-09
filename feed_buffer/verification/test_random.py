"""Constrained random against the golden model.

The model in feed_buffer_common mirrors the RTL cycle for cycle: a FIFO, an
output register with a valid bit, a sticky invalidate bit, and the bypass. Its
predictions for in_ready, out_valid and every output field are checked by
FeedBufferTB.step on every cycle of every test, directed and random alike.

So these tests only have to generate traffic worth checking. Three regimes,
each pushing the block into a different corner: light backpressure where the
bypass carries almost everything, heavy backpressure where the FIFOs fill and
in_ready starts falling, and an invalidate-heavy regime where the sticky bit
is set and cleared constantly.
"""

import random

import cocotb

from feed_buffer_common import FeedBufferTB, N_FEEDS

CYCLES = 3000


class FeedState:
    """Tracks whether a feed is mid packet, so SOP and EOP stay coherent."""

    def __init__(self):
        self.in_packet = False
        self.remaining = 0
        self.seq = 0


async def _run(tb, rnd, cycles, stall_p, start_p, invalidate_p, max_beats=6):
    feeds = [FeedState() for _ in range(N_FEEDS)]
    seq_counter = 0

    for _ in range(cycles):
        tb.out_ready = [
            0 if rnd.random() < stall_p else 1 for _ in range(N_FEEDS)
        ]

        for f, st in enumerate(feeds):
            if not st.in_packet:
                if rnd.random() < start_p:
                    seq_counter = (seq_counter + 1) & 0xFFFF
                    st.seq = seq_counter
                    st.remaining = rnd.randint(1, max_beats)
                    st.in_packet = True
                    tb.present(
                        f,
                        data=rnd.getrandbits(32),
                        seq=st.seq,
                        sop=1,
                        eop=1 if st.remaining == 1 else 0,
                    )
                    st.remaining -= 1
                    if st.remaining == 0:
                        st.in_packet = False
            else:
                last = st.remaining == 1
                tb.present(
                    f,
                    data=rnd.getrandbits(32),
                    seq=st.seq,
                    sop=0,
                    eop=1 if last else 0,
                )
                st.remaining -= 1
                if last:
                    st.in_packet = False

        if invalidate_p:
            for f in range(N_FEEDS):
                if rnd.random() < invalidate_p:
                    tb.invalidate(f)
                    # The arbiter abandons the packet, so the generator stops
                    # tracking it too. What arrives next on that feed is a
                    # fresh SOP, which is what clears the sticky bit.
                    feeds[f].in_packet = False
                    feeds[f].remaining = 0

        await tb.step()

    await tb.idle(8)


@cocotb.test()
async def test_random_light_backpressure(dut):
    """Mostly ready, so the bypass carries almost everything."""
    tb = FeedBufferTB(dut)
    await tb.start()
    await _run(
        tb,
        random.Random(0xFB01),
        CYCLES,
        stall_p=0.10,
        start_p=0.55,
        invalidate_p=0.0,
    )


@cocotb.test()
async def test_random_heavy_backpressure(dut):
    """Stalled most of the time, so the FIFOs fill and in_ready falls.

    This is the regime that exercises the stored path, the full condition and
    the ordering guarantee between bypassed and buffered beats.
    """
    tb = FeedBufferTB(dut)
    await tb.start()
    await _run(
        tb,
        random.Random(0xFB02),
        CYCLES,
        stall_p=0.75,
        start_p=0.80,
        invalidate_p=0.0,
    )


@cocotb.test()
async def test_random_with_invalidate(dut):
    """Invalidate firing often, on every feed, mixed with normal traffic.

    The sticky bit is set and cleared constantly here, against a background of
    backpressure, so the interaction between dropping and the FIFO is covered
    rather than just the two on their own.
    """
    tb = FeedBufferTB(dut)
    await tb.start()
    await _run(
        tb,
        random.Random(0xFB03),
        CYCLES,
        stall_p=0.35,
        start_p=0.65,
        invalidate_p=0.04,
    )


@cocotb.test()
async def test_random_single_beat_packets(dut):
    """Every packet is one beat, so SOP and EOP land on the same cycle.

    Single beat packets are the case where the two invalidate clear
    conditions collide, and where a FIFO holds the most distinct packets at
    once.
    """
    tb = FeedBufferTB(dut)
    await tb.start()
    await _run(
        tb,
        random.Random(0xFB04),
        CYCLES // 2,
        stall_p=0.50,
        start_p=0.85,
        invalidate_p=0.03,
        max_beats=1,
    )
