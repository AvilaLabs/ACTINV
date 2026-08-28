# ACTINV v1.0.0 — release notes

ACTINV v1.0 is the first technically complete repository release for reproducible activation and nuclide-inventory
workflows. It combines a deterministic Rust solver and data pipeline with a Python interface, transport-flux imports,
mesh execution, decay-photon sources, radiological responses, and explicitly bounded uncertainty reporting.

This is research-grade software. It is not approved for licensing, safety, waste classification, or regulatory use.
Public tagging and uploads to PyPI, crates.io, and GitHub Releases are separate maintainer actions and are not implied
by the repository's technical release verdict.

## Installation

Published Python wheels support Python 3.9 and newer through the stable ABI:

```bash
pip install actinv
```

That one installation provides both `import actinv` and the `actinv` command. Native standalone binaries are also
distributed as GitHub release artifacts, and the command can be built from source with:

```bash
cargo install --locked --path crates/actinv-cli
```

Nuclear data are not embedded in either software artifact. The recommended versioned neutron bundle can be installed
and verified with:

```bash
actinv data fetch
actinv data verify
```

See [Nuclear data](DATA.md) for charged-particle, covariance, offline, and manual-build options.

## Added since v0.5

- Strict ENDF-6 MF=33 covariance sidecars tied to the activation library, energy structure, and source manifest.
- Selectable CRAM-16/48 integration, exact local response sensitivities, and first-order MF=33 bands for selected heat
  and activity responses, with covered/uncovered parameters and numerical-method bounds kept separate.
- User-supplied, hash-pinned clearance-index, waste-index, ingestion-dose, and inhalation-dose tables, including
  scenario metadata, coefficient coverage, and an optional complete-coverage requirement.
- Independent re-derivation of all 289 embedded natural-abundance and atomic-mass rows from the Meija et al. primary
  table and AME2020, including byte-identical source regeneration.
- A bounded deterministic input-reliability gate and minimized regression corpus for the supported public interfaces.
- FNG/ITER cell-620 activation-history reproduction from the published research archive, preserving every supplied
  source hash and keeping all generated bulk data outside Git.
- Stable-ABI Python wheels that install both the Python API and full command, standalone release binaries, consistent
  `1.0.0` interface versions, and public-release documentation/checklists.
- A separately versioned, immutable data catalog with verified one-command setup for exact validated TENDL-2025
  artifacts and official decay archives.

## Validation summary

- P11's complete TENDL-2025 neutron covariance scan covers 2,850 evaluations, 84,489 MF=33 sections, and 285,023
  retained components with no parse error or silent omission. The final sidecar is fresh/cached byte-identical.
- Analytic sensitivities agree with independent finite differences, and reported covariance propagation agrees with
  direct matrix calculations and an independent fixed-seed sampling control within the frozen limits.
- P12 radiological responses are identical through CLI, Python, prepared, and mesh paths; 80 independent response
  comparisons agree exactly, and incomplete activity coverage remains explicit.
- The independent primary-table control reproduces 289 abundance/mass rows, all 84 element sums, and the generated
  Rust table byte-for-byte.
- Repeated 10,000-case input checks produce the same deterministic summary, and the fixed 1,000,000-case partition
  covers all eleven public reader families with no process-level failure below its 1 GiB ceiling. Five pre-run
  allocation/order and encoding findings retain focused regression tests.
- The FNG/ITER control compares Co-58, Tc-99m, Mn-56, and Cr-51 at all 170 endpoints. Its worst relative difference on
  populations at or above one million atoms is `2.88e-14`, within the frozen `1e-4` bound. Selected independently read
  reaction rates agree to `3.24e-16` relative.
- The clean-clone release control builds and verifies all three unpacked Rust archives, the standalone `1.0.0` binary,
  a Python 3.9 stable-ABI wheel, and the Python source distribution; package metadata, both licence texts, the wheel
  SBOM, installed console command, import behavior, strict Rust gates, prior verdicts and CLI/Python end-to-end
  behavior all pass.

The complete phase evidence and exact hashes are in `results/`, `protocols/`, and the append-only session records.

## Carried limitations

- ACTINV performs inventory evolution, not neutron/photon transport, criticality, thermal hydraulics, or a GUI.
- Mesh cells are independent and do not feed composition, flux, or temperature changes back to one another.
- Supported incident particles are neutron, proton, deuteron, and alpha; triton, helion, and gamma activation are not
  implemented.
- Unresolved-resonance treatment is infinite dilution. Finite-dilution self-shielding, probability tables, and
  Bondarenko factors are not implemented.
- Products absent from configured decay files remain named leakage. No missing decay or fission-yield data are
  invented.
- Contact gamma dose is a semi-infinite-slab screening proxy. Photon and neutron transport remain external, and the
  ordinary photon exporters retain a point-at-origin spatial placeholder.
- MF=33 bands cover retained cross-section covariance only. Decay, yield, flux, composition, response-coefficient,
  geometry, and model uncertainties are excluded; missing evaluated cross-covariance remains explicit.
- Radiological tables are not bundled or selected by ACTINV. Users must choose the applicable jurisdiction, edition,
  scenario, intake assumptions, chemical/aerosol form, and margins, and must assess missing coefficient coverage.
- OpenMC, MCNP, and FISPACT imports cover documented, fail-closed subsets with explicit physical normalization. They do
  not validate the originating transport calculation.
- The 132-experiment FNS result and the FNG/ITER cell-620 result apply to their recorded materials, data, histories,
  and responses. Neither is a general safety or shutdown-dose qualification.
- Performance measurements and large-mesh extrapolations are fixture-specific sizing evidence, not guarantees.
- Data inputs and generated bulk libraries are not embedded in the software package or Git repository. The optional
  data release retains public provenance, terms, and SHA-256 identities; users remain responsible for the dataset they
  select.

See [Qualification boundary](QUALIFICATION.md) for responsibilities when ACTINV is placed in a controlled analysis
chain and [Validation](VALIDATION.md) for detailed evidence.
