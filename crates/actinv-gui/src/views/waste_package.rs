//! A container of several items: the form for `actinv waste package` and its result. Each item
//! takes its activity from the open result times its mass, from another saved result, or as
//! declared becquerels; the command averages them under the NRC concentration-averaging position.
use super::waste_forms::{amount, card, class_chip, class_words, section, three, RULES};
use eframe::egui::{self, RichText};
use egui_extras::{Column, TableBuilder};
use serde_json::{json, Map, Value};
use std::collections::{BTreeMap, BTreeSet};

/// The waste types of the concentration-averaging position, with the words the form shows.
const TYPES: [(&str, &str); 5] = [
    ("activated_metal", "Activated metal"),
    ("contaminated_material", "Contaminated material"),
    (
        "design_radioactivity_component",
        "Component with design radioactivity",
    ),
    ("sealed_source", "Sealed source"),
    ("cartridge_filter", "Cartridge filter"),
];

const SCREENS: [(&str, &str); 3] = [
    ("none", "No screen"),
    ("activity_37mbq", "Leave out items under 37 MBq"),
    (
        "highest_item",
        "Leave out all but the highest-activity item",
    ),
];

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Source {
    /// The open result's activity per gram, times the item's mass.
    ThisResult,
    /// Another saved result's activity per gram, times the item's mass.
    File,
    /// Whole-item activities in Bq, entered for every step.
    Declared,
}

#[derive(Clone, Debug)]
pub struct Item {
    pub id: String,
    pub waste_type: usize,
    pub mass_g: f64,
    pub volume_cm3: f64,
    pub section_of: String,
    pub source: Source,
    /// The saved result an item of `Source::File` reads, and the nuclides active in it.
    pub file: String,
    #[cfg_attr(target_arch = "wasm32", allow(dead_code))]
    pub file_nuclides: BTreeSet<String>,
    /// Declared activity: nuclide and its Bq at each step.
    pub declared: Vec<(String, BTreeMap<u64, f64>)>,
}

impl Item {
    fn new(n: usize, source: Source) -> Self {
        Self {
            id: format!("item-{n}"),
            waste_type: 0,
            mass_g: 1000.,
            volume_cm3: 128.,
            section_of: String::new(),
            source,
            file: String::new(),
            file_nuclides: BTreeSet::new(),
            declared: Vec::new(),
        }
    }
}

pub struct PackageState {
    pub items: Vec<Item>,
    pub skipped: BTreeSet<u64>,
    /// Steps declared by hand when there is no result to number them.
    pub declared_steps: u64,
    pub screen: usize,
    pub encapsulated: bool,
    pub total_volume_cm3: f64,
    pub encapsulant_mass_g: f64,
    pub encapsulant_density_g_cm3: f64,
}

impl Default for PackageState {
    fn default() -> Self {
        Self {
            items: Vec::new(),
            skipped: BTreeSet::new(),
            declared_steps: 1,
            screen: 0,
            encapsulated: false,
            total_volume_cm3: 0.,
            encapsulant_mass_g: 0.,
            encapsulant_density_g_cm3: 0.,
        }
    }
}

/// What a frame asks the page to do.
#[derive(PartialEq, Eq, Debug)]
pub enum PackageAction {
    None,
    /// Choose the saved result of item `i`.
    PickFile(usize),
}

impl PackageState {
    /// The steps this container is classified at: the selected steps of the open result, or the
    /// declared steps when no result is open.
    pub fn targets(&self, steps: &[(u64, String)]) -> Vec<u64> {
        steps
            .iter()
            .map(|(n, _)| *n)
            .filter(|n| !self.skipped.contains(n))
            .collect()
    }

    /// The nuclides declared on any item, with positive activity at a target step.
    pub fn declared_nuclides(&self, targets: &[u64]) -> BTreeSet<String> {
        self.items
            .iter()
            .filter(|item| item.source == Source::Declared)
            .flat_map(|item| &item.declared)
            .filter(|(name, bq)| {
                !name.trim().is_empty()
                    && targets.iter().any(|t| bq.get(t).is_some_and(|v| *v > 0.))
            })
            .map(|(name, _)| name.trim().to_owned())
            .collect()
    }

    pub fn uses_this_result(&self) -> bool {
        self.items
            .iter()
            .any(|item| item.source == Source::ThisResult)
    }
}

