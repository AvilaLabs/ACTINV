use crate::{
    model::{self, ResultDocument},
    visuals, web,
};
use actinv_core::spec::Spec;
use eframe::egui::{self, Color32, RichText};
use egui_plot::{Legend, Line, Plot};
use serde_json::{json, Value};
use std::sync::mpsc::{self, Receiver};

const BLUE: Color32 = Color32::from_rgb(24, 0, 173);
const MAX_FILE_BYTES: usize = 32 * 1024 * 1024;
const FILE_TOO_LARGE: &str =
    "This workbench accepts files up to 32 MiB. Use the desktop app for larger results.";
type FileRead = Result<(FileKind, String, Vec<u8>), String>;

#[derive(Clone, Copy)]
enum FileKind {
    Auto,
    Problem,
    Result,
    Compare,
}

pub struct Workbench {
    page: usize,
    problem: Value,
    saved: Value,
    raw: String,
    raw_dirty: bool,
    /// The problem `raw` was last rendered from; re-render only when it changes.
    raw_source: Value,
    result: Option<ResultDocument>,
    comparison: Option<ResultDocument>,
    pending_file: Option<Receiver<FileRead>>,
    status: String,
    error: bool,
    step: usize,
    metric: usize,
    selected: String,
    filter: String,
    log_time: bool,
    dark: bool,
}

impl Workbench {
    pub fn new(cc: &eframe::CreationContext<'_>) -> Self {
        cc.egui_ctx.set_theme(egui::ThemePreference::Light);
        cc.egui_ctx.all_styles_mut(|style| {
            style.spacing.item_spacing = egui::vec2(10., 10.);
            style.spacing.button_padding = egui::vec2(12., 8.);
            style.visuals.selection.bg_fill = BLUE;
            style.visuals.selection.stroke.color = Color32::WHITE;
            style.visuals.hyperlink_color = if style.visuals.dark_mode {
                Color32::from_rgb(165, 178, 255)
            } else {
                BLUE
            };
        });
        let problem = model::decode_problem(model::EXAMPLE).expect("bundled problem");
        Self {
            page: 0,
            saved: problem.clone(),
            raw: String::new(),
            problem,
            raw_dirty: false,
            raw_source: Value::Null,
            result: None,
            comparison: None,
            pending_file: None,
            status: "Open a result or try the example to get started.".into(),
            error: false,
            step: 0,
            metric: 0,
            selected: String::new(),
            filter: String::new(),
            log_time: false,
            dark: false,
        }
    }

    fn report(&mut self, result: Result<String, String>) {
        match result {
            Ok(message) => {
                self.status = message;
                self.error = false;
            }
            Err(message) => {
                self.status = message;
                self.error = true;
            }
        }
    }

    fn replace_problem_allowed(&self) -> bool {
        if !self.raw_dirty && self.problem == self.saved {
            return true;
        }
        web_sys::window()
            .and_then(|window| {
                window.confirm_with_message(
            "Replace the current problem? Changes that have not been downloaded will be lost."
        ).ok()
            })
            .unwrap_or(false)
    }

    fn pick(&mut self, kind: FileKind, ctx: &egui::Context) {
        if self.pending_file.is_some() {
            return;
        }
        let (tx, rx) = mpsc::channel();
        self.pending_file = Some(rx);
        let ctx = ctx.clone();
        wasm_bindgen_futures::spawn_local(async move {
            if let Some(file) = rfd::AsyncFileDialog::new()
                .add_filter("ACTINV JSON", &["json"])
                .pick_file()
                .await
            {
                let name = file.file_name();
                if file.inner().size() > MAX_FILE_BYTES as f64 {
                    let _ = tx.send(Err(FILE_TOO_LARGE.into()));
                } else {
                    let bytes = file.read().await;
                    let _ = tx.send(Ok((kind, name, bytes)));
                }
            }
            ctx.request_repaint();
        });
    }

