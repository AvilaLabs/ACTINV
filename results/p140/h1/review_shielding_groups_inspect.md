# P140 H1 adversarial review: shielding, group structures, splice, step libraries, inspect

Scope reviewed (read-only, nothing executed except Python reads of JSON/ENDF text):
crates/actinv-core/src/shielding.rs, crates/actinv-data/src/resonance.rs (`rml_resonance_widths`),
crates/actinv-data/src/groups.rs + data/fispact_*_groups.json, spallation.rs, builder.rs (splice, structure checks),
activation.rs (triton/helion), run.rs / spec.rs (step libraries, self-shielding wiring), study.rs (shielding path),
crates/actinv-cli/src/inspect.rs, workflow.rs, docs/guide/specification.md, protocols P128-P131, P139 (+A), ledger 148, 149, 156, 162.

Counts: 14 findings. defect 2, robustness 8, documentation 4.

Ranked findings

----------------------------------------------------------------------------------------------------------------
## P140-H1-01  defect  (confidence: medium)
Resonances outside the evaluated resolved range [EL, EH] are used as shielding "peaks".

File: crates/actinv-core/src/shielding.rs:~680-745 (`resolved_lines`); `range.energy_min/max` are used only inside
an error message. Same omission for the BW and RM arms. `rml_resonance_widths` (resonance.rs:~1770-1850) also
returns every positive-energy level of every spin group regardless of the range.

Description. LRF=7 spin groups routinely carry levels above EH. They are R-matrix poles that feed the in-range
cross section, but their peaks lie outside the range the evaluation resolves, in the region where the evaluation
uses the MF=3 background / unresolved treatment. `resolved_lines` feeds them into the sigmoid model, so they scale
(and are counted in) groups above EH.

Evidence (real file, read with a throwaway Python parse): ~/nuclear-data/endfb-viii.1-fns-arm/pristine/W186.endf
has range EL=1e-5, EH=1e4 eV, IFG=0, KRM=3. Spin group 1 (J=+0.5) lists three levels above EH: 10107.81,
10241.14 and 10373.98 eV. The file has 175 levels: 3 with E<=0, 169 inside the range, 3 above EH. Ledger entry 162
reports "172 usable resonances" = 169 + 3, so the out-of-range levels ARE counted. The P139 G3 checker mirrors the
implementation's convention, so it cannot catch this.

Reproduction. Geometry spec (foil 0.01 cm, density 19.3, W186 metal) with resonance_files=[that file]; read the
ledger `shielding.geometry.factors.W186[*]` entries for the groups containing 10107-10374 eV (709-group grid).
Expected: no entry for groups above 1e4 eV, `resonances` totals 169 across all groups. Predicted actual: entries
exist in those groups, totals 172, and those groups' cross sections are scaled by a factor below 1.

Fix. Clip to `range.energy_min <= E <= range.energy_max` (per range) before building lines, for all three arms,
and add the dropped count to the geometry ledger (`resonances_outside_range`). If instead the owner decides
external levels should count, say so in the P139 protocol and docs and test it explicitly.

----------------------------------------------------------------------------------------------------------------
## P140-H1-02  defect  (confidence: high)
A schedule library override cannot use any FISPACT neutron structure other than fispact-709.

File: crates/actinv-core/src/spec.rs:742-795 (`Spectrum::check_structure_for`), called at spec.rs:2563 for steps
on an overriding library. The match is hard-coded: `"fispact-709" => (709*factor, true)`,
`"fispact-162" => (162*factor, false)`, everything else `unknown {label} structure`. The base-spectrum path
(`named_structure_groups`, spec.rs:2314-2330) was updated in P130 to consult
`GroupStructure::named/is_neutron_named`; this second copy of the table was not.

Reproduction (spec fragment): base library TENDL-2025 709 groups; step 2:
`{"dt":"1 h","flux":1.0,"library":{"path":"n1102.npz"},"spectrum":{"structure":"fispact-1102","flux_per_group":[... 1102 values ...]}}`
where n1102.npz is a neutron library built with `--groups fispact-1102`. Expected: accepted (docs/guide
specification.md:1062 says a spectrum on any of these structures is valid). Predicted actual: spec refused with
`unknown schedule step spectrum structure 'fispact-1102'` (same for -66 ... -616, and for xN refinements of them).
Workaround is `structure: "custom"` with explicit boundaries, which is not what the docs (spec.md:970) advertise.

