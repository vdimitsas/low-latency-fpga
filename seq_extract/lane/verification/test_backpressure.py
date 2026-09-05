"""Tests 10 to 14: flow control.

The ready equation has two terms:

    in_ready = !valid_q || out_ready

An empty stage always accepts, since there is nothing held to release. A stage
holding a beat follows out_ready. Nothing is ever dropped here, so there is no
third term.

10. in_ready falls when a held beat cannot be released, and the outputs stay
    stable for the whole stall.
11. An empty stage accepts even with out_ready low.
12. The pulse rides with its beat. A stall on the field beat holds both.
13. A bubble clears the pulse rather than leaving it asserted.
14. The beat counter does not advance during a stall, so the field is not
    captured off the wrong beat.

step reads the DUT after the clock edge, so in_ready is the value for the cycle
that has just started, not the one that decided whether the beat just driven
was accepted.
"""

import cocotb

from seq_extract_lane_common import (
    PULSE_BEAT,
    SeqExtractLaneTB,
    packet_beats,
)


@cocotb.test()
async def test_empty_stage_always_accepts(dut):
    """11. Nothing held, downstream closed, still ready."""
    tb = SeqExtractLaneTB(dut)
    await tb.start()

    tb.out_ready = 0
    got = await tb.step()
    assert got["in_ready"] == 1, "an empty stage refused a beat"


@cocotb.test()
async def test_held_beat_follows_out_ready(dut):
    """10. A beat is held, the outputs do not move, in_ready is low."""
    tb = SeqExtractLaneTB(dut)
    await tb.start()

    # Load the stage while downstream is open.
    tb.out_ready = 1
    tb.present(data=0xAB, sop=1)
    got = await tb.step()
    assert got["out_valid"] == 1, "the first beat did not appear"

    # Close downstream. The beat is stuck on the output.
    held_data = got["out_data"]
    tb.out_ready = 0

    for cycle in range(4):
        tb.present(data=0xCD)
        got = await tb.step()
        assert got["in_ready"] == 0, (
            f"cycle {cycle}: a held beat did not refuse the input"
        )
        assert got["out_valid"] == 1, f"cycle {cycle}: the held beat vanished"
        assert got["out_data"] == held_data, (
            f"cycle {cycle}: the held beat changed: got {got['out_data']:#x} "
            f"expected {held_data:#x}"
        )

    # Open downstream again and the waiting beat moves through.
    tb.out_ready = 1
    tb.present(data=0xCD, eop=1)
    got = await tb.step()
    assert got["out_data"] == 0xCD, (
        f"the waiting beat was lost: got {got['out_data']:#x} expected 0xcd"
    )


@cocotb.test()
async def test_stall_holds_the_pulse_with_its_beat(dut):
    """12 and 14. The pulse and its value are held while downstream is closed.

    Drive up to and including the beat that completes the field, with nothing
    in the way. That beat is then on the output with its pulse. Close
    downstream and watch both stay put.
    """
    tb = SeqExtractLaneTB(dut)
    await tb.start()

    seq = 0x2468_ACE0
    payload = packet_beats(seq, PULSE_BEAT + 1)

    for i in range(PULSE_BEAT + 1):
        tb.present(data=payload[i], sop=1 if i == 0 else 0)
        got = await tb.step()

    assert got["out_seq_valid"] == 1, "the field beat did not pulse"
    assert got["out_seq"] == seq, (
        f"wrong value on the pulse: got {got['out_seq']:#x} "
        f"expected {seq:#x}"
    )

    # Close downstream. The beat cannot leave, so it stays on the output.
    tb.out_ready = 0

    for cycle in range(4):
        got = await tb.step()
        assert got["out_valid"] == 1, (
            f"cycle {cycle}: the held beat vanished"
        )
        assert got["out_seq_valid"] == 1, (
            f"cycle {cycle}: the pulse dropped while its beat was still held"
        )
        assert got["out_seq"] == seq, (
            f"cycle {cycle}: the held value changed: got {got['out_seq']:#x} "
            f"expected {seq:#x}"
        )

    tb.out_ready = 1


@cocotb.test()
async def test_bubble_clears_the_pulse(dut):
    """13. An idle cycle after the field beat drops out_seq_valid."""
    tb = SeqExtractLaneTB(dut)
    await tb.start()

    seq = 0x9999_1111
    payload = packet_beats(seq, PULSE_BEAT + 1)

    # Drive up to and including the field beat, back to back.
    for i in range(PULSE_BEAT + 1):
        tb.present(data=payload[i], sop=1 if i == 0 else 0)
        got = await tb.step()

    assert got["out_seq_valid"] == 1, "the field beat did not pulse"

    # Nothing driven now, so the stage takes a bubble.
    got = await tb.step()
    assert got["out_valid"] == 0, "the bubble did not reach the output"
    assert got["out_seq_valid"] == 0, (
        "out_seq_valid stayed high on a bubble, so it is behaving as a level"
    )
    assert got["out_seq"] == seq, (
        f"out_seq should hold its value through a bubble: got "
        f"{got['out_seq']:#x} expected {seq:#x}"
    )
