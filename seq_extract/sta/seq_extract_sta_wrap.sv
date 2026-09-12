// -----------------------------------------------------------------------------
// seq_extract_sta_wrap
//
// Synthesis harness for timing analysis only. Not part of the design.
//
// Every input is registered on the way in and every output is registered on
// the way out, so every path through seq_extract starts at a flop and ends at
// a flop. Without this the port paths have no launch or capture point and
// Vivado reports nothing useful for them.
// -----------------------------------------------------------------------------

module seq_extract_sta_wrap #(
    parameter int N_FEEDS    = 4,
    parameter int DATA_W     = 64,
    parameter int SEQ_W      = 32,
    parameter int SEQ_OFFSET = 0
) (
    input  logic                                     clk,
    input  logic                                     rst_n,

    input  logic [N_FEEDS-1:0]                       in_valid,
    output logic [N_FEEDS-1:0]                       in_ready,
    input  logic [N_FEEDS-1:0][DATA_W-1:0]           in_data,
    input  logic [N_FEEDS-1:0]                       in_sop,
    input  logic [N_FEEDS-1:0]                       in_eop,
    input  logic [N_FEEDS-1:0][$clog2(DATA_W/8)-1:0] in_byte_cnt,

    output logic [N_FEEDS-1:0]                       out_valid,
    input  logic [N_FEEDS-1:0]                       out_ready,
    output logic [N_FEEDS-1:0][DATA_W-1:0]           out_data,
    output logic [N_FEEDS-1:0]                       out_sop,
    output logic [N_FEEDS-1:0]                       out_eop,
    output logic [N_FEEDS-1:0][$clog2(DATA_W/8)-1:0] out_byte_cnt,

    output logic [N_FEEDS-1:0][SEQ_W-1:0]            out_seq,
    output logic [N_FEEDS-1:0]                       out_seq_valid
);

    localparam int BYTE_CNT_W = $clog2(DATA_W/8);

    // -------------------------------------------------------------------------
    // input side flops
    // -------------------------------------------------------------------------
    logic [N_FEEDS-1:0]                 in_valid_q;
    logic [N_FEEDS-1:0][DATA_W-1:0]     in_data_q;
    logic [N_FEEDS-1:0]                 in_sop_q;
    logic [N_FEEDS-1:0]                 in_eop_q;
    logic [N_FEEDS-1:0][BYTE_CNT_W-1:0] in_byte_cnt_q;
    logic [N_FEEDS-1:0]                 out_ready_q;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            in_valid_q    <= '0;
            in_data_q     <= '0;
            in_sop_q      <= '0;
            in_eop_q      <= '0;
            in_byte_cnt_q <= '0;
            out_ready_q   <= '0;
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
    logic [N_FEEDS-1:0]                 dut_in_ready;
    logic [N_FEEDS-1:0]                 dut_out_valid;
    logic [N_FEEDS-1:0][DATA_W-1:0]     dut_out_data;
    logic [N_FEEDS-1:0]                 dut_out_sop;
    logic [N_FEEDS-1:0]                 dut_out_eop;
    logic [N_FEEDS-1:0][BYTE_CNT_W-1:0] dut_out_byte_cnt;
    logic [N_FEEDS-1:0][SEQ_W-1:0]      dut_out_seq;
    logic [N_FEEDS-1:0]                 dut_out_seq_valid;

    seq_extract #(
        .N_FEEDS    (N_FEEDS),
        .DATA_W     (DATA_W),
        .SEQ_W      (SEQ_W),
        .SEQ_OFFSET (SEQ_OFFSET)
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
            in_ready      <= '0;
            out_valid     <= '0;
            out_data      <= '0;
            out_sop       <= '0;
            out_eop       <= '0;
            out_byte_cnt  <= '0;
            out_seq       <= '0;
            out_seq_valid <= '0;
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
