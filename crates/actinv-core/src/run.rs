#![allow(non_snake_case)] // field names are the JSON wire format (Z, A, LISO, heat_W_per_g)
//! spec -> result. This is the single path the CLI, the Python API and the harness all take (P5 G3).
use crate::chain::{self, RateLedger};
use crate::cram::{step as cram_step, step_with_tangents, Cram};
use crate::damage::{DamageStepOut, PreparedDamageTable};
use crate::depletion::{DepletionTracker, LossCovariance, Tracked};
use crate::fission_count::{self, FissionStepOut};
use crate::gas;
use crate::photon::{self, PhotonDiagnostics, PhotonResponse, PhotonSourceOut};
use crate::power::{self, summarize_kerma, KermaStepOut, PowerStepOut};
use crate::quantity::{AtomsPerGram, FluxMultiplier, Kelvin, Seconds};
use crate::radiological::{PreparedRadiologicalTable, RadiologicalStepOut};
use crate::sparse::Csc;
use crate::spec::{
    DamageOptions, DecayRef, FissionYieldOptions, HashedFileRef, KermaOptions, LibraryRef,
    PhotonOptions, PhysicalInputs, PhysicalStep, Projectile, RadiologicalOptions,
    SelfShieldingOptions, Spec, Spectrum, SpontaneousFissionOptions, StepLibrary,
    UncertaintyOptions,
};
use crate::summary::{self, StateAtoms, StepSummary};
use crate::uncertainty::{
    self as uncertainty_report, BandInput, ChannelData, DecayParameter, DecaySensitivityOut,
    DesignParameterEntry, DesignReactionEntry, DesignReport, FluxParameter, FluxSensitivityOut,
    IsomerChannelEntry, IsomerPathwayProduct, IsomerPathwayShare, IsomerReport,
    IsomerVarianceShares, SensitivityOut, SensitivityParameter, StepUncertainty, VoiEntry,
    VoiReport, VoiUnranked, YieldParameter, YieldSensitivityOut,
};
use actinv_data::{
    composition, covariance, decay, fission,
    groups::GroupStructure,
    kerma::KermaTable,
    library::{self, ReactionLibrary},
    prepared as prepared_data,
};
use num_complex::Complex64 as C64;
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};
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
/// P92 gas: one light nuclide's current inventory, production and appm.
#[derive(serde::Serialize)]
pub struct GasSpecies {
    pub atoms_per_g: f64,
    /// `atoms_per_g` minus this nuclide's initial (material) population.
    pub produced_atoms_per_g: f64,
    /// `produced_atoms_per_g` per 1e6 initial atoms of the whole material.
    pub appm: f64,
    /// P95: `atoms_per_g` (initial content included) per 1e6 initial atoms of the whole
    /// material — the convention of FISPACT-II's printed `APPM OF`.
    pub inventory_appm: f64,
}
/// P92 gas: emitted only when `options.gas` is true.
#[derive(serde::Serialize)]
pub struct GasOut {
    pub H1: GasSpecies,
    pub H2: GasSpecies,
    pub H3: GasSpecies,
    pub He3: GasSpecies,
    pub He4: GasSpecies,
    /// H1 + H2 + H3 appm.
    pub H_appm: f64,
    /// He3 + He4 appm.
    pub He_appm: f64,
    /// H1 + H2 + H3 inventory appm (P95).
    pub H_inventory_appm: f64,
    /// He3 + He4 inventory appm (P95).
    pub He_inventory_appm: f64,
    /// The material's total initial atoms per gram — the appm normalization denominator.
    pub initial_atoms_per_g: f64,
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
    /// Atoms accumulated in the dedicated first-order removal sink; present only when a
    /// schedule step declares `removal`.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub removed_atoms_per_g: Option<f64>,
    pub negative_atoms_zeroed: f64,
    /// Sum over the whole state vector, leakage and removal sinks included. It also counts the
    /// unit source state, which holds exactly 1.0 in trace mode and in coupled runs with `feed`;
    /// `n_states_populated` counts that state too. Kept as-is: the value is a pinned study response.
    pub total_atoms_per_g: f64,
    pub n_states_populated: usize,
    /// Legacy CRAM asymptotic scale alpha0 * max(N), not a bound on total numerical
    /// error: floating-point factorization/solve errors can exceed this scale.
    /// Below-scale populations are reported, never silently removed.
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
    /// NRT damage observables; present only when the spec carries a `damage` section.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub damage: Option<DamageStepOut>,
    /// P92 gas production; present only when `options.gas` is true.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub gas: Option<GasOut>,
    /// P122 complete step summary; present only when the spec carries a `summary` section.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub summary: Option<StepSummary>,
    /// P125 fission count, burn-up and spontaneous-fission neutron source; present only when
    /// `options.fission` is set or `fission_yields.spontaneous` supplies nu-bar data.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub fission: Option<FissionStepOut>,
    /// P126 instantaneous kerma heating at the end of the step; present only when the spec
    /// carries a `kerma` section.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub kerma: Option<KermaStepOut>,
    /// P126 power-normalization record; present only on power-driven steps.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub power: Option<PowerStepOut>,
}
/// A trace-mode pathway source: (product row fed, rate at unit multiplier, bulk nuclide it came from).
type PathwaySource = (usize, f64, (i32, i32));

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
    /// P65 certified screening: emitted only when `options.screen` is
    /// declared. Absence is byte-identical to pre-P65 output.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub screen: Option<serde_json::Value>,
    /// largest relative disagreement between the summed pathway contributions and the main solve
    pub pathway_closure: f64,
    /// P58: per-step pathway aggregation by isomer product; emitted only when
    /// `uncertainty.isomer` is requested. Absence is byte-identical to
    /// pre-P58 output.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub isomer_pathway_shares: Option<Vec<IsomerPathwayShare>>,
    /// P127: full routes analysis (`crate::pathways::PathwayReport`); emitted only when the spec declares
    /// `pathways`. Absence is byte-identical to pre-P127 output.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub pathway_routes: Option<serde_json::Value>,
    pub ledger: serde_json::Value,
    pub certificate: serde_json::Value,
    pub ms: f64,
}

/// Nominal value of a response name on a step — the subset of the CLI's
/// edge vocabulary the P65 screen certificate supports.
fn nominal_response(step: &StepOut, response: &str) -> f64 {
    if let Some(kind) = response.strip_prefix("heat.") {
        match kind {
            "total" => step.heat_W_per_g.total,
            "alpha" => step.heat_W_per_g.alpha,
            "beta" => step.heat_W_per_g.beta,
            "gamma" => step.heat_W_per_g.gamma,
            _ => 0.0,
        }
    } else if response == "activity.total" {
        step.activity_Bq_per_g.values().sum()
    } else if let Some(name) = response.strip_prefix("activity:") {
        step.activity_Bq_per_g.get(name).copied().unwrap_or(0.0)
    } else {
        0.0
    }
}

pub(crate) fn name_of(za: i32, liso: i32) -> String {
    let s = composition::symbol_of(za / 1000);
    if liso > 0 {
        format!("{s}{}m{liso}", za % 1000)
    } else {
        format!("{s}{}", za % 1000)
    }
}

/// Moves the fraction `1 - kept` of every listed state of `vector` to the removal sink at
/// `sink` and returns the amount moved (the PARTITION operator, one element). The same linear
/// map acts on the state, its tangents and the comparison solution.
fn partition_move(vector: &mut [f64], members: &[usize], sink: usize, kept: f64) -> f64 {
    let mut moved_total = 0.0;
    for &k in members {
        let moved = vector[k] * (1.0 - kept);
        vector[k] -= moved;
        vector[sink] += moved;
        moved_total += moved;
    }
    moved_total
}

fn index_path(library_path: &str) -> String {
    match library_path.strip_suffix(".npz") {
        Some(stem) => format!("{stem}_index.json"),
        None => format!("{library_path}_index.json"),
    }
}

type ShaCacheKey = (String, u64, u128);

/// Fission-yield sampling input: (parent nuclide key, yield, rel_sigma).
pub(crate) type YieldInputs = Vec<((i32, i32, i32, i32), f64, f64)>;

/// Process-wide SHA-256 cache keyed by (path, length, modification time).
fn sha_cache() -> &'static Mutex<HashMap<ShaCacheKey, String>> {
    static CACHE: OnceLock<Mutex<HashMap<ShaCacheKey, String>>> = OnceLock::new();
    CACHE.get_or_init(|| Mutex::new(HashMap::new()))
}

fn modified_nanos(metadata: &std::fs::Metadata) -> u128 {
    metadata
        .modified()
        .ok()
        .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
        .map(|d| d.as_nanos())
        .unwrap_or(0)
}

/// Read an input once and hash exactly those bytes, so the certified SHA-256 always
/// names the bytes that are parsed: hashing and then re-opening the file for parsing
/// left a window in which it could be replaced. The digest also primes the cache.
fn read_verified(path: &str, declared: Option<&str>) -> Result<(Vec<u8>, String), String> {
    let before = std::fs::metadata(path).map_err(|e| format!("cannot stat {path}: {e}"))?;
    let bytes = std::fs::read(path).map_err(|e| format!("cannot read {path}: {e}"))?;
    let after = std::fs::metadata(path).map_err(|e| format!("cannot restat {path}: {e}"))?;
    if after.len() != before.len()
        || modified_nanos(&after) != modified_nanos(&before)
        || bytes.len() as u64 != after.len()
    {
        return Err(format!("input changed while reading: {path}"));
    }
    let computed = format!("{:x}", Sha256::digest(&bytes));
    if let Some(expected) = declared {
        if !computed.eq_ignore_ascii_case(expected) {
            return Err(format!(
                "SHA-256 mismatch for {path}: declared {expected}, computed {computed}"
            ));
        }
    }
    sha_cache()
        .lock()
        .map_err(|_| "SHA-256 cache lock poisoned")?
        .insert(
            (path.to_string(), after.len(), modified_nanos(&after)),
            computed.clone(),
        );
    Ok((bytes, computed))
}

/// `read_verified` for UTF-8 text inputs (ENDF, JSON).
fn read_verified_text(path: &str, declared: Option<&str>) -> Result<(String, String), String> {
    let (bytes, sha) = read_verified(path, declared)?;
    let text = String::from_utf8(bytes).map_err(|_| format!("{path} is not UTF-8 text"))?;
    Ok((text, sha))
}

fn file_sha256(path: &str) -> Result<String, String> {
    let metadata = std::fs::metadata(path).map_err(|e| format!("cannot stat {path}: {e}"))?;
    let key = (path.to_string(), metadata.len(), modified_nanos(&metadata));
    let cache = sha_cache();
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
    if after.len() != key.1 || modified_nanos(&after) != key.2 {
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
    /// A decay-only run (P124): no activation library, no rows and no groups.
    Absent,
}

fn group_structure_mismatch(requested: Option<&str>, stored: Option<&str>) -> bool {
    matches!((requested, stored), (Some(requested), Some(stored)) if requested != "custom" && requested != stored)
}

impl ActivationLibrary {
    fn dense(&self) -> Option<&library::Library> {
        match self {
            Self::Dense(library) => Some(library),
            Self::Groupwise(_) | Self::Collapsed(_) | Self::Absent => None,
        }
    }

    fn is_absent(&self) -> bool {
        matches!(self, Self::Absent)
    }

    fn validate_flux(&self, phi: &[f64]) -> Result<(), String> {
        match self {
            Self::Collapsed(library) => library.validate_flux(phi),
            Self::Dense(_) | Self::Groupwise(_) | Self::Absent => Ok(()),
        }
    }
}

impl ReactionLibrary for ActivationLibrary {
    fn rows(&self) -> &[library::Row] {
        match self {
            Self::Dense(library) => library.rows(),
            Self::Groupwise(library) => library.rows(),
            Self::Collapsed(library) => library.rows(),
            Self::Absent => &[],
        }
    }

    fn group_count(&self) -> usize {
        match self {
            Self::Dense(library) => ReactionLibrary::group_count(library),
            Self::Groupwise(library) => library.group_count(),
            Self::Collapsed(library) => library.group_count(),
            Self::Absent => 0,
        }
    }

    fn boundaries_ev(&self) -> &[f64] {
        match self {
            Self::Dense(library) => library.boundaries_ev(),
            Self::Groupwise(library) => library.boundaries_ev(),
            Self::Collapsed(library) => library.boundaries_ev(),
            Self::Absent => &[],
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
            // no rows exist, so no caller can name one
            Self::Absent => 0.0,
        }
    }

    fn row_cross_section(&self, row: usize, group: usize) -> f64 {
        match self {
            Self::Dense(library) => library.row_cross_section(row, group),
            Self::Groupwise(library) => library.row_cross_section(row, group),
            Self::Collapsed(library) => library.row_cross_section(row, group),
            Self::Absent => 0.0,
        }
    }

    fn fission_average_energy_ev(&self, row: usize, phi: &[f64]) -> Result<Option<f64>, String> {
        match self {
            Self::Dense(library) => library.fission_average_energy_ev(row, phi),
            Self::Groupwise(library) => library.fission_average_energy_ev(row, phi),
            Self::Collapsed(library) => library.fission_average_energy_ev(row, phi),
            Self::Absent => Ok(None),
        }
    }
}

/// One mesh cell's row values from [`PreparedRun::collapse_rows_batched`] (P81):
/// `values[row * stride + offset]` is the one-group value of `row` under `phi`.
#[derive(Clone, Copy)]
pub struct CellCollapse<'a> {
    pub phi: &'a [f64],
    pub values: &'a [f64],
    pub stride: usize,
    pub offset: usize,
}

/// P93: a mesh cell's pre-rebin flux-file origin, carried alongside the
/// rebinned `Spec.spectrum` so the flux channel's input-group parameters are
/// the flux file's own source groups (its own boundaries and per-group
/// statistical error), not the activation-library-aligned spectrum the cell
/// actually solves with. `source_flux_per_group` and `source_relative_error`
/// are in ascending-energy order (canonical flux files carry no `descending`
/// concept).
#[derive(Clone, Copy)]
pub struct MeshFluxOrigin<'a> {
    pub source_boundaries_eV: &'a [f64],
    pub source_flux_per_group: &'a [f64],
    pub source_relative_error: Option<&'a [f64]>,
}

/// The groupwise library with one cell's batched row values in place of `collapse_row` for the
/// run's base spectrum `phi` (the same slice, by address). Every other call goes to the library.
struct BatchedView<'a> {
    base: &'a ActivationLibrary,
    phi: &'a [f64],
    cell: CellCollapse<'a>,
}

impl ReactionLibrary for BatchedView<'_> {
    fn rows(&self) -> &[library::Row] {
        self.base.rows()
    }

    fn group_count(&self) -> usize {
        self.base.group_count()
    }

    fn boundaries_ev(&self) -> &[f64] {
        self.base.boundaries_ev()
    }

    fn collapse_row(
        &self,
        row: usize,
        phi: &[f64],
        flux_denominator: f64,
        first_flux_group: usize,
        last_flux_group: usize,
    ) -> f64 {
        if std::ptr::eq(phi, self.phi) {
            self.cell.values[row * self.cell.stride + self.cell.offset]
        } else {
            self.base.collapse_row(
                row,
                phi,
                flux_denominator,
                first_flux_group,
                last_flux_group,
            )
        }
    }

    fn collapse_row_scaled(
        &self,
        row: usize,
        phi: &[f64],
        flux_denominator: f64,
        first_flux_group: usize,
        last_flux_group: usize,
        scale: &dyn Fn(usize) -> f64,
    ) -> f64 {
        self.base.collapse_row_scaled(
            row,
            phi,
            flux_denominator,
            first_flux_group,
            last_flux_group,
            scale,
        )
    }

    fn row_cross_section(&self, row: usize, group: usize) -> f64 {
        self.base.row_cross_section(row, group)
    }

    fn fission_average_energy_ev(&self, row: usize, phi: &[f64]) -> Result<Option<f64>, String> {
        self.base.fission_average_energy_ev(row, phi)
    }
}

/// A library a schedule step switches to (`Step.library`), verified and loaded beside the base one. It shares the
/// run's decay chain: only its collapsed reaction rates differ, so the state vector needs no remapping.
struct PreparedStepLibrary {
    reference: LibraryRef,
    projectile: Projectile,
    library_sha: String,
    index_path: String,
    index_sha: String,
    library: ActivationLibrary,
    library_targets: Vec<(i32, i32)>,
    group_structure: Option<String>,
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
    decay_overrides_sha: Option<String>,
    decay_overrides_applied: Vec<String>,
    nuclides: HashMap<(i32, i32), decay::Nuclide>,
    decay_fallback_keys: std::collections::HashSet<(i32, i32)>,
    chain: chain::Chain,
    /// P92 gas: ZA of light ground states absent from the decay library and given a stable
    /// stand-in. Always empty when `options.gas` is false.
    gas_missing_light_states: Vec<(i32, i32)>,
    decay_nuclides_from_fallback: usize,
    photon_options: PhotonOptions,
    response: Option<PhotonResponse>,
    response_sha: Option<String>,
    fission_options: FissionYieldOptions,
    fission_yields: HashMap<(i32, i32), fission::FissionYields>,
    fission_yield_inputs: Vec<(HashedFileRef, String, (i32, i32))>,
    /// P125: present only when `fission_yields.spontaneous` is declared.
    spontaneous: Option<PreparedSpontaneous>,
    projectile: Projectile,
    library_group_structure: Option<String>,
    temperature_K: Kelvin,
    uncertainty_options: Option<UncertaintyOptions>,
    covariance: Option<PreparedCovariance>,
    /// P72 provenance record when `uncertainty.unmodeled_table` resolved
    /// the declared unmodeled term; emitted under `certificate.inputs`.
    unmodeled_table: Option<serde_json::Value>,
    /// P77 provenance record when `uncertainty.unmodeled_evalspread`
    /// resolved the unmodeled term from an `actinv-eval-spread-1` artifact.
    unmodeled_evalspread: Option<serde_json::Value>,
    radiological: Option<PreparedRadiological>,
    damage: Option<PreparedDamage>,
    shielding: Option<PreparedShielding>,
    /// Libraries named by schedule-step overrides, in order of first use (empty without overrides).
    step_libraries: Vec<PreparedStepLibrary>,
}

/// P125 spontaneous-fission inputs, resolved at preparation. The yields are already routed in
/// `PreparedRun::chain`; they are kept here for the ledger.
/// (reference, computed sha256, parent states read) for one spontaneous-fission yield file.
type SpontaneousYieldInput = (HashedFileRef, String, Vec<(i32, i32)>);

struct PreparedSpontaneous {
    /// One entry per yield file.
    yield_inputs: Vec<SpontaneousYieldInput>,
    yields: chain::SpontaneousYields,
    /// State -> (nu-bar, where it came from). Empty when no nu-bar input was declared.
    nubar: BTreeMap<(i32, i32), (f64, String)>,
    /// Provenance of each nu-bar input, for the certificate.
    nubar_inputs: Vec<serde_json::Value>,
}

/// Read and verify the spontaneous-fission inputs. Yields are taken at the lowest tabulated
/// incident energy (E = 0 for spontaneous fission); a table entry overrides an ENDF multiplicity
/// for the same state.
fn load_spontaneous(options: &SpontaneousFissionOptions) -> Result<PreparedSpontaneous, String> {
    let mut yields = chain::SpontaneousYields::new();
    let mut yield_inputs = Vec::with_capacity(options.files.len());
    for reference in &options.files {
        let (text, sha) = read_verified_text(&reference.path, Some(&reference.sha256))?;
        let evaluations = fission::parse_tape_text(&text)
            .map_err(|error| format!("{}: {error}", reference.path))?;
        let mut parents = Vec::with_capacity(evaluations.len());
        for evaluation in evaluations {
            let parent = evaluation.parent;
            let effective = evaluation
                .effective(0.0)
                .map_err(|error| format!("{}: {error}", reference.path))?;
            if yields.insert(parent, effective.products).is_some() {
                return Err(format!(
                    "duplicate spontaneous-fission yield parent {}_{}",
                    parent.0, parent.1
                ));
            }
            parents.push(parent);
        }
        yield_inputs.push((reference.clone(), sha, parents));
    }
    let mut nubar: BTreeMap<(i32, i32), (f64, String)> = BTreeMap::new();
    let mut nubar_inputs = Vec::new();
    if let Some(reference) = &options.nubar_endf {
        let (text, sha) = read_verified_text(&reference.path, Some(&reference.sha256))?;
        let parsed = fission::parse_spontaneous_nubar_text(&text)
            .map_err(|error| format!("{}: {error}", reference.path))?;
        for (key, value) in &parsed {
            nubar.insert(
                *key,
                (
                    value.nubar,
                    format!("ENDF MF=1/MT={} ({})", value.mt, value.basis),
                ),
            );
        }
        nubar_inputs.push(serde_json::json!({
            "kind": "endf",
            "path": reference.path,
            "sha256_declared": reference.sha256,
            "sha256": sha,
            "nuclides": parsed.len(),
        }));
    }
    if let Some(reference) = &options.nubar_table {
        let (text, sha) = read_verified_text(&reference.path, Some(&reference.sha256))?;
        let (source, table) = fission_count::parse_nubar_table(&text)
            .map_err(|error| format!("{}: {error}", reference.path))?;
        for (key, value) in &table {
            nubar.insert(*key, (*value, format!("table: {source}")));
        }
        nubar_inputs.push(serde_json::json!({
            "kind": "table",
            "path": reference.path,
            "sha256_declared": reference.sha256,
            "sha256": sha,
            "source": source,
            "nuclides": table.len(),
        }));
    }
    Ok(PreparedSpontaneous {
        yield_inputs,
        yields,
        nubar,
        nubar_inputs,
    })
}

struct PreparedDamage {
    options: crate::spec::DamageOptions,
    sha256: String,
    table: crate::damage::PreparedDamageTable,
}

struct PreparedShielding {
    options: crate::spec::SelfShieldingOptions,
    /// Bondarenko-table hash; `None` in geometry mode.
    sha256: Option<String>,
    table: Option<crate::shielding::PreparedShieldTable>,
    /// Sigmoid-curve geometry (P129); exclusive with `table`.
    geometry: Option<crate::shielding::PreparedGeometry>,
    /// Computed hash of each `geometry.resonance_files` entry, in order.
    resonance_sha256: Vec<String>,
}

/// P126: the verified kerma table of one run. It is read per run rather than prepared with the
/// activation data: it depends on neither the library nor the decay chain, only on the spec.
struct LoadedKerma {
    options: KermaOptions,
    sha256: String,
    table: KermaTable,
}

impl LoadedKerma {
    fn load(
        options: &KermaOptions,
        projectile: &str,
        library_boundaries_ev: &[f64],
    ) -> Result<Self, String> {
        let (text, sha256) = read_verified_text(&options.table.path, Some(&options.table.sha256))
            .map_err(|error| format!("kerma table: {error}"))?;
        let table = KermaTable::from_json(&text, projectile)?;
        if table.boundaries_eV() != library_boundaries_ev {
            return Err(
                "kerma table boundaries_eV do not match the activation library boundaries".into(),
            );
        }
        Ok(Self {
            options: options.clone(),
            sha256,
            table,
        })
    }
}

/// Heating of one atom of chain state `state` at unit flux multiplier under one spectrum.
struct KermaTerm {
    state: usize,
    key: (i32, i32),
    watts_per_atom: f64,
    damage_eV_per_s_per_atom: Option<f64>,
}

/// Every chain nuclide with a kerma row, folded with `flux_ascending` (one entry per spectrum).
fn kerma_terms_for(
    table: &KermaTable,
    keys: &[(i32, i32)],
    flux_ascending: &[f64],
) -> Vec<KermaTerm> {
    let rates = power::rate_per_barn_s(flux_ascending);
    keys.iter()
        .enumerate()
        .filter_map(|(state, &(za, liso))| {
            power::per_atom_rates(table, za, liso, &rates).map(|(watts_per_atom, damage)| {
                KermaTerm {
                    state,
                    key: (za, liso),
                    watts_per_atom,
                    damage_eV_per_s_per_atom: damage,
                }
            })
        })
        .collect()
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
    /// Decay-constant channel parameters; their tangents follow `parameters`.
    decay_parameters: Vec<DecayParameter>,
    /// Fission-yield channel parameters; their tangents follow the decay block.
    yield_parameters: Vec<YieldParameter>,
    /// P93 flux-channel parameters; their tangents follow the yield block.
    flux_parameters: Vec<FluxParameter>,
    /// Whether `uncertainty.channels` requested `"flux"`.
    flux_channel_requested: bool,
    /// `StepUncertainty.method`, chosen once per run: the P93 flux-only
    /// wording when no MF=33 covariance is propagated, else unchanged.
    method: &'static str,
    /// The band's name, following `method`.
    band_name: &'static str,
    directions: Vec<Csc>,
    /// Per-direction applicability: `Some(s)` tags a direction to spectrum
    /// index s (0 = base) so it only acts on steps using that spectrum and
    /// carries the flux multiplier; `None` (decay constants) acts unscaled on
    /// every step.
    parameter_spectrum: Vec<Option<usize>>,
    /// Whether the spec's `uncertainty.channels` requested each channel —
    /// needed to emit a `propagated` channel report even with zero parameters.
    decay_channel_requested: bool,
    yield_channel_requested: bool,
    /// Tangent states, indexed by *propagation* order — dense under the
    /// default path (one per declared parameter), covered-restricted under
    /// `restrict_tangents_to_covered`.
    tangents: Vec<Vec<f64>>,
    /// compact tangent index -> dense parameter position (parameters, then
    /// decay_parameters, then yield_parameters). Identity in the default
    /// path; under restriction, uncovered cross-section parameters keep
    /// their dense positions for reporting but have no tangent row.
    propagated_parameter_positions: Vec<usize>,
    covered_parameter_positions: Vec<usize>,
    covariance_barn2: Vec<f64>,
    /// Nonzero `(row, column, value)` entries of `covariance_barn2` in
    /// row-major order — populated only under tangent restriction, where the
    /// per-response quadratic form iterates them instead of the dense n^2
    /// scan. Summation order matches the dense loop restricted to nonzero
    /// terms, which contributes exactly +-0.0 each — bitwise identical.
    covariance_pairs: Vec<(u32, u32, f64)>,
    /// True under tangent restriction: the quadratic form contracts
    /// `covariance_pairs` (possibly empty when every covered entry is zero)
    /// and `covariance_barn2` is empty.
    contract_pairs: bool,
    uncovered_library_rows: Vec<usize>,
    absent_cross_parameter_pairs: usize,
    maximum_covariance_asymmetry_barn2: f64,
    excluded_blocks: Vec<covariance::ExcludedBlock>,
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
        let Some(&key) = chain.keys.get(global) else {
            continue;
        };
        let Some(nuclide) = nuclides.get(&key) else {
            continue;
        };
        let decay_rate = nuclide.lambda();
        if decay_rate <= 0.0 {
            continue;
        }
        // a hybrid reservoir nuclide's tracked population adds to its bulk activity
        *activity.entry(name_of(key.0, key.1)).or_insert(0.0) += decay_rate * atoms;
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
        "activity.total" => snapshot.activity.values().sum(),
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
            .map(|(global, subspace)| {
                nuclides
                    .get(&chain.keys[global])
                    .map(|nu| nu.lambda())
                    .unwrap_or(chain.lambda[global])
                    * tangent[subspace]
            })
            .unwrap_or(0.0);
    }
    if response == "activity.total" {
        // sensitivity of total activity: sum over radioactive nuclides of
        // λ·dn/dp — propagated through the full covariance, so
        // cross-nuclide terms are not dropped
        let mut value = 0.0;
        for (subspace, derivative) in tangent.iter().enumerate() {
            let global = keep[subspace];
            if global == chain.leak || global == chain.unit {
                continue;
            }
            value += nuclides
                .get(&chain.keys[global])
                .map(|nu| nu.lambda())
                .unwrap_or(chain.lambda[global])
                * derivative;
        }
        return value;
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
        let Some(nuclide) = chain.keys.get(global).and_then(|key| nuclides.get(key)) else {
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

/// P50 value-of-information table (protocol ACTINV-P50): rank each covered
/// uncertainty parameter by its share of this response's propagated variance.
/// MF=33 shares use the full collapsed covariance (`s_i·(Σ·s)_i`, negative
/// under anticorrelation); decay and yield channels are diagonal
/// (`(s_i·σ_i)^2`). Sensitivity-bearing parameters without covariance coverage
/// are summarized under `unranked`, never ranked at zero.
fn build_voi(
    top: usize,
    runtime: &UncertaintyRuntime,
    sensitivities: &[SensitivityOut],
    decay_channel: Option<&ChannelData<DecaySensitivityOut>>,
    yield_channel: Option<&ChannelData<YieldSensitivityOut>>,
    total_variance: f64,
) -> Result<VoiReport, String> {
    let mut entries: Vec<VoiEntry> = Vec::new();
    let mut unranked: BTreeMap<&'static str, VoiUnranked> = BTreeMap::new();
    let mut add_unranked = |channel: &'static str, sensitivity: f64| {
        let bucket = unranked.entry(channel).or_insert(VoiUnranked {
            count: 0,
            sensitivity_l2: 0.0,
        });
        bucket.count += 1;
        bucket.sensitivity_l2 += sensitivity * sensitivity;
    };

    let n_covered = runtime.covered_parameter_positions.len();
    let covered_sensitivity: Vec<f64> = runtime
        .covered_parameter_positions
        .iter()
        .map(|&position| sensitivities[position].value)
        .collect();
    for (row, &position) in runtime.covered_parameter_positions.iter().enumerate() {
        let parameter = &sensitivities[position].parameter;
        if parameter.covariance_excluded {
            continue;
        }
        let mut row_contribution = 0.0;
        for (column, &s_j) in covered_sensitivity.iter().enumerate() {
            row_contribution += runtime.covariance_barn2[row * n_covered + column] * s_j;
        }
        entries.push(VoiEntry {
            channel: "cross_section_mf33",
            parameter: serde_json::to_value(parameter)
                .map_err(|_| "voi parameter serialization failed")?,
            sensitivity: covered_sensitivity[row],
            standard_uncertainty: None,
            variance_share: covered_sensitivity[row] * row_contribution,
            share_fraction: None,
        });
    }
    for record in sensitivities {
        if record.value != 0.0
            && (!record.parameter.covariance_covered || record.parameter.covariance_excluded)
        {
            add_unranked("cross_section_mf33", record.value);
        }
    }

    if let Some(channel) = decay_channel {
        for record in &channel.sensitivities {
            let sensitivity = record.value;
            if record.parameter.covered {
                entries.push(VoiEntry {
                    channel: "decay_constants",
                    parameter: serde_json::to_value(&record.parameter)
                        .map_err(|_| "voi parameter serialization failed")?,
                    sensitivity,
                    standard_uncertainty: Some(record.parameter.standard_uncertainty_s),
                    variance_share: (sensitivity * record.parameter.standard_uncertainty_s).powi(2),
                    share_fraction: None,
                });
            } else if sensitivity != 0.0 {
                add_unranked("decay_constants", sensitivity);
            }
        }
    }
    if let Some(channel) = yield_channel {
        for record in &channel.sensitivities {
            let sensitivity = record.value;
            if record.parameter.covered {
                entries.push(VoiEntry {
                    channel: "fission_yields",
                    parameter: serde_json::to_value(&record.parameter)
                        .map_err(|_| "voi parameter serialization failed")?,
                    sensitivity,
                    standard_uncertainty: Some(record.parameter.standard_uncertainty),
                    variance_share: (sensitivity * record.parameter.standard_uncertainty).powi(2),
                    share_fraction: None,
                });
            } else if sensitivity != 0.0 {
                add_unranked("fission_yields", sensitivity);
            }
        }
    }

    let share_ranked = total_variance.is_finite() && total_variance > 0.0;
    entries.sort_by(|left, right| {
        if share_ranked {
            right
                .variance_share
                .abs()
                .total_cmp(&left.variance_share.abs())
        } else {
            right.sensitivity.abs().total_cmp(&left.sensitivity.abs())
        }
    });
    entries.truncate(top);
    for entry in &mut entries {
        entry.share_fraction = share_ranked.then_some(entry.variance_share / total_variance);
    }
    for bucket in unranked.values_mut() {
        bucket.sensitivity_l2 = bucket.sensitivity_l2.sqrt();
    }
    Ok(VoiReport {
        top: entries,
        total_propagated_variance: total_variance,
        unranked,
    })
}

/// True for `name_of`-style labels carrying an isomer ordinal (`W185m1`):
/// the last `m` is digit-preceded and digit-terminated, so element symbols
/// ending in `m` (Am, Cm, Pm, Sm, Tm) without an ordinal do not match.
fn is_isomer_name(name: &str) -> bool {
    match name.rfind('m') {
        Some(p) => {
            p > 0
                && name.as_bytes()[p - 1].is_ascii_digit()
                && p + 1 < name.len()
                && name[p + 1..].bytes().all(|b| b.is_ascii_digit())
        }
        None => false,
    }
}

fn isomer_channel_label(parameter: &SensitivityParameter) -> String {
    let target = name_of(parameter.target_za, parameter.target_liso);
    let product = if parameter.zap > 0 {
        name_of(parameter.zap, 0)
    } else {
        format!("ZAP{}", parameter.zap)
    };
    format!(
        "{target} MT={} -> {product}[lfs={}]",
        parameter.mt, parameter.lfs
    )
}

/// P58: partition the propagated variance the band already computed into
/// isomer-product, isomer-target, isomer-decay and ground classes, and rank
/// the isomer-class channels. Uses the same quadratic forms as `build_voi`.
fn build_isomer(
    top: usize,
    runtime: &UncertaintyRuntime,
    sensitivities: &[SensitivityOut],
    decay_channel: Option<&ChannelData<DecaySensitivityOut>>,
    yield_channel: Option<&ChannelData<YieldSensitivityOut>>,
    total_variance: f64,
) -> Result<IsomerReport, String> {
    let share_ranked = total_variance.is_finite() && total_variance > 0.0;
    let share = |variance_share: f64| share_ranked.then_some(variance_share / total_variance);
    let mut product = 0.0;
    let mut target = 0.0;
    let mut decay_isomer = 0.0;
    let mut ground = 0.0;
    let mut unranked_l2 = 0.0;
    let mut isomer_entries: Vec<IsomerChannelEntry> = Vec::new();

    let n_covered = runtime.covered_parameter_positions.len();
    let covered_sensitivity: Vec<f64> = runtime
        .covered_parameter_positions
        .iter()
        .map(|&position| sensitivities[position].value)
        .collect();
    for (row, &position) in runtime.covered_parameter_positions.iter().enumerate() {
        let parameter = &sensitivities[position].parameter;
        if parameter.covariance_excluded {
            continue;
        }
        let mut row_contribution = 0.0;
        for (column, &s_j) in covered_sensitivity.iter().enumerate() {
            row_contribution += runtime.covariance_barn2[row * n_covered + column] * s_j;
        }
        let variance_share = covered_sensitivity[row] * row_contribution;
        if parameter.lfs > 0 {
            product += variance_share;
        } else if parameter.target_liso > 0 {
            target += variance_share;
        } else {
            ground += variance_share;
        }
        if parameter.lfs > 0 || parameter.target_liso > 0 {
            isomer_entries.push(IsomerChannelEntry {
                channel_label: isomer_channel_label(parameter),
                channel: "cross_section_mf33",
                parameter: serde_json::to_value(parameter)
                    .map_err(|_| "isomer parameter serialization failed")?,
                sensitivity: covered_sensitivity[row],
                standard_uncertainty: None,
                variance_share,
                share_fraction: share(variance_share),
            });
        }
    }
    for record in sensitivities {
        if record.value != 0.0
            && (!record.parameter.covariance_covered || record.parameter.covariance_excluded)
        {
            unranked_l2 += record.value * record.value;
        }
    }

    if let Some(channel) = decay_channel {
        for record in &channel.sensitivities {
            if !record.parameter.covered {
                if record.value != 0.0 {
                    unranked_l2 += record.value * record.value;
                }
                continue;
            }
            let variance_share = (record.value * record.parameter.standard_uncertainty_s).powi(2);
            if record.parameter.liso > 0 {
                decay_isomer += variance_share;
                isomer_entries.push(IsomerChannelEntry {
                    channel_label: format!("{} lambda", record.parameter.nuclide),
                    channel: "decay_constants",
                    parameter: serde_json::to_value(&record.parameter)
                        .map_err(|_| "isomer parameter serialization failed")?,
                    sensitivity: record.value,
                    standard_uncertainty: Some(record.parameter.standard_uncertainty_s),
                    variance_share,
                    share_fraction: share(variance_share),
                });
            } else {
                ground += variance_share;
            }
        }
    }
    if let Some(channel) = yield_channel {
        for record in &channel.sensitivities {
            if !record.parameter.covered {
                if record.value != 0.0 {
                    unranked_l2 += record.value * record.value;
                }
                continue;
            }
            let variance_share = (record.value * record.parameter.standard_uncertainty).powi(2);
            if record.parameter.product_liso > 0 {
                product += variance_share;
                isomer_entries.push(IsomerChannelEntry {
                    channel_label: format!(
                        "{} -> {} yield",
                        record.parameter.parent_nuclide, record.parameter.product_nuclide
                    ),
                    channel: "fission_yields",
                    parameter: serde_json::to_value(&record.parameter)
                        .map_err(|_| "isomer parameter serialization failed")?,
                    sensitivity: record.value,
                    standard_uncertainty: Some(record.parameter.standard_uncertainty),
                    variance_share,
                    share_fraction: share(variance_share),
                });
            } else {
                ground += variance_share;
            }
        }
    }

    isomer_entries.sort_by(|left, right| {
        if share_ranked {
            right
                .variance_share
                .abs()
                .total_cmp(&left.variance_share.abs())
        } else {
            right.sensitivity.abs().total_cmp(&left.sensitivity.abs())
        }
    });
    isomer_entries.truncate(top);
    Ok(IsomerReport {
        variance_shares: IsomerVarianceShares {
            isomer_product_channels: share(product),
            isomer_target_channels: share(target),
            isomer_decay_constants: share(decay_isomer),
            ground_channels: share(ground),
            unranked_l2_sensitivity: unranked_l2.sqrt(),
        },
        top_isomer_channels: isomer_entries,
        total_propagated_variance: total_variance,
    })
}

/// Dense Cholesky solve `A x = b` for the small per-reaction covariance
/// blocks; `None` when the block is not positive-definite to working
/// precision — the caller names it `ill_conditioned` rather than emitting a
/// fake reduction.
fn cholesky_solve(matrix: &[f64], n: usize, rhs: &[f64]) -> Option<Vec<f64>> {
    let mut lower = vec![0.0; n * n];
    for i in 0..n {
        for j in 0..=i {
            let mut sum = matrix[i * n + j];
            for k in 0..j {
                sum -= lower[i * n + k] * lower[j * n + k];
            }
            if i == j {
                if !sum.is_finite() || sum <= 0.0 {
                    return None;
                }
                lower[i * n + i] = sum.sqrt();
            } else {
                lower[i * n + j] = sum / lower[j * n + j];
            }
        }
    }
    let mut x = rhs.to_vec();
    for i in 0..n {
        let mut sum = x[i];
        for j in 0..i {
            sum -= lower[i * n + j] * x[j];
        }
        x[i] = sum / lower[i * n + i];
    }
    for i in (0..n).rev() {
        let mut sum = x[i];
        for j in i + 1..n {
            sum -= lower[j * n + i] * x[j];
        }
        x[i] = sum / lower[i * n + i];
    }
    Some(x)
}

/// P60 measurement-design report: rank candidates by the variance a perfect
/// measurement *removes* — the Kalman-gain quantity `(Σ·s)_i²/Σ_ii` per
/// parameter and `(Σ·s)_Bᵀ Σ_BB⁻¹ (Σ·s)_B` per (target, MT) reaction block —
/// over the identical collapsed covariance `voi` and `isomer` already use.
fn build_design(
    top: usize,
    runtime: &UncertaintyRuntime,
    sensitivities: &[SensitivityOut],
    decay_channel: Option<&ChannelData<DecaySensitivityOut>>,
    yield_channel: Option<&ChannelData<YieldSensitivityOut>>,
    total_variance: f64,
) -> Result<DesignReport, String> {
    let share_ranked = total_variance.is_finite() && total_variance > 0.0;
    let total_finite = total_variance.is_finite();
    let share = |reduction: f64| share_ranked.then_some(reduction / total_variance);
    let posterior = |reduction: f64| total_finite.then_some(total_variance - reduction);
    // A reduction is a nonnegative quadratic form; a materially negative value
    // indicates a non-PSD block, not round-off — fail rather than emit it.
    let clamp_roundoff = |reduction: f64| -> Result<(f64, Option<f64>), String> {
        if reduction >= 0.0 {
            return Ok((reduction, None));
        }
        if -reduction <= 1.0e-9 * total_variance.abs().max(1.0e-300) {
            return Ok((0.0, Some(reduction)));
        }
        Err(format!(
            "materially negative measurement reduction {reduction:.17e}"
        ))
    };

    let n_covered = runtime.covered_parameter_positions.len();
    let covered_sensitivity: Vec<f64> = runtime
        .covered_parameter_positions
        .iter()
        .map(|&position| sensitivities[position].value)
        .collect();
    // (Σ·s)_i for every covered row — shared by per-parameter reductions and
    // the per-reaction block solves.
    let mut sigma_s = vec![0.0; n_covered];
    for (row, slot) in sigma_s.iter_mut().enumerate() {
        let mut value = 0.0;
        for (column, &s_j) in covered_sensitivity.iter().enumerate() {
            value += runtime.covariance_barn2[row * n_covered + column] * s_j;
        }
        *slot = value;
    }

    let mut parameter_entries: Vec<DesignParameterEntry> = Vec::new();
    let mut unranked: BTreeMap<&'static str, VoiUnranked> = BTreeMap::new();
    let mut add_unranked = |channel: &'static str, sensitivity: f64| {
        let bucket = unranked.entry(channel).or_insert(VoiUnranked {
            count: 0,
            sensitivity_l2: 0.0,
        });
        bucket.count += 1;
        bucket.sensitivity_l2 += sensitivity * sensitivity;
    };
    // Covariance is per-(mat, MT): a measurement on a nuclide's file
    // conditions every MT block it touches, so blocks group by target.
    let mut reactions: BTreeMap<(i32, i32), Vec<usize>> = BTreeMap::new();

    for (row, &position) in runtime.covered_parameter_positions.iter().enumerate() {
        let parameter = &sensitivities[position].parameter;
        if parameter.covariance_excluded {
            continue;
        }
        let sigma_ii = runtime.covariance_barn2[row * n_covered + row];
        let variance_share = covered_sensitivity[row] * sigma_s[row];
        if !sigma_ii.is_finite() || sigma_ii <= 0.0 {
            continue;
        }
        // A zero-diagonal row is already perfectly known — conditioning on it
        // removes nothing — so it stays out of the block solve entirely.
        reactions
            .entry((parameter.target_za, parameter.target_liso))
            .or_default()
            .push(row);
        let (reduction, removed) = clamp_roundoff(sigma_s[row] * sigma_s[row] / sigma_ii)?;
        parameter_entries.push(DesignParameterEntry {
            channel: "cross_section_mf33",
            parameter: serde_json::to_value(parameter)
                .map_err(|_| "design parameter serialization failed")?,
            sensitivity: covered_sensitivity[row],
            standard_uncertainty: None,
            variance_reduction: reduction,
            share_of_total: share(reduction),
            posterior_variance: posterior(reduction),
            reduction_at_half_uncertainty: None,
            variance_share: Some(variance_share),
            negative_reduction_roundoff_removed: removed,
        });
    }
    for record in sensitivities {
        if record.value != 0.0
            && (!record.parameter.covariance_covered || record.parameter.covariance_excluded)
        {
            add_unranked("cross_section_mf33", record.value);
        }
    }
    if let Some(channel) = decay_channel {
        for record in &channel.sensitivities {
            let sensitivity = record.value;
            if record.parameter.covered {
                let reduction = (sensitivity * record.parameter.standard_uncertainty_s).powi(2);
                parameter_entries.push(DesignParameterEntry {
                    channel: "decay_constants",
                    parameter: serde_json::to_value(&record.parameter)
                        .map_err(|_| "design parameter serialization failed")?,
                    sensitivity,
                    standard_uncertainty: Some(record.parameter.standard_uncertainty_s),
                    variance_reduction: reduction,
                    share_of_total: share(reduction),
                    posterior_variance: posterior(reduction),
                    reduction_at_half_uncertainty: Some(0.75 * reduction),
                    variance_share: Some(reduction),
                    negative_reduction_roundoff_removed: None,
                });
            } else if sensitivity != 0.0 {
                add_unranked("decay_constants", sensitivity);
            }
        }
    }
    if let Some(channel) = yield_channel {
        for record in &channel.sensitivities {
            let sensitivity = record.value;
            if record.parameter.covered {
                let reduction = (sensitivity * record.parameter.standard_uncertainty).powi(2);
                parameter_entries.push(DesignParameterEntry {
                    channel: "fission_yields",
                    parameter: serde_json::to_value(&record.parameter)
                        .map_err(|_| "design parameter serialization failed")?,
                    sensitivity,
                    standard_uncertainty: Some(record.parameter.standard_uncertainty),
                    variance_reduction: reduction,
                    share_of_total: share(reduction),
                    posterior_variance: posterior(reduction),
                    reduction_at_half_uncertainty: Some(0.75 * reduction),
                    variance_share: Some(reduction),
                    negative_reduction_roundoff_removed: None,
                });
            } else if sensitivity != 0.0 {
                add_unranked("fission_yields", sensitivity);
            }
        }
    }

    let mut reaction_entries: Vec<DesignReactionEntry> = Vec::new();
    for (&(target_za, target_liso), rows) in &reactions {
        let n = rows.len();
        let mts: Vec<i32> = {
            let mut set: BTreeSet<i32> = BTreeSet::new();
            for &row in rows {
                set.insert(
                    sensitivities[runtime.covered_parameter_positions[row]]
                        .parameter
                        .mt,
                );
            }
            set.into_iter().collect()
        };
        let mut block = vec![0.0; n * n];
        for (a, &row_a) in rows.iter().enumerate() {
            for (b, &row_b) in rows.iter().enumerate() {
                block[a * n + b] = runtime.covariance_barn2[row_a * n_covered + row_b];
            }
        }
        let rhs: Vec<f64> = rows.iter().map(|&row| sigma_s[row]).collect();
        match cholesky_solve(&block, n, &rhs) {
            Some(solution) => {
                let mut reduction = 0.0;
                for (index, &b_i) in rhs.iter().enumerate() {
                    reduction += b_i * solution[index];
                }
                let (reduction, _) = clamp_roundoff(reduction)?;
                reaction_entries.push(DesignReactionEntry {
                    target_za,
                    target_liso,
                    mts,
                    status: "emitted",
                    covered_parameters: n,
                    variance_reduction: Some(reduction),
                    share_of_total: share(reduction),
                    posterior_variance: posterior(reduction),
                });
            }
            None => reaction_entries.push(DesignReactionEntry {
                target_za,
                target_liso,
                mts,
                status: "ill_conditioned",
                covered_parameters: n,
                variance_reduction: None,
                share_of_total: None,
                posterior_variance: None,
            }),
        }
    }

    parameter_entries.sort_by(|left, right| {
        right
            .variance_reduction
            .abs()
            .total_cmp(&left.variance_reduction.abs())
    });
    parameter_entries.truncate(top);
    reaction_entries.sort_by(|left, right| {
        right
            .variance_reduction
            .unwrap_or(0.0)
            .total_cmp(&left.variance_reduction.unwrap_or(0.0))
    });
    reaction_entries.truncate(top);
    for bucket in unranked.values_mut() {
        bucket.sensitivity_l2 = bucket.sensitivity_l2.sqrt();
    }
    Ok(DesignReport {
        total_propagated_variance: total_variance,
        top_parameters: parameter_entries,
        top_reactions: reaction_entries,
        unranked,
    })
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
        // Compact tangents -> dense parameter positions: uncovered
        // parameters (no propagated row) emit zero sensitivity.
        let compact: Vec<f64> = runtime
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
        let mut values = vec![
            0.0;
            runtime.parameters.len()
                + runtime.decay_parameters.len()
                + runtime.yield_parameters.len()
                + runtime.flux_parameters.len()
        ];
        for (t, &position) in runtime.propagated_parameter_positions.iter().enumerate() {
            values[position] = compact[t];
        }
        let covered: Vec<f64> = runtime
            .covered_parameter_positions
            .iter()
            .map(|position| values[*position])
            .collect();
        let (variance, residue) = if runtime.contract_pairs {
            uncertainty_report::propagated_variance_pairs(&covered, &runtime.covariance_pairs)?
        } else {
            uncertainty_report::propagated_variance(&covered, &runtime.covariance_barn2)?
        };
        let response_unit = if response.starts_with("heat.") {
            "W g^-1"
        } else {
            "Bq g^-1"
        };
        let sensitivity_unit = format!("{response_unit} barn^-1");
        let xs_count = runtime.parameters.len();
        let decay_count = runtime.decay_parameters.len();
        let decay_channel = if decay_count > 0 || runtime.decay_channel_requested {
            let sensitivity_unit = format!("{response_unit} s");
            let activity_name = response.strip_prefix("activity:");
            let mut sensitivities = Vec::with_capacity(decay_count);
            let mut channel_variance = 0.0;
            let mut covered = 0usize;
            let mut total = 0usize;
            for (index, parameter) in runtime.decay_parameters.iter().enumerate() {
                let mut value = values[xs_count + index];
                // The response itself depends on lambda: activity X = lambda_X * n_X
                // carries the direct term n_X when X is the differentiated nuclide,
                // and each heat component carries n_X * E_X.
                let atoms = nominal
                    .activity
                    .get(parameter.nuclide.as_str())
                    .copied()
                    .unwrap_or(0.0)
                    / parameter.lambda_s;
                if activity_name == Some(parameter.nuclide.as_str()) {
                    value += atoms;
                } else if let Some(component) = response.strip_prefix("heat.") {
                    let energy = context
                        .nuclides
                        .get(&(parameter.za, parameter.liso))
                        .map(|nuclide| match component {
                            "alpha" => nuclide.e_heavy(),
                            "beta" => nuclide.e_light(),
                            "gamma" => nuclide.e_em(),
                            _ => nuclide.e_heavy() + nuclide.e_light() + nuclide.e_em(),
                        })
                        .unwrap_or(0.0);
                    value += atoms * energy * EV;
                }
                if value != 0.0 {
                    total += 1;
                    if parameter.covered {
                        covered += 1;
                        channel_variance += (value * parameter.standard_uncertainty_s).powi(2);
                    }
                }
                sensitivities.push(DecaySensitivityOut {
                    parameter: parameter.clone(),
                    value,
                    unit: sensitivity_unit.clone(),
                });
            }
            Some(ChannelData {
                variance: channel_variance,
                covered_parameters: covered,
                total_parameters: total,
                sensitivities,
            })
        } else {
            None
        };
        let yield_channel =
            if !runtime.yield_parameters.is_empty() || runtime.yield_channel_requested {
                let mut sensitivities = Vec::with_capacity(runtime.yield_parameters.len());
                let mut channel_variance = 0.0;
                let mut covered = 0usize;
                let mut total = 0usize;
                for (index, parameter) in runtime.yield_parameters.iter().enumerate() {
                    let value = values[xs_count + decay_count + index];
                    if value != 0.0 {
                        total += 1;
                        if parameter.covered {
                            covered += 1;
                            channel_variance += (value * parameter.standard_uncertainty).powi(2);
                        }
                    }
                    sensitivities.push(YieldSensitivityOut {
                        parameter: parameter.clone(),
                        value,
                        unit: response_unit.into(),
                    });
                }
                Some(ChannelData {
                    variance: channel_variance,
                    covered_parameters: covered,
                    total_parameters: total,
                    sensitivities,
                })
            } else {
                None
            };
        let yield_count = runtime.yield_parameters.len();
        let (flux_channel, flux_fully_correlated_bound) = if !runtime.flux_parameters.is_empty()
            || runtime.flux_channel_requested
        {
            let mut sensitivities = Vec::with_capacity(runtime.flux_parameters.len());
            let mut channel_variance = 0.0;
            let mut bound = 0.0;
            let mut covered = 0usize;
            let mut total = 0usize;
            for (index, parameter) in runtime.flux_parameters.iter().enumerate() {
                let value = values[xs_count + decay_count + yield_count + index];
                if value != 0.0 {
                    total += 1;
                    covered += 1;
                    channel_variance += (value * parameter.standard_uncertainty_relative).powi(2);
                    bound += (value * parameter.standard_uncertainty_relative).abs();
                }
                sensitivities.push(FluxSensitivityOut {
                    parameter: parameter.clone(),
                    value,
                    unit: response_unit.into(),
                });
            }
            (
                Some(ChannelData {
                    variance: channel_variance,
                    covered_parameters: covered,
                    total_parameters: total,
                    sensitivities,
                }),
                Some(bound),
            )
        } else {
            (None, None)
        };
        // Restricted runs emit per-parameter records only for propagated
        // parameters — uncovered positions have no tangent and would emit
        // meaningless zeros at O(responses × all-rows) serialization cost.
        let sensitivities: Vec<SensitivityOut> = if options.restrict_tangents_to_covered {
            runtime
                .propagated_parameter_positions
                .iter()
                .filter(|&&position| position < xs_count)
                .map(|&position| SensitivityOut {
                    parameter: runtime.parameters[position].clone(),
                    value: values[position],
                    unit: sensitivity_unit.clone(),
                })
                .collect()
        } else {
            values
                .iter()
                .copied()
                .take(xs_count)
                .zip(&runtime.parameters)
                .map(|(value, parameter)| SensitivityOut {
                    parameter: parameter.clone(),
                    value,
                    unit: sensitivity_unit.clone(),
                })
                .collect()
        };
        let voi = if let Some(voi_options) = &options.voi {
            // Same total the band uses: MF=33 propagated variance plus the
            // declared diagonal channels.
            //
            // P93: the flux channel is deliberately left out of this total
            // and out of `build_voi` itself — VOI ranks *nuclear-data*
            // parameters an experiment could better-measure to shrink the
            // band, and a transport tally's statistical error is neither a
            // nuclear-data parameter nor something a measurement campaign
            // can improve (it shrinks only by running more particle
            // histories). Excluding it keeps every entry's share_fraction a
            // valid ratio over the nuclear-data variance it was designed to
            // rank, instead of being silently diluted by an unranked term.
            let total_variance = variance
                + decay_channel
                    .as_ref()
                    .map_or(0.0, |channel| channel.variance)
                + yield_channel
                    .as_ref()
                    .map_or(0.0, |channel| channel.variance);
            Some(build_voi(
                voi_options.top,
                runtime,
                &sensitivities,
                decay_channel.as_ref(),
                yield_channel.as_ref(),
                total_variance,
            )?)
        } else {
            None
        };
        let isomer = if let Some(isomer_options) = &options.isomer {
            // P93: flux excluded from this total for the same reason as VOI
            // above — it is not a nuclear-data channel the isomer partition
            // is classifying.
            let total_variance = variance
                + decay_channel
                    .as_ref()
                    .map_or(0.0, |channel| channel.variance)
                + yield_channel
                    .as_ref()
                    .map_or(0.0, |channel| channel.variance);
            let top = isomer_options
                .top
                .or_else(|| options.voi.as_ref().map(|v| v.top))
                .unwrap_or(20);
            Some(build_isomer(
                top,
                runtime,
                &sensitivities,
                decay_channel.as_ref(),
                yield_channel.as_ref(),
                total_variance,
            )?)
        } else {
            None
        };
        let design = if let Some(design_options) = &options.design {
            // P93: flux excluded from this total for the same reason as VOI
            // above — it is not a nuclear-data channel the design report is
            // classifying.
            let total_variance = variance
                + decay_channel
                    .as_ref()
                    .map_or(0.0, |channel| channel.variance)
                + yield_channel
                    .as_ref()
                    .map_or(0.0, |channel| channel.variance);
            let top = design_options
                .top
                .or_else(|| options.voi.as_ref().map(|v| v.top))
                .unwrap_or(20);
            Some(build_design(
                top,
                runtime,
                &sensitivities,
                decay_channel.as_ref(),
                yield_channel.as_ref(),
                total_variance,
            )?)
        } else {
            None
        };
        let mut report = uncertainty_report::response_band(BandInput {
            nominal: snapshot_value(nominal, &response),
            alternate: snapshot_value(alternate, &response),
            unit: response_unit.into(),
            confidence_level: options.confidence_level,
            normal_multiplier: runtime.normal_multiplier,
            variance,
            negative_variance_roundoff_removed: residue,
            sensitivities,
            decay_channel,
            fission_yield_channel: yield_channel,
            flux_channel,
            flux_fully_correlated_bound,
            unmodeled_relative: options.unmodeled_relative,
        })?;
        report.voi = voi;
        report.isomer = isomer;
        report.design = design;
        if options.require_complete && report.coverage != "complete" {
            return Err(format!(
                "uncertainty response '{response}' has partial coverage in a requested channel"
            ));
        }
        responses.insert(response, report);
    }
    Ok(StepUncertainty {
        method: runtime.method,
        uncovered_library_rows: runtime.uncovered_library_rows.clone(),
        absent_cross_parameter_pairs: runtime.absent_cross_parameter_pairs,
        maximum_covariance_asymmetry_barn2: runtime.maximum_covariance_asymmetry_barn2,
        excluded_blocks: runtime.excluded_blocks.clone(),
        uncovered_decay_constants: runtime
            .decay_parameters
            .iter()
            .filter(|parameter| !parameter.covered)
            .map(|parameter| parameter.nuclide.clone())
            .collect(),
        uncovered_yield_products: runtime
            .yield_parameters
            .iter()
            .filter(|parameter| !parameter.covered)
            .map(|parameter| {
                format!(
                    "{} -> {}",
                    parameter.parent_nuclide, parameter.product_nuclide
                )
            })
            .collect(),
        responses,
    })
}

/// The spectrum-collapsed library normalizes its one-group rates by the flux
/// total, so it is defined only for a spectrum with a positive finite total. A
/// zero spectrum (an unreached mesh cell, a decay-only problem) is a legitimate
/// input that the groupwise data handle exactly, as pure decay.
fn collapsible_spectrum(phi: &[f64]) -> Option<&[f64]> {
    let total: f64 = phi.iter().sum();
    (total.is_finite() && total > 0.0).then_some(phi)
}

/// P93: resolves the flux channel's per-group relative statistical error,
/// from the mesh cell's flux-file origin when present, otherwise from the
/// spec's own declared spectrum. Pulled out of `run_started_profiled` so the
/// "channel requested but no errors given" failure (for both the plain-spec
/// and the mesh-cell case) is unit-testable without a full run.
fn resolve_flux_relative_error(
    spectrum: &Spectrum,
    flux_origin: Option<&MeshFluxOrigin<'_>>,
) -> Result<Vec<f64>, String> {
    match flux_origin {
        Some(origin) => origin
            .source_relative_error
            .map(|errors| errors.to_vec())
            .ok_or_else(|| {
                "uncertainty channel 'flux' requires the mesh cell's source relative_error"
                    .to_string()
            }),
        None => spectrum.ascending_relative_error().ok_or_else(|| {
            "uncertainty channel 'flux' requires spectrum.relative_error".to_string()
        }),
    }
}

/// P93: the flux channel's input-group basis for the base spectrum. A plain
/// spec's declared spectrum is always exactly library-group-aligned (an
/// existing invariant `run_started_profiled` checks), so its input groups
/// map to library groups by identity. A mesh cell's flux channel instead
/// uses the flux file's own source groups, mapped to library groups by the
/// same equal-lethargy rebin the nominal per-cell spectrum used.
enum FluxInputGroups<'a> {
    Identity {
        boundaries_ev: &'a [f64],
        flux_per_group: &'a [f64],
        relative_error: &'a [f64],
        /// `spectrum.descending`: `flux_per_group`/`relative_error` here are
        /// already reordered ascending, but `FluxParameter.group` reports
        /// the group index as the spec declared it (protocol G1 "descending
        /// input mapped to the right groups"), so the reversal is undone
        /// only for that reported label.
        descending: bool,
    },
    Rebinned {
        boundaries_ev: &'a [f64],
        flux_per_group: &'a [f64],
        relative_error: &'a [f64],
        /// Per source group: the (library group, equal-lethargy weight)
        /// pairs its flux is distributed across.
        weights: Vec<Vec<(usize, f64)>>,
    },
}

/// P93: builds the flux channel's per-input-group parameters and directions
/// for the base spectrum. `derivative_sub_base` is `derivative_subs[0]`
/// (library_row -> kept-state (row, col, per_barn_s) triplets), already
/// computed for the nominal MF=33 differentiation and reused here without
/// re-assembling the reaction network.
///
/// The direction for library group `d` is the reaction-only matrix that a
/// unit flux confined to group `d` alone would produce (linearity: rate is
/// `sum_g phi_g * sigma_g`, so `d(rate)/d(phi_d) = sigma_d`), built once per
/// (row, group) pair actually active rather than by re-assembling the whole
/// reaction network per group. An input group's direction is that same
/// linear combination of destination-group blocks the flux file's own rebin
/// would send it through (identity for a plain spec). Summing every group's
/// direction reproduces the nominal reaction matrix exactly (P93 G1).
/// Per library row: `(library_row, (row, col, unit_factor))` triplets, where
/// `unit_factor = per_barn_s / total_flux` (the group-independent scale of
/// that row's direction, before the per-group cross section is folded in).
type UnitRow = (usize, Vec<(usize, usize, f64)>);

#[allow(clippy::too_many_arguments, clippy::type_complexity)]
fn build_flux_parameters(
    derivative_sub_base: &BTreeMap<usize, Vec<(usize, usize, C64)>>,
    dense_library: &library::Library,
    lib_targets: &[(i32, i32)],
    shield_plan: Option<&crate::shielding::ShieldPlan>,
    m: usize,
    phi: &[f64],
    input: &FluxInputGroups<'_>,
) -> Result<(Vec<FluxParameter>, Vec<Csc>), String> {
    let group_count = dense_library.group_count();
    let total_flux: f64 = phi.iter().sum();
    if total_flux.is_nan() || total_flux <= 0.0 {
        return Ok((Vec::new(), Vec::new()));
    }
    // Per active library row, the group-independent unit factor: +1 for a
    // normal or fission-product row, -1 for a loss row, the independent
    // yield for a fission-product row to an absent chain state — exactly
    // `per_barn_s / (rate_per_barn_s at the base spectrum)`, recovered
    // without re-deriving the row classification.
    let unit_rows: Vec<UnitRow> = derivative_sub_base
        .iter()
        .map(|(&library_row, entries)| {
            let scaled = entries
                .iter()
                .map(|&(row, col, per_barn_s)| (row, col, per_barn_s.re / total_flux))
                .collect();
            (library_row, scaled)
        })
        .collect();
    // D[d]: the reaction-only (row, col, value) triplets a unit flux in
    // library group d alone produces, summed once over every active row.
    let mut d_blocks: Vec<Vec<(usize, usize, f64)>> = vec![Vec::new(); group_count];
    for (library_row, entries) in &unit_rows {
        let row_descriptor = dense_library.rows[*library_row];
        let target = lib_targets.get(row_descriptor.target).copied();
        // `group` indexes `d_blocks` but is also used to look up the row's
        // cross section and its shielding scale at that same group, so a
        // plain range loop (not an iterator over `d_blocks`) is clearest.
        #[allow(clippy::needless_range_loop)]
        for group in 0..group_count {
            let mut sigma = dense_library.row_cross_section(*library_row, group);
            if sigma == 0.0 {
                continue;
            }
            if let Some(plan) = shield_plan {
                if let Some((za, liso)) = target {
                    if let Some(scales) = plan.row_scales(za, liso, row_descriptor.mt) {
                        sigma *= scales.get(&group).copied().unwrap_or(1.0);
                    }
                }
            }
            if sigma == 0.0 {
                continue;
            }
            for &(row, col, unit_factor) in entries {
                let value = unit_factor * sigma;
                if value != 0.0 {
                    d_blocks[group].push((row, col, value));
                }
            }
        }
    }
    let (boundaries_ev, flux_per_group, relative_error, descending) = match input {
        FluxInputGroups::Identity {
            boundaries_ev,
            flux_per_group,
            relative_error,
            descending,
        } => (
            *boundaries_ev,
            *flux_per_group,
            *relative_error,
            *descending,
        ),
        FluxInputGroups::Rebinned {
            boundaries_ev,
            flux_per_group,
            relative_error,
            ..
        } => (*boundaries_ev, *flux_per_group, *relative_error, false),
    };
    let mut parameters = Vec::new();
    let mut directions = Vec::new();
    for g in 0..flux_per_group.len() {
        let flux_g = flux_per_group[g];
        if flux_g.is_nan() || flux_g <= 0.0 {
            continue;
        }
        let e_g = relative_error[g];
        if e_g.is_nan() || e_g <= 0.0 {
            continue;
        }
        let mut combined: Vec<(usize, usize, C64)> = Vec::new();
        match input {
            FluxInputGroups::Identity { .. } => {
                for &(row, col, value) in &d_blocks[g] {
                    combined.push((row, col, C64::new(value * flux_g, 0.0)));
                }
            }
            FluxInputGroups::Rebinned { weights, .. } => {
                for &(destination, weight) in &weights[g] {
                    for &(row, col, value) in &d_blocks[destination] {
                        combined.push((row, col, C64::new(value * weight * flux_g, 0.0)));
                    }
                }
            }
        }
        // `group` is reported as the spec declared it: `g` is the
        // already-reordered ascending index, undone here only for the label.
        let declared_group = if descending {
            flux_per_group.len() - 1 - g
        } else {
            g
        };
        parameters.push(FluxParameter {
            spectrum: 0,
            group: declared_group,
            lower_bound_eV: boundaries_ev[g],
            upper_bound_eV: boundaries_ev[g + 1],
            flux_per_cm2_s: flux_g,
            standard_uncertainty_relative: e_g,
        });
        directions.push(Csc::from_triplets(m, &combined));
    }
    Ok((parameters, directions))
}

impl PreparedRun {
    /// Immutable activation-library index loaded and validated during preparation.
    ///
    /// Callers that need target-specific builder limitations should use this exact
    /// index rather than reopening a potentially different file from disk.
    pub fn activation_index(&self) -> &serde_json::Value {
        &self.index
    }

    /// Effective decay records after primary/fallback merge and curated overrides.
    ///
    /// This map is the same data used to construct the prepared decay chain.
    pub fn decay_nuclides(&self) -> &HashMap<(i32, i32), decay::Nuclide> {
        &self.nuclides
    }

    pub fn prepare(spec: &Spec) -> Result<Self, String> {
        let started = std::time::Instant::now();
        let mut profiler = RunProfiler::from_environment();
        let physical = spec.physical_inputs()?;
        let prepared = Self::prepare_profiled(spec, &physical, &mut profiler);
        profiler.emit(started.elapsed());
        prepared
    }

    /// P72: resolve an `actinv-unmodeled-table-1` artifact into a declared
    /// `unmodeled_relative` value. Returns the resolved u plus the
    /// provenance record emitted under `certificate.inputs.unmodeled_table`.
    fn resolve_unmodeled_table(
        table: &crate::spec::UnmodeledTableRef,
        spec: &Spec,
    ) -> Result<(f64, serde_json::Value), String> {
        let (text, sha256) = read_verified_text(&table.path, Some(&table.sha256))?;
        let doc: serde_json::Value = serde_json::from_str(&text)
            .map_err(|e| format!("cannot parse unmodeled table {}: {e}", table.path))?;
        // the artifact may BE the table or carry it nested (d2 calibration)
        let root = if doc["schema"].as_str() == Some("actinv-unmodeled-table-1") {
            &doc
        } else {
            doc.get("unmodeled_table")
                .filter(|t| t["schema"].as_str() == Some("actinv-unmodeled-table-1"))
                .ok_or_else(|| format!("{}: no actinv-unmodeled-table-1 block found", table.path))?
        };
        let per_material = root["per_material"]
            .as_object()
            .ok_or("unmodeled table: per_material must be an object")?;
        let table_default = root["default"].as_f64();
        // key: declared, else dominant composition element (title-cased)
        let (key, key_source) = match &table.key {
            Some(k) => (k.clone(), "declared"),
            None => {
                let mut dominant = spec
                    .material
                    .composition
                    .iter()
                    .max_by(|a, b| a.1.total_cmp(b.1))
                    .map(|(k, _)| k.clone())
                    .ok_or(
                        "unmodeled_table: material.composition is empty — declare key explicitly",
                    )?;
                let alpha: String = dominant
                    .chars()
                    .take_while(|c| c.is_ascii_alphabetic())
                    .collect::<String>()
                    .to_lowercase();
                dominant = format!("{}{}", alpha[..1].to_uppercase(), &alpha[1..]);
                (dominant, "inferred")
            }
        };
        let (u, source) = match per_material.get(&key).and_then(|v| v.as_f64()) {
            Some(u) => (u, "per_material"),
            None => match table.fallback.or(table_default) {
                Some(u) => (
                    u,
                    if table.fallback.is_some() {
                        "spec_fallback"
                    } else {
                        "table_default"
                    },
                ),
                None => {
                    return Err(format!(
                    "unmodeled_table: no per_material entry for '{key}' and no default available"
                ))
                }
            },
        };
        Ok((
            u,
            serde_json::json!({
                "path": table.path,
                "sha256_declared": table.sha256,
                "sha256": sha256,
                "key": key,
                "key_source": key_source,
                "resolved_unmodeled_relative": u,
                "source": source,
            }),
        ))
    }

    /// P77: resolve an `actinv-eval-spread-1` artifact (the sha-bound output
    /// of `actinv eval-spread`) into a declared `unmodeled_relative`.
    fn resolve_unmodeled_evalspread(
        reference: &crate::spec::HashedFileRef,
    ) -> Result<(f64, serde_json::Value), String> {
        let (text, sha256) = read_verified_text(&reference.path, Some(&reference.sha256))?;
        let doc: serde_json::Value = serde_json::from_str(&text)
            .map_err(|e| format!("cannot parse eval-spread artifact {}: {e}", reference.path))?;
        if doc["schema"].as_str() != Some("actinv-eval-spread-1") {
            return Err(format!(
                "{}: schema must be 'actinv-eval-spread-1'",
                reference.path
            ));
        }
        let u = doc["suggested_unmodeled_relative"]
            .as_f64()
            .ok_or_else(|| {
                format!(
                    "{}: no numeric suggested_unmodeled_relative",
                    reference.path
                )
            })?;
        if !(u.is_finite() && u >= 0.0) {
            return Err(format!(
                "{}: suggested_unmodeled_relative must be finite and >= 0",
                reference.path
            ));
        }
        Ok((
            u,
            serde_json::json!({
                "path": reference.path,
                "sha256_declared": reference.sha256,
                "sha256": sha256,
                "resolved_unmodeled_relative": u,
                "source": "eval_spread",
            }),
        ))
    }

    fn prepare_profiled(
        spec: &Spec,
        physical: &PhysicalInputs,
        profiler: &mut RunProfiler,
    ) -> Result<Self, String> {
        Self::prepare_profiled_with(spec, physical, profiler, false)
    }

    /// `groupwise` keeps groupwise activation data even for a single spectrum (P85).
    fn prepare_profiled_with(
        spec: &Spec,
        physical: &PhysicalInputs,
        profiler: &mut RunProfiler,
        groupwise: bool,
    ) -> Result<Self, String> {
        // A spectrum-collapsed artifact is bound to one spectrum; per-step
        // spectra need the groupwise rows so each step collapses on its own.
        let multi_spectrum = physical
            .schedule
            .iter()
            .any(|step| step.spectrum_flux.is_some());
        // P72: a declared unmodeled table resolves to the unmodeled term
        // once, at prepare — the authored options stay on the prepared
        // run so the fingerprint still compares like-for-like; the band
        // emit injects the resolved value where `spec.uncertainty` is read.
        let mut unmodeled_table = None;
        let mut unmodeled_evalspread = None;
        if let Some(options) = &spec.uncertainty {
            if let Some(table) = &options.unmodeled_table {
                let (_u, provenance) = Self::resolve_unmodeled_table(table, spec)?;
                unmodeled_table = Some(provenance);
            }
            if let Some(spread) = &options.unmodeled_evalspread {
                let (_u, provenance) = Self::resolve_unmodeled_evalspread(spread)?;
                unmodeled_evalspread = Some(provenance);
            }
        }
        let mut prepared = Self::prepare_inputs_with_extensions_profiled(
            &spec.library,
            &spec.decay,
            &spec.photon,
            &spec.fission_yields,
            spec.projectile,
            physical.temperature,
            spec.uncertainty.as_ref(),
            spec.radiological.as_ref(),
            spec.damage.as_ref(),
            spec.self_shielding.as_ref(),
            if multi_spectrum || groupwise {
                None
            } else {
                collapsible_spectrum(physical.flux.values())
            },
            Some(&spec.spectrum.structure),
            spec.options.gas,
            profiler,
        )?;
        prepared.unmodeled_table = unmodeled_table;
        prepared.unmodeled_evalspread = unmodeled_evalspread;
        prepared.step_libraries =
            Self::prepare_step_libraries(&spec.step_libraries()?, physical.temperature)?;
        Ok(prepared)
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
            None,
            None,
            false,
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
        damage_options: Option<&DamageOptions>,
        shielding_options: Option<&SelfShieldingOptions>,
        gas: bool,
    ) -> Result<Self, String> {
        let mut profiler = RunProfiler::disabled();
        let temperature_K = Kelvin::new(temperature_K)
            .map_err(|_| "temperature_K must be finite and nonnegative".to_string())?;
        Self::prepare_inputs_with_extensions_profiled(
            library_ref,
            decay_ref,
            photon_options,
            fission_options,
            projectile,
            temperature_K,
            uncertainty_options,
            radiological_options,
            damage_options,
            shielding_options,
            None,
            None,
            gas,
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
        temperature_K: Kelvin,
        uncertainty_options: Option<&UncertaintyOptions>,
        radiological_options: Option<&RadiologicalOptions>,
        damage_options: Option<&DamageOptions>,
        shielding_options: Option<&SelfShieldingOptions>,
        collapse_flux: Option<&[f64]>,
        collapse_group_structure: Option<&str>,
        gas: bool,
        profiler: &mut RunProfiler,
    ) -> Result<Self, String> {
        let validation_started = profiler.start();
        if !projectile.is_neutron() && temperature_K.get() != 0.0 {
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
        // P124 decay-only run: an empty library path reads no activation data at all.
        let decay_only = library_ref.path.is_empty();
        if decay_only
            && (uncertainty_options.is_some()
                || shielding_options.is_some()
                || damage_options.is_some()
                || !fission_options.files.is_empty())
        {
            return Err(
                "a decay-only run (empty library.path) cannot carry uncertainty, self_shielding, \
                 damage or fission-yield files"
                    .into(),
            );
        }
        profiler.finish("prepare_validation", validation_started);

        let hashes_started = profiler.start();
        let (library_sha, idx_path, index_text, index_sha) = if decay_only {
            (
                String::new(),
                String::new(),
                "null".to_string(),
                String::new(),
            )
        } else {
            let library_sha = verify_hash(&library_ref.path, library_ref.sha256.as_deref())?;
            let idx_path = index_path(&library_ref.path);
            let (index_text, index_sha) = read_verified_text(&idx_path, None)?;
            (library_sha, idx_path, index_text, index_sha)
        };
        profiler.finish("input_hash_verification", hashes_started);

        let extensions_started = profiler.start();
        let (response, response_sha) = match &photon_options.response {
            Some(reference) => {
                let (text, sha) = read_verified_text(&reference.path, Some(&reference.sha256))
                    .map_err(|e| format!("photon response: {e}"))?;
                (Some(PhotonResponse::from_json(&text)?), Some(sha))
            }
            None => (None, None),
        };
        let radiological = match radiological_options {
            Some(options) => {
                let (text, sha256) = match crate::radiological::bundled_name(&options.table.path) {
                    Some(name) => {
                        crate::radiological::read_bundled(name, Some(&options.table.sha256))
                    }
                    None => read_verified_text(&options.table.path, Some(&options.table.sha256)),
                }
                .map_err(|error| format!("radiological table: {error}"))?;
                Some(PreparedRadiological {
                    options: options.clone(),
                    sha256,
                    table: PreparedRadiologicalTable::from_json(&text, &options.responses)?,
                })
            }
            None => None,
        };
        let damage = match damage_options {
            Some(options) => {
                let (text, sha256) =
                    read_verified_text(&options.table.path, Some(&options.table.sha256))
                        .map_err(|error| format!("damage table: {error}"))?;
                Some(PreparedDamage {
                    options: options.clone(),
                    sha256,
                    table: PreparedDamageTable::from_json(&text, projectile.name())?,
                })
            }
            None => None,
        };
        let shielding = match shielding_options {
            Some(options) => {
                if !projectile.is_neutron() {
                    return Err(format!(
                        "self_shielding is neutron-only; {} specs cannot declare it",
                        projectile.name()
                    ));
                }
                let (sha256, table) = match &options.table {
                    Some(reference) => {
                        let (text, sha256) =
                            read_verified_text(&reference.path, Some(&reference.sha256))
                                .map_err(|error| format!("self-shielding table: {error}"))?;
                        (
                            Some(sha256),
                            Some(crate::shielding::PreparedShieldTable::from_json(&text)?),
                        )
                    }
                    None => (None, None),
                };
                let mut resonance_sha256 = Vec::new();
                let geometry = match &options.geometry {
                    Some(geometry) => {
                        let mut files = Vec::with_capacity(geometry.resonance_files.len());
                        for reference in &geometry.resonance_files {
                            let (text, sha) =
                                read_verified_text(&reference.path, Some(&reference.sha256))
                                    .map_err(|error| {
                                        format!("self-shielding resonance file: {error}")
                                    })?;
                            resonance_sha256.push(sha);
                            files.push((reference.path.clone(), text));
                        }
                        Some(crate::shielding::PreparedGeometry::from_evaluations(
                            geometry, &files,
                        )?)
                    }
                    None => None,
                };
                Some(PreparedShielding {
                    options: options.clone(),
                    sha256,
                    table,
                    geometry,
                    resonance_sha256,
                })
            }
            None => None,
        };
        let mut fission_yields = HashMap::new();
        let mut fission_yield_inputs = Vec::with_capacity(fission_options.files.len());
        for reference in &fission_options.files {
            let (text, sha) = read_verified_text(&reference.path, Some(&reference.sha256))?;
            let parsed =
                fission::parse_text(&text).map_err(|e| format!("{}: {e}", reference.path))?;
            let parent = parsed.parent;
            if fission_yields.insert(parent, parsed).is_some() {
                return Err(format!(
                    "duplicate fission-yield parent {}_{}",
                    parent.0, parent.1
                ));
            }
            fission_yield_inputs.push((reference.clone(), sha, parent));
        }
        let spontaneous = fission_options
            .spontaneous
            .as_ref()
            .map(load_spontaneous)
            .transpose()?;
        profiler.finish("extension_input_preparation", extensions_started);

        let index_started = profiler.start();
        let index: serde_json::Value =
            serde_json::from_str(&index_text).map_err(|e| e.to_string())?;
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
        let library = if decay_only {
            ActivationLibrary::Absent
        } else if uncertainty_options.is_some() {
            // Covariance propagation still indexes the original dense rows directly. The complete NPZ matched its
            // computed (and, when supplied, declared) SHA-256 above, so member CRC work is redundant.
            ActivationLibrary::Dense(library::read_npz_after_sha256_verification(
                &library_ref.path,
            )?)
        } else if let Some(phi) = collapse_flux.filter(|_| shielding.is_none()) {
            // Self-shielding scales each library row's sigma per group inside
            // the unresolved ranges, which requires the groupwise data; the
            // spectrum-collapsed artifact keeps only one value per row.
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

        let actual_group_hash = GroupStructure {
            name: library_group_structure
                .clone()
                .unwrap_or_else(|| "custom".into()),
            boundaries_ev: library.boundaries_ev().to_vec(),
        }
        .hash();
        if !decay_only {
            let index_projectile = match index.get("projectile") {
                None | Some(serde_json::Value::Null) => Projectile::Neutron,
                Some(serde_json::Value::String(value)) => Projectile::parse(value)?,
                Some(_) => {
                    return Err("activation-library index projectile must be a string".into())
                }
            };
            if index_projectile != projectile {
                return Err(format!(
                    "spec projectile '{}' does not match activation-library projectile '{}'",
                    projectile.name(),
                    index_projectile.name()
                ));
            }
            match index.get("sha256_npz") {
                Some(serde_json::Value::String(recorded))
                    if !library_sha.eq_ignore_ascii_case(recorded) =>
                {
                    return Err(format!(
                    "activation-library index hash mismatch: index records {recorded}, computed {library_sha}"
                ));
                }
                Some(serde_json::Value::String(_)) => {}
                Some(_) => {
                    return Err("activation-library index sha256_npz must be a string".into())
                }
                None if !projectile.is_neutron() => {
                    return Err("charged activation-library index has no sha256_npz".into());
                }
                None => {}
            }
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
                let canonical = GroupStructure::resolve_named(name)?;
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
                    if (temperature - temperature_K.get()).abs() > 1e-9 {
                        return Err(format!(
                        "requested temperature {} K does not match library temperature {temperature} K",
                        temperature_K.get()
                    ));
                    }
                }
                None if !projectile.is_neutron() => {
                    return Err("charged activation-library index has no temperature_K".into());
                }
                None => {}
            }
        }
        let library_targets: Vec<(i32, i32)> = if decay_only {
            Vec::new()
        } else {
            index["targets"]
                .as_array()
                .ok_or("library index has no targets")?
                .iter()
                .map(|target| {
                    (
                        target["za"].as_i64().unwrap_or(0) as i32,
                        target["liso"].as_i64().unwrap_or(0) as i32,
                    )
                })
                .collect()
        };
        let covariance_started = profiler.start();
        let covariance = match uncertainty_options.and_then(|options| options.covariance.as_ref()) {
            Some(covariance_ref) => Some(Self::load_covariance(
                covariance_ref,
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
        let (decay_text, decay_primary_sha) = read_verified_text(&decay_ref.primary, None)?;
        let mut nuclides = decay::parse_text(&decay_text)
            .map_err(|error| format!("{}: {error}", decay_ref.primary))?;
        drop(decay_text);
        profiler.finish("decay_primary_read_parse", primary_decay_started);

        let fallback_decay_started = profiler.start();
        let mut decay_nuclides_from_fallback = 0usize;
        let mut decay_fallback_keys = std::collections::HashSet::new();
        let mut decay_fallback_sha = None;
        if let Some(fallback) = &decay_ref.fallback {
            if !fallback.is_empty() {
                let (fallback_text, sha) = read_verified_text(fallback, None)?;
                decay_fallback_sha = Some(sha);
                for (key, value) in decay::parse_text(&fallback_text)
                    .map_err(|error| format!("{fallback}: {error}"))?
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

        // Curated decay overrides (D6b): source-attributed per-nuclide
        // corrections applied after the primary+fallback merge, before
        // chain construction, so every downstream quantity — rates,
        // activity, heat, photons — inherits the patched constants.
        let mut decay_overrides_sha = None;
        let mut decay_overrides_applied = Vec::new();
        if let Some(overrides_path) = &decay_ref.overrides {
            if !overrides_path.is_empty() {
                let (text, sha) = read_verified_text(overrides_path, None)?;
                let items =
                    decay::parse_overrides(&text).map_err(|e| format!("{overrides_path}: {e}"))?;
                decay_overrides_applied = decay::apply_overrides(&mut nuclides, &items)
                    .map_err(|e| format!("{overrides_path}: {e}"))?;
                decay_overrides_sha = Some(sha);
            }
        }

        let chain_started = profiler.start();
        // P125: spontaneous-fission branches with supplied yields feed their products; without
        // `fission_yields.spontaneous` this is `chain::build` exactly.
        let mut chain = chain::build_with_spontaneous(
            &nuclides,
            spontaneous.as_ref().map(|prepared| &prepared.yields),
        );
        // P92 gas: ensure the five light ground states exist (a stable stand-in when the decay
        // library itself lacks one), then add the alpha/proton decay-gas edges. Both are no-ops
        // when gas is disabled, so chain::build's own output — and every gas-off byte — is
        // untouched (G3).
        let mut gas_missing_light_states: Vec<(i32, i32)> = Vec::new();
        if gas {
            let (augmented, missing) = chain::ensure_light_states(chain);
            chain = augmented;
            for za in &missing {
                nuclides.entry(*za).or_insert_with(|| decay::Nuclide {
                    mat: 0,
                    za: za.0,
                    awr: (za.0 % 1000) as f64,
                    liso: za.1,
                    nst: 1,
                    half_life: 0.0,
                    d_half_life: 0.0,
                    energies: Vec::new(),
                    modes: Vec::new(),
                    spectra: Vec::new(),
                });
            }
            gas_missing_light_states = missing;
            chain::add_gas_decay_edges(&nuclides, &mut chain);
        }
        if let Some(options) = uncertainty_options {
            let known: std::collections::HashSet<_> =
                nuclides.keys().map(|key| name_of(key.0, key.1)).collect();
            for selector in &options.responses {
                if *selector == "activity.total" {
                    continue;
                }
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
            decay_overrides_sha,
            decay_overrides_applied,
            nuclides,
            decay_fallback_keys,
            chain,
            gas_missing_light_states,
            decay_nuclides_from_fallback,
            photon_options: photon_options.clone(),
            response,
            response_sha,
            fission_options: fission_options.clone(),
            fission_yields,
            fission_yield_inputs,
            spontaneous,
            projectile,
            library_group_structure,
            temperature_K,
            uncertainty_options: uncertainty_options.cloned(),
            covariance,
            unmodeled_table: None,
            unmodeled_evalspread: None,
            radiological,
            damage,
            shielding,
            step_libraries: Vec::new(),
        })
    }

    /// Load and verify the libraries a schedule's `library` overrides name. Each is held to the same index
    /// contract as the base library (hashes, projectile, group structure, temperature), against the projectile
    /// the step declares. Neutron libraries must match the run's temperature; charged-particle libraries are 0 K.
    fn prepare_step_libraries(
        step_libraries: &[StepLibrary],
        temperature_K: Kelvin,
    ) -> Result<Vec<PreparedStepLibrary>, String> {
        step_libraries
            .iter()
            .map(|step_library| {
                Self::prepare_step_library(step_library, temperature_K).map_err(|error| {
                    format!("schedule library '{}': {error}", step_library.library.path)
                })
            })
            .collect()
    }

    fn prepare_step_library(
        step_library: &StepLibrary,
        temperature_K: Kelvin,
    ) -> Result<PreparedStepLibrary, String> {
        let reference = &step_library.library;
        let projectile = step_library.projectile;
        let library_sha = verify_hash(&reference.path, reference.sha256.as_deref())?;
        let idx_path = index_path(&reference.path);
        let (index_text, index_sha) = read_verified_text(&idx_path, None)?;
        let index: serde_json::Value =
            serde_json::from_str(&index_text).map_err(|e| e.to_string())?;
        let group_structure = index
            .get("groups")
            .and_then(serde_json::Value::as_str)
            .map(str::to_owned);
        let library = ActivationLibrary::Groupwise(
            prepared_data::load_or_prepare_groupwise_after_sha256_verification(
                &reference.path,
                &library_sha,
                &index_sha,
            )?,
        );
        let index_projectile = match index.get("projectile") {
            None | Some(serde_json::Value::Null) => Projectile::Neutron,
            Some(serde_json::Value::String(value)) => Projectile::parse(value)?,
            Some(_) => return Err("activation-library index projectile must be a string".into()),
        };
        if index_projectile != projectile {
            return Err(format!(
                "step projectile '{}' does not match activation-library projectile '{}'",
                projectile.name(),
                index_projectile.name()
            ));
        }
        match index.get("sha256_npz") {
            Some(serde_json::Value::String(recorded))
                if !library_sha.eq_ignore_ascii_case(recorded) =>
            {
                return Err(format!(
                    "activation-library index hash mismatch: index records {recorded}, computed {library_sha}"
                ));
            }
            Some(serde_json::Value::String(_)) => {}
            Some(_) => return Err("activation-library index sha256_npz must be a string".into()),
            None if !projectile.is_neutron() => {
                return Err("charged activation-library index has no sha256_npz".into());
            }
            None => {}
        }
        let actual_group_hash = GroupStructure {
            name: group_structure.clone().unwrap_or_else(|| "custom".into()),
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
        if !projectile.is_neutron() && group_structure.is_none() {
            return Err("charged activation-library index has no named group structure".into());
        }
        if let Some(name) = group_structure.as_deref() {
            if let Some(canonical) = GroupStructure::resolve_named(name)? {
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
        let expected_temperature = if projectile.is_neutron() {
            temperature_K.get()
        } else {
            0.0
        };
        match index.get("temperature_K") {
            Some(value) => {
                let temperature = value
                    .as_f64()
                    .ok_or("activation-library temperature_K must be numeric")?;
                if (temperature - expected_temperature).abs() > 1e-9 {
                    return Err(format!(
                        "requested temperature {expected_temperature} K does not match library temperature {temperature} K"
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
        Ok(PreparedStepLibrary {
            reference: reference.clone(),
            projectile,
            library_sha,
            index_path: idx_path,
            index_sha,
            library,
            library_targets,
            group_structure,
        })
    }

    fn load_covariance(
        covariance_ref: &HashedFileRef,
        activation_library_sha256: &str,
        activation_index_sha256: &str,
        group_boundary_sha256: &str,
        activation_index: &serde_json::Value,
        activation_targets: &[(i32, i32)],
    ) -> Result<PreparedCovariance, String> {
        let path = &covariance_ref.path;
        let sha256 = verify_hash(path, Some(&covariance_ref.sha256))?;
        let index_path = covariance::index_path(path)?.display().to_string();
        let (index_text, index_sha256) = read_verified_text(&index_path, None)
            .map_err(|error| format!("covariance index: {error}"))?;
        let index: serde_json::Value = serde_json::from_str(&index_text)
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

    fn ensure_compatible(&self, spec: &Spec, physical: &PhysicalInputs) -> Result<(), String> {
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
            || (physical.temperature.get() - self.temperature_K.get()).abs() > 1e-9
            || spec.uncertainty != self.uncertainty_options
            || spec.radiological.as_ref()
                != self.radiological.as_ref().map(|prepared| &prepared.options)
            || spec.damage.as_ref() != self.damage.as_ref().map(|prepared| &prepared.options)
            || spec.self_shielding.as_ref()
                != self.shielding.as_ref().map(|prepared| &prepared.options)
        {
            return Err("run spec nuclear-data inputs do not match the prepared data".into());
        }
        let step_libraries = spec.step_libraries()?;
        if step_libraries.len() != self.step_libraries.len()
            || step_libraries
                .iter()
                .zip(&self.step_libraries)
                .any(|(wanted, prepared)| {
                    wanted.library != prepared.reference || wanted.projectile != prepared.projectile
                })
        {
            return Err("run spec schedule libraries do not match the prepared data".into());
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

    /// Radioactive nuclides in the decay chain as (key, lambda, rel_sigma)
    /// tuples; `rel_sigma` is `d_half_life / half_life`, so a zero marks a
    /// radioactive nuclide with no declared uncertainty. This is the input
    /// set the P43 `decay_constants` sampling channel perturbs.
    pub(crate) fn decay_inputs(&self) -> Vec<((i32, i32), f64, f64)> {
        self.chain
            .keys
            .iter()
            .zip(self.chain.lambda.iter())
            // The channel's input set is radioactive *nuclides* — Z >= 1.
            // Pseudo-particle entries (the free neutron ZA=1, photons)
            // carry decay data but have no material-key name and are not
            // sampled or listed in coverage.
            .filter(|((za, _), lambda)| *za >= 1000 && **lambda > 0.0)
            .map(|(key, lambda)| {
                let rel = self
                    .nuclides
                    .get(key)
                    .filter(|nuclide| nuclide.half_life > 0.0)
                    .map_or(0.0, |nuclide| nuclide.d_half_life / nuclide.half_life);
                (*key, *lambda, rel)
            })
            .collect()
    }

    /// Effective independent fission yields for a spec as
    /// ((parent_za, parent_liso, product_za, product_liso), yield, rel_sigma)
    /// tuples, using the spec's fission-energy selection on the nominal
    /// (unperturbed) data. `rel_sigma` is `sigma_yield / yield`; a zero
    /// marks a pair with no declared uncertainty — or a zero yield, where
    /// a relative uncertainty is undefined. This is the input set the P43
    /// `fission_yields` sampling channel perturbs.
    pub(crate) fn yield_inputs(
        &self,
        spec: &Spec,
        physical: &PhysicalInputs,
    ) -> Result<YieldInputs, String> {
        let phi = physical.flux.values();
        let mut out = Vec::new();
        let mut parents: Vec<_> = self.fission_yields.keys().copied().collect();
        parents.sort_unstable();
        for parent in parents {
            let requested_energy = match spec.fission_yields.energy.as_str() {
                "fixed" => physical.fixed_fission_energy.map(|energy| energy.get()),
                "spectrum_average" => {
                    fission_average_energy_eV(&self.library, &self.library_targets, phi, parent)?
                }
                _ => None,
            };
            let Some(requested_energy) = requested_energy else {
                continue;
            };
            let effective = self.fission_yields[&parent].effective(requested_energy)?;
            for (&(dza, dliso), &y) in &effective.products {
                let sigma = effective
                    .uncertainties
                    .get(&(dza, dliso))
                    .copied()
                    .unwrap_or(0.0);
                let rel = if y > 0.0 { sigma / y } else { 0.0 };
                out.push(((parent.0, parent.1, dza, dliso), y, rel));
            }
        }
        Ok(out)
    }

    pub fn run(&self, spec: &Spec, entry_point: &str) -> Result<RunResult, String> {
        self.run_with_collapse(spec, entry_point, None)
    }

    /// [`Self::run`] taking the base-spectrum row values from a batched collapse. They are used
    /// only if `cell.phi` is bitwise the run's base spectrum; the result is the same either way.
    pub fn run_with_collapse(
        &self,
        spec: &Spec,
        entry_point: &str,
        cell: Option<CellCollapse<'_>>,
    ) -> Result<RunResult, String> {
        self.run_with_collapse_and_flux_origin(spec, entry_point, cell, None)
    }

    /// [`Self::run_with_collapse`] additionally taking the P93 mesh flux
    /// channel's pre-rebin source-group origin, when the flux channel is
    /// requested for a mesh cell.
    pub fn run_with_collapse_and_flux_origin(
        &self,
        spec: &Spec,
        entry_point: &str,
        cell: Option<CellCollapse<'_>>,
        flux_origin: Option<MeshFluxOrigin<'_>>,
    ) -> Result<RunResult, String> {
        let started = std::time::Instant::now();
        let mut profiler = RunProfiler::from_environment();
        let physical = spec.physical_inputs()?;
        let result = self.run_started_profiled(
            spec,
            &physical,
            entry_point,
            started,
            &mut profiler,
            cell,
            flux_origin,
        );
        profiler.emit(started.elapsed());
        result
    }

    /// Number of activation-library rows (the row range of [`Self::collapse_rows_batched`]).
    pub fn library_row_count(&self) -> usize {
        self.library.rows().len()
    }

    /// Batched one-group values for several spectra (see
    /// `PreparedLibrary::collapse_rows_batched`). Returns false, leaving `out` untouched, unless
    /// the library is groupwise.
    pub fn collapse_rows_batched(
        &self,
        phis: &[&[f64]],
        rows: std::ops::Range<usize>,
        out: &mut [f64],
    ) -> bool {
        let group_count = self.library.group_count();
        if phis.iter().any(|phi| phi.len() < group_count) {
            return false;
        }
        match &self.library {
            ActivationLibrary::Groupwise(library) => {
                library.collapse_rows_batched(phis, rows, out);
                true
            }
            _ => false,
        }
    }

    #[allow(clippy::too_many_arguments)]
    fn run_started_profiled(
        &self,
        spec: &Spec,
        physical: &PhysicalInputs,
        entry_point: &str,
        t0: std::time::Instant,
        profiler: &mut RunProfiler,
        cell_collapse: Option<CellCollapse<'_>>,
        flux_origin: Option<MeshFluxOrigin<'_>>,
    ) -> Result<RunResult, String> {
        let network_started = profiler.start();
        self.ensure_compatible(spec, physical)?;
        let library_sha = &self.library_sha;
        let idx_path = &self.index_path;
        let index_sha = &self.index_sha;
        let decay_primary_sha = &self.decay_primary_sha;
        let decay_fallback_sha = &self.decay_fallback_sha;
        let decay_overrides_sha = &self.decay_overrides_sha;
        let decay_overrides_applied = &self.decay_overrides_applied;
        let response = &self.response;
        let response_sha = &self.response_sha;
        let lib = &self.library;
        let idxj = &self.index;
        let lib_targets = &self.library_targets;
        let n_fallback = self.decay_nuclides_from_fallback;
        let ch = &self.chain;
        let decay_only = lib.is_absent();
        // ---- P43 option perturbations (nonlinear sampling). These are
        // ordinary spec options, so perturbed samples share one prepared
        // run: the maps are applied to a scaled view of the decay data
        // and to the effective yields, never to the prepared inputs.
        let mut decay_factors: BTreeMap<(i32, i32), f64> = spec
            .options
            .decay_scale
            .iter()
            .flatten()
            .map(|(name, factor)| {
                let key = match actinv_data::composition::material_key(name) {
                    Ok(actinv_data::composition::MaterialKey::Nuclide { za, liso, .. }) => {
                        (za, liso)
                    }
                    _ => {
                        return Err(format!(
                            "decay_scale key '{name}' must be an explicit nuclide"
                        ))
                    }
                };
                if !factor.is_finite() || *factor <= 0.0 {
                    return Err(format!("decay_scale['{name}'] must be finite and positive"));
                }
                Ok((key, *factor))
            })
            .collect::<Result<_, String>>()?;
        // ---- named half-life overrides (P124, OVER ALAM/ADLAM). A new half-life replaces the
        // datum exactly as a decay_scale factor of (datum / new) would, so it rides the same
        // scaled decay view; `half_life_uncertainty` replaces the decay channel's relative sigma.
        let mut override_ledger: Vec<serde_json::Value> = Vec::new();
        let mut decay_relative_overrides: HashMap<(i32, i32), f64> = HashMap::new();
        for item in &spec.overrides {
            let key = match item.kind.as_str() {
                "half_life" | "half_life_uncertainty" => {
                    crate::overrides::nuclide_key(&item.nuclide)?
                }
                _ => continue,
            };
            let datum = self.nuclides.get(&key).ok_or_else(|| {
                format!(
                    "override {} '{}' names a nuclide absent from the decay data",
                    item.kind, item.nuclide
                )
            })?;
            if datum.lambda() <= 0.0 {
                return Err(format!(
                    "override {} '{}' names a stable nuclide",
                    item.kind, item.nuclide
                ));
            }
            if item.kind == "half_life" {
                let seconds = item.half_life_seconds()?;
                *decay_factors.entry(key).or_insert(1.0) *= datum.half_life / seconds;
                override_ledger.push(serde_json::json!({
                    "nuclide": item.nuclide,
                    "kind": item.kind,
                    "value": item.value,
                    "units": item.units,
                    "half_life_s": seconds,
                    "library_half_life_s": datum.half_life,
                }));
            } else {
                decay_relative_overrides.insert(key, item.value);
                override_ledger.push(serde_json::json!({
                    "nuclide": item.nuclide,
                    "kind": item.kind,
                    "relative_standard_uncertainty": item.value,
                }));
            }
        }
        let yield_factors: BTreeMap<(i32, i32, i32, i32), f64> = spec
            .options
            .yield_scale
            .iter()
            .flatten()
            .map(|(name, factor)| {
                let (parent_raw, product_raw) = name.split_once(':').ok_or_else(|| {
                    format!("yield_scale key '{name}' must be '<parent>:<product>' nuclide names")
                })?;
                let parse = |raw: &str| match actinv_data::composition::material_key(raw) {
                    Ok(actinv_data::composition::MaterialKey::Nuclide { za, liso, .. }) => {
                        Ok((za, liso))
                    }
                    _ => Err(format!(
                        "yield_scale key '{raw}' must be an explicit nuclide"
                    )),
                };
                if !factor.is_finite() || *factor <= 0.0 {
                    return Err(format!("yield_scale['{name}'] must be finite and positive"));
                }
                let (pza, pliso) = parse(parent_raw)?;
                let (dza, dliso) = parse(product_raw)?;
                Ok(((pza, pliso, dza, dliso), *factor))
            })
            .collect::<Result<_, String>>()?;
        // Without decay_scale the prepared table is used as is; cloning it cost ~10 ms per mesh
        // cell (P81).
        let mut nuclides_scaled = (!decay_factors.is_empty()).then(|| self.nuclides.clone());
        for (key, factor) in &decay_factors {
            match ch.index.get(key) {
                None => {
                    return Err(format!(
                        "decay_scale nuclide '{}' is not in the decay chain",
                        name_of(key.0, key.1)
                    ))
                }
                Some(&i) if ch.lambda[i] <= 0.0 => {
                    return Err(format!(
                        "decay_scale nuclide '{}' is stable",
                        name_of(key.0, key.1)
                    ))
                }
                _ => {}
            }
            if let Some(nu) = nuclides_scaled
                .as_mut()
                .and_then(|table| table.get_mut(key))
            {
                nu.half_life /= *factor;
            }
        }
        let nuclides = nuclides_scaled.as_ref().unwrap_or(&self.nuclides);
        let decay_edges: Vec<(usize, usize, f64)> = if decay_factors.is_empty() {
            ch.decay.clone()
        } else {
            let col_factors: HashMap<usize, f64> = decay_factors
                .iter()
                .filter_map(|(key, factor)| ch.index.get(key).map(|&i| (i, *factor)))
                .collect();
            ch.decay
                .iter()
                .map(|&(r, c, v)| (r, c, v * col_factors.get(&c).copied().unwrap_or(1.0)))
                .collect()
        };
        // Effective per-state decay constants. Derivative directions are
        // stored as edge_value / lambda_parent: with scaled edges
        // (v_eff = f * v_nom) the scaled lambda keeps the entries exact
        // branching weights (v_eff / lambda_eff = v_nom / lambda_nom).
        let lambda_eff: Vec<f64> = if decay_factors.is_empty() {
            ch.lambda.clone()
        } else {
            let mut l = ch.lambda.clone();
            for (key, factor) in &decay_factors {
                if let Some(&i) = ch.index.get(key) {
                    l[i] *= *factor;
                }
            }
            l
        };
        // A decay-only run (P124) has no library groups to agree with.
        if !decay_only {
            // the solver's flux: the supplied groups, or `spectrum.rebin`'s conversion onto the
            // library's structure
            let spectrum_groups = physical.flux.values().len();
            if lib.group_count() != spectrum_groups {
                return Err(format!(
                    "spectrum has {} groups but activation library has {}",
                    spectrum_groups,
                    lib.group_count()
                ));
            }
            let declared_boundaries = match &spec.spectrum.rebin {
                // the conversion's destination is the named structure
                Some(_) => GroupStructure::resolve_named(&spec.spectrum.structure)?
                    .map(|destination| destination.boundaries_ev),
                None => spec.spectrum.boundaries_eV.clone(),
            };
            if let Some(boundaries) = &declared_boundaries {
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
        let phi = physical.flux.values();
        lib.validate_flux(phi)?;
        // ---- self-shielding (P19): the plan is fixed once per run — effective
        // dilution comes from the declared material composition, not evolved
        // inventories, and the spec temperature selects the table row.
        let shield_plan = match &self.shielding {
            Some(prepared) => {
                if let Some(table) = &prepared.table {
                    if table.boundaries_eV() != lib.boundaries_ev() {
                        return Err(
                            "self-shielding table boundaries_eV do not match the activation library boundaries"
                                .into(),
                        );
                    }
                }
                Some(crate::shielding::ShieldPlan::build(
                    prepared.table.as_ref(),
                    prepared.geometry.as_ref(),
                    &prepared.options,
                    &bulk_inv,
                    lib.boundaries_ev(),
                    self.temperature_K.get(),
                    spec.options.require_shielding_complete,
                )?)
            }
            None => None,
        };
        let mut effective_fission_yields = HashMap::new();
        let mut fission_yield_selection = Vec::new();
        let mut fission_parents: Vec<_> = self.fission_yields.keys().copied().collect();
        fission_parents.sort_unstable();
        for parent in fission_parents {
            let requested_energy = match spec.fission_yields.energy.as_str() {
                "fixed" => physical.fixed_fission_energy.map(|energy| energy.get()),
                "spectrum_average" => fission_average_energy_eV(lib, lib_targets, phi, parent)?,
                _ => None, // validated before preparation
            };
            let Some(requested_energy) = requested_energy else {
                continue;
            };
            let mut effective = self.fission_yields[&parent].effective(requested_energy)?;
            for (&(pza, pliso, dza, dliso), factor) in yield_factors
                .iter()
                .filter(|(k, _)| k.0 == parent.0 && k.1 == parent.1)
            {
                match effective.products.get_mut(&(dza, dliso)) {
                    Some(value) => *value *= factor,
                    None => {
                        return Err(format!(
                            "yield_scale pair '{}:{}' is absent from the effective yields",
                            name_of(pza, pliso),
                            name_of(dza, dliso)
                        ))
                    }
                }
                // The declared uncertainty scales with the yield so the
                // relative uncertainty is preserved.
                if let Some(sigma) = effective.uncertainties.get_mut(&(dza, dliso)) {
                    *sigma *= factor;
                }
            }
            if yield_factors
                .keys()
                .any(|k| k.0 == parent.0 && k.1 == parent.1)
            {
                effective.sum = effective.products.values().sum();
            }
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
        for &(pza, pliso, ..) in yield_factors.keys() {
            if !effective_fission_yields.contains_key(&(pza, pliso)) {
                return Err(format!(
                    "yield_scale parent '{}' has no effective fission yields",
                    name_of(pza, pliso)
                ));
            }
        }
        let mut led = RateLedger::default();
        // FISPACT-II DEPLETION: the initial nuclides the table follows, and the loss rows the
        // rate assembly records for them (the solver's own collapsed rates; nothing is
        // recomputed). Present only when `summary.depletion` is true.
        let want_depletion = spec
            .summary
            .as_ref()
            .is_some_and(|options| options.depletion);
        let depletion_columns: Vec<(usize, (i32, i32), f64)> = if want_depletion {
            bulk_inv
                .iter()
                .filter(|(_, atoms)| **atoms > 0.0)
                .filter_map(|(&key, &atoms)| ch.index.get(&key).map(|&column| (column, key, atoms)))
                .collect()
        } else {
            Vec::new()
        };
        if want_depletion {
            led.record_loss_columns = Some(depletion_columns.iter().map(|c| c.0).collect());
        }
        let rate_scales: Option<std::collections::HashMap<usize, f64>> = spec
            .options
            .rate_scale
            .as_ref()
            .map(|m| {
                m.iter()
                    .map(|(k, v)| {
                        let row = k
                            .parse::<usize>()
                            .map_err(|_| format!("rate_scale key '{k}' is not a row index"))?;
                        if !v.is_finite() || *v <= 0.0 {
                            return Err(format!("rate_scale['{k}'] must be finite and positive"));
                        }
                        if row >= lib.rows().len() {
                            return Err(format!("rate_scale row {row} out of range"));
                        }
                        Ok((row, *v))
                    })
                    .collect()
            })
            .transpose()?;
        // ---- named cross-section overrides (P124, OVER ACROSS): a view over the library that
        // substitutes the named rows' collapsed values, so rates, optical depth and mode
        // selection all see the same number.
        let cross_overrides =
            crate::overrides::resolve_cross_sections(&spec.overrides, lib.rows(), lib_targets)?;
        if !cross_overrides.is_empty() {
            if spec.uncertainty.is_some() {
                return Err("cross-section overrides cannot be combined with uncertainty".into());
            }
            if shield_plan.is_some() && cross_overrides.has_absolute() {
                return Err(
                    "override cross_section cannot be combined with self_shielding; \
                     use cross_section_scale"
                        .into(),
                );
            }
        }
        let override_view = (!cross_overrides.is_empty())
            .then(|| crate::overrides::OverriddenLibrary::new(lib, &cross_overrides));
        if let Some(view) = &override_view {
            view.check_spectrum(phi)?;
            for entry in &cross_overrides.entries {
                let (before, after) = view.before_after(entry, phi);
                override_ledger.push(serde_json::json!({
                    "nuclide": entry.nuclide,
                    "kind": entry.kind,
                    "mt": entry.mt,
                    "value": entry.value,
                    "library_rows": entry.rows,
                    "base_spectrum_collapsed_barns_before": before,
                    "base_spectrum_collapsed_barns_after": after,
                }));
            }
        }
        let reaction_assembly = if spec.uncertainty.is_some() {
            chain::reaction_rates_with_derivatives(
                lib,
                lib_targets,
                phi,
                ch,
                &effective_fission_yields,
                &mut led,
                shield_plan.as_ref(),
                rate_scales.as_ref(),
                spec.options.gas,
            )
        } else {
            let batched = cell_collapse
                .filter(|cell| {
                    cell.phi.len() == phi.len()
                        && cell
                            .phi
                            .iter()
                            .zip(phi)
                            .all(|(a, b)| a.to_bits() == b.to_bits())
                })
                .map(|cell| BatchedView {
                    base: lib,
                    phi,
                    cell,
                });
            let triplets = match (&override_view, &batched) {
                (Some(view), _) => chain::reaction_rates(
                    view,
                    lib_targets,
                    phi,
                    ch,
                    &effective_fission_yields,
                    &mut led,
                    shield_plan.as_ref(),
                    rate_scales.as_ref(),
                    spec.options.gas,
                ),
                (None, Some(view)) => chain::reaction_rates(
                    view,
                    lib_targets,
                    phi,
                    ch,
                    &effective_fission_yields,
                    &mut led,
                    shield_plan.as_ref(),
                    rate_scales.as_ref(),
                    spec.options.gas,
                ),
                (None, None) => chain::reaction_rates(
                    lib,
                    lib_targets,
                    phi,
                    ch,
                    &effective_fission_yields,
                    &mut led,
                    shield_plan.as_ref(),
                    rate_scales.as_ref(),
                    spec.options.gas,
                ),
            };
            chain::ReactionAssembly {
                yield_derivatives: Vec::new(),
                triplets,
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
        let sched = &physical.schedule;
        // ---- per-step spectra (P42): each step may override the base
        // spectrum; distinct override spectra collapse once each on the
        // groupwise rows (multi-spectrum specs never take the pre-collapsed
        // library path) and map back to their declaring steps.
        //
        // Schedule library overrides (`Step.library`) ride the same mechanism: a step on another library always
        // owns an extra spectrum entry, tagged with that library's slot, because its rates are collapsed from
        // that library's rows. Every library shares the decay chain, so the rate triplets are over the same
        // state indices and the state vector crosses a switch unchanged.
        let has_step_library = !self.step_libraries.is_empty();
        if let Some(feature) = spec.step_library_conflict() {
            return Err(format!(
                "schedule step library overrides cannot be combined with {feature}"
            ));
        }
        let (_, step_contexts) = spec.resolve_step_libraries()?;
        let mut step_spectrum_id: Vec<Option<usize>> = Vec::with_capacity(sched.len());
        let mut extra_spectra: Vec<&[f64]> = Vec::new();
        // The library slot each extra spectrum collapses against (None = base library); parallel to extra_spectra.
        let mut extra_libraries: Vec<Option<usize>> = Vec::new();
        for (si, step) in sched.iter().enumerate() {
            if let Some(sf) = &step.spectrum_flux {
                let vals: &[f64] = sf.values();
                if let Some(slot) = step.library {
                    // ensure_compatible matched the prepared libraries to this spec's, so the slot exists.
                    let step_library = &self.step_libraries[slot];
                    let declared = step_contexts[si].spectrum.ok_or_else(|| {
                        format!(
                            "schedule step {} on a library override has no spectrum",
                            si + 1
                        )
                    })?;
                    if step_library.library.group_count() != vals.len() {
                        return Err(format!(
                            "schedule step {} spectrum has {} groups but activation library '{}' has {}",
                            si + 1,
                            vals.len(),
                            step_library.reference.path,
                            step_library.library.group_count()
                        ));
                    }
                    if group_structure_mismatch(
                        Some(&declared.structure),
                        step_library.group_structure.as_deref(),
                    ) {
                        return Err(format!(
                            "schedule step {} spectrum group structure '{}' does not match activation-library group structure '{}'",
                            si + 1,
                            declared.structure,
                            step_library.group_structure.as_deref().unwrap_or_default()
                        ));
                    }
                    if let Some(boundaries) = &declared.boundaries_eV {
                        let stored = step_library.library.boundaries_ev();
                        if boundaries.len() != stored.len()
                            || boundaries.iter().zip(stored).any(|(declared, stored)| {
                                (declared - stored).abs()
                                    > 1e-12 * declared.abs().max(stored.abs()).max(1.0)
                            })
                        {
                            return Err(format!(
                                "schedule step {} spectrum boundaries do not match activation library '{}'",
                                si + 1,
                                step_library.reference.path
                            ));
                        }
                    }
                    step_library.library.validate_flux(vals)?;
                }
                let id = if step.library.is_none() && vals == phi {
                    None
                } else {
                    let pos = (0..extra_spectra.len())
                        .find(|&i| extra_libraries[i] == step.library && extra_spectra[i] == vals);
                    let pos = match pos {
                        Some(p) => p,
                        None => {
                            if step.library.is_none() {
                                lib.validate_flux(vals)?;
                            }
                            extra_spectra.push(vals);
                            extra_libraries.push(step.library);
                            extra_spectra.len() - 1
                        }
                    };
                    Some(pos)
                };
                step_spectrum_id.push(id);
            } else if step.library.is_some() {
                return Err(format!(
                    "schedule step {} on a library override has no spectrum",
                    si + 1
                ));
            } else {
                step_spectrum_id.push(None);
            }
        }
        // ---- kerma and power (P126): the heating table, the heating of one atom under each
        // spectrum, and for power-driven steps the start-of-run flux amplitude that pruning and
        // the burnup-mode decision need before the schedule is solved. The step loop
        // renormalizes the flux from the evolving composition; the estimate only sizes the
        // reaction graph.
        let kerma_loaded = match &spec.kerma {
            Some(options) => Some(LoadedKerma::load(
                options,
                spec.projectile.name(),
                lib.boundaries_ev(),
            )?),
            None => None,
        };
        let kerma_uncovered: Vec<String> = match &kerma_loaded {
            Some(loaded) => bulk_inv
                .keys()
                .filter(|(za, liso)| !loaded.table.covers(*za, *liso))
                .map(|(za, liso)| name_of(*za, *liso))
                .collect(),
            None => Vec::new(),
        };
        if let Some(loaded) = &kerma_loaded {
            if loaded.options.require_complete && !kerma_uncovered.is_empty() {
                return Err(format!(
                    "kerma: material nuclides lack table coverage: {}",
                    kerma_uncovered.join(", ")
                ));
            }
        }
        let kerma_terms: Vec<Vec<KermaTerm>> = match &kerma_loaded {
            Some(loaded) => std::iter::once(phi)
                .chain(extra_spectra.iter().copied())
                .map(|spectrum| kerma_terms_for(&loaded.table, &ch.keys, spectrum))
                .collect(),
            None => Vec::new(),
        };
        let has_power = sched.iter().any(|step| step.power_w_per_g.is_some());
        if has_power {
            if kerma_loaded.is_none() {
                return Err("power-driven schedule steps require a kerma section".into());
            }
            if spec.uncertainty.is_some() {
                return Err(
                    "power-driven schedule steps cannot be combined with uncertainty".into(),
                );
            }
            // The fission count and the routes analysis each run one matrix per step; a power step
            // is solved as several sub-intervals at different multipliers.
            if spec.options.fission.is_some() {
                return Err(
                    "power-driven schedule steps cannot be combined with options.fission".into(),
                );
            }
            if spec.pathways.is_some() {
                return Err("power-driven schedule steps cannot be combined with pathways".into());
            }
        }
        if spec.pathways.is_some() && sched.iter().any(|step| !step.partition.is_empty()) {
            return Err("schedule partition cannot be combined with pathways".into());
        }
        let mut estimated_multiplier: Vec<Option<f64>> = vec![None; sched.len()];
        let sched_estimated: Vec<PhysicalStep>;
        let sched: &[PhysicalStep] = if has_power {
            let mut resolved = Vec::with_capacity(sched.len());
            for (si, step) in sched.iter().enumerate() {
                match step.power_w_per_g {
                    Some(target) => {
                        let terms = &kerma_terms[step_spectrum_id[si].map_or(0, |id| id + 1)];
                        let heating_per_unit: f64 = terms
                            .iter()
                            .map(|term| {
                                term.watts_per_atom * bulk.get(&term.state).copied().unwrap_or(0.0)
                            })
                            .sum();
                        if !heating_per_unit.is_finite() || heating_per_unit <= 0.0 {
                            return Err(format!(
                                "schedule step {}: the starting material has no kerma heating under \
                                 this step's spectrum, so a power target cannot set a flux",
                                si + 1
                            ));
                        }
                        let estimate = target / heating_per_unit;
                        estimated_multiplier[si] = Some(estimate);
                        resolved.push(
                            step.with_multiplier(
                                FluxMultiplier::new(estimate).map_err(|error| {
                                    format!("schedule step {}: {error}", si + 1)
                                })?,
                            ),
                        );
                    }
                    None => resolved.push(step.clone()),
                }
            }
            sched_estimated = resolved;
            &sched_estimated
        } else {
            sched
        };
        let mut react_extra: Vec<Vec<(usize, usize, f64)>> = Vec::new();
        // Extra spectra carry their own derivative sets under uncertainty: a
        // row's tangent direction is the collapse of its derivative edges under
        // that step's spectrum.
        let mut react_extra_assembly: Vec<chain::ReactionAssembly> = Vec::new();
        // The first collapse's ledger of each overriding library, emitted under `ledger.step_libraries`.
        let mut step_library_ledgers: Vec<Option<RateLedger>> =
            self.step_libraries.iter().map(|_| None).collect();
        // Per-atom fission rates under each override spectrum, for the opt-in fission count.
        let mut fission_rates_extra: Vec<BTreeMap<usize, f64>> = Vec::new();
        // Loss rates of the depletion table's nuclides under each override spectrum.
        let mut loss_rates_extra: Vec<Vec<chain::LossRate>> = Vec::new();
        for (extra_phi, extra_slot) in extra_spectra.iter().zip(&extra_libraries) {
            let (extra_lib, extra_targets): (&ActivationLibrary, &[(i32, i32)]) = match extra_slot {
                Some(slot) => (
                    &self.step_libraries[*slot].library,
                    self.step_libraries[*slot].library_targets.as_slice(),
                ),
                None => (lib, lib_targets.as_slice()),
            };
            // a throwaway ledger per extra collapse: the base ledger already
            // recorded the channel universe; these calls only differ in phi
            let mut scratch = RateLedger {
                record_loss_columns: led.record_loss_columns.clone(),
                ..RateLedger::default()
            };
            if spec.uncertainty.is_some() {
                let assembly = chain::reaction_rates_with_derivatives(
                    extra_lib,
                    extra_targets,
                    extra_phi,
                    ch,
                    &effective_fission_yields,
                    &mut scratch,
                    shield_plan.as_ref(),
                    rate_scales.as_ref(),
                    spec.options.gas,
                );
                react_extra.push(assembly.triplets.clone());
                react_extra_assembly.push(assembly);
            } else if let Some(view) = &override_view {
                view.check_spectrum(extra_phi)?;
                react_extra.push(chain::reaction_rates(
                    view,
                    lib_targets,
                    extra_phi,
                    ch,
                    &effective_fission_yields,
                    &mut scratch,
                    shield_plan.as_ref(),
                    rate_scales.as_ref(),
                    spec.options.gas,
                ));
            } else {
                react_extra.push(chain::reaction_rates(
                    extra_lib,
                    extra_targets,
                    extra_phi,
                    ch,
                    &effective_fission_yields,
                    &mut scratch,
                    shield_plan.as_ref(),
                    rate_scales.as_ref(),
                    spec.options.gas,
                ));
            }
            fission_rates_extra.push(std::mem::take(&mut scratch.fission_rate_per_atom));
            loss_rates_extra.push(std::mem::take(&mut scratch.loss_rates));
            if let Some(slot) = extra_slot {
                if step_library_ledgers[*slot].is_none() {
                    step_library_ledgers[*slot] = Some(scratch);
                }
            }
        }
        let flux_weighted_time_s: f64 = sched
            .iter()
            .map(|step| (step.duration() * step.multiplier()).get())
            .sum();
        let mut burnup_optical_depth: HashMap<usize, f64> = HashMap::new();
        if has_step_library {
            // Each step depletes under its own library's rates, so the depth is summed step by step instead of
            // scaling the base rates by the whole schedule's flux-weighted time.
            for (si, step) in sched.iter().enumerate() {
                let step_time_s = (step.duration() * step.multiplier()).get();
                let step_react = match step_spectrum_id[si] {
                    Some(id) => &react_extra[id],
                    None => &react,
                };
                for (r, c, v) in step_react {
                    if *r == *c && *v < 0.0 {
                        *burnup_optical_depth.entry(*c).or_insert(0.0) += -v * step_time_s;
                    }
                }
            }
        } else {
            for (r, c, v) in &react {
                if *r == *c && *v < 0.0 {
                    *burnup_optical_depth.entry(*c).or_insert(0.0) += -v * flux_weighted_time_s;
                }
            }
        }
        // Product-column optical depth: the scale on which a flux
        // re-normalisation stops being linear — second-hop capture along
        // a product chain contributes at order (product depth)^2, so the
        // linear-scaling certificate bounds the correction by this depth.
        let mut product_optical_depth_max = 0.0f64;
        let mut product_optical_depth_nuclide = None;
        for (index, optical_depth) in burnup_optical_depth {
            let fraction = -(-optical_depth).exp_m1();
            if bulk.contains_key(&index) {
                if optical_depth > led.burnup_optical_depth_max {
                    led.burnup_optical_depth_max = optical_depth;
                    led.burnup_fraction_max = fraction;
                    led.burnup_nuclide = Some(ch.keys[index]);
                }
            } else if optical_depth > product_optical_depth_max {
                product_optical_depth_max = optical_depth;
                product_optical_depth_nuclide = Some(ch.keys[index]);
            }
        }
        // A partition (P124) removes bulk atoms mid-run; the trace formulation holds the bulk
        // undepleted, so a partition schedule always solves the full coupled system.
        let has_partition = sched.iter().any(|step| !step.partition.is_empty());
        // P133: the trace reservoir is constant, so a bulk nuclide that decays appreciably over the
        // schedule (an activated restart inventory) needs the coupled system just as burn-up does.
        // Decay depth uses the effective lambda (decay_scale and half-life overrides included).
        let schedule_time_s: f64 = sched.iter().map(|step| step.duration().get()).sum();
        let mut bulk_decay_fraction_max = 0.0f64;
        let mut bulk_decay_nuclide = None;
        for &(r, c, v) in &decay_edges {
            if r == c && v < 0.0 && bulk.contains_key(&c) {
                let fraction = -(v * schedule_time_s).exp_m1();
                if fraction > bulk_decay_fraction_max {
                    bulk_decay_fraction_max = fraction;
                    bulk_decay_nuclide = Some(ch.keys[c]);
                }
            }
        }
        let bulk_decays = bulk_decay_fraction_max >= 1e-6;
        let mode = match spec.options.mode.as_str() {
            "auto" if has_partition || bulk_decays => "coupled",
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
        if bulk_decays && mode == "trace" {
            let (za, liso) = bulk_decay_nuclide.expect("set with the fraction");
            return Err(format!(
                "options.mode 'trace' holds the initial material constant, but {} decays by a fraction of \
                 {bulk_decay_fraction_max:.3e} over the schedule; use 'coupled' or 'auto'",
                name_of(za, liso)
            ));
        }
        if has_partition && mode == "trace" {
            return Err(
                "schedule partition is not supported with options.mode 'trace': the trace \
                 formulation holds the bulk material undepleted; use 'coupled' or 'auto'"
                    .into(),
            );
        }
        // ---- feed/removal (P23): per-step boundary sources and first-order sinks.
        // Feed becomes a unit-state source edge, exactly like a constant bulk production term.
        // Removal becomes a first-order edge into the dedicated `removed` sink plus a diagonal
        // loss. Reservoir nuclides in trace mode are exempt from removal: the trace formulation
        // defines them as undepleted constants, and the ledger names every exemption.
        let has_feed = sched.iter().any(|step| !step.feed.is_empty());
        let has_removal = sched.iter().any(|step| !step.removal.is_empty());
        // a partition's removed atoms go to the same sink as first-order removal
        let has_sink = has_removal || has_partition;
        let removed = has_sink.then_some(ch.n);
        let n_total = ch.n + usize::from(has_sink);
        let mut step_feed: Vec<Vec<(usize, usize, f64)>> = Vec::with_capacity(sched.len());
        let mut feed_states: std::collections::HashSet<usize> = Default::default();
        for (si, step) in sched.iter().enumerate() {
            let mut edges = Vec::with_capacity(step.feed.len());
            for &(key, rate) in step.feed.iter() {
                let state = ch.index.get(&key).copied().ok_or_else(|| {
                    format!(
                        "schedule step {} feeds {}_{} which is absent from the decay chain",
                        si + 1,
                        key.0,
                        key.1
                    )
                })?;
                feed_states.insert(state);
                edges.push((state, ch.unit, rate));
            }
            step_feed.push(edges);
        }
        // Reservoir nuclides that are explicitly fed carry their fed population in a real tracked
        // state while the constant reservoir keeps producing through the unit source. A reservoir
        // nuclide that is also produced from a tracked (non-bulk) precursor gets the same hybrid
        // treatment: its constant bulk amount still sources through the unit state, and the atoms
        // arriving from the precursor accumulate in the tracked state and react further. Without
        // this that production had no destination and was dropped unledgered, which broke
        // composition superposition in trace mode (P75b: Co-59 from Fe-59, V-51 from Ti-51).
        // Production from one reservoir nuclide into another (Ca-40(n,p)K-40 in concrete) is
        // likewise tracked rather than dropped: trace mode approximates only the constancy of the
        // bulk, not the absence of production into nuclides that happen to be bulk.
        let mut tracked_reservoir: std::collections::HashSet<usize> = feed_states
            .iter()
            .copied()
            .filter(|state| bulk.contains_key(state))
            .collect();
        if mode == "trace" {
            for &(r, c, _) in decay_edges
                .iter()
                .chain(react.iter())
                .chain(react_extra.iter().flat_map(|edges| edges.iter()))
            {
                if r != c && bulk.contains_key(&r) {
                    tracked_reservoir.insert(r);
                }
            }
        }
        let mut step_removal: Vec<Vec<(usize, usize, f64)>> = Vec::with_capacity(sched.len());
        let mut removal_reservoir_exempt: Vec<String> = Vec::new();
        for (si, step) in sched.iter().enumerate() {
            let mut edges = Vec::new();
            for (selector, rate) in &step.removal {
                let targets: Vec<usize> = match selector {
                    crate::spec::RemovalSelector::Nuclide(za, liso) => {
                        vec![ch.index.get(&(*za, *liso)).copied().ok_or_else(|| {
                            format!(
                                "schedule step {} removes {}_{} which is absent from the decay chain",
                                si + 1,
                                za,
                                liso
                            )
                        })?]
                    }
                    crate::spec::RemovalSelector::Element(z) => ch
                        .keys
                        .iter()
                        .enumerate()
                        .filter(|(_, key)| key.0 / 1000 == *z)
                        .map(|(index, _)| index)
                        .collect(),
                };
                if targets.is_empty() {
                    return Err(format!(
                        "schedule step {} removal selects no chain nuclides",
                        si + 1
                    ));
                }
                for target in targets {
                    if mode == "trace"
                        && bulk.contains_key(&target)
                        && !feed_states.contains(&target)
                    {
                        let name = name_of(ch.keys[target].0, ch.keys[target].1);
                        if !removal_reservoir_exempt.contains(&name) {
                            removal_reservoir_exempt.push(name);
                        }
                        continue;
                    }
                    let sink = removed.expect("removal implies the sink state exists");
                    edges.push((sink, target, *rate));
                    edges.push((target, target, -*rate));
                }
            }
            step_removal.push(edges);
        }
        // ---- damage (P23): coverage is fixed once the material and table are both known;
        // the per-step fold uses each step's target inventories (reservoir or evolved).
        let damage_plan = match &self.damage {
            Some(prepared) => {
                if prepared.table.boundaries_eV() != lib.boundaries_ev() {
                    return Err(
                        "damage table boundaries_eV do not match the activation library boundaries"
                            .into(),
                    );
                }
                Some(prepared.table.plan(
                    &bulk_inv,
                    &prepared.options.displacement_energy_eV,
                    prepared.options.require_complete,
                )?)
            }
            None => None,
        };
        // P127: the bulk state behind every unit-state production edge, as (row, bulk state, rate); the routes
        // analysis splits the aggregated unit column back into its reservoirs with these. Filled only on request.
        let want_routes = spec.pathways.is_some();
        let mut unit_origin_decay: Vec<(usize, usize, f64)> = Vec::new();
        let mut unit_origin_react: Vec<Vec<(usize, usize, f64)>> = Vec::new();
        // (product row fed, rate at unit multiplier, bulk nuclide it came from), one list per spectrum
        // in rsubs order (0 = base, 1.. = step overrides)
        let mut sources: Vec<Vec<PathwaySource>> = Vec::new();
        let mut d_src: Vec<(usize, usize, f64)> = Vec::new();
        // r_srcs[0] is the base spectrum; one entry per distinct step override follows
        let mut r_srcs: Vec<Vec<(usize, usize, f64)>> = Vec::new();
        let r_src: Vec<(usize, usize, f64)>;
        let mut reaction_derivatives = Vec::new();
        // Derivative sets for each distinct step-spectrum override, parallel to
        // react_extra_assembly (empty when the schedule or run carries neither).
        let mut reaction_derivatives_extra: Vec<Vec<chain::ReactionDerivative>> =
            vec![Vec::new(); react_extra_assembly.len()];
        let mut yield_derivatives_extra: Vec<Vec<chain::YieldDerivative>> =
            vec![Vec::new(); react_extra_assembly.len()];
        // (decaying parent state, matrix row, matrix column, d(value)/d lambda_parent)
        let mut decay_derivatives: Vec<(usize, usize, usize, f64)> = Vec::new();
        let mut yield_derivatives = Vec::new();
        let mut bulk_heat = 0.0;
        let mut bulk_heat_split = (0.0, 0.0, 0.0);
        let mut bulk_photon_active: Vec<(String, (i32, i32), f64)> = Vec::new();
        if mode == "trace" {
            for (r, c, v) in &decay_edges {
                let lambda_c = lambda_eff[*c];
                if bulk.contains_key(c) {
                    if r != c && (!bulk.contains_key(r) || tracked_reservoir.contains(r)) {
                        d_src.push((*r, ch.unit, v * bulk[c]));
                        if want_routes {
                            unit_origin_decay.push((*r, *c, v * bulk[c]));
                        }
                        if lambda_c > 0.0 {
                            decay_derivatives.push((*c, *r, ch.unit, v * bulk[c] / lambda_c));
                        }
                    }
                    // a fed reservoir nuclide's tracked population decays and produces normally;
                    // production into other reservoir rows stays absorbed by the reservoir
                    if tracked_reservoir.contains(c)
                        && (r == c || !bulk.contains_key(r) || tracked_reservoir.contains(r))
                    {
                        d_src.push((*r, *c, *v));
                        if lambda_c > 0.0 {
                            decay_derivatives.push((*c, *r, *c, v / lambda_c));
                        }
                    }
                } else if !bulk.contains_key(r) || tracked_reservoir.contains(r) {
                    d_src.push((*r, *c, *v));
                    if lambda_c > 0.0 {
                        decay_derivatives.push((*c, *r, *c, v / lambda_c));
                    }
                }
            }
            // The trace remap runs per spectrum: bulk feeds differ across
            // spectra, so each override needs its own r_src and pathway sources.
            // The dropped-production ledger stays base-spectrum only.
            let reacts: Vec<&Vec<(usize, usize, f64)>> =
                std::iter::once(&react).chain(react_extra.iter()).collect();
            for (si_react, react_i) in reacts.iter().enumerate() {
                let mut r_i: Vec<(usize, usize, f64)> = Vec::new();
                let mut sources_i: Vec<PathwaySource> = Vec::new();
                let mut origin_i: Vec<(usize, usize, f64)> = Vec::new();
                for (r, c, v) in react_i.iter() {
                    if bulk.contains_key(c) {
                        if r == c {
                            if tracked_reservoir.contains(c) {
                                r_i.push((*r, *c, *v));
                            }
                            continue;
                        }
                        if bulk.contains_key(r) && !tracked_reservoir.contains(r) {
                            if si_react == 0 {
                                led.bulk_production_dropped.push((
                                    name_of(ch.keys[*c].0, ch.keys[*c].1),
                                    name_of(ch.keys[*r].0, ch.keys[*r].1),
                                    *v,
                                ));
                            }
                            continue;
                        }
                        if tracked_reservoir.contains(c) {
                            r_i.push((*r, *c, *v));
                        }
                        r_i.push((*r, ch.unit, v * bulk[c]));
                        if want_routes {
                            origin_i.push((*r, *c, v * bulk[c]));
                        }
                        sources_i.push((*r, v * bulk[c], ch.keys[*c]));
                    } else if !bulk.contains_key(r) || tracked_reservoir.contains(r) {
                        r_i.push((*r, *c, *v));
                    }
                }
                r_srcs.push(r_i);
                sources.push(sources_i);
                unit_origin_react.push(origin_i);
            }
            r_src = r_srcs[0].clone();
            let derivative_remap =
                |derivative: &chain::ReactionDerivative,
                 reaction_derivatives: &mut Vec<chain::ReactionDerivative>| {
                    if bulk.contains_key(&derivative.column) {
                        if tracked_reservoir.contains(&derivative.column)
                            && (derivative.row == derivative.column
                                || !bulk.contains_key(&derivative.row)
                                || tracked_reservoir.contains(&derivative.row))
                        {
                            reaction_derivatives.push(*derivative);
                        }
                        if derivative.row == derivative.column
                            || (bulk.contains_key(&derivative.row)
                                && !tracked_reservoir.contains(&derivative.row))
                        {
                            return;
                        }
                        reaction_derivatives.push(chain::ReactionDerivative {
                            row: derivative.row,
                            column: ch.unit,
                            per_barn_s: derivative.per_barn_s * bulk[&derivative.column],
                            ..*derivative
                        });
                    } else if !bulk.contains_key(&derivative.row)
                        || tracked_reservoir.contains(&derivative.row)
                    {
                        reaction_derivatives.push(*derivative);
                    }
                };
            for derivative in &reaction_assembly.derivatives {
                derivative_remap(derivative, &mut reaction_derivatives);
            }
            for (extra, reaction_derivatives_extra) in react_extra_assembly
                .iter()
                .zip(reaction_derivatives_extra.iter_mut())
            {
                for derivative in &extra.derivatives {
                    derivative_remap(derivative, reaction_derivatives_extra);
                }
            }
            // Fission-yield directions ride the same trace-form remap as the
            // reaction directions their product edges belong to.
            let yield_remap =
                |derivative: &chain::YieldDerivative,
                 yield_derivatives: &mut Vec<chain::YieldDerivative>| {
                    if bulk.contains_key(&derivative.column) {
                        if tracked_reservoir.contains(&derivative.column)
                            && (derivative.row == derivative.column
                                || !bulk.contains_key(&derivative.row)
                                || tracked_reservoir.contains(&derivative.row))
                        {
                            yield_derivatives.push(*derivative);
                        }
                        if derivative.row == derivative.column
                            || (bulk.contains_key(&derivative.row)
                                && !tracked_reservoir.contains(&derivative.row))
                        {
                            return;
                        }
                        yield_derivatives.push(chain::YieldDerivative {
                            row: derivative.row,
                            column: ch.unit,
                            per_yield_s: derivative.per_yield_s * bulk[&derivative.column],
                            ..*derivative
                        });
                    } else if !bulk.contains_key(&derivative.row)
                        || tracked_reservoir.contains(&derivative.row)
                    {
                        yield_derivatives.push(*derivative);
                    }
                };
            for derivative in &reaction_assembly.yield_derivatives {
                yield_remap(derivative, &mut yield_derivatives);
            }
            for (extra, yield_derivatives_extra) in react_extra_assembly
                .iter()
                .zip(yield_derivatives_extra.iter_mut())
            {
                for derivative in &extra.yield_derivatives {
                    yield_remap(derivative, yield_derivatives_extra);
                }
            }
            // Chain-index order: summing in HashMap order made heat/photon bits vary
            // from process to process whenever two or more bulk nuclides decay.
            let mut bulk_ordered: Vec<(&usize, &f64)> = bulk.iter().collect();
            bulk_ordered.sort_unstable_by_key(|(c, _)| **c);
            for (c, nb) in bulk_ordered {
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
            for (r, c, v) in &decay_edges {
                let lambda_c = lambda_eff[*c];
                if lambda_c > 0.0 {
                    decay_derivatives.push((*c, *r, *c, v / lambda_c));
                }
            }
            d_src = decay_edges;
            r_src = react.clone();
            r_srcs.push(react.clone());
            for extra in &react_extra {
                r_srcs.push(extra.clone());
            }
            reaction_derivatives = reaction_assembly.derivatives;
            yield_derivatives = reaction_assembly.yield_derivatives;
            for (i, extra) in react_extra_assembly.into_iter().enumerate() {
                reaction_derivatives_extra[i] = extra.derivatives;
                yield_derivatives_extra[i] = extra.yield_derivatives;
            }
        }
        // ---- initial vector
        let mut n0 = vec![0.0f64; n_total];
        if mode == "trace" {
            n0[ch.unit] = 1.0;
        } else {
            for (c, v) in &bulk {
                n0[*c] = *v;
            }
            if has_feed {
                n0[ch.unit] = 1.0;
            }
        }
        // ---- prune
        // Feed and removal edges join the reachability graph so fed nuclides and the removed
        // sink are kept; removal diagonals accumulate into the bound estimate's loss rates.
        let (mut keep, rate_pruned): (Vec<usize>, Vec<(usize, f64, f64)>) =
            match spec.options.prune.as_str() {
                "none" => ((0..n_total).collect(), Vec::new()),
                p => {
                    let mut graph = d_src.clone();
                    for edges in step_feed.iter().chain(step_removal.iter()) {
                        graph.extend_from_slice(edges);
                    }
                    // prune on the union of every step's reaction graph — a
                    // nuclide reachable under any spectrum must be kept
                    let r_union: Vec<(usize, usize, f64)> =
                        r_srcs.iter().flatten().copied().collect();
                    crate::prune::reachable_physical(
                        n_total,
                        &graph,
                        &r_union,
                        &n0,
                        sched,
                        p == "rate",
                        // P65: a screening spec prunes at its own raised
                        // bound; the certificate maps the drop onto the
                        // banded responses.
                        match spec.options.screen.as_ref() {
                            Some(screen) => AtomsPerGram::new(screen.bmin_atoms_per_g)
                                .map_err(|e| format!("options.screen.bmin_atoms_per_g: {e}"))?,
                            None => physical.bmin,
                        },
                    )
                }
            };
        // No edge reaches the sink of a partition-only schedule, so reachability would drop it;
        // it is the highest index, so appending keeps `keep` ascending.
        if let Some(sink) = removed.filter(|_| has_partition) {
            if !keep.contains(&sink) {
                keep.push(sink);
            }
        }
        let mut pos = vec![usize::MAX; n_total];
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
        let rsubs: Vec<Vec<(usize, usize, C64)>> = r_srcs.iter().map(sub).collect();
        // derivative_subs[0] is the base spectrum; one entry per distinct
        // step-spectrum override follows. A row's tangent direction differs
        // per spectrum because the collapse weights differ.
        let to_sub = |derivatives: Vec<chain::ReactionDerivative>,
                      pos: &Vec<usize>|
         -> BTreeMap<usize, Vec<(usize, usize, C64)>> {
            let mut derivative_sub: BTreeMap<usize, Vec<(usize, usize, C64)>> = BTreeMap::new();
            for derivative in derivatives {
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
            derivative_sub
        };
        type DirectionMap = BTreeMap<usize, Vec<(usize, usize, C64)>>;
        let mut derivative_subs: Vec<DirectionMap> = Vec::with_capacity(1 + react_extra.len());
        derivative_subs.push(to_sub(reaction_derivatives, &pos));
        for extra in reaction_derivatives_extra {
            derivative_subs.push(to_sub(extra, &pos));
        }
        // Decay-constant directions, grouped by the decaying parent's global
        // chain state; only edges inside the kept subspace survive.
        let mut decay_sub: BTreeMap<usize, Vec<(usize, usize, C64)>> = BTreeMap::new();
        for (parent, row, column, per_lambda_s) in decay_derivatives {
            if per_lambda_s != 0.0 && pos[row] != usize::MAX && pos[column] != usize::MAX {
                decay_sub.entry(parent).or_default().push((
                    pos[row],
                    pos[column],
                    C64::new(per_lambda_s, 0.0),
                ));
            }
        }
        // Independent-yield directions, keyed by (parent ZA, parent LISO,
        // product ZA, product state). Per-spectrum like derivative_subs: a
        // yield direction scales with the parent's collapsed fission rate.
        type YieldTangents = BTreeMap<(i32, i32, i32, i32), Vec<(usize, usize, C64)>>;
        let to_yield_sub =
            |derivatives: Vec<chain::YieldDerivative>, pos: &Vec<usize>| -> YieldTangents {
                let mut yield_sub: YieldTangents = BTreeMap::new();
                for derivative in derivatives {
                    if derivative.per_yield_s != 0.0
                        && pos[derivative.row] != usize::MAX
                        && pos[derivative.column] != usize::MAX
                    {
                        yield_sub
                            .entry((
                                derivative.parent.0,
                                derivative.parent.1,
                                derivative.product.0,
                                derivative.product.1,
                            ))
                            .or_default()
                            .push((
                                pos[derivative.row],
                                pos[derivative.column],
                                C64::new(derivative.per_yield_s, 0.0),
                            ));
                    }
                }
                yield_sub
            };
        let mut yield_subs: Vec<YieldTangents> = Vec::with_capacity(1 + react_extra.len());
        yield_subs.push(to_yield_sub(yield_derivatives, &pos));
        for extra in yield_derivatives_extra {
            yield_subs.push(to_yield_sub(extra, &pos));
        }
        let mut y = vec![0.0f64; m];
        for (g, v) in n0.iter().enumerate() {
            if *v != 0.0 && pos[g] != usize::MAX {
                y[pos[g]] = *v;
            }
        }
        // P93: the flux channel's input-group basis, resolved once whatever
        // else `uncertainty.channels` requests. A mesh cell's pre-rebin
        // source groups (`flux_origin`) take priority when present; a plain
        // spec's declared spectrum is the basis otherwise.
        let flux_channel_requested = spec
            .uncertainty
            .as_ref()
            .is_some_and(|options| options.channels.iter().any(|name| name == "flux"));
        let flux_group_weights: Option<Vec<Vec<(usize, f64)>>> =
            match (flux_channel_requested, &flux_origin) {
                (true, Some(origin)) => Some(crate::flux::group_rebin_weights(
                    origin.source_boundaries_eV,
                    lib.boundaries_ev(),
                )?),
                _ => None,
            };
        let flux_relative_error: Option<Vec<f64>> = if !flux_channel_requested {
            None
        } else {
            Some(resolve_flux_relative_error(
                &spec.spectrum,
                flux_origin.as_ref(),
            )?)
        };
        let build_flux_channel =
            |dense_library: &library::Library,
             derivative_sub_base: &BTreeMap<usize, Vec<(usize, usize, C64)>>|
             -> Result<(Vec<FluxParameter>, Vec<Csc>), String> {
                if !flux_channel_requested {
                    return Ok((Vec::new(), Vec::new()));
                }
                let relative_error = flux_relative_error.as_deref().expect("checked above");
                let input = match (&flux_origin, &flux_group_weights) {
                    (Some(origin), Some(weights)) => FluxInputGroups::Rebinned {
                        boundaries_ev: origin.source_boundaries_eV,
                        flux_per_group: origin.source_flux_per_group,
                        relative_error,
                        weights: weights.clone(),
                    },
                    _ => FluxInputGroups::Identity {
                        boundaries_ev: dense_library.boundaries_ev(),
                        flux_per_group: phi,
                        relative_error,
                        descending: spec.spectrum.descending,
                    },
                };
                build_flux_parameters(
                    derivative_sub_base,
                    dense_library,
                    lib_targets,
                    shield_plan.as_ref(),
                    m,
                    phi,
                    &input,
                )
            };
        // The collapsed MF=33 covariance of the depletion table's loss rows.
        let mut depletion_covariance: Option<LossCovariance> = None;
        let mut uncertainty_runtime = match (&self.covariance, &spec.uncertainty) {
            (Some(prepared), Some(options)) => {
                // Union of rows active under any step spectrum: a row's tangent
                // direction is spectrum-dependent, so parameters are (spectrum,
                // row) pairs and the covariance collapses jointly over all
                // spectra — the same physical per-group perturbation weighted
                // by each step's own flux shape.
                let active_rows: Vec<usize> = derivative_subs
                    .iter()
                    .flat_map(|subs| subs.keys().copied())
                    .collect::<BTreeSet<usize>>()
                    .into_iter()
                    .collect();
                let dense_library = lib.dense().ok_or(
                    "uncertainty propagation requires the verified dense activation library",
                )?;
                // When self-shielding is active the depletion folds per-group
                // scale factors into each row's collapsed cross section; the
                // covariance must collapse under the same per-row weights so
                // the sampled parameters mean the same thing the matrix uses.
                let row_scale = |row: usize, group: usize| -> f64 {
                    let Some(plan) = shield_plan.as_ref() else {
                        return 1.0;
                    };
                    let descriptor = dense_library.rows[row];
                    let Some(&(za, liso)) = lib_targets.get(descriptor.target) else {
                        return 1.0;
                    };
                    plan.row_scales(za, liso, descriptor.mt)
                        .and_then(|scales| scales.get(&group).copied())
                        .unwrap_or(1.0)
                };
                let phis: Vec<&[f64]> = std::iter::once(phi)
                    .chain(extra_spectra.iter().copied())
                    .collect();
                let mut collapsed = prepared.library.collapse_weighted_multi(
                    dense_library,
                    &phis,
                    &active_rows,
                    &row_scale,
                )?;
                let n_covered_rows = collapsed
                    .row_indices
                    .iter()
                    .copied()
                    .collect::<BTreeSet<usize>>()
                    .len();
                for (param, (&row, &cross_section)) in collapsed
                    .row_indices
                    .iter()
                    .zip(&collapsed.one_group_barns)
                    .enumerate()
                {
                    let phi_s = phis[collapsed.param_spectrum[param]];
                    let flux_denominator: f64 = phi_s.iter().sum();
                    let nominal = if flux_denominator == 0.0 {
                        0.0
                    } else if shield_plan.is_some() {
                        let first_flux_group = phi_s
                            .iter()
                            .position(|flux| *flux != 0.0)
                            .unwrap_or(phi_s.len());
                        let last_flux_group = phi_s
                            .iter()
                            .rposition(|flux| *flux != 0.0)
                            .map(|group| group + 1)
                            .unwrap_or(first_flux_group);
                        lib.collapse_row_scaled(
                            row,
                            phi_s,
                            flux_denominator,
                            first_flux_group,
                            last_flux_group,
                            &|group| row_scale(row, group),
                        )
                    } else {
                        lib.one_group(row, phi_s)
                    };
                    if cross_section.to_bits() != nominal.to_bits() {
                        return Err(format!(
                            "covariance collapse nominal cross section differs at library row {row}"
                        ));
                    }
                }
                // ADCROSS (P124): the named reaction's covered parameters take the declared
                // relative standard uncertainty; scaling a parameter's row and column leaves
                // every correlation unchanged.
                if spec
                    .overrides
                    .iter()
                    .any(|item| item.kind == "cross_section_uncertainty")
                {
                    let (scale, records) = crate::overrides::uncertainty_scales(
                        &spec.overrides,
                        lib.rows(),
                        lib_targets,
                        &collapsed.row_indices,
                        &collapsed.one_group_barns,
                        &collapsed.covariance_barn2,
                    )?;
                    crate::overrides::rescale_covariance(&mut collapsed.covariance_barn2, &scale);
                    override_ledger.extend(records);
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
                let excluded_self: std::collections::BTreeSet<(usize, i32)> = collapsed
                    .excluded_blocks
                    .iter()
                    .filter(|block| block.mt == block.mt1)
                    .map(|block| (block.target, block.mt))
                    .collect();
                // One cross-section parameter per (spectrum, row): the
                // direction is the row's derivative set collapsed under that
                // spectrum, the nominal is its one-group value under that
                // spectrum, and the joint covariance rows keep the
                // cross-spectrum correlations.
                let n_spectra = phis.len();
                let restrict = options.restrict_tangents_to_covered;
                // Per-parameter reports index the emitted sensitivity vector
                // positionally — incompatible with a compact tangent set.
                // The flag is internal (study comparison); keep the guard
                // so any future caller gets an error, not wrong data.
                if restrict
                    && (options.voi.is_some()
                        || options.isomer.is_some()
                        || options.design.is_some())
                {
                    return Err("restrict_tangents_to_covered cannot combine with \
                         uncertainty.voi/isomer/design reporting"
                        .into());
                }
                // A covered parameter with zero collapsed variance has an
                // identically zero covariance row (Σ is PSD, so Σ_ii = 0
                // forces Σ_i* = 0): it contributes exactly zero to every
                // contraction and its tangent may be skipped without
                // approximation. Restricted runs drop it.
                let n_cov = n_covered_rows.min(collapsed.row_indices.len());
                let n_cov_total = n_spectra * n_cov;
                let mut zero_variance_pairs: HashSet<(usize, usize)> = HashSet::new();
                for spectrum in 0..n_spectra {
                    for (pi, &row_index) in collapsed.row_indices[..n_cov].iter().enumerate() {
                        let diag = spectrum * n_cov + pi;
                        if collapsed.covariance_barn2[diag * n_cov_total + diag] == 0.0 {
                            zero_variance_pairs.insert((spectrum, row_index));
                        }
                    }
                }
                if std::env::var_os("ACTINV_TRACE").is_some() {
                    eprintln!(
                        "ACTINV_TRACE cov covered_rows={n_covered_rows} \
                         covered_params={n_cov_total} zero_diag={} \
                         spectra={n_spectra}",
                        zero_variance_pairs.len()
                    );
                }
                let mut parameters = Vec::with_capacity(active_rows.len() * n_spectra);
                let mut directions = Vec::with_capacity(parameters.capacity());
                let mut parameter_spectrum = Vec::with_capacity(parameters.capacity());
                let mut position_of: HashMap<(usize, usize), usize> =
                    HashMap::with_capacity(parameters.capacity());
                let mut propagated_parameter_positions = Vec::new();
                for spectrum in 0..n_spectra {
                    let phi_s = phis[spectrum];
                    for &row_index in &active_rows {
                        let row = lib.rows()[row_index];
                        let &(za, liso) = lib_targets.get(row.target).ok_or_else(|| {
                            format!("activation row {row_index} has an invalid target index")
                        })?;
                        let covered = covered_rows.contains_key(&row_index);
                        let excluded = covered && excluded_self.contains(&(row.target, row.mt));
                        position_of.insert((spectrum, row_index), parameters.len());
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
                            spectrum,
                            collapsed_cross_section_b: lib.one_group(row_index, phi_s),
                            covariance_covered: covered,
                            covariance_excluded: excluded,
                        });
                        // Restricted mode integrates a tangent only for
                        // covariance-covered rows: an uncovered parameter's
                        // tangent never enters the covered contraction, so
                        // integrating it is pure cost — its sensitivity
                        // record emits zero instead. A covered row whose
                        // collapsed variance is zero likewise contributes an
                        // exactly-zero row to every contraction — skipped.
                        if !restrict
                            || (covered && !zero_variance_pairs.contains(&(spectrum, row_index)))
                        {
                            propagated_parameter_positions.push(parameters.len() - 1);
                            let triplets = derivative_subs[spectrum]
                                .get(&row_index)
                                .map_or(&[][..], |triplets| triplets.as_slice());
                            directions.push(Csc::from_triplets(m, triplets));
                            parameter_spectrum.push(Some(spectrum));
                        }
                    }
                }
                // covered_parameter_positions aligns `parameters` positions to
                // the collapsed matrix's (spectrum-major) covered order.
                let mut covered_parameter_positions =
                    Vec::with_capacity(n_covered_rows * n_spectra);
                for spectrum in 0..n_spectra {
                    for &row_index in collapsed.row_indices[..n_cov].iter() {
                        if let Some(&position) = position_of.get(&(spectrum, row_index)) {
                            covered_parameter_positions.push(position);
                        }
                    }
                }
                let mut decay_parameters = Vec::new();
                let mut yield_parameters = Vec::new();
                let decay_channel_requested = options
                    .channels
                    .iter()
                    .any(|name| name == "decay_constants");
                let yield_channel_requested =
                    options.channels.iter().any(|name| name == "fission_yields");
                if decay_channel_requested {
                    for (&parent, triplets) in &decay_sub {
                        let &(za, liso) = ch.keys.get(parent).ok_or_else(|| {
                            format!("decay parameter {parent} is outside the chain index")
                        })?;
                        // Effective lambda: honours a decay_scale factor
                        // the way every other response in this run does.
                        // The declared relative uncertainty attaches to the
                        // datum (unscaled half-life), so the propagated
                        // sigma scales with the effective lambda — the same
                        // convention as scaled yields.
                        let lambda = nuclides
                            .get(&(za, liso))
                            .map_or(0.0, |nuclide| nuclide.lambda());
                        let relative = self
                            .nuclides
                            .get(&(za, liso))
                            .filter(|nuclide| nuclide.half_life > 0.0)
                            .map_or(0.0, |nuclide| nuclide.d_half_life / nuclide.half_life);
                        // ADLAM (P124): a declared relative sigma replaces the datum's own.
                        let relative = decay_relative_overrides
                            .get(&(za, liso))
                            .copied()
                            .unwrap_or(relative);
                        decay_parameters.push(DecayParameter {
                            nuclide: name_of(za, liso),
                            za,
                            liso,
                            lambda_s: lambda,
                            standard_uncertainty_s: lambda * relative,
                            covered: relative > 0.0,
                        });
                        directions.push(Csc::from_triplets(m, triplets));
                        parameter_spectrum.push(None);
                    }
                }
                for &(za, liso) in decay_relative_overrides.keys() {
                    if !decay_parameters
                        .iter()
                        .any(|parameter| (parameter.za, parameter.liso) == (za, liso))
                    {
                        return Err(format!(
                            "override half_life_uncertainty '{}' names a nuclide with no decay-constant parameter in this run",
                            name_of(za, liso)
                        ));
                    }
                }
                if yield_channel_requested {
                    // A yield parameter exists per spectrum in which its parent
                    // fission rate is nonzero: the tangent direction rides the
                    // parent's collapsed rate, which is spectrum-dependent.
                    let yield_keys: BTreeSet<(i32, i32, i32, i32)> = yield_subs
                        .iter()
                        .flat_map(|subs| subs.keys().copied())
                        .collect();
                    for (spectrum, yield_sub) in yield_subs.iter().enumerate() {
                        for &(parent_za, parent_liso, product_za, product_liso) in &yield_keys {
                            let effective = effective_fission_yields.get(&(parent_za, parent_liso));
                            let yield_value = effective
                                .and_then(|yields| yields.products.get(&(product_za, product_liso)))
                                .copied()
                                .unwrap_or(0.0);
                            let standard_uncertainty = effective
                                .and_then(|yields| {
                                    yields.uncertainties.get(&(product_za, product_liso))
                                })
                                .copied()
                                .unwrap_or(0.0);
                            yield_parameters.push(YieldParameter {
                                parent_nuclide: name_of(parent_za, parent_liso),
                                parent_za,
                                parent_liso,
                                product_nuclide: name_of(product_za, product_liso),
                                product_za,
                                product_liso,
                                spectrum,
                                yield_value,
                                standard_uncertainty,
                                covered: standard_uncertainty > 0.0,
                            });
                            let triplets = yield_sub
                                .get(&(parent_za, parent_liso, product_za, product_liso))
                                .map_or(&[][..], |triplets| triplets.as_slice());
                            directions.push(Csc::from_triplets(m, triplets));
                            parameter_spectrum.push(Some(spectrum));
                        }
                    }
                }
                let (flux_parameters, flux_directions) =
                    build_flux_channel(dense_library, &derivative_subs[0])?;
                for direction in flux_directions {
                    directions.push(direction);
                    parameter_spectrum.push(Some(0));
                }
                // decay/yield parameters always propagate (declared-uncertainty
                // channels, few in number); their dense positions follow the
                // cross-section block.
                let xs_count = parameters.len();
                for i in 0..decay_parameters.len() {
                    propagated_parameter_positions.push(xs_count + i);
                }
                for i in 0..yield_parameters.len() {
                    propagated_parameter_positions.push(xs_count + decay_parameters.len() + i);
                }
                // P93 flux-channel parameters likewise always propagate; their
                // directions were pushed after the yield block.
                for i in 0..flux_parameters.len() {
                    propagated_parameter_positions
                        .push(xs_count + decay_parameters.len() + yield_parameters.len() + i);
                }
                // The depletion table's loss rows take their covariance from the same collapse
                // (flux weights, shielding scales and ADCROSS rescaling included).
                if want_depletion {
                    let tracked_columns: BTreeSet<usize> =
                        depletion_columns.iter().map(|c| c.0).collect();
                    depletion_covariance =
                        Some(LossCovariance::from_collapsed(&collapsed, |row| {
                            let descriptor = lib.rows()[row];
                            descriptor.zap == -1
                                && lib_targets
                                    .get(descriptor.target)
                                    .and_then(|key| ch.index.get(key))
                                    .is_some_and(|column| tracked_columns.contains(column))
                        }));
                }
                // Restricted runs contract the quadratic form over nonzero
                // covariance entries only — MF=33 blocks are block-sparse, so
                // on wide-coverage chains this replaces a covered^2 scan per
                // response with one per stored pair. Row-major traversal is
                // bitwise-identical to the dense loop's nonzero terms.
                let (covariance_barn2, covariance_pairs) = if restrict {
                    let mut pairs = Vec::with_capacity(collapsed.covariance_barn2.len() / 16);
                    for row in 0..n_cov_total {
                        let base = row * n_cov_total;
                        for (column, &value) in collapsed.covariance_barn2[base..base + n_cov_total]
                            .iter()
                            .enumerate()
                        {
                            if value != 0.0 {
                                pairs.push((row as u32, column as u32, value));
                            }
                        }
                    }
                    (Vec::new(), Some(pairs))
                } else {
                    (collapsed.covariance_barn2, None)
                };
                Some(UncertaintyRuntime {
                    tangents: vec![vec![0.0; m]; propagated_parameter_positions.len()],
                    propagated_parameter_positions,
                    contract_pairs: covariance_pairs.is_some(),
                    covariance_pairs: covariance_pairs.unwrap_or_default(),
                    directions,
                    parameter_spectrum,
                    decay_channel_requested,
                    yield_channel_requested,
                    flux_channel_requested,
                    method: "local first-order propagation of spectrum-collapsed ENDF-6 MF=33 covariance",
                    band_name: "MF=33 nuclear-data band",
                    parameters,
                    decay_parameters,
                    yield_parameters,
                    flux_parameters,
                    covered_parameter_positions,
                    covariance_barn2,
                    uncovered_library_rows: collapsed.uncovered_rows,
                    absent_cross_parameter_pairs: collapsed.absent_cross_parameter_pairs,
                    maximum_covariance_asymmetry_barn2: collapsed.maximum_asymmetry_barn2,
                    excluded_blocks: collapsed.excluded_blocks,
                    normal_multiplier: uncertainty_report::normal_multiplier(
                        options.confidence_level,
                    ),
                    alternate_y: y.clone(),
                })
            }
            (None, Some(options)) => {
                // P93 flux-only mode: `spec.validate()` guarantees
                // `channels == ["flux"]` whenever covariance is absent.
                let dense_library = lib.dense().ok_or(
                    "uncertainty propagation requires the verified dense activation library",
                )?;
                let (flux_parameters, flux_directions) =
                    build_flux_channel(dense_library, &derivative_subs[0])?;
                let mut directions = Vec::with_capacity(flux_parameters.len());
                let mut parameter_spectrum = Vec::with_capacity(flux_parameters.len());
                for direction in flux_directions {
                    directions.push(direction);
                    parameter_spectrum.push(Some(0));
                }
                Some(UncertaintyRuntime {
                    tangents: vec![vec![0.0; m]; flux_parameters.len()],
                    propagated_parameter_positions: (0..flux_parameters.len()).collect(),
                    directions,
                    parameter_spectrum,
                    decay_channel_requested: false,
                    yield_channel_requested: false,
                    flux_channel_requested,
                    method: "first-order propagation of transport-tally statistical error (flux channel only)",
                    band_name: "transport-tally statistical-error band (flux channel only)",
                    parameters: Vec::new(),
                    decay_parameters: Vec::new(),
                    yield_parameters: Vec::new(),
                    flux_parameters,
                    covered_parameter_positions: Vec::new(),
                    covariance_barn2: Vec::new(),
                    covariance_pairs: Vec::new(),
                    contract_pairs: false,
                    uncovered_library_rows: Vec::new(),
                    absent_cross_parameter_pairs: 0,
                    maximum_covariance_asymmetry_barn2: 0.0,
                    excluded_blocks: Vec::new(),
                    normal_multiplier: uncertainty_report::normal_multiplier(
                        options.confidence_level,
                    ),
                    alternate_y: y.clone(),
                })
            }
            (None, None) => None,
            _ => return Err("prepared covariance state does not match the run spec".into()),
        };
        if uncertainty_runtime.is_none()
            && spec.overrides.iter().any(|item| {
                item.kind == "half_life_uncertainty" || item.kind == "cross_section_uncertainty"
            })
        {
            return Err("uncertainty overrides need an uncertainty section".into());
        }
        profiler.finish("material_network_preparation", network_started);
        if std::env::var_os("ACTINV_TRACE").is_some() {
            eprintln!(
                "ACTINV_TRACE net_done tangents={} params={} cov_entries={} covered_params={}",
                uncertainty_runtime.as_ref().map_or(0, |r| r.tangents.len()),
                uncertainty_runtime
                    .as_ref()
                    .map_or(0, |r| r.parameters.len()),
                uncertainty_runtime
                    .as_ref()
                    .map_or(0, |r| r.covariance_barn2.len()),
                uncertainty_runtime
                    .as_ref()
                    .map_or(0, |r| r.covered_parameter_positions.len()),
            );
        }

        let solve_started = profiler.start();
        // ---- depletion table (opt-in): trace mode holds the reservoir constant, so it is depleted
        // analytically from the same rates; coupled mode reads the evolved populations.
        let depletion_with_covariance = depletion_covariance.is_some();
        let mut depletion = want_depletion.then(|| {
            let tracked: Vec<Tracked> = depletion_columns
                .iter()
                .map(|&(column, key, atoms)| {
                    let relative = decay_relative_overrides.get(&key).copied().or_else(|| {
                        self.nuclides
                            .get(&key)
                            .filter(|nuclide| nuclide.half_life > 0.0)
                            .map(|nuclide| nuclide.d_half_life / nuclide.half_life)
                    });
                    Tracked {
                        key,
                        column,
                        initial_atoms: atoms,
                        lambda: lambda_eff[column],
                        lambda_relative: relative.filter(|relative| *relative > 0.0),
                    }
                })
                .collect();
            let mut slot_rates = vec![std::mem::take(&mut led.loss_rates)];
            slot_rates.append(&mut loss_rates_extra);
            DepletionTracker::new(
                mode == "trace",
                spec.projectile.is_neutron(),
                tracked,
                slot_rates,
                depletion_covariance,
            )
        });
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
        let photon_boundaries = physical.photon_boundaries.values();
        let material_response_complete = cdiag.unknown.is_empty()
            && response.as_ref().is_some_and(|r| {
                material_mass_fractions
                    .keys()
                    .all(|e| r.element_mass_attenuation.contains_key(e))
            });
        // Bremsstrahlung Z_eff (manual Eq. 68) from the declared material's atom fractions.
        let brems_z_eff = spec
            .photon
            .bremsstrahlung
            .as_ref()
            .map(|_| photon::material_z_eff(&bulk_inv, &cdiag.unknown));
        let mut photon_diagnostics: Vec<PhotonDiagnostics> = Vec::new();
        let mut steps = Vec::new();
        let mut damage_cumulative: f64 = 0.0;
        let mut damage_cumulative_elements: BTreeMap<String, f64> = BTreeMap::new();
        let mut t_cum = Seconds::new(0.0).expect("zero seconds is valid");
        let mut flux_weighted_time_cum = Seconds::new(0.0).expect("zero seconds is valid");
        let mut fluence_cum = 0.0f64;
        let base_flux_total = physical.flux.total();
        let bulk_activity: BTreeMap<String, f64> = bulk_photon_active
            .iter()
            .map(|(name, _, activity)| (name.clone(), *activity))
            .collect();
        // P127: reservoirs the routes analysis starts from (trace mode bulk nuclides, then the schedule's feed) and
        // the per-step graphs recorded from the matrices the solve assembles
        let mut route_reservoir_states: Vec<Option<usize>> = Vec::new();
        if want_routes {
            if mode == "trace" {
                let mut states: Vec<usize> = bulk.keys().copied().collect();
                states.sort_unstable();
                route_reservoir_states.extend(states.into_iter().map(Some));
            }
            if has_feed {
                route_reservoir_states.push(None);
            }
        }
        let route_index_of: HashMap<usize, usize> = route_reservoir_states
            .iter()
            .enumerate()
            .filter_map(|(p, state)| state.map(|c| (c, p)))
            .collect();
        let route_feed_index = route_reservoir_states
            .iter()
            .position(|state| state.is_none());
        let mut route_steps: Vec<crate::pathways::StepGraph> = Vec::new();
        // ---- P125 fission count (opt-in). The neutron-induced and spontaneous counts are two
        // extra states of a throwaway copy of each step's matrix, so each step's integral is exact
        // in the same CRAM sense as the inventory and the nominal solve is untouched.
        let fission_count_options = spec.options.fission.as_ref();
        let sf_nubar = self
            .spontaneous
            .as_ref()
            .filter(|prepared| !prepared.nubar.is_empty());
        let sf_decay_eff: Vec<(usize, f64)> = ch
            .ledger
            .sf_decay
            .iter()
            .map(|&(column, rate)| {
                let factor = decay_factors.get(&ch.keys[column]).copied().unwrap_or(1.0);
                (column, rate * factor)
            })
            .collect();
        // Counter edges take the same trace form as the reaction and decay edges: a reservoir
        // nuclide feeds the counter through the unit state, and its tracked population directly.
        let counter_edges = |rates: &[(usize, f64)]| -> Vec<(usize, f64)> {
            let mut edges = Vec::new();
            for &(column, rate) in rates {
                if mode == "trace" && bulk.contains_key(&column) {
                    if pos[ch.unit] != usize::MAX {
                        edges.push((pos[ch.unit], rate * bulk[&column]));
                    }
                    if tracked_reservoir.contains(&column) && pos[column] != usize::MAX {
                        edges.push((pos[column], rate));
                    }
                } else if pos[column] != usize::MAX {
                    edges.push((pos[column], rate));
                }
            }
            edges
        };
        // Index 0 is the base spectrum, then one entry per distinct step-spectrum override.
        let induced_edges: Vec<Vec<(usize, f64)>> = if fission_count_options.is_some() {
            std::iter::once(&led.fission_rate_per_atom)
                .chain(fission_rates_extra.iter())
                .map(|rates| {
                    let rates: Vec<(usize, f64)> = rates
                        .iter()
                        .map(|(&column, &rate)| (column, rate))
                        .collect();
                    counter_edges(&rates)
                })
                .collect()
        } else {
            Vec::new()
        };
        let spontaneous_edges = if fission_count_options.is_some() {
            counter_edges(&sf_decay_eff)
        } else {
            Vec::new()
        };
        let heavy_metal = fission_count::heavy_metal(&bulk_inv, |key| {
            nuclides
                .get(&key)
                .map(|nuclide| nuclide.awr)
                .filter(|awr| *awr > 0.0)
        });
        let mut induced_cum = 0.0f64;
        let mut spontaneous_cum = 0.0f64;
        // One step's matrix triplets: decay, the step spectrum's reactions scaled by the flux
        // multiplier, and the declaring step's feed/removal terms (not flux-scaled).
        let assemble_triplets =
            |si: usize, rs: &[(usize, usize, C64)], multiplier: f64| -> Vec<(usize, usize, C64)> {
                let mut trip = dsub.clone();
                if multiplier > 0.0 {
                    for (i, j, v) in rs.iter() {
                        trip.push((*i, *j, v * C64::new(multiplier, 0.0)));
                    }
                }
                for edges in [&step_feed[si], &step_removal[si]] {
                    for &(i, j, v) in edges {
                        if pos[i] != usize::MAX && pos[j] != usize::MAX {
                            trip.push((pos[i], pos[j], C64::new(v, 0.0)));
                        }
                    }
                }
                trip
            };
        let assemble = |si: usize, rs: &[(usize, usize, C64)], multiplier: f64| -> Csc {
            Csc::from_triplets(m, &assemble_triplets(si, rs, multiplier))
        };
        // Multiplier each step actually ran at: the schedule's for flux-driven steps, the
        // time-weighted mean of the solved sub-intervals for power-driven ones.
        let mut achieved_multiplier: Vec<f64> =
            sched.iter().map(|step| step.multiplier().get()).collect();
        let mut partition_records: Vec<serde_json::Value> = Vec::new();
        for (si, schedule_step) in sched.iter().enumerate() {
            let dt = schedule_step.duration();
            let scheduled_fl = schedule_step.multiplier();
            // ---- PARTITION (P124, FISPACT-II section 4.54): an instantaneous split before the
            // step evolves. Each named element keeps the stated fraction of its atoms in every
            // tracked state; the rest goes to the removal sink. The operator is linear and
            // parameter-independent, so tangents and the comparison solution take it unchanged.
            if let Some(sink) = removed.filter(|_| !schedule_step.partition.is_empty()) {
                let sink_position = pos[sink];
                for &(z, kept) in &schedule_step.partition {
                    let members: Vec<usize> = keep
                        .iter()
                        .enumerate()
                        .filter(|&(_, &g)| g < ch.keys.len() && ch.keys[g].0 / 1000 == z)
                        .map(|(k, _)| k)
                        .collect();
                    let removed_atoms = partition_move(&mut y, &members, sink_position, kept);
                    if let Some(runtime) = uncertainty_runtime.as_mut() {
                        for tangent in runtime.tangents.iter_mut() {
                            partition_move(tangent, &members, sink_position, kept);
                        }
                        partition_move(&mut runtime.alternate_y, &members, sink_position, kept);
                    }
                    partition_records.push(serde_json::json!({
                        "step": si + 1,
                        "element": composition::symbol_of(z),
                        "fraction_kept": kept,
                        "removed_atoms_per_g": removed_atoms,
                    }));
                }
            }
            // the step's spectrum (index into rsubs: 0 = base, 1.. = overrides)
            let rs = match step_spectrum_id[si] {
                Some(id) => &rsubs[id + 1],
                None => &rsubs[0],
            };
            // atoms per gram of chain state g in `state`: tracked states directly, a trace-mode
            // reservoir nuclide as its constant bulk plus any tracked delta (the gas and damage
            // folds read inventories the same way)
            let atoms_of = |g: usize, state: &[f64]| -> f64 {
                let tracked = if pos[g] != usize::MAX {
                    state[pos[g]]
                } else {
                    0.0
                };
                if mode == "coupled" {
                    tracked
                } else {
                    match bulk.get(&g) {
                        Some(reservoir) => {
                            reservoir
                                + if tracked_reservoir.contains(&g) {
                                    tracked
                                } else {
                                    0.0
                                }
                        }
                        None => tracked,
                    }
                }
            };
            let mut power_out: Option<PowerStepOut> = None;
            let mut power_zeroed = 0.0f64;
            let mut power_last_multiplier: Option<f64> = None;
            let fl = match (schedule_step.power_w_per_g, kerma_loaded.as_ref()) {
                (Some(target), Some(loaded)) => {
                    // P126: split the step into equal sub-intervals and renormalize the flux at
                    // the start of each from the composition then reached.
                    let terms = &kerma_terms[step_spectrum_id[si].map_or(0, |id| id + 1)];
                    let heating = |state: &[f64]| -> f64 {
                        terms
                            .iter()
                            .map(|term| term.watts_per_atom * atoms_of(term.state, state))
                            .sum()
                    };
                    let propagate = |state: &[f64],
                                     multiplier: f64,
                                     h: f64|
                     -> Result<(Vec<f64>, f64), String> {
                        let a = assemble(si, rs, multiplier);
                        let mut next = cram_step(&a, state, h, &c)?.0;
                        let mut clipped = 0.0f64;
                        for (k, v) in next.iter_mut().enumerate() {
                            if *v < 0.0
                                && keep[k] != ch.leak
                                && keep[k] != ch.unit
                                && Some(keep[k]) != removed
                            {
                                clipped += -*v;
                                *v = 0.0;
                            }
                        }
                        Ok((next, clipped))
                    };
                    let solved = power::solve_power_step(
                        &y,
                        dt.get(),
                        target,
                        loaded.options.power_tolerance,
                        loaded.options.max_substeps,
                        heating,
                        propagate,
                    )
                    .map_err(|error| format!("schedule step {}: {error}", si + 1))?;
                    let unit_spectrum_total: f64 = match step_spectrum_id[si] {
                        Some(id) => extra_spectra[id].iter().sum(),
                        None => base_flux_total.get(),
                    };
                    let mean_multiplier = solved.mean_multiplier();
                    power_out = Some(PowerStepOut::new(
                        &solved,
                        target,
                        spec.schedule[si].power_W_cm3,
                        loaded.options.power_tolerance,
                        loaded.options.max_substeps,
                        estimated_multiplier[si].unwrap_or(0.0),
                        unit_spectrum_total,
                    ));
                    power_zeroed = solved.zeroed_atoms;
                    power_last_multiplier = solved.multipliers.last().copied();
                    y = solved.state;
                    achieved_multiplier[si] = mean_multiplier;
                    FluxMultiplier::new(mean_multiplier)
                        .map_err(|error| format!("schedule step {}: {error}", si + 1))?
                }
                _ => scheduled_fl,
            };
            let trip = assemble_triplets(si, rs, fl.get());
            let a = Csc::from_triplets(m, &trip);
            if want_routes {
                // reservoir supply split by origin: (row position, reservoir, atoms g^-1 s^-1)
                let mut supply: Vec<(usize, usize, f64)> = Vec::new();
                for &(row, state, rate) in &unit_origin_decay {
                    if let (true, Some(&p)) = (pos[row] != usize::MAX, route_index_of.get(&state)) {
                        supply.push((pos[row], p, rate));
                    }
                }
                if fl.get() > 0.0 {
                    let slot = step_spectrum_id[si].map_or(0, |id| id + 1);
                    let origins = unit_origin_react
                        .get(slot)
                        .map_or(&[][..], |origins| origins.as_slice());
                    for &(row, state, rate) in origins {
                        if let (true, Some(&p)) =
                            (pos[row] != usize::MAX, route_index_of.get(&state))
                        {
                            supply.push((pos[row], p, rate * fl.get()));
                        }
                    }
                }
                if let Some(p) = route_feed_index {
                    for &(state, _, rate) in &step_feed[si] {
                        if pos[state] != usize::MAX {
                            supply.push((pos[state], p, rate));
                        }
                    }
                }
                route_steps.push(crate::pathways::StepGraph::from_matrix(
                    dt.get(),
                    &trip,
                    pos[ch.unit],
                    supply,
                ));
            }
            if fission_count_options.is_some() {
                // Fissions in this step: the two counters accumulate edge-rate times population
                // over the step from the pre-step state, in a solve that only they read.
                let mut augmented = trip.clone();
                if fl.get() > 0.0 {
                    let spectrum_index = step_spectrum_id[si].map_or(0, |id| id + 1);
                    for &(column, rate) in &induced_edges[spectrum_index] {
                        augmented.push((m, column, C64::new(rate * fl.get(), 0.0)));
                    }
                }
                for &(column, rate) in &spontaneous_edges {
                    augmented.push((m + 1, column, C64::new(rate, 0.0)));
                }
                let augmented_matrix = Csc::from_triplets(m + 2, &augmented);
                let mut counted = y.clone();
                counted.extend([0.0, 0.0]);
                let counted = cram_step(&augmented_matrix, &counted, dt.get(), &c)?.0;
                induced_cum += counted[m].max(0.0);
                spontaneous_cum += counted[m + 1].max(0.0);
            }
            if let Some(runtime) = uncertainty_runtime.as_mut() {
                // Each (spectrum, parameter) direction acts only on steps that
                // use its own spectrum — index 0 is the base spectrum — and
                // carries the flux multiplier there; spectrum-independent
                // directions (decay constants) act unscaled on every step.
                let step_spectrum = step_spectrum_id[si].map_or(0, |id| id + 1);
                let direction_scales: Vec<f64> = runtime
                    .parameter_spectrum
                    .iter()
                    .map(|tag| match tag {
                        None => 1.0,
                        Some(s) if *s == step_spectrum => fl.get(),
                        Some(_) => 0.0,
                    })
                    .collect();
                if std::env::var_os("ACTINV_TRACE").is_some() {
                    eprintln!("ACTINV_TRACE step={si} tangent_step_start");
                }
                let tangent_step = step_with_tangents(
                    &a,
                    &y,
                    &runtime.tangents,
                    &runtime.directions,
                    &direction_scales,
                    dt.get(),
                    &c,
                )?;
                if std::env::var_os("ACTINV_TRACE").is_some() {
                    eprintln!("ACTINV_TRACE step={si} tangent_step_done");
                }
                y = tangent_step.state;
                runtime.tangents = tangent_step.tangents;
                runtime.alternate_y = cram_step(
                    &a,
                    &runtime.alternate_y,
                    dt.get(),
                    alternate_c.as_ref().expect("alternate CRAM is prepared"),
                )?
                .0;
            } else if power_out.is_none() {
                y = cram_step(&a, &y, dt.get(), &c)?.0;
            }
            t_cum += dt;
            flux_weighted_time_cum += dt * fl;
            // physical fluence uses the step's own spectrum total
            let step_flux_total: f64 = match step_spectrum_id[si] {
                Some(id) => extra_spectra[id].iter().sum(),
                None => base_flux_total.get(),
            };
            fluence_cum += dt.get() * fl.get() * step_flux_total;
            let mut zeroed = power_zeroed;
            for (k, v) in y.iter_mut().enumerate() {
                if *v < 0.0 && keep[k] != ch.leak && keep[k] != ch.unit && Some(keep[k]) != removed
                {
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
                    if *value < 0.0
                        && keep[k] != ch.leak
                        && keep[k] != ch.unit
                        && Some(keep[k]) != removed
                    {
                        *value = 0.0;
                    }
                }
            }
            // Legacy CRAM asymptotic scale only, not a bound on matrix/solve roundoff.
            // Below-scale populations and their heat subtotal are reported, not removed.
            let nmax = y.iter().cloned().fold(0.0f64, f64::max);
            let floor = c.alpha0 * nmax;
            let (mut n_below, mut atoms_below, mut heat_below) = (0usize, 0.0, 0.0);
            for (k, v) in y.iter().enumerate() {
                let g = keep[k];
                if g == ch.leak || g == ch.unit || Some(g) == removed || *v <= 0.0 || *v >= floor {
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
                if g == ch.leak || g == ch.unit || Some(g) == removed || *v <= 0.0 {
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
                    // a hybrid reservoir nuclide (trace mode) already carries its constant bulk
                    // activity here; its tracked population adds to it rather than replacing it
                    *act.entry(nm.clone()).or_insert(0.0) += l * v;
                    match photon_active.iter_mut().find(|entry| entry.1 == key) {
                        Some(entry) => entry.2 += l * v,
                        None => photon_active.push((nm, key, l * v)),
                    }
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
            // ---- P92 gas: per-species inventory, production and appm. Mirrors the damage
            // section's composition-resolved-atoms idiom below: coupled mode reads the tracked
            // state directly, trace mode adds any tracked delta onto a bulk reservoir's constant
            // baseline (or, for the common case of a non-bulk gas product fed through the unit
            // source, reads the tracked state directly too).
            let gas_out = if spec.options.gas {
                let initial_atoms_per_g: f64 = bulk_inv.values().sum();
                let gas_atoms = |za: (i32, i32)| -> f64 {
                    let mut atoms = bulk_inv.get(&za).copied().unwrap_or(0.0);
                    if let Some(&g) = ch.index.get(&za) {
                        if pos[g] != usize::MAX {
                            if mode == "coupled" {
                                atoms = y[pos[g]];
                            } else if tracked_reservoir.contains(&g) {
                                atoms += y[pos[g]];
                            } else if !bulk.contains_key(&g) {
                                atoms = y[pos[g]];
                            }
                        }
                    }
                    atoms
                };
                let species = |za: (i32, i32)| -> GasSpecies {
                    let atoms_per_g = gas_atoms(za);
                    let initial = bulk_inv.get(&za).copied().unwrap_or(0.0);
                    let produced_atoms_per_g = atoms_per_g - initial;
                    let (appm, inventory_appm) = if initial_atoms_per_g > 0.0 {
                        (
                            produced_atoms_per_g / initial_atoms_per_g * 1e6,
                            atoms_per_g / initial_atoms_per_g * 1e6,
                        )
                    } else {
                        (0.0, 0.0)
                    };
                    GasSpecies {
                        atoms_per_g,
                        produced_atoms_per_g,
                        appm,
                        inventory_appm,
                    }
                };
                let h1 = species(gas::H1);
                let h2 = species(gas::H2);
                let h3 = species(gas::H3);
                let he3 = species(gas::HE3);
                let he4 = species(gas::HE4);
                let h_appm = h1.appm + h2.appm + h3.appm;
                let he_appm = he3.appm + he4.appm;
                let h_inventory_appm = h1.inventory_appm + h2.inventory_appm + h3.inventory_appm;
                let he_inventory_appm = he3.inventory_appm + he4.inventory_appm;
                Some(GasOut {
                    H1: h1,
                    H2: h2,
                    H3: h3,
                    He3: he3,
                    He4: he4,
                    H_appm: h_appm,
                    He_appm: he_appm,
                    H_inventory_appm: h_inventory_appm,
                    He_inventory_appm: he_inventory_appm,
                    initial_atoms_per_g,
                })
            } else {
                None
            };
            // ---- P122 step summary: per-state atoms follow the gas rule above (coupled reads the
            // tracked state; trace adds a tracked reservoir's population onto its constant bulk
            // atoms, reads non-bulk states directly and leaves other bulk states at their constant
            // atoms). The leakage, unit and removal sinks are not nuclide states, so `ch.keys`
            // excludes them by construction; bulk nuclides absent from the chain keep their bulk atoms.
            let mut step_summary = match &spec.summary {
                Some(options) => {
                    let state_atoms = |g: usize, key: (i32, i32)| -> f64 {
                        let mut atoms = bulk_inv.get(&key).copied().unwrap_or(0.0);
                        if pos[g] != usize::MAX {
                            if mode == "coupled" {
                                atoms = y[pos[g]];
                            } else if tracked_reservoir.contains(&g) {
                                atoms += y[pos[g]];
                            } else if !bulk.contains_key(&g) {
                                atoms = y[pos[g]];
                            }
                        }
                        atoms
                    };
                    let initial =
                        |key: &(i32, i32)| bulk_inv.get(key).is_some_and(|atoms| *atoms > 0.0);
                    let mut states: Vec<StateAtoms> = ch
                        .keys
                        .iter()
                        .enumerate()
                        .map(|(g, &key)| StateAtoms {
                            key,
                            atoms: state_atoms(g, key),
                            initial: initial(&key),
                        })
                        .collect();
                    states.extend(
                        bulk_inv
                            .iter()
                            .filter(|(key, _)| !ch.index.contains_key(*key))
                            .map(|(&key, &atoms)| StateAtoms {
                                key,
                                atoms,
                                initial: initial(&key),
                            }),
                    );
                    let leakage_atoms = if pos[ch.leak] != usize::MAX {
                        y.get(pos[ch.leak]).copied().unwrap_or(0.0)
                    } else {
                        0.0
                    };
                    Some(summary::build(
                        options,
                        nuclides,
                        &states,
                        leakage_atoms,
                        spec.material.mass_g,
                        spec.material.density_g_cm3,
                    ))
                }
                None => None,
            };
            if let (Some(tracker), Some(summary)) = (depletion.as_mut(), step_summary.as_mut()) {
                let slot = step_spectrum_id[si].map_or(0, |id| id + 1);
                summary.depletion =
                    Some(tracker.step(slot, fl.get(), dt.get(), &|column| atoms_of(column, &y)));
            }
            let uncertainty = match (&uncertainty_runtime, &spec.uncertainty) {
                (Some(runtime), Some(options)) => {
                    // P72/P77: a declared unmodeled source resolves to the
                    // prepared unmodeled term — the band emit sees it as
                    // `unmodeled_relative` exactly as if the spec declared
                    // it, with provenance on `certificate.inputs`.
                    let options_resolved;
                    let resolved_u = self
                        .unmodeled_table
                        .as_ref()
                        .and_then(|t| t["resolved_unmodeled_relative"].as_f64())
                        .or_else(|| {
                            self.unmodeled_evalspread
                                .as_ref()
                                .and_then(|t| t["resolved_unmodeled_relative"].as_f64())
                        });
                    let options = match resolved_u {
                        Some(u) => {
                            options_resolved = UncertaintyOptions {
                                unmodeled_relative: Some(u),
                                ..options.clone()
                            };
                            &options_resolved
                        }
                        None => options,
                    };
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
                    if std::env::var_os("ACTINV_TRACE").is_some() {
                        eprintln!("ACTINV_TRACE step={si} unc_emit_start");
                    }
                    let built = Some(build_step_uncertainty(
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
                    )?);
                    if std::env::var_os("ACTINV_TRACE").is_some() {
                        eprintln!("ACTINV_TRACE step={si} unc_emit_done");
                    }
                    built
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
            let damage = match (&self.damage, &damage_plan) {
                (Some(prepared), Some(plan)) => {
                    // composition-resolved target atoms: the constant reservoir in trace mode
                    // (plus any explicitly fed tracked population), evolved states in coupled mode
                    let mut atoms = bulk_inv.clone();
                    for (&key, target_atoms) in atoms.iter_mut() {
                        match ch.index.get(&key) {
                            Some(&g) if pos[g] != usize::MAX => {
                                if mode == "coupled" {
                                    *target_atoms = y[pos[g]];
                                } else if tracked_reservoir.contains(&g) {
                                    *target_atoms += y[pos[g]];
                                }
                            }
                            _ => {}
                        }
                    }
                    let mut out = prepared.table.fold(plan, &atoms, phi, fl.get());
                    damage_cumulative += out.dpa_rate_per_s * dt.get();
                    out.dpa = damage_cumulative;
                    for (name, element) in out.elements.iter_mut() {
                        let cumulative =
                            damage_cumulative_elements.entry(name.clone()).or_default();
                        *cumulative += element.dpa_rate_per_s * dt.get();
                        element.dpa = *cumulative;
                    }
                    Some(out)
                }
                _ => None,
            };
            let kerma_out = kerma_loaded.as_ref().map(|loaded| {
                let terms = &kerma_terms[step_spectrum_id[si].map_or(0, |id| id + 1)];
                let multiplier = power_last_multiplier.unwrap_or(fl.get());
                let mut contributions = Vec::new();
                let mut covered_atoms = 0.0f64;
                for term in terms {
                    let atoms = atoms_of(term.state, &y);
                    if atoms > 0.0 {
                        covered_atoms += atoms;
                        contributions.push((
                            term.key,
                            atoms,
                            multiplier * term.watts_per_atom * atoms,
                        ));
                    }
                }
                let total_atoms: f64 = (0..ch.keys.len())
                    .map(|g| atoms_of(g, &y))
                    .filter(|atoms| *atoms > 0.0)
                    .sum();
                let damage_energy = loaded.table.has_damage_energy().then(|| {
                    multiplier
                        * terms
                            .iter()
                            .filter_map(|term| {
                                term.damage_eV_per_s_per_atom
                                    .map(|damage| damage * atoms_of(term.state, &y))
                            })
                            .sum::<f64>()
                });
                summarize_kerma(
                    &contributions,
                    covered_atoms,
                    total_atoms,
                    loaded.options.top,
                    multiplier,
                    spec.material.density_g_cm3,
                    damage_energy,
                )
            });
            let photon_source = if want_photons {
                let active_refs: Vec<_> = photon_active
                    .iter()
                    .filter_map(|(name, key, activity)| {
                        nuclides.get(key).map(|nu| (name.as_str(), nu, *activity))
                    })
                    .collect();
                let (mut source, diagnostics) = photon::source_for_step(
                    &active_refs,
                    photon_boundaries,
                    &spec.photon.group_structure,
                    physical.mass.get(),
                    response.as_ref(),
                    &material_mass_fractions,
                    material_response_complete,
                    spec.photon.build_up_factor,
                    physical.gamma_cutoff.get(),
                )?;
                // Opt-in components, added after the decay-photon source and never merged into it.
                // Bremsstrahlung first, so the point-source dose can also cover it.
                if let (Some(brems), Some(z_eff)) = (&spec.photon.bremsstrahlung, &brems_z_eff) {
                    photon::add_bremsstrahlung(
                        &mut source,
                        &active_refs,
                        brems,
                        z_eff,
                        physical.mass.get(),
                        response.as_ref(),
                        &material_mass_fractions,
                        material_response_complete,
                        spec.photon.build_up_factor,
                    );
                }
                if let Some(point) = &spec.photon.point_source {
                    photon::add_point_source_dose(
                        &mut source,
                        response.as_ref(),
                        point,
                        physical.mass.get(),
                    );
                }
                photon_diagnostics.push(diagnostics);
                Some(source)
            } else {
                None
            };
            // ---- P125 fission count, burn-up and spontaneous-fission neutron source.
            let fission_out = if fission_count_options.is_some() || sf_nubar.is_some() {
                let count = fission_count_options.map(|options| {
                    fission_count::fission_count(
                        induced_cum,
                        spontaneous_cum,
                        &heavy_metal,
                        options.energy_per_fission_MeV,
                    )
                });
                let spontaneous_neutron_source = sf_nubar.map(|prepared| {
                    // Populated atoms of each SF state: the constant reservoir in trace mode plus
                    // any tracked population, the evolved state alone in coupled mode.
                    let decays: Vec<((i32, i32), f64)> = sf_decay_eff
                        .iter()
                        .map(|&(column, rate)| {
                            let mut atoms = 0.0;
                            if mode == "trace" {
                                atoms += bulk.get(&column).copied().unwrap_or(0.0);
                            }
                            if pos[column] != usize::MAX {
                                atoms += y[pos[column]].max(0.0);
                            }
                            (ch.keys[column], rate * atoms)
                        })
                        .collect();
                    fission_count::sf_neutron_source(&decays, &prepared.nubar)
                });
                Some(FissionStepOut {
                    count,
                    spontaneous_neutron_source,
                })
            } else {
                None
            };
            steps.push(StepOut {
                step: si + 1,
                t_s: t_cum.get(),
                flux: fl.get(),
                flux_weighted_time_s: flux_weighted_time_cum.get(),
                fluence_n_cm2: spec.projectile.is_neutron().then_some(fluence_cum),
                fluence_particles_cm2: (!spec.projectile.is_neutron()).then_some(fluence_cum),
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
                removed_atoms_per_g: removed.map(|sink| {
                    if pos[sink] != usize::MAX {
                        y.get(pos[sink]).copied().unwrap_or(0.0)
                    } else {
                        0.0
                    }
                }),
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
                damage,
                gas: gas_out,
                summary: step_summary,
                fission: fission_out,
                kerma: kerma_out,
                power: power_out,
            });
        }
        profiler.finish("schedule_solve_diagnostics", solve_started);
        if std::env::var_os("ACTINV_TRACE").is_some() {
            eprintln!("ACTINV_TRACE solve_done");
        }

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
        // Feed/removal boundary sources and sinks are not production chains: when a schedule
        // declares them, pathway attribution is suppressed rather than silently mis-attributed.
        // A schedule that switches library is suppressed the same way: the pathway solve below
        // carries the base library's rates only.
        let pathways_suppressed =
            want_paths && mode == "trace" && (has_feed || has_removal || has_step_library);
        if mode == "trace"
            && want_paths
            && sources.iter().any(|list| !list.is_empty())
            && !pathways_suppressed
        {
            // one unit state per (row fed, bulk nuclide), carrying its rate under every spectrum
            let mut agg: BTreeMap<(usize, (i32, i32)), Vec<f64>> = BTreeMap::new();
            for (spectrum, list) in sources.iter().enumerate() {
                for (row, rate, from) in list {
                    if pos[*row] != usize::MAX {
                        agg.entry((*row, *from))
                            .or_insert_with(|| vec![0.0; sources.len()])[spectrum] += rate;
                    }
                }
            }
            let labels: Vec<_> = agg.into_iter().collect();
            let ns = labels.len();
            let mp = m + ns; // one extra unit state per source
            let ptrip: Vec<(usize, usize, C64)> = dsub
                .iter()
                .filter(|(i, j, _)| *i != pos[ch.unit] && *j != pos[ch.unit])
                .cloned()
                .collect();
            let mut cols: Vec<Vec<f64>> = (0..ns)
                .map(|k| {
                    let mut v = vec![0.0f64; mp];
                    v[m + k] = 1.0;
                    v
                })
                .collect();
            for (si, schedule_step) in sched.iter().enumerate() {
                // the same matrices the solve used: the step's spectrum, at each multiplier it
                // applied (one per sub-interval for a power-driven step)
                let spectrum = step_spectrum_id[si].map_or(0, |id| id + 1);
                let intervals: Vec<(f64, f64)> = match steps[si].power.as_ref() {
                    Some(power) => power
                        .flux_multiplier_per_substep
                        .iter()
                        .map(|&multiplier| (power.substep_duration_s, multiplier))
                        .collect(),
                    None => vec![(
                        schedule_step.duration().get(),
                        schedule_step.multiplier().get(),
                    )],
                };
                for (h, fl) in intervals {
                    let mut trip = ptrip.clone();
                    if fl > 0.0 {
                        // feeds scale with the multiplier like the solve's unit column
                        for (k, ((row, _), rates)) in labels.iter().enumerate() {
                            trip.push((pos[*row], m + k, C64::new(rates[spectrum] * fl, 0.0)));
                        }
                        for (i, j, v) in rsubs[spectrum].iter() {
                            if *i != pos[ch.unit] && *j != pos[ch.unit] {
                                trip.push((*i, *j, v * C64::new(fl, 0.0)));
                            }
                        }
                    }
                    // during cooling the feeds are absent; each unit state stays exactly 1 so a
                    // later irradiation sources again
                    let a = Csc::from_triplets(mp, &trip);
                    cols = crate::cram::step_multi(&a, &cols, h, &c)?;
                    for (k, col) in cols.iter_mut().enumerate() {
                        for (other, value) in col[m..].iter_mut().enumerate() {
                            *value = if other == k { 1.0 } else { 0.0 };
                        }
                    }
                }
                // report at every step, ranked, keeping chains above 1e-6 of the nuclide
                let mut per: BTreeMap<String, Vec<Pathway>> = BTreeMap::new();
                for (k, col) in cols.iter().enumerate() {
                    let ((row, from), _) = &labels[k];
                    for (idx, v) in col.iter().enumerate().take(m) {
                        if *v <= 0.0 {
                            continue;
                        }
                        let g = keep[idx];
                        if g == ch.leak || g == ch.unit || Some(g) == removed {
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

        // ---- P127 routes: every source-to-target route, integrated exactly over the matrices the solve used
        let pathway_routes = match spec.pathways.as_ref() {
            Some(options) => {
                let routes_started = profiler.start();
                let node_names: Vec<String> = keep
                    .iter()
                    .map(|&g| {
                        if g == ch.leak {
                            "leak".to_owned()
                        } else if g == ch.unit {
                            "unit".to_owned()
                        } else if Some(g) == removed {
                            "removed".to_owned()
                        } else {
                            name_of(ch.keys[g].0, ch.keys[g].1)
                        }
                    })
                    .collect();
                let reservoirs: Vec<String> = route_reservoir_states
                    .iter()
                    .map(|state| match state {
                        Some(c) => name_of(ch.keys[*c].0, ch.keys[*c].1),
                        None => "feed".to_owned(),
                    })
                    .collect();
                // coupled mode starts from the initial nuclides; trace mode starts from its reservoirs
                let initial: Vec<(usize, f64)> = if mode == "coupled" {
                    keep.iter()
                        .enumerate()
                        .filter(|(_, g)| **g != ch.unit && **g != ch.leak && Some(**g) != removed)
                        .filter_map(|(k, g)| (n0[*g] > 0.0).then_some((k, n0[*g])))
                        .collect()
                } else {
                    Vec::new()
                };
                let mut global_labels = crate::pathways::GlobalLabels::new();
                crate::pathways::decay_edge_labels(nuclides, ch, &mut global_labels);
                crate::pathways::reaction_edge_labels(
                    &mut global_labels,
                    lib.rows(),
                    lib_targets,
                    ch,
                    &effective_fission_yields,
                    spec.projectile.is_neutron(),
                );
                let labels = crate::pathways::position_labels(
                    &global_labels,
                    &pos,
                    &route_reservoir_states,
                    m,
                );
                let mut final_atoms: HashMap<String, f64> = HashMap::new();
                let mut activity: HashMap<String, f64> = HashMap::new();
                let mut heat: HashMap<String, f64> = HashMap::new();
                if let Some(last) = steps.last() {
                    for entry in &last.inventory {
                        final_atoms.insert(entry.nuclide.clone(), entry.atoms_per_g);
                        if let Some(nu) = nuclides.get(&(entry.Z * 1000 + entry.A, entry.LISO)) {
                            let decays = nu.lambda() * entry.atoms_per_g;
                            activity.insert(entry.nuclide.clone(), decays);
                            heat.insert(
                                entry.nuclide.clone(),
                                decays * (nu.e_light() + nu.e_em() + nu.e_heavy()),
                            );
                        }
                    }
                }
                let report = crate::pathways::analyse(
                    &crate::pathways::PathwayInput {
                        node_names,
                        reservoirs,
                        steps: route_steps,
                        initial,
                        labels,
                        final_atoms,
                        activity,
                        heat,
                    },
                    options,
                )?;
                profiler.finish("pathway_routes", routes_started);
                Some(
                    serde_json::to_value(&report)
                        .map_err(|error| format!("pathway routes: {error}"))?,
                )
            }
            None => None,
        };

        // P58: per-step pathway aggregation by isomer product, emitted only
        // when the spec requested uncertainty.isomer.
        let isomer_requested = spec
            .uncertainty
            .as_ref()
            .is_some_and(|u| u.isomer.is_some());
        let isomer_pathway_shares = isomer_requested.then(|| {
            let reason = if mode != "trace" {
                "pathway attribution requires trace mode"
            } else if pathways_suppressed {
                "pathway attribution suppressed for feed/removal schedules"
            } else {
                "no pathway attribution emitted for this step"
            };
            steps
                .iter()
                .enumerate()
                .map(|(si, _)| match pathways.get(si) {
                    Some(per) if !per.is_empty() => {
                        let mut iso_total = 0.0;
                        let mut all_total = 0.0;
                        let mut by_product: BTreeMap<String, f64> = BTreeMap::new();
                        for chains in per.values() {
                            for p in chains {
                                all_total += p.atoms_per_g;
                                if is_isomer_name(&p.first_product) {
                                    iso_total += p.atoms_per_g;
                                    *by_product.entry(p.first_product.clone()).or_default() +=
                                        p.atoms_per_g;
                                }
                            }
                        }
                        let mut top: Vec<(String, f64)> = by_product.into_iter().collect();
                        top.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap());
                        top.truncate(10);
                        IsomerPathwayShare {
                            status: "emitted",
                            reason: None,
                            atoms_through_isomer_products_per_g: Some(iso_total),
                            share: (all_total > 0.0).then_some(iso_total / all_total),
                            top_isomer_products: top
                                .into_iter()
                                .map(|(first_product, atoms_per_g)| IsomerPathwayProduct {
                                    first_product,
                                    atoms_per_g,
                                    share_of_isomer_flow: if iso_total > 0.0 {
                                        atoms_per_g / iso_total
                                    } else {
                                        0.0
                                    },
                                })
                                .collect(),
                        }
                    }
                    _ => IsomerPathwayShare {
                        status: "unavailable",
                        reason: Some(reason),
                        atoms_through_isomer_products_per_g: None,
                        share: None,
                        top_isomer_products: Vec::new(),
                    },
                })
                .collect::<Vec<_>>()
        });

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

        // ---- P65 certified screening
        // The prune bound is time-independent (it bounds what the dropped
        // states could ever hold), so one widening applies at every step.
        let screen_block = if let Some(screen) = spec.options.screen.as_ref() {
            let mut removed_activity_bound = 0.0f64;
            let mut activity_bound_by_nuclide: BTreeMap<String, f64> = BTreeMap::new();
            for (index, atoms_bound, _) in &rate_pruned {
                let Some(key) = ch.keys.get(*index) else {
                    continue;
                };
                let Some(nuclide) = nuclides.get(key) else {
                    continue;
                };
                let contribution = nuclide.lambda() * atoms_bound;
                removed_activity_bound += contribution;
                *activity_bound_by_nuclide
                    .entry(name_of(key.0, key.1))
                    .or_default() += contribution;
            }
            let bound_for = |response: &str| -> Option<(f64, f64)> {
                // (lower-bound widening, upper-bound widening); heat's upper
                // also absorbs the below-floor bound at emit time.
                if response.starts_with("heat.") {
                    Some((
                        rate_pruning_heat_bound_W_per_g,
                        rate_pruning_heat_bound_W_per_g,
                    ))
                } else if response == "activity.total" {
                    Some((removed_activity_bound, removed_activity_bound))
                } else if let Some(name) = response.strip_prefix("activity:") {
                    let b = activity_bound_by_nuclide.get(name).copied().unwrap_or(0.0);
                    Some((b, b))
                } else {
                    None
                }
            };
            let mut names: Vec<String> = spec
                .uncertainty
                .as_ref()
                .map(|u| u.responses.clone())
                .unwrap_or_default();
            for base in ["heat.total", "activity.total"] {
                if !names.iter().any(|n| n == base) {
                    names.push(base.into());
                }
            }
            let mut certified: serde_json::Map<String, serde_json::Value> = serde_json::Map::new();
            for (si, step) in steps.iter().enumerate() {
                let mut per_resp = serde_json::Map::new();
                for response in &names {
                    let Some((blo, mut bhi)) = bound_for(response) else {
                        continue;
                    };
                    if response.starts_with("heat.") {
                        bhi += step.heat_bound_from_below_floor_W_per_g;
                    }
                    // Prefer the band's conservative edge; an unbanded run
                    // certifies the nominal — labelled honestly.
                    let (lo, hi, edge) = step
                        .uncertainty
                        .as_ref()
                        .and_then(|u| u.responses.get(response))
                        .map(|r| {
                            (
                                r.conservative_interval[0],
                                r.conservative_interval[1],
                                "conservative",
                            )
                        })
                        .unwrap_or_else(|| {
                            let n = nominal_response(step, response);
                            (n, n, "nominal")
                        });
                    per_resp.insert(
                        response.clone(),
                        serde_json::json!({
                            "edge": edge,
                            "lower": lo - blo,
                            "upper": hi + bhi,
                            "bound_lower": blo,
                            "bound_upper": bhi,
                        }),
                    );
                }
                certified.insert(si.to_string(), serde_json::Value::Object(per_resp));
            }
            Some(serde_json::json!({
                "bmin_atoms_per_g": screen.bmin_atoms_per_g,
                "kept_states": keep.len(),
                "dropped_states": rate_pruned.len(),
                "removed_heat_W_per_g_bound": rate_pruning_heat_bound_W_per_g,
                "removed_activity_Bq_per_g_bound": removed_activity_bound,
                "certified": serde_json::Value::Object(certified),
            }))
        } else {
            None
        };
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
        // a power-driven step's multiplier is solved, not scheduled: report what it ran at
        let flux_weighted_time_s: f64 = if has_power {
            sched
                .iter()
                .enumerate()
                .map(|(si, step)| step.duration().get() * achieved_multiplier[si])
                .sum()
        } else {
            flux_weighted_time_s
        };
        let fluence_total: f64 = sched
            .iter()
            .enumerate()
            .map(|(si, step)| {
                let total_i: f64 = match step_spectrum_id[si] {
                    Some(id) => extra_spectra[id].iter().sum(),
                    None => base_flux_total.get(),
                };
                step.duration().get() * achieved_multiplier[si] * total_i
            })
            .sum();
        let n_step_spectra = step_spectrum_id.iter().filter(|s| s.is_some()).count();
        let schedule_ledger = if spec.projectile.is_neutron() {
            serde_json::json!({
                "segments": sched.len(),
                "flux_weighted_time_s": flux_weighted_time_s,
                "fluence_n_cm2": fluence_total,
                "step_spectra": n_step_spectra,
            })
        } else {
            serde_json::json!({
                "segments": sched.len(),
                "flux_weighted_time_s": flux_weighted_time_s,
                "fluence_particles_cm2": fluence_total,
                "step_spectra": n_step_spectra,
            })
        };
        let decay_overrides_json = serde_json::to_value(decay_overrides_applied).unwrap();
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
                "note": "Legacy alpha0 * max(N) CRAM asymptotic scale, not a bound on total numerical error or floating-point solve error; below-scale populations and their heat subtotal are reported, not removed",
                "is_total_numerical_error_bound": false,
                "worst_heat_bound_fraction": steps.iter().map(|s| if s.heat_W_per_g.total > 0.0 { s.heat_bound_from_below_floor_W_per_g / s.heat_W_per_g.total } else { 0.0 }).fold(0.0f64, f64::max),
                "max_states_below_floor": steps.iter().map(|s| s.n_states_below_floor).max().unwrap_or(0),
            },
            "bulk_background_heat_W_per_g": bulk_heat,
            "photon_spectra": photon_diagnostics,
            "schedule": schedule_ledger,
            "assembly": {"n_bulk_isotopes": bulk.len(), "n_decay_triplets": d_src.len(), "n_reaction_triplets": r_src.len(),
                         "n_library_rows": lib.rows().len(), "n_chain_nuclides": ch.keys.len(), "flux_total": phi.iter().sum::<f64>(),
                         "rate_scale": spec.options.rate_scale.as_ref().map(|m| m.len()).unwrap_or(0),
                         "decay_scale": spec.options.decay_scale.as_ref().map(|m| m.len()).unwrap_or(0),
                         "yield_scale": spec.options.yield_scale.as_ref().map(|m| m.len()).unwrap_or(0)},
        });
        if !spec.projectile.is_neutron() {
            ledger.as_object_mut().expect("ledger is an object").insert(
                "projectile".into(),
                serde_json::Value::String(spec.projectile.name().into()),
            );
        }
        if has_step_library {
            let libraries: Vec<serde_json::Value> = self
                .step_libraries
                .iter()
                .enumerate()
                .map(|(slot, step_library)| {
                    let steps_on: Vec<usize> = sched
                        .iter()
                        .enumerate()
                        .filter(|(_, step)| step.library == Some(slot))
                        .map(|(si, _)| si + 1)
                        .collect();
                    let fluence: f64 = sched
                        .iter()
                        .enumerate()
                        .filter(|(_, step)| step.library == Some(slot))
                        .map(|(si, step)| {
                            let total: f64 = step_spectrum_id[si].map_or_else(
                                || base_flux_total.get(),
                                |id| extra_spectra[id].iter().sum(),
                            );
                            (step.duration() * step.multiplier()).get() * total
                        })
                        .sum();
                    let rates = step_library_ledgers[slot].as_ref();
                    serde_json::json!({
                        "library": step_library.reference.path,
                        "library_sha256": step_library.library_sha,
                        "projectile": step_library.projectile.name(),
                        "group_structure": step_library.group_structure,
                        "steps": steps_on,
                        "fluence_particles_cm2": fluence,
                        "products_no_evaluated_decay_data": rates.map(|ledger| &ledger.products_no_decay_data),
                        "products_unmapped_to_leakage": rates.map(|ledger| &ledger.products_unmapped),
                        "isomer_state_absent_from_decay_library_used_ground": rates.map(|ledger| &ledger.isomer_fell_back_to_ground),
                        "targets_absent_from_decay_library": rates.map(|ledger| {
                            ledger
                                .targets_absent_from_decay_lib
                                .iter()
                                .map(|(z, l)| format!("{z}_{l}"))
                                .collect::<Vec<_>>()
                        }),
                    })
                })
                .collect();
            ledger.as_object_mut().expect("ledger is an object").insert(
                "step_libraries".into(),
                serde_json::json!({
                    "libraries": libraries,
                    "fluence_note": "schedule.fluence_* and steps[].fluence_* accumulate every step's own spectrum total whatever its projectile; the per-library fluence is listed above",
                    "pathways_suppressed": pathways_suppressed.then_some(
                        "schedule library overrides declared; pathway attribution covers the base library only"
                    ),
                }),
            );
        }
        // Attached post-construction — the json! literal is at its
        // recursion cap. Product-chain optical depth bounds the nonlinear
        // correction to linear flux scaling (P70 flux_scale certificate).
        if bulk_decays {
            let fields = ledger.as_object_mut().expect("ledger is an object");
            fields.insert(
                "bulk_decay_fraction_max".into(),
                serde_json::json!(bulk_decay_fraction_max),
            );
            fields.insert(
                "bulk_decay_nuclide".into(),
                serde_json::json!(bulk_decay_nuclide.map(|key| name_of(key.0, key.1))),
            );
        }
        ledger.as_object_mut().expect("ledger is an object").insert(
            "max_product_optical_depth".into(),
            serde_json::json!(product_optical_depth_max),
        );
        ledger.as_object_mut().expect("ledger is an object").insert(
            "max_product_optical_depth_nuclide".into(),
            serde_json::json!(product_optical_depth_nuclide.map(|key| name_of(key.0, key.1))),
        );
        ledger
            .as_object_mut()
            .expect("ledger is an object")
            .insert("decay_overrides_applied".into(), decay_overrides_json);
        // P124 input-parity records; each key is present only when its feature was used, so
        // every other result is unchanged.
        {
            let object = ledger.as_object_mut().expect("ledger is an object");
            if decay_only {
                object.insert("decay_only".into(), serde_json::json!(true));
            }
            if !physical.spectrum_conversions.is_empty() {
                object.insert(
                    "spectrum_conversion".into(),
                    serde_json::json!(physical.spectrum_conversions),
                );
            }
            if !physical.wall_conversions.is_empty() {
                object.insert(
                    "wall_loading".into(),
                    serde_json::json!(physical.wall_conversions),
                );
            }
            if !override_ledger.is_empty() {
                object.insert("overrides".into(), serde_json::json!(override_ledger));
            }
            if want_depletion {
                object.insert(
                    "depletion".into(),
                    serde_json::json!({
                        "requested": true,
                        "model": if mode == "trace" { "trace_analytic" } else { "coupled" },
                        "nuclides": depletion_columns.len(),
                        "initial_nuclides_absent_from_decay_library":
                            absent.iter().map(|(z, l)| format!("{z}_{l}")).collect::<Vec<_>>(),
                        "rate_source": "the solver's collapsed loss rows times the flux multiplier",
                        "uncertainty": if depletion_with_covariance { "mf33" } else { "none" },
                    }),
                );
            }
            if has_partition {
                object.insert(
                    "partition".into(),
                    serde_json::json!({
                        "applied": partition_records,
                        "mode": mode,
                        "mode_forced_coupled": spec.options.mode == "auto",
                        "removed_atoms_per_g_final": steps
                            .last()
                            .and_then(|step| step.removed_atoms_per_g),
                    }),
                );
            }
        }
        // Present only for a decay library with inconsistent branching, so runs on consistent data are unchanged.
        if !ch.ledger.branching_sums.is_empty() {
            let sums: BTreeMap<String, f64> = ch
                .ledger
                .branching_sums
                .iter()
                .map(|((za, liso), sum)| (name_of(*za, *liso), *sum))
                .collect();
            ledger.as_object_mut().expect("ledger is an object").insert(
                "decay_branching_sums_off_unity".into(),
                serde_json::json!(sums),
            );
        }
        if has_feed || has_removal {
            let mut fed_atoms_per_g: BTreeMap<String, f64> = BTreeMap::new();
            for step in sched.iter() {
                for ((za, liso), rate) in &step.feed {
                    *fed_atoms_per_g.entry(name_of(*za, *liso)).or_insert(0.0) +=
                        rate * step.duration().get();
                }
            }
            let removal_declared: Vec<_> = sched
                .iter()
                .enumerate()
                .flat_map(|(si, step)| {
                    step.removal.iter().map(move |(selector, rate)| {
                        let selected = match selector {
                            crate::spec::RemovalSelector::Nuclide(za, liso) => {
                                vec![name_of(*za, *liso)]
                            }
                            crate::spec::RemovalSelector::Element(z) => ch
                                .keys
                                .iter()
                                .filter(|key| key.0 / 1000 == *z)
                                .map(|key| name_of(key.0, key.1))
                                .collect(),
                        };
                        serde_json::json!({
                            "step": si + 1,
                            "rate_per_s": rate,
                            "selected_states": selected,
                        })
                    })
                })
                .collect();
            ledger.as_object_mut().expect("ledger is an object").insert(
                "feed_removal".into(),
                serde_json::json!({
                    "feed_atoms_per_g_total": fed_atoms_per_g,
                    "removal_declared": removal_declared,
                    "removal_reservoir_exempt": removal_reservoir_exempt,
                    "removed_atoms_per_g_final": steps
                        .last()
                        .and_then(|step| step.removed_atoms_per_g),
                    "pathways_suppressed": pathways_suppressed.then_some(
                        "feed/removal declared; pathway attribution covers production chains only"
                    ),
                }),
            );
        }
        // ---- P61 completeness audit: consolidate the chain/defect ledgers into
        // one ranked verdict. Rates are per-parent coefficients (s^-1 per parent
        // atom); each channel's denominator is its own summed triplet rates.
        // Opt-in only — emitted solely when outputs explicitly lists "audit".
        if spec
            .options
            .outputs
            .as_ref()
            .is_some_and(|o| o.iter().any(|x| x == "audit"))
        {
            // Channel denominators count only positive-rate triplets — the
            // inflow through the channel. Diagonal outflow terms (−λ) sit in
            // the same vectors and would cancel the production measure.
            let total_react: f64 = r_src.iter().map(|(_, _, r)| *r).filter(|r| *r > 0.0).sum();
            let total_decay: f64 = d_src.iter().map(|(_, _, r)| *r).filter(|r| *r > 0.0).sum();
            let fraction = |rate: f64, denom: f64| -> Option<f64> {
                if denom.is_finite() && denom > 0.0 && rate.is_finite() {
                    Some(rate / denom)
                } else {
                    None
                }
            };
            let largest = |map: &BTreeMap<String, f64>| -> Vec<serde_json::Value> {
                let mut v: Vec<(&String, &f64)> = map.iter().collect();
                v.sort_by(|a, b| b.1.total_cmp(a.1));
                v.truncate(3);
                v.iter()
                    .map(|(k, r)| serde_json::json!({"name": k, "rate_coefficient_per_s": r}))
                    .collect()
            };
            let mut reaction_defects = Vec::new();
            for (class, map) in [
                (
                    "products_no_evaluated_decay_data",
                    &led.products_no_decay_data,
                ),
                ("products_unmapped_to_leakage", &led.products_unmapped),
                (
                    "isomer_fell_back_to_ground",
                    &led.isomer_fell_back_to_ground,
                ),
                ("fission_no_yields_to_leakage", &led.fission_no_yields),
            ] {
                if map.is_empty() {
                    continue;
                }
                let rate: f64 = map.values().sum();
                reaction_defects.push(serde_json::json!({
                    "class": class,
                    "instances": map.len(),
                    "rate_coefficient_per_s": rate,
                    "fraction_of_channel_flow": fraction(rate, total_react),
                    "largest": largest(map),
                }));
            }
            if !led.fission_product_leakage.is_empty() {
                let rate: f64 = led
                    .fission_product_leakage
                    .iter()
                    .map(|f| f.production_rate_per_parent_s)
                    .sum();
                let mut by_product: BTreeMap<String, f64> = BTreeMap::new();
                for f in &led.fission_product_leakage {
                    *by_product.entry(f.product.clone()).or_insert(0.0) +=
                        f.production_rate_per_parent_s;
                }
                reaction_defects.push(serde_json::json!({
                    "class": "fission_yield_products_to_leakage",
                    "instances": led.fission_product_leakage.len(),
                    "rate_coefficient_per_s": rate,
                    "fraction_of_channel_flow": fraction(rate, total_react),
                    "largest": largest(&by_product),
                }));
            }
            let mut decay_defects = Vec::new();
            if !ch.ledger.daughters_missing.is_empty() {
                let rate: f64 = ch.ledger.daughters_missing.iter().map(|d| d.2).sum();
                let mut by_name: BTreeMap<String, f64> = BTreeMap::new();
                for (za, liso, r) in &ch.ledger.daughters_missing {
                    *by_name.entry(name_of(*za, *liso)).or_insert(0.0) += *r;
                }
                decay_defects.push(serde_json::json!({
                    "class": "decay_daughters_missing",
                    "instances": ch.ledger.daughters_missing.len(),
                    "rate_coefficient_per_s": rate,
                    "fraction_of_channel_flow": fraction(rate, total_decay),
                    "largest": largest(&by_name),
                }));
            }
            if !ch.ledger.branching_sums.is_empty() {
                let worst = ch
                    .ledger
                    .branching_sums
                    .iter()
                    .map(|((za, liso), s)| {
                        serde_json::json!({
                            "nuclide": name_of(*za, *liso),
                            "branching_sum": s,
                        })
                    })
                    .collect::<Vec<_>>();
                decay_defects.push(serde_json::json!({
                    "class": "decay_branching_sums_off_unity",
                    "instances": worst.len(),
                    "entries": worst,
                }));
            }
            if ch.ledger.sf_branches > 0 {
                decay_defects.push(serde_json::json!({
                    "class": "spontaneous_fission_branches_to_leakage",
                    "instances": ch.ledger.sf_branches,
                }));
            }
            let mut unquantified: Vec<serde_json::Value> = Vec::new();
            if !led.targets_absent_from_decay_lib.is_empty() {
                unquantified.push(serde_json::json!({
                    "class": "targets_absent_from_decay_library",
                    "names": led.targets_absent_from_decay_lib.iter()
                        .map(|(z, l)| format!("{z}_{l}")).collect::<Vec<_>>(),
                }));
            }
            if !led.bulk_production_dropped.is_empty() {
                unquantified.push(serde_json::json!({
                    "class": "bulk_production_dropped",
                    "instances": led.bulk_production_dropped.len(),
                }));
            }
            let zeroed: f64 = steps.iter().map(|s| s.negative_atoms_zeroed).sum();
            if zeroed > 0.0 {
                unquantified.push(serde_json::json!({
                    "class": "negative_atoms_zeroed",
                    "atoms_zeroed": zeroed,
                }));
            }
            if !absent.is_empty() {
                unquantified.push(serde_json::json!({
                    "class": "composition_isotopes_absent_from_decay_library",
                    "names": absent.iter().map(|(z, l)| format!("{z}_{l}"))
                        .collect::<Vec<_>>(),
                }));
            }
            if n_fallback > 0 {
                unquantified.push(serde_json::json!({
                    "class": "decay_nuclides_from_fallback",
                    "instances": n_fallback,
                }));
            }
            reaction_defects.sort_by(|a: &serde_json::Value, b| {
                b["rate_coefficient_per_s"]
                    .as_f64()
                    .unwrap_or(0.0)
                    .total_cmp(&a["rate_coefficient_per_s"].as_f64().unwrap_or(0.0))
            });
            decay_defects.sort_by(|a: &serde_json::Value, b| {
                b["rate_coefficient_per_s"]
                    .as_f64()
                    .unwrap_or(0.0)
                    .total_cmp(&a["rate_coefficient_per_s"].as_f64().unwrap_or(0.0))
            });
            let status = if reaction_defects.is_empty()
                && decay_defects.is_empty()
                && unquantified.is_empty()
            {
                "complete"
            } else {
                "incomplete"
            };
            ledger.as_object_mut().expect("ledger is an object").insert(
                "completeness".into(),
                serde_json::json!({
                    "status": status,
                    "reaction_channel": {
                        "total_production_rate_coefficient_per_s": total_react,
                        "defects": reaction_defects,
                    },
                    "decay_channel": {
                        "total_decay_rate_coefficient_per_s": total_decay,
                        "defects": decay_defects,
                    },
                    "unquantified": unquantified,
                }),
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
            if prepared.table.has_a2() {
                ledger["radiological"]["formula"]["a2"] = "sum(activity_Bq_per_g / A2_Bq)".into();
            }
            if prepared.table.has_activity_limit() {
                ledger["radiological"]["formula"]["activity_limit"] =
                    "sum(activity_Bq_per_g / limit_Bq)".into();
            }
        }
        if let (Some(prepared), Some(plan)) = (&self.damage, &damage_plan) {
            ledger.as_object_mut().expect("ledger is an object").insert(
                "damage".into(),
                serde_json::json!({
                    "table_sha256": &prepared.sha256,
                    "require_complete": prepared.options.require_complete,
                    "displacement_energy_eV": prepared.options.displacement_energy_eV,
                    "covered_elements": plan
                        .covered_elements
                        .keys()
                        .map(|z| composition::symbol_of(*z).to_string())
                        .collect::<Vec<_>>(),
                    "uncovered_targets": &plan.uncovered_targets,
                    "model": "NRT: dpa_rate_per_s = 0.8 * damage_energy_eV_per_s_per_atom / (2 * E_d); damage targets are the material's composition-resolved nuclides; transmutation products are not damage targets",
                    "units": {
                        "damage_energy_eV_per_g_s": "eV per gram per second",
                        "dpa_rate_per_s": "displacements per atom per second",
                        "dpa": "dimensionless NRT displacements per atom",
                    },
                }),
            );
        }
        if let Some(loaded) = &kerma_loaded {
            let powered: Vec<(usize, &PowerStepOut)> = steps
                .iter()
                .filter_map(|step| step.power.as_ref().map(|power| (step.step, power)))
                .collect();
            ledger.as_object_mut().expect("ledger is an object").insert(
                "kerma".into(),
                serde_json::json!({
                    "table_sha256": &loaded.sha256,
                    "require_complete": loaded.options.require_complete,
                    "uncovered_composition_targets": &kerma_uncovered,
                    "model": "heating = flux_multiplier * sum_i N_i * sum_g K_ig * phi_g * 10^-24 cm^2/b * 1.602176634e-19 W; N_i are the tracked or reservoir inventories at the step end, so transmutation products with a table row contribute and products without one contribute nothing",
                    "units": {
                        "heating_W_per_g": "watts per gram of material",
                        "heating_W_cm3": "watts per cubic centimetre; present only with material.density_g_cm3",
                        "damage_energy_eV_per_g_s": "eV per gram per second from MT=444 rows",
                    },
                    "power": if has_power {
                        Some(serde_json::json!({
                            "steps": powered.len(),
                            "power_tolerance": loaded.options.power_tolerance,
                            "max_substeps": loaded.options.max_substeps,
                            "substeps_total": powered.iter().map(|(_, power)| power.substeps).sum::<usize>(),
                            "max_power_drift": powered
                                .iter()
                                .map(|(_, power)| power.max_power_drift)
                                .fold(0.0f64, f64::max),
                            "steps_not_converged": powered
                                .iter()
                                .filter(|(_, power)| !power.converged)
                                .map(|(step, _)| *step)
                                .collect::<Vec<_>>(),
                            "model": "power = flux_multiplier * sum_i N_i * k_i; each step is split into equal sub-intervals, the multiplier is set at the start of each from the composition then reached, and the split doubles up to max_substeps until every sub-interval's end power is within power_tolerance of the request (relative); the reported drift is that end-of-interval departure",
                            "estimate": "the start-of-run composition sizes the flux used for rate pruning and the burnup-mode decision before the schedule is solved; achieved multipliers are reported per step",
                        }))
                    } else {
                        None
                    },
                }),
            );
        }
        if let (Some(prepared), Some(plan)) = (&self.shielding, &shield_plan) {
            let (model, channel_map) = if plan.geometry.is_some() {
                (
                    "universal sigmoid curve (Martinho, Goncalves and Salgado): per resolved resonance z = N * sigma_peak * y * sqrt(Gamma_gamma / Gamma), G(z) = (A1 - A2) / (1 + (z/z0)^p) + A2; the per-group applied factor is the Gamma_gamma/E^2 * g*Gamma_n/Gamma weighted mean of G over resonances peaking in the group",
                    "reactions = all: every reaction of a shielded nuclide takes the group factor; reactions = capture: only mt 102 does",
                )
            } else {
                (
                    "Bondarenko: sigma_eff_g = sigma_g * ((1 - c_g) + c_g * f(sigma0_eff, T)); factors interpolate in ln(sigma0) and sqrt(T); outside unresolved ranges f = 1",
                    "mt 2 -> elastic; mt 18/19 -> fission; mt 102 -> capture; all other reactions take the total factor",
                )
            };
            let mut shielding_ledger = serde_json::json!({
                "table_sha256": &prepared.sha256,
                "dilution": plan.dilution,
                "sigma0_fixed_b": plan.sigma0_fixed_b,
                "require_complete": spec.options.require_shielding_complete,
                "sigma0_eff_b": plan.sigma0_eff_b,
                "applied_factors": plan.applied,
                "shielding_uncovered": &plan.uncovered_targets,
                "sigma_p_estimated": &plan.sigma_p_estimated,
                "channels": crate::shielding::CHANNELS,
                "model": model,
                "channel_map": channel_map,
            });
            let shielding_object = shielding_ledger
                .as_object_mut()
                .expect("shielding ledger is an object");
            if !plan.dilution_overrides.is_empty() {
                shielding_object.insert(
                    "dilution_overrides".into(),
                    serde_json::json!(&plan.dilution_overrides),
                );
            }
            if let Some(geometry) = &plan.geometry {
                shielding_object.insert("geometry".into(), serde_json::json!(geometry));
                shielding_object.insert(
                    "resonance_files_sha256".into(),
                    serde_json::json!(&prepared.resonance_sha256),
                );
            }
            ledger
                .as_object_mut()
                .expect("ledger is an object")
                .insert("shielding".into(), shielding_ledger);
        }
        if spec.options.gas {
            ledger.as_object_mut().expect("ledger is an object").insert(
                "gas".into(),
                serde_json::json!({
                    "table_version": gas::TABLE_VERSION,
                    "uncovered": &led.gas_uncovered,
                    "missing_light_states": self
                        .gas_missing_light_states
                        .iter()
                        .map(|(z, l)| name_of(*z, *l))
                        .collect::<Vec<_>>(),
                }),
            );
        }
        if let Some(options) = &spec.options.fission {
            ledger.as_object_mut().expect("ledger is an object").insert(
                "fission".into(),
                serde_json::json!({
                    "energy_per_fission_MeV": options.energy_per_fission_MeV,
                    "heavy_metal_min_Z": fission_count::HEAVY_METAL_MIN_Z,
                    "initial_heavy_metal_atoms_per_g": heavy_metal.atoms_per_g,
                    "heavy_metal_isotopes_without_mass": heavy_metal
                        .without_mass
                        .iter()
                        .map(|(za, liso)| name_of(*za, *liso))
                        .collect::<Vec<_>>(),
                    "model": "fissions = integral over the schedule of (MT=18 reaction rate per atom x population), flux-scaled per step, plus one per spontaneous-fission decay (lambda x SF branching x population), whether or not the state has yield data; every MT=18 row counts, including those with no fission yields; burnup_percent = 100 x fissions / initial atoms with Z >= 90; burnup_MWd_per_kg_hm = fissions x energy_per_fission / initial heavy-metal mass, with heavy-metal mass from the decay evaluation's AWR; the count is the rate integral, not FISPACT-II's change in the number of fissionable nuclides",
                }),
            );
        }
        if let Some(prepared) = &self.spontaneous {
            // Coverage is reported for states that are present in this solve, not for every state
            // in the decay library with an SF branch.
            let active =
                |key: &(i32, i32)| active_fission_parents.contains(&format!("{}_{}", key.0, key.1));
            let active_names = |keys: &[(i32, i32)]| -> Vec<String> {
                let mut keys: Vec<(i32, i32)> =
                    keys.iter().copied().filter(|key| active(key)).collect();
                keys.sort_unstable();
                keys.dedup();
                keys.into_iter().map(|key| name_of(key.0, key.1)).collect()
            };
            let sf_states: Vec<(i32, i32)> = ch
                .ledger
                .sf_decay
                .iter()
                .map(|&(column, _)| ch.keys[column])
                .collect();
            let (with_nubar, without_nubar): (Vec<_>, Vec<_>) = sf_states
                .iter()
                .copied()
                .partition(|key| prepared.nubar.contains_key(key));
            ledger.as_object_mut().expect("ledger is an object").insert(
                "spontaneous_fission".into(),
                serde_json::json!({
                    "yield_files": prepared.yield_inputs.iter().map(|(reference, sha, parents)| serde_json::json!({
                        "path": reference.path,
                        "sha256": sha,
                        "parents": parents.iter().map(|parent| name_of(parent.0, parent.1)).collect::<Vec<_>>(),
                    })).collect::<Vec<_>>(),
                    "yield_selection": "independent yields at the lowest tabulated incident energy (E = 0 for spontaneous fission)",
                    "yield_sum_per_parent": prepared
                        .yields
                        .iter()
                        .filter(|(parent, _)| active(parent))
                        .map(|(parent, products)| (name_of(parent.0, parent.1), products.values().sum::<f64>()))
                        .collect::<BTreeMap<_, _>>(),
                    "routed_to_products": active_names(&ch.ledger.sf_routed),
                    "uncovered_to_leakage": active_names(&ch.ledger.sf_uncovered),
                    "products_to_leakage": ch
                        .ledger
                        .sf_product_leakage
                        .iter()
                        .filter(|entry| active_fission_parents.contains(&entry.parent))
                        .collect::<Vec<_>>(),
                    "nubar_covered": active_names(&with_nubar),
                    "nubar_uncovered": active_names(&without_nubar),
                    "nubar_inputs": prepared.nubar_inputs,
                    "scope": "coverage lists name states present in this solve; states with an SF branch and no yield data keep the branch on leakage, and states with no nu-bar add to uncovered_sf_decays_per_s_per_g, never an estimate",
                }),
            );
        }
        if let (Some(options), Some(runtime)) = (&spec.uncertainty, &uncertainty_runtime) {
            let flux_running = runtime.flux_channel_requested;
            let flux_excluded_entry = if flux_running {
                "systematic flux uncertainty (transport model, geometry, transport nuclear data); statistical tally error is propagated as the flux channel"
            } else {
                "incident-flux uncertainty"
            };
            let mut record = serde_json::json!({
                "method": runtime.method,
                "band_name": runtime.band_name,
                "confidence_level": options.confidence_level,
                "normal_multiplier": runtime.normal_multiplier,
                "collapse_convention": "group-integrated flux is uniform in energy within each activation group; activation cross sections are groupwise constant; the union of activation and covariance boundaries is integrated",
                "active_parameters": runtime.parameters.len(),
                "covered_parameters": runtime.covered_parameter_positions.len(),
                "uncovered_library_rows": runtime.uncovered_library_rows,
                "absent_cross_parameter_pairs": runtime.absent_cross_parameter_pairs,
                "maximum_covariance_asymmetry_barn2": runtime.maximum_covariance_asymmetry_barn2,
                "excluded_blocks": runtime.excluded_blocks,
                "selected_cram_order": spec.options.cram_order,
                "comparison_cram_order": if spec.options.cram_order == 16 { 48 } else { 16 },
                "excluded_sources": [
                    "decay data and MF=32 resonance-parameter covariance",
                    "MF=40 radionuclide-production and yield covariance",
                    "fission/product-yield covariance",
                    flux_excluded_entry,
                    "material-composition uncertainty",
                    "response-coefficient uncertainty",
                    "model discrepancy"
                ],
            });
            if flux_running {
                let object = record.as_object_mut().expect("record is an object");
                object.insert("flux_channel_requested".into(), serde_json::json!(true));
                object.insert(
                    "flux_parameters".into(),
                    serde_json::json!(runtime.flux_parameters.len()),
                );
                object.insert(
                    "flux_held_at_nominal".into(),
                    serde_json::json!("energy-dependent fission-yield selection, pruning and mode selection are held at the nominal run's choices while the flux channel is propagated"),
                );
            }
            if let Some(prepared) = &self.covariance {
                let object = record.as_object_mut().expect("record is an object");
                object.insert(
                    "covariance_builder_fingerprint".into(),
                    prepared
                        .index
                        .get("builder_fingerprint")
                        .cloned()
                        .unwrap_or(serde_json::Value::Null),
                );
                object.insert(
                    "covariance_source_manifest_sha256".into(),
                    prepared
                        .index
                        .get("source_manifest_sha256")
                        .cloned()
                        .unwrap_or(serde_json::Value::Null),
                );
            }
            ledger
                .as_object_mut()
                .expect("ledger is an object")
                .insert("uncertainty".into(), record);
            if options.restrict_tangents_to_covered {
                // Only emitted under restriction — byte-identical ledger for
                // the dense path (sealed result hashes must not move).
                ledger["uncertainty"]["tangents_integrated"] = runtime.tangents.len().into();
                ledger["uncertainty"]["per_parameter_sensitivities"] = "covered_rows_only".into();
            }
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
                "decay_overrides": spec.decay.overrides.as_ref().filter(|p| !p.is_empty()).zip(decay_overrides_sha.as_ref())
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
        // Opt-in photon components are recorded only when requested, so default certificates
        // keep their bytes.
        if want_depletion {
            certificate["depletion"] = serde_json::json!({
                "formula": "removal per atom = lambda + sum of loss rows (sigma phi multiplier)",
                "uncertainty": if depletion_with_covariance {
                    "sqrt(w^T C w) of the MF=33 covariance, w = sigma_g phi_g / sum(sigma phi)"
                } else {
                    "none (no MF=33 covariance in this run)"
                },
            });
        }
        if let Some(point) = &spec.photon.point_source {
            certificate["photon"]["point_source"] = serde_json::json!({
                "formula": "FISPACT-II manual Eq. 63: D = C sum_i mu_a(E_i) exp(-mu(E_i) r) m_s S(E_i) / (4 pi r^2), group centroids, no build-up",
                "distance_m": point.distance_m,
                "air_attenuation": point.air_attenuation,
                "air_density_g_cm3": point.air_attenuation.then_some(point.air_density_g_cm3),
            });
        }
        if let Some(brems) = &spec.photon.bremsstrahlung {
            certificate["photon"]["bremsstrahlung"] = serde_json::json!({
                "model": "FISPACT-II manual appendix C.7.4, Eqs. 65-68: one beta-minus per decay at the mean beta energy, atom-fraction Z_eff",
                "nuclides": &brems.nuclides,
                "mean_beta_energy": "STYP=1 spectrum average energy of the decay record; never inferred",
            });
        }
        if let (Some(options), Some(runtime)) = (&spec.uncertainty, &uncertainty_runtime) {
            let flux_running = runtime.flux_channel_requested;
            let flux_excluded_entry = if flux_running {
                "systematic flux (statistical tally error covered by the flux channel)"
            } else {
                "flux"
            };
            let certificate_object = certificate
                .as_object_mut()
                .expect("certificate is an object");
            certificate_object.insert(
                "uncertainty".into(),
                serde_json::json!({
                    "method": runtime.method,
                    "band_name": runtime.band_name,
                    "confidence_level": options.confidence_level,
                    "selected_cram_order": spec.options.cram_order,
                    "comparison_cram_order": if spec.options.cram_order == 16 { 48 } else { 16 },
                    "collapse_convention": "uniform flux per unit energy inside activation groups, integrated on the union boundary grid",
                    "excluded_sources": ["decay/MF=32", "production/MF=40 and yields", flux_excluded_entry, "composition", "response coefficients", "model discrepancy"],
                }),
            );
            if let Some(prepared) = &self.covariance {
                certificate_object
                    .get_mut("inputs")
                    .and_then(serde_json::Value::as_object_mut)
                    .expect("certificate inputs is an object")
                    .insert(
                        "covariance".into(),
                        serde_json::json!({
                            "path": options.covariance.as_ref().map(|c| c.path.as_str()),
                            "sha256_declared": options.covariance.as_ref().map(|c| c.sha256.as_str()),
                            "sha256": prepared.sha256,
                            "index": {"path": prepared.index_path, "sha256": prepared.index_sha256},
                            "source_manifest_sha256": prepared.index.get("source_manifest_sha256"),
                            "builder_fingerprint": prepared.index.get("builder_fingerprint"),
                        }),
                    );
            }
        }
        if decay_only {
            // P124: no activation library was read, so none is certified.
            let certificate_object = certificate
                .as_object_mut()
                .expect("certificate is an object");
            certificate_object.insert("decay_only".into(), serde_json::json!(true));
            let inputs = certificate_object
                .get_mut("inputs")
                .and_then(serde_json::Value::as_object_mut)
                .expect("certificate inputs is an object");
            inputs.insert("library".into(), serde_json::Value::Null);
            inputs.insert("library_index".into(), serde_json::Value::Null);
        }
        if let Some(provenance) = &self.unmodeled_table {
            certificate
                .as_object_mut()
                .expect("certificate is an object")
                .get_mut("inputs")
                .and_then(serde_json::Value::as_object_mut)
                .expect("certificate inputs is an object")
                .insert("unmodeled_table".into(), provenance.clone());
        }
        if let Some(provenance) = &self.unmodeled_evalspread {
            certificate
                .as_object_mut()
                .expect("certificate is an object")
                .get_mut("inputs")
                .and_then(serde_json::Value::as_object_mut)
                .expect("certificate inputs is an object")
                .insert("unmodeled_evalspread".into(), provenance.clone());
        }
        if has_step_library {
            let entries: Vec<serde_json::Value> = self
                .step_libraries
                .iter()
                .map(|step_library| {
                    serde_json::json!({
                        "projectile": step_library.projectile.name(),
                        "library": {"path": step_library.reference.path, "sha256": step_library.library_sha},
                        "library_index": {"path": step_library.index_path, "sha256": step_library.index_sha},
                    })
                })
                .collect();
            certificate
                .as_object_mut()
                .expect("certificate is an object")
                .get_mut("inputs")
                .and_then(serde_json::Value::as_object_mut)
                .expect("certificate inputs is an object")
                .insert("step_libraries".into(), serde_json::Value::Array(entries));
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
        if let Some(prepared) = &self.damage {
            let mut metadata = prepared.table.certificate_metadata();
            let metadata_object = metadata
                .as_object_mut()
                .expect("damage metadata is an object");
            metadata_object.insert(
                "require_complete".into(),
                serde_json::Value::Bool(prepared.options.require_complete),
            );
            metadata_object.insert(
                "displacement_energy_eV".into(),
                serde_json::to_value(&prepared.options.displacement_energy_eV)
                    .expect("displacement energies serialize"),
            );
            let certificate_object = certificate
                .as_object_mut()
                .expect("certificate is an object");
            certificate_object.insert("damage".into(), metadata);
            certificate_object
                .get_mut("inputs")
                .and_then(serde_json::Value::as_object_mut)
                .expect("certificate inputs is an object")
                .insert(
                    "damage_table".into(),
                    serde_json::json!({
                        "path": &prepared.options.table.path,
                        "sha256_declared": &prepared.options.table.sha256,
                        "sha256": &prepared.sha256,
                    }),
                );
        }
        if let Some(loaded) = &kerma_loaded {
            let mut metadata = loaded.table.certificate_metadata();
            let metadata_object = metadata
                .as_object_mut()
                .expect("kerma metadata is an object");
            metadata_object.insert(
                "require_complete".into(),
                serde_json::Value::Bool(loaded.options.require_complete),
            );
            let certificate_object = certificate
                .as_object_mut()
                .expect("certificate is an object");
            certificate_object.insert("kerma".into(), metadata);
            certificate_object
                .get_mut("inputs")
                .and_then(serde_json::Value::as_object_mut)
                .expect("certificate inputs is an object")
                .insert(
                    "kerma_table".into(),
                    serde_json::json!({
                        "path": &loaded.options.table.path,
                        "sha256_declared": &loaded.options.table.sha256,
                        "sha256": &loaded.sha256,
                    }),
                );
        }
        if let (Some(prepared), Some(plan)) = (&self.shielding, &shield_plan) {
            let certificate_object = certificate
                .as_object_mut()
                .expect("certificate is an object");
            let (format_name, channel_map, method_limits) = if plan.geometry.is_some() {
                (
                    "sigmoid-curve-geometry",
                    "reactions = all: every reaction of a shielded nuclide takes the group factor; reactions = capture: only mt 102 does",
                    serde_json::json!([
                        "sigmoid parameters A1, A2, z0, p are the Martinho et al. fit quoted in the FISPACT-II manual (Eqs. 45-48); the peak cross section is the single-level Breit-Wigner value 4*pi/k^2 * g*Gamma_n/Gamma",
                        "Sigma_tot at each resonance counts only the shielded nuclide's own peak; the other mixture components are neglected (resonances are assumed not to overlap)",
                        "only Breit-Wigner and Reich-Moore resolved resonances are used; the unresolved range is not shielded",
                        "damage-energy observables are not shielded",
                        "number densities use the declared material and density, not evolved inventories",
                    ]),
                )
            } else {
                (
                    "actinv-shield-table-1",
                    "mt 2 -> elastic; mt 18/19 -> fission; mt 102 -> capture; all other reactions take the total factor",
                    serde_json::json!([
                        "resolved-resonance segments fold a SIGMA1-broadened pointwise quadrature per temperature column; unresolved segments carry PURR-style probability tables",
                        "factors apply to collapsed group rates via the full-group Bondarenko fold: covered segments carry the probability-table weight, the uncovered part is suppressed by sigma0/(sigma0+background total)",
                        "damage-energy observables are not shielded",
                        "composition dilution uses the declared material, not evolved inventories",
                        "sigma_p for composition members absent from the table is the analytic channel-radius estimate",
                    ]),
                )
            };
            let mut shielding_certificate = serde_json::json!({
                "format": format_name,
                "table_sha256": prepared.sha256,
                "dilution": plan.dilution,
                "sigma0_fixed_b": plan.sigma0_fixed_b,
                "temperature_K": self.temperature_K.get(),
                "sigma0_eff_b": plan.sigma0_eff_b,
                "applied_factors": plan.applied,
                "shielding_uncovered": plan.uncovered_targets,
                "sigma_p_estimated": plan.sigma_p_estimated,
                "channel_map": channel_map,
                "method_limits": method_limits,
            });
            let shielding_object = shielding_certificate
                .as_object_mut()
                .expect("shielding certificate is an object");
            if !plan.dilution_overrides.is_empty() {
                shielding_object.insert(
                    "dilution_overrides".into(),
                    serde_json::json!(&plan.dilution_overrides),
                );
            }
            if let Some(geometry) = &plan.geometry {
                shielding_object.insert("geometry".into(), serde_json::json!(geometry));
            }
            certificate_object.insert("shielding".into(), shielding_certificate);
            let inputs = certificate_object
                .get_mut("inputs")
                .and_then(serde_json::Value::as_object_mut)
                .expect("certificate inputs is an object");
            if let Some(table) = &prepared.options.table {
                inputs.insert(
                    "shielding_table".into(),
                    serde_json::json!({
                        "path": &table.path,
                        "sha256_declared": &table.sha256,
                        "sha256": &prepared.sha256,
                    }),
                );
            }
            if let Some(geometry) = &prepared.options.geometry {
                let files: Vec<serde_json::Value> = geometry
                    .resonance_files
                    .iter()
                    .zip(&prepared.resonance_sha256)
                    .map(|(reference, sha256)| {
                        serde_json::json!({
                            "path": &reference.path,
                            "sha256_declared": &reference.sha256,
                            "sha256": sha256,
                        })
                    })
                    .collect();
                inputs.insert(
                    "shielding_resonance_files".into(),
                    serde_json::Value::Array(files),
                );
            }
        }
        if let Some(prepared) = &self.spontaneous {
            certificate
                .as_object_mut()
                .expect("certificate is an object")
                .get_mut("inputs")
                .and_then(serde_json::Value::as_object_mut)
                .expect("certificate inputs is an object")
                .insert(
                    "spontaneous_fission".into(),
                    serde_json::json!({
                        "yield_files": prepared.yield_inputs.iter().map(|(reference, sha, parents)| serde_json::json!({
                            "path": reference.path,
                            "sha256_declared": reference.sha256,
                            "sha256": sha,
                            "parents": parents.iter().map(|parent| name_of(parent.0, parent.1)).collect::<Vec<_>>(),
                        })).collect::<Vec<_>>(),
                        "nubar": prepared.nubar_inputs,
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
        let result = RunResult {
            spec_title: spec.title.clone(),
            entry_point: entry_point.into(),
            projectile: (!spec.projectile.is_neutron()).then(|| spec.projectile.name().to_owned()),
            mode,
            pruned_states: m,
            total_states: ch.n,
            steps,
            pathways,
            screen: screen_block,
            pathway_closure: closure,
            isomer_pathway_shares,
            pathway_routes,
            ledger,
            certificate,
            ms: t0.elapsed().as_secs_f64() * 1e3,
        };
        // JSON writers record NaN and ±inf as null, indistinguishable from an absent value.
        crate::finite::check(&result)
            .map_err(|found| format!("the result holds a non-finite number: {found}"))?;
        Ok(result)
    }
}

/// P51: single-slot prepared-input cache. A `PreparedRun` gathers every
/// data-file-derived input a solve needs (activation library, covariance,
/// decay tables, photon/fission/radiological/damage/shielding preparations,
/// chain); none of it depends on the spec's material or schedule. The slot is
/// keyed by a fingerprint of every input that determines it — spec-side
/// references/options plus the resolved content sha256 of each referenced
/// file — so a hit reuses preparation only when every file is unchanged, and
/// a changed file is a miss that revalidates. One slot bounds worker memory.
///
/// P85: the slot also records a base fingerprint that leaves out the spectrum's flux values. When a
/// miss differs from the slot only in the spectrum, the slot is re-prepared with groupwise activation
/// data and then serves every spectrum with that base fingerprint, collapsing each run's own flux in
/// memory instead of building and writing a spectrum-collapsed artifact per spectrum.
#[derive(Default)]
pub struct PreparedCache {
    slot: Option<(String, PreparedRun)>,
    base: Option<String>,
    groupwise: bool,
    last_hit: bool,
    last_fingerprint_ms: f64,
}

impl PreparedCache {
    pub fn new() -> Self {
        Self::default()
    }
    /// Whether the most recent `run_with_cache` reused the slot.
    pub fn last_hit(&self) -> bool {
        self.last_hit
    }
    /// Time spent fingerprinting inputs on the most recent call — the only
    /// preparation a warm request pays.
    pub fn last_fingerprint_ms(&self) -> f64 {
        self.last_fingerprint_ms
    }
}

fn fingerprint_file(path: &str) -> Result<serde_json::Value, String> {
    Ok(serde_json::json!({"path": path, "sha256": file_sha256(path)?}))
}

/// The full fingerprint, and the base fingerprint without the spectrum's flux values and total and
/// without the collapse flux (P85).
fn prepared_fingerprint(
    spec: &Spec,
    physical: &PhysicalInputs,
) -> Result<(String, String), String> {
    let multi_spectrum = physical
        .schedule
        .iter()
        .any(|step| step.spectrum_flux.is_some());
    let collapse = if multi_spectrum {
        None
    } else {
        collapsible_spectrum(physical.flux.values())
    };
    let mut files = Vec::new();
    // A decay-only spec (P124) has no library file to fingerprint.
    if !spec.library.path.is_empty() {
        files.push(fingerprint_file(&spec.library.path)?);
    }
    files.push(fingerprint_file(&spec.decay.primary)?);
    if let Some(path) = spec.decay.fallback.as_ref().filter(|p| !p.is_empty()) {
        files.push(fingerprint_file(path)?);
    }
    if let Some(covariance) = spec
        .uncertainty
        .as_ref()
        .and_then(|options| options.covariance.as_ref())
    {
        files.push(fingerprint_file(&covariance.path)?);
    }
    if let Some(response) = spec.photon.response.as_ref() {
        files.push(fingerprint_file(&response.path)?);
    }
    for file in &spec.fission_yields.files {
        files.push(fingerprint_file(&file.path)?);
    }
    let step_libraries = spec.step_libraries()?;
    for step_library in &step_libraries {
        files.push(fingerprint_file(&step_library.library.path)?);
    }
    if let Some(spontaneous) = spec.fission_yields.spontaneous.as_ref() {
        for file in spontaneous
            .files
            .iter()
            .chain(&spontaneous.nubar_endf)
            .chain(&spontaneous.nubar_table)
        {
            files.push(fingerprint_file(&file.path)?);
        }
    }
    if let Some(options) = spec.radiological.as_ref() {
        match crate::radiological::bundled_name(&options.table.path) {
            Some(name) => files.push(serde_json::json!({
                "path": options.table.path,
                "sha256": crate::radiological::bundled_sha256(name)?,
            })),
            None => files.push(fingerprint_file(&options.table.path)?),
        }
    }
    if let Some(options) = spec.damage.as_ref() {
        files.push(fingerprint_file(&options.table.path)?);
    }
    if let Some(options) = spec.self_shielding.as_ref() {
        if let Some(table) = &options.table {
            files.push(fingerprint_file(&table.path)?);
        }
        if let Some(geometry) = &options.geometry {
            for reference in &geometry.resonance_files {
                files.push(fingerprint_file(&reference.path)?);
            }
        }
    }
    let mut canonical = serde_json::json!({
        "library": spec.library,
        "decay": spec.decay,
        "photon": spec.photon,
        "fission_yields": spec.fission_yields,
        "projectile": spec.projectile.name(),
        "temperature_K": physical.temperature.get(),
        "uncertainty": spec.uncertainty,
        "radiological": spec.radiological,
        "damage": spec.damage,
        "self_shielding": spec.self_shielding,
        "spectrum": spec.spectrum,
        // options.gas changes what PreparedRun.chain contains (light-state stand-ins and their
        // decay edges), unlike every other options.* field, which only affects how a prepared
        // chain is solved. It must be part of the cache key or a worker session could reuse a
        // gas-off PreparedRun for a gas-on spec sharing the same library/decay/etc (or the
        // reverse), silently omitting or fabricating gas output.
        "gas": spec.options.gas,
        "multi_spectrum": multi_spectrum,
        "collapse_flux": collapse
            .map(|flux| flux.iter().map(|v| v.to_bits()).collect::<Vec<u64>>()),
        "files": files,
    });
    // Present only for a schedule that switches library, so every other fingerprint is unchanged.
    if !step_libraries.is_empty() {
        canonical["step_libraries"] = serde_json::Value::Array(
            step_libraries
                .iter()
                .map(|step_library| {
                    serde_json::json!({
                        "library": step_library.library.path,
                        "sha256": step_library.library.sha256,
                        "projectile": step_library.projectile.name(),
                    })
                })
                .collect(),
        );
    }
    let full = format!("{:x}", Sha256::digest(canonical.to_string().as_bytes()));
    let object = canonical
        .as_object_mut()
        .ok_or("prepared fingerprint is not an object")?;
    object.remove("multi_spectrum");
    object.remove("collapse_flux");
    if let Some(spectrum) = object
        .get_mut("spectrum")
        .and_then(serde_json::Value::as_object_mut)
    {
        spectrum.remove("flux_per_group");
        spectrum.remove("total");
    }
    let base = format!("{:x}", Sha256::digest(canonical.to_string().as_bytes()));
    Ok((full, base))
}

pub fn run_with_cache(
    spec: &Spec,
    entry_point: &str,
    cache: &mut PreparedCache,
) -> Result<RunResult, String> {
    let started = std::time::Instant::now();
    let mut profiler = RunProfiler::from_environment();
    let physical = spec.physical_inputs()?;
    let fingerprint_started = std::time::Instant::now();
    let (fingerprint, base) = prepared_fingerprint(spec, &physical)?;
    cache.last_fingerprint_ms = fingerprint_started.elapsed().as_secs_f64() * 1e3;
    let same_base = cache.slot.is_some() && cache.base.as_deref() == Some(base.as_str());
    let hit = if cache.groupwise {
        same_base
    } else {
        matches!(cache.slot.as_ref(), Some((stored, _)) if *stored == fingerprint)
    };
    cache.last_hit = hit;
    if !hit {
        // Only the spectrum changed: prepare groupwise data that serve every spectrum (P85).
        let groupwise = same_base;
        let prepared =
            PreparedRun::prepare_profiled_with(spec, &physical, &mut profiler, groupwise)?;
        cache.slot = Some((fingerprint, prepared));
        cache.base = Some(base);
        cache.groupwise = groupwise;
    }
    let prepared = &cache.slot.as_ref().expect("cache slot populated").1;
    let result = prepared.run_started_profiled(
        spec,
        &physical,
        entry_point,
        started,
        &mut profiler,
        None,
        None,
    )?;
    profiler.emit(started.elapsed());
    Ok(result)
}

pub fn run(spec: &Spec, entry_point: &str) -> Result<RunResult, String> {
    run_with_cache(spec, entry_point, &mut PreparedCache::new())
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
            removed_atoms_per_g: None,
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
            damage: None,
            gas: None,
            summary: None,
            fission: None,
            kerma: None,
            power: None,
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

    #[test]
    fn zero_spectrum_is_not_collapsed() {
        assert!(super::collapsible_spectrum(&[0.0, 0.0]).is_none());
        assert!(super::collapsible_spectrum(&[]).is_none());
        assert!(super::collapsible_spectrum(&[0.0, f64::INFINITY]).is_none());
        assert_eq!(
            super::collapsible_spectrum(&[0.0, 2.5]),
            Some(&[0.0, 2.5][..])
        );
    }

    #[test]
    fn isomer_name_detection() {
        assert!(super::is_isomer_name("W185m1"));
        assert!(super::is_isomer_name("Mn57m1"));
        assert!(super::is_isomer_name("Ta180m2"));
        assert!(!super::is_isomer_name("W185"));
        assert!(!super::is_isomer_name("Am241"));
        assert!(!super::is_isomer_name("Cm242"));
        assert!(!super::is_isomer_name("Pm147"));
        assert!(!super::is_isomer_name("Sm149"));
        assert!(!super::is_isomer_name("Tm170"));
        assert!(!super::is_isomer_name("W185m"));
        assert!(!super::is_isomer_name("m1"));
    }
}

/// P93 G1: unit tests for the flux (transport-tally statistical error) channel.
#[cfg(test)]
mod flux_channel_tests {
    use super::*;
    use crate::chain::{self, Chain, ChainLedger, RateLedger};
    use crate::cram;
    use actinv_data::fission::EffectiveYields;
    use actinv_data::library::{Library, Row};

    fn dense_get(csc: &Csc, row: usize, col: usize) -> C64 {
        for k in csc.colptr[col]..csc.colptr[col + 1] {
            if csc.rowidx[k] == row {
                return csc.vals[k];
            }
        }
        C64::new(0.0, 0.0)
    }

    // -- Sum identity: the flux channel's per-group directions must sum to
    // -- exactly the reaction part of the nominal matrix, for a dense
    // -- (709-group) input and a coarse custom input.

    /// A two-row library (one MT=1 loss row, one MT=102 production row),
    /// generalized from chain.rs's own
    /// `derivative_free_assembly_preserves_rates_and_ledger` fixture to an
    /// arbitrary group count, with a deterministic pattern that includes
    /// exact-zero cross sections (exercising the `sigma == 0.0 { continue }`
    /// skip in `build_flux_parameters`).
    fn synthetic_library(ngroups: usize) -> Library {
        let mut bounds = Vec::with_capacity(ngroups + 1);
        for g in 0..=ngroups {
            bounds.push(1.0e-5 * 10f64.powf(11.0 * g as f64 / ngroups as f64));
        }
        let mut sig = Vec::with_capacity(2 * ngroups);
        for row in 0..2usize {
            for g in 0..ngroups {
                let phase = (row * 7 + g * 3) as f64;
                let value = (phase.sin() * 4.0 + 5.0).max(0.0);
                sig.push(if g % 4 == 0 { 0.0 } else { value });
            }
        }
        Library {
            rows: vec![
                Row {
                    target: 0,
                    mt: 102,
                    zap: 26_057,
                    lfs: 0,
                    lmf: 3,
                },
                Row {
                    target: 0,
                    mt: 1,
                    zap: -1,
                    lfs: 0,
                    lmf: 3,
                },
            ],
            sig,
            ngroups,
            bounds,
        }
    }

    fn synthetic_chain() -> Chain {
        Chain {
            index: HashMap::from([((26_056, 0), 0), ((26_057, 0), 1)]),
            keys: vec![(26_056, 0), (26_057, 0)],
            lambda: vec![0.0, 0.0],
            decay: Vec::new(),
            leak: 2,
            unit: 3,
            n: 4,
            ledger: ChainLedger::default(),
        }
    }

    fn synthetic_flux(ngroups: usize) -> Vec<f64> {
        (0..ngroups)
            .map(|g| {
                if g % 5 == 0 {
                    0.0
                } else {
                    1.0 + (g as f64 * 1.7).cos().abs()
                }
            })
            .collect()
    }

    fn check_sum_matches_nominal(ngroups: usize) {
        let library = synthetic_library(ngroups);
        let chain = synthetic_chain();
        let flux = synthetic_flux(ngroups);
        let targets = [(26_056, 0)];
        let yields: HashMap<(i32, i32), EffectiveYields> = HashMap::new();
        let mut ledger = RateLedger::default();
        let assembly = chain::reaction_rates_with_derivatives(
            &library,
            &targets,
            &flux,
            &chain,
            &yields,
            &mut ledger,
            None,
            None,
            false,
        );

        let mut nominal: BTreeMap<(usize, usize), C64> = BTreeMap::new();
        for &(row, col, value) in &assembly.triplets {
            *nominal
                .entry((row, col))
                .or_insert_with(|| C64::new(0.0, 0.0)) += C64::new(value, 0.0);
        }

        let mut derivative_sub_base: BTreeMap<usize, Vec<(usize, usize, C64)>> = BTreeMap::new();
        for derivative in &assembly.derivatives {
            if derivative.per_barn_s != 0.0 {
                derivative_sub_base
                    .entry(derivative.library_row)
                    .or_default()
                    .push((
                        derivative.row,
                        derivative.column,
                        C64::new(derivative.per_barn_s, 0.0),
                    ));
            }
        }

        let relative_error = vec![0.1; ngroups];
        let input = FluxInputGroups::Identity {
            boundaries_ev: library.boundaries_ev(),
            flux_per_group: &flux,
            relative_error: &relative_error,
            descending: false,
        };
        let (parameters, directions) = build_flux_parameters(
            &derivative_sub_base,
            &library,
            &targets,
            None,
            chain.n,
            &flux,
            &input,
        )
        .unwrap();
        assert_eq!(parameters.len(), directions.len());
        assert!(!directions.is_empty());

        let mut summed: BTreeMap<(usize, usize), C64> = BTreeMap::new();
        for direction in &directions {
            for col in 0..direction.n {
                for k in direction.colptr[col]..direction.colptr[col + 1] {
                    let row = direction.rowidx[k];
                    *summed
                        .entry((row, col))
                        .or_insert_with(|| C64::new(0.0, 0.0)) += direction.vals[k];
                }
            }
        }

        let mut keys: BTreeSet<(usize, usize)> = nominal.keys().copied().collect();
        keys.extend(summed.keys().copied());
        assert!(!keys.is_empty());
        for key in keys {
            let expected = nominal.get(&key).copied().unwrap_or(C64::new(0.0, 0.0));
            let actual = summed.get(&key).copied().unwrap_or(C64::new(0.0, 0.0));
            let scale = expected.norm().max(1e-300);
            assert!(
                (actual - expected).norm() <= 1e-12 * scale,
                "{key:?}: expected {expected}, got {actual} ({ngroups} groups)"
            );
        }
    }

    #[test]
    fn flux_direction_sum_reproduces_nominal_matrix_dense_709_group() {
        check_sum_matches_nominal(709);
    }

    #[test]
    fn flux_direction_sum_reproduces_nominal_matrix_coarse_custom() {
        check_sum_matches_nominal(5);
    }

    // -- CRAM tangent correctness: the flux parameter's tangent must match a
    // -- central difference of plain CRAM steps at perturbed flux, and (at
    // -- low burnup, single group, single reaction) the propagated relative
    // -- standard uncertainty of the product activity must equal the
    // -- declared relative error `e`.

    fn one_group_one_reaction_matrix(rate: f64, lambda_b: f64) -> Csc {
        // State 0 = target (held exactly constant: no entries in its own
        // row/column other than feeding state 1), state 1 = product.
        Csc::from_triplets(
            2,
            &[
                (1, 0, C64::new(rate, 0.0)),
                (1, 1, C64::new(-lambda_b, 0.0)),
            ],
        )
    }

    #[test]
    fn flux_tangent_matches_central_difference_of_plain_cram_steps() {
        let rate = 3.0e-3;
        let lambda_b = 1.0e-2;
        let n0 = [1.0, 0.0];
        let dt = 1.0;
        let c = super::cram(16);

        let a = one_group_one_reaction_matrix(rate, lambda_b);
        // ln(phi)-parameterized direction: d(rate)/d ln(phi) = rate itself,
        // since a single group's rate is linear in its own flux.
        let direction = Csc::from_triplets(2, &[(1, 0, C64::new(rate, 0.0))]);

        let step =
            cram::step_with_tangents(&a, &n0, &[vec![0.0, 0.0]], &[direction], &[1.0], dt, &c)
                .unwrap();
        let tangent_b = step.tangents[0][1];

        let h = 1.0e-6;
        let a_plus = one_group_one_reaction_matrix(rate * (1.0 + h), lambda_b);
        let a_minus = one_group_one_reaction_matrix(rate * (1.0 - h), lambda_b);
        let (state_plus, _) = cram::step(&a_plus, &n0, dt, &c).unwrap();
        let (state_minus, _) = cram::step(&a_minus, &n0, dt, &c).unwrap();
        // d/d ln(phi) ~= (f(phi(1+h)) - f(phi(1-h))) / (ln(1+h) - ln(1-h))
        let central_difference =
            (state_plus[1] - state_minus[1]) / ((1.0 + h).ln() - (1.0 - h).ln());

        let scale = tangent_b.abs().max(1e-12);
        assert!(
            (tangent_b - central_difference).abs() <= 1.0e-6 * scale,
            "tangent {tangent_b} vs central difference {central_difference}"
        );
    }

    #[test]
    fn low_burnup_relative_uncertainty_of_product_activity_equals_declared_error() {
        let rate = 1.0e-4;
        let lambda_b = 1.0e-3;
        let n0 = [1.0, 0.0];
        let dt = 1.0; // low burnup: rate*dt and lambda_b*dt are both << 1
        let c = super::cram(16);
        let e = 0.037; // declared relative statistical error of the one group

        let a = one_group_one_reaction_matrix(rate, lambda_b);
        let direction = Csc::from_triplets(2, &[(1, 0, C64::new(rate, 0.0))]);
        let step =
            cram::step_with_tangents(&a, &n0, &[vec![0.0, 0.0]], &[direction], &[1.0], dt, &c)
                .unwrap();
        let n_b = step.state[1];
        let tangent_b = step.tangents[0][1];

        // activity = lambda_b * N_b; sensitivity = lambda_b * tangent_b; the
        // lambda_b factor cancels in the ratio, so this is exactly N_b's own
        // relative sensitivity to ln(phi).
        assert!(n_b > 0.0);
        let relative_standard_uncertainty = (tangent_b * e).abs() / n_b;
        assert!(
            (relative_standard_uncertainty - e).abs() <= 1.0e-3 * e,
            "relative_standard_uncertainty {relative_standard_uncertainty} vs declared e {e}"
        );
    }

    // -- Validation: `resolve_flux_relative_error` names the spectrum or the
    // -- mesh cell when the channel is requested but no errors are given,
    // -- and a mesh cell's own errors take priority over the spec's.

    fn plain_spectrum(relative_error: Option<Vec<f64>>) -> Spectrum {
        Spectrum {
            structure: "custom".into(),
            flux_per_group: vec![1.0, 2.0],
            total: None,
            boundaries_eV: None,
            descending: false,
            relative_error,
            rebin: None,
            wall_loading_MW_m2: None,
        }
    }

    #[test]
    fn plain_spec_without_relative_error_names_the_spectrum() {
        let spectrum = plain_spectrum(None);
        let err = resolve_flux_relative_error(&spectrum, None).unwrap_err();
        assert!(err.contains("spectrum.relative_error"), "{err}");
    }

    #[test]
    fn mesh_cell_without_relative_error_names_the_cell() {
        let spectrum = plain_spectrum(None);
        let boundaries = [1.0, 2.0, 3.0];
        let flux = [1.0, 2.0];
        let origin = MeshFluxOrigin {
            source_boundaries_eV: &boundaries,
            source_flux_per_group: &flux,
            source_relative_error: None,
        };
        let err = resolve_flux_relative_error(&spectrum, Some(&origin)).unwrap_err();
        assert!(err.contains("mesh cell"), "{err}");
    }

    #[test]
    fn mesh_cell_relative_error_takes_priority_over_the_specs_own() {
        let spectrum = plain_spectrum(Some(vec![0.9, 0.9]));
        let boundaries = [1.0, 2.0, 3.0];
        let flux = [1.0, 2.0];
        let cell_error = [0.05, 0.06];
        let origin = MeshFluxOrigin {
            source_boundaries_eV: &boundaries,
            source_flux_per_group: &flux,
            source_relative_error: Some(&cell_error),
        };
        let resolved = resolve_flux_relative_error(&spectrum, Some(&origin)).unwrap();
        assert_eq!(resolved, vec![0.05, 0.06]);
    }

    #[test]
    fn plain_spec_relative_error_is_used_when_no_mesh_origin() {
        let spectrum = plain_spectrum(Some(vec![0.1, 0.2]));
        let resolved = resolve_flux_relative_error(&spectrum, None).unwrap();
        assert_eq!(resolved, vec![0.1, 0.2]);
    }

    #[test]
    fn plain_spec_relative_error_honours_descending_reorder() {
        let mut spectrum = plain_spectrum(Some(vec![0.1, 0.2]));
        spectrum.descending = true;
        let resolved = resolve_flux_relative_error(&spectrum, None).unwrap();
        assert_eq!(resolved, vec![0.2, 0.1]);
    }

    #[test]
    fn dense_get_reads_absent_entries_as_zero() {
        let csc = Csc::from_triplets(2, &[(1, 0, C64::new(3.0, 0.0))]);
        assert_eq!(dense_get(&csc, 1, 0), C64::new(3.0, 0.0));
        assert_eq!(dense_get(&csc, 0, 0), C64::new(0.0, 0.0));
        assert_eq!(dense_get(&csc, 0, 1), C64::new(0.0, 0.0));
    }

    #[test]
    fn descending_flag_maps_reported_group_index_to_the_declared_order() {
        // `flux_per_group`/`relative_error` reach `build_flux_parameters`
        // already reordered ascending (as `run_started_profiled` always
        // passes them): only the reported `FluxParameter.group` label
        // should undo the reversal, matching the array the spec declared.
        let ngroups = 4;
        let library = synthetic_library(ngroups);
        let chain = synthetic_chain();
        let flux = synthetic_flux(ngroups);
        let targets = [(26_056, 0)];
        let yields: HashMap<(i32, i32), EffectiveYields> = HashMap::new();
        let mut ledger = RateLedger::default();
        let assembly = chain::reaction_rates_with_derivatives(
            &library,
            &targets,
            &flux,
            &chain,
            &yields,
            &mut ledger,
            None,
            None,
            false,
        );
        let mut derivative_sub_base: BTreeMap<usize, Vec<(usize, usize, C64)>> = BTreeMap::new();
        for derivative in &assembly.derivatives {
            if derivative.per_barn_s != 0.0 {
                derivative_sub_base
                    .entry(derivative.library_row)
                    .or_default()
                    .push((
                        derivative.row,
                        derivative.column,
                        C64::new(derivative.per_barn_s, 0.0),
                    ));
            }
        }
        let relative_error = vec![0.1; ngroups];
        let ascending_input = FluxInputGroups::Identity {
            boundaries_ev: library.boundaries_ev(),
            flux_per_group: &flux,
            relative_error: &relative_error,
            descending: false,
        };
        let descending_input = FluxInputGroups::Identity {
            boundaries_ev: library.boundaries_ev(),
            flux_per_group: &flux,
            relative_error: &relative_error,
            descending: true,
        };
        let (ascending_parameters, _) = build_flux_parameters(
            &derivative_sub_base,
            &library,
            &targets,
            None,
            chain.n,
            &flux,
            &ascending_input,
        )
        .unwrap();
        let (descending_parameters, _) = build_flux_parameters(
            &derivative_sub_base,
            &library,
            &targets,
            None,
            chain.n,
            &flux,
            &descending_input,
        )
        .unwrap();
        assert_eq!(ascending_parameters.len(), descending_parameters.len());
        assert!(!ascending_parameters.is_empty());
        for (ascending, descending) in ascending_parameters.iter().zip(&descending_parameters) {
            // Same ascending group physically (same energy bounds, flux and
            // error): only the reported label differs.
            assert_eq!(ascending.lower_bound_eV, descending.lower_bound_eV);
            assert_eq!(ascending.upper_bound_eV, descending.upper_bound_eV);
            assert_eq!(ascending.flux_per_cm2_s, descending.flux_per_cm2_s);
            assert_eq!(descending.group, ngroups - 1 - ascending.group);
        }
    }
}

/// P92 gas end-to-end tests: a hand-built `PreparedRun` wired directly from Rust structs (no
/// files, no ENDF text, no on-disk caching), exercising the same `run_started_profiled` path
/// `run()` uses. This is the minimal way to run a real schedule through trace and coupled mode
/// and compare, which needs the full per-step solve — not reachable from `chain.rs`'s own
/// triplet-assembly tests.
#[cfg(test)]
mod gas_prepared_tests {
    use super::*;
    use std::collections::HashSet;

    const TARGET: (i32, i32) = (26_056, 0); // stand-in "T"; Fe56 only because "Fe56" is a
                                            // material-composition key the real element table accepts

    fn stand_in(za: (i32, i32)) -> decay::Nuclide {
        decay::Nuclide {
            mat: 0,
            za: za.0,
            awr: (za.0 % 1000) as f64,
            liso: za.1,
            nst: 1,
            half_life: 0.0,
            d_half_life: 0.0,
            energies: Vec::new(),
            modes: Vec::new(),
            spectra: Vec::new(),
        }
    }

    /// One stable target with a single covered (n,alpha) channel (MT 107): a loss row books the
    /// target's total depletion, and an unmapped-residual product row (matching the existing
    /// H3 case in `chain.rs`'s `gas_test_fixture`) still adds the He4 ejectile. Both rows carry
    /// the same tiny cross section, so the loss row's rate is exactly the one channel's rate.
    fn synthetic_gas_prepared(mode: &str) -> (Spec, PreparedRun) {
        let spec: Spec = serde_json::from_value(serde_json::json!({
            "spec": "actinv-spec-1",
            "library": {"path": "synthetic.npz"},
            "decay": {"primary": "synthetic.dat"},
            "material": {"mass_g": 1.0, "basis": "wt_percent", "composition": {"Fe56": 100.0}},
            "spectrum": {"structure": "custom", "boundaries_eV": [1.0, 2.0, 3.0, 4.0, 5.0],
                         "flux_per_group": [1.0e-2, 1.0e-2, 1.0e-2, 1.0e-2], "descending": false},
            "schedule": [{"dt": "1 s", "flux": 1.0}],
            "options": {"mode": mode, "prune": "none", "gas": true}
        }))
        .expect("synthetic gas spec parses");

        let lib = library::Library {
            rows: vec![
                library::Row {
                    target: 0,
                    mt: 1,
                    zap: -1,
                    lfs: 0,
                    lmf: 0,
                }, // total loss
                library::Row {
                    target: 0,
                    mt: 107,
                    zap: 999_999,
                    lfs: 0,
                    lmf: -2,
                }, // (n,alpha); unmapped residual, gas still applies
            ],
            sig: vec![1.0e-4; 2 * 4],
            ngroups: 4,
            bounds: vec![1.0, 2.0, 3.0, 4.0, 5.0],
        };
        let mut nuclides: HashMap<(i32, i32), decay::Nuclide> =
            HashMap::from([(TARGET, stand_in(TARGET))]);
        let chain = chain::build(&nuclides);
        let (mut chain, missing) = chain::ensure_light_states(chain);
        for za in &missing {
            nuclides.entry(*za).or_insert_with(|| stand_in(*za));
        }
        chain::add_gas_decay_edges(&nuclides, &mut chain);

        let prepared = PreparedRun {
            library_path: "synthetic.npz".into(),
            library_sha_declared: None,
            library_sha: "0".repeat(64),
            index_path: "synthetic_index.json".into(),
            index_sha: "0".repeat(64),
            index: serde_json::json!({}),
            library: ActivationLibrary::Dense(lib),
            library_targets: vec![TARGET],
            decay_primary: "synthetic.dat".into(),
            decay_primary_sha: "0".repeat(64),
            decay_fallback: None,
            decay_fallback_sha: None,
            decay_overrides_sha: None,
            decay_overrides_applied: Vec::new(),
            nuclides,
            decay_fallback_keys: HashSet::new(),
            chain,
            gas_missing_light_states: missing,
            decay_nuclides_from_fallback: 0,
            photon_options: PhotonOptions::default(),
            response: None,
            response_sha: None,
            fission_options: FissionYieldOptions::default(),
            fission_yields: HashMap::new(),
            fission_yield_inputs: Vec::new(),
            spontaneous: None,
            projectile: Projectile::Neutron,
            library_group_structure: None,
            temperature_K: Kelvin::new(293.6).expect("293.6 K is valid"), // spec.options.temperature_K default
            uncertainty_options: None,
            covariance: None,
            unmodeled_table: None,
            unmodeled_evalspread: None,
            radiological: None,
            damage: None,
            shielding: None,
            step_libraries: Vec::new(),
        };
        (spec, prepared)
    }

    fn run_he4_appm(mode: &str) -> f64 {
        let (spec, prepared) = synthetic_gas_prepared(mode);
        let physical = spec.physical_inputs().expect("physical inputs resolve");
        let mut profiler = RunProfiler::disabled();
        let result = prepared
            .run_started_profiled(
                &spec,
                &physical,
                "test",
                std::time::Instant::now(),
                &mut profiler,
                None,
                None,
            )
            .expect("run succeeds");
        assert_eq!(result.mode, mode);
        let last = result.steps.last().expect("at least one step");
        let gas = last
            .gas
            .as_ref()
            .expect("gas block present when options.gas is true");
        assert!(
            gas.He4.produced_atoms_per_g > 0.0,
            "the (n,alpha) channel must produce He4"
        );
        gas.He4.appm
    }

    #[test]
    fn trace_and_coupled_gas_agree_at_low_fluence() {
        let trace = run_he4_appm("trace");
        let coupled = run_he4_appm("coupled");
        let rel = (trace - coupled).abs() / coupled.abs();
        assert!(
            rel < 1e-6,
            "trace He4 appm {trace} vs coupled {coupled}, relative difference {rel}"
        );
    }

    #[test]
    fn gas_disabled_leaves_the_step_gas_block_absent() {
        let (mut spec, prepared) = synthetic_gas_prepared("coupled");
        spec.options.gas = false;
        let physical = spec.physical_inputs().expect("physical inputs resolve");
        let mut profiler = RunProfiler::disabled();
        let result = prepared
            .run_started_profiled(
                &spec,
                &physical,
                "test",
                std::time::Instant::now(),
                &mut profiler,
                None,
                None,
            )
            .expect("run succeeds");
        assert!(result.steps.last().unwrap().gas.is_none());
    }

    /// P95 G1: `inventory_appm` is FISPACT-II's printed `APPM OF` convention, which counts the
    /// initial content. ACTINV emits no t = 0 step, so a zero-flux first step stands in for it:
    /// at that step a material that is 10 at% H1 has H1 `inventory_appm` = 0.1 × 1e6 and produced
    /// `appm` = 0.
    #[test]
    fn inventory_appm_counts_initial_hydrogen_and_appm_does_not() {
        let (mut spec, prepared) = synthetic_gas_prepared("coupled");
        spec.material = serde_json::from_value(serde_json::json!({
            "mass_g": 1.0, "basis": "atom_fraction", "composition": {"Fe56": 0.9, "H1": 0.1}
        }))
        .expect("material parses");
        spec.schedule = serde_json::from_value(serde_json::json!([
            {"dt": "1 s", "flux": 0.0},
            {"dt": "1 s", "flux": 1.0}
        ]))
        .expect("schedule parses");
        let physical = spec.physical_inputs().expect("physical inputs resolve");
        let mut profiler = RunProfiler::disabled();
        let result = prepared
            .run_started_profiled(
                &spec,
                &physical,
                "test",
                std::time::Instant::now(),
                &mut profiler,
                None,
                None,
            )
            .expect("run succeeds");
        let first = result.steps[0].gas.as_ref().expect("gas block present");
        let rel = (first.H1.inventory_appm - 1.0e5).abs() / 1.0e5;
        assert!(
            rel < 1e-12,
            "H1 inventory_appm {} vs 1e5",
            first.H1.inventory_appm
        );
        // Produced appm is atoms minus the initial content, so zero up to the cancellation
        // roundoff of that subtraction (observed 4.4e-11 appm against a 1e5 inventory).
        assert!(
            first.H1.appm.abs() <= 1e-12 * first.H1.inventory_appm,
            "H1 produced appm {} at zero flux",
            first.H1.appm
        );
        assert_eq!(first.H_inventory_appm, first.H1.inventory_appm);
        assert_eq!(first.He_inventory_appm, 0.0);
        // After irradiation, a species with no initial content has inventory_appm == appm.
        let last = result.steps.last().unwrap().gas.as_ref().unwrap();
        assert!(last.He4.appm > 0.0);
        let rel_he4 = (last.He4.inventory_appm - last.He4.appm).abs() / last.He4.appm;
        assert!(
            rel_he4 < 1e-12,
            "He4 inventory_appm {} vs appm {}",
            last.He4.inventory_appm,
            last.He4.appm
        );
    }

    // ---- P126 power normalization and kerma, end to end on the same hand-built PreparedRun.

    /// Group flux of every one of the four synthetic groups (spec.spectrum.flux_per_group).
    const PHI: f64 = 1.0e-2;

    /// Heating of one atom at unit flux multiplier for a flat kerma factor, in W.
    fn watts_per_atom(kerma_barn_ev: f64) -> f64 {
        4.0 * kerma_barn_ev * PHI * crate::power::square_cm_per_barn() * EV
    }

    /// Replace the library with one whose total-loss row has cross section `sigma_b` in every
    /// group (the (n,alpha) product row keeps the same value).
    fn with_cross_section(prepared: &mut PreparedRun, sigma_b: f64) {
        prepared.library = ActivationLibrary::Dense(library::Library {
            rows: vec![
                library::Row {
                    target: 0,
                    mt: 1,
                    zap: -1,
                    lfs: 0,
                    lmf: 0,
                },
                library::Row {
                    target: 0,
                    mt: 107,
                    zap: 999_999,
                    lfs: 0,
                    lmf: -2,
                },
            ],
            sig: vec![sigma_b; 2 * 4],
            ngroups: 4,
            bounds: vec![1.0, 2.0, 3.0, 4.0, 5.0],
        });
    }

    /// Write a one-nuclide kerma table and point the spec at it; the caller removes the file.
    fn attach_kerma(
        spec: &mut Spec,
        tag: &str,
        kerma_barn_ev: f64,
        tolerance: f64,
        max_substeps: usize,
    ) -> std::path::PathBuf {
        let path =
            std::env::temp_dir().join(format!("actinv-kerma-{}-{tag}.json", std::process::id()));
        let table = serde_json::json!({
            "format": "actinv-kerma-table-1",
            "source": {"citation": "synthetic", "edition": "test", "url": "local"},
            "projectile": "neutron",
            "group_structure": "test-4g",
            "boundaries_eV": [1.0, 2.0, 3.0, 4.0, 5.0],
            "units": "kerma_barn_eV_per_group",
            "targets": {"Fe56": vec![kerma_barn_ev; 4]},
        });
        std::fs::write(&path, table.to_string()).expect("kerma table is written");
        let location = path.to_string_lossy().into_owned();
        spec.kerma = Some(KermaOptions {
            table: HashedFileRef {
                sha256: file_sha256(&location).expect("kerma table hashes"),
                path: location,
            },
            require_complete: true,
            power_tolerance: tolerance,
            max_substeps,
            top: 5,
        });
        path
    }

    fn initial_atoms(spec: &Spec, prepared: &PreparedRun) -> f64 {
        composition::material_atoms_per_gram(
            &spec.material.composition,
            &spec.material.basis,
            &prepared.nuclides,
        )
        .expect("composition resolves")
        .0
        .values()
        .sum()
    }

    fn run_prepared(spec: &Spec, prepared: &PreparedRun) -> Result<RunResult, String> {
        let physical = spec.physical_inputs()?;
        let mut profiler = RunProfiler::disabled();
        prepared.run_started_profiled(
            spec,
            &physical,
            "test",
            std::time::Instant::now(),
            &mut profiler,
            None,
            None,
        )
    }

    fn fe56_atoms(step: &StepOut) -> f64 {
        step.inventory
            .iter()
            .find(|nuclide| nuclide.nuclide == "Fe56")
            .expect("Fe56 is populated")
            .atoms_per_g
    }

    #[test]
    fn power_step_reproduces_the_requested_power_without_substeps_when_burnup_is_negligible() {
        for (mode, tag) in [("trace", "static-trace"), ("coupled", "static-coupled")] {
            let (mut spec, mut prepared) = synthetic_gas_prepared(mode);
            with_cross_section(&mut prepared, 1.0e-4);
            let path = attach_kerma(&mut spec, tag, 1.0, 1.0e-3, 8);
            let n0 = initial_atoms(&spec, &prepared);
            let target = 1.0; // W/g
            spec.material.density_g_cm3 = Some(7.874);
            spec.schedule = serde_json::from_value(serde_json::json!([
                {"dt": "1 s", "power_W_cm3": target * 7.874}
            ]))
            .expect("schedule parses");
            spec.validate().expect("spec validates");
            let result = run_prepared(&spec, &prepared).expect("run succeeds");
            std::fs::remove_file(&path).ok();
            let step = &result.steps[0];
            let power = step.power.as_ref().expect("power record present");
            assert_eq!(power.substeps, 1, "{mode}");
            assert!(power.converged, "{mode}");
            assert!((power.requested_W_per_g - target).abs() < 1e-12, "{mode}");
            assert_eq!(power.requested_W_cm3, Some(target * 7.874));
            let expected = target / (watts_per_atom(1.0) * n0);
            let rel = (step.flux - expected).abs() / expected;
            assert!(rel < 1e-9, "{mode}: flux {} vs {expected}", step.flux);
            assert!(
                (power.flux_per_substep_cm2_s[0] - expected * 4.0 * PHI).abs()
                    < 1e-9 * expected * 4.0 * PHI,
                "{mode}"
            );
            let kerma = step.kerma.as_ref().expect("kerma record present");
            assert!(
                (kerma.heating_W_per_g - target).abs() < 1e-6 * target,
                "{mode}"
            );
            assert!(
                (kerma.heating_W_cm3.unwrap() - target * 7.874).abs() < 1e-6 * target * 7.874,
                "{mode}"
            );
            assert_eq!(kerma.top_contributors[0].nuclide, "Fe56");
            assert_eq!(result.ledger["kerma"]["power"]["steps"], 1);
            assert_eq!(
                result.certificate["inputs"]["kerma_table"]["sha256"]
                    .as_str()
                    .map(str::len),
                Some(64)
            );
        }
    }

    #[test]
    fn power_step_substeps_when_the_inventory_burns_during_the_step() {
        let (mut spec, mut prepared) = synthetic_gas_prepared("coupled");
        with_cross_section(&mut prepared, 1.0e3);
        let path = attach_kerma(&mut spec, "burn", 1.0, 5.0e-2, 256);
        let n0 = initial_atoms(&spec, &prepared);
        // constant power P burns c = P * b / w atoms/g/s, where b is the loss rate per atom at
        // unit multiplier and w the heating per atom; choose P to burn 58% of the inventory
        let burn_fraction = 0.58;
        let loss_per_unit_multiplier = 4.0 * 1.0e3 * PHI * crate::power::square_cm_per_barn();
        let target = burn_fraction * n0 * watts_per_atom(1.0) / loss_per_unit_multiplier;
        spec.schedule = serde_json::from_value(serde_json::json!([
            {"dt": "1 s", "power_W_per_g": target}
        ]))
        .expect("schedule parses");
        spec.validate().expect("spec validates");
        let result = run_prepared(&spec, &prepared).expect("run succeeds");
        std::fs::remove_file(&path).ok();
        let step = &result.steps[0];
        let power = step.power.as_ref().expect("power record present");
        assert!(power.substeps > 1, "substeps {}", power.substeps);
        assert!(power.converged);
        assert!(power.max_power_drift <= 5.0e-2);
        assert!(power.refinement.len() > 1);
        assert_eq!(power.refinement[0].substeps, 1);
        assert!(power.refinement[0].max_power_drift > 5.0e-2);
        assert_eq!(power.flux_multiplier_per_substep.len(), power.substeps);
        assert!(
            power.flux_multiplier_per_substep.last() > power.flux_multiplier_per_substep.first()
        );
        let expected = (1.0 - burn_fraction) * n0;
        assert!(
            (fe56_atoms(step) - expected).abs() < 0.05 * expected,
            "Fe56 {} vs {expected}",
            fe56_atoms(step)
        );
        let kerma = step.kerma.as_ref().expect("kerma record present");
        assert!(
            (kerma.heating_W_per_g - target).abs() / target <= power.max_power_drift * (1.0 + 1e-9),
            "end-of-step heating {} vs {target}",
            kerma.heating_W_per_g
        );
    }

    #[test]
    fn flux_driven_step_reports_kerma_and_no_power_record() {
        let (mut spec, mut prepared) = synthetic_gas_prepared("coupled");
        with_cross_section(&mut prepared, 1.0e-4);
        let path = attach_kerma(&mut spec, "flux", 2.0, 1.0e-3, 8);
        let n0 = initial_atoms(&spec, &prepared);
        spec.schedule = serde_json::from_value(serde_json::json!([
            {"dt": "1 s", "flux": 3.0},
            {"dt": "1 s", "flux": 0.0}
        ]))
        .expect("schedule parses");
        let result = run_prepared(&spec, &prepared).expect("run succeeds");
        std::fs::remove_file(&path).ok();
        assert!(result.steps.iter().all(|step| step.power.is_none()));
        let expected = 3.0 * watts_per_atom(2.0) * n0;
        let heating = result.steps[0].kerma.as_ref().unwrap().heating_W_per_g;
        assert!(
            (heating - expected).abs() < 1e-6 * expected,
            "{heating} vs {expected}"
        );
        // cooling: no flux, no heating, no listed contributors
        let cooled = result.steps[1].kerma.as_ref().unwrap();
        assert_eq!(cooled.heating_W_per_g, 0.0);
        assert!(cooled.top_contributors.is_empty());
        // the kerma block is a pure addition: the JSON of a spec without it carries neither key
        let (plain_spec, plain_prepared) = synthetic_gas_prepared("coupled");
        let plain = run_prepared(&plain_spec, &plain_prepared).expect("run succeeds");
        let value = serde_json::to_value(&plain.steps[0]).unwrap();
        assert!(value.get("kerma").is_none() && value.get("power").is_none());
        assert!(plain.ledger.get("kerma").is_none());
    }

    #[test]
    fn trace_pathways_close_under_a_flux_multiplier_and_after_a_cooling_gap() {
        // the decomposition must use the multiplier the solve used, and a source must feed again
        // when irradiation resumes after cooling
        let (mut spec, prepared) = synthetic_gas_prepared("trace");
        spec.schedule = serde_json::from_value(serde_json::json!([
            {"dt": "1 s", "flux": 0.37},
            {"dt": "1 s", "flux": 0.0},
            {"dt": "1 s", "flux": 2.5}
        ]))
        .expect("schedule parses");
        let result = run_prepared(&spec, &prepared).expect("run succeeds");
        assert_eq!(result.mode, "trace");
        assert_eq!(result.pathways.len(), 3);
        assert!(!result.pathways[0].is_empty() && !result.pathways[2].is_empty());
        assert!(
            result.pathway_closure < 1e-10,
            "closure {}",
            result.pathway_closure
        );
    }

    #[test]
    fn power_step_in_trace_mode_reports_pathways_that_close() {
        let (mut spec, mut prepared) = synthetic_gas_prepared("trace");
        with_cross_section(&mut prepared, 1.0e-4);
        let path = attach_kerma(&mut spec, "paths", 1.0, 1.0e-3, 8);
        spec.schedule = serde_json::from_value(serde_json::json!([
            {"dt": "1 s", "power_W_per_g": 0.3},
            {"dt": "1 s", "flux": 0.0}
        ]))
        .expect("schedule parses");
        spec.validate().expect("spec validates");
        let result = run_prepared(&spec, &prepared).expect("run succeeds");
        std::fs::remove_file(&path).ok();
        assert_eq!(result.mode, "trace");
        let power = result.steps[0]
            .power
            .as_ref()
            .expect("power record present");
        assert!((power.flux_multiplier_per_substep[0] - 1.0).abs() > 1e-3);
        assert!(!result.pathways.is_empty() && !result.pathways[0].is_empty());
        assert!(
            result.pathway_closure < 1e-10,
            "closure {}",
            result.pathway_closure
        );
        assert!(result.ledger["kerma"]["power"]
            .get("pathways_suppressed")
            .is_none());
    }

    #[test]
    fn power_step_without_a_kerma_section_is_refused() {
        let (mut spec, prepared) = synthetic_gas_prepared("coupled");
        spec.schedule = serde_json::from_value(serde_json::json!([
            {"dt": "1 s", "power_W_per_g": 1.0}
        ]))
        .expect("schedule parses");
        let error = match run_prepared(&spec, &prepared) {
            Ok(_) => panic!("a power step with no kerma table must fail"),
            Err(error) => error,
        };
        assert!(error.contains("kerma"), "{error}");
    }

    fn run_synthetic(spec: &Spec, prepared: &PreparedRun) -> Result<RunResult, String> {
        run_prepared(spec, prepared)
    }

    #[test]
    fn partition_move_removes_exactly_the_fraction() {
        let mut vector = vec![8.0, 0.0, 4.0, 2.0, 0.0];
        let moved = partition_move(&mut vector, &[0, 2], 4, 0.25);
        assert_eq!(moved, 9.0);
        assert_eq!(vector, vec![2.0, 0.0, 1.0, 2.0, 9.0]);
        // kept = 1 leaves everything, kept = 0 moves everything
        assert_eq!(partition_move(&mut vector, &[0, 2], 4, 1.0), 0.0);
        assert_eq!(partition_move(&mut vector, &[0, 2], 4, 0.0), 3.0);
        assert_eq!(vector, vec![0.0, 0.0, 0.0, 2.0, 12.0]);
    }

    /// A zero-flux step with `partition: {Fe: kept}` moves exactly `1 - kept` of the Fe56
    /// inventory into the removal sink, forces the coupled formulation under `auto`, and
    /// reports the removal in the ledger.
    #[test]
    fn partition_step_moves_the_declared_fraction_to_the_sink() {
        let (mut spec, prepared) = synthetic_gas_prepared("auto");
        let removed_at = |spec: &mut Spec, kept: f64| {
            spec.schedule = serde_json::from_value(serde_json::json!([
                {"dt": "1 s", "flux": 0.0, "partition": {"Fe": kept}}
            ]))
            .expect("schedule parses");
            let result = run_synthetic(spec, &prepared).expect("run succeeds");
            assert_eq!(result.mode, "coupled");
            let initial = result.steps[0]
                .gas
                .as_ref()
                .expect("gas block present")
                .initial_atoms_per_g;
            let removed = result.steps[0]
                .removed_atoms_per_g
                .expect("a partition books the removal sink");
            (result, removed, initial)
        };
        let (result, removed, initial) = removed_at(&mut spec, 0.25);
        assert!(
            (removed - 0.75 * initial).abs() <= 1e-9 * initial,
            "removed {removed} vs 0.75 x {initial}"
        );
        let partition = &result.ledger["partition"];
        assert_eq!(partition["mode"], "coupled");
        assert_eq!(partition["mode_forced_coupled"], true);
        assert_eq!(partition["applied"][0]["element"], "Fe");
        assert_eq!(partition["applied"][0]["fraction_kept"], 0.25);
        let (_, removed_all, _) = removed_at(&mut spec, 0.0);
        assert!((removed_all - initial).abs() <= 1e-9 * initial);
        let (_, removed_none, _) = removed_at(&mut spec, 1.0);
        assert_eq!(removed_none, 0.0);
    }

    #[test]
    fn partition_with_explicit_trace_mode_is_rejected_at_run() {
        let (mut spec, prepared) = synthetic_gas_prepared("trace");
        spec.schedule = serde_json::from_value(serde_json::json!([
            {"dt": "1 s", "flux": 0.0, "partition": {"Fe": 0.5}}
        ]))
        .expect("schedule parses");
        let error = match run_synthetic(&spec, &prepared) {
            Err(error) => error,
            Ok(_) => panic!("a trace-mode partition must be rejected"),
        };
        assert!(error.contains("partition is not supported"), "{error}");
    }

    fn radioactive_stand_in(za: i32, half_life: f64, daughter: bool) -> decay::Nuclide {
        let mut nuclide = decay::Nuclide {
            mat: 0,
            za,
            awr: (za % 1000) as f64,
            liso: 0,
            nst: 0,
            half_life,
            d_half_life: 0.0,
            energies: Vec::new(),
            modes: Vec::new(),
            spectra: Vec::new(),
        };
        if daughter {
            // beta-minus decay (RTYP 1.0) to the next element, the whole branching
            nuclide.modes.push(decay::Mode {
                rtyp: 1.0,
                rfs: 0.0,
                q: 0.0,
                dq: 0.0,
                br: 1.0,
                dbr: 0.0,
            });
        }
        nuclide
    }

    /// A decay-only prepared run: no activation library, one radioactive parent (Co60 stand-in)
    /// feeding a stable daughter (Ni60 stand-in).
    fn synthetic_decay_only_prepared(overrides: serde_json::Value) -> (Spec, PreparedRun) {
        let spec = Spec::from_json(
            &serde_json::json!({
                "spec": "actinv-spec-1",
                "decay": {"primary": "synthetic.dat"},
                "material": {"mass_g": 1.0, "basis": "atoms_per_g", "composition": {"Co60": 1.0e20}},
                "schedule": [{"dt": "1000 s", "flux": 0.0}],
                "overrides": overrides,
            })
            .to_string(),
        )
        .expect("decay-only spec parses with no library and no spectrum");
        let mut nuclides: HashMap<(i32, i32), decay::Nuclide> = HashMap::from([
            ((27_060, 0), radioactive_stand_in(27_060, 1000.0, true)),
            ((28_060, 0), stand_in((28_060, 0))),
        ]);
        let chain = chain::build(&nuclides);
        let (chain, missing) = chain::ensure_light_states(chain);
        for za in &missing {
            nuclides.entry(*za).or_insert_with(|| stand_in(*za));
        }
        let prepared = PreparedRun {
            library_path: String::new(),
            library_sha_declared: None,
            library_sha: String::new(),
            index_path: String::new(),
            index_sha: String::new(),
            index: serde_json::Value::Null,
            library: ActivationLibrary::Absent,
            library_targets: Vec::new(),
            decay_primary: "synthetic.dat".into(),
            decay_primary_sha: "0".repeat(64),
            decay_fallback: None,
            decay_fallback_sha: None,
            decay_overrides_sha: None,
            decay_overrides_applied: Vec::new(),
            nuclides,
            decay_fallback_keys: HashSet::new(),
            chain,
            gas_missing_light_states: missing,
            decay_nuclides_from_fallback: 0,
            photon_options: PhotonOptions::default(),
            response: None,
            response_sha: None,
            fission_options: FissionYieldOptions::default(),
            fission_yields: HashMap::new(),
            fission_yield_inputs: Vec::new(),
            spontaneous: None,
            projectile: Projectile::Neutron,
            library_group_structure: None,
            temperature_K: Kelvin::new(293.6).expect("293.6 K is valid"),
            uncertainty_options: None,
            covariance: None,
            unmodeled_table: None,
            unmodeled_evalspread: None,
            radiological: None,
            damage: None,
            shielding: None,
            step_libraries: Vec::new(),
        };
        (spec, prepared)
    }

    #[test]
    fn radioactive_initial_material_selects_coupled_and_refuses_trace() {
        // P133: a decaying initial inventory cannot be a constant trace reservoir.
        let (mut spec, prepared) = synthetic_decay_only_prepared(serde_json::json!([]));
        let auto = run_synthetic(&spec, &prepared).expect("auto run succeeds");
        assert_eq!(auto.mode, "coupled");
        assert_eq!(auto.ledger["bulk_decay_nuclide"], "Co60");
        let fraction = auto.ledger["bulk_decay_fraction_max"].as_f64().unwrap();
        assert!((fraction - 0.5).abs() < 1e-12, "{fraction}");
        // the daughter takes exactly what the parent lost
        let ni60 = auto.steps[0]
            .inventory
            .iter()
            .find(|row| row.nuclide == "Ni60")
            .map(|row| row.atoms_per_g)
            .unwrap();
        assert!((ni60 - 0.5e20).abs() <= 1e-9 * 0.5e20, "{ni60}");
        spec.options.mode = "trace".into();
        let error = run_synthetic(&spec, &prepared).err().unwrap();
        assert!(error.contains("Co60 decays"), "{error}");
    }

    #[test]
    fn decay_only_run_needs_no_library_and_applies_a_half_life_override() {
        let (spec, prepared) = synthetic_decay_only_prepared(serde_json::json!([]));
        let plain = run_synthetic(&spec, &prepared).expect("decay-only run succeeds");
        assert_eq!(plain.ledger["decay_only"], true);
        assert_eq!(plain.certificate["decay_only"], true);
        let decayed = plain.steps[0].activity_Bq_per_g["Co60"];
        // one half-life of 1000 s: A = lambda N0 / 2
        let expected = std::f64::consts::LN_2 / 1000.0 * 1.0e20 * 0.5;
        assert!(
            (decayed - expected).abs() <= 1e-8 * expected,
            "{decayed} vs {expected}"
        );

        let (spec, prepared) = synthetic_decay_only_prepared(serde_json::json!([
            {"nuclide": "Co60", "kind": "half_life", "value": 2000.0, "units": "s"}
        ]));
        let overridden = run_synthetic(&spec, &prepared).expect("override run succeeds");
        // lambda halves and N decays by 2^-0.5 instead of 2^-1
        let ratio = overridden.steps[0].activity_Bq_per_g["Co60"] / decayed;
        assert!(
            (ratio - 0.5f64.sqrt()).abs() <= 1e-8,
            "activity ratio {ratio}"
        );
        assert_eq!(overridden.ledger["overrides"][0]["kind"], "half_life");
        assert_eq!(overridden.ledger["overrides"][0]["half_life_s"], 2000.0);
        assert_eq!(
            overridden.ledger["overrides"][0]["library_half_life_s"],
            1000.0
        );
    }

    #[test]
    fn half_life_override_naming_a_stable_nuclide_is_an_error() {
        let (spec, prepared) = synthetic_decay_only_prepared(serde_json::json!([
            {"nuclide": "Ni60", "kind": "half_life", "value": 10.0}
        ]));
        let error = match run_synthetic(&spec, &prepared) {
            Err(error) => error,
            Ok(_) => panic!("a stable nuclide has no half-life to override"),
        };
        assert!(error.contains("stable nuclide"), "{error}");
    }

    /// P140: in trace mode the decay of a (practically constant) radioactive bulk nuclide feeds its daughter
    /// through the unit state. The first-product table is built from the reaction sources only, so that
    /// production must either appear in `pathways` or show up as a closure gap; it must not vanish silently.
    #[test]
    fn p140_ps_f4_trace_decay_fed_product_is_reported_or_flagged() {
        let (mut spec, mut prepared) = synthetic_gas_prepared("trace");
        // Co60 stand-in with a 1e9 s half-life (decays ~1e-9 of itself over the schedule, under the trace
        // limit) into stable Ni60
        prepared
            .nuclides
            .insert((27_060, 0), radioactive_stand_in(27_060, 1.0e9, true));
        prepared.nuclides.insert((28_060, 0), stand_in((28_060, 0)));
        let (mut chain, missing) = chain::ensure_light_states(chain::build(&prepared.nuclides));
        for za in &missing {
            prepared
                .nuclides
                .entry(*za)
                .or_insert_with(|| stand_in(*za));
        }
        chain::add_gas_decay_edges(&prepared.nuclides, &mut chain);
        prepared.chain = chain;
        prepared.gas_missing_light_states = missing;
        spec.material = serde_json::from_value(serde_json::json!({
            "mass_g": 1.0, "basis": "wt_percent", "composition": {"Fe56": 99.0, "Co60": 1.0}
        }))
        .expect("material parses");
        spec.schedule = serde_json::from_value(serde_json::json!([
            {"dt": "1 s", "flux": 1.0},
            {"dt": "1 s", "flux": 0.0}
        ]))
        .expect("schedule parses");
        let result = run_prepared(&spec, &prepared).expect("run succeeds");
        assert_eq!(result.mode, "trace");
        assert!(
            !result.pathways.is_empty(),
            "the Fe56 activation is attributed"
        );
        let last = result.steps.len() - 1;
        let ni60 = result.steps[last]
            .inventory
            .iter()
            .find(|row| row.nuclide == "Ni60")
            .map(|row| row.atoms_per_g)
            .expect("Ni60 is in the inventory");
        assert!(ni60 > 0.0);
        let listed: f64 = result
            .pathways
            .get(last)
            .and_then(|per| per.get("Ni60"))
            .map(|chains| chains.iter().map(|p| p.atoms_per_g).sum())
            .unwrap_or(0.0);
        let covered = (listed - ni60).abs() <= 1e-6 * ni60;
        let flagged = result.pathway_closure >= 0.5;
        assert!(
            covered || flagged,
            "Ni60 inventory {ni60:e}: pathways list {listed:e} and pathway_closure is {}",
            result.pathway_closure
        );
    }

    // ---- P140 H1 regressions: schedule controls and kerma coverage.

    #[test]
    fn p140_sc_f5_cross_section_override_ignores_spectra_of_cooling_steps() {
        let (mut spec, prepared) = synthetic_gas_prepared("coupled");
        spec.options.gas = false;
        spec.overrides = serde_json::from_value(serde_json::json!([
            {"nuclide": "Fe56", "kind": "cross_section", "mt": 1, "value": 0.4}
        ]))
        .expect("overrides parse");
        let empty_spectrum = serde_json::json!({
            "structure": "custom",
            "boundaries_eV": [1.0, 2.0, 3.0, 4.0, 5.0],
            "flux_per_group": vec![0.0; 4],
        });
        spec.schedule = serde_json::from_value(serde_json::json!([
            {"dt": "1 s", "flux": 1.0},
            {"dt": "1 d", "flux": 0.0, "spectrum": empty_spectrum}
        ]))
        .expect("schedule parses");
        spec.validate().expect("spec validates");
        // the cooling step never reads the cross section, so its all-zero spectrum is no reason
        // to refuse the override
        run_synthetic(&spec, &prepared)
            .expect("an override is checked against spectra the schedule irradiates with");
        // an irradiating step on the same spectrum does read it, and is still refused
        spec.schedule[1].flux = 1.0;
        let error = run_synthetic(&spec, &prepared)
            .err()
            .expect("an irradiating step on a spectrum the reaction vanishes under");
        assert!(error.contains("collapses to zero"), "{error}");
    }

    #[test]
    fn p140_sc_f7_partition_acts_on_bulk_isotopes_absent_from_the_chain() {
        // natural iron: Fe54, Fe57 and Fe58 have no decay record and live only in the bulk
        // inventory, but a partition of Fe takes the whole element
        let (mut spec, prepared) = synthetic_gas_prepared("auto");
        spec.options.gas = false;
        spec.material = serde_json::from_value(serde_json::json!({
            "mass_g": 1.0, "basis": "wt_percent", "composition": {"Fe": 100.0}
        }))
        .expect("material parses");
        spec.summary = Some(serde_json::from_value(serde_json::json!({})).expect("summary parses"));
        spec.schedule = serde_json::from_value(serde_json::json!([
            {"dt": "1 s", "flux": 0.0, "partition": {"Fe": 0.25}}
        ]))
        .expect("schedule parses");
        let initial = initial_atoms(&spec, &prepared);
        match run_synthetic(&spec, &prepared) {
            // refusing a partition it cannot apply to the whole element is also correct
            Err(error) => assert!(
                error.contains("partition") && error.contains("decay"),
                "{error}"
            ),
            Ok(result) => {
                let removed = result.steps[0]
                    .removed_atoms_per_g
                    .expect("a partition books the removal sink");
                assert!(
                    (removed - 0.75 * initial).abs() <= 1e-9 * initial,
                    "removed {removed} vs 0.75 x {initial}"
                );
                let summary = result.steps[0].summary.as_ref().expect("summary present");
                let iron = summary
                    .elements
                    .as_ref()
                    .expect("element table present")
                    .iter()
                    .find(|element| element.symbol == "Fe")
                    .expect("an Fe row");
                assert!(
                    (iron.atoms - 0.25 * initial).abs() <= 1e-9 * initial,
                    "kept Fe {} vs 0.25 x {initial}",
                    iron.atoms
                );
            }
        }
    }

    #[test]
    fn p140_sc_f9_mode_forced_coupled_only_when_trace_would_have_run() {
        // burn-up alone selects the coupled formulation: the partition forced nothing
        let (mut spec, mut prepared) = synthetic_gas_prepared("auto");
        with_cross_section(&mut prepared, 1.0e3);
        spec.schedule = serde_json::from_value(serde_json::json!([
            {"dt": "1 s", "flux": 1.0e18, "partition": {"Fe": 0.5}}
        ]))
        .expect("schedule parses");
        let result = run_synthetic(&spec, &prepared).expect("run succeeds");
        assert_eq!(result.mode, "coupled");
        assert!(result.ledger["partition"].is_object());
        assert_eq!(
            result.ledger["partition"]["mode_forced_coupled"], false,
            "burn-up selects coupled with or without the partition"
        );

        // a decaying initial inventory does the same
        let (mut spec, prepared) = synthetic_decay_only_prepared(serde_json::json!([]));
        spec.schedule = serde_json::from_value(serde_json::json!([
            {"dt": "1000 s", "flux": 0.0, "partition": {"Co": 0.5}}
        ]))
        .expect("schedule parses");
        let result = run_synthetic(&spec, &prepared).expect("run succeeds");
        assert_eq!(result.mode, "coupled");
        assert!(result.ledger["partition"].is_object());
        assert_eq!(
            result.ledger["partition"]["mode_forced_coupled"], false,
            "bulk decay selects coupled with or without the partition"
        );
    }

    #[test]
    fn p140_sc_f16_omitted_spectrum_with_a_library_is_not_reported_as_one_group() {
        let (_, prepared) = synthetic_gas_prepared("coupled");
        let value = serde_json::json!({
            "spec": "actinv-spec-1",
            "library": {"path": "synthetic.npz"},
            "decay": {"primary": "synthetic.dat"},
            "material": {"mass_g": 1.0, "basis": "wt_percent", "composition": {"Fe56": 100.0}},
            "schedule": [{"dt": "1 s", "flux": 0.0}],
            "options": {"mode": "coupled", "prune": "none"}
        });
        let outcome =
            Spec::from_json(&value.to_string()).and_then(|spec| run_synthetic(&spec, &prepared));
        let error = match outcome {
            Err(error) => error,
            Ok(_) => panic!("a library run with no spectrum has nothing to collapse against"),
        };
        assert!(
            !error.contains("1 groups"),
            "the zero-flux placeholder must not leak into the message: {error}"
        );
        let lowered = error.to_lowercase();
        assert!(
            lowered.contains("spectrum")
                && (lowered.contains("required")
                    || lowered.contains("omitted")
                    || lowered.contains("declare")),
            "{error}"
        );
    }

    /// Natural iron against a kerma table whose only row is the element `Fe`: the chain holds
    /// Fe56 alone, so Fe54, Fe57 and Fe58 are composition isotopes that heat nothing.
    fn run_natural_iron_with_element_kerma(require_complete: bool) -> Result<RunResult, String> {
        let (mut spec, prepared) = synthetic_gas_prepared("coupled");
        spec.options.gas = false;
        spec.material = serde_json::from_value(serde_json::json!({
            "mass_g": 1.0, "basis": "wt_percent", "composition": {"Fe": 100.0}
        }))
        .expect("material parses");
        spec.schedule = serde_json::from_value(serde_json::json!([{"dt": "1 s", "flux": 1.0}]))
            .expect("schedule parses");
        let path = std::env::temp_dir().join(format!(
            "actinv-kerma-{}-p140-pf-f2-{require_complete}.json",
            std::process::id()
        ));
        let table = serde_json::json!({
            "format": "actinv-kerma-table-1",
            "source": {"citation": "synthetic", "edition": "test", "url": "local"},
            "projectile": "neutron",
            "group_structure": "test-4g",
            "boundaries_eV": [1.0, 2.0, 3.0, 4.0, 5.0],
            "units": "kerma_barn_eV_per_group",
            "targets": {"Fe": vec![1.0; 4]},
        });
        std::fs::write(&path, table.to_string()).expect("kerma table is written");
        let location = path.to_string_lossy().into_owned();
        spec.kerma = Some(KermaOptions {
            table: HashedFileRef {
                sha256: file_sha256(&location).expect("kerma table hashes"),
                path: location,
            },
            require_complete,
            power_tolerance: 1.0e-3,
            max_substeps: 8,
            top: 5,
        });
        let result = run_synthetic(&spec, &prepared);
        std::fs::remove_file(&path).ok();
        result
    }

    #[test]
    fn p140_pf_f2_require_complete_refuses_composition_isotopes_outside_the_chain() {
        let error = run_natural_iron_with_element_kerma(true)
            .err()
            .expect("Fe54 has a table row but is not a chain state, so it heats nothing");
        assert!(error.contains("kerma") && error.contains("Fe54"), "{error}");
    }

    #[test]
    fn p140_pf_f2_composition_isotopes_outside_the_chain_are_listed_as_uncovered() {
        let result = run_natural_iron_with_element_kerma(false).expect("run succeeds");
        let listed = result.ledger["kerma"]["uncovered_composition_targets"]
            .as_array()
            .expect("uncovered list present");
        assert!(
            listed.iter().any(|name| name.as_str() == Some("Fe54")),
            "{listed:?}"
        );
        // Fe56 is a chain state with a row and is not listed
        assert!(!listed.iter().any(|name| name.as_str() == Some("Fe56")));
    }
}

/// Schedule library overrides (`Step.library`) on a synthetic chain: two dense libraries over one decay chain,
/// solved by the full per-step path. Library A (4 groups) converts Fe56 to Fe57 at 0.5 s^-1 under the base
/// spectrum; library B (2 groups) converts Fe56 to Co59 at 0.3 s^-1 under its own spectrum. Every cross section
/// is 1 b, so a library's rate is 10^-24 times its spectrum's total flux.
#[cfg(test)]
mod step_library_tests {
    use super::*;
    use std::collections::HashSet;

    const TARGET: (i32, i32) = (26_056, 0);
    const PRODUCT_A: (i32, i32) = (26_057, 0);
    const PRODUCT_B: (i32, i32) = (27_059, 0);
    const RATE_A: f64 = 0.5;
    const RATE_B: f64 = 0.3;

    fn stand_in(za: (i32, i32)) -> decay::Nuclide {
        decay::Nuclide {
            mat: 0,
            za: za.0,
            awr: (za.0 % 1000) as f64,
            liso: za.1,
            nst: 1,
            half_life: 0.0,
            d_half_life: 0.0,
            energies: Vec::new(),
            modes: Vec::new(),
            spectra: Vec::new(),
        }
    }

    /// Loss row plus one capture-like product row, both 1 b in every group.
    fn one_channel_library(mt: i32, product: (i32, i32), bounds: Vec<f64>) -> library::Library {
        let ngroups = bounds.len() - 1;
        library::Library {
            rows: vec![
                library::Row {
                    target: 0,
                    mt: 1,
                    zap: -1,
                    lfs: 0,
                    lmf: 0,
                },
                library::Row {
                    target: 0,
                    mt,
                    zap: product.0,
                    lfs: product.1,
                    lmf: -1,
                },
            ],
            sig: vec![1.0; 2 * ngroups],
            ngroups,
            bounds,
        }
    }

    fn spec_json(with_switch: bool) -> Spec {
        let mut schedule = vec![serde_json::json!({"dt": "1 s", "flux": 1.0})];
        if with_switch {
            schedule.push(serde_json::json!({
                "dt": "2 s",
                "flux": 1.0,
                "library": {"path": "synthetic_b.npz"},
                "spectrum": {
                    "structure": "custom",
                    "boundaries_eV": [1.0, 3.0, 5.0],
                    "flux_per_group": [1.0e23, 2.0e23],
                },
            }));
            // cooling: keeps library B and its spectrum, but flux 0 switches every rate off
            schedule.push(serde_json::json!({"dt": "3 s", "flux": 0.0}));
        } else {
            schedule.push(serde_json::json!({"dt": "2 s", "flux": 1.0}));
            schedule.push(serde_json::json!({"dt": "3 s", "flux": 0.0}));
        }
        serde_json::from_value(serde_json::json!({
            "spec": "actinv-spec-1",
            "library": {"path": "synthetic.npz"},
            "decay": {"primary": "synthetic.dat"},
            "material": {"mass_g": 1.0, "basis": "wt_percent", "composition": {"Fe56": 100.0}},
            "spectrum": {"structure": "custom", "boundaries_eV": [1.0, 2.0, 3.0, 4.0, 5.0],
                         "flux_per_group": [1.25e23, 1.25e23, 1.25e23, 1.25e23], "descending": false},
            "schedule": schedule,
            "options": {"mode": "coupled", "prune": "none"}
        }))
        .expect("synthetic switching spec parses")
    }

    fn prepared(with_step_library: bool) -> PreparedRun {
        let nuclides: HashMap<(i32, i32), decay::Nuclide> = HashMap::from([
            (TARGET, stand_in(TARGET)),
            (PRODUCT_A, stand_in(PRODUCT_A)),
            (PRODUCT_B, stand_in(PRODUCT_B)),
        ]);
        let chain = chain::build(&nuclides);
        let step_libraries = if with_step_library {
            vec![PreparedStepLibrary {
                reference: LibraryRef {
                    path: "synthetic_b.npz".into(),
                    sha256: None,
                },
                projectile: Projectile::Neutron,
                library_sha: "1".repeat(64),
                index_path: "synthetic_b_index.json".into(),
                index_sha: "2".repeat(64),
                library: ActivationLibrary::Dense(one_channel_library(
                    105,
                    PRODUCT_B,
                    vec![1.0, 3.0, 5.0],
                )),
                library_targets: vec![TARGET],
                group_structure: None,
            }]
        } else {
            Vec::new()
        };
        PreparedRun {
            library_path: "synthetic.npz".into(),
            library_sha_declared: None,
            library_sha: "0".repeat(64),
            index_path: "synthetic_index.json".into(),
            index_sha: "0".repeat(64),
            index: serde_json::json!({}),
            library: ActivationLibrary::Dense(one_channel_library(
                102,
                PRODUCT_A,
                vec![1.0, 2.0, 3.0, 4.0, 5.0],
            )),
            library_targets: vec![TARGET],
            decay_primary: "synthetic.dat".into(),
            decay_primary_sha: "0".repeat(64),
            decay_fallback: None,
            decay_fallback_sha: None,
            decay_overrides_sha: None,
            decay_overrides_applied: Vec::new(),
            nuclides,
            decay_fallback_keys: HashSet::new(),
            chain,
            gas_missing_light_states: Vec::new(),
            decay_nuclides_from_fallback: 0,
            photon_options: PhotonOptions::default(),
            response: None,
            response_sha: None,
            fission_options: FissionYieldOptions::default(),
            fission_yields: HashMap::new(),
            fission_yield_inputs: Vec::new(),
            spontaneous: None,
            projectile: Projectile::Neutron,
            library_group_structure: None,
            temperature_K: Kelvin::new(293.6).expect("293.6 K is valid"),
            uncertainty_options: None,
            covariance: None,
            unmodeled_table: None,
            unmodeled_evalspread: None,
            radiological: None,
            damage: None,
            shielding: None,
            step_libraries,
        }
    }

    fn solve(spec: &Spec, prepared: &PreparedRun) -> Result<RunResult, String> {
        let physical = spec.physical_inputs()?;
        let mut profiler = RunProfiler::disabled();
        prepared.run_started_profiled(
            spec,
            &physical,
            "test",
            std::time::Instant::now(),
            &mut profiler,
            None,
            None,
        )
    }

    fn atoms(step: &StepOut, nuclide: &str) -> f64 {
        step.inventory
            .iter()
            .find(|entry| entry.nuclide == nuclide)
            .map_or(0.0, |entry| entry.atoms_per_g)
    }

    fn close(actual: f64, expected: f64) {
        assert!(
            (actual - expected).abs() <= 1e-9 * expected.abs().max(1e-300),
            "{actual} vs {expected}"
        );
    }

    #[test]
    fn inventory_carries_across_the_switch_and_the_second_library_applies_only_after_it() {
        let result = solve(&spec_json(true), &prepared(true)).expect("switching run succeeds");
        assert_eq!(result.steps.len(), 3);
        // all three nuclides are stable here, so the total is the initial inventory
        let n0: f64 = ["Fe56", "Fe57", "Co59"]
            .iter()
            .map(|name| atoms(&result.steps[2], name))
            .sum();
        assert!(n0 > 1.0e21, "{n0}");

        // step 1, base spectrum, library A: Fe56 -> Fe57 at RATE_A and no Co59 at all
        let n1 = n0 * (-RATE_A * 1.0).exp();
        let a1 = n0 - n1;
        close(atoms(&result.steps[0], "Fe56"), n1);
        close(atoms(&result.steps[0], "Fe57"), a1);
        assert!(atoms(&result.steps[0], "Co59") < 1.0e-9 * n0);

        // step 2, library B: the Fe56 left by step 1 depletes at RATE_B into Co59, and the Fe57 made before
        // the switch is carried across untouched
        let n2 = n1 * (-RATE_B * 2.0).exp();
        close(atoms(&result.steps[1], "Fe56"), n2);
        close(atoms(&result.steps[1], "Co59"), n1 - n2);
        close(atoms(&result.steps[1], "Fe57"), a1);

        // step 3, cooling on library B's context with flux 0: nothing is produced or lost
        close(atoms(&result.steps[2], "Fe56"), n2);
        close(atoms(&result.steps[2], "Co59"), n1 - n2);
        close(atoms(&result.steps[2], "Fe57"), a1);
    }

    #[test]
    fn single_library_schedule_is_unchanged_and_reports_no_switch() {
        let result = solve(&spec_json(false), &prepared(false)).expect("plain run succeeds");
        assert!(result.ledger.get("step_libraries").is_none());
        assert!(result.certificate["inputs"].get("step_libraries").is_none());
        // Library A applies to step 2 as well: Fe57 keeps growing and Co59 never appears.
        let n0: f64 = ["Fe56", "Fe57"]
            .iter()
            .map(|name| atoms(&result.steps[2], name))
            .sum();
        close(atoms(&result.steps[1], "Fe56"), n0 * (-RATE_A * 3.0).exp());
        assert!(atoms(&result.steps[1], "Co59") < 1.0e-9 * n0);
    }

    #[test]
    fn ledger_and_certificate_name_the_switched_library() {
        let result = solve(&spec_json(true), &prepared(true)).expect("switching run succeeds");
        let block = &result.ledger["step_libraries"];
        let library = &block["libraries"][0];
        assert_eq!(library["library"], serde_json::json!("synthetic_b.npz"));
        assert_eq!(library["steps"], serde_json::json!([2, 3]));
        assert_eq!(library["projectile"], serde_json::json!("neutron"));
        // step 2 only: 2 s at a spectrum total of 3e23; the cooling step contributes none
        close(
            library["fluence_particles_cm2"].as_f64().unwrap(),
            2.0 * 3.0e23,
        );
        assert!(block["pathways_suppressed"].is_null());
        let input = &result.certificate["inputs"]["step_libraries"][0];
        assert_eq!(
            input["library"]["path"],
            serde_json::json!("synthetic_b.npz")
        );
        assert_eq!(input["projectile"], serde_json::json!("neutron"));
    }

    #[test]
    fn a_prepared_run_without_the_schedule_libraries_is_refused() {
        let error = solve(&spec_json(true), &prepared(false)).err().unwrap();
        assert!(
            error.contains("schedule libraries do not match the prepared data"),
            "{error}"
        );
    }

    #[test]
    fn a_step_spectrum_must_have_the_step_librarys_group_count() {
        let mut spec = spec_json(true);
        spec.schedule[1].spectrum = Some(
            serde_json::from_value(serde_json::json!({
                "structure": "custom",
                "boundaries_eV": [1.0, 2.0, 3.0, 4.0],
                "flux_per_group": [1.0e23, 1.0e23, 1.0e23],
            }))
            .expect("three-group spectrum parses"),
        );
        let error = solve(&spec, &prepared(true)).err().unwrap();
        assert!(
            error.contains("has 3 groups but activation library 'synthetic_b.npz' has 2"),
            "{error}"
        );
    }

    #[test]
    fn a_step_library_run_refuses_single_library_features_at_run_time() {
        let mut spec = spec_json(true);
        spec.options.gas = true;
        let error = solve(&spec, &prepared(true)).err().unwrap();
        assert!(
            error.contains("cannot be combined with options.gas"),
            "{error}"
        );
    }

    #[test]
    fn depletion_table_follows_each_steps_own_library() {
        let mut spec = spec_json(true);
        spec.summary = Some(crate::spec::SummaryOptions {
            depletion: true,
            ..Default::default()
        });
        let result = solve(&spec, &prepared(true)).expect("switching run succeeds");
        let removal = |step: usize| {
            let table = result.steps[step]
                .summary
                .as_ref()
                .and_then(|summary| summary.depletion.as_ref())
                .expect("depletion table is present");
            assert_eq!(table.nuclides.len(), 1);
            assert_eq!(table.nuclides[0].nuclide, "Fe56");
            table.nuclides[0].removal_rate_per_s
        };
        // library A under the base spectrum, library B under its own, then flux off
        close(removal(0), RATE_A);
        close(removal(1), RATE_B);
        assert_eq!(removal(2), 0.0);
    }

    // ---- P140 regressions (SG-04, SG-05): they assert the intended behaviour ----

    #[test]
    fn p140_sg05_step_library_ledger_lists_material_nuclides_without_rows() {
        // Library B has rows for Co59 only, while the material is Fe56 alone: on the steps that use B
        // the Fe56 contributes no rate at all, and the ledger has to say so.
        let mut run_data = prepared(true);
        run_data.step_libraries[0].library =
            ActivationLibrary::Dense(one_channel_library(105, PRODUCT_A, vec![1.0, 3.0, 5.0]));
        run_data.step_libraries[0].library_targets = vec![PRODUCT_B];
        let result = solve(&spec_json(true), &run_data).expect("switching run succeeds");
        let library = &result.ledger["step_libraries"]["libraries"][0];
        let missing = library["material_nuclides_without_rows"]
            .as_array()
            .unwrap_or_else(|| panic!("no material_nuclides_without_rows list in {library}"));
        assert!(
            missing.iter().any(|name| name == "Fe56"),
            "Fe56 has no row in library B: {missing:?}"
        );
        // A library that does carry the nuclide (the default fixture's B) lists nothing of the kind.
        let covered = solve(&spec_json(true), &prepared(true)).expect("switching run succeeds");
        let listed = covered.ledger["step_libraries"]["libraries"][0]
            ["material_nuclides_without_rows"]
            .as_array()
            .unwrap_or_else(|| panic!("no material_nuclides_without_rows list"));
        assert!(listed.is_empty(), "{listed:?}");
    }

    /// The cards of one ENDF-6 section: six right-aligned 11-column fields per record, then SEND.
    fn p140_section(mat: i32, mf: i32, mt: i32, records: &[Vec<String>]) -> Vec<String> {
        let mut lines: Vec<String> = records
            .iter()
            .enumerate()
            .map(|(index, record)| {
                let mut fields = record.clone();
                fields.resize(6, "0".to_string());
                let body: String = fields.iter().map(|field| format!("{field:>11}")).collect();
                format!("{body}{mat:>4}{mf:>2}{mt:>3}{:>5}", index + 1)
            })
            .collect();
        lines.push(format!("{:66}{mat:>4}{mf:>2}{:>3}{:>5}", "", 0, 99_999));
        lines
    }

    /// A neutron tape for Fe56 whose single resolved Breit-Wigner level sits at 2.5 eV.
    fn p140_fe56_tape() -> String {
        let num = |value: f64| format!("{value:.6e}");
        let strings = |items: &[&str]| {
            items
                .iter()
                .map(|item| item.to_string())
                .collect::<Vec<_>>()
        };
        let mf1 = vec![
            vec!["26056".to_string(), num(55.454)],
            vec![],
            vec![
                num(1.0),
                num(2.0e7),
                "0".into(),
                "0".into(),
                "10".into(),
                "6".into(),
            ],
            vec![],
        ];
        let mf2 = vec![
            [
                vec!["26056".to_string(), num(55.454)],
                strings(&["0", "0", "1", "0"]),
            ]
            .concat(),
            [
                vec!["26056".to_string(), num(1.0)],
                strings(&["0", "0", "1", "0"]),
            ]
            .concat(),
            [vec![num(1e-5), num(1e4)], strings(&["1", "1", "0", "0"])].concat(),
            [vec![num(0.0), num(0.7)], strings(&["0", "0", "1", "0"])].concat(),
            [vec![num(55.454), num(0.0)], strings(&["0", "0", "6", "1"])].concat(),
            vec![
                num(2.5),
                num(0.5),
                num(0.051),
                num(0.001),
                num(0.05),
                num(0.0),
            ],
        ];
        let mut lines = p140_section(2631, 1, 451, &mf1);
        lines.extend(p140_section(2631, 2, 151, &mf2));
        lines.join("\n")
    }

    #[test]
    fn p140_sg04_geometry_certificate_method_limits_mention_r_matrix_limited_ranges() {
        let options: crate::spec::SelfShieldingOptions =
            serde_json::from_value(serde_json::json!({
                "geometry": {
                    "shape": "foil",
                    "length_cm": 0.01,
                    "density_g_cm3": 7.874,
                    "resonance_files": [{"path": "fe56.endf", "sha256": "0".repeat(64)}],
                }
            }))
            .unwrap();
        let geometry = crate::shielding::PreparedGeometry::from_evaluations(
            options.geometry.as_ref().unwrap(),
            &[("fe56.endf".to_string(), p140_fe56_tape())],
        )
        .expect("the synthetic Fe56 tape parses");
        let mut run_data = prepared(false);
        run_data.shielding = Some(PreparedShielding {
            options: options.clone(),
            sha256: None,
            table: None,
            geometry: Some(geometry),
            resonance_sha256: vec!["0".repeat(64)],
        });
        let mut spec = spec_json(false);
        spec.self_shielding = Some(options);
        let result = solve(&spec, &run_data).expect("geometry run succeeds");
        let limits = result.certificate["shielding"]["method_limits"]
            .as_array()
            .expect("geometry certificate lists method limits");
        // Expected: the sentence names the R-matrix-limited (LRF=7) support added in P139. Current text
        // says only Breit-Wigner and Reich-Moore are used.
        assert!(
            limits
                .iter()
                .any(|limit| limit.as_str().is_some_and(|text| text.contains("LRF=7"))),
            "method_limits is stale for LRF=7: {limits:?}"
        );
        assert!(
            !limits.iter().any(|limit| limit
                .as_str()
                .is_some_and(|text| text.contains("only Breit-Wigner and Reich-Moore"))),
            "{limits:?}"
        );
    }
}

/// FISPACT-II DEPLETION table on a synthetic chain run through the full per-step solve. The
/// radioactive target Fe56 (lambda 0.25 s^-1) has two reactions, MT102 at 1 b and MT16 at 2 b, so
/// under a 5e23 flux the channels remove it at 0.5 and 1.0 s^-1 and every product is stable; the
/// target is fed by nothing, so its population is the exact exponential of the total removal.
#[cfg(test)]
mod depletion_prepared_tests {
    use super::*;
    use std::collections::HashSet;

    const TARGET: (i32, i32) = (26_056, 0);
    const DAUGHTER: (i32, i32) = (27_056, 0);
    const CAPTURE_PRODUCT: (i32, i32) = (26_057, 0);
    const N2N_PRODUCT: (i32, i32) = (26_055, 0);
    const LAMBDA: f64 = 0.25;
    const RATE_CAPTURE: f64 = 0.5;
    const RATE_N2N: f64 = 1.0;
    const N0: f64 = 1.0e20;

    fn stand_in(za: (i32, i32), half_life: f64) -> decay::Nuclide {
        let mut nuclide = decay::Nuclide {
            mat: 0,
            za: za.0,
            awr: (za.0 % 1000) as f64,
            liso: za.1,
            nst: if half_life > 0.0 { 0 } else { 1 },
            half_life,
            d_half_life: 0.0,
            energies: Vec::new(),
            modes: Vec::new(),
            spectra: Vec::new(),
        };
        if half_life > 0.0 {
            // beta-minus decay (RTYP 1.0) to the daughter, the whole branching
            nuclide.modes.push(decay::Mode {
                rtyp: 1.0,
                rfs: 0.0,
                q: 0.0,
                dq: 0.0,
                br: 1.0,
                dbr: 0.0,
            });
        }
        nuclide
    }

    fn library() -> library::Library {
        let row = |mt: i32, zap: i32, lfs: i32| library::Row {
            target: 0,
            mt,
            zap,
            lfs,
            lmf: if zap == -1 { 0 } else { -1 },
        };
        let mut sig = Vec::new();
        sig.extend([1.0; 8]); // MT102 loss row and product row
        sig.extend([2.0; 8]); // MT16 loss row and product row
        library::Library {
            rows: vec![
                row(102, -1, -1),
                row(102, CAPTURE_PRODUCT.0, 0),
                row(16, -1, -1),
                row(16, N2N_PRODUCT.0, 0),
            ],
            sig,
            ngroups: 4,
            bounds: vec![1.0, 2.0, 3.0, 4.0, 5.0],
        }
    }

    fn prepared(radioactive: bool) -> PreparedRun {
        let nuclides: HashMap<(i32, i32), decay::Nuclide> = HashMap::from([
            (
                TARGET,
                stand_in(
                    TARGET,
                    if radioactive {
                        std::f64::consts::LN_2 / LAMBDA
                    } else {
                        0.0
                    },
                ),
            ),
            (DAUGHTER, stand_in(DAUGHTER, 0.0)),
            (CAPTURE_PRODUCT, stand_in(CAPTURE_PRODUCT, 0.0)),
            (N2N_PRODUCT, stand_in(N2N_PRODUCT, 0.0)),
        ]);
        let chain = chain::build(&nuclides);
        PreparedRun {
            library_path: "synthetic.npz".into(),
            library_sha_declared: None,
            library_sha: "0".repeat(64),
            index_path: "synthetic_index.json".into(),
            index_sha: "0".repeat(64),
            index: serde_json::json!({}),
            library: ActivationLibrary::Dense(library()),
            library_targets: vec![TARGET],
            decay_primary: "synthetic.dat".into(),
            decay_primary_sha: "0".repeat(64),
            decay_fallback: None,
            decay_fallback_sha: None,
            decay_overrides_sha: None,
            decay_overrides_applied: Vec::new(),
            nuclides,
            decay_fallback_keys: HashSet::new(),
            chain,
            gas_missing_light_states: Vec::new(),
            decay_nuclides_from_fallback: 0,
            photon_options: PhotonOptions::default(),
            response: None,
            response_sha: None,
            fission_options: FissionYieldOptions::default(),
            fission_yields: HashMap::new(),
            fission_yield_inputs: Vec::new(),
            spontaneous: None,
            projectile: Projectile::Neutron,
            library_group_structure: None,
            temperature_K: Kelvin::new(293.6).expect("293.6 K is valid"),
            uncertainty_options: None,
            covariance: None,
            unmodeled_table: None,
            unmodeled_evalspread: None,
            radiological: None,
            damage: None,
            shielding: None,
            step_libraries: Vec::new(),
        }
    }

    fn spec(mode: &str, summary: Option<serde_json::Value>) -> Spec {
        let mut value = serde_json::json!({
            "spec": "actinv-spec-1",
            "library": {"path": "synthetic.npz"},
            "decay": {"primary": "synthetic.dat"},
            "material": {"mass_g": 1.0, "basis": "atoms_per_g", "composition": {"Fe56": N0}},
            "spectrum": {"structure": "custom", "boundaries_eV": [1.0, 2.0, 3.0, 4.0, 5.0],
                         "flux_per_group": [1.25e23, 1.25e23, 1.25e23, 1.25e23],
                         "descending": false},
            "schedule": [
                {"dt": "1 s", "flux": 1.0},
                {"dt": "2 s", "flux": 0.5},
                {"dt": "3 s", "flux": 0.0},
            ],
            "options": {"mode": mode, "prune": "none"},
        });
        if let Some(summary) = summary {
            value["summary"] = summary;
        }
        serde_json::from_value(value).expect("synthetic depletion spec parses")
    }

    fn solve(spec: &Spec, prepared: &PreparedRun) -> RunResult {
        let physical = spec.physical_inputs().expect("physical inputs");
        let mut profiler = RunProfiler::disabled();
        prepared
            .run_started_profiled(
                spec,
                &physical,
                "test",
                std::time::Instant::now(),
                &mut profiler,
                None,
                None,
            )
            .expect("synthetic depletion run succeeds")
    }

    fn close(actual: f64, expected: f64) {
        assert!(
            (actual - expected).abs() <= 1e-9 * expected.abs().max(1e-300),
            "{actual} vs {expected}"
        );
    }

    fn table(result: &RunResult, step: usize) -> &crate::depletion::StepDepletion {
        result.steps[step]
            .summary
            .as_ref()
            .and_then(|summary| summary.depletion.as_ref())
            .expect("depletion table is present")
    }

    fn rate_of(nuclide: &crate::depletion::DepletionNuclide, channel: &str) -> Option<f64> {
        nuclide
            .channels
            .iter()
            .find(|entry| entry.channel == channel)
            .map(|entry| entry.rate_per_s)
    }

    #[test]
    fn coupled_table_matches_the_analytic_depletion_channel_by_channel() {
        let spec = spec("coupled", Some(serde_json::json!({"depletion": true})));
        let result = solve(&spec, &prepared(true));
        assert_eq!(result.steps.len(), 3);
        // (flux multiplier, seconds) of the three steps
        let schedule = [(1.0, 1.0), (0.5, 2.0), (0.0, 3.0)];
        let mut expected_start = N0;
        for (step, (multiplier, seconds)) in schedule.into_iter().enumerate() {
            let step_table = table(&result, step);
            assert_eq!(step_table.model, "coupled");
            assert_eq!(step_table.nuclides.len(), 1);
            let nuclide = &step_table.nuclides[0];
            assert_eq!(nuclide.nuclide, "Fe56");
            let capture = RATE_CAPTURE * multiplier;
            let n2n = RATE_N2N * multiplier;
            let total = LAMBDA + capture + n2n;
            close(nuclide.removal_rate_per_s, total);
            close(rate_of(nuclide, "decay").unwrap(), LAMBDA);
            if multiplier > 0.0 {
                close(rate_of(nuclide, "MT102").unwrap(), capture);
                close(rate_of(nuclide, "MT16").unwrap(), n2n);
                assert_eq!(nuclide.channels.len(), 3);
            } else {
                assert!(rate_of(nuclide, "MT102").is_none());
                assert_eq!(nuclide.channels.len(), 1);
            }
            // shares sum to one and the ranking is descending
            let share_sum: f64 = nuclide.channels.iter().map(|channel| channel.share).sum();
            assert!((share_sum - 1.0).abs() < 1e-12, "{share_sum}");
            for channel in &nuclide.channels {
                close(channel.share, channel.rate_per_s / total);
            }
            assert!(nuclide
                .channels
                .windows(2)
                .all(|pair| pair[0].rate_per_s >= pair[1].rate_per_s));
            // atoms: the start is the previous end, the end the solver's own population
            let expected_end = expected_start * (-total * seconds).exp();
            close(nuclide.atoms_start_per_g, expected_start);
            close(nuclide.atoms_end_per_g, expected_end);
            let solved = result.steps[step]
                .inventory
                .iter()
                .find(|entry| entry.nuclide == "Fe56")
                .map_or(0.0, |entry| entry.atoms_per_g);
            assert_eq!(nuclide.atoms_end_per_g, solved);
            close(nuclide.depleted_atoms_per_g, expected_start - expected_end);
            close(
                nuclide.depleted_fraction,
                (expected_start - expected_end) / expected_start,
            );
            close(nuclide.cumulative_depleted_atoms_per_g, N0 - expected_end);
            close(
                nuclide.cumulative_depleted_fraction,
                (N0 - expected_end) / N0,
            );
            assert_eq!(nuclide.initial_atoms_per_g, N0);
            expected_start = expected_end;
        }
        // decay-only cooling step: the whole removal is decay
        let cooling = &table(&result, 2).nuclides[0];
        assert_eq!(cooling.channels[0].channel, "decay");
        assert_eq!(cooling.channels[0].kind, "decay");
        assert!((cooling.channels[0].share - 1.0).abs() < 1e-12);
        // no covariance in this run: no uncertainty keys at all
        let json = serde_json::to_value(table(&result, 0)).unwrap();
        let nuclide = &json["nuclides"][0];
        assert!(nuclide
            .get("removal_rate_relative_standard_uncertainty")
            .is_none());
        assert!(nuclide.get("uncertainty_covered_share").is_none());
        assert!(nuclide["channels"][0]
            .get("relative_standard_uncertainty")
            .is_none());
        assert_eq!(nuclide["channels"][0]["channel"], "MT16");
        assert_eq!(nuclide["channels"][0]["reaction"], "(n,2n)");
        // the request is recorded in the ledger and the certificate
        assert_eq!(result.ledger["depletion"]["requested"], true);
        assert_eq!(result.ledger["depletion"]["model"], "coupled");
        assert_eq!(result.ledger["depletion"]["uncertainty"], "none");
        assert!(result.certificate.get("depletion").is_some());
    }

    #[test]
    fn trace_mode_depletes_the_reservoir_analytically_and_leaves_the_rest_alone() {
        let spec = spec("trace", Some(serde_json::json!({"depletion": true})));
        let result = solve(&spec, &prepared(false));
        assert_eq!(result.ledger["mode"], "trace");
        assert_eq!(result.ledger["depletion"]["model"], "trace_analytic");
        let first = &table(&result, 0);
        assert_eq!(first.model, "trace_analytic");
        let nuclide = &first.nuclides[0];
        close(nuclide.removal_rate_per_s, RATE_CAPTURE + RATE_N2N);
        close(nuclide.atoms_end_per_g, N0 * (-1.5f64).exp());
        close(nuclide.depleted_fraction, 1.0 - (-1.5f64).exp());
        // step 2 runs at half the flux for two seconds: another exp(-0.75 x 2)
        let second = &table(&result, 1).nuclides[0];
        close(second.atoms_start_per_g, nuclide.atoms_end_per_g);
        close(second.atoms_end_per_g, N0 * (-3.0f64).exp());
        close(second.cumulative_depleted_fraction, 1.0 - (-3.0f64).exp());
        // a stable target with the flux off has nothing left to remove it
        let cooling = &table(&result, 2).nuclides[0];
        assert!(cooling.channels.is_empty());
        assert_eq!(cooling.atoms_start_per_g, cooling.atoms_end_per_g);
        // the ordinary inventory table still holds the reservoir constant
        let summary = result.steps[0].summary.as_ref().unwrap();
        let reservoir = summary
            .nuclides
            .as_ref()
            .and_then(|rows| rows.iter().find(|row| row.nuclide == "Fe56"))
            .expect("Fe56 row");
        close(reservoir.atoms, N0);
    }

    #[test]
    fn default_runs_carry_no_depletion_keys_and_the_rest_is_unchanged() {
        let without = solve(
            &spec("coupled", Some(serde_json::json!({}))),
            &prepared(true),
        );
        let explicit_off = solve(
            &spec("coupled", Some(serde_json::json!({"depletion": false}))),
            &prepared(true),
        );
        let with = solve(
            &spec("coupled", Some(serde_json::json!({"depletion": true}))),
            &prepared(true),
        );
        let plain = solve(&spec("coupled", None), &prepared(true));
        for result in [&without, &explicit_off] {
            assert!(result.ledger.get("depletion").is_none());
            assert!(result.certificate.get("depletion").is_none());
            for step in &result.steps {
                let summary = serde_json::to_value(step.summary.as_ref().unwrap()).unwrap();
                assert!(summary.get("depletion").is_none());
            }
        }
        assert!(plain.ledger.get("depletion").is_none());
        assert!(plain.certificate.get("depletion").is_none());
        assert!(plain.steps.iter().all(|step| step.summary.is_none()));
        // the opt-in key is the only difference from the same summary without it
        for (a, b) in without.steps.iter().zip(&explicit_off.steps) {
            assert_eq!(
                serde_json::to_value(a.summary.as_ref().unwrap()).unwrap(),
                serde_json::to_value(b.summary.as_ref().unwrap()).unwrap()
            );
        }
        for (a, b) in without.steps.iter().zip(&with.steps) {
            let mut stripped = serde_json::to_value(b.summary.as_ref().unwrap()).unwrap();
            stripped.as_object_mut().unwrap().remove("depletion");
            assert_eq!(
                serde_json::to_value(a.summary.as_ref().unwrap()).unwrap(),
                stripped
            );
            assert_eq!(a.inventory.len(), b.inventory.len());
            for (x, y) in a.inventory.iter().zip(&b.inventory) {
                assert_eq!(x.atoms_per_g, y.atoms_per_g);
            }
        }
    }

    #[test]
    fn spec_option_parses_and_defaults_off() {
        let parsed: crate::spec::SummaryOptions =
            serde_json::from_value(serde_json::json!({})).unwrap();
        assert!(!parsed.depletion);
        // serialising the default leaves the key out, so existing specs keep their bytes
        let json = serde_json::to_value(&parsed).unwrap();
        assert!(json.get("depletion").is_none());
        let on: crate::spec::SummaryOptions =
            serde_json::from_value(serde_json::json!({"depletion": true})).unwrap();
        assert!(on.depletion);
        assert_eq!(serde_json::to_value(&on).unwrap()["depletion"], true);
    }

    // ---- P140 H1 regression tests (hardening pass) ----

    /// The synthetic spec with a single step of `dt`, flux multiplier `flux` and a first-order
    /// `removal` map, depletion on.
    fn removal_spec(mode: &str, dt: &str, flux: f64, removal: serde_json::Value) -> Spec {
        let value = serde_json::json!({
            "spec": "actinv-spec-1",
            "library": {"path": "synthetic.npz"},
            "decay": {"primary": "synthetic.dat"},
            "material": {"mass_g": 1.0, "basis": "atoms_per_g", "composition": {"Fe56": N0}},
            "spectrum": {"structure": "custom", "boundaries_eV": [1.0, 2.0, 3.0, 4.0, 5.0],
                         "flux_per_group": [1.25e23, 1.25e23, 1.25e23, 1.25e23],
                         "descending": false},
            "schedule": [{"dt": dt, "flux": flux, "removal": removal}],
            "options": {"mode": mode, "prune": "none"},
            "summary": {"depletion": true},
        });
        serde_json::from_value(value).expect("synthetic removal spec parses")
    }

    /// The channels explain the whole removal: their rates sum to `removal_rate_per_s` and their
    /// shares to one.
    fn assert_channels_explain_removal(nuclide: &crate::depletion::DepletionNuclide) {
        assert!(
            !nuclide.channels.is_empty(),
            "no channel carries the removal (removal_rate_per_s {})",
            nuclide.removal_rate_per_s
        );
        let rates: f64 = nuclide.channels.iter().map(|c| c.rate_per_s).sum();
        close(rates, nuclide.removal_rate_per_s);
        let shares: f64 = nuclide.channels.iter().map(|c| c.share).sum();
        assert!((shares - 1.0).abs() < 1e-12, "{shares}");
    }

    #[test]
    fn p140_dep_f1_removal_is_a_channel() {
        // Stable Fe56, flux off, scheduled first-order removal 1e-3 s^-1 for 1000 s. The state
        // falls by exp(-1); the matrix diagonal is exactly the declared removal.
        let spec = removal_spec(
            "coupled",
            "1000 s",
            0.0,
            serde_json::json!({"Fe56": 1.0e-3}),
        );
        let result = solve(&spec, &prepared(false));
        let nuclide = &table(&result, 0).nuclides[0];
        assert_eq!(nuclide.nuclide, "Fe56");
        close(nuclide.depleted_fraction, 1.0 - (-1.0f64).exp());
        close(nuclide.removal_rate_per_s, 1.0e-3);
        assert_channels_explain_removal(nuclide);
        assert!(nuclide.removal_rate_per_s.is_sign_positive());
    }

    #[test]
    fn p140_dep_f1_removal_adds_to_decay_and_reaction_channels() {
        // Radioactive Fe56 under flux 1 (capture 0.5, n2n 1.0, decay 0.25) plus removal 0.1: the
        // diagonal is 1.85 and the table must say so; the state follows exp(-1.85 dt).
        let spec = removal_spec("coupled", "1 s", 1.0, serde_json::json!({"Fe56": 0.1}));
        let result = solve(&spec, &prepared(true));
        let nuclide = &table(&result, 0).nuclides[0];
        close(nuclide.atoms_end_per_g, N0 * (-1.85f64).exp());
        close(nuclide.removal_rate_per_s, 1.85);
        assert_channels_explain_removal(nuclide);
        // the table's own rate reproduces the solved depletion of this unfed nuclide
        let implied = (nuclide.atoms_start_per_g / nuclide.atoms_end_per_g).ln();
        close(implied, nuclide.removal_rate_per_s);
    }

    #[test]
    fn p140_dep_f1_element_removal_is_a_channel() {
        let spec = removal_spec("coupled", "1000 s", 0.0, serde_json::json!({"Fe": 1.0e-3}));
        let result = solve(&spec, &prepared(false));
        let nuclide = &table(&result, 0).nuclides[0];
        close(nuclide.depleted_fraction, 1.0 - (-1.0f64).exp());
        close(nuclide.removal_rate_per_s, 1.0e-3);
        assert_channels_explain_removal(nuclide);
    }

    #[test]
    fn p140_dep_negzero_cooling_step_removal_rate_is_positive_zero() {
        // Stable Fe56 in the flux-off third step has no channel: removal_rate_per_s is 0.0, not
        // -0.0, in the struct and in the serialised table.
        let spec = spec("coupled", Some(serde_json::json!({"depletion": true})));
        let result = solve(&spec, &prepared(false));
        let cooling = &table(&result, 2);
        let nuclide = &cooling.nuclides[0];
        assert!(nuclide.channels.is_empty());
        assert_eq!(nuclide.removal_rate_per_s, 0.0);
        assert!(
            nuclide.removal_rate_per_s.is_sign_positive(),
            "removal_rate_per_s is negative zero"
        );
        let json = serde_json::to_string(cooling).unwrap();
        assert!(!json.contains("-0.0"), "{json}");
    }
}

/// P125 end-to-end tests: a hand-built `PreparedRun` (as in `gas_prepared_tests`) run through the
/// real per-step solve. The fission count is checked against the analytic depletion of a nuclide
/// whose only loss is fission, and spontaneous-fission routing, count and neutron source against
/// the decay law of a pure spontaneous-fission parent. The multiplicity and yields are synthetic.
#[cfg(test)]
mod fission_prepared_tests {
    use super::*;
    use std::collections::HashSet;

    const FUEL: (i32, i32) = (92_235, 0);
    const SF_PARENT: (i32, i32) = (98_252, 0);
    const FRAGMENT_A: (i32, i32) = (54_140, 0);
    const FRAGMENT_B: (i32, i32) = (38_094, 0);
    const SF_HALF_LIFE_S: f64 = 1.0e6;

    fn nuclide(za: (i32, i32), half_life: f64, modes: Vec<decay::Mode>) -> decay::Nuclide {
        decay::Nuclide {
            mat: 0,
            za: za.0,
            awr: (za.0 % 1000) as f64,
            liso: za.1,
            nst: if half_life > 0.0 { 0 } else { 1 },
            half_life,
            d_half_life: 0.0,
            energies: Vec::new(),
            modes,
            spectra: Vec::new(),
        }
    }

    fn spontaneous() -> PreparedSpontaneous {
        PreparedSpontaneous {
            yield_inputs: Vec::new(),
            // Two fragments of yield 1: two product atoms per fission.
            yields: HashMap::from([(
                SF_PARENT,
                BTreeMap::from([(FRAGMENT_A, 1.0), (FRAGMENT_B, 1.0)]),
            )]),
            nubar: BTreeMap::from([(SF_PARENT, (3.0, "test table".to_string()))]),
            nubar_inputs: Vec::new(),
        }
    }

    /// The fuel (stable stand-in) has a fission row and a loss row of equal cross section, so all
    /// its depletion is fission. The parent decays only by spontaneous fission.
    fn prepared_run(spontaneous: Option<PreparedSpontaneous>) -> PreparedRun {
        let lib = library::Library {
            rows: vec![
                library::Row {
                    target: 0,
                    mt: 18,
                    zap: 0,
                    lfs: 0,
                    lmf: 3,
                },
                library::Row {
                    target: 0,
                    mt: 1,
                    zap: -1,
                    lfs: 0,
                    lmf: 3,
                },
            ],
            sig: vec![1.0; 2 * 4],
            ngroups: 4,
            bounds: vec![1.0, 2.0, 3.0, 4.0, 5.0],
        };
        let sf_mode = decay::Mode {
            rtyp: 6.0,
            rfs: 0.0,
            q: 0.0,
            dq: 0.0,
            br: 1.0,
            dbr: 0.0,
        };
        let nuclides: HashMap<(i32, i32), decay::Nuclide> = HashMap::from([
            (FUEL, nuclide(FUEL, 0.0, Vec::new())),
            (SF_PARENT, nuclide(SF_PARENT, SF_HALF_LIFE_S, vec![sf_mode])),
            (FRAGMENT_A, nuclide(FRAGMENT_A, 0.0, Vec::new())),
            (FRAGMENT_B, nuclide(FRAGMENT_B, 0.0, Vec::new())),
        ]);
        let chain = chain::build_with_spontaneous(
            &nuclides,
            spontaneous.as_ref().map(|prepared| &prepared.yields),
        );
        PreparedRun {
            library_path: "synthetic.npz".into(),
            library_sha_declared: None,
            library_sha: "0".repeat(64),
            index_path: "synthetic_index.json".into(),
            index_sha: "0".repeat(64),
            index: serde_json::json!({}),
            library: ActivationLibrary::Dense(lib),
            library_targets: vec![FUEL],
            decay_primary: "synthetic.dat".into(),
            decay_primary_sha: "0".repeat(64),
            decay_fallback: None,
            decay_fallback_sha: None,
            decay_overrides_sha: None,
            decay_overrides_applied: Vec::new(),
            nuclides,
            decay_fallback_keys: HashSet::new(),
            chain,
            gas_missing_light_states: Vec::new(),
            decay_nuclides_from_fallback: 0,
            photon_options: PhotonOptions::default(),
            response: None,
            response_sha: None,
            fission_options: FissionYieldOptions::default(),
            fission_yields: HashMap::new(),
            fission_yield_inputs: Vec::new(),
            spontaneous,
            projectile: Projectile::Neutron,
            library_group_structure: None,
            temperature_K: Kelvin::new(293.6).expect("293.6 K is valid"),
            uncertainty_options: None,
            covariance: None,
            unmodeled_table: None,
            unmodeled_evalspread: None,
            radiological: None,
            damage: None,
            shielding: None,
            step_libraries: Vec::new(),
        }
    }

    fn spec(composition: serde_json::Value, dt: &str, fission: serde_json::Value) -> Spec {
        serde_json::from_value(serde_json::json!({
            "spec": "actinv-spec-1",
            "library": {"path": "synthetic.npz"},
            "decay": {"primary": "synthetic.dat"},
            "material": {"mass_g": 1.0, "basis": "wt_percent", "composition": composition},
            "spectrum": {"structure": "custom", "boundaries_eV": [1.0, 2.0, 3.0, 4.0, 5.0],
                         "flux_per_group": [1.0e12, 1.0e12, 1.0e12, 1.0e12], "descending": false},
            "schedule": [{"dt": dt, "flux": 1.0}],
            "options": {"mode": "coupled", "prune": "none",
                        "outputs": ["inventory", "activity", "heat"], "fission": fission}
        }))
        .expect("synthetic fission spec parses")
    }

    fn run_prepared(spec: &Spec, prepared: &PreparedRun) -> RunResult {
        let physical = spec.physical_inputs().expect("physical inputs resolve");
        let mut profiler = RunProfiler::disabled();
        prepared
            .run_started_profiled(
                spec,
                &physical,
                "test",
                std::time::Instant::now(),
                &mut profiler,
                None,
                None,
            )
            .expect("run succeeds")
    }

    fn atoms(step: &StepOut, name: &str) -> f64 {
        step.inventory
            .iter()
            .find(|entry| entry.nuclide == name)
            .map_or(0.0, |entry| entry.atoms_per_g)
    }

    fn close(got: f64, want: f64, rel: f64) {
        assert!(
            (got - want).abs() <= rel * want.abs(),
            "got {got}, want {want}"
        );
    }

    #[test]
    fn induced_fission_count_equals_the_depletion_of_a_fission_only_nuclide() {
        // Collapsed rate k = 1 b x 4e12 cm^-2 s^-1 = 4e-12 /s; k * t = 1 over the step.
        let spec = spec(
            serde_json::json!({"U235": 100.0}),
            "2.5e11 s",
            serde_json::json!({}),
        );
        let result = run_prepared(&spec, &prepared_run(None));
        let step = &result.steps[0];
        let fission = step.fission.as_ref().expect("fission block");
        let count = fission.count.as_ref().expect("fission count block");
        assert!(fission.spontaneous_neutron_source.is_none());
        let n0 = count.initial_heavy_metal_atoms_per_g;
        // The fuel's only loss is fission, so fissions = initial - remaining atoms.
        close(
            count.neutron_induced_fissions_per_g,
            n0 - atoms(step, "U235"),
            1e-9,
        );
        close(
            count.neutron_induced_fissions_per_g,
            n0 * (1.0 - (-1.0f64).exp()),
            1e-9,
        );
        assert_eq!(count.spontaneous_fissions_per_g, 0.0);
        close(
            count.fissions_per_g,
            count.neutron_induced_fissions_per_g,
            1e-15,
        );
        close(
            count.burnup_percent.unwrap(),
            100.0 * (1.0 - (-1.0f64).exp()),
            1e-9,
        );
        // Pure U-235 is 1 g of heavy metal per gram of material: 1e-3 kg.
        close(
            count.burnup_MWd_per_kg_hm.unwrap(),
            count.fissions_per_g * 200.0e6 * EV / 8.64e10 / 1.0e-3,
            1e-12,
        );
        assert_eq!(count.energy_per_fission_MeV, 200.0);
        assert!(
            result.ledger["fission"]["heavy_metal_isotopes_without_mass"]
                .as_array()
                .unwrap()
                .is_empty()
        );
    }

    #[test]
    fn spontaneous_fission_routes_products_counts_fissions_and_sources_neutrons() {
        let spec = spec(
            serde_json::json!({"Cf252": 100.0}),
            "1e6 s",
            serde_json::json!({"energy_per_fission_MeV": 180.0}),
        );
        let result = run_prepared(&spec, &prepared_run(Some(spontaneous())));
        let step = &result.steps[0];
        let fission = step.fission.as_ref().expect("fission block");
        let count = fission.count.as_ref().expect("count block");
        let n0 = count.initial_heavy_metal_atoms_per_g;
        // One half-life of a pure SF parent: half the atoms decayed, each decay one fission.
        close(count.spontaneous_fissions_per_g, 0.5 * n0, 1e-9);
        assert!(count.neutron_induced_fissions_per_g <= 1e-12 * n0);
        close(count.burnup_percent.unwrap(), 50.0, 1e-9);
        assert_eq!(count.energy_per_fission_MeV, 180.0);
        // Two product atoms per fission, and nothing booked to leakage.
        close(atoms(step, "Xe140"), 0.5 * n0, 1e-9);
        close(atoms(step, "Sr94"), 0.5 * n0, 1e-9);
        assert!(step.leakage_atoms_per_g <= 1e-9 * n0);
        assert_eq!(result.ledger["spontaneous_fission_branches_to_leakage"], 0);
        assert_eq!(
            result.ledger["spontaneous_fission"]["routed_to_products"],
            serde_json::json!(["Cf252"])
        );
        assert_eq!(
            result.ledger["spontaneous_fission"]["uncovered_to_leakage"],
            serde_json::json!([])
        );
        // Neutron source: lambda * N(t) * nu-bar.
        let source = fission
            .spontaneous_neutron_source
            .as_ref()
            .expect("neutron source block");
        let lambda = std::f64::consts::LN_2 / SF_HALF_LIFE_S;
        close(source.neutrons_per_s_per_g, lambda * 0.5 * n0 * 3.0, 1e-9);
        close(
            source.by_nuclide["Cf252"],
            source.neutrons_per_s_per_g,
            1e-15,
        );
        assert_eq!(source.uncovered_sf_decays_per_s_per_g, 0.0);
    }

    #[test]
    fn spontaneous_fission_without_yields_stays_on_leakage_but_is_still_counted() {
        let spec = spec(
            serde_json::json!({"Cf252": 100.0}),
            "1e6 s",
            serde_json::json!({}),
        );
        let result = run_prepared(&spec, &prepared_run(None));
        let step = &result.steps[0];
        let count = step
            .fission
            .as_ref()
            .and_then(|fission| fission.count.as_ref())
            .expect("count block");
        let n0 = count.initial_heavy_metal_atoms_per_g;
        close(count.spontaneous_fissions_per_g, 0.5 * n0, 1e-9);
        assert_eq!(atoms(step, "Xe140"), 0.0);
        assert_eq!(atoms(step, "Sr94"), 0.0);
        close(step.leakage_atoms_per_g, 0.5 * n0, 1e-9);
        assert_eq!(result.ledger["spontaneous_fission_branches_to_leakage"], 1);
        assert!(result.ledger.get("spontaneous_fission").is_none());
    }

    #[test]
    fn default_run_has_no_fission_output_and_no_fission_ledger_keys() {
        let mut value = serde_json::json!({
            "spec": "actinv-spec-1",
            "library": {"path": "synthetic.npz"},
            "decay": {"primary": "synthetic.dat"},
            "material": {"mass_g": 1.0, "basis": "wt_percent", "composition": {"Cf252": 100.0}},
            "spectrum": {"structure": "custom", "boundaries_eV": [1.0, 2.0, 3.0, 4.0, 5.0],
                         "flux_per_group": [1.0e12, 1.0e12, 1.0e12, 1.0e12], "descending": false},
            "schedule": [{"dt": "1e6 s", "flux": 1.0}],
            "options": {"mode": "coupled", "prune": "none", "outputs": ["inventory"]}
        });
        value["options"]["outputs"] = serde_json::json!(["inventory", "activity", "heat"]);
        let spec: Spec = serde_json::from_value(value).expect("default spec parses");
        let result = run_prepared(&spec, &prepared_run(None));
        assert!(result.steps[0].fission.is_none());
        assert!(serde_json::to_value(&result.steps[0])
            .unwrap()
            .get("fission")
            .is_none());
        assert!(result.ledger.get("fission").is_none());
        assert!(result.ledger.get("spontaneous_fission").is_none());
    }
}
