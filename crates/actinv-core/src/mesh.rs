#![allow(non_snake_case)] // JSON wire names carry their physical units.
//! Deterministic, bounded-memory independent-cell activation runner.

use crate::flux::{
    atomic_output, rebin_equal_lethargy, sha256_file, FluxCell, FluxGeometry, FluxSource,
    FluxStream, RebinResult,
};
use crate::run::{PreparedRun, RunResult};
use crate::spec::{
    DamageOptions, DecayRef, FissionYieldOptions, HashedFileRef, LibraryRef, Material, Options,
    PhotonOptions, Projectile, RadiologicalOptions, SelfShieldingOptions, Spec, Spectrum, Step,
    UncertaintyOptions,
};
use rayon::prelude::*;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::fs::File;
use std::io::{BufWriter, Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};

const MESH_SPEC_SCHEMA: &str = "actinv-mesh-spec-1";
const MESH_RESULT_SCHEMA: &str = "actinv-mesh-result-1";
const MAX_CHUNK_CELLS: usize = 65_536;
const MAX_THREADS: usize = 256;
const GROUPING_CACHE_CAP: usize = 256;
/// The memo is bounded by payload bytes as well as entry count so its
/// footprint stays bounded independently of cell count and record size.
const GROUPING_CACHE_BYTES: usize = 512 << 20;

fn default_chunk_cells() -> usize {
    64
}

fn default_threads() -> usize {
    1
}

fn default_true() -> bool {
    true
}

/// Top-level `RunResult` keys a `cell_result_fields` selection may keep.
const RESULT_FIELDS: &[&str] = &[
    "spec_title",
    "entry_point",
    "projectile",
    "mode",
    "pruned_states",
    "total_states",
    "steps",
    "pathways",
    "pathway_closure",
    "ledger",
    "certificate",
    "ms",
];

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MeshSpec {
    pub spec: String,
    #[serde(default)]
    pub title: String,
    #[serde(default)]
    pub projectile: Projectile,
    pub library: LibraryRef,
    #[serde(default)]
    pub decay: DecayRef,
    pub material: Material,
    pub flux: HashedFileRef,
    pub schedule: Vec<Step>,
    #[serde(default)]
    pub options: Options,
    #[serde(default)]
    pub photon: PhotonOptions,
    #[serde(default)]
    pub fission_yields: FissionYieldOptions,
    #[serde(default)]
    pub uncertainty: Option<UncertaintyOptions>,
    #[serde(default)]
    pub radiological: Option<RadiologicalOptions>,
    #[serde(default)]
    pub damage: Option<DamageOptions>,
    #[serde(default)]
    pub self_shielding: Option<SelfShieldingOptions>,
    #[serde(default = "default_chunk_cells")]
    pub chunk_cells: usize,
    #[serde(default = "default_threads")]
    pub threads: usize,
    /// Group cells whose rebinned activation-group flux is byte-identical:
    /// the first solve's serialized result is memoized (capacity
    /// `GROUPING_CACHE_CAP` distinct signatures) and re-emitted verbatim.
    #[serde(default = "default_true")]
    pub group_workloads: bool,
    /// Optional keep-only selection of `RunResult` top-level keys per cell.
    #[serde(default)]
    pub cell_result_fields: Option<Vec<String>>,
    /// Optional peak-RSS guard in bytes; checked after each completed chunk.
    #[serde(default)]
    pub memory_limit_bytes: Option<u64>,
    /// Resume mode: the output file is its own checkpoint. When set, output
    /// is written directly (not via a temporary rename); an existing output
    /// is validated against this spec's fingerprint and completed cells are
    /// not re-solved.
    #[serde(default)]
    pub resume: bool,
}

impl MeshSpec {
    pub fn from_json(text: &str) -> Result<Self, String> {
        let spec: Self =
            serde_json::from_str(text).map_err(|error| format!("mesh spec: {error}"))?;
        spec.validate()?;
        Ok(spec)
    }

