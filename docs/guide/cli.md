# Command line

The `actinv` command is included with the Python package or can be installed independently with Rust. Run `actinv --version` to identify your executable and `actinv --help` to see its commands. The established examples here cover 1.4.0; the waste commands are documented separately in [Component waste classes and impurity limits](waste.md).

The native waste composition workflow is `actinv waste composition solve
SPEC.json [OUT.json]`. It runs the bounded pure-element basis and every
distinct projection witness allowed by the wrapper limits, then emits one
result only after all requested runs and comparisons succeed. Its base-spec
path is relative to the wrapper file; paths inside that base spec retain the
usual current-working-directory/catalog rules. See the [waste handbook](waste.md#generate-and-verify-a-native-composition-basis) for its schema and scientific scope.

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

## Import flux and run independent cells

```text
actinv import-flux openmc SOURCE.h5 OUT.ndjson --tally ID --source-rate RATE
actinv import-flux meshtal SOURCE OUT.ndjson --tally ID --source-rate RATE
actinv import-flux mctal SOURCE OUT.ndjson --tally ID --source-rate RATE
actinv import-flux fispact FLUXES OUT.ndjson --groups GROUPS.json
actinv mesh SPEC.json OUT.ndjson
```

Additional import options and mesh fields are in the [specification reference](specification.md#flux-interchange-actinv-flux-1). Supply a physically justified source-rate normalization for transport tallies.

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
```

Each workflow consumes its own document format. [Advanced workflows](workflows.md) links the schemas, examples, and applicability limits. Run `actinv COMMAND --help` for available help; some commands print the shared usage rather than a dedicated page.
