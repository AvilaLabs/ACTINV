//! Mesh & transport: the shared view draws the four steps; this page runs their commands through
//! the job layer and reads what each one wrote once it stops, whether it finished, failed or was
//! cancelled. A mesh run writes its output as its own checkpoint, so a stopped run resumes in
//! the same folder.
use super::export::fresh_folder;
use crate::{
    app::{heading, job_folder, Desktop, JobKind},
    jobs::Command,
    nav::Page,
    platform::Dialog,
    views::{
        mesh::{self, Code, Context, MeshAction, SourceKind, Written},
        summary,
    },
};
use eframe::egui;
use serde_json::{json, Value};
use std::path::{Path, PathBuf};

/// A command this page started, and the folder it writes into.
pub(in crate::app) struct Pending {
    command: Command,
    folder: PathBuf,
    /// The banded source a conversion reads, kept with the files it adds.
    banded: Option<PathBuf>,
}

/// Every file in a folder, sorted, for the list of what was written.
fn files_in(folder: &Path) -> Vec<PathBuf> {
    let mut files: Vec<PathBuf> = std::fs::read_dir(folder)
        .into_iter()
        .flatten()
        .flatten()
        .map(|entry| entry.path())
        .filter(|path| path.is_file() && path.file_name().is_some_and(|n| n != "input.json"))
        .collect();
    files.sort();
    files
}

fn text(path: &Path) -> Result<String, String> {
    path.to_str()
        .map(str::to_owned)
        .ok_or_else(|| format!("{} is not valid UTF-8", path.display()))
}

impl Desktop {
    pub(super) fn mesh_page(&mut self, ui: &mut egui::Ui) {
        heading(
            ui,
            "Mesh & transport",
            "Activate every cell of a transport flux mesh, give the transport code its shutdown-dose source back, and check which cells clear their limits.",
        );
        self.mesh_capture();
        if self.job.is_none() {
            if let Some(pending) = self.mesh_pending.take() {
                self.mesh_finished(pending);
            }
        }
        let spec = self
            .validate()
            .ok()
            .and_then(|spec| serde_json::to_value(spec).ok());
        let (notes, refused) = spec.as_ref().map(mesh::mesh_notes).unwrap_or_default();
        let nuclides = self
            .result
            .as_ref()
            .map(|r| mesh::photon_nuclides(&r.value))
            .unwrap_or_default();
        let running = match self.job.as_ref().map(|job| job.kind) {
            Some(JobKind::Command(command))
                if self
                    .mesh_pending
                    .as_ref()
                    .is_some_and(|p| p.command == command) =>
            {
                Some(command.name())
            }
            _ => None,
        };
        let context = Context {
            notes: &notes,
            refused: refused.as_deref(),
            photon_nuclides: nuclides.len(),
            covariance: self.catalog_installed(mesh::COVARIANCE),
            running,
            busy: self.job.is_some(),
        };
        let started = match mesh::show(ui, &mut self.mesh, &context) {
            MeshAction::None => Ok(()),
            MeshAction::OpenData => {
                self.page = Page::Data;
                Ok(())
            }
            MeshAction::PickSource => {
                let (name, extensions) = self.mesh.format.filter();
                if let Some(path) = self.platform.pick_file(
                    Dialog::new()
                        .title("Choose the flux file")
                        .filter(name, extensions),
                ) {
                    if self.mesh.format.name().is_none() {
                        match mesh::read_flux(&path) {
                            Ok(flux) => self.mesh_flux(flux),
                            Err(error) => self.report(Err(error)),
                        }
                    }
                    self.mesh.source = Some(path);
                }
                Ok(())
            }
            MeshAction::PickGroups => {
                if let Some(path) = self.platform.pick_file(
                    Dialog::new()
                        .title("Choose the group structure")
                        .filter("Group structure", &["json"]),
                ) {
                    self.mesh.groups = Some(path);
                }
                Ok(())
            }
            MeshAction::Import => mesh::import_args(&self.mesh).and_then(|args| {
                self.mesh_start(
                    Command::ImportFlux,
                    Value::Null,
                    args,
                    "Importing the flux…",
                    None,
                )
            }),
            MeshAction::Activate => self.activate(spec.as_ref(), &nuclides, None),
            MeshAction::Resume => {
                let folder = self.mesh.unfinished.clone();
                self.activate(spec.as_ref(), &nuclides, folder)
            }
            MeshAction::OpenMesh => {
                if let Some(path) = self.platform.pick_file(
                    Dialog::new()
                        .title("Open a mesh result")
                        .filter("Mesh result", &["ndjson"]),
                ) {
                    match mesh::read_mesh(&path, None) {
                        Ok(run) => self.mesh_loaded(run, None),
                        Err(error) => self.report(Err(error)),
                    }
                }
                Ok(())
            }
            MeshAction::OpenCell(id) => {
                let path = self.mesh.mesh.as_ref().map(|m| m.path.clone());
                match path.map(|path| mesh::cell_result(&path, &id)) {
                    Some(Ok(result)) => {
                        self.step = summary::shutdown_step(&result).unwrap_or(0);
                        self.result = Some(result);
                        self.result_unsaved = false;
                        self.reset_plot = true;
                        self.page = Page::Summary;
                        self.report(Ok(format!(
                            "Cell {id} is the loaded result; Mesh & transport keeps the mesh."
                        )));
                    }
                    Some(Err(error)) => self.report(Err(error)),
                    None => {}
                }
                Ok(())
            }
            MeshAction::WriteSource => self.write_source(),
            MeshAction::SaveSource => {
                self.save_source();
                Ok(())
            }
            MeshAction::Check => self.check_facility(),
        };
        if let Err(error) = started {
            self.report(Err(error));
        }
    }

