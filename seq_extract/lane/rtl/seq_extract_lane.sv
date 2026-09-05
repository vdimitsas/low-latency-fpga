// -----------------------------------------------------------------------------
// seq_extract_lane
//
// One feed of the front end. Passes the beat stream through a single register
// stage and pulls the sequence number out of it on the way.
//
// The sequence number sits at a fixed byte offset from the start of a packet.
// That offset can land anywhere, so the field may sit inside one beat or span
// two. Both cases are handled, chosen at elaboration time.
// -----------------------------------------------------------------------------

module seq_extract_lane #(
    parameter int DATA_W     = 64,
    parameter int SEQ_W      = 32,
    parameter int SEQ_OFFSET = 0,
    parameter int BYTE_CNT_W = 3
) (
    input  logic                  clk,
    input  logic                  rst_n,

    // feed input
    input  logic                  in_valid,
    output logic                  in_ready,
    input  logic [DATA_W-1:0]     in_data,
    input  logic                  in_sop,
    input  logic                  in_eop,
    input  logic [BYTE_CNT_W-1:0] in_byte_cnt,

    // feed output
    output logic                  out_valid,
    input  logic                  out_ready,
    output logic [DATA_W-1:0]     out_data,
    output logic                  out_sop,
    output logic                  out_eop,
    output logic [BYTE_CNT_W-1:0] out_byte_cnt,

    // extracted metadata
    output logic [SEQ_W-1:0]      out_seq,
    output logic                  out_seq_valid
);

    // -------------------------------------------------------------------------
    // where the seq field sits
    // -------------------------------------------------------------------------
    localparam int SEQ_BEAT = SEQ_OFFSET / (DATA_W/8);
    localparam int SEQ_BIT  = (SEQ_OFFSET % (DATA_W/8)) * 8;

    // Bits of the field found in the first of the two beats.
    localparam int SEQ_LO_W = (SEQ_BIT + SEQ_W > DATA_W) ? (DATA_W - SEQ_BIT)
                                                         : SEQ_W;
    // Bits left for the following beat. Zero when the field sits in one beat.
    localparam int SEQ_HI_W = SEQ_W - SEQ_LO_W;
    localparam bit SPAN     = (SEQ_HI_W != 0);

    initial begin
        if (DATA_W % 8 != 0)
            $error("seq_extract_lane: DATA_W must be a whole number of bytes");
        if (SEQ_W < 1)
            $error("seq_extract_lane: SEQ_W must be at least 1");
        if (BYTE_CNT_W != $clog2(DATA_W/8))
            $error("seq_extract_lane: BYTE_CNT_W does not match DATA_W");
    end

    // -------------------------------------------------------------------------
    // beat counter
    //
    // Counts only as far as it needs to. It stops at its top value rather than
    // wrapping, so a long packet cannot roll it round to a value that looks
    // like the seq beat again.
    // -------------------------------------------------------------------------
    localparam int CNT_MAX = SPAN ? (SEQ_BEAT + 1) : SEQ_BEAT;
    localparam int CNT_W   = $clog2(CNT_MAX + 1);

    logic [CNT_W-1:0] beat_cnt;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            beat_cnt <= '0;
        end else if (in_valid && in_ready) begin
            if (in_sop)
                beat_cnt <= CNT_W'(1);
            else if (beat_cnt != CNT_W'(CNT_MAX))
                beat_cnt <= beat_cnt + 1'b1;
        end
    end

    // -------------------------------------------------------------------------
    // beat position markers
    //
    // at_seq_beat is the accepted beat holding the field, or its low part when
    // the field spans two beats. at_next_beat is the accepted beat after it,
    // used only in the spanning case.
    //
    // Both are gated by seq_pending_q. The beat counter stops at its top value
    // and sits there for the rest of the packet, so without the gate the
    // comparison below would keep matching on every later beat.
    // -------------------------------------------------------------------------
    logic at_seq_beat;
    logic at_next_beat;
    logic seq_pending_q;

    assign at_seq_beat  = seq_pending_q && in_valid && in_ready &&
                          (beat_cnt == CNT_W'(SEQ_BEAT));
    assign at_next_beat = seq_pending_q && in_valid && in_ready &&
                          (beat_cnt == CNT_W'(SEQ_BEAT + 1));

    // -------------------------------------------------------------------------
    // capture still owed
    //
    // Set at the start of every packet and cleared on the beat that completes
    // the field. One packet, one capture.
    // -------------------------------------------------------------------------
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            seq_pending_q <= 1'b1;
        end else if (in_valid && in_ready) begin
            if (in_sop)
                seq_pending_q <= 1'b1;
            else if (SPAN ? at_next_beat : at_seq_beat)
                seq_pending_q <= 1'b0;
        end
    end

    // -------------------------------------------------------------------------
    // low part of a spanning field
    // -------------------------------------------------------------------------
    logic [SEQ_LO_W-1:0] seq_lo_q;

    generate
        if (SPAN) begin : g_span
            always_ff @(posedge clk or negedge rst_n) begin
                if (!rst_n)
                    seq_lo_q <= '0;
                else if (at_seq_beat)
                    seq_lo_q <= in_data[SEQ_BIT+SEQ_LO_W-1 : SEQ_BIT];
            end
        end else begin : g_no_span
            assign seq_lo_q = '0;
        end
    endgenerate

    // -------------------------------------------------------------------------
    // extracted sequence number
    //
    // seq_valid_q is a one cycle pulse, not a level. It marks the single beat
    // that completed the field. It takes the same enable as valid_q, so it
    // rides with its own beat: a stall holds it, a bubble clears it.
    //
    // seq_q itself is written only on that beat and holds afterwards, so the
    // value stays readable until the next packet replaces it.
    // -------------------------------------------------------------------------
    logic [SEQ_W-1:0] seq_q;
    logic             seq_valid_q;

    generate
        if (SPAN) begin : g_seq_span
            always_ff @(posedge clk or negedge rst_n) begin
                if (!rst_n) begin
                    seq_q       <= '0;
                    seq_valid_q <= 1'b0;
                end else begin
                    if (in_ready)
                        seq_valid_q <= at_next_beat;

                    if (at_next_beat)
                        seq_q <= {in_data[SEQ_HI_W-1:0], seq_lo_q};
                end
            end
        end else begin : g_seq_fit
            always_ff @(posedge clk or negedge rst_n) begin
                if (!rst_n) begin
                    seq_q       <= '0;
                    seq_valid_q <= 1'b0;
                end else begin
                    if (in_ready)
                        seq_valid_q <= at_seq_beat;

                    if (at_seq_beat)
                        seq_q <= in_data[SEQ_BIT+SEQ_W-1 : SEQ_BIT];
                end
            end
        end
    endgenerate

    // -------------------------------------------------------------------------
    // datapath registers
    //
    // valid_q is enabled by in_ready alone so a bubble can propagate. The
    // payload follows the acceptance condition, so a held beat stays stable
    // across a stall.
    // -------------------------------------------------------------------------
    logic                  valid_q;
    logic [DATA_W-1:0]     data_q;
    logic                  sop_q;
    logic                  eop_q;
    logic [BYTE_CNT_W-1:0] byte_cnt_q;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            valid_q    <= 1'b0;
            data_q     <= '0;
            sop_q      <= 1'b0;
            eop_q      <= 1'b0;
            byte_cnt_q <= '0;
        end else begin
            if (in_ready)
                valid_q <= in_valid;

            if (in_valid && in_ready) begin
                data_q     <= in_data;
                sop_q      <= in_sop;
                eop_q      <= in_eop;
                byte_cnt_q <= in_byte_cnt;
            end
        end
    end

    // -------------------------------------------------------------------------
    // ready path
    //
    // An empty stage always accepts. A held beat is released when downstream
    // has room. Nothing is ever dropped here, so there is no third term.
    // -------------------------------------------------------------------------
    assign in_ready = !valid_q || out_ready;

    // -------------------------------------------------------------------------
    // output
    //
    // The beat passes through unchanged. The only thing this block adds is the
    // extracted sequence number and its valid.
    // -------------------------------------------------------------------------
    assign out_valid    = valid_q;
    assign out_data     = data_q;
    assign out_sop      = sop_q;
    assign out_eop      = eop_q;
    assign out_byte_cnt = byte_cnt_q;

    assign out_seq       = seq_q;
    assign out_seq_valid = seq_valid_q;

endmodule
