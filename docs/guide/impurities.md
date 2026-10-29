# Screen a material's impurities

Which impurities in an alloy matter, at which cooling times, for which concern, and how much of each can you
tolerate? `actinv impurities` answers with one command and one input. It solves each element of the alloy once as a
pure material, forms every activity, heat, clearance-index and waste-class table sum of any composition as a weighted
sum of those runs, and reports rankings, limits and worst cases over the impurity ranges you declare. Every number it
reports is then checked by a full solve of the material at that composition.

```sh
actinv impurities impurities.json impurities-result.json
actinv impurities impurities.json impurities-result.json --no-verify
```

The example below is `examples/impurities_ra_steel/`: a reduced-activation ferritic-martensitic steel in the fusion
first-wall spectrum of `examples/optimize_ra_steel`, after two full-power years at a total neutron flux of
5e14 n/cm2/s, at 1 d, 1 y, 10 y and 100 y of cooling. **Its values are illustrative.** They are not a qualified
material specification, a design limit or a regulatory position.

## Describe the screen

The document `impurities.json` uses the schema `actinv-impurities-1`:

```json
{
 "schema": "actinv-impurities-1",
 "base_spec": "base_spec.json",
 "balance": "Fe",
 "matrix": {"Cr": 9.0, "W": 1.1, "Mn": 0.4, "V": 0.2, "Ta": 0.12, "C": 0.11},
 "impurities": {
  "Co": {"nominal": 0.005, "min": 0.0, "max": 0.01},
  "Ni": {"nominal": 0.005, "min": 0.0, "max": 0.05},
  "Nb": {"nominal": 0.001, "min": 0.0, "max": 0.005},
  "Mo": {"nominal": 0.005, "min": 0.0, "max": 0.02},
  "Cu": {"nominal": 0.005, "min": 0.0, "max": 0.02},
  "Al": {"nominal": 0.005, "min": 0.0, "max": 0.01},
  "N": {"nominal": 0.03, "min": 0.015, "max": 0.045},
  "Ag": {"nominal": 0.0001, "min": 0.0, "max": 0.0005}
 },
 "targets": [2, 3, 4, 5],
 "endpoints": [
  {"endpoint": "activity.total"},
  {"endpoint": "heat.total", "limit": 1.4e-2},
  {"endpoint": "dose.contact_proxy"},
  {"endpoint": "activity:Nb94", "limit": 100.0},
  {"endpoint": "clearance_index"},
  {"endpoint": "waste_class", "class": "C"}
 ],
 "waste": {
  "rules": "us-nrc-10cfr61.55-v1",
  "mass_g": 1000.0,
  "density_g_cm3": 7.8,
  "waste_type": "activated_metal",
  "external_tritium": {"status": "not_applicable"}
 }
}
```

- `base_spec` is an `actinv-spec-1` run (a path relative to this file, or an embedded object). Its flux, schedule and
  data are used; its `material` is replaced. The command forces `mode: coupled` and `prune: reach`, the configuration
  proven additive, and records that. The example's schedule is two years of irradiation followed by cooling steps, so
  result steps 2 to 5 end at 1 d, 1 y, 10 y and 100 y of cooling.
- `balance` takes the remainder to 100 wt%. `matrix` holds the fixed constituents. Each impurity gives wt% as
  `nominal`, `min` and `max` (`min` &le; `nominal` &le; `max`), or one number for all three.
- `targets` are one-based result step numbers, as in `actinv clearance`.
- Each endpoint is a name or an object with `endpoint`, an optional `limit` and, for `waste_class`, the class `A`,
  `B` or `C` you require. `activity.total` is in Bq/g, `heat.total` in W/g, `activity:<nuclide>` in Bq/g,
  `dose.contact_proxy` in Gy/h, `clearance_index` is the IAEA index (limit 1, or your own `limit`; the table can be
  replaced with `limits`, as for `actinv budget`). A limit on `waste_class` is refused: its limits are the Part 61
  table sums.
