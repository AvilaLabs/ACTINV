# ACTINV P23 — demand-led capabilities: feed/removal, reverse calculation, damage observables

Opened 2026-09-10 under maintainer direction. The post-v1 competitive benchmark CB1
(`docs/COMPETITIVE_BENCHMARK.md`, protocol `627990751a4730fe22e457ea2fa334fca25ae0eae7f463c8677e488e5dbb7398`)
recorded three confirmed-absent capability axes for ACTINV: continuous feed/removal (held by SCALE/ORIGEN and
OpenMC), reverse calculation (held by ALARA), and damage observables (held by FISPACT-II and OpenMC). CB1 parked
them as demand-led candidates. On 2026-09-10 the maintainer supplied the demand and directed this phase.

P18b remains open with G0/G1 committed and green and its G2 controls staged but uncommitted since 2026-08-29. The
one-phase-at-a-time rule is relaxed for this ordering by explicit maintainer direction, recorded in the ROADMAP
changelog entry dated 2026-09-10. P18b's gates, seals and staged controls are untouched by this phase; nothing here
reads, modifies or depends on P18b evidence.

The comparator release is v1.0.1 (signed tag `v1.0.1`, commit `0332779401363d2f39722efe7a0b7218afcfb270`) plus the
post-release usability work on master. A successful P23 authorizes only an additive minor release.

## Frozen scope — what may change

1. `schedule[]` steps gain optional `feed` and `removal` maps. `feed` names explicit nuclide keys (`"Co60"`,
   `"Ta180m1"`) with constant feed rates in atoms s^-1 g^-1 of material; `removal` names nuclide keys or element
   keys (`"Fe"` expanding to every tracked state of that element) with first-order removal constants in s^-1.
   Both default to absent, which preserves v1.0.1 behavior byte-for-byte.
2. A new `removed` sink state exists exactly when a removal term is declared, reported per step as
   `removed_atoms_per_g`, parallel to `leakage_atoms_per_g`. It never produces.
3. `actinv reverse PROBLEM MEASUREMENTS [OUT]` and Python `actinv.reverse(problem, measurements)`: flux-estimation
   from measured activities in the linear (trace) regime, scalar normalization always, per-segment multipliers by
   request. It consumes the existing run path; it changes no solver internals.
4. A `damage` spec section folding a hash-pinned `actinv-damage-table-1` (per-target groupwise damage-energy
   production cross sections) against per-step target inventories, with explicit per-element displacement energies
   and NRT-dpa reporting. A new `actinv build-damage` command produces such tables from TENDL MF=3/MT=444 sections
   through the existing reconstruction and collapse pipeline, with per-source SHA-256 provenance.
5. Ledger, certificate, CLI, Python, prepared, mesh and GUI surfaces record the new features; Python object API
   gains matching `Schedule` methods.
6. Documentation: SPEC, METHOD, QUANTITIES, README, python/README, and a worked example per feature under
   `examples/`.

## Frozen scope — what may not change

- Evaluated nuclear data, decay constants, group boundaries, CRAM kernels, pruning significance semantics,
  covariance values, or any public v1.0.1 artifact.
- Existing result bytes: any specification that omits the new fields must produce results identical to the parent
  commit's on the frozen identity battery. No field is silently accepted, defaulted or ignored.
- No `unsafe`, no new runtime dependencies, no `Arc`/`Mutex`/interior mutability or clone-allocation borrow-checker
  workarounds. NNLS is implemented from the published Lawson–Hanson algorithm on the standard library only.
- No silent omission: unresolvable feed/removal keys, unsupported reverse regimes, missing displacement energies and
  uncovered damage targets all fail closed or enter the named ledger — never a zero.
- Feed/removal do not enter pathway attribution; pathway analysis remains the production-chain ranking and is
  explicitly unchanged when feed/removal are present (pathway output is suppressed with a named ledger reason rather
  than silently mis-attributing fed inventory).

## Frozen semantics

### Feed and removal

During a schedule step, `feed: {"Nuc": F}` adds the constant source F atoms s^-1 g^-1 to nuclide Nuc, implemented as
the triplet `(state(Nuc), unit, F)` — the same mechanism trace mode already uses for constant bulk production. The
`unit` state holds 1.0 for the whole solve whenever any feed exists, in both modes. Feeding a nuclide that is also a
material constituent is legal in both modes: the tracked state holds only the fed/produced population, and reported
inventory continues to add the constant reservoir contribution exactly as v1.0.1 does.

`removal: {"NucOrElement": r}` adds first-order loss r s^-1 during the step: triplets `(removed, K, r)` and
`(K, K, -r)` for every resolved tracked state K. Element keys expand over all tracked states of that element present
in the chain. Removal targets only tracked states: the constant bulk reservoir of trace mode is outside removal's
reach, because the trace formulation defines it as undepleted. A removal key that resolves only to reservoir
nuclides is recorded in the ledger under `removal_reservoir_exempt` and otherwise accepted — removing produced or
fed inventory of the same nuclide remains modeled. Draining the reservoir itself requires `mode: "coupled"`.

