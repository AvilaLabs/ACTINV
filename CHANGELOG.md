# Changelog

## Unreleased

**Added**

- Explicit ground-state/isomer material keys across weight-percent, atom-fraction and literal atoms-per-gram bases,
  with evaluated AWR provenance and ambiguity checks.
- Strict, hash-pinned ENDF-6 MF=8/MT=454/459 fission-yield parsing; independent-yield interpolation/selection; and
  yield-expanded MT=18 matrix feeds with complete mapped/leakage balance.
- Per-boundary elapsed time, multiplier-weighted exposure and physical fluence for arbitrary piecewise-constant pulse
  histories, through ordinary and mesh runs.
- Independent P9 controls against dense exponentials, OpenMC CRAM48, ALARA 2.9.2 and the CoNDERC U-235 Dickens pulse
  and Yarnell 20,000 s decay-heat sets.

**Fixed**

- Automatic trace/coupled selection now uses each initial isotope's reaction-loss optical depth over the complete
  multiplier-weighted schedule; non-unit pulse multipliers and cooling gaps can no longer be miscounted.
- Scientific-notation durations such as `1e-8 s` no longer treat the exponent marker as a unit suffix.

## v0.2.0 — 2026-08-26

**Added**

- Hashed `actinv-flux-1` NDJSON interchange and strict importers for OpenMC statepoint-format-18 mesh flux, traditional
  MCNP neutron MESHTAL, energy-binned F4:N MCTAL, and standard FISPACT-II `fluxes` files.
- Conservative equal-flux-per-lethargy rebinning with exact-grid identity and explicit underflow/overflow closure.
- `actinv-mesh-spec-1` and a bounded, ordered Rayon mesh runner that prepares immutable nuclear data once and invokes
  the ordinary independently pruned solver for every streamed cell.
- Independent P8 controls for format values and rejection paths, provenance, mesh identity/thread determinism,
  bounded HDF5 reads and measured/extrapolated sizing through one million cells.
- ENDF-6 MF=8/MT=457 discrete and continuous decay-photon parsing, including ENDF interpolation metadata.
- Per-step evaluated line and FISPACT-24/custom multigroup photon sources with explicit `E_EM` energy normalization,
  missing-spectrum bounds and per-gram/total strengths.
- NIST-response specific gamma constants and a clearly labelled FISPACT semi-infinite-slab contact air-dose proxy.
- OpenMC `IndependentSource` and MCNP `SDEF` photon-source exports.
- Reproducible external NIST photon-response builder and independent P7 G1–G6 controls.

**Fixed**

- Canonical flux streams now reject duplicate cell IDs as well as malformed ordering/counts, standard FISPACT input
  rejects blank identifying titles, and mesh-output path aliases cannot replace their canonical input. Import and mesh
  outputs publish atomically only after a validated footer.
- Truncated fixed-width activation-library records now fail instead of silently dropping the incomplete tail.
- Prepared execution removes repeated activation-library/decay-chain parsing from mesh cells without changing any
  pre-v0.2 ordinary result field.
- Certificates now compute and verify hashes for the activation library, its index, primary/fallback decay files and
  photon response; declarations are no longer repeated without verification.
- `atom_fraction` and `atoms_per_g` material bases now follow their documented meanings, including response mass
  fractions; non-finite specification values and mismatched custom/library boundaries fail validation.
- Rate-pruning bounds and relevant activation-library convergence/unsupported-feature guards now propagate to each
  run ledger as promised by the v0.1 documentation.
- Radioactive constant-bulk components now contribute to split alpha/beta/gamma heat and photon activity in trace mode.
- PyO3 updated from 0.22 to 0.29 so the binding builds normally with Python 3.14.

## v0.1.0 — 2026-08-27

First release. ACTINV computes nuclide inventories, activity and decay heat from any neutron flux spectrum.

**Added**
- Own ENDF-6 data pipeline: parsing, resolved-resonance reconstruction (SLBW, MLBW, Reich–Moore), SIGMA1 Doppler
  broadening, 709-group collapse — verified against IAEA's NJOY-processed cross sections to 2.3e-3.
- Activation libraries built by that pipeline from EAF-2010 (816 targets) and TENDL-2023 (2,847 targets).
- Decay data from ENDF/B-VIII.0 with a JEFF-3.3 fallback; the source of each nuclide's data is recorded.
- Rust solver: CRAM-16 with an in-house sparse complex LU; trace and coupled formulations selected from the recorded
  burn-up fraction; reachable-set and rate-significance pruning with bounds on what was removed.
- `actinv-spec-1` problem specification; `actinv run`, a Python module, and the validation harness as three entry
  points to one binary.
- Pathway analysis: ranked production chains per nuclide, exact by linearity.
- A missing-data ledger and an input-hashing certificate on every run, including the method's numerical floor.
- Validation against the 132-experiment FNS decay-heat benchmark, re-derivable by the shipped checkers.

**Known limitations** — see `docs/RELEASE_NOTES_v0.1.md`; each is reported by the code rather than hidden.

**Not included** — neutron transport, criticality, fission yields, decay-photon transport, covariance uncertainty.
