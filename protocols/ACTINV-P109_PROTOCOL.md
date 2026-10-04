# ACTINV P109 — native fixed-rate composition basis and witness verification

Opened 2026-10-03 after terminal P108-PASS on implementation
`78204834caeace66c050989c73259c1bf2f9969c` and successful closure push
`d675cf80dfd2574c41e0008c7880494151c1100e`. All six implementation workflows
and all four closure workflows were verified green. Preserve every predecessor
verdict and artifact; register this protocol before implementation/evidence.

## Scope and qualification boundary

Add `actinv waste composition solve SPEC.json [OUT.json]`, distinct from the
unchanged P108 caller-declared model command. Generate natural-element activity
responses with one immutable `PreparedRun` per component and evaluate their
P108 bounded-simplex projections. Fully solve every distinct activity-extremum
witness composition, plus a canonical feasible reference even when there is
no activity response. This closes native basis generation and selected-witness
verification for the fixed-rate affine model, not statistical uncertainty,
physical completeness, nuclear-data accuracy or disposal acceptance.

P75b qualifies coupled/reach superposition in its tested neutron domain (26
natural elements, three material mixtures, three spectra, flux amplitudes
1e10/1e13/1e15, one-year irradiation and cooling through 100 years). Trace/reach
failed; defaults cannot establish the same property. Initial native elements
are restricted to the union in those tested mixtures:
Al, B, C, Ca, Co, Cr, Cu, Eu, Fe, H, K, Mg, Mn, Mo, N, Na, Nb, Ni, O, P, S,
Si, Ta, Ti, V, W. The new artificial controls test the implementation and one
analytically specified neutron capture/decay model; they do not expand P75b's
physical validation domain. New spectra/schedules/material combinations retain
that domain limitation even when their selected witness checks pass.

Every class envelope remains a conservative superset for the generated affine
activity basis. Separate nuclide projections discard correlation. Outward
rounding certifies the arithmetic projection of those binary-float coefficients;
it does not bound CRAM/solver error or nuclear-data/model error. State explicitly
that numerical solver error is not bounded. Selected witness agreement is a
point verification, not a proof that every raw solver value lies within the
ULP-scale affine interval. No class threshold is relaxed by a tolerance.

## Strict input and work bounds

Schema `actinv-waste-composition-solve-spec-1`, `rules` exactly bundled
`us-nrc-10cfr61.55-v1` with SHA-256
`890268af81a53815b8e11c238e4a1694eabb79664a1ca087fd7fe22b9fabe5d7`,
nonempty `components` of at most four. Each component has unique nonempty id,
`base_spec` (embedded actinv-spec-1 or a path), fixed positive finite `mass_g`,
exactly one positive finite `displaced_volume_cm3` or `density_g_cm3`,
`waste_type`, P107 `external_tritium`, `composition_wt_percent_bounds`,
selected `targets` (one-based positive unique step integers),
`inventory_coverage`, `unbounded_inventory_reasons`, nonempty `model_source`
and `model_assumptions`. The coverage declaration asserts only the caller's
modeled inventory assumptions; the command can downgrade it, never upgrade it.
No supplied nuclide-properties, response coefficients, target times, confidence
or probability fields are accepted.

P108 exact binary-float feasibility, canonical natural-element symbols,
duplicate-key refusal and wt% equality semantics apply. At most eight elements
and four selected targets per component, at most 32 pure-element solves per
request, at most 64 distinct full witness compositions across the request.
All known dimensions and all component metadata/specs are validated before
any solve. Witness cardinality is known after basis projection; refuse before
exceeding its remaining request cap, never silently sample or trim witnesses.
Maximum executed solver work is 32 basis plus 64 full runs per request.
Basis work can occur before witness cardinality is known. Each component's
witness set must fit the remaining cap before any of its full runs; earlier
components may already have completed work if a later set causes refusal.
Output is emitted only after every component and verification succeeds.

