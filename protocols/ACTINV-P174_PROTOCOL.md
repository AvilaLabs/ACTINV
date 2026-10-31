# ACTINV-P174 — below-floor states in the activity, heat and dose totals (successor of P168's case 5d)

## Purpose

CRAM resolves a population only down to its numerical scale, `alpha0 x max(N)` (`numerical_floor_atoms_per_g`). Below
that scale a computed population is unresolved noise of either sign. ACTINV flags such states (`n_states_below_floor`)
but keeps their value in the activity, heat and dose totals. P168 found a case where that prints a wrong number a user
could act on. P168's case 5d is the motivating example. This protocol compares three repairs and today's behaviour on
the same cases, and adopts one by a rule fixed before any candidate code is written:

> Does reconstructing short-lived below-floor states from their production rate, or re-solving the step at CRAM-48, or
> reporting below-floor states as a bound, give totals at least as accurate as today's wherever today's are accurate,
> and correct them where today's are wrong, at acceptable cost?

The outcome is a decision (C, B, A in that order of preference, or the status quo stays), not a map. This protocol registers the cases, harness, checker
and rule. It contains no ACTINV source change; the candidate code is written after registration and is judged by it.

## Predecessor

P168 (ledger 286–290) checked the solver against 30-digit reference solutions on 19 synthetic cases at CRAM-16 and
CRAM-48. Case 5d, three radioactive members of equal half-life (1000 s) in a chain, broke at CRAM-16 only:
- In one 90,000 s step Cu-58 fell from a population near 10^20 atoms/g to a reference 1.9 x 10^-5. CRAM-16 left
  1.2 x 10^5.
- The step flagged it (`numerical_floor_atoms_per_g` 2.1 x 10^6, `n_states_below_floor` 1) and kept the value.
- The activity table reported 83 Bq/g where the reference total is 1.4 x 10^-8 Bq/g. CRAM-48 holds.

A code audit on 2026-10-30 found a related gap. The `screen` certificate (`options.screen`) widens `heat.*` by
`heat_bound_from_below_floor_W_per_g` but has no corresponding term for `activity.total` and `activity:<nuclide>`. In
5d's status quo the certificate's lower edge (the nominal total, 83 Bq/g) lies above the true total.

## Disclosure of what has been seen

- All of P168's evidence (`results/p168_limits.json`, `results/p168_verdict.json`, ledger 288), including the 38 P168
  runs' status, worst M1 and M2, and wall times (3 ms to 6.5 s). Seen with it: cases 6b and 7 "break" at both orders for
  reasons the ledger traces to the harness (6b: bulk nuclides absent from `inventory`) and to M2 having no absolute floor
  (7: populations of 10^-94 atoms/g after a year of cooling). They are not defects of the solver proper and this protocol
  does not repair them.
- The 30-digit references of P168 (`results/p168/refs`) were read for 5d and in part for the decay cases.
- **No ACTINV output for any held-out case, and none for any P168 case under an A or B policy, has been seen.** The
  harness and the checker have never run against a binary. Their `--self-test` modes use synthetic records only.
- The eight held-out references were computed once in a scratch directory to check the harness (`make-refs`, mpmath 1.3.0).
  The author read, for the decay cases and the two natural chains, the reference populations against the CRAM-16 scale (alpha0 x the largest
  population) to confirm that each case reaches it:
  - h1 has Mn-62 within a factor of 2.1 above the scale at 51 half-lives and every radioactive member far below it at 151;
  - h2 has Tc-90 at about half the scale at 63 half-lives, carrying nearly all the activity there;
  - h3 has every radioactive member far below it at 90.1 and 190.1 half-lives, and h5 at its last step;
  - h4's Co-58m1 is below it at 1 h and 1 d;
  - h7 has, from 100 y on, between four and eleven radioactive members below the scale (Po-214, Po-218, At-218, Tl-210
    and Tl-206 among them to 1e6 y), and h8 has Rn-220, Po-216, Po-212 and Tl-208 below it at every step.

  Those values are references, not ACTINV results. The case definitions were written before the references were
  computed and none was altered after this reading. The registered references are regenerated from the harness before
  registration (see Commands).
- **C's threshold (`lambda x step length >= 50`), its rate-separation ratio (50), its memory guard (1e-6), its
  reconstruction formula and its fallback to B were fixed before any run of any candidate.** Two conditions were added
  to the first statement of C on reading it against the cases, by reasoning and not by any ACTINV result:
  - the memory guard, because without it C would set Co-58, Ni-58 and Cu-58 to zero in 5d and leave 5d unfixed and
    uncovered;
  - rate separation, because with equal half-lives quasi-steady state is invalid however large lambda x step length is
    (h2 step 3, 5d).
