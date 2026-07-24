# ===============================================================
# File 1 — test_mainstream.py : basic serving behavior.
# Every test sweeps ALL feeds. All tests check the registered
# out_* data path via the scoreboard (content, order, boundaries).
# ===============================================================
import cocotb
from cocotb.triggers import RisingEdge, ClockCycles, Timer, ReadOnly
from arb_common import (NUM_FEEDS, FULL, PKT_LEN_LONG, PKT_LEN_SHORT,
                        PKT_LEN_EXTRA, reset_dut, drive_feeds,
                        OutScoreboard, run_packet, assert_skid_state)


# 1) single_feed_full_packet — one feed alone, SOP->EOP, per feed
@cocotb.test()
async def test_single_feed_full_packet(dut):
    for f in range(NUM_FEEDS):
        await reset_dut(dut)
        sb = OutScoreboard(dut)
        exp = await run_packet(dut, sb, feed=f, seq=100 + f,
                               n_chunks=PKT_LEN_LONG, base_data=0x1000 * (f + 1))
        sb.expect(exp)
        dut._log.info(f"feed {f}: full packet OK")


# 2) contention_lowest_normal_wins — all feeds valid, lowest serves
#    the whole packet atomically; swept by masking lower feeds off
@cocotb.test()
async def test_contention_lowest_normal_wins(dut):
    """All feeds present chunk 0 together; all get absorbed (lowest
    served directly, the rest captured into their own skids). From
    chunk 1 onward only `lowest` keeps advancing — the others present
    the SAME unchanged chunk 1 forever (a real sender never advances
    data that in_ready rejected), and must show in_ready=0 the whole
    time, proving they are truly never absorbed again."""
    for lowest in range(NUM_FEEDS):
        others = [f for f in range(NUM_FEEDS) if f > lowest]
        await reset_dut(dut)
        sb = OutScoreboard(dut)
        n = PKT_LEN_EXTRA
        exp = []

        other_chunk0_data = {f: 0xC000 + f for f in others}
        other_chunk0_seq  = {f: 800 + f for f in others}
        other_chunk1_data = {f: 0xBAD0 + f for f in others}
        other_chunk1_seq  = {f: 900 + f for f in others}

        for k in range(n):
            sop = 1 if k == 0 else 0
            eop = 1 if k == n - 1 else 0
            data = [0] * NUM_FEEDS
            seqs = [0] * NUM_FEEDS
            data[lowest] = 0x2000 + k
            seqs[lowest] = 500

            valid = (1 << lowest)
            sop_mask = (sop << lowest)
            eop_mask = (eop << lowest)

            if k == 0:
                for f in others:
                    data[f] = other_chunk0_data[f]
                    seqs[f] = other_chunk0_seq[f]
                    valid    |= (1 << f)
                    sop_mask |= (1 << f)
            else:
                for f in others:
                    data[f] = other_chunk1_data[f]
                    seqs[f] = other_chunk1_seq[f]
                    valid |= (1 << f)

            drive_feeds(dut, valid=valid, sop=sop_mask, eop=eop_mask,
                        data=data, seq=seqs)
            await RisingEdge(dut.clk)
            await ReadOnly()
            sb.sample()
            exp.append(dict(data=0x2000 + k, seq=500, sop=sop, eop=eop,
                            feed=lowest))
            await Timer(1, unit="ns")

        drive_feeds(dut, valid=0)
        sb.expect(exp)

        for f in others:
            sb.expect_in_ready_never(f, from_sample=1,
                                     msg=f"(lowest={lowest})")

        await ReadOnly()
        for f in others:
            assert_skid_state(dut, f, valid=1,
                              data=other_chunk0_data[f],
                              seq=other_chunk0_seq[f], sop=1,
                              msg=f"(lowest={lowest}, chunk-1 attempt "
                                  f"{hex(other_chunk1_data[f])} must be "
                                  f"rejected, not captured)")
        await Timer(1, unit="ns")

        dut._log.info(f"lowest {lowest}: atomic win; others held+blocked, "
                      f"skid contents verified frozen OK")


# 3) eop_handoff_next_feed — after EOP, a waiting feed is served
@cocotb.test()
async def test_eop_handoff_next_feed(dut):
    for first in range(NUM_FEEDS):
        for second in range(NUM_FEEDS):
            if second == first:
                continue
            await reset_dut(dut)
            sb = OutScoreboard(dut)
            e1 = await run_packet(dut, sb, feed=first, seq=700,
                                  n_chunks=PKT_LEN_SHORT, base_data=0x3000)
            e2 = await run_packet(dut, sb, feed=second, seq=701,
                                  n_chunks=PKT_LEN_SHORT, base_data=0x4000)
            sb.expect(e1 + e2)
    dut._log.info("eop handoff swept all pairs OK")


