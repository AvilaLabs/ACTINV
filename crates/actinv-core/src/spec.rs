#![allow(non_snake_case)] // field names are the JSON wire format (boundaries_eV, temperature_K)
//! `actinv-spec-1`: the one problem description the CLI, the Python API and the harness all consume.
//! Unknown fields are an error (`deny_unknown_fields`) — a misspelt option must never be silently ignored.
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
        }
    }
}

/// Parse a duration like "5 min" (also "5min", "300", "1 y"). Seconds when no unit is given.
pub fn parse_duration(s: &str) -> Result<f64, String> {
    let t = s.trim();
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
            for selector in &uncertainty.responses {
                let valid = matches!(
                    selector.as_str(),
                    "heat.total" | "heat.alpha" | "heat.beta" | "heat.gamma" | "activity:*"
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
                    | "radiological" | "ledger" | "certificate" => {}
                    value => return Err(format!("unknown options.outputs value '{value}'")),
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
    /// Group fluxes in ascending-energy order, scaled to `total` when given.
    pub fn flux_ascending(&self) -> Vec<f64> {
        let mut f = self.spectrum.flux_per_group.clone();
        if self.spectrum.descending {
            f.reverse();
        }
        if let Some(t) = self.spectrum.total {
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
}
