//! Measurements: bring what was measured into the calculation. Two questions: what flux explains
//! measured activities (`actinv reverse`, or `actinv reverse-qualified` when the nuclear-data
//! uncertainty is counted), and how a measured value narrows the result's uncertainty bands
//! (`actinv assimilate`). This module holds the forms, builds the documents the commands read and
//! reads what they write; the desktop page runs them.
use crate::{
    model::{self, ResultDocument},
    views::{certify::StepTime, waste_forms},
};
use eframe::egui::{self, RichText};
use serde_json::{json, Value};
use std::path::Path;

/// The covariance the qualified fit reads.
pub const COVARIANCE: &str = "catalog:tendl-2025-neutron-709g-covariance";

#[derive(Clone, Copy, PartialEq, Eq, Debug, Default)]
pub enum Question {
    #[default]
    Flux,
    Narrow,
}

const QUESTIONS: [(Question, &str, &str); 2] = [
    (
        Question::Flux,
        "What flux explains these activities?",
        "Fits the irradiation flux to activities measured after the run",
    ),
    (
        Question::Narrow,
        "How does a measurement narrow the bands?",
        "Folds a measured value into the uncertainty bands of the loaded result",
    ),
];

/// One measured activity.
#[derive(Clone, Debug, PartialEq)]
pub struct Measured {
    /// The schedule step, zero-based; `None` is the last step.
    pub step: Option<usize>,
    pub nuclide: String,
    pub activity: f64,
    /// One standard deviation, Bq/g; 0 when it is not known.
    pub sigma: f64,
}

impl Default for Measured {
    fn default() -> Self {
        Self {
            step: None,
            nuclide: String::new(),
            activity: 0.,
            sigma: 0.,
        }
    }
}

/// One measured value of a banded response.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Reading {
    pub response: String,
    pub value: f64,
    pub sigma: f64,
}

/// A finished fit, read back from the command's output.
#[derive(Clone, Debug, PartialEq)]
pub enum Fit {
    /// `actinv-reverse-1`.
    Plain(Value),
    /// `actinv-reverse-qualified-1`: the estimates, identifiability and consistency records.
    Qualified {
        estimates: Vec<Value>,
        identifiability: Value,
        consistency: Value,
    },
}

#[derive(Default)]
pub struct MeasurementsState {
    pub question: Question,
    pub rows: Vec<Measured>,
    /// One multiplier per irradiation step instead of one for all.
    pub segments: bool,
    /// Count the nuclear-data uncertainty (`reverse-qualified`).
    pub qualified: bool,
    pub fit: Option<Fit>,
    pub readings: Vec<Reading>,
    /// The result step the readings were taken at (zero-based).
    pub reading_step: usize,
    /// A measurement file loaded instead of the readings: its name and document.
    pub assay_file: Option<(String, Value)>,
    /// The preview of the fold: (summary, updated result).
    pub pending: Option<(Value, Value)>,
}

/// What the page must do after the form was drawn.
#[derive(Clone, Debug, PartialEq)]
pub enum MeasurementsAction {
    None,
    Fit,
    UseFit,
    LoadMeasurements,
    SaveMeasurements,
    LoadAssay,
    Preview,
    Apply,
    OpenData,
    OpenResult,
}

/// The `actinv-reverse-input-1` document of the rows.
pub fn measurements_doc(state: &MeasurementsState) -> Result<Value, String> {
    if state.rows.is_empty() {
        return Err("Add a measured activity.".into());
    }
    let mut seen = Vec::new();
    let mut list = Vec::new();
    for (i, row) in state.rows.iter().enumerate() {
        let nuclide = row.nuclide.trim();
        if nuclide.is_empty() {
            return Err(format!("Row {}: name the nuclide, such as Mn56.", i + 1));
        }
        if !(row.activity.is_finite() && row.activity >= 0.) {
            return Err(format!(
                "{nuclide}: the activity is a number of at least 0."
            ));
        }
        if seen.contains(&(row.step, nuclide.to_owned())) {
            return Err(format!("{nuclide} is measured twice at one step."));
        }
        seen.push((row.step, nuclide.to_owned()));
        let mut entry = json!({
            "step": row.step.map_or(json!("last"), |s| json!(s + 1)),
            "nuclide": nuclide,
            "activity_Bq_per_g": row.activity,
        });
        if row.sigma > 0. {
            entry["sigma_Bq_per_g"] = json!(row.sigma);
        } else if state.qualified {
            return Err(format!(
                "{nuclide}: give its uncertainty; counting the data uncertainty needs every one."
            ));
        }
        list.push(entry);
    }
    Ok(json!({"format": "actinv-reverse-input-1", "measurements": list}))
}

