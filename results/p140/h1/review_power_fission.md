# P140 H1 review: power, kerma, fission count, spontaneous fission

Reviewer did not implement this code. Read-only review; nothing was built or run (no cargo, no `actinv` binary).
Reproductions below are therefore sketches derived from reading the code, not executed failing tests. The one
data-based claim (F1) was checked by parsing the ENDF/B-VIII.0 decay file with a stand-alone Python script.
Base for diffs: `2f3daa4..HEAD`. Line numbers are at HEAD.

Summary: no defect was found in the power solver, the kerma fold, the burn-up arithmetic, the nu-bar reader or the
yield parser. One defect (F1) is a misclassification in the decay chain. The remaining findings are robustness
and documentation items.

## Ranked findings

### F1  defect  (confidence: high on mechanism, low on numerical impact)
Beta-delayed fission (RTYP 2.6) is counted as spontaneous fission.

- File: `crates/actinv-core/src/chain.rs:144-159`.
- The loop over RTYP digits pushes `led.sf_decay.push((k, rate))` as soon as it meets a digit 6 at any
  position. Only the yield routing is guarded by `position == 0`. For RTYP 2.6 (EC/beta+ followed by fission) the digit 6
  sits at position 1, so the branch is recorded in `sf_decay`, increments `sf_branches`, and, when SF yields are supplied,
  is pushed to `sf_uncovered`.
- `sf_decay` feeds three outputs:
  - `fission.count.spontaneous_fissions_per_g`.
  - `spontaneous_neutron_source.neutrons_per_s_per_g` and its `uncovered_sf_decays_per_s_per_g`.
  - ledger `spontaneous_fission.nubar_covered` and `nubar_uncovered`.
- The ENDF/B-VIII.0 decay file (`actinv-data/v1.1.0/decay/endf-b-viii-0_decay.dat`) has three RTYP 2.6 modes:
  Bk-238 (BR 4.8e-4), Bk-240 (BR 2e-5) and Tl-180 (BR 1e-6). It has 125 modes with RTYP 6.0.
- The fission would occur in the daughter (Cm-238, Cm-240, Hg-180), not in the parent. A nu-bar for the parent would be
  applied to the wrong nuclide, and the daughter's fission is not a spontaneous fission of the parent.
- Reproduction (unit test in `chain.rs`, following `spontaneous_fission_without_yield_data_stays_on_leakage_and_is_listed`):
  one nuclide with `lambda = l` and `modes = [Mode { rtyp: 2.6, br: 5e-4, .. }, Mode { rtyp: 2.0, br: 0.9995, .. }]`.
  Call `build_with_spontaneous(&nuclides, None)`.
  - Expected: `ledger.sf_decay.is_empty()` and `ledger.sf_branches == 0`. The delayed-fission branch goes to leakage
    under its own count, which is the old `build` behaviour for an unmodelled mode.
  - Predicted actual: `sf_decay == [(k, l * 5e-4)]` and `sf_branches == 1`.
- Impact: a pure-Bk-238 or Bk-240 sample reports a nonzero `spontaneous_fissions_per_g` equal to
  `lambda * 4.8e-4 * N`. All three nuclides are short-lived and rare, so the effect on realistic runs is negligible.
- Fix: push to `sf_decay` only when `position == 0` and `digits.len() == 1`. Count delayed fission under a separate
  ledger key if it should be visible.

### F2  robustness  (confidence: high on mechanism, medium on frequency)
Kerma coverage is evaluated against the declared composition, but heating is evaluated against the decay chain. A nuclide
absent from the decay library is "covered" and contributes nothing.

- Files: `run.rs:4073-4088` (`kerma_uncovered` iterates `bulk_inv` and calls `table.covers`), `run.rs:3952-3961` (inventory
  keys absent from `ch.index` are dropped from `bulk`), `run.rs:857-876` (`kerma_terms_for` iterates `ch.keys`).
- A composition isotope that is not a chain state but is matched by a table row (typically an element row such as `Fe`)
  has `covers == true`:
  - it is not listed in `uncovered_composition_targets`;
  - `require_complete: true` does not reject the run;
  - it adds zero heating;
  - `covered_atom_fraction` never sees it, because `atoms_of` reads only chain states.
