# ACTINV P54 Protocol — Certified Probabilistic Clearance

Sealed 2026-09-26 under standing rules 1–7. Hash of this file is recorded in
`results/g0_p54_seals.json`; any change after G0 is an append-only amendment.

## Goal

Regulatory clearance of activated material is a *decision under uncertainty*:
the sum-of-ratios criterion `S = Σᵢ aᵢ/Lᵢ` (specific activity over the
nuclide's clearance limit, IAEA RS-G-1.7 convention) must stay below 1 — and
the honest question is not the point value but **P(S < 1) given the propagated
nuclear-data covariance**. Every incumbent emits a point estimate or a
worst-case bound; none emits a certified probability interval. ACTINV already
propagates MF=33 covariance to per-nuclide activity bands — the gap between
"banded activity" and "P(clears)" is arithmetic, not physics. That is exactly
the decision-layer moat: this command exists in no other activation tool.

P54 emits the certified statement: for each cell (or single run result),
the sum-of-ratios, its propagated σ under both declared combination rules, a
probability interval `P(S < 1) ∈ [P_lo, P_hi]`, and a classification with an
explicit confidence threshold — plus full coverage ledgers so a nuclide that
never enters the statistic is never silently ignored.

## Inputs

- **Result document**: either an `actinv run` result JSON (single object with
  `steps`) or an `actinv mesh` NDJSON (`record:cell` entries with
  `result.steps`). Auto-detected by file shape. At the named STEP each record
  must carry `activity_Bq_per_g` and `uncertainty.responses` with
  `activity:<nuclide>` entries (`nominal`, `mf33_standard_uncertainty` or
  `combined_standard_uncertainty` in Bq g⁻¹).
- **Clearance limits table**: bundled `data/clearance_iaea_2004.json` — the
  IAEA unconditional-clearance concentration table (2004 basis, transcribed
  from the machine-readable copy shipped in ALARA
  `data/IAEA.clearance.2004.Bq_kg`, converted Bq kg⁻¹ → Bq g⁻¹;
  spot-verified against published RS-G-1.7 Table 2 values: Fe-55 = 1000,
  Co-60 = 0.1, Mn-54 = 0.1, Ni-63 = 100, Nb-94 = 0.1, Tc-99 = 1 Bq g⁻¹).
  `--limits PATH` accepts a user table of the same schema; its sha256 is
  recorded in the output header either way. Regulatory caveat recorded in
  the table's `notes` field and echoed in the output: national transpositions
  differ, progeny-included nuclides carry their IAEA marker semantics, and a
  nuclide absent from the table is **not** thereby cleared.
- **Confidence threshold** T (default 0.95): the certification bar.

## Command

```
actinv clearance INPUT STEP [--limits PATH] [--confidence T] OUT.ndjson
```

## Emitted document — `actinv-clearance-1` (one JSON object per line)

- **Header**: schema, input sha256, limits table sha256 + `source` provenance
  string, step, confidence threshold T, and `probability_model`: the declared
  assumption set (below).
- **Per-cell records** (`record: clearance`, `cell_index`, `cell_id`):
  - `sum_ratio` S, `sigma_sum_independent`, `sigma_sum_conservative`
  - `p_clear_interval = [P_lo, P_hi]` (bounds defined below)
  - `classification` ∈ `{deterministic_clear, deterministic_fail,
    clears_certified, fails_certified, indeterminate}`
  - `dominant_ratio` / `dominant_sigma`: the nuclide with the largest rᵢ and
    the largest σᵢ/Lᵢ contribution (the nuclide a measurement campaign should
    target — VoI handoff point)
  - `nuclides`: per-nuclide `{activity_Bq_g, limit_Bq_g, ratio, sigma_Bq_g,
    sigma_ratio, banded, in_table}`
  - `coverage`: `banded_ratio_share`, `unbanded_ratio_share` (fraction of S
    contributed by nuclides lacking a propagated σ — declared, never hidden),
    `unregulated_activity_share` (fraction of total specific activity held by
    nuclides absent from the limits table — excluded from S, flagged), and
    `regulated_count`
- **Footer**: `cells_evaluated`, `cells_certified_clear`,
  `cells_certified_fail`, `cells_indeterminate`, `cells_deterministic`, and
  `totals_cover` declaration. Wall-clock fields (`wall_time_s`) are declared
  nondeterministic and excluded from the determinism gate, same class as
  P52's `ms`.

## Math and rules (frozen)

1. Per nuclide: `aᵢ` = `activity_Bq_per_g[nuclide]` at STEP (absent or ≤ 0 →
   rᵢ = 0, skipped). `Lᵢ` = table limit. `rᵢ = aᵢ / Lᵢ`.
   `S = Σᵢ rᵢ` over nuclides with aᵢ > 0 **and** present in the table.
2. Banded σᵢ: `combined_standard_uncertainty` if present else
   `mf33_standard_uncertainty` (both Bq g⁻¹, MF33 XS channel only — the same
   coverage class as every prior band; decay/yield channels stay
   `not_evaluated`). Missing/zero σ → `banded: false`, contributes rᵢ
   nominally and counts into `unbanded_ratio_share`.
3. σ_S under the two declared rules:
   `σ_ind = √(Σᵢ (σᵢ/Lᵢ)²)` — independence bound;
   `σ_cons = Σᵢ (σᵢ/Lᵢ)` — fully-correlated bound. (The P53 joint covariance
   could place the true mixed correlation exactly between these; this phase
   ships the certified interval, not the point value.)
4. Probability model (declared): each banded aᵢ is a Gaussian marginal
   `N(aᵢ, σᵢ²)` — the linearized propagation the bands already represent.
   `P_lo = Φ((1 − S)/σ_cons)`, `P_hi = Φ((1 − S)/σ_ind)` — the interval is
   ordered because σ_cons ≥ σ_ind. If both σ are 0 the probability is
   degenerate: S < 1 → `deterministic_clear`, else `deterministic_fail`.
5. Classification: `clears_certified` iff `P_lo ≥ T`; `fails_certified` iff
   `P_hi ≤ 1 − T` (the generous bound cannot reach the bar); else
   `indeterminate`. Certification is deliberately two-sided-strict: the
   interval must *entirely* clear the bar — indeterminate is the honest
   middle, not a verdict.
6. Nuclide name normalisation: actinv `Co60m1`/`Sc45m1` ↔ table `Co-60m`/
   `Sc-45m` (dash stripped; trailing `m`/`m2` map to `m1`/`m2`). Table keys
   stay in RS-G-1.7 form; the output echoes the table label per nuclide.
7. Non-emitting or zero-activity nuclides contribute rᵢ = 0 and appear only
   in `regulated_count` bookkeeping — no fabricated uncertainty.

## Gates

- **G0** — seal: protocol + controls + `data/clearance_iaea_2004.json` sha'd
  into `results/g0_p54_seals.json`.
- **G1** — mechanics: fixture mesh + fixture run doc both emit well-formed
  `actinv-clearance-1`; rejections: missing uncertainty block, missing
  `activity_Bq_per_g`, step out of range, malformed limits file, confidence
  outside (0,1); ledger fields present and self-consistent.
- **G2** — exactness: closed-form Python re-derivation on a 2-group fixture —
  S, both σ rules, Φ interval, classification — to machine tolerance;
  boundary legs: S exactly 1, all-σ-zero (deterministic), a nuclide absent
  from the table (unregulated share), a nuclide with a=0 (skipped).
- **G3** — corpus demonstration: run on the existing sealed
  `results/p53_mesh.ndjson` (8 cells, real TENDL bands — zero new solve
  compute); report per-cell classifications, dominant nuclides, coverage
  shares.
- **G4** — determinism: two runs byte-identical modulo the declared
  wall-clock footer field.
- **G5** — independent checker: re-derives every emitted number from the raw
  input document + limits file (no emit-code reuse); planted mutations on
  S, on a nuclide σ, on a classification, and on a limits-table value must
  all be detected; checker asserts the canonical table spot values
  (Fe-55 = 1000, Co-60 = 0.1, Nb-94 = 0.1, Tc-99 = 1, Ni-63 = 100 Bq g⁻¹).
- **Limits control** (inside G5): table row count and the five canonical
  spot values verified; isomer name normalisation round-trip exercised.

## Out of scope

- Log-normal / Monte-Carlo activity distributions (the Gaussian-linearized
  interval is the declared model; richer sampling is a P56-adjacent question).
- Regulatory jurisdiction routing (national tables, conditioned clearance,
  surface-contamination limits, waste-class boundary rules beyond sum-of-
  ratios).
- Correlated mixed-material clearance via the P53 joint map (the machinery
  exists; the bounds shipped here already bracket it — a follow-on leg if the
  interval proves too wide in practice).
- Mass-weighted whole-component certification (needs per-cell masses the mesh
  format does not carry; declared deferral).

## Cost statement

Zero solver compute: arithmetic over emitted fields only. G3 reuses the
sealed P53 mesh artifact. The checker is pure text+math — seconds, not
minutes. Total expected wall: minutes for gates, dominated by nothing
heavier than JSON parsing a 500 MB mesh file.
