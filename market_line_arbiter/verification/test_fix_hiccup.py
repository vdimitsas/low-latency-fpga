# ===============================================================
# File 5 — test_fix_hiccup.py : fix + hiccup/giveup interactions.
#
# Reflects current RTL: sticky holds through the confirm cycle
# itself; release happens the CYCLE AFTER, gated by
# invalidate_feed_q AND the feed still being empty then. The
# release window is exactly ONE cycle wide — new data must be
# presented during that exact cycle, not one cycle later, or
# sticky re-locks onto the dead feed and the countdown restarts.
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


# 1) fix_wins_at_release_cycle — feed f starves to the confirm cycle
#    (chunk still dead); a fix on g is presented at the very next
#    cycle (the release window) — g wins right there.
@cocotb.test()
async def test_fix_wins_one_cycle_after_confirmed_giveup(dut):
    for f in range(NUM_FEEDS):
        g = (f + 1) % NUM_FEEDS
        await reset_dut(dut)
        sb = OutScoreboard(dut)
        await _open_packet(dut, sb, f, 100 + f, 0x1000)

        drive_feeds(dut, valid=0, out_ready=1)
        for _ in range(HICCUP_CYCLES - 1):
            await _edge_sample(dut, sb)

        await ReadOnly()
        inv = (int(dut.invalidate_feed.value) >> f) & 1
        assert inv == 1, \
            f"feed {f}: expected invalidate to fire on the confirm cycle"
        await Timer(1, unit="ns")

        data = [0] * NUM_FEEDS
        data[g] = 0x1F00
        seqs = [0] * NUM_FEEDS
        seqs[g] = 110 + f
        drive_feeds(dut, valid=(1 << g), sop=(1 << g), eop=(1 << g),
                    data=data, seq=seqs, fix_avail=(1 << g))
        await _edge_sample(dut, sb)

        drive_feeds(dut, valid=0, fix_avail=(1 << g))
        await _edge_sample(dut, sb)
        assert int(dut.fix_served_valid.value) == 1 and \
               int(dut.fix_served_feed.value) == g, \
            f"feed {f}: fix on {g} should win after release"

        served = [b for b in sb.got if b["feed"] == g]
        assert served and served[0]["data"] == 0x1F00, \
            f"feed {f}: g={g}'s fix beat should be served, got {sb.got}"
        drive_feeds(dut, valid=0)
        await _edge_sample(dut, sb)
        dut._log.info(f"feed {f}: fix {g} won at the release cycle OK")


# 2) atomicity_beats_fix_before_giveup — feed f is silent for a
#    sub-threshold gap (NOT yet at the confirm cycle) while a fix is
#    genuinely available on g. Sticky holds f regardless. g's fix
#    beat sits in its own skid and is handed off correctly on the
#    trailing edge once f finishes and sticky releases.
@cocotb.test()
async def test_atomicity_beats_fix_before_giveup(dut):
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
        seqs[g] = 210 + f
        drive_feeds(dut, valid=(1 << g), sop=(1 << g), eop=(1 << g),
                    data=data, seq=seqs, fix_avail=(1 << g))
        await _edge_sample(dut, sb)

        assert int(dut.fix_served_valid.value) == 0, \
            f"feed {f}: fix must not preempt while f is atomically held"
        assert all(b["feed"] != g for b in sb.got), \
            f"feed {f}: g={g}'s fix beat must not appear, got {sb.got}"

        drive_feeds(dut, valid=0, out_ready=1)
        data2 = [0] * NUM_FEEDS
        data2[f] = 0x2001
        seqs2 = [0] * NUM_FEEDS
        seqs2[f] = 200 + f
        drive_feeds(dut, valid=(1 << f), eop=(1 << f), data=data2,
                    seq=seqs2)
        await _edge_sample(dut, sb)
        exp.append(dict(data=0x2001, seq=200 + f, sop=0, eop=1, feed=f))

        drive_feeds(dut, valid=0, fix_avail=(1 << g))
        await _edge_sample(dut, sb)
        exp.append(dict(data=0x2F00, seq=210 + f, sop=1, eop=1, feed=g))

        drive_feeds(dut, valid=0)
        await _edge_sample(dut, sb)
        sb.expect(exp)
        dut._log.info(f"feed {f}: atomicity beat fix contention from "
                      f"{g}, resumed, completed, and correctly handed "
                      f"off to g's fix OK")


