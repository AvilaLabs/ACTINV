# JADE code-target prototype for ACTINV

`jade-actinv-code-target.patch` adds ACTINV as a runnable code target to
[JADE](https://github.com/JADE-V-V/JADE), the F4E/UKAEA/UNIBO nuclear-data
verification & validation framework. Status: **working prototype**, developed
against JADE `master` (September 2026) in support of the discussion at
`JADE-V-V/JADE` issue #548.

## What it implements

JADE's documented four touch points for a new code:

- `CODE.ACTINV` enum tag (`helper/constants.py`)
- `LibraryACTINV` (`config/run_config.py`): a JADE "library" pointing at an
  ACTINV data bundle directory (`activation/` + `decay/`); available zaids are
  read from the bundle's `*_index.json`
- `InputACTINV` (`run/input.py`): loads an `actinv-spec-1`/`actinv-mesh-spec-1`
  JSON template; `translate()` repoints non-`catalog:` `library.path` (with a
  recomputed sha256) and `decay.primary`/`fallback` file references at the
  selected bundle, so a benchmark spec runs against whichever data version is
  selected in the JADE library config
- `SingleRunACTINV` (`run/benchmark.py`): builds
  `actinv run <spec>.json result.json` and exports `ACTINV_DATA_DIR` for
  `catalog:` resolution; `nps` is a no-op (deterministic solver)
- `ActinvSimOutput` (`post/sim_output.py`): parses `result.json` into JADE's
  standardized tally DataFrames — tally 1 = total decay heat (W/g) and tally
  2 = total activity (Bq/g) binned on `time` (s); wired into `RawProcessor`

One generalization: `BenchmarkRunConfig` now tolerates a benchmark `codes:`
map missing a code tag (`actinv` isn't required in every existing config).

## Apply

```bash
git clone https://github.com/JADE-V-V/JADE.git && cd JADE
git apply /path/to/jade-actinv-code-target.patch
pip install -e .
```

## Verified

Against the clone in this workspace: `tests/test_actinv.py` (5 tests) passes,
and the touched modules' existing tests (`test_input`, `test_benchmark`,
`test_sim_output`, `test_run_config`) all pass — 24 passed, 7 skipped
(openmc/tkinter not installed). A real `actinv run` driven through
`SingleRunFactory` + `InputACTINV.translate()` solved the FNS Fe 5-minute spec
and `ActinvSimOutput` produced the expected time×heat DataFrame.

## Not done (needs upstream direction)

- A benchmark definition (raw/excel/atlas YAML configs + hosted inputs) — the
  natural candidate is FNS decay heat: per-foil `actinv-spec-1` templates with
  measured C/E comparison
- Benchmark `metadata.json` convention for an `actinv` version entry
- Whether activation-code benchmarking belongs in JADE at all — that's the
  question on issue #548
