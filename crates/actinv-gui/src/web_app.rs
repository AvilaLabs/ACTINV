use crate::{
    model::{self, ResultDocument},
    nav::{Group, Page},
    views::{
        check::{self, CheckAction},
        import::{self, ImportAction, ImportSlot, ImportState},
        material::{self, MaterialState},
        outputs::{self, OutputsAction, OutputsState},
        pathways::{self, PathwaysAction, PathwaysState},
        photons::{self, PhotonsAction, PhotonsState},
        steps::{self, StepsAction, StepsState},
        summary::{self, SummaryAction},
        tables::{self, TablesAction, TablesState},
        waste::{self, Detected},
        waste_bounds,
        waste_forms::{self, Question},
        waste_package, waste_uncertainty, Marks, Picker, Pinned, Target,
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
/// Below this width the sidebar hides behind a Menu button so the page keeps its room.
const NARROW: f32 = 760.;

/// The studies the browser sets up, in the order the studies page lists them: page, card title,
/// what it answers.
const STUDIES: [(Page, &str, &str); 5] = [
    (Page::Waste, "Waste class", "Which class the waste falls in, which package takes it, over which ranges, and how sure."),
    (Page::Impurities, "Impurities", "Which trace elements drive activity, heat, dose or waste class, and how far each may rise."),
    (Page::Compare, "Compare", "How materials, spectra or irradiation lengths compare, every case solved the same way."),
    (Page::Certify, "Certify", "Whether the design meets its limits once the nuclear-data uncertainty is counted."),
    (Page::Optimize, "Optimize", "Which design, within the ranges you give, does best while meeting every limit."),
];

/// What a toolbar click asks for, applied after the bar is drawn.
enum ToolbarAction {
    Open,
    Import,
    Download,
    OpenResult,
}

/// Cards of the same width, `most` to a row when they fit at 200 and two otherwise, each with a
/// title, up to three lines of text and one button. Returns the index of the card clicked.
fn card_grid(ui: &mut egui::Ui, cards: &[(&str, &str, &str)], most: usize) -> Option<usize> {
    // each card adds its frame's margins and stroke (14) and the gap to the next (8), and egui
    // wraps single widgets, not groups
    let available = ui.available_width();
    let per_row = if available >= most as f32 * (200. + 14.) + (most - 1) as f32 * 8. {
        most
    } else {
        2
    };
    let width = ((available - (per_row as f32) * 14. - (per_row - 1) as f32 * 8.) / per_row as f32)
        .clamp(160., 300.);
    let mut picked = None;
    for (row, chunk) in cards.chunks(per_row).enumerate() {
        ui.horizontal(|ui| {
            for (i, (title, text, button)) in chunk.iter().enumerate() {
                let clicked = ui
                    .group(|ui| {
                        ui.set_width(width);
                        ui.set_min_height(140.);
                        ui.vertical(|ui| {
                            ui.strong(*title);
                            // three lines of room for every description, so the buttons line up
                            let lines = 3.2 * ui.text_style_height(&egui::TextStyle::Body);
                            ui.scope(|ui| {
                                ui.set_min_height(lines);
                                ui.label(*text);
                            });
                            ui.add_space(4.);
                            ui.button(*button).clicked()
                        })
                        .inner
                    })
                    .inner;
                if clicked {
                    picked = Some(row * per_row + i);
                }
            }
        });
    }
    picked
}

/// The four file actions of the toolbar, shortened on a mid-width window.
fn file_buttons(ui: &mut egui::Ui, compact: bool, unsaved: bool) -> Option<ToolbarAction> {
    let mut action = None;
    if ui
        .button(if compact { "Open" } else { "Open problem…" })
        .on_hover_text("Open an ACTINV problem from your computer")
        .clicked()
    {
        action = Some(ToolbarAction::Open);
    }
    if ui
        .button(if compact {
            "Import deck…"
        } else {
            "Import FISPACT-II deck…"
        })
        .on_hover_text("Convert a FISPACT-II input deck into an ACTINV problem")
        .clicked()
    {
        action = Some(ToolbarAction::Import);
    }
    let download = if compact {
        "Download"
    } else {
        "Download problem"
    };
    if ui
        .button(if unsaved {
            format!("{download} •")
        } else {
            download.into()
        })
        .on_hover_text(if unsaved {
            "The problem has changes you have not downloaded"
        } else {
            "Save the problem to your computer"
        })
        .clicked()
    {
        action = Some(ToolbarAction::Download);
    }
    ui.separator();
    if ui
        .button(if compact {
            "Open result"
        } else {
            "Open result…"
        })
        .on_hover_text("Open a result saved from ACTINV to explore it")
        .clicked()
    {
        action = Some(ToolbarAction::OpenResult);
    }
    action
}

/// A folding sidebar caption with a painted triangle: the UI font has no arrow glyphs.
fn fold_header(ui: &mut egui::Ui, caption: &str, open: bool, accent: Color32) -> egui::Response {
    ui.horizontal(|ui| {
        ui.spacing_mut().item_spacing.x = 4.;
        let (rect, mark) = ui.allocate_exact_size(egui::vec2(8., 8.), egui::Sense::click());
        let (c, r) = (rect.center(), 3.5);
        let points = if open {
            vec![
                c + egui::vec2(-r, -r * 0.6),
                c + egui::vec2(r, -r * 0.6),
                c + egui::vec2(0., r * 0.8),
            ]
        } else {
            vec![
                c + egui::vec2(-r * 0.6, -r),
                c + egui::vec2(r * 0.8, 0.),
                c + egui::vec2(-r * 0.6, r),
            ]
        };
        ui.painter().add(egui::Shape::convex_polygon(
            points,
            accent,
            egui::Stroke::NONE,
        ));
        let label = ui.add(
            egui::Label::new(RichText::new(caption).small().color(accent))
                .sense(egui::Sense::click()),
        );
        mark | label
    })
    .inner
    .on_hover_cursor(egui::CursorIcon::PointingHand)
}
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
    /// An `actinv-impurities-result-1` for the Impurities page.
    ScreenResult,
    /// A container, ranges or how-sure result for the Waste class page.
    WasteAnswer,
}

/// A result computed ahead of time that the Start page can open.
struct WebExample {
    file: &'static str,
    kind: FileKind,
    /// The name the result pages show for it.
    label: &'static str,
    button: &'static str,
    text: &'static str,
}

const EXAMPLES: [WebExample; 2] = [
    WebExample {
        file: "iron-result.json",
        kind: FileKind::Result,
        label: "Example result: the iron example",
        button: "Iron example's result",
        text: "1 g of iron after 5 minutes at the FNS 14 MeV source, with an hour of cooling: the iron example, as ACTINV solved it.",
    },
    WebExample {
        file: "impurities-result.json",
        kind: FileKind::ScreenResult,
        label: "Example screen: impurities in a reduced-activation steel",
        button: "Impurity screen of a steel",
        text: "Which trace elements of a fusion steel drive its decay heat and its Nb-94 after two years at the first wall, and how far each may rise.",
    },
];

/// Fetch a file served beside the app.
async fn fetch_bytes(url: &str) -> Result<Vec<u8>, String> {
    use wasm_bindgen::JsCast;
    use wasm_bindgen_futures::JsFuture;
    let failed = |_: wasm_bindgen::JsValue| {
        format!("Could not fetch {url}; check the connection and try again.")
    };
    let window = web_sys::window().ok_or("No browser window")?;
    let response: web_sys::Response = JsFuture::from(window.fetch_with_str(url))
        .await
        .map_err(failed)?
        .dyn_into()
        .map_err(failed)?;
    if !response.ok() {
        return Err(format!(
            "Could not fetch {url}: HTTP {}.",
            response.status()
        ));
    }
    let buffer = JsFuture::from(response.array_buffer().map_err(failed)?)
        .await
        .map_err(failed)?;
    Ok(js_sys::Uint8Array::new(&buffer).to_vec())
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
        Target::Material => Page::Material,
        Target::Schedule => Page::Schedule,
        Target::Spectrum => Page::Spectrum,
        Target::Outputs => Page::Outputs,
        Target::CheckRun => Page::CheckRun,
        Target::Data => Page::Data,
        Target::Advanced => Page::Advanced,
    }
}

