# ACTINV v0.5.0 — release notes

*Research-grade. Not validated for licensing, safety or regulatory decisions.*

v0.5 completes the P9–P10 technical milestone: evaluated fission-product feeds and coupled/pulsed histories, followed
by a strict Rust activation-library pipeline for current TENDL-2025 neutron/proton/deuteron/alpha data and EAF-2010.
Tagging and publishing crates or wheels remain external maintainer acts.

## What works in v0.5

- Explicit ground-state/isomer materials under weight-percent, atom-fraction and atoms-per-gram bases.
- Hash-pinned ENDF independent fission yields with interpolation, complete mapped/leakage balance and coupled burn-up.
- Arbitrary piecewise-constant irradiation/pulse histories, with physical exposure and fluence at every boundary.
- `actinv build-library`: strict ENDF-6 parsing, deterministic content-addressed checkpoints and canonical NPZ/index
  output entirely in Rust, with no Python runtime dependency.
- Neutron SLBW/MLBW/Reich–Moore/R-matrix-limited reconstruction, infinite-dilution unresolved averages,
  arbitrary-temperature SIGMA1 broadening and area-preserving analytic ultra-narrow lines.
- TENDL charged-particle MF=6 residual production and end-to-end neutron/proton/deuteron/alpha identity through CLI,
  Python, prepared and mesh execution.

## Validation and complete-build evidence

All seven P10 gates pass. Independent controls compare W-186 R-matrix-limited capture with FENDL/NJOY, Ag-107
unresolved averages with a fresh NJOY2016.79 UNRESR run and high-order quadrature, temperature kernels and 52 Fr-226
analytic lines with direct integration, TENDL-2025 charged products with official residual tables, and identical
TENDL-2017 values with official processed FISPACT rows. The licensed FISPACT executable was not run.

The final Rust fingerprint builds five fresh/cached byte-identical external libraries:

| library | targets | rows | NPZ SHA-256 |
|---|---:|---:|---|
| TENDL-2025 neutron | 2,850 | 167,735 | `ec4c72bf598dc8ad3d533d9cfafdcf493e2d1f949a3e4db6251495659b68cc44` |
| TENDL-2025 proton | 2,850 | 528,057 | `0da7a35b37fd3b305ac2166ec092cdfb78123e76f8647d8808915e2c708d9790` |
| TENDL-2025 deuteron | 2,850 | 548,706 | `8050988981518cd63ac0c2ad76c6756370b154ea9f5a6d6435aa5f132b9d99ae` |
| TENDL-2025 alpha | 2,850 | 489,279 | `ead1141bfe07ec1a02055af014f8db0a49effe2fd60c29d181a505f7c6d10915` |
| EAF-2010 neutron | 816 | 115,702 | `5de78c8efec0501417297175378490beb6d21205308f632948db25171cb9b1a2` |

There are zero target errors, silent unsupported fallbacks or convergence flags. The EAF control independently
re-collapses all 816 targets. P5 remains P5-PASS and P6–P9 retain their checker-derived conditional verdicts.

P10 closes **P10-CONDITIONAL** because its append-only protocol record includes repair amendments. They document
fail-closed handling found by the complete corpus and corrections to independent-control premises; the final source,
data identities, unchanged acceptance tolerances and rerun evidence are pinned in `sessions_P10.md`.

## Known limitations

- Finite-dilution unresolved self-shielding, Bondarenko factors and probability tables are not implemented. P10's
  unresolved result is explicitly infinite dilution.
- Triton, helion and gamma activation remain out of scope; supported incident particles are neutron, proton,
  deuteron and alpha.
- Covariance propagation, clearance/waste indices, ICRP ingestion/inhalation coefficients, parser fuzzing at the P12
  target and FNG/ITER shutdown-dose validation remain P11/P12 work.
- Photon transport, neutron transport, criticality, depletion feedback between mesh cells and spatial source inference
  remain out of scope. Contact gamma dose is still a screening proxy.
- Products absent from both configured decay libraries remain explicit leakage, with their identities and rates in the
  ledger; the software does not invent decay data.

## Reproduction

Run the exact Rust commands in `AGENTS.md`, the CI subset in `.github/workflows/ci.yml`, and
`python controls/check_p10.py`. Complete external-library provenance and profiles are in
`results/g7_p10_builds.json`; nuclear data and generated bulk libraries are deliberately not in Git.

MIT OR Apache-2.0. Contributions remain under the Developer Certificate of Origin.
