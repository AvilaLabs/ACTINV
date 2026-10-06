# Describe a problem

An `actinv-spec-1` JSON problem contains data references, a material, a spectrum, and a schedule. The CLI, desktop, and Python interface use the same contract. Start with `actinv new problem.json` and edit that complete example.

Unknown fields are errors. Numbers must be finite; misspelt options are rejected instead of being ignored. See [Problem specification](specification.md) for the full field reference.

## Material

This fragment describes one gram of an iron/chromium mixture:

```json
"material": {
  "mass_g": 1.0,
  "basis": "wt_percent",
  "composition": {"Fe": 90.0, "Cr": 10.0}
}
```

Composition keys may be natural elements such as `Fe` or explicit nuclides such as `Fe56` and `Ba137m1`. A natural element and one of its explicit isotopes cannot appear in the same composition. Use explicit nuclides for elements without tabulated natural abundances.

| Basis | Interpretation |
| --- | --- |
| `wt_percent` | Grams per 100 g; values are used as supplied and an off-100 total is reported |
| `atom_fraction` | Relative atom amounts, normalized to one gram |
| `atoms_per_g` | Literal atom density per gram |

Inventory, activity, and heat are reported per gram. `mass_g` scales total photon source quantities; it does not turn `heat_W_per_g` into total watts.

## Spectrum

`flux_per_group` holds group-integrated flux in particles cm⁻² s⁻¹. It is not a spectral density per eV. The ordinary neutron library uses `fispact-709` with 709 values. Standard proton, deuteron, and alpha libraries use `fispact-162` with 162 values and `temperature_K: 0`.

`descending: true` means the values are supplied from highest energy to lowest. When `total` is present, ACTINV scales the vector to that total while preserving its shape. A positive total with an all-zero vector is rejected.

Custom spectra require increasing energy boundaries, one more boundary than group values, and a compatible activation library. Without an explicit rebin option, the boundaries must match the library. A custom neutron grid can request `"rebin": "equal_lethargy"`; its positive ascending boundaries must stay wholly within the activation-library range. This maps group-integrated physical flux under the assumption that flux is uniform per unit lethargy inside each source group. Integral conservation does not validate reaction rates near cross-section thresholds or resonances. Rebinning is opt-in, supports no out-of-range loss or extrapolation, and cannot be combined with per-step spectrum overrides. Use [transport import](workflows.md#transport-flux-and-mesh-calculations) for supported transport tallies and review the source-rate normalization.

## Irradiation and cooling

This fragment irradiates for five minutes and then cools for one hour:

```json
"schedule": [
  {"dt": "5 min", "flux": 1.0},
  {"dt": "1 h", "flux": 0.0}
]
```

Each duration is incremental. The final time above is 3,900 seconds from the start, including the irradiation. `flux` multiplies the chosen spectrum; zero means cooling. Add separate cooling steps for the times you want reported.

Single-problem schedules can supply a per-step spectrum with the same group structure and count. Optional `feed` gives atoms s⁻¹ g⁻¹ and `removal` gives first-order rates in s⁻¹; these are described in the specification reference.

## Data paths

Catalog references such as `catalog:tendl-2025-neutron-709g` resolve against `ACTINV_DATA_DIR`, or `./actinv-data` by default. `actinv new` uses these references unless you supply `--data-dir`.

Literal relative paths in CLI problems use the **current working directory**. Desktop problems use **Input base**. Python `Problem.from_file` uses the problem's directory; plain Python mappings use the current directory. JSON does not expand `~` to your home directory.

Use `actinv doctor problem.json` to inspect setup, then validate and run as shown in [Your first calculation](quick-start.md).
