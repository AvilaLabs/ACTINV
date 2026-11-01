# ACTINV-P179 — P172's identical-data arm on the repaired ENDF/B-VIII.1 library

## Purpose

P172 compared ACTINV and OpenMC 0.15.3 depletion against 2,360 measured decay-heat points from all 132 JAEA FNS
experiments. Its `actinv_endf8` arm gave ACTINV the same ENDF/B-VIII.1 cross sections as OpenMC, collapsed to 709
groups by ACTINV, so that the library difference between the two codes was removed. That arm's median |ln C/E| was
0.234, and its allowed sentence is P172's second.

Ledger 316 then found that ACTINV's processing summed R-matrix-limited (LRF=7) capture coherently over levels. The
ENDF/B-VIII.1 library of P172's arm was built with that defect, so its capture rates for 15 targets were wrong:
Si-28/29/30, Cl-35, Ca-40, V-51, Fe-54/57, Cu-63/65, Rh-103 and W-182/183/184/186. Ledger 316 recorded that the arm's
attribution is not reliable for samples whose decay heat depends on resolved-range capture until it is rerun on a
corrected library. The library has since been rebuilt with the repair (`82c6b75`, released in 1.7.1) for P178. The
question:

> With only the library replaced by the repaired one, what is the `actinv_endf8` arm's median |ln C/E| on P172's points,
> and where does it change?

A PASS certifies the measurement. The result is reported, not gated, as in P172. It may be better or worse than 0.234.

## Disclosure of what has been seen

- P172's record, verdict and every arm value were read in full when P172 was scored (ledger 295).
- No value of the rerun arm has been computed or read.
- The repaired library was compared with P172's before registration, array by array: the row layout and group bounds
  are identical, and 32 rows of the cross-section array differ, all MT 102 (radiative capture), for exactly the 15
  targets above. The magnitudes of the differences were not read. The indexes differ only as ledger 316 and P178
  record (parse notes, and the MT 102 reconstruction notes of the 15 targets).
- The compositions of the 132 FNS inputs were read to define the control set below: 30 experiments contain Ca, Cl, Cu,
  Fe, Rh, Si, V or W (the Ca, Cl, Cu, Fe, Inconel-600, NiCr, Rh, SS304, SS316, Si, V and W samples), and 102 contain
  none.
- The pipeline was rehearsed before registration (see Rehearsal). Only exit codes and gate lines were read.

## What changes and what does not

| | P172 `actinv_endf8` | P179 |
|---|---|---|
| Library | `~/nuclear-data/endfb-viii.1-p172/endfb81_all_709g.npz` (`ecaca7c0…`) | `~/nuclear-data/endfb-viii.1-p178/endfb81_all_709g.npz` (`a964a44e…`) |
| Index | `ff59cccf…` | `b5a4b668…` |
| ACTINV Python module | `f33ea3ed…` | the same file, copied to `target/p179-bin/libactinv.so` in the main checkout |
| Harness, `cb2_fns.py`, 709-group structure | as committed at P172 | unchanged (`2795f3ee…`, `3b3295d3…`, `ce4bf523…`) |
| FNS archive, decay files | P172's pins | the same pins |

The harness (`controls/p179_endf8_repaired.py`) imports P172's and redirects only the library and the work directory.
P172's TENDL-2025 and OpenMC values are taken from its sealed record (`results/p172_openmc_fns.json`, `2b4dd74a…`), not
recomputed.

## Scoring

As P172, on the points with a positive measurement and a positive value in all of P172's TENDL-2025 arm, the rerun arm,
OpenMC and P172's `actinv_endf8` arm:

- per arm, the statistics of P172 (median, p90 and max of |ln C/E|, geometric mean C/E, points and experiments within
  30%);
- the 95% bootstrap interval of median|ln C/E|(OpenMC) − median|ln C/E|(rerun), with P172's procedure (2,000
  resamples of experiments, seed 172);
- the shift `ln(rerun / P172 arm)` per point, the number of points it moves by more than 0.01, and per experiment of
  the 30 above, each arm's median |ln C/E| and the median shift.

## Gates

- **G0:** this protocol's SHA-256 (and any amendment's) is in `protocols/protocol_hash.txt`.
- **G1:** the pinned inputs in the table above, P172's sealed record and arm file, P172's module, P172's harness,
  `cb2_fns.py` and group structure unchanged from P172's record, a clean tracked tree, and the bounded cgroup (6 GiB, no
  swap, 128 tasks, at most two CPUs).
- **G2:** fidelity.
  - All 132 experiments, each with P172's points, times and measured values. The arm's own checks of P172's G2 pass.
  - At most 5% of points dropped for a non-positive calculation.
  - The libraries share their row layout and bounds, and every differing row is MT 102 of one of the 15 targets, all
    15 present.
  - **Control:** on each of the 102 experiments whose composition contains none of Ca, Cl, Cu, Fe, Rh, Si, V or W,
    every point of the rerun reproduces P172's arm within 1e-4 in ln. Higher-order paths through a repaired target
    (for example Ni-60(n,α)Fe-57 then capture) are far below that at FNS fluences.
- **G3:** `controls/check_p179.py` recomputes the summary and the allowed sentences and writes `results/p179_verdict.json`.
- **G4:** CI green on the evidence commit.

## Verdict

P179-PASS if G0–G4 hold, P179-FAIL otherwise. A failure of the control is a failure of the protocol, not a finding about
accuracy: it would mean the rerun is not a library-only change.

## Allowed sentences

With PASS, and X, N, M filled from the verdict:

1. "With its R-matrix-limited capture repaired and otherwise the same ENDF/B-VIII.1 cross sections as OpenMC, ACTINV has
   a median |ln C/E| of X on N measured decay-heat points from M JAEA FNS experiments."
2. "The repair changes K of those points by more than 1%, all in the 30 experiments whose samples contain Ca, Cl, Cu,
   Fe, Rh, Si, V or W."

With PASS, sentence 1 replaces P172's second sentence wherever that is used publicly. P172's verdict is not changed.
The bootstrap interval is recorded, and as in P172 no ranking sentence is allowed.

## Not claimed

- Nothing about OpenMC beyond P172's sentences. OpenMC's values are P172's.
- Nothing about TENDL-2025 or the shipped data, which the defect did not affect (ledger 316).
- No accuracy claim for the 102 control experiments beyond P172's.

## Rehearsal

Before registration the coordinator may run the whole pipeline once with `P179_REHEARSAL=1` (outputs only under
`target/p179-rehearsal*`, no verdict written). Only exit codes and gate lines are read; the checker's output is cut at
its `outcome:` line, and the outputs are deleted afterwards. The result is stated in the registration's ledger entry.

## Commands (coordinator)

From the worktree, with `PYTHONDONTWRITEBYTECODE=1` and
`ACTINV_PYTHON_MODULE=/home/connoravila/Documents/actinv/target/p179-bin/libactinv.so`, each mode inside the AGENTS.md
scope (200 % CPU):

1. (Before registration: `python3 controls/check_p179.py --selftest`.)
2. `python3 controls/p179_endf8_repaired.py library-diff`
3. `python3 controls/p179_endf8_repaired.py actinv`
4. `python3 controls/p179_endf8_repaired.py assemble`, then `python3 controls/check_p179.py`.