- The author's expectations, stated so they can be judged. They are predictions, not results:
  - A loses totals that are accurate today wherever a short-lived daughter sits below `alpha0 x max(N)`. Natural decay
    chains are the extreme case: N(Po-214)/N(U-238) is about 1e-21 and Po-214, Po-218, Rn-220, Po-216 and Po-212 carry
    a large share of the alpha activity. A is expected to fail the actinide guard on h7 and h8 and the no-loss items.
  - h2 step 3: Tc-90 has lambda x step length of 41.6, under the threshold, has equal half-life with its ancestors (so
    it also fails rate separation), and carries nearly all the activity. C sends it to the CRAM-48 refinement and
    should then match B there.
  - 5d: the memory guard sends Co-58 to the fallback, which is the refinement; C should match B on 5d.
  - h7 and h8: the short-lived daughters are separated from their resolved ancestors by many orders of magnitude, so C
    should reconstruct them at CRAM-16 without refining where the chains are in equilibrium. Steps where an upstream
    state has not yet relaxed fall back to B's refinement.
  - B passes by refinement; C should pass wherever B passes, and avoid the refinement cost wherever the
    quasi-steady-state conditions hold.

## Candidates (fixed now)

All four arms run from one binary. The arm is chosen by the problem's `options.below_floor`, a new optional string:

| arm | `options.below_floor` | meaning |
|---|---|---|
| `status_quo` | absent (or `"report"`) | today's behaviour; the field is never serialized when `"report"`, so existing output is unchanged |
| `A` | `"bound"` | report below-floor states as a bound |
| `B` | `"refine"` | refine the step at CRAM-48, then bound |
| `C` | `"equilibrium"` | reconstruct short-lived below-floor states from their production, bound the rest |

Why a problem field and not an environment variable: it is visible in the problem file and in the spec echo, it reaches
the CLI, the Python module and the browser application alike, and the harness cannot leave it set by accident. The field
is skipped when `"report"`, as `options.gas` is when false, so every existing problem, echo and fingerprint is unchanged.
Any other value is a validation error.

### What the policies do

A **state is below the floor** when `0 < N < numerical_floor_atoms_per_g` of the solve that produced the step, and it is
not the leakage, unit-source or removal state: exactly the states `n_states_below_floor` counts today. (States zeroed for
being negative, and exact zeros, are outside the count today and stay outside.)

- **A.** Below-floor states leave the activity, heat and dose responses of the step: their reported amount there is 0.
  Their populations stay in `inventory` and in the state vector, and evolve into later steps as before. The step reports
  the bound their true contribution cannot exceed:
  - activity: `sum_i lambda_i x floor`, over the below-floor states;
  - heat: `sum_i lambda_i x floor x (e_light + e_em + e_heavy) x EV`;
  - dose, for each radiological response: `sum_i lambda_i x floor x k_i`, with `k_i` the response's per-unit-activity
    coefficient for nuclide i.

  The bound also widens the `screen` certificate: its `upper` edge for `activity.total`, `activity:<nuclide>` and
  `heat.*` adds the below-floor bound. The existing field `heat_bound_from_below_floor_W_per_g` keeps its meaning
  (the below-floor subtotal `sum lambda_i N_i E_i`) under every policy.
- **B.** As A. In addition, when a CRAM-16 step's activity bound exceeds `rel_tol = 1e-6` of the step's reported
  activity total (zero total counts as exceeded), that step is re-solved at CRAM-48 from the same start state, and the
  CRAM-48 result becomes the step's state, so later steps start from it. Its own, lower floor then applies and the bound
  is recomputed. If the bound is still above `rel_tol` of the total, A's treatment stands and the run's ledger names the
  step. A run already at CRAM-48 cannot refine; its material bounds are named in the ledger.

