# Changelog

## Unreleased

**Added**

- ENDF-6 MF=8/MT=457 discrete and continuous decay-photon parsing, including ENDF interpolation metadata.
- Per-step evaluated line and FISPACT-24/custom multigroup photon sources with explicit `E_EM` energy normalization,
  missing-spectrum bounds and per-gram/total strengths.
- NIST-response specific gamma constants and a clearly labelled FISPACT semi-infinite-slab contact air-dose proxy.
- OpenMC `IndependentSource` and MCNP `SDEF` photon-source exports.
- Reproducible external NIST photon-response builder and independent P7 G1–G6 controls.

**Fixed**

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
