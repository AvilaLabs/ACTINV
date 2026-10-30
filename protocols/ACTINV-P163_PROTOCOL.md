# ACTINV-P163 — ACTINV against OpenMC's flux mode: a fair speed comparison on the steel-cube R2S demo

## Purpose

`results/openmc_r2s_demo.json` records ACTINV's R2S demo at 19 s end to end against 850 s for OpenMC's own
`openmc.deplete.R2SManager`, with a shutdown dose ratio of 1.019 ± 0.011. That figure must not be quoted as
"ACTINV versus OpenMC" as it stands. The OpenMC arm ran step 1 with its defaults: direct reaction-rate tallies for every
chain nuclide and every reaction, in one energy group (`get_microxs_and_flux` with no keyword arguments). Of its 829 s
in step 1, nearly all is that tally. OpenMC has a cheaper path, `reaction_rate_mode="flux"`: tally the multigroup flux
only, then collapse cross sections from it (`MicroXS.from_multigroup_flux`). The ACTINV arm already used something close
to a flux-only tally. The question this protocol answers:

> On the same model, the same transport settings and the same 2 CPU threads, how long do ACTINV and OpenMC 0.15.3 in
> flux mode each take to go from the model to the decay-photon source at 8, 64 and 512 mesh cells, and do they still
> agree on the shutdown dose?

The answer is a set of sentences the release notes may use, computed by a rule written down before any evidence run.
That set may be empty. A result in which ACTINV is slower, or not clearly faster, is a PASS of this protocol: the
protocol certifies the measurement, not the speed.

## What the old figure does and does not say

Recorded here so the new numbers are read against it:

- **Different tally.** The OpenMC arm tallied reaction rates directly (default); ACTINV's step 1 tallied the 709-group
  flux plus one throwaway (n,gamma) rate. Flux mode sits between them. Only the default arm's cost is known (829 s).
- **Different group count.** The OpenMC arm's default `energies` is one group; ACTINV's was 709. A direct tally in one
  group is exact per cell in OpenMC's own terms; flux mode collapses with a 709-group flux, which is an approximation
  that ACTINV also makes.
- **Different data.** OpenMC: ENDF/B-VIII.1 cross sections and the P32 chain. ACTINV: TENDL-2025 709-group activation
  data with ENDF/B-VIII.0 and JEFF-3.3 decay data. The recorded photon strengths differ by 38% (8.40e15 against
  6.09e15 photons/s) while the dose agrees to 2%; the dose ratio is therefore not evidence that the two inventories
  match, only that the dose does.
- **Thread counts were not recorded.** OpenMC's depletion pool defaults to all CPUs; the adapter's `actinv mesh` call
  defaults to one thread. This protocol sets two for both.
- **One run each**, with the ACTINV arm re-run alone after the first combined run was OOM-killed in an 8 GiB cgroup.
  No repetition, no variance.
