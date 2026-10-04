# ACTINV P111 — opt-in draft fusion intrusion screen

Opened 2026-10-03 after P110-PASS. Implementation
`f31e0f13af2f5ca69b86bc68a46e02bdcbd3f4a3` passed all six workflows; closure
`70489ac3ede013f7b1f22d24227c9e5a4ec6d44c` passed all four scheduled workflows.
Register this protocol before control/pack edits or scientific gate evidence.
Preserve every earlier verdict and sealed control. One phase open, one repair
round, amendment before repair; another failed gate after repair is terminal FAIL.

## Product and source boundary

Add `actinv waste intrusion-screen SPEC.json [OUT.json]`, explicitly selected
draft screening for one declared whole-container component at nominal result
steps. Reuse current nominal `actinv waste` evaluation, geometry, input aggregation,
nuclide aliases/properties and external H3 handling. Keep the complete nominal
Part 61 classification separately; screen the existing whole-component Bq map,
with external H3 counted once. No activation solve, dose/transport model, legal
compliance, site acceptance, probability, uncertainty propagation, impurity budget,
mixed-package averaging or automatic disposal-class promotion is added.

Freeze February 2026 NUREG-1556 Volume 22, ADAMS ML24092A377:
https://www.nrc.gov/docs/ML2409/ML24092A377.pdf . PDF SHA-256
`2deae800fc1a2ac3a7134481e81b75c90df98a1c9dade4393816719b55d27359`;
complete extracted text SHA-256
`ef1a98961cef3465461400395f384cb1296a8ead9e505ef6a53975041544f3ca`.
The reviewed quantitative table is **8-5**, printed pp. 8-70–8-71; presence
criteria are on 8-69 and Appendix Q, Q-6. Reconcile the March 2025 preliminary
ML24295A002 (PDF SHA
`094b9e266a7143893f77a5908d22b42ceaae59a81c050b51ff7d59990bdca762`,
text SHA `6f33834adc759c99a7e01c8192ffc8c998e14d618ca92bbb1f51fb273e796e5f`).
All 25 numeric rows match across the two reviewed versions. The current official
NUREG page still labels the report draft; do not present it as a final rule.
Source URLs/status and the manual PDF/table review go into a dated reconciliation
record. G0 uses a compact pinned source excerpt and independently parsed literal
row fixture; it must distinguish manual primary-PDF review from automated offline
excerpt checks. Do not claim CI downloaded/reviewed the full PDFs. No bulk data
or complete PDF goes into Git.

Preserve printed defects: Cs137 Class C **460** Ci/m3 (Part 61 is 4600),
activated-metal Nb94 **0.2/0.2/2** Ci/m3 (Part 61 C is 0.2), prose/Appendix Q's
Table 8-4 references, Appendix Q's `activity or is` typo, and the inaccurate
quoted DOT appendix title. Use the actual hazardous-substance/RQ appendix to
49 CFR 172.101 as context; supplied RQs remain explicit caller declarations,
not a new built-in legal/RQ catalog. The selected pack ID is
`us-nrc-nureg1556-v22-draft-2026-02-v1`, schema `actinv-waste-draft-pack-1`.
Ship identical copies under data/ and crates/actinv-core/data/.

## Strict input and reuse

Outer schema `actinv-waste-intrusion-screen-spec-1` has only:
`schema`, `draft_rules`, `waste_spec`, `package_basis`, `waste_form`,
`inventory_coverage`, `unbounded_inventory_reasons`, optional `site_wac` and
optional `dot_rq`. `waste_spec` is an embedded unchanged `actinv-waste-spec-1`,
with bundled `us-nrc-10cfr61.55-v1` rules, exactly one component and 1..64 unique
positive selected steps. Its input path resolves relative to the outer file,
as in nominal waste. `package_basis` is exactly `single_component_container`:
the component represents the whole disposal container, with no hidden denominator
from a selected cell or known subtotal. `inventory_coverage` is complete,
incomplete or unknown. Reasons are distinct nonempty strings, empty only for
complete; missing required H3 or nominal unknown properties downgrades affected
screen conclusions irrespective of the caller's coverage claim.

`waste_form` is metal, cement_or_polymer, soil, equipment, rubble, resin, ash,
calcined, or other. Metal requires activated_metal nominal waste type; every
activated_metal component requires metal form. Other forms use general waste.
Other carries an unlisted-form assessment indicator, not an invented dose result.

