//! Compare: how do materials, spectra and irradiation lengths compare? The page writes an
//! `actinv-study-1` grid around the open problem, from the material library and the standard
//! spectra, and ranks the cases of the `actinv-study-record-1` it reads back.
use crate::{model, views::Target};
use actinv_core::presets::SpectrumShape;
use eframe::egui::{self, RichText};
use egui_extras::{Column, TableBuilder};
use serde_json::{json, Value};
use std::collections::BTreeSet;

/// The page's question, under its heading.
pub const QUESTION: &str = "How do materials, spectra or irradiation lengths compare? Every combination is solved the same way as Run, then ranked side by side.";

/// The most cases one comparison may solve; `actinv study run` takes more from a file.
const MAX_CASES: usize = 200;
/// The name the open problem's own material, spectrum and irradiation take in the study.
const THIS: &str = "this-problem";
/// What every comparison records for each case and time.
const RESPONSES: [(&str, &str, &str); 2] = [
    ("total_activity_bq_per_g", "Activity", "Bq/g"),
    ("decay_heat_w_per_g", "Decay heat", "W/g"),
];

/// A finished comparison: its record and, beside it, the study it ran. Only the desktop loads one.
#[cfg_attr(target_arch = "wasm32", allow(dead_code))]
pub struct Found {
    pub path: std::path::PathBuf,
    pub record: Value,
    pub study: Value,
}

pub struct CompareState {
    pub this_material: bool,
    /// library material ids
    pub materials: BTreeSet<String>,
    pub this_spectrum: bool,
    pub shapes: Vec<SpectrumShape>,
    pub this_irradiation: bool,
    /// further irradiation lengths at full flux, such as "30 d, 5 y"
    pub lengths: String,
    /// which response and which time the ranking shows
    pub response: usize,
    pub time: usize,
    pub found: Option<Found>,
}

impl Default for CompareState {
    fn default() -> Self {
        Self {
            this_material: true,
            materials: BTreeSet::new(),
            this_spectrum: true,
            shapes: Vec::new(),
            this_irradiation: true,
            lengths: String::new(),
            response: 0,
            time: 0,
            found: None,
        }
    }
}

#[cfg_attr(target_arch = "wasm32", allow(dead_code))]
impl CompareState {
    /// Set the choices to the study that produced a record, so the two read together.
    pub fn show_study(&mut self, study: &Value) {
        let names = |kind: &str| -> Vec<String> {
            study["cases"][kind]
                .as_array()
                .into_iter()
                .flatten()
                .filter_map(|x| x["name"].as_str().map(str::to_owned))
                .collect()
        };
        let materials = names("materials");
        self.this_material = materials.iter().any(|n| n == THIS);
        self.materials = materials.into_iter().filter(|n| n != THIS).collect();
        let spectra = names("spectra");
        self.this_spectrum = spectra.iter().any(|n| n == THIS);
        self.shapes = SpectrumShape::ALL
            .into_iter()
            .filter(|s| spectra.iter().any(|n| n == s.id()))
            .collect();
        let schedules = names("schedules");
        self.this_irradiation = schedules.iter().any(|n| n == THIS);
        self.lengths = study["cases"]["schedules"]
            .as_array()
            .into_iter()
            .flatten()
            .filter(|s| s["name"] != THIS)
            .filter_map(|s| s["steps"][0]["dt"].as_str().map(str::to_owned))
            .collect::<Vec<_>>()
            .join(", ");
    }
}

/// Where the open problem's irradiation ends: the index of its last step with a flux.
fn last_irradiation(document: &Value) -> Option<usize> {
    document["schedule"]
        .as_array()?
        .iter()
        .rposition(|s| model::number(&s["flux"]) > 0.)
}

/// Why the open problem's material, spectrum or irradiation cannot join a comparison; `None` when it can.
fn this_material_problem(document: &Value) -> Option<&'static str> {
    if document["material"]["basis"] != "wt_percent" {
        return Some("its composition is not in weight percent");
    }
    if document["material"]["composition"]
        .as_object()
        .is_none_or(|c| c.is_empty())
    {
        return Some("it has no elements yet");
    }
    None
}

