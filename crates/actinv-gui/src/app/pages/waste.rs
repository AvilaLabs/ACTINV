//! Waste class: the page opens on the waste question being asked. The class of this result builds
//! an ordinary `actinv-waste-spec-1` document from the run (with the draft intrusion screen when
//! asked) and hands it to the CLI's own evaluator, in process; a container and activity ranges do
//! the same with `actinv waste package` and `actinv waste bounds`; how sure the class is runs
//! `actinv waste uncertainty` in the isolated worker, since it solves the problem once per sample.
//! What each shows is read from the result its command writes.
use crate::{
    app::{heading, Desktop, JobKind as AppJobKind},
    jobs::Command,
    model::{self, ResultDocument},
    platform::Dialog,
    views::{
        certify, waste as view,
        waste_bounds::{self, BoundsAction},
        waste_forms::{self as forms, Question},
        waste_package::{self, PackageAction, Source},
        waste_uncertainty,
    },
};
use actinv_core::{
    run::PreparedRun,
    spec::Spec,
    waste::{normalize_nuclide_key, NuclideProperties},
};
use eframe::egui;
use serde_json::{json, Map, Value};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs, io,
    path::{Path, PathBuf},
    sync::mpsc::{self, Receiver, TryRecvError},
    time::Duration,
};

const RULES: &str = forms::RULES;
const COMPONENT_ID: &str = "component";
/// The files one classification leaves behind. The spec names the input by this file name, so
/// the pair classifies the same way wherever the two are saved together.
const SPEC_FILE: &str = "waste-spec.json";
const INTRUSION_FILE: &str = "intrusion-spec.json";
const PACKAGE_FILE: &str = "package-spec.json";
const BOUNDS_FILE: &str = "bounds-spec.json";
const INPUT_FILE: &str = "result.json";
const OUTPUT_FILE: &str = "waste-result.json";
const DRAFT_RULES: &str = "us-nrc-nureg1556-v22-draft-2026-02-v1";

/// The waste forms of the draft intrusion table that are not metal, with the words shown.
const GENERAL_FORMS: [(&str, &str); 8] = [
    ("cement_or_polymer", "Cement or polymer"),
    ("soil", "Soil"),
    ("equipment", "Equipment"),
    ("rubble", "Rubble"),
    ("resin", "Resin"),
    ("ash", "Ash"),
    ("calcined", "Calcined"),
    ("other", "Other"),
];

#[derive(Clone, Copy, PartialEq, Eq)]
enum Volume {
    Entered,
    Density,
}

#[derive(Clone, Copy)]
enum JobKind {
    Properties,
    Classify,
    Package,
    Bounds,
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
    Finished(Question, Finished),
}

/// A finished classification: the private folder holding the spec and the result it read, the
/// exact text the evaluator wrote, and that text parsed for display.
struct Classified {
    scratch: Scratch,
    spec_file: &'static str,
    text: String,
    value: Value,
    view: Result<view::WasteView, String>,
}

/// A finished container or ranges result: its folder, spec file, text and value.
struct Finished {
    scratch: Scratch,
    spec_file: &'static str,
    text: String,
    value: Value,
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

/// The draft intrusion screen the class of this result may add.
#[derive(Default)]
struct Intrusion {
    on: bool,
    form: usize,
    incomplete: bool,
    reasons: String,
}

/// What the page remembers between frames.
#[derive(Default)]
pub(in crate::app) struct WasteState {
    question: Question,
    activated_metal: bool,
    volume: Option<Volume>,
    volume_cm3: f64,
    external_h3: bool,
    h3_source: String,
    h3_bq: BTreeMap<u64, f64>,
    /// Steps taken out of the selection; every step is selected until one is listed here.
    skipped: BTreeSet<u64>,
    /// Nuclide properties read from the decay data or entered, shared by every question.
    properties: forms::Properties,
    intrusion: Intrusion,
    package: waste_package::PackageState,
    bounds: waste_bounds::BoundsState,
    sure: waste_uncertainty::SureState,
    job: Option<Job>,
    done: Option<Classified>,
    /// The result the classification was made for, so a later result does not inherit it.
    classified_for: Option<String>,
    package_done: Option<Finished>,
    bounds_done: Option<Finished>,
    /// The how-sure result: where the command wrote it and what it holds.
    sure_done: Option<(PathBuf, Value)>,
    /// The problem file the running how-sure job reads.
    sure_problem: Option<Scratch>,
    error: Option<String>,
}

/// What a frame asks the page to do once drawing is over.
enum Action {
    None,
    LoadProperties(BTreeSet<String>),
    Classify,
    Package,
    PickFile(usize),
    Bounds,
    Fill {
        time: usize,
        step: usize,
        spread_pct: f64,
    },
    Sure,
    SaveSpec(Question),
    SaveResult(Question),
}

/// Which result a classification belongs to: its label, title and step times.
fn fingerprint(result: &crate::model::ResultDocument) -> String {
    let times: Vec<String> = result
        .steps()
        .iter()
        .map(|s| s["t_s"].to_string())
        .collect();
    format!(
        "{}|{}|{}",
        result.label,
        result.value["spec_title"],
        times.join(",")
    )
}

impl WasteState {
    /// The classification of `result`, when one was made for it on this page.
    pub(in crate::app) fn view_for(
        &self,
        result: &crate::model::ResultDocument,
    ) -> Option<&view::WasteView> {
        if self.classified_for.as_deref() != Some(fingerprint(result).as_str()) {
            return None;
        }
        self.done.as_ref().and_then(|done| done.view.as_ref().ok())
    }

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

    /// True when classifying would not leave a nuclide's properties out by accident: every one
    /// was looked up in the decay data now in the problem, or entered by hand.
    fn properties_ready(&self, active: &BTreeSet<String>, document: &Value) -> bool {
        self.properties.ready(active, &document["decay"])
    }

