"""Test 6: a completion arriving part way through a packet.

A copy is part way through this block when its twin completes. From that cycle
on its remaining beats are dropped, leaving a partial copy at the output.

Ordering makes this rare in the real pipeline. The arbiter serves one packet at
a time, so a copy and its twin cannot overlap, and the completion for the twin
normally lands before the copy starts. It is tested anyway, because the block
must not depend on that ordering holding.

The beats are driven by hand, one per cycle, so they go back to back with no
gap. dedup_egress has one pipeline register, so the beat driven in cycle i
appears in the sample from cycle i + 1.
"""

import cocotb

from dedup_egress_common import DedupEgressTB


@cocotb.test()
async def test_kill_mid_packet(dut):
    """The packet dies on beat 2 of 5 and never recovers."""
    tb = DedupEgressTB(dut)
    await tb.start()

    seq = 0x6060
    beats = 5
    kill_on = 2
    samples = []

    for i in range(beats):
        tb.present(
            seq=seq,
            data=0xC0 + i,
            sop=1 if i == 0 else 0,
            eop=1 if i == beats - 1 else 0,
        )
        if i == kill_on:
            tb.complete(seq)

        samples.append(await tb.step())

    # one more cycle to push the last beat out of the pipeline register
    samples.append(await tb.step())

    for i in range(beats):
        got = samples[i + 1]
        if i < kill_on:
            assert got["out_valid"] == 1, f"beat {i} dropped before the kill"
        else:
            assert got["out_valid"] == 0, (
                f"beat {i} was forwarded after the packet completed elsewhere"
            )


@cocotb.test()
async def test_next_packet_is_unaffected(dut):
    """After the kill, the stream must carry its next packet normally."""
    tb = DedupEgressTB(dut)
    await tb.start()

    dead = 0x6161
    alive = 0x6262

    for i in range(4):
        tb.present(
            seq=dead,
            data=0xD0 + i,
            sop=1 if i == 0 else 0,
            eop=1 if i == 3 else 0,
        )
        if i == 1:
            tb.complete(dead)
        await tb.step()

    # one more cycle so the last beat reaches the outputs
    await tb.step()

    samples = []
    for i in range(4):
        tb.present(
            seq=alive,
            data=0xE0 + i,
            sop=1 if i == 0 else 0,
            eop=1 if i == 3 else 0,
        )
        samples.append(await tb.step())

    # one more cycle to push the last beat out of the pipeline register
    samples.append(await tb.step())

    for i in range(4):
        got = samples[i + 1]
        assert got["out_valid"] == 1, (
            f"beat {i} of the following packet was dropped"
        )
