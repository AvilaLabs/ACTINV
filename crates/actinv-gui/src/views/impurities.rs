//! Impurities: which trace elements drive an endpoint, the worst case over their declared ranges,
//! and how far each may rise before a limit is reached. The view builds an `actinv-impurities-1`
//! screen from the open problem and draws an `actinv-impurities-result-1`; `actinv impurities`
//! computes the result, in the desktop worker or on the command line. The view computes no
//! numbers of its own: every value it shows is a field of the result.
use crate::{
    model,
    views::check::display_name,
    visuals::{accent, compact_number, BLUE},
};
use eframe::egui::{self, Color32, RichText};
use serde_json::{json, Map, Value};
use std::collections::BTreeSet;

pub const SCREEN_SCHEMA: &str = "actinv-impurities-1";
pub const RESULT_SCHEMA: &str = "actinv-impurities-result-1";

/// What an element is in the screen.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Role {
    /// Takes up the remainder to 100 wt%.
    Balance,
    /// Held at its share of the material.
    Fixed,
    /// Varied over a declared range.
    Impurity,
}

impl Role {
    fn label(self) -> &'static str {
        match self {
            Role::Balance => "Balance",
            Role::Fixed => "Fixed",
            Role::Impurity => "Impurity",
        }
    }
}

/// One element of the screen. Shares are wt%.
#[derive(Clone, Debug, PartialEq)]
pub struct Row {
    element: String,
    role: Role,
    /// The material's share, rescaled so the material sums to 100; zero for an added element.
    share: f64,
    nominal: f64,
    min: f64,
    max: f64,
    /// Added on this page rather than taken from the material.
    added: bool,
}

/// A scalar endpoint the screen can report.
#[derive(Clone, Debug, PartialEq)]
struct Scalar {
    id: &'static str,
    on: bool,
    limit: Option<f64>,
}

/// The scalar endpoints offered, in display order: (id, name, unit, what a limit means).
const SCALARS: [(&str, &str, &str); 4] = [
    ("activity.total", "Total activity", "Bq/g"),
    ("heat.total", "Decay heat", "W/g"),
    ("dose.contact_proxy", "Contact dose rate (proxy)", "Gy/h"),
    ("clearance_index", "Clearance index", "sum of A/CL"),
];

fn scalar_name(id: &str) -> String {
    if let Some(nuclide) = id.strip_prefix("activity:") {
        return format!("{nuclide} activity");
    }
    if let Some(class) = id.strip_prefix("waste_class:") {
        return format!("Waste class (target {class})");
    }
    SCALARS
        .iter()
        .find(|(i, _, _)| *i == id)
        .map_or_else(|| id.to_owned(), |(_, name, _)| (*name).to_owned())
}

/// The setup and the last result of the page.
pub struct ImpuritiesState {
    rows: Vec<Row>,
    /// The material the rows were last built from: (basis, composition).
    synced: Option<(Value, Value)>,
    /// One-based result steps; `None` until first chosen from the schedule.
    targets: Option<BTreeSet<u64>>,
    scalars: Vec<Scalar>,
    /// `activity:<nuclide>` endpoints and their limits (Bq/g).
    nuclides: Vec<(String, Option<f64>)>,
    new_nuclide: String,
    new_element: String,
    waste: bool,
    target_class: &'static str,
    activated_metal: bool,
    /// Bounds over the nuclear-data uncertainty: off unless asked for, since every sample repeats
    /// every element calculation.
    bands: Bands,
    pub result: Option<(String, Value)>,
    /// The result step on show, as an index into `targets`.
    step: usize,
    pub showing_result: bool,
    pub message: Option<Result<String, String>>,
}

impl Default for ImpuritiesState {
    fn default() -> Self {
        Self {
            rows: Vec::new(),
            synced: None,
            targets: None,
            scalars: SCALARS
                .iter()
                .map(|(id, _, _)| Scalar {
                    id,
                    on: matches!(*id, "activity.total" | "heat.total"),
                    limit: None,
                })
                .collect(),
            nuclides: Vec::new(),
            new_nuclide: String::new(),
            new_element: String::new(),
            waste: false,
            target_class: "C",
            activated_metal: true,
            bands: Bands::default(),
            result: None,
            step: 0,
            showing_result: false,
            message: None,
        }
    }
}

/// The `sampling` block and `confidence_level` of a screen with nuclear-data bounds.
#[derive(Clone, Debug, PartialEq)]
struct Bands {
    on: bool,
    confidence: f64,
    samples: u32,
    seed: u64,
    cross_sections: bool,
    decay: bool,
}

impl Default for Bands {
    fn default() -> Self {
        Self {
            on: false,
            confidence: 0.95,
            samples: minimum_samples(0.95),
            seed: 1,
            cross_sections: true,
            decay: true,
        }
    }
}

/// The confidence levels offered.
const CONFIDENCES: [f64; 3] = [0.9, 0.95, 0.99];
/// The covariance the cross-section channel samples.
const COVARIANCE: &str = "catalog:tendl-2025-neutron-709g-covariance";

/// Fewest samples whose largest value is an upper bound at `confidence`: (1 − c)/2 ≥ cⁿ, so 72 at
/// 0.95. `actinv impurities` refuses fewer.
fn minimum_samples(confidence: f64) -> u32 {
    (((1. - confidence) / 2.).ln() / confidence.ln()).ceil() as u32
}

fn percent(confidence: f64) -> String {
    format!("{}%", compact_number(confidence * 100.))
}

/// What the shell must do after the page was drawn.
#[derive(Debug, PartialEq)]
pub enum ImpuritiesAction {
    None,
    /// Run the screen (desktop) — the shell builds it with `screen_document`.
    Screen,
    /// Save the screen document for `actinv impurities`.
    SaveScreen,
    OpenResult,
    SaveResult,
    /// Open a setup page.
    GoTo(super::Target),
}

/// What the shell can do; the browser cannot run a screen.
pub struct Context {
    pub can_screen: bool,
    /// A screen is running in the worker.
    pub running: bool,
}

/// The canonical symbol of a natural element, or why the key is not one.
fn element(raw: &str) -> Result<String, String> {
    match actinv_data::composition::material_key(raw.trim()) {
        Ok(actinv_data::composition::MaterialKey::Element(symbol)) => Ok(symbol),
        Ok(_) => Err(format!(
            "{raw} is a nuclide; impurity screening works with natural elements"
        )),
        Err(_) => Err(format!("{raw} is not an element symbol")),
    }
}

/// Rebuild the rows when the material changed, keeping the roles and ranges the user set for
/// elements that are still there and every element added on this page.
fn sync(state: &mut ImpuritiesState, document: &Value) {
    let material = &document["material"];
    let key = (material["basis"].clone(), material["composition"].clone());
    if state.synced.as_ref() == Some(&key) {
        return;
    }
    let composition: Vec<(String, f64)> = material["composition"]
        .as_object()
        .map(|m| {
            m.iter()
                .map(|(k, v)| (element(k).unwrap_or_else(|_| k.clone()), model::number(v)))
                .collect()
        })
        .unwrap_or_default();
    let total: f64 = composition.iter().map(|(_, w)| w.max(0.)).sum();
    let scale = if total > 0. { 100. / total } else { 0. };
    let previous = std::mem::take(&mut state.rows);
    let mut rows: Vec<Row> = composition
        .iter()
        .map(|(e, w)| {
            let share = w.max(0.) * scale;
            match previous.iter().find(|r| &r.element == e) {
                Some(old) if old.role == Role::Impurity => Row {
                    share,
                    added: false,
                    ..old.clone()
                },
                Some(old) => Row {
                    element: e.clone(),
                    role: old.role,
                    share,
                    nominal: share,
                    min: share,
                    max: share,
                    added: false,
                },
                None => Row {
                    element: e.clone(),
                    role: Role::Fixed,
                    share,
                    nominal: share,
                    min: share,
                    max: share,
                    added: false,
                },
            }
        })
        .collect();
    rows.extend(
        previous
            .into_iter()
            .filter(|r| r.added && !composition.iter().any(|(e, _)| *e == r.element)),
    );
    if !rows.iter().any(|r| r.role == Role::Balance) {
        if let Some(largest) = rows
            .iter_mut()
            .filter(|r| !r.added)
            .max_by(|a, b| a.share.total_cmp(&b.share))
        {
            largest.role = Role::Balance;
        }
    }
    state.rows = rows;
    state.synced = Some(key);
}

/// One schedule step: its duration in seconds and whether it irradiates.
fn schedule(document: &Value) -> Vec<(f64, bool)> {
    document["schedule"]
        .as_array()
        .map(|steps| {
            steps
                .iter()
                .map(|s| {
                    let dt = s["dt"]
                        .as_str()
                        .and_then(|t| actinv_core::spec::parse_duration(t).ok())
                        .or_else(|| s["dt"].as_f64())
                        .unwrap_or(0.);
                    let driven = ["wall_loading_MW_m2", "power_W_cm3", "power_W_per_g"]
                        .iter()
                        .any(|k| s[*k].as_f64().is_some_and(|x| x != 0.));
                    (dt, driven || s["flux"].as_f64().is_some_and(|f| f != 0.))
                })
                .collect()
        })
        .unwrap_or_default()
}

/// When result step `step` (one-based, the end of schedule step `step`) falls: the cooling time
/// since the last irradiating step, or the end of irradiation.
fn when(steps: &[(f64, bool)], step: u64) -> String {
    let k = step as usize;
    if k == 0 || k > steps.len() {
        return format!("step {step}");
    }
    if steps[k - 1].1 {
        return if steps[k..].iter().any(|s| s.1) {
            "during irradiation".into()
        } else {
            "at shutdown".into()
        };
    }
    match steps[..k].iter().rposition(|s| s.1) {
        Some(last) => {
            let cooled: f64 = steps[last + 1..k].iter().map(|s| s.0).sum();
            format!("{} cooling", model::readable_time(cooled))
        }
        None => format!(
            "t = {}",
            model::readable_time(steps[..k].iter().map(|s| s.0).sum())
        ),
    }
}

