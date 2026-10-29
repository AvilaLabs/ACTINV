//! The review on Check & run: every setting the problem changes from its default, as a sentence,
//! with the page that sets it. Changes are found by comparing the solver's own reading of the
//! problem with its reading of a problem that keeps only the material, schedule, source and data,
//! so a setting no page knows about is still listed, by its key.
use crate::{model, views::Target};
use actinv_core::spec::Spec;
use eframe::egui::{self, RichText};
use serde_json::{Map, Value};

/// One setting that differs from its default.
#[derive(Clone, PartialEq, Debug)]
pub struct Change {
    /// The dotted key, such as `options.temperature_K`.
    pub path: String,
    /// The part of the problem it belongs to, as the review heads it.
    pub section: &'static str,
    pub text: String,
    /// The page that sets it.
    pub target: Target,
}

/// Keys the review leaves to the problem's own lines: what the material, schedule, source, data
/// and outputs lines already say, and the title.
const COVERED: [&str; 8] = [
    "spec",
    "title",
    "material",
    "schedule",
    "spectrum",
    "projectile",
    "library",
    "decay",
];

/// Every setting of `document` that differs from its default, in document order. `Err` while the
/// solver cannot read the problem; its own message says why.
pub fn changes(document: &Value) -> Result<Vec<Change>, String> {
    let read = |doc: &Value| -> Result<Value, String> {
        let spec = Spec::from_json(&doc.to_string())?;
        serde_json::to_value(&spec).map_err(|e| e.to_string())
    };
    let actual = read(document)?;
    let mut bare = Map::new();
    for key in COVERED {
        if let Some(value) = document.get(key) {
            bare.insert(key.into(), value.clone());
        }
    }
    let baseline = read(&Value::Object(bare))?;
    let mut found = Vec::new();
    if let Value::Object(map) = &actual {
        for (key, value) in map {
            if COVERED.contains(&key.as_str()) {
                continue;
            }
            section_changes(key, value, &baseline[key.as_str()], &mut found);
        }
    }
    Ok(found)
}

/// How many changes sit under any of `prefixes` (dotted keys); for the "· N changed" marks.
pub fn count(changes: &[Change], prefixes: &[&str]) -> usize {
    changes
        .iter()
        .filter(|c| {
            prefixes
                .iter()
                .any(|p| c.path == *p || c.path.starts_with(&format!("{p}.")))
        })
        .count()
}

/// The "Settings you changed" card: one row per change with the page that sets it, then the line
/// that says everything else is at its default. Returns the page to open when Edit was clicked.
pub fn show(ui: &mut egui::Ui, document: &Value) -> Option<Target> {
    let mut open = None;
    ui.set_width(ui.available_width());
    ui.heading("Settings you changed");
    let found = match changes(document) {
        Ok(found) => found,
        Err(_) => {
            // the problem card above shows the solver's reason
            ui.weak("Settings are listed here once the problem reads as valid.");
            return None;
        }
    };
    if found.is_empty() {
        ui.label("None. Every setting beyond the problem above is at its default.");
        return None;
    }
    let mut section = "";
    for (i, change) in found.iter().enumerate() {
        if i > 0 {
            ui.separator();
        }
        ui.horizontal(|ui| {
            let text_width = (ui.available_width() - 90.).max(160.);
            ui.allocate_ui_with_layout(
                egui::vec2(text_width, 0.),
                egui::Layout::top_down(egui::Align::Min),
                |ui| {
                    ui.set_width(text_width);
                    if change.section != section {
                        ui.strong(change.section);
                    }
                    ui.add(egui::Label::new(&change.text).wrap());
                },
            );
            ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                if ui
                    .button("Edit")
                    .on_hover_text(format!("Open {}", target_name(change.target)))
                    .clicked()
                {
                    open = Some(change.target);
                }
            });
        });
        section = change.section;
    }
    ui.add_space(6.);
    ui.label(RichText::new("All other settings are at their defaults.").weak());
    open
}

fn target_name(target: Target) -> &'static str {
    match target {
        Target::Material => "Material",
        Target::Schedule => "Irradiation & cooling",
        Target::Spectrum => "Source & spectrum",
        Target::Outputs => "What to compute",
        Target::CheckRun => "Check & run",
        Target::Data => "Data",
        Target::Advanced => "Advanced JSON",
    }
}

