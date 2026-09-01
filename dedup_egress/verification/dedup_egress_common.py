"""Shared helpers for the dedup_egress testbenches.

The DUT is dedup_egress itself. There is no wrapper file. dedup_egress has no
packed two dimensional ports, so cocotb drives it directly.

dedup_egress has one pipeline stage. The comparison against the table runs in
the cycle a beat is presented, and the beat plus its match results register
together. The outputs are combinational functions of that register, so a beat
presented in cycle N appears on the outputs in cycle N+1.

The ready path is still combinational: in_ready is driven by the registered
state and out_ready, so the upstream sees the decision in the same cycle.

The sequence number is not extracted from the payload here. It arrives on
in_seq, put there by DEDUP_INGRESS, so every beat carries one.

Outputs are read at the ReadOnly phase, which is the end of the cycle, after
the simulator has settled the combinational logic.
"""

import os

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ReadOnly, RisingEdge, Timer

# Must match the parameters the DUT is elaborated with.
DATA_W = int(os.environ.get("DATA_W", 64))
SEQ_W = int(os.environ.get("SEQ_W", 32))
CPT_DEPTH = int(os.environ.get("CPT_DEPTH", 8))

CLK_PERIOD_NS = 10

SEQ_MASK = (1 << SEQ_W) - 1
DATA_MASK = (1 << DATA_W) - 1


class GoldenDedupEgress:
    """Reference model of dedup_egress, cycle accurate.

    Call `evaluate` to get this cycle's outputs, then
    `registers_and_tables_update` with this cycle's inputs to move the state
    across the clock edge.

    State held across the edge:

      cpt_seq, cpt_occupied, cpt_wr_ptr     the completed packets table
      valid_q, data_q, sop_q, eop_q, seq_q  the registered beat
      cpt_match_q, bypass_match_q           the registered match results
    """

    def __init__(self, cpt_depth=CPT_DEPTH):
        self.cpt_depth = cpt_depth
        self.reset()

    def reset(self):
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

    # -- combinational, this cycle's inputs ---------------------------------

    def _matches(self, in_seq, cmpl_valid, cmpl_seq):
        """Match results this cycle, against the current table."""
        seq = in_seq & SEQ_MASK

        cpt_match = 0
        for e in range(self.cpt_depth):
            if self.cpt_occupied[e] and self.cpt_seq[e] == seq:
                cpt_match = 1

        if cmpl_valid and (cmpl_seq & SEQ_MASK) == seq:
            bypass_match = 1
        else:
            bypass_match = 0

        return cpt_match, bypass_match

    # -- combinational, from the registered state ---------------------------

    def _drop(self):
        if self.valid_q and (self.cpt_match_q or self.bypass_match_q):
            return 1
        return 0

    def _in_ready(self, out_ready):
        if (not self.valid_q) or out_ready or self._drop():
            return 1
        return 0

    def evaluate(self, out_ready):
        """Outputs presented during this cycle.

        Everything here comes from the registered state. This cycle's inputs
        do not reach the outputs until the next cycle, so out_ready is the
        only input needed here: it feeds the ready path combinationally.
        """
        if self.valid_q and not self._drop():
            out_valid = 1
        else:
            out_valid = 0

        return {
            "in_ready": self._in_ready(out_ready),
            "out_valid": out_valid,
            "out_data": self.data_q,
            "out_sop": self.sop_q,
            "out_eop": self.eop_q,
            "out_seq": self.seq_q,
        }

    def registers_and_tables_update(self, in_valid, in_data, in_seq, in_sop,
                                    in_eop, out_ready, cmpl_valid, cmpl_seq):
        """Move the state across the clock edge.

        This is the model's clock edge. The registered beat loads, and the
        completed packets table takes any completion arriving this cycle.
        """
        in_ready = self._in_ready(out_ready)
        cpt_match, bypass_match = self._matches(in_seq, cmpl_valid, cmpl_seq)

        # the cut: valid follows in_ready alone, payload follows the handshake
        if in_ready:
            self.valid_q = 1 if in_valid else 0

        if in_valid and in_ready:
            self.data_q = in_data & DATA_MASK
            self.sop_q = in_sop
            self.eop_q = in_eop
            self.seq_q = in_seq & SEQ_MASK
            self.cpt_match_q = cpt_match
            self.bypass_match_q = bypass_match

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
        await Timer(1, "ns")
        self.clear()
        self.dut.rst_n.value = 0
        self._apply()
        await Timer(CLK_PERIOD_NS * 5, "ns")
        self.dut.rst_n.value = 1
        await RisingEdge(self.dut.clk)
        self.golden.reset()

    async def step(self):
        """Run one cycle: drive the staged stimulus, settle, sample, advance.

        Returns the sampled outputs for that cycle. The stimulus is cleared
        afterwards so each cycle must be staged explicitly.

        The outputs sampled here belong to the beat accepted on the previous
        edge, not to the stimulus staged for this cycle.
        """
        self._apply()

        expected = self.golden.evaluate(out_ready=self.out_ready)

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

        await RisingEdge(self.dut.clk)

        self.golden.registers_and_tables_update(
            in_valid=self.in_valid,
            in_data=self.in_data,
            in_seq=self.in_seq,
            in_sop=self.in_sop,
            in_eop=self.in_eop,
            out_ready=self.out_ready,
            cmpl_valid=self.cmpl_valid,
            cmpl_seq=self.cmpl_seq,
        )

        self.cmpl_valid = 0
        self.cmpl_seq = 0
        self.idle_stream()

        return got


async def send_packet(tb, seq, beats, out_ready=None):
    """Stream a whole packet, returning one sample per beat.

    A beat is re-presented until in_ready is high, so the packet survives
    backpressure. Once a beat is accepted, one more cycle is run to let it
    reach the outputs, and that cycle's sample is the one returned for it.

    That extra cycle puts a gap between beats, so this does not drive back to
    back traffic. Tests that need back to back beats drive them by hand.
    """
    samples = []

    for i in range(beats):
        while True:
            if out_ready is not None:
                tb.out_ready = out_ready
            tb.present(
                seq=seq,
                data=0xA0 + i,
                sop=1 if i == 0 else 0,
                eop=1 if i == beats - 1 else 0,
            )
            got = await tb.step()
            if got["in_ready"]:
                break

        if out_ready is not None:
            tb.out_ready = out_ready
        samples.append(await tb.step())

    return samples
