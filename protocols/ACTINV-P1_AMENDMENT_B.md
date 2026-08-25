# ACTINV P1 — Amendment B (append-only), 2026-08-25 — control (3) repair and control (2) scoring rule

1. **Control (3) Rust vs Python, repair round.** First run: max relative 8.55e-12 (absolute 2.2e-16, one ulp)
   over the 9 populated nuclides — above the 1e-12 line. Cause: `num-complex` complex division uses the
   naive formula; CPython uses Smith's algorithm; eight sequential complex solves amplify the last-bit
   difference on small components. Repair: the crate implements its own Smith division (identical to
   CPython `_Py_c_quot`) and uses it at every division site, so the two implementations round identically.
   Threshold unchanged (≤ 1e-12).
2. **Control (2) scoring.** The script required CRAM mass outside the reachable sub-network to be exactly
   zero; the protocol's rule is the 1e-15-of-total threshold. Observed 6.2e-20. The script is corrected to
   the protocol's rule; the measured numbers (1.28e-11 / 1.33e-11) are unchanged.
3. **G3 supplementary.** The seeded planted deletion selected Cr-53 (stable) — the control passed by the
   letter (named; atom fraction 1.148e-4 reported) but its activity share is trivially zero. A second,
   supplementary deletion of a radioactive product is added for information; the scored control remains
   the seeded one.
Nothing else changes.
