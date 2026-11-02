# ACTINV-P182 — a faster rate prune that leaves every result unchanged

## Purpose

Rate pruning drops nuclides whose population can be proven to stay below the keep threshold (`bmin`, default 1e-8
atoms/g), with bounds that `ledger.rate_pruning` reports (P148). Inside a cyclic group of nuclides those bounds are
tightened by repeated sweeps until no bound falls by more than 1e-12 of itself in a sweep, or 1,000 sweeps.

On P181's mixed-material mesh with ACTINV's shipped TENDL-2025 library, profiling found 96% of each region's solve time
in those sweeps (median 236 ms of 270 ms per region; ENDF/B-VIII.1 13 ms). One cyclic group of 2,947 nuclides and
50,052 links never meets the tolerance and runs all 1,000 sweeps, because a few bounds already many orders of magnitude
below the threshold keep shrinking by a fixed ratio per sweep. They do not affect what is kept.

Two changes, in commits `793ad03` and `ead00ec`:

1. **Exact.** The sweeps recompute only members whose inputs changed in the previous sweep, over a flat edge layout,
   and duplicate edges are merged by a stable sort. Same arithmetic in the same order, so the same bits.
2. **Stopping rule.** A bound below `1e-6 × bmin` no longer counts toward the stopping tolerance. Bounds only fall, so
   such a state is dropped whatever later sweeps give, and it passes on at most its own value along each edge. Its
   bound stays valid but looser. Bounds at or above `1e-6 × bmin` are still tightened to 1e-12. `bmin = 0` reproduces
   the old rule.

The question:

> With the same inputs, does the candidate build give the same results as the baseline build, except for looser
> bounds on nuclides that are dropped anyway, and how much faster is it?

## Disclosure of what has been seen

- The profile above, and the 32-region mesh of P181 (8 elements) on both libraries: with a significance floor of
  1e-20 to 1e-11 atoms/g the photon sources were bit-identical to the baseline; `ledger.rate_pruning` totals moved by
  at most 2.5e-11 relative; more dropped nuclides were listed (their bounds no longer underflowed to zero). Wall time
  for that mesh fell from 8.8 s to 4.6 s on TENDL-2025.
- A sweep cap (not adopted) at 300, 100, 50 and 20 sweeps, on the same mesh.
- The local CI sweep of the candidate before it was pushed: every control step passed except a stale manifest
  (fixed) and the known environment-only failures; no diff-gated record moved.
- One FNS problem (CI's end-to-end iron spec) and the 32-region mesh, to find which output fields differ between the
  builds: on the iron problem only the run time `ms`; on the mesh only `ledger.rate_pruning` and the footer's
  `wall_time_s` and `cells_per_s`. Wall time for that mesh: 9.1 s baseline, 3.5 s candidate.
  The checker's comparison was run on those two output pairs before registration (both met **Unchanged** and
  **Pruning record**).
- No result on the problems below has been compared between the two builds, and no time on them has been read.

## Builds

- **Baseline:** `actinv` built from `d054c16` (master before the change), `cargo build --release -p
  actinv-cli`, copied to `target/p182-bin/baseline/actinv`, SHA-256 `2ca4bd75df5119b20bc0ca8224d4fecc7c3922d78a8f8dd7434111875a91dc28`.
- **Candidate:** the same from `ead00ec`, `target/p182-bin/candidate/actinv`, SHA-256
  `00508aea92f62bea4684beea12652cad59f5c81aba1e34456da86f89d03633c6`.

Same toolchain (rustc in the record), same machine.

## Problems

- **FNS.** P172's 132 JAEA FNS decay-heat problems, as P180 prepares them (`p180_speed.prepare`), each on two
  libraries: the shipped TENDL-2025 library of data catalog 1.2.0 and the repaired ENDF/B-VIII.1 709-group library
  (`a964a44e…`). 264 `actinv run` problems.
- **Mesh.** P181's mixed-material model, its neutron flux from OpenMC 0.15.3 written by P181's harness (step 1 only),
  at 512 elements (776 regions; full per-region results) and 4,096 elements (5,120 regions; the OpenMC adapter's
  result fields: mode, ledger, photon groups), each on both libraries: 4 `actinv mesh` problems. Inputs outside the
  repository, pinned:

