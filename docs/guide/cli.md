# Command line

The `actinv` command is included with the Python package or can be installed independently with Rust. Run `actinv --version` to identify your executable and `actinv --help` to see its commands. The established examples here cover 1.4.0; the waste commands are documented separately in [Component waste classes and impurity limits](waste.md).

The native waste composition workflow is `actinv waste composition solve
SPEC.json [OUT.json]`. It runs the bounded pure-element basis and every
distinct projection witness allowed by the wrapper limits, then emits one
result only after all requested runs and comparisons succeed. Its base-spec
path is relative to the wrapper file; paths inside that base spec retain the
usual current-working-directory/catalog rules. See the [waste handbook](waste.md#generate-and-verify-a-native-composition-basis) for its schema and scientific scope.

`actinv waste intrusion-screen SPEC.json [OUT.json]` keeps nominal Part 61
classification and the explicitly selected February 2026 draft fusion screen
separate. It screens one declared whole container at selected nominal steps.
See the [draft-screen format](waste.md#screen-a-whole-container-against-the-fusion-draft)
for caller-supplied WAC/RQ data, coverage and assessment indicators.

## Create, check, and solve

```text
actinv new OUT.json [--data-dir DIR]
actinv validate SPEC.json [--schema|--files|--hashes]
actinv doctor [SPEC.json]
actinv run SPEC.json [OUT.json]
```

`new` refuses to overwrite an existing file. `validate` defaults to schema checking; `--files` checks supported readable inputs and indexes, and `--hashes` additionally checks declared file hashes. The run validates evaluated-data compatibility and computes input hashes. Without an output path, `run` writes result JSON to standard output.

Schema checking does not open literal data paths. Symbolic `catalog:` references must resolve to installed artifacts before validation, so install their bundle first.

Literal relative data paths use the current working directory. [Describe a problem](problems.md#data-paths) explains catalog references and path handling across interfaces.

## Install and inspect data

```text
actinv data list
actinv data manifest
actinv data fetch [BUNDLE] [--output DIR] [--force]
actinv data verify [BUNDLE] [--output DIR]
```

See [Data setup](data.md) for bundle names and library/covariance pairing.

## Query data files

```text
actinv inspect decay DECAY_PATH [NUCLIDE...]
actinv inspect xs LIBRARY.npz [--target NUCLIDE] [--mt MT] [--spectrum SPEC.json]
actinv inspect yields YIELD_PATH... [--parent NUCLIDE] [--energy EV] [--cumulative]
actinv inspect library LIBRARY.npz
actinv inspect groups NAME
```

`inspect` reads the same files a run consumes and prints JSON to standard output. Add `--out OUT` to write the JSON to a file, or `--table` for a fixed-width table instead. It never modifies data.

- `decay` reports, per nuclide (all of them when none is named), the half-life, each decay mode with its branching ratio and daughter, the mean beta, gamma and alpha energies as stored in the evaluation, and the number of discrete gamma lines with their total energy per decay. Name nuclides as in a material, for example `Co60` or `Ta182m1`.
- `xs` lists a library's reactions: target, MT, product, group count and energy structure. With `--target` it also gives each reaction's group cross sections; `--target` takes a nuclide name or a raw target index (a ZA such as `26056` is not an index; name the nuclide), and a name needs the `*_index.json` file beside the library. `--mt` keeps one reaction type. With `--spectrum`, which accepts a spectrum object or a run spec, it also collapses each reaction with the solver's own flux-weighted average and reports the one-group cross section in barns and the reaction rate per target atom, `sigma * total_flux`. The spectrum must be on the library's group structure, checked as a run checks it: a named structure must have the library's boundaries, custom boundaries must match to `1e-12` relative. A spectrum with `rebin` is converted onto its named destination structure first, as a run does, and the destination must be the library's.
- `yields` reads one or more fission-yield evaluations and reports independent yields at every tabulated incident energy. `--energy EV` interpolates between tabulated energies exactly as a run does and reports each product's propagated uncertainty. `--cumulative` selects the evaluation's cumulative (MT=459) yields, when present, for inspection only; runs never use them. `--parent` selects an evaluation by parent nuclide.
- `library` summarizes a library without decoding its cross sections: targets, reactions (by MT), group structure, energy range, projectile, the scalar fields of the index file and its object fields (such as the `spallation` splice provenance, under `index_objects`). A library without an index file is named after any of the FISPACT-II structures its boundaries match.
- `groups` prints the boundaries of `fispact-709`, `fispact-162`, a refinement such as `fispact-709x4`, or a group JSON file, in ascending energy.

Without `--target`, `xs` omits the group values and lists every reaction in the library; narrow it with `--target` or `--mt`. With `--table` and a single reaction, `xs` prints one row per group.

## Render result tables

```text
actinv report RESULT.json [--tables LIST] [--format csv|tsv|markdown|text]
              [--out DIR] [--step N|all] [--nuclides LIST]
```

`report` turns a result JSON from `actinv run` into FISPACT-II style tables, ready to plot. It runs no solver and reads no data files; the output depends only on the result. `--tables` takes a comma-separated list (default: `tab1`, `tab2`, `tab3`, `tab4`, `totals`, `dominant`, `elements` and `timeseries`; `depletion` is built only when you name it); `--step` selects one step by its one-based number (default `all`); `--nuclides Co60,Mn54` keeps only those rows in `tab1`, `tab2` and `depletion` and adds their atoms and activity columns to `timeseries`. A nuclide that is in no selected step is an error. Names are written as in the result, for example `Co60` or `Ta182m1`.

| Table | One row per | Columns |
| --- | --- | --- |
| `tab1` | step and nuclide, largest population first | `step`, `time_s`, `nuclide`, `atoms_per_g`, `mass_g_per_g`, `atoms`, `mass_g` |
| `tab2` | step and nuclide with activity, largest first | `step`, `time_s`, `nuclide`, `activity_Bq_per_g`, `heat_W_per_g`, `alpha_W_per_g`, `beta_W_per_g`, `gamma_W_per_g`, `half_life_s`, `activity_Bq`, `heat_W` |
| `tab3` | step and ingestion or inhalation dose response | `step`, `time_s`, `response`, `value`, `unit`, `covered_activity_Bq_per_g`, `missing_activity_Bq_per_g`, `activity_coverage_fraction`, `contributing_nuclides`, `missing_active_nuclides` |
| `tab4` | step and photon energy group | `step`, `time_s`, `group`, `low_eV`, `high_eV`, `centroid_eV`, `photons_s_g`, `power_W_g`, `photons_s`, `power_W` |
| `totals` | step | total activity and heat per gram; with a `summary`, also Ci, the alpha, beta and gamma activity, activity by decay mode, activity and heat without H-3, mass, absolute and per-cm³ values |
| `dominant` | step, quantity and rank | the `summary.dominant` lists with percent, cumulative percent, and the counts to reach 90 % and 99 % |
| `elements` | step and element | the `summary.elements` table |
| `timeseries` | step | `activity_Bq_per_g`, `heat_total_W_per_g`, `heat_alpha_W_per_g`, `heat_beta_W_per_g`, `heat_gamma_W_per_g`, then the photon dose rates (`contact_gamma_air_dose_proxy_Gy_h`, `point_source_gamma_air_dose_Sv_h`), one column per radiological response, and `NUCLIDE_atoms_per_g`, `NUCLIDE_activity_Bq_per_g` for each `--nuclides` entry |
| `depletion` | step and initial nuclide | `step`, `time_s`, `nuclide`, `model`, `initial_atoms_per_g`, `atoms_start_per_g`, `atoms_end_per_g`, `depleted_atoms_per_g`, `depleted_fraction`, `cumulative_depleted_atoms_per_g`, `cumulative_depleted_fraction`, `removal_rate_per_s`, `removal_rate_relative_standard_uncertainty` (only when the result carries one), and `channels`, the ranked removal channels with their shares (`MT102 60.0000%; decay 40.0000%`). Needs `summary.depletion` in the problem |

Every column name carries its unit. `time_s` is the time at the end of the step. `_per_g` quantities are per gram of initial material; `atoms`, `mass_g`, `activity_Bq` and `heat_W` are those values times `material.mass_g`, and exist only when the result has a `summary`. Heat is in watts, where FISPACT-II prints kW. `half_life_s` is empty for a stable nuclide. A step that lacks a value (for instance a photon dose rate) leaves the cell empty.

`--format csv` (the default) and `tsv` print every number in the shortest form that parses back to the same value, for example `3.0000000000000004e-1`, with one header row. `markdown` prints a pipe table under a heading, and `text` a fixed-width table, both rounded to six significant digits and followed by `Note:` lines for omitted columns and for rows the summary trimmed (`min_atoms`, `include_stable`): `tab1` and `tab2` say how many entries are missing from `summary.nuclides`, and a `--nuclides` name the summary trimmed is named in a note. Without `--out`, the tables go to standard output; with several tables in `csv` or `tsv`, each is introduced by a `# name` line. `--out DIR` creates `DIR` and writes one file per table (`tab1.csv`, `tab4.csv`, and so on; `.tsv`, `.md` or `.txt` for the other formats), replacing files of the same name.

Anything the result cannot supply is left out, never estimated. Standard error lists the columns omitted and the tables that are unavailable, each with the reason (for example, no `summary` section, no `radiological` responses, no `photon_source`). The command fails only when no requested table can be built. Add a [`summary`](specification.md#step-summary) section to the problem for grams, the heat split and the element and dominant tables; an `outputs` selection that drops `inventory`, `activity_Bq_per_g` or `heat_W_per_g` removes the tables built from them.

## Import flux and run independent cells

```text
actinv import-flux openmc SOURCE.h5 OUT.ndjson --tally ID --source-rate RATE
actinv import-flux meshtal SOURCE OUT.ndjson --tally ID --source-rate RATE
actinv import-flux mctal SOURCE OUT.ndjson --tally ID --source-rate RATE
actinv import-flux fispact FLUXES OUT.ndjson --groups GROUPS.json
actinv mesh SPEC.json OUT.ndjson
```

Additional import options and mesh fields are in the [specification reference](specification.md#flux-interchange-actinv-flux-1). Supply a physically justified source-rate normalization for transport tallies.

### Facility twins and optional waste classes

`actinv twin TWINSPEC.json [OUT.json]` evaluates certified clearance bands for
the existing mesh components. To also classify each component's nominal
activity, add the optional `waste` block documented in [Component waste
classes](waste.md#classify-declared-waste-components-in-a-facility-twin). It
requires caller-supplied component mass, displaced volume or density, waste
type, cell-mass mapping, selected steps, nuclide properties, and an explicit
Part 61 rule-pack ID. The component groups already in the twin remain the only
source of cell membership.

The added `waste_classification` and `waste_facility_coverage` fields use the
original mesh activity maps. Cell and dose assays affect the existing twin
band results but leave both waste fields unchanged. The class summary counts
components separately; it does not make a facility class or determine disposal
acceptance. Omitting the `waste` block retains the existing twin result fields.

## Convert a FISPACT-II deck

```text
actinv import-fispact DECK.i OUT.json [--fluxes FLUXES] [--files FILES] [--library LIB.npz] [--decay DECAY] [--covariance COV.npz] [--strict]
```

Writes an `actinv-spec-1` problem and an `OUT.import.json` report that lists every deck keyword as mapped, approximated, noted or unsupported. `--strict` fails when any keyword is unsupported. See [Move a FISPACT-II deck to ACTINV](fispact-migration.md) for the keyword table and limits.

## Export photon sources

```text
actinv export-openmc RESULT.json STEP OUT.py
actinv export-mcnp RESULT.json STEP OUT.sdef
actinv export-openmc-mesh MESH_RESULT.ndjson STEP OUT.py
actinv export-r2s MESH_RESULT.ndjson STEP OUT.ndjson
actinv export-source openmc R2S_SOURCE.ndjson OUT
actinv export-source mcnp R2S_SOURCE.ndjson OUT
actinv export-source serpent R2S_SOURCE.ndjson OUT
actinv export-source alara R2S_SOURCE.ndjson OUT_DIR --shutdown-t-s T
```

`STEP` is one-based. The selected step must contain the requested photon source. Ordinary result exports place a point source at the origin; mesh sources need recorded geometry and independent spatial review.

`export-source alara` writes one ALARA `.photonSrc` file per cell under `OUT_DIR` (which must not exist, or exist and be empty) plus a provenance index `OUT_DIR/actinv-alara-index.json`. `--shutdown-t-s T` is required; cooling time is `step_t_s - T`, and every cell must share the same `step_t_s`. See [Advanced workflows](workflows.md#transport-flux-and-mesh-calculations) and `docs/INTERCHANGE_TRANSPORT.md` for the full contract.

## Build data

```text
actinv build-library INPUT OUTPUT.npz [OPTIONS]
actinv build-covariance INPUT ACTIVATION.npz OUTPUT.cov.npz [OPTIONS]
actinv build-damage EVALUATION_DIR OUT.json [OPTIONS]
actinv build-shielding EVALUATION_DIR OUT.json [OPTIONS]
```

The [specification reference](specification.md#build-an-activation-library) describes builder parameters. Activation libraries, covariance, shielding, and damage tables are distinct inputs.

## Study and design workflows

```text
actinv study validate STUDY.json
actinv study build STUDY.json [OUTDIR] [--revocations FILE]
actinv study run STUDY.json [OUTDIR] [--revocations FILE]
actinv reverse PROBLEM.json MEASUREMENTS.json [OUT.json] [--segments]
actinv reverse-qualified PROBLEM.json MEASUREMENTS.json OUT.ndjson
actinv optimize OPTSPEC.json [OUTDIR] [--resume]
actinv decide DECISION.json [OUT.json]
actinv budget BUDGET.json [OUT.json] [--no-verify]
actinv waste WASTE.json [OUT.json]
actinv waste budget BUDGET.json [OUT.json]
actinv waste bounds BOUNDS.json [OUT.json]
actinv waste composition COMPOSITION.json [OUT.json]
actinv waste composition solve NATIVE_COMPOSITION.json [OUT.json]
actinv waste intrusion-screen INTRUSION.json [OUT.json]
```

Each workflow consumes its own document format. [Advanced workflows](workflows.md) links the schemas, examples, and applicability limits. Run `actinv COMMAND --help` for available help; some commands print the shared usage rather than a dedicated page.
