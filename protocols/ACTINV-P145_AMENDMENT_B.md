# P145 Amendment B — U1 compares without wall-clock time; the study regression runs locally

Registered 2026-10-16, after the implementation was written and before any P145 evidence run. Two statements of the
protocol are wrong as written; the gates are restated so they can be met, with no gate relaxed beyond what the
existing robustness controls already accept.

## 1. U1 and the `ms` field

Every run result carries a top-level `ms`, the wall-clock time of that solve. Two `study.robustness` runs of the same
study therefore never write byte-identical `rob_<i>.out.json` files, and the P30 and P136 controls remove `ms` before
comparing. U1 is restated: every kept per-sample result must equal the `study.robustness` file of the same index
byte for byte after the single top-level `ms` member is removed from both, and each file must carry exactly one such
member. A sample that fails must fail in both runs.

## 2. The study regression is not in CI

Change-under-test paragraph and U5 say the P30/P43/P136 controls check in CI that `study` outputs stay byte-identical.
No CI workflow runs them. U5 gains a local gate instead: `controls/check_p136.py` is run with the release binary of
the P144 candidate (`2b60db9`, SHA-256 `216e136c0619680cc00586b432c81ebed3df4e4ea9e721aaa47019f92ca46b69`, built
before any P145 change) as reference and the P145 candidate release binary as candidate. Its G2 (the default study
gives the same files, timing fields and the hashes that cover them excepted), G3 and G4 must pass.
