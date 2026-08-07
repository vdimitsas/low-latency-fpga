// -----------------------------------------------------------------------------
// dedup_ingress
//
// Drops redundant copies of packets already confirmed complete by CHECKSUM.
// Cut-through: no store-and-forward, no buffering, never stalls.
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
    logic [CPT_DEPTH-1:0][SEQ_W-1:0]   cpt_seq;
    logic [CPT_DEPTH-1:0]              cpt_occupied;
    logic [CPT_PTR_W-1:0]              cpt_wr_ptr;

    // -------------------------------------------------------------------------
    // per feed sequence context
    // -------------------------------------------------------------------------
    logic [N_FEEDS-1:0][SEQ_W-1:0]     seq_regs;

    // -------------------------------------------------------------------------
    // combinational
    // -------------------------------------------------------------------------
    logic [N_FEEDS-1:0][SEQ_W-1:0]     seq_sel;
    logic [N_FEEDS-1:0]                seq_sel_valid;
    logic [N_FEEDS-1:0]                drop;

    // -------------------------------------------------------------------------
    // sequence extraction
    //
    // The seq field is present only in the first beat of a packet. It is sliced
    // out combinationally at SOP and latched for the remainder of the packet.
    // -------------------------------------------------------------------------
    logic [N_FEEDS-1:0][SEQ_W-1:0]     seq_extract;

    always_comb begin
        for (int f = 0; f < N_FEEDS; f++) begin
            seq_extract[f] = in_data[f][SEQ_OFFSET*8 +: SEQ_W];
        end
    end

    // seq used for comparison this cycle: freshly extracted on an SOP beat,
    // held value on every other beat
    always_comb begin
        for (int f = 0; f < N_FEEDS; f++) begin
            seq_sel_valid[f] = in_valid[f];
            seq_sel[f]       = in_sop[f] ? seq_extract[f] : seq_regs[f];
        end
    end

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            seq_regs <= '0;
        end else begin
            for (int f = 0; f < N_FEEDS; f++) begin
                if (in_valid[f] && in_ready[f] && in_sop[f]) begin
                    seq_regs[f] <= seq_extract[f];
                end
            end
        end
    end

    // -------------------------------------------------------------------------
    // comparator tree
    //
    // Runs every cycle. Each feed's current seq is compared against every CPT
    // entry and against the completion arriving this cycle. The same-cycle
    // bypass catches a copy whose completion has not yet been written to the
    // table.
    //
    // This is the widest cone in the block: N_FEEDS * (CPT_DEPTH + 1) equality
    // comparisons of SEQ_W bits. Written flat here. STA decides whether it
    // needs cutting.
    // -------------------------------------------------------------------------
    logic [N_FEEDS-1:0][CPT_DEPTH-1:0] cpt_match;
    logic [N_FEEDS-1:0]                bypass_match;

    always_comb begin
        for (int f = 0; f < N_FEEDS; f++) begin

            for (int e = 0; e < CPT_DEPTH; e++) begin
                cpt_match[f][e] = cpt_occupied[e] &&
                                  (cpt_seq[e] == seq_sel[f]);
            end

            bypass_match[f] = cmpl_valid && (cmpl_seq == seq_sel[f]);

            drop[f] = seq_sel_valid[f] &&
                      (|cpt_match[f] || bypass_match[f]);
        end
    end

    // -------------------------------------------------------------------------
    // completed packets table
    //
    // Circular. A completion always writes, overwriting the oldest entry once
    // the table has wrapped. There is no full condition and nothing ever
    // stalls: the table is a bounded window of recent completions, not a
    // guaranteed record.
    // -------------------------------------------------------------------------
    always_ff @(posedge clk or negedge rst_n) begin
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
    // Per feed and combinational. A feed accepts data whenever its own
    // downstream FIFO has room. The comparator tree is deliberately not in
    // this path: a dropped copy consumes nothing downstream, so the drop
    // decision has no bearing on whether upstream may push.
    // -------------------------------------------------------------------------
    assign in_ready = out_ready;

    // -------------------------------------------------------------------------
    // output
    //
    // Cut-through. Data, boundary markers and the extracted seq pass straight
    // through combinationally. The only thing DEDUP_INGRESS does to the stream is
    // withhold valid on a feed whose seq matches a completed packet.
    //
    // A drop can land mid packet: a copy already partly forwarded is killed
    // the moment its seq completes on another feed. FEED_BUFFER discards the
    // orphaned beats on the same completion feedback.
    // -------------------------------------------------------------------------
    always_comb begin
        for (int f = 0; f < N_FEEDS; f++) begin
            out_valid[f] = in_valid[f] && !drop[f];
            out_data[f]  = in_data[f];
            out_sop[f]   = in_sop[f];
            out_eop[f]   = in_eop[f];
            out_seq[f]   = seq_sel[f];
        end
    end

endmodule