/// The steps chosen until the user picks: every cooling step after the last irradiation, else
/// the last step.
fn default_targets(steps: &[(f64, bool)]) -> BTreeSet<u64> {
    let after = steps.iter().rposition(|s| s.1).map_or(0, |i| i + 1);
    let cooling: BTreeSet<u64> = (after + 1..=steps.len()).map(|k| k as u64).collect();
    if cooling.is_empty() && !steps.is_empty() {
        BTreeSet::from([steps.len() as u64])
    } else {
        cooling
    }
}

/// Why the base problem cannot be screened: `actinv impurities` refuses these because an
/// endpoint is then not linear in the composition.
fn base_problems(document: &Value) -> Vec<String> {
    let mut out = Vec::new();
    if document["material"]["basis"].as_str() != Some("wt_percent") {
        out.push("Impurity screening works in weight percent. Set the composition basis on Material to wt%.".into());
    }
    if !document["self_shielding"].is_null() {
        out.push("Self-shielding depends on the composition, so this problem cannot be screened. Turn it off on What to compute.".into());
    }
    if !document["uncertainty"].is_null() {
        out.push(
            "Remove the uncertainty section first; bands are not yet part of a screen.".into(),
        );
    }
    if !document["options"]["screen"].is_null() {
        out.push("The screened solver tier prunes by composition. Remove options.screen in Advanced JSON.".into());
    }
    if let Some(steps) = document["schedule"].as_array() {
        if steps
            .iter()
            .any(|s| !s["power_W_cm3"].is_null() || !s["power_W_per_g"].is_null())
        {
            out.push("A power-driven step sets its flux from the composition. Use flux or wall loading on Irradiation & cooling.".into());
        }
        if steps.iter().any(|s| !s["feed"].is_null()) {
            out.push(
                "A step with a feed adds atoms whatever the composition, so it cannot be screened."
                    .into(),
            );
        }
    }
    out
}

fn has_photon_response(document: &Value) -> bool {
    document["photon"]["response"].is_object()
        && document["photon"]["missing_spectra"].as_str() != Some("estimate")
}

/// Everything that stops the screen from running, in the order to fix it.
pub fn problems(state: &ImpuritiesState, document: &Value) -> Vec<String> {
    let mut out = base_problems(document);
    for r in &state.rows {
        if let Err(e) = element(&r.element) {
            out.push(format!("{e}."));
        }
    }
    let balance: Vec<&Row> = state
        .rows
        .iter()
        .filter(|r| r.role == Role::Balance)
        .collect();
    if balance.len() != 1 {
        out.push("Choose one element as the balance.".into());
    }
    let impurities: Vec<&Row> = state
        .rows
        .iter()
        .filter(|r| r.role == Role::Impurity)
        .collect();
    if impurities.is_empty() {
        out.push("Mark at least one element as an impurity, or add one.".into());
    }
    for r in &impurities {
        let name = display_name(&r.element);
        if !(r.min <= r.nominal && r.nominal <= r.max) {
            out.push(format!("{name}: keep min ≤ nominal ≤ max."));
        } else if r.max <= 0. {
            out.push(format!("{name}: set the largest share it may have."));
        }
    }
    let at_max: f64 = state
        .rows
        .iter()
        .map(|r| match r.role {
            Role::Balance => 0.,
            Role::Fixed => r.share,
            Role::Impurity => r.max,
        })
        .sum();
    if at_max >= 100. {
        out.push(format!(
            "With every impurity at its maximum the other elements take {} wt%; lower the maxima so the balance keeps a share.",
            compact_number(at_max)
        ));
    }
    if state.targets.as_ref().is_none_or(BTreeSet::is_empty) {
        out.push("Choose at least one step to screen.".into());
    }
    if !state.scalars.iter().any(|s| s.on) && state.nuclides.is_empty() && !state.waste {
        out.push("Choose at least one thing to screen.".into());
    }
    if state
        .scalars
        .iter()
        .any(|s| s.on && s.id == "dose.contact_proxy")
        && !has_photon_response(document)
    {
        out.push("Contact dose needs a photon response; add one on What to compute.".into());
    }
    if state.waste && !document["material"]["density_g_cm3"].is_number() {
        out.push("Waste class needs the material's density; set it on Material.".into());
    }
    let b = &state.bands;
    if b.on {
        if !b.cross_sections && !b.decay {
            out.push("Choose cross sections, decay constants or both to sample.".into());
        }
        let least = minimum_samples(b.confidence);
        if b.samples < least {
            out.push(format!(
                "At {} confidence a bound needs at least {least} samples.",
                percent(b.confidence)
            ));
        }
    }
    out
}

/// The `actinv-impurities-1` document for the open problem. `base` is the run spec to embed:
/// the desktop passes the problem with its file paths resolved, the browser the problem as it is.
pub fn screen_document(
    state: &ImpuritiesState,
    document: &Value,
    base: Value,
) -> Result<Value, String> {
    if let Some(first) = problems(state, document).into_iter().next() {
        return Err(first);
    }
    let balance = state
        .rows
        .iter()
        .find(|r| r.role == Role::Balance)
        .map(|r| r.element.clone())
        .unwrap_or_default();
    let matrix: Map<String, Value> = state
        .rows
        .iter()
        .filter(|r| r.role == Role::Fixed)
        .map(|r| (r.element.clone(), json!(r.share)))
        .collect();
    let impurities: Map<String, Value> = state
        .rows
        .iter()
        .filter(|r| r.role == Role::Impurity)
        .map(|r| {
            (
                r.element.clone(),
                json!({"nominal": r.nominal, "min": r.min, "max": r.max}),
            )
        })
        .collect();
    let mut endpoints: Vec<Value> = state
        .scalars
        .iter()
        .filter(|s| s.on)
        .map(|s| match s.limit {
            Some(limit) => json!({"endpoint": s.id, "limit": limit}),
            None => json!({"endpoint": s.id}),
        })
        .collect();
    endpoints.extend(state.nuclides.iter().map(|(n, limit)| match limit {
        Some(limit) => json!({"endpoint": format!("activity:{n}"), "limit": limit}),
        None => json!({"endpoint": format!("activity:{n}")}),
    }));
    let mut screen = json!({
        "schema": SCREEN_SCHEMA,
        "base_spec": base,
        "balance": balance,
        "matrix": matrix,
        "impurities": impurities,
        "targets": state.targets.clone().unwrap_or_default(),
        "endpoints": endpoints,
    });
    if state.waste {
        screen["endpoints"]
            .as_array_mut()
            .expect("endpoints array")
            .push(json!({"endpoint": "waste_class", "class": state.target_class}));
        screen["waste"] = json!({
            "rules": "us-nrc-10cfr61.55-v1",
            "mass_g": model::number(&document["material"]["mass_g"]),
            "density_g_cm3": document["material"]["density_g_cm3"],
            "waste_type": if state.activated_metal { "activated_metal" } else { "general" },
            "external_tritium": {"status": "not_applicable"},
        });
    }
    let b = &state.bands;
    if b.on {
        let mut sampling = json!({
            "channels": {"cross_section_mf33": b.cross_sections, "decay_constants": b.decay},
            "samples": b.samples,
            "seed": b.seed,
        });
        if b.cross_sections {
            sampling["covariance"] = json!({"path": COVARIANCE});
        }
        screen["sampling"] = sampling;
        screen["confidence_level"] = json!(b.confidence);
    }
    Ok(screen)
}

/// A JSON value with every null member removed, so an optional field the spec serializer wrote
/// as null reads as absent.
// the browser embeds the problem as it is, so only the desktop calls it
#[cfg_attr(target_arch = "wasm32", allow(dead_code))]
pub fn without_nulls(value: Value) -> Value {
    match value {
        Value::Object(m) => Value::Object(
            m.into_iter()
                .filter(|(_, v)| !v.is_null())
                .map(|(k, v)| (k, without_nulls(v)))
                .collect(),
        ),
        Value::Array(a) => Value::Array(a.into_iter().map(without_nulls).collect()),
        other => other,
    }
}

/// Check that a loaded file is a screen result.
pub fn check_result(value: &Value) -> Result<(), String> {
    match value["schema"].as_str() {
        Some(RESULT_SCHEMA) if value["targets"].is_array() => Ok(()),
        Some(RESULT_SCHEMA) => Err("This screen result has no targets.".into()),
        Some(other) => Err(format!(
            "This file is `{other}`, not an impurity screen result ({RESULT_SCHEMA})."
        )),
        None => Err(format!(
            "This file is not an impurity screen result ({RESULT_SCHEMA})."
        )),
    }
}

fn muted(ui: &egui::Ui) -> Color32 {
    if ui.visuals().dark_mode {
        Color32::from_rgb(175, 184, 205)
    } else {
        Color32::from_rgb(85, 93, 112)
    }
}

fn good(ui: &egui::Ui) -> Color32 {
    if ui.visuals().dark_mode {
        Color32::from_rgb(120, 200, 140)
    } else {
        Color32::from_rgb(20, 110, 50)
    }
}

fn card(ui: &mut egui::Ui, add: impl FnOnce(&mut egui::Ui)) {
    egui::Frame::group(ui.style())
        .inner_margin(16.)
        .show(ui, |ui| {
            ui.set_width(ui.available_width());
            add(ui);
        });
    ui.add_space(8.);
}

fn primary(ui: &mut egui::Ui, enabled: bool, text: &str) -> egui::Response {
    ui.add_enabled(
        enabled,
        egui::Button::new(RichText::new(text).color(Color32::WHITE)).fill(BLUE),
    )
}

