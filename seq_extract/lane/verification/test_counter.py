"""Test 15: the beat counter saturates.

Real market data packets run to hundreds of bytes, so a packet is always far
longer than the beat holding the sequence number. The counter therefore spends
most of a packet sitting at its maximum.

It saturates rather than wrapping. If it wrapped, a long enough packet would
roll it back round to the value that marks the field beat, and the lane would
pulse a second time on a beat carrying ordinary payload.

step reads the DUT after the clock edge, so the beat driven in a call is
already on the outputs when that call returns.
"""

import cocotb

from seq_extract_lane_common import (
    PULSE_BEAT,
    SeqExtractLaneTB,
    send_packet,
)


@cocotb.test()
async def test_long_packet_pulses_once(dut):
    """15. Sixty four beats, one pulse."""
    tb = SeqExtractLaneTB(dut)
    await tb.start()

    seq = 0x5A5A_A5A5
    samples = await send_packet(tb, seq=seq, beats=64)

    pulses = [i for i, s in enumerate(samples) if s["out_seq_valid"]]
    assert pulses == [PULSE_BEAT], (
        f"expected a single pulse on beat {PULSE_BEAT}, got pulses on {pulses}"
    )

    assert samples[PULSE_BEAT]["out_seq"] == seq, (
        f"wrong sequence number: got {samples[PULSE_BEAT]['out_seq']:#x} "
        f"expected {seq:#x}"
    )


@cocotb.test()
async def test_value_survives_a_long_tail(dut):
    """15b. out_seq is still readable at the end of a long packet."""
    tb = SeqExtractLaneTB(dut)
    await tb.start()

    seq = 0x0F0F_F0F0
    beats = 48
    samples = await send_packet(tb, seq=seq, beats=beats)

    assert samples[-1]["out_seq"] == seq, (
        f"out_seq was lost before the end of the packet: got "
        f"{samples[-1]['out_seq']:#x} expected {seq:#x}"
    )


@cocotb.test()
async def test_long_packets_back_to_back(dut):
    """15c. The counter restarts on sop even after saturating."""
    tb = SeqExtractLaneTB(dut)
    await tb.start()

    for n in range(3):
        seq = 0x3000_0000 + n
        samples = await send_packet(tb, seq=seq, beats=40)

        pulses = [i for i, s in enumerate(samples) if s["out_seq_valid"]]
        assert pulses == [PULSE_BEAT], (
            f"packet {n}: expected one pulse on beat {PULSE_BEAT}, "
            f"got pulses on {pulses}"
        )
        assert samples[PULSE_BEAT]["out_seq"] == seq, (
            f"packet {n}: got {samples[PULSE_BEAT]['out_seq']:#x} "
            f"expected {seq:#x}"
        )
