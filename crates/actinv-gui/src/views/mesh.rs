//! Mesh & transport: activate every cell of a transport flux mesh, give the transport code its
//! shutdown-dose source back, and check which cells clear their limits. Four steps on one page,
//! each open once the one before it is done: the flux (`actinv import-flux`), the mesh run
//! (`actinv mesh`), the source (`actinv export-openmc-mesh`, `export-r2s`, `export-r2s-joint`,
//! `export-source`) and the facility check (`actinv twin`). This module holds the state, builds
//! the documents the commands read, reads what they write and draws the forms; the desktop page
//! runs the commands.
use crate::{
    model::{self, ResultDocument},
    views::{summary, waste_forms},
};
use eframe::egui::{self, Color32, RichText};
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};
use std::{
    fs::File,
    io::{BufRead, BufReader, Read},
    path::{Path, PathBuf},
};

/// The covariance a banded mesh reads; the same one the waste and certify pages use.
pub const COVARIANCE: &str = "catalog:tendl-2025-neutron-709g-covariance";
/// The most cell rows the page keeps for its table; the counts and totals cover every cell.
const KEPT_ROWS: usize = 500;
/// The most photon-carrying nuclides a banded mesh adds as responses.
const MAX_BANDED_NUCLIDES: usize = 40;

/// Where the flux comes from.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum FluxFormat {
    Openmc,
    Meshtal,
    Mctal,
    Fispact,
    Actinv,
}

impl FluxFormat {
    pub const ALL: [FluxFormat; 5] = [
        FluxFormat::Openmc,
        FluxFormat::Meshtal,
        FluxFormat::Mctal,
        FluxFormat::Fispact,
        FluxFormat::Actinv,
    ];

    pub fn label(self) -> &'static str {
        match self {
            FluxFormat::Openmc => "OpenMC statepoint",
            FluxFormat::Meshtal => "MCNP meshtal",
            FluxFormat::Mctal => "MCNP mctal",
            FluxFormat::Fispact => "FISPACT-II fluxes",
            FluxFormat::Actinv => "ACTINV flux file",
        }
    }

    /// The `import-flux` format name; `None` for a flux file ACTINV wrote already.
    pub fn name(self) -> Option<&'static str> {
        match self {
            FluxFormat::Openmc => Some("openmc"),
            FluxFormat::Meshtal => Some("meshtal"),
            FluxFormat::Mctal => Some("mctal"),
            FluxFormat::Fispact => Some("fispact"),
            FluxFormat::Actinv => None,
        }
    }

    /// Whether the format names a tally and needs the source rate to make it absolute.
    pub fn tally(self) -> bool {
        matches!(
            self,
            FluxFormat::Openmc | FluxFormat::Meshtal | FluxFormat::Mctal
        )
    }

    pub fn filter(self) -> (&'static str, &'static [&'static str]) {
        match self {
            FluxFormat::Openmc => ("OpenMC statepoint", &["h5"]),
            FluxFormat::Meshtal => ("MCNP meshtal", &["msht", "meshtal", "txt"]),
            FluxFormat::Mctal => ("MCNP mctal", &["m", "mctal", "txt"]),
            FluxFormat::Fispact => ("FISPACT-II fluxes", &["fluxes", "txt", "dat"]),
            FluxFormat::Actinv => ("ACTINV flux file", &["ndjson"]),
        }
    }
}

/// A flux file a mesh run can read, pinned by its hash.
#[derive(Clone, Debug, PartialEq)]
pub struct Flux {
    pub path: PathBuf,
    pub sha256: String,
    pub cells: u64,
    pub groups: u64,
    pub units: String,
}

/// One cell's numbers at every step.
#[derive(Clone, Debug, PartialEq)]
pub struct CellRow {
    pub id: String,
    pub volume_cm3: Option<f64>,
    /// (activity Bq/g, heat W/g, contact dose Gy/h) per step.
    pub steps: Vec<(f64, f64, Option<f64>)>,
}

/// A mesh result read for the page.
pub struct MeshRun {
    pub path: PathBuf,
    /// The mesh spec it was solved from, when the page wrote it; the correlated source needs it.
    pub spec: Option<PathBuf>,
    pub cells: u64,
    pub complete: bool,
    pub rows: Vec<CellRow>,
    /// The first cell's result, to say when each step is.
    pub first: Option<ResultDocument>,
    /// The banded responses of the first cell, for the facility limits.
    pub banded: Vec<String>,
    pub geometry: bool,
    pub photons: bool,
}

impl MeshRun {
    pub fn steps(&self) -> usize {
        self.first.as_ref().map_or(0, |r| r.steps().len())
    }

    /// When step `index` (zero-based) is, in words.
    pub fn when(&self, index: usize) -> String {
        self.first
            .as_ref()
            .map(|r| summary::when(r, index))
            .unwrap_or_default()
    }

    /// The end of irradiation, s: what ALARA counts its cooling from.
    pub fn shutdown_s(&self) -> Option<f64> {
        let first = self.first.as_ref()?;
        let i = summary::shutdown_step(first)?;
        first.steps()[i]["t_s"].as_f64()
    }

    /// Every cell id, for the components.
    pub fn ids(&self) -> Vec<&str> {
        self.rows.iter().map(|r| r.id.as_str()).collect()
    }
}

/// The source the transport code gets back.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum SourceKind {
    /// One OpenMC source per cell (`export-openmc-mesh`).
    PerCell,
    /// The banded source (`export-r2s`), converted for one code (`export-source`).
    Banded,
}

/// The code the banded source is written for.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Code {
    Openmc,
    Mcnp,
    Serpent,
    Alara,
}

impl Code {
    pub const ALL: [Code; 4] = [Code::Openmc, Code::Mcnp, Code::Serpent, Code::Alara];

    pub fn label(self) -> &'static str {
        match self {
            Code::Openmc => "OpenMC (XML)",
            Code::Mcnp => "MCNP (SDEF)",
            Code::Serpent => "Serpent",
            Code::Alara => "ALARA",
        }
    }

    pub fn name(self) -> &'static str {
        match self {
            Code::Openmc => "openmc",
            Code::Mcnp => "mcnp",
            Code::Serpent => "serpent",
            Code::Alara => "alara",
        }
    }
}

/// A clearance limit of the facility check.
#[derive(Clone, Debug, PartialEq)]
pub struct Limit {
    pub name: String,
    pub response: String,
    pub value: f64,
    pub at_most: bool,
}

/// A named group of cells the facility check gives one verdict.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Component {
    pub name: String,
    pub cells: String,
}

/// The source the page wrote: the files, and the banded source read back for its table.
pub struct Written {
    pub files: Vec<PathBuf>,
    pub banded: Option<Value>,
}

pub struct MeshState {
    pub format: FluxFormat,
    pub source: Option<PathBuf>,
    pub tally: u64,
    pub source_rate: f64,
    pub groups: Option<PathBuf>,
    pub flux: Option<Flux>,
    /// The form is open to bring in a different flux.
    pub change_flux: bool,
    pub bands: bool,
    pub threads: usize,
    pub mesh: Option<MeshRun>,
    /// A mesh run that stopped before its last cell, in this folder; it can resume.
    pub unfinished: Option<PathBuf>,
    /// The step the table and the source read (zero-based).
    pub step: usize,
    pub kind: SourceKind,
    pub code: Code,
    pub joint: bool,
    pub written: Option<Written>,
    pub limits: Vec<Limit>,
    pub components: Vec<Component>,
    /// The step the limits apply at (zero-based); `None` applies them at every step.
    pub twin_step: Option<usize>,
    pub twin: Option<Value>,
}

