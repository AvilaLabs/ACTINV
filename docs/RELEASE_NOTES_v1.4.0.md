# ACTINV v1.4.0 — release notes

ACTINV 1.4.0 adds photonuclear activation, hydrogen/helium gas production, transport-tally error
propagation, impurity budgets for clearance, lean mesh output, and an OpenMC R2S adapter. It also
corrects trace mode for multi-element materials and makes mesh runs and workbench sweeps faster.
Every addition landed under a registered protocol whose verdict is recorded in `results/` and in
the ledger. Schemas are additive; outputs change only where "Results to re-check" says so.

Upgrade the Python package with:

```bash
python -m pip install --upgrade actinv
```

Rust CLI users can install this exact version with:

```bash
cargo install --locked --force actinv-cli --version 1.4.0
```

## Results to re-check

- **Trace-mode runs on multi-element materials.** Trace mode used to drop production into
  nuclides that are also bulk constituents: silently from a tracked precursor (Fe-59 → Co-59 in
  a cobalt-bearing alloy), and through the `bulk_production_dropped` ledger from another bulk
  nuclide. Such products now get a tracked state beside their constant reservoir, so trace mode
  approximates only the constancy of the bulk. Composition additivity in trace mode went from 0.51
  to 6.8e-12. Trace-mode results for multi-element materials move; coupled mode is bitwise
  unchanged (P75b, P77).
- **Activity of a nuclide that is both tracked and bulk.** Its tracked activity used to overwrite
  the bulk activity of the same nuclide in the output and the response snapshot; the two now add.
- **Libraries built without `--decay`.** Rank-compressed isomer labels could collide with
  catalog-matched ones, so two physically different states shared one label. Such rows are now
  explicit leakage with a ledger line recommending `--decay`. Builds with decay data are
  byte-identical (P99).
- **Workbench live sweep.** Every slider position is now solved, including positions that change
  only `spectrum.total`; the earlier flux-scaling shortcut is removed (P78). The v1.3.1 notes
  described it as `options.flux_scale`, but no such spec option existed.

Each landing was also checked bitwise against the master it merged into, on the recorded neutron
specs, mesh profiles, proton library and P32 import, so outside the items above results should not
move.

## What changes

### Photonuclear (incident-gamma) activation

- `build-library --projectile gamma` builds 162-group libraries from the TENDL `g` sublibrary, and
  `projectile: "gamma"` runs them with photon-labelled flux, including OpenMC photon tallies and
  mesh runs. Mesh runs refuse flux whose particle label does not match the projectile.
- Single-neutron production is counted once. MTs without MF=8 resolve their ground-state residual
  from the MT definition. Photofission is a total only; evaluations with photofission product
  yields fail closed.
- Under `--profile tendl`, the TENDL-2017 encodings are accepted: the IZAP=0 photofission total
  (P98), and MF=3 tables that start above threshold, which are extended below their first point by
  the MF=10 state sum so the threshold group is kept (P100).
- Qualification (P101): against FISPACT-II's processed TENDL-2017 `gxs-162` records on 8 nuclides,
  95/95 non-MT5 one-group values agree within 2e-3 (largest difference 1.3e-6), all below 30 MeV.
  Above 30 MeV these files carry only MT5. FISPACT-II's processing of MT5 follows a different
  rule, which is reproduced within its printed precision on all 1,002 sections. ACTINV integrates
  the MT5 product exactly and is checked against independent code to 1.4e-13. The full
  2,850-target TENDL-2025 build has no failures.
- **Not included:** no gamma library is published through `actinv data fetch`, there is no
  temperature treatment for gamma, and photofission product yields are out of scope. Build with
  `--decay` for correct isomer labels.

### Hydrogen and helium gas production

- `options.gas` (off by default) routes the light ejectiles of every covered MT, and decay alphas
  and protons, into the chain as H1, H2, H3, He3 and He4. Each step reports `atoms_per_g`,
  `produced_atoms_per_g`, produced `appm`, and `inventory_appm`, which follows the FISPACT-II
  `APPM OF` convention and includes initial content.
- Against FISPACT-II/TENDL-2017 on the 132 FNS experiments, 403/403 gated pairs agree within ±10 %
  (P95; P92 failed on the appm convention first). Neutron-only; refused with `uncertainty`;
  ternary-fission gas is not covered. With gas off, output is byte-identical.

### Transport-tally statistical error as an uncertainty channel

- `uncertainty.channels: ["flux"]` with a per-group `spectrum.relative_error`, or a mesh cell's own
  flux-file error, propagates each group's declared statistical error to first order (diagonal).
  It can run alone without a covariance sidecar, or alongside MF=33.
- Each response reports `flux_sensitivities` and the fully correlated bound
  `flux_fully_correlated_bound`. Checked against central finite differences on an unseen spec set
  (2097/2097 within tolerance) and against sampling on the P32 cube (P97, after P93 and P96
  failed). Systematic transport errors are not included.

### Impurity budgets for clearance

- `actinv budget` reports the IAEA clearance index from one coupled solve per element, with the
  spec margin factor, joint and single-impurity limits (or the reason none exists), top nuclides,
  and uncovered activity per target. Every emitted limit is re-solved at its composition and
  compared with the prediction; `--no-verify` skips that (P79). See `docs/BUDGET.md`.
- Python: `actinv.budget(budget, base_dir=None, verify=True)` returns the same document as the
  command line apart from timing keys (P89, after P87 failed).

### Mesh and R2S

- Mesh `cell_result_fields` accepts dotted `steps.<field>[.<key>...]` entries, built directly from
  the selected fields: on `ss316_r2s`, 1.26× faster at one thread and 4.6 % of the bytes. Output
  without dotted entries is unchanged (P90).
- `contrib/openmc_r2s`: an OpenMC 0.15.3 `R2SManager` subclass that replaces OpenMC's depletion
  step with an `actinv mesh` run and hands the decay photon sources back to OpenMC. Its default
  keeps only `steps.photon_source.groups` per step. Known limits are listed in its README.

### Performance

All bitwise identical:

- Groupwise collapse loops only over the intersection of the flux window and each row's stored
  span: mesh runs 2.0–3.4× faster (P77).
- Mesh runs collapse up to 16 cells per pass over the library, and single runs no longer clone the
  decay table: one-thread mesh runs 1.33–1.77× faster (P81).
- Mesh cell records carry the result text verbatim: SS316 with photon output 1.77× faster at three
  threads (P83).
- The prepared-run cache keeps groupwise data in memory after two spectra: a flux-only slider move
  on SS316 went from about 2.5 s to about 0.15 s (P85, P86).

## Desktop and browser

The software release does not change the published desktop preview, which has its own release
history. See [Versions and releases](guide/releases.md) for the desktop version and its solver.

## Known limitations

All limits in the [qualification boundary](guide/qualification.md) and
[data limitations](DATA_LIMITATIONS.md) still apply. Each verdict cited above records its exact
scope; the [changelog](../CHANGELOG.md) has the full list of changes.
