// -----------------------------------------------------------------------------
// feed_buffer_tb_wrap
//
// Verification wrapper. NOT design RTL.
//
// feed_buffer uses packed two dimensional ports of the form
// [N_FEEDS-1:0][W-1:0]. Those are awkward to reach from cocotb, so this
// wrapper presents each of them as a single flat vector and does the slicing
// internally.
//
// Feed f occupies bits [f*W +: W] of the flat vector.
// -----------------------------------------------------------------------------

module feed_buffer_tb_wrap #(
    parameter int N_FEEDS    = 4,
    parameter int DATA_W     = 64,
    parameter int SEQ_W      = 32,
    parameter int FIFO_DEPTH = 32
) (
    input  logic                      clk,
    input  logic                      rst_n,

    input  logic [N_FEEDS-1:0]        in_valid,
    output logic [N_FEEDS-1:0]        in_ready,
    input  logic [N_FEEDS*DATA_W-1:0] in_data_flat,
    input  logic [N_FEEDS*SEQ_W-1:0]  in_seq_flat,
    input  logic [N_FEEDS-1:0]        in_sop,
    input  logic [N_FEEDS-1:0]        in_eop,

    output logic [N_FEEDS-1:0]        out_valid,
    input  logic [N_FEEDS-1:0]        out_ready,
    output logic [N_FEEDS*DATA_W-1:0] out_data_flat,
    output logic [N_FEEDS*SEQ_W-1:0]  out_seq_flat,
    output logic [N_FEEDS-1:0]        out_sop,
    output logic [N_FEEDS-1:0]        out_eop,

    input  logic [N_FEEDS-1:0]        invalidate_feed
);

    logic [N_FEEDS-1:0][DATA_W-1:0] in_data;
    logic [N_FEEDS-1:0][SEQ_W-1:0]  in_seq;
    logic [N_FEEDS-1:0][DATA_W-1:0] out_data;
    logic [N_FEEDS-1:0][SEQ_W-1:0]  out_seq;

    always_comb begin
        for (int f = 0; f < N_FEEDS; f++) begin
            in_data[f] = in_data_flat[f*DATA_W +: DATA_W];
            in_seq[f]  = in_seq_flat[f*SEQ_W +: SEQ_W];
        end
    end

    always_comb begin
        for (int f = 0; f < N_FEEDS; f++) begin
            out_data_flat[f*DATA_W +: DATA_W] = out_data[f];
            out_seq_flat[f*SEQ_W  +: SEQ_W]   = out_seq[f];
        end
    end

    feed_buffer #(
        .N_FEEDS    (N_FEEDS),
        .DATA_W     (DATA_W),
        .SEQ_W      (SEQ_W),
        .FIFO_DEPTH (FIFO_DEPTH)
    ) u_feed_buffer (
        .clk             (clk),
        .rst_n           (rst_n),

        .in_valid        (in_valid),
        .in_ready        (in_ready),
        .in_data         (in_data),
        .in_seq          (in_seq),
        .in_sop          (in_sop),
        .in_eop          (in_eop),

        .out_valid       (out_valid),
        .out_ready       (out_ready),
        .out_data        (out_data),
        .out_seq         (out_seq),
        .out_sop         (out_sop),
        .out_eop         (out_eop),

        .invalidate_feed (invalidate_feed)
    );

endmodule
