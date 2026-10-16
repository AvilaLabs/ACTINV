# P141 — Move the `zip` dependency to 9.0.4 after the upstream yank

Registered 2026-10-16, before any change to a manifest or source file. Base commit: `50ca615` (the v1.5.0 release
commit plus the P120 Amendment C registration). The owner agreed on 2026-10-16 that the `zip` move gets its own
protocol-checked change; the yank recorded below moved it ahead of the 1.5.0 publication.

## Why

`actinv-data` and `actinv-cli` pin `zip = "=8.6.0"`. At 2026-10-16T03:32Z upstream yanked 8.6.0 and every other
release except 9.0.3 (9.0.4 followed at 09:09Z), citing GHSA-vv5m-jxxh-4627, GHSA-4v9h-p7m9-rh26,
GHSA-4684-h2q9-2vcp and further advisories to be published this month. Consequences, observed (ledger entry 172):

- the CI step "P16 typed-boundary control and independent verdict" builds a lockfile-less consumer of
  `actinv-core` offline and now fails to resolve `zip`; CI passed at 05:00Z only through its cached index;
- `cargo install actinv-cli` without `--locked` fails for every published version that pins 8.6.0.

ACTINV calls no extraction API (`extract`, `extract_unwrapped_root_dir`). It reads members of `.npz` libraries,
covariance sidecars and the single-member data-release archives in memory, and writes `.npz` files.

## Change under test

`zip = "=9.0.4"` (features unchanged: `default-features = false`, `deflate-flate2`) in `crates/actinv-data` and
`crates/actinv-cli`, `Cargo.lock`, `python/Cargo.lock`, and the source edits the 9.x API requires. The only API
break found in a trial build is that an entry's `name()` returns `Result<Cow<str>, ZipError>`; an entry whose name
is not valid is refused with an error, as an invalid member already is. No behaviour is intended to change.

## Required evidence

Executables run only under the required systemd cgroup, serially, with disk-backed `TMPDIR`. The reference binary
is the 1.5.0 release binary with `zip` 8.6.0, saved before the change; the candidate is the same tree with this
change.

- **Z1, writers byte-identical.** `build-library` of the full TENDL-2025 proton set (as P99 G2: `--format tendl
  --projectile proton --groups fispact-162 --temperature-K 0 --decay` primary and fallback) with both binaries gives
  byte-identical `.npz` files. `build-library` and `build-covariance` on the two pinned P11 inputs (TENDL-2025 `n-Fe056.tendl` and
  `n-Ni058.tendl`) give byte-identical `.npz` files.
- **Z2, readers identical.** The shipped neutron, patched neutron and proton libraries and the covariance sidecar
  load identically: `actinv inspect library` and `actinv inspect xs` (all targets and reactions) output is
  byte-identical between the binaries for each library, and the run
  results of `examples/fns_fe_5min.json` and of an uncertainty run with the shipped covariance are byte-identical
  apart from timing fields.
- **Z3, refusals unchanged.** The existing member-validation and data-fetch archive tests (wrong member name, extra
  member, encrypted or oversized member, path not enclosed) pass unchanged.
- **Z4.** The P16 consumer resolves and builds offline; the workspace tests, fmt, clippy `-D warnings` (native and
  wasm), the Python binding checks and the local CI steps pass; CI is green on the pushed commit for every triggered
  workflow.

## Verdict

P141-PASS if Z1–Z4 hold. Otherwise P141-FAIL, with the failing evidence preserved; a repair needs an amendment
registered first. A byte difference in Z1 is a failure even if the decoded arrays agree, because shipped library
hashes are pinned in the data catalog.

## Not claimed

No change to physics, data or results. No claim about the advisories beyond the fact that ACTINV does not call the
extraction APIs they concern.
