# ===============================================================
# test_random.py — constrained-random, full golden model, current
# market_line_arbiter (atomicity + release-one-cycle-after-confirm design).
#
# Lessons encoded from directed-test debugging:
#  1. serve_feed_q resets to 0 (not feed-0 one-hot) — no reset bias.
#     sticky requires |serve_feed_q, so the very first pick is fully
#     unbiased fix/lowest-index selection, no "index race" artifact.
#  2. sticky = |serve_feed_q && !serve_eop_q &&
#              !(|(invalidate_feed_q & ~eff_valid))
#     True atomicity: holds through the ENTIRE confirm cycle (so
#     invalidate_feed's live check still matches serve_feed and
#     fires correctly), releases exactly ONE cycle after confirm
#     (via invalidate_feed_q), and only if that feed is STILL empty
#     then. A momentary data gap alone never releases the feed.
#  3. invalidate_feed = out_ready && |invalidate_q && !serve_valid_c
#     — serve_feed is NOT part of this condition: serve_valid_c is
#     already masked by serve_feed, so re-masking is redundant.
#  4. When !sticky and NOTHING gets picked (no fix, no valid feed
#     anywhere), serve_feed holds at serve_feed_q UNCHANGED (not 0).
#     If that feed stays the only thing ever pointed at, the hiccup
#     countdown simply restarts on it — a legitimate steady state,
#     not a bug.
#  5. hiccup_cnt and invalidate_q only update when out_ready is
#     high (freeze during a downstream stall, don't advance or
#     clear). invalidate_feed itself is gated by out_ready directly.
#     invalidate_feed_q is NOT re-gated — it must faithfully record
#     invalidate_feed's own history, which already accounts for
#     out_ready once.
#  6. Stage1->2 flop: serve_valid_q/sop_q/eop_q are gated by
#     out_ready (an undelivered beat must not overwrite the flop
#     with a fresh claim); serve_seq_q/data_q are NOT gated — they
#     just carry whatever payload was last computed.
#  7. Skid has three real branches: empty->capture; occupied+served
#     with new data available->refill in place; occupied+served
#     with nothing new->drain to empty; occupied+not-served->hold.
#  8. in_ready is purely combinational: current (this-cycle)
#     skid_valid, LIVE serve_feed, LIVE out_ready. No lag anywhere.
#  9. Fix preference applies ONLY at the exact cycle sticky is
#     false — identical rule to normal picking (fix first, then
#     lowest eff_valid index).
# ===============================================================
import random
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, Timer, ReadOnly
from arb_common import NUM_FEEDS, HICCUP_CYCLES, DATA_W, SEQ_W, FULL

ARM_CNT = HICCUP_CYCLES - 3


def bit(vec, i):
    return (vec >> i) & 1


def encode(onehot):
    for i in range(NUM_FEEDS):
        if bit(onehot, i):
            return i
    return 0


# ---------------------------------------------------------------
# Stimulus: one legal-shape (not necessarily "polite") random
# snapshot of all inputs, per cycle.
# ---------------------------------------------------------------
class Stim:
    def __init__(self):
        self.feed_valid = 0
        self.feed_sop   = 0
        self.feed_eop   = 0
        self.feed_data  = [0] * NUM_FEEDS
        self.feed_seq   = [0] * NUM_FEEDS
        self.out_ready  = 1
        self.fix_avail  = 0

    @staticmethod
    def random():
        s = Stim()
        for i in range(NUM_FEEDS):
            if random.random() < 0.55:
                s.feed_valid |= (1 << i)
            if random.random() < 0.35:
                s.feed_sop |= (1 << i)
            if random.random() < 0.35:
                s.feed_eop |= (1 << i)
            s.feed_data[i] = random.randint(0, (1 << DATA_W) - 1)
            s.feed_seq[i]  = random.randint(0, (1 << SEQ_W) - 1)
            if random.random() < 0.25:
                s.fix_avail |= (1 << i)
        s.out_ready = 1 if random.random() < 0.7 else 0
        return s


