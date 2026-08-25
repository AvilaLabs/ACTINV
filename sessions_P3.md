# ACTINV P3 — session close, 2026-08-26

**Protocol:** protocols/ACTINV-P3_PROTOCOL.md (8008e60c…); Amendments A (NJOY/FENDL reference), B (control refinements,
G3 two-part criterion). **Verdict: P3-FAIL** — G2 (resonance reconstruction + Doppler) failed its controls after the
one repair round; G1, G3, G4 PASS; G5 recorded.

| gate | result |
|---|---|
| G1 decay fallback | PASS — JEFF-3.3 parser 0/200 mismatches; 50 nuclides added; 18 exotic EAF products have no evaluated decay data anywhere (ledgered, nil realised) |
| G2 reconstruction/Doppler | FAIL — implementation exact vs quadrature (1e-12); NJOY agreement medians 3e-5–1e-3 but maxima 1–2 % from under-sampled narrow resonances; two controls were mis-specified (constant "invariance", ψ wings) |
| G3 rate pruning | PASS — 1,440 → 71 states median, 69 → 2.7 ms; removed-heat bound 3e-13 |
| G4 certificate | PASS — every input and record hash re-matched; every C/E re-derived to 3.8e-16 |
| G5 docs | recorded |

Also this session: the memory incident (27 GB kernel; rule: chunk/window, `ulimit -v`), the private remote
AvilaLabs/ACTINV, the no-attribution rule. Successor: P3b (G2 only).
