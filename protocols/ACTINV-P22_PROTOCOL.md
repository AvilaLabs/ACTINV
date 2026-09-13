# ACTINV-P22 protocol — public re-score and release

Status: **frozen** at the opening commit. Amendments land as separate files;
this file is never edited afterward.

## Opening context

Roadmap row (post-v1 program, frozen by CB1): "Rerun frozen CB1 against
v1.0.0 and the candidate; score P17 held-out evidence; repeat open-code,
install, memory, runtime and mesh exercises; publish raw machine-readable
evidence, limitations and narrowly supported claims." Prerequisites:
P18–P21 complete — all are closed: P18b `P18b-FAIL`, P19 `P19-PASS`,
P20 `P20-PASS`, P21 `P21-PASS`, and the later-landed P23 `P23-PASS`.

P22 is the scorecard phase. The candidate is current master: the signed
v1.0.1 public release plus the P19 self-shielding, P20 uncertainty-channel,
P21 large-scale mesh, and P23 feed/removal/damage/reverse work. Every
improvement is re-measured against the frozen CB1 battery and every
remaining loss is named. The P17-sealed held-out evidence is read exactly
once here through unchanged scoring code.

## Candidate and comparator

- **Candidate**: `target/release/actinv` and
  `python/target/release/libactinv.so` built from the working tree at the
  post-freeze head, still at workspace version `1.0.1`. The G0 gate records
  both file SHA-256s, the head commit, `actinv --version`, rustc, kernel
  and the cgroup limits of the measurement scope.
- **Comparator**: the sealed CB1 record. `results/session_cb1.json` pins
  every `results/cb1_*.json` evidence file by SHA-256; G0 verifies each
  digest so the comparison baseline is provably the sealed one.
- Re-runs never overwrite sealed evidence: every P22 control writes
  `results/p22_*.json`. Where a frozen CB1 control is re-executed, its
  committed module source is imported and only its `RESULT` path is
  redirected — the measurement code itself is unchanged and its source
  SHA-256 is recorded.

## Gates

- **G0** — this file committed as `ACTINV-P22_PROTOCOL.md` (opening
  commit). All G0–G3 measurement runs on the unbumped `1.0.1` candidate;
  the `1.1.0` version bump happens only inside G4 and only when the
  G1–G3 evidence is green, so every measured leg and the recorded P21
  executed binary digest stay bound to the same candidate binary.
  `controls/g0_p22_seals.py` verifies: every prior verdict file exists
  with its expected verdict string (25 verdicts including `P18b-FAIL` and
  `P21-PASS`); every sealed CB1 evidence file hashes to
  `session_cb1.json`'s `evidence_sha256`; the executed-scale record
  `results/g3_p21_executed.json` exists, names 20,000 cells, and its
  `actinv_binary_sha256` equals the candidate binary's digest — the 20k
  executed evidence is the candidate's own, not another build's.
  `controls/check_g0_p22.py` independently re-verifies.
- **G1** — frozen numerical battery on the candidate, executed by
  `controls/g1_p22_battery.py` which imports the committed
  `cb1_numerical.py`, `cb1_alara.py` and `cb1_fns.py` modules and redirects
  only the output path:
  - each re-run's internal `pass` must be true;
  - deterministic metrics vs the sealed records within relative `1e-6`
    (the toolchain changed since CB1 — rustc 1.98.0 → 1.95.0 — so
    floating-point noise is anticipated; the band catches real solver
    regressions, not codegen drift):
    - `cb1_numerical`: every `worst.*` value no larger than the sealed
      value (equality expected; the band admits only noise),
    - `cb1_alara`: `worst_alara_vs_analytic_relative` and
      `worst_actinv_vs_analytic_relative` within the band, and
      `identical_inputs` hashes equal to the sealed record,
    - `cb1_fns`: `median_pooled_abs_log_C_over_E`,
      `p90_pooled_abs_log_C_over_E`,
      `pooled_geometric_mean_C_over_E` and
      `median_experiment_maximum_abs_log_C_over_E` within the band;
    - exact integer equality: `experiments_scored`,
      `experiments_all_points_within_30_percent`, `points_scored`,
      `positive_sigma_points` (the closest scored point sits 2.1e-4 from
      the 30% boundary, so the band cannot flip a count).
  - `controls/check_g1_p22.py` re-derives the comparisons from both
    records and rejects planted mutations.
