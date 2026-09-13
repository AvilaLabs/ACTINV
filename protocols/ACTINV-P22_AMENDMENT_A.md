# ACTINV P22 Amendment A — G4 identity gate: solver-semver normalization

**Date:** 2026-09-12. **Trigger:** the frozen G4 clause requires the rebuilt `1.1.0` artifacts to
re-run the four-surface normalized-identity battery with hashes that "must equal the G0 baseline
hashes exactly (the version string is compiled in but absent from normalized results — this proves
the bump is solver-inert)". The parenthetical premise is falsified by observation: the version
string is **not** absent from normalized results.

This amendment records the corrected gate. It does not change the G1–G3 comparisons, the held-out
read, the release-decision criteria, the scorecard requirements, or any sealed evidence of the
frozen protocol (`86f8509f58780a82923fcfdd9996db0e557a1eba845f553a0835b64f8a3cf971`).

## Diagnosis

- `normalized()` in `controls/g0_p21_battery.py` strips only `ms` and the `entry_point` fields.
- The run certificate carries `certificate.solver = "actinv-core <semver>"` — the documented
  solver-semver leaf. P10's amendments established the precedent of normalizing exactly this leaf
  when the solver version is the intentional change (see `docs/history/sessions/P10.md` and
  `protocols/ACTINV-P10_AMENDMENT_R.md`).
- Observed on 2026-09-12: the G4 run produced `03e65e71…` on all three single-run surfaces and
  `7af9862e…` on the mesh cell versus baseline `ebc307ff…` / `2fcef4f5…`. A field-level diff of
  the 1.0.1 and 1.1.0 normalized results shows the **only** differing leaf is
  `certificate.solver` (`actinv-core 1.0.1` → `actinv-core 1.1.0`).
- The same 1.0.1 binary (the G2 clean-clone artifact, sha256
  `07b23cf15e9e86f82e6e64a8295192785d68f1ecebd7d582c5970a3a41c269d9`, built from commit `c4a361f`)
  reproduces the frozen G0 baseline `ebc307ff…`/`2fcef4f5…` exactly in the current environment,
  confirming nothing else drifted.

## Amended G4 identity gate

The frozen requirement "identity hashes equal the G0 baseline exactly" is replaced by a strictly
stronger two-stage check:

1. **Baseline re-derivation.** The four-surface battery is re-run with the pre-bump (`1.0.1`)
   binary — the hash-pinned G2 clean-clone artifact — and must reproduce the G0 baseline
   normalized hashes **exactly** (no normalization beyond `ms`/`entry_point`). This proves the
   baseline still holds in the current environment.
2. **Solver-semver identity.** The battery is re-run with the post-bump `1.1.0` release binary and
   Python extension. After normalizing exactly the `certificate.solver` leaf (set to a fixed
   token), every surface's normalized result must be byte-identical to the corresponding
   solver-normalized stage-1 result, and the recorded raw (unmodified) hashes must be published
   alongside so the solver-field difference is visible, not hidden.

Both stages are recorded in `results/g4_p22_release_candidate.json`; `controls/check_g4_p22.py`
re-derives both comparisons and rejects planted mutations. A stage-2 pass proves the version bump
is solver-inert under the documented solver-semver normalization — the same claim the frozen text
intended, corrected for where the version string actually lives.
