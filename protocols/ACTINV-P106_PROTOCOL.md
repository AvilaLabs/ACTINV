# ACTINV P106 — conservative whole-component activity-box classification

Opened 2026-10-03 under the owner's direction to prioritize the waste extension.
P105 terminal PASS has been derived from green implementation CI and its closure
is recorded separately. This phase implements only the conservative-bounds
follow-up named in `docs/ROADMAP.md`
and `docs/WASTE_CLASSIFICATION_EXTENSION.md`.
It does not change P105, its verdict, or the nominal waste contract.

## Objective and claim boundary

Given a complete rectangular enclosure of whole-component nuclide activities at
each selected target, report an optimistic-to-pessimistic **conservative class
superset** under the selected single-component rule pack. These are deterministic
activity bounds supplied by the user or an identified upstream bounding method.
They are not probability distributions, confidence intervals, covariance
propagation, or evidence that an upstream physical model is qualified.

The smallest CLI accepts declared bounds directly. It does not read RunResult or
mesh data, launch a synthetic activation solve, or derive bounds from upstream
uncertainty fields. Multiple components and target times may be supplied, each
with its own bounds and coverage.

The initial mode accepts no composition variables. It does not propagate
uncertainty through activation or decay, consume per-element standard deviations,
calculate uncertainty-aware impurity budgets, or claim exact feasible classes.
No composition solver, nuclear-data qualification, full BTP interpretation,
fusion intrusion screen, package averaging, or site-acceptance conclusion is in
scope. Nominal `actinv waste` and `actinv waste budget` behavior is unchanged.

## Fixed rules, component data, and interval contract

- Select only the frozen `us-nrc-10cfr61.55-v1` pack, SHA-256
  `890268af81a53815b8e11c238e4a1694eabb79664a1ca087fd7fe22b9fabe5d7`.
  Preserve P103/P104/P105 source and predicate interpretations, including the
  inclusive single-contributor versus strict multi-contributor boundaries.
- Each component has one fixed waste type, positive finite mass, and positive
  finite displaced volume (or fixed density under the existing conservative
  solid-volume rule). Geometry and type cannot vary over an activity interval.
- Each component carries fixed identity, geometry, waste type, nuclide
  properties, and an external-H3 declaration. Each target has `step`, `t_s`,
  `activity_bounds_bq`, `inventory_coverage` (`complete` or `incomplete`), and
  `unbounded_inventory_reasons`, `bounds_source`, and `bounds_assumptions`.
  The latter two are nonempty strings per target, recording declared source
  and assumptions without validating physical completeness or qualifying the
  bound. Incomplete coverage requires at least one
  reason; complete coverage requires an empty reason list. Each supplied
  nuclide interval is `[lower_bq, upper_bq]` for **total Bq in the whole
  component**, finite and `0 <= lower <= upper`. The interval is simultaneous
  and rectangular across nuclides: every combination inside the box is
  admitted. It is not Bq/g. `[0,0]` contributes no positive inventory and needs
  no metadata. An omitted nuclide is exact zero only when
  `inventory_coverage="complete"` is explicitly self-declared; this phase
  cannot detect a false physical-completeness declaration. Empty inventory is
  accepted with either explicit coverage status; incomplete empty inventory
  remains unknown.
- Nuclide Z, half-life, and alpha-emitting metadata are fixed over the interval
  and use the P105 schema. Do not infer metadata from activity keys. An absent
  property record for any upper-positive nuclide makes the target
  incomplete/unknown. Malformed or inconsistent supplied properties (invalid Z,
  nonpositive half-life, or missing typed fields) are input refusals, even for
  inactive nuclides. Any declared incomplete inventory blocks a regulatory
  class envelope; preserve known-subset endpoint arithmetic as calculated-only
  and conditional.
- External H-3 remains separate from calculated activation. Per component,
  accept `not_applicable`, `required`, or an explicit bounded declaration with
  fixed source metadata, `excludes_activation: true`, and a target-step interval
  map. A required
  or otherwise unknown H-3 interval yields `unknown`/incomplete; it is never
  replaced with zero. A declared H-3 interval is combined with calculated H-3
  only after confirming the external interval excludes activation H-3. Do not
  double-count a supplied H-3 interval as an inventory row.
