//! Jobs: `actinv` commands run for an app page. A job is one JSON document,
//!
//! ```text
//! {"schema":"actinv-gui-job-1","command":"decide","document":{...},"out_dir":"/abs/dir","args":{...}}
//! ```
//!
//! `document` is the command's main input (a problem, a decision, a study ...), `args` carries
//! the rest of its command line, and every file the command writes goes into `out_dir`, which must
//! be absolute and empty or absent. A resumable command (`optimize`, `study-run`, `mesh`) accepts
//! a non-empty `out_dir` when `args.resume` is true. Where the command line takes a path the job
//! writes the document to `out_dir/input.json` first; file references inside a document are not
//! rewritten, so the page sends them absolute or embedded.
//!
//! `run` executes the job in the calling thread, prints nothing and changes no process state, so
//! the worker child and an ordinary background thread can both call it. It returns
//!
//! ```text
//! {"schema":"actinv-gui-job-result-1","command":...,"exit":0,"primary":"/abs/dir/main.json",
//!  "summary":{...},"files":["main.json",...],"message":"one line"}
//! ```
//!
//! `exit` is 0, or 3 when the check ran and failed with its output written (a verification that
//! did not pass; a doctor that found a problem). A command that cannot produce output returns an
//! error instead. `summary` is a small headline for the page, never a whole result: read the
//! output files for that. `progress` reads what a long command has written so far.
//!
//! Every message and exit status follows the command line; the `actinv` handlers in
//! `actinv_cli::command` and the library functions behind them do the work.
// The pages that launch jobs arrive after this layer.
#![allow(dead_code)]

use actinv_cli::{
    command::{doctor_report, mesh_photon_cells, photon_source_at, write_alara_export, Failure},
    decide, evalspread, optimize, resolve_catalog_json, twin, verify_bundle, waste_bounds,
    waste_intrusion, waste_package, waste_uncertainty,
};
use actinv_core::{
    flux::{import_fispact, import_mctal, import_meshtal, import_openmc},
    mesh::{run_mesh, MeshSpec},
    photon::{export_mcnp, export_openmc, export_openmc_mesh},
    spec::Spec,
    study::{self, Study},
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};
use std::{
    io::Read,
    path::{Path, PathBuf},
    sync::mpsc,
    thread,
};

pub const JOB_SCHEMA: &str = "actinv-gui-job-1";
pub const RESULT_SCHEMA: &str = "actinv-gui-job-result-1";

/// The most bytes of a progress file `progress` reads.
const MAX_PROGRESS_BYTES: u64 = 16 * 1024 * 1024;
/// The most files `JobResult::files` lists.
const MAX_LISTED_FILES: usize = 1000;
/// A summary larger than this is cut down to its scalar fields.
const MAX_SUMMARY_BYTES: usize = 16 * 1024;
/// The default `--window-rows` of `actinv import-flux openmc`.
const DEFAULT_WINDOW_ROWS: usize = 16_384;

/// A command a job can run; the serialised name is the command-line name.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum Command {
    #[serde(rename = "decide")]
    Decide,
    #[serde(rename = "optimize")]
    Optimize,
    #[serde(rename = "study-run")]
    StudyRun,
    #[serde(rename = "study-validate")]
    StudyValidate,
    #[serde(rename = "waste-package")]
    WastePackage,
    #[serde(rename = "waste-bounds")]
    WasteBounds,
    #[serde(rename = "waste-uncertainty")]
    WasteUncertainty,
    #[serde(rename = "waste-intrusion-screen")]
    WasteIntrusionScreen,
    #[serde(rename = "mesh")]
    Mesh,
    #[serde(rename = "twin")]
    Twin,
    #[serde(rename = "reverse")]
    Reverse,
    #[serde(rename = "reverse-qualified")]
    ReverseQualified,
    #[serde(rename = "eval-spread")]
    EvalSpread,
    #[serde(rename = "import-flux")]
    ImportFlux,
    #[serde(rename = "export-openmc")]
    ExportOpenmc,
    #[serde(rename = "export-mcnp")]
    ExportMcnp,
    #[serde(rename = "export-openmc-mesh")]
    ExportOpenmcMesh,
    #[serde(rename = "export-r2s")]
    ExportR2s,
    #[serde(rename = "export-r2s-joint")]
    ExportR2sJoint,
    #[serde(rename = "export-source")]
    ExportSource,
    #[serde(rename = "data-verify")]
    DataVerify,
    #[serde(rename = "doctor")]
    Doctor,
}

impl Command {
    pub const ALL: [Command; 22] = [
        Command::Decide,
        Command::Optimize,
        Command::StudyRun,
        Command::StudyValidate,
        Command::WastePackage,
        Command::WasteBounds,
        Command::WasteUncertainty,
        Command::WasteIntrusionScreen,
        Command::Mesh,
        Command::Twin,
        Command::Reverse,
        Command::ReverseQualified,
        Command::EvalSpread,
        Command::ImportFlux,
        Command::ExportOpenmc,
        Command::ExportMcnp,
        Command::ExportOpenmcMesh,
        Command::ExportR2s,
        Command::ExportR2sJoint,
        Command::ExportSource,
        Command::DataVerify,
        Command::Doctor,
    ];

    pub fn name(self) -> &'static str {
        match self {
            Command::Decide => "decide",
            Command::Optimize => "optimize",
            Command::StudyRun => "study-run",
            Command::StudyValidate => "study-validate",
            Command::WastePackage => "waste-package",
            Command::WasteBounds => "waste-bounds",
            Command::WasteUncertainty => "waste-uncertainty",
            Command::WasteIntrusionScreen => "waste-intrusion-screen",
            Command::Mesh => "mesh",
            Command::Twin => "twin",
            Command::Reverse => "reverse",
            Command::ReverseQualified => "reverse-qualified",
            Command::EvalSpread => "eval-spread",
            Command::ImportFlux => "import-flux",
            Command::ExportOpenmc => "export-openmc",
            Command::ExportMcnp => "export-mcnp",
            Command::ExportOpenmcMesh => "export-openmc-mesh",
            Command::ExportR2s => "export-r2s",
            Command::ExportR2sJoint => "export-r2s-joint",
            Command::ExportSource => "export-source",
            Command::DataVerify => "data-verify",
            Command::Doctor => "doctor",
        }
    }

    /// Whether the command reads `Job::document`; `doctor` takes one optionally.
    fn needs_document(self) -> bool {
        matches!(
            self,
            Command::Decide
                | Command::Optimize
                | Command::StudyRun
                | Command::StudyValidate
                | Command::WastePackage
                | Command::WasteBounds
                | Command::WasteUncertainty
                | Command::WasteIntrusionScreen
                | Command::Mesh
                | Command::Twin
                | Command::Reverse
                | Command::ReverseQualified
                | Command::EvalSpread
        )
    }

    /// Whether a rerun may continue in a non-empty `out_dir` (with `args.resume` true).
    pub fn resumable(self) -> bool {
        matches!(self, Command::Optimize | Command::StudyRun | Command::Mesh)
    }
}

