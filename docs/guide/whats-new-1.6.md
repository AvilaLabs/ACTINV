# What's new in 1.6

ACTINV 1.6.0 extends the waste commands and gives rate pruning a proven bound. This page is a tour by task. [Versions and releases](releases.md) has the version table, and the [1.6.0 release notes](https://github.com/AvilaLabs/ACTINV/blob/master/docs/RELEASE_NOTES_v1.6.0.md) have the full list.

The waste additions are new commands; a problem that does not use them computes what it computed before, except for the pruning change collected in [Results that may change](#results-that-may-change). Classification from measurement-supported scaling factors and rule packs for jurisdictions other than U.S. 10 CFR 61.55 are not in this release. The nuclear-data catalog stays at 1.1.0. The desktop is 0.5.0-preview.1, and the [desktop release page](https://github.com/AvilaLabs/ACTINV/blob/master/docs/releases/desktop-v0.5.0-preview.1.md) describes it.

## Classify waste under nuclear-data uncertainty

`actinv waste uncertainty SPEC [OUT]` answers "how sure is this class?". It re-solves the problem for each correlated draw of the spec's `study.robustness` channels (MF=33 cross sections, decay constants, fission yields, flux, composition) and classifies every sample with the same evaluator as `actinv waste`. The result gives the frequency of each class with Clopper-Pearson intervals, the class established at a confidence level (at least 72 samples at 0.95), percentiles of each table sum, and how much of the response the sampled channels cover. See [Classification under nuclear-data uncertainty](waste.md#classification-under-nuclear-data-uncertainty).

## Set impurity budgets under uncertainty

`actinv waste budget uncertainty SPEC [OUT]` turns a class impurity budget into one that holds at a confidence level. Each sample applies the same data factors to every element solve, and the budget is the order-statistic bound of the per-sample suprema, with the constraints that bind and a verification re-solve. Budgets are upper bounds only. See [Budgets under nuclear-data uncertainty](waste.md#budgets-under-nuclear-data-uncertainty).

## Classify a package of discrete items

`actinv waste package SPEC [OUT]` classifies one container of activated metal, contaminated material, components, sealed sources and cartridge filters under the NRC concentration-averaging and encapsulation BTP (Revision 1, 2015). It applies the BTP's item tables and the Factors of 2 and 10, classifies failing items alone, merges sectioned components, and credits encapsulation under the BTP's loading, volume, mass and Class C rules. The result records the readings of the BTP it relies on. See [Classify a package of discrete items](waste.md#classify-a-package-of-discrete-items).

## Use the waste commands from Python

`actinv.waste` exposes `classify`, `budget`, `bounds`, `composition`, `composition_solve`, `intrusion_screen`, `uncertainty`, `budget_uncertainty` and `package`, and `actinv.twin` runs a facility twin. Each takes a spec path or a dictionary and returns what the CLI writes; `actinv.waste.summary` gives one row per component and step. See [Waste classification from Python](python.md#waste-classification-from-python).

## Classify a result in the desktop

The desktop's **Waste class** page, in the EXPLORE group, builds a waste spec from the loaded result, fills the nuclide properties from the run's decay data, classifies it with the CLI's own evaluator, and shows each step's class, governing table, sum, margin and binding nuclides. The browser workbench opens and edits waste specs and shows waste results but does not classify. See [Classify a result as waste](desktop.md#classify-a-result-as-waste).

## Results that may change

Rate pruning now drops a nuclide only when a proven bound on its population over the whole schedule is below `bmin_atoms_per_g`; the previous rule could under-estimate a population. The [numerical method](method.md#time-integration) describes the bound.

- A few noise-level nuclides are kept or dropped differently. Over the 132 FNS decay-heat experiments at the default threshold, nineteen change heat by more than 1e-6 (at most 1.4 %) and total activity by up to 48 %, only at the end-of-irradiation step. The cause is Be-8 from C-12(n,n'α) in carbon-rich samples: it lives about 1e-16 s, so its population stays below the threshold while its activity equals its production rate. Nothing differs after the first cooling step, and every change is inside the run's bound. Use `prune: "reach"` or a lower threshold where end-of-irradiation activity matters.
- The ledger's `rate_pruning` block replaces `removed_heat_W_per_g_bound` with bounds on dropped and kept activity and heat, their sums `activity_Bq_per_g_bound` and `heat_W_per_g_bound`, and `unbounded_components`. Anything that reads the old field, compares ledgers or hashes a normalized result will see the change. See [the ledger](https://github.com/AvilaLabs/ACTINV/blob/master/docs/LEDGER.md).
