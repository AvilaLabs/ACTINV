# P140 H1 adversarial review: photon outputs and regulatory tables

Reviewer: independent (did not write the code). Base: HEAD 233e3e8, diff vs 2f3daa4.
Method: read-only. No cargo, no binary, no repo edits. Python (`-I`, PYTHONDONTWRITEBYTECODE) only read repo
data and local source files under ~/nuclear-data; converter outputs went to the session scratchpad, not the repo.
Nothing below was executed against the ACTINV binary; every "predicted" value is derived by reading code and data,
and the coordinator must reproduce before accepting.

Counts: defect 1, robustness 7, documentation 5.

Top 3: F1 (bremsstrahlung uses a branching-weighted per-decay energy as the energy of one electron per decay),
F2 (parent-with-progeny rows leave the progeny permanently "missing", so require_complete is unusable and coverage
is misreported for any Cs-137 / Ru-106 / Ce-144 / ... inventory), F3 ("Unlimited" A1/A2 rows can only be expressed
as "missing", same effect for ordinary activation inventories).

---------------------------------------------------------------------------------------------------------------

## F1  defect  crates/actinv-core/src/photon.rs:327-338 (mean_beta_energy_eV), :430-475 (add_bremsstrahlung)

`mean_beta_energy_eV` returns the STYP=1 spectrum's `average_energy` (ENDF list item ER). `add_bremsstrahlung` then
emits exactly one beta-minus per decay at that energy (certificate text and docs say the same). ER is not the mean
energy of a beta-minus; it is the average energy of that radiation type per decay of the nuclide, i.e. already
multiplied by the beta-minus branching ratio. So for any nuclide whose beta-minus branching is below 1 the model
uses the wrong electron energy and the wrong electron count at the same time.

Evidence from the ENDF/B-VIII.0 decay file (~/nuclear-data/endfb-viii.0-decay/bulk/endf-b-viii-0_decay.dat, MF8/MT457):
- Cu-64 (MAT 622): modes RTYP=2 BR 0.615 (EC/beta+), RTYP=1 BR 0.385. STYP=1 ER = 7.340812e4 eV, STYP=2 ER =
  4.896447e4 eV, STYP=8 ER = 2.056e3 eV; their sum 124428 eV equals the record's light-particle mean E_light
  1.244287e5 eV, which proves ER is per decay of the parent. Mean energy per beta-minus is 73408/0.385 = 190.7 keV.
- K-40 (MAT 332): STYP=1 ER = 4.993307e5 eV, beta-minus BR 0.8914, so 560.2 keV per beta-minus.

Predicted effect (Eq. 66 yield ~ n * E0 * ln(...) - ..., total energy ~ n * a Z E0^2 / 2):
- Cu-64: code uses n = 1, E0 = 73.4 keV; physical n = 0.385, E0 = 190.7 keV. Emitted bremsstrahlung energy is low by
  (73.4^2) / (0.385 * 190.7^2) = 0.385, i.e. 2.6x too small; photon count above ~73 keV is zero in the code but not
  in reality (endpoint region 73-579 keV missing entirely).