Fix. Make `check_structure_for` delegate to the same registry as `named_structure_groups` (neutron_grid =
`is_neutron_named`, group count from `GroupStructure::named(base).groups() * factor`). Add a unit test with
fispact-1102 on a step library.

----------------------------------------------------------------------------------------------------------------
## P140-H1-03  robustness / doc-vs-code  (confidence: high)
`geometry.choose` with an element key hard-errors unless EVERY isotope of that element in the material has a
resonance file; the docs state a weaker rule and an inconsistent soft path exists when `choose` is absent.

File: shielding.rs `PreparedGeometry::plan` (~870-885): for a chosen nuclide with no `lines`, `if self.choose.is_empty()
{ uncovered.push; continue }` else `return Err("... choose selects {name}, but no resonance file supplies it")`.
Docs: specification.md ("every entry must be in the material and supplied by a resonance file"), whose own example
is `"choose": ["W"]` with one file `n_W184.endf`.

Reproduction. Natural-W material (W180/182/183/184/186), `choose: ["W"]`, `resonance_files: [W186.endf]`.
Expected per docs: W186 shielded (the entry "W" is supplied by a file); other W isotopes are uncovered/named.
Predicted actual: run refused: `choose selects W180, but no resonance file supplies it` (BTreeMap order). Dropping
`choose` entirely works and reports W180..W184 under `shielding_uncovered`, so the strict behaviour is only
reachable by naming the element.

Fix. For element entries treat a missing file like the no-choose case (uncovered, error only under
require_shielding_complete); keep the hard error for explicit nuclide entries. Align the docs sentence and
example.

----------------------------------------------------------------------------------------------------------------
## P140-H1-04  documentation  (confidence: high)
Certificate `method_limits` for geometry mode still says LRF=7 is unsupported.

File: crates/actinv-core/src/run.rs:7775: "only Breit-Wigner and Reich-Moore resolved resonances are used; the
unresolved range is not shielded". After P139 the model also reads R-matrix-limited (LRF=7) ranges (shielding.rs
`RangeData::RMatrixLimited` arm; specification.md:695). The ledger `model` string (run.rs:7322) is fine; only the
certificate text is stale. Because it is part of the certificate bytes it also contradicts what an LRF=7 run did.

Reproduction. Any geometry run on an ENDF/B-VIII.1 W186 file; read `certificate.shielding.method_limits[2]`.
Expected: mentions LRF=7 (with the single-level approximation and IFG/closed-channel conventions). Actual: the
sentence above.

Fix. Update the string (and any golden file that pins it). Note this changes certificate bytes for geometry runs
only.

----------------------------------------------------------------------------------------------------------------
## P140-H1-05  robustness  (confidence: medium)
Library switch: material/inventory nuclides that are not targets of the new library silently stop reacting; no
ledger field reports it.

File: run.rs ~4166-4235 (extra collapses on `self.step_libraries[slot]`), ledger block run.rs:6842-6890. The
per-library ledger lists `targets_absent_from_decay_library` (targets present in the library but missing from the
decay chain) and unmapped products, but never "initial-material or tracked nuclides with no row in this library".

Reproduction. Base: TENDL neutron 709 library for Fe. Step 2 switches to a triton library built for O only (or
any library lacking Fe-56) with a 162-group spectrum. Expected: Fe-56 production/depletion in step 2 is either
refused (`require_complete`-style) or listed. Predicted actual: Fe-56 contributes zero rate in step 2, no warning;
all `step_libraries.*` coverage fields look clean. The same hole applies to intermediate nuclides built up in step 1
that the second library has rows for only under a different target set.

Fix. In the step-library ledger add `material_nuclides_without_rows` (composition nuclides and, optionally, states
above a threshold that have no row in the library) and an `options.require_step_library_coverage` flag.

----------------------------------------------------------------------------------------------------------------
## P140-H1-06  robustness  (confidence: medium; partly documented)
Spallation splice: double counting and unreplaced partner rows when the library has data above the splice, plus
a gap with the snapped boundary.

