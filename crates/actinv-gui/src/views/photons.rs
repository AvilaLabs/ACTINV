//! Photons & dose: decay photon groups, bremsstrahlung, dose rates and dose against cooling time.
use crate::{model, views::Target, visuals};
use eframe::egui;
use egui_extras::{Column, TableBuilder};
use egui_plot::{Line, Plot, Points};
use serde_json::Value;

/// View state kept between frames: only the display choices of the dose-against-time plot.
#[derive(Default)]
pub struct PhotonsState {
    log_time: bool,
    log_dose: bool,
}

#[derive(Debug, PartialEq)]
pub enum PhotonsAction {
    None,
    /// Open a setup page (e.g. Target::Outputs to enable photons).
    Open(Target),
    /// Save CSV text under the suggested file name (photon groups of this step).
    Export {
        file_name: String,
        contents: String,
    },
}

/// One dose-rate curve over the computed steps.
struct DoseSeries {
    name: &'static str,
    points: Vec<[f64; 2]>,
}

/// Dose fields drawn against time: label and the path below the step's `photon_source`.
const DOSE_FIELDS: [(&str, &[&str]); 4] = [
    (
        "Contact (screening)",
        &["contact_gamma_air_dose_proxy_Gy_h"],
    ),
    (
        "Point source",
        &["point_source_dose", "gamma_air_dose_Sv_h"],
    ),
    (
        "Bremsstrahlung contact",
        &["bremsstrahlung", "contact_gamma_air_dose_proxy_Gy_h"],
    ),
    (
        "Bremsstrahlung point source",
        &["bremsstrahlung", "point_source_gamma_air_dose_Sv_h"],
    ),
];

fn outputs_hint() -> String {
    format!(
        "No photon source was recorded for this step. Turn on Decay photons and dose in What to compute, then {}.",
        super::RUN_AGAIN
    )
}

