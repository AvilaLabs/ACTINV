//! Maintenance: when may each job inside a shut-down machine start, what dose does each crew
//! receive, and which wait after shutdown is allowed and costs least. The page plans in process
//! with `actinv_core::maintenance`, the same arithmetic as `actinv maintenance`, from the open
//! result's point-source dose and from run results or dose-rate tables the user adds.
use crate::{model::ResultDocument, views::Target, visuals};
use actinv_core::maintenance::{
    self, Cost, DoseCurve, DoseMap, Job, Outcome, Plan, Wait, DOSE_MAP_SCHEMA,
};
use eframe::egui::{self, RichText};
use egui_extras::{Column, TableBuilder};
use egui_plot::{HLine, Line, Plot, VLine};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

/// The page's question, under its heading.
pub const QUESTION: &str = "When may each job start after shutdown, what dose does each crew receive, and which wait is allowed and costs least? Plans from the dose rate at each job's place over cooling time.";

const DAY_S: f64 = 86_400.0;

/// A place a job is done at, with its dose rate over cooling time.
pub struct Place {
    /// What jobs name; editable.
    pub name: String,
    /// The file it came from.
    pub file: String,
    pub sha256: String,
    /// The point-source distance of a run result; `None` for a table.
    pub distance_m: Option<f64>,
    /// The table's own description of where its dose rates come from.
    pub source: Option<String>,
    curve: DoseCurve,
}

/// A job as typed: numbers stay text until the plan is solved, so a field may be left empty.
pub struct JobRow {
    pub name: String,
    pub place: String,
    pub hours: String,
    pub crew: String,
}

pub struct MaintenanceState {
    /// Plan at the open result's point-source dose too, when it has one.
    pub use_open: bool,
    /// The open result's place name.
    pub open_name: String,
    pub places: Vec<Place>,
    pub access_limit: String,
    pub individual_limit: String,
    pub jobs: Vec<JobRow>,
    pub waits_d: String,
    pub costs: bool,
    pub per_person_sv: String,
    pub per_outage_day: String,
    /// The open result's place, kept for the result it was read from.
    open: Option<(u64, Result<Place, String>)>,
    /// The last plan solved, with the places it was solved at (`{"plan", "places"}`), and its
    /// answer.
    solved: Option<(Value, Result<Outcome, String>)>,
}

impl Default for MaintenanceState {
    fn default() -> Self {
        Self {
            use_open: true,
            open_name: "open result".into(),
            places: Vec::new(),
            access_limit: String::new(),
            individual_limit: String::new(),
            jobs: Vec::new(),
            waits_d: "1, 7, 30, 90, 180".into(),
            costs: false,
            per_person_sv: String::new(),
            per_outage_day: String::new(),
            open: None,
            solved: None,
        }
    }
}

#[derive(Debug, PartialEq)]
pub enum MaintenanceAction {
    None,
    /// Choose a run result or a dose-rate table and pass it to [`MaintenanceState::add_file`].
    AddFile,
    /// Save [`result_document`].
    SaveResult,
    /// Open a result for the open-result place.
    OpenResult,
    GoTo(Target),
}

