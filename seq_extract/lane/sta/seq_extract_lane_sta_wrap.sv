// -----------------------------------------------------------------------------
// seq_extract_lane_sta_wrap
//
// Synthesis harness for timing analysis only. Not part of the design.
//
// Every input is registered on the way in and every output is registered on
// the way out, so every path through seq_extract_lane starts at a flop and
// ends at a flop. Without this the port paths have no launch or capture point
// and Vivado reports nothing useful for them.
// -----------------------------------------------------------------------------

module seq_extract_lane_sta_wrap #(
    parameter int DATA_W     = 64,
    parameter int SEQ_W      = 32,
    parameter int SEQ_OFFSET = 0,
    parameter int BYTE_CNT_W = 3
) (
    input  logic                  clk,
    input  logic                  rst_n,

    input  logic                  in_valid,
    output logic                  in_ready,
    input  logic [DATA_W-1:0]     in_data,
    input  logic                  in_sop,
    input  logic                  in_eop,
    input  logic [BYTE_CNT_W-1:0] in_byte_cnt,

    output logic                  out_valid,
    input  logic                  out_ready,
    output logic [DATA_W-1:0]     out_data,
    output logic                  out_sop,
    output logic                  out_eop,
    output logic [BYTE_CNT_W-1:0] out_byte_cnt,

    output logic [SEQ_W-1:0]      out_seq,
    output logic                  out_seq_valid
);

    // -------------------------------------------------------------------------
    // input side flops
    // -------------------------------------------------------------------------
    logic                  in_valid_q;
    logic [DATA_W-1:0]     in_data_q;
    logic                  in_sop_q;
    logic                  in_eop_q;
    logic [BYTE_CNT_W-1:0] in_byte_cnt_q;
    logic                  out_ready_q;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            in_valid_q    <= 1'b0;
            in_data_q     <= '0;
            in_sop_q      <= 1'b0;
            in_eop_q      <= 1'b0;
            in_byte_cnt_q <= '0;
            out_ready_q   <= 1'b0;
        end else begin
            in_valid_q    <= in_valid;
            in_data_q     <= in_data;
            in_sop_q      <= in_sop;
            in_eop_q      <= in_eop;
            in_byte_cnt_q <= in_byte_cnt;
            out_ready_q   <= out_ready;
        end
    end

    // -------------------------------------------------------------------------
    // device under test
    // -------------------------------------------------------------------------
    logic                  dut_in_ready;
    logic                  dut_out_valid;
    logic [DATA_W-1:0]     dut_out_data;
    logic                  dut_out_sop;
    logic                  dut_out_eop;
    logic [BYTE_CNT_W-1:0] dut_out_byte_cnt;
    logic [SEQ_W-1:0]      dut_out_seq;
    logic                  dut_out_seq_valid;

    seq_extract_lane #(
        .DATA_W     (DATA_W),
        .SEQ_W      (SEQ_W),
        .SEQ_OFFSET (SEQ_OFFSET),
        .BYTE_CNT_W (BYTE_CNT_W)
    ) u_dut (
        .clk           (clk),
        .rst_n         (rst_n),

        .in_valid      (in_valid_q),
        .in_ready      (dut_in_ready),
        .in_data       (in_data_q),
        .in_sop        (in_sop_q),
        .in_eop        (in_eop_q),
        .in_byte_cnt   (in_byte_cnt_q),

        .out_valid     (dut_out_valid),
        .out_ready     (out_ready_q),
        .out_data      (dut_out_data),
        .out_sop       (dut_out_sop),
        .out_eop       (dut_out_eop),
        .out_byte_cnt  (dut_out_byte_cnt),

        .out_seq       (dut_out_seq),
        .out_seq_valid (dut_out_seq_valid)
    );

    // -------------------------------------------------------------------------
    // output side flops
    // -------------------------------------------------------------------------
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            in_ready      <= 1'b0;
            out_valid     <= 1'b0;
            out_data      <= '0;
            out_sop       <= 1'b0;
            out_eop       <= 1'b0;
            out_byte_cnt  <= '0;
            out_seq       <= '0;
            out_seq_valid <= 1'b0;
        end else begin
            in_ready      <= dut_in_ready;
            out_valid     <= dut_out_valid;
            out_data      <= dut_out_data;
            out_sop       <= dut_out_sop;
            out_eop       <= dut_out_eop;
            out_byte_cnt  <= dut_out_byte_cnt;
            out_seq       <= dut_out_seq;
            out_seq_valid <= dut_out_seq_valid;
        end
    end

endmodule