impl Default for MeshState {
    fn default() -> Self {
        Self {
            format: FluxFormat::Openmc,
            source: None,
            tally: 1,
            source_rate: 0.,
            groups: None,
            flux: None,
            change_flux: false,
            bands: true,
            threads: 2,
            mesh: None,
            unfinished: None,
            step: 0,
            kind: SourceKind::Banded,
            code: Code::Openmc,
            joint: false,
            written: None,
            limits: Vec::new(),
            components: Vec::new(),
            twin_step: None,
            twin: None,
        }
    }
}

/// What the page must do after the form was drawn.
#[derive(Clone, Debug, PartialEq)]
pub enum MeshAction {
    None,
    PickSource,
    PickGroups,
    Import,
    Activate,
    Resume,
    OpenMesh,
    OpenCell(String),
    WriteSource,
    SaveSource,
    Check,
    OpenData,
}

/// The `import-flux` arguments for the chosen file.
pub fn import_args(state: &MeshState) -> Result<Value, String> {
    let format = state
        .format
        .name()
        .ok_or("An ACTINV flux file needs no import.")?;
    let source = state
        .source
        .as_ref()
        .ok_or("Choose the file your transport run wrote.")?;
    let mut args = json!({"format": format, "source": path_text(source)?});
    if state.format.tally() {
        if state.tally == 0 {
            return Err("The tally ID is a positive number.".into());
        }
        if !(state.source_rate.is_finite() && state.source_rate > 0.) {
            return Err("Give the source rate: source particles per second.".into());
        }
        args["tally"] = json!(state.tally);
        args["source_rate"] = json!(state.source_rate);
    } else {
        let groups = state
            .groups
            .as_ref()
            .ok_or("Choose the group structure the FISPACT-II fluxes use.")?;
        args["groups"] = json!(path_text(groups)?);
    }
    Ok(args)
}

fn path_text(path: &Path) -> Result<String, String> {
    path.to_str()
        .map(str::to_owned)
        .ok_or_else(|| format!("{} is not valid UTF-8", path.display()))
}

/// A flux file ACTINV wrote: its header and its hash.
pub fn read_flux(path: &Path) -> Result<Flux, String> {
    let mut file = File::open(path).map_err(|e| format!("cannot open {}: {e}", path.display()))?;
    let mut hasher = Sha256::new();
    let mut buffer = vec![0u8; 1 << 20];
    let mut first = Vec::new();
    loop {
        let n = file
            .read(&mut buffer)
            .map_err(|e| format!("cannot read {}: {e}", path.display()))?;
        if n == 0 {
            break;
        }
        if !first.contains(&b'\n') && first.len() < 1 << 24 {
            first.extend_from_slice(&buffer[..n]);
        }
        hasher.update(&buffer[..n]);
    }
    let line = first.split(|b| *b == b'\n').next().unwrap_or_default();
    let header: Value = serde_json::from_slice(line)
        .map_err(|_| format!("{} does not start with a flux header", path.display()))?;
    if header["schema"] != "actinv-flux-1" {
        return Err(format!(
            "{} is not an ACTINV flux file (actinv-flux-1); import the tally instead",
            path.display()
        ));
    }
    Ok(Flux {
        path: path.to_owned(),
        sha256: hasher
            .finalize()
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect(),
        cells: header["cell_count"].as_u64().unwrap_or(0),
        groups: header["energy_boundaries_eV"]
            .as_array()
            .map_or(0, |b| b.len().saturating_sub(1) as u64),
        units: header["flux_units"].as_str().unwrap_or("").to_owned(),
    })
}

/// The nuclides that carry the decay photons in a result, strongest first: the activities a
/// banded source needs bands for.
pub fn photon_nuclides(result: &Value) -> Vec<String> {
    let mut strongest: Vec<(String, f64)> = Vec::new();
    for step in result["steps"].as_array().into_iter().flatten() {
        let total = step["photon_source"]["total_photons_s_g"]
            .as_f64()
            .unwrap_or(0.);
        if total <= 0. {
            continue;
        }
        for entry in step["photon_source"]["by_nuclide"]
            .as_array()
            .into_iter()
            .flatten()
        {
            let Some(name) = entry["nuclide"].as_str() else {
                continue;
            };
            let share = entry["groups"]
                .as_array()
                .into_iter()
                .flatten()
                .map(|g| g["photons_s_g"].as_f64().unwrap_or(0.))
                .sum::<f64>()
                / total;
            match strongest.iter_mut().find(|(n, _)| n == name) {
                Some((_, best)) => *best = best.max(share),
                None => strongest.push((name.to_owned(), share)),
            }
        }
    }
    strongest.retain(|(_, share)| *share >= 1e-4);
    strongest.sort_by(|a, b| b.1.total_cmp(&a.1).then_with(|| a.0.cmp(&b.0)));
    strongest
        .into_iter()
        .take(MAX_BANDED_NUCLIDES)
        .map(|(name, _)| name)
        .collect()
}

/// What the run spec carries that a mesh run leaves out or refuses, in words.
pub fn mesh_notes(spec: &Value) -> (Vec<String>, Option<String>) {
    let mut notes = Vec::new();
    for (key, what) in [
        ("pathways", "pathways"),
        ("kerma", "KERMA"),
        ("overrides", "named data overrides"),
    ] {
        let set = match &spec[key] {
            Value::Null => false,
            Value::Array(list) => !list.is_empty(),
            _ => true,
        };
        if set {
            notes.push(format!(
                "The problem's {what} are left out: a mesh run does not compute them."
            ));
        }
    }
    let photons = spec["options"]["outputs"].as_array().is_none_or(|outputs| {
        outputs
            .iter()
            .any(|o| matches!(o.as_str(), Some("photons" | "dose")))
    });
    if !photons {
        notes.push(
            "The problem does not ask for photons, so there will be no shutdown-dose source; ask for photons or dose under What to compute."
                .into(),
        );
    }
    let refused = spec["schedule"]
        .as_array()
        .into_iter()
        .flatten()
        .position(|step| !step["spectrum"].is_null())
        .map(|i| {
            format!(
                "Step {} of the schedule has its own spectrum; in a mesh run each cell's flux is the spectrum, so take it out under Irradiation & cooling.",
                i + 1
            )
        });
    (notes, refused)
}

/// The mesh spec: the run spec as it is set up now, with each cell's flux in place of the
/// spectrum. `bands` are the responses to band when the problem has no uncertainty of its own.
pub fn mesh_spec(
    spec: &Value,
    flux: &Flux,
    bands: Option<&[String]>,
    threads: usize,
) -> Result<Value, String> {
    if let (_, Some(refused)) = mesh_notes(spec) {
        return Err(refused);
    }
    let mut out = Map::new();
    out.insert("spec".into(), json!("actinv-mesh-spec-1"));
    for key in [
        "title",
        "projectile",
        "library",
        "decay",
        "material",
        "schedule",
        "options",
        "photon",
        "fission_yields",
        "uncertainty",
        "radiological",
        "damage",
        "self_shielding",
        "summary",
    ] {
        if !spec[key].is_null() {
            out.insert(key.into(), spec[key].clone());
        }
    }
    out.insert(
        "flux".into(),
        json!({"path": path_text(&flux.path)?, "sha256": flux.sha256}),
    );
    if let Some(nuclides) = bands {
        let mut responses = vec!["activity.total".to_owned(), "heat.total".to_owned()];
        responses.extend(nuclides.iter().map(|n| format!("activity:{n}")));
        match out.get_mut("uncertainty") {
            Some(Value::Object(own)) => {
                let list = own.entry("responses").or_insert_with(|| json!([]));
                if let Value::Array(list) = list {
                    for response in responses {
                        if !list.iter().any(|r| r.as_str() == Some(&response)) {
                            list.push(json!(response));
                        }
                    }
                }
            }
            _ => {
                out.insert(
                    "uncertainty".into(),
                    json!({"covariance": {"path": COVARIANCE}, "responses": responses,
                           "confidence_level": 0.95}),
                );
            }
        }
    }
    out.insert("threads".into(), json!(threads.clamp(1, 256)));
    // the output is its own checkpoint, so a stopped run resumes where it stopped
    out.insert("resume".into(), json!(true));
    Ok(Value::Object(out))
}

