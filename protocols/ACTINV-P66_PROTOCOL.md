# ACTINV-P66 — Python SDK coverage for the certified surfaces (C4)

## Question

The `actinv` Python package already ships `Problem`/`Material`/
`Schedule`/`Spectrum`/`solve`/`Result`/`reverse` — but the capabilities
that differentiate the product (P64 `decide`, P49/P56 `optimize`, P65
`screen`) were CLI-only. Can the SDK expose them at the same contract
level with zero behavior divergence?

## Deliverable

- `actinv.decide(decision, base_dir=None)` — `actinv-decide-1` mapping
  or file path → `actinv-decision-1` document, via a new doc-level
  `run_decide_doc(dtext, base_dir, label)` split out of `run_decide`
  (the CLI command is unchanged).
- `actinv.optimize(optspec_path, outdir=None, resume=False)` — runs the
  search and returns the full `optimize_result.json` document.
- Certified screening needs no new binding: `options.screen` is a spec
  field and flows through `solve` unchanged.
- `objects.py` wrappers + `decide_json`/`optimize_json` low-level
  aliases (same pattern as `run_json`/`reverse_json`).

## Honesty rules

- The bindings call the same entry points as the CLI — identical
  documents, identical refusals (`decide` still rejects unbanded runs,
  `optimize` still writes the append-only ledger).
- No Python-side reimplementation of any check.

## Gates

- **G1 mechanics** — `decide` on all three input arms (dict, embedded
  run_spec, file path) reproduces the CLI decision document;
  `optimize` returns the result doc with `certification` present;
  `solve` on a screened spec emits the `screen` block.
- **G5 checker** — refusal paths identical from Python (unbanded decide
  spec → same error text as the CLI).