- **C, equilibrium reconstruction, falling back to B.** After a step's solve, the below-floor states are processed in
  topological order of the transmutation graph, parents first. Let `A_step` be the matrix the step was solved with: its
  entry `A_dj` (j != d) is the rate at which state j feeds state d (`b_{j->d} lambda_j` for decay, with the branching
  ratio of the mode that reaches d, plus `sigma_{j->d} phi` for reactions, collapsed over the groups with the step's flux
  and multiplier), and `-A_dd = rho_d` is d's total removal rate (`lambda_d`, plus the reaction loss `sigma_loss phi`,
  plus any first-order `removal` rate of the step). The unit-source state of trace mode and the feed source of a `feed`
  step are parents like any other (N = 1 for the unit state; a feed adds its rate to `P_d`). Leakage and removal sinks
  are never parents or members.

  A below-floor state d is **reconstructed** when all five conditions hold:
  1. **Relaxed:** `lambda_d x step length >= 50` (`RECONSTRUCT_THRESHOLD`).
  2. **Parents known:** every parent j with `A_dj > 0` and `N_j > 0` is resolved (not below the floor) or has itself been
     reconstructed. Otherwise d falls back; counted in `n_parent_fallback`.
  3. **Not in a cycle:** d does not belong to a strongly connected component of the graph with more than one state
     (self-loops are not cycles). Every below-floor state in such a component falls back; counted in `n_cycle_fallback`.
  4. **No memory:** with `P_d = sum_{j != d} A_dj N_j`, using the parents' end-of-step values, and
     `N_rec = P_d / rho_d`, the state's start-of-step value decayed over the step, `N_d(0) exp(-rho_d x step length)`, is
     at most `1e-6 x N_rec` (at most 0 if `P_d = 0`). A state that fails this still remembers its initial inventory, so
     production alone does not determine it; counted in `n_memory_fallback`.
  5. **Rate separation:** `rho_d >= 50 x rho_a` for every **nearest resolved ancestor** a of d: the resolved states
     that feed d along some path whose intermediate states are all reconstructed (walk up from d through reconstructed
     parents until a resolved state is reached; states with `N = 0` feed nothing). Reconstructed intermediates track
     their production instantly, so it is the slow resolved ancestor that sets how fast `P_d` changes, and that is what
     the daughter's removal must outpace. Equal half-lives never pass, however large `lambda x step length` is.
     Counted in `n_separation_fallback`. (Example: Pb-214's parent Po-218 is faster than Pb-214, but Po-218 is
     reconstructed, so the ancestor tested is Rn-222, against which the ratio is about 200. Measuring against Po-218
     would wrongly reject Pb-214.)

  A reconstructed state's reported amount is `N_rec`. It enters the activity, heat and dose totals like a resolved
  state, appears in `inventory` with that value, and the value replaces the solved one in the state carried to the next
  step. The floor of the step is unchanged.

  **A state that fails any condition gets B's treatment.** After the reconstruction pass the remaining (fallback)
  states leave the totals and their bound is computed. If that bound exceeds `rel_tol = 1e-6` of the step's reported
  activity total (zero total counts as exceeded) and the step was solved at CRAM-16, the step is re-solved at CRAM-48
  from the same start state, and the reconstruction pass is applied again to the CRAM-48 result with the same five
  conditions against that solve's own floor. States still below the CRAM-48 floor and not reconstructed get the bound,
  and a bound still above `rel_tol` is named in the run's ledger. A run already at CRAM-48 cannot refine; its material
  bounds are named in the ledger. So C contains B: wherever no state falls back, C pays nothing for a refinement; wherever
  the quasi-steady-state conditions fail, C does what B does. The scheme is a quasi-steady-state approximation; it errs
  by the relative change of the parents over the time `1/rho_d`, and the cases test it rather than assume it.

### Output contract (what the harness reads)

When the policy is not `"report"`, each step carries

```text
below_floor_bound: { policy, order, refined, n_states_excluded, numerical_floor_atoms_per_g,
                     activity_Bq_per_g, heat_W_per_g, dose }
```

For C the block also carries `n_reconstructed`, `reconstructed_states` (the ACTINV names of the reconstructed states),
`reconstruct_threshold` (50), `n_cycle_fallback`, `n_parent_fallback`, `n_memory_fallback` and `n_separation_fallback`,
and `refined` / `order` as for B; the run's `ledger.below_floor` carries `reconstruct_threshold` too. `n_states_below_floor` keeps counting every state that was
below the floor before treatment, so it equals `n_states_excluded + n_reconstructed`.

`order` is the CRAM order of the solve that produced the step's state (48 for a refined step), `refined` is true only for
a B or C step re-solved at CRAM-48, `numerical_floor_atoms_per_g` equals the step's own field of that name (which
reports the solve actually used), and `dose` maps radiological response ids to bounds (null without a radiological
section). `n_states_excluded` counts the states that left the totals (for C, the bounded-only states), and the bound
sums over those only. The
run's `ledger.below_floor` carries `{policy, rel_tol, refined_steps, unresolved_steps: [{step, ...}]}`, steps named by the
`step` value of the step they describe. The status quo carries none of this. Step numbers and `inventory` are unchanged
by A; B changes `inventory` only in refined steps; C changes it in refined and reconstructed steps.