/// A wt% field. Shares are small, so the drag is fine and the text shows them in full.
fn share(ui: &mut egui::Ui, value: &mut f64) -> bool {
    ui.add(
        egui::DragValue::new(value)
            .custom_parser(model::parse_finite)
            .custom_formatter(|n, _| compact_number(n))
            .speed(0.001)
            .range(0.0..=100.0),
    )
    .changed()
}

/// An optional positive limit: a checkbox, then its value.
fn limit_field(ui: &mut egui::Ui, limit: &mut Option<f64>, default: f64, unit: &str) {
    let mut on = limit.is_some();
    if ui.checkbox(&mut on, "with a limit").changed() {
        *limit = on.then_some(default);
    }
    if let Some(value) = limit {
        let speed = value.abs() * 0.01 + 1e-12;
        ui.add(
            egui::DragValue::new(value)
                .custom_parser(model::parse_finite)
                .custom_formatter(|n, _| compact_number(n))
                .speed(speed)
                .range(f64::MIN_POSITIVE..=f64::MAX),
        );
        ui.label(RichText::new(unit).color(muted(ui)));
    }
}

/// Draw the page body. Returns what the shell must do.
pub fn show(
    ui: &mut egui::Ui,
    document: &Value,
    state: &mut ImpuritiesState,
    context: &Context,
) -> ImpuritiesAction {
    sync(state, document);
    let mut action = ImpuritiesAction::None;
    ui.horizontal(|ui| {
        if ui
            .selectable_label(!state.showing_result, "Set up the screen")
            .clicked()
        {
            state.showing_result = false;
        }
        let label = match &state.result {
            Some(_) => "Screen result",
            None => "Screen result (none yet)",
        };
        if ui.selectable_label(state.showing_result, label).clicked() {
            state.showing_result = true;
        }
    });
    ui.add_space(10.);
    if state.showing_result {
        match &state.result {
            Some((label, result)) => {
                let label = label.clone();
                let result = result.clone();
                result_view(ui, &label, &result, state, &mut action);
            }
            None => {
                ui.label("No screen has been run yet. Set one up and press Screen impurities, or open a result saved by the app or by `actinv impurities`.");
                ui.add_space(8.);
                if ui.button("Open result…").clicked() {
                    action = ImpuritiesAction::OpenResult;
                }
            }
        }
    } else {
        setup_view(ui, document, state, context, &mut action);
    }
    action
}

fn setup_view(
    ui: &mut egui::Ui,
    document: &Value,
    state: &mut ImpuritiesState,
    context: &Context,
    action: &mut ImpuritiesAction,
) {
    let steps = schedule(document);
    if state.targets.is_none() {
        state.targets = Some(default_targets(&steps));
    }
    let base = base_problems(document);
    if !base.is_empty() {
        card(ui, |ui| {
            ui.label(RichText::new("This problem cannot be screened yet").strong());
            for p in &base {
                ui.colored_label(ui.visuals().warn_fg_color, p);
            }
        });
    }

    // 1. Composition: the balance, the fixed constituents and the impurity ranges.
    card(ui, |ui| {
        ui.horizontal(|ui| {
            ui.label(RichText::new("1  Composition").strong());
            ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                if ui.button("Edit on Material").clicked() {
                    *action = ImpuritiesAction::GoTo(super::Target::Material);
                }
            });
        });
        ui.label(RichText::new("Each element of the material is the balance, held fixed, or an impurity varied over a range (wt%). The balance takes whatever the others leave. Fixed shares come from Material.").color(muted(ui)));
        ui.add_space(6.);
        let mut remove = None;
        let mut new_balance = None;
        let balance_nominal = 100.
            - state
                .rows
                .iter()
                .map(|r| match r.role {
                    Role::Balance => 0.,
                    Role::Fixed => r.share,
                    Role::Impurity => r.nominal,
                })
                .sum::<f64>();
        egui::Grid::new("impurity-rows")
            .num_columns(6)
            .spacing([18., 6.])
            .striped(true)
            .show(ui, |ui| {
                for h in ["Element", "Role", "Nominal wt%", "Min", "Max", ""] {
                    ui.label(RichText::new(h).color(muted(ui)));
                }
                ui.end_row();
                for (i, row) in state.rows.iter_mut().enumerate() {
                    ui.monospace(display_name(&row.element));
                    let before = row.role;
                    let roles: &[Role] = if row.added {
                        &[Role::Impurity]
                    } else {
                        &[Role::Balance, Role::Fixed, Role::Impurity]
                    };
                    egui::ComboBox::from_id_salt(("impurity-role", i))
                        .selected_text(row.role.label())
                        .width(96.)
                        .show_ui(ui, |ui| {
                            for r in roles {
                                ui.selectable_value(&mut row.role, *r, r.label());
                            }
                        });
                    if row.role != before {
                        match row.role {
                            Role::Balance => new_balance = Some(i),
                            Role::Impurity => {
                                row.nominal = row.share;
                                row.min = 0.;
                                row.max = row.share;
                            }
                            Role::Fixed => {}
                        }
                    }
                    match row.role {
                        Role::Balance => {
                            ui.label(format!("{} (remainder)", compact_number(balance_nominal)));
                            ui.label("");
                            ui.label("");
                        }
                        Role::Fixed => {
                            ui.label(compact_number(row.share));
                            ui.label("");
                            ui.label("");
                        }
                        Role::Impurity => {
                            share(ui, &mut row.nominal);
                            share(ui, &mut row.min);
                            share(ui, &mut row.max);
                        }
                    }
                    if row.added {
                        if ui.small_button("Remove").clicked() {
                            remove = Some(i);
                        }
                    } else {
                        ui.label("");
                    }
                    ui.end_row();
                }
            });
        if let Some(i) = new_balance {
            for (j, r) in state.rows.iter_mut().enumerate() {
                if j != i && r.role == Role::Balance {
                    r.role = Role::Fixed;
                }
            }
        }
        if let Some(i) = remove {
            state.rows.remove(i);
        }
        ui.add_space(6.);
        if !state.rows.iter().any(|r| r.role == Role::Impurity) {
            ui.colored_label(
                accent(ui),
                "No impurities yet. Set the role of a trace element above to Impurity, or add one the material does not list, such as Co, Nb or Ag in steel.",
            );
            ui.add_space(4.);
        }
        ui.horizontal(|ui| {
            ui.label("Add an impurity the material does not list");
            let field = ui.add(
                egui::TextEdit::singleline(&mut state.new_element)
                    .hint_text("e.g. Co")
                    .desired_width(70.),
            );
            let entered = field.lost_focus() && ui.input(|i| i.key_pressed(egui::Key::Enter));
            if ui.button("Add").clicked() || entered {
                match element(&state.new_element) {
                    Ok(e) if state.rows.iter().any(|r| r.element == e) => {
                        state.message = Some(Err(format!(
                            "{} is already listed; set its role to Impurity.",
                            display_name(&e)
                        )));
                    }
                    Ok(e) => {
                        state.rows.push(Row {
                            element: e,
                            role: Role::Impurity,
                            share: 0.,
                            nominal: 0.,
                            min: 0.,
                            max: 0.,
                            added: true,
                        });
                        state.new_element.clear();
                        state.message = None;
                    }
                    Err(e) => state.message = Some(Err(format!("{e}."))),
                }
            }
        });
    });

    // 2. When: the result steps to screen.
    card(ui, |ui| {
        ui.label(RichText::new("2  When").strong());
        ui.label(RichText::new("The steps of Irradiation & cooling to screen. Limits and worst cases are reported at each, and the tightest over all of them.").color(muted(ui)));
        ui.add_space(6.);
        let targets = state.targets.get_or_insert_with(BTreeSet::new);
        if steps.is_empty() {
            ui.label("The problem has no steps yet; add them on Irradiation & cooling.");
        }
        ui.horizontal_wrapped(|ui| {
            for k in 1..=steps.len() as u64 {
                let mut on = targets.contains(&k);
                if ui
                    .checkbox(&mut on, format!("Step {k} · {}", when(&steps, k)))
                    .changed()
                {
                    if on {
                        targets.insert(k);
                    } else {
                        targets.remove(&k);
                    }
                }
            }
        });
    });

    // 3. What matters: endpoints and their limits.
    card(ui, |ui| {
        ui.label(RichText::new("3  What matters").strong());
        ui.label(RichText::new("What to rank the impurities by. Give a limit to find how far each impurity may rise before it is reached.").color(muted(ui)));
        ui.add_space(6.);
        let photon = has_photon_response(document);
        egui::Grid::new("impurity-endpoints")
            .num_columns(2)
            .spacing([18., 6.])
            .show(ui, |ui| {
                for s in state.scalars.iter_mut() {
                    let (_, name, unit) = SCALARS
                        .iter()
                        .find(|(id, _, _)| *id == s.id)
                        .copied()
                        .expect("offered endpoint");
                    let usable = s.id != "dose.contact_proxy" || photon;
                    let r = ui.add_enabled(usable, egui::Checkbox::new(&mut s.on, name));
                    if !usable {
                        r.on_disabled_hover_text(
                            "Needs a photon response; add one on What to compute.",
                        );
                    }
                    ui.horizontal(|ui| {
                        if !usable {
                            ui.label(
                                RichText::new("needs a photon response (What to compute)")
                                    .color(muted(ui)),
                            );
                        } else if s.on {
                            if s.id == "clearance_index" {
                                ui.label(
                                    RichText::new("limit 1: below it the material may be cleared")
                                        .color(muted(ui)),
                                );
                            } else {
                                limit_field(ui, &mut s.limit, 1.0, unit);
                            }
                        }
                    });
                    ui.end_row();
                }
                let mut remove = None;
                for (i, (nuclide, limit)) in state.nuclides.iter_mut().enumerate() {
                    ui.horizontal(|ui| {
                        if ui.small_button("Remove").clicked() {
                            remove = Some(i);
                        }
                        ui.label(format!("{nuclide} activity"));
                    });
                    ui.horizontal(|ui| limit_field(ui, limit, 1.0, "Bq/g"));
                    ui.end_row();
                }
                if let Some(i) = remove {
                    state.nuclides.remove(i);
                }
            });
        ui.horizontal(|ui| {
            ui.label("Activity of one nuclide");
            let field = ui.add(
                egui::TextEdit::singleline(&mut state.new_nuclide)
                    .hint_text("e.g. Nb94")
                    .desired_width(90.),
            );
            let entered = field.lost_focus() && ui.input(|i| i.key_pressed(egui::Key::Enter));
            if (ui.button("Add").clicked() || entered) && !state.new_nuclide.trim().is_empty() {
                let n = state.new_nuclide.trim().to_owned();
                if !state.nuclides.iter().any(|(m, _)| *m == n) {
                    state.nuclides.push((n, None));
                }
                state.new_nuclide.clear();
            }
        });
        ui.add_space(4.);
        ui.horizontal_wrapped(|ui| {
            ui.checkbox(&mut state.waste, "Waste class (10 CFR 61.55)");
            if state.waste {
                ui.label("must meet class");
                egui::ComboBox::from_id_salt("impurity-class")
                    .selected_text(state.target_class)
                    .width(48.)
                    .show_ui(ui, |ui| {
                        for c in ["A", "B", "C"] {
                            ui.selectable_value(&mut state.target_class, c, c);
                        }
                    });
                ui.label("as");
                egui::ComboBox::from_id_salt("impurity-waste-type")
                    .selected_text(if state.activated_metal {
                        "activated metal"
                    } else {
                        "general waste"
                    })
                    .show_ui(ui, |ui| {
                        ui.selectable_value(&mut state.activated_metal, true, "activated metal");
                        ui.selectable_value(&mut state.activated_metal, false, "general waste");
                    });
            }
        });
        if state.waste {
            ui.label(RichText::new(format!(
                "Uses the material's mass ({} g) and density as the component; no retained tritium beyond the calculation.",
                compact_number(model::number(&document["material"]["mass_g"]))
            )).color(muted(ui)));
        }
    });

    // 4. Nuclear-data uncertainty: optional bounds from repeated screens over sampled data.
    let elements = state
        .rows
        .iter()
        .filter(|r| r.role != Role::Balance)
        .count()
        + 1;
    card(ui, |ui| {
        ui.horizontal(|ui| {
            ui.label(RichText::new("4  Nuclear-data uncertainty").strong());
            ui.label(RichText::new("optional").color(muted(ui)));
        });
        ui.label(RichText::new("Repeat the screen over samples of the nuclear data to put a bound on every value and limit: an upper bound on each value, a lower bound on each limit.").color(muted(ui)));
        ui.add_space(6.);
        let b = &mut state.bands;
        ui.checkbox(&mut b.on, "Add bounds over the nuclear-data uncertainty");
        if !b.on {
            return;
        }
        ui.add_space(4.);
        egui::Grid::new("impurity-bands")
            .num_columns(2)
            .spacing([18., 6.])
            .show(ui, |ui| {
                ui.label("Confidence");
                ui.horizontal(|ui| {
                    for c in CONFIDENCES {
                        if ui
                            .selectable_label(b.confidence == c, percent(c))
                            .clicked()
                            && b.confidence != c
                        {
                            b.confidence = c;
                            b.samples = b.samples.max(minimum_samples(c));
                        }
                    }
                });
                ui.end_row();
                ui.label("Samples");
                ui.horizontal(|ui| {
                    let least = minimum_samples(b.confidence);
                    ui.add(egui::DragValue::new(&mut b.samples).range(least..=4096));
                    ui.label(
                        RichText::new(format!("at least {least} at {}", percent(b.confidence)))
                            .color(muted(ui)),
                    );
                });
                ui.end_row();
                ui.label("Sample");
                ui.horizontal(|ui| {
                    ui.checkbox(&mut b.cross_sections, "cross sections")
                        .on_hover_text("Correlated draws from the TENDL-2025 covariance in the data catalog (MF=33).");
                    ui.checkbox(&mut b.decay, "decay constants");
                });
                ui.end_row();
                ui.label("Seed");
                ui.add(egui::DragValue::new(&mut b.seed))
                    .on_hover_text("The same seed draws the same samples, so a screen can be repeated exactly.");
                ui.end_row();
            });
        ui.add_space(4.);
        ui.label(RichText::new(format!(
            "Each sample repeats every element calculation: {elements} × {} = {} calculations, plus re-solves of the samples behind the bounds. 72 samples of a 15-element steel take about an hour.",
            b.samples,
            elements as u64 * u64::from(b.samples),
        )).color(muted(ui)));
    });

    let problems = problems(state, document);
    ui.add_space(4.);
    ui.horizontal_wrapped(|ui| {
        if context.running {
            ui.spinner();
            ui.label(if state.bands.on {
                "Screening over the nuclear-data samples… every sample solves each element once, then the samples behind the bounds are re-solved as a check. Cancel from the status bar."
            } else {
                "Screening… each element is solved once, then every reported composition is re-solved as a check. Cancel from the status bar."
            });
        } else if context.can_screen {
            let r = primary(ui, problems.is_empty(), "Screen impurities");
            if r.clicked() {
                *action = ImpuritiesAction::Screen;
            }
        }
        if ui
            .add_enabled(problems.is_empty(), egui::Button::new("Save screen…"))
            .on_hover_text("A screen file for `actinv impurities`, with this problem embedded.")
            .clicked()
        {
            *action = ImpuritiesAction::SaveScreen;
        }
        if ui.button("Open result…").clicked() {
            *action = ImpuritiesAction::OpenResult;
        }
    });
    if !context.can_screen {
        ui.label(RichText::new("The browser cannot run a screen. Save the screen and run it with the desktop app or `actinv impurities screen.json result.json`, then open the result here.").color(muted(ui)));
    }
    if let Some(first) = problems.first() {
        ui.colored_label(ui.visuals().warn_fg_color, first);
    } else if context.can_screen && !context.running && !state.bands.on {
        ui.label(RichText::new(format!("One calculation per element ({elements}), then a full re-solve of each reported composition.")).color(muted(ui)));
    }
    match &state.message {
        Some(Ok(text)) => {
            ui.label(text);
        }
        Some(Err(text)) => {
            ui.colored_label(ui.visuals().error_fg_color, text);
        }
        None => {}
    }
}

