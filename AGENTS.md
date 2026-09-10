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
