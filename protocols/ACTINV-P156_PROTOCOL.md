# P156 — dpa without setup: a shipped damage table (data catalog 1.2.0)

Registered 2026-10-27, before any source change for the phase. First 1.7 release phase. Builds on P154-PASS
(`results/p154_verdict.json`), whose kinematic method this phase ships as data.

## Change under test

Today a user who wants dpa runs `actinv build-damage` over the evaluations, then lists a displacement energy for every
element in the spec. P156 ships the table and the energies, so a neutron problem gets dpa, exact NRT dpa, arc-dpa and
He/dpa by naming one catalog entry.

1. **Standard displacement energies.** `build-damage --method kinematic` defaults each element's `E_d` to the
   per-element table that NJOY2016 HEATR applies when no `ed` is given (heatr.f90 at tag 2016.80, commit
   `ab27c6414a0f25b173f2b637e6dc7e428fdaff00`, lines 328–363: Be 31, C 31, Mg 25, Al 27, Si 25, Ca 40, Ti–Cu 40, Zr 40,
   Nb 40, Mo 60, Ag 60, Ta 90, W 90, Au 30, Pb 25 eV; 25 eV otherwise), so an NRT value agrees with an NJOY-processed
   MT=444 library at the same energies. `--displacement-energy EL=eV` still overrides it per element. The flat 25 eV
   default is removed.
2. **arc-dpa at the fit's own energy.** The arc-dpa displacement cross section uses the reference `E_d` of the Nordlund
   et al. (2018) Table 1 fit it takes `b` and `c` from (Fe 40, Cu 33, Ni 39, Pd 41, Pt 42, W 70 eV), whatever `E_d` the
   NRT quantities use; the constants are only defined at that energy. A table built with `E_d` equal to the reference
   is unchanged.
3. **Table energies as the run default.** With an `actinv-damage-table-2` file, an element the spec's
   `damage.displacement_energy_eV` omits takes the energy the table records, and the step ledger names the source
   (`spec` or `table`). An explicit value must still equal the table's. Table-1 files are unchanged (every covered
   element must be given).
4. **Catalog 1.2.0.** A new artifact role `damage-table`, optional in a bundle. One artifact,
   `tendl-2025-neutron-damage`, on the 709-group structure, referenced by all four neutron bundles (full and patched,
   with and without covariance). Its targets are every TENDL-2025 neutron evaluation whose nuclide the catalog's decay
   data (ENDF/B-VIII.0, JEFF-3.3 where ENDF/B-VIII.0 has no record) lists as stable or with a half-life of at least one
   Julian year (3.15576e7 s). The atoms of a material are these nuclides; shorter-lived products are counted by the
   run's existing `covered_atom_fraction` and `uncovered_targets`, not given damage. (Measured on 2026-10-27: the
   kinematic build takes about 14 s per MB of evaluation on two cores, so the roughly 390 selected files, about 1.7 GB,
   take about 7 h and all 2,850 about 55 h.) The table is built from the unmodified evaluation files; the patched
   library's ordinate repairs (`docs/DATA_LIMITATIONS.md`) are not applied to it. Every v1.1.0 artifact keeps its id, path,
   bytes, SHA-256 and source URL; the new table is hosted on release `data-v1.2.0` with a v1.2.0 data notice
   (TENDL-2025, CC-BY-4.0, transformation stated). `data fetch` reuses a file already present under an older version
   directory when its SHA-256 matches, copying and re-verifying it instead of downloading.
5. **No-setup entry points.** `actinv new` writes a `damage` section that names the catalog table and requests the
   `damage` output. `import-fispact` uses the catalog table when a deck asks for damage
   and `--damage` is not given; `ALLDISPEN` and `ATDISPEN` are compared with the table's energies as for `--damage`.
6. **Build command (frozen).** The shipped table is
   `actinv build-damage DIR OUT --method kinematic --projectile neutron --groups fispact-709 --continuum-recoil evaluated`
   with the defaults of items 1–2 and no other option. `--continuum-recoil evaluated` takes the recoil spectrum from the
   evaluation where an MF=6 continuum gives one and falls back to kinematics per channel otherwise. Capture uses the
   default `evaluated` treatment (summed cascade recoil, partitioned once). `DIR` holds regular-file copies of the
   item-4 selection from `~/nuclear-data/tendl-2025-patched/files/n`, the distributed TENDL-2025 neutron files
   (Fe-56 SHA-256 `f33f867a…156d2e1e`); each file is hashed into the table.