    fn load(&mut self, kind: FileKind, name: String, bytes: &[u8]) -> Result<String, String> {
        if bytes.len() > MAX_FILE_BYTES {
            return Err(FILE_TOO_LARGE.into());
        }
        let text = std::str::from_utf8(bytes).map_err(|_| "Choose an ACTINV JSON text file")?;
        let kind = if matches!(kind, FileKind::Auto) {
            let value: Value =
                serde_json::from_str(text).map_err(|error| format!("Invalid JSON: {error}"))?;
            if value.get("steps").is_some() {
                FileKind::Result
            } else {
                FileKind::Problem
            }
        } else {
            kind
        };
        match kind {
            FileKind::Auto | FileKind::Problem => {
                let problem = model::decode_problem(text)?;
                if !self.replace_problem_allowed() {
                    return Ok("Kept the current problem.".into());
                }
                self.saved = problem.clone();
                self.problem = problem;
                self.raw.clear();
                self.raw_source = Value::Null;
                self.raw_dirty = false;
                self.page = 1;
                Ok(format!(
                    "Opened {name}. Data paths are preserved for the desktop app."
                ))
            }
            FileKind::Result | FileKind::Compare => {
                let value =
                    serde_json::from_str(text).map_err(|error| format!("Invalid JSON: {error}"))?;
                let result = ResultDocument::parse(value, name.clone())?;
                if matches!(kind, FileKind::Compare) {
                    self.comparison = Some(result);
                } else {
                    self.result = Some(result);
                    self.comparison = None;
                    self.step = 0;
                    self.selected.clear();
                }
                self.page = 2;
                Ok(format!("Opened {name}."))
            }
        }
    }

    fn export_problem(&mut self) {
        let result = (|| {
            if self.raw_dirty {
                return Err("Apply or discard the JSON edits before downloading.".into());
            }
            Spec::from_json(&self.problem.to_string())?;
            let text =
                serde_json::to_string_pretty(&self.problem).map_err(|error| error.to_string())?;
            web::download("problem.json", text.as_bytes())?;
            self.saved = self.problem.clone();
            Ok("Problem download started. Run it with ACTINV on your computer.".into())
        })();
        self.report(result);
    }

    fn welcome(&mut self, ui: &mut egui::Ui) {
        ui.heading("Activation results, ready to explore");
        ui.label("Open a result from ACTINV to inspect inventories, activity, decay heat, and photon spectra. Prepare a problem here and run it with the desktop app.");
        ui.add_space(16.);
        ui.horizontal_wrapped(|ui| {
            if ui.button("Open result file…").clicked() { self.pick(FileKind::Result, ui.ctx()); }
            if ui.button("Try the results tutorial").clicked() {
                self.result = Some(model::tutorial_result()); self.comparison = None; self.step = 0;
                self.selected.clear(); self.page = 2;
                self.report(Ok("Tutorial: a fictional nuclide with a one-hour half-life. These are teaching values.".into()));
            }
            if ui.button("Prepare a problem").clicked() { self.page = 1; }
        });
        ui.add_space(24.);
        ui.group(|ui| {
            ui.strong("Your files stay on your device");
            ui.label("Drop an ACTINV problem or result JSON anywhere in this window, or use Open file. Files are read locally in the browser.");
            ui.label("Download any edits before closing the tab. Files are not stored on a server.");
        });
        ui.add_space(16.);
        ui.strong("Run activation calculations");
        ui.label("The desktop app runs the solver, downloads evaluated nuclear data, and imports OpenMC and MCNP tallies.");
        ui.hyperlink_to("Download ACTINV for your computer", "./download/");
    }

