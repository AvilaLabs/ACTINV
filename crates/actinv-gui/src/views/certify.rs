//! Certify: does the design meet its limits once the nuclear-data uncertainty is counted? The page
//! writes an `actinv-decide-1` decision around the open problem and reads the `actinv-decision-1`
//! answer: a verdict, each limit's margin at its band edge, and what to measure next.
use crate::{model, views::Target};
use eframe::egui::{self, RichText};
use egui_extras::{Column, TableBuilder};
use serde_json::{json, Value};

/// What a limit is on. Values are per gram, as the result reports them.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Quantity {
    Heat,
    GammaHeat,
    BetaHeat,
    AlphaHeat,
    Activity,
    NuclideActivity,
}

impl Quantity {
    const ALL: [Quantity; 6] = [
        Quantity::Heat,
        Quantity::GammaHeat,
        Quantity::BetaHeat,
        Quantity::AlphaHeat,
        Quantity::Activity,
        Quantity::NuclideActivity,
    ];

    fn label(self) -> &'static str {
        match self {
            Quantity::Heat => "Decay heat",
            Quantity::GammaHeat => "Gamma heat",
            Quantity::BetaHeat => "Beta heat",
            Quantity::AlphaHeat => "Alpha heat",
            Quantity::Activity => "Activity",
            Quantity::NuclideActivity => "Activity of one nuclide",
        }
    }

    fn unit(self) -> &'static str {
        match self {
            Quantity::Activity | Quantity::NuclideActivity => "Bq/g",
            _ => "W/g",
        }
    }
}

/// How much of the nuclear-data band a limit must clear.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Band {
    Conservative,
    Normal,
    Nominal,
}

impl Band {
    const ALL: [Band; 3] = [Band::Conservative, Band::Normal, Band::Nominal];

    fn label(self) -> &'static str {
        match self {
            Band::Conservative => "Conservative band edge",
            Band::Normal => "Normal band edge",
            Band::Nominal => "Nominal value only",
        }
    }

    /// The edge `decide` evaluates: the upper edge for an upper limit, the lower for a lower one.
    fn edge(self, at_most: bool) -> &'static str {
        match (self, at_most) {
            (Band::Nominal, _) => "nominal",
            (Band::Conservative, true) => "conservative_upper",
            (Band::Conservative, false) => "conservative_lower",
            (Band::Normal, true) => "normal_upper",
            (Band::Normal, false) => "normal_lower",
        }
    }
}

/// One limit the design must meet.
#[derive(Clone, Debug, PartialEq)]
pub struct Limit {
    pub name: String,
    pub quantity: Quantity,
    pub nuclide: String,
    /// index into the schedule's step times
    pub step: usize,
    pub band: Band,
    /// true for "at most", false for "at least"
    pub at_most: bool,
    pub limit: f64,
}

impl Limit {
    fn new(step: usize, index: usize) -> Self {
        Self {
            name: format!("Limit {}", index + 1),
            quantity: Quantity::Heat,
            nuclide: String::new(),
            step,
            band: Band::Conservative,
            at_most: true,
            limit: 0.,
        }
    }

    /// The response name `decide` and the uncertainty block use.
    fn response(&self) -> Result<String, String> {
        Ok(match self.quantity {
            Quantity::Heat => "heat.total".into(),
            Quantity::GammaHeat => "heat.gamma".into(),
            Quantity::BetaHeat => "heat.beta".into(),
            Quantity::AlphaHeat => "heat.alpha".into(),
            Quantity::Activity => "activity.total".into(),
            Quantity::NuclideActivity => {
                let nuclide = nuclide_key(&self.nuclide)
                    .ok_or_else(|| format!("{}: name the nuclide, such as Co60.", self.name))?;
                format!("activity:{nuclide}")
            }
        })
    }
}

