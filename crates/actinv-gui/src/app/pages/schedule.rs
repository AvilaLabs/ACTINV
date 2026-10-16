//! Irradiation & cooling: the step list and its proportional timeline.
use crate::{
    app::{heading, numeric, Desktop, BLUE},
    model,
    nav::Page,
};
use eframe::egui::{self, Color32, Vec2};
use serde_json::json;

impl Desktop {
    pub(super) fn schedule(&mut self, ui: &mut egui::Ui) {
        heading(ui,"Irradiation & cooling","Durations accept units such as 5 min, 2 h, or 10 y. A zero flux multiplier is cooling.");
        let r = ui.scope(|ui| {
            let mut swap = None;
            let mut remove = None;
            if let Some(steps) = self.document["schedule"].as_array_mut() {
                let count = steps.len();
                egui::ScrollArea::vertical()
                    .id_salt("schedule-rows")
                    .max_height(285.)
                    .show(ui, |ui| {
                        for (i, step) in steps.iter_mut().enumerate() {
                            let (drop, payload) = ui.dnd_drop_zone::<usize, _>(
                                egui::Frame::group(ui.style()),
                                |ui| {
                                    ui.horizontal_wrapped(|ui| {
                                        ui.dnd_drag_source(
                                            egui::Id::new(("schedule-drag", i)),
                                            i,
                                            |ui| {
                                                ui.label("::").on_hover_text("Drag to reorder");
                                            },
                                        );
                                        ui.label(format!("{:02}", i + 1));
                                        let mut dt = step["dt"].as_str().unwrap_or("").to_owned();
                                        if ui
                                            .add(
                                                egui::TextEdit::singleline(&mut dt)
                                                    .desired_width(90.),
                                            )
                                            .changed()
                                        {
                                            step["dt"] = dt.into();
                                        }
                                        ui.label("× flux");
                                        numeric(ui, &mut step["flux"], 0.1);
                                        ui.label(if model::number(&step["flux"]) == 0. {
                                            "Cooling"
                                        } else {
                                            "Irradiation"
                                        });
                                        if ui.add_enabled(i > 0, egui::Button::new("Up")).clicked()
                                        {
                                            swap = Some((i, i - 1));
                                        }
                                        if ui
                                            .add_enabled(i + 1 < count, egui::Button::new("Down"))
                                            .clicked()
                                        {
                                            swap = Some((i, i + 1));
                                        }
                                        if ui.small_button("Remove").clicked() {
                                            remove = Some(i);
                                        }
                                    });
                                },
                            );
                            let _ = drop;
                            if let Some(source) = payload {
                                swap = Some((*source, i));
                            }
                        }
                    });
                if let Some((a, b)) = swap {
                    if a < steps.len() && b < steps.len() {
                        steps.swap(a, b);
                    }
                }
                if let Some(i) = remove {
                    steps.remove(i);
                }
                ui.horizontal(|ui| {
                    if ui.button("+ Irradiation").clicked() {
                        steps.push(json!({"dt":"5 min","flux":1.}));
                    }
                    if ui.button("+ Cooling").clicked() {
                        steps.push(json!({"dt":"1 h","flux":0.}));
                    }
                });
                ui.add_space(15.);
                ui.label("Schedule preview · block widths proportional to duration");
                let durations: Vec<_> = steps
                    .iter()
                    .map(|s| {
                        actinv_core::spec::parse_duration(s["dt"].as_str().unwrap_or(""))
                            .unwrap_or(0.)
                            .max(0.)
                    })
                    .collect();
                let total: f64 = durations.iter().sum();
                let (rect, _) = ui.allocate_exact_size(
                    Vec2::new(ui.available_width(), 42.),
                    egui::Sense::hover(),
                );
                let mut x = rect.left();
                for (i, d) in durations.iter().enumerate() {
                    let width = if total > 0. {
                        rect.width() * (*d / total) as f32
                    } else {
                        0.
                    };
                    let r =
                        egui::Rect::from_min_size(egui::pos2(x, rect.top()), Vec2::new(width, 42.));
                    ui.painter().rect_filled(
                        r.shrink(1.),
                        3.,
                        if model::number(&steps[i]["flux"]) == 0. {
                            Color32::from_rgb(146, 169, 198)
                        } else {
                            BLUE
                        },
                    );
                    if width > 24. {
                        ui.painter().text(
                            r.center(),
                            egui::Align2::CENTER_CENTER,
                            format!("{}", i + 1),
                            egui::FontId::proportional(13.),
                            Color32::WHITE,
                        );
                    }
                    x += width;
                }
                ui.label(format!(
                    "Total duration: {total:.4e} s · cobalt = irradiation · slate = cooling"
                ));
            }
        });
        self.tour
            .mark("schedule", r.response.rect.intersect(ui.clip_rect()));
        ui.add_space(16.);
        if ui.button("Continue to spectrum >").clicked() {
            self.page = Page::Spectrum;
        }
    }
}
