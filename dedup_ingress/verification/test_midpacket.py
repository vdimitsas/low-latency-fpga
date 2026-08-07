"""Test 6: the mid packet kill.

A feed is part way through streaming a packet when that packet completes on
another feed. From that cycle on its remaining beats are dropped, leaving a
partial copy downstream. FEED_BUFFER discards those orphaned beats on the same
completion feedback, which is outside this block.

This is the case that proves the seq register works: the beats after SOP carry
no sequence number of their own, so the only way they can be matched is from
the value latched at SOP.
"""

import cocotb

from dedup_ingress_common import DedupIngressTB, seq_into_beat


@cocotb.test()
async def test_kill_mid_packet(dut):
    """The packet dies on beat 2 of 5 and never recovers."""
    tb = DedupIngressTB(dut)
    await tb.start()

    seq = 0x6060
    beats = 5
    kill_on = 2

    for i in range(beats):
        tb.present(
            0,
            data=seq_into_beat(seq) if i == 0 else (0xC0 + i),
            sop=1 if i == 0 else 0,
            eop=1 if i == beats - 1 else 0,
        )
        if i == kill_on:
            tb.complete(seq)

        got = await tb.step()

        if i < kill_on:
            assert got["out_valid"][0] == 1, f"beat {i} dropped before the kill"
        else:
            assert got["out_valid"][0] == 0, (
                f"beat {i} was forwarded after the packet completed elsewhere"
            )

    await tb.idle(2)


@cocotb.test()
async def test_next_packet_on_that_feed_is_unaffected(dut):
    """After the kill, the feed must carry its next packet normally."""
    tb = DedupIngressTB(dut)
    await tb.start()

    dead = 0x6161
    alive = 0x6262

    for i in range(4):
        tb.present(
            0,
            data=seq_into_beat(dead) if i == 0 else (0xD0 + i),
            sop=1 if i == 0 else 0,
            eop=1 if i == 3 else 0,
        )
        if i == 1:
            tb.complete(dead)
        await tb.step()

    await tb.idle(2)

    for i in range(4):
        tb.present(
            0,
            data=seq_into_beat(alive) if i == 0 else (0xE0 + i),
            sop=1 if i == 0 else 0,
            eop=1 if i == 3 else 0,
        )
        got = await tb.step()
        assert got["out_valid"][0] == 1, (
            f"beat {i} of the following packet was dropped"
        )

    await tb.idle(2)


@cocotb.test()
async def test_kill_on_one_feed_only(dut):
    """Two feeds carry different packets, only the matching one dies."""
    tb = DedupIngressTB(dut)
    await tb.start()

    dying = 0x7070
    living = 0x8080

    for i in range(4):
        tb.present(
            0,
            data=seq_into_beat(dying) if i == 0 else (0x10 + i),
            sop=1 if i == 0 else 0,
            eop=1 if i == 3 else 0,
        )
        tb.present(
            1,
            data=seq_into_beat(living) if i == 0 else (0x20 + i),
            sop=1 if i == 0 else 0,
            eop=1 if i == 3 else 0,
        )
        if i == 1:
            tb.complete(dying)

        got = await tb.step()

        assert got["out_valid"][1] == 1, f"beat {i} of the healthy feed was dropped"
        if i >= 1:
            assert got["out_valid"][0] == 0, f"beat {i} of the dying feed survived"

    await tb.idle(2)
