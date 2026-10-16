//! Waste class: classify the loaded result against the bundled 10 CFR 61.55 tables. The page
//! builds an ordinary `actinv-waste-spec-1` document from the run and hands it to the CLI's own
//! evaluator, in process; what it shows is read from the result that evaluator writes.
use crate::{
    app::{heading, Desktop, BLUE},
    model::{self, ResultDocument},
    platform::Dialog,
    views::waste as view,
    visuals::accent,
};
use actinv_core::{
    run::PreparedRun,
    spec::Spec,
    waste::{normalize_nuclide_key, NuclideProperties},
};
use eframe::egui::{self, Color32, RichText};
use egui_extras::{Column, TableBuilder};
use serde_json::{json, Map, Value};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs, io,
    path::{Path, PathBuf},
    sync::mpsc::{self, Receiver, TryRecvError},
    time::Duration,
};

const RULES: &str = "us-nrc-10cfr61.55-v1";
const COMPONENT_ID: &str = "component";
/// The files one classification leaves behind. The spec names the input by this file name, so
/// the pair classifies the same way wherever the two are saved together.
const SPEC_FILE: &str = "waste-spec.json";
const INPUT_FILE: &str = "result.json";
const OUTPUT_FILE: &str = "waste-result.json";

#[derive(Clone, Copy, PartialEq, Eq)]
enum Volume {
    Entered,
    Density,
}

#[derive(Clone, Copy)]
enum JobKind {
    Properties,
    Classify,
}

struct Job {
    kind: JobKind,
    rx: Receiver<Result<Output, String>>,
}

enum Output {
    Properties {
        active: BTreeSet<String>,
        decay: Value,
        derived: BTreeMap<String, NuclideProperties>,
    },
    Classified(Classified),
}

/// A finished classification: the private folder holding the spec and the result it read, the
/// exact text the evaluator wrote, and that text parsed for display.
struct Classified {
    scratch: Scratch,
    text: String,
    view: Result<view::WasteView, String>,
}

/// A private folder for the files of one classification, removed when it is dropped.
struct Scratch(PathBuf);

impl Scratch {
    fn create() -> Result<Self, String> {
        let dir = std::env::temp_dir().join(format!(
            "actinv-gui-waste-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap_or_default()
                .as_nanos()
        ));
        let mut builder = fs::DirBuilder::new();
        #[cfg(unix)]
        {
            use std::os::unix::fs::DirBuilderExt;
            builder.mode(0o700);
        }
        builder
            .create(&dir)
            .map_err(|e| format!("could not create a private folder for the waste files: {e}"))?;
        Ok(Self(dir))
    }

    fn file(&self, name: &str) -> PathBuf {
        self.0.join(name)
    }
}

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

/// What the page remembers between frames.
#[derive(Default)]
pub(in crate::app) struct WasteState {
    activated_metal: bool,
    volume: Option<Volume>,
    volume_cm3: f64,
    external_h3: bool,
    h3_source: String,
    h3_bq: BTreeMap<u64, f64>,
    /// Steps taken out of the selection; every step is selected until one is listed here.
    skipped: BTreeSet<u64>,
    /// Properties read from the decay data, the nuclides they were read for and the problem's
    /// `decay` section at that time.
    derived: BTreeMap<String, NuclideProperties>,
    derived_for: Option<BTreeSet<String>>,
    decay: Value,
    /// Explicit entries that replace a nuclide's properties, by normalized nuclide key.
    overrides: BTreeMap<String, NuclideProperties>,
    job: Option<Job>,
    done: Option<Classified>,
    error: Option<String>,
}

/// What a frame asks the page to do once drawing is over.
enum Action {
    None,
    LoadProperties,
    Classify,
    SaveSpec,
    SaveResult,
}

impl WasteState {
    /// The volume basis in force: the material density only when the problem declares one.
    fn basis(&mut self, density: Option<f64>) -> Volume {
        let chosen = *self.volume.get_or_insert(if density.is_some() {
            Volume::Density
        } else {
            Volume::Entered
        });
        if density.is_some() {
            chosen
        } else {
            Volume::Entered
        }
    }

    /// The nuclides the classification needs properties for: those active at a selected step,
    /// and H-3 when external H-3 is declared, because the evaluator adds it to the inventory even
    /// where the run made none.
    fn nuclides(&self, steps: &[Value], selected: &[u64]) -> BTreeSet<String> {
        let mut nuclides = active_nuclides(steps, selected);
        if self.external_h3
            && !nuclides
                .iter()
                .any(|name| canonical(name) == canonical("H3"))
        {
            nuclides.insert("H3".to_owned());
        }
        nuclides
    }

    fn selected(&self, steps: &[(u64, f64)]) -> Vec<u64> {
        steps
            .iter()
            .map(|(number, _)| *number)
            .filter(|number| !self.skipped.contains(number))
            .collect()
    }

    /// True when classifying would not leave a nuclide's properties out by accident: they were
    /// read for exactly these nuclides from the decay data now in the problem, or the user has
    /// entered every one.
    fn properties_ready(&self, active: &BTreeSet<String>, document: &Value) -> bool {
        let loaded = self.derived_for.as_ref() == Some(active) && self.decay == document["decay"];
        loaded
            || active
                .iter()
                .all(|n| self.overrides.contains_key(&canonical(n)))
    }
}

fn canonical(name: &str) -> String {
    normalize_nuclide_key(name).unwrap_or_else(|_| name.to_owned())
}

fn step_number(step: &Value, index: usize) -> u64 {
    step["step"].as_u64().unwrap_or(index as u64 + 1)
}

/// The one-based number and time of every step of the result.
fn step_list(result: &ResultDocument) -> Vec<(u64, f64)> {
    result
        .steps()
        .iter()
        .enumerate()
        .map(|(index, step)| (step_number(step, index), model::number(&step["t_s"])))
        .collect()
}

/// Every nuclide with positive activity at one of the selected steps.
fn active_nuclides(steps: &[Value], selected: &[u64]) -> BTreeSet<String> {
    steps
        .iter()
        .enumerate()
        .filter(|(index, step)| selected.contains(&step_number(step, *index)))
        .flat_map(|(_, step)| step["activity_Bq_per_g"].as_object().into_iter().flatten())
        .filter(|(_, activity)| activity.as_f64().is_some_and(|a| a > 0.))
        .map(|(name, _)| name.clone())
        .collect()
}

