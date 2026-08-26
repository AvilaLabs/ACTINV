# ACTINV

**Open, standalone, activation-grade nuclide-inventory solver.** A neutron flux spectrum from any source — MCNP, PHITS,
Serpent, OpenMC, or a measurement — plus a material and an irradiation history gives the nuclide inventory, activity,
decay heat and evaluated decay-photon source over cooling time, with a ledger of everything the calculation could not
account for.

Rust core, Python API, code-agnostic validation harness. Avila Labs, Oviedo, Florida.
Licence: **MIT OR Apache-2.0**. Latest release: **v0.1.0**; P7 photon-source work is complete on the development branch.

> Research-grade software. Validated against the 132-experiment FNS decay-heat benchmark (see below), but **not**
> validated for licensing, safety or regulatory decisions. Known limitations are listed in
> [docs/RELEASE_NOTES_v0.1.md](docs/RELEASE_NOTES_v0.1.md) — the code reports each of them rather than hiding it.

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
[docs/DATA.md](docs/DATA.md) for every source and its terms, then build a library:

```bash
scripts/build_library.sh <endf-files-dir> <out-dir> <name> 1 5     # resumable, memory-capped
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

The exported point at `(0, 0, 0)` is a spatial placeholder. P7 supplies energy and total strength; P8 will supply
flux-import and mesh/spatial coupling.

The command line, the Python module and the validation harness are **one binary reached three ways** — verified
identical to 0.0, which is what makes the certificate's solver field meaningful.

## What a specification looks like

See [docs/SPEC.md](docs/SPEC.md). In short: a library and decay data, an elemental material, a group flux spectrum, an
irradiation/cooling schedule and optional external photon-response data. Unknown fields are an error — a misspelt
option is never silently ignored.

## Principles

- **Data are never bundled**; every input receives a computed SHA-256 in the run certificate, and declarations fail
  closed on a mismatch.
- **Fail closed, report everything.** Missing decay data, unmapped products, fission without yields, pruned nuclides,
  round-off, and the method's own numerical resolution floor all appear in the ledger — see [docs/LEDGER.md](docs/LEDGER.md).
- **Protocols are hashed before evidence**, verdicts are derived by checkers, ledgers are append-only.
- **The validation harness accepts any code's inventories** — see [docs/HARNESS.md](docs/HARNESS.md).

## Documentation

[Method](docs/METHOD.md) · [Data sources and terms](docs/DATA.md) · [Validation](docs/VALIDATION.md) ·
[Harness](docs/HARNESS.md) · [Ledger](docs/LEDGER.md) · [Specification](docs/SPEC.md) ·
[Traps in activation data](docs/DATA_TRAPS.md) · [Roadmap](docs/ROADMAP.md) · [Release notes](docs/RELEASE_NOTES_v0.1.md)

## Contributing

Dual-licensed MIT OR Apache-2.0, contributions under the Developer Certificate of Origin (`git commit -s`).
A change to physics or data handling comes with a control that would have caught the bug, and a ledger entry.
See [CONTRIBUTING.md](CONTRIBUTING.md).
