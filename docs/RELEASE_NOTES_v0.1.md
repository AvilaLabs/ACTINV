# ACTINV v0.1.0 — release notes

*Research-grade. Not validated for licensing, safety or regulatory decisions.*

## What this is

An open, standalone, activation-grade nuclide-inventory solver. A neutron flux spectrum from any source — MCNP, PHITS,
Serpent, OpenMC, or a measurement — plus a material and an irradiation history gives the nuclide inventory, activity and
decay heat over cooling time, together with a ledger of everything the calculation could not account for.

## What works in v0.1

- **Data pipeline, entirely our own.** ENDF-6 parsing, resolved-resonance reconstruction (SLBW, MLBW, Reich–Moore),
  SIGMA1 Doppler broadening and 709-group collapse. Verified against IAEA's NJOY-processed FENDL cross sections of the
  same evaluations to 2.3e-3 (medians 1e-5 to 3e-4).
- **Libraries.** EAF-2010 (816 targets) and TENDL-2023 (2,847 targets), both built by that pipeline.
- **Solver.** CRAM-16 with an in-house sparse complex LU, in Rust. Reachable-set and rate-significance pruning, each
  reporting a bound on what it removed. A typical benchmark experiment solves in about 3 ms.
- **Interfaces.** `actinv run spec.json`, `import actinv` from Python, and the validation harness — one binary reached
  three ways, verified identical to 0.0.
- **Pathway analysis.** Which chains produced each nuclide, with contributions that sum to the population to 6e-15.
- **Every run carries a ledger and a certificate**: what was missing, what was approximated, what was pruned, the hash
  of every input, and the numerical floor beneath which the method cannot resolve anything.

## Validation

FNS decay-heat benchmark (IAEA CoNDERC), 73 materials, 132 experiments, D–T irradiation at JAEA:

| | ACTINV / EAF-2010 | ACTINV / TENDL-2023 | FISPACT-II / TENDL-2017 |
|---|---|---|---|
| median geometric-mean C/E | 1.024 | 1.035 | 1.009 |
| median max abs ln C/E | 0.284 | 0.311 | 0.223 |
| within 30 % everywhere | 47 % | 45 % | 52 % |

Every C/E is re-derivable from the stored inventories by `controls/check_p4.py`; the run certificate hashes every input.

## Known limitations

Each is guarded — the calculation reports it rather than hiding it — and each is scheduled.

| limitation | affected data | guard | scheduled |
|---|---|---|---|
| Group values for evaluations with synthetic resonances far narrower than the Doppler width do not converge to 1e-3 between grid densities (worst 1.5e-2) | TENDL-2023 Fr-226, Rb-94; no FNS benchmark material contains either | `convergence_flag` in the library index; propagates to every run's ledger as `library_convergence_flags` | P10 (draft protocol: `protocols/DRAFT-P4b_PROTOCOL.md`) |
| R-matrix-limited (LRF=7) resolved ranges and unresolved ranges with LSSF=0 are not reconstructed | e.g. FENDL W-186 | ledgered per target as unsupported; MF=3 background used, never silently approximated | P10 |
| Fission products are not followed (no yields) | actinide targets | explicit leakage state, `fission_no_yields_to_leakage` with rates | P9 |
| 18 reaction products have no evaluated decay data in ENDF/B-VIII.0 or JEFF-3.3 | exotic products, nil realised in FNS | `products_no_evaluated_decay_data_ENDFB80_JEFF33`, booked to leakage | none known |
| **P7** Photon source & dose | Parse decay spectra (MF=8 continuous/discrete); line and multigroup decay-photon sources; contact γ dose-rate proxy (semi-infinite slab) and specific γ constants; export as OpenMC source and MCNP SDEF. | spectrum energy integral = Ē_EM per nuclide to 1e-6; known-nuclide dose constants (Co-60, Cs-137) within 2 % of tabulated | P5 | 2 |
| **P8** Flux import & mesh | Readers for OpenMC statepoint tallies, MCNP meshtal/mctal, FISPACT fluxes; mesh mode: independent cells in parallel (rayon), pruned per cell; sizing table to 10⁶ cells. | imported flux reproduces the file's totals to 1e-12; mesh result = per-cell single runs at 0.0; timing table | P5 | 3 |
| **P9** Fission & coupled mode | Fission yields (ENDF/B nfpy) as products; coupled bulk mode for burn-up > 1e-6 with automatic selection; pulsed histories; second validation family: CoNDERC fission decay-heat set; code-to-code vs ALARA and OpenMC on identical data. | yield sums = ν_f-consistent to 1e-6; coupled vs trace agree where burn-up ≪ 1; fission set C/E reported with FISPACT/ORIGEN references | P4 | 3–4 |
| **P11** Uncertainty | Covariance (MF=33) propagation to collapsed one-group cross sections; sensitivity of heat/activity to each collapsed σ; uncertainty bands in reports and certificates. | propagated variance = sampled variance on a 2×2 case to 1e-3; sensitivities vs finite differences to 1e-4 | P5 | 3 |
| **P12** v1.0 hardening | Clearance/waste indices (configurable table), ICRP dose coefficients, independent re-verification of abundance/mass tables from a primary source, parser fuzzing, FNG/ITER shutdown-dose activation step with provided fluxes, docs for use in licensing chains, v1.0 release. | all prior controls green in CI; fuzzing finds no crash in 10⁶ cases; FNG activation step vs reference | P7–P11 | 3–4 |

Also absent by design in v0.1: neutron transport of any kind, criticality, thermal-hydraulics, a graphical interface,
shutdown-dose-rate photon transport (the decay-photon source arrives in v0.2), flux import from transport-code files
(v0.2), fission yields (v0.5), covariance-propagated uncertainty (v1.0).

## Reproducing the validation

Nuclear data are never bundled. `docs/DATA.md` names every source and its terms; every run pins each input by SHA-256.
The harness and every control ship with the code, so the numbers above can be re-derived rather than taken on trust.

## Licence

MIT OR Apache-2.0. Contributions under the Developer Certificate of Origin; see CONTRIBUTING.md.
