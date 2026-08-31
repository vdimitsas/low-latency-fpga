"""Tests 10 and 11: flow control.

10. in_ready is out_ready ORed with the drop, per feed, plus an empty stage
    term. A beat that is being dropped needs no room downstream, so it is
    accepted even while out_ready is low. A beat that is passing still follows
    out_ready. A stage holding nothing always accepts.
11. Feeds are independent. Stalling or dropping one must not disturb another.

Two things changed with the pipeline and both matter here.

The ready equation is now three terms:

    in_ready = ~valid_q | out_ready | drop

The first term means an empty stage accepts regardless of out_ready. So a feed
only follows out_ready once it is actually holding a beat. Every test here
loads a beat first before checking the stall behaviour.

drop is now registered, so it reflects the beat sitting in the pipeline
register, not the beat being presented. A duplicate has to be accepted in one
cycle before its drop shows up on in_ready in the next.
"""

import cocotb

from dedup_ingress_common import LATENCY, DedupIngressTB, N_FEEDS, seq_into_beat


def ready_all():
    """out_ready high on every feed."""
    pattern = []
    for feed in range(N_FEEDS):
        pattern.append(1)
    return pattern


def ready_except(stalled_feed):
    """out_ready high everywhere except one feed."""
    pattern = []
    for feed in range(N_FEEDS):
        if feed == stalled_feed:
            pattern.append(0)
        else:
            pattern.append(1)
    return pattern


def ready_none():
    """out_ready low on every feed."""
    pattern = []
    for feed in range(N_FEEDS):
        pattern.append(0)
    return pattern



@cocotb.test()
async def test_in_ready_follows_out_ready(dut):
    """10. Per feed, and nothing else feeds into it.

    Every feed is kept holding a beat throughout, so the empty stage term is
    never what is driving in_ready. The CPT is empty so nothing is dropped,
    which leaves out_ready as the only live term.
    """
    tb = DedupIngressTB(dut)
    await tb.start()

    # Load every feed so valid_q is set and the stage is no longer empty.
    tb.out_ready = ready_all()
    for feed in range(N_FEEDS):
        tb.present(feed, seq=0x5000 + feed, sop=1)
    await tb.step()

    patterns = [
        [1, 1, 1, 1],
        [0, 1, 1, 1],
        [1, 0, 1, 0],
        [0, 0, 0, 0],
        [1, 1, 0, 0],
    ]

    for pattern in patterns:
        tb.out_ready = list(pattern)
        # Keep every feed offering a beat so no stage drains empty.
        for feed in range(N_FEEDS):
            tb.present(feed, data=0x55, sop=0)
        got = await tb.step()
        assert got["in_ready"] == pattern, (
            f"in_ready {got['in_ready']} did not follow out_ready {pattern}"
        )

    await tb.idle(2)


@cocotb.test()
async def test_drop_lifts_ready_when_downstream_is_full(dut):
    """10b. A dropped copy is accepted whatever out_ready says.

    A beat that is being dropped needs no room downstream, so the block
    releases it even while out_ready is low. This puts the comparator tree on
    the ready path deliberately, see the flow control section of the design
    document.
    """
    tb = DedupIngressTB(dut)
    await tb.start()

    seq = 0xAB
    tb.complete(seq)
    await tb.step()
    await tb.idle(2)

    # Accept a copy that will be dropped.
    tb.out_ready = ready_all()
    tb.present(0, seq=seq, sop=1)
    await tb.step()

    # It is now in the pipeline register. Close downstream completely: the
    # drop alone has to hold in_ready up.
    tb.out_ready = ready_none()
    got = await tb.step()
    assert got["out_valid"][0] == 0, "the duplicate should have been dropped"
    assert got["in_ready"][0] == 1, "a dropped copy was held off by out_ready"

    await tb.idle(2)

    # A passing copy on the same feed still follows out_ready.
    tb.out_ready = ready_all()
    tb.present(0, seq=0xCD, sop=1)
    await tb.step()

    tb.out_ready = ready_none()
    tb.present(0, data=0x99, sop=0)
    got = await tb.step()
    assert got["out_valid"][0] == 1, "a non matching copy should have passed"
    assert got["in_ready"][0] == 0, (
        "in_ready did not follow out_ready on a passing beat"
    )

    await tb.idle(2)


