//! Material: a starting composition from the shipped library, mass, density, composition basis and the
//! composition table.
use crate::{model, views::Marks, visuals::numeric};
use actinv_core::presets::{self, MaterialPreset};
use eframe::egui;
use egui_extras::{Column, TableBuilder};
use serde_json::{json, Value};
use std::sync::OnceLock;

/// The add-row draft and the message about the last refused add.
#[derive(Default)]
pub struct MaterialState {
    isotope: String,
    amount: f64,
    notice: Option<String>,
    /// the library entry picked in the starting-composition card
    library: Option<String>,
}

/// The density a freshly enabled checkbox starts from; the user is expected to type their own.
const DEFAULT_DENSITY_G_CM3: f64 = 1.;

/// The shipped material library (P162), read once.
fn library() -> &'static [MaterialPreset] {
    static LIBRARY: OnceLock<Vec<MaterialPreset>> = OnceLock::new();
    LIBRARY.get_or_init(|| presets::materials().unwrap_or_default())
}

/// What an entry's composition is, in a sentence.
fn basis_sentence(basis: &str) -> &'static str {
    match basis {
        "specification_maxima" => "Specification maxima: trace elements at their allowed maximum, the main element the balance. Trace activation is overstated by design.",
        "measured_heat" => "Measured heat: a published analysis of one batch, not the specification.",
        _ => "Nominal composition: generic values, not a real heat.",
    }
}

/// The largest few elements of a composition, as "Fe 70.2 · Cr 19 · Ni 9.25 · and 5 more".
fn composition_line(preset: &MaterialPreset) -> String {
    let mut parts: Vec<(&String, &f64)> = preset.composition_wt_percent.iter().collect();
    parts.sort_by(|a, b| b.1.total_cmp(a.1));
    let shown: Vec<String> = parts
        .iter()
        .take(4)
        .map(|(symbol, value)| format!("{symbol} {}", crate::visuals::compact_number(**value)))
        .collect();
    let rest = parts.len().saturating_sub(4);
    let tail = if rest > 0 {
        format!(" · and {rest} more")
    } else {
        String::new()
    };
    format!("{} wt%{tail}", shown.join(" · "))
}

/// Put a library entry in the problem: its weight-percent composition, and its density, or none when the
/// source gives none (a density left from another material would be wrong). An untitled problem takes the
/// material's name.
fn apply_preset(document: &mut Value, preset: &MaterialPreset) {
    document["material"]["basis"] = "wt_percent".into();
    document["material"]["composition"] = json!(preset.composition_wt_percent);
    set_density(document, preset.density_g_cm3);
    if document["title"]
        .as_str()
        .is_none_or(|t| t.trim().is_empty())
    {
        document["title"] = preset.name.clone().into();
    }
}

/// A starting composition from the library: open above an empty table, folded below a filled one.
fn library_card(ui: &mut egui::Ui, document: &mut Value, state: &mut MaterialState, empty: bool) {
    let body = |ui: &mut egui::Ui, state: &mut MaterialState, document: &mut Value| {
        let picked = state
            .library
            .as_deref()
            .and_then(|id| library().iter().find(|m| m.id == id));
        ui.horizontal_wrapped(|ui| {
            egui::ComboBox::from_id_salt("material-library")
                .width(340.)
                .selected_text(picked.map_or("Choose a material…", |m| m.name.as_str()))
                .show_ui(ui, |ui| {
                    let mut category = None;
                    for entry in library() {
                        if category != Some(entry.category.as_str()) {
                            category = Some(entry.category.as_str());
                            ui.label(egui::RichText::new(&entry.category).small().weak());
                        }
                        let here = state.library.as_deref() == Some(entry.id.as_str());
                        if ui.selectable_label(here, &entry.name).clicked() {
                            state.library = Some(entry.id.clone());
                        }
                    }
                });
            let label = if empty {
                "Use this material"
            } else {
                "Replace the composition with this one"
            };
            if ui
                .add_enabled(picked.is_some(), egui::Button::new(label))
                .clicked()
            {
                if let Some(preset) = picked {
                    apply_preset(document, preset);
                }
            }
        });
        if let Some(preset) = picked {
            ui.add_space(4.);
            ui.label(composition_line(preset));
            ui.label(basis_sentence(&preset.basis));
            ui.label(match preset.density_g_cm3 {
                Some(density) => format!(
                    "Density {} g/cm3, from the same source.",
                    crate::visuals::compact_number(density)
                ),
                None => "The source gives no density; enter one below if you need per-cm3 totals."
                    .into(),
            });
            ui.weak(&preset.note);
            ui.label(
                egui::RichText::new(format!(
                    "Source: {}. {}",
                    preset.source.citation, preset.source.table
                ))
                .small()
                .weak(),
            );
        }
    };
    if empty {
        egui::Frame::group(ui.style())
            .inner_margin(12.)
            .show(ui, |ui| {
                ui.set_width(ui.available_width());
                ui.strong("Start from a library material");
                ui.label("Cited compositions of common reactor and fusion materials. Check each against your own material certificate.");
                body(ui, state, document);
                ui.weak("Or add elements one by one below.");
            });
        ui.add_space(12.);
    } else {
        egui::CollapsingHeader::new("Library materials")
            .id_salt("material-library-card")
            .show(ui, |ui| body(ui, state, document));
    }
}

