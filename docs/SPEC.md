# ACTINV problem specification (`actinv-spec-1`)

One JSON document drives the CLI, Python API and validation harness. Unknown fields, non-finite numbers and invalid
hashes are errors; paths are literal filesystem paths (shell `~` expansion is not performed inside JSON).

```json
{
  "spec": "actinv-spec-1",
  "title": "FNS Fe, 5-minute irradiation",
  "projectile": "neutron",
  "library": {
    "path": "/data/actinv_tendl2025_n_709g.npz"
  },
  "decay": {
    "primary": "/data/endf-b-viii-0_decay.dat",
    "fallback": "/data/jeff-3-3_decay.dat"
  },
  "material": {
    "mass_g": 1.0,
    "basis": "wt_percent",
    "composition": {"Fe": 100.0}
  },
  "spectrum": {
    "structure": "fispact-709",
    "flux_per_group": ["709 numeric values"],
    "total": 1.116e10,
    "descending": true
  },
  "schedule": [
    {"dt": "5 min", "flux": 1.0},
    {"dt": "66 s", "flux": 0.0}
  ],
  "photon": {
    "group_structure": "fispact-24",
    "response": {
      "path": "/data/photon-response/nist-xcom-air-fe.json",
      "sha256": "4f00824ac66ef941cddbe20b93966523b7f0ff2271b35cdf8be538c48e404307"
    },
    "build_up_factor": 2.0,
    "gamma_constant_cutoff_eV": 20000.0
  },
  "options": {
    "mode": "auto",
    "prune": "rate",
    "bmin_atoms_per_g": 1e-8,
    "temperature_K": 293.6,
    "outputs": ["inventory", "activity", "heat", "photons", "dose", "pathways", "ledger", "certificate"]
  }
}
```

## Required inputs

| field | meaning |
|---|---|
| `projectile` | `neutron`, `proton`, `deuteron` or `alpha`; omission preserves the historical neutron default. |
| `library.path` | ACTINV `.npz` activation library; the adjacent `<stem>_index.json` is also required. |
| `library.sha256` | Optional declared hash. ACTINV always computes the library hash and fails if a declaration differs. The index's recorded library hash is checked too. |
| `decay.primary` | ENDF-6 radioactive-decay sublibrary. |
| `decay.fallback` | Optional second decay sublibrary; records absent from the primary are taken from it. |
| `material.composition` | Natural element symbols or explicit nuclides (`U235`, `Ba137m1`) and nonnegative values interpreted by `material.basis`. |
| `spectrum.flux_per_group` | Group-integrated fluxes. `descending: true` reverses the supplied order before use. |
| `schedule` | At least one duration/flux-multiplier pair. Accepted duration units: seconds, minutes, hours, days and years. |
| `fission_yields` | Optional hash-pinned ENDF-6 neutron-induced fission-yield evaluations; see below. Empty/omitted preserves the explicit no-yields leakage path. |

The certificate records computed SHA-256 values for the activation library, its index, primary/fallback decay data,
every fission-yield evaluation and the photon response. A declaration is a constraint, not a value copied into the
certificate.

## Material bases

`material.mass_g` defaults to 1 g. Inventories remain per gram; the mass scales the total photon rates and powers.
Composition keys are case-insensitive natural element symbols or explicit `SymbolA[mN]` nuclides. Bare `m` means
`m1`, so `BA137M`, `Ba137m` and `Ba137m1` identify the same state; aliases which collide are an error. A natural
element and one of its explicit isotopes cannot appear together. Explicit mass-based entries use the selected decay
evaluation's AWR times `1.00866491595 u` and fail if that record is absent. A literal `atoms_per_g` entry may instead
be ledgered as absent from the solvable chain; a photon-response calculation still requires its mass.

- `wt_percent` (default): each value is grams per 100 g. Values are used as stated rather than silently normalized;
  a total other than 100 is ledgered. Photon-response mixing normalizes them to mass fractions.
- `atom_fraction`: values are arbitrary elemental atom ratios. Natural isotopes are expanded and the mixture is
  normalized to one gram using the abundance-weighted elemental masses.
- `atoms_per_g`: each value is an elemental atom density per gram and is expanded by natural isotopic abundance.

All three bases apply identically to explicit nuclides: literal atom density for `atoms_per_g`, grams per 100 g for
`wt_percent`, and an arbitrary atom ratio normalized to one gram for `atom_fraction`. Response-function mixing
aggregates explicit isotopes back to elemental mass fractions.

## Projectile and spectrum

Neutrons use `fispact-709` with exactly 709 values. Proton, deuteron and alpha use `fispact-162` with exactly 162
values and require `options.temperature_K: 0`; charged specs reject fission-yield files. `custom` requires one more
strictly increasing boundary than flux values. Those boundaries must match the activation library to `1e-12`
relative. `total`, when present, rescales group values while preserving shape. The spec, library index, group
structure and temperature must all identify the same projectile/data build before matrix assembly.

