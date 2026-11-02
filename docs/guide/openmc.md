# Use ACTINV with OpenMC

OpenMC transports the neutrons and the decay photons; ACTINV activates the material in between. There are two ways to
connect them:

- **Inside OpenMC's shutdown-dose workflow.** A small adapter replaces the activation step of OpenMC's own
  `R2SManager`. Your OpenMC script stays an OpenMC script; steps 1 and 3 are OpenMC's, unmodified.
- **Through files.** Import the flux tally from an OpenMC statepoint, run `actinv mesh`, and write the decay-photon
  source back as OpenMC input. No Python coupling, and the same commands read MCNP tallies.

## How it compares

On the steel-cube shutdown-dose problem, with both codes on 2 threads, OpenMC's flux mode on the same 709 groups, and
three ACTINV runs and one to three OpenMC runs per size (protocol P163):

| Mesh cells | ACTINV, model to photon source | OpenMC 0.15.3 flux mode | Worst-case ratio |
|---|---|---|---|
| 8 | 10.7–10.8 s | 99.5–102.0 s | 9.2 |
| 64 | 16.4–16.8 s | 138.4–139.2 s | 8.2 |
| 512 | 64.5–66.4 s | 435.1 s | 6.5 |

ACTINV is at least 6.5x faster than OpenMC 0.15.3 flux mode at every measured size (8, 64, 512 mesh cells).

Against OpenMC 0.15.3 in its default tally mode (per-nuclide reaction-rate tallies, one energy group), one run at 8 mesh cells, ACTINV's steps 1 and 2 are at least 42x faster; this is not OpenMC's fastest mode.

The two codes used different data: OpenMC ENDF/B-VIII.1 cross sections with its depletion chain, ACTINV TENDL-2025.
Their shutdown dose rates agreed within 2.1% at every size. ACTINV's working set was about 1 GiB; OpenMC's flux mode
needed about 6.4 GiB. Both times include OpenMC's neutron transport, which the two arms share. The record is
`results/p163_speed.json`.