/// One job: the command, its main input document, where its files go, and its other arguments.
#[derive(Clone, Debug, PartialEq)]
pub struct Job {
    pub command: Command,
    pub document: Value,
    pub out_dir: PathBuf,
    pub args: Value,
}

#[derive(Deserialize)]
struct Envelope {
    schema: String,
    command: Command,
    #[serde(default)]
    document: Value,
    out_dir: PathBuf,
    #[serde(default)]
    args: Value,
}

impl Job {
    /// Parse and check an `actinv-gui-job-1` envelope. Nothing on disk is touched.
    pub fn from_json(text: &str) -> Result<Job, String> {
        let envelope: Envelope =
            serde_json::from_str(text).map_err(|e| format!("job envelope: {e}"))?;
        if envelope.schema != JOB_SCHEMA {
            return Err(format!(
                "job schema must be {JOB_SCHEMA}, got '{}'",
                envelope.schema
            ));
        }
        let job = Job {
            command: envelope.command,
            document: envelope.document,
            out_dir: envelope.out_dir,
            args: match envelope.args {
                Value::Null => json!({}),
                args => args,
            },
        };
        job.check()?;
        Ok(job)
    }

    /// The envelope as JSON, as `worker::spawn_job` sends it.
    pub fn to_value(&self) -> Value {
        json!({
            "schema": JOB_SCHEMA,
            "command": self.command.name(),
            "document": self.document,
            "out_dir": self.out_dir,
            "args": self.args,
        })
    }

    /// The refusals that need no filesystem: a relative `out_dir`, `args` that are not an
    /// object, a missing document.
    pub fn check(&self) -> Result<(), String> {
        if !self.out_dir.is_absolute() {
            return Err(format!(
                "job out_dir must be an absolute path, got '{}'",
                self.out_dir.display()
            ));
        }
        if !self.args.is_object() {
            return Err("job args must be a JSON object".into());
        }
        if self.command.needs_document() && !self.document.is_object() {
            return Err(format!(
                "{} needs a document (a JSON object)",
                self.command.name()
            ));
        }
        Ok(())
    }

    fn resume(&self) -> bool {
        arg_bool(&self.args, "resume")
    }

    /// Create `out_dir`, or accept it when it is empty or the job resumes in it.
    fn prepare_out_dir(&self) -> Result<(), String> {
        let dir = &self.out_dir;
        match std::fs::metadata(dir) {
            Ok(meta) if !meta.is_dir() => Err(format!("{} is not a directory", dir.display())),
            Ok(_) => {
                let empty = std::fs::read_dir(dir)
                    .map_err(|e| format!("cannot read {}: {e}", dir.display()))?
                    .next()
                    .is_none();
                if empty || (self.command.resumable() && self.resume()) {
                    Ok(())
                } else {
                    Err(format!(
                        "{} is not empty; choose an empty folder{}",
                        dir.display(),
                        if self.command.resumable() {
                            " or resume the earlier run"
                        } else {
                            ""
                        }
                    ))
                }
            }
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => std::fs::create_dir_all(dir)
                .map_err(|e| format!("cannot create {}: {e}", dir.display())),
            Err(e) => Err(format!("cannot inspect {}: {e}", dir.display())),
        }
    }

    fn path(&self, name: &str) -> PathBuf {
        self.out_dir.join(name)
    }

    /// The document as `out_dir/name`, for a function that takes a path.
    fn write_document(&self, name: &str) -> Result<PathBuf, String> {
        let path = self.path(name);
        write_json(&path, &self.document)?;
        Ok(path)
    }
}

/// What a job produced.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct JobResult {
    pub command: Command,
    /// 0, or 3 when the check ran and failed and its output is written.
    pub exit: i32,
    /// The main output file, absolute.
    pub primary: Option<PathBuf>,
    /// A small headline for the page.
    pub summary: Value,
    /// Every file in `out_dir`, relative to it.
    pub files: Vec<PathBuf>,
    /// The one-line summary the command line writes to stderr.
    pub message: String,
}

impl JobResult {
    pub fn to_value(&self) -> Value {
        let mut value = serde_json::to_value(self).expect("serialise job result");
        value["schema"] = json!(RESULT_SCHEMA);
        value
    }

    /// Read a result the worker returned.
    pub fn from_value(value: &Value) -> Result<JobResult, String> {
        if value["schema"] != RESULT_SCHEMA {
            return Err(format!("not a {RESULT_SCHEMA} document"));
        }
        serde_json::from_value(value.clone()).map_err(|e| format!("job result: {e}"))
    }
}

/// What a command produced before the files are listed.
struct Outcome {
    /// The main output, relative to `out_dir`.
    primary: Option<&'static str>,
    summary: Value,
    message: String,
    exit: i32,
}

impl Outcome {
    fn new(primary: &'static str, summary: Value, message: String) -> Outcome {
        Outcome {
            primary: Some(primary),
            summary: bounded(summary),
            message,
            exit: 0,
        }
    }
}

/// Run a job in this thread. See the module documentation.
pub fn run(job: &Job) -> Result<JobResult, String> {
    job.check()?;
    job.prepare_out_dir()?;
    let outcome = match job.command {
        Command::Decide => decide_job(job),
        Command::Optimize => optimize_job(job),
        Command::StudyRun => study_job(job, true),
        Command::StudyValidate => study_job(job, false),
        Command::WastePackage => waste_job(job),
        Command::WasteBounds => waste_job(job),
        Command::WasteUncertainty => waste_job(job),
        Command::WasteIntrusionScreen => waste_job(job),
        Command::Mesh => mesh_job(job),
        Command::Twin => twin_job(job),
        Command::Reverse => reverse_job(job, false),
        Command::ReverseQualified => reverse_job(job, true),
        Command::EvalSpread => eval_spread_job(job),
        Command::ImportFlux => import_flux_job(job),
        Command::ExportOpenmc | Command::ExportMcnp => export_photon_job(job),
        Command::ExportOpenmcMesh => export_mesh_job(job),
        Command::ExportR2s | Command::ExportR2sJoint => export_r2s_job(job),
        Command::ExportSource => export_source_job(job),
        Command::DataVerify => data_verify_job(job),
        Command::Doctor => doctor_job(job),
    }?;
    Ok(JobResult {
        command: job.command,
        exit: outcome.exit,
        primary: outcome.primary.map(|name| job.path(name)),
        summary: outcome.summary,
        files: list_files(&job.out_dir),
        message: outcome.message,
    })
}

/// Run a job on a thread with the solver's stack, as the worker child does.
pub fn run_on_solver_stack(job: Job) -> Result<JobResult, String> {
    let (tx, rx) = mpsc::sync_channel(1);
    thread::Builder::new()
        .stack_size(crate::model::SOLVER_STACK_BYTES)
        .spawn(move || {
            let _ = tx.send(run(&job));
        })
        .map_err(|e| e.to_string())?
        .join()
        .map_err(|_| "job thread panicked".to_owned())?;
    rx.recv().map_err(|e| e.to_string())?
}

