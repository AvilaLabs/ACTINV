//! Activities known only as ranges: the form for `actinv waste bounds` and its result. The command
//! takes every combination inside the declared box and reports every class the component can
//! reach; it needs no run, so the browser can set it up as well as the desktop.
use super::waste_forms::{
    amount, card, class_chip, class_colour, class_words, section, three, Component, RULES,
};
use eframe::egui::{self, RichText};
use egui_extras::{Column, TableBuilder};
use serde_json::{json, Map, Value};
use std::collections::BTreeSet;

#[derive(Clone, Debug, Default)]
pub struct Range {
    pub nuclide: String,
    pub lower: f64,
    pub upper: f64,
}

/// The ranges at one time.
#[derive(Clone, Debug)]
pub struct When {
    pub time: String,
    pub ranges: Vec<Range>,
    /// These nuclides are the whole inventory.
    pub complete: bool,
    pub reasons: String,
    pub source: String,
    pub assumptions: String,
    /// The result step and spread a fill takes.
    pub fill_step: usize,
    pub fill_spread_pct: f64,
}

impl Default for When {
    fn default() -> Self {
        Self {
            time: "0 s".into(),
            ranges: vec![Range::default()],
            complete: true,
            reasons: String::new(),
            source: String::new(),
            assumptions: String::new(),
            fill_step: usize::MAX,
            fill_spread_pct: 50.,
        }
    }
}

pub struct BoundsState {
    pub id: String,
    pub mass_g: Option<f64>,
    pub component: Component,
    pub h3_required: bool,
    pub times: Vec<When>,
}

impl Default for BoundsState {
    fn default() -> Self {
        Self {
            id: "component".into(),
            mass_g: None,
            component: Component::default(),
            h3_required: false,
            times: vec![When::default()],
        }
    }
}

#[derive(PartialEq, Debug)]
pub enum BoundsAction {
    None,
    /// Fill time `i` from result step `step` with this spread, in percent.
    Fill {
        time: usize,
        step: usize,
        spread_pct: f64,
    },
}

impl BoundsState {
    /// Every nuclide with a positive upper value.
    pub fn nuclides(&self) -> BTreeSet<String> {
        self.times
            .iter()
            .flat_map(|w| &w.ranges)
            .filter(|r| !r.nuclide.trim().is_empty() && r.upper > 0.)
            .map(|r| r.nuclide.trim().to_owned())
            .collect()
    }

    /// Put `activity` (Bq per gram, by nuclide) times the mass into time `time`, widened by
    /// `spread_pct` either way.
    pub fn fill(&mut self, time: usize, activity: &[(String, f64)], spread_pct: f64) {
        let mass = self.mass_g.unwrap_or(1.);
        let k = (spread_pct / 100.).clamp(0., 1e6);
        if let Some(when) = self.times.get_mut(time) {
            when.ranges = activity
                .iter()
                .filter(|(_, a)| *a > 0.)
                .map(|(nuclide, a)| Range {
                    nuclide: nuclide.clone(),
                    lower: (a * mass * (1. - k)).max(0.),
                    upper: a * mass * (1. + k),
                })
                .collect();
            if when.source.trim().is_empty() {
                when.source = "the ACTINV run open in the app".into();
            }
            if when.assumptions.trim().is_empty() {
                when.assumptions =
                    format!("the run's activity, widened by {spread_pct}% either way");
            }
        }
    }
}

