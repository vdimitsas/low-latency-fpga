"""Tests 1 and 2: the beat stream itself.

1. Every beat comes out unchanged one cycle later. The lane adds metadata, it
   never touches the payload, the framing or the byte count.
2. A cycle with no beat on the input produces a cycle with no beat on the
   output. The bubble propagates rather than being swallowed or duplicated.

step reads the DUT after the clock edge, so the beat driven in a call is
already on the outputs when that call returns. send_packet returns one sample
per beat, so indexing into its result means "the output for beat i".
"""

import cocotb

from seq_extract_lane_common import (
    BYTE_CNT_MAX,
    SeqExtractLaneTB,
    packet_beats,
    send_packet,
)


@cocotb.test()
async def test_beats_pass_through_unchanged(dut):
    """1. Payload, framing and byte count all survive the register stage."""
    tb = SeqExtractLaneTB(dut)
    await tb.start()

    seq = 0x1234_5678
    beats = 8
    expected = packet_beats(seq, beats)

    samples = await send_packet(tb, seq=seq, beats=beats)

    assert len(samples) == beats, (
        f"expected one sample per beat, got {len(samples)} for {beats} beats"
    )

    for i, got in enumerate(samples):
        assert got["out_valid"] == 1, f"beat {i} did not appear on the output"
        assert got["out_data"] == expected[i], (
            f"beat {i} payload changed: got {got['out_data']:#x} "
            f"expected {expected[i]:#x}"
        )

    assert samples[0]["out_sop"] == 1, "the first beat lost its sop"
    assert samples[-1]["out_eop"] == 1, "the last beat lost its eop"

    for i in range(1, beats):
        assert samples[i]["out_sop"] == 0, f"beat {i} gained a spurious sop"
    for i in range(beats - 1):
        assert samples[i]["out_eop"] == 0, f"beat {i} gained a spurious eop"


@cocotb.test()
async def test_last_beat_byte_count_survives(dut):
    """1b. A partial last beat carries its byte count through unchanged."""
    tb = SeqExtractLaneTB(dut)
    await tb.start()

    partial = 2
    samples = await send_packet(tb, seq=0x0BAD_F00D, beats=6,
                                last_byte_cnt=partial)

    for i, got in enumerate(samples[:-1]):
        assert got["out_byte_cnt"] == BYTE_CNT_MAX, (
            f"beat {i} should be a full beat, got byte_cnt "
            f"{got['out_byte_cnt']}"
        )

    assert samples[-1]["out_byte_cnt"] == partial, (
        f"the last beat lost its byte count: got "
        f"{samples[-1]['out_byte_cnt']} expected {partial}"
    )


@cocotb.test()
async def test_gap_propagates(dut):
    """2. An idle input cycle produces an idle output cycle."""
    tb = SeqExtractLaneTB(dut)
    await tb.start()

    # One beat in, so the stage is holding something.
    tb.present(data=0x11, sop=1)
    got = await tb.step()
    assert got["out_valid"] == 1, "the first beat did not appear"

    # Nothing driven this cycle. present is not called, so the input is idle.
    got = await tb.step()
    assert got["out_valid"] == 0, "a bubble was filled with a repeated beat"

    # And the stage recovers on the next real beat.
    tb.present(data=0x22, eop=1)
    got = await tb.step()
    assert got["out_valid"] == 1, "the beat after a bubble was lost"
    assert got["out_data"] == 0x22, (
        f"wrong beat after the bubble: got {got['out_data']:#x} expected 0x22"
    )


@cocotb.test()
async def test_long_gap(dut):
    """2b. Several idle cycles in a row, then traffic again."""
    tb = SeqExtractLaneTB(dut)
    await tb.start()

    tb.present(data=0x33, sop=1)
    await tb.step()

    for i in range(5):
        got = await tb.step()
        assert got["out_valid"] == 0, f"idle cycle {i} produced a beat"

    tb.present(data=0x44, eop=1)
    got = await tb.step()
    assert got["out_valid"] == 1, "traffic did not resume after a long gap"