Beyond FISPACT-II and NJOY-processed libraries: no processing step for the user; exact NRT and arc-dpa from the recoil
spectrum; uncovered damage bounded, not absent; capture recoil without the HEATR `tabsq6` weighting defect reported
upstream as njoy/NJOY2016#421.

## Gates

Checker: `controls/check_p156.py` (with `--self-check`), written from this protocol without reading the
implementation. Reference: master `a33c25b` (P155-PASS). Independent reference for G4: the NJOY2016.79 build used by
P154, with P154 Amendments A–C's reference definitions.

- **G0.** This protocol's SHA-256 is in `protocols/protocol_hash.txt`.
- **G1.** fmt, clippy `-D warnings`, workspace tests (`cargo check --tests` included).
- **G2, existing behaviour.** `examples/damage_demo.json` and `examples/fns_fe_5min.json` byte-identical to the
  reference apart from `ms`; `build-damage` without `--method` byte-identical on the P23 corpus; with P154's recorded
  build arguments, the four P154 targets' tables are byte-identical to the reference binary's (their `E_d` equal the
  arc reference, so item 2 does not change them).
- **G3, energies.** A kinematic build with no `--displacement-energy` records exactly the item-1 table for each
  element present and 25 eV otherwise. For W-184 built at `W=90` and at `W=70`, `displacement_arc_barn` is identical
  and `displacement_nrt_barn` differs; for Fe-56 at `Fe=40` the arc column equals the reference binary's.
- **G4, wider accuracy.** On TENDL-2025 V-51, Ti-48, Cu-63, Ta-181, Mo-98, Nb-93, Mn-55, Co-59, Si-28, Al-27, Be-9
  and Li-7, P154's G3 (elastic, 2 %), G4 (inelastic and disappearance, 5 %, with Amendment C's precondition set and
  references) and G5 (NJOY MT=444 within computed plus uncovered bound) hold against NJOY at the same `E_d`, with the
  NJOY-matching capture and continuum modes chosen per target as in P154. A target whose evaluation lacks a data form
  P154 covers is reported with its uncovered share, not dropped.
- **G5, shipped table.** The checker derives the item-4 selection itself from the pinned decay files and the corpus;
  the table is built from exactly that set with the item-6 command and records every file's SHA-256. Every selected
  target is a row or is listed with its reason; rows are finite and nonnegative and `damage_energy_barn_eV` equals the
  sum of its partials within 1e-12 relative. A rebuild of a seeded random 16-file subset reproduces the shipped rows
  byte for byte. Build time, peak memory and file size are reported.
- **G6, no setup.** `actinv new` output runs and its `damage` block equals that of the same spec with the table path
  and every energy written out, byte for byte apart from the energy-source field. In an FNS Fe run on the shipped
  table, `dpa`, `dpa_nrt` and `dpa_arc` are reproduced independently from the table rows within 1e-12 relative, and
  `covered_atom_fraction` is reported for every step. A
  spec giving an energy different from the table's is refused and writes nothing. An FNS deck with standard
  `ALLDISPEN` imports with the displacement keywords mapped and no `--damage`.
- **G7, catalog.** Catalog 1.2.0 validates; every v1.1.0 artifact is byte-identical in it; the staged assets match
  `SHA256SUMS` and `SIZES`. A fetch into a directory holding the v1.1.0 files downloads only the table. After the
  owner publishes `data-v1.2.0`, a clean `data fetch`, `data verify` and the G6 FNS run succeed from the published
  release.
- **G8.** CI green on the pushed commit; committed baselines unchanged except where this phase records them.

## Verdict

P156-PASS if G1–G8 hold. Development runs are disclosed in `results/p156/`. A failing gate is preserved under
`results/failures/p156/`; one repair round, registered as an amendment first.

## Not claimed

The tables inherit TENDL-2025's evaluation defects (`docs/DATA_LIMITATIONS.md`) and P154's limits: Lindhard–Robinson
partition, no cascade simulation or temperature effects, arc-dpa only where published constants exist, uncovered
channels as upper bounds. Charged-particle damage tables are not built. Nuclides with half-lives under a year get no
damage; their atom fraction is reported instead. The standard energies are a convention, not a
material-specific measurement; a user with better values rebuilds with `--displacement-energy`.