Limit root JSON and each path-based base spec to 8 MiB before reading. Refuse a
final serialized result larger than 32 MiB before the writer; never trim its
physics records to fit. Retain serde's bounded JSON recursion; scan before
constructing a Value and reject raw duplicate object keys recursively,
including embedded or path-based specs. Base-spec paths resolve relative to
the wrapper file, as the existing budget wrapper does; nuclear-data references
retain ordinary run/CWD semantics. Document this distinction. Present null
geometry is invalid, rather than equivalent to an absent geometry field.

Use strict Spec parsing/validation. Force material basis wt_percent, component
mass, mode coupled, prune reach, bmin_atoms_per_g zero and outputs ledger+audit.
Refuse non-neutron projectile; shielding, uncertainty, screen, feed, removal,
step spectra, rate/decay/yield scale overrides, radiological/damage extensions,
gas output, non-default photon response/settings and CRAM order other than 16.
No field may be silently ignored to evade these restrictions. Derive target
times by summing the same parsed schedule durations; no user time overrides.
Step identity means RunResult.steps[].step, the one-based endpoint of that
schedule segment. Reject absent/duplicate/unaddressable selected steps before
basis solves, then require the result's selected t_s to equal that same sum.
All basis/witness solves use identical effective data, spectrum, temperature,
schedule and numerical settings. No subprocess is launched by production code.

## Effective data, coverage and external tritium

Add minimal immutable PreparedRun accessors for its exact loaded activation
index (target identities and convergence/builder-ledger metadata) and effective
decay nuclide map after merge/overrides. Do not reread
another decay file or invent properties. For active products and positive
external H3, derive positive finite half-life and Z from the loaded nuclide.
Require lambda()>0 / radioactive nst rather than treating a stable-flag record
with a positive half-life as radioactive. Alpha emission is true for any positive-branch decay mode whose six-decimal
RTYP digit representation contains 4, matching existing chain semantics.
Stable states contribute no fabricated positive half-life. Missing properties
retain P107 unknown behavior; inconsistent positive activity for a stable state
refuses before output. Record primary/fallback/override and library/index input
hashes from solver certificates.

Require ledger+audit for every basis and witness run. Aggregate detected reasons
in sorted deterministic order over the whole component: audit status incomplete
or missing, its reaction/decay/unquantified defect classes, library convergence
flags and target limitations (including exact index metadata for reached
positive states beyond the initial bulk), and positive initial or reached inventory states
absent from the activation-target set when irradiation has positive exposure.
Retain the corresponding original audit/limitation evidence. An audit complete
status alone does not prove activation-target or physical completeness.
Caller-declared incomplete coverage and reasons are preserved. Any detected
omission downgrades every selected component target to incomplete, keeping both
regulatory endpoint classes and the envelope unknown while preserving known-
subset calculations. Do not turn a missing activation target into a physics zero.

External tritium remains a fixed, separately declared whole-component Bq box
over selected steps, excludes_activation=true with source attribution. Add it
once after activation superposition. Required-but-missing external H3 retains
unknown class-envelope behavior. No fuel/permeation model is inferred. Bounded H3 maps contain exactly the
selected canonical step keys; required status is map-less as in P107. External
H3 contributes no native atoms/g inventory; it enters only the waste evaluator.

## Native verification and deterministic output

Generate a canonical P108 spec with derived step times/properties, each pure
element's canonical Bq/g response map, final effective coverage/reasons and
caller source/assumptions plus explicit native basis identity. Reuse the P108
projection and P107 bounds evaluator; do not change their sealed contracts.

Deduplicate the entire canonical weight map of every min/max nuclide witness
over all selected targets, plus the zero-response canonical feasible reference
returned as min_witness_wt_percent by project_response with all-zero
coefficients. Deduplicate complete sorted maps, not just variable coordinates.
For each unique composition, perform a full native solve at the component mass.
At every selected target compare the union of all activities against the wt%-
weighted basis (per-nuclide relative 1e-6, absolute 1e-12 Bq/g; also activity L1
relative 1e-6/absolute 1e-12). Compare the complete atoms/g vectors with an L1
criterion: summed absolute disagreement <= max(1e-12 atoms/g, 1e-6 times the
larger vector L1 norm). Per-nuclide atom discrepancies are diagnostics; no
stronger componentwise atom-error bound is claimed.

