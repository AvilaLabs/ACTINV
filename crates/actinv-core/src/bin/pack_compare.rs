//! Measures what a pack's truncation costs: runs a problem on its whole library and on the
//! library assembled from a pack (`actinv_core::pack`) for each reaction depth given, and reports
//! the size of each assembly and how far its results move.
//!
//!   pack_compare SPEC.json PACKDIR DEPTH [DEPTH ...]
//!
//! Prints one JSON line per depth. Per step it compares total activity, total heat and total
//! photon emission, and the largest relative change of any nuclide carrying at least 0.1% of the
//! step's activity in the whole-library run. Each depth runs twice: with the whole decay data,
//! and with only the decay records the depth reaches (`trimmed_decay`).

use actinv_core::pack::{self, PackGraph};
use actinv_core::run::run;
use actinv_core::spec::Spec;
use serde_json::{json, Value};
use std::collections::HashMap;

fn read(path: &str) -> Result<Vec<u8>, String> {
    std::fs::read(path).map_err(|e| format!("{path}: {e}"))
}

fn relative(a: f64, b: f64) -> f64 {
    if a == b {
        0.0
    } else {
        (a - b).abs() / a.abs().max(b.abs())
    }
}

fn step_totals(step: &Value) -> (f64, f64, f64) {
    let activity = step["activity_Bq_per_g"]
        .as_object()
        .map(|map| map.values().filter_map(Value::as_f64).sum())
        .unwrap_or(0.0);
    let heat = step["heat_W_per_g"]["total"].as_f64().unwrap_or(0.0);
    let photons = step["photon_source"]["total_photons_s_g"]
        .as_f64()
        .unwrap_or(0.0);
    (activity, heat, photons)
}

fn compare(full: &Value, packed: &Value) -> Value {
    let mut worst = json!({"activity": 0.0, "heat": 0.0, "photons": 0.0, "nuclide": 0.0});
    let mut worst_nuclide = Value::Null;
    let steps_full = full["steps"].as_array().cloned().unwrap_or_default();
    let steps_packed = packed["steps"].as_array().cloned().unwrap_or_default();
    for (a, b) in steps_full.iter().zip(&steps_packed) {
        let (fa, fh, fp) = step_totals(a);
        let (pa, ph, pp) = step_totals(b);
        for (key, value) in [
            ("activity", relative(fa, pa)),
            ("heat", relative(fh, ph)),
            ("photons", relative(fp, pp)),
        ] {
            if value > worst[key].as_f64().unwrap_or(0.0) {
                worst[key] = value.into();
            }
        }
        if let Some(map) = a["activity_Bq_per_g"].as_object() {
            for (name, value) in map {
                let value = value.as_f64().unwrap_or(0.0);
                if fa > 0.0 && value >= 1e-3 * fa {
                    let other = b["activity_Bq_per_g"][name].as_f64().unwrap_or(0.0);
                    let change = relative(value, other);
                    if change > worst["nuclide"].as_f64().unwrap_or(0.0) {
                        worst["nuclide"] = change.into();
                        worst_nuclide = json!({"step": a["step"], "nuclide": name});
                    }
                }
            }
        }
    }
    json!({"largest_relative_change": worst, "at": worst_nuclide,
           "steps": [steps_full.len(), steps_packed.len()]})
}

/// A packed run given the other files it asks for from disk.
fn run_packed_from_disk(
    spec_text: &str,
    graph: &PackGraph,
    parts: &[(usize, Vec<u8>)],
    mut inputs: HashMap<String, Vec<u8>>,
) -> Result<Value, String> {
    loop {
        match pack::run_packed(spec_text, "pack-compare", graph, parts, inputs.clone()) {
            Ok(result) => return serde_json::to_value(result).map_err(|e| e.to_string()),
            Err(error) => {
                let Some(missing) = error.strip_suffix(" was not supplied with the run's inputs")
                else {
                    return Err(error);
                };
                if inputs.contains_key(missing) {
                    return Err(format!("asked twice for {missing}"));
                }
                inputs.insert(missing.to_owned(), read(missing)?);
            }
        }
    }
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.len() < 3 {
        eprintln!("usage: pack_compare SPEC.json PACKDIR DEPTH [DEPTH ...]");
        std::process::exit(2);
    }
    let result = (|| -> Result<(), String> {
        let spec_text = String::from_utf8(read(&args[0])?).map_err(|e| e.to_string())?;
        let spec = Spec::from_json(&spec_text)?;
        let directory = std::path::Path::new(&args[1]);
        let graph: PackGraph = serde_json::from_slice(
            &std::fs::read(directory.join("graph.json")).map_err(|e| e.to_string())?,
        )
        .map_err(|e| e.to_string())?;
        let full = serde_json::to_value(run(&spec, "pack-compare")?).map_err(|e| e.to_string())?;
        let start = pack::start_nuclides(&spec)?;
        let mut decay_paths = vec![spec.decay.primary.clone()];
        decay_paths.extend(
            spec.decay
                .fallback
                .iter()
                .filter(|f| !f.is_empty())
                .cloned(),
        );
        if decay_paths.len() != graph.decay.len() {
            return Err("the spec and the pack name different numbers of decay files".into());
        }
        for depth in &args[2..] {
            let depth: usize = depth.parse().map_err(|_| format!("bad depth {depth}"))?;
            let reach = pack::reachable(&graph, &start, depth)?;
            let mut parts = Vec::with_capacity(reach.targets.len());
            let mut fetched = 0usize;
            for &target in &reach.targets {
                let bytes = std::fs::read(directory.join(&graph.targets[target].file))
                    .map_err(|e| e.to_string())?;
                fetched += bytes.len();
                parts.push((target, bytes));
            }
            let (npz, _) = pack::assemble(&graph, &parts)?;
            let packed = run_packed_from_disk(&spec_text, &graph, &parts, HashMap::new())?;
            let mut records = HashMap::new();
            for file in pack::decay_files(&graph, &reach.nuclides) {
                records.insert(
                    file.to_owned(),
                    read(&directory.join(file).to_string_lossy())?,
                );
            }
            let mut trimmed_inputs = HashMap::new();
            let mut decay_bytes = 0usize;
            for (path, text) in
                decay_paths
                    .iter()
                    .zip(pack::decay_text(&graph, &reach.nuclides, &records)?)
            {
                decay_bytes += text.len();
                trimmed_inputs.insert(path.clone(), text.into_bytes());
            }
            let trimmed = run_packed_from_disk(&spec_text, &graph, &parts, trimmed_inputs)?;
            println!(
                "{}",
                json!({
                    "spec": args[0],
                    "depth": depth,
                    "targets": reach.targets.len(),
                    "fission_reached": reach.fission,
                    "target_file_bytes": fetched,
                    "assembled_npz_bytes": npz.len(),
                    "decay_nuclides": reach.nuclides.len(),
                    "trimmed_decay_bytes": decay_bytes,
                    "comparison": compare(&full, &packed),
                    "trimmed_decay": compare(&full, &trimmed),
                })
            );
        }
        Ok(())
    })();
    if let Err(error) = result {
        eprintln!("pack_compare: {error}");
        std::process::exit(1);
    }
}
