# v1.0 public-release checklist

The P12 checker closes the technical repository release. The maintainer performs the public acts below separately;
none is automated by the control suite.

P12 uses two commits to avoid a circular claim. The session records the immutable release-payload commit and its
successful workflow run; the following closure commit adds that attestation, final verdict, and manifest. Run and
confirm the workflow on the closure commit too. The public software tag belongs on the final green release commit,
including subsequent packaging-only release plumbing, not on the earlier technical payload commit.

## Before publishing

- [x] Confirm `controls/check_p12.py` reports `P12-CONDITIONAL` or `P12-PASS` on a clean clone.
- [x] Confirm the pushed commit's required GitHub Actions run is green.
- [x] Run the `release artifacts` workflow for that exact commit and download every artifact.
- [x] Verify artifact SHA-256 values and `actinv --version` / `actinv.__version__` are `1.0.0`.
- [x] Install a wheel into a new environment and run the documented smoke calculation.
- [x] Review [v1.0 release notes](RELEASE_NOTES_v1.0.md), [qualification boundary](QUALIFICATION.md), licences, and
  third-party-data notices.
- [x] Confirm the `actinv` names and maintainer accounts on PyPI/crates.io and use scoped credentials for each initial
  publication.
- [x] Add the `publish-crates.yml` trusted publisher to all three crates.io packages as described in the
  [crates.io release procedure](CRATES_RELEASE.md), revoke the initial API token, and record the maintainer
  [configuration attestation](../results/session_v1_crates_trusted_publishing.json).
- [x] Follow the account and environment setup in [PyPI release procedure](PYPI_RELEASE.md), publish the exact candidate
  to TestPyPI, and smoke-test both `import actinv` and the installed `actinv` command.

## Public acts

- [x] Create the signed `v1.0.0` tag at the final green software-release commit and push it.
- [x] Publish the Python wheels and source distribution to PyPI.
- [x] Publish Rust crates in dependency order: `actinv-data`, `actinv-core`, then `actinv-cli`.
- [x] Create the GitHub Release from the signed tag, attach standalone binaries and `SHA256SUMS`, and paste the v1.0
  release notes.
- [x] Install with `pip install actinv` and `cargo install actinv-cli` from the public registries in clean environments.
- [x] Record public URLs, upload identities, artifact hashes, and smoke-test results in the append-only
  [v1.0 software release record](../results/session_v1_release.json) and
  [crates.io publication record](../results/session_v1_crates_io.json).

## Versioned data release

- [x] Run `controls/g1_p13_data_distribution.py` with the exact release binary.
- [x] Stage only the catalog-named TENDL P10/P11 outputs with `scripts/prepare_data_release.py`; do not stage raw
  evaluations, caches, temporary archives, or EAF-2010.
- [x] Run `controls/g4_p13_release_stage.py STAGING_DIRECTORY` and verify every identity against the catalog and prior
  P10/P11 evidence.
- [x] Create the immutable `data-v1.0.0` GitHub release at the green P13 source commit and attach the staged assets,
  catalog, notice, `SHA256SUMS`, and `SIZES`.
- [x] From a clean directory, run `actinv data fetch`, `actinv data verify`, and a documented smoke calculation using
  the hosted assets. Record the release URL, tag commit, release ID, asset identities, and workflow result.

Software publishing must not include raw nuclear-data inputs, generated bulk libraries, credentials, local paths, or
caches. The separate data release may contain only the exact processed CC-BY-4.0 files named by the frozen catalog;
the official decay archives remain hosted by the IAEA and are not rehosted by ACTINV.

## data-v1.1.0 (derived-corpus release)

- [x] Build the full patched-corpus neutron artifact with `controls/p25c_release_build.py` (bounded, resumable
  iterate-and-evict; the complete failure ledger lands in `results/p25c_release_build.json`).
- [x] Rebuild the matching MF=33 covariance sidecar with `controls/p25c_release_covariance.py`; the sidecar index is
  bound to the patched activation artifact by `activation_library_sha256`.
- [x] Stage assets and generate the v1.1.0 catalog with `controls/p25c_release_stage.py`; verify with
  `controls/check_p25c_release.py`.
- [x] Create the immutable `data-v1.1.0` GitHub release at the release commit and attach the staged assets, catalog,
  notice, `SHA256SUMS`, and `SIZES`.
- [x] From a clean directory, run `actinv data fetch`, `actinv data verify`, and a documented smoke calculation using
  the hosted assets. Record the release URL, tag commit, release ID, and asset identities.
- [x] Tag the v1.1.1 software release (the embedded catalog requires it) and publish through the normal tag
  workflows.

## v1.1.2 (packaging-fix release)

The v1.1.1 tag carried a stale `smoke_python_wheel.py` catalog constant that rejected the correct embedded
catalog v1.1.0 and blocked that tag's PyPI publish; tag-triggered workflows check out the tag commit, so the
fix on master could not retro-apply. v1.1.2 re-fires the tag workflows on a checkout that contains it.