def drive(dut, s):
    dut.feed_valid.value = s.feed_valid
    dut.feed_sop.value   = s.feed_sop
    dut.feed_eop.value   = s.feed_eop
    dut.out_ready.value  = s.out_ready
    dut.fix_avail.value  = s.fix_avail
    dflat, sflat = 0, 0
    for i in range(NUM_FEEDS):
        dflat |= (s.feed_data[i] & ((1 << DATA_W) - 1)) << (i * DATA_W)
        sflat |= (s.feed_seq[i]  & ((1 << SEQ_W) - 1))  << (i * SEQ_W)
    dut.feed_data_flat.value = dflat
    dut.feed_seq_flat.value  = sflat


# ---------------------------------------------------------------
# Golden model — mirrors the current RTL block-for-block.
# ---------------------------------------------------------------
class Golden:
    def __init__(self):
        self.serve_feed_q  = 0
        self.serve_valid_q = 0
        self.serve_sop_q   = 0
        self.serve_eop_q   = 0
        self.serve_seq_q   = 0
        self.serve_data_q  = 0
        self.skid_valid = 0
        self.skid_data  = [0] * NUM_FEEDS
        self.skid_sop   = 0
        self.skid_eop   = 0
        self.skid_seq   = [0] * NUM_FEEDS
        self.hiccup_cnt      = 0
        self.invalidate_q      = 0   # per-feed, one-hot when armed
        self.invalidate_feed_q = 0   # per-feed, faithful 1-cycle delay

    def step(self, s):
        NF = NUM_FEEDS

        # ---- eff_view ----
        eff_valid = [0] * NF
        eff_sop   = [0] * NF
        eff_eop   = [0] * NF
        eff_seq   = [0] * NF
        eff_data  = [0] * NF
        for i in range(NF):
            if bit(self.skid_valid, i):
                eff_valid[i] = 1
                eff_sop[i]   = bit(self.skid_sop, i)
                eff_eop[i]   = bit(self.skid_eop, i)
                eff_seq[i]   = self.skid_seq[i]
                eff_data[i]  = self.skid_data[i]
            else:
                eff_valid[i] = bit(s.feed_valid, i)
                eff_sop[i]   = bit(s.feed_sop, i)
                eff_eop[i]   = bit(s.feed_eop, i)
                eff_seq[i]   = s.feed_seq[i]
                eff_data[i]  = s.feed_data[i]

        # ---- serve_mux ----
        confirmed_dead = 0
        for i in range(NF):
            if bit(self.invalidate_feed_q, i) and not eff_valid[i]:
                confirmed_dead = 1

        sticky = (self.serve_feed_q != 0) and (not self.serve_eop_q) \
                 and (not confirmed_dead)

        serve_feed = self.serve_feed_q
        picked = False
        if not sticky:
            for i in range(NF):
                if not picked and bit(s.fix_avail, i):
                    serve_feed = (1 << i)
                    picked = True
            if not picked:
                for i in range(NF):
                    if not picked and eff_valid[i]:
                        serve_feed = (1 << i)
                        picked = True
            # else: nothing found -> serve_feed stays serve_feed_q
            # unchanged (lesson 4) — NOT reset to 0

        serve_valid_c = 1 if (serve_feed & sum(eff_valid[i] << i
                              for i in range(NF))) else 0
        serve_sop_c = 1 if (serve_feed & sum(eff_sop[i] << i
                            for i in range(NF))) else 0
        serve_eop_c = 1 if (serve_feed & sum(eff_eop[i] << i
                            for i in range(NF))) else 0
        serve_seq_c  = 0
        serve_data_c = 0
        for i in range(NF):
            if bit(serve_feed, i):
                serve_seq_c  = eff_seq[i]
                serve_data_c = eff_data[i]

        # ---- in_ready (combinational, current skid_valid, live
        #      serve_feed/out_ready) ----
        in_ready = 0
        for i in range(NF):
            ready_i = (not bit(self.skid_valid, i)) or \
                      (bit(serve_feed, i) and s.out_ready)
            if ready_i:
                in_ready |= (1 << i)

        # ---- skid_upd: three real branches ----
        skid_valid_nxt = self.skid_valid
        skid_data_nxt  = list(self.skid_data)
        skid_sop_nxt   = self.skid_sop
        skid_eop_nxt   = self.skid_eop
        skid_seq_nxt   = list(self.skid_seq)

        for i in range(NF):
            occupied   = bit(self.skid_valid, i)
            served_now = bit(serve_feed, i) and s.out_ready
            if occupied:
                if served_now:
                    if bit(s.feed_valid, i) and bit(in_ready, i):
                        skid_data_nxt[i] = s.feed_data[i]
                        if bit(s.feed_sop, i):
                            skid_sop_nxt |= (1 << i)
                        else:
                            skid_sop_nxt &= ~(1 << i)
                        if bit(s.feed_eop, i):
                            skid_eop_nxt |= (1 << i)
                        else:
                            skid_eop_nxt &= ~(1 << i)
                        skid_seq_nxt[i] = s.feed_seq[i]
                    else:
                        skid_valid_nxt &= ~(1 << i)
                # else: occupied, not served -> hold (default)
            else:
                if bit(s.feed_valid, i) and not served_now:
                    skid_valid_nxt |= (1 << i)
                    skid_data_nxt[i] = s.feed_data[i]
                    if bit(s.feed_sop, i):
                        skid_sop_nxt |= (1 << i)
                    else:
                        skid_sop_nxt &= ~(1 << i)
                    if bit(s.feed_eop, i):
                        skid_eop_nxt |= (1 << i)
                    else:
                        skid_eop_nxt &= ~(1 << i)
                    skid_seq_nxt[i] = s.feed_seq[i]

        skid_valid_nxt &= FULL
        skid_sop_nxt   &= FULL
        skid_eop_nxt   &= FULL

        # ---- Stage 2: giveup / hiccup_cnt (out_ready gates the
        #      register, not the next-state logic) ----
        giveup = 1 if (self.hiccup_cnt == HICCUP_CYCLES - 1 and
                       self.serve_feed_q != 0 and
                       not self.serve_valid_q) else 0

        if self.serve_feed_q != 0:
            if self.serve_valid_q or giveup:
                hiccup_cnt_nxt = 0
            else:
                hiccup_cnt_nxt = self.hiccup_cnt + 1
        else:
            hiccup_cnt_nxt = 0

        hiccup_cnt_committed = hiccup_cnt_nxt if s.out_ready \
                               else self.hiccup_cnt

        # ---- invalidate_q: armed with serve_feed_q, out_ready-gated.
        #      Gated with !serve_valid_q so the arm point (ARM_CNT) can
        #      only latch during genuine silence, not on the initial
        #      valid serve — matches the RTL fix that removes the
        #      spurious first arm at the HICCUP_CYCLES=3 boundary.
        if s.out_ready:
            invalidate_q_nxt = self.serve_feed_q \
                               if (self.hiccup_cnt == ARM_CNT
                                   and not self.serve_valid_q) else 0
        else:
            invalidate_q_nxt = self.invalidate_q

        # ---- invalidate_feed: live, out_ready-gated, no serve_feed
        #      term (redundant — serve_valid_c already masks it)
        invalidate_feed = 0
        if s.out_ready and self.invalidate_q != 0 and not serve_valid_c:
            invalidate_feed = self.invalidate_q

        # ---- invalidate_feed_q: faithful record, NOT re-gated
        invalidate_feed_q_nxt = invalidate_feed

        # ---- OUTPUT (registered, visible THIS cycle) ----
        out_valid = self.serve_valid_q
        out_data  = self.serve_data_q
        out_seq   = self.serve_seq_q
        out_sop   = self.serve_sop_q
        out_eop   = self.serve_eop_q
        out_feed  = encode(self.serve_feed_q)

        fix_served_feed  = out_feed
        fix_served_valid = 1 if (self.serve_valid_q and
                                 (self.serve_feed_q & s.fix_avail)) else 0

        # ---- stash next-state for commit() ----
        self._nxt = dict(
            serve_feed_q  = serve_feed,
            serve_valid_q = 1 if (serve_valid_c and s.out_ready) else 0,
            serve_sop_q   = 1 if (serve_sop_c and s.out_ready) else 0,
            serve_eop_q   = 1 if (serve_eop_c and s.out_ready) else 0,
            serve_seq_q   = serve_seq_c,
            serve_data_q  = serve_data_c,
            skid_valid = skid_valid_nxt, skid_data = skid_data_nxt,
            skid_sop = skid_sop_nxt, skid_eop = skid_eop_nxt,
            skid_seq = skid_seq_nxt,
            hiccup_cnt = hiccup_cnt_committed,
            invalidate_q = invalidate_q_nxt,
            invalidate_feed_q = invalidate_feed_q_nxt,
        )

        return dict(
            in_ready=in_ready, invalidate_feed=invalidate_feed,
            out_valid=out_valid, out_data=out_data, out_seq=out_seq,
            out_sop=out_sop, out_eop=out_eop, out_feed=out_feed,
            fix_served_valid=fix_served_valid,
            fix_served_feed=fix_served_feed,
        )

    def commit(self):
        n = self._nxt
        self.serve_feed_q  = n['serve_feed_q']
        self.serve_valid_q = n['serve_valid_q']
        self.serve_sop_q   = n['serve_sop_q']
        self.serve_eop_q   = n['serve_eop_q']
        self.serve_seq_q   = n['serve_seq_q']
        self.serve_data_q  = n['serve_data_q']
        self.skid_valid = n['skid_valid']
        self.skid_data  = n['skid_data']
        self.skid_sop   = n['skid_sop']
        self.skid_eop   = n['skid_eop']
        self.skid_seq   = n['skid_seq']
        self.hiccup_cnt = n['hiccup_cnt']
        self.invalidate_q      = n['invalidate_q']
        self.invalidate_feed_q = n['invalidate_feed_q']


