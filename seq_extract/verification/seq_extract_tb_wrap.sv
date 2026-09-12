// -----------------------------------------------------------------------------
// seq_extract_tb_wrap
//
// Verification harness only. Not part of the design.
//
// cocotb cannot drive a packed two dimensional port, so every one of them is
// flattened into a single vector here. Feed f lives in bits [f*W : (f+1)*W) of
// each flat vector, which is the layout a packed array already has, so the
// mapping is a straight assignment.
// -----------------------------------------------------------------------------

module seq_extract_tb_wrap #(
    parameter int N_FEEDS    = 4,
    parameter int DATA_W     = 64,
    parameter int SEQ_W      = 32,
    parameter int SEQ_OFFSET = 0
) (
    input  logic                             clk,
    input  logic                             rst_n,

    input  logic [N_FEEDS-1:0]               in_valid,
    output logic [N_FEEDS-1:0]               in_ready,
    input  logic [N_FEEDS*DATA_W-1:0]        in_data_flat,
    input  logic [N_FEEDS-1:0]               in_sop,
    input  logic [N_FEEDS-1:0]               in_eop,
    input  logic [N_FEEDS*$clog2(DATA_W/8)-1:0] in_byte_cnt_flat,

    output logic [N_FEEDS-1:0]               out_valid,
    input  logic [N_FEEDS-1:0]               out_ready,
    output logic [N_FEEDS*DATA_W-1:0]        out_data_flat,
    output logic [N_FEEDS-1:0]               out_sop,
    output logic [N_FEEDS-1:0]               out_eop,
    output logic [N_FEEDS*$clog2(DATA_W/8)-1:0] out_byte_cnt_flat,

    output logic [N_FEEDS*SEQ_W-1:0]         out_seq_flat,
    output logic [N_FEEDS-1:0]               out_seq_valid
);

    localparam int BYTE_CNT_W = $clog2(DATA_W/8);

    logic [N_FEEDS-1:0][DATA_W-1:0]     in_data;
    logic [N_FEEDS-1:0][BYTE_CNT_W-1:0] in_byte_cnt;
    logic [N_FEEDS-1:0][DATA_W-1:0]     out_data;
    logic [N_FEEDS-1:0][BYTE_CNT_W-1:0] out_byte_cnt;
    logic [N_FEEDS-1:0][SEQ_W-1:0]      out_seq;

    always_comb begin
        for (int f = 0; f < N_FEEDS; f++) begin
            in_data[f]     = in_data_flat[f*DATA_W +: DATA_W];
            in_byte_cnt[f] = in_byte_cnt_flat[f*BYTE_CNT_W +: BYTE_CNT_W];

            out_data_flat[f*DATA_W +: DATA_W]             = out_data[f];
            out_byte_cnt_flat[f*BYTE_CNT_W +: BYTE_CNT_W] = out_byte_cnt[f];
            out_seq_flat[f*SEQ_W +: SEQ_W]                = out_seq[f];
        end
    end

    seq_extract #(
        .N_FEEDS    (N_FEEDS),
        .DATA_W     (DATA_W),
        .SEQ_W      (SEQ_W),
        .SEQ_OFFSET (SEQ_OFFSET)
    ) u_dut (
        .clk           (clk),
        .rst_n         (rst_n),

        .in_valid      (in_valid),
        .in_ready      (in_ready),
        .in_data       (in_data),
        .in_sop        (in_sop),
        .in_eop        (in_eop),
        .in_byte_cnt   (in_byte_cnt),

        .out_valid     (out_valid),
        .out_ready     (out_ready),
        .out_data      (out_data),
        .out_sop       (out_sop),
        .out_eop       (out_eop),
        .out_byte_cnt  (out_byte_cnt),

        .out_seq       (out_seq),
        .out_seq_valid (out_seq_valid)
    );

endmodule
