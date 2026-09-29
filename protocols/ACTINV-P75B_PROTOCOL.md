# ACTINV-P75B — Composition superposition at real flux

Date: 2026-09-29. Status: **frozen before execution.** Follows P75 (`results/p75_summary.md`), which
found flux-linearity fails at vessel fluence in moderated spectra, and whose G1 composition check
ran only at unit flux in trace mode, where a background subtraction limited precision.

## Question

At a fixed flux the inventory equations dN/dt = A(φ)N are linear in the initial composition at
any fluence. So a mixture's responses should equal the wt%-weighted sum of pure-element responses
at the same flux, with no background subtraction and no trace approximation. If this holds in
ACTINV numerically, composition questions (impurity budgets, material swaps, per-element dose
attribution) can be answered exactly as linear algebra over per-element runs at each cell's
actual flux. Does it hold, in which solver modes, and does the shipped default (auto mode, rate
pruning) preserve it?

## Population

Manifest `target/p75b/cases.json` sha256
`d872e559bd5fef143d0dcae1d33b2ef27fb211834cf7ab20eec378a0c045393c`, 783 runs, built by
`controls/p75b_composition.py build` from the P75 machinery (same data, compositions and schedule
definitions). Mixtures: ss316ln, eurofer97, concrete (P75 compositions). Pure elements at 100 wt%:
all 26 elements appearing in any mixture. Spectra: fns, mix, maxwell. Amplitudes 1e10, 1e13, 1e15.
Schedule `s1_1y` (1 y irradiation, then cooling to 1 h, 1 d, 1e6 s, 30 d, 1 y, 10 y, 100 y).
Compared steps: shutdown and every cooling step.

| arm | mode / prune |
|---|---|
| C | coupled / reach (gating) |
| R | trace / reach (gating) |
| A | auto / rate — shipped defaults (descriptive) |

## Definitions

For mixture m with composition w_e (wt%), prediction `Σ_e (w_e/100)·R(el_e)` per nuclide activity
and total heat, compared with R(m) of the same arm, spectrum, amplitude and step. Photon group power
is composed from the P75 Y-arm yield table (`target/p75/runs.jsonl`, sha-bound). The metrics
`e_A, e_H, e_P, e_agg` are P75's. Coverage rule as P75: photon metrics unavailable where
uncovered activity exceeds 1e-6 of the step, counted.

## Gates

- **G0** all 783 runs return 0; manifest and protocol hashes match.
- **G1 (coupled/reach)** PASS iff `e_agg ≤ 1e-6` at every compared step of every
  (mixture, spectrum, amplitude).
- **G2 (trace/reach)** PASS iff `e_agg ≤ 1e-6` likewise. (Trace mode is linear in composition by
  construction, so this tests the implementation. It says nothing about trace mode's physical
  validity at high fluence.)
- **Descriptive:** the same metrics for the auto/rate arm, with the modes auto chose for mixture
  and elements; per-nuclide deviation for nuclides ≥ 1e-6 of activity; photon coverage.

## Pre-registered expectations (the author's; not criteria)

- R: ≤ 1e-10 everywhere.
- C: within 1e-6, but coupled-mode CRAM round-off is relative to the whole inventory vector
  (bulk-dominated), so late-cooling steps at 1e10 may show noise well above R's.
- A: may violate superposition, because auto picks the mode per run from the maximum burn-up
  fraction over the nuclides present (a mixture containing Eu or B goes coupled while pure Fe stays
  trace), and rate pruning uses an absolute atoms/g floor that depends on concentration.

## Execution

Same controls as P75: sequential, AGENTS.md cgroup (6G, 200 % CPU), binary copy
`target/p75/actinv` (sha256 `8113231b…a9ea`), raw results deleted after extraction, checkpoint
`target/p75b/runs.jsonl`, verdict by `controls/check_p75b.py` → `results/p75b_verdict.json`.
Estimated ≈ 15 min.
