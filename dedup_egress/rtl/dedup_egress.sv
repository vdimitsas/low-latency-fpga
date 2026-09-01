// -----------------------------------------------------------------------------
// dedup_egress
//
// Last stage before the output. Drops any beat whose sequence number has
// already been confirmed complete by CHECKSUM.
//
// Two things reach this block and cannot be stopped earlier: fragments of
// packets killed mid flight by DEDUP_INGRESS, and whole copies that were
// already draining from FEED_BUFFER when their twin completed. In both cases
// the completion arrives after the decision to forward was made.
//
// Single stream. The arbiter has already serialised the feeds.
//
// The sequence number is not extracted here. Every beat carries it on in_seq,
// put there by DEDUP_INGRESS and passed through untouched.
//
// One cycle of latency. The ready path is combinational back to the input
// port, so no skid buffer is needed.
// -----------------------------------------------------------------------------

module dedup_egress #(
    parameter int DATA_W    = 64,
    parameter int SEQ_W     = 32,
    parameter int CPT_DEPTH = 8
) (
    input  logic              clk,
    input  logic              rst_n,

    // input stream
    input  logic              in_valid,
    output logic              in_ready,
    input  logic [DATA_W-1:0] in_data,
    input  logic [SEQ_W-1:0]  in_seq,
    input  logic              in_sop,
    input  logic              in_eop,

    // output stream
    output logic              out_valid,
    input  logic              out_ready,
    output logic [DATA_W-1:0] out_data,
    output logic [SEQ_W-1:0]  out_seq,
    output logic              out_sop,
    output logic              out_eop,

    // completion feedback from CHECKSUM
    input  logic              cmpl_valid,
    input  logic [SEQ_W-1:0]  cmpl_seq
);

    // -------------------------------------------------------------------------
    // parameter checks
    // -------------------------------------------------------------------------
    initial begin
        if (CPT_DEPTH < 1)
            $error("dedup_egress: CPT_DEPTH must be at least 1");
    end

    localparam int CPT_PTR_W = (CPT_DEPTH == 1) ? 1 : $clog2(CPT_DEPTH);

    // -------------------------------------------------------------------------
    // completed packets table
    // -------------------------------------------------------------------------
    logic [CPT_DEPTH-1:0][SEQ_W-1:0] cpt_seq;
    logic [CPT_DEPTH-1:0]            cpt_occupied;
    logic [CPT_PTR_W-1:0]            cpt_wr_ptr;

    // -------------------------------------------------------------------------
    // duplicate completion suppression
    //
    // A packet can complete more than once. Writing the same sequence number
    // again would consume an entry and evict a different, still useful one,
    // shortening the window for no gain. So the write is suppressed when the
    // value is already present.
    //
    // This is a separate comparison from the drop decision: that one compares
    // the beat's seq, this one compares cmpl_seq. CPT_DEPTH equality
    // comparisons of SEQ_W bits, on the completion path only.
    // -------------------------------------------------------------------------
    logic [CPT_DEPTH-1:0] cmpl_match;
    logic                 cmpl_present;

    always_comb begin
        for (int e = 0; e < CPT_DEPTH; e++) begin
            cmpl_match[e] = cpt_occupied[e] && (cpt_seq[e] == cmpl_seq);
        end
        cmpl_present = |cmpl_match;
    end

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            cpt_seq      <= '0;
            cpt_occupied <= '0;
            cpt_wr_ptr   <= '0;
        end else if (cmpl_valid && !cmpl_present) begin
            cpt_seq[cpt_wr_ptr]      <= cmpl_seq;
            cpt_occupied[cpt_wr_ptr] <= 1'b1;

            if (cpt_wr_ptr == CPT_PTR_W'(CPT_DEPTH-1))
                cpt_wr_ptr <= '0;
            else
                cpt_wr_ptr <= cpt_wr_ptr + 1'b1;
        end
    end

    // -------------------------------------------------------------------------
    // comparator tree
    //
    // Runs every cycle. The incoming beat's seq is compared against every
    // occupied CPT entry and against the completion arriving this cycle. The
    // same-cycle bypass catches a copy whose completion has not yet been
    // written to the table.
    //
    // CPT_DEPTH + 1 equality comparisons of SEQ_W bits. One stream, so this is
    // N_FEEDS times narrower than the same tree in DEDUP_INGRESS.
    // -------------------------------------------------------------------------
    logic [CPT_DEPTH-1:0] cpt_match;
    logic                 bypass_match;

    always_comb begin
        for (int e = 0; e < CPT_DEPTH; e++) begin
            cpt_match[e] = cpt_occupied[e] && (cpt_seq[e] == in_seq);
        end

        bypass_match = cmpl_valid && (cmpl_seq == in_seq);
    end

    // -------------------------------------------------------------------------
    // the pipeline cut
    //
    // The comparator results are registered together with the beat they belong
    // to. The drop decision is made on the far side, from the registered
    // results.
    //
    // valid_q is enabled by in_ready alone, with no in_valid qualifier. It has
    // to be able to take a zero: if a load happens with no beat behind it,
    // valid_q must clear so the bubble propagates. Qualifying it with in_valid
    // would hold the last beat asserted and present it again on the following
    // cycle.
    //
    // Everything else is enabled by in_valid && in_ready, the acceptance
    // condition of the handshake, so a held beat and its match results stay
    // stable together across a stall.
    // -------------------------------------------------------------------------
    logic                 valid_q;
    logic [DATA_W-1:0]    data_q;
    logic                 sop_q;
    logic                 eop_q;
    logic [SEQ_W-1:0]     seq_q;
    logic [CPT_DEPTH-1:0] cpt_match_q;
    logic                 bypass_match_q;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            valid_q        <= 1'b0;
            data_q         <= '0;
            sop_q          <= 1'b0;
            eop_q          <= 1'b0;
            seq_q          <= '0;
            cpt_match_q    <= '0;
            bypass_match_q <= 1'b0;
        end else begin
            if (in_ready) begin
                valid_q <= in_valid;
            end

            if (in_valid && in_ready) begin
                data_q         <= in_data;
                sop_q          <= in_sop;
                eop_q          <= in_eop;
                seq_q          <= in_seq;
                cpt_match_q    <= cpt_match;
                bypass_match_q <= bypass_match;
            end
        end
    end

    // -------------------------------------------------------------------------
    // drop decision, after the cut
    //
    // An OR reduction over the registered match results. CPT_DEPTH bits, one
    // level of logic. Everything expensive already happened in the previous
    // cycle.
    // -------------------------------------------------------------------------
    logic drop;

    assign drop = valid_q && (|cpt_match_q || bypass_match_q);

    // -------------------------------------------------------------------------
    // ready path
    //
    // Combinational, so upstream sees the decision in the same cycle and no
    // skid buffer is needed.
    //
    // Three terms. An empty stage always accepts, since there is nothing held
    // to release. A held beat is released when downstream has room, or when it
    // is being dropped and therefore needs no room at all.
    //
    // The drop term keeps a known redundant beat from being held up by a
    // stalled consumer. Those beats are discarded here and never consume a
    // slot downstream.
    // -------------------------------------------------------------------------
    assign in_ready = ~valid_q | out_ready | drop;

    // -------------------------------------------------------------------------
    // output
    //
    // The registered beat passes straight through. The only thing DEDUP_EGRESS
    // does to the stream is withhold valid on a beat whose seq matches a
    // completed packet.
    // -------------------------------------------------------------------------
    always_comb begin
        out_valid = valid_q && !drop;
        out_data  = data_q;
        out_sop   = sop_q;
        out_eop   = eop_q;
        out_seq   = seq_q;
    end

endmodule