fn this_spectrum_problem(document: &Value) -> Option<&'static str> {
    let spectrum = &document["spectrum"];
    if !crate::views::source::has_flux(document) {
        return Some("it has no flux yet");
    }
    if !spectrum["rebin"].is_null() || !spectrum["wall_loading_MW_m2"].is_null() {
        return Some("a rebinned or wall-loading spectrum cannot be compared");
    }
    None
}

fn this_irradiation_problem(document: &Value) -> Option<&'static str> {
    let steps = document["schedule"].as_array()?;
    let last = last_irradiation(document)?;
    let plain = steps[..=last].iter().all(|s| {
        [
            "power_W_cm3",
            "power_W_per_g",
            "wall_loading_MW_m2",
            "partition",
            "library",
        ]
        .iter()
        .all(|k| s[*k].is_null())
    });
    (!plain).then_some("power, wall-loading or library-switch steps cannot be compared")
}

/// The flux the standard spectra take: the problem's total, or the sum of its groups.
fn problem_flux(document: &Value) -> f64 {
    let total = model::number(&document["spectrum"]["total"]);
    if total > 0. {
        return total;
    }
    document["spectrum"]["flux_per_group"]
        .as_array()
        .map_or(0., |v| v.iter().map(model::number).sum())
}

/// The cooling times the comparison reports, after shutdown: shutdown itself and the end of each
/// cooling step after the last irradiation.
fn cooling_times(document: &Value) -> Vec<f64> {
    let mut times = vec![0.];
    let (Some(steps), Some(last)) = (document["schedule"].as_array(), last_irradiation(document))
    else {
        return times;
    };
    let mut t = 0.;
    for step in &steps[last + 1..] {
        if let Some(Ok(dt)) = step["dt"].as_str().map(actinv_core::spec::parse_duration) {
            t += dt;
            if dt > 0. {
                times.push(t);
            }
        }
    }
    times
}

/// The irradiation lengths typed, as (duration text, study name).
fn parse_lengths(text: &str) -> Result<Vec<(String, String)>, String> {
    let mut out: Vec<(String, String)> = Vec::new();
    for part in text
        .split([',', ';'])
        .map(str::trim)
        .filter(|p| !p.is_empty())
    {
        actinv_core::spec::parse_duration(part)
            .ok()
            .filter(|s| s.is_finite() && *s > 0.)
            .ok_or_else(|| format!("'{part}' is not a duration such as 30 d or 2 y."))?;
        let name = format!(
            "irradiate-{}",
            part.chars()
                .filter(|c| c.is_ascii_alphanumeric() || *c == '.')
                .collect::<String>()
        );
        if out.iter().any(|(_, n)| *n == name) {
            continue;
        }
        out.push((part.to_owned(), name));
    }
    Ok(out)
}

