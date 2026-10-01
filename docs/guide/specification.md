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
| `projectile` | `neutron`, `proton`, `deuteron`, `alpha` or `gamma`; omission preserves the historical neutron default. |
| `library.path` | ACTINV `.npz` activation library; the adjacent `<stem>_index.json` is also required. |
| `library.sha256` | Optional declared hash. ACTINV always computes the library hash and fails if a declaration differs. The index's recorded library hash is checked too. |
| `decay.primary` | ENDF-6 radioactive-decay sublibrary. |
| `decay.fallback` | Optional second decay sublibrary; records absent from the primary are taken from it. |
| `material.composition` | Natural element symbols or explicit nuclides (`U235`, `Ba137m1`) and nonnegative values interpreted by `material.basis`. |
| `spectrum.flux_per_group` | Group-integrated fluxes. `descending: true` reverses the supplied order before use. |
| `schedule` | At least one duration/flux-multiplier pair. Accepted duration units: seconds, minutes, hours, days and years. |
| `fission_yields` | Optional hash-pinned ENDF-6 neutron-induced fission-yield evaluations; see below. Empty/omitted preserves the explicit no-yields leakage path. |
| `uncertainty` | Optional neutron-only MF=33 sidecar and response selection; omission reads no covariance file and preserves the ordinary path. |
| `radiological` | Optional hash-pinned clearance, waste, ingestion, or inhalation response table; omission reads no table. |
| `damage` | Optional hash-pinned `actinv-damage-table-1` damage-energy table plus per-element displacement energies; required when `outputs` contains `damage`. |

The certificate records computed SHA-256 values for the activation library, its index, primary/fallback decay data,
every fission-yield evaluation, the photon response, covariance sidecar, and radiological table when present. A
declaration is a constraint, not a value copied into the certificate.

## Material bases

`material.mass_g` defaults to 1 g. Inventories remain per gram; the mass scales the total photon rates and powers.
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

Neutrons use `fispact-709` with exactly 709 values. Proton, deuteron and alpha use `fispact-162` with exactly 162
values and require `options.temperature_K: 0`; charged specs reject fission-yield files. `custom` requires one more
strictly increasing boundary than flux values. Those boundaries must match the activation library to `1e-12`
relative. `total`, when present, rescales group values while preserving shape; a positive `total` with an all-zero
`flux_per_group` has no shape to scale and is rejected. The spec, library index, group
structure and temperature must all identify the same projectile/data build before matrix assembly.

`spectrum.relative_error` is an optional array, the same length and order as `flux_per_group` (honouring
`descending`): each group's transport-tally statistical relative standard uncertainty. It is accepted and carried
but otherwise unused unless `uncertainty.channels` requests `"flux"` (see below); requesting that channel with no
`relative_error` given is an error naming the spectrum.

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

The `flux` channel is an [unreleased addition on master](releases.md#current-master). It propagates each input group's transport-tally statistical error (`spectrum.relative_error`, or a mesh
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
`ingestion_dose`, or `inhalation_dose`. Clearance/waste coefficients are limits in Bq/kg; ingestion/inhalation
coefficients are in Sv/Bq. Every coefficient must be finite and positive. An empty `responses` selector chooses every
table response in table order; otherwise only the named unique IDs are evaluated.

Each step reports the selected value, unit, covered and missing activity, activity-coverage fraction, contributing
nuclide count, and sorted active nuclides without a coefficient. Missing coefficients are not treated as zero.
`require_complete: true` rejects the entire calculation when any selected response lacks a coefficient for positive
activity. The certificate retains the table hash, source metadata, kind, basis, and coefficient count. See the
[qualification boundary](qualification.md) before using a regulatory table.

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

## Self-shielding

`self_shielding` is optional; omission preserves the ordinary unshielded path byte-for-byte. Its `table` names a
hash-pinned `actinv-shield-table-1` artifact — the declared SHA-256 is recomputed before the calculation and a
mismatch is an error. Build tables with `actinv build-shielding EVAL_DIR OUT.json`; the table's group boundaries
must equal the activation library's exactly.

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

A runnable walkthrough lives at `examples/shielding_demo.json`: pure W-186 under a 4–25 keV custom spectrum at
fixed `sigma0_b = 0.1` — the ledger names every applied factor and W-187 activity lands ~28% below the
unshielded solve of the same problem.

## Gas production (H and He isotopes)

This option is an [unreleased addition on master](releases.md#current-master).

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

## Photon options

The entire `photon` object is optional. Without a response file, ACTINV still emits evaluated line/multigroup photon
sources and energy-closure diagnostics, but dose fields are `null`.

| field | meaning | default |
|---|---|---|
| `group_structure` | `fispact-24`, or `custom` with `group_boundaries_eV`. | `fispact-24` |
| `group_boundaries_eV` | Finite, nonnegative, strictly increasing photon boundaries. | none |
| `response` | External `actinv-photon-response-1` JSON and mandatory SHA-256 declaration. | none |
| `build_up_factor` | Semi-infinite-slab screening factor `B`. | 2 |
| `gamma_constant_cutoff_eV` | Lower energy cutoff for specific gamma constants. | 20,000 eV |

Build response data with `scripts/build_photon_response.py`; see the [data-source record](https://github.com/AvilaLabs/ACTINV/blob/master/docs/DATA.md). A response must contain attenuation
curves for every material element to produce the contact-dose proxy.

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

For non-neutron projectiles (proton, deuteron, alpha and gamma), steps expose the generic `fluence_particles_cm2`
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
`auto`, `neutron`, `proton`, `deuteron`, `alpha` or `gamma`; `--groups` accepts `fispact-709`, `fispact-162` or a
custom boundary file. Neutron defaults are 709 groups and 293.6 K; proton/deuteron/alpha/gamma defaults are 162
groups and 0 K (gamma, like the other non-neutron projectiles, refuses a nonzero temperature — TENDL does not carry
a Doppler-broadened gamma sublibrary). The adjacent `<stem>_index.json` records source hashes, normalized options,
group hash, builder fingerprint, target ledgers and the final NPZ hash. A content-addressed cache is optional and
revalidated before reuse.

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
gamma files also contain MF=3 tables that start above the reaction threshold; under the default profile they fail
closed on the emitted-state sum, and under `--profile tendl` they are reconciled per group and recorded as
`state_sum_normalized`.

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
- `contact_gamma_air_dose_proxy_Gy_h`, response coverage, and explicit ungrouped/unrepresented power.

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
