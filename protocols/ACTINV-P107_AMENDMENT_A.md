# P107 Amendment A — test-module ordering lint repair (2026-10-03)

The required workspace Clippy gate on Rust 1.98 stopped with
`clippy::items_after_test_module`: the CLI `waste_bounds.rs` module put the
production `run_doc` function below its test module. Retain the complete failed
log at `results/p107_g3_clippy_attempt_1.log`. No P107 CLI class-control outcome
has been observed and G0 has not yet sealed.

Use the one repair round to move the unchanged `run_doc` item before the test
module. No function body, arithmetic, schema, predicate, fixture, rule value,
acceptance tolerance or scientific claim changes. Register this amendment
before the move; bind it in G0 and the derived verdict. Re-run the final source
quality gates and build the release CLI before controls. Preserve all prior
failed phases and P105 PASS. A further failed gate closes P107-FAIL and needs a
new successor protocol.
