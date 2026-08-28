#![allow(non_snake_case)] // field names are the JSON wire format (Z, A, LISO, heat_W_per_g)
//! spec -> result. This is the single path the CLI, the Python API and the harness all take (P5 G3).
use crate::chain::{self, RateLedger};
use crate::cram::{step as cram_step, step_with_tangents, Cram};
use crate::photon::{self, PhotonDiagnostics, PhotonResponse, PhotonSourceOut};
use crate::radiological::{PreparedRadiologicalTable, RadiologicalStepOut};
use crate::sparse::Csc;
use crate::spec::{
    DecayRef, FissionYieldOptions, HashedFileRef, LibraryRef, PhotonOptions, Projectile,
    RadiologicalOptions, Spec, UncertaintyOptions,
};
use crate::uncertainty::{
    self as uncertainty_report, BandInput, SensitivityOut, SensitivityParameter, StepUncertainty,
};
use actinv_data::{
    composition, covariance, decay, fission,
    groups::GroupStructure,
    library::{self, ReactionLibrary},
    prepared as prepared_data,
};
use num_complex::Complex64 as C64;
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, HashMap};
use std::io::Read;
use std::sync::{Mutex, OnceLock};

pub const EV: f64 = 1.602176634e-19;

/// P14 control-only timing. The environment switch is intentionally undocumented for users and does not alter the
/// result wire format. Disabled runs do not call the clock at stage boundaries.
#[derive(Default)]
struct RunProfiler {
    enabled: bool,
    stages_ms: BTreeMap<&'static str, f64>,
}

impl RunProfiler {
    fn from_environment() -> Self {
        Self {
            enabled: std::env::var_os("ACTINV_P14_PROFILE").is_some(),
            stages_ms: BTreeMap::new(),
        }
    }

    fn disabled() -> Self {
        Self::default()
    }

    fn start(&self) -> Option<std::time::Instant> {
        self.enabled.then(std::time::Instant::now)
    }

    fn finish(&mut self, name: &'static str, started: Option<std::time::Instant>) {
        if let Some(started) = started {
            self.stages_ms
                .insert(name, started.elapsed().as_secs_f64() * 1e3);
        }
    }

    fn emit(&self, total: std::time::Duration) {
        if !self.enabled {
            return;
        }
        let total_ms = total.as_secs_f64() * 1e3;
        let accounted_ms: f64 = self.stages_ms.values().sum();
        eprintln!(
            "ACTINV_P14_CORE_PROFILE {}",
            serde_json::json!({
                "schema": "actinv-p14-core-profile-1",
                "stages_ms": self.stages_ms,
                "accounted_ms": accounted_ms,
                "total_core_ms": total_ms,
                "uninstrumented_core_ms": (total_ms - accounted_ms).max(0.0),
            })
        );
    }
}

#[derive(serde::Serialize)]
pub struct NuclideOut {
    pub nuclide: String,
    pub Z: i32,
    pub A: i32,
    pub LISO: i32,
    pub atoms_per_g: f64,
}
#[derive(serde::Serialize)]
pub struct Heat {
    pub total: f64,
    pub alpha: f64,
    pub beta: f64,
    pub gamma: f64,
}
#[derive(serde::Serialize)]
pub struct StepOut {
    pub step: usize,
    pub t_s: f64,
    pub flux: f64,
    pub flux_weighted_time_s: f64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub fluence_n_cm2: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub fluence_particles_cm2: Option<f64>,
    pub inventory: Vec<NuclideOut>,
    pub activity_Bq_per_g: BTreeMap<String, f64>,
    pub heat_W_per_g: Heat,
    pub leakage_atoms_per_g: f64,
    pub negative_atoms_zeroed: f64,
    pub total_atoms_per_g: f64,
    pub n_states_populated: usize,
    /// CRAM's approximation floors at alpha0, so any population below alpha0 * max(N) is indistinguishable from zero
    /// by this method. Reported, never silently removed.
    pub numerical_floor_atoms_per_g: f64,
    pub n_states_below_floor: usize,
    pub atoms_below_floor: f64,
    pub heat_bound_from_below_floor_W_per_g: f64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub photon_source: Option<PhotonSourceOut>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub uncertainty: Option<StepUncertainty>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub radiological: Option<RadiologicalStepOut>,
}
#[derive(serde::Serialize)]
pub struct Pathway {
    pub from: String,
    pub first_product: String,
    pub atoms_per_g: f64,
    pub fraction: f64,
}

#[derive(serde::Serialize)]
pub struct RunResult {
    pub spec_title: String,
    pub entry_point: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub projectile: Option<String>,
    pub mode: String,
    pub pruned_states: usize,
    pub total_states: usize,
    pub steps: Vec<StepOut>,
    /// Per step: nuclide -> production chains ranked by contribution, labelled by the bulk isotope the chain started
    /// from and the first product it made. Trace mode only; the system is linear in the source there, so the
    /// contributions are exact and sum to the nuclide's population.
    pub pathways: Vec<BTreeMap<String, Vec<Pathway>>>,
    /// largest relative disagreement between the summed pathway contributions and the main solve
    pub pathway_closure: f64,
    pub ledger: serde_json::Value,
    pub certificate: serde_json::Value,
    pub ms: f64,
}

fn name_of(za: i32, liso: i32) -> String {
    let s = composition::symbol_of(za / 1000);
    if liso > 0 {
        format!("{s}{}m{liso}", za % 1000)
    } else {
        format!("{s}{}", za % 1000)
    }
}

fn index_path(library_path: &str) -> String {
    match library_path.strip_suffix(".npz") {
        Some(stem) => format!("{stem}_index.json"),
        None => format!("{library_path}_index.json"),
    }
}

fn file_sha256(path: &str) -> Result<String, String> {
    type CacheKey = (String, u64, u128);
    static CACHE: OnceLock<Mutex<HashMap<CacheKey, String>>> = OnceLock::new();
    let metadata = std::fs::metadata(path).map_err(|e| format!("cannot stat {path}: {e}"))?;
    let modified = metadata
        .modified()
        .ok()
        .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    let key = (path.to_string(), metadata.len(), modified);
    let cache = CACHE.get_or_init(|| Mutex::new(HashMap::new()));
    if let Some(value) = cache
        .lock()
        .map_err(|_| "SHA-256 cache lock poisoned")?
        .get(&key)
        .cloned()
    {
        return Ok(value);
    }
    let mut file = std::fs::File::open(path).map_err(|e| format!("cannot hash {path}: {e}"))?;
    let mut hasher = Sha256::new();
    let mut buffer = [0u8; 1024 * 1024];
    loop {
        let n = file
            .read(&mut buffer)
            .map_err(|e| format!("cannot hash {path}: {e}"))?;
        if n == 0 {
            break;
        }
        hasher.update(&buffer[..n]);
    }
    let value = format!("{:x}", hasher.finalize());
    let after = std::fs::metadata(path).map_err(|e| format!("cannot restat {path}: {e}"))?;
    let after_modified = after
        .modified()
        .ok()
        .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    if after.len() != key.1 || after_modified != key.2 {
        return Err(format!("input changed while hashing: {path}"));
    }
    cache
        .lock()
        .map_err(|_| "SHA-256 cache lock poisoned")?
        .insert(key, value.clone());
    Ok(value)
}

fn verify_hash(path: &str, declared: Option<&str>) -> Result<String, String> {
    let computed = file_sha256(path)?;
    if let Some(expected) = declared {
        if !computed.eq_ignore_ascii_case(expected) {
            return Err(format!(
                "SHA-256 mismatch for {path}: declared {expected}, computed {computed}"
            ));
        }
    }
    Ok(computed)
}

/// CRAM coefficients from the generated table (controls/gen_cram.py); never transcribed by hand.
fn cram(order: u8) -> Cram {
    use crate::cram_coeffs::{
        CRAM16_ALPHA, CRAM16_ALPHA0, CRAM16_THETA, CRAM48_ALPHA, CRAM48_ALPHA0, CRAM48_THETA,
    };
    fn from_pairs(alpha0: f64, theta: &[(f64, f64)], alpha: &[(f64, f64)]) -> Cram {
        Cram {
            alpha0,
            theta: theta.iter().map(|(r, i)| C64::new(*r, *i)).collect(),
            alpha: alpha.iter().map(|(r, i)| C64::new(*r, *i)).collect(),
        }
    }
    match order {
        16 => from_pairs(CRAM16_ALPHA0, &CRAM16_THETA, &CRAM16_ALPHA),
        48 => from_pairs(CRAM48_ALPHA0, &CRAM48_THETA, &CRAM48_ALPHA),
        _ => unreachable!("spec validation accepts only CRAM-16/48"),
    }
}

fn fission_average_energy_eV<L: ReactionLibrary + ?Sized>(
    library: &L,
    targets: &[(i32, i32)],
    phi: &[f64],
    parent: (i32, i32),
) -> Result<Option<f64>, String> {
    let row_index = library.rows().iter().position(|row| {
        row.mt == 18 && row.zap == 0 && targets.get(row.target).is_some_and(|key| *key == parent)
    });
    let Some(row_index) = row_index else {
        return Ok(None);
    };
    library
        .fission_average_energy_ev(row_index, phi)
        .map_err(|error| {
            format!(
                "cannot compute spectrum-average fission energy for {}_{}: {error}",
                parent.0, parent.1
            )
        })
}

enum ActivationLibrary {
    Dense(library::Library),
    Groupwise(prepared_data::PreparedLibrary),
    Collapsed(prepared_data::CollapsedLibrary),
}

fn group_structure_mismatch(requested: Option<&str>, stored: Option<&str>) -> bool {
    matches!((requested, stored), (Some(requested), Some(stored)) if requested != "custom" && requested != stored)
}

impl ActivationLibrary {
    fn dense(&self) -> Option<&library::Library> {
        match self {
            Self::Dense(library) => Some(library),
            Self::Groupwise(_) | Self::Collapsed(_) => None,
        }
    }

    fn validate_flux(&self, phi: &[f64]) -> Result<(), String> {
        match self {
            Self::Collapsed(library) => library.validate_flux(phi),
            Self::Dense(_) | Self::Groupwise(_) => Ok(()),
        }
    }
}

impl ReactionLibrary for ActivationLibrary {
    fn rows(&self) -> &[library::Row] {
        match self {
            Self::Dense(library) => library.rows(),
            Self::Groupwise(library) => library.rows(),
            Self::Collapsed(library) => library.rows(),
        }
    }

    fn group_count(&self) -> usize {
        match self {
            Self::Dense(library) => ReactionLibrary::group_count(library),
            Self::Groupwise(library) => library.group_count(),
            Self::Collapsed(library) => library.group_count(),
        }
    }

    fn boundaries_ev(&self) -> &[f64] {
        match self {
            Self::Dense(library) => library.boundaries_ev(),
            Self::Groupwise(library) => library.boundaries_ev(),
            Self::Collapsed(library) => library.boundaries_ev(),
        }
    }

