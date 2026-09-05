"""Tests 7 to 9: extraction when the field crosses a beat boundary.

Elaborate with SEQ_OFFSET = 30. The field occupies bytes 30 to 33, so two bytes
sit at the top of beat 3 and two at the bottom of beat 4.

7. No pulse on the beat holding the low part. The field is not complete yet.
8. The pulse lands on the following beat, carrying the joined value.
9. A stall between the two halves does not disturb the join. The low part is
   held in its own register across the stall.

step reads the DUT after the clock edge, so the beat driven in a call is
already on the outputs when that call returns.
"""

import cocotb

from seq_extract_lane_common import (
    PULSE_BEAT,
    SEQ_BEAT,
    SPAN,
    SeqExtractLaneTB,
    packet_beats,
    send_packet,
)


@cocotb.test()
async def test_no_pulse_until_the_field_is_complete(dut):
    """7 and 8. The low part alone is not enough, the join is."""
    assert SPAN, "this suite expects a spanning SEQ_OFFSET"

    tb = SeqExtractLaneTB(dut)
    await tb.start()

    seq = 0xCAFE_BABE
    samples = await send_packet(tb, seq=seq, beats=8)

    assert samples[SEQ_BEAT]["out_seq_valid"] == 0, (
        f"beat {SEQ_BEAT} carries only the low part but pulsed anyway"
    )

    pulses = [i for i, s in enumerate(samples) if s["out_seq_valid"]]
    assert pulses == [PULSE_BEAT], (
        f"expected a single pulse on beat {PULSE_BEAT}, got pulses on {pulses}"
    )


@cocotb.test()
async def test_joined_value_is_correct(dut):
    """8b. The halves are put back together in the right order."""
    tb = SeqExtractLaneTB(dut)
    await tb.start()

    # Values whose halves differ, so a swapped join is visible.
    for seq in (0xCAFE_BABE, 0xFFFF_0000, 0x0000_FFFF, 0x8000_0001):
        samples = await send_packet(tb, seq=seq, beats=8)
        got = samples[PULSE_BEAT]["out_seq"]
        assert got == seq, (
            f"join wrong: got {got:#x} expected {seq:#x}"
        )
        await tb.idle(2)


@cocotb.test()
async def test_stall_between_the_halves(dut):
    """9. The stall lands on beat 3, so beat 4 arrives late.

    Beats 3 and 4 hold the two halves of the sequence number. Downstream closes
    while beat 3 is on the output, then opens again. The half from beat 3 has
    to still be there when beat 4 turns up.

    step compares every output against the model on every cycle, stalled cycles
    included, so all this test has to do is place the stall and make sure the
    join happened.
    """
    tb = SeqExtractLaneTB(dut)
    await tb.start()

    seq = 0x1357_9BDF
    beats = 8
    payload = packet_beats(seq, beats)

    pulses = 0
    i = 0
    stalled = 0
    stall_for = 4

    while i < beats:
        # Close downstream for a few cycles once beat 3 is on the output. The
        # count is what ends the stall, not the beat index, since the index
        # cannot move while nothing is being accepted.
        if i == PULSE_BEAT and stalled < stall_for:
            tb.out_ready = 0
            stalled += 1
        else:
            tb.out_ready = 1

        tb.present(
            data=payload[i],
            sop=1 if i == 0 else 0,
            eop=1 if i == beats - 1 else 0,
        )
        got = await tb.step()

        if got["out_seq_valid"]:
            pulses += 1

        if got["in_ready"]:
            i += 1

    tb.out_ready = 1

    assert pulses == 1, f"expected exactly one pulse, got {pulses}"


@cocotb.test()
async def test_back_to_back_packets(dut):
    """8c. Consecutive packets, each joined independently."""
    tb = SeqExtractLaneTB(dut)
    await tb.start()

    for n in range(5):
        seq = 0x2000_0000 + n * 0x1111_1111
        samples = await send_packet(tb, seq=seq, beats=7)

        pulses = [i for i, s in enumerate(samples) if s["out_seq_valid"]]
        assert pulses == [PULSE_BEAT], (
            f"packet {n}: expected one pulse on beat {PULSE_BEAT}, got {pulses}"
        )
        assert samples[PULSE_BEAT]["out_seq"] == seq, (
            f"packet {n}: got {samples[PULSE_BEAT]['out_seq']:#x} "
            f"expected {seq:#x}"
        )