File: spallation.rs `apply` (~230-300): a table row replaces a library row only if
`(target, mt, zap, lfs, lmf)` is identical; table products are always `lmf=10`, loss rows `(mt,-1,-1,0)`.
Library residual rows are built with lmf=-1 (residual arithmetic, builder.rs:3025-3050) or lmf=6 (MF=6 products,
builder.rs:2998). So for the same (mt, zap, lfs) the loss row (lmf 0) IS replaced while the library's production
row (lmf -1 / 6) is NOT, and the table's lmf=10 row is APPENDED. Any library with nonzero data in the spliced
groups then carries both.

Reproduction (unit-test sketch). Library with rows {(t0, mt16, zap=-1,lfs=-1,lmf=0), (t0, mt16, zap=82207, lfs=0, lmf=-1)},
nonzero in all groups; table with channel mt=16, loss_barns=[a..], products=[{zap:82207, sigma_barns:[b..]}], splice
inside the nonzero region. Expected (docs "replaces ... at and above the splice"): above the splice only the
table values remain. Predicted actual: loss row = a (replaced), lmf=-1 production row unchanged (library values),
new lmf=10 row = b appended: production counted twice, depletion counted once. With the default 200 MeV and
TENDL's 200 MeV ceiling there is no overlap, so no shipped run is affected. docs/guide specification.md says
overlapping reactions "count them as well", so the double count is acknowledged; the asymmetric loss/production
replacement is not.

Related: on `fispact-1102` the effective splice is 208.93 MeV (documented), and the group 199.53-208.93 MeV that
holds the TENDL 200 MeV edge carries a flat-lethargy average diluted by the missing upper part. Neither source
covers 200-208.93 MeV.

Fix. Emit `untouched_rows_nonzero_in_spliced_groups` (count and a few keys) in the index `spallation` block and
in the build summary, or add `--zero-unnamed-above-splice`. Consider matching existing rows on (mt, zap, lfs)
ignoring lmf for product rows.

----------------------------------------------------------------------------------------------------------------
## P140-H1-07  robustness  (confidence: medium)
Multi-isotope MF=2 evaluations (natural-element files) are handled by an ad hoc filter.

File: shielding.rs:~678 `.filter(|isotope| isotope.zai == za || evaluation.isotopes.len() == 1)` with
`za = evaluation.metadata.za`.
(a) Natural-element evaluation (material ZA e.g. 40000, isotopes 40090...): no isotope matches, `lines` empty,
the user gets `no usable resolved (LRU=1) resonances` with no hint that the file is multi-isotope.
(b) Multi-isotope material whose ZA equals one of its isotopes: only that isotope is used, the others are silently
dropped (not even listed in `resonance_data_unused`), and the abundance (AFRAC/ABN) is ignored, which is
correct only because N comes from the composition.
(c) A single-isotope file whose isotope ZAI differs from the MAT ZA is silently accepted.

Reproduction. Any JEFF/ENDF natural-element file with several isotopes in one MF=2 section as a geometry resonance
file. Expected: each isotope keyed by its own ZA, or a clear "multi-isotope MF=2 not supported" error. Actual:
(a) misleading error; (b) partial silent use.

Fix. Key `lines` by `isotope.zai` (liso from the evaluation only when it matches), error explicitly for multi-isotope
files if unsupported, list dropped isotopes.

----------------------------------------------------------------------------------------------------------------
## P140-H1-08  robustness  (confidence: high)
`inspect library` omits object-valued index fields, including the new `spallation` block; `structure_name` knows only
709/162 when the index is missing.

File: inspect.rs:~1005-1015 (`library_report`): `Value::Object(_) => {}` drops every object field (`spallation`,
`options`, ...), keeping only scalars and array lengths. HELP (inspect.rs:25) promises "index provenance fields".
inspect.rs:~668 (`structure_name`) fallback loop is `[(709,"fispact-709"),(162,"fispact-162")]` only, although
`GroupStructure::named` now knows 13 structures.

Reproduction. Build a library with `--spallation`, then `actinv inspect library OUT.npz`: expected splice
provenance (table sha, effective splice energy) in the output; actual: absent from both JSON and `--table`. For an
index-less 1102-group library: expected `fispact-1102`, actual `custom`.

Fix. Emit objects (at least `spallation`) under `index_objects`, and iterate `NAMED_STRUCTURES` in `structure_name`.

----------------------------------------------------------------------------------------------------------------
## P140-H1-09  robustness  (confidence: high)
`validate --files` and GUI path resolution ignore the new path-bearing fields.