    /// Start one of this page's commands and remember where it writes.
    fn mesh_start(
        &mut self,
        command: Command,
        document: Value,
        args: Value,
        message: &str,
        folder: Option<PathBuf>,
    ) -> Result<(), String> {
        let folder = folder.unwrap_or_else(|| job_folder(command));
        self.start_command_in(command, document, args, message, folder.clone())?;
        self.mesh_pending = Some(Pending {
            command,
            folder,
            banded: None,
        });
        Ok(())
    }

    /// A new flux: what was solved from the old one no longer applies.
    fn mesh_flux(&mut self, flux: mesh::Flux) {
        if self.mesh.flux.as_ref() != Some(&flux) {
            self.mesh.mesh = None;
            self.mesh.unfinished = None;
            self.mesh.written = None;
            self.mesh.twin = None;
        }
        self.mesh.flux = Some(flux);
        self.mesh.change_flux = false;
    }

    fn mesh_loaded(&mut self, run: mesh::MeshRun, folder: Option<PathBuf>) {
        self.mesh.unfinished = folder.filter(|_| !run.complete);
        self.mesh.written = None;
        self.mesh.twin = None;
        self.mesh.mesh = Some(run);
    }

    /// Activate every cell, or resume the stopped run in `folder`.
    fn activate(
        &mut self,
        spec: Option<&Value>,
        nuclides: &[String],
        folder: Option<PathBuf>,
    ) -> Result<(), String> {
        let spec = match spec {
            Some(spec) => spec.clone(),
            None => serde_json::to_value(self.validate()?).map_err(|e| e.to_string())?,
        };
        let flux = self.mesh.flux.clone().ok_or("Bring in the flux first.")?;
        let bands =
            (self.mesh.bands && self.catalog_installed(mesh::COVARIANCE)).then_some(nuclides);
        let document = mesh::mesh_spec(&spec, &flux, bands, self.mesh.threads)?;
        // the core's own checks, before a worker starts
        let resolved = actinv_cli::resolve_catalog_json(&document.to_string())?;
        actinv_core::mesh::MeshSpec::from_json(&resolved)?;
        let args = if folder.is_some() {
            json!({"resume": true})
        } else {
            json!({})
        };
        let message = if folder.is_some() {
            "Resuming: only the cells the stopped run did not reach are solved."
        } else {
            "Activating every cell; cancel from the toolbar, and resume later."
        };
        self.mesh_start(Command::Mesh, document, args, message, folder)
    }