- **Eight cells.** At 8 cells, a fixed cost (loading the 709-group library, about 4–5 s of ACTINV's 5 s step 2) is most of
  ACTINV's activation time. A ratio at 8 cells says little about scaling.
- **Shared steps.** Transport (step 1's `model.run`, step 3) is OpenMC's in both arms. What differs is the tally
  overhead and the activation engine, and the headline number must not hide that.

## Environment (recorded by the harness, checked by G1)

- **Hardware.** The owner's laptop: Intel Core i3-N305 (8 logical CPUs, 30 GiB RAM) as `lscpu` and `/proc/meminfo`
  report; Linux 7.0. The harness records CPU model, logical CPU count, memory, kernel, 1-minute load average at start
  and end, and the cgroup files it runs under.
- **Limits.** The harness runs inside `systemd-run --user --scope -p MemoryMax=6G -p MemorySwapMax=0 -p TasksMax=128
  -p CPUQuota=200%`, with `RAYON_NUM_THREADS=2`. OpenMC gets `OMP_NUM_THREADS=2` and a 2-process depletion pool. The
  harness reads `memory.max`, `memory.swap.max`, `pids.max` and `cpu.max` from its own cgroup and refuses to start
  unless they show at most 6 GiB, 0, at most 128 and at most 2 CPUs. Each run is started in its **own** transient scope
  with the same limits except memory: **6 GiB for ACTINV runs, 9 GiB for both OpenMC arms**. This is an exception to
  the repository's 6 GiB rule, which the owner allowed for this protocol ("If we have enough memory available then yeah
  go for it", 2026-10-29). Development run 1 showed why it is needed: OpenMC's flux mode reached the 6 GiB cgroup at
  8 cells while loading the chain nuclides' cross sections, and systemd stopped the whole scope, harness included. With
  a scope per run, a run that exceeds its cap is killed alone and recorded as censored. The operator checks
  `MemAvailable` before the evidence run and starts only with at least 11 GiB free (9 GiB cap plus the harness and
  headroom). Each completed run records its scope's limits and `memory.peak`, and `ru_maxrss` of the child and of its
  children. The verdict reports per arm and size the working set (the larger `ru_maxrss`) and `memory.peak`. The latter
  counts page cache from reading data files, which fills toward the cap when the files are larger than it, so it is
  an upper bound, not the memory a run needs.
- **OpenMC.** 0.15.3, the version the adapter (`contrib/openmc_r2s/actinv_openmc_r2s.py`) requires and the demo ran.
  On the owner's laptop it is the micromamba environment `~/micromamba/envs/openmc0153dd` (Python 3.13; the
  harness is run with `~/micromamba/envs/openmc0153dd/bin/python`). The `openmc016` environment (OpenMC 0.16.0) is not
  used: flux mode there returns one-group fluxes and changes the step 2 inputs. On another machine, install it with
  `micromamba create -n openmc0153dd -c conda-forge python=3.13 openmc=0.15.3 numpy` (or build tag `v0.15.3` from source if that build is unavailable) and confirm
  `python -c "import openmc; print(openmc.__version__)"` prints `0.15.3`; the default system Python has no OpenMC.
- **OpenMC cross sections.** The official OpenMC ENDF/B-VIII.1 HDF5 library, as OpenBNCT's profile fetches it:
  `https://anl.box.com/shared/static/6qr7jezzihkj9p9esl5jn19qgpujyjyz.xz`, 9,661,406,540 bytes, SHA-256
  `b7ad59cb4a3d76d8a291326093a98507f8d24b6e6af629116d3f7dc85f83c4cb`
  (`~/nuclear-data/endfb-viii.1-download/download.sh` does the fetch and checks). It is extracted to
  `~/nuclear-data/endfb-viii.1-hdf5/` with `tar -xJf endfb81.tar.xz --strip-components=1` (fetched, checked against
  the digest below and extracted on 2026-10-29). `OPENMC_CROSS_SECTIONS` points at its `cross_sections.xml`. The harness records the
  archive digest the operator passes with `--xs-archive-sha256` and the digest of `cross_sections.xml` itself.
- **Depletion chain.** `~/nuclear-data/p32-work/chain/depletion/chain.xml` (3,820 nuclides, the P32 chain the demo used).
- **ACTINV.** `target/release/actinv` built from the checkout at the evidence commit; library
  `actinv-data/v1.1.0/activation/tendl-2025-neutron-709g.npz`; decay data `endf-b-viii-0_decay.dat` with fallback
  `jeff-3-3_decay.dat`. Whether the demo used this library or `tendl-2025-patched-neutron-709g.npz` is not recorded
  (`examples/openmc_pipeline` names the patched one, the demo README the other); this protocol fixes the unpatched
  one and states it.
- **Pins.** The checker pins, and G1 requires the record to match:

  | input | SHA-256 |
  |---|---|
  | `chain.xml` | `d3b829467cfdda9c624aaeb5709825df378b908ecc63c3f984ade51f8d0444a0` |
  | `tendl-2025-neutron-709g.npz` | `ec4c72bf598dc8ad3d533d9cfafdcf493e2d1f949a3e4db6251495659b68cc44` |
  | `endf-b-viii-0_decay.dat` | `6f04cf009086c179021f243a58dadc2d5bb078de5ba39c4fe46ccad77d228ddb` |
  | `jeff-3-3_decay.dat` | `850b8b7f85f8d88b6ad826c4cd341aaaffabd525c8ecf3c588a0ad437bf5d123` |
  | ENDF/B-VIII.1 archive | `b7ad59cb4a3d76d8a291326093a98507f8d24b6e6af629116d3f7dc85f83c4cb` |
  | `cross_sections.xml` | `b8ffb6104a946bf951dc0f3b9a1f57a60f7f57c4b308cd66df4fd3dd4bc557ce` |

  The harness, this protocol, `contrib/openmc_r2s/demo_steel_cube.py`, `contrib/openmc_r2s/actinv_openmc_r2s.py` and
  `crates/actinv-data/data/fispact_709_groups.json` are checked against the checkout. The record also holds the
  `actinv` binary's digest and the git commit; tracked files under `controls`, `contrib`, `protocols` and `crates` must
  be unmodified when the harness starts.

## The problem

`contrib/openmc_r2s/demo_steel_cube.py`, unmodified: a 20 cm cube of SS316-type steel, a 14.1 MeV isotropic point source
at the centre, 1e17 n/s for 365.25 days then 1e6 s of cooling (`timesteps` = [3.15576e7, 1e6], `source_rates` = [1e17, 0]),
the photon dose rate tallied in an air shell between 30 and 31 cm (AP dose coefficients), neutron and photon transport
each 20,000 particles × 5 batches. The harness changes three things, all recorded: the mesh dimension (below), the
random seed (`1000 + repetition` for neutrons, `1500 + repetition` for photons, identical across arms), and the
thread settings. Mesh cells span the cube; each holds only steel, so activation regions equal mesh cells.

| mesh cells | dimension | OpenMC regions |
|---|---|---|
| 8 | 2×2×2 | 8 (the recorded demo size) |
| 64 | 4×4×4 | 64 |
| 512 | 8×8×8 | 512 |

Particle counts are not scaled with cells. A finer mesh at fixed histories makes each cell's flux noisier; the quantity
checked is the integral dose, whose statistical error does not grow with the mesh.

## Arms

All three use `openmc.deplete.R2SManager`'s three steps. Only these differ:

- **`actinv`.** `ActinvR2SManager`, unmodified, `threads=2`. Step 1 is OpenMC's with the adapter's slim tally: flux on the
  709 library groups plus one (n,gamma) rate for one throwaway nuclide. Step 2 is one `actinv mesh` call for all regions.
  Step 3 is OpenMC's.
- **`openmc_flux`.** `R2SManager`, `step1_neutron_transport(micro_kwargs={"reaction_rate_mode": "flux", "energies":
  <the 709 library boundaries>})` and nothing else. This tallies the 709-group flux per region, then for every chain
  nuclide that has cross-section data collapses every chain reaction to one group with
  `MicroXS.from_multigroup_flux`. Step 2 is `IndependentOperator` with `PredictorIntegrator`, source-rate normalised, over
  the whole chain. Step 3 is OpenMC's. This is OpenMC's cheapest documented route to the same inputs and uses the same
  group structure as ACTINV. It is registered as "OpenMC's fastest equivalent path" for this protocol; if a faster
  supported route is found later (for example `reaction_rate_opts`, OpenMC 0.16, which tallies a subset directly), that
  is a new protocol.
- **`openmc_default`.** `R2SManager` with no keyword arguments: the arm recorded in `results/openmc_r2s_demo.json`. It
  is the reference point for G4 and for the one sentence about the old figure. It runs once, at 8 cells only; larger
  sizes would multiply a tally that already needed most of the memory in an 8 GiB cgroup.

## Repetitions and run limits

| arm | 8 cells | 64 cells | 512 cells | wall-clock limit per run |
|---|---|---|---|---|
| `actinv` | 3 | 3 | 3 | 900 s |
| `openmc_flux` | 3 | 2 | 1 | 3600 s |
| `openmc_default` | 1 | – | – | 3000 s |

Repetition r uses seed 1000 + r in every arm (OpenMC's per-particle seeding makes the histories independent of thread
count), and the arms interleave within a repetition so slow drift hits them all. Each run is a fresh child process in
its own session. On expiry its process group gets SIGTERM, ten seconds, then SIGKILL, and is reaped. A run that times
out or is killed by the cgroup is **censored**: it needed at least its recorded wall time, and is used only as that
lower bound. A crash is not a censoring.

**Skip rule.** An `openmc_flux` run at 64 or 512 cells is attempted only if the median steps 1+2 time at the previous
size (8 or 64), multiplied by the cell ratio (8×), is at most 3600 s. If not, it is recorded as skipped with the
projection. A skipped size has no OpenMC-flux measurement, produces no claim, and the projection is never quoted. The
checker recomputes the rule from the attempts.

**Bound on cost.** The worst case is every run reaching its limit: 9 × 900 + 6 × 3600 + 3000 = 32,700 s, about 9.1 h.
Estimates are below under "Expected cost".

## Timing

Each run reports three times from `time.monotonic()` reads taken directly around the manager's steps:

- `step1` = `step1_neutron_transport(...)`, including `material_volumes`, the OpenMC transport run, tally reading, and
  the flux-mode cross-section collapse. In the ACTINV arm the same call includes only the slim tally.
- `step2` = `step2_activation(...)`. For ACTINV this is the whole adapter step, including writing the flux and spec files,
  the `actinv mesh` subprocess (so its library load, solve and result write) and reading the result back. For OpenMC it
  is the `IndependentOperator` and `PredictorIntegrator` run, including its HDF5 output.
- `step3` = `step3_photon_transport(...)` at time index 2. Same code, same settings in both arms.

Not inside any step: interpreter start, `import openmc`, building the model, `openmc.lib` first-use and
cross-section-library load (these happen inside step 1 for both arms, once per run, and are counted there), and
tearing down. The child's total wall time is recorded; it may exceed the step sum by at most 180 s (G5).

Metrics, all from these steps:

- **H1 = step1 + step2.** From a model to a photon source. This is the headline metric: it includes transport, because
  that is what a user waits for, and it is symmetric (both arms run the same transport settings; ACTINV does not skip
  or speed up transport).
- **Activation-attributable** = for OpenMC flux, step1 + step2 minus the median ACTINV step 1 at the same size (which
  is, to within a one-nuclide tally, bare transport + `material_volumes`); for ACTINV, step 2. This isolates what
  differs. It is reported with H1 and never quoted alone.
- End-to-end (H1 + step3) is reported for comparison with the old 19 s / 850 s figure. Step 3 is the same in both arms
  and dilutes the ratio; no claim uses it.
- Page cache is not dropped (no privilege); the first repetition at each size is not discarded. All repetitions are
  reported; the claim rule uses the worst case over them.

## Accuracy: speed is not bought by a wrong answer

For every repetition where ACTINV and OpenMC flux both completed, per size:

1. The dose ratio, ACTINV over OpenMC flux (the dose tally of the manager's step 3), is in [0.95, 1.05]. The demo's ratio
   against the default arm was 1.019 ± 0.011 (1σ Monte Carlo, one run); with equal seeds the transport noise is shared
   and the band is wide enough to hold the data differences described above and narrow enough to catch a broken
   engine. It is a pre-registered engineering bound, not a statistical test.
2. At 8 cells, the dose of each of OpenMC flux and ACTINV is within [0.95, 1.05] of the `openmc_default` arm's. If that
   run was censored, each is within 7% of the recorded demo dose rate, 1019.79 Sv/h.
3. The photon strengths (photons/s) are within a factor of two. This is a sanity bound only: the recorded demo differed by
   a factor of 1.38 with the dose in agreement, and the protocol does not claim to explain that.
4. The summed neutron track length per source particle (all regions, all groups) agrees within 2%. With equal seeds the
   two arms see the same histories; a larger difference means the tallies are not comparable.

A size where any of these fails has no speed claim.

## Decision rule

The checker computes, per size with completed ACTINV runs and OpenMC-flux runs (censored runs count as their wall time):

- `L` = (smallest OpenMC-flux H1) / (largest ACTINV H1), the worst case over repetitions;
- `R` = (median OpenMC-flux H1) / (median ACTINV H1).

Outcome:

- `L ≥ 1` and the accuracy checks pass: **faster**. The quotable multiplier is `L` rounded down (to one decimal below
  10, to an integer above). The release notes may say "ACTINV is at least *N*x faster than OpenMC 0.15.3 flux mode
  from model to photon source (steps 1 and 2) at *M* mesh cells", and must give the mode and *M*. If an OpenMC run was
  censored, the sentence says so.
- `L < 1 ≤ R`: **overlap**. No speed claim at that size.
- `R < 1`: **slower**. No "faster than OpenMC" sentence. The release notes may say ACTINV is slower than OpenMC flux
  mode at *M* cells, and by how much, or say nothing.
- A general sentence ("at every measured size") is allowed only if every measured size is **faster**, and then uses the
  smallest quotable multiplier.
- Only measured sizes can appear. No extrapolation to other meshes, models, materials or machines; the sentences name
  this laptop's class of hardware and OpenMC 0.15.3.
- The 19 s / 850 s figure, or any figure from the `openmc_default` arm, may appear only labelled "against OpenMC's default
  per-nuclide tally mode", with 8 cells and one run stated; the multiplier is the default run's H1 over the largest ACTINV
  H1 at 8 cells, rounded down. It is never called OpenMC's speed.
- Scope of every sentence: *same model, same transport settings, same 2 threads; ENDF/B-VIII.1 for OpenMC, TENDL-2025
  for ACTINV; the 20 cm steel cube at 14.1 MeV*. A data difference is not a speed difference, but the sentence says
  which data each tool used.
- Where any measured size is **overlap** or **slower**, no unlabelled "faster than OpenMC" sentence is allowed anywhere in
  that release, including the old figure.

`controls/check_p163.py --release-notes FILE` scans the release notes: any line mentioning OpenMC with a speed comparison
must name a mode and a measured cell count, and every multiplier must be within the allowed value for that mode and
size.

## Expected cost (estimates, not measurements)

Anchored on the recorded demo (OpenMC default: step 1 829 s, step 2 11 s, step 3 10 s; ACTINV: 9.2 s, 5.0 s, 4.9 s at
8 cells). Flux-mode collapse cost has been seen only in development run 2 at 8 cells, and ACTINV's scaling to 512 cells
not at all; the ranges are guesses bracketed by the run limits.

| size | `actinv` per run | `openmc_flux` per run | `openmc_default` |
|---|---|---|---|
| 8 | 0.3–1.5 min (development run 2: 19 s) | 2–15 min (about 9 s of transport, then a one-off load of every chain nuclide's data, then per-cell collapse of ~500 nuclides × ~9 reactions, then depletion; development run 2: 2 min) | 14–15 min |
| 64 | 1–3 min | 5–60 min, 2 runs | not run |
| 512 | 3–10 min | 1 run only if the 64-cell median projects ≤ 3600 s | not run |

Expected campaign: about 2–4 h of wall time if 512 cells is skipped for OpenMC, bounded by 9.1 h. The ACTINV runs total
roughly 30 min. The `openmc_default` arm may be OOM-killed at 9 GiB (the recorded demo hit an 8 GiB cgroup when the
arms ran together); a kill is recorded as a censoring and G4 falls back to the recorded demo dose.

## Development runs

`controls/p163_harness.py --smoke` runs 8 cells, one repetition, ACTINV and OpenMC flux only, and writes the same record
with `smoke: true`; G1 refuses such a record. It exists to shake out the harness before evidence. Development runs
that find a harness bug are repaired by amendment before any evidence run. Records of development runs go under
`results/failures/p163/`. Two development runs were made before registration: `smoke1.json` (one scope for everything;
the OpenMC flux run hit 6 GiB and systemd stopped the harness, so the record ends after the ACTINV run) and
`smoke2.json` (a scope per run, as above; both runs completed at 8 cells: OpenMC flux mode had a working set of 6.4 GiB
`ru_maxrss` and 8.0 GiB of `memory.peak` with page cache, which set its 9 GiB cap; ACTINV 1.0 GiB and 2.0 GiB; the checker
found that the harness recorded the `micro_kwargs` keys after `R2SManager` had added its own file paths to that dict,
so the harness now records the keys it passes) and `smoke3.json` (the same after that repair; G3 to G6 of the checker passed on it, and OpenMC's `memory.peak`
reached the 9 GiB cap through page cache while its working set stayed at 6.4 GiB). The timings of
development runs are not results and are never quoted.

## Gates

- **G0.** This protocol's SHA-256 (and any amendment's) is in `protocols/protocol_hash.txt`.
- **G1.** Environment and inputs: not a smoke run; OpenMC 0.15.3; the harness's cgroup limits as above; every run
  recorded with its arm's memory cap, and every completed run's own scope showing that cap, no swap, at most 128 tasks
  and 2 CPUs, with a recorded memory peak; two threads; clean tracked inputs; every pinned external input and the
  in-repo inputs match.
- **G2.** The plan was carried out and nothing hidden: every (arm, size, repetition) appears exactly once as an attempt
  or as a skip the rule requires; every ACTINV run succeeded; OpenMC runs are `ok`, `timeout` or `killed` only.
- **G3.** Configuration fidelity: same model, seeds, particles, threads, schedule and photon settings everywhere; the
  flux arm is exactly `reaction_rate_mode="flux"` with the 709 library groups over at least 300 chain nuclides; the
  default arm used OpenMC's defaults; the ACTINV arm used the adapter's slim step 1; the arms collapse the same nuclide
  sets where both ran.
- **G4.** Accuracy bounds above hold for every completed pair and against the default arm.
- **G5.** Timing validity: positive steps, wall time within 180 s of the step sum, 1-minute load average at start ≤ 3.
- **G6.** The decision rule was applied: every size has an outcome; the allowed sentences are written to
  `results/p163_verdict.json`; and, when a release-notes file is given, every speed comparison there is within them.
- **G7.** CI green on the pushed evidence commit.

## Verdict

P163-PASS if G0–G7 hold. One repair round, registered as an amendment first. The checker never reports the speed as a
pass or fail; it reports the claims the data allow.

## Not claimed

- That ACTINV is faster than OpenMC in general. The result is for one geometry, one material, one spectrum, one
  irradiation history, one laptop and two threads, against OpenMC 0.15.3 flux mode.
- That flux mode is OpenMC's absolute fastest configuration for activation. OpenMC 0.16's `reaction_rate_opts`, cell-based
  R2S, fewer chain nuclides, or a smaller chain may be faster; none was tried.
- That the two tools' inventories agree. They use different data; only the dose is compared, and the photon strengths
  are held to a factor of two.
- Anything about accuracy beyond the bounds above; this is not a validation study.
- Reproducibility, provenance or audit trails as a selling point. The pins exist so the timings are attached to known
  inputs, as internal QA.