## Cases

All run twice, at `cram_order` 16 and 48, under each arm. Options `prune: none` and `bmin_atoms_per_g: 0` (as P168),
`outputs: [inventory, activity, heat, ledger, certificate]`.

### Selection set

P168's 19 stress cases with their references, unchanged: `results/p168/refs`, pinned by `results/p168/refs_manifest.json`,
built by `controls/p168/to_actinv.py` (`controls/p168/cases.py` lists them). Their problems are the same except for two
additions:
- the decay overrides below, so that heat is a response;
- the option fields of the arm.

### Held-out set (written now, not run before registration)

Eight cases with the same reference method as P168 (mpmath at 300 digits, 500 for h7 and h8, each cross-checked at twice that; 30 digits recorded, half-lives
at most 7 digits so that an ENDF 11-column field carries them exactly, inputs as decimal strings). Mass 1 g, atoms/g
basis, one flux group. Decay data are synthetic (ENDF-6 MF=8/MT=457, daughters from RTYP and RFS), reactions
one-group (P166 layout, bounds [1, 3] eV, flux multiplier = rate). Every step is a scored step. "decay" below means flux
multiplier 0.

**h1_triple_T10s.** Mn-62 -> Fe-62 -> Co-62 -> Ni-62 (stable), beta-minus, branching 1; every radioactive member has
half-life 10 s (a Jordan block of size 3). Initial: Mn-62 1e22 atoms/g. Decay steps 10, 50, 150, 300, 1000 s
(cumulative 1, 6, 21, 51, 151 half-lives).

**h2_triple_T3600s.** Nb-90 -> Mo-90 -> Tc-90 -> Ru-90 (stable), beta-minus, branching 1; half-life 3600 s each.
Initial: Nb-90 1e20. Decay steps 3600, 7200, 216000, 72000, 360000 s (cumulative 1, 3, 63, 83, 183 half-lives).

**h3_triple_T1e6s.** Sb-124 -> Te-124 -> I-124 -> Xe-124 (stable), beta-minus, branching 1; half-life 1,000,000 s each.
Initial: Sb-124 1e24. Decay steps 1e5, 4e7, 5e7, 1e8 s (cumulative 0.1, 40.1, 90.1, 190.1 half-lives).

**h4_isomer_cooled_1d.** Co-58m1 (ZA 27058, LISO 1; synthetic half-life 60 s) -> Co-58 g (half-life 6,122,304 s =
70.86 d) by isomeric transition, branching 1; Co-58 g -> Fe-58 (stable) by EC/beta-plus, branching 1. Initial: Co-58m1
1e20 atoms/g (the pumping is not simulated), Co-58 g 0. Decay steps 600, 3000, 82800 s (cumulative 10 min, 1 h, 1 d).

**h5_pulsed_1s_cool.** Ir-191 (stable) (n,gamma) Ir-192, sigma 100 b, flux 1e18 n/cm2/s; Ir-192 (synthetic half-life
0.01 s) -> Pt-192 (stable), beta-minus. Initial: Ir-191 1e22. 20 pulses, each an on-step of 0.001 s (flux multiplier 1)
and an off-step of 0.099 s (multiplier 0); then one 1 s cooling step (multiplier 0, 100 half-lives). 41 steps.

**h6_trace_near_floor.** Trace mode, flux 1e14 n/cm2/s, bulk Mn-55 1e22 atoms/g with impurities Co-59 3e6 and Sc-45 2e5
atoms/g. Reactions, all (n,gamma) MT 102: Mn-55 -> Mn-56 13 b; Co-59 -> Co-60 37 b; Sc-45 -> Sc-46 27 b. Decays, all
beta-minus, branching 1: Mn-56 (9284.04 s) -> Fe-56; Co-60 (166,352,700 s, 5.2714 y to 7 digits) -> Ni-60;
Sc-46 (7,239,456 s = 83.79 d) -> Ti-46; Fe-56, Ni-60, Ti-46 stable. Steps: irradiate 3600 s (multiplier 1), then decay
3600, 82800, 777600 s. The reference holds Mn-55, Co-59 and Sc-45 constant (trace mode: loss terms removed, production
kept), as P168's 6b. After the irradiation the CRAM-16 scale is about 8.7 atoms/g, between Sc-46 (1.9) and Co-60 (40):
a real product on each side of the floor, within a factor of 10.

