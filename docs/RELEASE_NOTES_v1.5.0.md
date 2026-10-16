# ACTINV v1.5.0 — release notes

ACTINV 1.5.0 carries the FISPACT-II parity program: a step summary with a depletion table, FISPACT-style
report tables, library inspection, a FISPACT-II deck importer, geometry self-shielding, power and
wall-loading normalization, spontaneous-fission and burn-up outputs, route-level pathways, and opt-in
regulatory tables. It also includes a hardening pass (P140) and the fixes to trace mode and pathway
attribution found along the way. Every addition landed under a registered protocol whose verdict is
recorded in `results/` and in the ledger. The parity features are opt-in and leave default results
unchanged; the exceptions are listed under "Results to re-check".

Upgrade the Python package with:

```bash
python -m pip install --upgrade actinv
```

Rust CLI users can install this exact version with:

```bash
cargo install --locked --force actinv-cli --version 1.5.0
```

## Results to re-check

Four items change default results: the ledger counts and pathway attribution from the P140 hardening
pass (ledger entries 168–171), which leave inventories unchanged, and the two trace-mode fixes (P133,
P134), which do change trace-mode results where they apply. The items after those affect only the
opt-in features named.

- **Ledger counts.** The ledger gains `unmodelled_decay_branches_to_leakage`: decay branches whose mode
  the chain does not model. It is emitted only when nonzero (3 with the shipped decay data, so every run
  on that data carries it) and is read as 0 when absent (P140 Amendment E). A fission that follows
  another transition (RTYP 2.6, beta-delayed fission) is counted there and is no longer counted as
  spontaneous fission of the parent, so `spontaneous_fission_branches_to_leakage` falls from 129 to 126
  in the default ledger. Anything that compares whole ledgers or certificates, or hashes a normalized
  result, will see the new key and the lower count; the inventories are byte-identical.
- **Pathway attribution (`pathways`, trace mode).** Production fed by the decay of a radioactive bulk
  nuclide is now included, and `pathway_closure` is 1 when a nuclide in the inventory has no chain at
  all. Results change only for materials with such a nuclide: Mo-100 double-beta decay is now the leading
  Ru-100 route in SS316; W-186 alpha decay feeds Hf-182 in tungsten (the shipped shielding example's
  closure falls from 0.98 to 5e-16); the natural-uranium closure falls from 1.0 to 4e-15.
- **Trace mode and radioactive initial material (P133).** Trace mode used to hold radioactive initial
  material constant. `auto` now also weighs bulk decay over the schedule and selects coupled when any
  initial nuclide decays by 1e-6 or more; explicit trace is refused there. On the base release, Co-60 at
  1e20 atoms/g decayed for one half-life reported 4.17e11 Bq/g where the correct value is 2.08e11.
  Restart or cooling runs from an activated inventory that did not set `coupled` should be rerun.
- **Trace-mode pathway attribution (P134).** Attribution ignored the step's flux multiplier and spectrum
  override, and lost production after a cooling gap; `pathway_closure` showed the error. It now uses the
  solve's own matrices, and power-driven steps report pathways over their realized sub-intervals.
- **Geometry self-shielding: resolved resonances are clipped to the evaluation's resolved range.** A
  resolved resonance is now used only inside its range's [EL, EH]; the others are reported as
  `resonances_outside_range`. For TENDL-2025 W-186 the resolved range ends at EH = 4341 eV although levels
  are listed to 17.3 keV: 108 of its 172 levels lie above EH and are dropped, so the 27 groups above
  4365 eV are no longer shielded. Their factors were as low as 0.71 at 0.1 cm and 0.94 at 0.01 cm; the 35
  groups below are bit-identical. For ENDF/B-VIII.1 W-186 the clip removes one group above 10 keV. **We
  have not verified whether FISPACT-II clips at EH in the same way**; no FISPACT-II foil factors were
  available, so the clip is a judgement, not a parity result. The TENDL-2025 W-186 foils at 0.01 cm and 0.1 cm
  were the only two cases in the P140 identity comparison (533 pairs) that differed beyond the ledger
  changes above; this clip explains them (ledger entry 170).
