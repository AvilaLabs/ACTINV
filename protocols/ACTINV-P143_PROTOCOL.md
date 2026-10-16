# P143 — Waste operations in the Python API

Registered 2026-10-16, before any source change for this phase. Base: the P142 candidate `5ee489c`. Owner direction
2026-10-16: finish the waste program; the roadmap's accepted item "Waste Python APIs" (open-items inventory of
2026-10-03) is this phase.

## Change under test

`actinv.waste` in the Python package exposes the six native waste operations, and `actinv.twin` exposes the facility
twin (with its optional waste block):

| Python | CLI |
|---|---|
| `actinv.waste.classify(spec)` | `actinv waste SPEC` |
| `actinv.waste.budget(spec)` | `actinv waste budget SPEC` |
| `actinv.waste.bounds(spec)` | `actinv waste bounds SPEC` |
| `actinv.waste.composition(spec)` | `actinv waste composition SPEC` |
| `actinv.waste.composition_solve(spec)` | `actinv waste composition solve SPEC` |
| `actinv.waste.intrusion_screen(spec)` | `actinv waste intrusion-screen SPEC` |
| `actinv.twin(spec)` | `actinv twin SPEC` |

`spec` is a path, or a dict together with `base_dir` (the directory its relative paths resolve against; default the
current directory). Each call returns the result as a dict, or with `text=True` the exact JSON text the CLI writes to
an output file. A refusal raises `ValueError` carrying the CLI's message. Calls print nothing.

Each binding runs the CLI's own entry point (`actinv_cli::waste::run` and siblings) with an output file in a private
temporary directory and returns that file's bytes, so the Python and CLI results come from one code path. A dict spec
is written to a uniquely named temporary file inside `base_dir`, so its relative paths resolve as the CLI's would,
and removed afterwards, also on error. `actinv.waste.summary(result)` flattens a classification result to one row per
component and target step (class, governing table and column, margin, binding nuclides, coverage status); it reads
only the result and computes nothing new.

No change to any waste rule, evaluator, schema or CLI behaviour. The waste and twin Rust modules change only if a
binding needs a public entry point; any such change must leave every CLI output byte-identical, which W1 checks.

## Required evidence

Only the coordinator runs executable jobs, serially, under the required systemd cgroup, with disk-backed `TMPDIR`.

- **W1, byte parity over the sealed campaigns.** A recording wrapper (`controls/p143_recorder.py`) stands in for
  `ACTINV_BIN` while the CI steps for P105, P107, P108, P110, P112 and P116 run unchanged. It runs the real release
  binary with the exact argv, stdin, stdout, stderr and exit, so the checkers see the CLI; for every `waste ...` and
  `twin ...` invocation it then calls the matching Python function on the same input file, before returning. For a
  CLI success with an output path, the Python `text=True` result must equal that file's bytes; without one, the CLI's
  stdout must equal that text, followed by one newline when the text does not already end in one. For a CLI refusal,
  Python must raise `ValueError` and the CLI's stderr must be its message followed by one newline. Two kinds of
  invocation have no Python counterpart and are logged with their reason but not compared: refusals of the argv shape
  (exit 2) and refusals about the CLI's own output target (message beginning `cannot write`). Every other waste/twin
  invocation of the campaigns is compared and logged to `results/p143/w1_parity.jsonl`. Pass: all six steps exit 0,
  at least one compared success per Python function, and no mismatch.
- **W2, dict input.** For one success of each function, the dict form (with `base_dir`) returns the same bytes as the
  path form, and leaves no file behind in `base_dir`, including after a refused call.
- **W3, quiet and clean.** At file-descriptor level a call writes nothing to stdout or stderr, and the private
  temporary directory is gone after success and after refusal.
- **W4.** The Python binding checks (`cargo clippy` for the binding crate, the module build and its tests), the
  workspace tests, fmt and clippy `-D warnings` (native and wasm) pass; CI is green on the pushed commit for every
  triggered workflow.

## Verdict

P143-PASS if W1–W4 hold. A failing gate is preserved under `results/failures/p143/`; one repair round, registered
as an amendment first.

## Not claimed

The Python API adds no capability beyond the CLI's and inherits its scope: nominal classification and screening, not
disposal acceptance, intrusion dose or legal compliance.