/// Read a mesh result: the counts, the first cells' numbers, and what it can be used for.
pub fn read_mesh(path: &Path, spec: Option<PathBuf>) -> Result<MeshRun, String> {
    let file = File::open(path).map_err(|e| format!("cannot open {}: {e}", path.display()))?;
    let mut run = MeshRun {
        path: path.to_owned(),
        spec,
        cells: 0,
        complete: false,
        rows: Vec::new(),
        first: None,
        banded: Vec::new(),
        geometry: false,
        photons: false,
    };
    let mut header = false;
    for line in BufReader::new(file).lines() {
        let line = line.map_err(|e| format!("cannot read {}: {e}", path.display()))?;
        if line.trim().is_empty() {
            continue;
        }
        let Ok(record) = serde_json::from_str::<Value>(&line) else {
            // a run stopped mid-write leaves a torn last line; resuming drops it
            break;
        };
        match record["record"].as_str() {
            Some("header") => {
                if record["schema"] != "actinv-mesh-result-1" {
                    return Err(format!(
                        "{} is not a mesh result (actinv-mesh-result-1)",
                        path.display()
                    ));
                }
                header = true;
                run.geometry = !record["geometry"].is_null();
            }
            Some("cell") => {
                run.cells += 1;
                let steps = record["result"]["steps"].as_array();
                if run.first.is_none() {
                    run.geometry |= !record["bounds_cm"].is_null();
                    if let Some(steps) = steps {
                        run.photons = steps.iter().any(|s| s["photon_source"].is_object());
                        let mut banded: Vec<String> = steps
                            .iter()
                            .flat_map(|s| {
                                s["uncertainty"]["responses"]
                                    .as_object()
                                    .into_iter()
                                    .flat_map(|r| r.keys().cloned())
                            })
                            .collect();
                        banded.sort();
                        banded.dedup();
                        run.banded = banded;
                    }
                    run.first =
                        ResultDocument::parse(record["result"].clone(), "first cell".into()).ok();
                }
                if run.rows.len() < KEPT_ROWS {
                    run.rows.push(CellRow {
                        id: record["id"].as_str().unwrap_or("?").to_owned(),
                        volume_cm3: record["volume_cm3"].as_f64(),
                        steps: steps
                            .into_iter()
                            .flatten()
                            .map(|s| {
                                (
                                    model::total_activity(s),
                                    model::metric(s, 1, ""),
                                    s["photon_source"]["contact_gamma_air_dose_proxy_Gy_h"]
                                        .as_f64(),
                                )
                            })
                            .collect(),
                    });
                }
            }
            Some("footer") => run.complete = true,
            _ => {}
        }
    }
    if !header {
        return Err(format!("{} has no mesh result header", path.display()));
    }
    Ok(run)
}

/// One cell's full result from a mesh result, to read on the result pages.
pub fn cell_result(path: &Path, id: &str) -> Result<ResultDocument, String> {
    let file = File::open(path).map_err(|e| format!("cannot open {}: {e}", path.display()))?;
    for line in BufReader::new(file).lines() {
        let line = line.map_err(|e| format!("cannot read {}: {e}", path.display()))?;
        let Ok(record) = serde_json::from_str::<Value>(&line) else {
            continue;
        };
        if record["record"] == "cell" && record["id"] == id {
            let mut result = record["result"].clone();
            if let Some(title) = result["spec_title"].as_str() {
                result["spec_title"] = json!(format!("{title}, cell {id}"));
            }
            return ResultDocument::parse(result, format!("mesh cell {id}"));
        }
    }
    Err(format!("{} has no cell {id}", path.display()))
}

/// The banded source written by `export-r2s`: its cells and footer, for the table.
pub fn read_banded(path: &Path) -> Result<Value, String> {
    let text = std::fs::read_to_string(path)
        .map_err(|e| format!("cannot read {}: {e}", path.display()))?;
    let mut cells = Vec::new();
    let mut footer = Value::Null;
    for line in text.lines().filter(|l| !l.trim().is_empty()) {
        let record: Value = serde_json::from_str(line).map_err(|e| e.to_string())?;
        match record["record"].as_str() {
            Some("cell") if cells.len() < KEPT_ROWS => cells.push(json!({
                "id": record["id"],
                "photons_s": record["photons_s"],
                "independent": record["sigma_photons_s_independent"],
                "conservative": record["sigma_photons_s_conservative"],
                "unbanded": record["coverage"]["unbanded_photon_share"],
            })),
            Some("footer") => footer = record,
            _ => {}
        }
    }
    Ok(json!({"cells": cells, "footer": footer}))
}

/// The facility check spec.
pub fn twin_spec(state: &MeshState, mesh: &MeshRun) -> Result<Value, String> {
    if state.limits.is_empty() {
        return Err("Add at least one limit.".into());
    }
    let mut limits = Vec::new();
    for (i, limit) in state.limits.iter().enumerate() {
        let name = if limit.name.trim().is_empty() {
            format!("limit-{}", i + 1)
        } else {
            limit.name.trim().to_owned()
        };
        if !mesh.banded.contains(&limit.response) {
            return Err(format!(
                "{name}: the mesh has no band for {}; tick Uncertainty bands and activate again.",
                limit.response
            ));
        }
        if !limit.value.is_finite() {
            return Err(format!("{name}: the limit is not a number."));
        }
        limits.push(
            json!({"name": name, "response": limit.response, "limit": limit.value,
                           "sense": if limit.at_most { "le" } else { "ge" }}),
        );
    }
    let mut spec = json!({"spec": "actinv-twin-1", "mesh_output": path_text(&mesh.path)?,
                          "limits": limits});
    if let Some(step) = state.twin_step {
        let t = mesh
            .first
            .as_ref()
            .and_then(|r| r.steps().get(step).and_then(|s| s["t_s"].as_f64()))
            .ok_or("The mesh has no such step.")?;
        spec["times_s"] = json!([t]);
    }
    let components: Vec<&Component> = state
        .components
        .iter()
        .filter(|c| !c.name.trim().is_empty())
        .collect();
    if !components.is_empty() {
        let mut map = Map::new();
        for component in components {
            let cells: Vec<String> = component
                .cells
                .split([',', ' ', ';'])
                .map(str::trim)
                .filter(|c| !c.is_empty())
                .map(str::to_owned)
                .collect();
            if cells.is_empty() {
                return Err(format!("{}: list its cells.", component.name.trim()));
            }
            map.insert(component.name.trim().to_owned(), json!(cells));
        }
        spec["components"] = Value::Object(map);
    }
    Ok(spec)
}

/// A size such as a volume: plain when it reads well so, else in scientific notation.
fn plain(value: f64) -> String {
    if (1e-2..1e5).contains(&value.abs()) {
        let text = format!("{value:.4}");
        text.trim_end_matches('0').trim_end_matches('.').to_owned()
    } else {
        model::sci(value)
    }
}

/// When an absolute time is, in words: the step it is, or the time itself.
fn when_at(mesh: Option<&MeshRun>, t: f64) -> String {
    let step = mesh.and_then(|m| {
        let first = m.first.as_ref()?;
        let i = first.steps().iter().position(|s| {
            s["t_s"]
                .as_f64()
                .is_some_and(|ts| (ts - t).abs() <= 1e-9 * t.abs().max(1.))
        })?;
        Some(m.when(i))
    });
    step.unwrap_or_else(|| format!("t = {}", model::readable_time(t)))
}

