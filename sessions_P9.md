# ACTINV P9 — session close, 2026-08-26

**Protocol:** `protocols/ACTINV-P9_PROTOCOL.md`
(`028c5846865490e9dee5902f22f5ad4be583ee332be9d92ce23efa80c52d39c0`).
**Verdict (`controls/check_p9.py`): P9-CONDITIONAL** — all six gates pass after the one repair pass recorded in
Amendment A (`5ac1f139b4f4c878f8595b685bdc1e7f0be95abc74b21ad3309df9ecc13dd4f6`).

| gate | result |
|---|---|
| G1 composition/NFPY | PASS — Rust/OpenMC yield difference `3.24e-16`; raw U-235 independent sums within `4.89e-7` of two; explicit basis difference `1.49e-16`; rejection plants fail closed |
| G2 fission matrix/conservation | PASS — matrix difference `1.85e-16`; mapped plus leakage source closes exactly; MT=459 has no effect; missing-product and missing-parent paths remain distinct |
| G3 coupled/auto | PASS — threshold quantities differ by at most `2.12e-16`; coupled parent depletion by `4.00e-16`; cases bracketing `1e-6` and non-unit multipliers select the expected mode |
| G4 pulses/OpenMC | PASS — dense exponential `1.33e-15`, OpenMC CRAM48 `3.91e-15`; timeline exact; split/merged histories `7.09e-16`; decay-gap effect analytically recovered |
| G5 ALARA identical data | PASS — official 2.9.2 build/reference run; identical collapsed rate exact; ten-pulse timeline exact; shutdown inventory maximum `4.12e-8` relative |
| G6 CoNDERC/provenance/regression | PASS — all 175 finite points; normalizations/hashes close; pre-P9 deterministic differences zero; tests, strict Clippy and rustfmt pass |

P9 delivers explicit isotope/isomer composition keys under all three bases, a strict ENDF-6 MF=8/MT=454/459 reader,
hash-pinned effective independent-yield selection, and fission-product matrix feeds with complete mapped/leakage
balance. Automatic mode now uses each initial isotope's reaction-loss optical depth over multiplier-weighted exposure.
The ordered schedule is the pulse representation, and every boundary reports elapsed time, weighted exposure and
physical fluence. Ordinary and mesh preparation share the complete fission configuration.

The U-235 CoNDERC results report accuracy without making it a post-hoc gate:

| history/channel | points | geometric mean C/E | range | max \|ln C/E\| |
|---|---:|---:|---:|---:|
| Dickens pulse beta | 32 | 0.9882 | 0.9305–1.0430 | 0.0720 |
| Dickens pulse gamma | 32 | 1.0183 | 0.8852–1.1884 | 0.1726 |
| Dickens pulse total | 32 | 1.0070 | 0.9555–1.0691 | 0.0668 |
| Yarnell 20,000 s total | 79 | 0.9845 | 0.9218–1.0194 | 0.0814 |

UKAEA-R(18)003 supplies the paired FISPACT-II context. Gauld's 2019 ORIGEN summary is contextual rather than an
identical-data comparison because its SCALE/ENDF library combination differs. By contrast, G5 runs ACTINV and ALARA
on the same extracted FENDL-2 Fe-56(n,p)Mn-56 cross section and decay evaluation. G4 uses the same matrix/history with
OpenMC's independent CRAM48 action.

**Repair round.** The first staging pass exposed four control/reporting issues: the local ALARA build's transcript and
mass-56 symbol formatting differed from the harness assumptions; the official FISPACT flux file has a normalization
and title after its 709 groups; the Dickens CSV header obscures that the report's plotted pulse ordinate is cooling
time times power/fission; and Rust 1.98 added two mechanical Clippy findings in new probes. Amendment A freezes the
corrections. No production physics, source datum, fixture, acceptance tolerance or accuracy gate changed, so the
successful checker verdict is conditional rather than pass.

The final evidence re-hashes the external NFPY/decay/activation/CoNDERC/report inputs and ACTINV certificates. The
original archives and reports, plus the ALARA source/build, remain outside the repository under `~/nuclear-data`.
An incidental duration-parser defect found in touched code is also fixed: `1e-8 s` is now parsed as scientific
notation and covered by a unit test.

P5 retains P5-PASS and P6–P8 retain their conditional verdicts. P9 does not publish a release or complete v0.5.
Tagging, pushing and publishing remain the principal's external acts.

**Next phase:** P10 — Data completeness, as fixed in `docs/ROADMAP.md`. It remains unopened and unhashed.