    fn collapse_row(
        &self,
        row: usize,
        phi: &[f64],
        flux_denominator: f64,
        first_flux_group: usize,
        last_flux_group: usize,
    ) -> f64 {
        match self {
            Self::Dense(library) => library.collapse_row(
                row,
                phi,
                flux_denominator,
                first_flux_group,
                last_flux_group,
            ),
            Self::Groupwise(library) => library.collapse_row(
                row,
                phi,
                flux_denominator,
                first_flux_group,
                last_flux_group,
            ),
            Self::Collapsed(library) => library.collapse_row(
                row,
                phi,
                flux_denominator,
                first_flux_group,
                last_flux_group,
            ),
        }
    }

    fn fission_average_energy_ev(&self, row: usize, phi: &[f64]) -> Result<Option<f64>, String> {
        match self {
            Self::Dense(library) => library.fission_average_energy_ev(row, phi),
            Self::Groupwise(library) => library.fission_average_energy_ev(row, phi),
            Self::Collapsed(library) => library.fission_average_energy_ev(row, phi),
        }
    }
}

/// Immutable, verified nuclear data shared by ordinary and mesh solves.
///
/// A prepared value owns the decompressed activation library, parsed decay records, decay
/// chain, index and optional photon response. It is `Send + Sync` by construction, so mesh
/// workers borrow it without cloning or re-reading source files.
pub struct PreparedRun {
    library_path: String,
    library_sha_declared: Option<String>,
    library_sha: String,
    index_path: String,
    index_sha: String,
    index: serde_json::Value,
    library: ActivationLibrary,
    library_targets: Vec<(i32, i32)>,
    decay_primary: String,
    decay_primary_sha: String,
    decay_fallback: Option<String>,
    decay_fallback_sha: Option<String>,
    nuclides: HashMap<(i32, i32), decay::Nuclide>,
    decay_fallback_keys: std::collections::HashSet<(i32, i32)>,
    chain: chain::Chain,
    decay_nuclides_from_fallback: usize,
    photon_options: PhotonOptions,
    response: Option<PhotonResponse>,
    response_sha: Option<String>,
    fission_options: FissionYieldOptions,
    fission_yields: HashMap<(i32, i32), fission::FissionYields>,
    fission_yield_inputs: Vec<(HashedFileRef, String, (i32, i32))>,
    projectile: Projectile,
    library_group_structure: Option<String>,
    temperature_K: f64,
    uncertainty_options: Option<UncertaintyOptions>,
    covariance: Option<PreparedCovariance>,
    radiological: Option<PreparedRadiological>,
}

struct PreparedCovariance {
    sha256: String,
    index_path: String,
    index_sha256: String,
    index: serde_json::Value,
    library: covariance::CovarianceLibrary,
}

struct PreparedRadiological {
    options: RadiologicalOptions,
    sha256: String,
    table: PreparedRadiologicalTable,
}

struct UncertaintyRuntime {
    parameters: Vec<SensitivityParameter>,
    directions: Vec<Csc>,
    tangents: Vec<Vec<f64>>,
    covered_parameter_positions: Vec<usize>,
    covariance_barn2: Vec<f64>,
    uncovered_library_rows: Vec<usize>,
    absent_cross_parameter_pairs: usize,
    maximum_covariance_asymmetry_barn2: f64,
    normal_multiplier: f64,
    alternate_y: Vec<f64>,
}

struct ResponseSnapshot {
    heat: [f64; 4],
    activity: BTreeMap<String, f64>,
}

struct ResponseContext<'a> {
    keep: &'a [usize],
    positions: &'a [usize],
    chain: &'a chain::Chain,
    nuclides: &'a HashMap<(i32, i32), decay::Nuclide>,
}

fn response_snapshot(
    state: &[f64],
    keep: &[usize],
    chain: &chain::Chain,
    nuclides: &HashMap<(i32, i32), decay::Nuclide>,
    bulk_heat_split: (f64, f64, f64),
    bulk_activity: &BTreeMap<String, f64>,
) -> ResponseSnapshot {
    let (mut alpha, mut beta, mut gamma) = bulk_heat_split;
    let mut activity = bulk_activity.clone();
    for (subspace, &atoms) in state.iter().enumerate() {
        let global = keep[subspace];
        if global == chain.leak || global == chain.unit || atoms <= 0.0 {
            continue;
        }
        let key = chain.keys[global];
        let Some(nuclide) = nuclides.get(&key) else {
            continue;
        };
        let decay_rate = nuclide.lambda();
        if decay_rate <= 0.0 {
            continue;
        }
        activity.insert(name_of(key.0, key.1), decay_rate * atoms);
        alpha += decay_rate * atoms * nuclide.e_heavy() * EV;
        beta += decay_rate * atoms * nuclide.e_light() * EV;
        gamma += decay_rate * atoms * nuclide.e_em() * EV;
    }
    ResponseSnapshot {
        heat: [alpha + beta + gamma, alpha, beta, gamma],
        activity,
    }
}

fn selected_responses(options: &UncertaintyOptions, nominal: &ResponseSnapshot) -> Vec<String> {
    let mut selected = std::collections::BTreeSet::new();
    if options.responses.is_empty() {
        for response in ["heat.total", "heat.alpha", "heat.beta", "heat.gamma"] {
            selected.insert(response.to_owned());
        }
        for nuclide in nominal.activity.keys() {
            selected.insert(format!("activity:{nuclide}"));
        }
    } else {
        for response in &options.responses {
            if response == "activity:*" {
                for nuclide in nominal.activity.keys() {
                    selected.insert(format!("activity:{nuclide}"));
                }
            } else {
                selected.insert(response.clone());
            }
        }
    }
    selected.into_iter().collect()
}

fn snapshot_value(snapshot: &ResponseSnapshot, response: &str) -> f64 {
    match response {
        "heat.total" => snapshot.heat[0],
        "heat.alpha" => snapshot.heat[1],
        "heat.beta" => snapshot.heat[2],
        "heat.gamma" => snapshot.heat[3],
        _ => response
            .strip_prefix("activity:")
            .and_then(|name| snapshot.activity.get(name))
            .copied()
            .unwrap_or(0.0),
    }
}

fn tangent_value(
    tangent: &[f64],
    response: &str,
    keep: &[usize],
    positions: &[usize],
    chain: &chain::Chain,
    nuclides: &HashMap<(i32, i32), decay::Nuclide>,
) -> f64 {
    if let Some(name) = response.strip_prefix("activity:") {
        return chain
            .keys
            .iter()
            .position(|key| name_of(key.0, key.1) == name)
            .and_then(|global| {
                let subspace = positions[global];
                (subspace != usize::MAX).then_some((global, subspace))
            })
            .map(|(global, subspace)| chain.lambda[global] * tangent[subspace])
            .unwrap_or(0.0);
    }
    let component = match response {
        "heat.total" => 0,
        "heat.alpha" => 1,
        "heat.beta" => 2,
        "heat.gamma" => 3,
        _ => unreachable!("validated response selector"),
    };
    let mut value = 0.0;
    for (subspace, derivative) in tangent.iter().enumerate() {
        let global = keep[subspace];
        if global == chain.leak || global == chain.unit {
            continue;
        }
        let Some(nuclide) = nuclides.get(&chain.keys[global]) else {
            continue;
        };
        let energy = match component {
            1 => nuclide.e_heavy(),
            2 => nuclide.e_light(),
            3 => nuclide.e_em(),
            _ => nuclide.e_heavy() + nuclide.e_light() + nuclide.e_em(),
        };
        value += nuclide.lambda() * derivative * energy * EV;
    }
    value
}

fn build_step_uncertainty(
    runtime: &UncertaintyRuntime,
    options: &UncertaintyOptions,
    nominal: &ResponseSnapshot,
    alternate: &ResponseSnapshot,
    context: &ResponseContext<'_>,
) -> Result<StepUncertainty, String> {
    let mut responses = BTreeMap::new();
    for response in selected_responses(options, nominal) {
        let values: Vec<f64> = runtime
            .tangents
            .iter()
            .map(|tangent| {
                tangent_value(
                    tangent,
                    &response,
                    context.keep,
                    context.positions,
                    context.chain,
                    context.nuclides,
                )
            })
            .collect();
        let covered: Vec<f64> = runtime
            .covered_parameter_positions
            .iter()
            .map(|position| values[*position])
            .collect();
        let (variance, residue) =
            uncertainty_report::propagated_variance(&covered, &runtime.covariance_barn2)?;
        let response_unit = if response.starts_with("heat.") {
            "W g^-1"
        } else {
            "Bq g^-1"
        };
        let sensitivity_unit = format!("{response_unit} barn^-1");
        let sensitivities = values
            .into_iter()
            .zip(&runtime.parameters)
            .map(|(value, parameter)| SensitivityOut {
                parameter: parameter.clone(),
                value,
                unit: sensitivity_unit.clone(),
            })
            .collect();
        let report = uncertainty_report::response_band(BandInput {
            nominal: snapshot_value(nominal, &response),
            alternate: snapshot_value(alternate, &response),
            unit: response_unit.into(),
            confidence_level: options.confidence_level,
            normal_multiplier: runtime.normal_multiplier,
            variance,
            negative_variance_roundoff_removed: residue,
            sensitivities,
        })?;
        if options.require_complete && report.coverage != "complete" {
            return Err(format!(
                "uncertainty response '{response}' has partial MF=33 coverage"
            ));
        }
        responses.insert(response, report);
    }
    Ok(StepUncertainty {
        method: "local first-order propagation of spectrum-collapsed ENDF-6 MF=33 covariance",
        uncovered_library_rows: runtime.uncovered_library_rows.clone(),
        absent_cross_parameter_pairs: runtime.absent_cross_parameter_pairs,
        maximum_covariance_asymmetry_barn2: runtime.maximum_covariance_asymmetry_barn2,
        responses,
    })
}

impl PreparedRun {
    pub fn prepare(spec: &Spec) -> Result<Self, String> {
        let mut profiler = RunProfiler::disabled();
        Self::prepare_profiled(spec, &mut profiler)
    }

    fn prepare_profiled(spec: &Spec, profiler: &mut RunProfiler) -> Result<Self, String> {
        let flux = spec.flux_ascending();
        Self::prepare_inputs_with_extensions_profiled(
            &spec.library,
            &spec.decay,
            &spec.photon,
            &spec.fission_yields,
            spec.projectile,
            spec.options.temperature_K,
            spec.uncertainty.as_ref(),
            spec.radiological.as_ref(),
            Some(&flux),
            Some(&spec.spectrum.structure),
            profiler,
        )
    }

    pub fn prepare_inputs(
        library_ref: &LibraryRef,
        decay_ref: &DecayRef,
        photon_options: &PhotonOptions,
        fission_options: &FissionYieldOptions,
        projectile: Projectile,
        temperature_K: f64,
    ) -> Result<Self, String> {
        Self::prepare_inputs_with_uncertainty(
            library_ref,
            decay_ref,
            photon_options,
            fission_options,
            projectile,
            temperature_K,
            None,
        )
    }

    pub fn prepare_inputs_with_uncertainty(
        library_ref: &LibraryRef,
        decay_ref: &DecayRef,
        photon_options: &PhotonOptions,
        fission_options: &FissionYieldOptions,
        projectile: Projectile,
        temperature_K: f64,
        uncertainty_options: Option<&UncertaintyOptions>,
    ) -> Result<Self, String> {
        Self::prepare_inputs_with_extensions(
            library_ref,
            decay_ref,
            photon_options,
            fission_options,
            projectile,
            temperature_K,
            uncertainty_options,
            None,
        )
    }

