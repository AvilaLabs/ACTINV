# Contributing to ACTINV

ACTINV is developed by Avila Labs under a protocol-first discipline: every gate is written and hashed before evidence
is gathered, verdicts are derived by checker scripts from stored results, ledgers are append-only, and every data gap
is recorded rather than silently dropped. Contributions are welcome under the same discipline.

- Licence: dual MIT OR Apache-2.0. By contributing you agree your contribution is licensed the same way.
- Sign-off: use the Developer Certificate of Origin (`git commit -s`). No CLA.
- Nuclear data are never committed. Point to the public host and the SHA-256; see docs/DATA.md.
- A change to physics or data handling comes with a control (a test that would have caught the bug) and a ledger entry.
- Controls live in `controls/`; the Rust core in `crates/`; the Python binding in `python/`.

Maintainer and coding-agent conventions are kept in
[`docs/maintainers/AGENTS.md`](docs/maintainers/AGENTS.md). Historical phase-session records are archived under
[`docs/history/sessions/`](docs/history/sessions/README.md); they are evidence, not user-facing setup material.

## Rust quality and ownership

Keep changes narrow and add regression tests for changed behavior. Do not introduce `unsafe` without maintainer
approval, and do not use unnecessary cloning, shared locking or interior mutability merely to bypass an ownership
problem. Explain the ownership constraint before proposing a structural architecture or concurrency change.

Run the same quality gates enforced by CI before submitting a Rust change:

```sh
cargo fmt --all -- --check
cargo check --workspace --all-targets --all-features
cargo clippy --workspace --all-targets --all-features -- -D warnings
cargo test --workspace --all-targets --all-features
```
