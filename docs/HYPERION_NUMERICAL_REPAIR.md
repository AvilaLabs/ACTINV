# HYPERION numerical repair

## Cause and change

The updated HYPERION comparison exposed an ACTINV floating-point issue, not a
remaining upstream spectrum error. In the custom sparse linear solve used by
CRAM, partial row pivoting can couple a trace radioactive population to a very
large stable inventory. Subtractive cancellation can then create inaccurate
trace populations. A small error relative to total inventory does not establish
accuracy for an individual radionuclide.

A two-state decay invariant reproduced the failure without nuclear data:
an initially absent parent must remain absent beside its stable daughter.
The pre-fix Rust regression failed; the independent SciPy/OpenMC calculation
preserved the zero parent. Reversing the state order removed the original
failure, and the diagnostic records identify the row exchanges.

The repair retains the sparse factorization and pivoting policy, then refines
each solution against its original matrix. Residual accumulation uses compensated
sums and fused-product remainders; correction solves reuse the factorization.
There are at most five corrections, with early termination if the solution no
longer changes. Non-finite residuals, corrections or solutions return errors.
The scalar, multiple-right-hand-side and tangent CRAM paths all use this method.
This follows the residual-correction approach documented by
[LAPACK ZGERFS](https://www.netlib.org/lapack/explore-html/d5/da4/group__gerfs_ga2aaa4e39caf445d7a9429942f60ed362.html);
it is an ACTINV implementation, not a call to LAPACK.

No population cutoff, nuclear-data substitution, composition adjustment or
acceptance-band relaxation was used. The legacy `numerical_floor` scale is
explicitly documented as **not** a bound on total floating-point error.
CRAM16's alpha0 is 2.124853710495224e-16; CRAM48's is
2.258038182743983e-47. These asymptotic approximation constants do not bound
linear-solve roundoff or conditioning errors.

## Verification scope

Regression coverage includes CRAM16/48, six stiffness scales from 1e-6 to 1e12,
four stable-background populations from 1 to 1e24, both parent/daughter state
orders, scalar/batched/tangent paths, a populated three-state Bateman chain in
all six permutations, and a general complex system that requires row pivoting.
The latter also checks an independently accumulated componentwise scaled
backward error. These are finite fixtures, not an all-matrices error guarantee.

The frozen protocol is `protocols/HYPERION-NUMERICS-2026-09-15.md`. Original
failures, reports and archives are retained. Fresh runs and verification receipts
are written separately under `results/hyperion-numerics-fix/` and
`target/hyperion-numerics-fixed/`.

The numerical candidate is built from an isolated tracked-source snapshot plus
only the three numerical Rust files. This excludes unrelated release/data edits
in the shared worktree. The candidate is not a released binary; its provenance
and the exact snapshot are recorded by the preflight receipts.

An initial build in the shared working tree failed because an unrelated
release edit referenced an unavailable data-catalog file. That failed build log
is retained as `target/hyperion-numerics-build-release.log`. No release file was
restored or replaced to bypass it. The isolated snapshot is based on
`ecbf028a6755d4e862dc221c1fd14400a357ed2d` (CLI/core 1.1.0), not the concurrent
working-tree version changes.

## Executed results

All 27 fresh CLI cases completed: 15 updated region/recipe cases, ten prior
shared-spectrum fast/thermal cases, and two joint-composition cases. All frozen
acceptance bands were retained.

| Comparison | Checks | Failures before | Failures after |
| --- | ---: | ---: | ---: |
| Updated-package independent raw-matrix rows | 9,410 | 98 | 0 |
| Prior fast/thermal independent raw-matrix rows | 19,507 | 167 | 0 |
| Joint-composition independent raw-matrix rows | 3,768 | 29 | 0 |
| Updated supplied inventories above 1 atom/g | 9,284 | 3 | 0 |

The three former Ta-182 exceptions now agree with the updated supplied values
to approximately 1e-15 relative:

| Region / cooling | Previous ACTINV | Supplied | Repaired ACTINV |
| --- | ---: | ---: | ---: |
| BLK_mid / 100 yr | 21034.758811 | 20819.805500 | 20819.805500 |
| BLK_back / 100 yr | 3526.989988 | 3572.611354 | 3572.611354 |
| BLK_back / 1000 yr | 3654.357289 | 3572.361013 | 3572.361013 |

Units are atoms/g. Across all 9,284 scored supplied inventories the maximum
relative difference is 1.11726948e-6 (0.000111727%). All 120 updated combined-index
checks and all 40 updated N50 contact-dose CSV checks pass their original bands.

Fresh independent assembly/solve checks for the 17 N50/joint cases pass all
32,395 meaningful inventory comparisons and all 408 Table-1/photon-source/dose
endpoint comparisons. Maximum independent dose difference is 3.64905253e-10
relative. The fresh scored population count is lower than the historical 32,685
because some old numerical artifacts no longer exceed 1 atom/g; every historical
row was also rechecked, so those disappearing artifacts were not excluded from
the before/after acceptance test.

The isolated snapshot passed all four required workspace commands: formatting,
check, Clippy with warnings denied, and all-target/all-feature tests. There were
182 passed tests and no failures; one existing desktop-worker test was explicitly
ignored because it requires a generated P11 fixture and `controls/check_desktop.py`.
That ignored check was not executed and is not claimed to pass. All 52 core tests,
including the new numerical regressions, passed.

Candidate SHA-256:
`7e306fe8174f36758bbc98fb28d05fea04d6c38a5b958e08835d8445ef61500f`.
The scientific campaign body took 91.946 s on the shared workstation with prepared
data/cache; this is not an isolated performance benchmark or a speedup claim.
Exact source hashes, input/run receipts, comparisons and cgroup limits are in
`results/hyperion-numerics-fix/fixed_summary.json` and the adjacent preflight receipt.

## Remaining scope limits

The repaired solver does not qualify the underlying nuclear data, upstream
waste-class labeling, or contact/self-dose model. Independent matrix controls
use different linear algebra but share the CRAM48 approximation. The comparison
uses the public energy-binned transport output, not a new transport simulation.
The dose estimate remains a one-dimensional uncollided contact/self-dose model
with ICRP-116 AP effective-dose response, not a three-dimensional dose map or an
access/disposal approval. No external response, public post, release or push is
part of this numerical repair.
