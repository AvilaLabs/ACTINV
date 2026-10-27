# P154 Amendment B — capture-photon and discrete-level references for G4 and G5

Registered after development run 2 (disclosed in the ledger) and before any evidence run. Tolerances are unchanged.
Raw NJOY MT=444–447 comparisons are still made and reported.

## Findings that require it

Development run 2 passed G2, G3 and G6. Inelastic G4 passed for Fe-56, Cr-52 and Ni-58, with a worst group at 0.26 %.
The remaining failures come from two NJOY2016.79 HEATR approximations. An independent reconstruction from the
TENDL-2025 files reproduces both.

- **Capture photons (G4 disappearance, G5).** In `tabsq6` (heatr.f90:4135–4212), each continuum panel of the MF=6
  photon spectrum is integrated with 4-point Gauss–Legendre weights. Those weights sum to 2, but they are multiplied
  by the panel width `dx` rather than `dx/2`. The normalisation `s` gives the discrete lines unit weight, so the
  continuum counts twice relative to them. The routine also interpolates the spectrum linearly (`inn=2`) whatever
  LEP the evaluation gives; TENDL-2025 gives LEP=1, a histogram.
  - The TENDL spectra are normalised: at 1e-5 eV the discrete probabilities plus the histogram integral equal 1.0000.
  - Per capture at 1e-5 eV, read as the format specifies, the photon-recoil damage energy is 160.5 eV for Fe-56.
    NJOY's arithmetic gives 214.1 eV, and the table gives 160.6 eV.
  - Table/NJOY MT=447 is constant over the capture-dominated groups: Fe-56 0.750, Cr-52 0.807, Ni-58 0.825.
  - For W-184 it is 1.537. Most photon recoils there sit near the 70 eV threshold, and NJOY's 4-point rule across
    the threshold is itself unconverged: 0.161 eV per capture with 4 points, 0.116 eV with 64 points.
- **Discrete-level ladder (G4 inelastic, W-184).** `disbar` (heatr.f90:1829–2013) handles discrete inelastic levels
  as it handles elastic scattering, which Amendment A already found:
  - It evaluates damage at points spaced by a factor 1.1 in energy, starting from the grid energy that first exceeds
    the previous point, and interpolates linearly between them (label 130).
  - Just above a level's threshold the damage rises steeply from zero, so the interpolation is too high. For W-184
    (first level near 111 keV), table/NJOY MT=446 is 0.66 in the 115 keV group, rising to 0.95 by 182 keV.
  - Fe-56, Cr-52 and Ni-58 have no failing inelastic group.

## B1. G4 disappearance

The reference is NJOY MT=447 with the capture-photon term replaced, pointwise on NJOY's PENDF grid:
`447 + σ₁₀₂(E) · (h_exact(E) − h_tabsq6(E))`. It is then collapsed like the other references.
- `h_tabsq6` is the checker's emulation of `tabsq6`: the same weights and `dx`, linear interpolation, the yield
  interpolated in E, and per-record values interpolated linearly in E.
- `h_exact` uses the same records and damage function, read as the format specifies: LEP honoured, each continuum
  panel integrated accurately with splits at the damage-function thresholds, discrete lines at unit weight.
- **Precondition.** In every group where capture carries at least 99 % of NJOY's MT=447, the collapsed
  `σ₁₀₂ · h_tabsq6` must equal NJOY MT=447 to within 0.5 %. Otherwise G4 fails for that target. The precondition
  values are reported.
- Where MF=6 MT=102 photons are absent, the reference stays raw MT=447.

## B2. G4 inelastic

The reference is NJOY MT=446 with the ladder term of the discrete levels MT=51–90 replaced:
`446 + Σ σ_MT(E) · (W_exact,MT(E) − W_ladder,MT(E))`.
- `W_exact` is the exact two-body damage per event, computed as in A1 from the level's MF=4 distribution and Q.
- `W_ladder` interpolates `W_exact` linearly between the points `disbar` would evaluate. Those points follow from
  NJOY's PENDF energy grid, the MF=4 incident energies and the 1.1 step, as the routine sets them.
- **Precondition.** In every group where discrete levels carry at least 99 % of NJOY's MT=446 and at least one level
  is within a factor 2 of its threshold, the collapsed `Σ σ_MT · W_ladder,MT` must equal NJOY MT=446 to within 0.5 %.
  Otherwise G4 fails for that target.

## B3. G5

The bound check uses NJOY MT=444 with three parts replaced: MT=445 by the A1 reference, and the B1 and B2 differences
added. Raw MT=444 and the A3 form are reported.

## Not changed

The candidate's `per-photon` capture treatment stays faithful to the evaluation; it does not reproduce `tabsq6`'s
weighting. This amends Amendment A's statement that the opt-in options "reproduce HEATR's treatment": they reproduce
HEATR's choice of physics (per-photon partition, evaluated recoil), not these two numerical approximations.
