# ACTINV-P100 — Photonuclear activation with MF=3 threshold extension (successor to P98)

Date: 2026-10-01. Status: **frozen before the changed code is written.**

## Why this protocol exists, and what is already known

P98 (`a72c9d31…`, ledger Entry 75) passed G0–G4 and G6. It failed G5, the FISPACT-II
`tal2017-g/gxs-162` cross-code comparison: 162 of 323 values were within 2e-3 (95 % required), and
the maximum difference was 0.179. A post-verdict diagnosis found two causes. **FISPACT-II values
have therefore already been read.** This protocol was designed knowing them. The disclosure is
made here, and the pass bands are carried over unchanged.

1. **FISPACT-II processed MT5 residual production follows a different rule.** Its values equal
   rule R below to 1e-6 in every group checked. In the 30–35 MeV group, R keeps only 23–33 % of the
   exact integral of σ(E)·y(E). The candidate integrates the product exactly, as ENDF-6 defines.
   - No candidate change can or should reproduce R.
   - P100 therefore removes MT5 from the direct comparison. It replaces that comparison with two
     exact checks: FISPACT-II against R (G5b), and the candidate against an independent exact
     integration (G4 on the TENDL-2017 inputs).
2. **TENDL-2017 MF=3 tables start above threshold** (Al-27 MT4, Nb-93 MT16, Ta-181 and W-186 MT4
   and MT17, Pb-208 MT4, and others). In the gap the MF=3 total is zero, while the MF=10 states
   carry the physical ramp from threshold. The candidate scales those states to the zero total, so
   the threshold group's production is lost. FISPACT-II keeps the ramp. This is a candidate-side
   loss, and the change below fixes it.

## Change under test

Branch `p100-gamma`: P98's change (branch `p94-gamma`), with master merged, plus one ingest rule.

**MF=3 threshold extension.** The rule applies to gamma evaluations under a normalization profile
(`--profile` not `none`), separately for each MT. It applies when all of these hold:
- the MT has an MF=3 table, whose first energy is E3;
- the MT has MF=10 sections with ZAP ≥ 0;
- at least one of those sections has a positive value at a tabulated energy below E3, or a
  positive left limit at E3.

When it applies, the MF=3 table is extended below E3 by the sum of the MT's MF=10 sections with
ZAP ≥ 0:
- **Points.** One point is added at every energy below E3 that any of those sections tabulates,
  and its value is the sections' summed value there.
- **Joining point.** A point at E3 carries the summed left limit. It is followed by the original
  first MF=3 point, so it becomes a doubled point when the two values differ.
- **Interpolation.** The added region is lin-lin (INT=2), and the original regions are kept.
- **Linearity condition.** The rule applies only when every one of those sections is lin-lin
  (INT=2) on every segment below E3, so that the sum is exact. Otherwise the MT is left unchanged,
  and a ledger line names the reason.
- **Ledger.** Each application writes one line naming the MT, E3, the first added energy, and the
  number of added points.

Runtime totals, state-sum reconciliation and every audit then run on the extended table as
before. Under `--profile none`, for non-gamma projectiles, and wherever the conditions fail,
behaviour is unchanged.

## Gates

Reference: master release `actinv` built from `410c485`, whose SHA-256 the checker records;
archived as `target/p100/ref_actinv`. Checker: `controls/check_p94.py` with
`ACTINV_GAMMA_PROTOCOL=P100`, selecting `target/p100/` and `results/p100_verdict.json`. It runs
under the 6 GB cgroup cap. Nothing carries over from P94 or P98.

- **G0:** the protocol hash is registered before the change is written.
- **G1:** P98's G1 in full. It adds unit tests for:
  - the extension applied under a profile: the table shape, the doubled joining point, and the
    ledger line;
  - no change under `none`;
  - no change for a neutron evaluation under a profile;
  - no change, plus the ledger reason, when a section below E3 is not lin-lin;
  - no change when every state is zero below E3.
- **G2:** P94's G2(a)–(c) in full, against this protocol's reference. That covers the neutron and
  proton specs, the mesh profiles, the proton library, and the P32 import.
- **G3:** P94's G3 in full. In addition, the TENDL-2025 gamma `.npz` must equal P98's candidate
  build (`d4590b8e…`, unchanged by P99 per the P98 post-merge record) row for row, except rows
  whose (target, MT) carries an extension ledger line. The count of changed (target, MT) pairs is
  reported.
- **G4:** P98's G4 in full on the 4 TENDL-2025 nuclides. It is also run on the 8 TENDL-2017
  inputs, built exactly as in G5.
  - The independent code re-derives the extension rule from this protocol's text alone, and the
    profile's per-group normalization from its documented specification.
  - The same 1e-9 relative tolerance applies, along with exact zeros at or above 200 MeV and
    two-way row existence.
- **G5 (FISPACT-II / TENDL-2017).** Inputs, build options, archive verification and the P98
  control's reconstruction rules (`controls/p98_g5_fispact.py`, `2c490ed6…`) carry over. That
  means the gamma single-neutron channel, free-neutron exclusion, MT5 raw-LFS recovery by rank, and
  the index mapping. All 8 must build.
  - **G5a, cross-code without MT5.**
    - One-group values per (nuclide, residual key, spectrum) use every contribution except MT5,
      on both sides. The spectra are P94's three.
    - Every residual carrying at least 1e-3 of the nuclide's summed non-MT5 residual production,
      on either side, is compared.
    - Every one of the 8 nuclides must contribute at least one compared value under each of
      `gdr_flat_8_30_MeV` and `brems_20_MeV`.
    - Pass: at least **95 %** within **2e-3** relative, and all within **2e-2**. Unmatched
      residuals are reported.
  - **G5b, the FISPACT-II MT5 rule.** Every MF=10 MT5 section with ZAP > 1 in the 8 processed
    records must equal rule R. The comparison runs over every group below 200 MeV where either
    value is at least 1e-12 b, within **1e-5** relative; the processed files carry 7 significant
    digits.
    - Each section's raw state is recovered by rank, and its yield is paired as in `production_terms`.
    - **Rule R.** On the union of the raw MF=3 MT5 and yield energy grids, form the points
      σ(E)·y(E). Each table's value is taken as right-continuous, except at the lowest grid energy,
      where each table's first-listed value is used. Interpolate those points lin-lin in E. The
      group value is the lethargy average over the group.
    - Doubled points anywhere other than the first grid energy are reported.
  - **Reported, not gated:**
    - P98's full all-MT comparison;
    - group rows against 2.5e-3;
    - every extension and `state_sum_normalized` ledger line in the G5 build.
- **G6:** CI replay; every step exits 0.

Merge only if G0–G6 all pass. A FAIL stands; thresholds are not lowered afterwards.