    fn problem(&mut self, ui: &mut egui::Ui) {
        ui.heading("Prepare a calculation");
        ui.label("Edit the iron example or open your own problem. Download the JSON and open it in the desktop app to run.");
        ui.horizontal_wrapped(|ui| {
            if ui.button("Open problem…").clicked() { self.pick(FileKind::Problem, ui.ctx()); }
            if ui.button("Load iron example").clicked() && self.replace_problem_allowed() {
                self.problem = model::decode_problem(model::EXAMPLE).expect("bundled problem");
                self.saved = self.problem.clone(); self.raw_dirty = false; self.raw.clear(); self.raw_source = Value::Null;
            }
            if ui.button("Validate problem").clicked() {
                let result = if self.raw_dirty { Err("Apply the JSON edits first.".into()) }
                    else { Spec::from_json(&self.problem.to_string()).map(|_| "Problem specification is valid. Nuclear-data files will be checked by the desktop app.".into()) };
                self.report(result);
            }
            if ui.button("Download problem").clicked() { self.export_problem(); }
        });
        ui.separator();
        ui.add_enabled_ui(!self.raw_dirty, |ui| {
            ui.horizontal(|ui| {
                ui.label("Title");
                let mut title = self.problem["title"].as_str().unwrap_or("").to_owned();
                if ui.text_edit_singleline(&mut title).changed() { self.problem["title"] = title.into(); }
            });
            ui.collapsing("Material composition", |ui| {
                ui.horizontal(|ui| {
                    ui.label("Mass (g)");
                    let mut mass = self.problem["material"]["mass_g"].as_f64().unwrap_or(1.);
                    if ui.add(egui::DragValue::new(&mut mass).custom_parser(model::parse_finite).range(0.000001..=1e30).speed(0.1)).changed() { self.problem["material"]["mass_g"] = json!(mass); }
                });
                ui.label(format!("Composition basis: {}", self.problem["material"]["basis"].as_str().unwrap_or("wt_percent")));
                if let Some(composition) = self.problem["material"]["composition"].as_object_mut() {
                    for (name, value) in composition {
                        ui.horizontal(|ui| {
                            ui.label(name);
                            let mut amount = value.as_f64().unwrap_or(0.);
                            if ui.add(egui::DragValue::new(&mut amount).custom_parser(model::parse_finite).range(0.0..=1e30).speed(0.1)).changed() { *value = json!(amount); }
                        });
                    }
                }
                ui.label("Add or remove nuclides in the JSON editor below.");
            });
            ui.collapsing("Irradiation and cooling", |ui| {
                if let Some(schedule) = self.problem["schedule"].as_array_mut() {
                    for (index, step) in schedule.iter_mut().enumerate() {
                        ui.push_id(index, |ui| ui.horizontal_wrapped(|ui| {
                            ui.label(format!("Step {}", index + 1));
                            let mut duration = step["dt"].as_str().unwrap_or("").to_owned();
                            if ui.add(egui::TextEdit::singleline(&mut duration).desired_width(100.)).changed() { step["dt"] = duration.into(); }
                            ui.label("Flux multiplier");
                            if let Some(mut flux) = step["flux"].as_f64() {
                                if ui.add(egui::DragValue::new(&mut flux).custom_parser(model::parse_finite).range(0.0..=1e30).speed(0.1)).changed() { step["flux"] = json!(flux); }
                            } else { ui.label("Edit this step's spectrum in JSON."); }
                        }));
                    }
                }
                ui.label("Durations accept units such as 300 s, 1 h, and 7 d. A zero flux multiplier means cooling.");
            });
            ui.collapsing("Incident spectrum", |ui| visuals::spectrum(ui, &self.problem));
        });
        ui.collapsing("Complete problem JSON", |ui| {
            if !self.raw_dirty && self.raw_source != self.problem {
                self.raw = serde_json::to_string_pretty(&self.problem).unwrap_or_default();
                self.raw_source = self.problem.clone();
            }
            if ui
                .add(
                    egui::TextEdit::multiline(&mut self.raw)
                        .code_editor()
                        .desired_rows(18)
                        .desired_width(f32::INFINITY),
                )
                .changed()
            {
                self.raw_dirty = true;
            }
            ui.horizontal(|ui| {
                if ui.button("Apply JSON edits").clicked() {
                    let result = model::decode_problem(&self.raw).map(|value| {
                        self.problem = value;
                        self.raw_dirty = false;
                        "JSON edits applied. Validate the problem before downloading.".into()
                    });
                    self.report(result);
                }
                if ui.button("Discard JSON edits").clicked() {
                    self.raw_dirty = false;
                }
            });
        });
    }

