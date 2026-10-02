# Advanced workflows

Use these workflows after you have run and checked a single-material problem. They share the solver but add their own inputs, coverage requirements, and output formats.

## Transport flux and mesh calculations

Import supported OpenMC statepoints, MCNP MESHTAL/MCTAL files, or FISPACT fluxes into `actinv-flux-1` NDJSON. Supply a tally identity and source-rate normalization where required. Review energy ordering, units, cell volume, and rebinning before solving.

`actinv mesh` calculates independent cells and writes `actinv-mesh-result-1` NDJSON. Cells do not exchange material or flux and have no transport or thermal feedback. Workload grouping can reuse identical spectra; output selection, a post-hoc memory guard, and checkpoint resume are described in the [mesh specification](specification.md#independent-mesh-specification-actinv-mesh-spec-1).

Photon exports turn computed sources into transport inputs. Ordinary exports use a point at the origin; mesh exports require cell bounds. Check spatial interpretation in the receiving transport code.

`export-source` converts a banded `actinv-r2s-source-1` document into the format a transport code or activation-adjacent R2S tool reads directly: OpenMC settings-XML, an MCNP SDEF deck, a Serpent `src` card block, or — for ALARA-based R2S tools such as `nucleide` and PyNE — one ALARA `.photonSrc` file per cell plus a provenance index (`export-source alara`, requires `--shutdown-t-s`). See the [command reference](cli.md#export-photon-sources), `docs/INTERCHANGE_TRANSPORT.md` for the pinned format contract, and `contrib/nucleide_r2s/demo.py` for a working round-trip through nucleide's own ALARA reader.

## Build libraries and response tables

`build-library` converts supported evaluated ENDF-6 data into an activation library and adjacent index. Projectile, group structure, temperature, product-state identity, and builder options determine compatibility.

`build-covariance` creates an MF=33 sidecar for a specific activation library. `build-shielding` creates a neutron self-shielding table. `build-damage` creates a damage-energy table. These inputs are requested separately in the problem; an activation-library build alone does not enable those calculations.

See the [specification reference](specification.md#build-an-activation-library) for commands and formats. Keep source data and attribution outside the repository.

## Uncertainty and self-shielding

The `uncertainty` section selects response bands and coverage policy. MF=33 cross sections are the default channel; half-life and independent fission-yield uncertainties are optional. [Results](results.md#read-uncertainty-with-its-coverage) explains how to read the bands.

Since 1.4.0, `channels: ["flux"]` is available with groupwise `spectrum.relative_error`, or each mesh cell's own supplied errors. This propagates tally statistics and can run without covariance when it is the only requested channel. It excludes systematic transport errors and is omitted from measurement-design rankings. See the [uncertainty reference](specification.md#mf33-uncertainty).

The `self_shielding` section selects a hash-pinned neutron table and composition-derived or fixed background dilution. It supports bounded unresolved-range Bondarenko treatment; it does not apply resolved-region pointwise shielding or shield damage observables. Use the [self-shielding reference](specification.md#self-shielding) for the supported table contract and completeness option.

## Studies

An `actinv-study-1` document expands cases over declared material, schedule, data, and calculation choices. `study validate`, `build`, and `run` separate document checking, deterministic case generation, and execution.

The study schema also describes refinement and seeded robustness sampling. Consult the [study reference](https://github.com/AvilaLabs/ACTINV/blob/master/docs/STUDY.md) for scalar-response restrictions, sampling channels, and execution limits.

## Reverse calculation

`actinv reverse` estimates a common flux multiplier or per-irradiation-step multipliers from measured activities. It requires a linear trace-regime problem and rejects unsupported or unidentifiable cases. It estimates normalization; it does not unfold the spectrum shape.

`reverse-qualified` adds nuclear-data covariance to the measurement model and reports posterior covariance and identifiability. Its forward problem must request the relevant banded activity responses. See the [command reference](cli.md#study-and-design-workflows) and [reverse example](https://github.com/AvilaLabs/ACTINV/blob/master/examples/reverse_demo_problem.json).

## Design search and impurity budgets

`optimize` searches declared composition fractions, flux scale, and schedule durations with a seeded optimizer. Constraints can use propagated uncertainty edges. Banded candidates can take minutes each; use it as a batch workflow. Read the [optimization schema](https://github.com/AvilaLabs/ACTINV/blob/master/docs/OPTIMIZE.md) and [steel example](https://github.com/AvilaLabs/ACTINV/tree/master/examples/optimize_ra_steel).

`decide` evaluates declared response constraints and ranks measurement targets. `budget` estimates and verifies impurity limits against a supplied clearance calculation. These workflows inherit the selected response table's applicability; they do not choose a jurisdiction or establish regulatory acceptance. See the [budget schema](https://github.com/AvilaLabs/ACTINV/blob/master/docs/BUDGET.md).
