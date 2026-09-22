# OpenMC → ACTINV pipeline

A complete activation workflow in ~5 minutes: Monte Carlo transport in OpenMC,
groupwise flux into ACTINV, decay heat and a decay-photon source back out as an
OpenMC module. This is the pattern for a real R2S-style analysis — swap the toy
geometry for your model and the rest is unchanged.

```
OpenMC transport ──► statepoint.N.h5 ──► actinv import-flux ──► flux.ndjson
        │                                                     │
        │                                                     ▼
        │                                        actinv mesh (activation solve)
        │                                                     │
        │                       ┌─────────────────────────────┤
        ▼                       ▼                             ▼
   decay-heat table      activity inventory          photon_source.py
   (summarize.py)        per cooling step            (IndependentSource for a
                                                      downstream OpenMC run)
```

## Prerequisites

- `actinv` installed (`pip install actinv`) and the data bundle fetched once:
  `actinv data fetch`
- Python with `openmc >= 0.14` and `numpy`
- An OpenMC HDF5 cross-section library; set `OPENMC_CROSS_SECTIONS` to its
  `cross_sections.xml` (e.g. an ENDF/B-VIII.1 HDF5 distribution)

## Run

```bash
./run.sh
```

Useful overrides: `ACTINV`, `PYTHON`, `ACTINV_LIBRARY` (path to the activation
.npz — defaults to the fetched `tendl-2025-patched-neutron` bundle),
`ACTINV_DATA_DIR`, `SOURCE_RATE` (default 1e15 n/s), `TALLY_ID`.

## What it does

1. **`model.py`** — a 14.1 MeV isotropic point source irradiating a 1 cm iron
   cube at 10 cm standoff, in vacuum. The tally is the exact subset
   `import-flux` accepts: a `MeshFilter` (one cell fitted to the cube) plus an
   `EnergyFilter` whose bins are the activation library's own 709 group edges —
   read directly from the ACTINV `.npz`, so the two tools cannot disagree on
   group structure.
2. **`actinv import-flux openmc`** — converts the statepoint tally into
   ACTINV's canonical flux format (`flux.ndjson`), scaled to the absolute
   source rate.
3. **`actinv mesh`** — solves activation for the mesh cell over a 5-minute
   irradiation followed by cooling steps. The spec is emitted by `run.sh` with
   the flux file's SHA-256 pinned.
4. **`summarize.py`** — decay heat, total activity and dominant nuclides per
   step. Expected physics: Mn-56 dominates early (the classic fast-neutron iron
   signature), Fe-55/Cr-51/Mn-54 at late times.
5. **`actinv export-openmc-mesh`** — writes `photon_source.py`, a Python module
   defining `sources` (a list of `openmc.IndependentSource` with absolute
   photon/s rates) for importing into a downstream photon-transport dose run.

## Verified output (1e6 particles, ENDF/B-VIII.1)

```
       t [s]   heat [W/g] activity [Bq/g]  top nuclides
         300     9.55e-06       2.78e+07  Mn56 1.86e+07, Mn57 8.02e+06, ...
        4200     5.63e-06      1.394e+07  Mn56 1.39e+07, Fe55 5.67e+03, ...
   6.054e+05     1.892e-10           9914  Fe55 5.64e+03, Cr51 3.04e+03, ...
```

## Notes and limits

- The mesh cell is fitted to the cube so the tracklength tally averages over
  iron only — a sphere in a bigger cell would dilute the flux by the void
  fraction.
- This is a single-material 0-D activation solve: ACTINV performs no transport
  and no spatial coupling; spatial R2S uses `actinv mesh` over a real mesh with
  per-cell spectra (`docs/SPEC.md`, "Independent mesh specification").
- `import-flux` is deliberately strict — a tally with different scores,
  filters, or estimators fails with a named error rather than a guessed
  interpretation.