# 4) back_to_back_same_feed — EOP then new SOP immediately, no gap
@cocotb.test()
async def test_back_to_back_same_feed(dut):
    for f in range(NUM_FEEDS):
        await reset_dut(dut)
        sb = OutScoreboard(dut)
        exp = []
        for pkt in range(2):
            n = PKT_LEN_SHORT
            for k in range(n):
                sop = 1 if k == 0 else 0
                eop = 1 if k == n - 1 else 0
                data = [0] * NUM_FEEDS
                data[f] = 0x5000 + pkt * 0x100 + k
                seqs = [0] * NUM_FEEDS
                seqs[f] = 800 + pkt
                drive_feeds(dut, valid=(1 << f), sop=(sop << f),
                            eop=(eop << f), data=data, seq=seqs)
                await RisingEdge(dut.clk)
                await ReadOnly()
                sb.sample()
                exp.append(dict(data=0x5000 + pkt * 0x100 + k,
                                seq=800 + pkt, sop=sop, eop=eop, feed=f))
                await Timer(1, unit="ns")
        drive_feeds(dut, valid=0)
        await RisingEdge(dut.clk)
        await ReadOnly()
        sb.sample()
        await Timer(1, unit="ns")
        sb.expect(exp)
        dut._log.info(f"feed {f}: back-to-back OK")


# 5) single_chunk_packet — SOP=EOP one beat, alone and back-to-back
@cocotb.test()
async def test_single_chunk_packet(dut):
    for f in range(NUM_FEEDS):
        await reset_dut(dut)
        sb = OutScoreboard(dut)
        exp = []
        for pkt in range(3):
            data = [0] * NUM_FEEDS
            data[f] = 0x6000 + pkt
            seqs = [0] * NUM_FEEDS
            seqs[f] = 900 + pkt
            drive_feeds(dut, valid=(1 << f), sop=(1 << f),
                        eop=(1 << f), data=data, seq=seqs)
            await RisingEdge(dut.clk)
            await ReadOnly()
            sb.sample()
            exp.append(dict(data=0x6000 + pkt, seq=900 + pkt,
                            sop=1, eop=1, feed=f))
            await Timer(1, unit="ns")
        drive_feeds(dut, valid=0)
        await RisingEdge(dut.clk)
        await ReadOnly()
        sb.sample()
        await Timer(1, unit="ns")
        sb.expect(exp)
        dut._log.info(f"feed {f}: single-chunk packets OK")


# 6) speculative_hit_zero_loss — pick parked on feed 0, beat on feed 0
#    -> transfers the SAME cycle (out_* one flop later)
@cocotb.test()
async def test_speculative_hit_zero_loss(dut):
    await reset_dut(dut)
    sb = OutScoreboard(dut)
    data = [0] * NUM_FEEDS
    data[0] = 0x8000
    seqs = [0] * NUM_FEEDS
    seqs[0] = 1100
    drive_feeds(dut, valid=1, sop=1, eop=1, data=data, seq=seqs)
    await RisingEdge(dut.clk)
    await ReadOnly()
    sb.sample()
    await Timer(1, unit="ns")
    drive_feeds(dut, valid=0)
    sb.expect([dict(data=0x8000, seq=1100, sop=1, eop=1, feed=0)])
    dut._log.info("speculative hit: zero-loss OK")


# 7) redirect_zero_loss — pick parked on feed 0, beat arrives on feed
#    f!=0 -> redirect mux serves it the SAME cycle (per feed)
@cocotb.test()
async def test_redirect_zero_loss(dut):
    for f in range(1, NUM_FEEDS):
        await reset_dut(dut)
        sb = OutScoreboard(dut)
        data = [0] * NUM_FEEDS
        data[f] = 0x9000 + f
        seqs = [0] * NUM_FEEDS
        seqs[f] = 1200 + f
        drive_feeds(dut, valid=(1 << f), sop=(1 << f), eop=(1 << f),
                    data=data, seq=seqs)
        await RisingEdge(dut.clk)
        await ReadOnly()
        sb.sample()
        await Timer(1, unit="ns")
        drive_feeds(dut, valid=0)
        sb.expect([dict(data=0x9000 + f, seq=1200 + f, sop=1, eop=1,
                        feed=f)])
        dut._log.info(f"feed {f}: redirect zero-loss OK")