    pub fn validate(&self) -> Result<(), String> {
        if self.spec != MESH_SPEC_SCHEMA {
            return Err(format!("unsupported mesh spec version '{}'", self.spec));
        }
        if self.flux.path.is_empty()
            || self.flux.sha256.len() != 64
            || !self
                .flux
                .sha256
                .bytes()
                .all(|byte| byte.is_ascii_hexdigit())
        {
            return Err("flux requires a path and a 64-hex-digit sha256".into());
        }
        if !(1..=MAX_CHUNK_CELLS).contains(&self.chunk_cells) {
            return Err(format!(
                "chunk_cells must be between 1 and {MAX_CHUNK_CELLS}"
            ));
        }
        if !(1..=MAX_THREADS).contains(&self.threads) {
            return Err(format!("threads must be between 1 and {MAX_THREADS}"));
        }
        if let Some(fields) = &self.cell_result_fields {
            for field in fields {
                if !RESULT_FIELDS.contains(&field.as_str()) {
                    return Err(format!(
                        "cell_result_fields entry '{field}' is not a run-result field"
                    ));
                }
            }
        }
        if self.memory_limit_bytes.is_some_and(|limit| limit == 0) {
            return Err("memory_limit_bytes must be positive".into());
        }

        // Reuse the ordinary-spec validator for every shared field. The placeholder spectrum
        // is valid by construction and is replaced with each rebinned cell before execution.
        self.cell_spec(vec![1.0, 2.0], vec![0.0]).validate()
    }

    /// Canonical SHA-256 of the spec content that determines output bytes.
    /// Scheduling (`threads`, `chunk_cells`), the resume flag and the memory
    /// guard do not change emitted records, so they are excluded: a resumed
    /// run may legitimately carry different values for them.
    pub fn fingerprint_sha256(&self) -> Result<String, String> {
        let mut value = serde_json::to_value(self).map_err(|error| error.to_string())?;
        let object = value
            .as_object_mut()
            .ok_or("mesh spec did not serialize as an object")?;
        for key in ["resume", "threads", "chunk_cells", "memory_limit_bytes"] {
            object.remove(key);
        }
        let canonical = serde_json::to_string(&value).map_err(|error| error.to_string())?;
        let digest = Sha256::digest(canonical.as_bytes());
        Ok(digest.iter().map(|byte| format!("{byte:02x}")).collect())
    }

    fn cell_spec(&self, boundaries_eV: Vec<f64>, flux_per_group: Vec<f64>) -> Spec {
        Spec {
            spec: "actinv-spec-1".into(),
            title: self.title.clone(),
            projectile: self.projectile,
            library: self.library.clone(),
            decay: self.decay.clone(),
            material: self.material.clone(),
            spectrum: Spectrum {
                structure: "custom".into(),
                flux_per_group,
                total: None,
                boundaries_eV: Some(boundaries_eV),
                descending: false,
            },
            schedule: self.schedule.clone(),
            options: self.options.clone(),
            photon: self.photon.clone(),
            fission_yields: self.fission_yields.clone(),
            uncertainty: self.uncertainty.clone(),
            radiological: self.radiological.clone(),
            damage: self.damage.clone(),
            self_shielding: self.self_shielding.clone(),
        }
    }
}

#[derive(Debug, Serialize)]
struct MeshHeader {
    record: &'static str,
    schema: &'static str,
    spec_title: String,
    cell_count: u64,
    /// Canonical hash of the mesh spec's output-determining content; binds a
    /// resumed run to the same problem.
    spec_fingerprint_sha256: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    projectile: Option<String>,
    flux_units: &'static str,
    source_energy_boundaries_eV: Vec<f64>,
    activation_energy_boundaries_eV: Vec<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    geometry: Option<FluxGeometry>,
    certificate: MeshCertificate,
}

#[derive(Debug, Serialize)]
struct MeshCertificate {
    solver: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    projectile: Option<String>,
    canonical_flux: CanonicalFluxCertificate,
    upstream_source: FluxSource,
}

#[derive(Debug, Serialize)]
struct CanonicalFluxCertificate {
    path: String,
    sha256_declared: String,
    sha256_computed: String,
}

#[derive(Debug, Serialize)]
pub struct RebinLedger {
    method: &'static str,
    source_total: f64,
    destination_total: f64,
    underflow: f64,
    overflow: f64,
    relative_closure: f64,
}

#[derive(Debug, Serialize)]
struct MeshCellRecord {
    record: &'static str,
    ordinal: u64,
    id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    index: Option<[usize; 3]>,
    #[serde(skip_serializing_if = "Option::is_none")]
    bounds_cm: Option<[[f64; 2]; 3]>,
    #[serde(skip_serializing_if = "Option::is_none")]
    volume_cm3: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    source_relative_error: Option<Vec<f64>>,
    rebin: RebinLedger,
    result: serde_json::Value,
}

#[derive(Debug, Serialize)]
struct MeshFooter {
    record: &'static str,
    cell_count: u64,
    source_flux_sum_over_cells: f64,
    destination_flux_sum_over_cells: f64,
    underflow_sum_over_cells: f64,
    overflow_sum_over_cells: f64,
    max_rebin_relative_closure: f64,
    min_pruned_states: usize,
    max_pruned_states: usize,
    cells_served_from_reuse: u64,
    wall_time_s: f64,
    cells_per_s: f64,
}

