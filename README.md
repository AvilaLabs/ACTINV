# ACTINV

[![CI](https://github.com/AvilaLabs/ACTINV/actions/workflows/ci.yml/badge.svg)](https://github.com/AvilaLabs/ACTINV/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/actinv?label=PyPI)](https://pypi.org/project/actinv/)
[![crates.io](https://img.shields.io/crates/v/actinv-cli?label=crates.io)](https://crates.io/crates/actinv-cli)
[![License: MIT OR Apache-2.0](https://img.shields.io/badge/license-MIT%20OR%20Apache--2.0-blue.svg)](#contributing-and-licence)

**Open, reproducible activation and nuclide-inventory calculations from the command line or Python.**

ACTINV answers a practical question: what radioactive nuclides are created when a material is irradiated, and what
happens to them afterward? Give it a material, particle-flux spectrum, irradiation and cooling schedule, and evaluated
nuclear data. It returns inventories, activity, decay heat, photon sources, selected radiological responses, and an
explicit record of anything the supplied data could not account for.

It is useful for activation studies, shutdown inventories, source-term preparation, and reproducible comparisons with
other inventory codes. The numerical core is written in Rust, with both a Python interface and a standalone command.

[PyPI](https://pypi.org/project/actinv/) · [crates.io](https://crates.io/crates/actinv-cli) ·
[v1.0.0 software release](https://github.com/AvilaLabs/ACTINV/releases/tag/v1.0.0) ·
[v1.0.0 nuclear-data release](https://github.com/AvilaLabs/ACTINV/releases/tag/data-v1.0.0) ·
[Documentation](#documentation)

> ACTINV is research-grade software. Version 1.0 is a technically validated release, not an approval for licensing,
> safety, waste classification, or regulatory decisions. See [Qualification boundary](docs/QUALIFICATION.md) before
> using results in a formal analysis chain.

## Quick start

### 1. Install ACTINV

For most users, PyPI is the simplest option. One command installs both the Python interface and the `actinv` terminal
command on Python 3.9 or newer:

```bash
pip install actinv
```

Prebuilt wheels are available for supported systems, so this route does not require a Rust compiler. If you already
use Rust and only want the command-line program, install it directly from crates.io:

```bash
cargo install --locked actinv-cli
```

Prebuilt standalone executables are also attached to the
[v1.0.0 GitHub Release](https://github.com/AvilaLabs/ACTINV/releases/tag/v1.0.0).

Confirm the installation at any time with `actinv --version` or explore every command with `actinv --help`.

### 2. Fetch nuclear data

The software package stays small by keeping nuclear data in a separate, versioned release. Download the recommended
neutron data in one command:

```bash
actinv data fetch
```

The command downloads about 139 MiB, verifies every file with SHA-256 before installing it under
`actinv-data/v1.0.0/`, and prints the exact paths to paste into a problem. Nothing is silently updated: a later data
release goes in a new version directory. See [Data setup](docs/DATA.md) for the other particle and covariance bundles.

### 3. Run a calculation

An ACTINV problem is a JSON file containing the data paths printed by `actinv data fetch`, a material composition, a
group flux spectrum, and an irradiation/cooling schedule. Validate it first, then run it:

```bash
actinv validate problem.json
actinv run problem.json result.json
```

The same calculation from Python is:

```python
from pathlib import Path
import json
import actinv

problem = Path("problem.json").read_text(encoding="utf-8")
print(actinv.validate(problem))

result = json.loads(actinv.run(problem))
last_step = result["steps"][-1]
print("decay heat (W/g):", last_step["heat_W_per_g"]["total"])
print("activity (Bq/g):", sum(last_step["activity_Bq_per_g"].values()))
print("unaccounted inputs:", result["ledger"])
```

Start with [the specification guide](docs/SPEC.md), which explains every field and includes complete examples. Unknown
fields are rejected, so misspelled options do not silently change a calculation. At any time, verify the installed
data without downloading it again:

```bash
actinv data verify
```

## Preparing an activation library

The standalone command builds deterministic libraries from supported ENDF-6 evaluations. For example:

```bash
actinv build-library /data/TENDL-2025/n /data/tendl2025-n.npz \
  --format tendl --projectile neutron --groups fispact-709 \
  --temperature-K 293.6 --workers 4 --cache /data/actinv-cache
```

For MF=33 cross-section uncertainty information:

```bash
actinv build-covariance /data/TENDL-2025/n /data/tendl2025-n.npz \
  /data/tendl2025-n.cov.npz --workers 4 --cache /data/actinv-cov-cache
```

Every input file is SHA-256 hashed in the result certificate. Generated bulk libraries and raw nuclear-data inputs are
not stored in this repository.

## Transport-code workflows

ACTINV accepts a spectrum from any source. It also has strict importers for documented subsets of OpenMC statepoints,
MCNP MESHTAL/MCTAL files, and FISPACT flux files. Normalization must always be stated explicitly:

```bash
actinv import-flux openmc statepoint.h5 flux.ndjson \
  --tally 7 --source-rate 1.0e15 --energy-floor-eV 1.0e-5
actinv mesh mesh.json mesh-result.ndjson
```

Selected decay-photon steps can be exported as OpenMC or MCNP source fragments:

```bash
actinv export-openmc result.json 2 source.py
actinv export-mcnp result.json 2 source.sdef
```

Mesh cells are solved independently. ACTINV does not perform particle transport, spatial coupling, criticality, or
thermal-hydraulic feedback.

## What the result tells you

Depending on the requested outputs, each time step can contain:

- nuclide atoms and activity;
- total and alpha/beta/gamma decay heat;
- evaluated decay-photon lines or multigroup sources;
- ranked production pathways;
- user-selected clearance, waste, ingestion, or inhalation responses;
- an MF=33 cross-section uncertainty band and numerical-method comparison; and
- a ledger for missing decay/yield/response data, pruned populations, balance terms, and method resolution.

Radiological coefficients are user-supplied, hash-pinned tables. ACTINV does not choose a jurisdiction, regulation,
chemical form, aerosol class, or safety margin for you.

## Validation in brief

The repository carries executable controls and compact evidence for every release phase. Highlights include:

- 132 IAEA FNS decay-heat experiments across 73 materials;
- independent decay-photon, transport-import, fission-yield, pulse-history, and mesh checks;
- complete deterministic TENDL-2025 neutron/proton/deuteron/alpha and EAF-2010 library builds;
- independent CRAM, sensitivity, MF=33 covariance, and uncertainty-propagation checks;
- a fixed one-million-case reliability check across all supported public input readers; and
- an independently reproduced FNG/ITER cell-620 activation history at all 170 time points.

Validation establishes behavior for the recorded models, inputs, and acceptance bounds. It does not establish that a
particular user's material, spectrum, nuclear-data choice, radiological table, or regulatory scenario is suitable.
See [Validation](docs/VALIDATION.md), [v1.0 release notes](docs/RELEASE_NOTES_v1.0.md), and the
[qualification boundary](docs/QUALIFICATION.md) for the evidence and complete limitations.

## Design principles

- **Inputs stay attributable.** Nuclear data are distributed separately under their own terms, verified before use,
  and recorded by hash in every calculation.
- **Incomplete information stays visible.** Missing data and approximations appear in the ledger.
- **Interfaces share one solver.** CLI, Python, prepared, and mesh paths are checked for scientific identity.
- **Evidence is reproducible.** Protocols are frozen before results; repair records are append-only.

## Documentation

[Specification](docs/SPEC.md) · [Data sources](docs/DATA.md) · [Method](docs/METHOD.md) ·
[Validation](docs/VALIDATION.md) · [Qualification boundary](docs/QUALIFICATION.md) ·
[Ledger](docs/LEDGER.md) · [Harness](docs/HARNESS.md) · [Roadmap](docs/ROADMAP.md) ·
[v1.0 release notes](docs/RELEASE_NOTES_v1.0.md)

## Contributing and licence

ACTINV is dual-licensed under MIT or Apache-2.0. Contributions use the Developer Certificate of Origin. Physics or
data-handling changes need a regression control and an append-only evidence record; see
[CONTRIBUTING.md](CONTRIBUTING.md).
