# Versions and releases

Software, nuclear data, and desktop packages have separate release histories. Record all the versions relevant to your calculation.

| Component | Version covered here | Release |
| --- | --- | --- |
| CLI, Python, and Rust workspace | 1.5.0 | [v1.5.0](https://github.com/AvilaLabs/ACTINV/releases/tag/v1.5.0) |
| Embedded nuclear-data catalog | 1.1.0 | [data-v1.1.0](https://github.com/AvilaLabs/ACTINV/releases/tag/data-v1.1.0) |
| Desktop preview | 0.4.0-preview.1, using solver 1.5.0 | [desktop preview](https://github.com/AvilaLabs/ACTINV/releases/tag/desktop-v0.4.0-preview.1) |

Use `actinv --version` for the executable. `actinv data manifest` prints its embedded catalog. Source-built desktop candidates may use a newer solver than the published preview; inspect their package/build record.

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
cargo install --locked --force actinv-cli --version 1.5.0
```

A software upgrade does not silently replace installed data. Revisit optional settings and coverage when a release changes their behavior, and retain the executable or source version for older results.

## Changes that may affect earlier results

The [1.5.0 release notes](https://github.com/AvilaLabs/ACTINV/blob/master/docs/RELEASE_NOTES_v1.5.0.md) list the default-result changes: trace mode with radioactive initial material (restart or cooling runs from an activated inventory should be rerun), trace-mode pathway attribution, the spontaneous-fission branch count, and corrections to opt-in photon, regulatory-table and self-shielding outputs. [Results that may change](whats-new.md#results-that-may-change) summarizes them.

The [1.2.1 release notes](https://github.com/AvilaLabs/ACTINV/blob/master/docs/RELEASE_NOTES_v1.2.1.md) identify self-shielding interpolation, composition validation, and decay-branch accounting corrections. Review them if your earlier calculations used those paths.

The [1.3.1 release notes](https://github.com/AvilaLabs/ACTINV/blob/master/docs/RELEASE_NOTES_v1.3.1.md) describe that release's capabilities and their qualification boundaries. The [1.4.0 release notes](https://github.com/AvilaLabs/ACTINV/blob/master/docs/RELEASE_NOTES_v1.4.0.md) identify a trace-mode correction that moves results for multi-element materials; review them if your earlier calculations used trace mode. The [changelog](https://github.com/AvilaLabs/ACTINV/blob/master/CHANGELOG.md) retains the full software history; [data-release notes](https://github.com/AvilaLabs/ACTINV/blob/master/docs/DATA_RELEASE_NOTES_v1.1.0.md) describe data changes.

Publishing a new executable does not repair or supersede an evaluation's recorded failure. See [Known data limitations](data-limits.md) before assuming a newer software version implies better nuclear data.
