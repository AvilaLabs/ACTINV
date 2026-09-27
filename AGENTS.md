# Local development safety

Read `docs/maintainers/AGENTS.md` for the scientific and maintainer rules.

An unfiltered recursive launch of a Rust test executable exhausted this laptop's
memory on 2026-09-10. These rules apply to all agents working in this checkout:

- Subagents may inspect and edit code; only the coordinating agent runs builds,
  executable tests, benchmarks, or solver jobs. Run one such job at a time.
- On this Linux workstation, run those jobs inside an enforced systemd cgroup:
  `systemd-run --user --scope -p MemoryMax=6G -p MemorySwapMax=0 -p TasksMax=128 -p CPUQuota=200% -- env CARGO_BUILD_JOBS=1 RUST_TEST_THREADS=1 RAYON_NUM_THREADS=2 COMMAND ...`
- If cgroup limits cannot be enforced, stop. Do not fall back to an unlimited
  command. Verify limits with a read-only cgroup inspection, never by allocating
  memory until the OOM killer fires.
- Put temporary build artifacts on disk, not a RAM-backed `/tmp`. For preflight,
  create `target/preflight-tmp` and set `TMPDIR` to its absolute path inside the
  bounded scope. The isolated snapshot still excludes unrelated untracked files.
- Do not run a Rust test's `current_exe()` without an explicitly isolated,
  reviewed test-child entry point. Application worker launching is disabled
  under `cfg(test)`; integration tests must use a separately built application.
- Review process-spawning code before executing it. Require bounded waits,
  child termination/reaping, and regression coverage for cancellation races.
- Do not terminate or reconfigure another assistant's processes. Coordinate
  shared workload limits with the user if contention is observed.

These are local workstation protections, not replacements for CI or scientific
qualification. Never claim that an unexecuted check passed.

# Commit policy

Strict no-AI-authorship: commits carry plain messages only — never add
"Generated with", "Co-Authored-By", or any agent/tool attribution trailers,
names, or links. Author and committer stay the repo owner's identity.

# CI verification (owner direction, 2026-09-26)

- After every `git push`, verify the GitHub Actions run on the pushed commit
  reports green before treating the push as done (`gh run list` /
  `gh run view` on the new SHA — the controls workflow especially).
- A red check on any pushed commit must be fixed before starting new feature
  work. No new red X's.
- Before pushing, run locally whatever cheap gates CI runs first (tracked-file
  manifest refresh via `scripts/refresh_manifest.py --write --index`,
  `cargo fmt --all -- --check`) so obvious failures never reach CI.
- Ordering that actually works (learned 2026-09-27, three CI reds): stage the
  code changes FIRST (`git add <files>`), THEN run
  `scripts/refresh_manifest.py --write --index` — it hashes staged/indexed
  content, so running it before staging records the old hashes. Confirm
  `MANIFEST.sha256` appears in the staged diff before committing.
- Local checks must include test code: `cargo build` does not compile
  `#[cfg(test)]` modules. Run `cargo check -p actinv-cli --tests` (or the
  equivalent for touched crates) before pushing — a struct-literal missing a
  new field in test helpers passed local build and broke CI's `cargo test`.
- Local clippy (1.95) lags CI's toolchain (1.98); new lints can land
  unheralded. `cargo clippy --all-targets` helps; when CI fails on a lint
  that doesn't exist locally, fix the lint anyway and note the gap —
  a rustup toolchain update closes it for good.
- Controls checkers resolve the binary as `ACTINV_BIN` or
  `target/release/actinv`, NOT `target/debug/actinv`. Before running a
  checker locally after changing emitted-result fields (ledger keys,
  certificate block), rebuild release: `cargo build --release -p actinv-cli`
  — a stale release binary silently reproduces the pre-change baseline and
  hides committed-baseline drift that `git diff --exit-code` catches in CI.
- Adding unconditional ledger/certificate fields changes
  `normalized_result_sha256` in run-output controls
  (`results/p15_cache_integrity.json` is diff-gated in CI). Regenerate the
  baseline with the fresh release binary and commit it — the invariants
  stay green; only the schema hash moves.

# User preferences (owner direction, 2026-09-21)

- Do not count reproducibility/provenance/auditability as a competitive
  advantage or selling axis when evaluating "best in class". Users do not
  care about it. Keep the SHA chains and defect ledgers — they catch real
  upstream bugs — but treat them as internal QA, not leadership levers.
  The axes that matter are accuracy, speed, and capability breadth.

- Future public docs site (owner picked the look, 2026-09-25): **mdBook**,
  rust theme — i.e. the rust-analyzer manual style, not Sphinx/MkDocs.
  Untracked demo scaffold left in place: `book.toml` + generated
  `docs/SUMMARY.md` (grouping is auto-guessed, needs curation; many docs
  are stale). Binary + build output under `scratch/mdbook-demo/`.
