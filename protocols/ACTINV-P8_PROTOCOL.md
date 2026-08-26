# ACTINV P8 — flux import and independent mesh execution

**Roadmap row:** P8 (second and final phase of the v0.2 milestone). **Opened:** after the P7 close. **Time box:** three
calendar days. **External acts:** the principal's. This protocol covers transport-flux interchange and independent
activation solves over cells. Neutron transport, flux generation, self-shielding, interpolation between spatial cells,
and heterogeneous material-map inference are out of scope; a P8 mesh specification applies one explicit material and
schedule to every imported cell.

**Minimum gate input** (standing rule 7): deterministic four-energy-group, four-cell fixtures representing an OpenMC
18.2 statepoint, an MCNP rectangular neutron `meshtal`, an MCNP F4:N `mctal`, and standard FISPACT-II `fluxes`; the
existing 10-target CI activation library and decay files; and one 8-cell canonical file on the library's exact group
structure. HDF5 fixtures are generated with pinned h5py and read independently by h5py. No OpenMC or MCNP executable,
full activation-library build, full FNS rerun, or million-cell solve is a prerequisite for the gates.

## Normative formats and numerical choices

1. `actinv import-flux {openmc|meshtal|mctal|fispact} ... OUT.ndjson` produces line-delimited JSON schema
   `actinv-flux-1`. The first record is a header containing the source format, selector, computed source SHA-256,
   normalization, ascending energy boundaries in eV, units, geometry and declared cell count. It is followed by one
   ordered record per cell containing a unique cell ID, optional one-based spatial index/bounds/volume, group-integrated
   fluxes and optional relative errors, then one footer with independently accumulated counts and totals. Values are
   finite and nonnegative; boundaries are strictly increasing after any explicit energy-floor substitution. Duplicate,
   missing, extra or out-of-order records are hard errors. Import writes a sibling temporary file and renames only after
   a complete footer; interrupted work never resembles a successful interchange file.
2. Canonical group values are **integrated neutron flux per group** in `n cm^-2 s^-1`, not density per eV or lethargy.
   Each cell total is their compensated sum. The footer reports the compensated sum over cells and, where every cell
   has a volume, the volume-integrated total. Source-native totals are retained and checked rather than silently
   replaced. MCNP and OpenMC tallies normalized per source particle require an explicit positive source rate in
   `source s^-1`; FISPACT values are taken as the absolute physical values written in the file.
3. The source file is hashed while it is read and re-statted afterwards; an input changed during import is rejected.
   Any auxiliary boundary file is independently hashed. A mesh solve trusts only a user-declared SHA-256 for the
   completed canonical file, recomputes it, and binds both that hash and the embedded upstream hashes into its
   certificate. The original transport file need not remain locally available after canonicalization.
4. OpenMC support is the statepoint-format-18 mesh-flux subset described by the current 18.2 specification: one
   selected tally with exactly one `MeshFilter` and one `EnergyFilter` in either order, score `flux`, nuclide `total`,
   and a three-dimensional regular or rectilinear Cartesian mesh. The mean is `results[..., sum] / n_realizations`.
   Filter rows use OpenMC's declared order and strides (the last filter varies fastest); mesh cells remain in native
   one-based `(i,j,k)` order with `i` fastest. OpenMC's flux score is volume-integrated track length per source
   particle, so physical cell flux is `mean * source_rate / cell_volume`. Other filters, scores, nuclides, mesh types,
   dimensions or statepoint major versions are named in a hard error. HDF5 metadata and result rows are read in bounded
   windows rather than loading the statepoint or result dataset wholesale.
5. MCNP `meshtal` support is the traditional ASCII column (`COL`) format for rectangular/XYZ neutron FMESH flux,
   with energy-resolved rows and optional per-cell `Total` rows. Cylindrical/spherical meshes, dose-response or tally
   multipliers, time/collision/user bins, matrix/CUV/scientific-column/XDMF forms and non-neutron tallies are rejected.
   MCNP F4/FMesh flux is already divided by volume and normalized per source particle, so only the explicit source rate
   is applied. Source `Total` rows must equal the parsed energy-bin sum to 1e-12 relative.
6. MCNP `mctal` support is an energy-binned F4:N cell-flux tally: one spatial F dimension containing MCNP cell IDs,
   one E dimension, and singleton D/U/S/M/C/T dimensions. Perturbations, multipliers, cumulative bins, mesh tallies and
   other tally/particle types are rejected. Values and relative errors are paired under the MCTAL ordering; any total
   energy bin is checked but not duplicated as a group. MCNP MeV energy boundaries are converted to eV by exactly
   `1e6`, and the explicit source rate is applied without another volume normalization.
7. A standard FISPACT-II `fluxes` file is read against an explicit hashed group-structure JSON: exactly N descending
   high-to-low flux values, followed by first-wall loading and the identifying title. The importer reverses values and
   boundaries together into canonical ascending order, retains wall loading/title, and applies no hidden rescaling.
   Arbitrary `arb_flux` input is not conflated with the standard file.
8. If a transport energy grid begins at zero, import requires an explicit positive `energy_floor_eV` below the next
   boundary. The canonical grid uses that floor and provenance retains the original zero and the substitution; the
   tool never invents a floor. Exact library boundaries take a copy-only path. Otherwise, mesh execution rebins by the
   FISPACT default equal-flux-per-unit-lethargy rule: an input group's contribution is proportional to logarithmic
   overlap. Compensated sums must conserve `destination + underflow + overflow = source` to 1e-12 relative. Energy
   outside the activation library is ledgered and never folded into an edge group or erased by renormalization.