/// The `actinv-waste-bounds-spec-1` document.
pub fn bounds_spec(state: &BoundsState, properties: Value) -> Result<Value, String> {
    let id = state.id.trim();
    if id.is_empty() {
        return Err("Give the component a name.".into());
    }
    let mut component = state.component.fields(state.mass_g.unwrap_or(0.))?;
    let mut targets = Vec::new();
    for (i, when) in state.times.iter().enumerate() {
        let step = i + 1;
        let t_s = actinv_core::spec::parse_duration(when.time.trim())
            .map_err(|_| format!("Time {step}: write the time as a duration, such as 10 y."))?;
        let mut bounds = Map::new();
        for r in &when.ranges {
            let name = r.nuclide.trim();
            if name.is_empty() {
                continue;
            }
            if !(r.lower.is_finite() && r.upper.is_finite() && 0. <= r.lower && r.lower <= r.upper)
            {
                return Err(format!(
                    "Time {step}: the {name} range needs 0 at most lower, and lower at most upper."
                ));
            }
            if bounds
                .insert(
                    name.to_owned(),
                    json!({"lower_bq": r.lower, "upper_bq": r.upper}),
                )
                .is_some()
            {
                return Err(format!("Time {step}: {name} is listed twice."));
            }
        }
        if bounds.is_empty() {
            return Err(format!("Time {step}: give at least one nuclide's range."));
        }
        let reasons: Vec<&str> = when
            .reasons
            .lines()
            .map(str::trim)
            .filter(|l| !l.is_empty())
            .collect();
        if !when.complete && reasons.is_empty() {
            return Err(format!(
                "Time {step}: say what the ranges leave out, one reason per line."
            ));
        }
        if when.source.trim().is_empty() || when.assumptions.trim().is_empty() {
            return Err(format!(
                "Time {step}: say where the ranges come from and what they assume."
            ));
        }
        targets.push(json!({
            "step": step,
            "t_s": t_s,
            "activity_bounds_bq": bounds,
            "inventory_coverage": if when.complete { "complete" } else { "incomplete" },
            "unbounded_inventory_reasons": if when.complete { Vec::new() } else { reasons },
            "bounds_source": when.source.trim(),
            "bounds_assumptions": when.assumptions.trim(),
        }));
    }
    component["id"] = json!(id);
    component["nuclide_properties"] = properties;
    component["external_tritium"] =
        json!({"status": if state.h3_required { "required" } else { "not_applicable" }});
    component["targets"] = json!(targets);
    Ok(json!({
        "schema": "actinv-waste-bounds-spec-1",
        "rules": RULES,
        "components": [component],
    }))
}

