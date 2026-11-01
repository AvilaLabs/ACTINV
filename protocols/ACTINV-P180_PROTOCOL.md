# ACTINV-P180 — wall time of ACTINV and OpenMC depletion on P172's 132 FNS problems

## Purpose

P172 ran ACTINV and OpenMC 0.15.3 depletion on the same 132 JAEA FNS decay-heat problems. Its record holds OpenMC's
per-case depletion times (20–64 s) and ACTINV's in-process times (about 1 s), but P172 was not built to measure speed.
OpenMC ran in chunks that shared one start-up and one cross-section collapse per flux, on a nuclide set that was the
union over the chunk, while ACTINV ran inside a long-lived Python process. `docs/maintainers/ACCURACY_PROGRAM.md` recorded the
comparison as an unregistered observation that needs its own protocol before it can be cited. This is that protocol:

> Solving one FNS problem in a fresh process, with the same two CPU cores and the same ENDF/B-VIII.1 cross sections,
> how long do the released ACTINV 1.7.1 and OpenMC 0.15.3 depletion take from start to finish, and how long does
> OpenMC's depletion step alone take?

The times are reported, not gated. A PASS certifies the measurement, whatever it shows.

## Disclosure of what has been seen

- P172's per-case OpenMC depletion times and ACTINV's in-process times, and the R2S demonstration in
  `results/openmc_r2s_demo.json` (19 s against 850 s, a different problem), were read before this protocol was written.
- No time of the runs this protocol defines has been measured or read.
- The pipeline was rehearsed on three problems before registration (see Rehearsal). Only exit codes and gate lines
  were read; no time was printed.

## What is run

The 132 experiments of P172, in sorted order (material, then experiment). For each, three processes run one after
another, in this order, inside the harness's bounded scope (6 GiB, no swap, 128 tasks, 200 % CPU):

| Arm | Process | Data |
|---|---|---|
| `actinv_endf8` | the released `actinv 1.7.1` Linux binary, `actinv run PROBLEM.json RESULT.json` | the ENDF/B-VIII.1 709-group library P178 and P179 pin, built from the same evaluations as OpenMC's library |
| `actinv_tendl` | the same binary | the shipped TENDL-2025 library of data catalog 1.2.0 |
| `openmc` | `controls/openmc_fns_driver.py` (P172's driver, unmodified) with a one-case job | OpenMC 0.15.3, ENDF/B-VIII.1 HDF5, the published `chain_endfb81_fast.xml`, P172's environment (`OMP_NUM_THREADS=2`) |

- The ACTINV problems are `controls/cb2_fns.py`'s, exactly as P172 ran them (irradiation, cooling steps, the 709-group
  flux, mode `auto`, rate pruning, outputs inventory, activity, heat, ledger and certificate), with only the library
  changed. ACTINV runs with `RAYON_NUM_THREADS=2` and a private cache under the work directory.
- The OpenMC job is `openmc_fns_headtohead.build_job` for the one experiment, with P172's published chain. The driver
  starts OpenMC, loads the chain, collapses the cross sections of the nuclides reachable from the sample
  (`MicroXS.from_multigroup_flux`), and depletes with `IndependentOperator` and `PredictorIntegrator`. It reports the
  session start-up, the collapse and the depletion time.
- Each process is timed from its start to its exit with `time.monotonic`, and its CPU time is read from `wait4`. The
  1-minute load average before each process and the user's other transient scopes before each problem are recorded.
- Before the timed pass, each arm runs the first problem once, untimed, so that ACTINV's cache is prepared and the files
  of both codes are in the page cache.

The binary is the release asset `actinv-1.7.1-linux-x86_64.tar.gz` of the v1.7.1 GitHub release, which matches the
release's `SHA256SUMS`. It was copied to `target/p180-bin/actinv` in the main checkout.

## Frozen inputs

| input | SHA-256 |
|---|---|
| `target/p180-bin/actinv` (actinv 1.7.1, the release binary) | `bd0c21c96a17918d6a21b29553be33877ff3c5b7fa4b92882598d7a37f6458bc` |
| `~/nuclear-data/endfb-viii.1-p178/endfb81_all_709g.npz` and its index | `a964a44e…92051`, `b5a4b668…7b41a` |
| `actinv-data/v1.2.0/activation/tendl-2025-neutron-709g.npz` and its index | P172's pins |
| chain, `cross_sections.xml`, decay files, FNS archive, abundance table | P172's pins |
| `controls/openmc_fns_driver.py` | equal to the digest in P172's record |

