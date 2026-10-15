# P123 — Radiological completeness: point-source dose, bremsstrahlung, Steiner-22, A2 index, bundled opt-in tables

Registered 2026-10-15, before any P123 result is produced, under the owner-approved FISPACT-II parity plan
(2026-10-15; the owner deferred the bundled-table decision to the coordinator: bundle, opt-in, sources recorded,
no default jurisdiction). Base commit: `2f3daa4`. Tracker rows: DOSE 2, BREMSSTRAHLUNG, 22-group γ, ATWO, HAZARDS,
CLEAR tables.

## Change under test

All parts are opt-in. Default results are unchanged.

**Part A** (photon side):
1. The `photon.group_structure: "steiner-22"` structure, from FISPACT-II manual Table 6.
2. `photon.point_source {distance_m ≥ 0.3, air_attenuation, air_density_g_cm3}`: the dose rate in air from the
   step's photon source as a point source of `material.mass_g`, per manual Eq. 63 (no build-up). An optional
   `air_mass_attenuation` curve is added to the photon response builder.
3. `photon.bremsstrahlung {nuclides}`: a separately reported bremsstrahlung component from the named β⁻ emitters,
   per manual Eqs. 65–68, with Z_eff as the atom-fraction mean Z. The 0 eV-edge group is left empty and its energy
   reported.
4. Radiological kind `a2`: Σ A_i/A2_i from a user-selected table.

**Part B** (tables):
5. Bundled opt-in tables, each carrying its source block, selected only by explicit symbolic reference:
   - IAEA SSR-6 Rev.1 A1/A2 and exempt values, from the public-domain 49 CFR 173.435/173.436 eCFR XML
   - clearance and exemption levels from Directive 2013/59/Euratom Annex VII
   The committed conversion scripts reproduce them.
6. A local fetch-and-parse script for ICRP 119 (ICRP 72 adult public ingestion/inhalation coefficients). The derived
   table is never committed.

## Gates

Checker: `controls/check_p123.py`, run by the coordinator under the 6 GiB cgroup. Reference: release build of
`2f3daa4`. Candidate: release build of the implementation commit.

- **G0.** This protocol's SHA-256 is in `protocols/protocol_hash.txt` in a commit before any P123 evidence.
- **G1.** fmt, clippy with `-D warnings`, and the workspace tests, including the new photon, spec, radiological and
  table-resolution unit tests.
- **G2, unchanged by default.** `examples/fns_fe_5min.json` and the existing photon/dose example specs are
  byte-identical apart from `ms`. The photon response builder's default output is byte-identical to the
  committed/installed response.
- **G3, point source, independent recomputation.** For the FNS Fe spec at its cooling steps, with
  `point_source.distance_m` 1.0 and 3.0, the checker recomputes the dose from the emitted per-nuclide photon groups
  and the response file (air μen/ρ, μ/ρ, log–log interpolation, Eq. 63) within 1e-9 relative. It also checks the
  ratio of the 1 m to the 3 m dose against 9·exp(μ·2 m) per group.
- **G4, bremsstrahlung, independent recomputation.** For a Cl-36/K-42-bearing case (or FNS Fe with the β⁻ emitters
  present at its steps), the checker recomputes the Eq. 66/67 group energies from the decay file's mean β energy
  and Z_eff within 1e-9 relative. The decay-photon fields are identical with and without the option.
- **G5, tables.**
  - Re-running each committed converter on the pinned source files reproduces the committed tables byte for byte.
  - A sample of at least 25 rows per table matches values read independently from the source document text.
  - Report-only: agreement rates against FISPACT's `a2_2012` and `clear_2012`, with every mismatch listed.
  - The ICRP 119 script, run locally, reproduces at least 98 % of FISPACT `hazards_2012` ICRP-72 rows within 2 %,
    with every exception listed and explained.
- **G6, A2 index.** For the FNS Fe spec with the bundled A2 table selected, the A2 index equals Σ activity/A2
  recomputed by the checker within 1e-12 relative. Nuclides without an A2 value are listed and not counted.
- **G7.** CI green on the pushed commit, with the committed run-output baselines unchanged.

A gate that fails is recorded as P123-FAIL with its evidence; repairs need an amendment registered first.

## Not claimed

The point-source dose has no build-up and the bremsstrahlung is FISPACT's thick-target approximation. Neither is a
shielding calculation. The bundled tables are not an endorsement of a jurisdiction, and ICRP coefficients are not
shipped.
