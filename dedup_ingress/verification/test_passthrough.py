"""Tests 1 and 7: traffic that must not be touched.

1. Empty CPT, no completions ever. Every beat on every feed passes through.
7. Completions present, but no arriving seq matches any of them. Still nothing
   is dropped, which is what separates a working comparator from one that
   matches everything.

step reads the DUT after the clock edge, so the beat driven in a call is
already on the outputs when that call returns. send_packet returns beat aligned
samples, so indexing into its result means "the output for beat i".
"""

import cocotb

from dedup_ingress_common import DedupIngressTB, N_FEEDS, send_packet


@cocotb.test()
async def test_empty_cpt_passes_everything(dut):
    """1. Nothing has completed, so nothing may be dropped."""
    tb = DedupIngressTB(dut)
    await tb.start()

    for feed in range(N_FEEDS):
        seq = 0x1000 + feed
        samples = await send_packet(tb, feed, seq=seq, beats=4)

        for i, s in enumerate(samples):
            assert s["out_valid"][feed] == 1, (
                f"feed {feed} beat {i} was dropped with an empty CPT"
            )

        assert samples[0]["out_seq"][feed] == seq
        assert samples[0]["out_sop"][feed] == 1
        assert samples[-1]["out_eop"][feed] == 1


@cocotb.test()
async def test_completions_that_never_match(dut):
    """7. A populated CPT must not drop unrelated sequence numbers."""
    tb = DedupIngressTB(dut)
    await tb.start()

    for seq in (0x10, 0x11, 0x12, 0x13):
        tb.complete(seq)
        await tb.step()

    # Every one of these differs from every table entry.
    for feed in range(N_FEEDS):
        samples = await send_packet(tb, feed, seq=0x2000 + feed, beats=3)
        for i, s in enumerate(samples):
            assert s["out_valid"][feed] == 1, (
                f"feed {feed} beat {i} dropped on a non-matching seq"
            )


@cocotb.test()
async def test_all_feeds_in_parallel(dut):
    """1b. Four distinct packets in flight at once, none of them dropped."""
    tb = DedupIngressTB(dut)
    await tb.start()

    beats = 4
    samples = []

    for beat in range(beats):
        for feed in range(N_FEEDS):
            tb.present(
                feed,
                seq=0x3000 + feed if beat == 0 else None,
                data=None if beat == 0 else (0xB0 + beat),
                sop=1 if beat == 0 else 0,
                eop=1 if beat == beats - 1 else 0,
            )
        samples.append(await tb.step())

    for beat in range(beats):
        got = samples[beat]
        assert got["out_valid"] == [1] * N_FEEDS, (
            f"beat {beat}: expected all feeds to pass, got {got['out_valid']}"
        )