For external H3 lower and upper offsets separately, evaluate predicted and
direct whole-component activities using the shared rule evaluator and actual
geometry/type/properties. Compare every row/constraint value at relative 1e-6 /
absolute 1e-12; require exact identity of regulatory/calculated class,
contributor identities/counts, strictness and passes flags. Any disagreement
refuses the request, even if a tolerant numeric comparison passes. The final
projected envelope is re-evaluated after all witness audits downgrade coverage.

Result schema `actinv-waste-composition-solve-result-1`, method
`native_fixed_rate_composition_polytope`, fixed affine response model, original
input SHA, final generated-response input SHA, rule identity and unit fields.
Retain the exact generated-response JSON text for independent hash/metadata
checking and reuse with P108. Preserve caller nuclear-data reference paths in
certificates; do not add generated machine-specific absolute working paths.
Retain P108 component/target/projection/envelope fields. Add deterministic
solver basis and native verification records: canonical pure/witness weights,
selected times, all activity and atoms/g maps, audits and omission reasons,
solver input certificates, predicted/direct class constraints, errors,
tolerances, counts and pass flags. Omit ms/timings and other runtime counters.
No normal RunResult, ledger or certificate field changes. Validate all before
the one final existing output writer; semantic failures preserve any output
sentinel. No crash-atomic guarantee is added.

## Frozen minimum artificial population

No evaluated nuclear data or external fetch. Deterministic generated artifacts
under disk target/p109-controls, relative run/CWD paths, reproducible ZIP
timestamps/member order/permissions. Bind generator/control/test/fixture bytes
and every generated NPZ/index/decay hash in G0 before native CLI evidence.
Pin abundance/mass JSON SHA
`285b38a823dcee398a1dcab798c3b5a17d6b7ae5817edc663ed502314367a87b`.

Ten activation targets: Fe54/56/57/58, Si28/29/30, Nb93, Nb94, Mo94. Eleven
single-group rows: nine explicit MT102 total-loss zero-barn rows (every target
except Nb93), and Nb93 MT102 total loss plus Nb93->Nb94 product, both 1 barn.
Boundaries [1,2] eV, neutron flux [1e12] cm^-2 s^-1, T293.6 K. Primary decay:
eight stable natural parents, artificial Nb94 half-life1e11 s with a correctly
encoded beta-minus RTYP1/RFS0/BR1 branch to stable Mo94; additional artificial
H3 half-life1e9 s beta-minus to stable He3 for external-H3 metadata. All 12
records are primary, no fallback. Every row/target identity and stable/decay
assumption is checked independently. Two variants remove the Fe58 activation
target/zero row or remove the Nb94 decay record; both must give unknown coverage.

Base schedule: 1e6 s irradiation multiplier1, then 1e6 s cooling multiplier0;
targets [1,2], mass1g, activated_metal. Native shape Fe50 fixed, Nb[1,10],
Si[40,49] obeys Si=50-Nb. Exactly nine separate valid requests, each one
component, two targets, four endpoint evaluations (18 targets/36 endpoints):

1. Ranged composition, volume1 cm3 -> A..C both targets.
2. Fe50/Nb10/Si40 all fixed, volume7 -> stable A both targets.
3. Ranged composition, volume0.1 -> C..above-C both targets.
4. Fe50/Nb1/Si49 fixed, volume1, external H3[740000,2220000] at each target
   -> A..B, H3 merged exactly once.
5. Ranged composition/volume1, external H3 required -> unknown.
6. Ranged composition/volume1, caller inventory incomplete with explicit
   artificial reason -> unknown despite otherwise complete model audits.
7. Ranged composition/volume1, Fe58-target-omission variant -> unknown, even
   though its absent modeled zero channel cannot change the Nb activity.
8. Ranged composition/volume1, Nb94-decay-omission variant -> unknown, with
   leakage/audit evidence and known-subset arithmetic preserved.