#[derive(Debug, Serialize)]
pub struct MeshSummary {
    pub output: String,
    pub canonical_flux_sha256: String,
    pub cells: u64,
    pub source_groups: usize,
    pub activation_groups: usize,
    pub output_bytes: u64,
    pub wall_time_s: f64,
    pub cells_per_s: f64,
}

#[derive(Clone, Copy, Default)]
struct Compensated {
    sum: f64,
    correction: f64,
}

impl Compensated {
    fn add(&mut self, value: f64) {
        let next = self.sum + value;
        if self.sum.abs() >= value.abs() {
            self.correction += (self.sum - next) + value;
        } else {
            self.correction += (value - next) + self.sum;
        }
        self.sum = next;
    }

    fn total(self) -> f64 {
        self.sum + self.correction
    }
}

#[derive(Default)]
struct MeshTotals {
    cells: u64,
    source: Compensated,
    destination: Compensated,
    underflow: Compensated,
    overflow: Compensated,
    max_closure: f64,
    min_pruned: Option<usize>,
    max_pruned: usize,
}

impl MeshTotals {
    fn add(&mut self, rebin: &RebinLedger, pruned: usize) {
        self.cells += 1;
        self.source.add(rebin.source_total);
        self.destination.add(rebin.destination_total);
        self.underflow.add(rebin.underflow);
        self.overflow.add(rebin.overflow);
        self.max_closure = self.max_closure.max(rebin.relative_closure);
        self.min_pruned = Some(self.min_pruned.map_or(pruned, |value| value.min(pruned)));
        self.max_pruned = self.max_pruned.max(pruned);
    }
}

impl RebinLedger {
    fn from_result(rebinned: &RebinResult) -> Self {
        RebinLedger {
            method: if rebinned.exact_grid {
                "copy"
            } else {
                "equal-flux-per-unit-lethargy"
            },
            source_total: rebinned.source_total,
            destination_total: rebinned.destination_total,
            underflow: rebinned.underflow,
            overflow: rebinned.overflow,
            relative_closure: rebinned.relative_closure,
        }
    }
}

fn relative_difference(left: f64, right: f64) -> f64 {
    (left - right).abs() / left.abs().max(right.abs()).max(f64::MIN_POSITIVE)
}

fn write_record(output: &mut BufWriter<File>, record: &impl Serialize) -> Result<(), String> {
    serde_json::to_writer(&mut *output, record).map_err(|error| error.to_string())?;
    output.write_all(b"\n").map_err(|error| error.to_string())
}

fn result_without_timing(result: RunResult) -> Result<serde_json::Value, String> {
    let mut value = serde_json::to_value(result).map_err(|error| error.to_string())?;
    value
        .as_object_mut()
        .ok_or("ordinary solver result did not serialize as an object")?
        .remove("ms");
    Ok(value)
}

fn resolved_path(path: &Path) -> Result<PathBuf, String> {
    if path.exists() {
        return std::fs::canonicalize(path)
            .map_err(|error| format!("cannot resolve {}: {error}", path.display()));
    }
    let name = path
        .file_name()
        .ok_or_else(|| format!("output path {} has no file name", path.display()))?;
    let parent = path
        .parent()
        .filter(|value| !value.as_os_str().is_empty())
        .unwrap_or_else(|| Path::new("."));
    let resolved_parent = std::fs::canonicalize(parent).map_err(|error| {
        format!(
            "cannot resolve output directory {}: {error}",
            parent.display()
        )
    })?;
    Ok(resolved_parent.join(name))
}

/// SHA-256 of the rebinned activation-group flux vector — the workload
/// signature. Two cells with equal rebinned flux produce equal results, so
/// the second is served from the memo rather than re-solved.
fn flux_signature(flux_per_group: &[f64]) -> [u8; 32] {
    let mut hasher = Sha256::new();
    for value in flux_per_group {
        hasher.update(value.to_le_bytes());
    }
    hasher.finalize().into()
}

fn solve_result(
    mesh_spec: &MeshSpec,
    prepared: &PreparedRun,
    activation_boundaries: &[f64],
    flux_per_group: Vec<f64>,
    cell_id: &str,
) -> Result<String, String> {
    let spec = mesh_spec.cell_spec(activation_boundaries.to_vec(), flux_per_group);
    spec.validate()
        .map_err(|error| format!("cell '{cell_id}': {error}"))?;
    let result = prepared
        .run(&spec, "mesh")
        .map_err(|error| format!("cell '{cell_id}': {error}"))?;
    let mut result =
        result_without_timing(result).map_err(|error| format!("cell '{cell_id}': {error}"))?;
    if let Some(fields) = &mesh_spec.cell_result_fields {
        result
            .as_object_mut()
            .ok_or_else(|| format!("cell '{cell_id}': result did not serialize as an object"))?
            .retain(|key, _| fields.iter().any(|field| field == key));
    }
    serde_json::to_string(&result).map_err(|error| format!("cell '{cell_id}': {error}"))
}

