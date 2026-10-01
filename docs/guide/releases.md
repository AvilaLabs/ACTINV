# Versions and releases

Software, nuclear data, and desktop packages have separate release histories. Record all the versions relevant to your calculation.

| Component | Version covered here | Release |
| --- | --- | --- |
| CLI, Python, and Rust workspace | 1.3.1 | [v1.3.1](https://github.com/AvilaLabs/ACTINV/releases/tag/v1.3.1) |
| Embedded nuclear-data catalog | 1.1.0 | [data-v1.1.0](https://github.com/AvilaLabs/ACTINV/releases/tag/data-v1.1.0) |
| Published desktop preview | 0.1.0-preview.1, using solver 1.0.1 | [desktop preview](https://github.com/AvilaLabs/ACTINV/releases/tag/desktop-v0.1.0-preview.1) |

Use `actinv --version` for the executable. `actinv data manifest` prints its embedded catalog. Source-built desktop candidates may use a newer solver than the published preview; inspect their package/build record.

## Current master

The source branch also contains unreleased impurity-budget workflows, the Python `budget` helper, hydrogen/helium isotope production through `options.gas`, transport-tally statistical error propagation through the `flux` uncertainty channel, and lean mesh output through dotted `steps.<field>` entries in `cell_result_fields`. These additions are described in the [Unreleased changelog](https://github.com/AvilaLabs/ACTINV/blob/master/CHANGELOG.md#unreleased); they are not promised by the published 1.3.1 packages or older desktop preview.

For these features, use a source build at a recorded commit and the current field reference. The workspace version can still read 1.3.1 before the next release, so record the commit as well as `actinv --version`.

## Upgrade software

```bash
python -m pip install --upgrade actinv
```

For an exact Rust CLI version:

```bash
cargo install --locked --force actinv-cli --version 1.3.1
```

A software upgrade does not silently replace installed data. Revisit optional settings and coverage when a release changes their behavior, and retain the executable or source version for older results.

## Changes that may affect earlier results

The [1.2.1 release notes](https://github.com/AvilaLabs/ACTINV/blob/master/docs/RELEASE_NOTES_v1.2.1.md) identify self-shielding interpolation, composition validation, and decay-branch accounting corrections. Review them if your earlier calculations used those paths.

The [1.3.1 release notes](https://github.com/AvilaLabs/ACTINV/blob/master/docs/RELEASE_NOTES_v1.3.1.md) describe later capabilities and their qualification boundaries. The [changelog](https://github.com/AvilaLabs/ACTINV/blob/master/CHANGELOG.md) retains the full software history; [data-release notes](https://github.com/AvilaLabs/ACTINV/blob/master/docs/DATA_RELEASE_NOTES_v1.1.0.md) describe data changes.

Publishing a new executable does not repair or supersede an evaluation's recorded failure. See [Known data limitations](data-limits.md) before assuming a newer software version implies better nuclear data.
