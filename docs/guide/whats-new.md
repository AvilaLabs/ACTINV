# What's new in 1.5

ACTINV 1.5.0 is mostly about work that used to leave ACTINV for another tool: reading a FISPACT-II deck, printing FISPACT-style tables, estimating dose at a distance, tracing production routes, and checking an inventory against a transport or clearance table. This page is a tour by task. [Versions and releases](releases.md) has the version table, and the [1.5.0 release notes](https://github.com/AvilaLabs/ACTINV/blob/master/docs/RELEASE_NOTES_v1.5.0.md) have the full list.

Nearly everything below is opt-in. A problem that does not name a new section computes what it computed before; the changes to default results are collected in [Results that may change](#results-that-may-change). The nuclear-data catalog stays at 1.1.0. The desktop is 0.4.0-preview.1, and the [desktop release page](https://github.com/AvilaLabs/ACTINV/blob/master/docs/releases/desktop-v0.4.0-preview.1.md) describes it.

## Start from a FISPACT-II deck

`actinv import-fispact` converts a FISPACT-II input deck into an ACTINV problem and writes a report that marks every keyword as mapped, approximated, noted or unsupported:

```text
actinv import-fispact DECK.i OUT.json [--fluxes FLUXES] [--files FILES] [--library LIB.npz] [--decay DECAY] [--covariance COV.npz] [--strict]
```

All 132 public FNS decks import. `POWER`, `WALL`, `PARTITION`, `OVER`, `GRPCONVERT`, `DOSE 2`, `GROUP`, `BREMSSTRAHLUNG`, `PATH`/`ROUTES` and `ATWO` are mapped, and the importer accepts three-letter keyword abbreviations such as `TOL`. Several keywords still need you to add a section by hand, and a few have no equivalent; [Move a FISPACT-II deck to ACTINV](fispact-migration.md) has the full keyword map, including the new [Coming from FISPACT-II](fispact-migration.md#coming-from-fispact-ii) table.

The deck gives ACTINV the problem, not the data. ACTINV uses its own activation and decay libraries, so results differ from a FISPACT-II run by the difference between the data sets as well as by the solvers.

The same importer is in the desktop and the browser workbench as **Import FISPACT-II deck** (Ctrl/Cmd+I in the desktop).

## Reproduce FISPACT-II features

These are the FISPACT-II capabilities added in 1.5. Each is an optional section of the [problem specification](specification.md).

- **Spectrum conversion.** `spectrum.rebin` converts a spectrum between group structures inside the run, and every FISPACT-II neutron structure from `fispact-66` to `fispact-1102` (to 1 GeV) is available. See [Spectrum conversion](specification.md#spectrum-conversion-spectrumrebin) and [Group structures](specification.md#group-structures).
- **Schedule controls.** Steps can be driven by a wall loading, a power density or a specific power (with a groupwise kerma table), and a step can apply an instantaneous elemental partition. Power is renormalized on sub-intervals within a step; `max_substeps: 1` gives FISPACT-II's single normalization. See [Kerma heating and power-driven steps](results.md#kerma-heating-and-power-driven-steps).
- **Overrides and decay-only runs.** Per-nuclide, per-reaction cross-section, half-life and uncertainty `overrides`, and cooling runs from decay data alone. See [Named data overrides](specification.md#named-data-overrides-overrides) and [Decay-only runs](specification.md#decay-only-runs).
- **Fission.** Spontaneous-fission products, the cumulative fission count, burn-up and the spontaneous-fission neutron source. See [Fission count, burn-up and spontaneous-fission neutrons](results.md#fission-count-burn-up-and-spontaneous-fission-neutrons).
- **Projectiles and libraries.** Triton and helion projectiles, and switching the activation library between steps. Neither triton nor helion has a library in the standard catalog, and neither has been tried on real data, so treat them as untested beyond synthetic fixtures.
- **Query data without a run.** `actinv inspect` prints decay data, group and spectrum-collapsed cross sections, fission yields and group boundaries:

  ```text
  actinv inspect decay DECAY_PATH [NUCLIDE...]
  actinv inspect xs LIBRARY.npz [--target NUCLIDE] [--mt MT] [--spectrum SPEC.json]
  ```

  See [Query data files](cli.md#query-data-files).

## Get new outputs

### Tables and a step summary

The optional `summary` section adds the full inventory with grams, activity, alpha/beta/gamma heat and half-life per nuclide, step totals (including activity and heat without tritium), an element table, dominant lists with cumulative percentages, and `MIND`/`NOSTABLE`-style filters. A `summary.depletion` table adds, for each initial nuclide, the depleted fraction and the removal rate split between decay and each reaction. See [Step summary](results.md#step-summary) and [Depletion table](results.md#depletion-table).

`actinv report` renders an existing result as FISPACT-style tables (`TAB1` to `TAB4`, step totals, dominant and element tables, a plot-ready time series) in csv, tsv, markdown or text. It runs no solver:

```text
actinv report RESULT.json [--tables LIST] [--format csv|tsv|markdown|text]
              [--out DIR] [--step N|all] [--nuclides LIST]
```

See [Render result tables](cli.md#render-result-tables).

### Photons and dose

- The 22-group Steiner photon structure alongside the 24-group structure. See [Steiner 22-group structure](specification.md#steiner-22-group-structure).
- `photon.point_source` gives the gamma air-kerma rate at a distance from the sample (at least 0.3 m), like FISPACT-II's `DOSE 2`. See [Point-source dose](specification.md#point-source-dose-photonpoint_source).
- `photon.bremsstrahlung` reports the bremsstrahlung photons and their dose as a separate block; it is not added to the decay-photon dose. See [Bremsstrahlung](specification.md#bremsstrahlung-photonbremsstrahlung).

The dose values are `null` when the photon response has no air attenuation curve or when nuclides have decay-record gamma power with no photon spectrum, rather than a number that looks complete. The shipped response has no air attenuation curve, so a point-source dose needs a rebuilt response or `air_attenuation: false`; the specification explains both. Approximate gamma spectra for such nuclides (`SPEK`) are not estimated; the power that has no spectrum is reported instead.

### Production pathways and routes

The optional `pathways` section traces multi-link routes from a source nuclide to a target, including loops, and integrates each route exactly through every schedule step. Each target reports a closure: the total production, the sum over the listed routes, and what the routes leave unexplained. See [Read the pathway routes](results.md#read-the-pathway-routes) and [Pathways and routes analysis](specification.md#pathways-and-routes-analysis).

### Regulatory tables

A problem can now compare an inventory with bundled public tables: the A1 and A2 values of 49 CFR 173.435, the exempt values of 49 CFR 173.436, and the clearance and exemption levels of Euratom Directive 2013/59 Annex VII. They are opt-in (`radiological.table.path` set to `bundled:NAME`). ACTINV chooses no jurisdiction for you, and whether a table applies to your material and use is your decision.

`actinv report` has a table for them. It is built only when you name it:

```text
actinv report RESULT.json --tables regulatory
```

It lists, per step, each clearance, waste or A2 response with its value and the share of the activity the table covers. Some rows have no finite limit, and progeny can be included in a parent's value; the tables record both, so `require_complete` can be satisfied by an activation inventory. See [Bundled regulatory tables](specification.md#bundled-regulatory-tables).

ICRP 119 dose coefficients are not bundled because of their copyright. If you hold the publication, `scripts/regulatory/fetch_icrp119.py` builds a table for your own use; see [Building the ICRP 119 dose-coefficient table locally](specification.md#building-the-icrp-119-dose-coefficient-table-locally).

## Treat self-shielding by geometry

`self_shielding.geometry` applies a resolved-resonance self-shielding correction for a foil, wire, sphere or cylinder using the universal sigmoid curve, as FISPACT-II's `SSFGEOMETRY` does. It reads ENDF resonance files you supply, including R-matrix-limited (LRF=7) evaluations such as ENDF/B-VIII and FENDL-3.2c. `self_shielding.dilution_overrides` sets the background dilution for chosen groups when you use a Bondarenko table. See [Geometry self-shielding](specification.md#geometry-self-shielding-resolved-resonances) and [Dilution overrides](specification.md#dilution-overrides).

A resonance is used only inside the energy range its evaluation resolves; see [Results that may change](#results-that-may-change).

## Understand the uncertainty

- `study.robustness.statistics` adds, for each response, the sample mean, standard deviation, skewness, exact percentiles and a histogram, the input-output Pearson and Spearman correlations by sampled parameter, a response-response correlation matrix, and bootstrap errors next to the first-order standard deviation. It is the counterpart to FISPACT-II's `SENSITIVITY` and `MCSAMPLE` output, and it is absent unless you ask for it. See [Monte Carlo sampling statistics](specification.md#monte-carlo-sampling-statistics).
- A single sample has no standard deviation, correlations and bootstrap errors need at least three samples, and `first_order` reads `not_comparable` when channels beyond `cross_section_mf33` are sampled.
- The removal-rate uncertainty in the depletion table uses the same MF=33 covariance as the rest of the run.

## Use the 0.4 interface

Desktop 0.4.0-preview.1 and the browser workbench now have thirteen pages in three groups, with the same look as before. The browser still never runs the solver.

- **Outputs** (under setup) is one place to enable the step summary and depletion, decay photons and dose, production pathways and routes, regulatory tables, geometry self-shielding, uncertainty with each of its channels (including flux tally statistics) and gas production. A status line on each card gives the solver's own message when two choices conflict.
- **Irradiation & cooling** has a **Drive** for each step (flux multiplier, wall loading, power in W/cm3 or W/g), and **Material** has an optional density.
- **Charts**, **Tables**, **Photons & dose**, **Pathways** and **Ledger & certificate** share one result bar and one chosen step. **Tables** exports CSV that is byte-identical to `actinv report`.
- **Import FISPACT-II deck** converts a deck with the importer's per-keyword report. Unlike the CLI, the desktop converts a deck that irradiates but has no fluxes file by inserting a flagged placeholder spectrum; replace it before running. The CLI refuses such a deck.

See [Use the desktop](desktop.md) and [Use the browser workbench](browser.md).

## Results that may change

Most 1.5 changes are opt-in. These are not, or they correct a result you may already have.

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