- An all-zero inventory may classify as A only when the target explicitly has
  complete coverage, every required metadata/coverage condition is satisfied,
  and external H-3 is explicitly `not_applicable` or validly bounded to zero. Empty complete inventory
  then qualifies A. Empty inventory is not evidence of completeness by itself.
  Normalize activation and external-H3 maps separately; reject duplicate
  canonical aliases within either source map. Then merge their H-3 intervals by
  adding lower to lower and upper to upper exactly once.

The CLI accepts only bundled id `us-nrc-10cfr61.55-v1` with the pinned bytes
above; it has no custom rule-pack path. A generic core API may accept another
valid pack, but that is not a qualified CLI option. The CLI has no source-file,
RunResult, mesh, or solver dependency.

The command is `actinv waste bounds BOUNDS.json [OUT.json]`. Input schema is
`actinv-waste-bounds-spec-1`, with `rules` and a nonempty `components` array.
Each component has unique nonempty `id`, `mass_g`, exactly one of
`displaced_volume_cm3`/`density_g_cm3`, `waste_type`, `nuclide_properties`,
`external_tritium`, and a nonempty `targets` array. Each target has the fields
listed above; activity maps hold `{ "lower_bq": number, "upper_bq": number }`.
Steps are positive integers, unique within a component; `t_s` is finite and
nonnegative. Components may represent independent schedules. Target order need
not imply a time evolution model. Unknown fields, invalid identities, duplicate
canonical activity/property aliases, and contradictory coverage/reasons fail
before writing output. Coverage reasons must be nonempty strings.

External `bounded` declarations have `source`, `excludes_activation:true`, and
`activity_bounds_bq` mapping canonical decimal target-step strings to intervals.
Require exactly the component's selected steps, without aliases such as `01`.
`required` is accepted and produces unknown results; a missing external
declaration or missing bounded target interval is an input refusal. Positive
external H-3 requires a supplied fixed H-3 property record like any other
upper-positive activity. Empty or malformed source/assumption strings fail.

Output schema is `actinv-waste-bounds-result-1`, method
`declared_activity_box`. Retain rule identity/SHA, input SHA, component identity,
geometry/type/fixed properties, external declaration, target identity,
source/assumptions/coverage/reasons, activation bounds, external bounds and
merged evaluated bounds. Each target emits `lower`, `upper` endpoint
evaluations, row-fraction ranges aligned by table/column/row/nuclide, and all
six A/B/C constraints with endpoint sums/margins/contributors/strictness.
Absent zero-activity endpoint rows have zero concentration/fraction; null
limits/fractions remain null. Incomplete endpoints retain known-subset
arithmetic and identify all upper-positive unknown nuclides. The envelope and
stability fields below are top-level target fields. No normal RunResult,
solver ledger or certificate field changes are authorized.

## Conservative endpoint semantics

For each target, evaluate the lower-activity endpoint and upper-activity
endpoint using the unchanged P105 row arithmetic and fixed geometry/properties,
with interval-aware contributor counts. The nominal point API counts only
positive point activities, so the interval API must expose or internally retain
the endpoint-specific contributor policy: at the optimistic endpoint count
only contributors whose lower bound is positive; at the pessimistic endpoint
count every contributor whose upper bound can be positive. Do not approximate
this by adding epsilon activity. This handles intervals crossing zero
conservatively at strict mixture boundaries: a potentially contributing
second nuclide cannot be discarded merely because its lower bound is zero.

Because every table/column concentration sum is nondecreasing in every
nonnegative activity and adding possible contributors can only make the strict
mixture predicate at least as restrictive, the low endpoint is an optimistic
class bound and the high endpoint is a pessimistic bound. Return the ordered
inclusive range between those endpoint classes as a **conservative superset**;
do not describe every class in that range as feasible. Endpoints retain the
actual class, all row fractions, every Table 1/Table 2 column constraint,
contributors, strictness, binding rows/columns, and unknown/unlisted coverage.
Keep the two tables separate.

If coverage or required H-3 is unknown/incomplete, emit
`class_envelope=["unknown"]`, `class_is_stable=false`, and `stable_class=null`.
`conservative_superset` is null when the envelope is unknown.
Both endpoint evaluations have `class=unknown` and incomplete coverage while
retaining `calculated_only_class` and all known-subset arithmetic. For complete
coverage, emit the ordered `class_envelope` between endpoint classes and
`conservative_superset=true`; `class_is_stable` is true iff endpoint classes
match, and `stable_class` is their common class or null. All-zero complete
inventory with H-3 `not_applicable` is stable A.