    #[allow(clippy::too_many_arguments)]
    pub fn prepare_inputs_with_extensions(
        library_ref: &LibraryRef,
        decay_ref: &DecayRef,
        photon_options: &PhotonOptions,
        fission_options: &FissionYieldOptions,
        projectile: Projectile,
        temperature_K: f64,
        uncertainty_options: Option<&UncertaintyOptions>,
        radiological_options: Option<&RadiologicalOptions>,
    ) -> Result<Self, String> {
        let mut profiler = RunProfiler::disabled();
        Self::prepare_inputs_with_extensions_profiled(
            library_ref,
            decay_ref,
            photon_options,
            fission_options,
            projectile,
            temperature_K,
            uncertainty_options,
            radiological_options,
            None,
            None,
            &mut profiler,
        )
    }

    #[allow(clippy::too_many_arguments)]
    fn prepare_inputs_with_extensions_profiled(
        library_ref: &LibraryRef,
        decay_ref: &DecayRef,
        photon_options: &PhotonOptions,
        fission_options: &FissionYieldOptions,
        projectile: Projectile,
        temperature_K: f64,
        uncertainty_options: Option<&UncertaintyOptions>,
        radiological_options: Option<&RadiologicalOptions>,
        collapse_flux: Option<&[f64]>,
        collapse_group_structure: Option<&str>,
        profiler: &mut RunProfiler,
    ) -> Result<Self, String> {
        let validation_started = profiler.start();
        if !temperature_K.is_finite() || temperature_K < 0.0 {
            return Err("temperature_K must be finite and nonnegative".into());
        }
        if !projectile.is_neutron() && temperature_K != 0.0 {
            return Err(format!(
                "{} prepared runs require temperature_K: 0",
                projectile.name()
            ));
        }
        if !projectile.is_neutron() && !fission_options.files.is_empty() {
            return Err(format!(
                "fission-yield files are not supported for {} activation",
                projectile.name()
            ));
        }
        profiler.finish("prepare_validation", validation_started);

        let hashes_started = profiler.start();
        let library_sha = verify_hash(&library_ref.path, library_ref.sha256.as_deref())?;
        let idx_path = index_path(&library_ref.path);
        let index_sha = verify_hash(&idx_path, None)?;
        let decay_primary_sha = verify_hash(&decay_ref.primary, None)?;
        let decay_fallback_sha = match &decay_ref.fallback {
            Some(path) if !path.is_empty() => Some(verify_hash(path, None)?),
            _ => None,
        };
        profiler.finish("input_hash_verification", hashes_started);

        let extensions_started = profiler.start();
        let (response, response_sha) = match &photon_options.response {
            Some(reference) => {
                let sha = verify_hash(&reference.path, Some(&reference.sha256))?;
                let text = std::fs::read_to_string(&reference.path)
                    .map_err(|e| format!("cannot read photon response {}: {e}", reference.path))?;
                (Some(PhotonResponse::from_json(&text)?), Some(sha))
            }
            None => (None, None),
        };
        let radiological = match radiological_options {
            Some(options) => {
                let sha256 = verify_hash(&options.table.path, Some(&options.table.sha256))?;
                let text = std::fs::read_to_string(&options.table.path).map_err(|error| {
                    format!(
                        "cannot read radiological table {}: {error}",
                        options.table.path
                    )
                })?;
                Some(PreparedRadiological {
                    options: options.clone(),
                    sha256,
                    table: PreparedRadiologicalTable::from_json(&text, &options.responses)?,
                })
            }
            None => None,
        };
        let mut fission_yields = HashMap::new();
        let mut fission_yield_inputs = Vec::with_capacity(fission_options.files.len());
        for reference in &fission_options.files {
            let sha = verify_hash(&reference.path, Some(&reference.sha256))?;
            let parsed = fission::parse_file(&reference.path)?;
            let parent = parsed.parent;
            if fission_yields.insert(parent, parsed).is_some() {
                return Err(format!(
                    "duplicate fission-yield parent {}_{}",
                    parent.0, parent.1
                ));
            }
            fission_yield_inputs.push((reference.clone(), sha, parent));
        }
        profiler.finish("extension_input_preparation", extensions_started);

        let index_started = profiler.start();
        let index: serde_json::Value =
            serde_json::from_str(&std::fs::read_to_string(&idx_path).map_err(|e| e.to_string())?)
                .map_err(|e| e.to_string())?;
        let library_group_structure = index
            .get("groups")
            .and_then(serde_json::Value::as_str)
            .map(str::to_owned);
        if group_structure_mismatch(collapse_group_structure, library_group_structure.as_deref()) {
            return Err(format!(
                "spec group structure '{}' does not match activation-library group structure '{}'",
                collapse_group_structure.unwrap_or_default(),
                library_group_structure.as_deref().unwrap_or_default()
            ));
        }
        profiler.finish("index_read_validation", index_started);

        let activation_started = profiler.start();
        let library = if uncertainty_options.is_some() {
            // Covariance propagation still indexes the original dense rows directly. The complete NPZ matched its
            // computed (and, when supplied, declared) SHA-256 above, so member CRC work is redundant.
            ActivationLibrary::Dense(library::read_npz_after_sha256_verification(
                &library_ref.path,
            )?)
        } else if let Some(phi) = collapse_flux {
            ActivationLibrary::Collapsed(
                prepared_data::load_or_prepare_collapsed_after_sha256_verification(
                    &library_ref.path,
                    &library_sha,
                    &index_sha,
                    phi,
                )?,
            )
        } else {
            ActivationLibrary::Groupwise(
                prepared_data::load_or_prepare_groupwise_after_sha256_verification(
                    &library_ref.path,
                    &library_sha,
                    &index_sha,
                )?,
            )
        };
        profiler.finish("activation_read_validation", activation_started);

        let index_projectile = match index.get("projectile") {
            None | Some(serde_json::Value::Null) => Projectile::Neutron,
            Some(serde_json::Value::String(value)) => Projectile::parse(value)?,
            Some(_) => return Err("activation-library index projectile must be a string".into()),
        };
        if index_projectile != projectile {
            return Err(format!(
                "spec projectile '{}' does not match activation-library projectile '{}'",
                projectile.name(),
                index_projectile.name()
            ));
        }
        match index.get("sha256_npz") {
            Some(serde_json::Value::String(recorded)) => {
                if !library_sha.eq_ignore_ascii_case(recorded) {
                    return Err(format!(
                        "activation-library index hash mismatch: index records {recorded}, computed {library_sha}"
                    ));
                }
            }
            Some(_) => return Err("activation-library index sha256_npz must be a string".into()),
            None if !projectile.is_neutron() => {
                return Err("charged activation-library index has no sha256_npz".into());
            }
            None => {}
        }
        let actual_group_hash = GroupStructure {
            name: library_group_structure
                .clone()
                .unwrap_or_else(|| "custom".into()),
            boundaries_ev: library.boundaries_ev().to_vec(),
        }
        .hash();
        if let Some(recorded) = index.get("group_boundary_sha256") {
            let recorded = recorded
                .as_str()
                .ok_or("activation-library group_boundary_sha256 must be a string")?;
            if !actual_group_hash.eq_ignore_ascii_case(recorded) {
                return Err(format!(
                    "activation-library group-boundary hash mismatch: index records {recorded}, computed {actual_group_hash}"
                ));
            }
        } else if !projectile.is_neutron() {
            return Err("charged activation-library index has no group_boundary_sha256".into());
        }
        if !projectile.is_neutron() && library_group_structure.is_none() {
            return Err("charged activation-library index has no named group structure".into());
        }
        if let Some(name) = library_group_structure.as_deref() {
            let canonical = match name {
                "fispact-709" => Some(GroupStructure::fispact_709()?),
                "fispact-162" => Some(GroupStructure::fispact_162()?),
                _ => None,
            };
            if let Some(canonical) = canonical {
                if canonical.boundaries_ev.len() != library.boundaries_ev().len()
                    || canonical
                        .boundaries_ev
                        .iter()
                        .zip(library.boundaries_ev())
                        .any(|(expected, stored)| expected.to_bits() != stored.to_bits())
                {
                    return Err(format!(
                        "activation-library boundaries do not match declared group structure '{name}'"
                    ));
                }
            }
        }
        match index.get("temperature_K") {
            Some(value) => {
                let temperature = value
                    .as_f64()
                    .ok_or("activation-library temperature_K must be numeric")?;
                if (temperature - temperature_K).abs() > 1e-9 {
                    return Err(format!(
                        "requested temperature {temperature_K} K does not match library temperature {temperature} K"
                    ));
                }
            }
            None if !projectile.is_neutron() => {
                return Err("charged activation-library index has no temperature_K".into());
            }
            None => {}
        }
        let library_targets: Vec<(i32, i32)> = index["targets"]
            .as_array()
            .ok_or("library index has no targets")?
            .iter()
            .map(|target| {
                (
                    target["za"].as_i64().unwrap_or(0) as i32,
                    target["liso"].as_i64().unwrap_or(0) as i32,
                )
            })
            .collect();
        let covariance_started = profiler.start();
        let covariance = match uncertainty_options {
            Some(options) => Some(Self::load_covariance(
                options,
                &library_sha,
                &index_sha,
                &actual_group_hash,
                &index,
                &library_targets,
            )?),
            None => None,
        };
        profiler.finish("covariance_read_validation", covariance_started);

        let primary_decay_started = profiler.start();
        let mut nuclides =
            decay::parse_file(&decay_ref.primary).map_err(|error| error.to_string())?;
        profiler.finish("decay_primary_read_parse", primary_decay_started);

        let fallback_decay_started = profiler.start();
        let mut decay_nuclides_from_fallback = 0usize;
        let mut decay_fallback_keys = std::collections::HashSet::new();
        if let Some(fallback) = &decay_ref.fallback {
            if !fallback.is_empty() {
                for (key, value) in
                    decay::parse_file(fallback).map_err(|error| error.to_string())?
                {
                    if let std::collections::hash_map::Entry::Vacant(entry) = nuclides.entry(key) {
                        entry.insert(value);
                        decay_nuclides_from_fallback += 1;
                        decay_fallback_keys.insert(key);
                    }
                }
            }
        }
        profiler.finish("decay_fallback_read_parse_merge", fallback_decay_started);

        let chain_started = profiler.start();
        let chain = chain::build(&nuclides);
        if let Some(options) = uncertainty_options {
            let known: std::collections::HashSet<_> =
                nuclides.keys().map(|key| name_of(key.0, key.1)).collect();
            for selector in &options.responses {
                if let Some(name) = selector.strip_prefix("activity:") {
                    if name != "*" && !known.contains(name) {
                        return Err(format!(
                            "uncertainty response '{selector}' names a nuclide absent from the decay library"
                        ));
                    }
                }
            }
        }
        profiler.finish("chain_construction", chain_started);
        Ok(Self {
            library_path: library_ref.path.clone(),
            library_sha_declared: library_ref.sha256.clone(),
            library_sha,
            index_path: idx_path,
            index_sha,
            index,
            library,
            library_targets,
            decay_primary: decay_ref.primary.clone(),
            decay_primary_sha,
            decay_fallback: decay_ref
                .fallback
                .as_ref()
                .filter(|path| !path.is_empty())
                .cloned(),
            decay_fallback_sha,
            nuclides,
            decay_fallback_keys,
            chain,
            decay_nuclides_from_fallback,
            photon_options: photon_options.clone(),
            response,
            response_sha,
            fission_options: fission_options.clone(),
            fission_yields,
            fission_yield_inputs,
            projectile,
            library_group_structure,
            temperature_K,
            uncertainty_options: uncertainty_options.cloned(),
            covariance,
            radiological,
        })
    }