fn cell_record(
    cell: &FluxCell,
    rebinned: &RebinResult,
    result: serde_json::Value,
) -> MeshCellRecord {
    MeshCellRecord {
        record: "cell",
        ordinal: cell.ordinal,
        id: cell.id.clone(),
        index: cell.index,
        bounds_cm: cell.bounds_cm,
        volume_cm3: cell.volume_cm3,
        source_relative_error: cell.relative_error.clone(),
        rebin: RebinLedger::from_result(rebinned),
        result,
    }
}

/// Process peak RSS on Linux, in bytes. `None` on platforms without
/// `/proc/self/status` — the memory guard then simply never fires.
fn peak_rss_bytes() -> Option<u64> {
    let status = std::fs::read_to_string("/proc/self/status").ok()?;
    for line in status.lines() {
        if let Some(rest) = line.strip_prefix("VmHWM:") {
            let text = rest.trim().strip_suffix("kB")?.trim();
            return text.parse::<u64>().ok().map(|kib| kib * 1024);
        }
    }
    None
}

/// A completed cell record's byte offset inside an existing output prefix.
struct ResumePrefix {
    /// Byte offset to resume writing at (end of the last complete record).
    offset: u64,
    /// Per-completed-cell record offsets, indexed by ordinal.
    cell_offsets: Vec<u64>,
    /// Per-completed-cell `result.pruned_states`, indexed by ordinal.
    pruned_states: Vec<u64>,
}

/// Scan an existing mesh output for the resumable prefix: byte-identical
/// header, then complete in-order cell records. Returns `None` when the file
/// already carries a footer (the run is complete).
fn resume_scan(output: &Path, expected_header: &[u8]) -> Result<Option<ResumePrefix>, String> {
    let mut file = File::open(output)
        .map_err(|error| format!("cannot read mesh output {}: {error}", output.display()))?;
    let mut content = Vec::new();
    file.read_to_end(&mut content)
        .map_err(|error| format!("cannot read mesh output {}: {error}", output.display()))?;
    if content.is_empty() {
        return Ok(Some(ResumePrefix {
            offset: 0,
            cell_offsets: Vec::new(),
            pruned_states: Vec::new(),
        }));
    }
    let header_end = content
        .iter()
        .position(|byte| *byte == b'\n')
        .ok_or("mesh output contains no complete header line")?
        + 1;
    if content[..header_end] != *expected_header {
        return Err(
            "mesh output header does not match this spec's fingerprint; refusing to resume".into(),
        );
    }
    let mut offset = header_end;
    let mut cell_offsets: Vec<u64> = Vec::new();
    let mut pruned_states: Vec<u64> = Vec::new();
    let mut position = header_end;
    while position < content.len() {
        let line_end = content[position..]
            .iter()
            .position(|byte| *byte == b'\n')
            .map(|index| position + index + 1)
            .unwrap_or(content.len());
        let line = &content[position..line_end];
        if line.is_empty() {
            position = line_end;
            continue;
        }
        if !line.ends_with(b"\n") {
            // Truncated tail from an interrupted write: resume after the
            // last complete record.
            break;
        }
        let record_bytes = &line[..line.len() - 1];
        let value: serde_json::Value = match serde_json::from_slice(record_bytes) {
            Ok(value) => value,
            Err(_) if line_end == content.len() => break,
            Err(_) => {
                return Err(format!(
                    "mesh output contains a corrupt record at completed cell {}",
                    cell_offsets.len()
                ))
            }
        };
        match value.get("record").and_then(serde_json::Value::as_str) {
            Some("cell") => {
                if value.get("ordinal").and_then(serde_json::Value::as_u64)
                    != Some(cell_offsets.len() as u64)
                {
                    return Err(format!(
                        "mesh output cell ordinal out of order at completed cell {}",
                        cell_offsets.len()
                    ));
                }
                let pruned = value
                    .get("result")
                    .and_then(|result| result.get("pruned_states"))
                    .and_then(serde_json::Value::as_u64)
                    .ok_or("mesh output cell record lacks result.pruned_states")?;
                cell_offsets.push(position as u64);
                pruned_states.push(pruned);
                offset = line_end;
            }
            Some("footer") => return Ok(None),
            _ => {
                return Err(format!(
                    "mesh output contains an unrecognized record before completion at cell {}",
                    cell_offsets.len()
                ))
            }
        }
        position = line_end;
    }
    Ok(Some(ResumePrefix {
        offset: offset as u64,
        cell_offsets,
        pruned_states,
    }))
}

