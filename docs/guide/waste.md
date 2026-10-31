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
actinv waste budget uncertainty budget-uncertainty.json budget-uncertainty-result.json
actinv waste bounds activity-bounds.json activity-bounds-result.json
actinv waste composition composition.json composition-result.json
actinv waste composition solve native-composition.json native-composition-result.json
actinv waste intrusion-screen intrusion.json intrusion-result.json
actinv waste uncertainty uncertainty.json uncertainty-result.json
actinv waste package package.json package-result.json
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

From Python the same commands are `actinv.waste.classify`, `budget`, `bounds`, `composition`, `composition_solve`,
`intrusion_screen`, `uncertainty`, `budget_uncertainty` and `package`, and `actinv.twin`; they return the CLI's result. See [Python](python.md#waste-classification-from-python).

## Classify declared waste components in a facility twin

`actinv twin` can optionally add nominal Part 61 classifications for components
that already group mesh cells. This uses the original mesh activities and the
same `actinv waste` evaluator; cell assay, mixture, propagated and dose updates
change the twin's certified-band results, but do not change the waste
classification. Each component needs an explicit mass, either a displaced
volume or density, its waste type, and cell masses that match its existing twin
group and close to the component mass. ACTINV does not infer mass or volume from
mesh geometry.

```json
{
  "spec": "actinv-twin-1",
  "mesh_output": "facility-mesh.ndjson",
  "limits": [{"name":"heat","response":"heat.total","limit":2.0}],
  "components": {"primary": ["cell-1", "cell-2"]},
  "waste": {
    "schema": "actinv-twin-waste-spec-1",
    "rules": "us-nrc-10cfr61.55-v1",
    "targets": [1, 2],
    "nuclide_properties": {
      "C14": {"z":6,"half_life_s":1.808e11,"alpha_emitting":false}
    },
    "components": {
      "primary": {
        "mass_g": 2.0,
        "displaced_volume_cm3": 1.5,
        "waste_type": "general",
        "cell_masses_g": {"cell-1":0.5,"cell-2":1.5},
        "external_tritium": {"status":"not_applicable"}
      }
    }
  }
}
```

The `components` groups remain the only source of cell membership; names must
match the waste components, groups cannot overlap, and each `cell_masses_g`
map must contain exactly that group's cells. Selected steps must exist in each
cell, and timestamps within a component must match exactly. External H-3 is
declared separately and counted once. Missing active-nuclide properties or
required-but-undeclared H-3 preserve an unknown/conditional nominal result.

The twin output adds `waste_classification`, an ordinary
`actinv-waste-result-1` document with per-component, per-target margins and
constraints, and `waste_facility_coverage`, which lists assigned and unassigned
mesh cells and counts separate component classes at each requested step. The
summary does not combine components into a package class or determine disposal
acceptance. This classification is nominal screening only; upstream physical
reaction and decay completeness remains a separate question.

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
overrides, gas output, fission-count and spontaneous-fission options,
non-default photon settings, and CRAM orders other than 16.

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

## Classification under nuclear-data uncertainty

`actinv waste uncertainty SPEC.json [OUT.json]` asks how likely each class is when the nuclear data are uncertain. It
solves your problem once at its nominal inputs and then once for each correlated draw of the sampled channels, the
sampler that `study.robustness` uses (correlated MF=33 cross-section draws with the collapsed relative covariance, any
indefinite correlation structure repaired; decay constants and fission yields at their declared uncertainties; and
optionally flux and composition). Every solve carries a consistent inventory, so each sample is classified with the nominal
evaluator and the table sums keep their correlations. The first-order `uncertainty` block cannot give a band on a
table sum, because it reports no covariance between nuclides.

```json
{
  "schema": "actinv-waste-uncertainty-spec-1",
  "problem": "fns-iron.json",
  "rules": "us-nrc-10cfr61.55-v1",
  "targets": [3, 5],
  "nuclide_properties": "from_decay_data",
  "components": [{
    "id": "first-wall-plug",
    "mass_g": 1000,
    "density_g_cm3": 7.8,
    "waste_type": "activated_metal",
    "external_tritium": {"status": "not_applicable"}
  }],
  "sampling": {
    "channels": {"cross_section_mf33": true, "decay_constants": true},
    "covariance": {"path": "catalog:tendl-2025-neutron-709g-covariance"},
    "samples": 512,
    "seed": 20261016,
    "keep_samples": "samples"
  },
  "confidence_level": 0.95
}
```

