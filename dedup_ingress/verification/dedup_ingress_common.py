"""Shared helpers for the dedup_ingress testbenches.

The DUT is dedup_ingress_tb_wrap, which flattens dedup_ingress's packed two
dimensional ports into single vectors. Feed f lives in bits [f*W : (f+1)*W) of
a flat vector.

dedup_ingress has one pipeline stage. The comparator tree runs in the cycle a
beat is presented, and the beat plus its match results register together. The
outputs are combinational functions of that register.

step drives the inputs to the DUT and the model at the same point, then waits
for the clock edge and reads at the ReadOnly phase, once the simulator has
settled. So the outputs it returns belong to the beat driven that cycle, and
in_ready belongs to the cycle that has just started.
"""

import os

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import NextTimeStep, ReadOnly, RisingEdge

# Must match the parameters the wrapper is elaborated with.
N_FEEDS = int(os.environ.get("N_FEEDS", 4))
DATA_W = int(os.environ.get("DATA_W", 64))
SEQ_W = int(os.environ.get("SEQ_W", 32))
SEQ_OFFSET = int(os.environ.get("SEQ_OFFSET", 0))
CPT_DEPTH = int(os.environ.get("CPT_DEPTH", 8))

CLK_PERIOD_NS = 10

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

    Call `drive` with this cycle's inputs, then `evaluate`. `evaluate` moves
    the state across the clock edge and returns the outputs after it, so the
    values it returns belong to the beat just driven.

    State held across the edge:

      cpt_seq, cpt_occupied, cpt_wr_ptr     the completed packets table
      seq_regs                              per feed sequence context
      valid_q, data_q, sop_q, eop_q, seq_q  the registered beat
      cpt_match_q, bypass_match_q           the registered match results
    """

    def __init__(self, n_feeds=N_FEEDS, cpt_depth=CPT_DEPTH):
        self.n_feeds = n_feeds
        self.cpt_depth = cpt_depth
        self.reset()

    def reset(self):
        # this cycle's inputs, put here by drive
        self.in_valid = [0] * self.n_feeds
        self.in_data = [0] * self.n_feeds
        self.in_sop = [0] * self.n_feeds
        self.in_eop = [0] * self.n_feeds
        self.out_ready = [0] * self.n_feeds
        self.cmpl_valid = 0
        self.cmpl_seq = 0

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

    def drive(self, in_valid, in_data, in_sop, in_eop, out_ready,
              cmpl_valid, cmpl_seq):
        """Put this cycle's inputs on the model, the way wires hold them."""
        self.in_valid = list(in_valid)
        self.in_data = [d & DATA_MASK for d in in_data]
        self.in_sop = list(in_sop)
        self.in_eop = list(in_eop)
        self.out_ready = list(out_ready)
        self.cmpl_valid = cmpl_valid
        self.cmpl_seq = cmpl_seq & SEQ_MASK

    # -- combinational, from this cycle's inputs -----------------------------

    def _seq_sel(self):
        """The seq compared this cycle, before the register."""
        out = []
        for f in range(self.n_feeds):
            if self.in_sop[f]:
                out.append(seq_from_beat(self.in_data[f]))
            else:
                out.append(self.seq_regs[f])
        return out

    def _matches(self):
        """Comparator results this cycle, against the table as it stands now."""
        seq_sel = self._seq_sel()

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
                if (self.cmpl_valid and self.cmpl_seq == seq_sel[f])
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

    def _in_ready(self):
        drop = self._drop()
        return [
            1 if ((not self.valid_q[f]) or self.out_ready[f] or drop[f]) else 0
            for f in range(self.n_feeds)
        ]

    def evaluate(self):
        """Move across the clock edge, then report the outputs after it.

        in_ready, the sequence context and the comparator results are
        combinational, so they are computed from the flops and the table before
        either is written. The flops then load, and the outputs are read from
        them. So the returned outputs belong to the beat just driven, and
        in_ready belongs to the cycle that starts after the edge.
        """
        in_ready = self._in_ready()
        seq_sel, cpt_match, bypass_match = self._matches()

        # sequence context: written on any valid SOP beat, no in_ready term
        for f in range(self.n_feeds):
            if self.in_valid[f] and self.in_sop[f]:
                self.seq_regs[f] = seq_from_beat(self.in_data[f])

        # the cut: valid follows in_ready alone, payload follows the handshake
        for f in range(self.n_feeds):
            if in_ready[f]:
                self.valid_q[f] = 1 if self.in_valid[f] else 0

            if self.in_valid[f] and in_ready[f]:
                self.data_q[f] = self.in_data[f]
                self.sop_q[f] = self.in_sop[f]
                self.eop_q[f] = self.in_eop[f]
                self.seq_q[f] = seq_sel[f]
                self.cpt_match_q[f] = cpt_match[f]
                self.bypass_match_q[f] = bypass_match[f]

        # completed packets table, suppressed when the value is already held
        if self.cmpl_valid:
            already_held = any(
                self.cpt_occupied[e] and self.cpt_seq[e] == self.cmpl_seq
                for e in range(self.cpt_depth)
            )
            if not already_held:
                self.cpt_seq[self.cpt_wr_ptr] = self.cmpl_seq
                self.cpt_occupied[self.cpt_wr_ptr] = 1
                self.cpt_wr_ptr = (self.cpt_wr_ptr + 1) % self.cpt_depth

        drop = self._drop()

        return {
            "in_ready": self._in_ready(),
            "out_valid": [
                1 if (self.valid_q[f] and not drop[f]) else 0
                for f in range(self.n_feeds)
            ],
            "out_data": list(self.data_q),
            "out_sop": list(self.sop_q),
            "out_eop": list(self.eop_q),
            "out_seq": list(self.seq_q),
        }


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

    def present(self, feed, seq=None, data=None, sop=0, eop=0):
        """Stage one feed's stimulus for the coming cycle."""
        if seq is not None and data is None:
            data = seq_into_beat(seq)
        self.in_valid[feed] = 1
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
        cocotb.start_soon(Clock(self.dut.clk, CLK_PERIOD_NS, "ns").start())

        self.clear()
        self.dut.rst_n.value = 0
        self._apply()

        for _ in range(5):
            await RisingEdge(self.dut.clk)

        self.dut.rst_n.value = 1
        await RisingEdge(self.dut.clk)
        self.golden.reset()

    async def step(self):
        """Run one cycle and return the DUT outputs read after the edge.

        The inputs reach the DUT and the model at the same point. The read
        happens after the clock edge, so out_data and the rest show the beat
        driven this cycle, and in_ready is the value for the cycle that has
        just started.
        """
        self._apply()
        self.golden.drive(
            in_valid=self.in_valid,
            in_data=self.in_data,
            in_sop=self.in_sop,
            in_eop=self.in_eop,
            out_ready=self.out_ready,
            cmpl_valid=self.cmpl_valid,
            cmpl_seq=self.cmpl_seq,
        )
        expected = self.golden.evaluate()

        await RisingEdge(self.dut.clk)
        await ReadOnly()
        got = self.sample()

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

        self.cmpl_valid = 0
        self.cmpl_seq = 0
        self.idle_feeds()

        # leave the read only region, otherwise the next call cannot drive
        await NextTimeStep()

        return got

    async def idle(self, cycles=1):
        for _ in range(cycles):
            await self.step()


async def send_packet(tb, feed, seq, beats):
    """Stream a whole packet on one feed, returning one sample per beat.

    in_ready is read after the edge, so it is the value for the coming cycle.
    It is held and used on the next pass to decide whether the beat that was
    just driven was taken. A refused beat is presented again.
    """
    samples = []
    in_ready = 1          # the stage starts empty, so the first beat is taken
    i = 0

    while i < beats:
        tb.present(
            feed,
            data=seq_into_beat(seq) if i == 0 else (0xA0 + i),
            sop=1 if i == 0 else 0,
            eop=1 if i == beats - 1 else 0,
        )
        got = await tb.step()

        if in_ready:
            samples.append(got)
            i += 1

        in_ready = got["in_ready"][feed]

    return samples
