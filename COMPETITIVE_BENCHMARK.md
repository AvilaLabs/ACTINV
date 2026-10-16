# ACTINV competitive benchmark

*Latest first: the FISPACT-II parity tracker (2026-10-15, live), the decay-aware TENDL-2023 FNS corpus (2026-09-26 — the
isomer-routing fix arm), then CB3 identical-data FISPACT comparison
(2026-09-20), then the CB2 refresh (2026-09-18). Frozen/superseded
records live in `docs/history/COMPETITIVE_BENCHMARK_ARCHIVE.md`.*

## FISPACT-II feature parity tracker — opened 2026-10-15 · live

Owner direction 2026-10-15: reach feature parity with FISPACT-II, and surpass it wherever a better
modern method exists. The reference is the FISPACT-II user manual — UKAEA-R(18)001 (Release 4.0)
and UKAEA-CCFE-RE(21)02 (Release 5.0/5.1) — read keyword by keyword (≈95 control keywords plus the
output chapter), each checked against ACTINV source on `2f3daa4`. Status: **absent** (no
equivalent), **partial**, **parity**, **ahead**. A row moves only on a closed phase verdict; a
row marked *ahead* names the specific respect in which ACTINV goes beyond FISPACT-II.

Already ahead before this tracker (unchanged): CRAM-16/48 with compensated-residual verified solves
(FISPACT-II: LSODES with user tolerances); full-covariance first-order bands plus correlated sampling,
value-of-information and measurement design (FISPACT-II: pathway sum-of-squares and per-pair Monte
Carlo); per-step spectra with uncertainty; mesh/R2S with OpenMC, MCNP, Serpent and ALARA source export;
reverse calculation, optimizer, impurity budgets, `decide`, assimilation and facility twins; US Part 61
waste classes; open source and open data.

