# ===============================================================
# File 3 — test_hiccup.py : starvation, atomicity, giveup, resume.
#
# Reflects current RTL:
#   - sticky HOLDS the parked feed through the confirm cycle itself
#     (invalidate_feed fires while serve_feed_q still points at it).
#   - Release happens the CYCLE AFTER confirm, gated by
#     invalidate_feed_q (one-cycle-delayed record of invalidate_feed)
#     AND the feed still being empty then — not immediately.
# ===============================================================
import cocotb
from cocotb.triggers import RisingEdge, ClockCycles, Timer, ReadOnly
from arb_common import (NUM_FEEDS, FULL, HICCUP_CYCLES, PKT_LEN_SHORT,
                        reset_dut, drive_feeds, OutScoreboard)


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
async def test_starve_below_threshold_resumes(dut):
    for f in range(NUM_FEEDS):
        await reset_dut(dut)
        sb = OutScoreboard(dut)
        exp = []
        await _open_packet(dut, sb, f, 100 + f, 0x1000)
        exp.append(dict(data=0x1000, seq=100 + f, sop=1, eop=0, feed=f))

        drive_feeds(dut, valid=0, out_ready=1)
        for _ in range(HICCUP_CYCLES - 2):
            await ReadOnly()
            inv = (int(dut.invalidate_feed.value) >> f) & 1
            assert inv == 0, \
                f"feed {f}: invalidate must not fire below threshold"
            await Timer(1, unit="ns")
            await _edge_sample(dut, sb)

        data = [0] * NUM_FEEDS
        data[f] = 0x1001
        seqs = [0] * NUM_FEEDS
        seqs[f] = 100 + f
        drive_feeds(dut, valid=(1 << f), eop=(1 << f), data=data, seq=seqs)
        await _edge_sample(dut, sb)
        exp.append(dict(data=0x1001, seq=100 + f, sop=0, eop=1, feed=f))

        drive_feeds(dut, valid=0)
        await _edge_sample(dut, sb)
        sb.expect(exp)
        dut._log.info(f"feed {f}: sub-threshold starve resumed OK")


@cocotb.test()
async def test_atomicity_holds_despite_contention(dut):
    for f in range(NUM_FEEDS):
        g = (f + 1) % NUM_FEEDS
        await reset_dut(dut)
        sb = OutScoreboard(dut)
        exp = []
        await _open_packet(dut, sb, f, 200 + f, 0x2000)
        exp.append(dict(data=0x2000, seq=200 + f, sop=1, eop=0, feed=f))

        data = [0] * NUM_FEEDS
        data[g] = 0x2F00
        seqs = [0] * NUM_FEEDS
        seqs[g] = 250 + f
        drive_feeds(dut, valid=(1 << g), sop=(1 << g), eop=(1 << g),
                    data=data, seq=seqs, out_ready=1)
        await _edge_sample(dut, sb)

        assert all(b["feed"] != g for b in sb.got), \
            f"feed {f}: g={g}'s beat must not hijack f's atomic hold, " \
            f"got {sb.got}"

        drive_feeds(dut, valid=0, out_ready=1)
        data2 = [0] * NUM_FEEDS
        data2[f] = 0x2001
        seqs2 = [0] * NUM_FEEDS
        seqs2[f] = 200 + f
        drive_feeds(dut, valid=(1 << f), eop=(1 << f), data=data2,
                    seq=seqs2)
        await _edge_sample(dut, sb)
        exp.append(dict(data=0x2001, seq=200 + f, sop=0, eop=1, feed=f))

        drive_feeds(dut, valid=0)
        await _edge_sample(dut, sb)
        exp.append(dict(data=0x2F00, seq=250 + f, sop=1, eop=1, feed=g))

        sb.expect(exp)
        dut._log.info(f"feed {f}: held atomically despite g={g}'s "
                      f"contention, resumed, completed, and correctly "
                      f"handed off to g OK")


