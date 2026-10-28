# ACTINV data bundle v1.2.0 — attribution and provenance

This notice covers the nuclear-data files installed by `actinv data fetch`. It does not change ACTINV's qualification
boundary: users remain responsible for choosing data and models appropriate to their application.

## What is new in v1.2.0

This data release adds `tendl-2025-neutron-damage`, a shipped damage table for the neutron bundles, and changes no
other artifact: every v1.1.0 artifact keeps its identifier, path, size, and SHA-256. The table is referenced by the
four neutron bundles (full and patched, with and without covariance). It lets a neutron problem request dpa, exact
NRT dpa, arc-dpa, and He/dpa by naming one catalog entry instead of building a table first.

Source and transformation: the table is derived from the official TENDL-2025 neutron evaluations (not from the
patched derivative; the ordinate repairs described below are not applied to it). Its targets are the TENDL-2025
neutron evaluations whose nuclide the catalog's decay data lists as stable or with a half-life of at least one
Julian year. ACTINV computed the kinematic damage energy and displacement cross sections from each evaluation with
`actinv build-damage --method kinematic` on the 709-group structure, and hashed every evaluation file into the
table. Two departures from the distributed files, both recorded in the table: Pb-208 is built from the neutron working
corpus file that the activation library also uses (SHA-256 `86788a14563ecdb844628a6a455864874ba5f5bca9b8142c10a59ea00df87c72`),
because the distributed file carries a literal `NaN` that the P10 repairs replace; and where a 0 K resonance
reconstruction goes negative (Fe-54's elastic near 3.5 keV), the value is set to zero as NJOY RECONR does, and the
target's row records the clipped range as `zero_k_clipped`. The table was built after a correction to ACTINV's
Reich–Moore reconstruction: resonances whose AJ values carry opposite signs (the two channel spins) are now separate
channels, as NJOY RECONR treats them. Four evaluations change: Al-27, Cl-37, Mn-55 and Si-29. The v1.1.0 activation
libraries were built before the correction and are unchanged. For those four nuclides, their resolved-range capture
differs from the corrected 0 K reconstruction by up to 16 % at isolated Al-27 resonances near 345 keV (2.1 % for
Si-29, 1.1 % for Cl-37, under 1e-4 for Mn-55); thermal capture is identical, and capture resonance integrals differ by
at most 3e-5. The standard displacement energies are a convention, not a material-specific measurement. The table inherits the evaluation defects documented in `docs/DATA_LIMITATIONS.md`.

Licence and attribution: the table is a transformation of TENDL-2025 evaluations that declare `CC-BY-4.0`, and is
offered under CC-BY-4.0 with the attribution given in the TENDL section below. It was changed from the source form by
the computation described above.

## What was new in v1.1.0

The v1.1.0 data release added `tendl-2025-patched`, an Avila Labs remediation derivative of the TENDL-2025 neutron
sublibrary, alongside the unchanged v1.0.0 artifacts. It is **not an official TENDL release** and must never be
presented as one. The official upstream evaluations remain the property of their creators; the preferred long-term
path is a corrected upstream TENDL release, which the TENDL authors have indicated will land in a future release.

Why it exists: ACTINV's P25 audit machinery confirmed that the official TENDL-2025 neutron sublibrary leaks the
thermal (n,p) cross section into the first emitted-state ordinate of certain MF=10 product records. The defect was
reported upstream, and the root cause was acknowledged by the TENDL authors. The `tendl-2025-patched` corpus zeroes
exactly the 44 enumerated leaked leading ordinates across the 28 confirmed-signature files and is byte-identical to
the official archive everywhere else. The full scope, mechanism, and remaining limitations are documented in
`docs/DATA_LIMITATIONS.md` and the P25c phase evidence.

Qualification status: the derived corpus passed the frozen P25c construction, coverage, surgical-integrity,
leak-clearance, and retrospective nonregression gates. That is retrospective evidence of consistency, not blind
validation. Five dosimetry-critical targets (Ni-58, Nb-93, Ag-109, In-113, Au-197) still fail closed under other,
non-signature defect classes that this patch does not repair. Users must consult `docs/DATA_LIMITATIONS.md` before
relying on this data for any real application.

## TENDL-2025 activation, covariance, and damage data

The activation libraries, neutron covariance sidecar, and neutron damage table are transformations of TENDL-2025 evaluated nuclear data
(the patched-neutron artifacts additionally apply the documented 44-ordinate surgical patch). Every TENDL-2025
evaluation used to produce these artifacts declares `CC-BY-4.0` in its ENDF-6 identification record.

- Creators named by TENDL-2025: A.J. Koning, D. Rochman, V. Raffuzzi, and J.-C. Sublet.
- Source: <https://tendl.web.psi.ch/tendl_2025/tendl2025.html>
- Licence: Creative Commons Attribution 4.0 International,
  <https://creativecommons.org/licenses/by/4.0/>
- Recommended reference: A.J. Koning, D. Rochman, J.-C. Sublet, N. Dzysiuk, M. Fleming, and S. van der Marck,
  “TENDL: Complete Nuclear Data Library for Innovative Nuclear Science and Technology,” Nuclear Data Sheets 155
  (2019) 1–55, <https://doi.org/10.1016/j.nds.2019.01.002>; and D. Rochman et al., Nuclear Physics A 1053 (2025)
  122951.

ACTINV changed the source form by parsing ENDF-6 reaction/product records, reconstructing supported resolved and
unresolved resonances, applying the recorded temperature treatment, and collapsing cross sections or MF=33 covariance
onto the named FISPACT group structure. The activation artifacts use the P10 builder fingerprint
`7a50ba3441b30b829ae857ed192b2e52554d6c149460475f7735599f29548a43`; the covariance sidecar uses P11 fingerprint
`c9825cafd8945f32efda4a00ea081af811b887562ebf07ae33ac05ea1d6846d1`.

The TENDL-2025 neutron working corpus differs from the official archive at two nonfinite Pb-208 fields at 1 MeV. In
MF=3/MT=1 and MF=3/MT=3, the literal `NaN` field was replaced with the immediately preceding finite left-branch value
`4.925328-7`. This fail-closed repair and both line identities are recorded in ACTINV's P10 protocol and evidence.
The `tendl-2025-patched` corpus additionally differs at the 44 documented leading ordinates (zeroing the leaked
thermal (n,p) value), recorded in the P25c patch log; all other bytes are identical to the official archive. The
released artifact's source set carries the same two documented Pb-208 repairs, so its built input equals the P10
working corpus plus the P25c patch.

Exact official archive, source-manifest, builder, output, and index identities are recorded in the bundled catalog
and in ACTINV's `docs/DATA.md`, `docs/P10_G7_EXECUTION.md`, `docs/P11_G6_EXECUTION.md`, and the P25c phase evidence.

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
ENDF/B, JEFF, or any other third-party nuclear data. Redistribution of the processed TENDL-2025 release assets and of
the `tendl-2025-patched` derivative and the damage table is under TENDL's CC-BY-4.0 declaration and the attribution above. No warranty is
provided for either the software or data.