- [x] Bump the workspace, `python` and inter-crate versions to 1.1.2; refresh `MANIFEST.sha256`.
- [x] Tag `v1.1.2` at the release commit and push; the tag workflows publish release artifacts, crates.io and
  PyPI.
- [x] Approve the `crates.io` and `pypi` environment gates; confirm all three crates and the wheel/sdist set
  land at 1.1.2.
- [x] Attach the packaged platform archives and `SHA256SUMS` to the `v1.1.2` GitHub release; record the
  release URL, tag commit, release ID, and asset identities in `results/p25c_release_publish.json`.

## v1.2.0 (feature release — draft)

- [x] Bump the workspace, `python`, `pyproject.toml` and inter-crate versions to 1.2.0; update
  `release-artifacts.yml`, README release links and the DATA_LIMITATIONS header; refresh
  `MANIFEST.sha256`.
- [x] Write the v1.2.0 changelog section and `docs/RELEASE_NOTES_v1.2.0.md` from the 79 commits
  since v1.1.2 (25 code-touching).
- [~] `controls` workflow on the release commit: fmt+manifest gates fixed and pass; the suite
  still fails at `check_g3_p18b` (generated-leg fixture) — pre-existing on master since
  2026-09-19, not release-introduced; publish paths do not gate on it.
- [x] Tag `v1.2.0` at the release commit and push; tag workflows publish release artifacts,
  crates.io and PyPI. (Tag commit `9808219`; original crates run stalled pending and was
  resumed via `workflow_dispatch` run 35676054284.)
- [x] Approve the `crates.io` and `pypi` environment gates; confirm all three crates and the
  wheel/sdist set land at 1.2.0. (Verified: PyPI `actinv 1.2.0` sdist+5 wheels;
  crates.io actinv-data/actinv-core/actinv-cli 1.2.0.)
