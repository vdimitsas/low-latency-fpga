# udp_parser

A low-latency **UDP market-data parser** in SystemVerilog, targeting FPGA
line-rate feed handling.

The design is organised as a set of modular pipeline stages. Each stage is
developed, verified and timing closed on its own before being integrated, so a
problem is found in the smallest possible context rather than after everything
is wired together.

## Components

| Component | Status | WNS at 325 MHz |
|---|---|---|
| `dedup_ingress` | Implemented, verified, timing closed | +0.179 ns |
| `feed_buffer` | Implemented, verified, timing closed | +0.688 ns |
| `market_line_arbiter` | Implemented, verified, timing closed | +0.298 ns |
| `checksum` | Planned | |
| `dedup_egress` | Planned | |
| `fix_tracker` | Planned | |
| `timer` | Planned | |

All figures are post synthesis on a Xilinx Kintex-7 `xc7k160tffg676-3`. Each
component's own README carries its worst path and how to reproduce it.

End to end results will be documented at this level once the remaining stages
exist and the pipeline is integrated.

## Repository layout

```
udp_parser/
├── docs/                 # system level design document and pipeline diagram
├── dedup_ingress/        # duplicate removal at the head of the pipeline
├── feed_buffer/          # per feed FIFO storage
├── market_line_arbiter/  # redundant feed arbitration
├── LICENSE
└── README.md
```

Every component folder has the same shape, so it is only described once here:
`rtl/` holds the design, `verification/` the cocotb suite, `sta/` the synthesis
and timing script, and `docs/` the design document with its diagrams. Each also
has its own `README.md` covering what the block does, its results, and how to
run its tests.

Start with [`docs/design.md`](docs/design.md) for the pipeline as a whole: what
each stage is for, how they connect, and how the design relates to published
work on FPGA market data feed handling.

## License

Released under the MIT License. See [LICENSE](LICENSE).
