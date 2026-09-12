// -----------------------------------------------------------------------------
// seq_extract
//
// Front end of the parser. One lane per feed, each pulling the sequence number
// out of its own beat stream. Nothing crosses between feeds here.
// -----------------------------------------------------------------------------

module seq_extract #(
    parameter int N_FEEDS    = 4,
    parameter int DATA_W     = 64,
    parameter int SEQ_W      = 32,
    parameter int SEQ_OFFSET = 0
) (
    input  logic                                     clk,
    input  logic                                     rst_n,

    // feed inputs
    input  logic [N_FEEDS-1:0]                       in_valid,
    output logic [N_FEEDS-1:0]                       in_ready,
    input  logic [N_FEEDS-1:0][DATA_W-1:0]           in_data,
    input  logic [N_FEEDS-1:0]                       in_sop,
    input  logic [N_FEEDS-1:0]                       in_eop,
    input  logic [N_FEEDS-1:0][$clog2(DATA_W/8)-1:0] in_byte_cnt,

    // feed outputs
    output logic [N_FEEDS-1:0]                       out_valid,
    input  logic [N_FEEDS-1:0]                       out_ready,
    output logic [N_FEEDS-1:0][DATA_W-1:0]           out_data,
    output logic [N_FEEDS-1:0]                       out_sop,
    output logic [N_FEEDS-1:0]                       out_eop,
    output logic [N_FEEDS-1:0][$clog2(DATA_W/8)-1:0] out_byte_cnt,

    // extracted metadata
    output logic [N_FEEDS-1:0][SEQ_W-1:0]            out_seq,
    output logic [N_FEEDS-1:0]                       out_seq_valid
);

    localparam int BYTE_CNT_W = $clog2(DATA_W/8);

    // -------------------------------------------------------------------------
    // one lane per feed
    //
    // Feeds are independent. A lane sees only its own stream, so a stall or a
    // packet boundary on one feed has no effect on any other.
    // -------------------------------------------------------------------------
    generate
        for (genvar g = 0; g < N_FEEDS; g++) begin : g_lane
            seq_extract_lane #(
                .DATA_W     (DATA_W),
                .SEQ_W      (SEQ_W),
                .SEQ_OFFSET (SEQ_OFFSET),
                .BYTE_CNT_W (BYTE_CNT_W)
            ) u_lane (
                .clk           (clk),
                .rst_n         (rst_n),

                .in_valid      (in_valid[g]),
                .in_ready      (in_ready[g]),
                .in_data       (in_data[g]),
                .in_sop        (in_sop[g]),
                .in_eop        (in_eop[g]),
                .in_byte_cnt   (in_byte_cnt[g]),

                .out_valid     (out_valid[g]),
                .out_ready     (out_ready[g]),
                .out_data      (out_data[g]),
                .out_sop       (out_sop[g]),
                .out_eop       (out_eop[g]),
                .out_byte_cnt  (out_byte_cnt[g]),

                .out_seq       (out_seq[g]),
                .out_seq_valid (out_seq_valid[g])
            );
        end
    endgenerate

endmodule
