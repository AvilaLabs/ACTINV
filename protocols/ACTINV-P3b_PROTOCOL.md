# ACTINV P3b — resolved-resonance reconstruction and Doppler broadening, second attempt (G2 of P3)

**Opened:** 2026-08-26 after P3 closed P3-FAIL on G2. **Time box:** one calendar day. Only G2 is in scope; all other
P3 results stand. **Controls (frozen here):**
(a) own reconstruction of the FENDL-3.2c evaluations (Fe-56 Reich–Moore NAPS=1; Ag-107 MLBW NAPS=0) at 0 K on a
    resonance-adaptive grid — log backbone (20,000 points) ∪ ACE points ∪ for every resonance E_r ± 200 Γ_r sampled
    uniformly in arctan(2(E−E_r)/Γ_r) with 401 points (fine at the peak, coarse in the wings) ∪ midpoints refined once
    where linear interpolation error > 1e-4 — broadened to 293.6 K by SIGMA1 and sampled at the ACE points inside the
    resolved range, versus IAEA's NJOY2016 ACE: ≤ 3e-3 relative for MT 2 and MT 102 (top 0.1 % of the range and points
    with σ < 1e-6 b excluded and counted); median and p99 reported.
(b) exact laws of the SIGMA1 kernel: 1/v is invariant (≤ 1e-6 on a 16,000-point log grid, 1e-3–1e4 eV); a constant
    σ₀ broadens to σ₀(1 + 1/(2y²)) exactly (≤ 1e-6 for y ≥ 3).
(c1) exact-kernel quadrature vs the analytic SIGMA1 on 1/v, constant, linear and a resonance line ≤ 1e-6.
(c2) ψ-function reference (Gaussian-in-energy approximation), E_r = 1e5 eV: ≤ 2e-3 at the peak and within ±5 Γ —
    information beyond ±5 Γ is reported, not gated.
(d) TENDL-2023 Fe-56/Ag-107/W-186 formalism coverage and 293.6 K one-group values on the FNS spectrum vs EAF-2010 —
    reported. Unsupported formalisms (LRF=7, LRU=2 with LSSF=0) are ledgered, never approximated.
**Verdict:** P3b-PASS if (a), (b), (c1), (c2) pass; P3b-CONDITIONAL after one repair round; else P3b-FAIL.
Memory rule in force: `ulimit -v 12000000`; chunked kernels.
