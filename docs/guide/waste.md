# Component waste classes and impurity limits

`actinv waste` evaluates nominal component inventories against an explicitly
selected U.S. 10 CFR 61.55 rule pack. It reports Class A, B, C, or above Class C
at your selected schedule steps, together with the table fractions that control
the result. `actinv waste budget` calculates and verifies impurity specifications
for a requested class. `actinv waste bounds` classifies directly declared
whole-component activity intervals and reports a conservative class superset.

```sh
actinv waste component.json component-waste.json
actinv waste budget class-budget.json class-budget-result.json
actinv waste bounds activity-bounds.json activity-bounds-result.json
actinv waste composition composition.json composition-result.json
actinv waste composition solve native-composition.json native-composition-result.json
actinv waste intrusion-screen intrusion.json intrusion-result.json
```

This is a concentration calculation for research and design screening. It does
not determine disposal-site acceptance, waste-form compliance, or intrusion dose.
Nominal classification consumes solver results; the bounds command consumes only
the activity intervals declared in its input. These intervals are deterministic
enclosures, not probability distributions, confidence intervals, covariance
propagation, or evidence that an upstream physical model is qualified. Bounds
are absolute total Bq for the whole component, not concentration. They are
simultaneous rectangular intervals across nuclides; all combinations in the box
are admitted. The command does not infer time evolution between target steps or
derive bounds from a `RunResult`. The separate `intrusion-screen` command applies
the selected draft screen to nominal inventories.

## Screen a whole container against the fusion draft

`actinv waste intrusion-screen` evaluates the February 2026 draft NUREG-1556
Volume 22 Table 8-5. Select this draft explicitly with
`us-nrc-nureg1556-v22-draft-2026-02-v1`. The result keeps the complete nominal
Part 61 `classification` and a separate `draft_intrusion_screen`. It reports
`review_indicated`, `indeterminate`, or
`not_indicated_by_implemented_checks`; it does not calculate intrusion dose or
determine legal compliance or disposal acceptance.

Embed one ordinary waste specification representing the whole disposal container.
Its input result path resolves relative to this outer document. Native run
activities are multiplied by component mass; mesh cell activities are weighted
by each cell's mass before aggregation. External H-3 is added once. The following
quantities and properties are artificial examples:

```json
{
  "schema": "actinv-waste-intrusion-screen-spec-1",
  "draft_rules": "us-nrc-nureg1556-v22-draft-2026-02-v1",
  "package_basis": "single_component_container",
  "waste_form": "equipment",
  "inventory_coverage": "complete",
  "unbounded_inventory_reasons": [],
  "waste_spec": {
    "schema": "actinv-waste-spec-1",
    "rules": "us-nrc-10cfr61.55-v1",
    "input": "component-result.json",
    "targets": [2],
    "nuclide_properties": {
      "C14": {"z": 6, "half_life_s": 315576000, "alpha_emitting": false}
    },
    "components": [{
      "id": "whole-container",
      "mass_g": 1000,
      "displaced_volume_cm3": 128.2,
      "waste_type": "general",
      "external_tritium": {"status": "not_applicable"}
    }]
  },
  "site_wac": {
    "source": "artificial site-WAC example",
    "membership_coverage": "complete",
    "nuclides": {
      "C14": {"status": "listed", "limit": {"value": 100, "unit": "nCi/g"}}
    }
  },
  "dot_rq": {
    "source": "artificial caller-declared RQ example",
    "coverage": "complete",
    "nuclides": {"C14": 40000}
  }
}
```

Site WAC and whole-activity DOT RQs are optional caller declarations. The command
supplies no default WAC or RQ catalog. WAC entries can also declare
`listed_no_numeric_limit` or `unlisted`; missing entries in an incomplete map
remain unknown. RQs are positive finite Bq. An individual activity at its RQ is
present; a mixture ratio at or above one does not by itself settle each
below-RQ contributor's presence. The result preserves all four presence
predicates and their supporting quantities, including unresolved source inputs.

`inventory_coverage: "complete"` declares that the whole container inventory is
accounted for; ACTINV cannot verify that physical claim. Use `incomplete` or
`unknown` with nonempty reasons when inventory is missing. Required undeclared
H-3 or missing properties also leave the total-container denominator unknown.
The 1% activity-share test never uses a known subtotal as a complete container.
`metal` form requires `activated_metal`; the other forms use `general`.

Rows use the computed nominal A/B/C column. Unknown and above-Class-C inventories
have no selected column. The strict less-than-five-year sum includes matching
named nuclides, while the alpha row uses only its printed eight members. Printed
no-limit cells remain explicit; row equality is indeterminate. The literal draft
values include Cs-137 Class C at 460 Ci/m³ and activated-metal Nb-94 at
0.2/0.2/2 Ci/m³. These differ from Part 61 and remain separate in the result.

