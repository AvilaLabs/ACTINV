# P120 local verification

Opt-in single-case neutron rebinning passed 80 focused checks, independent
analytic activation/decay endpoints, one-cell mesh parity, cache reuse and
source-group uncertainty finite differences. The installed development Python
wheel passed eight new native/API/CLI tests and five existing object tests.
Workspace fmt/check/clippy/tests passed (486 tests; two existing ignored cases
are identified in the JSON). Eight control regressions, twelve runner regressions,
binding Rust checks, CLI test compilation and handbook/browser checks passed.

The historical replay ran all 50 unchanged commands at clean base `d1d14b3`
with a freshly built reference CLI. The candidate separately passed the unchanged
P116 G1 campaign: 35 requests, 138 comparisons and its mutation/refusal/repeat
checks. Existing frozen sources, scientific fixtures and FAIL archives were not
rewritten. The final runner's cancellation handling was strengthened after the
local historical replay and tested independently; candidate CI replays the
complete command sequence with that final runner.

The evaluated-data smoke verified catalog 1.1.0 / TENDL-2025 / ENDF/B-VIII.0
with JEFF-3.3 fallback and compared a one-group [1 eV, 14 MeV] input with an
independently integrated 709-group input: 1 g Fe56, 1e12 n cm^-2 s^-1,
300 s irradiation and 3600 s cooling. Activities were 811782.5485980073 and
620466.7272172584 Bq/g. Complete numerical vectors pass at 1e-12 relative/absolute.
The sparse inventory/activity union is compared by nuclide with absent entries
as zero, and every unmatched value is reported. The original raw field-shape
failure is retained: its largest unmatched inventory is about 4.4e-52 atoms/g.
This is mapping evidence, not validation against measurements.

`initial_observations.json` also preserves the initial checker failure from
requiring raw exact equality beyond the registered protocol. Neither the frozen
scientific tolerances nor the fixture was changed. Raw equality remains a reported
observation. The production solver was not changed to suppress either discrepancy.

For the optional evaluated smoke after building the development CLI and setting
up the existing data bundle, use:

```sh
python3 controls/p120_evaluated_smoke.py \
  --candidate-bin "$PWD/target/release/actinv" \
  --data-dir /path/to/actinv-data
```

On the maintainer's Linux workstation, execute this inside the enforced scope
specified in `AGENTS.md`; put `TMPDIR` on disk. The script verifies provisioned
files and downloads nothing. The output directory is fresh on every attempt.

The JSON records contain actual argv, exits, cgroup limits, software/data hashes,
reference/candidate identities and the complete changed-source binding.
The implementation requires the development source; the published 1.4.0 package
does not contain the option. Broader waste qualification remains open.
GitHub Actions observations are recorded separately after publication.
