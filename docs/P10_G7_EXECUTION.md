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

`scripts/prepare_tendl_2025.py` performs the extraction into a new directory. It rejects absolute/parent paths,
links, special files, empty or duplicate flattened names, wrong projectile names, wrong archive sizes and wrong file
counts; the final flat input directory is published only after all 2,850 regular files have streamed successfully.
Its detailed external manifest records every file hash and defines the compact manifest hash used by G7 evidence.

All four downloads and safe extractions completed. The immutable acquisition identities are:

| projectile | archive SHA-256 | detailed manifest SHA-256 | compact file-manifest SHA-256 |
|---|---|---|---|
| neutron | `e547527688506cbe09813364dcefa2aed11f474139bfa129d7cd4ca24fae21fa` | `b578ab395c6c71d7727dfb0513e88effd96692862664a6279802638531239b67` | `f38df7c49da6cef8ac3d23c45c81dfb394829eefd38ee4af0db6dde92f0beaa4` |
| proton | `49340a03b0d9ac86598c6b710c0bc2ec0babd3fa0717a9ff1d75f042fccc5b0b` | `98a8bd55784c326b8696de91f494111326378e776a975a512e59806a8c9ec2ef` | `0458a6c20e0b2fbb23934d2672304d210ceef74b0fc2807e9d9271c9aacf6ffd` |
| deuteron | `34f459aea0b5ac9c40820c88d898618f926ec3b52858a5393e42d57707ec5f1c` | `afb52c55b2a1babca998cc3d8af0f7004c64f85d160e3c5aabf16a05839355d9` | `feaa774185fb215e45c6fdf6bb26670bfeae9e4263386cfcccd4b7abcd3fa47f` |
| alpha | `25520f6eb42ce024c065f85255277ed169b2f826e9fc24f5d093c99d5c60e018` | `e3aaf11e60c46b43361796c2c297bab4fb714fe57ab26a315594f2b4799dfdbf` | `ca8bd5ea75d3cc3590a9f4115d94ec54f2cc110a09275b782ace3d608b1b7c81` |

Each manifest has exactly 2,850 regular files and no link or special entry. The normalized 816-file EAF-2010
manifest has SHA-256 `87baeeef62650cdf8791bd3f198c906b1e6787eb7017a3ec4b02d4cee88bc15e`.

## Complete-neutron preflight repairs

The first strict neutron pass found the bounded upstream defects frozen in
[Amendment D](../protocols/ACTINV-P10_AMENDMENT_D.md) and the Reich–Moore cancellation defect frozen in
[Amendment E](../protocols/ACTINV-P10_AMENDMENT_E.md). The official extraction remains unchanged. The deterministic
working preparation program has SHA-256 `84a65826e87876bd9bc891bc34897412daf0eae63c092bb88a4b8f654d532190`;
its external detailed manifest has SHA-256
`a6d17f996153d2671c0c51bfb6303e2a87a5af03e0696bfb34d668a31dbfb2a2`, and its compact file manifest is
`b1ea3fe043ec243e2df0a3894206872c2ce18c3b4541c19b35029b3ed3e7b15c`. Exactly 2,849 files are byte-identical;
only the two frozen Pb-208 fields differ.

`results/g7_p10_neutron_sources.json` independently scans 267,559 Breit–Wigner records, re-hashes all working
files, checks strict rejection plants, proves two different finite Pb-208 substitutions produce a byte-identical
50-row activation library, and matches the repaired Reich–Moore capture value to an 80-decimal-digit calculation.
Its result SHA-256 is `55c55195edb99d40be7a9c92540b678ea6a86c7b11f2a4e9c3870f0b62b69681`.
The post-repair Rust parse of all 2,850 working evaluations completed in 95.18 s with 13,012 KiB peak RSS and zero
errors. The strict Rust format/check/Clippy/test checkpoint also passes.

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

## Final execution record

The final post-Amendment-N builder fingerprint is
`7a50ba3441b30b829ae857ed192b2e52554d6c149460475f7735599f29548a43`. Each fresh/cached pair is byte-identical and
all cache inventories, source manifests, options, group hashes and index links re-match. Times are `/usr/bin/time -v`
wall clock under the recorded four-worker, 4 GiB address-space cap; RSS is the fresh-build maximum.

| corpus | targets | rows | fresh / cached | fresh peak RSS KiB | NPZ SHA-256 |
|---|---:|---:|---:|---:|---|
| TENDL-2025 neutron | 2,850 | 167,735 | 2:34:58 / 1:38.63 | 2,325,904 | `ec4c72bf598dc8ad3d533d9cfafdcf493e2d1f949a3e4db6251495659b68cc44` |
| TENDL-2025 proton | 2,850 | 528,057 | 1:53.68 / 1:10.26 | 1,768,056 | `0da7a35b37fd3b305ac2166ec092cdfb78123e76f8647d8808915e2c708d9790` |
| TENDL-2025 deuteron | 2,850 | 548,706 | 2:05.14 / 1:15.48 | 1,845,148 | `8050988981518cd63ac0c2ad76c6756370b154ea9f5a6d6435aa5f132b9d99ae` |
| TENDL-2025 alpha | 2,850 | 489,279 | 1:39.45 / 1:02.43 | 1,649,180 | `ead1141bfe07ec1a02055af014f8db0a49effe2fd60c29d181a505f7c6d10915` |
| EAF-2010 neutron | 816 | 115,702 | 1:21.74 / 0:59.07 | 1,404,044 | `5de78c8efec0501417297175378490beb6d21205308f632948db25171cb9b1a2` |

Total: 12,216 targets and 1,849,479 rows, with zero target errors, unsupported fallback entries, convergence flags or
swaps. The complete EAF control independently re-collapses all 816 targets and scores 2,787,099 unchanged points.

Amendment N records the corpus-bounded Co-58 linearization-depth repair. Amendment O aligns the independent G4 width
control with the previously frozen NJOY component-width semantics. Amendment P repairs the historical P6 version
checker so later coherent semantic versions do not falsely regress P6; Amendment Q makes the separately rooted Rust
extension crate part of that coherence check. Amendment R normalizes only that intentional solver-semver change in
G6's legacy-result hash while requiring every other leaf to remain identical. All are hash-pinned in
`protocols/protocol_hash.txt`; no frozen file was edited after hashing.

The final G1–G7 evidence, P5–P9 verdicts, CI subset, clean-clone regeneration check and exact Rust quality commands are
assembled by `controls/g7_p10_complete.py`. The checker-derived close is **P10-CONDITIONAL**, completing technical
v0.5 without claiming finite-dilution self-shielding, a licensed FISPACT executable run, a tag or publication.
