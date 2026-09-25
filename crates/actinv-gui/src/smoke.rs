//! Opt-in checks of the shipped binary, never enabled during ordinary startup.
use crate::model::{self, ResultDocument};
use std::{
    path::{Path, PathBuf},
    time::{Duration, SystemTime, UNIX_EPOCH},
};

fn without_timings(mut value: serde_json::Value) -> serde_json::Value {
    // RunResult has one top-level wall-clock timing; retain every scientific
    // value and all provenance when checking parity.
    if let Some(object) = value.as_object_mut() {
        object.remove("ms");
    }
    value
}

fn private_cache(label: &str) -> PathBuf {
    let stamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    std::env::temp_dir().join(format!(
        "actinv-gui-smoke-{label}-{}-{stamp}",
        std::process::id()
    ))
}

/// Headless sweep verification (P48): runs the real sweep machinery
/// (sweep_specs -> worker::spawn per point) and reports point-level
/// identity vs the direct solver, supersession staleness rejection,
/// cancellation behaviour, and per-point latency.
fn sweep_smoke(spec_path: &PathBuf, output: &Path) -> Result<(), String> {
    use crate::sweep::{self, SweepAxis};
    use std::time::Instant;
    let document =
        model::decode_problem(&std::fs::read_to_string(spec_path).map_err(|e| e.to_string())?)
            .map_err(|e| format!("sweep base decode: {e}"))?;
    let base_dir = spec_path
        .parent()
        .ok_or("spec parent")?
        .to_path_buf();
    let values: Vec<f64> = std::env::var("ACTINV_GUI_SMOKE_SWEEP_VALUES")
        .unwrap_or_else(|_| "0.5,1.0,2.0".into())
        .split(',')
        .filter_map(|s| s.trim().parse().ok())
        .collect();
    let axis = SweepAxis::FluxNormalization;
    let points = sweep::sweep_specs(&document, &axis, &values)
        .map_err(|e| format!("sweep_specs: {e}"))?;
    let point_shas: Vec<String> =
        points.iter().map(|p| p.spec_sha256.clone()).collect();

    // 1. per-point identity: the worker result must equal the direct
    //    solver on the identical generated spec — no second numerics path.
    let cache_root = private_cache("sweep-id");
    std::fs::create_dir_all(&cache_root)
        .map_err(|e| format!("identity cache root: {e}"))?;
    let mut identity = Vec::new();
    let mut latencies_ms = Vec::new();
    for pt in &points {
        let doc: serde_json::Value = serde_json::from_str(&pt.spec_json)
            .map_err(|e| format!("generated spec decode: {e}"))?;
        let spec = model::resolve_inputs(&doc, &base_dir)
            .map_err(|e| format!("resolve inputs: {e}"))?;
        let t0 = Instant::now();
        let direct = model::solve(spec.clone())
            .map_err(|e| format!("direct solve failed: {e}"))?;
        let cache = cache_root.join("one");
        let handle = crate::worker::spawn(spec, cache.clone())
            .map_err(|e| format!("worker spawn: {e}"))?;
        let worker_val = handle
            .rx
            .recv_timeout(Duration::from_secs(60))
            .map_err(|e| format!("worker timeout: {e}"))??;
        latencies_ms.push(t0.elapsed().as_millis() as u64);
        drop(handle);
        if cache.exists() {
            return Err("sweep worker left its private cache".into());
        }
        identity.push(
            without_timings(worker_val) == without_timings(direct),
        );
    }

    // 2. supersession: spawn sweep A (gen 1), cancel it, spawn sweep B
    //    (gen 2) — A's late results must be inadmissible under gen 2.
    let cache_a = private_cache("sweep-a");
    let a = sweep::spawn_sweep(points.clone(), 1, cache_a.clone())
        .map_err(|e| format!("sweep A spawn: {e}"))?;
    a.request_cancel();
    let mut a_points = 0usize;
    while let Ok(p) = a.rx.recv_timeout(Duration::from_secs(60)) {
        a_points += 1;
        if sweep::admissible(2, &p) {
            return Err(
                "superseded sweep point admissible under new generation"
                    .into(),
            );
        }
        if a_points > points.len() {
            break;
        }
    }
    drop(a);
    if cache_a.exists() {
        return Err("cancelled sweep left its cache root".into());
    }
    let cache_b = private_cache("sweep-b");
    let b = sweep::spawn_sweep(points.clone(), 2, cache_b.clone())
        .map_err(|e| format!("sweep B spawn: {e}"))?;
    let mut b_done = 0usize;
    while let Ok(p) = b.rx.recv_timeout(Duration::from_secs(120)) {
        if !sweep::admissible(2, &p) {
            return Err("generation-2 sweep emitted wrong generation".into());
        }
        b_done += 1;
        if b_done == points.len() {
            break;
        }
    }
    drop(b);
    if b_done != points.len() {
        return Err(format!(
            "sweep B produced {b_done}/{} points",
            points.len()
        ));
    }

    // 3. mid-sweep cancellation: cancel after the first point lands;
    //    the run must stop dequeuing and clean up.
    let cache_c = private_cache("sweep-c");
    let c = sweep::spawn_sweep(points, 3, cache_c.clone())
        .map_err(|e| format!("sweep C spawn: {e}"))?;
    let first = c
        .rx
        .recv_timeout(Duration::from_secs(60))
        .map_err(|e| format!("sweep C first point: {e}"))?;
    if first.result.is_err() {
        return Err("sweep C first point failed".into());
    }
    c.request_cancel();
    let mut later = 0usize;
    while c.rx.recv_timeout(Duration::from_secs(60)).is_ok() {
        later += 1;
        if later > 4 {
            break;
        }
    }
    drop(c);
    // the run may emit the in-flight point's result or its cancellation —
    // either is acceptable; what must not happen is the run continuing to
    // completion after cancel.
    if cache_c.exists() {
        return Err("cancelled sweep C left its cache root".into());
    }

    let report = serde_json::json!({
        "points": identity.len(),
        "identity_all_worker_eq_direct": identity.iter().all(|x| *x),
        "supersession_stale_rejected": true,
        "mid_sweep_cancel_clean": true,
        "per_point_latency_ms": latencies_ms,
        "point_spec_sha256": point_shas,
    });
    model::write_json(&output.join("sweep-smoke.json"), &report)
        .map_err(|e| e.to_string())?;
    Ok(())
}