    /// Write the chosen source for the chosen step.
    fn write_source(&mut self) -> Result<(), String> {
        let run = self.mesh.mesh.as_ref().ok_or("Activate the cells first.")?;
        let input = text(&run.path)?;
        let step = self.mesh.step + 1;
        match self.mesh.kind {
            SourceKind::PerCell => self.mesh_start(
                Command::ExportOpenmcMesh,
                Value::Null,
                json!({"input": input, "step": step}),
                "Writing the OpenMC source…",
                None,
            ),
            SourceKind::Banded if self.mesh.joint => {
                let spec = run
                    .spec
                    .as_ref()
                    .ok_or("The correlated source needs a mesh this page activated.")?;
                let spec = text(spec)?;
                self.mesh_start(
                    Command::ExportR2sJoint,
                    Value::Null,
                    json!({"input": input, "mesh_spec": spec, "step": step}),
                    "Correlating the bands between cells…",
                    None,
                )
            }
            SourceKind::Banded => self.mesh_start(
                Command::ExportR2s,
                Value::Null,
                json!({"input": input, "step": step}),
                "Writing the banded source…",
                None,
            ),
        }
    }

    /// Convert the banded source for the chosen code.
    fn convert_source(&mut self, banded: PathBuf) -> Result<(), String> {
        let code = self.mesh.code;
        let mut args = json!({"format": code.name(), "input": text(&banded)?});
        if code == Code::Alara {
            let shutdown = self
                .mesh
                .mesh
                .as_ref()
                .and_then(mesh::MeshRun::shutdown_s)
                .ok_or(
                    "ALARA counts cooling from the end of irradiation, and this mesh has none.",
                )?;
            args["shutdown_t_s"] = json!(shutdown);
        }
        self.mesh_start(
            Command::ExportSource,
            Value::Null,
            args,
            "Converting the source…",
            None,
        )?;
        if let Some(pending) = &mut self.mesh_pending {
            pending.banded = Some(banded);
        }
        Ok(())
    }

    fn check_facility(&mut self) -> Result<(), String> {
        let run = self.mesh.mesh.as_ref().ok_or("Activate the cells first.")?;
        let spec = mesh::twin_spec(&self.mesh, run)?;
        self.mesh_start(
            Command::Twin,
            spec,
            json!({}),
            "Checking the facility…",
            None,
        )
    }

    /// Read what a command of this page wrote once it stopped.
    fn mesh_finished(&mut self, pending: Pending) {
        let folder = &pending.folder;
        let read = match pending.command {
            Command::ImportFlux => {
                let path = folder.join("flux.ndjson");
                if !path.is_file() {
                    return;
                }
                mesh::read_flux(&path).map(|flux| self.mesh_flux(flux))
            }
            Command::Mesh => {
                let path = folder.join("mesh.ndjson");
                if !path.is_file() {
                    return;
                }
                mesh::read_mesh(&path, Some(folder.join("input.json")))
                    .map(|run| self.mesh_loaded(run, Some(folder.clone())))
            }
            Command::ExportR2s | Command::ExportR2sJoint => {
                let name = if pending.command == Command::ExportR2s {
                    "r2s_source.ndjson"
                } else {
                    "r2s_joint.ndjson"
                };
                let path = folder.join(name);
                if !path.is_file() {
                    return;
                }
                self.convert_source(path)
            }
            Command::ExportSource | Command::ExportOpenmcMesh => {
                let mut files = files_in(folder);
                if files.is_empty() {
                    return;
                }
                let banded = match &pending.banded {
                    Some(path) => {
                        files.insert(0, path.clone());
                        mesh::read_banded(path).map(Some)
                    }
                    None => Ok(None),
                };
                banded.map(|banded| self.mesh.written = Some(Written { files, banded }))
            }
            Command::Twin => {
                let path = folder.join("twin.json");
                if !path.is_file() {
                    return;
                }
                std::fs::read_to_string(&path)
                    .map_err(|e| e.to_string())
                    .and_then(|text| serde_json::from_str(&text).map_err(|e| e.to_string()))
                    .map(|twin| self.mesh.twin = Some(twin))
            }
            _ => Ok(()),
        };
        if let Err(error) = read {
            self.report(Err(error));
        }
    }