`problem` is an `actinv-spec-1` file, relative to this one; `rules`, `targets` and `components` are those of the
nominal waste command, with exactly one component and no mesh cells, and the declared H-3 of `external_tritium` is
added to every sample without being sampled. `nuclide_properties` is either the object of `actinv-waste-spec-1` or the
string `"from_decay_data"`, which derives the properties from the decay data for every nuclide with positive activity
at a target step in the nominal run or any sample. `sampling.channels` and `sampling.covariance` are the objects of
`study.robustness` (`cross_section_mf33`, `flux_rel_std`, `composition_rel_std`, `decay_constants`,
`fission_yields`), `samples` is at least 20 and at most 4096, and `confidence_level` defaults to 0.95. With
`keep_samples`, each sample's perturbed problem and result are written to that folder as `rob_<i>.json` and
`rob_<i>.out.json`, byte for byte what a `study.robustness` run with the same channels, sample count and seed writes,
apart from the wall-clock `ms` of each solve.
The command refuses, and writes nothing, when the problem enables gas production, switches library or projectile
during the schedule, has power-driven or wall-loading steps, a per-step spectrum or partition, or an `uncertainty`
block; when no channel is enabled; when `samples` is below 20; for a confidence level outside (0, 1); and for unknown
keys or another schema.

The `actinv-waste-uncertainty-result-1` result gives, per component and target step:

- `nominal`: the target as `actinv waste` reports it.
- `samples` and `failed_samples` (`[{sample, error}]`). A sample whose solve or classification fails counts in every
  denominator as class `unknown`, so a failure can only weaken a class.
- `classes`: `count`, `probability` and a two-sided Clopper-Pearson interval (`lower`, `upper`) at `confidence_level`
  for `A`, `B`, `C`, `above_class_c` and `unknown`; and `at_most_class`: the same for P(class at most X), X = `A`,
  `B`, `C`, `above_class_c`.
- `established_class` and `needed_samples`. The classes are ordered A, B, C, above Class C. The established class is
  the first one for which the lower end of the interval of P(class at most X) is at least the confidence level.
  Even when every sample agrees this needs (α/2)^(1/n) ≥ 1 − α, which is at least **72 samples at confidence 0.95**
  (`minimum_samples_for_establishment` states the number for your level). When nothing is established the result is
  `not_established`, and `needed_samples` gives, for the first class whose observed cumulative frequency exceeds the
  level, the smallest sample count (up to 1 000 000) at which that frequency would establish it, or says that the
  observed frequency cannot establish a class.
- `constraints`: for each table constraint (`target_class`, `table`, `column`) the 5th, 50th and 95th nearest-rank
  percentiles of the normalized sum (the class boundary is 1) over the samples with an inventory, and the count and
  fraction above 1.

Per component, `earliest_established_step` names the earliest target step at which each class is the established
class. The `coverage` block lists `channels_sampled`, the MF=33 rows, decay constants and yields the sampled channels
left uncovered (by name), the nominal run's completeness ledger (recorded when the problem lists `audit` in
`options.outputs`) and a fixed statement of what the probabilities do not include.