/// The `actinv-study-1` grid: the open problem's material, spectrum and irradiation where chosen,
/// the chosen library materials and standard spectra, and further irradiation lengths at full flux,
/// all reported at the problem's cooling times. `run_spec` is the resolved open problem, whose data
/// references the study takes.
pub fn study_spec(
    run_spec: &Value,
    document: &Value,
    state: &CompareState,
) -> Result<Value, String> {
    let mut materials = Vec::new();
    if state.this_material {
        if let Some(why) = this_material_problem(document) {
            return Err(format!("The problem's material cannot be compared: {why}."));
        }
        materials.push(json!({
            "name": THIS,
            "mass_g": run_spec["material"]["mass_g"],
            "composition": run_spec["material"]["composition"],
        }));
    }
    for id in &state.materials {
        let preset = crate::views::material::library()
            .iter()
            .find(|p| p.id == *id)
            .ok_or_else(|| format!("'{id}' is not in the material library."))?;
        materials.push(json!({"name": id, "composition": preset.composition_wt_percent}));
    }
    let mut spectra = Vec::new();
    if state.this_spectrum {
        if let Some(why) = this_spectrum_problem(document) {
            return Err(format!("The problem's spectrum cannot be compared: {why}."));
        }
        let s = &run_spec["spectrum"];
        let mut spectrum = json!({
            "name": THIS,
            "structure": s["structure"],
            "flux_per_group": s["flux_per_group"],
            "descending": s["descending"].as_bool().unwrap_or(false),
        });
        for key in ["total", "boundaries_eV"] {
            if !s[key].is_null() {
                spectrum[key] = s[key].clone();
            }
        }
        spectra.push(spectrum);
    }
    if !state.shapes.is_empty() {
        let flux = problem_flux(document);
        if flux <= 0. {
            return Err(
                "The standard spectra take the problem's total flux; set the source first.".into(),
            );
        }
        for shape in SpectrumShape::ALL
            .iter()
            .filter(|s| state.shapes.contains(s))
        {
            let values = actinv_core::presets::spectrum(*shape, flux)?;
            spectra.push(json!({
                "name": shape.id(),
                "structure": "fispact-709",
                "flux_per_group": values,
                "descending": true,
                "total": flux,
            }));
        }
    }
    let mut schedules = Vec::new();
    if state.this_irradiation {
        let last = last_irradiation(document)
            .ok_or("The problem's schedule has no irradiation step to compare.")?;
        if let Some(why) = this_irradiation_problem(document) {
            return Err(format!(
                "The problem's irradiation cannot be compared: {why}."
            ));
        }
        schedules.push(json!({"name": THIS, "steps": run_spec["schedule"].as_array().map(|s| s[..=last].to_vec())}));
    }
    for (dt, name) in parse_lengths(&state.lengths)? {
        schedules.push(json!({"name": name, "steps": [{"dt": dt, "flux": 1.0}]}));
    }
    if materials.is_empty() || spectra.is_empty() || schedules.is_empty() {
        return Err("Choose at least one material, one spectrum and one irradiation.".into());
    }
    let cases = materials.len() * spectra.len() * schedules.len();
    if cases < 2 {
        return Err("Choose at least two of something to compare.".into());
    }
    if cases > MAX_CASES {
        return Err(format!(
            "{cases} combinations: the app compares at most {MAX_CASES}. Choose fewer, or run a study file with `actinv study run`."
        ));
    }
    let mut study = json!({
        "study": "actinv-study-1",
        "study_id": "compare",
        "title": format!("Compare: {}", document["title"].as_str().unwrap_or("").trim()),
        "library": run_spec["library"],
        "decay": run_spec["decay"],
        "cases": {"materials": materials, "spectra": spectra, "schedules": schedules},
        "cooling_times_s": cooling_times(document),
        "responses": RESPONSES.map(|(r, _, _)| r),
    });
    if run_spec["options"].is_object() {
        study["options"] = run_spec["options"].clone();
    }
    Ok(study)
}

/// How many cases the current choices make.
fn case_count(state: &CompareState) -> usize {
    let materials = usize::from(state.this_material) + state.materials.len();
    let spectra = usize::from(state.this_spectrum) + state.shapes.len();
    let schedules =
        usize::from(state.this_irradiation) + parse_lengths(&state.lengths).map_or(0, |l| l.len());
    materials * spectra * schedules
}

/// A study name in words; an added irradiation reads its length from the study.
fn label(kind: &str, name: &str, study: &Value) -> String {
    if name == THIS {
        return format!("This problem's {kind}");
    }
    match kind {
        "material" => crate::views::material::library()
            .iter()
            .find(|p| p.id == name)
            .map_or_else(|| name.to_owned(), |p| p.name.clone()),
        "spectrum" => SpectrumShape::ALL
            .into_iter()
            .find(|s| s.id() == name)
            .map_or_else(
                || name.to_owned(),
                |s| crate::views::source::shape_name(s).to_owned(),
            ),
        _ => study["cases"]["schedules"]
            .as_array()
            .into_iter()
            .flatten()
            .find(|s| s["name"] == name)
            .and_then(|s| s["steps"][0]["dt"].as_str())
            .map_or_else(|| name.to_owned(), |dt| format!("{dt} at full flux")),
    }
}

