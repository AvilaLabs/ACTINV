//! Page bodies shared by the desktop shell and the browser workbench. A view reads or edits the
//! document or result it is given and returns what the shell must do next; no view opens a dialog,
//! touches the file system, or runs the solver.
pub mod import;
pub mod material;
pub mod outputs;
pub mod pathways;
pub mod photons;
pub mod steps;
pub mod tables;

use eframe::egui;

/// A file chosen for a spec field: the path as the spec should record it, and its SHA-256.
pub struct Pinned {
    pub path: String,
    pub sha256: String,
}

/// File chooser supplied by the shell. `None` when cancelled or not available in this shell.
pub trait Picker {
    fn pick(&mut self, title: &str, extensions: &[&str]) -> Option<Pinned>;
}

/// Screen rectangles of tour anchors, collected while a view renders.
#[derive(Default)]
pub struct Marks(pub Vec<(&'static str, egui::Rect)>);

impl Marks {
    pub fn mark(&mut self, anchor: &'static str, rect: egui::Rect) {
        self.0.push((anchor, rect));
    }
}

/// A setup page a view can send the user to.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Target {
    Overview,
    Material,
    Schedule,
    Spectrum,
    Outputs,
}