/// The ranges form. `steps` are the open result's steps, which a time can be filled from.
pub fn show_form(
    ui: &mut egui::Ui,
    state: &mut BoundsState,
    steps: &[(u64, String)],
) -> BoundsAction {
    let mut action = BoundsAction::None;
    section(
        ui,
        "Component",
        "One component, its whole activity given as lower and upper values for each nuclide.",
    );
    card(ui, |ui| {
        ui.horizontal_wrapped(|ui| {
            ui.label("Name");
            ui.add(egui::TextEdit::singleline(&mut state.id).desired_width(140.));
            ui.label("Mass");
            let mut mass = state.mass_g.unwrap_or(0.);
            if amount(ui, &mut mass, 80.).changed() {
                state.mass_g = Some(mass);
            }
            ui.label("g");
        });
        let mass = state.mass_g.unwrap_or(0.);
        state.component.edit(ui, "bounds-component", mass);
        ui.checkbox(
            &mut state.h3_required,
            "Retained H-3 the ranges leave out may be present (the class then stays unknown)",
        );
    });

    section(
        ui,
        "Activity ranges",
        "Whole-component activity in Bq. Every combination inside the ranges is allowed, so the answer lists every class the component can reach.",
    );
    let mut remove_time = None;
    let count = state.times.len();
    for (i, when) in state.times.iter_mut().enumerate() {
        card(ui, |ui| {
            ui.push_id(("bounds-time", i), |ui| {
                ui.horizontal_wrapped(|ui| {
                    ui.label(RichText::new(format!("Time {}", i + 1)).strong());
                    ui.label("at");
                    ui.add(egui::TextEdit::singleline(&mut when.time).desired_width(80.))
                        .on_hover_text("When these ranges hold, as a duration such as 10 y");
                    ui.weak("recorded with the answer; the ranges are not decayed");
                    if count > 1 && ui.button("Remove time").clicked() {
                        remove_time = Some(i);
                    }
                });
                if !steps.is_empty() {
                    let chosen = when.fill_step.min(steps.len() - 1);
                    when.fill_step = chosen;
                    ui.horizontal_wrapped(|ui| {
                        if ui.button("Fill from this result").clicked() {
                            action = BoundsAction::Fill {
                                time: i,
                                step: chosen,
                                spread_pct: when.fill_spread_pct,
                            };
                        }
                        egui::ComboBox::from_id_salt("fill-step")
                            .selected_text(format!(
                                "step {} · {}",
                                steps[chosen].0, steps[chosen].1
                            ))
                            .show_ui(ui, |ui| {
                                for (k, (n, label)) in steps.iter().enumerate() {
                                    ui.selectable_value(
                                        &mut when.fill_step,
                                        k,
                                        format!("step {n} · {label}"),
                                    );
                                }
                            });
                        ui.label("±");
                        ui.add(
                            egui::DragValue::new(&mut when.fill_spread_pct)
                                .range(0.0..=1000.)
                                .speed(1.),
                        );
                        ui.label("%  times the mass above");
                    });
                }
                let mut remove = None;
                egui::Grid::new("ranges")
                    .num_columns(4)
                    .spacing([10., 4.])
                    .show(ui, |ui| {
                        ui.weak("Nuclide");
                        ui.weak("Lower (Bq)");
                        ui.weak("Upper (Bq)");
                        ui.end_row();
                        for (k, r) in when.ranges.iter_mut().enumerate() {
                            ui.add(
                                egui::TextEdit::singleline(&mut r.nuclide)
                                    .hint_text("C14")
                                    .desired_width(70.),
                            );
                            amount(ui, &mut r.lower, 100.);
                            amount(ui, &mut r.upper, 100.);
                            if ui.small_button("Remove").clicked() {
                                remove = Some(k);
                            }
                            ui.end_row();
                        }
                    });
                if let Some(k) = remove {
                    when.ranges.remove(k);
                }
                if ui.small_button("+ Nuclide").clicked() {
                    when.ranges.push(Range::default());
                }
                ui.add_space(4.);
                ui.checkbox(&mut when.complete, "These nuclides are the whole inventory");
                if !when.complete {
                    ui.label("What the ranges leave out, one reason per line:");
                    ui.add(
                        egui::TextEdit::multiline(&mut when.reasons)
                            .desired_rows(2)
                            .desired_width(f32::INFINITY),
                    );
                }
                egui::Grid::new("provenance")
                    .num_columns(2)
                    .spacing([10., 4.])
                    .show(ui, |ui| {
                        ui.label("Where they come from");
                        ui.add(
                            egui::TextEdit::singleline(&mut when.source)
                                .hint_text("measurement report, calculation, …")
                                .desired_width(420.),
                        );
                        ui.end_row();
                        ui.label("What they assume");
                        ui.add(
                            egui::TextEdit::singleline(&mut when.assumptions)
                                .hint_text("how the lower and upper values were set")
                                .desired_width(420.),
                        );
                        ui.end_row();
                    });
            });
        });
    }
    if let Some(i) = remove_time {
        state.times.remove(i);
    }
    if ui.button("+ Time").clicked() {
        state.times.push(When::default());
    }
    action
}

