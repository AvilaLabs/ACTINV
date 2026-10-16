# P140 H1 review: schedule controls (rebin, wall loading, partition, overrides, decay-only)

Reviewer did not implement this code. Read-only review: no build, test or binary was run. All findings are from
reading `git diff 2f3daa4 HEAD` plus the surrounding code; the "predicted actual" lines are derived by tracing code,
not by execution, so the coordinator must reproduce each before accepting.

Files read: crates/actinv-core/src/{flux.rs (rebin_equal_energy, clip_spectrum_range, rebin_equal_lethargy),
overrides.rs, spec.rs (Spectrum, Step/StepWire, validate, validate_decay_only, validate_overrides, physical_inputs,
resolve_step_libraries, step_library_conflict), run.rs (decay-only prepare, half-life overrides, cross-section view,
partition operator, ledger blocks, ADCROSS), chain.rs diff, reverse.rs / mesh.rs / study.rs diffs}, the P124 section of
docs/guide/specification.md, crates/actinv-cli (waste_composition_solve, command validate, inspect load_spectrum,
optimize FluxScale).

Counts: defect 3, robustness 9, documentation 4 (16 findings).

---------------------------------------------------------------------------------------------------------------

## Ranked findings

### F1 [defect] Rebin that leaves (almost) no flux in the destination range runs as a silent pure-decay problem
- Where: crates/actinv-core/src/spec.rs:837-894 (`Spectrum::resolved_flux`), 1773-1800 / 2559 (guards), quantity.rs:262
  (`GroupFluxes::new` accepts all zeros); prepared.rs:296-303 (`validate_flux` accepts a zero request).
- The pre-existing guard `check_total_has_shape` (spec.rs:728) exists precisely so a declared `total` "would [not]
  silently discard it and turn the run into pure decay". It looks only at the SOURCE groups. After `rebin`, all of the
  flux can fall in `underflow`/`overflow` (source wholly outside the destination grid) and `converted.flux_per_group`
  is all zeros. Nothing checks `destination_total > 0` when `source_total > 0`. The ledger records
  `outside_destination_below/above` but the run proceeds; every irradiating step then has zero reaction rates.
  Partial loss (e.g. 90 % of the flux outside the grid) is likewise accepted without a threshold.
- Reproduction (validates; `physical_inputs` and run succeed):
  ```
  "spectrum": {"structure": "fispact-709", "boundaries_eV": [1e-12, 1e-11], "flux_per_group": [1e14],
               "rebin": {}},
  "schedule": [{"dt": "1 d", "flux": 1.0}]
  ```
  fispact-709 spans 1e-5 eV to 1e9 eV (crates/actinv-data/data/fispact_709_groups.json), so the single source group is
  entirely underflow. Expected: error naming the spectrum ("no flux lands in the destination structure"). Predicted
  actual: `physical.flux.values()` all zero, `spectrum_conversion[0].outside_destination_below == 1e14`,
  `destination_total == 0`, activation result equals a cooling-only run. A `wall_loading_MW_m2` step on the same
  spectrum errors with "needs a spectrum with positive total flux" (so only the plain `flux` path is silent).
  Unit sketch: `Spec::from_json(..).unwrap().physical_inputs().unwrap().flux.values().iter().sum::<f64>() == 0.0`.
- Confidence: high (code path is unconditional).
- Fix: in `resolved_flux`, after rebin, error when `converted.source_total > 0 && converted.destination_total == 0`;
  consider also refusing (or recording a ledger warning field) when the outside fraction exceeds a stated bound
  (e.g. 1 %).

### F2 [defect] Decay-only spec with wall_loading_MW_m2 = 0 validates, then fails at run with an unrelated message
- Where: spec.rs:976-992 (`wall_flux_multiplier`), 2593-2603 (caller), 1790-1796 (zero allowed), 1150-1154
  (`irradiates` false for wall 0).
- `validate()` accepts `wall_loading_MW_m2: 0` ("nonnegative"; `irradiates()` is false so the decay-only gate passes).
  `physical_inputs()` then calls `wall_flux_multiplier(0.0, None, total)`; for the decay-only placeholder spectrum (or any
  zero-total spectrum) with no reference it returns "a wall-loading step needs a spectrum with positive total flux or a
  declared wall_loading_MW_m2 reference", although the wall loading is zero and the multiplier is trivially 0.
- Reproduction: `{"spec":"actinv-spec-1","decay":{"primary":"d.endf"},"material":{"composition":{"FE":100}},
  "schedule":[{"dt":"1 d","wall_loading_MW_m2":0.0}]}`. Expected: validates and runs (cooling). Predicted actual:
  `Spec::from_json` Ok, `physical_inputs()`/run Err as above. `actinv validate` says "ok" for a spec that cannot run.
