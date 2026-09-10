#![cfg_attr(all(windows, not(debug_assertions)), windows_subsystem = "windows")]

mod app;
mod capture;
mod model;
mod options;
mod smoke;
mod tour;
mod transport;
mod visuals;

fn main() -> eframe::Result {
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
