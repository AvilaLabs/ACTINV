#![allow(non_snake_case)] // field names are the JSON wire format (Z, A, LISO, heat_W_per_g)
//! spec -> result. This is the single path the CLI, the Python API and the harness all take (P5 G3).
use crate::chain::{self, RateLedger};
use crate::cram::{step as cram_step, Cram};
use crate::photon::{self, PhotonDiagnostics, PhotonResponse, PhotonSourceOut};
use crate::sparse::Csc;
use crate::spec::{
    DecayRef, FissionYieldOptions, HashedFileRef, LibraryRef, PhotonOptions, Projectile, Spec,
};
use actinv_data::{composition, decay, fission, groups::GroupStructure, library};
use num_complex::Complex64 as C64;
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, HashMap};
use std::io::Read;
use std::sync::{Mutex, OnceLock};

pub const EV: f64 = 1.602176634e-19;

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

/// CRAM-16 from the generated coefficient table (controls/gen_cram.py); never transcribed by hand.
fn cram16() -> Cram {
    use crate::cram_coeffs::{CRAM16_ALPHA, CRAM16_ALPHA0, CRAM16_THETA};
    Cram {
        alpha0: CRAM16_ALPHA0,
        theta: CRAM16_THETA.iter().map(|(r, i)| C64::new(*r, *i)).collect(),
        alpha: CRAM16_ALPHA.iter().map(|(r, i)| C64::new(*r, *i)).collect(),
    }
}

fn fission_average_energy_eV(
    library: &library::Library,
    targets: &[(i32, i32)],
    phi: &[f64],
    parent: (i32, i32),
) -> Result<Option<f64>, String> {
    let row_index = library.rows.iter().position(|row| {
        row.mt == 18 && row.zap == 0 && targets.get(row.target).is_some_and(|key| *key == parent)
    });
    let Some(row_index) = row_index else {
        return Ok(None);
    };
    let sigma = library.sigma(row_index);
    let mut numerator = 0.0;
    let mut denominator = 0.0;
    for (group, (&cross_section, &flux)) in sigma.iter().zip(phi).enumerate() {
        let weight = cross_section * flux;
        if weight == 0.0 {
            continue;
        }
        let low = library.bounds[group];
        let high = library.bounds[group + 1];
        if !(low.is_finite() && high.is_finite() && low > 0.0 && high > low) {
            return Err(format!(
                "cannot compute spectrum-average fission energy for {}_{}: contributing group [{low}, {high}] eV is not strictly positive",
                parent.0, parent.1
            ));
        }
        let representative = (high - low) / (high / low).ln();
        numerator += weight * representative;
        denominator += weight;
    }
    Ok((denominator > 0.0).then_some(numerator / denominator))
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
    library: library::Library,
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
}

