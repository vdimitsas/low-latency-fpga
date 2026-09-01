"""Shared helpers for the dedup_egress testbenches.

The DUT is dedup_egress itself. There is no wrapper file. dedup_egress has no
packed two dimensional ports, so cocotb drives it directly.

dedup_egress has one pipeline stage. The comparison against the table runs in
the cycle a beat is presented, and the beat plus its match results register
together. The outputs are combinational functions of that register.

step drives the inputs to the DUT and the model at the same point, then waits
for the clock edge and reads at the ReadOnly phase, once the simulator has
settled. So the outputs it returns belong to the beat driven that cycle, and
in_ready belongs to the cycle that has just started.

The sequence number is not extracted from the payload here. It arrives on
in_seq, put there by DEDUP_INGRESS, so every beat carries one.
"""

import os

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import NextTimeStep, ReadOnly, RisingEdge

# Must match the parameters the DUT is elaborated with.
DATA_W = int(os.environ.get("DATA_W", 64))
SEQ_W = int(os.environ.get("SEQ_W", 32))
CPT_DEPTH = int(os.environ.get("CPT_DEPTH", 8))

CLK_PERIOD_NS = 10

SEQ_MASK = (1 << SEQ_W) - 1
DATA_MASK = (1 << DATA_W) - 1


class GoldenDedupEgress:
    """Reference model of dedup_egress, cycle accurate.

    Call `drive` with this cycle's inputs, then `evaluate`. `evaluate` moves
    the state across the clock edge and returns the outputs after it, so the
    values it returns belong to the beat just driven.

    State held across the edge:

      cpt_seq, cpt_occupied, cpt_wr_ptr     the completed packets table
      valid_q, data_q, sop_q, eop_q, seq_q  the registered beat
      cpt_match_q, bypass_match_q           the registered match results
    """

    def __init__(self, cpt_depth=CPT_DEPTH):
        self.cpt_depth = cpt_depth
        self.reset()

    def reset(self):
        # this cycle's inputs, put here by drive
        self.in_valid = 0
        self.in_data = 0
        self.in_seq = 0
        self.in_sop = 0
        self.in_eop = 0
        self.out_ready = 0
        self.cmpl_valid = 0
        self.cmpl_seq = 0

        self.cpt_seq = [0] * self.cpt_depth
        self.cpt_occupied = [0] * self.cpt_depth
        self.cpt_wr_ptr = 0

        self.valid_q = 0
        self.data_q = 0
        self.sop_q = 0
        self.eop_q = 0
        self.seq_q = 0
        self.cpt_match_q = 0
        self.bypass_match_q = 0

    def drive(self, in_valid, in_data, in_seq, in_sop, in_eop, out_ready,
              cmpl_valid, cmpl_seq):
        """Put this cycle's inputs on the model, the way wires hold them."""
        self.in_valid = in_valid
        self.in_data = in_data & DATA_MASK
        self.in_seq = in_seq & SEQ_MASK
        self.in_sop = in_sop
        self.in_eop = in_eop
        self.out_ready = out_ready
        self.cmpl_valid = cmpl_valid
        self.cmpl_seq = cmpl_seq & SEQ_MASK

    # -- combinational, this cycle's inputs ---------------------------------

    def _matches(self):
        """Match results this cycle, against the table as it stands now."""
        cpt_match = 0
        for e in range(self.cpt_depth):
            if self.cpt_occupied[e] and self.cpt_seq[e] == self.in_seq:
                cpt_match = 1

        if self.cmpl_valid and self.cmpl_seq == self.in_seq:
            bypass_match = 1
        else:
            bypass_match = 0

        return cpt_match, bypass_match

    # -- combinational, from the registered state ---------------------------

    def _drop(self):
        if self.valid_q and (self.cpt_match_q or self.bypass_match_q):
            return 1
        return 0

    def _in_ready(self):
        if (not self.valid_q) or self.out_ready or self._drop():
            return 1
        return 0

    def evaluate(self):
        """Move across the clock edge, then report the outputs after it.

        in_ready and the match results are combinational, so they are computed
        from the flops and the table before either is written. The flops then
        load, and the outputs are read from them. So the returned outputs
        belong to the beat just driven, and in_ready belongs to the cycle that
        starts after the edge.
        """
        in_ready = self._in_ready()
        cpt_match, bypass_match = self._matches()

        # the cut: valid follows in_ready alone, payload follows the handshake
        if in_ready:
            self.valid_q = 1 if self.in_valid else 0

        if self.in_valid and in_ready:
            self.data_q = self.in_data
            self.sop_q = self.in_sop
            self.eop_q = self.in_eop
            self.seq_q = self.in_seq
            self.cpt_match_q = cpt_match
            self.bypass_match_q = bypass_match

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

        if self.valid_q and not self._drop():
            out_valid = 1
        else:
            out_valid = 0

        return {
            "in_ready": self._in_ready(),
            "out_valid": out_valid,
            "out_data": self.data_q,
            "out_sop": self.sop_q,
            "out_eop": self.eop_q,
            "out_seq": self.seq_q,
        }


