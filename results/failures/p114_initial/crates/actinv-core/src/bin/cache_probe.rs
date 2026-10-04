//! P84 probe: a flux-only sequence of runs through one `PreparedCache` (`warm`) or a fresh cache per
//! run (`cold`). Prints one JSON line per run: result hash (top-level `ms` removed), wall time, hit.

use actinv_core::{
    run::{run_with_cache, PreparedCache},
    spec::Spec,
};
use sha2::{Digest, Sha256};

const FACTORS: [f64; 10] = [1.0, 1.1, 0.9, 1.25, 0.8, 1.5, 0.67, 2.0, 0.5, 1.05];

fn main() {
    let arguments: Vec<String> = std::env::args().collect();
    if arguments.len() != 3 || !matches!(arguments[2].as_str(), "warm" | "cold") {
        eprintln!("usage: cache_probe SPEC.json warm|cold");
        std::process::exit(2);
    }
    let result = (|| -> Result<(), String> {
        let text = std::fs::read_to_string(&arguments[1]).map_err(|error| error.to_string())?;
        let base = Spec::from_json(&text)?;
        let base_total = base
            .spectrum
            .total
            .unwrap_or_else(|| base.spectrum.flux_per_group.iter().sum());
        let mut warm = PreparedCache::new();
        for factor in FACTORS {
            let mut spec = base.clone();
            spec.spectrum.total = Some(base_total * factor);
            let mut cold = PreparedCache::new();
            let cache = if arguments[2] == "warm" {
                &mut warm
            } else {
                &mut cold
            };
            let started = std::time::Instant::now();
            let result = run_with_cache(&spec, "cache-probe", cache)?;
            let wall_ms = started.elapsed().as_secs_f64() * 1e3;
            let mut value = serde_json::to_value(result).map_err(|error| error.to_string())?;
            value
                .as_object_mut()
                .ok_or("result is not an object")?
                .remove("ms");
            let text = serde_json::to_string(&value).map_err(|error| error.to_string())?;
            let hash = format!("{:x}", Sha256::digest(text.as_bytes()));
            println!(
                "{}",
                serde_json::json!({"factor": factor, "sha256": hash, "wall_ms": wall_ms, "hit": cache.last_hit()})
            );
        }
        Ok(())
    })();
    if let Err(error) = result {
        eprintln!("{error}");
        std::process::exit(1);
    }
}