    /// Copy the written source into a new folder the user chooses.
    fn save_source(&mut self) {
        let Some(written) = &self.mesh.written else {
            return;
        };
        let Some(parent) = self
            .platform
            .pick_folder(Dialog::new().title("Choose where to save the source"))
        else {
            return;
        };
        let folder = fresh_folder(&parent, "shutdown-dose-source");
        let copied = std::fs::create_dir_all(&folder)
            .map_err(|e| format!("cannot create {}: {e}", folder.display()))
            .and_then(|()| {
                for file in &written.files {
                    let name = file.file_name().ok_or("a written file has no name")?;
                    std::fs::copy(file, folder.join(name))
                        .map_err(|e| format!("cannot copy {}: {e}", file.display()))?;
                }
                Ok(())
            });
        self.report(copied.map(|()| {
            format!(
                "Saved {} files in {}.",
                written.files.len(),
                folder.display()
            )
        }));
    }

    /// Screenshots: load the mesh, source and facility check the capture names.
    fn mesh_capture(&mut self) {
        if self.capture.is_none() || self.mesh.mesh.is_some() {
            return;
        }
        let Some(path) = std::env::var_os("ACTINV_GUI_CAPTURE_MESH") else {
            return;
        };
        let path = PathBuf::from(path);
        if let Ok(flux) = mesh::read_flux(&path.with_file_name("flux.ndjson")) {
            self.mesh.flux = Some(flux);
        }
        let spec = path.with_file_name("mesh_spec.json");
        if let Ok(run) = mesh::read_mesh(&path, spec.is_file().then_some(spec)) {
            self.mesh.mesh = Some(run);
            self.mesh.step = 1;
        }
        if let Ok(banded) = mesh::read_banded(&path.with_file_name("r2s.ndjson")) {
            self.mesh.written = Some(Written {
                files: vec![
                    path.with_file_name("r2s.ndjson"),
                    path.with_file_name("source_openmc.xml"),
                ],
                banded: Some(banded),
            });
        }
        if let Some(twin) = std::fs::read_to_string(path.with_file_name("twin.json"))
            .ok()
            .and_then(|t| serde_json::from_str::<Value>(&t).ok())
        {
            if let Some(limits) = twin["limits"].as_array() {
                self.mesh.limits = limits
                    .iter()
                    .map(|l| mesh::Limit {
                        name: l["name"].as_str().unwrap_or("").into(),
                        response: l["response"].as_str().unwrap_or("").into(),
                        value: l["limit"].as_f64().unwrap_or(0.),
                        at_most: l["sense"] != "ge",
                    })
                    .collect();
            }
            if let Some(components) = twin["components"].as_object() {
                self.mesh.components = components
                    .iter()
                    .map(|(name, c)| mesh::Component {
                        name: name.clone(),
                        cells: c["cells"]
                            .as_array()
                            .into_iter()
                            .flatten()
                            .filter_map(Value::as_str)
                            .collect::<Vec<_>>()
                            .join(", "),
                    })
                    .collect();
            }
            // the limit times, from what binds them when the spec is not beside the result
            let times: Vec<f64> = twin["facility"]["binding_cells"]
                .as_object()
                .into_iter()
                .flatten()
                .filter_map(|(key, _)| key.rsplit_once('@')?.1.parse().ok())
                .collect();
            let t = twin["times_s"][0]
                .as_f64()
                .or_else(|| (times.len() == 1).then(|| times[0]));
            let first = self.mesh.mesh.as_ref().and_then(|m| m.first.as_ref());
            self.mesh.twin_step = t.and_then(|t| {
                first?
                    .steps()
                    .iter()
                    .position(|s| s["t_s"].as_f64() == Some(t))
            });
            self.mesh.twin = Some(twin);
        }
    }
}
