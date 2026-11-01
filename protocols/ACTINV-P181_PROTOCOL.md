# ACTINV-P181 — ACTINV and OpenMC's flux mode on a mixed-material mesh as the element count rises

## Purpose

P163 compared ACTINV with OpenMC 0.15.3's flux mode on a steel cube at 8, 64 and 512 mesh cells, each cell holding one
material. A real R2S mesh crosses material boundaries: OpenMC splits each element by material
(`mesh_material_volumes`), so the activation regions number the elements times the materials in them, and fine meshes
are coarsened in practice because the activation step and its memory grow with them.
`docs/maintainers/WORKFLOW_ACCURACY_PLAN.md` (item 3, test C1) asks for the cost of that growth. This protocol answers:

> On a mixed-material model, with the transport held fixed and the same 2 CPU threads, how do the time from model to
> photon source and the memory of ACTINV and of OpenMC 0.15.3 in flux mode grow as the mesh is refined from 8 to
> 32,768 elements, and what is the finest mesh each completes inside the same memory and time limits?

A PASS certifies the measurement, whatever it shows. A result in which OpenMC reaches the finer mesh, or ACTINV is not
faster, is a PASS.

## Disclosure of what has been seen

- P163's record (`results/p163_speed.json`): at 512 single-material cells OpenMC's flux mode took about 200 s in step 1
  and 230 s in step 2 with a working set of 6.5 GiB, ACTINV about 9 s and 56 s with 1.1 GiB. The sizes and limits
  below were chosen from those numbers.
- The pipeline was rehearsed at 8 and 64 elements before registration (see Rehearsal). Only exit codes, statuses,
  phases and region counts were read; no time or memory figure of this model was printed or read.

## The problem

