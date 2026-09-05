"""Shared helpers for the seq_extract_lane testbenches.

The DUT is seq_extract_lane itself. Its ports are already flat, so there is no
wrapper and no packing.

The lane has one register stage. A beat presented in a cycle appears on the
outputs after that cycle's clock edge. The sequence number is pulled out of the
beat on its way into that stage, so out_seq and out_seq_valid appear with the
beat that completed the field, not before it and not after.

step drives the inputs to the DUT and the model at the same point, then waits
for the clock edge and reads at the ReadOnly phase, once the simulator has
settled. So the outputs it returns belong to the beat driven that cycle, and
in_ready belongs to the cycle that has just started.
"""

import os

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import NextTimeStep, ReadOnly, RisingEdge

# Must match the parameters the DUT is elaborated with.
DATA_W = int(os.environ.get("DATA_W", 64))
SEQ_W = int(os.environ.get("SEQ_W", 32))
SEQ_OFFSET = int(os.environ.get("SEQ_OFFSET", 28))
BYTE_CNT_W = int(os.environ.get("BYTE_CNT_W", 3))

CLK_PERIOD_NS = 10

BYTES_PER_BEAT = DATA_W // 8
SEQ_MASK = (1 << SEQ_W) - 1
DATA_MASK = (1 << DATA_W) - 1
BYTE_CNT_MAX = BYTES_PER_BEAT - 1

# Where the field sits, mirroring the localparams in the RTL.
SEQ_BEAT = SEQ_OFFSET // BYTES_PER_BEAT
SEQ_BIT = (SEQ_OFFSET % BYTES_PER_BEAT) * 8
SEQ_LO_W = (DATA_W - SEQ_BIT) if (SEQ_BIT + SEQ_W > DATA_W) else SEQ_W
SEQ_HI_W = SEQ_W - SEQ_LO_W
SPAN = SEQ_HI_W != 0

# The beat the pulse lands on, in packet order.
PULSE_BEAT = SEQ_BEAT + 1 if SPAN else SEQ_BEAT


def beats_for_seq(seq, filler=0):
    """Build the one or two beats that carry `seq`, as a dict of beat index.

    The field is placed at SEQ_OFFSET from the start of the packet, so it lands
    in beat SEQ_BEAT, and continues into the beat after it when it spans.
    """
    seq &= SEQ_MASK
    out = {}

    lo = seq & ((1 << SEQ_LO_W) - 1)
    out[SEQ_BEAT] = ((filler & DATA_MASK) & ~(((1 << SEQ_LO_W) - 1) << SEQ_BIT)
                     | (lo << SEQ_BIT)) & DATA_MASK

    if SPAN:
        hi = (seq >> SEQ_LO_W) & ((1 << SEQ_HI_W) - 1)
        out[SEQ_BEAT + 1] = ((filler & DATA_MASK) & ~((1 << SEQ_HI_W) - 1)
                             | hi) & DATA_MASK

    return out


def packet_beats(seq, beats, filler=0xA0):
    """Build a whole packet as a list of beat payloads carrying `seq`.

    Beats that hold no part of the field get a distinct filler value so that a
    passthrough mismatch is visible.
    """
    carriers = beats_for_seq(seq)
    return [
        carriers.get(i, (filler + i) & DATA_MASK)
        for i in range(beats)
    ]


class GoldenSeqExtractLane:
    """Reference model of seq_extract_lane, cycle accurate.

    Call `drive` with this cycle's inputs, then `evaluate`. `evaluate` moves
    the state across the clock edge and returns the outputs after it, so the
    values it returns belong to the beat just driven.

    State held across the edge:

      beat_cnt                                  beat index within the packet
      seq_pending_q                             capture still owed this packet
      seq_lo_q                                  low part of a spanning field
      seq_q, seq_valid_q                        the extracted metadata
      valid_q, data_q, sop_q, eop_q, byte_cnt_q the registered beat
    """

    def __init__(self):
        self.reset()

    def reset(self):
        # this cycle's inputs, put here by drive
        self.in_valid = 0
        self.in_data = 0
        self.in_sop = 0
        self.in_eop = 0
        self.in_byte_cnt = 0
        self.out_ready = 0

        self.beat_cnt = 0
        self.seq_pending_q = 1
        self.seq_lo_q = 0
        self.seq_q = 0
        self.seq_valid_q = 0

        self.valid_q = 0
        self.data_q = 0
        self.sop_q = 0
        self.eop_q = 0
        self.byte_cnt_q = 0

    def drive(self, in_valid, in_data, in_sop, in_eop, in_byte_cnt, out_ready):
        """Put this cycle's inputs on the model, the way wires hold them."""
        self.in_valid = in_valid
        self.in_data = in_data & DATA_MASK
        self.in_sop = in_sop
        self.in_eop = in_eop
        self.in_byte_cnt = in_byte_cnt
        self.out_ready = out_ready

    # -- combinational, from the registered state ---------------------------

    def _in_ready(self):
        return 1 if ((not self.valid_q) or self.out_ready) else 0

    def evaluate(self):
        """Move across the clock edge, then report the outputs after it.

        in_ready and the beat markers are combinational, so they are computed
        from the flops before any of them are written. The flops then load, and
        the outputs are read from them.
        """
        in_ready = self._in_ready()
        accepted = self.in_valid and in_ready

        at_seq_beat = 1 if (accepted and self.seq_pending_q
                            and self.beat_cnt == SEQ_BEAT) else 0
        at_next_beat = 1 if (accepted and self.seq_pending_q
                             and self.beat_cnt == SEQ_BEAT + 1) else 0

        cnt_max = SEQ_BEAT + 1 if SPAN else SEQ_BEAT

        # low part of a spanning field
        if SPAN and at_seq_beat:
            self.seq_lo_q = (self.in_data >> SEQ_BIT) & ((1 << SEQ_LO_W) - 1)

        # the extracted metadata: the pulse takes the same enable as valid_q
        if in_ready:
            self.seq_valid_q = at_next_beat if SPAN else at_seq_beat

        if SPAN:
            if at_next_beat:
                hi = self.in_data & ((1 << SEQ_HI_W) - 1)
                self.seq_q = ((hi << SEQ_LO_W) | self.seq_lo_q) & SEQ_MASK
        else:
            if at_seq_beat:
                self.seq_q = (self.in_data >> SEQ_BIT) & SEQ_MASK

        # the register stage: valid follows in_ready alone, payload follows
        # the acceptance condition
        if in_ready:
            self.valid_q = 1 if self.in_valid else 0

        if accepted:
            self.data_q = self.in_data
            self.sop_q = self.in_sop
            self.eop_q = self.in_eop
            self.byte_cnt_q = self.in_byte_cnt

        # capture still owed, cleared on the beat that completes the field
        if accepted:
            if self.in_sop:
                self.seq_pending_q = 1
            elif (at_next_beat if SPAN else at_seq_beat):
                self.seq_pending_q = 0

        # beat counter, written last so the markers above saw its old value
        if accepted:
            if self.in_sop:
                self.beat_cnt = 1
            elif self.beat_cnt != cnt_max:
                self.beat_cnt += 1

        return {
            "in_ready": self._in_ready(),
            "out_valid": self.valid_q,
            "out_data": self.data_q,
            "out_sop": self.sop_q,
            "out_eop": self.eop_q,
            "out_byte_cnt": self.byte_cnt_q,
            "out_seq": self.seq_q,
            "out_seq_valid": self.seq_valid_q,
        }


