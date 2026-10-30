# ACTINV-P168 — limits map: the solver against high-precision reference solutions in extreme regimes

## Purpose

ACTINV's solver has been checked against FISPACT-II, ORIGEN and measurements in ordinary regimes, and against exact
solutions for a few short chains. It has not been checked where activation solvers are known to be fragile:
- very long and very short times;
- very high flux;
- long pulse trains;
- reaction and isomer loops;
- defective (equal-rate) systems;
- a 28-order dynamic range;
- target burn-up of tens of percent.

The question:

> In which of these regimes does ACTINV's solver reproduce exact solutions, and where, if anywhere, does it break?

The outcome is a map, not a gate. Every case is reported as *holds*, *breaks* or *fails to run*, whatever the result.
A break is a finding, and it is repaired under a successor protocol, not here. This protocol changes no ACTINV source.

The cases use synthetic decay and reaction data, so they test the numerics, not any nuclear-data library. The
physics limits found by the code audit are outside this protocol:
- flux above an evaluation's top energy;
- self-shielding fixed at the declared composition;
- no charged-particle transport.

They need source changes and their own protocols.

## Reference solutions (frozen before any ACTINV run)

- **Generation.** `controls/p168/make_cases.py` builds each case's matrix from decimal input strings and solves it
  with mpmath:
  - matrix exponential by scaling and squaring at 120–500 working digits, depending on the case (recorded in
    each reference file);
  - closed-form Bateman, Jordan-block and periodic-steady-state solutions where they exist, asserted against the
    exponential.

  Inventories are stored to 30 significant digits at every step end. Half-lives are rounded to 7 digits so that an
  ENDF 11-column field carries them exactly.
- **Committed references.** `results/p168/refs/`, pinned by `results/p168/refs_manifest.json`. Pulse-train
  references are thinned to checkpoints, as listed in the manifest. The checker does not need mpmath.
- **ACTINV inputs.** `controls/p168/to_actinv.py` writes, per case:
  - a synthetic ENDF-6 decay file (MF=8/MT=457);
  - for irradiating cases, a one-group synthetic reaction library in the P166 layout (bounds [1, 3] eV, flux
    multiplier = rate);
  - a problem file.

  These are generated at run time and never tracked.

## Cases (18, plus one scale case)

| Case | Regime | Probe |
|---|---|---|
| 1a, 1b | 10³, 10⁶, 10⁹-year cooling | 14-member U-238-like chain, from pure parent and from secular equilibrium; rates span 21 orders, λt up to 10²⁰ |
| 2 | 1 ns, 1 µs, 1 ms steps | λ from 10⁻¹⁰ to 10⁹ s⁻¹, plus an exactly equal-rate pair |
| 3 (×3) | flux 10¹⁴, 10¹⁵, 10¹⁶ n/cm²/s | Co-59 → Co-60m/Co-60 → Co-61; σφt up to 63; 1 h, 1 y, 10 y, then cooling |
| 4, 10⁴ pulses | pulse train | 1 ns pulses every 0.1 s, Au-197(n,γ) → Au-198, every pulse an explicit step (20,000 steps) |
| 4, 10⁵ pulses | scale | the same train to 10⁵ pulses (200,000 steps), checkpoints at 10³, 10⁴, 10⁵ pulses |
| 4, 10⁶ (×2) | pulse train, truncated | 0.1 s and 1 s periods, truncated at 10³ pulses |
| 5a (×2) | reaction loop | A → B by capture, B → A, B → C, weak and strong |
| 5b (×2) | isomer loop | ground ↔ metastable by capture and isomeric transition |
| 5c, 5d | defective systems | a capture rate equal to a decay constant; three equal half-lives |
| 6a, 6b | dynamic range | ppb impurity in a 10²² atoms/g matrix; coupled (6a) and trace (6b) |
| 7 | target burn-up | W-186 → W-187 → Re-187 → Re-188 → Os-188 at 10¹⁴ for 5 y; 45% of the target transmuted |

Each case runs twice: CRAM order 16 (the default) and 48.
- Mode: as listed (6b trace, all others coupled).
- Options: `prune: none`, `bmin_atoms_per_g: 0`.

