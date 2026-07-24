module feed_arbiter_tb_wrap #(
    parameter NUM_FEEDS     = 4,
    parameter FEED_ID_W     = $clog2(NUM_FEEDS),
    parameter HICCUP_CYCLES = 4,
    parameter SEQ_W         = 32,
    parameter DATA_W        = 64
)(
    input  logic                           clk,
    input  logic                           rst_n,

    input  logic [NUM_FEEDS-1:0]           feed_valid,
    input  logic [NUM_FEEDS-1:0]           feed_sop,
    input  logic [NUM_FEEDS-1:0]           feed_eop,
    input  logic [NUM_FEEDS*DATA_W-1:0]    feed_data_flat,
    input  logic [NUM_FEEDS*SEQ_W-1:0]     feed_seq_flat,

    input  logic                           out_ready,
    input  logic [NUM_FEEDS-1:0]           fix_avail,

    output logic [NUM_FEEDS-1:0]           in_ready,
    output logic [NUM_FEEDS-1:0]           invalidate_feed,

    output logic                           out_valid,
    output logic [DATA_W-1:0]              out_data,
    output logic [SEQ_W-1:0]               out_seq,
    output logic                           out_sop,
    output logic                           out_eop,
    output logic [FEED_ID_W-1:0]           out_feed,

    output logic [FEED_ID_W-1:0]           fix_served_feed,
    output logic                           fix_served_valid
);

    logic [DATA_W-1:0] feed_data [NUM_FEEDS];
    logic [SEQ_W-1:0]  feed_seq  [NUM_FEEDS];

    always_comb begin
        for (int i = 0; i < NUM_FEEDS; i++) begin
            feed_data[i] = feed_data_flat[i*DATA_W +: DATA_W];
            feed_seq[i]  = feed_seq_flat [i*SEQ_W  +: SEQ_W];
        end
    end

    feed_arbiter #(
        .NUM_FEEDS(NUM_FEEDS),
        .HICCUP_CYCLES(HICCUP_CYCLES),
        .SEQ_W(SEQ_W),
        .DATA_W(DATA_W)
    ) dut (
        .clk(clk),
        .rst_n(rst_n),
        .feed_valid(feed_valid),
        .feed_sop(feed_sop),
        .feed_eop(feed_eop),
        .feed_data(feed_data),
        .feed_seq(feed_seq),
        .out_ready(out_ready),
        .fix_avail(fix_avail),
        .in_ready(in_ready),
        .invalidate_feed(invalidate_feed),
        .out_valid(out_valid),
        .out_data(out_data),
        .out_seq(out_seq),
        .out_sop(out_sop),
        .out_eop(out_eop),
        .out_feed(out_feed),
        .fix_served_feed(fix_served_feed),
        .fix_served_valid(fix_served_valid)
    );

endmodule