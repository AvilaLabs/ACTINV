//! Irradiation & cooling: the step list with its per-step drive, the requirements a drive adds
//! (kerma table, density, spectrum reference) and the proportional timeline.
use crate::{
    model::{self, Drive},
    views::{Marks, Picker, Target},
    visuals::BLUE,
};
use eframe::egui::{self, Color32, Vec2};
use serde_json::{json, Value};

/// Slate block of a cooling step in the timeline.
const COOLING: Color32 = Color32::from_rgb(146, 169, 198);

/// Message about the last kerma-table choice that gave nothing.
#[derive(Default)]
pub struct StepsState {
    pin_note: Option<String>,
}

pub enum StepsAction {
    None,
    Open(Target),
}

pub fn show(
    ui: &mut egui::Ui,
    document: &mut Value,
    state: &mut StepsState,
    picker: &mut dyn Picker,
    marks: &mut Marks,
) -> StepsAction {
    let mut action = StepsAction::None;
    let r = ui.scope(|ui| {
        if let Some(steps) = document["schedule"].as_array_mut() {
            rows(ui, steps);
        }
        let kinds = drives(document);
        requirements(ui, document, state, picker, &kinds, &mut action);
        if let Some(steps) = document["schedule"].as_array() {
            ui.add_space(15.);
            timeline(ui, steps);
        }
    });
    marks.mark("schedule", r.response.rect.intersect(ui.clip_rect()));
    action
}

/// The drive of every schedule step, in order.
fn drives(document: &Value) -> Vec<Drive> {
    document["schedule"]
        .as_array()
        .map(|steps| steps.iter().map(model::drive_of).collect())
        .unwrap_or_default()
}

/// Change a step's drive, keeping its number as the starting value. A power must be positive, so
/// a zero (cooling) starts a power step at 1.
fn switch_drive(step: &mut Value, to: Drive) {
    let value = model::drive_value(step);
    let start = if to.is_power() && value <= 0. {
        1.
    } else {
        value
    };
    model::set_drive(step, to, start);
}

/// The value box of a step: edits only the key of the step's current drive.
fn drive_input(ui: &mut egui::Ui, step: &mut Value, drive: Drive) {
    let key = drive.key();
    let mut n = model::number(&step[key]);
    if ui
        .add(
            egui::DragValue::new(&mut n)
                .custom_parser(model::parse_finite)
                .speed(if drive.is_power() { 1. } else { 0.1 })
                .range(0.0..=f64::MAX),
        )
        .changed()
    {
        step[key] = json!(n);
    }
}