- Reproduction: spec with `material.basis: "atoms_per_g"` and an isotope that the decay library lacks. The decay file
  must lack it, so `composition_isotopes_absent_from_decay_library` lists it. Give the kerma table an element row for
  that element and set `require_complete: true`.
  - Expected: the run is rejected, or the isotope appears under uncovered.
  - Predicted: the run proceeds, `uncovered_composition_targets` is empty, and the power multiplier is computed
    without that isotope.
- Fix: build `kerma_uncovered` from `bulk` chain states, plus the `absent` list as uncovered with reason "not in the
  decay chain".

### F3  robustness  (confidence: high)
Power-step feasibility and the flux estimate come from the initial composition, not the composition at that step.

- File: `run.rs:4122-4141`. For every power step, `heating_per_unit` is taken from `bulk` (the t=0 inventory).
  If it is zero, the run fails before the schedule starts with "schedule step N: the starting material has no
  kerma heating under this step's spectrum".
- Reproduction: material `Ni58`; the kerma table has only a `Co58` row (or only the element row `Co`). Schedule:
  `[{"dt":"1 y","flux":1e14}, {"dt":"1 y","power_W_per_g":1e-3}]`.
  - Expected: step 1 builds Co-58 and step 2 normalizes on it.
  - Predicted: the run is refused at preparation.
- The documented consequence (pruning and mode decision use the start-composition estimate) covers the opposite case. It
  does not say that step N is rejected outright when the start material has no heating.
