"""Tests 1 to 3: the wiring.

seq_extract has no logic of its own. It is N_FEEDS instances of
seq_extract_lane, and the lane is verified against a cycle accurate model in
its own suite. So the only thing that can be wrong here is the indexing: a feed
picking up another feed's beat, sequence number or ready.

1. Four packets in flight at once, each with its own sequence number. Every
   feed's output carries its own.
2. A feed held off does not disturb the others.
3. A feed with no traffic stays quiet while the others run.

step reads the DUT after the clock edge, so the beats driven in a call are
already on the outputs when that call returns.
"""

import cocotb

from seq_extract_common import (
    N_FEEDS,
    PULSE_BEAT,
    SeqExtractTB,
    packet_beats,
)


@cocotb.test()
async def test_each_feed_carries_its_own_packet(dut):
    """1. Four packets at once, nothing crosses between feeds."""
    tb = SeqExtractTB(dut)
    await tb.start()

    beats = 8
    seqs = [0x1111_0000 + feed * 0x0101_0101 for feed in range(N_FEEDS)]
    payloads = [packet_beats(seqs[feed], beats) for feed in range(N_FEEDS)]

    samples = []

    for i in range(beats):
        for feed in range(N_FEEDS):
            tb.present(
                feed,
                data=payloads[feed][i],
                sop=1 if i == 0 else 0,
                eop=1 if i == beats - 1 else 0,
            )
        samples.append(await tb.step())

    for i in range(beats):
        got = samples[i]
        for feed in range(N_FEEDS):
            assert got["out_valid"][feed] == 1, (
                f"feed {feed} beat {i} did not appear on the output"
            )
            assert got["out_data"][feed] == payloads[feed][i], (
                f"feed {feed} beat {i} carried the wrong payload: got "
                f"{got['out_data'][feed]:#x} expected {payloads[feed][i]:#x}"
            )

    for feed in range(N_FEEDS):
        pulses = []
        for i in range(beats):
            if samples[i]["out_seq_valid"][feed]:
                pulses.append(i)

        assert pulses == [PULSE_BEAT], (
            f"feed {feed}: expected one pulse on beat {PULSE_BEAT}, got "
            f"pulses on {pulses}"
        )
        assert samples[PULSE_BEAT]["out_seq"][feed] == seqs[feed], (
            f"feed {feed} got another feed's sequence number: "
            f"{samples[PULSE_BEAT]['out_seq'][feed]:#x} expected "
            f"{seqs[feed]:#x}"
        )


@cocotb.test()
async def test_a_stalled_feed_does_not_disturb_the_others(dut):
    """2. out_ready is per feed, and so is in_ready.

    One experiment per feed. That feed's consumer is closed for the whole run,
    the other three are open, and the other three have to stream their packets
    as if nothing were happening. The stalled feed is left mid packet, which is
    fine: recovery is the lane's business and is tested there.
    """
    tb = SeqExtractTB(dut)

    beats = 8

    for stalled in range(N_FEEDS):
        await tb.start()

        seqs = [0x2222_0000 + stalled * 0x100 + feed
                for feed in range(N_FEEDS)]
        payloads = [packet_beats(seqs[feed], beats)
                    for feed in range(N_FEEDS)]

        tb.out_ready = [0 if feed == stalled else 1
                        for feed in range(N_FEEDS)]

        samples = []

        for i in range(beats):
            for feed in range(N_FEEDS):
                tb.present(
                    feed,
                    data=payloads[feed][i],
                    sop=1 if i == 0 else 0,
                    eop=1 if i == beats - 1 else 0,
                )
            samples.append(await tb.step())

        for i in range(beats):
            got = samples[i]

            for feed in range(N_FEEDS):
                if feed == stalled:
                    # Its empty stage took beat 0, then refused everything
                    # after, so the output sits on beat 0 for the whole run.
                    assert got["out_valid"][feed] == 1, (
                        f"feed {stalled} stalled: its held beat vanished on "
                        f"cycle {i}"
                    )
                    assert got["out_data"][feed] == payloads[feed][0], (
                        f"feed {stalled} stalled: its held beat changed on "
                        f"cycle {i}: got {got['out_data'][feed]:#x} expected "
                        f"{payloads[feed][0]:#x}"
                    )
                    assert got["in_ready"][feed] == 0, (
                        f"feed {stalled} stalled: it accepted a beat on "
                        f"cycle {i} with its consumer closed"
                    )
                    continue

                assert got["in_ready"][feed] == 1, (
                    f"feed {stalled} stalled: feed {feed} was held off on "
                    f"beat {i}"
                )
                assert got["out_valid"][feed] == 1, (
                    f"feed {stalled} stalled: feed {feed} lost beat {i}"
                )
                assert got["out_data"][feed] == payloads[feed][i], (
                    f"feed {stalled} stalled: feed {feed} beat {i} carried "
                    f"{got['out_data'][feed]:#x} expected "
                    f"{payloads[feed][i]:#x}"
                )

        for feed in range(N_FEEDS):
            if feed == stalled:
                continue

            assert samples[PULSE_BEAT]["out_seq_valid"][feed] == 1, (
                f"feed {stalled} stalled: feed {feed} did not pulse"
            )
            assert samples[PULSE_BEAT]["out_seq"][feed] == seqs[feed], (
                f"feed {stalled} stalled: feed {feed} got "
                f"{samples[PULSE_BEAT]['out_seq'][feed]:#x} expected "
                f"{seqs[feed]:#x}"
            )


@cocotb.test()
async def test_an_idle_feed_stays_quiet(dut):
    """3. A feed with no traffic produces no output.

    One experiment per feed. That feed is never driven, the other three stream
    a packet each. The idle feed must hold out_valid and out_seq_valid low
    throughout, so a neighbour's beat cannot leak onto it.
    """
    tb = SeqExtractTB(dut)

    beats = 8

    for idle_feed in range(N_FEEDS):
        await tb.start()

        seqs = [0x3333_0000 + idle_feed * 0x100 + feed
                for feed in range(N_FEEDS)]
        payloads = [packet_beats(seqs[feed], beats)
                    for feed in range(N_FEEDS)]

        samples = []

        for i in range(beats):
            for feed in range(N_FEEDS):
                if feed == idle_feed:
                    continue

                tb.present(
                    feed,
                    data=payloads[feed][i],
                    sop=1 if i == 0 else 0,
                    eop=1 if i == beats - 1 else 0,
                )
            samples.append(await tb.step())

        for i in range(beats):
            got = samples[i]

            assert got["out_valid"][idle_feed] == 0, (
                f"feed {idle_feed} was never driven but produced a beat on "
                f"cycle {i}"
            )
            assert got["out_seq_valid"][idle_feed] == 0, (
                f"feed {idle_feed} was never driven but pulsed on cycle {i}"
            )

            for feed in range(N_FEEDS):
                if feed == idle_feed:
                    continue

                assert got["out_valid"][feed] == 1, (
                    f"feed {idle_feed} idle: feed {feed} lost beat {i}"
                )
                assert got["out_data"][feed] == payloads[feed][i], (
                    f"feed {idle_feed} idle: feed {feed} beat {i} carried "
                    f"{got['out_data'][feed]:#x} expected "
                    f"{payloads[feed][i]:#x}"
                )

        for feed in range(N_FEEDS):
            if feed == idle_feed:
                continue

            assert samples[PULSE_BEAT]["out_seq"][feed] == seqs[feed], (
                f"feed {idle_feed} idle: feed {feed} got "
                f"{samples[PULSE_BEAT]['out_seq'][feed]:#x} expected "
                f"{seqs[feed]:#x}"
            )
