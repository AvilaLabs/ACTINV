# FNS iron discrepancy diagnosis 001

Frozen before new diagnostic numerical execution, 2026-09-18.

The original FNS-IRON-001 result remains unchanged: geometric-mean C/E
0.9262795135736329; 5/20 points inside reported errors. This is a diagnostic
follow-up, not a new blind validation trial or an accuracy acceptance gate.

## Questions and fixed procedure

1. Reconstruct each nuclide's activity and heat from fresh CLI inventories,
   independently parsed ENDF/B-VIII.0 decay records (JEFF-3.3 only for missing
   keys), lambda = ln(2)/half-life, and the three total mean energies: light,
   electromagnetic, heavy. Do not double-count their detailed subcomponents.
   Compare summed heat against CLI, tolerance 1e-10 relative + 1e-18 W/g.
   Report Mn56, Mn57, Fe53 and the remaining inventory at every measurement.
2. Check zero-flux Mn56 cooling against N(t0) exp(-lambda*(t-t0)), starting
   at the first measurement. Report discrepancies without assuming this
   independently checks irradiation production or all daughter feeding.
3. Execute all 21 published 2000 five-minute Fe measurements at their exact
   times, with its own archived spectrum and deck. Use the unchanged released
   patched TENDL-2025 library and decay data from FNS-IRON-001. Check mass,
   irradiation, flux, and numerical equality of both archived spectra.
   Do not tune inputs or replace the 1996 case with the better-fitting campaign.
   Report both C/E distributions and reported-error coverage separately.
4. Compare the existing archived ACTINV and FISPACT TENDL-2017 comparison
   evidence, explicitly noting its different library and rounded cooling times.
   It is corroboration, not a same-data solver verification.

All source members are read from the same SHA-256-pinned CoNDERC archive as
FNS-IRON-001. Additional member SHA-256 values:

- `fns/Fe/2000exp_5min.exp`: `7c95977ba08e6cd2afc15d719cf6e55e6326952630c8b22d32ec02a0ef2cedd5`
- `fns/Fe/2000exp_5min_fluxes`: `ccfe40519346d7525c7211dfefacb13f38fe933ed302c13ce060a75e03aebf50`
- `fns/Fe/TENDL-2017_2000exp_5min.i`: `cad6fe7a947ae98aac019532f9c3e60192b20b3ae9fe13b511471dd6c3d71480`

Primary interpretation source: Gilbert and Sublet, Nuclear Fusion 59 (2019)
086045, section 4.2, DOI 10.1088/1741-4326/ab278a:
https://scientific-publications.ukaea.uk/wp-content/uploads/GILBERT_SUBLET_NUCL_FUS_2019.PDF
It already reports unexplained variation between five-minute iron batches.
This motivates the comparison; it does not prove either campaign is wrong.

No parameter fitting, measurement rescaling, solver changes, or promotion of
reported errors to a defined confidence level. Failures remain in an append-only
ledger. Numerical jobs execute sequentially in hosted CI, avoiding workstation
contention. Preserve raw outputs in CI artifacts; commit compact diagnostic
results and source/code hashes only. If no defect is established, report that
and identify what remains unresolved rather than manufacturing a fix.