The screen accepts 1–64 selected steps, at most 128 mesh cells, and at most 1024
properties, WAC entries, RQs and positive nuclides per target. Input files must
be regular files: the outer document is limited to 8 MiB and the result input to
64 MiB. Duplicate keys, duplicate normalized nuclide identities, nonfinite
arithmetic and outputs above 32 MiB are refused before writing a result.

## Classify declared composition ranges

`actinv waste composition` projects caller-supplied fixed-rate affine
pure-element responses over an explicitly bounded natural-element composition
whose coordinates sum to exactly 100 wt%. It does not generate those responses,
run activation, read a nuclear-data library, or infer that rates are physically
linear. The caller must establish that the responses apply under common fixed
conditions and state their basis in each target's source and assumptions.
Responses are Bq/g for a 100 wt% pure constituent; projected whole-component
activities are Bq after multiplying by the fixed component mass. Geometry and
waste type are fixed across the declared composition range.

The input schema is `actinv-waste-composition-spec-1`; only the bundled
`us-nrc-10cfr61.55-v1` rule pack is accepted. Composition keys are canonical
natural-element symbols, and each target gives a response map for every
constituent. An omitted nuclide within a constituent's explicit map means zero
only under that caller-declared model. Coverage remains an independent caller
declaration. Each nuclide is projected separately, so the P107 activity box
loses cross-nuclide correlation and its ordered class envelope is a conservative
superset that can contain unattainable classes. The result does not report
probabilities, confidence levels, covariance propagation, automatic physical
uncertainty, disposal acceptance, or intrusion dose.

This artificial example uses a pure-Nb C-14 response of 74,000 Bq/g. With
component mass 1 g and volume 1 cm³, Nb can occupy 20–60 wt%; Si is fixed at 10
wt%, and Fe closes the exact 100 wt% total. The resulting C-14 activity range
is 14,800–44,400 Bq, corresponding to 5–15% of the applicable Table 1 limit.

```json
{
  "schema": "actinv-waste-composition-spec-1",
  "rules": "us-nrc-10cfr61.55-v1",
  "response_model": "fixed_rate_affine_activity",
  "components": [{
    "id": "artificial-alloy",
    "mass_g": 1,
    "displaced_volume_cm3": 1,
    "waste_type": "general",
    "nuclide_properties": {
      "C14": {"z": 6, "half_life_s": 315576000, "alpha_emitting": false}
    },
    "external_tritium": {"status": "not_applicable"},
    "composition_wt_percent_bounds": {
      "Si": {"lower_wt_percent": 10, "upper_wt_percent": 10},
      "Nb": {"lower_wt_percent": 20, "upper_wt_percent": 60},
      "Fe": {"lower_wt_percent": 30, "upper_wt_percent": 70}
    },
    "targets": [{
      "step": 1,
      "t_s": 0,
      "element_activity_bq_per_g": {
        "Si": {},
        "Nb": {"C14": 74000},
        "Fe": {}
      },
      "inventory_coverage": "complete",
      "unbounded_inventory_reasons": [],
      "bounds_source": "artificial declared pure-element response",
      "bounds_assumptions": "fixed-rate affine model; coefficients are not generated by ACTINV"
    }]
  }]
}
```

The `actinv-waste-composition-result-1` result records both the original input
SHA-256 and the SHA-256 of the canonical projected P107 bounds input. Each
target carries the canonical response coefficients, per-nuclide lower/upper
activity projections, feasible minimum/maximum witness compositions and dual
multipliers, plus the complete flat P107 endpoint, row, constraint, coverage,
binding and class-envelope fields. External H-3 is passed unchanged to the P107
evaluator and added once. Invalid input is fully checked before the output file
is written.

## Generate and verify a native composition basis

The native composition path passed its artificial fixed-rate basis and selected-
witness control suite (P110: nine cases, 18 targets and 36 endpoints).

`actinv waste composition solve` generates pure-element responses by running
ACTINV, projects them over a bounded natural-element composition whose
coordinates sum to exactly 100 wt%, then runs every distinct projection witness
and a canonical feasible reference. This adds native basis generation and
selected-point verification to the caller-declared `composition` workflow. It
does not prove physical completeness, bound CRAM or solver error, establish
nuclear-data accuracy, or determine disposal acceptance. Selected witness
agreement does not prove that every solver value lies within the arithmetic
projection box.