## Metrics and classification

At every step end with a reference, with R the reference and N ACTINV's atoms per gram (a nuclide missing from
ACTINV counts as N = 0):

- **M1, normalized error:** max over nuclides of |N − R| / max R.
- **M2, relative error of the nuclides that matter:** max |N − R| / R over radioactive nuclides whose activity
  λR is at least 10⁻⁶ of the step's total reference activity.
- **M3, trace case 6b only:** max |N − R| / R over every nuclide with R > 0.

A case and order **holds** if M1 ≤ 10⁻¹⁰ and M2 ≤ 10⁻⁶ at every step, and for 6b also M3 ≤ 10⁻⁶. Otherwise it
**breaks**, with the first failing step and nuclide recorded. A nonzero exit or a run past the timeout of 1,800 s
**fails to run**. Wall time and exit status are recorded for every run.

The thresholds are a user's standard, not CRAM's theoretical error: a solver error of one part per million in any
nuclide that carries a millionth of the activity is invisible next to nuclear-data uncertainty. A *breaks* means
ACTINV would print a wrong number a user could act on.

## Development disclosure

- The code audit (`docs/maintainers/ACCURACY_PROGRAM.md`) named these regimes from reading the source. It
  noted the safeguards (iterative refinement, the `n_below` ledger, the pruning bound) and that none of them has
  been tested at these extremes.
- All 38 problems the harness generates (19 cases × 2 CRAM orders) passed `actinv validate --hashes` with the pinned
  binary. No case was run, and no ACTINV inventory for any case has been seen.

## ACTINV under test

- **Binary.** The P163 release build, SHA-256 `186cc2587bb61766b3b3c426c61cd44f00ed472401026dea2ca3dd595c8d1194`,
  built from d6f516b. The evidence commit's `crates/` tree must equal d6f516b's, except `crates/actinv-gui`, which
  this binary does not contain.
- Runs inside the bounded scope from AGENTS.md, one at a time.

## Gates

- **G0.** This protocol's SHA-256 (and any amendment's) is in `protocols/protocol_hash.txt`.
- **G1.** Pins and environment:
  - the references equal the manifest;
  - the binary is the pinned one;
  - the crates tree is d6f516b's, apart from the GUI crate;
  - the tracked tree was clean;
  - the run was in the bounded cgroup.
- **G2.** Fidelity:
  - every case and order has a record;
  - every reference file reproduces its hash;
  - float re-checks of the closed forms (two-member Bateman, the periodic steady state) agree with the committed
    references within 10⁻¹²;
  - each record's classification follows from its metrics.
- **G3.** The limits map and sentences are recomputed from the record and written to (or, with `--no-write`, equal)
  `results/p168_verdict.json`.
- **G4.** CI green on the pushed evidence commit.

## Verdict

P168-PASS if G0–G4 hold, whatever the map shows. One repair round, registered as an amendment first.

## Allowed sentences

Filled from the computed numbers only, as `controls/check_p168.py` writes them:

1. "On {n} synthetic stress cases checked against high-precision reference solutions (30 significant digits
   recorded), ACTINV's CRAM-16 solver held to 1e-10 normalized error and 1e-06 relative error on the nuclides carrying
   activity in {k} cases, broke that bound in {b} and did not run to completion in {f}."
2. "With CRAM-48 the solver held in {k} cases, broke in {b} and did not run to completion in {f}."
3. For each order: "The largest normalized error (M1) over the cases that ran at CRAM-{order} was {value} ({case})."
4. If any: "CRAM-48 held in {r} of the {b} cases where CRAM-16 broke: {cases}."

The per-case map (status, worst M1 and M2, the first failing step and nuclide, wall time) is in the verdict's
`limits_map`, and the handbook may quote any row of it.

## Not claimed

- Anything about nuclear data, spectra, self-shielding, energy range or charged particles. The cases are synthetic.
- That a regime not listed here holds.
- That the 10⁶-pulse trains run in full. They are truncated at 10³ pulses. The 10⁵-pulse scale case measures whether
  long explicit trains are practical.