/// Read measurements from an `actinv-reverse-input-1` document.
pub fn rows_from(doc: &Value) -> Result<Vec<Measured>, String> {
    if doc["format"] != "actinv-reverse-input-1" {
        return Err("This is not a measurements file (actinv-reverse-input-1).".into());
    }
    doc["measurements"]
        .as_array()
        .ok_or("The file lists no measurements.")?
        .iter()
        .map(|m| {
            let step = match &m["step"] {
                Value::String(s) if s == "last" => None,
                v => Some(
                    v.as_u64()
                        .filter(|s| *s >= 1)
                        .ok_or("A step is a number from 1, or \"last\".")?
                        as usize
                        - 1,
                ),
            };
            Ok(Measured {
                step,
                nuclide: m["nuclide"].as_str().unwrap_or_default().to_owned(),
                activity: m["activity_Bq_per_g"].as_f64().unwrap_or(0.),
                sigma: m["sigma_Bq_per_g"].as_f64().unwrap_or(0.),
            })
        })
        .collect()
}

/// The problem the fit solves: as it is set up, with the bands of the measured nuclides when the
/// fit counts the data uncertainty, and without bands otherwise (they only slow the plain fit).
pub fn fit_problem(spec: &Value, state: &MeasurementsState) -> Value {
    let mut problem = spec.clone();
    if !state.qualified {
        if let Some(map) = problem.as_object_mut() {
            map.remove("uncertainty");
        }
        return problem;
    }
    let mut nuclides: Vec<String> = Vec::new();
    for row in &state.rows {
        let response = format!("activity:{}", row.nuclide.trim());
        if !nuclides.contains(&response) {
            nuclides.push(response);
        }
    }
    match problem["uncertainty"].as_object_mut() {
        Some(own) => {
            let list = own.entry("responses").or_insert_with(|| json!([]));
            if let Value::Array(list) = list {
                for response in nuclides {
                    if !list.iter().any(|r| r.as_str() == Some(&response)) {
                        list.push(json!(response));
                    }
                }
            }
        }
        None => {
            problem["uncertainty"] = json!({"covariance": {"path": COVARIANCE},
                                            "responses": nuclides, "confidence_level": 0.95});
        }
    }
    problem
}

/// Read a fit's output.
pub fn read_fit(path: &Path, qualified: bool) -> Result<Fit, String> {
    let text = std::fs::read_to_string(path)
        .map_err(|e| format!("cannot read {}: {e}", path.display()))?;
    if !qualified {
        return serde_json::from_str(&text)
            .map(Fit::Plain)
            .map_err(|e| e.to_string());
    }
    let mut estimates = Vec::new();
    let mut identifiability = Value::Null;
    let mut consistency = Value::Null;
    for line in text.lines().filter(|l| !l.trim().is_empty()) {
        let record: Value = serde_json::from_str(line).map_err(|e| e.to_string())?;
        match record["record"].as_str() {
            Some("estimate") => estimates.push(record),
            Some("identifiability") => identifiability = record,
            Some("consistency") => consistency = record,
            _ => {}
        }
    }
    if estimates.is_empty() {
        return Err(format!("{} carries no estimate", path.display()));
    }
    Ok(Fit::Qualified {
        estimates,
        identifiability,
        consistency,
    })
}

/// The multiplier of each irradiation step the fit found: (schedule step, one-based; multiplier;
/// its standard deviation). One common multiplier applies to every irradiation step.
pub fn multipliers(fit: &Fit, document: &Value) -> Vec<(usize, f64, f64)> {
    let estimates: Vec<&Value> = match fit {
        Fit::Plain(result) => match &result["estimates"] {
            Value::Array(list) => list.iter().collect(),
            single @ Value::Object(_) => vec![single],
            _ => Vec::new(),
        },
        Fit::Qualified { estimates, .. } => estimates.iter().collect(),
    };
    let sigma = |e: &Value| {
        e["standard_error"]
            .as_f64()
            .or(e["posterior_sigma"].as_f64())
            .unwrap_or(0.)
    };
    if let [only] = estimates.as_slice() {
        if only["step"].is_null() {
            // one common multiplier: every irradiation step takes it
            return document["schedule"]
                .as_array()
                .into_iter()
                .flatten()
                .enumerate()
                .filter(|(_, s)| model::number(&s["flux"]) > 0.)
                .map(|(i, _)| (i + 1, model::number(&only["multiplier"]), sigma(only)))
                .collect();
        }
    }
    estimates
        .iter()
        .filter_map(|e| {
            Some((
                e["step"].as_u64()? as usize,
                model::number(&e["multiplier"]),
                sigma(e),
            ))
        })
        .collect()
}

