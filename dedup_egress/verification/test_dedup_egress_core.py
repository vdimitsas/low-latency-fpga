"""Tests 2, 3 and 4: the drop decision itself.

2. A completion lands in the table, and a later copy of that packet is dropped.
3. The same cycle bypass: a copy arriving in the very cycle its completion
   arrives, before the table write is visible.
4. Both paths live in the same cycle: a beat matching the table while a
   completion for a different sequence number arrives.

step reads the DUT after the clock edge, so the beat presented in a call is
already on the outputs when that call returns.
"""

import cocotb

from dedup_egress_common import DedupEgressTB, send_packet


@cocotb.test()
async def test_later_copy_is_dropped(dut):
    """2. Completion goes into the table, the next copy is dropped."""
    tb = DedupEgressTB(dut)
    await tb.start()

    seq = 0x4321

    # The copy that gets through.
    samples = await send_packet(tb, seq=seq, beats=3)
    for i, s in enumerate(samples):
        assert s["out_valid"] == 1, f"beat {i} of the first copy was dropped"

    # CHECKSUM confirms it.
    tb.complete(seq)
    await tb.step()

    # A second copy of the same packet is now dead weight.
    samples = await send_packet(tb, seq=seq, beats=3)
    for i, s in enumerate(samples):
        assert s["out_valid"] == 0, f"beat {i} of the duplicate was forwarded"


@cocotb.test()
async def test_same_cycle_bypass(dut):
    """3. Completion and matching copy in the same cycle."""
    tb = DedupEgressTB(dut)
    await tb.start()

    seq = 0x5555

    tb.present(seq=seq, sop=1)
    tb.complete(seq)
    got = await tb.step()
    assert got["out_valid"] == 0, (
        "the bypass did not catch a copy arriving with its own completion"
    )

    # And the entry is in the table from the next cycle on.
    tb.present(seq=seq, sop=1)
    got = await tb.step()
    assert got["out_valid"] == 0, "the completion did not persist into the CPT"


@cocotb.test()
async def test_table_and_bypass_together(dut):
    """4. The table path and the bypass path both live in the same cycle.

    A beat matching a table entry, in the same cycle a completion arrives for a
    different sequence number. The table match must still drop it, and the
    completion must still land.
    """
    tb = DedupEgressTB(dut)
    await tb.start()

    old_seq = 0x0A0A
    new_seq = 0x0B0B

    for seq in (0x0101, old_seq, 0x0202):
        tb.complete(seq)
        await tb.step()

    tb.present(seq=old_seq, sop=1)
    tb.complete(new_seq)
    got = await tb.step()
    assert got["out_valid"] == 0, "table match was not dropped"

    # new_seq went into the table on that same edge.
    tb.present(seq=new_seq, sop=1)
    got = await tb.step()
    assert got["out_valid"] == 0, "the completion was not written to the table"


@cocotb.test()
async def test_unrelated_seq_survives_a_live_completion(dut):
    """A completion must not drop a beat carrying a different seq."""
    tb = DedupEgressTB(dut)
    await tb.start()

    tb.present(seq=0x0C0C, sop=1)
    tb.complete(0x0D0D)
    got = await tb.step()
    assert got["out_valid"] == 1, "a non-matching beat was dropped by the bypass"