# ---------------------------------------------------------------
# The random test.
# ---------------------------------------------------------------
@cocotb.test()
async def test_random_full_golden(dut):
    """Constrained-random fuzzing checked against a full golden model."""
    N_CYCLES = 3000
    random.seed(20260723)

    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())

    dut.rst_n.value = 0
    drive(dut, Stim())
    await ClockCycles(dut.clk, 3)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)

    model = Golden()
    mismatches = 0

    for cyc in range(N_CYCLES):
        s = Stim.random()
        drive(dut, s)
        exp = model.step(s)

        await Timer(1, unit="ns")
        await ReadOnly()

        got = dict(
            in_ready=int(dut.in_ready.value),
            invalidate_feed=int(dut.invalidate_feed.value),
            out_valid=int(dut.out_valid.value),
            out_data=int(dut.out_data.value),
            out_seq=int(dut.out_seq.value),
            out_sop=int(dut.out_sop.value),
            out_eop=int(dut.out_eop.value),
            out_feed=int(dut.out_feed.value),
            fix_served_valid=int(dut.fix_served_valid.value),
            fix_served_feed=int(dut.fix_served_feed.value),
        )

        keys = ["in_ready", "invalidate_feed", "fix_served_valid"]
        if exp["out_valid"]:
            keys += ["out_valid", "out_data", "out_seq", "out_sop",
                     "out_eop", "out_feed"]
        else:
            keys += ["out_valid"]
        if exp["fix_served_valid"]:
            keys.append("fix_served_feed")

        bad = [k for k in keys if got[k] != exp[k]]
        if bad:
            mismatches += 1
            if mismatches <= 10:
                dut._log.error(f"cycle {cyc} MISMATCH on {bad}")
                dut._log.error(f"  got={got}")
                dut._log.error(f"  exp={exp}")
                dut._log.error(
                    f"  stim: valid={s.feed_valid:04b} sop={s.feed_sop:04b} "
                    f"eop={s.feed_eop:04b} out_rdy={s.out_ready} "
                    f"fix={s.fix_avail:04b}")
                dut._log.error(
                    f"  model(pre): serve_feed_q={model.serve_feed_q:04b} "
                    f"serve_valid_q={model.serve_valid_q} "
                    f"serve_eop_q={model.serve_eop_q} "
                    f"skid_valid={model.skid_valid:04b} "
                    f"hiccup_cnt={model.hiccup_cnt} "
                    f"invalidate_q={model.invalidate_q:04b} "
                    f"invalidate_feed_q={model.invalidate_feed_q:04b}")

        await RisingEdge(dut.clk)
        model.commit()
        await Timer(1, unit="ns")

    assert mismatches == 0, f"{mismatches} mismatches over {N_CYCLES} cycles"
    dut._log.info(f"random golden test passed: {N_CYCLES} cycles, "
                  f"0 mismatches")