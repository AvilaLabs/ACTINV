# P140 H1 review: summary, depletion, loss-rate recording, report consumer

Reviewer: not the implementer. Read-only review; nothing was built or run. Every prediction below comes from reading
the code at HEAD (233e3e8). Line numbers are for that tree.
Scope read: crates/actinv-core/src/summary.rs, depletion.rs, the LossRate changes in chain.rs, the wiring in run.rs
(3805-3830, 3880-3945, 4165-4230, 4460-4620, 4860-5230, 5340-5380, 5560-5700, 5910-5980, 6940-6960, 7550-7565), spec.rs
(SummaryOptions and validation), mesh.rs (STEP_FIELDS, maximal_step_out, field pruning), crates/actinv-cli/src/report.rs,
docs/guide/results.md and specification.md, protocols P122, P135 (+A), P137, P140, and controls/check_p135.py.

Counts: defect 3, robustness 3, documentation 3 (9 findings). Also 1 low-confidence note.

---------------------------------------------------------------------------------------------------------------

## F1  [defect, HIGH confidence] Scheduled `removal` is a diagonal term the depletion table does not know about

File: crates/actinv-core/src/depletion.rs:6-11 (module claim), :64-65 (`removal_rate_per_s`);
run.rs:4380-4423 (removal edges built as `(sink,target,+r)` and `(target,target,-r)`), run.rs:5351-5380 (tracker
inputs: only `lambda_eff` and recorded loss rows).
docs/guide/results.md:138-139: "The channels therefore sum to `removal_rate_per_s`, the diagonal of the step matrix".

Description: A schedule step's `removal` map (first-order, s^-1, nuclide or element selector) adds `-rate` to the
diagonal of the target state. The depletion table builds `removal_rate_per_s` and the channel list only from
`lambda_eff[column]` plus the recorded library loss rows. So with `removal` declared on an initial-material nuclide in
coupled mode, the end atoms (read from the evolved state) fall by exp(-removal*dt) but no channel, no share and no part
of `removal_rate_per_s` accounts for it. `depleted_fraction` is large while `removal_rate_per_s` is tiny and the shares
sum to 1 over channels that explain none of the loss. The documented "sum to the diagonal" statement is false for such
steps. The docs only say "net of ... a partition or removal applied at the step start", which describes an
instantaneous action; `removal` is continuous. A `feed` into an initial nuclide likewise shows up as negative
depletion with no channel (the docs mention only ingrowth from other nuclides).
In trace mode the analytic model ignores `removal` and `feed` entirely, so a fed, removable reservoir nuclide (the
case spec docs line ~996 describes as "carried in a real state and removable") is shown as undepleted by removal.

Reproduction (spec fragment merged into a coupled FNS Fe-type spec):
```json
"options": {"mode": "coupled"},
"summary": {"depletion": true},
"schedule": [{"duration": "1000 s", "flux": 0, "removal": {"Fe56": 1e-3}}]
```
Expected (by the documented contract): a channel (or a documented separate entry) carrying 1e-3 s^-1 for Fe56 and a
`removal_rate_per_s` equal to the matrix diagonal, 1e-3 (flux 0, Fe56 stable). Predicted actual: `channels == []`,
`removal_rate_per_s == 0`, `atoms_end_per_g = start*exp(-1) = 0.368*start`, `depleted_fraction = 0.632`.
Unit-test sketch: DepletionTracker tests cannot see this; add a `depletion_prepared_tests` case with the above and assert
`sum(channel.rate) == -diag`.

Fix: pass each tracked column's declared removal rate for the step (sum of `step_removal` diagonal entries) into
`DepletionTracker::step` as a third channel kind (`"removal"`, no MT), include it in `total`, shares and the analytic
exponent; or state in the docs and in `removal_rate_per_s`'s doc comment that declared removal and feed are outside the
table and refuse or flag (ledger note) when a step declares them with depletion on.

---------------------------------------------------------------------------------------------------------------

## F2  [defect, MEDIUM-HIGH confidence] Trace mode + uncertainty + depletion: reaction channels of reservoir nuclides get no uncertainty