- **Photon outputs.** `photon.bremsstrahlung` used the decay record's average beta energy per decay of
  the parent as the energy of one electron per decay. It now uses `f` electrons per decay (the
  beta-minus branching) of energy `ER / f`; a nuclide with no beta-minus mode is a missing input.
  Partial beta emitters such as Cu-64 (about 2.6 times more energy) and K-40 change; pure beta-minus
  emitters do not. The point-source dose is `null` where decay-record gamma power has no photon spectrum,
  or where the distance, mass or air density is non-finite or out of range.
- **Bundled radiological tables.** The bundled tables were regenerated, so their SHA-256 values changed.
  A spec that declares a bundled hash must use the new one. A misspelled `radiological.responses`
  selector for a bundled table now fails at validation.
- **Depletion and sampling statistics.** In `summary.depletion`, trace fractions use a cumulative exponent
  (FNS Fe fractions moved by 2.6e-5 relative) and no uncertainty is invented for an excluded covariance
  block. One sample has no standard deviation, correlations and bootstrap errors need at least three
  samples, and `first_order` reads `not_comparable` when channels beyond `cross_section_mf33` are sampled.

Outside these items, default results do not move: before the P140 pass, the P122–P139 additions were
each checked byte-identical (apart from timing) against the previous release build on the recorded
default specs, and the P140 identity comparison found no other difference.

## What changes

### Step summary and depletion table (P122, P135)

- `summary` with `material.density_g_cm3`: the full inventory with grams, Bq, alpha/beta/gamma W and
  half-life; Ci, activity and heat excluding tritium; per-cm³ and absolute totals; an exact
  branching-weighted activity split by decay mode; an element table; dominant lists with cumulative
  percent and counts to 90 % and 99 %; MIND and NOSTABLE filters. An independent MT=457 parse made 32,158
  comparisons with no mismatches (P122).
- `summary.depletion` (FISPACT-II `DEPLETION`): per step and initial nuclide, atoms at start and end,
  step and cumulative depleted fraction, and the removal rate split into decay and reaction channels by
  MT, with shares that sum to one. With an MF=33 covariance each channel also reports the relative
  standard uncertainty of its one-group rate (checked against an independent collapse of the TENDL-2025
  covariance within 1e-6). The joint total is not evaluable on Fe, where every isotope has an uncovered
  reaction channel, and is omitted there (P135, PASS under Amendment A).

### Report tables, inspection and the deck importer (P131, P132, P137, P138)

- `actinv report RESULT.json`: FISPACT-style `TAB1` to `TAB4`, step totals, dominant and element tables,
  a `depletion` table and a plot-ready `timeseries` (`GRAPH`, `NUCGRAPH`), as csv, tsv, markdown or text.
  Values are exact `{:e}` with units in the headers; columns the result lacks are omitted and named,
  never filled in. All 32,781 CSV cells were rebuilt independently from the result JSON (P137).
- `actinv inspect {decay, xs, yields, library, groups}` queries decay data, group and one-group cross
  sections for a spectrum, independent and cumulative fission yields, and group boundaries without a run
  (P131). The command knows every named group structure and honours `rebin`.
- `actinv import-fispact DECK.i` converts a FISPACT-II input deck into an ACTINV spec with a sidecar that
  reports every keyword as mapped, approximated, noted or unsupported; `--strict` fails on unsupported.
  All 132 public FNS decks import, and 2,511 steps agree with the decks' own `.out` files (P132, P138).
  POWER, WALL, PARTITION, OVER, GRPCONVERT, DOSE 2, GROUP, BREMSSTRAHLUNG, PATH/ROUTES and ATWO are
  mapped. After P140 it also accepts three-letter abbreviations such as `TOL` (as used in the CoNDERC
  fission decks), NOHEAD, MCSEED and the legacy switches, and reports superseded `TIME` lines, `OVER`
  after a step and `DOSE 1` after `DOSE 2`; deeply nested or huge `PULSE` loops are refused before
  expansion. A deck that irradiates without a fluxes file is refused by the command-line importer.
- The report tables and the importer moved into `actinv-core` (`actinv_core::report`,
  `actinv_core::fispact_import`) and the data catalog into `actinv-data` (`actinv_data::catalog`) so the
  browser workbench can use them. `actinv report`, `actinv inspect` and `actinv import-fispact` output is
  byte-identical to before the move.

### Data and schedule controls (P124, P128, P130)

- All FISPACT-II neutron group structures, fispact-66 to fispact-1102 (to 1 GeV), and an opt-in
  high-energy residual-production splice for `build-library` (P130). The 13 structures equal their
  source boundary for boundary.
