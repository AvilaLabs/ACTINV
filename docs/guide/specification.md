# ACTINV problem specification (`actinv-spec-1`)

One JSON document drives the CLI, Python API and validation harness. Unknown fields, non-finite numbers and invalid
hashes are errors; literal paths use the CLI working directory (shell `~` expansion is not performed inside JSON).

Any `path` field or decay `primary`/`fallback` string may instead carry a symbolic reference
`catalog:<artifact-id>` naming an artifact in the embedded data catalog (`actinv data list` prints the IDs, e.g.
`catalog:tendl-2025-patched-neutron-709g`). The reference resolves to `<data-root>/v<catalog-version>/<artifact path>`,
where the data root is `$ACTINV_DATA_DIR` when set and `./actinv-data` otherwise. An omitted or null `sha256` is
filled from the catalog declaration; a declared hash that disagrees with the catalog is an error, as is a reference
to an artifact that is not installed. This keeps problem files portable between machines while remaining
hash-pinned — `actinv new` emits catalog references by default.

## Start from a complete example

Create a valid, editable problem with its full spectrum:

```bash
actinv new problem.json
actinv validate problem.json
```

[Your first calculation](quick-start.md) walks through installing data and solving it. This reference describes optional fields and advanced input formats. JSON fragments below belong inside a complete problem; they are not standalone runnable inputs.

## Required inputs

| field | meaning |
|---|---|
| `projectile` | `neutron`, `proton`, `deuteron`, `triton`, `helion` (He-3), `alpha` or `gamma`; omission preserves the historical neutron default. |
| `library.path` | ACTINV `.npz` activation library; the adjacent `<stem>_index.json` is also required. May be omitted only when every schedule step has zero flux (see Decay-only runs). |
| `library.sha256` | Optional declared hash. ACTINV always computes the library hash and fails if a declaration differs. The index's recorded library hash is checked too. |
| `decay.primary` | ENDF-6 radioactive-decay sublibrary. |
| `decay.fallback` | Optional second decay sublibrary; records absent from the primary are taken from it. |
| `material.composition` | Natural element symbols or explicit nuclides (`U235`, `Ba137m1`) and nonnegative values interpreted by `material.basis`. |
| `spectrum.flux_per_group` | Group-integrated fluxes. `descending: true` reverses the supplied order before use. |
| `schedule` | At least one duration plus exactly one of `flux` (multiplier), `wall_loading_MW_m2` or, with a `kerma` section, a power target (`power_W_cm3` / `power_W_per_g`; an explicit `flux: 0` beside a power target is accepted); optional `partition`, `feed`, `removal`, `spectrum`, `library`. Accepted duration units: seconds, minutes, hours, days and years. |
| `fission_yields` | Optional hash-pinned ENDF-6 neutron-induced fission-yield evaluations, and optional spontaneous-fission yields and neutron multiplicities; see below. Empty/omitted preserves the explicit no-yields leakage path. |
| `uncertainty` | Optional neutron-only MF=33 sidecar and response selection; omission reads no covariance file and preserves the ordinary path. |
| `radiological` | Optional hash-pinned clearance, waste, ingestion, or inhalation response table; omission reads no table. |
| `damage` | Optional hash-pinned `actinv-damage-table-1` damage-energy table plus per-element displacement energies; required when `outputs` contains `damage`. |
| `kerma` | Optional hash-pinned `actinv-kerma-table-1` heating table; adds per-step kerma heating and enables power-driven schedule steps (`power_W_cm3` / `power_W_per_g`, with `material.density_g_cm3` for the former). |

The certificate records computed SHA-256 values for the activation library, its index, primary/fallback decay data,
every fission-yield evaluation, the photon response, covariance sidecar, and radiological table when present. A
declaration is a constraint, not a value copied into the certificate.

## Material bases

