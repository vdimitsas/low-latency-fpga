"""Tests 10 and 11: flow control.

10. in_ready is out_ready ORed with the drop, plus a term for a feed offering
    nothing. A beat that is being dropped needs no room downstream, so it is
    accepted even while out_ready is low. A beat that is passing still follows
    out_ready.
11. Feeds are independent. Stalling or dropping one must not disturb another.

The ready equation is three terms:

    in_ready = ~in_valid | out_ready | drop

The output register takes out_ready as its enable, so while downstream is
closed that feed freezes: the register holds the beat it already has, and the
beat on the bus is refused and has to be presented again.

step reads the DUT after the clock edge, so in_ready is the value for the cycle
that has just started, not the one that decided whether the beat just driven
was accepted.
"""

import cocotb

from dedup_ingress_common import DedupIngressTB, N_FEEDS

# The beats the sequence number is made to arrive on. Fixed values, not random:
# a directed test has to fail the same way twice.
ARRIVAL_BEATS = [3, 4, 7]


def ready_all():
    """out_ready high on every feed."""
    return [1] * N_FEEDS


def ready_except(stalled_feed):
    """out_ready high everywhere except one feed."""
    pattern = []
    for f in range(N_FEEDS):
        if f == stalled_feed:
            pattern.append(0)
        else:
            pattern.append(1)
    return pattern


def ready_none():
    """out_ready low on every feed."""
    return [0] * N_FEEDS


@cocotb.test()
async def test_an_idle_feed_always_accepts(dut):
    """The first term on its own: no beat offered, downstream closed."""
    tb = DedupIngressTB(dut)
    await tb.start()

    tb.out_ready = ready_none()
    got = await tb.step()
    assert got["in_ready"] == ready_all(), (
        f"an idle feed refused a beat: {got['in_ready']}"
    )


@cocotb.test()
async def test_in_ready_follows_out_ready(dut):
    """10. Per feed, and nothing else feeds into it.

    Every feed offers a beat throughout, so the idle term is never what is
    driving in_ready. The CPT is empty so nothing is dropped, which leaves
    out_ready as the only live term.
    """
    tb = DedupIngressTB(dut)
    await tb.start()

    patterns = [
        [1, 1, 1, 1],
        [0, 1, 1, 1],
        [1, 0, 1, 0],
        [0, 0, 0, 0],
        [1, 1, 0, 0],
    ]

    for pattern in patterns:
        tb.out_ready = list(pattern)
        for feed in range(N_FEEDS):
            tb.present(feed, data=0x55)
        got = await tb.step()
        assert got["in_ready"] == pattern, (
            f"in_ready {got['in_ready']} did not follow out_ready {pattern}"
        )


@cocotb.test()
async def test_held_beat_follows_out_ready(dut):
    """10c. A beat is held in the output register, and does not move.

    The register's enable is out_ready, so closing a feed freezes it. The beat
    it is holding stays on the outputs for the whole stall, and the beat being
    offered on the bus is refused.
    """
    tb = DedupIngressTB(dut)
    await tb.start()

    # Load the register while downstream is open.
    tb.out_ready = ready_all()
    tb.present(0, data=0xAB, sop=1)
    got = await tb.step()
    assert got["out_valid"][0] == 1, "the first beat did not appear"
    held_data = got["out_data"][0]

    # Close downstream. The register is frozen on that beat.
    tb.out_ready = ready_none()

    for cycle in range(4):
        tb.present(0, data=0xCD)
        got = await tb.step()

        assert got["in_ready"][0] == 0, (
            f"cycle {cycle}: a closed feed did not refuse the input"
        )
        assert got["out_valid"][0] == 1, (
            f"cycle {cycle}: the held beat vanished"
        )
        assert got["out_data"][0] == held_data, (
            f"cycle {cycle}: the held beat changed: got "
            f"{got['out_data'][0]:#x} expected {held_data:#x}"
        )

    # Open downstream again and the waiting beat moves through.
    tb.out_ready = ready_all()
    tb.present(0, data=0xCD, eop=1)
    got = await tb.step()
    assert got["out_data"][0] == 0xCD, (
        f"the waiting beat was lost: got {got['out_data'][0]:#x} expected 0xcd"
    )