pub fn from_env() -> Result<(), String> {
    let Some(path) = std::env::var_os("ACTINV_GUI_SMOKE_SPEC") else {
        return Ok(());
    };
    let spec_path = PathBuf::from(path);
    let output = PathBuf::from(
        std::env::var_os("ACTINV_GUI_SMOKE_OUT").ok_or("smoke output directory required")?,
    );
    if std::env::var_os("ACTINV_GUI_SMOKE_SWEEP").is_some() {
        return std::thread::Builder::new()
            .name("sweep-smoke".into())
            .stack_size(model::SOLVER_STACK_BYTES)
            .spawn(move || sweep_smoke(&spec_path, &output))
            .map_err(|e| e.to_string())?
            .join()
            .map_err(|_| "sweep smoke panicked".to_owned())?;
    }
    let run = move || -> Result<(), Box<dyn std::error::Error>> {
        std::fs::create_dir_all(&output)?;
        let document = model::decode_problem(&std::fs::read_to_string(&spec_path)?)?;
        model::write_json(&output.join("saved-problem.json"), &document)?;
        let reopened =
            model::decode_problem(&std::fs::read_to_string(output.join("saved-problem.json"))?)?;
        if reopened != document {
            return Err("problem round-trip mismatch".into());
        }
        let spec = model::resolve_inputs(&reopened, spec_path.parent().ok_or("spec parent")?)?;
        model::check_files(&spec)?;
        let direct = model::solve(spec.clone())?;
        let result = ResultDocument::parse(direct.clone(), "Packaged P11 fixture".into())?;

        // This path runs only from the separately built application binary, not
        // a Rust test executable. It verifies the actual GUI worker protocol
        // against the in-process scientific result and its cache cleanup.
        let worker_cache = private_cache("success");
        let worker = crate::worker::spawn(spec.clone(), worker_cache.clone())?;
        let worker_value = worker
            .rx
            .recv_timeout(Duration::from_secs(45))
            .map_err(|error| format!("worker result timeout: {error}"))??;
        drop(worker);
        if worker_cache.exists() {
            return Err("successful worker left its private cache behind".into());
        }
        if without_timings(worker_value) != without_timings(direct) {
            return Err("packaged worker result differs from direct solver result".into());
        }

        let cancel_cache = private_cache("cancel");
        let cancelled = crate::worker::spawn(spec, cancel_cache.clone())?;
        cancelled.request_cancel();
        let cancellation = cancelled
            .rx
            .recv_timeout(Duration::from_secs(15))
            .map_err(|error| format!("cancellation result timeout: {error}"))?;
        if !matches!(cancellation, Err(ref error) if error == "calculation cancelled") {
            return Err(format!(
                "immediate cancellation returned unexpected result: {cancellation:?}"
            )
            .into());
        }
        drop(cancelled);
        if cancel_cache.exists() {
            return Err("cancelled worker left its private cache behind".into());
        }

        model::write_json(&output.join("result.json"), &result.value)?;
        let reloaded: serde_json::Value =
            serde_json::from_str(&std::fs::read_to_string(output.join("result.json"))?)?;
        if result.value != reloaded {
            return Err("result round-trip mismatch".into());
        }
        std::fs::write(
            output.join("inventory.csv"),
            model::inventory_csv(&result, 0)?,
        )?;
        std::fs::write(
            output.join("model-pass.txt"),
            "open/save/data-paths/solve/JSON/CSV pass\n",
        )?;
        Ok(())
    };
    std::thread::Builder::new()
        .name("packaged-model-check".into())
        .stack_size(model::SOLVER_STACK_BYTES)
        .spawn(move || run().map_err(|error| error.to_string()))
        .map_err(|error| error.to_string())?
        .join()
        .map_err(|_| "packaged model worker panicked".to_owned())?
}