/// A typed nuclide in the result's spelling: `co60` and `CO60` become `Co60`, `nb94M1` becomes `Nb94m1`.
fn nuclide_key(text: &str) -> Option<String> {
    let text = text.trim();
    let letters: String = text
        .chars()
        .take_while(|c| c.is_ascii_alphabetic())
        .collect();
    let rest = &text[letters.len()..];
    let digits: String = rest.chars().take_while(|c| c.is_ascii_digit()).collect();
    let state = rest[digits.len()..].to_ascii_lowercase();
    if letters.is_empty() || letters.len() > 2 || digits.is_empty() {
        return None;
    }
    if !(state.is_empty()
        || state
            .strip_prefix('m')
            .is_some_and(|n| n.chars().all(|c| c.is_ascii_digit())))
    {
        return None;
    }
    let mut symbol = letters.to_ascii_lowercase();
    symbol[..1].make_ascii_uppercase();
    actinv_data::composition::z_of(&symbol)?;
    Some(format!("{symbol}{digits}{state}"))
}

/// The end time of a schedule step and when that is, in words.
#[derive(Clone, Debug, PartialEq)]
pub struct StepTime {
    pub t_s: f64,
    pub label: String,
}

fn irradiates(step: &Value) -> bool {
    model::number(&step["flux"]) > 0.
        || step["power"].is_object()
        || step["wall_loading"].is_object()
}

/// Every step's end time, labelled like the results: during irradiation, at shutdown, or how long
/// after shutdown. Empty when a duration does not parse.
pub fn step_times(document: &Value) -> Vec<StepTime> {
    let Some(steps) = document["schedule"].as_array() else {
        return Vec::new();
    };
    let mut t = 0.;
    let mut ends = Vec::with_capacity(steps.len());
    for step in steps {
        match step["dt"].as_str().map(actinv_core::spec::parse_duration) {
            Some(Ok(dt)) => {
                t += dt;
                ends.push(t);
            }
            _ => return Vec::new(),
        }
    }
    let last_on = steps.iter().rposition(irradiates);
    ends.iter()
        .enumerate()
        .map(|(i, &t_s)| {
            let label = match steps[..=i].iter().rposition(irradiates) {
                Some(on) if on == i && last_on != Some(i) => {
                    format!(
                        "during irradiation, {} after start",
                        model::readable_time(t_s)
                    )
                }
                Some(on) if on == i => "at shutdown".into(),
                Some(on) => format!("{} after shutdown", model::readable_time(t_s - ends[on])),
                None => format!("{} after start", model::readable_time(t_s)),
            };
            StepTime { t_s, label }
        })
        .collect()
}

#[derive(Default)]
pub struct CertifyState {
    pub limits: Vec<Limit>,
    /// how many measurement suggestions to list per limit
    pub top: usize,
    /// the decision shown and the file it came from
    pub decision: Option<(std::path::PathBuf, Value)>,
}

/// What a limit needs from the problem before `decide` can run: a band needs uncertainty with a
/// covariance file. `None` when ready.
pub fn missing(document: &Value) -> Option<&'static str> {
    let uncertainty = &document["uncertainty"];
    if !uncertainty.is_object() {
        return Some("Certifying counts the nuclear-data uncertainty, which is off for this problem. Turn it on under What to compute, with a covariance file.");
    }
    if uncertainty["covariance"]["path"]
        .as_str()
        .is_none_or(|p| p.trim().is_empty())
    {
        return Some("Certifying needs the cross-section covariance file (the flux-only mode cannot certify). Choose one under What to compute.");
    }
    None
}