fn number_or_dash(v: &Value) -> String {
    v.as_f64().map_or_else(|| "—".into(), compact_number)
}

/// A computed value for reading: three significant figures, scientific outside 0.001 to 1e5.
fn short(n: f64) -> String {
    let a = n.abs();
    if a == 0. || !a.is_finite() {
        return compact_number(n);
    }
    if (1e-3..1e5).contains(&a) {
        let digits = (2 - a.log10().floor() as i32).max(0) as usize;
        let text = format!("{n:.digits$}");
        if text.contains('.') {
            text.trim_end_matches('0').trim_end_matches('.').to_owned()
        } else {
            text
        }
    } else {
        let text = format!("{n:.2e}");
        match text.split_once('e') {
            Some((m, e)) => format!("{}e{e}", m.trim_end_matches('0').trim_end_matches('.')),
            None => text,
        }
    }
}

fn value_or_dash(v: &Value) -> String {
    v.as_f64().map_or_else(|| "—".into(), short)
}

/// A limit entry `{status, wt_pct}` in words.
fn limit_text(v: &Value) -> (String, Option<&'static str>) {
    match v["status"].as_str() {
        Some("limit") => (format!("{} wt%", value_or_dash(&v["wt_pct"])), None),
        Some("no response")
            if v["detail"]
                .as_str()
                .is_some_and(|d| d.contains("exhausted") || d.contains("composition bound")) =>
        {
            (
                "any share".into(),
                Some("The limit is not reached before the balance element runs out."),
            )
        }
        Some("no response") => (
            "not raised".into(),
            Some("This impurity does not raise the endpoint, so it has no limit."),
        ),
        Some("infeasible") => (
            "over already".into(),
            Some("The limit is exceeded even with none of this impurity."),
        ),
        Some("composition_bound") => (
            "any share".into(),
            Some("The limit is not reached before the balance runs out."),
        ),
        Some(other) => (other.to_owned(), None),
        None => ("—".into(), None),
    }
}

/// A result's nuclear-data bounds: (confidence, samples), when it has them.
fn bands_of(result: &Value) -> Option<(f64, u64)> {
    let u = &result["uncertainty"];
    Some((
        u["confidence_level"].as_f64()?,
        u["order_statistic"]["n"].as_u64()?,
    ))
}

/// The upper bound on a value over the samples, as read.
fn value_bound(v: &Value) -> Option<String> {
    match v["status"].as_str()? {
        "bounded" => v["upper_bound"].as_f64().map(short),
        other => Some(other.replace('_', " ")),
    }
}

/// The lower bound on a limit over the samples, and why when it is not simply a share.
fn limit_bound(l: &Value) -> Option<(String, Option<String>)> {
    let n = l["by_sample"].as_array().map_or(0, Vec::len);
    let count = |s: &str| l["sample_status"][s].as_u64().unwrap_or(0);
    Some(match l["status"].as_str()? {
        "established" => {
            let bound = l["bound"].as_f64()?;
            let over = count("infeasible") + count("failed");
            if bound == 0. && over > 0 {
                (
                    "0".into(),
                    Some(format!("In {over} of {n} samples the limit is exceeded even without this impurity (or the sample failed), so the bound is 0.")),
                )
            } else {
                (format!("≥ {}", short(bound)), None)
            }
        }
        "not_established" => (
            "not established".into(),
            Some(l["reason"].as_str().map_or_else(
                || "Some samples need a positive share of this impurity to meet the limit, so no lower bound applies.".to_owned(),
                str::to_owned,
            )),
        ),
        "unbounded" => (
            "any share".into(),
            Some("The limit is not reached in enough samples to bound it.".into()),
        ),
        other => (other.replace('_', " "), l["reason"].as_str().map(str::to_owned)),
    })
}

