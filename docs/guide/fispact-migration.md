# Move a FISPACT-II deck to ACTINV

`actinv import-fispact` reads a FISPACT-II input deck (`.i`) and writes an ACTINV [problem specification](specification.md). It converts the problem description: the material, the neutron (or charged-particle) spectrum, the irradiation and cooling schedule, and the few output settings that have an ACTINV counterpart. It does not read FISPACT-II's nuclear data. The activation library and decay data of the converted problem are ACTINV's, so results differ from a FISPACT-II run by the difference between the two data sets as well as by the solvers.

```text
actinv import-fispact DECK.i OUT.json [--fluxes FLUXES] [--files FILES]
                      [--library LIB.npz] [--decay DECAY]
                      [--covariance COV.npz] [--strict]
```

The command writes `OUT.json` (the problem) and `OUT.import.json` (the import report, below). Four further options supply data that a deck does not name; each file is hashed into the spec, and a deck that does not use an option imports exactly as it would without it:

| Option | Used by | Fills |
| --- | --- | --- |
| `--fission-yields FILE...` | `USEFISSION` | `fission_yields.files`: ENDF neutron-induced yield files, one per parent. The parent of each file is read from its own header. |
| `--resonance FILE...` | `SSFGEOMETRY` | `self_shielding.geometry.resonance_files`: ENDF evaluations with resolved resonance parameters |
| `--self-shielding-table FILE` | `PROBTABLE`, `SSFDILUTION` | `self_shielding.table`: a Bondarenko table |
| `--damage TABLE` (optional) | `ALLDISPEN`, `ATDISPEN` | `damage.table`: a damage table, for example from `actinv build-damage --method kinematic`. Without it a neutron deck uses the catalog's `tendl-2025-neutron-damage`. A table that records its displacement energies (an `actinv-damage-table-2` file) is compared with the deck's. |

A file that cannot be read stops the import. An option the deck never uses is listed under `options_unused` in the report. Run and check the converted problem in the usual way:

```text
actinv import-fispact TENDL-2017_1996exp_5min.i fe_5min.json
actinv validate fe_5min.json
actinv run fe_5min.json fe_5min_result.json
```

