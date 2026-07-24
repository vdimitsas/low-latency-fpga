module feed_arbiter #(
    parameter NUM_FEEDS     = 4,
    parameter FEED_ID_W     = $clog2(NUM_FEEDS),
    parameter HICCUP_CYCLES = 4,
    parameter HICCUP_W      = $clog2(HICCUP_CYCLES + 1),
    parameter SEQ_W         = 32,
    parameter DATA_W        = 64
)(
    input  logic                 clk,
    input  logic                 rst_n,
    input  logic [NUM_FEEDS-1:0] feed_valid,
    input  logic [NUM_FEEDS-1:0] feed_sop,
    input  logic [NUM_FEEDS-1:0] feed_eop,
    input  logic [DATA_W-1:0]    feed_data [NUM_FEEDS],
    input  logic [SEQ_W-1:0]     feed_seq  [NUM_FEEDS],
    input  logic                 out_ready,
    input  logic [NUM_FEEDS-1:0] fix_avail,
    output logic [NUM_FEEDS-1:0] in_ready,
    output logic [NUM_FEEDS-1:0] invalidate_feed,
    output logic                 out_valid,
    output logic [DATA_W-1:0]    out_data,
    output logic [SEQ_W-1:0]     out_seq,
    output logic                 out_sop,
    output logic                 out_eop,
    output logic [FEED_ID_W-1:0] out_feed,
    output logic [FEED_ID_W-1:0] fix_served_feed,
    output logic                 fix_served_valid
);

localparam logic [HICCUP_W-1:0] ARM_CNT = HICCUP_W'(HICCUP_CYCLES - 3);

// synthesis translate_off
initial begin
    if (HICCUP_CYCLES < 3) begin
        $error("feed_arbiter: HICCUP_CYCLES must be >= 3 (got %0d). ",
               HICCUP_CYCLES);
        $error("Thresholds of 1 or 2 need a different invalidate path ");
        $error("(no spare cycle to pre-arm) — not supported by this design.");
    end
end
// synthesis translate_on

logic [NUM_FEEDS-1:0] serve_feed;
logic [NUM_FEEDS-1:0] serve_feed_q;
logic serve_sop_c,   serve_eop_c,   serve_valid_c;
logic serve_sop_q,   serve_eop_q,   serve_valid_q;
logic [SEQ_W-1:0]  serve_seq_c,  serve_seq_q;
logic [DATA_W-1:0] serve_data_c, serve_data_q;

logic [NUM_FEEDS-1:0] eff_valid, eff_sop, eff_eop;
logic [SEQ_W-1:0]     eff_seq  [NUM_FEEDS];
logic [DATA_W-1:0]    eff_data [NUM_FEEDS];

logic [NUM_FEEDS-1:0] skid_valid,     skid_valid_nxt;
logic [DATA_W-1:0]    skid_data      [NUM_FEEDS];
logic [DATA_W-1:0]    skid_data_nxt  [NUM_FEEDS];
logic [NUM_FEEDS-1:0] skid_sop,       skid_sop_nxt;
logic [NUM_FEEDS-1:0] skid_eop,       skid_eop_nxt;
logic [SEQ_W-1:0]     skid_seq       [NUM_FEEDS];
logic [SEQ_W-1:0]     skid_seq_nxt   [NUM_FEEDS];

logic [HICCUP_W-1:0]  hiccup_cnt, hiccup_cnt_nxt;
logic                 giveup;
// invalidate_q: per-feed — records WHICH feed is armed one cycle
// before confirm. Drives invalidate_feed's live-gate check only.
logic [NUM_FEEDS-1:0] invalidate_q;
// invalidate_feed_q: faithful one-cycle-delayed record of
// invalidate_feed itself (NOT re-gated by out_ready — invalidate_feed
// already accounts for it once; re-gating here would distort history).
// Used ONLY to release sticky the cycle after a confirmed giveup,
// and only if that same feed is still genuinely empty.
logic [NUM_FEEDS-1:0] invalidate_feed_q;

// =================================================================
// STAGE 1 — all live/combinational
// =================================================================

always_comb begin : eff_view
    for (int i = 0; i < NUM_FEEDS; i++) begin
        eff_valid[i] = skid_valid[i] || feed_valid[i];
        eff_sop[i]   = skid_valid[i] ? skid_sop[i]  : feed_sop[i];
        eff_eop[i]   = skid_valid[i] ? skid_eop[i]  : feed_eop[i];
        eff_seq[i]   = skid_valid[i] ? skid_seq[i]  : feed_seq[i];
        eff_data[i]  = skid_valid[i] ? skid_data[i] : feed_data[i];
    end
end

// ---- serve_mux: sticky HOLDS the parked feed through the entire
//      confirm cycle (so invalidate_feed's live check still matches
//      serve_feed and fires correctly, and the dead chunk's "no
//      data" state correctly passes through as this feed's own
//      beat). Only releases the CYCLE AFTER a confirmed giveup —
//      via invalidate_feed_q — and only if that feed is STILL
//      empty then. Else: fix preference, else lowest-index feed
//      with data. ----
always_comb begin : serve_mux
    automatic logic sticky;
    automatic logic picked;

    sticky = |serve_feed_q && !serve_eop_q &&
             !(|(invalidate_feed_q & ~eff_valid));

    serve_feed = serve_feed_q;
    picked     = 1'b0;

    if (!sticky) begin
        for (int i = 0; i < NUM_FEEDS; i++)
            if (!picked && fix_avail[i]) begin
                serve_feed = NUM_FEEDS'(1) << i;
                picked     = 1'b1;
            end
        for (int i = 0; i < NUM_FEEDS; i++)
            if (!picked && eff_valid[i]) begin
                serve_feed = NUM_FEEDS'(1) << i;
                picked     = 1'b1;
            end
    end

    serve_valid_c = |(serve_feed & eff_valid);
    serve_sop_c   = |(serve_feed & eff_sop);
    serve_eop_c   = |(serve_feed & eff_eop);
    // OR-reduction instead of sequential priority chain — flattens
    // the mux from 4 LUT levels down to ~2, since it no longer
    // depends on evaluation order (serve_feed is always one-hot)
    serve_seq_c  = '0;
    serve_data_c = '0;
    for (int i = 0; i < NUM_FEEDS; i++) begin
        serve_seq_c  |= eff_seq[i]  & {SEQ_W{serve_feed[i]}};
        serve_data_c |= eff_data[i] & {DATA_W{serve_feed[i]}};
    end
