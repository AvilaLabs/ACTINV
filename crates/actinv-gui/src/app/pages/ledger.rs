//! Ledger & certificate: filterable evidence trees.
use crate::app::{heading, json_tree, Desktop};
use eframe::egui;

impl Desktop {
    pub(super) fn ledger(&mut self, ui: &mut egui::Ui) {
        heading(
            ui,
            "Evidence",
            "The ledger of missing data and approximations, and the certificate of inputs and data behind the answer.",
        );
        self.result_bar(ui);
        let r = ui.scope(|ui| {
            if let Some(result) = &self.result {
                ui.add(egui::TextEdit::singleline(&mut self.ledger_filter).hint_text("Filter evidence by field or value…").desired_width(360.));
                if self.ledger_filter.is_empty(){
                    json_tree(ui, "Ledger", &result.value["ledger"]);
                    json_tree(ui, "Certificate", &result.value["certificate"]);
                }else{
                    let query=self.ledger_filter.to_lowercase();let mut found=false;
                    for section in ["ledger","certificate"]{ui.heading(section);if let Some(entries)=result.value[section].as_object(){for (key,value) in entries{if key.to_lowercase().contains(&query)||value.to_string().to_lowercase().contains(&query){found=true;json_tree(ui,key,value);}}}}
                    if !found{ui.label("No matching evidence fields. Try a nuclide name, a file hash, or a category such as decay.");}
                }
                // The MF=33 record of the selected step; the shaded band on Charts is drawn from it.
                if let Some(step) = result.steps().get(self.step) {
                    if !step["uncertainty"].is_null() {
                        json_tree(ui, "Uncertainty record", &step["uncertainty"]);
                    }
                }
            } else {
                ui.label("Run a calculation or open a result to inspect its evidence.");
            }
        });
        self.tour
            .mark("ledger", r.response.rect.intersect(ui.clip_rect()));
    }
}