/// The `actinv-waste-package-spec-1` document. `result_name` is how the spec names the open
/// result; `properties` the `nuclide_properties` map.
pub fn package_spec(
    state: &PackageState,
    targets: &[u64],
    result_name: &str,
    properties: Value,
) -> Result<Value, String> {
    if targets.is_empty() {
        return Err("Choose at least one step to classify.".into());
    }
    if state.items.is_empty() {
        return Err("Add the items that go in the container.".into());
    }
    let mut ids = BTreeSet::new();
    let mut items = Vec::new();
    for item in &state.items {
        let id = item.id.trim();
        if id.is_empty() {
            return Err("Every item needs a name.".into());
        }
        if !ids.insert(id.to_owned()) {
            return Err(format!(
                "Two items are called {id}; give each its own name."
            ));
        }
        if !(item.mass_g.is_finite() && item.mass_g > 0.) {
            return Err(format!("Item {id} needs a positive mass."));
        }
        if !(item.volume_cm3.is_finite() && item.volume_cm3 > 0.) {
            return Err(format!("Item {id} needs a positive volume."));
        }
        let (kind, _) = TYPES[item.waste_type];
        let mut entry = json!({"id": id, "waste_type": kind, "mass_g": item.mass_g});
        let volume_key = if kind == "cartridge_filter" {
            "envelope_volume_cm3"
        } else {
            "displaced_volume_cm3"
        };
        entry[volume_key] = json!(item.volume_cm3);
        if !item.section_of.trim().is_empty() {
            entry["section_of"] = json!(item.section_of.trim());
        }
        entry["activity"] = match item.source {
            Source::ThisResult => json!({"result": result_name}),
            Source::File => {
                if item.file.trim().is_empty() {
                    return Err(format!(
                        "Choose the saved result item {id} takes its activity from."
                    ));
                }
                json!({"result": item.file.trim()})
            }
            Source::Declared => {
                let mut per_step = Map::new();
                for step in targets {
                    let mut at = Map::new();
                    for (name, bq) in &item.declared {
                        let name = name.trim();
                        if name.is_empty() {
                            continue;
                        }
                        let value = bq.get(step).copied().unwrap_or(0.);
                        if !(value.is_finite() && value >= 0.) {
                            return Err(format!(
                                "Item {id}: the {name} activity at step {step} must be zero or more."
                            ));
                        }
                        at.insert(name.to_owned(), json!(value));
                    }
                    if at.is_empty() {
                        return Err(format!(
                            "Item {id}: declare at least one nuclide's activity."
                        ));
                    }
                    per_step.insert(step.to_string(), Value::Object(at));
                }
                json!({"declared_bq": per_step})
            }
        };
        if state.encapsulated {
            entry["encapsulated"] = json!(true);
        }
        items.push(entry);
    }
    let mut sections: BTreeMap<&str, usize> = BTreeMap::new();
    for item in &state.items {
        let s = item.section_of.trim();
        if !s.is_empty() {
            *sections.entry(s).or_default() += 1;
        }
    }
    if let Some((name, _)) = sections.iter().find(|(_, n)| **n < 2) {
        return Err(format!(
            "Only one piece of {name} is in the container; every piece of a sectioned component goes in the same container."
        ));
    }
    let (screen, _) = SCREENS[state.screen];
    let encapsulation = if state.encapsulated {
        for (value, what) in [
            (state.total_volume_cm3, "total encapsulated volume"),
            (state.encapsulant_mass_g, "encapsulant mass"),
            (state.encapsulant_density_g_cm3, "encapsulant density"),
        ] {
            if !(value.is_finite() && value > 0.) {
                return Err(format!("Enter a positive {what}."));
            }
        }
        json!({
            "total_volume_cm3": state.total_volume_cm3,
            "encapsulant_mass_g": state.encapsulant_mass_g,
            "encapsulant_density_g_cm3": state.encapsulant_density_g_cm3,
        })
    } else {
        Value::Null
    };
    Ok(json!({
        "schema": "actinv-waste-package-spec-1",
        "rules": RULES,
        "targets": targets,
        "screen": if state.encapsulated { "none" } else { screen },
        "nuclide_properties": properties,
        "items": items,
        "encapsulation": encapsulation,
    }))
}

