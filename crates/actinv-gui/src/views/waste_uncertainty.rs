//! How sure the class is: the form for `actinv waste uncertainty` and its result. The command solves
//! the problem once per sample, with the nuclear data drawn from their uncertainties, classifies
//! every sample and says which class holds at 95 % confidence.
use super::waste_forms::{card, class_colour, class_words, section, step_choice, Component, RULES};
use eframe::egui::{self, RichText};
use serde_json::{json, Value};
use std::collections::BTreeSet;

pub const COVARIANCE: &str = "catalog:tendl-2025-neutron-709g-covariance";
/// Samples needed before a class can be established at 95 % confidence.
pub const ESTABLISH: u64 = 72;

pub struct SureState {
    pub component: Component,
    pub skipped: BTreeSet<u64>,
    pub samples: u64,
    pub seed: u64,
    pub cross_sections: bool,
    pub decay_constants: bool,
    pub flux_pct: f64,
}

impl Default for SureState {
    fn default() -> Self {
        Self {
            component: Component::default(),
            skipped: BTreeSet::new(),
            samples: 100,
            seed: 1,
            cross_sections: true,
            decay_constants: true,
            flux_pct: 0.,
        }
    }
}

/// Why the problem cannot be sampled, in words; `None` when it can.
pub fn unsupported(document: &Value) -> Option<String> {
    let options = &document["options"];
    if options["gas"] == true {
        return Some("The problem tracks gas release, which the sampler does not support.".into());
    }
    if ["rate_scale", "decay_scale", "yield_scale"]
        .iter()
        .any(|k| !options[k].is_null())
    {
        return Some(
            "The problem scales rates, decay or yields itself; the sampler writes those scales."
                .into(),
        );
    }
    for (i, step) in document["schedule"]
        .as_array()
        .into_iter()
        .flatten()
        .enumerate()
    {
        let n = i + 1;
        if !step["power"].is_null() {
            return Some(format!(
                "Step {n} is set by power; the sampler needs every step set by flux."
            ));
        }
        if !step["wall_loading_MW_m2"].is_null() || !step["partition"].is_null() {
            return Some(format!(
                "Step {n} is set by wall loading; the sampler needs every step set by flux."
            ));
        }
        if !step["library"].is_null() || !step["projectile"].is_null() {
            return Some(format!(
                "Step {n} switches library or projectile, which the sampler does not support."
            ));
        }
        if !step["spectrum"].is_null() {
            return Some(format!(
                "Step {n} has its own spectrum; the sampler varies only the main one."
            ));
        }
    }
    None
}

/// The problem the sampler solves: this problem without its `uncertainty` block, because the
/// sampler draws its own.
pub fn sampled_problem(spec: &Value) -> Value {
    let mut problem = spec.clone();
    if let Some(map) = problem.as_object_mut() {
        map.remove("uncertainty");
    }
    problem
}

/// The `actinv-waste-uncertainty-spec-1` document. `problem` is how it names the problem file.
pub fn uncertainty_spec(
    state: &SureState,
    problem: &str,
    targets: &[u64],
    mass_g: f64,
) -> Result<Value, String> {
    if targets.is_empty() {
        return Err("Choose at least one step to classify.".into());
    }
    if !(20..=4096).contains(&state.samples) {
        return Err("Use between 20 and 4096 samples.".into());
    }
    let flux = state.flux_pct / 100.;
    if !(flux.is_finite() && (0. ..=1.).contains(&flux)) {
        return Err("The flux spread is a percentage between 0 and 100.".into());
    }
    if !state.cross_sections && !state.decay_constants && flux == 0. {
        return Err("Vary at least one input: cross sections, decay constants or the flux.".into());
    }
    let mut component = state.component.fields(mass_g)?;
    component["id"] = json!("component");
    component["external_tritium"] = json!({"status": "not_applicable"});
    let mut channels = json!({});
    if state.cross_sections {
        channels["cross_section_mf33"] = json!(true);
    }
    if state.decay_constants {
        channels["decay_constants"] = json!(true);
    }
    if flux > 0. {
        channels["flux_rel_std"] = json!(flux);
    }
    let mut sampling = json!({"channels": channels, "samples": state.samples, "seed": state.seed});
    if state.cross_sections {
        sampling["covariance"] = json!({"path": COVARIANCE});
    }
    Ok(json!({
        "schema": "actinv-waste-uncertainty-spec-1",
        "problem": problem,
        "rules": RULES,
        "targets": targets,
        "nuclide_properties": "from_decay_data",
        "components": [component],
        "sampling": sampling,
        "confidence_level": 0.95,
    }))
}

