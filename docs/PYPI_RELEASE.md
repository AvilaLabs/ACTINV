# PyPI release procedure

ACTINV publishes Python distributions from GitHub Actions with PyPI Trusted Publishing. No long-lived PyPI token is
stored in GitHub. The build jobs have read-only repository access; only the two isolated publishing jobs receive an
OIDC identity, and each is protected by its own GitHub environment.

## One-time account setup

Create a `testpypi` environment under the ACTINV repository's **Settings → Environments** page and require maintainer
approval. Repeat for an environment named `pypi`.

Sign in to TestPyPI's **Account settings → Publishing** page and add a pending GitHub publisher:

```text
PyPI project name: actinv
Owner: AvilaLabs
Repository: ACTINV
Workflow: publish-pypi.yml
Environment: testpypi
```

TestPyPI uses a separate account from PyPI. On PyPI's **Account settings → Publishing** page, add the corresponding
pending publisher with `Environment: pypi`; the other four values remain identical. A pending publisher does not
reserve the project name, so perform the first publication promptly after configuration.

## Candidate and production sequence

1. Push the intended release commit and require the normal `controls` workflow to pass.
2. Run **publish Python package** manually on that commit. Approve the `testpypi` environment only after all five wheel
   builds, the source distribution, native wheel smokes, and complete-set validation are green.
3. In a clean environment, install and check the TestPyPI candidate:

   ```bash
   python -m venv actinv-test
   actinv-test/bin/python -m pip install --index-url https://test.pypi.org/simple/ --no-deps actinv==1.0.0
   actinv-test/bin/actinv --version
   actinv-test/bin/actinv data list
   actinv-test/bin/python -c "import actinv; print(actinv.__version__)"
   ```

   On Windows, use `actinv-test\\Scripts\\` in place of `actinv-test/bin/`.
4. Create and push the signed `v1.0.0` tag on that exact green commit. The tag starts a fresh build of the same matrix;
   the workflow refuses a tag that does not match the package version.
5. Review the assembled artifact identities and approve the `pypi` environment. The official PyPA action publishes
   the files and their signed attestations.
6. Install `actinv==1.0.0` from ordinary PyPI in clean Linux, macOS, and Windows environments. Record the PyPI URL,
   workflow run, tag commit, distribution SHA-256 values, and smoke results in the release record.

PyPI distribution filenames are immutable. If any uploaded v1.0.0 file is wrong, do not try to replace it; correct the
source and publish a new version. Nuclear-data libraries remain outside the wheel and source distribution and continue
to use the separately versioned, SHA-256-verified data release.
