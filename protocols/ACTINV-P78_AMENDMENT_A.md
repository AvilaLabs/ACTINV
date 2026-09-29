# ACTINV-P78 Amendment A — G2 residue scan (post-hoc)

Date: 2026-09-29. Status: **post-hoc**, written after the P78 verdict (`results/p78_verdict.json`,
G2 FAIL) was known. The original verdict is not revised and stays on record.

## Defect in the original gate

G2 searched for the string `flux_scale"` anywhere under `crates/actinv-gui/src`. Change 3 of the same
protocol requires the smoke test to assert that a flux-only point carries **no** `flux_scale` block,
which needs that string. The gate could not pass under its own protocol.

## Amended G2 (G2a)

No code under `crates/actinv-gui/src` defines or emits flux scaling. The scan pattern is:
`scale_flux_result`, `FLUX_SCALE_MAX_CORRECTION`, `LINEAR_CONTAINERS`, `walk_and_scale`, an
assignment `["flux_scale"] =`, or a JSON key `"flux_scale":`. A read-only absence check such as
`fv["flux_scale"].is_null()` is allowed.

## Decision rule

The master-commit rule of P78 is evaluated with G2a in place of G2. The owner has delegated this kind
of procedural call (2026-09-29). Recorded in `results/p78a_verdict.json` by
`controls/check_p78.py --amendment A`.
