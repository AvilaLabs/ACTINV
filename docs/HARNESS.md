# The FNS harness — code-agnostic by construction

`controls/run_fns.py` runs every CoNDERC FNS experiment: reads the FISPACT-II `.i` (composition, flux, schedule), the
`.exp` (measured decay heat), the `.out`/`.nuclides` (reference results), solves with `actinv-solve`, evaluates decay
heat with `controls/harness/decayheat.py`, and writes one JSON record per experiment.

**Adding another code.** Produce inventory records in the interchange schema — a list of steps, each
`{"t_s": cooling time, "nuclides": [{"Z", "A", "LISO", "atoms_per_g"}], "source": "<code>"}` — and call
`decayheat.heat_W_per_g` on each step. The C/E computation and the checker are the same for every code; the FISPACT-II
reference in the records is read from its own outputs, so a third code enters as one more column.

**Alignment rules** (protocol P2 Amendment C): measured rows are matched to schedule steps by time with the unit inferred;
padded zero rows and non-positive measurements are excluded and ledgered.

**Re-derivation.** `controls/check_p3.py` recomputes every C/E from the stored inventories and re-matches every hash in
`results/fns_certificate.json`.
