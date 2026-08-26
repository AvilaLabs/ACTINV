# ACTINV agent working agreement

- Keep changes small and scoped. Do not redesign architecture merely to satisfy the borrow checker; explain the
  ownership constraint before making a structural ownership or concurrency change.
- Do not introduce `unsafe` without explicit maintainer approval. Avoid unnecessary `clone()`, `Arc`, `Mutex`,
  interior mutability, or allocation as compiler workarounds.
- Consult the pinned dependency source or current official crate documentation before relying on an unfamiliar API.
- Add a regression test for behavior changes. Physics and data-handling changes also require an independent control
  when practical and must follow the frozen protocol and append-only ledger discipline.
- Before treating a Rust checkpoint as complete, run:

  ```text
  cargo fmt --all -- --check
  cargo check --workspace --all-targets --all-features
  cargo clippy --workspace --all-targets --all-features -- -D warnings
  cargo test --workspace --all-targets --all-features
  ```

- Never commit nuclear-data inputs or generated bulk libraries; record public provenance and SHA-256 hashes instead.
