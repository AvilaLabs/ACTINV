//! Waste class: the nominal 10 CFR 61.55 classes of an `actinv-waste-result-1`, as the result
//! states them. The view reads the document and computes nothing: the class, the governing table
//! and column, the sums and the binding nuclides are the evaluator's own fields. The one derived
//! line is the earliest selected step within each class, found by scanning the targets. The
//! desktop shows the result of its own evaluation here and the browser shows any file it is given.
use crate::visuals::accent;
use eframe::egui::{self, Color32, RichText};
use egui_extras::{Column, TableBuilder};
use serde_json::Value;

const RESULT_SCHEMA: &str = "actinv-waste-result-1";
#[cfg_attr(not(target_arch = "wasm32"), allow(dead_code))]
const SPEC_SCHEMA: &str = "actinv-waste-spec-1";
/// Binding nuclides listed in a table cell before "+ n more".
const LISTED_NUCLIDES: usize = 6;

/// A waste spec to fill in. It lists no nuclide properties, and its mass and volume are
/// placeholders to replace with the component's own.
#[cfg_attr(not(target_arch = "wasm32"), allow(dead_code))]
pub const SPEC_SKELETON: &str = r#"{
  "schema": "actinv-waste-spec-1",
  "rules": "us-nrc-10cfr61.55-v1",
  "input": "result.json",
  "targets": [1],
  "nuclide_properties": {},
  "components": [
    {
      "id": "component",
      "mass_g": 1.0,
      "displaced_volume_cm3": 1.0,
      "waste_type": "general",
      "external_tritium": {"status": "not_applicable"}
    }
  ]
}
"#;

/// Which kind of file a waste result was found in.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Source {
    /// An ordinary `actinv-waste-result-1` document.
    Waste,
    /// An intrusion-screen result; its `classification` member is the waste result.
    IntrusionScreen,
    /// A twin result; its `waste_classification` member is the waste result.
    Twin,
}

impl Source {
    fn label(self) -> &'static str {
        match self {
            Source::Waste => "Waste classification",
            Source::IntrusionScreen => "Intrusion-screen result: its Part 61 classification",
            Source::Twin => "Facility twin result: its waste classification",
        }
    }
}

/// One entry of `evaluation.binding_constraints`.
#[derive(Debug, PartialEq)]
pub struct Constraint {
    table: u64,
    column: Option<u64>,
    target_class: String,
    normalized_sum: f64,
    normalized_margin: f64,
    contributors: Vec<String>,
}

/// One selected step of one component.
#[derive(Debug, PartialEq)]
pub struct StepClass {
    step: u64,
    t_s: f64,
    class: String,
    status: String,
    coverage: String,
    calculated_only_class: Option<String>,
    unknown_reason: Option<String>,
    binding: Vec<Constraint>,
}

#[derive(Debug, PartialEq)]
pub struct Component {
    id: String,
    mass_g: Option<f64>,
    volume_cm3: Option<f64>,
    waste_type: String,
    external_h3: String,
    status: String,
    steps: Vec<StepClass>,
}

/// A parsed waste result: what `show` draws.
#[derive(Debug, PartialEq)]
pub struct WasteView {
    source: Source,
    rules: String,
    input: String,
    status: String,
    components: Vec<Component>,
}

fn text(value: &Value, key: &str, whose: &str) -> Result<String, String> {
    value[key]
        .as_str()
        .map(str::to_owned)
        .ok_or_else(|| format!("{whose} has no `{key}` text"))
}

/// Text that is shown but not needed: a dash when the document lacks it.
fn text_or_dash(value: &Value, key: &str) -> String {
    value[key].as_str().unwrap_or("—").to_owned()
}

fn strings(value: &Value) -> Vec<String> {
    value
        .as_array()
        .map(|items| {
            items
                .iter()
                .filter_map(|v| v.as_str().map(str::to_owned))
                .collect()
        })
        .unwrap_or_default()
}

