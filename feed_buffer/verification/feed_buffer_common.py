"""Shared helpers for the feed_buffer testbenches.

The DUT is feed_buffer_tb_wrap, which flattens feed_buffer's packed two
dimensional ports into single vectors. Feed f lives in bits [f*W : (f+1)*W).

feed_buffer registers its output, so outputs are a function of state, not of
this cycle's inputs. Every helper here therefore drives early in the cycle and
samples late, after the combinational ready and valid logic has settled.
"""

import os
from collections import deque

from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, Timer

# Must match the parameters the wrapper is elaborated with.
N_FEEDS = int(os.environ.get("N_FEEDS", 4))
DATA_W = int(os.environ.get("DATA_W", 64))
SEQ_W = int(os.environ.get("SEQ_W", 32))
FIFO_DEPTH = int(os.environ.get("FIFO_DEPTH", 32))

CLK_PERIOD_NS = 10
DRIVE_DELAY_NS = 1
SAMPLE_DELAY_NS = 8

SEQ_MASK = (1 << SEQ_W) - 1
DATA_MASK = (1 << DATA_W) - 1


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


class Beat:
    """One beat as it moves through the model."""

    def __init__(self, data, seq, sop, eop):
        self.data = data & DATA_MASK
        self.seq = seq & SEQ_MASK
        self.sop = sop
        self.eop = eop

    def __eq__(self, other):
        return (
            self.data == other.data
            and self.seq == other.seq
            and self.sop == other.sop
            and self.eop == other.eop
        )

    def __repr__(self):
        return (
            f"Beat(data={self.data:#x}, seq={self.seq:#x}, "
            f"sop={self.sop}, eop={self.eop})"
        )


class GoldenFeed:
    """Reference model of one feed's slice of feed_buffer.

    Mirrors the RTL exactly: a FIFO, an output register with a valid bit, a
    sticky invalidate bit, and the bypass path.
    """

    def __init__(self, depth=FIFO_DEPTH):
        self.depth = depth
        self.reset()

    def reset(self):
        self.fifo = deque()
        self.out_reg = None
        self.out_reg_valid = 0
        self.invalidated = 0

    # -- combinational view of this cycle ---------------------------------
    def in_ready(self, out_ready=None):
        if len(self.fifo) < self.depth:
            return 1
        # Full. A beat is still accepted if one is leaving on the same edge.
        return self._out_reg_free(out_ready if out_ready is not None else 0)

    def out_valid(self):
        return self.out_reg_valid

    def out_beat(self):
        return self.out_reg

    def _drop(self, invalidate_feed, in_sop):
        return 1 if ((invalidate_feed or self.invalidated) and not in_sop) else 0

    def _out_reg_free(self, out_ready):
        return 1 if (not self.out_reg_valid or out_ready) else 0

    def _bypass(self, in_valid, in_sop, out_ready, invalidate_feed):
        return 1 if (
            in_valid
            and self.in_ready(out_ready)
            and not self._drop(invalidate_feed, in_sop)
            and len(self.fifo) == 0
            and self._out_reg_free(out_ready)
        ) else 0

    # -- state advance across the clock edge ------------------------------
    def commit(self, in_valid, beat, out_ready, invalidate_feed):
        in_sop = beat.sop if beat is not None else 0
        in_eop = beat.eop if beat is not None else 0

        drop = self._drop(invalidate_feed, in_sop)
        accepted = in_valid and self.in_ready(out_ready)
        bypass = self._bypass(in_valid, in_sop, out_ready, invalidate_feed)
        out_reg_free = self._out_reg_free(out_ready)
        fifo_was_empty = len(self.fifo) == 0

        # output register
        if out_reg_free:
            if not fifo_was_empty:
                self.out_reg = self.fifo[0]
                self.out_reg_valid = 1
            elif bypass:
                self.out_reg = beat
                self.out_reg_valid = 1
            else:
                self.out_reg_valid = 0

        # fifo read
        if not fifo_was_empty and out_reg_free:
            self.fifo.popleft()

        # fifo write. On a full FIFO this is only reachable when a read
        # happened above, so the append lands in the slot that just freed.
        if accepted and not drop and not bypass:
            self.fifo.append(beat)

        # sticky invalidate
        if accepted and (in_sop or in_eop):
            self.invalidated = 0
        elif invalidate_feed:
            self.invalidated = 1


class GoldenFeedBuffer:
    """All feeds together."""

    def __init__(self, n_feeds=N_FEEDS, depth=FIFO_DEPTH):
        self.feeds = [GoldenFeed(depth) for _ in range(n_feeds)]

    def reset(self):
        for f in self.feeds:
            f.reset()

    def in_ready(self, out_ready):
        return [f.in_ready(out_ready[i]) for i, f in enumerate(self.feeds)]

    def out_valid(self):
        return [f.out_valid() for f in self.feeds]


