# ACTINV P113 — Amendment A: portable complete-output evidence

Registered 2026-10-04 before amended control edits or new gate evidence.
This consumes P113's single repair round. The original protocol remains
unchanged, SHA-256 `0495157f3e8308d6a28475b94363e24b932f462c10f901518fa1c809a68ebbfa`.

## Discovery and retained evidence

Static inspection after the initial passing G0 seal found that the proposed
G1 `output_sha256` hashes the raw twin output, whose existing `mesh_output`
field contains the absolute working-directory path. Identical input bytes
therefore cannot replay the persisted evidence on a different checkout path,
including GitHub Actions. G1 and G2 have not executed; no failed executable
gate is asserted. The initial Rust quality observations passed, but those
preceding the final waste-row presentation change are superseded for G3.

Retain all 59 original bound source/artifact/log files under
`results/failures/p113_initial_seal/`. This directory name does not change the
passing status of the retained seal. Its static discovery record has SHA-256
`0f099f92637eea2e3346f02be0d588f9f6d96c95cdd41aae0d7050b1fbd36c4f`
and lists every retained file's exact SHA-256. Original G0 SHA-256 is
`61d8c6faaf7eb93d8f3a4d9e369e8710eee45b93f946d405f54c216dd730a037`.

## Sole control correction

For each successful request, including assay cases, require the output's
`mesh_output` to equal the exact absolute mesh path declared by that request
in its execution directory. Make a deep copy of the complete decoded output,
replace only that verified field with the declared relative mesh reference,
then hash canonical sorted JSON. Any mismatched path is a control failure.
Keep every other output field in this digest. Preserve raw output-byte repeat
checks and the complete stable G1/G2 comparison; do not discard scientific,
assay, compatibility, ordering, numeric or categorical fields.

Add independent regressions for equal complete-output hashes across two
execution roots, changed scientific/legacy values changing the digest, and
an incorrect absolute mesh path refusing normalization. Amend G0 to bind this
registered amendment and all retained evidence, verify their exact identities,
and record `repair_rounds: 1`. Reseal and replay the whole G0 before the first
G1. Update source/seal/verdict regression metadata and G3 to the same repair
identity, retaining the original successful observations separately.

The exact fixture stays unchanged:
`e6bcebc856c7faaa571455a45c81da49e988a34bfd1f7149bc67656d93d5f956`,
35 requests and 138 component-target results. No case, expected value,
production behavior, source row, threshold, tolerance, input/output contract,
required gate, or scope changes under this amendment. Final changed-source
Rust quality and a fresh release are still required. Another failed gate after
this round is terminal FAIL and requires a separately registered successor.