- [x] Attach packaged platform archives and `SHA256SUMS` to the `v1.2.0` GitHub release; paste the
  release notes. (https://github.com/AvilaLabs/ACTINV/releases/tag/v1.2.0)
- [x] Record the release URL, tag commit, release ID, asset identities and smoke results in
  `results/`. (`results/release_v1.2.0.json`; production PyPI smoke: `pip install
  actinv==1.2.0` + `actinv --version` -> 1.2.0)
- [x] TENDL-2017 In-115 repair decided: benchmark-internal artifact only, NOT shipped as a
  data release — TENDL-2025 already carries the correct evaluation. Disclosed in
  DATA_LIMITATIONS.md.

## v1.2.1 (patch release)

- [x] Bump the workspace, `python`, `pyproject.toml` and inter-crate versions to 1.2.1; update
  `release-artifacts.yml`, README release links and the DATA_LIMITATIONS header; refresh
  `MANIFEST.sha256`.
- [x] Rename the changelog's Unreleased section to v1.2.1 and write `docs/RELEASE_NOTES_v1.2.1.md`.
- [x] Push the release commit and the `v1.2.1` tag in one atomic push. The P18/P18b and P12-G5
  release-boundary checks require the workspace version to be the newest tag, so the release
  commit's `controls` run must already see the tag; the publish workflows wait for that run.
  (Tag commit `f30d5cd`; `controls` run 35863111572 green; release artifacts run 35863111642.)
- [x] Approve the `crates.io` and `pypi` environment gates; confirm all three crates and the
  wheel/sdist set land at 1.2.1. (Gates approved via the API after each verify/validation job;
  crates run 35863112008, PyPI run 35863112087; PyPI digests match the validated set.)
- [x] Attach packaged platform archives and `SHA256SUMS` to the `v1.2.1` GitHub release; paste the
  release notes. (https://github.com/AvilaLabs/ACTINV/releases/tag/v1.2.1, release 394678773)
- [x] Record the release URL, tag commit, release ID, asset identities and smoke results in
  `results/release_v1.2.1.json`. (Production smoke: `pip install actinv==1.2.1` and
  `cargo install --locked actinv-cli --version 1.2.1` both give `actinv --version` -> 1.2.1.)

## v1.3.0/v1.3.1 (feature release + same-day packaging patch)

The v1.3.0 tag's crates publish failed at `cargo publish` verification: `actinv-core` bundled the
IAEA clearance table with `include_str!("../../../data/clearance_iaea_2004.json")`, a path outside
the package root, so the crate tarball — and the PyPI sdist, which vendors the workspace `crates/`
tree without the repo-level `data/` — could not build it. The fix ships the table inside the crate
(`crates/actinv-core/data/`, drift-guarded in `controls/g2_p54_exactness.py`) and landed as v1.3.1;
PyPI filenames are immutable so v1.3.0's defective sdist remains published but superseded.

Two pre-tag gates would have caught this; adopt them for every release:

- [ ] Run `cargo publish -p actinv-data --dry-run`, `-p actinv-core --dry-run`, and
  `-p actinv-cli --dry-run` on the release commit (each resolves inter-crate deps from the
  registry index, so run them after any prior registry publish of those versions, or accept the
  missing-version warning for not-yet-published deps) — the verify build fails on any
  `include_str!`/`include_bytes!` path that escapes the package root.
- [ ] Build the PyPI sdist (`maturin sdist` or the publish workflow's sdist job) and check every
  `include_str!`/`include_bytes!` in `crates/` resolves inside the packaged tree.

Sequencing notes learned this cycle:

- `controls` does not run on tag pushes, and its `release_boundary` check fails the pre-tag
  workspace bump by design (`version` ahead of the newest published tag). After pushing the tag,
  re-run controls on the same commit (`workflow_dispatch` on master, or `gh run rerun`) — the
  publish workflows' `ci-green` gate reads the **newest completed** controls run for the release
  commit and aborts if that run is the pre-tag failure.
- Each `crates.io` environment job (`Publish actinv-data`, `-core`, `-cli`) and the `pypi` job are
  separate environment approvals; approve each pending deployment as it queues.

v1.3.0/v1.3.1 execution: bumps `af046ce`+`2979f35` (v1.3.0; `2979f35` also reseated the P10-G6
canonical hash for the `decay_overrides_applied` schema key) and `6e86cad` (v1.3.1 packaging fix);
tags `v1.3.0`/`v1.3.1`; controls runs 36428795353+36429728374 (v1.3.0) and 36451256724 (v1.3.1)
green; PyPI/crates verified at both versions (records in `results/release_v1.3.0.json` and
`results/release_v1.3.1.json`).

## v1.4.0 (feature release)

- [x] Bump the workspace, `python`, `pyproject.toml` and inter-crate versions to 1.4.0; update
  `release-artifacts.yml`, README release links, the DATA_LIMITATIONS header and the handbook's
  version references; refresh `MANIFEST.sha256`.
- [x] Rename the changelog's Unreleased section to v1.4.0 and write `docs/RELEASE_NOTES_v1.4.0.md`.
- [x] Pre-tag gates: `cargo publish --dry-run` for `actinv-data`, `actinv-core` and `actinv-cli`,
  and every `include_str!`/`include_bytes!` in `crates/` resolving inside its package; local CI
  replay green.
- [x] Push the release commit and the `v1.4.0` tag in one atomic push; re-run `controls` on the
  release commit after the tag exists.
- [x] Approve the `crates.io` and `pypi` environment gates; confirm all three crates and the
  wheel/sdist set land at 1.4.0.
- [x] Attach packaged platform archives and `SHA256SUMS` to the `v1.4.0` GitHub release; paste the
  release notes.
- [x] Production smoke from PyPI and crates.io; record the release in `results/release_v1.4.0.json`.
- [x] Handbook and website updated with the release (standing rule from 2026-10-01): handbook
  version references in the release commit; the `actinv-web` bundle CI built from the release
  commit deployed with `npx wrangler@4 deploy --config wrangler.jsonc`; `/`, `/download/` and
  `/docs/` checked live.

v1.4.0 execution: release commit and tag `85247cf` pushed atomically, so `controls` (run
36954992700) saw the tag and passed without a re-run. The local replay's single `p16` failure was
the fresh worktree's missing `target/tmp`, reproduced, then passed on rerun. Crates run 36954993114
and PyPI run 36954993352 green; environment gates approved via the API on the maintainer's
approval. Record: `results/release_v1.4.0.json`. From this release on, every release also deploys
the website and handbook (add the step above to each future section).

## desktop-v0.3.0-preview.1 (GUI-only release)

The solver, CLI and Python stay at 1.4.0; the workspace version does not move and no `v…` tag is
created.

- [ ] Version 0.3.0-preview.1 in `crates/actinv-gui/Cargo.toml`, `packaging/desktop.json`,
  `packaging/windows.rc` and the README/handbook/install references; `docs/releases/desktop-v0.3.0-preview.1.md`
  written; CHANGELOG Unreleased entries; refresh `MANIFEST.sha256`.
- [ ] Merge the sign-in PR with `web.yml`, `ci.yml` and `desktop.yml` green.
- [ ] Web: build from the merge commit as `web.yml` does (`trunk build … --release --locked`,
  `mdbook build`, `scripts/check_docs.py dist/docs`), then deploy the `actinv-web` bundle with
  `npx wrangler@4 deploy --config wrangler.jsonc`; check `/`, `/download/` and `/docs/` live,
  including the Sign in button and the new handbook page.
- [ ] Desktop: run `desktop.yml` on the merge commit; attach its Windows, macOS and Linux
  artifacts and checksums to a `desktop-v0.3.0-preview.1` GitHub release (pre-release first) and
  paste `docs/releases/desktop-v0.3.0-preview.1.md`; record the per-platform check results.
- [ ] Manual check of the desktop device sign-in against the live account service, and of sign
  out removing `~/.config/avila/credentials.json`.
- [ ] Handbook and website updated with the release: handbook version references (this PR),
  actinv.avilalabs.org download page, and avilalabs.org/actinv (+ `/docs`) through the
  website-live PR.