| # | FISPACT-II capability (keywords) | ACTINV status | phase |
|---:|---|---|---|
| 1 | Per-nuclide inventory columns: grams, Bq, α/β/γ power, half-life, flags (`ATOMS`, `HALF`) | **parity** (`summary`: grams, Bq, α/β/γ W, half-life per nuclide; P122-PASS) | P122 |
| 2 | Step totals: Ci, α/β/γ Bq, activity and heat excluding tritium, total mass, per-cm³ with `DENSITY` | **parity** (α/β/γ split by branching weight of every decay mode; P122-PASS) | P122 |
| 3 | Inventory by element (`NOCOMP` to suppress) | **parity** (P122-PASS) | P122 |
| 4 | Dominant-nuclide lists per quantity (`SORTDOMINANT`, `NOSORT`) | **parity** (plus cumulative percent and counts to 90 %/99 %; P122-PASS) | P122 |
| 5 | Output filters `MIND`, `NOSTABLE` | **parity** (P122-PASS) | P122 |
| 6 | Point-source dose at distance (`DOSE 2`) | **parity** (`photon.point_source`, manual Eq. 63, no build-up; P123-PASS) | P123 |
| 7 | Bremsstrahlung dose correction (`BREMSSTRAHLUNG`) | **parity** (thick-target component reported separately; P123-PASS) | P123 |
| 8 | Approximate γ spectra for nuclides without spectral data (`SPEK`) | absent (unrepresented power reported) | P123 |
| 9 | Transport index / A2 (`ATWO`); bundled hazard, clearance and A2 data (`HAZARDS`, `CLEAR`) | **parity** (A2 index; opt-in A1/A2/exempt and Euratom clearance tables; ICRP 119 coefficients by local fetch script, not bundled; P123-PASS) | P123 |
| 10 | 22-group Steiner photon structure (`GROUP 1`), user γ groups (`READGG`) | **parity** (`steiner-22`, custom groups; P123-PASS) | P123 |
| 11 | Arbitrary input group structure with conversion (`GRPCONVERT`, `CNVTYPE`, `LIMGRP`) | **parity** (`spectrum.rebin` with the flux-conservation residual in the ledger; P124-PASS) | P124 |
| 12 | First-wall-loading normalization (`WALL`) | **parity** (P124-PASS) | P124 |
| 13 | Instantaneous elemental partition (`PARTITION`) | **parity** (plus continuous removal; P124-PASS) | P124 |
| 14 | Named cross-section / half-life overrides (`OVER ACROSS/ALAM/ADCROSS/ADLAM`) | **parity** (per-nuclide, per-MT `overrides`; P124-PASS) | P124 |
| 15 | Cooling-only runs from decay data alone (`GETDECAY` without `GETXS`) | **parity** (P124-PASS) | P124 |
| 16 | Spontaneous-fission products (`READSF`) | **parity** (MT=454 SF yields; uncovered branches listed; P125-PASS) | P125 |
| 17 | Fissioning-actinide selection, cumulative yields (`FISYIELD`, `CUMFYLD`, `USEFISSION`, `NOFISS`) | partial (actinide selection by the yield files supplied; cumulative yields inspectable via `actinv inspect yields`, P131; no keyword-level switch) | P125 |
| 18 | Number of fissions, actinide burn-up, SF neutron source | **parity** (fission count as the rate integral, a documented difference; burn-up; SF neutron source; P125-PASS) | P125 |
| 19 | Depletion rates with uncertainty (`DEPLETION`) | **parity** (`summary.depletion`: per-channel rates from the solver's own matrices, shares summing to one, MF=33 channel uncertainties; P135-PASS) | P135 |
| 20 | Power normalization by kerma (`POWER`); kerma, gas and dpa from library MTs | **ahead**: power held on adaptive sub-intervals within a step (B-10 end-of-step power error −39.3 % with FISPACT-II's single start-of-step normalization, −0.097 % adaptive); `max_substeps: 1` reproduces FISPACT-II (P126-PASS) | P126 |
| 21 | Pathways and routes: multi-link paths with loops, `PATH`, `ROUTES`, generic pathways, `LOOKAHEAD`, `PATHRESET` | **ahead**: routes integrated exactly through every step, with a per-target closure (total, Σ routes, unexplained residual) FISPACT-II does not report (P127-PASS; trace attribution repaired by P134; production fed by decay of radioactive bulk nuclides attributed by P140, closing natural U and W to ~1e-15) | P127, P140 |
| 22 | Triton and helion projectiles; projectile or library change mid-schedule (`PROJECTILE 6/7`, `GETXS` in inventory phase) | **parity** (step library switches verified; triton/helion implemented and unit-tested, not exercised on real data: no sublibrary installed; P128-PASS) | P128 |
| 23 | Geometry self-shielding (`SSFGEOMETRY`), per-group dilution override (`SSFDILUTION`) | **parity**: foil, wire, sphere, cylinder by the universal sigmoid curve, per-group dilution overrides (P129-PASS); also from R-matrix-limited (LRF=7) evaluations such as ENDF/B-VIII and FENDL-3.2c (P139-PASS); resolved levels are clipped to the evaluation's resolved range [EL, EH] (P140), and whether FISPACT-II clips there is not verified | P129, P139, P140 |
| 24 | High-energy data to 1 GeV, 1102 groups, spallation splice (`USESPALLATION`, `USEXSEXTRA`) | **parity**: every FISPACT-II group structure (66–1102, to 1 GeV), opt-in residual-production splice (P130-PASS) | P130 |
| 25 | Library inspection (`PRINTLIB`, `EXTRACTXS`) | **parity** (`actinv inspect`: decay, group and spectrum-collapsed cross sections, yields, group bounds, as JSON or tables, without a run; P131-PASS) | P131 |
| 26 | Tabular and plot output (`TAB1`–`TAB4`, `GRAPH`, `NUCGRAPH`) | **parity** (`actinv report`: exact CSV/TSV, six-digit markdown/text, plot-ready `timeseries`; P137-PASS) | P132 |
| 27 | FISPACT-II input decks | **parity** (`actinv import-fispact`: all 132 FNS decks import, per-keyword status sidecar, `--strict`; parity keywords mapped; P132-PASS, P138-PASS) | P132, P138 |
| 28 | Monte Carlo sensitivity distributions and correlation tables (`SENSITIVITY`, `MCSAMPLE`) | **parity** (`study.robustness.statistics`: distributions, exact percentiles, Pearson/Spearman tables, bootstrap errors; P136-PASS) | P136 |

Order: Wave 1 (P122–P124) → Wave 2 (P125–P127) → Wave 3 (P128–P130) → Wave 4 (P131–P132), then a
closing scorecard mapping every keyword. Each phase has its own protocol, checker and verdict.

## FISPACT-II keyword scorecard — 2026-10-15

Closing map of every control keyword the `actinv import-fispact` keyword table recognizes (94) to its ACTINV
equivalent. Statuses follow the parity tracker above and are not raised past its rows; a keyword the tracker does not
name takes the status of the capability it belongs to. *Not applicable* marks a keyword that exists only because of
FISPACT-II's own architecture (deck syntax, log and print switches, library preparation, solver tolerances). "Not
mapped by import-fispact" means the capability exists in ACTINV but the importer reports the keyword `unsupported` and
you add the spec section by hand. Sources: `crates/actinv-core/src/fispact_import.rs`,
`docs/guide/fispact-migration.md`, `docs/guide/specification.md`, `docs/guide/cli.md`, ledger entries 146–166.

| keyword | ACTINV equivalent | status | note |
|---|---|---|---|
| *Control and initialization* | | | |
| `CLOBBER` | none | not applicable | ACTINV writes only the files you name |
| `END` | end of the JSON spec | not applicable | no deck terminator in a JSON spec |
| `FISPACT` | `title` | **parity** | title line imported |
| `JSON` | result JSON | not applicable | results are always JSON |
| `LOGLEVEL` | none | not applicable | run-log verbosity |
| `MONITOR` | none | not applicable | run-log verbosity |
| `SAVELINES` | none | not applicable | library-preparation printing switch |
| `TOLERANCE` | CRAM, `cram_order` 16 or 48 | not applicable | CRAM has no solver tolerance |
| `PROJECTILE` | `projectile` (neutron, deuteron, proton, alpha, gamma, triton, helion) | **parity** | triton and helion not exercised on real data; P128-PASS |
| *Material and irradiation* | | | |
| `MASS` | `material.mass_g`, basis `wt_percent` | **parity** | imported |
| `FUEL` | `material`, basis `atoms_per_g` | **parity** | imported; mass taken from mass numbers |
| `DENSITY` | `material.density_g_cm3` | **parity** | per-cm³ totals; imported only when POWER needs it |
| `FLUX` | `spectrum.total`, step `flux` multiplier | **parity** | imported |
| `ZERO` | zero-flux step | **parity** | imported as flux 0 |
| `TIME` | `schedule` durations | **parity** | each run becomes one step |
| `PULSE` | explicit `schedule` steps | **parity** | loops expanded on import |
| `ENDPULSE` | explicit `schedule` steps | **parity** | closes a PULSE loop on import |
| `POWER` | `power_W_cm3` with `kerma` | **ahead** | adaptive sub-intervals; import needs a kerma table the deck lacks |
| `WALL` | `wall_loading_MW_m2` | **parity** | imported |
| `PARTITION` | `schedule[].partition` | **parity** | plus continuous removal |
| `OVER` | `overrides` (cross section, half-life) | **parity** | ADCROSS and ADLAM not imported |
| `GRPCONVERT` | `spectrum.rebin` | **parity** | imported for FISPACT-II standard structures |
| `CNVTYPE` | `spectrum.rebin.convention` | **parity** | imported |
| `LIMGRP` | `spectrum.rebin` energy limits | **parity** | imported as eV limits |
| `SSFGEOMETRY` | `self_shielding.geometry` | **parity** | foil, wire, sphere, cylinder; not mapped by import-fispact |
| `SSFCHOOSE` | `self_shielding.geometry.choose` | **parity** | not mapped by import-fispact |
| `SSFFUEL` | `geometry.density_g_cm3` with the material | **parity** | density replaces atoms per area; not mapped by import-fispact |
| `SSFMASS` | `geometry.density_g_cm3` with the material | **parity** | density replaces atoms per area; not mapped by import-fispact |
| `SSFDILUTION` | `self_shielding.dilution_overrides` | **parity** | table mode only; not mapped by import-fispact |
| `PROBTABLE` | `self_shielding.table` (Bondarenko) | partial | tables built from evaluations; not mapped by import-fispact |
| `ALLDISPEN` | `damage` (NRT dpa) | partial | needs a user-built MT=444 table; not mapped by import-fispact |
| `ATDISPEN` | `damage` (NRT dpa) | partial | needs a user-built MT=444 table; not mapped by import-fispact |
| *Output* | | | |
| `ATOMS` | `summary`, `actinv report` | **parity** | per-nuclide atoms, grams, Bq per step |
| `STEP` | step results | **parity** | every step reported in full |
| `SPECTRUM` | `photon` source per group | **parity** | per-step decay-photon spectra |
| `HALF` | `summary` half-life column | **parity** | in every result step |
| `NOCOMP` | `summary.elements` | **parity** | element table is optional |
| `NOSORT` | `summary.dominant_top` | **parity** | dominant lists optional |
| `SORTDOMINANT` | `summary.dominant_top` | **parity** | plus cumulative percent and counts to 90 % and 99 % |
| `MIND` | `summary.min_atoms` | **parity** | imported |
| `NOSTABLE` | `summary.include_stable` | **parity** | imported |
| `DOSE` | `photon.point_source` | **parity** | importer maps DOSE 2 only |
| `BREMSSTRAHLUNG` | `photon.bremsstrahlung` | **parity** | reported separately from decay-photon dose |
| `GROUP` | `photon.group_structure`, `steiner-22` | **parity** | importer maps GROUP 0 and 1 |
| `SPEK` | none | absent | unrepresented power is reported, not estimated |
| `READGG` | custom `photon.group_structure` | **parity** | not mapped by import-fispact |
| `ATWO` | `radiological` A2 index | **parity** | bundled 49 CFR 173 A2 table |
| `HAZARDS` | `radiological`, bundled regulatory tables | **parity** | ICRP 119 coefficients by local script |
| `CLEAR` | `radiological` clearance tables, `actinv clearance` | **parity** | Euratom tables opt-in |
| `TAB1` | `actinv report --tables tab1` | **parity** | exact CSV or TSV |
| `TAB2` | `actinv report --tables tab2` | **parity** | activity, heat, alpha/beta/gamma split |
| `TAB3` | `actinv report --tables tab3` | **parity** | needs a `radiological` dose response |
| `TAB4` | `actinv report --tables tab4` | **parity** | decay-photon source per group |
| `NOT1` | none | not applicable | suppresses a tab file ACTINV does not write |
| `NOT2` | none | not applicable | suppresses a tab file ACTINV does not write |
| `NOT3` | none | not applicable | suppresses a tab file ACTINV does not write |
| `NOT4` | none | not applicable | suppresses a tab file ACTINV does not write |
| `CULTAB` | none | not applicable | output selector; result JSON holds every step |
| `GRAPH` | `actinv report --tables timeseries` | **parity** | plot-ready rows |
| `NUCGRAPH` | `actinv report --tables timeseries` | **parity** | `--nuclides` columns |
| `SPLIT` | none | not applicable | output file format selector |
| *Uncertainty and pathways* | | | |
| `UNCERTAINTY` | `uncertainty` (MF=33) | **ahead** | full-covariance bands; levels 1 and 2 imported |
| `NOERROR` | no `uncertainty` block | **parity** | imported |
| `UNCTYPE` | `uncertainty.channels` | **parity** | not mapped by import-fispact |
| `ERROR` | `overrides` relative uncertainties | **parity** | not mapped by import-fispact |
| `COVARIANCE` | `uncertainty.covariance` sidecar | **ahead** | not mapped by import-fispact |
| `SENSITIVITY` | `study.robustness.statistics` | **parity** | P136; not mapped by import-fispact |
| `MCSAMPLE` | `study.robustness.statistics` | **parity** | P136; not mapped by import-fispact |
| `DEPLETION` | `summary.depletion` | **parity** | P135; not mapped by import-fispact |
| `PATH` | `pathways` | **ahead** | targets imported; numeric arguments not interpreted |
| `ROUTES` | `pathways` | **ahead** | exact route integration with closure |
| `RESULT` | `pathways.targets`, `rank_by` | **parity** | not mapped by import-fispact |
| `LOOKAHEAD` | `pathways.max_depth`, `path_floor` | **parity** | pathways, row 21 |
| `PATHRESET` | `pathways` floors | **parity** | pathways, row 21 |
| `GENERIC` | `pathways` | **parity** | generic pathways grouped by edge-type sequence |
| `INDEXPATH` | `pathways` route listing | **parity** | not named in tracker row 21 |
| *Library and data* | | | |
| `GETXS` | `library`, `spectrum.structure`, step `library` | **parity** | mid-schedule GETXS not mapped (needs second library) |
| `GETDECAY` | `decay`; decay-only runs | **parity** | cooling-only runs need no library |
| `LIBVERSION` | none | not applicable | library-preparation printing switch |
| `COMPRESSXS` | none | not applicable | library-preparation switch |
| `FULLXS` | none | not applicable | library-preparation printing switch |
| `PRINTLIB` | `actinv inspect` | **parity** | decay, group and collapsed data without a run |
| `EXTRACTXS` | `actinv inspect` | **parity** | cross sections as JSON or tables |
| `USESPALLATION` | `build-library --spallation` | **parity** | splice table user-supplied; not mapped by import-fispact |
| `USEXSEXTRA` | `build-library --spallation`, group structures to 1 GeV | **parity** | tracker row 24; not mapped by import-fispact |
| `XSTHRESHOLD` | none | absent | no cross-section threshold switch |
| `NOXRAY` | none | absent | no decay-data X-ray switch |
| `IRON` | none | absent | no iron background removal |
| `READSF` | `fission_yields.spontaneous` | **parity** | MT=454 SF yields; not mapped by import-fispact |
| `FISYIELD` | `fission_yields.files` | partial | selection by yield files supplied; no keyword switch |
| `CUMFYLD` | `actinv inspect yields` | partial | cumulative yields inspectable; no keyword switch |
| `USEFISSION` | `fission_yields.files` | partial | selection by yield files supplied; no keyword switch |
| `NOFISS` | omit `fission_yields` | partial | selection by yield files supplied; no keyword switch |
| `FISCHOOSE` | `fission_yields.files` | partial | selection by yield files supplied; no keyword switch |

Counts over the 94 keywords: 61 parity, 5 ahead, 8 partial, 4 absent, 16 not applicable. *Ahead* is claimed only where a closed verdict or the "Already ahead" paragraph shows it: `POWER`, `UNCERTAINTY`, `COVARIANCE`, `PATH`, `ROUTES` (power renormalized within the step, P126; full-covariance uncertainty; exact route integration with closure, P127). `RESULT` and `INDEXPATH` are counted *parity* on judgement: the `pathways` section covers target selection and route listing, though tracker row 21 does not name them.

Remaining gaps (every keyword with status partial or absent):

- `SPEK` (absent): approximate γ spectra for nuclides without spectral data; needs an estimator for the unrepresented power now reported.
- `XSTHRESHOLD` (absent): no cross-section threshold switch; needs a spec option, specified from the manual.
- `NOXRAY` (absent): no decay-data switch of that name; needs a decay-data selection option, specified from the manual.
- `IRON` (absent): fictitious-matrix iron background removal is not available; needs a design.
- `FISYIELD`, `USEFISSION`, `NOFISS`, `FISCHOOSE` (partial): actinides are chosen by the yield files supplied; needs a keyword-level selection among them and an importer mapping.
- `CUMFYLD` (partial): cumulative yields are inspectable with `actinv inspect yields`; needs a keyword-level switch.
- `PROBTABLE` (partial): self-shielding tables come from evaluations; needs an importer mapping, since the deck names no table.
- `ALLDISPEN`, `ATDISPEN` (partial): `damage` needs a user-built MT=444 table and none ships; needs a shipped table and an importer mapping.
- Importer mapping gaps, status unchanged: `DEPLETION`, `SENSITIVITY`, `MCSAMPLE`, `SSFGEOMETRY`, `SSFCHOOSE`, `SSFFUEL`, `SSFMASS`, `SSFDILUTION`, `READGG`, `READSF`, `USESPALLATION`, `USEXSEXTRA`, `UNCTYPE`, `ERROR`, `COVARIANCE`, `RESULT`, `ADCROSS`/`ADLAM` under `OVER`, and a `GETXS` after the first `TIME` need the importer to build the spec section or name its hash-pinned data.

## Capability scorecard — 2026-09-30 refresh (ACTINV column only)

This refresh updates the **ACTINV column only**. The competitor columns (ALARA, OpenMC,
FISPACT-II, SCALE/ORIGEN) below are copied unchanged from the CB1 capability scorecard
(`docs/history/COMPETITIVE_BENCHMARK_ARCHIVE.md`, dated **2026-08-28, ACTINV 1.0.0**). They are
the CB1 survey, not re-surveyed here, and are not upgraded, downgraded, or supplemented from
outside knowledge in this refresh. The legend and `V*`/`V**` footnote markers are CB1's own and
are reused exactly; new rows below use fresh footnote markers (`†‡§¶`) scoped to this section.

`V` = verified complete for the named axis; `P` = meaningful partial support; `A` = confirmed
absent; `?` = not verified from sufficient current official evidence. "Unverified" is not a hidden
"no." CB1 per-cell evidence is in
[`results/cb1_capabilities.json`](results/cb1_capabilities.json).

| capability axis | ACTINV | ALARA | OpenMC | FISPACT-II | SCALE/ORIGEN |
|---|:---:|:---:|:---:|:---:|:---:|
| licence/access model established | V | V | V | V | V |
| install path established | V | V | V | V | V |
| projectiles and energy domain | P | P | V | V | P |
| finite-dilution self-shielding | V* | ? | V | V | V |
| irradiation schedules | V | V | V | V | V |
| fission yields | V | ? | V | V | V |
| covariance/uncertainty | P | ? | P | V | P |
| activation responses | V | V | P | V | V |
| transport coupling | P | P | V | P | V |
| CLI and programmatic API | V | P | V | V | P |
| deterministic input provenance | V | ? | ? | ? | ? |
| spatial/mesh operation | V | V | V | ? | P |
| continuous feed/removal | V** | ? | V | ? | V |
| reverse calculation | V** | V | ? | ? | ? |
| damage observables | V** | ? | V | V | ? |
| documented operating-system routes | V | P | V | V | V |
| compile-time physical unit types | A | ? | ? | ? | ? |
| hydrogen/helium gas production (produced + inventory appm) | V† | *not surveyed* | *not surveyed* | *not surveyed* | *not surveyed* |
| transport-tally statistical error as a flux uncertainty channel | V‡ | *not surveyed* | *not surveyed* | *not surveyed* | *not surveyed* |
| lean R2S mesh output (dotted `cell_result_fields`) | V§ | *not surveyed* | *not surveyed* | *not surveyed* | *not surveyed* |
| impurity budgets for clearance | V¶ | *not surveyed* | *not surveyed* | *not surveyed* | *not surveyed* |
| photonuclear (incident-gamma) activation | P‖ | *not surveyed* | *not surveyed* | *not surveyed* | *not surveyed* |

None of the 17 CB1-era axis cells (rows above the gas-production row) changed tier since CB1: no
protocol/verdict evidence from 2026-08-28 to 2026-09-30 moved any of them from one of `V`/`P`/`A`/`?`
to another. The five rows below them are new axes CB1 did not score (the photonuclear row was added 2026-10-01); the competitor cells there are
marked `not surveyed` rather than guessed, as CB1 did not evaluate ALARA, OpenMC, FISPACT-II, or
SCALE/ORIGEN on these specific axes.

`V*` and `V**` keep their CB1 meaning (Bondarenko self-shielding only; the scoped P23 surfaces) —
see the archive for their full text.

`†` **Hydrogen/helium gas production.** `options.gas` (off by default; refused together with
`uncertainty`, and for any non-neutron projectile) routes light ejectiles of every covered MT, plus
decay alphas and protons, into the chain as ordinary nuclides (H1, H2, H3, He3, He4); each step
reports produced `appm` and FISPACT-II's `inventory_appm` convention (includes initial content).
Fission ejectiles are uncovered and named in the ledger. **P92** first compared ACTINV against
FISPACT-II/TENDL-2017 on the 132 CB3 FNS experiments and **FAILed** gate G5 (H1 was compared on the
wrong convention — produced appm vs. FISPACT-II's inventory appm). **P95**, the gate-G5 successor
that added `inventory_appm`, **PASSed**: 403/403 gated pairs within ±10 %
(`results/p95_verdict.json`, `"pass": true`). Ledger Entry 71; `CHANGELOG.md` Unreleased;
`docs/guide/qualification.md`.

`‡` **Transport-tally statistical error as a flux uncertainty channel.**
`uncertainty.channels: ["flux"]` with a per-group `spectrum.relative_error` (or a mesh cell's own
flux-file error) propagates each group's declared statistical error to first order
(sensitivity d*R*/d ln φ_g, diagonal); it can run flux-only with no covariance sidecar, or alongside
MF=33. It excludes systematic transport error (model, geometry, transport nuclear data). **P93**
**FAILed** (a real ledger-serialization defect plus two checker defects in its finite-difference
gate). **P96**, a fresh-spec successor, **FAILed** too (it carried P93's defect forward). **P97**,
which added the one missing field, **PASSed** on the merged candidate: 2097/2097 finite-difference
comparisons within tolerance, sampling-variance ratios 0.993–0.996 on the P32 cube
(`results/p97_verdict.json`, `"pass": true`). Ledger Entry 72; `CHANGELOG.md` Unreleased;
`docs/guide/specification.md`, `docs/guide/qualification.md`, `docs/guide/workflows.md`.

`§` **Lean R2S mesh output.** Mesh `cell_result_fields` accepts dotted `steps.<field>[.<key>...]`
entries, assembling each cell's text from the selected `RunResult`/`StepOut` fields through
explicit accessors instead of serializing the whole result; the OpenMC R2S adapter's default is now
`["mode", "ledger", "steps.photon_source.groups"]`. Output without dotted entries is byte-unchanged.
**P90 PASSed**: bitwise agreement with the full record on two profiles, 1.26× faster at 1 thread
(gate margin 1.259× against a 1.25× pass bound — thin, reported as measured), output 4.6 % of the
full record's bytes (`results/p90_verdict.json`, `"pass": true`). Ledger Entry 73; `CHANGELOG.md`
Unreleased; `docs/guide/specification.md`.

`¶` **Impurity budgets for clearance.** `actinv budget` reports, from one coupled/reach solve per
element (composition superposition, P75b), the clearance index (matrix-only and at-spec), the spec
margin factor *k*, per-impurity gradients and contributions, joint and sole-impurity limits, and
uncovered nuclides; every emitted limit is re-solved at its composition before being reported
(`--no-verify` skips that check). **P79 PASSed** (G0–G2 all pass; EUROFER97 re-solved
independently to max relative deviation 2.6e-15; SS316LN not exercised because the matrix alone
already exceeds the clearance limit, so no edge exists) and **Amendment A PASSed**
(adds sole-impurity limits; 16 points, max relative deviation 2.6e-15) — `results/p79_verdict.json`
and `results/p79a_verdict.json` (both all-gates-pass; these verdicts record gate status per field
rather than one top-level `pass` boolean). The Python binding reached the CLI's verbatim output
only on its second attempt: **P87 FAILed** (CLI and Python documents differed in one field), and
**P89 PASSed** (`results/p89_verdict.json`, G0–G3 all pass). Known limitation: an impurity whose
activation products are absent from the bundled IAEA clearance table reports no clearance-index
response rather than an invented value (e.g., Ag-108m). Ledger Entries 59, 68, 69; `CHANGELOG.md`
Unreleased; `docs/guide/workflows.md`, `docs/BUDGET.md`.

`‖` **Photonuclear (incident-gamma) activation.** `build-library --projectile gamma` builds
162-group libraries from the TENDL `g` sublibrary, and `projectile: "gamma"` runs them with
photon-labelled flux. Scored `P`, not `V`: photofission is a total only (product yields fail
closed), there is no temperature treatment, and no gamma library is published through
`actinv data fetch`. The CB1 "projectiles and energy domain" cell stays `P` (no triton or helion).
**P94 FAILed** (the candidate could not build the TENDL-2017 inputs of its FISPACT-II gate) and **P98 FAILed** (162/323 cross-code values within 2e-3,
against 95 %). **P100 FAILed** (a checker crash, a checker left-limit error, and a false premise
about the reference's printed digits). **P101 PASSed** (`results/p101_verdict.json`, `"pass":
true`). Against FISPACT-II's processed TENDL-2017 `gxs-162` records on 8 nuclides, 95/95 non-MT5
one-group values agree within 2e-3 (max 1.3e-6), all below 30 MeV. Above 30 MeV TENDL-2017
carries only MT5, where FISPACT-II's processing follows a different rule. That rule is reproduced
within one unit of FISPACT-II's last printed digit on all 1,002 sections, and ACTINV's exact
integration matches an independent code to 1.4e-13. Ledger Entries 75, 76; `CHANGELOG.md`
Unreleased; `docs/guide/specification.md`, `docs/guide/qualification.md`.

A capability existing does not establish its accuracy for every application or material; see the
per-row evidence above and the limits named in each cited verdict before relying on a `V`.

## Decay-aware TENDL-2023 FNS corpus — 2026-09-26 · latest

Isomer routing was the last structural accuracy defect: the legacy Python
builder rank-compressed `LFS→LISO`, feeding the wrong isomer whenever an
evaluation's level index didn't coincide with the decay library's LISO
(TENDL-2023 Ta-182M was the confirmed case). The Rust builder's
decay-aware path resolves physical LIS/ELIS against ENDF/B-VIII.0 with
JEFF-3.3 fallback. The rebuilt library
(`actinv_tendl2023_fns_decay_709g.npz` — 519 targets incl. 266 isomer
evaluations, zero build failures) was scored across the FNS corpus
(`results/fns_tendl_decay/`):

| pooled geometric-mean C/E | value |
|---|---:|
| **ACTINV · TENDL-2023 decay-aware** | **1.097** |
| ACTINV · TENDL-2023 rank-mapped (old arm) | 1.249 |
| FISPACT-II · TENDL-2017 (frozen reference) | 1.244 |

Per-material geomean highlights: **In 29.2 → 1.9** (the old arm's
catastrophic misroute, fixed); W 1.84 → 2.45 and Zn 0.90 → 0.63 regressed
— TENDL-2023-vs-2017 cross-section differences, now physically routed;
Sn ~1.33 and Ta ~0.85 unchanged (their residual is decay-data/evaluation
version, not routing — TENDL-2023's Ta-181 lacks the 15.8-min Ta-182m
capture channel entirely). Remaining tail is decay-data disagreement
between libraries (e.g. Ta-182m deposited energy 506 vs ~16 keV/decay),
not closable while arms use different decay libraries.
See `docs/defects/` for the upstream evaluation defect reports surfaced
while routing was verified.

## Verified error bars — decay-aware corpus vs measured FNS data · 2026-09-27

`results/d2_calibration.json` — the `unmodeled_relative` discrepancy
field fit on 70 FNS experiments (1117 points) of the decay-aware
TENDL-2023 corpus and *checked against 62 experiments it never saw*
(982 holdout points). This is the claim no competitor publishes: bands
whose stated coverage is verified on held-out measured data.

- Pooled unmodeled-error scale **u = 0.437** (the sealed pre-fix corpus
  needed u\* = 0.898 — the isomer-routing fix nearly halved the honest
  residual scale).
- Held-out coverage at ±1σ: **79.6%** (and only u = 0.306 is needed for
  honest 68% coverage); at ±2σ: **93.1%** (u = 0.536 for a true 95%).
- Per-material tails: La 1.37, Na 1.21, Dy 1.04, W 0.91 — the residual
  channel-defect list, quantified.
- Consumable: `actinv-unmodeled-table-1` artifact feeds
  `uncertainty.unmodeled_table` (P72) so runs inherit the calibrated
  field per material family without hand-tuned scalars.

## Identical-data OpenMC comparison — ENDF/B-VIII.1 · 21 experiments · 424 points

`results/FNS_ENDF8_HEADTOHEAD.md` — ACTINV and OpenMC 0.15.3 depleted
on the *same* ENDF-8 files (pointwise XS matching OpenMC's 0 K data,
collapsed to FISPACT-709), scored against FNS decay-heat measurements:

| arm | gm C/E | median \|ln C/E\| | p90 \|ln C/E\| | expts all-pts within 30% |
|---|---:|---:|---:|---:|
| **ACTINV / ENDF-8** | **0.788** | **0.131** | **0.970** | 10/21 |
| OpenMC / ENDF-8 | 0.685 | 0.181 | 1.640 | 10/21 |
| ACTINV / TENDL-2025 | 1.089 | 0.141 | 0.587 | 12/21 |
| ACTINV / TENDL-2017 | 1.261 | 0.086 | 0.504 | 15/21 |
| FISPACT / TENDL-2017 | 1.256 | 0.085 | 0.490 | 15/21 |

On identical ENDF-8 data ACTINV beats OpenMC on every pooled metric —
gm closer to 1 (0.788 vs 0.685), median |ln C/E| 0.131 vs 0.181, and the
tail is the separation: p90 0.970 vs **1.640** (OpenMC's worst points are
~4 orders off). The mechanism is **isomer channels**: OpenMC's depletion
chain is ground-state-only where it matters — Ta-180m's ~41% isomer
branch (OpenMC overshoots Ta 1.46 vs ACTINV 0.89), Y-89's (n,n′) channel
(absent entirely — OpenMC 0.28 gm on a mis-shaped curve), W-185m1 (97% of
first-cool heat; ACTINV's extra channel gets closer at 1.76 vs 0.28). The
OpenMC arm is neutron-induced depletion of its own transport fluxes —
the comparison isolates the activation solve, not transport quality.
ACTINV wall time for the corpus: 88 s for 132 experiments (~0.7 s/case).

## CB3 identical-data FISPACT comparison — 2026-09-20 · ACTINV 1.1.2

The FNS leg was re-run with the nuclear-data confound removed:
**ACTINV 1.1.2 on TENDL-2017** vs the frozen published
FISPACT-II 4.0/TENDL-2017 references (`results/cb3_fns_tendl2017.json`,
runner `controls/cb3_fns.py`, artifact
`neutron.n.p10.npz sha256 21815670…747d` built from 1,072 TENDL-2017
ENDF-6 files — the union of all experiment targets plus every nuclide
printed in any FISPACT inventory across the 132 outputs — with
`--decay` state resolution against ENDF/B-VIII.0
`endf-b-viii-0_decay.dat sha256 6f04cf00…8ddb` **plus
`--decay-fallback` against JEFF-3.3**
`jeff-3-3_decay.dat sha256 850b8b7f…d123` for cross-library level-scheme
disagreements, index `sha256 1f1124ab…62a06`).

| metric | ACTINV/TENDL-2017 | FISPACT-II/TENDL-2017 | ACTINV/TENDL-2025 (CB2) |
|---|---:|---:|---:|
| median pooled \|log C/E\| | **0.1030** | 0.1053 | 0.139 |
| p90 pooled \|log C/E\| | 0.689 | **0.685** | **0.664** |
| experiments all-points within 30% | **71** | 69 | 59 |
| pooled geometric mean C/E | **1.0605** (closer to 1) | 1.0636 | **1.031** |
| median experiment geometric mean C/E | **1.0047** | 1.0085 | — |

**Verdict: ACTINV leads FISPACT on identical data, and the margin grew.**
On the same TENDL-2017 cross sections ACTINV is closer to the
measurements on the median point error (0.1030 vs 0.1053), the
within-30% experiment count (**71 vs 69 — and both split experiments
now fall ACTINV's way**: Sb-2000 and Ta-1996-5min pass for ACTINV and
fail for FISPACT), the pooled bias (1.0605 vs 1.0636), and the median
experiment bias (1.0047 vs 1.0085). FISPACT keeps a statistically
marginal tail edge (p90 0.685 vs 0.689).

All six machine checks pass (132/132 experiments, certificate inputs
match, positive pairs, references present, time alignment ≤2%,
identities match). Two findings:

1. **Solver+processing validated.** Comparing ACTINV to FISPACT *on the
   same cross sections*: 131/132 experiments agree within 30% — only
   Al-1996's late-time points diverge >2× (both codes sit ~4 orders
   below the measurement: 2.4e-9 vs 2.6e-8 µW/g vs 4.4e-5 measured —
   numerically divergent on a point both catastrophically miss anyway).
   Same XS in → same heat out; the collapse, chain assembly and CRAM
   solve agree with FISPACT's pipeline across the board.

2. **Five defect classes were measured and fixed.** The first
   TENDL-2017 artifact ran 19 experiments beyond 2× of FISPACT, all
   ACTINV-low. Class one was isomer-product mapping: TENDL-2017's MF=8
   excitation fields carry ~10–100 eV rounding and sentinel-grade
   values, so strict excitation matching rejected ~4,300 isomer
   channels; the LFS→LIS label fallback (FISPACT's own semantics)
   recovered 1,347. Class two was coverage scope: the original
   715-target build omitted isomer-variant files (In-116N, Eu-152N,
   Ir-191N…) and whole nuclides FISPACT actually printed — the true
   FISPACT inventory is 788 nuclides, of which 764 TENDL-2017 files
   were missing. Class three was product-only isomers: 226 distinct
   product states had no cross-section catalog home and were leaked,
   but an isomer product needs only *decay* data to feed its daughter —
   e.g. Sc-50m (produced by Ti-50(n,p) and four other reactions)
   supplies 57% of FISPACT's early Sc-50 inventory. Class four was
   cross-library state numbering: a TENDL file's declared LISO is
   file-order noise while decay-library LISO follows evaluator level
   index, so Ta-182's 15.8-min isomer (file LISO=1, decay LISO=2) was
   routed to the 0.283-s state and evaporated. Class five was
   cross-library *level-scheme disagreement*: evaluations assign
   different level indices and energies to the same physical isomer —
   ENDF/B-VIII calls Sb-120m 151 keV/LIS 4 while JEFF-3.3 and TENDL
   call it 200 keV/LIS 6, and five more isomers (Cu-68m, Cs-135m,
   Dy-147m, Hf-178n, Au-189m) disagree by 0.2–0.5 keV — so primary-only
   matching left ~15 physical states homeless and dumped their
   production onto the ground state (Sb-2000 carried a uniform 1.35×
   overprediction vs FISPACT for exactly this reason). The builder now
   resolves against the primary table, then a `--decay-fallback` table,
   then an unambiguous loose-ELIS tier (±250 eV or 0.1%, sole candidate
   only); unmatched states whose file ordinal would alias an occupied
   decay state still get a synthetic ordinal (Tb-156N → LISO 10002)
   rather than silently decaying as the wrong nuclide (DATA_TRAPS #1).
   Ta-2000 max|log| 0.263→0.250 and now Sb-2000 0.541→0.256 — both
   remaining split experiments pass for ACTINV and fail for FISPACT, a
   2–0 within-30% margin in ACTINV's favour.

Residual confounds, honestly: ACTINV's decay data remain
ENDF/B-VIII-primary + JEFF-3.3-fallback ENDF-6 files while FISPACT used
its condensed `tendl17_decay12`; 10 source files were evicted as genuine
data defects (non-monotonic TAB1 grids, total widths below channel
sums — each ledgered in the artifact index); and 24 nuclides in the
FISPACT reachable space have no TENDL-2017 evaluation at all.

**Decay-library sensitivity (measured 2026-09-21).** The same arm was
re-scored with primacy swapped — JEFF-3.3 primary + ENDF/B-VIII
fallback (`results/cb3_fns_jeff_primary.json`): median pooled
|log C/E| moves 0.1030 → 0.1058, within-30% experiments 71 → 69, and
pooled geometric mean C/E 1.0605 → 1.0687 — i.e. under JEFF-primary the
lead over FISPACT (0.1053, 69) disappears to parity. The two deciding
experiments are the same isomer-resolution cases fixed under ENDF
primacy: Sb-2000 and Ta-1996-5min both flip back to failing (max|log|
0.256→0.265 and 0.231→0.310). The sensitivity is bidirectional — Yb-2000
worsens +0.453 while Bi-1996-7hour improves −0.107 — so the honest claim
is that the identical-data margin sits *inside* decay-evaluation
uncertainty, not above it.

---

## CB2 refresh — 2026-09-18 · ACTINV 1.1.2
The measurable CB1 legs were re-run on the current binary and data. The
frozen CB1 record below is unchanged; this section records what moved.

**FNS 132-experiment comparison (`results/cb2_fns.json`).** Re-executed
all 132 experiments on actinv 1.1.2 against the same pinned TENDL-2025
library, decay files and frozen FISPACT-II/TENDL-2017 published
references. **Every score is numerically identical to CB1** — the solver
is bit-reproducible across versions on identical inputs, and the
product-plus-data comparison conclusion stands: FISPACT-II/TENDL-2017
keeps the better typical point error and more experiments wholly within
30% (69 vs 59); ACTINV/TENDL-2025 keeps the slightly better p90 point
error (0.6637 vs 0.6846) and pooled bias closer to one (1.0313 vs
1.0636). This remains a product-plus-data comparison, not solver
validation. Wall time for the 132 fresh runs measured 152 s (CB1
recorded 298 s — different host load, not a speed claim).

**Identical-data ALARA comparison — superseded.** CB1's single
processed-data pulse case (4.12e-8 relative) is superseded by the P40
912-case census on identical FENDL-3.2c inputs: median class-cleaned
total-activity agreement of 1.06%, with all residual divergence
classified into named representation floors and data-availability gaps
(`results/verdict_p40.json`). That is the current evidence for the
identical-data claim; the 4.12e-8 figure stays as the frozen CB1
record.

**CRAM-48 kernel benchmark (`results/cb2_performance.json`).** Re-run
against the same OpenMC 0.15.3 at the same Python-call boundary, both
sides pinned to one thread:

| operator states | CB1 ratio (openmc/actinv) | CB2 ratio |
|---:|---:|---:|
| 2 | 186.8× | 182× |
| 32 | 20.3× | 13.8× |
| 256 | 3.96× | 2.64× |
| 1024 | 2.83× | **1.28×** |
| 2048 | — | 0.98× |
| 4096 | — | 1.83× |

(2048/4096 added 2026-09-20 — the TENDL-2025 library builds ~1700-state
production operators, so coverage extends past the realistic top end.
The 2048 row reads parity under host contention; 4096 confirms no
second cliff.)

ACTINV 1.1.2 now leads at every tested size, including 1024 states —
while still running the compensated-residual verification that OpenMC's
`spsolve` path does not perform at all. The earlier 0.83× crossover was
diagnosed by gate instrumentation (`refinement_stats`): at 1024 states
every pole was flagged by `backward_ok`, but the violating rows were all
at *subnormal* scale (denominators ~1e-318, below `f64::MIN_POSITIVE`),
where a relative backward-error bound has no representable accuracy and
its 1e-6-scaled floor underflows. SuperLU's unrefined solve fails the
identical bound on the identical rows. The gate now exempts rows whose
entire scale is subnormal — they cannot hide a material component — and
applies the same exemption to the refinement loop's per-row convergence
floor (subnormal deltas could never satisfy it, previously driving
2–5 wasted iterations). The state dynamic-range check is unchanged and
still mandatory: the trace-daughter regression test demonstrated that
a residual-only gate is unsafe, and that check still engages refinement
on every mixed-scale solve. `scale_shift` was also rewritten to build
the shifted matrix directly in CSC form instead of a triplet
round-trip (~6× faster). Net effect at 1024 states: 24 poles flagged,
each converging in exactly one iteration; CB1 numerical output is
bit-identical to the pre-change record. The whole-product speed
comparison is unchanged in kind — a kernel result, not a product-speed
verdict.

**Not re-measured.** Install/first-use timings remain the v1.0.0-era
CB1 record, archived at `docs/history/COMPETITIVE_BENCHMARK_ARCHIVE.md`;
nothing in them is claimed to have improved or regressed. The
capability-survey legs' competitor columns (ALARA, OpenMC, FISPACT-II,
SCALE/ORIGEN) also remain that dated CB1 survey and were not
re-surveyed; the ACTINV column has a dated refresh in
[Capability scorecard — 2026-09-30 refresh](#capability-scorecard--2026-09-30-refresh-actinv-column-only)
above.
