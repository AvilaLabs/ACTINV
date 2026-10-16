//! Sweep: generate and solve a family of problems along one parameter.
use crate::app::{heading, Desktop, BLUE};
use eframe::egui;
use serde_json::Value;

/// Whether any step is driven by something other than a flux multiplier (a wall loading or a
/// power density). The sweep scales `spectrum.total`, which does not move those drives.
fn has_non_flux_drive(document: &Value) -> bool {
    document["schedule"].as_array().is_some_and(|steps| {
        steps.iter().any(|s| {
            ["wall_loading_MW_m2", "power_W_cm3", "power_W_per_g"]
                .iter()
                .any(|key| !s[*key].is_null())
        })
    })
}

impl Desktop {
    /// Interactive parameter sweep (P48). Every point is a full generated
    /// spec solved through the same isolated worker path as a manual run;
    /// the plot binds each rendered value to its spec digest.
    pub(super) fn sweep_page(&mut self, ui: &mut egui::Ui) {
        heading(
            ui,
            "Sweep",
            "Each point is a complete solver run on a generated spec — the same qualified path as a manual Run. Results stay bound to their parameters.",
        );
        const AXES: [&str; 3] = [
            "Composition fraction (wt%)",
            "Flux normalization (×)",
            "Cooling step duration (s)",
        ];
        let axis_obj = match self.sweep.axis {
            0 => crate::sweep::SweepAxis::CompositionFraction {
                element: self.sweep.element.clone(),
            },
            1 => crate::sweep::SweepAxis::FluxNormalization,
            _ => crate::sweep::SweepAxis::CoolingTimeS {
                step_index: self.sweep.step_index,
            },
        };
        ui.horizontal(|ui| {
            egui::ComboBox::from_id_salt("sweep-axis")
                .selected_text(AXES[self.sweep.axis])
                .show_ui(ui, |ui| {
                    for (i, a) in AXES.iter().enumerate() {
                        ui.selectable_value(&mut self.sweep.axis, i, *a);
                    }
                });
            match self.sweep.axis {
                0 => {
                    let mut elems: Vec<String> = self.document["material"]["composition"]
                        .as_object()
                        .map(|m| m.keys().cloned().collect())
                        .unwrap_or_default();
                    elems.sort();
                    if self.sweep.element.is_empty() {
                        self.sweep.element = elems.first().cloned().unwrap_or_default();
                    }
                    egui::ComboBox::from_id_salt("sweep-element")
                        .selected_text(&self.sweep.element)
                        .show_ui(ui, |ui| {
                            for e in &elems {
                                ui.selectable_value(&mut self.sweep.element, e.clone(), e);
                            }
                        });
                }
                2 => {
                    let n = self.document["schedule"]
                        .as_array()
                        .map(|s| s.len())
                        .unwrap_or(0);
                    egui::ComboBox::from_id_salt("sweep-step")
                        .selected_text(format!("cooling step {}", self.sweep.step_index + 1))
                        .show_ui(ui, |ui| {
                            for i in 0..n {
                                ui.selectable_value(
                                    &mut self.sweep.step_index,
                                    i,
                                    format!("step {}", i + 1),
                                );
                            }
                        });
                }
                _ => {}
            }
        });
        let flux_blocked = self.sweep.axis == 1 && has_non_flux_drive(&self.document);
        if flux_blocked {
            ui.colored_label(
                BLUE,
                "This problem has wall-loading or power-driven steps. The flux axis scales the spectrum total, which does not move those drives, so it is not offered here. Vary the drive in the problem and use the command line (actinv run) for such studies, or sweep composition or a cooling time.",
            );
        }
        ui.horizontal(|ui| {
            ui.label("from");
            ui.add(egui::DragValue::new(&mut self.sweep.lo).speed(0.05));
            ui.label("to");
            ui.add(egui::DragValue::new(&mut self.sweep.hi).speed(0.05));
            ui.label("points");
            ui.add(
                egui::DragValue::new(&mut self.sweep.count)
                    .range(2..=crate::sweep::MAX_SWEEP_POINTS),
            );
            egui::ComboBox::from_id_salt("sweep-response")
                .selected_text(self.sweep.response.label())
                .show_ui(ui, |ui| {
                    for r in [
                        crate::sweep::SweepResponse::TotalActivityBqPerG,
                        crate::sweep::SweepResponse::TotalHeatWPerG,
                    ] {
                        ui.selectable_value(&mut self.sweep.response, r, r.label());
                    }
                });
            ui.label("at step");
            let nsteps = self
                .result
                .as_ref()
                .and_then(|r| r.value["steps"].as_array().map(|s| s.len()))
                .unwrap_or(1);
            egui::ComboBox::from_id_salt("sweep-steprow")
                .selected_text(format!("{}", self.sweep.step_row + 1))
                .show_ui(ui, |ui| {
                    for i in 0..nsteps {
                        ui.selectable_value(&mut self.sweep.step_row, i, format!("{}", i + 1));
                    }
                });
        });
        ui.horizontal(|ui| {
            if self.sweep.running {
                if ui.button("Cancel sweep").clicked() {
                    if let Some(h) = &self.sweep.handle {
                        h.request_cancel();
                    }
                    self.sweep.running = false;
                    self.report(Ok(
                        "Sweep cancellation requested; the current worker is stopping and the queue is abandoned."
                            .into(),
                    ));
                }
                ui.spinner();
                ui.label(format!(
                    "{} point(s) finished",
                    self.sweep.points.len()
                ));
            } else if ui
                .add_enabled(!flux_blocked, egui::Button::new("Run sweep"))
                .clicked()
            {
                self.run_sweep();
            }
            ui.checkbox(
                &mut self.sweep.screened,
                "certified screening (warm cache — each point emits the P65 certificate)",
            );
            let live_resp = ui.add_enabled(
                !flux_blocked || self.sweep.live,
                egui::Checkbox::new(
                    &mut self.sweep.live,
                    "live (slider — solves only the newest position, certified)",
                ),
            );
            ui.checkbox(
                &mut self.sweep.frontier,
                "frontier (activity vs heat Pareto of landed points)",
            );
            if live_resp.changed() {
                if self.sweep.live {
                    // Entering live mode supersedes any batch sweep and
                    // clears the plotted curve for a fresh generation.
                    if let Some(h) = &self.sweep.handle {
                        h.request_cancel();
                    }
                    self.sweep.handle = None;
                    self.sweep.running = false;
                    self.sweep.generation += 1;
                    self.sweep.points.clear();
                    self.sweep.live_value = self.sweep.lo
                        .clamp(self.sweep.lo.min(self.sweep.hi),
                               self.sweep.lo.max(self.sweep.hi));
                } else {
                    self.sweep.live_handle = None; // drop cancels+joins
                }
            }
        });
        if self.sweep.live {
            let axis = match self.sweep.axis {
                0 => crate::sweep::SweepAxis::CompositionFraction {
                    element: self.sweep.element.clone(),
                },
                1 => crate::sweep::SweepAxis::FluxNormalization,
                _ => crate::sweep::SweepAxis::CoolingTimeS {
                    step_index: self.sweep.step_index,
                },
            };
            let (lo, hi) = (
                self.sweep.lo.min(self.sweep.hi),
                self.sweep.lo.max(self.sweep.hi),
            );
            let slider = egui::Slider::new(&mut self.sweep.live_value, lo..=hi)
                .logarithmic(lo > 0.0)
                .text(axis.label());
            if ui.add(slider).changed() {
                if self.sweep.live_handle.is_none() {
                    match crate::sweep::spawn_sweep_live(crate::sweep::SCREEN_BMIN) {
                        Ok(h) => self.sweep.live_handle = Some(h),
                        Err(e) => self.report(Err(format!("live probe: {e}"))),
                    }
                }
                if let Some(h) = &self.sweep.live_handle {
                    match crate::sweep::sweep_specs(&self.document, &axis, &[self.sweep.live_value])
                    {
                        Ok(mut pts) => {
                            if let Some(p) = pts.pop() {
                                h.submit(self.sweep.generation, p);
                            }
                        }
                        Err(e) => self.report(Err(format!("live spec: {e}"))),
                    }
                }
            }
        }
        if self.sweep.points.is_empty() {
            ui.label("No completed points yet.");
        } else {
            let accent = crate::visuals::accent(ui);
            let mut pts: Vec<[f64; 2]> = Vec::new();
            let mut failures = 0usize;
            for p in &self.sweep.points {
                match &p.result {
                    Ok(v) => {
                        if let Some(y) = self.sweep.response.extract(v, self.sweep.step_row) {
                            pts.push([p.param, y]);
                        }
                    }
                    Err(_) => failures += 1,
                }
            }
            pts.sort_by(|a, b| a[0].total_cmp(&b[0]));
            egui_plot::Plot::new("sweep-plot")
                .height(220.)
                .allow_scroll(false)
                .x_axis_label(axis_obj.label())
                .y_axis_label(self.sweep.response.label())
                .show(ui, |p| {
                    p.line(egui_plot::Line::new("sweep", pts.clone()).color(accent));
                    p.points(
                        egui_plot::Points::new("sweep points", pts.clone())
                            .radius(4.)
                            .color(accent),
                    );
                });
            if self.sweep.frontier {
                // Live Pareto view: (activity, heat) per landed point at
                // the selected step row; nondominated staircase live.
                let mut pairs: Vec<(f64, f64, String)> = Vec::new();
                for p in &self.sweep.points {
                    if let Ok(v) = &p.result {
                        let a = crate::sweep::SweepResponse::TotalActivityBqPerG
                            .extract(v, self.sweep.step_row);
                        let h = crate::sweep::SweepResponse::TotalHeatWPerG
                            .extract(v, self.sweep.step_row);
                        if let (Some(a), Some(h)) = (a, h) {
                            pairs.push((a, h, p.label.clone()));
                        }
                    }
                }
                if pairs.len() >= 2 {
                    let nd = crate::sweep::nondominated(
                        &pairs.iter().map(|(a, h, _)| (*a, *h)).collect::<Vec<_>>(),
                    );
                    let mut front: Vec<[f64; 2]> = pairs
                        .iter()
                        .zip(&nd)
                        .filter(|(_, d)| **d)
                        .map(|((a, h, _), _)| [*a, *h])
                        .collect();
                    front.sort_by(|x, y| x[0].total_cmp(&y[0]));
                    let all: Vec<[f64; 2]> = pairs.iter().map(|(a, h, _)| [*a, *h]).collect();
                    egui_plot::Plot::new("sweep-frontier")
                        .height(200.)
                        .allow_scroll(false)
                        .x_axis_label("total activity Bq/g")
                        .y_axis_label("total heat W/g")
                        .legend(egui_plot::Legend::default())
                        .show(ui, |p| {
                            p.points(
                                egui_plot::Points::new("landed", all)
                                    .radius(4.)
                                    .color(egui::Color32::from_gray(140)),
                            );
                            p.points(
                                egui_plot::Points::new("nondominated", front.clone())
                                    .radius(5.5)
                                    .color(accent),
                            );
                            p.line(egui_plot::Line::new("frontier", front.clone()).color(accent));
                        });
                    ui.monospace(format!(
                        "{} of {} landed points nondominated — nominal frontier at step {}",
                        front.len(),
                        pairs.len(),
                        self.sweep.step_row + 1
                    ));
                } else {
                    ui.label("frontier needs ≥2 landed points with heat + activity");
                }
            }
            let last = self.sweep.points.last();
            ui.label(format!(
                "{} point(s) plotted · {} failed · generation {}",
                pts.len(),
                failures,
                self.sweep.generation
            ));
            if let Some(p) = last {
                let tier = match (p.cache_hit, p.screen_kept_states) {
                    (Some(hit), Some(kept)) => format!(
                        "screened · {} states kept · cache {}",
                        kept,
                        if hit { "warm" } else { "cold" }
                    ),
                    _ => "isolated worker".to_string(),
                };
                ui.monospace(format!(
                    "latest: {} · {} ms · {} · spec sha256 {}… — every rendered point is bound to the spec that produced it",
                    p.label,
                    p.elapsed_ms,
                    tier,
                    &p.spec_sha256[..16]
                ));
            }
        }
    }