- In-run spectrum conversion between group structures (`spectrum.rebin`), first-wall-loading
  normalization (`wall_loading_MW_m2`), instantaneous elemental partition at step start, per-nuclide and
  per-MT cross-section, half-life and uncertainty `overrides`, and decay-only runs without a library
  (P124).
- Triton and helion projectiles, and schedule-level library switching (P128). Library switching is
  verified. Triton and helion are implemented and unit-tested but not exercised on real data, because no
  triton or helion sublibrary is installed.
- Schedule refusals added in P140: a rebin that lands no flux or reaches outside the spectrum, `kerma` in
  decay-only runs, damage rows without heating, an empty partition, and a 14 MeV wall-loading fallback
  that is refused for non-neutron projectiles. Power steps that do not converge warn on stderr.

### Geometry self-shielding (P129, P139)

- Foil, wire, sphere and cylinder geometries by the universal sigmoid curve, with per-group dilution
  overrides (P129). `geometry.density_g_cm3` defaults to the material density and a disagreement is
  refused.
- R-matrix-limited (LRF=7) evaluations such as ENDF/B-VIII and FENDL-3.2c are read, deriving the neutron
  and capture widths from the spin-group channel widths or reduced-width amplitudes (P139, PASS under
  Amendment A). For ENDF/B-VIII.1 W-186 the group factors equal an independent recomputation from MF=2
  exactly. Against TENDL-2025 on 53 common groups the factors differ by up to 5.4 % at 0.01 cm and 29 %
  at 0.1 cm (report-only).
- The resolved-range clip described under "Results to re-check".

### Power normalization (P126)

- A groupwise kerma table (`scripts/build_kerma_table.py`, from NJOY HEATR output), instantaneous nuclear
  heating per step, and `power_W_cm3` / `power_W_per_g` steps whose flux is renormalized on adaptive
  sub-intervals (P126, PASS under Amendment A; the initial G5 failure stays preserved).
- FISPACT-II normalizes once per step. `max_substeps: 1` reproduces that and is byte-identical to the
  flux-driven run at the start-of-step multiplier. On a B-10 replay the end-of-step power error is
  −39.3 % with one sub-interval, −1.52 % at 64 and −0.097 % at 4096. That comparison is report-only.

### Fission and burn-up (P125)

- Spontaneous-fission products from MT=454 yields, the cumulative fission count (the rate integral, a
  documented difference from FISPACT-II), burn-up (% FIMA and MWd/kg HM) and the spontaneous-fission
  neutron source (`options.fission`). Cf-252 spontaneous fissions agree with a 60-digit chain solve within
  2.8e-14. The spontaneous-fission yield routing was **data-limited** (no spontaneous-fission yield
  sublibrary is installed) and is covered by unit tests only.

### Pathways and routes (P127, P134)

- Multi-link routes with loops, and generic pathways, each integrated exactly through every schedule
  step, with a per-target closure of what the listed routes leave unexplained (P127). Every route for
  four Fe-activation targets was recomputed independently as a linear chain: 154 comparisons, all within
  1e-8. The pathway fixes are described under "Results to re-check".

### Sampling statistics (P136)

- `study.robustness.statistics` (FISPACT-II `SENSITIVITY` / `MCSAMPLE` output): per-response
  distribution (mean, std, skewness, exact order-statistic percentiles, histogram), input-output Pearson
  and Spearman tables by sampled parameter, a response-response correlation matrix, and bootstrap
  standard errors next to the first-order std. Absent by default, with existing records unchanged.
  Input correlations were recomputed only for the flux and composition parameters, because per-sample
  MF=33, decay and yield inputs are not in the record.

### Photon, dose and regulatory outputs (P123)

- The Steiner 22-group photon structure, a point-source dose rate in air at a distance
  (`photon.point_source`, FISPACT-II manual Eq. 63, no build-up), a separately reported thick-target
  bremsstrahlung component (`photon.bremsstrahlung`, with `electrons_per_decay`,
  `zero_lower_edge_energy_fraction` and `energy_closure` fields), and the A2 index.
- Opt-in `actinv report --tables regulatory`: the non-dose radiological responses (clearance and waste
  indices, A2 and activity limits) per step, with activity coverage. The default table set is unchanged.