Both sets of triplets join the reachability graph before pruning, so fed nuclides and the removed sink are kept;
removal diagonal contributions accumulate into the effective loss rate the pruner uses for bound estimates. Feed
rates and removal constants are step-local: they apply exactly during the declaring step and not during others.

Nuclide keys resolve through the same parser the material and radiological tables use; a key that names no chain
state and no reservoir nuclide is an error, not a skip.

### Reverse calculation

The measurement file has format `actinv-reverse-input-1`:

```json
{"measurements": [
  {"step": "last", "nuclide": "Co60", "activity_Bq_per_g": 4.2e6, "sigma_Bq_per_g": 1.0e5}
]}
```

`step` is a 1-based index or `"last"`. `sigma_Bq_per_g` is optional; omitted sigmas produce an unweighted fit.
Activities must be finite and nonnegative; sigmas finite and positive; duplicate (step, nuclide) measurements are an
error.

In the trace regime each measured activity is exactly linear in the flux normalization m:
`A_i(m) = m * a_i`, where `a_i` is the activity from one forward run at the declared schedule with all flux
multipliers replaced by 1. The scalar estimate is weighted least squares,
`m_hat = sum(w_i a_i A_i) / sum(w_i a_i^2)` with `w_i = 1/sigma_i^2` (1 for unweighted), standard error
`(sum(w_i a_i^2))^-1/2`, and a per-measurement residual table plus chi-square and degrees of freedom. If every
sensitivity coefficient is zero, or the problem resolves to `coupled` mode, or a measured nuclide is absent from the
computed inventory at that step, the command fails with a named error — never a silent zero weight or an iterated
nonlinear solve.

With `--segments`, each irradiation (flux > 0) schedule step gets an unknown multiplier; cooling steps are fixed at
zero. The sensitivity matrix column for segment k is the measured-nuclide activity vector from a forward run with
only segment k at multiplier 1 and all others at 0 — exact in the linear regime by superposition. The system is
solved by Lawson–Hanson NNLS. Fewer measurements than segments, a rank-deficient sensitivity matrix, or any segment
with an all-zero sensitivity column fails with a named error. The report carries per-segment estimates, the residual
table, chi-square, and the matrix condition number.

The result is `actinv-reverse-1` JSON: mode (`normalization`/`segments`), estimates, standard errors where defined,
sensitivity identities (problem and measurement hashes), per-measurement residuals, chi-square/dof, condition
number for segment mode, and explicit `method_limits` text. Python `actinv.reverse` returns the same mapping.
Reverse runs report the regime they used; they never relabel a coupled-mode problem as linear.

### Damage observables

`actinv-damage-table-1` is a JSON table: `format`, `source` provenance block (citation, edition, url) matching the
radiological convention, `projectile`, `group_structure` plus `boundaries_eV`, `units` fixed to
`"damage_energy_barn_eV_per_group"`, and `targets` mapping nuclide or element keys to per-group damage-energy
production cross sections in barn·eV. A target row's length must equal the group count. Missing groups, unknown
fields and malformed keys are errors.

The spec section is:

```json
"damage": {
  "table": {"path": "...", "sha256": "..."},
  "displacement_energy_eV": {"Fe": 40.0, "Cr": 40.0},
  "require_complete": false
}
```

`displacement_energy_eV` is mandatory whenever the table covers any target of that element; a covered element with
no declared energy is a named error, not a default. The displacement model is NRT: displacements per target atom per
second `= 0.8 * (damage energy rate in eV/s/atom) / (2 * E_d)`. Per step the run reports, inside a `damage` block:
`dpa_rate_per_s` and cumulative `dpa` for the material (atom-fraction-weighted over covered elements), per-element
rate and cumulative components, `damage_energy_eV_per_g_s`, and the uncovered-target ledger. `require_complete:
true` turns any uncovered active material target into a named error. Table targets absent from the material are
inert.

The fold uses each step's target inventory: constant reservoir composition in trace mode, evolved states in coupled
mode. `"damage"` joins `options.outputs` as an accepted token; a `damage` section without the token still computes
its block (consistent with ledger/certificate behavior), while the token without a section is an error.

`actinv build-damage TENDL_DIR OUT.json --projectile neutron --groups fispact-709 --temperature-K 293.6 [--cache DIR]`
processes every MF=3/MT=444 section in the named evaluation directory through the same resonance-reconstruction,
Doppler-broadening and group-collapse code paths as `build-library`, and writes an `actinv-damage-table-1` whose
provenance records every source file's SHA-256 and whose `uncovered` list names evaluations lacking MT=444. The
command never fabricates or zero-fills a missing section.

Mesh mode: the damage section applies per cell with that cell's flux, identical in structure to the radiological
fold.

## Frozen evidence and scoring rules

All controls are independent implementations under `controls/` importing no production, audit or scoring module —
the established pattern. Analytic expectations are exact where the physics allows; reference-bound where it does
not. No control's premise may be changed after seeing its result; a wrong premise is corrected by append-only
amendment with the numbers that showed it. Every fixed battery is named in the protocol text, not chosen afterward.