`site_wac`, when supplied, has only nonempty `source`, `membership_coverage`
(complete/incomplete), and `nuclides`. Each canonicalizable unique nuclide maps
to one of: `{status: listed, limit: {value: finite_nonnegative, unit: Ci/m3|nCi/g}}`,
`{status: listed_no_numeric_limit}`, or `{status: unlisted}`; no extra fields.
Missing entries in a complete membership map are unlisted; missing entries in
an incomplete/absent map are unknown. Explicit listed_no_numeric_limit is listed
but supplies no numeric 1%-WAC comparison, which remains indeterminate. Do not
substitute draft table values for site WAC. Convert concentrations using the
component's actual mass/volume (37000 Bq/cm3 per Ci/m3, 37 Bq/g per nCi/g).

`dot_rq`, when supplied, has only nonempty `source`, `coverage` (complete/incomplete),
and `nuclides`, mapping unique canonicalizable names to positive finite whole-
activity **Bq** RQs. No inferred default for an absent nuclide: it is unknown even
if the map is declared complete. Reject zero/negative/nonfinite RQs. State that
form, applicability and source suitability are caller declarations. Arithmetic
mixture ratios are calculated from this mapping; no transport/packaging approval.

Recursively reject raw duplicate object keys before Value/typed decoding, also
within embedded waste specs and native input JSON/each mesh NDJSON record.
Reject aliases duplicated after normalization in properties/WAC/RQ/activity maps.
Keep existing nominal behavior by factoring a quiet evaluator and an input-bytes
parser; run_doc retains its output behavior. Screen reads the regular input file
once under its size limit, validates exact bytes, and passes those same bytes to
the shared evaluator so a second read cannot evade validation. No subprocesses.

Bound outer input at 8 MiB, result input at 64 MiB, mesh at 128 cells, properties/
WAC/RQ at 1024 entries each, positive nuclides at 1024 per selected target and
serialized output at 32 MiB. Only regular files, bounded reads, finite sums/
concentrations/ratios; refuse overflow. Validate all screen declarations before
nominal evaluation; write once after complete success. Invalid or late-invalid
requests preserve an existing output sentinel; no crash-atomicity claim.

## Frozen presence and table interpretations

Evaluate each positive-activity nuclide with three-valued predicates (true,
false, indeterminate), then OR: any true means present; all false means absent;
otherwise presence is indeterminate. Retain each predicate and reason. A true
predicate does not make missing source/coverage information complete.

1. Concentration **> 0.01 times** its supplied WAC numeric limit. Equality is
false. Missing/no-numeric limit is indeterminate.
2. Concentration **> 260000 Bq/cm3** and not appearing in Part 61/WAC. Freeze
consensus of the two readings of the source's `or`: at/below threshold false;
above, both listed false, neither listed true, one listed/one unlisted or any
unresolved membership indeterminate. Part 61 listing uses applicability of its
named/aggregate/metal rows, not just literal nuclide-name presence. Missing
properties needed for aggregate membership leave listing unknown. Do not treat
missing WAC as unlisted.
3. Individual activity **>= its declared RQ** establishes true, even if other
inventory is missing. With complete inventory/RQs and package mixture sum
`sum(activity_i/RQ_i) < 1`, below-RQ contributors are false. If mixture reaches
one but an individual positive contributor is below its own RQ, its criterion is
indeterminate: the draft does not settle per-nuclide marking of mixture RQs.
Missing RQs/denominator coverage leave affected sub-RQ conclusions indeterminate.
Report the known mixture ratio and whether its population is complete separately.
4. Activity **>= 0.01 times** total container activity, including all listed and
unlisted activity plus external H3 once. Equality true. Incomplete inventory or
required undeclared H3 leaves the denominator indeterminate; never use the known
subtotal as the whole container. Zero total has no positive nuclides to test.

Copy all 25 printed rows, units, A/B/C values, explicit no-limit cells and
metal selectors literally. Interpret the explicit <5-year **sum** as all
positive matching inventory activity (365.25 days/year, strict <5), including
named nuclides; retain their independent named-row constraints too. The printed
Pu238/239/240/242, Am241/243, Cm243/244 row is the fixed eight-nuclide aggregate,
following note g's combined category. Cm242 and Np237 stay outside that row.
Do not add row sums together, extend that group to all transuranics, or import a
Part 61 sum-of-fractions into this draft screen. Unknown short-group membership
leaves that row conditional; expose members and contributions.

