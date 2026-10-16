//! Native helpers for optional scientific inputs. The requested-output cards (uncertainty,
//! regulatory tables, photon settings) live in `views::outputs`, which the browser shares; this
//! module keeps what only the desktop shell needs.
use crate::{
    platform::{Dialog, Native, Platform},
    views::{Marks, Picker, Pinned},
};
use eframe::egui;
use serde_json::{json, Value};

/// File chooser for the shared views, answered by the shell's dialogs. The hash is computed here,
/// so a pinned path and its SHA-256 always agree.
#[cfg_attr(not(test), allow(dead_code))]
pub struct PlatformPicker<'a>(pub &'a dyn Platform);

impl Picker for PlatformPicker<'_> {
    fn pick(&mut self, title: &str, extensions: &[&str]) -> Option<Pinned> {
        let mut dialog = Dialog::new().title(title);
        if !extensions.is_empty() {
            dialog = dialog.filter("Files", extensions);
        }
        let path = self.0.pick_file(dialog)?;
        let sha256 = actinv_core::flux::sha256_file(&path).ok()?;
        Some(Pinned {
            path: path.to_string_lossy().into_owned(),
            sha256,
        })
    }
}

/// A hash-pinned file reference (`{"path", "sha256"}`) with a chooser. Nothing is written until
/// the user edits, so a missing reference stays missing.
pub fn reference(ui: &mut egui::Ui, value: &mut Value, platform: &dyn Platform) {
    let mut path = value["path"].as_str().unwrap_or("").to_owned();
    let mut hash = value["sha256"].as_str().unwrap_or("").to_owned();
    let (old_path, old_hash) = (path.clone(), hash.clone());
    let mut pinned = None;
    let mut failure = None;
    ui.horizontal(|ui| {
        ui.label("File");
        ui.text_edit_singleline(&mut path);
        if ui.button("Choose and pin file").clicked() {
            if let Some(chosen) = platform.pick_file(Dialog::new()) {
                match actinv_core::flux::sha256_file(&chosen) {
                    Ok(sha256) => pinned = Some((chosen.to_string_lossy().into_owned(), sha256)),
                    Err(error) => failure = Some(error),
                }
            }
        }
    });
    ui.horizontal(|ui| {
        ui.label("SHA-256");
        ui.text_edit_singleline(&mut hash);
    });
    if let Some(error) = failure {
        ui.colored_label(ui.visuals().error_fg_color, error);
    }
    if let Some((path, sha256)) = pinned {
        *value = json!({"path": path, "sha256": sha256});
    } else if path != old_path || hash != old_hash {
        if !value.is_object() {
            *value = json!({"path": "", "sha256": ""});
        }
        value["path"] = path.into();
        value["sha256"] = hash.into();
    }
}

/// Ascending boundaries in eV, applied explicitly; the editor itself is shared with the Outputs page.
pub fn boundaries(ui: &mut egui::Ui, value: &mut Value, id: &str) {
    if let Some(applied) = crate::views::outputs::boundaries_edit(ui, value, id) {
        *value = applied;
    }
}

/// The Outputs page body with the native file dialogs, for callers that have no state of their own.
/// Drafts are not kept between frames.
#[cfg_attr(not(test), allow(dead_code))]
pub fn show(ui: &mut egui::Ui, doc: &mut Value) {
    let mut state = crate::views::outputs::OutputsState::default();
    let mut marks = Marks::default();
    let mut picker = PlatformPicker(&Native);
    crate::views::outputs::show(ui, doc, &mut state, &mut picker, &mut marks);
}
