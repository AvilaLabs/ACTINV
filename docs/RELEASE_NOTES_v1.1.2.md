# ACTINV v1.1.2 — release notes

ACTINV 1.1.2 is a release-tooling patch: the solver, schemas, data catalog, and public interfaces are
identical to 1.1.1. It exists to restore channel parity — the v1.1.1 tag carried a stale wheel-smoke
constant that rejected the correct embedded data catalog v1.1.0, which blocked that tag's PyPI publish.
GitHub, crates.io and PyPI now carry the same patch version.

Upgrade the Python package with:

```bash
python -m pip install --upgrade actinv
```

Rust CLI users can install this exact patch with:

```bash
cargo install --locked --force actinv-cli --version 1.1.2
```

## What changes

- `scripts/smoke_python_wheel.py` now expects the shipped embedded data catalog v1.1.0.
- The P18/P18b release boundary accepts the published v1.1.* tags while still rejecting any tag naming a
  version ahead of the workspace.
- The P22 post-bump digest check scopes to the 1.1.0 release window.
- The P10 neutron-output identity pin was re-seated to the deterministic value produced by the reviewed
  CRAM iterative-refinement solve.

## Qualification boundary

Unchanged from v1.1.1 — `docs/DATA_LIMITATIONS.md` and `docs/QUALIFICATION.md` apply as before.
