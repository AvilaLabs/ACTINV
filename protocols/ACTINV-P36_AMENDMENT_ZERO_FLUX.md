# P36 cache compatibility correction for zero-flux runs

Frozen 2026-09-18 before executing the corrected implementation.

GitHub run 35363227073 failed P8-G5 at ordinary cell 0, whose spectrum is
identically zero. P36's shape-normalized collapsed-cache validation requires
positive sums, inadvertently rejecting even a zero-flux artifact requested
with the same all-zero vector. The collapse writer already supports zero
flux; the P8 mesh control includes it as the independent ordinary/mesh case.

Accept the zero-flux case only when the vectors have equal nonzero length
and every entry in both vectors is zero. Do not change positive-flux shape
tolerances, cache identity hashes, checksum validation, or positive/zero
mismatch rejection. No division by zero is performed on this path.

Acceptance: a dedicated unit regression accepts identical zero vectors and
rejects a positive request against a zero cache, a group-count mismatch,
NaN, and infinity. Preserve the existing reverse-direction rejection test
(positive cache / zero request). Run all Rust quality gates and the unchanged
P8 mesh/ordinary identity control in GitHub CI. Do not alter P8 acceptance
criteria or overwrite its historical evidence to obtain a pass.
