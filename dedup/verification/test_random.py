"""Test 12: constrained random against the golden model.

Every cycle the model in dedup_common predicts out_valid, in_ready and out_seq
from the same stimulus the DUT sees, and DedupTB.step checks them. This test
therefore only has to generate traffic worth checking.

The constraints matter more than the volume. Sequence numbers are drawn from a
small pool so that duplicates and completions collide often, and completions
are drawn from packets that have actually been seen, so the CPT fills with
plausible values rather than noise.
"""

import random

import cocotb

from dedup_common import DedupTB, N_FEEDS

CYCLES = 3000
SEQ_POOL = 64


class FeedState:
    """Tracks whether a feed is mid packet, so SOP and EOP stay coherent."""

    def __init__(self):
        self.in_packet = False
        self.seq = 0
        self.remaining = 0


@cocotb.test()
async def test_constrained_random(dut):
    tb = DedupTB(dut)
    await tb.start()

    rnd = random.Random(0xD3D0)
    feeds = [FeedState() for _ in range(N_FEEDS)]
    seen = []

    for _ in range(CYCLES):
        # Backpressure: mostly ready, occasionally not, independently per feed.
        tb.out_ready = [0 if rnd.random() < 0.15 else 1 for _ in range(N_FEEDS)]

        for f, st in enumerate(feeds):
            if not st.in_packet:
                if rnd.random() < 0.55:
                    st.seq = rnd.randrange(SEQ_POOL)
                    st.remaining = rnd.randint(1, 6)
                    st.in_packet = True
                    seen.append(st.seq)
                    tb.present(
                        f,
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
                    data=rnd.getrandbits(16),
                    sop=0,
                    eop=1 if last else 0,
                )
                st.remaining -= 1
                if last:
                    st.in_packet = False

        # Completions, drawn from packets that have actually been offered so
        # the table fills with values the feeds can plausibly repeat.
        if seen and rnd.random() < 0.18:
            tb.complete(rnd.choice(seen[-40:]))

        await tb.step()

    await tb.idle(4)


@cocotb.test()
async def test_constrained_random_heavy_duplicates(dut):
    """Same shape, but a tiny seq pool so almost everything is a duplicate."""
    tb = DedupTB(dut)
    await tb.start()

    rnd = random.Random(0xBEEF)
    feeds = [FeedState() for _ in range(N_FEEDS)]
    pool = 6

    for _ in range(CYCLES // 2):
        tb.out_ready = [0 if rnd.random() < 0.1 else 1 for _ in range(N_FEEDS)]

        for f, st in enumerate(feeds):
            if not st.in_packet:
                if rnd.random() < 0.7:
                    st.seq = rnd.randrange(pool)
                    st.remaining = rnd.randint(1, 4)
                    st.in_packet = True
                    tb.present(
                        f, seq=st.seq, sop=1, eop=1 if st.remaining == 1 else 0
                    )
                    st.remaining -= 1
                    if st.remaining == 0:
                        st.in_packet = False
            else:
                last = st.remaining == 1
                tb.present(f, data=rnd.getrandbits(16), sop=0, eop=1 if last else 0)
                st.remaining -= 1
                if last:
                    st.in_packet = False

        if rnd.random() < 0.3:
            tb.complete(rnd.randrange(pool))

        await tb.step()

    await tb.idle(4)
