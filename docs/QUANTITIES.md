# Physical quantity boundaries

ACTINV keeps the public `actinv-spec-1` JSON format simple: users still provide ordinary numbers with explicit unit
names such as `mass_g`, `temperature_K` and `fixed_energy_eV`. Immediately behind that wire format, the Rust core
converts the covered values to distinct zero-cost types. This makes an energy impossible to pass where a duration is
required while preserving the exact `f64` representation and released numerical path.

The conversion is intentionally incremental. This table is the P16 boundary inventory; a raw quantity not listed
here remains outside the P16 compile-time claim.

| boundary value | declared unit | Rust type | sole production raw → typed site | meaningful operation / sole kernel adapter |
|---|---|---|---|---|
| `material.mass_g` | g | `Grams` | `Spec::physical_inputs` → `Grams::new` | `Grams::get` at `photon::source_for_step` |
| `options.temperature_K` | K | `Kelvin` | `Spec::physical_inputs` → `Kelvin::new` | `Kelvin::get` at library-index comparison; legacy `PreparedRun` wrappers use the same constructor |
| `options.bmin_atoms_per_g` | atoms g⁻¹ | `AtomsPerGram` | `Spec::physical_inputs` → `AtomsPerGram::new` | passed typed to `prune::reachable_physical`; `AtomsPerGram::get` is confined to the pruning adapter |
| ascending spectrum bins | particles cm⁻² s⁻¹ per group | `GroupFluxes` / `ParticleFlux` | `Spec::physical_inputs` → `GroupFluxes::new` | `GroupFluxes::values` at activation-library collapse; `GroupFluxes::total` for rate/fluence |
| `schedule[].dt` | s after parsing | `Seconds` | `Spec::physical_inputs` → `Seconds::new` | `Seconds::get` only at CRAM/pruning/result adapters |
| `schedule[].flux` | dimensionless | `FluxMultiplier` | `Spec::physical_inputs` → `FluxMultiplier::new` | scales `Seconds` or `ParticleFlux`; `get` only at matrix/result adapters |
| requested photon group boundaries | eV | `EnergyBoundaries` | `Spec::physical_inputs` → `EnergyBoundaries::new` | `EnergyBoundaries::values` at `photon::source_for_step` |
| `photon.gamma_constant_cutoff_eV` | eV | `ElectronVolts` | `Spec::physical_inputs` → `ElectronVolts::new` | `ElectronVolts::get` at `photon::source_for_step` |
| `fission_yields.fixed_energy_eV` | eV | `ElectronVolts` | `Spec::physical_inputs` → `ElectronVolts::new` | `ElectronVolts::get` at yield selection |
| collapsed activation-library row | barn | `CrossSectionBarns` | `CrossSectionBarns::from_collapsed_kernel` in reaction assembly | multiplied only by `RatePerBarnSecond` |
| summed physical projectile flux | particles cm⁻² s⁻¹ | `ParticleFlux` | `ParticleFlux::sum_groups` in the typed group wrapper or rate kernel | `RatePerBarnSecond::from_particle_flux`; with typed time produces `ParticleFluence` |
| reaction coefficient per barn | barn⁻¹ s⁻¹ | `RatePerBarnSecond` | `RatePerBarnSecond::from_particle_flux` | applies the barn-to-cm² factor once; multiplied by `CrossSectionBarns` |
| reaction rate | s⁻¹ | `RatePerSecond` | `CrossSectionBarns * RatePerBarnSecond` | `RatePerSecond::get` at sparse-matrix assembly |
| reported fluence | particles cm⁻² | `ParticleFluence` | `ParticleFlux * Seconds` | `ParticleFluence::get` at result serialization |

All scalar wrappers have private fields and `#[repr(transparent)]`; their size and alignment are checked against
`f64`. Constructors reject values outside the existing field policy and there are no blanket `From<f64>` or
`Into<f64>` implementations. The legacy helpers `parse_duration`, `Spec::flux_ascending`, `Spec::schedule_seconds`
and the raw-temperature `PreparedRun` functions remain available for downstream source compatibility. Production
top-level runs create one `PhysicalInputs` value and share it between data preparation and execution.

The single barn conversion is `RatePerBarnSecond::from_particle_flux`. Do not repeat the numerical conversion factor
in a caller. New dimensional boundaries belong in this inventory, their compile-pass/fail fixtures and the P16
checker before production use.
