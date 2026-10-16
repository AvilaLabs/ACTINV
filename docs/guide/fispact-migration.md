# Move a FISPACT-II deck to ACTINV

`actinv import-fispact` reads a FISPACT-II input deck (`.i`) and writes an ACTINV [problem specification](specification.md). It converts the problem description: the material, the neutron (or charged-particle) spectrum, the irradiation and cooling schedule, and the few output settings that have an ACTINV counterpart. It does not read FISPACT-II's nuclear data. The activation library and decay data of the converted problem are ACTINV's, so results differ from a FISPACT-II run by the difference between the two data sets as well as by the solvers.

```text
actinv import-fispact DECK.i OUT.json [--fluxes FLUXES] [--files FILES]
                      [--library LIB.npz] [--decay DECAY]
                      [--covariance COV.npz] [--strict]
```

The command writes `OUT.json` (the problem) and `OUT.import.json` (the import report, below). Run and check the converted problem in the usual way:

```text
actinv import-fispact TENDL-2017_1996exp_5min.i fe_5min.json
actinv validate fe_5min.json
actinv run fe_5min.json fe_5min_result.json
```

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

A power-driven step is refused beside `uncertainty`, `pathways` and step libraries, and the importer follows those refusals instead of writing a spec ACTINV would reject.

## The import report

Nothing is dropped silently. `OUT.import.json` (`actinv-fispact-import-report-1`) lists every keyword with its line, the section of the deck it appeared in, its arguments, and one status:

| Status | Meaning |
| --- | --- |
| `mapped` | translated into a spec field |
| `approximated` | translated with a stated caveat, given in `detail` |
| `noted` | no effect on the calculated physics (printing, files, solver tolerances) |
| `unsupported` | would change the physics and has no ACTINV equivalent, or is unknown to this importer (a misspelling, for instance) |

The report also holds the counts, the spectrum source and total, the library chosen, the content of the files file, the SHA-256 of the deck, `required_additions` (spec sections that the deck implies and no deck can supply, such as `kerma` for `POWER`; empty for most decks), and a `schedule_map` giving, for each ACTINV step, the deck line that defined it, whether it is irradiation or cooling, and the FISPACT-II output keyword that ran it. ACTINV reports every step in full, so the choice between `ATOMS`, `STEP` and `SPECTRUM` does not change what is computed.

Check every `approximated` and `unsupported` entry before trusting a converted problem. `--strict` makes the command fail, after writing the report, when any entry is `unsupported`.

## Not translated

These are reported as `unsupported`, each with a note saying what to do:

- The self-shielding keywords `PROBTABLE`, `SSFGEOMETRY`, `SSFCHOOSE`, `SSFFUEL`, `SSFMASS` and `SSFDILUTION`. ACTINV has `self_shielding.table`, `self_shielding.geometry` and `dilution_overrides`, but each needs hash-pinned data (a Bondarenko table, or ENDF resonance files) that a deck does not name, so add the section by hand.
- A `GETXS` after the first `TIME`. ACTINV expresses it as a step `library` override, which needs the second library's path and the spectrum in its group structure. The entry names the schedule step to edit. Step libraries are refused beside `uncertainty`, `kerma`, power-driven steps, `partition`, `pathways` and cross-section overrides.
- `ADCROSS`, `ADLAM` (error factor against relative uncertainty), `ACROSS` daughters that are isomers or ambiguous, and `RESULT`.
- Displacement-damage keywords (`ALLDISPEN`, `ATDISPEN`; see the `damage` option), `USEFISSION` and the fission-yield keywords (use `fission_yields.files`), `COVARIANCE`, `UNCTYPE`, `ERROR`, `MCSAMPLE`, `SENSITIVITY`, `DEPLETION`, `IRON`, `READGG` and the other library-selection keywords.
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