    fn results(&mut self, ui: &mut egui::Ui) {
        ui.heading("Results");
        ui.horizontal_wrapped(|ui| {
            if ui.button("Open result…").clicked() {
                self.pick(FileKind::Result, ui.ctx());
            }
            if ui
                .add_enabled(self.result.is_some(), egui::Button::new("Compare result…"))
                .clicked()
            {
                self.pick(FileKind::Compare, ui.ctx());
            }
            if self.comparison.is_some() && ui.button("Remove comparison").clicked() {
                self.comparison = None;
            }
        });
        let Some(result) = &self.result else {
            ui.label("Open a result file or try the tutorial from Welcome.");
            return;
        };
        ui.label(&result.label);
        ui.label(result.value["spec_title"].as_str().unwrap_or(""));
        ui.horizontal_wrapped(|ui| {
            for (i, label) in ["Activity (Bq/g)", "Total heat (W/g)", "Inventory (atoms/g)"]
                .iter()
                .enumerate()
            {
                ui.selectable_value(&mut self.metric, i, *label);
            }
            ui.checkbox(&mut self.log_time, "Log time");
        });
        ui.horizontal_wrapped(|ui| {
            ui.label("Computed step");
            ui.add(
                egui::Slider::new(&mut self.step, 0..=result.steps().len() - 1)
                    .custom_formatter(|v, _| format!("{}", v as usize + 1)),
            );
            ui.label(format!(
                "t = {:.5e} s",
                model::number(&result.steps()[self.step]["t_s"])
            ));
            if !self.selected.is_empty()
                && ui
                    .button(format!("Clear {} selection", self.selected))
                    .clicked()
            {
                self.selected.clear();
            }
        });
        let points = |doc: &ResultDocument| -> Vec<[f64; 2]> {
            doc.steps()
                .iter()
                .filter_map(|step| {
                    let t = model::number(&step["t_s"]);
                    if self.log_time && t <= 0. {
                        return None;
                    }
                    Some([
                        if self.log_time { t.log10() } else { t },
                        model::metric(step, self.metric, &self.selected),
                    ])
                })
                .collect()
        };
        let color = visuals::accent(ui);
        Plot::new(("history", self.metric, self.log_time, &self.selected))
            .height(270.)
            .legend(Legend::default())
            .x_axis_label(if self.log_time {
                "log10(Time / s)"
            } else {
                "Time (s)"
            })
            .y_axis_label(
                ["Activity (Bq/g)", "Total heat (W/g)", "Inventory (atoms/g)"][self.metric],
            )
            .show(ui, |plot| {
                plot.line(Line::new(&result.label, points(result)).color(color));
                if let Some(comparison) = &self.comparison {
                    plot.line(
                        Line::new(&comparison.label, points(comparison))
                            .color(Color32::from_rgb(198, 104, 25)),
                    );
                }
            });
        if self.comparison.is_some() {
            ui.label("Comparison uses each file's recorded normalization and absolute time. Check that the material, inputs, and schedules are comparable.");
        }
        if self.log_time {
            ui.small("Zero-time points are omitted from the logarithmic plot. Exports retain physical time.");
        }
        let step = &result.steps()[self.step];
        visuals::contributors(ui, step, &mut self.selected);
        ui.separator();
        ui.horizontal(|ui| {
            ui.label("Find nuclide");
            ui.text_edit_singleline(&mut self.filter);
        });
        egui::Grid::new("inventory").striped(true).show(ui, |ui| {
            ui.strong("Nuclide");
            ui.strong("Atoms/g");
            ui.strong("Bq/g");
            ui.end_row();
            if let Some(rows) = step["inventory"].as_array() {
                for row in rows {
                    let name = row["nuclide"].as_str().unwrap_or("");
                    if !name.to_lowercase().contains(&self.filter.to_lowercase()) {
                        continue;
                    }
                    if ui.selectable_label(self.selected == name, name).clicked() {
                        self.selected = name.into();
                    }
                    ui.label(format!("{:.6e}", model::number(&row["atoms_per_g"])));
                    ui.label(format!(
                        "{:.6e}",
                        model::number(&step["activity_Bq_per_g"][name])
                    ));
                    ui.end_row();
                }
            }
        });
        let mut exported = None;
        ui.horizontal_wrapped(|ui| {
            if ui.button("Download inventory CSV").clicked() {
                exported = Some(
                    model::inventory_csv(result, self.step)
                        .and_then(|csv| web::download("inventory.csv", csv.as_bytes())),
                );
            }
            if ui.button("Download result JSON").clicked() {
                exported = Some(
                    serde_json::to_vec_pretty(&result.value)
                        .map_err(|error| error.to_string())
                        .and_then(|bytes| web::download("result.json", &bytes)),
                );
            }
        });
        if let Some(exported) = exported {
            self.report(exported.map(|()| "Download started.".into()));
        }
    }

