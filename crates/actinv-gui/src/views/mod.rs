//! Page bodies shared by the desktop shell and the browser workbench. A view reads or edits the
//! document or result it is given and returns what the shell must do next; no view opens a dialog,
//! touches the file system, or runs the solver.
pub mod certify;
pub mod check;
pub mod compare;
pub mod export;
pub mod import;
pub mod impurities;
pub mod material;
#[cfg(not(target_arch = "wasm32"))]
pub mod mesh;
pub mod optimize;
pub mod outputs;
pub mod pathways;
pub mod photons;
pub mod questions;
pub mod review;
pub mod source;
pub mod steps;
pub mod summary;
pub mod tables;
pub mod waste;
pub mod waste_bounds;
pub mod waste_forms;
pub mod waste_package;
pub mod waste_uncertainty;

use eframe::egui;

/// A file chosen for a spec field: the path as the spec should record it, and its SHA-256.
// The browser's picker answers `None` and writes the pin itself, so it never builds one.
#[cfg_attr(target_arch = "wasm32", allow(dead_code))]
pub struct Pinned {
    pub path: String,
    pub sha256: String,
}

/// File chooser supplied by the shell. `None` when cancelled or not available in this shell.
pub trait Picker {
    fn pick(&mut self, title: &str, extensions: &[&str]) -> Option<Pinned>;
}

/// The end of a sentence telling the user to run the problem again: the desktop runs it itself,
/// the browser cannot run it and says where it can be run.
#[cfg(not(target_arch = "wasm32"))]
pub const RUN_AGAIN: &str = "run it again";
#[cfg(target_arch = "wasm32")]
pub const RUN_AGAIN: &str = "download the problem and run it with the desktop app or `actinv run`";

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
    Material,
    Schedule,
    Spectrum,
    Outputs,
    /// Check & run: the title, the review and the Run button.
    CheckRun,
    /// The nuclear data the problem uses.
    Data,
    /// The full-document editor, for settings no page edits.
    Advanced,
}
