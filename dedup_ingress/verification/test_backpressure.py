"""Tests 10 and 11: flow control.

10. in_ready is out_ready ORed with the drop, per feed, plus an empty stage
    term. A beat that is being dropped needs no room downstream, so it is
    accepted even while out_ready is low. A beat that is passing still follows
    out_ready. A stage holding nothing always accepts.
11. Feeds are independent. Stalling or dropping one must not disturb another.

The ready equation is three terms:

    in_ready = ~valid_q | out_ready | drop

The first term means an empty stage accepts regardless of out_ready. So a feed
only follows out_ready once it is actually holding a beat.

step reads the DUT after the clock edge. in_ready is combinational, so the
value read there is the one for the cycle that has just started, not the one
that decided whether the beat just driven was accepted.
"""

import cocotb

from dedup_ingress_common import DedupIngressTB, N_FEEDS


def ready_all():
    """out_ready high on every feed."""
    return [1] * N_FEEDS


def ready_except(stalled_feed):
    """out_ready high everywhere except one feed."""
    return [0 if f == stalled_feed else 1 for f in range(N_FEEDS)]


def ready_none():
    """out_ready low on every feed."""
    return [0] * N_FEEDS


@cocotb.test()
async def test_empty_stage_always_accepts(dut):
    """The first term on its own: nothing held, downstream closed."""
    tb = DedupIngressTB(dut)
    await tb.start()

    tb.out_ready = ready_none()
    got = await tb.step()
    assert got["in_ready"] == ready_all(), (
        f"an empty stage refused a beat: {got['in_ready']}"
    )


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

    # Downstream closed, and the copy that arrives is a known duplicate. The
    # drop alone has to hold in_ready up.
    tb.out_ready = ready_none()
    tb.present(0, seq=seq, sop=1)
    got = await tb.step()
    assert got["out_valid"][0] == 0, "the duplicate should have been dropped"
    assert got["in_ready"][0] == 1, "a dropped copy was held off by out_ready"

    # A passing copy on the same feed still follows out_ready.
    tb.out_ready = ready_none()
    tb.present(0, seq=0xCD, sop=1)
    got = await tb.step()
    assert got["out_valid"][0] == 1, "a non matching copy should have passed"
    assert got["in_ready"][0] == 0, (
        "in_ready did not follow out_ready on a passing beat"
    )


@cocotb.test()
async def test_stalled_feed_does_not_disturb_others(dut):
    """11. Feed 0 held off, feeds 1 to 3 stream normally."""
    tb = DedupIngressTB(dut)
    await tb.start()

    beats = 4
    samples = []

    for beat in range(beats):
        tb.out_ready = ready_except(0)

        # Feed 0 offers a beat every cycle. Its first one is accepted because
        # the stage starts empty, everything after that stalls behind it.
        tb.present(
            0,
            seq=0xF000 if beat == 0 else None,
            data=None if beat == 0 else 0xAA,
            sop=1 if beat == 0 else 0,
        )

        # Feeds 1 to 3 stream a whole packet each.
        for feed in range(1, N_FEEDS):
            tb.present(
                feed,
                seq=0xF000 + feed if beat == 0 else None,
                data=None if beat == 0 else (0x30 + beat),
                sop=1 if beat == 0 else 0,
                eop=1 if beat == beats - 1 else 0,
            )

        got = await tb.step()
        samples.append(got)

        # Feed 0 is holding a beat with its consumer closed, so it refuses.
        assert got["in_ready"][0] == 0, (
            f"cycle {beat}: feed 0 should be stalled, got {got['in_ready'][0]}"
        )
        for feed in range(1, N_FEEDS):
            assert got["in_ready"][feed] == 1, f"feed {feed} was wrongly held off"

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

    doomed = 0x1A1A
    tb.complete(doomed)
    await tb.step()

    tb.present(0, seq=doomed, sop=1)
    for feed in range(1, N_FEEDS):
        tb.present(feed, seq=0x2B00 + feed, sop=1)
    got = await tb.step()

    assert got["out_valid"][0] == 0, "the duplicate on feed 0 survived"
    for feed in range(1, N_FEEDS):
        assert got["out_valid"][feed] == 1, (
            f"feed {feed} was dropped alongside the duplicate on feed 0"
        )


@cocotb.test()
async def test_seq_survives_a_stalled_sop(dut):
    """The seq register holds the SOP that is on the bus during a stall.

    The RTL writes seq_regs on in_valid && in_sop, with no in_ready term. A SOP
    held on the bus across a stall is written on every cycle of that stall, and
    every one of those writes stores the same value, so the register holds the
    right seq when the beat is finally accepted.

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
        got = await tb.step()

        assert got["out_seq"][feed] == second, (
            f"feed {feed} lost the stalled SOP: got {got['out_seq'][feed]:#x} "
            f"expected {second:#x}"
        )


@cocotb.test()
async def test_seq_context_is_per_feed(dut):
    """Every feed holds its own sequence number at the same time.

    All four feeds are given a different SOP in the same cycle, then all four
    send a mid packet beat. Each one must come out carrying its own seq, which
    fails if seq_regs is shared or indexed wrongly.
    """
    tb = DedupIngressTB(dut)
    await tb.start()

    seqs = [0x9100 + feed * 0x11 for feed in range(N_FEEDS)]

    tb.out_ready = ready_all()
    for feed in range(N_FEEDS):
        tb.present(feed, seq=seqs[feed], sop=1)
    await tb.step()

    # Mid packet beats: no seq on the bus, so each feed can only be right from
    # its own context register.
    for feed in range(N_FEEDS):
        tb.present(feed, data=0x66, sop=0)
    got = await tb.step()

    for feed in range(N_FEEDS):
        assert got["out_seq"][feed] == seqs[feed], (
            f"feed {feed} carried {got['out_seq'][feed]:#x}, "
            f"expected {seqs[feed]:#x}"
        )