- Fix: do not error for steps after the first when the start heating is zero. Use a documented fallback estimate (for
  example the flux of the previous step, or the step's own `flux` if any). Document the refusal otherwise.

### F4  robustness  (confidence: high)
A decay-only spec that carries a `kerma` section fails with a misleading error.

- Files: `run.rs:826-839` and `run.rs:495` (`Absent => &[]`). `Spec::validate_decay_only` (`spec.rs:2341`) does not refuse
  `kerma`.
- Result: "kerma table boundaries_eV do not match the activation library boundaries".
- Reproduction: `decay_only_spec()` from the spec tests plus a `kerma` section.
  - Expected: a clear refusal ("a decay-only run has no activation library") or an all-zero kerma block.
  - Predicted: the boundary-mismatch message.
- Fix: refuse `kerma` in `validate_decay_only`, with the same wording as the other refusals.

### F5  robustness  (confidence: medium)
Handling of non-convergence and of refinement-time failures in `solve_power_step`.

- File: `power.rs:79-135`, wiring `run.rs:5625-5634`.
- At `max_substeps` the unconverged result is accepted and the run continues.
  - It is reported honestly: `power.converged: false` and ledger `kerma.power.steps_not_converged`. This is not silent.
  - There is no stderr warning and no certificate flag, and the exit status is 0.
  - Ledger entry 154 shows the default (tolerance 1e-3, 64 sub-intervals) is not enough for a strongly burning case:
    B-10 gives -1.52 % at 64 and converges only at 1024. Every strongly burning power step with defaults is therefore
    "unconverged" by design. Consider a first-order-aware default, or warn.
- Refinement can turn a completed coarse attempt into a hard error. Reproduction: one covered nuclide with a short
  half-life, and a long step. At n=1 the rate is positive, drift is 1, and the code refines to n=2. At the start
  of sub-interval 2, `heating_per_unit` underflows to 0, so the error is "no kerma heating at the start of
  sub-interval 2 of 2". The user is not told that a coarse attempt had run.
- The `rate <= 0 || !finite` test (`power.rs:98`) also fires for net-negative heating (a legal negative table row) and for
  NaN. The message blames the table coverage in all cases.
- Fix: a distinct message for negative or NaN heating, and mention the refinement level at which the heating
  vanished. Optionally a `kerma.require_converged` flag.

### F6  robustness / documentation  (confidence: medium)
Element rows apply the natural-element kerma to every isotope and every product, and count them as "covered".

- File: `crates/actinv-data/src/kerma.rs:142-147` (`lookup` falls back to `elements[za/1000]`), consumed by
  `kerma_terms_for`.
- A table with an `U` row gives U-235, U-237 and any U product the natural-uranium MT=301 value. A `Fe` row gives Fe-55
  the Fe-natural value. The docs say a nuclide row wins over the element row but not that products inherit it.
  `covered_atom_fraction` and the "lower bound on the heating" wording in results.md therefore overstate coverage quality.
- Isomers (`liso > 0`) do not fall back to the ground-state row. They take the element row or nothing.
- Fix: document it, and list per-step "covered by element row only" atoms in the kerma block, or add a table flag
  `element_rows_apply_to_products`.

### F7  documentation  (confidence: high)
The SF neutron source preferring MT=452 (total) includes delayed neutrons.

- Files: `fission.rs:360-370`, `specification.md` lines 220-223, `results.md` lines 163-165.
- In the ENDF/B-VIII.0 decay file, Cf-252 has MT=452 = 3.7676 (total), MT=456 = 3.7590 (prompt) and MT=455 = 8.6e-3
  (delayed). The reported "spontaneous-fission neutron source" is therefore 0.23 % above the prompt-neutron source
  at fission time for Cf-252. Other nuclides differ by their own delayed fraction.
- Docs say "total, preferred" but not that the source includes delayed neutrons emitted later than the fission, nor that it
  differs from the prompt multiplicity used in conventional SF source terms.
- Fix: state it, or offer a switch to prefer MT=456.

### F8  robustness  (confidence: high, severity low)
`resolve_inputs` and `check_files` skip the new input files.

- File: `crates/actinv-cli/src/workflow.rs:35` and `:68`. Only `fission_yields.files` is resolved and checked.
  `fission_yields.spontaneous.{files, nubar_endf, nubar_table}` are not resolved relative to the spec directory in the
  GUI path, and `actinv check`, with or without `--hashes`, does not open them. (`damage.table` and `self_shielding.*` are also
  omitted there, so this follows an existing gap.)
- Reproduction: a spec in directory D with `"nubar_table": {"path": "nubar.json", ...}`, loaded from a different cwd
  through the GUI, or run through `actinv check`.
  - Expected: resolved or checked.
  - Predicted: the GUI run fails later with "cannot open nubar.json"; `check` reports success.
- Fix: extend both functions to the spontaneous inputs.

### F9  documentation  (confidence: high)
"Every MT=18 row counts" is overstated.

- Files: `run.rs:7393`, `specification.md` lines 240-241, and `chain.rs:578-590`.
- In `reaction_rates`, a row whose target is not a chain state executes `continue` (recorded in
  `targets_absent_from_decay_library`) before the MT=18 branch. Its fissions are never added to `fission_rate_per_atom`,
  so they are missing from the count.
- Fix: reword ("every MT=18 row whose target is a decay-chain state"), or count those rows and book the products to leakage.

### F10  robustness  (confidence: medium, severity low)
Table coverage of the damage-energy rows is not checked.

- Files: `kerma.rs:215-221`, `power.rs:300-313`, `run.rs:857-876`.
- A `damage_energy_targets` row for a nuclide with no `targets` row is dropped in silence, because
  `per_atom_rates` returns `None` when there is no heating row. A heating-covered nuclide with no damage row adds
  nothing to `damage_energy_eV_per_g_s`, with no coverage figure for damage.
- `scripts/build_kerma_table.py` with `--damage-energy` also writes a partial damage set without a warning when
  some materials lack MT=444. Only 301 gaps are listed under `uncovered`.
- Fix: validate damage keys against the heating keys, and report a damage coverage fraction.

## Checked and found correct

Power solver (`power.rs`):
- The sub-interval multiplier is `target / heating(start)`, so power is exact at each sub-interval start.
- The drift is the absolute relative end power. The sign is dropped, which is fine for a tolerance test.
- The doubling is capped exactly at `max_substeps` (1, 2, 4, 5 for a cap of 5).
- `converged` and `refinement` are reported. A zero-length step gives drift 0 and returns the initial state.
- `zeroed_atoms` comes from the accepted attempt only.
- The mean multiplier is arithmetic, which equals time-weighted because the sub-intervals are equal.
- The analytic trace-mode depletion table (`exp(-total * dt)`) is exact with the mean multiplier, since the rates are linear
  in the multiplier.
- `max_substeps: 1` behaves as one start-of-step normalization.
- The unit conversion (`square_cm_per_barn()`, 1e-24 from the sealed quantity layer, times 1.602176634e-19) is right.
  `quantity.rs` is untouched.

Spec validation and wiring (`spec.rs`):
- `power_tolerance` must lie in (0,1), `max_substeps` in 1..=4096, and `top >= 1`.
- Power must be finite and positive. `power_W_cm3` needs a finite positive density, and zero density is rejected earlier.
  An overflowing quotient is caught by `solve_power_step`'s target check or by `FluxMultiplier::new`.
- `flux: 0` beside a power target is accepted. Power with `wall_loading_MW_m2` or with a nonzero `flux` is refused. The
  wire form omits `flux` only for wall-loading steps.
- Power plus `uncertainty`, `options.screen`, `options.fission`, `pathways` and step libraries is refused in
  `Spec::validate`, with a duplicate guard in `run.rs`.
- The trace-mode attribution (`options.outputs` "pathways") of power steps reproduces each sub-interval multiplier
  and length. The full `pathways` routes analysis is refused with power. This matches the docs and amendment A.
- Defaults are byte-identical: `kerma`, `options.fission` and `fission_yields.spontaneous` skip serialization when
  `None`, and the new `StepOut` fields are `skip_serializing_if` options.

Kerma table and script (`kerma.rs`, `build_kerma_table.py`):
- Strict JSON with `deny_unknown_fields`; canonical names; length and finiteness checks.
- Negative heating is allowed and named. Negative damage energy is refused. Boundaries must be ascending and are compared
  exactly to the library (mismatch is a safe refusal). The projectile must match.
- MT=301 and MT=444 are reported separately and never summed, so there is no double counting. 444 is a subset of the
  deposited recoil energy.
- The group fold is `sum K_g * phi_g * 1e-24`, in eV/s.
- The script's ENDF record field positions (HEAD, CONT, TAB1, LIST of MF=1/MT=451 in GENDF and PENDF) match the
  ENDF-6 layouts.
- The lin-lin lethargy collapse formula `a*ln(hi/lo) + b*(hi-lo)` over `ln(high/low)` is correct. The output keys match
  the Rust reader. `uncovered` lists materials without MT=301.

Fission count and burn-up (`fission_count.rs`):
- Heavy metal is Z >= 90 (Th and Pa included, as for thorium fuel). The mass is `count * AWR * m_n / N_A`.
- Zero heavy metal gives `None` for both burn-up figures (no division by zero), and a missing isotope mass gives
  `None` for MWd/kg with the isotope listed.
- The MWd/kg arithmetic (`fissions * MeV * 1e6 * eV_J / 8.64e10 J/MWd / kg_HM`) matches the unit test constant 37.0874.
- The 200 MeV default is applied linearly. The energy is validated finite and positive. `{"fission": {}}` parses to
  the default.
- The companion CRAM solve starts counters at zero from the pre-step state after any partition. It sees the same triplets
  as the inventory solve, including trace-mode unit edges and tracked reservoirs. The counters run before the uncertainty
  step overwrites `y`.
- Decay-only runs work with `options.fission` (empty induced edges, spontaneous only). Half-life overrides scale the SF
  rate through `decay_factors`.
- Missing nu-bar nuclides are summed into `uncovered_sf_decays_per_s_per_g`. They are never estimated, and the ledger names
  the populated ones. A repeated state accumulates.

Spontaneous-fission yields and nu-bar (`fission.rs`, `chain.rs`):
- The MT=454 sum is forced to 2 within 1e-6 at parse time. `effective(0.0)` takes the lowest tabulated energy without
  renormalizing. The ledger reports the per-parent sum.
- The multi-material tape reader groups contiguous MATs and skips materials without MT=454.
- The nu-bar reader:
  - takes the LNU=1 constant term or the lowest LNU=2 point;
  - takes the state from the MF=8/MT=457 HEAD L2, falling back to MF=1/MT=451 (matches the real file layout, checked
    against Cf-252 and Am-242m1);
  - rejects unsupported LNU;
  - rejects duplicates;
  - prefers MT=452 over MT=456.
- A product missing from the decay chain goes to leakage with a ledger entry carrying the yield. Only a branch that is the
  whole mode (position 0) is routed, and a parent without yields keeps the old leakage behaviour.
- A table nu-bar overrides the ENDF value. An empty table, a non-positive value and a missing citation are refused.

Not examined or not exercised: runtime behaviour of any of the above (no builds or runs were allowed); real ENDF/B `sfy`
files (none installed locally); whether the 1e-6 sum tolerance on MT=454 is met by the real `sfy` evaluations;
`reverse`, `study`, `mesh` refusals beyond confirming the calls exist.
