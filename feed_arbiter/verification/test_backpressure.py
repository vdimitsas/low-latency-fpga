# ===============================================================
# File 2 — test_backpressure.py : out_ready / in_ready / skid.
#
# Reflects current RTL:
#   - in_ready[i] = !skid_valid[i] || (serve_feed[i] && out_ready),
#     driven COMBINATIONALLY, straight out to DATA_FWD (no flop).
#   - skid capture (empty case) fires whenever a beat is NOT directly
#     transferred this cycle.
#   - occupied+served: skid drains to output; if a new beat arrives
#     with in_ready, it refills the skid in place, else it empties.
#   - hiccup_cnt is a SCALAR, and only advances/clears when out_ready
#     is HIGH — a downstream stall must never count toward giveup,
#     and must never clear it either.
#   - hiccup_cnt's clear-condition reads serve_valid_q, which is
#     ONE CYCLE BEHIND the live silence — so after N silent cycles,
#     hiccup_cnt reads (N-1), floored at 0, not N.
# ===============================================================
import cocotb
from cocotb.triggers import RisingEdge, ClockCycles, Timer, ReadOnly
from arb_common import (NUM_FEEDS, FULL, HICCUP_CYCLES, PKT_LEN_SHORT,
                        reset_dut, drive_feeds, OutScoreboard,
                        assert_skid_state)


async def _edge_sample(dut, sb):
    await RisingEdge(dut.clk)
    await ReadOnly()
    sb.sample()
    await Timer(1, unit="ns")


def _dbg_skid(dut, f, label):
    sv  = (int(dut.dut.skid_valid.value) >> f) & 1
    svn = (int(dut.dut.skid_valid_nxt.value) >> f) & 1
    sd  = int(dut.dut.skid_data[f].value)
    sdn = int(dut.dut.skid_data_nxt[f].value)
    ss  = (int(dut.dut.skid_sop.value) >> f) & 1
    ssn = (int(dut.dut.skid_sop_nxt.value) >> f) & 1
    se  = (int(dut.dut.skid_eop.value) >> f) & 1
    sen = (int(dut.dut.skid_eop_nxt.value) >> f) & 1
    sq  = int(dut.dut.skid_seq[f].value)
    sqn = int(dut.dut.skid_seq_nxt[f].value)
    srv = (int(dut.dut.serve_feed.value) >> f) & 1
    dut._log.info(f"[SKID] feed={f} {label}")
    dut._log.info(f"  skid_valid     = {hex(sv)}")
    dut._log.info(f"  skid_valid_nxt = {hex(svn)}")
    dut._log.info(f"  skid_data      = {hex(sd)}")
    dut._log.info(f"  skid_data_nxt  = {hex(sdn)}")
    dut._log.info(f"  skid_sop       = {hex(ss)}")
    dut._log.info(f"  skid_sop_nxt   = {hex(ssn)}")
    dut._log.info(f"  skid_eop       = {hex(se)}")
    dut._log.info(f"  skid_eop_nxt   = {hex(sen)}")
    dut._log.info(f"  skid_seq       = {hex(sq)}")
    dut._log.info(f"  skid_seq_nxt   = {hex(sqn)}")
    dut._log.info(f"  serve_feed     = {hex(srv)}")


# ===============================================================
# 1) freeze_mid_packet
# ===============================================================
@cocotb.test()
async def test_freeze_mid_packet(dut):
    for f in range(NUM_FEEDS):
        await reset_dut(dut)
        sb = OutScoreboard(dut)
        seqs = [0] * NUM_FEEDS
        seqs[f] = 300 + f
        exp = []

        data = [0] * NUM_FEEDS
        data[f] = 0xA000
        drive_feeds(dut, valid=(1 << f), sop=(1 << f), data=data, seq=seqs)
        await _edge_sample(dut, sb)
        exp.append(dict(data=0xA000, seq=300 + f, sop=1, eop=0, feed=f))

        data[f] = 0xA001
        drive_feeds(dut, valid=(1 << f), data=data, seq=seqs, out_ready=0)
        await _edge_sample(dut, sb)

        drive_feeds(dut, valid=0, out_ready=0)
        for _ in range(3):
            await _edge_sample(dut, sb)

        drive_feeds(dut, valid=0, out_ready=1)
        await _edge_sample(dut, sb)
        exp.append(dict(data=0xA001, seq=300 + f, sop=0, eop=0, feed=f))

        data[f] = 0xA002
        drive_feeds(dut, valid=(1 << f), eop=(1 << f), data=data, seq=seqs)
        await _edge_sample(dut, sb)
        exp.append(dict(data=0xA002, seq=300 + f, sop=0, eop=1, feed=f))

        drive_feeds(dut, valid=0)
        await _edge_sample(dut, sb)
        sb.expect(exp)
        dut._log.info(f"feed {f}: freeze + skid resume OK")