- `waste` is required by a `waste_class` endpoint: the rule pack, mass, one geometry denominator, waste type and
  external-tritium declaration, as for [`actinv waste budget`](waste.md#set-a-class-impurity-budget). Nuclide
  properties are derived from the run's decay data.
- `dose.contact_proxy` needs the base spec's `photon` section with a photon response that has attenuation curves for
  every element in the material.

The command refuses, and writes nothing, when the response would not be linear in composition: `self_shielding`,
`options.screen`, a schedule `feed`, power-driven steps and an `uncertainty` section in the base spec (first-order
bands do not compose across element runs; nuclear-data uncertainty is the `sampling` block, supported below). It also
refuses a `min` above `nominal` or a `nominal` above `max`, an unknown endpoint, `dose.contact_proxy` without a
`photon` section and a `limit` on `waste_class`.

## Read the result

The result `actinv-impurities-result-1` holds, for each target step, one entry per endpoint. From the repository
root, with the data installed:

```sh
actinv impurities examples/impurities_ra_steel/impurities.json impurities-result.json
```

Output, the cobalt entry of `activity.total` at step 2 (1 d of cooling), in Bq/g:

```text
            "Co": {
              "contribution_at_max": 310378679.26071066,
              "contribution_nominal": 155189339.63035533,
              "gradient_per_wt_pct": 31037867926.071064,
              "max_wt_pct": 0.01,
              "min_wt_pct": 0.0,
              "nominal_wt_pct": 0.005,
              "top_nuclides": [
                [
                  "Co58",
                  165792519.95582134
                ],
                [
                  "Co58m1",
                  17348137.37118505
                ],
                [
                  "Fe59",
                  10081400.746684032
                ]
              ]
            },
```

Cobalt at its nominal 0.005 wt% adds 1.6e8 Bq/g at one day, mostly Co-58 made by (n,2n) on Co-59.

For a scalar endpoint the entry gives:

- `matrix_only`, `nominal` and `worst_case`: the value with the matrix alone, at the nominal composition and at the
  worst case over the declared ranges. `worst_case.corner` names, for each impurity, whether the worst case takes
  `min` or `max`.
- `impurities.<element>`: the `gradient_per_wt_pct` at nominal, the `contribution_nominal` (the endpoint at nominal
  minus the endpoint with that impurity removed and the balance element taking its place), the `contribution_at_max`
  (the impurity at its range maximum against removed, the others at nominal) and the `top_nuclides` behind the
  contribution: the three largest per-nuclide shares of the contribution's sign. For an impurity that lowers the
  endpoint, they are the balance element's nuclides it displaces. `ranking` lists the impurities by contribution
  at nominal. `top_nuclides` is `null` for `heat.total`, which the run does not resolve by nuclide.
- With a `limit`: `limits.alone` (the impurity is the only one present), `limits.others_at_nominal` and
  `limits.others_at_worst` (the others at their worst-case values), each with a `wt_pct` and a `status`, and `joint_k`,
  the factor by which every impurity's nominal weight can be scaled to reach the limit.

```sh
actinv impurities examples/impurities_ra_steel/impurities.json impurities-result.json
```

Output, the cobalt entry of `heat.total` at step 2, in W/g against the 1.4e-2 W/g limit:

```text
            "Co": {
              "contribution_at_max": 0.00005761499161947032,
              "contribution_nominal": 0.00002880749580973516,
              "gradient_per_wt_pct": 0.005761499161947032,
              "limits": {
                "alone": {
                  "status": "limit",
                  "wt_pct": 0.06871907545895065
                },
                "others_at_nominal": {
                  "status": "limit",
                  "wt_pct": 0.06435931557971897
                },
                "others_at_worst": {
                  "status": "limit",
                  "wt_pct": 0.04183787399387127
                }
              },
              "max_wt_pct": 0.01,
              "min_wt_pct": 0.0,
              "nominal_wt_pct": 0.005,
              "top_nuclides": null
            },
```

With the other impurities at nominal, cobalt may rise to 0.064 wt% before the steel's heat one day after shutdown
reaches the limit; with the others at their worst case, to 0.042 wt%.

A status is `limit` (the number exists), `no response` (the impurity does not raise the endpoint, or the limit lies
beyond what the balance element can give up) or `infeasible` (the other constituents alone already reach the limit).
For the contact proxy a status `unavailable` means the proxy is `null` there and the entry says why.

The `summary` gives, for each endpoint, the `tightest_limit` of each impurity across targets with the `step` that
binds it, the impurities `ranking` by their largest contribution across targets, and the `worst` value or class
across targets.

```sh
actinv impurities examples/impurities_ra_steel/impurities.json impurities-result.json
```

Output, the tightest `heat.total` limits with the others at nominal, from `summary`:

```text
        "others_at_nominal": {
          "Ag": {
            "status": "limit",
            "step": 2,
            "wt_pct": 0.12560332896898269
          },
          "Al": {
            "status": "limit",
            "step": 2,
            "wt_pct": 0.1378665895018817
          },
          "Co": {
            "status": "limit",
            "step": 2,
            "wt_pct": 0.06435931557971897
          },
          "Cu": {
            "status": "limit",
            "step": 2,
            "wt_pct": 2.073400347459034
          },
          "Mo": {
            "status": "limit",
            "step": 2,
            "wt_pct": 0.8845944427497271
          },
          "N": {
            "status": "no response",
            "step": null,
            "wt_pct": null
          },
          "Nb": {
            "status": "limit",
            "step": 2,
            "wt_pct": 0.1891269323069253
          },
          "Ni": {
            "status": "limit",
            "step": 2,
            "wt_pct": 0.16134111838932721
          }
        },
```

Every heat limit binds at one day of cooling, where the iron, chromium and manganese products still dominate.
Nitrogen gives no response. In place of iron it lowers the heat up to 10 y, and at 100 y the limit lies beyond what
the balance element can give up.

### The waste class

A `waste_class` entry reports `nominal_class`, and `worst_case` with the class over the ranges and its `binding`
table, column and nuclides. Each Part 61 table sum is linear in composition, so each is maximised over the range box
at its own corner and the class is read from the maximised sums. That is a conservative bound, since the class never
decreases when a sum increases. The impurities' class limits are those `actinv waste budget` gives, in
`summary.<endpoint>.impurity_class_limits`; an open boundary is a supremum, not an attained maximum.

```sh
actinv impurities examples/impurities_ra_steel/impurities.json impurities-result.json
```

Output, the start of the `waste_class:C` entry's `worst_case` at step 2:

```text
          "worst_case": {
            "binding": {
              "column": null,
              "constraint": "table_1",
              "corner": {
                "Ag": "max",
                "Al": "max",
                "Co": "max",
                "Cu": "max",
                "Mo": "max",
                "N": "max",
                "Nb": "max",
                "Ni": "max"
              },
              "normalized_sum_max": 0.7039397258233535,
```

With every impurity at its maximum, the Table 1 sum reaches 0.70 of its Class C limit, so the steel stays Class C
over the declared ranges. The entry's `ranking` names the impurities behind it: nitrogen (C-14), then molybdenum and
niobium (Nb-94).

## Add nuclear-data uncertainty

Everything above uses the nominal nuclear data. Add a `sampling` block and every value and limit also gets a bound over
the nuclear-data uncertainty. The worked example `examples/impurities_ra_steel/impurities_uncertainty.json` is
`impurities.json` plus two keys:

```json
 "confidence_level": 0.95,
 "sampling": {
  "channels": {"cross_section_mf33": true, "decay_constants": true},
  "covariance": {"path": "catalog:tendl-2025-neutron-709g-covariance"},
  "samples": 72,
  "seed": 158001
 }
```

```sh
actinv impurities examples/impurities_ra_steel/impurities_uncertainty.json impurities-uncertainty-result.json
```

`sampling` is the block of [`actinv waste budget uncertainty`](waste.md#budgets-under-nuclear-data-uncertainty):
`samples` from 20 to 4096, a `seed`, the `channels` and `covariance` of `study.robustness`, and an optional
`keep_samples` folder, relative to this file. `confidence_level` runs from 0.5 to 0.999 and defaults to 0.95.
The composition channel is refused, because composition is what the screen varies. So are a base spec that enables gas
production, declares its own rate, decay or yield scales, or switches library, projectile or spectrum during the
schedule, and a `confidence_level` without `sampling`. Every refusal is made before any solve, and nothing is written.

**Method.** At one fixed draw of the data the activation is again linear in composition, so the algorithm above applies
to that draw unchanged. The command draws the factors of every sample once, from the sampling plan of the full alloy at
the *plan composition* (the matrix, every impurity at its range maximum and the balance element taking the remainder, so
every element the screen can contain is in the plan), and applies the same factors to every element solve of that
sample. It then runs the whole screen on the sample's element responses: the matrix-only, nominal and worst-case value
of every endpoint at every target, each impurity's contribution at nominal, the limits and `joint_k`, and for
`waste_class` the nominal class, the worst class and the per-impurity class limits. The fields of the nominal screen
are unchanged; the bands are added beside them.

**Bounds.** With *n* samples, *k* is the largest index for which the lower end of the two-sided Clopper-Pearson interval
at the confidence level of (*n* &minus; *k* + 1)/*n* is at least the confidence level. A value is reported by its
median and its upper bound, the *k*-th largest per-sample value. A limit is reported by its lower bound, the *k*-th
smallest per-sample limit. These are order-statistic (Wilks) tolerance bounds: exact for any distribution, one-sided,
and they need no normality. **At least 72 samples are needed at confidence 0.95**; a document with fewer is refused,
and the refusal names the minimum for your level (528 at 0.99).

**Reading the result.** Each per-target endpoint entry gains an `uncertainty` object:

- `matrix_only`, `nominal` and `worst_case`, and each impurity's `contribution_nominal`: the `median`, the
  `upper_bound` and its `status` (`bounded`, or `unbounded` when a failed sample or an unavailable proxy reaches the
  rank), with the value of every sample in `by_sample`.
- With a `limit`, each impurity's `limits.alone`, `limits.others_at_nominal` and `limits.others_at_worst`, and
  `joint_k`: the `bound`, the `status` and the per-sample limits (`by_sample`, `sorted`). A sample whose limit lies
  beyond the balance element's reach enters at that cap (`capped`); a sample that fails or is infeasible enters at 0, so
  it can only lower the bound. The limit is `not_established` when any sample (or the nominal data) has a positive
  lower bound, and `unbounded` when the *k*-th smallest has no response.
- For `waste_class`, the `frequency` of each class at nominal and over the ranges, and `meets_target`: the probability
  that the target class is met at nominal and at the worst case, with its Clopper-Pearson `lower` bound at the
  confidence level. Each impurity's class limit, over all targets, is under `uncertainty.impurity_class_limits`.
- `ranking`: for each impurity the fraction of samples in which it is the largest contributor at nominal.

The top-level `uncertainty` block gives the `confidence_level`, the `order_statistic` (`k`, `n` and
`minimum_samples`), the `sampling` it ran, the plan composition, the `coverage` of the sampled channels, the
`failed_samples`, `not_claimed` and `verification_pass`. With `keep_samples`, each sample writes `sample_NNNN.json`: its
factor block, the factors each element solve actually carried, the element responses used and the sample's screen
values, enough to recompute every band.

**Verification.** Unless `--no-verify` is given, the nominal checks above still run. For every established limit, the
samples at sorted ranks *k* &minus; 2 to *k* + 2 are re-solved with the full alloy and that sample's own factors at 0.99
and 1.01 times their own limit: the 0.99 point must meet the limit and the 1.01 point must not, unless the limit is
capped. For every upper bound, the sample that holds it is re-solved at the composition the value belongs to (its own
worst-case corner for `worst_case`) and must agree to 1e-6 relative. Differences smaller than 1e-12 times the
endpoint's largest value in that sample are ignored. That is round-off, such as a trace nuclide that one solve keeps
at 1e-162 Bq/g and another sets to zero. A point decided this way records `rel_dev_before_floor` and `floor`. The
results are in `verification.uncertainty`, and a miss exits 3 after writing.

The worked example's 72 samples take about 45 minutes on one laptop core: 15 element solves per sample, 1,080 in all, plus
356 re-solves that verify the bounds. Its heat at step 2 (1 d of cooling) shows what the bounds add. With the
nominal data, the worst case, 1.38e-2 W/g, is just under the 1.4e-2 W/g limit. Over the nuclear-data uncertainty, the
steel exceeds that limit in 30 of the 72 samples even without its impurities. The 95% upper bound on the worst case is
2.5e-2 W/g, and the cobalt limit's lower bound is 0: no cobalt share meets the limit at 95% confidence.

```sh
actinv impurities examples/impurities_ra_steel/impurities_uncertainty.json impurities-uncertainty-result.json
```

Output, the end of `uncertainty.worst_case` in the `heat.total` entry at step 2, after its 72 per-sample values:

```text
              ],
              "median": 0.013621176480236313,
              "order_statistic_rank": 1,
              "status": "bounded",
              "upper_bound": 0.025141681784584535,
              "upper_bound_sample": 68,
              "valid_samples": 72
            }
```

**What the bands do not claim.** They are conditional on the sampled channels and assume, per sample, that the
activation response is linear in composition, as the nominal screen does. Composition, geometry, flux-shape and model
uncertainty are not sampled. Measured coverage of these channels on the FNS corpus is below nominal
(`docs/DATA_LIMITATIONS.md`), so a bound can be optimistic. The upper bound on a value and the lower bound on a limit
are one-sided order-statistic tolerance bounds. They are not two-sided intervals and not a design margin. The worked
example's composition and history are illustrative.

## In the desktop app and the browser

The **Impurities** page, under **STUDIES**, screens the material of the problem that is open. **Composition** lists
its elements: choose the **Balance** (the largest at first), keep an element **Fixed** at its share from **Material**,
or make it an **Impurity** with a nominal, minimum and maximum in wt%. Add an impurity the material does not list by
its symbol. **When** selects the steps to screen; every cooling step after the last irradiation is selected at first.
**What matters** chooses the endpoints, each with an optional limit, the activity of single nuclides, and the waste
class with the class to meet; the waste class uses the material's mass and density as the component. The page needs
a composition in wt%, and it explains on the page what it refuses, as the command does.

**Screen impurities** runs the screen in the background, with verification, and shows the result: whether it was
verified, an overview with each endpoint's worst case, largest contributor and tightest limit over all screened
steps, and for a chosen step, each endpoint's nominal, impurity-free and worst-case values with every impurity's
contribution, the nuclides it acts through and its limits. **Save screen…** writes the `actinv-impurities-1`
document with the problem embedded, and **Save result…** and **Open result…** write and read results, including
those of `actinv impurities`. The browser sets up screens and reads results but does not run them.

## What to keep in mind

- **Linearity.** At fixed flux the coupled activation is linear in the initial composition. That holds without
  self-shielding, screening or feed; it is a property of the tested neutron spectra and schedules, not a general
  one. Ranges that move the composition far from the tested mixtures are not covered by that evidence.
- **Contact dose is a slab proxy.** `dose.contact_proxy` is the semi-infinite-slab screening proxy, not a dose for a
  geometry. It is not linear, because the material's own attenuation enters it: the command sums the pure elements'
  grouped photon sources and evaluates the proxy at the composition's attenuation exactly as a run would. Its worst
  case is a **corner maximum** over the corners of the range box (at most 12 impurities with a range); an interior
  maximum is not excluded. Its limits are found by bisection to 1e-9 relative in the impurity's weight fraction and
  assume the proxy rises with the impurity. Its gradient is a central difference. A proxy the response cannot
  evaluate is reported as `null` with the reason, never as a number.
- **Every number is verified.** Unless `--no-verify` is given, the nominal composition, every worst-case corner and
  every reported limit is solved in full and compared with the prediction (`verification.points`, tolerance 1e-6
  relative). The command exits 3, after writing the result, if any point misses.
- **The Part 61 class** is the nominal U.S. classification, not disposal acceptance.