@cocotb.test()
async def test_drop_lifts_ready_when_downstream_is_full(dut):
    """10b. A dropped copy is accepted whatever out_ready says.

    A beat that is being dropped needs no room downstream, so the block takes
    it even while out_ready is low. This puts the comparators on the ready path
    deliberately, see the flow control section of the design document.
    """
    tb = DedupIngressTB(dut)
    await tb.start()

    for n, seq_beat in enumerate(ARRIVAL_BEATS):
        seq = 0xAB00 + n
        tb.complete(seq)
        await tb.step()

        # The beats ahead of the number, downstream open, so they get through.
        tb.out_ready = ready_all()
        for beat in range(seq_beat):
            tb.present(0, data=0x60 + beat, sop=1 if beat == 0 else 0)
            await tb.step()

        # Downstream closed, and the number that arrives is a known duplicate.
        # The drop alone has to hold in_ready up.
        tb.out_ready = ready_none()
        tb.present(0, seq=seq)
        got = await tb.step()
        assert got["in_ready"][0] == 1, (
            f"arrival beat {seq_beat}: a dropped copy was held off by "
            f"out_ready"
        )

        # A number that matches nothing is held off by out_ready as usual.
        tb.out_ready = ready_none()
        tb.present(0, seq=0xCD00 + n, sop=1)
        got = await tb.step()
        assert got["in_ready"][0] == 0, (
            f"arrival beat {seq_beat}: in_ready did not follow out_ready on a "
            f"passing beat"
        )

        tb.out_ready = ready_all()
        await tb.idle(3)


@cocotb.test()
async def test_stalled_feed_does_not_disturb_others(dut):
    """11. Feed 0 held off, feeds 1 to 3 stream normally."""
    tb = DedupIngressTB(dut)
    await tb.start()

    beats = 4
    samples = []

    for beat in range(beats):
        tb.out_ready = ready_except(0)

        # Feed 0 offers a beat every cycle and is refused every cycle.
        tb.present(0, data=0xAA, sop=1 if beat == 0 else 0)

        # Feeds 1 to 3 stream a whole packet each.
        for feed in range(1, N_FEEDS):
            tb.present(
                feed,
                data=0x30 + beat,
                sop=1 if beat == 0 else 0,
                eop=1 if beat == beats - 1 else 0,
            )

        got = await tb.step()
        samples.append(got)

        assert got["in_ready"][0] == 0, (
            f"cycle {beat}: feed 0 should be stalled, got {got['in_ready'][0]}"
        )
        for feed in range(1, N_FEEDS):
            assert got["in_ready"][feed] == 1, (
                f"feed {feed} was wrongly held off"
            )

    for beat in range(beats):
        got = samples[beat]
        for feed in range(1, N_FEEDS):
            assert got["out_valid"][feed] == 1, (
                f"feed {feed} beat {beat} was dropped"
            )


@cocotb.test()
async def test_drop_on_one_feed_does_not_disturb_others(dut):
    """11b. A drop is scoped to the feed it happens on."""
    tb = DedupIngressTB(dut)
    await tb.start()

    for n, seq_beat in enumerate(ARRIVAL_BEATS):
        doomed = 0x1A1A + n
        tb.complete(doomed)
        await tb.step()

        # Lead in on every feed.
        for beat in range(seq_beat):
            for feed in range(N_FEEDS):
                tb.present(feed, data=0x50 + beat, sop=1 if beat == 0 else 0)
            await tb.step()

        # Feed 0 declares the number that has completed, the rest declare
        # numbers of their own.
        tb.present(0, seq=doomed)
        for feed in range(1, N_FEEDS):
            tb.present(feed, seq=0x2B00 + n * 0x10 + feed)
        got = await tb.step()

        assert got["out_valid"][0] == 0, (
            f"arrival beat {seq_beat}: the duplicate on feed 0 survived"
        )
        for feed in range(1, N_FEEDS):
            assert got["out_valid"][feed] == 1, (
                f"arrival beat {seq_beat}: feed {feed} was dropped alongside "
                f"the duplicate on feed 0"
            )

        await tb.idle(3)


