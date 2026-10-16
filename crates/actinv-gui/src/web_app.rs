use crate::{
    model::{self, ResultDocument},
    nav::Page,
    views::{
        import::{self, ImportAction, ImportSlot, ImportState},
        material::{self, MaterialState},
        outputs::{self, OutputsAction, OutputsState},
        pathways::{self, PathwaysAction, PathwaysState},
        photons::{self, PhotonsAction, PhotonsState},
        steps::{self, StepsAction, StepsState},
        tables::{self, TablesAction, TablesState},
        waste::{self, Detected},
        Marks, Picker, Pinned, Target,
    },
    visuals::{self, BLUE},
    web,
};
use actinv_core::spec::Spec;
use eframe::egui::{self, Color32, RichText};
use egui_plot::{Legend, Line, Plot};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::sync::mpsc::{self, Receiver};

const MAX_FILE_BYTES: usize = 32 * 1024 * 1024;
const FILE_TOO_LARGE: &str =
    "This workbench accepts files up to 32 MiB. Use the desktop app for larger results.";
/// Shown beside the pickers: a browser cannot reveal where a file lives on disk.
const PIN_NOTE: &str = "Files chosen here are recorded by file name with their SHA-256. Keep each file where you run ACTINV so that name resolves, or edit the path.";
const METRICS: [&str; 3] = ["Activity (Bq/g)", "Total heat (W/g)", "Inventory (atoms/g)"];
type FileRead = Result<(FileKind, String, Vec<u8>), String>;

