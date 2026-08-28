# ACTINV data bundle v1.0.0 — attribution and provenance

This notice covers the nuclear-data files installed by `actinv data fetch`. It does not change ACTINV's qualification
boundary: users remain responsible for choosing data and models appropriate to their application.

## TENDL-2025 activation and covariance data

The activation libraries and neutron covariance sidecar are transformations of TENDL-2025 evaluated nuclear data.
Every TENDL-2025 evaluation used to produce these artifacts declares `CC-BY-4.0` in its ENDF-6 identification record.

- Creators named by TENDL-2025: A.J. Koning, D. Rochman, V. Raffuzzi, and J.-C. Sublet.
- Source: <https://tendl.web.psi.ch/tendl_2025/tendl2025.html>
- Licence: Creative Commons Attribution 4.0 International,
  <https://creativecommons.org/licenses/by/4.0/>
- Recommended reference: A.J. Koning, D. Rochman, J.-C. Sublet, N. Dzysiuk, M. Fleming, and S. van der Marck,
  “TENDL: Complete Nuclear Data Library for Innovative Nuclear Science and Technology,” Nuclear Data Sheets 155
  (2019) 1–55, <https://doi.org/10.1016/j.nds.2019.01.002>.

ACTINV changed the source form by parsing ENDF-6 reaction/product records, reconstructing supported resolved and
unresolved resonances, applying the recorded temperature treatment, and collapsing cross sections or MF=33 covariance
onto the named FISPACT group structure. The activation artifacts use the P10 builder fingerprint
`7a50ba3441b30b829ae857ed192b2e52554d6c149460475f7735599f29548a43`; the covariance sidecar uses P11 fingerprint
`c9825cafd8945f32efda4a00ea081af811b887562ebf07ae33ac05ea1d6846d1`.

The TENDL-2025 neutron working corpus differs from the official archive at two nonfinite Pb-208 fields at 1 MeV. In
MF=3/MT=1 and MF=3/MT=3, the literal `NaN` field was replaced with the immediately preceding finite left-branch value
`4.925328-7`. This fail-closed repair and both line identities are recorded in ACTINV's P10 protocol and evidence.

Exact official archive, source-manifest, builder, output, and index identities are recorded in the bundled catalog and
in ACTINV's `docs/DATA.md`, `docs/P10_G7_EXECUTION.md`, and `docs/P11_G6_EXECUTION.md`.

## Decay data downloaded from official hosts

ACTINV does not rehost the decay evaluations. The fetch command downloads the following official archives and verifies
both the archive and its single extracted payload before installation:

- ENDF/B-VIII.0 radioactive-decay sublibrary from the IAEA Nuclear Data Services mirror. Reference: D.A. Brown et al.,
  “ENDF/B-VIII.0: The 8th Major Release of the Nuclear Reaction Data Library with CIELO-project Cross Sections, New
  Standards and Thermal Scattering Data,” Nuclear Data Sheets 148 (2018) 1–142,
  <https://doi.org/10.1016/j.nds.2018.02.001>.
- JEFF-3.3 radioactive-decay sublibrary from the IAEA Nuclear Data Services mirror. Reference: A.J.M. Plompen et al.,
  “The joint evaluated fission and fusion nuclear data library, JEFF-3.3,” European Physical Journal A 56 (2020) 181,
  <https://doi.org/10.1140/epja/s10050-020-00141-9>.

The official projects' notices and terms continue to apply to those files.

## Software and data licences are separate

ACTINV source code is offered under MIT OR Apache-2.0. Those software licences do not replace or relicense TENDL,
ENDF/B, JEFF, or any other third-party nuclear data. Redistribution of the processed TENDL-2025 release assets is under
TENDL's CC-BY-4.0 declaration and the attribution above. No warranty is provided for either the software or data.
