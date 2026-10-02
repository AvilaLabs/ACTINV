# ACTINV as the photon-source input of nucleide's/PyNE's ALARA-based R2S

`nucleide` (nukehub-dev/nucleide, BSD-2) and PyNE build rigorous two-step (R2S) shutdown-dose
workflows around ALARA: they write the ALARA inputs and read ALARA's plain-text photon-source file,
and leave the activation solve to ALARA.

`actinv export-source alara` (P102) writes ACTINV's banded `actinv-r2s-source-1` document as ALARA
`.photonSrc` files — one per cell, `TOTAL\t<time>\t<densities…>` — so `nucleide`'s and PyNE's
existing readers can take ACTINV's per-group spectra where they would read ALARA's, with no change on
their side. This is the same zero-compute adapter lane as `contrib/openmc_r2s` (P57): no solver
path is invoked here either.

## Requirements

- `nucleide` 0.16.0, pinned (`pip install nucleide==0.16.0`; abi3 wheels, Python >= 3.10). The
  package is pre-alpha — later versions are a re-run, not a claim.
- An `actinv` binary that supports `export-source alara` (`cargo build --release -p actinv-cli`, or
  on `PATH`).
- An `actinv-r2s-source-1` document (`actinv export-r2s`), with every cell carrying the same
  `step_t_s` and a positive `volume_cm3`.

## Minimal usage

```
python3 contrib/nucleide_r2s/demo.py [R2S_SOURCE.ndjson] [--shutdown-t-s T] [--out DIR]
```

Defaults to the repository's corpus document (`results/p52_r2s_source.ndjson`) and
`--shutdown-t-s 300`, the corpus's end of irradiation (it irradiates for 300 s, then cools
1 d, 30 d and 1 y, so the exported step is 34214400 s after shutdown). The demo:

1. Runs `actinv export-source alara R2S_SOURCE.ndjson OUT --shutdown-t-s T`.
2. Loads every emitted `.photonSrc` file with nucleide's own ALARA photon-source reader
   (`nucleide.r2s.photon_group_sums`, `nucleide.alara.alara_photon_total_strength`).
3. Builds nucleide zone photon sources and tags voxels through nucleide's own `r2s` API
   (`nucleide.r2s.tag_zone_strength`) — one voxel per zone here, since ACTINV's mesh cells are
   already independent volume elements.
4. Prints a conservation table: each cell's `Sigma_g density * volume_cm3` against the cell's
   declared `photons_s`.

## Demo result

On the repository's 8-cell corpus (`results/p52_r2s_source.ndjson`, 220.303 total photons/s,
115-entry group grid), every cell's reconstructed total matches its declared `photons_s` to
**0.00e+00 relative error** (density and volume round-trip exactly in this case), and
`nucleide.r2s.tag_zone_strength`'s total matches the declared total. This is a mechanics
demonstration — it shows the export is read correctly by nucleide's own code, not a physics
validation of the activation result itself (that is the subject of the `export-r2s` protocols,
P43/P44/P52).

## What differs from a real ALARA-driven R2S run

- **No per-nuclide spectra.** `actinv-r2s-source-1` carries per-cell photon totals, not per-nuclide
  group spectra, so only `TOTAL` rows are emitted. A real ALARA output also emits per-nuclide rows;
  this is a documented limit of the interchange document, not of the adapter.
- **Centroid grid, not energy bounds.** The group grid is the union of `centroid_eV` values, sorted
  ascending. Writing the matching ALARA deck `photon_source` energy-bin bounds (for an actual ALARA
  run, if one is wanted alongside this export) is the user's step — read them from
  `actinv-alara-index.json`'s `group_centroids_eV`.
- **One voxel per zone.** The demo's `tag_zone_strength` call uses `zone_of_voxel = range(n)`
  because ACTINV's mesh cells are already independent zones; a real R2S mesh with multiple voxels
  per activation zone would pass a non-trivial mapping and (optionally) `split=True`.

## Testing

`test_nucleide_r2s.py` runs the demo against the corpus document and checks the printed
conservation table. It needs `nucleide` importable and an `actinv` binary built; it skips cleanly
(not failing) if either is absent:

```
python3 -m unittest contrib/nucleide_r2s/test_nucleide_r2s -v
```
