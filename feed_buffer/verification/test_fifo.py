"""sync_fifo, standalone.

The FIFO is generic and knows nothing about beats or feeds, so it is verified
on its own before feed_buffer is built on top of it.

Two properties worth stating up front.

rd_data is combinational. When empty is low, rd_data already shows the head in
that same cycle. That is what makes the read path one cycle rather than two,
and test_head_is_visible_immediately is the assertion that holds it in place.

The write is not qualified with full inside the module. A write and a read
together on a full FIFO are both accepted, one in and one out. A write on a
full FIFO with no read overwrites the head, and the caller is required to
prevent that, so no test here does it.
"""

import os
import random
from collections import deque

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, Timer

WIDTH = int(os.environ.get("WIDTH", 98))
DEPTH = int(os.environ.get("DEPTH", 32))

CLK_PERIOD_NS = 10
DRIVE_DELAY_NS = 1
SAMPLE_DELAY_NS = 8

MASK = (1 << WIDTH) - 1


class FifoTB:
    def __init__(self, dut):
        self.dut = dut
        self.model = deque()

    async def start(self):
        cocotb.start_soon(Clock(self.dut.clk, CLK_PERIOD_NS, "ns").start())
        await Timer(1, "ns")
        self.dut.rst_n.value = 0
        self.dut.wr_en.value = 0
        self.dut.rd_en.value = 0
        self.dut.wr_data.value = 0
        await Timer(CLK_PERIOD_NS * 5, "ns")
        self.dut.rst_n.value = 1
        await RisingEdge(self.dut.clk)
        self.model.clear()

    async def step(self, wr_en=0, wr_data=0, rd_en=0, check=True):
        """One cycle. Returns the flags and rd_data seen during it."""
        await Timer(DRIVE_DELAY_NS, "ns")
        self.dut.wr_en.value = wr_en
        self.dut.wr_data.value = wr_data & MASK
        self.dut.rd_en.value = rd_en

        exp_empty = 1 if len(self.model) == 0 else 0
        exp_full = 1 if len(self.model) >= DEPTH else 0
        exp_head = self.model[0] if self.model else None

        await Timer(SAMPLE_DELAY_NS, "ns")
        got = {
            "empty": int(self.dut.empty.value),
            "full": int(self.dut.full.value),
            "rd_data": int(self.dut.rd_data.value),
        }

        if check:
            assert got["empty"] == exp_empty, (
                f"empty mismatch: got {got['empty']} expected {exp_empty} "
                f"at occupancy {len(self.model)}"
            )
            assert got["full"] == exp_full, (
                f"full mismatch: got {got['full']} expected {exp_full} "
                f"at occupancy {len(self.model)}"
            )
            if exp_head is not None:
                assert got["rd_data"] == exp_head, (
                    f"rd_data mismatch: got {got['rd_data']:#x} "
                    f"expected {exp_head:#x}"
                )

        await RisingEdge(self.dut.clk)

        # Advance the model exactly as the RTL does. The write is no longer
        # qualified with full inside the FIFO, so a write and a read together
        # on a full FIFO are both accepted: one in, one out, occupancy
        # unchanged. A write on a full FIFO with no read overwrites the head,
        # which the caller is required to prevent and which no test does.
        if rd_en and not exp_empty:
            self.model.popleft()
        if wr_en and (not exp_full or (rd_en and not exp_empty)):
            self.model.append(wr_data & MASK)

        self.dut.wr_en.value = 0
        self.dut.rd_en.value = 0
        return got


@cocotb.test()
async def test_empty_at_reset(dut):
    """Reset leaves the FIFO empty and not full."""
    tb = FifoTB(dut)
    await tb.start()

    got = await tb.step()
    assert got["empty"] == 1, "not empty after reset"
    assert got["full"] == 0, "full after reset"


@cocotb.test()
async def test_head_is_visible_immediately(dut):
    """rd_data shows the head in the same cycle empty goes low.

    A write on cycle N makes empty low on cycle N+1. This asserts that
    rd_data is already the written value on N+1, with no read having
    happened. If rd_data were registered it would only appear on N+2, and
    the whole read path would cost an extra cycle.
    """
    tb = FifoTB(dut)
    await tb.start()

    value = 0xDEADBEEF & MASK
    await tb.step(wr_en=1, wr_data=value)

    got = await tb.step()
    assert got["empty"] == 0, "empty did not clear after a write"
    assert got["rd_data"] == value, (
        f"head not visible in the cycle empty went low: "
        f"got {got['rd_data']:#x} expected {value:#x}"
    )


@cocotb.test()
async def test_fill_to_full(dut):
    """full asserts at exactly DEPTH entries, not before."""
    tb = FifoTB(dut)
    await tb.start()

    for i in range(DEPTH):
        got = await tb.step(wr_en=1, wr_data=0x100 + i)
        assert got["full"] == 0, f"full asserted early, at {i} entries"

    got = await tb.step()
    assert got["full"] == 1, f"full did not assert at {DEPTH} entries"
    assert got["empty"] == 0, "empty asserted while full"


