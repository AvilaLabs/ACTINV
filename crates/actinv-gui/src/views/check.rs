//! Check & run: one line per setup step saying what the problem will do, or what is still missing
//! and where to fix it. The lines are plain reads of the document so they read like a sentence;
//! the solver's own validation (`Spec::from_json`) stays the final word and is shown as it is.
use crate::{model, views::Target};
use actinv_core::spec::{parse_duration, Spec};
use eframe::egui::{self, RichText};
use serde_json::Value;

/// One setup step as Check & run reports it.
#[derive(Clone, PartialEq, Debug)]
pub struct Line {
    pub target: Target,
    pub title: &'static str,
    /// What the step sets up, or what it still needs.
    pub text: String,
    pub ready: bool,
}

fn line(target: Target, title: &'static str, result: Result<String, String>) -> Line {
    let ready = result.is_ok();
    Line {
        target,
        title,
        text: result.unwrap_or_else(|missing| missing),
        ready,
    }
}

/// The four editing steps, in flow order.
pub fn assess(document: &Value) -> [Line; 4] {
    [
        line(Target::Material, "Material", material(document)),
        line(
            Target::Schedule,
            "Irradiation & cooling",
            schedule(document),
        ),
        line(Target::Spectrum, "Source & spectrum", source(document)),
        line(Target::Outputs, "What to compute", outputs(document)),
    ]
}

/// Whether the editing step behind `target` is ready; the sidebar ticks the steps that are.
pub fn ready(document: &Value, target: Target) -> bool {
    assess(document)
        .iter()
        .any(|line| line.target == target && line.ready)
}

fn material(document: &Value) -> Result<String, String> {
    let material = &document["material"];
    let mut parts: Vec<(&str, f64)> = material["composition"]
        .as_object()
        .map(|map| {
            map.iter()
                .map(|(k, v)| (k.as_str(), model::number(v)))
                .filter(|(_, v)| *v > 0.)
                .collect()
        })
        .unwrap_or_default();
    if parts.is_empty() {
        return Err("Add at least one element or isotope.".into());
    }
    parts.sort_by(|a, b| b.1.total_cmp(&a.1).then_with(|| a.0.cmp(b.0)));
    let unit = match material["basis"].as_str() {
        Some("wt_percent") => " wt%",
        Some("atom_fraction") => " atom fraction",
        Some("atoms_per_g") => " atoms/g",
        _ => "",
    };
    let mut text = parts
        .iter()
        .take(3)
        .map(|(name, value)| format!("{} {}{unit}", display_name(name), number(*value)))
        .collect::<Vec<_>>()
        .join(", ");
    if parts.len() > 3 {
        text.push_str(&format!(" and {} more", parts.len() - 3));
    }
    if let Some(mass) = material["mass_g"].as_f64() {
        text.push_str(&format!(" · {} g", number(mass)));
    }
    Ok(text)
}

/// `FE` reads as `Fe`; isotope names keep their spelling.
pub fn display_name(name: &str) -> String {
    if name.len() <= 2 && name.chars().all(|c| c.is_ascii_alphabetic()) {
        let mut chars = name.chars();
        let first = chars.next().map(|c| c.to_ascii_uppercase());
        first
            .into_iter()
            .chain(chars.map(|c| c.to_ascii_lowercase()))
            .collect()
    } else {
        name.to_owned()
    }
}

/// Up to four significant figures, without trailing zeros.
fn number(value: f64) -> String {
    if value != 0. && !(1e-3..1e5).contains(&value.abs()) {
        return model::sci(value);
    }
    let text = format!("{value:.4}");
    text.trim_end_matches('0').trim_end_matches('.').to_owned()
}

fn schedule(document: &Value) -> Result<String, String> {
    let steps = document["schedule"]
        .as_array()
        .map_or(&[][..], Vec::as_slice);
    if steps.is_empty() {
        return Err("Add an irradiation step.".into());
    }
    let mut irradiation = 0.;
    let mut cooling = 0.;
    let mut cooling_steps = 0;
    let mut irradiating_steps = 0;
    for step in steps {
        let dt = step["dt"]
            .as_str()
            .and_then(|text| parse_duration(text).ok())
            .or_else(|| step["dt"].as_f64())
            .unwrap_or(0.);
        if model::is_cooling(step) {
            cooling += dt;
            cooling_steps += 1;
        } else {
            irradiation += dt;
            irradiating_steps += 1;
        }
    }
    if irradiating_steps == 0 {
        return Err(
            "Every step is cooling. Give one step a flux, wall loading or power to irradiate."
                .into(),
        );
    }
    let mut text = format!("{} irradiation", model::readable_time(irradiation));
    if irradiating_steps > 1 {
        text.push_str(&format!(" in {irradiating_steps} steps"));
    }
    match cooling_steps {
        0 => text.push_str(", no cooling"),
        1 => text.push_str(&format!(
            ", then cooling for {}",
            model::readable_time(cooling)
        )),
        n => text.push_str(&format!(
            ", then {n} cooling steps to {} after shutdown",
            model::readable_time(cooling)
        )),
    }
    Ok(text)
}

fn source(document: &Value) -> Result<String, String> {
    let projectile = document["projectile"].as_str().unwrap_or("neutron");
    let spectrum = &document["spectrum"];
    let groups = spectrum["flux_per_group"]
        .as_array()
        .map_or(&[][..], Vec::as_slice);
    if !groups.iter().any(|v| model::number(v) > 0.) {
        return Err(format!(
            "The {projectile} spectrum has no positive group value. Paste or import one."
        ));
    }
    let structure = spectrum["structure"].as_str().unwrap_or("custom");
    let flux = match spectrum["total"].as_f64() {
        Some(total) => format!("normalized to {} /cm²/s", model::sci(total)),
        None => {
            let sum: f64 = groups.iter().map(model::number).sum();
            format!("{} /cm²/s in total", model::sci(sum))
        }
    };
    Ok(format!(
        "{}, {} groups ({structure}), {flux}",
        capital(projectile),
        groups.len()
    ))
}