/// The container form. `steps` are the open result's (empty when none is open); `files` is
/// false where saved results cannot be read (the browser).
pub fn show_form(
    ui: &mut egui::Ui,
    state: &mut PackageState,
    steps: &[(u64, String)],
    files: bool,
) -> PackageAction {
    let mut action = PackageAction::None;
    let have_result = !steps.is_empty();
    if state.items.is_empty() {
        let source = if have_result {
            Source::ThisResult
        } else {
            Source::Declared
        };
        state.items.push(Item::new(1, source));
    }

    section(
        ui,
        "Items in the container",
        "Each item's activity is the run's activity per gram times its mass, another saved result's, or becquerels you declare. Pieces of one sectioned component share a \"piece of\" name.",
    );
    let declared_steps: Vec<(u64, String)> = (1..=state.declared_steps)
        .map(|n| (n, format!("step {n}")))
        .collect();
    let step_list = if have_result { steps } else { &declared_steps };
    let targets = state.targets(step_list);
    let mut remove = None;
    let count = state.items.len();
    for (i, item) in state.items.iter_mut().enumerate() {
        card(ui, |ui| {
            ui.push_id(("package-item", i), |ui| {
                ui.horizontal_wrapped(|ui| {
                    ui.add(
                        egui::TextEdit::singleline(&mut item.id)
                            .hint_text("name")
                            .desired_width(110.),
                    );
                    egui::ComboBox::from_id_salt("type")
                        .selected_text(TYPES[item.waste_type].1)
                        .width(200.)
                        .show_ui(ui, |ui| {
                            for (k, (_, label)) in TYPES.iter().enumerate() {
                                ui.selectable_value(&mut item.waste_type, k, *label);
                            }
                        });
                    amount(ui, &mut item.mass_g, 70.);
                    ui.label("g");
                    amount(ui, &mut item.volume_cm3, 70.);
                    ui.label(if TYPES[item.waste_type].0 == "cartridge_filter" {
                        "cm³ envelope"
                    } else {
                        "cm³ displaced"
                    });
                    ui.label("piece of");
                    ui.add(
                        egui::TextEdit::singleline(&mut item.section_of)
                            .hint_text("optional")
                            .desired_width(90.),
                    );
                    if count > 1 && ui.button("Remove").clicked() {
                        remove = Some(i);
                    }
                });
                ui.horizontal_wrapped(|ui| {
                    ui.label("Activity");
                    if have_result {
                        ui.selectable_value(
                            &mut item.source,
                            Source::ThisResult,
                            "this result × mass",
                        );
                    }
                    if files {
                        ui.selectable_value(
                            &mut item.source,
                            Source::File,
                            "a saved result × mass",
                        );
                    }
                    ui.selectable_value(&mut item.source, Source::Declared, "declared in Bq");
                    if item.source == Source::File {
                        if ui.button("Choose…").clicked() {
                            action = PackageAction::PickFile(i);
                        }
                        if item.file.is_empty() {
                            ui.weak("no file chosen");
                        } else {
                            ui.weak(&item.file);
                        }
                    }
                });
                if item.source == Source::Declared {
                    declared_table(ui, item, &targets);
                }
            });
        });
    }
    if let Some(i) = remove {
        state.items.remove(i);
    }
    let next = state.items.len() + 1;
    if ui.button("+ Item").clicked() {
        let source = state
            .items
            .last()
            .map_or(Source::Declared, |item| item.source);
        state.items.push(Item::new(next, source));
    }

    section(ui, "Steps", "");
    card(ui, |ui| {
        if have_result {
            super::waste_forms::step_choice(ui, "package", steps, &mut state.skipped);
        } else {
            ui.horizontal(|ui| {
                ui.label("Declared steps");
                ui.add(egui::DragValue::new(&mut state.declared_steps).range(1..=64));
                ui.weak("Each declared item gives its activity at every step.");
            });
        }
    });

    section(ui, "Averaging", "");
    card(ui, |ui| {
        ui.checkbox(&mut state.encapsulated, "The items are encapsulated");
        if state.encapsulated {
            ui.horizontal_wrapped(|ui| {
                ui.label("Total volume");
                amount(ui, &mut state.total_volume_cm3, 80.);
                ui.label("cm³   encapsulant");
                amount(ui, &mut state.encapsulant_mass_g, 80.);
                ui.label("g at");
                amount(ui, &mut state.encapsulant_density_g_cm3, 60.);
                ui.label("g/cm³");
            });
            ui.weak("Encapsulated containers are averaged over the credited volume; there is no screen.");
        } else {
            ui.horizontal(|ui| {
                ui.label("Screen");
                egui::ComboBox::from_id_salt("package-screen")
                    .selected_text(SCREENS[state.screen].1)
                    .width(320.)
                    .show_ui(ui, |ui| {
                        for (k, (_, label)) in SCREENS.iter().enumerate() {
                            ui.selectable_value(&mut state.screen, k, *label);
                        }
                    });
            });
        }
    });
    action
}

