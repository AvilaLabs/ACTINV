# ACTINV-P169 Amendment A — the source condition in G1 excludes the browser crate

Registered after the train and test records were written (on registration commit e932d36) and before any verdict.
This is the protocol's one repair round.

## Defect

G1 required each record's `crates/` tree to equal d6f516b's. Between d6f516b and e932d36, one file changed under
`crates/`: `crates/actinv-gui/src/web_app.rs` (the browser workbench redesign, 781f369). The ACTINV command-line binary
under test does not depend on `actinv-gui`: `crates/actinv-cli/Cargo.toml` names no such dependency and
`cargo tree -p actinv-cli -e normal` lists no `actinv-gui` package. The pinned binary is unchanged
(`186cc2587bb61766b3b3c426c61cd44f00ed472401026dea2ca3dd595c8d1194`). The gate was mis-specified. The P168 protocol
already words the same condition correctly.

## Change

G1's source condition becomes: every path under `crates/` that differs between d6f516b and the record's `git_head` lies
under `crates/actinv-gui/`. The binary pin, clean-tree and cgroup conditions are unchanged. `controls/check_p169.py`
implements this with `git diff --name-only d6f516b <git_head> -- crates`.

No candidate, selection rule, threshold, data pin or record is changed, and nothing is rerun. The records stand as
written.

## Disclosure

The checker's output on the unamended gates has been seen, including the selection (A, no candidate eligible) and the
held-out numbers. This amendment cannot change any of them. It decides only whether G1 accepts records made from a
tree that differs from d6f516b in the browser crate alone.