/// Re-read a completed cell's `result` object from a resumable prefix, for
/// seeding the grouping memo so a resumed run's reuse accounting and emitted
/// bytes match an uninterrupted run.
fn read_prefix_result(
    file: &mut File,
    cell_offsets: &[u64],
    ordinal: u64,
) -> Result<String, String> {
    let offset = *cell_offsets
        .get(ordinal as usize)
        .ok_or_else(|| format!("completed cell {ordinal} has no recorded offset"))?;
    file.seek(SeekFrom::Start(offset))
        .map_err(|error| error.to_string())?;
    let mut line = Vec::new();
    let mut byte = [0u8; 1];
    loop {
        match file.read(&mut byte) {
            Ok(0) => break,
            Ok(_) if byte[0] == b'\n' => break,
            Ok(_) => line.push(byte[0]),
            Err(error) => return Err(error.to_string()),
        }
    }
    let value: serde_json::Value =
        serde_json::from_slice(&line).map_err(|error| format!("prefix cell {ordinal}: {error}"))?;
    let result = value
        .get("result")
        .ok_or_else(|| format!("prefix cell {ordinal} has no result field"))?;
    serde_json::to_string(result).map_err(|error| error.to_string())
}

/// How a first-occurrence signature in this chunk resolves.
enum Pending {
    /// Solved in this chunk; value is the index into `input_cells`.
    Solve(usize),
    /// Solved by an earlier interrupted run; value is the cell ordinal whose
    /// result can be re-read from the resumable prefix.
    Prefix(u64),
}

