# ACTINV-P25 — Amendment B: per-stratum coverage floors

Frozen after the G1 census and G2 mechanism traces, before any repaired build
is scored. (The protocol text calls this amendment "Amendment A"; that
identifier was consumed by the MF=8 field-offset correction, so the
coverage-floor freeze is recorded here as Amendment B.)

Authority: P25 protocol §"Frozen acceptance gates" item 3 — the scored-coverage
floor per stratum is fixed after the diagnosis census and before any repaired
build is scored.

## Frozen floors

Scored-share floors are computed against the **full** fixed eligible
population (the 1,859-row P18b eligible ledger):

| stratum  | eligible rows | floor share | floor rows |
|----------|--------------:|------------:|-----------:|
| neutron  |           469 | ≥ 40% | ≥ 188 scored |
| proton   |           695 | ≥ 60% | ≥ 417 scored |
| deuteron |           143 | ≥ 50% | ≥  72 scored |
| alpha    |           552 | ≥ 55% | ≥ 304 scored |

Basis: `docs/P25_DIAGNOSIS.md` §5–6 — the floors sit strictly below the
repairable-population projections (63% / 77% / 60% / 74%) so that rows which
still land on `undefined_ratio` or residual construction failures after repair
are absorbed rather than waived.

## Secondary reconciliation envelope (grid-density artifacts only)

The frozen 0.001 standard envelope stands unchanged for every class. One
narrow, mechanism-gated extension is authorized:

- A group-level emitted-sum excess may be reconciled by the common T/S factor
  up to relative excess **0.03** only where the exact-decimal discriminator
  proves, for that (MT, ZAP) family, that (i) every declared product ordinate
  is consistent with the MF=3 total within the standard envelope, and (ii) the
  excess occurs solely at energies lying strictly between declared product
  gridpoints — i.e. the inconsistency is provably a grid-density
  interpolation artifact, never a declared-value contradiction.
- Every application is ledgered `interp_artifact_reconciled` with the group,
  scale factor and discriminator result; the per-file and per-stratum counts
  are republished in `results/g4_p25_repairs.json`.
- Any excess at a declared product gridpoint beyond the 0.001 envelope fails
  closed exactly as before.

## Unchanged

- All other frozen gates, thresholds and the P18/P18b records are untouched.
- Rows whose construction still fails after repair keep named
  `construction_failed:<class>` outcomes and count against coverage.
- Rows dropped for proven `genuine_source_inconsistency` may be excluded from
  a *reported* adjusted denominator only where the G2 trace verifies the
  class; the frozen floors above use the full denominator regardless.