/// One active nuclide: its normalized key, its name in the result and what the decay data gave.
struct PropertyRow {
    key: String,
    name: String,
    derived: Option<(String, NuclideProperties)>,
}

fn property_rows(
    active: &BTreeSet<String>,
    derived: &BTreeMap<String, NuclideProperties>,
) -> Vec<PropertyRow> {
    let by_key: BTreeMap<String, (&String, NuclideProperties)> = derived
        .iter()
        .map(|(name, properties)| (canonical(name), (name, *properties)))
        .collect();
    active
        .iter()
        .map(|name| {
            let key = canonical(name);
            PropertyRow {
                derived: by_key.get(&key).map(|(k, p)| ((*k).clone(), *p)),
                key,
                name: name.clone(),
            }
        })
        .collect()
}

/// The `nuclide_properties` of the spec: an override where there is one, else the decay data's
/// value. A nuclide with neither stays absent, so the evaluator reports it as unknown.
fn effective_properties(
    rows: &[PropertyRow],
    overrides: &BTreeMap<String, NuclideProperties>,
) -> Result<BTreeMap<String, NuclideProperties>, String> {
    let mut out = BTreeMap::new();
    for row in rows {
        if let Some(entry) = overrides.get(&row.key) {
            if entry.z < 1 || !entry.half_life_s.is_finite() || entry.half_life_s <= 0. {
                return Err(format!(
                    "The override for {} needs Z of at least 1 and a positive half-life.",
                    row.name
                ));
            }
            out.insert(row.key.clone(), *entry);
        } else if let Some((key, properties)) = &row.derived {
            out.insert(key.clone(), *properties);
        }
    }
    Ok(out)
}

enum Geometry {
    Volume(f64),
    Density(f64),
}

struct SpecInputs<'a> {
    targets: &'a [u64],
    properties: &'a BTreeMap<String, NuclideProperties>,
    mass_g: f64,
    geometry: Geometry,
    activated_metal: bool,
    /// The declared external H-3: its source and the activity of each selected step in Bq.
    external_h3: Option<(&'a str, &'a BTreeMap<u64, f64>)>,
}

fn external_tritium(inputs: &SpecInputs) -> Result<Value, String> {
    let Some((source, activity)) = inputs.external_h3 else {
        return Ok(json!({"status": "not_applicable"}));
    };
    if source.trim().is_empty() {
        return Err(
            "Describe the source of the external H-3: a retention calculation or a measurement reference."
                .into(),
        );
    }
    let mut per_step = Map::new();
    for step in inputs.targets {
        let bq = activity.get(step).copied().unwrap_or(0.);
        if !bq.is_finite() || bq < 0. {
            return Err(format!(
                "The external H-3 activity at step {step} must be finite and not negative."
            ));
        }
        per_step.insert(step.to_string(), json!(bq));
    }
    Ok(json!({
        "status": "declared",
        "source": source.trim(),
        "excludes_activation": true,
        "activity_bq": per_step,
    }))
}

/// The `actinv-waste-spec-1` document for one component. `input` is the result's file name, so
/// the spec and the result classify the same way wherever they are saved together.
fn build_spec(inputs: &SpecInputs) -> Result<Value, String> {
    if inputs.targets.is_empty() {
        return Err("Select at least one step to classify.".into());
    }
    if !(inputs.mass_g.is_finite() && inputs.mass_g > 0.) {
        return Err("The problem's material mass must be positive.".into());
    }
    let (key, value, what) = match inputs.geometry {
        Geometry::Volume(volume) => ("displaced_volume_cm3", volume, "displaced volume"),
        Geometry::Density(density) => ("density_g_cm3", density, "material density"),
    };
    if !(value.is_finite() && value > 0.) {
        return Err(format!("Enter a positive {what}."));
    }
    let mut component = json!({
        "id": COMPONENT_ID,
        "mass_g": inputs.mass_g,
        "waste_type": if inputs.activated_metal { "activated_metal" } else { "general" },
        "external_tritium": external_tritium(inputs)?,
    });
    component[key] = json!(value);
    Ok(json!({
        "schema": "actinv-waste-spec-1",
        "rules": RULES,
        "input": INPUT_FILE,
        "targets": inputs.targets,
        "nuclide_properties": serde_json::to_value(inputs.properties).map_err(|e| e.to_string())?,
        "components": [component],
    }))
}

/// The spec the page's inputs describe for this result and problem.
fn classify_request(
    state: &mut WasteState,
    result: &ResultDocument,
    document: &Value,
) -> Result<Value, String> {
    let selected = state.selected(&step_list(result));
    let active = state.nuclides(result.steps(), &selected);
    if !state.properties_ready(&active, document) {
        return Err("Load the nuclide properties for the selected steps first.".into());
    }
    let rows = property_rows(&active, &state.derived);
    let properties = effective_properties(&rows, &state.overrides)?;
    let density = document["material"]["density_g_cm3"]
        .as_f64()
        .filter(|d| *d > 0.);
    let geometry = match state.basis(density) {
        Volume::Density => Geometry::Density(density.unwrap_or_default()),
        Volume::Entered => Geometry::Volume(state.volume_cm3),
    };
    build_spec(&SpecInputs {
        targets: &selected,
        properties: &properties,
        mass_g: document["material"]["mass_g"].as_f64().unwrap_or(1.),
        geometry,
        activated_metal: state.activated_metal,
        external_h3: state
            .external_h3
            .then_some((state.h3_source.as_str(), &state.h3_bq)),
    })
}

/// Read Z, half-life and alpha emission from the decay data of the problem.
fn derive(spec: Spec, active: BTreeSet<String>, decay: Value) -> Result<Output, String> {
    let prepared = PreparedRun::prepare(&spec)?;
    let derived = actinv_cli::waste_composition_verify::derive_properties(&prepared, &active)?;
    Ok(Output::Properties {
        active,
        decay,
        derived,
    })
}