@cocotb.test()
async def test_write_and_read_together_while_full(dut):
    """A write and a read on the same cycle are both accepted when full.

    This is what stops a full FIFO refusing a beat for one cycle while full
    catches up with the pop. Occupancy has to stay at DEPTH, the beat leaving
    has to be the old head, and order has to hold across the drain after.
    """
    tb = FifoTB(dut)
    await tb.start()

    for i in range(DEPTH):
        await tb.step(wr_en=1, wr_data=0x200 + i)

    got = await tb.step()
    assert got["full"] == 1, "expected the FIFO to be full"

    # Four cycles of one in, one out, while full throughout.
    for j in range(4):
        got = await tb.step(wr_en=1, wr_data=0x900 + j, rd_en=1)
        assert got["full"] == 1, (
            f"cycle {j}: full dropped, so occupancy changed during an overwrite"
        )
        assert got["rd_data"] == 0x200 + j, (
            f"cycle {j}: the head was corrupted by the beat arriving, "
            f"got {got['rd_data']:#x}"
        )

    # Drain. What comes out is the untouched remainder, then the four new ones.
    expected = [0x200 + i for i in range(4, DEPTH)] + [0x900 + j for j in range(4)]
    for i, want in enumerate(expected):
        got = await tb.step(rd_en=1)
        assert got["rd_data"] == want, (
            f"entry {i} wrong after the overwrites: "
            f"got {got['rd_data']:#x} expected {want:#x}"
        )

    got = await tb.step()
    assert got["empty"] == 1, "not empty after a full drain"


@cocotb.test()
async def test_read_while_empty_is_ignored(dut):
    """A read from an empty FIFO must not move the read pointer."""
    tb = FifoTB(dut)
    await tb.start()

    for _ in range(4):
        got = await tb.step(rd_en=1)
        assert got["empty"] == 1, "empty dropped on a refused read"

    # A write after those refused reads must still be readable.
    value = 0xC0FFEE & MASK
    await tb.step(wr_en=1, wr_data=value)
    got = await tb.step(rd_en=1)
    assert got["rd_data"] == value, (
        "the read pointer moved while empty, so the next write was skipped"
    )


@cocotb.test()
async def test_simultaneous_read_and_write(dut):
    """Read and write in the same cycle, at several occupancies.

    Occupancy must stay put and ordering must hold. Depth 1 and depth
    DEPTH-1 are included because those are the boundaries where the flags
    are about to change.
    """
    tb = FifoTB(dut)
    await tb.start()

    for occupancy in (1, 2, DEPTH - 1):
        # Set up the occupancy from empty.
        for i in range(occupancy):
            await tb.step(wr_en=1, wr_data=0x300 + i)

        # Read and write together, several times.
        for j in range(4):
            got = await tb.step(wr_en=1, wr_data=0x400 + j, rd_en=1)
            assert got["empty"] == 0, "empty asserted during a read and write"

        # Drain, and check what comes out is what the model says.
        while len(tb.model) > 0:
            await tb.step(rd_en=1)

        got = await tb.step()
        assert got["empty"] == 1, f"not empty after draining from {occupancy}"


@cocotb.test()
async def test_wrap(dut):
    """Fill, drain, then fill past the wrap point. Order must hold."""
    tb = FifoTB(dut)
    await tb.start()

    # Fill and drain once so the pointers are part way round.
    for i in range(DEPTH):
        await tb.step(wr_en=1, wr_data=0x500 + i)
    for _ in range(DEPTH):
        await tb.step(rd_en=1)

    got = await tb.step()
    assert got["empty"] == 1, "not empty after the first drain"

    # Fill again. The write pointer wraps part way through.
    for i in range(DEPTH):
        await tb.step(wr_en=1, wr_data=0x600 + i)

    got = await tb.step()
    assert got["full"] == 1, "full did not assert after wrapping"

    for i in range(DEPTH):
        got = await tb.step(rd_en=1)
        assert got["rd_data"] == 0x600 + i, (
            f"order broken across the wrap at entry {i}"
        )


@cocotb.test()
async def test_random(dut):
    """Random reads and writes against a deque.

    The step helper checks empty, full and rd_data every cycle, so this only
    has to generate traffic. Write and read probabilities are deliberately
    unequal in each regime so the FIFO spends real time at both boundaries.
    """
    tb = FifoTB(dut)
    await tb.start()

    rnd = random.Random(0xF1F0)
    counter = 0

    for wr_p, rd_p, cycles in ((0.7, 0.3, 800), (0.3, 0.7, 800), (0.5, 0.5, 800)):
        for _ in range(cycles):
            wr = 1 if rnd.random() < wr_p else 0
            rd = 1 if rnd.random() < rd_p else 0
            # The caller must not write into a full FIFO unless it is also
            # reading, which is the contract feed_buffer satisfies. Holding to
            # it here keeps the overwrite case in its own directed test.
            if wr and len(tb.model) >= DEPTH and not rd:
                wr = 0
            counter = (counter + 1) & MASK
            await tb.step(wr_en=wr, wr_data=counter, rd_en=rd)

    # Drain whatever is left, checking order the whole way.
    while len(tb.model) > 0:
        await tb.step(rd_en=1)

    got = await tb.step()
    assert got["empty"] == 1, "not empty after the final drain"