- **G2** — install/memory/runtime/mesh exercises, executed by
  `controls/g2_p22_exercises.py`:
  - `cb1_first_use.py` re-run unchanged (RESULT redirect): the public
    `actinv==1.0.0` PyPI wheel still downloads, byte-matches the release
    record, installs, imports, and runs the first example;
  - `cb1_performance.py` re-run unchanged (RESULT redirect): internal
    checks pass; each process-level `median_ms` is within `2x` the sealed
    median and each peak RSS within `1.25x` the sealed value (machine-noise
    bands; fresh numbers are published regardless);
  - clean-clone build: `git clone` of the head commit into a fresh
    directory and `cargo build --release` must produce a working binary
    (`--version` runs) — the open-source build claim re-executed;
  - mesh exercise at the P21-evidenced scale: a fresh 1,000-cell
    reduced-field run on the pinned TENDL-2025 709-group library at
    `chunk_cells=64`, `threads=2` must close its footer, and its peak RSS
    must stay within `1.25x` the G3-recorded 1,000-cell leg
    (`402,251,776` bytes); the executed 20,000-cell record stands as the
    scale evidence (its binary digest was bound to the candidate in G0).
  - `controls/check_g2_p22.py` re-verifies every comparison and rejects
    planted mutations.
- **G3** — held-out read, exactly once, by `controls/g3_p22_heldout.py`:
  verify `controls/p17_scoring.py`, `controls/p17_heldout.py` and
  `controls/g5_p17_heldout.py` still hash to the values sealed inside
  `results/g5_p17_heldout.json` (`control_source_sha256`,
  `helper_source_sha256`, `checker_source_sha256`) plus the P17 protocol
  and amendment digests; then re-score all 94 sealed rows through the
  unchanged `p17_scoring` metric functions and require the family metrics
  to reproduce the sealed values within relative `1e-12`. The held-out
  outcome is published as-is — it remains a failure under P17's frozen
  gates and the scorecard says so; no post-read metric, exclusion or
  threshold change is permitted. `controls/check_g3_p22.py` re-derives the
  metrics from the sealed rows and rejects planted mutations.
- **G4** — scorecard, capability re-score and release-candidate assembly,
  executed by `controls/g4_p22_release.py`:
  - `docs/COMPETITIVE_BENCHMARK.md` gains a P22 candidate column/section
    carrying the fresh G1/G2 numbers, names every remaining loss, and
    updates the capability cells the P19–P23 work changed (self-shielding
    is no longer "absent"; covariance channels and mesh scale cite their
    executed records). Every token `controls/check_cb1.py` requires stays
    intact.
  - If and only if the G1–G3 evidence is green, the workspace version is
    bumped to `1.1.0` (committed), the release binary and Python wheel are
    rebuilt, the four-surface normalized-identity battery is re-run and
    must equal the G0 baseline hashes exactly (the version string is
    compiled in but absent from normalized results — this proves the bump
    is solver-inert), and `results/g4_p22_release_candidate.json` records
    every artifact SHA-256, sizes, versions and provenance.
  - The release decision record evaluates the frozen criteria:
    `release_ready` iff every prior verdict is present (P18b-FAIL
    included — it blocks its own artifacts, not the additive feature
    set), the G1/G2 comparisons hold, the held-out metrics reproduce
    exactly, and the RC artifacts built and identity-proved. The decision
    and its reasons are recorded; tagging and publishing stay a separate,
    user-authorized action and are not performed by P22.
  - `controls/check_g4_p22.py` verifies the scorecard tokens, the artifact
    record, and the decision arithmetic.
- **G5** — `controls/check_p22.py`: independent closure. Re-runs every
  gate checker, re-derives the comparisons itself (CB1 seal digests,
  metric deltas, held-out metric reproduction, release-decision logic),
  re-asserts all 25 prior verdicts, verifies `MANIFEST.sha256`, rejects
  planted evidence mutations under `--self-test`, and writes
  `results/p22_closure_check.json` plus `results/verdict_p22.json`
  (carrying the release decision verbatim).

## Explicit non-claims

- No metric, threshold, eligibility or exclusion changes after the
  held-out read.
- No "best overall" claim — only workload-scoped, evidenced claims.
- A release decision of "ready" authorizes nothing by itself: the version
  tag, GitHub release and PyPI upload remain separate maintainer actions.
- The P17 held-out families stay FAIL; nothing in P22 rewrites that
  outcome.