/// Exit 3 when the output carries a verification that neither passed nor was skipped, as
/// `actinv budget` and `actinv impurities` exit; 0 otherwise.
pub fn verification_exit(doc: &Value) -> i32 {
    let verification = &doc["verification"];
    if verification.is_object()
        && verification["skipped"] != true
        && verification["verified"] != true
    {
        3
    } else {
        0
    }
}

// ---------------------------------------------------------------------------
// Commands
// ---------------------------------------------------------------------------

fn decide_job(job: &Job) -> Result<Outcome, String> {
    let input = job.write_document("input.json")?;
    let out = job.path("decision.json");
    let doc = decide::run_decide(utf8(&input)?, Some(utf8(&out)?))?;
    let certified = doc["verdict"]["certified"].as_bool().unwrap_or(false);
    let constraints = doc["constraints"].as_array().map_or(0, Vec::len);
    let binding = doc["verdict"]["binding"].as_array().map_or(0, Vec::len);
    // "not certified" is an answer, not a failed check: exit 0
    Ok(Outcome::new(
        "decision.json",
        json!({"certified": certified, "constraints": constraints, "binding": binding}),
        format!(
            "decide: {} — {constraints} constraints, {binding} binding -> {}",
            if certified {
                "certified"
            } else {
                "not certified"
            },
            out.display()
        ),
    ))
}

fn optimize_job(job: &Job) -> Result<Outcome, String> {
    let mut document = job.document.clone();
    // a base spec sent inline is written beside the optimizer spec and named relative to it
    if let Some(base) = job.args.get("base_spec").filter(|base| base.is_object()) {
        write_json(&job.path("base_spec.json"), base)?;
        document["base_spec"] = json!("base_spec.json");
    }
    let input = job.path("input.json");
    write_json(&input, &document)?;
    let summary = optimize::run_optimize(utf8(&input)?, Some(utf8(&job.out_dir)?), job.resume())?;
    let result = summary.out_dir.join("optimize_result.json");
    Ok(Outcome::new(
        "optimize_result.json",
        json!({
            "n_evals": summary.n_evals,
            "best_feasible": summary.best_feasible,
            "infeasible": summary.infeasible,
            "wall_s": summary.wall_s,
        }),
        format!(
            "optimize: {} evals, best {} ({}), {:.1} s -> {}",
            summary.n_evals,
            if summary.infeasible {
                "none (infeasible box)".to_string()
            } else {
                format!("eval {}", summary.best_feasible.unwrap_or(0))
            },
            result.display(),
            summary.wall_s,
            summary.out_dir.display(),
        ),
    ))
}

/// `study run` (executed) or `study validate` (schema and scope checks only).
fn study_job(job: &Job, execute: bool) -> Result<Outcome, String> {
    let input = job.write_document("input.json")?;
    let text = read_text(&input)?;
    if !execute {
        let study = Study::from_json(&text)?;
        let cases = study.case_ids().len();
        let responses = study.responses.len();
        let summary = json!({"study_id": study.study_id, "cases": cases, "responses": responses});
        write_json(&job.path("study_validate.json"), &summary)?;
        return Ok(Outcome::new(
            "study_validate.json",
            summary,
            format!(
                "ok: {} — {cases} cases, {responses} responses",
                study.study_id
            ),
        ));
    }
    let study = Study::from_json(&resolve_catalog_json(&text)?)?;
    let revocations = arg_str(&job.args, "revocations").map(PathBuf::from);
    let record = study::execute(&study, &input, &job.out_dir, revocations.as_deref())?;
    let cases = record["cases"].as_array().map_or(0, Vec::len);
    Ok(Outcome::new(
        "study_record.json",
        json!({
            "study_id": study.study_id,
            "cases": cases,
            "verdict": record["verdict"],
            "population": record["population"],
        }),
        format!(
            "ok: {} — {cases} cases -> {}",
            study.study_id,
            job.out_dir.display()
        ),
    ))
}

fn waste_job(job: &Job) -> Result<Outcome, String> {
    let primary = match job.command {
        Command::WastePackage => "waste_package.json",
        Command::WasteBounds => "waste_bounds.json",
        Command::WasteUncertainty => "waste_uncertainty.json",
        _ => "waste_intrusion_screen.json",
    };
    let input = job.write_document("input.json")?;
    let out = job.path(primary);
    let (input, output) = (utf8(&input)?, Some(utf8(&out)?));
    let doc = match job.command {
        Command::WastePackage => waste_package::run(input, output),
        Command::WasteBounds => waste_bounds::run(input, output),
        Command::WasteUncertainty => waste_uncertainty::run(input, output),
        _ => waste_intrusion::run(input, output),
    }?;
    Ok(Outcome {
        exit: verification_exit(&doc),
        ..Outcome::new(
            primary,
            headline(&doc),
            format!("{}: wrote {}", job.command.name(), out.display()),
        )
    })
}

fn mesh_job(job: &Job) -> Result<Outcome, String> {
    let input = job.write_document("input.json")?;
    let spec = MeshSpec::from_json(&resolve_catalog_json(&read_text(&input)?)?)?;
    let out = job.path("mesh.ndjson");
    let summary = run_mesh(&spec, &out)?;
    let message = format!(
        "mesh: {} cells, {} bytes -> {}",
        summary.cells, summary.output_bytes, summary.output
    );
    Ok(Outcome::new(
        "mesh.ndjson",
        serde_json::to_value(&summary).map_err(|e| e.to_string())?,
        message,
    ))
}

fn twin_job(job: &Job) -> Result<Outcome, String> {
    let input = job.write_document("input.json")?;
    let out = job.path("twin.json");
    let doc = twin::run(utf8(&input)?, Some(utf8(&out)?))?;
    let cleared = doc["facility"]["cleared"].as_u64().unwrap_or(0);
    let restricted = doc["facility"]["restricted"].as_u64().unwrap_or(0);
    let binding = doc["facility"]["binding_cells"]
        .as_array()
        .map_or(0, Vec::len);
    Ok(Outcome {
        exit: verification_exit(&doc),
        ..Outcome::new(
            "twin.json",
            json!({
                "schema": doc["schema"],
                "cells": doc["cells"],
                "cleared": cleared,
                "restricted": restricted,
                "binding_cells": binding,
            }),
            format!(
                "twin: {cleared} of {} cells clear -> {}",
                cleared + restricted,
                out.display()
            ),
        )
    })
}