/// Every class the component can reach at each time, and the table sums behind it.
pub fn show_result(ui: &mut egui::Ui, result: &Value) {
    let Some(components) = result["components"].as_array() else {
        ui.colored_label(ui.visuals().error_fg_color, "This is not a ranges result.");
        return;
    };
    for component in components {
        for target in component["targets"].as_array().into_iter().flatten() {
            card(ui, |ui| {
                let envelope: Vec<&str> = target["class_envelope"]
                    .as_array()
                    .into_iter()
                    .flatten()
                    .filter_map(Value::as_str)
                    .collect();
                ui.horizontal_wrapped(|ui| {
                    ui.label(
                        RichText::new(format!(
                            "{} · time {} · {}",
                            component["id"].as_str().unwrap_or(""),
                            target["step"],
                            crate::model::readable_time(target["t_s"].as_f64().unwrap_or(0.)),
                        ))
                        .strong(),
                    );
                });
                ui.horizontal_wrapped(|ui| {
                    if target["class_is_stable"] == true {
                        let class = target["stable_class"].as_str().unwrap_or("unknown");
                        ui.label(
                            RichText::new(class_words(class))
                                .heading()
                                .color(class_colour(ui, class)),
                        );
                        ui.label("everywhere inside the ranges");
                    } else {
                        ui.spacing_mut().item_spacing.x = 6.;
                        ui.label(RichText::new("Can reach").heading());
                        for (k, class) in envelope.iter().enumerate() {
                            if k > 0 && k + 1 == envelope.len() {
                                ui.label(RichText::new("or").heading());
                            }
                            let comma = if k + 2 < envelope.len() { "," } else { "" };
                            ui.label(
                                RichText::new(format!("{}{comma}", class_words(class)))
                                    .heading()
                                    .color(class_colour(ui, class)),
                            );
                        }
                    }
                });
                for reason in target["coverage_reasons"].as_array().into_iter().flatten() {
                    ui.colored_label(ui.visuals().warn_fg_color, reason.as_str().unwrap_or(""));
                }
                let rows = target["constraint_ranges"]
                    .as_array()
                    .cloned()
                    .unwrap_or_default();
                if rows.is_empty() {
                    return;
                }
                ui.push_id(
                    (
                        "bounds-rows",
                        component["id"].to_string(),
                        target["step"].to_string(),
                    ),
                    |ui| {
                        TableBuilder::new(ui)
                            .striped(true)
                            .cell_layout(egui::Layout::left_to_right(egui::Align::Center))
                            .column(Column::auto().at_least(110.))
                            .column(Column::auto().at_least(130.))
                            .column(Column::auto().at_least(170.))
                            .column(Column::auto().at_least(90.))
                            .column(Column::remainder().at_least(120.))
                            .header(24., |mut h| {
                                for t in [
                                    "Class limit",
                                    "Table",
                                    "Sum of fractions",
                                    "Met",
                                    "Nuclides",
                                ] {
                                    h.col(|ui| {
                                        ui.strong(t);
                                    });
                                }
                            })
                            .body(|body| {
                                body.rows(24., rows.len(), |mut row| {
                                    let r = &rows[row.index()];
                                    row.col(|ui| {
                                        class_chip(ui, r["target_class"].as_str().unwrap_or(""));
                                    });
                                    row.col(|ui| {
                                        ui.label(match r["column"].as_u64() {
                                            Some(c) => format!("Table {}, column {c}", r["table"]),
                                            None => format!("Table {}", r["table"]),
                                        });
                                    });
                                    row.col(|ui| {
                                        ui.monospace(format!(
                                            "{} to {}",
                                            three(
                                                r["lower"]["normalized_sum"]
                                                    .as_f64()
                                                    .unwrap_or(f64::NAN)
                                            ),
                                            three(
                                                r["upper"]["normalized_sum"]
                                                    .as_f64()
                                                    .unwrap_or(f64::NAN)
                                            ),
                                        ));
                                    });
                                    row.col(|ui| {
                                        ui.label(passes_words(
                                            &r["lower"]["passes"],
                                            &r["upper"]["passes"],
                                        ));
                                    });
                                    row.col(|ui| {
                                        let who: Vec<&str> = r["upper"]["contributors"]
                                            .as_array()
                                            .into_iter()
                                            .flatten()
                                            .filter_map(|c| c.as_str().or(c["nuclide"].as_str()))
                                            .take(4)
                                            .collect();
                                        ui.label(who.join(", "));
                                    });
                                });
                            });
                    },
                );
            });
        }
    }
}

