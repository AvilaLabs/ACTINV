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
derive bounds from a `RunResult`. None of these commands applies the separate
proposed fusion intrusion screen.

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