# 3) giveup_cancelled_ignores_fix — at the confirm cycle, f's own
#    live chunk IS valid (cancels invalidate); even with a fix
#    available elsewhere, sticky stays held (invalidate_feed never
#    fired, so invalidate_feed_q never arms), and f continues; g's
#    fix is then served correctly once f's own eop later releases
#    sticky.
@cocotb.test()
async def test_giveup_cancelled_ignores_fix(dut):
    for f in range(NUM_FEEDS):
        g = (f + 1) % NUM_FEEDS
        await reset_dut(dut)
        sb = OutScoreboard(dut)
        exp = []
        await _open_packet(dut, sb, f, 300 + f, 0x3000)
        exp.append(dict(data=0x3000, seq=300 + f, sop=1, eop=0, feed=f))

        drive_feeds(dut, valid=0, out_ready=1)
        for _ in range(HICCUP_CYCLES - 2):
            await _edge_sample(dut, sb)

        data = [0] * NUM_FEEDS
        data[f] = 0x3001
        data[g] = 0x3F00
        seqs = [0] * NUM_FEEDS
        seqs[f] = 300 + f
        seqs[g] = 310 + f
        drive_feeds(dut, valid=(1 << f) | (1 << g), sop=(1 << g),
                    eop=(1 << g), data=data, seq=seqs,
                    fix_avail=(1 << g))
        await ReadOnly()
        inv = (int(dut.invalidate_feed.value) >> f) & 1
        assert inv == 0, \
            f"feed {f}: invalidate must cancel when f's own chunk " \
            f"is valid, even with a fix available elsewhere"
        await Timer(1, unit="ns")
        await _edge_sample(dut, sb)
        exp.append(dict(data=0x3001, seq=300 + f, sop=0, eop=0, feed=f))

        assert all(b["feed"] != g for b in sb.got), \
            f"feed {f}: fix on {g} must not win while f legitimately " \
            f"continues, got {sb.got}"

        data2 = [0] * NUM_FEEDS
        data2[f] = 0x3002
        seqs2 = [0] * NUM_FEEDS
        seqs2[f] = 300 + f
        drive_feeds(dut, valid=(1 << f), eop=(1 << f), data=data2,
                    seq=seqs2, fix_avail=(1 << g))
        await _edge_sample(dut, sb)
        exp.append(dict(data=0x3002, seq=300 + f, sop=0, eop=1, feed=f))

        drive_feeds(dut, valid=0, fix_avail=(1 << g))
        await _edge_sample(dut, sb)
        exp.append(dict(data=0x3F00, seq=310 + f, sop=1, eop=1, feed=g))

        drive_feeds(dut, valid=0)
        await _edge_sample(dut, sb)
        sb.expect(exp)
        dut._log.info(f"feed {f}: giveup cancelled, fix on {g} "
                      f"correctly deferred then served OK")


# 4) multiple_fixes_after_giveup — present at the exact release
#    cycle (one edge after confirm), lowest-index fix wins.
@cocotb.test()
async def test_multiple_fixes_after_giveup(dut):
    for f in range(NUM_FEEDS):
        others = [x for x in range(NUM_FEEDS) if x != f]
        winner = min(others)
        await reset_dut(dut)
        sb = OutScoreboard(dut)
        await _open_packet(dut, sb, f, 400 + f, 0x4000)

        drive_feeds(dut, valid=0, out_ready=1)
        for _ in range(HICCUP_CYCLES - 1):
            await _edge_sample(dut, sb)

        mask = 0
        data = [0] * NUM_FEEDS
        seqs = [0] * NUM_FEEDS
        for k in others:
            mask |= (1 << k)
            data[k] = 0x4F00 + k
            seqs[k] = 410 + k
        drive_feeds(dut, valid=mask, sop=mask, eop=mask,
                    data=data, seq=seqs, fix_avail=mask)
        await _edge_sample(dut, sb)

        drive_feeds(dut, valid=0, fix_avail=mask)
        await _edge_sample(dut, sb)
        assert int(dut.fix_served_valid.value) == 1 and \
               int(dut.fix_served_feed.value) == winner, \
            f"feed {f}: expected lowest fix {winner} to win after " \
            f"giveup, got {int(dut.fix_served_feed.value)}"

        served = [b for b in sb.got if b["feed"] == winner]
        assert served, \
            f"feed {f}: winner {winner}'s beat should be served, " \
            f"got {sb.got}"
        drive_feeds(dut, valid=0)
        await _edge_sample(dut, sb)
        dut._log.info(f"feed {f}: lowest fix {winner} won after "
                      f"giveup OK")