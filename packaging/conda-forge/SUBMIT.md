# Submitting ACTINV 1.7.1 to conda-forge

Recipe: `recipe.yaml` (rattler-build, schema v1; staged-recipes accepts it).
Source: PyPI sdist `actinv-1.7.1.tar.gz`, sha256 c00454d16406b695359ac6a80bac8d4c88478b3f591d9ccde59f4d69e07c8997
(taken from https://pypi.org/pypi/actinv/1.7.1/json). Re-verify: `curl -L <url> | sha256sum`.
Not build-tested (no builds allowed when written).

## Before submitting
1. Replace `OWNER_GITHUB_HANDLE` in recipe.yaml (maintainer must have a GitHub account; they get write access to the feedstock).
2. Check the sdist actually contains LICENSE-MIT, LICENSE-APACHE (python/pyproject.toml lists them), the `crates/` path dependencies, and a Cargo.lock; cargo-bundle-licenses needs the manifest. Download and `tar tzf` it into an empty dir.
3. Compare against the current ruff-feedstock / polars-feedstock recipes in case conventions changed.
4. Optional local test: `rattler-build build --recipe recipe.yaml -c conda-forge` (or the staged-recipes `build-locally.py`).

## Commands
    gh repo fork conda-forge/staged-recipes --clone --remote
    cd staged-recipes
    git checkout -b actinv
    mkdir recipes/actinv
    cp /home/connoravila/Documents/actinv/target/next-wt/packaging/conda-forge/recipe.yaml recipes/actinv/recipe.yaml
    git add recipes/actinv && git commit -m "Add actinv recipe"
    git push -u origin actinv
    gh pr create --repo conda-forge/staged-recipes --title "Add actinv" --body-file pr_body.md

The PR template is a checklist; paste it into pr_body.md and tick: recipe in its own folder; license file included; sdist from PyPI; build number 0; tests present; `recipe.yaml` used; maintainers listed and are willing; no pre-existing "actinv" package on conda-forge; `noarch` not applicable (compiled).

## Typical reviewer requests
- Pin Python minimum: the abi3 (cp39) wheel means one build per platform; reviewers may ask for `python_min` / `abi3` handling. If asked, add `build.python.version_independent: true` plus `python-abi3` in host/run, and `PYO3_USE_ABI3_FORWARD_COMPATIBILITY`/`python_min` context per current docs.
- Third-party license bundling (`THIRDPARTY.yml`) — included.
- Cross-compilation (osx-arm64, linux-aarch64): maturin cross-python block included; may need `PYO3_CROSS_*` tweaks.
- Tests: `actinv --help` and `import actinv`. `actinv.openmc` is not imported (optional openmc); say so if asked.
- Possibly `cargo-auditable` wrapper per ruff recipe (optional).
- Large/long build: Rust workspace compile time on CI.

## Open questions
- Is the sdist complete enough to build offline-from-crates.io (it needs network for crates; conda-forge allows cargo fetch)? Verify it builds.
- Does `actinv --help` exit 0 with the entry point? Check before submission.
- Does the CLI binary exist standalone? No: it is the `actinv:_cli` entry point inside the Python package (CLI crate is linked into the extension). A separate `actinv-cli` conda package is not needed.
- Data files: does the package depend on separately downloaded nuclear data (actinv-data)? Confirm nothing at runtime is missing.
