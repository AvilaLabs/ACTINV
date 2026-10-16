//! Spectrum: group values, normalization, boundaries and transport import.
use crate::{
    app::{heading, numeric, text_field, Desktop, Job, JobKind, JobOutput},
    model,
    platform::Dialog,
};
use eframe::egui;
use egui_plot::{Line, Plot};
use serde_json::json;
use std::{sync::mpsc, time::Instant};

impl Desktop {
    pub(super) fn spectrum(&mut self, ui: &mut egui::Ui) {
        let accent = crate::visuals::accent(ui);
        heading(
            ui,
            "Incident spectrum",
            "Review group values and explicitly choose the normalization used by the solver.",
        );
        let r=ui.scope(|ui|{
            ui.horizontal(|ui|{ui.label("Group structure");text_field(ui,&mut self.document["spectrum"]["structure"]);});
            let mut normalized=self.document["spectrum"]["total"].is_number();if ui.checkbox(&mut normalized,"Normalize group values to a total flux (particles / cm^2 / s)").changed(){if normalized{self.document["spectrum"]["total"]=json!(1e12);}else{self.document["spectrum"].as_object_mut().unwrap().remove("total");}}
            if normalized {numeric(ui,&mut self.document["spectrum"]["total"],1e10);}else{ui.label("Group values are absolute group fluxes (particles / cm^2 / s).");}
            let mut descending=self.document["spectrum"]["descending"].as_bool().unwrap_or(false);if ui.checkbox(&mut descending,"Input groups are highest-energy first").changed(){self.document["spectrum"]["descending"]=descending.into();}
            let points:Vec<[f64;2]>=self.document["spectrum"]["flux_per_group"].as_array().map(|a|a.iter().enumerate().map(|(i,v)|[i as f64+1.,model::number(v)]).collect()).unwrap_or_default();
            Plot::new("incident").height(260.).allow_scroll(false).x_axis_label("Input group index").y_axis_label(if normalized{"Relative group weight"}else{"Group flux / cm^2 / s"}).show(ui,|p|p.line(Line::new("Incident spectrum",points).color(accent)));
            ui.collapsing("Edit group values",|ui|{if let Some(values)=self.document["spectrum"]["flux_per_group"].as_array_mut(){egui::ScrollArea::vertical().max_height(200.).show_rows(ui,28.,values.len(),|ui,range|{for i in range {ui.horizontal(|ui|{ui.label(format!("Group {}",i+1));numeric(ui,&mut values[i],1.);});}});}});
            ui.label("Custom boundaries and optional photon, uncertainty, or response settings are available in Advanced JSON.");
            ui.collapsing("Import or paste group values", |ui| {
                ui.label("Paste one flux column from a spreadsheet, or open a one-column CSV/text file. Optional header: flux. Set normalization and energy order above explicitly.");
                if ui.button("Open CSV / text").clicked() {
                    if let Some(path) = self.platform.pick_file(Dialog::new().filter("Spectrum", &["csv", "txt"])) {
                        match std::fs::read_to_string(path) { Ok(s) => self.spectrum_text = s, Err(e) => self.report(Err(e.to_string())) }
                    }
                }
                ui.add(egui::TextEdit::multiline(&mut self.spectrum_text).desired_rows(5).hint_text("flux\n1.2e10\n3.4e9\n…"));
                if ui.button("Check and apply group values").clicked() {
                    let named = self.document["spectrum"]["structure"].as_str().map(actinv_data::groups::GroupStructure::named);
                    let count = match named {
                        Some(Ok(Some(group))) => group.groups(),
                        _ => self.document["spectrum"]["boundaries_eV"].as_array().map_or(0, |v| v.len().saturating_sub(1)),
                    };
                    match model::parse_group_values(&self.spectrum_text,count) {
                        Ok(values) => { self.document["spectrum"]["flux_per_group"] = json!(values); self.report(Ok(format!("Imported {count} group values. Review normalization and energy order before running."))); },
                        Err(e) => self.report(Err(e)),
                    }
                }
            });
        });
        if self.document["spectrum"]["structure"].as_str() == Some("custom") {
            crate::options::boundaries(
                ui,
                &mut self.document["spectrum"]["boundaries_eV"],
                "incident-boundaries",
            );
        }
        egui::CollapsingHeader::new("Spectrum by physical energy")
            .open(self.capture.as_ref().map(|_| true))
            .show(ui, |ui| crate::visuals::spectrum(ui, &self.document));
        let request = ui
            .add_enabled_ui(self.job.is_none(), |ui| {
                self.transport.show(ui, &self.document)
            })
            .inner;
        if let Some(request) = request {
            let (tx, rx) = mpsc::channel();
            let ctx = ui.ctx().clone();
            std::thread::spawn(move || {
                let _ = tx.send(crate::transport::run(request).map(JobOutput::Spectrum));
                ctx.request_repaint();
            });
            self.job = Some(Job {
                rx: Some(rx),
                handle: None,
                started: Instant::now(),
                kind: JobKind::Transport,
                cancelled: false,
            });
            self.report(Ok(
                "Reading transport data and checking the complete stream…".into(),
            ));
        }
        if let Some(preview) = &self.imported {
            ui.label(&preview.description);
            if ui.button("Apply imported absolute spectrum").clicked() {
                self.document["spectrum"] = preview.spectrum.clone();
                self.imported = None;
            }
        }
        self.tour
            .mark("spectrum", r.response.rect.intersect(ui.clip_rect()));
    }
}