/// When a cooling time is, in words.
fn time_label(t: f64) -> String {
    if t == 0. {
        "at shutdown".into()
    } else {
        format!("{} after shutdown", model::readable_time(t))
    }
}

pub enum CompareAction {
    None,
    Compare,
    Open(Target),
}

/// `solves_here` is false in the browser, which writes the study file for `actinv study run`.
pub fn show(
    ui: &mut egui::Ui,
    document: &Value,
    state: &mut CompareState,
    running: bool,
    solves_here: bool,
) -> CompareAction {
    let mut action = CompareAction::None;
    if last_irradiation(document).is_none()
        && !state.this_irradiation
        && state.lengths.trim().is_empty()
    {
        state.lengths = "1 y".into();
    }
    let column = |ui: &mut egui::Ui, title: &str, note: &str| {
        ui.strong(title);
        crate::visuals::unjustified(ui, egui::RichText::new(note).weak());
        ui.add_space(4.);
    };
    let this_box =
        |ui: &mut egui::Ui, on: &mut bool, text: &str, problem: Option<&str>| match problem {
            None => {
                ui.checkbox(on, text);
            }
            Some(why) => {
                *on = false;
                ui.add_enabled(false, egui::Checkbox::new(&mut false, text))
                    .on_disabled_hover_text(why);
                ui.weak(format!("Unavailable: {why}."));
            }
        };
    ui.columns(3, |columns| {
        let ui = &mut columns[0];
        column(
            ui,
            "Materials",
            "Compositions from the library, in weight percent.",
        );
        this_box(
            ui,
            &mut state.this_material,
            "This problem's material",
            this_material_problem(document),
        );
        let mut drop = None;
        for id in &state.materials {
            ui.horizontal(|ui| {
                ui.label(label("material", id, &Value::Null));
                if ui.small_button("Remove").clicked() {
                    drop = Some(id.clone());
                }
            });
        }
        if let Some(id) = drop {
            state.materials.remove(&id);
        }
        let mut add = None;
        egui::ComboBox::from_id_salt("compare-add-material")
            .selected_text("Add a library material")
            .width(ui.available_width().min(320.))
            .show_ui(ui, |ui| {
                let mut category = "";
                for preset in crate::views::material::library() {
                    if state.materials.contains(&preset.id) {
                        continue;
                    }
                    if preset.category != category {
                        category = &preset.category;
                        ui.label(
                            RichText::new(category.to_uppercase())
                                .small()
                                .color(crate::visuals::accent(ui)),
                        );
                    }
                    if ui.selectable_label(false, &preset.name).clicked() {
                        add = Some(preset.id.clone());
                    }
                }
            });
        if let Some(id) = add {
            state.materials.insert(id);
        }
        let ui = &mut columns[1];
        column(
            ui,
            "Spectra",
            "Standard shapes take this problem's total flux.",
        );
        this_box(
            ui,
            &mut state.this_spectrum,
            "This problem's spectrum",
            this_spectrum_problem(document),
        );
        for shape in SpectrumShape::ALL {
            let mut on = state.shapes.contains(&shape);
            if ui
                .checkbox(&mut on, crate::views::source::shape_name(shape))
                .on_hover_text(shape.description())
                .changed()
            {
                if on {
                    state.shapes.push(shape);
                } else {
                    state.shapes.retain(|s| *s != shape);
                }
            }
        }
        let ui = &mut columns[2];
        column(
            ui,
            "Irradiation",
            "Each is followed by this problem's cooling.",
        );
        let irradiation_problem = if last_irradiation(document).is_none() {
            Some("the schedule has no irradiation step")
        } else {
            this_irradiation_problem(document)
        };
        this_box(
            ui,
            &mut state.this_irradiation,
            "This problem's irradiation",
            irradiation_problem,
        );
        ui.label("Also irradiate at full flux for");
        ui.add(
            egui::TextEdit::singleline(&mut state.lengths)
                .hint_text("30 d, 5 y")
                .desired_width(f32::INFINITY),
        );
        if let Err(error) = parse_lengths(&state.lengths) {
            ui.colored_label(ui.visuals().error_fg_color, error);
        }
    });
    ui.add_space(10.);
    let times = cooling_times(document);
    ui.weak(format!(
        "Each case is reported {}.",
        times
            .iter()
            .map(|t| time_label(*t))
            .collect::<Vec<_>>()
            .join(", ")
    ));
    let cases = case_count(state);
    ui.label(if cases == 1 {
        format!(
            "1 case, one solve. Tick more above to compare; the app compares up to {MAX_CASES}."
        )
    } else {
        format!("{cases} combinations, one solve each. The app compares up to {MAX_CASES}.")
    });
    if cases > MAX_CASES {
        ui.colored_label(
            ui.visuals().error_fg_color,
            format!("The app compares at most {MAX_CASES}; `actinv study run` takes up to 1024 from a file."),
        );
    }
    ui.add_space(6.);
    let (text, hover) = if solves_here {
        ("Compare", "Solves every combination in the isolated worker; the status bar counts them and can cancel")
    } else {
        (
            "Download the study file",
            "The browser does not solve. Run actinv study run compare.json out beside the nuclear data, or use the desktop app.",
        )
    };
    if ui
        .add_enabled(
            !running && (2..=MAX_CASES).contains(&cases),
            egui::Button::new(RichText::new(text).strong()),
        )
        .on_hover_text(hover)
        .clicked()
    {
        action = CompareAction::Compare;
    }
    if !solves_here {
        ui.weak("The browser sets up the comparison; the desktop app or `actinv study run compare.json out` solves it.");
    }
    if this_material_problem(document).is_some() && this_spectrum_problem(document).is_some() {
        ui.horizontal(|ui| {
            if ui.button("Open Material").clicked() {
                action = CompareAction::Open(Target::Material);
            }
            if ui.button("Open Source & spectrum").clicked() {
                action = CompareAction::Open(Target::Spectrum);
            }
        });
    }
    if let Some(found) = &state.found {
        ui.add_space(16.);
        ranking(ui, found, &mut state.response, &mut state.time);
    }
    action
}

