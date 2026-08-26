#![allow(non_snake_case)]   // field names are the JSON wire format (Z, A, LISO, heat_W_per_g)
//! spec -> result. This is the single path the CLI, the Python API and the harness all take (P5 G3).
use std::collections::{BTreeMap, HashMap};
use actinv_data::{composition, decay, library};
use crate::chain::{self, RateLedger};
use crate::cram::{Cram, step as cram_step};
use crate::sparse::Csc;
use crate::spec::Spec;
use num_complex::Complex64 as C64;

pub const EV: f64 = 1.602176634e-19;

#[derive(serde::Serialize)]
pub struct NuclideOut { pub nuclide: String, pub Z: i32, pub A: i32, pub LISO: i32, pub atoms_per_g: f64 }
#[derive(serde::Serialize)]
pub struct Heat { pub total: f64, pub alpha: f64, pub beta: f64, pub gamma: f64 }
#[derive(serde::Serialize)]
pub struct StepOut {
    pub step: usize, pub t_s: f64, pub flux: f64,
    pub inventory: Vec<NuclideOut>,
    pub activity_Bq_per_g: BTreeMap<String, f64>,
    pub heat_W_per_g: Heat,
    pub leakage_atoms_per_g: f64,
    pub negative_atoms_zeroed: f64,
    pub total_atoms_per_g: f64,
    pub n_states_populated: usize,
}
#[derive(serde::Serialize)]
pub struct RunResult {
    pub spec_title: String,
    pub entry_point: String,
    pub mode: String,
    pub pruned_states: usize,
    pub total_states: usize,
    pub steps: Vec<StepOut>,
    pub ledger: serde_json::Value,
    pub certificate: serde_json::Value,
    pub ms: f64,
}

fn name_of(za: i32, liso: i32) -> String {
    let s = composition::symbol_of(za / 1000);
    if liso > 0 { format!("{s}{}m{liso}", za % 1000) } else { format!("{s}{}", za % 1000) }
}

/// CRAM-16 from the generated coefficient table (controls/gen_cram.py); never transcribed by hand.
fn cram16() -> Cram {
    use crate::cram_coeffs::{CRAM16_ALPHA, CRAM16_ALPHA0, CRAM16_THETA};
    Cram { alpha0: CRAM16_ALPHA0,
        theta: CRAM16_THETA.iter().map(|(r, i)| C64::new(*r, *i)).collect(),
        alpha: CRAM16_ALPHA.iter().map(|(r, i)| C64::new(*r, *i)).collect() }
}

