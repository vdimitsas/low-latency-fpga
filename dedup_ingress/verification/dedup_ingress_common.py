"""Shared helpers for the dedup_ingress testbenches.

The DUT is dedup_ingress_tb_wrap, which flattens dedup_ingress's packed two dimensional ports
into single vectors. Feed f lives in bits [f*W : (f+1)*W) of a flat vector.

dedup_ingress has one pipeline stage. The comparator tree runs in the cycle a
beat is presented, and the beat plus its match results register together. The
outputs are combinational functions of that register, so a beat presented in
cycle N appears on the outputs in cycle N+1.

The ready path is still combinational: in_ready is driven by the registered
state and out_ready, so the upstream sees the decision in the same cycle.

Every helper here samples outputs late in the cycle, after the inputs for that
cycle have been driven and the logic has settled.
"""

import os

from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, Timer

# Must match the parameters the wrapper is elaborated with.
N_FEEDS = int(os.environ.get("N_FEEDS", 4))
DATA_W = int(os.environ.get("DATA_W", 64))
SEQ_W = int(os.environ.get("SEQ_W", 32))
SEQ_OFFSET = int(os.environ.get("SEQ_OFFSET", 0))
CPT_DEPTH = int(os.environ.get("CPT_DEPTH", 8))

CLK_PERIOD_NS = 10
DRIVE_DELAY_NS = 1
SAMPLE_DELAY_NS = 8

# Cycles between a beat being accepted and it appearing on the outputs.
LATENCY = 1

SEQ_MASK = (1 << SEQ_W) - 1
DATA_MASK = (1 << DATA_W) - 1


def seq_into_beat(seq, filler=0):
    """Build a first beat carrying `seq` at the parameterised offset."""
    beat = filler & DATA_MASK
    field = ((1 << SEQ_W) - 1) << (SEQ_OFFSET * 8)
    beat &= ~field & DATA_MASK
    beat |= (seq & SEQ_MASK) << (SEQ_OFFSET * 8)
    return beat & DATA_MASK


def seq_from_beat(beat):
    """Slice the seq field back out of a beat, mirroring the RTL."""
    return (beat >> (SEQ_OFFSET * 8)) & SEQ_MASK


def _pack(values, width):
    word = 0
    for i, v in enumerate(values):
        word |= (v & ((1 << width) - 1)) << (i * width)
    return word


def _unpack(word, width, count):
    mask = (1 << width) - 1
    return [(word >> (i * width)) & mask for i in range(count)]


def _bits(word, count):
    return [(word >> i) & 1 for i in range(count)]


