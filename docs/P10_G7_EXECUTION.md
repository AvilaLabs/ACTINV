# P10 G7 complete-library execution plan

This note records the standing-rule-7 plan before the complete external builds. It is execution evidence, not a
protocol amendment and does not change a gate or tolerance.

## Smallest gate input is already settled

P10 G1–G6 passed on the frozen minimum inputs before any complete TENDL download. The complete libraries are the G7
phase deliverable; they are not being used to repair or substitute for a minimum-input gate. G7 will build exactly
the frozen TENDL-2025 s30 neutron, proton, deuteron and alpha sublibraries plus EAF-2010.

## Official archives and acquisition checkpoints

The official TENDL-2025 archive page is
`https://tendl.imperial.ac.uk/tendl_2025/tar.html` (page says last updated 2025-07-04). HTTP HEAD on 2026-08-27
reported byte-range support and these exact lengths:

| projectile | official s30 archive | bytes |
|---|---|---:|
| neutron | `https://tendl.imperial.ac.uk/tendl_2025/tar_files/TENDL-n.tgz` | 3,517,450,425 |
| proton | `https://tendl.imperial.ac.uk/tendl_2025/tar_files/TENDL-p.tgz` | 2,352,215,809 |
| deuteron | `https://tendl.imperial.ac.uk/tendl_2025/tar_files/TENDL-d.tgz` | 3,063,536,212 |
| alpha | `https://tendl.imperial.ac.uk/tendl_2025/tar_files/TENDL-a.tgz` | 1,604,280,144 |

The total download is 10,537,482,590 bytes. Each archive is downloaded outside Git with byte-range resume, then
size-checked, gzip-tested, SHA-256 hashed and extracted independently. Each extracted corpus must contain the declared
2,850 regular s30 evaluations. Archive hashes and deterministic per-file-manifest hashes go into G7 evidence; raw
evaluations, archives, checkpoints and generated libraries never enter Git.

## Profile before the complete computation

G1 already exercised a four-worker W-186/Ag-107/Fr-226/Rb-94 neutron build under a 1 GiB total address-space cap:
peak RSS was 76,760 KiB, and its fresh/cached indexes and NPZs were byte-identical. G4's 38-target one-worker
neutron build took 645.4 s; its isolated four-worker kernel profile had an 8.658 s median and 28,012 KiB median RSS.
After archive extraction and before any complete build, one pinned Fe-56 unit from each TENDL-2025 corpus will be
profiled with `/usr/bin/time -v`; the G7 result will record wall time, peak RSS, rows and hashes. This settles the
representative unit and identifies whether parsing/collapse or neutron resonance processing dominates.

## Resumable build order and resource bounds

1. Run structural/hash preflight and the four one-target profiles.
2. Build alpha, proton and deuteron at 0 K on CCFE-162; build EAF-2010 at 293.6 K on CCFE-709; build TENDL-2025
   neutron last at 293.6 K on CCFE-709.
3. Use four workers, no concurrent heavy build, a 4 GiB address-space cap, and a separate content-addressed cache per
   corpus. A retry uses the same cache, so interruption resumes per source evaluation.
4. After every successful fresh build, rerun from the completed cache and require byte-identical NPZ/index output.
   Record target/row/ledger counts, convergence flags, archive and file-manifest hashes, builder/options/group hashes,
   cache hits, peak RSS, wall time and final artifact hashes.
5. Run the bounded EAF P2 regression, all P5–P9 controls, the CI subset and the full Rust quality gate before deriving
   `controls/check_p10.py` and closing P10.

An unsupported feature remains a fail-closed target error. It is diagnosed across the corpus before a scoped
implementation change; any such change gets a regression test and invalidates old builder-fingerprint checkpoints
by design.
