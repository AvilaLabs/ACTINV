# ACTINV P5 — session close, 2026-08-27

**Protocol:** protocols/ACTINV-P5_PROTOCOL.md (98e5ee2a…), hashed before any code. **Verdict: P5-PASS**, no amendments.

| gate | result |
|---|---|
| G0 CRAM coefficients (added mid-phase) | r(0) exact to 4.4e-16; absolute error vs exp ≤ 1.0e-15; generated table equals the recorded values |
| G1 readers | decay: 0 mismatches over 3,821 materials; library: 164,315 rows and 932 MB byte-identical to numpy |
| G2 composition | 132 FNS materials; atoms 3.5e-16, mass balance 2.2e-16, provenance carried in the binary |
| G3 entry points | CLI = Python at 0.0 over 1,372 scalars; certificates identical apart from the entry-point field |
| G4 physics unchanged | 132 experiments; worst 4.8e-13 μW/g absolute, 2.1e-10 relative where resolvable |
| G5 pathways | closure 6.2e-15; planted reaction removal exact to 7.8e-16 |
| G6 ledger and certificate | 19/19 categories; planted decay-record deletion identical across all three entry points |
| G7 mode selection | auto=trace at burn-up 3.3e-12, coupled above 1e-6; coupled's floor is 8.3e11× trace's |

**What P5 delivered.** The Rust core now owns the whole path from `actinv-spec-1` to result — spec parsing and
validation, ENDF decay and `.npz` library readers, embedded abundance tables with provenance, chain assembly, the trace
formulation, pruning, CRAM, inventory/activity/heat split, pathway analysis, ledger and certificate. `actinv run`, the
Python API and the FNS harness are one binary reached three ways, which is what makes the identity gate meaningful and
the certificate's solver field real. Python keeps library building, the harness and every checker.

**Defect found and fixed in this phase:** the CRAM coefficients had been transcribed by hand when the solver moved into
the library; all 32 were wrong and every inventory came back empty. Constants are now generated from the recorded
source with their citation, and `controls/g0_cram_coefficients.py` checks them in a second.

**New capability worth naming:** every step now reports its numerical floor (α0 × max N), the states beneath it and a
bound on the heat they could contribute — so the method's own resolution limit is stated rather than hidden.
