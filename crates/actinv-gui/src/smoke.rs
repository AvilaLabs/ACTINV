//! Opt-in checks of the shipped binary, never enabled during ordinary startup.
use crate::model::{self, ResultDocument};
use std::{
    path::PathBuf,
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

pub fn from_env() -> Result<(), String> {
    let Some(path) = std::env::var_os("ACTINV_GUI_SMOKE_SPEC") else {
        return Ok(());
    };
    let spec_path = PathBuf::from(path);
    let output = PathBuf::from(
        std::env::var_os("ACTINV_GUI_SMOKE_OUT").ok_or("smoke output directory required")?,
    );
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
