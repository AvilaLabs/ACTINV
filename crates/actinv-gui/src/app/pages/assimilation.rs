//! Assimilation: fold a measurement into the loaded result's certified bands.
use crate::{
    app::{heading, Desktop, BLUE},
    model::ResultDocument,
    platform::Dialog,
};
use eframe::egui::{self, Color32, RichText};
use egui_extras::{Column, TableBuilder};
use serde_json::{json, Value};

impl Desktop {
    /// Assay panel: fold an `actinv-assay-1` document (scalar, entries,
    /// or mixture) into the loaded result's certified bands through the
    /// same fusion path the CLI `assimilate` command runs. Preview
    /// shows the per-response update before Apply commits it.
    pub(super) fn assimilation(&mut self, ui: &mut egui::Ui) {
        heading(
            ui,
            "Assimilation",
            "Fold a measured value into the uncertainty bands of the loaded result.",
        );
        if self.result.is_none() {
            ui.group(|ui| {
                ui.add_space(30.);
                ui.heading("Your results will appear here");
                ui.label("Run a calculation or open a result JSON, then fold a measurement into its bands.");
                if ui.button("Open result").clicked() {
                    self.open_result(ui.ctx(), false);
                }
                ui.add_space(30.);
            });
            return;
        }
        ui.group(|ui| {
            ui.label(RichText::new("Fold a measurement into these bands").strong());
            ui.label(
                "Paste an actinv-assay-1 assay — scalar fields, an `entries` list (multi-nuclide count), or a `mixture` (linear-combination measurement) — then preview the Kalman update before applying it.",
            );
            ui.horizontal(|ui| {
                if ui.button("Load assay file…").clicked() {
                    if let Some(p) = self
                        .platform
                        .pick_file(Dialog::new().filter("JSON", &["json"]))
                    {
                        match std::fs::read_to_string(&p) {
                            Ok(text) => {
                                self.assay_text = text;
                                self.assay_pending = None;
                            }
                            Err(e) => self
                                .report(Err(format!("cannot read {}: {e}", p.display()))),
                        }
                    }
                }
                let preview = ui.add_enabled(
                    !self.assay_text.trim().is_empty(),
                    egui::Button::new("Preview fusion"),
                );
                if preview.clicked() {
                    self.assay_pending = None;
                    let res = &self.result.as_ref().expect("checked").value;
                    let fused = serde_json::from_str::<Value>(&self.assay_text)
                        .map_err(|e| e.to_string())
                        .and_then(|a| {
                            actinv_cli::assimilate::fuse_document(res, &a, "workbench")
                        });
                    match fused {
                        Ok(pending) => {
                            self.assay_pending = Some(pending);
                        }
                        Err(e) => self.report(Err(format!("assay fusion: {e}"))),
                    }
                }
                let apply = ui.add_enabled(
                    self.assay_pending.is_some(),
                    egui::Button::new("Apply to result"),
                );
                if apply.clicked() {
                    if let Some((_, updated)) = self.assay_pending.take() {
                        let label = format!(
                            "{} · assay fused",
                            self.result
                                .as_ref()
                                .map(|r| r.label.as_str())
                                .unwrap_or_default()
                        );
                        match ResultDocument::parse(updated, label) {
                            Ok(doc) => {
                                self.result = Some(doc);
                                self.result_unsaved = true;
                                self.report(Ok(
                                    "Assay applied — the certified bands now carry the measurement."
                                        .into(),
                                ));
                            }
                            Err(e) => {
                                self.report(Err(format!("fused result rejected: {e}")))
                            }
                        }
                    }
                }
            });
            ui.add(
                egui::TextEdit::multiline(&mut self.assay_text)
                    .hint_text(
                        "{\n  \"schema\": \"actinv-assay-1\",\n  \"response\": \"heat.total\",\n  \"time_s\": 3600,\n  \"value\": 1.2e-6,\n  \"standard_uncertainty\": 3e-8\n}",
                    )
                    .desired_rows(5)
                    .font(egui::TextStyle::Monospace),
            );
            if let Some((summary, _)) = &self.assay_pending {
                let verdict = summary["verdict"].as_str().unwrap_or("assimilated");
                let color = match verdict {
                    "consistent" => Color32::from_rgb(0, 120, 60),
                    "marginal" => Color32::from_rgb(160, 110, 0),
                    "conflict" => Color32::from_rgb(180, 30, 30),
                    _ => BLUE,
                };
                ui.horizontal(|ui| {
                    ui.colored_label(color, RichText::new(verdict).strong());
                    if let Some(mc) = summary["measured_combination"].as_object() {
                        ui.label(format!(
                            "measured {:.4e} · forward {:.4e} · innovation {:.3} ln",
                            mc["value"].as_f64().unwrap_or(f64::NAN),
                            mc["forward_value"].as_f64().unwrap_or(f64::NAN),
                            mc["innovation_ln"].as_f64().unwrap_or(f64::NAN),
                        ));
                    }
                });
                let rows: Vec<Value> = if let Some(u) = summary["updates"].as_array() {
                    u.iter()
                        .map(|u| {
                            json!({
                                "response": u["response"],
                                "prior": u["prior"]["nominal"],
                                "posterior": u["update"]["posterior"],
                                "gain": u["update"]["kalman_gain"],
                                "verdict": u["verdict"],
                            })
                        })
                        .collect()
                } else if let Some(ts) = summary["terms"].as_array() {
                    ts.iter()
                        .map(|t| {
                            json!({
                                "response": t["response"],
                                "share": t["share_of_combination"],
                                "prior": t["prior_nominal"],
                                "posterior": t["posterior"],
                                "gain": t["kalman_gain"],
                                "verdict": summary["verdict"],
                            })
                        })
                        .collect()
                } else {
                    Vec::new()
                };
                TableBuilder::new(ui)
                    .striped(true)
                    .column(Column::auto())
                    .column(Column::auto())
                    .column(Column::auto())
                    .column(Column::auto())
                    .column(Column::auto())
                    .column(Column::auto())
                    .header(18.0, |mut h| {
                        for c in
                            ["response", "share", "prior", "posterior", "gain", "verdict"]
                        {
                            h.col(|ui| {
                                ui.label(RichText::new(c).strong());
                            });
                        }
                    })
                    .body(|mut b| {
                        for row in &rows {
                            b.row(16.0, |mut r| {
                                r.col(|ui| {
                                    ui.monospace(row["response"].as_str().unwrap_or(""));
                                });
                                r.col(|ui| {
                                    let s = row["share"].as_f64();
                                    ui.monospace(
                                        s.map(|v| format!("{v:.3}"))
                                            .unwrap_or_else(|| "—".into()),
                                    );
                                });
                                for k in ["prior", "posterior", "gain"] {
                                    r.col(|ui| {
                                        let v = row[k].as_f64();
                                        ui.monospace(
                                            v.map(|v| format!("{v:.4e}"))
                                                .unwrap_or_else(|| "—".into()),
                                        );
                                    });
                                }
                                r.col(|ui| {
                                    ui.monospace(row["verdict"].as_str().unwrap_or(""));
                                });
                            });
                        }
                    });
                if summary["induced_correlations"].is_object() {
                    ui.label(
                        "Mixture terms are correlated after this update — the emitted record carries the induced matrix.",
                    );
                }
            }
        });
    }
}
