# ACTINV P12 — session close, 2026-08-27

**Protocol:** `protocols/ACTINV-P12_PROTOCOL.md`
(`247e669691d99a5e548734528a069bb49962e6ae356ba14f962abcf2826ed715`).
**Verdict (`controls/check_p12.py`): P12-CONDITIONAL** — all six gates pass; the append-only repair history is
retained in Amendments A--E and every protocol hash is frozen in `protocols/protocol_hash.txt`.

| gate | result |
|---|---|
| G1 radiological responses | PASS — 80 independent comparisons pass; CLI, Python, prepared and mesh fields are exact; all 13 rejection plants fail closed |
| G2 primary tables | PASS — all 289 Meija/AME2020 abundance and mass pairs, 84 element sums and the generated Rust table reproduce exactly |
| G3 input reliability | PASS — two 10,000-case smoke runs are deterministic; the fixed 1,000,000-case partition covers all 11 public reader families with no process-level failure below 1 GiB |
| G4 FNG/ITER cell 620 | PASS — four selected nuclides pass all 170 endpoints; the worst material population difference is `2.88e-14`, and 120 independently read rates differ by at most `3.24e-16` relative |
| G5 release candidate | PASS — exact Rust gates, clean-clone controls, unpacked crates, standalone CLI, Python 3.9 stable-ABI wheel, source archive, licences and SBOM pass |
| G6 closure | PASS — G1--G5 re-derive, the repository inventory reproduces byte-for-byte, the payload commit is pushed, and its exact GitHub Actions run completed successfully |

## Release payload and CI

The frozen technical payload is commit
`0151dd06ee12bc047da34a9e35341d23590a12a9` with tree
`c16fcb93c704d3946aabe43c44ad934c7c553907` in
`https://github.com/AvilaLabs/ACTINV.git`.

GitHub Actions run `33134485488` for that exact commit completed successfully on the `master` push using
`.github/workflows/ci.yml`:
https://github.com/AvilaLabs/ACTINV/actions/runs/33134485488

The run passed Rust formatting, check, strict Clippy and tests; workspace and Python controls; source and data-subset
reproducibility; dependency, release-note and prior-evidence checks; release packaging; the CLI/Python end-to-end
comparison; P8--P12 controls; and the nested self-contained-clone check. It began at `2026-08-28T01:58:31Z` and
completed at `2026-08-28T02:06:16Z`.

## Frozen gate evidence

| result | SHA-256 |
|---|---|
| `results/g1_p12_radiological.json` | `7babfce5211468e8cf288df638e14a83b68e4480cd91d68bd1a7598752e8527b` |
| `results/g2_p12_primary_tables.json` | `0d5514d2366af897e583a34cc5b939753d3f7e4321807e16734d1ab0dfffa0ef` |
| `results/g3_p12_parser_fuzz.json` | `01f3579d1ea4affa87d66dd29f011b94c4c09d2714a1fe6cd7caf0f4e38213c5` |
| `results/g4_p12_fng.json` | `2561c1bbf0b537fb68d0602bf1361efe2bfeac9c2b3dba96931cbd320fb43198` |
| `results/g5_p12_release.json` | `dbac843d1fd43d6b8e92494c763eeb00489b4649e63b07b1d2f12cda9343b105` |

| protocol record | SHA-256 |
|---|---|
| `protocols/ACTINV-P12_PROTOCOL.md` | `247e669691d99a5e548734528a069bb49962e6ae356ba14f962abcf2826ed715` |
| `protocols/ACTINV-P12_AMENDMENT_A.md` | `21f73ecfa3858bc9967183e0bb090382c6512acd2f5b4e9f25252cf32c67571a` |
| `protocols/ACTINV-P12_AMENDMENT_B.md` | `c4c823c5bb07235df43a9e26c5c4b40e852745fbfb72723612b9867507df0769` |
| `protocols/ACTINV-P12_AMENDMENT_C.md` | `141a3e7dc70fd3d324930ffb6db328201f69c64bf447f65569650ca042fd559c` |
| `protocols/ACTINV-P12_AMENDMENT_D.md` | `91084144aa8ead0679bece73375c7880c8b6037ad5647d3bebc28539f30993f4` |
| `protocols/ACTINV-P12_AMENDMENT_E.md` | `1f05dab0e0fcd4df7a58afe3bdab2f319a553e796551a1c49d90d34117e1c6f1` |

## Delivered and boundary of the verdict

P12 completes the repository's technical v1.0 scope: configurable radiological responses, independently controlled
primary abundance/mass data, input robustness coverage, the scoped FNG/ITER activation comparison, user-facing
documentation and reproducible 1.0.0 CLI, crate and Python package candidates. The checker-derived verdict is
conditional because Amendments A--E remain part of the frozen audit trail; it is not a statement that a failed gate
was waived.

The close manifest covers every repository source and evidence file except itself and the two reports that derive
their manifest verdict from it (`results/g6_p12_complete.json` and `results/verdict_p12.json`). Amendment E freezes
that exact exclusion set; the closure commit binds all three generated files together without circular hashes.

No raw or generated bulk nuclear data is committed. The FNG/ITER comparison validates the supplied activation
history, one-group inputs, decay chain and schedule; it does not validate transport, shielding geometry, an entire
shutdown-dose model or a regulatory analysis. Radiological-response controls validate formulas and provenance, not
the suitability of a user's response table.

This technical close does not create a Git tag, GitHub Release, PyPI or crates.io publication, software
qualification, regulatory approval or licensing claim. Those remain separate maintainer actions.
