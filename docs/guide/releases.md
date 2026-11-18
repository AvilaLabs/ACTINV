# Release history

Software, nuclear data and the desktop have separate version numbers. Record all three with a result.

| Component | Version covered here | Release |
| --- | --- | --- |
| CLI, Python, and Rust workspace | 1.7.2 | [v1.7.2](https://github.com/AvilaLabs/ACTINV/releases/tag/v1.7.2) |
| Embedded nuclear-data catalog | 1.2.0 | [data-v1.2.0](https://github.com/AvilaLabs/ACTINV/releases/tag/data-v1.2.0) |
| Desktop preview | 0.6.0-preview.1, using solver 1.7.0 | [desktop preview](https://github.com/AvilaLabs/ACTINV/releases/tag/desktop-v0.6.0-preview.1) |

`actinv --version` prints the software version and `actinv data manifest` the catalog it embeds. A source build between releases can carry a version number that hasn't moved yet, so record its commit too.

Each release below says what it adds and, under **Results that may change**, what can make a rerun of an old problem
differ. The [changelog](https://github.com/AvilaLabs/ACTINV/blob/master/CHANGELOG.md) has every change, and
[Known data limitations](data-limits.md) covers the data: a newer executable doesn't make an evaluation better.

## 1.7.2

Release 1.7.2 makes `actinv data fetch` work for first-time installs again. The IAEA site now refuses automated
downloads of the two decay files, and fetch now takes ACTINV's byte-identical copy when that happens. The OpenMC R2S
adapter also ships with the Python package (`from actinv.openmc import ActinvR2SManager`), with an opt-in uncertainty
band on every photon tally; see [Use ACTINV with OpenMC](openmc.md#inside-openmcs-r2s-workflow).

It also adds mixture self-shielding (`self_shielding.mixture`) and faster rate pruning, and fixes the
potential-scattering estimate of `dilution: "composition"` self-shielding. The
[1.7.2 release notes](https://github.com/AvilaLabs/ACTINV/blob/master/docs/RELEASE_NOTES_v1.7.2.md) have the
detail.

**Results that may change.** No default result changes. Rerun calculations that used `self_shielding` with `dilution: "composition"` on materials whose main scatterers are light nuclides missing from the table (concrete, water); from 1.1.0 to 1.7.1 they were over-shielded.

## 1.7.1

Release 1.7.1 fixes resonance capture in libraries and self-shielding tables you build from R-matrix-limited
evaluations. These include ENDF/B-VIII.1 and FENDL-3.2c tungsten, copper, Fe-54 and Fe-57; from 1.5.0 to 1.7.0
their thermal capture was off by factors up to 9. If you built such a library or table, rebuild it. The TENDL-2025
data that ACTINV downloads is unaffected.

It also fixes the `study.robustness` cross-section sampler and adds:
- national clearance tables (`actinv clearance --jurisdiction de|uk|ca`);
- a shutdown-dose source covariance export (`actinv export-r2s-cov`);
- `build-shielding --profile` and `--workers`, and a `profile` for geometry self-shielding.

The [1.7.1 release notes](https://github.com/AvilaLabs/ACTINV/blob/master/docs/RELEASE_NOTES_v1.7.1.md) list what to
re-check.

**Results that may change.** No default result changes. Rebuild any library or self-shielding table you built from R-matrix-limited evaluations (ENDF/B-VIII.1 and FENDL-3.2c tungsten, copper, Fe-54, Fe-57 and others) with 1.5.0 to 1.7.0, and rerun what used it. `study.robustness` bands from `cross_section_mf33` can also differ from 1.7.0.

## 1.7.0

Release 1.7.0 adds impurity screening for fusion materials (`actinv impurities`), a damage table in the data catalog
(1.2.0) so that dpa, exact NRT dpa, arc-dpa and He/dpa need no table to build, FISPACT-II parity additions
(estimated photon spectra, fission-yield and uncertainty keywords in `actinv import-fispact`, and
`actinv build-damage --method kinematic`), `actinv new --material` with a cited material library and standard spectra,
and `actinv report --format html`. The desktop 0.6.0-preview.1 reaches most of the command line: an Export page, and
pages for impurity screens, certification, optimization, comparison, waste questions, transport meshes and
measurements; the browser sets these up and opens two results solved ahead of time. The pathway closure no longer
counts noise-level populations, and `actinv decide` computes the measurement-design blocks it used to leave null. [What's new in 1.7](whats-new-1.7.md) is a tour; see also the
[1.7.0 release notes](https://github.com/AvilaLabs/ACTINV/blob/master/docs/RELEASE_NOTES_v1.7.0.md) and the
[desktop 0.6.0-preview.1 notes](https://github.com/AvilaLabs/ACTINV/blob/master/docs/releases/desktop-v0.6.0-preview.1.md).

**Results that may change.** Two defaults moved: `pathway_closure` counts only populations above 1e-12 of the step's largest, so the quick start reports 3.9e-15 instead of 4.117, and `actinv decide` computes the `design.top_reactions` blocks it reported as `ill_conditioned`. [Results that may change](whats-new-1.7.md#results-that-may-change) has the detail.

## 1.6.1

Release 1.6.1 fixes one defect of 1.6.0. A trace-mode run with pathway output, which the default outputs
include, could stop with an `index out of bounds` error. This happened when a reaction of a bulk nuclide led to a
product with no decay data. Runs that completed in 1.6.0 give the same results in 1.6.1.

## 1.6.0

Release 1.6.0 adds waste classification under nuclear-data uncertainty (`actinv waste uncertainty`), impurity budgets
under uncertainty (`actinv waste budget uncertainty`), classes for packages of discrete items with encapsulation
(`actinv waste package`), the waste commands in the Python API (`actinv.waste`, `actinv.twin`) and a Waste class page
in the desktop. Rate pruning now drops a nuclide only when a proven bound on its population is below the threshold,
and the ledger's `rate_pruning` block reports bounds on the change in total activity and heat. See the [1.6.0 release notes](https://github.com/AvilaLabs/ACTINV/blob/master/docs/RELEASE_NOTES_v1.6.0.md).

**Results that may change.** Rate pruning now drops a nuclide only when a proven bound on its population over the whole schedule is below `bmin_atoms_per_g`; the previous rule could under-estimate a population. The [numerical method](method.md#time-integration) describes the bound.

- A few noise-level nuclides are kept or dropped differently. Over the 132 FNS decay-heat experiments at the default threshold, nineteen change heat by more than 1e-6 (at most 1.4 %) and total activity by up to 48 %, only at the end-of-irradiation step. The cause is Be-8 from C-12(n,n'α) in carbon-rich samples: it lives about 1e-16 s, so its population stays below the threshold while its activity equals its production rate. Nothing differs after the first cooling step, and every change is inside the run's bound. Use `prune: "reach"` or a lower threshold where end-of-irradiation activity matters.
- The ledger's `rate_pruning` block replaces `removed_heat_W_per_g_bound` with bounds on dropped and kept activity and heat, their sums `activity_Bq_per_g_bound` and `heat_W_per_g_bound`, and `unbounded_components`. Anything that reads the old field, compares ledgers or hashes a normalized result will see the change. See [the ledger](https://github.com/AvilaLabs/ACTINV/blob/master/docs/LEDGER.md).

## 1.5.0

Release 1.5.0 adds FISPACT-II deck import (`actinv import-fispact`), result tables (`actinv report`) and library queries (`actinv inspect`), a step summary and depletion table, photon dose at a distance with a separate bremsstrahlung block, production pathways and routes with closure, bundled regulatory tables (49 CFR 173.435/173.436 and Euratom 2013/59 Annex VII), geometry self-shielding, sampling statistics for robustness studies, wall-loading and power-driven steps, and spontaneous-fission products. The desktop 0.4.0-preview.1 adds the Outputs, Tables, Photons & dose and Pathways pages and deck import. The data catalog stays at 1.1.0. See the [1.5.0 release notes](https://github.com/AvilaLabs/ACTINV/blob/master/docs/RELEASE_NOTES_v1.5.0.md) and the [desktop 0.4.0-preview.1 notes](https://github.com/AvilaLabs/ACTINV/blob/master/docs/releases/desktop-v0.4.0-preview.1.md).

**Results that may change.** Most 1.5 additions are opt-in. These are not, or they correct a result you may already have.

**Defaults that moved**

- A fission after another decay transition (beta-delayed fission, RTYP 2.6) was counted as spontaneous fission of the parent. It is now counted in the new ledger key `unmodelled_decay_branches_to_leakage`, so `spontaneous_fission_branches_to_leakage` falls from 129 to 126 on the shipped data. Inventories do not change. The new key appears only when it is nonzero.
- Trace-mode pathway attribution now includes production fed by the decay of a radioactive bulk nuclide, and `pathway_closure` is 1 when a nuclide in the inventory has no chain at all. Inventories do not change. In SS316 the leading Ru-100 route is Mo-100 double-beta decay; in tungsten, W-186 alpha decay feeds Hf-182 (the closure of the shipped shielding example falls from 0.98 to 5e-16); and the natural-uranium closure falls from 1.0 to 4e-15.
- Trace mode no longer holds radioactive initial material constant. `auto` now selects the coupled solver when any initial nuclide decays by 1e-6 or more over the schedule, and an explicit trace request is refused there. Restart or cooling runs from an activated inventory that did not set `coupled` should be rerun.
- Trace-mode pathway attribution used to ignore the step's flux multiplier and spectrum override, and lost production after a cooling gap. It now uses the solve's own matrices, and `pathway_closure` shows the difference.

**Opt-in features that were corrected**

- Bremsstrahlung used the decay record's average electron energy per decay of the parent as the energy of one electron per decay. It now uses the beta-minus branching and the matching energy per electron. Partial beta emitters such as Cu-64 change (about 2.6 times more energy); pure beta-minus emitters do not.
- The bundled regulatory tables were regenerated, so their SHA-256 values changed. A spec that declares a bundled table's hash must use the new one; `actinv` fills a missing hash itself.
- Geometry self-shielding uses a resolved resonance only inside the range [EL, EH] that its evaluation resolves. TENDL-2025 W-186 is resolved to EH = 4341 eV and loses its factors above that energy; ENDF/B-VIII.1 W-186 loses one group above 10 keV. Whether FISPACT-II clips resonances at the same energy is not verified here, so a W-186 comparison with FISPACT-II may differ for this reason. A `geometry.density_g_cm3` that disagrees with the material density is refused.

The [changelog](https://github.com/AvilaLabs/ACTINV/blob/master/CHANGELOG.md) lists the remaining fixes (schedule refusals, depletion and sampling statistics, importer robustness).

## 1.4.0

Release 1.4.0 adds impurity-budget workflows (`actinv budget` and the Python `budget` helper), the OpenMC R2S adapter, hydrogen/helium isotope production through `options.gas`, transport-tally statistical error propagation through the `flux` uncertainty channel, lean mesh output through dotted `steps.<field>` entries in `cell_result_fields`, and photonuclear (incident-gamma) activation through `build-library --projectile gamma`. Gamma activation libraries are not published through `actinv data fetch`; build one from the TENDL `g` sublibrary. See the [1.4.0 release notes](https://github.com/AvilaLabs/ACTINV/blob/master/docs/RELEASE_NOTES_v1.4.0.md).

**Results that may change.** A trace-mode correction moves results for multi-element materials; see the [1.4.0 release notes](https://github.com/AvilaLabs/ACTINV/blob/master/docs/RELEASE_NOTES_v1.4.0.md).

## Earlier releases

The [1.3.1 release notes](https://github.com/AvilaLabs/ACTINV/blob/master/docs/RELEASE_NOTES_v1.3.1.md) describe that release and its qualification boundaries. The [1.2.1 release notes](https://github.com/AvilaLabs/ACTINV/blob/master/docs/RELEASE_NOTES_v1.2.1.md) list corrections to self-shielding interpolation, composition validation and decay-branch accounting; review them if earlier calculations used those paths. The [data-release notes](https://github.com/AvilaLabs/ACTINV/blob/master/docs/DATA_RELEASE_NOTES_v1.1.0.md) describe data changes.

## Upgrade

```bash
python -m pip install --upgrade actinv
```

For an exact Rust CLI version:

```bash
cargo install --locked --force actinv-cli --version 1.7.2
```

An upgrade never replaces installed data. Keep the old executable, or its version, for results you may need to reproduce.