end

always_comb begin : skid_upd
    for (int i = 0; i < NUM_FEEDS; i++) begin
        skid_valid_nxt[i] = skid_valid[i];
        skid_data_nxt[i]  = skid_data[i];
        skid_sop_nxt[i]   = skid_sop[i];
        skid_eop_nxt[i]   = skid_eop[i];
        skid_seq_nxt[i]   = skid_seq[i];

        if (skid_valid[i]) begin
            if (serve_feed[i] && out_ready) begin
                if (feed_valid[i] && in_ready[i]) begin
                    skid_data_nxt[i] = feed_data[i];
                    skid_sop_nxt[i]  = feed_sop[i];
                    skid_eop_nxt[i]  = feed_eop[i];
                    skid_seq_nxt[i]  = feed_seq[i];
                end else begin
                    skid_valid_nxt[i] = 1'b0;
                end
            end
        end else if (feed_valid[i] && !(serve_feed[i] && out_ready)) begin
            skid_valid_nxt[i] = 1'b1;
            skid_data_nxt[i]  = feed_data[i];
            skid_sop_nxt[i]   = feed_sop[i];
            skid_eop_nxt[i]   = feed_eop[i];
            skid_seq_nxt[i]   = feed_seq[i];
        end
    end
end

always_comb begin : in_ready_upd
    for (int i = 0; i < NUM_FEEDS; i++)
        in_ready[i] = !skid_valid[i] || (serve_feed[i] && out_ready);
end

// =================================================================
// STAGE 1 -> STAGE 2 flop
// =================================================================
always_ff @(posedge clk) begin : stage1_ff
    if (!rst_n) begin
        serve_feed_q  <= '0;
        serve_valid_q <= 1'b0;
        serve_sop_q   <= 1'b0;
        serve_eop_q   <= 1'b0;
        serve_seq_q   <= '0;
        serve_data_q  <= '0;
        skid_valid    <= '0;
        skid_sop      <= '0;
        skid_eop      <= '0;
    end else begin
        serve_feed_q  <= serve_feed;
        serve_valid_q <= serve_valid_c && out_ready;
        serve_sop_q   <= serve_sop_c   && out_ready;
        serve_eop_q   <= serve_eop_c   && out_ready;
        serve_seq_q   <= serve_seq_c;
        serve_data_q  <= serve_data_c;
        skid_valid    <= skid_valid_nxt;
        skid_sop      <= skid_sop_nxt;
        skid_eop      <= skid_eop_nxt;
    end
end

always_ff @(posedge clk) begin
    skid_data <= skid_data_nxt;
    skid_seq  <= skid_seq_nxt;
end

// =================================================================
// STAGE 2
// =================================================================

assign giveup = (hiccup_cnt == HICCUP_W'(HICCUP_CYCLES - 1)) && |serve_feed_q && !serve_valid_q;

always_comb begin : hiccup_upd
    hiccup_cnt_nxt = hiccup_cnt;

    if (|serve_feed_q) begin
        if (serve_valid_q || giveup)
            hiccup_cnt_nxt = '0;
        else
            hiccup_cnt_nxt = hiccup_cnt + 1'b1;
    end else begin
        hiccup_cnt_nxt = '0;
    end
end

always_ff @(posedge clk) begin : hiccup_ff
    if (!rst_n)         hiccup_cnt <= '0;
    else if (out_ready) hiccup_cnt <= hiccup_cnt_nxt;
end

always_ff @(posedge clk) begin
    if (!rst_n)
        invalidate_q <= '0;
    else if (out_ready)
        invalidate_q <= ((hiccup_cnt == ARM_CNT) && !serve_valid_q) ? serve_feed_q : '0;
end

// ---- invalidate_feed: fires when the LIVE serve_feed matches the
//      armed feed AND this cycle's chunk is confirmed dead. Gated
//      by out_ready — this is the "did a real transfer/non-transfer
//      genuinely happen" point, the single place this signal is
//      gated. ----
always_comb begin : invalidate_upd
    invalidate_feed = '0;
    if (out_ready && |invalidate_q && !serve_valid_c)
        invalidate_feed = invalidate_q;
end

// ---- invalidate_feed_q: faithful record of invalidate_feed one
//      cycle later. NOT separately gated — invalidate_feed already
//      encodes out_ready's effect once; gating again would distort
//      history. ----
always_ff @(posedge clk) begin
    if (!rst_n) invalidate_feed_q <= '0;
    else        invalidate_feed_q <= invalidate_feed;
end

always_comb begin : output_drive
    out_valid = serve_valid_q;
    out_data  = serve_data_q;
    out_seq   = serve_seq_q;
    out_sop   = serve_sop_q;
    out_eop   = serve_eop_q;

    out_feed = '0;
    for (int i = 0; i < NUM_FEEDS; i++)
        if (serve_feed_q[i]) out_feed = FEED_ID_W'(i);
end

always_comb begin : sel_drive
    fix_served_feed  = out_feed;
    fix_served_valid = serve_valid_q && |(serve_feed_q & fix_avail);
end

endmodule