Numerical evaluation uses the P105 direct activity/limit denominators and its
`1e-6` relative / `1e-12` absolute arithmetic-comparison tolerance. The
tolerance never relaxes a class predicate or moves an endpoint across an
inclusive/strict boundary. Class labels at exact, immediately-below, and
immediately-above boundaries must match the frozen predicates exactly.

## Minimal independent artificial population

Freeze all 126 existing P103 public/source-independent classification vectors
unchanged as zero-width total-Bq boxes. Add exactly these 20 named artificial
interval/coverage vectors. For a named row fraction `f`, use activity
`f * limit * 37,000 Bq` for Ci/m3 rows and `f * limit * 37 Bq` for nCi/g rows,
with mass=1 g and volume=1 cm3. Unmentioned nuclides are exact zero. Derive
every expected row sum and endpoint label independently from the pinned pack;
the arithmetic definition here is the vector's mathematical input, not a
measured result. No nuclear data or predictive validation is implied.

1. `empty_complete`: empty complete inventory, H-3 not_applicable -> A/A,
   stable A.
2. `empty_incomplete`: empty inventory, incomplete with reason -> endpoint
   classes unknown/unknown; envelope `[unknown]`, unstable.
3. `required_h3`: empty complete inventory, H-3 required -> unknown/unknown.
4. `single_t1_boundary_box`: general C-14 has Table 1 fraction interval [0.1, 1.0]
   -> endpoint A/C; envelope A..C, unstable. (Boundary-adjacent arithmetic is
   already controlled in the inherited 126 vectors with 1e-10 offsets.)
5. `mixture_t1_strict_box`: general C-14 and Tc-99 have fractions [0.05,0.5] each
   -> summed endpoints 0.1/1.0, classes C/above-C by strict mixture rules.
6. `zero_crossing_contributor`: general C-14 fixed at 0.05; Tc-99 has
   fraction [0,0.05] -> optimistic A, pessimistic C, envelope A..C.
7. `sr90_cs137_example`: 50 Ci/m3 Sr-90 and 22 Ci/m3 Cs-137, the official mixture totaling
   5/6 in its controlling Table 2 column -> B/B.
8. `t1_t2_separate`: general C-14 Table 1 fraction 0.2 and Ni-63 Table 2 Column 1
   fraction 0.5 -> C/C; table sums are never combined.
9. `t2_three_columns`: three target records for general Ni-63 at its Column
   1, 2, and 3 boundaries -> A, B, C respectively.
10. `metal_nb94`: Nb-94 Table 1 fraction 0.2, activated_metal -> C/C.
11. `alpha_tru`: Pu-239 alpha-transuranic fraction 0.2 with complete
    properties -> C/C.
12. `cm242_cross_table`: Cm-242 fraction 0.2 in Table 1 and short-lived Table 2
    aggregate -> C/C with both tables represented.
13. `dedicated_co60`: Co-60 Table 2 Column 1 fraction 0.2 -> A/A; its
    dedicated row has no B/C limit. Do not also add it to the short-lived
    aggregate.
14. `missing_active_properties`: general C-14 activity [0,29600] Bq with no property
    record -> unknown/unknown, retaining known-subset calculations.
15. `inactive_and_malformed_properties`: `[0,0]` C-14 without properties
    alongside complete empty inventory -> A/A; separate malformed supplied
    properties (bad Z/nonpositive half-life/missing typed field) -> refusal.
16. `long_lived_unlisted`: Fe-55 fixed at 100 Bq and exactly five-year half-life, with valid
    properties -> A/A, unlisted activity explicitly retained.
17. `external_h3_cross`: activation H-3 zero and valid external-H3 interval
    [740000,2220000] Bq -> A/B; H-3 source excludes activation.
18. `external_h3_merge`: activation and external H-3 each contribute fraction
    0.6 to the H-3 A limit -> combined fraction 1.2, B/B exactly once; alias
    duplication is a refusal.
19. `target_coverage_change`: two target times with zero intervals, first
    complete and second incomplete with reason -> A/A then unknown/unknown.
20. `target_bounds_change`: two complete target times with general C-14 fraction
    0.05 then 0.2 -> A then C; no interval/completeness is copied between times.

