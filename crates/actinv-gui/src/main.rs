#![cfg_attr(all(windows, not(debug_assertions)), windows_subsystem = "windows")]

mod app;
mod capture;
mod model;
mod options;
mod smoke;
mod sweep;
mod tour;
mod transport;
mod visuals;
mod worker;

fn main() -> eframe::Result {
    if std::env::var_os("ACTINV_GUI_CALCULATION_WORKER").is_some() {
        if let Err(error) = calculation_worker() {
            eprintln!("{error}");
            std::process::exit(1);
        }
        return Ok(());
    }
    smoke::from_env().expect("opt-in packaged desktop model smoke test");
    if std::env::var_os("ACTINV_GUI_SMOKE_SPEC").is_some()
        && std::env::var_os("ACTINV_GUI_SMOKE_MODEL_ONLY").is_some()
    {
        return Ok(());
    }
    let icon = eframe::icon_data::from_png_bytes(include_bytes!("../assets/avila-labs-logo.png"))
        .expect("embedded Avila Labs icon");
    eframe::run_native(
        "ACTINV · Avila Labs",
        eframe::NativeOptions {
            viewport: eframe::egui::ViewportBuilder::default()
                .with_icon(icon)
                .with_inner_size([1320.0, 860.0])
                .with_min_inner_size([900.0, 640.0]),
            ..Default::default()
        },
        Box::new(|cc| Ok(Box::new(app::Desktop::new(cc)))),
    )
}

fn calculation_worker() -> Result<(), String> {
    use std::io::{Read, Write};
    // Worker mode is deliberately a narrow stdin/stdout protocol.  In
    // particular, it must not enter capture/smoke modes inherited from the
    // launching desktop process.
    let mut input = String::new();
    std::io::stdin()
        .read_to_string(&mut input)
        .map_err(|e| e.to_string())?;
    let spec = actinv_core::spec::Spec::from_json(&input).map_err(|e| e.to_string())?;
    let result = worker::run_in_solver_thread(spec)?;
    serde_json::to_writer(std::io::stdout(), &result).map_err(|e| e.to_string())?;
    std::io::stdout().flush().map_err(|e| e.to_string())?;
    Ok(())
}
