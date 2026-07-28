# UDP Market-Data Parser, Design Overview

## 1. Purpose and scope

This document describes the architecture of a UDP market-data parser implemented on FPGA. It covers the parser as a whole: the design goals that shape it, the pipeline structure, the interface conventions its stages share, and the current implementation status of each stage.

The parser is under active development. Individual components are designed, implemented, and verified independently before integration, and each has its own documentation covering its internal microarchitecture in detail. This document stays at the system level and does not duplicate that per-component detail.

This design covers the transport layer: taking packets off redundant UDP feeds, removing duplicates, arbitrating between lines, and validating them. Decoding the exchange specific message format carried inside the payload is a separate concern and sits downstream of this pipeline.

It is written for engineers evaluating or working with the design: readers who want to understand what the parser is intended to do, how its stages fit together, and where the project currently stands.

## 2. Context

Exchanges distribute market data over UDP, typically as multiple redundant feeds. The same logical stream is published on several lines so that a receiver can tolerate loss on any one of them: if a packet is dropped or delayed on one feed, the same packet is expected to arrive on another.

In practice these feeds are not synchronised. Packets arrive at different times on different lines, the ordering between feeds is unpredictable, and the content can diverge for short periods when one feed falls behind or loses data. A receiver therefore cannot assume the lines are identical or aligned, and cannot simply select one and ignore the rest.

This shapes the parser in two ways. First, the design cannot depend on a single feed: it must select between them and be able to abandon one that stops delivering mid-packet. Second, that selection is held for the duration of a packet, since interleaving beats from different feeds would corrupt the output. The choice is therefore made per packet rather than per cycle, and is broken early only when the selected feed stalls beyond a threshold.

Because market data is latency-sensitive, both of these have to happen without deep buffering or pipeline stalls. The cost of waiting is measured in cycles.

## 3. Design goals

**Deterministic, low-latency handling.** Every stage is designed for fixed, predictable cycle behaviour rather than best-effort throughput. Data moves through the pipeline without deep buffering, and the path from input to output is short enough to reason about in cycles.

**Frequency target.** The design targets 325 MHz on a Xilinx Kintex-7. This is set high deliberately: it forces short combinational paths and shallow logic depth at every stage, which keeps per-stage latency low and leaves headroom for the pipeline to grow without renegotiating timing.

**No loss under backpressure.** Downstream stalls must never cause a beat to be dropped or duplicated. Each stage absorbs backpressure locally and propagates readiness upstream, so a stall degrades throughput but never correctness.

**No loss on unselected feeds.** A feed that is not currently being served must not lose data. Incoming beats are captured and held, and readiness is signalled back to the source so it knows when to hold off, ensuring that a feed passed over by arbitration can still be served correctly later.

**Packet integrity.** Packet boundaries are preserved end to end. Once a packet begins, its beats are delivered in order and without interleaving from another source.

**Modularity and independent verification.** Stages communicate through a common handshake and beat format, so each can be developed, verified, and timing-closed on its own before integration. This keeps verification tractable and makes it possible to reason about a stage in isolation.

**Parameterisation.** Feed count, data and sequence widths, and timing thresholds are parameters rather than hard-coded constants, so the design can be retargeted without structural change.

## 4. Pipeline architecture

The parser is organised as a chain of independent stages, each with a single responsibility. Data flows left to right through the main path, with two satellite blocks handling retry logic and a feedback path closing the loop back to the front of the pipeline.

![Pipeline architecture](images/pipeline.svg)

### Stages

**DEDUP** sits at the head of the pipeline. Because the feeds are redundant copies of the same stream arriving at unpredictable times, the same packet will appear on other feeds after it has already been served. DEDUP drops those late duplicates so a packet that has already been forwarded successfully does not enter the pipeline a second time.

**FEED_BUFFER** provides per-feed FIFO storage. The arbiter serves one feed at a time, so without buffering here the unselected feeds would immediately backpressure their sources. The FIFOs absorb incoming traffic while a feed waits its turn, and backpressure only propagates upstream when a FIFO genuinely fills.

The buffer is bypassed when it is not needed. If a feed's FIFO is empty and the arbiter is ready to accept, the data is forwarded straight through rather than written and read back, saving a cycle on the common path. This applies independently to every feed, so all feeds that can be forwarded are forwarded. Buffering begins as soon as the arbiter stops accepting, and once a FIFO holds data it keeps draining in order until empty, so ordering is never mixed between the bypass and stored paths.

FEED_BUFFER makes no selection decisions and has no knowledge of why the arbiter is or is not ready. It sees only readiness on its output, presents what it holds, and lets the arbiter choose.

Two inputs let the buffer discard data it no longer needs to hold. The arbiter drives an invalidate vector, one bit per feed, when it gives up on a stalled feed mid-packet: the buffer then clears that feed's remaining beats so the abandoned packet's tail is never forwarded as a fragment. Separately, the completion feedback from CHECKSUM tells the buffer which packets have already been served successfully, so it can drop copies of those packets still sitting in the other feeds' FIFOs.

Latency through this stage is a range rather than a fixed number: minimum on the bypass path, and bounded above by FIFO depth and how long a feed waits to be served.

**MARKET_LINE_ARBITER** picks which feed goes downstream, keeps that choice for the whole packet, and gives up on a feed that goes quiet in the middle of a packet for a parameterisable number of cycles. When it gives up, it sends an invalidate vector to FEED_BUFFER, one bit per feed, telling it to clear what is left of that packet so the leftover part is never sent on as a fragment.

**CHECKSUM** validates the served packet. Its result is the definition of success for the pipeline: a packet is only complete once its checksum passes, which is why the completion feedback originates here rather than at the arbiter.

### Satellite logic

