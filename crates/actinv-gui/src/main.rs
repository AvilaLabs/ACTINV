mod app;
mod capture;
mod model;
mod tour;

fn main() -> eframe::Result {
    eframe::run_native(
        "ACTINV · Avila Labs",
        eframe::NativeOptions {
            viewport: eframe::egui::ViewportBuilder::default()
                .with_inner_size([1320.0, 860.0])
                .with_min_inner_size([900.0, 640.0]),
            ..Default::default()
        },
        Box::new(|cc| Ok(Box::new(app::Desktop::new(cc)))),
    )
}