/// The waste result inside `value`: the document itself, or the member an intrusion-screen or
/// twin result carries.
fn locate(value: &Value) -> Result<(Source, &Value), String> {
    if !value.is_object() {
        return Err("This file is not a JSON object, so it is not a waste result.".into());
    }
    [
        (Source::Waste, value),
        (Source::IntrusionScreen, &value["classification"]),
        (Source::Twin, &value["waste_classification"]),
    ]
    .into_iter()
    .find(|(_, doc)| doc["schema"].as_str() == Some(RESULT_SCHEMA))
    .ok_or_else(|| {
        let found = match value["schema"].as_str() {
            Some(schema) => format!("This file is `{schema}`, not a waste result."),
            None => "This file has no waste result in it.".to_owned(),
        };
        format!(
            "{found} Open an {RESULT_SCHEMA} document, an intrusion-screen result (a `classification` member) or a twin result (a `waste_classification` member)."
        )
    })
}

fn parse_constraint(constraint: &Value, whose: &str) -> Result<Constraint, String> {
    let number = |key: &str| {
        constraint[key]
            .as_f64()
            .ok_or_else(|| format!("{whose} has a binding constraint without a numeric `{key}`"))
    };
    Ok(Constraint {
        table: constraint["table"]
            .as_u64()
            .ok_or_else(|| format!("{whose} has a binding constraint without a `table`"))?,
        column: constraint["column"].as_u64(),
        target_class: text(constraint, "target_class", whose)?,
        normalized_sum: number("normalized_sum")?,
        normalized_margin: number("normalized_margin")?,
        contributors: strings(&constraint["contributors"]),
    })
}

fn parse_step(target: &Value, whose: &str) -> Result<StepClass, String> {
    let step = target["step"]
        .as_u64()
        .ok_or_else(|| format!("{whose} has a target without a step number"))?;
    let whose = format!("{whose}, step {step}");
    let unknown = strings(&target["unknown_reason"]);
    let unknown_reason = match &target["unknown_reason"] {
        Value::String(reason) => Some(reason.clone()),
        Value::Array(_) if !unknown.is_empty() => {
            Some(format!("no nuclide properties for {}", unknown.join(", ")))
        }
        _ => None,
    };
    let binding = target["evaluation"]["binding_constraints"]
        .as_array()
        .map_or(&[][..], Vec::as_slice)
        .iter()
        .map(|constraint| parse_constraint(constraint, &whose))
        .collect::<Result<Vec<_>, _>>()?;
    Ok(StepClass {
        step,
        t_s: target["t_s"]
            .as_f64()
            .ok_or_else(|| format!("{whose} has no numeric `t_s`"))?,
        class: text(target, "class", &whose)?,
        status: text(target, "status", &whose)?,
        coverage: text_or_dash(target, "coverage"),
        calculated_only_class: target["calculated_only_class"].as_str().map(str::to_owned),
        unknown_reason,
        binding,
    })
}

fn parse_component(component: &Value, index: usize) -> Result<Component, String> {
    let whose = format!("Component {}", index + 1);
    let steps = component["targets"]
        .as_array()
        .filter(|targets| !targets.is_empty())
        .ok_or_else(|| format!("{whose} has no targets"))?
        .iter()
        .map(|target| parse_step(target, &whose))
        .collect::<Result<Vec<_>, _>>()?;
    Ok(Component {
        id: text(component, "id", &whose)?,
        mass_g: component["mass_g"].as_f64(),
        volume_cm3: component["displaced_volume_cm3"].as_f64(),
        waste_type: text_or_dash(component, "waste_type"),
        external_h3: component["external_tritium"]["status"]
            .as_str()
            .unwrap_or("—")
            .to_owned(),
        status: text_or_dash(component, "status"),
        steps,
    })
}

/// Read a waste result, an intrusion-screen result or a twin result. Anything else is an error
/// message for the page, never a panic.
pub fn parse(value: &Value) -> Result<WasteView, String> {
    let (source, doc) = locate(value)?;
    let components = doc["components"]
        .as_array()
        .filter(|components| !components.is_empty())
        .ok_or("The waste result has no components")?
        .iter()
        .enumerate()
        .map(|(index, component)| parse_component(component, index))
        .collect::<Result<Vec<_>, _>>()?;
    Ok(WasteView {
        source,
        rules: text_or_dash(doc, "rules"),
        input: text_or_dash(doc, "input"),
        status: text_or_dash(doc, "status"),
        components,
    })
}