/// A margin, (limit - band edge) / |limit|, in words: how far the band edge is under or over.
fn margin_words(margin: f64) -> String {
    if margin >= 0. {
        format!("{:.0}% under the limit", 100. * margin)
    } else if margin > -9. {
        format!("{:.0}% over the limit", -100. * margin)
    } else {
        format!("{:.0} times the limit", 1. - margin)
    }
}

/// The unit a response is in, for the limit field.
fn unit(response: &str) -> &'static str {
    if response.starts_with("heat") {
        "W/g"
    } else if response.starts_with("activity") {
        "Bq/g"
    } else if response.contains("dose") {
        "Sv/h"
    } else {
        ""
    }
}

/// Words for a response: "total activity", "Co60 activity".
fn response_words(response: &str) -> String {
    match response {
        "activity.total" => "total activity".into(),
        "heat.total" => "total decay heat".into(),
        other => match other.strip_prefix("activity:") {
            Some(nuclide) => format!("{nuclide} activity"),
            None => other.into(),
        },
    }
}

/// The step header of a card: the step number in a circle, the title and how far it is.
fn step_header(ui: &mut egui::Ui, number: usize, title: &str, done: Option<&str>, open: bool) {
    let accent = crate::visuals::accent(ui);
    let weak = ui.visuals().weak_text_color();
    ui.horizontal(|ui| {
        let size = 24.;
        let (rect, _) = ui.allocate_exact_size(egui::vec2(size, size), egui::Sense::hover());
        let painter = ui.painter();
        let colour = if open { accent } else { weak };
        if done.is_some() {
            painter.circle_filled(rect.center(), size / 2., colour);
        } else {
            painter.circle_stroke(
                rect.center(),
                size / 2. - 1.,
                egui::Stroke::new(1.5, colour),
            );
        }
        painter.text(
            rect.center(),
            egui::Align2::CENTER_CENTER,
            number.to_string(),
            egui::FontId::proportional(14.),
            match (done.is_some(), ui.visuals().dark_mode) {
                (false, _) => colour,
                (true, false) => Color32::WHITE,
                (true, true) => ui.visuals().panel_fill,
            },
        );
        ui.add_space(4.);
        ui.label(RichText::new(title).size(17.).color(if open {
            ui.visuals().strong_text_color()
        } else {
            weak
        }));
        if let Some(done) = done {
            ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                ui.label(RichText::new(done).weak());
            });
        }
    });
    ui.add_space(4.);
}

/// A file field: the button that picks it and the file's name, with the full path on hover.
fn file_field(ui: &mut egui::Ui, path: Option<&PathBuf>, button: &str) -> bool {
    ui.horizontal(|ui| {
        let clicked = ui.button(button).clicked();
        match path {
            Some(path) => {
                let name = path.file_name().map_or_else(
                    || path.display().to_string(),
                    |n| n.to_string_lossy().into_owned(),
                );
                ui.label(RichText::new(name).monospace())
                    .on_hover_text(path.display().to_string());
            }
            None => {
                ui.weak("none chosen");
            }
        }
        clicked
    })
    .inner
}

/// The step picker the table and the source share.
fn step_picker(ui: &mut egui::Ui, state: &mut MeshState, mesh: &MeshRun) {
    let count = mesh.steps();
    if count == 0 {
        return;
    }
    state.step = state.step.min(count - 1);
    egui::ComboBox::from_id_salt("mesh-step")
        .width(260.)
        .selected_text(format!(
            "Step {}, {}",
            state.step + 1,
            mesh.when(state.step)
        ))
        .show_ui(ui, |ui| {
            for i in 0..count {
                ui.selectable_value(
                    &mut state.step,
                    i,
                    format!("Step {}, {}", i + 1, mesh.when(i)),
                );
            }
        });
}

/// What the form needs from the page.
pub struct Context<'a> {
    /// Notes on the problem as a mesh run sees it, and the reason it cannot run, if any.
    pub notes: &'a [String],
    pub refused: Option<&'a str>,
    /// The nuclides that carry the photons in the open result, which a banded mesh bands.
    pub photon_nuclides: usize,
    pub covariance: bool,
    /// The command running now, if one of this page's.
    pub running: Option<&'static str>,
    pub busy: bool,
}