class GoldenDedupIngress:
    """Reference model of dedup_ingress, cycle accurate.

    Call `evaluate` with the inputs presented during a cycle to get that
    cycle's outputs, then `commit` to advance the state across the clock edge.

    State held across the edge:

      cpt_seq, cpt_occupied, cpt_wr_ptr   the completed packets table
      seq_regs                            per feed sequence context
      valid_q, data_q, sop_q, eop_q, seq_q  the registered beat
      cpt_match_q, bypass_match_q           the registered comparator results
    """

    def __init__(self, n_feeds=N_FEEDS, cpt_depth=CPT_DEPTH):
        self.n_feeds = n_feeds
        self.cpt_depth = cpt_depth
        self.reset()

    def reset(self):
        self.cpt_seq = [0] * self.cpt_depth
        self.cpt_occupied = [0] * self.cpt_depth
        self.cpt_wr_ptr = 0
        self.seq_regs = [0] * self.n_feeds

        self.valid_q = [0] * self.n_feeds
        self.data_q = [0] * self.n_feeds
        self.sop_q = [0] * self.n_feeds
        self.eop_q = [0] * self.n_feeds
        self.seq_q = [0] * self.n_feeds
        self.cpt_match_q = [0] * self.n_feeds
        self.bypass_match_q = [0] * self.n_feeds

    # -- combinational, this cycle's inputs ---------------------------------

    def _seq_sel(self, in_data, in_sop):
        """The seq compared this cycle, before the register."""
        out = []
        for f in range(self.n_feeds):
            if in_sop[f]:
                out.append(seq_from_beat(in_data[f]))
            else:
                out.append(self.seq_regs[f])
        return out

    def _matches(self, in_data, in_sop, cmpl_valid, cmpl_seq):
        """Comparator results this cycle, against the current table."""
        seq_sel = self._seq_sel(in_data, in_sop)

        cpt_match = []
        bypass_match = []
        for f in range(self.n_feeds):
            cpt_match.append(
                1
                if any(
                    self.cpt_occupied[e] and self.cpt_seq[e] == seq_sel[f]
                    for e in range(self.cpt_depth)
                )
                else 0
            )
            bypass_match.append(
                1
                if (bool(cmpl_valid) and (cmpl_seq & SEQ_MASK) == seq_sel[f])
                else 0
            )
        return seq_sel, cpt_match, bypass_match

    # -- combinational, from the registered state ---------------------------

    def _drop(self):
        return [
            1
            if (self.valid_q[f] and (self.cpt_match_q[f] or self.bypass_match_q[f]))
            else 0
            for f in range(self.n_feeds)
        ]

    def evaluate(self, in_valid, in_data, in_sop, out_ready, cmpl_valid, cmpl_seq):
        """Outputs presented during this cycle.

        Everything here comes from the registered state. The inputs of this
        cycle affect the outputs of the next one, apart from out_ready which
        feeds the ready path combinationally.
        """
        drop = self._drop()

        return {
            "in_ready": [
                1 if ((not self.valid_q[f]) or out_ready[f] or drop[f]) else 0
                for f in range(self.n_feeds)
            ],
            "out_valid": [
                1 if (self.valid_q[f] and not drop[f]) else 0
                for f in range(self.n_feeds)
            ],
            "out_data": list(self.data_q),
            "out_sop": list(self.sop_q),
            "out_eop": list(self.eop_q),
            "out_seq": list(self.seq_q),
            "drop": drop,
        }

    def commit(self, in_valid, in_data, in_sop, in_eop, out_ready, cmpl_valid, cmpl_seq):
        """Advance the state across the clock edge."""
        drop = self._drop()
        in_ready = [
            1 if ((not self.valid_q[f]) or out_ready[f] or drop[f]) else 0
            for f in range(self.n_feeds)
        ]

        seq_sel, cpt_match, bypass_match = self._matches(
            in_data, in_sop, cmpl_valid, cmpl_seq
        )

        # sequence context: written on any valid SOP beat, no in_ready term
        for f in range(self.n_feeds):
            if in_valid[f] and in_sop[f]:
                self.seq_regs[f] = seq_from_beat(in_data[f])

        # the cut: valid follows in_ready alone, payload follows the handshake
        for f in range(self.n_feeds):
            if in_ready[f]:
                self.valid_q[f] = 1 if in_valid[f] else 0

            if in_valid[f] and in_ready[f]:
                self.data_q[f] = in_data[f] & DATA_MASK
                self.sop_q[f] = in_sop[f]
                self.eop_q[f] = in_eop[f]
                self.seq_q[f] = seq_sel[f]
                self.cpt_match_q[f] = cpt_match[f]
                self.bypass_match_q[f] = bypass_match[f]

        # completed packets table, suppressed when the value is already held
        if cmpl_valid:
            seq = cmpl_seq & SEQ_MASK
            already_held = any(
                self.cpt_occupied[e] and self.cpt_seq[e] == seq
                for e in range(self.cpt_depth)
            )
            if not already_held:
                self.cpt_seq[self.cpt_wr_ptr] = seq
                self.cpt_occupied[self.cpt_wr_ptr] = 1
                self.cpt_wr_ptr = (self.cpt_wr_ptr + 1) % self.cpt_depth


