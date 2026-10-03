# ACTINV P103 — nominal component waste classes and verified class budgets

Opened 2026-10-03 at the owner's request to prioritize the waste extension. This
changes the unopened scheduling order only. P75b coupled/reach linearity and
P79/P79a verified elemental solves are its foundations; previous failure verdicts
and uncertainty qualification limits remain unchanged. No draft intrusion screen,
uncertainty-aware class, mixed-package averaging, transport or site-acceptance claim.

## Frozen inputs and source decisions

The selected pack is `data/waste_us_nrc_61_55_v1.json`, mirrored inside the core
crate. Values were checked against current eCFR 10 CFR 61.55 (as of 2026-10-01)
and official 2025 CFR XML, whose URL/hash are in the pack. The source-review note
records single-component volume interpretation. Rule values are data; predicates
are code. Users must explicitly select this pack by id or a supplied pack path.

- 1 Ci = 3.7e10 Bq, 1 nCi = 37 Bq, 1 m3 = 1e6 cm3. A year for category selection
  is 365.25 days. Less/greater than five years are strict; exactly five is neither.
- Waste type is `general` or `activated_metal`. Metal variants replace their
  general rows; Ni-59/Nb-94 metal-only rows do not apply to general waste.
- Table 1 alpha-transuranic category: Z > 92, alpha-emitting, half-life > 5 y.
  A dedicated nuclide row takes precedence over a generic category within the
  same table (notably Pu-241); never add its activity twice in that table.
  Use total nuclide activity, with explicit half-life/Z/alpha metadata. Missing
  metadata on any active nuclide yields an unknown class, never assumed coverage.
- Aggregate Table 2 activity includes nuclides with half-life < 5 y without a
  dedicated Table 2 row, including Cm-242 even though it has a Table 1 row.
  Cm-242 alone therefore evaluates both tables. This literal conservative
  cross-table reading is reported; named Table 2 rows have same-table precedence.
  Separate tables are never summed together. Omitted B/C limits contribute zero
  to those column sums; footnote 1 still makes an A-exceeding waste at least B.
- One contributing nuclide within a table/column uses inclusive boundaries from
  (a)(3)/(a)(4); more than one uses strict sum-of-fractions boundaries from (a)(7).
  Table 1's A threshold is 0.1; B requires the same Table 1 threshold; C allows 1.
  Table 2 uses columns 1/2/3. Above either C constraint is above-Class-C.
- No applicable table activity gives A under (a)(6), provided coverage is complete.
- Component mass and displaced volume are finite and positive. Volume is supplied
  explicitly as envelope minus major voids, or mass/density with a declared
  conservative solid-volume basis. Cell-specific Bq/g is multiplied by cell mass
  before aggregation; cell masses must close to component mass to 1e-9 relative.
  Missing cells, repeated ids, overlapping component membership, absent selected
  steps and unequal time stamps are errors. Mesh result/footer schemas are checked.
- External H-3 is declared at each selected step as additional activity in Bq,
  with a source and an attestation that it excludes calculated activation H-3.
  `not_applicable` is an explicit declaration. Required but missing H-3 gives
  unknown, with the calculated-only class reported separately. No implicit zero.

This phase uses a declared single component under (a)(8); it does not qualify
full CA BTP conformity. Direct inspection of ML12254B065 for package/piece rules
remains a separate follow-up source task. General waste also uses a declared
single material volume without package dilution, not an inferred waste-form rule.

## Product contract

`actinv waste WASTE.json [OUT.json]` consumes `actinv-waste-spec-1` with selected
`rules`, `input` (run JSON or mesh NDJSON), `targets` (one-based result steps),
`nuclide_properties` map (Z, half_life_s, alpha_emitting), and `components`.
Components name `id`, `mass_g`, `displaced_volume_cm3` (or `density_g_cm3`),
`waste_type`, and cells `{id,mass_g}` for mesh; a single-run component has no
cells and uses its declared mass. `external_tritium` declares `status` as
`not_applicable`, `declared`, or `required`; declared entries map step numbers
to additional `activity_bq` and include `source`/`excludes_activation:true`.
Output `actinv-waste-result-1` records normalized inventories, units, all row
fractions, column sums, binding constraints, nominal class/coverage and margins.