For a row, applicability is OR of its members' presence. Display arithmetic
concentration from all matching known inventory, including absent members, and
retain conditional presence/membership explicitly. This is the frozen conservative
aggregate interpretation, not a dose calculation. Show per-row selected column,
limit, contribution, and relation below/at_limit/above/no_numeric_limit/unknown.
Select only the **computed nominal A/B/C class**. Unknown or above_class_c gives
no selected column and an indeterminate screen; never default to Class C.
Unlisted present nuclides or unlisted form indicate assessment review. For a known
applicable numeric row, above indicates review; exact equality is indeterminate
(caption says below, prose says greater). Below and typed no_numeric_limit do not
indicate review by that row. Indeterminate presence/membership/coverage propagates
to the aggregate indicator unless another clear review indication already exists.
Incomplete WAC/RQ/inventory remains visible even when OR establishes presence.

Output schema `actinv-waste-intrusion-screen-result-1`: immutable nominal
`classification`, separate `draft_intrusion_screen` with source/pack identity,
interpretations, coverage/missing inputs, per-target activity/denominator evidence,
per-nuclide predicates, row comparisons and assessment indicator
`review_indicated|indeterminate|not_indicated_by_implemented_checks`.
Include outer input SHA, generated inner waste-spec JSON and its exact SHA,
units, and `disposal_acceptance: not_assessed`, `intrusion_dose: not_calculated`,
`legal_compliance: not_determined`. No PASS/compliant result or combined draft
sum-of-fractions. Keep nominal statuses unchanged inside classification.

## Gates, population and closure

G0 BEFORE production edits: independently check the two source versions and all
25 literal rows/units/columns/nulls/notes, source-review decisions, status URLs,
pack/mirror, preserved prior verdicts (P110 historical/source CI verification),
protocol registry and all control/fixture/excerpt bytes. Seal then replay exact
stable equality before the CLI feature exists. Source/oracle code consumes no
production screen arithmetic. Maintain all failed predecessors unchanged.

G1: independent Decimal rule arithmetic and whole-component fixture inventories.
At least 75 exact row/column checks (all 25 x A/B/C) via source pack and pure
Rust arithmetic regressions, plus at least 40 distinct CLI case vectors / 40
targets. Freeze the generated fixture before G0: WAC and 0.26 thresholds at
below/equal/above, RQ and mixture below/equal/above, 1% share below/equal/above,
each single sufficient OR predicate, all-false and unknown combinations,
criterion-2 consensus cases, missing/explicit WAC status and RQs, incomplete
inventory/required H3, external H3 combined once, zero inventory, both geometry
units/mesh aggregation, named/short/alpha aggregates and overlaps, all no-limit
columns, Cs137 literal discrepancy, metal selectors, above-C/unknown classes,
other forms, equality indicator and byte-identical repeats. Some abstract row/
column boundaries are not reachable under computed Part 61 class; qualify their
pure arithmetic separately and do not claim impossible end-to-end examples.
Require >=20 meaningful planted report mutations and >=30 distinct refusals
with sentinels (duplicates/aliases, nonfinite/overflow, invalid units/classes/
forms/counts/schemas/paths, unsupported settings, late-invalid target).

G2: full read-only G0/G1 replay, exact stable whole-report equality before replay
diagnostics, exact request/output SHA binding, separate outputs byte-identical.
G3: serial bounded fmt/check/Clippy/test workspace all targets/features, source/
oracle/seal regressions, four inherited child lifecycle races, fresh release,
P105/P107/P108/P110 scientific replay and historical P107/P108/P109/P110 verdict
verification, handbook build/link/Chromium checks. All observed exits/source/log/
binary hashes recorded; no unexecuted check passes. All local jobs coordinator
only in enforced 6 GiB/no-swap/128-task/200% CPU scope, Cargo jobs1/test threads1/
Rayon2 and disk TMPDIR. Read-only cap inspection, no unlimited fallback. Control
children <=300 s with terminate/kill/reap; overall science <=600 s, quality <=1200 s.
No recursive test executable child. Minimum synthetic inputs only; no large
nuclear-data campaign, solver job, benchmark or external dose assessment needed.

Only complete source-bound local gates yield P111-LOCAL-PASS. Terminal P111-PASS
requires all six scheduled workflows green on exact recorded implementation SHA,
bound to G0/G3 and historic Rust sources. Stage before indexed manifest refresh,
plain owner commit, push and verify all scheduled runs. Verify closure push before
another phase. Discoveries outside this scope go to PARKING. Waste budgets,
uncertainty methods, physical validation and twin/workbench remain later scopes.
