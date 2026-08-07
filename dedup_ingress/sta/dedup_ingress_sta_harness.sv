// -----------------------------------------------------------------------------
// dedup_ingress_sta_harness
//
// Synthesis harness. NOT design RTL and NOT part of the pipeline.
//
// dedup_ingress is a cut-through block: its datapath runs from input port to output
// port with no register in between. A standalone synthesis of dedup_ingress therefore
// contains no register to register path through the comparator tree, so STA
// has nothing to time and reports only the CPT bookkeeping logic.
//
// This harness flops every input and every output of dedup_ingress. That turns the
// combinational datapath into a real register to register path, so STA
// measures the logic depth of the block itself with no assumed I/O budget.
//
// The flops here are an artefact of the measurement, not of the design.
// -----------------------------------------------------------------------------

module dedup_ingress_sta_harness #(
    parameter int N_FEEDS    = 4,
    parameter int DATA_W     = 64,
    parameter int SEQ_W      = 32,
    parameter int SEQ_OFFSET = 0,
    parameter int CPT_DEPTH  = 8
) (
    input  logic                           clk,
    input  logic                           rst_n,

    input  logic [N_FEEDS-1:0]             in_valid,
    output logic [N_FEEDS-1:0]             in_ready,
    input  logic [N_FEEDS-1:0][DATA_W-1:0] in_data,
    input  logic [N_FEEDS-1:0]             in_sop,
    input  logic [N_FEEDS-1:0]             in_eop,

    output logic [N_FEEDS-1:0]             out_valid,
    input  logic [N_FEEDS-1:0]             out_ready,
    output logic [N_FEEDS-1:0][DATA_W-1:0] out_data,
    output logic [N_FEEDS-1:0]             out_sop,
    output logic [N_FEEDS-1:0]             out_eop,
    output logic [N_FEEDS-1:0][SEQ_W-1:0]  out_seq,

    input  logic                           cmpl_valid,
    input  logic [SEQ_W-1:0]               cmpl_seq
);

    // -------------------------------------------------------------------------
    // input side registers
    // -------------------------------------------------------------------------
    logic [N_FEEDS-1:0]             in_valid_q;
    logic [N_FEEDS-1:0][DATA_W-1:0] in_data_q;
    logic [N_FEEDS-1:0]             in_sop_q;
    logic [N_FEEDS-1:0]             in_eop_q;
    logic [N_FEEDS-1:0]             out_ready_q;
    logic                           cmpl_valid_q;
    logic [SEQ_W-1:0]               cmpl_seq_q;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            in_valid_q   <= '0;
            in_data_q    <= '0;
            in_sop_q     <= '0;
            in_eop_q     <= '0;
            out_ready_q  <= '0;
            cmpl_valid_q <= '0;
            cmpl_seq_q   <= '0;
        end else begin
            in_valid_q   <= in_valid;
            in_data_q    <= in_data;
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
    logic [N_FEEDS-1:0]             dut_in_ready;
    logic [N_FEEDS-1:0]             dut_out_valid;
    logic [N_FEEDS-1:0][DATA_W-1:0] dut_out_data;
    logic [N_FEEDS-1:0]             dut_out_sop;
    logic [N_FEEDS-1:0]             dut_out_eop;
    logic [N_FEEDS-1:0][SEQ_W-1:0]  dut_out_seq;

    dedup_ingress #(
        .N_FEEDS    (N_FEEDS),
        .DATA_W     (DATA_W),
        .SEQ_W      (SEQ_W),
        .SEQ_OFFSET (SEQ_OFFSET),
        .CPT_DEPTH  (CPT_DEPTH)
    ) u_dedup_ingress (
        .clk        (clk),
        .rst_n      (rst_n),

        .in_valid   (in_valid_q),
        .in_ready   (dut_in_ready),
        .in_data    (in_data_q),
        .in_sop     (in_sop_q),
        .in_eop     (in_eop_q),

        .out_valid  (dut_out_valid),
        .out_ready  (out_ready_q),
        .out_data   (dut_out_data),
        .out_sop    (dut_out_sop),
        .out_eop    (dut_out_eop),
        .out_seq    (dut_out_seq),

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
            out_sop   <= '0;
            out_eop   <= '0;
            out_seq   <= '0;
        end else begin
            in_ready  <= dut_in_ready;
            out_valid <= dut_out_valid;
            out_data  <= dut_out_data;
            out_sop   <= dut_out_sop;
            out_eop   <= dut_out_eop;
            out_seq   <= dut_out_seq;
        end
    end

endmodule
