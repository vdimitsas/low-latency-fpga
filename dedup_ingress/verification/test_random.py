"""Test 12: constrained random against the golden model.

Every cycle the model in dedup_ingress_common predicts in_ready, the output
beat and the extracted metadata from the same stimulus the DUT sees, and
DedupIngressTB.step checks them. This test therefore only has to generate
traffic worth checking.

The constraints matter more than the volume. Sequence numbers are drawn from a
small pool so that duplicates and completions collide often, and completions
are drawn from packets that have actually been seen, so the CPT fills with
plausible values rather than noise.

This is also where the beat carrying the sequence number is randomised. The
directed suites run over fixed values so a failure is reproducible. Here it is
drawn per packet, so arrival beats the directed tests do not name are covered
too, and the model checks every cycle of them.
"""

import random

import cocotb

from dedup_ingress_common import DedupIngressTB, N_FEEDS

CYCLES = 3000
SEQ_POOL = 64

# The range the arrival beat is drawn from. It is never 0: the sequence number
# sits behind the IP and UDP headers, so it cannot land in the first beat.
SEQ_BEAT_MIN = 3
SEQ_BEAT_MAX = 8


class FeedState:
    """Tracks where a feed is in its packet, so the framing stays coherent."""

    def __init__(self):
        self.in_packet = False
        self.seq = 0
        self.beat = 0
        self.beats = 0
        self.seq_beat = SEQ_BEAT_MIN


@cocotb.test()
async def test_constrained_random(dut):
    tb = DedupIngressTB(dut)
    await tb.start()

    rnd = random.Random(0xD3D0)

    feeds = []
    for _ in range(N_FEEDS):
        feeds.append(FeedState())

    seen = []

    for _ in range(CYCLES):
        # Backpressure: mostly ready, occasionally not, independently per feed.
        pattern = []
        for _ in range(N_FEEDS):
            if rnd.random() < 0.15:
                pattern.append(0)
            else:
                pattern.append(1)
        tb.out_ready = pattern

        for f in range(N_FEEDS):
            st = feeds[f]

            if not st.in_packet:
                if rnd.random() < 0.55:
                    st.seq = rnd.randrange(SEQ_POOL)
                    st.seq_beat = rnd.randint(SEQ_BEAT_MIN, SEQ_BEAT_MAX)
                    # Most packets run past their sequence number. A few do
                    # not, which is a malformed frame and must not be dropped.
                    if rnd.random() < 0.9:
                        st.beats = rnd.randint(st.seq_beat + 1,
                                               st.seq_beat + 6)
                    else:
                        st.beats = rnd.randint(1, st.seq_beat)
                    st.beat = 0
                    st.in_packet = True
                    seen.append(st.seq)

            if st.in_packet:
                last = st.beat == st.beats - 1
                if st.beat == st.seq_beat:
                    beat_seq = st.seq
                else:
                    beat_seq = None

                tb.present(
                    f,
                    seq=beat_seq,
                    data=rnd.getrandbits(16),
                    sop=1 if st.beat == 0 else 0,
                    eop=1 if last else 0,
                )

        # Completions, drawn from packets that have actually been offered so
        # the table fills with values the feeds can plausibly repeat.
        if seen and rnd.random() < 0.18:
            tb.complete(rnd.choice(seen[-40:]))

        got = await tb.step()

        # A beat only moves on if it was accepted. A refused beat is presented
        # again next cycle, which is what leaving st.beat alone does.
        for f in range(N_FEEDS):
            st = feeds[f]
            if st.in_packet and got["in_ready"][f]:
                st.beat += 1
                if st.beat == st.beats:
                    st.in_packet = False

    tb.out_ready = [1] * N_FEEDS
    await tb.idle(4)


@cocotb.test()
async def test_constrained_random_heavy_duplicates(dut):
    """Same shape, but a tiny seq pool so almost everything is a duplicate."""
    tb = DedupIngressTB(dut)
    await tb.start()

    rnd = random.Random(0xBEEF)

    feeds = []
    for _ in range(N_FEEDS):
        feeds.append(FeedState())

    pool = 6

    for _ in range(CYCLES // 2):
        pattern = []
        for _ in range(N_FEEDS):
            if rnd.random() < 0.1:
                pattern.append(0)
            else:
                pattern.append(1)
        tb.out_ready = pattern

        for f in range(N_FEEDS):
            st = feeds[f]

            if not st.in_packet:
                if rnd.random() < 0.7:
                    st.seq = rnd.randrange(pool)
                    st.seq_beat = rnd.randint(SEQ_BEAT_MIN, SEQ_BEAT_MAX)
                    st.beats = rnd.randint(st.seq_beat + 1, st.seq_beat + 4)
                    st.beat = 0
                    st.in_packet = True

            if st.in_packet:
                last = st.beat == st.beats - 1
                if st.beat == st.seq_beat:
                    beat_seq = st.seq
                else:
                    beat_seq = None

                tb.present(
                    f,
                    seq=beat_seq,
                    data=rnd.getrandbits(16),
                    sop=1 if st.beat == 0 else 0,
                    eop=1 if last else 0,
                )

        if rnd.random() < 0.3:
            tb.complete(rnd.randrange(pool))

        got = await tb.step()

        for f in range(N_FEEDS):
            st = feeds[f]
            if st.in_packet and got["in_ready"][f]:
                st.beat += 1
                if st.beat == st.beats:
                    st.in_packet = False

    tb.out_ready = [1] * N_FEEDS
    await tb.idle(4)


@cocotb.test()
async def test_constrained_random_heavy_stalls(dut):
    """Downstream closed most of the time, so the register spends its life
    frozen and every beat has to be presented several times over."""
    tb = DedupIngressTB(dut)
    await tb.start()

    rnd = random.Random(0xB105)

    feeds = []
    for _ in range(N_FEEDS):
        feeds.append(FeedState())

    for _ in range(CYCLES // 2):
        pattern = []
        for _ in range(N_FEEDS):
            if rnd.random() < 0.7:
                pattern.append(0)
            else:
                pattern.append(1)
        tb.out_ready = pattern

        for f in range(N_FEEDS):
            st = feeds[f]

            if not st.in_packet:
                if rnd.random() < 0.6:
                    st.seq = rnd.randrange(SEQ_POOL)
                    st.seq_beat = rnd.randint(SEQ_BEAT_MIN, SEQ_BEAT_MAX)
                    st.beats = rnd.randint(st.seq_beat + 1, st.seq_beat + 3)
                    st.beat = 0
                    st.in_packet = True

            if st.in_packet:
                last = st.beat == st.beats - 1
                if st.beat == st.seq_beat:
                    beat_seq = st.seq
                else:
                    beat_seq = None

                tb.present(
                    f,
                    seq=beat_seq,
                    data=rnd.getrandbits(16),
                    sop=1 if st.beat == 0 else 0,
                    eop=1 if last else 0,
                )

        if rnd.random() < 0.2:
            tb.complete(rnd.randrange(SEQ_POOL))

        got = await tb.step()

        for f in range(N_FEEDS):
            st = feeds[f]
            if st.in_packet and got["in_ready"][f]:
                st.beat += 1
                if st.beat == st.beats:
                    st.in_packet = False

    tb.out_ready = [1] * N_FEEDS
    await tb.idle(4)