/// The `actinv-decide-1` document: the resolved problem embedded as the run spec, with each limit's
/// response added to its uncertainty responses so the band exists.
pub fn decide_spec(
    mut run_spec: Value,
    limits: &[Limit],
    times: &[StepTime],
    top: usize,
) -> Result<Value, String> {
    if limits.is_empty() {
        return Err("Add at least one limit.".into());
    }
    let mut constraints = Vec::new();
    for limit in limits {
        if !limit.limit.is_finite() || limit.limit <= 0. {
            return Err(format!("{}: enter a positive limit.", limit.name));
        }
        let time = times
            .get(limit.step)
            .ok_or_else(|| format!("{}: choose a time in the schedule.", limit.name))?;
        let response = limit.response()?;
        let responses = run_spec["uncertainty"]["responses"]
            .as_array_mut()
            .ok_or("the problem's uncertainty block has no response list")?;
        if !responses.iter().any(|r| r == &json!(response)) {
            responses.push(json!(response));
        }
        constraints.push(json!({
            "name": limit.name.trim(),
            "response": response,
            "time_s": time.t_s,
            "edge": limit.band.edge(limit.at_most),
            "sense": if limit.at_most { "le" } else { "ge" },
            "limit": limit.limit,
        }));
    }
    let mut names: Vec<&str> = limits.iter().map(|l| l.name.trim()).collect();
    names.sort_unstable();
    if names.windows(2).any(|w| w[0] == w[1]) || names.iter().any(|n| n.is_empty()) {
        return Err("Give every limit its own name.".into());
    }
    Ok(json!({
        "schema": "actinv-decide-1",
        "run_spec": run_spec,
        "decision": {"constraints": constraints, "measurement_top": top.clamp(1, 20)},
    }))
}

pub enum CertifyAction {
    None,
    Certify,
    Open(Target),
}