# ===============================================================
# 2) skid_catches_stale_beat
# ===============================================================
@cocotb.test()
async def test_skid_catches_stale_beat(dut):
    for f in range(NUM_FEEDS):
        await reset_dut(dut)
        sb = OutScoreboard(dut)
        seqs = [0] * NUM_FEEDS
        seqs[f] = 400 + f
        data = [0] * NUM_FEEDS
        data[f] = 0xB000

        drive_feeds(dut, valid=(1 << f), sop=(1 << f), eop=(1 << f),
                    data=data, seq=seqs, out_ready=0)
        await _edge_sample(dut, sb)
        assert_skid_state(dut, f, valid=1, data=0xB000, seq=400 + f, sop=1,
                          msg=f"(feed {f}, stale-window capture)")

        drive_feeds(dut, valid=0, out_ready=0)
        await _edge_sample(dut, sb)

        drive_feeds(dut, valid=0, out_ready=1)
        await _edge_sample(dut, sb)
        drive_feeds(dut, valid=0)
        await _edge_sample(dut, sb)
        sb.expect([dict(data=0xB000, seq=400 + f, sop=1, eop=1, feed=f)])
        dut._log.info(f"feed {f}: stale beat caught and served OK")


# ===============================================================
# 3) skid_refill_same_cycle
# ===============================================================
@cocotb.test()
async def test_skid_refill_same_cycle(dut):
    for f in range(NUM_FEEDS):
        await reset_dut(dut)
        sb = OutScoreboard(dut)
        seqs = [0] * NUM_FEEDS
        seqs[f] = 500 + f
        data = [0] * NUM_FEEDS

        data[f] = 0xC0A0
        drive_feeds(dut, valid=(1 << f), sop=(1 << f), data=data,
                    seq=seqs, out_ready=0)
        await _edge_sample(dut, sb)
        _dbg_skid(dut, f, "after A (stale capture)")

        data[f] = 0xC0B0
        drive_feeds(dut, valid=(1 << f), data=data, seq=seqs, out_ready=1)
        await _edge_sample(dut, sb)
        _dbg_skid(dut, f, "after B presented (expect refill)")
        assert_skid_state(dut, f, valid=1, data=0xC0B0, seq=500 + f, sop=0,
                          msg=f"(feed {f}, refill in place)")

        data[f] = 0xC0C0
        drive_feeds(dut, valid=(1 << f), eop=(1 << f), data=data, seq=seqs,
                    out_ready=1)
        await _edge_sample(dut, sb)
        _dbg_skid(dut, f, "after C (B should have drained)")

        drive_feeds(dut, valid=0)
        await _edge_sample(dut, sb)
        sb.expect([
            dict(data=0xC0A0, seq=500 + f, sop=1, eop=0, feed=f),
            dict(data=0xC0B0, seq=500 + f, sop=0, eop=0, feed=f),
            dict(data=0xC0C0, seq=500 + f, sop=0, eop=1, feed=f),
        ])
        dut._log.info(f"feed {f}: refill-in-place, correct order OK")


# ===============================================================
# 4) in_ready_reset_ones
# ===============================================================
@cocotb.test()
async def test_in_ready_reset_ones(dut):
    await reset_dut(dut)
    await ReadOnly()
    ir = int(dut.in_ready.value)
    assert ir == FULL, f"in_ready after reset: {ir:04b}, expected all 1s"
    await Timer(1, unit="ns")
    dut._log.info("in_ready resets to all-ones OK")


# ===============================================================
# 5) in_ready_full_skid_unserved_zero
# ===============================================================
@cocotb.test()
async def test_in_ready_full_skid_unserved_zero(dut):
    for f in range(NUM_FEEDS):
        served  = (f + 1) % NUM_FEEDS
        idle_f  = next(x for x in range(NUM_FEEDS) if x not in (f, served))
        await reset_dut(dut)
        seqs = [0] * NUM_FEEDS
        data = [0] * NUM_FEEDS

        seqs[served] = 700 + f
        data[served] = 0xD100
        drive_feeds(dut, valid=(1 << served), sop=(1 << served),
                    data=data, seq=seqs, out_ready=1)
        await RisingEdge(dut.clk)
        await Timer(1, unit="ns")

        data[f] = 0xD000
        seqs[f] = 600 + f
        data[served] = 0xD101
        drive_feeds(dut, valid=(1 << f) | (1 << served), sop=(1 << f),
                    data=data, seq=seqs, out_ready=1)
        await RisingEdge(dut.clk)
        await ReadOnly()
        _dbg_skid(dut, f, "after filling f's skid (served stays sticky)")
        await Timer(1, unit="ns")

        for k in range(3):
            data[served] = 0xD102 + k
            drive_feeds(dut, valid=(1 << served), data=data,
                        seq=seqs, out_ready=1)
            await RisingEdge(dut.clk)
            await ReadOnly()
            _dbg_skid(dut, f, f"loop k={k}, checking f")
            ir = int(dut.in_ready.value)
            assert (ir >> f) & 1 == 0, \
                f"feed {f}: full-skid unserved must read in_ready=0, " \
                f"got {ir:04b}"
            assert (ir >> idle_f) & 1 == 1, \
                f"feed {idle_f}: empty-skid unserved must read 1, " \
                f"got {ir:04b}"
            assert (ir >> served) & 1 == 1, \
                f"feed {served}: actively served must read 1, got {ir:04b}"
            await Timer(1, unit="ns")
        dut._log.info(f"feed {f}: per-feed in_ready semantics OK")