/// Write the result and the spec into a private folder and run the CLI's evaluator on them. The
/// result file is what `Export JSON` writes, so the pair reproduces the classification when it
/// is saved together.
fn classify(result: Value, spec: Value) -> Result<Output, String> {
    let scratch = Scratch::create()?;
    model::write_json(&scratch.file(INPUT_FILE), &result)?;
    let spec_text = serde_json::to_string_pretty(&spec).map_err(|e| e.to_string())? + "\n";
    let spec_path = scratch.file(SPEC_FILE);
    fs::write(&spec_path, &spec_text).map_err(|e| format!("cannot write the waste spec: {e}"))?;
    let out_path = scratch.file(OUTPUT_FILE);
    let as_text = |path: &Path| {
        path.to_str()
            .map(str::to_owned)
            .ok_or_else(|| "the temporary folder is not valid UTF-8".to_owned())
    };
    let (spec_arg, out_arg) = (as_text(&spec_path)?, as_text(&out_path)?);
    actinv_cli::waste::run(&spec_arg, Some(out_arg.as_str()))?;
    let text = fs::read_to_string(&out_path)
        .map_err(|e| format!("cannot read the evaluator's result: {e}"))?;
    let view = serde_json::from_str::<Value>(&text)
        .map_err(|e| e.to_string())
        .and_then(|value| view::parse(&value));
    Ok(Output::Classified(Classified {
        scratch,
        text,
        view,
    }))
}

/// Run `work` on its own thread and wake the window when it is done.
fn start(
    ctx: &egui::Context,
    kind: JobKind,
    work: impl FnOnce() -> Result<Output, String> + Send + 'static,
) -> Result<Job, String> {
    let (tx, rx) = mpsc::channel();
    let ctx = ctx.clone();
    std::thread::Builder::new()
        .name("actinv-waste".into())
        .stack_size(model::SOLVER_STACK_BYTES)
        .spawn(move || {
            let _ = tx.send(work());
            ctx.request_repaint();
        })
        .map_err(|e| e.to_string())?;
    Ok(Job { kind, rx })
}

/// Copy a file to a name that must not exist yet.
fn copy_new(from: &Path, to: &Path) -> Result<(), String> {
    let mut source =
        fs::File::open(from).map_err(|e| format!("cannot read {}: {e}", from.display()))?;
    let mut target = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(to)
        .map_err(|e| format!("cannot create {}: {e}", to.display()))?;
    io::copy(&mut source, &mut target)
        .map(|_| ())
        .map_err(|e| format!("cannot write {}: {e}", to.display()))
}

/// Save the spec and the result it reads side by side, refusing to replace either.
fn save_pair(source: &Path, folder: &Path) -> Result<(), String> {
    for name in [SPEC_FILE, INPUT_FILE] {
        let path = folder.join(name);
        if path.exists() {
            return Err(format!(
                "{} already exists. Choose another folder so nothing is replaced.",
                path.display()
            ));
        }
    }
    for name in [SPEC_FILE, INPUT_FILE] {
        copy_new(&source.join(name), &folder.join(name))?;
    }
    Ok(())
}

impl Desktop {
    pub(super) fn waste_page(&mut self, ui: &mut egui::Ui) {
        self.poll_waste();
        heading(
            ui,
            "Waste class",
            "Classify the selected steps against the bundled 10 CFR 61.55 tables, with the evaluator `actinv waste` runs.",
        );
        self.result_bar(ui);
        if self.result.is_none() {
            self.no_result(ui, &[]);
            return;
        }
        if self.waste.job.is_some() {
            ui.ctx().request_repaint_after(Duration::from_millis(200));
        }
        let action = {
            let result = self.result.as_ref().expect("checked");
            controls(ui, result, &self.document, &mut self.waste)
        };
        match action {
            Action::None => {}
            Action::LoadProperties => self.load_properties(ui.ctx()),
            Action::Classify => self.classify_waste(ui.ctx()),
            Action::SaveSpec => self.save_waste_spec(),
            Action::SaveResult => self.save_waste_result(),
        }
        if let Some(error) = &self.waste.error {
            ui.colored_label(ui.visuals().error_fg_color, error.as_str());
        }
        match self.waste.done.as_ref().map(|done| &done.view) {
            Some(Ok(parsed)) => view::show(ui, parsed),
            Some(Err(error)) => {
                ui.colored_label(ui.visuals().error_fg_color, error.as_str());
            }
            None => {}
        }
    }

    fn waste_started(&mut self, started: Result<Job, String>) {
        match started {
            Ok(job) => {
                let message = match job.kind {
                    JobKind::Properties => "Reading nuclide properties from the decay data…",
                    JobKind::Classify => "Classifying the selected steps…",
                };
                self.waste.job = Some(job);
                self.waste.error = None;
                self.report(Ok(message.into()));
            }
            Err(error) => self.waste_failed(error),
        }
    }

    fn waste_failed(&mut self, error: String) {
        self.waste.error = Some(error.clone());
        self.report(Err(error));
    }

    fn load_properties(&mut self, ctx: &egui::Context) {
        let Some(result) = &self.result else {
            return;
        };
        let selected = self.waste.selected(&step_list(result));
        let active = self.waste.nuclides(result.steps(), &selected);
        let decay = self.document["decay"].clone();
        let started = self.validate().and_then(|spec| {
            start(ctx, JobKind::Properties, move || {
                derive(spec, active, decay)
            })
        });
        self.waste_started(started);
    }

    fn classify_waste(&mut self, ctx: &egui::Context) {
        let Some(result) = &self.result else {
            return;
        };
        let started = classify_request(&mut self.waste, result, &self.document).and_then(|spec| {
            let value = result.value.clone();
            start(ctx, JobKind::Classify, move || classify(value, spec))
        });
        self.waste_started(started);
    }

    /// Install a finished background job.
    fn poll_waste(&mut self) {
        let polled = self.waste.job.as_ref().map(|job| job.rx.try_recv());
        let outcome = match polled {
            None | Some(Err(TryRecvError::Empty)) => return,
            Some(Ok(outcome)) => outcome,
            Some(Err(TryRecvError::Disconnected)) => {
                Err("The waste evaluator stopped unexpectedly.".into())
            }
        };
        self.waste.job = None;
        match outcome {
            Ok(Output::Properties {
                active,
                decay,
                derived,
            }) => {
                let found = active
                    .iter()
                    .filter(|name| derived.keys().any(|key| canonical(key) == canonical(name)))
                    .count();
                let total = active.len();
                self.waste.derived = derived;
                self.waste.derived_for = Some(active);
                self.waste.decay = decay;
                self.waste.error = None;
                self.report(Ok(format!(
                    "Nuclide properties read for {found} of {total} active nuclides. A nuclide without a decay record leaves its class unknown unless you override it."
                )));
            }
            Ok(Output::Classified(done)) => {
                let message = match &done.view {
                    Ok(_) => "Classification complete. Save the spec and the result to reproduce it with `actinv waste`.",
                    Err(_) => "The evaluator wrote a result this page cannot read.",
                };
                self.waste.done = Some(done);
                self.waste.error = None;
                self.report(Ok(message.into()));
            }
            Err(error) => self.waste_failed(error),
        }
    }

