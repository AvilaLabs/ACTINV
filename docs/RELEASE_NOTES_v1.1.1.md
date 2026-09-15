# ACTINV v1.1.1 — release notes

ACTINV 1.1.1 is a data-catalog patch release with one solver hardening fix. Schemas, solver order, public
interfaces and physics are unchanged from 1.1.0. The embedded data catalog advances to v1.1.0 so that
`actinv data fetch` installs the `tendl-2025-patched` remediation derivative qualified in P25c (`data-v1.1.0`
release).

Upgrade the Python package with:

```bash
python -m pip install --upgrade actinv
```

Rust CLI users can install this exact patch with:

```bash
cargo install --locked --force actinv-cli --version 1.1.1
```

## What changes

- The embedded catalog becomes `actinv-data-catalog-v1.1.0.json`; installed data lives under
  `actinv-data/v1.1.0/`.
- The new default bundle `tendl-2025-patched-neutron` installs the P25c-qualified patched 709-group neutron
  activation library — an Avila Labs remediation derivative of TENDL-2025 that zeroes exactly the 44 enumerated
  leaked leading ordinates across the 28 confirmed-signature files and is byte-identical to the official archive
  everywhere else. It is not an official TENDL release.
- `tendl-2025-patched-neutron-covariance` adds the MF=33 covariance sidecar rebuilt against the patched artifact;
  the sidecar index cryptographically pins the patched library's SHA-256.
- `actinv new` now emits `catalog:tendl-2025-patched-neutron-709g` references.
- All v1.0.0 artifact IDs resolve to exactly the same bytes: the proton, deuteron, alpha and decay assets are
  unchanged, and the superseded `tendl-2025-neutron` bundles remain fetchable for byte-compatible reproduction of
  earlier work.
- CRAM linear solves now apply bounded iterative refinement (`Lu::solve_refined`) with compensated residual
  evaluation; the run certificate's `numerical_floor` note no longer claims a total numerical-error bound. Results
  are unchanged on previously green cases; the fix removes a trace-inventory precision loss in shifted solves.

## Qualification boundary

The patched corpus passed the frozen P25c construction, coverage, surgical-integrity, leak-clearance and
retrospective nonregression gates — retrospective evidence of internal consistency, not blind validation. The
shipped patched artifact holds 1,679 targets / 87,075 rows: the full-corpus strict build ledgered 1,172
source-file exclusions across all defect classes. Five dosimetry-critical targets (Ni-58, Nb-93, Ag-109, In-113,
Au-197) still fail closed under non-signature defect classes, and 16 of the 17 previously defect-bearing IRDFF-II
targets are absent. ACTINV and the data remain research-grade; `docs/DATA_LIMITATIONS.md` and
`docs/QUALIFICATION.md` still apply.
