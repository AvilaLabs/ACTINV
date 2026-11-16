# Verification coverage and execution

The implementation worklist covers numerical, parser, decision, and concurrency
risks. Existing frozen scientific controls remain the qualification references.
These generated tests are internal regression QA; they do not extend a scientific
qualification claim or change its acceptance tolerances. Register the required
protocol/amendment before a new scientific evidence campaign.

## Risk map

| Behavior | Existing reference | Added verification | Expansion worklist |
|---|---|---|---|
| Quantity domains and conversions | `quantity.rs` unit tests | all-bit-pattern generated tests; Kani scalar constructors and flux conversion | more derived-quantity boundaries after specifying overflow contracts |
| CSC assembly and shifted matrices | sparse and CRAM controls | independent dense accumulation; 16 partitioned bounded Kani pattern harnesses | larger patterns, permutation invariance, malformed-structure contracts |
| Complex sparse LU | numerical controls and `sparse.rs` regressions | generated diagonally dominant matrices, forced row pivots and uniform binary scaling with independently formed RHS | ill-conditioned, mixed dynamic range, singular matrices |
| Decay propagation and CRAM | analytical, dense expm, P149 multiprecision | generated one/two-daughter cases; time splitting, linearity, conservation, multi-RHS and log-rate tangents | multi-rate stiff chains, feed/removal and larger tangent cases compared with multiprecision |
| Group collapse | processing and energy-group controls | exact integer numerator/denominator oracle; flux normalization invariance | interpolation-law combinations, rebin closure, shielding |
| Declared spectrum total | validated specification and ordinary-case compatibility controls | independent equal/half-weight extremes, representable traces and generated positive finite shapes | unequal shapes across wide dynamic ranges and rebin combinations |
| ENDF primitives and records | P12 bounded parser probe | generated Unicode/ASCII field boundaries; coverage-guided record parsing; Kani field proof and experimental rejection claims | activation, covariance, decay and fission readers as separate structured targets |
| Problem JSON | schema tests and P12 | coverage-guided production spec parser | mesh, photon, flux and response schemas |
| NPZ input | library regressions and P12 | coverage-guided small synthetic archives | prepared cache, damaged compression, HDF5 transport paths |
| Waste classifications and interval envelopes | Part 61 unit tests and P105+ controls | generated interior witnesses and missing-metadata checks; scoped boundary/aggregation mutation campaign | more nuclide/geometry/metadata combinations |
| Worker cancellation versus exit | desktop worker tests | production polling function shared with Loom, deterministic tests, and actual system-child termination/reaping checks | result receiver drop, sweep generation transitions, blocked pipes |
| Executed memory behavior | Rust safety checks | selected kernel tests under Miri | extend compatible dependency paths where useful |
| Uncertainty, pathways, transport, mesh, R2S, inverse solvers | existing feature controls | existing controls retained | add independent/generated oracles per feature; these are not covered by the initial kernel suite |

“Added” describes checked-in harnesses, not a claim that every tool has passed.
The local reports under `target/verification/*.json` distinguish successful,
failed and timed-out executions. The expansion column is intentionally explicit:
bounded proofs and representative generated cases do not cover an entire solver.

## Contracts and bounds

- The quantity wrappers validate construction and retain normal binary64 arithmetic.
  Finite operands can overflow during subsequent arithmetic. The new tests do not
  silently change that contract or assume closure over finite values.
