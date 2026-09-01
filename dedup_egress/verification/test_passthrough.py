"""Tests 1 and 7: traffic that must not be touched.

1. Empty CPT, no completions ever. Every beat passes through.
7. Completions present, but no arriving seq matches any of them. Still nothing
   is dropped, which is what separates working comparison logic from logic that
   matches everything.

dedup_egress has one cycle of latency, so a beat presented in one cycle appears
on the outputs in the next. send_packet returns beat aligned samples, so
indexing into its result still means "the output for beat i".

send_packet leaves a gap between beats. Back to back traffic is covered by the
constrained random test.
"""

import cocotb

from dedup_egress_common import DedupEgressTB, send_packet


@cocotb.test()
async def test_empty_cpt_passes_everything(dut):
    """1. Nothing has completed, so nothing may be dropped."""
    tb = DedupEgressTB(dut)
    await tb.start()

    seq = 0x1000
    samples = await send_packet(tb, seq=seq, beats=4)

    for i, s in enumerate(samples):
        assert s["out_valid"] == 1, f"beat {i} was dropped with an empty CPT"
        assert s["out_seq"] == seq, (
            f"beat {i} carried {s['out_seq']:#x}, expected {seq:#x}"
        )

    assert samples[0]["out_sop"] == 1, "the first beat lost its SOP"
    assert samples[-1]["out_eop"] == 1, "the last beat lost its EOP"


@cocotb.test()
async def test_completions_that_never_match(dut):
    """7. A populated CPT must not drop unrelated sequence numbers."""
    tb = DedupEgressTB(dut)
    await tb.start()

    for seq in (0x10, 0x11, 0x12, 0x13):
        tb.complete(seq)
        await tb.step()

    # Every one of these differs from every table entry.
    for seq in (0x2000, 0x2001, 0x2002):
        samples = await send_packet(tb, seq=seq, beats=3)
        for i, s in enumerate(samples):
            assert s["out_valid"] == 1, (
                f"beat {i} of seq {seq:#x} dropped on a non-matching seq"
            )