class SeqExtractLaneTB:
    """Drives seq_extract_lane one cycle at a time."""

    def __init__(self, dut):
        self.dut = dut
        self.golden = GoldenSeqExtractLane()
        self.clear()

    def clear(self):
        self.in_valid = 0
        self.in_data = 0
        self.in_sop = 0
        self.in_eop = 0
        self.in_byte_cnt = BYTE_CNT_MAX
        self.out_ready = 1

    def present(self, data=0, sop=0, eop=0, byte_cnt=None):
        """Stage one beat for the coming cycle.

        byte_cnt defaults to a full beat, since that is what every beat but the
        last one carries.
        """
        self.in_valid = 1
        self.in_data = data & DATA_MASK
        self.in_sop = sop
        self.in_eop = eop
        self.in_byte_cnt = BYTE_CNT_MAX if byte_cnt is None else byte_cnt

    def idle_input(self):
        self.in_valid = 0
        self.in_sop = 0
        self.in_eop = 0

    def _apply(self):
        d = self.dut
        d.in_valid.value = self.in_valid
        d.in_data.value = self.in_data
        d.in_sop.value = self.in_sop
        d.in_eop.value = self.in_eop
        d.in_byte_cnt.value = self.in_byte_cnt
        d.out_ready.value = self.out_ready

    def sample(self):
        d = self.dut
        return {
            "in_ready": int(d.in_ready.value),
            "out_valid": int(d.out_valid.value),
            "out_data": int(d.out_data.value),
            "out_sop": int(d.out_sop.value),
            "out_eop": int(d.out_eop.value),
            "out_byte_cnt": int(d.out_byte_cnt.value),
            "out_seq": int(d.out_seq.value),
            "out_seq_valid": int(d.out_seq_valid.value),
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
            in_byte_cnt=self.in_byte_cnt,
            out_ready=self.out_ready,
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
        assert got["out_seq_valid"] == expected["out_seq_valid"], (
            f"out_seq_valid mismatch: got {got['out_seq_valid']} "
            f"expected {expected['out_seq_valid']}"
        )
        assert got["out_seq"] == expected["out_seq"], (
            f"out_seq mismatch: got {got['out_seq']:#x} "
            f"expected {expected['out_seq']:#x}"
        )

        if expected["out_valid"]:
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
            assert got["out_byte_cnt"] == expected["out_byte_cnt"], (
                f"out_byte_cnt mismatch: got {got['out_byte_cnt']} "
                f"expected {expected['out_byte_cnt']}"
            )

        self.idle_input()

        # leave the read only region, otherwise the next call cannot drive
        await NextTimeStep()

        return got

    async def idle(self, cycles=1):
        for _ in range(cycles):
            await self.step()


async def send_packet(tb, seq, beats, last_byte_cnt=None):
    """Stream a whole packet, returning one sample per beat.

    in_ready is read after the edge, so it is the value for the coming cycle.
    It is held and used on the next pass to decide whether the beat that was
    just driven was taken. A refused beat is presented again.
    """
    payload = packet_beats(seq, beats)
    samples = []
    in_ready = 1          # the stage starts empty, so the first beat is taken
    i = 0

    while i < beats:
        last = i == beats - 1
        tb.present(
            data=payload[i],
            sop=1 if i == 0 else 0,
            eop=1 if last else 0,
            byte_cnt=last_byte_cnt if (last and last_byte_cnt is not None)
            else None,
        )
        got = await tb.step()

        if in_ready:
            samples.append(got)
            i += 1

        in_ready = got["in_ready"]

    return samples
