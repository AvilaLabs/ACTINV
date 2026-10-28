# ACTINV data v1.2.0

This release adds one artifact, `tendl-2025-neutron-damage`, and a notice for it. Every v1.1.0 artifact keeps its
identifier, path, size, SHA-256 and source URL, so `catalog:<id>` references in existing problem files resolve to
exactly the same bytes.

```bash
actinv data list
actinv data fetch
actinv data verify
```

`tendl-2025-neutron-damage` is an `actinv-damage-table-2` file on the 709-group structure: damage energy and
displacement cross sections (NRT and, where published constants exist, arc-dpa) with the helium production needed for
He/dpa. The four neutron bundles (full and patched, with and without covariance) reference it, so a neutron problem
gets dpa by naming `catalog:tendl-2025-neutron-damage` in its `damage` section with no displacement energies;
`actinv new` writes that section, and `actinv import-fispact` uses it for decks that give `ALLDISPEN` or `ATDISPEN`.
The proton, deuteron and alpha bundles are unchanged.

**What it covers.** Every TENDL-2025 neutron evaluation whose nuclide the catalog's decay data (ENDF/B-VIII.0, JEFF-3.3
where ENDF/B-VIII.0 has no record) lists as stable or with a half-life of at least one Julian year. Shorter-lived
products are not given damage; a run counts them in `covered_atom_fraction` and `uncovered_targets`.

**How it was made.** `actinv build-damage DIR OUT --method kinematic --projectile neutron --groups fispact-709
--continuum-recoil evaluated --continue-on-error true`, from the unmodified TENDL-2025 neutron files (the patched
library's ordinate repairs are not applied), except Pb-208: the distributed file carries a literal `NaN`, so the
table uses the repaired working-corpus file the activation library is built from (SHA-256 `86788a14…`). A negative
0 K resonance reconstruction is set to zero, as NJOY RECONR does; this happens only for Fe-54's elastic between 3,491
and 3,517 eV (at most −1e-4 b), and the row records it as `zero_k_clipped`. Displacement energies are the NJOY2016 HEATR defaults when no energy is given; the table records them and
a run refuses a different value. Each evaluation file's SHA-256 is recorded in the table.

**Fetching.** A file already installed under an older version directory with the catalog's SHA-256 is copied into
`v1.2.0/` and re-verified instead of downloaded, so an existing v1.1.0 installation fetches only the table and the
notice.

**Limits.** The table inherits TENDL-2025's evaluation defects (`docs/DATA_LIMITATIONS.md`) and the kinematic method's
limits: Lindhard-Robinson partition, no cascade simulation or temperature effects, arc-dpa only where published
constants exist, uncovered channels as upper bounds. The displacement energies are a convention, not a
material-specific measurement; rebuild with `--displacement-energy` for better values.

The table is a transformation of CC-BY-4.0 TENDL-2025 evaluations and is distributed under the same licence; see
`ACTINV-DATA-NOTICE-v1.2.0.md` for creators, citation and the transformation. `SHA256SUMS` and `SIZES` cover every
attached payload and the catalog.
