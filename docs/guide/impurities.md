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
`options.screen`, a schedule `feed`, power-driven steps and an `uncertainty` section. It also refuses a `min` above
`nominal` or a `nominal` above `max`, an unknown endpoint, `dose.contact_proxy` without a `photon` section and a
`limit` on `waste_class`.

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
