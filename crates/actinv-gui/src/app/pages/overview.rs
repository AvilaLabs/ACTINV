//! Overview & data: nuclear data locations, projectile, solver options and requested outputs.
use crate::{
    app::{heading, path_field, text_field, Desktop, Job, JobKind, JobOutput, ResultAction},
    model,
    nav::Page,
    platform::Dialog,
};
use eframe::egui;
use serde_json::json;
use std::{path::PathBuf, sync::mpsc, time::Instant};

impl Desktop {
    pub(super) fn setup(&mut self, ui: &mut egui::Ui) {
        heading(
            ui,
            "Set up a calculation",
            "Choose your evaluated data, then define the material and irradiation history.",
        );
        ui.group(|ui| {
            ui.strong("Welcome to ACTINV");
            ui.label("Explore a small teaching result offline, or use the complete iron problem below for your first real calculation.");
            if ui.button("Try offline results tutorial").clicked() {
                if self.result_unsaved { self.result_guard = Some(ResultAction::Tutorial); }
                else { self.result = Some(model::tutorial_result()); self.step = 0; self.selected.clear(); self.comparison = None; self.page = Page::Results; self.reset_plot = true; self.tour.start(true); }
            }
            ui.label("Standard data: about 139 MiB download, 229 MiB installed. Each calculation run uses a private cache (about 282 MiB for the iron example).");
        });
        ui.label("Calculation title");
        text_field(ui, &mut self.document["title"]);
        ui.add_space(12.);
        let response=egui::Frame::group(ui.style()).inner_margin(16.).show(ui,|ui| {
            ui.heading("Nuclear data");
            ui.label("Paths are resolved against the input base folder. Existing CLI examples commonly use the repository root.");
            ui.horizontal(|ui|{ui.label("Input base");let mut base=self.base.to_string_lossy().into_owned();if ui.add(egui::TextEdit::singleline(&mut base).desired_width((ui.available_width()-220.).max(150.))).changed(){self.base=PathBuf::from(base);}
if ui.button("Choose folder").clicked(){if let Some(p)=self.platform.pick_folder(Dialog::new()){self.base=p;}}});
            path_field(ui,"Activation library (.npz)",&mut self.document["library"]["path"],&*self.platform);
            ui.collapsing("Activation library SHA-256 pin",|ui|{
                ui.label("A declared hash must match the chosen library. Update or remove the pin when intentionally switching libraries. The solver always records the computed hash.");
                let mut hash=self.document["library"]["sha256"].as_str().unwrap_or("").to_owned();
                if ui.add(egui::TextEdit::singleline(&mut hash).desired_width(500.).hint_text("Optional SHA-256")).changed(){if hash.trim().is_empty(){self.document["library"].as_object_mut().unwrap().remove("sha256");}else{self.document["library"]["sha256"]=hash.into();}}
            });
            path_field(ui,"Primary decay data",&mut self.document["decay"]["primary"],&*self.platform);
            if self.document["decay"]["fallback"].is_string(){path_field(ui,"Fallback decay data",&mut self.document["decay"]["fallback"],&*self.platform);if ui.small_button("Remove fallback").clicked(){self.document["decay"].as_object_mut().unwrap().remove("fallback");}}
            else if ui.small_button("Add fallback decay data").clicked(){self.document["decay"]["fallback"]="".into();}
            if ui.add_enabled(self.job.is_none(),egui::Button::new("Download standard neutron data · 139 MiB…")).clicked() {
                if let Some(folder)=self.platform.pick_folder(Dialog::new().title("Choose the parent folder for actinv-data")) {
                    let (tx,rx)=mpsc::channel();let ctx=ui.ctx().clone();
                    std::thread::spawn(move||{let output=actinv_cli::fetch_bundle(None,folder.join("actinv-data"),false).map(|s|JobOutput::Data(s.problem_fragment));let _=tx.send(output);ctx.request_repaint();});
                    self.job=Some(Job{rx:Some(rx),handle:None,started:Instant::now(),kind:JobKind::Data,cancelled:false});self.status="Downloading and verifying standard neutron data… Files are checked before installation; cancellation is unavailable while files are being verified.".into();self.error=false;
                }
            }
            if let Some(fragment)=&self.downloaded {
                ui.label("Verified neutron data are ready. Applying paths keeps your material and spectrum; use a compatible neutron 709-group spectrum.");
                if ui.button("Use downloaded library & decay paths").clicked(){
                    self.document["library"]=fragment["library"].clone();self.document["decay"]=fragment["decay"].clone();
                    self.document["projectile"]=fragment["projectile"].clone();
                }
            }
            ui.collapsing("Need the standard nuclear data?",|ui|{ui.label("Run this once in a terminal, then select the downloaded files above:");ui.code("actinv data fetch");ui.label("The standard download includes neutron activation and decay data. Data are versioned separately from the application.");});
        });
        self.tour
            .mark("inputs", response.response.rect.intersect(ui.clip_rect()));
        ui.add_space(16.);
        ui.horizontal(|ui| {
            ui.label("Projectile");
            let p = self.document["projectile"]
                .as_str()
                .unwrap_or("neutron")
                .to_owned();
            egui::ComboBox::from_id_salt("projectile")
                .selected_text(&p)
                .show_ui(ui, |ui| {
                    for v in ["neutron", "proton", "deuteron", "triton", "helion", "alpha"] {
                        if ui.selectable_label(p == v, v).clicked() {
                            self.document["projectile"] = v.into();
                        }
                    }
                });
        });
        ui.add_space(16.);
        ui.collapsing("Calculation options & requested outputs",|ui|{
            if self.document["options"].is_null(){self.document["options"]=json!({});}
            let mut mode=self.document["options"]["mode"].as_str().unwrap_or("auto").to_owned();
            ui.horizontal(|ui|{ui.label("Solver mode");egui::ComboBox::from_id_salt("mode").selected_text(&mode).show_ui(ui,|ui|{for m in ["auto","trace","coupled"]{if ui.selectable_value(&mut mode,m.into(),m).changed(){self.document["options"]["mode"]=mode.clone().into();}}});});
            ui.label("Trace mode supports ranked production pathways. Auto chooses an appropriate mode based on the problem.");
            let mut temperature=self.document["options"]["temperature_K"].as_f64().unwrap_or(293.6);
            ui.horizontal(|ui|{ui.label("Temperature (K)");if ui.add(egui::DragValue::new(&mut temperature).custom_parser(model::parse_finite).speed(1.).range(0.0..=f64::MAX)).changed(){self.document["options"]["temperature_K"]=json!(temperature);}});
            let mut outputs=self.document["options"]["outputs"].as_array().cloned().unwrap_or_else(||vec![json!("inventory"),json!("activity"),json!("heat")]);
            ui.horizontal_wrapped(|ui|{for (key,label) in [("inventory","Inventory"),("activity","Activity"),("heat","Decay heat"),("photons","Photon spectrum"),("pathways","Production pathways")]{let mut enabled=outputs.iter().any(|v|v.as_str()==Some(key));if ui.checkbox(&mut enabled,label).changed(){outputs.retain(|v|v.as_str()!=Some(key));if enabled{outputs.push(json!(key));}self.document["options"]["outputs"]=json!(outputs);}}});
        });
        if ui.button("Continue to material >").clicked() {
            self.page = Page::Material;
        }
        crate::options::show(ui, &mut self.document);
    }
}