/// A figure with its bound beside it, the bound muted, in one grid cell; each with an optional
/// explanation on hover.
fn with_bound(
    ui: &mut egui::Ui,
    text: String,
    why: Option<&str>,
    bound: Option<(String, Option<String>)>,
) {
    ui.horizontal(|ui| {
        let r = ui.label(text);
        if let Some(why) = why {
            r.on_hover_text(why);
        }
        if let Some((b, why)) = bound {
            let r = ui.label(RichText::new(b).color(muted(ui)));
            if let Some(why) = why {
                r.on_hover_text(why);
            }
        }
    });
}

fn result_view(
    ui: &mut egui::Ui,
    label: &str,
    result: &Value,
    state: &mut ImpuritiesState,
    action: &mut ImpuritiesAction,
) {
    let base_steps = if result["base_spec"].is_object() {
        schedule(&result["base_spec"])
    } else {
        Vec::new()
    };
    let times: std::collections::BTreeMap<u64, f64> = result["targets"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|t| Some((t["step"].as_u64()?, t["t_s"].as_f64()?)))
        .collect();
    let step_name = |step: u64| {
        if !base_steps.is_empty() {
            format!("Step {step} · {}", when(&base_steps, step))
        } else if let Some(t) = times.get(&step) {
            format!("Step {step} · t = {}", model::readable_time(*t))
        } else {
            format!("Step {step}")
        }
    };
    let units: std::collections::BTreeMap<&str, &str> = result["targets"][0]["endpoints"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|e| Some((e["response"].as_str()?, e["unit"].as_str()?)))
        .collect();
    ui.horizontal_wrapped(|ui| {
        ui.label(RichText::new(label).strong());
        if ui.button("Save result…").clicked() {
            *action = ImpuritiesAction::SaveResult;
        }
        if ui.button("Open result…").clicked() {
            *action = ImpuritiesAction::OpenResult;
        }
    });
    let impurities: Vec<String> = result["impurities"]
        .as_object()
        .map(|m| m.keys().cloned().collect())
        .unwrap_or_default();
    ui.label(
        RichText::new(format!(
            "Balance {} · {} impurities ({}) · {} element solves{}",
            display_name(result["balance"].as_str().unwrap_or("?")),
            impurities.len(),
            impurities
                .iter()
                .map(|e| display_name(e))
                .collect::<Vec<_>>()
                .join(", "),
            result["element_solves"].as_array().map_or(0, Vec::len),
            result["elapsed_ms"]
                .as_f64()
                .map_or(String::new(), |ms| format!(
                    " · {}",
                    model::readable_time(ms / 1e3)
                )),
        ))
        .color(muted(ui)),
    );
    let verification = &result["verification"];
    // with bounds, the samples behind them are re-solved too; verified covers both
    let sampled = verification["uncertainty"]
        .as_object()
        .filter(|u| u.get("skipped").and_then(Value::as_bool) != Some(true));
    let sampled_points = sampled
        .and_then(|u| u.get("points"))
        .and_then(Value::as_array);
    ui.add_space(4.);
    match verification["verified"].as_bool() {
        Some(true) => {
            ui.horizontal_top(|ui| {
                crate::visuals::tick(ui, good(ui));
                ui.add(egui::Label::new(RichText::new(format!(
                    "Verified: every reported composition ({}) was re-solved as a full alloy; largest difference {} (tolerance {}).{}",
                    verification["points"].as_array().map_or(0, Vec::len),
                    number_or_dash(&verification["max_rel_dev"]),
                    number_or_dash(&verification["tolerance_rel"]),
                    sampled_points.map_or(String::new(), |p| format!(
                        " The samples behind the bounds were re-solved too ({} points); largest difference {}.",
                        p.len(),
                        number_or_dash(&verification["uncertainty"]["max_rel_dev"]),
                    )),
                )).color(good(ui))).wrap());
            });
        }
        Some(false) => {
            let nominal_ok = verification["max_rel_dev"]
                .as_f64()
                .zip(verification["tolerance_rel"].as_f64())
                .is_some_and(|(d, t)| d <= t);
            let message = match sampled {
                Some(u) if nominal_ok => {
                    let missed = sampled_points.map_or(0, |p| {
                        p.iter().filter(|x| x["pass"].as_bool() == Some(false)).count()
                    });
                    format!(
                        "Verification failed: {missed} of the {} checks on the samples behind the bounds differ from the screen (largest difference {}). The nominal figures were confirmed; do not rely on the bounds.",
                        sampled_points.map_or(0, Vec::len),
                        number_or_dash(u.get("max_rel_dev").unwrap_or(&Value::Null)),
                    )
                }
                _ => format!(
                    "Verification failed: a re-solved composition differs from the screen by {} (tolerance {}). Do not rely on these numbers.",
                    number_or_dash(&verification["max_rel_dev"]),
                    number_or_dash(&verification["tolerance_rel"]),
                ),
            };
            ui.colored_label(ui.visuals().error_fg_color, message);
        }
        None => {
            ui.colored_label(ui.visuals().warn_fg_color, "Not verified: this screen was run without re-solving its compositions (--no-verify).");
        }
    }
    let bands = bands_of(result);
    if let Some((confidence, n)) = bands {
        let u = &result["uncertainty"];
        let channels: Vec<&str> = u["sampling"]["channels"]
            .as_object()
            .into_iter()
            .flatten()
            .filter(|(_, on)| on.as_bool() == Some(true))
            .map(|(c, _)| match c.as_str() {
                "cross_section_mf33" => "cross sections",
                "decay_constants" => "decay constants",
                other => other,
            })
            .collect();
        let failed = u["failed_samples"].as_array().map_or(0, Vec::len);
        ui.label(format!(
            "Nuclear-data bounds at {} confidence from {n} samples of {}{}. Grey figures are these bounds: an upper bound on each value and change, a lower bound on each limit.",
            percent(confidence),
            if channels.is_empty() { "the data".to_owned() } else { channels.join(" and ") },
            if failed > 0 { format!(" ({failed} failed, counted against the bounds)") } else { String::new() },
        ));
    }
    ui.add_space(10.);

    // At a glance: each endpoint over every screened step.
    let summary = &result["summary"];
    card(ui, |ui| {
        ui.label(RichText::new("At a glance, over every screened step").strong());
        ui.add_space(6.);
        egui::Grid::new("impurity-glance")
            .num_columns(4)
            .spacing([22., 6.])
            .striped(true)
            .show(ui, |ui| {
                for h in [
                    "Endpoint",
                    "Worst case",
                    "Largest at nominal",
                    "Tightest limit, others at nominal",
                ] {
                    ui.label(RichText::new(h).color(muted(ui)));
                }
                ui.end_row();
                for id in result["endpoints"]
                    .as_array()
                    .into_iter()
                    .flatten()
                    .filter_map(Value::as_str)
                {
                    let s = &summary[id];
                    ui.label(scalar_name(id));
                    let worst = &s["worst"];
                    if let Some(class) = worst["class"].as_str() {
                        ui.label(format!(
                            "class {class} · {}",
                            step_name(worst["step"].as_u64().unwrap_or(0))
                        ));
                    } else {
                        let step = worst["step"].as_u64().unwrap_or(0);
                        let bound = bands.and_then(|(confidence, _)| {
                            let band = &endpoint_at(result, id, step)?["uncertainty"]["worst_case"];
                            Some((
                                format!("≤ {} at {}", value_bound(band)?, percent(confidence)),
                                None,
                            ))
                        });
                        with_bound(
                            ui,
                            format!(
                                "{} {} · {}",
                                value_or_dash(&worst["value"]),
                                shown_unit(units.get(id).copied().unwrap_or("")),
                                step_name(step)
                            ),
                            None,
                            bound,
                        );
                    }
                    let top = &s["ranking"][0];
                    ui.label(top["impurity"].as_str().map_or("—".into(), display_name));
                    let class_limits = &s["impurity_class_limits"]["impurities"];
                    let tightest = if class_limits.is_object() {
                        class_limits.as_object().and_then(|m| {
                            m.iter()
                                .filter(|(_, v)| v["others_at_nominal"]["status"] == "limit")
                                .filter_map(|(e, v)| {
                                    v["others_at_nominal"]["upper_supremum"]
                                        .as_f64()
                                        .map(|w| (e, w))
                                })
                                .min_by(|a, b| a.1.total_cmp(&b.1))
                        })
                    } else {
                        s["tightest_limit"]["others_at_nominal"]
                            .as_object()
                            .and_then(|m| {
                                m.iter()
                                    .filter_map(|(e, v)| v["wt_pct"].as_f64().map(|w| (e, w)))
                                    .min_by(|a, b| a.1.total_cmp(&b.1))
                            })
                    };
                    match tightest {
                        Some((e, w)) => ui.label(format!("{} ≤ {} wt%", display_name(e), short(w))),
                        None if class_limits.is_object() => ui.label("no limit reached"),
                        None if s["tightest_limit"].is_object() => {
                            let infeasible = s["tightest_limit"]["others_at_nominal"]
                                .as_object()
                                .is_some_and(|m| m.values().any(|v| v["status"] == "infeasible"));
                            ui.label(if infeasible {
                                "exceeded even without impurities"
                            } else {
                                "no limit reached"
                            })
                        }
                        None => ui.label(RichText::new("no limit set").color(muted(ui))),
                    };
                    ui.end_row();
                }
            });
    });

    // One step in detail.
    let targets = result["targets"].as_array().map_or(&[][..], Vec::as_slice);
    if targets.is_empty() {
        return;
    }
    state.step = state.step.min(targets.len() - 1);
    ui.add_space(6.);
    ui.horizontal_wrapped(|ui| {
        ui.label("Show");
        for (i, t) in targets.iter().enumerate() {
            let step = t["step"].as_u64().unwrap_or(0);
            if ui
                .selectable_label(state.step == i, step_name(step))
                .clicked()
            {
                state.step = i;
            }
        }
    });
    ui.add_space(6.);
    let target = &targets[state.step];
    for endpoint in target["endpoints"].as_array().into_iter().flatten() {
        if endpoint["response"]
            .as_str()
            .is_some_and(|r| r.starts_with("waste_class:"))
        {
            let id = endpoint["response"].as_str().unwrap_or("");
            class_card(
                ui,
                endpoint,
                &summary[id],
                bands.map(|b| (b, &result["uncertainty"]["impurity_class_limits"][id])),
            );
        } else {
            scalar_card(ui, endpoint, bands);
        }
    }
}