/// `solves_here` is false in the browser, which writes the decision file for `actinv decide` instead.
pub fn show(
    ui: &mut egui::Ui,
    document: &Value,
    state: &mut CertifyState,
    running: bool,
    solves_here: bool,
) -> CertifyAction {
    let mut action = CertifyAction::None;
    if state.top == 0 {
        state.top = 5;
    }
    let times = step_times(document);
    if let Some(why) = missing(document) {
        egui::Frame::group(ui.style())
            .inner_margin(12.)
            .show(ui, |ui| {
                ui.set_width(ui.available_width());
                ui.colored_label(ui.visuals().warn_fg_color, why);
                if ui.button("Open What to compute").clicked() {
                    action = CertifyAction::Open(Target::Outputs);
                }
            });
        ui.add_space(12.);
    }
    if times.is_empty() {
        ui.colored_label(
            ui.visuals().error_fg_color,
            "The schedule has no steps, or a duration does not read; limits are set at the end of a step.",
        );
        return action;
    }
    if state.limits.is_empty() {
        state.limits.push(Limit::new(times.len() - 1, 0));
    }
    ui.heading("Limits");
    ui.label("Each limit is checked at the end of one step, at the edge of the nuclear-data band you choose. Values are per gram of material.");
    ui.add_space(6.);
    let mut remove = None;
    for (i, limit) in state.limits.iter_mut().enumerate() {
        egui::Frame::group(ui.style())
            .inner_margin(10.)
            .show(ui, |ui| {
                ui.set_width(ui.available_width());
                ui.horizontal_wrapped(|ui| {
                    ui.add(egui::TextEdit::singleline(&mut limit.name).desired_width(140.));
                    egui::ComboBox::from_id_salt(("certify-quantity", i))
                        .selected_text(limit.quantity.label())
                        .show_ui(ui, |ui| {
                            for q in Quantity::ALL {
                                ui.selectable_value(&mut limit.quantity, q, q.label());
                            }
                        });
                    if limit.quantity == Quantity::NuclideActivity {
                        ui.add(
                            egui::TextEdit::singleline(&mut limit.nuclide)
                                .hint_text("Co60")
                                .desired_width(70.),
                        );
                    }
                    egui::ComboBox::from_id_salt(("certify-sense", i))
                        .selected_text(if limit.at_most { "at most" } else { "at least" })
                        .show_ui(ui, |ui| {
                            ui.selectable_value(&mut limit.at_most, true, "at most");
                            ui.selectable_value(&mut limit.at_most, false, "at least");
                        });
                    ui.add(
                        egui::DragValue::new(&mut limit.limit)
                            .custom_parser(model::parse_finite)
                            .custom_formatter(|n, _| crate::visuals::compact_number(n))
                            .speed(0.01),
                    );
                    ui.label(limit.quantity.unit());
                    if ui.small_button("Remove").clicked() {
                        remove = Some(i);
                    }
                });
                ui.horizontal_wrapped(|ui| {
                    ui.label("at");
                    limit.step = limit.step.min(times.len() - 1);
                    egui::ComboBox::from_id_salt(("certify-time", i))
                        .selected_text(&times[limit.step].label)
                        .show_ui(ui, |ui| {
                            for (s, time) in times.iter().enumerate() {
                                ui.selectable_value(
                                    &mut limit.step,
                                    s,
                                    format!("step {}: {}", s + 1, time.label),
                                );
                            }
                        });
                    ui.label("checked at the");
                    egui::ComboBox::from_id_salt(("certify-band", i))
                        .selected_text(limit.band.label())
                        .show_ui(ui, |ui| {
                            for b in Band::ALL {
                                ui.selectable_value(&mut limit.band, b, b.label());
                            }
                        });
                });
            });
        ui.add_space(4.);
    }
    if let Some(i) = remove {
        state.limits.remove(i);
    }
    ui.horizontal_wrapped(|ui| {
        if ui.button("+ Limit").clicked() {
            let n = state.limits.len();
            state.limits.push(Limit::new(times.len() - 1, n));
        }
        ui.label("Suggest up to");
        ui.add(egui::DragValue::new(&mut state.top).range(1..=20));
        ui.label("measurements per limit");
    });
    ui.weak("The conservative edge adds the declared unmodelled share to the band; the normal edge is the band at the problem's confidence level. Nominal ignores the uncertainty, and is offered only for comparison.");
    ui.add_space(8.);
    let ready = missing(document).is_none() && !state.limits.is_empty();
    let (label, hover) = if solves_here {
        (
            "Certify",
            "Solves the problem once with bands, then checks every limit",
        )
    } else {
        (
            "Download the decision file",
            "The browser does not solve. Run actinv decide decision.json verdict.json beside the nuclear data, or use the desktop app.",
        )
    };
    if ui
        .add_enabled(
            ready && !running,
            egui::Button::new(RichText::new(label).strong()),
        )
        .on_hover_text(hover)
        .clicked()
    {
        action = CertifyAction::Certify;
    }
    if !solves_here {
        ui.weak("The browser sets up the decision; the desktop app or `actinv decide decision.json verdict.json` solves it.");
    }
    if let Some((_, decision)) = &state.decision {
        ui.add_space(16.);
        verdict(ui, decision, &times);
    }
    action
}