**h7_u238_chain_real (the actinide guard).** The U-238 series to Pb-206 with ENDF/B-VIII.0 half-lives and branchings,
read from `actinv-data/v1.2.0/decay/endf-b-viii-0_decay.dat` (MF8/MT457, the decay data ACTINV ships) and written into
`H7_NUCLIDES` and `H7_DECAYS` in the harness. 19 nuclides, half-lives in s:

| nuclide | half-life | decay (branching) |
|---|---|---|
| U-238 | 1.40999e17 | alpha to Th-234 |
| Th-234 | 2082240 | beta-minus to Pa-234m |
| Pa-234m | 69.54 | beta-minus to U-234 (0.9984), IT to Pa-234 (0.0016) |
| Pa-234 | 24120 | beta-minus to U-234 |
| U-234 | 7.74739e12 | alpha to Th-230 |
| Th-230 | 2.37881e12 | alpha to Ra-226 |
| Ra-226 | 5.04922e10 | alpha to Rn-222 |
| Rn-222 | 330350.4 | alpha to Po-218 |
| Po-218 | 185.88 | alpha to Pb-214 (0.9998), beta-minus to At-218 (0.0002) |
| At-218 | 1.5 | alpha to Bi-214 |
| Pb-214 | 1608 | beta-minus to Bi-214 |
| Bi-214 | 1194 | beta-minus to Po-214 (0.99979), alpha to Tl-210 (0.00021) |
| Po-214 | 0.0001643 | alpha to Pb-210 |
| Tl-210 | 78 | beta-minus to Pb-210 |
| Pb-210 | 7.005787e8 | beta-minus to Bi-210 |
| Bi-210 | 433036.8 | beta-minus to Po-210 (0.9999987), alpha to Tl-206 (0.0000013) |
| Po-210 | 1.195569e7 | alpha to Pb-206 |
| Tl-206 | 252.12 | beta-minus to Pb-206 |
| Pb-206 | stable | |

Left out of the chain because the synthetic writer cannot express them (RTYP 1.5 and 6) or because they are under
1e-7: spontaneous fission of U-238, U-234, Th-230 and Th-232; Tl-210 beta-neutron emission (8.5e-5); Pb-210 alpha
(1.9e-8); At-218 beta-minus (1e-3). The dominant branch of each takes the remainder. Bi-210's alpha branch
(1.32e-6 in the file) is written 1.3e-6 so that its complement is a 7-digit field. Initial: U-238 2.5298e21 atoms/g (1 g
pure). Decay steps 3,155,760,000; 312,420,240,000; 2,840,184,000,000; 28,401,840,000,000 s (100 y, 9900 y, 90,000 y,
900,000 y; cumulative 100 y, 1e4 y, 1e5 y, 1e6 y with the 365.25-day year). The reference is computed at 500 digits.
Ra-226 onward reaches equilibrium with Th-230, and the short-lived members fall below the floor.

**h8_th232_chain_real.** The same for the Th-232 series to Pb-208 (12 nuclides): Th-232 4.43384e17 s alpha;
Ra-228 1.814562e8 beta-minus; Ac-228 22140 beta-minus; Th-228 6.033813e7 alpha; Ra-224 316224 alpha; Rn-220 55.6
alpha; Po-216 0.145 alpha; Pb-212 38304 beta-minus; Bi-212 3633 beta-minus to Po-212 (0.6406) and alpha to Tl-208
(0.3594); Po-212 2.99e-7 alpha; Tl-208 183.18 beta-minus; Pb-208 stable. Initial: Th-232 2.5953e21 atoms/g. Decay steps
315,576,000; 2,840,184,000; 28,401,840,000; 284,018,400,000 s (10 y, 90 y, 900 y, 9000 y; cumulative 10 y to 1e4 y).

The case definitions are `HELD_OUT` in `controls/p174_floor.py`. Changing anything there changes the protocol.

### Responses and energies

The synthetic decay files carry no decay energies. The harness therefore writes an `actinv-decay-overrides-1` document
(`decay.overrides`) giving every radioactive synthetic nuclide `e_light_eV` 1e5, `e_em_eV` 2e5, `e_heavy_eV` 3e5, so that
reference heat is `sum lambda R x 6e5 eV x 1.602176634e-19 J`. The responses scored on the synthetic cases are the total
**activity** (`sum of activity_Bq_per_g`) and the total **heat** (`heat_W_per_g.total`). The contact dose needs a
radiological table, which the synthetic cases do not have, so it is **not scored on them**; see Disclosures.