/// One endpoint's entry at one result step.
fn endpoint_at<'a>(result: &'a Value, id: &str, step: u64) -> Option<&'a Value> {
    result["targets"]
        .as_array()?
        .iter()
        .find(|t| t["step"].as_u64() == Some(step))?["endpoints"]
        .as_array()?
        .iter()
        .find(|e| e["response"] == id)
}

/// A bar for a contribution: its length is its share of the largest, accent when it adds and grey
/// when it lowers the endpoint (by displacing the balance).
fn bar(ui: &mut egui::Ui, value: f64, largest: f64) {
    let (rect, _) = ui.allocate_exact_size(egui::vec2(110., 10.), egui::Sense::hover());
    let painter = ui.painter();
    painter.rect_filled(rect, 2., ui.visuals().faint_bg_color);
    if largest > 0. && value != 0. {
        let mut fill = rect;
        fill.set_width(rect.width() * (value.abs() / largest).clamp(0.01, 1.) as f32);
        let color = if value > 0. {
            accent(ui)
        } else {
            Color32::from_gray(150)
        };
        painter.rect_filled(fill, 2., color);
    }
}

/// A unit to print after a number; the clearance index is a pure number.
fn shown_unit(unit: &str) -> &str {
    if unit.starts_with("1 ") {
        ""
    } else {
        unit
    }
}

fn scalar_card(ui: &mut egui::Ui, e: &Value, bands: Option<(f64, u64)>) {
    let id = e["response"].as_str().unwrap_or("");
    let unit = shown_unit(e["unit"].as_str().unwrap_or(""));
    let limit = e["limit"].as_f64();
    let worst = e["worst_case"]["value"].as_f64();
    let u = bands.map(|b| (b, &e["uncertainty"]));
    let at = |confidence: f64| format!("at {}", percent(confidence));
    card(ui, |ui| {
        ui.horizontal(|ui| {
            ui.label(RichText::new(scalar_name(id)).strong());
            ui.label(RichText::new(unit).color(muted(ui)));
            if let (Some(limit), Some(worst)) = (limit, worst) {
                let worst_bound = u.and_then(|((c, _), u)| {
                    (u["worst_case"]["status"] == "bounded")
                        .then(|| (c, u["worst_case"]["upper_bound"].as_f64()))
                });
                ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                    if let Some((c, bound)) = worst_bound.filter(|_| worst <= limit) {
                        if bound.is_some_and(|b| b <= limit) {
                            ui.colored_label(
                                good(ui),
                                format!(
                                    "worst case within the limit of {} {unit}, also {}",
                                    short(limit),
                                    at(c)
                                ),
                            );
                        } else {
                            ui.colored_label(
                                ui.visuals().warn_fg_color,
                                format!(
                                    "worst case within the limit of {} {unit}, but not {}",
                                    short(limit),
                                    at(c)
                                ),
                            );
                        }
                    } else if worst <= limit {
                        ui.colored_label(
                            good(ui),
                            format!("worst case within the limit of {} {unit}", short(limit)),
                        );
                    } else {
                        ui.colored_label(
                            ui.visuals().warn_fg_color,
                            format!("worst case over the limit of {} {unit}", short(limit)),
                        );
                    }
                });
            }
        });
        let corner = e["worst_case"]["corner"].as_object().map(|m| {
            m.iter()
                .map(|(el, side)| format!("{} {}", display_name(el), side.as_str().unwrap_or("")))
                .collect::<Vec<_>>()
                .join(", ")
        });
        ui.horizontal_wrapped(|ui| {
            ui.label(format!("Nominal {}", value_or_dash(&e["nominal"])));
            ui.label(RichText::new("·").color(muted(ui)));
            let nominal = e["nominal"].as_f64().unwrap_or(0.).abs();
            let without = match e["matrix_only"].as_f64() {
                Some(m) if m.abs() < 1e-12 * nominal => "≈ 0".to_owned(),
                _ => value_or_dash(&e["matrix_only"]),
            };
            ui.label(format!("without impurities {without}"));
            ui.label(RichText::new("·").color(muted(ui)));
            let w = ui.label(
                RichText::new(format!(
                    "worst case {}",
                    value_or_dash(&e["worst_case"]["value"])
                ))
                .strong(),
            );
            w.on_hover_text(format!(
                "{}{}",
                corner.map_or(String::new(), |c| format!("At {c}.\n")),
                e["worst_case"]["method"].as_str().unwrap_or("")
            ));
        });
        if let Some(((c, _), u)) = u {
            let up = |key: &str| value_bound(&u[key]).unwrap_or_else(|| "—".into());
            ui.label(
                RichText::new(format!(
                    "Upper bounds {}: nominal {} · without impurities {} · worst case {}",
                    at(c),
                    up("nominal"),
                    up("matrix_only"),
                    up("worst_case")
                ))
                .color(muted(ui)),
            );
        }
        if let Some(k) = e.get("joint_k").filter(|k| k.is_object()) {
            let text = match k["status"].as_str() {
                Some("limit") => format!(
                    "All impurities together may rise to {}× their nominal shares before the limit.",
                    value_or_dash(&k["value"])
                ),
                Some("infeasible") => "The limit is exceeded even without the impurities.".into(),
                Some("no response") if k["detail"].as_str().is_some_and(|d| d.contains("exhausted") || d.contains("composition bound")) => {
                    "All impurities together cannot reach the limit before the balance element runs out.".into()
                }
                Some("no response") => "Together, in their nominal proportions, the impurities do not raise this endpoint.".into(),
                Some(other) => other.to_owned(),
                None => String::new(),
            };
            let bound = u
                .and_then(|((c, _), u)| {
                    let b = limit_bound(&u["joint_k"])?;
                    // a bound of 0 because samples are over without impurities is said below
                    (b.0 != "0" || over_without(e, u).is_none()).then_some((c, b))
                })
                .map_or(String::new(), |(c, (b, _))| {
                    format!(" Bound {}: {b}×.", at(c))
                });
            ui.label(RichText::new(format!("{text}{bound}")).color(muted(ui)));
        }
        if let Some(((c, _), u)) = u {
            if let Some((over, n)) = over_without(e, u) {
                ui.colored_label(
                    ui.visuals().warn_fg_color,
                    format!(
                        "Without impurities the limit is already exceeded in {over} of the {n} samples, so {} no share of any impurity is assured: the bounds on the limits are 0.",
                        at(c)
                    ),
                );
            }
        }
        ui.add_space(6.);
        let rows: Vec<(&str, &Value)> = e["ranking"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(Value::as_str)
            .map(|el| (el, &e["impurities"][el]))
            .collect();
        let largest = rows
            .iter()
            .filter_map(|(_, v)| v["contribution_nominal"].as_f64())
            .fold(0., |a: f64, b| a.max(b.abs()));
        let has_limits = rows.iter().any(|(_, v)| v["limits"].is_object());
        let nuclides_known = rows.iter().any(|(_, v)| v["top_nuclides"].is_array());
        let scale = e["nominal"]
            .as_f64()
            .unwrap_or(0.)
            .abs()
            .max(e["worst_case"]["value"].as_f64().unwrap_or(0.).abs());
        let mut columns = vec!["Impurity", "Range wt%", "Adds at nominal", "", "at max"];
        if nuclides_known {
            columns.push("Mostly through");
        }
        if has_limits {
            columns.extend(["Max alone", "others nominal", "others at worst"]);
        }
        egui::Grid::new(("impurity-ranking", id))
            .num_columns(columns.len())
            .spacing([16., 5.])
            .striped(true)
            .show(ui, |ui| {
                for h in &columns {
                    ui.label(RichText::new(*h).color(muted(ui)));
                }
                ui.end_row();
                for (el, v) in rows {
                    ui.monospace(display_name(el));
                    ui.label(format!(
                        "{} ({}–{})",
                        number_or_dash(&v["nominal_wt_pct"]),
                        number_or_dash(&v["min_wt_pct"]),
                        number_or_dash(&v["max_wt_pct"])
                    ));
                    // a contribution at round-off level of the endpoint reads as zero
                    let signed = |c: f64| {
                        if c.abs() < 1e-12 * scale {
                            "≈ 0".to_owned()
                        } else {
                            format!("{}{}", if c > 0. { "+" } else { "" }, short(c))
                        }
                    };
                    let c = v["contribution_nominal"].as_f64().unwrap_or(0.);
                    let band = u.and_then(|(_, u)| {
                        let b = &u["impurities"][el]["contribution_nominal"];
                        let text = match b["upper_bound"].as_f64() {
                            Some(x) if b["status"] == "bounded" && signed(x) == "≈ 0" => {
                                return None
                            }
                            Some(x) if b["status"] == "bounded" => format!("≤ {}", signed(x)),
                            _ => value_bound(b)?,
                        };
                        Some((text, None))
                    });
                    with_bound(ui, signed(c), None, band);
                    bar(ui, c, largest);
                    ui.label(signed(v["contribution_at_max"].as_f64().unwrap_or(0.)));
                    let nuclides = v["top_nuclides"]
                        .as_array()
                        .map(|n| {
                            n.iter()
                                .filter_map(|p| p[0].as_str())
                                .collect::<Vec<_>>()
                                .join(", ")
                        })
                        .unwrap_or_default();
                    if nuclides_known {
                        ui.label(if nuclides.is_empty() {
                            "—".to_owned()
                        } else {
                            nuclides
                        });
                    }
                    if has_limits {
                        for which in ["alone", "others_at_nominal", "others_at_worst"] {
                            let nominal = &v["limits"][which];
                            let (text, why) = limit_text(nominal);
                            let band = u.and_then(|(_, u)| {
                                let band = &u["impurities"][el]["limits"][which];
                                // the note above the table says why every bound is 0
                                limit_bound(band).filter(|(b, _)| {
                                    *b != text
                                        && !restates(nominal, band)
                                        && !(*b == "0" && over_without(e, u).is_some())
                                })
                            });
                            with_bound(ui, text, why, band);
                        }
                    }
                    ui.end_row();
                }
            });
        if has_limits {
            ui.add_space(4.);
            ui.label(RichText::new("Max: the largest share of that impurity at which this step stays within the limit, with the other impurities absent, at nominal, or at their worst.").color(muted(ui)).small());
        }
        if rows_negative(e) {
            ui.label(RichText::new("A negative change means the impurity lowers the endpoint: it takes the place of balance element that activates more.").color(muted(ui)).small());
        }
    });
}