/// The pages the browser offers: the desktop's, less the two that run the solver.
/// Whether the top bar ticks a setup step (see `views::check`); Check & run is ticked when every
/// other step is.
fn step_ready(problem: &Value, page: Page) -> bool {
    match page {
        Page::Material => check::ready(problem, Target::Material),
        Page::Schedule => check::ready(problem, Target::Schedule),
        Page::Spectrum => check::ready(problem, Target::Spectrum),
        Page::Outputs => check::ready(problem, Target::Outputs),
        Page::CheckRun => check::assess(problem).iter().all(|line| line.ready),
        _ => false,
    }
}

fn browser_pages() -> impl Iterator<Item = Page> {
    Page::ALL
        .into_iter()
        .filter(|page| !matches!(page, Page::Sweep | Page::Mesh | Page::Measurements))
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
    certify: crate::views::certify::CertifyState,
    optimize: crate::views::optimize::OptimizeState,
    compare: crate::views::compare::CompareState,
    /// folding groups (Tools) the visitor opened
    nav_open: Vec<crate::nav::Group>,
    /// The studies page of cards is shown over `hub_from`; any other page change closes it.
    hub: bool,
    hub_from: Page,
    /// On a narrow window the sidebar hides behind the Menu button.
    menu_open: bool,
    source_view: crate::views::source::SourceState,
    steps: StepsState,
    outputs: OutputsState,
    tables: TablesState,
    photons: PhotonsState,
    pathways: PathwaysState,
    /// The waste result on the Waste class page: its file name and what it parsed to.
    waste_result: Option<(String, waste::WasteView, Value)>,
    /// The waste question open, the forms of the other three, and the results opened for them.
    waste_question: Question,
    waste_properties: waste_forms::Properties,
    waste_package: waste_package::PackageState,
    waste_bounds: waste_bounds::BoundsState,
    waste_sure: waste_uncertainty::SureState,
    waste_answers: Vec<(Question, String, Value)>,
    export: crate::views::export::ExportState,
    /// The waste spec in the editor on the Waste class page.
    waste_spec: String,
    impurities: crate::views::impurities::ImpuritiesState,
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
        let problem = model::example();
        Self {
            page: Page::Start,
            saved: problem.clone(),
            raw: String::new(),
            problem,
            raw_dirty: false,
            raw_source: Value::Null,
            result: None,
            comparison: None,
            pending_file: None,
            status: "Start from the iron example, open a problem or a result, or drop a file anywhere in this window.".into(),
            error: false,
            step: 0,
            metric: 0,
            selected: String::new(),
            log_time: false,
            ledger_filter: String::new(),
            spectrum_text: String::new(),
            material_view: MaterialState::default(),
            certify: Default::default(),
            optimize: Default::default(),
            compare: Default::default(),
            nav_open: Vec::new(),
            hub: false,
            hub_from: Page::Start,
            menu_open: false,
            source_view: Default::default(),
            steps: StepsState::default(),
            outputs: OutputsState::default(),
            tables: TablesState::default(),
            photons: PhotonsState::default(),
            pathways: PathwaysState::default(),
            waste_result: None,
            waste_question: Question::Class,
            waste_properties: waste_forms::Properties::default(),
            waste_package: waste_package::PackageState::default(),
            waste_bounds: waste_bounds::BoundsState::default(),
            waste_sure: waste_uncertainty::SureState::default(),
            waste_answers: Vec::new(),
            export: Default::default(),
            waste_spec: String::new(),
            impurities: Default::default(),
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

    /// Open a result computed ahead of time (`scripts/web_examples.py`) and served beside the app
    /// at `examples/`, as if the visitor had opened the file.
    fn open_example(&mut self, example: &WebExample, ctx: &egui::Context) {
        if self.pending_file.is_some() {
            return;
        }
        let (tx, rx) = mpsc::channel();
        self.pending_file = Some(rx);
        self.report(Ok(format!("Fetching {}…", example.label)));
        let ctx = ctx.clone();
        let (file, kind, label) = (example.file, example.kind, example.label);
        wasm_bindgen_futures::spawn_local(async move {
            let read = fetch_bytes(&format!("./examples/{file}"))
                .await
                .map(|bytes| (kind, label.to_owned(), bytes));
            let _ = tx.send(read);
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
            | FileKind::WasteSpec
            | FileKind::ScreenResult
            | FileKind::WasteAnswer => {}
        }
        let text = std::str::from_utf8(bytes).map_err(|_| "Choose an ACTINV JSON text file")?;
        let kind = if matches!(kind, FileKind::Auto) {
            let value: Value =
                serde_json::from_str(text).map_err(|error| format!("Invalid JSON: {error}"))?;
            match waste::detect(&value) {
                _ if value["schema"] == crate::views::impurities::RESULT_SCHEMA => {
                    FileKind::ScreenResult
                }
                _ if waste_answer_question(&value).is_some() => FileKind::WasteAnswer,
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
                self.page = Page::CheckRun;
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
                    self.step = summary::shutdown_step(&result).unwrap_or(0);
                    self.result = Some(result);
                    self.comparison = None;
                    self.selected.clear();
                    self.tables = TablesState::default();
                    self.photons = PhotonsState::default();
                    self.pathways = PathwaysState::default();
                }
                self.page = Page::Summary;
                Ok(format!("Opened {name}."))
            }
            FileKind::WasteResult => {
                let value: Value =
                    serde_json::from_str(text).map_err(|error| format!("Invalid JSON: {error}"))?;
                self.waste_result = Some((name.clone(), waste::read_result(text)?, value));
                self.waste_question = Question::Class;
                self.page = Page::Waste;
                Ok(format!("Opened {name}."))
            }
            FileKind::WasteAnswer => {
                let value: Value =
                    serde_json::from_str(text).map_err(|error| format!("Invalid JSON: {error}"))?;
                let question = waste_answer_question(&value)
                    .ok_or("Choose a container, ranges or how-sure waste result")?;
                self.waste_answers.retain(|(q, ..)| *q != question);
                self.waste_answers.push((question, name.clone(), value));
                self.waste_question = question;
                self.page = Page::Waste;
                Ok(format!("Opened {name}."))
            }
            FileKind::ScreenResult => {
                let value: Value =
                    serde_json::from_str(text).map_err(|error| format!("Invalid JSON: {error}"))?;
                crate::views::impurities::check_result(&value)?;
                self.impurities.result = Some((name.clone(), value));
                self.impurities.showing_result = true;
                self.page = Page::Impurities;
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
        self.page = Page::CheckRun;
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

    fn load_example(&mut self) {
        if self.replace_problem_allowed() {
            self.problem = model::example();
            self.saved = self.problem.clone();
            self.raw_dirty = false;
            self.raw.clear();
            self.raw_source = Value::Null;
            self.page = Page::CheckRun;
            self.report(Ok("Iron example loaded: 1 g of iron, 5 minutes at the FNS 14 MeV source, then cooling.".into()));
        }
    }

    fn new_problem(&mut self) {
        if self.replace_problem_allowed() {
            self.problem = model::blank();
            // nothing to download yet: the first download is the first save
            self.saved = Value::Null;
            self.raw_dirty = false;
            self.raw.clear();
            self.raw_source = Value::Null;
            self.page = Page::Material;
            self.report(Ok("New problem. Fill in the material, the irradiation and the source; each step is ticked when it is complete.".into()));
        }
    }

    fn tutorial(&mut self) {
        self.result = Some(model::tutorial_result());
        self.comparison = None;
        self.step = 0;
        self.selected.clear();
        self.page = Page::Summary;
        self.report(Ok(
            "Tutorial: a fictional nuclide with a one-hour half-life. These are teaching values."
                .into(),
        ));
    }

    /// The home page: ways to begin, what is open, and where calculations run.
    fn start(&mut self, ui: &mut egui::Ui) {
        let accent = visuals::accent(ui);
        visuals::heading(
            ui,
            "Welcome to ACTINV",
            "What a material becomes under irradiation, and how it cools: activity, decay heat, dose and waste class.",
        );
        ui.group(|ui| {
            ui.set_width(ui.available_width());
            ui.strong("This browser workbench prepares problems and explores results");
            ui.label("Calculations run in the desktop app or with `actinv run` on your computer. Set up a problem here, download it, run it there, then open the result here or in the app.");
            ui.horizontal_wrapped(|ui| {
                ui.hyperlink_to("Download ACTINV for your computer", "./download/");
                ui.label("·");
                ui.hyperlink_to("Command-line guide", "./docs/cli.html");
            });
            ui.add_space(6.);
            ui.label("See what it computes without installing anything: these results were solved ahead of time.");
            for example in &EXAMPLES {
                ui.horizontal_wrapped(|ui| {
                    if ui.button(example.button).clicked() {
                        self.open_example(example, ui.ctx());
                    }
                    ui.weak(example.text);
                });
            }
        });
        ui.add_space(12.);
        ui.label(RichText::new("START A PROBLEM").small().color(accent));
        let picked = card_grid(
            ui,
            &[
                ("New problem", "Start from nothing: pick a material, an irradiation and a source. Nothing is filled in for you.", "New problem"),
                ("From the iron example", "1 g of iron, 5 minutes at the FNS 14 MeV neutron source, then an hour of cooling.", "Load iron example"),
                ("Open a problem", "An ACTINV problem JSON from your computer, or one written by `actinv new`.", "Open problem…"),
                ("Import a FISPACT-II deck", "Convert an input deck. A report shows what was mapped and what you still need to add.", "Import deck…"),
            ],
            4,
        );
        match picked {
            Some(1) => self.load_example(),
            Some(0) => self.new_problem(),
            Some(2) => self.pick(FileKind::Problem, &["json"], ui.ctx()),
            Some(3) => {
                self.import.get_or_insert_with(ImportState::default);
            }
            _ => {}
        }
        ui.add_space(16.);
        if let Some(page) =
            crate::views::questions::show(ui, |page| browser_pages().any(|p| p == page))
        {
            self.page = page;
        }
        ui.add_space(16.);
        ui.columns(2, |columns| {
            let ui = &mut columns[0];
            ui.label(RichText::new("CURRENT PROBLEM").small().color(accent));
            ui.group(|ui| {
                ui.set_width(ui.available_width());
                ui.strong(self.problem["title"].as_str().unwrap_or("Untitled problem"));
                let lines = check::assess(&self.problem);
                for line in &lines {
                    let text = RichText::new(&line.text);
                    let text = if line.ready {
                        text
                    } else {
                        text.color(ui.visuals().error_fg_color)
                    };
                    visuals::unjustified(ui, text);
                }
                ui.horizontal_wrapped(|ui| {
                    let (label, page) = match lines.iter().find(|line| !line.ready) {
                        Some(line) => ("Fix what is missing >", page_for(line.target)),
                        None => ("Check & download >", Page::CheckRun),
                    };
                    if ui.button(label).clicked() {
                        self.page = page;
                    }
                    if ui.button("Edit from step 1").clicked() {
                        self.page = Page::Material;
                    }
                });
            });
            let ui = &mut columns[1];
            ui.label(RichText::new("RESULTS").small().color(accent));
            ui.group(|ui| {
                ui.set_width(ui.available_width());
                match &self.result {
                    Some(result) => {
                        ui.strong(&result.label);
                        if let Some(title) = result.value["spec_title"].as_str() {
                            ui.label(title);
                        }
                        ui.horizontal(|ui| {
                            if ui.button("View summary >").clicked() {
                                self.page = Page::Summary;
                            }
                        });
                    }
                    None => {
                        visuals::unjustified(
                            ui,
                            "Open a result saved from ACTINV to explore it here.",
                        );
                        ui.horizontal_wrapped(|ui| {
                            if ui.button("Open result…").clicked() {
                                self.pick(FileKind::Result, &["json"], ui.ctx());
                            }
                            if ui
                                .button("Try the results tutorial")
                                .on_hover_text("A fictional nuclide with teaching values")
                                .clicked()
                            {
                                self.tutorial();
                            }
                        });
                    }
                }
            });
        });
        ui.add_space(16.);
        ui.group(|ui| {
            ui.set_width(ui.available_width());
            ui.strong("Your files stay on your device");
            ui.label("Drop a problem, result, waste spec or FISPACT-II deck anywhere in this window; files are read locally in the browser and never uploaded. Download any edits before closing the tab; the browser asks first while edits are unsaved.");
        });
    }

    /// The last setup step. The browser cannot run the solver, so this is where the problem is
    /// handed to the desktop app or the command line.
    fn check_run(&mut self, ui: &mut egui::Ui) {
        visuals::heading(
            ui,
            "Check & run",
            "Read the problem back, fix anything marked, then download it and run it on your computer.",
        );
        ui.label("Calculation title");
        ui.add_enabled_ui(!self.raw_dirty, |ui| {
            visuals::wide_text_field(ui, &mut self.problem["title"]);
        });
        ui.add_space(12.);
        let action = egui::Frame::group(ui.style())
            .inner_margin(16.)
            .show(ui, |ui| {
                ui.heading("The problem");
                check::show(ui, &self.problem)
            })
            .inner;
        if let CheckAction::Open(target) = action {
            self.page = page_for(target);
        }
        ui.add_space(12.);
        let review = egui::Frame::group(ui.style())
            .inner_margin(16.)
            .show(ui, |ui| crate::views::review::show(ui, &self.problem))
            .inner;
        if let Some(target) = review {
            self.page = page_for(target);
        }
        ui.add_space(16.);
        ui.group(|ui| {
            ui.set_width(ui.available_width());
            ui.strong("Run it on your computer");
            ui.label("1. Download the problem.");
            ui.horizontal_wrapped(|ui| {
                if ui
                    .add(egui::Button::new(RichText::new("Download problem").color(Color32::WHITE)).fill(BLUE))
                    .clicked()
                {
                    self.export_problem();
                }
                if ui.button("Validate only").clicked() {
                    let result = if self.raw_dirty {
                        Err("Apply the JSON edits first.".into())
                    } else {
                        Spec::from_json(&self.problem.to_string()).map(|_| "Problem specification is valid. Nuclear-data files are checked when it runs.".into())
                    };
                    self.report(result);
                }
            });
            ui.label("2. Run it with the desktop app (Open problem, then Run), or in a terminal:");
            ui.code("actinv data fetch\nactinv run problem.json result.json");
            ui.label("3. Open result.json here, or drop it into this window, to explore it.");
            ui.horizontal_wrapped(|ui| {
                ui.hyperlink_to("Download ACTINV", "./download/");
                if ui.button("Open result…").clicked() {
                    self.pick(FileKind::Result, &["json"], ui.ctx());
                }
            });
        });
    }

    /// Data: the paths the problem records. The browser cannot read the files; the program that
    /// runs the problem does.
    fn data_page(&mut self, ui: &mut egui::Ui) {
        visuals::heading(
            ui,
            "Data",
            "The nuclear data this problem uses: the standard data ACTINV installs, or files of your own.",
        );
        self.editors(ui, |this, ui| {
            egui::Frame::group(ui.style())
                .inner_margin(16.)
                .show(ui, |ui| {
                    ui.set_width(ui.available_width());
                    ui.heading("This problem's data");
                    ui.label("The program that runs the problem reads these files; the browser records the paths but does not read them. `catalog:` names point into the standard data that `actinv data fetch` installs.");
                    ui.horizontal(|ui| {
                        ui.label("Activation library (.npz)");
                        visuals::text_field(ui, &mut this.problem["library"]["path"]);
                    });
                    ui.horizontal(|ui| {
                        ui.label("Primary decay data");
                        visuals::text_field(ui, &mut this.problem["decay"]["primary"]);
                    });
                });
        });
        ui.add_space(12.);
        egui::Frame::group(ui.style()).inner_margin(16.).show(ui, |ui| {
            ui.set_width(ui.available_width());
            ui.heading("Build your own data");
            ui.label("Libraries are built from evaluated nuclear data files with the command line (`actinv build-library`, `build-damage`, `build-shielding`, `build-covariance`). Point the problem at what you built above.");
            ui.hyperlink_to("Download ACTINV", "./download/");
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
        });
    }

    fn projectile(&mut self, ui: &mut egui::Ui) {
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
            ui.label("Charged-particle and photonuclear problems need a library for that projectile (named on Check & run) and a temperature of 0 K. The standard spectra are neutron shapes, so import this projectile's group values from transport below.");
        }
        ui.add_space(8.);
    }

    fn spectrum_page(&mut self, ui: &mut egui::Ui) {
        visuals::heading(
            ui,
            "Source & spectrum",
            "The particle that irradiates the material and its energy spectrum. Normalization is always explicit.",
        );
        self.editors(ui, |this, ui| {
            this.projectile(ui);
            let source = crate::views::source::applies(&this.problem);
            let started_empty = !crate::views::source::has_flux(&this.problem);
            if source && started_empty {
                crate::views::source::starting_card(ui, &mut this.problem, &mut this.source_view);
            }
            let accent = visuals::accent(ui);
            ui.horizontal(|ui| {
                ui.label("Group structure");
                visuals::text_field(ui, &mut this.problem["spectrum"]["structure"]);
            });
            let mut normalized = this.problem["spectrum"]["total"].is_number();
            if ui
                .checkbox(
                    &mut normalized,
                    "Normalize group values to a total flux (particles/cm²/s)",
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
                ui.label("Group values are absolute group fluxes (particles/cm²/s).");
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
            if !started_empty {
                Plot::new("incident")
                    .height(260.)
                    .allow_scroll(false)
                    .x_axis_label("Input group index")
                    .y_axis_label(if normalized {
                        "Relative group weight"
                    } else {
                        "Group flux (particles/cm²/s)"
                    })
                    .y_axis_formatter(|mark, _| crate::visuals::compact_number(mark.value))
                    .show(ui, |plot| {
                        plot.line(Line::new("Incident spectrum", points).color(accent))
                    });
            }
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
            if !started_empty {
                ui.collapsing("Spectrum by physical energy", |ui| {
                    visuals::spectrum(ui, &this.problem)
                });
            }
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
            if source && !started_empty {
                ui.add_space(8.);
                crate::views::source::starting_card(ui, &mut this.problem, &mut this.source_view);
            }
        });
    }

    fn outputs_page(&mut self, ui: &mut egui::Ui) {
        visuals::heading(
            ui,
            "What to compute",
            "Activity, decay heat and inventory are always computed. Add what else this problem should answer.",
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
                // The browser leaves `spread.can_run` off, so the card never asks for this.
                OutputsAction::EstimateSpread => {}
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
                .add_enabled(self.result.is_some(), egui::Button::new("Compare with…"))
                .clicked()
            {
                self.pick(FileKind::Compare, &["json"], ui.ctx());
            }
            if self.comparison.is_some() && ui.button("Remove comparison").clicked() {
                self.comparison = None;
            }
            if let Some(result) = &self.result {
                if ui
                    .button("Download result")
                    .on_hover_text(
                        "The complete result JSON: every step, the ledger and the certificate.",
                    )
                    .clicked()
                {
                    exported = Some(
                        serde_json::to_vec_pretty(&result.value)
                            .map_err(|error| error.to_string())
                            .and_then(|bytes| web::download("result.json", &bytes)),
                    );
                }
                if self.page != Page::Export
                    && ui
                        .button("Export…")
                        .on_hover_text("A report, charts and tables in one zip")
                        .clicked()
                {
                    self.page = Page::Export;
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
        // one line names the result: its title, then the file it came from
        ui.horizontal_wrapped(|ui| match result.value["spec_title"].as_str() {
            Some(title) => {
                ui.label(RichText::new(title).strong());
                ui.label(RichText::new(format!("· {}", result.label)).weak());
            }
            None => {
                ui.label(RichText::new(&result.label).strong());
            }
        });
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
            // the time in words; the exact time on hover
            ui.label(format!(
                "of {steps} · {}",
                crate::views::summary::when(result, self.step)
            ))
            .on_hover_text(format!(
                "t = {:.6e} s from the start of the schedule",
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
                ui.label("Open a result saved from ACTINV to explore activity, decay heat, dose and the nuclide inventory.");
                ui.horizontal_wrapped(|ui| {
                    if ui.button("Open result…").clicked() {
                        self.pick(FileKind::Result, &["json"], ui.ctx());
                    }
                    if ui.button("Try the results tutorial").clicked() {
                        self.tutorial();
                    }
                    if ui.button("Set up a problem >").clicked() {
                        self.page = Page::CheckRun;
                    }
                });
                ui.add_space(30.);
            });
            return false;
        }
        true
    }

    fn summary_page(&mut self, ui: &mut egui::Ui) {
        if !self.result_page(
            ui,
            "Summary",
            "The main answers at the selected time, how they change as the material cools, and what dominates them.",
        ) {
            return;
        }
        let Some(result) = &self.result else {
            return;
        };
        match summary::show(ui, result, self.comparison.as_ref(), self.step) {
            SummaryAction::None => {}
            SummaryAction::Step(step) => self.step = step,
            SummaryAction::Open(page) => self.page = page,
            SummaryAction::Follow(nuclide) => {
                self.selected = nuclide;
                self.metric = 0;
                self.page = Page::Charts;
            }
        }
    }

    /// Back and Continue between the numbered setup steps.
    fn step_footer(&mut self, ui: &mut egui::Ui) {
        let Some(number) = self.page.step() else {
            return;
        };
        ui.add_space(20.);
        ui.separator();
        ui.horizontal_wrapped(|ui| {
            if number > 1 && ui.button("< Back").clicked() {
                self.page = Page::STEPS[number - 2];
            }
            if let Some(next) = self.page.next_step() {
                if ui
                    .button(format!("Continue to {} >", next.label().to_lowercase()))
                    .clicked()
                {
                    self.page = next;
                }
            }
            ui.label(RichText::new(format!("Step {number} of {}", Page::STEPS.len())).weak());
        });
    }

    fn charts(&mut self, ui: &mut egui::Ui) {
        if !self.result_page(
            ui,
            "Time history",
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
            "Nuclides",
            "The inventory, totals, dominant nuclides and the other report tables for the selected step.",
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

    fn export_page(&mut self, ui: &mut egui::Ui) {
        if !self.result_page(
            ui,
            "Export",
            "Save what this result answers: a report to read and share, charts and tables for your own documents, a photon source for transport, and the files to rerun or reuse it.",
        ) {
            return;
        }
        let Some(result) = &self.result else {
            return;
        };
        let context = crate::views::export::Context {
            problem: Some(&self.problem),
            waste: None,
            step: self.step,
            save_label: "Download .zip",
        };
        if let crate::views::export::ExportAction::Save { name, files } =
            crate::views::export::show(ui, result, &mut self.export, &context)
        {
            let zip = crate::views::export::zip(&files);
            let saved = web::download(&format!("{name}.zip"), &zip).map(|()| {
                format!(
                    "Downloading {name}.zip with {} files. Open report.html from it to read or print the report.",
                    files.len()
                )
            });
            self.export.message = Some(saved.clone());
            self.report(saved);
        }
    }

    fn ledger_page(&mut self, ui: &mut egui::Ui) {
        if !self.result_page(
            ui,
            "Evidence",
            "The ledger of missing data and approximations, and the certificate of inputs and data behind the answer.",
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
    fn certify_page(&mut self, ui: &mut egui::Ui) {
        use crate::views::certify::{self, CertifyAction};
        visuals::heading(
            ui,
            "Certify",
            "Does the design meet its limits once the nuclear-data uncertainty is counted, and which measurement would narrow the answer most?",
        );
        match certify::show(ui, &self.problem, &mut self.certify, false, false) {
            CertifyAction::None => {}
            CertifyAction::Open(target) => self.page = page_for(target),
            CertifyAction::Certify => {
                let times = certify::step_times(&self.problem);
                let saved = certify::decide_spec(self.problem.clone(), &self.certify.limits, &times, self.certify.top)
                    .and_then(|decision| serde_json::to_string_pretty(&decision).map_err(|e| e.to_string()))
                    .and_then(|text| web::download("decision.json", text.as_bytes()))
                    .map(|()| "Download started. Run `actinv decide decision.json verdict.json` where the nuclear data and the covariance file are installed.".to_owned());
                self.report(saved);
            }
        }
    }

    /// Optimize: the browser sets up the search and downloads it with the problem beside it.
    fn optimize_page(&mut self, ui: &mut egui::Ui) {
        use crate::views::optimize::{self, OptimizeAction};
        visuals::heading(ui, "Optimize", optimize::QUESTION);
        match optimize::show(ui, &self.problem, &mut self.optimize, false, false) {
            OptimizeAction::None | OptimizeAction::UseDesign(..) => {}
            OptimizeAction::Open(target) => self.page = page_for(target),
            OptimizeAction::Search => {
                let times = crate::views::certify::step_times(&self.problem);
                let saved = optimize::optimize_spec(self.problem.clone(), &self.problem, &self.optimize, &times)
                    .and_then(|(search, args)| {
                        let pretty = |v: &serde_json::Value| serde_json::to_string_pretty(v).map_err(|e| e.to_string());
                        web::download("search.json", pretty(&search)?.as_bytes())?;
                        web::download("base_spec.json", pretty(&args["base_spec"])?.as_bytes())
                    })
                    .map(|()| "Two downloads started: search.json and base_spec.json. Keep them in one folder and run `actinv optimize search.json out` where the nuclear data are installed.".to_owned());
                self.report(saved);
            }
        }
    }

    /// Compare: the browser sets up the study and downloads it.
    fn compare_page(&mut self, ui: &mut egui::Ui) {
        use crate::views::compare::{self, CompareAction};
        visuals::heading(ui, "Compare", compare::QUESTION);
        match compare::show(ui, &self.problem, &mut self.compare, false, false) {
            CompareAction::None => {}
            CompareAction::Open(target) => self.page = page_for(target),
            CompareAction::Compare => {
                let saved = compare::study_spec(&self.problem, &self.problem, &self.compare)
                    .and_then(|study| serde_json::to_string_pretty(&study).map_err(|e| e.to_string()))
                    .and_then(|text| web::download("compare.json", text.as_bytes()))
                    .map(|()| "Download started. Run `actinv study run compare.json out` where the nuclear data are installed.".to_owned());
                self.report(saved);
            }
        }
    }

    fn impurities_page(&mut self, ui: &mut egui::Ui) {
        use crate::views::impurities::{self, Context, ImpuritiesAction};
        visuals::heading(
            ui,
            "Impurities",
            "Which trace elements drive activity, heat, dose or waste class, the worst case over their specified ranges, and how far each may rise before a limit is reached.",
        );
        let context = Context {
            can_screen: false,
            running: false,
        };
        match impurities::show(ui, &self.problem, &mut self.impurities, &context) {
            ImpuritiesAction::None | ImpuritiesAction::Screen => {}
            ImpuritiesAction::SaveScreen => {
                let saved = impurities::screen_document(
                    &self.impurities,
                    &self.problem,
                    self.problem.clone(),
                )
                .and_then(|screen| {
                    serde_json::to_string_pretty(&screen).map_err(|e| e.to_string())
                })
                .and_then(|text| web::download("impurities.json", text.as_bytes()))
                .map(|()| {
                    "Download started. Keep impurities.json beside the nuclear data the problem names, and run `actinv impurities impurities.json result.json`.".to_owned()
                });
                self.impurities.message = Some(saved.clone());
                self.report(saved);
            }
            ImpuritiesAction::OpenResult => {
                self.pick(FileKind::ScreenResult, &["json"], ui.ctx());
            }
            ImpuritiesAction::SaveResult => {
                if let Some((name, value)) = &self.impurities.result {
                    let saved = serde_json::to_string_pretty(value)
                        .map_err(|e| e.to_string())
                        .and_then(|text| web::download(name, text.as_bytes()))
                        .map(|()| "Download started.".to_owned());
                    self.report(saved);
                }
            }
            ImpuritiesAction::GoTo(target) => self.page = page_for(target),
        }
    }

    fn waste_page(&mut self, ui: &mut egui::Ui) {
        visuals::heading(
            ui,
            "Waste class",
            "Which waste question do you have? The browser sets each one up and reads its result; the desktop app and the `actinv waste` commands compute the answer.",
        );
        waste_forms::question_tiles(ui, &mut self.waste_question);
        match self.waste_question {
            Question::Class => self.waste_class(ui),
            Question::Container => self.waste_container(ui),
            Question::Ranges => self.waste_ranges(ui),
            Question::HowSure => self.waste_sure(ui),
        }
        let question = self.waste_question;
        if question != Question::Class {
            if let Some((_, name, value)) = self.waste_answers.iter().find(|(q, ..)| *q == question)
            {
                ui.add_space(12.);
                ui.separator();
                waste_forms::section(ui, "Answer", name);
                match question {
                    Question::Container => waste_package::show_result(ui, value),
                    Question::Ranges => waste_bounds::show_result(ui, value),
                    _ => waste_uncertainty::show_result(ui, value),
                }
            }
        }
    }

    fn waste_class(&mut self, ui: &mut egui::Ui) {
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
            Some((name, parsed, value)) => {
                ui.label(RichText::new(name).strong());
                waste::show(ui, parsed);
                waste::show_intrusion(ui, value);
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

    /// The download row under a browser waste form, and the button that opens its result.
    fn waste_download_row(&mut self, ui: &mut egui::Ui, label: &str) -> bool {
        let mut download = false;
        ui.add_space(8.);
        ui.horizontal_wrapped(|ui| {
            if waste_forms::primary(ui, true, label).clicked() {
                download = true;
            }
            if ui.button("Open its result…").clicked() {
                self.pick(FileKind::WasteAnswer, &["json"], ui.ctx());
            }
        });
        download
    }

    fn waste_container(&mut self, ui: &mut egui::Ui) {
        let steps: Vec<(u64, String)> = self
            .result
            .as_ref()
            .map(|r| {
                r.steps()
                    .iter()
                    .enumerate()
                    .map(|(i, s)| {
                        (
                            s["step"].as_u64().unwrap_or(i as u64 + 1),
                            model::readable_time(model::number(&s["t_s"])),
                        )
                    })
                    .collect()
            })
            .unwrap_or_default();
        waste_package::show_form(ui, &mut self.waste_package, &steps, false);
        let declared: Vec<(u64, String)> = (1..=self.waste_package.declared_steps)
            .map(|n| (n, String::new()))
            .collect();
        let targets = self
            .waste_package
            .targets(if steps.is_empty() { &declared } else { &steps });
        let mut active = self.waste_package.declared_nuclides(&targets);
        if let Some(result) = self
            .result
            .as_ref()
            .filter(|_| self.waste_package.uses_this_result())
        {
            for step in result
                .steps()
                .iter()
                .enumerate()
                .filter(|(i, s)| targets.contains(&s["step"].as_u64().unwrap_or(*i as u64 + 1)))
            {
                for (name, a) in step.1["activity_Bq_per_g"]
                    .as_object()
                    .into_iter()
                    .flatten()
                {
                    if a.as_f64().is_some_and(|a| a > 0.) {
                        active.insert(name.clone());
                    }
                }
            }
        }
        waste_forms::section(ui, "Nuclide properties", "Z, half-life and alpha emission of every nuclide with activity. The browser has no decay data, so enter them here.");
        waste_forms::properties_card(
            ui,
            "package",
            &mut self.waste_properties,
            &active,
            &Value::Null,
            None,
        );
        if self.waste_download_row(ui, "Download the container spec") {
            let uses_result = self.waste_package.uses_this_result();
            let saved = self
                .waste_properties
                .map(&active)
                .and_then(|p| waste_package::package_spec(&self.waste_package, &targets, "result.json", p))
                .and_then(|spec| serde_json::to_string_pretty(&spec).map_err(|e| e.to_string()))
                .and_then(|text| web::download("package-spec.json", text.as_bytes()))
                .map(|()| {
                    if uses_result {
                        "Download started. Put the run result beside it as result.json and run `actinv waste package package-spec.json`.".to_owned()
                    } else {
                        "Download started. Run `actinv waste package package-spec.json`.".to_owned()
                    }
                });
            self.report(saved);
        }
    }

    fn waste_ranges(&mut self, ui: &mut egui::Ui) {
        if self.waste_bounds.mass_g.is_none() {
            self.waste_bounds.mass_g = self.problem["material"]["mass_g"].as_f64();
            let density = self.problem["material"]["density_g_cm3"].as_f64();
            self.waste_bounds.component.density_g_cm3 = density.unwrap_or(0.);
            self.waste_bounds.component.use_density = density.is_some();
        }
        let steps: Vec<(u64, String)> = self
            .result
            .as_ref()
            .map(|r| {
                r.steps()
                    .iter()
                    .enumerate()
                    .map(|(i, s)| (i as u64 + 1, model::readable_time(model::number(&s["t_s"]))))
                    .collect()
            })
            .unwrap_or_default();
        if let waste_bounds::BoundsAction::Fill {
            time,
            step,
            spread_pct,
        } = waste_bounds::show_form(ui, &mut self.waste_bounds, &steps)
        {
            if let Some(result) = &self.result {
                let activity: Vec<(String, f64)> = result
                    .steps()
                    .get(step)
                    .and_then(|s| s["activity_Bq_per_g"].as_object())
                    .into_iter()
                    .flatten()
                    .filter_map(|(n, a)| a.as_f64().map(|a| (n.clone(), a)))
                    .collect();
                self.waste_bounds.fill(time, &activity, spread_pct);
            }
        }
        let active = self.waste_bounds.nuclides();
        waste_forms::section(ui, "Nuclide properties", "Z, half-life and alpha emission of every nuclide with a range. The browser has no decay data, so enter them here.");
        waste_forms::properties_card(
            ui,
            "bounds",
            &mut self.waste_properties,
            &active,
            &Value::Null,
            None,
        );
        if self.waste_download_row(ui, "Download the ranges spec") {
            let saved = self
                .waste_properties
                .map(&active)
                .and_then(|p| waste_bounds::bounds_spec(&self.waste_bounds, p))
                .and_then(|spec| serde_json::to_string_pretty(&spec).map_err(|e| e.to_string()))
                .and_then(|text| web::download("bounds-spec.json", text.as_bytes()))
                .map(|()| "Download started. Run `actinv waste bounds bounds-spec.json`; it needs no nuclear data.".to_owned());
            self.report(saved);
        }
    }

    fn waste_sure(&mut self, ui: &mut egui::Ui) {
        if let Some(why) = waste_uncertainty::unsupported(&self.problem) {
            waste_forms::card(ui, |ui| {
                ui.colored_label(ui.visuals().warn_fg_color, why);
            });
            return;
        }
        if self.waste_sure.component.density_g_cm3 == 0. {
            let density = self.problem["material"]["density_g_cm3"].as_f64();
            self.waste_sure.component.density_g_cm3 = density.unwrap_or(0.);
            self.waste_sure.component.use_density = density.is_some();
        }
        let steps: Vec<(u64, String)> = crate::views::certify::step_times(&self.problem)
            .into_iter()
            .enumerate()
            .map(|(i, t)| (i as u64 + 1, t.label))
            .collect();
        let mass = self.problem["material"]["mass_g"].as_f64().unwrap_or(1.);
        waste_uncertainty::show_form(ui, &mut self.waste_sure, &steps, mass, None);
        if self.waste_download_row(ui, "Download the sampling spec") {
            let targets: Vec<u64> = steps
                .iter()
                .map(|(n, _)| *n)
                .filter(|n| !self.waste_sure.skipped.contains(n))
                .collect();
            let pretty = |v: &Value| serde_json::to_string_pretty(v).map_err(|e| e.to_string());
            let saved = waste_uncertainty::uncertainty_spec(&self.waste_sure, "problem.json", &targets, mass)
                .and_then(|spec| {
                    web::download("uncertainty.json", pretty(&spec)?.as_bytes())?;
                    web::download("problem.json", pretty(&waste_uncertainty::sampled_problem(&self.problem))?.as_bytes())
                })
                .map(|()| "Two downloads started: uncertainty.json and problem.json. Keep them in one folder and run `actinv waste uncertainty uncertainty.json` where the nuclear data are installed.".to_owned());
            self.report(saved);
        }
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

    /// Global actions, the same on every page: the problem and result files on the left, the
    /// appearance and the account on the right. On a narrow window the files move into the menu.
    /// Returns the bar's bottom edge.
    fn toolbar(&mut self, ui: &mut egui::Ui) -> f32 {
        let width = ui.ctx().content_rect().width();
        let (narrow, compact) = (width < NARROW, width < 1100.);
        let unsaved = self.raw_dirty || self.problem != self.saved;
        crate::web::mark_unsaved(unsaved);
        let mut action = None;
        let bar = egui::Panel::top("toolbar").show(ui, |ui| {
            egui::containers::Sides::new().shrink_left().show(
                ui,
                |ui| {
                    ui.horizontal_wrapped(|ui| {
                        if narrow {
                            let text = if self.menu_open { "Close menu" } else { "Menu" };
                            if ui.selectable_label(self.menu_open, text).clicked() {
                                self.menu_open = !self.menu_open;
                            }
                        } else {
                            action = file_buttons(ui, compact, unsaved);
                        }
                        if self.pending_file.is_some() {
                            ui.spinner();
                        }
                    });
                },
                |ui| {
                    self.suite.controls(ui);
                    let text = if self.dark {
                        "Light theme"
                    } else {
                        "Dark theme"
                    };
                    if ui.button(text).clicked() {
                        self.dark = !self.dark;
                        ui.ctx().set_theme(if self.dark {
                            egui::ThemePreference::Dark
                        } else {
                            egui::ThemePreference::Light
                        });
                    }
                },
            );
        });
        if let Some(action) = action {
            self.file_action(action, &ui.ctx().clone());
        }
        bar.response.rect.bottom()
    }

    fn file_action(&mut self, action: ToolbarAction, ctx: &egui::Context) {
        match action {
            ToolbarAction::Open => self.pick(FileKind::Problem, &["json"], ctx),
            ToolbarAction::Import => {
                self.import.get_or_insert_with(ImportState::default);
            }
            ToolbarAction::Download => self.export_problem(),
            ToolbarAction::OpenResult => self.pick(FileKind::Result, &["json"], ctx),
        }
    }

    /// The sidebar of a wide window. A narrow window shows the same list full width, from the
    /// Menu button.
    fn sidebar(&mut self, ui: &mut egui::Ui) {
        if ui.ctx().content_rect().width() < NARROW {
            return;
        }
        egui::Panel::left("navigation")
            .default_size(215.)
            .resizable(false)
            .show(ui, |ui| {
                egui::ScrollArea::vertical()
                    .auto_shrink([false, false])
                    .show(ui, |ui| self.nav_list(ui, false));
            });
    }

    /// The pages in the order a calculation is set up and read. Results are listed once there is
    /// one to read, and the studies open from one page of cards. In the narrow menu the file
    /// actions come first.
    fn nav_list(&mut self, ui: &mut egui::Ui, narrow: bool) {
        let accent = visuals::accent(ui);
        ui.spacing_mut().item_spacing.y = 3.;
        ui.spacing_mut().button_padding.y = 5.;
        ui.add_space(8.);
        if narrow {
            let unsaved = self.raw_dirty || self.problem != self.saved;
            let action = ui
                .horizontal_wrapped(|ui| file_buttons(ui, false, unsaved))
                .inner;
            if let Some(action) = action {
                self.menu_open = false;
                self.file_action(action, &ui.ctx().clone());
            }
            ui.add_space(10.);
        }
        let mut go = None;
        let mut hub = false;
        let item = |ui: &mut egui::Ui, selected: bool, text: String, ready: bool| {
            ui.horizontal(|ui| {
                let clicked = ui.selectable_label(selected, text).clicked();
                // the tick sits at the list's right edge so the ticks line up
                if ready {
                    ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                        ui.add_space(8.);
                        visuals::tick(ui, accent).on_hover_text("This step is filled in");
                    });
                }
                clicked
            })
            .inner
        };
        let current = if self.hub { None } else { Some(self.page) };
        if item(ui, current == Some(Page::Start), "Start".into(), false) {
            go = Some(Page::Start);
        }
        ui.add_space(10.);
        ui.label(
            RichText::new(Group::Setup.caption().unwrap_or_default())
                .small()
                .color(accent),
        );
        for page in Page::STEPS {
            let n = page.step().unwrap_or_default();
            let ready = step_ready(&self.problem, page);
            if item(
                ui,
                current == Some(page),
                format!("{n}  {}", page.label()),
                ready,
            ) {
                go = Some(page);
            }
        }
        ui.add_space(10.);
        ui.label(
            RichText::new(Group::Results.caption().unwrap_or_default())
                .small()
                .color(accent),
        );
        let results = browser_pages().filter(|p| p.needs_result() && *p != Page::Waste);
        let mut listed = false;
        for page in results {
            // a result page without a result is listed only while it is shown
            if self.result.is_some() || current == Some(page) {
                listed = true;
                if item(ui, current == Some(page), page.label().into(), false) {
                    go = Some(page);
                }
            }
        }
        if !listed {
            ui.label(RichText::new("Open a result to explore it here.").weak());
        }
        ui.add_space(10.);
        ui.label(
            RichText::new(Group::Studies.caption().unwrap_or_default())
                .small()
                .color(accent),
        );
        let in_study = current.is_some_and(|p| STUDIES.iter().any(|s| s.0 == p));
        if item(ui, self.hub, "All studies".into(), false) {
            hub = true;
        }
        // inside a study, its neighbours are one click away
        if in_study {
            for (page, title, _) in STUDIES {
                ui.horizontal(|ui| {
                    ui.add_space(12.);
                    if ui.selectable_label(current == Some(page), title).clicked() {
                        go = Some(page);
                    }
                });
            }
        }
        ui.add_space(10.);
        let here = current.is_some_and(|p| p.group() == Group::Tools);
        let open = here || !Group::Tools.folds() || self.nav_open.contains(&Group::Tools);
        if fold_header(ui, Group::Tools.caption().unwrap_or_default(), open, accent).clicked()
            && !here
        {
            if open {
                self.nav_open.retain(|g| *g != Group::Tools);
            } else {
                self.nav_open.push(Group::Tools);
            }
        }
        if open {
            for page in [Page::Data, Page::Advanced] {
                if item(ui, current == Some(page), page.label().into(), false) {
                    go = Some(page);
                }
            }
        }
        if let Some(page) = go {
            self.page = page;
            self.hub = false;
            self.menu_open = false;
        } else if hub {
            self.hub = true;
            self.hub_from = self.page;
            self.menu_open = false;
        }
    }

    /// The studies, one card each. The browser sets a study up and reads its result; the desktop
    /// app or the command line runs it.
    fn studies_hub(&mut self, ui: &mut egui::Ui) {
        visuals::heading(
            ui,
            "Studies",
            "Questions that take many calculations. Set one up here and download it; the desktop app or the command line runs it, and its result opens here.",
        );
        let cards: Vec<_> = STUDIES
            .iter()
            .map(|(_, title, text)| (*title, *text, "Open"))
            .collect();
        // five cards sit three and two; four to a row would leave one alone
        if let Some(i) = card_grid(ui, &cards, 3) {
            self.page = STUDIES[i].0;
            self.hub = false;
        }
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
        let bottom = self.toolbar(ui);
        self.suite.prompt(ui.ctx(), bottom);
        egui::Panel::bottom("status").show(ui, |ui| {
            ui.label(RichText::new(&self.status).color(if self.error {
                ui.visuals().error_fg_color
            } else {
                ui.visuals().text_color()
            }));
        });
        self.sidebar(ui);
        // a page change made anywhere else closes the studies page
        if self.hub && self.page != self.hub_from {
            self.hub = false;
        }
        egui::CentralPanel::default().show(ui, |ui| {
            egui::ScrollArea::vertical().show(ui, |ui| {
                // At most 1100 wide, but never wider than the window: `set_max_width` alone widens
                // a narrower window's content past its edge, where it is clipped.
                ui.set_max_width(ui.available_width().min(1100.));
                if self.menu_open && ui.ctx().content_rect().width() < NARROW {
                    self.nav_list(ui, true);
                    return;
                }
                ui.add_space(16.);
                if self.hub {
                    self.studies_hub(ui);
                    return;
                }
                match self.page {
                    Page::Start => self.start(ui),
                    Page::Material => self.material_page(ui),
                    Page::Schedule => self.schedule_page(ui),
                    Page::Spectrum => self.spectrum_page(ui),
                    Page::Outputs => self.outputs_page(ui),
                    Page::CheckRun => self.check_run(ui),
                    Page::Summary => self.summary_page(ui),
                    Page::Charts => self.charts(ui),
                    Page::Tables => self.tables_page(ui),
                    Page::Photons => self.photons_page(ui),
                    Page::Pathways => self.pathways_page(ui),
                    Page::Ledger => self.ledger_page(ui),
                    Page::Export => self.export_page(ui),
                    Page::Waste => self.waste_page(ui),
                    Page::Impurities => self.impurities_page(ui),
                    Page::Certify => self.certify_page(ui),
                    Page::Optimize => self.optimize_page(ui),
                    Page::Compare => self.compare_page(ui),
                    Page::Data => self.data_page(ui),
                    Page::Advanced => self.advanced_page(ui),
                    // the browser never runs the solver
                    Page::Sweep | Page::Mesh | Page::Measurements => self.start(ui),
                }
                if self.page.group() == Group::Setup && self.page != Page::CheckRun {
                    self.step_footer(ui);
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
        assert!(!pages.contains(&Page::Sweep) && !pages.contains(&Page::Measurements));
        assert!(!pages.contains(&Page::Mesh));
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

/// The question a container, ranges or how-sure waste result answers, from its schema.
fn waste_answer_question(value: &Value) -> Option<Question> {
    match value["schema"].as_str()? {
        "actinv-waste-package-1" => Some(Question::Container),
        "actinv-waste-bounds-result-1" => Some(Question::Ranges),
        "actinv-waste-uncertainty-result-1" => Some(Question::HowSure),
        _ => None,
    }
}
