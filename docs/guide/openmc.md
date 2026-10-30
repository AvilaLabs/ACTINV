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

## Inside OpenMC's R2S workflow

You need OpenMC 0.15.3 (the version that has `openmc.deplete.R2SManager`), an `actinv` binary, and the standard data
(`actinv data fetch`). The adapter is one file,
[`contrib/openmc_r2s/actinv_openmc_r2s.py`](https://github.com/AvilaLabs/ACTINV/blob/master/contrib/openmc_r2s/actinv_openmc_r2s.py);
put it next to your script.

```python
import openmc
from actinv_openmc_r2s import ActinvR2SManager

openmc.config["chain_file"] = "chain.xml"   # OpenMC's step 1 still asks for one

r2s = ActinvR2SManager(
    model, mesh,
    library="actinv-data/v1.2.0/activation/tendl-2025-neutron-709g.npz",
    decay_primary="actinv-data/v1.2.0/decay/endf-b-viii-0_decay.dat",
    decay_fallback="actinv-data/v1.2.0/decay/jeff-3-3_decay.dat",
)
r2s.step1_neutron_transport()
r2s.step2_activation(timesteps=[3.15e7, 1.0e6], source_rates=[1.0e17, 0.0])
r2s.step3_photon_transport(time_indices=[2])
```

What changes compared with OpenMC's own manager:

- **Step 1** tallies the flux on the library's 709 groups, so ACTINV never rebins it, and tallies one throwaway
  reaction instead of every chain nuclide's. Pass `micro_kwargs` to override either.
- **Step 2** writes every region's flux and material, runs one `actinv mesh` for all of them, and hands OpenMC a
  results object that answers `get_decay_photon_energy()` for each region.
- **Step 3** and everything after it are OpenMC's own code.

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
