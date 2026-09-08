use crate::{
    model::{self, ResultDocument},
    tour::Tour,
};
use actinv_core::spec::Spec;
use eframe::egui::{self, Color32, RichText, Vec2};
use egui_extras::{Column, TableBuilder};
use egui_plot::{Line, Plot};
use serde_json::{json, Value};
use std::{
    path::PathBuf,
    sync::mpsc::{self, Receiver},
    time::{Duration, Instant},
};

const BLUE: Color32 = Color32::from_rgb(24, 0, 173);
const PAGES: [&str; 8] = [
    "Overview & data",
    "Material",
    "Irradiation & cooling",
    "Spectrum",
    "Results",
    "Spectra & pathways",
    "Ledger & certificate",
    "Advanced JSON",
];
enum JobOutput {
    Calculation(Value),
    Data(Value),
}
type JobResult = Result<JobOutput, String>;

pub struct Desktop {
    document: Value,
    saved: Value,
    saved_base: PathBuf,
    path: Option<PathBuf>,
    base: PathBuf,
    page: usize,
    status: String,
    error: bool,
    logo: egui::TextureHandle,
    result: Option<ResultDocument>,
    comparison: Option<ResultDocument>,
    job: Option<(Receiver<JobResult>, Instant)>,
    step: usize,
    metric: usize,
    selected: String,
    filter: String,
    ledger_filter: String,
    descending: bool,
    reset_plot: bool,
    isotope: String,
    amount: f64,
    raw: String,
    raw_dirty: bool,
    tour: Tour,
    help: bool,
    pending: Option<Pending>,
    undo: Vec<Value>,
    redo: Vec<Value>,
    allow_close: bool,
    downloaded: Option<Value>,
    scene_view: egui::Rect,
    pathway_node: String,
    capture: Option<crate::capture::Capture>,
}
#[derive(Clone, Copy)]
enum Pending {
    Open,
    Example,
    Close,
}
impl Desktop {
    pub fn new(cc: &eframe::CreationContext<'_>) -> Self {
        Self::from_context(&cc.egui_ctx)
    }
    fn from_context(ctx: &egui::Context) -> Self {
        let mut visuals = egui::Visuals::light();
        visuals.selection.bg_fill = BLUE;
        visuals.selection.stroke = egui::Stroke::new(1., Color32::WHITE);
        visuals.hyperlink_color = BLUE;
        visuals.panel_fill = Color32::from_rgb(247, 248, 252);
        ctx.set_visuals(visuals);
        ctx.style_mut_of(egui::Theme::Light, |s| {
            s.spacing.item_spacing = Vec2::new(10., 9.);
            s.spacing.button_padding = Vec2::new(12., 7.);
        });
        let decoded = image::load_from_memory(include_bytes!("../assets/avila-labs-logo.png"))
            .expect("embedded logo")
            .into_rgba8();
        let logo = ctx.load_texture(
            "avila-labs",
            egui::ColorImage::from_rgba_unmultiplied(
                [decoded.width() as usize, decoded.height() as usize],
                decoded.as_raw(),
            ),
            Default::default(),
        );
        let document = model::decode_problem(model::EXAMPLE).expect("bundled example");
        let saved = document.clone();
        Self {
            document, saved, saved_base:model::working_directory(), path:None,
            base:model::working_directory(), page:0,
            status:"Start with the iron example, or open your own problem. Choose your installed nuclear data below.".into(),
            error:false, logo, result:None, comparison:None, job:None,
            step:0, metric:0, selected:String::new(), filter:String::new(), ledger_filter:String::new(),
            descending:true, reset_plot:false, isotope:String::new(), amount:0.,
            raw:String::new(), raw_dirty:false, tour:Tour::default(), help:false,
            pending:None, undo:vec![], redo:vec![], allow_close:false,
            downloaded:None, scene_view:egui::Rect::ZERO, pathway_node:String::new(), capture:crate::capture::Capture::from_env(),
        }
    }
    fn dirty(&self) -> bool {
        self.raw_dirty || self.document != self.saved || self.base != self.saved_base
    }
    fn report(&mut self, result: Result<String, String>) {
        match result {
            Ok(s) => {
                self.status = s;
                self.error = false;
            }
            Err(e) => {
                self.status = e;
                self.error = true;
            }
        }
    }
    fn request(&mut self, p: Pending) {
        if self.dirty() {
            self.pending = Some(p);
        } else {
            self.perform(p);
        }
    }
    fn perform(&mut self, p: Pending) {
        match p {
            Pending::Open => {
                if let Some(path) = rfd::FileDialog::new()
                    .add_filter("ACTINV problem", &["json"])
                    .pick_file()
                {
                    let result = std::fs::read_to_string(&path)
                        .map_err(|e| e.to_string())
                        .and_then(|s| model::decode_problem(&s));
                    match result {
                        Ok(doc) => {
                            self.document = doc;
                            self.saved = self.document.clone();
                            self.saved_base = self.base.clone();
                            self.base = path.parent().unwrap_or(std::path::Path::new(".")).into();
                            self.saved_base = self.base.clone();
                            self.path = Some(path);
                            self.raw_dirty = false;
                            self.undo.clear();
                            self.redo.clear();
                            self.page = 0;
                            self.report(Ok("Problem opened. Relative input paths use the problem's folder; change Input base if needed.".into()));
                        }
                        Err(e) => self.report(Err(e)),
                    }
                }
            }
            Pending::Example => {
                self.document = model::decode_problem(model::EXAMPLE).unwrap();
                self.saved = self.document.clone();
                self.saved_base = self.base.clone();
                self.path = None;
                self.base = model::working_directory();
                self.saved_base = self.base.clone();
                self.raw_dirty = false;
                self.undo.clear();
                self.redo.clear();
                self.page = 0;
            }
            Pending::Close => {}
        }
    }
    fn save_problem(&mut self) -> bool {
        if self.raw_dirty {
            self.report(Err("Apply or discard your JSON edits before saving.".into()));
            return false;
        }
        let path = self.path.clone().or_else(|| {
            rfd::FileDialog::new()
                .add_filter("JSON", &["json"])
                .set_file_name("problem.json")
                .save_file()
        });
        if let Some(path) = path {
            // Save resolved paths so Save As cannot silently change input meaning.
            let value = if path.parent() != Some(self.base.as_path()) {
                match model::resolve_inputs(&self.document, &self.base)
                    .and_then(|s| serde_json::to_value(s).map_err(|e| e.to_string()))
                {
                    Ok(v) => v,
                    Err(e) => {
                        self.report(Err(e));
                        return false;
                    }
                }
            } else {
                self.document.clone()
            };
            match model::write_json(&path, &value) {
                Ok(()) => {
                    self.document = value;
                    self.saved = self.document.clone();
                    self.saved_base = self.base.clone();
                    self.path = Some(path);
                    self.report(Ok("Problem saved.".into()));
                    return true;
                }
                Err(e) => self.report(Err(e)),
            }
        }
        false
    }
    fn validate(&self) -> Result<Spec, String> {
        if self.raw_dirty {
            return Err("Apply or discard your JSON edits before validating or running.".into());
        }
        let spec = model::resolve_inputs(&self.document, &self.base)?;
        model::check_files(&spec)?;
        Ok(spec)
    }
    fn run(&mut self, ctx: &egui::Context) {
        match self.validate() {
            Err(e) => self.report(Err(e)),
            Ok(spec) => {
                let (tx, rx) = mpsc::channel();
                let ctx = ctx.clone();
                if let Err(error) = std::thread::Builder::new()
                    .name("actinv-solver".into())
                    .stack_size(model::SOLVER_STACK_BYTES)
                    .spawn(move || {
                        let result = model::solve(spec).map(JobOutput::Calculation);
                        let _ = tx.send(result);
                        ctx.request_repaint();
                    })
                {
                    self.report(Err(format!("Could not start calculation: {error}")));
                    return;
                }
                self.job = Some((rx, Instant::now()));
                self.report(Ok(
                    "Preparing data and solving… The first preparation can take longer.".into(),
                ));
            }
        }
    }
    fn open_result(&mut self, compare: bool) {
        if let Some(path) = rfd::FileDialog::new()
            .add_filter("ACTINV result", &["json"])
            .pick_file()
        {
            let r = std::fs::read_to_string(&path)
                .map_err(|e| e.to_string())
                .and_then(|s| serde_json::from_str(&s).map_err(|e| e.to_string()))
                .and_then(|v| {
                    ResultDocument::parse(
                        v,
                        path.file_name()
                            .unwrap_or_default()
                            .to_string_lossy()
                            .into(),
                    )
                });
            match r {
                Ok(r) => {
                    if compare {
                        self.comparison = Some(r);
                    } else {
                        self.result = Some(r);
                        self.step = 0;
                        self.selected.clear();
                    }
                    self.reset_plot = true;
                    self.page = 4;
                    self.report(Ok("Result loaded.".into()));
                }
                Err(e) => self.report(Err(e)),
            }
        }
    }
    fn export(&mut self, csv: bool) {
        let Some(result) = &self.result else {
            return;
        };
        if let Some(path) = rfd::FileDialog::new()
            .set_file_name(if csv { "inventory.csv" } else { "result.json" })
            .save_file()
        {
            let r = if csv {
                model::inventory_csv(result, self.step)
                    .and_then(|csv| std::fs::write(path, csv).map_err(|e| e.to_string()))
            } else {
                model::write_json(&path, &result.value)
            };
            self.report(r.map(|()| "Export saved.".into()));
        }
    }
    fn toolbar(&mut self, ui: &mut egui::Ui) {
        egui::Panel::top("toolbar").show(ui,|ui| {
            ui.horizontal(|ui| {
                ui.image((self.logo.id(),Vec2::splat(44.)));
                ui.vertical(|ui|{ui.label(RichText::new("ACTINV").size(24.).strong());ui.label(RichText::new("AVILA LABS  /  ACTIVATION & INVENTORY").size(10.).color(BLUE));});
                ui.separator();
                let open=ui.button("Open problem").on_hover_text("Load an ACTINV specification (Ctrl+O)");self.tour.mark("open",open.rect);if open.clicked(){self.request(Pending::Open);}
                if ui.button(if self.dirty(){"Save •"}else{"Save"}).on_hover_text("Save problem (Ctrl+S)").clicked(){self.save_problem();}
                let valid=ui.button("Validate");self.tour.mark("validate",valid.rect);if valid.clicked(){let r=self.validate().map(|_|"Specification and input locations are valid. File hashes and evaluated data are checked during the solve.".into());self.report(r);}
                let run=ui.add_enabled(self.job.is_none(),egui::Button::new(RichText::new("▶ Run").color(Color32::WHITE)).fill(BLUE));self.tour.mark("run",run.rect);if run.clicked(){self.run(ui.ctx());}
                ui.with_layout(egui::Layout::right_to_left(egui::Align::Center),|ui|{if ui.button("? Help").clicked(){self.help=true;}
if self.job.is_some(){ui.spinner();}});
            });
        });
    }
    fn navigation(&mut self, ui: &mut egui::Ui) {
        egui::Panel::left("navigation")
            .default_size(205.)
            .min_size(165.)
            .resizable(true)
            .show(ui, |ui| {
                ui.add_space(16.);
                ui.label(RichText::new("CALCULATION").small().color(BLUE));
                for (i, label) in PAGES.iter().enumerate() {
                    if i == 4 {
                        ui.add_space(18.);
                        ui.label(RichText::new("EXPLORE").small().color(BLUE));
                    }
                    if i == 7 {
                        ui.add_space(18.);
                    }
                    let r = ui.selectable_label(self.page == i, *label);
                    if r.clicked() {
                        self.page = i;
                    }
                }
                ui.add_space(24.);
                ui.separator();
                if ui.button("Load iron example").clicked() {
                    self.request(Pending::Example);
                }
                if ui
                    .add_enabled(
                        !self.undo.is_empty() && !self.raw_dirty,
                        egui::Button::new("Undo edit"),
                    )
                    .clicked()
                {
                    if let Some(v) = self.undo.pop() {
                        self.redo.push(std::mem::replace(&mut self.document, v));
                    }
                }
                if ui
                    .add_enabled(
                        !self.redo.is_empty() && !self.raw_dirty,
                        egui::Button::new("Redo edit"),
                    )
                    .clicked()
                {
                    if let Some(v) = self.redo.pop() {
                        self.undo.push(std::mem::replace(&mut self.document, v));
                    }
                }
                ui.add_space(12.);
                ui.label(
                    RichText::new("Same solver. Traceable inputs.\nEvery data gap stays visible.")
                        .small()
                        .weak(),
                );
            });
    }
    fn setup(&mut self, ui: &mut egui::Ui) {
        heading(
            ui,
            "Set up a calculation",
            "Choose your evaluated data, then define the material and irradiation history.",
        );
        ui.label("Calculation title");
        text_field(ui, &mut self.document["title"]);
        ui.add_space(12.);
        let response=egui::Frame::group(ui.style()).inner_margin(16.).show(ui,|ui| {
            ui.heading("Nuclear data");
            ui.label("Paths are resolved against the input base folder. Existing CLI examples commonly use the repository root.");
            ui.horizontal(|ui|{ui.label("Input base");let mut base=self.base.to_string_lossy().into_owned();if ui.add(egui::TextEdit::singleline(&mut base).desired_width((ui.available_width()-220.).max(150.))).changed(){self.base=PathBuf::from(base);}
if ui.button("Choose folder").clicked(){if let Some(p)=rfd::FileDialog::new().pick_folder(){self.base=p;}}});
            path_field(ui,"Activation library (.npz)",&mut self.document["library"]["path"]);
            ui.collapsing("Activation library SHA-256 pin",|ui|{
                ui.label("A declared hash must match the chosen library. Update or remove the pin when intentionally switching libraries. The solver always records the computed hash.");
                let mut hash=self.document["library"]["sha256"].as_str().unwrap_or("").to_owned();
                if ui.add(egui::TextEdit::singleline(&mut hash).desired_width(500.).hint_text("Optional SHA-256")).changed(){if hash.trim().is_empty(){self.document["library"].as_object_mut().unwrap().remove("sha256");}else{self.document["library"]["sha256"]=hash.into();}}
            });
            path_field(ui,"Primary decay data",&mut self.document["decay"]["primary"]);
            if self.document["decay"]["fallback"].is_string(){path_field(ui,"Fallback decay data",&mut self.document["decay"]["fallback"]);if ui.small_button("Remove fallback").clicked(){self.document["decay"].as_object_mut().unwrap().remove("fallback");}}
            else if ui.small_button("Add fallback decay data").clicked(){self.document["decay"]["fallback"]="".into();}
            if ui.add_enabled(self.job.is_none(),egui::Button::new("Download standard neutron data · 139 MiB…")).clicked() {
                if let Some(folder)=rfd::FileDialog::new().set_title("Choose the parent folder for actinv-data").pick_folder() {
                    let (tx,rx)=mpsc::channel();let ctx=ui.ctx().clone();
                    std::thread::spawn(move||{let output=actinv_cli::fetch_bundle(None,folder.join("actinv-data"),false).map(|s|JobOutput::Data(s.problem_fragment));let _=tx.send(output);ctx.request_repaint();});
                    self.job=Some((rx,Instant::now()));self.status="Downloading and verifying standard neutron data… Detailed progress is available in the terminal.".into();self.error=false;
                }
            }
            if let Some(fragment)=&self.downloaded {
                ui.label("Verified neutron data are ready. Applying paths keeps your material and spectrum; use a compatible neutron 709-group spectrum.");
                if ui.button("Use downloaded library & decay paths").clicked(){
                    self.document["library"]=fragment["library"].clone();self.document["decay"]=fragment["decay"].clone();
                    self.document["projectile"]=fragment["projectile"].clone();
                }
            }
            ui.collapsing("Need the standard nuclear data?",|ui|{ui.label("Run this once in a terminal, then select the downloaded files above:");ui.code("actinv data fetch");ui.label("The standard download includes neutron activation and decay data. Data are versioned separately from the application.");});
        });
        self.tour
            .mark("inputs", response.response.rect.intersect(ui.clip_rect()));
        ui.add_space(16.);
        ui.horizontal(|ui| {
            ui.label("Projectile");
            let p = self.document["projectile"]
                .as_str()
                .unwrap_or("neutron")
                .to_owned();
            egui::ComboBox::from_id_salt("projectile")
                .selected_text(&p)
                .show_ui(ui, |ui| {
                    for v in ["neutron", "proton", "deuteron", "alpha"] {
                        if ui.selectable_label(p == v, v).clicked() {
                            self.document["projectile"] = v.into();
                        }
                    }
                });
        });
        ui.add_space(16.);
        ui.collapsing("Calculation options & requested outputs",|ui|{
            if self.document["options"].is_null(){self.document["options"]=json!({});}
            let mut mode=self.document["options"]["mode"].as_str().unwrap_or("auto").to_owned();
            ui.horizontal(|ui|{ui.label("Solver mode");egui::ComboBox::from_id_salt("mode").selected_text(&mode).show_ui(ui,|ui|{for m in ["auto","trace","coupled"]{if ui.selectable_value(&mut mode,m.into(),m).changed(){self.document["options"]["mode"]=mode.clone().into();}}});});
            ui.label("Trace mode supports ranked production pathways. Auto chooses an appropriate mode based on the problem.");
            let mut temperature=self.document["options"]["temperature_K"].as_f64().unwrap_or(293.6);
            ui.horizontal(|ui|{ui.label("Temperature (K)");if ui.add(egui::DragValue::new(&mut temperature).speed(1.).range(0.0..=f64::MAX)).changed(){self.document["options"]["temperature_K"]=json!(temperature);}});
            let mut outputs=self.document["options"]["outputs"].as_array().cloned().unwrap_or_else(||vec![json!("inventory"),json!("activity"),json!("heat")]);
            ui.horizontal_wrapped(|ui|{for (key,label) in [("inventory","Inventory"),("activity","Activity"),("heat","Decay heat"),("photons","Photon spectrum"),("pathways","Production pathways")]{let mut enabled=outputs.iter().any(|v|v.as_str()==Some(key));if ui.checkbox(&mut enabled,label).changed(){outputs.retain(|v|v.as_str()!=Some(key));if enabled{outputs.push(json!(key));}self.document["options"]["outputs"]=json!(outputs);}}});
        });
        if ui.button("Continue to material >").clicked() {
            self.page = 1;
        }
    }
    fn material(&mut self, ui: &mut egui::Ui) {
        heading(ui,"Define the material","Use element symbols for natural composition, or individual isotope names for enrichment.");
        let group = ui.scope(|ui| {
            ui.horizontal(|ui| {
                ui.label("Mass (g)");
                numeric(ui, &mut self.document["material"]["mass_g"], 0.1);
                ui.label("Composition basis");
                let basis = self.document["material"]["basis"]
                    .as_str()
                    .unwrap_or("wt_percent")
                    .to_owned();
                egui::ComboBox::from_id_salt("basis")
                    .selected_text(&basis)
                    .show_ui(ui, |ui| {
                        for b in ["wt_percent", "atom_fraction", "atoms_per_g"] {
                            if ui.selectable_label(b == basis, b).clicked() {
                                self.document["material"]["basis"] = b.into();
                            }
                        }
                    });
            });
            ui.add_space(12.);
            let mut remove = None;
            if let Some(composition) = self.document["material"]["composition"].as_object_mut() {
                TableBuilder::new(ui)
                    .id_salt("material-table")
                    .striped(true)
                    .column(Column::remainder())
                    .column(Column::initial(180.))
                    .column(Column::initial(80.))
                    .header(28., |mut h| {
                        h.col(|ui| {
                            ui.strong("Element / isotope");
                        });
                        h.col(|ui| {
                            ui.strong("Amount");
                        });
                        h.col(|_| {});
                    })
                    .body(|mut body| {
                        for (key, value) in composition.iter_mut() {
                            body.row(34., |mut row| {
                                row.col(|ui| {
                                    ui.label(key);
                                });
                                row.col(|ui| {
                                    numeric(ui, value, 0.1);
                                });
                                row.col(|ui| {
                                    if ui.small_button("Remove").clicked() {
                                        remove = Some(key.clone());
                                    }
                                });
                            });
                        }
                    });
                if let Some(key) = remove {
                    composition.remove(&key);
                }
                let total: f64 = composition.values().map(model::number).sum();
                ui.label(format!("Composition total: {total:.6}"));
            }
            ui.horizontal(|ui| {
                ui.add(
                    egui::TextEdit::singleline(&mut self.isotope)
                        .hint_text("e.g. Fe56")
                        .desired_width(150.),
                );
                ui.add(egui::DragValue::new(&mut self.amount).speed(0.1));
                if ui.button("Add isotope").clicked() {
                    let key = self.isotope.trim();
                    if key.is_empty() {
                        self.report(Err("Enter an element or isotope name.".into()));
                    } else if let Some(map) =
                        self.document["material"]["composition"].as_object_mut()
                    {
                        if map.contains_key(key) {
                            self.status =
                                "That isotope is already listed. Edit its amount in the table."
                                    .into();
                            self.error = true;
                        } else {
                            map.insert(key.into(), json!(self.amount));
                            self.isotope.clear();
                        }
                    }
                }
            });
        });
        self.tour
            .mark("material", group.response.rect.intersect(ui.clip_rect()));
        ui.add_space(20.);
        if ui.button("Continue to irradiation & cooling >").clicked() {
            self.page = 2;
        }
    }
    fn schedule(&mut self, ui: &mut egui::Ui) {
        heading(ui,"Irradiation & cooling","Durations accept units such as 5 min, 2 h, or 10 y. A zero flux multiplier is cooling.");
        let r = ui.scope(|ui| {
            let mut swap = None;
            let mut remove = None;
            if let Some(steps) = self.document["schedule"].as_array_mut() {
                let count = steps.len();
                egui::ScrollArea::vertical()
                    .id_salt("schedule-rows")
                    .max_height(285.)
                    .show(ui, |ui| {
                        for (i, step) in steps.iter_mut().enumerate() {
                            let (drop, payload) = ui.dnd_drop_zone::<usize, _>(
                                egui::Frame::group(ui.style()),
                                |ui| {
                                    ui.horizontal_wrapped(|ui| {
                                        ui.dnd_drag_source(
                                            egui::Id::new(("schedule-drag", i)),
                                            i,
                                            |ui| {
                                                ui.label("::").on_hover_text("Drag to reorder");
                                            },
                                        );
                                        ui.label(format!("{:02}", i + 1));
                                        let mut dt = step["dt"].as_str().unwrap_or("").to_owned();
                                        if ui
                                            .add(
                                                egui::TextEdit::singleline(&mut dt)
                                                    .desired_width(90.),
                                            )
                                            .changed()
                                        {
                                            step["dt"] = dt.into();
                                        }
                                        ui.label("× flux");
                                        numeric(ui, &mut step["flux"], 0.1);
                                        ui.label(if model::number(&step["flux"]) == 0. {
                                            "Cooling"
                                        } else {
                                            "Irradiation"
                                        });
                                        if ui.add_enabled(i > 0, egui::Button::new("Up")).clicked()
                                        {
                                            swap = Some((i, i - 1));
                                        }
                                        if ui
                                            .add_enabled(i + 1 < count, egui::Button::new("Down"))
                                            .clicked()
                                        {
                                            swap = Some((i, i + 1));
                                        }
                                        if ui.small_button("Remove").clicked() {
                                            remove = Some(i);
                                        }
                                    });
                                },
                            );
                            let _ = drop;
                            if let Some(source) = payload {
                                swap = Some((*source, i));
                            }
                        }
                    });
                if let Some((a, b)) = swap {
                    if a < steps.len() && b < steps.len() {
                        steps.swap(a, b);
                    }
                }
                if let Some(i) = remove {
                    steps.remove(i);
                }
                ui.horizontal(|ui| {
                    if ui.button("+ Irradiation").clicked() {
                        steps.push(json!({"dt":"5 min","flux":1.}));
                    }
                    if ui.button("+ Cooling").clicked() {
                        steps.push(json!({"dt":"1 h","flux":0.}));
                    }
                });
                ui.add_space(15.);
                ui.label("Schedule preview · block widths proportional to duration");
                let durations: Vec<_> = steps
                    .iter()
                    .map(|s| {
                        actinv_core::spec::parse_duration(s["dt"].as_str().unwrap_or(""))
                            .unwrap_or(0.)
                            .max(0.)
                    })
                    .collect();
                let total: f64 = durations.iter().sum();
                let (rect, _) = ui.allocate_exact_size(
                    Vec2::new(ui.available_width(), 42.),
                    egui::Sense::hover(),
                );
                let mut x = rect.left();
                for (i, d) in durations.iter().enumerate() {
                    let width = if total > 0. {
                        rect.width() * (*d / total) as f32
                    } else {
                        0.
                    };
                    let r =
                        egui::Rect::from_min_size(egui::pos2(x, rect.top()), Vec2::new(width, 42.));
                    ui.painter().rect_filled(
                        r.shrink(1.),
                        3.,
                        if model::number(&steps[i]["flux"]) == 0. {
                            Color32::from_rgb(146, 169, 198)
                        } else {
                            BLUE
                        },
                    );
                    if width > 24. {
                        ui.painter().text(
                            r.center(),
                            egui::Align2::CENTER_CENTER,
                            format!("{}", i + 1),
                            egui::FontId::proportional(13.),
                            Color32::WHITE,
                        );
                    }
                    x += width;
                }
                ui.label(format!(
                    "Total duration: {total:.4e} s · cobalt = irradiation · slate = cooling"
                ));
            }
        });
        self.tour
            .mark("schedule", r.response.rect.intersect(ui.clip_rect()));
        ui.add_space(16.);
        if ui.button("Continue to spectrum >").clicked() {
            self.page = 3;
        }
    }
    fn spectrum(&mut self, ui: &mut egui::Ui) {
        heading(
            ui,
            "Incident spectrum",
            "Review group values and explicitly choose the normalization used by the solver.",
        );
        let r=ui.scope(|ui|{
            ui.horizontal(|ui|{ui.label("Group structure");text_field(ui,&mut self.document["spectrum"]["structure"]);});
            let mut normalized=self.document["spectrum"]["total"].is_number();if ui.checkbox(&mut normalized,"Normalize group values to a total flux (particles / cm^2 / s)").changed(){if normalized{self.document["spectrum"]["total"]=json!(1e12);}else{self.document["spectrum"].as_object_mut().unwrap().remove("total");}}
            if normalized {numeric(ui,&mut self.document["spectrum"]["total"],1e10);}else{ui.label("Group values are absolute group fluxes (particles / cm^2 / s).");}
            let mut descending=self.document["spectrum"]["descending"].as_bool().unwrap_or(false);if ui.checkbox(&mut descending,"Input groups are highest-energy first").changed(){self.document["spectrum"]["descending"]=descending.into();}
            let points:Vec<[f64;2]>=self.document["spectrum"]["flux_per_group"].as_array().map(|a|a.iter().enumerate().map(|(i,v)|[i as f64+1.,model::number(v)]).collect()).unwrap_or_default();
            Plot::new("incident").height(260.).x_axis_label("Input group index").y_axis_label(if normalized{"Relative group weight"}else{"Group flux / cm^2 / s"}).show(ui,|p|p.line(Line::new("Incident spectrum",points).color(BLUE)));
            ui.collapsing("Edit group values",|ui|{if let Some(values)=self.document["spectrum"]["flux_per_group"].as_array_mut(){egui::ScrollArea::vertical().max_height(200.).show_rows(ui,28.,values.len(),|ui,range|{for i in range {ui.horizontal(|ui|{ui.label(format!("Group {}",i+1));numeric(ui,&mut values[i],1.);});}});}});
            ui.label("Custom boundaries and optional photon, uncertainty, or response settings are available in Advanced JSON.");
        });
        self.tour
            .mark("spectrum", r.response.rect.intersect(ui.clip_rect()));
    }
    fn results(&mut self, ui: &mut egui::Ui) {
        heading(
            ui,
            "Explore the inventory",
            "Select a time and a nuclide to connect the table with its history.",
        );
        ui.horizontal(|ui| {
            let open = ui.button("Open result");
            self.tour.mark("result_open", open.rect);
            if open.clicked() {
                self.open_result(false);
            }
            if ui
                .add_enabled(self.result.is_some(), egui::Button::new("Add comparison"))
                .clicked()
            {
                self.open_result(true);
            }
            if self.comparison.is_some() && ui.button("Remove comparison").clicked() {
                self.comparison = None;
            }
            let export = ui.add_enabled(self.result.is_some(), egui::Button::new("Export JSON"));
            self.tour.mark("export", export.rect);
            if export.clicked() {
                self.export(false);
            }
            if ui
                .add_enabled(
                    self.result.is_some(),
                    egui::Button::new("Export inventory CSV"),
                )
                .clicked()
            {
                self.export(true);
            }
        });
        let Some(result) = &self.result else {
            let r=ui.group(|ui|{ui.add_space(30.);ui.heading("Your results will appear here");ui.label("Run a calculation or open a result JSON to explore activity, decay heat, and nuclide inventory.");ui.add_space(30.);});
            self.tour.mark("chart", r.response.rect);
            self.tour.mark("inventory", r.response.rect);
            return;
        };
        ui.label(RichText::new(&result.label).strong());
        if let Some(title) = result.value["spec_title"].as_str() {
            ui.label(title);
        }
        if let Some(other) = &self.comparison {
            ui.label(format!("Comparison: {} · absolute physical time; normalization is as recorded in each result",other.label));
        }
        ui.horizontal(|ui| {
            for (i, label) in ["Activity (Bq/g)", "Total heat (W/g)", "Inventory (atoms/g)"]
                .iter()
                .enumerate()
            {
                if ui.selectable_value(&mut self.metric, i, *label).changed() {
                    self.reset_plot = true;
                }
            }
            if ui.small_button("Reset view").clicked() {
                self.reset_plot = true;
            }
        });
        ui.horizontal(|ui| {
            ui.label("Computed step");
            ui.add(
                egui::Slider::new(&mut self.step, 0..=result.steps().len() - 1)
                    .custom_formatter(|v, _| format!("{}", v as usize + 1)),
            );
            ui.label(format!(
                "t = {:.5e} s",
                model::number(&result.steps()[self.step]["t_s"])
            ));
            if !self.selected.is_empty() {
                ui.label(format!("Selected: {}", self.selected));
                if ui.small_button("Clear selection").clicked() {
                    self.selected.clear();
                    self.reset_plot = true;
                }
            }
        });
        let primary: Vec<[f64; 2]> = result
            .steps()
            .iter()
            .map(|s| {
                [
                    model::number(&s["t_s"]),
                    model::metric(s, self.metric, &self.selected),
                ]
            })
            .collect();
        let mut plot = Plot::new("history")
            .height(240.)
            .x_axis_label("Time (s)")
            .legend(egui_plot::Legend::default());
        if self.reset_plot {
            plot = plot.reset();
            self.reset_plot = false;
        }
        let response_key = match self.metric {
            1 => Some("heat.total".to_owned()),
            0 if !self.selected.is_empty() => Some(format!("activity:{}", self.selected)),
            _ => None,
        };
        let band = response_key.as_ref().and_then(|key| {
            result
                .steps()
                .iter()
                .map(|s| {
                    let b = &s["uncertainty"]["responses"][key]["normal_interval"];
                    Some((s["t_s"].as_f64()?, b[0].as_f64()?, b[1].as_f64()?))
                })
                .collect::<Option<Vec<_>>>()
        });
        if band.is_some() {
            ui.label("Shaded band: recorded MF=33 normal interval. Confidence and coverage are in Spectra & pathways.");
        }
        let chart = plot.show(ui, |p| {
            if let Some(band) = &band {
                let xs: Vec<_> = band.iter().map(|b| b.0).collect();
                let lower: Vec<_> = band.iter().map(|b| b.1).collect();
                let upper: Vec<_> = band.iter().map(|b| b.2).collect();
                p.add(
                    egui_plot::FilledArea::new("MF=33 normal interval", &xs, &lower, &upper)
                        .fill_color(Color32::from_rgba_unmultiplied(24, 0, 173, 35)),
                );
            }

            p.line(
                Line::new(
                    if self.selected.is_empty() || self.metric == 1 {
                        "Whole material"
                    } else {
                        &self.selected
                    },
                    primary,
                )
                .color(BLUE)
                .width(2.),
            );
            if let Some(other) = &self.comparison {
                let points: Vec<[f64; 2]> = other
                    .steps()
                    .iter()
                    .map(|s| {
                        [
                            model::number(&s["t_s"]),
                            model::metric(s, self.metric, &self.selected),
                        ]
                    })
                    .collect();
                p.line(
                    Line::new(&other.label, points)
                        .color(Color32::from_rgb(192, 96, 25))
                        .style(egui_plot::LineStyle::dashed_dense()),
                );
            }
            p.vline(
                egui_plot::VLine::new(
                    "Selected step",
                    model::number(&result.steps()[self.step]["t_s"]),
                )
                .color(Color32::GRAY),
            );
            if p.response().clicked() {
                p.pointer_coordinate().map(|c| c.x)
            } else {
                None
            }
        });
        self.tour.mark("chart", chart.response.rect);
        if let Some(time) = chart.inner {
            self.step = result
                .steps()
                .iter()
                .enumerate()
                .min_by(|(_, a), (_, b)| {
                    (model::number(&a["t_s"]) - time)
                        .abs()
                        .total_cmp(&(model::number(&b["t_s"]) - time).abs())
                })
                .map(|(i, _)| i)
                .unwrap_or(0);
        }
        let step = &result.steps()[self.step];
        ui.horizontal(|ui| {
            ui.label(format!("Activity  {:.4e} Bq/g", model::metric(step, 0, "")));
            ui.separator();
            ui.label(format!("Heat  {:.4e} W/g", model::metric(step, 1, "")));
            ui.separator();
            ui.label(format!(
                "Inventory  {:.4e} atoms/g",
                model::metric(step, 2, "")
            ));
        });
        let table = ui.scope(|ui| {
            ui.horizontal(|ui| {
                ui.add(egui::TextEdit::singleline(&mut self.filter).hint_text("Filter nuclides…"));
                ui.checkbox(&mut self.descending, "Highest activity first");
            });
            let mut rows: Vec<_> = step["inventory"]
                .as_array()
                .map(|a| {
                    a.iter()
                        .filter(|r| {
                            r["nuclide"]
                                .as_str()
                                .unwrap_or("")
                                .to_lowercase()
                                .contains(&self.filter.to_lowercase())
                        })
                        .collect()
                })
                .unwrap_or_default();
            rows.sort_by(|a, b| {
                let name = |v: &Value| v["nuclide"].as_str().unwrap_or("").to_owned();
                if self.descending {
                    model::number(&step["activity_Bq_per_g"][name(b)])
                        .total_cmp(&model::number(&step["activity_Bq_per_g"][name(a)]))
                } else {
                    name(a).cmp(&name(b))
                }
            });
            TableBuilder::new(ui)
                .id_salt("inventory-table")
                .striped(true)
                .resizable(true)
                .column(Column::initial(220.).at_least(120.))
                .columns(Column::remainder().at_least(165.), 2)
                .max_scroll_height(250.)
                .header(28., |mut h| {
                    for label in ["Nuclide", "Atoms / g", "Activity (Bq / g)"] {
                        h.col(|ui| {
                            ui.strong(label);
                        });
                    }
                })
                .body(|body| {
                    body.rows(27., rows.len(), |mut row| {
                        let v = rows[row.index()];
                        let name = v["nuclide"].as_str().unwrap_or("");
                        row.set_selected(self.selected == name);
                        row.col(|ui| {
                            if ui.selectable_label(self.selected == name, name).clicked() {
                                self.selected = name.into();
                                self.reset_plot = true;
                            }
                        });
                        row.col(|ui| {
                            ui.monospace(format!("{:.6e}", model::number(&v["atoms_per_g"])));
                        });
                        row.col(|ui| {
                            ui.monospace(format!(
                                "{:.6e}",
                                model::number(&step["activity_Bq_per_g"][name])
                            ));
                        });
                    });
                });
        });
        self.tour
            .mark("inventory", table.response.rect.intersect(ui.clip_rect()));
    }
    fn details(&mut self, ui: &mut egui::Ui) {
        heading(
            ui,
            "Spectra & production pathways",
            "Explore optional outputs recorded by the calculation.",
        );
        let r=ui.scope(|ui|{
            let Some(result)=&self.result else{ui.label("Open a result in Results, or run a calculation first.");return;};
            ui.add(egui::Slider::new(&mut self.step,0..=result.steps().len()-1).text("Computed step").custom_formatter(|v,_|format!("{}",v as usize+1)));let step=&result.steps()[self.step];
            ui.heading("Decay photon source");
            if step["photon_source"].is_null(){ui.label("No photon source was requested. Add \"photons\" to options.outputs in Advanced JSON, then run again.");}else{
                if let Some(groups)=step["photon_source"]["groups"].as_array(){
                    if groups.iter().any(|g|model::number(&g["photons_s_g"])>0.) {
                    let bars:Vec<_>=groups.iter().map(|g|egui_plot::Bar::new(model::number(&g["centroid_eV"])/1e6,model::number(&g["photons_s_g"])).width((model::number(&g["high_eV"])-model::number(&g["low_eV"]))/1e6)).collect();
                    Plot::new("photon-groups").height(230.).x_axis_label("Photon energy (MeV)").y_axis_label("Photons / s / g per group").show(ui,|p|p.bar_chart(egui_plot::BarChart::new("Decay photons",bars).color(BLUE)));
                }
                    else {ui.label("No grouped photon emission is recorded at this step. Review the photon source details and ledger for missing evaluated spectra or unrepresented emission.");}
                }
                json_tree(ui,"Photon source",&step["photon_source"]);
            }
            ui.separator();ui.heading("Production pathways");
            if let Some(pathways)=result.value["pathways"].as_array().and_then(|p|p.get(self.step)).and_then(Value::as_object){
                if pathways.is_empty(){ui.label("No production pathways were recorded for this step. Pathway decomposition is available in trace mode.");}
                if !pathways.contains_key(&self.selected){self.selected=pathways.keys().next().cloned().unwrap_or_default();self.scene_view=egui::Rect::ZERO;}
                egui::ComboBox::from_id_salt("pathway-isotope").selected_text(if self.selected.is_empty(){"Choose a nuclide"}else{&self.selected}).show_ui(ui,|ui|{for key in pathways.keys(){ui.selectable_value(&mut self.selected,key.clone(),key);}});
                if let Some(paths)=pathways.get(&self.selected).and_then(Value::as_array){
                    ui.horizontal(|ui|{ui.label("Drag to pan; scroll to zoom.");if ui.small_button("Fit pathways").clicked(){self.scene_view=egui::Rect::ZERO;}});
                    ui.allocate_ui(Vec2::new(ui.available_width(),240.),|ui|{
                        egui::Scene::new().zoom_range(0.2..=2.).show(ui,&mut self.scene_view,|ui|{
                    let (rect,_)=ui.allocate_exact_size(Vec2::new(700.,(paths.len().min(12) as f32*52.).max(70.)),egui::Sense::hover());
                    for (i,path) in paths.iter().take(12).enumerate(){
                        draw_pathway(ui,rect,i,path,&self.selected,&mut self.pathway_node);
                    }
                        });
                    });
                    if !self.pathway_node.is_empty(){
                        let atom=step["inventory"].as_array().and_then(|rows|rows.iter().find(|r|r["nuclide"].as_str()==Some(&self.pathway_node))).and_then(|r|r["atoms_per_g"].as_f64());
                        ui.group(|ui|{ui.strong(format!("{} at step {}",self.pathway_node,self.step+1));if let Some(atoms)=atom{ui.label(format!("{atoms:.6e} atoms/g"));}else{ui.label("No inventory entry was recorded for this node at this step.");}
if let Some(activity)=step["activity_Bq_per_g"][&self.pathway_node].as_f64(){ui.label(format!("{activity:.6e} Bq/g"));}});
                    }
                    ui.label("Source > first product > selected nuclide. Intermediate chain members are not enumerated in this result format. Diagram shows up to 12 ranked contributions.");json_tree(ui,"All contributions",&Value::Array(paths.clone()));
                }
            }else{ui.label("No pathway output is present.");}
            for key in ["uncertainty","radiological"]{if !step[key].is_null(){json_tree(ui,key,&step[key]);}}
        });
        self.tour
            .mark("details", r.response.rect.intersect(ui.clip_rect()));
    }
    fn ledger(&mut self, ui: &mut egui::Ui) {
        heading(
            ui,
            "Ledger & certificate",
            "Inspect missing data, approximations, and the provenance behind the answer.",
        );
        let r = ui.scope(|ui| {
            if let Some(result) = &self.result {
                ui.add(egui::TextEdit::singleline(&mut self.ledger_filter).hint_text("Filter evidence by field or value…").desired_width(360.));
                if self.ledger_filter.is_empty(){
                    json_tree(ui, "Ledger", &result.value["ledger"]);
                    json_tree(ui, "Certificate", &result.value["certificate"]);
                }else{
                    let query=self.ledger_filter.to_lowercase();let mut found=false;
                    for section in ["ledger","certificate"]{ui.heading(section);if let Some(entries)=result.value[section].as_object(){for (key,value) in entries{if key.to_lowercase().contains(&query)||value.to_string().to_lowercase().contains(&query){found=true;json_tree(ui,key,value);}}}}
                    if !found{ui.label("No matching evidence fields. Try a nuclide name, a file hash, or a category such as decay.");}
                }
            } else {
                ui.label("Run a calculation or open a result to inspect its evidence.");
            }
        });
        self.tour
            .mark("ledger", r.response.rect.intersect(ui.clip_rect()));
    }
    fn advanced(&mut self, ui: &mut egui::Ui) {
        heading(ui,"Advanced specification","Edit every ACTINV option. Apply checks the schema; Validate checks the scientific inputs.");
        if !self.raw_dirty {
            self.raw = serde_json::to_string_pretty(&self.document).unwrap_or_default();
        }
        ui.horizontal(|ui| {
            if ui
                .add_enabled(self.raw_dirty, egui::Button::new("Apply JSON edits"))
                .clicked()
            {
                match model::decode_problem(&self.raw) {
                    Ok(v) => {
                        self.document = v;
                        self.raw_dirty = false;
                        self.report(Ok("JSON edits applied.".into()));
                    }
                    Err(e) => self.report(Err(e)),
                }
            }
            if ui
                .add_enabled(self.raw_dirty, egui::Button::new("Discard JSON edits"))
                .clicked()
            {
                self.raw_dirty = false;
            }
            ui.label("Other editors pause while JSON edits are pending.");
        });
        egui::ScrollArea::both().show(ui, |ui| {
            let theme =
                egui_extras::syntax_highlighting::CodeTheme::from_memory(ui.ctx(), ui.style());
            let mut layouter = |ui: &egui::Ui, text: &dyn egui::TextBuffer, wrap_width: f32| {
                let mut job = egui_extras::syntax_highlighting::highlight(
                    ui.ctx(),
                    ui.style(),
                    &theme,
                    text.as_str(),
                    "json",
                );
                job.wrap.max_width = wrap_width;
                ui.fonts_mut(|f| f.layout_job(job))
            };
            if ui
                .add(
                    egui::TextEdit::multiline(&mut self.raw)
                        .font(egui::TextStyle::Monospace)
                        .code_editor()
                        .desired_rows(28)
                        .desired_width(f32::INFINITY)
                        .layouter(&mut layouter),
                )
                .changed()
            {
                self.raw_dirty = true;
            }
        });
    }
}
impl eframe::App for Desktop {
    fn logic(&mut self, ctx: &egui::Context, _: &mut eframe::Frame) {
        let result = self.job.as_ref().and_then(|(rx, _)| match rx.try_recv() {
            Ok(v) => Some(v),
            Err(mpsc::TryRecvError::Disconnected) => Some(Err(
                "The calculation worker stopped unexpectedly. Check the terminal output.".into(),
            )),
            Err(mpsc::TryRecvError::Empty) => None,
        });
        if let Some(result) = result {
            self.job = None;
            match result {
                Ok(JobOutput::Data(fragment)) => {
                    self.downloaded = Some(fragment);
                    self.page = 0;
                    self.report(Ok(
                        "Data downloaded and verified. Use the installed paths below when ready."
                            .into(),
                    ));
                }
                Ok(JobOutput::Calculation(v)) => {
                    match ResultDocument::parse(v, "Completed calculation".into()) {
                        Ok(r) => {
                            self.result = Some(r);
                            self.step = 0;
                            self.selected.clear();
                            self.reset_plot = true;
                            self.page = 4;
                            self.report(Ok(
                                "Calculation complete. Explore the results and review the ledger."
                                    .into(),
                            ));
                        }
                        Err(e) => self.report(Err(e)),
                    }
                }
                Err(e) => self.report(Err(e)),
            }
        }
        if self.job.is_some() {
            ctx.request_repaint_after(Duration::from_millis(200));
        }
    }
    fn ui(&mut self, ui: &mut egui::Ui, _: &mut eframe::Frame) {
        self.render(ui);
    }
}
impl Desktop {
    fn render(&mut self, ui: &mut egui::Ui) {
        let ctx = ui.ctx().clone();
        if ctx.input(|i| i.viewport().close_requested())
            && !self.allow_close
            && (self.dirty() || self.job.is_some())
        {
            ctx.send_viewport_cmd(egui::ViewportCommand::CancelClose);
            self.pending = Some(Pending::Close);
        }
        if ctx.input_mut(|i| i.consume_key(egui::Modifiers::COMMAND, egui::Key::S)) {
            self.save_problem();
        }
        if ctx.input_mut(|i| i.consume_key(egui::Modifiers::COMMAND, egui::Key::O)) {
            self.request(Pending::Open);
        }
        if ctx.input(|i| i.key_pressed(egui::Key::F1)) {
            self.help = true;
        }
        if let Some(capture) = &mut self.capture {
            capture.prepare(&ctx, &mut self.page, &mut self.tour, &mut self.result);
        }
        self.tour.anchors.clear();
        if let Some(step) = self.tour.step() {
            self.page = step.page;
        }
        self.toolbar(ui);
        egui::Panel::bottom("status")
            .resizable(true)
            .default_size(76.)
            .show(ui, |ui| {
                egui::ScrollArea::vertical().show(ui, |ui| {
                    ui.horizontal_wrapped(|ui| {
                        if let Some((_, start)) = &self.job {
                            ui.spinner();
                            ui.label(format!("Running · {:.0}s", start.elapsed().as_secs_f64()));
                        }
                        ui.colored_label(
                            if self.error {
                                Color32::from_rgb(160, 40, 35)
                            } else {
                                Color32::from_rgb(60, 70, 90)
                            },
                            &self.status,
                        );
                    });
                });
            });
        self.navigation(ui);
        let before = self.document.clone();
        egui::CentralPanel::default().show(ui,|ui|{
            egui::ScrollArea::vertical().id_salt(("page",self.page)).show(ui,|ui|{
                ui.add_space(12.);
                if self.raw_dirty && self.page<4{ui.colored_label(BLUE,"Apply or discard your pending edits in Advanced JSON to resume editing here.");}
                ui.add_enabled_ui(!self.raw_dirty||self.page>=4,|ui|{match self.page {0=>self.setup(ui),1=>self.material(ui),2=>self.schedule(ui),3=>self.spectrum(ui),4=>self.results(ui),5=>self.details(ui),6=>self.ledger(ui),_=>self.advanced(ui)}});
            });
        });
        if before != self.document {
            self.undo.push(before);
            if self.undo.len() > 100 {
                self.undo.remove(0);
            }
            self.redo.clear();
        }
        if self.help {
            egui::Window::new("Help & walkthroughs").collapsible(false).resizable(false).default_width(430.).show(&ctx,|ui|{
            ui.heading("Let’s walk through ACTINV");ui.label("Guides highlight real controls while dimming the surrounding workspace. You can use the highlighted control, or press Next to keep exploring. Escape exits a tour.");
            if ui.button("Start: create and run a calculation").clicked(){self.help=false;self.tour.start(false);}
            if ui.button("Start: understand and export results").clicked(){self.help=false;self.tour.start(true);}
            ui.separator();ui.label("Shortcuts: Ctrl/Cmd+O opens a problem; Ctrl/Cmd+S saves; F1 opens Help.");ui.hyperlink_to("ACTINV specification guide","https://github.com/AvilaLabs/ACTINV/blob/master/docs/SPEC.md");ui.hyperlink_to("Data setup","https://github.com/AvilaLabs/ACTINV/blob/master/docs/DATA.md");ui.hyperlink_to("Qualification and limitations","https://github.com/AvilaLabs/ACTINV/blob/master/docs/QUALIFICATION.md");if ui.button("Close help").clicked(){self.help=false;}
        });
        }
        if let Some(pending) = self.pending {
            egui::Modal::new(egui::Id::new("unsaved")).show(&ctx,|ui|{ui.heading("Keep your work?");ui.label(if self.job.is_some(){"A calculation is still running. Closing the application will stop it."}else{"Your problem has unsaved changes. Save them before continuing, or explicitly discard them."});ui.horizontal(|ui|{
            if ui.button("Cancel").clicked(){self.pending=None;}
            if ui.button("Discard and continue").clicked(){self.pending=None;if matches!(pending,Pending::Close){self.allow_close=true;ctx.send_viewport_cmd(egui::ViewportCommand::Close);}else{self.perform(pending);}}
            if ui.button("Save and continue").clicked()&&self.save_problem(){self.pending=None;if matches!(pending,Pending::Close){self.allow_close=true;ctx.send_viewport_cmd(egui::ViewportCommand::Close);}else{self.perform(pending);}}
        });});
        }
        self.tour.show(&ctx);
        if let Some(capture) = &mut self.capture {
            capture.finish(&ctx);
        }
    }
}
fn draw_pathway(
    ui: &mut egui::Ui,
    rect: egui::Rect,
    index: usize,
    path: &Value,
    selected: &str,
    inspected: &mut String,
) {
    let source = path["from"].as_str().unwrap_or("?");
    let first = path["first_product"].as_str().unwrap_or("?");
    let names = if first == selected {
        vec![source, selected]
    } else {
        vec![source, first, selected]
    };
    let y = rect.top() + 26. + index as f32 * 52.;
    let xs: Vec<_> = (0..names.len())
        .map(|i| rect.left() + 65. + (rect.width() - 130.) * i as f32 / (names.len() - 1) as f32)
        .collect();
    for i in 0..names.len() - 1 {
        let from = egui::pos2(xs[i] + 47., y);
        let to = egui::pos2(xs[i + 1] - 50., y);
        ui.painter()
            .line_segment([from, to], egui::Stroke::new(1.5, BLUE));
        ui.painter().add(egui::Shape::convex_polygon(
            vec![to, to + Vec2::new(-9., -5.), to + Vec2::new(-9., 5.)],
            BLUE,
            egui::Stroke::NONE,
        ));
        if i == 1 {
            ui.painter().text(
                egui::pos2((from.x + to.x) / 2., y - 12.),
                egui::Align2::CENTER_CENTER,
                "via chain",
                egui::FontId::proportional(10.),
                Color32::DARK_GRAY,
            );
        }
    }
    for (i, (x, name)) in xs.into_iter().zip(names).enumerate() {
        let node = egui::Rect::from_center_size(egui::pos2(x, y), Vec2::new(95., 32.));
        if ui
            .interact(
                node,
                egui::Id::new(("pathway-node", index, i)),
                egui::Sense::click(),
            )
            .on_hover_text("Click to inspect this nuclide at the selected step")
            .clicked()
        {
            *inspected = name.into();
        }
        ui.painter()
            .rect_filled(node, 5., Color32::from_rgb(229, 226, 250));
        ui.painter().text(
            node.center(),
            egui::Align2::CENTER_CENTER,
            name,
            egui::FontId::proportional(14.),
            BLUE,
        );
    }
    ui.painter().text(
        egui::pos2(rect.center().x, y + 20.),
        egui::Align2::CENTER_TOP,
        format!(
            "{:.2}% of product atoms",
            100. * model::number(&path["fraction"])
        ),
        egui::FontId::proportional(10.),
        Color32::DARK_GRAY,
    );
}