/// The problem with the fitted flux: each irradiation step's multiplier replaced by the fit's.
pub fn use_fit(document: &Value, fit: &Fit) -> Value {
    let mut out = document.clone();
    for (step, multiplier, _) in multipliers(fit, document) {
        if let Some(entry) = out["schedule"].get_mut(step - 1) {
            entry["flux"] = json!(multiplier);
        }
    }
    out
}

/// The banded responses of a result step, for the readings.
pub fn banded(result: &ResultDocument, step: usize) -> Vec<String> {
    let mut list: Vec<String> = result
        .steps()
        .get(step)
        .and_then(|s| s["uncertainty"]["responses"].as_object())
        .map(|r| r.keys().cloned().collect())
        .unwrap_or_default();
    list.sort();
    list
}

/// The `actinv-assay-1` document of the readings, or the loaded file.
pub fn assay_doc(state: &MeasurementsState, result: &ResultDocument) -> Result<Value, String> {
    if let Some((_, doc)) = &state.assay_file {
        return Ok(doc.clone());
    }
    if state.readings.is_empty() {
        return Err("Add a measured value.".into());
    }
    let t = result
        .steps()
        .get(state.reading_step)
        .and_then(|s| s["t_s"].as_f64())
        .ok_or("The result has no such step.")?;
    let mut entries = Vec::new();
    for reading in &state.readings {
        if reading.response.is_empty() {
            return Err("Choose what each value measures.".into());
        }
        if !(reading.value.is_finite() && reading.value > 0.) {
            return Err(format!(
                "{}: the measured value is positive.",
                words(&reading.response)
            ));
        }
        if !(reading.sigma.is_finite() && reading.sigma > 0.) {
            return Err(format!(
                "{}: give its uncertainty.",
                words(&reading.response)
            ));
        }
        entries.push(json!({"response": reading.response, "value": reading.value,
                            "standard_uncertainty": reading.sigma}));
    }
    Ok(json!({"schema": "actinv-assay-1", "time_s": t, "entries": entries}))
}

/// Words for a banded response.
fn words(response: &str) -> String {
    match response {
        "activity.total" => "Total activity".into(),
        "heat.total" => "Total decay heat".into(),
        other => match other.strip_prefix("activity:") {
            Some(nuclide) => format!("{nuclide} activity"),
            None => other.into(),
        },
    }
}

/// The unit of a response's value.
fn unit(response: &str) -> &'static str {
    if response.starts_with("heat") {
        "W/g"
    } else if response.starts_with("activity") {
        "Bq/g"
    } else {
        ""
    }
}

/// What the form needs from the page.
pub struct Context<'a> {
    pub steps: &'a [StepTime],
    pub result: Option<&'a ResultDocument>,
    pub covariance: bool,
    pub running: bool,
    pub busy: bool,
}

pub fn show(
    ui: &mut egui::Ui,
    state: &mut MeasurementsState,
    context: &Context,
) -> MeasurementsAction {
    waste_forms::tiles(ui, &QUESTIONS, &mut state.question);
    ui.add_space(4.);
    match state.question {
        Question::Flux => flux_form(ui, state, context),
        Question::Narrow => narrow_form(ui, state, context),
    }
}

fn step_words(steps: &[StepTime], step: Option<usize>) -> String {
    match step {
        None => "Last step".to_owned(),
        Some(i) => steps.get(i).map_or(format!("Step {}", i + 1), |s| {
            format!("Step {}, {}", i + 1, s.label)
        }),
    }
}

