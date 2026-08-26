# ACTINV v0.2.0 — release notes

*Research-grade. Not validated for licensing, safety or regulatory decisions.*

v0.2 makes ACTINV useful as the activation step in a manually supervised rigorous two-step workflow. It imports
supported transport-code neutron mesh tallies into a hashed, code-neutral stream, solves every cell independently, and
also adds evaluated decay-photon sources, screening-dose quantities and transport-source exports from v0.2's P7 work.

## What works in v0.2

- `actinv import-flux` reads OpenMC statepoint-format-18 mesh-flux tallies, traditional rectangular neutron MCNP
  MESHTAL files, energy-binned MCNP F4:N MCTAL tallies and standard FISPACT-II `fluxes` files within the explicitly
  documented subsets. Unsupported variants are named hard errors.
- `actinv-flux-1` is ordered NDJSON with source/auxiliary hashes, physical normalization, energy and spatial metadata,
  independently accumulated totals and a required closing footer. Writers publish by atomic sibling rename.
- Exact activation-library grids copy bit-for-bit. Other positive grids use FISPACT's default equal-flux-per-unit-
  lethargy rule, with compensated conservation and separate underflow/overflow instead of edge folding.
- `actinv mesh` verifies a declared canonical hash, prepares immutable library/decay/response data once, and runs
  independently pruned cells through the ordinary core in bounded chunks with Rayon. Thread count affects scheduling
  only; deterministic records remain ordered.
- P7's evaluated decay-photon line/multigroup sources, gamma-constant/contact-screening response quantities and
  OpenMC/MCNP point-source exports are included.

OpenMC and MCNP tallies normalized per source particle require an explicit positive physical source rate. OpenMC flux
is divided by cell volume; MCNP F4/FMesh flux is not divided again. FISPACT standard-file values are used as written.
A source grid beginning at zero requires a user-chosen positive floor below its second boundary.

## Validation and measured limits

All P8 gates pass. OpenMC results agree with independent h5py 3.16.0 and both MCNP readers agree with independent text
parsers at 0.0 on the deterministic fixtures. Four formats reproduce the same physical spectrum exactly. Eight mesh
cells match eight separate ordinary runs at 0.0 and choose eight different pruning sizes; one- and four-thread cell
records are byte-identical. A 48 MiB unused HDF5 payload did not increase measured importer peak RSS.

The measured four-worker/four-cell-chunk sizing rows are:

| cells | wall time (s) | peak RSS (MB) | output (MB) | cells/s |
|---:|---:|---:|---:|---:|
| 8 | 0.352 | 127.0 | 0.118 | 22.7 |
| 16 | 0.354 | 126.9 | 0.221 | 45.2 |
| 32 | 0.366 | 126.8 | 0.425 | 87.5 |
| 64 | 0.383 | 127.1 | 0.835 | 167.0 |

The fitted one-million-cell row was **not run**. For this 10-target, two-step, four-worker control configuration it
estimates 573.8 seconds and 12.80 GB of result output; the bounded-buffer model uses the measured 127.1 MB RSS ceiling.
Larger chains, schedules, inventories and photon/pathway detail can materially change time and output size. Full raw
rows and assumptions are in `results/g6_p8_scaling_regression.json`.

P8's checker verdict is **P8-CONDITIONAL** because the first independent-control pass required one documented repair
round. The Python MESHTAL reader used exact float lookup after MeV/eV conversion and left total rows in source order;
the nonfinite plant also expected a narrower error phrase than the strict decoder returned. Amendment A records all
three control-only corrections. Production physics, imported values and tolerances did not change. P7 remains
P7-CONDITIONAL for its separately documented exporter-control repair; P5 remains P5-PASS and P6 P6-CONDITIONAL.

## Known limitations

- Mesh cells are independent. v0.2 does not perform neutron transport, depletion feedback, self-shielding, spatial
  interpolation or heterogeneous material-map inference; one explicit material and schedule applies to every cell.
- The importer subsets above are exact, not generic. Other OpenMC filters/estimators/mesh types, MCNP particles,
  dimensions, responses/multipliers and binary/scientific-column variants fail closed.
- Equal-lethargy rebinning cannot recreate detail absent from a coarse source spectrum. Underflow and overflow are
  reported and excluded from activation rather than silently reassigned.
- Ordinary photon export remains a point at the origin. Mesh activation output carries geometry, but v0.2 does not
  synthesize a distributed shutdown-dose transport source or solve photon transport.
- Fission products are not followed; fission production is sent to the explicit leakage state. Covariance uncertainty,
  clearance/waste indices and ICRP ingestion/inhalation dose coefficients remain later-roadmap work.
- TENDL R-matrix-limited and unresolved `LSSF=0` resonance ranges retain the v0.1 guards, as do the documented
  ultra-narrow-resonance convergence flags and products without evaluated decay data.
- `hdf5-pure` 0.39.0 is young and exact-pinned. Reference h5py fixtures and padded-file memory tests pass, but unknown
  HDF5 encodings fail rather than falling back to a native-HDF5 implementation.

## Reproduction and external acts

Run the P8 controls with the pinned CI data subset and `python controls/check_p8.py`; the checker derives the verdict
from `results/g1_p8_*.json` through `g6_p8_*.json`. Nuclear and transport data remain external and are bound by SHA-256.
Tagging, publishing crates/wheels, hosting a public release and any safety or licensing use are the user's external
acts.

MIT OR Apache-2.0. Contributions remain under the Developer Certificate of Origin.
