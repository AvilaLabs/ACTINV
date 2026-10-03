# ACTINV P108 — declared affine composition-range classification

Opened 2026-10-03 under the owner's roadmap direction after P107 terminal PASS.
P107 implementation `27d5636ad5331a3dacd8130a6b5e3516fb661e9f` passed all six
scheduled workflows. Its closure push is verified separately. Preserve P105/
P107 PASS, P103/P104/P106 FAIL and every historical artifact/checker.

## Product scope and qualification boundary

Add `actinv waste composition COMPOSITION.json [OUT.json]`. This first layer
classifies a caller-declared fixed affine activity model over feasible natural-
element wt% ranges. Native activation basis generation and full-material solve
verification are a later phase using P75b coupled/reach and P79/P105 helpers.
This command does not run activation, read a library or infer physical linearity.
P75b qualifies coupled/reach superposition; trace/reach failed. Users supplying
solver responses must ensure common fixed-rate conditions. No composition-
dependent geometry, confidence level, covariance, automatic uncertainty,
uncertainty budget, intrusion screen or disposal acceptance is qualified here.

The mathematical model is explicit: each declared coefficient `r[i,n]` is the
nuclide's Bq/g for a 100 wt% pure constituent under common fixed conditions.
At composition w, whole-component activity is
`mass_g * sum_i(w[i] * r[i,n] / 100)`.
The feasible set is `lower[i] <= w[i] <= upper[i]` and `sum_i(w[i]) = 100`.
Intervals are simultaneous, deterministic constraints. Omitted products in an
element's explicitly supplied map mean exact zero only within the declared
model; inventory completeness is independently self-declared per target as in
P107. Neither an empty response nor a complete declaration proves physical
completeness. Source/assumptions must identify the declared response basis.

Project each nuclide separately over this same feasible composition polytope.
Then use the existing activity-box evaluator. Separate projections discard
cross-nuclide correlations, so the final ordered class envelope is a
**conservative superset**, potentially containing unattainable classes. Endpoint
agreement qualifies stability only within that enclosure and the declared
affine model. Keep P107 unknown-inventory/property/external-H3 behavior and all
six separate table/column constraints, strict contributor counts and bindings.
Never relax a class boundary using a numerical comparison tolerance.

## Frozen input/output contract

Input schema `actinv-waste-composition-spec-1`, `rules` exactly the bundled
`us-nrc-10cfr61.55-v1` (SHA
`890268af81a53815b8e11c238e4a1694eabb79664a1ca087fd7fe22b9fabe5d7`),
`response_model` exactly `fixed_rate_affine_activity`, and nonempty `components`.
Each component has unique nonempty `id`, fixed `mass_g`, exactly one positive
`displaced_volume_cm3` or `density_g_cm3`, `waste_type`, fixed
`nuclide_properties`, P107 `external_tritium`,
`composition_wt_percent_bounds`, and nonempty `targets`.

The composition map holds `{lower_wt_percent, upper_wt_percent}` for 1–64
canonical natural-element identities; finite `0 <= lower <= upper <= 100`.
Fixed constituents use equal bounds. Reject isotope identities, duplicate raw
JSON keys and canonical aliases, empty/infeasible boxes, and overflow. Require
`sum(lower) <= 100 <= sum(upper)`; no automatic renormalization or implicit
balance. A balance constituent is simply another explicitly bounded coordinate.
Use existing material-key validation. The generic core optimization API may
accept named coordinates; the CLI restricts them to natural elements.

Each target has unique positive `step`, finite nonnegative `t_s`,
`element_activity_bq_per_g`, `inventory_coverage`,
`unbounded_inventory_reasons`, nonempty `bounds_source` and
`bounds_assumptions`. The element response map must contain exactly every
composition constituent, including explicitly empty maps for zero response.
Its inner nuclide maps have finite nonnegative coefficients and reject raw and
canonical duplicate aliases. P107 coverage/reason consistency, property
validation, external-H3 canonical selected-step completeness/source/exclusion,
unknown fields and validate-all-before-write rules remain mandatory. Geometry
and metadata are fixed for all allowed compositions. No base-spec, uncertainty
or probability fields are accepted in this declared-model mode.