File: crates/actinv-cli/src/workflow.rs `resolve_inputs` / `check_files`: they handle library, decay, photon,
covariance, radiological, kerma and fission-yield files; not `schedule[].library.path`,
`self_shielding.geometry.resonance_files[*].path` (and, pre-existing, `self_shielding.table`, `damage.table`).

Reproduction. Spec with relative `schedule[1].library.path` or relative resonance_files, `actinv validate spec.json
--files`: expected "cannot open" error for a missing file; actual `ok`. The GUI (`model::resolve_inputs`) does not
re-base them, so a project folder spec with relative step-library/resonance paths fails at run time only.

Fix. Add the fields to both functions (and the hash checks).

----------------------------------------------------------------------------------------------------------------
## P140-H1-10  robustness  (confidence: medium)
`inspect xs --spectrum`: group-structure compatibility is checked only by count (named spectra) and with a
looser tolerance than the solver.

File: inspect.rs:735-762 (`spectrum_flux`). Named structures: only `flux_per_group.len()` is compared, never the
spectrum's `structure` name against the library index `groups` / boundary hash (the solver does,
`group_structure_mismatch`, run.rs:2692). Custom: tolerance `1e-9*|right|` vs 1e-12 in the solver
(run.rs:4016-4021). `rebin` spectra fail with a count error rather than a rebin message. `--target 26056` is
parsed as a target INDEX (resolve_target: any integer string), giving "out of range" or, with no index file, a
misleading "no reactions match".

Reproduction. A spectrum `{"structure":"fispact-709",...}` against a custom-boundary 709-group library: expected
mismatch error; actual: a collapse with the wrong energy mapping and no warning.

Fix. Resolve the named structure and compare boundaries bitwise like `structure_name`; share the solver's tolerance;
reject all-digit targets with ZA-shaped values.

----------------------------------------------------------------------------------------------------------------
## P140-H1-11  documentation  (confidence: high)
docs/guide/specification.md:1060 (group-structure table) lists `fispact-162` as "proton, deuteron, alpha, gamma"
although triton and helion use it too (lib.rs:270, builder `check_fispact_structure`, spec.md:72 and the triton
section). Fix: add triton, helion.

----------------------------------------------------------------------------------------------------------------
## P140-H1-12  documentation  (confidence: high)
run.rs:~6474 reason string for suppressed isomer pathway shares: "pathway attribution suppressed for feed/removal
schedules" is also emitted when `pathways_suppressed` comes from a step library override (run.rs:6262). Cosmetic, but
it names the wrong cause. Fix: choose the message from `has_step_library`.

----------------------------------------------------------------------------------------------------------------
## P140-H1-13  robustness  (confidence: low)
Geometry mode takes its own `density_g_cm3`; `material.density_g_cm3` (used by power steps) may differ and nothing
cross-checks or defaults it (spec.rs `SelfShieldingGeometry::validate`). A mismatch silently scales every z.
Fix: default to `material.density_g_cm3` when present, error on disagreement beyond rounding.

----------------------------------------------------------------------------------------------------------------
## P140-H1-14  documentation / informational  (confidence: low, not verifiable offline)
Effective-length rules are discontinuous across shapes: cylinder `1.65 r h/(r+h)` tends to `1.65 r` as h -> infinity
while wire is `2 r`, and to `1.65 h` as r -> infinity while foil is `1.5 t`. The code follows the protocol/manual
Table 25 as quoted, so this is only worth a sentence in the docs so users do not read the cylinder as a
limit of the others.

----------------------------------------------------------------------------------------------------------------
## Checked and found correct

Group structures (a): all 13 vendored JSON files (66, 69, 100, 162, 172, 175, 211, 315, 351, 586, 616, 709, 1102):
boundaries strictly monotonic (all published descending, `from_json` reverses to ascending), no duplicates, all
positive eV, `n_boundaries == len`, `structure` equals the file name, group count equals the name. Registry
(`NAMED_STRUCTURES`) distinct, `named()` verifies the declared name, `is_neutron_named` excludes only 162;
`check_fispact_structure` (builder) accepts every neutron grid for neutron and requires 162 groups otherwise
(a refined `fispact-709x2` was already rejected before this change). Spot values match the tests' `ebins` list.
Documented snap value checked: first 1102 boundary at/above 200 MeV is 208.93 MeV; 709/162 contain 2.0e8.

