//! Optimize: the shared view sets up the search and draws the answer; this page runs `actinv
//! optimize` in the isolated worker, loads the result it writes, and puts a chosen design into the
//! problem.
use crate::{
    app::{heading, Desktop, JobKind},
    jobs::Command,
    views::{
        certify,
        optimize::{self, Found, OptimizeAction},
    },
};
use eframe::egui;
use serde_json::Value;
use std::path::Path;

/// A finished search: its result and, beside it, the search it ran.
fn load(result: &Path) -> Result<Found, String> {
    let read = |path: &Path| -> Result<Value, String> {
        let text = std::fs::read_to_string(path).map_err(|e| format!("{}: {e}", path.display()))?;
        serde_json::from_str(&text).map_err(|e| format!("{}: {e}", path.display()))
    };
    let search = read(&result.with_file_name("input.json"))?;
    Ok(Found {
        path: result.to_owned(),
        result: read(result)?,
        search,
    })
}

impl Desktop {
    pub(super) fn optimize_page(&mut self, ui: &mut egui::Ui) {
        heading(ui, "Optimize", optimize::QUESTION);
        if self.capture.is_some() && self.optimize.found.is_none() {
            if let Some(path) = std::env::var_os("ACTINV_GUI_CAPTURE_OPTIMIZE") {
                self.optimize.found = load(Path::new(&path)).ok();
                if let Some(found) = &self.optimize.found {
                    let search = found.search.clone();
                    self.optimize.show_search(&search, &self.document);
                }
            }
        }
        // load the result the last search wrote, once
        if let Some(done) = self.command_result(Command::Optimize) {
            if let Some(path) = done.primary.clone().filter(|path| {
                done.exit == 0
                    && self
                        .optimize
                        .found
                        .as_ref()
                        .is_none_or(|found| &found.path != path)
            }) {
                match load(&path) {
                    Ok(found) => self.optimize.found = Some(found),
                    Err(error) => {
                        // remember the path so a broken result is reported once
                        self.optimize.found = Some(Found {
                            path,
                            result: Value::Null,
                            search: Value::Null,
                        });
                        self.report(Err(format!("Cannot read the search result: {error}")));
                    }
                }
            }
        }
        let running = self
            .job
            .as_ref()
            .is_some_and(|job| job.kind == JobKind::Command(Command::Optimize));
        let busy = self.job.is_some();
        match optimize::show(
            ui,
            &self.document,
            &mut self.optimize,
            running || busy,
            true,
        ) {
            OptimizeAction::None => {}
            OptimizeAction::Open(target) => self.page = super::page_for(target),
            OptimizeAction::UseDesign(axes, x) => {
                let applied = serde_json::from_value::<Vec<actinv_cli::optimize::Axis>>(axes)
                    .map_err(|e| e.to_string())
                    .and_then(|axes| actinv_cli::optimize::apply_axes(&self.document, &axes, &x));
                match applied {
                    Ok(document) => {
                        self.document = document;
                        self.report(Ok(
                            "The best design is in the problem now; run it from Check & run, or Undo edit to go back."
                                .into(),
                        ));
                    }
                    Err(error) => self.report(Err(format!("Cannot use this design: {error}"))),
                }
            }
            OptimizeAction::Search => {
                let started = self.validate().and_then(|spec| {
                    let run_spec = serde_json::to_value(spec).map_err(|e| e.to_string())?;
                    let times = certify::step_times(&self.document);
                    let (search, args) =
                        optimize::optimize_spec(run_spec, &self.document, &self.optimize, &times)?;
                    self.start_command(
                        Command::Optimize,
                        search,
                        args,
                        "Searching: every design is one solve; the status bar counts them.",
                    )
                });
                if let Err(error) = started {
                    self.report(Err(error));
                }
            }
        }
    }
}