/// The answer: the verdict, each limit's margin, and what to measure next.
fn verdict(ui: &mut egui::Ui, decision: &Value, times: &[StepTime]) {
    let certified = decision["verdict"]["certified"].as_bool().unwrap_or(false);
    let constraints = decision["constraints"]
        .as_array()
        .cloned()
        .unwrap_or_default();
    let holding = constraints
        .iter()
        .filter(|c| c["satisfied"] == true)
        .count();
    egui::Frame::group(ui.style()).inner_margin(14.).show(ui, |ui| {
        ui.set_width(ui.available_width());
        let (title, colour) = if certified {
            ("Certified", crate::visuals::accent(ui))
        } else {
            ("Not certified", ui.visuals().error_fg_color)
        };
        ui.label(RichText::new(title).heading().color(colour));
        let confidence = decision["uncertainty_provenance"]["confidence_level"].as_f64().unwrap_or(0.95);
        ui.label(format!(
            "{holding} of {} limits hold at their band edges, with the nuclear-data uncertainty at {:.0}% confidence.",
            constraints.len(),
            confidence * 100.
        ));
        let over = decision["verdict"]["nominal_would_overcertify"].as_u64().unwrap_or(0);
        if over > 0 {
            ui.colored_label(
                ui.visuals().warn_fg_color,
                format!("{over} of these pass on the nominal value but fail once the uncertainty is counted: a calculation without bands would have certified them."),
            );
        }
        if decision["completeness"]["status"] == "incomplete" {
            ui.colored_label(
                ui.visuals().warn_fg_color,
                "The run is incomplete: some reaction or decay data were missing, so the band may be too narrow. Run the problem itself and read Evidence for the gaps.",
            );
        }
    });
    ui.add_space(8.);
    TableBuilder::new(ui)
        .id_salt("certify-margins")
        .striped(true)
        .cell_layout(egui::Layout::left_to_right(egui::Align::Center))
        .column(Column::auto().at_least(120.))
        .column(Column::auto().at_least(200.))
        .column(Column::auto().at_least(110.))
        .column(Column::auto().at_least(110.))
        .column(Column::auto().at_least(110.))
        .column(Column::remainder())
        .header(26., |mut h| {
            for title in [
                "Name",
                "Quantity and time",
                "Value at the band edge",
                "Limit",
                "Margin",
                "Without uncertainty",
            ] {
                h.col(|ui| {
                    ui.strong(title);
                });
            }
        })
        .body(|mut body| {
            for c in &constraints {
                body.row(28., |mut row| {
                    let holds = c["satisfied"] == true;
                    let unit = if c["response"]
                        .as_str()
                        .is_some_and(|r| r.starts_with("heat"))
                    {
                        "W/g"
                    } else {
                        "Bq/g"
                    };
                    row.col(|ui| {
                        ui.label(c["name"].as_str().unwrap_or(""));
                    });
                    row.col(|ui| {
                        let when = times
                            .iter()
                            .find(|t| {
                                (t.t_s - model::number(&c["time_s"])).abs() <= 1e-6 * t.t_s.max(1.)
                            })
                            .map_or_else(
                                || model::readable_time(model::number(&c["time_s"])),
                                |t| t.label.clone(),
                            );
                        ui.label(format!(
                            "{}, {when}",
                            response_words(c["response"].as_str().unwrap_or(""))
                        ));
                    });
                    row.col(|ui| {
                        ui.label(format!("{} {unit}", three(model::number(&c["value"]))));
                    });
                    row.col(|ui| {
                        let sense = if c["sense"] == "ge" {
                            "at least"
                        } else {
                            "at most"
                        };
                        ui.label(format!(
                            "{sense} {} {unit}",
                            three(model::number(&c["limit"]))
                        ));
                    });
                    row.col(|ui| {
                        let violation = model::number(&c["violation"]);
                        let text = margin_words(violation);
                        if holds {
                            ui.label(text);
                        } else {
                            ui.colored_label(ui.visuals().error_fg_color, text);
                        }
                    });
                    row.col(|ui| match c["nominal_satisfied"].as_bool() {
                        Some(true) if !holds => {
                            ui.colored_label(ui.visuals().warn_fg_color, "passes without bands");
                        }
                        Some(true) => {
                            ui.label("passes");
                        }
                        Some(false) => {
                            ui.label("fails");
                        }
                        None => {
                            ui.weak("not computable");
                        }
                    });
                });
            }
        });
    if let Some(measurements) = decision["measurements"]
        .as_object()
        .filter(|m| !m.is_empty())
    {
        ui.add_space(12.);
        ui.heading("What to measure next");
        ui.label("The nuclear data whose measurement would narrow each band most. Each share is what a perfect measurement of that one quantity alone would remove from the band's variance.");
        let mut ordered: Vec<(&String, &Value)> = measurements.iter().collect();
        ordered.sort_by(|a, b| key_time(a.0).total_cmp(&key_time(b.0)));
        for (key, design) in ordered {
            let (response, t) = key.split_once('@').unwrap_or((key.as_str(), ""));
            let when = t
                .parse::<f64>()
                .ok()
                .and_then(|t| {
                    times
                        .iter()
                        .find(|s| (s.t_s - t).abs() <= 1e-6 * s.t_s.max(1.))
                })
                .map_or(String::new(), |s| format!(", {}", s.label));
            ui.add_space(4.);
            ui.strong(format!("{}{when}", response_words(response)));
            let lines = measurement_lines(design);
            if lines.is_empty() {
                ui.weak("Nothing was ranked for this band.");
            }
            for line in lines {
                ui.label(line);
            }
        }
    }
}

