# ACTINV-P87 — Python binding for `actinv budget`

Date: 2026-09-29. Status: **frozen before the binding is written.**

## Change under test

Branch `p87-work` from master `ba97c5d`. It exposes `actinv budget` (P79) in the Python module, in
the same pattern as `decide`:

- Native `budget(budget_json: str, base_dir: str | None = None, verify: bool = True) -> str`, in
  `python/src/lib.rs`. It calls `actinv_cli::budget::run_budget_doc(text, base_dir or ".",
  verify)` with the GIL released and returns the `actinv-budget-result-1` document as JSON text.
  Errors raise `RuntimeError`, as `decide` does.
- In `python/src/objects.py`, `budget(budget, *, base_dir=None, verify=True) -> dict` takes a
  mapping, or a path to an `actinv-budget-1` file. For a path, `base_dir` defaults to the file's
  directory, so a relative `base_spec` resolves as it does on the command line. The native function
  is also exported as `budget_json`, and both names are added to `__all__`.
- A failed verification is not an exception. The document is returned and its `verification`
  block says so; the CLI's exit code 3 has no Python equivalent. The docstring and
  `docs/BUDGET.md` say this.
- Unit tests in `scripts/test_python_objects.py`: schema errors raise `RuntimeError`, and the
  mapping and path forms give the same document apart from timing keys, on a fixture that runs in
  the CI's Python step.

The solver and the CLI are unchanged.

## Gates

Checker `controls/check_p87.py`, under the 6 GB cgroup cap.

- **G0:** the protocol hash is registered before the binding is written.
- **G1 static:** `cargo fmt --check` and `cargo clippy -D warnings` on the Python crate
  (`python/Cargo.toml`). A release wheel builds with `maturin build --release --locked`, and
  `scripts/test_python_objects.py` passes with that wheel installed, including the new tests.
- **G2 parity:** for `controls/p79/ss316ln_exvessel.json` and `controls/p79/eurofer97_exvessel.json`
  with verification on, the documents from `actinv.budget(path)` and from `target/release/actinv
  budget path out.json` are equal as parsed JSON. Before comparing, the timing keys `ms` and
  `elapsed_ms` are removed at every depth. Both must also show the same verification outcome.
- **G3 CI replay:** every step of the local CI replay exits 0.

Merge only if G0–G3 all pass.