impl PreparedRun {
    pub fn prepare(spec: &Spec) -> Result<Self, String> {
        Self::prepare_inputs(
            &spec.library,
            &spec.decay,
            &spec.photon,
            &spec.fission_yields,
            spec.projectile,
            spec.options.temperature_K,
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
        let library_sha = verify_hash(&library_ref.path, library_ref.sha256.as_deref())?;
        let idx_path = index_path(&library_ref.path);
        let index_sha = verify_hash(&idx_path, None)?;
        let decay_primary_sha = verify_hash(&decay_ref.primary, None)?;
        let decay_fallback_sha = match &decay_ref.fallback {
            Some(path) if !path.is_empty() => Some(verify_hash(path, None)?),
            _ => None,
        };
        let (response, response_sha) = match &photon_options.response {
            Some(reference) => {
                let sha = verify_hash(&reference.path, Some(&reference.sha256))?;
                let text = std::fs::read_to_string(&reference.path)
                    .map_err(|e| format!("cannot read photon response {}: {e}", reference.path))?;
                (Some(PhotonResponse::from_json(&text)?), Some(sha))
            }
            None => (None, None),
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
        let library = library::read_npz(&library_ref.path)?;
        let index: serde_json::Value =
            serde_json::from_str(&std::fs::read_to_string(&idx_path).map_err(|e| e.to_string())?)
                .map_err(|e| e.to_string())?;
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
        let library_group_structure = index
            .get("groups")
            .and_then(serde_json::Value::as_str)
            .map(str::to_owned);
        let actual_group_hash = GroupStructure {
            name: library_group_structure
                .clone()
                .unwrap_or_else(|| "custom".into()),
            boundaries_ev: library.bounds.clone(),
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
                if canonical.boundaries_ev.len() != library.bounds.len()
                    || canonical
                        .boundaries_ev
                        .iter()
                        .zip(&library.bounds)
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
        let mut nuclides =
            decay::parse_file(&decay_ref.primary).map_err(|error| error.to_string())?;
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
        let chain = chain::build(&nuclides);
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
        })
    }

    pub fn library_boundaries_eV(&self) -> &[f64] {
        &self.library.bounds
    }

    pub fn library_groups(&self) -> usize {
        self.library.ngroups
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
        {
            return Err("run spec nuclear-data inputs do not match the prepared data".into());
        }
        if spec.spectrum.structure != "custom"
            && self
                .library_group_structure
                .as_deref()
                .is_some_and(|structure| structure != spec.spectrum.structure)
        {
            return Err(format!(
                "spec group structure '{}' does not match activation-library group structure '{}'",
                spec.spectrum.structure,
                self.library_group_structure.as_deref().unwrap_or_default()
            ));
        }
        Ok(())
    }

    pub fn run(&self, spec: &Spec, entry_point: &str) -> Result<RunResult, String> {
        self.run_started(spec, entry_point, std::time::Instant::now())
    }

    fn run_started(
        &self,
        spec: &Spec,
        entry_point: &str,
        t0: std::time::Instant,
    ) -> Result<RunResult, String> {
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
        if lib.ngroups != spec.spectrum.flux_per_group.len() {
            return Err(format!(
                "spectrum has {} groups but activation library has {}",
                spec.spectrum.flux_per_group.len(),
                lib.ngroups
            ));
        }
        if let Some(boundaries) = &spec.spectrum.boundaries_eV {
            if boundaries.len() != lib.bounds.len()
                || boundaries
                    .iter()
                    .zip(&lib.bounds)
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
        let react = chain::reaction_rates(
            lib,
            lib_targets,
            &phi,
            ch,
            &effective_fission_yields,
            &mut led,
        );
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
        let mut y = vec![0.0f64; m];
        for (g, v) in n0.iter().enumerate() {
            if *v != 0.0 && pos[g] != usize::MAX {
                y[pos[g]] = *v;
            }
        }
        // ---- solve
        let c = cram16();
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
        for (si, (dt, fl)) in sched.iter().enumerate() {
            let mut trip = dsub.clone();
            if *fl > 0.0 {
                for (i, j, v) in rsub.iter() {
                    trip.push((*i, *j, v * C64::new(*fl, 0.0)));
                }
            }
            let a = Csc::from_triplets(m, &trip);
            let (yy, _) = cram_step(&a, &y, *dt, &c)?;
            y = yy;
            t_cum += dt;
            flux_weighted_time_cum += dt * fl;
            let mut zeroed = 0.0;
            for (k, v) in y.iter_mut().enumerate() {
                if *v < 0.0 && keep[k] != ch.leak && keep[k] != ch.unit {
                    zeroed += -*v;
                    *v = 0.0;
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
            let mut act = BTreeMap::new();
            let mut photon_active = bulk_photon_active.clone();
            for (name, _, activity) in &bulk_photon_active {
                act.insert(name.clone(), *activity);
            }
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
            });
        }
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
                         "n_library_rows": lib.rows.len(), "n_chain_nuclides": ch.keys.len(), "flux_total": phi.iter().sum::<f64>()},
        });
        if !spec.projectile.is_neutron() {
            ledger.as_object_mut().expect("ledger is an object").insert(
                "projectile".into(),
                serde_json::Value::String(spec.projectile.name().into()),
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
            "cram": "CRAM-16, incomplete partial fractions (Pusa, NSE 182:297, 2016)",
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
        if !spec.projectile.is_neutron() {
            certificate
                .as_object_mut()
                .expect("certificate is an object")
                .insert(
                    "projectile".into(),
                    serde_json::Value::String(spec.projectile.name().into()),
                );
        }
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
    let prepared = PreparedRun::prepare(spec)?;
    prepared.run_started(spec, entry_point, started)
}

#[cfg(test)]
mod projectile_output_tests {
    use super::{Heat, StepOut};
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
}