| File | SHA-256 |
|---|---|
| `target/p182-inputs/d8/run/activation/flux.ndjson` | `d44a5ca1be4ce4d6c3dfbf21a29df942838f113568b4aa19c20de2cf1b18c4d3` |
| `target/p182-inputs/d16/run/activation/flux.ndjson` | `709dd7db7799966dc012532f59bf43b8190deeaea5fea328179502fe1edbd52b` |
| `target/p182-inputs/mesh_d8_tendl.json` | `744e11cb5f836d057ce37290cca4b43ef60e12d39616150e4c1373fdbc5d7333` |
| `target/p182-inputs/mesh_d8_endf.json` | `739a84bc0ab66c4259bae5cc3304c9252aeb140be343cbe0cd296b5abdc6d8bb` |
| `target/p182-inputs/mesh_d16_tendl.json` | `55a6c565313ab926dc06377a861ef95707bb03ccdb0541eb30724cbcc3135ab5` |
| `target/p182-inputs/mesh_d16_endf.json` | `0c9faf23da79fe405caa1e3b149b62c7c8a00e96416d4d5af2c642271969f1d3` |

## Runs

Inside the AGENTS.md scope (6 GiB, 200 % CPU), on a quiet machine. Every problem is run by both builds, one after
the other, the order alternating (baseline first on even problems). Each process is timed from start to exit, with its
CPU time. Outputs are kept until the checker has compared them, then deleted (they are large); the record keeps their
digests and the comparison.

## Comparison

Each output is canonicalized: JSON parsed, and the timing fields removed (`ms` of an `actinv run` result or of a mesh
region's result; the mesh footer's `wall_time_s` and `cells_per_s`). No output field names the build. Then:

- **Unchanged:** everything outside `ledger.rate_pruning` (every cell of a mesh) is identical, value for value.
- **Pruning record:** in `ledger.rate_pruning`,
  - the kept nuclides are the same (every result above already proves it; recorded here as well);
  - every nuclide the baseline lists as dropped is listed by the candidate, with a bound at least as large (looser or
    equal, each a valid bound);
  - nuclides only the candidate lists have bounds below `1e-6 × bmin`;
  - each of the record's totals (`activity_Bq_per_g_bound`, `heat_W_per_g_bound`, `dropped_activity_Bq_per_g_bound`,
    `dropped_heat_W_per_g_bound`, `kept_activity_Bq_per_g_bound`, `kept_heat_W_per_g_bound`, where present) is at
    least `1 − 1e-12` times the baseline's, and at most `1 + 1e-6` times it when the baseline's is positive.

  - every other field of the record (`threshold_atoms_per_g`, `unbounded_components`) is the same.

  `bmin` is the problem's (`options.bmin_atoms_per_g`, default 1e-8; a screening spec's
  `options.screen.bmin_atoms_per_g`). A bound counts as at least as large when it is at least `1 − 1e-12` times the
  baseline's.

## Gates

- **G0:** this protocol's SHA-256 is in `protocols/protocol_hash.txt`.
- **G1:** the builds' digests and commits, the libraries, the mesh inputs, the harness and checker equal the pins; a
  clean tracked tree; the harness inside the AGENTS.md scope.
- **G2:** all 268 problems ran under both builds, and every run exited 0.
- **G3:** every problem meets **Unchanged** and **Pruning record**.
- **G4:** quiet machine: no other transient scope of the user at the start, between problems or at the end; median
  1-minute load before the runs at most 3.0.
- **G5:** CI green on the evidence commit.

P182-PASS if G0–G5 hold. A PASS certifies the change as result-preserving on these problems; the times are reported,
not gated. One repair round, registered as an amendment first.

## Allowed sentences

With PASS, filled by the checker:

1. "On P181's mixed-material mesh at E elements (R regions) with LIBRARY, `actinv mesh` took B s before the change
   and C s after it, with identical results apart from looser bounds on nuclides that are dropped anyway (one run
   each, two threads)." (each mesh problem; LIBRARY is "ACTINV's shipped TENDL-2025 data" or "an ENDF/B-VIII.1
   library")
2. "On P172's 132 FNS problems, the median time per problem went from B s to C s with TENDL-2025 and from B' s to C' s
   with ENDF/B-VIII.1 (one process per problem, two CPU threads)."

Each may be quoted with the machine and the two-thread limit.

## Not claimed

Nothing about problems other than these; nothing about pruning modes other than `rate` (the default); no change to any
physics result is claimed beyond identity on these problems.

## Commands (coordinator)

`controls/p182_harness.py` (`prepare`, `run`, `assemble`) and `controls/check_p182.py`, from a worktree at the
registration commit, inside the AGENTS.md scope, with no other job running.
