# Component waste classes and impurity limits

`actinv waste` evaluates nominal component inventories against an explicitly
selected U.S. 10 CFR 61.55 rule pack. It reports Class A, B, C, or above Class C
at your selected schedule steps, together with the table fractions that control
the result. `actinv waste budget` calculates and verifies impurity specifications
for a requested class.

```sh
actinv waste component.json component-waste.json
actinv waste budget class-budget.json class-budget-result.json
```

This is a concentration calculation for research and design screening. It does
not determine disposal-site acceptance, waste-form compliance, or intrusion dose.
The current commands use nominal inventories; they do not classify uncertainty
intervals or apply the proposed fusion intrusion screen.

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
