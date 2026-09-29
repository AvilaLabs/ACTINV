# P75 — linear-response falsification: outcome

Protocol `protocols/ACTINV-P75_PROTOCOL.md` (sha256 `0203ed0a…e2359`, frozen before execution).
Verdict derived by `controls/check_p75.py` → `results/p75_verdict.json` (sha-binds manifest
`0d9b3f5a…`, checkpoint and binary `8113231b…`). 1,032/1,032 runs, 1,279 s total solver wall time,
2-CPU/6 GB cgroup. Executed 2026-09-29.

## Frozen verdict

| gate | result | key number |
|---|---|---|
| G0 integrity | **PASS** | yields consistent to 4.5e-16; composed photon power = ACTINV's own to 1.8e-15 |
| G1 superposition ≤ 1e-9 | **FAIL** | max e_agg 1.4e-2 (concrete, pulsed, 100 y) |
| G2 GO (≤ 1e-2 in R_vessel) | **FAIL** | 102/280 R_vessel cases exceed 1e-2; worst 14.8 (concrete, Maxwellian, 1e13 × 1 y, Eu-152) |
| G2 STRONG (≤ 1e-3) | **FAIL** | 142/280 exceed 1e-3 |
| G4 shipped P70 certificate | **PASS — vacuous** | 0 of 672 pairs certified; nothing to violate |

## Diagnosis (post-hoc; not part of the frozen verdict)

**G2 fails for three physical reasons, each named by the worst-nuclide field:**

1. *Product burn-up of high-σ first-hop products* — Co-58 in SS316LN, Ta-182/183 in EUROFER97,
   Na-22 and Eu-152 in concrete. At 1e13 × 1 y in the soft `mix` spectrum the SS316LN photon power
   at 12 d is off by 19–21 %.
2. *Bulk burn-up of high-σ trace constituents* — Eu-151 in concrete (τ_bulk 2.4 at 1e13 Maxwellian).
3. *Second-order production dominating late-time responses of pure elements* — Co-60 from pure Fe
   (via Fe-59 → Co-59), Zn-65 from pure Cu, Re-186m from pure W. The linear operator is identically
   zero for these, so the relative error is O(1) at **any** fluence once first-order products decay
   (pure Fe at 1e10: ≤ 1e-4 through 10 y, 0.84 at 100 y; pure Cu Maxwellian at 1e10: ≤ 2e-6 through 1 d, 0.25 at 1e6 s, 1.0 at 30 d).
   Optical depths do not see this (τ ~ 1e-8 there).

Post-hoc envelope cuts (R_vessel cases, worst over the stated cooling window):

| subset | n | > 1e-2 (≤ 30 d) | > 1e-2 (all cooling) |
|---|---:|---:|---:|
| D-T (`fns`) spectrum, all materials | 70 | 0 (max 5e-3) | 8 |
| alloys + concrete, `fns` | 30 | 0 (max 1.3e-3) | 3 |
| alloys + concrete, all spectra | 120 | 18 | 32 |
| all | 280 | 43 | 102 |

So pure linear response holds to < 0.5 % in a hard D-T field up to 3.2e20 n cm⁻² for cooling
≤ 1 y, and fails in moderated spectra at vessel-level fluence.

**G1 failure is attributed to the test design, not the solver (unconfirmed for one descriptive
statistic):** every failing row is concrete or W — the materials with natural radioactive bulk
(K-40; W-180) — at long cooling, where the heat cancellation factor |B|/|U−B| reaches 1e7–1e12.
`U` was run at 1 n cm⁻² s⁻¹, so `U − B` loses most significant digits. The per-nuclide deviation
of 0.40 (descriptive) was not traced. A rerun needs a larger `U` amplitude or an operator emitted
by ACTINV directly, without subtraction.

**G4 is vacuous because `max_product_optical_depth` is always Mo-86**, with τ_p ≈ 2.4e7 at
1e13 × 1 y — an implied one-group loss cross section of order 1e10 b. That is a data or processing
defect (unexamined). It inflates τ_p so the slider never certifies. Code reading also found three
scaling errors in `scale_flux_result` that would surface once it does certify: `total_atoms_per_g`
(which includes the unirradiated bulk) is scaled ×v; zero-flux background activity is scaled ×v;
the photon source is left unscaled.

**Theory note for the next protocol:** at fixed flux the Bateman system is linear in the initial
composition at any fluence (coupled mode included), so composition superposition should hold beyond
the trace regime. P75 tested it only at unit flux in trace mode.
