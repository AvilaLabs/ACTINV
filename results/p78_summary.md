# P78 — retire the P70 flux-scaling shortcut in the live sweep: outcome

Protocol `protocols/ACTINV-P78_PROTOCOL.md` (sha256 `24e285bf…c2ac`, frozen before the build).
Branch `slider-flux-scale-off` from `6160035`. Verdict: `results/p78_verdict.json`
(`controls/check_p78.py`). Executed 2026-09-29 under the 6G cgroup cap, `CARGO_BUILD_JOBS=1`.

## Frozen verdict

| gate | result | number |
|---|---|---|
| G0 protocol registered | **PASS** | `24e285bf…` in `protocol_hash.txt` |
| G1 fmt / clippy `-D warnings` all targets / tests | **PASS** | rc 0 / 0 / 0; 25 passed, 1 ignored |
| G2 no residue | **FAIL** | 1 match: `smoke.rs` `if !fv["flux_scale"].is_null()` |
| G3 live control `g1_p69_live.py` | **PASS** | 7/7 checks; flux-only point solved, not scaled |
| G4 wasm lib clippy | **PASS** | rc 0 |

Master commit permitted by the protocol's own rule (G1–G3): **no**.

## Why G2 failed (post-hoc, not a revision of the verdict)

The single match is the smoke test's assertion that a flux-only point carries **no** `flux_scale`
block, which change 3 of the same protocol requires. The gate's pattern did not exclude the negative
check it mandated. No scaling code remains in the crate.

## Cost of the change (descriptive)

Every flux-only slider move is now a full solve, and a cache miss: the prepared-run fingerprint
(`run.rs::prepared_fingerprint`) includes `spectrum`, so a new `spectrum.total` re-prepares.
On the smoke fixture (`examples/fns_fe_5min.json`, TENDL-2025, 21 steps, rate prune + screen):

- debug workbench build: 23.7 s for the flux-only point (warm screened point: 0.7 s);
- release CLI `target/p75/actinv`, cold, same injected spec at 2.5× flux: **0.99 s** wall, 131 MB.

Excluding a pure normalisation from the fingerprint (rescaling the prepared flux instead) would make
flux-only moves warm again; not done.

## Other observations

- The committed `results/g1_p69_live.json` had 5 checks: the three P70 checks added in `fde8897` were
  never recorded as run. It is regenerated here (7 checks).
- `docs/RELEASE_NOTES_v1.3.1.md` lists `options.flux_scale` as a spec option; none exists (the P70
  path was workbench-only). The release notes are historical and are not edited; the CHANGELOG
  Unreleased entry says so.

## Amendment A (post-hoc, 2026-09-29)

`protocols/ACTINV-P78_AMENDMENT_A.md` (sha256 `2997990f…29d4`) replaces the G2 pattern with one that
matches code defining or emitting flux scaling, not a read-only absence check. G2a **PASS**
(0 matches); `results/p78a_verdict.json` permits the master commit. The original verdict above is
unchanged. Decided under the owner's delegation of procedural calls.