`controls/p181_harness.py`, `build_model`: a 14.1 MeV isotropic point source at the centre of a tungsten sphere
(radius 4 cm, 19.25 g/cm³), inside a CuCrZr shell to 5.5 cm (Cu 98.9, Cr 0.8, Zr 0.3 wt%, 8.90 g/cm³) and a water
shell to 6.5 cm (1.0 g/cm³), in SS316 steel (P163's composition, 7.93 g/cm³) to the faces of a 20 cm cube. The demo's
air detector shell at 30–31 cm, the irradiation (1e17 n/s for 365.25 days, then 1e6 s of cooling), the photon dose
tally and the transport settings (20,000 particles × 5 batches for neutrons and for photons, seeds 1000 and 1500) are
P163's. The activation mesh spans the cube with dimension d per axis:

| d | 2 | 4 | 8 | 16 | 24 | 32 |
|---|---|---|---|---|---|---|
| elements | 8 | 64 | 512 | 4,096 | 13,824 | 32,768 |

The spheres cut elements at every size, so elements near them hold two or more materials. The number of activation
regions is recorded per run. Particle counts are not scaled: finer elements get noisier fluxes, and the quantity checked
is the integral dose.

## Arms

- **`actinv`.** `actinv.openmc.ActinvR2SManager` from `python/pysrc/actinv/openmc.py` at the registered commit (the
  module users import; it is loaded without the package's native extension, which the adapter does not use), with the
  released ACTINV 1.7.1 Linux binary (`target/p180-bin/actinv`, `bd0c21c9…`, P180's) and the shipped TENDL-2025
  library and decay data of data catalog 1.2.0, `threads=2`. Step 1 is OpenMC's with a flux-only tally on the
  library's 709 groups; step 2 is one `actinv mesh` call for all regions; step 3 is OpenMC's.
- **`openmc_flux`.** P163's arm unchanged: `R2SManager`, step 1 with `reaction_rate_mode="flux"` and the 709 library
  boundaries, `IndependentOperator` with `PredictorIntegrator` over P163's chain (3,820 nuclides), ENDF/B-VIII.1
  cross sections, step 3 OpenMC's.

The data differ as in P163 (TENDL-2025 for ACTINV, ENDF/B-VIII.1 for OpenMC); that is each code's normal data, and the
accuracy checks below are sanity bounds, not a comparison of data.

## Limits, order and stopping

- **Harness scope.** The harness runs in the AGENTS.md scope (6 GiB, no swap, 128 tasks, 200 % CPU) and refuses to
  start otherwise. It holds no model.
- **Run scopes.** Each run gets its own transient scope with **10 GiB**, no swap, 128 tasks and 200 % CPU, the same for
  both arms. The owner allowed more than the repository's 6 GiB for this protocol (2026-11-01). Each run has a
  **4-hour** wall-clock limit, the same for both arms; on expiry its process group gets SIGTERM, ten seconds, then
  SIGKILL, and is reaped.
- **Headroom.** Before each run the harness waits, up to 30 min, until `MemAvailable` is at least 11 GiB, and records
  it. A run that started with less is flagged; if it is killed, the kill is not attributed to its memory.
- **Order.** Sizes ascending; at each size `actinv` then `openmc_flux`. One run per arm and size (this is a scaling
  test; P163 measured the run-to-run spread on the same machine at 8–512 cells).
- **Stop rule.** An arm whose run at one size did not complete steps 1 and 2 is not run at larger sizes.
- **Projection rule.** An arm is not run at a size where its steps-1-2 time at the previous size, scaled by the element
  ratio, exceeds the 4-hour limit. The checker recomputes both rules from the record.
- **Quiet machine.** The coordinator runs no other job during the campaign. The harness records the user's other
  transient scopes at the start, before each run and at the end, and the 1-minute load average before each run.

Worst case: 12 runs at the limit is 48 h; the rules make that unreachable in practice. Expected from P163's per-region
costs: 4–8 h, most of it OpenMC at 4,096 and 13,824 elements.

## What each run records

Times from `time.monotonic()` reads directly around each manager step, as in P163: step 1 (transport, mesh material
volumes, tally reading and, for OpenMC, the cross-section collapse), step 2 (the activation solve with its file I/O),
step 3 (photon transport). **H1 = step 1 + step 2**, model to photon source, is the headline. The record is written after
each step, so a run stopped in step 3 keeps H1.

Memory: the run scope's `memory.peak` (includes page cache, an upper bound) and `ru_maxrss` of the run and of its
children (the working set, the larger of the two), at the end of each step; and a heartbeat every 5 s with the current
phase, `memory.current` and `memory.peak`, so a run killed at its limit leaves how far it got.

Status per run: `ok` (all three steps), `timeout`, `killed` (a SIGKILL the harness did not send: the kernel at the
10 GiB limit, or systemd-oomd under memory pressure), or `failed` (any other non-zero exit, with its stderr tail).

## Gates

- **G0:** this protocol's SHA-256 (and any amendment's) is in `protocols/protocol_hash.txt`.
- **G1:** the pins (the binary `bd0c21c9…`, the library, decay files, chain, `cross_sections.xml` and archive digests,
  all P163's or P180's), OpenMC 0.15.3, `actinv 1.7.1`, the harness, this protocol, the adapter module, its
  `_defaults.py`, the demo file and the group file equal to the registered commit's, a clean tracked tree, the harness
  cgroup within the AGENTS.md limits and every run scope at 10 GiB, no swap, 2 CPUs. Not a smoke record.
- **G2:** every planned (arm, size) was run or not run by a registered rule, recomputed by the checker; both arms
  completed all three steps at 8 elements; every completed run has finite times, a dose and a photon strength, and
  the 709-group flux.
- **G3:** a quiet machine: no other transient scope of the user at the start, before any run or at the end, and the
  median 1-minute load average before the runs at most 3.0.
- **G4:** CI green on the evidence commit.

## Accuracy: speed and reach are not bought by a wrong answer

At each size where both arms completed all three steps:

1. the dose ratio ACTINV / OpenMC flux is within [0.90, 1.10] (wider than P163's 5 % because tungsten, copper and water
   bring larger data differences than steel; it is an engineering bound that catches a broken pipeline, not a test of
   data);
2. the photon strengths are within a factor of two;
3. the summed neutron flux per source particle agrees within 2 % (same seed, same histories).

At a size where only ACTINV completed, its dose must be within [0.90, 1.10] of OpenMC's dose at the largest size where
both completed. A size that fails these checks has no sentence.

## Summary (by the checker, `controls/check_p181.py`)

Per arm and size: status, regions, step times, H1, peak working set and `memory.peak`, the heartbeat's last phase and
memory for runs that did not complete. Per size with both: the H1 ratio OpenMC / ACTINV and the working-set ratio. Per
arm, the **reach**: the largest size that completed steps 1 and 2, and why the next size did not (killed at the memory
limit, timeout, failure, not run by the projection rule, or none: the largest size planned).

## Verdict

P181-PASS if G0–G4 hold, P181-FAIL otherwise. One repair round, registered as an amendment first.

## Allowed sentences

With PASS, the numbers filled by the checker:

1. Per size where both completed and the accuracy checks pass: "On a mixed-material model (a tungsten sphere in CuCrZr,
   water and steel, 14.1 MeV source) meshed into E elements (R activation regions), with the same transport and two
   CPU threads, ACTINV 1.7.1 took A s from model to photon source and OpenMC 0.15.3 in flux mode O s, with peak working
   memory of a GiB and o GiB (one run each)."
2. Reach: "Inside the same 10 GiB memory limit and 4-hour limit per run, ACTINV completed meshes up to E_A elements
   (R_A regions) and OpenMC 0.15.3 in flux mode up to E_O elements (R_O regions)", followed by the checker's reason for
   each arm that stopped below 32,768 elements: "; at E elements it was stopped by its memory limit" (status `killed`
   with a heartbeat `memory.peak` of at least 9.5 GiB and the headroom met), "…by the time limit", "…by a failure", or
   "; it was not run at E elements because its time at the previous size projected beyond the limit". A kill without
   that heartbeat evidence is reported as "stopped by the system at E elements", with no memory claim.

Each may be quoted with the machine (CPU model in the record) and the 2-thread setting.

## Not claimed

- Nothing about accuracy beyond the sanity bounds above; nothing about other models, materials, machines, thread
  counts, or OpenMC versions; no projection beyond the measured sizes; no claim for OpenMC's direct-tally mode.
- Not a statement about the tools' run-to-run spread (one run per size).

## Rehearsal

Before registration the coordinator ran `p181_harness.py --smoke` (8 and 64 elements, both arms, the projection rule off)
with outputs under `target/p181-rehearsal*`. Only exit codes, statuses, phases and region counts were read; the outputs
were deleted afterwards. The result is recorded in the ledger entry that registers this protocol.

## Commands (coordinator)

From the worktree at the registered commit, inside the AGENTS.md scope (200 % CPU, 6 GiB), with no other job running:

1. `~/micromamba/envs/openmc0153dd/bin/python controls/p181_harness.py --actinv-bin
   /home/connoravila/Documents/actinv/target/p180-bin/actinv --xs-archive-sha256
   b7ad59cb4a3d76d8a291326093a98507f8d24b6e6af629116d3f7dc85f83c4cb`
2. `python3 controls/check_p181.py` (writes `results/p181_verdict.json`).
