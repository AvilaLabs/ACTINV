#![allow(non_snake_case)] // field names are the JSON wire format (Z, A, LISO, heat_W_per_g)
//! spec -> result. This is the single path the CLI, the Python API and the harness all take (P5 G3).
use crate::chain::{self, RateLedger};
use crate::cram::{step as cram_step, Cram};
use crate::photon::{self, PhotonDiagnostics, PhotonResponse, PhotonSourceOut};
use crate::sparse::Csc;
use crate::spec::Spec;
use actinv_data::{composition, decay, library};
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

pub fn run(spec: &Spec, entry_point: &str) -> Result<RunResult, String> {
    let t0 = std::time::Instant::now();
    // ---- data
    let library_sha = verify_hash(&spec.library.path, spec.library.sha256.as_deref())?;
    let idx_path = index_path(&spec.library.path);
    let index_sha = verify_hash(&idx_path, None)?;
    let decay_primary_sha = verify_hash(&spec.decay.primary, None)?;
    let decay_fallback_sha = match &spec.decay.fallback {
        Some(path) if !path.is_empty() => Some(verify_hash(path, None)?),
        _ => None,
    };
    let (response, response_sha) = match &spec.photon.response {
        Some(reference) => {
            let sha = verify_hash(&reference.path, Some(&reference.sha256))?;
            let text = std::fs::read_to_string(&reference.path)
                .map_err(|e| format!("cannot read photon response {}: {e}", reference.path))?;
            (Some(PhotonResponse::from_json(&text)?), Some(sha))
        }
        None => (None, None),
    };
    let lib = library::read_npz(&spec.library.path)?;
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
                    (declared - stored).abs() > 1e-12 * declared.abs().max(stored.abs()).max(1.0)
                })
        {
            return Err(
                "custom spectrum boundaries do not match the activation library boundaries".into(),
            );
        }
    }
    let idxj: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(&idx_path).map_err(|e| e.to_string())?)
            .map_err(|e| e.to_string())?;
    if let Some(recorded) = idxj["sha256_npz"].as_str() {
        if !library_sha.eq_ignore_ascii_case(recorded) {
            return Err(format!("activation-library index hash mismatch: index records {recorded}, computed {library_sha}"));
        }
    }
    if let Some(temperature) = idxj["temperature_K"].as_f64() {
        if (temperature - spec.options.temperature_K).abs() > 1e-9 {
            return Err(format!(
                "requested temperature {} K does not match library temperature {temperature} K",
                spec.options.temperature_K
            ));
        }
    }
    let lib_targets: Vec<(i32, i32)> = idxj["targets"]
        .as_array()
        .ok_or("library index has no targets")?
        .iter()
        .map(|t| {
            (
                t["za"].as_i64().unwrap_or(0) as i32,
                t["liso"].as_i64().unwrap_or(0) as i32,
            )
        })
        .collect();
    let mut nuclides = decay::parse_file(&spec.decay.primary).map_err(|e| e.to_string())?;
    let mut n_fallback = 0usize;
    if let Some(fb) = &spec.decay.fallback {
        if !fb.is_empty() {
            for (k, v) in decay::parse_file(fb).map_err(|e| e.to_string())? {
                if let std::collections::hash_map::Entry::Vacant(entry) = nuclides.entry(k) {
                    entry.insert(v);
                    n_fallback += 1;
                }
            }
        }
    }
    let ch = chain::build(&nuclides);
    // ---- material
    let composition_total: f64 = spec.material.composition.values().sum();
    let (bulk_inv, cdiag) =
        composition::atoms_per_gram_basis(&spec.material.composition, &spec.material.basis);
    let material_mass_fractions = if response.is_some() {
        composition::mass_fractions(&spec.material.composition, &spec.material.basis)?
    } else {
        BTreeMap::new()
    };
    let phi = spec.flux_ascending();
    let mut led = RateLedger::default();
    let react = chain::reaction_rates(&lib, &lib_targets, &phi, &ch, &mut led);
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
    let t_irr: f64 = spec
        .schedule_seconds()
        .iter()
        .filter(|(_, f)| *f > 0.0)
        .map(|(d, _)| d)
        .sum();
    let mut burnup: HashMap<usize, f64> = HashMap::new();
    for (r, c, v) in &react {
        if *r == *c && bulk.contains_key(c) {
            *burnup.entry(*c).or_insert(0.0) += -v * t_irr;
        }
    }
    led.burnup_max = burnup.values().cloned().fold(0.0, f64::max);
    let mode = match spec.options.mode.as_str() {
        "auto" => {
            if led.burnup_max < 1e-6 {
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
    let sched = spec.schedule_seconds();
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
        .filter_map(|(key, atoms_bound)| nuclides.get(key).map(|nuclide| (nuclide, atoms_bound)))
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
    let ledger = serde_json::json!({
        "mode": mode, "max_burnup_fraction": led.burnup_max,
        "composition_basis": spec.material.basis,
        "composition_input_total": composition_total,
        "composition_weight_percent_total": if spec.material.basis == "wt_percent" { Some(composition_total) } else { None },
        "composition_not_summing_to_100": spec.material.basis == "wt_percent" && (composition_total - 100.0).abs() > 1e-9,
        "composition_isotopes_absent_from_decay_library": absent.iter().map(|(z, l)| format!("{z}_{l}")).collect::<Vec<_>>(),
        "composition_elements_unknown": cdiag.unknown,
        "products_no_evaluated_decay_data": led.products_no_decay_data,
        "fission_no_yields_to_leakage": led.fission_no_yields,
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
        "assembly": {"n_bulk_isotopes": bulk.len(), "n_decay_triplets": d_src.len(), "n_reaction_triplets": r_src.len(),
                     "n_library_rows": lib.rows.len(), "n_chain_nuclides": ch.keys.len(), "flux_total": phi.iter().sum::<f64>()},
    });
    let certificate = serde_json::json!({
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
        },
        "tables_provenance": composition::provenance(),
        "cram": "CRAM-16, incomplete partial fractions (Pusa, NSE 182:297, 2016)",
        "mode": mode, "prune": spec.options.prune, "bmin_atoms_per_g": spec.options.bmin_atoms_per_g,
        "material_basis": spec.material.basis,
        "photon": {"group_structure": spec.photon.group_structure, "build_up_factor": spec.photon.build_up_factor,
                   "gamma_constant_cutoff_eV": spec.photon.gamma_constant_cutoff_eV},
    });
    Ok(RunResult {
        spec_title: spec.title.clone(),
        entry_point: entry_point.into(),
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
