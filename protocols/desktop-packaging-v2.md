# Desktop preview packaging control, version 0.2.0-preview.1

Frozen before package verification, 2026-10-01.

Desktop version `0.2.0-preview.1` and prospective tag `desktop-v0.2.0-preview.1` package the
existing desktop interface on solver/library/Python version `1.4.0`, replacing the
`0.1.0-preview.1` packages built on solver `1.0.1`. No interface feature, historical scientific
verdict or solver equation changes in this packaging step.

The packaging, test and evidence requirements of `desktop-packaging-v1.md` apply unchanged:
the same four platform packages, pinned packaging tool, identification of version, target, exact
source commit, binary hash and package hashes, the synthetic P11 fixture model checks with exactly
equal reopened result JSON, native captures where a graphical session is available, and disposable
install/uninstall locations. Compile-only, model execution, native rendering and manual
interaction evidence stay distinct. Existing Rust gates and the full scientific CI remain
required.

In addition, each package's recorded solver version must read `1.4.0`, and the source commit must
contain the `v1.4.0` software release.

Publish first as a GitHub pre-release with the actual evidence and remaining limitations.
Promotion to a normal release, and pointing the download page at it, follows the maintainer's
manual test.