### Nonregression set

Every tracked JSON file under `examples/` or `controls/` whose top level is an `actinv-spec-1` problem
(`git ls-files`; 24 at the base commit). Those at the top level of `examples/` are the *public* examples and must all
run; the others are *fixtures* and are listed, not failed, if the master binary cannot run them. Each runs once on the
master binary and `NONREG_REPEATS` (3) times under each arm of the candidate binary, with `options.below_floor` set in
a copy of the problem for A and B, from the repository root.

## Metrics, per case, order, step and response

R is the 30-digit reference. For a response, T_ref is its total from R, T the total ACTINV reports.
- **M_tot:** |T - T_ref| / T_ref (infinite if T_ref is 0 and T is not). Responses: activity, heat. A total **holds**
  when M_tot <= `TOL_TOTAL` = 1e-6.
- **Covered:** |T - T_ref| <= the reported bound for that response (0 under the status quo).
- **Fixed:** holds, or covered.
- **Bound validity:** the reported bound is at least the reference contribution of the excluded states
  (`sum lambda_i R_i` over the nuclides whose inventory entry is below `numerical_floor_atoms_per_g` of the step), to
  within 1e-12 of itself, and the same for heat. "Never violated" means this holds at every scored step of every run.
- **Bound usefulness:** bound divided by the reported total (reported, not gated).
- **Cost:** median wall time over `REPEATS` (5) runs of the whole `actinv run`, as a ratio and a difference against the
  status quo. The first run is the one scored. Arms are interleaved within each repeat.
- **Measurable:** a wall-time difference is measurable if it exceeds max(`NOISE_S` = 20 ms, `NOISE_FRACTION` = 10% of
  the status quo's median). The 2x limit below is waived where the excess is under 20 ms: below that
  a ratio measures process start-up.

Screen certificates: for 5d and the eight held-out cases a further run per order and arm sets `prune: rate` and
`options.screen.bmin_atoms_per_g` 1e-8. The certified `activity.total` and `heat.total` interval must contain T_ref at
every scored step, to 1e-9 of T_ref. The status quo's certificates are recorded, not required.

## Frozen inputs

| input | pin |
|---|---|
| P168 references manifest `results/p168/refs_manifest.json` | `998cff380f68d808ffecb45386419fecd8e568798ae4b01b00568835908c7974` |
| held-out references manifest `results/p174/refs_manifest.json` | `748a344c88af6155667ea173c1f10b5afa081c0f6cf73a55862ef8e1fdf5b63b` (made by `p174_floor.py make-refs` before registration; also in `PINS` of `controls/check_p174.py`) |
| master binary: the P163 release build | `186cc2587bb61766b3b3c426c61cd44f00ed472401026dea2ca3dd595c8d1194` |
| registration base commit | `b1a1cc4` |

The P163 binary was built from d6f516b. The crates tree of the base commit differs from d6f516b's only in
`crates/actinv-gui`, which this binary does not contain, so the binary is the master solver. It is the *status quo
reference* of G4: the candidate binary's status quo arm must equal it.

The candidate binary is built after registration from the evidence commit. It is not pinned here, because it does not
exist. The record carries its SHA-256, the evidence commit and its crates tree, and the SHA-256 of every `crates/` file
(except the GUI crate) that differs from `b1a1cc4`; G1 checks them against the tree.

The protocol, the harness (`controls/p174_floor.py`) and the checker (`controls/check_p174.py`) are hashed in
`protocols/protocol_hash.txt` at registration (G0). The harness is also imported by the checker for its constants and
case lists, and its hash at run time is recorded and must equal the registered one.

## Decision rule

Per candidate X in {A, B, C}, the gates G2, G3 and G4 below are evaluated separately, and all arms are reported
whatever the outcome. The decision is ordered and fixed now:

1. **C is adopted** if C passes G2, G3 and G4. (If B passes too, C is still adopted: it contains B, costs less where
   quasi-steady state holds and fixes the cause rather than the symptom.)
2. Otherwise **B is adopted** if B passes G2, G3 and G4.
3. Otherwise **A is adopted** if A passes G2, G3 and G4. A's G3 includes the actinide guard like every candidate's, so A
   is adoptable only if it keeps h7's and h8's totals.
4. Otherwise **the status quo stays**, with the finding documented in data-limits.

The verdict is P174-PASS if G0, G1 (and G5, externally) hold, whatever the decision.

## Gates

- **G0.** This protocol's SHA-256 (and any amendment's), the harness's and the checker's are in
  `protocols/protocol_hash.txt`.
