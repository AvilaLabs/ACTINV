# ACTINV-P171 Amendment A — the ACTINV arm creates its result directory

Registered after the first ACTINV arm stopped and before any record was written. This is the protocol's one repair
round.

## What happened

After registration (02b5cc1), a smoke test ran OpenMC's driver on the smallest chunk (5 experiments: Al, Cu, NiCr, V,
Zr; official chain). It finished in 96 s with a peak of 2.4 GB in its 6 GiB scope, so no default split is needed and
amendment slot 1 stays unused. Its heat values were not computed or read.

The ACTINV TENDL-2025 arm then ran inside the bounded scope. It solved all 132 experiments and stopped when
`controls/cb2_fns.py` wrote its result: `results/p171/` did not exist, and `cb2_fns` writes without creating the
directory. The OpenMC arms write through `write_atomic`, which creates it, so only the ACTINV arms are affected.

The run printed one progress line per tenth experiment (index, material, experiment, number of points, wall time, cache
hits). No C/E, heat or summary statistic was printed or read, and the per-experiment files in the work directory were
not opened.

## Change

`run_actinv_arm` in `controls/p171_openmc_fns.py` creates the result's parent directory before calling `cb2_fns`.
Nothing else changes: the cases, arms, pins, module, scoring, gates and sentences are as registered.

`controls/check_p171.py --selftest` gains a regression case. It runs `run_actinv_arm` against a stand-in `cb2_fns` that
writes as the real one does, into a directory that does not exist yet. The case fails on the registered harness and
passes on the amended one.

## Rerun

The ACTINV work directory of the stopped run (`target/p171-actinv-tendl2025/`) is deleted before the rerun, so no
cached result carries over. Every arm runs on this amendment's registration commit.
