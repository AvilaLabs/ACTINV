# ACTINV-P188 — The IRDFF-II covariance sidecar, rebuilt with absolute short-range variances, checked and sampled

## Purpose

P184 built a covariance sidecar in which TENDL-2025's dosimetry reactions take IRDFF-II's covariance. The covariance
is expressed about TENDL-2025's cross section and includes the difference between the two evaluations. P184 passed
(ledger 340), but its method scaled IRDFF-II's short-range (LB=8) variances by `r²` (ledger 341). ENDF-102 defines
each LB=8 F as a contribution to the absolute variance, in b², and ACTINV applies it that way. An absolute variance
carries over unchanged. The error matters for:

- Cu-63(n,γ), up to 17 times in some bins;
- Am-241(n,f), up to about 10%;
- Fe-54(n,p), only below 0.89 MeV.

Fe-54(n,α)'s F are all zero.

P188 rebuilds the sidecar with that one change and asks two questions:

1. Does the sidecar hold exactly the corrected covariance, with nothing else changed (P184's checks)?
2. On P187's two problems, does ACTINV's first-order band with this sidecar agree with brute-force sampling (P187's
   check)?

A pass makes the sidecar a candidate for a data release. Shipping it needs the owner's approval and the catalog
sweep. P188 does not show that IRDFF-II's covariances are right for these spectra.

## Method

`controls/p188.py` runs P184's and P187's sealed code (`controls/p184_covariance.py`, `controls/p184_harness.py`,
`controls/check_p184.py`, `controls/p187_harness.py`, `controls/check_p187.py`) with these changes.

- **The method change.** Each IRDFF-II LB=8 block goes into the patched evaluation with IRDFF-II's own energies and
  F values. In P184 it was "each F scaled by r² of the interval holding its own interval's midpoint". The LB=5
  matrix, the selection rule, the sources and the build are P184's.
- **The checker's change.** P184's G3 recomputation expects each LB=8 component to equal IRDFF-II's block to P184's
  tolerance, unscaled.
- **Sampling.** P187's harness and checker run with the new sidecar in place of the v1.2.0 sidecar, pinned by the
  SHA-256 the build records. They use seeds 188001 (A) and 188002 (B) and P187's problems, N, statistic, limits and
  gates.
- **Outputs and binary.**
  - Outputs go under `target/p188-work`. The record (`results/p188_record.json`) holds both parts, and the samples
    file is `results/p188_samples.json.gz`.
  - The binary is built from a clean checkout of the registration commit. Its Rust sources are master's since
    `4ce2822`.

## Gates

- **G0:** this protocol's SHA-256 is in `protocols/protocol_hash.txt`, with the SHA-256s of the sealed files above
  and of `controls/p188.py`.
- **G1–G4:** P184's G1–G4 on the new sidecar, as P184 states them, with the LB=8 expectation above. G1's
  registration commit is P188's.
- **G5:** P184's G5 observation: FNS iron, before (v1.2.0) and after (the new sidecar). The values are recorded, not
  gated.
- **G6:** P187's G1, G2 and G3 on the new sidecar. G1's registration commit is P188's.
- **G7:** CI green on the evidence commit.

P188-PASS if G0–G7 hold. One repair round (an amendment with a fresh run) is allowed.

## Disclosure

- **Seen before writing:**
  - P184's whole record, and the per-reaction `r` at each IRDFF-II LB=8 bin (ledger 341);
  - P187's whole record, on the v1.2.0 sidecar.
- **Unit check.** `controls/p188.py --selftest` checks that the change leaves P184's LB=5 recomputation untouched
  and carries LB=8 unscaled.
- **Rehearsal.** One rehearsal of the full pipeline was run before registration, with P187's 4-sample rehearsal
  mode for the sampling part. It was read for:
  - exit statuses;
  - which gates hold. G0 and the registration-commit checks cannot hold before registration.

  No band, no sampled spread and no matrix value of the new sidecar was read. Its outputs are deleted before the
  scored run.

## Allowed sentences

On P188-PASS, filled by `controls/p188.py` from the record:

1. "In ACTINV's P188 sidecar, {N} TENDL-2025 reactions on {M} target nuclides take IRDFF-II's covariance, expressed
   about TENDL-2025's cross section and including the difference between the two evaluations, with IRDFF-II's
   short-range variances as IRDFF-II gives them; every other covariance is TENDL-2025's, unchanged."
2. P184's G5 sentence for each response, with "P188's" in place of "P184's".
3. "With P188's sidecar, on two FNS-spectrum point problems (iron, and a nickel, cobalt, aluminium, tungsten and
   niobium mixture), ACTINV's first-order activity band agreed with the spread of 200 sampled solves within the
   registered limit on all {n} tested (nuclide, time) pairs; the ratio of first-order to sampled variance ranged
   from {lo} to {hi}."

A sentence about ACTINV's default band applies only after a data release.

## Commands (coordinator)

- Inside the bounded scope, from a worktree at the registration commit:
  - `cargo build --release --locked -p actinv-cli`, with `ACTINV_BIN` set to that binary (P184's harness runs
    `ACTINV_BIN`; the first rehearsal stopped because it was not set);
  - `controls/p188.py stage`, `build`, `observe` and `sample`;
  - then `assemble` and `check`.
- After the verdict, the sidecar is kept outside Git for the release decision, and the other outputs are deleted.
