// -----------------------------------------------------------------------------
// sync_fifo
//
// Single clock FIFO. Read data is combinational: when empty is low, rd_data
// already shows the head, so a beat can be read in the same cycle it is seen.
//
// The storage is an array indexed by a register, which Vivado maps to
// distributed RAM rather than flops.
//
// Full and empty come from an extra pointer bit rather than a counter. Both
// pointers are WIDTH_PTR+1 bits wide: equal pointers means empty, equal
// pointers with different top bits means full.
// -----------------------------------------------------------------------------

module sync_fifo #(
    parameter int WIDTH = 98,
    parameter int DEPTH = 32
) (
    input  logic             clk,
    input  logic             rst_n,

    // write side
    input  logic             wr_en,
    input  logic [WIDTH-1:0] wr_data,
    output logic             full,

    // read side
    input  logic             rd_en,
    output logic [WIDTH-1:0] rd_data,
    output logic             empty
);

    // -------------------------------------------------------------------------
    // parameter guards
    // -------------------------------------------------------------------------
    initial begin
        if (DEPTH < 2)
            $error("sync_fifo: DEPTH must be at least 2");
        if (DEPTH != (1 << $clog2(DEPTH)))
            $error("sync_fifo: DEPTH must be a power of two");
        if (WIDTH < 1)
            $error("sync_fifo: WIDTH must be at least 1");
    end

    localparam int PTR_W = $clog2(DEPTH);

    // -------------------------------------------------------------------------
    // storage and pointers
    //
    // Pointers carry one bit more than the address so that a wrapped write
    // pointer can be told apart from an equal one.
    // -------------------------------------------------------------------------
    logic [WIDTH-1:0] mem [DEPTH];

    logic [PTR_W:0]   wr_ptr;
    logic [PTR_W:0]   rd_ptr;

    wire [PTR_W-1:0]  wr_addr = wr_ptr[PTR_W-1:0];
    wire [PTR_W-1:0]  rd_addr = rd_ptr[PTR_W-1:0];

    // -------------------------------------------------------------------------
    // flags
    //
    // empty: pointers identical.
    // full:  addresses identical, wrap bits opposite.
    // -------------------------------------------------------------------------
    assign empty = (wr_ptr == rd_ptr);
    assign full  = (wr_ptr[PTR_W-1:0] == rd_ptr[PTR_W-1:0]) &&
                   (wr_ptr[PTR_W]     != rd_ptr[PTR_W]);

    // -------------------------------------------------------------------------
    // read data
    //
    // Combinational. When empty is low this is the head, available in the same
    // cycle. When empty is high it is stale and must not be used.
    // -------------------------------------------------------------------------
    assign rd_data = mem[rd_addr];

    // -------------------------------------------------------------------------
    // write
    //
    // wr_en is qualified with full here as well as by the caller, so a write
    // into a full FIFO cannot corrupt the head.
    // -------------------------------------------------------------------------
    always_ff @(posedge clk) begin
        if (wr_en && !full) begin
            mem[wr_addr] <= wr_data;
        end
    end

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            wr_ptr <= '0;
        end else if (wr_en && !full) begin
            wr_ptr <= wr_ptr + 1'b1;
        end
    end

    // -------------------------------------------------------------------------
    // read
    // -------------------------------------------------------------------------
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            rd_ptr <= '0;
        end else if (rd_en && !empty) begin
            rd_ptr <= rd_ptr + 1'b1;
        end
    end

endmodule
