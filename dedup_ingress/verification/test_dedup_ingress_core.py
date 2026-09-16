"""Tests 2, 3 and 4: the drop decision itself.

2. A completion lands in the table, and a later copy of that packet on another
   feed is dropped.
3. The same cycle bypass: a copy arriving in the very cycle its completion
   arrives, before the table write is visible.
4. Both paths exercised together: a populated table and a live completion, with
   one feed matching each, in the same cycle.

The beat carrying the sequence number is not fixed by this block. It is
wherever seq_extract puts it, which depends on the venue's header layout, so
every test here runs over several values of it.

The drop cannot begin before that beat, so a duplicate always gets its leading
beats out of the door. That is the cost of extracting the number downstream of
SOP and is checked here, not glossed over: the tests assert the head passes and
the tail dies.

step reads the DUT after the clock edge, so the beat driven in a call is on
the outputs when that call returns.
"""

import cocotb

from dedup_ingress_common import DedupIngressTB, N_FEEDS, send_packet

# The beats every test here runs over. 3 and 4 are the two offsets this design
# targets. 7 stands for a venue that places the field further into its header.
# Fixed values, not random: a directed test has to fail the same way twice.
ARRIVAL_BEATS = [3, 4, 7]


async def lead_in(tb, feeds, beats):
    """Drive the beats ahead of the sequence number on one or more feeds.

    Returns one sample per beat. None of these beats can be dropped: no
    sequence number has arrived, so there is nothing to compare.
    """
    samples = []
    for beat in range(beats):
        for feed in feeds:
            tb.present(feed, data=0x90 + beat, sop=1 if beat == 0 else 0)
        samples.append(await tb.step())
    return samples


@cocotb.test()
async def test_later_copy_is_dropped(dut):
    """2. Completion goes into the table, the next copy is dropped."""
    tb = DedupIngressTB(dut)
    await tb.start()

    for n, seq_beat in enumerate(ARRIVAL_BEATS):
        seq = 0x4321 + n
        beats = seq_beat + 3

        # Feed 0 carries the copy that gets through.
        samples = await send_packet(tb, 0, seq=seq, beats=beats,
                                    seq_beat=seq_beat)
        for i, s in enumerate(samples):
            assert s["out_valid"][0] == 1, (
                f"arrival beat {seq_beat}: beat {i} of the first copy was "
                f"dropped"
            )

        # CHECKSUM confirms it.
        tb.complete(seq)
        await tb.step()

        # Feed 1's copy is now dead weight, but only from the beat its
        # sequence number arrives on.
        samples = await send_packet(tb, 1, seq=seq, beats=beats,
                                    seq_beat=seq_beat)

        for i in range(seq_beat):
            assert samples[i]["out_valid"][1] == 1, (
                f"arrival beat {seq_beat}: beat {i} was dropped before the "
                f"sequence number had arrived"
            )
        for i in range(seq_beat, beats):
            assert samples[i]["out_valid"][1] == 0, (
                f"arrival beat {seq_beat}: beat {i} of the duplicate was "
                f"forwarded"
            )

        await tb.idle(2)


@cocotb.test()
async def test_same_cycle_bypass(dut):
    """3. Completion and matching sequence number in the same cycle."""
    tb = DedupIngressTB(dut)
    await tb.start()

    for n, seq_beat in enumerate(ARRIVAL_BEATS):
        seq = 0x5555 + n

        await lead_in(tb, [1], seq_beat)

        # The number arrives in the very cycle its completion does. The table
        # write has not happened yet, so only the bypass can catch this.
        tb.present(1, seq=seq)
        tb.complete(seq)
        got = await tb.step()
        assert got["out_valid"][1] == 0, (
            f"arrival beat {seq_beat}: the bypass did not catch a copy "
            f"arriving with its own completion"
        )

        await tb.idle(2)

        # And the entry is in the table from the next cycle on.
        await lead_in(tb, [2], seq_beat)
        tb.present(2, seq=seq)
        got = await tb.step()
        assert got["out_valid"][2] == 0, (
            f"arrival beat {seq_beat}: the completion did not persist into "
            f"the CPT"
        )

        await tb.idle(2)


@cocotb.test()
async def test_table_and_bypass_together(dut):
    """4. One feed matches the table, another matches the live completion."""
    tb = DedupIngressTB(dut)
    await tb.start()

    for n, seq_beat in enumerate(ARRIVAL_BEATS):
        old_seq = 0x0A0A + n
        new_seq = 0x0B0B + n
        other_seq = 0x0C0C + n

        # Populate the table with a few entries, old_seq among them.
        for seq in (0x0101 + n, old_seq, 0x0202 + n):
            tb.complete(seq)
            await tb.step()

        await lead_in(tb, [0, 1, 2], seq_beat)

        # Feed 0 matches a table entry.
        # Feed 1 matches the completion arriving this very cycle.
        # Feed 2 matches nothing and must survive.
        tb.present(0, seq=old_seq)
        tb.present(1, seq=new_seq)
        tb.present(2, seq=other_seq)
        tb.complete(new_seq)
        got = await tb.step()

        assert got["out_valid"][0] == 0, (
            f"arrival beat {seq_beat}: table match on feed 0 was not dropped"
        )
        assert got["out_valid"][1] == 0, (
            f"arrival beat {seq_beat}: bypass match on feed 1 was not dropped"
        )
        assert got["out_valid"][2] == 1, (
            f"arrival beat {seq_beat}: feed 2 matched nothing but was dropped"
        )

        await tb.idle(2)


@cocotb.test()
async def test_duplicate_on_every_feed(dut):
    """2b. Once completed, the copy is dropped on all feeds at once."""
    tb = DedupIngressTB(dut)
    await tb.start()

    feeds = list(range(N_FEEDS))

    for n, seq_beat in enumerate(ARRIVAL_BEATS):
        seq = 0x7777 + n
        tb.complete(seq)
        await tb.step()

        samples = await lead_in(tb, feeds, seq_beat)

        # Nothing can be dropped yet.
        for i, s in enumerate(samples):
            assert s["out_valid"] == [1] * N_FEEDS, (
                f"arrival beat {seq_beat}: beat {i} expected every feed to "
                f"pass, got {s['out_valid']}"
            )

        for feed in feeds:
            tb.present(feed, seq=seq)
        got = await tb.step()

        assert got["out_valid"] == [0] * N_FEEDS, (
            f"arrival beat {seq_beat}: expected every feed dropped, got "
            f"{got['out_valid']}"
        )

        await tb.idle(2)


@cocotb.test()
async def test_drop_persists_to_the_end_of_the_packet(dut):
    """2c. Once the number has matched, every later beat dies too.

    The sequence number is held in seq_regs, so the beats after the one that
    carried it have to keep matching without it being on the bus again.
    """
    tb = DedupIngressTB(dut)
    await tb.start()

    for n, seq_beat in enumerate(ARRIVAL_BEATS):
        seq = 0x8888 + n
        beats = seq_beat + 6

        tb.complete(seq)
        await tb.step()

        samples = await send_packet(tb, 3, seq=seq, beats=beats,
                                    seq_beat=seq_beat)

        for i in range(seq_beat, beats):
            assert samples[i]["out_valid"][3] == 0, (
                f"arrival beat {seq_beat}: beat {i} survived although the "
                f"packet had already been killed"
            )
            assert samples[i]["out_seq_valid"][3] == 1, (
                f"arrival beat {seq_beat}: beat {i} lost the sequence number "
                f"it was killed on"
            )

        await tb.idle(2)