/// The form. `steps` are the problem's schedule steps; `covariance` says whether the covariance
/// data is installed (None where that cannot be known, the browser).
pub fn show_form(
    ui: &mut egui::Ui,
    state: &mut SureState,
    steps: &[(u64, String)],
    mass_g: f64,
    covariance: Option<bool>,
) {
    section(
        ui,
        "Component",
        &format!(
            "The irradiated material, {} g, the mass of the problem.",
            super::waste_forms::three(mass_g)
        ),
    );
    card(ui, |ui| state.component.edit(ui, "sure-component", mass_g));

    section(ui, "Steps to classify", "");
    card(ui, |ui| step_choice(ui, "sure", steps, &mut state.skipped));

    section(
        ui,
        "What varies",
        "Each sample redraws these inputs from their uncertainties and solves the problem again.",
    );
    card(ui, |ui| {
        ui.horizontal_wrapped(|ui| {
            ui.checkbox(&mut state.cross_sections, "Cross sections");
            match covariance {
                Some(false) if state.cross_sections => {
                    ui.colored_label(
                        ui.visuals().warn_fg_color,
                        "needs the covariance data: actinv data fetch tendl-2025-neutron-covariance",
                    );
                }
                _ => {
                    ui.weak("from the TENDL-2025 covariance data");
                }
            }
        });
        ui.horizontal_wrapped(|ui| {
            ui.checkbox(&mut state.decay_constants, "Decay constants");
            ui.weak("from the half-life uncertainties in the decay data");
        });
        ui.horizontal_wrapped(|ui| {
            ui.label("Flux");
            ui.add(
                egui::DragValue::new(&mut state.flux_pct)
                    .range(0.0..=100.)
                    .speed(0.5)
                    .suffix(" %"),
            );
            ui.weak("one standard deviation; 0 keeps the flux fixed");
        });
        ui.add_space(4.);
        ui.horizontal_wrapped(|ui| {
            ui.label("Samples");
            ui.add(egui::DragValue::new(&mut state.samples).range(20..=4096));
            ui.label("seed");
            ui.add(egui::DragValue::new(&mut state.seed).range(0..=u64::MAX));
            if state.samples < ESTABLISH {
                ui.colored_label(
                    ui.visuals().warn_fg_color,
                    format!("fewer than {ESTABLISH} cannot establish a class at 95 %"),
                );
            } else {
                ui.weak(format!("one solve each, plus the nominal; {ESTABLISH} or more can establish a class at 95 %"));
            }
        });
    });
}

const CLASSES: [&str; 5] = ["A", "B", "C", "above_class_c", "unknown"];

/// The established class at each step and the share of samples in each class.
pub fn show_result(ui: &mut egui::Ui, result: &Value) {
    let Some(component) = result["components"].get(0) else {
        ui.colored_label(
            ui.visuals().error_fg_color,
            "This is not a how-sure result.",
        );
        return;
    };
    let samples = result["samples"].as_u64().unwrap_or(0);
    let failed = result["samples_failed"].as_u64().unwrap_or(0);
    ui.weak(format!(
        "{samples} samples at {}% confidence{}.",
        result["confidence_level"].as_f64().unwrap_or(0.95) * 100.,
        if failed > 0 {
            format!("; {failed} failed to solve and count as unknown")
        } else {
            String::new()
        }
    ));
    for target in component["targets"].as_array().into_iter().flatten() {
        card(ui, |ui| {
            let established = target["established_class"]
                .as_str()
                .unwrap_or("not_established");
            ui.horizontal_wrapped(|ui| {
                ui.label(
                    RichText::new(format!(
                        "Step {} · {}",
                        target["step"],
                        crate::model::readable_time(target["t_s"].as_f64().unwrap_or(0.))
                    ))
                    .strong(),
                );
                ui.label(
                    RichText::new(class_words(established))
                        .heading()
                        .color(class_colour(ui, established)),
                );
                let nominal = target["nominal"]["class"].as_str();
                if let Some(nominal) = nominal {
                    ui.weak(format!("nominal {}", class_words(nominal)));
                }
            });
            if established == "not_established" {
                ui.label(needed_words(&target["needed_samples"]));
            }
            ui.add_space(4.);
            bars(ui, &target["classes"]);
        });
    }
    if let Some(statement) = result["coverage"]["statement"].as_str() {
        ui.weak(statement);
    }
}

fn needed_words(needed: &Value) -> String {
    match needed["status"].as_str() {
        Some("needs_more_samples") => format!(
            "No class is established yet; about {} samples would establish {} if the share holds.",
            needed["samples_needed"],
            class_words(needed["class"].as_str().unwrap_or("")),
        ),
        Some("not_reachable_within_cap") => {
            "No class can be established within the 4096-sample cap at the shares seen.".into()
        }
        Some("observed_frequency_cannot_establish_a_class") => {
            "The samples split between classes, so more samples would not establish one.".into()
        }
        _ => "No class is established at this confidence.".into(),
    }
}