    fn load_covariance(
        options: &UncertaintyOptions,
        activation_library_sha256: &str,
        activation_index_sha256: &str,
        group_boundary_sha256: &str,
        activation_index: &serde_json::Value,
        activation_targets: &[(i32, i32)],
    ) -> Result<PreparedCovariance, String> {
        let path = &options.covariance.path;
        let sha256 = verify_hash(path, Some(&options.covariance.sha256))?;
        let index_path = covariance::index_path(path)?.display().to_string();
        let index_sha256 = verify_hash(&index_path, None)?;
        let index: serde_json::Value = serde_json::from_str(
            &std::fs::read_to_string(&index_path)
                .map_err(|error| format!("cannot read covariance index {index_path}: {error}"))?,
        )
        .map_err(|error| format!("cannot parse covariance index {index_path}: {error}"))?;
        let string = |name: &str| {
            index
                .get(name)
                .and_then(serde_json::Value::as_str)
                .ok_or_else(|| format!("covariance index {name} must be a string"))
        };
        if string("schema")? != "actinv-covariance-index-1" {
            return Err("unsupported covariance-index schema".into());
        }
        if string("projectile")? != "neutron" {
            return Err("covariance sidecar is not for neutron activation".into());
        }
        for (field, expected) in [
            ("sha256_npz", sha256.as_str()),
            ("activation_library_sha256", activation_library_sha256),
            ("activation_index_sha256", activation_index_sha256),
            ("group_boundary_sha256", group_boundary_sha256),
        ] {
            let recorded = string(field)?;
            if !recorded.eq_ignore_ascii_case(expected) {
                return Err(format!(
                    "covariance index {field} mismatch: records {recorded}, expected {expected}"
                ));
            }
        }
        let covariance_targets = index
            .get("targets")
            .and_then(serde_json::Value::as_array)
            .ok_or("covariance index targets must be an array")?;
        let activation_target_values = activation_index
            .get("targets")
            .and_then(serde_json::Value::as_array)
            .ok_or("activation index targets must be an array")?;
        if covariance_targets.len() != activation_targets.len()
            || activation_target_values.len() != activation_targets.len()
        {
            return Err("covariance and activation target counts differ".into());
        }
        for (target, ((covariance_target, activation_target), &(za, liso))) in covariance_targets
            .iter()
            .zip(activation_target_values)
            .zip(activation_targets)
            .enumerate()
        {
            let cov_integer = |name: &str| {
                covariance_target
                    .get(name)
                    .and_then(serde_json::Value::as_i64)
                    .ok_or_else(|| format!("covariance target {target} {name} must be an integer"))
            };
            let activation_integer = |name: &str| {
                activation_target
                    .get(name)
                    .and_then(serde_json::Value::as_i64)
                    .ok_or_else(|| format!("activation target {target} {name} must be an integer"))
            };
            if cov_integer("target")? != target as i64
                || cov_integer("za")? != i64::from(za)
                || cov_integer("liso")? != i64::from(liso)
                || cov_integer("za")? != activation_integer("za")?
                || cov_integer("liso")? != activation_integer("liso")?
                || cov_integer("mat")? != activation_integer("mat")?
            {
                return Err(format!(
                    "covariance target {target} identity differs from the activation index"
                ));
            }
            for name in ["file", "source_sha256"] {
                let covariance_value = covariance_target
                    .get(name)
                    .and_then(serde_json::Value::as_str)
                    .ok_or_else(|| format!("covariance target {target} {name} must be a string"))?;
                let activation_value = activation_target
                    .get(name)
                    .and_then(serde_json::Value::as_str)
                    .ok_or_else(|| format!("activation target {target} {name} must be a string"))?;
                if covariance_value != activation_value {
                    return Err(format!(
                        "covariance target {target} {name} differs from the activation index"
                    ));
                }
            }
        }
        let library = covariance::read_npz(path)?;
        let component_count = index
            .get("components")
            .and_then(serde_json::Value::as_u64)
            .and_then(|value| usize::try_from(value).ok())
            .ok_or("covariance index components must be a nonnegative integer")?;
        if component_count != library.components.len() {
            return Err(format!(
                "covariance index records {component_count} components but sidecar contains {}",
                library.components.len()
            ));
        }
        let mut total_lb_counts: BTreeMap<i32, usize> = BTreeMap::new();
        let mut target_component_counts = vec![0usize; activation_targets.len()];
        let mut target_sections = vec![std::collections::BTreeSet::new(); activation_targets.len()];
        let mut target_lb_counts = vec![BTreeMap::new(); activation_targets.len()];
        for (component, stored) in library.components.iter().enumerate() {
            if stored.target >= activation_targets.len() {
                return Err(format!(
                    "covariance component {component} has out-of-range target {}",
                    stored.target
                ));
            }
            target_component_counts[stored.target] += 1;
            target_sections[stored.target].insert(stored.mt);
            *target_lb_counts[stored.target]
                .entry(stored.lb)
                .or_default() += 1;
            *total_lb_counts.entry(stored.lb).or_default() += 1;
        }
        let parse_lb_counts = |value: &serde_json::Value, context: &str| {
            let object = value
                .as_object()
                .ok_or_else(|| format!("{context} lb_counts must be an object"))?;
            object
                .iter()
                .map(|(key, value)| {
                    let lb = key
                        .parse::<i32>()
                        .map_err(|_| format!("{context} has invalid LB key '{key}'"))?;
                    let count = value
                        .as_u64()
                        .and_then(|count| usize::try_from(count).ok())
                        .ok_or_else(|| format!("{context} LB={lb} count is invalid"))?;
                    Ok((lb, count))
                })
                .collect::<Result<BTreeMap<_, _>, String>>()
        };
        let indexed_lb_counts = parse_lb_counts(
            index
                .get("lb_counts")
                .ok_or("covariance index has no lb_counts")?,
            "covariance index",
        )?;
        if indexed_lb_counts != total_lb_counts {
            return Err("covariance index LB inventory differs from the sidecar".into());
        }
        for (target, value) in covariance_targets.iter().enumerate() {
            let recorded_components = value
                .get("components")
                .and_then(serde_json::Value::as_u64)
                .and_then(|count| usize::try_from(count).ok())
                .ok_or_else(|| format!("covariance target {target} components is invalid"))?;
            let recorded_sections = value
                .get("mf33_sections")
                .and_then(serde_json::Value::as_u64)
                .and_then(|count| usize::try_from(count).ok())
                .ok_or_else(|| format!("covariance target {target} mf33_sections is invalid"))?;
            let recorded_lb_counts = parse_lb_counts(
                value
                    .get("lb_counts")
                    .ok_or_else(|| format!("covariance target {target} has no lb_counts"))?,
                &format!("covariance target {target}"),
            )?;
            if recorded_components != target_component_counts[target]
                || recorded_sections != target_sections[target].len()
                || recorded_lb_counts != target_lb_counts[target]
            {
                return Err(format!(
                    "covariance target {target} inventory differs from the sidecar"
                ));
            }
        }
        Ok(PreparedCovariance {
            sha256,
            index_path,
            index_sha256,
            index,
            library,
        })
    }

    pub fn library_boundaries_eV(&self) -> &[f64] {
        self.library.boundaries_ev()
    }

    pub fn library_groups(&self) -> usize {
        self.library.group_count()
    }

    fn ensure_compatible(&self, spec: &Spec) -> Result<(), String> {
        if spec.library.path != self.library_path
            || spec.library.sha256 != self.library_sha_declared
            || spec.decay.primary != self.decay_primary
            || spec.decay.fallback.as_ref().filter(|path| !path.is_empty())
                != self.decay_fallback.as_ref()
            || spec
                .photon
                .response
                .as_ref()
                .map(|value| (&value.path, &value.sha256))
                != self
                    .photon_options
                    .response
                    .as_ref()
                    .map(|value| (&value.path, &value.sha256))
            || spec.fission_yields != self.fission_options
            || spec.projectile != self.projectile
            || (spec.options.temperature_K - self.temperature_K).abs() > 1e-9
            || spec.uncertainty != self.uncertainty_options
            || spec.radiological.as_ref()
                != self.radiological.as_ref().map(|prepared| &prepared.options)
        {
            return Err("run spec nuclear-data inputs do not match the prepared data".into());
        }
        if group_structure_mismatch(
            Some(&spec.spectrum.structure),
            self.library_group_structure.as_deref(),
        ) {
            return Err(format!(
                "spec group structure '{}' does not match activation-library group structure '{}'",
                spec.spectrum.structure,
                self.library_group_structure.as_deref().unwrap_or_default()
            ));
        }
        Ok(())
    }

    pub fn run(&self, spec: &Spec, entry_point: &str) -> Result<RunResult, String> {
        let mut profiler = RunProfiler::disabled();
        self.run_started_profiled(spec, entry_point, std::time::Instant::now(), &mut profiler)
    }