# ===============================================================
# 6) out_ready_bounce
# ===============================================================
@cocotb.test()
async def test_out_ready_bounce(dut):
    for f in range(NUM_FEEDS):
        await reset_dut(dut)
        sb = OutScoreboard(dut)
        seqs = [0] * NUM_FEEDS
        seqs[f] = 800 + f
        exp = []
        data = [0] * NUM_FEEDS

        data[f] = 0xE000
        drive_feeds(dut, valid=(1 << f), sop=(1 << f), data=data, seq=seqs)
        await _edge_sample(dut, sb)
        exp.append(dict(data=0xE000, seq=800 + f, sop=1, eop=0, feed=f))

        data[f] = 0xE001
        drive_feeds(dut, valid=(1 << f), data=data, seq=seqs, out_ready=0)
        await _edge_sample(dut, sb)

        drive_feeds(dut, valid=0, out_ready=1)
        await _edge_sample(dut, sb)
        exp.append(dict(data=0xE001, seq=800 + f, sop=0, eop=0, feed=f))

        data[f] = 0xE002
        drive_feeds(dut, valid=(1 << f), eop=(1 << f), data=data,
                    seq=seqs, out_ready=0)
        await _edge_sample(dut, sb)
        drive_feeds(dut, valid=0, out_ready=1)
        await _edge_sample(dut, sb)
        exp.append(dict(data=0xE002, seq=800 + f, sop=0, eop=1, feed=f))

        drive_feeds(dut, valid=0)
        await _edge_sample(dut, sb)
        sb.expect(exp)
        dut._log.info(f"feed {f}: bounce delivered all beats once OK")


# ===============================================================
# 7) long_stall_no_hiccup — hiccup_cnt can be frozen by an out_ready
#    stall at ANY point in its counting range. hiccup_cnt trails
#    live silence by one cycle (its clear-condition reads serve_valid_q,
#    which is itself one cycle behind), so after `stall_at` silent
#    cycles, hiccup_cnt reads max(stall_at - 1, 0), not stall_at.
# ===============================================================
@cocotb.test()
async def test_long_stall_no_hiccup(dut):
    for f in range(NUM_FEEDS):
        for stall_at in range(HICCUP_CYCLES - 1):
            await reset_dut(dut)
            sb = OutScoreboard(dut)
            seqs = [0] * NUM_FEEDS
            seqs[f] = 900 + f
            exp = []

            data = [0] * NUM_FEEDS
            data[f] = 0xF000
            drive_feeds(dut, valid=(1 << f), sop=(1 << f), data=data,
                        seq=seqs)
            await _edge_sample(dut, sb)
            exp.append(dict(data=0xF000, seq=900 + f, sop=1, eop=0,
                            feed=f))

            drive_feeds(dut, valid=0, out_ready=1)
            for _ in range(stall_at):
                await _edge_sample(dut, sb)

            expected_hc = max(stall_at - 1, 0)
            await ReadOnly()
            hc_before = int(dut.dut.hiccup_cnt.value)
            await Timer(1, unit="ns")
            assert hc_before == expected_hc, \
                f"feed {f}: expected hiccup_cnt={expected_hc} before " \
                f"the stall (stall_at={stall_at}), got {hc_before}"

            drive_feeds(dut, valid=0, out_ready=0)
            for _ in range(HICCUP_CYCLES * 2):
                await ReadOnly()
                hc  = int(dut.dut.hiccup_cnt.value)
                inv = (int(dut.invalidate_feed.value) >> f) & 1
                assert hc == expected_hc, \
                    f"feed {f}: hiccup_cnt must freeze at {expected_hc} " \
                    f"during an out_ready=0 stall, got {hc}"
                assert inv == 0, \
                    f"feed {f}: invalidate must never fire during a " \
                    f"downstream stall (stall_at={stall_at}), got fired"
                await Timer(1, unit="ns")
                await _edge_sample(dut, sb)

            drive_feeds(dut, valid=0, out_ready=1)
            await _edge_sample(dut, sb)
            await ReadOnly()
            hc_after = int(dut.dut.hiccup_cnt.value)
            await Timer(1, unit="ns")
            assert hc_after == expected_hc + 1, \
                f"feed {f}: after resume, hiccup_cnt should continue " \
                f"from {expected_hc} (-> {expected_hc + 1}), got {hc_after}"

            data[f] = 0xF001
            drive_feeds(dut, valid=(1 << f), eop=(1 << f), data=data,
                        seq=seqs, out_ready=1)
            await _edge_sample(dut, sb)
            exp.append(dict(data=0xF001, seq=900 + f, sop=0, eop=1,
                            feed=f))

            drive_feeds(dut, valid=0)
            await _edge_sample(dut, sb)
            sb.expect(exp)
            dut._log.info(f"feed {f}, stall_at={stall_at}: froze "
                          f"correctly, resumed correctly, completed OK")