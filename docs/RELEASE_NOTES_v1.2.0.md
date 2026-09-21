# ACTINV v1.2.0 — release notes

ACTINV 1.2.0 is a minor release: it adds time-varying irradiation spectra, multi-spectrum uncertainty
propagation, a decay-photon source export for downstream transport, and a set of isomer-resolution and
decay-data improvements that move the identical-data benchmark score. The solver, schemas and data
catalog remain backward-compatible with 1.1.x; the embedded catalog stays `data-v1.1.0`.

Upgrade the Python package with:

```bash
python -m pip install --upgrade actinv
```

Rust CLI users can install this exact version with:

```bash
cargo install --locked --force actinv-cli --version 1.2.0
```

## What changes

- **Per-step spectra.** Each schedule step may declare its own spectrum; the step's flux multiplier
  scales that spectrum's total. Runs with step spectra use the groupwise library path and are
  validated against the library's group count. This enables activation under time-varying fields
  (pulsed operation, shifting blankets, schedule-driven spectral drift) without manual per-step runs.
- **Multi-spectrum uncertainty.** MF=33 covariance propagation collapses jointly over (spectrum, row)
  parameters, so a run with per-step spectra still reports propagated cross-section, fission-yield and
  decay-constant uncertainty. Identical overrides reduce to the bitwise-identical single-spectrum
  path.
- **`activity.total` uncertainty response.** Total activity is propagated through the full covariance
  as a first-class response rather than a per-nuclide RSS combination.
- **`actinv export-openmc-mesh`.** Exports decay-photon sources for downstream photon transport: one
  IndependentSource per mesh cell, box-sampled inside recorded bounds, with the cell's discrete
  spectrum and absolute strengths.
- **Isomer resolution.** Emitted-state products resolve to decay-library isomer levels through the
  MF=1 state table with rank-compressed LFS mapping; a LIS label fallback plus a `--decay-fallback`
  second sublibrary recover channels that previously decayed to artificial homes. On the identical-data
  FNS corpus this moves the within-30% experiment count from 69 (parity with the frozen FISPACT-4
  reference) to 71, with median |log C/E| 0.103 vs 0.105.
- **Robustness and numerics.** Perturbation channels draw mean-preserving lognormal factors from an
  eigen-clipped PSD covariance; CRAM applies a selective refinement gate and exempts subnormal-scale
  rows from convergence forcing. The zero-spectrum collapse fallback fix from the unreleased queue is
  included.
- **Data-side disclosure.** A benchmark-corpus defect screen identified a spurious high-energy capture
  bump in the TENDL-2017 In-115 evaluation (capture exceeding the nonelastic bound at >10 MeV, fixed in
  TENDL-2025). A ledgered repair exists for the research corpus and is described in `ledger.md` entry
  53; it is not part of the shipped `data-v1.1.0` catalog.

## Qualification boundary

Unchanged — `docs/DATA_LIMITATIONS.md` and `docs/QUALIFICATION.md` apply as before. New capabilities
extend the envelope (time-varying spectra, photon-source handoff, multi-spectrum UQ); they do not
alter the validation status of the underlying solver or data.
