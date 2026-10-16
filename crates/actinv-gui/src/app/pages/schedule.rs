//! Irradiation & cooling: the heading and navigation around the shared step editor, and the file
//! chooser that editor (and the Outputs page) asks the shell for.
use super::page_for;
use crate::{
    app::{heading, Desktop},
    nav::Page,
    platform::{Dialog, Platform},
    views::{
        steps::{self, StepsAction},
        Marks, Picker, Pinned,
    },
};
use eframe::egui;
use std::path::Path;

/// The shell's `Picker`: a native dialog through the `Platform`, and the SHA-256 of the chosen
/// file. The path is recorded as chosen (absolute), as the other "Choose and pin" buttons do.
pub(super) struct ShellPicker<'a> {
    platform: &'a dyn Platform,
    /// The folder relative input paths resolve against; not used to rewrite chosen paths.
    #[allow(dead_code)]
    base: &'a Path,
}

impl<'a> ShellPicker<'a> {
    pub(super) fn new(platform: &'a dyn Platform, base: &'a Path) -> Self {
        Self { platform, base }
    }
}

impl Picker for ShellPicker<'_> {
    fn pick(&mut self, title: &str, extensions: &[&str]) -> Option<Pinned> {
        let path = self
            .platform
            .pick_file(Dialog::new().title(title).filter("File", extensions))?;
        let sha256 = actinv_core::flux::sha256_file(&path).ok()?;
        Some(Pinned {
            path: path.to_string_lossy().into_owned(),
            sha256,
        })
    }
}

impl Desktop {
    pub(super) fn schedule(&mut self, ui: &mut egui::Ui) {
        heading(ui,"Irradiation & cooling","Durations accept units such as 5 min, 2 h, or 10 y. Each step is driven by a flux multiplier, a wall loading or a power; a zero flux multiplier is cooling.");
        let mut marks = Marks::default();
        let mut picker = ShellPicker::new(&*self.platform, &self.base);
        let action = steps::show(
            ui,
            &mut self.document,
            &mut self.steps,
            &mut picker,
            &mut marks,
        );
        for (anchor, rect) in marks.0 {
            self.tour.mark(anchor, rect);
        }
        if let StepsAction::Open(target) = action {
            self.page = page_for(target);
        }
        ui.add_space(16.);
        if ui.button("Continue to spectrum >").clicked() {
            self.page = Page::Spectrum;
        }
    }
}
