# Method

**Problem.** Nuclide inventory under a neutron flux spectrum and during cooling: dN/dt = A N, A = decay matrix + Σ_reactions
rate matrix, rates = σ̄ φ with σ̄ the spectrum-collapsed cross section per (target, reaction, product, isomer).

**Data.** Activation cross sections: EAF-2010 (816 targets, every channel separate, MF=9/10 isomer branching), parsed by
our own ENDF-6 reader and pre-collapsed to the FISPACT 709-group structure with flat-lethargy weighting. TENDL-2023
support (resolved-resonance reconstruction — SLBW, MLBW, Reich-Moore — and SIGMA1 Doppler broadening) is implemented
in `controls/resonance.py` and `controls/doppler.py`; the R-matrix-limited formalism (LRF=7) and unresolved ranges
with LSSF=0 are reported as unsupported, never silently approximated. Decay data: ENDF/B-VIII.0 primary, JEFF-3.3
fallback, source recorded per nuclide.

**Solver.** CRAM-16 (Pusa 2016 coefficients, incomplete-partial-fraction recurrence) with our own sparse complex LU
(Gilbert–Peierls, partial pivoting, Smith division). Two prunings: reachable-set (exact) and rate-significance (bounded:
a forward bound on atoms that could reach each nuclide; dropped nuclides and their bounds are written to the result).

**Trace activation.** When burn-up is negligible (recorded per run), the composition is a constant source through a unit
state and only products are solved — CRAM's absolute round-off is then relative to the product inventory, not the bulk.

**Ledger.** Every run reports: composition isotopes without cross sections, products without evaluated decay data,
fission booked to leakage (no yields yet), atoms booked to leakage for any reason, negative round-off zeroed, decay
sources used, pruning bounds, measurement rows excluded and why.