fn rows(ui: &mut egui::Ui, steps: &mut Vec<Value>) {
    let mut swap = None;
    let mut remove = None;
    let count = steps.len();
    egui::ScrollArea::vertical()
        .id_salt("schedule-rows")
        .max_height(285.)
        .show(ui, |ui| {
            for (i, step) in steps.iter_mut().enumerate() {
                let (drop, payload) =
                    ui.dnd_drop_zone::<usize, _>(egui::Frame::group(ui.style()), |ui| {
                        // every row the same width, whatever its phase or buttons
                        ui.set_width(ui.available_width());
                        ui.horizontal_wrapped(|ui| {
                            ui.dnd_drag_source(egui::Id::new(("schedule-drag", i)), i, |ui| {
                                ui.label("::").on_hover_text("Drag to reorder");
                            });
                            ui.label(format!("{:02}", i + 1));
                            let mut dt = step["dt"].as_str().unwrap_or("").to_owned();
                            if ui
                                .add(egui::TextEdit::singleline(&mut dt).desired_width(90.))
                                .changed()
                            {
                                step["dt"] = dt.into();
                            }
                            ui.label("Drive");
                            let drive = model::drive_of(step);
                            egui::ComboBox::from_id_salt(("drive", i))
                                .selected_text(drive.label())
                                .show_ui(ui, |ui| {
                                    for choice in Drive::ALL {
                                        if ui
                                            .selectable_label(choice == drive, choice.label())
                                            .clicked()
                                            && choice != drive
                                        {
                                            switch_drive(step, choice);
                                        }
                                    }
                                });
                            let drive = model::drive_of(step);
                            if drive == Drive::Flux {
                                ui.label("×");
                            }
                            drive_input(ui, step, drive);
                            if !drive.unit().is_empty() {
                                ui.label(drive.unit());
                            }
                            ui.add_sized(
                                [90., 20.],
                                egui::Label::new(if model::is_cooling(step) {
                                    "Cooling"
                                } else {
                                    "Irradiation"
                                }),
                            );
                            if ui.add_enabled(i > 0, egui::Button::new("Up")).clicked() {
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
                    });
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
}

/// Zero-based indices of the steps driven by `drive`.
fn steps_with(drives: &[Drive], drive: Drive) -> Vec<usize> {
    drives
        .iter()
        .enumerate()
        .filter(|(_, d)| **d == drive)
        .map(|(i, _)| i)
        .collect()
}

/// "Step 3" or "Steps 3, 5" for zero-based indices.
fn step_list(indices: &[usize]) -> String {
    let numbers: Vec<String> = indices.iter().map(|i| (i + 1).to_string()).collect();
    format!(
        "{} {}",
        if indices.len() == 1 { "Step" } else { "Steps" },
        numbers.join(", ")
    )
}

/// Whether `material.density_g_cm3` is a usable density.
fn has_density(document: &Value) -> bool {
    document["material"]["density_g_cm3"]
        .as_f64()
        .is_some_and(|d| d.is_finite() && d > 0.)
}

fn requirements(
    ui: &mut egui::Ui,
    document: &mut Value,
    state: &mut StepsState,
    picker: &mut dyn Picker,
    drives: &[Drive],
    action: &mut StepsAction,
) {
    let error = ui.visuals().error_fg_color;
    let power = drives.iter().any(|d| d.is_power());
    if power || document["kerma"].is_object() {
        ui.add_space(8.);
        kerma_panel(ui, document, state, picker, power);
    }
    let per_cm3 = steps_with(drives, Drive::PowerPerCm3);
    if !per_cm3.is_empty() && !has_density(document) {
        ui.horizontal(|ui| {
            ui.colored_label(
                error,
                format!(
                    "{} use{} W/cm3: set a density on Material.",
                    step_list(&per_cm3),
                    if per_cm3.len() == 1 { "s" } else { "" }
                ),
            );
            if ui.button("Go to Material").clicked() {
                *action = StepsAction::Open(Target::Material);
            }
        });
    }
    if drives.contains(&Drive::WallLoading) {
        ui.add_space(4.);
        ui.horizontal_wrapped(|ui| {
            ui.label(wall_note(&document["spectrum"]));
            if ui.button("Go to Spectrum").clicked() {
                *action = StepsAction::Open(Target::Spectrum);
            }
        });
    }
    if drives.iter().any(|d| *d != Drive::Flux) {
        ui.add_space(4.);
        #[cfg(not(target_arch = "wasm32"))]
        ui.label("Parameter sweeps over wall-loading or power steps are command-line only; the Sweep panel varies spectrum.total, which does not set those steps.");
        #[cfg(target_arch = "wasm32")]
        ui.label("Parameter sweeps over wall-loading or power steps are command-line only.");
    }
}

/// What a wall-loading step is scaled against.
fn wall_note(spectrum: &Value) -> String {
    match spectrum["wall_loading_MW_m2"].as_f64() {
        Some(reference) => format!(
            "Wall loading: the spectrum is referenced to {reference} MW/m² (spectrum.wall_loading_MW_m2, in Advanced JSON); a step's multiplier is its loading divided by that."
        ),
        None => "Wall loading: the spectrum has no reference loading (spectrum.wall_loading_MW_m2, in Advanced JSON), so ACTINV uses 4.44e13 n/cm²/s per MW/m² and needs a spectrum with a positive total.".into(),
    }
}

/// Make sure `kerma` exists as an object with a `table`, which the schema requires.
fn ensure_kerma(document: &mut Value) {
    if !document["kerma"].is_object() {
        document["kerma"] = json!({"table": {"path": "", "sha256": ""}});
    }
}

/// Write `kerma.table`, creating the section when it is not there yet.
fn set_kerma_table(document: &mut Value, path: &str, sha256: &str) {
    ensure_kerma(document);
    document["kerma"]["table"] = json!({"path": path, "sha256": sha256});
}

fn kerma_complete(document: &Value) -> bool {
    let table = &document["kerma"]["table"];
    let sha = table["sha256"].as_str().unwrap_or("");
    !table["path"].as_str().unwrap_or("").is_empty()
        && sha.len() == 64
        && sha.bytes().all(|b| b.is_ascii_hexdigit())
}

fn kerma_panel(
    ui: &mut egui::Ui,
    document: &mut Value,
    state: &mut StepsState,
    picker: &mut dyn Picker,
    needed: bool,
) {
    if needed && !kerma_complete(document) {
        ui.colored_label(
            ui.visuals().error_fg_color,
            "Power steps need a kerma table, pinned by its SHA-256.",
        );
    }
    let mut path = document["kerma"]["table"]["path"]
        .as_str()
        .unwrap_or("")
        .to_owned();
    let mut sha = document["kerma"]["table"]["sha256"]
        .as_str()
        .unwrap_or("")
        .to_owned();
    ui.horizontal(|ui| {
        ui.label("Kerma table");
        if ui
            .add(
                egui::TextEdit::singleline(&mut path)
                    .desired_width((ui.available_width() - 150.).max(160.)),
            )
            .changed()
        {
            set_kerma_table(document, &path, &sha);
        }
        if ui.button("Choose and pin").clicked() {
            match picker.pick("Kerma table", &["json"]) {
                Some(pinned) => {
                    set_kerma_table(document, &pinned.path, &pinned.sha256);
                    state.pin_note = None;
                }
                None => {
                    state.pin_note = Some("No kerma table was chosen. Pick a file here, or type its path and SHA-256.".into());
                }
            }
        }
    });
    ui.horizontal(|ui| {
        ui.label("SHA-256");
        if ui
            .add(egui::TextEdit::singleline(&mut sha).desired_width(520.))
            .changed()
        {
            set_kerma_table(document, &path, &sha);
        }
    });
    if let Some(note) = &state.pin_note {
        ui.label(note);
    }
    if document["kerma"].is_object() {
        let mut require = document["kerma"]["require_complete"]
            .as_bool()
            .unwrap_or(false);
        ui.horizontal(|ui| {
            if ui
                .checkbox(&mut require, "Require complete coverage")
                .changed()
            {
                document["kerma"]["require_complete"] = require.into();
            }
            if ui.small_button("Remove kerma table").clicked() {
                if let Some(map) = document.as_object_mut() {
                    map.remove("kerma");
                }
            }
        });
    }
    if document["kerma"].is_object() {
        let kerma = &document["kerma"];
        ui.label(format!(
            "Advanced (Advanced JSON): power_tolerance {}, max_substeps {}, top {}",
            kerma["power_tolerance"].as_f64().unwrap_or(1.0e-3),
            kerma["max_substeps"].as_u64().unwrap_or(64),
            kerma["top"].as_u64().unwrap_or(10)
        ));
    }
}

/// Block colour of a step, as before drives existed: slate when it only cools, cobalt when it irradiates.
fn step_colour(step: &Value) -> Color32 {
    if model::is_cooling(step) {
        COOLING
    } else {
        BLUE
    }
}

fn timeline(ui: &mut egui::Ui, steps: &[Value]) {
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
    let (rect, _) =
        ui.allocate_exact_size(Vec2::new(ui.available_width(), 42.), egui::Sense::hover());
    let mut x = rect.left();
    for (i, d) in durations.iter().enumerate() {
        let width = if total > 0. {
            rect.width() * (*d / total) as f32
        } else {
            0.
        };
        let r = egui::Rect::from_min_size(egui::pos2(x, rect.top()), Vec2::new(width, 42.));
        ui.painter()
            .rect_filled(r.shrink(1.), 3., step_colour(&steps[i]));
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
        "Total duration: {} · cobalt = irradiation · slate = cooling",
        model::readable_time(total)
    ));
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::views::Pinned;
    use actinv_core::spec::Spec;

    struct Fixed(Option<(String, String)>);
    impl Picker for Fixed {
        fn pick(&mut self, _title: &str, extensions: &[&str]) -> Option<Pinned> {
            assert_eq!(extensions, ["json"]);
            self.0.clone().map(|(path, sha256)| Pinned { path, sha256 })
        }
    }

    fn document() -> Value {
        model::decode_problem(model::EXAMPLE).unwrap()
    }

    #[test]
    fn switching_drive_keeps_the_number_and_every_other_step_field() {
        let mut step = json!({
            "dt": "1 h", "flux": 2.5, "partition": {"Fe": 0.5}, "feed": {"Co60": 1.},
            "spectrum": {"structure": "fispact-709", "flux_per_group": [1.0]}
        });
        let before = step.clone();
        switch_drive(&mut step, Drive::WallLoading);
        assert_eq!(step["wall_loading_MW_m2"], json!(2.5));
        assert!(step.get("flux").is_none());
        switch_drive(&mut step, Drive::PowerPerGram);
        assert_eq!(step["power_W_per_g"], json!(2.5));
        assert!(step.get("wall_loading_MW_m2").is_none());
        switch_drive(&mut step, Drive::Flux);
        assert_eq!(step, before);
    }

    #[test]
    fn a_cooling_step_starts_a_power_step_at_one() {
        let mut step = json!({"dt": "1 h", "flux": 0.});
        switch_drive(&mut step, Drive::PowerPerCm3);
        assert_eq!(step["power_W_cm3"], json!(1.));
        switch_drive(&mut step, Drive::Flux);
        assert_eq!(step["flux"], json!(1.));
        let mut cooling = json!({"dt": "1 h", "flux": 0.});
        switch_drive(&mut cooling, Drive::WallLoading);
        assert_eq!(cooling["wall_loading_MW_m2"], json!(0.));
    }

    #[test]
    fn a_pinned_kerma_table_and_a_power_step_pass_the_core_validation() {
        let mut doc = document();
        set_kerma_table(&mut doc, "kerma.json", &"0".repeat(64));
        assert!(kerma_complete(&doc));
        model::set_drive(&mut doc["schedule"][0], Drive::PowerPerGram, 2.);
        let spec = Spec::from_json(&doc.to_string()).unwrap();
        assert!(spec.schedule[0].is_power_driven());
        assert_eq!(spec.kerma.unwrap().table.path, "kerma.json");
        // W/cm3 additionally needs the density that the banner asks for
        model::set_drive(&mut doc["schedule"][0], Drive::PowerPerCm3, 2.);
        assert!(!has_density(&doc));
        assert!(Spec::from_json(&doc.to_string())
            .unwrap_err()
            .contains("density_g_cm3"));
        doc["material"]["density_g_cm3"] = json!(7.874);
        assert!(has_density(&doc));
        assert!(Spec::from_json(&doc.to_string()).is_ok());
    }

    #[test]
    fn kerma_stub_is_valid_json_for_the_schema_and_incomplete_until_pinned() {
        let mut doc = document();
        ensure_kerma(&mut doc);
        assert!(!kerma_complete(&doc));
        // the section deserializes (table present) and is then refused by validation, not by serde
        let error = Spec::from_json(&doc.to_string()).unwrap_err();
        assert!(error.contains("kerma.table"), "{error}");
        set_kerma_table(&mut doc, "k.json", &"a".repeat(63));
        assert!(!kerma_complete(&doc));
        set_kerma_table(&mut doc, "k.json", &"a".repeat(64));
        assert!(kerma_complete(&doc));
        assert!(Spec::from_json(&doc.to_string()).is_ok());
    }

    #[test]
    fn step_lists_read_naturally() {
        assert_eq!(step_list(&[2]), "Step 3");
        assert_eq!(step_list(&[2, 4]), "Steps 3, 5");
        let drives = [
            Drive::Flux,
            Drive::PowerPerCm3,
            Drive::PowerPerGram,
            Drive::PowerPerCm3,
        ];
        assert_eq!(steps_with(&drives, Drive::PowerPerCm3), vec![1, 3]);
    }

    #[test]
    fn wall_note_names_the_reference_or_the_default_current() {
        assert!(wall_note(&json!({"wall_loading_MW_m2": 1.5})).contains("1.5 MW/m²"));
        assert!(wall_note(&json!({})).contains("4.44e13"));
    }

    fn render(doc: &mut Value, picker: &mut dyn Picker) -> (StepsAction, Marks) {
        let ctx = egui::Context::default();
        ctx.memory_mut(|m| m.set_everything_is_visible(true));
        let mut state = StepsState::default();
        let mut marks = Marks::default();
        let mut action = StepsAction::None;
        let mut output = ctx.run_ui(Default::default(), |ui| {
            action = show(ui, doc, &mut state, picker, &mut marks);
        });
        output.textures_delta.clear();
        (action, marks)
    }

    #[test]
    fn the_view_renders_every_drive_without_touching_the_document() {
        let mut doc = document();
        doc["schedule"] = json!([
            {"dt": "1 h", "flux": 1., "partition": {"Fe": 0.5}},
            {"dt": "1 h", "wall_loading_MW_m2": 1.5},
            {"dt": "1 h", "flux": 0., "power_W_cm3": 150.},
            {"dt": "1 h", "flux": 0., "power_W_per_g": 20.},
            {"dt": "10 y", "flux": 0.}
        ]);
        let before = doc.clone();
        let (action, marks) = render(&mut doc, &mut Fixed(None));
        assert_eq!(doc, before, "rendering must not edit or rebuild a step");
        assert!(matches!(action, StepsAction::None));
        assert!(marks.0.iter().any(|(name, _)| *name == "schedule"));
    }

    #[test]
    fn the_unchanged_example_schedule_renders_unchanged() {
        let mut doc = document();
        let before = doc.clone();
        let (_, marks) = render(&mut doc, &mut Fixed(None));
        assert_eq!(doc, before);
        assert_eq!(marks.0.len(), 1);
    }
}
