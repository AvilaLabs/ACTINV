//! Optimize: which design, within the ranges given, does best on one quantity while meeting every
//! limit? The page writes an `actinv-optimize-1` search around the open problem and reads the
//! `actinv-optimize-result-1` answer: the best design, how it compares, and every design tried.
use crate::{
    model,
    views::{
        certify::{
            limit_editor, margin_words, response_words, step_times, three, Band, Limit, Quantity,
            StepTime,
        },
        Target,
    },
};
use eframe::egui::{self, RichText};
use egui_extras::{Column, TableBuilder};
use serde_json::{json, Value};

/// The page's question, under its heading.
pub const QUESTION: &str = "Which design, within the ranges you give, does best on one quantity while meeting every limit?";

/// The most evaluations one search may spend (`actinv optimize` refuses more).
const MAX_EVALS: usize = 64;
/// The most things one search may vary.
const MAX_CHANGES: usize = 6;

/// One thing the search may vary, and its range.
#[derive(Clone, Debug, PartialEq)]
pub enum Change {
    /// An element's weight percent; the other elements are scaled so the total stays 100.
    Element { symbol: String, from: f64, to: f64 },
    /// The total flux, as a multiple of the problem's.
    Flux { from: f64, to: f64 },
    /// One schedule step's length, as durations such as "30 d".
    Step {
        step: usize,
        from: String,
        to: String,
    },
}

impl Change {
    fn kind(&self) -> &'static str {
        match self {
            Change::Element { .. } => "Element content",
            Change::Flux { .. } => "Flux",
            Change::Step { .. } => "Step length",
        }
    }
}

/// What the search makes as small or as large as it can.
#[derive(Clone, Debug, PartialEq)]
pub struct Goal {
    pub lowest: bool,
    pub quantity: Quantity,
    pub nuclide: String,
    pub step: usize,
    pub band: Band,
}

impl Goal {
    fn response(&self) -> Result<String, String> {
        Limit {
            name: "The goal".into(),
            quantity: self.quantity,
            nuclide: self.nuclide.clone(),
            step: self.step,
            band: self.band,
            at_most: self.lowest,
            limit: 1.,
        }
        .response()
    }
}

/// A search result and the search that produced it (its `design_axes` say what each value means).
// Only the desktop loads results and uses designs; the browser sets up searches.
#[cfg_attr(target_arch = "wasm32", allow(dead_code))]
pub struct Found {
    pub path: std::path::PathBuf,
    pub result: Value,
    pub search: Value,
}

pub struct OptimizeState {
    pub changes: Vec<Change>,
    pub goal: Option<Goal>,
    pub limits: Vec<Limit>,
    /// spread-out designs tried first
    pub spread: usize,
    /// designs tried around the best so far
    pub refine: usize,
    pub found: Option<Found>,
}

impl Default for OptimizeState {
    fn default() -> Self {
        Self {
            changes: Vec::new(),
            goal: None,
            limits: Vec::new(),
            spread: 12,
            refine: 8,
            found: None,
        }
    }
}