/// Draw the page body and return what the page must do.
pub fn show(ui: &mut egui::Ui, state: &mut MeshState, context: &Context) -> MeshAction {
    let mut action = MeshAction::None;
    let warn = ui.visuals().warn_fg_color;
    let error = ui.visuals().error_fg_color;
    let idle = !context.busy;

    // 1. The flux
    let flux_done = state
        .flux
        .as_ref()
        .map(|f| format!("{} cells, {} groups", f.cells, f.groups));
    waste_forms::card(ui, |ui| {
        step_header(ui, 1, "The flux on the mesh", flux_done.as_deref(), true);
        ui.label("The flux tally of your transport run, per mesh cell and energy group.");
        ui.add_space(6.);
        if let Some(flux) = state.flux.as_ref().filter(|_| !state.change_flux) {
            ui.horizontal_wrapped(|ui| {
                ui.label(
                    RichText::new(flux.path.file_name().unwrap_or_default().to_string_lossy())
                        .monospace(),
                )
                .on_hover_text(flux.path.display().to_string());
                ui.label(format!(
                    "{} cells, {} energy groups, {}",
                    flux.cells, flux.groups, flux.units
                ));
                ui.weak(format!(
                    "SHA-256 {}…",
                    &flux.sha256[..12.min(flux.sha256.len())]
                ))
                .on_hover_text(&flux.sha256);
            });
            ui.add_space(4.);
            if ui
                .add_enabled(idle, egui::Button::new("Use another flux…"))
                .clicked()
            {
                state.change_flux = true;
            }
            return;
        }
        egui::Grid::new("mesh-flux")
            .num_columns(2)
            .spacing([12., 8.])
            .show(ui, |ui| {
                ui.label("From");
                ui.horizontal_wrapped(|ui| {
                    for format in FluxFormat::ALL {
                        if ui
                            .selectable_value(&mut state.format, format, format.label())
                            .changed()
                        {
                            state.source = None;
                        }
                    }
                });
                ui.end_row();
                ui.label("File");
                if file_field(ui, state.source.as_ref(), "Choose…") {
                    action = MeshAction::PickSource;
                }
                ui.end_row();
                if state.format.tally() {
                    ui.label("Tally");
                    ui.add(egui::DragValue::new(&mut state.tally).range(1..=u32::MAX as u64));
                    ui.end_row();
                    ui.label("Source rate");
                    ui.horizontal(|ui| {
                        waste_forms::amount(ui, &mut state.source_rate, 120.);
                        ui.weak("source particles per second; makes the per-particle tally a flux");
                    });
                    ui.end_row();
                }
                if state.format == FluxFormat::Fispact {
                    ui.label("Groups");
                    if file_field(ui, state.groups.as_ref(), "Choose…") {
                        action = MeshAction::PickGroups;
                    }
                    ui.end_row();
                }
            });
        ui.add_space(6.);
        if state.format != FluxFormat::Actinv {
            ui.horizontal(|ui| {
                let ready = idle && import_args(state).is_ok();
                if waste_forms::primary(ui, ready, "Import flux").clicked() {
                    action = MeshAction::Import;
                }
                if context.running == Some("import-flux") {
                    ui.spinner();
                    ui.weak("Importing…");
                } else if let Err(why) = import_args(state) {
                    ui.weak(why);
                }
            });
        }
        if state.flux.is_some() && ui.button("Keep the current flux").clicked() {
            state.change_flux = false;
        }
    });

    // 2. The mesh run
    let mesh_done = state.mesh.as_ref().map(|m| {
        if m.complete {
            format!("{} cells solved", m.cells)
        } else {
            format!("{} cells so far", m.cells)
        }
    });
    let can_run = state.flux.is_some();
    waste_forms::card(ui, |ui| {
        step_header(
            ui,
            2,
            "Activate every cell",
            mesh_done.as_deref(),
            can_run || state.mesh.is_some(),
        );
        ui.label("Solves the problem as it is set up now in every cell: the material, the irradiation and cooling, the library and what to compute. Each cell's flux takes the place of the spectrum; the schedule's flux multipliers scale it.");
        ui.add_space(6.);
        for note in context.notes {
            ui.colored_label(warn, note);
        }
        if let Some(refused) = context.refused {
            ui.colored_label(error, refused);
        }
        ui.horizontal_wrapped(|ui| {
            ui.add_enabled_ui(context.covariance, |ui| {
                ui.checkbox(&mut state.bands, "Uncertainty bands");
            });
            if context.covariance {
                ui.weak(if context.photon_nuclides > 0 {
                    format!(
                        "Bands the activity, the heat and the {} nuclides that carry the photons in the open result: the banded source and the facility check need them.",
                        context.photon_nuclides
                    )
                } else {
                    "Bands the activity and the heat. Run the problem once first so the bands also cover the nuclides that carry the photons.".to_owned()
                });
            } else {
                ui.weak("Needs the TENDL-2025 covariance.");
                if ui.link("Install it on Data").clicked() {
                    action = MeshAction::OpenData;
                }
            }
        });
        ui.horizontal(|ui| {
            ui.label("Threads");
            ui.add(egui::DragValue::new(&mut state.threads).range(1..=16));
            ui.weak("cells solved at once");
        });
        ui.add_space(6.);
        ui.horizontal_wrapped(|ui| {
            let cells = state.flux.as_ref().map_or(0, |f| f.cells);
            let ready = idle && can_run && context.refused.is_none();
            let label = match cells {
                0 => "Activate the cells".to_owned(),
                1 => "Activate 1 cell".to_owned(),
                n => format!("Activate {n} cells"),
            };
            if waste_forms::primary(ui, ready, &label).clicked() {
                action = MeshAction::Activate;
            }
            if state.unfinished.is_some()
                && ui
                    .add_enabled(idle, egui::Button::new("Resume"))
                    .on_hover_text("Solve only the cells the stopped run did not reach")
                    .clicked()
            {
                action = MeshAction::Resume;
            }
            if ui
                .add_enabled(idle, egui::Button::new("Open a mesh result…"))
                .clicked()
            {
                action = MeshAction::OpenMesh;
            }
            if context.running == Some("mesh") {
                ui.spinner();
                ui.weak("Solving the cells; cancel from the toolbar. A stopped run can resume.");
            } else if !can_run {
                ui.weak("Bring in the flux first.");
            }
        });
        if let Some(mesh) = &state.mesh {
            ui.add_space(8.);
            if !mesh.complete {
                ui.colored_label(
                    warn,
                    "This run stopped before its last cell; Resume solves the rest.",
                );
            }
            let pick = cell_table(ui, state, context);
            if let Some(id) = pick {
                action = MeshAction::OpenCell(id);
            }
        }
    });

    // 3. The source
    let mesh_ready = state.mesh.as_ref().is_some_and(|m| m.complete);
    let source_done = state
        .written
        .as_ref()
        .map(|w| format!("{} files written", w.files.len()));
    waste_forms::card(ui, |ui| {
        step_header(
            ui,
            3,
            "The shutdown-dose source",
            source_done.as_deref(),
            mesh_ready,
        );
        ui.label("The decay photons of every cell at one step, as the source of the photon transport run that gives the shutdown dose.");
        let Some(mesh) = state.mesh.as_ref().filter(|m| m.complete) else {
            ui.weak("Activate the cells first.");
            return;
        };
        if !mesh.photons {
            ui.colored_label(warn, "This mesh has no decay photons: ask for photons or dose under What to compute and activate again.");
            return;
        }
        let banded = !mesh.banded.is_empty();
        let joint = mesh.spec.is_some();
        let geometry = mesh.geometry;
        ui.add_space(6.);
        egui::Grid::new("mesh-source")
            .num_columns(2)
            .spacing([12., 8.])
            .show(ui, |ui| {
                ui.label("Step");
                // the picker borrows the state; the mesh is read again inside it
                let mesh = state.mesh.take();
                if let Some(mesh) = &mesh {
                    step_picker(ui, state, mesh);
                }
                state.mesh = mesh;
                ui.end_row();
                ui.label("Source");
                ui.vertical(|ui| {
                    ui.add_enabled_ui(banded, |ui| {
                        ui.radio_value(&mut state.kind, SourceKind::Banded, "Banded source, with each cell's uncertainty")
                    }).response.on_disabled_hover_text("Needs a mesh activated with Uncertainty bands.");
                    ui.add_enabled_ui(geometry, |ui| {
                        ui.radio_value(&mut state.kind, SourceKind::PerCell, "One OpenMC source per cell, without bands")
                    }).response.on_disabled_hover_text("Needs a flux mesh with cell bounds.");
                });
                ui.end_row();
                if state.kind == SourceKind::Banded {
                    ui.label("For");
                    ui.horizontal_wrapped(|ui| {
                        for code in Code::ALL {
                            ui.selectable_value(&mut state.code, code, code.label());
                        }
                    });
                    ui.end_row();
                    ui.label("");
                    ui.add_enabled_ui(joint, |ui| {
                        ui.checkbox(&mut state.joint, "Correlate the bands between cells")
                            .on_hover_text("Adds the cell-to-cell correlated sigma; takes about as long as activating the mesh.")
                            .on_disabled_hover_text("Needs a mesh this page activated: the correlation reads its spec.");
                    });
                    ui.end_row();
                }
            });
        if !banded && state.kind == SourceKind::Banded && geometry {
            state.kind = SourceKind::PerCell;
        }
        let possible = match state.kind {
            SourceKind::Banded => banded,
            SourceKind::PerCell => geometry,
        };
        ui.add_space(6.);
        ui.horizontal_wrapped(|ui| {
            if waste_forms::primary(ui, idle && possible, "Write the source").clicked() {
                action = MeshAction::WriteSource;
            }
            if state.written.is_some()
                && ui
                    .add_enabled(idle, egui::Button::new("Save to folder…"))
                    .clicked()
            {
                action = MeshAction::SaveSource;
            }
            if let Some(running) = context.running.filter(|r| r.starts_with("export")) {
                ui.spinner();
                ui.weak(if running == "export-r2s-joint" {
                    "Correlating the bands; this takes about as long as the mesh run."
                } else {
                    "Writing the source…"
                });
            }
        });
        if let Some(written) = &state.written {
            ui.add_space(6.);
            ui.horizontal_wrapped(|ui| {
                for file in &written.files {
                    let name = file.file_name().unwrap_or_default().to_string_lossy();
                    ui.label(RichText::new(name).monospace())
                        .on_hover_text(file.display().to_string());
                }
            });
            if let Some(banded) = &written.banded {
                banded_table(ui, banded);
            }
        }
    });

    // 4. The facility check
    let twin_done = state.twin.as_ref().map(|t| {
        format!(
            "{} cleared, {} restricted",
            t["facility"]["cleared"].as_u64().unwrap_or(0),
            t["facility"]["restricted"].as_u64().unwrap_or(0)
        )
    });
    let banded = state
        .mesh
        .as_ref()
        .is_some_and(|m| m.complete && !m.banded.is_empty());
    waste_forms::card(ui, |ui| {
        step_header(ui, 4, "Facility check", twin_done.as_deref(), banded);
        ui.label("Which cells clear their limits once the data uncertainty is counted: a cell clears only when its whole band does.");
        let Some(mesh) = state.mesh.as_ref().filter(|_| banded) else {
            ui.weak("Activate the cells with Uncertainty bands first.");
            return;
        };
        let responses = mesh.banded.clone();
        let ids = mesh.ids().join(", ");
        if state.limits.is_empty() {
            state.limits.push(Limit {
                name: "activity".into(),
                response: if responses.iter().any(|r| r == "activity.total") {
                    "activity.total".into()
                } else {
                    responses[0].clone()
                },
                value: 0.,
                at_most: true,
            });
        }
        ui.add_space(6.);
        ui.label(RichText::new("Limits").strong());
        let mut remove = None;
        egui::Grid::new("mesh-limits")
            .num_columns(5)
            .spacing([10., 6.])
            .show(ui, |ui| {
                for (i, limit) in state.limits.iter_mut().enumerate() {
                    ui.add_sized(
                        [150., ui.spacing().interact_size.y],
                        egui::TextEdit::singleline(&mut limit.name).hint_text("name"),
                    );
                    egui::ComboBox::from_id_salt(("mesh-limit-response", i))
                        .width(200.)
                        .selected_text(response_words(&limit.response))
                        .show_ui(ui, |ui| {
                            for response in &responses {
                                ui.selectable_value(
                                    &mut limit.response,
                                    response.clone(),
                                    response_words(response),
                                );
                            }
                        });
                    ui.horizontal(|ui| {
                        ui.selectable_value(&mut limit.at_most, true, "at most");
                        ui.selectable_value(&mut limit.at_most, false, "at least");
                    });
                    ui.horizontal(|ui| {
                        waste_forms::amount(ui, &mut limit.value, 110.);
                        ui.weak(unit(&limit.response));
                    });
                    if ui
                        .small_button("×")
                        .on_hover_text("Remove this limit")
                        .clicked()
                    {
                        remove = Some(i);
                    }
                    ui.end_row();
                }
            });
        if let Some(i) = remove {
            state.limits.remove(i);
        }
        if ui.button("Add a limit").clicked() {
            state.limits.push(Limit {
                name: String::new(),
                response: "heat.total".into(),
                value: 0.,
                at_most: true,
            });
        }
        ui.add_space(4.);
        ui.horizontal(|ui| {
            ui.label("When");
            let words = |step: Option<usize>| match step {
                None => "At every step".to_owned(),
                Some(i) => format!("Step {}, {}", i + 1, mesh.when(i)),
            };
            egui::ComboBox::from_id_salt("mesh-twin-step")
                .width(260.)
                .selected_text(words(state.twin_step))
                .show_ui(ui, |ui| {
                    ui.selectable_value(&mut state.twin_step, None, words(None));
                    for i in 0..mesh.steps() {
                        ui.selectable_value(&mut state.twin_step, Some(i), words(Some(i)));
                    }
                });
            ui.weak("a clearance limit usually applies after a set cooling time");
        });
        ui.add_space(8.);
        ui.label(RichText::new("Components").strong());
        ui.weak(
            "Optional: name groups of cells, such as a plate or a flange, to get one verdict each.",
        );
        let mut remove = None;
        egui::Grid::new("mesh-components")
            .num_columns(3)
            .spacing([10., 6.])
            .show(ui, |ui| {
                for (i, component) in state.components.iter_mut().enumerate() {
                    ui.add_sized(
                        [150., ui.spacing().interact_size.y],
                        egui::TextEdit::singleline(&mut component.name).hint_text("name"),
                    );
                    ui.add_sized(
                        [380., ui.spacing().interact_size.y],
                        egui::TextEdit::singleline(&mut component.cells).hint_text(&ids),
                    );
                    if ui
                        .small_button("×")
                        .on_hover_text("Remove this component")
                        .clicked()
                    {
                        remove = Some(i);
                    }
                    ui.end_row();
                }
            });
        if let Some(i) = remove {
            state.components.remove(i);
        }
        if ui.button("Add a component").clicked() {
            state.components.push(Component::default());
        }
        ui.add_space(8.);
        let spec = state.mesh.as_ref().map(|m| twin_spec(state, m));
        ui.horizontal_wrapped(|ui| {
            let ready = idle && spec.as_ref().is_some_and(Result::is_ok);
            if waste_forms::primary(ui, ready, "Check the facility").clicked() {
                action = MeshAction::Check;
            }
            if let Some(Err(why)) = &spec {
                ui.weak(why);
            }
        });
        if let Some(twin) = &state.twin {
            ui.add_space(8.);
            twin_view(ui, twin, state.mesh.as_ref());
        }
    });
    action
}