9. Fe50/Si50 fixed (Nb coordinate absent), volume1 -> exact zero activity and
   stable A; a full reference solve is mandatory despite empty projections.

Except case6, caller coverage complete/empty reasons. Source and assumptions
explicitly identify the artificial declared model; external status
not_applicable except cases4/5. Full fixture bytes settle exact structure,
not a post-hoc interpretation of CLI output.

Independent Python uses Decimal precision80 to derive N0=N_A/M_Nb with
N_A=6.02214076e23 and M_Nb=92.90637317 g/mol, k=1e-12 s^-1,
lambda=ln(2)/1e11. From zero Nb94:
N93(t)=N0 exp(-kt);
N94(t)=N0*k/(lambda-k)*(exp(-kt)-exp(-lambda*t));
Mo94(t)=N0-N93(t)-N94(t).
At cooling tau: N93 unchanged, N94 multiplied exp(-lambda*tau), its lost
atoms added to Mo94. Activity=lambda*N94. At composition x wt% multiply
these Nb populations/activity by x/100. Stable Fe/Si atoms are independently
N_A*abundance/molar_mass times their mass fractions using pinned JSON tables.
Analytical activity comparison relative1e-6/absolute1e-12 Bq/g; atom inventory
comparison uses the same L1 criterion plus individual stable Fe/Si and Nb93
relative1e-6 checks. No componentwise tiny Mo94 precision claim.

Pure Nb activity is approximately4.49e4 Bq/g; fractions at volume1 for Nb1/10
are about0.061/0.607 against the activated-metal Nb94 limit0.2 Ci/m3. Control
labels follow exact scalar predicates, never tolerance-adjusted classes.
Exact Decimal vertex oracle validates every generated coefficient projection;
P105/P107 independent endpoint oracle validates the resulting class report.

## Gates, failures and closure

- G0: register protocol before edits/evidence; independently seal all generated
  data/fixture/control/test bytes, exact nine-case population and expected
  labels; recheck fixed rules and historical P105/P107/P108 verdicts.
- G1: all nine native requests, independent analytic activities/populations,
  exact generated-basis projection containment/tightness and canonical witnesses,
  full verification/audit downgrades/class comparisons, source identity,
  repeat-byte determinism. At least 20 planted report mutations and 30 invalid
  inputs (including nested duplicates and unsupported settings) rejected;
  every refusal preserves an existing output sentinel. Direct full-solve
  disagreement cannot yield an unconditional report.
- G2: bind exact G1 SHA; full read-only replay compares stable evidence before
  diagnostics. Re-run P105/P107/P108 scientific controls and terminal historical
  verdict/source checks without changing their sealed evidence.
- G3: workspace fmt/check/clippy/test all targets/features, native/accessor/
  coverage/verification regressions, frozen seal/persistence regressions, four
  inherited bounded child lifecycle tests, handbook build/links/Chromium and CI.
  All executable jobs coordinator-only, serial, enforced systemd scope 6GiB
  memory/no swap/128tasks/200%CPU, Cargo jobs1/test threads1/Rayon2, disk TMPDIR
  target/preflight-tmp. Inspect limits read-only. No unlimited fallback or new
  Rust test current_exe launch. Controls five-minute per-child waits with the
  reviewed P105 terminate/kill/reap runner, ten-minute overall control scope;
  quality jobs twenty minutes. Review child code before execution.
- Closure: checker LOCAL-PASS only from all local gates; final PASS only after
  all six scheduled implementation workflows green on recorded exact SHA,
  bound to G0/G3 and historical Rust source blobs. Stage before indexed manifest
  refresh; owner plain commits; authorized pushes and all-workflow verification.

One repair round. Freeze an amendment before repair and preserve the failed
gate; another failed gate closes FAIL under a successor. New discoveries go
to PARKING. P105/P107/P108 PASS and P103/P104/P106 FAIL remain unchanged.
Statistical uncertainty/budgets, draft intrusion screening and twin/workbench
remain separate phases; this phase does not silently repair P105 budget audit
coverage or expand its historical qualification.