fn flux_form(
    ui: &mut egui::Ui,
    state: &mut MeasurementsState,
    context: &Context,
) -> MeasurementsAction {
    let mut action = MeasurementsAction::None;
    waste_forms::card(ui, |ui| {
        ui.label(RichText::new("Measured activities").strong());
        ui.label("The activities measured in a sample of the material, per gram, at the steps of the schedule as it is set up now. The fit finds the irradiation flux that reproduces them.");
        ui.add_space(6.);
        let mut remove = None;
        egui::Grid::new("measured-rows")
            .num_columns(5)
            .spacing([10., 6.])
            .show(ui, |ui| {
                for title in ["When", "Nuclide", "Activity, Bq/g", "Uncertainty, Bq/g", ""] {
                    ui.label(RichText::new(title).weak());
                }
                ui.end_row();
                for (i, row) in state.rows.iter_mut().enumerate() {
                    egui::ComboBox::from_id_salt(("measured-step", i))
                        .width(230.)
                        .selected_text(step_words(context.steps, row.step))
                        .show_ui(ui, |ui| {
                            ui.selectable_value(
                                &mut row.step,
                                None,
                                step_words(context.steps, None),
                            );
                            for s in 0..context.steps.len() {
                                ui.selectable_value(
                                    &mut row.step,
                                    Some(s),
                                    step_words(context.steps, Some(s)),
                                );
                            }
                        });
                    // As tall as the step and amount buttons beside it.
                    let margin = ui.spacing().button_padding;
                    let height = ui.text_style_height(&egui::TextStyle::Body) + 2. * margin.y;
                    ui.add_sized(
                        [100., height],
                        egui::TextEdit::singleline(&mut row.nuclide)
                            .hint_text("Mn56")
                            .margin(margin),
                    );
                    waste_forms::amount(ui, &mut row.activity, 120.);
                    waste_forms::amount(ui, &mut row.sigma, 120.)
                        .on_hover_text("One standard deviation; 0 when it is not known");
                    if ui
                        .small_button("×")
                        .on_hover_text("Remove this measurement")
                        .clicked()
                    {
                        remove = Some(i);
                    }
                    ui.end_row();
                }
            });
        if let Some(i) = remove {
            state.rows.remove(i);
            state.fit = None;
        }
        ui.horizontal_wrapped(|ui| {
            if ui.button("Add a measurement").clicked() {
                let step = state.rows.last().and_then(|r| r.step);
                state.rows.push(Measured {
                    step,
                    ..Default::default()
                });
            }
            if ui.button("Load measurements…").clicked() {
                action = MeasurementsAction::LoadMeasurements;
            }
            if ui
                .add_enabled(
                    !state.rows.is_empty(),
                    egui::Button::new("Save measurements…"),
                )
                .clicked()
            {
                action = MeasurementsAction::SaveMeasurements;
            }
        });
    });
    waste_forms::card(ui, |ui| {
        ui.label(RichText::new("How to fit").strong());
        ui.checkbox(&mut state.segments, "One flux for each irradiation step")
            .on_hover_text("Needs at least as many measurements as irradiation steps; otherwise one flux scales them all.");
        ui.horizontal_wrapped(|ui| {
            ui.add_enabled_ui(context.covariance, |ui| {
                ui.checkbox(&mut state.qualified, "Count the nuclear-data uncertainty");
            });
            if context.covariance {
                ui.weak("gives each flux a confidence interval, says which steps the measurements can tell apart, and checks they agree; needs every uncertainty");
            } else {
                ui.weak("needs the TENDL-2025 covariance.");
                if ui.link("Install it on Data").clicked() {
                    action = MeasurementsAction::OpenData;
                }
            }
        });
        if state.qualified {
            state.segments = true;
        }
        ui.add_space(6.);
        let doc = measurements_doc(state);
        ui.horizontal_wrapped(|ui| {
            if waste_forms::primary(ui, !context.busy && doc.is_ok(), "Fit the flux").clicked() {
                action = MeasurementsAction::Fit;
            }
            if context.running {
                ui.spinner();
                ui.weak("Fitting: one solve per irradiation step.");
            } else if let Err(why) = &doc {
                ui.weak(why);
            }
        });
    });
    if let Some(fit) = &state.fit {
        waste_forms::card(ui, |ui| {
            if fit_view(ui, fit, context) {
                action = MeasurementsAction::UseFit;
            }
        });
    }
    action
}

/// One fitted irradiation step: its number, the multiplier, its standard error, the confidence
/// interval and whether the measurements can tell it apart.
type StepFit = (u64, f64, f64, Option<[f64; 2]>, Option<bool>);

/// One band in the preview: the response, its nominal before, after, and the gain.
type BandChange = (String, Option<f64>, Option<f64>, Option<f64>);