/// One line per ranked quantity, largest share first: cross sections merged over their total and
/// product rows, then decay and yield parameters, then whole reactions the solver could rank.
fn measurement_lines(design: &Value) -> Vec<String> {
    let mut lines: Vec<(String, f64)> = Vec::new();
    for entry in design["top_parameters"].as_array().into_iter().flatten() {
        let Some(share) = entry["share_of_total"].as_f64().filter(|s| s.is_finite()) else {
            continue;
        };
        let p = &entry["parameter"];
        let what = match entry["channel"].as_str() {
            Some("cross_section_mf33") => format!(
                "{} {} cross section",
                p["target_nuclide"].as_str().unwrap_or("?"),
                actinv_core::pathways::reaction_label(p["MT"].as_i64().unwrap_or(0) as i32, true)
            ),
            Some("decay_constants") => format!(
                "{} half-life",
                p["nuclide"]
                    .as_str()
                    .or(p["name"].as_str())
                    .unwrap_or("a nuclide's")
            ),
            Some("fission_yields") => "a fission yield".into(),
            Some(other) => other.replace('_', " "),
            None => continue,
        };
        if !lines.iter().any(|(seen, _)| *seen == what) {
            lines.push((what, share));
        }
    }
    for r in design["top_reactions"].as_array().into_iter().flatten() {
        if r["status"] != "emitted" {
            continue;
        }
        if let Some(share) = r["share_of_total"].as_f64().filter(|s| s.is_finite()) {
            lines.push((format!("all of {} together", reaction_words(r)), share));
        }
    }
    lines.sort_by(|a, b| b.1.total_cmp(&a.1));
    lines
        .into_iter()
        .map(|(what, share)| format!("{what}: {}", share_words(&json!(share))))
        .collect()
}

/// The time in a measurement key `response@time_s`, for ordering.
fn key_time(key: &str) -> f64 {
    key.rsplit_once('@')
        .and_then(|(_, t)| t.parse().ok())
        .unwrap_or(f64::INFINITY)
}

/// A value to three significant figures, without trailing zeros: 764.1 becomes "764", 0.0123456 "0.0123".
fn three(x: f64) -> String {
    if x == 0. || !x.is_finite() {
        return crate::visuals::compact_number(x);
    }
    let decimals = 2 - x.abs().log10().floor() as i32;
    if (-3..0).contains(&decimals) {
        // 1000 to 999999: whole numbers rounded to three figures
        let scale = 10f64.powi(-decimals);
        return format!("{:.0}", (x / scale).round() * scale);
    }
    if !(0..=6).contains(&decimals) {
        return format!("{x:.2e}");
    }
    let text = format!("{x:.*}", decimals as usize);
    if text.contains('.') {
        text.trim_end_matches('0').trim_end_matches('.').to_owned()
    } else {
        text
    }
}

/// "Activity of Co60", "Decay heat".
fn response_words(response: &str) -> String {
    match response {
        "heat.total" => "Decay heat".into(),
        "heat.gamma" => "Gamma heat".into(),
        "heat.beta" => "Beta heat".into(),
        "heat.alpha" => "Alpha heat".into(),
        "activity.total" => "Activity".into(),
        other => match other.strip_prefix("activity:") {
            Some(nuclide) => format!("Activity of {nuclide}"),
            None => other.into(),
        },
    }
}