The identity battery is: the released FNS iron public example (`examples/fns_fe_5min.json`) plus the P16-style
CLI/Python/prepared/mesh comparison fixtures, each run once at the parent commit and once at the candidate with all
new fields absent; normalized results must be byte-identical.

Analytic feed/removal battery, all at fixed small dimension and checked to 1e-12 against closed forms implemented
independently in `controls/p23_feed_removal.py` (and cross-checked by dense augmented-matrix exponential where
noted):

- feed s into a stable nuclide for time t: `N = s t`;
- feed s into a decaying nuclide: `N = (s/lambda)(1 - e^{-lambda t})`; the `removed` sink stays exactly zero;
- removal r on an initially stocked decaying nuclide: `N = N0 e^{-(lambda+r)t}`,
  `N_removed = N0 * r/(lambda+r) * (1 - e^{-(lambda+r)t})`, with atoms conserved across state, sink and decayed
  daughters to 1e-12;
- feed into a parent with a decaying daughter: full two-body Bateman-with-source solution;
- element removal expanding over every matching state;
- cooling-step removal acting while feed is off.

Metamorphic battery: feed result invariant under splitting a constant-feed step; feed scales linearly in s;
removal-only steps leave flux-independent results unchanged; absent fields produce byte-identical output.

Reverse battery in `controls/p23_reverse.py`: forward runs at a declared nonzero multiplier generate synthetic
measurements; reverse must recover the multiplier to 1e-12 relative on (a) a single measurement, (b) a
multi-nuclide multi-step set, and (c) the segment case with known distinct segment multipliers. A deliberately
inconsistent measurement set must report its residuals and chi-square rather than force agreement. Underdetermined
segments, a coupled-mode problem, zero sensitivities and absent nuclides must each fail with the named error.

Damage battery in `controls/p23_damage.py`: a one-element one-target mono-group case in closed form; an independent
fold from result JSON + table file reproducing every per-step number to 1e-12; a `build-damage` mini-corpus whose
groupwise values an independent re-collapse of the same MF=3/MT=444 sections reproduces; coverage ledger honesty
(absent targets named, never zero-filled); and mesh/single-cell identity.

## Cost and checkpoint discipline

The minimum gate inputs are the synthetic fixtures above plus the public iron example; no full-library rebuild is a
P23 gate input. `build-damage` controls use a small named TENDL-2025 subset, hash-pinned in the control. All nuclear
data, generated tables and bulk artifacts stay outside Git. Every heavy job runs under the workstation cgroup rules
in `AGENTS.md`, one at a time, resumable where it exceeds ten minutes.

## Gates

### G0 — opening and identity battery

The protocol hash is recorded below before any implementation. A control binds the parent commit and confirms the
identity battery is byte-identical at the parent before work begins. Prior verdicts and the v1.0.1 release identity
are re-asserted. Committed, pushed and green before feature code lands.

### G1 — feed/removal

Spec fields, physical plumbing, sink state, ledger and output surfaces implemented; the full analytic and
metamorphic batteries pass; prune interaction demonstrated (a fed nuclide unreachable by reactions is kept and
solved); trace and coupled modes both exercised; bulk-removal reservoir exemption ledgered; CLI, Python, prepared
and mesh paths agree exactly; Python `Schedule` gains feed/removal methods. Zero-field runs remain byte-identical.

### G2 — reverse calculation

`actinv reverse` and `actinv.reverse` implemented; the scalar battery recovers known multipliers to 1e-12; segment
mode solves the declared NNLS problem and reports its condition number; every named refusal fires; results carry
problem/measurement hashes; the linear-regime refusal is honest and documented.

### G3 — damage observables

`damage` section, table schema, fold, ledger and `build-damage` implemented; the damage battery passes including the
independent fold and mini-corpus re-collapse; NRT and Ed provenance explicit; uncovered targets named; mesh parity.

### G4 — compatibility, performance and docs

Identity battery byte-identical at the candidate. Frozen-workload runtime and peak RSS no worse than 1.05 times the
parent baseline with all features absent. SPEC/METHOD/QUANTITIES/README/python-README updated; one worked example
per feature under `examples/`; known-limitations text names what P23 did not do (coupled-mode reverse, pathway
attribution of fed inventory, non-NRT models, damage covariance).

### G5 — independent closure

An independent closure checker imports no production module; rederives the analytic battery expectations; rehashes
every committed evidence artifact; repeats the WLS/NNLS arithmetic on stored sensitivity matrices; re-folds one
damage step from the stored table; verifies the identity battery; and rejects planted mutations of feed rates,
removal constants, sink rows, estimates, residuals, coverage lists and hashes. Session file, manifest regeneration,
verdict, commit and push close the phase. `P23-PASS` permits an additive minor release proposal; the release itself
is the maintainer's act.

## Closure interpretation

`P23-PASS` means ACTINV computes continuous per-step feed and first-order removal with an audited mass sink, infers
flux normalization and per-segment multipliers from measured activities in the linear regime with honest refusals
outside it, and reports NRT damage observables from hash-pinned tables including a self-produced TENDL MT=444 route.
It does not claim parity with a licensed code's feature breadth, a nonlinear reverse solver, pathway attribution of
fed material, or any displacement model beyond NRT.
