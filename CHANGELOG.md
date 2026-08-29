# Changelog

## Unreleased

**Validation**

- Opened the evidence-directed P18 state-identity phase under a pre-evidence hash. It freezes physical ENDF product
  mapping, state-partial conservation, fail-loud missing-isomer handling and a family-level held-out public
  isomeric-ratio score before any production behavior changes.
- Recorded P18's sole procedural amendment after an incorrect fixed-column redaction exposed five early source
  families. Those families are permanently diagnostic; the remaining held-out partition, product behavior and all
  acceptance thresholds are unchanged.
- Sealed 962 public isomeric-ratio reaction families at family granularity before calculation, leaving 180 families
  and 1,945 rows genuinely held out. The committed metadata contains no dependent measurement, and an independent
  checker rederives the split and rejects identity, partition, quarantine and value-leak plants.
- Closed the post-release P17 open-validation phase with its frozen `P17-FAIL` verdict intact. Same-operator,
  identical-data, processing, provenance, independent-arithmetic and quality controls pass; all 94 held-out rows and
  every unsupported case remain visible. The failure records three falsified benchmark assumptions and changes no
  production behavior, public interface, default, package or nuclear-data artifact.

**Fixed**

- The crates.io trusted-publisher environment now matches the registry's configured `crates.io` OIDC identity. A
  protected manual recovery path can resume publication from an existing version-matching release tag without moving
  or reusing that tag.

## v1.0.1 — 2026-08-28

**Added**

- Automatic, deterministic prepared activation-data caches bound to the exact source library, index, schema and flux
  bits, with atomic publication, fail-closed integrity checks and safe deletion/recreation.
- Indexed sparse groupwise reads for prepared and mesh workflows, plus an exact spectrum-collapsed path for ordinary
  runs. The Python interface shares the same Rust cache and solver path.
- Independent exhaustive representation, cache-mutation, interface and frozen opening-binary performance controls.

**Changed**

- On the public FNS iron example and recorded x86-64 host, the warm path's median wall time fell from 3.075 s to
  1.185 s and peak RSS from 1.077 GB to 129 MB, while the normalized result and certificate provenance remained exact.
  These measurements describe that workload, host and warm-cache state rather than a general competitor claim.
- Release validation now derives the current software version from package metadata and keeps the independently
  versioned `data-v1.0.0` catalog distinct during wheel smoke tests.

**Fixed**

- Patch-version provenance no longer changes the frozen P15 scientific-result hash. Raw result certificates still
  retain the exact solver version, and the normalized control accepts only the documented solver-semver field.
- Standalone crate validation now resolves temporary package lockfiles offline, proves their external dependency
  records are a subset of the workspace lock, and keeps compiler temporary files inside the selected gate work root.

## v1.0.0 — 2026-08-28

**Added**

- Strict ENDF-6 MF=33 LB=0--6/8/9 parsing and deterministic, checkpointed `actinv-covariance-1` sidecars linked to
  their activation library, group structure and source manifest.
- Selectable CRAM-16/48 plus exact differentiation of the selected incomplete-partial-fraction recurrence for local
  heat/activity sensitivities through irradiation, pulses and cooling.
- Per-response MF=33 standard uncertainties, normal intervals, separate alternate-CRAM bounds, conservative expanded
  intervals, complete parameter metadata and explicit covered/uncovered/absent-cross accounting through CLI, PyO3,
  prepared and mesh entry points.
- Independent Python/dense/NJOY/OpenMC controls and a complete 2,850-source covariance scan with fresh/cached byte
  identity, bounded memory and zero parsing omissions.
- Hash-pinned clearance, waste, ingestion and inhalation response tables with explicit scenario metadata, coefficient
  coverage and identical CLI/Python/prepared/mesh results.
- Independent primary-source regeneration of all 289 embedded natural-abundance and atomic-mass rows.
- A deterministic one-million-case, eleven-family production-reader reliability gate with a repeated CI partition
  and a strict 1 GiB process ceiling.
- FNG/ITER cell-620 activation-history reproduction at 170 endpoints from a fully hash-pinned published archive.
- Stable-ABI Python wheels for Python 3.9+, standalone release binaries, a public artifact workflow and user-facing
  installation, release and qualification documentation.
- The PyPI wheel installs both `import actinv` and the full `actinv` terminal command through one compiled Rust-backed
  package, with native wheel smoke tests on Linux, macOS and Windows.
- A strict embedded `actinv-data-catalog-1` manifest and `actinv data list/fetch/verify/manifest` commands.
- Versioned, atomic, SHA-256-verified setup for the exact P10 TENDL-2025 activation libraries, the matching P11
  neutron covariance sidecar, and official ENDF/B-VIII.0/JEFF-3.3 decay archives.
- Data attribution, offline/manual setup guidance, release staging, and independent distribution controls without
  committing nuclear-data payloads to Git.

**Fixed**

- Covariance checkpoint identity is source-local, and full sidecar aggregation retains one grid map and validates the
  combined output once instead of rebuilding growing prefixes quadratically.
- The local CI end-to-end control can load an explicitly built PyO3 extension when no wheel is installed, while still
  preferring and testing the installed package in release CI.
- ENDF, decay, fission-yield and activation-library readers now prove declared payload sizes before reserving memory;
  path and in-memory entry points share the same production implementations.
- CLI and Python package versions now agree at `1.0.0`; `actinv --version` and `actinv --help` are supported and
  regression-tested.

## v0.5.0 — 2026-08-27

**Added**

- Explicit ground-state/isomer material keys across weight-percent, atom-fraction and literal atoms-per-gram bases,
  with evaluated AWR provenance and ambiguity checks.
- Strict, hash-pinned ENDF-6 MF=8/MT=454/459 fission-yield parsing; independent-yield interpolation/selection; and
  yield-expanded MT=18 matrix feeds with complete mapped/leakage balance.
- Per-boundary elapsed time, multiplier-weighted exposure and physical fluence for arbitrary piecewise-constant pulse
  histories, through ordinary and mesh runs.
- Independent P9 controls against dense exponentials, OpenMC CRAM48, ALARA 2.9.2 and the CoNDERC U-235 Dickens pulse
  and Yarnell 20,000 s decay-heat sets.
- A strict, deterministic Rust ENDF-6 activation-library builder with content-addressed per-target checkpoints and
  neutron, proton, deuteron and alpha runtime contracts.
- R-matrix-limited resolved reconstruction, infinite-dilution `LSSF=0` unresolved averages, arbitrary-temperature
  SIGMA1 broadening, analytic ultra-narrow lines and charged-particle MF=6 residual production.
- Complete hash-pinned TENDL-2025 neutron/proton/deuteron/alpha and EAF-2010 builds: 12,216 targets and 1,849,479 rows,
  all fresh/cached byte-identical with zero target errors, silent fallbacks or convergence flags.
- Independent P10 controls against NJOY2016.79, FENDL ACE, official TENDL residual tables and official processed
  FISPACT TENDL-2017 rows; no licensed FISPACT executable run is claimed.

**Fixed**

- Automatic trace/coupled selection now uses each initial isotope's reaction-loss optical depth over the complete
  multiplier-weighted schedule; non-unit pulse multipliers and cooling gaps can no longer be miscounted.
- Scientific-notation durations such as `1e-8 s` no longer treat the exponent marker as a unit suffix.
- Deep but bounded resonance linearization now admits the corpus-observed pass-19 Co-58 kink while retaining the
  unchanged error tolerance and ten-million-point safety cap.
- Rust and independent controls use NJOY component-effective Breit-Wigner widths consistently when evaluator `GT`
  rounding differs from the component sum.

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
