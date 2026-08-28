# v1.0 public-release checklist

The P12 checker closes the technical repository release. The maintainer performs the public acts below separately;
none is automated by the control suite.

P12 uses two commits to avoid a circular claim. The session records the immutable release-payload commit and its
successful workflow run; the following closure commit adds that attestation, final verdict, and manifest. Run and
confirm the workflow on the closure commit too. A later public tag belongs on that green closure commit, not on the
earlier payload commit.

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

## Public acts

- [ ] Create the signed `v1.0.0` tag at the final green P12 closure commit and push it.
- [ ] Publish the Python wheels and source distribution to PyPI.
- [ ] Publish Rust crates in dependency order: `actinv-data`, `actinv-core`, then `actinv-cli`.
- [ ] Create the GitHub Release from the signed tag, attach standalone binaries and `SHA256SUMS`, and paste the v1.0
  release notes.
- [ ] Install with `pip install actinv` and `cargo install actinv-cli` from the public registries in clean environments.
- [ ] Record public URLs, upload identities, artifact hashes, and smoke-test results in an append-only release record.

Publishing must not include raw nuclear-data inputs, generated bulk libraries, credentials, local paths, or caches.