/// Read the text of a file opened as a waste result, as the browser's Waste class page does.
#[cfg_attr(not(target_arch = "wasm32"), allow(dead_code))]
pub fn read_result(text: &str) -> Result<WasteView, String> {
    let value: Value =
        serde_json::from_str(text).map_err(|error| format!("Invalid JSON: {error}"))?;
    parse(&value)
}

impl WasteView {
    /// The classes the page shows, per component and selected step, as JSON (`web/smoke.mjs`).
    #[cfg_attr(not(target_arch = "wasm32"), allow(dead_code))]
    pub fn classes(&self) -> Value {
        let source = match self.source {
            Source::Waste => "waste",
            Source::IntrusionScreen => "intrusion_screen",
            Source::Twin => "twin",
        };
        let components: Vec<Value> = self
            .components
            .iter()
            .map(|component| {
                let steps: Vec<Value> = component
                    .steps
                    .iter()
                    .map(|step| serde_json::json!({"step": step.step, "class": step.class}))
                    .collect();
                serde_json::json!({"id": component.id, "steps": steps})
            })
            .collect();
        serde_json::json!({"source": source, "components": components})
    }
}

/// What a file the browser was given holds, when it is a waste document.
#[cfg_attr(not(target_arch = "wasm32"), allow(dead_code))]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Detected {
    Spec,
    Result,
}

#[cfg_attr(not(target_arch = "wasm32"), allow(dead_code))]
pub fn detect(value: &Value) -> Option<Detected> {
    if value["schema"].as_str() == Some(SPEC_SCHEMA) {
        return Some(Detected::Spec);
    }
    locate(value).ok().map(|_| Detected::Result)
}

/// The structure of a waste spec the browser can check without evaluating it: the schema and the
/// members the evaluator requires. The evaluator itself does the rest when ACTINV runs the spec.
#[cfg_attr(not(target_arch = "wasm32"), allow(dead_code))]
pub fn check_spec(text: &str) -> Result<(), String> {
    let value: Value =
        serde_json::from_str(text).map_err(|error| format!("Invalid JSON: {error}"))?;
    match value["schema"].as_str() {
        Some(SPEC_SCHEMA) => {}
        Some(other) => {
            return Err(format!(
                "This file is `{other}`, not an {SPEC_SCHEMA} waste spec."
            ))
        }
        None => {
            return Err(format!(
                "The document has no `schema`; a waste spec is `{SPEC_SCHEMA}`."
            ))
        }
    }
    for key in ["rules", "input"] {
        if value[key].as_str().is_none_or(str::is_empty) {
            return Err(format!("The waste spec needs a `{key}` text."));
        }
    }
    let steps = value["targets"].as_array();
    if !steps.is_some_and(|steps| {
        !steps.is_empty() && steps.iter().all(|s| s.as_u64().is_some_and(|n| n > 0))
    }) {
        return Err("`targets` must list one-based step numbers (positive integers).".into());
    }
    if !value["nuclide_properties"].is_object() {
        return Err("`nuclide_properties` must be an object keyed by nuclide.".into());
    }
    let components = value["components"]
        .as_array()
        .filter(|components| !components.is_empty())
        .ok_or("`components` must list at least one component.")?;
    for (index, component) in components.iter().enumerate() {
        let whose = format!("Component {}", index + 1);
        if component["id"].as_str().is_none_or(str::is_empty) {
            return Err(format!("{whose} needs an `id`."));
        }
        if !component["mass_g"].as_f64().is_some_and(|m| m > 0.) {
            return Err(format!("{whose} needs a positive `mass_g`."));
        }
        if !matches!(
            component["waste_type"].as_str(),
            Some("general" | "activated_metal")
        ) {
            return Err(format!(
                "{whose} needs `waste_type` general or activated_metal."
            ));
        }
        if component["external_tritium"]["status"].as_str().is_none() {
            return Err(format!("{whose} needs an `external_tritium` status."));
        }
    }
    Ok(())
}

/// Position of a class on the A, B, C scale; `None` for above Class C and for unknown.
fn rank(class: &str) -> Option<u8> {
    match class {
        "A" => Some(0),
        "B" => Some(1),
        "C" => Some(2),
        _ => None,
    }
}

fn class_label(class: &str) -> String {
    match class {
        "A" | "B" | "C" => format!("Class {class}"),
        "above_class_c" => "Above Class C".into(),
        "unknown" => "Unknown".into(),
        other => other.to_owned(),
    }
}