/// A limit's bound that only restates its nominal limit: 0 where the limit is already exceeded,
/// unbounded where the impurity does not raise the endpoint, or every sample at the balance cap
/// where any share is allowed.
fn restates(nominal: &Value, band: &Value) -> bool {
    let established = band["status"] == "established";
    match nominal["status"].as_str() {
        Some("infeasible") => established && band["bound"].as_f64() == Some(0.),
        Some("no response" | "composition_bound") => {
            band["status"] == "unbounded"
                || (established
                    && band["sample_status"]["capped"].as_u64().is_some_and(|c| {
                        c > 0 && Some(c) == band["by_sample"].as_array().map(|b| b.len() as u64)
                    }))
        }
        _ => false,
    }
}

/// When the endpoint is within its limit without impurities at the nominal data but not in some
/// samples: (samples over the limit, samples).
fn over_without(e: &Value, u: &Value) -> Option<(usize, usize)> {
    let limit = e["limit"].as_f64()?;
    if e["matrix_only"].as_f64()? > limit {
        return None;
    }
    let samples = u["matrix_only"]["by_sample"].as_array()?;
    let over = samples
        .iter()
        .filter(|v| v.as_f64().is_some_and(|v| v > limit))
        .count();
    (over > 0).then_some((over, samples.len()))
}

/// Whether any impurity of a scalar endpoint lowers it.
fn rows_negative(e: &Value) -> bool {
    e["impurities"].as_object().is_some_and(|m| {
        m.values()
            .any(|v| v["contribution_nominal"].as_f64().is_some_and(|c| c < 0.))
    })
}