pub fn show(ui: &mut egui::Ui, document: &mut Value, state: &mut MaterialState, marks: &mut Marks) {
    let empty = document["material"]["composition"]
        .as_object()
        .is_none_or(|c| c.is_empty());
    if empty {
        library_card(ui, document, state, true);
    }
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
        if let Some(composition) = document["material"]["composition"]
            .as_object_mut()
            .filter(|c| !c.is_empty())
        {
            TableBuilder::new(ui)
                .id_salt("material-table")
                .striped(true)
                .cell_layout(egui::Layout::left_to_right(egui::Align::Center))
                .column(Column::exact(220.))
                .column(Column::exact(160.))
                .column(Column::remainder())
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
                                ui.label(crate::views::check::display_name(key));
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
            let unit = if wt_percent { " wt%" } else { "" };
            ui.label(format!(
                "Composition total: {}{unit}",
                crate::visuals::compact_number(total)
            ));
            if wt_percent && (total - 100.).abs() > 1e-6 {
                ui.colored_label(ui.visuals().error_fg_color, "Weight percentages do not total 100%. Values are used as entered; ACTINV does not silently normalize them.");
            }
        } else {
            ui.weak("No elements yet. Choose a library material above, or add them here one at a time.");
        }
        ui.horizontal(|ui| {
            ui.add(
                egui::TextEdit::singleline(&mut state.isotope)
                    .hint_text("Fe or Fe56")
                    .desired_width(150.),
            );
            ui.label("amount");
            ui.add(
                egui::DragValue::new(&mut state.amount)
                    .custom_parser(model::parse_finite)
                    .custom_formatter(|n, _| crate::visuals::compact_number(n))
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
    if !empty {
        ui.add_space(12.);
        library_card(ui, document, state, false);
    }
}

/// The optional density: a checkbox that sets or removes `material.density_g_cm3`, and its value.
fn density(ui: &mut egui::Ui, document: &mut Value) {
    let mut enabled = document["material"]["density_g_cm3"].is_number();
    if ui.checkbox(&mut enabled, "Density (g/cm3)").changed() {
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
        "atom_fraction" => "Atom fraction",
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
    fn every_library_material_makes_a_valid_problem_with_its_own_density() {
        assert_eq!(library().len(), 19);
        for preset in library() {
            let mut doc = document();
            set_density(&mut doc, Some(3.));
            doc["material"]["basis"] = "atom_fraction".into();
            apply_preset(&mut doc, preset);
            let spec = Spec::from_json(&doc.to_string()).unwrap();
            assert_eq!(spec.material.basis, "wt_percent");
            assert_eq!(spec.material.composition, preset.composition_wt_percent);
            // the old density never survives a material change
            assert_eq!(
                spec.material.density_g_cm3, preset.density_g_cm3,
                "{}",
                preset.id
            );
            assert_eq!(
                doc["title"],
                document()["title"],
                "a titled problem keeps its title"
            );
            assert!(
                composition_line(preset).ends_with("more")
                    || preset.composition_wt_percent.len() <= 4
            );
        }
        let mut blank = model::blank();
        apply_preset(&mut blank, &library()[0]);
        assert_eq!(blank["title"], json!(library()[0].name));
    }

    #[test]
    fn an_empty_composition_shows_the_library_card_and_no_total_error() {
        let ctx = egui::Context::default();
        let mut doc = model::blank();
        let mut state = MaterialState {
            library: Some("ss316".into()),
            ..MaterialState::default()
        };
        let mut marks = Marks::default();
        let mut output = ctx.run_ui(egui::RawInput::default(), |ui| {
            show(ui, &mut doc, &mut state, &mut marks);
        });
        output.textures_delta.clear();
        let shapes = format!("{:?}", output.shapes);
        assert!(shapes.contains("Start from a library material"));
        assert!(shapes.contains("Replace") || shapes.contains("Use this material"));
        assert!(!shapes.contains("do not total 100%"));
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
