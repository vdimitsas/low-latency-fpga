"""Test 10: flow control.

in_ready is out_ready ORed with the drop, plus an empty stage term. A beat that
is being dropped needs no room downstream, so it is accepted even while
out_ready is low. A beat that is passing still follows out_ready. A stage
holding nothing always accepts.

The ready equation is three terms:

    in_ready = ~valid_q | out_ready | drop

The first term means an empty stage accepts regardless of out_ready. So the
block only follows out_ready once it is actually holding a beat. Every test
here loads a beat first before checking the stall behaviour.

drop is registered, so it reflects the beat sitting in the pipeline register,
not the beat being presented. A duplicate has to be accepted in one cycle
before its drop shows up on in_ready in the next.
"""

import cocotb

from dedup_egress_common import DedupEgressTB


@cocotb.test()
async def test_empty_stage_always_accepts(dut):
    """The first term on its own: nothing held, downstream closed."""
    tb = DedupEgressTB(dut)
    await tb.start()

    tb.out_ready = 0
    got = await tb.step()
    assert got["in_ready"] == 1, "an empty stage refused a beat"


@cocotb.test()
async def test_in_ready_follows_out_ready(dut):
    """10. With a beat held and nothing dropped, out_ready is the only term.

    The stage is kept holding a beat throughout, so the empty stage term is
    never what is driving in_ready. The CPT is empty so nothing is dropped,
    which leaves out_ready as the only live term.
    """
    tb = DedupEgressTB(dut)
    await tb.start()

    # Load the stage so valid_q is set and it is no longer empty.
    tb.out_ready = 1
    tb.present(seq=0x5000, sop=1)
    await tb.step()

    for pattern in (1, 0, 1, 0, 0, 1):
        tb.out_ready = pattern
        # Keep offering a beat so the stage never drains empty.
        tb.present(seq=0x5000, data=0x55)
        got = await tb.step()
        assert got["in_ready"] == pattern, (
            f"in_ready {got['in_ready']} did not follow out_ready {pattern}"
        )


@cocotb.test()
async def test_drop_lifts_ready_when_downstream_is_full(dut):
    """10b. A dropped copy is accepted whatever out_ready says.

    A beat that is being dropped needs no room downstream, so the block
    releases it even while out_ready is low. This puts the match result on the
    ready path deliberately, see the flow control section of the design
    document.
    """
    tb = DedupEgressTB(dut)
    await tb.start()

    seq = 0xAB
    tb.complete(seq)
    await tb.step()

    # Accept a copy that will be dropped.
    tb.out_ready = 1
    tb.present(seq=seq, sop=1)
    await tb.step()

    # It is now in the pipeline register. Close downstream completely: the
    # drop alone has to hold in_ready up.
    tb.out_ready = 0
    got = await tb.step()
    assert got["out_valid"] == 0, "the duplicate should have been dropped"
    assert got["in_ready"] == 1, "a dropped copy was held off by out_ready"

    # A passing copy still follows out_ready.
    tb.out_ready = 1
    tb.present(seq=0xCD, sop=1)
    await tb.step()

    tb.out_ready = 0
    tb.present(seq=0xCD, data=0x99)
    got = await tb.step()
    assert got["out_valid"] == 1, "a non matching copy should have passed"
    assert got["in_ready"] == 0, (
        "in_ready did not follow out_ready on a passing beat"
    )


@cocotb.test()
async def test_packet_survives_backpressure(dut):
    """A whole packet gets through with downstream open every other cycle."""
    tb = DedupEgressTB(dut)
    await tb.start()

    seq = 0x7A7A
    beats = 4
    samples = []
    cycle = 0

    for i in range(beats):
        # Offer the beat until it is taken. out_ready alternates, so it is
        # refused about half the time.
        while True:
            tb.out_ready = cycle % 2
            tb.present(
                seq=seq,
                data=0xB0 + i,
                sop=1 if i == 0 else 0,
                eop=1 if i == beats - 1 else 0,
            )
            got = await tb.step()
            cycle += 1
            if got["in_ready"]:
                break

        tb.out_ready = 1
        samples.append(await tb.step())
        cycle += 1

    for i, s in enumerate(samples):
        assert s["out_valid"] == 1, f"beat {i} was lost under backpressure"
        assert s["out_seq"] == seq, f"beat {i} carried the wrong seq"
