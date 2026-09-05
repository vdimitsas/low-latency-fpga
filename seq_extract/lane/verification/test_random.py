"""Test 16: constrained random against the golden model.

Every cycle the model in seq_extract_lane_common predicts in_ready, the output
beat and the extracted metadata from the same stimulus the DUT sees, and
SeqExtractLaneTB.step compares all of it. That comparison happens on stalled
cycles and idle cycles too, not only on accepted beats, so this test does not
need to score anything itself. It only has to generate traffic worth checking.

SEQ_OFFSET is fixed at elaboration, so a single run cannot vary it. The sweep
over offsets happens in the Makefile, one elaboration per offset, some of them
spanning and some not.

The constraints matter more than the volume:

  packet lengths reach past the field beat most of the time, but not always, so
  a packet that ends before the field is covered
  gaps between packets are common, so bubbles land in every position
  out_ready drops often, so stalls land on the field beat and its neighbours
  the last beat carries a random byte count
"""

import random

import cocotb

from seq_extract_lane_common import (
    BYTE_CNT_MAX,
    PULSE_BEAT,
    SEQ_MASK,
    SeqExtractLaneTB,
    packet_beats,
)

CYCLES = 4000


async def stream_packet(tb, rnd, seq, beats, stall_rate):
    """Drive one packet, re-presenting a beat that was refused.

    in_ready is read after the edge, so it is the value for the coming cycle.
    It is held and used on the next pass to decide whether the beat that was
    just driven was taken.
    """
    payload = packet_beats(seq, beats)
    in_ready = 1
    cycles = 0
    i = 0

    while i < beats:
        tb.out_ready = 0 if rnd.random() < stall_rate else 1

        last = i == beats - 1
        tb.present(
            data=payload[i],
            sop=1 if i == 0 else 0,
            eop=1 if last else 0,
            byte_cnt=rnd.randint(0, BYTE_CNT_MAX) if last else None,
        )
        got = await tb.step()
        cycles += 1

        if in_ready:
            i += 1

        in_ready = got["in_ready"]

    return cycles


@cocotb.test()
async def test_constrained_random(dut):
    """Mixed traffic: gaps, stalls, long and short packets."""
    tb = SeqExtractLaneTB(dut)
    await tb.start()

    rnd = random.Random(0x5EED)
    cycles = 0

    while cycles < CYCLES:
        seq = rnd.getrandbits(32) & SEQ_MASK

        # Most packets are long enough to carry the whole field. A few are not.
        if rnd.random() < 0.85:
            beats = rnd.randint(PULSE_BEAT + 1, PULSE_BEAT + 12)
        else:
            beats = rnd.randint(1, PULSE_BEAT)

        cycles += await stream_packet(tb, rnd, seq, beats, stall_rate=0.25)

        gap = rnd.randint(0, 4)
        tb.out_ready = 1
        await tb.idle(gap)
        cycles += gap


@cocotb.test()
async def test_constrained_random_heavy_stalls(dut):
    """Same shape, with downstream closed most of the time."""
    tb = SeqExtractLaneTB(dut)
    await tb.start()

    rnd = random.Random(0xB105)
    cycles = 0

    while cycles < CYCLES // 2:
        seq = rnd.getrandbits(32) & SEQ_MASK
        beats = rnd.randint(PULSE_BEAT + 1, PULSE_BEAT + 6)

        cycles += await stream_packet(tb, rnd, seq, beats, stall_rate=0.7)

        tb.out_ready = 1
        await tb.idle(2)
        cycles += 2


@cocotb.test()
async def test_constrained_random_no_gaps(dut):
    """Packets back to back, so a sop always follows an eop directly."""
    tb = SeqExtractLaneTB(dut)
    await tb.start()

    rnd = random.Random(0x0FF1)
    cycles = 0

    while cycles < CYCLES // 2:
        seq = rnd.getrandbits(32) & SEQ_MASK
        beats = rnd.randint(PULSE_BEAT + 1, PULSE_BEAT + 8)

        cycles += await stream_packet(tb, rnd, seq, beats, stall_rate=0.15)

    tb.out_ready = 1
    await tb.idle(4)