    fn save_waste_spec(&mut self) {
        let Some(source) = self.waste.done.as_ref().map(|done| done.scratch.0.clone()) else {
            return;
        };
        let Some(folder) = self.platform.pick_folder(
            Dialog::new().title("Choose a folder for waste-spec.json and result.json"),
        ) else {
            return;
        };
        let saved = save_pair(&source, &folder);
        self.report(saved.map(|()| {
            format!(
                "Saved {SPEC_FILE} and {INPUT_FILE} in {}. Run `actinv waste {SPEC_FILE}` there.",
                folder.display()
            )
        }));
    }

    fn save_waste_result(&mut self) {
        let Some(text) = self.waste.done.as_ref().map(|done| done.text.clone()) else {
            return;
        };
        self.save_text(OUTPUT_FILE, &text);
    }
}

/// The inputs and buttons of the page. Returns what the buttons asked for.
fn controls(
    ui: &mut egui::Ui,
    result: &ResultDocument,
    document: &Value,
    state: &mut WasteState,
) -> Action {
    let steps = step_list(result);
    let selected = state.selected(&steps);
    let active = state.nuclides(result.steps(), &selected);
    let rows = property_rows(&active, &state.derived);
    let ready = !selected.is_empty() && state.properties_ready(&active, document);
    let mass_g = document["material"]["mass_g"].as_f64().unwrap_or(1.);
    let density = document["material"]["density_g_cm3"]
        .as_f64()
        .filter(|d| *d > 0.);
    let mut action = Action::None;
    component_card(ui, state, mass_g, density);
    ui.add_space(8.);
    steps_card(ui, state, &steps);
    ui.add_space(8.);
    external_card(ui, state, &selected);
    ui.add_space(8.);
    if properties_card(ui, state, &rows, ready) {
        action = Action::LoadProperties;
    }
    ui.add_space(8.);
    let idle = state.job.is_none();
    let finished = state.done.is_some();
    ui.horizontal(|ui| {
        let classify = ui.add_enabled(
            idle && ready,
            egui::Button::new(RichText::new("Classify").color(Color32::WHITE)).fill(BLUE),
        );
        if classify.clicked() {
            action = Action::Classify;
        }
        if ui
            .add_enabled(finished, egui::Button::new("Save spec…"))
            .on_hover_text("Save waste-spec.json and the result it reads, result.json, in a folder")
            .clicked()
        {
            action = Action::SaveSpec;
        }
        if ui
            .add_enabled(finished, egui::Button::new("Save result…"))
            .on_hover_text("Save the evaluator's result exactly as `actinv waste` writes it")
            .clicked()
        {
            action = Action::SaveResult;
        }
        if let Some(job) = &state.job {
            ui.spinner();
            ui.label(match job.kind {
                JobKind::Properties => "Reading decay data…",
                JobKind::Classify => "Classifying…",
            });
        }
    });
    if !ready && !selected.is_empty() {
        ui.label("Load the nuclide properties for these steps before classifying.");
    }
    ui.add_space(8.);
    action
}

fn component_card(ui: &mut egui::Ui, state: &mut WasteState, mass_g: f64, density: Option<f64>) {
    let basis = state.basis(density);
    ui.group(|ui| {
        ui.label(RichText::new("Component").strong());
        ui.label(format!(
            "One homogeneous component, the irradiated material: {mass_g} g, the material mass of the problem."
        ));
        ui.horizontal(|ui| {
            ui.label("Waste type");
            ui.selectable_value(&mut state.activated_metal, false, "General");
            ui.selectable_value(&mut state.activated_metal, true, "Activated metal");
        });
        if let Some(density) = density {
            ui.horizontal(|ui| {
                ui.selectable_value(
                    &mut state.volume,
                    Some(Volume::Density),
                    format!("Derive the volume from the material density ({density} g/cm³)"),
                );
                ui.selectable_value(
                    &mut state.volume,
                    Some(Volume::Entered),
                    "Enter the displaced volume",
                );
            });
        }
        match (basis, density) {
            (Volume::Density, Some(density)) => {
                ui.label(format!(
                    "Displaced volume = mass / density = {:.6} cm³, the conservative solid basis.",
                    mass_g / density
                ));
            }
            _ => {
                ui.horizontal(|ui| {
                    ui.label("Displaced volume (cm³)");
                    ui.add(
                        egui::DragValue::new(&mut state.volume_cm3)
                            .custom_parser(model::parse_finite)
                            .speed(0.1)
                            .range(0.0..=f64::MAX),
                    );
                });
                ui.label("The envelope of the component with major voids removed.");
            }
        }
    });
}

fn steps_card(ui: &mut egui::Ui, state: &mut WasteState, steps: &[(u64, f64)]) {
    ui.group(|ui| {
        ui.horizontal(|ui| {
            ui.label(RichText::new("Steps to classify").strong());
            if ui.small_button("Select all").clicked() {
                state.skipped.clear();
            }
        });
        egui::ScrollArea::vertical()
            .id_salt("waste-step-choice")
            .max_height(120.)
            .show(ui, |ui| {
                ui.horizontal_wrapped(|ui| {
                    for (number, t_s) in steps {
                        let mut on = !state.skipped.contains(number);
                        if ui
                            .checkbox(&mut on, format!("Step {number} · {t_s:.4e} s"))
                            .changed()
                        {
                            if on {
                                state.skipped.remove(number);
                            } else {
                                state.skipped.insert(*number);
                            }
                        }
                    }
                });
            });
    });
}

