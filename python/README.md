# ACTINV for Python

ACTINV calculates how a material's nuclide inventory changes during irradiation and cooling. It reports inventories,
activities, decay heat, photon sources, selected radiological indices, NRT damage observables (dpa), uncertainty
information, and an explicit ledger of incomplete input data.

Installation is one command:

```bash
pip install actinv
```

That installation provides both `import actinv` and the `actinv` terminal command. No Rust compiler is needed when a
wheel is available for the platform.

```python
import actinv
import json

with open("problem.json", encoding="utf-8") as source:
    result = json.loads(actinv.run(source.read()))

print(result["steps"][-1]["heat_W_per_g"]["total"])
```

Nuclear-data libraries are distributed separately from the Python wheel. Install and verify the recommended versioned
bundle with `actinv data fetch` and `actinv data verify`; each calculation records hashes for
the selected files. See the [project README](https://github.com/AvilaLabs/ACTINV#readme) for the quick start, download
instructions, examples, validation evidence, and qualification boundary.

ACTINV is research-grade software. It is not approved for licensing, safety, or regulatory decisions.

## Object interface

```python
from pathlib import Path
from actinv import Problem, Material, Schedule, solve

problem = Problem.example(data_dir=Path("actinv-data"))
problem["material"] = Material({"Fe": 100}, mass_g=10)
problem["schedule"] = Schedule().irradiate("5 min").cool("1 h")
problem.save("iron.json")
result = solve(problem)  # Also accepts Path("iron.json") or a plain dictionary.
print(result.heat())      # (seconds, W/g)
print(result.activity())  # (seconds, total Bq/g)
result.save("iron-result.json")
```

`Problem.from_file(path)` resolves data references against the problem's directory; supply `base=` for older
repository-relative files. Plain mappings use the current working directory. `Spectrum(values, structure=…,
total=…, descending=…)` provides explicit spectrum construction. Complete result fields remain accessible by key.
Existing `run(json_text)` still returns JSON text; `run_json` is an explicit alias. `solve` returns `Result`.

Schedule steps may carry continuous feed and first-order removal:

```python
problem["schedule"] = Schedule().irradiate("5 min", feed={"Co60": 1e12}, removal={"Mn56": 1e-3}).cool("1 h")
```

`actinv.reverse(problem, measurements, segments=False)` — or `actinv-reverse` on the command line — recovers a flux
multiplier (or per-segment multipliers) from measured activities in the linear regime. Damage observables are
requested with `options.outputs` containing `"damage"` plus a `damage` section pinning an
`actinv-damage-table-1` file and per-element displacement energies.
