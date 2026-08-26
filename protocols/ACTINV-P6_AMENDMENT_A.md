# ACTINV P6 — Amendment A (append-only), 2026-08-27 — G1 was mis-scored

**What happened.** P6-G1 requires that "a clone into an empty directory builds the workspace and produces `actinv`; no
file outside the clone is required except the nuclear data named in the spec." I scored it PASS on the strength of a
clean-clone build plus the end-to-end control, without ever running the other CI controls from that clone. The first CI
run on the private repository failed immediately: `controls/g0_cram_coefficients.py` read the CRAM coefficients from
`~/Documents/Avila-Labs/scouting/act-p0/results/cram_coefficients.json` — a path in a different, private repository.
The gate's own wording would have caught this had the gate actually been executed.

**Repair.**
1. The coefficients are vendored to `data/cram_coefficients.json`, carrying their citation and the note that they were
   recorded in ACT-P0 from `openmc.deplete.cram` (OpenMC, MIT). `controls/gen_cram.py` and
   `controls/g0_cram_coefficients.py` read the vendored file. The generated `cram_coeffs.rs` is byte-identical to
   before, which confirms the vendored copy matches the recorded one.
2. G1 is replaced by an executable test rather than an assertion: `controls/g1_self_contained.py` clones the repository
   into a temporary directory, redirects `HOME` to an empty directory, and runs every control CI runs. Any dependence
   on a file outside the clone fails there. It also checks that regenerating the derived sources leaves the tree clean.

**Consequence.** P6 has used its one repair round; the verdict becomes **P6-CONDITIONAL**. The v0.1.0 tag stands, since
no result changed — only the location of a constants file and the strength of a gate.