fn external_card(ui: &mut egui::Ui, state: &mut WasteState, selected: &[u64]) {
    ui.group(|ui| {
        ui.label(RichText::new("External H-3").strong());
        ui.checkbox(
            &mut state.external_h3,
            "Declare retained H-3 that the activation calculation does not model",
        );
        if !state.external_h3 {
            ui.label("Not applicable: the inventory holds the only H-3.");
            return;
        }
        ui.horizontal(|ui| {
            ui.label("Source");
            ui.add(
                egui::TextEdit::singleline(&mut state.h3_source)
                    .hint_text("retention calculation or measurement reference")
                    .desired_width(320.),
            );
        });
        ui.label("Whole-component activity in Bq at each selected step, excluding the H-3 already in the inventory.");
        egui::ScrollArea::vertical()
            .id_salt("waste-h3-steps")
            .max_height(140.)
            .show(ui, |ui| {
                for step in selected {
                    ui.horizontal(|ui| {
                        ui.label(format!("Step {step}"));
                        ui.add(
                            egui::DragValue::new(state.h3_bq.entry(*step).or_insert(0.))
                                .custom_parser(model::parse_finite)
                                .speed(1.)
                                .range(0.0..=f64::MAX),
                        );
                    });
                }
            });
    });
}

/// The properties table. True when the user asked to read them from the decay data.
fn properties_card(
    ui: &mut egui::Ui,
    state: &mut WasteState,
    rows: &[PropertyRow],
    ready: bool,
) -> bool {
    let mut load = false;
    ui.group(|ui| {
        ui.label(RichText::new("Nuclide properties").strong());
        ui.label("Z, half-life and alpha emission of every nuclide with positive activity at a selected step, read from the decay data of the problem that is open. A nuclide without a decay record stays blank and leaves its class unknown. An override is your own entry and is marked as one.");
        ui.horizontal(|ui| {
            if ui
                .add_enabled(state.job.is_none(), egui::Button::new("Load from decay data"))
                .clicked()
            {
                load = true;
            }
            ui.label(if ready {
                format!("{} active nuclides at the selected steps.", rows.len())
            } else {
                "Not loaded for these steps.".to_owned()
            });
        });
        property_table(ui, state, rows);
    });
    load
}