- **G1.** Limits and environment:
  - both references manifests equal their pins and every reference file reproduces its manifest hash;
  - the recorded constants equal the registered ones (`constants()` in the harness);
  - the master binary is the pinned one, the candidate binary differs from it, and every run used the recorded binary;
  - the record's candidate-source hashes equal the files in the tree, and its crates tree is its commit's;
  - the tracked tree was clean and the run was in the bounded cgroup;
  - every registered run (27 cases x 2 orders x 4 arms, plus the 9 screened cases' screen runs: 5d and the eight held-out cases) is in the record exactly
    once, with wall times, and the record's examples are exactly the tracked `actinv-spec-1` problems.
- **G2, selection set.** For each candidate:
  - every run ran (exit 0, within the 1,800 s limit);
  - the **contract** of Output contract holds at every scored step:
    - `below_floor_bound` present with the right policy, finite and nonnegative, zero if no state is excluded;
    - the state counts agree (inventory entries below the floor and not reconstructed, plus the reconstructed ones, equal
      `n_states_below_floor`; the first equals `n_states_excluded`);
    - no excluded nuclide appears in the activity table, and every other radioactive nuclide's entry equals
      lambda x N to 1e-9;
    - the floor is at least alpha0(order) x the largest inventory entry;
    - `ledger.below_floor.refined_steps` is the set of steps flagged refined;
    - B and C refine exactly when they should: a CRAM-16 step left unrefined has a bound at most `rel_tol` of its total,
      a refined step is a CRAM-16 step re-solved at CRAM-48, and a bound that is still above `rel_tol` after the final
      solve is named in `unresolved_steps`;
    - C's reconstructed states each have `lambda x step length >= 50` and meet rate separation against their nearest
      resolved ancestors (recomputed by the checker from the reference's decays, reactions and the step's flux
      multiplier; pulse-train schedules are not recomputed), their count and names agree between the block and the
      inventory, the threshold is 50 in the block and the ledger, a below-floor state that met the threshold and was not
      reconstructed has a fallback counted, and A and B reconstruct nothing;
  - **bound validity** at every scored step;
  - **5d is fixed:** under the status quo, 5d at CRAM-16 has a step with a flagged state and an activity total off by more
    than `TOL_TOTAL` (the defect reproduces), and under the candidate every step's activity and heat totals are fixed;
  - **nothing is lost:** at every case, order, step and response where the status quo holds, the candidate holds;
  - the screen certificate of 5d contains the reference.
- **G3, held-out set.** For each candidate: the contract, bound validity and certificate containment as above, nothing
  lost, and the **actinide guard:** on h7 and h8, at both orders and every step, the activity and heat totals are within
  `TOL_TOTAL` of the reference (not merely covered by a bound): no real activity is lost. For B also:
  - M_tot of B is at most A's at every step and response (to 1e-9 relative).

  For B and C:
  - **cost where it triggers:** on every case-order and example where B or C re-solved a step, median wall time at most
    2x the status quo's, or an excess below `NOISE_S`;
  - **cost where it does not:** on every other held-out case-order and example, the median excess over the status quo is
    not measurable.
- **G4, nonregression.** For each candidate, on every runnable example: the candidate binary's status quo equals the master
  binary in `steps[*]` (added keys ignored, everything else exactly), its repeats are identical, and each arm runs
  cleanly with the same number of steps. A differs from the master only in steps the master flagged
  (`n_states_below_floor > 0`); B and C only in the first such step and later ones. All public examples must run. At least one
  example must run.
- **G5.** CI green on the pushed evidence commit.

## Allowed sentences

Filled from the computed numbers only, as `controls/check_p174.py` writes them:

1. (only if B or A is adopted) "ACTINV no longer reports activity from states below its solver's numerical resolution; it
   reports their bound."
   (only if C is adopted) "ACTINV reconstructs short-lived nuclides below its solver's numerical resolution from their
   production rate, and reports a bound for the rest."
2. "On P168's {n} case-orders, {k} had an activity or heat total outside 1e-06 of the reference at some step under the
   status quo." and, for each candidate, "Candidate {X} brought {j} of those {k} within 1e-06 of the reference or inside
   its reported bound."
3. "On the {h} held-out case-orders ({s} scored steps, {f} with a below-floor state under the status quo) the bound
   problems were {a} for A, {b} for B and {c} for C; B re-solved {r} steps at CRAM-48 and C reconstructed {q} states,
   at a largest wall-time ratio of {x} against the status quo for B."
4. (only if the status quo stays) "No candidate met the decision rule ({gates}); below-floor states stay in the
   totals, flagged by n_states_below_floor, and the finding is documented in data-limits."

No sentence may call a code, library or setting better or worse than another. A speed statement beyond sentence 3 is not
made. The handbook may quote any gate item of the verdict.

## Disclosures

- The `numerical_floor_atoms_per_g` field keeps its name and meaning (the spec's "numerical floor", `alpha0 x max(N)`,
  explicitly not a bound on total numerical error). The new bound is a bound on the *contribution of the excluded
  states* under the assumption that a state below the floor is at most the floor. It is not a bound on the error of the
  kept states, and a state below the floor can in principle be larger than the floor in truth; bound validity is
  therefore tested against references, not assumed.
- Reported values change only for steps that have below-floor states, which today are flagged. For B, the steps after a
  refined step start from the refined state; for C, from the reconstructed values.
- C's reconstruction is a quasi-steady-state approximation with a threshold, a separation ratio and a memory guard chosen by the
  author, not derived. The thresholds (50, 50 and 1e-6) and the formula are registered unchanged whatever the
  cases show; a different value would be a different candidate under a successor protocol.
- **Dose.** Contact dose, like any radiological response, is linear in per-nuclide activity, so it has the same
  treatment and bound. The synthetic cases carry no radiological table, so dose is not scored by M_tot or bound validity
  here. It is covered by the candidate's own unit test (registered at candidate time) and, where an example carries a
  radiological section, by the unchanged-`steps[*]` comparison of G4.
- **"Holds today" is defined on totals,** not on P168's inventory metrics (M1, M2). A candidate that leaves the inventory
  alone cannot break an inventory metric, so that definition would make the no-loss test empty. Hence a candidate that
  excludes an accurate small state and moves a total by more than 1e-6 has lost it, even if its bound covers the change.
  The draft of this protocol applied the no-loss test to the selection set only; the held-out set's nothing-lost item is
  an addition of this registration.
- The cost limits (2x, with a 20 ms and 10% measurable threshold) are as the draft's "2x" and "nothing measurable" made
  numerical. Most synthetic cases run in a few milliseconds, so their wall times are dominated by process start-up; the
  examples, which run for seconds, are the cost probe that carries weight.
- The master binary (P163) is older than the registration commit by P168–P172, which touched no solver source
  (`crates/` differs only in the GUI crate).
- Cases 6b and 7 have the P168 harness-convention and M2-floor breaks (see above). Under this protocol's totals-based
  definition they are classified on their totals; 7's dust at CRAM-48 (about 10^-14 atoms/g against a reference total of
  3.8 x 10^-99 Bq/g) is a total the status quo already does not hold, so a candidate cannot lose it.