/// `step` is the zero-based selected step from the shell's result bar.
pub fn show(
    ui: &mut egui::Ui,
    result: &model::ResultDocument,
    step: usize,
    state: &mut PhotonsState,
) -> PhotonsAction {
    let mut action = PhotonsAction::None;
    let steps = result.steps();
    let index = step.min(steps.len().saturating_sub(1));
    let Some(step_value) = steps.get(index) else {
        ui.label("The result has no steps.");
        return action;
    };
    let ps = &step_value["photon_source"];
    if !ps.is_object() {
        ui.label(outputs_hint());
        if ui.button("Open Outputs").clicked() {
            action = PhotonsAction::Open(Target::Outputs);
        }
        return action;
    }
    let accent = visuals::accent(ui);

    ui.label(source_summary(ps));
    if has_groups(ps)
        && ui
            .button("Export photon groups (CSV)")
            .on_hover_text("Decay photon and bremsstrahlung groups of the selected step")
            .clicked()
    {
        if let Some(contents) = groups_csv(step_value) {
            action = PhotonsAction::Export {
                file_name: format!("photon_groups_step_{}.csv", index + 1),
                contents,
            };
        }
    }
    ui.add_space(8.);

    ui.heading("Decay photons");
    if bars(&ps["groups"]).iter().any(|b| b.1 > 0.) {
        group_chart(ui, "photon-groups", "Decay photons", &ps["groups"], accent);
    } else {
        ui.label("No grouped photon emission is recorded at this step. Review the photon source details and ledger for missing evaluated spectra or unrepresented emission.");
    }
    let by_nuclide = nuclide_rows(
        &ps["by_nuclide"],
        &[
            "activity_Bq_g",
            "source_photons_per_decay",
            "source_power_W_g",
            "contact_gamma_air_dose_proxy_Gy_h",
        ],
        "source_power_W_g",
    );
    if !by_nuclide.is_empty() {
        table(
            ui,
            "photon-nuclides",
            &[
                "Nuclide",
                "Activity (Bq / g)",
                "Photons / decay",
                "Source power (W / g)",
                "Contact dose (Gy / h)",
            ],
            &by_nuclide,
        );
    }

    ui.add_space(8.);
    ui.heading("Dose rates");
    let rows = dose_rows(ps);
    ui.group(|ui| {
        egui::Grid::new("photon-dose-rates")
            .num_columns(2)
            .striped(true)
            .show(ui, |ui| {
                for (label, value) in &rows {
                    ui.label(label);
                    ui.monospace(value);
                    ui.end_row();
                }
            });
    });
    for note in dose_notes(ps) {
        ui.label(note);
    }
    ui.label("Dose values are screening estimates in air, not transport results. Sv / h fields are air kerma rates, numerically Gy / h; no quality factor is applied. The sum rows add like units only.");

    if ps["bremsstrahlung"].is_object() {
        let brems = &ps["bremsstrahlung"];
        ui.add_space(8.);
        ui.heading("Bremsstrahlung");
        ui.label(brems_summary(brems));
        if bars(&brems["groups"]).iter().any(|b| b.1 > 0.) {
            group_chart(
                ui,
                "bremsstrahlung-groups",
                "Bremsstrahlung",
                &brems["groups"],
                accent,
            );
        } else {
            ui.label("No bremsstrahlung photons are recorded at this step.");
        }
        for note in brems_notes(brems) {
            ui.label(note);
        }
        let rows = nuclide_rows(
            &brems["by_nuclide"],
            &[
                "activity_Bq_g",
                "electrons_per_decay",
                "mean_beta_energy_eV",
                "photons_s_g",
                "power_W_g",
                "contact_gamma_air_dose_proxy_Gy_h",
            ],
            "power_W_g",
        );
        if !rows.is_empty() {
            table(
                ui,
                "bremsstrahlung-nuclides",
                &[
                    "Nuclide",
                    "Activity (Bq / g)",
                    "Electrons / decay",
                    "Mean beta energy (eV)",
                    "Photons / s / g",
                    "Power (W / g)",
                    "Contact dose (Gy / h)",
                ],
                &rows,
            );
        }
    }

    ui.add_space(8.);
    ui.heading("Dose against cooling time");
    ui.horizontal(|ui| {
        ui.checkbox(&mut state.log_time, "Log time");
        ui.checkbox(&mut state.log_dose, "Log dose rate");
    });
    let series = dose_series(steps, state.log_time, state.log_dose);
    if series.is_empty() {
        ui.label(
            "No dose rate is recorded at any step with the current time and dose-axis choices.",
        );
    } else {
        if state.log_time {
            ui.label("Time axis shows log10(seconds); zero-time points are omitted. Tables and exports retain physical time.");
        }
        let single = series.len() == 1;
        Plot::new("photon-dose-time")
            .height(230.)
            .allow_scroll(false)
            .x_axis_label(if state.log_time {
                "log10(Time / s)"
            } else {
                "Time (s)"
            })
            .y_axis_label(if state.log_dose {
                "log10(Dose rate in air, Gy / h)"
            } else {
                "Dose rate in air (Gy / h)"
            })
            .legend(egui_plot::Legend::default())
            .show(ui, |p| {
                for s in series {
                    if s.points.len() == 1 {
                        let mut dot = Points::new(s.name, s.points);
                        if single {
                            dot = dot.color(accent);
                        }
                        p.points(dot);
                    } else {
                        let mut line = Line::new(s.name, s.points);
                        if single {
                            line = line.color(accent);
                        }
                        p.line(line);
                    }
                }
            });
    }

    ui.add_space(8.);
    ui.horizontal(|ui| {
        ui.label(settings_line(ps));
        if ui.small_button("Edit in What to compute").clicked() {
            action = PhotonsAction::Open(Target::Outputs);
        }
    });
    visuals::json_tree(ui, "Photon source", ps);
    action
}

fn group_chart(ui: &mut egui::Ui, id: &str, name: &str, groups: &Value, accent: egui::Color32) {
    let bars: Vec<_> = bars(groups)
        .into_iter()
        .map(|(centre, height, width)| egui_plot::Bar::new(centre, height).width(width))
        .collect();
    Plot::new(id.to_owned())
        .height(230.)
        .allow_scroll(false)
        .x_axis_label("Photon energy (MeV)")
        .y_axis_label("Photons / s / g per group")
        .show(ui, |p| {
            p.bar_chart(egui_plot::BarChart::new(name, bars).color(accent))
        });
}

