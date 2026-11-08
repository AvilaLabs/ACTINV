//! Runs each problem twice, once reading its inputs from disk and once from memory
//! (`run_with_inputs`, the browser's path), and reports whether the two results are identical
//! apart from wall times. The memory run is given exactly the files it asks for: each one it
//! reports missing is read from disk and the run is repeated, so the reported inputs are the
//! complete set the problem names.
//!
//!   memory_run_probe SPEC.json [SPEC.json ...]
//!
//! One JSON line per problem; exits 1 when any pair differs or fails.

use actinv_core::run::{run, run_with_inputs};
use actinv_core::spec::Spec;
use serde_json::Value;
use std::collections::HashMap;

/// Wall times, which differ from run to run and read zero where there is no clock.
fn strip_wall_times(value: &mut Value) {
    match value {
        Value::Object(map) => {
            map.remove("ms");
            map.remove("wall_time_s");
            for child in map.values_mut() {
                strip_wall_times(child);
            }
        }
        Value::Array(items) => items.iter_mut().for_each(strip_wall_times),
        _ => {}
    }
}

fn first_difference(left: &Value, right: &Value, at: &str) -> Option<String> {
    match (left, right) {
        (Value::Object(a), Value::Object(b)) => {
            for key in a.keys().chain(b.keys()) {
                let child = format!("{at}/{key}");
                match (a.get(key), b.get(key)) {
                    (Some(x), Some(y)) => {
                        if let Some(found) = first_difference(x, y, &child) {
                            return Some(found);
                        }
                    }
                    _ => return Some(child),
                }
            }
            None
        }
        (Value::Array(a), Value::Array(b)) => {
            if a.len() != b.len() {
                return Some(format!("{at} (length {} vs {})", a.len(), b.len()));
            }
            a.iter()
                .zip(b)
                .enumerate()
                .find_map(|(index, (x, y))| first_difference(x, y, &format!("{at}/{index}")))
        }
        _ => (left != right).then(|| at.to_owned()),
    }
}

fn probe(path: &str) -> Result<Value, String> {
    let spec = Spec::from_json(
        &std::fs::read_to_string(path).map_err(|error| format!("{path}: {error}"))?,
    )?;
    let mut from_disk = serde_json::to_value(run(&spec, path)?).map_err(|e| e.to_string())?;
    let mut inputs: HashMap<String, Vec<u8>> = HashMap::new();
    let mut from_memory = loop {
        match run_with_inputs(&spec, path, inputs.clone()) {
            Ok(result) => break serde_json::to_value(result).map_err(|e| e.to_string())?,
            Err(error) => {
                let Some(missing) = error.strip_suffix(" was not supplied with the run's inputs")
                else {
                    return Err(format!("memory run: {error}"));
                };
                let missing = missing.to_owned();
                if inputs.contains_key(&missing) {
                    return Err(format!("memory run asked twice for {missing}"));
                }
                let bytes =
                    std::fs::read(&missing).map_err(|error| format!("{missing}: {error}"))?;
                inputs.insert(missing, bytes);
            }
        }
    };
    strip_wall_times(&mut from_disk);
    strip_wall_times(&mut from_memory);
    let mut names: Vec<&String> = inputs.keys().collect();
    names.sort();
    Ok(serde_json::json!({
        "spec": path,
        "identical": from_disk == from_memory,
        "first_difference": first_difference(&from_disk, &from_memory, ""),
        "inputs": names,
    }))
}

fn main() {
    let paths: Vec<String> = std::env::args().skip(1).collect();
    if paths.is_empty() {
        eprintln!("usage: memory_run_probe SPEC.json [SPEC.json ...]");
        std::process::exit(2);
    }
    let mut failed = false;
    for path in &paths {
        let line = probe(path).unwrap_or_else(
            |error| serde_json::json!({"spec": path, "identical": false, "error": error}),
        );
        failed |= line["identical"] != Value::Bool(true);
        println!("{line}");
    }
    std::process::exit(i32::from(failed));
}
