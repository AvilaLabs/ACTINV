# P129 — Geometry self-shielding and per-group dilution overrides (FISPACT-II SSFGEOMETRY / SSFDILUTION parity)

Registered 2026-10-15, before any P129 result is produced, under the owner-approved FISPACT-II parity plan
(2026-10-15). Base commit: `2f3daa4`. Tracker rows: geometry self-shielding, SSFDILUTION.

## Change under test

`self_shielding` gains two opt-in modes. Existing specs (a `table` with no new keys) serialize, fingerprint and
solve exactly as before.

1. `dilution_overrides`: per-nuclide, per-group σ0 (barns, 0-based ascending library groups) replacing the computed
   background dilution in the existing Bondarenko path.
2. `geometry`: the universal sigmoid curve of Martinho, Gonçalves and Salgado, as documented in the FISPACT-II
   manual (Eqs. 44–50, Table 25).
   - G(z) = (A1−A2)/(1+(z/z0)^p)+A2, with A1=1.000, A2=0.060, z0=2.70, p=0.82.
   - z = N·σ_peak·y·√(Γγ/Γ).
   - Effective lengths: foil 1.5t, wire 2r, sphere r, cylinder 1.65rh/(r+h).
   - σ_peak is the single-level Breit–Wigner peak 4π/k²·gΓn/Γ.
   - Each group's factor is the (Γγ/E²)·gΓn/Γ-weighted mean of G over resonances peaking in the group.

   The resonances come from hash-pinned ENDF-6 MF=2 files: SLBW, MLBW and Reich–Moore ranges are used, and LRF=7 is
   rejected. `table` and `geometry` are exclusive.

## Gates

Checker: `controls/check_p129.py`, run by the coordinator under the 6 GiB cgroup. Reference: release build of
`2f3daa4`. Candidate: release build of the implementation commit.

- **G0.** This protocol's SHA-256 is in `protocols/protocol_hash.txt` in a commit before any P129 evidence.
- **G1.** fmt, clippy with `-D warnings`, and the workspace tests, including the 13 new unit tests in
  `shielding.rs` and `spec.rs`: sigmoid limits, effective lengths, the weighted group mean, override bypass, and
  validation.
- **G2, unchanged by default.** `examples/shielding_demo.json` and `examples/fns_fe_5min.json` give byte-identical
  results (apart from `ms`) under reference and candidate.
- **G3, independent recomputation.** For a tungsten foil (W-186 resonance file from the installed TENDL-2025 or
  ENDF/B-VIII.0 evaluation, 0.01 cm and 0.1 cm), the checker:
  - independently parses MF=2 and computes per-resonance z and G
  - computes the weighted group factors on the run's group structure
  - compares them with the ledger's applied factors within 1e-9 relative

  It also checks that every factor lies in [A2, 1] and that the factors fall monotonically with thickness.
- **G4, override.** A table-mode run with a `dilution_overrides` entry equal to the computed σ0 for that
  nuclide/group is byte-identical to the run without it, apart from `ms`, the echoed spec and its fingerprint.
  A tenfold σ0 moves the factor toward one.
- **G5, report-only comparison with FISPACT-II.** Where the manual or a FISPACT-II output provides a tabulated
  geometry factor for a foil case, it is tabulated beside ACTINV's. There is no pass threshold.
- **G6.** CI green on the pushed commit, with the committed run-output baselines unchanged.

A gate that fails is recorded as P129-FAIL with its evidence; repairs need an amendment registered first.

## Not claimed

Overlapping resonances and mixture background are neglected, as in the published method. There is no damage
shielding and no measurement validation.