fn heading(ui: &mut egui::Ui, title: &str, subtitle: &str) {
    ui.heading(RichText::new(title).size(27.));
    ui.label(RichText::new(subtitle).color(Color32::from_rgb(85, 93, 112)));
    ui.add_space(16.);
}
fn text_field(ui: &mut egui::Ui, value: &mut Value) {
    let mut s = value.as_str().unwrap_or("").to_owned();
    if ui
        .add(egui::TextEdit::singleline(&mut s).desired_width(230.))
        .changed()
    {
        *value = s.into();
    }
}
fn numeric(ui: &mut egui::Ui, value: &mut Value, speed: f64) {
    let mut n = model::number(value);
    if ui
        .add(
            egui::DragValue::new(&mut n)
                .speed(speed)
                .range(0.0..=f64::MAX),
        )
        .changed()
    {
        *value = json!(n);
    }
}
fn path_field(ui: &mut egui::Ui, label: &str, value: &mut Value) {
    ui.label(label);
    ui.horizontal(|ui| {
        let mut s = value.as_str().unwrap_or("").to_owned();
        if ui
            .add(
                egui::TextEdit::singleline(&mut s)
                    .desired_width((ui.available_width() - 100.).max(160.)),
            )
            .changed()
        {
            *value = s.into();
        }
        if ui.button("Browse…").clicked() {
            if let Some(p) = rfd::FileDialog::new().pick_file() {
                *value = p.to_string_lossy().into_owned().into();
            }
        }
    });
}
fn json_tree(ui: &mut egui::Ui, label: &str, value: &Value) {
    match value {
        Value::Object(map) => {
            egui::CollapsingHeader::new(format!(
                "{} · {} entries",
                label.replace('_', " "),
                map.len()
            ))
            .id_salt(label)
            .default_open(label == "Ledger")
            .show(ui, |ui| {
                for (k, v) in map {
                    json_tree(ui, k, v);
                }
            });
        }
        Value::Array(items) => {
            egui::CollapsingHeader::new(format!(
                "{} · {} items",
                label.replace('_', " "),
                items.len()
            ))
            .id_salt(label)
            .show(ui, |ui| {
                egui::ScrollArea::vertical().max_height(260.).show_rows(
                    ui,
                    24.,
                    items.len(),
                    |ui, range| {
                        for i in range {
                            ui.horizontal_wrapped(|ui| {
                                ui.monospace(format!("[{i}] {}", items[i]));
                            });
                        }
                    },
                );
            });
        }
        _ => {
            ui.horizontal_wrapped(|ui| {
                ui.strong(label.replace('_', " "))
                    .on_hover_text(format!("Result field: {label}"));
                ui.label(value.to_string());
            });
        }
    }
}

