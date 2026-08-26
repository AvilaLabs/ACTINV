# Changelog

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