@cocotb.test()
async def test_stalled_feed_does_not_disturb_others(dut):
    """11. Feed 0 held off, feeds 1 to 3 stream normally."""
    tb = DedupIngressTB(dut)
    await tb.start()

    beats = 4
    samples = []

    for beat in range(beats + LATENCY):
        tb.out_ready = ready_except(0)

        # Feed 0 offers a beat every cycle. Its first one is accepted because
        # the stage starts empty, everything after that stalls behind it.
        tb.present(
            0,
            data=seq_into_beat(0xF000) if beat == 0 else 0xAA,
            sop=1 if beat == 0 else 0,
        )

        # Feeds 1 to 3 stream a whole packet each.
        if beat < beats:
            for feed in range(1, N_FEEDS):
                tb.present(
                    feed,
                    data=seq_into_beat(0xF000 + feed) if beat == 0 else (0x30 + beat),
                    sop=1 if beat == 0 else 0,
                    eop=1 if beat == beats - 1 else 0,
                )

        got = await tb.step()
        samples.append(got)

        # Feed 0 accepts its first beat into an empty stage, then holds.
        expected_ready0 = 1 if beat == 0 else 0
        assert got["in_ready"][0] == expected_ready0, (
            f"cycle {beat}: feed 0 in_ready was {got['in_ready'][0]}, "
            f"expected {expected_ready0}"
        )
        for feed in range(1, N_FEEDS):
            assert got["in_ready"][feed] == 1, f"feed {feed} was wrongly held off"

    for beat in range(beats):
        got = samples[beat + LATENCY]
        for feed in range(1, N_FEEDS):
            assert got["out_valid"][feed] == 1, (
                f"feed {feed} beat {beat} was dropped"
            )

    await tb.idle(2)


@cocotb.test()
async def test_drop_on_one_feed_does_not_disturb_others(dut):
    """11b. A drop is scoped to the feed it happens on."""
    tb = DedupIngressTB(dut)
    await tb.start()

    doomed = 0x1A1A
    tb.complete(doomed)
    await tb.step()
    await tb.idle(2)

    tb.present(0, seq=doomed, sop=1)
    for feed in range(1, N_FEEDS):
        tb.present(feed, seq=0x2B00 + feed, sop=1)
    await tb.step()

    got = None
    for _ in range(LATENCY):
        got = await tb.step()

    assert got["out_valid"][0] == 0, "the duplicate on feed 0 survived"
    for feed in range(1, N_FEEDS):
        assert got["out_valid"][feed] == 1, (
            f"feed {feed} was dropped alongside the duplicate on feed 0"
        )

    await tb.idle(2)


@cocotb.test()
async def test_seq_survives_a_stalled_sop(dut):
    """The seq register holds the SOP that is on the bus during a stall.

    The RTL writes seq_regs on in_valid && in_sop, with no in_ready term. A SOP
    held on the bus across a stall is written on every cycle of that stall, and
    every one of those writes stores the same value, so the register holds the
    right seq when the beat is finally accepted.

    This replaces the old test, which asserted the opposite rule. The write
    used to be qualified with in_ready and no longer is. See the sequence
    context section of dedup_ingress.sv for why.

    Every feed is stalled in turn, each holding a different SOP, so a feed
    picking up another feed's sequence number would show up here.
    """
    tb = DedupIngressTB(dut)
    await tb.start()

    for feed in range(N_FEEDS):
        first = 0x3C00 + feed
        second = 0x4D00 + feed

        # Accept a SOP into the empty stage so the feed is no longer empty.
        tb.out_ready = ready_all()
        tb.present(feed, seq=first, sop=1)
        await tb.step()

        # Close this feed downstream and offer the next packet's SOP. It is
        # held on the bus, unaccepted, for several cycles.
        for _ in range(3):
            tb.out_ready = ready_except(feed)
            tb.present(feed, seq=second, sop=1)
            got = await tb.step()
            assert got["in_ready"][feed] == 0, f"feed {feed} should be stalled"

        # Open downstream. The held SOP is accepted now.
        tb.out_ready = ready_all()
        tb.present(feed, seq=second, sop=1)
        await tb.step()

        # Its next beat carries no seq of its own, so it can only be right if
        # seq_regs holds `second` for this feed.
        tb.present(feed, data=0x55, sop=0)
        await tb.step()

        got = None
        for _ in range(LATENCY):
            got = await tb.step()

        assert got["out_seq"][feed] == second, (
            f"feed {feed} lost the stalled SOP: got {got['out_seq'][feed]:#x} "
            f"expected {second:#x}"
        )

        await tb.idle(2)


@cocotb.test()
async def test_seq_context_is_per_feed(dut):
    """Every feed holds its own sequence number at the same time.

    All four feeds are given a different SOP in the same cycle, then all four
    send a mid packet beat. Each one must come out carrying its own seq, which
    fails if seq_regs is shared or indexed wrongly.
    """
    tb = DedupIngressTB(dut)
    await tb.start()

    seqs = []
    for feed in range(N_FEEDS):
        seqs.append(0x9100 + feed * 0x11)

    tb.out_ready = ready_all()
    for feed in range(N_FEEDS):
        tb.present(feed, seq=seqs[feed], sop=1)
    await tb.step()

    # Mid packet beats: no seq on the bus, so each feed can only be right from
    # its own context register.
    for feed in range(N_FEEDS):
        tb.present(feed, data=0x66, sop=0)
    await tb.step()

    got = None
    for _ in range(LATENCY):
        got = await tb.step()

    for feed in range(N_FEEDS):
        assert got["out_seq"][feed] == seqs[feed], (
            f"feed {feed} carried {got['out_seq'][feed]:#x}, "
            f"expected {seqs[feed]:#x}"
        )

    await tb.idle(2)
