//! Scientific views derived only from the currently supplied or calculated values.
use eframe::egui;
use egui_plot::{Bar, BarChart, Line, Plot};
use serde_json::Value;
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
    let bounds = match spec.spectrum.structure.as_str() {
        "fispact-709" => {
            actinv_data::groups::GroupStructure::fispact_709().map(|g| g.boundaries_ev)
        }
        "fispact-162" => {
            actinv_data::groups::GroupStructure::fispact_162().map(|g| g.boundaries_ev)
        }
        _ => spec
            .spectrum
            .boundaries_eV
            .clone()
            .ok_or_else(|| "Custom boundaries not yet provided".into()),
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
        "Total incident flux: {:.5e} particles/cm²/s · {} groups",
        flux.iter().sum::<f64>(),
        flux.len()
    ));
    Plot::new("spectrum-energy")
        .height(210.)
        .allow_scroll(false)
        .x_axis_label("Energy (eV), logarithmic")
        .y_axis_label("Group-integrated flux (particles/cm²/s)")
        .x_axis_formatter(|mark, _| format!("{:.1e}", 10_f64.powf(mark.value)))
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
