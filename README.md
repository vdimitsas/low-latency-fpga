# udp_parser

A low-latency **UDP market-data parser** in SystemVerilog, targeting FPGA
line-rate feed handling.

The design is organised as a set of modular pipeline stages. Each stage lives in
its own folder with its own RTL, verification suite, and README, so components
can be developed and verified independently before being integrated end to end.

## Components

| Component      | Status                                    |
|----------------|-------------------------------------------|
| `dedup_ingress` | Implemented, timing closed, and verified. |
| `market_line_arbiter` | Implemented, timing closed, and verified. |

Further pipeline stages are in development and will be added as their own
components. Whole-project (end-to-end) results will be documented at this top
level once the stages are integrated; until then, each component's own results
live in that component's README.

## Repository layout

```
udp_parser/
├── docs/                  # project-level documentation (end-to-end)
├── dedup_ingress/         # duplicate removal at the head of the pipeline
│   ├── README.md
│   ├── docs/
│   ├── rtl/
│   ├── sta/
│   └── verification/
├── market_line_arbiter/    # redundant-feed arbitration stage
│   ├── README.md          # component description, results, how to run
│   ├── docs/              # component design notes & timing-closure report
│   ├── rtl/
│   └── verification/
├── LICENSE
└── README.md
```

## License

Released under the MIT License. See [LICENSE](LICENSE).
