# ACTINV P113 — opt-in nominal waste classification in the facility twin

Opened 2026-10-04 after P112-PASS and green closure commit
`1b6ef05b2acc2b68fe8cefeb91a196949bec7294`. Register this protocol before
new control edits, gate evidence or production changes. One phase is open.

## Frozen product contract

Extend `actinv twin TWINSPEC.json [OUT.json]` with an optional `waste` object.
Its schema is `actinv-twin-waste-spec-1`; require explicitly selected bundled
rules `us-nrc-10cfr61.55-v1`, one-based `targets`, `nuclide_properties`, and a
`components` object keyed by the existing twin component names. Each entry
declares positive `mass_g`, exactly one positive `displaced_volume_cm3` or
`density_g_cm3`, `waste_type` (`general` or `activated_metal`),
`cell_masses_g` keyed by cell ID, and the unchanged nominal `external_tritium`
declaration. Reject null rather than treating a supplied null block as omitted.
Reject unknown fields in this new block and its component/property/declaration
objects. No second independently declared cell-membership list is accepted.

The existing twin `components` groups are the sole membership source. Require
the waste component-name set to equal that group-name set, nonempty groups,
nonempty IDs, no duplicate IDs within a group, no overlap between groups, and
exact equality between each mass-map key set and its group's IDs. Every assigned
cell must exist in the mesh. Cell masses sum to component mass within the
unchanged relative tolerance `1e-9`. Selected step indices and timestamps must
exist and agree within each component under the existing nominal comparator.
Never infer component mass or displaced volume from mesh voxel geometry.

Evaluate the exact original mesh bytes through the shared quiet nominal waste
evaluator. Aggregate whole-component activity as the sum of each cell's
`activity_Bq_per_g` times its declared cell mass, then apply component volume or
mass normalization once. Add independently declared external H-3 once, retaining
activation-only and external evidence. Preserve P105 rule thresholds, dedicated
row precedence, activated-metal replacements, strict boundaries, fractions,
binding constraints and missing-property/required-H-3 outcomes. This is nominal
classification only; P105 arithmetic qualification does not establish physical
reaction/decay completeness.

Add sibling output `waste_classification`, carrying an ordinary
`actinv-waste-result-1` document with all component/target classes, margins,
binding constraints and conditional coverage. Add `waste_facility_coverage`,
schema `actinv-twin-waste-summary-1`, with:

- `inventory_basis: "original_mesh_activity_Bq_per_g"` and
  `assay_adjustment: "not_applied"`;
- sorted `assigned_cell_ids` and `unassigned_cell_ids`, their counts,
  `membership_coverage` (`complete` or `partial`), and `component_count`;
- `target_class_counts`: one entry in requested target order, each with `step`
  and a `counts` object containing all five nominal class strings (including
  `unknown`) and integer component counts.

This summary counts separate component classes; it does not combine components
into a package or assign a scalar facility class. Existing twin assays, dose
assays and propagation modify certified response bands, not original activity
maps. Keep that distinction explicit. A changed assay may change existing
clearance results but must leave the waste document and its summary unchanged.
Unassigned mesh cells remain visible even when every assigned component has
complete rule-property coverage. No intrusion-screen, budget, dose, site
acceptance, legal compliance, statistical band or mixed-package qualification
enters this phase. Workbench and Python surfaces remain separate follow-ups.

## Input, output and compatibility limits

Read the twin spec once as a regular non-symlink UTF-8 file, at most 8 MiB. This
outer-spec resource guard applies to ordinary twin invocations too. When waste
is enabled, read the mesh once as a regular non-symlink file, at most 64 MiB;
reuse those validated bytes in both twin and waste paths. Require proper nominal
mesh header/cell/footer order and matching counts, at most 128 cells, at most
128 components, 1–64 unique positive targets, at most 1,024 nuclide properties,
and at most 1,024 positive canonical activities per component/target. Validate
all mesh activity maps, including unselected steps, for finite nonnegative
numbers and canonical alias collisions. Reject recursive raw duplicate JSON
keys in the supplied spec and every mesh record when the feature is enabled.
Validate canonical property-key uniqueness and property values up front.
Reject malformed or nonfinite external-H-3 values and alias step keys; require
every selected declared external step. Preserve conditional unknown outcomes
for required, undeclared H-3 and missing active-nuclide properties.

When enabled, bound each referenced assay document to 8 MiB and reject
non-regular/symlink files; cap cell and dose assay lists at 128 each. Preserve
ordinary twin semantics when the block is omitted, apart from the explicit
outer-spec guard. Existing keys and values must match the frozen independent
legacy fixture expectations. With the block present, existing keys and values
must also equal the same request without the block. Serialize the entire final
result before a single output write; reject enabled results above 32 MiB,
including any emitted newline. Invalid late components, targets, assays or
output-size checks must preserve an existing output sentinel. No child process
is introduced into production evaluation.

