"""Test 12: constrained random against the golden model.

Every cycle the model in dedup_egress_common predicts out_valid, in_ready and
the output beat from the same stimulus the DUT sees, and DedupEgressTB.step
checks them. This test therefore only has to generate traffic worth checking.

The constraints matter more than the volume. Sequence numbers are drawn from a
small pool so that duplicates and completions collide often, and completions
are drawn from packets that have actually been seen, so the CPT fills with
plausible values rather than noise.

This is also the only test that drives back to back beats at full rate under
random backpressure, so the pipeline register and the ready path are exercised
here in ways the directed tests do not reach.
"""

import random

import cocotb

from dedup_egress_common import DedupEgressTB

CYCLES = 3000
SEQ_POOL = 64


class StreamState:
    """Tracks whether the stream is mid packet, so SOP and EOP stay coherent."""

    def __init__(self):
        self.in_packet = False
        self.seq = 0
        self.remaining = 0


@cocotb.test()
async def test_constrained_random(dut):
    tb = DedupEgressTB(dut)
    await tb.start()

    rnd = random.Random(0xD3D0)
    st = StreamState()
    seen = []

    for _ in range(CYCLES):
        # Backpressure: mostly ready, occasionally not.
        if rnd.random() < 0.15:
            tb.out_ready = 0
        else:
            tb.out_ready = 1

        if not st.in_packet:
            if rnd.random() < 0.55:
                st.seq = rnd.randrange(SEQ_POOL)
                st.remaining = rnd.randint(1, 6)
                st.in_packet = True
                seen.append(st.seq)
                tb.present(
                    seq=st.seq,
                    data=rnd.getrandbits(16),
                    sop=1,
                    eop=1 if st.remaining == 1 else 0,
                )
                st.remaining -= 1
                if st.remaining == 0:
                    st.in_packet = False
        else:
            last = st.remaining == 1
            tb.present(
                seq=st.seq,
                data=rnd.getrandbits(16),
                sop=0,
                eop=1 if last else 0,
            )
            st.remaining -= 1
            if last:
                st.in_packet = False

        # Completions, drawn from packets that have actually been offered so
        # the table fills with values the stream can plausibly repeat.
        if seen and rnd.random() < 0.18:
            tb.complete(rnd.choice(seen[-40:]))

        await tb.step()

    # one more cycle so the last beat reaches the outputs
    await tb.step()


@cocotb.test()
async def test_constrained_random_heavy_duplicates(dut):
    """Same shape, but a tiny seq pool so almost everything is a duplicate."""
    tb = DedupEgressTB(dut)
    await tb.start()

    rnd = random.Random(0xBEEF)
    st = StreamState()
    pool = 6

    for _ in range(CYCLES // 2):
        if rnd.random() < 0.1:
            tb.out_ready = 0
        else:
            tb.out_ready = 1

        if not st.in_packet:
            if rnd.random() < 0.7:
                st.seq = rnd.randrange(pool)
                st.remaining = rnd.randint(1, 4)
                st.in_packet = True
                tb.present(
                    seq=st.seq,
                    data=rnd.getrandbits(16),
                    sop=1,
                    eop=1 if st.remaining == 1 else 0,
                )
                st.remaining -= 1
                if st.remaining == 0:
                    st.in_packet = False
        else:
            last = st.remaining == 1
            tb.present(
                seq=st.seq,
                data=rnd.getrandbits(16),
                sop=0,
                eop=1 if last else 0,
            )
            st.remaining -= 1
            if last:
                st.in_packet = False

        if rnd.random() < 0.3:
            tb.complete(rnd.randrange(pool))

        await tb.step()

    # one more cycle so the last beat reaches the outputs
    await tb.step()