/// `reverse` (the inverse solution) or `reverse-qualified` (its qualified NDJSON form). The
/// document is the problem; `args.measurements` is the measurements JSON.
fn reverse_job(job: &Job, qualified: bool) -> Result<Outcome, String> {
    let problem = job.write_document("input.json")?;
    let problem_text = resolve_catalog_json(&read_text(&problem)?)?;
    let spec = Spec::from_json(&problem_text)?;
    let measurements = match job.args.get("measurements") {
        Some(Value::String(text)) => text.clone(),
        Some(value) if !value.is_null() => value.to_string(),
        _ => return Err("args.measurements is required".into()),
    };
    write_file(&job.path("measurements.json"), &measurements)?;
    if qualified {
        let (doc, summary) =
            actinv_core::reverse::solve_qualified(&spec, &problem_text, &measurements)?;
        let out = job.path("reverse_qualified.ndjson");
        write_file(&out, &doc)?;
        let message = format!(
            "qualified inverse: {} segments, {} measurements, {} forward runs -> {}",
            summary["segments"],
            summary["measurements"],
            summary["forward_runs"],
            out.display()
        );
        return Ok(Outcome::new("reverse_qualified.ndjson", summary, message));
    }
    let result = actinv_core::reverse::solve(
        &spec,
        &problem_text,
        &measurements,
        arg_bool(&job.args, "segments"),
    )?;
    let out = job.path("reverse.json");
    write_json(&out, &result)?;
    Ok(Outcome::new(
        "reverse.json",
        headline(&result),
        format!("wrote {}", out.display()),
    ))
}

fn eval_spread_job(job: &Job) -> Result<Outcome, String> {
    let input = job.write_document("input.json")?;
    let out = job.path("eval_spread.json");
    let doc = evalspread::run(utf8(&input)?, Some(utf8(&out)?))?;
    let spread = doc["max_abs_ln_ratio"].as_f64().unwrap_or(0.0);
    Ok(Outcome::new(
        "eval_spread.json",
        json!({
            "n_steps": doc["n_steps"],
            "max_abs_ln_ratio": doc["max_abs_ln_ratio"],
            "suggested_unmodeled_relative": doc["suggested_unmodeled_relative"],
            "skipped_nonpositive_pairs": doc["skipped_nonpositive_pairs"],
            "comparison": doc["comparison"],
        }),
        format!(
            "eval-spread: max |ln ratio| {spread:.3e} over {} steps -> {}",
            doc["n_steps"],
            out.display()
        ),
    ))
}

/// `import-flux FORMAT SOURCE OUT`: no document; `args` hold the format, the source file and the
/// format's options.
fn import_flux_job(job: &Job) -> Result<Outcome, String> {
    let args = &job.args;
    let format = required_str(args, "format")?;
    let source = required_str(args, "source")?;
    let out = job.path("flux.ndjson");
    let floor = arg_opt_f64(args, "energy_floor_eV")?;
    let summary = match format {
        "openmc" => import_openmc(
            source,
            &out,
            required_i64(args, "tally")?,
            required_f64(args, "source_rate")?,
            floor,
            arg_opt_u64(args, "window_rows")?.map_or(DEFAULT_WINDOW_ROWS, |rows| rows as usize),
        ),
        "meshtal" => import_meshtal(
            source,
            &out,
            required_u64(args, "tally")?,
            required_f64(args, "source_rate")?,
            floor,
        ),
        "mctal" => import_mctal(
            source,
            &out,
            required_u64(args, "tally")?,
            required_f64(args, "source_rate")?,
            floor,
        ),
        "fispact" => import_fispact(source, required_str(args, "groups")?, &out),
        _ => Err(format!(
            "unsupported import-flux format '{format}'; expected openmc, meshtal, mctal or fispact"
        )),
    }?;
    let message = format!(
        "import-flux {format}: {} cells, {} groups -> {}",
        summary.cells, summary.groups, summary.output
    );
    Ok(Outcome::new(
        "flux.ndjson",
        serde_json::to_value(&summary).map_err(|e| e.to_string())?,
        message,
    ))
}

/// `export-openmc` and `export-mcnp`: the step's photon source from a result file.
fn export_photon_job(job: &Job) -> Result<Outcome, String> {
    let input = required_str(&job.args, "input")?;
    let step = step_arg(&job.args)?;
    let text = std::fs::read_to_string(input).map_err(|e| format!("cannot read {input}: {e}"))?;
    let source = photon_source_at(&text, input, step)?;
    let (fragment, name) = if job.command == Command::ExportOpenmc {
        (export_openmc(&source)?, "photon_source_openmc.py")
    } else {
        (export_mcnp(&source)?, "photon_source_mcnp.txt")
    };
    let out = job.path(name);
    write_file(&out, &fragment)?;
    Ok(Outcome::new(
        name,
        json!({"step": step, "total_photons_s": source.total_photons_s}),
        format!("step {step} photon source -> {}", out.display()),
    ))
}

/// `export-openmc-mesh`: the step's photon source of every cell of a mesh result.
fn export_mesh_job(job: &Job) -> Result<Outcome, String> {
    let input = required_str(&job.args, "input")?;
    let step = step_arg(&job.args)?;
    let cells = mesh_photon_cells(input, step).map_err(|(_, message)| message)?;
    let fragment = export_openmc_mesh(&cells)?;
    let out = job.path("photon_source_mesh_openmc.py");
    write_file(&out, &fragment)?;
    Ok(Outcome::new(
        "photon_source_mesh_openmc.py",
        json!({"step": step, "cells": cells.len()}),
        format!(
            "step {step} spatial photon source: {} cells -> {}",
            cells.len(),
            out.display()
        ),
    ))
}

/// `export-r2s` and `export-r2s-joint`: the banded or the joint R2S source of a mesh result.
/// `args.input` is the mesh result, and for the joint source `args.mesh_spec` is the mesh spec.
fn export_r2s_job(job: &Job) -> Result<Outcome, String> {
    let input = required_str(&job.args, "input")?;
    let step = step_arg(&job.args)?;
    let mesh = std::fs::read(input).map_err(|e| format!("cannot read {input}: {e}"))?;
    let (doc, summary, name, message) = if job.command == Command::ExportR2s {
        let (doc, summary) = actinv_core::r2s::emit_r2s_source(&mesh, step)?;
        let message = format!(
            "step {step} banded r2s source: {} cells, {} partially unbanded",
            summary["cells"], summary["cells_partially_unbanded"]
        );
        (doc, summary, "r2s_source.ndjson", message)
    } else {
        let spec_path = required_str(&job.args, "mesh_spec")?;
        let spec = resolve_catalog_json(&read_text(Path::new(spec_path))?)?;
        let (doc, summary) = actinv_core::r2s::emit_r2s_joint(&mesh, spec.as_bytes(), step)?;
        let message = format!(
            "step {step} joint r2s source: {} cells, sigma_correlated {}",
            summary["cells"], summary["sigma_total_correlated"]
        );
        (doc, summary, "r2s_joint.ndjson", message)
    };
    let out = job.path(name);
    write_file(&out, &doc)?;
    Ok(Outcome::new(
        name,
        summary,
        format!("{message} -> {}", out.display()),
    ))
}

