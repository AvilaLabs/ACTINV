# FNS-IRON-DIAG-001 Amendment 1: isolate the spectrum difference

Frozen 2026-09-18 after initial hosted run 35400313891 and before this new
counterfactual calculation. Initial evidence remains in
`results/fns-iron-diagnosis/initial.json` and the append-only ledger.

Both campaigns use the same mass, irradiation duration and total flux, but
initial inspection by the diagnostic confirmed their 709-group spectra are
numerically different. C/E changes from 0.92628 (1996) to 1.05950 (2000).

Run exactly one additional case: the original 1996 measurement schedule and
all its original inputs, changing only the spectrum shape to the archived
2000 spectrum. Preserve the total flux. Report the resulting heat ratio
against the original 1996 calculation at every identical time. Reconstruct
its heat independently by the original protocol. This isolates spectrum
sensitivity; it is a counterfactual, not an additional experimental benchmark
or a replacement for either original campaign. No fitting or accuracy gate.

Also complete the original protocol's archived-reference investigation with
the printed FISPACT atom counts, activity and heat at the first 1996 cooling
point (66 seconds), for Mn56, Mn57 and Fe53. Pin the additional archive member
`fns/Fe/TENDL-2017_1996exp_5min.out` to SHA-256
`29b21cd8936fd99095ecc99b761b7995459c61e2f2fd49654f7b4fa537db8b26`.
Check its sample mass and time before converting total kW to microW/g.
Its activation library differs and printed values are rounded; do not call
this an identical-data solver comparison.
