# ACTINV P50 — measurement-priority (value-of-information) ranking

Opened 2026-09-25. Executes the roadmap's P50 row: given a spec that declares
uncertainty, emit a ranked table of the nuclear-data parameters — MF=33 reaction
channels, MF=8/MT=457 decay constants, MF=8/MT=454 fission yields — that carry
each requested response's propagated variance, so "which measurement most buys
down this band" is a product answer, not a research exercise. No shipping code
(FISPACT-II, ALARA, OpenMC, SCALE) publishes per-channel variance attribution
inside a qualified run result.

This phase adds one optional field to `UncertaintyOptions` (`voi`) and one
optional block to each emitted `ResponseUncertainty` (`voi`). Runs without the
flag are byte-identical to pre-P50 results; no sealed evidence, solver numerics,
spec schema field semantics, or default data change. Prior verdicts are
preserved byte-for-byte.

## Why this phase exists

P43–P49 made ACTINV propagate bands and decide designs under them. The next
question an analyst asks is "the band is too wide — what do I measure?". Today
that answer is hand-rolled sensitivity archaeology. P50 makes it a ranked,
fully-evidenced table: each parameter's share of the response variance, its
declared standard uncertainty, and its coverage status — the measurement-
priority answer for activation.

## Frozen scope

P50 may:

- extend `actinv-spec-1` `uncertainty` with optional `voi` (absence preserves
  pre-P50 output bytes exactly), emitting `voi` inside each requested
  `ResponseUncertainty` per step;
- add controls, examples, tests, documentation and an independent checker under
  `controls/`, `examples/`, `results/`, `protocols/`, `docs/`,
  `crates/actinv-core/` and `crates/actinv-cli/`;
- implement the ranking purely as reporting over quantities the qualified
  worker already computes — sensitivities, covered-parameter positions, the
  collapsed covariance, and per-channel declared sigmas. No new solve path, no
  new numerics, no approximation of the band itself.

P50 may not:

- alter any emitted value of a run that does not request `voi` — the flag is
  opt-in and absence must be byte-identical;
- rank or score a parameter with no covariance coverage: such parameters are
  ledgered `unranked` with their sensitivity magnitude, never assigned zero or
  fabricated variance;
- silently drop correlated rows: MF=33 share attribution uses the full
  covariance matrix, including off-diagonal terms; negative shares under
  anticorrelation are emitted, not hidden;
- introduce an approximation to the propagated variance to make the ranking
  cheap — the ranking consumes the same `covariance_barn2` and sensitivity
  vectors the band consumes;
- claim measurement priorities beyond the declared channels — decay/yield
  parameters participate only when their channels were requested, exactly as
  their band contribution rules state.

## Frozen definitions

**Spec syntax** — `uncertainty.voi` (optional object, `deny_unknown_fields`):

```json
"voi": { "top": 20 }
```

- `top` (required): integer 1–256; the number of ranked parameters emitted per
  `(step, response)` pair, in `|variance_share|` descending order.
- Any other key: hard validation error.
- `voi` without `uncertainty.responses` (or on a run whose uncertainty block
  produced no response bands) emits no `voi` blocks — it is a reporting
  augmentation of the band path, not a standalone mode.

**Per-parameter variance share** (the frozen ranking quantity):

- *MF=33 channel:* for covered parameter `i` with sensitivity `s_i` and the
  collapsed covariance `Σ` over covered parameters, `share_i = s_i·(Σ·s)_i`.
  `Σ_i share_i = V` exactly (up to the round-off the band already reports).
  Shares may be negative under anticorrelation; ranking is by `|share_i|`, and
  the sign is emitted.
- *Decay-constant channel:* `share_i = (s_i·σ_i)²` (diagonal, by the channel's
  declared model).
- *Fission-yield channel:* `share_i = (s_i·σ_i)²` (diagonal, by the channel's
  declared model).
- Parameters with `covered == false` (XS rows outside the covariance, decay
  files without MF=8/MT=457 `dT`, yields without `DY`): emitted only in the
  `unranked` summary — count and L2 sensitivity magnitude per family — never
  ranked.

**Emitted `voi` block** inside each `ResponseUncertainty` (only when requested):

```json
"voi": {
  "top": [
    {"channel": "cross_section_mf33", "parameter": {…SensitivityParameter…},
     "sensitivity": s_i, "variance_share": share_i, "share_fraction": share_i/V},
    {"channel": "decay_constants", "parameter": {…DecayParameter…},
     "sensitivity": s_i, "variance_standard_deviation": σ_i,
     "variance_share": share_i, "share_fraction": share_i/V},
    …
  ],
  "total_propagated_variance": V,
  "unranked": {"cross_section_mf33": {"count": n, "sensitivity_l2": m},
               "decay_constants":    {"count": n, "sensitivity_l2": m},
               "fission_yields":     {"count": n, "sensitivity_l2": m}}
}
```