The [desktop](desktop.md#import-a-fispact-ii-deck) imports a deck from the toolbar (**Import FISPACT-II deck…**) and shows the same per-keyword report as `actinv import-fispact`. A deck that irradiates, imported without a fluxes file, gets a flat placeholder spectrum flagged as a required input. Replace it before running, because the result means nothing until you do.

## Coming from FISPACT-II

This map is by capability, not by keyword; the [keyword translation](#keyword-translation) below says what the importer does with each deck line. **Supported** means ACTINV has an equivalent that does the same job. **Supported differently** means the job gets done, but the input, the output or the numerical treatment is not FISPACT-II's. **Not supported** means ACTINV has nothing equivalent. **Not needed** marks a keyword that exists only for FISPACT-II's own printing, files or solver. A capability can be supported in ACTINV and still be absent from the importer, in which case you add the spec section by hand; the last column says so. "Imported" means the importer maps the deck keywords.

| FISPACT-II capability (keywords) | ACTINV equivalent | Status | Importer and notes |
| --- | --- | --- | --- |
| Material and irradiation: `MASS`, `FUEL`, `DENSITY`, `FLUX`, `ZERO`, `TIME`, `PULSE`, `PROJECTILE` | `material`, `spectrum.total`, `schedule`, `projectile`; the **Material** and **Irradiation & cooling** pages | Supported | Imported. Gamma, triton and helion need `--library`; triton and helion have not been run on real data |
| Wall loading and power: `WALL`, `POWER` | step `wall_loading_MW_m2`, `power_W_cm3` or `power_W_per_g`; a `kerma` table; the step **Drive** on **Irradiation & cooling** | Supported differently | Imported, but a power step needs a kerma table the deck cannot supply. ACTINV holds the power on sub-intervals within a step; `max_substeps: 1` reproduces FISPACT-II's single normalization |
| Elemental partition: `PARTITION` | `schedule[].partition` | Supported | Imported. Applied at the start of the next step |
| Group conversion: `GRPCONVERT`, `CNVTYPE`, `LIMGRP` | `spectrum.rebin` | Supported | Imported for FISPACT-II standard structures |
| Cross-section and half-life changes: `OVER` with `ACROSS`, `ALAM` | `overrides` | Supported differently | Imported. `ADCROSS`, `ADLAM` (error factors) are not imported; overrides are not step-scoped |
| Change of library mid-run: `GETXS` after the first `TIME` | step `library` | Supported | Not imported: it needs the second library's path |
| Cooling from decay data alone: `GETDECAY` without `GETXS` | decay-only run | Supported | Imported |
| Library choice: `GETXS`, `GETDECAY`, `ARRAY`, `COLLAPSE` | `library`, `decay`; `actinv data fetch` | Supported differently | ACTINV uses its own libraries, not the deck's. Library-preparation switches (`LIBVERSION`, `COMPRESSXS`, `FULLXS`, `SAVELINES`) have no counterpart and are noted |
| Inventory listing, totals, elements, dominant nuclides: `ATOMS`, `HALF`, `NOCOMP`, `NOSORT`, `SORTDOMINANT`, `MIND`, `NOSTABLE` | `summary` section; **Step summary** on **Outputs** | Supported | Imported (`MIND`, `NOSTABLE`, `SORTDOMINANT`) |
| Tab and plot files: `TAB1` to `TAB4`, `GRAPH`, `NUCGRAPH` | `actinv report`; the **Tables** page | Supported differently | Noted. ACTINV writes one result JSON and renders tables from it afterwards |
| Depletion rates: `DEPLETION` | `summary.depletion`; `actinv report --tables depletion` | Supported | Not imported (reported as unsupported); add `summary.depletion` by hand |
| Point-source dose and bremsstrahlung: `DOSE 2`, `BREMSSTRAHLUNG` | `photon.point_source`, `photon.bremsstrahlung`; **Photons & dose** | Supported differently | Imported. The dose is an air kerma rate, and bremsstrahlung is a separate block; both need the photon response described [below](#keywords-that-need-more-than-the-deck) |
| Photon groups: `GROUP`, `READGG` | `photon.group_structure` (`fispact-24`, `steiner-22`, or custom) | Supported | `GROUP` is imported. `READGG` is imported as a custom structure when the files file has a `ggbins` entry whose file holds the group count and then that many plus one ascending energies in eV, as the FISPACT-II manual describes; a missing, unreadable or malformed `ggbins` file is reported as unsupported with the reason |
| Approximate gamma spectra for nuclides without spectral data: `SPEK` | `photon.missing_spectra: "estimate"` | Supported differently | Imported as approximated. ACTINV's estimate is an exponential shape set by each nuclide's mean energy per decay mode, not FISPACT-II's spectrum, and the result carries certified lower and upper dose bounds |
| Transport index and hazards: `ATWO`, `HAZARDS`, `CLEAR` | `radiological` with `bundled:` tables; `actinv report --tables regulatory`; **Regulatory tables** on **Outputs** | Supported differently | `ATWO` is imported. `HAZARDS` and `CLEAR` are noted: ACTINV chooses no regulation for you. 49 CFR 173.435/173.436 and Euratom 2013/59 Annex VII are bundled; ICRP 119 coefficients are not and come from a local script. These are the regulations' numbers, not FISPACT-II's data files |
| Geometry self-shielding: `SSFGEOMETRY`, `SSFCHOOSE`, `SSFFUEL`, `SSFMASS` | `self_shielding.geometry` | Supported | Not imported (reported as unsupported); the section needs hash-pinned ENDF resonance files the deck does not name |
| Dilution override: `SSFDILUTION`, `PROBTABLE` | `self_shielding.dilution_overrides`, `self_shielding.table` | Supported differently | Not imported. ACTINV needs a hash-pinned Bondarenko table; tables are built from evaluations with `actinv build-shielding` |
| Uncertainty: `UNCERTAINTY`, `NOERROR` | `uncertainty` (MF=33 covariance) | Supported differently | Imported. The default covariance is the TENDL-2025 MF=33 data, not the deck's library. ACTINV propagates full-covariance bands rather than FISPACT-II's pathway sums |
| Uncertainty controls: `COVARIANCE`, `UNCTYPE`, `ERROR` | `uncertainty.covariance`, `uncertainty.channels`, `overrides` | Supported differently | Not imported |
| Monte Carlo sampling and sensitivity: `MCSAMPLE`, `SENSITIVITY` | `study.robustness.statistics` | Supported differently | Not imported (reported as unsupported). The statistics are those of an ACTINV robustness study, not a FISPACT-II sampling run |
| Pathways: `PATH`, `ROUTES`, `LOOKAHEAD`, `PATHRESET`, `GENERIC`, `INDEXPATH`, `RESULT` | `pathways`; **Pathways** page | Supported differently | `PATH` and `ROUTES` are imported as targets with default thresholds; the others are noted. The result reports a closure for each target |
| Fission: `READSF`, `FISYIELD`, `CUMFYLD`, `USEFISSION`, `NOFISS`, `FISCHOOSE` | `fission_yields.files`, `fission_yields.spontaneous`, `options.fission`, `actinv inspect yields` | Supported differently | Not imported. Which actinides fission is set by the yield files you supply, with no keyword switch |
| Displacement damage: `ALLDISPEN`, `ATDISPEN` | `damage` | Supported differently | Imported against `--damage TABLE`, or against the catalog table `tendl-2025-neutron-damage` for a neutron deck without it; a per-element `ATDISPEN` value overrides `ALLDISPEN` in any order. They are mapped when the table was built at the deck's energies (or records none, as a table 1 does) and reported as unsupported otherwise, with the `actinv build-damage --method kinematic --displacement-energy` command that builds a matching table. An element with no energy in the deck takes the table's energy, else FISPACT-II's default (25 eV outside its table 8). With `--damage` and neither keyword the section uses those defaults, as FISPACT-II computes displacement rates by default. With the catalog table installed, the energies are compared at import. If it is not installed, the deck's energies are written as given, an element the deck omits takes the table's energy at run time (reported as approximated), and the run refuses a mismatch. A deck for another projectile needs `--damage` and is unsupported without it, naming the option |
| High-energy data: `USESPALLATION`, `USEXSEXTRA` | group structures to 1 GeV; `build-library` splice option | Supported differently | Not imported. You build the library with the splice table |
| Library inspection: `PRINTLIB`, `EXTRACTXS` | `actinv inspect` | Supported | Noted. Works without a run |
| X-ray switch: `NOXRAY` | `photon.exclude_xrays` | Supported | Imported |
| Cross-section threshold switch, iron background removal: `XSTHRESHOLD`, `IRON` | none | Not supported | `XSTHRESHOLD` is noted (ACTINV propagates the full covariance); `IRON` is reported as unsupported |
| Run control and print switches: `CLOBBER`, `LOGLEVEL`, `MONITOR`, `TOLERANCE`, `NOT1` to `NOT4`, `CULTAB`, `SPLIT`, `JSON` | none | Not needed | Noted. ACTINV uses CRAM, which has no solver tolerance, and writes only the files you name |

After the import, edit the JSON problem rather than the deck: ACTINV runs the problem, not the deck. A converted problem does not reproduce FISPACT-II's numbers by construction, because the data differ. See [Understand the competitive benchmark](benchmarks.md) for the recorded FNS comparison on common data.

### Import in the desktop

The desktop's **Import FISPACT-II deck…** (Ctrl/Cmd+I) runs the same importer and shows the same per-keyword report. It differs from the CLI in one case: a deck that irradiates but comes without a fluxes file converts with a flat placeholder spectrum, flagged as a required input, so you can open the problem and fill in the spectrum. The CLI refuses such a deck. Replace the placeholder before running. See [Import a FISPACT-II deck](desktop.md#import-a-fispact-ii-deck).

## What is read

| Input | Used for |
| --- | --- |
| `DECK.i` | material, flux, schedule, projectile, and the keywords listed below |
| `--fluxes FLUXES` | the group spectrum: a FISPACT-II `fluxes` file (values highest energy first, first-wall loading, title) |
| `--files FILES` | a FISPACT-II `files` file. Its `fluxes` entry names the spectrum when `--fluxes` is absent. Without `--files`, `DECK_files` and then `files` beside the deck are tried. |
| `--library`, `--decay` | the ACTINV activation library and decay data for the problem |
| `--covariance` | an MF=33 covariance sidecar paired with `--library`, used when the deck asks for uncertainties |

The `xs_endf`, `dk_endf` and other data entries of a files file are listed in the report for reference and never read. Without `--library` and `--decay` the problem uses the catalog references that `actinv new` uses (`catalog:tendl-2025-neutron-709g`, `catalog:endfb-viii-0-decay` with the JEFF-3.3 fallback, and for charged particles the matching 162-group library); install them with `actinv data fetch`. A gamma-projectile deck needs `--library`.

The group count of a `fluxes` file is taken from the file itself. ACTINV reads 709-group neutron spectra and 162-group charged-particle spectra directly; a `GETXS 1 N` line must agree with the file. A file in another FISPACT-II standard structure (66, 69, 100, 172, 175, 211, 315, 351, 586, 616 or 1102 groups) is imported when the deck says `GRPCONVERT`: the importer takes the file's boundaries from that structure and writes a `spectrum.rebin` section, so ACTINV converts the spectrum to the library's groups inside the run. A file with any other group count stops the import; convert it to 709 groups first.

## How the deck is read

The importer follows the FISPACT-II input rules:

- Keywords are free format and case-insensitive. Only the leading characters matter: a word of three or more characters that begins exactly one keyword is that keyword (`TOL`, `HAZA`, `UNCERT`, `TOLER` are `TOLERANCE`, `HAZARDS`, `UNCERTAINTY`, `TOLERANCE`), and an ambiguous abbreviation is unknown. The deprecated spellings `NOHEAD`/`NOHEADER`, `COLLAPSE` (`GETXS 1`), `ARRAY` (`GETDECAY 0`) and `AINPUT` (`GETXS 0` and `GETDECAY 0`), and the legacy switches `MCSEED`, `CONV`, `DOMINANT`, `EAFVERSION`, `ENFA`, `LEVEL`, `LINA`, `LOOPS`, `NEWFILE`, `SEQNUMBER`, `SEQUENTIAL` and `TAPA` are known. A word that matches nothing is reported as a separate `unsupported` entry ("unknown to this importer"), including when it follows a keyword that takes a list, such as `PATH` or `OVER`.
- The deck is read as bytes. A UTF-8 byte-order mark is ignored, lines end at `\n`, `\r\n` or a bare `\r`, and bytes that are not UTF-8 (a Latin-1 micro sign in a comment, for example) are replaced rather than stopping the import; the report records `deck.lossy_utf8`.
- `<< ... >>` comments may appear anywhere, including inside a line, and may span lines. A line whose first character is `*` is text (the title after `FISPACT`, and after `END`); `<<` has no meaning inside it.
- The library phase ends at `FISPACT`, the initial conditions at the first `TIME`, and the deck at `END`. Nothing after `END` is read.
- `TIME t [SECS|MINS|HOURS|DAYS|YEARS]` sets an interval; the next `ATOMS`, `STEP` or `SPECTRUM` runs it. Each run becomes one step of the ACTINV schedule, at the flux in force when the output keyword is read. A word after the number that is not one of these units is an unknown keyword, and the interval is in seconds. A bare number is seconds (the UKAEA tutorial deck says so).
  - **Assumption.** That `TIME` only sets up an interval and the output keyword runs it is the reading of the UKAEA FISPACT-II tutorial decks, whose comments say that `ATOMS` "tells FISPACT-II to solve the rate equations". It is not confirmed against a FISPACT-II output for a deck with two `TIME` lines before one `ATOMS`; the pypact reader treats every `TIME` as a step. The importer therefore keeps the deferral but never hides its cost: a `TIME` that a later `TIME` replaces, or that no output keyword follows, is `unsupported` (so `--strict` stops), and its entry gives the lost interval and flux. Put an `ATOMS` after each `TIME` to make the intent unambiguous.
- `FLUX` sets the flux for the steps that follow. The first positive value becomes `spectrum.total`, and each step's `flux` multiplier is its flux divided by that value, so a step at `FLUX 0` is a cooling step. `ZERO` also sets the flux to zero, as in FISPACT-II. The ACTINV schedule carries elapsed time through `ZERO`; FISPACT-II restarts the elapsed time printed after it (the cooling time restarts at zero, as in the pypact reference `test31.out`). The interval durations agree; absolute times in the two outputs differ by the irradiation time. FISPACT-II is documented to allow `ZERO` once and to refuse irradiation after it; the importer does neither check and simply sets the flux to zero (both statements are unverified here).
- `PULSE n ... ENDPULSE` runs its body `n` times in total (`PULSE 3` gives three executions, not four), expanded into explicit steps (at most 100000). Loops nest to 64 levels, and a deck whose loops expand to more than two million keyword executions is refused before anything runs. Keywords in the body are reported once, at their first execution. The body of `PULSE 0` does not run, but its keywords are still listed (marked as skipped), so an unsupported keyword there is visible to `--strict`. This reading of `n` has not been checked against the FISPACT-II manual.

## Keyword translation

| FISPACT-II | ACTINV |
| --- | --- |
| `MASS kg n` and `ELEMENT percent` lines | `material` with `mass_g` and basis `wt_percent` |
| `FUEL n` and `NUCLIDE atoms` lines | `material` with basis `atoms_per_g` and explicit nuclide keys; the mass is taken from mass numbers (the atom counts are exact) |
| `FLUX`, `ZERO`, `TIME`, `ATOMS`, `STEP`, `SPECTRUM`, `PULSE` | `spectrum.total` and `schedule` |
| `PROJECTILE n` | `projectile` (1 neutron, 2 deuteron, 3 proton, 4 alpha, 5 gamma, 6 triton, 7 helion). Gamma, triton and helion have no catalog library: pass `--library`. |
| `FISPACT` title line | `title` |
| `MIND`, `NOSTABLE`, `SORTDOMINANT` | the `summary` block: `min_atoms`, `include_stable`, `dominant_top` |
| `UNCERTAINTY 1` or `2` | `uncertainty` with an MF=33 covariance (see below) |
| `UNCTYPE 1`, `2`, `3` | `uncertainty.channels` (`cross_section_mf33`, `decay_constants`, both); type 2 is `approximated` |
| `SENSITIVITY SIGMA` | `uncertainty.voi`, `approximated` |
| `ERROR` | per-reaction `overrides` of kind `cross_section_uncertainty`, `approximated` |
| `USEFISSION` with `--fission-yields` | `fission_yields.files` |
| `NOFISS`, `CUMFYLD`, `FISYIELD` | no yield files; `fission_yields.yields: "cumulative"`; `fission_yields.parents` |
| `FISCHOOSE` | `pathways.fission_parents` |
| `SSFGEOMETRY` (and `SSFCHOOSE`) with `--resonance` | `self_shielding.geometry` |
| `PROBTABLE`, `SSFDILUTION` with `--self-shielding-table` | `self_shielding.table` (`approximated`), `self_shielding.dilution_overrides` |
| `NOERROR` | no `uncertainty` block |
| `GETXS`, `GETDECAY` | the library and decay references; a `GETXS 1 N` group count is checked against the spectrum |
| `POWER p n mt…` | schedule `power_W_cm3`, with `material.density_g_cm3` from `DENSITY` (see below) |
| `WALL w` | schedule `wall_loading_MW_m2`, with `spectrum.wall_loading_MW_m2` taken from the fluxes file (see below) |
| `PARTITION` element/fraction pairs | schedule `partition` on the next step |
| `OVER nuclide` with `ACROSS`, `ALAM` | `overrides` entries of kind `cross_section` and `half_life` |
| `GRPCONVERT`, `CNVTYPE`, `LIMGRP` | `spectrum.rebin` with `convention`, `emin_eV`, `emax_eV` |
| `DOSE 2 distance` | `photon.point_source` |
| `GROUP 0`, `GROUP 1` | `photon.group_structure` `fispact-24`, `steiner-22` |
| `BREMSSTRAHLUNG` nuclides | `photon.bremsstrahlung.nuclides` |
| `PATH`, `ROUTES` | a `pathways` section, the nuclides named as `targets` |
| `ATWO` | `radiological` with the bundled `us-49cfr173-a2` table |
| `HALF`, `HAZARDS`, `CLEAR`, `GRAPH`, `NUCGRAPH`, `TAB1` to `TAB4`, `NOT1` to `NOT4`, `TOLERANCE`, `PRINTLIB`, `MONITOR`, `CLOBBER` and similar | recorded as `noted`: they select FISPACT-II printing, files or solver controls and do not change the calculated physics. The tables and plot data come from `actinv report` ([below](#tables-and-plot-data-from-a-result)). `HAZARDS` and `CLEAR` name a regulation, which ACTINV does not choose for you: add a `radiological` section with the table you want (see [Bundled regulatory tables](specification.md#bundled-regulatory-tables)). |

A deferred setting that appears twice (`UNCERTAINTY`, `GRPCONVERT`, `CNVTYPE`, `LIMGRP`) takes its last value, and the earlier entries say they were superseded.

`UNCERTAINTY` needs a covariance sidecar. With `--covariance FILE` it is hashed and used. With the default neutron library the catalog covariance sidecar is used and the entry is `approximated`, because its uncertainties are the TENDL-2025 MF=33 data, not those the deck's FISPACT-II library carried. With another `--library` and no `--covariance` the entry is `unsupported`. Level 3 (pathways only) produces no band.

`DENSITY` is written to `material.density_g_cm3` only when a `POWER` step needs it. Otherwise it is recorded in the report and left out, since it only adds per-cm3 summary totals and the converted problem stays identical to a hand-written one; add it yourself if you want them. The `summary` block carries the summary options; a build without them rejects a spec that contains it.

### Keywords that need more than the deck

- **`POWER`.** `POWER p n mt_1 … mt_n` gives a power density `p` in W/cc normalized on the kerma of the listed MT numbers (usually `1 301`). It is written as `power_W_cm3` on every step that follows until the next `FLUX`, `WALL`, `ZERO` or `POWER`, and the deck's `DENSITY` becomes `material.density_g_cm3`. The list is read as `n` followed by exactly `n` MT numbers; ACTINV normalizes on the kerma table's total (MT=301), and another list, a missing list or words after the list are named in the entry. A power-driven step needs a `kerma` section with a hash-pinned `actinv-kerma-table-1` file, which no deck provides, so the entry is `approximated`: the importer writes the spec without it, lists it (and the density, when the deck has no `DENSITY`) under `required_additions`, and says so in its summary line. The spec does not validate until you add them. Build the table with `scripts/build_kerma_table.py`.
- **`WALL`.** The first-wall loading of the fluxes file becomes `spectrum.wall_loading_MW_m2`, and a step's multiplier is `wall` divided by it, as in section 4.91 of the FISPACT-II manual. Because the multiplier then applies to the whole file spectrum, `spectrum.total` is the sum of the file's group values and any `FLUX` in the same deck is expressed against it. A file that records no positive loading falls back to ACTINV's 4.44e13 n/cm2/s per MW/m2 equivalence, and the entry is `approximated`.
- **`PARTITION`.** Element/fraction pairs, with or without a leading pair count, give the fraction of each element's atoms kept. FISPACT-II applies the split when it reads the keyword, ACTINV at the start of the next step, so a `PARTITION` with no step after it is `unsupported`.
- **`OVER`.** Each `ACROSS`, `ALAM`, `ADCROSS` and `ADLAM` is reported under its own name; `OVER` itself is the worst of them. `ACROSS target daughter value` becomes a `cross_section` override on the ENDF reaction that takes the target to the daughter, found from the change in Z and A: (n,g), (n,2n), (n,3n), (n,p) and (n,a) only, for neutron projectiles. FISPACT-II sets the channel to that daughter; ACTINV sets the whole collapsed reaction and keeps its product branching, so the entry is `approximated`. A daughter that is an isomer, a change that two reactions share (for example (n,d) and (n,np)) and a negative value are `unsupported`. `ALAM value [unit]` becomes a `half_life` override. `ADCROSS` and `ADLAM` are `unsupported`: ACTINV takes a relative standard uncertainty, FISPACT-II an error factor, and the spec does not define the conversion. Cross-section overrides cannot be combined with `uncertainty`, so the importer drops the `uncertainty` block and marks `UNCERTAINTY` as `unsupported`.
- **`GRPCONVERT`, `CNVTYPE`, `LIMGRP`.** `CNVTYPE 0` is `equal_lethargy`, `CNVTYPE 1` is `equal_energy`. `LIMGRP low high` is read as two energies in eV, so the entry is `approximated`. The group counts of the spectrum come from the fluxes file, not from the arguments of `GRPCONVERT`.
- **`DOSE 2`, `GROUP`, `BREMSSTRAHLUNG`.** `DOSE 2 distance` writes `photon.point_source`; a distance below 0.3 m is raised to 0.3 m, as FISPACT-II does. The dose values are `null` until you add a `photon.response` with an air attenuation curve. The shipped photon response has none, so an imported `DOSE 2` gives `null` doses by default: rebuild a response with `scripts/build_photon_response.py --air-attenuation` and declare its new SHA-256, or set `photon.point_source.air_attenuation` to `false` for the unattenuated dose. The dose is the air kerma rate (numerically Gy/h), not an ambient dose equivalent, and it needs `options.outputs` to include `photons` or `dose` when the deck sets outputs. `BREMSSTRAHLUNG` is `approximated`: ACTINV reports the bremsstrahlung photons and their dose in a separate block and does not add them to the decay-photon dose. The electrons per decay come from each nuclide's beta-minus decay branching and their energy is the STYP 1 average energy divided by that branching. A deck that lists no nuclides, or an element, is `unsupported`.
- **`PATH`, `ROUTES`.** The nuclides named become `pathways.targets` and the route-search thresholds keep their defaults; numeric arguments are listed in the entry as not interpreted. ACTINV refuses `pathways` together with power-driven steps or `PARTITION`; the importer then drops the section and marks the entry `unsupported`.
- **`ATWO`.** The A2 values of 49 CFR 173.435 (the IAEA SSR-6 Rev.1 table) are written as `bundled:us-49cfr173-a2`, with one response per uranium absorption type. They are not FISPACT-II's own A2 file, so the entry is `approximated`. Nuclides with an unlimited A2 (Mn-53, Ni-59, Zr-93, ...) count as covered with zero contribution, so `require_complete: true` accepts an activation inventory that holds them.

- **Fission.** `USEFISSION` needs `--fission-yields`; without it the entry is `unsupported` and names the option. With it `fission_yields.files` lists the files, and the energy setting stays at the spec default. `NOFISS` forces no yield files, whatever else is given. `CUMFYLD` sets `fission_yields.yields` to `cumulative`. `FISYIELD n` selects the parents: a positive count includes the listed actinides, a negative count excludes them and zero excludes every parent that has a yield file. Without `FISYIELD` the FISPACT-II default of U-235, U-238 and Pu-239 applies, as an include list cut down to the parents that have a yield file. A named parent with no yield file does not fission in FISPACT-II either, so it is dropped and the entry names it. `FISCHOOSE` restricts only the pathway analysis, so it becomes `pathways.fission_parents` when the deck has `PATH` or `ROUTES` and is `noted` otherwise.
- **Uncertainty.** `UNCTYPE 1`, `2` and `3` set the uncertainty channels. ACTINV propagates the MF=33 covariance whenever a covariance sidecar is given, so type 2 cannot leave the cross-section contribution out: the entry is `approximated` and says the band is wider. `SENSITIVITY SIGMA` asks for the ranked first-order variance shares (`uncertainty.voi`) in place of sampled sensitivity coefficients, and adds the uncertainty block when no `UNCERTAINTY` keyword did; `SENSITIVITY LAMBDA` is `unsupported`. `MCSAMPLE` and `MCSEED` configure the sampling that the analytic result replaces: they are `noted` beside a mapped `SENSITIVITY` and `unsupported` without one. `ERROR n` with parent, daughter and a fractional error becomes a `cross_section_uncertainty` override on the reaction that takes the parent to the daughter (`Fission` or `0` is MT 18). The error is read as the relative standard uncertainty of that reaction, which is an interpretation, so the entry is `approximated`; `-1` asks for the library value and is `noted`. The override needs the uncertainty block with the cross-section channel, so without one the entry is `unsupported`. `COVARIANCE` is `noted`: ACTINV has no collapse step.
- **Self-shielding.** `SSFGEOMETRY type length [height]` (1 foil, 2 wire, 3 sphere, 4 cylinder, lengths in cm) with `--resonance` becomes `self_shielding.geometry`, with the density from `DENSITY` (when the deck has none the density is listed under `required_additions`), and `SSFCHOOSE` becomes its `choose` list. `SSFMASS` and `SSFFUEL` give a composition for the self-shielding calculation, but ACTINV shields the problem's own material, so they are `approximated`. `PROBTABLE` with `--self-shielding-table` becomes `self_shielding.table`; the Bondarenko factors stand in for FISPACT-II's probability tables, so it is `approximated`. `SSFDILUTION` with the table becomes `dilution_overrides`. FISPACT-II numbers the energy groups from the highest energy (group 1), and ACTINV's index is 0-based and ascending in energy, so group `g` of the 709-group library is index `709 - g`. FISPACT-II does not allow `SSFGEOMETRY` beside `PROBTABLE`, and neither does ACTINV.
- **Noted and corrected.** `XSTHRESHOLD` and `DEPLETION` are `noted`: the first is a cost control of FISPACT-II's pathway-based uncertainty estimate, the second an output request (set `summary.depletion` for the table). `RESULT` is `noted`: a converted deck always defines a material and runs a schedule, whose inventory the pathways use.

A power-driven step is refused beside `uncertainty`, `pathways` and step libraries, and the importer follows those refusals instead of writing a spec ACTINV would reject.

## The import report

Nothing is dropped silently. `OUT.import.json` (`actinv-fispact-import-report-1`) lists every keyword with its line, the section of the deck it appeared in, its arguments, and one status:

| Status | Meaning |
| --- | --- |
| `mapped` | translated into a spec field |
| `approximated` | translated with a stated caveat, given in `detail` |
| `noted` | no effect on the calculated physics (printing, files, solver tolerances) |
| `unsupported` | would change the physics and has no ACTINV equivalent, or is unknown to this importer (a misspelling, for instance) |

The report also holds the counts, `options_unused` (present only when an option was given that no keyword of the deck used), the spectrum source and total, the library chosen, the content of the files file, the SHA-256 of the deck, `required_additions` (spec sections that the deck implies and no deck can supply, such as `kerma` for `POWER`; empty for most decks), and a `schedule_map` giving, for each ACTINV step, the deck line that defined it, whether it is irradiation or cooling, and the FISPACT-II output keyword that ran it. ACTINV reports every step in full, so the choice between `ATOMS`, `STEP` and `SPECTRUM` does not change what is computed.

Check every `approximated` and `unsupported` entry before trusting a converted problem. `--strict` makes the command fail, after writing the report, when any entry is `unsupported`.

## Not translated

These are reported as `unsupported`, each with a note saying what to do:

- The self-shielding keywords `PROBTABLE`, `SSFGEOMETRY`, `SSFCHOOSE`, `SSFFUEL`, `SSFMASS` and `SSFDILUTION`, unless the option that supplies their data is given (`--resonance` or `--self-shielding-table`, above). Each entry then names the missing option. A target type that ACTINV has no shape for, a cylinder without its height and `SSFGEOMETRY` beside `PROBTABLE` stay `unsupported`.
- A `GETXS` after the first `TIME`. ACTINV expresses it as a step `library` override, which needs the second library's path and the spectrum in its group structure. The entry names the schedule step to edit. Step libraries are refused beside `uncertainty`, `kerma`, power-driven steps, `partition`, `pathways` and cross-section overrides.
- `ADCROSS`, `ADLAM` (error factor against relative uncertainty), and `ACROSS` daughters that are isomers or ambiguous.
- Displacement energies that differ from the damage table's (the entry gives the `build-damage` command for a matching table), displacement damage in a non-neutron deck without `--damage`, `USEFISSION` without `--fission-yields`, `SENSITIVITY LAMBDA`, `IRON` (not a keyword of the 2023 manual), `READSF`, `USESPALLATION`, `USEXSEXTRA` and the other library-selection keywords.
- A `GRPCONVERT` spectrum whose group count is not a FISPACT-II standard structure stops the import, since its boundaries are not in the deck.

A material defined after the first `TIME` is not representable.

## Behaviour to know about

- **`MASS` percentages.** Percentages that do not sum to 100 are written as given and the spec does not renormalize them; the importer says the same reading applies in FISPACT-II, which has not been confirmed from the manual or an output (unverified).
- **`MIND`.** The UKAEA tutorial decks comment that 1e5 atoms is the FISPACT-II default threshold. A deck with no `MIND` has that implicit threshold; the importer neither writes nor mentions it, so set `summary.min_atoms` if the nuclide lists should match (unverified default).
- **`GRPCONVERT a b`.** The two numbers are not read. The group counts come from the fluxes file and the destination structure is always the library's, whatever `b` says.
- **Isotopic abundances.** A `MASS` composition is resolved through ACTINV's natural-abundance table. FISPACT-II's differs slightly, and the report does not list the difference.
- **`OVER` after the first step.** ACTINV overrides are not step-scoped, so an `OVER` that follows a scheduled step applies to the whole run, including the earlier steps. Its entries are `approximated` and say so.
- **`DOSE`.** `DOSE 1` after a `DOSE 2` and before any step cancels the point source (the `DOSE 2` entry is marked superseded). After a step the point source stays for the whole run and the `DOSE 1` entry is `approximated`.
- **`--files`.** A `fluxes` entry that names a missing file stops the import only when the deck irradiates; a cooling-only deck imports and the report records the unresolved entry (`fluxes_file_unresolved`).
- **`--strict`.** After a failure the report is written, and an `OUT.json` from an earlier run is removed so that it cannot be mistaken for the result. `OUT` may not be the deck itself.

## Tables and plot data from a result

FISPACT-II writes its tables and graph files while it runs. ACTINV writes one result JSON, and `actinv report` renders it afterwards, so no tab file is needed and the tables can be regenerated for any step. The report is a pure function of the result file; it runs no solver and reads no data.

```text
actinv report RESULT.json [--tables LIST] [--format csv|tsv|markdown|text]
              [--out DIR] [--step N|all] [--nuclides Co60,Mn54]
```

| FISPACT-II | `--tables` | Content |
| --- | --- | --- |
| `TAB1` | `tab1` | atoms and grams per nuclide, per step |
| `TAB2` | `tab2` | activity (Bq) and heat (W, where FISPACT-II prints kW) per nuclide, per step; also the alpha, beta and gamma heat split and the half-life |
| `TAB3` | `tab3` | ingestion and inhalation dose responses, per step. These are totals over nuclides: the result holds the response value and its activity coverage, not the per-nuclide coefficients |
| `TAB4` | `tab4` | decay-photon source per energy group, per step (`photons_s_g`, `power_W_g`, absolute and per gram) |
| `GRAPH`, `NUCGRAPH` | `timeseries` | one row per step: total activity, alpha, beta and gamma heat, the photon dose rates and radiological responses when present, and `--nuclides` columns (atoms and Bq per gram) |
| `SORTDOMINANT` listings | `dominant`, `elements`, `totals` | the `summary` dominant lists, the element table and the step totals (see the [results page](results.md#step-summary)) |

Grams, the heat split, half-lives, absolute (whole-sample) columns, the element table and the dominant lists come from the result's `summary`; a deck imported with `MIND`, `NOSTABLE` or `SORTDOMINANT` already has the block. Without it `tab1` and `tab2` still give atoms and Bq per gram from the base fields, the other tables are reported as unavailable, and standard error names every column that was left out. Nothing is filled in. `tab3` is unavailable unless the problem declares `radiological` with a dose response.

Quantities keep the result's basis. A `_per_g` column is per gram of initial material; the unsuffixed `atoms`, `mass_g`, `activity_Bq` and `heat_W` columns scale by `material.mass_g`. `csv` and `tsv` hold exact values, formatted so they parse back to the same number; `text` and `markdown` round to six significant digits. See [Command line](cli.md#render-result-tables) for the options.

## Convert a directory of decks

`controls/p132_import_all.py` imports every `*/*.i` deck under a directory with the release binary and prints the status counts per keyword:

```text
cargo build --release -p actinv-cli
python3 controls/p132_import_all.py ~/nuclear-data/conderc-fns/fns --out /tmp/p132-imports
```

It passes the library, decay and covariance files named by `ACTINV_LIBRARY`, `ACTINV_ENDF_DECAY` and `ACTINV_COVARIANCE`, when set, and otherwise lets the importer use the catalog references.
