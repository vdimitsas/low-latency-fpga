"""Shared helpers for the dedup_ingress testbenches.

The DUT is dedup_ingress_tb_wrap, which flattens dedup_ingress's packed two
dimensional ports into single vectors. Feed f lives in bits [f*W : (f+1)*W) of
a flat vector.

dedup_ingress has one register, at its output. The comparators, the drop
decision and the ready path all run in the cycle a beat is presented, and the
beat is registered on the way out. So a beat presented in a cycle appears on
the outputs after that cycle's clock edge, and in_ready is combinational from
the same cycle's inputs.

step drives the inputs to the DUT and the model at the same point, then waits
for the clock edge and reads at the ReadOnly phase, once the simulator has
settled. So the outputs it returns belong to the beat driven that cycle, and
in_ready belongs to the cycle that has just started.

The sequence number does not travel in in_data. It arrives on in_seq, marked by
in_seq_valid on one beat of the packet, the way seq_extract delivers it. That
beat is never beat 0: the beats before it carry no sequence number at all and
cannot be dropped. out_seq_valid says which beats carry a real out_seq, so the
stale window ahead of it is explicit on the interface.
"""

import os

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import NextTimeStep, ReadOnly, RisingEdge

# Must match the parameters the wrapper is elaborated with.
N_FEEDS = int(os.environ.get("N_FEEDS", 4))
DATA_W = int(os.environ.get("DATA_W", 64))
SEQ_W = int(os.environ.get("SEQ_W", 32))
CPT_DEPTH = int(os.environ.get("CPT_DEPTH", 8))

CLK_PERIOD_NS = 10

SEQ_MASK = (1 << SEQ_W) - 1
DATA_MASK = (1 << DATA_W) - 1


def _pack(values, width):
    word = 0
    for i, v in enumerate(values):
        word |= (v & ((1 << width) - 1)) << (i * width)
    return word


def _unpack(word, width, count):
    mask = (1 << width) - 1
    values = []
    for i in range(count):
        values.append((word >> (i * width)) & mask)
    return values


def _bits(word, count):
    values = []
    for i in range(count):
        values.append((word >> i) & 1)
    return values