#[cfg_attr(target_arch = "wasm32", allow(dead_code))]
impl OptimizeState {
    /// Set the form to the search that produced a result, so the two read together.
    pub fn show_search(&mut self, search: &Value, document: &Value) {
        let times = step_times(document);
        let step_at = |t: f64| {
            times
                .iter()
                .position(|s| (s.t_s - t).abs() <= 1e-6 * s.t_s.max(1.))
                .unwrap_or(times.len().saturating_sub(1))
        };
        let band = |edge: &Value| match edge.as_str() {
            Some(e) if e.starts_with("conservative") => Band::Conservative,
            Some(e) if e.starts_with("normal") => Band::Normal,
            _ => Band::Nominal,
        };
        let bounds = |axis: &Value| {
            (
                model::number(&axis["bounds"][0]),
                model::number(&axis["bounds"][1]),
            )
        };
        self.changes = search["design_axes"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(|axis| {
                let (from, to) = bounds(axis);
                Some(match axis["kind"].as_str()? {
                    "composition_fraction" => Change::Element {
                        symbol: element_symbol(axis["element"].as_str()?)?,
                        from,
                        to,
                    },
                    "flux_scale" => Change::Flux { from, to },
                    "step_dt" => Change::Step {
                        step: axis["step"].as_u64()? as usize,
                        from: format!("{from} s"),
                        to: format!("{to} s"),
                    },
                    _ => return None,
                })
            })
            .collect();
        let objective = &search["objective"];
        if let Some((quantity, nuclide)) = objective["response"].as_str().and_then(quantity_of) {
            self.goal = Some(Goal {
                lowest: objective["direction"] != "max",
                quantity,
                nuclide,
                step: step_at(model::number(&objective["time_s"])),
                band: band(&objective["edge"]),
            });
        }
        self.limits = search["constraints"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(|c| {
                let (quantity, nuclide) = quantity_of(c["response"].as_str()?)?;
                Some(Limit {
                    name: c["name"].as_str().unwrap_or("").to_owned(),
                    quantity,
                    nuclide,
                    step: step_at(model::number(&c["time_s"])),
                    band: band(&c["edge"]),
                    at_most: c["sense"] != "ge",
                    limit: model::number(&c["limit"]),
                })
            })
            .collect();
        let optimizer = &search["optimizer"];
        self.spread = optimizer["init_points"].as_u64().unwrap_or(12) as usize;
        self.refine = optimizer["refine_points"].as_u64().unwrap_or(8) as usize;
    }
}

/// The quantity a response name stands for, and its nuclide for a one-nuclide activity.
#[cfg_attr(target_arch = "wasm32", allow(dead_code))]
fn quantity_of(response: &str) -> Option<(Quantity, String)> {
    Some(match response {
        "heat.total" => (Quantity::Heat, String::new()),
        "heat.gamma" => (Quantity::GammaHeat, String::new()),
        "heat.beta" => (Quantity::BetaHeat, String::new()),
        "heat.alpha" => (Quantity::AlphaHeat, String::new()),
        "activity.total" => (Quantity::Activity, String::new()),
        other => (
            Quantity::NuclideActivity,
            other.strip_prefix("activity:")?.to_owned(),
        ),
    })
}

/// Whether the problem has a band to check limits against.
fn banded(document: &Value) -> bool {
    document["uncertainty"]["covariance"]["path"]
        .as_str()
        .is_some_and(|p| !p.trim().is_empty())
}

/// The element symbols in the composition, in their written spelling, largest first.
fn elements(document: &Value) -> Vec<(String, f64)> {
    let mut list: Vec<(String, f64)> = document["material"]["composition"]
        .as_object()
        .map(|c| {
            c.iter()
                .map(|(k, v)| (k.clone(), model::number(v)))
                .collect()
        })
        .unwrap_or_default();
    list.sort_by(|a, b| b.1.total_cmp(&a.1));
    list
}

/// "Fe" from "fe" or "FE"; `None` for an unknown element.
fn element_symbol(text: &str) -> Option<String> {
    let text = text.trim();
    if text.is_empty() || text.len() > 2 || !text.chars().all(|c| c.is_ascii_alphabetic()) {
        return None;
    }
    let mut symbol = text.to_ascii_lowercase();
    symbol[..1].make_ascii_uppercase();
    actinv_data::composition::z_of(&symbol)?;
    Some(symbol)
}

/// The composition key an element is written under, or its symbol when the material lacks it.
fn composition_key(document: &Value, symbol: &str) -> String {
    elements(document)
        .into_iter()
        .map(|(k, _)| k)
        .find(|k| k.eq_ignore_ascii_case(symbol))
        .unwrap_or_else(|| symbol.to_owned())
}

/// What the problem must have before a search can start. `None` when ready.
pub fn missing(document: &Value) -> Option<&'static str> {
    if elements(document).is_empty() {
        return Some("The material has no elements yet. Set it up first.");
    }
    if step_times(document).is_empty() {
        return Some("The schedule has no steps, or a duration does not read. Set it up first.");
    }
    None
}

/// The `actinv-optimize-1` search and its inline base problem: the document says what to vary and
/// what to aim for, the base problem (sent as `args.base_spec`) is the resolved open problem.
pub fn optimize_spec(
    mut run_spec: Value,
    document: &Value,
    state: &OptimizeState,
    times: &[StepTime],
) -> Result<(Value, Value), String> {
    if state.changes.is_empty() {
        return Err("Add at least one thing to vary.".into());
    }
    if state.changes.len() > MAX_CHANGES {
        return Err(format!("Vary at most {MAX_CHANGES} things at once."));
    }
    let goal = state.goal.as_ref().ok_or("Choose a goal.")?;
    let mut axes = Vec::new();
    let mut varied = Vec::new();
    let mut top_total = 0.;
    for change in &state.changes {
        axes.push(match change {
            Change::Element { symbol, from, to } => {
                if document["material"]["basis"] != "wt_percent" {
                    return Err("Varying an element needs the composition in weight percent; change it on the Material page.".into());
                }
                let symbol = element_symbol(symbol)
                    .ok_or_else(|| format!("'{}' is not an element symbol.", symbol.trim()))?;
                if varied.contains(&symbol) {
                    return Err(format!("{symbol} is varied twice."));
                }
                if !(0. ..100.).contains(from) || !(*to > *from && *to <= 100.) {
                    return Err(format!(
                        "{symbol}: the range must run from a lower to a higher value between 0 and 100 wt%."
                    ));
                }
                top_total += to;
                varied.push(symbol.clone());
                json!({"kind": "composition_fraction", "element": composition_key(document, &symbol), "bounds": [from, to]})
            }
            Change::Flux { from, to } => {
                if model::number(&document["spectrum"]["total"]) <= 0. {
                    return Err("Varying the flux needs the spectrum's total flux; set it on the Source & spectrum page.".into());
                }
                if axes.iter().any(|a: &Value| a["kind"] == "flux_scale") {
                    return Err("The flux is varied twice.".into());
                }
                if !(*from > 0. && to > from && to.is_finite()) {
                    return Err("Flux: the range must run from a lower to a higher positive multiple.".into());
                }
                json!({"kind": "flux_scale", "bounds": [from, to]})
            }
            Change::Step { step, from, to } => {
                if *step >= times.len() {
                    return Err("Step length: choose a step in the schedule.".into());
                }
                if axes.iter().any(|a: &Value| a["kind"] == "step_dt" && a["step"] == json!(step)) {
                    return Err(format!("Step {} is varied twice.", step + 1));
                }
                let read = |text: &str| {
                    actinv_core::spec::parse_duration(text.trim())
                        .ok()
                        .filter(|s| s.is_finite() && *s > 0.)
                };
                let (Some(lo), Some(hi)) = (read(from), read(to)) else {
                    return Err(format!("Step {}: write both lengths as durations, such as 30 d or 2 y.", step + 1));
                };
                if hi <= lo {
                    return Err(format!("Step {}: the second length must be longer than the first.", step + 1));
                }
                json!({"kind": "step_dt", "step": step, "bounds": [lo, hi]})
            }
        });
    }
    if top_total > 100. {
        return Err(
            "The elements varied could add up to more than 100 wt%; narrow their ranges.".into(),
        );
    }
    if !varied.is_empty()
        && elements(document)
            .iter()
            .all(|(k, v)| *v <= 0. || varied.iter().any(|s| s.eq_ignore_ascii_case(k)))
    {
        return Err("Leave at least one element of the material unvaried; it takes up the rest of the 100 wt%.".into());
    }
    let banded = banded(document);
    let mut responses = Vec::new();
    let objective_response = goal.response()?;
    let goal_time = times
        .get(goal.step)
        .ok_or("The goal: choose a time in the schedule.")?;
    let goal_band = if banded { goal.band } else { Band::Nominal };
    if goal_band != Band::Nominal {
        responses.push(objective_response.clone());
    }
    let mut constraints = Vec::new();
    for limit in &state.limits {
        if !limit.limit.is_finite() || limit.limit <= 0. {
            return Err(format!("{}: enter a positive limit.", limit.name));
        }
        let time = times
            .get(limit.step)
            .ok_or_else(|| format!("{}: choose a time in the schedule.", limit.name))?;
        let response = limit.response()?;
        let band = if banded { limit.band } else { Band::Nominal };
        if band != Band::Nominal {
            responses.push(response.clone());
        }
        constraints.push(json!({
            "name": limit.name.trim(),
            "response": response,
            "time_s": time.t_s,
            "edge": band.edge(limit.at_most),
            "sense": if limit.at_most { "le" } else { "ge" },
            "limit": limit.limit,
        }));
    }
    let mut names: Vec<&str> = state.limits.iter().map(|l| l.name.trim()).collect();
    names.sort_unstable();
    if names.windows(2).any(|w| w[0] == w[1]) || names.iter().any(|n| n.is_empty()) {
        return Err("Give every limit its own name.".into());
    }
    if !responses.is_empty() {
        let list = run_spec["uncertainty"]["responses"]
            .as_array_mut()
            .ok_or("the problem's uncertainty block has no response list")?;
        for response in responses {
            if !list.iter().any(|r| r == &json!(response)) {
                list.push(json!(response));
            }
        }
    }
    let (spread, refine) = (state.spread.max(1), state.refine);
    if spread + refine > MAX_EVALS {
        return Err(format!("A search may try at most {MAX_EVALS} designs."));
    }
    let search = json!({
        "schema": "actinv-optimize-1",
        "base_spec": "base_spec.json",
        "design_axes": axes,
        "objective": {
            "response": objective_response,
            "time_s": goal_time.t_s,
            "edge": goal_band.edge(goal.lowest),
            "direction": if goal.lowest { "min" } else { "max" },
        },
        "constraints": constraints,
        "optimizer": {
            "algorithm": "lhs_corners_coordinate",
            "seed": 1,
            "init_points": spread,
            "refine_points": refine,
        },
    });
    Ok((search, json!({"base_spec": run_spec})))
}

#[cfg_attr(target_arch = "wasm32", allow(dead_code))]
pub enum OptimizeAction {
    None,
    Search,
    /// Put a design into the problem: the search's `design_axes` and the design's values.
    UseDesign(Value, Vec<f64>),
    Open(Target),
}

/// `solves_here` is false in the browser, which writes the search file for `actinv optimize`.
pub fn show(
    ui: &mut egui::Ui,
    document: &Value,
    state: &mut OptimizeState,
    running: bool,
    solves_here: bool,
) -> OptimizeAction {
    let mut action = OptimizeAction::None;
    if let Some(why) = missing(document) {
        egui::Frame::group(ui.style())
            .inner_margin(12.)
            .show(ui, |ui| {
                ui.set_width(ui.available_width());
                ui.colored_label(ui.visuals().warn_fg_color, why);
                ui.horizontal(|ui| {
                    if ui.button("Open Material").clicked() {
                        action = OptimizeAction::Open(Target::Material);
                    }
                    if ui.button("Open Irradiation & cooling").clicked() {
                        action = OptimizeAction::Open(Target::Schedule);
                    }
                });
            });
        return action;
    }
    let times = step_times(document);
    let last = times.len() - 1;
    let banded = banded(document);
    let goal = state.goal.get_or_insert(Goal {
        lowest: true,
        quantity: Quantity::Activity,
        nuclide: String::new(),
        step: last,
        band: Band::Conservative,
    });
    if state.changes.is_empty() {
        let symbol = elements(document)
            .get(1)
            .or(elements(document).first())
            .map(|(k, _)| element_symbol(k).unwrap_or_else(|| k.clone()))
            .unwrap_or_default();
        state.changes.push(Change::Element {
            symbol,
            from: 0.,
            to: 1.,
        });
    }

    ui.heading("What the search may change");
    ui.label("Each search varies up to six things, each within the range you give. A varied element replaces its share of the material; the other elements are scaled so the total stays 100 wt%.");
    ui.add_space(6.);
    let composition = elements(document);
    let mut remove = None;
    for (i, change) in state.changes.iter_mut().enumerate() {
        egui::Frame::group(ui.style())
            .inner_margin(10.)
            .show(ui, |ui| {
                ui.set_width(ui.available_width());
                ui.horizontal_wrapped(|ui| {
                    let mut kind = change.kind();
                    egui::ComboBox::from_id_salt(("optimize-kind", i))
                        .selected_text(kind)
                        .show_ui(ui, |ui| {
                            for k in ["Element content", "Flux", "Step length"] {
                                ui.selectable_value(&mut kind, k, k);
                            }
                        });
                    if kind != change.kind() {
                        *change = match kind {
                            "Flux" => Change::Flux { from: 0.5, to: 2. },
                            "Step length" => Change::Step {
                                step: 0,
                                from: String::new(),
                                to: String::new(),
                            },
                            _ => Change::Element {
                                symbol: String::new(),
                                from: 0.,
                                to: 1.,
                            },
                        };
                    }
                    match change {
                        Change::Element { symbol, from, to } => {
                            egui::ComboBox::from_id_salt(("optimize-element", i))
                                .selected_text(if symbol.is_empty() {
                                    "choose"
                                } else {
                                    symbol.as_str()
                                })
                                .show_ui(ui, |ui| {
                                    for (k, wt) in &composition {
                                        let name = element_symbol(k).unwrap_or_else(|| k.clone());
                                        let label = format!("{name}  (now {} wt%)", three(*wt));
                                        ui.selectable_value(symbol, name, label);
                                    }
                                    ui.separator();
                                    ui.horizontal(|ui| {
                                        ui.label("Another element");
                                        ui.add(
                                            egui::TextEdit::singleline(symbol)
                                                .hint_text("Nb")
                                                .desired_width(50.),
                                        );
                                    });
                                });
                            ui.label("from");
                            ui.add(egui::DragValue::new(from).range(0. ..=100.).speed(0.01));
                            ui.label("to");
                            ui.add(egui::DragValue::new(to).range(0. ..=100.).speed(0.01));
                            ui.label("wt%");
                            let now = composition
                                .iter()
                                .find(|(k, _)| k.eq_ignore_ascii_case(symbol.trim()))
                                .map_or(0., |(_, v)| *v);
                            ui.weak(format!("now {} wt%", three(now)));
                        }
                        Change::Flux { from, to } => {
                            ui.label("from");
                            ui.add(egui::DragValue::new(from).range(0. ..=1e6).speed(0.01));
                            ui.label("to");
                            ui.add(egui::DragValue::new(to).range(0. ..=1e6).speed(0.01));
                            ui.label("times the problem's flux");
                        }
                        Change::Step { step, from, to } => {
                            *step = (*step).min(last);
                            egui::ComboBox::from_id_salt(("optimize-step", i))
                                .selected_text(format!("step {}", *step + 1))
                                .show_ui(ui, |ui| {
                                    for (s, step_doc) in document["schedule"]
                                        .as_array()
                                        .into_iter()
                                        .flatten()
                                        .enumerate()
                                    {
                                        let dt = step_doc["dt"].as_str().unwrap_or("?");
                                        ui.selectable_value(
                                            step,
                                            s,
                                            format!("step {}: {dt}", s + 1),
                                        );
                                    }
                                });
                            ui.label("from");
                            ui.add(
                                egui::TextEdit::singleline(from)
                                    .hint_text("30 d")
                                    .desired_width(80.),
                            );
                            ui.label("to");
                            ui.add(
                                egui::TextEdit::singleline(to)
                                    .hint_text("2 y")
                                    .desired_width(80.),
                            );
                        }
                    }
                    if ui.small_button("Remove").clicked() {
                        remove = Some(i);
                    }
                });
            });
        ui.add_space(4.);
    }
    if let Some(i) = remove {
        state.changes.remove(i);
    }
    if state.changes.len() < MAX_CHANGES && ui.button("+ Something to vary").clicked() {
        state.changes.push(Change::Element {
            symbol: String::new(),
            from: 0.,
            to: 1.,
        });
    }

    ui.add_space(14.);
    ui.heading("Goal");
    egui::Frame::group(ui.style())
        .inner_margin(10.)
        .show(ui, |ui| {
            ui.set_width(ui.available_width());
            ui.horizontal_wrapped(|ui| {
                egui::ComboBox::from_id_salt("optimize-direction")
                    .selected_text(if goal.lowest { "Lowest" } else { "Highest" })
                    .show_ui(ui, |ui| {
                        ui.selectable_value(&mut goal.lowest, true, "Lowest");
                        ui.selectable_value(&mut goal.lowest, false, "Highest");
                    });
                egui::ComboBox::from_id_salt("optimize-quantity")
                    .selected_text(goal.quantity.label())
                    .show_ui(ui, |ui| {
                        for q in Quantity::ALL {
                            ui.selectable_value(&mut goal.quantity, q, q.label());
                        }
                    });
                if goal.quantity == Quantity::NuclideActivity {
                    ui.add(
                        egui::TextEdit::singleline(&mut goal.nuclide)
                            .hint_text("Co60")
                            .desired_width(70.),
                    );
                }
                ui.label("at");
                goal.step = goal.step.min(last);
                egui::ComboBox::from_id_salt("optimize-time")
                    .selected_text(&times[goal.step].label)
                    .show_ui(ui, |ui| {
                        for (s, time) in times.iter().enumerate() {
                            ui.selectable_value(
                                &mut goal.step,
                                s,
                                format!("step {}: {}", s + 1, time.label),
                            );
                        }
                    });
                if banded {
                    ui.label("judged at the");
                    egui::ComboBox::from_id_salt("optimize-band")
                        .selected_text(goal.band.label())
                        .show_ui(ui, |ui| {
                            for b in Band::ALL {
                                ui.selectable_value(&mut goal.band, b, b.label());
                            }
                        });
                }
            });
        });

    ui.add_space(14.);
    ui.heading("Limits");
    ui.label(if banded {
        "Optional. A design counts only if it meets every limit, at the band edge you choose for it."
    } else {
        "Optional. A design counts only if it meets every limit. The problem has no uncertainty, so limits are checked on the nominal value; turn it on under What to compute to check band edges."
    });
    ui.add_space(6.);
    limit_editor(ui, "optimize", &mut state.limits, &times, banded);
    if ui.button("+ Limit").clicked() {
        let n = state.limits.len();
        state.limits.push(Limit::new(last, n));
    }

    ui.add_space(14.);
    ui.heading("Search");
    ui.horizontal_wrapped(|ui| {
        ui.label("Try");
        ui.add(egui::DragValue::new(&mut state.spread).range(1..=MAX_EVALS));
        ui.label("designs spread over the ranges, then");
        ui.add(egui::DragValue::new(&mut state.refine).range(0..=MAX_EVALS));
        ui.label("more around the best so far.");
    });
    let total = state.spread + state.refine;
    let per = if banded {
        "one solve with bands"
    } else {
        "one solve"
    };
    if total > MAX_EVALS {
        ui.colored_label(
            ui.visuals().error_fg_color,
            format!("{total} designs: a search may try at most {MAX_EVALS}."),
        );
    } else {
        ui.weak(format!(
            "{total} designs, {per} each. The search is seeded, so the same inputs try the same designs."
        ));
    }
    ui.add_space(8.);
    let (label, hover) = if solves_here {
        ("Search designs", "Solves every design in the isolated worker; the status bar shows progress and can cancel")
    } else {
        (
            "Download the search file",
            "The browser does not solve. Run actinv optimize search.json out beside the nuclear data, or use the desktop app.",
        )
    };
    if ui
        .add_enabled(
            !running && total <= MAX_EVALS,
            egui::Button::new(RichText::new(label).strong()),
        )
        .on_hover_text(hover)
        .clicked()
    {
        action = OptimizeAction::Search;
    }
    if !solves_here {
        ui.weak("The browser sets up the search; the desktop app or `actinv optimize search.json out` runs it.");
    }
    if let Some(found) = &state.found {
        ui.add_space(16.);
        if let Some(use_it) = answer(ui, document, found, &times, solves_here) {
            action = use_it;
        }
    }
    action
}

/// The value a design axis has in the open problem, for comparison.
fn current(document: &Value, axis: &Value) -> Option<f64> {
    match axis["kind"].as_str()? {
        "composition_fraction" => {
            let element = axis["element"].as_str()?;
            Some(
                elements(document)
                    .iter()
                    .find(|(k, _)| k.eq_ignore_ascii_case(element))
                    .map_or(0., |(_, v)| *v),
            )
        }
        "flux_scale" => Some(1.),
        "step_dt" => {
            let step = axis["step"].as_u64()? as usize;
            actinv_core::spec::parse_duration(document["schedule"][step]["dt"].as_str()?).ok()
        }
        _ => None,
    }
}

/// A design value in the axis's own terms.
fn axis_value(axis: &Value, x: f64) -> String {
    match axis["kind"].as_str() {
        Some("composition_fraction") => format!("{} wt%", three(x)),
        Some("flux_scale") => format!("{} times", three(x)),
        Some("step_dt") => model::readable_time(x),
        _ => three(x),
    }
}

/// The column title for a design axis.
fn axis_title(axis: &Value) -> String {
    match axis["kind"].as_str() {
        Some("composition_fraction") => axis["element"]
            .as_str()
            .and_then(element_symbol)
            .unwrap_or_default(),
        Some("flux_scale") => "Flux".into(),
        Some("step_dt") => format!("Step {} length", axis["step"].as_u64().unwrap_or(0) + 1),
        _ => "?".into(),
    }
}

/// The answer: the best design and how it compares, then every design tried.
fn answer(
    ui: &mut egui::Ui,
    document: &Value,
    found: &Found,
    times: &[StepTime],
    solves_here: bool,
) -> Option<OptimizeAction> {
    let mut action = None;
    let result = &found.result;
    let axes = found.search["design_axes"]
        .as_array()
        .cloned()
        .unwrap_or_default();
    let objective = &result["objective"];
    let response = objective["response"].as_str().unwrap_or("");
    let unit = if response.starts_with("heat") {
        "W/g"
    } else {
        "Bq/g"
    };
    let when = times
        .iter()
        .find(|t| (t.t_s - model::number(&objective["time_s"])).abs() <= 1e-6 * t.t_s.max(1.))
        .map_or_else(
            || model::readable_time(model::number(&objective["time_s"])),
            |t| t.label.clone(),
        );
    let goal_words = format!("{}, {when}", response_words(response));
    let infeasible = result["infeasible"] == true || result["best_feasible"].is_null();
    let best_x: Vec<f64> = result["best_feasible"]["x"]
        .as_array()
        .map(|x| x.iter().map(model::number).collect())
        .unwrap_or_default();
    egui::Frame::group(ui.style()).inner_margin(14.).show(ui, |ui| {
        ui.set_width(ui.available_width());
        if infeasible {
            ui.label(RichText::new("No design meets every limit").heading().color(ui.visuals().error_fg_color));
            ui.label("None of the designs tried met all the limits. Widen the ranges, loosen a limit, or try more designs; the closest ones are listed below.");
        } else {
            ui.label(RichText::new("Best design found").heading().color(crate::visuals::accent(ui)));
            ui.label(format!(
                "{goal_words}: {} {unit}, the {} of {} designs tried.",
                three(model::number(&result["best_feasible"]["objective"])),
                if objective["direction"] == "max" { "highest" } else { "lowest" },
                result["n_evals"].as_u64().unwrap_or(0),
            ));
            if result["winner_verification"]["bit_identical"] == true {
                ui.weak("A fresh solve of this design reproduced the value exactly.");
            }
        }
        ui.weak(format!(
            "Searched in {}.",
            model::readable_time(model::number(&result["wall_s"]))
        ));
    });
    if !infeasible && best_x.len() == axes.len() {
        ui.add_space(8.);
        TableBuilder::new(ui)
            .id_salt("optimize-best")
            .striped(true)
            .cell_layout(egui::Layout::left_to_right(egui::Align::Center))
            .column(Column::auto().at_least(140.))
            .column(Column::auto().at_least(140.))
            .column(Column::remainder())
            .header(26., |mut h| {
                for title in ["Varied", "Best design", "The problem now"] {
                    h.col(|ui| {
                        ui.strong(title);
                    });
                }
            })
            .body(|mut body| {
                for (axis, x) in axes.iter().zip(&best_x) {
                    body.row(28., |mut row| {
                        row.col(|ui| {
                            ui.label(axis_title(axis));
                        });
                        row.col(|ui| {
                            ui.label(axis_value(axis, *x));
                        });
                        row.col(|ui| {
                            ui.label(
                                current(document, axis).map_or("?".into(), |v| axis_value(axis, v)),
                            );
                        });
                    });
                }
            });
        ui.add_space(6.);
        if solves_here
            && ui
                .button("Use this design")
                .on_hover_text("Puts these values into the problem; Undo edit takes them back out")
                .clicked()
        {
            action = Some(OptimizeAction::UseDesign(json!(axes), best_x.clone()));
        }
    }
    let ranked = result["ranked"].as_array().cloned().unwrap_or_default();
    if !ranked.is_empty() {
        ui.add_space(10.);
        egui::CollapsingHeader::new(format!("Every design tried ({})", ranked.len()))
            .id_salt("optimize-ranked")
            .default_open(infeasible)
            .show(ui, |ui| {
                let mut table = TableBuilder::new(ui)
                    .id_salt("optimize-ranked-table")
                    .striped(true)
                    .cell_layout(egui::Layout::left_to_right(egui::Align::Center))
                    .column(Column::auto().at_least(40.));
                for _ in &axes {
                    table = table.column(Column::auto().at_least(90.));
                }
                table
                    .column(Column::auto().at_least(120.))
                    .column(Column::remainder())
                    .header(26., |mut h| {
                        h.col(|ui| {
                            ui.strong("Rank");
                        });
                        for axis in &axes {
                            h.col(|ui| {
                                ui.strong(axis_title(axis));
                            });
                        }
                        h.col(|ui| {
                            ui.strong(format!("Goal ({unit})"));
                        });
                        h.col(|ui| {
                            ui.strong("Limits");
                        });
                    })
                    .body(|mut body| {
                        for (rank, row_value) in ranked.iter().enumerate() {
                            body.row(26., |mut row| {
                                row.col(|ui| {
                                    ui.label(format!("{}", rank + 1));
                                });
                                let x = row_value["x"].as_array().cloned().unwrap_or_default();
                                for (axis, v) in axes
                                    .iter()
                                    .zip(x.iter().chain(std::iter::repeat(&Value::Null)))
                                {
                                    row.col(|ui| {
                                        ui.label(axis_value(axis, model::number(v)));
                                    });
                                }
                                row.col(|ui| match row_value["objective"].as_f64() {
                                    Some(v) => {
                                        ui.label(three(v));
                                    }
                                    None => {
                                        ui.weak("not computed");
                                    }
                                });
                                row.col(|ui| {
                                    if row_value["feasible"] == true {
                                        ui.label("all met");
                                    } else if row_value["status"]
                                        .as_str()
                                        .is_some_and(|s| s.contains("axis"))
                                    {
                                        ui.weak("outside a range limit");
                                    } else {
                                        match row_value["violation_sum"].as_f64() {
                                            Some(v) if v.is_finite() => {
                                                ui.colored_label(
                                                    ui.visuals().error_fg_color,
                                                    margin_words(v),
                                                );
                                            }
                                            _ => {
                                                ui.colored_label(
                                                    ui.visuals().error_fg_color,
                                                    "not computable",
                                                );
                                            }
                                        }
                                    }
                                });
                            });
                        }
                    });
            });
    }
    action
}

#[cfg(test)]
mod tests {
    use super::*;