/// "12% under the limit", "8.5% over the limit", "at the limit".
fn margin_words(violation: f64) -> String {
    if !violation.is_finite() {
        return "not computable".into();
    }
    if violation.abs() <= 1e-9 {
        return "at the limit".into();
    }
    let percent = three(violation.abs() * 100.);
    if violation < 0. {
        format!("{percent}% inside the limit")
    } else {
        format!("{percent}% beyond the limit")
    }
}

/// "Fe56 (n,p)" from a design reaction entry.
fn reaction_words(entry: &Value) -> String {
    let za = entry["target_za"].as_i64().unwrap_or(0) as i32;
    let symbol = actinv_data::composition::symbol_of(za / 1000);
    let state = match entry["target_liso"].as_i64().unwrap_or(0) {
        0 => String::new(),
        n => format!("m{n}"),
    };
    let mts: Vec<String> = entry["mts"]
        .as_array()
        .map(|m| {
            m.iter()
                .filter_map(Value::as_i64)
                .map(|mt| actinv_core::pathways::reaction_label(mt as i32, true))
                .collect()
        })
        .unwrap_or_default();
    let mut unique = mts.clone();
    unique.dedup();
    format!("{symbol}{}{state} {}", za % 1000, unique.join(", "))
}

fn share_words(share: &Value) -> String {
    match share.as_f64() {
        Some(s) if s.is_finite() => format!("{}% of the variance", three(s * 100.)),
        _ => String::new(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn problem() -> Value {
        let mut doc = model::decode_problem(model::EXAMPLE).unwrap();
        doc["schedule"] = json!([
            {"dt": "5 min", "flux": 1.0},
            {"dt": "1 d", "flux": 0.0},
            {"dt": "30 d", "flux": 0.0}
        ]);
        doc["uncertainty"] = json!({"covariance": {"path": "cov.npz", "sha256": "ab"}, "responses": ["heat.total"], "confidence_level": 0.95});
        doc
    }

    #[test]
    fn step_times_end_each_step_and_read_like_the_results() {
        let times = step_times(&problem());
        assert_eq!(
            times.iter().map(|t| t.t_s).collect::<Vec<_>>(),
            vec![300., 86700., 2678700.]
        );
        assert_eq!(times[0].label, "at shutdown");
        assert!(times[1].label.ends_with("after shutdown"));
        let mut bad = problem();
        bad["schedule"][1]["dt"] = "soon".into();
        assert!(step_times(&bad).is_empty());
    }

    #[test]
    fn nuclides_take_the_result_spelling() {
        assert_eq!(nuclide_key(" co60 ").as_deref(), Some("Co60"));
        assert_eq!(nuclide_key("NB94M1").as_deref(), Some("Nb94m1"));
        assert_eq!(nuclide_key("H3").as_deref(), Some("H3"));
        for bad in ["", "60", "Co", "Xx60", "Co60q", "Coo60"] {
            assert_eq!(nuclide_key(bad), None, "{bad}");
        }
    }

    #[test]
    fn the_decision_names_edges_by_side_and_adds_the_responses() {
        let doc = problem();
        let times = step_times(&doc);
        let mut heat = Limit::new(1, 0);
        heat.limit = 1e-3;
        let mut floor = Limit::new(2, 1);
        floor.quantity = Quantity::NuclideActivity;
        floor.nuclide = "mn54".into();
        floor.at_most = false;
        floor.band = Band::Normal;
        floor.limit = 10.;
        let spec = decide_spec(doc.clone(), &[heat.clone(), floor], &times, 5).unwrap();
        let constraints = spec["decision"]["constraints"].as_array().unwrap();
        assert_eq!(constraints[0]["edge"], "conservative_upper");
        assert_eq!(constraints[0]["time_s"], json!(86700.));
        assert_eq!(constraints[1]["edge"], "normal_lower");
        assert_eq!(constraints[1]["sense"], "ge");
        assert_eq!(constraints[1]["response"], "activity:Mn54");
        assert_eq!(
            spec["run_spec"]["uncertainty"]["responses"],
            json!(["heat.total", "activity:Mn54"])
        );
        // the decision parses as the command reads it
        let parsed: Result<actinv_cli::decide::DecideSpec, _> = serde_json::from_value(spec);
        assert!(parsed.is_ok());

        let mut unnamed = heat.clone();
        unnamed.name = "Limit 1".into();
        assert!(
            decide_spec(doc.clone(), &[heat.clone(), unnamed], &times, 5)
                .unwrap_err()
                .contains("own name")
        );
        let mut zero = heat.clone();
        zero.limit = 0.;
        assert!(decide_spec(doc.clone(), &[zero], &times, 5)
            .unwrap_err()
            .contains("positive"));
        assert!(decide_spec(doc, &[], &times, 5).is_err());
    }

    #[test]
    fn a_problem_without_covariance_is_sent_to_what_to_compute() {
        let mut doc = problem();
        assert!(missing(&doc).is_none());
        doc["uncertainty"]["covariance"]["path"] = "".into();
        assert!(missing(&doc).unwrap().contains("covariance"));
        doc.as_object_mut().unwrap().remove("uncertainty");
        assert!(missing(&doc).unwrap().contains("off"));
    }

    #[test]
    fn measurements_merge_rows_of_one_reaction_and_skip_unranked_blocks() {
        let design = json!({
            "top_parameters": [
                {"channel": "cross_section_mf33", "share_of_total": 0.948,
                 "parameter": {"MT": 103, "ZAP": -1, "target_nuclide": "Fe56"}},
                {"channel": "cross_section_mf33", "share_of_total": 0.948,
                 "parameter": {"MT": 103, "ZAP": 25056, "target_nuclide": "Fe56"}},
                {"channel": "cross_section_mf33", "share_of_total": 0.69,
                 "parameter": {"MT": 16, "ZAP": -1, "target_nuclide": "Fe54"}},
                {"channel": "cross_section_mf33", "share_of_total": null,
                 "parameter": {"MT": 102, "ZAP": -1, "target_nuclide": "Fe58"}}
            ],
            "top_reactions": [
                {"target_za": 20043, "target_liso": 0, "mts": [16], "status": "ill_conditioned", "share_of_total": null}
            ]
        });
        assert_eq!(
            measurement_lines(&design),
            vec![
                "Fe56 (n,p) cross section: 94.8% of the variance".to_owned(),
                "Fe54 (n,2n) cross section: 69% of the variance".to_owned(),
            ]
        );
    }

    #[test]
    fn margins_read_as_words() {
        assert_eq!(three(764.108643), "764");
        assert_eq!(three(0.0123456), "0.0123");
        assert_eq!(three(49.059424), "49.1");
        assert_eq!(three(1500.), "1500");
        assert_eq!(three(2.5e-9), "2.50e-9");
        assert_eq!(three(764108.6), "764000");
        assert_eq!(three(3.2e7), "3.20e7");
        assert_eq!(key_time("heat.total@86700"), 86700.);
        assert_eq!(margin_words(-0.12), "12% inside the limit");
        assert_eq!(margin_words(0.085), "8.5% beyond the limit");
        assert_eq!(margin_words(0.), "at the limit");
        let entry =
            json!({"target_za": 26056, "target_liso": 0, "mts": [103], "share_of_total": 0.4});
        assert_eq!(reaction_words(&entry), "Fe56 (n,p)");
        assert_eq!(share_words(&entry["share_of_total"]), "40% of the variance");
    }
}