/// The cells at the chosen step, highest activity first. Returns the cell whose Open was clicked.
fn cell_table(ui: &mut egui::Ui, state: &mut MeshState, context: &Context) -> Option<String> {
    let mesh = state.mesh.take()?;
    let mut picked = None;
    ui.horizontal(|ui| {
        ui.label(RichText::new("Cells at").strong());
        step_picker(ui, state, &mesh);
        if mesh.cells as usize > mesh.rows.len() {
            ui.weak(format!(
                "the first {} of {} cells",
                mesh.rows.len(),
                mesh.cells
            ));
        }
    });
    let step = state.step;
    let mut rows: Vec<&CellRow> = mesh.rows.iter().collect();
    let at = |row: &CellRow| row.steps.get(step).copied().unwrap_or((0., 0., None));
    rows.sort_by(|a, b| at(b).0.total_cmp(&at(a).0));
    egui::ScrollArea::vertical()
        .id_salt("mesh-cells")
        .max_height(260.)
        .show(ui, |ui| {
            let dose = rows.iter().any(|r| at(r).2.is_some());
            egui::Grid::new("mesh-cell-rows")
                .num_columns(6)
                .striped(true)
                .spacing([24., 4.])
                .show(ui, |ui| {
                    for title in [
                        "Cell",
                        "Volume, cm³",
                        "Activity, Bq/g",
                        "Heat, W/g",
                        "Contact dose, Gy/h",
                    ] {
                        if dose || !title.starts_with("Contact") {
                            ui.label(RichText::new(title).strong());
                        }
                    }
                    ui.label("");
                    ui.end_row();
                    for row in rows {
                        let (activity, heat, contact) = at(row);
                        ui.label(RichText::new(&row.id).monospace());
                        ui.label(row.volume_cm3.map_or("".into(), plain));
                        ui.label(model::sci(activity));
                        ui.label(model::sci(heat));
                        if dose {
                            ui.label(contact.map_or("".into(), model::sci));
                        }
                        if ui
                            .add_enabled(!context.busy, egui::Button::new("Open").small())
                            .on_hover_text("Read this cell on the result pages")
                            .clicked()
                        {
                            picked = Some(row.id.clone());
                        }
                        ui.end_row();
                    }
                });
        });
    state.mesh = Some(mesh);
    picked
}

/// The banded source per cell, and its totals.
fn banded_table(ui: &mut egui::Ui, banded: &Value) {
    let footer = &banded["footer"];
    let total = footer["total_photons_s"].as_f64().unwrap_or(0.);
    let mut sentence = format!(
        "{} photons/s in all; sigma {} if the cells are independent, {} if fully correlated",
        model::sci(total),
        model::sci(footer["sigma_total_independent"].as_f64().unwrap_or(0.)),
        model::sci(footer["sigma_total_conservative"].as_f64().unwrap_or(0.)),
    );
    if let Some(correlated) = footer["sigma_total_correlated"].as_f64() {
        sentence.push_str(&format!(
            ", {} with their correlation",
            model::sci(correlated)
        ));
    }
    ui.add_space(4.);
    ui.label(sentence + ".");
    egui::ScrollArea::vertical()
        .id_salt("mesh-banded")
        .max_height(220.)
        .show(ui, |ui| {
            egui::Grid::new("mesh-banded-rows")
                .num_columns(5)
                .striped(true)
                .spacing([24., 4.])
                .show(ui, |ui| {
                    for title in [
                        "Cell",
                        "Photons/s",
                        "Sigma, independent",
                        "Sigma, fully correlated",
                        "Without a band",
                    ] {
                        ui.label(RichText::new(title).strong());
                    }
                    ui.end_row();
                    for cell in banded["cells"].as_array().into_iter().flatten() {
                        ui.label(RichText::new(cell["id"].as_str().unwrap_or("?")).monospace());
                        ui.label(model::sci(cell["photons_s"].as_f64().unwrap_or(0.)));
                        ui.label(model::sci(cell["independent"].as_f64().unwrap_or(0.)));
                        ui.label(model::sci(cell["conservative"].as_f64().unwrap_or(0.)));
                        ui.label(
                            cell["unbanded"]
                                .as_f64()
                                .map_or("".into(), |s| format!("{:.1}%", 100. * s)),
                        )
                        .on_hover_text(
                            "Share of the cell's photons from nuclides the mesh did not band",
                        );
                        ui.end_row();
                    }
                });
        });
}