The `actinv-waste-composition-solve-spec-1` schema uses the bundled Part 61
rule pack. Each component supplies an `actinv-spec-1` `base_spec` (embedded
object or file path), component mass and exactly one geometry denominator,
waste type, external-tritium declaration, `composition_wt_percent_bounds`
coordinates, selected one-based result steps, inventory coverage declaration
and reasons, plus model source and assumptions. ACTINV replaces the base
material composition for each pure-element run and fixes the requested
component mass. The command enforces the P75b-qualified natural-element set
`Al, B, C, Ca, Co, Cr, Cu, Eu, Fe, H, K, Mg, Mn, Mo, N, Na, Nb, Ni, O, P, S,
Si, Ta, Ti, V, W` and supported fixed-rate neutron features. It refuses unsupported nonlinear
options and duplicate JSON keys. A request may contain at most four components,
eight elements and four target steps per component, 32 pure-element runs, and
64 distinct full witness compositions. The wrapper and each path-based base
spec are limited to 8 MiB; a final result is limited to 32 MiB. Unsupported
features include non-neutron projectiles, feed/removal/per-step spectra,
screening, uncertainty, shielding and radiological/damage extensions, scale
overrides, gas output, non-default photon settings, and CRAM orders other than
16.

The element set comes from P75b's tested mixtures. Its superposition results
apply to the tested neutron spectra and schedules; passing these witness checks
does not extend that physical qualification to new conditions.

For a path `base_spec`, that path resolves relative to the wrapper JSON file.
Paths inside the base spec, including nuclear-data references, retain ordinary
ACTINV current-working-directory and catalog resolution. A `PreparedRun` is
reused for the basis and witness runs of that component. External H-3 remains
a separate whole-component Bq interval and is added once to the waste
evaluator; the command infers no permeation model.

The result preserves the P108 projections and adds solver certificates and
audit evidence for each pure-element basis and verified witness. Activity
responses use Bq/g, whole-component class inputs use Bq, composition weights
use wt%, and atom inventories use atoms/g. Coverage defects downgrade selected
targets to unknown while retaining known-subset calculations. The exact
generated P108 document is embedded for reuse. These deterministic calculations
do not claim probability or propagated uncertainty.

```json
{
  "schema": "actinv-waste-composition-solve-spec-1",
  "rules": "us-nrc-10cfr61.55-v1",
  "components": [{
    "id": "declared-alloy",
    "base_spec": "activation-base.json",
    "mass_g": 1,
    "displaced_volume_cm3": 1,
    "waste_type": "activated_metal",
    "external_tritium": {"status": "not_applicable"},
    "composition_wt_percent_bounds": {
      "Fe": {"lower_wt_percent": 40, "upper_wt_percent": 80},
      "Nb": {"lower_wt_percent": 20, "upper_wt_percent": 60}
    },
    "targets": [1, 2],
    "inventory_coverage": "complete",
    "unbounded_inventory_reasons": [],
    "model_source": "declared fixed-rate material envelope",
    "model_assumptions": "composition fractions form one exact 100 wt% mixture"
  }]
}
```

Here the two weight bounds jointly admit a 100 wt% mixture. The command checks
exact binary-float feasibility rather than normalizing an infeasible box.

## Classify declared activity bounds

The bounds input schema is `actinv-waste-bounds-spec-1`. It accepts only the
bundled `us-nrc-10cfr61.55-v1` rule pack. Each component declares its fixed
geometry and waste type, nuclide properties, external-tritium status, and one
or more target records. The example uses artificial C-14 values to show the
shape; replace the source and assumptions with the basis for your declared box.

```json
{
  "schema": "actinv-waste-bounds-spec-1",
  "rules": "us-nrc-10cfr61.55-v1",
  "components": [{
    "id": "example-component",
    "mass_g": 1000,
    "displaced_volume_cm3": 128.2,
    "waste_type": "general",
    "nuclide_properties": {
      "C14": {"z": 6, "half_life_s": 180808200000, "alpha_emitting": false}
    },
    "external_tritium": {"status": "not_applicable"},
    "targets": [{
      "step": 1,
      "t_s": 0,
      "activity_bounds_bq": {
        "C14": {"lower_bq": 100, "upper_bq": 200}
      },
      "inventory_coverage": "complete",
      "unbounded_inventory_reasons": [],
      "bounds_source": "illustrative declared enclosure",
      "bounds_assumptions": "artificial input; not measured or probability-based"
    }]
  }]
}
```

Run it with `actinv waste bounds activity-bounds.json`. An omitted output path
writes JSON to standard output. The `actinv-waste-bounds-result-1` result records
the exact input SHA-256 and rule-pack identity, component geometry and metadata,
activation-only and external-tritium intervals, and the merged evaluated
intervals. Each target includes lower and upper endpoint evaluations, every
Table 1 and Table 2 column constraint, row-fraction ranges, the ordered
`class_envelope`, `conservative_superset`, `class_is_stable`, and `stable_class`.
The class range is a conservative superset; intermediate
classes are not asserted to be feasible.

