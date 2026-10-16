//! Material: mass, density, composition basis and the composition table.
use crate::{model, views::Marks, visuals::numeric};
use eframe::egui;
use egui_extras::{Column, TableBuilder};
use serde_json::{json, Value};

/// The add-row draft and the message about the last refused add.
#[derive(Default)]
pub struct MaterialState {
    isotope: String,
    amount: f64,
    notice: Option<String>,
}

/// The density a freshly enabled checkbox starts from; the user is expected to type their own.
const DEFAULT_DENSITY_G_CM3: f64 = 1.;

pub fn show(ui: &mut egui::Ui, document: &mut Value, state: &mut MaterialState, marks: &mut Marks) {
    let group = ui.scope(|ui| {
        ui.horizontal(|ui| {
            ui.label("Mass (g)");
            numeric(ui, &mut document["material"]["mass_g"], 0.1);
            density(ui, document);
            ui.label("Composition basis");
            let basis = document["material"]["basis"]
                .as_str()
                .unwrap_or("wt_percent")
                .to_owned();
            egui::ComboBox::from_id_salt("basis")
                .selected_text(basis_label(&basis))
                .show_ui(ui, |ui| {
                    for b in ["wt_percent", "atom_fraction", "atoms_per_g"] {
                        if ui.selectable_label(b == basis, basis_label(b)).clicked() {
                            document["material"]["basis"] = b.into();
                        }
                    }
                });
        });
        ui.label(density_note(&document["material"]));
        ui.add_space(12.);
        let wt_percent = document["material"]["basis"].as_str() == Some("wt_percent");
        let mut remove = None;
        if let Some(composition) = document["material"]["composition"].as_object_mut() {
            TableBuilder::new(ui)
                .id_salt("material-table")
                .striped(true)
                .column(Column::remainder())
                .column(Column::initial(180.))
                .column(Column::initial(80.))
                .header(28., |mut h| {
                    h.col(|ui| {
                        ui.strong("Element / isotope");
                    });
                    h.col(|ui| {
                        ui.strong("Amount");
                    });
                    h.col(|_| {});
                })
                .body(|mut body| {
                    for (key, value) in composition.iter_mut() {
                        body.row(34., |mut row| {
                            row.col(|ui| {
                                ui.label(key);
                            });
                            row.col(|ui| {
                                numeric(ui, value, 0.1);
                            });
                            row.col(|ui| {
                                if ui.small_button("Remove").clicked() {
                                    remove = Some(key.clone());
                                }
                            });
                        });
                    }
                });
            if let Some(key) = remove {
                composition.remove(&key);
            }
            let total: f64 = composition.values().map(model::number).sum();
            ui.label(format!("Composition total: {total:.6}"));
            if wt_percent && (total - 100.).abs() > 1e-6 {
                ui.colored_label(ui.visuals().error_fg_color, "Weight percentages do not total 100%. Values are used as entered; ACTINV does not silently normalize them.");
            }
        }
        ui.horizontal(|ui| {
            ui.add(
                egui::TextEdit::singleline(&mut state.isotope)
                    .hint_text("e.g. Fe56")
                    .desired_width(150.),
            );
            ui.add(
                egui::DragValue::new(&mut state.amount)
                    .custom_parser(model::parse_finite)
                    .speed(0.1),
            );
            if ui.button("Add element / isotope").clicked() {
                state.notice = add_component(document, &state.isotope, state.amount).err();
                if state.notice.is_none() {
                    state.isotope.clear();
                }
            }
        });
        if let Some(notice) = &state.notice {
            ui.colored_label(ui.visuals().error_fg_color, notice);
        }
    });
    marks.mark("material", group.response.rect.intersect(ui.clip_rect()));
}

/// The optional density: a checkbox that sets or removes `material.density_g_cm3`, and its value.
fn density(ui: &mut egui::Ui, document: &mut Value) {
    let mut enabled = document["material"]["density_g_cm3"].is_number();
    ui.label("Density (g/cm3)");
    if ui.checkbox(&mut enabled, "").changed() {
        set_density(document, enabled.then_some(DEFAULT_DENSITY_G_CM3));
    }
    if enabled {
        let mut value = model::number(&document["material"]["density_g_cm3"]);
        if ui
            .add(
                egui::DragValue::new(&mut value)
                    .custom_parser(model::parse_finite)
                    .speed(0.01)
                    .range(1e-9..=f64::MAX),
            )
            .changed()
        {
            set_density(document, Some(value));
        }
    }
}