#[allow(clippy::too_many_arguments)]
fn write_mesh_body(
    spec: &MeshSpec,
    prepared: &PreparedRun,
    pool: &rayon::ThreadPool,
    mut stream: FluxStream,
    source_groups: &[f64],
    activation_boundaries: &[f64],
    canonical_hash: &str,
    resume_skip: u64,
    expected_cells: u64,
    prefix_pruned: &[u64],
    mut prefix_reader: Option<(&mut File, &[u64])>,
    output: &mut BufWriter<File>,
    started: &std::time::Instant,
) -> Result<(f64, f64), String> {
    let mut totals = MeshTotals::default();
    let mut cells_reused = 0u64;
    // Signature memo: at most GROUPING_CACHE_CAP distinct results and
    // GROUPING_CACHE_BYTES of payload, so memory stays bounded independently
    // of cell count and per-record size.
    let mut memo: HashMap<[u8; 32], String> = HashMap::new();
    let mut memo_bytes = 0usize;
    loop {
        let input_cells = stream.read_chunk(spec.chunk_cells)?;
        if input_cells.is_empty() {
            break;
        }
        let rebinned: Vec<RebinResult> = pool.install(|| {
            input_cells
                .par_iter()
                .map(|cell| {
                    rebin_equal_lethargy(source_groups, &cell.flux_per_group, activation_boundaries)
                        .map_err(|error| format!("cell '{}': {error}", cell.id))
                })
                .collect::<Result<_, String>>()
        })?;
        let signatures: Vec<[u8; 32]> = rebinned
            .iter()
            .map(|value| flux_signature(&value.flux_per_group))
            .collect();
        let mut pending: HashMap<[u8; 32], Pending> = HashMap::new();
        let mut to_solve: Vec<usize> = Vec::new();
        let mut resolved: Vec<Option<String>> = (0..input_cells.len()).map(|_| None).collect();
        // In-chunk repeats: cell index -> first-occurrence chunk index.
        let mut deferred: Vec<(usize, usize)> = Vec::new();
        for (index, cell) in input_cells.iter().enumerate() {
            let signature = signatures[index];
            let skipped = cell.ordinal < resume_skip;
            if spec.group_workloads {
                if let Some(entry) = pending.get(&signature) {
                    match *entry {
                        Pending::Solve(first) => {
                            if !skipped {
                                deferred.push((index, first));
                            }
                        }
                        Pending::Prefix(ordinal) => {
                            if !skipped {
                                let (file, offsets) = prefix_reader
                                    .as_mut()
                                    .map(|(file, offsets)| (&mut **file, *offsets))
                                    .ok_or("resumable prefix reader unavailable")?;
                                resolved[index] = Some(read_prefix_result(file, offsets, ordinal)?);
                            }
                        }
                    }
                    cells_reused += 1;
                    continue;
                }
                if let Some(stored) = memo.get(&signature) {
                    if !skipped {
                        resolved[index] = Some(stored.clone());
                    }
                    cells_reused += 1;
                    continue;
                }
            }
            // First occurrence of this signature in the stream.
            if skipped {
                if spec.group_workloads && memo.len() < GROUPING_CACHE_CAP {
                    let (file, offsets) = prefix_reader
                        .as_mut()
                        .map(|(file, offsets)| (&mut **file, *offsets))
                        .ok_or("resumable prefix reader unavailable")?;
                    let text = read_prefix_result(file, offsets, cell.ordinal)?;
                    if memo_bytes + text.len() <= GROUPING_CACHE_BYTES {
                        memo_bytes += text.len();
                        memo.insert(signature, text);
                    }
                }
                pending.insert(signature, Pending::Prefix(cell.ordinal));
                continue;
            }
            pending.insert(signature, Pending::Solve(index));
            to_solve.push(index);
        }
        let solved: Vec<Result<String, String>> = pool.install(|| {
            to_solve
                .par_iter()
                .map(|&index| {
                    solve_result(
                        spec,
                        prepared,
                        activation_boundaries,
                        rebinned[index].flux_per_group.clone(),
                        &input_cells[index].id,
                    )
                })
                .collect()
        });
        for (result, &index) in solved.into_iter().zip(to_solve.iter()) {
            let text = result?;
            if spec.group_workloads
                && memo.len() < GROUPING_CACHE_CAP
                && memo_bytes + text.len() <= GROUPING_CACHE_BYTES
            {
                memo_bytes += text.len();
                memo.insert(signatures[index], text.clone());
            }
            resolved[index] = Some(text);
        }
        for (index, first) in deferred {
            resolved[index] = resolved[first].clone();
        }
        for (index, cell) in input_cells.iter().enumerate() {
            let ledger = RebinLedger::from_result(&rebinned[index]);
            if cell.ordinal < resume_skip {
                // Completed in an earlier partial run: still feeds the
                // footer's flux and pruned-state closure.
                let pruned = *prefix_pruned
                    .get(cell.ordinal as usize)
                    .ok_or("resumable prefix lacks a pruned-state record")?;
                totals.add(&ledger, pruned as usize);
                continue;
            }
            let text = resolved[index]
                .as_ref()
                .expect("every unsolved cell resolves to a result string");
            let result: serde_json::Value = serde_json::from_str(text)
                .map_err(|error| format!("cell '{}': {error}", cell.id))?;
            let pruned = result["pruned_states"]
                .as_u64()
                .ok_or("ordinary solver result has no numeric pruned_states")?
                as usize;
            let record = cell_record(cell, &rebinned[index], result);
            totals.add(&ledger, pruned);
            write_record(output, &record)?;
        }
        output.flush().map_err(|error| error.to_string())?;
        if let Some(limit) = spec.memory_limit_bytes {
            if let Some(peak) = peak_rss_bytes() {
                if peak > limit {
                    return Err(format!(
                        "memory_limit_bytes exceeded: peak RSS {peak} bytes > limit {limit} bytes"
                    ));
                }
            }
        }
    }
    let source_footer = stream.finish()?;
    if totals.cells != expected_cells {
        return Err(format!(
            "mesh processed {} cells; canonical header declares {}",
            totals.cells, expected_cells
        ));
    }
    if relative_difference(totals.source.total(), source_footer.flux_sum_over_cells) > 1e-12 {
        return Err("mesh source totals do not close the canonical footer".into());
    }
    let final_hash = sha256_file(&spec.flux.path)?;
    if final_hash != canonical_hash {
        return Err(format!(
            "canonical flux changed during mesh execution: {}",
            spec.flux.path
        ));
    }
    let wall_time_s = started.elapsed().as_secs_f64();
    let cells_per_s = totals.cells as f64 / wall_time_s.max(f64::MIN_POSITIVE);
    let footer = MeshFooter {
        record: "footer",
        cell_count: totals.cells,
        source_flux_sum_over_cells: totals.source.total(),
        destination_flux_sum_over_cells: totals.destination.total(),
        underflow_sum_over_cells: totals.underflow.total(),
        overflow_sum_over_cells: totals.overflow.total(),
        max_rebin_relative_closure: totals.max_closure,
        min_pruned_states: totals.min_pruned.unwrap_or(0),
        max_pruned_states: totals.max_pruned,
        cells_served_from_reuse: cells_reused,
        wall_time_s,
        cells_per_s,
    };
    write_record(output, &footer)?;
    output.flush().map_err(|error| error.to_string())?;
    Ok((wall_time_s, cells_per_s))
}

