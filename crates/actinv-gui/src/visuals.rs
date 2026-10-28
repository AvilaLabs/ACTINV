//! Scientific views derived only from the currently supplied or calculated values.
use eframe::egui;
use egui::{Color32, RichText};
use egui_plot::{Bar, BarChart, Line, Plot};
use serde_json::{json, Value};

use crate::model;
pub fn accent(ui: &egui::Ui) -> egui::Color32 {
    if ui.visuals().dark_mode {
        egui::Color32::from_rgb(165, 178, 255)
    } else {
        egui::Color32::from_rgb(24, 0, 173)
    }
}

pub fn spectrum(ui: &mut egui::Ui, doc: &Value) {
    let Ok(spec) = serde_json::from_value::<actinv_core::spec::Spec>(doc.clone()) else {
        return;
    };
    let bounds = match actinv_data::groups::GroupStructure::named(spec.spectrum.structure.as_str())
    {
        Ok(Some(group)) => Ok(group.boundaries_ev),
        Ok(None) => spec
            .spectrum
            .boundaries_eV
            .clone()
            .ok_or_else(|| "Custom boundaries not yet provided".into()),
        Err(error) => Err(error),
    };
    let Ok(bounds) = bounds else { return };
    let flux = spec.flux_ascending();
    if bounds.len() != flux.len() + 1
        || bounds.iter().any(|e| !e.is_finite() || *e <= 0.)
        || bounds.windows(2).any(|w| w[0] >= w[1])
    {
        ui.label("Energy plot requires matching, positive, increasing boundaries.");
        return;
    }
    let mut points = Vec::with_capacity(flux.len() * 2);
    for (w, f) in bounds.windows(2).zip(&flux) {
        points.push([w[0].log10(), *f]);
        points.push([w[1].log10(), *f]);
    }
    let color = accent(ui);
    ui.label(format!(
        "Total incident flux: {} particles/cm²/s · {} groups",
        compact_number(flux.iter().sum::<f64>()),
        flux.len()
    ));
    Plot::new("spectrum-energy")
        .height(210.)
        .allow_scroll(false)
        .x_axis_label("Energy (eV), logarithmic")
        .y_axis_label("Group-integrated flux (particles/cm²/s)")
        .x_axis_formatter(|mark, _| format!("{:.1e}", 10_f64.powf(mark.value)))
        .y_axis_formatter(|mark, _| compact_number(mark.value))
        .show(ui, |p| {
            p.line(Line::new("Incident group flux", points).color(color))
        });
    ui.label("Step heights are group-integrated flux, not density per eV. Normalization and input ordering are applied as configured.");
}

pub fn contributors(ui: &mut egui::Ui, step: &Value, selected: &mut String) {
    let Some(activity) = step["activity_Bq_per_g"].as_object() else {
        return;
    };
    let total: f64 = activity.values().filter_map(Value::as_f64).sum();
    let mut entries: Vec<_> = activity
        .iter()
        .filter_map(|(k, v)| v.as_f64().filter(|v| *v > 0.).map(|v| (k.as_str(), v)))
        .collect();
    entries.sort_by(|a, b| b.1.total_cmp(&a.1).then_with(|| a.0.cmp(b.0)));
    entries.truncate(10);
    if total <= 0. {
        ui.label("No positive activity at this step.");
        return;
    }
    ui.label("Top activity contributors at the selected time");
    let color = accent(ui);
    let bars: Vec<_> = entries
        .iter()
        .enumerate()
        .map(|(i, (name, v))| Bar::new(i as f64, 100. * v / total).name(*name).width(0.7))
        .collect();
    Plot::new("activity-contributions")
        .height(220.)
        .allow_scroll(false)
        .include_y(0.)
        .include_y(100.)
        .x_axis_label("Nuclide rank")
        .y_axis_label("Share of total activity (%)")
        .x_axis_formatter(|mark, _| {
            let i = mark.value.round();
            if (i - mark.value).abs() < 0.01 && i >= 0. {
                entries
                    .get(i as usize)
                    .map(|e| e.0.to_string())
                    .unwrap_or_default()
            } else {
                String::new()
            }
        })
        .show(ui, |p| {
            p.bar_chart(BarChart::new("Activity share", bars).color(color))
        });
    ui.horizontal_wrapped(|ui| {
        for (name, v) in &entries {
            if ui
                .selectable_label(
                    selected == name,
                    format!("{name}: {:.1}%", 100. * v / total),
                )
                .clicked()
            {
                *selected = (*name).into();
            }
        }
    });
    let covered: f64 = entries.iter().map(|e| e.1).sum();
    ui.label(format!("Top {} account for {:.2}% of activity; remaining nuclides {:.2}%. Select a nuclide to follow its history.",entries.len(),100.*covered/total,100.*(total-covered).max(0.)/total));
}