- Opt-in bundled tables: IAEA SSR-6 A1/A2/exempt values from 49 CFR 173.435 and 173.436, and Euratom
  2013/59 Annex VII clearance and exemption levels. Unlimited A1/A2 rows, and progeny included in a
  parent's value, are covered with zero contribution through optional per-response `unlimited` and
  `covered_by_parent` lists, so `require_complete` works for activation inventories.
- **ICRP 119 dose coefficients are never bundled.** `scripts/regulatory/fetch_icrp119.py` builds the table
  locally from the source you supply.

### Other additions

- `actinv export-source alara`: writes one `.photonSrc` file per cell plus an index (P102; verified
  against `nucleide` 0.16.0's own ALARA photon-source reader).
- `actinv waste` and `actinv waste budget`: nominal U.S. Part 61 component classification and verified
  class-specific impurity limits (P105); `actinv waste bounds` for declared activity intervals (P107);
  `actinv waste composition` and `actinv waste composition solve` for declared composition ranges and
  native-run verification of the extremum compositions (P108, P110). These check selected points. They do
  not bound solver or nuclear-data error, infer probability, or qualify disposal acceptance. See the
  [waste handbook](guide/waste.md).
- `resolve_inputs` and `--files` checks cover every path-bearing field. Problems that name `catalog:`
  data resolve against `ACTINV_DATA_DIR` or `actinv-data` beside the problem in the desktop app's input
  resolution, and `check_files` names an unknown catalog id.

### Hardening pass (P140)

P140 reviewed seven module groups with reviewers who had not implemented them: 90 findings, three
rejected with reasons. Every accepted behavioural finding was reproduced by a regression test written to
assert the correct behaviour (107 failed on the unrepaired code), then repaired. The repaired candidate
passed the 151-case matrix (133 pass, 18 documented refusals), the handbook-fragment check (97 of 97
gating items) and the importer stress check (132 FNS, 2 fission and 6 pypact decks, 5,426 synthetic
decks, no panic), and CI was green on the pushed commit (ledger entries 168–171, **P140: PASS**). The
changes that affect results are listed under "Results to re-check"; the rest are refusals and
validation. Two P140 points are carried forward as open: the self-shielding clip above, and the
bremsstrahlung group-energy rule, which keeps FISPACT-II's midpoint rule (Eq. 67) and so overshoots the
energy in the group holding the endpoint energy; `energy_closure` now reports it.

## Where ACTINV stands against FISPACT-II

[COMPETITIVE_BENCHMARK.md](../COMPETITIVE_BENCHMARK.md) tracks 28 FISPACT-II capability rows and maps the
94 control keywords the importer recognizes: 61 parity, 5 ahead (`POWER`, `UNCERTAINTY`, `COVARIANCE`,
`PATH`, `ROUTES`), 8 partial, 4 absent and 16 not applicable. The statuses rest on the closed verdicts
cited above. Remaining gaps: `SPEK`, `XSTHRESHOLD`, `NOXRAY` and `IRON` are absent; `FISYIELD`,
`CUMFYLD`, `USEFISSION`, `NOFISS`, `FISCHOOSE`, `PROBTABLE`, `ALLDISPEN` and `ATDISPEN` are partial; and
several keywords exist in ACTINV but are not mapped by the importer. No speed comparison with FISPACT-II
is made or implied.

## Desktop and browser

Desktop `0.4.0-preview.1` ships the new 13-page interface on the 1.5.0 solver: Outputs, Charts, Tables,
Photons & dose and Pathways pages, a per-step Drive, an Import FISPACT-II deck command, an optional
material density, and controls for gas production and every uncertainty channel. The browser workbench gets the same pages and still never runs the solver. The look is
unchanged. See the [desktop 0.4.0-preview.1 release](releases/desktop-v0.4.0-preview.1.md) and
[Versions and releases](guide/releases.md).

## Known limitations

All limits in the [qualification boundary](guide/qualification.md) and
[data limitations](DATA_LIMITATIONS.md) still apply. In particular:

- Whether FISPACT-II clips resolved resonances at the evaluation's upper resonance energy is unknown
  (see "Results to re-check").
- Triton and helion projectiles, and the spontaneous-fission yield routing, are not exercised on real
  data.
- The `actinv waste-verify` command does not yet list `unmodelled_decay_branches_to_leakage` among its
  ledger reasons.

Each verdict cited above records its exact scope; the [changelog](../CHANGELOG.md) has the full list of
changes.
