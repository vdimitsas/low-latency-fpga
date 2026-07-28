# ===============================================================
# File 4 — test_fix.py : fix_avail preference.
#
# serve_feed_q resets to '0 (not seeded to feed 0), so sticky is
# false on cycle one regardless — serve_mux runs its real priority
# order (fix first, then lowest eff_valid) from the very first
# cycle. A fix ALWAYS wins immediately, even against a lower-index
# normal feed, even on cycle one. No index-race exception exists.
#
# fix_served_valid/fix_served_feed are FLOPPED, registered from the
# same combinational cycle as the actual transfer (out_valid/
# out_data) — both become visible at the SAME edge. So: drive
# inputs, cross ONE real edge, then check both together.
# ===============================================================
import cocotb
from cocotb.triggers import RisingEdge, ClockCycles, Timer, ReadOnly
from arb_common import (NUM_FEEDS, FULL, HICCUP_CYCLES, reset_dut,
                        drive_feeds, OutScoreboard)


async def _edge_sample(dut, sb):
    await RisingEdge(dut.clk)
    await ReadOnly()
    sb.sample()
    await Timer(1, unit="ns")


# 1) fix_preferred_over_normal — a fix on feed f beats a waiting
#    normal feed immediately, regardless of index (per feed)
@cocotb.test()
async def test_fix_preferred_over_normal(dut):
    for f in range(NUM_FEEDS):
        normal = (f + 1) % NUM_FEEDS
        await reset_dut(dut)
        sb = OutScoreboard(dut)
        data = [0] * NUM_FEEDS
        data[f]      = 0x1F00
        data[normal] = 0x1BAD
        seqs = [0] * NUM_FEEDS
        seqs[f]      = 100 + f
        seqs[normal] = 900

        drive_feeds(dut, valid=(1 << f) | (1 << normal),
                    sop=(1 << f) | (1 << normal),
                    eop=(1 << f) | (1 << normal),
                    data=data, seq=seqs, fix_avail=(1 << f))
        await _edge_sample(dut, sb)
        assert int(dut.fix_served_valid.value) == 1, \
            f"feed {f}: expected fix_served_valid=1"
        assert int(dut.fix_served_feed.value) == f, \
            f"feed {f}: fix_served_feed={int(dut.fix_served_feed.value)}"
        assert sb.got and sb.got[0]["data"] == 0x1F00 and \
               sb.got[0]["feed"] == f, \
            f"feed {f}: fix beat should be served first, got {sb.got}"
        drive_feeds(dut, valid=0)
        await _edge_sample(dut, sb)
        dut._log.info(f"feed {f}: fix preferred over normal {normal} OK")


# 2) multiple_fixes_lowest_fix_wins — several flagged: lowest index
@cocotb.test()
async def test_multiple_fixes_lowest_fix_wins(dut):
    cases = [[1, 3], [0, 2, 3], [2, 3], [1, 2]]
    for feeds in cases:
        winner = min(feeds)
        mask = 0
        for k in feeds:
            mask |= (1 << k)
        await reset_dut(dut)
        sb = OutScoreboard(dut)
        data = [0x2000 + i for i in range(NUM_FEEDS)]
        seqs = [200 + i for i in range(NUM_FEEDS)]
        drive_feeds(dut, valid=mask, sop=mask, eop=mask,
                    data=data, seq=seqs, fix_avail=mask)
        await _edge_sample(dut, sb)
        assert int(dut.fix_served_valid.value) == 1, \
            f"feeds {feeds}: expected fix_served_valid=1"
        assert int(dut.fix_served_feed.value) == winner, \
            f"feeds {feeds}: got {int(dut.fix_served_feed.value)}, " \
            f"expected {winner}"
        assert sb.got and sb.got[0]["feed"] == winner, \
            f"feeds {feeds}: served beat from {sb.got[0]['feed']}"
        drive_feeds(dut, valid=0)
        await _edge_sample(dut, sb)
        dut._log.info(f"feeds {feeds}: lowest fix {winner} wins OK")