fn table(ui: &mut egui::Ui, id: &str, headers: &[&str], rows: &[Vec<String>]) {
    TableBuilder::new(ui)
        .id_salt(id)
        .striped(true)
        .resizable(true)
        .column(Column::initial(120.).at_least(80.))
        .columns(Column::remainder().at_least(110.), headers.len() - 1)
        .max_scroll_height(250.)
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

/// Bars of a group list as `(centroid MeV, photons per second per gram, width MeV)`.
fn bars(groups: &Value) -> Vec<(f64, f64, f64)> {
    groups
        .as_array()
        .map(|groups| {
            groups
                .iter()
                .map(|g| {
                    let (low, high) = (model::number(&g["low_eV"]), model::number(&g["high_eV"]));
                    // an empty group has no centroid; centring its bar on zero stretched the
                    // energy axis below 0 MeV
                    let centroid = model::number(&g["centroid_eV"]);
                    let centre = if centroid > 0. {
                        centroid
                    } else {
                        (low + high) / 2.
                    };
                    (
                        centre / 1e6,
                        model::number(&g["photons_s_g"]),
                        (high - low) / 1e6,
                    )
                })
                .collect()
        })
        .unwrap_or_default()
}

fn has_groups(ps: &Value) -> bool {
    [&ps["groups"], &ps["bremsstrahlung"]["groups"]]
        .iter()
        .any(|g| g.as_array().is_some_and(|a| !a.is_empty()))
}

/// Photon groups of one step as CSV, decay photons first and bremsstrahlung after, with the
/// exact `{:e}` values of the result. `None` when the step has no groups.
fn groups_csv(step: &Value) -> Option<String> {
    let ps = &step["photon_source"];
    let mut out = String::from(
        "component,low_eV,high_eV,centroid_eV,photons_s_g,photons_s,power_W_g,power_W\n",
    );
    let mut count = 0;
    for (component, groups) in [
        ("decay_photons", &ps["groups"]),
        ("bremsstrahlung", &ps["bremsstrahlung"]["groups"]),
    ] {
        for g in groups.as_array().into_iter().flatten() {
            count += 1;
            out.push_str(component);
            for key in [
                "low_eV",
                "high_eV",
                "centroid_eV",
                "photons_s_g",
                "photons_s",
                "power_W_g",
                "power_W",
            ] {
                out.push_str(&format!(",{:e}", model::number(&g[key])));
            }
            out.push('\n');
        }
    }
    (count > 0).then_some(out)
}

/// A number as `{:.4e}`, or a dash for an absent or null field.
fn cell(v: &Value) -> String {
    v.as_f64()
        .map_or_else(|| "-".to_string(), |x| format!("{x:.4e}"))
}

/// A dose field, or the words saying it is not reported.
fn reported(v: &Value) -> String {
    v.as_f64()
        .map_or_else(|| "not reported".to_string(), |x| format!("{x:.4e}"))
}

/// Table rows for a per-nuclide list: the nuclide name, then each key, largest `sort_key` first.
fn nuclide_rows(list: &Value, keys: &[&str], sort_key: &str) -> Vec<Vec<String>> {
    let mut entries: Vec<&Value> = list.as_array().into_iter().flatten().collect();
    entries.sort_by(|a, b| model::number(&b[sort_key]).total_cmp(&model::number(&a[sort_key])));
    entries
        .into_iter()
        .map(|e| {
            let mut row = vec![e["nuclide"].as_str().unwrap_or("").to_string()];
            row.extend(keys.iter().map(|k| cell(&e[*k])));
            row
        })
        .collect()
}

/// Dose curves over the steps. Steps without a value (null or absent) leave a gap, and with
/// log axes steps at zero time or with a non-positive dose are omitted.
fn dose_series(steps: &[Value], log_time: bool, log_dose: bool) -> Vec<DoseSeries> {
    DOSE_FIELDS
        .iter()
        .filter_map(|&(name, path)| {
            let points: Vec<[f64; 2]> = steps
                .iter()
                .filter_map(|s| {
                    let mut at = &s["photon_source"];
                    for key in path {
                        at = &at[*key];
                    }
                    let dose = at.as_f64().filter(|d| d.is_finite())?;
                    let t = model::number(&s["t_s"]);
                    if (log_time && t <= 0.) || (log_dose && dose <= 0.) {
                        return None;
                    }
                    Some([
                        if log_time { t.log10() } else { t },
                        if log_dose { dose.log10() } else { dose },
                    ])
                })
                .collect();
            (!points.is_empty()).then_some(DoseSeries { name, points })
        })
        .collect()
}

fn source_summary(ps: &Value) -> String {
    format!(
        "Group structure {} · {} groups · {} photons / s / g · source power {} W / g · {} of gamma power represented",
        ps["group_structure"].as_str().unwrap_or("unknown"),
        ps["groups"].as_array().map_or(0, Vec::len),
        cell(&ps["total_photons_s_g"]),
        cell(&ps["source_power_W_g"]),
        ps["represented_gamma_power_fraction"]
            .as_f64()
            .map_or_else(|| "-".to_string(), |f| format!("{:.2}%", 100. * f)),
    )
}

fn brems_summary(b: &Value) -> String {
    format!(
        "Model {} · Z_eff {} · {} photons / s / g · power {} W / g · energy closure {} · zero-lower-edge energy fraction {}",
        b["model"].as_str().unwrap_or("unknown"),
        cell(&b["z_eff"]),
        cell(&b["photons_s_g"]),
        cell(&b["power_W_g"]),
        cell(&b["energy_closure"]),
        cell(&b["zero_lower_edge_energy_fraction"]),
    )
}

/// Label and value rows of the dose-rate card.
fn dose_rows(ps: &Value) -> Vec<(String, String)> {
    let mut rows = vec![(
        "Contact, semi-infinite slab (Gy / h)".to_string(),
        reported(&ps["contact_gamma_air_dose_proxy_Gy_h"]),
    )];
    let brems = &ps["bremsstrahlung"];
    if brems.is_object() {
        rows.push((
            "Bremsstrahlung contact (Gy / h)".into(),
            reported(&brems["contact_gamma_air_dose_proxy_Gy_h"]),
        ));
        if let Some(sum) = sum_of(
            &ps["contact_gamma_air_dose_proxy_Gy_h"],
            &brems["contact_gamma_air_dose_proxy_Gy_h"],
        ) {
            rows.push((
                "Sum of the two contact fields (Gy / h)".into(),
                format!("{sum:.4e}"),
            ));
        }
    }
    let point = &ps["point_source_dose"];
    if point.is_object() {
        let distance = model::number(&point["distance_m"]);
        rows.push((
            format!("Dose at {distance} m (Sv / h, air kerma)"),
            reported(&point["gamma_air_dose_Sv_h"]),
        ));
        rows.push((
            "Dose per gram (Sv / h / g)".into(),
            reported(&point["gamma_air_dose_Sv_h_per_g"]),
        ));
        rows.push((
            "Source mass (g)".into(),
            format!("{}", model::number(&point["mass_g"])),
        ));
        rows.push((
            "Air attenuation".into(),
            if point["air_attenuation"].as_bool().unwrap_or(false) {
                format!(
                    "on, air density {} g/cm3",
                    model::number(&point["air_density_g_cm3"])
                )
            } else {
                "off".into()
            },
        ));
        if brems.is_object() {
            rows.push((
                format!("Bremsstrahlung at {distance} m (Sv / h)"),
                reported(&brems["point_source_gamma_air_dose_Sv_h"]),
            ));
            if let Some(sum) = sum_of(
                &point["gamma_air_dose_Sv_h"],
                &brems["point_source_gamma_air_dose_Sv_h"],
            ) {
                rows.push((
                    format!("Sum of the two fields at {distance} m (Sv / h)"),
                    format!("{sum:.4e}"),
                ));
            }
        }
    }
    rows
}

/// The sum of two dose fields, only when both are present.
fn sum_of(a: &Value, b: &Value) -> Option<f64> {
    Some(a.as_f64()? + b.as_f64()?)
}

/// Why a dose is missing, for each dose of the decay-photon source that is null.
fn dose_notes(ps: &Value) -> Vec<String> {
    let mut notes = Vec::new();
    if ps["contact_gamma_air_dose_proxy_Gy_h"].is_null() {
        let mut note = "The contact dose is not reported at this step. It needs a photon response covering the material's elements, and photon power below the response's lowest energy must stay under 1e-4 of the step's photon power.".to_string();
        if let Some(coverage) = ps["dose_response_power_coverage"].as_f64() {
            note.push_str(&format!(" Response power coverage here: {coverage:.4}."));
        }
        if let Some(sub) = ps["dose_response_subthreshold_power_W_g"].as_f64() {
            note.push_str(&format!(" Power below the response: {sub:.4e} W / g."));
        }
        notes.push(note);
    }
    let point = &ps["point_source_dose"];
    if point.is_object() && point["gamma_air_dose_Sv_h"].is_null() {
        notes.push(format!(
            "The point-source dose is not available: {}. Power it could not use: {} W below the response's lowest energy, {} W outside the response or the group structure.",
            point["unavailable_reason"]
                .as_str()
                .unwrap_or("no reason was recorded"),
            cell(&point["subthreshold_power_W"]),
            cell(&point["excluded_power_W"]),
        ));
    }
    notes
}

/// Warnings about bremsstrahlung inputs that are missing or unused.
fn brems_notes(b: &Value) -> Vec<String> {
    let mut notes = Vec::new();
    if let Some(reason) = b["z_eff_unavailable_reason"].as_str() {
        notes.push(format!("Effective atomic number unavailable: {reason}."));
    }
    if b["inputs_complete"].as_bool() == Some(false) {
        let missing: Vec<String> = b["missing_inputs"]
            .as_array()
            .into_iter()
            .flatten()
            .map(|m| {
                format!(
                    "{} ({})",
                    m["nuclide"].as_str().unwrap_or("?"),
                    m["missing"].as_str().unwrap_or("input missing")
                )
            })
            .collect();
        notes.push(format!(
            "Missing inputs, so these nuclides are absent from the totals: {}.",
            missing.join(", ")
        ));
    }
    let idle: Vec<&str> = b["requested_nuclides_without_activity"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .collect();
    if !idle.is_empty() {
        notes.push(format!(
            "Requested without activity at this step: {}.",
            idle.join(", ")
        ));
    }
    notes
}

/// The photon settings the result records, to point back at the Outputs page.
fn settings_line(ps: &Value) -> String {
    let point = &ps["point_source_dose"];
    if point.is_object() {
        format!(
            "Source settings are edited in What to compute (point source at {} m, group structure {}).",
            model::number(&point["distance_m"]),
            ps["group_structure"].as_str().unwrap_or("unknown")
        )
    } else {
        format!(
            "Source settings are edited in What to compute (group structure {}).",
            ps["group_structure"].as_str().unwrap_or("unknown")
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn step(t: f64, photon_source: Value) -> Value {
        json!({
            "t_s": t,
            "inventory": [],
            "activity_Bq_per_g": {},
            "heat_W_per_g": {"total": 0.},
            "photon_source": photon_source
        })
    }

    fn source(contact: Value, point: Value, brems_point: Value) -> Value {
        json!({
            "group_structure": "fispact-24",
            "groups": [
                {"low_eV": 0., "high_eV": 1.0e6, "centroid_eV": 5.0e5, "photons_s_g": 3.0,
                 "photons_s": 3.0, "power_W_g": 1.0e-13, "power_W": 1.0e-13},
                {"low_eV": 1.0e6, "high_eV": 2.0e6, "centroid_eV": 1.5e6, "photons_s_g": 0.0,
                 "photons_s": 0.0, "power_W_g": 0.0, "power_W": 0.0}
            ],
            "by_nuclide": [
                {"nuclide": "A1", "activity_Bq_g": 1.0, "source_photons_per_decay": 2.0,
                 "source_power_W_g": 1.0e-14, "contact_gamma_air_dose_proxy_Gy_h": null},
                {"nuclide": "B2", "activity_Bq_g": 5.0, "source_photons_per_decay": 1.0,
                 "source_power_W_g": 9.0e-14, "contact_gamma_air_dose_proxy_Gy_h": 1.0e-6}
            ],
            "total_photons_s_g": 3.0,
            "source_power_W_g": 1.0e-13,
            "represented_gamma_power_fraction": 0.5,
            "contact_gamma_air_dose_proxy_Gy_h": contact,
            "point_source_dose": point,
            "bremsstrahlung": {
                "model": "thin-target",
                "z_eff": 7.0,
                "z_eff_unavailable_reason": null,
                "groups": [{"low_eV": 0., "high_eV": 1.0e5, "centroid_eV": 5.0e4,
                            "photons_s_g": 0.5, "photons_s": 0.5, "power_W_g": 1.0e-15,
                            "power_W": 1.0e-15}],
                "by_nuclide": [{"nuclide": "P32", "activity_Bq_g": 2.0, "electrons_per_decay": 1.0,
                                "mean_beta_energy_eV": 6.95e5, "photons_s_g": 0.5,
                                "power_W_g": 1.0e-15, "contact_gamma_air_dose_proxy_Gy_h": 2.0e-9}],
                "contact_gamma_air_dose_proxy_Gy_h": 2.0e-9,
                "point_source_gamma_air_dose_Sv_h": brems_point,
                "inputs_complete": false,
                "missing_inputs": [{"nuclide": "K42", "missing": "no mean beta energy"}],
                "requested_nuclides_without_activity": ["Cl36"]
            }
        })
    }

    fn point(dose: Value, reason: Value) -> Value {
        json!({"distance_m": 1.0, "mass_g": 1.0, "air_attenuation": true,
               "air_density_g_cm3": 0.0012, "gamma_air_dose_Sv_h": dose,
               "gamma_air_dose_Sv_h_per_g": dose, "subthreshold_power_W": 2.0e-9,
               "excluded_power_W": 3.0e-9, "unavailable_reason": reason})
    }

    fn document() -> model::ResultDocument {
        let first = step(
            0.,
            source(
                json!(1.0e-6),
                point(json!(2.0e-7), Value::Null),
                json!(1.0e-9),
            ),
        );
        let second = step(
            10.,
            source(
                json!(5.0e-7),
                point(json!(1.0e-7), Value::Null),
                Value::Null,
            ),
        );
        model::ResultDocument::parse(json!({"steps": [first, second]}), "test".into())
            .expect("valid result")
    }

    #[test]
    fn bars_are_in_mev_with_group_widths() {
        let ps = source(Value::Null, Value::Null, Value::Null);
        let b = bars(&ps["groups"]);
        assert_eq!(b.len(), 2);
        assert_eq!(b[0], (0.5, 3.0, 1.0));
        assert_eq!(b[1], (1.5, 0.0, 1.0));
        assert!(bars(&Value::Null).is_empty());
    }

    #[test]
    fn dose_series_skip_nulls_and_respect_log_axes() {
        let doc = document();
        let linear = dose_series(doc.steps(), false, false);
        let names: Vec<_> = linear.iter().map(|s| s.name).collect();
        assert_eq!(
            names,
            [
                "Contact (screening)",
                "Point source",
                "Bremsstrahlung contact",
                "Bremsstrahlung point source"
            ]
        );
        assert_eq!(linear[0].points, vec![[0., 1.0e-6], [10., 5.0e-7]]);
        // The second step has no bremsstrahlung point-source dose.
        assert_eq!(linear[3].points, vec![[0., 1.0e-9]]);
        let logged = dose_series(doc.steps(), true, true);
        assert_eq!(logged[0].points.len(), 1, "zero-time step omitted");
        assert!((logged[0].points[0][0] - 1.).abs() < 1e-12);
        assert!((logged[0].points[0][1] - 5.0e-7_f64.log10()).abs() < 1e-12);
    }

    #[test]
    fn groups_csv_lists_decay_then_bremsstrahlung_groups() {
        let doc = document();
        let csv = groups_csv(&doc.steps()[0]).expect("groups present");
        let lines: Vec<_> = csv.lines().collect();
        assert_eq!(
            lines[0],
            "component,low_eV,high_eV,centroid_eV,photons_s_g,photons_s,power_W_g,power_W"
        );
        assert_eq!(lines.len(), 4);
        assert_eq!(lines[1], "decay_photons,0e0,1e6,5e5,3e0,3e0,1e-13,1e-13");
        assert!(lines[3].starts_with("bremsstrahlung,0e0,1e5,5e4,5e-1,"));
        assert!(groups_csv(&json!({"photon_source": null})).is_none());
    }

    #[test]
    fn null_point_source_dose_states_its_reason() {
        let ps = source(
            json!(1.0e-6),
            point(Value::Null, json!("no air attenuation curve")),
            Value::Null,
        );
        let notes = dose_notes(&ps);
        assert_eq!(notes.len(), 1);
        assert!(notes[0].contains("no air attenuation curve"));
        assert!(notes[0].contains("2.0000e-9"));
        assert!(notes[0].contains("3.0000e-9"));
        let rows = dose_rows(&ps);
        assert!(rows
            .iter()
            .any(|(k, v)| k.starts_with("Dose at 1 m") && v == "not reported"));
        // A missing reason is still stated.
        let ps = source(json!(1.0e-6), point(Value::Null, Value::Null), Value::Null);
        assert!(dose_notes(&ps)[0].contains("no reason was recorded"));
    }

    #[test]
    fn null_contact_dose_is_explained_and_sums_need_both_fields() {
        let ps = source(
            Value::Null,
            point(json!(2.0e-7), Value::Null),
            json!(1.0e-9),
        );
        assert!(dose_notes(&ps)[0].contains("contact dose is not reported"));
        let rows = dose_rows(&ps);
        assert!(!rows
            .iter()
            .any(|(k, _)| k.starts_with("Sum of the two contact")));
        let sum = rows
            .iter()
            .find(|(k, _)| k.starts_with("Sum of the two fields"))
            .expect("point-source sum");
        assert_eq!(sum.1, "2.0100e-7");
        assert!(rows
            .iter()
            .any(|(k, v)| k == "Air attenuation" && v == "on, air density 0.0012 g/cm3"));
    }

    #[test]
    fn bremsstrahlung_warnings_name_the_missing_inputs() {
        let ps = source(json!(1.0e-6), Value::Null, Value::Null);
        let notes = brems_notes(&ps["bremsstrahlung"]);
        assert_eq!(notes.len(), 2);
        assert!(notes[0].contains("K42 (no mean beta energy)"));
        assert!(notes[1].contains("Cl36"));
    }

    #[test]
    fn nuclide_rows_sort_by_the_chosen_key_and_dash_nulls() {
        let ps = source(Value::Null, Value::Null, Value::Null);
        let rows = nuclide_rows(
            &ps["by_nuclide"],
            &["activity_Bq_g", "contact_gamma_air_dose_proxy_Gy_h"],
            "source_power_W_g",
        );
        assert_eq!(rows[0], ["B2", "5.0000e0", "1.0000e-6"]);
        assert_eq!(rows[1], ["A1", "1.0000e0", "-"]);
    }

    fn render(doc: &model::ResultDocument, step: usize) -> PhotonsAction {
        let ctx = egui::Context::default();
        let mut state = PhotonsState::default();
        let mut action = PhotonsAction::None;
        for _ in 0..2 {
            let mut output = ctx.run_ui(Default::default(), |ui| {
                action = show(ui, doc, step, &mut state);
            });
            output.textures_delta.clear();
        }
        action
    }

    #[test]
    fn page_renders_with_and_without_a_photon_source() {
        assert_eq!(render(&document(), 0), PhotonsAction::None);
        // A step past the end is clamped to the last step.
        assert_eq!(render(&document(), 99), PhotonsAction::None);
        let bare =
            model::ResultDocument::parse(json!({"steps": [step(0., Value::Null)]}), "bare".into())
                .expect("valid result");
        assert_eq!(render(&bare, 0), PhotonsAction::None);
    }
}