All added metadata is explicitly artificial: half-life is ten Julian years
for C-14, Tc-99, Ni-63, Sr-90, Cs-137, Nb-94, Pu-239 and H-3; one year for
Co-60 and Cm-242; exactly five years for Fe-55. Z matches the element; only
Pu-239/Cm-242 are alpha emitting. Unless noted, target is step 1 at t_s=0,
coverage complete with empty reasons, source/assumptions identify the
artificial box, and external H-3 is not_applicable. Multi-target cases use
steps 1,2 (and 3 for Ni-63) at times 0,100 (and 200) seconds. The 20-case
count excludes syntactically invalid refusal variants; 126+20=146 case records.
The inherited vector fixture SHA is
`bfefb655b2df52da7ccb7a93cfd7c22bdc18762917e900e828ebd97d58b2bb42`.

## Independent controls and fail-closed mutations

The checker independently computes each endpoint's inventory-to-concentration
conversion, row fractions, Table 1 sum, each applicable Table 2 column sum,
contributor counts/strictness, class, envelope, and `stable_class`. At exact
boundaries the expected class comes from the protocol predicate, not a tolerant
numeric comparison. Check the envelope property for every vector: both
independent endpoint classes lie inside it; do not claim the range enumerates
exact feasible cases.

Plant mutations that must be rejected include altered endpoint activity,
geometry, rule-pack SHA, class/envelope, endpoint row fraction, one table/column
sum, contributor count/strictness, target time, `stable_class`, completeness,
upper-positive missing metadata, and external-H3 interval/source/exclusion.
Refusal tests cover negative/nonfinite/reversed bounds, duplicate normalized
nuclide aliases, accumulation overflow, missing target boxes, duplicate target
steps, contradictory step/time identities, incomplete inventory without a
  reason, complete inventory with a nonempty reason list, missing external declaration or bounded H-3 target,
invalid geometry, and missing/empty source or bounding assumptions. Mutations
must be passed through the independent report validator; comparing a changed
scalar to its original value alone is not a control. A self-declared complete
inventory with omitted nuclides is accepted as complete by contract; this tool
does not detect a false physical-completeness declaration.

## Gates, lifecycle, and estimated work

- G0: register this protocol hash before production implementation. Seal
  checker/helper/test hashes and 146 generated case inputs before production
  CLI execution; independently derive every expected endpoint and require the
  20 specified labels. Verify the pinned rule/vector bytes and inherit P105
  source decisions. Source/vector checks are public arithmetic, not predictive
  evidence. A mismatch stops G0 before any CLI evidence.
- G1: core endpoint/envelope API and Rust boundary/refusal regressions; all
  nominal P105 tests remain unchanged.
- G2: deterministic Python checker for the artificial population, planted
  mutations, data-free CLI JSON round trips and byte-identical repeated output
  at separate output paths. The 146 cases may be one component batch. No nuclear source fetch, solver,
  or probabilistic sampling is needed for this interval-input mode.
- G3: `cargo fmt --all -- --check`, `cargo check --workspace --all-targets --all-features`,
  `cargo clippy --workspace --all-targets --all-features -- -D warnings`, and
  `cargo test --workspace --all-targets --all-features` under the enforced workstation
  cgroup, handbook link/build/browser checks for public schema docs, and CI checker
  integration. Run one executable job at a time; bounded subprocesses must
  terminate and reap on timeout. Preserve any failure and require green Actions
  after pushes.
- Estimated cost: 146 data-free CLI inputs, under five minutes for the class
  gate; existing incremental Rust build cache, individual quality jobs bounded
  at 20 minutes. No activation solve or nuclear-data fetch is required. Parent
  coordinator owns all executable checks. Use serial enforced 6G/zero-swap,
  128-task/200% CPU systemd scopes with disk TMPDIR; inspect resource limits
  read-only. Child waits are bounded, terminate/kill/reap is reviewed, and the
  four inherited P105 child lifecycle regressions execute. No test current_exe.

Checker-derived P106-PASS requires G0/G1/G2/local quality plus all scheduled
workflows green on the implementation push. A local-only disposition may be
recorded while CI is pending. Preserve any failed gate; one repair round is
available. A further failed gate closes FAIL and requires a successor with a
new protocol. Close with session, append-only ledger, indexed manifest,
owner-authored commit, push and verified green Actions. P105 and its failed
predecessors remain immutable. CLI help may point to the public waste handbook.