# 3) fix_beats_lower_normal — fix on a HIGH index vs normal data on
#    LOWER indices: the fix wins immediately, no exception
@cocotb.test()
async def test_fix_beats_lower_normal(dut):
    cases = [(0, 2), (1, 3), (0, 3), (2, 3), (0, 1)]
    for normal_low, fix_high in cases:
        await reset_dut(dut)
        sb = OutScoreboard(dut)
        data = [0] * NUM_FEEDS
        data[normal_low] = 0x3BAD
        data[fix_high]   = 0x3F00
        seqs = [0] * NUM_FEEDS
        seqs[normal_low] = 300
        seqs[fix_high]   = 301

        drive_feeds(dut, valid=(1 << normal_low) | (1 << fix_high),
                    sop=(1 << normal_low) | (1 << fix_high),
                    eop=(1 << normal_low) | (1 << fix_high),
                    data=data, seq=seqs, fix_avail=(1 << fix_high))
        await _edge_sample(dut, sb)
        assert int(dut.fix_served_valid.value) == 1 and \
               int(dut.fix_served_feed.value) == fix_high, \
            f"n={normal_low} fix={fix_high}: fix should win immediately"
        assert sb.got and sb.got[0]["feed"] == fix_high, \
            f"n={normal_low} fix={fix_high}: fix beat should be served " \
            f"first, got {sb.got}"
        drive_feeds(dut, valid=0)
        await _edge_sample(dut, sb)
        dut._log.info(f"fix {fix_high} beats lower normal {normal_low} "
                      f"immediately OK")


# 4) mid_packet_immunity — a fix flagged while a packet streams must
#    wait: the stream is not preempted; the fix wins right after held
#    finishes
@cocotb.test()
async def test_mid_packet_immunity(dut):
    for held in range(NUM_FEEDS):
        fixf = (held + 2) % NUM_FEEDS
        await reset_dut(dut)
        sb = OutScoreboard(dut)
        seqs = [0] * NUM_FEEDS
        seqs[held] = 400 + held
        seqs[fixf] = 410 + held
        exp = []

        # held presents ALONE at k=0 so it becomes the sticky pick
        # unconditionally -- nothing else contends, so index order
        # (fixf vs held) can never hijack the first serve. fixf's
        # beat (and fix_avail) only show up from k=1, once held is
        # already genuinely mid-packet.
        for k in range(3):
            data = [0] * NUM_FEEDS
            data[held] = 0x4000 + k
            sop = 1 if k == 0 else 0
            eop = 1 if k == 2 else 0
            valid_mask = (1 << held)
            sop_mask   = (sop << held)
            eop_mask   = (eop << held)
            if k == 1:
                data[fixf] = 0x4F00
                valid_mask |= (1 << fixf)
                sop_mask   |= (1 << fixf)
                eop_mask   |= (1 << fixf)
            drive_feeds(dut,
                        valid=valid_mask, sop=sop_mask, eop=eop_mask,
                        data=data, seq=seqs,
                        fix_avail=(1 << fixf) if k >= 1 else 0)
            await _edge_sample(dut, sb)
            exp.append(dict(data=0x4000 + k, seq=400 + held,
                            sop=sop, eop=eop, feed=held))
            if k == 1:
                fsv = int(dut.fix_served_valid.value)
                dut._log.info(f"[DEBUG] held={held} fixf={fixf} k={k} "
                              f"fix_served_valid={fsv}")
                assert fsv == 0, \
                    f"held {held}: fix must not preempt mid-packet (k={k})"

        drive_feeds(dut, valid=0, fix_avail=(1 << fixf))
        await _edge_sample(dut, sb)
        assert int(dut.fix_served_valid.value) == 1 and \
               int(dut.fix_served_feed.value) == fixf, \
            f"held {held}: fix should win right after held's own EOP"
        exp.append(dict(data=0x4F00, seq=410 + held, sop=1, eop=1,
                        feed=fixf))
        drive_feeds(dut, valid=0)
        await _edge_sample(dut, sb)
        sb.expect(exp)
        dut._log.info(f"held {held}: immune mid-packet, fix at EOP OK")