## Fission yields

`fission_yields` is optional. Each file is one hash-pinned ENDF evaluation for one parent:

```json
"fission_yields": {
  "files": [
    {
      "path": "/data/endfb-viii.0-nfpy/nfy-092_U_235.endf",
      "sha256": "64 hexadecimal digits"
    }
  ],
  "energy": "fixed",
  "fixed_energy_eV": 0.0253
}
```

The production source is MF=8/MT=454 independent yield. MF=8/MT=459 cumulative tables are parsed and checked but
never used as matrix sources. Every independent table must sum to two fission fragments within `1e-6`; values are not
renormalized. Duplicate parents, energies or products, malformed/truncated records, negative/nonfinite values and hash
mismatches fail closed.

`energy: "fixed"` requires a finite nonnegative `fixed_energy_eV`; selection is exact, linearly interpolated, or
clamped to the evaluated range. `energy: "spectrum_average"` is the default and forbids `fixed_energy_eV`; it uses
the fission-rate-weighted representative incident energy separately for each parent. The certificate records the
requested energy, selected bracket, interpolation weight, clamp decision, product count and effective yield sum.
Fissioning parents without a matching file remain explicit leakage and never borrow another parent's evaluation.

## Photon options

The entire `photon` object is optional. Without a response file, ACTINV still emits evaluated line/multigroup photon
sources and energy-closure diagnostics, but dose fields are `null`.

| field | meaning | default |
|---|---|---|
| `group_structure` | `fispact-24`, or `custom` with `group_boundaries_eV`. | `fispact-24` |
| `group_boundaries_eV` | Finite, nonnegative, strictly increasing photon boundaries. | none |
| `response` | External `actinv-photon-response-1` JSON and mandatory SHA-256 declaration. | none |
| `build_up_factor` | Semi-infinite-slab screening factor `B`. | 2 |
| `gamma_constant_cutoff_eV` | Lower energy cutoff for specific gamma constants. | 20,000 eV |

Build response data with `scripts/build_photon_response.py`; see [DATA.md](DATA.md). A response must contain attenuation
curves for every material element to produce the contact-dose proxy.

## Options and result

`mode` is `auto`, `trace`, or `coupled`. For each initial nuclide, `auto` computes the base-spectrum reaction-loss
optical depth `tau = loss_rate * sum(dt * flux_multiplier)` and burn-up fraction `-expm1(-tau)`; it selects `trace`
only when the largest fraction is strictly below `1e-6`. The controlling nuclide, optical depth and fraction are
ledgered. Explicitly requested modes are always honored. `prune` is `rate`, `reach`, or `none`. The `outputs` list
controls optional pathway and photon/dose calculations; the core inventory/activity/heat diagnostics remain in each
result step.

The ordered schedule is the pulse representation: every positive `flux` multiplier scales all base projectile rates,
and zero is an exact decay-only gap. Results are emitted after every segment. Each step records the current multiplier
as `flux`, cumulative elapsed `t_s`, cumulative multiplier-weighted exposure `flux_weighted_time_s`, and physical
`fluence_n_cm2` (base total flux times weighted exposure). Scientific notation in a duration, such as `1e-8 s`, is
accepted as a number rather than mistaken for a unit suffix.

For charged projectiles, steps expose the generic `fluence_particles_cm2` and identify the projectile in the result,
ledger, certificate and prepared/mesh compatibility records. Neutron results retain their historical bytes and
`fluence_n_cm2` field when `projectile` is omitted.

## Build an activation library

The production builder is part of the `actinv` binary:

```bash
actinv build-library INPUT OUTPUT.npz \
  --format auto --projectile auto --groups fispact-709 \
  --temperature-K 293.6 --workers 4 --cache /data/actinv-cache
```

`INPUT` is one ENDF-6 evaluation or a directory. `--format` accepts `auto`, `tendl` or `eaf`; `--projectile` accepts
`auto`, `neutron`, `proton`, `deuteron` or `alpha`; `--groups` accepts `fispact-709`, `fispact-162` or a custom
boundary file. Neutron defaults are 709 groups and 293.6 K; charged defaults are 162 groups and 0 K. The adjacent
`<stem>_index.json` records source hashes, normalized options, group hash, builder fingerprint, target ledgers and the
final NPZ hash. A content-addressed cache is optional and revalidated before reuse.

When photons are requested (or `outputs` is omitted), `steps[].photon_source` contains:

- evaluated discrete line rates and per-nuclide evaluated/source yields;
- group photon rates, energy centroids and emitted powers, per gram and for `material.mass_g`;
- raw energy moments, explicit `E_EM` normalization factors and represented-power fraction;
- specific gamma constants in `Gy m2/(Bq s)` and `mGy m2/(GBq h)` when a response is supplied;
- `contact_gamma_air_dose_proxy_Gy_h`, response coverage, and explicit ungrouped/unrepresented power.

