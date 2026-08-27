# ACTINV P10 Amendment P — forward-compatible P6 version hygiene

**Date:** 2026-08-27. **Trigger:** The P10 close reran the P5–P9 verdict checkers after confirming every underlying
gate result still passed. `check_p6.py` changed the historical P6 verdict from `P6-CONDITIONAL` to `P6-FAIL` solely
because its version-hygiene expression required every future checkout to remain exactly `0.1.0`. ACTINV advanced all
workspace crates and the Python package together to `0.2.0` at the checker-derived P8 close.

## Diagnosis

P6 G6 requires coherent version and licence metadata, not a permanent ban on later versioned milestones. The current
three workspace crates are uniformly `0.2.0`, the Python project is `0.2.0`, every crate and the Python package declare
`MIT OR Apache-2.0`, and both licence files plus the changelog remain present. The hard-coded equality therefore
mistook the intended P8 release progression for a P6 regression.

## Frozen repair

1. P6 G6 requires one valid three-component numeric semantic version shared exactly by every workspace crate and the
   Python project, at or above P6's `0.1.0` baseline.
2. Every workspace crate and the Python project must declare `MIT OR Apache-2.0`; `LICENSE-MIT`, `LICENSE-APACHE` and
   `CHANGELOG.md` must exist. The checker reports the actual common version.
3. No prior P6 result, release note, source package, licence, product behavior or scientific tolerance changes. P6
   remains `P6-CONDITIONAL` only if all original P6 gates and the corrected metadata invariant pass.
4. G1, the P5–P9 verdict checkers, the CI subset and the complete P10 controls must pass before G7 closes.

This is a historical checker-maintenance correction. It does not alter Rust builder source, the post-N fingerprint,
or any complete external library.