/// The earliest selected step whose class is `worst` or better, by time. Unknown steps and steps
/// above Class C never count.
fn earliest_within<'a>(steps: &'a [StepClass], worst: &str) -> Option<&'a StepClass> {
    let limit = rank(worst)?;
    steps
        .iter()
        .filter(|s| rank(&s.class).is_some_and(|r| r <= limit))
        .min_by(|a, b| a.t_s.total_cmp(&b.t_s).then(a.step.cmp(&b.step)))
}

impl StepClass {
    fn governing(&self) -> Option<&Constraint> {
        self.binding.first()
    }

    /// Every binding nuclide once, in the order the constraints list them.
    fn binding_nuclides(&self) -> Vec<&str> {
        let mut seen = Vec::new();
        for name in self.binding.iter().flat_map(|c| &c.contributors) {
            if !seen.contains(&name.as_str()) {
                seen.push(name.as_str());
            }
        }
        seen
    }
}

fn governing_label(constraint: &Constraint, tied: usize) -> String {
    let place = match constraint.column {
        Some(column) => format!("Table {}, column {column}", constraint.table),
        None => format!("Table {}", constraint.table),
    };
    let ties = if tied > 0 {
        format!(" (+{tied} tied)")
    } else {
        String::new()
    };
    format!(
        "{place} for {}{ties}",
        class_label(&constraint.target_class)
    )
}

fn fraction(value: f64) -> String {
    if value != 0. && value.abs() < 1e-3 {
        format!("{value:.4e}")
    } else {
        format!("{value:.4}")
    }
}

fn time(t_s: f64) -> String {
    crate::model::readable_time(t_s)
}

fn nuclide_list(names: &[&str]) -> String {
    let shown = names
        .iter()
        .take(LISTED_NUCLIDES)
        .copied()
        .collect::<Vec<_>>()
        .join(", ");
    match names.len().saturating_sub(LISTED_NUCLIDES) {
        0 => shown,
        more => format!("{shown} + {more} more"),
    }
}

/// Class colours come from the palette the other pages already use: the accent, the warning and
/// error colours, and the weak text colour.
fn class_colour(ui: &egui::Ui, class: &str) -> Color32 {
    match class {
        "A" => accent(ui),
        "B" => ui.visuals().warn_fg_color,
        "C" | "above_class_c" => ui.visuals().error_fg_color,
        _ => ui.visuals().weak_text_color(),
    }
}

/// The waste result on screen: one card per component.
pub fn show(ui: &mut egui::Ui, view: &WasteView) {
    ui.group(|ui| {
        ui.label(RichText::new(view.source.label()).strong());
        ui.label(format!(
            "Rules {} · status {} · input {}",
            view.rules, view.status, view.input
        ));
    });
    for (index, component) in view.components.iter().enumerate() {
        ui.add_space(8.);
        component_card(ui, index, component);
    }
    ui.add_space(8.);
    ui.small("Classification is nominal screening of the stated inventory against the selected rules. It is not disposal acceptance, intrusion dose or legal compliance.");
}

fn component_card(ui: &mut egui::Ui, index: usize, component: &Component) {
    ui.group(|ui| {
        ui.label(RichText::new(&component.id).strong());
        let mut facts = Vec::new();
        if let Some(mass) = component.mass_g {
            facts.push(format!("mass {mass} g"));
        }
        if let Some(volume) = component.volume_cm3 {
            facts.push(format!("displaced volume {volume} cm³"));
        }
        facts.push(format!("waste type {}", component.waste_type));
        facts.push(format!("external H-3 {}", component.external_h3));
        facts.push(format!("component status {}", component.status));
        ui.label(facts.join(" · "));
        strip(ui, &component.steps);
        earliest_lines(ui, &component.steps);
        step_table(ui, index, &component.steps);
        unknown_lines(ui, &component.steps);
    });
}

/// One cell per selected step, in the order the result lists them, coloured by class.
fn strip(ui: &mut egui::Ui, steps: &[StepClass]) {
    ui.label("Class by cooling time");
    ui.horizontal_wrapped(|ui| {
        for step in steps {
            let colour = class_colour(ui, &step.class);
            egui::Frame::group(ui.style())
                .fill(colour.gamma_multiply(0.2))
                .show(ui, |ui| {
                    ui.vertical(|ui| {
                        ui.label(
                            RichText::new(class_label(&step.class))
                                .strong()
                                .color(colour),
                        );
                        ui.small(format!("step {}", step.step));
                        ui.small(time(step.t_s));
                    });
                });
        }
    });
}