/// Whether a class limit is met across the ranges: at both ends, at neither, or only at some
/// values inside them.
fn passes_words(lower: &Value, upper: &Value) -> &'static str {
    match (lower.as_bool(), upper.as_bool()) {
        (Some(true), Some(true)) => "always",
        (Some(false), _) => "never",
        (Some(true), Some(false)) => "depends on the values",
        _ => "unknown",
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn example() -> BoundsState {
        let mut state = BoundsState {
            id: "example-component".into(),
            mass_g: Some(1000.),
            ..BoundsState::default()
        };
        state.component.use_density = false;
        state.component.volume_cm3 = 128.2;
        state.times[0].ranges = vec![
            Range {
                nuclide: "C14".into(),
                lower: 100.,
                upper: 200.,
            },
            Range {
                nuclide: "Nb94".into(),
                lower: 5e4,
                upper: 2e5,
            },
        ];
        state.times[0].source = "illustrative declared enclosure".into();
        state.times[0].assumptions = "artificial input".into();
        state
    }

    #[test]
    fn the_example_is_written_as_the_command_reads_it() {
        let state = example();
        let spec = bounds_spec(
            &state,
            json!({"C14": {"z": 6, "half_life_s": 1.8e11, "alpha_emitting": false}}),
        )
        .unwrap();
        let c = &spec["components"][0];
        assert_eq!(spec["schema"], "actinv-waste-bounds-spec-1");
        assert_eq!(c["displaced_volume_cm3"], 128.2);
        assert_eq!(c["external_tritium"]["status"], "not_applicable");
        assert_eq!(
            c["targets"][0]["activity_bounds_bq"]["Nb94"]["upper_bq"],
            2e5
        );
        assert_eq!(c["targets"][0]["inventory_coverage"], "complete");
        assert_eq!(c["targets"][0]["unbounded_inventory_reasons"], json!([]));
        assert_eq!(c["targets"][0]["t_s"], 0.);
        assert_eq!(
            state.nuclides(),
            ["C14".to_owned(), "Nb94".to_owned()].into()
        );
    }

    #[test]
    fn bad_ranges_are_refused_in_words() {
        let mut state = example();
        state.times[0].ranges[0].lower = 300.;
        assert!(bounds_spec(&state, json!({}))
            .unwrap_err()
            .contains("lower at most upper"));
        let mut state = example();
        state.times[0].complete = false;
        assert!(bounds_spec(&state, json!({}))
            .unwrap_err()
            .contains("leave out"));
        state.times[0].reasons = "Co60 not measured\n".into();
        let spec = bounds_spec(&state, json!({})).unwrap();
        assert_eq!(
            spec["components"][0]["targets"][0]["unbounded_inventory_reasons"],
            json!(["Co60 not measured"])
        );
        let mut state = example();
        state.times[0].source.clear();
        assert!(bounds_spec(&state, json!({}))
            .unwrap_err()
            .contains("come from"));
        let mut state = example();
        state.times[0].time = "soon".into();
        assert!(bounds_spec(&state, json!({}))
            .unwrap_err()
            .contains("duration"));
        let mut state = example();
        state.mass_g = None;
        assert!(bounds_spec(&state, json!({})).is_err());
    }

    #[test]
    fn a_fill_takes_the_run_times_the_mass_and_widens_it() {
        let mut state = example();
        state.times[0].source.clear();
        state.fill(0, &[("Co60".into(), 2.), ("Fe55".into(), 0.)], 50.);
        let r = &state.times[0].ranges;
        assert_eq!(r.len(), 1);
        assert_eq!((r[0].lower, r[0].upper), (1000., 3000.));
        assert!(!state.times[0].source.is_empty());
    }
}
