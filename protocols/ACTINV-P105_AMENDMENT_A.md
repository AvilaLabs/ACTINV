# P105 Amendment A — synthetic decay LIST integer encoding

2026-10-03. G1 write and explicit read-only replay passed all frozen vectors and
supplemental gates. The first G2 invocation stopped before any activation solve:
the synthetic decay mode LIST header used `[0.0] * 6`, encoding its four integer
fields as `0.0000E+00`. The checked ENDF reader correctly refused this malformed
header. Preserve the full invocation output in `results/p105_g2_attempt_1.log`
and the original successor seal in `results/g0_p105_successor_v1.json`.

Encode that zero-mode LIST header as `[0.0, 0.0, 0, 0, 0, 0]`, matching the
established P11 fixture and the checked ENDF CONT/LIST record contract. All other
record values remain unchanged: stable states have NST=1 and zero half-life,
Nb94 has NST=0 and artificial half-life 1e11 s, energies are zero, NDK=0 and no
radiation spectra are present. Capture cross section, flux, schedule, natural
composition, mass/volume, source rules, targets and numerical tolerances do not
change. This is an encoding correction, not a new evaluated nuclear input or a
change to the physical fixture.

Register this amendment and reseal repaired controls before another G2 invocation.
This is P105's one repair round under the standing rules. A subsequent failed
gate closes FAIL; never overwrite the failed attempt or silently retry.