class DedupEgressTB:
    """Drives dedup_egress one cycle at a time."""

    def __init__(self, dut):
        self.dut = dut
        self.golden = GoldenDedupEgress()
        self.clear()

    def clear(self):
        self.in_valid = 0
        self.in_data = 0
        self.in_seq = 0
        self.in_sop = 0
        self.in_eop = 0
        self.out_ready = 1
        self.cmpl_valid = 0
        self.cmpl_seq = 0

    def present(self, seq, data=0, sop=0, eop=0):
        """Stage a valid beat for the coming cycle.

        seq is required. Every beat carries its own sequence number on in_seq,
        so there is no mid packet beat that has to inherit one.

        To stage an idle cycle, call idle_stream, or simply do not call this:
        step clears the stimulus after every cycle.
        """
        self.in_valid = 1
        self.in_data = data
        self.in_seq = seq & SEQ_MASK
        self.in_sop = sop
        self.in_eop = eop

    def idle_stream(self):
        self.in_valid = 0
        self.in_sop = 0
        self.in_eop = 0

    def complete(self, seq):
        self.cmpl_valid = 1
        self.cmpl_seq = seq & SEQ_MASK

    def _apply(self):
        d = self.dut
        d.in_valid.value = self.in_valid
        d.in_data.value = self.in_data
        d.in_seq.value = self.in_seq
        d.in_sop.value = self.in_sop
        d.in_eop.value = self.in_eop
        d.out_ready.value = self.out_ready
        d.cmpl_valid.value = self.cmpl_valid
        d.cmpl_seq.value = self.cmpl_seq

    def sample(self):
        d = self.dut
        return {
            "in_ready": int(d.in_ready.value),
            "out_valid": int(d.out_valid.value),
            "out_sop": int(d.out_sop.value),
            "out_eop": int(d.out_eop.value),
            "out_data": int(d.out_data.value),
            "out_seq": int(d.out_seq.value),
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
            in_seq=self.in_seq,
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
        if expected["out_valid"]:
            assert got["out_seq"] == expected["out_seq"], (
                f"out_seq mismatch: got {got['out_seq']:#x} "
                f"expected {expected['out_seq']:#x}"
            )
            assert got["out_data"] == expected["out_data"], (
                f"out_data mismatch: got {got['out_data']:#x} "
                f"expected {expected['out_data']:#x}"
            )
            assert got["out_sop"] == expected["out_sop"], (
                f"out_sop mismatch: got {got['out_sop']} "
                f"expected {expected['out_sop']}"
            )
            assert got["out_eop"] == expected["out_eop"], (
                f"out_eop mismatch: got {got['out_eop']} "
                f"expected {expected['out_eop']}"
            )

        self.cmpl_valid = 0
        self.cmpl_seq = 0
        self.idle_stream()

        # leave the read only region, otherwise the next call cannot drive
        await NextTimeStep()

        return got


async def send_packet(tb, seq, beats):
    """Stream a whole packet, returning one sample per beat.

    in_ready is read after the edge, so it is the value for the coming cycle.
    It is held and used on the next pass to decide whether the beat that was
    just driven was taken. A refused beat is presented again.
    """
    samples = []
    in_ready = 1          # the stage starts empty, so the first beat is taken
    i = 0

    while i < beats:
        tb.present(
            seq=seq,
            data=0xA0 + i,
            sop=1 if i == 0 else 0,
            eop=1 if i == beats - 1 else 0,
        )
        got = await tb.step()

        if in_ready:
            samples.append(got)
            i += 1

        in_ready = got["in_ready"]

    return samples