The probabilities are conditional on the sampled channels. They exclude MF=32 and MF=40 covariance, correlation
between channels, composition, geometry, model and flux-systematic uncertainty, except where a listed channel samples
it, and measured coverage of these channels on the FNS corpus is below nominal (see
[Data limitations](https://github.com/AvilaLabs/ACTINV/blob/master/docs/DATA_LIMITATIONS.md)), so the intervals can be
overconfident. They are not tolerance limits, a budget at a confidence level, a package class or a disposal
acceptance.

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

`actinv waste budget` writes its result even when a verification point misses; the `verification` block shows the
miss and the command exits 3 (`verified` false). Refused input still exits 1 and writes nothing.

Budgets require fixed rates and coupled mode with reach pruning. Self-shielding,
uncertainty, composition-dependent screening and schedule feed are refused.
Nominal classification may consume other supported run configurations, but that
does not make their composition responses linear.

The reference pack follows [10 CFR 61.55](https://www.ecfr.gov/current/title-10/chapter-I/part-61/subpart-D/section-61.55).
Single-nuclide equality is accepted where the regulation says “does not exceed”;
mixture sums use its strict less-than rule. Separate tables remain separate
constraints, and entries with no Class B/C limit retain the rule's Class B
fallback. The selected pack and its interpretation are recorded in each result.

## Budgets under nuclear-data uncertainty

`actinv waste budget uncertainty SPEC.json [OUT.json]` asks how large an impurity can be when the nuclear data are
uncertain. The nominal budget above solves each element once and gives, per impurity, the supremum of its weight
percent for which every table constraint at every target stays inside the target class. Each correlated draw of the
data gives its own element responses and therefore its own supremum. The command draws the sampled channels once per
sample, from the sampling plan of the full budget material, and applies the same factors (cross-section rate scales,
decay-constant scales, yield scales and flux factor) to every element solve of that sample, so the element responses
of one sample come from one data set. It then applies the nominal interval algorithm to each sample's responses.

An impurity level x keeps the class in a fraction F(x) of data draws. The budget at confidence γ is the k-th smallest
per-sample supremum, with k the largest index for which the lower end of the two-sided Clopper-Pearson interval at γ
of (n − k + 1)/n is at least γ. Any x below it keeps the class with probability at least γ, at confidence γ, as long
as the samples represent the data uncertainty. This is the order-statistic (Wilks) tolerance bound: it is exact for
any distribution of the supremum and needs no normality and no first-order expansion. It is reported as an open bound,
not an attained maximum. The same k applies to every impurity and to the joint specification scale factor.

Some sample count is needed before any budget exists. With k = 1 the bound is the smallest supremum, which needs
(α/2)^(1/n) ≥ γ: **72 samples at confidence 0.95**. A spec with fewer samples is refused, and the refusal names the
minimum (`order_statistic.minimum_samples` states it for your level).

```json
{
  "schema": "actinv-waste-budget-uncertainty-spec-1",
  "budget": "class-budget.json",
  "confidence_level": 0.95,
  "sampling": {
    "channels": {"cross_section_mf33": true, "decay_constants": true, "flux_rel_std": 0.05},
    "covariance": {"path": "catalog:tendl-2025-neutron-709g-covariance"},
    "samples": 200,
    "seed": 146001,
    "keep_samples": "samples"
  }
}
```

`budget` is the path of an `actinv-waste-budget-1` document, relative to this file, or that document embedded.
`sampling` is the block of `actinv waste uncertainty`: `samples` from 20 to 4096, a `seed`, the `channels` and
`covariance` of `study.robustness`, and an optional `keep_samples` folder, relative to this file. The composition
channel is refused, because composition is what the budget solves for. The command also refuses everything
`actinv waste budget` refuses, and a base spec that enables gas production, declares its own rate, decay or yield
scales, or switches library, projectile or spectrum during the schedule. Every refusal is made before any solve, and
nothing is written.

The `actinv-waste-budget-uncertainty-1` result carries:

- `nominal`: the complete `actinv waste budget` result, unchanged.
- `order_statistic`: `k`, `n` and `minimum_samples`.
- `quantities`: one entry per impurity and one for `joint_scale_factor`, with the unit (`wt_pct` or `scale_factor`),
  the `status`, the `budget`, the `nominal_supremum` for comparison, the per-sample suprema
  (`suprema_by_sample`, `suprema_sorted`), `sample_status` counts (`bound`, `capped`, `failed`, `infeasible`,
  `no_response`) and the `binding_tally`: how many samples were bound by each table, column and step. A sample
  whose solve fails or whose interval is infeasible counts as a supremum of 0, so it can only lower the budget. A
  supremum limited only by the balance (no constraint binds) is `capped` and enters the order statistic at the cap.
- `status`: `established`, `not_established` or `unbounded`. The order statistic bounds the upper end only. When the
  nominal budget, or any sample, has a positive lower bound for an impurity (the class needs at least some of it),
  the budget is `not_established` with reason `positive_lower_bound` and the count of such samples; two-sided
  budgets are not claimed. An impurity with no response in any constraint is `unbounded`.
- `verification`: for each established quantity, the samples at sorted ranks k − 2 to k + 2 are re-solved with the full
  material, the impurity (alone, the others at specification) or the joint scale factor at 0.99 and 1.01 times that
  sample's own supremum and with that sample's own factors, and classified with the waste evaluator; every sample at
  rank k or above is re-solved at 0.99 times the reported budget. A row records the classes at the target steps and
  whether they are all at most the target class. `verification_pass` is true when each 0.99 point is inside the class
  and each 1.01 point is outside it. The command exits 3, after writing the result, when it is false.
- `failed_samples`, `coverage` (the channels sampled, the inputs they left uncovered and a fixed statement) and
  `not_claimed`.

With `keep_samples`, each sample writes `sample_NNNN.json`: its factor block, each element's solved activities and
class-constraint sums with the factors actually applied to that solve, and the sample's intervals.

The budgets are conditional on the sampled channels, and each sample assumes the activation response is linear in
composition, as the nominal budget does. Composition, geometry and model uncertainty are not sampled. Measured coverage
of these channels on the FNS corpus is below nominal (see
[Data limitations](https://github.com/AvilaLabs/ACTINV/blob/master/docs/DATA_LIMITATIONS.md)), so a budget can be
optimistic. A budget is not a composition tolerance, a procurement specification, a package class or a disposal
acceptance.

## Classify a package of discrete items

`actinv waste package SPEC.json [OUT.json]` classifies one disposal container that holds several discrete items, such
as activated steel pieces of different sizes, a sealed source or a cartridge filter, under the NRC Concentration
Averaging and Encapsulation Branch Technical Position (Revision 1, Volume 1, February 2015; ADAMS ML12254B065; "BTP"
below). `actinv waste` classifies one component on its own concentrations. A container of mixed items cannot simply
be averaged: the BTP limits how concentrated an individual item may be before it has to be taken out of the average,
how small items and sectioned components are grouped, and how much credit encapsulation earns. Homogeneous
(blendable) waste is not covered. The BTP's Tables 2 and 3 and its thresholds are bundled as
`us-nrc-ca-btp-2015-rev1-v1`; the Part 61 limits come from the same rule pack as `actinv waste`. "BTP Table n" and
"CFR Table n" (10 CFR 61.55) are different tables throughout.

An item has an id, a BTP waste type (`activated_metal`, `contaminated_material`, `design_radioactivity_component`,
`sealed_source` or `cartridge_filter`), a mass in g and a volume in cm3: the displaced volume (envelope with major voids
removed), or for a cartridge filter the envelope volume (BTP Table 4). Its activity is either taken from an ACTINV run
result (the per-gram activities at each target step times the item mass; a mesh result needs `cells` as in
`actinv waste`) or declared as whole-item Bq by nuclide and step, which suits a sealed source or a measurement.

```json
{
  "schema": "actinv-waste-package-spec-1",
  "rules": "us-nrc-10cfr61.55-v1",
  "targets": [2, 3],
  "screen": "none",
  "nuclide_properties": {
    "Nb94": {"z": 41, "half_life_s": 640000000000, "alpha_emitting": false},
    "Cs137": {"z": 55, "half_life_s": 951000000, "alpha_emitting": false}
  },
  "items": [
    {"id": "plug-1", "waste_type": "activated_metal", "mass_g": 7800, "displaced_volume_cm3": 1000,
     "activity": {"result": "plug-result.json"}},
    {"id": "rod-a", "waste_type": "activated_metal", "mass_g": 780, "displaced_volume_cm3": 100,
     "section_of": "rod-7", "activity": {"result": "rod-result.json"}},
    {"id": "rod-b", "waste_type": "activated_metal", "mass_g": 780, "displaced_volume_cm3": 100,
     "section_of": "rod-7", "activity": {"result": "rod-result.json"}},
    {"id": "source-1", "waste_type": "sealed_source", "mass_g": 400, "displaced_volume_cm3": 60,
     "activity": {"declared_bq": {"2": {"Cs137": 150000000}, "3": {"Cs137": 148000000}}}}
  ],
  "encapsulation": null
}
```

`rules`, `targets` and `nuclide_properties` are those of `actinv waste`. `btp` is optional: the bundled id (the
default) or the path of an `actinv-waste-btp-1` file, relative to the spec. `screen` is `none` (the default),
`activity_37mbq` or `highest_item`. `section_of` names the original component an item was cut from. The command refuses,
and writes nothing, for another schema, an unknown waste type, a missing or non-positive mass or volume, duplicate ids,
no items, a `section_of` whose pieces differ in waste type or are alone in the package, an unknown screen, a declared
activity step that is not a target, and the encapsulation inputs described below.

### What the command does at each target step

1. Items are grouped by waste type (BTP 3.4). Each item alone is evaluated with the nominal Part 61 evaluator, which
   gives its own class and its concentrations. Within a group the command averages all items over their summed
   volume and mass, finds the more restrictive of CFR Table 1 and 2 (the one giving the higher class; on a tie the
   larger sum of fractions) and decides whether the primary gamma emitters Co-60, Nb-94 and Cs-137 are
   classification-controlling: more than 50 % of that table's sum of fractions (BTP p.25).
2. For each candidate class X from A to C, every item is tested against the limits of X:
   - *Primary gamma emitters.* The item passes if its sum of fractions against BTP Table 2 is at most 1, or if each
     primary gamma of concern meets the Factor of 2 (controlling) or the Factor of 10 (not controlling) against the
     class limit of X. Items of 280 cm3 or more use their own sum; items under 280 cm3 share one sum over their
     summed inventory, and the pool passes or fails as a whole. The result records which limit each item met.
   - *Other radionuclides of concern.* H-3, C-14, Ni-59 and Ni-63 pass BTP Table 3 or the Factor of 10, nuclide by
     nuclide. The alpha-emitting transuranics with half-life above 5 years (excluding Pu-241 and Cm-242) are one entry
     per item: their summed activity against BTP Table 3, or their summed concentration against the Factor of 10 of the
     CFR Table 1 transuranic row. Nuclides outside Tables 2 and 3 use the Factor of 10 only.
3. Failing items are removed from the average and classified alone (BTP 3.3.1). The remaining items are classified
   on their volume- and mass-averaged concentrations; call that class Y. X qualifies when Y is not above X. The class
   of the group is the lowest qualifying X, reported as X because the items were tested at X's limits; if none
   qualifies the group is above Class C. All three candidates are listed in the result.
4. Pieces of one sectioned component in the package are treated as that component (BTP 3.3.2.3) when no piece exceeds
   BTP Table 3 and no piece under 280 cm3 exceeds BTP Table 2. Pieces that do are separate items, and the result
   lists them as `split_out`. The test uses the BTP columns of each candidate class X, so the merge can differ between
   candidates; the step reports the sections of the chosen candidate. Item tests name a merged component by its
   `section_of` id.
5. The package class is the highest class of any group and of any item classified alone (BTP 3.4). An item left out
   of the average therefore still counts.

The two optional screens (BTP 3.3.2.1) replace step 2. With `activity_37mbq`, a group whose items each have a total
activity below 37 MBq is classified on the average of the whole group; otherwise the item tests run. With
`highest_item` the group takes the highest class of any one item.

### Readings fixed in advance

The BTP is silent or ambiguous in places. The readings below were fixed before the implementation (protocol P147) and
are stated in every result. They are choices made here, not statements of what the BTP says.

- The volume limit for encapsulation is 331 ft3 = 9.373 m3, the value common to the BTP text (9.5 m3), Figure 5 and
  Volume 2 (9.4 m3). A waste loading of at least 14 % qualifies.
- The class limit in the Factors of 2 and 10 is that of the class being tested: 0.1 times the CFR Table 1 value for
  Class A and B and the Table 1 value for Class C, or the CFR Table 2 column of that class. Where the column has no
  limit (Co-60, H-3 and the under-5-year group in Classes B and C) the factor test does not apply to that nuclide in
  that class, and the BTP is not cited for it.
- A radionuclide of concern exceeds 1 % of its class limit for the class being tested.
- The 37 MBq screen uses the total activity of the item, strictly less than 37 MBq.
- An item of exactly 280 cm3 is individual for BTP Table 2.
- A Factor test passes below the factor times the limit (strictly); a BTP Table 2 sum of fractions passes at 1 or less
  and a BTP Table 3 activity at the table value or less.
- The waste loading is the sum of the encapsulated items' volumes over the total encapsulated volume, unless the
  waste volume is declared. The 0.2 m3 cap applies to volume-based limits and the 500 kg cap to mass-based limits.

### Encapsulation

Add an `encapsulation` block to describe one encapsulated container; every item is then encapsulated:

```json
"encapsulation": {"total_volume_cm3": 500000, "encapsulant_mass_g": 900000,
                  "encapsulant_density_g_cm3": 2.0, "waste_volume_cm3": null}
```

The item tests of step 2 apply unchanged to the items. The credited volume and mass then follow BTP 3.3.4. With a
loading of at least 14 % and a total volume of at most 331 ft3, the average is taken over the whole encapsulated
volume and over the item mass plus the encapsulant mass. With a lower loading, the credited volume is at most 0.2 m3
and the credited mass at most 500 kg, and never more than the actual volume or mass. A loading of at least 14 % in a
container above 331 ft3 gets no credit (`alternative_approach_required`, no class). The encapsulant mass is credited at
a density of at most 2.5 g/cm3. Separately, the activity of all items averaged over the waste and the whole encapsulant
must not exceed Class C (`exceeds_class_c_cap` otherwise). An encapsulated container is one waste form, so its items
form one group whatever their waste types. When they mix activated metal with other types, the group is evaluated,
item tests included, under both the activated-metal and the general rule rows (groups
`encapsulated_activated_metal_rules` and `encapsulated_general_rules`) and the higher class is kept. An item that fails
its tests is classified alone, without encapsulation credit. `waste_volume_cm3` overrides the summed item volumes. The command refuses a non-positive field,
a waste volume above the total volume (a loading above 1), an encapsulation block together with an item flagged
`"encapsulated": false` (encapsulated and unencapsulated items do not share a package), and a screen other than `none`.

### Result

The `actinv-waste-package-1` result holds the SHA-256 of the spec, the rule pack and the BTP data, and for every
target step: the package class and status, each item (volume, mass, total activity, own class, activities), the
sectioned components, each group's sums of fractions for both CFR tables, the restrictive table, the primary gamma share
and whether it controls, every candidate with its small-item pool, per-item tests and limit met, removed items,
remainder class, volume and mass, and the encapsulation block (waste volume, loading, credited volume and mass,
status, class and the Class C cap). An item with a nuclide that has no properties makes its group and the package
`unknown`, and the group lists those items. Alongside are the readings, the implementation choices and the list of what
is not claimed.

The result is consistent with the BTP only under the readings above, and only where the disposal site's licence adopts
the BTP; the site's licence conditions override it. It is not a classification for shipment or a disposal acceptance,
and it does not cover blending of homogeneous wastes (BTP 3.2), alternative approaches (BTP 3.8), 10 CFR 61.58
authorizations or exemptions, structural stability under 10 CFR 61.56, centring procedures, compatibility documentation
or the encapsulation topical reports.

## Clearance against national tables

`actinv clearance` certifies the clearance index at one step, per cell or for one run result, against the IAEA 2004
table (RS-G-1.7) unless you choose another. `--jurisdiction de`, `uk` or `ca` evaluates the same statistic, the sum of
activity over limit with its certified probability, against the legal text of Germany, the United Kingdom or Canada.
`--jurisdiction iaea` is the default. A jurisdiction cannot be combined with `--limits`. The tables are copies of the
laws, not official versions, and choosing a table, a route and the conditions attached to a clearance decision is yours.

| `--jurisdiction` | Table | Limit used | Routes (`--route`) |
| --- | --- | --- | --- |
| `de` | Strahlenschutzverordnung (StrlSchV 2018), Anlage 4 Tabelle 1, as last amended by Art. 10 G v. 23.10.2024 | Bq/g, with Tabelle 2 for the daughters | `unrestricted-clearance` (default), `rubble-gt-1000-mg-a`, `soil`, `landfill-le-100-mg-a`, `incineration-le-100-mg-a`, `landfill-le-1000-mg-a`, `incineration-le-1000-mg-a`, `scrap-metal-recycling` |
| `uk` | Environmental Permitting (England and Wales) Regulations 2016, Schedule 23 Part 3, Table 2 "Concentration of radionuclides" | Bq/g, solid or relevant liquid | `table-2-solid-or-relevant-liquid` |
| `ca` | Nuclear Substances and Radiation Devices Regulations (SOR/2000-207), Schedule 2 "Unconditional Clearance Levels" | Bq/g | `unconditional-clearance` |

Germany: column 3 of Tabelle 1 is both the Freigrenze as specific activity and the level for unrestricted clearance of
solid and liquid material. The route columns are the specific clearance of building rubble over 1000 Mg/a (column 6),
soil areas (7), landfill and incineration up to 100 Mg/a (8, 9) and up to 1000 Mg/a (10, 11), and scrap metal for
recycling (14). A blank cell means the table has no value for that nuclide in that column, so the nuclide is reported
in `not_listed` for that route. The surface columns in Bq/cm2, the total-activity Freigrenze and the HRQ and half-life
columns are not evaluated. The regulation lets nuclides be left out of the sum when they are at most 10 percent of it;
ACTINV does not apply that allowance. A parent marked `+` includes the daughters of Tabelle 2. Where a nuclide has a plain and a `+` row, each column uses the smaller value.

United Kingdom: the regulation calls Table 2 the concentration table referred to in paragraphs 5 and 6 of Part 2, not a
clearance table, and its catch-all row (0.01 Bq/g, or a dose-based concentration, for any other artificial radionuclide)
is not applied. A suffix `+` or `sec` marks a parent in secular equilibrium with the daughters in Table 3. Where a
nuclide is printed in several forms (Th-232, U-235, U-238, Cf-253; in Germany Zr-97, Pb-212 and Th-232) the form with the
smallest limit is used, so U-235 and U-238 take the `sec` value of 0.01 Bq/g with their whole Table 3 chain covered. A
progeny is left out of the sum only if the chosen form covers it, and a plain row covers none. The forms and the choice
are recorded per nuclide as `form_choice` in the sidecar.

Canada: Schedule 2 is the clearance schedule; Schedule 1 holds exemption quantities and is not used. A nuclide that
Schedule 2 does not list has the regulation's own level of 1 Bq/g up to atomic number 81, which ACTINV applies and marks
`"basis": "default_rule"`. Above 81 the level is 1 Bq/g without alpha emission and 0.1 Bq/g with it; ACTINV does not
decide alpha emission and reports those nuclides in `not_listed`. A footnote marks parents whose progeny in secular
equilibrium are included.

Each record keeps the fields of the default evaluation: `activity_Bq_g`, `limit_Bq_g` and `ratio` per nuclide, the sum of
fractions as `sum_ratio`, and the driving nuclide as `dominant_ratio`. With a jurisdiction it adds:

- `not_listed`: nuclides present above the negligible threshold (`--negligible`, default 1e-9 Bq/g) that the table
  does not list for the route, with the activity and the reason. They are not part of the sum and are never treated as
  zero or dropped.
- `below_negligible_unlisted`: the count and total activity of unlisted nuclides below that threshold.
- `covered_by_parent`: progeny whose parent is present, which the parent's value already includes, left out of the sum
  as in the response tables of `actinv run`.
- `basis` on each nuclide (`table` or `default_rule`), and in the header a `jurisdiction` block with the route, the
  citation, the table's SHA-256, the licence and a note on what the regulation allows that ACTINV does not apply.

The same tables are bundled for `actinv run` as `bundled:de-strlschv-2018-anlage-4`,
`bundled:uk-epr-2016-sch23-pt3-table-2` and `bundled:ca-nsrdr-2000-207-schedule-2`, with one `clearance_index` response
per route and the coefficients in Bq/kg.

Sources and licences. Germany: the text of the ordinance is an official work (amtliches Werk) without copyright under
section 5(1) UrhG; it is published by the Bundesministerium der Justiz and is not an official version, which is the
Bundesgesetzblatt. United Kingdom: contains public sector information licensed under the Open Government Licence v3.0
(legislation.gov.uk). Canada: reproduced from the Justice Laws Website under the Reproduction of Federal Law Order
(SI/97-5); not an official version. The converter is `scripts/regulatory/convert_national_clearance.py`, the archived
source files with their SHA-256 are under `controls/fixtures/clearance_sources/`, and
`controls/check_jurisdiction_tables.py` re-reads them with an independent reader and checks every shipped value.