File: run.rs:4560-4575 (`derivative_remap`), run.rs:4872-4882 (`active_rows` taken from `derivative_subs`),
run.rs:5213-5226 (`LossCovariance::from_collapsed` over that collapse).
docs/guide/specification.md:777-780 ("It works with trace and coupled mode ... With an uncertainty section ... each
reaction channel also carries the relative standard uncertainty").

Description: The depletion covariance is taken from the same `collapsed` object the bands use, and that collapse covers
only `active_rows`, the library rows that have a derivative in `derivative_subs`. In trace mode `derivative_remap`
drops every derivative with `derivative.row == derivative.column` for a bulk (reservoir) column that is not in
`tracked_reservoir` (the `return` at ~4560), because the trace formulation does not deplete the reservoir. A loss row's
derivative is exactly that diagonal entry (chain.rs: `row: col, column: col`). So the loss rows of all untracked
reservoir nuclides (Fe56 and the other initial nuclides, the thing the depletion table is about) are never in
`active_rows`, hence never in `collapsed.row_indices`, hence absent from `LossCovariance.index`, and
`LossCovariance::variance` returns None for every one of their reaction channels.
Result: `relative_standard_uncertainty` is missing on every reaction channel of reservoir nuclides,
`removal_rate_relative_standard_uncertainty` is None, `uncertainty_covered_share` is only the decay share (0 for a
stable nuclide), while ledger.depletion.uncertainty still says "mf33". P135 G5 (check_p135.py `gate_g5`, ~line 833) only
ran `mode="coupled"`, so this path has no evidence. (Trace mode is what the shipped FNS Fe example selects with
`mode: auto`.)

Reproduction: `examples/fns_fe_5min.json` unchanged (auto -> trace) plus
`"summary": {"depletion": true}` and an `uncertainty` block with the TENDL covariance sidecar (as in
check_p135 G5). Expected: Fe56 MT102 etc. carry `relative_standard_uncertainty`, as in the coupled G5 run with the same
data. Predicted actual: none of the Fe-nuclide reaction channels carry it, `uncertainty_covered_share == 0.0`.

Fix: collapse the depletion loss rows with a dedicated `collapse_weighted_multi(dense_library, &phis, &loss_rows,
&row_scale)` call (rows of tracked columns with `zap == -1`), independent of `active_rows`, so the bands, parameter
counts and `n_covered_rows` are not perturbed; apply the ADCROSS rescale to it as well. Add a trace-mode case to G5.

---------------------------------------------------------------------------------------------------------------

## F3  [defect, MEDIUM confidence] Excluded (zeroed) covariance blocks are reported as "uncertainty 0", not "not covered"

File: depletion.rs:114-148 (`from_collapsed` ignores `collapsed.excluded_blocks`), :154-172 (`variance`);
covariance.rs:1308-1330 (a non-PSD or asymmetric self block is zeroed over the whole principal submatrix);
run.rs:4984-4992, 5048 (the band code marks such parameters `covariance_excluded`).

Description: When a (target, MT) self block is excluded the collapse zeroes its covariance entries and flags it; the
band path reports the parameter as excluded. The depletion path does not look at `excluded_blocks`, so
`variance()` returns `Some(0.0)` (the row is in `index`, sigma > 0) and the channel gets
`relative_standard_uncertainty: Some(0.0)`, counts as "covered" in `uncertainty_covered_share`, and lets the joint
`removal_rate_relative_standard_uncertainty` be emitted as if the channel had zero uncertainty (understating it).
A zeroed cross block is silently treated as uncorrelated; that part is the same as the bands. The P135 checker even says
"the candidate would have excluded the block" for the negative self-variance case, but the candidate code has no such
rule.

Reproduction (unit-test sketch in depletion.rs tests, using the existing helpers): build a `synthetic_library`
covariance with `relative_component(102, 102, -0.04)` (negative variance makes the 1x1 block non-PSD) or
two inconsistent asymmetric entries, run `collapse`, track MT102 with a loss row, call `step`. Expected: no
`relative_standard_uncertainty` on MT102 and `uncertainty_covered_share` excluding it. Predicted actual:
`relative_standard_uncertainty == Some(0.0)`, covered share includes it.

Fix: keep a set of excluded (library target, MT) self blocks in `LossCovariance` (from
`collapsed.excluded_blocks` with `mt == mt1`, mapped through the library row's target/mt) and make `variance` return
None for rows in it.

---------------------------------------------------------------------------------------------------------------

## F4  [robustness, HIGH confidence] Trace-analytic depletion fractions lose all precision exactly in the regime where trace is chosen

File: depletion.rs:327-350 (`end = start * exp(-total*dt)`, `depleted = start - end`, `fraction = depleted/start`,
`cumulative = (initial - end)/initial`).

Description: Trace mode is selected only when burnup is below about 1e-6, so R*dt is typically 1e-12 to 1e-6. Computing
the depleted amount as `start - start*exp(-x)` has absolute error ~1.1e-16*start, so `depleted_fraction` has relative
error ~1e-16/x: 1e-4 at x = 1e-12, and exactly 0.0 (or one quantum 1.1e-16) for x < ~1e-16, while
`removal_rate_per_s > 0`. The cumulative fields are the same. The checker already works around it (check_p135.py G4
"resolution_limited", accepts 6e-16 absolute below 1e-6), so the loss is known but the table is what users read.
Reproduction (test sketch): `DepletionTracker::new(true, true, tracked(0.0,None), [[loss(0,102,1e-20)]], None)`;
`step(0, 1.0, 1.0, ..)`. Expected `depleted_fraction = 1e-20` (and `depleted_atoms_per_g = 1e-20*start`); predicted
actual `0.0` (end == start in f64). With rate 3e-12 expected 3e-12; actual is a multiple of 1.11e-16/start-rounded value,
about 4e-5 relative error.

Fix: in the analytic branch carry the exponent: `depleted = start * -f64::exp_m1(-total*dt)`,
`end = start - depleted`, and accumulate the cumulative exponent (or use `-expm1(-sum R dt)`) for the cumulative fraction.
Coupled mode cannot avoid the subtraction (the state is what it is), but could note the resolution in the docs.

---------------------------------------------------------------------------------------------------------------

## F5  [robustness, MEDIUM confidence] NOSTABLE does not remove stable states that have no decay record

File: summary.rs:56-58, 507 (`stable = record.is_some() && lambda == 0.0`; filter `options.include_stable || !row.stable`);
docs/guide/specification.md:~768 ("`false` is FISPACT NOSTABLE: omit stable nuclides").

Description: States the run carries without a decay record (H1 and other light stand-ins, bulk nuclides absent from the
chain, `decay_data: false`) are not flagged `stable`, so `include_stable: false` keeps them in the table, including the
usually huge H1/bulk rows that NOSTABLE exists to hide. This is documented in the struct comment and results.md, but the
spec docs for `include_stable` do not say it, and a user who sets NOSTABLE on a hydrocarbon or on a material whose
isotopes are missing from the decay library will see non-radioactive rows. The report's TAB1 shows them (grams, atoms).
Reproduction: material `{"H": 100}` (or any composition whose isotope has no decay record) with
`summary: {"include_stable": false}`; summary.nuclides still lists H1 with `decay_data: false, stable: false`.
Fix: either also drop `!decay_data && activity == 0` rows under NOSTABLE (it cannot be radioactive as far as the run
knows) or add the sentence to the `include_stable` row of the spec table.

---------------------------------------------------------------------------------------------------------------

## F6  [robustness, LOW-MEDIUM confidence] Decay-channel uncertainty ignores `uncertainty.channels`

File: run.rs:5355-5365 (`lambda_relative` from `d_half_life/half_life` or the override), depletion.rs:281-289, 306-318;
compare run.rs:5100-5135 (the band code adds decay parameters only when `channels` contains `decay_constants`).

Description: `uncertainty.channels` defaults to `["cross_section_mf33"]`. The bands then exclude decay-constant
uncertainty, but the depletion table still attaches the half-life uncertainty to the decay channel and folds it into the
joint total. Two outputs of the same run use different uncertainty sets. docs/guide/results.md:143-146 states the
decay behaviour but not that it ignores the channel list. Conversely with `channels: ["decay_constants"]` and a covariance
file the xs channel uncertainties (always propagated, run.rs:1145) also appear. Not wrong data, but inconsistent
with the user's channel selection.
Reproduction: default uncertainty block with covariance on a spec with a radioactive initial nuclide (e.g. Co-60 in the
material): decay channel `relative_standard_uncertainty = d_half_life/half_life` although `decay_constants` is not
requested and the bands carry no decay term.
Fix: only set `lambda_relative` when `channels` contains `decay_constants` (or document the difference and name it in
the ledger).

---------------------------------------------------------------------------------------------------------------

## F7  [documentation, MEDIUM confidence] Depletion docs overstate "one row per initial nuclide" and "carry no rates"

File: docs/guide/results.md:117-118, 123; run.rs:3815-3822, 6953.
Description: `depletion_columns` keeps only initial nuclides with atoms > 0 that are in the decay chain. A nuclide
absent from the decay library has no row at all; results.md says such nuclides "carry no rates and are listed under
ledger...", which reads as if a row exists with empty channels. The ledger names are `ZA_LISO` strings ("26056_0"),
not the `Fe56` style of the table, and are not described. Also results.md:123 "one row per initial nuclide" is not exact
for the same reason.
Fix: reword to "initial nuclides absent from the decay library have no row" and give the ledger name format.

---------------------------------------------------------------------------------------------------------------

## F8  [documentation, MEDIUM confidence] Trace-mode summary and depletion blocks disagree inside one step, and the summary text does not warn

File: summary.rs:2-5; run.rs:5917-5933; results.md:68-73, 112-117.
Description: In trace mode `summary.nuclides` shows each reservoir nuclide at its constant bulk atoms (and therefore
a decaying initial radionuclide at constant activity and heat, and zero-depleted stable nuclides), while
`summary.depletion` of the same step shows `atoms_end_per_g` below `initial_atoms_per_g` for the same nuclide. Both
are by design and results.md explains the depletion side ("held constant in the inventory and is instead depleted
analytically"), but the summary section's statement of the population rule does not say that the summary totals, activity
and heat of an initial radionuclide do not decay in trace mode and that `mass_g` therefore is not a closure test there.
Fix: one sentence in results.md "Step summary" pointing to the depletion table for the analytic view.

---------------------------------------------------------------------------------------------------------------

## F9  [robustness, LOW confidence] `variance.max(0.0)` hides a NaN variance; zero-removal rows report `uncertainty_covered_share: 0`

File: depletion.rs:171, :323.
(a) `Some(variance.max(0.0))`: `f64::max` returns the non-NaN operand, so a NaN variance (NaN in a covariance entry or
rate) becomes `Some(0.0)` and an "uncertainty 0" claim instead of None. The collapse probably validates its inputs, so
low likelihood; use `variance.is_nan().then_some(None)` before the clamp.
(b) For a nuclide with no removal at all (stable, zero flux) under a covariance run the tracker emits
`uncertainty_covered_share: 0.0`. 0/0 is undefined; a stable nuclide in a cooling step reads as "none of the removal is
covered". Prefer omitting the key or `1.0`.

---------------------------------------------------------------------------------------------------------------

## F10  [documentation / feature gap, LOW] `actinv report` does not render the depletion table; TAB2 trims silently

File: crates/actinv-cli/src/report.rs (no reference to `depletion`; tab2 at ~line 500-600).
Description: `steps[].summary.depletion` has no report table (TAB1-TAB4, totals, dominant, elements, timeseries only),
so the FISPACT DEPLETION analogue has no `report` view and the docs for `report` do not say that. In TAB1 a note
reports rows trimmed by `min_atoms`/`include_stable`; TAB2 trims the same rows with no note, so TAB2 totals can differ from
the `totals` table with no explanation. `--nuclides X` naming a nuclide trimmed from `summary.nuclides` passes
`check_nuclides` (it checks `inventory`) and yields an empty table without comment.
Fix: a `depletion` table (nuclide, step, depleted fraction, channels with shares) and a note in TAB2.

---------------------------------------------------------------------------------------------------------------

## Low-confidence notes (no finding)

- summary.rs:223-238 classes a decay by the first digit of RTYP only (documented). A record whose alpha follows a first
  beta (RTYP 1.4) counts as beta-; `alpha_Bq` then undercounts the delayed alpha, whereas chain.rs `rtyp_digits` treats
  every digit. Matches the stated rule; flagging only because the FISPACT-compatible `alpha_Bq` might differ.
- A non-finite `atoms` (inf) would make `percent = inf/inf = NaN`, serialised as null, and `actinv report` would stop with
  "lacks a numeric 'percent'". Needs a pathological state; not reachable from a normal run.
- mesh `cell_result_fields` such as `steps.summary.depletion.nuclides` validate (placeholder holds an empty Vec), but
  array element paths are not selectable; that is the existing mesh limitation.

---------------------------------------------------------------------------------------------------------------

## Checked and found correct

Summary (summary.rs)
- Constants: AMU_G = 1.66053906660e-24 g, neutron mass 1.00866491595 u (composition::NEUTRON_MASS_AMU), 1 Ci = 3.7e10 Bq.
  grams = atoms x AWR x m_n(u) x u(g): AWR is the mass ratio to the neutron, so this is right; A x u fallback is flagged
  with `mass_from_a`.
- `absolute` multiplies per-gram totals by `material.mass_g`, `per_cm3` by `material.density_g_cm3` (g/cm^3 times W/g or
  Bq/g gives per cm^3). Validation (spec.rs:1417-1431) guarantees mass_g > 0, density > 0 and finite, `min_atoms` finite >= 0,
  `dominant_top` in 1..=256, so no zero-mass or zero-density division in these paths. `per_cm3` is absent when no density.
- Activity, alpha/beta/gamma use the same lambda and e_heavy/e_light/e_em as run.rs:985-987 and the scaled-nuclide table
  (decay_scale / half-life overrides are honoured because `nuclides` is the scaled map at run.rs:3638).
- Stable and no-record states: lambda = 0, heat zero, `half_life_s` null, `stable` only when a record exists. `lambda()` also
  honours NST == 1.
- Ex-H3 filter uses (Z=1, A=3, LISO=0), the only H-3 state; applied to activity and heat in the totals and in `scaled`.
  Totals sum every state before any table filter, so MIND/NOSTABLE trim only the nuclide table (tests assert it); `elements`
  and `dominant` also use all rows, as documented.
- Mode partition: positive branchings only, shortfall past BRANCHING_TOLERANCE books to `other`, an excess or a near-unit sum
  is rescaled, no modes -> all `other`; sums to the activity. Consistent with chain.rs:119-133 (which books the shortfall to
  leakage and rescales an excess).
- `dominant_list`: non-positive/NaN values are dropped, sort is value descending then name (total order, deterministic),
  n90/n99 count over the full ranking, exact-90 % boundary handled by the 1e-12 slack, empty ranking returns zeros.
  Row sort uses `total_cmp` on atoms with (Z, A, LISO) tie-break; element rows come from a BTreeMap; so every sum has a fixed
  order and results are reproducible.
- Population rule in run.rs:5917-5965 matches the gas fold and the documented rule; sinks (leak, unit, removed) are excluded
  by `ch.keys`; bulk nuclides outside the chain keep bulk atoms; atoms <= 0 are ignored.
- Serialization: `summary` on StepOut, `depletion` on StepSummary, `per_cm3`, `nuclides`, `elements` and
  `SummaryOptions.depletion` (`is_false`) all use `skip_serializing_if`; `material.density_g_cm3` is skipped when None;
  mesh STEP_FIELDS / STEP_OPTIONAL_FIELDS and `maximal_step_out` include `summary`.

Depletion (depletion.rs, chain.rs, run.rs)
- LossRate is recorded only for the requested columns, only on the `zap == -1` loss branch and only when rate != 0, before
  the step multiplier; so `rate * multiplier` is exactly the matrix diagonal contribution of that row, with shielding,
  `rate_scale`, overrides and batched collapse already applied. No double recording: the base `led` and each extra spectrum
  use their own ledger (`scratch`), `loss_rates` is taken once at run.rs:5370.
- Slot indexing: `step_spectrum_id[si].map_or(0, +1)` matches `slot_rates = [base] + loss_rates_extra` and the covariance
  `param_spectrum` indexing. Power steps use `fl` = the time-weighted (equal sub-interval) mean multiplier, exact for a rate
  that is linear in the multiplier and for the analytic exp of the sum.
- Zero-flux steps (`multiplier > 0.0` gate) leave only decay; no removal at all gives an empty channel list with no division
  (`share` is only computed for existing channels, total > 0); `fraction()` guards base <= 0; rates are filtered > 0.
- Channel ordering (rate desc, then decay before MT, then MT) and tracked ordering (atoms desc, key) are total orders;
  `by_mt` is a BTreeMap, so sums are deterministic. An initial nuclide absent at the end shows end = 0, fraction 1.
- Uncertainty: weights `rate/sigma` are the right derivative for a one-group rate (shield scaling and `rate_scale` cancel),
  non-positive or NaN nominal sigma returns None, a channel with any uncovered row gets no uncertainty, the joint total is
  emitted only when every channel is covered, the decay term is independent, cross-MT terms come from the collapsed full
  matrix, ADCROSS rescaling is applied to `collapsed` before `LossCovariance::from_collapsed` (run.rs:4995-5009 then 5213).
  Matrix indexing `left*total+right` matches the collapse's `row_indices.len()` dimension.
- Ledger/certificate `depletion` entries are written only when requested; `model` agrees with `mode`.

Report (report.rs)
- TAB1/TAB2 column/cell alignment for every combination of summary present/absent and multipliers present/absent;
  absolute values use `summary.totals.absolute.multiplier`; `half_life_s: null` -> empty cell; sort ties are deterministic;
  dominant, elements and totals read the same JSON pointers the Rust structs serialise (`activity_by_mode/*`,
  `absolute/*`, `per_cm3/*`), and per-cm3 columns are not reported as "missing" when no density was given.
