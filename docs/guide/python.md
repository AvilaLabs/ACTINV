# Python

Install with `python -m pip install actinv`, then install the data from your working folder with `actinv data fetch`. Python calls the same Rust solver as the CLI.

## Construct a problem

```python
from actinv import Material, Problem, Schedule, solve

problem = Problem.example()
problem["material"] = Material({"Fe": 100.0}, mass_g=10.0)
problem["schedule"] = Schedule().irradiate("5 min").cool("1 h")
problem.save("iron.json")

result = solve(problem)
print(result.heat())         # List of (seconds, W/g) pairs.
print(result.activity())     # List of (seconds, total Bq/g) pairs.
print(result.activity("Mn56"))
result.save("iron-result.json")
```

The example includes the iron spectrum and data references. `Material` keeps the declared composition basis; it does not silently convert weight percentages to fractions. `Schedule` methods return the same schedule for chaining. Use `Spectrum` when supplying your own group-integrated flux vector.

`mass_g=10.0` does not change the unit returned by `heat()`: multiply W/g by ten to obtain this sample's total watts.

## Load an existing problem

```python
from pathlib import Path
from actinv import Problem, solve

problem = Problem.from_file("iron.json")
result = solve(problem)
# You can also use: result = solve(Path("iron.json"))
print(result.steps[-1]["heat_W_per_g"]["total"])
print(result.ledger)
```

`Problem.from_file` resolves literal relative data references against the file's directory. Supply `base=` when an older problem expects a different base folder. Plain dictionaries use the current working directory. Catalog references use `ACTINV_DATA_DIR` or `./actinv-data`.

`Result` is a mapping: every result field remains accessible by key. Its helpers select existing data and preserve the reported units.

## Use the JSON interface

```python
import json
from pathlib import Path
import actinv

text = Path("problem.json").read_text(encoding="utf-8")
print(actinv.validate(text))
result = json.loads(actinv.run(text))
print(result["steps"][-1]["heat_W_per_g"]["total"])
```

`run` and its alias `run_json` accept JSON text and return JSON text. Literal paths in this interface use the current directory. `solve` returns a `Result` object instead.

## Optional features

Set the corresponding problem blocks to request uncertainty, photon responses, radiological indices, damage, or self-shielding. Continuous feed and removal can be attached to schedule steps:

```python
problem["schedule"] = (
    Schedule()
    .irradiate("5 min", feed={"Co60": 1e12}, removal={"Mn56": 1e-3})
    .cool("1 h")
)
```

Feed units are atoms s⁻¹ g⁻¹ and removal units are s⁻¹. Other helpers include `reverse`, `decide`, and `optimize`; current master also adds `actinv.budget(budget, base_dir=None, verify=True)`. A failed budget verification is returned in the document rather than raised as an exception. See the [Python package reference](https://github.com/AvilaLabs/ACTINV/blob/master/python/README.md), [Advanced workflows](workflows.md), and [release availability](releases.md#current-master).
