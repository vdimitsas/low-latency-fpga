// -----------------------------------------------------------------------------
// dedup_ingress
//
// Drops redundant copies of packets already confirmed complete by CHECKSUM.
//
// The sequence number is no longer extracted here. It arrives on in_seq from
// the seq_extract stage in front of this block, valid on the beat marked by
// in_seq_valid and held stable for the rest of the packet. This block only
// compares it against the completed packets table.
//
// The comparators, the drop decision and the ready path all run in one cycle.
// The beat is registered on the way out, so every output of this block comes
// from a flop and the boundary with feed_buffer is a register to register path.
// -----------------------------------------------------------------------------

module dedup_ingress #(
    parameter int N_FEEDS   = 4,
    parameter int DATA_W    = 64,
    parameter int SEQ_W     = 32,
    parameter int CPT_DEPTH = 8
) (
    input  logic                           clk,
    input  logic                           rst_n,

    // feed inputs
    input  logic [N_FEEDS-1:0]             in_valid,
    output logic [N_FEEDS-1:0]             in_ready,
    input  logic [N_FEEDS-1:0][DATA_W-1:0] in_data,
    input  logic [N_FEEDS-1:0]             in_sop,
    input  logic [N_FEEDS-1:0]             in_eop,

    // sequence number from seq_extract, per feed
    input  logic [N_FEEDS-1:0][SEQ_W-1:0]  in_seq,
    input  logic [N_FEEDS-1:0]             in_seq_valid,

    // byte count for the data beat
    input  logic [N_FEEDS-1:0][$clog2(DATA_W/8)-1:0] in_byte_cnt,

    // feed outputs
    output logic [N_FEEDS-1:0]             out_valid,
    input  logic [N_FEEDS-1:0]             out_ready,
    output logic [N_FEEDS-1:0][DATA_W-1:0] out_data,
    output logic [N_FEEDS-1:0]             out_sop,
    output logic [N_FEEDS-1:0]             out_eop,
    output logic [N_FEEDS-1:0][$clog2(DATA_W/8)-1:0] out_byte_cnt,
    output logic [N_FEEDS-1:0][SEQ_W-1:0]  out_seq,
    output logic [N_FEEDS-1:0]             out_seq_valid,

    // completion feedback from CHECKSUM
    input  logic                           cmpl_valid,
    input  logic [SEQ_W-1:0]               cmpl_seq
);

    // -------------------------------------------------------------------------
    // parameter guards
    // -------------------------------------------------------------------------
    initial begin
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
    //
    // in_seq is valid only on the beat marked by in_seq_valid. It is captured
    // there and held for the rest of the packet, so the comparison has a stable
    // value on every beat after the sequence number has arrived.
    //
    // seq_valid_regs tracks whether the sequence number has arrived yet on this
    // packet. Before it has, there is nothing to compare and the beat cannot be
    // dropped. It clears on SOP and sets on in_seq_valid.
    // -------------------------------------------------------------------------
    logic [N_FEEDS-1:0][SEQ_W-1:0] seq_regs;
    logic [N_FEEDS-1:0]            seq_valid_regs;
    logic [N_FEEDS-1:0][SEQ_W-1:0] seq_sel;
    logic [N_FEEDS-1:0]            seq_sel_valid;

    // seq compared this cycle: the freshly arriving value on the beat it lands,
    // the held value afterwards.
    //
    // A SOP beat clears the valid here, combinationally. seq_valid_regs only
    // clears at the clock edge, so without this the first beat of a packet
    // would still be compared against the number the previous packet left
    // behind, and could be dropped on it.
    always_comb begin
        for (int f = 0; f < N_FEEDS; f++) begin
            seq_sel[f] = in_seq_valid[f] ? in_seq[f] : seq_regs[f];

            if (in_seq_valid[f])
                seq_sel_valid[f] = 1'b1;
            else if (in_sop[f])
                seq_sel_valid[f] = 1'b0;
            else
                seq_sel_valid[f] = seq_valid_regs[f];
        end
    end

    // -------------------------------------------------------------------------
    // sequence context register
    //
    // Written while the beat carrying the sequence number is on the bus, without
    // consulting in_ready. Keeping in_ready out of this enable matters for
    // timing: in_ready is driven by drop, so gating this write on it would put
    // the drop cone on a path ending here.
    //
    // Writing before acceptance is safe. While in_ready is low the upstream
    // holds in_valid, in_seq and in_seq_valid stable, so the value written is
    // the value that will eventually be accepted. Repeated writes in that window
    // write the same value to the same bits.
    //
    // seq_valid_regs clears on SOP so a new packet starts with no sequence
    // number, and sets on the beat in_seq_valid marks.
    // -------------------------------------------------------------------------
    always_ff @(posedge clk) begin
        if (!rst_n) begin
            seq_regs       <= '0;
            seq_valid_regs <= '0;
        end else begin
            for (int f = 0; f < N_FEEDS; f++) begin
                if (in_valid[f] && in_sop[f]) begin
                    seq_valid_regs[f] <= 1'b0;
                end

                if (in_valid[f] && in_seq_valid[f]) begin
                    seq_regs[f]       <= in_seq[f];
                    seq_valid_regs[f] <= 1'b1;
                end
            end
        end
    end

    // -------------------------------------------------------------------------
    // comparators
    //
    // Each feed's seq is compared against every CPT entry and against the
    // completion arriving this cycle. The comparison only counts once the
    // sequence number has arrived, so it is qualified by seq_sel_valid.
    //
    // These are the wide comparisons. Vivado maps each one onto a carry chain,
    // three CARRY4 deep at SEQ_W = 32.
    // -------------------------------------------------------------------------
    logic [N_FEEDS-1:0][CPT_DEPTH-1:0] cpt_match;
    logic [N_FEEDS-1:0]                bypass_match;

    always_comb begin
        for (int f = 0; f < N_FEEDS; f++) begin
            for (int e = 0; e < CPT_DEPTH; e++) begin
                cpt_match[f][e] = seq_sel_valid[f] && cpt_occupied[e] &&
                                  (cpt_seq[e] == seq_sel[f]);
            end
            bypass_match[f] = seq_sel_valid[f] && cmpl_valid &&
                              (cmpl_seq == seq_sel[f]);
        end
    end

    // -------------------------------------------------------------------------
    // drop decision
    //
    // An OR reduction over the match results, per feed.
    // -------------------------------------------------------------------------
    logic [N_FEEDS-1:0] drop;

    always_comb begin
        for (int f = 0; f < N_FEEDS; f++) begin
            drop[f] = in_valid[f] && (|cpt_match[f] || bypass_match[f]);
        end
    end

    // -------------------------------------------------------------------------
    // completed packets table
    //
    // Circular. A completion overwrites the oldest entry once the table has
    // wrapped. There is no full condition and nothing ever stalls: the table is
    // a bounded window of recent completions, not a guaranteed record.
    //
    // Duplicate completions are not suppressed. A sequence number already held
    // may be written a second time. This cannot deliver a duplicate downstream:
    // a copy that reaches dedup_egress after its twin has completed is dropped
    // there, and a copy far enough behind to fall outside this window is beyond
    // what any bounded table can catch.
    // -------------------------------------------------------------------------
    always_ff @(posedge clk) begin
        if (!rst_n) begin
            cpt_seq      <= '0;
            cpt_occupied <= '0;
            cpt_wr_ptr   <= '0;
        end else if (cmpl_valid) begin
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
    // Three terms. A feed offering nothing is ready, since there is nothing to
    // refuse. A beat is taken when downstream has room, or when it is being
    // dropped and therefore needs no room at all.
    //
    // drop keeps in_ready high for a beat that is being dropped. Since it's
    // dropped in_ready has no reason to stay low for that feed
    // -------------------------------------------------------------------------
    assign in_ready = ~in_valid | out_ready | drop;

    // -------------------------------------------------------------------------
    // output register
    //
    // out_ready high means downstream has taken the beat in the register, so
    // a new beat can load and flow on. out_ready low means it has not, so the
    // register holds. drop only goes to out_valid, since it is the slowest
    // signal and stays off the payload enable.
    //
    // Why out_ready and not in_ready:
    // in_ready is ~in_valid | out_ready | drop, so it is also high for a
    // dropped beat while downstream is closed.
    // Loading on in_ready would then wipe a beat downstream has not taken.
    // With out_ready the register loads only once downstream has taken its
    // beat, and a dropped beat never gets in.
    // -------------------------------------------------------------------------
    always_ff @(posedge clk) begin
        if (!rst_n) begin
            out_valid     <= '0;
            out_data      <= '0;
            out_sop       <= '0;
            out_eop       <= '0;
            out_seq       <= '0;
            out_seq_valid <= '0;
            out_byte_cnt  <= '0;
        end else begin
            for (int f = 0; f < N_FEEDS; f++) begin
                if (out_ready[f]) begin
                    out_valid[f] <= in_valid[f] && !drop[f];
                end

                if (out_ready[f] && in_valid[f]) begin
                    out_data[f]      <= in_data[f];
                    out_sop[f]       <= in_sop[f];
                    out_eop[f]       <= in_eop[f];
                    out_seq[f]       <= seq_sel[f];
                    out_seq_valid[f] <= seq_sel_valid[f];
                    out_byte_cnt[f]  <= in_byte_cnt[f];
                end
            end
        end
    end

endmodule