    fn run_started_profiled(
        &self,
        spec: &Spec,
        entry_point: &str,
        t0: std::time::Instant,
        profiler: &mut RunProfiler,
    ) -> Result<RunResult, String> {
        let network_started = profiler.start();
        self.ensure_compatible(spec)?;
        let library_sha = &self.library_sha;
        let idx_path = &self.index_path;
        let index_sha = &self.index_sha;
        let decay_primary_sha = &self.decay_primary_sha;
        let decay_fallback_sha = &self.decay_fallback_sha;
        let response = &self.response;
        let response_sha = &self.response_sha;
        let lib = &self.library;
        let idxj = &self.index;
        let lib_targets = &self.library_targets;
        let nuclides = &self.nuclides;
        let n_fallback = self.decay_nuclides_from_fallback;
        let ch = &self.chain;
        if lib.group_count() != spec.spectrum.flux_per_group.len() {
            return Err(format!(
                "spectrum has {} groups but activation library has {}",
                spec.spectrum.flux_per_group.len(),
                lib.group_count()
            ));
        }
        if let Some(boundaries) = &spec.spectrum.boundaries_eV {
            if boundaries.len() != lib.boundaries_ev().len()
                || boundaries
                    .iter()
                    .zip(lib.boundaries_ev())
                    .any(|(declared, stored)| {
                        (declared - stored).abs()
                            > 1e-12 * declared.abs().max(stored.abs()).max(1.0)
                    })
            {
                return Err(
                    "custom spectrum boundaries do not match the activation library boundaries"
                        .into(),
                );
            }
        }
        // ---- material
        let composition_total: f64 = spec.material.composition.values().sum();
        let (bulk_inv, cdiag) = composition::material_atoms_per_gram(
            &spec.material.composition,
            &spec.material.basis,
            nuclides,
        )?;
        let material_mass_fractions = if response.is_some() {
            composition::material_mass_fractions(
                &spec.material.composition,
                &spec.material.basis,
                nuclides,
            )?
        } else {
            BTreeMap::new()
        };
        let phi = spec.flux_ascending();
        lib.validate_flux(&phi)?;
        let mut effective_fission_yields = HashMap::new();
        let mut fission_yield_selection = Vec::new();
        let mut fission_parents: Vec<_> = self.fission_yields.keys().copied().collect();
        fission_parents.sort_unstable();
        for parent in fission_parents {
            let requested_energy = match spec.fission_yields.energy.as_str() {
                "fixed" => spec.fission_yields.fixed_energy_eV,
                "spectrum_average" => fission_average_energy_eV(lib, lib_targets, &phi, parent)?,
                _ => None, // validated before preparation
            };
            let Some(requested_energy) = requested_energy else {
                continue;
            };
            let effective = self.fission_yields[&parent].effective(requested_energy)?;
            fission_yield_selection.push(serde_json::json!({
                "parent": name_of(parent.0, parent.1),
                "parent_ZA": parent.0,
                "parent_LISO": parent.1,
                "mode": spec.fission_yields.energy,
                "requested_energy_eV": effective.requested_energy_ev,
                "lower_energy_eV": effective.lower_energy_ev,
                "upper_energy_eV": effective.upper_energy_ev,
                "upper_weight": effective.upper_weight,
                "clamped": effective.clamped,
                "effective_yield_sum": effective.sum,
                "products": effective.products.len(),
            }));
            effective_fission_yields.insert(parent, effective);
        }
        let mut led = RateLedger::default();
        let reaction_assembly = if spec.uncertainty.is_some() {
            chain::reaction_rates_with_derivatives(
                lib,
                lib_targets,
                &phi,
                ch,
                &effective_fission_yields,
                &mut led,
            )
        } else {
            chain::ReactionAssembly {
                triplets: chain::reaction_rates(
                    lib,
                    lib_targets,
                    &phi,
                    ch,
                    &effective_fission_yields,
                    &mut led,
                ),
                derivatives: Vec::new(),
            }
        };
        let react = reaction_assembly.triplets;
        // ---- trace formulation: bulk isotopes become constant sources through the unit state
        let mut bulk: HashMap<usize, f64> = HashMap::new();
        let mut absent: Vec<(i32, i32)> = Vec::new();
        for ((za, liso), atoms) in &bulk_inv {
            match ch.index.get(&(*za, *liso)) {
                Some(&c) => {
                    *bulk.entry(c).or_insert(0.0) += atoms;
                }
                None => absent.push((*za, *liso)),
            }
        }
        let sched = spec.schedule_seconds();
        let flux_weighted_time_s: f64 = sched.iter().map(|(duration, flux)| duration * flux).sum();
        let mut burnup_optical_depth: HashMap<usize, f64> = HashMap::new();
        for (r, c, v) in &react {
            if *r == *c && *v < 0.0 && bulk.contains_key(c) {
                *burnup_optical_depth.entry(*c).or_insert(0.0) += -v * flux_weighted_time_s;
            }
        }
        for (index, optical_depth) in burnup_optical_depth {
            let fraction = -(-optical_depth).exp_m1();
            if optical_depth > led.burnup_optical_depth_max {
                led.burnup_optical_depth_max = optical_depth;
                led.burnup_fraction_max = fraction;
                led.burnup_nuclide = Some(ch.keys[index]);
            }
        }
        let mode = match spec.options.mode.as_str() {
            "auto" => {
                if led.burnup_fraction_max < 1e-6 {
                    "trace"
                } else {
                    "coupled"
                }
            }
            m => m,
        }
        .to_string();
        let mut sources: Vec<(usize, f64, (i32, i32))> = Vec::new(); // (product row fed, rate, bulk nuclide it came from)
        let mut d_src: Vec<(usize, usize, f64)> = Vec::new();
        let mut r_src: Vec<(usize, usize, f64)> = Vec::new();
        let mut reaction_derivatives = Vec::new();
        let mut bulk_heat = 0.0;
        let mut bulk_heat_split = (0.0, 0.0, 0.0);
        let mut bulk_photon_active: Vec<(String, (i32, i32), f64)> = Vec::new();
        if mode == "trace" {
            for (r, c, v) in &ch.decay {
                if bulk.contains_key(c) {
                    if r != c && !bulk.contains_key(r) {
                        d_src.push((*r, ch.unit, v * bulk[c]));
                    }
                } else if !bulk.contains_key(r) {
                    d_src.push((*r, *c, *v));
                }
            }
            for (r, c, v) in &react {
                if bulk.contains_key(c) {
                    if r == c {
                        continue;
                    }
                    if bulk.contains_key(r) {
                        led.bulk_production_dropped.push((
                            name_of(ch.keys[*c].0, ch.keys[*c].1),
                            name_of(ch.keys[*r].0, ch.keys[*r].1),
                            *v,
                        ));
                        continue;
                    }
                    r_src.push((*r, ch.unit, v * bulk[c]));
                    sources.push((*r, v * bulk[c], ch.keys[*c]));
                } else if !bulk.contains_key(r) {
                    r_src.push((*r, *c, *v));
                }
            }
            for derivative in reaction_assembly.derivatives {
                if bulk.contains_key(&derivative.column) {
                    if derivative.row == derivative.column || bulk.contains_key(&derivative.row) {
                        continue;
                    }
                    reaction_derivatives.push(chain::ReactionDerivative {
                        row: derivative.row,
                        column: ch.unit,
                        per_barn_s: derivative.per_barn_s * bulk[&derivative.column],
                        ..derivative
                    });
                } else if !bulk.contains_key(&derivative.row) {
                    reaction_derivatives.push(derivative);
                }
            }
            for (c, nb) in &bulk {
                let key = ch.keys[*c];
                if let Some(nu) = nuclides.get(&key) {
                    if nu.lambda() > 0.0 {
                        let activity = nu.lambda() * nb;
                        bulk_heat_split.0 += activity * nu.e_heavy() * EV;
                        bulk_heat_split.1 += activity * nu.e_light() * EV;
                        bulk_heat_split.2 += activity * nu.e_em() * EV;
                        bulk_photon_active.push((name_of(key.0, key.1), key, activity));
                    }
                }
            }
            bulk_heat = bulk_heat_split.0 + bulk_heat_split.1 + bulk_heat_split.2;
        } else {
            d_src = ch.decay.clone();
            r_src = react.clone();
            reaction_derivatives = reaction_assembly.derivatives;
        }
        // ---- initial vector
        let mut n0 = vec![0.0f64; ch.n];
        if mode == "trace" {
            n0[ch.unit] = 1.0;
        } else {
            for (c, v) in &bulk {
                n0[*c] = *v;
            }
        }
        // ---- prune
        let (keep, rate_pruned): (Vec<usize>, Vec<(usize, f64, f64)>) =
            match spec.options.prune.as_str() {
                "none" => ((0..ch.n).collect(), Vec::new()),
                p => crate::prune::reachable(
                    ch.n,
                    &d_src,
                    &r_src,
                    &n0,
                    &sched,
                    p == "rate",
                    spec.options.bmin_atoms_per_g,
                ),
            };
        let mut pos = vec![usize::MAX; ch.n];
        for (k, g) in keep.iter().enumerate() {
            pos[*g] = k;
        }
        let m = keep.len();
        let sub = |t: &Vec<(usize, usize, f64)>| -> Vec<(usize, usize, C64)> {
            t.iter()
                .filter(|(i, j, _)| pos[*i] != usize::MAX && pos[*j] != usize::MAX)
                .map(|(i, j, v)| (pos[*i], pos[*j], C64::new(*v, 0.0)))
                .collect()
        };
        let dsub = sub(&d_src);
        let rsub = sub(&r_src);
        let mut derivative_sub: BTreeMap<usize, Vec<(usize, usize, C64)>> = BTreeMap::new();
        for derivative in reaction_derivatives {
            if derivative.per_barn_s != 0.0
                && pos[derivative.row] != usize::MAX
                && pos[derivative.column] != usize::MAX
            {
                derivative_sub
                    .entry(derivative.library_row)
                    .or_default()
                    .push((
                        pos[derivative.row],
                        pos[derivative.column],
                        C64::new(derivative.per_barn_s, 0.0),
                    ));
            }
        }
        let mut y = vec![0.0f64; m];
        for (g, v) in n0.iter().enumerate() {
            if *v != 0.0 && pos[g] != usize::MAX {
                y[pos[g]] = *v;
            }
        }
        let mut uncertainty_runtime = match (&self.covariance, &spec.uncertainty) {
            (Some(prepared), Some(options)) => {
                let active_rows: Vec<usize> = derivative_sub.keys().copied().collect();
                let dense_library = lib.dense().ok_or(
                    "uncertainty propagation requires the verified dense activation library",
                )?;
                let collapsed = prepared
                    .library
                    .collapse(dense_library, &phi, &active_rows)?;
                let maximum_covariance = collapsed
                    .covariance_barn2
                    .iter()
                    .map(|value| value.abs())
                    .fold(0.0f64, f64::max);
                let symmetry_tolerance = 128.0
                    * f64::EPSILON
                    * maximum_covariance
                    * collapsed.row_indices.len().max(1) as f64;
                if collapsed.maximum_asymmetry_barn2 > symmetry_tolerance {
                    return Err(format!(
                        "collapsed covariance is materially asymmetric: {:.17e} barn^2 (bound {:.17e})",
                        collapsed.maximum_asymmetry_barn2, symmetry_tolerance
                    ));
                }
                for (&row, &cross_section) in
                    collapsed.row_indices.iter().zip(&collapsed.one_group_barns)
                {
                    if cross_section.to_bits() != lib.one_group(row, &phi).to_bits() {
                        return Err(format!(
                            "covariance collapse nominal cross section differs at library row {row}"
                        ));
                    }
                }
                if options.require_complete && !collapsed.uncovered_rows.is_empty() {
                    return Err(format!(
                        "require_complete rejected {} active activation rows without MF=33 self-covariance",
                        collapsed.uncovered_rows.len()
                    ));
                }
                let covered_rows: BTreeMap<usize, usize> = collapsed
                    .row_indices
                    .iter()
                    .enumerate()
                    .map(|(position, row)| (*row, position))
                    .collect();
                let mut parameters = Vec::with_capacity(active_rows.len());
                let mut directions = Vec::with_capacity(active_rows.len());
                let mut covered_parameter_positions = Vec::new();
                for (parameter_position, &row_index) in active_rows.iter().enumerate() {
                    let row = lib.rows()[row_index];
                    let &(za, liso) = lib_targets.get(row.target).ok_or_else(|| {
                        format!("activation row {row_index} has an invalid target index")
                    })?;
                    let covered = covered_rows.contains_key(&row_index);
                    if covered {
                        covered_parameter_positions.push(parameter_position);
                    }
                    parameters.push(SensitivityParameter {
                        library_row: row_index,
                        target: row.target,
                        target_nuclide: name_of(za, liso),
                        target_za: za,
                        target_liso: liso,
                        mt: row.mt,
                        zap: row.zap,
                        lfs: row.lfs,
                        lmf: row.lmf,
                        collapsed_cross_section_b: lib.one_group(row_index, &phi),
                        covariance_covered: covered,
                    });
                    directions.push(Csc::from_triplets(m, &derivative_sub[&row_index]));
                }
                Some(UncertaintyRuntime {
                    tangents: vec![vec![0.0; m]; parameters.len()],
                    directions,
                    parameters,
                    covered_parameter_positions,
                    covariance_barn2: collapsed.covariance_barn2,
                    uncovered_library_rows: collapsed.uncovered_rows,
                    absent_cross_parameter_pairs: collapsed.absent_cross_parameter_pairs,
                    maximum_covariance_asymmetry_barn2: collapsed.maximum_asymmetry_barn2,
                    normal_multiplier: uncertainty_report::normal_multiplier(
                        options.confidence_level,
                    ),
                    alternate_y: y.clone(),
                })
            }
            (None, None) => None,
            _ => return Err("prepared covariance state does not match the run spec".into()),
        };
        profiler.finish("material_network_preparation", network_started);

        let solve_started = profiler.start();
        // ---- solve
        let c = cram(spec.options.cram_order);
        let alternate_c = spec.uncertainty.as_ref().map(|_| {
            cram(if spec.options.cram_order == 16 {
                48
            } else {
                16
            })
        });
        let want_photons = spec
            .options
            .outputs
            .as_ref()
            .is_none_or(|o| o.iter().any(|x| x == "photons" || x == "dose"));
        let photon_boundaries = spec.photon_boundaries();
        let material_response_complete = cdiag.unknown.is_empty()
            && response.as_ref().is_some_and(|r| {
                material_mass_fractions
                    .keys()
                    .all(|e| r.element_mass_attenuation.contains_key(e))
            });
        let mut photon_diagnostics: Vec<PhotonDiagnostics> = Vec::new();
        let mut steps = Vec::new();
        let mut t_cum = 0.0;
        let mut flux_weighted_time_cum = 0.0;
        let base_flux_total: f64 = phi.iter().sum();
        let bulk_activity: BTreeMap<String, f64> = bulk_photon_active
            .iter()
            .map(|(name, _, activity)| (name.clone(), *activity))
            .collect();
        for (si, (dt, fl)) in sched.iter().enumerate() {
            let mut trip = dsub.clone();
            if *fl > 0.0 {
                for (i, j, v) in rsub.iter() {
                    trip.push((*i, *j, v * C64::new(*fl, 0.0)));
                }
            }
            let a = Csc::from_triplets(m, &trip);
            if let Some(runtime) = uncertainty_runtime.as_mut() {
                let tangent_step = step_with_tangents(
                    &a,
                    &y,
                    &runtime.tangents,
                    &runtime.directions,
                    *fl,
                    *dt,
                    &c,
                )?;
                y = tangent_step.state;
                runtime.tangents = tangent_step.tangents;
                runtime.alternate_y = cram_step(
                    &a,
                    &runtime.alternate_y,
                    *dt,
                    alternate_c.as_ref().expect("alternate CRAM is prepared"),
                )?
                .0;
            } else {
                y = cram_step(&a, &y, *dt, &c)?.0;
            }
            t_cum += dt;
            flux_weighted_time_cum += dt * fl;
            let mut zeroed = 0.0;
            for (k, v) in y.iter_mut().enumerate() {
                if *v < 0.0 && keep[k] != ch.leak && keep[k] != ch.unit {
                    zeroed += -*v;
                    *v = 0.0;
                    if let Some(runtime) = uncertainty_runtime.as_mut() {
                        for tangent in &mut runtime.tangents {
                            tangent[k] = 0.0;
                        }
                    }
                }
            }
            if let Some(runtime) = uncertainty_runtime.as_mut() {
                for (k, value) in runtime.alternate_y.iter_mut().enumerate() {
                    if *value < 0.0 && keep[k] != ch.leak && keep[k] != ch.unit {
                        *value = 0.0;
                    }
                }
            }
            // ---- numerical floor: CRAM approximates exp(z) with an absolute floor of alpha0, so states whose population
            // is below alpha0 * max(N) carry no information. They are reported with a bound on the heat they could add.
            let nmax = y.iter().cloned().fold(0.0f64, f64::max);
            let floor = c.alpha0 * nmax;
            let (mut n_below, mut atoms_below, mut heat_below) = (0usize, 0.0, 0.0);
            for (k, v) in y.iter().enumerate() {
                let g = keep[k];
                if g == ch.leak || g == ch.unit || *v <= 0.0 || *v >= floor {
                    continue;
                }
                n_below += 1;
                atoms_below += *v;
                if let Some(nu) = nuclides.get(&ch.keys[g]) {
                    heat_below += nu.lambda() * *v * (nu.e_light() + nu.e_em() + nu.e_heavy()) * EV;
                }
            }
            // ---- outputs
            let mut inv = Vec::new();
            let mut act = bulk_activity.clone();
            let mut photon_active = bulk_photon_active.clone();
            let (mut ha, mut hb, mut hg) = bulk_heat_split;
            for (k, v) in y.iter().enumerate() {
                let g = keep[k];
                if g == ch.leak || g == ch.unit || *v <= 0.0 {
                    continue;
                }
                let key = ch.keys[g];
                let nu = match nuclides.get(&key) {
                    Some(n) => n,
                    None => continue,
                };
                let nm = name_of(key.0, key.1);
                inv.push(NuclideOut {
                    nuclide: nm.clone(),
                    Z: key.0 / 1000,
                    A: key.0 % 1000,
                    LISO: key.1,
                    atoms_per_g: *v,
                });
                let l = nu.lambda();
                if l > 0.0 {
                    act.insert(nm.clone(), l * v);
                    photon_active.push((nm, key, l * v));
                    ha += l * v * nu.e_heavy() * EV;
                    hb += l * v * nu.e_light() * EV;
                    hg += l * v * nu.e_em() * EV;
                }
            }
            let heat = Heat {
                total: ha + hb + hg,
                alpha: ha,
                beta: hb,
                gamma: hg,
            };
            let uncertainty = match (&uncertainty_runtime, &spec.uncertainty) {
                (Some(runtime), Some(options)) => {
                    let nominal = ResponseSnapshot {
                        heat: [heat.total, heat.alpha, heat.beta, heat.gamma],
                        activity: act.clone(),
                    };
                    let alternate = response_snapshot(
                        &runtime.alternate_y,
                        &keep,
                        ch,
                        nuclides,
                        bulk_heat_split,
                        &bulk_activity,
                    );
                    Some(build_step_uncertainty(
                        runtime,
                        options,
                        &nominal,
                        &alternate,
                        &ResponseContext {
                            keep: &keep,
                            positions: &pos,
                            chain: ch,
                            nuclides,
                        },
                    )?)
                }
                (None, None) => None,
                _ => return Err("uncertainty runtime state is inconsistent".into()),
            };
            let radiological = self
                .radiological
                .as_ref()
                .map(|prepared| {
                    prepared
                        .table
                        .evaluate(&act, prepared.options.require_complete)
                        .map_err(|error| format!("radiological step {}: {error}", si + 1))
                })
                .transpose()?;
            let photon_source = if want_photons {
                let active_refs: Vec<_> = photon_active
                    .iter()
                    .filter_map(|(name, key, activity)| {
                        nuclides.get(key).map(|nu| (name.as_str(), nu, *activity))
                    })
                    .collect();
                let (source, diagnostics) = photon::source_for_step(
                    &active_refs,
                    &photon_boundaries,
                    &spec.photon.group_structure,
                    spec.material.mass_g,
                    response.as_ref(),
                    &material_mass_fractions,
                    material_response_complete,
                    spec.photon.build_up_factor,
                    spec.photon.gamma_constant_cutoff_eV,
                )?;
                photon_diagnostics.push(diagnostics);
                Some(source)
            } else {
                None
            };
            steps.push(StepOut {
                step: si + 1,
                t_s: t_cum,
                flux: *fl,
                flux_weighted_time_s: flux_weighted_time_cum,
                fluence_n_cm2: spec
                    .projectile
                    .is_neutron()
                    .then_some(base_flux_total * flux_weighted_time_cum),
                fluence_particles_cm2: (!spec.projectile.is_neutron())
                    .then_some(base_flux_total * flux_weighted_time_cum),
                inventory: inv,
                activity_Bq_per_g: act,
                heat_W_per_g: heat,
                leakage_atoms_per_g: pos[ch.leak]
                    .checked_sub(0)
                    .and_then(|p| {
                        if p != usize::MAX {
                            y.get(p).copied()
                        } else {
                            None
                        }
                    })
                    .unwrap_or(0.0),
                negative_atoms_zeroed: zeroed,
                total_atoms_per_g: y.iter().sum(),
                n_states_populated: y.iter().filter(|v| **v > 0.0).count(),
                numerical_floor_atoms_per_g: floor,
                n_states_below_floor: n_below,
                atoms_below_floor: atoms_below,
                heat_bound_from_below_floor_W_per_g: heat_below,
                photon_source,
                uncertainty,
                radiological,
            });
        }
        profiler.finish("schedule_solve_diagnostics", solve_started);

        let pathways_started = profiler.start();
        // ---- pathways (trace mode only): give every source its own unit state so one factorisation serves all of them,
        // then each source's contribution is the solve started from that unit state alone. Exact by linearity.
        let want_paths = spec
            .options
            .outputs
            .as_ref()
            .is_none_or(|o| o.iter().any(|x| x == "pathways"));
        let mut pathways: Vec<BTreeMap<String, Vec<Pathway>>> = Vec::new();
        let mut closure = 0.0f64;
        if mode == "trace" && want_paths && !sources.is_empty() {
            let mut agg: BTreeMap<(usize, (i32, i32)), f64> = BTreeMap::new();
            for (row, rate, from) in &sources {
                if pos[*row] != usize::MAX {
                    *agg.entry((*row, *from)).or_insert(0.0) += rate;
                }
            }
            let labels: Vec<_> = agg.into_iter().collect();
            let ns = labels.len();
            let mp = m + ns; // one extra unit state per source
            let mut ptrip: Vec<(usize, usize, C64)> = dsub
                .iter()
                .filter(|(i, j, _)| *i != pos[ch.unit] && *j != pos[ch.unit])
                .cloned()
                .collect();
            for (k, ((row, _), rate)) in labels.iter().enumerate() {
                ptrip.push((pos[*row], m + k, C64::new(*rate, 0.0)));
            }
            let mut cols: Vec<Vec<f64>> = (0..ns)
                .map(|k| {
                    let mut v = vec![0.0f64; mp];
                    v[m + k] = 1.0;
                    v
                })
                .collect();
            let mut t_acc = 0.0;
            for (dt, fl) in sched.iter() {
                let mut trip = ptrip.clone();
                if *fl > 0.0 {
                    for (i, j, v) in rsub.iter() {
                        if *i != pos[ch.unit] && *j != pos[ch.unit] {
                            trip.push((*i, *j, v * C64::new(*fl, 0.0)));
                        }
                    }
                } else {
                    // during cooling the sources are off: drop the unit-state feeds
                    trip.retain(|(_, j, _)| *j < m);
                }
                let a = Csc::from_triplets(mp, &trip);
                cols = crate::cram::step_multi(&a, &cols, *dt, &c)?;
                if *fl <= 0.0 {
                    for col in cols.iter_mut() {
                        for k in 0..ns {
                            col[m + k] = 0.0;
                        }
                    }
                }
                t_acc += dt;
                let _ = t_acc;
                // report at every step, ranked, keeping chains above 1e-6 of the nuclide
                let mut per: BTreeMap<String, Vec<Pathway>> = BTreeMap::new();
                for (k, col) in cols.iter().enumerate() {
                    let ((row, from), _) = &labels[k];
                    for (idx, v) in col.iter().enumerate().take(m) {
                        if *v <= 0.0 {
                            continue;
                        }
                        let g = keep[idx];
                        if g == ch.leak || g == ch.unit {
                            continue;
                        }
                        per.entry(name_of(ch.keys[g].0, ch.keys[g].1))
                            .or_default()
                            .push(Pathway {
                                from: name_of(from.0, from.1),
                                first_product: name_of(ch.keys[*row].0, ch.keys[*row].1),
                                atoms_per_g: *v,
                                fraction: 0.0,
                            });
                    }
                }
                // closure is measured on the complete decomposition, before any chain is dropped for reporting
                for n in &steps[pathways.len()].inventory {
                    if let Some(v) = per.get(&n.nuclide) {
                        let sum: f64 = v.iter().map(|p| p.atoms_per_g).sum();
                        if n.atoms_per_g > 0.0 {
                            closure = closure.max((sum - n.atoms_per_g).abs() / n.atoms_per_g);
                        }
                    }
                }
                for v in per.values_mut() {
                    let tot: f64 = v.iter().map(|p| p.atoms_per_g).sum();
                    for p in v.iter_mut() {
                        p.fraction = if tot > 0.0 { p.atoms_per_g / tot } else { 0.0 };
                    }
                    v.sort_by(|a, b| b.atoms_per_g.partial_cmp(&a.atoms_per_g).unwrap());
                    v.retain(|p| p.fraction >= 1e-6); // reporting threshold only; the closure above used every chain
                }
                pathways.push(per);
            }
        }
        profiler.finish("pathway_decomposition", pathways_started);

        let reporting_started = profiler.start();
        // ---- ledger
        let rate_pruning: Vec<_> = rate_pruned
            .iter()
            .filter_map(|(index, atoms_bound, feed_bound)| {
                ch.keys.get(*index).map(|key| {
                    serde_json::json!({
                        "nuclide": name_of(key.0, key.1),
                        "atoms_per_g_bound": atoms_bound,
                        "feed_atoms_per_g_s_bound": feed_bound,
                    })
                })
            })
            .collect();
        let rate_pruning_heat_bound_W_per_g: f64 = rate_pruned
            .iter()
            .filter_map(|(index, atoms_bound, _)| ch.keys.get(*index).map(|key| (key, atoms_bound)))
            .filter_map(|(key, atoms_bound)| {
                nuclides.get(key).map(|nuclide| (nuclide, atoms_bound))
            })
            .map(|(nuclide, atoms_bound)| {
                nuclide.lambda()
                    * atoms_bound
                    * (nuclide.e_light() + nuclide.e_em() + nuclide.e_heavy())
                    * EV
            })
            .sum();
        let mut library_convergence_flags = Vec::new();
        let mut library_target_limitations = Vec::new();
        if let Some(targets) = idxj["targets"].as_array() {
            for target in targets {
                let key = (
                    target["za"].as_i64().unwrap_or(0) as i32,
                    target["liso"].as_i64().unwrap_or(0) as i32,
                );
                if !bulk_inv.contains_key(&key) {
                    continue;
                }
                if let Some(flag) = target["convergence_flag"].as_str() {
                    library_convergence_flags.push(serde_json::json!({
                        "nuclide": name_of(key.0, key.1), "flag": flag,
                    }));
                }
                if let Some(entries) = target["ledger"].as_array() {
                    for entry in entries.iter().filter_map(|value| value.as_str()) {
                        library_target_limitations.push(serde_json::json!({
                            "nuclide": name_of(key.0, key.1), "limitation": entry,
                        }));
                    }
                }
            }
        }
        let explicit_isotope_masses: Vec<_> = cdiag
            .explicit_nuclides
            .iter()
            .map(|(name, (za, liso, molar_mass, atoms_per_g))| {
                let nuclide = nuclides.get(&(*za, *liso));
                let source = if nuclide.is_none() {
                    None
                } else if self.decay_fallback_keys.contains(&(*za, *liso)) {
                    self.decay_fallback.as_deref()
                } else {
                    Some(self.decay_primary.as_str())
                };
                serde_json::json!({
                    "nuclide": name,
                    "ZA": za,
                    "LISO": liso,
                    "awr": nuclide.map(|value| value.awr),
                    "neutron_mass_u": composition::NEUTRON_MASS_AMU,
                    "molar_mass_g_mol": molar_mass,
                    "atoms_per_g": atoms_per_g,
                    "source": source,
                })
            })
            .collect();
        // Reaction rows exist for the whole library. Fission diagnostics describe parents that were actually present
        // in this solve, not every zero-population actinide whose cross section happened to collapse nonzero.
        let mut active_fission_parents: std::collections::HashSet<String> = bulk
            .keys()
            .map(|index| {
                let key = ch.keys[*index];
                format!("{}_{}", key.0, key.1)
            })
            .collect();
        for step in &steps {
            for nuclide in &step.inventory {
                active_fission_parents.insert(format!(
                    "{}_{}",
                    nuclide.Z * 1000 + nuclide.A,
                    nuclide.LISO
                ));
            }
        }
        led.fission_no_yields
            .retain(|parent, _| active_fission_parents.contains(parent));
        led.fission_product_leakage
            .retain(|entry| active_fission_parents.contains(&entry.parent));
        led.fission_balance
            .retain(|parent, _| active_fission_parents.contains(parent));
        let fission_yield_inputs: Vec<_> = self
            .fission_yield_inputs
            .iter()
            .map(|(reference, computed_sha, parent)| {
                let data = &self.fission_yields[parent];
                serde_json::json!({
                    "path": reference.path,
                    "sha256_declared": reference.sha256,
                    "sha256": computed_sha,
                    "parent": name_of(parent.0, parent.1),
                    "parent_ZA": parent.0,
                    "parent_LISO": parent.1,
                    "awr": data.awr,
                    "independent": data.independent.iter().map(|table| serde_json::json!({
                        "energy_eV": table.energy_ev,
                        "products": table.products.len(),
                        "sum": table.sum,
                    })).collect::<Vec<_>>(),
                    "cumulative_tables": data.cumulative.len(),
                })
            })
            .collect();
        let schedule_ledger = if spec.projectile.is_neutron() {
            serde_json::json!({
                "segments": sched.len(),
                "flux_weighted_time_s": flux_weighted_time_s,
                "fluence_n_cm2": base_flux_total * flux_weighted_time_s,
            })
        } else {
            serde_json::json!({
                "segments": sched.len(),
                "flux_weighted_time_s": flux_weighted_time_s,
                "fluence_particles_cm2": base_flux_total * flux_weighted_time_s,
            })
        };
        let mut ledger = serde_json::json!({
            "mode": mode,
            "max_burnup_fraction": led.burnup_fraction_max,
            "max_burnup_optical_depth": led.burnup_optical_depth_max,
            "max_burnup_nuclide": led.burnup_nuclide.map(|key| name_of(key.0, key.1)),
            "composition_basis": spec.material.basis,
            "composition_input_total": composition_total,
            "composition_weight_percent_total": if spec.material.basis == "wt_percent" { Some(composition_total) } else { None },
            "composition_not_summing_to_100": spec.material.basis == "wt_percent" && (composition_total - 100.0).abs() > 1e-9,
            "composition_isotopes_absent_from_decay_library": absent.iter().map(|(z, l)| format!("{z}_{l}")).collect::<Vec<_>>(),
            "composition_elements_unknown": cdiag.unknown,
            "explicit_isotope_masses": explicit_isotope_masses,
            "products_no_evaluated_decay_data": led.products_no_decay_data,
            "fission_no_yields_to_leakage": led.fission_no_yields,
            "fission_yield_products_to_leakage": led.fission_product_leakage,
            "fission_yield_balance": led.fission_balance,
            "fission_yield_selection": fission_yield_selection,
            "products_unmapped_to_leakage": led.products_unmapped,
            "isomer_state_absent_from_decay_library_used_ground": led.isomer_fell_back_to_ground,
            "targets_absent_from_decay_library": led.targets_absent_from_decay_lib.iter().map(|(z, l)| format!("{z}_{l}")).collect::<Vec<_>>(),
            "bulk_production_dropped": led.bulk_production_dropped.len(),
            "decay_daughters_missing": ch.ledger.daughters_missing.len(),
            "spontaneous_fission_branches_to_leakage": ch.ledger.sf_branches,
            "decay_nuclides_from_fallback": n_fallback,
            "negative_atoms_zeroed_per_step": steps.iter().map(|s| s.negative_atoms_zeroed).collect::<Vec<_>>(),
            "rate_pruning": {
                "threshold_atoms_per_g": spec.options.bmin_atoms_per_g,
                "dropped": rate_pruning,
                "removed_heat_W_per_g_bound": rate_pruning_heat_bound_W_per_g,
            },
            "library_convergence_flags": library_convergence_flags,
            "library_target_limitations": library_target_limitations,
            "numerical_floor": {
                "alpha0": c.alpha0,
                "note": "CRAM's absolute error floors at alpha0; populations below alpha0 * max(N) are indistinguishable from zero and are reported, not removed",
                "worst_heat_bound_fraction": steps.iter().map(|s| if s.heat_W_per_g.total > 0.0 { s.heat_bound_from_below_floor_W_per_g / s.heat_W_per_g.total } else { 0.0 }).fold(0.0f64, f64::max),
                "max_states_below_floor": steps.iter().map(|s| s.n_states_below_floor).max().unwrap_or(0),
            },
            "bulk_background_heat_W_per_g": bulk_heat,
            "photon_spectra": photon_diagnostics,
            "schedule": schedule_ledger,
            "assembly": {"n_bulk_isotopes": bulk.len(), "n_decay_triplets": d_src.len(), "n_reaction_triplets": r_src.len(),
                         "n_library_rows": lib.rows().len(), "n_chain_nuclides": ch.keys.len(), "flux_total": phi.iter().sum::<f64>()},
        });
        if !spec.projectile.is_neutron() {
            ledger.as_object_mut().expect("ledger is an object").insert(
                "projectile".into(),
                serde_json::Value::String(spec.projectile.name().into()),
            );
        }
        if let Some(prepared) = &self.radiological {
            let coverage_per_step = steps
                .iter()
                .filter_map(|step| {
                    step.radiological.as_ref().map(|radiological| {
                        serde_json::json!({
                            "step": step.step,
                            "responses": radiological.responses.iter().map(|response| serde_json::json!({
                                "id": &response.id,
                                "covered_activity_Bq_per_g": response.covered_activity_Bq_per_g,
                                "missing_activity_Bq_per_g": response.missing_activity_Bq_per_g,
                                "activity_coverage_fraction": response.activity_coverage_fraction,
                                "contributing_nuclide_count": response.contributing_nuclide_count,
                                "missing_active_nuclides": &response.missing_active_nuclides,
                            })).collect::<Vec<_>>(),
                        })
                    })
                })
                .collect::<Vec<_>>();
            ledger.as_object_mut().expect("ledger is an object").insert(
                "radiological".into(),
                serde_json::json!({
                    "table_sha256": &prepared.sha256,
                    "require_complete": prepared.options.require_complete,
                    "formula": {
                        "clearance_index_and_waste_index": "sum(1000 * activity_Bq_per_g / limit_Bq_per_kg)",
                        "ingestion_dose_and_inhalation_dose": "sum(activity_Bq_per_g * dose_coefficient_Sv_per_Bq)",
                    },
                    "scenario_semantics": "the caller selects and hash-pins the regulatory or dose-coefficient table; ACTINV applies no intake mass, occupancy, frequency, retention factor or safety factor",
                    "missing_coefficient_semantics": "positive activity without a coefficient is excluded from the value, reported exactly, and never treated as a zero coefficient",
                    "coverage_per_step": coverage_per_step,
                }),
            );
        }
        if let (Some(prepared), Some(options), Some(runtime)) =
            (&self.covariance, &spec.uncertainty, &uncertainty_runtime)
        {
            ledger.as_object_mut().expect("ledger is an object").insert(
                "uncertainty".into(),
                serde_json::json!({
                    "method": "local first-order propagation of spectrum-collapsed ENDF-6 MF=33 covariance",
                    "band_name": "MF=33 nuclear-data band",
                    "confidence_level": options.confidence_level,
                    "normal_multiplier": runtime.normal_multiplier,
                    "collapse_convention": "group-integrated flux is uniform in energy within each activation group; activation cross sections are groupwise constant; the union of activation and covariance boundaries is integrated",
                    "active_parameters": runtime.parameters.len(),
                    "covered_parameters": runtime.covered_parameter_positions.len(),
                    "uncovered_library_rows": runtime.uncovered_library_rows,
                    "absent_cross_parameter_pairs": runtime.absent_cross_parameter_pairs,
                    "maximum_covariance_asymmetry_barn2": runtime.maximum_covariance_asymmetry_barn2,
                    "selected_cram_order": spec.options.cram_order,
                    "comparison_cram_order": if spec.options.cram_order == 16 { 48 } else { 16 },
                    "excluded_sources": [
                        "decay data and MF=32 resonance-parameter covariance",
                        "MF=40 radionuclide-production and yield covariance",
                        "fission/product-yield covariance",
                        "incident-flux uncertainty",
                        "material-composition uncertainty",
                        "response-coefficient uncertainty",
                        "model discrepancy"
                    ],
                    "covariance_builder_fingerprint": prepared.index.get("builder_fingerprint"),
                    "covariance_source_manifest_sha256": prepared.index.get("source_manifest_sha256"),
                }),
            );
        }
        let mut certificate = serde_json::json!({
            "solver": concat!("actinv-core ", env!("CARGO_PKG_VERSION")),
            "entry_point": entry_point,
            "library": spec.library.path, "library_sha256_declared": spec.library.sha256,
            "decay_primary": spec.decay.primary, "decay_fallback": spec.decay.fallback,
            "inputs": {
                "library": {"path": spec.library.path, "sha256": library_sha},
                "library_index": {"path": idx_path, "sha256": index_sha},
                "decay_primary": {"path": spec.decay.primary, "sha256": decay_primary_sha},
                "decay_fallback": spec.decay.fallback.as_ref().filter(|p| !p.is_empty()).zip(decay_fallback_sha.as_ref())
                    .map(|(path, sha)| serde_json::json!({"path": path, "sha256": sha})),
                "photon_response": spec.photon.response.as_ref().zip(response_sha.as_ref())
                    .map(|(r, sha)| serde_json::json!({"path": r.path, "sha256": sha})),
                "fission_yields": fission_yield_inputs,
            },
            "tables_provenance": composition::provenance(),
            "cram": format!("CRAM-{}, incomplete partial fractions (Pusa, NSE 182:297, 2016)", spec.options.cram_order),
            "mode": mode, "prune": spec.options.prune, "bmin_atoms_per_g": spec.options.bmin_atoms_per_g,
            "material_basis": spec.material.basis,
            "photon": {"group_structure": spec.photon.group_structure, "build_up_factor": spec.photon.build_up_factor,
                       "gamma_constant_cutoff_eV": spec.photon.gamma_constant_cutoff_eV},
            "fission_yields": {
                "energy": spec.fission_yields.energy,
                "fixed_energy_eV": spec.fission_yields.fixed_energy_eV,
                "selection": fission_yield_selection,
            },
        });
        if let (Some(prepared), Some(options)) = (&self.covariance, &spec.uncertainty) {
            let certificate_object = certificate
                .as_object_mut()
                .expect("certificate is an object");
            certificate_object.insert(
                "uncertainty".into(),
                serde_json::json!({
                    "method": "local first-order propagation of spectrum-collapsed ENDF-6 MF=33 covariance",
                    "band_name": "MF=33 nuclear-data band",
                    "confidence_level": options.confidence_level,
                    "selected_cram_order": spec.options.cram_order,
                    "comparison_cram_order": if spec.options.cram_order == 16 { 48 } else { 16 },
                    "collapse_convention": "uniform flux per unit energy inside activation groups, integrated on the union boundary grid",
                    "excluded_sources": ["decay/MF=32", "production/MF=40 and yields", "flux", "composition", "response coefficients", "model discrepancy"],
                }),
            );
            certificate_object
                .get_mut("inputs")
                .and_then(serde_json::Value::as_object_mut)
                .expect("certificate inputs is an object")
                .insert(
                    "covariance".into(),
                    serde_json::json!({
                        "path": options.covariance.path,
                        "sha256_declared": options.covariance.sha256,
                        "sha256": prepared.sha256,
                        "index": {"path": prepared.index_path, "sha256": prepared.index_sha256},
                        "source_manifest_sha256": prepared.index.get("source_manifest_sha256"),
                        "builder_fingerprint": prepared.index.get("builder_fingerprint"),
                    }),
                );
        }
        if let Some(prepared) = &self.radiological {
            let mut metadata = prepared.table.certificate_metadata();
            let metadata_object = metadata
                .as_object_mut()
                .expect("radiological metadata is an object");
            metadata_object.insert(
                "require_complete".into(),
                serde_json::Value::Bool(prepared.options.require_complete),
            );
            metadata_object.insert(
                "scenario_semantics".into(),
                serde_json::Value::String(
                    "the caller selects and hash-pins the regulatory or dose-coefficient table; ACTINV applies no intake mass, occupancy, frequency, retention factor or safety factor".into(),
                ),
            );
            metadata_object.insert(
                "missing_coefficient_semantics".into(),
                serde_json::Value::String(
                    "positive activity without a coefficient is excluded from the value, reported exactly, and never treated as a zero coefficient".into(),
                ),
            );
            let certificate_object = certificate
                .as_object_mut()
                .expect("certificate is an object");
            certificate_object.insert("radiological".into(), metadata);
            certificate_object
                .get_mut("inputs")
                .and_then(serde_json::Value::as_object_mut)
                .expect("certificate inputs is an object")
                .insert(
                    "radiological_table".into(),
                    serde_json::json!({
                        "path": &prepared.options.table.path,
                        "sha256_declared": &prepared.options.table.sha256,
                        "sha256": &prepared.sha256,
                    }),
                );
        }
        if !spec.projectile.is_neutron() {
            certificate
                .as_object_mut()
                .expect("certificate is an object")
                .insert(
                    "projectile".into(),
                    serde_json::Value::String(spec.projectile.name().into()),
                );
        }
        profiler.finish("ledger_certificate_assembly", reporting_started);
        Ok(RunResult {
            spec_title: spec.title.clone(),
            entry_point: entry_point.into(),
            projectile: (!spec.projectile.is_neutron()).then(|| spec.projectile.name().to_owned()),
            mode,
            pruned_states: m,
            total_states: ch.n,
            steps,
            pathways,
            pathway_closure: closure,
            ledger,
            certificate,
            ms: t0.elapsed().as_secs_f64() * 1e3,
        })
    }
}