- `V` is the response's propagated variance over every covered channel the band
  used (MF=33 matrix plus declared diagonal channels), i.e. the variance the
  emitted band was built on — not a recomputation.
- `share_fraction` divides by `V`; when `V` is zero or nonfinite it is emitted
  as `null`, ranking falls back to `|sensitivity|` order, and the computed
  shares are still emitted — the table names which channels the response sees
  without fabricating a variance story.
- `unranked` appears for every sensitivity-bearing family that has uncovered
  members, with `sensitivity_l2 = sqrt(Σ s_i²)` over that family's uncovered
  parameters.

**Ranking correctness rule (checker-visible):** sorting `top` by
`|variance_share|` descending must reproduce the emitted order, and the emitted
`top` set must be exactly the `top` largest `|share|` values over all
sensitivity-bearing covered parameters — no channel family may be favoured or
excluded.

## Demonstration

`examples/optimize_ra_steel/opt_v2_winner.json` — the P49 campaign winner
composition (Fe balance, Ni 0, Mo 0, Nb 0.113 wt%) re-run with `voi.top = 20`
and the full `tendl-2025` corpus. The product answer: the ranked parameters
carrying the Nb-94 constraint band and the 1-y heat band — "these two or three
measurements buy down the feasible region".

## Gate sequence

- **G0 (seal).** This protocol hashed; artifact set pinned (core uncertainty
  path, CLI, controls, checker, demo spec); recorded in
  `results/g0_p50_seal.json`. Nothing downstream may change sealed files except
  by append-only amendment.
- **G1 (mechanics).** Determinism: identical rerun emits byte-identical `voi`
  blocks. Flag absent ⇒ result byte-identical to a run with the flag removed.
  Spec schema round-trips `voi`; `top` bounds enforced; unknown keys rejected.
- **G2 (controls).** Frozen synthetic corpus cases: (a) planted dominant
  channel ranks first; (b) emitted shares sum to the emitted total variance
  within fp tolerance; (c) an uncovered parameter appears under `unranked` with
  nonzero sensitivity, never ranked; (d) a constructed anticorrelated pair emits
  a negative share for the correct member and still sums to `V`; (e) `top`
  smaller than the covered count truncates without losing the top element.
- **G3 (demonstration).** The declared RA-steel winner run executes; the voi
  tables are emitted for the declared responses; measured wall time ledgered.
  The ranked table must name at least one specific, checked parameter (not a
  degenerate all-uncovered result).
- **G4 (independent checker).** `controls/check_g4_p50.py` re-derives every
  emitted share from the recorded sensitivities plus the pinned corpus
  covariance (no code from the emit path), re-sorts, verifies the order and the
  truncation set, recomputes `unranked` summaries, and rejects planted
  mutations (wrong ranking, zeroed share, fabricated coverage).

**Minimum gate input** (standing rule 7): G1/G2 use the small synthetic
covariance fixtures (second-scale runs); only G3 executes a full-corpus run
(~10–15 min class). The phase needs no campaign.

## Amendment rule

Post-G0 changes to sealed artifacts are append-only amendments: recorded in
this file with reason and sha, then re-sealed. Any change to the frozen
definitions above is a new phase, not an amendment.

## Amendment 1 — mechanical repair (record filename)

Post-seal, the G0 seal record is emitted at `results/g0_p50_seals.json`
(following the P49 `g0_p49_seals.json` convention); the G0 paragraph above
names `results/g0_p50_seal.json`. Filename only — no definition, ranking rule,
gate, or artifact content changes. Sealed artifact shas unchanged; the
protocol file itself is re-hashed under this amendment.

Amended protocol sha256: recorded in the phase verdict.

## Amendment 2 — mechanical repair (checker row bound)

Post-seal, `controls/check_g4_p50.py` bounded the Python collapse with
`selected = all library rows` — the full corpus covers ~106k rows, exceeding
the sealed memory scope. Repaired to draw the active row set from the emitted
sensitivity parameters, the same boundary the runtime draws. No definition,
ranking rule, gate, share formula, or tolerance changed. Sealed
`check_g4_p50.py` sha superseded by the amended module; all other sealed
artifacts unchanged.

Amended checker sha256: recorded in the phase verdict.