- Mesh and study problems, and problems of other `spec` values, are outside the nonregression set.

## Not claimed

- Anything about nuclear data, spectra or the physics limits P168 named as outside its scope.
- That a regime not listed here holds.
- That the floor is the right threshold. The protocol tests whether acting on it repairs the totals without harm; it does
  not test a different threshold.
- Speed beyond the stated cost items.

## Commands (coordinator)

Before registration (cheap, no ACTINV binary):
1. `python3 controls/p174_floor.py make-refs` writes `results/p174/refs` and `results/p174/refs_manifest.json`
   (mpmath 1.3.0, about 35 s). Put the manifest's SHA-256 into the table above and into `PINS` of `controls/check_p174.py`.
   Commit the references (they are JSON, not archives).
2. `python3 controls/p174_floor.py --self-test` and `python3 controls/check_p174.py --self-test`.
3. Append to `protocols/protocol_hash.txt` the SHA-256 lines of this protocol, `controls/p174_floor.py` and
   `controls/check_p174.py` (after the last edit of any of them).

After registration, inside the AGENTS.md scope, one job at a time:

4. Write the candidate (`options.below_floor`, the output contract above) in `crates/`, with its tests. Build:
   `cargo build --release -p actinv-cli`.
5. `ACTINV_BIN=target/release/actinv ACTINV_BASE_BIN=<P163 binary> python3 controls/p174_floor.py all` writes
   `results/p174_floor.json` (the solver section takes minutes; the nonregression section runs 24 problems 10 times each).
6. `python3 controls/check_p174.py` writes `results/p174_verdict.json`; `--no-write` compares.
7. Local gates before pushing the evidence commit: manifest refresh, `cargo fmt --all -- --check`, `cargo check -p
   actinv-core -p actinv-cli --tests`, `controls/check_public_examples.py`.
