//! Export: the shared view picks the files; this page writes them into a new folder inside the
//! folder the user chooses, never over an existing one.
use crate::{
    app::{heading, Desktop},
    platform::Dialog,
    views::{
        export::{self, Context, ExportAction},
        waste,
    },
};
use eframe::egui;
use std::path::{Path, PathBuf};

/// `parent/name`, or `parent/name-2`, `-3`, … when that exists already.
fn fresh_folder(parent: &Path, name: &str) -> PathBuf {
    let mut path = parent.join(name);
    let mut n = 2;
    while path.exists() {
        path = parent.join(format!("{name}-{n}"));
        n += 1;
    }
    path
}

/// Write the files into a new folder; their names may hold one level of subfolder.
fn write_all(folder: &Path, files: &[(String, Vec<u8>)]) -> Result<(), String> {
    for (name, bytes) in files {
        let path = folder.join(name);
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)
                .map_err(|e| format!("cannot create {}: {e}", parent.display()))?;
        }
        std::fs::write(&path, bytes)
            .map_err(|e| format!("cannot write {}: {e}", path.display()))?;
    }
    Ok(())
}

impl Desktop {
    pub(super) fn export_page(&mut self, ui: &mut egui::Ui) {
        heading(
            ui,
            "Export",
            "Save what this result answers: a report to read and share, charts and tables for your own documents, and the files to rerun or reuse it.",
        );
        self.result_bar(ui);
        let Some(result) = &self.result else {
            self.no_result(ui, &[]);
            return;
        };
        let context = Context {
            problem: Some(&self.document),
            waste: self.waste.view_for(result).map(waste::report_section),
            step: self.step,
            save_label: "Save to folder…",
        };
        let ExportAction::Save { name, files } =
            export::show(ui, result, &mut self.export, &context)
        else {
            return;
        };
        let Some(parent) = self
            .platform
            .pick_folder(Dialog::new().title("Choose where to save the export"))
        else {
            return;
        };
        let folder = fresh_folder(&parent, &name);
        let written = write_all(&folder, &files);
        if written.is_ok() && files.iter().any(|(n, _)| n == "result.json") {
            self.result_unsaved = false;
        }
        let message = written.map(|()| {
            format!(
                "Saved {} files in {}. Open report.html in a browser to read or print it.",
                files.len(),
                folder.display()
            )
        });
        self.export.message = Some(message.clone());
        self.report(message);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn an_export_never_lands_on_an_existing_folder() {
        let parent = std::env::temp_dir().join(format!("actinv-export-{}", std::process::id()));
        std::fs::create_dir_all(parent.join("x-export")).unwrap();
        let folder = fresh_folder(&parent, "x-export");
        assert_eq!(folder, parent.join("x-export-2"));
        write_all(&folder, &[("tables/a.csv".into(), b"1".to_vec())]).unwrap();
        assert_eq!(std::fs::read(folder.join("tables/a.csv")).unwrap(), b"1");
        std::fs::remove_dir_all(&parent).unwrap();
    }
}