/// One bar per class with samples: the share, and the confidence interval as a line across it.
fn bars(ui: &mut egui::Ui, classes: &Value) {
    let width = (ui.available_width() - 260.).clamp(120., 520.);
    for class in CLASSES {
        let entry = &classes[class];
        let count = entry["count"].as_u64().unwrap_or(0);
        if count == 0 {
            continue;
        }
        let share = entry["probability"].as_f64().unwrap_or(0.);
        let (lower, upper) = (
            entry["lower"].as_f64().unwrap_or(share),
            entry["upper"].as_f64().unwrap_or(share),
        );
        ui.horizontal(|ui| {
            ui.allocate_ui_with_layout(
                egui::vec2(110., 18.),
                egui::Layout::left_to_right(egui::Align::Center),
                |ui| ui.label(class_words(class)),
            );
            let (rect, _) = ui.allocate_exact_size(egui::vec2(width, 14.), egui::Sense::hover());
            let colour = class_colour(ui, class);
            let painter = ui.painter();
            painter.rect_filled(rect, 3., ui.visuals().extreme_bg_color);
            let filled = egui::Rect::from_min_size(
                rect.min,
                egui::vec2(rect.width() * share as f32, rect.height()),
            );
            painter.rect_filled(filled, 3., colour.gamma_multiply(0.75));
            let x = |p: f64| rect.left() + rect.width() * p.clamp(0., 1.) as f32;
            let y = rect.center().y;
            let stroke = egui::Stroke::new(1.5, ui.visuals().text_color());
            painter.line_segment([egui::pos2(x(lower), y), egui::pos2(x(upper), y)], stroke);
            for p in [lower, upper] {
                painter.line_segment([egui::pos2(x(p), y - 5.), egui::pos2(x(p), y + 5.)], stroke);
            }
            ui.label(format!(
                "{count} samples, {:.0}% (95% interval {:.0} to {:.0}%)",
                share * 100.,
                lower * 100.,
                upper * 100.
            ));
        });
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_example_is_written_as_the_command_reads_it() {
        let state = SureState {
            component: Component {
                density_g_cm3: 7.8,
                ..Component::default()
            },
            samples: 72,
            seed: 20261029,
            ..SureState::default()
        };
        let spec = uncertainty_spec(&state, "/abs/problem.json", &[2, 3, 4], 1000.).unwrap();
        assert_eq!(spec["schema"], "actinv-waste-uncertainty-spec-1");
        assert_eq!(spec["nuclide_properties"], "from_decay_data");
        assert_eq!(spec["components"][0]["density_g_cm3"], 7.8);
        assert_eq!(
            spec["components"][0]["external_tritium"]["status"],
            "not_applicable"
        );
        assert_eq!(
            spec["sampling"],
            json!({"channels": {"cross_section_mf33": true, "decay_constants": true},
                   "covariance": {"path": COVARIANCE}, "samples": 72, "seed": 20261029})
        );
    }

    #[test]
    fn flux_alone_needs_no_covariance_and_nothing_varied_is_refused() {
        let mut state = SureState {
            cross_sections: false,
            decay_constants: false,
            component: Component {
                density_g_cm3: 7.8,
                ..Component::default()
            },
            ..SureState::default()
        };
        assert!(uncertainty_spec(&state, "p", &[1], 1.)
            .unwrap_err()
            .contains("Vary"));
        state.flux_pct = 10.;
        let spec = uncertainty_spec(&state, "p", &[1], 1.).unwrap();
        assert_eq!(spec["sampling"]["channels"], json!({"flux_rel_std": 0.1}));
        assert!(spec["sampling"].get("covariance").is_none());
        state.samples = 10;
        assert!(uncertainty_spec(&state, "p", &[1], 1.).is_err());
    }

    #[test]
    fn problems_the_sampler_cannot_take_are_named_and_the_uncertainty_block_is_dropped() {
        assert!(unsupported(&json!({"schedule": [{"dt": "1 d", "flux": 1e10}]})).is_none());
        assert!(
            unsupported(&json!({"schedule": [{"dt": "1 d", "power": {"MW": 1}}]}))
                .unwrap()
                .contains("Step 1")
        );
        assert!(unsupported(&json!({"options": {"gas": true}}))
            .unwrap()
            .contains("gas"));
        let p = sampled_problem(&json!({"title": "x", "uncertainty": {"responses": []}}));
        assert_eq!(p, json!({"title": "x"}));
    }
}