/// `export-source FORMAT R2S_SOURCE`: `args.format` is openmc, mcnp, serpent or alara, `args.input`
/// the R2S source file, and for alara `args.shutdown_t_s` the shutdown time. ALARA writes one file
/// per cell, so it takes the whole of `out_dir`.
fn export_source_job(job: &Job) -> Result<Outcome, String> {
    let format = required_str(&job.args, "format")?;
    let input = required_str(&job.args, "input")?;
    let bytes = std::fs::read(input).map_err(|e| format!("cannot read {input}: {e}"))?;
    if format == "alara" {
        let shutdown_t_s = required_f64(&job.args, "shutdown_t_s")?;
        let message =
            write_alara_export(&bytes, shutdown_t_s, &job.out_dir).map_err(failure_message)?;
        let index =
            read_bounded_json(&job.path("actinv-alara-index.json")).unwrap_or_else(|| json!({}));
        return Ok(Outcome::new(
            "actinv-alara-index.json",
            json!({"format": "alara", "step": index["step"], "cooling_s": index["cooling_s"]}),
            message,
        ));
    }
    let (doc, summary) = actinv_core::source_adapter::export_source(format, &bytes)?;
    let name = match format {
        "openmc" => "source_openmc.xml",
        "mcnp" => "source_mcnp.txt",
        _ => "source_serpent.txt",
    };
    let out = job.path(name);
    write_file(&out, &doc)?;
    let message = format!(
        "step {} {} source: {} cells, {} photons/s -> {}",
        summary["step"],
        summary["format"],
        summary["cells"],
        summary["total_photons_s"],
        out.display()
    );
    Ok(Outcome::new(name, summary, message))
}

/// `data verify [BUNDLE] [--output DIR]`: `args.bundle` and `args.output` are optional; the data
/// directory defaults to `$ACTINV_DATA_DIR`, else `./actinv-data`. A bundle that does not verify
/// is a failed check (exit 3) with the reason written.
fn data_verify_job(job: &Job) -> Result<Outcome, String> {
    let output = match arg_str(&job.args, "output") {
        Some(dir) => PathBuf::from(dir),
        None => match std::env::var_os("ACTINV_DATA_DIR") {
            Some(value) if !value.is_empty() => PathBuf::from(value),
            _ => PathBuf::from("actinv-data"),
        },
    };
    let out = job.path("data_verify.json");
    match verify_bundle(arg_str(&job.args, "bundle"), &output) {
        Ok(summary) => {
            let doc = serde_json::to_value(&summary).map_err(|e| e.to_string())?;
            write_json(&out, &doc)?;
            Ok(Outcome::new(
                "data_verify.json",
                json!({
                    "verified": true,
                    "bundle": summary.bundle,
                    "catalog_version": summary.catalog_version,
                    "root": summary.root,
                    "files": summary.files.len(),
                }),
                format!(
                    "Verified {} files in {}.",
                    summary.files.len(),
                    summary.root
                ),
            ))
        }
        Err(error) => {
            let summary = json!({"verified": false, "error": error});
            write_json(&out, &summary)?;
            Ok(Outcome {
                exit: 3,
                ..Outcome::new("data_verify.json", summary, error)
            })
        }
    }
}

/// `doctor [PROBLEM]`: the document, when given, is the problem whose input files are checked. A
/// problem found is a failed check (exit 3) with the report written; a problem document that does
/// not parse is an error.
fn doctor_job(job: &Job) -> Result<Outcome, String> {
    let problem = if job.document.is_null() {
        None
    } else {
        Some(read_text(&job.write_document("input.json")?)?)
    };
    let report = match &problem {
        Some(text) => doctor_report(Some(&|| Ok(text.clone()))),
        None => doctor_report(None),
    };
    let mut text = report.text;
    let (exit, message) = match report.failure {
        None => (0, text.lines().last().unwrap_or_default().to_string()),
        Some((2, message)) => return Err(message),
        Some((_, message)) => {
            text.push_str(&message);
            text.push('\n');
            (3, message.lines().next().unwrap_or_default().to_string())
        }
    };
    write_file(&job.path("doctor.txt"), &text)?;
    Ok(Outcome {
        exit,
        ..Outcome::new(
            "doctor.txt",
            json!({"ready": exit == 0, "report": text}),
            message,
        )
    })
}

// ---------------------------------------------------------------------------
// Progress
// ---------------------------------------------------------------------------

/// How far a running command has got.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Progress {
    pub done: usize,
    /// The planned total; `None` when it is not known.
    pub total: Option<usize>,
    pub note: String,
}

/// What `command` has written to `out_dir` so far, if it writes as it goes: `optimize` ledgers each
/// evaluation (the total is the planned budget; the search may stop sooner), `study-run` records
/// each case. A mesh run writes its result in one step, so it has no progress. Reads are bounded.
pub fn progress(command: &Command, out_dir: &Path) -> Option<Progress> {
    match command {
        Command::Optimize => {
            let ledger = read_bounded(&out_dir.join("optimize_ledger.jsonl"))?;
            let done = ledger.iter().filter(|byte| **byte == b'\n').count();
            let total = read_bounded_json(&out_dir.join("input.json")).and_then(|spec| {
                let init = spec["optimizer"]["init_points"].as_u64()? as usize;
                let refine = spec["optimizer"]["refine_points"].as_u64()? as usize;
                Some((init.min(optimize::MAX_EVALS) + refine).min(optimize::MAX_EVALS))
            });
            Some(Progress {
                done,
                total,
                note: "evaluations ledgered".into(),
            })
        }
        Command::StudyRun => {
            let total = read_bounded_json(&out_dir.join("manifest.json"))
                .and_then(|manifest| manifest["n_cases"].as_u64())
                .map(|n| n as usize);
            let recorded = read_bounded_json(&out_dir.join("study_record.json")).and_then(|r| {
                r["cases_completed"]
                    .as_u64()
                    .or_else(|| r["population"]["declared"].as_u64())
            });
            let done = match recorded {
                Some(n) => n as usize,
                None => count_case_outputs(&out_dir.join("cases")),
            };
            Some(Progress {
                done,
                total,
                note: "cases run".into(),
            })
        }
        _ => None,
    }
}

/// The case directories under `cases` that hold an `out.json`.
fn count_case_outputs(cases: &Path) -> usize {
    std::fs::read_dir(cases).map_or(0, |entries| {
        entries
            .flatten()
            .take(MAX_LISTED_FILES * 100)
            .filter(|entry| entry.path().join("out.json").is_file())
            .count()
    })
}

/// The first `MAX_PROGRESS_BYTES` of a file; `None` when it cannot be read.
fn read_bounded(path: &Path) -> Option<Vec<u8>> {
    let mut bytes = Vec::new();
    std::fs::File::open(path)
        .ok()?
        .take(MAX_PROGRESS_BYTES)
        .read_to_end(&mut bytes)
        .ok()?;
    Some(bytes)
}