fn declared_table(ui: &mut egui::Ui, item: &mut Item, targets: &[u64]) {
    let mut remove = None;
    egui::Grid::new("declared")
        .num_columns(targets.len() + 2)
        .spacing([10., 4.])
        .show(ui, |ui| {
            ui.weak("Nuclide");
            for step in targets {
                ui.weak(format!("Bq at step {step}"));
            }
            ui.end_row();
            for (k, (name, bq)) in item.declared.iter_mut().enumerate() {
                ui.add(
                    egui::TextEdit::singleline(name)
                        .hint_text("Nb94")
                        .desired_width(70.),
                );
                for step in targets {
                    amount(ui, bq.entry(*step).or_insert(0.), 90.);
                }
                if ui.small_button("Remove").clicked() {
                    remove = Some(k);
                }
                ui.end_row();
            }
        });
    if let Some(k) = remove {
        item.declared.remove(k);
    }
    if ui.small_button("+ Nuclide").clicked() {
        item.declared.push((String::new(), BTreeMap::new()));
    }
}

fn status_words(status: &str) -> &'static str {
    match status {
        "classified" => "classified",
        "unknown" => "a nuclide has no properties, so the class is unknown",
        "alternative_approach_required" => {
            "outside the averaging position; an alternative approach is needed"
        }
        "exceeds_class_c_cap" => "the averaged value exceeds the Class C cap",
        _ => "",
    }
}

