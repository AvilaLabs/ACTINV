# ACTINV v1.7.2 — release notes

ACTINV 1.7.2 makes `actinv data fetch` work again for first-time installs and ships the OpenMC R2S adapter inside
the Python package. It also:
- adds an uncertainty band for OpenMC shutdown-dose tallies;
- adds mixture self-shielding;
- makes rate pruning faster;
- fixes the potential-scattering estimate of composition-dilution self-shielding.

The bundled TENDL-2025 data, the data catalog (1.2.0) and default results are unchanged. Everything else in the
[1.7.1 release notes](RELEASE_NOTES_v1.7.1.md) and [1.7.0 release notes](RELEASE_NOTES_v1.7.0.md) applies, including
their known limitations.

Upgrade the Python package with:

```bash
python -m pip install --upgrade actinv
```

Rust CLI users can install this exact version with:

```bash
cargo install --locked --force actinv-cli --version 1.7.2
```

## Data download

The two decay-data archives (ENDF/B-VIII.0 and JEFF-3.3) come from the IAEA Nuclear Data Section. Its site now
answers automated downloads with HTTP 403, so `actinv data fetch` 1.7.1 and earlier stop on them for anyone
installing the data for the first time.

When the IAEA URL fails, 1.7.2 takes ACTINV's byte-identical copy from the
[decay-sources-v1 release](https://github.com/AvilaLabs/ACTINV/releases/tag/decay-sources-v1). It holds the copy to
the catalog's size and SHA-256, so it installs exactly the files the catalog names. With an older release, install
the two files by hand as described in the
[install guide](guide/install.md#if-fetch-stops-with-http-status-403). Existing data folders need nothing.

## OpenMC

`from actinv.openmc import ActinvR2SManager` now comes with `pip install actinv`. It replaces copying the contrib
script, which is kept as a shim. The adapter needs OpenMC 0.15.3 or newer. It is tested on 0.15.3 and 0.16.0, and
weekly on OpenMC's development branch. Importing `actinv` alone never imports OpenMC.

- **Defaults:** the library, decay data and solver come from the installed data and package unless you pass them.
- **Step 1** tallies only the flux on the library's groups and needs no depletion chain.
- **Reloading:** `load_results(path)` reopens a finished run without solving again.
- **MPI:** under MPI, `actinv mesh` runs once, on rank 0, and a failure there stops every rank.
- **Uncertainty band:** `uncertainty={}` gives every photon tally a first-order band in
  `results["dose_uncertainty"]`. The band covers activation cross sections (MF=33), decay constants and flux-tally
  noise, plus the photon tally's own statistics. It is a lower bound on the data uncertainty: nuclides outside the
  bands and channels without covariance data add nothing. The band has not yet been checked against sampling.

See [Use ACTINV with OpenMC](guide/openmc.md#inside-openmcs-r2s-workflow).

## Also in this release

- **Mixture self-shielding** (`self_shielding.mixture`, opt-in): computes resonance self-shielding factors at run
  time from the material's own pointwise cross sections, so overlapping resonances of different nuclides shield each
  other. It has not yet been qualified against measurements.
- **Faster rate pruning** on large reaction networks. Results are unchanged. `ledger.rate_pruning` may list more
  dropped nuclides, with looser but still valid bounds on nuclides that are dropped anyway.
- **Python:** the extension module is now `actinv._actinv`, re-exported by `actinv`. `import actinv` and the
  `actinv` command are unchanged.
- **Reproducible tables:** `build-shielding` no longer records the input directory, so identical inputs give a
  byte-identical table.
- **Source package:** the Python source distribution lists only the workspace crates it ships, so building it from
  source no longer fails in tools that read the top-level `Cargo.toml`.

## Results to re-check

- **Self-shielding with `dilution: "composition"`.**
  - **The defect:** the analytic potential-scattering estimate, used for nuclides the table does not cover, was 100
    times too small. The ENDF channel radius was read as femtometres.
  - **Who is affected:** materials whose main scatterers are light nuclides missing from the table, such as concrete
    and water. They were over-shielded.
  - **Affected releases:** 1.1.0 to 1.7.1.
  - **What to do:** rerun calculations that used this option.
  - **Not affected:** default runs, which do not self-shield.
