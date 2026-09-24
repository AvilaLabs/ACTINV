#![allow(non_snake_case)] // field names are the JSON wire format (boundaries_eV, temperature_K)
//! `actinv-spec-1`: the one problem description the CLI, the Python API and the harness all consume.
//! Unknown fields are an error (`deny_unknown_fields`) — a misspelt option must never be silently ignored.
use crate::quantity::{
    AtomsPerGram, ElectronVolts, EnergyBoundaries, FluxMultiplier, Grams, GroupFluxes, Kelvin,
    Seconds,
};
pub use actinv_data::activation::Projectile;
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Spec {
    pub spec: String,
    #[serde(default)]
    pub title: String,
    #[serde(default)]
    pub projectile: Projectile,
    pub library: LibraryRef,
    #[serde(default)]
    pub decay: DecayRef,
    pub material: Material,
    pub spectrum: Spectrum,
    pub schedule: Vec<Step>,
    #[serde(default)]
    pub options: Options,
    #[serde(default)]
    pub photon: PhotonOptions,
    #[serde(default)]
    pub fission_yields: FissionYieldOptions,
    #[serde(default)]
    pub uncertainty: Option<UncertaintyOptions>,
    #[serde(default)]
    pub radiological: Option<RadiologicalOptions>,
    #[serde(default)]
    pub damage: Option<DamageOptions>,
    #[serde(default)]
    pub self_shielding: Option<SelfShieldingOptions>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LibraryRef {
    pub path: String,
    #[serde(default)]
    pub sha256: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct HashedFileRef {
    pub path: String,
    pub sha256: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct UncertaintyOptions {
    pub covariance: HashedFileRef,
    #[serde(default)]
    pub responses: Vec<String>,
    /// Uncertainty channels propagated into the band. Absent or empty selects
    /// `cross_section_mf33` only (the P11/G2 behavior); `decay_constants` and
    /// `fission_yields` add first-order MF=8/MT=457 half-life and MF=8/MT=454
    /// independent-yield channels, each reported with its own coverage.
    #[serde(default)]
    pub channels: Vec<String>,
    #[serde(default = "confidence_95")]
    pub confidence_level: f64,
    #[serde(default)]
    pub require_complete: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RadiologicalOptions {
    pub table: HashedFileRef,
    #[serde(default)]
    pub responses: Vec<String>,
    #[serde(default)]
    pub require_complete: bool,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DamageOptions {
    pub table: HashedFileRef,
    /// Per-element displacement energies in eV, keyed by canonical element symbol ("Fe").
    /// Mandatory for every element the table covers in the material.
    #[serde(default)]
    pub displacement_energy_eV: BTreeMap<String, f64>,
    #[serde(default)]
    pub require_complete: bool,
}

/// Finite-dilution self-shielding (P19): a hash-pinned `actinv-shield-table-1`
/// supplies per-(nuclide, group, channel) Bondarenko factors on the frozen
/// sigma0 x temperature grid. At run time the covered nuclide's effective
/// dilution selects the factor; each group's unresolved-range overlap fraction
/// blends it in. Missing coverage is named in the ledger, never silently
/// treated as factor one.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SelfShieldingOptions {
    pub table: HashedFileRef,
    /// "composition" (default) computes per-nuclide sigma0 from the material
    /// composition; "fixed" applies `sigma0_b` to every covered nuclide.
    #[serde(default = "dilution_composition")]
    pub dilution: String,
    /// Required positive sigma0 in barns for dilution "fixed"; must be null
    /// (or absent) for "composition".
    #[serde(default)]
    pub sigma0_b: Option<f64>,
}

fn dilution_composition() -> String {
    "composition".into()
}

fn confidence_95() -> f64 {
    0.95
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FissionYieldOptions {
    #[serde(default)]
    pub files: Vec<HashedFileRef>,
    #[serde(default = "spectrum_average")]
    pub energy: String,
    #[serde(default)]
    pub fixed_energy_eV: Option<f64>,
}

fn spectrum_average() -> String {
    "spectrum_average".into()
}

impl Default for FissionYieldOptions {
    fn default() -> Self {
        Self {
            files: Vec::new(),
            energy: spectrum_average(),
            fixed_energy_eV: None,
        }
    }
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecayRef {
    pub primary: String,
    #[serde(default)]
    pub fallback: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PhotonOptions {
    #[serde(default = "photon_groups")]
    pub group_structure: String,
    #[serde(default)]
    pub group_boundaries_eV: Option<Vec<f64>>,
    #[serde(default)]
    pub response: Option<HashedFileRef>,
    #[serde(default = "build_up_two")]
    pub build_up_factor: f64,
    #[serde(default = "gamma_cutoff")]
    pub gamma_constant_cutoff_eV: f64,
}
fn photon_groups() -> String {
    "fispact-24".into()
}
fn build_up_two() -> f64 {
    2.0
}
fn gamma_cutoff() -> f64 {
    2.0e4
}
impl Default for PhotonOptions {
    fn default() -> Self {
        Self {
            group_structure: photon_groups(),
            group_boundaries_eV: None,
            response: None,
            build_up_factor: build_up_two(),
            gamma_constant_cutoff_eV: gamma_cutoff(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Material {
    #[serde(default = "one")]
    pub mass_g: f64,
    #[serde(default = "wt")]
    pub basis: String,
    pub composition: BTreeMap<String, f64>,
}
fn one() -> f64 {
    1.0
}
fn wt() -> String {
    "wt_percent".into()
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Spectrum {
    #[serde(default = "g709")]
    pub structure: String,
    pub flux_per_group: Vec<f64>,
    /// total projectile flux (particles cm^-2 s^-1); the group values are scaled to it when given
    #[serde(default)]
    pub total: Option<f64>,
    /// ascending group boundaries in eV; required only when `structure` is "custom"
    #[serde(default)]
    pub boundaries_eV: Option<Vec<f64>>,
    /// true when `flux_per_group` is listed highest-energy first, as FISPACT fluxes files are
    #[serde(default)]
    pub descending: bool,
}

impl Spectrum {
    /// A positive declared `total` needs a group shape to scale; an all-zero shape
    /// would silently discard it and turn the run into pure decay.
    fn check_total_has_shape(&self, label: &str) -> Result<(), String> {
        if self.total.is_some_and(|total| total > 0.0)
            && self.flux_per_group.iter().sum::<f64>() <= 0.0
        {
            return Err(format!(
                "{label}.total is positive but flux_per_group sums to zero: there is no shape to scale"
            ));
        }
        Ok(())
    }

    /// Group fluxes in ascending-energy order, scaled to `total` when given.
    pub fn ascending_flux(&self) -> Vec<f64> {
        let mut f = self.flux_per_group.clone();
        if self.descending {
            f.reverse();
        }
        if let Some(t) = self.total {
            let s: f64 = f.iter().sum();
            if s > 0.0 {
                let k = t / s;
                for v in f.iter_mut() {
                    *v *= k;
                }
            }
        }
        f
    }
}
fn g709() -> String {
    "fispact-709".into()
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Step {
    /// duration with a unit: "300 s", "5 min", "7 h", "1 d", "100 y"
    pub dt: String,
    /// multiplier on the spectrum's total during this step; 0 is cooling
    pub flux: f64,
    /// optional per-step spectrum replacing the base `spectrum` for this step's duration;
    /// the `flux` multiplier scales this spectrum's total. Must share the base spectrum's
    /// structure and group count.
    #[serde(default)]
    pub spectrum: Option<Spectrum>,
    /// optional constant feed during this step: explicit nuclide key -> atoms s^-1 g^-1 of material
    #[serde(default)]
    pub feed: Option<BTreeMap<String, f64>>,
    /// optional first-order removal during this step: nuclide or element key -> s^-1
    #[serde(default)]
    pub removal: Option<BTreeMap<String, f64>>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Options {
    #[serde(default = "auto")]
    pub mode: String,
    #[serde(default = "rate")]
    pub prune: String,
    #[serde(default = "bmin")]
    pub bmin_atoms_per_g: f64,
    #[serde(default = "t293")]
    pub temperature_K: f64,
    #[serde(default = "cram16_order")]
    pub cram_order: u8,
    #[serde(default)]
    pub outputs: Option<Vec<String>>,
    /// Fail closed when a material nuclide lacks shielding-table coverage
    /// (P19). Default false: uncovered nuclides are named in the ledger and
    /// their rates pass through unmodified.
    #[serde(default)]
    pub require_shielding_complete: bool,
    /// Optional per-library-row multiplicative perturbation of collapsed
    /// reaction rates, keyed by activation-library row index (P30
    /// nonlinear sampling). Absent = unperturbed. The applied factors are
    /// named in the run ledger.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub rate_scale: Option<BTreeMap<String, f64>>,
    /// Optional per-nuclide multiplicative perturbation of decay
    /// constants, keyed by explicit-nuclide name such as "Mn56"
    /// (P43 nonlinear sampling). Scaling a nuclide's decay constant
    /// scales its decay edges, activity, decay heat, photon source and
    /// dose responses consistently. Absent or stable nuclides are
    /// named errors at run time. The applied factors are named in the
    /// run ledger.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub decay_scale: Option<BTreeMap<String, f64>>,
    /// Optional per-(parent, product) multiplicative perturbation of
    /// independent fission yields, keyed "<parent>:<product>"
    /// explicit-nuclide names such as "U235:I135" (P43 nonlinear
    /// sampling). The declared yield uncertainty scales with the
    /// yield so relative uncertainty is preserved. A pair absent
    /// from the effective yields is a named error at run time.
    /// The applied factors are named in the run ledger.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub yield_scale: Option<BTreeMap<String, f64>>,
}
fn auto() -> String {
    "auto".into()
}
fn rate() -> String {
    "rate".into()
}
fn bmin() -> f64 {
    1e-8
}
fn t293() -> f64 {
    293.6
}
fn cram16_order() -> u8 {
    16
}
impl Default for Options {
    fn default() -> Self {
        Self {
            mode: auto(),
            prune: rate(),
            bmin_atoms_per_g: bmin(),
            temperature_K: t293(),
            cram_order: cram16_order(),
            outputs: None,
            require_shielding_complete: false,
            rate_scale: None,
            decay_scale: None,
            yield_scale: None,
        }
    }
}

/// Parse a scale-map key as an explicit nuclide (an element key like "Fe"
/// is rejected); returns the (za, liso) identity for solver use.
fn scale_nuclide_key(raw: &str, field: &str) -> Result<(i32, i32), String> {
    match actinv_data::composition::material_key(raw)
        .map_err(|error| format!("{field} key '{raw}': {error}"))?
    {
        actinv_data::composition::MaterialKey::Nuclide { za, liso, .. } => {
            Ok((za, liso))
        }
        _ => Err(format!(
            "{field} key '{raw}' must be an explicit nuclide, e.g. 'Mn56'"
        )),
    }
}

/// Which tracked states a schedule removal key selects: one nuclide or every state of an element.
#[derive(Debug, Clone, Copy)]
pub(crate) enum RemovalSelector {
    Nuclide(i32, i32),
    Element(i32),
}

#[derive(Debug, Clone)]
pub(crate) struct PhysicalStep {
    duration: Seconds,
    multiplier: FluxMultiplier,
    /// per-step spectrum (ascending group fluxes); None = base spectrum
    pub(crate) spectrum_flux: Option<GroupFluxes>,
    /// ((ZA, LISO), atoms s^-1 g^-1) constant feed during this step
    pub(crate) feed: Vec<((i32, i32), f64)>,
    /// (selector, s^-1) first-order removal during this step
    pub(crate) removal: Vec<(RemovalSelector, f64)>,
}

impl PhysicalStep {
    pub(crate) const fn duration(&self) -> Seconds {
        self.duration
    }

    pub(crate) const fn multiplier(&self) -> FluxMultiplier {
        self.multiplier
    }
}

#[derive(Debug, Clone)]
pub(crate) struct PhysicalInputs {
    pub(crate) mass: Grams,
    pub(crate) temperature: Kelvin,
    pub(crate) bmin: AtomsPerGram,
    pub(crate) flux: GroupFluxes,
    pub(crate) schedule: Vec<PhysicalStep>,
    pub(crate) photon_boundaries: EnergyBoundaries,
    pub(crate) gamma_cutoff: ElectronVolts,
    pub(crate) fixed_fission_energy: Option<ElectronVolts>,
}

/// Parse a duration like "5 min" (also "5min", "300", "1 y"). Seconds when no unit is given.
pub fn parse_duration(s: &str) -> Result<f64, String> {
    let t = s.trim();
    // The split search below is quadratic in the length; no real duration is long.
    if t.len() > 64 {
        return Err(format!(
            "duration '{}...' is longer than 64 characters",
            t.chars().take(24).collect::<String>()
        ));
    }
    let split = (0..=t.len())
        .rev()
        .find(|index| {
            t.is_char_boundary(*index)
                && t[..*index].trim().parse::<f64>().is_ok()
                && t[*index..]
                    .trim()
                    .chars()
                    .all(|character| character.is_ascii_alphabetic())
        })
        .ok_or_else(|| format!("bad duration '{s}'"))?;
    let (num, unit) = t.split_at(split);
    let v: f64 = num
        .trim()
        .parse()
        .map_err(|_| format!("bad duration '{s}'"))?;
    let f = match unit.trim().to_lowercase().as_str() {
        "" | "s" | "sec" | "secs" | "second" | "seconds" => 1.0,
        "m" | "min" | "mins" | "minute" | "minutes" => 60.0,
        "h" | "hr" | "hrs" | "hour" | "hours" => 3600.0,
        "d" | "day" | "days" => 86400.0,
        "y" | "yr" | "yrs" | "year" | "years" => 365.25 * 86400.0,
        u => return Err(format!("unknown time unit '{u}' in '{s}'")),
    };
    Ok(v * f)
}

impl Spec {
    pub fn from_json(text: &str) -> Result<Spec, String> {
        let s: Spec = serde_json::from_str(text).map_err(|e| format!("spec: {e}"))?;
        s.validate()?;
        Ok(s)
    }
    pub fn validate(&self) -> Result<(), String> {
        if self.spec != "actinv-spec-1" {
            return Err(format!("unsupported spec version '{}'", self.spec));
        }
        if self.material.composition.is_empty() {
            return Err("material.composition is empty".into());
        }
        if !self.material.mass_g.is_finite() || self.material.mass_g <= 0.0 {
            return Err("material.mass_g must be positive".into());
        }
        if self.library.path.is_empty() {
            return Err("library.path is empty".into());
        }
        if let Some(hash) = &self.library.sha256 {
            if hash.len() != 64 || !hash.bytes().all(|byte| byte.is_ascii_hexdigit()) {
                return Err("library.sha256 must contain 64 hexadecimal digits".into());
            }
        }
        if let Some(uncertainty) = &self.uncertainty {
            if uncertainty.covariance.path.is_empty()
                || uncertainty.covariance.sha256.len() != 64
                || !uncertainty
                    .covariance
                    .sha256
                    .bytes()
                    .all(|byte| byte.is_ascii_hexdigit())
            {
                return Err(
                    "uncertainty.covariance requires a path and a 64-hex-digit sha256".into(),
                );
            }
            if !uncertainty.confidence_level.is_finite()
                || !(0.0..1.0).contains(&uncertainty.confidence_level)
                || uncertainty.confidence_level == 0.0
            {
                return Err(
                    "uncertainty.confidence_level must be strictly between zero and one".into(),
                );
            }
            let mut selectors = std::collections::HashSet::new();
            let mut seen_channels = std::collections::HashSet::new();
            for channel in &uncertainty.channels {
                match channel.as_str() {
                    "cross_section_mf33" | "decay_constants" | "fission_yields" => {}
                    "uncovered_remainder" => {
                        return Err(
                            "uncertainty.channels cannot request uncovered_remainder; it is always named, never propagated"
                                .into(),
                        );
                    }
                    other => {
                        return Err(format!("unknown uncertainty channel '{other}'"));
                    }
                }
                if !seen_channels.insert(channel.as_str()) {
                    return Err(format!("duplicate uncertainty channel '{channel}'"));
                }
            }
            for selector in &uncertainty.responses {
                let valid = matches!(
                    selector.as_str(),
                    "heat.total"
                        | "heat.alpha"
                        | "heat.beta"
                        | "heat.gamma"
                        | "activity:*"
                        | "activity.total"
                ) || selector
                    .strip_prefix("activity:")
                    .is_some_and(|nuclide| !nuclide.is_empty() && nuclide != "*");
                if !valid {
                    return Err(format!(
                        "unknown uncertainty response selector '{selector}'"
                    ));
                }
                if !selectors.insert(selector) {
                    return Err(format!(
                        "duplicate uncertainty response selector '{selector}'"
                    ));
                }
            }
            if !self.projectile.is_neutron() {
                return Err("MF=33 uncertainty is supported only for neutron activation".into());
            }
        }
        if let Some(radiological) = &self.radiological {
            if radiological.table.path.is_empty()
                || radiological.table.sha256.len() != 64
                || !radiological
                    .table
                    .sha256
                    .bytes()
                    .all(|byte| byte.is_ascii_hexdigit())
            {
                return Err("radiological.table requires a path and a 64-hex-digit sha256".into());
            }
            let mut selectors = std::collections::HashSet::new();
            for selector in &radiological.responses {
                if selector.trim().is_empty() {
                    return Err("radiological response selectors must be nonempty".into());
                }
                if !selectors.insert(selector) {
                    return Err(format!(
                        "duplicate radiological response selector '{selector}'"
                    ));
                }
            }
        }
        if let Some(damage) = &self.damage {
            if damage.table.path.is_empty()
                || damage.table.sha256.len() != 64
                || !damage
                    .table
                    .sha256
                    .bytes()
                    .all(|byte| byte.is_ascii_hexdigit())
            {
                return Err("damage.table requires a path and a 64-hex-digit sha256".into());
            }
            for (key, value) in &damage.displacement_energy_eV {
                match actinv_data::composition::material_key(key) {
                    Ok(actinv_data::composition::MaterialKey::Element(symbol))
                        if *key == symbol => {}
                    Ok(_) => {
                        return Err(format!(
                            "damage.displacement_energy_eV key '{key}' is not an element symbol"
                        ));
                    }
                    Err(error) => {
                        return Err(format!(
                            "damage.displacement_energy_eV key '{key}': {error}"
                        ));
                    }
                }
                if !value.is_finite() || *value <= 0.0 {
                    return Err(format!(
                        "damage.displacement_energy_eV for '{key}' must be finite and positive"
                    ));
                }
            }
        }
        if self.decay.primary.is_empty() {
            return Err("decay.primary is empty".into());
        }
        if self
            .material
            .composition
            .values()
            .any(|value| !value.is_finite() || *value < 0.0)
            || self.material.composition.values().sum::<f64>() <= 0.0
        {
            return Err(
                "material.composition values must be finite and nonnegative with a positive total"
                    .into(),
            );
        }
        actinv_data::composition::validate_material_keys(&self.material.composition)?;
        match self.material.basis.as_str() {
            "wt_percent" | "atom_fraction" | "atoms_per_g" => {}
            b => return Err(format!("unknown material.basis '{b}'")),
        }
        if self.schedule.is_empty() {
            return Err("schedule is empty".into());
        }
        for st in &self.schedule {
            let d = parse_duration(&st.dt)?;
            if !d.is_finite() || d < 0.0 {
                return Err(format!("negative duration '{}'", st.dt));
            }
            if !st.flux.is_finite() || st.flux < 0.0 {
                return Err("flux multiplier must be finite and nonnegative".into());
            }
            if let Some(feed) = &st.feed {
                for (key, rate) in feed {
                    match actinv_data::composition::material_key(key) {
                        Ok(actinv_data::composition::MaterialKey::Nuclide { .. }) => {}
                        Ok(actinv_data::composition::MaterialKey::Element(_)) => {
                            return Err(format!(
                                "schedule feed key '{key}' must name an explicit nuclide"
                            ));
                        }
                        Err(error) => {
                            return Err(format!("schedule feed key '{key}': {error}"));
                        }
                    }
                    if !rate.is_finite() || *rate < 0.0 {
                        return Err(format!(
                            "schedule feed rate for '{key}' must be finite and nonnegative"
                        ));
                    }
                }
            }
            if let Some(removal) = &st.removal {
                for (key, rate) in removal {
                    if let Err(error) = actinv_data::composition::material_key(key) {
                        return Err(format!("schedule removal key '{key}': {error}"));
                    }
                    if !rate.is_finite() || *rate < 0.0 {
                        return Err(format!(
                            "schedule removal rate for '{key}' must be finite and nonnegative"
                        ));
                    }
                }
            }
        }
        match self.options.mode.as_str() {
            "auto" | "trace" | "coupled" => {}
            m => return Err(format!("unknown options.mode '{m}'")),
        }
        match self.options.prune.as_str() {
            "rate" | "reach" | "none" => {}
            p => return Err(format!("unknown options.prune '{p}'")),
        }
        if !self.options.bmin_atoms_per_g.is_finite() || self.options.bmin_atoms_per_g < 0.0 {
            return Err("options.bmin_atoms_per_g must be finite and nonnegative".into());
        }
        if !self.options.temperature_K.is_finite() || self.options.temperature_K < 0.0 {
            return Err("options.temperature_K must be finite and nonnegative".into());
        }
        if !matches!(self.options.cram_order, 16 | 48) {
            return Err("options.cram_order must be 16 or 48".into());
        }
        if let Some(scales) = &self.options.rate_scale {
            for (key, factor) in scales {
                if key.parse::<usize>().is_err() {
                    return Err(format!(
                        "options.rate_scale key '{key}' is not a library row index"
                    ));
                }
                if !factor.is_finite() || *factor <= 0.0 {
                    return Err(format!(
                        "options.rate_scale['{key}'] must be finite and positive"
                    ));
                }
            }
        }
        if let Some(scales) = &self.options.decay_scale {
            for (key, factor) in scales {
                scale_nuclide_key(key, "options.decay_scale")?;
                if !factor.is_finite() || *factor <= 0.0 {
                    return Err(format!(
                        "options.decay_scale['{key}'] must be finite and positive"
                    ));
                }
            }
        }
        if let Some(scales) = &self.options.yield_scale {
            for (key, factor) in scales {
                let (parent, product) = key.split_once(':').ok_or_else(|| {
                    format!(
                        "options.yield_scale key '{key}' must be \
                         '<parent>:<product>' nuclide names"
                    )
                })?;
                scale_nuclide_key(parent, "options.yield_scale")?;
                scale_nuclide_key(product, "options.yield_scale")?;
                if !factor.is_finite() || *factor <= 0.0 {
                    return Err(format!(
                        "options.yield_scale['{key}'] must be finite and positive"
                    ));
                }
            }
        }
        if !self.projectile.is_neutron() && self.options.temperature_K != 0.0 {
            return Err(format!(
                "{} specs require options.temperature_K: 0",
                self.projectile.name()
            ));
        }
        if !self.projectile.is_neutron() && !self.fission_yields.files.is_empty() {
            return Err(format!(
                "fission-yield files are not supported for {} activation",
                self.projectile.name()
            ));
        }
        if let Some(outputs) = &self.options.outputs {
            for output in outputs {
                match output.as_str() {
                    "inventory" | "activity" | "heat" | "photons" | "dose" | "pathways"
                    | "radiological" | "damage" | "ledger" | "certificate" => {}
                    value => return Err(format!("unknown options.outputs value '{value}'")),
                }
            }
            if outputs.iter().any(|o| o == "damage") && self.damage.is_none() {
                return Err("options.outputs 'damage' requires a damage section".into());
            }
        }
        if let Some(shielding) = &self.self_shielding {
            // Checked here, not only at prepare time, so validate-only tooling agrees.
            if !self.projectile.is_neutron() {
                return Err(format!(
                    "self_shielding is neutron-only; {} specs cannot declare it",
                    self.projectile.name()
                ));
            }
            match shielding.dilution.as_str() {
                "composition" => {
                    if shielding.sigma0_b.is_some() {
                        return Err(
                            "self_shielding dilution 'composition' takes sigma0_b: null".into()
                        );
                    }
                }
                "fixed" => match shielding.sigma0_b {
                    Some(value) if value.is_finite() && value > 0.0 => {}
                    _ => {
                        return Err(
                            "self_shielding dilution 'fixed' requires a positive finite sigma0_b"
                                .into(),
                        );
                    }
                },
                value => {
                    return Err(format!("unknown self_shielding.dilution '{value}'"));
                }
            }
        }
        if self.spectrum.structure == "custom" {
            let b = self
                .spectrum
                .boundaries_eV
                .as_ref()
                .ok_or("custom spectrum needs boundaries_eV")?;
            if b.len() != self.spectrum.flux_per_group.len() + 1 {
                return Err(format!(
                    "boundaries_eV has {} entries; {} groups need {}",
                    b.len(),
                    self.spectrum.flux_per_group.len(),
                    self.spectrum.flux_per_group.len() + 1
                ));
            }
            if b.iter().any(|value| !value.is_finite()) || b.windows(2).any(|w| w[1] <= w[0]) {
                return Err("boundaries_eV must be finite and strictly ascending".into());
            }
        } else {
            let (groups, expected_projectile) = match self.spectrum.structure.as_str() {
                "fispact-709" => (709, Projectile::Neutron),
                "fispact-162" if !self.projectile.is_neutron() => (162, self.projectile),
                "fispact-162" => {
                    return Err("fispact-162 is reserved for charged-particle spectra".into());
                }
                value => return Err(format!("unknown spectrum.structure '{value}'")),
            };
            if self.projectile != expected_projectile {
                return Err(format!(
                    "{} requires projectile '{}', got '{}'",
                    self.spectrum.structure,
                    expected_projectile.name(),
                    self.projectile.name()
                ));
            }
            if self.spectrum.flux_per_group.len() != groups {
                return Err(format!(
                    "{} requires {groups} flux values, got {}",
                    self.spectrum.structure,
                    self.spectrum.flux_per_group.len()
                ));
            }
        }
        if self
            .spectrum
            .flux_per_group
            .iter()
            .any(|f| !f.is_finite() || *f < 0.0)
        {
            return Err("group fluxes must be finite and nonnegative".into());
        }
        if self
            .spectrum
            .total
            .is_some_and(|total| !total.is_finite() || total < 0.0)
        {
            return Err("spectrum.total must be finite and nonnegative".into());
        }
        self.spectrum.check_total_has_shape("spectrum")?;
        match self.photon.group_structure.as_str() {
            "fispact-24" => {
                if self.photon.group_boundaries_eV.is_some() {
                    return Err(
                        "photon.group_boundaries_eV is only valid with group_structure 'custom'"
                            .into(),
                    );
                }
            }
            "custom" => {
                let b = self
                    .photon
                    .group_boundaries_eV
                    .as_ref()
                    .ok_or("custom photon group structure needs group_boundaries_eV")?;
                if b.len() < 2
                    || b[0] < 0.0
                    || b.iter().any(|v| !v.is_finite())
                    || b.windows(2).any(|w| w[1] <= w[0])
                {
                    return Err("photon.group_boundaries_eV must be finite, nonnegative and strictly increasing".into());
                }
            }
            value => return Err(format!("unknown photon.group_structure '{value}'")),
        }
        if !self.photon.build_up_factor.is_finite() || self.photon.build_up_factor <= 0.0 {
            return Err("photon.build_up_factor must be positive".into());
        }
        if !self.photon.gamma_constant_cutoff_eV.is_finite()
            || self.photon.gamma_constant_cutoff_eV < 0.0
        {
            return Err("photon.gamma_constant_cutoff_eV must be nonnegative".into());
        }
        if let Some(r) = &self.photon.response {
            if r.path.is_empty()
                || r.sha256.len() != 64
                || !r.sha256.bytes().all(|b| b.is_ascii_hexdigit())
            {
                return Err("photon.response requires a path and a 64-hex-digit sha256".into());
            }
        }
        let mut yield_paths = std::collections::HashSet::new();
        for reference in &self.fission_yields.files {
            if reference.path.is_empty()
                || reference.sha256.len() != 64
                || !reference
                    .sha256
                    .bytes()
                    .all(|byte| byte.is_ascii_hexdigit())
            {
                return Err(
                    "every fission_yields file requires a path and a 64-hex-digit sha256".into(),
                );
            }
            if !yield_paths.insert(&reference.path) {
                return Err(format!(
                    "duplicate fission_yields file path '{}'",
                    reference.path
                ));
            }
        }
        match self.fission_yields.energy.as_str() {
            "spectrum_average" => {
                if self.fission_yields.fixed_energy_eV.is_some() {
                    return Err(
                        "fission_yields.fixed_energy_eV is forbidden with spectrum_average".into(),
                    );
                }
            }
            "fixed" => {
                let energy = self
                    .fission_yields
                    .fixed_energy_eV
                    .ok_or("fission_yields.energy 'fixed' requires fixed_energy_eV")?;
                if !energy.is_finite() || energy < 0.0 {
                    return Err(
                        "fission_yields.fixed_energy_eV must be finite and nonnegative".into(),
                    );
                }
            }
            value => return Err(format!("unknown fission_yields.energy '{value}'")),
        }
        if self.fission_yields.files.is_empty()
            && (self.fission_yields.energy != "spectrum_average"
                || self.fission_yields.fixed_energy_eV.is_some())
        {
            return Err("empty fission_yields.files requires the default energy settings".into());
        }
        Ok(())
    }

    pub(crate) fn physical_inputs(&self) -> Result<PhysicalInputs, String> {
        let mass = Grams::new(self.material.mass_g)
            .map_err(|_| "material.mass_g must be positive".to_string())?;
        let temperature = Kelvin::new(self.options.temperature_K)
            .map_err(|_| "options.temperature_K must be finite and nonnegative".to_string())?;
        let bmin = AtomsPerGram::new(self.options.bmin_atoms_per_g)
            .map_err(|_| "options.bmin_atoms_per_g must be finite and nonnegative".to_string())?;
        let flux = GroupFluxes::new(self.flux_ascending())
            .map_err(|_| "group fluxes must be finite and nonnegative".to_string())?;
        let schedule = self
            .schedule
            .iter()
            .map(|step| {
                let parsed = parse_duration(&step.dt)?;
                let duration =
                    Seconds::new(parsed).map_err(|_| format!("negative duration '{}'", step.dt))?;
                let multiplier = FluxMultiplier::new(step.flux)
                    .map_err(|_| "flux multiplier must be finite and nonnegative".to_string())?;
                let feed = step
                    .feed
                    .iter()
                    .flatten()
                    .map(
                        |(key, rate)| match actinv_data::composition::material_key(key) {
                            Ok(actinv_data::composition::MaterialKey::Nuclide {
                                za, liso, ..
                            }) => Ok(((za, liso), *rate)),
                            Ok(actinv_data::composition::MaterialKey::Element(_)) => Err(format!(
                                "schedule feed key '{key}' must name an explicit nuclide"
                            )),
                            Err(error) => Err(format!("schedule feed key '{key}': {error}")),
                        },
                    )
                    .collect::<Result<Vec<_>, String>>()?;
                let removal = step
                    .removal
                    .iter()
                    .flatten()
                    .map(
                        |(key, rate)| match actinv_data::composition::material_key(key) {
                            Ok(actinv_data::composition::MaterialKey::Nuclide {
                                za, liso, ..
                            }) => Ok((RemovalSelector::Nuclide(za, liso), *rate)),
                            Ok(actinv_data::composition::MaterialKey::Element(symbol)) => {
                                let z =
                                    actinv_data::composition::z_of(&symbol).ok_or_else(|| {
                                        format!("unknown element symbol in removal key '{key}'")
                                    })?;
                                Ok((RemovalSelector::Element(z), *rate))
                            }
                            Err(error) => Err(format!("schedule removal key '{key}': {error}")),
                        },
                    )
                    .collect::<Result<Vec<_>, String>>()?;
                let spectrum_flux = step
                    .spectrum
                    .as_ref()
                    .map(|s| {
                        s.check_total_has_shape("schedule step spectrum")?;
                        if s.structure != self.spectrum.structure {
                            return Err(format!(
                                "schedule step spectrum.structure '{}' must match the base spectrum's '{}'",
                                s.structure, self.spectrum.structure
                            ));
                        }
                        let f = s.ascending_flux();
                        if f.len() != flux.values().len() {
                            return Err(format!(
                                "schedule step spectrum has {} groups; the base spectrum declares {}",
                                f.len(),
                                flux.values().len()
                            ));
                        }
                        GroupFluxes::new(f)
                            .map_err(|_| "schedule step spectrum group fluxes must be finite and nonnegative".to_string())
                    })
                    .transpose()?;
                Ok(PhysicalStep {
                    duration,
                    multiplier,
                    spectrum_flux,
                    feed,
                    removal,
                })
            })
            .collect::<Result<Vec<_>, String>>()?;
        let photon_boundaries = EnergyBoundaries::new(self.photon_boundaries()).map_err(|_| {
            "photon.group_boundaries_eV must be finite, nonnegative and strictly increasing"
                .to_string()
        })?;
        let gamma_cutoff = ElectronVolts::new(self.photon.gamma_constant_cutoff_eV)
            .map_err(|_| "photon.gamma_constant_cutoff_eV must be nonnegative".to_string())?;
        let fixed_fission_energy = self
            .fission_yields
            .fixed_energy_eV
            .map(ElectronVolts::new)
            .transpose()
            .map_err(|_| {
                "fission_yields.fixed_energy_eV must be finite and nonnegative".to_string()
            })?;
        Ok(PhysicalInputs {
            mass,
            temperature,
            bmin,
            flux,
            schedule,
            photon_boundaries,
            gamma_cutoff,
            fixed_fission_energy,
        })
    }
    /// Group fluxes in ascending-energy order, scaled to `total` when given.
    pub fn flux_ascending(&self) -> Vec<f64> {
        self.spectrum.ascending_flux()
    }
    pub fn schedule_seconds(&self) -> Vec<(f64, f64)> {
        self.schedule
            .iter()
            .map(|s| (parse_duration(&s.dt).unwrap_or(0.0), s.flux))
            .collect()
    }

    pub fn photon_boundaries(&self) -> Vec<f64> {
        match &self.photon.group_boundaries_eV {
            Some(v) => v.clone(),
            None => crate::photon::FISPACT_24_BOUNDARIES_EV.to_vec(),
        }
    }
}

#[cfg(test)]
mod duration_tests {
    use super::{parse_duration, Projectile, Spec};

    fn minimal_spec() -> serde_json::Value {
        serde_json::json!({
            "spec": "actinv-spec-1",
            "library": {"path": "activation.npz"},
            "decay": {"primary": "decay.endf"},
            "material": {
                "basis": "wt_percent",
                "composition": {"FE": 100.0},
            },
            "spectrum": {
                "structure": "custom",
                "boundaries_eV": [1.0, 2.0],
                "flux_per_group": [1.0],
            },
            "schedule": [{"dt": "1 s", "flux": 1.0}],
        })
    }

    #[test]
    fn scientific_notation_is_not_a_unit_suffix() {
        assert_eq!(parse_duration("1e-8 s").unwrap(), 1e-8);
        assert_eq!(
            parse_duration("2.5E+3ms").unwrap_err(),
            "unknown time unit 'ms' in '2.5E+3ms'"
        );
        assert_eq!(parse_duration("3e2").unwrap(), 300.0);
    }

    #[test]
    fn equivalent_duration_spellings_share_the_physical_boundary() {
        let spellings = ["300 s", "300s", "5 min", "5min", "0.08333333333333333 h"];
        for spelling in spellings {
            let seconds = parse_duration(spelling).unwrap();
            assert!((seconds - 300.0).abs() <= f64::EPSILON * 300.0);
        }

        let mut value = minimal_spec();
        for invalid in ["-1 s", "nan s", "inf s", "1 fortnight"] {
            value["schedule"][0]["dt"] = serde_json::Value::String(invalid.into());
            assert!(Spec::from_json(&value.to_string()).is_err(), "{invalid}");
        }
    }

    #[test]
    fn contradictory_or_unbounded_inputs_fail_validation() {
        // A positive total with no shape used to be dropped silently (pure-decay run).
        let mut value = minimal_spec();
        value["spectrum"]["flux_per_group"] = serde_json::json!([0.0]);
        value["spectrum"]["total"] = serde_json::json!(1.0e10);
        assert!(Spec::from_json(&value.to_string())
            .unwrap_err()
            .contains("no shape to scale"));
        // Duration parsing is quadratic in length; a long string is refused up front.
        let mut value = minimal_spec();
        value["schedule"][0]["dt"] = serde_json::json!("x".repeat(100_000));
        assert!(Spec::from_json(&value.to_string())
            .unwrap_err()
            .contains("longer than 64"));
        // Self-shielding is neutron-only at validation, not only at prepare time.
        let mut value = minimal_spec();
        value["projectile"] = serde_json::json!("proton");
        value["options"] = serde_json::json!({"temperature_K": 0.0});
        value["self_shielding"] = serde_json::json!({
            "table": {"path": "shield.json", "sha256": "0".repeat(64)},
            "dilution": "composition"
        });
        assert!(Spec::from_json(&value.to_string())
            .unwrap_err()
            .contains("neutron-only"));
    }

    #[test]
    fn misspelt_composition_element_fails_validation() {
        let mut value = minimal_spec();
        value["material"]["composition"] = serde_json::json!({"Fe": 99.0, "Coo": 1.0});
        assert!(Spec::from_json(&value.to_string())
            .unwrap_err()
            .contains("unknown element symbol"));
        value["material"]["composition"] = serde_json::json!({"Fe": 99.0, "Tc": 1.0});
        assert!(Spec::from_json(&value.to_string())
            .unwrap_err()
            .contains("no natural isotopic abundance"));
    }

    #[test]
    fn omitted_projectile_is_backward_compatible_neutron() {
        let spec = Spec::from_json(&minimal_spec().to_string()).unwrap();
        assert_eq!(spec.projectile, Projectile::Neutron);
        assert_eq!(spec.options.temperature_K, 293.6);
    }

    #[test]
    fn charged_projectiles_require_zero_kelvin_and_no_fission_yields() {
        let mut value = minimal_spec();
        value["projectile"] = serde_json::json!("proton");
        assert!(Spec::from_json(&value.to_string())
            .unwrap_err()
            .contains("temperature_K: 0"));

        value["options"] = serde_json::json!({"temperature_K": 0.0});
        assert_eq!(
            Spec::from_json(&value.to_string()).unwrap().projectile,
            Projectile::Proton
        );

        value["fission_yields"] = serde_json::json!({
            "files": [{"path": "yield.endf", "sha256": "0".repeat(64)}],
        });
        assert!(Spec::from_json(&value.to_string())
            .unwrap_err()
            .contains("not supported for proton"));
    }

    #[test]
    fn feed_and_removal_are_optional_and_validated() {
        let spec = Spec::from_json(&minimal_spec().to_string()).unwrap();
        assert!(spec.schedule[0].feed.is_none() && spec.schedule[0].removal.is_none());

        let mut value = minimal_spec();
        value["schedule"][0]["feed"] = serde_json::json!({"Co60": 1e10, "Ta180m": 2.0});
        value["schedule"][0]["removal"] = serde_json::json!({"Co60": 1e-9, "Ni": 1e-8});
        let spec = Spec::from_json(&value.to_string()).unwrap();
        let feed = spec.schedule[0].feed.as_ref().unwrap();
        assert_eq!(feed.len(), 2);
        assert!(spec.schedule[0]
            .removal
            .as_ref()
            .unwrap()
            .contains_key("Ni"));
    }

    #[test]
    fn feed_requires_explicit_nuclides() {
        let mut value = minimal_spec();
        value["schedule"][0]["feed"] = serde_json::json!({"Fe": 1.0});
        assert!(Spec::from_json(&value.to_string())
            .unwrap_err()
            .contains("must name an explicit nuclide"));
        value["schedule"][0]["feed"] = serde_json::json!({"Xx99": 1.0});
        assert!(Spec::from_json(&value.to_string()).is_err());
        value["schedule"][0]["feed"] = serde_json::json!({"Co60": -1.0});
        assert!(Spec::from_json(&value.to_string())
            .unwrap_err()
            .contains("finite and nonnegative"));
        value["schedule"][0]["feed"] = serde_json::json!({"Co60": null});
        assert!(Spec::from_json(&value.to_string()).is_err());
    }

    #[test]
    fn per_step_spectrum_parses_and_validates() {
        // accepted when structure and group count match the base spectrum
        let mut value = minimal_spec();
        value["schedule"][0]["spectrum"] = serde_json::json!({
            "structure": "custom",
            "boundaries_eV": [1.0, 2.0],
            "flux_per_group": [2.0],
        });
        let spec = Spec::from_json(&value.to_string()).unwrap();
        let physical = spec.physical_inputs().unwrap();
        assert_eq!(
            physical.schedule[0]
                .spectrum_flux
                .as_ref()
                .unwrap()
                .values(),
            &[2.0]
        );
    }

    #[test]
    fn per_step_spectrum_requires_matching_structure_and_groups() {
        // structure mismatch is rejected
        let mut value = minimal_spec();
        value["schedule"][0]["spectrum"] = serde_json::json!({
            "structure": "fispact-709",
            "flux_per_group": [1.0],
        });
        assert!(Spec::from_json(&value.to_string())
            .and_then(|s| s.physical_inputs())
            .unwrap_err()
            .contains("must match the base spectrum"));

        // group-count mismatch is rejected
        let mut value = minimal_spec();
        value["schedule"][0]["spectrum"] = serde_json::json!({
            "structure": "custom",
            "boundaries_eV": [1.0, 2.0, 3.0],
            "flux_per_group": [1.0, 1.0],
        });
        assert!(Spec::from_json(&value.to_string())
            .and_then(|s| s.physical_inputs())
            .unwrap_err()
            .contains("base spectrum declares 1"));
    }

    #[test]
    fn per_step_spectrum_accepts_uncertainty() {
        // The joint MF=33 collapse covers (spectrum, row) parameters, so a
        // per-step spectrum combined with uncertainty is a valid spec.
        let mut value = minimal_spec();
        value["schedule"][0]["spectrum"] = serde_json::json!({
            "structure": "custom",
            "boundaries_eV": [1.0, 2.0],
            "flux_per_group": [1.0],
        });
        value["uncertainty"] = serde_json::json!({
            "covariance": {"path": "cov.npz", "sha256": "0".repeat(64)},
        });
        assert!(Spec::from_json(&value.to_string()).is_ok());
    }

    #[test]
    fn removal_rates_are_finite_and_nonnegative() {
        let mut value = minimal_spec();
        value["schedule"][0]["removal"] = serde_json::json!({"Co60": -1e-9});
        assert!(Spec::from_json(&value.to_string())
            .unwrap_err()
            .contains("finite and nonnegative"));
        value["schedule"][0]["removal"] = serde_json::json!({"Co60": "1e-9"});
        assert!(Spec::from_json(&value.to_string()).is_err());
    }

    #[test]
    fn self_shielding_dilution_modes_are_validated() {
        let table = || serde_json::json!({"path": "t.json", "sha256": "0".repeat(64)});
        let mut value = minimal_spec();
        value["self_shielding"] = serde_json::json!({"table": table(), "dilution": "fixed"});
        assert!(Spec::from_json(&value.to_string())
            .unwrap_err()
            .contains("positive finite sigma0_b"));

        value["self_shielding"]["sigma0_b"] = serde_json::json!(-0.5);
        assert!(Spec::from_json(&value.to_string()).is_err());

        value["self_shielding"]["sigma0_b"] = serde_json::json!(0.1);
        assert!(Spec::from_json(&value.to_string()).is_ok());

        value["self_shielding"]["dilution"] = serde_json::json!("composition");
        assert!(Spec::from_json(&value.to_string())
            .unwrap_err()
            .contains("sigma0_b: null"));

        value["self_shielding"]["sigma0_b"] = serde_json::Value::Null;
        let spec = Spec::from_json(&value.to_string()).unwrap();
        assert_eq!(spec.self_shielding.unwrap().dilution, "composition");

        value["self_shielding"]["dilution"] = serde_json::json!("bogus");
        assert!(Spec::from_json(&value.to_string())
            .unwrap_err()
            .contains("unknown self_shielding.dilution"));
    }

    #[test]
    fn self_shielding_accepts_uncertainty_combination() {
        let mut value = minimal_spec();
        value["self_shielding"] = serde_json::json!({
            "table": {"path": "t.json", "sha256": "0".repeat(64)},
            "dilution": "fixed",
            "sigma0_b": 0.1,
        });
        value["uncertainty"] = serde_json::json!({
            "covariance": {"path": "c.npy", "sha256": "0".repeat(64)}
        });
        assert!(Spec::from_json(&value.to_string()).is_ok());
    }

    #[test]
    fn decay_scale_requires_explicit_nuclide_positive_factors() {
        let mut value = minimal_spec();
        value["options"] = serde_json::json!({"decay_scale": {"Mn56": 1.5, "Co60m1": 0.8}});
        let spec = Spec::from_json(&value.to_string()).unwrap();
        assert_eq!(spec.options.decay_scale.as_ref().unwrap()["Mn56"], 1.5);

        for (key, want) in [
            ("Fe", "must be an explicit nuclide"),
            ("Mn56:Co60", "malformed explicit nuclide key"),
            ("", "malformed material composition key"),
        ] {
            let mut v = minimal_spec();
            v["options"] = serde_json::json!({"decay_scale": {key: 1.0}});
            let e = Spec::from_json(&v.to_string()).unwrap_err();
            assert!(e.contains(want), "{key}: {e}");
        }
        for bad in [serde_json::json!(0.0), serde_json::json!(-1.0), serde_json::json!("x")] {
            let mut v = minimal_spec();
            v["options"] = serde_json::json!({"decay_scale": {"Mn56": bad}});
            assert!(Spec::from_json(&v.to_string()).is_err());
        }
    }

    #[test]
    fn yield_scale_requires_parent_product_nuclide_pairs() {
        let mut value = minimal_spec();
        value["options"] =
            serde_json::json!({"yield_scale": {"U235:I135": 2.0, "U235:Xe135m1": 1.1}});
        let spec = Spec::from_json(&value.to_string()).unwrap();
        assert_eq!(spec.options.yield_scale.as_ref().unwrap()["U235:I135"], 2.0);

        for key in ["U235", "U235:", ":I135", "U235:I135:Xe135", "U235-I135"] {
            let mut v = minimal_spec();
            v["options"] = serde_json::json!({"yield_scale": {key: 2.0}});
            assert!(
                Spec::from_json(&v.to_string()).is_err(),
                "key '{key}' must be refused"
            );
        }
        for key in ["U235:Fe", "Fe:I135"] {
            let mut v = minimal_spec();
            v["options"] = serde_json::json!({"yield_scale": {key: 2.0}});
            let e = Spec::from_json(&v.to_string()).unwrap_err();
            assert!(e.contains("explicit nuclide"), "{key}: {e}");
        }
        let mut v = minimal_spec();
        v["options"] = serde_json::json!({"yield_scale": {"U235:I135": 0.0}});
        assert!(Spec::from_json(&v.to_string()).is_err());
    }

    #[test]
    fn all_scale_maps_coexist_on_one_spec() {
        let mut value = minimal_spec();
        value["options"] = serde_json::json!({
            "rate_scale": {"0": 1.1},
            "decay_scale": {"Mn56": 1.5},
            "yield_scale": {"U235:I135": 2.0},
        });
        let spec = Spec::from_json(&value.to_string()).unwrap();
        assert!(spec.options.rate_scale.is_some());
        assert!(spec.options.decay_scale.is_some());
        assert!(spec.options.yield_scale.is_some());
    }
}
