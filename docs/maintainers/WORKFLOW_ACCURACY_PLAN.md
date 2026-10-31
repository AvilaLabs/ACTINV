# Workflow accuracy plan: where ACTINV can be better, not equal

Owner direction, 2026-10-30. Research of user-reported gaps in OpenMC depletion/R2S, FISPACT-II (with MCR2S,
R2Smesh, pypact), ALARA, PyNE R2S, D1S, CINDER, ORIGEN and ACAB found that the field's open accuracy problems are in
the shutdown-dose *workflow*, not in the point inventory solve, where ACTINV and FISPACT-II already tie at the
nuclear-data and measurement-noise floor (see `ACCURACY_PROGRAM.md`). This plan covers the three workflow items. It
refines Stage 4 of the accuracy program; Stages 1–3 stand.

Every item below is registered as a protocol before any scored run. A gain counts only against a reference or
measurement that did not choose or tune it.

## 1. Dose uncertainty carried end to end

**Gap.** Most R2S implementations ignore the flux tally's statistical error (UNED, UW/ORNL and KIT work, 2023–2026),
and no production tool found carries nuclear-data uncertainty through to the shutdown dose rate.

**What ACTINV has.** Per-cell activity and source bands from MF=33 cross-section covariance, half-life and yield
channels, and the 1.4.0 `flux` channel (each mesh cell's tally error, diagonal across groups); `export-r2s` writes a
banded photon source.

**What is missing.** Bands stop at the source. The dose needs:

- the sensitivity of the dose to each parameter, `dD/dp = sum_c R_c dS_c/dp`, where `R_c` is the dose per unit source
  from region `c`. One photon run tallying dose by source birth region (an OpenMC mesh-born or cell-born filter) gives
  every `R_c`;
- the right correlations: nuclear-data parameters are fully shared by every region; neutron tally noise is independent
  between regions and groups; the photon tally's own noise is added on top.

**Groundwork (2026-10-30, from reading OpenMC 0.15.3 and ACTINV source; nothing run).**

- OpenMC: `MeshBornFilter` and `CellBornFilter` bin a photon tally by source birth region. R2SManager accepts a
  user `photon_model` with its own tallies, so `R_c` needs no OpenMC change. There is no birth-energy filter, so
  per-group `R_{c,g}` needs one photon run per coarse source group (5–10).
- ACTINV: `export-r2s` carries one pooled sigma per nuclide and cell, which mixes shared and independent channels.
  `export-r2s-joint` gives the exact cell-pair correlation of total photon strength, but for MF=33 only and without
  the sensitivity vectors.
- **Step one:** export the source covariance `C_S` over (region, photon group). This is the shared-channel block
  (MF=33, decay, yields) plus the per-region flux-noise block. Then `Var(D) = R^T C_S R + sum_c S_c^2 sigma_{R,c}^2`.
- **Cheapest validation:** D is linear in S at fixed R. So about 200 sampled ACTINV-only reruns with R fixed check
  `R^T C_S R`, and a few OpenMC seeds check `sigma_R`. No full R2S reruns are needed.

**Tests, before SINBAD.**

- A1: on the steel cube and one tungsten case, compare the first-order dose band with brute-force sampling: perturb
  cross sections and fluxes, rerun steps 1–3 N times. Pass: band widths agree within a stated tolerance.
- A2: report the dose band against OpenMC's own R2S, which has no band.

**A1 result: P175 PASS after Amendment A** (CI green on 331b3cc). `export-r2s-cov` writes `C_S` with the shared
MF=33 columns and independent flux columns. On four cases (steel with MF=33 and half-lives, steel with flux noise
only, steel with all channels, tungsten with all channels), at 1 and 30 days after shutdown, and for a uniform and a
cell-weighted response, `R^T C_S R` matches the variance of 200 sampled mesh reruns within each row's registered
limit (ratios 0.87–1.29). The photon-tally term `sigma_R` is not tested, and the cases are synthetic. The first run
exposed two defects in the reference sampler, now fixed.

**Test, with SINBAD FNG.** Does the band cover the measured dose at the stated coverage? This is the claim no other
code can make today.

## 2. Group-collapse and resonance error in the reaction rates

**Gap.** Group-wise activation codes approximate the within-group flux shape. CINDER's 63-group rebinning gave about
90% error on one reaction; FISPACT-II users report group-wise capture in tungsten overestimated without self-shielding
factors derived from continuous-energy runs (Gilbert, Sublet and Dudarev 2016).

**What ACTINV has.** The OpenMC adapter tallies 709 groups, so the flux is never rebinned. Bondarenko and geometry
self-shielding exist but have no recorded comparison. `options.rate_scale` scales any single collapsed reaction rate.

**Test, before SINBAD.**

- B1: in each region of a steel, tungsten, copper and concrete model, compare ACTINV's 709-group collapsed rates for
  the dose-dominant reactions with OpenMC continuous-energy reaction-rate tallies of the same reactions on the same
  data. This measures the error directly.

**B1 result: P173 PASS** (CI green on 5e66166). On the same ENDF/B-VIII.1 data, 76 of 78 rows used:
- Collapse and self-shielding are material for capture only: Mn-55, Co-59 and Mo-98 (n,γ) in every steel region
  (ACTINV's flat-lethargy rate 6.6–11.7% above the continuous-energy tally), Cu-63 (n,γ) in CuCrZr (14.3–17.6%). No
  threshold reaction is material, and nothing in tungsten or concrete. Item 2's fix is warranted.
- Processing is material for one reaction: W-182(n,p) is 6% below NJOY. Cause, found after the run: the evaluation
  has no MF3 MT103, only the MT600–649 partials, and its MF10 MT103 state cross sections sum 3–8% below those
  partials at 11–18 MeV. NJOY (and so OpenMC) builds MT103 from the MF3 partials; ACTINV's builder takes MF10
  (`builder.rs`, lumped-channel synthesis skipped when MF10 holds the summary MT). An evaluation inconsistency, not
  an arithmetic error in either code; which to follow is a builder policy decision.
- Before the per-region correction: score ACTINV's existing Bondarenko self-shielding on the same P173 rows. If it
  removes the capture differences, the fix is a default, not a new tally.

**P176 result: PASS, no candidate** (CI green on 3025d89).
- ACTINV's Bondarenko table with composition dilution removes the steel capture differences. Mn-55 drops to 0.005,
  Co-59 to 0.022–0.026 and Mo-98 to −0.022 to −0.025.
- It makes no other row material, but leaves Cu-63 (n,γ) in CuCrZr at 0.055 and 0.069, above the 0.05 materiality line.
- The geometry option was not measured: ACTINV refused the ENDF/B-VIII.1 Cu-63 file, a defect whose fix
  (`self_shielding.geometry.profile endfb8`) follows this verdict.
- So item 2 proceeds. The cheapest form may now be the Bondarenko table on by default, plus a correction for the rows
  it leaves, rather than a correction for every capture row.

**Fix, if B1 shows it matters.**

- Step 1 also tallies continuous-energy rates for the top-N dominant reactions.
- ACTINV applies the ratio per region as a rate correction. This needs per-cell `rate_scale` in the mesh
  specification, which is shared across cells today.

Scored on held-out materials or regions.

## 3. Mixed materials in mesh elements

**Correction to the first read.** OpenMC 0.15.3's mesh R2S already splits each mesh element by material
(`mesh_material_volumes`), with a flux per element–material pair, and the ACTINV adapter inherits that. The open
problem is cost: regions multiply as elements × materials, which is why fine meshes are coarsened (MCR2S, R2Smesh,
PyNE). ACTINV's contribution here is speed and memory (P163: about 6.5–9× on step 2, about 1 GiB against 6.4 GiB),
not a new correction.

**Test.** C1: time and memory on a mixed-material mesh as the element count rises, with the transport step held
fixed. This extends P163. Report the mesh resolution each code reaches inside the same memory cap.

## Out of scope here, noted from the same research

- Waste classification across several national schemes at once, including fusion nuclides missing from the
  standards. ACTINV covers US 10 CFR 61, EU, German, UK and Canadian clearance, and US transport tables.
- pypact-compatible output (planned for 1.8).
- Too few measured shutdown-dose experiments: SINBAD FNG via RSICC DLC-237 (US route; the NEA request was cancelled).

## Order

1. B1: cheapest, and it decides whether item 2 is real.
2. A1.
3. C1.
4. Fixes from B1 if warranted.
5. SINBAD FNG scoring of items 1 and 2 when the licence arrives.
