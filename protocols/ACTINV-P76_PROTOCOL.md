# ACTINV-P76 — Impurity-budget prototype with self-verification

Date: 2026-09-29. Status: **frozen before any element run.**

## Question

P75b showed that in coupled mode a mixture's activities equal the wt%-weighted sum of pure-element
activities at the same flux, to 1e-11. So any linear response — here the IAEA clearance index
`CI(t) = Σ_n A_n(t)/L_n` — is exactly linear in composition at a component's flux. One coupled
run per element then answers impurity questions as linear algebra:

- the clearance index of the matrix alone and at a declared impurity specification;
- per-impurity attribution at spec;
- the single-impurity limit (others at spec) for CI = 1;
- the spec margin factor k (the largest uniform scaling of the impurity spec with CI = 1);
- the cooling time at which each result binds.

Can this be computed from element runs and **verified** by full solves at the computed edges?

## Population

`controls/p76_impurity_budget.py`; element manifest `target/p76/cases.json` sha256
`598210a212299d17051f3857d1c442e6050e9c824a92ce658d5c71bd0ab8a749` (40 runs: 20 elements × 2
scenarios). Same data as P75 (TENDL-2025 709g, ENDF/B-VIII.0 + JEFF-3.3 decay), coupled mode,
reach pruning. Binary copy `target/p75/actinv` sha256 `8113231b…a9ea`.

- Materials (wt%, representative, not certified): EUROFER97-type matrix (Cr 9, W 1.1, Mn 0.4,
  V 0.2, Ta 0.12, C 0.11, N 0.03, Si 0.05, Fe balance) with impurity spec Nb 10, Mo 50, Ni 50,
  Cu 50, Co 50, Al 100, Ti 100, Ag 1 ppm. SS316LN-type matrix (Cr 17.5, Ni 12.25, Mo 2.5, Mn 1.8,
  Si 0.5, N 0.07, C 0.03, P 0.025, S 0.01, Fe balance) with impurity spec Co 0.05, Nb 0.01,
  Ta 0.01, Cu 0.3, B 0.001, Ag 0.0001 wt%.
- Scenarios: `vessel` — P75 `mix` spectrum at 1e12 n cm⁻² s⁻¹; `firstwall` — FNS D-T shape at
  1e14. Irradiation 5 y continuous; cooling to 1e6 s, 1, 10, 30, 50, 100 y.
- Clearance limits: bundled `data/clearance_iaea_2004.json` (IAEA RS-G-1.7 Table 2 basis,
  277 nuclides). Nuclide keys map `Co60 → Co-60`, `Co60m1 → Co-60m`; higher isomers are
  unmapped. Activity of nuclides with no limit is reported as uncovered, never silently cleared.
  Known gap found before freeze: the bundled table has no Ag-108m entry.
- Targets: clearance at 50 y and 100 y of cooling.

## Method

Fe is the balance element, so `∂CI/∂w_e = (r_e − r_Fe)/100` per wt%, where r_e is the CI of pure
element e. Verification points are taken at the first target (50 y):
(i) spec × k, predicted CI = 1;
(ii) the dominant impurity at its single-impurity limit with others at spec, predicted CI = 1;
(iii) the as-specified composition, predicted CI as composed.
Points (i) and (ii) are emitted only when they are physically meaningful: k > 0, the limit is
positive, and the matrix alone is below 1.

## Gates

- **G0** all 40 element runs return 0; protocol hash registered.
- **G1 (self-verification)** every emitted verification point's full coupled solve reproduces the
  predicted CI within 1e-6 relative.
- **Descriptive:** the budget report per scenario, material and target, including uncovered
  activity share and top uncovered nuclides, and whether the matrix alone is clearable. The
  numbers are for these representative inputs only — not a qualified clearance assessment.

## Pre-registered expectations (the author's)

G1 passes near 1e-10 (P75b). The first-wall scenario is likely unclearable at 100 y even with zero
impurities (matrix Nb-94/Mo-93 from Mo, W, Ta, and C-14 from N). The vessel scenario is likely
clearable, with Co and Nb dominating attribution.

## Execution

Sequential, cgroup (6G, 200 %), roughly 1–2 min. The verdict is derived by
`p76_impurity_budget.py check` into `results/p76_verdict.json`, sha-binding the protocol,
manifest, both checkpoints, the budget file, the binary and the limits table.
