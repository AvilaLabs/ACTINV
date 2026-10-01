# Your first calculation

Run the supplied iron example from a terminal. This example models a five-minute irradiation followed by cooling and includes the complete measured flux-spectrum shape. You do not need to clone the repository or type a 709-group vector.

## 1. Prepare a working folder

After [installing ACTINV](install.md), create and enter an empty folder for this calculation:

```bash
mkdir actinv-example
cd actinv-example
actinv --version
```

## 2. Install data and create the problem

```bash
actinv data fetch
actinv new problem.json
```

`new` writes a complete, editable problem and refuses to overwrite an existing file. Its default data references use catalog IDs so the problem can be used on another machine with the same installed data.

## 3. Validate and run

```bash
actinv validate problem.json
actinv validate problem.json --hashes
actinv run problem.json result.json
```

The first validation checks the JSON specification. `--hashes` also checks the files it supports and their declared hashes; the solver checks evaluated-data compatibility during the run. A successful run writes the complete result to `result.json`.

The first run prepares data and can take longer than subsequent runs. If setup fails, run `actinv doctor problem.json` and follow [Troubleshooting](troubleshooting.md).

## 4. Inspect the final step

If you installed the Python package, inspect the JSON with:

```python
import json
from pathlib import Path

result = json.loads(Path("result.json").read_text(encoding="utf-8"))
last = result["steps"][-1]
print("Time (s):", last["t_s"])
print("Activity (Bq/g):", sum(last["activity_Bq_per_g"].values()))
print("Decay heat (W/g):", last["heat_W_per_g"]["total"])
print("Data and calculation notes:", result["ledger"])
```

You can also open the result in the [browser workbench](browser.md) to explore plots. [Read your results](results.md) explains the units, time steps, and diagnostic fields.

## 5. Adapt the example

Open `problem.json` in a text editor. Change the title, material composition, or schedule, then validate and run again with a new output filename. Reuse the supplied spectrum only when it represents your intended irradiation; changing the material does not make that spectrum appropriate for a different facility.

Use [Describe a problem](problems.md) for input conventions. The [measured iron case](https://github.com/AvilaLabs/ACTINV/tree/master/examples/fns_iron) provides a fuller comparison against experimental decay-heat measurements.