Splice mechanics: `effective_boundary_index` (snap up with 1e-12 relative tolerance, rejects index 0 and no-group-
above), alignment check, zero fill below the splice for added rows, contiguous insertion after each target's last
row, `n_rows` bookkeeping, schema/dup/finite/non-negative validation, projectile match, `library.validate()` after
splice. Only exotic point: additions for a target with no existing rows go at the end out of target order
(harmless for the NPZ reader, would only break the checkpoint-restore `row_offset` assumption, which never sees
spliced libraries).

Geometry (a): sigmoid limits and NaN/non-positive handling, effective lengths and cylinder-height rules,
positive/finite dimension checks, weighted mean cannot divide by zero (lines require positive widths, `weight<=0`
skip), resonances outside the library group range skipped by `group_of`, groups half-open [b_g, b_g+1), unit
conversion for N (atoms/g x g/cm3, 1e-24 cm2/b), capture-only maps only MT 102, `row_scales` returns None for empty
maps, zero/negative number density gives G=1 (no division), duplicate-file detection, study.rs and run.rs share
the same `ShieldPlan::build` and the same atoms-per-gram input, collapse nominal check uses `row_scale`.
Overrides: validated positive finite, canonical nuclide, table mode only, unknown nuclide/group is an error after
the loops, clamped to the grid floor, serialization skipped when empty, spec echo and fingerprint unchanged for
specs without the new keys (`table` is `Option` with skip; field order preserved).

LRF=7 widths: KRM!=3 and missing/dangling pair indices are errors; entrance pair found by MT=2; AWRI from the pair
mass ratio (ledger 162 notes the 3e-7 HEAD-AWR difference); |J| and |I| (parity sign stripped); capture =
MT=102 sum, total = all channels; bound levels skipped before any penetrability call; IFG=0 never needs radii;
IFG=1 uses Gamma = 2 P gamma^2 with the reconstruction's own penetrability helper (refactored without change:
`rml_reduced_amplitude` now delegates); closed channels (E <= threshold, lab frame via `-Q/lab_to_cm`, capture pair
exempt) contribute zero under both IFG branches per Amendment A; charged channels use the Coulomb path. Multiple
MT=2 pairs would be summed as neutron with the first pair's kinematics (not present in any inspected file).

Triton/helion (c): `parse/name/nsub/za/from_nsub` are consistent (10030/(1,3), 20030/(2,3)); AWI sanity check uses
nominal mass 3 and cannot confuse deuteron (2) or alpha (4); `residual_product` generalizes the neutron delta by
(Zp, Ap-1), verified by hand for MT 102/103/105/106 and the MT 4, 50-91 ground-state rule; catalog expected-group
table, CLI usage, FISPACT PROJECTILE 6/7 mapping all updated.

Step libraries (c): conflict list covers uncertainty, gas, self_shielding, damage, fission_yields, rate_scale,
power-driven steps, kerma, partition, options.fission, cross-section overrides and spec-level pathways; mesh and
study refuse; output pathways are suppressed with a reason; prune uses the union of per-spectrum graphs;
burn-up optical depth is summed per step; return to base clears the sticky spectrum; neutron libraries must match
the run temperature, other projectiles 0 K; library hashes and paths enter the prepared-data fingerprint and the
certificate; step libraries are loaded groupwise (never the pre-collapsed artifact).

Inspect (d): units (eV, s, barns, 1/atom/s) are consistent, the one-group collapse and rate use
`ReactionLibrary::one_group` and the same sum-of-flux convention as the solver, isomer names use `m<n>` like the
rest of the crate, daughter derivation uses `step_of` with RFS, stable detection matches `Nuclide::lambda`,
discrete gamma energy uses FD x RI x E like photon.rs, `effective()` is the solver's interpolation (clamping reported),
cumulative tables are routed through the same interpolation, option parsing rejects duplicates/unknown flags and
negative or non-finite energies, empty selections and missing nuclides are errors, table rendering handles ragged
rows. Index-less libraries degrade to indices and `custom`/`neutron (no index)` labels explicitly flagged.

Not checked / limits: no execution of any binary, test or checker (rules); IFG=1 on real data has only synthetic
coverage (ledger 162 G4); manual Table 25 and Eq. 43-50 could not be compared against the manual itself.
