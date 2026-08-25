# ACTINV P2 — Amendment C (append-only), 2026-08-25 — harness alignment rules (G4 readers)

1. Measured rows are aligned to schedule steps by TIME, not by index: the `.exp` time unit is inferred from
   {s, min, h, d, y} as the unit minimising the median relative mismatch to the cumulative cooling times; a row
   is matched to the step within 2 % (or 1 s); unmatched rows are ledgered and excluded. (Al 1996exp_7hour has 10
   measured rows and 7 schedule steps; index alignment happened to coincide for the first 7 — by luck.)
2. Measured rows with heat ≤ 0 are excluded from C/E and ledgered (Bi 1996exp_5min contains such rows).
3. `.nuclides` nuclide names may have no space between symbol and mass number ("Ir194", "Au196n"); the header
   regex allows zero or more spaces.
These are reader/alignment rules; the solver and its results are untouched. G4 is rerun so that stored records
reflect the rules.