`inventory_coverage: "complete"` is an explicit caller declaration that omitted
nuclides are zero; ACTINV cannot verify its physical truth. Incomplete coverage,
required external tritium, or missing properties for any nuclide with positive
upper activity produce an unknown class envelope, while preserving known-subset
endpoint arithmetic. A bounded external-tritium declaration must identify its
source, set `excludes_activation` to true, and provide a whole-component Bq
interval for every selected step. The tool adds its lower bound to the
activation lower bound and its upper bound to the activation upper bound once.
An entirely zero merged inventory is stable Class A only with complete coverage
and an explicit external-tritium declaration.

For the bounds command, a bounded external-tritium declaration has this shape
(a fragment to replace the component's `external_tritium` object):

```json
{
  "status": "bounded",
  "source": "retention enclosure or measurement reference",
  "excludes_activation": true,
  "activity_bounds_bq": {
    "1": {"lower_bq": 100000, "upper_bq": 200000}
  }
}
```

The example quantities are illustrative. Supply exactly the selected step keys
and fixed H-3 properties whenever its upper activity is positive. Use
`{"status": "required"}` when a needed contribution is unavailable; the whole
box then remains unknown. Stability always depends on the declared box being
valid; it does not establish that every physical uncertainty is covered.

## Describe a component

The input is an existing `actinv run` result or `actinv mesh` NDJSON file. Select
the bundled rules by its id (`us-nrc-10cfr61.55-v1`) or give a path to a custom
rule-pack JSON file relative to the waste document. Specify
the component mass and displaced volume: its envelope with major voids removed.
Alternatively, provide mass and density to use the conservative solid-volume
basis. For a mesh, list each member cell and its mass. The command adds cell
activities before dividing by the component denominator; cell concentrations
are never averaged without their masses.

For example, a single-run classification document is:

```json
{
  "schema": "actinv-waste-spec-1",
  "rules": "us-nrc-10cfr61.55-v1",
  "input": "component-result.json",
  "targets": [2, 3],
  "nuclide_properties": {
    "Nb94": {"z": 41, "half_life_s": 100000000000, "alpha_emitting": false}
  },
  "components": [{
    "id": "synthetic-component",
    "mass_g": 1000,
    "displaced_volume_cm3": 128.2,
    "waste_type": "activated_metal",
    "external_tritium": {"status": "not_applicable"}
  }]
}
```

The properties above are artificial values illustrating the format. Supply source-appropriate properties
for every active nuclide in your result: half-life and alpha-emitter status decide
membership in aggregate categories. Missing properties produce an unknown class.
Use `general` when activated-metal limits do not apply. `targets` are one-based
result step numbers, so each identifies an actual calculated time.

## Include retained tritium

Fuel permeation and retention can add H-3 that the activation calculation does
not model. Declare it separately for each component and selected step:

```json
{
  "status": "declared",
  "source": "retention calculation or measurement reference",
  "excludes_activation": true,
  "activity_bq": {"2": 120000, "3": 110000}
}
```

These are additional whole-component activities in Bq. The declaration must
exclude the activation H-3 already in the inventory; ACTINV adds the declared
activity to any calculated H-3 in that component. Use `required` when an
external contribution is needed but unavailable; the result then reports an
unknown class alongside the calculated-only class. Declare `not_applicable`
explicitly when there is no external contribution.

## Set a class impurity budget

A budget document uses `schema: "actinv-waste-budget-1"`, a `base_spec` naming
an existing run specification by path or embedding that specification object, a
`balance` element, fixed `matrix` constituents and `impurities` in wt%, and
`targets`. Add `target_class` as `A`, `B`, or `C`, the selected `rules`, component
geometry, `waste_type`, properties and external-tritium declaration. Budget
constituents are natural-element symbols; the budget-level external-tritium
declaration uses the same status choices and maps each selected step to a
whole-component activity in Bq.

The command solves each element, intersects every applicable table constraint
across all target times, and re-solves each usable limit and joint specification
margin with the full material. It reports the binding table, column and step.
An open mixture boundary has a supremum and an interior verified composition;
the supremum is not an attained maximum. Composition bounds, infeasible cases
and absence of a positive response are reported explicitly.

Budgets require fixed rates and coupled mode with reach pruning. Self-shielding,
uncertainty, composition-dependent screening and schedule feed are refused.
Nominal classification may consume other supported run configurations, but that
does not make their composition responses linear.

The reference pack follows [10 CFR 61.55](https://www.ecfr.gov/current/title-10/chapter-I/part-61/subpart-D/section-61.55).
Single-nuclide equality is accepted where the regulation says “does not exceed”;
mixture sums use its strict less-than rule. Separate tables remain separate
constraints, and entries with no Class B/C limit retain the rule's Class B
fallback. The selected pack and its interpretation are recorded in each result.
