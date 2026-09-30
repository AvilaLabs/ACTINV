# ACTINV-P84 — Reuse parsed decay data across prepared-cache misses (exact)

Date: 2026-09-29. Status: **frozen before the changed code is built or run.**

## Motivation (measured)

The workbench live sweep, `actinv budget` and the surrogate runner reuse one `PreparedCache`. Its
fingerprint includes the spectrum, so a flux-only change (for example the live slider moving
`spectrum.total`, which since P78 is always solved) is a miss and repeats the whole prepare step. A
single run of `C__el_Fe__fns__1e+13` profiled on master `feb0c68` (`ACTINV_P14_PROFILE`, one thread):
of about 1.15 s of preparation, reading and parsing the decay data takes 1.0 s (primary 603 ms,
fallback read/parse/merge 398 ms). The only spectrum-dependent preparation, loading the collapsed
activation library, takes 124 ms.

## Change under test

Branch `p84-decay-memo` from master `feb0c68`.

1. A process-wide memo of parsed decay files, keyed by the file's SHA-256 as `file_sha256` reports it.
   That is the same (path, length, modification time) → SHA-256 cache the `PreparedCache` fingerprint
   already relies on. On a hit, preparation takes a clone of the parsed table and the recorded hash
   instead of reading and parsing the file. On a miss it reads, verifies and parses exactly as before,
   and stores the result under the hash of the bytes it read. Parse errors are not stored. The memo
   holds at most four files.
2. Everything else in preparation runs unchanged on every miss: validations, library loading for the
   new spectrum, the fallback merge, decay overrides and chain construction. The merge iterates the
   fallback table in hash-map order, as it already does today; only the fallback count and key set
   depend on that iteration, and both are order-independent.

A process that prepares once (`actinv run`, `actinv mesh`) behaves as before apart from storing one
memo entry per decay file.

## Measurement tool

`crates/actinv-core/src/bin/cache_probe.rs` is added before either binary is built, identically to
reference and candidate. It is instrumentation, not part of the change. It reads a spec and runs it
at 10 fixed multiples of its spectrum total (1.0, 1.1, 0.9, 1.25, 0.8, 1.5, 0.67, 2.0, 0.5, 1.05),
either through one `PreparedCache` (`warm`) or through a fresh cache per run (`cold`). For each run
it prints the SHA-256 of the result JSON with the top-level `ms` removed, the wall time, and whether
the cache hit.

## Gates

Reference: master `feb0c68` plus the probe, release build. Candidate: this branch, release build.
Checker `controls/check_p84.py`, under the 6 GB cgroup cap, `RAYON_NUM_THREADS=1`. Specs
(`target/p75b/specs/` in the main checkout): `A__ss316ln__fns__1e+13` (gated),
`A__eurofer97__maxwell__1e+13`, `A__concrete__mix__1e+13`.

- **G0** protocol hash registered before the first build with the change.
- **G1 static:** `cargo fmt --all -- --check`; `cargo clippy -p actinv-core -p actinv-data
  -p actinv-cli --all-targets -- -D warnings`; `cargo test --release -p actinv-core -p actinv-data`
  passes and includes tests that (a) a memo hit returns a table equal to a fresh parse, and (b)
  rewriting the file with different content (new length) yields the new content, not the memo.
- **G2 bitwise:** for each spec, the 10 result hashes are identical across all four combinations:
  reference and candidate, each warm and cold.
- **G3 bitwise, single runs:** the P75b population (783 specs, `actinv run`): full result JSON
  identical between reference and candidate after removing timing keys (as in P81).
- **G4 CI replay:** the runtime CI controls pass with the candidate.
- **G5 adoption threshold (pre-registered):** `A__ss316ln__fns__1e+13` warm, runs 2–10 (every run
  after the first is a flux-only cache miss). The median over those 9 runs, taking the median of 3
  alternating reference/candidate repetitions of the whole sequence, must be at least **1.5×**
  faster for the candidate. The expected gain is well above that (about 1 s of about 1.7 s), so the
  margin covers the run-to-run variance seen in P82/P83. The other specs and cold mode are reported.

Merge only if G0–G5 all pass.