Output schema `actinv-waste-composition-result-1`, method
`declared_affine_composition_polytope`, original input SHA, selected rule
identity, fixed response-model/unit declaration, canonical composition bounds
and the 100 wt% equality. Reuse the complete P107 component/target bounds report;
retain its projected-input SHA separately. Per target retain canonical response
coefficients and projection records per nuclide: lower/upper total-Bq bounds,
min/max feasible witness compositions and the dual multipliers used for the
bound certificates. Retain coverage, source and assumptions, activation bounds,
external and merged H3 bounds, endpoints/ranges/constraints/envelope/stability.
No normal RunResult, ledger or certificate schema changes.

## Optimization and floating-point containment

For each scalar response, obtain primal witnesses by lower-bound initialization
and allocating residual weight in ascending/descending coefficient order,
respecting capacities, with canonical-coordinate tie breaking. Validate witness
feasibility (absolute composition roundoff tolerance `1e-10` wt%) and record the
whole composition; tolerance does not change the declared feasible set.

Use the bounded-simplex dual for conservative scalar activity bounds. For any
finite multiplier lambda and normalized coefficients `c[i]=r[i,n]/100`,

`Dmin(lambda)=100*lambda + sum_i min((c[i]-lambda)*lower[i], (c[i]-lambda)*upper[i])`

is a lower bound on every feasible activity per gram, and the corresponding
`Dmax` using max is an upper bound. Evaluate candidate multipliers zero and each
normalized coefficient. Choose the greatest outward-rounded lower certificate
and least outward-rounded upper certificate. Directed interval rounding must
enclose division, subtraction, multiplication and addition; account for signed
intermediates and subnormal/overflow behavior. Apply fixed component mass with
outward rounding. Clamp the lower bound to zero using physical nonnegativity;
all-zero coefficient vectors produce exact `[0,0]`. Reject nonfinite bound
construction. A generic optimizer must refuse invalid response/coordinate sets.
Do not inject epsilon inventory into the class evaluator.

Independent Python exact arithmetic uses `Decimal.from_float` for input floats
and enumerates all feasible vertices of the tiny controls, without importing
the Rust optimizer. Reported scalar bounds must contain exact mathematical
min/max without a tolerance. Tightness must be within relative `1e-10` /
absolute `1e-12` Bq. Witness feasibility and value use `1e-10` wt% and relative
`1e-10` / absolute `1e-12` Bq respectively. Independently classify the validated
projected endpoints under unchanged P105 predicates; numerical row comparisons
retain relative `1e-6` / absolute `1e-12`, never tolerant class predicates.

## Minimum sealed artificial population

Exactly 12 valid components, 13 targets, 26 endpoints; mass=1 g, volume=1 cm3,
general waste unless stated. Properties are artificial: C14/Tc99/H3 half-life
ten Julian years, Z=6/43/1, no alpha; external H3 not_applicable unless stated.
Targets step=1, t=0; complete/empty reasons and nonempty artificial source/
assumptions unless stated. For Table-1 fractions use pure response
`f * (limit * 37000)` Bq/g. All unmentioned response maps are explicitly empty.

1. `single_fixed`: Nb fixed100, C14 coefficient fraction .05 -> stable A.
2. `all_zero`: Si[0,100], Fe[0,100], empty response maps -> exact zero, stable A.
3. `correlated_two`: Si fixed10, Nb[20,60], Fe[30,70]; Nb C14 coefficient .25,
   others zero -> activity fractions .05..15, A..C envelope containing
   unattainable B (Table 1 has no B transition).
4. `opposite_products`: same polytope; Nb C14 fraction1, Fe Tc99 fraction1 ->
   separate fractions .2..6 and .3..7; box C..above-C while every feasible
   composition has total Table-1 fraction .9 and class C.
5. `fixed_three`: Si10/Nb60/Fe30 all fixed, same coefficients as case4 -> C.
6. `coefficient_tie`: Si[0,100], Fe[0,100], both C14 coefficient .05 -> stable A;
   deterministic canonical tie witnesses.
7. `zero_crossing`: Si fixed10, Nb[0,60], Fe[30,90]; Nb C14 coefficient .25,
   Si Tc99 coefficient .5, Fe zero -> Tc99 fraction .05 fixed, C14 0..15;
   A..C with endpoint-specific contributor counts.