/// The spec field a chosen file is pinned into.
#[derive(Clone, Copy, Debug, PartialEq)]
enum PinField {
    /// `{path, sha256}` replaces the object at this dotted path.
    Set(&'static str),
    /// `{path, sha256}` is appended to the array at this dotted path.
    Push(&'static str),
}

#[derive(Clone, Copy)]
enum FileKind {
    Auto,
    Problem,
    Result,
    Compare,
    /// A data file whose name and SHA-256 are written into a spec field.
    Pin(PinField),
    /// One of the three files of the deck import dialog.
    Import(ImportSlot),
    /// A one-column spectrum text file for the Spectrum page.
    Spectrum,
    /// A waste result, an intrusion-screen result or a twin result with `waste_classification`.
    WasteResult,
    /// An `actinv-waste-spec-1` document for the editor on the Waste class page.
    WasteSpec,
}

/// A file choice a shared view asked for while it was drawn.
struct PinRequest {
    title: String,
    extensions: Vec<String>,
}

/// The browser's `Picker`. The views call it synchronously, but a browser file dialog answers
/// later, so it only records the request and answers `None`; the workbench then opens the
/// asynchronous dialog and writes the result into the field the request names.
#[derive(Default)]
struct BrowserPicker {
    request: Option<PinRequest>,
}

impl Picker for BrowserPicker {
    fn pick(&mut self, title: &str, extensions: &[&str]) -> Option<Pinned> {
        self.request = Some(PinRequest {
            title: title.to_owned(),
            extensions: extensions.iter().map(|e| (*e).to_owned()).collect(),
        });
        None
    }
}

/// The field each picker title writes. The shared views name the dialog, not the field, so this
/// list follows the `picker.pick(...)` calls in `views/outputs.rs` and `views/steps.rs`.
fn pin_field_for(title: &str) -> Option<PinField> {
    Some(match title {
        "Kerma table" => PinField::Set("kerma.table"),
        "Photon response data" => PinField::Set("photon.response"),
        "Radiological response table" => PinField::Set("radiological.table"),
        "Covariance sidecar" => PinField::Set("uncertainty.covariance"),
        "Resonance evaluation (ENDF-6)" => {
            PinField::Push("self_shielding.geometry.resonance_files")
        }
        _ => return None,
    })
}

/// Write `value` at a dotted path, creating the objects on the way.
fn set_dotted(doc: &mut Value, path: &str, value: Value) {
    let mut node = doc;
    for key in path.split('.') {
        if !node.is_object() {
            *node = json!({});
        }
        node = node
            .as_object_mut()
            .expect("made an object above")
            .entry(key.to_owned())
            .or_insert(Value::Null);
    }
    *node = value;
}

/// Record a chosen file in the document: its name (the browser has no folder) and SHA-256.
fn apply_pin(doc: &mut Value, field: PinField, name: &str, sha256: &str) {
    let pinned = json!({"path": name, "sha256": sha256});
    match field {
        PinField::Set(path) => set_dotted(doc, path, pinned),
        PinField::Push(path) => {
            let pointer = format!("/{}", path.replace('.', "/"));
            let mut list = doc
                .pointer(&pointer)
                .and_then(Value::as_array)
                .cloned()
                .unwrap_or_default();
            list.push(pinned);
            set_dotted(doc, path, Value::Array(list));
        }
    }
}

/// The setup page a view asked to open.
fn page_for(target: Target) -> Page {
    match target {
        Target::Overview => Page::Overview,
        Target::Material => Page::Material,
        Target::Schedule => Page::Schedule,
        Target::Spectrum => Page::Spectrum,
        Target::Outputs => Page::Outputs,
    }
}

/// The pages the browser offers: the desktop's, less the two that run the solver.
fn browser_pages() -> impl Iterator<Item = Page> {
    Page::ALL
        .into_iter()
        .filter(|page| !matches!(page, Page::Sweep | Page::Assimilation))
}

pub struct Workbench {
    page: Page,
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
    log_time: bool,
    ledger_filter: String,
    spectrum_text: String,
    material_view: MaterialState,
    steps: StepsState,
    outputs: OutputsState,
    tables: TablesState,
    photons: PhotonsState,
    pathways: PathwaysState,
    /// The waste result on the Waste class page: its file name and what it parsed to.
    waste_result: Option<(String, waste::WasteView)>,
    /// The waste spec in the editor on the Waste class page.
    waste_spec: String,
    /// The deck import dialog while it is open.
    import: Option<ImportState>,
    dark: bool,
    suite: avila_account::ui_web::WebSuite,
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
            page: Page::Overview,
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
            log_time: false,
            ledger_filter: String::new(),
            spectrum_text: String::new(),
            material_view: MaterialState::default(),
            steps: StepsState::default(),
            outputs: OutputsState::default(),
            tables: TablesState::default(),
            photons: PhotonsState::default(),
            pathways: PathwaysState::default(),
            waste_result: None,
            waste_spec: String::new(),
            import: None,
            dark: false,
            suite: avila_account::ui_web::WebSuite::new(&cc.egui_ctx, "actinv"),
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

    /// Open the browser's file dialog; the chosen file arrives in `logic` as `kind`. No filter
    /// when `extensions` is empty.
    fn pick(&mut self, kind: FileKind, extensions: &[&str], ctx: &egui::Context) {
        if self.pending_file.is_some() {
            return;
        }
        let (tx, rx) = mpsc::channel();
        self.pending_file = Some(rx);
        let ctx = ctx.clone();
        let extensions: Vec<String> = extensions.iter().map(|e| (*e).to_owned()).collect();
        wasm_bindgen_futures::spawn_local(async move {
            let mut dialog = rfd::AsyncFileDialog::new();
            if !extensions.is_empty() {
                dialog = dialog.add_filter("Files", &extensions);
            }
            if let Some(file) = dialog.pick_file().await {
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

    /// Start the file dialog a view asked for through the `BrowserPicker`.
    fn start_pin(&mut self, request: Option<PinRequest>, ctx: &egui::Context) {
        let Some(request) = request else {
            return;
        };
        match pin_field_for(&request.title) {
            Some(field) => {
                let extensions: Vec<&str> = request.extensions.iter().map(String::as_str).collect();
                self.pick(FileKind::Pin(field), &extensions, ctx);
            }
            None => self.report(Err(format!(
                "{} cannot be chosen in the browser. Type the file name and its SHA-256 instead.",
                request.title
            ))),
        }
    }

    fn load(&mut self, kind: FileKind, name: String, bytes: &[u8]) -> Result<String, String> {
        if bytes.len() > MAX_FILE_BYTES {
            return Err(FILE_TOO_LARGE.into());
        }
        match kind {
            FileKind::Pin(field) => {
                let sha256 = format!("{:x}", Sha256::digest(bytes));
                apply_pin(&mut self.problem, field, &name, &sha256);
                return Ok(format!(
                    "Pinned {name} (SHA-256 {}…). The problem records the file name only; keep the file where you run ACTINV.",
                    &sha256[..12]
                ));
            }
            FileKind::Import(slot) => {
                // Read leniently: a stray non-UTF-8 byte in a deck must not stop the conversion.
                let text = String::from_utf8_lossy(bytes).into_owned();
                return match &mut self.import {
                    Some(state) => {
                        state.set_file(slot, name.clone(), text);
                        Ok(format!("Read {name}."))
                    }
                    None => Err("The import dialog was closed; the file was not used.".into()),
                };
            }
            FileKind::Spectrum => {
                self.spectrum_text = String::from_utf8_lossy(bytes).into_owned();
                return Ok(format!(
                    "Read {name}. Check and apply the group values below."
                ));
            }
            FileKind::Auto
            | FileKind::Problem
            | FileKind::Result
            | FileKind::Compare
            | FileKind::WasteResult
            | FileKind::WasteSpec => {}
        }
        let text = std::str::from_utf8(bytes).map_err(|_| "Choose an ACTINV JSON text file")?;
        let kind = if matches!(kind, FileKind::Auto) {
            let value: Value =
                serde_json::from_str(text).map_err(|error| format!("Invalid JSON: {error}"))?;
            match waste::detect(&value) {
                Some(Detected::Spec) => FileKind::WasteSpec,
                Some(Detected::Result) => FileKind::WasteResult,
                None if value.get("steps").is_some() => FileKind::Result,
                None => FileKind::Problem,
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
                self.page = Page::Overview;
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
                    self.tables = TablesState::default();
                    self.photons = PhotonsState::default();
                    self.pathways = PathwaysState::default();
                }
                self.page = Page::Charts;
                Ok(format!("Opened {name}."))
            }
            FileKind::WasteResult => {
                self.waste_result = Some((name.clone(), waste::read_result(text)?));
                self.page = Page::Waste;
                Ok(format!("Opened {name}."))
            }
            FileKind::WasteSpec => {
                waste::check_spec(text)?;
                self.waste_spec = text.to_owned();
                self.page = Page::Waste;
                Ok(format!(
                    "Opened {name}. Edit it below, then download it and run it with `actinv waste`."
                ))
            }
            // handled above
            FileKind::Pin(_) | FileKind::Import(_) | FileKind::Spectrum => Ok(String::new()),
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

    /// Replace the problem with a converted deck. It is a new, unsaved problem.
    fn use_imported(&mut self, spec: Value) -> bool {
        let problem = match model::decode_problem(&spec.to_string()) {
            Ok(problem) => problem,
            Err(error) => {
                self.report(Err(format!(
                    "The converted deck is not a valid ACTINV problem: {error}"
                )));
                return false;
            }
        };
        if !self.replace_problem_allowed() {
            self.report(Ok("Kept the current problem.".into()));
            return false;
        }
        self.problem = problem;
        // no clean copy: the converted problem has not been downloaded yet
        self.saved = Value::Null;
        self.raw.clear();
        self.raw_source = Value::Null;
        self.raw_dirty = false;
        self.page = Page::Overview;
        self.report(Ok("Deck imported as a new problem. Add anything the report listed as required, then download it.".into()));
        true
    }

    /// The import dialog: the shared view in a window, and what its actions need from the browser.
    fn import_dialog(&mut self, ctx: &egui::Context) {
        let Some(mut state) = self.import.take() else {
            return;
        };
        let mut action = ImportAction::None;
        let height = (ctx.content_rect().height() - 140.).max(200.);
        egui::Window::new("Import FISPACT-II deck")
            .collapsible(false)
            .default_width(780.)
            .show(ctx, |ui| {
                egui::ScrollArea::vertical()
                    .max_height(height)
                    .show(ui, |ui| {
                        action = import::show(ui, &mut state);
                    });
            });
        let mut keep = true;
        match action {
            ImportAction::None => {}
            ImportAction::Choose(slot) => self.pick(FileKind::Import(slot), &[], ctx),
            ImportAction::Use(spec) => keep = !self.use_imported(spec),
            ImportAction::SaveReport {
                file_name,
                contents,
            } => {
                let saved = web::download(&file_name, contents.as_bytes());
                self.report(saved.map(|()| "Report download started.".into()));
            }
            ImportAction::Open(target) => self.page = page_for(target),
            ImportAction::Close => keep = false,
        }
        if keep {
            self.import = Some(state);
        }
    }

    /// Setup editors pause while raw JSON edits are pending, as on the desktop.
    fn editors(&mut self, ui: &mut egui::Ui, body: impl FnOnce(&mut Self, &mut egui::Ui)) {
        if self.raw_dirty {
            ui.label("JSON edits are pending on Advanced JSON. Apply or discard them to use the other editors.");
        }
        ui.add_enabled_ui(!self.raw_dirty, |ui| body(self, ui));
    }

    fn overview(&mut self, ui: &mut egui::Ui) {
        visuals::heading(
            ui,
            "Overview & data",
            "Open a result to explore it, or prepare a problem here and run it with the desktop app or the command line.",
        );
        ui.group(|ui| {
            ui.strong("Welcome to the ACTINV workbench");
            ui.label("Open a result from ACTINV to inspect inventories, activity, decay heat, and photon spectra. Prepare a problem here and run it with the desktop app.");
            ui.horizontal_wrapped(|ui| {
                if ui.button("Open result file…").clicked() {
                    self.pick(FileKind::Result, &["json"], ui.ctx());
                }
                if ui.button("Try the results tutorial").clicked() {
                    self.result = Some(model::tutorial_result());
                    self.comparison = None;
                    self.step = 0;
                    self.selected.clear();
                    self.page = Page::Charts;
                    self.report(Ok("Tutorial: a fictional nuclide with a one-hour half-life. These are teaching values.".into()));
                }
            });
        });
        ui.add_space(16.);
        ui.label("Calculation title");
        ui.add_enabled_ui(!self.raw_dirty, |ui| {
            visuals::text_field(ui, &mut self.problem["title"]);
        });
        ui.horizontal_wrapped(|ui| {
            if ui.button("Open problem…").clicked() {
                self.pick(FileKind::Problem, &["json"], ui.ctx());
            }
            if ui.button("Load iron example").clicked() && self.replace_problem_allowed() {
                self.problem = model::decode_problem(model::EXAMPLE).expect("bundled problem");
                self.saved = self.problem.clone();
                self.raw_dirty = false;
                self.raw.clear();
                self.raw_source = Value::Null;
            }
            if ui
                .button("Import FISPACT-II deck…")
                .on_hover_text("Convert a FISPACT-II input deck into an ACTINV problem")
                .clicked()
            {
                self.import.get_or_insert_with(ImportState::default);
            }
            if ui.button("Validate problem").clicked() {
                let result = if self.raw_dirty {
                    Err("Apply the JSON edits first.".into())
                } else {
                    Spec::from_json(&self.problem.to_string()).map(|_| "Problem specification is valid. Nuclear-data files will be checked by the desktop app.".into())
                };
                self.report(result);
            }
            if ui.button("Download problem").clicked() {
                self.export_problem();
            }
        });
        ui.add_space(8.);
        self.editors(ui, |this, ui| {
            egui::Frame::group(ui.style())
                .inner_margin(16.)
                .show(ui, |ui| {
                    ui.heading("Nuclear data");
                    ui.label("Paths are resolved by the program that runs the problem. The browser records them but does not read the files.");
                    ui.horizontal(|ui| {
                        ui.label("Activation library (.npz)");
                        visuals::text_field(ui, &mut this.problem["library"]["path"]);
                    });
                    ui.horizontal(|ui| {
                        ui.label("Primary decay data");
                        visuals::text_field(ui, &mut this.problem["decay"]["primary"]);
                    });
                });
            ui.add_space(16.);
            this.projectile_and_solver(ui);
        });
        ui.add_space(8.);
        ui.label("Photons and dose, pathways, step summary, regulatory tables, self-shielding and uncertainty are chosen on the Outputs page.");
        ui.horizontal_wrapped(|ui| {
            if ui.small_button("Open Outputs").clicked() {
                self.page = Page::Outputs;
            }
            if ui.button("Continue to material >").clicked() {
                self.page = Page::Material;
            }
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

    fn projectile_and_solver(&mut self, ui: &mut egui::Ui) {
        // Projectiles the picker offers; triton, helion and alpha stay in the JSON.
        const PROJECTILES: [&str; 4] = ["neutron", "proton", "deuteron", "gamma"];
        ui.horizontal(|ui| {
            ui.label("Projectile");
            let projectile = self.problem["projectile"]
                .as_str()
                .unwrap_or("neutron")
                .to_owned();
            if PROJECTILES.contains(&projectile.as_str()) {
                egui::ComboBox::from_id_salt("projectile")
                    .selected_text(&projectile)
                    .show_ui(ui, |ui| {
                        for choice in PROJECTILES {
                            if ui.selectable_label(projectile == choice, choice).clicked() {
                                self.problem["projectile"] = choice.into();
                            }
                        }
                    });
            } else {
                ui.label(format!("{projectile} (set in Advanced JSON)"));
            }
        });
        if self.problem["projectile"].as_str().unwrap_or("neutron") != "neutron" {
            ui.label("Charged-particle and photonuclear problems need a library for that projectile and a temperature of 0 K.");
        }
        ui.add_space(16.);
        ui.group(|ui| {
            ui.strong("Solver");
            let mode = self.problem["options"]["mode"]
                .as_str()
                .unwrap_or("auto")
                .to_owned();
            ui.horizontal(|ui| {
                ui.label("Solver mode");
                egui::ComboBox::from_id_salt("mode")
                    .selected_text(&mode)
                    .show_ui(ui, |ui| {
                        for choice in ["auto", "trace", "coupled"] {
                            if ui.selectable_label(mode == choice, choice).clicked() {
                                self.problem["options"]["mode"] = choice.into();
                            }
                        }
                    });
            });
            ui.label("Trace mode supports ranked production pathways. Auto chooses an appropriate mode based on the problem.");
            let mut temperature = self.problem["options"]["temperature_K"]
                .as_f64()
                .unwrap_or(293.6);
            ui.horizontal(|ui| {
                ui.label("Temperature (K)");
                if ui
                    .add(
                        egui::DragValue::new(&mut temperature)
                            .custom_parser(model::parse_finite)
                            .speed(1.)
                            .range(0.0..=f64::MAX),
                    )
                    .changed()
                {
                    self.problem["options"]["temperature_K"] = json!(temperature);
                }
            });
        });
    }

    fn material_page(&mut self, ui: &mut egui::Ui) {
        visuals::heading(
            ui,
            "Define the material",
            "Use element symbols for natural composition, or individual isotope names for enrichment.",
        );
        self.editors(ui, |this, ui| {
            let mut marks = Marks::default();
            material::show(ui, &mut this.problem, &mut this.material_view, &mut marks);
            ui.add_space(20.);
            if ui.button("Continue to irradiation & cooling >").clicked() {
                this.page = Page::Schedule;
            }
        });
    }

    fn schedule_page(&mut self, ui: &mut egui::Ui) {
        visuals::heading(
            ui,
            "Irradiation & cooling",
            "Durations accept units such as 5 min, 2 h, or 10 y. Each step is driven by a flux multiplier, a wall loading or a power; a zero flux multiplier is cooling.",
        );
        self.editors(ui, |this, ui| {
            let mut marks = Marks::default();
            let mut picker = BrowserPicker::default();
            let action = steps::show(
                ui,
                &mut this.problem,
                &mut this.steps,
                &mut picker,
                &mut marks,
            );
            if picker.request.is_some() {
                // the view reports a cancelled choice for any answer of `None`; the dialog is
                // still to come, so drop that note
                this.steps = StepsState::default();
            }
            this.start_pin(picker.request.take(), ui.ctx());
            if let StepsAction::Open(target) = action {
                this.page = page_for(target);
            }
            ui.small(PIN_NOTE);
            ui.add_space(16.);
            if ui.button("Continue to spectrum >").clicked() {
                this.page = Page::Spectrum;
            }
        });
    }

    fn spectrum_page(&mut self, ui: &mut egui::Ui) {
        visuals::heading(
            ui,
            "Incident spectrum",
            "Review group values and explicitly choose the normalization used by the solver.",
        );
        self.editors(ui, |this, ui| {
            let accent = visuals::accent(ui);
            ui.horizontal(|ui| {
                ui.label("Group structure");
                visuals::text_field(ui, &mut this.problem["spectrum"]["structure"]);
            });
            let mut normalized = this.problem["spectrum"]["total"].is_number();
            if ui
                .checkbox(
                    &mut normalized,
                    "Normalize group values to a total flux (particles / cm^2 / s)",
                )
                .changed()
            {
                if normalized {
                    this.problem["spectrum"]["total"] = json!(1e12);
                } else if let Some(spectrum) = this.problem["spectrum"].as_object_mut() {
                    spectrum.remove("total");
                }
            }
            if normalized {
                visuals::numeric(ui, &mut this.problem["spectrum"]["total"], 1e10);
            } else {
                ui.label("Group values are absolute group fluxes (particles / cm^2 / s).");
            }
            let mut descending = this.problem["spectrum"]["descending"]
                .as_bool()
                .unwrap_or(false);
            if ui
                .checkbox(&mut descending, "Input groups are highest-energy first")
                .changed()
            {
                this.problem["spectrum"]["descending"] = descending.into();
            }
            let points: Vec<[f64; 2]> = this.problem["spectrum"]["flux_per_group"]
                .as_array()
                .map(|values| {
                    values
                        .iter()
                        .enumerate()
                        .map(|(i, v)| [i as f64 + 1., model::number(v)])
                        .collect()
                })
                .unwrap_or_default();
            Plot::new("incident")
                .height(260.)
                .allow_scroll(false)
                .x_axis_label("Input group index")
                .y_axis_label(if normalized {
                    "Relative group weight"
                } else {
                    "Group flux / cm^2 / s"
                })
                .show(ui, |plot| {
                    plot.line(Line::new("Incident spectrum", points).color(accent))
                });
            ui.collapsing("Edit group values", |ui| {
                if let Some(values) = this.problem["spectrum"]["flux_per_group"].as_array_mut() {
                    egui::ScrollArea::vertical().max_height(200.).show_rows(
                        ui,
                        28.,
                        values.len(),
                        |ui, range| {
                            for i in range {
                                ui.horizontal(|ui| {
                                    ui.label(format!("Group {}", i + 1));
                                    visuals::numeric(ui, &mut values[i], 1.);
                                });
                            }
                        },
                    );
                }
            });
            if this.problem["spectrum"]["structure"].as_str() == Some("custom") {
                if let Some(boundaries) = outputs::boundaries_edit(
                    ui,
                    &this.problem["spectrum"]["boundaries_eV"],
                    "incident-boundaries",
                ) {
                    this.problem["spectrum"]["boundaries_eV"] = boundaries;
                }
            }
            ui.collapsing("Spectrum by physical energy", |ui| {
                visuals::spectrum(ui, &this.problem)
            });
            ui.collapsing("Import or paste group values", |ui| {
                ui.label("Paste one flux column from a spreadsheet, or open a one-column CSV/text file. Optional header: flux. Set normalization and energy order above explicitly.");
                if ui.button("Open CSV / text").clicked() {
                    this.pick(FileKind::Spectrum, &["csv", "txt"], ui.ctx());
                }
                ui.add(
                    egui::TextEdit::multiline(&mut this.spectrum_text)
                        .desired_rows(5)
                        .hint_text("flux\n1.2e10\n3.4e9\n…"),
                );
                if ui.button("Check and apply group values").clicked() {
                    let named = this.problem["spectrum"]["structure"]
                        .as_str()
                        .map(actinv_data::groups::GroupStructure::named);
                    let count = match named {
                        Some(Ok(Some(group))) => group.groups(),
                        _ => this.problem["spectrum"]["boundaries_eV"]
                            .as_array()
                            .map_or(0, |v| v.len().saturating_sub(1)),
                    };
                    match crate::model::parse_group_values(&this.spectrum_text, count) {
                        Ok(values) => {
                            this.problem["spectrum"]["flux_per_group"] = json!(values);
                            this.report(Ok(format!("Imported {count} group values. Review normalization and energy order before downloading.")));
                        }
                        Err(error) => this.report(Err(error)),
                    }
                }
            });
            ui.label("Custom boundaries and optional photon, uncertainty, or response settings are available in Advanced JSON.");
        });
    }

    fn outputs_page(&mut self, ui: &mut egui::Ui) {
        visuals::heading(
            ui,
            "Outputs",
            "Choose what ACTINV should compute besides the inventory, activity and decay heat.",
        );
        self.editors(ui, |this, ui| {
            let mut marks = Marks::default();
            let mut picker = BrowserPicker::default();
            let action = outputs::show(
                ui,
                &mut this.problem,
                &mut this.outputs,
                &mut picker,
                &mut marks,
            );
            this.start_pin(picker.request.take(), ui.ctx());
            match action {
                OutputsAction::None => {}
                OutputsAction::Open(target) => this.page = page_for(target),
            }
            ui.small(PIN_NOTE);
        });
    }

    /// The bar every results page shows: file actions, the loaded result and the step slider.
    fn result_bar(&mut self, ui: &mut egui::Ui) {
        let mut exported = None;
        ui.horizontal_wrapped(|ui| {
            if ui.button("Open result…").clicked() {
                self.pick(FileKind::Result, &["json"], ui.ctx());
            }
            if ui
                .add_enabled(self.result.is_some(), egui::Button::new("Compare result…"))
                .clicked()
            {
                self.pick(FileKind::Compare, &["json"], ui.ctx());
            }
            if self.comparison.is_some() && ui.button("Remove comparison").clicked() {
                self.comparison = None;
            }
            if let Some(result) = &self.result {
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
            }
        });
        if let Some(exported) = exported {
            self.report(exported.map(|()| "Download started.".into()));
        }
        let Some(result) = &self.result else {
            return;
        };
        let steps = result.steps().len();
        self.step = self.step.min(steps.saturating_sub(1));
        ui.label(RichText::new(&result.label).strong());
        if let Some(title) = result.value["spec_title"].as_str() {
            ui.label(title);
        }
        if let Some(other) = &self.comparison {
            ui.label(format!(
                "Comparison: {} · absolute physical time; normalization is as recorded in each result",
                other.label
            ));
        }
        ui.horizontal_wrapped(|ui| {
            ui.label("Computed step");
            ui.add(
                egui::Slider::new(&mut self.step, 0..=steps.saturating_sub(1))
                    .custom_formatter(|v, _| format!("{}", v as usize + 1)),
            );
            ui.label(format!(
                "of {steps} · t = {:.5e} s",
                model::number(&result.steps()[self.step]["t_s"])
            ));
        });
        ui.add_space(8.);
    }

    /// Heading, result bar and the empty state of a results page. True when a result is loaded.
    fn result_page(&mut self, ui: &mut egui::Ui, title: &str, subtitle: &str) -> bool {
        visuals::heading(ui, title, subtitle);
        self.result_bar(ui);
        if self.result.is_none() {
            ui.group(|ui| {
                ui.add_space(30.);
                ui.heading("Your results will appear here");
                ui.label("Open a result JSON, or try the tutorial from Overview & data, to explore activity, decay heat, and nuclide inventory.");
                ui.add_space(30.);
            });
            return false;
        }
        true
    }

    fn charts(&mut self, ui: &mut egui::Ui) {
        if !self.result_page(
            ui,
            "Charts",
            "Follow activity, decay heat and inventory through time, and pick the step to inspect.",
        ) {
            return;
        }
        let accent = visuals::accent(ui);
        let Some(result) = &self.result else {
            return;
        };
        ui.horizontal_wrapped(|ui| {
            for (i, label) in METRICS.iter().enumerate() {
                ui.selectable_value(&mut self.metric, i, *label);
            }
            ui.checkbox(&mut self.log_time, "Log time");
        });
        if !self.selected.is_empty() {
            ui.horizontal(|ui| {
                ui.label(format!("Selected: {}", self.selected));
                if ui.small_button("Clear selection").clicked() {
                    self.selected.clear();
                }
            });
        }
        let log_time = self.log_time;
        let metric = self.metric;
        let plot_time = |t: f64| if log_time { t.log10() } else { t };
        let points = |doc: &ResultDocument| -> Vec<[f64; 2]> {
            doc.steps()
                .iter()
                .filter(|step| !log_time || model::number(&step["t_s"]) > 0.)
                .map(|step| {
                    [
                        plot_time(model::number(&step["t_s"])),
                        model::metric(step, metric, &self.selected),
                    ]
                })
                .collect()
        };
        // The recorded MF=33 normal interval of the plotted response, when the result has one.
        let response_key = match metric {
            1 => Some("heat.total".to_owned()),
            0 if !self.selected.is_empty() => Some(format!("activity:{}", self.selected)),
            _ => None,
        };
        let band = response_key.as_ref().and_then(|key| {
            result
                .steps()
                .iter()
                .filter(|step| !log_time || model::number(&step["t_s"]) > 0.)
                .map(|step| {
                    let interval = &step["uncertainty"]["responses"][key]["normal_interval"];
                    Some((
                        plot_time(step["t_s"].as_f64()?),
                        interval[0].as_f64()?,
                        interval[1].as_f64()?,
                    ))
                })
                .collect::<Option<Vec<_>>>()
        });
        if band.is_some() {
            ui.label("Shaded band: recorded MF=33 normal interval. Confidence and coverage are in the uncertainty record on Ledger & certificate.");
        }
        let chart = Plot::new(("history", metric, log_time, &self.selected))
            .height(270.)
            .allow_scroll(false)
            .legend(Legend::default())
            .x_axis_label(if log_time {
                "log10(Time / s)"
            } else {
                "Time (s)"
            })
            .y_axis_label(METRICS[metric])
            .show(ui, |plot| {
                if let Some(band) = &band {
                    let xs: Vec<_> = band.iter().map(|b| b.0).collect();
                    let lower: Vec<_> = band.iter().map(|b| b.1).collect();
                    let upper: Vec<_> = band.iter().map(|b| b.2).collect();
                    plot.add(
                        egui_plot::FilledArea::new("MF=33 normal interval", &xs, &lower, &upper)
                            .fill_color(accent.gamma_multiply(0.2)),
                    );
                }
                plot.line(Line::new(&result.label, points(result)).color(accent));
                if let Some(comparison) = &self.comparison {
                    plot.line(
                        Line::new(&comparison.label, points(comparison))
                            .color(Color32::from_rgb(198, 104, 25)),
                    );
                }
                let selected_t = model::number(&result.steps()[self.step]["t_s"]);
                if !log_time || selected_t > 0. {
                    plot.vline(
                        egui_plot::VLine::new("Selected step", plot_time(selected_t))
                            .color(Color32::GRAY),
                    );
                }
                if plot.response().clicked() {
                    plot.pointer_coordinate().map(|c| c.x)
                } else {
                    None
                }
            });
        if let Some(time) = chart.inner {
            self.step = result
                .steps()
                .iter()
                .enumerate()
                .filter(|(_, step)| !log_time || model::number(&step["t_s"]) > 0.)
                .min_by(|(_, a), (_, b)| {
                    (plot_time(model::number(&a["t_s"])) - time)
                        .abs()
                        .total_cmp(&(plot_time(model::number(&b["t_s"])) - time).abs())
                })
                .map(|(i, _)| i)
                .unwrap_or(0);
        }
        if self.comparison.is_some() {
            ui.label("Comparison uses each file's recorded normalization and absolute time. Check that the material, inputs, and schedules are comparable.");
        }
        if log_time {
            ui.small("Zero-time points are omitted from the logarithmic plot. Exports retain physical time.");
        }
        let step = &result.steps()[self.step];
        ui.collapsing("Which nuclides dominate activity?", |ui| {
            visuals::contributors(ui, step, &mut self.selected)
        });
        ui.horizontal_wrapped(|ui| {
            ui.label(format!("Activity  {:.4e} Bq/g", model::metric(step, 0, "")));
            ui.separator();
            ui.label(format!("Heat  {:.4e} W/g", model::metric(step, 1, "")));
            ui.separator();
            ui.label(format!(
                "Inventory  {:.4e} atoms/g",
                model::metric(step, 2, "")
            ));
        });
    }

    fn tables_page(&mut self, ui: &mut egui::Ui) {
        if !self.result_page(
            ui,
            "Tables",
            "Inventory, totals, dominant nuclides and the other report tables for the selected step.",
        ) {
            return;
        }
        let Some(result) = &self.result else {
            return;
        };
        match tables::show(ui, result, self.step, &mut self.tables) {
            TablesAction::None => {}
            TablesAction::EnableSummary => {
                if self.raw_dirty {
                    self.report(Err("Apply or discard the JSON edits first.".into()));
                } else {
                    // An empty object takes every summary default.
                    self.problem["summary"] = json!({});
                    self.report(Ok("Added `summary: {}` to the problem. The browser does not run the solver: download the problem and run it with the desktop app or the CLI, then open the new result here.".into()));
                }
            }
            TablesAction::Export {
                file_name,
                contents,
            } => {
                let saved = web::download(&file_name, contents.as_bytes());
                self.report(saved.map(|()| "Download started.".into()));
            }
        }
    }

    fn photons_page(&mut self, ui: &mut egui::Ui) {
        if !self.result_page(
            ui,
            "Photons & dose",
            "Decay photon groups and dose rates for the selected step, and dose against cooling time.",
        ) {
            return;
        }
        let Some(result) = &self.result else {
            return;
        };
        match photons::show(ui, result, self.step, &mut self.photons) {
            PhotonsAction::None => {}
            PhotonsAction::Open(target) => self.page = page_for(target),
            PhotonsAction::Export {
                file_name,
                contents,
            } => {
                let saved = web::download(&file_name, contents.as_bytes());
                self.report(saved.map(|()| "Download started.".into()));
            }
        }
    }

    fn pathways_page(&mut self, ui: &mut egui::Ui) {
        if !self.result_page(
            ui,
            "Pathways",
            "How the selected nuclides are produced: first products and full routes.",
        ) {
            return;
        }
        let Some(result) = &self.result else {
            return;
        };
        match pathways::show(ui, result, self.step, &mut self.pathways) {
            PathwaysAction::None => {}
            PathwaysAction::Open(target) => self.page = page_for(target),
        }
    }

    fn ledger_page(&mut self, ui: &mut egui::Ui) {
        if !self.result_page(
            ui,
            "Ledger & certificate",
            "Inspect missing data, approximations, and the provenance behind the answer.",
        ) {
            return;
        }
        let Some(result) = &self.result else {
            return;
        };
        ui.add(
            egui::TextEdit::singleline(&mut self.ledger_filter)
                .hint_text("Filter evidence by field or value…")
                .desired_width(360.),
        );
        if self.ledger_filter.is_empty() {
            visuals::json_tree(ui, "Ledger", &result.value["ledger"]);
            visuals::json_tree(ui, "Certificate", &result.value["certificate"]);
        } else {
            let query = self.ledger_filter.to_lowercase();
            let mut found = false;
            for section in ["ledger", "certificate"] {
                ui.heading(section);
                if let Some(entries) = result.value[section].as_object() {
                    for (key, value) in entries {
                        if key.to_lowercase().contains(&query)
                            || value.to_string().to_lowercase().contains(&query)
                        {
                            found = true;
                            visuals::json_tree(ui, key, value);
                        }
                    }
                }
            }
            if !found {
                ui.label("No matching evidence fields. Try a nuclide name, a file hash, or a category such as decay.");
            }
        }
        // The MF=33 record of the selected step; the shaded band on Charts is drawn from it.
        if let Some(step) = result.steps().get(self.step) {
            if !step["uncertainty"].is_null() {
                visuals::json_tree(ui, "Uncertainty record", &step["uncertainty"]);
            }
        }
    }

    /// The waste class page: a viewer for waste results and an editor for waste specs. The
    /// browser never classifies; the evaluator runs in the desktop app and the CLI.
    fn waste_page(&mut self, ui: &mut egui::Ui) {
        visuals::heading(
            ui,
            "Waste class",
            "Read the Part 61 classes in a waste result, and edit a waste spec to run with the desktop app or `actinv waste`. The browser does not classify.",
        );
        ui.horizontal_wrapped(|ui| {
            if ui.button("Open waste result…").clicked() {
                self.pick(FileKind::WasteResult, &["json"], ui.ctx());
            }
            if ui.button("Open waste spec…").clicked() {
                self.pick(FileKind::WasteSpec, &["json"], ui.ctx());
            }
        });
        ui.add_space(8.);
        match &self.waste_result {
            Some((name, parsed)) => {
                ui.label(RichText::new(name).strong());
                waste::show(ui, parsed);
            }
            None => {
                ui.group(|ui| {
                    ui.add_space(30.);
                    ui.heading("Your waste result will appear here");
                    ui.label("Open an actinv-waste-result-1 file, an intrusion-screen result or a twin result with waste_classification. Dropping the file on the window works too.");
                    ui.add_space(30.);
                });
            }
        }
        ui.add_space(16.);
        self.waste_spec_editor(ui);
    }

    fn waste_spec_editor(&mut self, ui: &mut egui::Ui) {
        ui.strong("Waste spec");
        ui.label("An actinv-waste-spec-1 document names the run result it reads in `input`; keep that result next to the spec when you run it.");
        ui.horizontal_wrapped(|ui| {
            if ui
                .add_enabled(
                    self.waste_spec.trim().is_empty(),
                    egui::Button::new("Start from a skeleton"),
                )
                .clicked()
            {
                self.waste_spec = waste::SPEC_SKELETON.to_owned();
            }
            if ui.button("Check spec").clicked() {
                let checked = waste::check_spec(&self.waste_spec);
                self.report(checked.map(|()| {
                    "The waste spec has the expected structure. ACTINV checks the rest when it runs the spec.".into()
                }));
            }
            if ui.button("Download spec").clicked() {
                let saved = waste::check_spec(&self.waste_spec)
                    .and_then(|()| web::download("waste-spec.json", self.waste_spec.as_bytes()));
                self.report(saved.map(|()| "Download started.".into()));
            }
        });
        ui.add(
            egui::TextEdit::multiline(&mut self.waste_spec)
                .code_editor()
                .hint_text("Open an actinv-waste-spec-1 file, or start from a skeleton.")
                .desired_rows(18)
                .desired_width(f32::INFINITY),
        );
    }

    fn advanced_page(&mut self, ui: &mut egui::Ui) {
        visuals::heading(
            ui,
            "Advanced specification",
            "Edit every ACTINV option. Apply checks the schema; Validate checks the scientific inputs.",
        );
        if !self.raw_dirty && self.raw_source != self.problem {
            self.raw = serde_json::to_string_pretty(&self.problem).unwrap_or_default();
            self.raw_source = self.problem.clone();
        }
        ui.horizontal(|ui| {
            if ui
                .add_enabled(self.raw_dirty, egui::Button::new("Apply JSON edits"))
                .clicked()
            {
                let result = model::decode_problem(&self.raw).map(|value| {
                    self.problem = value;
                    self.raw_dirty = false;
                    "JSON edits applied. Validate the problem before downloading.".into()
                });
                self.report(result);
            }
            if ui
                .add_enabled(self.raw_dirty, egui::Button::new("Discard JSON edits"))
                .clicked()
            {
                self.raw_dirty = false;
            }
            ui.label("Other editors pause while JSON edits are pending.");
        });
        if ui
            .add(
                egui::TextEdit::multiline(&mut self.raw)
                    .code_editor()
                    .desired_rows(28)
                    .desired_width(f32::INFINITY),
            )
            .changed()
        {
            self.raw_dirty = true;
        }
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
            // With the import dialog open a drop is the deck; the other two files are chosen
            // with the dialog's buttons.
            let kind = if self.import.is_some() {
                FileKind::Import(ImportSlot::Deck)
            } else {
                FileKind::Auto
            };
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
                    file.bytes_async().await.map(|bytes| (kind, name, bytes))
                };
                let _ = tx.send(result);
                ctx.request_repaint();
            });
        }
    }

    fn ui(&mut self, ui: &mut egui::Ui, _: &mut eframe::Frame) {
        let nav = egui::Panel::top("navigation").show(ui, |ui| {
            egui::containers::Sides::new().shrink_left().show(
                ui,
                |ui| {
                    ui.horizontal_wrapped(|ui| {
                        let mut group = None;
                        for page in browser_pages() {
                            if group != Some(page.group()) {
                                if group.is_some() {
                                    ui.separator();
                                }
                                if let Some(caption) = page.group().caption() {
                                    ui.label(RichText::new(caption).small().weak());
                                }
                                group = Some(page.group());
                            }
                            // pages that read a result are dimmed until one is open; the waste page
                            // reads a waste result, not a run result
                            let missing = if page == Page::Waste {
                                self.waste_result.is_none()
                            } else {
                                self.result.is_none()
                            };
                            let label = if page.needs_result() && missing {
                                RichText::new(page.label()).weak()
                            } else {
                                RichText::new(page.label())
                            };
                            ui.selectable_value(&mut self.page, page, label);
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
                    });
                },
                |ui| self.suite.controls(ui),
            );
        });
        self.suite.prompt(ui.ctx(), nav.response.rect.bottom());
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
                    Page::Overview => self.overview(ui),
                    Page::Material => self.material_page(ui),
                    Page::Schedule => self.schedule_page(ui),
                    Page::Spectrum => self.spectrum_page(ui),
                    Page::Outputs => self.outputs_page(ui),
                    Page::Charts => self.charts(ui),
                    Page::Tables => self.tables_page(ui),
                    Page::Photons => self.photons_page(ui),
                    Page::Pathways => self.pathways_page(ui),
                    Page::Ledger => self.ledger_page(ui),
                    Page::Waste => self.waste_page(ui),
                    Page::Advanced => self.advanced_page(ui),
                    // the browser never runs the solver
                    Page::Sweep | Page::Assimilation => self.overview(ui),
                }
            });
        });
        let ctx = ui.ctx().clone();
        self.import_dialog(&ctx);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_pin_replaces_the_object_at_its_path_and_creates_the_parents() {
        let mut doc = json!({"photon": {"point_source": {"distance_m": 1.0}}});
        apply_pin(&mut doc, PinField::Set("photon.response"), "r.json", "ab");
        assert_eq!(
            doc["photon"]["response"],
            json!({"path": "r.json", "sha256": "ab"})
        );
        assert_eq!(doc["photon"]["point_source"]["distance_m"], 1.0);
        let mut empty = json!({});
        apply_pin(&mut empty, PinField::Set("kerma.table"), "k.json", "cd");
        assert_eq!(
            empty["kerma"]["table"],
            json!({"path": "k.json", "sha256": "cd"})
        );
    }

    #[test]
    fn a_resonance_pin_is_appended() {
        let path = "self_shielding.geometry.resonance_files";
        let mut doc = json!({});
        apply_pin(&mut doc, PinField::Push(path), "a.endf", "01");
        apply_pin(&mut doc, PinField::Push(path), "b.endf", "02");
        let files = doc["self_shielding"]["geometry"]["resonance_files"]
            .as_array()
            .unwrap();
        assert_eq!(files.len(), 2);
        assert_eq!(files[1], json!({"path": "b.endf", "sha256": "02"}));
    }

    #[test]
    fn every_picker_title_the_views_use_names_a_field() {
        for title in [
            "Kerma table",
            "Photon response data",
            "Radiological response table",
            "Covariance sidecar",
            "Resonance evaluation (ENDF-6)",
        ] {
            assert!(pin_field_for(title).is_some(), "{title}");
        }
        assert!(pin_field_for("Something else").is_none());
    }

    #[test]
    fn the_browser_picker_records_the_request_and_answers_none() {
        let mut picker = BrowserPicker::default();
        assert!(picker.pick("Kerma table", &["json"]).is_none());
        let request = picker.request.take().unwrap();
        assert_eq!(request.title, "Kerma table");
        assert_eq!(request.extensions, ["json"]);
    }

    #[test]
    fn the_browser_offers_the_desktop_pages_less_the_solver_ones() {
        let pages: Vec<_> = browser_pages().collect();
        assert_eq!(pages.len(), Page::ALL.len() - 2);
        assert!(!pages.contains(&Page::Sweep) && !pages.contains(&Page::Assimilation));
        assert!(pages.contains(&Page::Advanced));
    }

    #[test]
    fn pasted_group_values_follow_the_desktop_rules() {
        assert_eq!(
            crate::model::parse_group_values("flux\n1\n2\n3", 3).unwrap(),
            [1., 2., 3.]
        );
        assert!(crate::model::parse_group_values("1,2", 3).is_err());
        assert!(crate::model::parse_group_values("0,0", 2).is_err());
        assert!(crate::model::parse_group_values("1,x", 2).is_err());
        assert!(crate::model::parse_group_values("1,-1", 2).is_err());
    }
}