- Kani checks the actual production source files included by path. Constructor
  inputs cover all binary64 encodings. The default campaign also includes flux
  conversion, 16 sparse-pattern partitions, and the full-ASCII ENDF field claim,
  each with its own 300s budget. Rejection claims remain explicit experiments;
  a selected harness can be run with `kani --harness NAME`.
  Earlier monolithic/symbolic-heap proofs exceeded 300s; their failed reports are
  retained locally. They are replaced by the following equivalent input domains:
  - CSC: each of the 16 possible 2x2 sparsity patterns, with zero input values and
    unit scale/shift. Partitioning the four input booleans exhaustively gives
    each proof a concrete heap shape. This checks structure, not general matrix
    sizes or floating-point accuracy. Assertions include dimensions, the exact
    retained row set plus diagonal, sorted rows, consistent storage lengths, and
    the expected zero/unit values. A runner regression guards the complete
    mask/name partition and its inclusion in the default campaign.
  - ENDF success: every 66-byte ASCII data area, including NUL bytes, with exact
    returned slice lengths and pointer identity at all six field boundaries.
    This implies the original byte-equality claim without asking the verifier
    to compare the same bytes through two aliases.
  - ENDF truncation: six disjoint 11-length ranges cover every ASCII prefix of
    lengths 0 through 65. Each proof selects arbitrary bytes, then uses a safe
    ASCII view restricted to that input domain and enumerates the 11 lengths.
    It assumes no parser outcome. A regression checks the complete length
    partition and its registration as an experiment. These claims remain
    unproved: both the symbolic-length attempt and the first concrete-length
    range exceeded 300s. Other range registrations are not a claim of execution.
  - ENDF non-ASCII: both checked field and tail readers reject a 66-byte record
    containing one U+00A0 among spaces, at every possible character position.
    This is a representative non-ASCII family, not all Unicode strings.
    A separate 75-byte version has valid numeric tail fields, so truncation or
    invalid numbers cannot mask a missing non-ASCII guard. An executable
    regression accepts an ASCII positive control with the same tail and rejects
    every shared non-ASCII fixture. The finite fixtures are UTF-8-validated at
    compile time; runtime input construction is outside these reader claims.
    Both rejection families remain unproved after 300s attempts with symbolic
    positions and with exhaustive fixture iteration. Passing their ordinary
    regressions is not recorded as a successful Kani proof.
  The ENDF input generators use safe `std::ascii::Char` construction or safe
  ASCII slice views under Kani's pinned nightly-only `ascii_char` feature. This
  introduces no unsafe code or production feature dependency. Masking each
  independent byte to seven bits still covers every ASCII record. The success
  input uses six 11-byte construction loops; the truncation inputs restrict
  arbitrary bytes to ASCII. Their unwind bound is 12. Non-ASCII fixture loops
  use groups of 13 and an unwind bound of 14. Unwinding assertions remain enabled
  and must pass. No production function or library operation is stubbed.
  Do not add assumptions merely to make a failed proof green.
  A one-minute diagnostic of `checked_fields("")` also remained in standard
  library unwinding. Kani 0.68.0 has an open
  [formatting/integer-parsing performance issue](https://github.com/model-checking/kani/issues/5127)
  consistent with these paths; that is supporting context, not proof that every
  local timeout has that cause. Ordinary regressions cover all 66 short lengths
  for each generated ASCII record without claiming formal exhaustive content
  coverage for the rejection path.
- Numerical generated cases use closed, linear parent-to-one/two-stable-daughter systems with
  exposure in [0,100), initial populations up to 1e6, and decay-rate decades
  from 1e-10 through 1e6. The scale-based 5e-12 acceptance budget checks absolute
  error relative to total inventory, not relative accuracy of vanishing traces.
  Analytical references use exp/expm1 independently of CRAM. The two-branch
  family covers all six state permutations, two independent RHS columns and
  derivatives with respect to log decay rate.
- Sparse generated tests use small integer components so dense assembly is exact;
  LU uses strictly diagonally dominant complex matrices and a 2e-12 scaled error
  budget. Its known solution is independent of the solver's own residual code.
  Additional 2x2 through 6x6 cases force a row pivot and uniformly scale the
  matrix by 2^-400 through 2^400, preserving its conditioning.
- Declared-total regressions independently check equal/half-weight solutions
  where summation or a multiplier overflows, underflows or becomes subnormal.
  The generated family samples every positive finite binary64 weight encoding,
  2 through 16 groups, an optional zero group and totals 2^-900 through 2^900;
  this is sampling, not an exhaustive floating-point proof. Ordinary-case bit
  compatibility is checked separately from the independent accuracy controls.
- Fuzz inputs are at most 64 KiB. NPZ exploration admits at most 32 members and
  1 MiB declared decoded bytes; it is not a decompression-bomb qualification.
  Per-input wall/RSS bounds and the outer cgroup still apply. The P12 oversized
  declaration regressions remain necessary. Seeds contain only synthetic data
  and the tracked example specification.
  The NPZ target also wraps mutated NPY members in a freshly checksummed ZIP
  envelope, so CRC failures do not prevent reaching array-header/shape handling.
  Fuzz builds use 16 codegen units to keep optimization of the large production
  crates manageable. An initial single-unit cold build exceeded the 900s outer
  deadline before executing inputs; that attempt is not a successful fuzz run.
  The spec target's cold dependency graph also exceeded 900s; it has a separate
  1800s build/run ceiling. Other fuzz targets retain 900s. Select an individual
  parser with `fuzz --target endf`, `npz`, or `spec`. NPZ and spec share JSON
  dependency features to avoid rebuilding the data crate twice.
- Loom instruments the harness's channels and completion flag while calling the
  exact production polling function. The child process is modeled. Loom's mpsc
  stub does not model sender disconnection, which is checked separately with
  deterministic tests. OS pipe behavior and actual child cleanup need the
  process integration checks; model success does not prove those operations.
- Miri keeps host isolation and its default UB checks enabled. Its interpreted
  environment explicitly sets eight proptest cases, seed 159, one test thread,
  and disables on-disk failure persistence. Ordinary generated runs retain
  failure persistence. Merely setting host environment variables does not
  configure an isolated Miri program.

## Run locally

Tool versions live in `verification/tools.toml`. Application and verification
workspaces have separate lockfiles. The runner checks that their reachable
production dependency versions match, including source checksums. Optional
dependencies enabled only by the wider desktop workspace may be absent. When
application dependencies change, reconcile the verification lockfile before
running linked checks. Proptest disables its optional process-fork
features: generated tests never recursively launch a Rust test executable.

Run one suite at a time, from the checkout root:

```sh
systemd-run --user --scope -p MemoryMax=6G -p MemorySwapMax=0 -p TasksMax=128 -p CPUQuota=200% -- env CARGO_BUILD_JOBS=1 RUST_TEST_THREADS=1 RAYON_NUM_THREADS=2 python3 scripts/verify.py properties
```

Replace `properties` with `kani`, `fuzz`, `mutants`, `loom`, `miri`, or `all`.
`workspace` runs the required application fmt/check/clippy/test gates and the
verification workspace's formatting and clippy checks.
`--extended` increases generated cases and fuzz duration. `kani` runs the verified
default campaign; `kani --harness NAME` also selects explicit rejection experiments.
`--extended` does not silently include unproved experiments. `all` is serial and
stops on the first failure. The runner verifies the actual cgroup files before
spawning compilers, rejects RAM-backed temporary storage, prevents competing
runner invocations, and terminates only its own process group on timeout.
It writes an execution report even when a command fails or times out.
Mutation testing operates in place inside a disposable snapshot of tracked
worktree content plus the verification additions. Unrelated untracked files are
excluded. The stable snapshot path permits compiler-cache reuse across campaigns.

Kani is supplied by `kani-verifier`; cargo-fuzz and cargo-mutants have their own
pinned versions. Miri needs the pinned nightly's `miri` and `rust-src` components.
Install/build tools under the same resource scope. Tool installation is explicit;
the runner does not silently install or update tools. Build artifacts, fuzz
discoveries, and mutation output stay under `target/verification/` on disk.
Retain minimized defect inputs and proptest regression seeds in version control
when they are synthetic and suitably small; add ordinary production regression
tests for every confirmed defect.

## CI adoption

The additional workflow initially supports manual suites. Promote the bounded
property suite and stable short proofs to required PR checks after observing
clean local and hosted runs. Proposed later cadence: corpus replay on PRs,
longer fuzz/numerical exploration nightly, and mutation/deeper Loom/Miri runs
weekly or for relevant changes. Keep budgets explicit; do not weaken an
assertion or skip a failure to meet a runtime target.

Verus and Creusot remain deferred until a specific algorithm merits deductive
specifications and loop invariants. The decision depends on uncovered behavior,
not on the number of tools installed.

## Recommended next audit

[The first correctness audit](CORRECTNESS_AUDIT.md) records the reviewed paths,
the repaired spectrum-normalization defect and remaining gaps. Continue the
data-to-result audit by mapping supported feature combinations to independent
controls, code entry points and explicit refusals. Reuse existing
P149 multi-precision history/branching controls and other frozen controls; the new
generated decay tests are supplementary coverage, not their replacement.
Review the expectations themselves: verify that negative controls fail for the
intended reason, that structural claims include dimensions and retained entries,
and that callers enforce every precondition assumed by a harness.

Prioritize these reviews and add focused regressions for confirmed gaps:

1. **Numerical failure handling and difficult systems.** Resolve public API shape
   and coefficient contracts, then extend beyond the current well-conditioned
   generated LU matrices and forced-pivot regressions: singular/near-singular systems, mixed dynamic
   range, and zero or tiny components next to large inventories. Check solution
   error against an independent reference; a small residual alone is insufficient.
   Review propagation and feature combinations against the applicable existing
   reference controls before proposing new numerical evidence.
2. **Scientific input semantics.** Trace units, energy-group boundaries,
   interpolation/threshold behavior, nuclide/isomer identity, and missing-data
   handling from import through matrix assembly to observables. Structured fuzzing
   exercises parser behavior but does not establish scientific meaning. Focus
   additional generated cases on gaps in the existing builder and processing
   controls, including combinations of supported options.
3. **Worker lifecycle failures.** Exercise receiver drop, late cancellation,
   blocked pipes, and stale results with a separately built application and
   bounded process cleanup. The current polling model does not cover the entire
   desktop worker lifecycle.

Record each finding with its trigger, expected/observed behavior, affected public
path, severity, and a reproducer or missing control. Promote stable verification
suites to required CI after measured hosted runs. Scientific evidence expansions
still need their own registered protocol/amendment and independent controls.
The full-corpus E2 audit remains the separate preparatory scope in
`E2_AUDIT_PLAN.md`; this recommendation does not open or execute it.

## Mutation review

The initial 24-mutation campaign caught 15 changes and left nine survivors.
Six survivors identified missing public-API cases: strict Z>92 and half-life>5y
alpha membership in both listing and evaluation, and
acceptance of finite zero concentrations/sums after a positive subnormal activity
underflows during unit conversion. These have focused regressions in
`crates/actinv-core/src/waste_verification_tests.rs`; production rule algorithms
and limits are unchanged.

Three mutations are excluded with exact name patterns after call-site review:

- `add_table2_row`: changing `activity_bq > 0` to `>= 0` is equivalent because
  both callers iterate the `nuclides` map built after zero activities are removed.
  Its activity argument is strictly positive on every public-API call.
- `finite_total`: changing `current + value` to `current - value` negates the
  successive totals for validated nonnegative inputs. The totals are used only
  to check finite overflow, then discarded. Under binary64 round-to-nearest,
  their magnitudes and overflow points coincide, and no sign is observable in
  a returned result.
- `RulePack::validate` at `waste.rs:182:52`: allowing a null limit at column zero
  in the match arm preserves rejection because the immediately following guard
  rejects every Table 2 row with a null column-zero limit. Only Table 2 rows can
  enter that arm. This changes the error wording; the campaign checks acceptance
  and classification behavior, not exact validation-message wording. The
  position-specific exclusion retains the adjacent mutation of the positive
  numeric limit check, which existing tests already catch.

Review these exclusions if the callers, accepted domains, rounding assumptions,
or use of the totals change. The remaining campaign contains 21 viable mutation
candidates; an empty campaign, an uncaught mutation, or a timeout fails the runner.