fn section_changes(key: &str, value: &Value, default: &Value, found: &mut Vec<Change>) {
    if value == default || value.is_null() {
        return;
    }
    let section = section_name(key);
    // A whole optional block switched on reads as one sentence; its parts are on its page.
    if default.is_null() {
        found.push(Change {
            path: key.into(),
            section,
            text: block_sentence(key, value),
            target: target_for(key),
        });
        return;
    }
    let mut leaves = Vec::new();
    diff(key, value, default, &mut leaves);
    for (path, value, default) in leaves {
        if path == "options.outputs" {
            // the "What to compute" line says what the run reports
            continue;
        }
        found.push(Change {
            text: leaf_sentence(&path, &value, &default),
            section,
            target: target_for(&path),
            path,
        });
    }
}

/// Differing leaves under `path`. Arrays compare whole: a list reads as one setting.
fn diff(path: &str, value: &Value, default: &Value, out: &mut Vec<(String, Value, Value)>) {
    match (value, default) {
        (Value::Object(a), Value::Object(b)) => {
            for (key, v) in a {
                let d = b.get(key).unwrap_or(&Value::Null);
                if v != d && !v.is_null() {
                    diff(&format!("{path}.{key}"), v, d, out);
                }
            }
        }
        _ if value != default => out.push((path.into(), value.clone(), default.clone())),
        _ => {}
    }
}

fn section_name(key: &str) -> &'static str {
    match key {
        "options" => "Solver",
        "photon" => "Photons & dose",
        "radiological" => "Regulatory",
        "uncertainty" => "Uncertainty",
        "damage" => "Damage",
        "self_shielding" => "Self-shielding",
        "summary" => "Step summary",
        "pathways" => "Pathways",
        "kerma" => "KERMA",
        "fission_yields" => "Fission yields",
        "overrides" => "Overrides",
        _ => "Other",
    }
}

/// The page that sets a key. What no page edits opens the full-document editor.
fn target_for(path: &str) -> Target {
    let top = path.split('.').next().unwrap_or(path);
    match top {
        "photon" | "radiological" | "uncertainty" | "damage" | "self_shielding" | "summary"
        | "pathways" => Target::Outputs,
        "fission_yields" => Target::Data,
        "options" => match path {
            "options.mode" | "options.temperature_K" | "options.gas" => Target::Outputs,
            _ => Target::Advanced,
        },
        _ => Target::Advanced,
    }
}

fn block_sentence(key: &str, value: &Value) -> String {
    match key {
        "uncertainty" => {
            let channels = list(&value["channels"]);
            let mut text = "Nuclear-data uncertainty on".to_owned();
            if !channels.is_empty() {
                text.push_str(&format!(": {}", channels.replace('_', " ")));
            }
            if let Some(n) = value["samples"].as_u64() {
                text.push_str(&format!(", {n} samples"));
            }
            text
        }
        "damage" => "Damage (dpa) computed".into(),
        "self_shielding" => "Self-shielding corrections applied".into(),
        "radiological" => "Regulatory quantities (clearance, waste) computed".into(),
        "summary" => "A summary of each step is written".into(),
        "pathways" => "Production pathways ranked".into(),
        "kerma" => "KERMA computed".into(),
        "overrides" => match value.as_array().map(Vec::len) {
            Some(1) => "1 data override".into(),
            Some(n) => format!("{n} data overrides"),
            None => "Data overrides".into(),
        },
        _ => format!("{} set", humanize(key)),
    }
}

fn leaf_sentence(path: &str, value: &Value, default: &Value) -> String {
    let key = path.rsplit('.').next().unwrap_or(path);
    let name = match path {
        "options.mode" => "Solver mode".to_owned(),
        "options.temperature_K" => "Temperature (K)".to_owned(),
        "options.bmin_atoms_per_g" => "Smallest inventory kept (atoms/g)".to_owned(),
        "options.cram_order" => "Decay approximation order".to_owned(),
        "options.prune" => "Chain pruning".to_owned(),
        "options.gas" => "Gas production".to_owned(),
        "options.rate_scale" => "Reaction rates scaled".to_owned(),
        "options.decay_scale" => "Decay constants scaled".to_owned(),
        "options.yield_scale" => "Fission yields scaled".to_owned(),
        _ => humanize(key),
    };
    match (value, default) {
        (Value::Bool(on), _) => format!("{name} {}", if *on { "on" } else { "off" }),
        (_, Value::Null) => format!("{name}: {}", phrase(value)),
        _ => format!("{name}: {} (default {})", phrase(value), phrase(default)),
    }
}