fn capital(word: &str) -> String {
    let mut chars = word.chars();
    chars
        .next()
        .map(|c| c.to_uppercase().chain(chars).collect())
        .unwrap_or_default()
}

/// What the run will report. Inventory, activity and heat are in every result.
fn outputs(document: &Value) -> Result<String, String> {
    let listed: Vec<&str> = document["options"]["outputs"]
        .as_array()
        .map(|items| items.iter().filter_map(Value::as_str).collect())
        .unwrap_or_else(|| crate::views::outputs::DEFAULT_OUTPUTS.to_vec());
    let mut parts = vec!["activity", "decay heat", "inventory"];
    if listed.contains(&"photons") {
        parts.push("photons and dose");
    }
    if listed.contains(&"pathways") {
        parts.push("pathways");
    }
    if document["damage"].is_object() {
        parts.push("dpa");
    }
    if document["uncertainty"].is_object() {
        parts.push("uncertainty");
    }
    if document["summary"].is_object() {
        parts.push("step summary");
    }
    let last = parts.pop().unwrap_or_default();
    Ok(format!("{} and {last}", parts.join(", ")))
}

/// What the shell must do after the checklist was drawn.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum CheckAction {
    None,
    Open(Target),
}

/// Draw the checklist and, below it, the solver's own verdict on the specification.
pub fn show(ui: &mut egui::Ui, document: &Value) -> CheckAction {
    let mut action = CheckAction::None;
    let accent = crate::visuals::accent(ui);
    let lines = assess(document);
    // One row per step: the mark, the step and its sentence across the width, the button at the
    // right edge. Rows rather than a grid, so the sentence takes the room the frame has.
    ui.set_width(ui.available_width());
    let mark = ui.text_style_height(&egui::TextStyle::Body);
    for (i, line) in lines.iter().enumerate() {
        if i > 0 {
            ui.separator();
        }
        ui.horizontal(|ui| {
            if line.ready {
                crate::visuals::tick(ui, accent);
            } else {
                ui.add_sized(
                    [mark, mark],
                    egui::Label::new(
                        RichText::new("!")
                            .color(ui.visuals().error_fg_color)
                            .strong(),
                    ),
                );
            }
            ui.add_space(6.);
            let text_width = (ui.available_width() - 90.).max(160.);
            ui.allocate_ui_with_layout(
                egui::vec2(text_width, 0.),
                egui::Layout::top_down(egui::Align::Min),
                |ui| {
                    ui.set_width(text_width);
                    ui.strong(line.title);
                    if line.ready {
                        ui.label(&line.text);
                    } else {
                        ui.colored_label(ui.visuals().error_fg_color, &line.text);
                    }
                },
            );
            ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                if ui.button(if line.ready { "Edit" } else { "Fix" }).clicked() {
                    action = CheckAction::Open(line.target);
                }
            });
        });
    }
    if lines.iter().all(|line| line.ready) {
        if let Err(problem) = Spec::from_json(&document.to_string()) {
            ui.add_space(6.);
            ui.colored_label(
                ui.visuals().error_fg_color,
                format!("The specification is not valid yet: {problem}"),
            );
        }
    }
    action
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn the_example_reads_as_sentences() {
        let lines = assess(&crate::model::decode_problem(crate::model::EXAMPLE).unwrap());
        assert!(lines.iter().all(|line| line.ready), "{lines:?}");
        assert_eq!(lines[0].text, "Fe 100 wt% · 1 g");
        assert_eq!(
            lines[1].text,
            "5 min irradiation, then 20 cooling steps to 57 min after shutdown"
        );
        assert_eq!(
            lines[2].text,
            "Neutron, 709 groups (fispact-709), normalized to 1.12e10 /cm²/s"
        );
        assert_eq!(
            lines[3].text,
            "activity, decay heat, inventory, photons and dose and pathways"
        );
    }

    #[test]
    fn missing_parts_say_what_to_add_and_where() {
        let mut doc = crate::model::decode_problem(crate::model::EXAMPLE).unwrap();
        doc["material"]["composition"] = json!({});
        doc["schedule"] = json!([{"dt": "1 h", "flux": 0.0}]);
        doc["spectrum"]["flux_per_group"] = json!([0.0, 0.0]);
        let lines = assess(&doc);
        assert!(!lines[0].ready && lines[0].text.starts_with("Add at least one"));
        assert!(!lines[1].ready && lines[1].text.starts_with("Every step is cooling"));
        assert!(!lines[2].ready && lines[2].text.contains("neutron spectrum"));
        assert!(lines[3].ready);
        assert!(!ready(&doc, Target::Material));
        assert!(ready(&doc, Target::Outputs));
    }

    #[test]
    fn a_long_composition_names_the_largest_three() {
        let mut doc = crate::model::decode_problem(crate::model::EXAMPLE).unwrap();
        doc["material"]["composition"] =
            json!({"FE": 70.0, "CR": 18.0, "NI": 10.0, "Co59": 0.05, "MN": 1.95});
        assert_eq!(
            assess(&doc)[0].text,
            "Fe 70 wt%, Cr 18 wt%, Ni 10 wt% and 2 more · 1 g"
        );
    }
}