- Confidence: high.
- Fix: return `(0.0, basis)` when `wall == 0.0` before looking at `total`/`reference`.

### F3 [defect] `half_life_uncertainty` overrides can never be used in a decay-only run; documentation says they can
- Where: docs/guide/specification.md:160-162 ("`half_life` and `half_life_uncertainty` overrides remain allowed");
  spec.rs:2341-2377 (`validate_decay_only` explicitly passes the kind), 2379-2441 and 2459-2477 (`validate_overrides`
  requires `uncertainty` requesting `decay_constants`), spec.rs:2350 (`uncertainty` is rejected for decay-only); run.rs:5324.
- A `half_life_uncertainty` needs an `uncertainty` section (validate_overrides) and a decay-only spec forbids
  `uncertainty` (validate_decay_only) and the run again refuses ("uncertainty overrides need an uncertainty section").
  The branch in `validate_decay_only` that exempts the kind is dead, and no test covers it (the decay-only tests only
  exercise `half_life`).
- Reproduction: decay-only spec + `"overrides":[{"nuclide":"Co60","kind":"half_life_uncertainty","value":0.01}]`.
  Documented: allowed. Actual: Err "override half_life_uncertainty 'Co60' needs an uncertainty section requesting the
  decay_constants channel" (and if an uncertainty section is added: "uncertainty is not available: a decay-only run...").
- Confidence: high (documentation and dead code; behaviour is a refusal, which is arguably right).
- Fix: correct the guide (only `half_life` remains) and remove the dead exemption, or support covariance-free decay
  uncertainty (out of scope for P124).

### F4 [robustness] `kerma` is not rejected for decay-only specs; failure is late and misleading
- Where: spec.rs:2341-2377 (list omits `kerma`), run.rs:826-845 (`LoadedKerma::load` compares to `lib.boundaries_ev()`,
  which is `&[]` for `ActivationLibrary::Absent`).
- A decay-only spec with a `kerma` section validates; the run fails with "kerma table boundaries_eV do not match the
  activation library boundaries". Docs list "everything that needs a library" and omit kerma, `options.fission`
  (fine) and `options.gas` (works from decay data). Cooling-only kerma is a legitimate empty-heating output.
- Reproduction: `decay_only_spec()` + `"kerma": {"table": {"path":"k.json","sha256":"0"*64}}` -> `from_json` Ok,
  run Err (message above).
- Confidence: high.
- Fix: refuse `kerma` in `validate_decay_only` with the NOTE message, or skip the boundary comparison when the
  library is absent.

### F5 [robustness] Absolute `cross_section` override is refused when the reaction collapses to zero under ANY spectrum of the schedule, including spectra of non-irradiating steps and an unused base spectrum
- Where: overrides.rs:164-177 (`check_spectrum`), run.rs:3867 (base `phi`) and 4177 (every extra spectrum).
- The check is applied to the base spectrum even when every step carries its own spectrum, and to every distinct
  step spectrum even if that step has `flux: 0`. A threshold reaction (e.g. `{"nuclide":"Fe56","kind":"cross_section",
  "mt":16,"value":0.4}`) beside a cooling step whose spectrum is all-zero or sub-threshold aborts the whole run with
  "cannot set a nonzero value: the reaction collapses to zero under a spectrum of the schedule", although the override
  would never be exercised there.
- Reproduction: base spectrum 14 MeV, schedule `[{"dt":"1 h","flux":1}, {"dt":"1 d","flux":0,
  "spectrum":{"structure":"fispact-709","flux_per_group":[0 x 709]}}]` plus the override above. Expected: runs (the
  cooling step does not use the cross section). Predicted actual: Err at `view.check_spectrum(extra_phi)`.
- Confidence: medium (design choice, but the refusal is stricter than the stated reason).
- Fix: check only spectra of steps with a positive multiplier (and the base spectrum only if some step uses it).

### F6 [robustness] `partition: {}` (present but empty) is treated as "has a partition" by validation, mesh/study/reverse gates and step-library conflict, but as "no partition" by the run
- Where: spec.rs:1810 (`if let Some(partition)` then trace-mode refusal), 1720 / 2812 (`partition.is_some()`),
  mesh.rs:248, study.rs:589, reverse.rs:352/676 versus run.rs:4281 and 5538 (`!partition.is_empty()`).