## Gates and minimum controlled population

**G0:** Bind this registered protocol, the unchanged bundled Part 61 pack/core
mirror, P105 source/vector seals and PASS, P112 PASS plus its implementation/CI
records, and all new fixture/checker/oracle/seal/verdict/regression sources.
Require both prior historical verdict replays. Independently derive all
expected values from source rows and caller-declared synthetic inputs, using
the frozen P105 independent arithmetic or a separately reviewed equivalent;
never use a production waste result as the classification oracle. Seal the
complete immutable fixture population and exact expected reports. Replay the
whole stable persisted G0 before production edits; JSON normalize stable values
before comparison. Bind control hashes safely, rejecting missing files,
traversal and symlink escape. No evaluated nuclear data is required.

Minimum fixture: at least 24 distinct requests and 32 component/target results,
using a tiny four-cell/two-step synthetic mesh family with unequal cell masses,
nonunit component volumes and two disjoint component groups. Cover all five
class outcomes, two-table mixtures, aggregate categories, metal replacements,
volume-versus-density equivalence, split-versus-unsplit activity equivalence,
mass-normalized transuranics, selected-step reordering, exact and adjacent
boundaries, external H-3 counted once, required-H-3 unknown, missing-property
unknown, partial and complete membership, empty activity, and scalar, mixture,
propagated and dose-assay separation. Include ordinary twin expected reports
and deterministic repeat requests. Population and expected values freeze at G0;
no post-result exclusions, case additions or threshold changes.

**G1:** Run every frozen request using a fresh release binary. Independently
sum whole-component Bq and compare every nominal component/target inventory,
class, coverage, unknown reason, table/column constraint, contribution, fraction,
margin, pass/strict flag and binding result. Compare all membership IDs/counts,
five-class count summaries, explicit assay basis, and existing twin output
identity. Numeric comparisons use `rel_tol=1e-12`, `abs_tol=1e-12`; categorical
structure and identity are exact. Require at least 30 meaningful report
mutations to be rejected, including margins, contributors, unknown coverage,
membership and class-count/basis fields. Require at least 30 distinct malformed
input refusals with output sentinel preservation: duplicate/overlapping/missing
membership, mass-map mismatch/closure, bad geometry/types/external declarations,
step/timestamp gaps, raw duplicates, aliases, malformed counts/order, resource
limits and invalid late component/assay. Repeat outputs byte-identically.

**G2:** Rerun the entire G1 population, refusals, mutations and compatibility
checks in a separate disk path. Require exact stable-report equality and repeat
output byte identity before appending replay diagnostics. Bind exact G0/G1
artifact hashes. A full read-only G0/G1/G2 replay must also pass.

**G3:** Fresh fmt, workspace all-target/all-feature check, strict Clippy, and
workspace all-target/all-feature executable tests; compile test modules.
Build a fresh release. Run new independent source/seal/verdict regressions,
four bounded child-lifecycle races, historical P107/P108/P109/P110/P111/P112
verification, and unchanged P105/P107/P108/P110/P111/P112 scientific replays.
Build the mdBook, check links and inspect the rendered documentation with the
existing bounded Chromium smoke. Record actual exits, log hashes, Rust source
hashes, release-binary hash and exact enforced resource inspection. No inherited
build/test observation substitutes for these changed-source checks. Stage files
before indexed manifest refresh and require the indexed manifest gate before
push. Plain owner commits; every scheduled implementation workflow must be green
on the exact bound SHA before terminal PASS. Verify every closure-push workflow
before starting another phase. Future changes verify historical sources/artifact
bytes from this recorded implementation commit.

## Resource and stop rules

Coordinator alone executes jobs, serially, in enforced systemd limits:
6 GiB memory, zero swap, 128 tasks, 200% CPU; Cargo jobs1, Rust tests1, Rayon2,
disk-backed `target/preflight-tmp`. Inspect caps read-only before executable
gates; never use allocation as a limits test and never fall back unbounded.
Review all process-spawning code. Reuse the bounded P105 runner with timeout,
terminate/kill/reap; no recursive Rust test executable. Individual synthetic CLI
children at most two minutes, a science gate at most ten minutes, a quality job
at most twenty minutes. No facility-scale solver or bulk input campaign is
needed; no gate is expected to exceed ten minutes apart from fresh workspace
quality. Use existing dependency/build caches and retain individual gate logs.

One repair round: register an amendment before repairing any failed gate,
retaining original source/artifact/log bytes. Another failed gate after that
round is terminal FAIL and requires a separately frozen successor. Out-of-scope
discoveries go to PARKING. Never convert historical FAIL or CONDITIONAL verdicts
or claim an unexecuted check passed.