/// `temperature_K` reads "Temperature (K)", `distance_m` "Distance (m)".
fn humanize(key: &str) -> String {
    const UNITS: [&str; 12] = [
        "K", "m", "s", "g", "eV", "MeV", "W", "Bq", "cm", "Sv", "h", "kg",
    ];
    let mut words: Vec<&str> = key.split('_').filter(|w| !w.is_empty()).collect();
    let unit = match words.last() {
        Some(last) if words.len() > 1 && UNITS.contains(last) => words.pop(),
        _ => None,
    };
    let mut text = words.join(" ");
    if let Some(first) = text.get(..1) {
        text = first.to_uppercase() + &text[1..];
    }
    match unit {
        Some(unit) => format!("{text} ({unit})"),
        None => text,
    }
}

fn list(value: &Value) -> String {
    value
        .as_array()
        .map(|items| {
            items
                .iter()
                .filter_map(Value::as_str)
                .collect::<Vec<_>>()
                .join(", ")
        })
        .unwrap_or_default()
}

/// A value as a short phrase: numbers to four figures, lists joined, objects as their entries.
fn phrase(value: &Value) -> String {
    let text = match value {
        Value::Number(n) => n.as_f64().map_or_else(|| n.to_string(), number),
        Value::String(s) => s.clone(),
        Value::Bool(b) => if *b { "on" } else { "off" }.into(),
        Value::Array(items) if items.iter().all(Value::is_string) => list(value),
        Value::Array(items) => format!("{} values", items.len()),
        Value::Object(map) => map
            .iter()
            .map(|(k, v)| format!("{k} {}", phrase(v)))
            .collect::<Vec<_>>()
            .join(", "),
        Value::Null => "none".into(),
    };
    if text.chars().count() > 80 {
        text.chars().take(77).collect::<String>() + "…"
    } else {
        text
    }
}

fn number(value: f64) -> String {
    if value != 0. && !(1e-3..1e5).contains(&value.abs()) {
        return model::sci(value);
    }
    let text = format!("{value:.4}");
    text.trim_end_matches('0').trim_end_matches('.').to_owned()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn example() -> Value {
        model::decode_problem(model::EXAMPLE).unwrap()
    }

    #[test]
    fn the_example_changes_nothing_beyond_its_own_lines() {
        // the example's outputs list is the What to compute line's business
        assert_eq!(changes(&example()).unwrap(), []);
    }

    #[test]
    fn a_changed_option_reads_with_its_default_and_opens_its_page() {
        let mut doc = example();
        doc["options"]["temperature_K"] = json!(600.0);
        doc["options"]["mode"] = json!("trace");
        let found = changes(&doc).unwrap();
        let texts: Vec<_> = found.iter().map(|c| c.text.as_str()).collect();
        assert!(
            texts.contains(&"Temperature (K): 600 (default 293.6)"),
            "{texts:?}"
        );
        assert!(
            texts.contains(&"Solver mode: trace (default auto)"),
            "{texts:?}"
        );
        assert!(found
            .iter()
            .all(|c| c.target == Target::Outputs && c.section == "Solver"));
        assert_eq!(count(&found, &["options"]), 2);
        assert_eq!(count(&found, &["photon"]), 0);
    }

    #[test]
    fn a_value_equal_to_the_default_is_not_a_change() {
        let mut doc = example();
        doc["options"]["temperature_K"] = json!(293.6);
        doc["options"]["mode"] = json!("auto");
        assert_eq!(changes(&doc).unwrap(), []);
    }

    #[test]
    fn a_key_no_page_knows_is_listed_by_name_and_opens_the_editor() {
        let mut doc = example();
        doc["options"]["cram_order"] = json!(48);
        let found = changes(&doc).unwrap();
        assert_eq!(found.len(), 1);
        assert_eq!(found[0].text, "Decay approximation order: 48 (default 16)");
        assert_eq!(found[0].target, Target::Advanced);
    }

    #[test]
    fn an_invalid_problem_is_refused_in_the_solvers_words() {
        let mut doc = example();
        doc["options"]["mode"] = json!("sideways");
        assert!(changes(&doc).is_err());
    }

    #[test]
    fn keys_read_as_words_with_their_unit() {
        assert_eq!(humanize("temperature_K"), "Temperature (K)");
        assert_eq!(humanize("distance_m"), "Distance (m)");
        assert_eq!(humanize("point_source"), "Point source");
        assert_eq!(humanize("m"), "M");
    }
}
