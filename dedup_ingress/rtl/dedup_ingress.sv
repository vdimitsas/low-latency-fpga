// -----------------------------------------------------------------------------
// dedup_ingress
//
// Drops redundant copies of packets already confirmed complete by CHECKSUM.
//
// The comparator tree is cut by a register. The comparators run before it, the
// OR reduction and the drop decision run after it. This is the only pipeline
// stage in the block and it costs one cycle of latency.
//
// The cut exists because the tree, the drop decision and the ready path in one
// cycle failed timing. The worst path ran from cmpl_seq through a 32 bit
// equality, the OR reduction, drop and in_ready, ending on a register clock
// enable. Splitting it puts the comparators in one cycle and everything after
// them in the next.
// -----------------------------------------------------------------------------

module dedup_ingress #(
    parameter int N_FEEDS    = 4,
    parameter int DATA_W     = 64,
    parameter int SEQ_W      = 32,
    parameter int SEQ_OFFSET = 0,
    parameter int CPT_DEPTH  = 8
) (
    input  logic                           clk,
    input  logic                           rst_n,

    // feed inputs
    input  logic [N_FEEDS-1:0]             in_valid,
    output logic [N_FEEDS-1:0]             in_ready,
    input  logic [N_FEEDS-1:0][DATA_W-1:0] in_data,
    input  logic [N_FEEDS-1:0]             in_sop,
    input  logic [N_FEEDS-1:0]             in_eop,

    // feed outputs
    output logic [N_FEEDS-1:0]             out_valid,
    input  logic [N_FEEDS-1:0]             out_ready,
    output logic [N_FEEDS-1:0][DATA_W-1:0] out_data,
    output logic [N_FEEDS-1:0]             out_sop,
    output logic [N_FEEDS-1:0]             out_eop,
    output logic [N_FEEDS-1:0][SEQ_W-1:0]  out_seq,

    // completion feedback from CHECKSUM
    input  logic                           cmpl_valid,
    input  logic [SEQ_W-1:0]               cmpl_seq
);

    // -------------------------------------------------------------------------
    // parameter guards
    // -------------------------------------------------------------------------
    initial begin
        if (SEQ_OFFSET*8 + SEQ_W > DATA_W)
            $error("dedup_ingress: seq field does not fit in the first beat");
        if (CPT_DEPTH < 1)
            $error("dedup_ingress: CPT_DEPTH must be at least 1");
        if (N_FEEDS < 1)
            $error("dedup_ingress: N_FEEDS must be at least 1");
    end

    localparam int CPT_PTR_W = (CPT_DEPTH == 1) ? 1 : $clog2(CPT_DEPTH);

    // -------------------------------------------------------------------------
    // completed packets table
    // -------------------------------------------------------------------------
    logic [CPT_DEPTH-1:0][SEQ_W-1:0] cpt_seq;
    logic [CPT_DEPTH-1:0]            cpt_occupied;
    logic [CPT_PTR_W-1:0]            cpt_wr_ptr;

    // -------------------------------------------------------------------------
    // per feed sequence context
    // -------------------------------------------------------------------------
    logic [N_FEEDS-1:0][SEQ_W-1:0] seq_regs;
    logic [N_FEEDS-1:0][SEQ_W-1:0] seq_extract;
    logic [N_FEEDS-1:0][SEQ_W-1:0] seq_sel;

    // -------------------------------------------------------------------------
    // sequence extraction
    //
    // The seq field is present only in the first beat of a packet. It is sliced
    // out combinationally at SOP and latched for the remainder of the packet.
    // -------------------------------------------------------------------------
    always_comb begin
        for (int f = 0; f < N_FEEDS; f++) begin
            seq_extract[f] = in_data[f][SEQ_OFFSET*8 +: SEQ_W];
        end
    end

    // seq compared this cycle: freshly extracted on an SOP beat, held value on
    // every other beat
    always_comb begin
        for (int f = 0; f < N_FEEDS; f++) begin
            seq_sel[f] = in_sop[f] ? seq_extract[f] : seq_regs[f];
        end
    end

    // -------------------------------------------------------------------------
    // sequence context register
    //
    // Written on any valid SOP beat, without consulting in_ready.
    //
    // seq_regs is not part of the acceptance protocol. It is a local context
    // register that remembers the sequence number for the rest of the packet.
    // Acceptance is still in_valid && in_ready on the datapath below. This
    // register only observes the SOP beat while it is present on the bus.
    //
    // Keeping in_ready out of this enable matters for timing. in_ready is
    // driven by drop, so gating this write on it would put the drop cone on a
    // path ending here.
    //
    // Writing before acceptance is safe. While in_ready is low the upstream
    // holds in_valid and in_data stable, so the SOP beat stays on the bus and
    // the value written is the value that will eventually be accepted. Repeated
    // writes in that window write the same value to the same bits.
    //
    // This relies on the upstream honouring the stability rule of the
    // handshake. A source that withdrew in_valid or changed in_data during a
    // stall would corrupt this register.
    // -------------------------------------------------------------------------
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            seq_regs <= '0;
        end else begin
            for (int f = 0; f < N_FEEDS; f++) begin
                if (in_valid[f] && in_sop[f]) begin
                    seq_regs[f] <= seq_extract[f];
                end
            end
        end
    end

    // -------------------------------------------------------------------------
    // comparators, before the cut
    //
    // Each feed's seq is compared against every CPT entry and against the
    // completion arriving this cycle. N_FEEDS * (CPT_DEPTH + 1) equality
    // comparisons of SEQ_W bits.
    //
    // These are the wide comparisons. Vivado maps each one onto a carry chain,
    // three CARRY4 deep at SEQ_W = 32. Nothing else happens in this cycle: the
    // results go straight into the register below.
    // -------------------------------------------------------------------------
    logic [N_FEEDS-1:0][CPT_DEPTH-1:0] cpt_match;
    logic [N_FEEDS-1:0]                bypass_match;

    always_comb begin
        for (int f = 0; f < N_FEEDS; f++) begin
            for (int e = 0; e < CPT_DEPTH; e++) begin
                cpt_match[f][e] = cpt_occupied[e] && (cpt_seq[e] == seq_sel[f]);
            end
            bypass_match[f] = cmpl_valid && (cmpl_seq == seq_sel[f]);
        end
    end

    // -------------------------------------------------------------------------
    // the cut
    //
    // The beat and its comparison results register together, so drop stays
    // aligned with the beat it belongs to.
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
    logic [N_FEEDS-1:0]                valid_q;
    logic [N_FEEDS-1:0][DATA_W-1:0]    data_q;
    logic [N_FEEDS-1:0]                sop_q;
    logic [N_FEEDS-1:0]                eop_q;
    logic [N_FEEDS-1:0][SEQ_W-1:0]     seq_q;
    logic [N_FEEDS-1:0][CPT_DEPTH-1:0] cpt_match_q;
    logic [N_FEEDS-1:0]                bypass_match_q;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            valid_q        <= '0;
            data_q         <= '0;
            sop_q          <= '0;
            eop_q          <= '0;
            seq_q          <= '0;
            cpt_match_q    <= '0;
            bypass_match_q <= '0;
        end else begin
            for (int f = 0; f < N_FEEDS; f++) begin
                if (in_ready[f]) begin
                    valid_q[f] <= in_valid[f];
                end

                if (in_valid[f] && in_ready[f]) begin
                    data_q[f]         <= in_data[f];
                    sop_q[f]          <= in_sop[f];
                    eop_q[f]          <= in_eop[f];
                    seq_q[f]          <= seq_sel[f];
                    cpt_match_q[f]    <= cpt_match[f];
                    bypass_match_q[f] <= bypass_match[f];
                end
            end
        end
    end

    // -------------------------------------------------------------------------
    // drop decision, after the cut
    //
    // An OR reduction over the registered match results. CPT_DEPTH bits per
    // feed, one level of logic. Everything expensive already happened in the
    // previous cycle.
    // -------------------------------------------------------------------------
    logic [N_FEEDS-1:0] drop;

    always_comb begin
        for (int f = 0; f < N_FEEDS; f++) begin
            drop[f] = valid_q[f] && (|cpt_match_q[f] || bypass_match_q[f]);
        end
    end

    // -------------------------------------------------------------------------
    // completion already held
    //
    // A packet can complete more than once. A copy that got past this block
    // before its twin completed is still served by the arbiter and still
    // checksummed, so CHECKSUM raises a second completion for a sequence
    // number the table already holds.
    //
    // Writing it again would consume an entry and evict a different, still
    // useful sequence number, shortening the window for no gain. So the write
    // is suppressed when the value is already present.
    //
    // This is a separate comparison from the drop decision: that one compares
    // each feed's seq_sel, this one compares cmpl_seq. CPT_DEPTH equality
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

    // -------------------------------------------------------------------------
    // completed packets table
    //
    // Circular. A completion carrying a sequence number not already held
    // writes, overwriting the oldest entry once the table has wrapped. There
    // is no full condition and nothing ever stalls: the table is a bounded
    // window of recent completions, not a guaranteed record.
    // -------------------------------------------------------------------------
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
    // ready path
    //
    // Per feed and combinational, so the upstream sees the decision in the same
    // cycle and no skid buffer is needed.
    //
    // Three terms. An empty stage always accepts, since there is nothing held
    // to release. A held beat is released when downstream has room, or when it
    // is being dropped and therefore needs no room at all.
    //
    // The drop term keeps a feed carrying a known redundant copy from being
    // held up by a full FIFO downstream. Those beats are discarded here and
    // never consume a slot.
    // -------------------------------------------------------------------------
    assign in_ready = ~valid_q | out_ready | drop;

    // -------------------------------------------------------------------------
    // output
    //
    // The registered beat passes straight through. The only thing this block
    // does to the stream is withhold valid on a feed whose seq matches a
    // completed packet.
    //
    // A drop can land mid packet: a copy already partly forwarded is killed the
    // moment its seq completes on another feed. The beats already past this
    // block are not recalled. They are served like any others and die at
    // DEDUP_EGRESS, which drops anything whose seq has already completed.
    // -------------------------------------------------------------------------
    always_comb begin
        for (int f = 0; f < N_FEEDS; f++) begin
            out_valid[f] = valid_q[f] && !drop[f];
            out_data[f]  = data_q[f];
            out_sop[f]   = sop_q[f];
            out_eop[f]   = eop_q[f];
            out_seq[f]   = seq_q[f];
        end
    end

endmodule