    fn record(&self, ui: &mut egui::Ui) {
        ui.heading("Result record");
        let Some(result) = &self.result else {
            ui.label("Open a result file first.");
            return;
        };
        for (label, key) in [
            ("Inputs and calculation details", "certificate"),
            ("Data gaps and diagnostics", "ledger"),
        ] {
            ui.collapsing(label, |ui| {
                let mut text = serde_json::to_string_pretty(&result.value[key]).unwrap_or_default();
                ui.add(
                    egui::TextEdit::multiline(&mut text)
                        .code_editor()
                        .desired_width(f32::INFINITY)
                        .desired_rows(16)
                        .interactive(false),
                );
            });
        }
        ui.collapsing("Photon source at the selected step", |ui| {
            let source = &result.steps()[self.step]["photon_source"];
            if source.is_null() {
                ui.label("This result contains no photon source at the selected step.");
            } else {
                if let Some(groups) = source["groups"].as_array() {
                    let bars: Vec<_> = groups
                        .iter()
                        .filter_map(|group| {
                            let low = group["low_eV"].as_f64()?;
                            let high = group["high_eV"].as_f64()?;
                            let centroid = group["centroid_eV"].as_f64()?;
                            let emission = group["photons_s_g"].as_f64()?;
                            (high > low && emission >= 0.).then(|| {
                                egui_plot::Bar::new(centroid / 1e6, emission)
                                    .width((high - low) / 1e6)
                            })
                        })
                        .collect();
                    let color = visuals::accent(ui);
                    Plot::new("photon-groups")
                        .height(230.)
                        .x_axis_label("Photon energy (MeV)")
                        .y_axis_label("Photons / s / g per group")
                        .show(ui, |plot| {
                            plot.bar_chart(
                                egui_plot::BarChart::new("Decay photons", bars).color(color),
                            )
                        });
                }
                let mut text = serde_json::to_string_pretty(source).unwrap_or_default();
                ui.add(
                    egui::TextEdit::multiline(&mut text)
                        .code_editor()
                        .desired_width(f32::INFINITY)
                        .desired_rows(18)
                        .interactive(false),
                );
            }
        });
    }
}

impl eframe::App for Workbench {
    fn logic(&mut self, ctx: &egui::Context, _: &mut eframe::Frame) {
        if let Some(rx) = &self.pending_file {
            match rx.try_recv() {
                Ok(read) => {
                    self.pending_file = None;
                    let result = read.and_then(|(kind, name, bytes)| self.load(kind, name, &bytes));
                    self.report(result);
                }
                Err(mpsc::TryRecvError::Disconnected) => {
                    self.pending_file = None;
                }
                Err(mpsc::TryRecvError::Empty) => {}
            }
        }
        let drops = ctx.input(|input| input.raw.dropped_files.clone());
        let dropped = drops.len();
        if let Some(file) = drops.into_iter().next() {
            if self.pending_file.is_some() {
                return;
            }
            if dropped > 1 {
                // Only one file is read per drop; say so instead of discarding the rest silently.
                self.report(Err(format!(
                    "{dropped} files were dropped; only the first is opened. Drop one file at a time."
                )));
            }
            let (tx, rx) = mpsc::channel();
            self.pending_file = Some(rx);
            let ctx = ctx.clone();
            wasm_bindgen_futures::spawn_local(async move {
                let name = file.path().to_string_lossy().into_owned();
                let result = if file
                    .web_file()
                    .is_some_and(|file| file.size() > MAX_FILE_BYTES as f64)
                {
                    Err(FILE_TOO_LARGE.into())
                } else {
                    file.bytes_async()
                        .await
                        .map(|bytes| (FileKind::Auto, name, bytes))
                };
                let _ = tx.send(result);
                ctx.request_repaint();
            });
        }
    }

    fn ui(&mut self, ui: &mut egui::Ui, _: &mut eframe::Frame) {
        egui::Panel::top("navigation").show(ui, |ui| {
            ui.horizontal_wrapped(|ui| {
                for (index, title) in ["Welcome", "Problem", "Results", "Result record"]
                    .iter()
                    .enumerate()
                {
                    ui.selectable_value(&mut self.page, index, *title);
                }
                ui.separator();
                if ui.checkbox(&mut self.dark, "Dark theme").changed() {
                    ui.ctx().set_theme(if self.dark {
                        egui::ThemePreference::Dark
                    } else {
                        egui::ThemePreference::Light
                    });
                }
                if self.pending_file.is_some() {
                    ui.spinner();
                }
                if self.raw_dirty || self.problem != self.saved {
                    ui.label("Problem has unsaved changes");
                }
            })
        });
        egui::Panel::bottom("status").show(ui, |ui| {
            ui.label(RichText::new(&self.status).color(if self.error {
                ui.visuals().error_fg_color
            } else {
                ui.visuals().text_color()
            }));
        });
        egui::CentralPanel::default().show(ui, |ui| {
            egui::ScrollArea::vertical().show(ui, |ui| {
                ui.set_max_width(1100.);
                ui.add_space(16.);
                match self.page {
                    1 => self.problem(ui),
                    2 => self.results(ui),
                    3 => self.record(ui),
                    _ => self.welcome(ui),
                }
            });
        });
    }
}