/// A JSON file of at most `MAX_PROGRESS_BYTES`; `None` when it is larger, unreadable or not JSON.
fn read_bounded_json(path: &Path) -> Option<Value> {
    let bytes = read_bounded(path)?;
    if bytes.len() as u64 >= MAX_PROGRESS_BYTES {
        return None;
    }
    serde_json::from_slice(&bytes).ok()
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

fn utf8(path: &Path) -> Result<&str, String> {
    path.to_str()
        .ok_or_else(|| format!("{} is not valid UTF-8", path.display()))
}

fn failure_message((_, message): Failure) -> String {
    message
}

fn read_text(path: &Path) -> Result<String, String> {
    std::fs::read_to_string(path).map_err(|e| format!("cannot read {}: {e}", path.display()))
}

fn write_file(path: &Path, contents: impl AsRef<[u8]>) -> Result<(), String> {
    std::fs::write(path, contents).map_err(|e| format!("cannot write {}: {e}", path.display()))
}

fn write_json(path: &Path, value: &Value) -> Result<(), String> {
    let text = serde_json::to_string_pretty(value).map_err(|e| e.to_string())?;
    write_file(path, format!("{text}\n"))
}

/// The scalar fields of an object, and the length of each array as `n_<key>`: a headline that
/// stays small however large the result is.
fn headline(doc: &Value) -> Value {
    let mut out = Map::new();
    for (key, value) in doc.as_object().into_iter().flatten() {
        match value {
            Value::String(text) if text.len() <= 200 => {
                out.insert(key.clone(), value.clone());
            }
            Value::Bool(_) | Value::Number(_) => {
                out.insert(key.clone(), value.clone());
            }
            Value::Array(items) => {
                out.insert(format!("n_{key}"), json!(items.len()));
            }
            _ => {}
        }
        if out.len() >= 32 {
            break;
        }
    }
    Value::Object(out)
}

/// A summary as given when it is small, else its headline.
fn bounded(summary: Value) -> Value {
    if summary.to_string().len() > MAX_SUMMARY_BYTES {
        headline(&summary)
    } else {
        summary
    }
}

/// Every file under `dir`, relative to it, sorted; symbolic links are not followed.
fn list_files(dir: &Path) -> Vec<PathBuf> {
    fn walk(root: &Path, dir: &Path, depth: usize, files: &mut Vec<PathBuf>) {
        let Ok(entries) = std::fs::read_dir(dir) else {
            return;
        };
        for entry in entries.flatten() {
            if files.len() >= MAX_LISTED_FILES {
                return;
            }
            let Ok(kind) = entry.file_type() else {
                continue;
            };
            let path = entry.path();
            if kind.is_file() {
                if let Ok(relative) = path.strip_prefix(root) {
                    files.push(relative.to_path_buf());
                }
            } else if kind.is_dir() && depth < 4 {
                walk(root, &path, depth + 1, files);
            }
        }
    }
    let mut files = Vec::new();
    walk(dir, dir, 0, &mut files);
    files.sort();
    files
}

fn arg_str<'a>(args: &'a Value, key: &str) -> Option<&'a str> {
    args.get(key).and_then(Value::as_str)
}

fn arg_bool(args: &Value, key: &str) -> bool {
    args.get(key).and_then(Value::as_bool).unwrap_or(false)
}

fn required_str<'a>(args: &'a Value, key: &str) -> Result<&'a str, String> {
    arg_str(args, key).ok_or_else(|| format!("args.{key} is required (a string)"))
}

fn required_f64(args: &Value, key: &str) -> Result<f64, String> {
    arg_opt_f64(args, key)?.ok_or_else(|| format!("args.{key} is required (a number)"))
}

fn required_i64(args: &Value, key: &str) -> Result<i64, String> {
    args.get(key)
        .and_then(Value::as_i64)
        .ok_or_else(|| format!("args.{key} is required (an integer)"))
}

fn required_u64(args: &Value, key: &str) -> Result<u64, String> {
    arg_opt_u64(args, key)?.ok_or_else(|| format!("args.{key} is required (a positive integer)"))
}

fn arg_opt_f64(args: &Value, key: &str) -> Result<Option<f64>, String> {
    match args.get(key) {
        None | Some(Value::Null) => Ok(None),
        Some(value) => value
            .as_f64()
            .map(Some)
            .ok_or_else(|| format!("args.{key} must be a number")),
    }
}

fn arg_opt_u64(args: &Value, key: &str) -> Result<Option<u64>, String> {
    match args.get(key) {
        None | Some(Value::Null) => Ok(None),
        Some(value) => value
            .as_u64()
            .map(Some)
            .ok_or_else(|| format!("args.{key} must be a positive integer")),
    }
}

