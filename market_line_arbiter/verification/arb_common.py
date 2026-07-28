# ===============================================================
# arb_common.py — shared helpers for the new market_line_arbiter suite.
# ===============================================================
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, Timer, ReadOnly
import os

NUM_FEEDS     = 4
HICCUP_CYCLES = int(os.environ.get("HICCUP_CYCLES", "4"))
DATA_W        = 64
SEQ_W         = 32
FULL          = (1 << NUM_FEEDS) - 1

# Shared packet-length constants — reference these, never a bare
# literal, so packet size is changed in exactly one place.
PKT_LEN_LONG   = 4   # multi-chunk packet used for basic serving checks
PKT_LEN_SHORT  = 2   # short packet used for handoff/back-to-back checks
PKT_LEN_EXTRA  = 3   # used where contention/starvation needs 3 beats


async def reset_dut(dut):
    """Start the clock, reset, drive all inputs idle."""
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.rst_n.value          = 0
    dut.feed_valid.value     = 0
    dut.feed_sop.value       = 0
    dut.feed_eop.value       = 0
    dut.feed_data_flat.value = 0
    dut.feed_seq_flat.value  = 0
    dut.out_ready.value      = 1
    dut.fix_avail.value      = 0
    await ClockCycles(dut.clk, 3)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)


def drive_feeds(dut, *, valid=0, sop=0, eop=0, data=None, seq=None,
                out_ready=1, fix_avail=0):
    """Drive all inputs in one call. data/seq are per-feed lists."""
    dut.feed_valid.value = valid
    dut.feed_sop.value   = sop
    dut.feed_eop.value   = eop
    dut.out_ready.value  = out_ready
    dut.fix_avail.value  = fix_avail
    dflat, sflat = 0, 0
    for i in range(NUM_FEEDS):
        d = data[i] if data else 0
        s = seq[i]  if seq  else 0
        dflat |= (d & ((1 << DATA_W) - 1)) << (i * DATA_W)
        sflat |= (s & ((1 << SEQ_W) - 1))  << (i * SEQ_W)
    dut.feed_data_flat.value = dflat
    dut.feed_seq_flat.value  = sflat


def assert_skid_state(dut, feed, *, valid=1, data=None, seq=None, sop=None,
                      msg=""):
    """Check one feed's skid register contents directly. Call after
    ReadOnly(). Only compares fields that are given (not None)."""
    sv = int(dut.dut.skid_valid.value)
    got_valid = (sv >> feed) & 1
    assert got_valid == valid, \
        f"feed {feed}: skid_valid={got_valid}, expected {valid} {msg}"
    if data is not None:
        sd = int(dut.dut.skid_data[feed].value)
        assert sd == data, \
            f"feed {feed}: skid_data={hex(sd)}, expected {hex(data)} {msg}"
    if seq is not None:
        ss = int(dut.dut.skid_seq[feed].value)
        assert ss == seq, \
            f"feed {feed}: skid_seq={ss}, expected {seq} {msg}"
    if sop is not None:
        ssop = (int(dut.dut.skid_sop.value) >> feed) & 1
        assert ssop == sop, \
            f"feed {feed}: skid_sop={ssop}, expected {sop} {msg}"


class OutScoreboard:
    """Collects registered out_* beats and checks order + content.
    Also tracks in_ready per cycle for tests that need to verify a
    feed was never absorbed."""
    def __init__(self, dut):
        self.dut = dut
        self.got = []
        self.in_ready_hist = []   # list of int(dut.in_ready.value), per sample

    def sample(self):
        """Call after an edge (in ReadOnly) to record a beat if valid,
        and always record the current in_ready snapshot."""
        self.in_ready_hist.append(int(self.dut.in_ready.value))
        if int(self.dut.out_valid.value):
            self.got.append(dict(
                data=int(self.dut.out_data.value),
                seq=int(self.dut.out_seq.value),
                sop=int(self.dut.out_sop.value),
                eop=int(self.dut.out_eop.value),
                feed=int(self.dut.out_feed.value),
            ))

    def expect(self, beats):
        """beats: list of dicts with the same keys; exact order match."""
        assert len(self.got) == len(beats), \
            f"beat count: got {len(self.got)}, expected {len(beats)}\n" \
            f"got={self.got}\nexp={beats}"
        for k, (g, e) in enumerate(zip(self.got, beats)):
            for key in e:
                assert g[key] == e[key], \
                    f"beat {k} field '{key}': got {g[key]}, expected {e[key]}\n" \
                    f"got={g}\nexp={e}"

    def expect_in_ready_never(self, feed, from_sample=0, msg=""):
        """Assert in_ready[feed] was 0 for every sampled cycle from
        from_sample onward (skip earlier samples where it may have
        been legitimately 1, e.g. before a beat was first rejected)."""
        bad = [(i, v) for i, v in enumerate(self.in_ready_hist)
               if i >= from_sample and (v >> feed) & 1]
        assert not bad, \
            f"feed {feed}: expected in_ready=0 from sample {from_sample} " \
            f"onward {msg}, but was 1 at sample(s) {bad}"


async def run_packet(dut, sb, feed, seq, n_chunks, base_data):
    """Drive one full packet on `feed`, sampling the scoreboard each
    cycle. Chunk k carries data base_data+k. Returns expected beats."""
    exp = []
    for k in range(n_chunks):
        sop = 1 if k == 0 else 0
        eop = 1 if k == n_chunks - 1 else 0
        data = [0] * NUM_FEEDS
        data[feed] = base_data + k
        seqs = [0] * NUM_FEEDS
        seqs[feed] = seq
        drive_feeds(dut, valid=(1 << feed), sop=(sop << feed),
                    eop=(eop << feed), data=data, seq=seqs)
        await RisingEdge(dut.clk)
        await ReadOnly()
        sb.sample()
        exp.append(dict(data=base_data + k, seq=seq, sop=sop, eop=eop,
                        feed=feed))
        await Timer(1, unit="ns")   # leave ReadOnly before next drive
    # drop valid after the packet (no extra edge needed: out_stage_ff
    # is a single flop fed by comb logic, so the EOP beat already
    # registered on the edge crossed inside the loop above)
    drive_feeds(dut, valid=0)
    return exp