# ACTINV v1.6.0 — release notes

ACTINV 1.6.0 extends the waste commands and replaces the rate-pruning rule. The waste commands gain classification
and impurity budgets under nuclear-data uncertainty, classes for packages of discrete items under the NRC
concentration-averaging and encapsulation position, a Python API and a desktop page. Rate pruning now drops a nuclide
only when a proven bound on its population over the whole schedule is below the threshold, and the run ledger reports
bounds on the change that pruning can make to total activity and heat. Every addition landed under a registered
protocol whose verdict is recorded in `results/` and in the ledger. The waste additions are new commands and leave
existing results unchanged; the pruning change is listed under "Results to re-check".

Upgrade the Python package with:

```bash
python -m pip install --upgrade actinv
```

Rust CLI users can install this exact version with:

```bash
cargo install --locked --force actinv-cli --version 1.6.0
```

## Results to re-check

One change affects default results: the rate-pruning rule (P148).

- **Which nuclides are pruned.** In the default `rate` mode a nuclide is dropped only when its proven population bound
  over the whole schedule is below `bmin_atoms_per_g` (default 1e-8). The previous rule took the largest of converging
  paths instead of their sum and could under-estimate a population, so it was an estimate, not a bound. A few
  noise-level nuclides are now kept or dropped differently; the CI end-to-end case keeps 38 states instead of 40, and
  its heat moves by at most 1.3e-23 W/g.
- **Measured effect.** Over the 132 FNS decay-heat experiments at the default threshold, the median bound on the heat
  change is 4.9e-12 of the heat. Nineteen experiments change heat by more than 1e-6, at most 1.38 % (Cl) and 1.31 %
  (I), and total activity by up to 48 % (I) and 33 % (Cl). These changes occur only at the end-of-irradiation step and
  come from Be-8 made by C-12(n,n'α) in the carbon of the iodine and chlorine compounds: Be-8 lives about 1e-16 s, so
  its population never reaches the threshold while its activity equals its production rate. Nothing differs after
  the first cooling step. Every change is inside the run's own bound. One-year steady and pulsed iron histories change
  by at most 2.3e-14.
- **Ledger fields.** `ledger.rate_pruning` replaces `removed_heat_W_per_g_bound` (which covered dropped states only)
  with `dropped_activity_Bq_per_g_bound`, `dropped_heat_W_per_g_bound`, `kept_activity_Bq_per_g_bound`,
  `kept_heat_W_per_g_bound`, their sums `activity_Bq_per_g_bound` and `heat_W_per_g_bound`, and
  `unbounded_components`; each dropped nuclide carries `atoms_per_g_bound` and `feed_atoms_per_g_s_bound`. Anything
  that reads the old field, compares whole ledgers, or hashes a normalized result will see the new block. The
  `options.screen` certificate widens `heat.*`, `activity.total` and `activity:<nuclide>` by these bounds.

## What changes

### Rate pruning with a proven bound (P148)

For each nuclide the solver bounds the atoms that can ever pass through it (its throughput) from its initial
amount and the throughput of its parents, summed over every parent and limited by each edge's share of the parent's
loss, then bounds its population by the smaller of that throughput and its initial amount plus its largest inflow
divided by its decay constant. Cycles are bounded by a certified contraction or kept whole and listed in
`unbounded_components`. The bound covers the linear problem ACTINV assembles: the stated decay and reaction data,
the schedule's largest flux multiplier for every step, and the estimated flux of a power-driven step. It does not
cover nuclear-data, flux-shape or CRAM error. The derivation is in the [method](guide/method.md) and
[`docs/METHOD.md`](METHOD.md). The bound can be loose: in the FNS experiments it exceeded the actual change by up to
816 times for heat and 2.3e3 times for activity.

### Waste classification under nuclear-data uncertainty (P145)

`actinv waste uncertainty SPEC [OUT]` (and `actinv.waste.uncertainty`) re-solves the problem for each correlated draw
of the `study.robustness` channels (MF=33 cross sections, decay constants, fission yields, flux, composition) and
classifies every sample with the nominal evaluator. The result gives class frequencies with Clopper-Pearson
intervals, the class established at a confidence level (at least 72 samples at 0.95), nearest-rank percentiles of
each table sum, and the coverage of the sampled channels.

### Impurity budgets under nuclear-data uncertainty (P146)

`actinv waste budget uncertainty SPEC [OUT]` (and `actinv.waste.budget_uncertainty`) draws the robustness channels
once per sample and applies the same factors to every element solve of a class budget. The budget at a confidence
level is the order-statistic bound of the per-sample suprema of each impurity and of the joint scale factor, with
binding-constraint tallies, a verification re-solve and channel coverage.

### Packages of discrete items (P147)

`actinv waste package SPEC [OUT]` (and `actinv.waste.package`) classifies one container of activated metal,
contaminated material, design-radioactivity components, sealed sources and cartridge filters under the NRC
concentration-averaging and encapsulation BTP (Revision 1, 2015): BTP Tables 2 and 3, the Factors of 2 and 10,
removal and separate classification of failing items, merging of sectioned components, the optional 37 MBq and
highest-item screens, and encapsulation credit under the loading, volume, mass and Class C rules. The BTP numbers are
bundled as `waste_us_nrc_btp_2015_v1.json`, and the result records the readings it relies on.

### Waste in the Python API (P143)

`actinv.waste.classify`, `budget`, `bounds`, `composition`, `composition_solve` and `intrusion_screen`, and
`actinv.twin`, take a spec path or a dictionary and return the result the CLI writes; `actinv.waste.summary`
flattens a classification to one row per component and step. Refusals raise `ValueError`.

## Desktop and browser

Desktop `0.5.0-preview.1` adds a **Waste class** page (P144) on the 1.6.0 solver: it builds an `actinv-waste-spec-1`
document from the loaded result, classifies it with the CLI's own evaluator, and shows each step's class, governing
table, sum, margin and binding nuclides. The browser workbench opens and edits waste specs and shows waste, intrusion
and twin results; it never classifies. The look is unchanged. See the
[desktop 0.5.0-preview.1 release](releases/desktop-v0.5.0-preview.1.md) and [Versions and releases](guide/releases.md).

## Known limitations

All limits in the [qualification boundary](guide/qualification.md) and
[data limitations](DATA_LIMITATIONS.md) still apply. In particular:

- The pruning bound covers the assembled linear problem only, and it can be much larger than the actual change. At
  the default threshold, short-lived nuclides with a large production rate (Be-8 above) can carry a large share of
  the end-of-irradiation activity while their populations stay below the threshold; use `prune: "reach"` or a lower
  `bmin_atoms_per_g` where end-of-irradiation activity matters.
- Class probabilities and budgets under uncertainty are conditional on the sampled channels; rows without covariance
  data are not sampled, and their share is reported as coverage. Budgets under uncertainty are upper bounds only.
- `actinv waste package` does not cover blending, the BTP's alternative approaches, 10 CFR 61.58 or site licence
  conditions.
- Two waste scopes are not in this release: classification from measurement-supported scaling factors, and rule
  packs for jurisdictions other than U.S. 10 CFR 61.55. Both are planned for a later release.
- The P148 protocol closed FAIL on its CI gate because the evidence commit regenerated two records that older
  controls keep frozen; P150 qualified the same solver on CI, with P148's evidence as its scientific basis.

Each verdict cited above records its exact scope; the [changelog](../CHANGELOG.md) has the full list of changes.
