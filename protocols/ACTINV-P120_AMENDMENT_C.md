# P120 Amendment C — two live guards in P110: the solver version in G1 and the working tree in G0

Registered 2026-10-16, after the red `controls` run on the v1.5.0 release commit and before any edit to
`controls/check_p110.py`. P120's rule applies unchanged: keep every check on the archived records; drop only the
requirement that the live code never change. Amendment B asked that any further live guard get its own amendment
before it is edited; this is that amendment.

## What happened

The release commit `79a2d36963af061b34cfe3d8ff50d17874f273cc` (tag `v1.5.0`) raised the workspace version from
1.4.0 to 1.5.0 and changed nothing else that P110 reads. `controls` run 38901865435 failed in the step "P110
native composition basis and witness verification": G1 `persisted_result_matches` was false because all nine
`case_evidence[].output_sha256` values differed. The tag-gated publish workflows stopped at their green-controls
gate, so nothing was published.

The cause is the solver version. P110 was sealed at `f31e0f1` (2026-10-03), when the workspace version was 1.4.0,
and G1 hashes the raw output of `actinv waste composition solve`, whose solver certificate carries
`"solver": "actinv-core <version>"` (3 to 5 times per output). With the 1.5.0 release binary, replacing the bytes
`actinv-core 1.5.0` by `actinv-core 1.4.0` makes each of the nine outputs hash exactly to its sealed
`output_sha256`; no other `1.5.0` occurs and no other byte differs. The record is
`results/failures/release_v1.5.0/p110_version_binding.json`, and the failed step's CI log is preserved beside it.
A raw hash over the version string forbids every release: a guard on the present written as a check on the past.

A second guard of the same kind was found while reading: G0 under `--no-write` hashes the 23 frozen control files
from the **working tree** into `control_hashes` and compares the report with the sealed
`results/g0_p110_composition_solve.json`. Editing `check_p110.py` for the first guard would therefore fail G0. The
23 sealed digests equal the Git objects at `f31e0f1` (0 mismatches, checked before this registration).

## The change

Only `controls/check_p110.py` changes, plus a new regression file `controls/test_p120_amendment_c.py`, this
registration, its `protocol_hash.txt` line, a ledger entry, `MANIFEST.sha256` and the preserved failure record.
`.github/workflows/ci.yml` and every committed result are unchanged.

1. **G0.** `control_hashes` is computed from the Git objects of the 23 files at `f31e0f1`, P120 item 2. Every other
   G0 input is read as before.
2. **G1.** Before `output_sha256` is computed, the exact bytes `actinv-core <V>` are replaced by
   `actinv-core <S>`, where `<V>` is the version in the current workspace `Cargo.toml` and `<S>` is the version in
   `Cargo.toml` at `f31e0f1` (1.4.0). Nothing else is normalized. The repeat-run byte comparison, the oracle
   validation, the mutation and refusal checks, and every other field use the raw output. When `<V>` equals `<S>`
   the bytes are unchanged. Any other byte difference still changes `output_sha256` and fails G1.
3. Unchanged: the sealed G0, G1 and G2 records, the P110 verdict, its history, and the P111–P118 checks that pin
   them.

## Required evidence

Only the coordinator runs executable jobs, serially, under the required systemd cgroup, with disk-backed `TMPDIR`.

- **C1.** The preserved record above.
- **C2, regressions** in `controls/test_p120_amendment_c.py`: the token map changes only the exact token; an
  output that differs in any other byte keeps a different digest after the map; the map is the identity when the
  versions are equal; G0's control hashes from `f31e0f1` equal the sealed `control_hashes`, and the same function
  pointed at a commit where the files differ does not.
- **C3.** With the 1.5.0 release binary, the CI steps P103 through P118 exit 0 exactly as `ci.yml` lists them, and
  `check_p110.py --no-write` reproduces the sealed G0, G1 and G2 records byte for byte.
- **C4.** CI green on the pushed commit for every triggered workflow.

## Not claimed

No preserved verdict changes. P110's records stay as sealed at 1.4.0; they now also replay under a later solver
version, with the version token the only bytes mapped.