On accuracy, the competitive benchmark compares the two activation paths on the same ENDF/B-VIII.1 data against 21
FNS decay-heat experiments (424 measured points): median |ln C/E| 0.131 for ACTINV and 0.181 for OpenMC's depletion.
See [Understand the competitive benchmark](benchmarks.md#openmc-a-common-endfb-viii1-comparison).

On all 132 FNS experiments (protocol P172): against 2360 measured decay-heat points from 132 JAEA FNS experiments,
ACTINV with its shipped TENDL-2025 data has a median |ln C/E| of 0.139; OpenMC 0.15.3 depletion with its published
ENDF/B-VIII.1 fast-spectrum depletion chain (chain_endfb81_fast.xml) and ENDF/B-VIII.1 has 0.316. With its
R-matrix-limited capture repaired and otherwise the same ENDF/B-VIII.1 cross sections as OpenMC, ACTINV has a median
|ln C/E| of 0.234 on 2360 measured decay-heat points from 132 JAEA FNS experiments (P179). See
[All 132 FNS experiments](benchmarks.md#all-132-fns-experiments) for OpenMC's other chains.

Without transport, on the same 132 problems (protocol P180): on the 132 JAEA FNS decay-heat problems of P172, each
solved in its own process on the same two CPU cores with the same ENDF/B-VIII.1 cross sections, ACTINV 1.7.1 took a
median of 0.61 s per problem from start to finish and OpenMC 0.15.3 depletion 18.1 s; the median per-problem ratio
was 29.8. Counting only OpenMC's depletion step, without its start-up and cross-section collapse, the median
per-problem ratio was 9. See [Per-problem time](benchmarks.md#per-problem-time-on-the-fns-problems).

On a mixed-material mesh (a tungsten sphere in CuCrZr, water and steel, protocol P181 Amendment A, one run per size
on an Intel Core i3-N305). Inside the same 10
GiB memory limit and 4-hour limit per run, ACTINV (with an ENDF/B-VIII.1 activation library) completed meshes up to
32,768 elements (36,208 regions) and OpenMC 0.15.3 in flux mode up to 4,096 elements (5,120 regions); at 13,824
elements OpenMC was stopped by its memory limit. On a mixed-material model (a tungsten sphere in CuCrZr, water and
steel, 14.1 MeV source) meshed into 4,096 elements (5,120 activation regions), with the same transport and two CPU
threads, ACTINV 1.7.1 with an ENDF/B-VIII.1 activation library took 155 s from model to photon source and OpenMC
0.15.3 in flux mode 3,432 s, with peak working memory of 1.4 GiB and 7.2 GiB (one run each). See
[Larger meshes with several materials](benchmarks.md#larger-meshes-with-several-materials) for every size and the
conditions, including that ACTINV's runs were redone on ENDF/B-VIII.1 after a first run on TENDL-2025 was seen.

## Inside OpenMC's R2S workflow

You need OpenMC 0.15.3 or newer (the versions that have `openmc.deplete.R2SManager`), the `actinv` Python
package (`pip install actinv`), and the standard data (`actinv data fetch`). The adapter is the module
`actinv.openmc`; importing `actinv` alone never imports OpenMC, and `actinv.openmc` stops with a clear message if
OpenMC is missing or older than 0.15.3. It is tested on OpenMC 0.15.3 and 0.16.0, and weekly on OpenMC's
development branch.

```python
import openmc
from actinv.openmc import ActinvR2SManager

r2s = ActinvR2SManager(model, mesh)
r2s.run(timesteps=[3.15e7, 1.0e6], source_rates=[1.0e17, 0.0], photon_time_indices=[2])
```

`run` calls the same steps as OpenMC's own manager: three in OpenMC 0.15.3 (the third is
`step3_photon_transport`), four from 0.16 (`step3_photon_source` builds the sources, `step4_photon_transport` runs
them). A cell-based run also needs `bounding_boxes={cell.id: box}`, as with OpenMC's manager.

With no further arguments the manager uses the default data bundle you installed (under `./actinv-data` or
`$ACTINV_DATA_DIR`; it tells you to run `actinv data fetch` if the files are missing) and the `actinv` command that
came with the package. To use other files or another build, pass `library=`, `decay_primary=`, `decay_fallback=` or
`actinv_bin=`.

What changes compared with OpenMC's own manager:

- **Step 1** tallies the flux on the library's 709 groups, so ACTINV never rebins it, and needs no depletion chain:
  it is OpenMC's step 1 with only the flux tallied, and it writes the same results and files. Pass
  `micro_kwargs={"chain_file": "chain.xml"}` (or `nuclides`/`reactions`) to run OpenMC's own cross-section step
  instead.
- **Step 2** writes every region's flux and material, runs one `actinv mesh` for all of them, and hands OpenMC a
  results object that answers `get_decay_photon_energy()` for each region.
  It also saves the region materials as `materials.xml`, so `r2s.load_results(path)` reopens a finished run
  without solving again.
- **The photon sources** are ACTINV's decay-photon spectrum for each region, with strength in photons/s. From
  OpenMC 0.16 the manager builds them in place of OpenMC's `DecaySpectrum` sources, which would need nuclide
  densities and a depletion chain; the regions, boxes and domain constraints are OpenMC's. Scoring by parent nuclide
  (`by_parent_nuclide=True`) is not available, since the spectrum is per region.
- **Photon transport** and everything after it are OpenMC's own code.

`contrib/openmc_r2s/demo_steel_cube.py` runs the steel-cube problem with both managers and compares the dose. The
adapter's [README](https://github.com/AvilaLabs/ACTINV/blob/master/contrib/openmc_r2s/README.md) lists its limits:
neutron activation only, and one flux spectrum per schedule, scaled by each step's source rate.

## Through files

```text
actinv import-flux openmc statepoint.h5 flux.ndjson --tally 1 --source-rate 1e17
actinv mesh mesh.json result.ndjson
actinv export-openmc-mesh result.ndjson 2 source.py
```

The tally must be a flux tally on one regular or rectilinear mesh with an energy filter. `--source-rate` turns the per-source-particle tally into a flux, in
source particles per second. The mesh problem names the flux file, the materials and the schedule; see
[Advanced workflows](workflows.md#transport-flux-and-mesh-calculations) and the
[mesh specification](specification.md#independent-mesh-specification-actinv-mesh-spec-1).

`export-openmc-mesh` writes one OpenMC source per cell at one result step, counted from one (here the second). To carry the activation
uncertainty into the photon run, write a banded source with `actinv export-r2s` and convert it with
`actinv export-source openmc`; see [Export photon sources](cli.md#export-photon-sources).
