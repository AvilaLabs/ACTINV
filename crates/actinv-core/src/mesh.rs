#![allow(non_snake_case)] // JSON wire names carry their physical units.
//! Deterministic, bounded-memory independent-cell activation runner.

use crate::damage::DamageStepOut;
use crate::flux::{
    atomic_output, rebin_equal_lethargy, sha256_file, FluxCell, FluxGeometry, FluxSource,
    FluxStream, RebinResult,
};
use crate::photon::PhotonSourceOut;
use crate::radiological::RadiologicalStepOut;
use crate::run::{
    CellCollapse, GasOut, GasSpecies, Heat, MeshFluxOrigin, PreparedRun, RunResult, StepOut,
};
use crate::spec::{
    DamageOptions, DecayRef, FissionYieldOptions, HashedFileRef, LibraryRef, Material, Options,
    PhotonOptions, Projectile, RadiologicalOptions, SelfShieldingOptions, Spec, Spectrum, Step,
    UncertaintyOptions,
};
use crate::uncertainty::StepUncertainty;
use rayon::prelude::*;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, HashMap};
use std::fs::File;
use std::io::{BufRead, BufWriter, Seek, SeekFrom, Write};
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

/// Every key `RunResult` serializes to (a superset of `RESULT_FIELDS`: `screen`
/// and `isomer_pathway_shares` are not selectable through `cell_result_fields`,
/// but `run_result_field` still has to know them so a future field can't be
/// added to the struct without a matching accessor arm going unnoticed). Used
/// only by the accessor-completeness unit tests: `run_result_field`'s match
/// arms are checked against this list there, not read at runtime.
#[cfg(test)]
const RUN_RESULT_ACCESSOR_FIELDS: &[&str] = &[
    "spec_title",
    "entry_point",
    "projectile",
    "mode",
    "pruned_states",
    "total_states",
    "steps",
    "pathways",
    "screen",
    "pathway_closure",
    "isomer_pathway_shares",
    "ledger",
    "certificate",
    "ms",
];

/// `RunResult` fields that are absent (key omitted) rather than serialized as
/// `null` when unset (`#[serde(skip_serializing_if = "Option::is_none")]`).
/// `run_result_field` reports these as `Value::Null` when unset; none of them
/// can otherwise ever serialize to a literal `null`, so the sentinel is
/// unambiguous for exactly this set.
const RESULT_OPTIONAL_FIELDS: &[&str] = &["projectile", "screen", "isomer_pathway_shares"];

/// Every key `StepOut` serializes to. Used both to validate `steps.<field>`
/// selections and, together with `RUN_RESULT_ACCESSOR_FIELDS`, to check that
/// `step_field`/`run_result_field` have a match arm for every field a future
/// change might add.
const STEP_FIELDS: &[&str] = &[
    "step",
    "t_s",
    "flux",
    "flux_weighted_time_s",
    "fluence_n_cm2",
    "fluence_particles_cm2",
    "inventory",
    "activity_Bq_per_g",
    "heat_W_per_g",
    "leakage_atoms_per_g",
    "removed_atoms_per_g",
    "negative_atoms_zeroed",
    "total_atoms_per_g",
    "n_states_populated",
    "numerical_floor_atoms_per_g",
    "n_states_below_floor",
    "atoms_below_floor",
    "heat_bound_from_below_floor_W_per_g",
    "photon_source",
    "uncertainty",
    "radiological",
    "damage",
    "gas",
];