class GoldenDedupIngress:
    """Reference model of dedup_ingress, cycle accurate.

    Call `drive` with this cycle's inputs, then `evaluate`. `evaluate` moves
    the state across the clock edge and returns the outputs after it, so the
    values it returns belong to the beat just driven.

    State held across the edge:

      cpt_seq, cpt_occupied, cpt_wr_ptr   the completed packets table
      seq_regs                            per feed sequence number
      seq_valid_regs                      whether it has arrived on this packet
      out_*                               the output register
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
        self.in_seq = [0] * self.n_feeds
        self.in_seq_valid = [0] * self.n_feeds
        self.out_ready = [0] * self.n_feeds
        self.cmpl_valid = 0
        self.cmpl_seq = 0

        self.cpt_seq = [0] * self.cpt_depth
        self.cpt_occupied = [0] * self.cpt_depth
        self.cpt_wr_ptr = 0
        self.seq_regs = [0] * self.n_feeds
        self.seq_valid_regs = [0] * self.n_feeds

        # the output register
        self.out_valid_q = [0] * self.n_feeds
        self.out_data_q = [0] * self.n_feeds
        self.out_sop_q = [0] * self.n_feeds
        self.out_eop_q = [0] * self.n_feeds
        self.out_seq_q = [0] * self.n_feeds
        self.out_seq_valid_q = [0] * self.n_feeds

    def drive(self, in_valid, in_data, in_sop, in_eop, in_seq, in_seq_valid,
              out_ready, cmpl_valid, cmpl_seq):
        """Put this cycle's inputs on the model, the way wires hold them."""
        self.in_valid = list(in_valid)
        self.in_data = []
        for d in in_data:
            self.in_data.append(d & DATA_MASK)
        self.in_sop = list(in_sop)
        self.in_eop = list(in_eop)
        self.in_seq = []
        for value in in_seq:
            self.in_seq.append(value & SEQ_MASK)
        self.in_seq_valid = list(in_seq_valid)
        self.out_ready = list(out_ready)
        self.cmpl_valid = cmpl_valid
        self.cmpl_seq = cmpl_seq & SEQ_MASK

    # -- combinational, from this cycle's inputs and the flops ---------------

    def _seq_sel(self):
        """The seq compared this cycle, and whether it means anything yet.

        The arriving value on the beat in_seq_valid marks, the held value
        afterwards. Before the sequence number has arrived there is nothing to
        compare.

        A SOP beat clears the valid here and now. seq_valid_regs only clears at
        the clock edge, so without this the first beat of a packet would still
        be compared against the number the previous packet left behind.
        """
        seq = []
        valid = []
        for f in range(self.n_feeds):
            if self.in_seq_valid[f]:
                seq.append(self.in_seq[f])
                valid.append(1)
            elif self.in_sop[f]:
                seq.append(self.seq_regs[f])
                valid.append(0)
            else:
                seq.append(self.seq_regs[f])
                valid.append(self.seq_valid_regs[f])
        return seq, valid

    def _drop(self):
        seq_sel, seq_sel_valid = self._seq_sel()

        drop = []
        for f in range(self.n_feeds):
            cpt_match = 0
            for e in range(self.cpt_depth):
                if (seq_sel_valid[f] and self.cpt_occupied[e]
                        and self.cpt_seq[e] == seq_sel[f]):
                    cpt_match = 1

            bypass_match = 0
            if (seq_sel_valid[f] and self.cmpl_valid
                    and self.cmpl_seq == seq_sel[f]):
                bypass_match = 1

            if self.in_valid[f] and (cpt_match or bypass_match):
                drop.append(1)
            else:
                drop.append(0)
        return drop

    def _in_ready(self):
        drop = self._drop()
        ready = []
        for f in range(self.n_feeds):
            if (not self.in_valid[f]) or self.out_ready[f] or drop[f]:
                ready.append(1)
            else:
                ready.append(0)
        return ready

    def evaluate(self):
        """Move across the clock edge, then report the outputs after it.

        in_ready, the sequence context and the drop decision are
        combinational, so they are computed from the inputs and from the flops
        before any of them are written. The flops then load, and the outputs
        are read from them.
        """
        seq_sel, seq_sel_valid = self._seq_sel()
        drop = self._drop()

        # the output register: the enable is out_ready, so a closed feed holds
        # what it has. drop is not on the payload enable, only on the valid.
        for f in range(self.n_feeds):
            if self.out_ready[f]:
                if self.in_valid[f] and not drop[f]:
                    self.out_valid_q[f] = 1
                else:
                    self.out_valid_q[f] = 0

            if self.out_ready[f] and self.in_valid[f]:
                self.out_data_q[f] = self.in_data[f]
                self.out_sop_q[f] = self.in_sop[f]
                self.out_eop_q[f] = self.in_eop[f]
                self.out_seq_q[f] = seq_sel[f]
                self.out_seq_valid_q[f] = seq_sel_valid[f]

        # sequence context: cleared on SOP, written on the beat in_seq_valid
        # marks. No in_ready term, so a beat held across a stall writes the
        # same value on every cycle of it.
        for f in range(self.n_feeds):
            if self.in_valid[f] and self.in_sop[f]:
                self.seq_valid_regs[f] = 0

            if self.in_valid[f] and self.in_seq_valid[f]:
                self.seq_regs[f] = self.in_seq[f]
                self.seq_valid_regs[f] = 1

        # completed packets table: every completion writes, circular
        if self.cmpl_valid:
            self.cpt_seq[self.cpt_wr_ptr] = self.cmpl_seq
            self.cpt_occupied[self.cpt_wr_ptr] = 1
            self.cpt_wr_ptr = (self.cpt_wr_ptr + 1) % self.cpt_depth

        return {
            "in_ready": self._in_ready(),
            "out_valid": list(self.out_valid_q),
            "out_data": list(self.out_data_q),
            "out_sop": list(self.out_sop_q),
            "out_eop": list(self.out_eop_q),
            "out_seq": list(self.out_seq_q),
            "out_seq_valid": list(self.out_seq_valid_q),
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
        self.in_seq = [0] * self.n_feeds
        self.in_seq_valid = [0] * self.n_feeds
        self.out_ready = [1] * self.n_feeds
        self.cmpl_valid = 0
        self.cmpl_seq = 0

    def present(self, feed, seq=None, data=None, sop=0, eop=0):
        """Stage one feed's stimulus for the coming cycle.

        Passing seq marks this beat as the one carrying the sequence number,
        which is what in_seq_valid means. It is independent of sop: the two
        land on the same beat only if a test asks for it.
        """
        self.in_valid[feed] = 1
        if data is None:
            self.in_data[feed] = 0
        else:
            self.in_data[feed] = data & DATA_MASK
        self.in_sop[feed] = sop
        self.in_eop[feed] = eop

        if seq is None:
            self.in_seq_valid[feed] = 0
        else:
            self.in_seq[feed] = seq & SEQ_MASK
            self.in_seq_valid[feed] = 1

    def idle_feeds(self):
        for f in range(self.n_feeds):
            self.in_valid[f] = 0
            self.in_sop[f] = 0
            self.in_eop[f] = 0
            self.in_seq_valid[f] = 0

    def complete(self, seq):
        self.cmpl_valid = 1
        self.cmpl_seq = seq & SEQ_MASK

    def _apply(self):
        d = self.dut
        d.in_valid.value = _pack(self.in_valid, 1)
        d.in_sop.value = _pack(self.in_sop, 1)
        d.in_eop.value = _pack(self.in_eop, 1)
        d.in_data_flat.value = _pack(self.in_data, DATA_W)
        d.in_seq_flat.value = _pack(self.in_seq, SEQ_W)
        d.in_seq_valid.value = _pack(self.in_seq_valid, 1)
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
            "out_seq_valid": _bits(int(d.out_seq_valid.value), self.n_feeds),
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
            in_seq=self.in_seq,
            in_seq_valid=self.in_seq_valid,
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
                assert got["out_seq_valid"][f] == expected["out_seq_valid"][f], (
                    f"out_seq_valid mismatch on feed {f}: got "
                    f"{got['out_seq_valid'][f]} "
                    f"expected {expected['out_seq_valid'][f]}"
                )
                if expected["out_seq_valid"][f]:
                    assert got["out_seq"][f] == expected["out_seq"][f], (
                        f"out_seq mismatch on feed {f}: got "
                        f"{got['out_seq'][f]:#x} "
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


async def send_packet(tb, feed, seq, beats, seq_beat):
    """Stream a whole packet on one feed, returning one sample per beat.

    The sequence number is delivered on beat `seq_beat`, the way seq_extract
    delivers it.

    in_ready is read after the edge, so it is the value for the coming cycle.
    It is held and used on the next pass to decide whether the beat that was
    just driven was taken. A refused beat is presented again.
    """
    samples = []
    in_ready = 1          # the block starts ready, so the first beat is taken
    i = 0

    while i < beats:
        if i == seq_beat:
            beat_seq = seq
        else:
            beat_seq = None

        tb.present(
            feed,
            seq=beat_seq,
            data=0xA0 + i,
            sop=1 if i == 0 else 0,
            eop=1 if i == beats - 1 else 0,
        )
        got = await tb.step()

        if in_ready:
            samples.append(got)
            i += 1

        in_ready = got["in_ready"][feed]

    return samples
