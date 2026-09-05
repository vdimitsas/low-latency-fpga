"""Tests 3 to 6: extraction when the field sits inside one beat.

Elaborate with SEQ_OFFSET = 28. The payload starts at byte 28, so the field
occupies bytes 28 to 31, which sit entirely inside beat 3.

3. out_seq_valid is low on every beat except the one that carries the field.
4. It pulses on that beat, and out_seq carries the right value there.
5. out_seq holds its value after the pulse. Nothing downstream has to sample it
   in the same cycle.
6. Back to back packets each get their own pulse and their own value.

step reads the DUT after the clock edge, so the beat driven in a call is
already on the outputs when that call returns.
"""

import cocotb

from seq_extract_lane_common import (
    PULSE_BEAT,
    SPAN,
    SeqExtractLaneTB,
    send_packet,
)


@cocotb.test()
async def test_pulses_once_on_the_field_beat(dut):
    """3 and 4. One pulse, on the right beat, carrying the right value."""
    assert not SPAN, "this suite expects a non spanning SEQ_OFFSET"

    tb = SeqExtractLaneTB(dut)
    await tb.start()

    seq = 0xDEAD_BEEF
    beats = 8
    samples = await send_packet(tb, seq=seq, beats=beats)

    pulses = [i for i, s in enumerate(samples) if s["out_seq_valid"]]
    assert pulses == [PULSE_BEAT], (
        f"expected a single pulse on beat {PULSE_BEAT}, got pulses on {pulses}"
    )

    assert samples[PULSE_BEAT]["out_seq"] == seq, (
        f"wrong sequence number: got {samples[PULSE_BEAT]['out_seq']:#x} "
        f"expected {seq:#x}"
    )


@cocotb.test()
async def test_seq_holds_after_the_pulse(dut):
    """5. The value stays readable for the rest of the packet."""
    tb = SeqExtractLaneTB(dut)
    await tb.start()

    seq = 0x0123_4567
    beats = 10
    samples = await send_packet(tb, seq=seq, beats=beats)

    for i in range(PULSE_BEAT, beats):
        assert samples[i]["out_seq"] == seq, (
            f"out_seq changed on beat {i}: got {samples[i]['out_seq']:#x} "
            f"expected {seq:#x}"
        )


@cocotb.test()
async def test_back_to_back_packets(dut):
    """6. Two packets with no gap, each pulsing once with its own value."""
    tb = SeqExtractLaneTB(dut)
    await tb.start()

    first = 0xAAAA_0001
    second = 0xBBBB_0002
    beats = 7

    samples = await send_packet(tb, seq=first, beats=beats)
    pulses = [i for i, s in enumerate(samples) if s["out_seq_valid"]]
    assert pulses == [PULSE_BEAT], (
        f"first packet: expected one pulse on beat {PULSE_BEAT}, got {pulses}"
    )
    assert samples[PULSE_BEAT]["out_seq"] == first, (
        f"first packet: got {samples[PULSE_BEAT]['out_seq']:#x} "
        f"expected {first:#x}"
    )

    # No idle cycle in between: the next sop follows the previous eop directly.
    samples = await send_packet(tb, seq=second, beats=beats)
    pulses = [i for i, s in enumerate(samples) if s["out_seq_valid"]]
    assert pulses == [PULSE_BEAT], (
        f"second packet: expected one pulse on beat {PULSE_BEAT}, got {pulses}"
    )
    assert samples[PULSE_BEAT]["out_seq"] == second, (
        f"second packet: got {samples[PULSE_BEAT]['out_seq']:#x} "
        f"expected {second:#x}"
    )


@cocotb.test()
async def test_many_packets_in_a_row(dut):
    """6b. A run of packets, each one extracted independently."""
    tb = SeqExtractLaneTB(dut)
    await tb.start()

    for n in range(6):
        seq = 0x1000_0000 + n * 0x0101_0101
        samples = await send_packet(tb, seq=seq, beats=6)

        pulses = [i for i, s in enumerate(samples) if s["out_seq_valid"]]
        assert pulses == [PULSE_BEAT], (
            f"packet {n}: expected one pulse on beat {PULSE_BEAT}, got {pulses}"
        )
        assert samples[PULSE_BEAT]["out_seq"] == seq, (
            f"packet {n}: got {samples[PULSE_BEAT]['out_seq']:#x} "
            f"expected {seq:#x}"
        )

        await tb.idle(2)
