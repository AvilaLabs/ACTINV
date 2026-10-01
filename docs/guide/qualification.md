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
| Neutron, proton, deuteron, alpha, and gamma (photonuclear) activation | No triton or helion activation. Gamma is current master only: no photofission product yields, no temperature treatment, and no published library (build from the TENDL `g` sublibrary); cross-code agreement with FISPACT-II is established below 30 MeV, and above it only against FISPACT-II's own MT5 processing rule |
| Single-material and independent-cell inventories | No particle transport, criticality, spatial material exchange, or thermal/flux feedback |
| Photon sources and contact gamma response | Contact response is a semi-infinite-slab screening proxy; ordinary exports use a point at the origin |
| Finite-dilution neutron self-shielding | Explicit unresolved-range Bondarenko table required; no resolved-region pointwise shielding; damage is not shielded |
| Cross-section, half-life, independent-yield, and transport-tally uncertainty | Only requested, retained channels propagate; flux statistics use supplied groupwise errors, not systematic transport errors |
| Hydrogen and helium isotope production on current master | Neutron-only `options.gas`; excludes ternary-fission gas and cannot be combined with uncertainty |
| Radiological responses | Explicit user-selected table required; ACTINV supplies no default regulation, jurisdiction, intake scenario, or margin |
| Feed and first-order removal | Schedule-level source/sink model, not a coupled process flowsheet; pathway attribution does not track fed material |
| Reverse calculation | Linear trace-regime normalization estimate, not spectrum unfolding or a general inverse transport model |
| NRT damage | Declared material targets and displacement energies; no recombination, general damage function, or damage covariance |

Missing decay modes, fission yields, spectra, response coefficients, or material-target data are not inferred. Read the ledger and feature-specific coverage fields. A complete-coverage option checks its declared feature, not the entire analysis chain.

## Uncertainty interpretation

MF=33 cross-section covariance is the default. Half-life and independent fission-yield uncertainties may be requested separately and are treated as diagonal channels. Current master adds a diagonal transport-tally statistical `flux` channel; it can run alone without an MF=33 sidecar. [Check release availability](releases.md#current-master) before requesting these additions.

Decay-yield and cross-channel correlations, MF=32 resonance covariance, MF=40 production covariance, composition, response-coefficient, geometry, and model uncertainties are outside the propagated band. Flux uncertainty is excluded unless its channel is requested; even then, systematic errors in the transport model, geometry, and transport nuclear data remain excluded.

An explicitly declared unmodeled relative contribution can supplement that band, including a term drawn from a calibration or evaluation-spread artifact. It does not recover a missing channel or establish coverage of all omitted sources.

These are first-order intervals for retained input uncertainties. They are not tolerance limits or safety margins. The alternate CRAM-order difference is a method diagnostic rather than a proof of total numerical error.

## Assess and retain a calculation

Confirm composition, basis, mass, spectrum units and normalization, group ordering, schedule, projectile, temperature, target coverage, and data applicability. Check [known evaluation defects](data-limits.md) and [validation evidence](validation.md) for the intended reactions and time range.

Retain the exact software version or commit, complete problem and result, original data and terms, generated libraries and indexes, upstream tally definitions and normalization, selected coefficient tables, and your applicability and uncertainty assessment. Apply the independent reviews and acceptance process required by the organization responsible for the analysis.