**FIX_TRACKER** keeps track of packets whose checksum failed. CHECKSUM tells it which packet went bad, and from then on it watches the stream coming out of DEDUP for a fresh copy of that packet. As soon as a copy arrives on any feed, it tells the arbiter to prefer that feed.

It does not wait for the new copy to be validated first. Waiting for CHECKSUM to confirm the replacement would add a full round trip before the arbiter is even told the packet is available, so FIX_TRACKER acts on arrival instead. If the replacement also fails its checksum, CHECKSUM reports the failure again and FIX_TRACKER simply starts watching for the next copy. The process repeats until a good copy gets through or the timer runs out.

**TIMER** starts counting when a checksum fails. It gives the pipeline a set number of cycles to receive a good copy of that packet. If no good copy arrives in time, the timer stops waiting and an error is reported downstream. The number of cycles is a parameter.

**Completion feedback** runs from CHECKSUM back to DEDUP and FEED_BUFFER. It tells them a packet has been served successfully. DEDUP uses it to drop later copies of that packet, and FEED_BUFFER uses it to release copies it is still holding.

### Number of feeds

The design takes the feed count as a parameter, so it is not tied to a fixed number. In practice the number stays small.

Exchanges publish market data as redundant multicast lines, usually two identical copies called the A and B feeds, sent over separate network paths so that a packet lost on one can be recovered from the other. This A/B arrangement is the norm across venues such as NYSE Pillar, Nasdaq and OTC Markets. A receiver taking both lines from two sites ends up with four feeds, which is the realistic upper end.

Larger counts do not appear in practice, because the redundancy comes from having a few independent paths, not from having many copies. The design therefore targets up to four feeds, while keeping the count parameterisable.

### Pipeline register placement

The stages are split by what they do, not by how many cycles they take. Whether a register goes between two stages is decided later, from static timing analysis.

If two stages can run in the same cycle and still meet the target frequency, they share a cycle and save latency. If they can't, a register goes between them. At 325 MHz there isn't much time in a cycle, so merging two stages that both do real work is often not possible.

The same applies inside a stage. If one stage has too much logic to finish in a single cycle, it is split into more than one cycle. How many cycles each stage takes is decided when the design is implemented and timed, not before.

## 5. Component status

The parser is built one stage at a time. Each component is designed, implemented, verified, and timed on its own before it is integrated with the others. The table below shows where each stage currently stands.

| Component | Status |
|---|---|
| `market_line_arbiter` | Implemented, verified, timing closed |
| `dedup` | Planned |
| `feed_buffer` | Planned |
| `checksum` | Planned |
| `fix_tracker` | Planned |
| `timer` | Planned |

Only `market_line_arbiter` is complete. It has its own documentation covering its microarchitecture, verification, and timing results in detail.

The remaining stages are specified at the level described in this document but not yet written. Integration and end to end verification follow once the individual stages are in place.

## 6. Interfaces

All stages talk to each other the same way, so any stage can be connected to the next without special glue logic.

**Handshake.** Each connection uses a valid/ready pair. The sender raises valid when it has data. The receiver raises ready when it can accept. A beat moves on a clock edge only when both are high. A receiver that cannot accept lowers ready, and the sender holds its data steady until the beat is taken. This is what lets backpressure travel upstream without anything being lost.

**Beat format.** A packet is carried as one or more beats. Each beat carries the payload, its sequence number, and two boundary markers: start of packet and end of packet. A single beat packet has both markers set. The sequence number identifies which packet the beat belongs to, and is what DEDUP and FIX_TRACKER use to match copies of the same packet across different feeds.

**Per feed signalling.** Stages that handle several feeds at once carry the handshake per feed rather than for the group. A feed that is blocked does not stop the others, and readiness is reported back to each feed independently.

**Widths.** Payload width, sequence number width, and feed count are parameters. Stages are written against those parameters rather than fixed sizes, so the pipeline can be retargeted without changing the logic.

## 7. Verification approach

Each component is verified on its own before it is integrated. A stage is only considered done when it passes its own test suite and closes timing, so problems are found in the smallest possible context rather than after everything is wired together.

**Per component.** Verification uses cocotb with Verilator. Each stage has two layers of testing. Directed tests cover specific behaviours: normal operation, boundary cases, backpressure, and error handling. A constrained random test then drives the stage with randomised traffic and checks every output on every cycle against an independent golden model written in Python.

**Parameter sweeps.** Because thresholds and widths are parameters, the suites are run across a range of values rather than a single configuration. A design that works at one setting and breaks at another is not correct, and sweeping catches that.

The feed count is currently only exercised at four. That reflects what is realistic in practice, since exchanges publish a small number of redundant lines, but it is a gap in coverage rather than a proof of correctness. Claiming the design is properly parameterisable in feed count means running the suites at other values as well, for example one, two, three, five and eight. That work is outstanding.

**Integration.** Once several stages exist, they will be verified together end to end: full packets driven in at the feeds and checked at the output, including duplicate handling, feed dropout, and recovery. This layer is not built yet.

## 8. Future work

**Remaining stages.** DEDUP, FEED_BUFFER, CHECKSUM, FIX_TRACKER and TIMER are specified but not implemented. Each will be built the same way as the arbiter: designed, verified against a golden model, and timing closed on its own before integration. End to end verification of the assembled pipeline follows after that.

**Feed count coverage.** The verification suites currently run at four feeds. Running them across other values is needed before the design can be called parameterisable in feed count.

**Higher throughput.** The current target is 325 MHz on a 64-bit datapath. Production market data parsers run considerably faster, with figures around 644 MHz on a narrower 16-bit datapath used to sustain 10G line rate. Reaching that would mean rebalancing the pipeline for a much shorter cycle, which is a redesign rather than an optimisation.