@cocotb.test()
async def test_giveup_switches_when_confirmed(dut):
    for f in range(NUM_FEEDS):
        g = (f + 2) % NUM_FEEDS
        await reset_dut(dut)
        sb = OutScoreboard(dut)
        await _open_packet(dut, sb, f, 300 + f, 0x3000)

        drive_feeds(dut, valid=0, out_ready=1)
        inv_fired = False
        for _ in range(HICCUP_CYCLES):
            await ReadOnly()
            inv = (int(dut.invalidate_feed.value) >> f) & 1
            if inv:
                inv_fired = True
            await Timer(1, unit="ns")
            await _edge_sample(dut, sb)
        assert inv_fired, f"feed {f}: expected invalidate to fire " \
                          f"by the confirm cycle"

        # this IS the release cycle (invalidate_feed_q=1 now) — g's
        # data must be presented THIS cycle, not one cycle later
        data = [0] * NUM_FEEDS
        data[g] = 0x3F00
        seqs = [0] * NUM_FEEDS
        seqs[g] = 310 + f
        drive_feeds(dut, valid=(1 << g), sop=(1 << g), eop=(1 << g),
                    data=data, seq=seqs)
        await _edge_sample(dut, sb)
        drive_feeds(dut, valid=0)
        await _edge_sample(dut, sb)

        served = [b for b in sb.got if b["feed"] == g]
        assert served and served[0]["data"] == 0x3F00, \
            f"feed {f}: g={g} should be served cleanly after giveup, " \
            f"got {sb.got}"
        dut._log.info(f"feed {f}: giveup confirmed, switched to g={g} OK")


@cocotb.test()
async def test_giveup_cancelled_resumes_normally(dut):
    for f in range(NUM_FEEDS):
        await reset_dut(dut)
        sb = OutScoreboard(dut)
        exp = []
        await _open_packet(dut, sb, f, 400 + f, 0x4000)
        exp.append(dict(data=0x4000, seq=400 + f, sop=1, eop=0, feed=f))

        drive_feeds(dut, valid=0, out_ready=1)
        for _ in range(HICCUP_CYCLES - 2):
            await _edge_sample(dut, sb)

        data = [0] * NUM_FEEDS
        data[f] = 0x4001
        seqs = [0] * NUM_FEEDS
        seqs[f] = 400 + f
        drive_feeds(dut, valid=(1 << f), data=data, seq=seqs)
        await ReadOnly()
        inv = (int(dut.invalidate_feed.value) >> f) & 1
        assert inv == 0, \
            f"feed {f}: invalidate must cancel on a valid last chunk"
        await Timer(1, unit="ns")
        await _edge_sample(dut, sb)
        exp.append(dict(data=0x4001, seq=400 + f, sop=0, eop=0, feed=f))

        data[f] = 0x4002
        drive_feeds(dut, valid=(1 << f), eop=(1 << f), data=data,
                    seq=seqs)
        await _edge_sample(dut, sb)
        exp.append(dict(data=0x4002, seq=400 + f, sop=0, eop=1, feed=f))

        drive_feeds(dut, valid=0)
        await _edge_sample(dut, sb)
        sb.expect(exp)
        dut._log.info(f"feed {f}: giveup cancelled, packet completed "
                      f"normally OK")


@cocotb.test()
async def test_fresh_packet_after_giveup(dut):
    for f in range(NUM_FEEDS):
        await reset_dut(dut)
        sb = OutScoreboard(dut)
        await _open_packet(dut, sb, f, 500 + f, 0x5000)

        drive_feeds(dut, valid=0, out_ready=1)
        for _ in range(HICCUP_CYCLES - 1):
            await _edge_sample(dut, sb)

        drive_feeds(dut, valid=0)
        await _edge_sample(dut, sb)

        data = [0] * NUM_FEEDS
        data[f] = 0x5100
        seqs = [0] * NUM_FEEDS
        seqs[f] = 510 + f
        drive_feeds(dut, valid=(1 << f), sop=(1 << f), eop=(1 << f),
                    data=data, seq=seqs)
        await _edge_sample(dut, sb)
        drive_feeds(dut, valid=0)
        await _edge_sample(dut, sb)

        served = [b for b in sb.got if b["data"] == 0x5100]
        assert served, f"feed {f}: fresh packet after giveup should " \
                       f"serve normally, got {sb.got}"
        dut._log.info(f"feed {f}: fresh packet after giveup OK")