#[cfg(test)]
mod ui_tests {
    use super::*;
    #[test]
    fn every_page_and_setup_walkthrough_renders_at_minimum_size() {
        let ctx = egui::Context::default();
        let mut app = Desktop::from_context(&ctx);
        for page in 0..8 {
            app.page = page;
            for _ in 0..2 {
                let mut output = ctx.run_ui(
                    egui::RawInput {
                        screen_rect: Some(egui::Rect::from_min_size(
                            egui::Pos2::ZERO,
                            Vec2::new(900., 640.),
                        )),
                        ..Default::default()
                    },
                    |ui| app.render(ui),
                );
                assert!(!output.shapes.is_empty());
                output.textures_delta.clear();
            }
        }
        for (index, step) in crate::tour::SETUP.iter().enumerate() {
            app.tour.active = Some((false, index));
            for _ in 0..2 {
                let mut output = ctx.run_ui(
                    egui::RawInput {
                        screen_rect: Some(egui::Rect::from_min_size(
                            egui::Pos2::ZERO,
                            Vec2::new(900., 640.),
                        )),
                        ..Default::default()
                    },
                    |ui| app.render(ui),
                );
                output.textures_delta.clear();
            }
            let rect = app
                .tour
                .anchors
                .get(step.target)
                .expect("walkthrough target exists");
            assert!(
                rect.intersects(ctx.content_rect()),
                "target {} must be visible",
                step.target
            );
        }
    }
}
