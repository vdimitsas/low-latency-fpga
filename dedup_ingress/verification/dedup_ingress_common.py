"""Shared helpers for the dedup_ingress testbenches.

The DUT is dedup_ingress_tb_wrap, which flattens dedup_ingress's packed two dimensional ports
into single vectors. Feed f lives in bits [f*W : (f+1)*W) of a flat vector.

dedup_ingress is cut-through, so its outputs are combinational functions of its inputs.
Every helper here therefore samples outputs late in the cycle, after the inputs
for that cycle have been driven and the logic has settled.
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

    def evaluate(self, in_valid, in_data, in_sop, out_ready, cmpl_valid, cmpl_seq):
        seq_sel = []
        for f in range(self.n_feeds):
            if in_sop[f]:
                seq_sel.append(seq_from_beat(in_data[f]))
            else:
                seq_sel.append(self.seq_regs[f])

        drop = []
        for f in range(self.n_feeds):
            hit_table = any(
                self.cpt_occupied[e] and self.cpt_seq[e] == seq_sel[f]
                for e in range(self.cpt_depth)
            )
            hit_bypass = bool(cmpl_valid) and (cmpl_seq & SEQ_MASK) == seq_sel[f]
            drop.append(1 if (in_valid[f] and (hit_table or hit_bypass)) else 0)

        return {
            "in_ready": list(out_ready),
            "out_valid": [
                1 if (in_valid[f] and not drop[f]) else 0 for f in range(self.n_feeds)
            ],
            "out_seq": seq_sel,
            "drop": drop,
        }

    def commit(self, in_valid, in_data, in_sop, out_ready, cmpl_valid, cmpl_seq):
        for f in range(self.n_feeds):
            if in_valid[f] and out_ready[f] and in_sop[f]:
                self.seq_regs[f] = seq_from_beat(in_data[f])

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
        """
        await Timer(DRIVE_DELAY_NS, "ns")
        self._apply()

        stim = dict(
            in_valid=list(self.in_valid),
            in_data=list(self.in_data),
            in_sop=list(self.in_sop),
            out_ready=list(self.out_ready),
            cmpl_valid=self.cmpl_valid,
            cmpl_seq=self.cmpl_seq,
        )
        expected = self.golden.evaluate(**stim)

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
                if stim["in_valid"][f]:
                    assert got["out_seq"][f] == expected["out_seq"][f], (
                        f"out_seq mismatch on feed {f}: got {got['out_seq'][f]:#x} "
                        f"expected {expected['out_seq'][f]:#x}"
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


async def cocotb_start_clock(dut):
    import cocotb

    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, "ns").start())
    await Timer(1, "ns")


async def send_packet(tb, feed, seq, beats, out_ready=None):
    """Stream a whole packet on one feed, returning per beat samples."""
    samples = []
    for i in range(beats):
        if out_ready is not None:
            tb.out_ready = list(out_ready)
        tb.present(
            feed,
            seq=seq if i == 0 else None,
            data=seq_into_beat(seq) if i == 0 else (0xA0 + i),
            sop=1 if i == 0 else 0,
            eop=1 if i == beats - 1 else 0,
        )
        samples.append(await tb.step())
    return samples
