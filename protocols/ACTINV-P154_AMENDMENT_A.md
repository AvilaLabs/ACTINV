# P154 Amendment A — reference for the elastic gate, NJOY treatments for the comparisons

Registered after development run 1 (disclosed in the ledger) and before any evidence run. Tolerances are unchanged.
Where NJOY MT=445–447 stop being the gate reference, the comparison with them is still made and reported.

## Findings that require it

- **Elastic (G3).** NJOY2016.79 HEATR's elastic damage carries numerical approximations that exceed the 2 % tolerance
  in the groups that failed. (a) The elastic damage onset uses a fixed 25 eV displacement energy, not the run's
  (heatr.f90:1891, 1941), and the damage is linearly interpolated on a 10 % energy grid. Near W-184's onset (3.2–6.4
  keV, E_d = 70 eV) this gives −9 % to +4 %. (b) Tabulated MF=4 distributions are projected onto Legendre moments with
  a 64-point quadrature (hgetco). This cannot resolve the forward-peaked TENDL distributions above about 60 MeV: at
  200 MeV, 36–51 % of the W-184 pdf lies beyond the outermost node, and NJOY falls 3.7 times low in the top group.
  For Fe-56, Cr-52 and Ni-58 the top groups fall 2–3 % low. Outside the unresolved range, the table equals NJOY's
  pointwise σ times the exact per-event elastic damage to within 1e-4.
- **Capture (G4).** For TENDL-2025 the capture photons are in MF=6. HEATR then partitions each photon's recoil
  separately (sixbar/tabsq6), not as one photon (capdam). P154's `evaluated` capture treatment forms one recoil from
  the summed photon recoil. The two differ by 7–15 % at thermal energies.
- **Continuum (G4).** For MT=91, HEATR takes the damage from the evaluation's own residual-nucleus recoil spectrum
  (getsix). P154 derives the recoil from the emitted neutron's evaluated spectrum by momentum conservation. In
  TENDL-2025 Fe-56, Cr-52 and Ni-58 the two are inconsistent: up to 28 % of the recoil probability lies outside the
  window the neutron data allow. Group damage then differs by up to 40 % at 5–8 MeV.
- **Frame convention.** Development run 1 showed that the candidate read LCT=3 light ejectiles in the laboratory
  frame. The ENDF convention, which NJOY follows, is centre of mass for light particles and laboratory for heavy
  recoils. This is fixed in the candidate before evidence.

## A1. G3

The gate reference is NJOY's RECONR pointwise elastic cross section times the checker's own exact per-event elastic
damage W(E). W(E) is computed from the evaluation's MF=4 distribution by adaptive integration over μ, with the
damage function and displacement energy of the protocol, and the product is collapsed as before. The 2 % tolerance
and the group selection are unchanged. The table against NJOY MT=445 is reported, with the approximations above
named.

## A2. G4

The tables compared with NJOY MT=446 and MT=447 are built in NJOY's treatments: `--capture-photons per-photon` where
the evaluation gives MF=6 capture photons, and `--continuum-recoil evaluated` where a continuum channel carries a
residual-nucleus recoil spectrum. These two opt-in build options reproduce HEATR's treatment; the defaults stay
`evaluated` capture and `kinematic` continuum recoil, and the default-mode tables against NJOY are reported. The checker
determines each treatment from the evaluation file. A group whose uncovered damage-energy bound is at most 0.5 % of
the channel's computed value (TENDL's placeholder cross sections) counts as covered.

## A3. G5

The bound check uses NJOY MT=444 with its MT=445 part replaced by the A1 reference, on the A2 tables. Raw MT=444 is
reported.