/// Set (`Some`) or remove (`None`) `material.density_g_cm3`.
fn set_density(document: &mut Value, density: Option<f64>) {
    let Some(material) = document["material"].as_object_mut() else {
        return;
    };
    match density {
        Some(value) if value.is_finite() => {
            material.insert("density_g_cm3".into(), json!(value));
        }
        _ => {
            material.remove("density_g_cm3");
        }
    }
}

/// The line under the mass row: what a density is for, and the volume it implies.
fn density_note(material: &Value) -> String {
    const USE: &str = "Density is optional. It enables per-cm3 totals in the step summary, W/cm3 power steps, and is offered to geometry self-shielding.";
    match material["density_g_cm3"].as_f64() {
        Some(density) if density.is_finite() && density > 0. => {
            let volume = model::number(&material["mass_g"]) / density;
            format!("{USE} Volume = {volume:.4} cm3.")
        }
        _ => USE.into(),
    }
}

/// Add one composition entry; the error is the message to show beside the add row.
fn add_component(document: &mut Value, name: &str, amount: f64) -> Result<(), String> {
    let key = name.trim();
    if key.is_empty() {
        return Err("Enter an element or isotope name.".into());
    }
    let Some(map) = document["material"]["composition"].as_object_mut() else {
        return Err("The material has no composition table to add to.".into());
    };
    if map.contains_key(key) {
        return Err("That isotope is already listed. Edit its amount in the table.".into());
    }
    map.insert(key.into(), json!(amount));
    Ok(())
}

fn basis_label(basis: &str) -> &str {
    match basis {
        "wt_percent" => "Weight percent (%)",
        "atom_fraction" => "Atomic ratios",
        "atoms_per_g" => "Atoms per gram",
        _ => basis,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use actinv_core::spec::Spec;

    fn document() -> Value {
        model::decode_problem(model::EXAMPLE).unwrap()
    }

    #[test]
    fn density_sets_and_removes_the_documented_field() {
        let mut doc = document();
        assert!(doc["material"].get("density_g_cm3").is_none());
        set_density(&mut doc, Some(7.874));
        assert_eq!(doc["material"]["density_g_cm3"], json!(7.874));
        assert!(Spec::from_json(&doc.to_string()).is_ok());
        let spec = Spec::from_json(&doc.to_string()).unwrap();
        assert_eq!(spec.material.density_g_cm3, Some(7.874));
        set_density(&mut doc, None);
        assert!(doc["material"].get("density_g_cm3").is_none());
        set_density(&mut doc, Some(f64::NAN));
        assert!(doc["material"].get("density_g_cm3").is_none());
        assert!(Spec::from_json(&doc.to_string()).is_ok());
    }

    #[test]
    fn density_note_reports_the_volume_only_for_a_positive_density() {
        let material = json!({"mass_g": 10., "density_g_cm3": 5.});
        assert!(density_note(&material).contains("Volume = 2.0000 cm3"));
        assert!(!density_note(&json!({"mass_g": 10.})).contains("Volume"));
        assert!(!density_note(&json!({"mass_g": 10., "density_g_cm3": 0.})).contains("Volume"));
    }

    #[test]
    fn adding_components_refuses_blank_and_duplicate_names() {
        let mut doc = document();
        assert!(add_component(&mut doc, "  ", 1.).is_err());
        assert!(add_component(&mut doc, "FE", 1.).is_err());
        add_component(&mut doc, " Co59 ", 0.5).unwrap();
        assert_eq!(doc["material"]["composition"]["Co59"], json!(0.5));
    }

    #[test]
    fn the_view_renders_marks_the_anchor_and_leaves_the_document_alone() {
        let ctx = egui::Context::default();
        let mut doc = document();
        let before = doc.clone();
        let mut state = MaterialState::default();
        let mut marks = Marks::default();
        let mut output = ctx.run_ui(egui::RawInput::default(), |ui| {
            show(ui, &mut doc, &mut state, &mut marks);
        });
        output.textures_delta.clear();
        assert_eq!(doc, before);
        assert!(marks.0.iter().any(|(name, _)| *name == "material"));
    }
}