`actinv waste budget BUDGET.json [OUT.json]` consumes `actinv-waste-budget-1`:
P79's base_spec/balance/matrix/impurities/targets plus `rules`, `target_class`
(A/B/C), `mass_g`, `displaced_volume_cm3` (or density), `waste_type`, properties
and external tritium. One shared core evaluator handles classification and
budget verification. Force coupled/reach and refuse all P79 nonlinear inputs.
Elemental basis solves emit inventory evidence. Constraints remain separate.
Report sole-impurity and others-at-spec limits, joint specification margin,
binding table/column/step, infeasible/no-response/composition-bound status.
Balance remains positive. For open mixture constraints, report a supremum and
verify an interior point at a relative inset of 5e-7; the supremum is not labelled
an attained maximum. Every emitted usable point is re-solved using the full
composition at all targets; no bypass flag. Missing category/H-3 coverage refuses
unconditional budgets. Every verification inventory and predicted/solved sum
is emitted; numerical agreement <= 1e-6 relative (1e-12 absolute near zero).

For target class A, intersect S1/0.1 <= 1 and S2_col1 <= 1; for B,
S1/0.1 <= 1 and S2_col2 <= 1; for C, S1 <= 1 and S2_col3 <= 1.
These constrain membership at or below the requested class. Absent applicable
rows give zero constraints, not missing limits. Each sum is linear in wt% with
fixed geometry and external H-3. Let x be an impurity wt% (or joint scale k):
each constraint is b + g*x <= 1. For g > 0 the upper edge is (1-b)/g;
for g < 0 it is a lower edge; g = 0 with b > 1 is infeasible. Intersect all
edges across targets with nonnegative impurities and positive balance. An
otherwise-feasible zero-width open interval is infeasible. No positive response
is distinguished from a composition-bound upper edge. Report both interval
ends; use an inward point when an edge is open. Re-evaluation of the actual
inventory decides strictness by its contributing nuclide count, so no epsilon
changes the classification predicates. The conservative 5e-7 relative inset is
used on computed upper regulatory edges (even if the single-nuclide edge is
closed); retain the mathematical supremum and endpoint-attainment separately.

## Gates (smallest inputs)

G0: source/pack check and seal before production implementation. The checker
compares every table row, unit, null limit, mirror bytes and protocol/pack hashes.
G1: regulation's Sr-90 50 + Cs-137 22 Ci/m3 example gives B and sum 5/6.
Independent synthetic vectors cover both tables, exact and adjacent boundaries,
aggregate and alpha categories, general/metal variants, mixed units, no covered
rows, unknown metadata/H-3 and unequal cell masses. Planted invalid input fails.
G2: one tiny synthetic decay/activation library written only under disk-backed
target. Use Fe balance and Nb impurity; Fe-56 inert, Nb-93(n,gamma)Nb-94 at
1 barn, one group, 1e12 n/cm2/s, 1e6 s irradiation then 1e6 s cooling. Synthetic
Nb-94 half-life is 1e11 s and all other states stable (these are artificial
control inputs, not evaluated nuclide properties). Targets are steps 1 and 2;
component mass 1 g, displaced volume 1/7.8 cm3, activated_metal, external H-3
not_applicable, Nb specification 0.01 wt%, target class C. The
checker independently recomputes constraints from emitted full-solve inventories,
checks every usable limit and joint point at every target, perturbing the binding
edge where the composition permits it. Analytic algebra vectors cover negative
gradients, matrix infeasibility, no-response and composition bounds.
G3: deterministic end-to-end waste CLI on the small component and its budget;
independent arithmetic rejects altered class/sum/limit/verification inventories.
Required fmt/check/clippy/test workspace gates and CLI test compilation pass.
Add the independent data-free checker to CI. Checker verdict P103-PASS requires
all gates; otherwise preserve the failure. One repair round per standing rules.

No input requires a large nuclear-data fetch. All jobs use the enforced 6G/0-swap,
128-task, 200% CPU systemd scope and disk TMPDIR; coordinator executes serially.
Controls launching the CLI use bounded subprocess timeouts and reap children.
Closure records a session, append-only ledger entry, results and indexed manifest.