/// `StepOut` fields that are absent (key omitted) rather than serialized as
/// `null` when unset. See `RESULT_OPTIONAL_FIELDS`: none of these can
/// otherwise serialize to a literal `null`, so `Value::Null` is an
/// unambiguous "unset" sentinel for exactly this set.
const STEP_OPTIONAL_FIELDS: &[&str] = &[
    "fluence_n_cm2",
    "fluence_particles_cm2",
    "removed_atoms_per_g",
    "photon_source",
    "uncertainty",
    "radiological",
    "damage",
    "gas",
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
    /// Per-cell material overrides: cell id -> material. Cells not listed
    /// solve with the default `material`. The signature that memoizes
    /// identical-flux cells mixes the resolved material bytes in, so an
    /// override can never inherit another material's result.
    #[serde(default)]
    pub materials: Option<HashMap<String, Material>>,
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
            let mut has_plain_steps = false;
            let mut has_dotted_steps = false;
            for field in fields {
                if field == "steps" {
                    has_plain_steps = true;
                    continue;
                }
                if let Some(rest) = field.strip_prefix("steps.") {
                    has_dotted_steps = true;
                    validate_step_path(rest)?;
                    continue;
                }
                if !RESULT_FIELDS.contains(&field.as_str()) {
                    return Err(format!(
                        "cell_result_fields entry '{field}' is not a run-result field"
                    ));
                }
            }
            if has_plain_steps && has_dotted_steps {
                return Err(
                    "cell_result_fields cannot combine 'steps' with a 'steps.<field>' entry".into(),
                );
            }
        }
        if self.memory_limit_bytes.is_some_and(|limit| limit == 0) {
            return Err("memory_limit_bytes must be positive".into());
        }
        // The guard reads Linux peak-RSS accounting; elsewhere it could never fire, so
        // refuse it rather than let a declared safety limit silently do nothing.
        if self.memory_limit_bytes.is_some() && peak_rss_bytes().is_none() {
            return Err(
                "memory_limit_bytes needs peak-RSS accounting (/proc/self/status, Linux), \
                 which this platform does not provide"
                    .into(),
            );
        }

        // Reuse the ordinary-spec validator for every shared field. The placeholder spectrum
        // is valid by construction and is replaced with each rebinned cell before execution.
        self.cell_spec(vec![1.0, 2.0], vec![0.0], &self.material)
            .validate()?;
        if let Some(overrides) = &self.materials {
            for (cell_id, material) in overrides {
                self.cell_spec(vec![1.0, 2.0], vec![0.0], material)
                    .validate()
                    .map_err(|error| format!("materials['{cell_id}']: {error}"))?;
            }
        }
        Ok(())
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

    fn cell_spec(
        &self,
        boundaries_eV: Vec<f64>,
        flux_per_group: Vec<f64>,
        material: &Material,
    ) -> Spec {
        Spec {
            spec: "actinv-spec-1".into(),
            title: self.title.clone(),
            projectile: self.projectile,
            library: self.library.clone(),
            decay: self.decay.clone(),
            material: material.clone(),
            spectrum: Spectrum {
                structure: "custom".into(),
                flux_per_group,
                total: None,
                boundaries_eV: Some(boundaries_eV),
                descending: false,
                // P93: the flux channel's mesh input is the flux file's own
                // (pre-rebin) source groups, carried separately via
                // `MeshFluxOrigin`; this rebinned spectrum never carries it.
                relative_error: None,
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
    /// Canonical SHA-256 of the material this cell solved with — groups
    /// cells into material classes for downstream assimilation.
    material_sha256: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    source_relative_error: Option<Vec<f64>>,
    rebin: RebinLedger,
    /// The cell's result text, written verbatim (P82). With `float_roundtrip`, parsing it into a
    /// `Value` and serializing again would reproduce it byte for byte, so the round trip is skipped.
    result: Box<serde_json::value::RawValue>,
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
///
/// P93: when the flux channel is requested, the caller also passes the cell's
/// own (pre-rebin) source `relative_error`: two cells with equal flux but
/// different tally statistical error propagate different bands and must not
/// be deduplicated together. With `None` the digest is the pre-P93 one.
fn flux_signature(
    flux_per_group: &[f64],
    relative_error: Option<&[f64]>,
    material_sha: &[u8; 32],
) -> [u8; 32] {
    let mut hasher = Sha256::new();
    hasher.update(material_sha);
    for value in flux_per_group {
        hasher.update(value.to_le_bytes());
    }
    if let Some(errors) = relative_error {
        hasher.update([1u8]);
        for value in errors {
            hasher.update(value.to_le_bytes());
        }
    }
    hasher.finalize().into()
}

fn material_sha256(material: &Material) -> [u8; 32] {
    let canonical = serde_json::to_vec(material).unwrap_or_default();
    Sha256::digest(&canonical).into()
}

fn hex32(digest: &[u8; 32]) -> String {
    digest.iter().map(|byte| format!("{byte:02x}")).collect()
}

/// Cells whose flux collapse shares one pass over the library (P81); bounds the batch buffer to
/// `COLLAPSE_BATCH_CELLS` values per library row.
const COLLAPSE_BATCH_CELLS: usize = 16;
/// Library rows per parallel task of a batched collapse.
const COLLAPSE_ROW_BLOCK: usize = 4096;

/// Solve the cells `to_solve` (chunk indices), in order, collapsing each batch of cells in one
/// pass over the library. Every cell's result is the same as solving it alone.
#[allow(clippy::too_many_arguments)]
fn solve_cells(
    mesh_spec: &MeshSpec,
    prepared: &PreparedRun,
    pool: &rayon::ThreadPool,
    source_boundaries: &[f64],
    activation_boundaries: &[f64],
    rebinned: &[RebinResult],
    cell_materials: &[&Material],
    input_cells: &[FluxCell],
    to_solve: &[usize],
) -> Vec<Result<(String, usize), String>> {
    // P93: a mesh cell's flux channel uses the flux file's own (pre-rebin)
    // source groups, not `cell_spec`'s activation-group-rebinned spectrum
    // (which never carries relative_error — see `cell_spec`). Built once
    // per cell only when the channel is actually requested, so a run
    // without it is unaffected.
    let flux_channel_requested = mesh_spec
        .uncertainty
        .as_ref()
        .is_some_and(|options| options.channels.iter().any(|name| name == "flux"));
    let mut solved = Vec::with_capacity(to_solve.len());
    for batch in to_solve.chunks(COLLAPSE_BATCH_CELLS) {
        let specs: Vec<Spec> = batch
            .iter()
            .map(|&index| {
                mesh_spec.cell_spec(
                    activation_boundaries.to_vec(),
                    rebinned[index].flux_per_group.clone(),
                    cell_materials[index],
                )
            })
            .collect();
        let fluxes: Vec<Vec<f64>> = specs.iter().map(Spec::flux_ascending).collect();
        let phis: Vec<&[f64]> = fluxes.iter().map(Vec::as_slice).collect();
        let rows = prepared.library_row_count();
        // A lone cell gains nothing from a shared pass (the pass costs a fixed number of lanes),
        // so it keeps the ordinary per-row collapse; the result is the same either way.
        let batch_worthwhile = phis.len() >= 2;
        let mut values = vec![0.0f64; rows * phis.len() * usize::from(batch_worthwhile)];
        let batched = batch_worthwhile
            && pool.install(|| {
                values
                    .par_chunks_mut(COLLAPSE_ROW_BLOCK * phis.len())
                    .enumerate()
                    .map(|(block, out)| {
                        let start = block * COLLAPSE_ROW_BLOCK;
                        let end = (start + COLLAPSE_ROW_BLOCK).min(rows);
                        prepared.collapse_rows_batched(&phis, start..end, out)
                    })
                    .collect::<Vec<bool>>()
                    .into_iter()
                    .all(|done| done)
            });
        let results: Vec<Result<(String, usize), String>> = pool.install(|| {
            batch
                .par_iter()
                .enumerate()
                .map(|(slot, &index)| {
                    let cell = batched.then_some(CellCollapse {
                        phi: phis[slot],
                        values: &values,
                        stride: phis.len(),
                        offset: slot,
                    });
                    let flux_origin = flux_channel_requested.then(|| MeshFluxOrigin {
                        source_boundaries_eV: source_boundaries,
                        source_flux_per_group: &input_cells[index].flux_per_group,
                        source_relative_error: input_cells[index].relative_error.as_deref(),
                    });
                    solve_result(
                        mesh_spec,
                        prepared,
                        &specs[slot],
                        cell,
                        flux_origin,
                        &input_cells[index].id,
                    )
                })
                .collect()
        });
        solved.extend(results);
    }
    solved
}

/// Selected `steps.<field>[.<key>...]` entries of `cell_result_fields`, split
/// on `.` past the `steps.` prefix. Empty when there are none (P90's
/// unchanged route then applies).
fn dotted_step_paths(fields: &[String]) -> Vec<Vec<&str>> {
    fields
        .iter()
        .filter_map(|field| {
            field
                .strip_prefix("steps.")
                .map(|rest| rest.split('.').collect())
        })
        .collect()
}

/// Validate one `steps.<rest>` entry: `<rest>`'s first segment must be a
/// `StepOut` field, and every further segment must descend through a JSON
/// object (checked against `maximal_step_out`'s shape — a `StepOut` with
/// every optional field populated, so absence in a particular run's step
/// never makes a structurally valid path look invalid). A dynamic map
/// (`activity_Bq_per_g`, `uncertainty.responses`, `damage.elements`) is left
/// empty in that shape, so a path cannot go past it into a data-dependent
/// key; only the map itself is selectable.
fn validate_step_path(rest: &str) -> Result<(), String> {
    let segments: Vec<&str> = rest.split('.').collect();
    if segments.iter().any(|segment| segment.is_empty()) {
        return Err(format!(
            "cell_result_fields entry 'steps.{rest}' has an empty path segment"
        ));
    }
    let (field, tail) = segments
        .split_first()
        .expect("split on a non-empty string yields at least one segment");
    if !STEP_FIELDS.contains(field) {
        return Err(format!(
            "cell_result_fields entry 'steps.{rest}': '{field}' is not a StepOut field"
        ));
    }
    let shape = serde_json::to_value(maximal_step_out())
        .expect("a fully populated StepOut always serializes");
    let mut node = &shape[*field];
    for segment in tail {
        let object = node.as_object().ok_or_else(|| {
            format!(
                "cell_result_fields entry 'steps.{rest}' reaches a non-object value \
                 with segments remaining"
            )
        })?;
        node = object.get(*segment).ok_or_else(|| {
            format!("cell_result_fields entry 'steps.{rest}': '{segment}' is not a key there")
        })?;
    }
    Ok(())
}

/// A `StepOut` with every optional field populated (and every
/// `skip_serializing_if`-when-empty `Vec` given one placeholder element), used
/// only to learn the static JSON shape `steps.<field>[.<key>...]` validation
/// checks against. Values are placeholders; only key presence and whether a
/// node is an object, array or scalar matter. Kept in sync with `StepOut` by
/// `step_field_names_cover_every_key_a_populated_step_out_serializes` (a new
/// field left out here fails that test, not silently falls through).
fn maximal_step_out() -> StepOut {
    StepOut {
        step: 0,
        t_s: 0.0,
        flux: 0.0,
        flux_weighted_time_s: 0.0,
        fluence_n_cm2: Some(0.0),
        fluence_particles_cm2: Some(0.0),
        inventory: Vec::new(),
        activity_Bq_per_g: BTreeMap::new(),
        heat_W_per_g: Heat {
            total: 0.0,
            alpha: 0.0,
            beta: 0.0,
            gamma: 0.0,
        },
        leakage_atoms_per_g: 0.0,
        removed_atoms_per_g: Some(0.0),
        negative_atoms_zeroed: 0.0,
        total_atoms_per_g: 0.0,
        n_states_populated: 0,
        numerical_floor_atoms_per_g: 0.0,
        n_states_below_floor: 0,
        atoms_below_floor: 0.0,
        heat_bound_from_below_floor_W_per_g: 0.0,
        photon_source: Some(PhotonSourceOut {
            group_structure: String::new(),
            boundaries_eV: Vec::new(),
            lines: Vec::new(),
            groups: Vec::new(),
            by_nuclide: Vec::new(),
            grouped_photons_s_g: 0.0,
            grouped_photons_s: 0.0,
            total_photons_s_g: 0.0,
            total_photons_s: 0.0,
            source_power_W_g: 0.0,
            source_power_W: 0.0,
            ungrouped_power_W_g: 0.0,
            unrepresented_gamma_power_W_g: 0.0,
            represented_gamma_power_fraction: 0.0,
            contact_gamma_air_dose_proxy_Gy_h: None,
            dose_response_power_coverage: None,
            dose_response_subthreshold_power_W_g: 0.0,
        }),
        uncertainty: Some(StepUncertainty {
            method: "shape",
            uncovered_library_rows: Vec::new(),
            absent_cross_parameter_pairs: 0,
            maximum_covariance_asymmetry_barn2: 0.0,
            excluded_blocks: Vec::new(),
            uncovered_decay_constants: vec!["shape".into()],
            uncovered_yield_products: vec!["shape".into()],
            responses: BTreeMap::new(),
        }),
        radiological: Some(RadiologicalStepOut {
            responses: Vec::new(),
        }),
        damage: Some(DamageStepOut {
            dpa_rate_per_s: 0.0,
            dpa: 0.0,
            damage_energy_eV_per_g_s: 0.0,
            covered_atom_fraction: 0.0,
            elements: BTreeMap::new(),
        }),
        gas: Some(GasOut {
            H1: placeholder_gas_species(),
            H2: placeholder_gas_species(),
            H3: placeholder_gas_species(),
            He3: placeholder_gas_species(),
            He4: placeholder_gas_species(),
            H_appm: 0.0,
            He_appm: 0.0,
            H_inventory_appm: 0.0,
            He_inventory_appm: 0.0,
            initial_atoms_per_g: 0.0,
        }),
    }
}

fn placeholder_gas_species() -> GasSpecies {
    GasSpecies {
        atoms_per_g: 0.0,
        produced_atoms_per_g: 0.0,
        appm: 0.0,
        inventory_appm: 0.0,
    }
}

/// Explicit accessor for one `RunResult` top-level field's `serde_json::Value`.
/// `None` for a name that is not a `RunResult` field at all;
/// `Some(Ok(Value::Null))` for a `RESULT_OPTIONAL_FIELDS` name whose `Option`
/// is unset (never otherwise reachable, since none of those fields serialize
/// to a literal `null` when set — see `RESULT_OPTIONAL_FIELDS`).
fn run_result_field(result: &RunResult, name: &str) -> Option<Result<serde_json::Value, String>> {
    let value = match name {
        "spec_title" => serde_json::to_value(&result.spec_title),
        "entry_point" => serde_json::to_value(&result.entry_point),
        "projectile" => match &result.projectile {
            Some(projectile) => serde_json::to_value(projectile),
            None => Ok(serde_json::Value::Null),
        },
        "mode" => serde_json::to_value(&result.mode),
        "pruned_states" => serde_json::to_value(result.pruned_states),
        "total_states" => serde_json::to_value(result.total_states),
        "steps" => serde_json::to_value(&result.steps),
        "pathways" => serde_json::to_value(&result.pathways),
        "screen" => match &result.screen {
            Some(screen) => serde_json::to_value(screen),
            None => Ok(serde_json::Value::Null),
        },
        "pathway_closure" => serde_json::to_value(result.pathway_closure),
        "isomer_pathway_shares" => match &result.isomer_pathway_shares {
            Some(shares) => serde_json::to_value(shares),
            None => Ok(serde_json::Value::Null),
        },
        "ledger" => serde_json::to_value(&result.ledger),
        "certificate" => serde_json::to_value(&result.certificate),
        "ms" => serde_json::to_value(result.ms),
        _ => return None,
    };
    Some(value.map_err(|error| error.to_string()))
}

/// Explicit accessor for one `StepOut` top-level field's `serde_json::Value`.
/// See `run_result_field`; `STEP_OPTIONAL_FIELDS` is the analogous unset-sentinel set here.
fn step_field(step: &StepOut, name: &str) -> Option<Result<serde_json::Value, String>> {
    let value = match name {
        "step" => serde_json::to_value(step.step),
        "t_s" => serde_json::to_value(step.t_s),
        "flux" => serde_json::to_value(step.flux),
        "flux_weighted_time_s" => serde_json::to_value(step.flux_weighted_time_s),
        "fluence_n_cm2" => match step.fluence_n_cm2 {
            Some(value) => serde_json::to_value(value),
            None => Ok(serde_json::Value::Null),
        },
        "fluence_particles_cm2" => match step.fluence_particles_cm2 {
            Some(value) => serde_json::to_value(value),
            None => Ok(serde_json::Value::Null),
        },
        "inventory" => serde_json::to_value(&step.inventory),
        "activity_Bq_per_g" => serde_json::to_value(&step.activity_Bq_per_g),
        "heat_W_per_g" => serde_json::to_value(&step.heat_W_per_g),
        "leakage_atoms_per_g" => serde_json::to_value(step.leakage_atoms_per_g),
        "removed_atoms_per_g" => match step.removed_atoms_per_g {
            Some(value) => serde_json::to_value(value),
            None => Ok(serde_json::Value::Null),
        },
        "negative_atoms_zeroed" => serde_json::to_value(step.negative_atoms_zeroed),
        "total_atoms_per_g" => serde_json::to_value(step.total_atoms_per_g),
        "n_states_populated" => serde_json::to_value(step.n_states_populated),
        "numerical_floor_atoms_per_g" => serde_json::to_value(step.numerical_floor_atoms_per_g),
        "n_states_below_floor" => serde_json::to_value(step.n_states_below_floor),
        "atoms_below_floor" => serde_json::to_value(step.atoms_below_floor),
        "heat_bound_from_below_floor_W_per_g" => {
            serde_json::to_value(step.heat_bound_from_below_floor_W_per_g)
        }
        "photon_source" => match &step.photon_source {
            Some(photon_source) => serde_json::to_value(photon_source),
            None => Ok(serde_json::Value::Null),
        },
        "uncertainty" => match &step.uncertainty {
            Some(uncertainty) => serde_json::to_value(uncertainty),
            None => Ok(serde_json::Value::Null),
        },
        "radiological" => match &step.radiological {
            Some(radiological) => serde_json::to_value(radiological),
            None => Ok(serde_json::Value::Null),
        },
        "damage" => match &step.damage {
            Some(damage) => serde_json::to_value(damage),
            None => Ok(serde_json::Value::Null),
        },
        "gas" => match &step.gas {
            Some(gas) => serde_json::to_value(gas),
            None => Ok(serde_json::Value::Null),
        },
        _ => return None,
    };
    Some(value.map_err(|error| error.to_string()))
}

/// Prune `value` (a JSON object) to the keys named by `tails`' first segments;
/// a tail that ends there keeps that key's value whole, one that continues
/// recurses. Called only on paths `validate_step_path` already accepted, so
/// `value` is always an object here and every requested key is present.
fn prune_object(value: &serde_json::Value, tails: &[&[&str]]) -> Result<serde_json::Value, String> {
    let object = value
        .as_object()
        .ok_or("cell_result_fields: a dotted path reaches a non-object value")?;
    let mut grouped: BTreeMap<&str, Vec<&[&str]>> = BTreeMap::new();
    for tail in tails {
        let (head, rest) = tail
            .split_first()
            .expect("prune_object is never called with an empty tail");
        grouped.entry(*head).or_default().push(rest);
    }
    let mut out = serde_json::Map::new();
    for (key, rests) in grouped {
        let Some(child) = object.get(key) else {
            continue;
        };
        let value = if rests.iter().any(|rest| rest.is_empty()) {
            child.clone()
        } else {
            prune_object(child, &rests)?
        };
        out.insert(key.to_string(), value);
    }
    Ok(serde_json::Value::Object(out))
}

/// Build one step's lean object: `"step"` plus each selected `StepOut` field,
/// pruned to the selected keys. A selected field whose value is unset in this
/// step (`STEP_OPTIONAL_FIELDS`, e.g. `photon_source` without photon output)
/// is left out entirely, as in the full output.
fn build_lean_step(
    step: &StepOut,
    step_paths: &[Vec<&str>],
) -> Result<serde_json::Map<String, serde_json::Value>, String> {
    let mut grouped: BTreeMap<&str, Vec<&[&str]>> = BTreeMap::new();
    for path in step_paths {
        let (head, tail) = path
            .split_first()
            .expect("validate_step_path rejects an empty path");
        grouped.entry(*head).or_default().push(tail);
    }
    let mut out = serde_json::Map::new();
    out.insert("step".to_string(), serde_json::Value::from(step.step));
    for (field, tails) in grouped {
        if field == "step" {
            continue; // already emitted unconditionally
        }
        let whole = step_field(step, field)
            .ok_or_else(|| format!("internal: unvalidated step field '{field}'"))??;
        if STEP_OPTIONAL_FIELDS.contains(&field) && whole.is_null() {
            continue;
        }
        let value = if tails.iter().any(|tail| tail.is_empty()) {
            whole
        } else {
            prune_object(&whole, &tails)?
        };
        out.insert(field.to_string(), value);
    }
    Ok(out)
}

/// Build the lean cell text directly from `result`'s fields, never
/// serializing the whole `RunResult`. `top_fields` are the non-`steps.`
/// `cell_result_fields` entries; `"ms"` is always left out (it is removed
/// unconditionally in the unchanged route too, via `result_without_timing`).
fn lean_cell_text(
    result: &RunResult,
    top_fields: &[&str],
    step_paths: &[Vec<&str>],
) -> Result<String, String> {
    let mut map: BTreeMap<String, serde_json::Value> = BTreeMap::new();
    for &field in top_fields {
        if field == "steps" || field == "ms" {
            continue;
        }
        let value = run_result_field(result, field)
            .ok_or_else(|| format!("cell_result_fields entry '{field}' is not a run-result field"))?
            .map_err(|error| format!("field '{field}': {error}"))?;
        if RESULT_OPTIONAL_FIELDS.contains(&field) && value.is_null() {
            continue;
        }
        map.insert(field.to_string(), value);
    }
    if !step_paths.is_empty() {
        let mut steps = Vec::with_capacity(result.steps.len());
        for step in &result.steps {
            steps.push(serde_json::Value::Object(build_lean_step(
                step, step_paths,
            )?));
        }
        map.insert("steps".to_string(), serde_json::Value::Array(steps));
    }
    serde_json::to_string(&map).map_err(|error| error.to_string())
}

fn solve_result(
    mesh_spec: &MeshSpec,
    prepared: &PreparedRun,
    spec: &Spec,
    cell: Option<CellCollapse<'_>>,
    flux_origin: Option<MeshFluxOrigin<'_>>,
    cell_id: &str,
) -> Result<(String, usize), String> {
    spec.validate()
        .map_err(|error| format!("cell '{cell_id}': {error}"))?;
    let result = prepared
        .run_with_collapse_and_flux_origin(spec, "mesh", cell, flux_origin)
        .map_err(|error| format!("cell '{cell_id}': {error}"))?;
    let step_paths = mesh_spec
        .cell_result_fields
        .as_deref()
        .map(dotted_step_paths)
        .unwrap_or_default();
    if !step_paths.is_empty() {
        // P90 lean route: no whole-`RunResult` `Value` is ever built.
        let pruned = result.pruned_states;
        let top_fields: Vec<&str> = mesh_spec
            .cell_result_fields
            .as_deref()
            .unwrap_or(&[])
            .iter()
            .filter(|field| !field.starts_with("steps."))
            .map(String::as_str)
            .collect();
        let text = lean_cell_text(&result, &top_fields, &step_paths)
            .map_err(|error| format!("cell '{cell_id}': {error}"))?;
        return Ok((text, pruned));
    }
    // Unchanged route (P82/P88): byte-for-byte identical to pre-P90 behavior
    // when `cell_result_fields` has no `steps.` entry (including `None`).
    let mut result =
        result_without_timing(result).map_err(|error| format!("cell '{cell_id}': {error}"))?;
    // The field filter may strip `pruned_states` from the emitted record;
    // the runner itself needs it, so read it before filtering.
    let pruned = result["pruned_states"]
        .as_u64()
        .ok_or_else(|| format!("cell '{cell_id}': solver result has no numeric pruned_states"))?
        as usize;
    if let Some(fields) = &mesh_spec.cell_result_fields {
        result
            .as_object_mut()
            .ok_or_else(|| format!("cell '{cell_id}': result did not serialize as an object"))?
            .retain(|key, _| fields.iter().any(|field| field == key));
    }
    let text =
        serde_json::to_string(&result).map_err(|error| format!("cell '{cell_id}': {error}"))?;
    Ok((text, pruned))
}

fn cell_record(
    cell: &FluxCell,
    rebinned: &RebinResult,
    result: Box<serde_json::value::RawValue>,
    material_sha256: String,
) -> MeshCellRecord {
    MeshCellRecord {
        record: "cell",
        ordinal: cell.ordinal,
        id: cell.id.clone(),
        index: cell.index,
        bounds_cm: cell.bounds_cm,
        volume_cm3: cell.volume_cm3,
        material_sha256,
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
#[derive(Debug)]
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
    let read_error =
        |error: std::io::Error| format!("cannot read mesh output {}: {error}", output.display());
    // Stream the prefix line by line: a nearly complete large mesh output can be
    // many GB, and this pre-pass runs before any memory guard can see it.
    let mut reader = std::io::BufReader::new(File::open(output).map_err(read_error)?);
    let mut line = Vec::new();
    let header_len = reader.read_until(b'\n', &mut line).map_err(read_error)?;
    if header_len == 0 {
        return Ok(Some(ResumePrefix {
            offset: 0,
            cell_offsets: Vec::new(),
            pruned_states: Vec::new(),
        }));
    }
    if !line.ends_with(b"\n") {
        return Err("mesh output contains no complete header line".into());
    }
    if line != expected_header {
        return Err(
            "mesh output header does not match this spec's fingerprint; refusing to resume".into(),
        );
    }
    let mut position = header_len as u64;
    let mut offset = position;
    let mut cell_offsets: Vec<u64> = Vec::new();
    let mut pruned_states: Vec<u64> = Vec::new();
    loop {
        line.clear();
        let read = reader.read_until(b'\n', &mut line).map_err(read_error)?;
        if read == 0 {
            break;
        }
        let line_start = position;
        position += read as u64;
        if !line.ends_with(b"\n") {
            // Truncated tail from an interrupted write: resume after the
            // last complete record.
            break;
        }
        let at_end = reader.fill_buf().map_err(read_error)?.is_empty();
        let value: serde_json::Value = match serde_json::from_slice(&line[..line.len() - 1]) {
            Ok(value) => value,
            Err(_) if at_end => break,
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
                cell_offsets.push(line_start);
                pruned_states.push(pruned);
                offset = position;
            }
            Some("footer") => return Ok(None),
            _ => {
                return Err(format!(
                    "mesh output contains an unrecognized record before completion at cell {}",
                    cell_offsets.len()
                ))
            }
        }
    }
    Ok(Some(ResumePrefix {
        offset,
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
    // One buffered read instead of a syscall per byte; every call seeks first.
    let mut line = Vec::new();
    std::io::BufReader::new(&mut *file)
        .read_until(b'\n', &mut line)
        .map_err(|error| error.to_string())?;
    if line.last() == Some(&b'\n') {
        line.pop();
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
    let mut memo: HashMap<[u8; 32], (String, usize)> = HashMap::new();
    let mut memo_bytes = 0usize;
    let flux_channel = spec
        .uncertainty
        .as_ref()
        .is_some_and(|options| options.channels.iter().any(|channel| channel == "flux"));
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
        let cell_materials: Vec<&Material> = input_cells
            .iter()
            .map(|cell| {
                spec.materials
                    .as_ref()
                    .and_then(|m| m.get(&cell.id))
                    .unwrap_or(&spec.material)
            })
            .collect();
        let cell_material_sha: Vec<[u8; 32]> =
            cell_materials.iter().map(|m| material_sha256(m)).collect();
        let signatures: Vec<[u8; 32]> = rebinned
            .iter()
            .zip(input_cells.iter())
            .zip(cell_material_sha.iter())
            .map(|((value, cell), sha)| {
                // Errors join the signature only when the flux channel consumes them, so
                // memoization without the channel is exactly the pre-P93 behavior.
                let errors = cell.relative_error.as_deref().filter(|_| flux_channel);
                flux_signature(&value.flux_per_group, errors, sha)
            })
            .collect();
        let mut pending: HashMap<[u8; 32], Pending> = HashMap::new();
        let mut to_solve: Vec<usize> = Vec::new();
        let mut resolved: Vec<Option<(String, usize)>> =
            (0..input_cells.len()).map(|_| None).collect();
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
                                let pruned = *prefix_pruned
                                    .get(ordinal as usize)
                                    .ok_or("resumable prefix lacks a pruned-state record")?
                                    as usize;
                                resolved[index] =
                                    Some((read_prefix_result(file, offsets, ordinal)?, pruned));
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
                    let pruned = *prefix_pruned
                        .get(cell.ordinal as usize)
                        .ok_or("resumable prefix lacks a pruned-state record")?
                        as usize;
                    if memo_bytes + text.len() <= GROUPING_CACHE_BYTES {
                        memo_bytes += text.len();
                        memo.insert(signature, (text, pruned));
                    }
                }
                pending.insert(signature, Pending::Prefix(cell.ordinal));
                continue;
            }
            pending.insert(signature, Pending::Solve(index));
            to_solve.push(index);
        }
        let solved = solve_cells(
            spec,
            prepared,
            pool,
            source_groups,
            activation_boundaries,
            &rebinned,
            &cell_materials,
            &input_cells,
            &to_solve,
        );
        for (result, &index) in solved.into_iter().zip(to_solve.iter()) {
            let (text, pruned) = result?;
            if spec.group_workloads
                && memo.len() < GROUPING_CACHE_CAP
                && memo_bytes + text.len() <= GROUPING_CACHE_BYTES
            {
                memo_bytes += text.len();
                memo.insert(signatures[index], (text.clone(), pruned));
            }
            resolved[index] = Some((text, pruned));
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
            let (text, pruned) = resolved[index]
                .take()
                .expect("every unsolved cell resolves to a result string");
            let result = serde_json::value::RawValue::from_string(text)
                .map_err(|error| format!("cell '{}': {error}", cell.id))?;
            let record = cell_record(
                cell,
                &rebinned[index],
                result,
                hex32(&cell_material_sha[index]),
            );
            totals.add(&ledger, pruned);
            write_record(output, &record)?;
        }
        output.flush().map_err(|error| error.to_string())?;
        if let Some(limit) = spec.memory_limit_bytes {
            if let Some(peak) = peak_rss_bytes() {
                if peak > limit {
                    return Err(format!(
                        "memory_limit_bytes exceeded: peak RSS {peak} bytes > limit {limit} bytes{}",
                        if spec.resume {
                            ""
                        } else {
                            "; completed cells are kept only with \"resume\": true"
                        }
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
    // P94: the unit string only separates neutron from every other projectile, so the particle
    // label decides between them. `import-flux openmc` writes `source.metadata.particle` only
    // for a photon tally. A gamma run needs that label; a file carrying it serves only gamma.
    // Unlabelled `particles` files keep their pre-P94 meaning for proton, deuteron and alpha.
    let particle_label = source_header
        .source
        .metadata
        .get("particle")
        .and_then(serde_json::Value::as_str);
    let label_matches = match spec.projectile {
        Projectile::Gamma => particle_label == Some("photon"),
        _ => particle_label.is_none(),
    };
    if !label_matches {
        return Err(format!(
            "canonical flux particle label {} does not match {} projectile",
            particle_label.map_or_else(|| "(none)".to_string(), |label| format!("'{label}'")),
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
        spec.options.gas,
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
    #[test]
    fn spliced_result_text_equals_round_tripped_record_byte_for_byte() {
        #[derive(serde::Serialize)]
        struct Record<T> {
            record: &'static str,
            ordinal: u64,
            result: T,
        }
        let mut result = serde_json::Map::new();
        let floats = [
            -0.0,
            0.0,
            f64::MIN_POSITIVE / 3.0,
            5e-324,
            f64::MAX,
            -1.7976931348623157e308,
            0.1,
            1.0 / 3.0,
            2.0f64.powi(60) + 1.0,
            123456789.12345679,
            6.02214076e23,
            1e-17,
            f64::NAN,
            f64::INFINITY,
        ];
        result.insert("floats".into(), serde_json::json!(floats));
        result.insert(
            "u64".into(),
            serde_json::json!([u64::MAX, 0u64, 1u64 << 53]),
        );
        result.insert("i64".into(), serde_json::json!([i64::MIN, -1i64, i64::MAX]));
        result.insert(
            "strings".into(),
            serde_json::json!([
                "quote \" backslash \\ tab \t",
                "\u{1}\u{1f}",
                "é ☢ 𝔸",
                "</script>"
            ]),
        );
        result.insert(
            "nested".into(),
            serde_json::json!({"z": [1.5, {"b": -0.0, "a": null}], "a": true}),
        );
        let text = serde_json::to_string(&serde_json::Value::Object(result)).unwrap();
        let round_tripped = serde_json::to_string(&Record {
            record: "cell",
            ordinal: 7,
            result: serde_json::from_str::<serde_json::Value>(&text).unwrap(),
        })
        .unwrap();
        let spliced = serde_json::to_string(&Record {
            record: "cell",
            ordinal: 7,
            result: serde_json::value::RawValue::from_string(text).unwrap(),
        })
        .unwrap();
        assert_eq!(spliced, round_tripped);
    }

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
                overrides: None,
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
            materials: None,
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

    fn write_minimal_canonical_flux(path: &Path, flux_units: &str) -> String {
        // `run_mesh`'s projectile/particle-label check fires immediately after
        // `FluxStream::open`, which only parses and validates the header line; no
        // cell or footer record is needed to exercise it.
        let header = serde_json::json!({
            "record": "header",
            "schema": "actinv-flux-1",
            "source": {
                "format": "test-fixture",
                "path": "fixture",
                "sha256": "0".repeat(64),
            },
            "energy_boundaries_eV": [1.0, 2.0],
            "flux_units": flux_units,
            "cell_count": 1,
            "geometry": null,
            "energy_floor": null,
        });
        std::fs::write(path, format!("{}\n", header)).unwrap();
        sha256_file(path).unwrap()
    }

    fn fixture_temp_path(label: &str) -> PathBuf {
        static NEXT: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
        std::env::temp_dir().join(format!(
            "actinv-p94-mesh-fixture-{label}-{}-{}.ndjson",
            std::process::id(),
            NEXT.fetch_add(1, std::sync::atomic::Ordering::Relaxed)
        ))
    }

    #[test]
    fn gamma_mesh_run_rejects_legacy_unlabelled_neutron_flux() {
        let flux_path = fixture_temp_path("gamma-vs-neutron-flux");
        let sha256 = write_minimal_canonical_flux(&flux_path, "n cm^-2 s^-1");
        let mut spec = minimal_spec();
        spec.projectile = Projectile::Gamma;
        spec.options.temperature_K = 0.0;
        spec.flux = HashedFileRef {
            path: flux_path.display().to_string(),
            sha256,
        };
        let output = fixture_temp_path("gamma-vs-neutron-out");
        let error = run_mesh(&spec, &output).unwrap_err();
        assert!(
            error.contains("canonical flux units") && error.contains("gamma"),
            "{error}"
        );
        assert!(!output.exists());
        std::fs::remove_file(&flux_path).unwrap();
    }

    #[test]
    fn neutron_mesh_run_rejects_particle_labelled_flux() {
        let flux_path = fixture_temp_path("neutron-vs-particles-flux");
        let sha256 = write_minimal_canonical_flux(&flux_path, "particles cm^-2 s^-1");
        let mut spec = minimal_spec();
        spec.projectile = Projectile::Neutron;
        spec.flux = HashedFileRef {
            path: flux_path.display().to_string(),
            sha256,
        };
        let output = fixture_temp_path("neutron-vs-particles-out");
        let error = run_mesh(&spec, &output).unwrap_err();
        assert!(
            error.contains("canonical flux units") && error.contains("neutron"),
            "{error}"
        );
        assert!(!output.exists());
        std::fs::remove_file(&flux_path).unwrap();
    }

    fn write_particle_flux(path: &Path, particle: Option<&str>) -> String {
        let mut source = serde_json::json!({
            "format": "test-fixture",
            "path": "fixture",
            "sha256": "0".repeat(64),
        });
        if let Some(particle) = particle {
            source["metadata"] = serde_json::json!({ "particle": particle });
        }
        let header = serde_json::json!({
            "record": "header",
            "schema": "actinv-flux-1",
            "source": source,
            "energy_boundaries_eV": [1.0, 2.0],
            "flux_units": "particles cm^-2 s^-1",
            "cell_count": 1,
        });
        std::fs::write(path, format!("{}\n", header)).unwrap();
        sha256_file(path).unwrap()
    }

    fn particle_label_error(projectile: Projectile, particle: Option<&str>) -> String {
        let flux_path = fixture_temp_path("particle-label-flux");
        let sha256 = write_particle_flux(&flux_path, particle);
        let mut spec = minimal_spec();
        spec.projectile = projectile;
        spec.options.temperature_K = 0.0;
        spec.flux = HashedFileRef {
            path: flux_path.display().to_string(),
            sha256,
        };
        let output = fixture_temp_path("particle-label-out");
        let error = run_mesh(&spec, &output).unwrap_err();
        assert!(!output.exists());
        std::fs::remove_file(&flux_path).unwrap();
        error
    }

    #[test]
    fn gamma_mesh_run_rejects_unlabelled_particle_flux() {
        let error = particle_label_error(Projectile::Gamma, None);
        assert!(
            error.contains("particle label (none)") && error.contains("gamma"),
            "{error}"
        );
    }

    #[test]
    fn charged_mesh_run_rejects_photon_labelled_flux() {
        let error = particle_label_error(Projectile::Proton, Some("photon"));
        assert!(
            error.contains("particle label 'photon'") && error.contains("proton"),
            "{error}"
        );
    }

    #[test]
    fn resume_scan_streams_complete_records_and_stops_at_a_torn_tail() {
        let stamp = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let path = std::env::temp_dir().join(format!(
            "actinv-mesh-resume-{}-{stamp}.ndjson",
            std::process::id()
        ));
        let header = b"{\"record\":\"header\"}\n";
        let cell = |ordinal: u64| {
            format!(
                "{{\"record\":\"cell\",\"ordinal\":{ordinal},\"result\":{{\"pruned_states\":{}}}}}\n",
                ordinal + 3
            )
        };
        let scan = |bytes: &[u8]| {
            std::fs::write(&path, bytes).unwrap();
            resume_scan(&path, header)
        };
        let two = [header.as_slice(), cell(0).as_bytes(), cell(1).as_bytes()].concat();
        // Two complete records plus a torn tail: resume after the second.
        let prefix = scan(&[two.as_slice(), b"{\"record\":\"ce"].concat())
            .unwrap()
            .unwrap();
        assert_eq!(prefix.offset, two.len() as u64);
        assert_eq!(
            prefix.cell_offsets,
            vec![header.len() as u64, (header.len() + cell(0).len()) as u64]
        );
        assert_eq!(prefix.pruned_states, vec![3, 4]);
        // An unparseable final complete line is also a torn write.
        assert_eq!(
            scan(&[two.as_slice(), b"garbage\n"].concat())
                .unwrap()
                .unwrap()
                .offset,
            two.len() as u64
        );
        // ... but an unparseable record followed by more data is corruption.
        assert!(
            scan(&[header.as_slice(), b"garbage\n", cell(0).as_bytes()].concat())
                .unwrap_err()
                .contains("corrupt record")
        );
        // A footer means the run is complete; an empty file starts from zero.
        assert!(
            scan(&[two.as_slice(), b"{\"record\":\"footer\"}\n"].concat())
                .unwrap()
                .is_none()
        );
        assert_eq!(scan(b"").unwrap().unwrap().offset, 0);
        assert!(scan(b"{\"record\":\"other\"}\n")
            .unwrap_err()
            .contains("fingerprint"));
        std::fs::remove_file(&path).unwrap();
    }

    use std::collections::BTreeSet;

    /// A `StepOut` with every optional field unset and every collection
    /// empty — the opposite extreme from `maximal_step_out`, used to test
    /// the accessors' and the lean route's absence handling.
    fn minimal_step() -> StepOut {
        StepOut {
            step: 3,
            t_s: 1.5,
            flux: 2.5,
            flux_weighted_time_s: 0.25,
            fluence_n_cm2: None,
            fluence_particles_cm2: None,
            inventory: Vec::new(),
            activity_Bq_per_g: BTreeMap::new(),
            heat_W_per_g: Heat {
                total: 0.0,
                alpha: 0.0,
                beta: 0.0,
                gamma: 0.0,
            },
            leakage_atoms_per_g: 0.0,
            removed_atoms_per_g: None,
            negative_atoms_zeroed: 0.0,
            total_atoms_per_g: 0.0,
            n_states_populated: 0,
            numerical_floor_atoms_per_g: 0.0,
            n_states_below_floor: 0,
            atoms_below_floor: 0.0,
            heat_bound_from_below_floor_W_per_g: 0.0,
            photon_source: None,
            uncertainty: None,
            radiological: None,
            damage: None,
            gas: None,
        }
    }

    /// A `RunResult` with every optional field populated (one step from
    /// `maximal_step_out`, one from `minimal_step`, exercising both the
    /// present and the absent case for every `steps.*` optional field).
    fn populated_result() -> RunResult {
        RunResult {
            spec_title: "t".into(),
            entry_point: "run".into(),
            projectile: Some("triton".into()),
            mode: "auto".into(),
            pruned_states: 5,
            total_states: 9,
            steps: vec![maximal_step_out(), minimal_step()],
            pathways: vec![BTreeMap::new()],
            screen: Some(serde_json::json!({"ok": true})),
            pathway_closure: 0.01,
            isomer_pathway_shares: Some(Vec::new()),
            ledger: serde_json::json!({"k": 1}),
            certificate: serde_json::json!({"c": 2}),
            ms: 12.5,
        }
    }

    /// A `RunResult` with every optional field unset — the opposite extreme.
    fn minimal_result() -> RunResult {
        RunResult {
            spec_title: String::new(),
            entry_point: String::new(),
            projectile: None,
            mode: String::new(),
            pruned_states: 0,
            total_states: 0,
            steps: Vec::new(),
            pathways: Vec::new(),
            screen: None,
            pathway_closure: 0.0,
            isomer_pathway_shares: None,
            ledger: serde_json::Value::Null,
            certificate: serde_json::Value::Null,
            ms: 0.0,
        }
    }

    #[test]
    fn run_result_field_matches_the_value_route_for_every_name() {
        for result in [populated_result(), minimal_result()] {
            let whole = serde_json::to_value(&result).unwrap();
            for &name in RUN_RESULT_ACCESSOR_FIELDS {
                let expected = whole.get(name).cloned().unwrap_or(serde_json::Value::Null);
                let actual = run_result_field(&result, name).unwrap().unwrap();
                assert_eq!(actual, expected, "field {name}");
            }
        }
        assert!(run_result_field(&populated_result(), "not-a-field").is_none());
    }

    #[test]
    fn step_field_matches_the_value_route_for_every_name() {
        for step in [maximal_step_out(), minimal_step()] {
            let whole = serde_json::to_value(&step).unwrap();
            for &name in STEP_FIELDS {
                let expected = whole.get(name).cloned().unwrap_or(serde_json::Value::Null);
                let actual = step_field(&step, name).unwrap().unwrap();
                assert_eq!(actual, expected, "field {name}");
            }
        }
        assert!(step_field(&maximal_step_out(), "not-a-field").is_none());
    }

    #[test]
    fn run_result_accessor_fields_cover_every_key_a_populated_result_serializes() {
        let whole = serde_json::to_value(populated_result()).unwrap();
        let keys: BTreeSet<&str> = whole
            .as_object()
            .unwrap()
            .keys()
            .map(String::as_str)
            .collect();
        let names: BTreeSet<&str> = RUN_RESULT_ACCESSOR_FIELDS.iter().copied().collect();
        assert_eq!(names, keys);
    }

    #[test]
    fn step_field_names_cover_every_key_a_populated_step_out_serializes() {
        let whole = serde_json::to_value(maximal_step_out()).unwrap();
        let keys: BTreeSet<&str> = whole
            .as_object()
            .unwrap()
            .keys()
            .map(String::as_str)
            .collect();
        let names: BTreeSet<&str> = STEP_FIELDS.iter().copied().collect();
        assert_eq!(names, keys);
    }

    #[test]
    fn cell_result_fields_rejects_a_non_step_field_dotted_path() {
        let mut spec = minimal_spec();
        spec.cell_result_fields = Some(vec!["steps.not_a_field".into()]);
        assert!(spec.validate().unwrap_err().contains("not_a_field"));
    }

    #[test]
    fn cell_result_fields_rejects_a_path_past_a_scalar() {
        let mut spec = minimal_spec();
        spec.cell_result_fields = Some(vec!["steps.t_s.extra".into()]);
        assert!(spec.validate().unwrap_err().contains("non-object"));
    }

    #[test]
    fn cell_result_fields_rejects_an_unknown_key_under_an_object_field() {
        let mut spec = minimal_spec();
        spec.cell_result_fields = Some(vec!["steps.photon_source.not_a_key".into()]);
        assert!(spec.validate().unwrap_err().contains("not_a_key"));
    }

    #[test]
    fn cell_result_fields_accepts_a_dynamic_map_field_whole_but_not_a_key_inside_it() {
        // `activity_Bq_per_g` is a nuclide -> f64 map: its keys are data, not part of the
        // static shape, so only the whole field is a valid selection.
        let mut spec = minimal_spec();
        spec.cell_result_fields = Some(vec!["steps.activity_Bq_per_g".into()]);
        assert!(spec.validate().is_ok());
        spec.cell_result_fields = Some(vec!["steps.activity_Bq_per_g.FE56".into()]);
        assert!(spec.validate().is_err());
    }

    #[test]
    fn cell_result_fields_rejects_plain_steps_combined_with_a_dotted_entry() {
        let mut spec = minimal_spec();
        spec.cell_result_fields = Some(vec!["steps".into(), "steps.t_s".into()]);
        assert!(spec.validate().unwrap_err().contains("cannot combine"));
        spec.cell_result_fields = Some(vec!["steps.t_s".into()]);
        assert!(spec.validate().is_ok());
        spec.cell_result_fields = Some(vec!["steps".into()]);
        assert!(spec.validate().is_ok());
    }

    #[test]
    fn cell_result_fields_accepts_the_g3_selection() {
        let mut spec = minimal_spec();
        spec.cell_result_fields = Some(vec![
            "mode".into(),
            "ledger".into(),
            "pruned_states".into(),
            "steps.photon_source.groups".into(),
            "steps.heat_W_per_g".into(),
            "steps.t_s".into(),
        ]);
        assert!(spec.validate().is_ok());
    }

    #[test]
    fn lean_cell_text_always_excludes_ms_even_if_selected() {
        let result = populated_result();
        let text = lean_cell_text(&result, &["ms", "mode"], &[vec!["t_s"]]).unwrap();
        let value: serde_json::Value = serde_json::from_str(&text).unwrap();
        assert!(!value.as_object().unwrap().contains_key("ms"));
        assert!(value.as_object().unwrap().contains_key("mode"));
    }

    #[test]
    fn lean_text_matches_the_pruned_subtree_of_the_full_value_route() {
        // The full route: the whole `RunResult` as a `Value`, minus `ms` (as
        // `result_without_timing` does for the unchanged route).
        let result = populated_result();
        let mut full = serde_json::to_value(&result).unwrap();
        full.as_object_mut().unwrap().remove("ms");

        let top_fields = ["mode", "ledger", "pruned_states"];
        let step_paths: Vec<Vec<&str>> = vec![
            vec!["photon_source", "groups"],
            vec!["heat_W_per_g"],
            vec!["t_s"],
        ];
        let lean_text = lean_cell_text(&result, &top_fields, &step_paths).unwrap();
        let lean: serde_json::Value = serde_json::from_str(&lean_text).unwrap();

        let full_steps = full["steps"].as_array().unwrap();
        let mut expected_steps = Vec::new();
        for step in full_steps {
            let mut expected = serde_json::Map::new();
            expected.insert("step".into(), step["step"].clone());
            if let Some(photon) = step.get("photon_source") {
                let mut pruned_photon = serde_json::Map::new();
                pruned_photon.insert("groups".into(), photon["groups"].clone());
                expected.insert(
                    "photon_source".into(),
                    serde_json::Value::Object(pruned_photon),
                );
            }
            expected.insert("heat_W_per_g".into(), step["heat_W_per_g"].clone());
            expected.insert("t_s".into(), step["t_s"].clone());
            expected_steps.push(serde_json::Value::Object(expected));
        }
        let mut expected_top = serde_json::Map::new();
        expected_top.insert("mode".into(), full["mode"].clone());
        expected_top.insert("ledger".into(), full["ledger"].clone());
        expected_top.insert("pruned_states".into(), full["pruned_states"].clone());
        expected_top.insert("steps".into(), serde_json::Value::Array(expected_steps));

        assert_eq!(lean, serde_json::Value::Object(expected_top));
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

    #[test]
    fn flux_signature_without_relative_error_matches_the_pre_p93_digest() {
        // The dedup signature must be byte-identical to its pre-flux-channel
        // form whenever `relative_error` is not passed (i.e. whenever the mesh
        // spec did not request the flux channel), so memoization for every
        // existing mesh run is completely unaffected by P93.
        let flux = [0.0_f64, 1.25, -3.5, f64::MIN_POSITIVE, 6.02214076e23];
        let material_sha: [u8; 32] = Sha256::digest(b"fixture material").into();
        let mut expected = Sha256::new();
        expected.update(material_sha);
        for value in &flux {
            expected.update(value.to_le_bytes());
        }
        let expected: [u8; 32] = expected.finalize().into();
        assert_eq!(flux_signature(&flux, None, &material_sha), expected);
        // Passing errors changes the digest (this is the P93 extension).
        let errors = [0.0_f64, 0.05, 0.0, 0.1, 0.0];
        assert_ne!(
            flux_signature(&flux, Some(&errors), &material_sha),
            expected
        );
    }
}
