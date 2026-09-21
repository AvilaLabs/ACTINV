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
- [ ] Confirm `controls` workflow green on the release commit.
- [ ] Tag `v1.2.0` at the release commit and push; tag workflows publish release artifacts,
  crates.io and PyPI.
- [ ] Approve the `crates.io` and `pypi` environment gates; confirm all three crates and the
  wheel/sdist set land at 1.2.0.
- [ ] Attach packaged platform archives and `SHA256SUMS` to the `v1.2.0` GitHub release; paste the
  release notes.
- [ ] Record the release URL, tag commit, release ID, asset identities and smoke results in
  `results/`.
- [ ] Decide separately whether the ledgered TENDL-2017 In-115 repair becomes a labeled
  remediation-derivative data release (`data-v1.2.0`); it is disclosed but NOT shipped in this
  software release.