/// Execute independent activation solves for every cell in a completed canonical flux file.
pub fn run_mesh(spec: &MeshSpec, output: impl AsRef<Path>) -> Result<MeshSummary, String> {
    spec.validate()?;
    let started = std::time::Instant::now();
    let output = output.as_ref();
    if resolved_path(Path::new(&spec.flux.path))? == resolved_path(output)? {
        return Err("mesh output must not overwrite its canonical flux input".into());
    }

    let canonical_hash = sha256_file(&spec.flux.path)?;
    if !canonical_hash.eq_ignore_ascii_case(&spec.flux.sha256) {
        return Err(format!(
            "SHA-256 mismatch for {}: declared {}, computed {}",
            spec.flux.path, spec.flux.sha256, canonical_hash
        ));
    }

    let stream = FluxStream::open(&spec.flux.path)?;
    let source_header = stream.header.clone();
    let expected_flux_units = if spec.projectile.is_neutron() {
        "n cm^-2 s^-1"
    } else {
        "particles cm^-2 s^-1"
    };
    if source_header.flux_units != expected_flux_units {
        return Err(format!(
            "canonical flux units '{}' do not match {} projectile; expected '{expected_flux_units}'",
            source_header.flux_units,
            spec.projectile.name()
        ));
    }
    let prepared = PreparedRun::prepare_inputs_with_extensions(
        &spec.library,
        &spec.decay,
        &spec.photon,
        &spec.fission_yields,
        spec.projectile,
        spec.options.temperature_K,
        spec.uncertainty.as_ref(),
        spec.radiological.as_ref(),
        spec.damage.as_ref(),
        spec.self_shielding.as_ref(),
    )?;
    let activation_boundaries = prepared.library_boundaries_eV().to_vec();
    if activation_boundaries.len() != prepared.library_groups() + 1 {
        return Err("activation library group boundaries are inconsistent".into());
    }
    let pool = rayon::ThreadPoolBuilder::new()
        .num_threads(spec.threads)
        .build()
        .map_err(|error| format!("cannot create mesh worker pool: {error}"))?;

    let header = MeshHeader {
        record: "header",
        schema: MESH_RESULT_SCHEMA,
        spec_title: spec.title.clone(),
        cell_count: source_header.cell_count,
        spec_fingerprint_sha256: spec.fingerprint_sha256()?,
        projectile: (!spec.projectile.is_neutron()).then(|| spec.projectile.name().to_owned()),
        flux_units: expected_flux_units,
        source_energy_boundaries_eV: source_header.energy_boundaries_eV.clone(),
        activation_energy_boundaries_eV: activation_boundaries.clone(),
        geometry: source_header.geometry.clone(),
        certificate: MeshCertificate {
            solver: format!("actinv-core {}", env!("CARGO_PKG_VERSION")),
            projectile: (!spec.projectile.is_neutron()).then(|| spec.projectile.name().to_owned()),
            canonical_flux: CanonicalFluxCertificate {
                path: spec.flux.path.clone(),
                sha256_declared: spec.flux.sha256.clone(),
                sha256_computed: canonical_hash.clone(),
            },
            upstream_source: source_header.source.clone(),
        },
    };

    // The header's serialized bytes double as the resume identity: a resumed
    // run must reproduce them exactly (same spec fingerprint, flux hash and
    // grids).
    let mut header_bytes = serde_json::to_vec(&header).map_err(|error| error.to_string())?;
    header_bytes.push(b'\n');

    // In resume mode the output file is the checkpoint: existing complete
    // records stand, a truncated tail is dropped, and only unfinished cells
    // are re-solved. Otherwise output is written atomically as before.
    let mut resume_skip = 0u64;
    let mut resume_offset = 0u64;
    let mut prefix_pruned: Vec<u64> = Vec::new();
    let mut cell_offsets: Vec<u64> = Vec::new();
    if spec.resume && output.exists() {
        match resume_scan(output, &header_bytes)? {
            None => {
                return Ok(MeshSummary {
                    output: output.display().to_string(),
                    canonical_flux_sha256: canonical_hash,
                    cells: source_header.cell_count,
                    source_groups: source_header.energy_boundaries_eV.len() - 1,
                    activation_groups: activation_boundaries.len() - 1,
                    output_bytes: std::fs::metadata(output)
                        .map_err(|error| error.to_string())?
                        .len(),
                    wall_time_s: started.elapsed().as_secs_f64(),
                    cells_per_s: 0.0,
                });
            }
            Some(prefix) => {
                resume_offset = prefix.offset;
                resume_skip = prefix.cell_offsets.len() as u64;
                cell_offsets = prefix.cell_offsets;
                prefix_pruned = prefix.pruned_states;
            }
        }
    }

    let mut final_wall_time = 0.0;
    let mut final_rate = 0.0;
    if spec.resume {
        // Direct write: the output file itself is the checkpoint. The
        // validated prefix (header plus complete cell records) stands; a
        // truncated tail is cut at resume_offset and only the remaining
        // cells are solved and appended.
        let file = std::fs::OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .open(output)
            .map_err(|error| format!("cannot open {}: {error}", output.display()))?;
        // A separate open (not try_clone) is required: cloned handles share
        // one file offset, so prefix reads would move the writer's cursor.
        let mut prefix_file = File::open(output)
            .map_err(|error| format!("cannot re-open {}: {error}", output.display()))?;
        file.set_len(resume_offset)
            .map_err(|error| format!("cannot truncate {}: {error}", output.display()))?;
        let mut writer = BufWriter::new(file);
        writer
            .seek(SeekFrom::Start(resume_offset))
            .map_err(|error| error.to_string())?;
        if resume_offset == 0 {
            writer
                .write_all(&header_bytes)
                .map_err(|error| error.to_string())?;
        }
        let (wall, rate) = write_mesh_body(
            spec,
            &prepared,
            &pool,
            stream,
            &source_header.energy_boundaries_eV,
            &activation_boundaries,
            &canonical_hash,
            resume_skip,
            source_header.cell_count,
            &prefix_pruned,
            Some((&mut prefix_file, &cell_offsets)),
            &mut writer,
            &started,
        )?;
        writer.flush().map_err(|error| error.to_string())?;
        final_wall_time = wall;
        final_rate = rate;
    } else {
        atomic_output(output, |mut writer| {
            writer
                .write_all(&header_bytes)
                .map_err(|error| error.to_string())?;
            write_mesh_body(
                spec,
                &prepared,
                &pool,
                stream,
                &source_header.energy_boundaries_eV,
                &activation_boundaries,
                &canonical_hash,
                0,
                source_header.cell_count,
                &[],
                None,
                &mut writer,
                &started,
            )
            .map(|(wall, rate)| {
                final_wall_time = wall;
                final_rate = rate;
            })
        })?;
    }

    let output_bytes = std::fs::metadata(output)
        .map_err(|error| format!("cannot stat mesh result {}: {error}", output.display()))?
        .len();
    Ok(MeshSummary {
        output: output.display().to_string(),
        canonical_flux_sha256: canonical_hash,
        cells: source_header.cell_count,
        source_groups: source_header.energy_boundaries_eV.len() - 1,
        activation_groups: activation_boundaries.len() - 1,
        output_bytes,
        wall_time_s: final_wall_time,
        cells_per_s: final_rate,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::BTreeMap;

    fn minimal_spec() -> MeshSpec {
        MeshSpec {
            spec: MESH_SPEC_SCHEMA.into(),
            title: String::new(),
            projectile: Projectile::Neutron,
            library: LibraryRef {
                path: "library.npz".into(),
                sha256: None,
            },
            decay: DecayRef {
                primary: "decay.dat".into(),
                fallback: None,
            },
            material: Material {
                mass_g: 1.0,
                basis: "wt_percent".into(),
                composition: BTreeMap::from([("FE".into(), 100.0)]),
            },
            flux: HashedFileRef {
                path: "flux.ndjson".into(),
                sha256: "0".repeat(64),
            },
            schedule: vec![Step {
                dt: "1 s".into(),
                flux: 1.0,
                spectrum: None,
                feed: None,
                removal: None,
            }],
            options: Options::default(),
            photon: PhotonOptions::default(),
            fission_yields: FissionYieldOptions::default(),
            uncertainty: None,
            radiological: None,
            damage: None,
            self_shielding: None,
            chunk_cells: 1,
            threads: 1,
            group_workloads: true,
            cell_result_fields: None,
            memory_limit_bytes: None,
            resume: false,
        }
    }

    #[test]
    fn mesh_controls_are_bounded() {
        let mut spec = minimal_spec();
        assert!(spec.validate().is_ok());
        spec.chunk_cells = 0;
        assert!(spec.validate().unwrap_err().contains("chunk_cells"));
        spec.chunk_cells = 1;
        spec.threads = MAX_THREADS + 1;
        assert!(spec.validate().unwrap_err().contains("threads"));
    }

    #[test]
    fn output_alias_resolves_to_the_canonical_input() {
        let stamp = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let root =
            std::env::temp_dir().join(format!("actinv-mesh-path-{}-{stamp}", std::process::id()));
        let child = root.join("child");
        std::fs::create_dir_all(&child).unwrap();
        let input = root.join("flux.ndjson");
        std::fs::write(&input, b"fixture").unwrap();
        let alias = child.join("..").join("flux.ndjson");
        assert_eq!(
            resolved_path(&input).unwrap(),
            resolved_path(&alias).unwrap()
        );
        std::fs::remove_dir_all(root).unwrap();
    }
}