`material.mass_g` defaults to 1 g. Inventories remain per gram; the mass scales the total photon rates and powers.
`material.density_g_cm3` is optional (finite and positive); only the [step summary](#step-summary)'s per-cm³ totals use it.
Composition keys are case-insensitive natural element symbols or explicit `SymbolA[mN]` nuclides. Bare `m` means
`m1`, so `BA137M`, `Ba137m` and `Ba137m1` identify the same state; aliases which collide are an error. A natural
element and one of its explicit isotopes cannot appear together. An unknown element symbol is an error, and so is a
natural element without tabulated natural isotopic abundances (Tc, Pm, Po, At, Rn, Fr, Ra, Ac): give those as
explicit nuclides, since a natural key would otherwise contribute no atoms. Explicit mass-based entries use the selected decay
evaluation's AWR times `1.00866491595 u` and fail if that record is absent. A literal `atoms_per_g` entry may instead
be ledgered as absent from the solvable chain; a photon-response calculation still requires its mass.

- `wt_percent` (default): each value is grams per 100 g. Values are used as stated rather than silently normalized;
  a total other than 100 is ledgered. Photon-response mixing normalizes them to mass fractions.
- `atom_fraction`: values are arbitrary elemental atom ratios. Natural isotopes are expanded and the mixture is
  normalized to one gram using the abundance-weighted elemental masses.
- `atoms_per_g`: each value is an elemental atom density per gram and is expanded by natural isotopic abundance.

All three bases apply identically to explicit nuclides: literal atom density for `atoms_per_g`, grams per 100 g for
`wt_percent`, and an arbitrary atom ratio normalized to one gram for `atom_fraction`. Response-function mixing
aggregates explicit isotopes back to elemental mass fractions.

## Projectile and spectrum

Neutrons use `fispact-709` with exactly 709 values, or any other FISPACT-II neutron structure listed under
[Group structures](#group-structures) with exactly that structure's group count. Proton, deuteron, triton, helion and alpha use
`fispact-162` with exactly 162 values and require `options.temperature_K: 0`; charged specs reject fission-yield files. `custom` requires one more
strictly increasing boundary than flux values. Those boundaries must match the activation library to `1e-12`
relative. `total`, when present, rescales group values while preserving shape; a positive `total` with an all-zero
`flux_per_group` has no shape to scale and is rejected. The spec, library index, group
structure and temperature must all identify the same projectile/data build before matrix assembly.

`spectrum.relative_error` is an optional array, the same length and order as `flux_per_group` (honouring
`descending`): each group's transport-tally statistical relative standard uncertainty. It is accepted and carried
but otherwise unused unless `uncertainty.channels` requests `"flux"` (see below); requesting that channel with no
`relative_error` given is an error naming the spectrum.

## Spectrum conversion (`spectrum.rebin`)

`spectrum.rebin` converts a spectrum supplied in its own groups onto a library structure inside the run
(FISPACT-II `GRPCONVERT`, `CNVTYPE` and `LIMGRP`). With it, `spectrum.structure` names the destination structure
(`fispact-709`, `fispact-162` or a refinement such as `fispact-709x4`; not `custom`), and `boundaries_eV`
(strictly ascending, one more than the flux values) plus `flux_per_group` describe the supplied groups.

| field | meaning | default |
|---|---|---|
| `convention` | `equal_lethargy` (`CNVTYPE 0`): flux is spread uniformly in lethargy inside a source group. `equal_energy` (`CNVTYPE 1`): uniformly in energy. | `equal_lethargy` |
| `emin_eV` | `LIMGRP` lower limit: flux in source groups below it is folded into the first group above it, whose lower edge becomes `emin_eV`. | none |
| `emax_eV` | `LIMGRP` upper limit: flux above it is folded into the last group below it, whose upper edge becomes `emax_eV`. | none |

`total` rescales the supplied spectrum before conversion. `relative_error` cannot accompany `rebin` (it is indexed by
the supplied groups). Flux falling outside the destination structure's range is not discarded silently: it is
reported. The ledger gains `spectrum_conversion` (one record per converted spectrum, the base spectrum first, then
any step spectrum) holding `convention`, `cnvtype`, `destination_structure`, `source_groups`, `destination_groups`,
`emin_eV`, `emax_eV`, `limit_flux_moved_from_below`, `limit_flux_moved_from_above`, `source_total`,
`destination_total`, `outside_destination_below`, `outside_destination_above`, `flux_conservation_residual` (the
relative closure of source against destination plus outside flux; checked to `1e-12`) and `exact_grid`. A spectrum
without `rebin` is unchanged and adds nothing to the ledger.

## Wall loading (`wall_loading_MW_m2`)

A schedule step gives exactly one of `flux` (a multiplier on the spectrum's total) and `wall_loading_MW_m2` (first
wall loading in MW m^-2, FISPACT-II `WALL`); giving both or neither is an error. FISPACT-II section 4.91 scales the
flux by `wall / wall_in_file`, the wall loading recorded with the spectrum. ACTINV follows that rule when the
spectrum declares `spectrum.wall_loading_MW_m2` (the loading its group fluxes correspond to): the step multiplier is
`wall / spectrum.wall_loading_MW_m2`. Without a reference, ACTINV uses the manual's closing statement of section
4.91, "a current C, of 4.44 x 10^13 n cm^-2 s^-1 is equivalent to 1 MW m^-2" (a 14 MeV neutron current), and sets
the step's total flux to `wall x 4.44e13` n cm^-2 s^-1 with the spectrum's shape; that needs a spectrum with a
positive total. The constant is `spec::WALL_CURRENT_PER_MW_M2`. A step spectrum uses its own reference. The ledger
gains `wall_loading`, one record per wall step: `step`, `wall_loading_MW_m2`, `basis`
(`spectrum_reference_wall_loading` or `14_MeV_current_equivalence`), `reference_wall_loading_MW_m2`,
`current_per_MW_m2_cm2_s`, `flux_multiplier` and `step_total_flux_cm2_s`.

## Partition (`schedule[].partition`)

`partition` is a map from element symbol to the fraction of that element's atoms **kept** at the start of the step
(FISPACT-II section 4.54 `PARTITION`: a value of 0.01 for Ar reduces Ar by a factor of 100). Every nuclide and
isomeric state of the element is scaled; the removed atoms move to the removal sink that `removal` also uses and are
reported as `removed_atoms_per_g`. Fractions must lie in [0, 1] and keys must be elements. The split is applied
before the step evolves and also acts on the uncertainty tangents and the comparison solution.

A partition removes bulk atoms, which the `trace` formulation cannot represent (it holds the bulk undepleted), so
`options.mode: "trace"` with a partition is an error, and `auto` selects `coupled`. The ledger gains `partition`:
`applied` (per step and element: `step`, `element`, `fraction_kept`, `removed_atoms_per_g`), `mode`,
`mode_forced_coupled` and `removed_atoms_per_g_final`. Partition is not accepted by mesh specs, study specs, reverse
solves or the waste-composition solve, and is refused together with `pathways` and schedule `library` overrides.

## Named data overrides (`overrides`)

`overrides` is an optional list replacing data for a named nuclide (FISPACT-II section 4.53 `OVER`). They are
applied while the reaction and decay networks are assembled, and each is listed in the ledger under `overrides`.

| `kind` | FISPACT-II | fields | effect |
|---|---|---|---|
| `cross_section` | `ACROSS` | `nuclide`, `mt`, `value` (barns, >= 0) | Sets the reaction's one-group collapsed cross section for the run's spectrum. Product branching is kept. |
| `cross_section_scale` | none (ACTINV) | `nuclide`, `mt`, `value` (> 0) | Multiplies the collapsed value. |
| `half_life` | `ALAM` | `nuclide`, `value`, `units` (default `s`) | Replaces the half-life; the units are any `schedule` duration unit. |
| `cross_section_uncertainty` | `ADCROSS` | `nuclide`, `mt`, `value` | Replaces the relative standard uncertainty of the reaction; needs the `cross_section_mf33` channel and a covariance. |
| `half_life_uncertainty` | `ADLAM` | `nuclide`, `value` | Replaces the decay constant's relative standard uncertainty; needs the `decay_constants` channel. |

Differences from the manual: FISPACT-II selects an `ACROSS` reaction by daughter, ACTINV by ENDF reaction number
`mt`; `ADCROSS`/`ADLAM` take a relative standard uncertainty, not an error factor. A cross-section rescale keeps the
correlations of the covariance row. A name that matches no library row or decay record is an error. Cross-section
value overrides cannot be combined with `uncertainty` (the MF=33 collapse is anchored to the library value), and an
absolute `cross_section` cannot be combined with `self_shielding`. Duplicate overrides, and a `cross_section` and a
`cross_section_scale` on the same reaction, are rejected. A cross-section override's ledger entry records the
one-group collapsed value before and after.

## Decay-only runs

A spec whose every step has zero flux needs no activation library: omit `library` (or leave `library.path` empty)
and `spectrum`. The decay data and the material are read as usual and the schedule is a pure decay/cooling
history. A spec that omits `library` with any irradiating step is rejected with a message naming the step rule.
Everything that needs a library is rejected for a decay-only spec: `uncertainty`, `self_shielding`, `damage`,
`fission_yields` files, `options.rate_scale`, `options.yield_scale`, `library.sha256` and cross-section overrides.
`half_life` and `half_life_uncertainty` overrides remain allowed. The ledger and certificate carry
`decay_only: true` and the library and library-index inputs are `null`. Runs that supply a library are unchanged.
Mesh specs still require a library.

## Fission yields

`fission_yields` is optional. Each file is one hash-pinned ENDF evaluation for one parent:

```json
"fission_yields": {
  "files": [
    {
      "path": "/data/endfb-viii.0-nfpy/nfy-092_U_235.endf",
      "sha256": "64 hexadecimal digits"
    }
  ],
  "energy": "fixed",
  "fixed_energy_eV": 0.0253
}
```

The production source is MF=8/MT=454 independent yield. MF=8/MT=459 cumulative tables are parsed and checked but
never used as matrix sources. Every independent table must sum to two fission fragments within `1e-6`; values are not
renormalized. Duplicate parents, energies or products, malformed/truncated records, negative/nonfinite values and hash
mismatches fail closed.

`energy: "fixed"` requires a finite nonnegative `fixed_energy_eV`; selection is exact, linearly interpolated, or
clamped to the evaluated range. `energy: "spectrum_average"` is the default and forbids `fixed_energy_eV`; it uses
the fission-rate-weighted representative incident energy separately for each parent. The certificate records the
requested energy, selected bracket, interpolation weight, clamp decision, product count and effective yield sum.
Fissioning parents without a matching file remain explicit leakage and never borrow another parent's evaluation.

### Spontaneous fission

Without further input, a decay state's spontaneous-fission branch (RTYP digit 6) is booked to leakage and counted
in the ledger's `spontaneous_fission_branches_to_leakage`. `fission_yields.spontaneous` routes it to products
instead, and supplies the neutron multiplicity for a spontaneous-fission neutron source. Every input is hash-pinned
and optional, but the object must declare at least one:

```json
"fission_yields": {
  "spontaneous": {
    "files": [{"path": "/data/sfy/spontaneous-yields.endf", "sha256": "64 hexadecimal digits"}],
    "nubar_endf": {"path": "/data/endfb-viii.0-decay/endf-b-viii-0_decay.dat", "sha256": "..."},
    "nubar_table": {"path": "nubar.json", "sha256": "..."}
  }
}
```

- `files` are ENDF-6 MF=8/MT=454 spontaneous-fission yield evaluations (the ENDF/B `sfy` sublibrary, same record
  layout as the neutron-induced files). A file may hold one material or the whole sublibrary; each material is one
  parent. Yields are read at the lowest tabulated incident energy, which is `0` for spontaneous fission, and the same
  checks apply (independent yields sum to two fragments within `1e-6`, no renormalization). A state with a
  spontaneous-fission branch and a yield evaluation sends `λ · BR_SF · yield` to each fragment, so one decay makes
  two product atoms and nothing goes to leakage. A fragment absent from the decay library is booked to leakage by name.
  A state with the branch and no evaluation keeps the branch on leakage and is named under
  `ledger.spontaneous_fission.uncovered_to_leakage`; no other parent's yields are borrowed. Only a branch that is the
  whole decay mode is routed.
- `nubar_endf` names an ENDF file whose MF=1 MT=452 (total, preferred) or MT=456 (prompt) section gives each state's
  spontaneous-fission neutron multiplicity, taken at `E = 0` (a polynomial's constant term, or the lowest tabulated
  point, recorded in the ledger). The ENDF/B-VIII.0 decay sublibrary carries these values (Cf-252: 3.7676). Do not name a
  neutron sublibrary: its MF=1 is the induced-fission multiplicity.
- `nubar_table` names a JSON document `{"source": "<citation>", "nubar": {"Cf252": 3.7676, ...}}`. The citation is
  required and recorded in the certificate; keys are explicit nuclide names. Its entries override `nubar_endf` for the
  same state. ACTINV builds in no multiplicity, and a state with an SF branch and no multiplicity is reported as
  uncovered, never estimated.

With any `nubar_*` input every step gains `fission.spontaneous_neutron_source` (see [Read your results](results.md)).

### Fission count and burn-up

`options.fission` (absent by default) adds a `fission.count` block to every step:

```json
"options": { "fission": { "energy_per_fission_MeV": 200.0 } }
```

`energy_per_fission_MeV` defaults to 200, a conventional round figure and not a nuclide-specific energy; it affects
only the MWd/kg burn-up and must be finite and positive. The option works without fission-yield files: every library
fission (MT=18) row is counted, whether or not yields exist for it. Both this option and
`fission_yields.spontaneous` are refused by the native waste composition solve. The count integrates one flux
multiplier per step, so `options.fission` is refused with power-driven steps (whose multiplier is solved per
sub-interval) and with schedule `library` overrides (the count reads the base library's MT=18 rates).
`fission_yields.spontaneous` reads decay data only and combines with both.

## MF=33 uncertainty

`uncertainty` is optional and neutron-only. `uncertainty.covariance` names an `actinv-covariance-1` sidecar
(`path` and a mandatory `sha256`); the adjacent `<stem>_index.json` must link the exact activation-library/index
hashes, neutron projectile, group-boundary hash and every target/source identity. ACTINV recomputes and records
both sidecar and index hashes before matrix assembly. `covariance` may be omitted only when `channels` is exactly
`["flux"]` (flux-only mode, below); any other `channels` value still requires it, and when present MF=33 is
propagated exactly as always regardless of what else `channels` requests.

`responses` accepts the canonical selectors `heat.total`, `heat.alpha`, `heat.beta`, `heat.gamma`,
`activity:Nuclide`, `activity:*`, and `activity.total` (the aggregate propagated directly through the
full covariance — not a root-sum-square combination of per-nuclide bands). Selectors must be unique.
An empty or omitted list selects all four heat
components plus every activity reported at that step. `confidence_level` defaults to `0.95` and must be strictly
between zero and one. `require_complete` defaults to `false`; when true, an active activation row without a valid
MF=33 self-covariance — or a nonzero-sensitivity parameter in any requested channel without uncertainty data —
fails rather than returning a partial band.

`channels` is optional and accepts `"cross_section_mf33"` (the implicit default), `"decay_constants"`,
`"fission_yields"` and `"flux"`. When omitted, only the MF=33 cross-section channel is evaluated and the channel
fields are absent from the output. `decay_constants` propagates
each radioactive chain member's decay-constant uncertainty `sigma_lambda = lambda * dT_half/T_half` read from the
pinned decay file's MF=8/MT=457 record. `fission_yields` propagates each populated fission edge's independent-yield
`DY` read from the pinned MF=8/MT=454 file at the requested yield energy. Both channels are diagonal — the
evaluations carry no correlation data — and each reports its own sensitivity list, standard uncertainty and
coverage. A nuclide or product with no declared uncertainty is named in `uncovered_decay_constants` /
`uncovered_yield_products`.

The `flux` channel (1.4.0) propagates each input group's transport-tally statistical error (`spectrum.relative_error`, or a mesh
cell's own flux-file `relative_error` — a mesh cell without one is an error naming that cell) as a first-order,
diagonal (uncorrelated group-to-group) channel: the parameter is the log of that group's absolute flux after
`total` scaling, its direction is the reaction-only burn matrix a unit flux confined to that group alone would
produce, and its sensitivity is exactly the response's own sensitivity to that group's flux level. Each
`flux_sensitivities` parameter reports its `group` in the order the spec declared `flux_per_group` (undoing
`descending` for that label only — energy bounds, flux and standard uncertainty are unaffected, since they name
the same physical group either way). The channel's
variance is `sum((s_g * e_g)^2)` over groups with a positive sensitivity and a given error; a fully-correlated
alternative bound, `sum(|s_g| * e_g)`, is reported alongside it (`flux_fully_correlated_bound`) for a worst-case,
correlated-error comparison. `flux` needs no covariance sidecar by itself: `channels: ["flux"]` alone (`covariance`
omitted) is
*flux-only mode* — MF=33 is not propagated and the band is the flux channel alone, with the method and band name
naming that. Everything else the flux collapse depends on (self-shielding row scale, `rate_scale`, fission-yield
selection, mode and pruning choices) is held at the nominal run's values. The flux channel is diagonal, needs no
covariance and is not a nuclear-data parameter, so it is excluded from `voi`, `isomer` and `design` (below): those
report ranks over parameters an experiment could better-measure, and a transport tally's statistical error only
shrinks by running more particle histories.

`voi` is optional (`{"top": N}`, 1–256) and emits a value-of-information table inside each requested response: the
`top` parameters ranked by `|variance_share|` — the share of the propagated variance each parameter carries
(`s_i·(Σ·s)_i` over the MF=33 block, `(s_i·σ_i)²` for the diagonal decay/yield channels — negative shares are
emitted, not hidden, under anticorrelation) — plus the total propagated variance the band was built on and an
`unranked` summary naming sensitivity-bearing parameters with no covariance coverage. The table answers "which
measurement most buys down this band": removing a parameter's uncertainty drops the variance by its share.

Each requested response reports its nominal value, local sensitivity to every active collapsed row in response units
per barn, MF=33 standard uncertainty, relative standard uncertainty when defined, the requested two-sided normal
interval, an alternate-CRAM-order difference, and a conservative interval expanded by that numerical-method bound.
When extra channels are requested the record adds per-channel sensitivity lists, per-channel standard uncertainties
and a `combined_standard_uncertainty` equal to the root-sum-of-squares across channels, plus a `channels` report
naming each channel's coverage. Coverage is `complete` only when every nonzero-sensitivity parameter in every
requested channel has evaluated uncertainty data. Missing evaluated cross-reaction terms contribute zero and are
counted; they are not invented. These intervals are neither tolerance limits nor safety margins, and exclude
MF=32 resonance-parameter and MF=40 production covariance, decay-yield and cross-channel correlation,
material-composition, response-coefficient and model uncertainty — the `uncovered_remainder` channel names these.
Absent a requested `flux` channel, incident-flux uncertainty is excluded there too; with `flux` requested, that
entry instead names the narrower remainder the channel does not cover (systematic flux uncertainty from the
transport model, geometry and transport nuclear data — the channel covers only the tally's own statistical error).

### Additional uncertainty reporting

| Field | Meaning |
| --- | --- |
| `isomer` | Optional object with `top` (1–256), reporting isomer-resolved variance and pathway partitions |
| `design` | Optional object with `top` (1–256), ranking variance removed by a perfect measurement using the covariance model |
| `unmodeled_relative` | Declared finite nonnegative relative term; adds `(u * nominal)^2` to variance |
| `unmodeled_table` | Hash-pinned `actinv-unmodeled-table-1` file, optional material-family `key`, and nonnegative `fallback` |
| `unmodeled_evalspread` | Hash-pinned `actinv-eval-spread-1` artifact supplying its suggested relative term |

Only one of the three `unmodeled_*` sources may be declared. A relative error term cannot cover a zero or missing-channel prediction. It supplements the declared band; it does not establish coverage of all excluded uncertainty sources. `isomer.top` and `design.top` default to `voi.top` when set, otherwise 20.

## Radiological responses

`radiological` is optional. Its table is strict JSON with format `actinv-radiological-table-1`; the declared SHA-256
is mandatory and is recomputed before the calculation. ACTINV ships no default table and makes no jurisdiction or
scenario selection. A minimal table is:

```json
{
  "format": "actinv-radiological-table-1",
  "title": "Example only",
  "source": {
    "citation": "Issuing authority and publication",
    "edition": "2026",
    "url": "https://example.invalid/source",
    "jurisdiction": "example"
  },
  "responses": [
    {
      "id": "clearance-2026",
      "kind": "clearance_index",
      "basis": "Describe the applicable material and scenario",
      "coefficients": {"Co60": 100.0, "Mn56": 1000.0}
    }
  ]
}
```

Response IDs and canonical nuclide keys must be unique. `kind` is `clearance_index`, `waste_index`,
`ingestion_dose`, `inhalation_dose`, `a2`, or `activity_limit`. Clearance/waste coefficients are limits in Bq/kg;
ingestion/inhalation coefficients are in Sv/Bq; `a2` coefficients are A2 transport values in Bq (the FISPACT-II `ATWO`
index `sum(A_i / A2_i)`, reported per gram of material); `activity_limit` coefficients are any other absolute activity
limit in Bq (A1, an exempt consignment or practice total), giving `sum(A_i / limit_i)` per gram of material. A2 values
are bundled (below), or supply your own table, converting IAEA TBq values to Bq. Every coefficient must be finite and positive. An empty `responses` selector chooses every
table response in table order; otherwise only the named unique IDs are evaluated.

Each step reports the selected value, unit, covered and missing activity, activity-coverage fraction, contributing
nuclide count, and sorted active nuclides without a coefficient. Missing coefficients are not treated as zero.
`require_complete: true` rejects the entire calculation when any selected response lacks a coefficient for positive
activity. The certificate retains the table hash, source metadata, kind, basis, and coefficient count. See the
[qualification boundary](qualification.md) before using a regulatory table.

A table's `source` may also carry the optional strings `scope`, `retrieved`, `source_file`, `source_sha256`,
`converter`, `licence`, and `row_provenance`. When present they are nonempty and are kept in the certificate.

### Bundled regulatory tables

ACTINV ships a few regulatory tables inside the program, converted from public sources by scripts that are kept in
the repository. They are opt-in: set `radiological.table.path` to `bundled:NAME`. Nothing is selected for you, there
is no default jurisdiction, and an unknown name is an error that lists the available names.

```json
"radiological": {
  "table": {"path": "bundled:us-49cfr173-a2"},
  "responses": ["a2-u-medium"]
}
```

`actinv` fills a missing `sha256` from the bundled table and rejects a different one. Other front ends must declare
the 64-hex-digit SHA-256 of the table, which is recorded in the result like any other table.

| Name | Source and scope | Coefficient | Licence |
| --- | --- | --- | --- |
| `us-49cfr173-a1`, `us-49cfr173-a2` | 49 CFR 173.435 (eCFR, 2026-10-13), the IAEA SSR-6 Rev.1 (2018) Table 2 A1 and A2 values. United States transport of radioactive material. | A1 or A2 in Bq (from TBq) | US Government work, public domain |
| `us-49cfr173-exempt-concentration` | 49 CFR 173.436, exempt material activity concentration. Transport. | Bq/kg (from Bq/g) | public domain |
| `us-49cfr173-exempt-consignment` | 49 CFR 173.436, exempt consignment activity. Transport. | activity in Bq | public domain |
| `euratom-2013-59-annex-vii-a` | Council Directive 2013/59/Euratom, Annex VII Table A Part 1: exemption and clearance of solid material, artificial radionuclides. European Union (national law may differ). | Bq/kg (from kBq/kg) | EU reuse with attribution |
| `euratom-2013-59-annex-vii-b-concentration` | Annex VII Table B column 2, exemption of moderate amounts. | Bq/kg (from kBq/kg) | EU reuse with attribution |
| `euratom-2013-59-annex-vii-b-activity` | Annex VII Table B column 3, total activity for exemption. | activity in Bq | EU reuse with attribution |

Each table's `source` block names the title, edition, URL, retrieval date, SHA-256 of the source file, converter script
(`scripts/regulatory/`), and licence. The converters read the downloaded file and type no numbers. Tables and the
per-row sidecars are under `crates/actinv-core/data/regulatory/`; a sidecar (`*.rows.json`) holds the original row text,
footnote markers, flags, and the reason any source row is not in a table. ACTINV does not read the sidecars.

How to read the tables:

- **Limits in Bq, not Bq/kg.** The A1, A2, and total-activity tables hold an activity in Bq, with response kind `a2`
  (A2) or `activity_limit` (A1, exempt consignment, Directive Table B column 3). The index is the number of limits per
  gram of material; multiply by the mass in grams to compare a consignment or inventory.
- **Uranium.** The transport tables list uranium by lung-absorption type. Each carries three responses that differ only
  in the uranium rows: `...-u-fast`, `...-u-medium`, `...-u-slow`. An empty `responses` list returns all three; choose
  one deliberately.
- **Unlimited and element-level rows.** Rows with no finite limit ("Unlimited"), and element-level rows such as
  `U (nat)` or `Rb(nat)`, cannot be coefficients. They are in the sidecar. ACTINV reports their activity as uncovered
  and never as zero, so `require_complete: true` rejects a run that contains them.
- **Parents with progeny.** Rows marked as including progeny (footnote (a) in 173.435, (b) in 173.436, (1) or (2) in the
  Directive) keep the parent's value. The sidecar lists the progeny when the source does. If your inventory contains the
  progeny too, they are counted again; remove them from the inventory or accept the conservative result.
- **Source anomalies** are recorded rather than corrected: the Directive's Table B lists U-240 twice (the nuclide-alone
  row is used), and its parent/progeny marks disagree with its progeny lists for a few nuclides.
- **Isomer labels.** Three CFR rows use lifetimes (`Eu-150`, `Np-236`, `Ta-178`). They are mapped to ACTINV isomer
  states by the decay half-lives in ENDF/B-VIII.0, and flagged in the sidecar.

These tables carry the regulation's numbers only. Whether a table applies to your material, jurisdiction, and use is
your decision.

### Building the ICRP 119 dose-coefficient table locally

ICRP Publication 119 (the carrier of the ICRP 72 public ingestion and inhalation coefficients) is copyrighted, so
ACTINV does not ship its values. If you have a copy of the PDF, generate a table for your own use:

```text
python3 -I scripts/regulatory/fetch_icrp119.py --pdf P119.pdf --out icrp119.json --fispact-convention
```

This needs `pdftotext` (poppler). The script reads Annex F (ingestion, adult) and Annex G (inhalation, 1 um AMAD,
adult, types F/M/S) and writes one response per variant (`ingestion-adult-variant1`, `inhalation-adult-F`, ...) next
to an `icrp119.variants.json` that records every choice. With `--fispact-convention` it adds
`ingestion-adult-fispact-max` and `inhalation-adult-fispact-max`, the maximum over the f1 variants and over F/M/S,
which is the convention found in FISPACT-II's hazards file for its ICRP 72 rows. Keep the output out of version
control and do not redistribute it. `--download-url URL --yes-download` fetches the PDF first, but only from a URL you
confirm.

`scripts/regulatory/compare_fispact.py` compares a table with the clearance, A2, or hazards files of a FISPACT-II
2012 data bundle that you hold. It is a QA report, not a feature; the FISPACT files are never copied into ACTINV.

## Damage observables (dpa)

`damage` is optional and is required when `options.outputs` contains `"damage"`. Its table is strict JSON with
format `actinv-damage-table-1`; the declared SHA-256 is mandatory and is recomputed before the calculation. The
table's `projectile` must match the problem projectile, and its `boundaries_eV` must be the activation library's
boundaries exactly — `actinv build-damage` produces tables collapsed onto the same group structure.

A table row is a per-group damage-energy production cross section in barn·eV, keyed by a canonical explicit nuclide
(`Fe56`, `Ta180m1`) or a canonical element symbol (`Fe`); an element row covers every material nuclide of that
element without its own row. Rows must be nonnegative, finite, and exactly `boundaries - 1` in length. Target keys
are validated against the canonical naming rules — `fe56` is an error, not a synonym.

`displacement_energy_eV` maps canonical element symbols to positive displacement energies; every covered element
must have one, and a nuclide key there is an error. `require_complete: true` rejects the run when any material
composition nuclide lacks a row. Uncovered nuclides are otherwise named in the ledger's
`damage.uncovered_targets` and reduce `covered_atom_fraction`; they are never treated as zero data.

Each step reports `damage`: total `damage_energy_eV_per_g_s`, material `dpa_rate_per_s`, cumulative `dpa`, the
covered-atom fraction, and a per-element block of `atoms_per_g`, `damage_energy_eV_per_g_s`, `dpa_rate_per_s`,
`dpa`. The displacement model is NRT: `dpa_rate = 0.8 * damage_energy_per_atom_per_s / (2 * E_d)`; the material
rate is the covered-atom-weighted mean of the element rates. Damage targets are the material's
composition-resolved nuclides — transmutation products are not counted, and in coupled mode the evolved target
inventories are used. The ledger records the table hash, covered elements, displacement energies, uncovered
targets, model, and units; the certificate records the table's provenance and the computed input hash.

Damage-energy production comes from ENDF-6 MF=3/MT=444 sections. TENDL-2025 and EAF-2010 as distributed do not
carry MT=444; build tables from `heatr`-processed or equivalent damage-energy evaluations.

## Kerma heating and power-driven steps

`kerma` is optional. It adds an instantaneous nuclear-heating output to every step and lets a schedule step name a
heating power instead of a flux, as FISPACT-II's `POWER` keyword does. Without a `kerma` section, and without a
step that names a power, the problem, its result and its ledger are byte-for-byte what they were before.

```json
"material": {"composition": {"Fe": 100.0}, "density_g_cm3": 7.874},
"kerma": {
  "table": {"path": "/data/kerma/fe_kerma.json", "sha256": "64 hexadecimal digits"},
  "power_tolerance": 0.001,
  "max_substeps": 64,
  "top": 10
},
"schedule": [
  {"dt": "1 y", "power_W_cm3": 150.0},
  {"dt": "10 y", "flux": 0}
]
```

`kerma.table` is a hash-pinned `actinv-kerma-table-1` file; the declared SHA-256 is recomputed before the run.
`require_complete: true` rejects the run when a material nuclide has no heating row (otherwise such nuclides add no
heating and are named in the ledger's `kerma.uncovered_composition_targets`). `power_tolerance` (default `0.001`,
open interval (0, 1)), `max_substeps` (default 64, 1 to 4096) and `top` (default 10, the number of contributors listed
per step) are described below.

**Table format.** Strict JSON; unknown fields are an error.

| field | meaning |
|---|---|
| `format` | `actinv-kerma-table-1`. |
| `source` | `citation`, `edition`, `url`, all nonempty. |
| `projectile`, `group_structure`, `boundaries_eV` | The projectile must match the problem; `boundaries_eV` must equal the activation library's boundaries exactly. |
| `units` | `kerma_barn_eV_per_group`. |
| `targets` | Canonical explicit nuclide (`Fe56`, `Ta180m1`) or element (`Fe`) keys, each with one finite barn·eV value per group. A nuclide row wins over its element's row. |
| `damage_energy_targets` | Optional, same keys; nonnegative barn·eV damage-energy production. |
| `temperature_K`, `files`, `uncovered` | Optional provenance: the table's temperature, per-file SHA-256s, and the evaluations that had no kerma section. |

A `targets` row is the group average of NJOY HEATR's total heating kerma, ENDF MT=301, in eV·barn per atom; the
optional `damage_energy_targets` rows are the group-averaged total damage-energy production, MT=444 (NJOY2016
`heatr.f90` header: MT=301 total kerma, 444 total damage energy). An atom of nuclide `i` in group fluxes `phi_g`
(particles cm⁻² s⁻¹) deposits `k_i = sum_g K_ig * phi_g * 1e-24 * 1.602176634e-19` watts. Whether the photon energy
of a capture is counted as heating depends on HEATR's `local` flag when the table was made (default 0: gamma rays
are transported and their energy is not deposited at the reaction site); ACTINV reads the table as given and cannot
see that choice, so state it in the table's `source.edition`. A negative group value (energy-balance kerma can go
negative where an evaluation's photon-production data are inconsistent) is accepted and named in the certificate's
`targets_with_negative_group_values`; a negative damage-energy value is an error. A table is built from NJOY output
with `scripts/build_kerma_table.py` (below).

**Power-driven steps.** A step names `power_W_cm3` or `power_W_per_g` instead of `flux`; the three are mutually
exclusive (`flux: 0` beside a power target is accepted because that is what a result echo carries; any other flux
with a power target is an error). A power must be finite and positive, so cooling stays `flux: 0`. `power_W_cm3`
needs `material.density_g_cm3` (finite, positive; otherwise unused) and is converted to W per gram with it; the
gram-based inventories need no density. A power-driven step requires a `kerma` section, uses the step's own
`spectrum` when it has one, and may combine with `feed`/`removal`. It is refused with `uncertainty`,
`options.screen`, `options.fission`, `pathways`, schedule `library` overrides, the reverse and study tools and mesh
specs, and its pathway attribution is not emitted. It does combine with `partition`.

The flux multiplier on the step's spectrum is chosen so that the instantaneous heating
`sum_i N_i(t) * k_i(spectrum) * multiplier` equals the requested power, with `N_i` the evolving atoms per gram of
every nuclide that has a kerma row (transmutation products with a row contribute). FISPACT-II fixes the flux once
per `POWER` card from the inventory at the start of the interval, and the user is expected to break a long
irradiation into sub-steps by hand. ACTINV does that automatically: the step is split into equal sub-intervals, the
multiplier is set at the start of each from the composition then reached, and every sub-interval's end-of-interval
power is compared with the request. If the largest relative departure (the *power drift*) exceeds `power_tolerance`
the number of sub-intervals doubles (up to `max_substeps`) and the step is solved again from its start. A cap of 1
reproduces FISPACT-II's start-of-step normalization. The achieved multipliers, the total flux in each sub-interval
and the worst drift are reported (see [results](results.md#kerma-heating-and-power-driven-steps)); a step that hits
`max_substeps` above the tolerance is reported with `converged: false` and listed in the ledger, not hidden. The
drift measures the interval end only, which bounds the interior when power varies monotonically across a
sub-interval.

Rate pruning and the automatic burnup-mode choice are decided before the schedule is solved, so for a power-driven
step they use the flux amplitude implied by the starting composition (reported per step as
`estimated_flux_multiplier`). If the achieved multipliers differ greatly from it, set `options.prune` and
`options.mode` explicitly.

**Kerma output.** With a `kerma` section every step reports its instantaneous heating at the step's end. Flux-driven
steps use their `flux` multiplier; a power step uses its final sub-interval's. See the results guide for the fields.

## Build a kerma table

```bash
python3 scripts/build_kerma_table.py OUTPUT.json --pendf fe56.pendf \
  --boundaries-from damage_table.json --group-structure fispact-709 \
  --temperature-K 293.6 --damage-energy
python3 scripts/build_kerma_table.py OUTPUT.json --gendf fe56.gendf --group-structure my-groups
```

The script reads ENDF-6 tapes written by NJOY2016 and runs no NJOY. `--pendf` takes a HEATR-processed PENDF: its
pointwise MF=3/MT=301 (and with `--damage-energy` MT=444) section is collapsed onto the boundaries of
`--boundaries-from` (any JSON with a `boundaries_eV` list) with a flat-in-lethargy weight, the weighting
`actinv build-damage` uses; linear-linear sections only, and zero outside the section's energy range. `--gendf` takes
GROUPR output, whose own group averages and `EGN` boundaries are used; GROUPR must have been run with `lord=0`,
`nsigz=1` and one temperature per tape, and a GENDF names every material as a ground state because it carries no
isomer designation. A material without MT=301 is listed under `uncovered`, never zero-filled. Several tapes may be
combined into one table; two materials mapping to the same target are an error. The output file's SHA-256 is printed
for pinning into `kerma.table`.

## Self-shielding

`self_shielding` is optional; omission preserves the ordinary unshielded path byte-for-byte. Its `table` names a
hash-pinned `actinv-shield-table-1` artifact — the declared SHA-256 is recomputed before the calculation and a
mismatch is an error. Build tables with `actinv build-shielding EVAL_DIR OUT.json`; the table's group boundaries
must equal the activation library's exactly. Instead of a `table`, a `geometry` section selects the
resolved-resonance sigmoid-curve model described below; exactly one of the two is required.

`dilution` selects the background dilution each covered nuclide sees: `"composition"` derives
`sigma0_i = sum_j(n_j * sigma_p,j) / n_i` from the declared material (table potential cross sections where the
nuclide is covered, an analytic channel-radius estimate elsewhere, named as estimates in the ledger);
`"fixed"` applies the validated positive `sigma0_b` to every covered nuclide. Factors interpolate in
`ln(sigma0) x sqrt(T)` and clamp at the grid ends.

Each covered group applies a full-group Bondarenko fold, not a flat lethargy blend: the unresolved-range segment
carries its probability-table weight mean `w = sigma0/(sigma0+sigma_t)` and weighted moment `sigma_x*w`, and the
uncovered part is suppressed by `sigma0/(sigma0+sigma_t,background)` over the smooth MF=3 background. The emitted
`group_factors` are the applied scale; `factors` remains the covered-segment factor for reporting, and tables
without `group_factors` fall back to the flat `(1-c)+c*f` blend.

Material nuclides absent from the table are named `shielding_uncovered` and their rates are untouched;
`require_shielding_complete: true` fails the run instead. The section rejects any sha256 or boundary mismatch.
The ledger and certificate record the table hash, dilution mode, effective sigma0 per nuclide, applied factors,
and method limits — including that resolved-region pointwise shielding is not applied and damage observables
are not shielded.

When `uncertainty` is also present, the MF=33 collapse weights each row's spectrum integral by that row's
shield factors (the `collapse_weighted` convention: `sigma_i = sum_g phi_g * f_i,g * sigma_i,g / sum_g phi_g`),
so the propagated parameters are the same shielded one-group cross sections the depletion matrix uses; the
run checks the collapsed nominals against the shielded fold bitwise. The same fold applies on the
`study.robustness` MF=33 sampling path.

### Monte Carlo sampling statistics

The `study.robustness` sampling path is the equivalent of FISPACT-II `SENSITIVITY`/`MCSAMPLE`. By default
it reports each response's sample mean, std and sampling error. Declare `robustness.statistics` (an object,
`{}` for the defaults) to add a `sampling_statistics` block per case:

```json
"robustness": {
  "samples": 1000, "seed": 17, "responses": ["total_activity_bq_per_g"],
  "channels": {"cross_section_mf33": true, "flux_rel_std": 0.05},
  "covariance": {"path": "cov.npz", "sha256": "..."},
  "statistics": {"percentiles": [2.5, 16, 50, 84, 97.5], "histogram_bins": 20,
                 "correlation_top": 10, "bootstrap_resamples": 200}
}
```

Per response and cooling time it gives the distribution (`n`, `mean`, `std`, `skewness`, `min`, `max`,
`percentiles`, optional `histogram`), a bootstrap `convergence` block (standard error of the std and of every
requested percentile), the linear `first_order` std for the same response when the run computed it (with the
ratio and the gap in bootstrap standard errors), and `input_correlations`: the Pearson and Spearman
correlation of the response with each sampled input parameter, ranked by `|spearman|` and cut to
`correlation_top`, each parameter named by channel and target nuclide, MT, decay-constant or yield edge. A
case-level `response_correlation` matrix gives the Pearson correlation between all requested responses.

Percentiles are exact order statistics (nearest rank): the smallest sample value `x_(k)` with
`k/n >= p/100`, `k = max(1, ceil(n p/100))`; there is no interpolation. Spearman uses average ranks for ties.
The sampled input is the natural log of each applied multiplicative factor. Without `statistics` the
robustness record is unchanged. See [the study reference](https://github.com/AvilaLabs/ACTINV/blob/master/docs/STUDY.md)
for every field.

A runnable walkthrough lives at `examples/shielding_demo.json`: pure W-186 under a 4–25 keV custom spectrum at
fixed `sigma0_b = 0.1` — the ledger names every applied factor and W-187 activity lands ~28% below the
unshielded solve of the same problem.

### Dilution overrides

`self_shielding.dilution_overrides` (table mode only) replaces the computed background dilution for chosen
nuclides and groups, the equivalent of FISPACT-II `SSFDILUTION`:

```json
"dilution_overrides": {"W182": {"300": 100.0, "301": 80.0}, "W184": {"194": 2.5}}
```

Keys are canonical nuclide names; inner keys are library group indices (0-based, ascending energy, the same
indices as `applied_factors`); values are positive finite dilution cross sections in barns. For a named group the
supplied `sigma0` is used in place of the nuclide's `"composition"` or `"fixed"` value; every other group keeps
the computed one. A nuclide the table does not cover, or a group the table does not carry for that nuclide, is an
error. The ledger and certificate list the applied overrides under `dilution_overrides`; the reported
`sigma0_eff_b` remains the computed value.

### Geometry self-shielding (resolved resonances)

`self_shielding.geometry` selects FISPACT-II's `SSFGEOMETRY` model instead of the Bondarenko table: the
"universal sigmoid curve" of Martinho, Goncalves and Salgado. It is exclusive with `table`, `dilution`, `sigma0_b`
and `dilution_overrides`, as `SSFGEOMETRY` and `PROBTABLE` are exclusive.

```json
"self_shielding": {
  "geometry": {
    "shape": "foil",
    "length_cm": 0.01,
    "density_g_cm3": 19.3,
    "resonance_files": [{"path": "n_W184.endf", "sha256": "..."}],
    "choose": ["W"],
    "reactions": "all"
  }
}
```

- `shape` is `"foil"` (`length_cm` = thickness), `"wire"` or `"sphere"` (`length_cm` = radius), or `"cylinder"`
  (`length_cm` = radius plus `height_cm`). Dimensions must be finite and positive; `height_cm` is required for,
  and only for, a cylinder. The effective length is `y = 1.5 t` (foil), `2 r` (wire), `r` (sphere),
  `1.65 r h / (r + h)` (cylinder).
- `density_g_cm3` converts the declared material (atoms per gram) to number densities. FISPACT-II instead takes
  atoms per unit area or length from `SSFFUEL`/`SSFMASS`; ACTINV uses the run's own material and a density.
- `resonance_files` are hash-pinned ENDF-6 neutron evaluations, one nuclide each, carrying MF=2/MT=151 resolved
  resonance parameters (Breit-Wigner or Reich-Moore; R-matrix-limited ranges are rejected). An empty list, an
  evaluation without MF=2, or one without a resolved range is an error.
- `choose` restricts the correction to listed nuclides or elements (`SSFCHOOSE`); every entry must be in the
  material and supplied by a resonance file. Without it every material nuclide that a file supplies is shielded;
  other material nuclides are named in `shielding_uncovered` (an error under `require_shielding_complete`).
- `reactions` is `"all"` (default; FISPACT-II applies the factor to every reaction of the shielded nuclide) or
  `"capture"` (MT 102 only).

For each resolved resonance `i` of a shielded nuclide, `z_i = N * sigma_peak,i * y * sqrt(Gamma_gamma/Gamma)`, with
`N` the nuclide number density, `sigma_peak,i = 4 pi/k^2 * g * Gamma_n/Gamma` the Breit-Wigner peak cross section
and `g = (2J+1)/(2(2I+1))`. The resonance factor is `G(z) = (A1 - A2)/(1 + (z/z0)^p) + A2` with `A1 = 1.000`,
`A2 = 0.060`, `z0 = 2.70`, `p = 0.82` (FISPACT-II manual Eqs. 43-48). The factor applied in a library group is the
mean of `G` over the resonances peaking in that group, weighted by `w_i = (Gamma_gamma/E^2) * g Gamma_n/Gamma`
(Eqs. 49-50); groups without resonance peaks are untouched. The scaled cross sections feed the same per-group fold
as the Bondarenko path, including the MF=33 collapse.

The ledger `shielding.geometry` block and the certificate record, per shielded nuclide, the number density and the
applied factor for each group with its energy range and resonance count, the effective length, and the resonance
file hashes (`resonance_files_sha256`, certificate `inputs.shielding_resonance_files`). Limits: the peak cross
section is the single-level Breit-Wigner value (FISPACT-II cites Froehner, JEFF Report 18, for it without
reproducing the formula); only the shielded nuclide's own peak enters `Sigma_tot`, the other mixture components are
neglected (the model assumes non-overlapping resonances); the unresolved range, damage and temperature dependence
are not treated; and the sigmoid fit is an approximation to Monte Carlo results for simple shapes, not a
transport calculation.

## Gas production (H and He isotopes)

This option was added in 1.4.0.

`options.gas: true` (default `false`) tracks the light charged-particle products of neutron activation — H1, H2,
H3, He3 and He4 — as real inventory nuclides, exactly as FISPACT-II does. Every neutron reaction's light-particle
multiplicities (protons, deuterons, tritons, He-3, alphas) are read from a table built from the ENDF-6 reaction
definitions for MT 11–45 (excluding the 18–21 and 38 fission MTs), 102–117 and 152–200; MT 4 and 51–91 (inelastic)
and MT 102 emit none. A product row whose MT is not in the table (MT 18, fission, is the practical case: ternary
gas is not modelled) contributes no ejectiles and is named in the ledger's `gas.uncovered`, keyed by MT, with its
share of the reaction rate. Each decaying nuclide's own modes also feed the gas states directly: branching × λ
into He4 for every RTYP digit 4 (alpha) and into H1 for every digit 7 (proton).

Because the five gas states are real chain nuclides, they decay and react further like any other state — H3
decays to He3 at its own tabulated half-life, and a secondary reaction such as He3(n,p)H3 applies when the
library carries it — and they appear in the ordinary `inventory`, `activity_Bq_per_g` and `heat_W_per_g` output,
not only in the block below. If a light nuclide is absent from the decay library, a stable sink stands in for it
and the ledger's `gas.missing_light_states` names it. Trace mode feeds the gas states from bulk material targets
through the unit-source mechanism exactly as it feeds any other product, including the hybrid reservoir treatment
when H or He is itself a bulk material constituent; the gas edges are ordinary edges in the graph pruning already
operates on, so `options.prune` needs no gas-specific handling.

`options.gas` is refused together with `uncertainty`, and for any `projectile` other than `neutron` (v1). With
gas off, every byte of `PreparedRun`, the result and the ledger is unchanged from a pre-P92 run; `gas` is absent
from the spec echo and fingerprint entirely rather than serialized as `false`.

Each step gains a `gas` block when enabled: per species (`H1`, `H2`, `H3`, `He3`, `He4`) it gives `atoms_per_g`
(the same quantity the main inventory reports for that nuclide), `produced_atoms_per_g` (`atoms_per_g` minus the
material's initial population of that nuclide, ordinarily zero) and `appm` (`produced_atoms_per_g` per 1e6 atoms
of the material's total initial population). It also gives `inventory_appm` (`atoms_per_g` per 1e6 initial
atoms, the initial content included). It also gives `H_appm` (H1+H2+H3), `He_appm` (He3+He4), their inventory
counterparts `H_inventory_appm` and `He_inventory_appm`, and `initial_atoms_per_g`, the appm normalization
denominator. The two conventions differ only for a light nuclide the material starts with, e.g. hydrogen in
a hydrocarbon. FISPACT-II's printed `APPM OF` is the inventory convention (P95); compare against
`inventory_appm`, not `appm`. These fields are plain `f64` inventory populations and dimensionless ratios computed at
result serialization, not raw inputs converted at a `Spec::physical_inputs` boundary, so they stay
outside the P16 typed-quantity inventory (`docs/QUANTITIES.md`, which P16 pins by hash), as
`inventory[].atoms_per_g` does. The ledger's `gas` block records the ejectile table
version, `uncovered` and `missing_light_states`.

## Step summary

`summary` (optional) adds a complete inventory report, `steps[].summary`, to every step: a FISPACT-II-style
nuclide table, element table, decay-mode activity totals and dominant-contributor rankings. Without it the result is
unchanged and no `summary` key is written.

| Field | Default | Meaning |
| --- | --- | --- |
| `nuclides` | `true` | Emit the per-nuclide table. |
| `elements` | `true` | Emit the per-element table. |
| `dominant_top` | `10` | Entries kept in each dominant-contributor list; 1 to 256. |
| `min_atoms` | `0` | FISPACT `MIND`: omit nuclide-table rows whose absolute atoms (`atoms × material.mass_g`) fall below this. Finite and not negative. |
| `include_stable` | `true` | `false` is FISPACT `NOSTABLE`: omit stable nuclides from the nuclide table. |
| `depletion` | `false` | `true` is FISPACT `DEPLETION`: add `summary.depletion`, the per-step depletion table of every initial-material nuclide. |

`min_atoms` and `include_stable` trim the nuclide table only. Totals, the element table and the dominant rankings
always include every state, so a filtered table still reconciles with its totals. The same `summary` section is
accepted by the mesh specification and applies to every cell.

`depletion` is opt-in within the summary: without it no `depletion` key appears in `steps[].summary`, and the ledger
and certificate carry no `depletion` entry. It works with trace and coupled mode, with per-step spectra, and with
schedule library switches (each step reports the rates of the library it ran on). With an `uncertainty` section that
supplies MF=33 covariance, each reaction channel also carries the relative standard uncertainty of its one-group
rate. [Read your results](results.md#depletion-table) describes the emitted fields.

```json
{
  "material": {"mass_g": 10.0, "density_g_cm3": 7.87, "composition": {"Fe": 100}},
  "summary": {"dominant_top": 5, "min_atoms": 1e6, "include_stable": false}
}
```

[Read your results](results.md#step-summary) describes the emitted fields.

## Pathways and routes analysis

The optional `pathways` object asks for a full source-to-target route analysis (FISPACT-II's `ROUTES` and
`UNCERTAINTY` pathway tables). It is opt-in: without it the result is byte-identical to a run that never heard of
it, and it is independent of the older `options.outputs` entry `pathways`, which keeps the trace-mode
first-product table.

```json
"pathways": {
  "targets": ["Mn54", "Co60"],
  "sources": [],
  "max_depth": 10,
  "path_floor": 0.005,
  "loop_floor": 0.01,
  "max_paths": 100
}
```

| Field | Default | Meaning |
| --- | --- | --- |
| `targets` | `[]` | Target nuclides. Empty selects the dominant nuclides of the final step automatically. |
| `rank_by` | `"activity"` | Ranking for automatic targets: `"activity"` or `"heat"` at the final step. |
| `top_targets` | `10` | Number of automatic targets. |
| `sources` | `[]` | Source nuclides. Empty selects every initial nuclide (in trace mode the bulk material's nuclides, plus `feed` when the schedule declares one). |
| `max_depth` | `10` | Largest number of edges between a source and a target (1 to 64). |
| `path_floor` | `0.005` | A route is kept when it carries at least this fraction of its target's inventory. |
| `loop_floor` | `0.01` | A walk that revisits a nuclide is kept when it raises its loop-erased path's contribution by at least this fraction. |
| `max_paths` | `100` | Routes reported per target; the excess is dropped smallest first and shows up in the residual. |
| `by_step` | `false` | Also report every route's atoms at the end of each schedule step. |

Nuclide names are explicit nuclides such as `Mn54` or `Co60m1`. A source that is not an initial nuclide, or a target
that is not a tracked state of the run, is named in the result's `notes` rather than failing the run.

Each route is integrated exactly rather than estimated. For a route `x0 -> x1 -> ... -> xk`, ACTINV solves the
linear chain with every node's full removal rate (decay, all reactions and declared removal) on the diagonal and
only the route's own edge as the source, through every schedule step, using the same per-step rates the main solve
used. The solve is a dense matrix exponential of the small bidiagonal system, so there is no Bateman closed form
and equal rates are harmless. The analysis is linear in the source atoms and works in both trace and coupled
modes. A route is at most `max_depth` edges long; the search discards a branch as soon as an upper bound on what
it can still deliver falls below the smallest target's `path_floor`, so default settings stay fast on large
networks.

The target inventory is the sum over every walk through the graph of that walk's chain contribution. Walks that
never repeat a nuclide are reported as ordinary routes. A walk that revisits a nuclide (for example a
`(n,g)` reaction followed by a decay back to the parent) has `is_loop: true`, names its loop-erased `base_path`,
and reports `loop_gain`, its contribution relative to that base path. The floors apply to routes and loop walks
separately, so a route below `path_floor` alone is not promoted by the loops attached to it.

## Combining schedule features

The schedule features were built independently, and some pairs would be half-applied rather than wrong, so they are
refused when the spec is validated, with a message naming both features:

| feature | refused together with |
|---|---|
| power-driven steps (`power_W_cm3` / `power_W_per_g`) | `uncertainty`, `options.screen`, `options.fission`, `pathways`, schedule `library` overrides, `wall_loading_MW_m2` on the same step |
| schedule `partition` | `pathways`, schedule `library` overrides, `options.mode: "trace"` |
| schedule `library` overrides | `uncertainty`, `options.gas`, `self_shielding`, `damage`, `fission_yields.files`, `options.rate_scale`, power-driven steps, `kerma`, `partition`, `options.fission`, cross-section `overrides`, `pathways`, `spectrum.rebin` on a step spectrum, decay-only runs |

`wall_loading_MW_m2`, `spectrum.rebin`, half-life `overrides` and `fission_yields.spontaneous`
combine with all of these. A power step may carry a `partition`.

## Photon options

The entire `photon` object is optional. Without a response file, ACTINV still emits evaluated line/multigroup photon
sources and energy-closure diagnostics, but dose fields are `null`.

| field | meaning | default |
|---|---|---|
| `group_structure` | `fispact-24`, `steiner-22`, or `custom` with `group_boundaries_eV`. | `fispact-24` |
| `group_boundaries_eV` | Finite, nonnegative, strictly increasing photon boundaries. | none |
| `response` | External `actinv-photon-response-1` JSON and mandatory SHA-256 declaration. | none |
| `build_up_factor` | Semi-infinite-slab screening factor `B`. | 2 |
| `gamma_constant_cutoff_eV` | Lower energy cutoff for specific gamma constants. | 20,000 eV |
| `point_source` | Optional point-source dose, the FISPACT-II `DOSE 2` analog; see below. | none |
| `bremsstrahlung` | Optional bremsstrahlung component, the FISPACT-II `BREMSSTRAHLUNG` analog; see below. | none |

Build response data with `scripts/build_photon_response.py`; see the [data-source record](https://github.com/AvilaLabs/ACTINV/blob/master/docs/DATA.md). A response must contain attenuation
curves for every material element to produce the contact-dose proxy.

Photon power in groups whose centroid is below the response's lowest tabulated energy (1 keV for the shipped NIST
response) does not enter the proxy. It is reported as `dose_response_subthreshold_power_W_g`. The proxy is still given
when that power is at most `1e-4` of the step's total photon power; a nuclide's own proxy follows the same rule with
that nuclide's power. The limit is fixed. Below 1 keV the air-to-material ratio is not tabulated and can move by up to
about an order of magnitude in light elements, so a `1e-4` share leaves the proxy low by at most about `1e-3`. If the
share is larger, the proxy is `null`; use a response that reaches lower energies or a transport calculation. Groups
above the response's highest energy, missing elements and power outside the group structure still make the proxy `null`.

### Steiner 22-group structure

`group_structure: "steiner-22"` selects the 22-group "Steiner" gamma structure that FISPACT-II uses for `GROUP 1`.
The boundaries are the "22 Group" column of Table 6 of the FISPACT-II User Manual (UKAEA-R(18)001; Table 5 of
UKAEA-CCFE-RE(21)02): 0, 0.01, 0.10, 0.20, 0.40, 1.00, 1.50, then 0.5 MeV steps to 8.00, then 10.00, 12.00 and
14.00 MeV. Unlike the 24-group structure, which extends to 20 MeV here, the table stops at 14 MeV; photons above it are
counted in the overflow diagnostics and, as for any structure that omits source photons, make the contact and
point-source doses `null`. Combining `steiner-22` with `group_boundaries_eV` is an error.

### Point-source dose (`photon.point_source`)

Absent by default; when present every step's `photon_source` gains `point_source_dose`. It is the FISPACT-II `DOSE 2`
analog: manual Eq. (63), `D = C sum_i mu_a(E_i) exp(-mu(E_i) r) m_s S(E_i) / (4 pi r^2)`, where the step's grouped
photon source is a point source and each group acts at its centroid energy, as in the contact dose. `mu_a` is the
response's air mass energy-absorption coefficient, the same curve the contact dose uses. Eq. (63) has no build-up
factor, so `build_up_factor` plays no part.

| field | meaning | default |
|---|---|---|
| `distance_m` | Source-to-detector distance in metres. Must be at least 0.3. | required |
| `air_attenuation` | Apply the air transmission `exp(-mu r)` of Eq. (63). | `true` |
| `air_density_g_cm3` | Converts the air mass attenuation to a linear coefficient. | 1.20479e-3 (NIST dry air near sea level) |

FISPACT-II raises a `dist` below 0.3 m to 0.3 m and prints a message; ACTINV rejects it so the configured distance is
the one used. The source mass is `material.mass_g`, which defaults to 1 g, so the default matches FISPACT-II's 1 g
convention; for another mass the dose scales linearly and `gamma_air_dose_Sv_h_per_g` gives the 1 g value. Air
transmission needs an `air_mass_attenuation` curve (NIST table 4 mu/rho) in the photon response: build it with
`scripts/build_photon_response.py --air-attenuation`. A response without that curve gives a `null` dose and an
`unavailable_reason` when `air_attenuation` is `true`; set it `false` to ask for the unattenuated dose. The dose is
`null` with a reason under the same conditions as the contact proxy: no response, power above the response's top
energy or outside the group structure, or a sub-threshold share above `1e-4`.

### Bremsstrahlung (`photon.bremsstrahlung`)

Absent by default. `nuclides` lists canonical nuclide names (`Cl36`, `Ar42`, `K42`), as FISPACT-II's
`BREMSSTRAHLUNG` keyword does; duplicates, elements and non-canonical spellings are errors. Each listed nuclide with
positive activity at a step is treated as emitting one beta-minus per decay at its mean beta energy `E0`. Manual
Eqs. (65)-(67) then give, per electron and group `[E_i, E_i+1]` (keV, `a = 2.76e-6` per keV), `aZ[E0 ln(E_i+1/E_i) -
(E_i+1 - E_i)]` photons when `E0 >= E_i+1`, `aZ[E0 ln(E0/E_i) - (E0 - E_i)]` when `E_i < E0 < E_i+1`, and none when
`E0 <= E_i`, carrying the bin-midpoint energy `(E_i + E_i+1)/2`. The same expressions are applied to the continuous
beta spectrum through its mean energy, as the manual does. The result is a separate `photon_source.bremsstrahlung`
block with its own groups, per-nuclide groups, contact dose (the `B/2 mu_a/mu_m` slab formula, in Gy/h) and, when
`point_source` is set, point-source dose. It is never added to the decay-photon groups, powers or dose proxies; add
the two doses yourself if you want the total.

Inputs are never inferred. `E0` is the average energy of the decay record's beta-minus (STYP 1) spectrum; the
light-particle mean energy, which also counts conversion and Auger electrons, is not substituted. `Z` is the atom-fraction
mean atomic number of the declared `material` (Eq. 68; the manual says atom fractions even though mass fractions are
used elsewhere), taken from the initial composition. A nuclide that lacks `E0`, or any nuclide when `Z` is unavailable
(a material element without natural-isotope data), is listed in `missing_inputs` with the missing input, contributes
nothing, and sets `inputs_complete` to `false`; the totals then cover only the nuclides that were evaluated. Listed
nuclides without activity appear in `requested_nuclides_without_activity`.

Approximations: this is the manual's thin approximation, not electron transport. Eq. (66) diverges in a group whose
lower edge is 0 eV, such as the first group of both built-in structures; the manual does not say how FISPACT-II
treats it, so ACTINV leaves that group empty and reports the exact energy the model places there as
`zero_lower_edge_group_power_W_g`. FISPACT-II selects candidates by half-life and mean-energy criteria (appendix C.7.5)
for its `PRINTLIB 4` listing; ACTINV applies no such filter and evaluates exactly the nuclides you list.

## Options and result

`mode` is `auto`, `trace`, or `coupled`. `cram_order` is `16` (default) or `48`; an uncertainty run evaluates the
other order as its separately reported numerical-method comparison. For each initial nuclide, `auto` computes the
base-spectrum reaction-loss
optical depth `tau = loss_rate * sum(dt * flux_multiplier)` and burn-up fraction `-expm1(-tau)`; it selects `trace`
only when the largest fraction is strictly below `1e-6`. The controlling nuclide, optical depth and fraction are
ledgered. Explicitly requested modes are always honored. `prune` is `rate`, `reach`, or `none`. The `outputs` list
controls optional pathway and photon/dose calculations; the core inventory/activity/heat diagnostics remain in each
result step.

The ordered schedule is the pulse representation: every positive `flux` multiplier scales all base projectile rates,
and zero is an exact decay-only gap. Results are emitted after every segment. Each step records the current multiplier
as `flux`, cumulative elapsed `t_s`, cumulative multiplier-weighted exposure `flux_weighted_time_s`, and physical
`fluence_n_cm2` (base total flux times weighted exposure). Scientific notation in a duration, such as `1e-8 s`, is
accepted as a number rather than mistaken for a unit suffix.

Any step may also carry its own `spectrum` — the same shape as the base `spectrum` — replacing it for that step's
duration while `flux` still scales the step's total. This expresses pulsed or multi-field irradiations where the
spectrum shape itself changes between segments (for example fusion pulses at different field positions or a spallation
pulse inside a thermal field). A step spectrum must share the base spectrum's `structure` and group count; an override
identical to the base deduplicates to it. The physical fluence sums each step's own spectrum total times its
multiplier-weighted duration, and the schedule ledger reports how many steps declared overrides as `step_spectra`.
Multi-spectrum schedules always collapse on the groupwise library rows (never the pre-collapsed artifact). Combined
with `uncertainty`, the MF=33 collapse expands to one parameter per (spectrum, library row): each step's tangent
directions are the rows collapsed under its own spectrum, decay-constant directions stay spectrum-independent, and
the propagated covariance carries the cross-spectrum blocks `φ_sᵀ C φ_s'` so a single physical draw propagates
through every step's collapse. Each sensitivity parameter reports its `spectrum` index (0 = base).

Any step may also declare an optional `library` override (and, when that library is for a different projectile than
the spec's, the matching `projectile`). The override is sticky: the step and every later step collapse their reaction
rates from the new library until a step names another library, or names the spec's own `library` again to return to
it. The step that switches must carry its own `spectrum` in the new library's group structure (`fispact-709` for a
neutron library, `fispact-162` for any other projectile, or `custom` with boundaries equal to the library's); later
steps on that library keep it as their default spectrum, and may replace it for a single step with their own. A
`projectile` without a `library` is rejected. Each overriding library is verified like the base library (hashes,
index projectile, group structure, and temperature: a neutron library must match `options.temperature_K`, any other
projectile's index must say 0 K), and its path joins the prepared-data fingerprint.

All libraries share the one decay chain, so the state vector is the same through a switch: the inventory at the end
of one step is the initial inventory of the next, and only the collapsed rate triplets change. A product that the
decay chain does not know leaks exactly as in a single-library run; each overriding library's unmapped products,
products without decay data, isomer fallbacks and absent targets are listed under `ledger.step_libraries`, together
with the steps it served and its fluence, and the library and index hashes appear under
`certificate.inputs.step_libraries`. The cumulative `fluence_*` fields add every step's own spectrum total whatever
its projectile; the per-library fluence is the one to read across a switch. Pathway attribution covers the base
library only and is suppressed (named in `ledger.step_libraries.pathways_suppressed`). Overrides cannot be combined
with `uncertainty`, `options.gas`, `self_shielding`, `damage`, `fission_yields` or `options.rate_scale` (each is built
on one library's rows), nor with power-driven steps, `kerma`, `partition`, `options.fission`, cross-section `overrides`
or the full `pathways` analysis (each reads the base library's rates, groups or per-step matrix), with mesh mode, or
inside a study; the spec is refused rather than half-applied.

Any step may also declare optional `feed` and `removal` maps. `feed` entries are explicit nuclides
(`Co60`, `Ta180m1`) with constant rates in atoms s⁻¹ g⁻¹, applied during the declaring step regardless of the
multiplier — a feed on a zero-flux cooling step still delivers atoms. `removal` entries are nuclides or element
symbols with first-order rates in s⁻¹; an element applies its rate to every tracked isotope and isomer of that
element, and a nuclide key removes only that state. Removed atoms accumulate in a dedicated sink reported as
`removed_atoms_per_g`, present only when a step declares removal. In trace mode the constant-reservoir material
nuclides are exempt from removal — the trace formulation holds them undepleted — unless the same nuclide is also
fed, in which case the fed atoms are carried in a real state and are removable. Every exempted reservoir nuclide
is named in the ledger under `feed_removal.removal_reservoir_exempt`, along with the per-nuclide totals fed and
the declared removals. Pathway attribution covers production chains only and is suppressed when a schedule
declares feed or removal.

For non-neutron projectiles (proton, deuteron, triton, helion, alpha and gamma), steps expose the generic `fluence_particles_cm2`
and identify the projectile in the result, ledger, certificate and prepared/mesh compatibility records. Neutron
results retain their historical bytes and `fluence_n_cm2` field when `projectile` is omitted.

### Screening and perturbation options

`options.screen` accepts `{"bmin_atoms_per_g": value}` with a finite nonnegative value and requires `prune: "rate"`. It applies the declared screening threshold and emits a `screen` certificate for dropped-state bounds on banded responses. Inspect that certificate and coverage before relying on a screened calculation.

The optional scale maps support explicit perturbations for sensitivity and sampling workflows:

| Field | Key | Value |
| --- | --- | --- |
| `options.rate_scale` | Activation-library row index as a string | Multiplicative collapsed-reaction-rate factor |
| `options.decay_scale` | Explicit radioactive nuclide, such as `Mn56` | Multiplicative decay-constant factor |
| `options.yield_scale` | Explicit `parent:product`, such as `U235:I135` | Multiplicative independent-yield factor |

Applied factors are recorded in the ledger. Absent or stable decay targets and absent yield pairs are named errors. Decay scaling changes decay edges and the corresponding activity, heat, photon, and dose responses consistently; yield uncertainties scale with their yields.

## Build an activation library

The production builder is part of the `actinv` binary:

```bash
actinv build-library INPUT OUTPUT.npz \
  --format auto --projectile auto --groups fispact-709 \
  --temperature-K 293.6 --workers 4 --cache /data/actinv-cache
```

`INPUT` is one ENDF-6 evaluation or a directory. `--format` accepts `auto`, `tendl` or `eaf`; `--projectile` accepts
`auto`, `neutron`, `proton`, `deuteron`, `triton`, `helion`, `alpha` or `gamma`; `--groups` accepts any named structure under
[Group structures](#group-structures) (a neutron build takes `fispact-709` or another neutron grid; the
charged-particle and gamma builds take `fispact-162`) or a custom boundary file. Neutron defaults are 709 groups and 293.6 K; the other projectiles' defaults are 162
groups and 0 K (gamma, like the other non-neutron projectiles, refuses a nonzero temperature — TENDL does not carry
a Doppler-broadened gamma sublibrary). The adjacent `<stem>_index.json` records source hashes, normalized options,
group hash, builder fingerprint, target ledgers and the final NPZ hash. A content-addressed cache is optional and
revalidated before reuse.

### Group structures

The named structures are FISPACT-II's own standard grids. Each is vendored as a JSON file under
`crates/actinv-data/data/fispact_<N>_groups.json` whose `source` field names the `ebins_<N>` file of FISPACT-II's
`ebins.tar.bz2` and that file's SHA-256; the boundaries are copied verbatim and are checked in the unit tests against
the published first, second, middle, penultimate and last values. Boundaries are stored ascending after loading.

| Name | Groups | Upper edge | Use |
| --- | --- | --- | --- |
| `fispact-66` (WIMS/LANL) | 66 | 25 MeV | neutron |
| `fispact-69` (WIMS) | 69 | 10 MeV | neutron |
| `fispact-100` (GAM-II) | 100 | 14.918 MeV | neutron |
| `fispact-172` (XMAS) | 172 | 19.6403 MeV | neutron |
| `fispact-175` (VITAMIN-J) | 175 | 19.6403 MeV | neutron |
| `fispact-211` (VITAMIN-J+) | 211 | 55 MeV | neutron |
| `fispact-315` (TRIPOLI) | 315 | 19.64 MeV | neutron |
| `fispact-351` (TRIPOLI+) | 351 | 55 MeV | neutron |
| `fispact-586` (CASMO) | 586 | 20 MeV | neutron |
| `fispact-616` (RED/LLNL) | 616 | 20 MeV | neutron |
| `fispact-709` (CCFE) | 709 | 1 GeV | neutron (default) |
| `fispact-1102` (UKAEA) | 1102 | 1 GeV | neutron |
| `fispact-162` (CCFE) | 162 | 1 GeV (5 keV floor) | proton, deuteron, alpha, gamma |

An `xN` suffix (`fispact-1102x2`) refines each group log-uniformly into `N` subgroups, as it does for 709. A spectrum
on any of these structures must supply exactly the structure's group count, and its library must have been built on
the same named structure: the name, group count and boundary hash are all checked, and a flux on a different grid
must be rebinned to the library first or given as `custom` boundaries matching the library. Group edges above an evaluation's
upper energy carry zero cross section, so a 20 MeV grid is the natural pairing for a 20 MeV evaluation and only the
709, 1102 and 162 grids reach the 200 MeV upper limit of the TENDL neutron evaluations and beyond.

### High-energy spallation splice

`build-library --spallation TABLE.json [--splice-energy-MeV MEV]` is the counterpart of FISPACT-II's
`USESPALLATION`: above a splice energy (default 200 MeV) the residual-production cross sections of a separate,
user-supplied high-energy library are merged into the activation library. Nothing is fetched or generated; the table
is the only source of the spliced values and ACTINV ships none.

The table is JSON with schema `actinv-spallation-residuals-1` (unknown fields are rejected):

```json
{
  "schema": "actinv-spallation-residuals-1",
  "description": "required free text naming the code, version and run that produced the data",
  "projectile": "proton",
  "boundaries_eV": [2.0e8, 2.4e8, 2.8e8, "...", 1.0e9],
  "targets": [
    {"za": 82208, "liso": 0, "channels": [
      {"mt": 5,
       "loss_barns": [0.9, 0.8, "..."],
       "products": [{"zap": 80200, "lfs": 0, "sigma_barns": [0.002, 0.003, "..."]}]}
    ]}
  ]
}
```

- `boundaries_eV` are ascending and must equal the library's group boundaries from the effective splice energy to the
  top of the library (to `1e-12` relative), so each table group is exactly one library group. Every `sigma_barns` and
  `loss_barns` array has one finite, nonnegative value per table group.
- The splice energy snaps up to the nearest library boundary, so no group mixes two sources. `splice 200 MeV` is
  exact on `fispact-709` and `fispact-162`; on the log-spaced `fispact-1102` it becomes 208.93 MeV. Both the
  requested and the effective energy are recorded. A splice at or below the lowest boundary, or leaving no group
  above it (an EAF-range 20 MeV grid), is an error.
- A product is the row `(mt, zap, lfs, MF=10)` of its target; `loss_barns`, if given, is the matching target-depletion
  row. An existing row with the same key has its spliced groups replaced; a new row is appended after the target's
  rows with zero cross section below the splice. Rows the table does not name, and every group below the splice,
  keep their values bit for bit. Spliced channels leave the other MTs of the same target untouched, so an evaluation
  that already tabulates overlapping reactions above the splice counts them as well; as in FISPACT-II, the user
  chooses the splice energy and the table's content.
- Use an MT that carries no gas-production accounting (MT 5 is conventional); a channel whose MT is in the
  hydrogen/helium table also receives its ejectile counts under `options.gas`.
- Table targets absent from the library are skipped and listed. The table's `projectile` must equal the library's.

The index gains a `spallation` block (omitted without the option, leaving the index unchanged) with the table path
and SHA-256, its description, the requested and effective splice energies, the first spliced group, the rows
replaced and added in total and per target, and the skipped targets. Each affected target's ledger carries a line with
the same counts.

### Triton and helion projectiles

`--projectile triton` and `--projectile helion` build from ENDF-6 evaluations whose incident-particle record is
NSUB 10030 (triton, projectile ZA `(1, 3)`) or NSUB 20030 (He-3, ZA `(2, 3)`), on the same CCFE-162 structure and the
same MF=3/6/8/10 families as the other charged projectiles. The projectile is read from the evaluation, not from a file
name, and AWI must be within 10% of mass number 3. Residual arithmetic is target + projectile − emitted particles
(for example Fe-56 + t gives Co-59 for MT=102, Fe-56 + He-3 gives Ni-59), and the MT=5 residual production of charged
projectiles uses the same MF=6/MF=8/MF=10 path as proton, deuteron and alpha. ACTINV ships no triton or helion
library: the TENDL t and h sublibraries are the intended source, but none is in the standard catalog, so build the
library yourself and check its coverage in the target ledgers.

### Gamma (photonuclear) projectile

`--projectile gamma` builds from the TENDL `g` sublibrary (public, TENDL-2025): NSUB 0, projectile ZA `(0, 0)`, AWI
required to be exactly `0.0`. Residual arithmetic is target + photon − emitted particles on the CCFE-162 group
structure, the same structure and MF=3/6/8/10 families the proton/deuteron/alpha path already uses. Some gamma MTs
(for example 50, 51 and 91 — single-neutron production to the ground state, a discrete level, or the continuum) carry
MF=3/6 cross sections without an MF=8 product declaration; the builder resolves their ground-state residual from the
ENDF MT reaction definition instead of dropping the row, and records every such resolution in the target ledger.
Single-neutron production is counted once. When MT=4 carries its own MF=8 state-resolved declaration
(TENDL-2025 Ta-181 and W-186, for example), MT=4 is used and every MT50–91 detail section is skipped. When MT=4 has no
MF=8 of its own and MT50–91 detail is present, MT=4 is skipped and the detail sections carry the channel. Each skip is
recorded in the target ledger.

Photofission is recognized only as a total cross section: the MF=10 IZAP=−1 total-fission sentinel (TENDL-2025),
or, under a normalization profile such as `--profile tendl`, the TENDL-2017 encoding of the same quantity as a single
MF=10 IZAP=0 LFS=0 section with no MF=3 MT=18 (P98, recorded in the ledger). An evaluation that declares actual
photofission product yields under MT=18 fails closed, since photofission yields are out of scope for v1. TENDL-2017
gamma files also contain MF=3 tables that start above the reaction threshold while the MF=10 states carry the ramp
from threshold. Under the default profile these fail closed on the emitted-state sum, or, where the state sum
stays within the standard zero-total envelope, are scaled to the zero total with a ledger record, so build TENDL-2017
gamma files with `--profile tendl`. Under `--profile tendl` the
builder applies the MF=3 threshold extension (P100): for each such MT it extends the MF=3 table below its first
energy E3 with the summed MF=10 states (ZAP ≥ 0), one point at each energy below E3 that a state tabulates, plus a
joining point at E3 carrying the states' summed left limit, interpolated lin-lin. The extension applies only when
every state is lin-lin below E3; otherwise the MT is left unchanged and the ledger says why. Each extension writes a
`MT<n>: MF=3 threshold extension: …` ledger line, and remaining per-group differences are reconciled and recorded as
`state_sum_normalized`. Without the extension the threshold group's production would be scaled to the zero MF=3
total and lost. No TENDL-2025 gamma file triggers it.

Pass `--decay` (and `--decay-fallback`) when building a gamma library: isomer labels then resolve against the decay
sublibrary the runtime uses. Without decay data the builder falls back to its target catalog and label ranks.

Gamma activation libraries are not yet published through `actinv data fetch` — that is a separate release decision.

## Build a damage-energy table

```bash
actinv build-damage INPUT OUTPUT.json \
  --projectile auto --groups fispact-709 --temperature-K 293.6 --cache /data/actinv-damage-cache
```

`INPUT` is one ENDF-6 evaluation or a directory. Every MF=3/MT=444 damage-energy production section is collapsed
through the same parser, temperature check, and lethargy integration as `build-library`; `--projectile`, `--groups`,
`--temperature-K`, and `--cache` share that command's semantics. The result is a strict `actinv-damage-table-1`
document: group-structure label and boundaries, per-file SHA-256 provenance, a `targets` map of canonical nuclide
rows, and an `uncovered` list naming every evaluation without MT=444 — absent sections are reported, never
zero-filled. The output file's SHA-256 is printed on success for pinning into `damage.table`.

## Build an MF=33 covariance sidecar

```bash
actinv build-covariance INPUT ACTIVATION.npz OUTPUT.cov.npz \
  --workers 4 --cache /data/actinv-cov-cache
```

`INPUT` must be the neutron ENDF corpus from which `ACTIVATION.npz` was built. Source hashes, filenames, MAT,
ZA/LISO target identities and the adjacent activation index must all agree. The builder supports strict MF=33 NI
forms LB=0--6, 8 and 9; NC components, foreign or cross-sublibrary references, lumped MTL, malformed dimensions and
unknown forms fail with MAT/MF/MT context. Its separate `<stem>_index.json` records the activation identities, source
manifest, representation inventory, builder fingerprint and final sidecar hash. Per-source checkpoints are
content-addressed and revalidated; worker count and cache reuse do not change canonical bytes. Raw evaluations,
checkpoints and generated sidecars remain external data and must not be committed.

When photons are requested (or `outputs` is omitted), `steps[].photon_source` contains:

- evaluated discrete line rates and per-nuclide evaluated/source yields;
- group photon rates, energy centroids and emitted powers, per gram and for `material.mass_g`;
- raw energy moments, explicit `E_EM` normalization factors and represented-power fraction;
- specific gamma constants in `Gy m2/(Bq s)` and `mGy m2/(GBq h)` when a response is supplied;
- `contact_gamma_air_dose_proxy_Gy_h`, response coverage, and explicit ungrouped/unrepresented power;
- `dose_response_subthreshold_power_W_g`, only when non-zero: photon power below the response's lowest energy, omitted from the proxy.
- `point_source_dose`, only with `photon.point_source`: distance, mass, dose in Sv/h and per gram, excluded power and the reason a dose is `null`; each `by_nuclide` entry gains `point_source_gamma_air_dose_Sv_h`;
- `bremsstrahlung`, only with `photon.bremsstrahlung`: the separate component described above.

Use one-based result step numbers for transport export:

```bash
actinv export-openmc result.json 2 source.py
actinv export-mcnp result.json 2 source.sdef
```

Both exports use the photon-group centroids and total photons/s. The point at the origin is a placeholder, not a
spatial activation model. An export fails if custom boundaries omitted any source photons.

For mesh results, `actinv export-openmc-mesh mesh_result.ndjson STEP source.py` writes a distributed spatial
source: one `openmc.IndependentSource` per mesh cell, sampled uniformly inside the cell's recorded `bounds_cm`
(`openmc.stats.Box`), with a discrete photon-energy distribution taken from the cell's exported group centroids and
probabilities, and the cell's absolute photon rate as `strength`. The fragment ends with `TOTAL_PHOTONS_S`, the
absolute sum over all cells. Cells whose photon source is zero contribute no entry; a cell with photons but missing
geometry, a missing step, an unrequested photon output, or an inconsistent group total fails the export closed.

## Flux interchange (`actinv-flux-1`)

Transport spectra are canonicalized before activation. The format is newline-delimited JSON: exactly one `header`,
the declared number of ordered `cell` records, and one closing `footer`. A cell value is integrated flux in
`n cm^-2 s^-1` (neutron) or `particles cm^-2 s^-1` (photon and the other non-neutron projectiles) for that energy
group—not flux density per eV or lethargy. Every ID is unique and every ordinal begins at zero and increases by one.
The strict reader rejects blank, malformed, missing, duplicate, extra and trailing records, invalid totals,
nonfinite/negative values and inconsistent geometry.

```bash
actinv import-flux openmc statepoint.h5 flux.ndjson \
  --tally 7 --source-rate 1.0e15 --energy-floor-eV 1.0e-5 --window-rows 16384
actinv import-flux meshtal meshtal flux.ndjson \
  --tally 24 --source-rate 1.0e15 --energy-floor-eV 1.0e-5
actinv import-flux mctal mctal flux.ndjson \
  --tally 4 --source-rate 1.0e15 --energy-floor-eV 1.0e-5
actinv import-flux fispact fluxes flux.ndjson --groups descending-boundaries.json
```

The OpenMC and MCNP source rate is mandatory and positive. It converts a per-source-particle tally to physical flux;
FISPACT `fluxes` values are already absolute and are not rescaled. If the source grid starts at zero, an explicit
positive `--energy-floor-eV` below the next boundary is required and both the original zero and replacement are kept
in provenance. Every importer hashes and re-stats its input and publishes the canonical file by sibling temporary-file
rename only after the footer closes.

Supported subsets are deliberately narrow:

- OpenMC statepoint major 18, one selected `flux`/`total`/tracklength tally with exactly one 3D Cartesian regular or
  rectilinear `MeshFilter` and one `EnergyFilter`, in either order, plus an optional single-bin `ParticleFilter`
  (`neutron` or `photon`; more than one particle bin is refused, since a multi-particle tally mixes flux across
  particle types into one row). A tally with no `ParticleFilter` is legacy behaviour and is accepted only when the
  mesh spec's projectile is `neutron`; a photon `ParticleFilter` writes `particles cm^-2 s^-1` flux and a `particle`
  metadata field, and a mesh run whose projectile disagrees with the imported flux's particle units fails before
  solving. Importing an unfiltered or neutron-filtered tally writes byte-identical output to the pre-P94 importer;
- MCNP traditional rectangular XYZ neutron FMESH `meshtal` column output with energy rows and optional checked totals;
- MCNP energy-binned F4:N `mctal` with one cell-ID F dimension, singleton remaining dimensions and optional checked
  total energy bins;
- standard FISPACT-II `fluxes`: N descending group values, first-wall loading, then its identifying title, against an
  explicitly supplied descending group-boundary JSON file.

Structured meshes (OpenMC, meshtal) above 100,000,000 cells are refused before any allocation is sized by the
declared dimensions.

Other scores, particles, estimators, filters, dimensions, mesh shapes, multipliers, responses, cumulative/time bins or
file variants produce a named error rather than a guessed interpretation.

## Independent mesh specification (`actinv-mesh-spec-1`)

Mesh mode replaces the ordinary `spectrum` with a mandatory canonical-file path and SHA-256. All cells receive the
same explicit library, decay data, optional fission-yield files, material, schedule, options, photon configuration,
uncertainty configuration, and radiological configuration, and solve independently. Covariance and radiological
data are verified and prepared once; workers borrow them rather than re-reading or cloning them per cell.

```json
{
  "spec": "actinv-mesh-spec-1",
  "title": "iron activation mesh",
  "projectile": "neutron",
  "library": {
    "path": "/data/actinv_tendl2025_n_709g.npz",
    "sha256": "64 hexadecimal digits"
  },
  "decay": {"primary": "/data/endf-b-viii-0_decay.dat"},
  "material": {
    "mass_g": 1.0,
    "basis": "wt_percent",
    "composition": {"Fe": 100.0}
  },
  "flux": {
    "path": "flux.ndjson",
    "sha256": "64 hexadecimal digits"
  },
  "schedule": [
    {"dt": "5 min", "flux": 1.0},
    {"dt": "1 h", "flux": 0.0}
  ],
  "options": {
    "mode": "auto",
    "prune": "rate",
    "bmin_atoms_per_g": 1e-8,
    "temperature_K": 293.6
  },
  "chunk_cells": 64,
  "threads": 4,
  "group_workloads": true,
  "cell_result_fields": ["steps", "pruned_states", "total_states", "certificate"],
  "memory_limit_bytes": 4000000000,
  "resume": false
}
```

`chunk_cells` defaults to 64 and is bounded to 1–65,536. `threads` defaults to 1 and is bounded to 1–256. Execute it
with `actinv mesh mesh.json mesh-result.ndjson`. Immutable activation/decay/response data are verified, decompressed
and prepared once. Canonical cells are read a chunk at a time, restored to input order after Rayon execution, and
written as `actinv-mesh-result-1` header/cell/footer records.

`group_workloads` defaults to true: cells whose rebinned activation-group flux vectors are byte-identical share one
solved result (keyed by the SHA-256 of the f64 little-endian group bytes, memo bounded to 256 distinct workloads and
512 MiB of memoized result bytes).
Reuse changes only scheduling — every cell record is bit-identical to a `group_workloads: false` run, and the
footer records the count as `cells_served_from_reuse`. `cell_result_fields` keeps only the named top-level
`RunResult` fields in each cell record; absent means the complete record, and an unknown name is rejected at
validation.

An entry can also be dotted, `steps.<field>[.<key>...]` (P90), to keep only part of each step instead of the
whole `steps` array: `<field>` must be a `StepOut` field (e.g. `heat_W_per_g`, `photon_source`), and further
segments descend through JSON objects — `steps.photon_source.groups` keeps only `photon_source.groups` (with
every key of each group entry, since the path ends there), dropping `photon_source`'s other fields and every
other step field. `steps` and a `steps.<field>` entry cannot both appear. A path that reaches an array or a
scalar with segments still to consume is rejected at validation, as is a key that does not exist at that point
in `StepOut`'s shape — except inside a nuclide- or response-keyed map (`activity_Bq_per_g`, `uncertainty.responses`,
`damage.elements`), which can only be selected whole, not narrowed to one dynamic key. A selected field that is
absent in a given step (e.g. `photon_source` with no photon output) is left out of that step's object, exactly as
in the complete record; every emitted step object otherwise holds `step` plus the selected paths. With any dotted
entry present, the cell text is assembled directly from `RunResult`/`StepOut` fields rather than by serializing
and then pruning the whole result, so a large unselected field (e.g. the per-nuclide inventory) is never built.

`memory_limit_bytes` is a post-hoc guard: after each completed chunk the process peak RSS
(`/proc/self/status` `VmHWM`) is compared to the limit and the run aborts with a named error carrying both numbers.
It is refused at validation on platforms without that accounting (it could never fire there). Without `resume`
the output is written atomically, so a tripped guard keeps no completed cells; pair it with `resume: true`.

`resume: true` makes the output file itself the checkpoint. The run writes directly (not via atomic rename); on
start it validates any existing file: the header must equal byte-for-byte the header a fresh run would emit —
including the `spec_fingerprint_sha256` field, the canonical-JSON SHA-256 of the spec with `resume`, `threads`,
`chunk_cells` and `memory_limit_bytes` removed. Complete in-order cell records stand; a torn final line is
truncated; a mid-file corrupt or out-of-order record is a named error; a file whose footer is already present
returns its summary without re-solving. Only unfinished cells are re-executed, and a completed resume is
byte-identical to an uninterrupted run except the footer's timing fields. A resumed run reproduces the
uninterrupted `cells_served_from_reuse` count because completed prefix cells seed the grouping memo.

Matching source/library boundaries use a bit-identical copy path. Other positive grids use FISPACT's default equal
flux per unit lethargy rule. Every cell result includes `source_total`, rebinned `destination_total`, `underflow`,
`overflow`, closure and method; energy outside the library is never folded into an edge group or renormalized away.
The ordinary run result is nested without per-cell timing. Only footer `wall_time_s` and `cells_per_s` vary with
scheduling; header and ordered cell bytes are deterministic across chunk and thread counts. The header certificate
binds the declared/computed canonical hash and its embedded transport/auxiliary hashes. Any cell or footer failure
names the failing premise and leaves no final result file.

### Numerical floor diagnostic

`numerical_floor_atoms_per_g` retains its existing serialized name and value
`alpha0 * max(N)` for compatibility. It is a CRAM asymptotic diagnostic scale,
**not a bound on total numerical error**, factorization/solve roundoff or each
reported population's accuracy. `heat_bound_from_below_floor_W_per_g` is the
computed heat subtotal of the reported below-scale populations, not a bound on
all numerical dose or heat error. The ledger marks
`numerical_floor.is_total_numerical_error_bound = false` explicitly.

CRAM shifted-system solves use bounded iterative refinement against the original
matrix, with compensated residual accumulation and fused-product error terms.
This applies to scalar, batched and tangent solves; it does not replace physical
inputs, filter small positive populations or establish a universal forward-error
bound. Independent numerical checks remain necessary for the claimed domain.
