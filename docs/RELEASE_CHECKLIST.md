# v1.0 public-release checklist

The P12 checker closes the technical repository release. The maintainer performs the public acts below separately;
none is automated by the control suite.

P12 uses two commits to avoid a circular claim. The session records the immutable release-payload commit and its
successful workflow run; the following closure commit adds that attestation, final verdict, and manifest. Run and
confirm the workflow on the closure commit too. The public software tag belongs on the final green release commit,
including subsequent packaging-only release plumbing, not on the earlier technical payload commit.

## Before publishing

- [ ] Confirm `controls/check_p12.py` reports `P12-CONDITIONAL` or `P12-PASS` on a clean clone.
- [ ] Confirm the pushed commit's required GitHub Actions run is green.
- [ ] Run the `release artifacts` workflow for that exact commit and download every artifact.
- [ ] Verify artifact SHA-256 values and `actinv --version` / `actinv.__version__` are `1.0.0`.
- [ ] Install a wheel into a new environment and run the documented smoke calculation.
- [ ] Review [v1.0 release notes](RELEASE_NOTES_v1.0.md), [qualification boundary](QUALIFICATION.md), licences, and
  third-party-data notices.
- [ ] Confirm the `actinv` names and maintainer accounts on PyPI/crates.io and configure trusted publishing or scoped
  release credentials.
- [ ] Follow the account and environment setup in [PyPI release procedure](PYPI_RELEASE.md), publish the exact candidate
  to TestPyPI, and smoke-test both `import actinv` and the installed `actinv` command.

## Public acts

- [ ] Create the signed `v1.0.0` tag at the final green software-release commit and push it.
- [ ] Publish the Python wheels and source distribution to PyPI.
- [ ] Publish Rust crates in dependency order: `actinv-data`, `actinv-core`, then `actinv-cli`.
- [ ] Create the GitHub Release from the signed tag, attach standalone binaries and `SHA256SUMS`, and paste the v1.0
  release notes.
- [ ] Install with `pip install actinv` and `cargo install actinv-cli` from the public registries in clean environments.
- [ ] Record public URLs, upload identities, artifact hashes, and smoke-test results in an append-only release record.

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
