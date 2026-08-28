# ACTINV P3b — archived session close, 2026-08-26

**Scope:** G2 of P3 (resolved-resonance reconstruction + Doppler broadening) under its own protocol (90e011a4…).
**Verdict (controls/check_p3b.py): P3b-PASS** — no repair round.

| control | result |
|---|---|
| (a) vs NJOY/FENDL-3.2c ACE 293.6 K, Fe-56 RM + Ag-107 MLBW, MT 2/102 | maxima 4e-4–2.3e-3 (line 3e-3), medians 1e-5–3e-4 |
| (b) exact SIGMA1 laws | 1/v 1.3e-7; constant law 9.8e-8 |
| (c1) exact-kernel quadrature | ≤ 1.4e-12 |
| (c2) ψ reference within ±5 Γ | peak 5.5e-7, wings 2.0e-5 |
| (d) TENDL-2023 vs EAF-2010, Fe-56 on the FNS spectrum | (n,2n) 0.360 vs 0.442 b; (n,α) 0.0404 vs 0.0364; (n,p) 0.0936 vs 0.0940; (n,γ) 2.15e-3 vs 1.34e-3 b (reported) |

With P3 (G1, G3, G4 PASS; G5 recorded) this completes the P3 scope. Unsupported in the reconstruction: LRF=7 and
unresolved ranges with LSSF=0 — ledgered, never approximated.
