# ACTINV

**Open, standalone, activation-grade nuclide-inventory solver.** A particle flux spectrum from any source — MCNP, PHITS,
Serpent, OpenMC, or a measurement — plus a material and an irradiation history gives the nuclide inventory, activity,
decay heat and evaluated decay-photon source over cooling time. Hash-pinned independent fission yields, explicit
isotopes/isomers, coupled burn-up and pulsed histories are supported, with a ledger of everything the calculation
could not account for.

Rust core, Python API, code-agnostic validation harness. Avila Labs, Oviedo, Florida.
Licence: **MIT OR Apache-2.0**. Current version: **v0.5.0** (tagging and publishing are external acts).

> Research-grade software. Validated against the 132-experiment FNS decay-heat benchmark (see below), but **not**
> validated for licensing, safety or regulatory decisions. Known limitations are listed in
> [docs/RELEASE_NOTES_v0.5.md](docs/RELEASE_NOTES_v0.5.md) — the code reports each of them rather than hiding it.

## Validation at a glance

FNS decay-heat benchmark (IAEA CoNDERC): 73 materials, 132 experiments, D–T irradiation at JAEA.

| | ACTINV / EAF-2010 | ACTINV / TENDL-2023 | FISPACT-II / TENDL-2017 |
|---|---|---|---|
| median geometric-mean C/E | 1.024 | 1.035 | 1.009 |
| within 30 % everywhere | 47 % | 45 % | 52 % |

Every number is re-derivable: the harness and all controls ship with the code, and each run hashes its inputs.

P7 photon gates independently parse all 3,821 ENDF/B-VIII.0 decay evaluations (7,113 spectra). Calculated specific
gamma constants are 0.30565 for Co-60 and 0.07695 for equilibrium Cs-137/Ba-137m, respectively 1.09% and 1.34% from
the gate references. See [Validation](docs/VALIDATION.md) for the complete gate record and limitations.

P8 imports the supported OpenMC/MCNP/FISPACT flux subsets into a hashed streaming format. Its eight-cell control gives
exact mesh/single-run identity for all cells and byte-identical cell records at one and four threads.

P9 reports all 175 finite U-235 thermal decay-heat channel points in the CoNDERC Dickens pulse and Yarnell 20,000 s
sets. Geometric-mean total C/E is **1.0070** and **0.9845**, respectively. On identical Fe-56(n,p)Mn-56 data and a
10-pulse history, shutdown inventory differs from ALARA 2.9.2 by at most `4.12e-8`; pulse evolution agrees with OpenMC
CRAM48 at `3.91e-15` on resolvable populations.

P10 moves production library construction into Rust and completes deterministic TENDL-2025 neutron, proton,
deuteron and alpha libraries plus EAF-2010: **12,216 targets and 1,849,479 reaction/product rows**, with zero target
errors, unsupported fallbacks or convergence flags. R-matrix-limited and infinite-dilution unresolved reconstruction,
arbitrary-temperature neutron broadening and analytic ultra-narrow lines are independently controlled. The P10
checker verdict is **P10-CONDITIONAL** because its frozen repair history is retained.

## Install

```bash
cargo install --path crates/actinv-cli                         # the `actinv` command
cd python && maturin build --release --out ../dist             # build the Python wheel
pip install ../dist/actinv-*.whl                               # `import actinv`
```
Requires a Rust toolchain and Python ≥ 3.9. The binding uses PyO3 0.29 and builds against Python 3.14.

## Get the data

Nuclear data are never bundled; they are fetched from their public hosts and pinned by SHA-256.
`scripts/fetch_ci_data.sh` pulls the small subset used by the tests. For real work see
[docs/DATA.md](docs/DATA.md) for every source and its terms, then build a library directly with the Rust binary:

```bash
actinv build-library /data/TENDL-2025/n /data/tendl2025-n.npz \
  --format tendl --projectile neutron --groups fispact-709 \
  --temperature-K 293.6 --workers 4 --cache /data/actinv-cache
python3 scripts/build_photon_response.py /data/nist-air-fe.json --elements Fe  # optional dose response
```

## Run

```bash
actinv validate examples/fns_fe_5min.json      # parse and check the specification
actinv run examples/fns_fe_5min.json out.json  # solve
```

```python
import actinv, json
result = json.loads(actinv.run(open("examples/fns_fe_5min.json").read()))
print(result["steps"][1]["heat_W_per_g"]["total"])       # decay heat after the first cooling step
print(result["pathways"][1]["Mn56"])                     # which chains made Mn-56, ranked
print(result["steps"][1]["photon_source"]["groups"])    # decay-photon multigroup source
print(result["ledger"])                                  # everything the calculation could not account for
```

Export a selected one-based step as a transport source:

```bash
actinv export-openmc out.json 2 source.py
actinv export-mcnp out.json 2 source.sdef
```

The ordinary-result exporters still emit a point at `(0, 0, 0)`. P8 mesh results carry source cell indices and bounds,
but constructing a distributed decay-photon transport source from them remains an explicit user workflow.

## Import transport flux and solve independent cells

```bash
# OpenMC statepoint-format 18. Other supported FORMAT values are meshtal, mctal and fispact.
actinv import-flux openmc statepoint.h5 flux.ndjson \
  --tally 7 --source-rate 1.0e15 --energy-floor-eV 1.0e-5

# mesh.json declares flux.ndjson's SHA-256 and one material/schedule for every cell.
actinv mesh mesh.json mesh-result.ndjson
```

Importers require explicit physical normalization and accept only the documented fail-closed subsets. See
[Specification](docs/SPEC.md) for commands, schemas and an `actinv-mesh-spec-1` example.

The command line, the Python module and the validation harness are **one binary reached three ways** — verified
identical to 0.0, which is what makes the certificate's solver field meaningful.

## What a specification looks like

See [docs/SPEC.md](docs/SPEC.md). In short: a library and decay data, a natural-element and/or explicit-nuclide
material, a group flux spectrum, an irradiation/cooling schedule, optional hash-pinned fission yields and optional
external photon-response data. Unknown fields are an error — a misspelt option is never silently ignored.

## Principles

- **Data are never bundled**; every input receives a computed SHA-256 in the run certificate, and declarations fail
  closed on a mismatch.
- **Fail closed, report everything.** Missing decay/yield data, unmapped fission products, yield balance/leakage,
  burn-up selection, pruned nuclides, round-off, and the method's own numerical resolution floor all appear in the
  ledger — see [docs/LEDGER.md](docs/LEDGER.md).
- **Protocols are hashed before evidence**, verdicts are derived by checkers, ledgers are append-only.
- **The validation harness accepts any code's inventories** — see [docs/HARNESS.md](docs/HARNESS.md).

## Documentation

[Method](docs/METHOD.md) · [Data sources and terms](docs/DATA.md) · [Validation](docs/VALIDATION.md) ·
[Harness](docs/HARNESS.md) · [Ledger](docs/LEDGER.md) · [Specification](docs/SPEC.md) ·
[Traps in activation data](docs/DATA_TRAPS.md) · [Roadmap](docs/ROADMAP.md) · [Release notes](docs/RELEASE_NOTES_v0.5.md)

## Contributing

Dual-licensed MIT OR Apache-2.0, contributions under the Developer Certificate of Origin (`git commit -s`).
A change to physics or data handling comes with a control that would have caught the bug, and a ledger entry.
See [CONTRIBUTING.md](CONTRIBUTING.md).