/// The fit: the flux found, how the measurements agree with it, and Use this flux.
/// Returns whether Use this flux was clicked.
fn fit_view(ui: &mut egui::Ui, fit: &Fit, context: &Context) -> bool {
    let accent = crate::visuals::accent(ui);
    let schedule: Vec<StepTime> = context.steps.to_vec();
    let (common, per_step): (Option<(f64, f64)>, Vec<StepFit>) = match fit {
        Fit::Plain(result) => match &result["estimates"] {
            Value::Object(_) => (
                Some((
                    model::number(&result["estimates"]["multiplier"]),
                    model::number(&result["estimates"]["standard_error"]),
                )),
                Vec::new(),
            ),
            Value::Array(list) => (
                None,
                list.iter()
                    .map(|e| {
                        (
                            e["step"].as_u64().unwrap_or(0),
                            model::number(&e["multiplier"]),
                            model::number(&e["standard_error"]),
                            None,
                            None,
                        )
                    })
                    .collect(),
            ),
            _ => (None, Vec::new()),
        },
        Fit::Qualified {
            estimates,
            identifiability,
            ..
        } => (
            None,
            estimates
                .iter()
                .map(|e| {
                    let step = e["step"].as_u64().unwrap_or(0);
                    let resolvable = identifiability["segments"]
                        .as_array()
                        .into_iter()
                        .flatten()
                        .find(|s| s["step"].as_u64() == Some(step))
                        .and_then(|s| s["resolvable"].as_bool());
                    let interval = e["confidence_interval"]
                        .as_array()
                        .filter(|c| c.len() == 2)
                        .map(|c| [model::number(&c[0]), model::number(&c[1])]);
                    (
                        step,
                        model::number(&e["multiplier"]),
                        model::number(&e["posterior_sigma"]),
                        interval,
                        resolvable,
                    )
                })
                .collect(),
        ),
    };
    if let Some((m, sigma)) = common {
        ui.label(
            RichText::new(format!(
                "The irradiation flux was {} ± {} times the spectrum's total.",
                fixed(m),
                fixed(sigma)
            ))
            .size(17.)
            .color(accent),
        );
    } else {
        ui.label(
            RichText::new(
                "The flux in each irradiation step, as a multiple of the spectrum's total",
            )
            .size(17.)
            .color(accent),
        );
        egui::Grid::new("fit-steps")
            .num_columns(4)
            .striped(true)
            .spacing([24., 4.])
            .show(ui, |ui| {
                for title in ["Step", "Flux multiplier", "95% interval", ""] {
                    ui.label(RichText::new(title).strong());
                }
                ui.end_row();
                for (step, m, sigma, interval, resolvable) in &per_step {
                    let i = (*step as usize).saturating_sub(1);
                    ui.label(schedule.get(i).map_or(format!("Step {step}"), |s| {
                        format!("Step {step}, {}", s.label)
                    }));
                    ui.label(format!("{} ± {}", fixed(*m), fixed(*sigma)));
                    ui.label(interval.map_or(String::new(), |[a, b]| {
                        format!("{} to {}", fixed(a), fixed(b))
                    }));
                    match resolvable {
                        Some(false) => {
                            ui.colored_label(
                                ui.visuals().warn_fg_color,
                                "the measurements cannot tell this step apart",
                            );
                        }
                        _ => {
                            ui.label("");
                        }
                    }
                    ui.end_row();
                }
            });
    }
    ui.add_space(6.);
    match fit {
        Fit::Plain(result) => {
            ui.label(RichText::new("How the fit reproduces each measurement").strong());
            egui::Grid::new("fit-rows")
                .num_columns(4)
                .striped(true)
                .spacing([24., 4.])
                .show(ui, |ui| {
                    for title in ["Nuclide", "Measured, Bq/g", "Fitted, Bq/g", "Difference"] {
                        ui.label(RichText::new(title).strong());
                    }
                    ui.end_row();
                    for m in result["measurements"].as_array().into_iter().flatten() {
                        let measured = model::number(&m["activity_Bq_per_g"]);
                        let fitted = model::number(&m["predicted_Bq_per_g"]);
                        ui.label(format!(
                            "{} at step {}",
                            m["nuclide"].as_str().unwrap_or("?"),
                            m["step"]
                        ));
                        ui.label(model::sci(measured));
                        ui.label(model::sci(fitted));
                        ui.label(if measured > 0. {
                            format!("{:+.2}%", 100. * (fitted - measured) / measured)
                        } else {
                            String::new()
                        });
                        ui.end_row();
                    }
                });
            if let (Some(chi), Some(dof)) = (
                result["chi_square"].as_f64(),
                result["degrees_of_freedom"].as_u64(),
            ) {
                ui.weak(format!(
                    "Chi-square {} for {dof} degrees of freedom.",
                    model::sci(chi)
                ));
            }
        }
        Fit::Qualified { consistency, .. } => {
            let pulls: Vec<&Value> = consistency["pulls"]
                .as_array()
                .into_iter()
                .flatten()
                .collect();
            let flagged: Vec<String> = pulls
                .iter()
                .filter(|p| p["flagged"] == true)
                .map(|p| p["nuclide"].as_str().unwrap_or("?").to_owned())
                .collect();
            if flagged.is_empty() {
                ui.label(format!(
                    "The {} measurements agree with the fit within their uncertainties and the data's.",
                    pulls.len()
                ));
            } else {
                ui.colored_label(
                    ui.visuals().warn_fg_color,
                    format!(
                        "These measurements disagree with the fit by more than their uncertainty allows: {}.",
                        flagged.join(", ")
                    ),
                );
            }
            if let (Some(chi), Some(dof)) = (
                consistency["chi_square"].as_f64(),
                consistency["degrees_of_freedom"].as_u64(),
            ) {
                ui.weak(format!(
                    "Chi-square {} for {dof} degrees of freedom.",
                    model::sci(chi)
                ));
            }
        }
    }
    ui.add_space(6.);
    let mut used = false;
    ui.horizontal_wrapped(|ui| {
        used = ui
            .add_enabled(!context.busy, egui::Button::new("Use this flux"))
            .on_hover_text("Put the fitted multipliers into the irradiation steps of the problem")
            .clicked();
        ui.weak("puts the fitted flux into the schedule; Undo edit takes it back");
    });
    used
}

