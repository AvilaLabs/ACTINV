# Submitting ACTINV to conda-forge

Recipe: `recipe.yaml` (rattler-build, schema v1; staged-recipes accepts it).
Submitted as conda-forge/staged-recipes#35456 (fork branch `Connoravila1:actinv`).
Source: PyPI sdist `actinv-1.7.2.tar.gz`, sha256 12846693b72f01619d88822cd8f47f020c61ab2161b5f64d1461059baaaae927
(taken from https://pypi.org/pypi/actinv/1.7.2/json). Re-verify: `curl -L <url> | sha256sum`.
Built and tested locally with rattler-build 0.80.0 (linux-64, Python 3.12, `variants.yaml` below standing in for
conda-forge's pinning): the Rust build, `import actinv`, `pip check` and `actinv --help` pass.

The PR first carried 1.7.1 with `0001-workspace-members-glob.patch`: the 1.7.1 sdist's top-level Cargo.toml listed
`crates/actinv-gui`, which the sdist does not ship, so `cargo-bundle-licenses` failed. 1.7.2 lists `crates/*` (and CI
checks the sdist's manifests), so the recipe moved to 1.7.2 without the patch.

## Before submitting
1. Maintainer: `Connoravila1` (gets write access to the feedstock).
2. Check the sdist actually contains LICENSE-MIT, LICENSE-APACHE (python/pyproject.toml lists them), the `crates/` path dependencies, and a Cargo.lock; cargo-bundle-licenses needs the manifest. Download and `tar tzf` it into an empty dir.
3. Compare against the current ruff-feedstock / polars-feedstock recipes in case conventions changed.
4. Local test: `rattler-build build --recipe recipe.yaml -c conda-forge -m variants.yaml`, with

       c_stdlib: [sysroot]
       c_stdlib_version: ["2.17"]
       c_compiler: [gcc]
       c_compiler_version: ["14"]
       python: ["3.12"]

   (staged-recipes' `build-locally.py` uses conda-forge's real pinning instead).

## Commands
    gh repo fork conda-forge/staged-recipes --clone --remote
    cd staged-recipes
    git checkout -b actinv
    mkdir recipes/actinv
    cp <actinv checkout>/packaging/conda-forge/recipe.yaml recipes/actinv/
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
- Answered 2026-11-02: the sdist builds (crates fetched from crates.io during the build), and `actinv --help` exits 0.
- Does the CLI binary exist standalone? No: it is the `actinv:_cli` entry point inside the Python package (CLI crate is linked into the extension). A separate `actinv-cli` conda package is not needed.
- Data files: does the package depend on separately downloaded nuclear data (actinv-data)? Confirm nothing at runtime is missing.
