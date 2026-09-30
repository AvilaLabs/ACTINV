# ACTINV-P89 — Python `budget` passes a budget file through verbatim

Date: 2026-09-29. Status: **frozen before the fix is written.**

## Why

P87 (`cb9f187e…`) passed G0, G1 and G3. It **failed G2**: for both P79 inputs, the Python and CLI
documents differ in exactly one field, `budget_sha256`. Every physics, verification and summary
field is equal. The CLI hashes the budget file's bytes. The Python wrapper parses a budget file and
serializes it again, so the native call hashes different text. The recorded provenance hash of a
file run from Python therefore does not identify the file. The P87 FAIL stands.

## Change under test

This is the P87 binding with one fix in `python/src/objects.py`. For a path, `budget` reads the
file's text and passes it to the native function unchanged. A mapping is still serialized with
`json.dumps(..., allow_nan=False)`, and its `budget_sha256` is the hash of that text; the docstring
and `docs/BUDGET.md` say so. There is one new unit test: for a file, `budget_sha256` equals the
SHA-256 of the file's bytes, and the file is written with formatting that `json.dumps` would not
reproduce. The Rust code is unchanged from P87.

## Gates

These are the P87 gates, applied to this change. Checker `controls/check_p89.py` writes
`results/p89_verdict.json`, and its logs are under `target/p89/`.

- **G0:** the protocol hash is registered before the fix is written.
- **G1:** Python crate fmt and clippy clean; the wheel builds; `scripts/test_python_objects.py`
  passes, including the P87 tests and the new hash test.
- **G2:** for `controls/p79/ss316ln_exvessel.json` and `controls/p79/eurofer97_exvessel.json` with
  verification on, the Python and CLI documents are equal after removing `ms` and `elapsed_ms` at
  every depth, and the verification outcomes agree. `budget_sha256` is included in the comparison.
- **G3:** every step of the local CI replay exits 0.

Merge only if G0–G3 all pass.