- K-40: E0 low by 11 %, electron count high by 12 %; net energy about 0.89x.
- Nuclides at ~100 % beta-minus (the Cl-36 / Sr-90 / Y-90 / K-42 / P-32 use the manual's list targets) are barely
  affected (Cl-36 is 98.1 %), which is why the Cl-36 / FNS Fe checks (ledger 151 G4) could not see it: G4 recomputes
  Eq. 66/67 from the same E0, so it verifies the arithmetic, not the input.

Reproduction (unit-test sketch in photon.rs `mod parity`, using the existing `nuclide()` helper extended with modes):
```
let mut n = nuclide(&[], Some(7.340812e4));            // STYP=1 ER per decay
n.modes = vec![Mode{rtyp:2.0,rfs:0.0,q:0.0,dq:0.0,br:0.615,dbr:0.0},
               Mode{rtyp:1.0,rfs:0.0,q:0.0,dq:0.0,br:0.385,dbr:0.0}];
// bremsstrahlung for 1 Bq/g, Z=26, group [5e4,5e5] eV
// expected (physical): 0.385 * aZ[E0 ln(E0/lo) - (E0-lo)] with E0 = 190.7 keV  (non-zero in 100-190 keV)
// predicted actual:    1.0   * same expression with E0 = 73.4 keV
```
Confidence: high that ER is per decay (shown above); medium on severity, because the FISPACT-II manual's own
convention for partially-beta nuclides is not available locally. Either way the model "one beta per decay at E0" is
internally inconsistent when E0 is branch-weighted.
Suggested fix: electrons per decay f = sum of BR over modes whose RTYP contains digit 1 (beta-minus, including
"1.x" multi-particle codes) and E0 = ER(STYP=1) / f; if f is unavailable or zero report the nuclide in
`missing_inputs`. At minimum, document that the option is meaningful only for ~100 % beta-minus emitters and add the
branching used to `BremsstrahlungNuclideOut`.

---------------------------------------------------------------------------------------------------------------

## F2  robustness  crates/actinv-core/src/radiological.rs:419-431; docs/guide/specification.md ("Parents with progeny"); bundled tables

Rows that "include progeny" keep only the parent's coefficient. The progeny themselves are mostly absent from the same
table (verified by script against the committed tables): Euratom Table A: Cs-137 -> Ba-137m, Ru-106 -> Rh-106,
Ce-144 -> Pr-144/Pr-144m, Ag-110m -> Ag-110, In-114m -> In-114, Zr-97 -> Nb-97m, Mo-101 -> Tc-101, Pd-109/Cd-109 ->
Ag-109m, Ru-105 -> Rh-105m, Am-242m -> Np-238, U-232 -> Th-228 ... Pb-212 chain; Euratom Table B: Th-234/U-238 ->
Pa-234m, Ra-226/Rn-222 chains, Ag-108m -> Ag-108, etc.; 49 CFR 173.436: Cs-137 -> Ba-137m, Ru-106 -> Rh-106,
Ag-108m -> Ag-108, Am-242m -> Am-242, Th-234/U-238 -> Pa-234m, Ra/Th chains.
Every real inventory containing the parent also contains the progeny (ACTINV evolves the chain), so the progeny
activity is always reported as "missing" even though the regulation says it is covered by the parent value.

Reproduction: decay-only spec, 1 Bq/g Cs-137, 1 d cooling, `radiological.table.path = bundled:euratom-2013-59-annex-vii-a`.
Predicted actual: value = 1000*1/100 = 10 (correct), `missing_active_nuclides = ["Ba137m1"]`,
`missing_activity_Bq_per_g` about 0.94, `activity_coverage_fraction` about 0.51, and with `require_complete: true`
the whole run is rejected. Expected: Ba-137m recognised as covered by the parent (coverage 1.0, no failure).
The opposite failure also exists where the progeny does have its own row (Sr-90/Y-90 in 49 CFR 173.435 footnote (a):
A2(Y-90) = 0.3 TBq; 173.436 Sr-90(b)/Y-90): both rows are summed, the progeny is counted twice. The docs only
describe this second case ("counted again; remove them from the inventory"), but the inventory cannot be edited,
and the first case is not described at all.
Confidence: high (data-verified; behaviour read from evaluate()).
Suggested fix: add an optional per-response `progeny_covered` list (or a table-level `covered_by_parent` map from
the sidecar data the converters already extract) that evaluate() treats as covered with zero contribution and lists
separately; keep the format hash-pinned. Until then state the limitation in the handbook.

## F3  robustness  crates/actinv-core/src/radiological.rs:419-431; scripts/regulatory/convert_cfr_173435.py (omit "Unlimited")

"Unlimited" A1/A2 values (44 rows: Ca-41, Hf-182, I-129, Mn-53, Ni-59, Pb-205, Pd-107, Rb-87, Re-187, Sm-147, Tc-97,
Th-232, Zr-93, U-235/U-238, U-236 fast, ...) are not coefficients, so the nuclide is "missing" instead of "contributes
zero". For A1/A2 an unlimited limit means exactly zero contribution; reporting it as uncovered distorts
`activity_coverage_fraction` and makes `require_complete: true` impossible for ordinary activation products
(Mn-53 from Fe-54(n,2n); Ni-59 and Zr-93 in steels; I-129 in fission products). The docs do say `require_complete`
rejects such runs, but that makes the documented FISPACT-II `ATWO` mapping (bundled A2) unusable in strict mode.
Reproduction: any Fe or SS316 irradiation with the cooling chain run to long times, `bundled:us-49cfr173-a2`,
`require_complete: true`. Predicted: error naming Mn53 (and Ni59 for steel). Expected: success, Mn53 reported as
"unlimited".
Confidence: high on mechanism (positive atoms of Mn-53 follow from the decay library); I did not run it.
Suggested fix: same mechanism as F2 (an `unlimited` list in the response, reported but counted as covered).

## F4  robustness  crates/actinv-core/src/run.rs:5389-5393, 6117, 7563-7578; spec.rs:1977-1987

`photon.point_source` and `photon.bremsstrahlung` are only evaluated when `want_photons` is true (`options.outputs`
absent, or containing "photons" or "dose"). With `options.outputs: ["activity"]` the options validate, no
`photon_source` is produced, yet `certificate.photon.point_source` / `.bremsstrahlung` are still written, claiming a
computation that did not happen. `spec.rs` rejects `damage` output without a `damage` section but has no analogous
check here.
Reproduction: `examples/fns_fe_5min.json` plus `"options":{"outputs":["activity"]}` plus
`"photon":{"point_source":{"distance_m":1.0}}`. Predicted: runs, certificate shows `point_source`, no step has
`photon_source`. Expected: validation error naming the conflicting options.
Confidence: high (code read). Fix: validate in Spec::validate.

## F5  robustness  crates/actinv-core/src/photon.rs:164-239 (point_source_dose), :843-866 (missing_power)

The point-source dose is withheld for under/overflow and above-response energies, but not for decay-record EM energy
that has no photon spectrum (`unrepresented_gamma_power_W_g`, `nuclides_with_em_energy_but_no_photon_spectrum`).
`reference_power_W_g` is `source_power_W_g`, which excludes that power, so the dose is `Some(x)` while some of the
gamma power is absent. The contact proxy has the same pre-existing gap; the new field inherits it silently and the doc
statement "null under the same conditions as the contact proxy" is only true in that sense.
Reproduction: nuclide with `energies[2] > 0` and no STYP=0 spectrum plus one normal nuclide, point_source with air
attenuation off. Predicted: `gamma_air_dose_Sv_h` is a number, `unrepresented_gamma_power_W_g > 0`. Expected: either
null with a reason or a documented `represented_gamma_power_fraction` caveat in `point_source_dose`.
Confidence: high on code, low on practical frequency. Fix: add the unrepresented power to `excluded_power_W` (or
expose the fraction inside `point_source_dose`).

## F6  robustness  crates/actinv-core/src/photon.rs:320-325, 455-475, 467-469, 517-523

Bremsstrahlung completeness is not reported in the doses:
(a) The 0-eV-edge group is left empty and its energy only appears in `zero_lower_edge_group_power_W_g`; the contact and
point doses are still `Some`. The omitted share is (E0*u - u^2/2)/(E0^2/2) for u = 10 keV (first edge of both built-in
structures): 19 % of the emitted energy at E0 = 100 keV, 8 % at 250 keV, 4 % at 500 keV. Self-absorption in the
contact proxy makes the dose impact small, but the point-source dose has no such damping and the output carries no
flag.
(b) Group energy is photons x bin midpoint (Eq. 67 as implemented). In the group that contains E0 every photon has
energy <= E0 but is assigned (lo+hi)/2, which can exceed E0 (for a 120 keV electron in [100,200] keV: assigned 150 keV,
true mean under 120 keV). I could not check whether the manual prescribes this; if it does, it is faithful, but the
emitted energy per electron can then exceed E0 in a narrow case, so no energy-closure diagnostic exists.
Confidence: medium (design, not arithmetic). Fix: add `energy_closure` (emitted / (n * E0)) and a flag when the
zero-edge share exceeds a stated tolerance; document (b).

## F7  robustness  crates/actinv-core/src/photon.rs:244-303

`add_point_source_dose` is `pub` and trusts `options.distance_m` and `air_density_g_cm3`; only `Spec::validate` (spec.rs
2168-2176) enforces `distance_m >= 0.3` and `air_density > 0`. A library caller (python binding, GUI, tests) passing
`distance_m = 0` gets `inf` for each term and `Some(inf)` (the completeness test does not check finiteness), which
serde_json cannot serialise. Also, `gamma_air_dose_Sv_h_per_g = d / mass_g` has no guard for `mass_g <= 0` (validated
upstream only). Confidence: high, severity low. Fix: `debug_assert!`/early `unavailable_reason` for non-finite or
sub-0.3 m values and a finiteness check on `dose`.

## F8  robustness  crates/actinv-core/src/spec.rs:1593-1620

For `bundled:` tables the table text is available in memory at validation time, yet `radiological.responses` selectors
are only checked for emptiness and duplicates. A typo such as `"a2-u-medum"` passes `actinv validate` and
`check_files`, and fails only when the run builds the table (`unknown radiological response selector`). The handbook
H4 gate validates complete specs, so this matters for the documented fragments. Reproduction: spec with
`{"table":{"path":"bundled:us-49cfr173-a2"},"responses":["a2-u-medum"]}`; predicted: validate OK, run error.
Confidence: high. Fix: in validate, parse `bundled_table(name)` and check the selectors.

---------------------------------------------------------------------------------------------------------------

## Documentation findings

D1  docs/guide/results.md:26: "the `a2` transport index, which needs your A2 table" is stale; bundled A2/A1 tables
exist (specification.md and fispact-migration.md say so).

D2  scripts/regulatory/convert_euratom_annex_vii.py docstring (output list, `-b-activity`): "the index is the fraction
of the limit per kg of material; multiply by the mass in kg". The engine and the emitted `basis` text compute per
gram (activity input is Bq/g), so the multiplier is the mass in grams. A user following the converter docstring is off
by 1000.

D3  docs/guide/specification.md "Parents with progeny" (see F2): incomplete and the suggested remedy ("remove them
from the inventory") is not possible; the "missing progeny" case is undocumented. 49 CFR 173.435 footnote (i)
(Mo-99 A2 = 0.74 TBq for domestic use; table holds the 0.6 TBq value) and the Euratom K-40 potassium-salts note
(Table B columns 2 and 3; only the concentration `basis` mentions it) are recorded only in the row sidecars, not in
the handbook.

D4  Photon point-source and contact outputs are labelled Sv/h (`gamma_air_dose_Sv_h`, results.md "dose rate in Sv/h")
but are the absorbed dose in air (air kerma rate, Gy/h numerically). Only a code comment (photon.rs:69) says this.
Sv/h implies a dose equivalent (ambient dose equivalent H*(10) is roughly 1.1 to 1.4 times air kerma at
0.1 to 3 MeV). Say so in results.md. (Contact proxy is named Gy_h, so the two photon dose fields use different
units for the same quantity.)

D5  The default `air_attenuation: true` needs an `air_mass_attenuation` curve that the shipped/installed photon
response does not contain, so a default `point_source` returns `null` with a reason until the user rebuilds and
re-hashes a response (documented in two places, but the quick-start `DOSE 2` import path produces nulls by default).
Consider noting it in the fispact-migration `DOSE 2` row.

---------------------------------------------------------------------------------------------------------------

## What I checked and found correct

Regulatory tables and converters
- Re-ran both converters (CFR with the pinned eCFR XML, Euratom with the pinned XHTML, outputs to scratch): all seven
  bundled tables and the three sidecars are byte-identical to the committed files.
- Independent re-parse of the sidecar cell text (separate regex, not the converter's `scaled_float`) against all four
  CFR tables and all three uranium variants: 1055 A1, 1055 A2, 1101 + 1101 exempt checks, 0 mismatches, no extra keys.
- Units: A1/A2 TBq -> Bq (x1e12), exempt concentration Bq/g -> Bq/kg (x1e3), Euratom kBq/kg -> Bq/kg (x1e3), Table B
  column 3 in Bq. The converter's Ci-versus-TBq cross check (6 % band) raised no `unit_cross_check_off` flag on any
  row, which independently confirms every exponent. Spot values match SSR-6 / Directive from memory: Co-60 A1=A2=0.4,
  Cs-137 2/0.6, Tc-99m 10/4, Tc-99 40/0.9, Am-241 10/1e-3, H-3 40/40, C-14 40/3, Ni-63 40/30, Mo-99 1/0.6; Euratom A:
  H-3 100, C-14 1, Co-60 0.1, Cs-137 0.1, Sr-90 1, Tc-99m 100, Pu-239 0.1 kBq/kg; Table B H-3 1e6 kBq/kg and 1e9 Bq.
- Isomer mapping: every `mN` key in all tables exists in ENDF/B-VIII.0 decay data with the half-life of the intended
  state (checked 51 keys against MF8/MT457). Eu-150, Np-236 and Ta-178 lifetime labels map to the right states.
  `Tc99m1` etc. match `run.rs::name_of`.
- "Unlimited" rows are never coefficients and are not silently zero: they appear in `missing_active_nuclides`
  (hence F3 is about usability, not hidden zeros). Element-level rows (U(nat), Rb(nat), ...) are omitted and listed
  in the sidecar. Uranium is split into three explicit responses; nothing is picked for the user.
- Radiological kinds: `contribution`, `output_unit`, `coefficient_unit` and `formula` agree with each other and with
  specification.md; `a2`/`activity_limit` formula keys are added to the ledger only when selected, and optional
  source fields use `skip_serializing_if`, so default certificates and ledgers keep their bytes. Zero/NaN/overflow
  guards in `evaluate` are sound; strictly positive coefficients are enforced at parse time.
- `bundled:` handling: unknown name lists the choices; a conflicting declared hash is an error in resolve, check_files
  and run; the hash is of the embedded bytes; `bundled:` paths are not joined to the spec directory.
- fetch_icrp119.py: column choice (last of six = adult), `n` -> `m2` isomer naming, minus-sign control character,
  https-only confirmed download. Could not run (needs the PDF).

Photon
- Point-source dose units: mu_en [cm2/g] x 0.1 = m2/kg; W/m2 x m2/kg = Gy/s; x 3600; air transmission
  exp(-mu/rho * rho * 100 * r) is dimensionally right for r in metres; NIST dry-air density 1.20479e-3 g/cm3 is the
  right value for table 4. Inverse-square and linear-in-mass behaviour hold by construction and by the existing tests.
- Distance below 0.3 m is rejected in `Spec::validate` (and the docs say so); zero or negative `material.mass_g` is
  rejected (spec.rs:1417); non-finite/non-positive air density rejected.
- Missing air attenuation curve gives `null` plus a reason, not zero; energies below/above the response range go to
  `subthreshold_power_W` / `excluded_power_W` and withhold the dose; ungrouped (overflow) power withholds it too.
- Default outputs are unchanged: every new field uses `skip_serializing_if`/`default`, `air_mass_attenuation` is
  optional, `air-attenuation` is opt-in in the response builder, and the builder reads the second NIST column (mu/rho)
  for the new curve (the first/third were already mu/rho and mu_en/rho).
- Bremsstrahlung arithmetic: Eq. 66 branches (E0 >= hi, lo < E0 < hi, E0 <= lo), keV/eV conversions, EV factor, and the
  zero-edge analytic energy aZ(E0*u - u^2/2) are correct as implemented; the group with lower edge 0 is not given a
  photon count; nuclides lacking a STYP=1 average energy or a Z_eff are listed in `missing_inputs` with the reason and
  contribute nothing; requested nuclides without activity are listed; the bremsstrahlung block is never merged into
  the decay-photon groups, so there is no double counting with decay photons by construction (the user adds the two).
- Z_eff: atom-fraction mean of Z from the composition-resolved inventory (explicit nuclides and natural-abundance
  elements both handled), unresolved elements make it unavailable rather than approximate.
- Steiner-22: 22 groups, 0 ... 14 MeV, strictly increasing, consistent with the documented description; photons above
  14 MeV go to overflow and withhold the doses. I could not verify the table against the FISPACT-II manual (not
  available locally); the unit test only checks internal consistency and shared edges with the 24-group structure.
