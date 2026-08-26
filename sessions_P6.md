# ACTINV P6 — session close, 2026-08-27

**Protocol:** protocols/ACTINV-P6_PROTOCOL.md (f97f8618…). **Verdict (controls/check_p6.py): P6-PASS.**

| gate | result |
|---|---|
| G1 clean-clone build | 9 s from a fresh clone to a working `actinv` |
| G2 CI control suite | pinned data subset, 10-target library, both entry points at 0.0 deviation |
| G3 wheel and binary | wheel installs in a fresh venv; `cargo install` binary works; both exact |
| G4 reproducibility | independent builds → byte-identical result JSON and certificates |
| G5 release notes | four known limitations carried from the roadmap, checker-enforced |
| G6 version and licence | 0.1.0 across three crates, MIT OR Apache-2.0, licences and changelog present |

**v0.1 is complete.** The milestone's own definition — *a stranger can install it, run it on public data, and re-derive
the validation* — is met and each half is gated: installation by G1/G3, public data by G2's pinned fetch, re-derivation
by the shipped harness and checkers.

**Remaining for the principal, both external acts:** making the repository public, and publishing the release.
