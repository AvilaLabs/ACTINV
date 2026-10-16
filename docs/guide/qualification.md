# Scope and qualification

ACTINV is research-grade calculation software. Recorded controls demonstrate specified numerical, data-handling, and benchmark behavior within their stated bounds. They do not approve ACTINV, or a calculation made with it, for licensing, safety, waste classification, or another regulatory purpose.

## Where ACTINV fits

1. A transport calculation or measurement supplies the particle spectrum and its physical normalization.
2. The analyst chooses activation, decay, yield, covariance, and response data appropriate to the problem.
3. ACTINV calculates inventory evolution and requested responses, reporting coverage and omissions.
4. Separate transport or consequence tools use the source terms when spatial or shielding effects matter.
5. The responsible organization reviews applicability, uncertainty, margins, configuration, and acceptance.

The certificate identifies the files used. It does not establish that those inputs are physically suitable.

## Supported scope and boundaries

| Capability | Boundary |
| --- | --- |
| Neutron, proton, deuteron, triton, helion, alpha, and gamma (photonuclear) activation | Triton and helion (data-limited): the builder, spec and solver accept them through the same charged-particle path as the other projectiles, but no triton or helion library is in the standard catalog, no cross-code comparison has been run for either, and the only tests are synthetic fixtures. Gamma (1.4.0): no photofission product yields, no temperature treatment, and no published library (build from the TENDL `g` sublibrary); cross-code agreement with FISPACT-II is established below 30 MeV, and above it only against FISPACT-II's own MT5 processing rule |
| Incident energy range | A library holds cross sections only where its source evaluation tabulates them and only up to the top of its group structure: TENDL neutron evaluations reach 200 MeV, the 709- and 1102-group neutron grids and the 162-group charged-particle grid reach 1 GeV, and the EAF-family grids stop at 20 MeV (55 MeV for 211 and 351). Above an evaluation's upper energy groups are zero, not extrapolated. The opt-in spallation splice adds user-supplied residual-production data above a chosen energy; ACTINV supplies none and does not qualify the table |
| Schedule-level library switch | A step may switch to another activation library (another projectile or evaluation); verified on a synthetic two-library chain only, not against FISPACT-II's multi-library runs. Not available with uncertainty, gas, self-shielding, damage, fission yields, mesh or study |
| Single-material and independent-cell inventories | No particle transport, criticality, spatial material exchange, or thermal/flux feedback |
| Photon sources and contact gamma response | Contact response is a semi-infinite-slab screening proxy; ordinary exports use a point at the origin |
| Finite-dilution neutron self-shielding | Explicit unresolved-range Bondarenko table required, with optional per-group dilution overrides; damage is not shielded. The opt-in geometry mode (foil, wire, sphere, cylinder) applies the universal sigmoid curve to resolved Breit-Wigner, Reich-Moore or R-matrix-limited (LRF=7) resonances from user-supplied ENDF files; it neglects other mixture components in the resonance cross section and has unit-test coverage only, with no recorded comparison against FISPACT-II or measurement |
| Cross-section, half-life, independent-yield, and transport-tally uncertainty | Only requested, retained channels propagate; flux statistics use supplied groupwise errors, not systematic transport errors |
| Hydrogen and helium isotope production (1.4.0) | Neutron-only `options.gas`; excludes ternary-fission gas and cannot be combined with uncertainty |
| Spontaneous fission, fission count and burn-up | Opt-in. Spontaneous-fission branches reach products only for states with a user-supplied yield evaluation, and the neutron source covers only states with a user-supplied or decay-file multiplicity; the rest stay on leakage or are reported uncovered. The count is the MT=18 rate integral plus spontaneous decays, and burn-up in MWd/kg uses a stated per-fission energy (200 MeV default), not a nuclide-specific value. No ternary-fission products, no delayed-neutron time dependence, and no cross-code qualification of these outputs |
| Radiological responses | Explicit user-selected table required; ACTINV supplies no default regulation, jurisdiction, intake scenario, or margin. Public-domain US transport (49 CFR 173.435/.436) and EU Directive 2013/59/Euratom Annex VII tables are bundled but opt-in by name (`bundled:NAME`); ICRP 119 dose coefficients are built locally from your own PDF. Bundled tables are faithful conversions, not an assessment that a regulation applies to your case; element-level entries need your attention, and unlimited or progeny-covered entries are counted as covered with zero contribution and reported separately |
| Feed and first-order removal | Schedule-level source/sink model, not a coupled process flowsheet; pathway attribution does not track fed material |
| Reverse calculation | Linear trace-regime normalization estimate, not spectrum unfolding or a general inverse transport model |
| NRT damage | Declared material targets and displacement energies; no recombination, general damage function, or damage covariance |

Missing decay modes, fission yields, spectra, response coefficients, or material-target data are not inferred. Read the ledger and feature-specific coverage fields. A complete-coverage option checks its declared feature, not the entire analysis chain.

## Uncertainty interpretation

MF=33 cross-section covariance is the default. Half-life and independent fission-yield uncertainties may be requested separately and are treated as diagonal channels. Since 1.4.0 a diagonal transport-tally statistical `flux` channel is also available; it can run alone without an MF=33 sidecar.

Decay-yield and cross-channel correlations, MF=32 resonance covariance, MF=40 production covariance, composition, response-coefficient, geometry, and model uncertainties are outside the propagated band. Flux uncertainty is excluded unless its channel is requested; even then, systematic errors in the transport model, geometry, and transport nuclear data remain excluded.

An explicitly declared unmodeled relative contribution can supplement that band, including a term drawn from a calibration or evaluation-spread artifact. It does not recover a missing channel or establish coverage of all omitted sources.

These are first-order intervals for retained input uncertainties. They are not tolerance limits or safety margins. The alternate CRAM-order difference is a method diagnostic rather than a proof of total numerical error.

## Assess and retain a calculation

Confirm composition, basis, mass, spectrum units and normalization, group ordering, schedule, projectile, temperature, target coverage, and data applicability. Check [known evaluation defects](data-limits.md) and [validation evidence](validation.md) for the intended reactions and time range.

Retain the exact software version or commit, complete problem and result, original data and terms, generated libraries and indexes, upstream tally definitions and normalization, selected coefficient tables, and your applicability and uncertainty assessment. Apply the independent reviews and acceptance process required by the organization responsible for the analysis.