/// A check mark drawn with lines, so it never depends on a font having the glyph.
pub fn tick(ui: &mut egui::Ui, color: Color32) -> egui::Response {
    let size = ui.text_style_height(&egui::TextStyle::Body);
    let (rect, response) = ui.allocate_exact_size(egui::vec2(size, size), egui::Sense::hover());
    let r = rect.shrink(size * 0.18);
    let points = vec![
        egui::pos2(r.left(), r.center().y),
        egui::pos2(r.left() + r.width() * 0.38, r.bottom() - r.height() * 0.08),
        egui::pos2(r.right(), r.top() + r.height() * 0.1),
    ];
    ui.painter().add(egui::Shape::line(
        points,
        egui::Stroke::new(size * 0.13, color),
    ));
    response
}

/// The cobalt accent of the light theme: selection, run and links.
pub const BLUE: egui::Color32 = egui::Color32::from_rgb(24, 0, 173);

pub fn heading(ui: &mut egui::Ui, title: &str, subtitle: &str) {
    ui.heading(RichText::new(title).size(27.));
    let color = if ui.visuals().dark_mode {
        Color32::from_rgb(175, 184, 205)
    } else {
        Color32::from_rgb(85, 93, 112)
    };
    ui.label(RichText::new(subtitle).color(color));
    ui.add_space(16.);
}

pub fn text_field(ui: &mut egui::Ui, value: &mut Value) {
    let mut s = value.as_str().unwrap_or("").to_owned();
    if ui
        .add(egui::TextEdit::singleline(&mut s).desired_width(230.))
        .changed()
    {
        *value = s.into();
    }
}

/// A text field across the available width, for long values such as a title.
pub fn wide_text_field(ui: &mut egui::Ui, value: &mut Value) {
    let mut s = value.as_str().unwrap_or("").to_owned();
    let width = ui.available_width().min(720.);
    if ui
        .add(egui::TextEdit::singleline(&mut s).desired_width(width))
        .changed()
    {
        *value = s.into();
    }
}

/// A number as people write it: plain in the everyday range, scientific when large or small, so
/// a flux reads 1.12e10 rather than 11160000000.
pub fn compact_number(n: f64) -> String {
    if n != 0. && !(1e-3..1e6).contains(&n.abs()) {
        let text = format!("{n:.4e}");
        // trim trailing zeros of the mantissa: 1.1200e10 -> 1.12e10
        match text.split_once('e') {
            Some((mantissa, exponent)) if mantissa.contains('.') => {
                format!(
                    "{}e{exponent}",
                    mantissa.trim_end_matches('0').trim_end_matches('.')
                )
            }
            _ => text,
        }
    } else {
        let text = format!("{n:.6}");
        text.trim_end_matches('0').trim_end_matches('.').to_owned()
    }
}

pub fn numeric(ui: &mut egui::Ui, value: &mut Value, speed: f64) {
    let mut n = model::number(value);
    if ui
        .add(
            egui::DragValue::new(&mut n)
                .custom_parser(model::parse_finite)
                .custom_formatter(|n, _| compact_number(n))
                .speed(speed)
                .range(0.0..=f64::MAX),
        )
        .changed()
    {
        *value = json!(n);
    }
}

pub fn json_tree(ui: &mut egui::Ui, label: &str, value: &Value) {
    match value {
        Value::Object(map) => {
            egui::CollapsingHeader::new(format!(
                "{} · {} entries",
                label.replace('_', " "),
                map.len()
            ))
            .id_salt(label)
            .default_open(label == "Ledger")
            .show(ui, |ui| {
                for (k, v) in map {
                    json_tree(ui, k, v);
                }
            });
        }
        Value::Array(items) => {
            egui::CollapsingHeader::new(format!(
                "{} · {} {}",
                label.replace('_', " "),
                items.len(),
                if items.len() == 1 { "item" } else { "items" }
            ))
            .id_salt(label)
            .show(ui, |ui| {
                egui::ScrollArea::vertical().max_height(260.).show_rows(
                    ui,
                    24.,
                    items.len(),
                    |ui, range| {
                        for i in range {
                            ui.horizontal_wrapped(|ui| {
                                ui.monospace(format!("[{i}] {}", items[i]));
                            });
                        }
                    },
                );
            });
        }
        _ => {
            ui.horizontal_wrapped(|ui| {
                ui.strong(label.replace('_', " "))
                    .on_hover_text(format!("Result field: {label}"));
                ui.label(value.to_string());
            });
        }
    }
}

#[cfg(test)]
mod tests {
    #[test]
    fn numbers_read_plainly_or_scientifically() {
        assert_eq!(super::compact_number(1.116e10), "1.116e10");
        assert_eq!(super::compact_number(3e9), "3e9");
        assert_eq!(super::compact_number(2.5), "2.5");
        assert_eq!(super::compact_number(300.), "300");
        assert_eq!(super::compact_number(0.), "0");
        assert_eq!(super::compact_number(1e-5), "1e-5");
    }
}