/// `bands`: the result's (confidence, samples) and this endpoint's class-limit bounds.
fn class_card(ui: &mut egui::Ui, e: &Value, summary: &Value, bands: Option<((f64, u64), &Value)>) {
    let target = e["target_class"].as_str().unwrap_or("?");
    card(ui, |ui| {
        ui.horizontal(|ui| {
            ui.label(RichText::new(format!("Waste class, target {target}")).strong());
            ui.label(RichText::new("10 CFR 61.55").color(muted(ui)));
            ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                if e["worst_case"]["meets_target"].as_bool() == Some(true) {
                    ui.colored_label(good(ui), format!("meets class {target} at the worst case"));
                } else {
                    ui.colored_label(
                        ui.visuals().warn_fg_color,
                        format!("may exceed class {target} at the worst case"),
                    );
                }
            });
        });
        ui.horizontal_wrapped(|ui| {
            ui.label(format!(
                "Nominal class {}",
                e["nominal_class"].as_str().unwrap_or("—")
            ));
            ui.label(RichText::new("·").color(muted(ui)));
            ui.label(format!(
                "without impurities {}",
                e["matrix_only"]["class"].as_str().unwrap_or("—")
            ));
            ui.label(RichText::new("·").color(muted(ui)));
            ui.label(RichText::new(format!(
                "worst case {}",
                e["worst_case"]["class"].as_str().unwrap_or("—")
            )))
            .on_hover_text(e["worst_case"]["method"].as_str().unwrap_or(""));
            if let Some(b) = e["binding_constraint_at_nominal"].as_str() {
                ui.label(RichText::new("·").color(muted(ui)));
                ui.label(format!("binding: {}", b.replace('_', " ")));
            }
        });
        if let Some(((confidence, n), _)) = bands {
            let meets = &e["uncertainty"]["meets_target"];
            let part = |which: &str| {
                let m = &meets[which];
                format!(
                    "in {} of {n} (probability ≥ {}%)",
                    m["count"].as_u64().unwrap_or(0),
                    m["lower"].as_f64().map_or("—".into(), |p| short(p * 100.)),
                )
            };
            ui.label(
                RichText::new(format!(
                    "Over the samples, at {} confidence: class {target} at nominal {} · at the worst case {}",
                    percent(confidence),
                    part("nominal"),
                    part("worst_case"),
                ))
                .color(muted(ui)),
            );
        }
        ui.add_space(6.);
        let rows: Vec<(&str, &Value)> = e["ranking"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(Value::as_str)
            .map(|el| (el, &e["impurities"][el]))
            .collect();
        let largest = rows
            .iter()
            .filter_map(|(_, v)| v["contribution_nominal"].as_f64())
            .fold(0., |a: f64, b| a.max(b.abs()));
        let limits = &summary["impurity_class_limits"]["impurities"];
        egui::Grid::new(("impurity-class", target))
            .num_columns(6)
            .spacing([16., 5.])
            .striped(true)
            .show(ui, |ui| {
                for h in [
                    "Impurity",
                    "Adds to the table sum, at nominal",
                    "",
                    "at max",
                    "Max alone",
                    "others nominal",
                ] {
                    ui.label(RichText::new(h).color(muted(ui)));
                }
                ui.end_row();
                for (el, v) in rows {
                    ui.monospace(display_name(el));
                    let c = v["contribution_nominal"].as_f64().unwrap_or(0.);
                    ui.label(short(c));
                    bar(ui, c, largest);
                    ui.label(value_or_dash(&v["contribution_at_max"]));
                    for which in ["alone", "others_at_nominal"] {
                        let l = &limits[el][which];
                        let (text, why) = match l["status"].as_str() {
                            Some("limit") => {
                                (format!("{} wt%", value_or_dash(&l["upper_supremum"])), None)
                            }
                            _ => limit_text(l),
                        };
                        let band =
                            bands.and_then(|(_, b)| limit_bound(&b["impurities"][el][which]));
                        with_bound(ui, text, why, band);
                    }
                    ui.end_row();
                }
            });
        ui.add_space(4.);
        ui.label(RichText::new(format!("Max: the share below which the component stays class {target} at every screened step. Table sums are fractions of the class limit: 1 is the boundary.")).color(muted(ui)).small());
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    fn problem() -> Value {
        json!({
            "material": {"mass_g": 1000.0, "basis": "wt_percent", "density_g_cm3": 7.8,
                         "composition": {"FE": 90.0, "CR": 9.0, "CO": 0.01, "NI": 0.99}},
            "schedule": [{"dt": "2 y", "flux": 1.0}, {"dt": "1 d", "flux": 0.0}, {"dt": "1 y", "flux": 0.0}],
        })
    }

    #[test]
    fn the_largest_element_is_the_balance_and_the_rest_start_fixed() {
        let mut state = ImpuritiesState::default();
        sync(&mut state, &problem());
        let roles: Vec<(&str, Role)> = state
            .rows
            .iter()
            .map(|r| (r.element.as_str(), r.role))
            .collect();
        assert!(roles.contains(&("Fe", Role::Balance)), "{roles:?}");
        assert!(roles.contains(&("Co", Role::Fixed)), "{roles:?}");
        assert_eq!(roles.iter().filter(|(_, r)| *r == Role::Balance).count(), 1);
    }

    #[test]
    fn cooling_steps_after_shutdown_are_the_default_targets() {
        let steps = schedule(&problem());
        assert_eq!(default_targets(&steps), BTreeSet::from([2, 3]));
        assert_eq!(when(&steps, 1), "at shutdown");
        assert_eq!(
            when(&steps, 2),
            format!("{} cooling", model::readable_time(86400.))
        );
    }

    #[test]
    fn the_screen_document_follows_the_rows_and_choices() {
        let doc = problem();
        let mut state = ImpuritiesState::default();
        sync(&mut state, &doc);
        assert!(!problems(&state, &doc).is_empty(), "no impurity yet");
        for r in state.rows.iter_mut().filter(|r| r.element == "Co") {
            r.role = Role::Impurity;
            r.min = 0.;
            r.nominal = 0.01;
            r.max = 0.05;
        }
        state.nuclides.push(("Nb94".into(), Some(100.)));
        state.waste = true;
        state.targets = Some(default_targets(&schedule(&doc)));
        assert_eq!(problems(&state, &doc), Vec::<String>::new());
        let screen = screen_document(&state, &doc, json!({"title": "base"})).unwrap();
        assert_eq!(screen["schema"], SCREEN_SCHEMA);
        assert_eq!(screen["balance"], "Fe");
        assert_eq!(
            screen["impurities"]["Co"],
            json!({"nominal": 0.01, "min": 0.0, "max": 0.05})
        );
        assert_eq!(screen["matrix"]["Cr"], json!(9.0));
        assert!(screen["matrix"].get("Fe").is_none());
        assert_eq!(screen["targets"], json!([2, 3]));
        assert_eq!(
            screen["endpoints"],
            json!([{"endpoint": "activity.total"}, {"endpoint": "heat.total"},
                   {"endpoint": "activity:Nb94", "limit": 100.0},
                   {"endpoint": "waste_class", "class": "C"}])
        );
        assert_eq!(screen["waste"]["density_g_cm3"], json!(7.8));
        // The CLI's own parser accepts what the page writes.
        #[cfg(not(target_arch = "wasm32"))]
        for e in screen["endpoints"].as_array().unwrap() {
            actinv_cli::impurities::parse_endpoint(e).unwrap();
        }
    }

    #[test]
    fn impurity_maxima_that_leave_no_balance_are_refused() {
        let doc = problem();
        let mut state = ImpuritiesState::default();
        sync(&mut state, &doc);
        for r in state.rows.iter_mut().filter(|r| r.element == "Co") {
            r.role = Role::Impurity;
            r.max = 95.;
        }
        assert!(problems(&state, &doc)
            .iter()
            .any(|p| p.contains("balance keeps a share")));
    }

    #[test]
    fn power_driven_and_atom_fraction_problems_are_explained() {
        let mut doc = problem();
        doc["material"]["basis"] = json!("atom_fraction");
        doc["schedule"][0] = json!({"dt": "1 y", "power_W_per_g": 1.0});
        let p = base_problems(&doc);
        assert!(p.iter().any(|m| m.contains("weight percent")), "{p:?}");
        assert!(p.iter().any(|m| m.contains("power-driven")), "{p:?}");
    }

    #[test]
    fn an_added_impurity_survives_a_material_edit() {
        let mut doc = problem();
        let mut state = ImpuritiesState::default();
        sync(&mut state, &doc);
        state.rows.push(Row {
            element: "Nb".into(),
            role: Role::Impurity,
            share: 0.,
            nominal: 0.,
            min: 0.,
            max: 0.005,
            added: true,
        });
        doc["material"]["composition"]["MN"] = json!(0.4);
        sync(&mut state, &doc);
        assert!(state.rows.iter().any(|r| r.element == "Nb" && r.added));
        assert!(state.rows.iter().any(|r| r.element == "Mn"));
    }

    #[test]
    fn nulls_are_dropped_so_optional_fields_read_as_absent() {
        let v = without_nulls(json!({"a": null, "b": [{"c": null, "d": 1}]}));
        assert_eq!(v, json!({"b": [{"d": 1}]}));
    }

    #[test]
    fn a_screen_result_renders_in_both_themes() {
        let result = json!({
            "schema": RESULT_SCHEMA, "balance": "Fe", "elapsed_ms": 1500.0,
            "impurities": {"Co": {"nominal": 0.005, "min": 0.0, "max": 0.01}},
            "element_solves": [{"element": "Fe"}, {"element": "Co"}],
            "endpoints": ["heat.total", "waste_class:C"],
            "base_spec": problem(),
            "summary": {
                "heat.total": {"ranking": [{"impurity": "Co", "step": 2}], "worst": {"step": 2, "value": 1e-3},
                    "tightest_limit": {"others_at_nominal": {"Co": {"status": "limit", "step": 2, "wt_pct": 0.06}}}},
                "waste_class:C": {"ranking": [{"impurity": "Co", "step": 2}], "worst": {"class": "C", "step": 2},
                    "impurity_class_limits": {"impurities": {"Co": {"alone": {"status": "composition_bound"},
                        "others_at_nominal": {"status": "limit", "upper_supremum": 1.5}}}}}
            },
            "targets": [{"step": 2, "t_s": 63201600.0, "endpoints": [
                {"response": "heat.total", "unit": "W/g", "kind": "linear", "limit": 0.014,
                 "nominal": 9e-4, "matrix_only": 8e-4, "joint_k": {"status": "limit", "value": 7.3},
                 "worst_case": {"value": 1e-3, "corner": {"Co": "max"}, "method": "exact"},
                 "ranking": ["Co"],
                 "impurities": {"Co": {"contribution_at_max": 2e-4, "nominal_wt_pct": 0.005, "min_wt_pct": 0.0,
                     "max_wt_pct": 0.01, "top_nuclides": [["Co60", 2e-4]],
                     "limits": {"alone": {"status": "limit", "wt_pct": 0.07},
                                "others_at_nominal": {"status": "no response", "wt_pct": null},
                                "others_at_worst": {"status": "infeasible", "wt_pct": null}}}}},
                {"response": "waste_class:C", "target_class": "C", "nominal_class": "A",
                 "matrix_only": {"class": "A"}, "binding_constraint_at_nominal": "table_1",
                 "worst_case": {"class": "B", "meets_target": true, "method": "corner"},
                 "ranking": ["Co"], "impurities": {"Co": {"contribution_at_max": 0.1}}}]}],
            "verification": {"verified": true, "max_rel_dev": 1e-15, "tolerance_rel": 1e-6, "points": [{}]}
        });
        // the same screen with nuclear-data bounds
        let mut banded = result.clone();
        let value = |m: f64| json!({"status": "bounded", "median": m, "upper_bound": 1.5 * m, "by_sample": [m]});
        let limit = |b: f64| {
            json!({"status": "established", "bound": b, "by_sample": [b],
            "sample_status": {"bound": 1, "infeasible": 0, "failed": 0}})
        };
        banded["uncertainty"] = json!({
            "confidence_level": 0.95, "order_statistic": {"k": 1, "n": 72, "minimum_samples": 72},
            "sampling": {"channels": {"cross_section_mf33": true, "decay_constants": true}},
            "failed_samples": [],
            "impurity_class_limits": {"waste_class:C": {"impurities": {"Co": {
                "alone": {"status": "unbounded", "by_sample": []}, "others_at_nominal": limit(1.2)}}}},
        });
        let heat = &mut banded["targets"][0]["endpoints"][0];
        heat["uncertainty"] = json!({
            "matrix_only": value(8e-4), "nominal": value(9e-4), "worst_case": value(1e-3),
            "joint_k": {"status": "established", "bound": 0.0, "by_sample": [0.0, 7.0],
                "sample_status": {"bound": 1, "infeasible": 1, "failed": 0}},
            "impurities": {"Co": {"contribution_nominal": value(1e-4),
                "limits": {"alone": limit(0.05), "others_at_nominal": {"status": "not_established", "reason": null},
                           "others_at_worst": limit(0.0)}}},
        });
        banded["targets"][0]["endpoints"][1]["uncertainty"] = json!({"meets_target": {
            "nominal": {"count": 72, "lower": 0.95}, "worst_case": {"count": 70, "lower": 0.903}}});
        assert_eq!(bands_of(&banded), Some((0.95, 72)));
        assert_eq!(bands_of(&result), None);
        assert_eq!(
            limit_bound(&banded["targets"][0]["endpoints"][0]["uncertainty"]["joint_k"])
                .map(|b| b.0),
            Some("0".to_owned())
        );
        assert_eq!(
            limit_bound(&limit(0.05)).map(|b| b.0),
            Some("≥ 0.05".to_owned())
        );
        for (result, dark) in [
            (&result, false),
            (&result, true),
            (&banded, false),
            (&banded, true),
        ] {
            let ctx = egui::Context::default();
            ctx.set_visuals(if dark {
                egui::Visuals::dark()
            } else {
                egui::Visuals::light()
            });
            let mut state = ImpuritiesState {
                result: Some(("r.json".into(), result.clone())),
                showing_result: true,
                ..Default::default()
            };
            state.bands.on = bands_of(result).is_some();
            let context = Context {
                can_screen: true,
                running: false,
            };
            for _ in 0..2 {
                let mut output = ctx.run_ui(Default::default(), |ui| {
                    assert_eq!(
                        show(ui, &problem(), &mut state, &context),
                        ImpuritiesAction::None
                    );
                });
                assert!(!output.shapes.is_empty());
                output.textures_delta.clear();
            }
            state.showing_result = false;
            let mut output = ctx.run_ui(Default::default(), |ui| {
                show(ui, &problem(), &mut state, &context);
            });
            output.textures_delta.clear();
        }
    }

    #[test]
    fn bounds_need_enough_samples_and_a_channel_and_go_into_the_screen() {
        assert_eq!(
            CONFIDENCES.map(minimum_samples),
            [29, 72, 528],
            "(1 - c)/2 >= c^n"
        );
        let mut state = ImpuritiesState::default();
        sync(&mut state, &problem());
        for r in &mut state.rows {
            if r.element == "Co" {
                r.role = Role::Impurity;
                r.min = 0.;
                r.nominal = 0.005;
                r.max = 0.01;
            }
        }
        state.targets = Some(default_targets(&schedule(&problem())));
        let plain = screen_document(&state, &problem(), json!({})).unwrap();
        assert!(plain.get("sampling").is_none() && plain.get("confidence_level").is_none());

        state.bands.on = true;
        state.bands.samples = 71;
        assert!(problems(&state, &problem())
            .iter()
            .any(|p| p.contains("at least 72 samples")));
        state.bands.samples = 72;
        state.bands.cross_sections = false;
        state.bands.decay = false;
        assert!(problems(&state, &problem())
            .iter()
            .any(|p| p.contains("decay constants or both")));
        state.bands.decay = true;
        let screen = screen_document(&state, &problem(), json!({})).unwrap();
        assert_eq!(
            screen["sampling"],
            json!({"channels": {"cross_section_mf33": false, "decay_constants": true}, "samples": 72, "seed": 1})
        );
        assert_eq!(screen["confidence_level"], json!(0.95));
        state.bands.cross_sections = true;
        let screen = screen_document(&state, &problem(), json!({})).unwrap();
        assert_eq!(screen["sampling"]["covariance"]["path"], COVARIANCE);
    }

    #[test]
    fn only_a_screen_result_is_accepted() {
        assert!(check_result(&json!({"schema": RESULT_SCHEMA, "targets": []})).is_ok());
        assert!(check_result(&json!({"schema": "actinv-waste-result-1"})).is_err());
    }
}
