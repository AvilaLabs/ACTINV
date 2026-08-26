# ACTINV P7 — session close, 2026-08-26

**Protocol:** `protocols/ACTINV-P7_PROTOCOL.md`
(`5dd3c3f5f09440a0bb69154ba18d99d581f862001a252c41f1f1102fbc615255`).
**Verdict (`controls/check_p7.py`): P7-CONDITIONAL** — all gates pass after one repair round recorded in Amendment A
(`5f5319ced7faff3f1141c80619c738c99b9d8593f8aa8ea4997d42293ff4b9c5`).

| gate | result |
|---|---|
| G1 spectrum reader | PASS — 3,821 sections/7,113 spectra; 3,785 selected records; maximum relative field difference `3.04e-16` |
| G2 source conservation | PASS — count closure `3.57e-16`, `E_EM` closure `2.90e-15`; planted missing/outside-group cases recovered |
| G3 inventory identity | PASS — 21 steps/518 nuclide rows; CLI, Python and harness have zero differences; closure at most `4.15e-16` |
| G4 dose references | PASS — Co-60 within 1.09 % and equilibrium Cs-137/Ba-137m within 1.34 % of tabulated constants |
| G5 transport exports | PASS after Amendment A — 24 groups, exact cross-export values, OpenMC syntax valid, MCNP maximum 76 columns |
| G6 provenance/regression | PASS — bad hashes fail closed; computed hashes match; pre-P7 scalar differences zero; P5/P6 stay green |

P7 delivers a lossless ENDF MF=8/MT=457 spectrum reader, analytic integration of ENDF interpolation laws 1–5,
inspectable line and multigroup sources normalized to evaluated electromagnetic decay energy, per-nuclide source and
dose contributions, FISPACT-compatible contact-dose and point-source gamma-constant proxies, and OpenMC/MCNP source
exports. The spec, result, ledger and certificate documents now carry the relevant photon configuration, corrections,
bounds and verified input hashes.

The response builder records official NIST source-page hashes and produces deterministic external data. The P7 Fe
artifact is `/home/connoravila/nuclear-data/photon-response/nist-xcom-air-fe.json`, SHA-256
`4f00824ac66ef941cddbe20b93966523b7f0ff2271b35cdf8be538c48e404307`; rebuilding from its cached source pages produced
identical bytes.

**Repair round.** The first G5 control parsed MCNP `SP1` probabilities starting on the line after the card and therefore
discarded its first value. It also described an 80-column criterion while enforcing the protocol's 78-column criterion.
Amendment A froze the correction before it was applied. The repaired independent parser consumes the entire continued
card and the prose matches the gate. No exporter or production result changed.

**Incidental corrections in touched paths.** Input hashes are computed rather than trusted; material bases now have
their documented semantics; radioactive trace material contributes to heat splits and photon activity; pruning bounds
and inherited library limitations reach the ledger; and PyO3 0.29.2 supports the system's CPython 3.14 build. These
changes preserve the pre-P7 scalar baseline exactly.

**Next phase:** P8 — Flux import & mesh, as fixed in `docs/ROADMAP.md`: OpenMC statepoint, MCNP meshtal/mctal and FISPACT
flux readers, independent parallel mesh-cell solves, and a sizing table through 10^6 cells. P8 is not opened and has no
protocol hash.