/// One case of a record: its three names, its value, and whether it ran.
struct Case {
    names: [String; 3],
    value: Option<f64>,
    failed: bool,
}

/// The ranking: one chosen response at one chosen time, lowest first, with a bar for scale.
fn ranking(ui: &mut egui::Ui, found: &Found, response: &mut usize, time: &mut usize) {
    let times: Vec<f64> = found.study["cooling_times_s"]
        .as_array()
        .map(|t| t.iter().map(model::number).collect())
        .unwrap_or_default();
    if times.is_empty() {
        return;
    }
    *response = (*response).min(RESPONSES.len() - 1);
    *time = (*time).min(times.len() - 1);
    ui.heading("Ranking");
    ui.horizontal_wrapped(|ui| {
        egui::ComboBox::from_id_salt("compare-response")
            .selected_text(RESPONSES[*response].1)
            .show_ui(ui, |ui| {
                for (i, (_, name, _)) in RESPONSES.iter().enumerate() {
                    ui.selectable_value(response, i, *name);
                }
            });
        egui::ComboBox::from_id_salt("compare-time")
            .selected_text(time_label(times[*time]))
            .show_ui(ui, |ui| {
                for (i, t) in times.iter().enumerate() {
                    ui.selectable_value(time, i, time_label(*t));
                }
            });
        ui.weak("lowest first, per gram of material");
    });
    let (key, _, unit) = RESPONSES[*response];
    let t_key = format!("{}", times[*time]);
    let mut cases: Vec<Case> = found.record["cases"]
        .as_array()
        .into_iter()
        .flatten()
        .map(|c| {
            let id = c["case_id"].as_str().unwrap_or("");
            let mut parts = id.splitn(3, "__").map(str::to_owned);
            let names = [
                parts.next().unwrap_or_default(),
                parts.next().unwrap_or_default(),
                parts.next().unwrap_or_default(),
            ];
            let per_time = &c["per_time"];
            // the record keys times as written in the study; match them by value
            let value = per_time.as_object().and_then(|m| {
                m.iter()
                    .find(|(k, _)| {
                        *k == &t_key
                            || k.parse::<f64>()
                                .is_ok_and(|t| (t - times[*time]).abs() <= 1e-9 * t.max(1.))
                    })
                    .and_then(|(_, v)| v[key].as_f64())
            });
            Case {
                names,
                value,
                failed: c["status"] != "executed" && c["status"] != "resumed",
            }
        })
        .collect();
    cases.sort_by(|a, b| match (a.value, b.value) {
        (Some(x), Some(y)) => x.total_cmp(&y),
        (Some(_), None) => std::cmp::Ordering::Less,
        (None, Some(_)) => std::cmp::Ordering::Greater,
        _ => std::cmp::Ordering::Equal,
    });
    let kinds = ["material", "spectrum", "irradiation"];
    // only the columns that differ between cases
    let shown: Vec<usize> = (0..3)
        .filter(|&k| {
            let distinct: BTreeSet<&str> = cases.iter().map(|c| c.names[k].as_str()).collect();
            distinct.len() > 1
        })
        .collect();
    let lowest = cases.iter().filter_map(|c| c.value).find(|v| *v > 0.);
    let highest = cases.iter().filter_map(|c| c.value).fold(0f64, f64::max);
    let failed = cases.iter().filter(|c| c.failed).count();
    if failed > 0 {
        ui.colored_label(
            ui.visuals().warn_fg_color,
            format!(
                "{failed} of {} cases did not finish; they are listed last.",
                cases.len()
            ),
        );
    }
    let mut table = TableBuilder::new(ui)
        .id_salt("compare-ranking")
        .striped(true)
        .cell_layout(egui::Layout::left_to_right(egui::Align::Center))
        .column(Column::auto().at_least(30.));
    for _ in &shown {
        table = table.column(Column::auto().at_least(150.));
    }
    table
        .column(Column::auto().at_least(110.))
        .column(Column::auto().at_least(80.))
        .column(Column::remainder().at_least(120.))
        .header(26., |mut h| {
            h.col(|ui| {
                ui.strong("#");
            });
            for &k in &shown {
                h.col(|ui| {
                    let mut title = kinds[k].to_owned();
                    title[..1].make_ascii_uppercase();
                    ui.strong(title);
                });
            }
            h.col(|ui| {
                ui.strong(unit);
            });
            h.col(|ui| {
                ui.strong("Times the lowest");
            });
            h.col(|_| {});
        })
        .body(|mut body| {
            for (rank, case) in cases.iter().enumerate() {
                body.row(26., |mut row| {
                    row.col(|ui| {
                        ui.label(format!("{}", rank + 1));
                    });
                    for &k in &shown {
                        row.col(|ui| {
                            ui.label(label(kinds[k], &case.names[k], &found.study));
                        });
                    }
                    row.col(|ui| match case.value {
                        Some(v) => {
                            ui.label(crate::views::certify::three(v));
                        }
                        None if case.failed => {
                            ui.colored_label(ui.visuals().error_fg_color, "did not finish");
                        }
                        None => {
                            ui.weak("not recorded");
                        }
                    });
                    row.col(|ui| {
                        if let (Some(v), Some(low)) = (case.value, lowest) {
                            ui.label(crate::views::certify::three(v / low));
                        }
                    });
                    row.col(|ui| {
                        if let Some(v) = case.value.filter(|_| highest > 0.) {
                            let width = ui.available_width().max(40.);
                            let (rect, _) = ui
                                .allocate_exact_size(egui::vec2(width, 12.), egui::Sense::hover());
                            let fill = egui::Rect::from_min_size(
                                rect.min,
                                egui::vec2((v / highest) as f32 * rect.width(), rect.height()),
                            );
                            ui.painter()
                                .rect_filled(fill, 2., crate::visuals::accent(ui));
                        }
                    });
                });
            }
        });
    if shown.len() < 3 {
        let fixed: Vec<String> = (0..3)
            .filter(|k| !shown.contains(k))
            .filter_map(|k| {
                cases
                    .first()
                    .map(|c| label(kinds[k], &c.names[k], &found.study))
            })
            .collect();
        if !fixed.is_empty() {
            ui.weak(format!("Common to every case: {}.", fixed.join("; ")));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn problem() -> Value {
        let mut doc = model::decode_problem(model::EXAMPLE).unwrap();
        doc["schedule"] = json!([
            {"dt": "1 y", "flux": 1.0},
            {"dt": "1 d", "flux": 0.0},
            {"dt": "29 d", "flux": 0.0}
        ]);
        doc
    }

    fn state() -> CompareState {
        CompareState {
            materials: ["eurofer97".to_owned(), "ss316".to_owned()].into(),
            shapes: vec![SpectrumShape::Fission],
            lengths: "30 d, 5 y".into(),
            ..CompareState::default()
        }
    }

    #[test]
    fn the_comparison_is_a_valid_study() {
        let doc = problem();
        let study = study_spec(&doc, &doc, &state()).unwrap();
        let parsed = actinv_core::study::Study::from_json(&study.to_string()).unwrap();
        parsed.validate().unwrap();
        assert_eq!(parsed.case_ids().len(), 3 * 2 * 3);
        assert_eq!(case_count(&state()), 18);
        assert_eq!(
            study["cooling_times_s"],
            json!([0.0, 86400.0, 30.0 * 86400.0])
        );
        assert_eq!(
            study["cases"]["schedules"][0]["steps"],
            json!([{"dt": "1 y", "flux": 1.0}])
        );
        assert_eq!(study["cases"]["schedules"][1]["name"], "irradiate-30d");
        let fission = &study["cases"]["spectra"][1];
        let sum: f64 = fission["flux_per_group"]
            .as_array()
            .unwrap()
            .iter()
            .map(model::number)
            .sum();
        assert!((sum / problem_flux(&doc) - 1.).abs() < 1e-12);
    }

    #[test]
    fn what_cannot_be_compared_is_refused_in_words() {
        let doc = problem();
        let mut lonely = CompareState::default();
        assert!(study_spec(&doc, &doc, &lonely).unwrap_err().contains("two"));
        lonely.lengths = "soon".into();
        assert!(study_spec(&doc, &doc, &lonely)
            .unwrap_err()
            .contains("not a duration"));
        let mut atoms = problem();
        atoms["material"]["basis"] = "atom_fraction".into();
        assert!(study_spec(&atoms, &atoms, &state())
            .unwrap_err()
            .contains("weight percent"));
        let mut cooled = problem();
        cooled["schedule"] = json!([{"dt": "1 d", "flux": 0.0}]);
        assert!(study_spec(&cooled, &cooled, &state())
            .unwrap_err()
            .contains("no irradiation"));
        let mut many = state();
        many.materials = crate::views::material::library()
            .iter()
            .map(|p| p.id.clone())
            .collect();
        many.shapes = SpectrumShape::ALL.to_vec();
        many.lengths = "1 d, 2 d, 3 d, 4 d, 5 d".into();
        assert!(study_spec(&doc, &doc, &many)
            .unwrap_err()
            .contains("at most 200"));
    }

    #[test]
    fn a_study_reads_back_into_the_choices() {
        let doc = problem();
        let study = study_spec(&doc, &doc, &state()).unwrap();
        let mut shown = CompareState::default();
        shown.show_study(&study);
        assert_eq!(study_spec(&doc, &doc, &shown).unwrap(), study);
        assert_eq!(
            label("material", "ss316", &study),
            "Stainless steel 316 (PNNL generic)"
        );
        assert_eq!(
            label("irradiation", "irradiate-30d", &study),
            "30 d at full flux"
        );
        assert_eq!(label("spectrum", THIS, &study), "This problem's spectrum");
    }
}