@cocotb.test()
async def test_seq_survives_a_stalled_arrival(dut):
    """The seq register holds the number that is on the bus during a stall.

    The RTL writes seq_regs on in_valid && in_seq_valid, with no in_ready term.
    A beat held on the bus across a stall is written on every cycle of that
    stall, and every one of those writes stores the same value, so the register
    holds the right number when the beat is finally accepted.

    Every feed is stalled in turn, each holding a different number, so a feed
    picking up another feed's sequence number would show up here.
    """
    tb = DedupIngressTB(dut)
    await tb.start()

    seq_beat = 3

    for feed in range(N_FEEDS):
        seq = 0x3C00 + feed

        tb.out_ready = ready_all()
        for beat in range(seq_beat):
            tb.present(feed, data=0x80 + beat, sop=1 if beat == 0 else 0)
            await tb.step()

        # Close this feed downstream and offer the beat carrying the number.
        # It is refused for several cycles and re-presented each time.
        for cycle in range(3):
            tb.out_ready = ready_except(feed)
            tb.present(feed, seq=seq, data=0xB7)
            got = await tb.step()
            assert got["in_ready"][feed] == 0, (
                f"feed {feed} cycle {cycle}: should be stalled"
            )

        # Open downstream. The held beat is accepted now.
        tb.out_ready = ready_all()
        tb.present(feed, seq=seq, data=0xB7)
        got = await tb.step()
        assert got["out_seq"][feed] == seq, (
            f"feed {feed} lost the stalled number on its own beat: got "
            f"{got['out_seq'][feed]:#x} expected {seq:#x}"
        )
        assert got["out_data"][feed] == 0xB7, (
            f"feed {feed} lost the beat it had been holding on the bus"
        )

        # Its next beat carries no number of its own, so it can only be right
        # if seq_regs holds `seq` for this feed.
        tb.present(feed, data=0x55)
        got = await tb.step()

        assert got["out_seq_valid"][feed] == 1, (
            f"feed {feed} lost the sequence number across the stall"
        )
        assert got["out_seq"][feed] == seq, (
            f"feed {feed} lost the stalled number: got "
            f"{got['out_seq'][feed]:#x} expected {seq:#x}"
        )

        await tb.idle(3)


@cocotb.test()
async def test_seq_context_is_per_feed(dut):
    """Every feed holds its own sequence number at the same time.

    All four feeds are given a different number on the same beat, then all four
    send a later beat. Each one must come out carrying its own number, which
    fails if seq_regs is shared or indexed wrongly.
    """
    tb = DedupIngressTB(dut)
    await tb.start()

    for n, seq_beat in enumerate(ARRIVAL_BEATS):
        seqs = []
        for feed in range(N_FEEDS):
            seqs.append(0x9100 + n * 0x1000 + feed * 0x11)

        tb.out_ready = ready_all()
        for beat in range(seq_beat):
            for feed in range(N_FEEDS):
                tb.present(feed, data=0x40 + beat, sop=1 if beat == 0 else 0)
            await tb.step()

        for feed in range(N_FEEDS):
            tb.present(feed, seq=seqs[feed])
        await tb.step()

        # A later beat: no number on the bus, so each feed can only be right
        # from its own context register.
        for feed in range(N_FEEDS):
            tb.present(feed, data=0x66)
        got = await tb.step()

        for feed in range(N_FEEDS):
            assert got["out_seq_valid"][feed] == 1, (
                f"arrival beat {seq_beat}: feed {feed} lost its number"
            )
            assert got["out_seq"][feed] == seqs[feed], (
                f"arrival beat {seq_beat}: feed {feed} carried "
                f"{got['out_seq'][feed]:#x}, expected {seqs[feed]:#x}"
            )

        await tb.idle(3)