/// The facility verdict: the counts, what binds each limit, the components and the cells.
fn twin_view(ui: &mut egui::Ui, twin: &Value, mesh: Option<&MeshRun>) {
    let facility = &twin["facility"];
    let cleared = facility["cleared"].as_u64().unwrap_or(0);
    let restricted = facility["restricted"].as_u64().unwrap_or(0);
    let all = restricted == 0;
    let colour = if all {
        crate::visuals::accent(ui)
    } else {
        ui.visuals().warn_fg_color
    };
    ui.label(
        RichText::new(if all {
            format!("Every cell clears: {cleared} of {}.", cleared + restricted)
        } else {
            let verb = if restricted == 1 { "does" } else { "do" };
            format!(
                "{restricted} of {} cells {verb} not clear.",
                cleared + restricted
            )
        })
        .size(17.)
        .color(colour),
    );
    ui.add_space(4.);
    if let Some(binding) = facility["binding_cells"].as_object() {
        ui.label(RichText::new("What binds each limit").strong());
        egui::Grid::new("mesh-binding")
            .num_columns(5)
            .striped(true)
            .spacing([24., 4.])
            .show(ui, |ui| {
                for title in ["Limit", "When", "Cell", "Band", "Margin"] {
                    ui.label(RichText::new(title).strong());
                }
                ui.end_row();
                for (key, entry) in binding {
                    let (name, t) = key.rsplit_once('@').unwrap_or((key.as_str(), ""));
                    ui.label(name);
                    ui.label(t.parse::<f64>().map_or(t.to_owned(), |t| when_at(mesh, t)));
                    ui.label(RichText::new(entry["cell"].as_str().unwrap_or("?")).monospace());
                    let band = |i: usize| entry["band"][i].as_f64().unwrap_or(0.);
                    ui.label(format!(
                        "{} to {}",
                        model::sci(band(0)),
                        model::sci(band(1))
                    ));
                    let margin = entry["margin"].as_f64().unwrap_or(0.);
                    ui.label(margin_words(margin));
                    ui.end_row();
                }
            });
    }
    if let Some(components) = twin["components"].as_object().filter(|c| !c.is_empty()) {
        ui.add_space(6.);
        ui.label(RichText::new("Components").strong());
        for (name, component) in components {
            let verdict = component["verdict"].as_str().unwrap_or("?");
            let missing = component["missing_cells"].as_array().map_or(0, Vec::len);
            let mut text = format!("{name}: {verdict}");
            if missing > 0 {
                text.push_str(&format!(", {missing} cells not in the mesh"));
            }
            ui.label(text);
        }
    }
    let restricted: Vec<&Value> = twin["per_cell"]
        .as_array()
        .into_iter()
        .flatten()
        .filter(|c| c["verdict"] != "cleared")
        .collect();
    if !restricted.is_empty() {
        ui.add_space(6.);
        ui.label(RichText::new("Cells that do not clear").strong());
        for cell in restricted.iter().take(50) {
            ui.label(format!(
                "{}: at worst {}",
                cell["cell"].as_str().unwrap_or("?"),
                margin_words(cell["worst_margin"].as_f64().unwrap_or(0.))
            ));
        }
        if restricted.len() > 50 {
            ui.weak(format!(
                "and {} more; the file lists them all",
                restricted.len() - 50
            ));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn flux() -> Flux {
        Flux {
            path: "/data/flux.ndjson".into(),
            sha256: "a".repeat(64),
            cells: 2,
            groups: 3,
            units: "n cm^-2 s^-1".into(),
        }
    }

    fn run_spec() -> Value {
        json!({
            "spec": "actinv-spec-1", "title": "Fe plate", "projectile": "neutron",
            "library": {"path": "/lib/tendl.json"}, "decay": {"primary": "/lib/decay.json"},
            "material": {"mass_g": 1.0, "composition": {"FE": 100.0}},
            "spectrum": {"structure": "fispact-709", "flux_per_group": [1.0]},
            "schedule": [{"dt": "1 h", "flux": 1.0}, {"dt": "1 d", "flux": 0.0}],
            "options": {"mode": "auto"}, "pathways": {"top": 5}, "overrides": []
        })
    }

    #[test]
    fn the_mesh_spec_takes_the_problem_and_puts_the_flux_in_place_of_the_spectrum() {
        let spec = mesh_spec(&run_spec(), &flux(), None, 4).unwrap();
        assert_eq!(spec["spec"], "actinv-mesh-spec-1");
        assert_eq!(spec["flux"]["path"], "/data/flux.ndjson");
        assert_eq!(spec["flux"]["sha256"], "a".repeat(64));
        assert!(spec["spectrum"].is_null() && spec["pathways"].is_null());
        assert!(spec["overrides"].is_null() && spec["uncertainty"].is_null());
        assert_eq!(spec["schedule"], run_spec()["schedule"]);
        assert_eq!(spec["threads"], 4);
        assert_eq!(spec["resume"], true);
        // the core accepts it as it stands
        actinv_core::mesh::MeshSpec::from_json(&spec.to_string()).unwrap();
    }

    #[test]
    fn bands_cover_the_totals_and_the_photon_nuclides_and_keep_the_problems_own() {
        let nuclides = vec!["Mn56".to_owned(), "Co60".to_owned()];
        let spec = mesh_spec(&run_spec(), &flux(), Some(&nuclides), 1).unwrap();
        assert_eq!(spec["uncertainty"]["covariance"]["path"], COVARIANCE);
        assert_eq!(
            spec["uncertainty"]["responses"],
            json!([
                "activity.total",
                "heat.total",
                "activity:Mn56",
                "activity:Co60"
            ])
        );
        let mut own = run_spec();
        own["uncertainty"] = json!({"covariance": {"path": "/c.json", "sha256": "b"},
                                    "responses": ["heat.total"]});
        let spec = mesh_spec(&own, &flux(), Some(&nuclides), 1).unwrap();
        assert_eq!(spec["uncertainty"]["covariance"]["path"], "/c.json");
        assert_eq!(
            spec["uncertainty"]["responses"],
            json!([
                "heat.total",
                "activity.total",
                "activity:Mn56",
                "activity:Co60"
            ])
        );
    }

    #[test]
    fn notes_say_what_a_mesh_run_leaves_out_and_refuses_a_step_spectrum() {
        let (notes, refused) = mesh_notes(&run_spec());
        assert_eq!(notes.len(), 1);
        assert!(notes[0].contains("pathways"));
        assert!(refused.is_none());
        let mut spec = run_spec();
        spec["options"]["outputs"] = json!(["activity"]);
        spec["schedule"][1]["spectrum"] = json!({"structure": "x"});
        let (notes, refused) = mesh_notes(&spec);
        assert!(notes.iter().any(|n| n.contains("photons")));
        assert!(refused.unwrap().contains("Step 2"));
        assert!(mesh_spec(&spec, &flux(), None, 1).is_err());
    }

    #[test]
    fn photon_nuclides_rank_by_their_largest_share() {
        let source = |parts: &[(&str, f64)]| {
            json!({"total_photons_s_g": parts.iter().map(|p| p.1).sum::<f64>(),
                   "by_nuclide": parts.iter().map(|(n, s)| json!({"nuclide": n,
                       "groups": [{"photons_s_g": s}]})).collect::<Vec<_>>()})
        };
        let result = json!({"steps": [
            {"photon_source": source(&[("Mn56", 90.), ("Co60", 10.), ("Fe59", 1e-6)])},
            {"photon_source": source(&[("Co60", 99.), ("Mn54", 1.)])},
            {}
        ]});
        assert_eq!(photon_nuclides(&result), ["Co60", "Mn56", "Mn54"]);
    }

    #[test]
    fn import_args_name_the_tally_and_the_source_rate() {
        let mut state = MeshState {
            source: Some("/runs/statepoint.100.h5".into()),
            ..Default::default()
        };
        assert!(import_args(&state).unwrap_err().contains("source rate"));
        state.source_rate = 1e18;
        assert_eq!(
            import_args(&state).unwrap(),
            json!({"format": "openmc", "source": "/runs/statepoint.100.h5", "tally": 1,
                   "source_rate": 1e18})
        );
        state.format = FluxFormat::Fispact;
        assert!(import_args(&state).unwrap_err().contains("group structure"));
        state.groups = Some("/runs/groups.json".into());
        assert_eq!(import_args(&state).unwrap()["groups"], "/runs/groups.json");
        state.format = FluxFormat::Actinv;
        assert!(import_args(&state).is_err());
    }

    fn write(name: &str, lines: &[Value]) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("actinv-mesh-view-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join(name);
        let text: Vec<String> = lines.iter().map(Value::to_string).collect();
        std::fs::write(&path, text.join("\n") + "\n").unwrap();
        path
    }

    fn cell(id: &str, activity: f64) -> Value {
        json!({"record": "cell", "id": id, "ordinal": 0, "bounds_cm": [[0, 1], [0, 1], [0, 1]],
               "volume_cm3": 1.0, "result": {"spec_title": "Fe", "steps": [
                   {"step": 1, "t_s": 3600.0, "flux": 1.0,
                    "inventory": [], "activity_Bq_per_g": {"Mn56": activity}, "heat_W_per_g": {"total": 1e-9},
                    "photon_source": {"contact_gamma_air_dose_proxy_Gy_h": 2e-3},
                    "uncertainty": {"responses": {"activity.total": {}, "activity:Mn56": {}}}},
                   {"step": 2, "t_s": 90000.0, "flux": 0.0,
                    "inventory": [], "activity_Bq_per_g": {"Mn56": activity / 1e3}, "heat_W_per_g": {"total": 0.0}}]}})
    }

    #[test]
    fn a_mesh_result_is_read_with_its_cells_bands_and_whether_it_finished() {
        let header = json!({"record": "header", "schema": "actinv-mesh-result-1", "cell_count": 2});
        let path = write(
            "mesh.ndjson",
            &[
                header.clone(),
                cell("a", 10.),
                cell("b", 30.),
                json!({"record": "footer", "cell_count": 2}),
            ],
        );
        let mesh = read_mesh(&path, None).unwrap();
        assert_eq!(mesh.cells, 2);
        assert!(mesh.complete && mesh.photons && mesh.geometry);
        assert_eq!(mesh.banded, ["activity.total", "activity:Mn56"]);
        assert_eq!(mesh.rows[1].steps[0], (30., 1e-9, Some(2e-3)));
        assert_eq!(mesh.steps(), 2);
        assert_eq!(mesh.shutdown_s(), Some(3600.));
        assert_eq!(mesh.when(1), "1 d after shutdown");
        let opened = cell_result(&path, "b").unwrap();
        assert_eq!(opened.value["spec_title"], "Fe, cell b");
        assert!(cell_result(&path, "c").is_err());

        // a run stopped mid-write: no footer, a torn last line
        let path = write("stopped.ndjson", &[header, cell("a", 10.)]);
        std::fs::write(
            &path,
            std::fs::read_to_string(&path).unwrap() + "{\"record\":\"ce",
        )
        .unwrap();
        let mesh = read_mesh(&path, None).unwrap();
        assert_eq!(mesh.cells, 1);
        assert!(!mesh.complete);
    }

    #[test]
    fn the_twin_spec_needs_banded_limits_and_lists_component_cells() {
        let header = json!({"record": "header", "schema": "actinv-mesh-result-1", "cell_count": 1});
        let path = write(
            "twin-mesh.ndjson",
            &[header, cell("a", 10.), json!({"record": "footer"})],
        );
        let mesh = read_mesh(&path, None).unwrap();
        let mut state = MeshState::default();
        assert!(twin_spec(&state, &mesh).is_err());
        state.limits.push(Limit {
            name: "".into(),
            response: "heat.total".into(),
            value: 2.,
            at_most: true,
        });
        assert!(twin_spec(&state, &mesh).unwrap_err().contains("no band"));
        state.limits[0].response = "activity.total".into();
        state.components.push(Component {
            name: "plate".into(),
            cells: "a, b".into(),
        });
        let spec = twin_spec(&state, &mesh).unwrap();
        assert_eq!(spec["spec"], "actinv-twin-1");
        assert_eq!(
            spec["limits"],
            json!([{"name": "limit-1", "response": "activity.total", "limit": 2.0, "sense": "le"}])
        );
        assert_eq!(spec["components"]["plate"], json!(["a", "b"]));
        assert!(spec["times_s"].is_null());
        state.twin_step = Some(1);
        assert_eq!(
            twin_spec(&state, &mesh).unwrap()["times_s"],
            json!([90000.0])
        );
        state.twin_step = Some(5);
        assert!(twin_spec(&state, &mesh).is_err());
        assert_eq!(when_at(Some(&mesh), 90000.), "1 d after shutdown");
        assert_eq!(when_at(None, 90000.), "t = 1.04 d");
    }

    #[test]
    fn sizes_read_plainly_when_they_can() {
        assert_eq!(plain(1.), "1");
        assert_eq!(plain(12.5), "12.5");
        assert_eq!(plain(1e6), "1.00e6");
    }

    #[test]
    fn margins_read_as_how_far_under_or_over_the_limit() {
        assert_eq!(margin_words(0.678), "68% under the limit");
        assert_eq!(margin_words(-0.529), "53% over the limit");
        assert_eq!(margin_words(-1124.2), "1125 times the limit");
    }

    #[test]
    fn a_flux_file_is_pinned_by_its_hash() {
        let header = json!({"record": "header", "schema": "actinv-flux-1", "cell_count": 2,
                            "energy_boundaries_eV": [1.0, 2.0, 3.0], "flux_units": "n cm^-2 s^-1"});
        let path = write("flux.ndjson", &[header]);
        let flux = read_flux(&path).unwrap();
        assert_eq!((flux.cells, flux.groups), (2, 2));
        let expected: String = Sha256::digest(std::fs::read(&path).unwrap())
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect();
        assert_eq!(flux.sha256, expected);
        let path = write("not-flux.ndjson", &[json!({"schema": "other"})]);
        assert!(read_flux(&path).is_err());
    }

    #[test]
    fn the_banded_source_is_read_with_its_totals() {
        let path = write(
            "r2s.ndjson",
            &[
                json!({"record": "header", "step": 2}),
                json!({"record": "cell", "id": "a", "photons_s": 10.0,
                       "sigma_photons_s_independent": 1.0, "sigma_photons_s_conservative": 2.0,
                       "coverage": {"unbanded_photon_share": 0.25}}),
                json!({"record": "footer", "total_photons_s": 10.0}),
            ],
        );
        let banded = read_banded(&path).unwrap();
        assert_eq!(banded["cells"][0]["unbanded"], 0.25);
        assert_eq!(banded["footer"]["total_photons_s"], 10.0);
    }
}