/// A multiplier for reading: four significant figures, plain when it reads well so.
fn fixed(value: f64) -> String {
    if value == 0. || (1e-3..1e5).contains(&value.abs()) {
        let digits = (3 - value.abs().log10().floor() as i32).clamp(0, 6) as usize;
        format!("{value:.digits$}")
    } else {
        format!("{value:.3e}")
    }
}

fn narrow_form(
    ui: &mut egui::Ui,
    state: &mut MeasurementsState,
    context: &Context,
) -> MeasurementsAction {
    let mut action = MeasurementsAction::None;
    let Some(result) = context.result else {
        waste_forms::card(ui, |ui| {
            ui.label("Narrowing needs a result with uncertainty bands. Run the problem, or open a result.");
            if ui.button("Open result…").clicked() {
                action = MeasurementsAction::OpenResult;
            }
        });
        return action;
    };
    let count = result.steps().len();
    state.reading_step = state.reading_step.min(count.saturating_sub(1));
    let responses = banded(result, state.reading_step);
    waste_forms::card(ui, |ui| {
        ui.label(RichText::new("Measured values").strong());
        ui.label("A value measured on the material, with its uncertainty, at one step of the loaded result. It narrows the band of what it measures and, through their shared data, the bands of everything correlated with it.");
        ui.add_space(6.);
        ui.horizontal(|ui| {
            ui.label("When");
            egui::ComboBox::from_id_salt("reading-step")
                .width(260.)
                .selected_text(format!(
                    "Step {}, {}",
                    state.reading_step + 1,
                    crate::views::summary::when(result, state.reading_step)
                ))
                .show_ui(ui, |ui| {
                    for i in 0..count {
                        if ui
                            .selectable_value(
                                &mut state.reading_step,
                                i,
                                format!(
                                    "Step {}, {}",
                                    i + 1,
                                    crate::views::summary::when(result, i)
                                ),
                            )
                            .changed()
                        {
                            state.pending = None;
                        }
                    }
                });
        });
        if responses.is_empty() {
            ui.colored_label(
                ui.visuals().warn_fg_color,
                "This step has no uncertainty bands. Ask for them under What to compute, run again, then narrow them here.",
            );
            return;
        }
        if let Some(name) = state.assay_file.as_ref().map(|(name, _)| name.clone()) {
            ui.horizontal(|ui| {
                ui.label(format!("Measurements from {name}"));
                if ui
                    .small_button("×")
                    .on_hover_text("Enter the values here instead")
                    .clicked()
                {
                    state.assay_file = None;
                    state.pending = None;
                }
            });
        } else {
            if state.readings.is_empty() {
                state.readings.push(Reading {
                    response: responses[0].clone(),
                    ..Default::default()
                });
            }
            let mut remove = None;
            egui::Grid::new("readings")
                .num_columns(4)
                .spacing([10., 6.])
                .show(ui, |ui| {
                    for title in ["What", "Measured", "Uncertainty", ""] {
                        ui.label(RichText::new(title).weak());
                    }
                    ui.end_row();
                    for (i, reading) in state.readings.iter_mut().enumerate() {
                        egui::ComboBox::from_id_salt(("reading-response", i))
                            .width(200.)
                            .selected_text(words(&reading.response))
                            .show_ui(ui, |ui| {
                                for r in &responses {
                                    ui.selectable_value(&mut reading.response, r.clone(), words(r));
                                }
                            });
                        ui.horizontal(|ui| {
                            waste_forms::amount(ui, &mut reading.value, 120.);
                            ui.weak(unit(&reading.response));
                        });
                        ui.horizontal(|ui| {
                            waste_forms::amount(ui, &mut reading.sigma, 120.);
                            ui.weak(unit(&reading.response));
                        });
                        if ui
                            .small_button("×")
                            .on_hover_text("Remove this value")
                            .clicked()
                        {
                            remove = Some(i);
                        }
                        ui.end_row();
                    }
                });
            if let Some(i) = remove {
                state.readings.remove(i);
                state.pending = None;
            }
        }
        ui.horizontal_wrapped(|ui| {
            if state.assay_file.is_none() && ui.button("Add a value").clicked() {
                state.readings.push(Reading {
                    response: responses[0].clone(),
                    ..Default::default()
                });
            }
            if ui
                .button("Load a measurement file…")
                .on_hover_text(
                    "An actinv-assay-1 file: values, or a mixture such as a gross-gamma count",
                )
                .clicked()
            {
                action = MeasurementsAction::LoadAssay;
            }
        });
        ui.add_space(6.);
        let doc = assay_doc(state, result);
        ui.horizontal_wrapped(|ui| {
            if waste_forms::primary(ui, !context.busy && doc.is_ok(), "Preview").clicked() {
                action = MeasurementsAction::Preview;
            }
            if ui
                .add_enabled(
                    state.pending.is_some(),
                    egui::Button::new("Apply to the result"),
                )
                .clicked()
            {
                action = MeasurementsAction::Apply;
            }
            if let Err(why) = &doc {
                ui.weak(why);
            }
        });
    });
    if let Some((summary, _)) = &state.pending {
        waste_forms::card(ui, |ui| preview_view(ui, summary));
    }
    action
}