    /// The nuclides a container needs properties for: those active in the open result at the
    /// target steps (when an item reads it), in a saved result an item reads, or declared.
    fn package_nuclides(
        &self,
        result: Option<&ResultDocument>,
        targets: &[u64],
    ) -> BTreeSet<String> {
        let mut active = self.package.declared_nuclides(targets);
        if let Some(result) = result.filter(|_| self.package.uses_this_result()) {
            active.extend(active_nuclides(result.steps(), targets));
        }
        for item in &self.package.items {
            if item.source == Source::File {
                active.extend(item.file_nuclides.iter().cloned());
            }
        }
        active
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

/// The steps of the result labelled by their time, as the forms list them.
fn labelled_steps(result: &ResultDocument) -> Vec<(u64, String)> {
    step_list(result)
        .into_iter()
        .map(|(n, t)| (n, model::readable_time(t)))
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

enum Geometry {
    Volume(f64),
    Density(f64),
}

struct SpecInputs<'a> {
    targets: &'a [u64],
    properties: &'a Value,
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
        "nuclide_properties": inputs.properties,
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
    let properties = state.properties.map(&active)?;
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

/// The draft intrusion screen around a classification spec, as `actinv waste intrusion-screen`
/// reads it.
fn intrusion_spec(
    intrusion: &Intrusion,
    activated_metal: bool,
    waste_spec: Value,
) -> Result<Value, String> {
    let reasons: Vec<&str> = intrusion
        .reasons
        .lines()
        .map(str::trim)
        .filter(|l| !l.is_empty())
        .collect();
    if intrusion.incomplete && reasons.is_empty() {
        return Err(
            "Say what the inventory leaves out, one reason per line, for the intrusion screen."
                .into(),
        );
    }
    let form = if activated_metal {
        "metal"
    } else {
        GENERAL_FORMS[intrusion.form.min(GENERAL_FORMS.len() - 1)].0
    };
    Ok(json!({
        "schema": "actinv-waste-intrusion-screen-spec-1",
        "draft_rules": DRAFT_RULES,
        "package_basis": "single_component_container",
        "waste_form": form,
        "inventory_coverage": if intrusion.incomplete { "incomplete" } else { "complete" },
        "unbounded_inventory_reasons": if intrusion.incomplete { reasons } else { Vec::new() },
        "waste_spec": waste_spec,
    }))
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

type Runner = fn(&str, Option<&str>) -> Result<Value, String>;

/// Write `spec` (and `result`, when given, as result.json) into a private folder and run a CLI
/// evaluator on them. Returns the folder and the exact text the evaluator wrote.
fn run_in_scratch(
    spec_file: &str,
    spec: &Value,
    result: Option<&Value>,
    runner: Runner,
) -> Result<(Scratch, String, Value), String> {
    let scratch = Scratch::create()?;
    if let Some(result) = result {
        model::write_json(&scratch.file(INPUT_FILE), result)?;
    }
    let spec_text = serde_json::to_string_pretty(spec).map_err(|e| e.to_string())? + "\n";
    let spec_path = scratch.file(spec_file);
    fs::write(&spec_path, &spec_text).map_err(|e| format!("cannot write the spec: {e}"))?;
    let out_path = scratch.file(OUTPUT_FILE);
    let as_text = |path: &Path| {
        path.to_str()
            .map(str::to_owned)
            .ok_or_else(|| "the temporary folder is not valid UTF-8".to_owned())
    };
    let (spec_arg, out_arg) = (as_text(&spec_path)?, as_text(&out_path)?);
    runner(&spec_arg, Some(out_arg.as_str()))?;
    let text = fs::read_to_string(&out_path)
        .map_err(|e| format!("cannot read the evaluator's result: {e}"))?;
    let value = serde_json::from_str::<Value>(&text).map_err(|e| e.to_string())?;
    Ok((scratch, text, value))
}

/// Classify the result with the CLI's evaluator, with the draft intrusion screen around it when
/// `intrusion` is given. The result file is what `Export JSON` writes, so the pair reproduces the
/// classification when it is saved together.
fn classify(result: Value, spec: Value, intrusion: Option<Value>) -> Result<Output, String> {
    let (spec_file, spec, runner): (_, _, Runner) = match intrusion {
        Some(screen) => (INTRUSION_FILE, screen, actinv_cli::waste_intrusion::run),
        None => (SPEC_FILE, spec, actinv_cli::waste::run),
    };
    let (scratch, text, value) = run_in_scratch(spec_file, &spec, Some(&result), runner)?;
    let view = view::parse(&value);
    Ok(Output::Classified(Classified {
        scratch,
        spec_file,
        text,
        value,
        view,
    }))
}

fn package(result: Option<Value>, spec: Value) -> Result<Output, String> {
    let (scratch, text, value) = run_in_scratch(
        PACKAGE_FILE,
        &spec,
        result.as_ref(),
        actinv_cli::waste_package::run,
    )?;
    Ok(Output::Finished(
        Question::Container,
        Finished {
            scratch,
            spec_file: PACKAGE_FILE,
            text,
            value,
        },
    ))
}

fn bounds(spec: Value) -> Result<Output, String> {
    let (scratch, text, value) =
        run_in_scratch(BOUNDS_FILE, &spec, None, actinv_cli::waste_bounds::run)?;
    Ok(Output::Finished(
        Question::Ranges,
        Finished {
            scratch,
            spec_file: BOUNDS_FILE,
            text,
            value,
        },
    ))
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

/// Save the spec and the result it reads (when there is one) side by side, refusing to replace
/// either.
fn save_pair(source: &Path, folder: &Path, spec_file: &str) -> Result<Vec<&'static str>, String> {
    let names: Vec<&str> = [spec_file, INPUT_FILE]
        .into_iter()
        .filter(|name| source.join(name).is_file())
        .collect();
    for name in &names {
        let path = folder.join(name);
        if path.exists() {
            return Err(format!(
                "{} already exists. Choose another folder so nothing is replaced.",
                path.display()
            ));
        }
    }
    for name in &names {
        copy_new(&source.join(name), &folder.join(name))?;
    }
    Ok([
        SPEC_FILE,
        INTRUSION_FILE,
        PACKAGE_FILE,
        BOUNDS_FILE,
        INPUT_FILE,
    ]
    .into_iter()
    .filter(|n| names.contains(n))
    .collect())
}

/// The command a saved spec runs with.
fn command_for(spec_file: &str) -> &'static str {
    match spec_file {
        INTRUSION_FILE => "actinv waste intrusion-screen",
        PACKAGE_FILE => "actinv waste package",
        BOUNDS_FILE => "actinv waste bounds",
        _ => "actinv waste",
    }
}

/// The nuclides with positive activity at any step of a saved result.
fn file_nuclides(path: &Path) -> Result<BTreeSet<String>, String> {
    let text = fs::read_to_string(path).map_err(|e| format!("{}: {e}", path.display()))?;
    let value: Value =
        serde_json::from_str(&text).map_err(|e| format!("{}: {e}", path.display()))?;
    let steps = value["steps"]
        .as_array()
        .ok_or_else(|| format!("{} is not an ACTINV run result.", path.display()))?;
    let all: Vec<u64> = (1..=steps.len() as u64).collect();
    Ok(active_nuclides(steps, &all))
}

impl Desktop {
    pub(super) fn waste_page(&mut self, ui: &mut egui::Ui) {
        self.poll_waste();
        self.waste_capture();
        heading(
            ui,
            "Waste class",
            "Which waste question do you have? Each is answered with the 10 CFR 61.55 tables, by the same evaluator the `actinv waste` commands run.",
        );
        forms::question_tiles(ui, &mut self.waste.question);
        if self.waste.job.is_some() {
            ui.ctx().request_repaint_after(Duration::from_millis(200));
        }
        let action = match self.waste.question {
            Question::Class => self.class_question(ui),
            Question::Container => self.container_question(ui),
            Question::Ranges => self.ranges_question(ui),
            Question::HowSure => self.sure_question(ui),
        };
        match action {
            Action::None => {}
            Action::LoadProperties(active) => self.load_properties(ui.ctx(), active),
            Action::Classify => self.classify_waste(ui.ctx()),
            Action::Package => self.classify_package(ui.ctx()),
            Action::PickFile(i) => self.pick_package_file(i),
            Action::Bounds => self.classify_bounds(ui.ctx()),
            Action::Fill {
                time,
                step,
                spread_pct,
            } => self.fill_bounds(time, step, spread_pct),
            Action::Sure => self.start_sure(),
            Action::SaveSpec(question) => self.save_waste_spec(question),
            Action::SaveResult(question) => self.save_waste_result(question),
        }
        if let Some(error) = &self.waste.error {
            ui.colored_label(ui.visuals().error_fg_color, error.as_str());
        }
        self.waste_answer(ui);
    }

    /// Screenshots: open the question and result the capture names.
    fn waste_capture(&mut self) {
        if self.capture.is_none() {
            return;
        }
        let Some(question) = std::env::var("ACTINV_GUI_CAPTURE_WASTE").ok() else {
            return;
        };
        self.waste.question = match question.as_str() {
            "container" => Question::Container,
            "ranges" => Question::Ranges,
            "sure" => Question::HowSure,
            _ => Question::Class,
        };
        let Some(path) = std::env::var_os("ACTINV_GUI_CAPTURE_WASTE_RESULT") else {
            return;
        };
        if self.waste.package_done.is_some()
            || self.waste.bounds_done.is_some()
            || self.waste.sure_done.is_some()
        {
            return;
        }
        let Ok(text) = fs::read_to_string(&path) else {
            return;
        };
        let Ok(value) = serde_json::from_str::<Value>(&text) else {
            return;
        };
        let Ok(scratch) = Scratch::create() else {
            return;
        };
        match self.waste.question {
            Question::Container => {
                self.waste.package_done = Some(Finished {
                    scratch,
                    spec_file: PACKAGE_FILE,
                    text,
                    value,
                });
            }
            Question::Ranges => {
                self.waste.bounds_done = Some(Finished {
                    scratch,
                    spec_file: BOUNDS_FILE,
                    text,
                    value,
                });
            }
            Question::HowSure => self.waste.sure_done = Some((PathBuf::from(path), value)),
            Question::Class => {}
        }
    }

    fn class_question(&mut self, ui: &mut egui::Ui) -> Action {
        if self.result.is_none() {
            forms::section(ui, "This result", "");
            self.no_result(ui, &[]);
            return Action::None;
        }
        self.result_bar(ui);
        let result = self.result.as_ref().expect("checked");
        controls(ui, result, &self.document, &mut self.waste)
    }

    fn container_question(&mut self, ui: &mut egui::Ui) -> Action {
        let steps = self.result.as_ref().map(labelled_steps).unwrap_or_default();
        let mut action = match waste_package::show_form(ui, &mut self.waste.package, &steps, true) {
            PackageAction::PickFile(i) => Action::PickFile(i),
            _ => Action::None,
        };
        let declared: Vec<(u64, String)> = (1..=self.waste.package.declared_steps)
            .map(|n| (n, String::new()))
            .collect();
        let targets = self
            .waste
            .package
            .targets(if steps.is_empty() { &declared } else { &steps });
        let active = self.waste.package_nuclides(self.result.as_ref(), &targets);
        if let Some(next) = self.properties_section(ui, "package", &active) {
            action = next;
        }
        let ready = !active.is_empty() && self.waste.properties_ready(&active, &self.document);
        if let Some(next) = run_row(
            ui,
            &self.waste,
            "Classify the container",
            ready,
            self.waste.package_done.is_some(),
            Question::Container,
        ) {
            action = match next {
                Action::Classify => Action::Package,
                other => other,
            };
        }
        action
    }

    fn ranges_question(&mut self, ui: &mut egui::Ui) -> Action {
        if self.waste.bounds.mass_g.is_none() {
            self.waste.bounds.mass_g = self.document["material"]["mass_g"].as_f64();
            let density = self.document["material"]["density_g_cm3"].as_f64();
            self.waste.bounds.component.density_g_cm3 = density.unwrap_or(0.);
            self.waste.bounds.component.use_density = density.is_some();
        }
        let steps = self.result.as_ref().map(labelled_steps).unwrap_or_default();
        let mut action = match waste_bounds::show_form(ui, &mut self.waste.bounds, &steps) {
            BoundsAction::Fill {
                time,
                step,
                spread_pct,
            } => Action::Fill {
                time,
                step,
                spread_pct,
            },
            _ => Action::None,
        };
        let active = self.waste.bounds.nuclides();
        if let Some(next) = self.properties_section(ui, "bounds", &active) {
            action = next;
        }
        let ready = !active.is_empty() && self.waste.properties_ready(&active, &self.document);
        if let Some(next) = run_row(
            ui,
            &self.waste,
            "Find the classes",
            ready,
            self.waste.bounds_done.is_some(),
            Question::Ranges,
        ) {
            action = match next {
                Action::Classify => Action::Bounds,
                other => other,
            };
        }
        action
    }

    fn sure_question(&mut self, ui: &mut egui::Ui) -> Action {
        if let Some(why) = waste_uncertainty::unsupported(&self.document) {
            forms::card(ui, |ui| {
                ui.colored_label(ui.visuals().warn_fg_color, why);
            });
            return Action::None;
        }
        if self.waste.sure.component.density_g_cm3 == 0. {
            let density = self.document["material"]["density_g_cm3"].as_f64();
            self.waste.sure.component.density_g_cm3 = density.unwrap_or(0.);
            self.waste.sure.component.use_density = density.is_some();
        }
        let steps: Vec<(u64, String)> = certify::step_times(&self.document)
            .into_iter()
            .enumerate()
            .map(|(i, t)| (i as u64 + 1, t.label))
            .collect();
        let mass = self.document["material"]["mass_g"].as_f64().unwrap_or(1.);
        let covariance = self.catalog_installed(waste_uncertainty::COVARIANCE);
        waste_uncertainty::show_form(ui, &mut self.waste.sure, &steps, mass, Some(covariance));
        if !self.document["uncertainty"].is_null() {
            ui.weak("The problem's own uncertainty block is left out; the sampler draws these inputs instead.");
        }
        let running = self
            .job
            .as_ref()
            .is_some_and(|job| job.kind == AppJobKind::Command(Command::WasteUncertainty));
        let mut action = Action::None;
        ui.add_space(8.);
        ui.horizontal(|ui| {
            let samples = self.waste.sure.samples;
            if forms::primary(ui, self.job.is_none(), "Sample the class")
                .on_hover_text(format!(
                    "{} solves of the problem in the background; cancel from the toolbar",
                    samples + 1
                ))
                .clicked()
            {
                action = Action::Sure;
            }
            if ui
                .add_enabled(
                    self.waste.sure_done.is_some(),
                    egui::Button::new("Save result…"),
                )
                .clicked()
            {
                action = Action::SaveResult(Question::HowSure);
            }
            if running {
                ui.spinner();
                ui.label("Sampling…");
            }
        });
        action
    }

    /// The shared properties card, for the nuclides of one question.
    fn properties_section(
        &mut self,
        ui: &mut egui::Ui,
        salt: &str,
        active: &BTreeSet<String>,
    ) -> Option<Action> {
        forms::section(
            ui,
            "Nuclide properties",
            "Z, half-life and alpha emission of every nuclide with activity, read from the problem's decay data. Enter one by hand where the data has no record.",
        );
        let idle = self.waste.job.is_none();
        forms::properties_card(
            ui,
            salt,
            &mut self.waste.properties,
            active,
            &self.document["decay"],
            Some(idle && !active.is_empty()),
        )
        .then(|| Action::LoadProperties(active.clone()))
    }

    fn waste_started(&mut self, started: Result<Job, String>) {
        match started {
            Ok(job) => {
                let message = match job.kind {
                    JobKind::Properties => "Reading nuclide properties from the decay data…",
                    JobKind::Classify => "Classifying the selected steps…",
                    JobKind::Package => "Classifying the container…",
                    JobKind::Bounds => "Finding the classes the ranges reach…",
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

    /// Read the properties of `active` the decay data has not been asked about.
    fn load_properties(&mut self, ctx: &egui::Context, active: BTreeSet<String>) {
        let decay = self.document["decay"].clone();
        let wanted = self.waste.properties.to_look_up(&active, &decay);
        let asked = if wanted.is_empty() { active } else { wanted };
        let started = self
            .validate()
            .and_then(|spec| start(ctx, JobKind::Properties, move || derive(spec, asked, decay)));
        self.waste_started(started);
    }

    fn classify_waste(&mut self, ctx: &egui::Context) {
        let Some(result) = &self.result else {
            return;
        };
        let started = classify_request(&mut self.waste, result, &self.document)
            .and_then(|spec| {
                let screen = if self.waste.intrusion.on {
                    Some(intrusion_spec(
                        &self.waste.intrusion,
                        self.waste.activated_metal,
                        spec.clone(),
                    )?)
                } else {
                    None
                };
                Ok((spec, screen))
            })
            .and_then(|(spec, screen)| {
                let value = result.value.clone();
                start(ctx, JobKind::Classify, move || {
                    classify(value, spec, screen)
                })
            });
        self.waste_started(started);
    }

    fn classify_package(&mut self, ctx: &egui::Context) {
        let steps = self.result.as_ref().map(labelled_steps).unwrap_or_default();
        let declared: Vec<(u64, String)> = (1..=self.waste.package.declared_steps)
            .map(|n| (n, String::new()))
            .collect();
        let targets = self
            .waste
            .package
            .targets(if steps.is_empty() { &declared } else { &steps });
        let active = self.waste.package_nuclides(self.result.as_ref(), &targets);
        let result = self
            .result
            .as_ref()
            .filter(|_| self.waste.package.uses_this_result())
            .map(|r| r.value.clone());
        let started = self
            .waste
            .properties
            .map(&active)
            .and_then(|properties| {
                waste_package::package_spec(&self.waste.package, &targets, INPUT_FILE, properties)
            })
            .and_then(|spec| start(ctx, JobKind::Package, move || package(result, spec)));
        self.waste_started(started);
    }

    fn pick_package_file(&mut self, i: usize) {
        let Some(path) = self.platform.pick_file(
            Dialog::new()
                .title("Choose the saved result this item takes its activity from")
                .filter("ACTINV result", &["json"]),
        ) else {
            return;
        };
        match file_nuclides(&path) {
            Ok(nuclides) => {
                if let Some(item) = self.waste.package.items.get_mut(i) {
                    item.file = path.display().to_string();
                    item.file_nuclides = nuclides;
                }
            }
            Err(error) => self.waste_failed(error),
        }
    }

    fn classify_bounds(&mut self, ctx: &egui::Context) {
        let active = self.waste.bounds.nuclides();
        let started = self
            .waste
            .properties
            .map(&active)
            .and_then(|properties| waste_bounds::bounds_spec(&self.waste.bounds, properties))
            .and_then(|spec| start(ctx, JobKind::Bounds, move || bounds(spec)));
        self.waste_started(started);
    }

    fn fill_bounds(&mut self, time: usize, step: usize, spread_pct: f64) {
        let Some(result) = &self.result else {
            return;
        };
        let activity: Vec<(String, f64)> = result
            .steps()
            .get(step)
            .and_then(|s| s["activity_Bq_per_g"].as_object())
            .into_iter()
            .flatten()
            .filter_map(|(name, a)| a.as_f64().map(|a| (name.clone(), a)))
            .collect();
        self.waste.bounds.fill(time, &activity, spread_pct);
    }

    fn start_sure(&mut self) {
        let started = self.validate().and_then(|spec| {
            let value = serde_json::to_value(spec).map_err(|e| e.to_string())?;
            let scratch = Scratch::create()?;
            let problem = scratch.file("problem.json");
            model::write_json(&problem, &waste_uncertainty::sampled_problem(&value))?;
            let steps = certify::step_times(&self.document).len() as u64;
            let targets: Vec<u64> = (1..=steps)
                .filter(|n| !self.waste.sure.skipped.contains(n))
                .collect();
            let mass = self.document["material"]["mass_g"].as_f64().unwrap_or(1.);
            let path = problem
                .to_str()
                .ok_or("the temporary folder is not valid UTF-8")?
                .to_owned();
            let spec =
                waste_uncertainty::uncertainty_spec(&self.waste.sure, &path, &targets, mass)?;
            self.start_command(
                Command::WasteUncertainty,
                spec,
                json!({}),
                "Sampling the waste class: one solve per sample, in the background.",
            )?;
            self.waste.sure_problem = Some(scratch);
            Ok(())
        });
        if let Err(error) = started {
            self.waste_failed(error);
        }
    }

    /// Install a finished background job.
    fn poll_waste(&mut self) {
        // the how-sure result arrives through the job layer
        if let Some(done) = self.command_result(Command::WasteUncertainty) {
            if let Some(path) = done.primary.clone().filter(|path| {
                done.exit == 0 && self.waste.sure_done.as_ref().is_none_or(|(p, _)| p != path)
            }) {
                let read = fs::read_to_string(&path)
                    .map_err(|e| e.to_string())
                    .and_then(|text| {
                        serde_json::from_str::<Value>(&text).map_err(|e| e.to_string())
                    });
                match read {
                    Ok(value) => self.waste.sure_done = Some((path, value)),
                    Err(error) => {
                        self.waste.sure_done = Some((path, Value::Null));
                        self.waste_failed(format!("Cannot read the sampling result: {error}"));
                    }
                }
                self.waste.sure_problem = None;
            }
        }
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
                self.waste.properties.absorb(&active, decay, &derived);
                self.waste.error = None;
                self.report(Ok(format!(
                    "Nuclide properties read for {found} of {total} nuclides. A nuclide without a decay record leaves its class unknown unless you enter it."
                )));
            }
            Ok(Output::Classified(done)) => {
                let message = match &done.view {
                    Ok(_) => "Classification complete. Save the spec and the result to reproduce it from the command line.",
                    Err(_) => "The evaluator wrote a result this page cannot read.",
                };
                self.waste.done = Some(done);
                self.waste.classified_for = self.result.as_ref().map(fingerprint);
                self.waste.error = None;
                self.report(Ok(message.into()));
            }
            Ok(Output::Finished(question, done)) => {
                match question {
                    Question::Container => self.waste.package_done = Some(done),
                    _ => self.waste.bounds_done = Some(done),
                }
                self.waste.error = None;
                self.report(Ok(
                    "Classified. Save the spec to reproduce it from the command line.".into(),
                ));
            }
            Err(error) => self.waste_failed(error),
        }
    }

    /// The answer to the open question, below its form.
    fn waste_answer(&mut self, ui: &mut egui::Ui) {
        let question = self.waste.question;
        let has = match question {
            Question::Class => self.waste.done.is_some(),
            Question::Container => self.waste.package_done.is_some(),
            Question::Ranges => self.waste.bounds_done.is_some(),
            Question::HowSure => self.waste.sure_done.is_some(),
        };
        if !has {
            return;
        }
        ui.add_space(12.);
        ui.separator();
        forms::section(ui, "Answer", "");
        match question {
            Question::Class => {
                let done = self.waste.done.as_ref().expect("checked");
                match &done.view {
                    Ok(parsed) => view::show(ui, parsed),
                    Err(error) => {
                        ui.colored_label(ui.visuals().error_fg_color, error.as_str());
                    }
                }
                view::show_intrusion(ui, &done.value);
            }
            Question::Container => {
                waste_package::show_result(
                    ui,
                    &self.waste.package_done.as_ref().expect("checked").value,
                );
            }
            Question::Ranges => {
                waste_bounds::show_result(
                    ui,
                    &self.waste.bounds_done.as_ref().expect("checked").value,
                );
            }
            Question::HowSure => {
                waste_uncertainty::show_result(
                    ui,
                    &self.waste.sure_done.as_ref().expect("checked").1,
                );
            }
        }
    }

    fn save_waste_spec(&mut self, question: Question) {
        let source = match question {
            Question::Class => self
                .waste
                .done
                .as_ref()
                .map(|d| (d.scratch.0.clone(), d.spec_file)),
            Question::Container => self
                .waste
                .package_done
                .as_ref()
                .map(|d| (d.scratch.0.clone(), d.spec_file)),
            Question::Ranges => self
                .waste
                .bounds_done
                .as_ref()
                .map(|d| (d.scratch.0.clone(), d.spec_file)),
            Question::HowSure => None,
        };
        let Some((source, spec_file)) = source else {
            return;
        };
        let Some(folder) = self.platform.pick_folder(
            Dialog::new().title("Choose a folder for the spec and the result it reads"),
        ) else {
            return;
        };
        let saved = save_pair(&source, &folder, spec_file);
        self.report(saved.map(|names| {
            format!(
                "Saved {} in {}. Run `{} {spec_file}` there.",
                names.join(" and "),
                folder.display(),
                command_for(spec_file),
            )
        }));
    }

    fn save_waste_result(&mut self, question: Question) {
        let text = match question {
            Question::Class => self.waste.done.as_ref().map(|d| d.text.clone()),
            Question::Container => self.waste.package_done.as_ref().map(|d| d.text.clone()),
            Question::Ranges => self.waste.bounds_done.as_ref().map(|d| d.text.clone()),
            Question::HowSure => self
                .waste
                .sure_done
                .as_ref()
                .and_then(|(path, _)| fs::read_to_string(path).ok()),
        };
        let Some(text) = text else {
            return;
        };
        self.save_text(OUTPUT_FILE, &text);
    }
}

/// The button row under a form: the calculation, then saving its spec and result.
fn run_row(
    ui: &mut egui::Ui,
    state: &WasteState,
    label: &str,
    ready: bool,
    finished: bool,
    question: Question,
) -> Option<Action> {
    let mut action = None;
    ui.add_space(8.);
    ui.horizontal(|ui| {
        let idle = state.job.is_none();
        if forms::primary(ui, idle && ready, label).clicked() {
            action = Some(Action::Classify);
        }
        if ui
            .add_enabled(finished, egui::Button::new("Save spec…"))
            .on_hover_text(
                "Save the spec and the result it reads, so the command line reproduces this answer",
            )
            .clicked()
        {
            action = Some(Action::SaveSpec(question));
        }
        if ui
            .add_enabled(finished, egui::Button::new("Save result…"))
            .on_hover_text("Save the evaluator's result exactly as the command writes it")
            .clicked()
        {
            action = Some(Action::SaveResult(question));
        }
        if let Some(job) = &state.job {
            ui.spinner();
            ui.label(match job.kind {
                JobKind::Properties => "Reading decay data…",
                _ => "Classifying…",
            });
        }
    });
    if !ready {
        ui.weak("Load or enter the nuclide properties first.");
    }
    action
}

/// The class-of-this-result form. Returns what its buttons asked for.
fn controls(
    ui: &mut egui::Ui,
    result: &ResultDocument,
    document: &Value,
    state: &mut WasteState,
) -> Action {
    let steps = step_list(result);
    let selected = state.selected(&steps);
    let active = state.nuclides(result.steps(), &selected);
    let ready = !selected.is_empty() && state.properties_ready(&active, document);
    let mass_g = document["material"]["mass_g"].as_f64().unwrap_or(1.);
    let density = document["material"]["density_g_cm3"]
        .as_f64()
        .filter(|d| *d > 0.);
    let mut action = Action::None;
    forms::section(
        ui,
        "Component",
        &format!("One homogeneous component, the irradiated material: {mass_g} g, the material mass of the problem."),
    );
    component_card(ui, state, mass_g, density);
    forms::section(ui, "Steps to classify", "");
    steps_card(ui, state, &steps);
    forms::section(ui, "Retained H-3", "");
    external_card(ui, state, &selected);
    forms::section(
        ui,
        "Nuclide properties",
        "Z, half-life and alpha emission of every nuclide with activity at a selected step, read from the problem's decay data. Enter one by hand where the data has no record.",
    );
    let idle = state.job.is_none();
    if forms::properties_card(
        ui,
        "class",
        &mut state.properties,
        &active,
        &document["decay"],
        Some(idle && !active.is_empty()),
    ) {
        action = Action::LoadProperties(active.clone());
    }
    forms::section(ui, "Draft intrusion screen", "");
    intrusion_card(ui, state);
    if let Some(next) = run_row(
        ui,
        state,
        if state.intrusion.on {
            "Classify and screen"
        } else {
            "Classify"
        },
        ready,
        state.done.is_some(),
        Question::Class,
    ) {
        action = next;
    }
    action
}

fn component_card(ui: &mut egui::Ui, state: &mut WasteState, mass_g: f64, density: Option<f64>) {
    let basis = state.basis(density);
    forms::card(ui, |ui| {
        ui.horizontal(|ui| {
            ui.label("Waste type");
            ui.selectable_value(&mut state.activated_metal, true, "Activated metal");
            ui.selectable_value(&mut state.activated_metal, false, "General");
        });
        if let Some(density) = density {
            ui.horizontal(|ui| {
                ui.label("Volume from");
                ui.selectable_value(
                    &mut state.volume,
                    Some(Volume::Density),
                    format!("the material density ({density} g/cm³)"),
                );
                ui.selectable_value(
                    &mut state.volume,
                    Some(Volume::Entered),
                    "a displaced volume",
                );
            });
        }
        match (basis, density) {
            (Volume::Density, Some(density)) => {
                ui.weak(format!(
                    "Volume = mass / density = {} cm³, the solid volume with no voids.",
                    forms::three(mass_g / density)
                ));
            }
            _ => {
                ui.horizontal(|ui| {
                    ui.label("Displaced volume");
                    forms::amount(ui, &mut state.volume_cm3, 90.);
                    ui.label("cm³");
                    ui.weak("the envelope with major voids removed");
                });
            }
        }
    });
}

fn steps_card(ui: &mut egui::Ui, state: &mut WasteState, steps: &[(u64, f64)]) {
    let labelled: Vec<(u64, String)> = steps
        .iter()
        .map(|(n, t)| (*n, model::readable_time(*t)))
        .collect();
    forms::card(ui, |ui| {
        forms::step_choice(ui, "class", &labelled, &mut state.skipped)
    });
}

fn external_card(ui: &mut egui::Ui, state: &mut WasteState, selected: &[u64]) {
    forms::card(ui, |ui| {
        ui.checkbox(
            &mut state.external_h3,
            "Declare retained H-3 that the activation calculation does not model",
        );
        if !state.external_h3 {
            ui.weak("Not applicable: the inventory holds the only H-3.");
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
                        forms::amount(ui, state.h3_bq.entry(*step).or_insert(0.), 100.);
                    });
                }
            });
    });
}

fn intrusion_card(ui: &mut egui::Ui, state: &mut WasteState) {
    forms::card(ui, |ui| {
        ui.checkbox(
            &mut state.intrusion.on,
            "Also screen the whole container against the draft intrusion table (NUREG-1556 Vol. 22, February 2026 draft)",
        );
        if !state.intrusion.on {
            ui.weak("A screen of the draft table, not an intrusion dose. Site acceptance limits and reportable quantities are added from the command line.");
            return;
        }
        ui.horizontal(|ui| {
            ui.label("Waste form");
            if state.activated_metal {
                ui.label("Metal, as the waste type is activated metal");
            } else {
                egui::ComboBox::from_id_salt("intrusion-form")
                    .selected_text(
                        GENERAL_FORMS[state.intrusion.form.min(GENERAL_FORMS.len() - 1)].1,
                    )
                    .show_ui(ui, |ui| {
                        for (k, (_, label)) in GENERAL_FORMS.iter().enumerate() {
                            ui.selectable_value(&mut state.intrusion.form, k, *label);
                        }
                    });
            }
        });
        let mut complete = !state.intrusion.incomplete;
        ui.checkbox(
            &mut complete,
            "The run's nuclides are the container's whole inventory",
        );
        state.intrusion.incomplete = !complete;
        if state.intrusion.incomplete {
            ui.label("What the inventory leaves out, one reason per line:");
            ui.add(
                egui::TextEdit::multiline(&mut state.intrusion.reasons)
                    .desired_rows(2)
                    .desired_width(f32::INFINITY),
            );
        }
    });
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

    fn inputs<'a>(targets: &'a [u64], props: &'a Value) -> SpecInputs<'a> {
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
        let props = json!({"Nb94": properties(41, 6.3e11)});
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
        let props = json!({});
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
        let props = json!({});
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
        let props = json!({});
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
    fn classifying_waits_for_properties_of_exactly_these_nuclides() {
        let document = json!({"decay": {"primary": "a.dat"}});
        let active = BTreeSet::from(["C14".to_owned()]);
        let mut state = WasteState::default();
        assert!(!state.properties_ready(&active, &document));
        state
            .properties
            .absorb(&active, document["decay"].clone(), &BTreeMap::new());
        assert!(state.properties_ready(&active, &document));
        // another selection, or another decay file, is not covered
        let other = BTreeSet::from(["C14".to_owned(), "Fe55".to_owned()]);
        assert!(!state.properties_ready(&other, &document));
        let moved = json!({"decay": {"primary": "b.dat"}});
        assert!(!state.properties_ready(&active, &moved));
        // entering every nuclide by hand is also enough, and so is nothing to enter
        let mut by_hand = WasteState::default();
        by_hand
            .properties
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
        let derived = BTreeMap::from([
            ("C14".to_owned(), properties(6, 1.8e11)),
            ("Fe55".to_owned(), properties(26, 8.6e7)),
        ]);
        state
            .properties
            .absorb(&active, document["decay"].clone(), &derived);
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
        let props = json!({"C14": properties(6, 1.808e11)});
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
            _ => panic!("a classification was expected"),
        }
    }

    #[test]
    fn the_evaluator_result_is_the_exact_text_it_wrote_and_parses_for_display() {
        let done = finished(classify(native_run(), c14_spec(), None).unwrap());
        let on_disk = fs::read_to_string(done.scratch.file(OUTPUT_FILE)).unwrap();
        assert_eq!(done.text, on_disk);
        let value: Value = serde_json::from_str(&done.text).unwrap();
        assert_eq!(value["schema"], "actinv-waste-result-1");
        assert_eq!(value["input"], "result.json");
        assert!(done.view.is_ok(), "{:?}", done.view);
    }

    #[test]
    fn the_saved_pair_reproduces_the_result_byte_for_byte() {
        let done = finished(classify(native_run(), c14_spec(), None).unwrap());
        let folder = Scratch::create().unwrap();
        save_pair(&done.scratch.0, &folder.0, SPEC_FILE).unwrap();
        let again = folder.file("again.json");
        actinv_cli::waste::run(
            folder.file(SPEC_FILE).to_str().unwrap(),
            Some(again.to_str().unwrap()),
        )
        .unwrap();
        assert_eq!(fs::read_to_string(again).unwrap(), done.text);
        // neither saved file may be replaced by a second save
        let error = save_pair(&done.scratch.0, &folder.0, SPEC_FILE).unwrap_err();
        assert!(error.contains("already exists"), "{error}");
    }

    #[test]
    fn dropping_a_classification_removes_its_private_folder() {
        let done = finished(classify(native_run(), c14_spec(), None).unwrap());
        let path = done.scratch.0.clone();
        assert!(path.is_dir());
        drop(done);
        assert!(!path.exists());
    }

    #[test]
    fn evaluator_errors_come_back_as_text() {
        // a result that is not a native run (the teaching example has no entry point)
        let teaching = model::tutorial_result().value;
        let error = classify(teaching, c14_spec(), None).err().expect("refused");
        assert!(error.contains("not an ACTINV RunResult"), "{error}");
        // a target step the result does not have
        let mut spec = c14_spec();
        spec["targets"] = json!([7]);
        let error = classify(native_run(), spec, None).err().expect("refused");
        assert!(error.contains("no selected step 7"), "{error}");
    }

    #[test]
    fn unknown_coverage_is_reported_when_a_nuclide_has_no_properties() {
        let mut spec = c14_spec();
        spec["nuclide_properties"] = json!({});
        let done = finished(classify(native_run(), spec, None).unwrap());
        let value: Value = serde_json::from_str(&done.text).unwrap();
        let target = &value["components"][0]["targets"][0];
        assert_eq!(target["class"], "unknown");
        assert_eq!(target["coverage"], "incomplete");
    }

    #[test]
    fn the_intrusion_screen_wraps_the_classification_and_both_read_back() {
        let screen = intrusion_spec(&Intrusion::default(), false, c14_spec()).unwrap();
        assert_eq!(screen["waste_form"], "cement_or_polymer");
        assert_eq!(screen["waste_spec"], c14_spec());
        let done = finished(classify(native_run(), c14_spec(), Some(screen)).unwrap());
        assert_eq!(done.spec_file, INTRUSION_FILE);
        assert!(done.view.is_ok(), "{:?}", done.view);
        assert!(done.value["draft_intrusion_screen"]["targets"].is_array());
        // metal follows the waste type, and an incomplete inventory needs its reasons
        let metal = Intrusion {
            incomplete: true,
            ..Intrusion::default()
        };
        assert!(intrusion_spec(&metal, true, c14_spec())
            .unwrap_err()
            .contains("leaves out"));
        let metal = Intrusion {
            incomplete: true,
            reasons: "Co60 not modelled\n".into(),
            ..Intrusion::default()
        };
        let screen = intrusion_spec(&metal, true, c14_spec()).unwrap();
        assert_eq!(screen["waste_form"], "metal");
        assert_eq!(
            screen["unbounded_inventory_reasons"],
            json!(["Co60 not modelled"])
        );
    }

    #[test]
    fn a_container_of_declared_items_is_classified_and_saves_its_spec_alone() {
        let mut state = waste_package::PackageState::default();
        let item = |id: &str, bq: f64| {
            let mut item = waste_package::Item {
                id: id.into(),
                waste_type: 0,
                mass_g: 7800.,
                volume_cm3: 1000.,
                section_of: String::new(),
                source: Source::Declared,
                file: String::new(),
                file_nuclides: BTreeSet::new(),
                declared: Vec::new(),
            };
            item.declared = vec![("Nb94".into(), BTreeMap::from([(1, bq)]))];
            item
        };
        state.items = vec![item("plug-1", 2e6), item("plug-2", 1e5)];
        let properties = json!({"Nb94": properties(41, 6.4e11)});
        let spec = waste_package::package_spec(&state, &[1], INPUT_FILE, properties).unwrap();
        let Output::Finished(Question::Container, done) = package(None, spec).unwrap() else {
            panic!("a container result was expected");
        };
        assert_eq!(done.value["schema"], "actinv-waste-package-1");
        assert!(
            ["A", "B", "C"].contains(&done.value["steps"][0]["package_class"].as_str().unwrap())
        );
        let folder = Scratch::create().unwrap();
        let saved = save_pair(&done.scratch.0, &folder.0, done.spec_file).unwrap();
        assert_eq!(saved, [PACKAGE_FILE]);
    }

    #[test]
    fn activity_ranges_are_classified_by_the_bounds_command() {
        let mut state = waste_bounds::BoundsState {
            mass_g: Some(1000.),
            ..Default::default()
        };
        state.component.use_density = false;
        state.component.volume_cm3 = 128.2;
        state.times[0].ranges = vec![waste_bounds::Range {
            nuclide: "Nb94".into(),
            lower: 5e4,
            upper: 2e5,
        }];
        state.times[0].source = "test".into();
        state.times[0].assumptions = "test".into();
        let properties = json!({"Nb94": properties(41, 6.4e11)});
        let spec = waste_bounds::bounds_spec(&state, properties).unwrap();
        let Output::Finished(Question::Ranges, done) = bounds(spec).unwrap() else {
            panic!("a ranges result was expected");
        };
        let target = &done.value["components"][0]["targets"][0];
        assert!(target["class_envelope"].is_array(), "{target}");
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
        let selected = app.waste.selected(&step_list(app.result.as_ref().unwrap()));
        let active = app
            .waste
            .nuclides(app.result.as_ref().unwrap().steps(), &selected);
        app.load_properties(&ctx, active);
        wait_for_waste_job(&mut app);
        app.classify_waste(&ctx);
        wait_for_waste_job(&mut app);
        assert!(matches!(&app.waste.done, Some(done) if done.view.is_ok()));
        app.save_waste_spec(Question::Class);
        assert!(!app.error, "{}", app.status);
        app.save_waste_result(Question::Class);
        assert!(!app.error, "{}", app.status);
        for name in [SPEC_FILE, INPUT_FILE, OUTPUT_FILE] {
            assert!(out.join(name).is_file(), "{name} was not saved");
        }
    }
}