## Summary

On the timed pass:

- per arm, the median, mean, 10th and 90th percentiles, minimum, maximum and total of the wall and CPU times;
- OpenMC's phases (start-up, collapse, depletion, and the rest of the process) in the same way;
- per problem, the ratios OpenMC wall / ACTINV (ENDF/B-VIII.1) wall, OpenMC depletion / ACTINV (ENDF/B-VIII.1) wall,
  OpenMC wall / ACTINV (TENDL-2025) wall, and OpenMC CPU / ACTINV (ENDF/B-VIII.1) CPU, each with its median, geometric
  mean, range and percentiles;
- diagnostics, not gated: per problem, the largest |ln| between this run's OpenMC decay heats and P172's, and between
  ACTINV's ENDF/B-VIII.1 decay heats at the measured times and P179's.

## Gates

- **G0:** this protocol's SHA-256 (and any amendment's) is in `protocols/protocol_hash.txt`.
- **G1:** the pins above, `actinv 1.7.1`, OpenMC 0.15.3, P172's driver unchanged, the bounded cgroup and a clean tracked
  tree.
- **G2:** every problem ran in all three arms with exit code 0; every ACTINV result has a finite decay heat at every
  cooling time; every OpenMC case executed with finite decay heat and its three phase times; the warm-up ran cleanly.
- **G3:** a quiet machine. No other transient scope of the user ran at the start, at the end or before any problem (the
  coordinator runs no other job during the run), and the median 1-minute load average before the processes is at most
  3.0. Then `controls/check_p180.py` recomputes the summary and writes `results/p180_verdict.json`.
- **G4:** CI green on the evidence commit.

## Verdict

P180-PASS if G0–G4 hold, P180-FAIL otherwise. A failure of G3 means the times were not measured on a quiet machine; it
is not a finding about either code.

## Allowed sentences

With PASS, and the numbers filled from the verdict:

1. "On the 132 JAEA FNS decay-heat problems of P172, each solved in its own process on the same two CPU cores with the
   same ENDF/B-VIII.1 cross sections, ACTINV 1.7.1 took a median of A s per problem from start to finish and OpenMC
   0.15.3 depletion O s; the median per-problem ratio was R."
2. "Counting only OpenMC's depletion step, without its start-up and cross-section collapse, the median per-problem ratio
   was R2."
3. "With its shipped TENDL-2025 data, ACTINV took a median of T s per problem."

Each may be quoted with the machine (CPU model, recorded in the verdict) and the two-core limit.

## Not claimed

- Nothing about accuracy; that is P172's and P179's.
- Nothing about other problem sizes, transport, or OpenMC's coupled (transport) depletion. Mesh-scale timing is a
  separate test.
- No amortised comparison: a user who solves many problems in one OpenMC session shares its start-up and, for one flux,
  its collapse. Sentence 2 bounds that case from OpenMC's side, since it counts only the depletion step against
  ACTINV's whole process.

## Rehearsal

Before registration the coordinator may run the pipeline once with `P180_REHEARSAL=1`: the first three problems only,
outputs under `target/p180-rehearsal*`, no verdict written. Only exit codes and gate lines are read; no time is printed,
the checker's output is cut at its `outcome:` line, and the outputs are deleted afterwards.

## Commands (coordinator)

From the worktree, with `PYTHONDONTWRITEBYTECODE=1` and
`ACTINV_PYTHON_MODULE=/home/connoravila/Documents/actinv/target/p179-bin/libactinv.so` (used only by `prepare`, to load
`cb2_fns.py`), each mode inside the AGENTS.md scope (200 % CPU), with no other job running:

1. (Before registration: `python3 controls/check_p180.py --selftest`.)
2. `python3 controls/p180_speed.py prepare`
3. `python3 controls/p180_speed.py run`
4. `python3 controls/p180_speed.py assemble`, then `python3 controls/check_p180.py`.
