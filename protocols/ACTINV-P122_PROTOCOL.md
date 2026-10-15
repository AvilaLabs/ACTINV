# P122 — Complete step summary (FISPACT-II inventory-report parity)

Registered 2026-10-15, before any P122 result is produced, after the owner approved the FISPACT-II parity plan
(2026-10-15, "i approve your order"). Base commit: `2f3daa4` (origin/master). Tracker: `COMPETITIVE_BENCHMARK.md`,
"FISPACT-II feature parity tracker", rows on the inventory report, element table, dominant lists, MIND and NOSTABLE.

## Change under test

An opt-in per-step summary that gives everything FISPACT-II prints in its inventory blocks, computed from the
state ACTINV already holds. Nothing in the solve changes.

1. Spec: optional `material.density_g_cm3` (finite, > 0) and optional top-level `summary` with
   - `nuclides` (default true) and `elements` (default true)
   - `dominant_top` (default 10, range 1–256)
   - `min_atoms` (FISPACT MIND, absolute atoms for `material.mass_g`, default 0)
   - `include_stable` (default true; false is FISPACT NOSTABLE)

   The filters apply to the nuclide table only; totals always cover the whole inventory.
2. Output: `StepOut.summary`, serialized only when `summary` is requested. It contains:
   - the full inventory: in trace mode the bulk reservoir is included by the same rule as the existing gas
     accounting; the sink states are excluded; states without decay data are kept and flagged
   - per-nuclide atoms, grams, Bq, α/β/γ W and half-life
   - totals: Bq, Ci, heat by type, mass per initial gram, leakage, plus per-cm³ and absolute values where density
     or mass is given
   - an exact branching-weighted activity partition by decay mode
   - FISPACT-compatible α/β/γ Bq
   - activity and heat excluding H-3
   - an element table
   - dominant lists for activity and total, α, β and γ heat, each with percent, cumulative percent and the
     number of nuclides needed for 90 % and 99 %

## Gates

Checker: `controls/check_p122.py`, run by the coordinator under the 6 GiB cgroup, serially. Reference binary:
release build of `2f3daa4`. Candidate: release build of the P122 implementation commit. Gate input:
`examples/fns_fe_5min.json` (SHA-256 `ed55ed00f6c00fc4a2351e18332e98e7feb33d9b7ca65e7ba861e0e5e8feef39`, data from
the installed `actinv-data/v1.1.0` bundle), plus that spec with `summary: {}` and `density_g_cm3: 7.874` added,
run in its declared mode and in the other mode (trace/coupled).

- **G0.** This protocol's SHA-256 is in `protocols/protocol_hash.txt` in a commit before any P122 evidence.
- **G1.** `cargo fmt --all -- --check`; `cargo clippy --workspace --all-targets --all-features -- -D warnings`;
  `cargo test --workspace --all-targets --all-features`. New unit tests in the summary module cover:
  - totals and the mode partition summing to total activity
  - MIND/NOSTABLE filtering leaving totals unchanged
  - dominant cumulative percent and the 90/99 % counts
  - element aggregation
  - the mass-from-A fallback flag
- **G2, unchanged by default.** Candidate and reference results for the unmodified gate spec are byte-identical
  apart from `ms`.
- **G3, independent recomputation.** From the candidate's emitted summary inventory (atoms per nuclide) and an
  independent Python parse of the ENDF-6 MF=8 MT=457 decay file named by the spec, the checker recomputes per
  nuclide Bq, α/β/γ W and grams, plus the totals, the Ci value, the element table, the mode partition and the
  dominant lists. Agreement within 1e-9 relative per value, or 1e-30 absolute for zero.
- **G4, consistency.**
  - The summary total activity and α/β/γ/total heat agree with the step's existing `activity_Bq_per_g` sum and
    `heat_W_per_g` within 1e-9 relative, at every step where the existing fields cover the same states; any
    difference must be explained by the states the existing fields omit, which the checker lists.
  - The mode partition sums to total activity within 1e-12 relative.
  - Trace and coupled runs agree on totals within the existing P-series trace/coupled tolerance used by
    `controls/` (the checker states the tolerance it takes and where from).
- **G5, report-only comparison with FISPACT-II 4.0.** For the FNS Fe 1996 5-minute case
  (`~/nuclear-data/conderc-fns/fns/Fe/TENDL-2017_1996exp_5min.out`), the checker tabulates ACTINV's summary totals
  (Ci, α/β/γ Bq, ex-tritium values, element composition, top dominant nuclides) against the FISPACT block at
  matching cooling times. Different libraries (TENDL-2017 vs TENDL-2025) are expected to differ. This gate
  records evidence and has no pass threshold.
- **G6.** CI green on the pushed commit, with the committed run-output baselines unchanged.

A gate that fails is recorded as P122-FAIL with its evidence; repairs need an amendment registered before they are
made.

## Not claimed

No dose quantities (P123 extends the dominant lists to dose), no fission or burn-up quantities (P125), no change to
any existing field or to the solve.
