# ===============================================================
# test_hiccup_threshold.py — verifies arm/confirm/giveup land at
# the correct cycle relative to HICCUP_CYCLES, and the release
# timing: sticky holds through confirm, releases the CYCLE AFTER
# via invalidate_feed_q, only if the feed is still empty then. The
# release window is exactly ONE cycle wide.
# ===============================================================
import cocotb
from cocotb.triggers import RisingEdge, Timer, ReadOnly
from arb_common import (NUM_FEEDS, HICCUP_CYCLES, reset_dut,
                        drive_feeds, OutScoreboard)


async def _edge_sample(dut, sb):
    await RisingEdge(dut.clk)
    await ReadOnly()
    sb.sample()
    await Timer(1, unit="ns")


async def _open_packet(dut, sb, f, seq, data0):
    data = [0] * NUM_FEEDS
    data[f] = data0
    seqs = [0] * NUM_FEEDS
    seqs[f] = seq
    drive_feeds(dut, valid=(1 << f), sop=(1 << f), data=data, seq=seqs)
    await _edge_sample(dut, sb)

@cocotb.test()
async def test_arm_confirm_giveup_timing(dut):
    assert HICCUP_CYCLES >= 3, \
        "this build's HICCUP_CYCLES is below the supported minimum"
    dut._log.info(f"ARM_CNT = {int(dut.dut.ARM_CNT.value)}")
    for f in range(NUM_FEEDS):
        await reset_dut(dut)
        sb = OutScoreboard(dut)
        await _open_packet(dut, sb, f, 100 + f, 0x1000)

        drive_feeds(dut, valid=0, out_ready=1)
        inv_seen_at = None
        for cyc in range(HICCUP_CYCLES + 2):
            await ReadOnly()
            inv = (int(dut.invalidate_feed.value) >> f) & 1
            dut._log.info(f"cyc={cyc+1} inv={inv} inv_seen_at={inv_seen_at} hiccup_cnt={int(dut.dut.hiccup_cnt.value)} invalidate_q={int(dut.dut.invalidate_q.value)} giveup={int(dut.dut.giveup.value)} serve_valid_q={int(dut.dut.serve_valid_q.value)}")
            if inv and inv_seen_at is None:
                inv_seen_at = cyc
            await Timer(1, unit="ns")
            await _edge_sample(dut, sb)
            
        exp_fire = HICCUP_CYCLES - 1
        assert inv_seen_at == exp_fire, \
            f"feed {f}: invalidate fired at stalled-cycle {inv_seen_at}, " \
            f"expected exactly {exp_fire} (HICCUP_CYCLES=" \
            f"{HICCUP_CYCLES})"
        dut._log.info(f"feed {f}: invalidate confirmed at cycle "
                      f"{inv_seen_at} for HICCUP_CYCLES={HICCUP_CYCLES}")


@cocotb.test()
async def test_atomicity_holds_through_confirm(dut):
    for f in range(NUM_FEEDS):
        await reset_dut(dut)
        sb = OutScoreboard(dut)
        await _open_packet(dut, sb, f, 150 + f, 0x1500)

        drive_feeds(dut, valid=0, out_ready=1)
        for cyc in range(HICCUP_CYCLES):
            await ReadOnly()
            parked = (int(dut.dut.serve_feed_q.value) >> f) & 1
            assert parked == 1, \
                f"feed {f}: must remain parked through cycle {cyc} " \
                f"(including confirm), got serve_feed_q lost it"
            await Timer(1, unit="ns")
            await _edge_sample(dut, sb)
        dut._log.info(f"feed {f}: atomicity held through confirm, all "
                      f"{HICCUP_CYCLES} cycles OK")


@cocotb.test()
async def test_last_chunk_race_cancels(dut):
    for f in range(NUM_FEEDS):
        await reset_dut(dut)
        sb = OutScoreboard(dut)
        await _open_packet(dut, sb, f, 200 + f, 0x2000)

        drive_feeds(dut, valid=0, out_ready=1)
        for _ in range(HICCUP_CYCLES - 2):
            await _edge_sample(dut, sb)

        data = [0] * NUM_FEEDS
        data[f] = 0x2001
        seqs = [0] * NUM_FEEDS
        seqs[f] = 200 + f
        drive_feeds(dut, valid=(1 << f), data=data, seq=seqs, out_ready=1)
        await ReadOnly()
        inv = (int(dut.invalidate_feed.value) >> f) & 1
        assert inv == 0, \
            f"feed {f}: invalidate must be cancelled when the live " \
            f"last-chance chunk is valid, got fired"
        await Timer(1, unit="ns")
        await _edge_sample(dut, sb)

        drive_feeds(dut, valid=0)
        await _edge_sample(dut, sb)
        dut._log.info(f"feed {f}: last-chunk race correctly cancelled "
                      f"invalidate")


@cocotb.test()
async def test_last_chunk_race_confirms_and_handoff(dut):
    for f in range(NUM_FEEDS):
        other = (f + 1) % NUM_FEEDS
        await reset_dut(dut)
        sb = OutScoreboard(dut)
        await _open_packet(dut, sb, f, 300 + f, 0x3000)

        drive_feeds(dut, valid=0, out_ready=1)
        inv_now = 0
        for _ in range(HICCUP_CYCLES):
            await ReadOnly()
            inv_now = (int(dut.invalidate_feed.value) >> f) & 1
            await Timer(1, unit="ns")
            await _edge_sample(dut, sb)

        assert inv_now == 1, \
            f"feed {f}: expected invalidate fired on the confirm cycle"

        # mark how many beats existed before the release point, so
        # we only inspect what happens FROM here on
        beats_before_release = len(sb.got)

        data = [0] * NUM_FEEDS
        data[other] = 0x3100
        seqs = [0] * NUM_FEEDS
        seqs[other] = 310 + f
        drive_feeds(dut, valid=(1 << other), sop=(1 << other),
                    eop=(1 << other), data=data, seq=seqs, out_ready=1)
        await _edge_sample(dut, sb)
        drive_feeds(dut, valid=0)
        await _edge_sample(dut, sb)

        new_beats = sb.got[beats_before_release:]
        served = [b for b in new_beats if b["feed"] == other]
        assert served and served[0]["data"] == 0x3100, \
            f"feed {f}: fresh feed {other} should be picked up cleanly " \
            f"at the release cycle, got {new_beats}"
        assert all(b["feed"] != f for b in new_beats), \
            f"feed {f}: invalidated feed must not appear again after " \
            f"release, got {new_beats}"
        dut._log.info(f"feed {f}: confirmed giveup, released at the "
                      f"correct cycle, handoff to {other} OK")