fn earliest_lines(ui: &mut egui::Ui, steps: &[StepClass]) {
    for worst in ["A", "B", "C"] {
        let line = match earliest_within(steps, worst) {
            Some(step) => format!("step {} (t = {})", step.step, time(step.t_s)),
            None => "none of the selected steps".into(),
        };
        ui.label(format!(
            "Earliest selected step within {}: {line}",
            class_label(worst)
        ));
    }
    ui.small("A step is within Class B when its class is A or B, and within Class C when it is A, B or C.");
    let unknown = steps.iter().filter(|s| s.class == "unknown").count();
    if unknown > 0 {
        ui.small(format!(
            "{unknown} selected step(s) have an unknown class and are not counted."
        ));
    }
}

fn step_table(ui: &mut egui::Ui, index: usize, steps: &[StepClass]) {
    TableBuilder::new(ui)
        .id_salt(("waste-steps", index))
        .striped(true)
        .resizable(true)
        .column(Column::initial(44.).at_least(36.))
        .column(Column::initial(96.).at_least(70.))
        .column(Column::initial(110.).at_least(80.))
        .column(Column::initial(70.).at_least(50.))
        .column(Column::initial(76.).at_least(50.))
        .column(Column::initial(190.).at_least(90.))
        .column(Column::initial(70.).at_least(50.))
        .column(Column::initial(70.).at_least(50.))
        .column(Column::remainder().at_least(80.))
        .max_scroll_height(320.)
        .header(24., |mut header| {
            for label in [
                "Step",
                "t",
                "Class",
                "Status",
                "Coverage",
                "Governing constraint",
                "Sum",
                "Margin",
                "Binding nuclides",
            ] {
                header.col(|ui| {
                    ui.strong(label);
                });
            }
        })
        .body(|body| {
            body.rows(24., steps.len(), |mut row| {
                let step = &steps[row.index()];
                row.col(|ui| {
                    ui.monospace(step.step.to_string());
                });
                row.col(|ui| {
                    ui.monospace(time(step.t_s));
                });
                row.col(|ui| {
                    let colour = class_colour(ui, &step.class);
                    ui.label(
                        RichText::new(class_label(&step.class))
                            .strong()
                            .color(colour),
                    );
                });
                row.col(|ui| {
                    ui.label(&step.status);
                });
                row.col(|ui| {
                    ui.label(&step.coverage);
                });
                let governing = step.governing();
                row.col(|ui| {
                    ui.label(governing.map_or("—".to_owned(), |c| {
                        governing_label(c, step.binding.len() - 1)
                    }));
                });
                row.col(|ui| {
                    ui.monospace(governing.map_or("—".to_owned(), |c| fraction(c.normalized_sum)));
                });
                row.col(|ui| {
                    ui.monospace(
                        governing.map_or("—".to_owned(), |c| fraction(c.normalized_margin)),
                    );
                });
                row.col(|ui| {
                    ui.label(nuclide_list(&step.binding_nuclides()));
                });
            });
        });
}