fn sha256_hex(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

/// A file's name without its folder or `.json`.
fn stem(file: &str) -> String {
    let name = file.rsplit(['/', '\\']).next().unwrap_or(file);
    name.strip_suffix(".json").unwrap_or(name).to_owned()
}

/// A run result's place from its point-source dose.
fn run_place(value: &Value, name: &str, file: &str, sha256: String) -> Result<Place, String> {
    let (curve, _) = maintenance::run_curve(value, file, name)?;
    let distance_m = value["steps"]
        .as_array()
        .into_iter()
        .flatten()
        .find_map(|step| step["photon_source"]["point_source_dose"]["distance_m"].as_f64());
    Ok(Place {
        name: name.to_owned(),
        file: file.to_owned(),
        sha256,
        distance_m,
        source: None,
        curve,
    })
}

impl MaintenanceState {
    /// Add the places of a run result (one) or of an `actinv-dose-map-1` table (one per location).
    pub fn add_file(&mut self, file: &str, bytes: &[u8]) -> Result<String, String> {
        let value: Value = serde_json::from_slice(bytes).map_err(|e| format!("{file}: {e}"))?;
        let sha256 = sha256_hex(bytes);
        let mut added = Vec::new();
        if value["schema"] == DOSE_MAP_SCHEMA {
            let map: DoseMap = serde_json::from_value(value).map_err(|e| format!("{file}: {e}"))?;
            let source = map.source.clone();
            for curve in map.curves(file)? {
                added.push(Place {
                    name: curve.name.clone(),
                    file: file.to_owned(),
                    sha256: sha256.clone(),
                    distance_m: None,
                    source: source.clone(),
                    curve,
                });
            }
        } else {
            added.push(run_place(&value, &stem(file), file, sha256)?);
        }
        let count = added.len();
        for mut place in added {
            place.name = self.free_name(&place.name);
            self.places.push(place);
        }
        Ok(if count == 1 {
            format!("Added {file} as a place.")
        } else {
            format!("Added {count} places from {file}.")
        })
    }

    /// Fill the page from an `actinv-maintenance-1` plan whose dose rates are run results or a
    /// table, read beside it. Screenshot mode uses it; a twin source is refused.
    #[cfg(not(target_arch = "wasm32"))]
    pub fn load_plan(&mut self, path: &std::path::Path) -> Result<String, String> {
        let read = |p: &std::path::Path| {
            std::fs::read(p).map_err(|e| format!("cannot read {}: {e}", p.display()))
        };
        let mut value: Value =
            serde_json::from_slice(&read(path)?).map_err(|e| format!("{}: {e}", path.display()))?;
        let object = value
            .as_object_mut()
            .ok_or_else(|| format!("{} is not a JSON object", path.display()))?;
        if object.remove("schema") != Some(json!(maintenance::PLAN_SCHEMA)) {
            return Err(format!(
                "{} is not an {} plan",
                path.display(),
                maintenance::PLAN_SCHEMA
            ));
        }
        let source = object.remove("dose_rates").unwrap_or_default();
        let plan: Plan =
            serde_json::from_value(value).map_err(|e| format!("{}: {e}", path.display()))?;
        let base = path.parent().unwrap_or(std::path::Path::new("."));
        let mut fresh = MaintenanceState {
            use_open: false,
            ..Default::default()
        };
        if let Some(runs) = source["runs"].as_array() {
            for run in runs {
                let (Some(file), Some(location)) =
                    (run["result"].as_str(), run["location"].as_str())
                else {
                    return Err("each run names its result and location".into());
                };
                let bytes = read(&base.join(file))?;
                let result: Value =
                    serde_json::from_slice(&bytes).map_err(|e| format!("{file}: {e}"))?;
                fresh
                    .places
                    .push(run_place(&result, location, file, sha256_hex(&bytes))?);
            }
        } else if let Some(table) = source["table"].as_str() {
            fresh.add_file(table, &read(&base.join(table))?)?;
        } else {
            return Err("This page plans from run results or a dose-rate table; solve a twin plan with `actinv maintenance`.".into());
        }
        let text = |x: f64| visuals::compact_number(x);
        fresh.access_limit = text(plan.access_limit_Sv_h * 1e3);
        fresh.individual_limit = plan
            .individual_limit_Sv
            .map(|x| text(x * 1e3))
            .unwrap_or_default();
        fresh.jobs = plan
            .jobs
            .iter()
            .map(|job| JobRow {
                name: job.name.clone(),
                place: job.location.clone(),
                hours: text(job.duration_h),
                crew: job.crew.to_string(),
            })
            .collect();
        fresh.waits_d = plan
            .waits_d
            .iter()
            .map(|w| text(*w))
            .collect::<Vec<_>>()
            .join(", ");
        if let Some(cost) = &plan.cost {
            fresh.costs = true;
            fresh.per_person_sv = text(cost.per_person_Sv);
            fresh.per_outage_day = text(cost.per_outage_day);
        }
        *self = fresh;
        Ok(format!("Opened the plan {}.", path.display()))
    }

    /// `name`, or `name 2`, `name 3`… when a place already has it.
    fn free_name(&self, name: &str) -> String {
        let taken = |n: &str| {
            (self.use_open && n == self.open_name) || self.places.iter().any(|p| p.name == n)
        };
        if !taken(name) {
            return name.to_owned();
        }
        (2..)
            .map(|i| format!("{name} {i}"))
            .find(|n| !taken(n))
            .unwrap_or_default()
    }

    /// Read the open result's place again when the result changes.
    fn refresh_open(&mut self, open: Option<&ResultDocument>) {
        match open {
            None => self.open = None,
            Some(doc) if self.open.as_ref().map(|o| o.0) != Some(doc.id) => {
                let bytes = serde_json::to_vec(&doc.value).unwrap_or_default();
                let place = run_place(&doc.value, &self.open_name, &doc.label, sha256_hex(&bytes));
                self.open = Some((doc.id, place));
            }
            Some(_) => {}
        }
    }

    /// Every place a job may name: the open result's first, when it is used.
    fn all_places(&self) -> Vec<&Place> {
        let open = self
            .open
            .as_ref()
            .filter(|_| self.use_open)
            .and_then(|o| o.1.as_ref().ok());
        open.into_iter().chain(self.places.iter()).collect()
    }

    fn rename(&mut self, from: &str, to: &str) {
        for job in &mut self.jobs {
            if job.place == from {
                job.place = to.to_owned();
            }
        }
    }
}

fn number(text: &str, what: &str) -> Result<f64, String> {
    text.trim()
        .parse::<f64>()
        .ok()
        .filter(|x| x.is_finite())
        .ok_or_else(|| format!("{what} must be a number"))
}

fn optional(text: &str, what: &str) -> Result<Option<f64>, String> {
    if text.trim().is_empty() {
        Ok(None)
    } else {
        number(text, what).map(Some)
    }
}

/// The plan the page's fields describe, in the units of `actinv-maintenance-1`.
pub fn plan(state: &MaintenanceState) -> Result<Plan, String> {
    let access = number(&state.access_limit, "The access limit")?;
    let individual = optional(&state.individual_limit, "The individual limit")?;
    let mut jobs = Vec::with_capacity(state.jobs.len());
    for (i, row) in state.jobs.iter().enumerate() {
        let name = if row.name.trim().is_empty() {
            format!("job {}", i + 1)
        } else {
            row.name.trim().to_owned()
        };
        let crew = row
            .crew
            .trim()
            .parse::<u32>()
            .map_err(|_| format!("{name}: the crew must be a whole number"))?;
        jobs.push(Job {
            location: row.place.clone(),
            duration_h: number(&row.hours, &format!("{name}: the hours"))?,
            crew,
            name,
        });
    }
    let waits_d = state
        .waits_d
        .split([',', ' ', ';'])
        .filter(|w| !w.trim().is_empty())
        .map(|w| number(w, "Each wait"))
        .collect::<Result<Vec<_>, _>>()?;
    let cost = if state.costs {
        Some(Cost {
            per_person_Sv: number(&state.per_person_sv, "The cost per person-sievert")?,
            per_outage_day: number(&state.per_outage_day, "The cost per outage day")?,
        })
    } else {
        None
    };
    Ok(Plan {
        access_limit_Sv_h: access / 1e3,
        individual_limit_Sv: individual.map(|x| x / 1e3),
        jobs,
        waits_d,
        cost,
        interpolation_tolerance: None,
    })
}

/// The saved answer: the outcome, the plan it solved and the files its dose rates came from.
pub fn result_document(state: &MaintenanceState) -> Option<Value> {
    let (key, Ok(outcome)) = state.solved.as_ref()? else {
        return None;
    };
    let mut result = serde_json::to_value(outcome).ok()?;
    let mut plan = key["plan"].clone();
    plan["schema"] = json!(maintenance::PLAN_SCHEMA);
    result["plan"] = plan;
    result["dose_rates"] = json!({"places": state.all_places().iter().map(|p| json!({
        "name": p.name, "file": p.file, "sha256": p.sha256,
        "distance_m": p.distance_m, "source": p.source,
    })).collect::<Vec<_>>()});
    result["note"] = json!("The planner does arithmetic on the dose rates it is given; their accuracy is that of their source. It does not say a plan is safe.");
    Some(result)
}

/// A time after shutdown in days, or in hours when under a day.
fn when(d: f64) -> String {
    if d <= 0.0 {
        "at shutdown".into()
    } else if d < 1.0 {
        format!("{:.1} h", d * 24.0)
    } else if d < 100.0 {
        format!("{d:.1} d")
    } else {
        format!("{d:.0} d")
    }
}

/// Three significant figures.
fn sig3(x: f64) -> String {
    if x == 0.0 || !x.is_finite() {
        return visuals::compact_number(x);
    }
    let scale = 10f64.powi(2 - x.abs().log10().floor() as i32);
    visuals::compact_number((x * scale).round() / scale)
}

fn msv(sv: f64) -> String {
    sig3(sv * 1e3)
}

fn money(x: f64) -> String {
    sig3(x)
}

/// Why a wait is not allowed, or empty when it is.
fn why_not(wait: &Wait, plan: &Plan) -> String {
    let closed: Vec<&str> = wait
        .jobs
        .iter()
        .filter(|job| !job.accessible)
        .map(|job| job.name.as_str())
        .collect();
    let mut why = Vec::new();
    if !closed.is_empty() {
        why.push(format!("over the access limit: {}", closed.join(", ")));
    }
    if let Some(limit) = plan.individual_limit_Sv {
        if wait.largest_individual_Sv > limit {
            why.push("a worker over the individual limit".into());
        }
    }
    why.join("; ")
}

pub fn show(
    ui: &mut egui::Ui,
    state: &mut MaintenanceState,
    open: Option<&ResultDocument>,
) -> MaintenanceAction {
    let mut action = MaintenanceAction::None;
    state.refresh_open(open);
    let accent = visuals::accent(ui);

    ui.heading("Places");
    ui.label("Each place is a dose rate over cooling time: the point-source dose of a run result, or a dose-rate table (actinv-dose-map-1, Sv/h) from photon transport.");
    match &state.open {
        None => {
            ui.horizontal(|ui| {
                ui.label("No result is open.");
                if ui.small_button("Open a result…").clicked() {
                    action = MaintenanceAction::OpenResult;
                }
            });
        }
        Some((_, Err(why))) => {
            ui.label(format!(
                "The open result has no dose rate to plan with: {why}."
            ));
            if ui
                .small_button("Turn on point-source dose in What to compute")
                .clicked()
            {
                action = MaintenanceAction::GoTo(Target::Outputs);
            }
        }
        Some((_, Ok(_))) => {}
    }
    let mut rename = None;
    let mut remove = None;
    egui::Grid::new("maintenance-places")
        .striped(true)
        .num_columns(4)
        .show(ui, |ui| {
            if let Some((_, Ok(place))) = &state.open {
                ui.checkbox(&mut state.use_open, "")
                    .on_hover_text("Plan at the open result too");
                let before = state.open_name.clone();
                ui.add_sized(
                    [150., 22.],
                    egui::TextEdit::singleline(&mut state.open_name),
                );
                if state.open_name != before {
                    rename = Some((before, state.open_name.clone()));
                }
                ui.label(describe(place, "the open result"));
                ui.label("");
                ui.end_row();
            }
            for (i, place) in state.places.iter_mut().enumerate() {
                ui.label("");
                let before = place.name.clone();
                ui.add_sized([150., 22.], egui::TextEdit::singleline(&mut place.name));
                if place.name != before {
                    rename = Some((before, place.name.clone()));
                }
                ui.label(describe(place, &place.file));
                if ui.small_button("Remove").clicked() {
                    remove = Some(i);
                }
                ui.end_row();
            }
        });
    if let Some((from, to)) = rename {
        if let Some(Ok(place)) = state.open.as_mut().map(|o| &mut o.1) {
            if place.name == from {
                place.name = to.clone();
            }
        }
        state.rename(&from, &to);
    }
    if let Some(i) = remove {
        state.places.remove(i);
    }
    if ui.button("Add dose rates…").on_hover_text("A run result whose problem asks for a point-source dose, or an actinv-dose-map-1 table").clicked() {
        action = MaintenanceAction::AddFile;
    }
    let names: Vec<String> = state.all_places().iter().map(|p| p.name.clone()).collect();

    ui.add_space(8.);
    ui.heading("Limits");
    egui::Grid::new("maintenance-limits")
        .num_columns(2)
        .show(ui, |ui| {
            ui.label("Access limit (mSv/h)");
            ui.add(
                egui::TextEdit::singleline(&mut state.access_limit)
                    .hint_text("required")
                    .desired_width(110.),
            );
            ui.end_row();
            ui.label("Individual limit (mSv)");
            ui.add(
                egui::TextEdit::singleline(&mut state.individual_limit)
                    .hint_text("optional")
                    .desired_width(110.),
            );
            ui.end_row();
        });
    ui.label(RichText::new("A job may run only while the dose rate at its place stays at or under the access limit; every worker's dose over the plan stays at or under the individual limit.").weak());

    ui.add_space(8.);
    ui.heading("Jobs, in order");
    let mut drop = None;
    let mut raise = None;
    if !state.jobs.is_empty() {
        egui::Grid::new("maintenance-jobs")
            .striped(true)
            .num_columns(6)
            .show(ui, |ui| {
                for label in ["Job", "Place", "Hours", "Crew", "", ""] {
                    ui.strong(label);
                }
                ui.end_row();
                for (i, job) in state.jobs.iter_mut().enumerate() {
                    ui.add_sized(
                        [200., 22.],
                        egui::TextEdit::singleline(&mut job.name)
                            .hint_text(format!("job {}", i + 1)),
                    );
                    egui::ComboBox::from_id_salt(("maintenance-place", i))
                        .selected_text(if job.place.is_empty() {
                            "choose"
                        } else {
                            job.place.as_str()
                        })
                        .show_ui(ui, |ui| {
                            for name in &names {
                                ui.selectable_value(&mut job.place, name.clone(), name);
                            }
                        });
                    ui.add(egui::TextEdit::singleline(&mut job.hours).desired_width(60.));
                    ui.add(egui::TextEdit::singleline(&mut job.crew).desired_width(40.));
                    if i > 0 && ui.small_button("Up").clicked() {
                        raise = Some(i);
                    } else if i == 0 {
                        ui.label("");
                    }
                    if ui.small_button("Remove").clicked() {
                        drop = Some(i);
                    }
                    ui.end_row();
                }
            });
    }
    if let Some(i) = raise {
        state.jobs.swap(i - 1, i);
    }
    if let Some(i) = drop {
        state.jobs.remove(i);
    }
    if ui.button("Add job").clicked() {
        state.jobs.push(JobRow {
            name: String::new(),
            place: names.first().cloned().unwrap_or_default(),
            hours: String::new(),
            crew: "1".into(),
        });
    }
    ui.label(RichText::new("Jobs run one after another from the wait, and the whole crew is present for the whole job.").weak());

    ui.add_space(8.);
    ui.heading("Waits and costs");
    ui.horizontal(|ui| {
        ui.label("Waits to show (days after shutdown)");
        ui.add(egui::TextEdit::singleline(&mut state.waits_d).desired_width(200.));
    });
    ui.checkbox(&mut state.costs, "Find the wait of least cost");
    if state.costs {
        ui.horizontal(|ui| {
            ui.label("Cost per person-Sv");
            ui.add(egui::TextEdit::singleline(&mut state.per_person_sv).desired_width(100.));
            ui.label("per outage day");
            ui.add(egui::TextEdit::singleline(&mut state.per_outage_day).desired_width(100.));
        });
    }

    ui.add_space(12.);
    ui.separator();
    ui.heading("Plan");
    let (curves, places_key): (Vec<DoseCurve>, Vec<Value>) = state
        .all_places()
        .iter()
        .map(|p| (p.curve.clone(), json!([p.name, p.sha256])))
        .unzip();
    if curves.is_empty() {
        ui.label(
            "Add a place to plan at: open a result with a point-source dose, or add dose rates.",
        );
        return action;
    }
    if state.jobs.is_empty() {
        ui.label("Add the jobs, in the order they are done.");
        draw_curves(ui, &state.all_places(), None, None, accent);
        return action;
    }
    let plan = match plan(state) {
        Ok(plan) => plan,
        Err(why) => {
            ui.colored_label(ui.visuals().warn_fg_color, why);
            draw_curves(ui, &state.all_places(), None, None, accent);
            return action;
        }
    };
    let key = json!({"plan": plan, "places": places_key});
    if state.solved.as_ref().is_none_or(|(k, _)| *k != key) {
        state.solved = Some((key, maintenance::evaluate(&plan, &curves)));
    }
    let places = state.all_places();
    let outcome = match state.solved.as_ref().map(|s| &s.1) {
        Some(Ok(outcome)) => outcome,
        Some(Err(why)) => {
            ui.colored_label(ui.visuals().error_fg_color, why);
            draw_curves(ui, &places, None, None, accent);
            return action;
        }
        None => return action,
    };

    match outcome.earliest_wait_d {
        Some(d) => ui.label(
            RichText::new(format!(
                "Every job is allowed from {} after shutdown.",
                when(d)
            ))
            .strong()
            .color(accent),
        ),
        None => ui.label(
            RichText::new("No wait within the tabulated cooling times allows every job.")
                .strong()
                .color(ui.visuals().warn_fg_color),
        ),
    };
    if let Some(best) = &outcome.best {
        let earliest = outcome
            .earliest_wait_d
            .is_some_and(|d| (d - best.wait_d).abs() <= 1e-6 * d.max(1.0));
        ui.label(format!(
            "Least cost at {}{}: {} in all, {} person-mSv collective, {} mSv to the most-exposed worker.",
            when(best.wait_d),
            if earliest { " (the earliest allowed wait)" } else { "" },
            best.cost.map(money).unwrap_or_default(),
            msv(best.collective_Sv),
            msv(best.largest_individual_Sv),
        ));
        ui.label(RichText::new("Near a flat minimum the least-cost wait can move a long way for a small change in cost; compare the costs of the waits below.").weak());
    }
    let check = &outcome.interpolation_check;
    if check.coarse {
        ui.colored_label(
            ui.visuals().warn_fg_color,
            format!(
                "The cooling times may be too sparse for this plan: without every other one, {} moves by {:.0}%. Add cooling times around the waits and job lengths that matter, run again and add the new result.",
                check.at.as_deref().unwrap_or("an answer"),
                check.largest_relative_change.unwrap_or(0.0) * 100.0
            ),
        );
    }

    ui.add_space(6.);
    draw_curves(
        ui,
        &places,
        Some(plan.access_limit_Sv_h),
        outcome.earliest_wait_d,
        accent,
    );

    ui.add_space(6.);
    ui.strong("Each job on its own");
    let rows: Vec<Vec<String>> = outcome
        .alone
        .iter()
        .map(|job| {
            vec![
                job.name.clone(),
                job.location.clone(),
                job.earliest_start_d
                    .map(when)
                    .unwrap_or_else(|| "never, within the tabulated times".into()),
            ]
        })
        .collect();
    table(
        ui,
        "maintenance-alone",
        &["Job", "Place", "Earliest start"],
        &rows,
    );

    ui.add_space(6.);
    ui.strong("At each wait");
    let best = outcome.best.iter().map(|wait| (wait, true));
    let rows: Vec<Vec<String>> = outcome
        .waits
        .iter()
        .map(|wait| (wait, false))
        .chain(best)
        .map(|(wait, least)| {
            let mut row = vec![
                if least {
                    format!("{} (least cost)", when(wait.wait_d))
                } else {
                    when(wait.wait_d)
                },
                if wait.feasible {
                    "yes".into()
                } else {
                    "no".into()
                },
                msv(wait.collective_Sv),
                msv(wait.largest_individual_Sv),
                when(wait.outage_d),
            ];
            if plan.cost.is_some() {
                row.push(wait.cost.map(money).unwrap_or_default());
            }
            row.push(why_not(wait, &plan));
            row
        })
        .collect();
    let mut headers = vec![
        "Wait",
        "Allowed",
        "Collective (person-mSv)",
        "Most-exposed worker (mSv)",
        "Outage ends",
    ];
    if plan.cost.is_some() {
        headers.push("Cost");
    }
    headers.push("Why not");
    table(ui, "maintenance-waits", &headers, &rows);

    ui.add_space(6.);
    ui.horizontal(|ui| {
        if ui.button("Save result…").clicked() {
            action = MaintenanceAction::SaveResult;
        }
        ui.label(RichText::new("The same answer as `actinv maintenance` on this plan. The planner does arithmetic on the dose rates it is given; it does not say a plan is safe.").weak());
    });
    action
}

fn describe(place: &Place, file: &str) -> String {
    let last = when(place.curve.last_s() / DAY_S);
    let span = if place.curve.first_s() == 0.0 {
        format!("from shutdown to {last}")
    } else {
        format!(
            "{} to {last} after shutdown",
            when(place.curve.first_s() / DAY_S)
        )
    };
    match (place.distance_m, &place.source) {
        (Some(m), _) => format!(
            "{file} · point source at {} m · {span}",
            visuals::compact_number(m)
        ),
        (None, Some(source)) => format!("{file} · {source} · {span}"),
        (None, None) => format!("{file} · {span}"),
    }
}

/// Dose rate at every place against cooling time, on log axes, with the access limit and the
/// earliest allowed wait.
fn draw_curves(
    ui: &mut egui::Ui,
    places: &[&Place],
    access_sv_h: Option<f64>,
    earliest_d: Option<f64>,
    accent: egui::Color32,
) {
    let single = places.len() == 1;
    Plot::new("maintenance-dose")
        .height(240.)
        .allow_scroll(false)
        .x_axis_label("log10(Days after shutdown)")
        .y_axis_label("log10(Dose rate, mSv / h)")
        .legend(egui_plot::Legend::default().position(egui_plot::Corner::LeftBottom))
        .show(ui, |p| {
            for place in places {
                let points: Vec<[f64; 2]> = place
                    .curve
                    .points()
                    .filter(|(t, r)| *t > 0.0 && *r > 0.0)
                    .map(|(t, r)| [(t / DAY_S).log10(), (r * 1e3).log10()])
                    .collect();
                let mut line = Line::new(place.name.clone(), points);
                if single {
                    line = line.color(accent);
                }
                p.line(line);
            }
            if let Some(limit) = access_sv_h.filter(|l| *l > 0.0) {
                p.hline(HLine::new("Access limit", (limit * 1e3).log10()));
            }
            if let Some(d) = earliest_d.filter(|d| *d > 0.0) {
                p.vline(VLine::new("Earliest allowed wait", d.log10()));
            }
        });
}

fn table(ui: &mut egui::Ui, id: &str, headers: &[&str], rows: &[Vec<String>]) {
    TableBuilder::new(ui)
        .id_salt(id)
        .striped(true)
        .resizable(true)
        .column(Column::initial(140.).at_least(80.))
        .columns(Column::remainder().at_least(90.), headers.len() - 1)
        .vscroll(false)
        .header(28., |mut h| {
            for &label in headers {
                h.col(|ui| {
                    ui.strong(label);
                });
            }
        })
        .body(|body| {
            body.rows(27., rows.len(), |mut row| {
                let cells = &rows[row.index()];
                for (i, text) in cells.iter().enumerate() {
                    row.col(|ui| {
                        if i == 0 {
                            ui.label(text);
                        } else {
                            ui.monospace(text);
                        }
                    });
                }
            });
        });
}

#[cfg(test)]
mod tests {
    use super::*;

    fn run(rates: &[f64]) -> Vec<u8> {
        let mut steps = vec![json!({"t_s": 0.0, "flux": 1.0,
            "photon_source": {"point_source_dose": {"gamma_air_dose_Sv_h": 1.0, "distance_m": 1.0}}})];
        for (i, r) in rates.iter().enumerate() {
            steps.push(json!({"t_s": (i + 1) as f64 * DAY_S, "flux": 0.0,
                "photon_source": {"point_source_dose": {"gamma_air_dose_Sv_h": r, "distance_m": 1.0}}}));
        }
        serde_json::to_vec(&json!({"steps": steps})).unwrap()
    }

    fn table() -> Vec<u8> {
        serde_json::to_vec(&json!({
            "schema": DOSE_MAP_SCHEMA, "unit": "Sv/h", "source": "transport",
            "locations": [
                {"name": "port", "times_s": [0.0, DAY_S, 10.0 * DAY_S], "dose_rate": [4e-3, 2e-3, 1e-4]},
                {"name": "hall", "times_s": [0.0, 10.0 * DAY_S], "dose_rate": [1e-4, 1e-5]}
            ]
        }))
        .unwrap()
    }

    #[test]
    fn a_run_adds_one_place_named_after_its_file_and_a_table_one_per_location() {
        let mut state = MaintenanceState::default();
        state
            .add_file("parts/pipe-result.json", &run(&[2e-3, 1e-3]))
            .unwrap();
        state.add_file("map.json", &table()).unwrap();
        let names: Vec<_> = state.places.iter().map(|p| p.name.as_str()).collect();
        assert_eq!(names, ["pipe-result", "port", "hall"]);
        assert_eq!(state.places[0].distance_m, Some(1.0));
        assert_eq!(state.places[1].source.as_deref(), Some("transport"));
        // the same file again gets fresh names
        state.add_file("map.json", &table()).unwrap();
        assert_eq!(state.places[3].name, "port 2");
        assert!(state.add_file("bad.json", b"{\"steps\": []}").is_err());
    }

    #[test]
    fn the_plan_is_in_sieverts_and_names_unnamed_jobs() {
        let mut state = MaintenanceState {
            access_limit: "3".into(),
            individual_limit: "20".into(),
            waits_d: "1, 7;30".into(),
            ..Default::default()
        };
        state.jobs.push(JobRow {
            name: String::new(),
            place: "port".into(),
            hours: "8".into(),
            crew: "2".into(),
        });
        let plan = plan(&state).unwrap();
        assert_eq!(plan.access_limit_Sv_h, 3e-3);
        assert_eq!(plan.individual_limit_Sv, Some(2e-2));
        assert_eq!(plan.waits_d, [1.0, 7.0, 30.0]);
        assert_eq!(plan.jobs[0].name, "job 1");
        assert!(plan.cost.is_none());
        state.jobs[0].crew = "two".into();
        assert!(super::plan(&state).unwrap_err().contains("whole number"));
        state.jobs[0].crew = "2".into();
        state.access_limit.clear();
        assert!(super::plan(&state).unwrap_err().contains("access limit"));
    }

    #[test]
    fn renaming_a_place_carries_its_jobs() {
        let mut state = MaintenanceState::default();
        state.add_file("map.json", &table()).unwrap();
        state.jobs.push(JobRow {
            name: "cut".into(),
            place: "port".into(),
            hours: "1".into(),
            crew: "1".into(),
        });
        state.rename("port", "port cell");
        assert_eq!(state.jobs[0].place, "port cell");
    }

    #[test]
    fn the_page_solves_the_same_plan_as_the_core_and_saves_it() {
        let mut state = MaintenanceState {
            access_limit: "1".into(),
            waits_d: "2, 5".into(),
            ..Default::default()
        };
        state.add_file("map.json", &table()).unwrap();
        state.jobs.push(JobRow {
            name: "cut".into(),
            place: "port".into(),
            hours: "4".into(),
            crew: "2".into(),
        });
        let ctx = egui::Context::default();
        let mut output = ctx.run_ui(egui::RawInput::default(), |ui| {
            assert_eq!(show(ui, &mut state, None), MaintenanceAction::None);
        });
        output.textures_delta.clear();
        let shapes = format!("{:?}", output.shapes);
        assert!(shapes.contains("Every job is allowed from"), "{shapes}");
        let curves: Vec<_> = state.places.iter().map(|p| p.curve.clone()).collect();
        let expected = maintenance::evaluate(&plan(&state).unwrap(), &curves).unwrap();
        let saved = result_document(&state).unwrap();
        assert_eq!(saved["earliest_wait_d"], json!(expected.earliest_wait_d));
        assert_eq!(saved["plan"]["schema"], maintenance::PLAN_SCHEMA);
        assert_eq!(saved["plan"]["access_limit_Sv_h"], json!(1e-3));
        assert_eq!(saved["dose_rates"]["places"][0]["name"], "port");
        assert!(expected.earliest_wait_d.is_some());
    }

    #[test]
    fn a_cli_plan_fills_the_page() {
        let dir =
            std::env::temp_dir().join(format!("actinv-maintenance-plan-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("map.json"), table()).unwrap();
        let plan = json!({
            "schema": maintenance::PLAN_SCHEMA,
            "dose_rates": {"table": "map.json"},
            "access_limit_Sv_h": 3e-3, "individual_limit_Sv": 2e-2,
            "jobs": [{"name": "cut", "location": "port", "duration_h": 8, "crew": 2}],
            "waits_d": [1, 7.5],
            "cost": {"per_person_Sv": 1e6, "per_outage_day": 2e3}
        });
        std::fs::write(dir.join("plan.json"), plan.to_string()).unwrap();
        let mut state = MaintenanceState::default();
        state.load_plan(&dir.join("plan.json")).unwrap();
        assert!(!state.use_open);
        assert_eq!(state.places.len(), 2);
        assert_eq!(state.access_limit, "3");
        assert_eq!(state.individual_limit, "20");
        assert_eq!(state.waits_d, "1, 7.5");
        assert!(state.costs);
        assert_eq!(state.jobs[0].place, "port");
        let back = super::plan(&state).unwrap();
        assert_eq!(back.jobs[0].crew, 2);
        assert_eq!(back.cost.unwrap().per_person_Sv, 1e6);
        std::fs::write(
            dir.join("twin.json"),
            json!({"schema": maintenance::PLAN_SCHEMA,
            "dose_rates": {"twin": "t.json", "shutdown_s": 0, "sv_per_gy": 1},
            "access_limit_Sv_h": 1e-3, "jobs": []})
            .to_string(),
        )
        .unwrap();
        assert!(state
            .load_plan(&dir.join("twin.json"))
            .unwrap_err()
            .contains("twin"));
        std::fs::remove_dir_all(&dir).unwrap();
    }
}
