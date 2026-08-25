# ACTINV P3 — Amendment A (append-only), 2026-08-26 — G2 control (a) reference

**Trigger:** the installed openmc 0.15.3 wheel lacks its compiled resonance-reconstruction module
(`RuntimeError: Resonance reconstruction not available`), so the planned 0 K comparison cannot be made.
**Replacement reference (stronger):** FENDL-3.2c publishes, for the same evaluation, the raw ENDF-6 file and IAEA's
NJOY2016-processed ACE at 293.6 K (RECONR err 0.001, BROADR err 0.001; inputs in `njoy/*.nji`). Control (a) becomes:
own reconstruction of the FENDL evaluation's resolved range + own SIGMA1 broadening to 293.6 K, evaluated at the ACE
energy-grid points inside the resolved range, versus the ACE values — Fe-56 (Reich–Moore, NAPS=1, 312 resonances) and
Ag-107 (MLBW, NAPS=0, 400 resonances) for MT 2 and MT 102. Tolerance ≤ 3e-3 relative (three 0.1 % NJOY tolerances:
reconstruction linearisation, broadening, thinning); median reported. TENDL-2023 files remain the P3 targets for the
formalism coverage report (control (d)). Nothing else changes.
