"""Tests 1 and 7: traffic that must not be touched.

1. Empty CPT, no completions ever. Every beat on every feed passes through.
7. Completions present, but no arriving seq matches any of them. Still nothing
   is dropped, which is what separates a working comparator from one that
   matches everything.

The sequence number arrives on beat 3, not at SOP. Beats 0 to 2 leave with
out_seq_valid low and a stale out_seq, and must never be held up or dropped for
want of a sequence number.

step reads the DUT after the clock edge, so the beat driven in a call is on
the outputs when that call returns. send_packet returns one sample per beat, so
indexing into its result means "the output for beat i".
"""

import cocotb

from dedup_ingress_common import DedupIngressTB, N_FEEDS, send_packet

# The beats the sequence number is made to arrive on. 3 and 4 are the two
# offsets this design targets. 7 stands for a venue that places the field
# further into its header. Fixed values, not random: a directed test has to
# fail the same way twice.
ARRIVAL_BEATS = [3, 4, 7]


@cocotb.test()
async def test_empty_cpt_passes_everything(dut):
    """1. Nothing has completed, so nothing may be dropped."""
    tb = DedupIngressTB(dut)
    await tb.start()

    for seq_beat in ARRIVAL_BEATS:
        beats = seq_beat + 2

        for feed in range(N_FEEDS):
            seq = 0x1000 + feed
            samples = await send_packet(tb, feed, seq=seq, beats=beats,
                                        seq_beat=seq_beat)

            for i, s in enumerate(samples):
                assert s["out_valid"][feed] == 1, (
                    f"arrival beat {seq_beat}: feed {feed} beat {i} was "
                    f"dropped with an empty CPT"
                )

            assert samples[0]["out_sop"][feed] == 1
            assert samples[-1]["out_eop"][feed] == 1

            # Nothing ahead of the arrival beat carries a sequence number.
            for i in range(seq_beat):
                assert samples[i]["out_seq_valid"][feed] == 0, (
                    f"arrival beat {seq_beat}: feed {feed} beat {i} claimed a "
                    f"sequence number too early"
                )

            # From it on the number is there and is held to the end.
            for i in range(seq_beat, beats):
                assert samples[i]["out_seq_valid"][feed] == 1, (
                    f"arrival beat {seq_beat}: feed {feed} beat {i} lost its "
                    f"sequence number"
                )
                assert samples[i]["out_seq"][feed] == seq, (
                    f"arrival beat {seq_beat}: feed {feed} beat {i}: got "
                    f"{samples[i]['out_seq'][feed]:#x} expected {seq:#x}"
                )

            await tb.idle(2)


@cocotb.test()
async def test_completions_that_never_match(dut):
    """7. A populated CPT must not drop unrelated sequence numbers."""
    tb = DedupIngressTB(dut)
    await tb.start()

    for seq in (0x10, 0x11, 0x12, 0x13):
        tb.complete(seq)
        await tb.step()

    # Every one of these differs from every table entry. The packets run past
    # the arrival beat so the comparison actually happens.
    for seq_beat in ARRIVAL_BEATS:
        for feed in range(N_FEEDS):
            samples = await send_packet(tb, feed, seq=0x2000 + feed,
                                        beats=seq_beat + 2, seq_beat=seq_beat)
            for i, s in enumerate(samples):
                assert s["out_valid"][feed] == 1, (
                    f"arrival beat {seq_beat}: feed {feed} beat {i} dropped "
                    f"on a non-matching seq"
                )

            await tb.idle(2)


@cocotb.test()
async def test_all_feeds_in_parallel(dut):
    """1b. Four distinct packets in flight at once, none of them dropped."""
    tb = DedupIngressTB(dut)
    await tb.start()

    seqs = []
    for feed in range(N_FEEDS):
        seqs.append(0x3000 + feed)

    for seq_beat in ARRIVAL_BEATS:
        beats = seq_beat + 2
        samples = []

        for beat in range(beats):
            for feed in range(N_FEEDS):
                tb.present(
                    feed,
                    seq=seqs[feed] if beat == seq_beat else None,
                    data=0xB0 + beat,
                    sop=1 if beat == 0 else 0,
                    eop=1 if beat == beats - 1 else 0,
                )
            samples.append(await tb.step())

        for beat in range(beats):
            got = samples[beat]
            assert got["out_valid"] == [1] * N_FEEDS, (
                f"arrival beat {seq_beat}: beat {beat} expected all feeds to "
                f"pass, got {got['out_valid']}"
            )

        # Each feed carries its own number, from the beat it arrived on.
        for beat in range(seq_beat, beats):
            for feed in range(N_FEEDS):
                assert samples[beat]["out_seq"][feed] == seqs[feed], (
                    f"arrival beat {seq_beat}: feed {feed} beat {beat} "
                    f"carried {samples[beat]['out_seq'][feed]:#x}, "
                    f"expected {seqs[feed]:#x}"
                )

        await tb.idle(2)


@cocotb.test()
async def test_beats_ahead_of_the_seq_leave_unmarked(dut):
    """The window before the sequence number arrives.

    Every beat ahead of the sequence number is forwarded without waiting for it
    and without being dropped, and says so: out_seq_valid is low on all of
    them, whatever the table holds. The moment the number lands, a packet whose
    number is already complete dies on that beat.

    The arrival beat is not fixed by the block, it is wherever seq_extract puts
    it, so several are covered.
    """
    tb = DedupIngressTB(dut)
    await tb.start()

    for n, seq_beat in enumerate(ARRIVAL_BEATS):
        doomed = 0x4040 + n

        # The table already holds the number this packet is about to declare.
        tb.complete(doomed)
        await tb.step()

        for beat in range(seq_beat):
            tb.present(0, data=0x70 + beat, sop=1 if beat == 0 else 0)
            got = await tb.step()

            assert got["out_valid"][0] == 1, (
                f"arrival beat {seq_beat}: beat {beat} was dropped before any "
                f"sequence number had arrived"
            )
            assert got["out_seq_valid"][0] == 0, (
                f"arrival beat {seq_beat}: beat {beat} claimed a valid "
                f"sequence number it does not have"
            )
            assert got["in_ready"][0] == 1, (
                f"arrival beat {seq_beat}: beat {beat} was held up waiting "
                f"for a sequence number"
            )

        # The number lands here, and the packet dies on this beat.
        tb.present(0, seq=doomed, eop=1)
        got = await tb.step()

        assert got["out_seq_valid"][0] == 1, (
            f"arrival beat {seq_beat}: the sequence number did not land"
        )
        assert got["out_valid"][0] == 0, (
            f"arrival beat {seq_beat}: the beat carrying a completed sequence "
            f"number was forwarded"
        )

        await tb.idle(2)
