# ACTINV for Python

ACTINV calculates how a material's nuclide inventory changes during irradiation and cooling. It reports inventories,
activities, decay heat, photon sources, selected radiological indices, uncertainty information, and an explicit ledger
of incomplete input data.

After the v1.0 package is published, installation is one command:

```bash
pip install actinv
```

```python
import actinv
import json

with open("problem.json", encoding="utf-8") as source:
    result = json.loads(actinv.run(source.read()))

print(result["steps"][-1]["heat_W_per_g"]["total"])
```

Nuclear-data libraries are distributed separately from the Python wheel. Install and verify the recommended versioned
bundle with the standalone command's `actinv data fetch` and `actinv data verify`; each calculation records hashes for
the selected files. See the [project README](https://github.com/AvilaLabs/ACTINV#readme) for the quick start, download
instructions, examples, validation evidence, and qualification boundary.

ACTINV is research-grade software. It is not approved for licensing, safety, or regulatory decisions.