- Reproduction: `{"dt":"1 s","flux":0,"partition":{}}` with `options.mode: "trace"` -> validation Err ("partition is not
  supported with options.mode 'trace'"), while the same step in a coupled/auto run is a no-op; with `pathways` set,
  validation refuses a no-op. Inconsistent but fails closed.
- Confidence: high; severity low.
- Fix: normalize an empty map to `None` in `StepWire::try_from`, or use `is_some_and(|p| !p.is_empty())` everywhere.

### F7 [robustness] Partition silently skips nuclides of the element that are absent from the decay chain, while the step summary re-adds those bulk atoms unchanged
- Where: run.rs:5540-5547 (`members` come from `keep` and `ch.keys` only) versus run.rs:5928-5936 (summary
  `states.extend(bulk_inv ... filter !ch.index.contains_key)`).
- Material nuclides absent from the decay library are tracked in `ledger.composition_isotopes_absent_from_decay_library`
  and live only in `bulk_inv`. `partition_move` never sees them, so `partition: {"X": 0.0}` leaves those atoms in the
  summary inventory and mass totals; `removed_atoms_per_g` is understated by them.
- Reproduction: needs a composition nuclide with no decay record (e.g. an element whose isotope is missing from the
  primary and fallback decay files) plus `summary` and a partition of that element with kept 0. Expected: element
  fully removed from summary. Predicted actual: the absent isotope's atoms remain in `summary` (grams, atoms).
- Confidence: low-medium (needs such a nuclide; reasoning from code, no run).
- Fix: at partition time, scale the absent bulk entries too (or refuse a partition of an element with absent isotopes).

### F8 [robustness] Partition forces the coupled solve, whose CRAM absolute floor can swamp trace-level activation products
- Where: run.rs:4281-4299 (`auto` + partition -> coupled), 5780-5800 (floor `alpha0 * nmax`).
- With a bulk of ~1e22 atoms/g the coupled system has a numerical floor `c.alpha0 * nmax` (~1e6 atoms/g for alpha0 ~2e-16);
  products below it are "reported, not removed" (`n_states_below_floor`). This is why the trace formulation exists;
  a harmless partition (e.g. `{"Ar": 0.01}` on natural Fe/steel) now moves a trace-mode problem onto the coupled
  formulation with no accuracy statement. Ledger partition block does not carry `numerical_floor`/`n_states_below_floor`.
- Confidence: medium; documented as "auto selects coupled" but not as a precision change.
- Fix: document the floor consequence in the Partition section and in `ledger.partition`
  (add the worst step's `numerical_floor_atoms_per_g`).

### F9 [robustness] `options.mode: "auto"` reported as `mode_forced_coupled: true` even when coupled would have been chosen anyway
- Where: run.rs:6965 (`"mode_forced_coupled": spec.options.mode == "auto"`).
- `auto` already picks coupled for burn-up (>1e-6) or bulk decay. The flag is true whenever the user left `auto`.
  Not wrong in effect but misleading evidence. Fix: record `has_partition && would_have_been_trace`.
- Confidence: high; severity low.

### F10 [robustness] `spectrum.rebin` limits (`emin_eV`/`emax_eV`) that lie outside the spectrum are only caught at run time
- Where: spec.rs:896-945 (`check_rebin`) versus flux.rs clip errors ("emin lies at or above the whole spectrum",
  "emax lies at or below the whole spectrum"), raised from `physical_inputs`.
- Reproduction: `rebin: {"emin_eV": 1e9}` on a 1e-3..2e7 eV source: `actinv validate` -> ok, `run` -> Err. Also, a
  `total` declared with a zero source shape in a STEP spectrum is caught only at run (`check_total_has_shape` at
  spec.rs:2559), while the base is caught at validate (2134). Also `actinv validate` prints `flux_per_group.len()`
  (source groups for a rebinned spectrum; "1 groups" for a decay-only placeholder) under "groups" (command.rs:1114).
- Confidence: high; severity low.
- Fix: run the clip (pure function of the spec) from `check_rebin`.

### F11 [robustness] `inspect xs` ignores `spectrum.rebin` and can silently collapse a library on the wrong grid
- Where: crates/actinv-cli/src/inspect.rs:733-761 (`spectrum_flux`).
- It takes `spectrum.ascending_flux()` and compares only the COUNT with `library.ngroups`. A run-spec spectrum with a
  `rebin` onto fispact-709 whose SOURCE also has 709 groups (different boundaries, e.g. a custom 709-group transport
  structure) passes the count check and is collapsed as if already on the library grid. Predicted actual: wrong
  one-group cross sections, no error. (For other source counts the error text, "spectrum has N group fluxes", is
  misleading.)
- Confidence: medium (inspect is another reviewer's module; flagged because `rebin` is the cause).
- Fix: use `Spectrum::resolved_flux` (it is `pub(crate)`; expose a public wrapper) or refuse a spectrum with `rebin`.

### F12 [robustness] Wall-loading fallback applies a 14 MeV D-T neutron equivalence to any projectile and spectrum; `spectrum.total` becomes inert for wall steps
- Where: spec.rs:970-992, 2593-2612; crates/actinv-cli/src/optimize.rs:407-416.
- Fallback: step flux = wall * 4.44e13 regardless of `projectile` (a proton or alpha spec gets a neutron-current
  equivalence) and of spectrum shape. No validation or ledger warning (`basis` string only). Also the multiplier is
  `wall * 4.44e13 / total`, so step flux is independent of `spectrum.total`: the `optimize` axis `FluxScale`
  (which scales `spectrum.total`) is a silent no-op for fallback wall steps (flat objective), whereas it works on the
  reference basis.
- Confidence: high for the `total` cancellation (algebra), medium for the projectile point.
- Fix: refuse the fallback for non-neutron projectiles; make optimize detect wall steps; or document both.

### F13 [documentation] The reference-loading and fallback wall bases treat flux lost outside the destination range differently
- Where: spec.rs:2593-2612 with `resolved_flux`.
- With a reference: multiplier = wall/reference times the REBINNED sum (flux outside the destination grid is lost, so
  step flux < wall/reference * source total). Without one: multiplier = wall*4.44e13 / rebinned total, which silently
  renormalises over the lost flux (step flux = wall*4.44e13 exactly). The guide says "(wall / reference) times the sum
  of the spectrum's group fluxes" without saying which sum. Both are defensible; they should be stated.
- Confidence: high. Fix: state in the guide, record `source_total` in the wall record.

### F14 [documentation] Absolute `cross_section` overrides are spectrum-independent; the ledger records only the base spectrum
- Where: overrides.rs:213-242 (Absolute rule applied under every phi), run.rs:3868-3881 (ledger),
  guide line ~143 ("for the run's spectrum").
- With per-step spectra the same barn value is imposed under every spectrum (a 14 MeV step and a thermal step collapse
  to the same number), unlike FISPACT-II's one-group value only if the deck has a single spectrum. The ledger shows
  `base_spectrum_collapsed_barns_before/after` only. Fix: say so in the guide; log before/after per extra spectrum.
- Confidence: high.

### F15 [documentation] LIMGRP: the group that straddles a limit keeps all of its flux inside a shrunken interval, but `limit_flux_moved_from_below/above` reports only wholly-outside groups
- Where: flux.rs `clip_spectrum_range` doc and code; spec.rs:877-883 record; guide "flux in source groups below it".
- For `emin` inside group g the flux of g stays whole and its lower edge becomes `emin`: the below-limit fraction of g
  is effectively moved up, but it is not counted in `limit_flux_moved_from_below`. Total is conserved, shape in the
  straddling group is compressed (equal-lethargy density rises). Consistent with the stated convention, but the
  ledger understates the redistribution and the manual-equivalence is unverified here (no copy of FISPACT-II 4.36).
- Confidence: medium. Fix: also report the straddling group's below-limit share, or state the convention in the guide.

### F16 [documentation] Omitted `spectrum` messages
- A spec WITH a library, an omitted `spectrum` and only cooling steps validates and fails at run with
  "spectrum has 1 groups but activation library has 709" (placeholder leaks into the message). A decay-only spec with
  a step `spectrum` and no base spectrum fails with "schedule step spectrum.structure 'fispact-709' must match the base
  spectrum's 'custom'". Both refuse correctly but name the placeholder. Fix: detect the placeholder and say
  "spectrum is required when a library is given / when a step declares a spectrum".
- Confidence: high; severity low.

---------------------------------------------------------------------------------------------------------------

## What I checked and found correct (no finding)

Rebin / LIMGRP
- `rebin_equal_energy` and `rebin_equal_lethargy`: partition-point start, loop termination, `saturating_sub` for
  sources wholly below the grid, empty loop for sources wholly above it; under/overflow only for the portion outside;
  partial overlaps use energy widths (equal-energy) or log widths (lethargy) over the source group's own width, so every
  source group contributes exactly its flux across destination + under + overflow; closure check 1e-12 hard error;
  Neumaier sums. Exact-grid shortcut compares boundaries bitwise (no false hit).
- Strict-ascending/positive/finite boundary validation (zero-width and descending sources rejected by
  `validate_boundaries` and `check_rebin`); `descending` flux reversal pairs with always-ascending boundaries.
- `clip_spectrum_range`: after `emin` the new first width is strictly positive (`partition_point(<= limit)`), after `emax`
  the new last width is strictly positive (`partition_point(< limit)`); the same-source-group emin/emax case, emin equal
  to an existing boundary, limits outside the spectrum (no-op), `emin>=top` and `emax<=bottom` errors; no zero-width group
  can be produced for the rebin.
- `total` scaling happens before rebin, fixed `relative_error` + rebin refused, `custom` + rebin refused, rebin on a step
  that switches library refused, group count compared after rebin, base/step distinctions in `spectrum_conversion`.
- `reverse::solve_qualified` refuses `rebin` (it indexes raw `flux_per_group`); mesh cells use their own rebin and set
  `rebin: None`; `study` echo is not affected.

Wall loading
- Exactly-one-driver logic in `StepWire::try_from` and in `validate()` (flux/wall/power, both orders; hand-built Step);
  wall and power mutually exclusive on a step; serialization omits `flux` for wall steps and round-trips; sign/NaN checks;
  step spectrum uses its own reference (and sticky library-step spectrum); `irradiates()` counts wall > 0, so a wall step
  cannot hide inside a decay-only spec; waste-composition solve now uses `irradiates()`; mesh/study/reverse refuse wall.
- Field order of `Step`, `Spectrum`, `Spec` preserved and new fields use `skip_serializing_if`, so default serialization
  is byte-identical (checked against the 2f3daa4 `Step` layout).

Partition
- Fraction in [0,1], NaN, element-only keys, duplicate elements (canonical symbol), partition + pathways refused at both
  validation and run, trace refusal at validation and run, `auto` -> coupled, step-library combination refused.
- `partition_move` is a linear, parameter-independent operator and is applied to `y`, every tangent and
  `alternate_y`; the sink is forced into `keep` for a partition-only schedule (pruning would otherwise drop it);
  negative-clip loops exclude the sink; first step, last step and zero-length step behave (applied at step start, before
  evolution; zero-length step is a pure split); element absent from inventory is a recorded no-op (needed for
  products that appear later).
- Depletion: removed atoms count as depleted by design (`depleted_atoms_per_g` doc says net of partition/removal);
  summary excludes the sink from nuclide states; fission count uses the post-partition `y` as its start state.

Overrides
- Cross-section view: loss row anchors the absolute value, product rows scale by value/loss (branching preserved),
  scale multiplies all rows, zero value turns the reaction off (no triplets), `check_spectrum` guards the 0/0 split; the
  view feeds reaction rates, burn-up optical depth, loss-rate recording for depletion and the fission rate counter, so mode
  selection and rates agree; MT 18 has both a loss row and the zap=0 fission row (builder.rs) and both scale;
  `collapse_row_scaled` honours Scale and the run refuses Absolute + self-shielding for all rows; absent nuclide / MT with
  no row / element name / no loss row all produce named errors, nothing is silently ignored; cross-section value
  overrides + uncertainty refused; step-library + cross-section overrides refused (rows belong to the base library).
- Half-life: factor = datum/new rides the existing `decay_scale` machinery (edges, lambda_eff, nuclide table copy used by
  photons/heat/activity, bulk-decay mode test, SF decay rates via `sf_decay_eff`, depletion lambda, decay-parameter
  lambda); stable nuclide and nuclide absent from the decay data are errors; stacking with `decay_scale` multiplies;
  units parsed by the schedule parser (unknown units rejected at validate); non-finite/non-positive rejected.
- ADCROSS/ADLAM: rescale `D C D` keeps correlations/PSD; applied to the matrix used by both the band and the depletion
  covariance; requires the channel and (for ADCROSS) a covariance; zero-variance parameters counted and an all-zero
  match is a named error; ADLAM replaces the datum's relative sigma in both the decay channel and depletion.
- Duplicate detection normalises nuclide names to (ZA, LISO); cross_section + cross_section_scale on one reaction refused.

Decay-only
- Gate "no step irradiates" (flux, wall, power); library.sha256, uncertainty, self-shielding, damage, fission-yield
  files, rate/yield scale, step-library, cross-section overrides all rejected with the shared NOTE; prepare skips every
  library read, stand-in groups, index and hash; ledger/certificate carry `decay_only` and null library inputs; the
  `lib.group_count()==spectrum` test is skipped; empty `rows()` yields no triplets; no division by flux total or by group
  count was found that could produce NaN in summary, photon or uncertainty paths (uncertainty is refused outright).
  `mesh` refuses an empty library path.
