// -----------------------------------------------------------------------------
// dedup_egress_sta_harness
//
// Synthesis harness. NOT design RTL and NOT part of the pipeline.
//
// dedup_egress holds a register inside it, so a standalone synthesis does have
// register to register paths. But the comparator tree is fed from the input
// port, and the drop feeds in_ready and out_valid, which are output ports.
// Without a harness those two ends have no assumed I/O budget and are not
// timed, so the numbers would cover only the internal paths.
//
// This harness flops every input and every output of dedup_egress. That turns
// both ends into real register to register paths, and the reported worst path
// is then the logic depth of the block itself.
//
// The flops here are an artefact of the measurement, not of the design.
// -----------------------------------------------------------------------------

module dedup_egress_sta_harness #(
    parameter int DATA_W    = 64,
    parameter int SEQ_W     = 32,
    parameter int CPT_DEPTH = 8
) (
    input  logic              clk,
    input  logic              rst_n,

    input  logic              in_valid,
    output logic              in_ready,
    input  logic [DATA_W-1:0] in_data,
    input  logic [SEQ_W-1:0]  in_seq,
    input  logic              in_sop,
    input  logic              in_eop,

    output logic              out_valid,
    input  logic              out_ready,
    output logic [DATA_W-1:0] out_data,
    output logic [SEQ_W-1:0]  out_seq,
    output logic              out_sop,
    output logic              out_eop,

    input  logic              cmpl_valid,
    input  logic [SEQ_W-1:0]  cmpl_seq
);

    // -------------------------------------------------------------------------
    // input side registers
    // -------------------------------------------------------------------------
    logic              in_valid_q;
    logic [DATA_W-1:0] in_data_q;
    logic [SEQ_W-1:0]  in_seq_q;
    logic              in_sop_q;
    logic              in_eop_q;
    logic              out_ready_q;
    logic              cmpl_valid_q;
    logic [SEQ_W-1:0]  cmpl_seq_q;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            in_valid_q   <= '0;
            in_data_q    <= '0;
            in_seq_q     <= '0;
            in_sop_q     <= '0;
            in_eop_q     <= '0;
            out_ready_q  <= '0;
            cmpl_valid_q <= '0;
            cmpl_seq_q   <= '0;
        end else begin
            in_valid_q   <= in_valid;
            in_data_q    <= in_data;
            in_seq_q     <= in_seq;
            in_sop_q     <= in_sop;
            in_eop_q     <= in_eop;
            out_ready_q  <= out_ready;
            cmpl_valid_q <= cmpl_valid;
            cmpl_seq_q   <= cmpl_seq;
        end
    end

    // -------------------------------------------------------------------------
    // device under test
    // -------------------------------------------------------------------------
    logic              dut_in_ready;
    logic              dut_out_valid;
    logic [DATA_W-1:0] dut_out_data;
    logic [SEQ_W-1:0]  dut_out_seq;
    logic              dut_out_sop;
    logic              dut_out_eop;

    dedup_egress #(
        .DATA_W    (DATA_W),
        .SEQ_W     (SEQ_W),
        .CPT_DEPTH (CPT_DEPTH)
    ) u_dedup_egress (
        .clk        (clk),
        .rst_n      (rst_n),

        .in_valid   (in_valid_q),
        .in_ready   (dut_in_ready),
        .in_data    (in_data_q),
        .in_seq     (in_seq_q),
        .in_sop     (in_sop_q),
        .in_eop     (in_eop_q),

        .out_valid  (dut_out_valid),
        .out_ready  (out_ready_q),
        .out_data   (dut_out_data),
        .out_seq    (dut_out_seq),
        .out_sop    (dut_out_sop),
        .out_eop    (dut_out_eop),

        .cmpl_valid (cmpl_valid_q),
        .cmpl_seq   (cmpl_seq_q)
    );

    // -------------------------------------------------------------------------
    // output side registers
    // -------------------------------------------------------------------------
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            in_ready  <= '0;
            out_valid <= '0;
            out_data  <= '0;
            out_seq   <= '0;
            out_sop   <= '0;
            out_eop   <= '0;
        end else begin
            in_ready  <= dut_in_ready;
            out_valid <= dut_out_valid;
            out_data  <= dut_out_data;
            out_seq   <= dut_out_seq;
            out_sop   <= dut_out_sop;
            out_eop   <= dut_out_eop;
        end
    end

endmodule
