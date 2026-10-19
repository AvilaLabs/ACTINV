# P148 Amendment A — cyclic starting bound, synthetic references and long-history cases

Registered 2026-10-19, after implementation and before the candidate commit and any evidence run. No gate is relaxed.
Development runs on uncommitted code are disclosed below; they are not evidence.

## A1. Starting bound for a cyclic component

The protocol specified a Collatz–Wielandt weight from power iteration of `I + Cᵀ`. A development unit test (an isomer
pair: IT branch 1 one way, a small (n,n') rate the other) showed that this iteration does not reach a weight with
`q < 1` in 100 steps when the component is periodic and `ρ(C)` is small: the eigenvalues of `I + Cᵀ` are `1 ± √ε` and
the weight barely moves from its start, where `q = 1`. The component would have been reported unbounded though it
contracts strongly.

Replacement, used by both the Rust implementation and the independent checker: with `C_mj = min(a_jm T, g_jm)` inside
the component and `b` the inflow bound from outside plus `N0`, iterate `v_0 = 1`, `v_{k+1} = 1 + C v_k`. Then
`C v_k = v_{k+1} − 1`, and the first `k ≤ 1000` with `q_k = max_m (v_{k+1,m} − 1)/v_{k,m} < 1` gives a vector with
`C v ≤ q v`. With `t = max_m Θ_m / v_m`, `Θ_m ≤ b_m + t q v_m`, so `Θ_m ≤ v_m max_m(b_m/v_m)/(1 − q)`. That is the
start; the sweeps are as registered. If no `k ≤ 1000` gives `q < 1`, or the iterate overflows, the component is
unbounded as registered. Every fixed point of the bound map lies below any such start, so the sweeps converge to the
same values from any valid start.

## A2. R1 and R2 on synthetic problems

Synthetic problems do not pass through a spec, a library or the solver. `prune_probe --synthetic BATTERY OUT`
evaluates raw problems (decay, feed, removal and per-spectrum reaction triplets, `N0`, a `(duration, multiplier)`
schedule) through the library's public `prune::prune_schedule`. The checker `controls/check_p148.py synthetic`
generates the battery (named cases for each required feature, plus seeded random networks, at least 30 in all),
recomputes everything independently (Kosaraju components, Kahn order, the inequalities above), and takes its R2
reference from dense matrix exponentials (`scipy.linalg.expm`), with each step's own spectrum, feed and removal, at 40
points per step plus `t = 0`; the pruned solution is the same calculation restricted to the kept states with their full
diagonals. A sampled supremum can miss a peak between samples, so a pass shows the bound dominates every sampled
value. Tolerance: `1e-9` of the largest population. The probe's spec mode, used for the FNS and long-history cases,
keeps the registered CRAM-48 reference and CRAM-16/48 tolerance.

## A3. Capture contents

While a capture is active, `run` attaches each state's name and evaluated mean energy per decay (light, EM and heavy,
eV) to the record. The checker recomputes the ledger's heat bounds from those energies, so it checks the arithmetic
of the heat bound, not the decay-data energies themselves (those have their own controls).

## A4. Long-history cases (R3)

The registered "bundled pulsed example" is replaced by a case with the same spectrum as the first long case, so the
two differ only in history. Both are coupled-mode runs on the FNS iron 2000 5-min spectrum and composition with the
EAF-2010 library:

- one year at constant flux, then cooling steps of 60 s, 1 h, 1 d, 30 d and 1 year;
- twelve monthly cycles of 20 days at full flux followed by the rest of the month at zero flux, then one year of
  cooling.

## A5. Development runs disclosed

On uncommitted code, before this amendment:
- the unit tests in `prune.rs` passed (7 of 7, including the R4 converging-path case);
- `check_p148.py synthetic` reported R1 and R2 passing on 40 cases with every required feature covered;
- the checker selftest caught every planted error;
- R1 recomputation matched one FNS iron capture exactly;
- `p148_runs.py` was run on six cases.

The removal-edge share was changed from unbounded to 1 to match the protocol after these runs. The evidence reruns
everything on the candidate.

## A6. Historical P65 controls

The P65 controls (`g1_p65_mechanics.py`, `g2_p65_exactness.py`, `g3_p65_demo.py`, `check_g5_p65.py`) read the
pre-P148 field names (`removed_heat_W_per_g_bound`, `removed_activity_Bq_per_g_bound`) and the pre-P148 drop rule.
They are sealed records of P65 at its own commits, are not run by CI, and are not edited. Run on the candidate they
would fail on the renamed fields by design; R5 lists them with the other changed baselines.

## A7. R5 method and the baselines found

A local replay of the CI control steps on uncommitted code (disclosed as development work) found these results that
the candidate changes; no other CI-gated record moved:

- `results/p15_cache_integrity.json` (normalized-result hash);
- `results/g1_p110_composition_solve.json` (nine case-output hashes) and `results/g2_p110_composition_solve.json`
  (the G1 record hash);
- `controls/ci_expected.json` (`pruned_states` 40 → 38: Mn59 and Ti51 now fall below `bmin`), re-seated with a dated
  note as at its earlier rebaseline;
- `PRE_P10_NEUTRON_NORMALIZED_SHA256` in `controls/g6_p10_projectile_runtime.py`, re-seated with a comment as at its
  four earlier re-seats. The records that control and `ci_end_to_end.py` write (`results/g6_p10_projectile_runtime.json`,
  `results/ci_end_to_end.json`) are not gated by CI, were already older than the current solver, and embed local paths;
  they are not regenerated.

`controls/p148_r5.py` runs each case with the binary built from the opening commit `364a73e` and with the candidate.
It requires the old binary to reproduce the committed pre-P148 value and the candidate the re-seated one. It
classifies every difference between the two outputs with `controls/p148_baselines.py`, and also compares each
regenerated record with its opening version. The classes make R5's "drop set" and "previous values" concrete:

- pruning: the `rate_pruning` block, the `screen` certificate, pruned-state counts;
- drop set: rows, keys and photon lines of the dropped nuclides (lines matched by energy, with total photons no
  more than the dropped nuclides emit); populated and below-floor state counts that move by no more than the number
  dropped; below-floor sums and bounds that do not increase; and `pathway_closure` equal to the old per-nuclide
  maximum over the nuclides that remain (in the end-to-end case the old maximum, 1.0, was the now-dropped Mn59);
- hash fields;
- bounded: heat within `heat_W_per_g_bound`, activity within `activity_Bq_per_g_bound`;
- rounding: within 1e-12 relative;
- noise: atom values that are themselves within the R2 slack (1e-12 of the largest total atoms), or values
  describing a nuclide at or below 1e-12 of its step's total atoms on both sides.

Anything else fails R5. The classifier has a selftest (`p148_baselines.py --selftest`) whose planted changes
(heat or activity beyond its bound, a 1 % change in kept atoms, a nuclide only in the new result, a changed string,
a count moved too far, a changed closure, a new key) must all be caught, and whose allowed changes must all pass.
The rounding and noise classes are an interpretation that the protocol did not state; they are registered here
before the evidence run.

Three CI controls exceed the 6 GB local memory limit (`check_g3_p18b.py`, `check_g4_p18b.py` and `check_p18b.py`, which load the full
TENDL-2025 libraries) and were not run locally; they do not read pruning output, and CI runs them.

The typed entry point keeps its name `prune::reachable_physical`, which the P16 typed-boundary control requires.