class FeedBufferTB:
    """Drives feed_buffer_tb_wrap one cycle at a time."""

    def __init__(self, dut):
        self.dut = dut
        self.n_feeds = N_FEEDS
        self.golden = GoldenFeedBuffer()
        self.cycle = 0
        # every beat the arbiter actually took, per feed, as (cycle, Beat).
        # A beat counts as taken on the cycle out_valid and out_ready are both
        # high, so a beat held across a stall is recorded once, not every
        # cycle it sits in the register.
        self.taken = [[] for _ in range(N_FEEDS)]
        self.clear()

    def clear(self):
        self.in_valid = [0] * self.n_feeds
        self.in_data = [0] * self.n_feeds
        self.in_seq = [0] * self.n_feeds
        self.in_sop = [0] * self.n_feeds
        self.in_eop = [0] * self.n_feeds
        self.out_ready = [1] * self.n_feeds
        self.invalidate_feed = [0] * self.n_feeds

    def present(self, feed, data=0, seq=0, sop=0, eop=0, valid=1):
        """Stage one feed's stimulus for the coming cycle."""
        self.in_valid[feed] = valid
        self.in_data[feed] = data
        self.in_seq[feed] = seq
        self.in_sop[feed] = sop
        self.in_eop[feed] = eop

    def idle_feeds(self):
        for f in range(self.n_feeds):
            self.in_valid[f] = 0
            self.in_sop[f] = 0
            self.in_eop[f] = 0

    def invalidate(self, feed):
        self.invalidate_feed[feed] = 1

    def _apply(self):
        d = self.dut
        d.in_valid.value = _pack(self.in_valid, 1)
        d.in_sop.value = _pack(self.in_sop, 1)
        d.in_eop.value = _pack(self.in_eop, 1)
        d.in_data_flat.value = _pack(self.in_data, DATA_W)
        d.in_seq_flat.value = _pack(self.in_seq, SEQ_W)
        d.out_ready.value = _pack(self.out_ready, 1)
        d.invalidate_feed.value = _pack(self.invalidate_feed, 1)

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

    def out_beat(self, got, feed):
        """The beat presented on a feed's output this cycle."""
        return Beat(
            got["out_data"][feed],
            got["out_seq"][feed],
            got["out_sop"][feed],
            got["out_eop"][feed],
        )

    async def start(self):
        """Start the clock and hold reset for a few cycles."""
        import cocotb

        cocotb.start_soon(Clock(self.dut.clk, CLK_PERIOD_NS, "ns").start())
        await Timer(1, "ns")
        self.clear()
        self.dut.rst_n.value = 0
        self._apply()
        await Timer(CLK_PERIOD_NS * 5, "ns")
        self.dut.rst_n.value = 1
        await RisingEdge(self.dut.clk)
        self.golden.reset()
        self.cycle = 0
        self.taken = [[] for _ in range(self.n_feeds)]

    async def step(self, check=True):
        """Run one cycle: drive, settle, sample, advance the model.

        Returns the sampled outputs. The stimulus is cleared afterwards so
        each cycle has to be staged explicitly.
        """
        await Timer(DRIVE_DELAY_NS, "ns")
        self._apply()

        beats = [
            Beat(self.in_data[f], self.in_seq[f], self.in_sop[f], self.in_eop[f])
            if self.in_valid[f]
            else None
            for f in range(self.n_feeds)
        ]
        stim = dict(
            in_valid=list(self.in_valid),
            beats=beats,
            out_ready=list(self.out_ready),
            invalidate_feed=list(self.invalidate_feed),
        )

        exp_in_ready = self.golden.in_ready(self.out_ready)
        exp_out_valid = self.golden.out_valid()
        exp_out_beats = [f.out_beat() for f in self.golden.feeds]

        await Timer(SAMPLE_DELAY_NS, "ns")
        got = self.sample()

        if check:
            assert got["in_ready"] == exp_in_ready, (
                f"in_ready mismatch: got {got['in_ready']} "
                f"expected {exp_in_ready}"
            )
            assert got["out_valid"] == exp_out_valid, (
                f"out_valid mismatch: got {got['out_valid']} "
                f"expected {exp_out_valid}"
            )
            for f in range(self.n_feeds):
                if exp_out_valid[f]:
                    assert self.out_beat(got, f) == exp_out_beats[f], (
                        f"output beat mismatch on feed {f}: "
                        f"got {self.out_beat(got, f)} expected {exp_out_beats[f]}"
                    )

        for f in range(self.n_feeds):
            if got["out_valid"][f] and self.out_ready[f]:
                self.taken[f].append((self.cycle, self.out_beat(got, f)))

        await RisingEdge(self.dut.clk)
        self.cycle += 1

        for f in range(self.n_feeds):
            self.golden.feeds[f].commit(
                stim["in_valid"][f],
                stim["beats"][f],
                stim["out_ready"][f],
                stim["invalidate_feed"][f],
            )

        self.idle_feeds()
        self.invalidate_feed = [0] * self.n_feeds

        return got

    async def idle(self, cycles=1):
        for _ in range(cycles):
            await self.step()


async def drain(tb, cycles=8):
    """Idle for `cycles` so anything still held works its way out."""
    await tb.idle(cycles)


def beats_taken(tb, feed):
    """Every beat the arbiter took on `feed`, in order."""
    return [b for _, b in tb.taken[feed]]


def cycles_taken(tb, feed):
    """The cycle each beat on `feed` was taken."""
    return [c for c, _ in tb.taken[feed]]