/// The one-based `args.step`, a number or a numeric string.
fn step_arg(args: &Value) -> Result<usize, String> {
    let step = match args.get("step") {
        Some(Value::Number(n)) => n.as_u64().ok_or("args.step must be a positive integer")?,
        Some(Value::String(text)) => text
            .parse::<u64>()
            .map_err(|_| format!("STEP must be a positive integer, got '{text}'"))?,
        _ => return Err("args.step is required (a one-based step)".into()),
    };
    if step == 0 {
        return Err("STEP is one-based and must be positive".into());
    }
    Ok(step as usize)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};

    /// A fresh directory under the system temporary directory, removed on drop.
    struct Scratch(PathBuf);

    impl Scratch {
        fn new(tag: &str) -> Scratch {
            static NEXT: AtomicUsize = AtomicUsize::new(0);
            let path = std::env::temp_dir().join(format!(
                "actinv-gui-jobs-{tag}-{}-{}",
                std::process::id(),
                NEXT.fetch_add(1, Ordering::Relaxed)
            ));
            std::fs::create_dir_all(&path).unwrap();
            Scratch(path)
        }

        fn path(&self, name: &str) -> PathBuf {
            self.0.join(name)
        }

        fn write(&self, name: &str, text: &str) -> PathBuf {
            let path = self.path(name);
            std::fs::create_dir_all(path.parent().unwrap()).unwrap();
            std::fs::write(&path, text).unwrap();
            path
        }
    }

    impl Drop for Scratch {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }

    fn envelope(command: &str, out_dir: &Path, document: Value, args: Value) -> String {
        json!({
            "schema": JOB_SCHEMA,
            "command": command,
            "document": document,
            "out_dir": out_dir,
            "args": args,
        })
        .to_string()
    }

    fn job(command: Command, out_dir: &Path, document: Value, args: Value) -> Job {
        Job {
            command,
            document,
            out_dir: out_dir.to_path_buf(),
            args,
        }
    }

    const BOUNDS: &str = r#"{
      "schema": "actinv-waste-bounds-spec-1",
      "rules": "us-nrc-10cfr61.55-v1",
      "components": [{
        "id": "example-component",
        "mass_g": 1000,
        "displaced_volume_cm3": 128.2,
        "waste_type": "general",
        "nuclide_properties": {
          "C14": {"z": 6, "half_life_s": 180808200000, "alpha_emitting": false}
        },
        "external_tritium": {"status": "not_applicable"},
        "targets": [{
          "step": 1,
          "t_s": 0,
          "activity_bounds_bq": {
            "C14": {"lower_bq": 100, "upper_bq": 200}
          },
          "inventory_coverage": "complete",
          "unbounded_inventory_reasons": [],
          "bounds_source": "illustrative declared enclosure",
          "bounds_assumptions": "artificial input; not measured or probability-based"
        }]
      }]
    }"#;

    const R2S_SOURCE: &str = concat!(
        r#"{"record":"header","schema":"actinv-r2s-source-1","step":2}"#,
        "\n",
        r#"{"record":"cell","id":"a","bounds_cm":[[0.0,1.0],[0.0,1.0],[0.0,1.0]],"volume_cm3":1.0,"photons_s":3.0,"groups":[{"centroid_eV":1.0e5,"photons_s":1.0},{"centroid_eV":2.0e5,"photons_s":2.0}]}"#,
        "\n",
        r#"{"record":"footer","cell_count":1}"#,
        "\n"
    );

    const ALARA_SOURCE: &str = concat!(
        r#"{"record":"header","schema":"actinv-r2s-source-1","step":4}"#,
        "\n",
        r#"{"record":"cell","id":"cell-a","bounds_cm":[[0.0,1.0],[0.0,1.0],[0.0,1.0]],"volume_cm3":2.0,"photons_s":3.0,"step_t_s":5000.0,"groups":[{"centroid_eV":1.0e5,"photons_s":1.0},{"centroid_eV":2.0e5,"photons_s":2.0}]}"#,
        "\n",
        r#"{"record":"footer","cell_count":1}"#,
        "\n"
    );

    #[test]
    fn envelope_parses_and_round_trips() {
        let dir = std::env::temp_dir().join("actinv-gui-jobs-envelope");
        let text = envelope("decide", &dir, json!({"a": 1}), json!({"resume": false}));
        let job = Job::from_json(&text).unwrap();
        assert_eq!(job.command, Command::Decide);
        assert_eq!(job.out_dir, dir);
        assert_eq!(job.document["a"], 1);
        assert_eq!(Job::from_json(&job.to_value().to_string()).unwrap(), job);
        // absent args are an empty object
        let bare = json!({"schema": JOB_SCHEMA, "command": "doctor", "out_dir": dir});
        assert_eq!(Job::from_json(&bare.to_string()).unwrap().args, json!({}));
    }

    #[test]
    fn envelope_refusals() {
        let dir = std::env::temp_dir().join("actinv-gui-jobs-refusals");
        let relative = envelope("decide", Path::new("out"), json!({}), json!({}));
        assert!(Job::from_json(&relative).unwrap_err().contains("absolute"));
        let unknown = envelope("frobnicate", &dir, json!({}), json!({}));
        assert!(Job::from_json(&unknown).unwrap_err().contains("frobnicate"));
        let wrong_schema = envelope("decide", &dir, json!({}), json!({})).replace("job-1", "job-9");
        assert!(Job::from_json(&wrong_schema)
            .unwrap_err()
            .contains(JOB_SCHEMA));
        let no_document = envelope("decide", &dir, Value::Null, json!({}));
        assert!(Job::from_json(&no_document)
            .unwrap_err()
            .contains("needs a document"));
        let bad_args = envelope("doctor", &dir, Value::Null, json!([1]));
        assert!(Job::from_json(&bad_args).unwrap_err().contains("args"));
        // a command with no document accepts none
        assert!(Job::from_json(&envelope("doctor", &dir, Value::Null, json!({}))).is_ok());
    }

    #[test]
    fn command_names_round_trip() {
        for command in Command::ALL {
            let value = serde_json::to_value(command).unwrap();
            assert_eq!(value, json!(command.name()));
            assert_eq!(serde_json::from_value::<Command>(value).unwrap(), command);
        }
        let unique: std::collections::BTreeSet<_> = Command::ALL.iter().map(|c| c.name()).collect();
        assert_eq!(unique.len(), Command::ALL.len());
    }

    #[test]
    fn out_dir_must_be_empty_unless_resuming() {
        let scratch = Scratch::new("outdir");
        let created = scratch.path("made/by/the/job");
        let fresh = job(Command::Doctor, &created, Value::Null, json!({}));
        fresh.prepare_out_dir().unwrap();
        assert!(created.is_dir());
        // an existing empty directory is accepted
        fresh.prepare_out_dir().unwrap();

        scratch.write("full/old.json", "{}");
        let full = scratch.path("full");
        let refused = job(Command::Decide, &full, json!({}), json!({}));
        assert!(refused.prepare_out_dir().unwrap_err().contains("not empty"));
        // resume does not open a command that cannot resume
        let decide = job(Command::Decide, &full, json!({}), json!({"resume": true}));
        assert!(decide.prepare_out_dir().is_err());
        let optimize = job(Command::Optimize, &full, json!({}), json!({}));
        assert!(optimize.prepare_out_dir().is_err());
        let resumed = job(Command::Optimize, &full, json!({}), json!({"resume": true}));
        resumed.prepare_out_dir().unwrap();
        // a file is not a directory
        let file = scratch.path("full/old.json");
        let on_file = job(Command::Doctor, &file, Value::Null, json!({}));
        assert!(on_file
            .prepare_out_dir()
            .unwrap_err()
            .contains("not a directory"));
    }

    #[test]
    fn verification_maps_to_exit_three() {
        assert_eq!(
            verification_exit(&json!({"verification": {"verified": true}})),
            0
        );
        assert_eq!(
            verification_exit(&json!({"verification": {"skipped": true}})),
            0
        );
        assert_eq!(
            verification_exit(&json!({"verification": {"verified": false, "max_rel_dev": 1.0}})),
            3
        );
        assert_eq!(verification_exit(&json!({"verification": {}})), 3);
        // no verification object, no check: exit 0 (a decision that is not certified)
        assert_eq!(
            verification_exit(&json!({"verdict": {"certified": false}})),
            0
        );
        assert_eq!(verification_exit(&json!({"verification": "text"})), 0);
    }

    #[test]
    fn result_serialises_with_schema() {
        let result = JobResult {
            command: Command::WasteBounds,
            exit: 3,
            primary: Some(PathBuf::from("/x/main.json")),
            summary: json!({"k": 1}),
            files: vec![PathBuf::from("main.json")],
            message: "m".into(),
        };
        let value = result.to_value();
        assert_eq!(value["schema"], RESULT_SCHEMA);
        assert_eq!(value["command"], "waste-bounds");
        assert_eq!(value["exit"], 3);
        assert_eq!(value["files"], json!(["main.json"]));
        assert_eq!(JobResult::from_value(&value).unwrap(), result);
        assert!(JobResult::from_value(&json!({"schema": "other"})).is_err());
    }

    #[test]
    fn headline_is_small() {
        let doc = json!({
            "schema": "s", "ok": true, "n": 3, "long": "x".repeat(500),
            "cells": [1, 2, 3], "nested": {"a": 1},
        });
        assert_eq!(
            headline(&doc),
            json!({"schema": "s", "ok": true, "n": 3, "n_cells": 3})
        );
        let big = json!({"schema": "s", "rows": vec![1; 20_000]});
        assert_eq!(bounded(big), json!({"schema": "s", "n_rows": 20000}));
    }

    #[test]
    fn optimize_progress_reads_the_ledger_and_the_plan() {
        let scratch = Scratch::new("opt-progress");
        let dir = scratch.path("run");
        assert!(progress(&Command::Optimize, &dir).is_none());
        scratch.write(
            "run/optimize_ledger.jsonl",
            "{\"eval_id\":0}\n{\"eval_id\":1}\n{\"eval_id\":2}\n{\"eval",
        );
        // no spec yet: the count alone
        let p = progress(&Command::Optimize, &dir).unwrap();
        assert_eq!((p.done, p.total), (3, None));
        scratch.write(
            "run/input.json",
            r#"{"optimizer": {"init_points": 4, "refine_points": 6}}"#,
        );
        let p = progress(&Command::Optimize, &dir).unwrap();
        assert_eq!((p.done, p.total), (3, Some(10)));
        // the plan is capped at the optimizer's own limit
        scratch.write(
            "run/input.json",
            r#"{"optimizer": {"init_points": 500, "refine_points": 500}}"#,
        );
        let p = progress(&Command::Optimize, &dir).unwrap();
        assert_eq!(p.total, Some(optimize::MAX_EVALS));
    }

    #[test]
    fn study_progress_reads_the_record_then_the_case_folders() {
        let scratch = Scratch::new("study-progress");
        let dir = scratch.path("run");
        assert_eq!(
            progress(&Command::StudyRun, &dir),
            Some(Progress {
                done: 0,
                total: None,
                note: "cases run".into()
            })
        );
        scratch.write("run/manifest.json", r#"{"n_cases": 5, "cases": []}"#);
        scratch.write("run/cases/a/out.json", "{}");
        scratch.write("run/cases/b/out.json", "{}");
        scratch.write("run/cases/c/spec.json", "{}");
        let p = progress(&Command::StudyRun, &dir).unwrap();
        assert_eq!((p.done, p.total), (2, Some(5)));
        scratch.write(
            "run/study_record.json",
            r#"{"status": "partial", "cases_completed": 3, "cases_declared": 5}"#,
        );
        let p = progress(&Command::StudyRun, &dir).unwrap();
        assert_eq!((p.done, p.total), (3, Some(5)));
        scratch.write(
            "run/study_record.json",
            r#"{"status": "complete", "population": {"declared": 5}}"#,
        );
        assert_eq!(progress(&Command::StudyRun, &dir).unwrap().done, 5);
        // a command that writes its result in one piece has no progress
        assert!(progress(&Command::Mesh, &dir).is_none());
    }

    #[test]
    fn waste_bounds_runs_in_process() {
        let scratch = Scratch::new("bounds");
        let out = scratch.path("out");
        let document: Value = serde_json::from_str(BOUNDS).unwrap();
        let result = run(&job(Command::WasteBounds, &out, document, json!({}))).unwrap();
        assert_eq!(result.exit, 0);
        assert_eq!(result.primary, Some(out.join("waste_bounds.json")));
        assert_eq!(result.summary["schema"], "actinv-waste-bounds-result-1");
        assert_eq!(
            result.files,
            vec![
                PathBuf::from("input.json"),
                PathBuf::from("waste_bounds.json")
            ]
        );
        let written: Value =
            serde_json::from_str(&std::fs::read_to_string(out.join("waste_bounds.json")).unwrap())
                .unwrap();
        assert_eq!(written["schema"], "actinv-waste-bounds-result-1");
        // a second run into the same directory is refused
        let again = run(&job(
            Command::WasteBounds,
            &out,
            serde_json::from_str(BOUNDS).unwrap(),
            json!({}),
        ));
        assert!(again.unwrap_err().contains("not empty"));
    }

    #[test]
    fn export_source_runs_in_process() {
        let scratch = Scratch::new("export-source");
        let input = scratch.write("r2s.ndjson", R2S_SOURCE);
        let out = scratch.path("out");
        let args = json!({"format": "mcnp", "input": input});
        let result = run(&job(Command::ExportSource, &out, Value::Null, args)).unwrap();
        assert_eq!(result.exit, 0);
        assert_eq!(result.summary["step"], 2);
        let text = std::fs::read_to_string(out.join("source_mcnp.txt")).unwrap();
        assert!(text.contains("SI4 L 0.1 0.2"), "{text}");
        // the command line's own wording, which prints the format as a JSON string
        assert!(
            result.message.contains("step 2 \"mcnp\" source"),
            "{}",
            result.message
        );

        let alara_input = scratch.write("alara.ndjson", ALARA_SOURCE);
        let alara = scratch.path("alara");
        let args = json!({"format": "alara", "input": alara_input, "shutdown_t_s": 0.0});
        let result = run(&job(Command::ExportSource, &alara, Value::Null, args)).unwrap();
        assert_eq!(result.primary, Some(alara.join("actinv-alara-index.json")));
        assert_eq!(result.summary["step"], 4);
        assert!(result
            .files
            .contains(&PathBuf::from("actinv-alara-index.json")));
        assert_eq!(result.files.len(), 2, "{:?}", result.files);

        let missing = json!({"format": "mcnp", "input": scratch.path("none.ndjson")});
        let refused = run(&job(
            Command::ExportSource,
            &scratch.path("out2"),
            Value::Null,
            missing,
        ));
        assert!(refused.unwrap_err().contains("cannot read"));
        let no_format = run(&job(
            Command::ExportSource,
            &scratch.path("out3"),
            Value::Null,
            json!({}),
        ));
        assert!(no_format.unwrap_err().contains("args.format"));
    }

    #[test]
    fn export_mcnp_runs_in_process() {
        let scratch = Scratch::new("export-mcnp");
        let result_doc = json!({"steps": [
            {"step": 1},
            {"step": 2, "photon_source": {
                "group_structure": "test",
                "boundaries_eV": [1.0e4, 1.0e6],
                "lines": [],
                "groups": [{
                    "low_eV": 1.0e4, "high_eV": 1.0e6, "centroid_eV": 2.0e5,
                    "photons_s_g": 1.0, "photons_s": 1.0, "power_W_g": 0.0, "power_W": 0.0
                }],
                "by_nuclide": [],
                "grouped_photons_s_g": 1.0, "grouped_photons_s": 1.0,
                "total_photons_s_g": 1.0, "total_photons_s": 1.0,
                "source_power_W_g": 0.0, "source_power_W": 0.0,
                "ungrouped_power_W_g": 0.0,
                "unrepresented_gamma_power_W_g": 0.0,
                "represented_gamma_power_fraction": 1.0
            }}
        ]});
        let input = scratch.write("result.json", &result_doc.to_string());
        let run_step = |step: Value, tag: &str| {
            run(&job(
                Command::ExportMcnp,
                &scratch.path(tag),
                Value::Null,
                json!({"input": input, "step": step}),
            ))
        };
        let result = run_step(json!(2), "ok").unwrap();
        assert_eq!(
            result.primary,
            Some(scratch.path("ok/photon_source_mcnp.txt"))
        );
        assert!(
            !std::fs::read_to_string(scratch.path("ok/photon_source_mcnp.txt"))
                .unwrap()
                .is_empty()
        );
        // the step may be given as text, as on the command line
        assert!(run_step(json!("2"), "text").is_ok());
        // the refusals are the command line's
        assert!(run_step(json!(0), "zero")
            .unwrap_err()
            .contains("one-based"));
        assert!(run_step(json!(3), "absent")
            .unwrap_err()
            .contains("has no step 3"));
        assert!(run_step(json!(1), "bare")
            .unwrap_err()
            .contains("has no photon_source"));
    }
}
