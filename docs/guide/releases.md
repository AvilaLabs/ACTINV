# Versions and releases

Software, nuclear data, and desktop packages have separate release histories. Record all the versions relevant to your calculation.

| Component | Version covered here | Release |
| --- | --- | --- |
| CLI, Python, and Rust workspace | 1.6.1 | [v1.6.1](https://github.com/AvilaLabs/ACTINV/releases/tag/v1.6.1) |
| Embedded nuclear-data catalog | 1.1.0 | [data-v1.1.0](https://github.com/AvilaLabs/ACTINV/releases/tag/data-v1.1.0) |
| Desktop preview | 0.5.0-preview.1, using solver 1.6.1 | [desktop preview](https://github.com/AvilaLabs/ACTINV/releases/tag/desktop-v0.5.0-preview.1) |

Use `actinv --version` for the executable. `actinv data manifest` prints its embedded catalog. Source-built desktop candidates may use a newer solver than the published preview; inspect their package/build record.

## New in 1.6.1

Release 1.6.1 fixes one defect of 1.6.0. A trace-mode run with pathway output, which the default outputs
include, could stop with an `index out of bounds` error. This happened when a reaction of a bulk nuclide led to a
product with no decay data. Runs that completed in 1.6.0 give the same results in 1.6.1.

## New in 1.6.0

Release 1.6.0 adds waste classification under nuclear-data uncertainty (`actinv waste uncertainty`), impurity budgets
under uncertainty (`actinv waste budget uncertainty`), classes for packages of discrete items with encapsulation
(`actinv waste package`), the waste commands in the Python API (`actinv.waste`, `actinv.twin`) and a Waste class page
in the desktop. Rate pruning now drops a nuclide only when a proven bound on its population is below the threshold,
and the ledger's `rate_pruning` block reports bounds on the change in total activity and heat; see
[What's new in 1.6](whats-new-1.6.md).

## New in 1.5.0

Release 1.5.0 adds FISPACT-II deck import (`actinv import-fispact`), result tables (`actinv report`) and library queries (`actinv inspect`), a step summary and depletion table, photon dose at a distance with a separate bremsstrahlung block, production pathways and routes with closure, bundled regulatory tables (49 CFR 173.435/173.436 and Euratom 2013/59 Annex VII), geometry self-shielding, sampling statistics for robustness studies, wall-loading and power-driven steps, and spontaneous-fission products. The desktop 0.4.0-preview.1 adds the Outputs, Tables, Photons & dose and Pathways pages and deck import. Some defaults changed; see [Changes that may affect earlier results](#changes-that-may-affect-earlier-results). The data catalog stays at 1.1.0. [What's new in 1.5](whats-new.md) is a tour of the additions; see also the [1.5.0 release notes](https://github.com/AvilaLabs/ACTINV/blob/master/docs/RELEASE_NOTES_v1.5.0.md) and the [desktop 0.4.0-preview.1 notes](https://github.com/AvilaLabs/ACTINV/blob/master/docs/releases/desktop-v0.4.0-preview.1.md).

## New in 1.4.0

Release 1.4.0 adds impurity-budget workflows (`actinv budget` and the Python `budget` helper), the OpenMC R2S adapter, hydrogen/helium isotope production through `options.gas`, transport-tally statistical error propagation through the `flux` uncertainty channel, lean mesh output through dotted `steps.<field>` entries in `cell_result_fields`, and photonuclear (incident-gamma) activation through `build-library --projectile gamma`. Gamma activation libraries are not published through `actinv data fetch`; build one from the TENDL `g` sublibrary. See the [1.4.0 release notes](https://github.com/AvilaLabs/ACTINV/blob/master/docs/RELEASE_NOTES_v1.4.0.md).

Source builds between releases can carry a workspace version that has not moved yet, so record the commit as well as `actinv --version`.

## Upgrade software

```bash
python -m pip install --upgrade actinv
```

For an exact Rust CLI version:

```bash
cargo install --locked --force actinv-cli --version 1.6.1
```

A software upgrade does not silently replace installed data. Revisit optional settings and coverage when a release changes their behavior, and retain the executable or source version for older results.

## Changes that may affect earlier results

The [1.6.0 release notes](https://github.com/AvilaLabs/ACTINV/blob/master/docs/RELEASE_NOTES_v1.6.0.md) describe the one default-result change: the new rate-pruning rule keeps or drops a few different noise-level nuclides, and the ledger's `rate_pruning` fields are renamed and extended. [Results that may change](whats-new-1.6.md#results-that-may-change) gives the measured effect.

The [1.5.0 release notes](https://github.com/AvilaLabs/ACTINV/blob/master/docs/RELEASE_NOTES_v1.5.0.md) list the default-result changes: trace mode with radioactive initial material (restart or cooling runs from an activated inventory should be rerun), trace-mode pathway attribution, the spontaneous-fission branch count, and corrections to opt-in photon, regulatory-table and self-shielding outputs. [Results that may change](whats-new.md#results-that-may-change) summarizes them.

The [1.2.1 release notes](https://github.com/AvilaLabs/ACTINV/blob/master/docs/RELEASE_NOTES_v1.2.1.md) identify self-shielding interpolation, composition validation, and decay-branch accounting corrections. Review them if your earlier calculations used those paths.

The [1.3.1 release notes](https://github.com/AvilaLabs/ACTINV/blob/master/docs/RELEASE_NOTES_v1.3.1.md) describe that release's capabilities and their qualification boundaries. The [1.4.0 release notes](https://github.com/AvilaLabs/ACTINV/blob/master/docs/RELEASE_NOTES_v1.4.0.md) identify a trace-mode correction that moves results for multi-element materials; review them if your earlier calculations used trace mode. The [changelog](https://github.com/AvilaLabs/ACTINV/blob/master/CHANGELOG.md) retains the full software history; [data-release notes](https://github.com/AvilaLabs/ACTINV/blob/master/docs/DATA_RELEASE_NOTES_v1.1.0.md) describe data changes.

Publishing a new executable does not repair or supersede an evaluation's recorded failure. See [Known data limitations](data-limits.md) before assuming a newer software version implies better nuclear data.
