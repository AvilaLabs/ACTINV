# ACTINV-P189 — ACTINV's activation step on a 1,000,000-element mesh within 6 GiB

## Purpose

P181 Amendment A ran ACTINV's activation step on mixed meshes of up to 32,768 elements (36,208 regions) and
stopped there because OpenMC's 709-group step-1 tally runs out of memory first. P189 asks whether ACTINV's own
step scales further. On one laptop, inside the usual 6 GiB, 4-core bounded scope:

1. Does `actinv mesh` finish a 1,000,000-element mesh, with four materials and a distinct 709-group flux in every
   element?
2. Is every element's result right? A random sample is re-solved on its own and must match exactly.

P189 measures capability and cost. It makes no accuracy claim about the fluxes, which are interpolated.

## Method

`controls/p189_harness.py` runs these steps.

- **Coarse flux (`coarse`, input).** OpenMC 0.15.3 tallies the neutron flux of P181's model (14.1 MeV point
  source; tungsten sphere, CuCrZr and water shells, SS316 steel to the faces of a 20 cm cube) on a 16³ mesh over the
  cube in the 709 FISPACT groups: 10 batches of 100,000 particles, seed 189, ENDF/B-VIII.1. The flux is scaled to
  P181's source rate of 1e17 n/s. The file is `target/p189-work/coarse.npz`. It was made before registration, and
  its SHA-256 is pinned below.
- **Fine mesh (`fine`).** The cube is split into 100³ elements of 0.2 cm. Each element's flux is interpolated
  trilinearly, linear in flux and per group, between coarse element centres (clamped at the outermost centres). Each
  element takes its material from its centre's radius:
  - W inside 4.0 cm;
  - CuCrZr inside 5.5 cm;
  - water inside 6.5 cm;
  - SS316 elsewhere.

  Compositions and densities are P181's. The harness writes the flux file (`actinv-flux-1`, about 15 GB) and the
  mesh spec, with `materials` giving each non-steel element its material.
- **Run (`run`).** One `actinv mesh` with these inputs:
  - library and decay: the shipped TENDL-2025 709-group library and the catalog decay data;
  - schedule: P181's (1 year at 1e17 n/s, then 1e6 s of cooling);
  - options: mode auto, rate pruning, outputs photons and ledger, 293.6 K;
  - output: each element's mode and photon source groups (`cell_result_fields`), which is what step 3 reads;
  - 4 threads, resume on.

  The harness bounds the wait at 24 h and always reaps the child. Every 30 s it writes a heartbeat with the scope's
  memory. If the machine interrupts the run (not its own limits), the run may be resumed from its output with the
  same command; the record lists every attempt.
- **Sample (`sample`).** 100 elements are drawn without replacement (`numpy.random.default_rng(189)`). Each is
  re-solved alone with `actinv run`, from a point spec equal to the mesh cell's: material, the element's flux as a
  custom spectrum on the library groups, schedule and options. The stand-alone result, reduced to the same fields,
  must equal the mesh's cell result exactly.
- **Assemble (`assemble`).** `results/p189_record.json` holds the input hashes, the run attempts and environment, a
  streamed scan of the output (cell count, each ordinal once, footer) and the 100 sample rows.

The binary is built in the bounded scope from a clean checkout of the registration commit
(`cargo build --release --locked -p actinv-cli`). Every step runs in the bounded scope: 6 GiB, no swap, 128 tasks,
CPUQuota 400%.

## Gates

- **G0:** this protocol's SHA-256, and those of `controls/p189_harness.py` and `controls/check_p189.py`, are in
  `protocols/protocol_hash.txt`.
- **G1:** provenance. The run is from the registration commit with a clean tree, the binary's SHA-256 is recorded,
  and the scope is 6 GiB memory, no swap, 400% CPU and 128 tasks. The record is not a rehearsal and the mesh is
  100³. The coarse flux's SHA-256 is the pin below.
- **G2:** completeness. Exit status 0, and 1,000,000 cell records, each ordinal exactly once, matching the footer.
- **G3:** memory. The scope's peak memory is at most 6 GiB. It is recorded and quoted.
- **G4:** correctness. All 100 sampled elements equal their stand-alone results.
- **G5:** flux conservation. The output footer's flux sums equal the input file's to 1e-12 relative, with zero
  rebinning closure.
- **G6:** CI green on the evidence commit.

P189-PASS if G0–G6 hold. One repair round (an amendment with a fresh run) is allowed.

Pinned input: `coarse.npz` SHA-256 `88315fcdd77cf6ddc222954eb5dff52e7351e9a6f9d9d9a5bba469576c10a8bf`.

## Disclosure

- **Seen before writing:** P181A's step-2 times and memory at 8–32,768 elements (ledger 328–332), and the
  coarse-flux run's exit status, wall time and zero-bin count.
- **Rehearsal.** One rehearsal of the whole pipeline at 20³ elements (`P189_REHEARSAL=1`, outputs under
  `target/p189-rehearsal`) was read for exit statuses and gate results only. Its outputs are deleted before the
  scored run.
- The library is the shipped default, not ENDF/B-VIII.1, so that the run is what a user of the default data gets.

## Allowed sentence

On P189-PASS, filled by `controls/check_p189.py` from the record:

"On one laptop (Intel i3-N305, 4 cores, 6 GiB memory limit), ACTINV's activation step solved a 1,000,000-element mesh
(four materials, 709 groups, a distinct flux in every element, interpolated from an OpenMC tally) in {hours} h with
a peak memory of {gib} GiB; 100 randomly chosen elements reproduced their stand-alone results exactly."

The sentence is about ACTINV's step 2 only. OpenMC's steps 1 and 3 at this size are not part of it.

## Commands (coordinator)

From a worktree at the registration commit, with `ACTINV_BIN` set to the binary built there and an `actinv-data`
link to the installed data, each inside the bounded scope:

- `python3 controls/p189_harness.py fine`, `run`, `sample`, `assemble`;
- `python3 controls/check_p189.py`.
