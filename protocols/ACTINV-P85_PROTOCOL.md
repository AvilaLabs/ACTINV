# ACTINV-P85 — Groupwise prepared data after a spectrum-only cache miss (exact)

Date: 2026-09-29. Status: **frozen before the changed code is built or run.**

## Supersedes P84 (withdrawn before any candidate build)

P84 (`310e389e…`) proposed memoizing parsed decay data. Before any candidate was built, running the
P84 measurement probe on the **reference** showed that its motivation was incomplete, and that its
timing design was biased because reference and candidate share the on-disk prepared cache. A
flux-only miss for a spectrum total not seen before took about 4 s in a warm process, of which about
3 s built a new spectrum-collapsed artifact: open and verify the 275 MB prepared library, collapse it,
write a 6.9 MB file under the prepared cache (984 MB at the time), and read it back. A decay memo
addresses about 0.65 s of that, so P84 is withdrawn untested. Its protocol file and hash stay on
record.

## Change under test

Branch `p84-decay-memo` from master `feb0c68` (the branch name predates the withdrawal). No decay
memo. In `run_with_cache`:

1. Besides the existing fingerprint, compute a **base fingerprint**: the same canonical object without
   the group flux values and total (the spectrum's structure, boundaries and ordering stay),
   without `collapse_flux` and without `multi_spectrum`.
2. On a miss where the cached slot has the same base fingerprint (the inputs are unchanged, only the
   spectrum differs), prepare with **groupwise** activation data, i.e. no collapse flux, the same
   route per-step-spectrum runs and `actinv mesh` already use. Mark the slot groupwise.
3. A groupwise slot is reused for any later spec with the same base fingerprint. The run collapses
   its own spectrum from the in-memory rows, and nothing is written to disk.
4. Everything else is unchanged. The first preparation in a cache, and every miss whose base
   fingerprint differs, prepare exactly as today (collapsed artifact for a single spectrum). A repeat
   of an identical spec is a hit as today.

Exactness: the collapsed artifact (`build_collapsed_artifact`) and the groupwise
`PreparedLibrary::collapse_row` add the same products over the same flux window. They share the same
denominator and order; the artifact only adds extra `+0.0` or `−0.0` terms outside the row's stored
span, which cannot change a sum that starts at `+0.0`. Fission average energies call the same
function on the same values. Uncertainty runs load dense data on both routes, and self-shielded runs
are already groupwise. The gates test this on real sequences.

Memory: a groupwise slot holds the prepared rows in memory, about 275 MB for TENDL-2025 709 groups,
instead of the collapsed values, about 7 MB. This applies only after the cache has seen a second
spectrum for the same inputs.

## Measurement tool

`crates/actinv-core/src/bin/cache_probe.rs` (added before the P84 reference build, unchanged). For a
spec, it runs 10 fixed multiples of its spectrum total (1.0, 1.1, 0.9, 1.25, 0.8, 1.5, 0.67, 2.0, 0.5,
1.05), `warm` (one `PreparedCache`) or `cold` (fresh cache per run). Per run it prints the SHA-256 of
the result JSON without the top-level `ms`, the wall time and the hit flag. Every probe invocation
gets its own **fresh, empty `ACTINV_CACHE_DIR`**, so no run benefits from artifacts another run wrote.

## Gates

Reference: master `feb0c68` plus the probe (`actinv` `53dedafb…`, probe `d4129172…`). Candidate:
this branch, release build. Checker `controls/check_p85.py`, under the 6 GB cgroup cap,
`RAYON_NUM_THREADS=1`. Specs from `target/p75b/specs/` in the main checkout:
`A__ss316ln__fns__1e+13` (gated), `A__eurofer97__maxwell__1e+13`, `A__concrete__mix__1e+13`.

- **G0** protocol hash registered before the first build with the change.
- **G1 static:** fmt, clippy (`-p actinv-core -p actinv-data -p actinv-cli --all-targets -D
  warnings`), `cargo test --release -p actinv-core -p actinv-data`, including a unit test that a
  collapsed artifact's values and fission energies equal the groupwise values bit for bit for several
  spectra (interior zeros, narrow windows, all-positive).
- **G2 bitwise:** for each spec, the 10 result hashes are identical for reference cold, reference
  warm, candidate cold and candidate warm. The candidate's warm hit flags must read false, false, then
  true for runs 3–10.
- **G3 bitwise, single runs:** the P75b population (783 specs, `actinv run`), full result JSON identical
  between reference and candidate after removing timing keys (as in P81).
- **G4 CI replay:** the runtime CI controls pass with the candidate.
- **G5 adoption threshold (pre-registered):** `A__ss316ln__fns__1e+13` warm, runs 3–10 (runs that hit
  the groupwise slot on the candidate and are novel-spectrum misses on the reference). Per repetition,
  take the median wall time of those 8 runs; take the median over 3 alternating repetitions. The
  candidate must be at least **3×** faster. The expected gain is much larger (about 4 s against well
  under 1 s), so the margin covers timing noise. Everything else is reported.

Merge only if G0–G5 all pass.