fn property_table(ui: &mut egui::Ui, state: &mut WasteState, rows: &[PropertyRow]) {
    let marker = accent(ui);
    let warn = ui.visuals().warn_fg_color;
    // The button of a row cannot change the map the row is drawn from; it is applied after.
    let mut change: Option<(String, Option<NuclideProperties>)> = None;
    TableBuilder::new(ui)
        .id_salt("waste-properties")
        .striped(true)
        .resizable(true)
        .column(Column::initial(90.).at_least(60.))
        .column(Column::initial(70.).at_least(50.))
        .column(Column::initial(150.).at_least(90.))
        .column(Column::initial(50.).at_least(40.))
        .column(Column::initial(120.).at_least(80.))
        .column(Column::remainder().at_least(80.))
        .max_scroll_height(260.)
        .header(24., |mut header| {
            for label in ["Nuclide", "Z", "Half-life (s)", "Alpha", "Source", ""] {
                header.col(|ui| {
                    ui.strong(label);
                });
            }
        })
        .body(|body| {
            body.rows(26., rows.len(), |mut row| {
                let entry = &rows[row.index()];
                row.col(|ui| {
                    ui.monospace(&entry.name);
                });
                if let Some(edit) = state.overrides.get_mut(&entry.key) {
                    row.col(|ui| {
                        ui.add(egui::DragValue::new(&mut edit.z).range(0..=200));
                    });
                    row.col(|ui| {
                        ui.add(
                            egui::DragValue::new(&mut edit.half_life_s)
                                .custom_parser(model::parse_finite)
                                .custom_formatter(|v, _| format!("{v:.6e}"))
                                .range(0.0..=f64::MAX),
                        );
                    });
                    row.col(|ui| {
                        ui.checkbox(&mut edit.alpha_emitting, "");
                    });
                    row.col(|ui| {
                        ui.label(RichText::new("override").strong().color(marker));
                    });
                    row.col(|ui| {
                        if ui.small_button("Clear override").clicked() {
                            change = Some((entry.key.clone(), None));
                        }
                    });
                } else {
                    let found = entry.derived.as_ref().map(|(_, p)| *p);
                    row.col(|ui| {
                        ui.monospace(found.map_or("—".to_owned(), |p| p.z.to_string()));
                    });
                    row.col(|ui| {
                        ui.monospace(
                            found.map_or("—".to_owned(), |p| format!("{:.6e}", p.half_life_s)),
                        );
                    });
                    row.col(|ui| {
                        ui.monospace(found.map_or("—", |p| {
                            if p.alpha_emitting {
                                "yes"
                            } else {
                                "no"
                            }
                        }));
                    });
                    row.col(|ui| match found {
                        Some(_) => {
                            ui.label("decay data");
                        }
                        None => {
                            ui.label(RichText::new("no decay record").color(warn));
                        }
                    });
                    row.col(|ui| {
                        if ui.small_button("Override").clicked() {
                            // Start from the decay data's value; with none, from an invalid
                            // placeholder that classifying refuses until it is filled in.
                            let start = found.unwrap_or(NuclideProperties {
                                z: 0,
                                half_life_s: 0.,
                                alpha_emitting: false,
                            });
                            change = Some((entry.key.clone(), Some(start)));
                        }
                    });
                }
            });
        });
    match change {
        Some((key, Some(properties))) => {
            state.overrides.insert(key, properties);
        }
        Some((key, None)) => {
            state.overrides.remove(&key);
        }
        None => {}
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn properties(z: i32, half_life_s: f64) -> NuclideProperties {
        NuclideProperties {
            z,
            half_life_s,
            alpha_emitting: false,
        }
    }

    fn series() -> Vec<Value> {
        vec![
            json!({"step": 1, "t_s": 0.0, "activity_Bq_per_g": {"C14": 2.0, "Nb94": 0.0, "Fe55": 1.0}}),
            json!({"step": 2, "t_s": 10.0, "activity_Bq_per_g": {"C14": 1.0, "Co60": 5.0}}),
            json!({"step": 3, "t_s": 20.0, "activity_Bq_per_g": {"Ni63": 3.0}}),
        ]
    }

    fn inputs<'a>(
        targets: &'a [u64],
        props: &'a BTreeMap<String, NuclideProperties>,
    ) -> SpecInputs<'a> {
        SpecInputs {
            targets,
            properties: props,
            mass_g: 2.5,
            geometry: Geometry::Volume(1.0),
            activated_metal: true,
            external_h3: None,
        }
    }

    #[test]
    fn declared_external_h3_needs_h3_properties_even_when_the_run_made_none() {
        let mut state = WasteState::default();
        assert!(!state.nuclides(&series(), &[1, 2, 3]).contains("H3"));
        state.external_h3 = true;
        let wanted = state.nuclides(&series(), &[1, 2, 3]);
        assert!(wanted.contains("H3"));
        assert_eq!(
            wanted.len(),
            active_nuclides(&series(), &[1, 2, 3]).len() + 1
        );
        // a run that already made H-3 lists it once, under its own name
        let mut steps = series();
        steps[0]["activity_Bq_per_g"]["H3"] = json!(1.0);
        assert_eq!(
            state
                .nuclides(&steps, &[1])
                .iter()
                .filter(|n| canonical(n) == "H3")
                .count(),
            1
        );
    }

    #[test]
    fn active_nuclides_are_those_with_positive_activity_at_a_selected_step() {
        let all = active_nuclides(&series(), &[1, 2, 3]);
        assert_eq!(
            all.into_iter().collect::<Vec<_>>(),
            ["C14", "Co60", "Fe55", "Ni63"]
        );
        // Nb94 has zero activity; step 3 is not selected
        let some = active_nuclides(&series(), &[1, 2]);
        assert_eq!(
            some.into_iter().collect::<Vec<_>>(),
            ["C14", "Co60", "Fe55"]
        );
        assert!(active_nuclides(&series(), &[]).is_empty());
    }

    #[test]
    fn steps_are_numbered_from_the_result_and_default_to_all_selected() {
        let result = ResultDocument {
            value: json!({"steps": [{"step": 4, "t_s": 1.0}, {"t_s": 2.0}]}),
            label: String::new(),
        };
        let steps = step_list(&result);
        assert_eq!(steps, [(4, 1.0), (2, 2.0)]);
        let mut state = WasteState::default();
        assert_eq!(state.selected(&steps), [4, 2]);
        state.skipped.insert(4);
        assert_eq!(state.selected(&steps), [2]);
    }

    #[test]
    fn the_spec_has_the_documented_shape() {
        let mut props = BTreeMap::new();
        props.insert("Nb94".to_owned(), properties(41, 6.3e11));
        let spec = build_spec(&inputs(&[2, 3], &props)).unwrap();
        assert_eq!(
            spec,
            json!({
                "schema": "actinv-waste-spec-1",
                "rules": "us-nrc-10cfr61.55-v1",
                "input": "result.json",
                "targets": [2, 3],
                "nuclide_properties": {
                    "Nb94": {"z": 41, "half_life_s": 6.3e11, "alpha_emitting": false}
                },
                "components": [{
                    "id": "component",
                    "mass_g": 2.5,
                    "displaced_volume_cm3": 1.0,
                    "waste_type": "activated_metal",
                    "external_tritium": {"status": "not_applicable"}
                }]
            })
        );
    }

    #[test]
    fn a_declared_density_replaces_the_volume_and_general_waste_is_the_default() {
        let props = BTreeMap::new();
        let mut request = inputs(&[1], &props);
        request.geometry = Geometry::Density(7.874);
        request.activated_metal = false;
        let spec = build_spec(&request).unwrap();
        let component = &spec["components"][0];
        assert_eq!(component["density_g_cm3"], 7.874);
        assert!(component.get("displaced_volume_cm3").is_none());
        assert_eq!(component["waste_type"], "general");
    }

    #[test]
    fn declared_h3_lists_every_selected_step_and_needs_a_source() {
        let props = BTreeMap::new();
        let activity = BTreeMap::from([(2, 1.2e5), (3, 1.1e5), (9, 7.0)]);
        let mut request = inputs(&[2, 3], &props);
        request.external_h3 = Some(("  retention estimate ", &activity));
        let spec = build_spec(&request).unwrap();
        assert_eq!(
            spec["components"][0]["external_tritium"],
            json!({
                "status": "declared",
                "source": "retention estimate",
                "excludes_activation": true,
                "activity_bq": {"2": 1.2e5, "3": 1.1e5}
            })
        );
        request.external_h3 = Some(("   ", &activity));
        assert!(build_spec(&request).unwrap_err().contains("source"));
        let negative = BTreeMap::from([(2, -1.0), (3, 0.0)]);
        request.external_h3 = Some(("estimate", &negative));
        assert!(build_spec(&request).unwrap_err().contains("step 2"));
        // a step with no entry is declared as zero, the value the page shows for it
        let partial = BTreeMap::from([(2, 5.0)]);
        request.external_h3 = Some(("estimate", &partial));
        let spec = build_spec(&request).unwrap();
        assert_eq!(
            spec["components"][0]["external_tritium"]["activity_bq"]["3"],
            0.0
        );
    }

    #[test]
    fn missing_inputs_are_refused_in_words() {
        let props = BTreeMap::new();
        assert!(build_spec(&inputs(&[], &props))
            .unwrap_err()
            .contains("at least one step"));
        let mut request = inputs(&[1], &props);
        request.geometry = Geometry::Volume(0.0);
        assert!(build_spec(&request)
            .unwrap_err()
            .contains("displaced volume"));
        request.geometry = Geometry::Density(f64::NAN);
        assert!(build_spec(&request)
            .unwrap_err()
            .contains("material density"));
        request.geometry = Geometry::Volume(1.0);
        request.mass_g = 0.0;
        assert!(build_spec(&request).unwrap_err().contains("mass"));
    }

    #[test]
    fn properties_join_the_active_nuclides_by_normalized_key() {
        let active = BTreeSet::from(["H-3".to_owned(), "Fe55".to_owned(), "Zz99".to_owned()]);
        let derived = BTreeMap::from([
            ("H3".to_owned(), properties(1, 3.887e8)),
            ("Fe55".to_owned(), properties(26, 8.6e7)),
        ]);
        let rows = property_rows(&active, &derived);
        let by_name: BTreeMap<_, _> = rows.iter().map(|r| (r.name.as_str(), r)).collect();
        let h3 = by_name["H-3"];
        assert_eq!(h3.key, canonical("H3"));
        assert_eq!(h3.derived.as_ref().map(|(k, _)| k.as_str()), Some("H3"));
        assert!(by_name["Zz99"].derived.is_none());
    }

    #[test]
    fn an_override_replaces_the_decay_value_under_one_key_and_blanks_stay_absent() {
        let active = BTreeSet::from(["Fe55".to_owned(), "Zz99".to_owned(), "Co60".to_owned()]);
        let derived = BTreeMap::from([
            ("Fe55".to_owned(), properties(26, 8.6e7)),
            ("Co60".to_owned(), properties(27, 1.66e8)),
        ]);
        let rows = property_rows(&active, &derived);
        let overrides = BTreeMap::from([(canonical("Fe55"), properties(26, 9.9e7))]);
        let effective = effective_properties(&rows, &overrides).unwrap();
        assert_eq!(
            effective.len(),
            2,
            "Zz99 has neither a record nor an override"
        );
        assert_eq!(effective[&canonical("Fe55")].half_life_s, 9.9e7);
        assert_eq!(effective["Co60"].half_life_s, 1.66e8);
        assert!(!effective.contains_key("Zz99"));
        // an entry for a nuclide the decay data lacks fills the blank
        let overrides = BTreeMap::from([(canonical("Zz99"), properties(30, 1.0e5))]);
        let effective = effective_properties(&rows, &overrides).unwrap();
        assert_eq!(effective.len(), 3);
    }

    #[test]
    fn an_unfilled_override_is_refused() {
        let active = BTreeSet::from(["Zz99".to_owned()]);
        let rows = property_rows(&active, &BTreeMap::new());
        for bad in [
            properties(0, 1.0),
            properties(30, 0.0),
            properties(30, f64::NAN),
        ] {
            let overrides = BTreeMap::from([(canonical("Zz99"), bad)]);
            let error = effective_properties(&rows, &overrides).unwrap_err();
            assert!(
                error.contains("Zz99") && error.contains("override"),
                "{error}"
            );
        }
    }

    #[test]
    fn classifying_waits_for_properties_of_exactly_these_nuclides() {
        let document = json!({"decay": {"primary": "a.dat"}});
        let active = BTreeSet::from(["C14".to_owned()]);
        let mut state = WasteState::default();
        assert!(!state.properties_ready(&active, &document));
        state.derived_for = Some(active.clone());
        state.decay = document["decay"].clone();
        assert!(state.properties_ready(&active, &document));
        // another selection, or another decay file, is not covered
        let other = BTreeSet::from(["C14".to_owned(), "Fe55".to_owned()]);
        assert!(!state.properties_ready(&other, &document));
        let moved = json!({"decay": {"primary": "b.dat"}});
        assert!(!state.properties_ready(&active, &moved));
        // entering every nuclide by hand is also enough, and so is nothing to enter
        let mut by_hand = WasteState::default();
        by_hand
            .overrides
            .insert(canonical("C14"), properties(6, 1.8e11));
        assert!(by_hand.properties_ready(&active, &document));
        assert!(WasteState::default().properties_ready(&BTreeSet::new(), &document));
    }

    #[test]
    fn density_is_offered_only_when_the_problem_declares_one() {
        let mut state = WasteState::default();
        assert!(state.basis(None) == Volume::Entered);
        let mut state = WasteState::default();
        assert!(state.basis(Some(7.8)) == Volume::Density);
        state.volume = Some(Volume::Entered);
        assert!(state.basis(Some(7.8)) == Volume::Entered);
        assert!(state.basis(None) == Volume::Entered);
    }

    #[test]
    fn a_request_needs_loaded_properties_and_builds_from_the_page_inputs() {
        let result = ResultDocument {
            value: json!({"steps": series()}),
            label: String::new(),
        };
        let document = json!({
            "decay": {"primary": "d.dat"},
            "material": {"mass_g": 4.0, "density_g_cm3": 8.0, "composition": {"FE": 100.0}}
        });
        let mut state = WasteState::default();
        assert!(classify_request(&mut state, &result, &document)
            .unwrap_err()
            .contains("Load the nuclide properties"));
        state.skipped.insert(3);
        let active = active_nuclides(result.steps(), &[1, 2]);
        state.derived = BTreeMap::from([
            ("C14".to_owned(), properties(6, 1.8e11)),
            ("Fe55".to_owned(), properties(26, 8.6e7)),
        ]);
        state.derived_for = Some(active);
        state.decay = document["decay"].clone();
        let spec = classify_request(&mut state, &result, &document).unwrap();
        assert_eq!(spec["targets"], json!([1, 2]));
        assert_eq!(spec["components"][0]["mass_g"], 4.0);
        // the density is declared, so the spec carries it and the volume is the CLI's to derive
        assert_eq!(spec["components"][0]["density_g_cm3"], 8.0);
        let names: Vec<_> = spec["nuclide_properties"]
            .as_object()
            .unwrap()
            .keys()
            .collect();
        assert_eq!(names, ["C14", "Fe55"], "Co60 has no decay record");
    }

    fn native_run() -> Value {
        json!({
            "entry_point": "desktop",
            "mode": "coupled",
            "steps": [
                {"step": 1, "t_s": 0.0, "activity_Bq_per_g": {"C14": 4.0e6}},
                {"step": 2, "t_s": 1.0e9, "activity_Bq_per_g": {"C14": 3.0e6}}
            ]
        })
    }

    fn c14_spec() -> Value {
        let props = BTreeMap::from([("C14".to_owned(), properties(6, 1.808e11))]);
        build_spec(&SpecInputs {
            targets: &[1, 2],
            properties: &props,
            mass_g: 2.0,
            geometry: Geometry::Volume(1.5),
            activated_metal: false,
            external_h3: None,
        })
        .unwrap()
    }

    fn finished(output: Output) -> Classified {
        match output {
            Output::Classified(done) => done,
            Output::Properties { .. } => panic!("a classification was expected"),
        }
    }

    #[test]
    fn the_evaluator_result_is_the_exact_text_it_wrote_and_parses_for_display() {
        let done = finished(classify(native_run(), c14_spec()).unwrap());
        let on_disk = fs::read_to_string(done.scratch.file(OUTPUT_FILE)).unwrap();
        assert_eq!(done.text, on_disk);
        let value: Value = serde_json::from_str(&done.text).unwrap();
        assert_eq!(value["schema"], "actinv-waste-result-1");
        assert_eq!(value["input"], "result.json");
        assert!(done.view.is_ok(), "{:?}", done.view);
    }

    #[test]
    fn the_saved_pair_reproduces_the_result_byte_for_byte() {
        let done = finished(classify(native_run(), c14_spec()).unwrap());
        let folder = Scratch::create().unwrap();
        save_pair(&done.scratch.0, &folder.0).unwrap();
        let again = folder.file("again.json");
        actinv_cli::waste::run(
            folder.file(SPEC_FILE).to_str().unwrap(),
            Some(again.to_str().unwrap()),
        )
        .unwrap();
        assert_eq!(fs::read_to_string(again).unwrap(), done.text);
        // neither saved file may be replaced by a second save
        let error = save_pair(&done.scratch.0, &folder.0).unwrap_err();
        assert!(error.contains("already exists"), "{error}");
    }

    #[test]
    fn dropping_a_classification_removes_its_private_folder() {
        let done = finished(classify(native_run(), c14_spec()).unwrap());
        let path = done.scratch.0.clone();
        assert!(path.is_dir());
        drop(done);
        assert!(!path.exists());
    }

    #[test]
    fn evaluator_errors_come_back_as_text() {
        // a result that is not a native run (the teaching example has no entry point)
        let teaching = model::tutorial_result().value;
        let error = classify(teaching, c14_spec()).err().expect("refused");
        assert!(error.contains("not an ACTINV RunResult"), "{error}");
        // a target step the result does not have
        let mut spec = c14_spec();
        spec["targets"] = json!([7]);
        let error = classify(native_run(), spec).err().expect("refused");
        assert!(error.contains("no selected step 7"), "{error}");
    }

    #[test]
    fn unknown_coverage_is_reported_when_a_nuclide_has_no_properties() {
        let mut spec = c14_spec();
        spec["nuclide_properties"] = json!({});
        let done = finished(classify(native_run(), spec).unwrap());
        let value: Value = serde_json::from_str(&done.text).unwrap();
        let target = &value["components"][0]["targets"][0];
        assert_eq!(target["class"], "unknown");
        assert_eq!(target["coverage"], "incomplete");
    }

    /// Answers the platform's dialogs in order from a fixed list.
    struct Queued(std::cell::RefCell<std::collections::VecDeque<PathBuf>>);

    impl crate::platform::Platform for Queued {
        fn pick_file(&self, _: crate::platform::Dialog<'_>) -> Option<PathBuf> {
            self.0.borrow_mut().pop_front()
        }
        fn pick_folder(&self, _: crate::platform::Dialog<'_>) -> Option<PathBuf> {
            self.0.borrow_mut().pop_front()
        }
        fn save_file(&self, _: crate::platform::Dialog<'_>) -> Option<PathBuf> {
            self.0.borrow_mut().pop_front()
        }
    }

    fn wait_for_waste_job(app: &mut Desktop) {
        let deadline = std::time::Instant::now() + Duration::from_secs(900);
        while app.waste.job.is_some() {
            assert!(
                std::time::Instant::now() < deadline,
                "the waste job did not finish"
            );
            std::thread::sleep(Duration::from_millis(20));
            app.poll_waste();
        }
        assert!(app.waste.error.is_none(), "{:?}", app.waste.error);
    }

    /// P144 V1 driver. Opens the case's problem and result through the application's own Open
    /// actions, sets the page inputs the case declares, loads the properties, classifies, and saves
    /// the spec pair and the result through the page's save actions into the case's folder.
    #[test]
    #[ignore = "P144 V1 driver; needs ACTINV_P144_V1_CASE and the nuclear data the problem names"]
    fn p144_v1_drive_the_waste_page() {
        let case_path = std::env::var("ACTINV_P144_V1_CASE").expect("ACTINV_P144_V1_CASE");
        let case: Value = serde_json::from_str(&fs::read_to_string(case_path).unwrap()).unwrap();
        let path = |key: &str| PathBuf::from(case[key].as_str().expect(key));
        let out = path("out_dir");
        let ctx = egui::Context::default();
        let mut app = Desktop::from_context(&ctx);
        app.platform = Box::new(Queued(std::cell::RefCell::new(
            [
                path("problem"),
                path("result"),
                out.clone(),
                out.join(OUTPUT_FILE),
            ]
            .into(),
        )));
        app.perform(crate::app::Pending::Open);
        assert!(!app.error, "{}", app.status);
        app.open_result(&ctx, false);
        // As `logic` does each frame: install the result the loading thread parsed.
        let (compare, rx) = app.result_load.take().expect("a result load started");
        let loaded = rx
            .recv_timeout(Duration::from_secs(900))
            .expect("the result loaded");
        app.install_result(loaded, compare);
        assert!(app.result.is_some() && !app.error, "{}", app.status);
        // The input base folder a user sets in Overview & data when the data sit elsewhere.
        if let Some(base) = case["base_dir"].as_str() {
            app.base = PathBuf::from(base);
        }
        app.page = crate::nav::Page::Waste;
        app.waste.activated_metal = case["activated_metal"].as_bool().unwrap();
        if let Some(volume) = case["volume_cm3"].as_f64() {
            app.waste.volume = Some(Volume::Entered);
            app.waste.volume_cm3 = volume;
        }
        if let Some(h3) = case["external_h3"].as_object() {
            app.waste.external_h3 = true;
            app.waste.h3_source = h3["source"].as_str().unwrap().to_owned();
            let steps = step_list(app.result.as_ref().unwrap());
            let bq = h3["bq_every_step"].as_f64().unwrap();
            app.waste.h3_bq = steps.iter().map(|(number, _)| (*number, bq)).collect();
        }
        app.load_properties(&ctx);
        wait_for_waste_job(&mut app);
        app.classify_waste(&ctx);
        wait_for_waste_job(&mut app);
        assert!(matches!(&app.waste.done, Some(done) if done.view.is_ok()));
        app.save_waste_spec();
        assert!(!app.error, "{}", app.status);
        app.save_waste_result();
        assert!(!app.error, "{}", app.status);
        for name in [SPEC_FILE, INPUT_FILE, OUTPUT_FILE] {
            assert!(out.join(name).is_file(), "{name} was not saved");
        }
    }
}