/// Why a step has no class, and the class the declared inventory alone would have.
fn unknown_lines(ui: &mut egui::Ui, steps: &[StepClass]) {
    for step in steps {
        let Some(reason) = &step.unknown_reason else {
            continue;
        };
        let alone = step
            .calculated_only_class
            .as_deref()
            .map_or_else(String::new, |class| {
                format!(" The declared inventory alone is {}.", class_label(class))
            });
        ui.small(format!(
            "Step {}: {} ({reason}).{alone}",
            step.step,
            class_label(&step.class).to_lowercase()
        ));
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn constraint(table: u64, column: Option<u64>, class: &str, sum: f64, who: &[&str]) -> Value {
        json!({
            "target_class": class, "table": table, "column": column,
            "source_sum_fraction": sum, "normalized_sum": sum, "normalized_margin": 1.0 - sum,
            "strict": true, "passes": true, "contributor_count": who.len(), "contributors": who,
        })
    }

    fn target(step: u64, t_s: f64, class: &str, binding: Vec<Value>) -> Value {
        let unknown = class == "unknown";
        json!({
            "step": step, "t_s": t_s,
            "inventory_activity_bq": {}, "external_tritium_activity_bq": 0.0,
            "calculated_only_class": if unknown { json!("B") } else { json!(class) },
            "class": class,
            "coverage": if unknown { "incomplete" } else { "complete" },
            "external_tritium_status": "not_applicable",
            "status": if unknown { "unknown" } else { "nominal" },
            "unknown_reason": if unknown { json!(["Ni59"]) } else { Value::Null },
            "evaluation": {"class": class, "binding_constraints": binding, "constraints": []},
        })
    }

    fn waste_result(targets: Vec<Value>) -> Value {
        json!({
            "schema": "actinv-waste-result-1", "input": "component-result.json",
            "rules": "us-nrc-10cfr61.55-v1", "status": "nominal",
            "targets": targets.iter().map(|t| t["step"].clone()).collect::<Vec<_>>(),
            "components": [{
                "id": "synthetic-component", "mass_g": 1000.0, "displaced_volume_cm3": 128.2,
                "waste_type": "activated_metal", "external_tritium": {"status": "not_applicable"},
                "status": "nominal", "targets": targets,
            }],
        })
    }

    fn cooling_series() -> Value {
        waste_result(vec![
            target(
                1,
                0.0,
                "C",
                vec![constraint(2, Some(3), "C", 0.9, &["Nb94", "C14"])],
            ),
            target(2, 1.0e6, "B", vec![constraint(1, None, "B", 0.4, &["C14"])]),
            target(
                3,
                1.0e9,
                "A",
                vec![constraint(2, Some(1), "A", 0.01, &["Nb94"])],
            ),
            target(
                4,
                2.0e9,
                "unknown",
                vec![constraint(2, Some(1), "B", 0.5, &["Ni59"])],
            ),
        ])
    }

    #[test]
    fn a_waste_result_is_read_field_by_field() {
        let view = parse(&cooling_series()).unwrap();
        assert_eq!(view.source, Source::Waste);
        assert_eq!(view.rules, "us-nrc-10cfr61.55-v1");
        assert_eq!(view.input, "component-result.json");
        let component = &view.components[0];
        assert_eq!(component.id, "synthetic-component");
        assert_eq!(component.mass_g, Some(1000.0));
        assert_eq!(component.volume_cm3, Some(128.2));
        assert_eq!(component.external_h3, "not_applicable");
        assert_eq!(component.steps.len(), 4);
        let first = &component.steps[0];
        assert_eq!((first.step, first.class.as_str()), (1, "C"));
        let governing = first.governing().unwrap();
        assert_eq!((governing.table, governing.column), (2, Some(3)));
        assert_eq!(governing.normalized_sum, 0.9);
        assert!((governing.normalized_margin - 0.1).abs() < 1e-12);
        assert_eq!(first.binding_nuclides(), ["Nb94", "C14"]);
        let last = &component.steps[3];
        assert_eq!(last.coverage, "incomplete");
        assert_eq!(
            last.unknown_reason.as_deref(),
            Some("no nuclide properties for Ni59")
        );
        assert_eq!(last.calculated_only_class.as_deref(), Some("B"));
    }

    #[test]
    fn intrusion_and_twin_results_carry_the_waste_result_as_a_member() {
        let inner = cooling_series();
        let screen = json!({"schema": "actinv-waste-intrusion-screen-result-1", "classification": inner.clone()});
        let twin = json!({"spec": "actinv-twin-result-1", "waste_classification": inner});
        assert_eq!(parse(&screen).unwrap().source, Source::IntrusionScreen);
        assert_eq!(parse(&twin).unwrap().source, Source::Twin);
        for doc in [&screen, &twin] {
            assert_eq!(parse(doc).unwrap().components[0].steps.len(), 4);
            assert_eq!(detect(doc), Some(Detected::Result));
        }
    }

    #[test]
    fn other_documents_are_an_error_message_not_a_panic() {
        let other_schema = json!({"schema": "actinv-waste-bounds-result-1"});
        let message = parse(&other_schema).unwrap_err();
        assert!(
            message.contains("actinv-waste-bounds-result-1")
                && message.contains("not a waste result")
        );
        assert!(parse(&json!({"steps": []}))
            .unwrap_err()
            .contains("no waste result"));
        assert!(parse(&json!([1, 2]))
            .unwrap_err()
            .contains("not a JSON object"));
        assert!(parse(&Value::Null).is_err());
        assert_eq!(detect(&other_schema), None);
    }

    #[test]
    fn a_malformed_waste_result_names_what_is_missing() {
        let mut no_components = cooling_series();
        no_components["components"] = json!([]);
        assert!(parse(&no_components).unwrap_err().contains("no components"));
        let mut no_targets = cooling_series();
        no_targets["components"][0]["targets"] = json!([]);
        assert!(parse(&no_targets)
            .unwrap_err()
            .contains("Component 1 has no targets"));
        let mut no_class = cooling_series();
        no_class["components"][0]["targets"][1]["class"] = Value::Null;
        let message = parse(&no_class).unwrap_err();
        assert!(
            message.contains("step 2") && message.contains("`class`"),
            "{message}"
        );
        let mut no_step = cooling_series();
        no_step["components"][0]["targets"][0]["step"] = json!("one");
        assert!(parse(&no_step).unwrap_err().contains("step number"));
        let mut bad_constraint = cooling_series();
        bad_constraint["components"][0]["targets"][0]["evaluation"]["binding_constraints"][0]
            ["table"] = Value::Null;
        assert!(parse(&bad_constraint).unwrap_err().contains("`table`"));
    }

    #[test]
    fn a_required_h3_reason_is_kept_as_text() {
        let mut doc = cooling_series();
        doc["components"][0]["targets"][1]["unknown_reason"] =
            json!("required external H-3 not declared");
        let view = parse(&doc).unwrap();
        assert_eq!(
            view.components[0].steps[1].unknown_reason.as_deref(),
            Some("required external H-3 not declared")
        );
    }

    #[test]
    fn the_earliest_step_within_each_class_is_found_by_scanning() {
        let view = parse(&cooling_series()).unwrap();
        let steps = &view.components[0].steps;
        // C at step 1 (t = 0), B at step 2, A at step 3; the unknown step 4 is never counted.
        assert_eq!(earliest_within(steps, "A").map(|s| s.step), Some(3));
        assert_eq!(earliest_within(steps, "B").map(|s| s.step), Some(2));
        assert_eq!(earliest_within(steps, "C").map(|s| s.step), Some(1));
        assert!(earliest_within(steps, "above_class_c").is_none());
    }

    #[test]
    fn earliest_ignores_unknown_and_above_class_c_and_orders_by_time() {
        let doc = waste_result(vec![
            target(1, 5.0, "above_class_c", vec![]),
            target(2, 9.0, "A", vec![]),
            target(3, 7.0, "A", vec![]),
            target(4, 1.0, "unknown", vec![]),
        ]);
        let view = parse(&doc).unwrap();
        let steps = &view.components[0].steps;
        // the steps are listed out of time order: the earlier time wins, not the lower number
        assert_eq!(earliest_within(steps, "A").map(|s| s.step), Some(3));
        assert_eq!(earliest_within(steps, "C").map(|s| s.step), Some(3));
        let none = waste_result(vec![target(1, 0.0, "above_class_c", vec![])]);
        let view = parse(&none).unwrap();
        assert!(earliest_within(&view.components[0].steps, "C").is_none());
    }

    #[test]
    fn tied_binding_constraints_are_merged_and_labelled() {
        let doc = waste_result(vec![target(
            1,
            0.0,
            "A",
            vec![
                constraint(1, None, "A", 0.5, &["C14", "Ni63"]),
                constraint(2, Some(1), "A", 0.5, &["Ni63", "Sr90"]),
            ],
        )]);
        let view = parse(&doc).unwrap();
        let step = &view.components[0].steps[0];
        assert_eq!(step.binding_nuclides(), ["C14", "Ni63", "Sr90"]);
        assert_eq!(
            governing_label(step.governing().unwrap(), 1),
            "Table 1 for Class A (+1 tied)"
        );
        let second = &step.binding[1];
        assert_eq!(governing_label(second, 0), "Table 2, column 1 for Class A");
    }

    #[test]
    fn a_step_without_binding_constraints_has_no_governing_row() {
        let doc = waste_result(vec![target(1, 0.0, "A", vec![])]);
        let view = parse(&doc).unwrap();
        assert!(view.components[0].steps[0].governing().is_none());
        assert!(view.components[0].steps[0].binding_nuclides().is_empty());
    }

    #[test]
    fn labels_and_numbers_are_formatted_for_reading() {
        assert_eq!(class_label("A"), "Class A");
        assert_eq!(class_label("above_class_c"), "Above Class C");
        assert_eq!(class_label("unknown"), "Unknown");
        assert_eq!(fraction(0.9), "0.9000");
        assert_eq!(fraction(0.0), "0.0000");
        assert_eq!(fraction(-0.25), "-0.2500");
        assert_eq!(fraction(1.5e-5), "1.5000e-5");
        assert_eq!(time(0.0), "0 s");
        assert_eq!(time(300.0), "5 min");
        let many: Vec<String> = (0..9).map(|i| format!("N{i}")).collect();
        let names: Vec<&str> = many.iter().map(String::as_str).collect();
        assert_eq!(nuclide_list(&names[..2]), "N0, N1");
        assert_eq!(nuclide_list(&names), "N0, N1, N2, N3, N4, N5 + 3 more");
    }

    #[test]
    fn detect_tells_a_spec_from_a_result() {
        let spec = json!({"schema": "actinv-waste-spec-1"});
        assert_eq!(detect(&spec), Some(Detected::Spec));
        assert_eq!(detect(&cooling_series()), Some(Detected::Result));
        assert_eq!(detect(&json!({"steps": [1]})), None);
        assert_eq!(detect(&json!("text")), None);
    }

    fn spec_text() -> Value {
        json!({
            "schema": "actinv-waste-spec-1", "rules": "us-nrc-10cfr61.55-v1",
            "input": "component-result.json", "targets": [2, 3],
            "nuclide_properties": {"Nb94": {"z": 41, "half_life_s": 1.0e11, "alpha_emitting": false}},
            "components": [{
                "id": "synthetic-component", "mass_g": 1000, "displaced_volume_cm3": 128.2,
                "waste_type": "activated_metal", "external_tritium": {"status": "not_applicable"},
            }],
        })
    }

    #[test]
    fn the_skeleton_passes_the_structure_check() {
        assert_eq!(check_spec(SPEC_SKELETON), Ok(()));
        assert_eq!(
            detect(&serde_json::from_str(SPEC_SKELETON).unwrap()),
            Some(Detected::Spec)
        );
    }

    #[test]
    fn the_documented_spec_passes_the_structure_check() {
        assert_eq!(check_spec(&spec_text().to_string()), Ok(()));
    }

    #[test]
    fn a_spec_with_missing_parts_is_refused_with_a_reason() {
        assert!(check_spec("{").unwrap_err().starts_with("Invalid JSON"));
        let other = json!({"schema": "actinv-waste-bounds-spec-1"});
        assert!(check_spec(&other.to_string())
            .unwrap_err()
            .contains("not an actinv-waste-spec-1"));
        assert!(check_spec("{}").unwrap_err().contains("no `schema`"));
        let edits: [(&str, Value, &str); 7] = [
            ("rules", json!(""), "`rules`"),
            ("input", Value::Null, "`input`"),
            ("targets", json!([0]), "`targets`"),
            ("targets", json!([]), "`targets`"),
            ("nuclide_properties", json!([]), "`nuclide_properties`"),
            ("components", json!([]), "`components`"),
            (
                "components",
                json!([{"id": "x", "mass_g": 0, "waste_type": "general", "external_tritium": {"status": "required"}}]),
                "mass_g",
            ),
        ];
        for (key, value, expected) in edits {
            let mut spec = spec_text();
            spec[key] = value;
            let message = check_spec(&spec.to_string()).unwrap_err();
            assert!(message.contains(expected), "{key}: {message}");
        }
        let mut bad_type = spec_text();
        bad_type["components"][0]["waste_type"] = json!("metal");
        assert!(check_spec(&bad_type.to_string())
            .unwrap_err()
            .contains("waste_type"));
        let mut no_h3 = spec_text();
        no_h3["components"][0]["external_tritium"] = json!({});
        assert!(check_spec(&no_h3.to_string())
            .unwrap_err()
            .contains("external_tritium"));
    }
}