pub fn run(spec: &Spec, entry_point: &str) -> Result<RunResult, String> {
    let started = std::time::Instant::now();
    let mut profiler = RunProfiler::from_environment();
    let prepared = PreparedRun::prepare_profiled(spec, &mut profiler)?;
    let result = prepared.run_started_profiled(spec, entry_point, started, &mut profiler)?;
    profiler.emit(started.elapsed());
    Ok(result)
}

#[cfg(test)]
mod projectile_output_tests {
    use super::{group_structure_mismatch, Heat, StepOut};
    use std::collections::BTreeMap;

    fn step(neutron: bool) -> StepOut {
        StepOut {
            step: 1,
            t_s: 2.0,
            flux: 3.0,
            flux_weighted_time_s: 6.0,
            fluence_n_cm2: neutron.then_some(12.0),
            fluence_particles_cm2: (!neutron).then_some(12.0),
            inventory: Vec::new(),
            activity_Bq_per_g: BTreeMap::new(),
            heat_W_per_g: Heat {
                total: 0.0,
                alpha: 0.0,
                beta: 0.0,
                gamma: 0.0,
            },
            leakage_atoms_per_g: 0.0,
            negative_atoms_zeroed: 0.0,
            total_atoms_per_g: 0.0,
            n_states_populated: 0,
            numerical_floor_atoms_per_g: 0.0,
            n_states_below_floor: 0,
            atoms_below_floor: 0.0,
            heat_bound_from_below_floor_W_per_g: 0.0,
            photon_source: None,
            uncertainty: None,
            radiological: None,
        }
    }

    #[test]
    fn neutron_and_charged_fluence_fields_are_disjoint() {
        let neutron = serde_json::to_value(step(true)).unwrap();
        assert_eq!(neutron["fluence_n_cm2"], 12.0);
        assert!(neutron.get("fluence_particles_cm2").is_none());

        let charged = serde_json::to_value(step(false)).unwrap();
        assert_eq!(charged["fluence_particles_cm2"], 12.0);
        assert!(charged.get("fluence_n_cm2").is_none());
    }

    #[test]
    fn group_structure_comparison_distinguishes_absent_custom_and_mismatch() {
        assert!(!group_structure_mismatch(None, Some("fispact-709")));
        assert!(!group_structure_mismatch(Some("fispact-709"), None));
        assert!(!group_structure_mismatch(
            Some("fispact-709"),
            Some("fispact-709")
        ));
        assert!(!group_structure_mismatch(
            Some("custom"),
            Some("fispact-709")
        ));
        assert!(group_structure_mismatch(
            Some("fispact-709"),
            Some("fispact-162")
        ));
    }
}
