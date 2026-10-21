# ACTINV v1.6.1 — release notes

ACTINV 1.6.1 fixes one defect of 1.6.0. Everything else in the
[1.6.0 release notes](RELEASE_NOTES_v1.6.0.md) applies unchanged, including the rate-pruning change under "Results to
re-check" and the known limitations.

Upgrade the Python package with:

```bash
python -m pip install --upgrade actinv
```

Rust CLI users can install this exact version with:

```bash
cargo install --locked --force actinv-cli --version 1.6.1
```

## Fixed

A trace-mode run with pathway output, which the default outputs include, could stop with `index out of bounds` in
the pathway decomposition. Two conditions together triggered it:
- a reaction of a bulk nuclide leads to a product with no decay data, so its first product is the leakage state;
- that source's column holds a positive round-off value at a real nuclide.

The P149 scaling study found it on tungsten, 7 h, without pruning.

The leakage state is a sink and never a pathway target, so a source that feeds it carries no production chain. Such
sources are now left out of the attribution. The solve is unchanged. In a run that completed in 1.6.0, every such
column held nothing at a real nuclide, so the results are the same in 1.6.1.

## Desktop

Desktop `0.5.0-preview.1` uses solver 1.6.1; see the
[desktop 0.5.0-preview.1 release](releases/desktop-v0.5.0-preview.1.md).

The [changelog](../CHANGELOG.md) has the full list of changes.
