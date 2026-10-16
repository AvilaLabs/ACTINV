//! Material: mass, composition basis and the composition table.
use crate::{
    app::{heading, numeric, Desktop},
    model,
    nav::Page,
};
use eframe::egui;
use egui_extras::{Column, TableBuilder};
use serde_json::json;

impl Desktop {
    pub(super) fn material(&mut self, ui: &mut egui::Ui) {
        heading(ui,"Define the material","Use element symbols for natural composition, or individual isotope names for enrichment.");
        let group = ui.scope(|ui| {
            ui.horizontal(|ui| {
                ui.label("Mass (g)");
                numeric(ui, &mut self.document["material"]["mass_g"], 0.1);
                ui.label("Composition basis");
                let basis = self.document["material"]["basis"]
                    .as_str()
                    .unwrap_or("wt_percent")
                    .to_owned();
                egui::ComboBox::from_id_salt("basis")
                    .selected_text(basis_label(&basis))
                    .show_ui(ui, |ui| {
                        for b in ["wt_percent", "atom_fraction", "atoms_per_g"] {
                            if ui.selectable_label(b == basis, basis_label(b)).clicked() {
                                self.document["material"]["basis"] = b.into();
                            }
                        }
                    });
            });
            ui.add_space(12.);
            let mut remove = None;
            if let Some(composition) = self.document["material"]["composition"].as_object_mut() {
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
                if self.document["material"]["basis"].as_str() == Some("wt_percent") && (total - 100.).abs() > 1e-6 {
                    ui.colored_label(ui.visuals().error_fg_color, "Weight percentages do not total 100%. Values are used as entered; ACTINV does not silently normalize them.");
                }
            }
            ui.horizontal(|ui| {
                ui.add(
                    egui::TextEdit::singleline(&mut self.isotope)
                        .hint_text("e.g. Fe56")
                        .desired_width(150.),
                );
                ui.add(egui::DragValue::new(&mut self.amount).custom_parser(model::parse_finite).speed(0.1));
                if ui.button("Add element / isotope").clicked() {
                    let key = self.isotope.trim();
                    if key.is_empty() {
                        self.report(Err("Enter an element or isotope name.".into()));
                    } else if let Some(map) =
                        self.document["material"]["composition"].as_object_mut()
                    {
                        if map.contains_key(key) {
                            self.status =
                                "That isotope is already listed. Edit its amount in the table."
                                    .into();
                            self.error = true;
                        } else {
                            map.insert(key.into(), json!(self.amount));
                            self.isotope.clear();
                        }
                    }
                }
            });
        });
        self.tour
            .mark("material", group.response.rect.intersect(ui.clip_rect()));
        ui.add_space(20.);
        if ui.button("Continue to irradiation & cooling >").clicked() {
            self.page = Page::Schedule;
        }
    }
}

fn basis_label(basis: &str) -> &str {
    match basis {
        "wt_percent" => "Weight percent (%)",
        "atom_fraction" => "Atomic ratios",
        "atoms_per_g" => "Atoms per gram",
        _ => basis,
    }
}
