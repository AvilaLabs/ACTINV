# Qualification boundary

ACTINV is research-grade calculation software. Its controls demonstrate that the recorded implementation reproduces
specified numerical, data-handling, and benchmark results within frozen bounds. They do not approve ACTINV—or any
calculation made with it—for licensing, safety, waste classification, or another regulatory purpose.

## Where ACTINV fits

ACTINV can be one version-controlled component in an organization's analysis chain:

1. A transport calculation or measurement supplies a particle-flux spectrum and its physical normalization.
2. The analyst selects evaluated activation, decay, fission-yield, photon-response, covariance, and radiological data.
3. ACTINV calculates inventory evolution and reports requested responses, provenance, coverage, and omissions.
4. Separate transport and consequence tools use ACTINV source terms where spatial or shielding effects matter.
5. The responsible organization applies its own review, uncertainty treatment, margins, configuration control, and
   regulatory acceptance process.

The run certificate hashes ACTINV's declared inputs. It helps demonstrate which inputs were used; it does not establish
that those inputs were correct or applicable.

## Responsibilities outside ACTINV

The analyst and qualifying organization remain responsible for:

- choosing the applicable jurisdiction, regulation, table edition, clearance or waste category, intake pathway,
  chemical form, aerosol class, age group, and any other scenario-specific coefficient selection;
- confirming material composition, mass, geometry, irradiation schedule, flux units, normalization, energy structure,
  temperature, and nuclear-data applicability;
- preserving third-party data licences, original files, SHA-256 hashes, generated-library identities, the ACTINV
  version/commit, the complete specification, and the result certificate;
- assessing uncertainty sources not covered by the selected MF=33 data and adding independently justified margins;
- establishing validation applicability to the material, reactions, time range, response, and operating regime in the
  intended use;
- independently reviewing consequential inputs and outputs and meeting the organization's software-quality and
  configuration-management procedures; and
- obtaining any required approval from the responsible authority.

ACTINV does not ship a default regulatory table. A radiological response is calculated only from an explicit,
hash-pinned user table. Missing coefficients and activity coverage are reported; `require_complete` can make missing
coverage a calculation error.

## Minimum calculation record

For a reviewable calculation, archive at least:

- the exact ACTINV release artifact or source commit and its SHA-256;
- the input specification and result, including the certificate and ledger;
- original evaluated-data archives, their terms, and their SHA-256 values;
- generated activation/covariance/response artifacts and their indices;
- transport or measurement inputs, tally definitions, normalizations, and spatial mapping;
- the selected radiological table with its citation, edition, scenario metadata, and hash;
- the applicable ACTINV validation evidence and a written applicability assessment; and
- independent checks, uncertainty additions, review records, and approvals required by the organization.

Re-running the same executable and inputs is useful but is not an independent verification of modeling assumptions.

## Model and validation limits

- ACTINV does not perform neutron or photon transport, criticality, thermal hydraulics, or geometry-dependent
  shielding. Its contact gamma dose is a semi-infinite-slab screening proxy, not a transported dose rate.
- Ordinary photon exporters use a point at the origin. Mesh calculations preserve cell indices and bounds, but users
  must construct any distributed transport source and verify its spatial interpretation.
- Mesh cells are independent; there is no material, temperature, or flux feedback between cells.
- Incident-particle support is neutron, proton, deuteron, and alpha. Triton, helion, and gamma activation are outside
  v1.0.
- Unresolved-resonance processing is infinite dilution. Finite-dilution self-shielding, probability tables, and
  Bondarenko treatment are not implemented.
- Products absent from configured decay data remain explicit leakage. ACTINV does not infer missing decay modes,
  fission yields, spectra, or radiological coefficients.
- The reported MF=33 band covers only retained cross-section covariance for selected responses. It excludes decay,
  fission-yield, flux, composition, response-coefficient, geometry, and model uncertainties; absent evaluated
  cross-covariance is reported rather than invented. The local propagation is a first-order result, not a safety
  margin.
- OpenMC, MCNP, and FISPACT importers accept only their documented subsets and require explicit normalization. Import
  controls do not qualify the originating transport model.
- FNS validation measures decay heat for its recorded materials and histories. The FNG/ITER control validates the
  supplied cell-620 activation history and selected nuclides, not full shutdown-dose transport or a licensing model.
- Recorded speed and memory measurements describe their fixtures and hardware; they are not performance guarantees.

## Release verdicts

Several phase verdicts are `CONDITIONAL` because the frozen, append-only record includes documented repair amendments.
That label preserves development history; it must not be rewritten as an unconditional qualification claim. Exact
protocols, amendments, compact evidence, and checker outputs are stored in `protocols/`, `results/`, and the phase
session records.

For scientific capabilities and numerical evidence, see [Validation](VALIDATION.md). For data identities and terms,
see [Data sources](DATA.md). For the v1.0 feature and limitation summary, see
[v1.0 release notes](RELEASE_NOTES_v1.0.md).
