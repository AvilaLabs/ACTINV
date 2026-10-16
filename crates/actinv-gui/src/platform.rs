//! File-dialog seam for the desktop shell. Pages ask a `Platform` for a path instead of calling a
//! dialog toolkit, so a test can script the answer. Native dialogs answer synchronously, which is
//! what every current call site relies on; the browser shell (`web_app.rs`) still uses
//! `rfd::AsyncFileDialog` directly because its dialogs answer on a later frame.
use std::path::PathBuf;

/// What a dialog should offer: an optional title, one file-type filter and a suggested file name.
#[derive(Clone, Copy, Default)]
pub struct Dialog<'a> {
    pub title: Option<&'a str>,
    pub filter: Option<(&'a str, &'a [&'a str])>,
    pub file_name: Option<&'a str>,
}

impl<'a> Dialog<'a> {
    pub fn new() -> Self {
        Self::default()
    }
    pub fn title(mut self, title: &'a str) -> Self {
        self.title = Some(title);
        self
    }
    pub fn filter(mut self, name: &'a str, extensions: &'a [&'a str]) -> Self {
        self.filter = Some((name, extensions));
        self
    }
    pub fn file_name(mut self, file_name: &'a str) -> Self {
        self.file_name = Some(file_name);
        self
    }
}

/// Every method returns `None` when the user cancels.
pub trait Platform {
    fn pick_file(&self, dialog: Dialog<'_>) -> Option<PathBuf>;
    fn pick_folder(&self, dialog: Dialog<'_>) -> Option<PathBuf>;
    fn save_file(&self, dialog: Dialog<'_>) -> Option<PathBuf>;
}

/// Native dialogs through `rfd`.
pub struct Native;

impl Native {
    fn builder(dialog: Dialog<'_>) -> rfd::FileDialog {
        let mut builder = rfd::FileDialog::new();
        if let Some(title) = dialog.title {
            builder = builder.set_title(title);
        }
        if let Some((name, extensions)) = dialog.filter {
            builder = builder.add_filter(name, extensions);
        }
        if let Some(file_name) = dialog.file_name {
            builder = builder.set_file_name(file_name);
        }
        builder
    }
}

impl Platform for Native {
    fn pick_file(&self, dialog: Dialog<'_>) -> Option<PathBuf> {
        Self::builder(dialog).pick_file()
    }
    fn pick_folder(&self, dialog: Dialog<'_>) -> Option<PathBuf> {
        Self::builder(dialog).pick_folder()
    }
    fn save_file(&self, dialog: Dialog<'_>) -> Option<PathBuf> {
        Self::builder(dialog).save_file()
    }
}