class DedupIngressTB:
    """Drives dedup_ingress_tb_wrap one cycle at a time."""

    def __init__(self, dut):
        self.dut = dut
        self.n_feeds = N_FEEDS
        self.golden = GoldenDedupIngress()
        self.clear()

    def clear(self):
        self.in_valid = [0] * self.n_feeds
        self.in_data = [0] * self.n_feeds
        self.in_sop = [0] * self.n_feeds
        self.in_eop = [0] * self.n_feeds
        self.out_ready = [1] * self.n_feeds
        self.cmpl_valid = 0
        self.cmpl_seq = 0

    def present(self, feed, seq=None, data=None, sop=0, eop=0, valid=1):
        """Stage one feed's stimulus for the coming cycle."""
        if seq is not None and data is None:
            data = seq_into_beat(seq)
        self.in_valid[feed] = valid
        self.in_data[feed] = 0 if data is None else data
        self.in_sop[feed] = sop
        self.in_eop[feed] = eop

    def idle_feeds(self):
        for f in range(self.n_feeds):
            self.in_valid[f] = 0
            self.in_sop[f] = 0
            self.in_eop[f] = 0

    def complete(self, seq):
        self.cmpl_valid = 1
        self.cmpl_seq = seq & SEQ_MASK

    def _apply(self):
        d = self.dut
        d.in_valid.value = _pack(self.in_valid, 1)
        d.in_sop.value = _pack(self.in_sop, 1)
        d.in_eop.value = _pack(self.in_eop, 1)
        d.in_data_flat.value = _pack(self.in_data, DATA_W)
        d.out_ready.value = _pack(self.out_ready, 1)
        d.cmpl_valid.value = self.cmpl_valid
        d.cmpl_seq.value = self.cmpl_seq

    def sample(self):
        d = self.dut
        return {
            "in_ready": _bits(int(d.in_ready.value), self.n_feeds),
            "out_valid": _bits(int(d.out_valid.value), self.n_feeds),
            "out_sop": _bits(int(d.out_sop.value), self.n_feeds),
            "out_eop": _bits(int(d.out_eop.value), self.n_feeds),
            "out_data": _unpack(int(d.out_data_flat.value), DATA_W, self.n_feeds),
            "out_seq": _unpack(int(d.out_seq_flat.value), SEQ_W, self.n_feeds),
        }

    async def start(self):
        """Start the clock and hold reset for a few cycles."""
        await cocotb_start_clock(self.dut)
        self.clear()
        self.dut.rst_n.value = 0
        self._apply()
        await Timer(CLK_PERIOD_NS * 5, "ns")
        self.dut.rst_n.value = 1
        await RisingEdge(self.dut.clk)
        self.golden.reset()

    async def step(self, check=True):
        """Run one cycle: drive the staged stimulus, settle, sample, advance.

        Returns the sampled outputs for that cycle. The stimulus is cleared
        afterwards so each cycle must be staged explicitly.

        The outputs sampled here belong to the beat accepted on the previous
        edge, not to the stimulus staged for this cycle.
        """
        await Timer(DRIVE_DELAY_NS, "ns")
        self._apply()

        stim = dict(
            in_valid=list(self.in_valid),
            in_data=list(self.in_data),
            in_sop=list(self.in_sop),
            in_eop=list(self.in_eop),
            out_ready=list(self.out_ready),
            cmpl_valid=self.cmpl_valid,
            cmpl_seq=self.cmpl_seq,
        )
        expected = self.golden.evaluate(
            in_valid=stim["in_valid"],
            in_data=stim["in_data"],
            in_sop=stim["in_sop"],
            out_ready=stim["out_ready"],
            cmpl_valid=stim["cmpl_valid"],
            cmpl_seq=stim["cmpl_seq"],
        )

        await Timer(SAMPLE_DELAY_NS, "ns")
        got = self.sample()

        if check:
            assert got["out_valid"] == expected["out_valid"], (
                f"out_valid mismatch: got {got['out_valid']} "
                f"expected {expected['out_valid']}"
            )
            assert got["in_ready"] == expected["in_ready"], (
                f"in_ready mismatch: got {got['in_ready']} "
                f"expected {expected['in_ready']}"
            )
            for f in range(self.n_feeds):
                if expected["out_valid"][f]:
                    assert got["out_seq"][f] == expected["out_seq"][f], (
                        f"out_seq mismatch on feed {f}: got {got['out_seq'][f]:#x} "
                        f"expected {expected['out_seq'][f]:#x}"
                    )
                    assert got["out_data"][f] == expected["out_data"][f], (
                        f"out_data mismatch on feed {f}: got {got['out_data'][f]:#x} "
                        f"expected {expected['out_data'][f]:#x}"
                    )
                    assert got["out_sop"][f] == expected["out_sop"][f], (
                        f"out_sop mismatch on feed {f}: got {got['out_sop'][f]} "
                        f"expected {expected['out_sop'][f]}"
                    )
                    assert got["out_eop"][f] == expected["out_eop"][f], (
                        f"out_eop mismatch on feed {f}: got {got['out_eop'][f]} "
                        f"expected {expected['out_eop'][f]}"
                    )

        await RisingEdge(self.dut.clk)
        self.golden.commit(**stim)

        self.cmpl_valid = 0
        self.cmpl_seq = 0
        self.idle_feeds()

        return got

    async def idle(self, cycles=1):
        for _ in range(cycles):
            await self.step()

    async def drain(self, cycles=LATENCY):
        """Run enough idle cycles for a beat in flight to reach the outputs."""
        for _ in range(cycles):
            await self.step()


async def cocotb_start_clock(dut):
    import cocotb

    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, "ns").start())
    await Timer(1, "ns")


async def send_packet(tb, feed, seq, beats, out_ready=None):
    """Stream a whole packet on one feed, returning per beat samples.

    A beat is re-presented until in_ready is high on that feed, so the packet
    survives backpressure.

    The returned list is beat aligned: entry i is the cycle in which beat i
    appeared on the outputs, which is LATENCY cycles after it was accepted.
    Enough trailing idle cycles are run to drain the last beat out.
    """
    samples = []
    accept_at = []

    for i in range(beats):
        while True:
            if out_ready is not None:
                tb.out_ready = list(out_ready)
            tb.present(
                feed,
                seq=seq if i == 0 else None,
                data=seq_into_beat(seq) if i == 0 else (0xA0 + i),
                sop=1 if i == 0 else 0,
                eop=1 if i == beats - 1 else 0,
            )
            accepted = tb.golden.evaluate(
                in_valid=list(tb.in_valid),
                in_data=list(tb.in_data),
                in_sop=list(tb.in_sop),
                out_ready=list(tb.out_ready),
                cmpl_valid=tb.cmpl_valid,
                cmpl_seq=tb.cmpl_seq,
            )["in_ready"][feed]
            here = len(samples)
            samples.append(await tb.step())
            if accepted:
                accept_at.append(here)
                break

    for _ in range(LATENCY):
        if out_ready is not None:
            tb.out_ready = list(out_ready)
        samples.append(await tb.step())

    return [samples[k + LATENCY] for k in accept_at]