pub fn run(spec: &Spec, entry_point: &str) -> Result<RunResult, String> {
    let t0 = std::time::Instant::now();
    // ---- data
    let lib = library::read_npz(&spec.library.path)?;
    let idxj: serde_json::Value = serde_json::from_str(&std::fs::read_to_string(spec.library.path.replace(".npz", "_index.json")).map_err(|e| e.to_string())?).map_err(|e| e.to_string())?;
    let lib_targets: Vec<(i32, i32)> = idxj["targets"].as_array().ok_or("library index has no targets")?
        .iter().map(|t| (t["za"].as_i64().unwrap_or(0) as i32, t["liso"].as_i64().unwrap_or(0) as i32)).collect();
    let mut nuclides = decay::parse_file(&spec.decay.primary).map_err(|e| e.to_string())?;
    let mut n_fallback = 0usize;
    if let Some(fb) = &spec.decay.fallback {
        if !fb.is_empty() {
            for (k, v) in decay::parse_file(fb).map_err(|e| e.to_string())? {
                if !nuclides.contains_key(&k) { nuclides.insert(k, v); n_fallback += 1; }
            }
        }
    }
    let ch = chain::build(&nuclides);
    // ---- material
    let wt_total: f64 = spec.material.composition.values().sum();
    let (bulk_inv, cdiag) = composition::atoms_per_gram(&spec.material.composition);
    let phi = spec.flux_ascending();
    let mut led = RateLedger::default();
    let react = chain::reaction_rates(&lib, &lib_targets, &phi, &ch, &mut led);
    // ---- trace formulation: bulk isotopes become constant sources through the unit state
    let mut bulk: HashMap<usize, f64> = HashMap::new();
    let mut absent: Vec<(i32, i32)> = Vec::new();
    for ((za, liso), atoms) in &bulk_inv {
        match ch.index.get(&(*za, *liso)) { Some(&c) => { *bulk.entry(c).or_insert(0.0) += atoms; } None => absent.push((*za, *liso)) }
    }
    let t_irr: f64 = spec.schedule_seconds().iter().filter(|(_, f)| *f > 0.0).map(|(d, _)| d).sum();
    let mut burnup: HashMap<usize, f64> = HashMap::new();
    for (r, c, v) in &react { if *r == *c && bulk.contains_key(c) { *burnup.entry(*c).or_insert(0.0) += -v * t_irr; } }
    led.burnup_max = burnup.values().cloned().fold(0.0, f64::max);
    let mode = match spec.options.mode.as_str() {
        "auto" => if led.burnup_max < 1e-6 { "trace" } else { "coupled" },
        m => m,
    }.to_string();
    let mut d_src: Vec<(usize, usize, f64)> = Vec::new();
    let mut r_src: Vec<(usize, usize, f64)> = Vec::new();
    let mut bulk_heat = 0.0;
    if mode == "trace" {
        for (r, c, v) in &ch.decay {
            if bulk.contains_key(c) { if r != c && !bulk.contains_key(r) { d_src.push((*r, ch.unit, v * bulk[c])); } }
            else if !bulk.contains_key(r) { d_src.push((*r, *c, *v)); }
        }
        for (r, c, v) in &react {
            if bulk.contains_key(c) {
                if r == c { continue; }
                if bulk.contains_key(r) { led.bulk_production_dropped.push((name_of(ch.keys[*c].0, ch.keys[*c].1), name_of(ch.keys[*r].0, ch.keys[*r].1), *v)); continue; }
                r_src.push((*r, ch.unit, v * bulk[c]));
            } else if !bulk.contains_key(r) { r_src.push((*r, *c, *v)); }
        }
        for (c, nb) in &bulk {
            let key = ch.keys[*c]; if let Some(nu) = nuclides.get(&key) {
                if nu.lambda() > 0.0 { bulk_heat += nu.lambda() * nb * (nu.e_light() + nu.e_em() + nu.e_heavy()) * EV; }
            }
        }
    } else {
        d_src = ch.decay.clone(); r_src = react.clone();
    }
    // ---- initial vector
    let mut n0 = vec![0.0f64; ch.n];
    if mode == "trace" { n0[ch.unit] = 1.0; } else { for (c, v) in &bulk { n0[*c] = *v; } }
    // ---- prune
    let sched = spec.schedule_seconds();
    let keep: Vec<usize> = match spec.options.prune.as_str() {
        "none" => (0..ch.n).collect(),
        p => crate::prune::reachable(ch.n, &d_src, &r_src, &n0, &sched, p == "rate", spec.options.bmin_atoms_per_g).0,
    };
    let mut pos = vec![usize::MAX; ch.n];
    for (k, g) in keep.iter().enumerate() { pos[*g] = k; }
    let m = keep.len();
    let sub = |t: &Vec<(usize, usize, f64)>| -> Vec<(usize, usize, C64)> {
        t.iter().filter(|(i, j, _)| pos[*i] != usize::MAX && pos[*j] != usize::MAX).map(|(i, j, v)| (pos[*i], pos[*j], C64::new(*v, 0.0))).collect()
    };
    let dsub = sub(&d_src); let rsub = sub(&r_src);
    let mut y = vec![0.0f64; m];
    for (g, v) in n0.iter().enumerate() { if *v != 0.0 && pos[g] != usize::MAX { y[pos[g]] = *v; } }
    // ---- solve
    let c = cram16();
    let mut steps = Vec::new(); let mut t_cum = 0.0;
    for (si, (dt, fl)) in sched.iter().enumerate() {
        let mut trip = dsub.clone();
        if *fl > 0.0 { for (i, j, v) in rsub.iter() { trip.push((*i, *j, v * C64::new(*fl, 0.0))); } }
        let a = Csc::from_triplets(m, &trip);
        let (yy, _) = cram_step(&a, &y, *dt, &c)?;
        y = yy; t_cum += dt;
        let mut zeroed = 0.0;
        for (k, v) in y.iter_mut().enumerate() {
            if *v < 0.0 && keep[k] != ch.leak && keep[k] != ch.unit { zeroed += -*v; *v = 0.0; }
        }
        // ---- outputs
        let mut inv = Vec::new(); let mut act = BTreeMap::new();
        let (mut ha, mut hb, mut hg) = (0.0, 0.0, 0.0);
        for (k, v) in y.iter().enumerate() {
            let g = keep[k];
            if g == ch.leak || g == ch.unit || *v <= 0.0 { continue; }
            let key = ch.keys[g]; let nu = match nuclides.get(&key) { Some(n) => n, None => continue };
            let nm = name_of(key.0, key.1);
            inv.push(NuclideOut { nuclide: nm.clone(), Z: key.0 / 1000, A: key.0 % 1000, LISO: key.1, atoms_per_g: *v });
            let l = nu.lambda();
            if l > 0.0 {
                act.insert(nm, l * v);
                ha += l * v * nu.e_heavy() * EV; hb += l * v * nu.e_light() * EV; hg += l * v * nu.e_em() * EV;
            }
        }
        let heat = Heat { total: ha + hb + hg + bulk_heat, alpha: ha, beta: hb, gamma: hg };
        steps.push(StepOut { step: si + 1, t_s: t_cum, flux: *fl, inventory: inv, activity_Bq_per_g: act, heat_W_per_g: heat,
            leakage_atoms_per_g: pos[ch.leak].checked_sub(0).and_then(|p| if p != usize::MAX { y.get(p).copied() } else { None }).unwrap_or(0.0),
            negative_atoms_zeroed: zeroed,
            total_atoms_per_g: y.iter().sum(), n_states_populated: y.iter().filter(|v| **v > 0.0).count() });
    }
    // ---- ledger
    let ledger = serde_json::json!({
        "mode": mode, "max_burnup_fraction": led.burnup_max,
        "composition_weight_percent_total": wt_total,
        "composition_not_summing_to_100": (wt_total - 100.0).abs() > 1e-9,
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
        "bulk_background_heat_W_per_g": bulk_heat,
        "assembly": {"n_bulk_isotopes": bulk.len(), "n_decay_triplets": d_src.len(), "n_reaction_triplets": r_src.len(),
                     "n_library_rows": lib.rows.len(), "n_chain_nuclides": ch.keys.len(), "flux_total": phi.iter().sum::<f64>()},
    });
    let certificate = serde_json::json!({
        "solver": concat!("actinv-core ", env!("CARGO_PKG_VERSION")),
        "entry_point": entry_point,
        "library": spec.library.path, "library_sha256_declared": spec.library.sha256,
        "decay_primary": spec.decay.primary, "decay_fallback": spec.decay.fallback,
        "tables_provenance": composition::provenance(),
        "cram": "CRAM-16, incomplete partial fractions (Pusa, NSE 182:297, 2016)",
        "mode": mode, "prune": spec.options.prune, "bmin_atoms_per_g": spec.options.bmin_atoms_per_g,
    });
    Ok(RunResult { spec_title: spec.title.clone(), entry_point: entry_point.into(), mode, pruned_states: m, total_states: ch.n,
                   steps, ledger, certificate, ms: t0.elapsed().as_secs_f64() * 1e3 })
}
