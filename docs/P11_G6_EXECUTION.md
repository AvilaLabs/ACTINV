# P11 G6 complete-covariance execution record

This note records the standing-rule-7 plan and final evidence for the complete TENDL-2025 neutron covariance build.
It does not amend a gate or acceptance threshold.

## Minimum input first

P11 G1--G5 were settled before the complete corpus run. Fe-56/Ni-58 and synthetic two-group networks were the
smallest inputs needed for parser, collapse, NJOY, sensitivity, propagation and entry-point decisions. The 2,850-file
scan is G6's deliverable, not a substitute for those controls. It therefore ran only after those bounded gates passed.

The covariance builder uses the completed P10 neutron activation library
`ec4c72bf598dc8ad3d533d9cfafdcf493e2d1f949a3e4db6251495659b68cc44` and its index
`8bd19b4001c246758e739cd0067a0087e1ce5c2157438dae97bd52e1d3beb3fb`. Raw sources are the external deterministic
P10 working corpus; its P11 filename/source manifest is
`34f2048782bd50e4cab69e269826215632675514dd88c2bad1fe70ee92ce1ac4`.

## Checkpoint and memory design

Each source is addressed by its SHA-256, the matching activation-target identity and the explicit per-source parser
fingerprint. A source checkpoint is re-read, re-hashed and structurally validated before aggregation. Final grids are
interned through one retained map, and the complete library receives one validation before its sibling-temporary NPZ
and index are published. A one-source mutation therefore invalidates only that source; interruption resumes by source.

All complete runs used four workers, no concurrent heavy job and a 4 GiB address-space limit. Every canonical NPY
member is below the protocol's 1 GB allocation limit:

| member | uncompressed bytes |
|---|---:|
| `components.npy` | 20,521,784 |
| `grid_offsets.npy` | 512,328 |
| `grid_values.npy` | 4,805,344 |
| `values.npy` | 427,623,256 |

## Current-code fresh/cached identity

The final Rust builder fingerprint is
`c9825cafd8945f32efda4a00ea081af811b887562ebf07ae33ac05ea1d6846d1`. A current-code fresh run without a
covariance cache and a current-code run reusing all 2,850 validated checkpoints produced byte-identical artifacts:

| run | cache hits | wall | peak RSS | swaps | NPZ SHA-256 | index SHA-256 |
|---|---:|---:|---:|---:|---|---|
| fresh | 0 | 1:33.64 | **1,095,648 KiB** | 0 | `c19dec86b44ad5d90b66c9ab94d53e18641a1d354a89402a4da7986b6c530cde` | `9691ee5c4a7e3e89c428f912de712b5f805b29c86cc94a5b23f3c95a5951aea6` |
| cached | 2,850 | 66.40 s | 911,992 KiB | 0 | `c19dec86b44ad5d90b66c9ab94d53e18641a1d354a89402a4da7986b6c530cde` | `9691ee5c4a7e3e89c428f912de712b5f805b29c86cc94a5b23f3c95a5951aea6` |

Both runs have zero exit status and zero major page faults. The NPZ is 237,866,615 bytes on disk. It and every raw
source/checkpoint remain outside Git.

## Independent complete scan and coverage

`controls/g6_p11_complete.py` independently streams and SHA-256 checks every raw source, parses MF=33 with the Python
control, and compares every target's MAT/ZA/LISO, section count, component count and LB inventory to the Rust index.
It does not import production Rust. Its compact per-target inventory has SHA-256
`eafaa9d6c6de27ec8cebe0de4c88dea8be2060adeab6e2856301a6ca2a46b1e3`.

| inventory | count |
|---|---:|
| files / files with MF=33 / files without MF=33 | 2,850 / 2,850 / 0 |
| MF=33 sections | 84,489 |
| components | 285,023 |
| LB=5 / LB=6 / LB=8 | 84,489 / 116,045 / 84,489 |
| independent parse/hash errors | 0 |
| silently ignored components | 0 |

The activation library has 167,735 rows. Of 127,724 non-MF10 rows, 105,817 have a valid target/MT MF=33
self-covariance and 21,907 do not, for 82.848% eligible-row coverage. The remaining 40,011 MF=10 rows require MF=40
and are explicitly uncovered. The 21,907 missing-self rows comprise 8,698 unmapped-product (`LMF=-1`) and 13,209
loss (`LMF=0`) rows. Runtime coverage is response-specific after mode/pruning; these corpus totals do not imply that
every run has partial coverage.

## Regression and close

The exact required Rust commands pass: rustfmt, workspace/all-target/all-feature check, strict Clippy and all tests.
Python controls compile. The local CI end-to-end path, clean-clone self-containment, release-note and dependency
checks, and P5--P10 checker verdicts pass. GitHub CI adds the bounded P11 G3--G5 controls; G1/G2/G6 retain external
data/NJOY requirements and are represented by committed hash-pinned evidence rather than downloading bulk data in CI.

The P11 checker derives **P11-CONDITIONAL** because the frozen append-only record includes Amendments A--E. This
opens P12 but does not tag, publish, claim v1.0 or characterize an MF=33 normal interval as a licensing safety margin.