    fn problem() -> Value {
        let mut doc = model::decode_problem(model::EXAMPLE).unwrap();
        doc["material"]["composition"] = json!({"FE": 91.0, "CR": 9.0});
        doc["schedule"] = json!([
            {"dt": "1 y", "flux": 1.0},
            {"dt": "100 y", "flux": 0.0}
        ]);
        doc
    }

    fn state() -> OptimizeState {
        OptimizeState {
            changes: vec![
                Change::Element {
                    symbol: "ni".into(),
                    from: 0.,
                    to: 3.,
                },
                Change::Flux { from: 0.5, to: 2. },
                Change::Step {
                    step: 0,
                    from: "180 d".into(),
                    to: "2 y".into(),
                },
            ],
            goal: Some(Goal {
                lowest: true,
                quantity: Quantity::Activity,
                nuclide: String::new(),
                step: 1,
                band: Band::Conservative,
            }),
            limits: vec![Limit {
                limit: 1e-6,
                ..Limit::new(1, 0)
            }],
            ..OptimizeState::default()
        }
    }

    #[test]
    fn the_search_is_a_valid_optimize_spec() {
        let doc = problem();
        let times = step_times(&doc);
        let (search, args) = optimize_spec(doc.clone(), &doc, &state(), &times).unwrap();
        let dir = std::env::temp_dir().join(format!("actinv-gui-optimize-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("search.json");
        std::fs::write(&path, search.to_string()).unwrap();
        let parsed = actinv_cli::optimize::load_optimize_spec(&path);
        std::fs::remove_dir_all(&dir).unwrap();
        parsed.unwrap();
        assert_eq!(search["design_axes"][0]["element"], "Ni");
        assert_eq!(
            search["design_axes"][2]["bounds"][1],
            json!(2. * 365.25 * 86400.)
        );
        // no uncertainty: everything is judged on the nominal value
        assert_eq!(search["objective"]["edge"], "nominal");
        assert_eq!(search["constraints"][0]["edge"], "nominal");
        assert_eq!(args["base_spec"], doc);
    }

    #[test]
    fn band_edges_add_their_responses_to_the_uncertainty() {
        let mut doc = problem();
        doc["uncertainty"] =
            json!({"covariance": {"path": "cov.npz", "sha256": "ab"}, "responses": ["heat.total"]});
        let times = step_times(&doc);
        let (search, args) = optimize_spec(doc.clone(), &doc, &state(), &times).unwrap();
        assert_eq!(search["objective"]["edge"], "conservative_upper");
        assert_eq!(
            args["base_spec"]["uncertainty"]["responses"],
            json!(["heat.total", "activity.total"])
        );
    }

    #[test]
    fn impossible_searches_are_refused_in_words() {
        let doc = problem();
        let times = step_times(&doc);
        let refused = |change: Change| {
            let mut s = state();
            s.changes = vec![change];
            optimize_spec(doc.clone(), &doc, &s, &times).unwrap_err()
        };
        assert!(refused(Change::Element {
            symbol: "Xx".into(),
            from: 0.,
            to: 1.
        })
        .contains("not an element"));
        assert!(refused(Change::Element {
            symbol: "Fe".into(),
            from: 50.,
            to: 40.
        })
        .contains("range"));
        assert!(refused(Change::Step {
            step: 0,
            from: "2 y".into(),
            to: "1 y".into()
        })
        .contains("longer"));
        assert!(refused(Change::Flux { from: 0., to: 2. }).contains("Flux"));
        let mut s = state();
        s.changes = vec![
            Change::Element {
                symbol: "Fe".into(),
                from: 0.,
                to: 90.,
            },
            Change::Element {
                symbol: "Cr".into(),
                from: 0.,
                to: 10.,
            },
        ];
        assert!(optimize_spec(doc.clone(), &doc, &s, &times)
            .unwrap_err()
            .contains("unvaried"));
        s.changes = vec![Change::Element {
            symbol: "Ni".into(),
            from: 0.,
            to: 3.,
        }];
        s.spread = 60;
        s.refine = 10;
        assert!(optimize_spec(doc.clone(), &doc, &s, &times)
            .unwrap_err()
            .contains("at most 64"));
    }

    #[test]
    fn a_search_reads_back_into_the_form() {
        let doc = problem();
        let times = step_times(&doc);
        let original = state();
        let (search, _) = optimize_spec(doc.clone(), &doc, &original, &times).unwrap();
        let mut shown = OptimizeState::default();
        shown.show_search(&search, &doc);
        let (again, _) = optimize_spec(doc.clone(), &doc, &shown, &times).unwrap();
        assert_eq!(again, search);
        assert_eq!(
            shown.goal,
            original.goal.map(|g| Goal {
                band: Band::Nominal,
                ..g
            })
        );
    }

    #[test]
    fn design_values_read_in_their_own_terms() {
        let doc = problem();
        let element = json!({"kind": "composition_fraction", "element": "CR", "bounds": [0, 10]});
        assert_eq!(axis_title(&element), "Cr");
        assert_eq!(current(&doc, &element), Some(9.));
        assert_eq!(axis_value(&element, 0.11328), "0.113 wt%");
        let step = json!({"kind": "step_dt", "step": 0, "bounds": [1, 2]});
        assert_eq!(axis_title(&step), "Step 1 length");
        assert_eq!(current(&doc, &step), Some(365.25 * 86400.));
        assert_eq!(axis_value(&json!({"kind": "flux_scale"}), 1.5), "1.5 times");
    }
}
