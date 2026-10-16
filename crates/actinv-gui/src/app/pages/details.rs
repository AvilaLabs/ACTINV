//! Spectra & pathways: decay photon groups and recorded production pathways.
use crate::{
    app::{heading, json_tree, Desktop, BLUE},
    model,
};
use eframe::egui::{self, Color32, Vec2};
use egui_plot::Plot;
use serde_json::Value;

impl Desktop {
    pub(super) fn details(&mut self, ui: &mut egui::Ui) {
        let accent = crate::visuals::accent(ui);
        heading(
            ui,
            "Spectra & production pathways",
            "Explore optional outputs recorded by the calculation.",
        );
        let r=ui.scope(|ui|{
            let Some(result)=&self.result else{ui.label("Open a result in Results, or run a calculation first.");return;};
            ui.add(egui::Slider::new(&mut self.step,0..=result.steps().len()-1).text("Computed step").custom_formatter(|v,_|format!("{}",v as usize+1)));let step=&result.steps()[self.step];
            ui.heading("Decay photon source");
            if step["photon_source"].is_null(){ui.label("No photon source was requested. Add \"photons\" to options.outputs in Advanced JSON, then run again.");}else{
                if let Some(groups)=step["photon_source"]["groups"].as_array(){
                    if groups.iter().any(|g|model::number(&g["photons_s_g"])>0.) {
                    let bars:Vec<_>=groups.iter().map(|g|egui_plot::Bar::new(model::number(&g["centroid_eV"])/1e6,model::number(&g["photons_s_g"])).width((model::number(&g["high_eV"])-model::number(&g["low_eV"]))/1e6)).collect();
                    Plot::new("photon-groups").height(230.).allow_scroll(false).x_axis_label("Photon energy (MeV)").y_axis_label("Photons / s / g per group").show(ui,|p|p.bar_chart(egui_plot::BarChart::new("Decay photons",bars).color(accent)));
                }
                    else {ui.label("No grouped photon emission is recorded at this step. Review the photon source details and ledger for missing evaluated spectra or unrepresented emission.");}
                }
                json_tree(ui,"Photon source",&step["photon_source"]);
            }
            ui.separator();ui.heading("Production pathways");
            if let Some(pathways)=result.value["pathways"].as_array().and_then(|p|p.get(self.step)).and_then(Value::as_object){
                if pathways.is_empty(){ui.label("No production pathways were recorded for this step. Pathway decomposition is available in trace mode.");}
                if !pathways.contains_key(&self.selected){self.selected=pathways.keys().next().cloned().unwrap_or_default();self.scene_view=egui::Rect::ZERO;}
                egui::ComboBox::from_id_salt("pathway-isotope").selected_text(if self.selected.is_empty(){"Choose a nuclide"}else{&self.selected}).show_ui(ui,|ui|{for key in pathways.keys(){ui.selectable_value(&mut self.selected,key.clone(),key);}});
                if let Some(paths)=pathways.get(&self.selected).and_then(Value::as_array){
                    ui.horizontal(|ui|{ui.label("Drag to pan; scroll to zoom.");if ui.small_button("Fit pathways").clicked(){self.scene_view=egui::Rect::ZERO;}});
                    ui.allocate_ui(Vec2::new(ui.available_width(),240.),|ui|{
                        egui::Scene::new().zoom_range(0.2..=2.).show(ui,&mut self.scene_view,|ui|{
                    let (rect,_)=ui.allocate_exact_size(Vec2::new(700.,(paths.len().min(12) as f32*52.).max(70.)),egui::Sense::hover());
                    for (i,path) in paths.iter().take(12).enumerate(){
                        draw_pathway(ui,rect,i,path,&self.selected,&mut self.pathway_node);
                    }
                        });
                    });
                    if !self.pathway_node.is_empty(){
                        let atom=step["inventory"].as_array().and_then(|rows|rows.iter().find(|r|r["nuclide"].as_str()==Some(&self.pathway_node))).and_then(|r|r["atoms_per_g"].as_f64());
                        ui.group(|ui|{ui.strong(format!("{} at step {}",self.pathway_node,self.step+1));if let Some(atoms)=atom{ui.label(format!("{atoms:.6e} atoms/g"));}else{ui.label("No inventory entry was recorded for this node at this step.");}
if let Some(activity)=step["activity_Bq_per_g"][&self.pathway_node].as_f64(){ui.label(format!("{activity:.6e} Bq/g"));}});
                    }
                    ui.label("Source > first product > selected nuclide. Intermediate chain members are not enumerated in this result format. Diagram shows up to 12 ranked contributions.");json_tree(ui,"All contributions",&Value::Array(paths.clone()));
                }
            }else{ui.label("No pathway output is present.");}
            for key in ["uncertainty","radiological"]{if !step[key].is_null(){json_tree(ui,key,&step[key]);}}
        });
        self.tour
            .mark("details", r.response.rect.intersect(ui.clip_rect()));
    }
}

fn draw_pathway(
    ui: &mut egui::Ui,
    rect: egui::Rect,
    index: usize,
    path: &Value,
    selected: &str,
    inspected: &mut String,
) {
    let source = path["from"].as_str().unwrap_or("?");
    let first = path["first_product"].as_str().unwrap_or("?");
    let names = if first == selected {
        vec![source, selected]
    } else {
        vec![source, first, selected]
    };
    let y = rect.top() + 26. + index as f32 * 52.;
    let xs: Vec<_> = (0..names.len())
        .map(|i| rect.left() + 65. + (rect.width() - 130.) * i as f32 / (names.len() - 1) as f32)
        .collect();
    for i in 0..names.len() - 1 {
        let from = egui::pos2(xs[i] + 47., y);
        let to = egui::pos2(xs[i + 1] - 50., y);
        ui.painter()
            .line_segment([from, to], egui::Stroke::new(1.5, BLUE));
        ui.painter().add(egui::Shape::convex_polygon(
            vec![to, to + Vec2::new(-9., -5.), to + Vec2::new(-9., 5.)],
            BLUE,
            egui::Stroke::NONE,
        ));
        if i == 1 {
            ui.painter().text(
                egui::pos2((from.x + to.x) / 2., y - 12.),
                egui::Align2::CENTER_CENTER,
                "via chain",
                egui::FontId::proportional(10.),
                Color32::DARK_GRAY,
            );
        }
    }
    for (i, (x, name)) in xs.into_iter().zip(names).enumerate() {
        let node = egui::Rect::from_center_size(egui::pos2(x, y), Vec2::new(95., 32.));
        if ui
            .interact(
                node,
                egui::Id::new(("pathway-node", index, i)),
                egui::Sense::click(),
            )
            .on_hover_text("Click to inspect this nuclide at the selected step")
            .clicked()
        {
            *inspected = name.into();
        }
        ui.painter()
            .rect_filled(node, 5., Color32::from_rgb(229, 226, 250));
        ui.painter().text(
            node.center(),
            egui::Align2::CENTER_CENTER,
            name,
            egui::FontId::proportional(14.),
            BLUE,
        );
    }
    ui.painter().text(
        egui::pos2(rect.center().x, y + 20.),
        egui::Align2::CENTER_TOP,
        format!(
            "{:.2}% of product atoms",
            100. * model::number(&path["fraction"])
        ),
        egui::FontId::proportional(10.),
        Color32::DARK_GRAY,
    );
}