9. `actinv-mesh-spec-1` contains the same library, decay, material, schedule, options and photon objects as
   `actinv-spec-1`, replacing `spectrum` with a hashed `flux` reference and adding bounded `chunk_cells` and `threads`
   controls. `actinv mesh SPEC.json OUT.ndjson` prepares and verifies immutable nuclear data once, streams canonical
   cells in chunks, rebins each spectrum, performs each cell's ordinary core solve and pruning independently with
   Rayon, restores input order, and streams `actinv-mesh-result-1` header/cell/footer records. Each cell record carries
   its spatial metadata, rebin ledger and ordinary run result. A failure names the cell and leaves no final output.
10. Thread count changes scheduling only: one thread and N threads must produce byte-identical deterministic records
    apart from explicitly excluded wall-clock fields. Per-cell solver timing is not serialized; the mesh footer records
    total wall time and throughput. Input/output buffering is bounded by `chunk_cells`, group count and one chunk of
    results, never total cell count. Output size remains proportional to requested result detail and is reported rather
    than hidden.
11. P8 closes the v0.2 milestone: crate/wheel version becomes 0.2.0 and changelog, specification, method, data,
    validation, ledger and release notes document supported subsets, normalization requirements and measured limits.
    Tagging, publishing and making any release public remain external acts.

## Dependencies fixed before use

- Production `rayon` 1.12 (MIT OR Apache-2.0) supplies scoped data-parallel iteration.
- Production `hdf5-pure` is exact-pinned to 0.39.0 (MIT). It has no C dependency and provides streaming file access,
  bounded metadata/chunk caches and leading-dimension row-window reads. Because it is young and releasing rapidly,
  compatibility with reference-HDF5 files is a gate and unsupported encodings must fail rather than fall back.
- Control-only `h5py` 3.16.0 (BSD-3-Clause) independently creates and reads the HDF5 fixtures. MCNP and FISPACT
  controls use independent small Python parsers; production code is not copied from them or from MCNPTools.

Primary format references are the OpenMC statepoint/tally documentation and source, MCNP 6.3.2 Theory & User Manual,
and FISPACT-II User Manual `GRPCONVERT`/`CNVTYPE`. Their URLs and data terms are recorded in `docs/DATA.md`.

## Deliverables

1. Rust canonical format reader/writer plus OpenMC statepoint, MCNP meshtal/mctal and FISPACT flux importers exposed
   through the CLI, with deterministic fixtures and precise unsupported-form errors.
2. Conservative spectrum rebinning with exact-match identity and explicit underflow/overflow diagnostics.
3. Prepared-data core execution and deterministic, chunked Rayon mesh runner using the same single-cell solver path.
4. Independent controls, a measured timing/RSS/output-size table with explicitly labelled extrapolation through
   10^6 cells, a checker-derived verdict, session/manifest, and v0.2 documentation/version metadata.

## Gates

**G1 Canonical, FISPACT and rebin.** Production and independent readers agree at 0.0 on every canonical/FISPACT field
and group value. Exact-boundary rebinning is bit-identical. Split-group equal-lethargy cases, including partial
underflow/overflow, conserve at 1e-12 relative and match an independent implementation to 1e-12. Zero-boundary input
without an explicit floor and malformed/truncated/duplicate canonical records fail closed.

**G2 OpenMC statepoint.** A pinned-h5py statepoint fixture exercises both filter orders, regular and rectilinear cell
volumes, nonuniform spectra and result row windows. Rust agrees with independent h5py/OpenMC-layout calculations to
1e-12 relative for every cell/group and all aggregate totals, preserves native indices exactly, and rejects planted
wrong score/nuclide/filter/mesh/version cases. A source-rate omission and canonical/source hash mismatch are hard
errors. Peak RSS is bounded by configured windows rather than padded statepoint size.

**G3 MCNP readers.** On representative column meshtal and F4:N mctal fixtures, production and independent parsers
agree at 0.0 on IDs/indices/bounds and within 1e-12 on every energy, flux, relative error and total after MeV/eV and
source-rate conversion. Planted inconsistent `Total`, wrong tally type, dose multiplier, extra bin dimension and
truncation each fail with the unsupported premise named.

**G4 Provenance and interchange identity.** Every importer produces deterministic canonical bytes on repeat; source,
auxiliary and canonical SHA-256 values recompute independently and propagate to the mesh certificate. A file mutated
during import, a wrong declared canonical hash, a nonfinite/negative value and a changed footer count each fail closed.
The four source formats representing the same physical case yield identical canonical cell spectra and totals at 0.0
after their specified unit/normalization conversions.

**G5 Mesh identity and determinism.** For at least eight nonuniform cells, every deterministic physics/result scalar,
array, ledger item and certificate input from mesh mode equals a separate ordinary `actinv-spec-1` run at 0.0 after
excluding entry-point labels and wall-clock fields. Cells have independently different pruned-state counts. One-thread
and multi-thread runs produce byte-identical ordered cell records and footer totals apart from wall time/throughput; a
planted cell failure creates no final result.

**G6 Scaling and regression.** The runner demonstrates bounded-memory streaming over cell counts exceeding one chunk.
A reproducible table reports measured wall time, peak RSS, output bytes and cells/s for at least four increasing sizes,
then labels fitted/extrapolated rows through 10^6 cells with assumptions and no claim that an unexecuted size ran.
Workspace tests and strict Clippy pass; P5 remains P5-PASS, P6/P7 retain their recorded conditional verdicts, and P8
does not change any pre-P8 single-cell deterministic result field.

## Verdict (`controls/check_p8.py`)

P8-PASS: G1-G6. P8-CONDITIONAL after one documented repair round on a gate. P8-FAIL otherwise. Standing rules 1-7
apply.
