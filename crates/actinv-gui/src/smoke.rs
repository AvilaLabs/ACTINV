//! Opt-in checks of the shipped binary, never enabled during ordinary startup.
use crate::model::{self, ResultDocument};
use std::path::PathBuf;

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
        let result = ResultDocument::parse(model::solve(spec)?, "Packaged P11 fixture".into())?;
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