8. `external_h3`: Nb fixed100 with zero response, H3 properties, bounded external
   H3 [740000,2220000] Bq -> A..B.
9. `external_merge`: Nb fixed100, activation H3 coefficient888000 Bq/g,
   external fixed888000 Bq -> stable B, combined once.
10. `incomplete`: same zero model as case2, inventory incomplete with reason ->
    both regulatory classes unknown, calculated-only arithmetic retained.
11. `missing_properties`: Si[0,100], Nb[0,100], Nb C14 coefficient74000 Bq/g,
    empty properties -> unknown at both endpoints, including lower zero.
12. `two_times`: same polytope as case3; first response as case3 at step1/t0;
    second response all constituents C14 fraction .02 at step2/t100 -> stable A
    at the second target, with no copied coefficients/coverage/time.

Refusal variants are separate: invalid/nonfinite/reversed/out-of-100 weights,
infeasible lower/upper sums, empty coordinates, raw/canonical duplicates,
isotope identity, missing/extra response constituent, invalid activities,
unknown fields, invalid geometry/properties/target identity/coverage/H3 and
missing source/assumptions. All refusals leave an existing output sentinel
unchanged. Planted report mutations must reject changed coefficients, bounds,
composition/equality/witness/dual multiplier, geometry/rule/input identity,
endpoint row/constraint/class/envelope/stability, source/coverage/properties and
external H3. Do not merely compare a mutated scalar with its original.

## Historical qualification verification and gates

P107's frozen verdict checker validates Rust hashes against its current ROOT.
Future Rust changes must instead verify that historical source from its recorded
implementation commit. Before any Rust mutation, add a new historical verifier
and CI transition; preserve the sealed P107 checker and artifacts unchanged.
Read each recorded Rust blob from the exact implementation commit with bounded
git children; verify its recorded SHA and reconstruct only those sources in a
disk temporary tree. Invoke the unchanged verdict derivation with its quality
source lookup scoped to that tree, while all evidence/predecessor/CI lookups
remain the unchanged recorded artifacts. Require exact persisted verdict
equality. Test changed current source, wrong commit/blob hash, and changed
evidence/CI identities. Use the already reviewed P105 bounded child runner;
no new unlimited waits, test current_exe, builds or application launches.

- G0: register protocol before edits/evidence; historical verifier tests and
  replay before Rust changes. Freeze independent controls/test/case hashes and
  the 12-case population before production CLI cases. Recheck pinned pack and
  previous verdicts; independently derive vertices/extrema and expected classes.
- G1: core projection/regressions, all 12 data-free CLI cases, exact containment,
  tightness, feasible witnesses, independent endpoint/class/range verification,
  all planted mutations and sentinel-preserving refusals. Separate output paths
  must be byte-identical; no nuclear-data fetch or activation solve.
- G2: full read-only replay compares stable evidence before diagnostics; bind G1
  exact SHA and repeat identity. Replay P105 and P107 scientific controls, with
  P107 historical quality verified on the implementation commit.
- G3: workspace fmt/check/clippy/test all targets/features, meaningful projection
  and CLI regressions, inherited four bounded child lifecycle tests, handbook
  build/link/Chromium, and CI integration. Coordinator alone executes serial
  jobs inside enforced MemoryMax=6G, MemorySwapMax=0, TasksMax=128,
  CPUQuota=200%, CARGO_BUILD_JOBS=1, RUST_TEST_THREADS=1, RAYON_NUM_THREADS=2,
  disk TMPDIR `target/preflight-tmp`; inspect limits read-only, no unlimited
  fallback. Controls timeout five minutes, quality jobs twenty minutes. Review
  process spawning and require bounded termination/kill/reaping.
- Closure: derive LOCAL-PASS only from all local gates; final PASS additionally
  requires every scheduled implementation workflow green on the exact recorded
  SHA with artifact/source binding. Stage code before indexed manifest refresh,
  owner/plain commit, authorized push, verify every pushed SHA. No new feature
  work if any pushed check is red. Append session/ledger and retain failures.

One repair round is available; freeze an amendment before repair and preserve
the failed gate. A further failure closes FAIL and requires a successor. Native
basis generation/full-material verification remains explicitly open after this
declared-model layer, alongside draft intrusion screening and twin/workbench.