/// The fold before it is applied: the verdict, and each band before and after.
fn preview_view(ui: &mut egui::Ui, summary: &Value) {
    let verdict = summary["verdict"].as_str().unwrap_or("assimilated");
    let (colour, sentence) = match verdict {
        "consistent" => (
            crate::visuals::accent(ui),
            "The measurement agrees with the calculation.",
        ),
        "marginal" => (
            ui.visuals().warn_fg_color,
            "The measurement agrees only at the edge of the band.",
        ),
        "conflict" => (
            ui.visuals().error_fg_color,
            "The measurement conflicts with the calculation.",
        ),
        _ => (crate::visuals::accent(ui), "The measurement is folded in."),
    };
    ui.label(RichText::new(sentence).size(17.).color(colour));
    let rows: Vec<BandChange> = if let Some(updates) = summary["updates"].as_array() {
        updates
            .iter()
            .map(|u| {
                (
                    u["response"].as_str().unwrap_or("").to_owned(),
                    u["prior"]["nominal"].as_f64(),
                    u["update"]["posterior"].as_f64(),
                    u["update"]["kalman_gain"].as_f64(),
                )
            })
            .collect()
    } else {
        summary["terms"]
            .as_array()
            .into_iter()
            .flatten()
            .map(|t| {
                (
                    t["response"].as_str().unwrap_or("").to_owned(),
                    t["prior_nominal"].as_f64(),
                    t["posterior"].as_f64(),
                    t["kalman_gain"].as_f64(),
                )
            })
            .collect()
    };
    egui::Grid::new("fold-rows")
        .num_columns(4)
        .striped(true)
        .spacing([24., 4.])
        .show(ui, |ui| {
            for title in [
                "Band",
                "Calculated",
                "After the measurement",
                "Weight of the measurement",
            ] {
                ui.label(RichText::new(title).strong());
            }
            ui.end_row();
            let show = |v: Option<f64>| v.map_or("".to_owned(), model::sci);
            for (response, prior, posterior, gain) in rows {
                ui.label(words(&response));
                ui.label(show(prior));
                ui.label(show(posterior));
                ui.label(gain.map_or(String::new(), |g| format!("{:.0}%", 100. * g)));
                ui.end_row();
            }
        });
    if summary["induced_correlations"].is_object() {
        ui.weak("The measured terms are correlated after this; the applied result carries their correlation.");
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn steps() -> Vec<StepTime> {
        vec![StepTime {
            t_s: 60.,
            label: "at shutdown".into(),
        }]
    }

    fn rows() -> Vec<Measured> {
        vec![
            Measured {
                step: None,
                nuclide: "Mn56".into(),
                activity: 92688.9,
                sigma: 926.9,
            },
            Measured {
                step: Some(0),
                nuclide: " Fe55 ".into(),
                activity: 37.8,
                sigma: 0.,
            },
        ]
    }

    #[test]
    fn measurements_become_a_reverse_input_and_read_back() {
        let mut state = MeasurementsState {
            rows: rows(),
            ..Default::default()
        };
        let doc = measurements_doc(&state).unwrap();
        assert_eq!(
            doc,
            json!({"format": "actinv-reverse-input-1", "measurements": [
                {"step": "last", "nuclide": "Mn56", "activity_Bq_per_g": 92688.9, "sigma_Bq_per_g": 926.9},
                {"step": 1, "nuclide": "Fe55", "activity_Bq_per_g": 37.8}]})
        );
        let back = rows_from(&doc).unwrap();
        assert_eq!(back[0], state.rows[0]);
        assert_eq!(back[1].step, Some(0));
        // the qualified fit needs every uncertainty, and a nuclide once per step
        state.qualified = true;
        assert!(measurements_doc(&state).unwrap_err().contains("Fe55"));
        state.rows[1].sigma = 0.4;
        state.rows[1].step = None;
        state.rows[1].nuclide = "Mn56".into();
        assert!(measurements_doc(&state).unwrap_err().contains("twice"));
        assert!(rows_from(&json!({"format": "other"})).is_err());
        let _ = steps();
    }

    #[test]
    fn the_qualified_problem_bands_the_measured_nuclides_and_the_plain_one_drops_bands() {
        let state = MeasurementsState {
            rows: rows(),
            qualified: true,
            ..Default::default()
        };
        let spec = json!({"schedule": [{"dt": "60 s", "flux": 1.0}],
                          "uncertainty": {"responses": ["activity:Mn56", "heat.total"]}});
        let problem = fit_problem(&spec, &state);
        assert_eq!(
            problem["uncertainty"]["responses"],
            json!(["activity:Mn56", "heat.total", "activity:Fe55"])
        );
        let bare = fit_problem(&json!({"schedule": []}), &state);
        assert_eq!(bare["uncertainty"]["covariance"]["path"], COVARIANCE);
        let plain = MeasurementsState {
            rows: rows(),
            ..Default::default()
        };
        assert!(fit_problem(&spec, &plain)["uncertainty"].is_null());
    }

    #[test]
    fn using_the_fit_sets_the_flux_of_the_irradiation_steps() {
        let document = json!({"schedule": [{"dt": "1 h", "flux": 1.0}, {"dt": "1 d", "flux": 0.0},
                                           {"dt": "1 h", "flux": 0.5}]});
        let common = Fit::Plain(json!({"estimates": {"multiplier": 2.0, "standard_error": 0.01}}));
        assert_eq!(
            multipliers(&common, &document),
            [(1, 2.0, 0.01), (3, 2.0, 0.01)]
        );
        let used = use_fit(&document, &common);
        assert_eq!(used["schedule"][0]["flux"], 2.0);
        assert_eq!(used["schedule"][1]["flux"], 0.0);
        assert_eq!(used["schedule"][2]["flux"], 2.0);
        let per_step = Fit::Qualified {
            estimates: vec![
                json!({"step": 1, "multiplier": 1.3, "posterior_sigma": 0.1}),
                json!({"step": 3, "multiplier": 0.7, "posterior_sigma": 0.1}),
            ],
            identifiability: Value::Null,
            consistency: Value::Null,
        };
        let used = use_fit(&document, &per_step);
        assert_eq!(used["schedule"][0]["flux"], 1.3);
        assert_eq!(used["schedule"][2]["flux"], 0.7);
    }

    #[test]
    fn readings_become_an_assay_at_the_step_time() {
        let result = ResultDocument::parse(
            json!({"steps": [
                {"step": 1, "t_s": 60.0, "inventory": [], "activity_Bq_per_g": {"Mn56": 1.0},
                 "heat_W_per_g": {"total": 0.0},
                 "uncertainty": {"responses": {"heat.total": {}, "activity:Mn56": {}}}}]}),
            "r".into(),
        )
        .unwrap();
        assert_eq!(banded(&result, 0), ["activity:Mn56", "heat.total"]);
        let mut state = MeasurementsState::default();
        assert!(assay_doc(&state, &result).is_err());
        state.readings.push(Reading {
            response: "activity:Mn56".into(),
            value: 2.0,
            sigma: 0.,
        });
        assert!(assay_doc(&state, &result)
            .unwrap_err()
            .contains("uncertainty"));
        state.readings[0].sigma = 0.1;
        assert_eq!(
            assay_doc(&state, &result).unwrap(),
            json!({"schema": "actinv-assay-1", "time_s": 60.0, "entries": [
                {"response": "activity:Mn56", "value": 2.0, "standard_uncertainty": 0.1}]})
        );
    }

    #[test]
    fn multipliers_read_with_four_figures() {
        assert_eq!(fixed(2.000000000001), "2.000");
        assert_eq!(fixed(0.011547), "0.01155");
        assert_eq!(fixed(1234.5), "1234");
    }
}
