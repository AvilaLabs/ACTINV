# Troubleshoot setup and runs

Start with the installed version and the failing problem:

```bash
actinv --version
actinv doctor problem.json
actinv validate problem.json --hashes
```

`doctor` reports the current working directory and supported input-file checks. Hash validation supplements those checks; evaluated-data compatibility is checked during a run.

| Symptom | Check and action |
| --- | --- |
| `actinv` command not found | Confirm pip installed into the active Python environment and its scripts directory is on PATH; reopen the terminal after installing |
| Catalog artifact is not installed | Run `actinv data fetch` in the working folder, or set `ACTINV_DATA_DIR` to the installation root |
| Cannot open a literal data path | Check the current directory for CLI use, **Input base** in the desktop, or the problem's directory for `Problem.from_file` |
| Cannot open `<stem>_index.json` | Install the activation library and its matching adjacent index together; covariance needs its own index too |
| SHA-256 mismatch | Verify the selected bundle, data path, and library/index pairing; recover the exact declared input rather than removing its hash constraint |
| Covariance does not match activation data | Use the full/full or patched/patched pairing in [Data setup](data.md#choose-a-bundle) |
| Unknown field or invalid composition | Use the [specification reference](specification.md); check spelling, units, isotope aliases, and natural-element/isotope overlaps |
| Positive total with zero spectrum | Supply a nonzero spectrum shape before setting its total flux |
| Projectile, group, or temperature mismatch | Select a compatible library; standard charged-particle libraries use 162 groups and 0 K |
| Missing optional result | Confirm you requested the output and supplied its required data; absence is not a computed zero |
| Browser will not run or fetch data | Use desktop, CLI, or Python for calculations; the browser edits inputs and displays results |

## An existing downloaded file is wrong

`actinv data verify` reports installed-file failures. `fetch` leaves an incorrect existing file untouched by default. After confirming the intended bundle and destination, request verified replacement:

```bash
actinv data fetch --force
actinv data verify
```

For a custom installation, add `--output` with its root. For another bundle, name that bundle explicitly.

## A prepared cache is corrupt or incompatible

Stop calculations that use the affected cache, then remove the cache artifact or use a new, empty cache directory. ACTINV recreates prepared data from the original inputs. Cache removal affects preparation time; keep your original evaluated data, problems, and results.

Use an absolute `ACTINV_CACHE_DIR` on a disk with enough space if your normal cache location is unsuitable. Desktop runs use their own private caches.

## Small, zero, or unexpected responses

Inspect the ledger for missing material targets, leakage, pruned populations, mode selection, and optional response coverage. Check normalization, irradiation length, cooling time, and the selected library. A missing channel can produce a small prediction without causing a parser error.

Consult [Known data limitations](data-limits.md) before attributing a discrepancy to the solver. Include the software version, a minimal problem, the exact command, and the error message in a [bug report](https://github.com/AvilaLabs/ACTINV/issues). Share data identities or links rather than bulk nuclear-data files.