Use one-based result step numbers for transport export:

```bash
actinv export-openmc result.json 2 source.py
actinv export-mcnp result.json 2 source.sdef
```

Both exports use the photon-group centroids and total photons/s. The point at the origin is a placeholder, not a
spatial activation model. An export fails if custom boundaries omitted any source photons.

## Flux interchange (`actinv-flux-1`)

Transport spectra are canonicalized before activation. The format is newline-delimited JSON: exactly one `header`,
the declared number of ordered `cell` records, and one closing `footer`. A cell value is integrated neutron flux in
`n cm^-2 s^-1` for that energy group—not flux density per eV or lethargy. Every ID is unique and every ordinal begins
at zero and increases by one. The strict reader rejects blank, malformed, missing, duplicate, extra and trailing
records, invalid totals, nonfinite/negative values and inconsistent geometry.

```bash
actinv import-flux openmc statepoint.h5 flux.ndjson \
  --tally 7 --source-rate 1.0e15 --energy-floor-eV 1.0e-5 --window-rows 16384
actinv import-flux meshtal meshtal flux.ndjson \
  --tally 24 --source-rate 1.0e15 --energy-floor-eV 1.0e-5
actinv import-flux mctal mctal flux.ndjson \
  --tally 4 --source-rate 1.0e15 --energy-floor-eV 1.0e-5
actinv import-flux fispact fluxes flux.ndjson --groups descending-boundaries.json
```

The OpenMC and MCNP source rate is mandatory and positive. It converts a per-source-particle tally to physical flux;
FISPACT `fluxes` values are already absolute and are not rescaled. If the source grid starts at zero, an explicit
positive `--energy-floor-eV` below the next boundary is required and both the original zero and replacement are kept
in provenance. Every importer hashes and re-stats its input and publishes the canonical file by sibling temporary-file
rename only after the footer closes.

Supported subsets are deliberately narrow:

- OpenMC statepoint major 18, one selected `flux`/`total`/tracklength tally with exactly one 3D Cartesian regular or
  rectilinear `MeshFilter` and one `EnergyFilter`, in either order;
- MCNP traditional rectangular XYZ neutron FMESH `meshtal` column output with energy rows and optional checked totals;
- MCNP energy-binned F4:N `mctal` with one cell-ID F dimension, singleton remaining dimensions and optional checked
  total energy bins;
- standard FISPACT-II `fluxes`: N descending group values, first-wall loading, then its identifying title, against an
  explicitly supplied descending group-boundary JSON file.

Other scores, particles, estimators, filters, dimensions, mesh shapes, multipliers, responses, cumulative/time bins or
file variants produce a named error rather than a guessed interpretation.

## Independent mesh specification (`actinv-mesh-spec-1`)

Mesh mode replaces the ordinary `spectrum` with a mandatory canonical-file path and SHA-256. All cells receive the
same explicit library, decay data, optional fission-yield files, material, schedule, options and photon configuration
and solve independently.

```json
{
  "spec": "actinv-mesh-spec-1",
  "title": "iron activation mesh",
  "projectile": "neutron",
  "library": {
    "path": "/data/actinv_tendl2025_n_709g.npz",
    "sha256": "64 hexadecimal digits"
  },
  "decay": {"primary": "/data/endf-b-viii-0_decay.dat"},
  "material": {
    "mass_g": 1.0,
    "basis": "wt_percent",
    "composition": {"Fe": 100.0}
  },
  "flux": {
    "path": "flux.ndjson",
    "sha256": "64 hexadecimal digits"
  },
  "schedule": [
    {"dt": "5 min", "flux": 1.0},
    {"dt": "1 h", "flux": 0.0}
  ],
  "options": {
    "mode": "auto",
    "prune": "rate",
    "bmin_atoms_per_g": 1e-8,
    "temperature_K": 293.6
  },
  "chunk_cells": 64,
  "threads": 4
}
```

`chunk_cells` defaults to 64 and is bounded to 1–65,536. `threads` defaults to 1 and is bounded to 1–256. Execute it
with `actinv mesh mesh.json mesh-result.ndjson`. Immutable activation/decay/response data are verified, decompressed
and prepared once. Canonical cells are read a chunk at a time, restored to input order after Rayon execution, and
written as `actinv-mesh-result-1` header/cell/footer records.

Matching source/library boundaries use a bit-identical copy path. Other positive grids use FISPACT's default equal
flux per unit lethargy rule. Every cell result includes `source_total`, rebinned `destination_total`, `underflow`,
`overflow`, closure and method; energy outside the library is never folded into an edge group or renormalized away.
The ordinary run result is nested without per-cell timing. Only footer `wall_time_s` and `cells_per_s` vary with
scheduling; header and ordered cell bytes are deterministic across chunk and thread counts. The header certificate
binds the declared/computed canonical hash and its embedded transport/auxiliary hashes. Any cell or footer failure
names the failing premise and leaves no final result file.
