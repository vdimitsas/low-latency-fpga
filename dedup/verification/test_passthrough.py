"""Tests 1 and 7: traffic that must not be touched.

1. Empty CPT, no completions ever. Every beat on every feed passes through.
7. Completions present, but no arriving seq matches any of them. Still nothing
   is dropped, which is what separates a working comparator from one that
   matches everything.
"""

import cocotb

from dedup_common import DedupTB, N_FEEDS, send_packet


@cocotb.test()
async def test_empty_cpt_passes_everything(dut):
    """1. Nothing has completed, so nothing may be dropped."""
    tb = DedupTB(dut)
    await tb.start()

    for feed in range(N_FEEDS):
        samples = await send_packet(tb, feed, seq=0x1000 + feed, beats=4)
        for i, s in enumerate(samples):
            assert s["out_valid"][feed] == 1, (
                f"feed {feed} beat {i} was dropped with an empty CPT"
            )
        assert samples[0]["out_seq"][feed] == 0x1000 + feed
        assert samples[0]["out_sop"][feed] == 1
        assert samples[-1]["out_eop"][feed] == 1

    await tb.idle(4)


@cocotb.test()
async def test_completions_that_never_match(dut):
    """7. A populated CPT must not drop unrelated sequence numbers."""
    tb = DedupTB(dut)
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

    await tb.idle(4)


@cocotb.test()
async def test_all_feeds_in_parallel(dut):
    """1b. Four distinct packets in flight at once, none of them dropped."""
    tb = DedupTB(dut)
    await tb.start()

    for beat in range(4):
        for feed in range(N_FEEDS):
            tb.present(
                feed,
                seq=0x3000 + feed if beat == 0 else None,
                data=None if beat == 0 else (0xB0 + beat),
                sop=1 if beat == 0 else 0,
                eop=1 if beat == 3 else 0,
            )
        got = await tb.step()
        assert got["out_valid"] == [1] * N_FEEDS, (
            f"beat {beat}: expected all feeds to pass, got {got['out_valid']}"
        )

    await tb.idle(4)
