# P120 — Preserved-history verification reads pinned Git objects, not the working tree

Registered 2026-10-07, before changing any history verifier. The owner approved this change on 2026-10-07 ("Yes you
can do the actinv thing"; "Alright go ahead"), after it was explained as: keep every check on the archived records,
drop only the requirement that the live code never change. It is needed so that the owner-approved contact-dose fix
(P121) can change Rust source without turning CI red. Base commit: `9af5486` (origin/master).

## Defect

The CI verifiers that keep the P116, P117 and P118 records immutable compare the **current working tree** with the
source maps those phases froze, in addition to comparing the pinned commits' Git objects. Any later change to any of
the 100 Rust files, to the 27 handbook inputs, or to several hundred control and script files therefore fails CI,
although the preserved history is untouched. Static map (2026-10-06):

- `controls/check_p118_history.py`: lines 150 (319 terminal source files), 153–157 (100 Rust files, working-tree
  half), 158–162 (27 handbook inputs, working-tree half).
- `controls/check_p117_history.py`: lines 368–381 and 573–574 (193 control files and 100 Rust files).
- `controls/check_p116.py`: lines 530–580 (current Rust population and the single permitted twin-waste change) and the
  `--no-write` G0 re-derivation (lines 255–272, 596, 838–846), which hashes the current Rust and control files into
  the report compared with the sealed `results/g0_p116_twin_waste.json`.
- `controls/check_p116_history.py`: lines 165–208.
- The unit tests that call these verifiers live: `controls/test_p116_history.py`, `test_p117_history.py`,
  `test_p118_history.py`.

These are guards on the present, written as checks on the past. A verifier of a preserved record should establish
that the record, its archive and the commit it pins are intact, not that the code base never changes again.

## Change under test

Permitted existing source edits: the four verifiers and three test files named above. No other existing file
changes, except this registration, its `protocol_hash.txt` line, a ledger entry, `MANIFEST.sha256` and a short note
in `docs/maintainers/CI_DATA_RECOVERY.md`. `.github/workflows/ci.yml` is not edited, so every workflow-transition
guard stays exactly as it is.

1. Wherever a verifier compares a current working-tree file with a frozen digest and also compares the pinned
   commit's Git object with the same digest, the working-tree comparison is removed. The Git-object comparison stays.
2. Wherever a verifier compares only the working tree, it compares the pinned commit's Git object instead
   (P116: `2117f3b`; P117: `b81e8c3`; P118: `6f3f964`). The populations (100 Rust files; the 319, 193 and 99 file maps;
   27 handbook inputs) are taken from the pinned commit's tree, not from `git ls-files` of the working tree.
3. P116 G0 under `--no-write` re-derives its report from `2117f3b`'s Git objects and must still equal the sealed
   `results/g0_p116_twin_waste.json` byte for byte.
4. Unchanged: every archive population and digest check, discovery pins, receipts, raw/durable log agreement,
   resource and exit records, verdict identities, absent-artifact lists, the `ci.yml` transition guards, and the P116
   G1/G2 behavioural replay, which still runs the current release binary and byte-compares twin outputs with the
   sealed results. That replay is the guard on present behaviour and stays one.
5. Emitted verification records keep their schema. A field that asserted current-tree identity (for example
   `current_source_matches_except_p119_edits`) keeps its name only if its value is now established from Git objects;
   any such field is listed in the result record with what it now means. Committed historical results are not edited.

## Required evidence

Only the coordinator runs executable jobs, serially, under the required systemd cgroup (6 GiB, zero swap, 128 tasks,
200 % CPU), with disk-backed `TMPDIR`.

- **E1, unchanged verdicts on the unchanged tree.** Before any edit, run the CI controls steps from "P103 source-seal
  integrity" through "P118 terminal failure remains immutable" and record each exit. After the edit, at the candidate
  commit with no Rust change, the same steps exit identically, and every record they emit is byte-identical apart from
  fields named under item 5.
- **E2, the past is still guarded.** Fresh regressions show that each converted check still fails on: a mutated
  archive byte, a mutated or missing archive file, an extra archive file, a mutated discovery or sealed digest, and a
  pinned-commit object whose digest differs (simulated by pointing the verifier at a different commit).
- **E3, the present is no longer frozen.** Fresh regressions, in a disposable copy of the repository, add one comment
  line to `crates/actinv-core/src/photon.rs`, `docs/guide/results.md` and `controls/check_p116.py`, and add one new
  untracked `.rs` file; every converted verifier still passes. The test that asserted the opposite
  (`test_current_rust_tree_must_match_the_implementation_commit` and any equivalent) is replaced by this one, and the
  replacement is named in the result.
- **E4.** The P116 G1/G2 replay, cargo fmt/check/clippy/test, the manifest refresh (stage first) and the remaining CI
  steps pass on the candidate; CI is green on the pushed commit for all implementation workflows.

## Verdict

P120-PASS if E1–E4 hold, recorded by `controls/check_p120.py` from the stored step exits and records. Otherwise
P120-FAIL with the failing evidence preserved; a repair needs an amendment registered first.

## Not claimed

No preserved verdict changes. P116's PASS and P117/P118's FAIL dispositions, all archived bytes and every sealed result
are unchanged. Present behaviour stays guarded by behavioural replays, regression tests and the Rust quality gates,
not by forbidding source changes.
