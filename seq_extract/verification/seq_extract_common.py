"""Shared helpers for the seq_extract testbench.

The DUT is seq_extract_tb_wrap, which flattens seq_extract's packed two
dimensional ports into single vectors. Feed f lives in bits [f*W : (f+1)*W) of
a flat vector.

There is no golden model here. seq_extract holds no logic of its own, it is
N_FEEDS instances of seq_extract_lane, and the lane is already verified against
a cycle accurate model in its own suite. What these tests check is the wiring:
that feed f's beat, sequence number and ready all belong to feed f.
"""

import os

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import NextTimeStep, ReadOnly, RisingEdge

# Must match the parameters the wrapper is elaborated with.
N_FEEDS = int(os.environ.get("N_FEEDS", 4))
DATA_W = int(os.environ.get("DATA_W", 64))
SEQ_W = int(os.environ.get("SEQ_W", 32))
SEQ_OFFSET = int(os.environ.get("SEQ_OFFSET", 28))

# Functional simulation only, so the period changes nothing but the numbers
# in the log. 10 ns is what every block in this project uses. The real 325
# MHz target lives in the STA scripts.
CLK_PERIOD_NS = float(os.environ.get("CLK_PERIOD_NS", 10))

BYTES_PER_BEAT = DATA_W // 8
BYTE_CNT_W = (BYTES_PER_BEAT - 1).bit_length()
DATA_MASK = (1 << DATA_W) - 1
BYTE_CNT_MAX = BYTES_PER_BEAT - 1

# Where the field sits, mirroring the localparams in the lane.
SEQ_BEAT = SEQ_OFFSET // BYTES_PER_BEAT
SEQ_BIT = (SEQ_OFFSET % BYTES_PER_BEAT) * 8
SEQ_LO_W = (DATA_W - SEQ_BIT) if (SEQ_BIT + SEQ_W > DATA_W) else SEQ_W
SEQ_HI_W = SEQ_W - SEQ_LO_W
SPAN = SEQ_HI_W != 0

# The beat the pulse lands on, in packet order.
PULSE_BEAT = SEQ_BEAT + 1 if SPAN else SEQ_BEAT


def _pack(values, width):
    """Stack per feed values into one flat vector, feed 0 in the low bits."""
    word = 0
    for i, v in enumerate(values):
        word |= (v & ((1 << width) - 1)) << (i * width)
    return word


def _unpack(word, width, count):
    """Split a flat vector back into per feed values."""
    mask = (1 << width) - 1
    return [((word >> (i * width)) & mask) for i in range(count)]


def _bits(word, count):
    """Split a one bit per feed vector into a list."""
    return [(word >> i) & 1 for i in range(count)]


def beats_for_seq(seq, filler=0):
    """Build the one or two beats that carry `seq`, keyed by beat index.

    The field starts at SEQ_OFFSET bytes into the packet, so it lands in beat
    SEQ_BEAT at bit SEQ_BIT. When it does not fit in what is left of that beat,
    the rest continues at bit 0 of the beat after it.
    """
    out = {}

    # The part of the field that fits in beat SEQ_BEAT. seq_place marks the
    # bits it occupies, cleared out of the filler before the field goes in.
    lo = seq & ((1 << SEQ_LO_W) - 1)
    seq_place = ((1 << SEQ_LO_W) - 1) << SEQ_BIT
    out[SEQ_BEAT] = (filler & ~seq_place) | (lo << SEQ_BIT)

    if SPAN:
        # What was left over, sitting at the bottom of the next beat.
        hi = seq >> SEQ_LO_W
        seq_place = (1 << SEQ_HI_W) - 1
        out[SEQ_BEAT + 1] = (filler & ~seq_place) | hi

    return out


def packet_beats(seq, beats, filler=0xA0):
    """Build a whole packet as a list of beat payloads carrying `seq`.

    Beats holding no part of the field get a value that differs per beat, so a
    beat delivered out of order is visible.
    """
    seq_beats = beats_for_seq(seq)

    payload = []
    for i in range(beats):
        if i in seq_beats:
            payload.append(seq_beats[i])
        else:
            payload.append((filler + i) & DATA_MASK)

    return payload


class SeqExtractTB:
    """Drives seq_extract_tb_wrap one cycle at a time.

    There is no model to compare against here, so step does no checking. It
    drives the staged stimulus, waits for the edge, and returns what came out.
    The tests do the asserting.
    """

    def __init__(self, dut):
        self.dut = dut
        self.n_feeds = N_FEEDS
        self.clear()

    def clear(self):
        self.in_valid = [0] * self.n_feeds
        self.in_data = [0] * self.n_feeds
        self.in_sop = [0] * self.n_feeds
        self.in_eop = [0] * self.n_feeds
        self.in_byte_cnt = [BYTE_CNT_MAX] * self.n_feeds
        self.out_ready = [1] * self.n_feeds

    def present(self, feed, data=0, sop=0, eop=0, byte_cnt=None):
        """Stage one feed's beat for the coming cycle."""
        self.in_valid[feed] = 1
        self.in_data[feed] = data & DATA_MASK
        self.in_sop[feed] = sop
        self.in_eop[feed] = eop
        self.in_byte_cnt[feed] = BYTE_CNT_MAX if byte_cnt is None else byte_cnt

    def idle_feeds(self):
        for f in range(self.n_feeds):
            self.in_valid[f] = 0
            self.in_sop[f] = 0
            self.in_eop[f] = 0

    def _apply(self):
        d = self.dut
        d.in_valid.value = _pack(self.in_valid, 1)
        d.in_sop.value = _pack(self.in_sop, 1)
        d.in_eop.value = _pack(self.in_eop, 1)
        d.in_data_flat.value = _pack(self.in_data, DATA_W)
        d.in_byte_cnt_flat.value = _pack(self.in_byte_cnt, BYTE_CNT_W)
        d.out_ready.value = _pack(self.out_ready, 1)

    def sample(self):
        d = self.dut
        return {
            "in_ready": _bits(int(d.in_ready.value), self.n_feeds),
            "out_valid": _bits(int(d.out_valid.value), self.n_feeds),
            "out_sop": _bits(int(d.out_sop.value), self.n_feeds),
            "out_eop": _bits(int(d.out_eop.value), self.n_feeds),
            "out_seq_valid": _bits(int(d.out_seq_valid.value), self.n_feeds),
            "out_data": _unpack(int(d.out_data_flat.value), DATA_W,
                                self.n_feeds),
            "out_byte_cnt": _unpack(int(d.out_byte_cnt_flat.value),
                                    BYTE_CNT_W, self.n_feeds),
            "out_seq": _unpack(int(d.out_seq_flat.value), SEQ_W,
                               self.n_feeds),
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

    async def step(self):
        """Run one cycle and return the DUT outputs read after the edge."""
        self._apply()

        await RisingEdge(self.dut.clk)
        await ReadOnly()
        got = self.sample()

        self.idle_feeds()

        # leave the read only region, otherwise the next call cannot drive
        await NextTimeStep()

        return got

    async def idle(self, cycles=1):
        for _ in range(cycles):
            await self.step()
