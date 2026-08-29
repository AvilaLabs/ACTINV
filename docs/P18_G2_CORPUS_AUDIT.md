# P18 G2 four-corpus product-state audit

P18 G2 is complete and **fails its frozen scientific gate**. The result is not an interrupted run or an incomplete
sample: a bounded Rust probe read all 2,850 evaluations for each of neutron, proton, deuteron and alpha, accounting
for all 11,400 files and 1,810,499 MF=8/9/10 declarations. No measurement or held-out isomeric-ratio value was read.

The catalog and declaration machinery behaved cleanly. Every corpus produced the same 2,323-nuclide, 2,850-state
catalog in forward and reverse file order; there were no missing files, missing MF3 totals, conflicting duplicate
declarations or descriptor-set omissions. ENDF `LFS=0` is treated as an intrinsically identified ground state even
when that product has no separate target evaluation. Positive states still require an evaluated excitation match.

## Frozen conservation result

The protocol requires each MF10 state partial and each mutually exclusive per-product state sum to remain below its
MF3 reaction total at every union-grid energy and after group collapse. The allowed excess is exactly
`max(1e-12 b, 5e-10 * max(total, peak_total))` pointwise and
`max(1e-14 b, 5e-10 * max(total, peak_total))` after collapse. MF9 yields are checked as derived partials under the
same rule. Those limits were frozen before the scan and were not relaxed after seeing the result.

| projectile | declarations | files with a violation | comparison violations | MF9 | MF10 |
|---|---:|---:|---:|---:|---:|
| neutron | 459,764 | 2,365 / 2,850 | 2,128,813 | 85,198 | 2,043,615 |
| proton | 451,758 | 2,411 / 2,850 | 175,883 | 0 | 175,883 |
| deuteron | 463,400 | 2,417 / 2,850 | 264,272 | 0 | 264,272 |
| alpha | 435,577 | 2,253 / 2,850 | 78,647 | 0 | 78,647 |

The failures span both small evaluated-rounding/domain mismatches and large pointwise shape mismatches. For example,
the proton Fe-52 MF10 partial is already positive at the exact MF3 threshold where the corresponding total is still
zero. An independent fixed-column read of the source records reproduces both values. The worst excess is the
neutron Nb-85m MT103 state sum at `1.258925e-3 eV`: `6.581325577782869e11 b` against a
`6.16396e10 b` total, with a `345.804 b` tolerance. The compact evidence records the worst comparison for every
projectile, including the total peak needed to rederive its tolerance.

## Identity audit

All declarations enter one predeclared accounting decision, and all unambiguous emitted identities are backed either
by the ENDF ground-state definition or by a physical excitation/catalog match. The candidate would change 45,320
distinct old-rank identities across 10,719 source files:

| projectile | changed identities | changed files | MF8 versus Q conflicts |
|---|---:|---:|---:|
| neutron | 10,948 | 2,542 | 28 |
| proton | 11,589 | 2,722 | 44 |
| deuteron | 11,514 | 2,720 | 25 |
| alpha | 11,269 | 2,735 | 46 |

The 143 conflicts are all explicit rather than silently rank-remapped. Their MF8 `ELFS` and MF9/10 `QM-QI` values
differ by one to two electron-volts under the frozen one-eV/relative tolerance. Two differences sit on the exact
one-eV boundary before binary floating-point subtraction; removing those two cannot change the failed gate because
141 conflicts and the conservation failures remain.

Every changed identity is retained in the deterministic
`results/g2_p18_changed_identities.json.gz` artifact. Its uncompressed canonical JSON is 19,346,122 bytes and its
SHA-256 is `869eb618265f1159879cf644f44f67ad7189d24b8eaf455f299c905d8a102785`; deterministic gzip reduces it to
898,317 bytes with SHA-256 `606347ce4d12788451be3a4a3765bfa5305613631eb37ea5de30d163546e083e`.

## Bounded execution and independent check

The probe processed one evaluation at a time, checkpointed one JSON line per file and ran each projectile
sequentially under the protocol's 12 GB virtual-memory cap. Observed peak RSS was 13--20 MB; no array approached the
1 GiB limit. Raw TENDL inputs and the 68--72 MB per-projectile checkpoints remain external to Git. Only provenance,
hashes, aggregates, worst cases and the compact changed-identity artifact are committed.

`controls/check_g2_p18.py` imports no production or audit module. It binds the four manifests and checkpoints,
repeats declaration/comparison/violation arithmetic, independently recomputes every recorded worst-case tolerance,
decompresses and checks all 45,320 changed identities, and rejects seven mutation plants. A passing checker means the
failed G2 verdict is complete and reproducible; it does not turn the scientific gate into a pass.

Because G2 is terminally false, the cost-control rule forbids expanding into affected-target builds, G3 runtime work,
diagnostic measurement scoring or held-out unsealing. P18 cannot authorize v1.1.0. The public v1.0.1 release and its
data artifacts are unchanged. Resolving the evaluated precision, threshold-domain and state-sum semantics requires a
new pre-evidence protocol; it cannot be achieved by weakening P18 after seeing these results.

## Reproduction

With the four hash-pinned external TENDL staging trees and the ignored checkpoints already generated:

```text
python controls/g2_p18_corpus_audit.py --no-write
python controls/check_g2_p18.py --no-write
```

The committed aggregate is `results/g2_p18_corpus_audit.json`, and the independent checker result is
`results/g2_p18_check.json`.
