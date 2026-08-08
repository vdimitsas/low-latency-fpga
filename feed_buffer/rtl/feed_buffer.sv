// -----------------------------------------------------------------------------
// feed_buffer
//
// Per feed FIFO storage between DEDUP_INGRESS and MARKET_LINE_ARBITER.
//
// The arbiter serves one feed at a time. Without storage here the feeds it is
// not serving would immediately backpressure their sources. Each feed gets its
// own FIFO, bypass path and output register, so a feed waiting its turn never
// blocks another.
//
// This block holds no completed packets table. A completion arrives after the
// arbiter has already served the copy, so those beats are past this block and
// cannot be recalled here. DEDUP_EGRESS stops them at the tail of the pipeline,
// and since it is needed anyway a table here would catch nothing extra.
// -----------------------------------------------------------------------------

module feed_buffer #(
    parameter int N_FEEDS    = 4,
    parameter int DATA_W     = 64,
    parameter int SEQ_W      = 32,
    parameter int FIFO_DEPTH = 32
) (
    input  logic                           clk,
    input  logic                           rst_n,

    // from DEDUP_INGRESS
    input  logic [N_FEEDS-1:0]             in_valid,
    output logic [N_FEEDS-1:0]             in_ready,
    input  logic [N_FEEDS-1:0][DATA_W-1:0] in_data,
    input  logic [N_FEEDS-1:0][SEQ_W-1:0]  in_seq,
    input  logic [N_FEEDS-1:0]             in_sop,
    input  logic [N_FEEDS-1:0]             in_eop,

    // to MARKET_LINE_ARBITER
    output logic [N_FEEDS-1:0]             out_valid,
    input  logic [N_FEEDS-1:0]             out_ready,
    output logic [N_FEEDS-1:0][DATA_W-1:0] out_data,
    output logic [N_FEEDS-1:0][SEQ_W-1:0]  out_seq,
    output logic [N_FEEDS-1:0]             out_sop,
    output logic [N_FEEDS-1:0]             out_eop,

    // from MARKET_LINE_ARBITER
    input  logic [N_FEEDS-1:0]             invalidate_feed
);

    initial begin
        if (N_FEEDS < 1)
            $error("feed_buffer: N_FEEDS must be at least 1");
        if (FIFO_DEPTH < 2)
            $error("feed_buffer: FIFO_DEPTH must be at least 2");
        if (FIFO_DEPTH != (1 << $clog2(FIFO_DEPTH)))
            $error("feed_buffer: FIFO_DEPTH must be a power of two");
    end

    typedef struct packed {
        logic [DATA_W-1:0] data;
        logic [SEQ_W-1:0]  seq;
        logic              sop;
        logic              eop;
    } beat_t;

    localparam int BEAT_W = $bits(beat_t);

    logic [N_FEEDS-1:0] invalidated;
    beat_t              out_reg   [N_FEEDS];
    logic [N_FEEDS-1:0] out_reg_valid;

    generate
        for (genvar f = 0; f < N_FEEDS; f++) begin : g_feed

            beat_t in_beat;
            assign in_beat.data = in_data[f];
            assign in_beat.seq  = in_seq[f];
            assign in_beat.sop  = in_sop[f];
            assign in_beat.eop  = in_eop[f];

            // -----------------------------------------------------------------
            // sticky invalidate
            //
            // The arbiter gives up on a feed only after it has gone silent,
            // which happens only once its FIFO has run empty. So the beats to
            // discard are not ones already held: they are the tail of the
            // abandoned packet arriving late.
            //
            // Set combinationally, so a beat arriving in the same cycle as
            // invalidate_feed is already dropped. Cleared on EOP, the end of
            // that tail, and on SOP, a fresh packet. Without EOP a feed that
            // recovers mid packet stays dead. Without SOP a tail that never
            // arrives leaves the bit set and swallows the packet behind it.
            //
            // A SOP beat is accepted, not dropped.
            // -----------------------------------------------------------------
            logic drop;
            assign drop = (invalidate_feed[f] || invalidated[f]) && !in_sop[f];

            always_ff @(posedge clk or negedge rst_n) begin
                if (!rst_n) begin
                    invalidated[f] <= 1'b0;
                end else if (in_valid[f] && in_ready[f] &&
                             (in_sop[f] || in_eop[f])) begin
                    invalidated[f] <= 1'b0;
                end else if (invalidate_feed[f]) begin
                    invalidated[f] <= 1'b1;
                end
            end

            logic              fifo_wr_en;
            logic              fifo_rd_en;
            logic              fifo_full;
            logic              fifo_empty;
            logic [BEAT_W-1:0] fifo_rd_data;

            sync_fifo #(
                .WIDTH (BEAT_W),
                .DEPTH (FIFO_DEPTH)
            ) u_fifo (
                .clk     (clk),
                .rst_n   (rst_n),
                .wr_en   (fifo_wr_en),
                .wr_data (in_beat),
                .full    (fifo_full),
                .rd_en   (fifo_rd_en),
                .rd_data (fifo_rd_data),
                .empty   (fifo_empty)
            );

            // -----------------------------------------------------------------
            // flow control
            //
            // in_ready is FIFO occupancy alone. Letting the arbiter's out_ready
            // into it would run a combinational path from the arbiter back
            // through this block into DEDUP_INGRESS, which has no margin.
            // -----------------------------------------------------------------
            assign in_ready[f] = !fifo_full;

            logic out_reg_free;
            assign out_reg_free = !out_reg_valid[f] || out_ready[f];

            // -----------------------------------------------------------------
            // bypass
            //
            // A beat arriving to an empty FIFO with the output register free
            // goes straight into that register, saving the write then read
            // round trip. fifo_empty is in the condition so a beat can never
            // overtake one already queued.
            // -----------------------------------------------------------------
            logic bypass;
            assign bypass = in_valid[f] && in_ready[f] && !drop &&
                            fifo_empty && out_reg_free;

            assign fifo_wr_en = in_valid[f] && in_ready[f] && !drop && !bypass;
            assign fifo_rd_en = !fifo_empty && out_reg_free;

            // -----------------------------------------------------------------
            // output register
            //
            // Registered so out_ready never reaches back into this block's own
            // ready path. Holds its contents while the arbiter is not ready.
            // -----------------------------------------------------------------
            always_ff @(posedge clk or negedge rst_n) begin
                if (!rst_n) begin
                    out_reg[f]       <= '0;
                    out_reg_valid[f] <= 1'b0;
                end else if (out_reg_free) begin
                    if (!fifo_empty) begin
                        out_reg[f]       <= beat_t'(fifo_rd_data);
                        out_reg_valid[f] <= 1'b1;
                    end else if (bypass) begin
                        out_reg[f]       <= in_beat;
                        out_reg_valid[f] <= 1'b1;
                    end else begin
                        out_reg_valid[f] <= 1'b0;
                    end
                end
            end

            assign out_valid[f] = out_reg_valid[f];
            assign out_data[f]  = out_reg[f].data;
            assign out_seq[f]   = out_reg[f].seq;
            assign out_sop[f]   = out_reg[f].sop;
            assign out_eop[f]   = out_reg[f].eop;

        end
    endgenerate

endmodule
