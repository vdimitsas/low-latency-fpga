"""Test 6: the mid packet kill.

A feed is part way through streaming a packet when that packet completes on
another feed. From that cycle on its remaining beats are dropped, leaving a
partial copy downstream. Those orphaned beats are not recalled: they are served
like any others and dropped at DEDUP_EGRESS, which is outside this block.

There are two ways a packet dies part way through. The completion can arrive
while the packet is already running, which is what these tests drive. Or the
number can already be in the table when the packet starts, in which case the
kill lands on the beat the number arrives on, which test_dedup_ingress_core
covers.

Either way the beats after the kill carry no sequence number of their own, so
the only thing that can keep matching them is the value held in seq_regs.

The beats are driven by hand, one per cycle, so they go back to back. step
reads the DUT after the clock edge, so entry i of samples is the output for
beat i.
"""

import cocotb

from dedup_ingress_common import DedupIngressTB

# The beats the sequence number is made to arrive on. Fixed values, not random:
# a directed test has to fail the same way twice.
ARRIVAL_BEATS = [3, 4, 7]


@cocotb.test()
async def test_kill_mid_packet(dut):
    """The completion lands two beats after the number, and the packet dies."""
    tb = DedupIngressTB(dut)
    await tb.start()

    for n, seq_beat in enumerate(ARRIVAL_BEATS):
        seq = 0x6060 + n
        beats = seq_beat + 5
        kill_on = seq_beat + 2
        samples = []

        for i in range(beats):
            tb.present(
                0,
                seq=seq if i == seq_beat else None,
                data=0xC0 + i,
                sop=1 if i == 0 else 0,
                eop=1 if i == beats - 1 else 0,
            )
            if i == kill_on:
                tb.complete(seq)

            samples.append(await tb.step())

        for i in range(beats):
            got = samples[i]
            if i < kill_on:
                assert got["out_valid"][0] == 1, (
                    f"arrival beat {seq_beat}: beat {i} was dropped before "
                    f"the completion arrived"
                )
            else:
                assert got["out_valid"][0] == 0, (
                    f"arrival beat {seq_beat}: beat {i} was forwarded after "
                    f"the packet completed elsewhere"
                )

        await tb.idle(2)


@cocotb.test()
async def test_next_packet_on_that_feed_is_unaffected(dut):
    """After the kill, the feed must carry its next packet normally."""
    tb = DedupIngressTB(dut)
    await tb.start()

    for n, seq_beat in enumerate(ARRIVAL_BEATS):
        dead = 0x6161 + n
        alive = 0x6262 + n
        beats = seq_beat + 3
        kill_on = seq_beat + 1

        for i in range(beats):
            tb.present(
                0,
                seq=dead if i == seq_beat else None,
                data=0xD0 + i,
                sop=1 if i == 0 else 0,
                eop=1 if i == beats - 1 else 0,
            )
            if i == kill_on:
                tb.complete(dead)
            await tb.step()

        samples = []
        for i in range(beats):
            tb.present(
                0,
                seq=alive if i == seq_beat else None,
                data=0xE0 + i,
                sop=1 if i == 0 else 0,
                eop=1 if i == beats - 1 else 0,
            )
            samples.append(await tb.step())

        for i in range(beats):
            assert samples[i]["out_valid"][0] == 1, (
                f"arrival beat {seq_beat}: beat {i} of the following packet "
                f"was dropped"
            )

        await tb.idle(2)


@cocotb.test()
async def test_kill_on_one_feed_only(dut):
    """Two feeds carry different packets, only the matching one dies."""
    tb = DedupIngressTB(dut)
    await tb.start()

    for n, seq_beat in enumerate(ARRIVAL_BEATS):
        dying = 0x7070 + n
        living = 0x8080 + n
        beats = seq_beat + 3
        kill_on = seq_beat + 1
        samples = []

        for i in range(beats):
            tb.present(
                0,
                seq=dying if i == seq_beat else None,
                data=0x10 + i,
                sop=1 if i == 0 else 0,
                eop=1 if i == beats - 1 else 0,
            )
            tb.present(
                1,
                seq=living if i == seq_beat else None,
                data=0x20 + i,
                sop=1 if i == 0 else 0,
                eop=1 if i == beats - 1 else 0,
            )
            if i == kill_on:
                tb.complete(dying)

            samples.append(await tb.step())

        for i in range(beats):
            got = samples[i]
            assert got["out_valid"][1] == 1, (
                f"arrival beat {seq_beat}: beat {i} of the healthy feed was "
                f"dropped"
            )
            if i >= kill_on:
                assert got["out_valid"][0] == 0, (
                    f"arrival beat {seq_beat}: beat {i} of the dying feed "
                    f"survived"
                )

        await tb.idle(2)


@cocotb.test()
async def test_kill_lands_on_the_completion_cycle(dut):
    """The kill is not delayed: it takes effect on the completion's own cycle.

    The bypass path exists for exactly this. The table is written at the clock
    edge, so on the cycle the completion arrives the entry is not there yet.
    Without the bypass this beat would slip through and the kill would start
    one beat late.
    """
    tb = DedupIngressTB(dut)
    await tb.start()

    for n, seq_beat in enumerate(ARRIVAL_BEATS):
        seq = 0x9090 + n
        kill_on = seq_beat + 1

        for i in range(kill_on):
            tb.present(
                0,
                seq=seq if i == seq_beat else None,
                data=0xF0 + i,
                sop=1 if i == 0 else 0,
            )
            got = await tb.step()
            assert got["out_valid"][0] == 1, (
                f"arrival beat {seq_beat}: beat {i} died before the "
                f"completion arrived"
            )

        # The completion arrives now. This beat must already be dead.
        tb.present(0, data=0xFF)
        tb.complete(seq)
        got = await tb.step()
        assert got["out_valid"][0] == 0, (
            f"arrival beat {seq_beat}: the beat sharing a cycle with the "
            f"completion was forwarded"
        )

        await tb.idle(2)
