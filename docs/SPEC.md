# ACTINV problem specification (`actinv-spec-1`)

One JSON document drives the CLI, Python API and validation harness. Unknown fields, non-finite numbers and invalid
hashes are errors; paths are literal filesystem paths (shell `~` expansion is not performed inside JSON).

```json
{
  "spec": "actinv-spec-1",
  "title": "FNS Fe, 5-minute irradiation",
  "library": {
    "path": "/data/actinv_tendl2023_709g.npz"
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
| `library.path` | ACTINV `.npz` activation library; the adjacent `<stem>_index.json` is also required. |
| `library.sha256` | Optional declared hash. ACTINV always computes the library hash and fails if a declaration differs. The index's recorded library hash is checked too. |
| `decay.primary` | ENDF-6 radioactive-decay sublibrary. |
| `decay.fallback` | Optional second decay sublibrary; records absent from the primary are taken from it. |
| `material.composition` | Natural element symbols and nonnegative values. Explicit isotope keys are not implemented yet. |
| `spectrum.flux_per_group` | Group-integrated fluxes. `descending: true` reverses the supplied order before use. |
| `schedule` | At least one duration/flux-multiplier pair. Accepted duration units: seconds, minutes, hours, days and years. |

The certificate records computed SHA-256 values for the activation library, its index, primary/fallback decay data and
photon response. A declaration is a constraint, not a value copied into the certificate.

## Material bases

`material.mass_g` defaults to 1 g. Inventories remain per gram; the mass scales the total photon rates and powers.

- `wt_percent` (default): each value is grams per 100 g. Values are used as stated rather than silently normalized;
  a total other than 100 is ledgered. Photon-response mixing normalizes them to mass fractions.
- `atom_fraction`: values are arbitrary elemental atom ratios. Natural isotopes are expanded and the mixture is
  normalized to one gram using the abundance-weighted elemental masses.
- `atoms_per_g`: each value is an elemental atom density per gram and is expanded by natural isotopic abundance.

## Neutron spectrum

`fispact-709` requires exactly 709 values. `custom` requires one more strictly increasing boundary than flux values;
those boundaries must match the boundaries stored in the activation library to 1e-12 relative. `total`, when present,
rescales the group values while preserving shape. The library temperature and `options.temperature_K` must agree.

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

`mode` is `auto`, `trace`, or `coupled`; `auto` selects trace below a recorded burn-up fraction of 1e-6. `prune` is
`rate`, `reach`, or `none`. The `outputs` list controls the optional pathway and photon/dose calculations; the core
inventory/activity/heat diagnostics remain in each result step.

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