    fn run_sweep(&mut self) {
        use crate::sweep::SweepAxis;
        let axis = match self.sweep.axis {
            0 => SweepAxis::CompositionFraction {
                element: self.sweep.element.clone(),
            },
            1 => SweepAxis::FluxNormalization,
            _ => SweepAxis::CoolingTimeS {
                step_index: self.sweep.step_index,
            },
        };
        let (lo, hi) = (self.sweep.lo, self.sweep.hi);
        let n = self.sweep.count.max(2);
        let values: Vec<f64> = (0..n)
            .map(|i| lo + (hi - lo) * i as f64 / (n - 1) as f64)
            .collect();
        let base = &self.document;
        match crate::sweep::sweep_specs(base, &axis, &values) {
            Err(e) => self.report(Err(format!("sweep setup: {e}"))),
            Ok(points) => {
                // Supersede any running sweep: dropping the old handle
                // cancels its worker and joins the supervisor; the new
                // generation makes any late point inadmissible.
                self.sweep.handle = None;
                self.sweep.generation += 1;
                self.sweep.points.clear();
                let cache = std::env::temp_dir().join(format!(
                    "actinv-sweep-{}-{}",
                    std::process::id(),
                    self.sweep.generation
                ));
                let launched = if self.sweep.screened {
                    crate::sweep::spawn_sweep_screened(
                        points,
                        self.sweep.generation,
                        crate::sweep::SCREEN_BMIN,
                    )
                    .map(|h| (h, "certified-screening"))
                } else {
                    crate::sweep::spawn_sweep(points, self.sweep.generation, cache)
                        .map(|h| (h, "isolated-worker"))
                };
                match launched {
                    Ok((h, tier)) => {
                        self.sweep.handle = Some(h);
                        self.sweep.running = true;
                        self.report(Ok(format!(
                            "Sweep running: {} generated specs through the {} path.",
                            n, tier
                        )));
                    }
                    Err(e) => self.report(Err(format!("sweep launch: {e}"))),
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn only_wall_and_power_steps_block_the_flux_axis() {
        let flux = json!({"schedule": [{"dt": "1 h", "flux": 1.0}, {"dt": "1 d", "flux": 0.0}]});
        assert!(!has_non_flux_drive(&flux));
        for key in ["wall_loading_MW_m2", "power_W_cm3", "power_W_per_g"] {
            let doc = json!({"schedule": [{"dt": "1 h", key: 1.5}]});
            assert!(has_non_flux_drive(&doc), "{key}");
        }
        assert!(!has_non_flux_drive(&json!({})));
    }
}