/// The container's class at every step, how each waste-type group averaged, and every item.
pub fn show_result(ui: &mut egui::Ui, result: &Value) {
    let Some(steps) = result["steps"].as_array() else {
        ui.colored_label(
            ui.visuals().error_fg_color,
            "This is not a container result.",
        );
        return;
    };
    for step in steps {
        card(ui, |ui| {
            ui.horizontal_wrapped(|ui| {
                let when = step["t_s"].as_f64().map_or(String::new(), |t| {
                    format!(" · {}", crate::model::readable_time(t))
                });
                ui.label(RichText::new(format!("Step {}{when}", step["step"])).strong());
                let class = step["package_class"].as_str().unwrap_or("unknown");
                ui.label(
                    RichText::new(class_words(class))
                        .heading()
                        .color(super::waste_forms::class_colour(ui, class)),
                );
                ui.weak(status_words(step["status"].as_str().unwrap_or("")));
            });
            if let Some(groups) = step["groups"].as_array() {
                for group in groups {
                    let kind = group["waste_type"].as_str().unwrap_or("");
                    let label = TYPES
                        .iter()
                        .find(|(k, _)| *k == kind)
                        .map_or(kind, |(_, l)| l);
                    ui.horizontal_wrapped(|ui| {
                        ui.label(format!("{label}:"));
                        class_chip(ui, group["mixture_class"].as_str().unwrap_or("unknown"));
                        if group["screen_applied"] == true {
                            ui.weak("screened");
                        }
                        let removed: Vec<String> = group["removed_items"]
                            .as_array()
                            .into_iter()
                            .flatten()
                            .map(|r| {
                                format!(
                                    "{} ({})",
                                    r["id"].as_str().unwrap_or("?"),
                                    class_words(r["own_class"].as_str().unwrap_or("unknown"))
                                )
                            })
                            .collect();
                        if !removed.is_empty() {
                            ui.weak(format!("classed on their own: {}", removed.join(", ")));
                        }
                    });
                }
            }
            if let Some(e) = step["encapsulation"].as_object() {
                ui.weak(format!(
                    "Encapsulation: waste loading {}, {} ({}).",
                    three(e.get("loading").and_then(Value::as_f64).unwrap_or(0.)),
                    e.get("status")
                        .and_then(Value::as_str)
                        .unwrap_or("")
                        .replace('_', " "),
                    class_words(e.get("class").and_then(Value::as_str).unwrap_or("unknown")),
                ));
            }
            let items = step["items"].as_array().cloned().unwrap_or_default();
            ui.push_id(("package-items", step["step"].to_string()), |ui| {
                TableBuilder::new(ui)
                    .striped(true)
                    .cell_layout(egui::Layout::left_to_right(egui::Align::Center))
                    .column(Column::auto().at_least(100.))
                    .column(Column::auto().at_least(150.))
                    .column(Column::auto().at_least(80.))
                    .column(Column::auto().at_least(90.))
                    .column(Column::auto().at_least(110.))
                    .column(Column::remainder().at_least(90.))
                    .header(24., |mut h| {
                        for t in [
                            "Item",
                            "Waste type",
                            "Mass (g)",
                            "Volume (cm³)",
                            "Activity (Bq)",
                            "Own class",
                        ] {
                            h.col(|ui| {
                                ui.strong(t);
                            });
                        }
                    })
                    .body(|body| {
                        body.rows(24., items.len(), |mut row| {
                            let item = &items[row.index()];
                            let kind = item["waste_type"].as_str().unwrap_or("");
                            row.col(|ui| {
                                ui.label(item["id"].as_str().unwrap_or(""));
                            });
                            row.col(|ui| {
                                ui.label(
                                    TYPES
                                        .iter()
                                        .find(|(k, _)| *k == kind)
                                        .map_or(kind, |(_, l)| l),
                                );
                            });
                            row.col(|ui| {
                                ui.monospace(three(item["mass_g"].as_f64().unwrap_or(0.)));
                            });
                            row.col(|ui| {
                                ui.monospace(three(item["volume_cm3"].as_f64().unwrap_or(0.)));
                            });
                            row.col(|ui| {
                                ui.monospace(three(
                                    item["total_activity_bq"].as_f64().unwrap_or(0.),
                                ));
                            });
                            row.col(|ui| {
                                class_chip(ui, item["own_class"].as_str().unwrap_or("unknown"));
                            });
                        });
                    });
            });
        });
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn declared(id: &str, bq: f64) -> Item {
        let mut item = Item::new(1, Source::Declared);
        item.id = id.into();
        item.declared = vec![("Nb94".into(), BTreeMap::from([(1, bq), (2, bq)]))];
        item
    }

    #[test]
    fn the_example_container_is_written_as_the_command_reads_it() {
        let mut state = PackageState {
            items: vec![
                declared("plug-1", 2e6),
                declared("rod-a", 1.5e5),
                declared("rod-b", 1.5e5),
            ],
            ..PackageState::default()
        };
        state.items[1].section_of = "rod-7".into();
        state.items[2].section_of = "rod-7".into();
        let spec = package_spec(&state, &[1, 2], "result.json", json!({})).unwrap();
        assert_eq!(spec["schema"], "actinv-waste-package-spec-1");
        assert_eq!(spec["screen"], "none");
        assert_eq!(spec["encapsulation"], Value::Null);
        assert_eq!(
            spec["items"][0]["activity"]["declared_bq"]["2"]["Nb94"],
            2e6
        );
        assert_eq!(spec["items"][1]["section_of"], "rod-7");
        assert_eq!(spec["items"][0]["displaced_volume_cm3"], 128.);
        assert_eq!(state.declared_nuclides(&[1, 2]), ["Nb94".to_owned()].into());
    }

    #[test]
    fn result_items_name_the_result_and_filters_take_an_envelope() {
        let mut item = Item::new(1, Source::ThisResult);
        item.waste_type = 4;
        let state = PackageState {
            items: vec![item],
            ..PackageState::default()
        };
        let spec = package_spec(&state, &[3], "result.json", json!({})).unwrap();
        assert_eq!(
            spec["items"][0]["activity"],
            json!({"result": "result.json"})
        );
        assert_eq!(spec["items"][0]["envelope_volume_cm3"], 128.);
        assert!(spec["items"][0].get("displaced_volume_cm3").is_none());
        assert!(state.uses_this_result());
    }

    #[test]
    fn impossible_containers_are_refused_in_words() {
        let mut state = PackageState::default();
        assert!(package_spec(&state, &[1], "r", json!({}))
            .unwrap_err()
            .contains("items"));
        state.items = vec![declared("a", 1.), declared("a", 1.)];
        assert!(package_spec(&state, &[1], "r", json!({}))
            .unwrap_err()
            .contains("Two items"));
        state.items[1].id = "b".into();
        state.items[1].section_of = "rod".into();
        assert!(package_spec(&state, &[1], "r", json!({}))
            .unwrap_err()
            .contains("Only one piece"));
        state.items[1].section_of.clear();
        state.encapsulated = true;
        assert!(package_spec(&state, &[1], "r", json!({}))
            .unwrap_err()
            .contains("total encapsulated"));
        state.total_volume_cm3 = 1e4;
        state.encapsulant_mass_g = 5e3;
        state.encapsulant_density_g_cm3 = 2.;
        state.screen = 1;
        let spec = package_spec(&state, &[1], "r", json!({})).unwrap();
        assert_eq!(spec["screen"], "none");
        assert_eq!(spec["items"][0]["encapsulated"], true);
        state.items[0].source = Source::File;
        assert!(package_spec(&state, &[1], "r", json!({}))
            .unwrap_err()
            .contains("saved result"));
        assert!(package_spec(&state, &[], "r", json!({})).is_err());
    }
}
