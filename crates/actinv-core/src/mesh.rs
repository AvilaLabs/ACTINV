#![allow(non_snake_case)] // JSON wire names carry their physical units.
//! Deterministic, bounded-memory independent-cell activation runner.

use crate::flux::{
    atomic_output, rebin_equal_lethargy, sha256_file, FluxCell, FluxGeometry, FluxSource,
    FluxStream,
};
use crate::run::{PreparedRun, RunResult};
use crate::spec::{
    DecayRef, FissionYieldOptions, HashedFileRef, LibraryRef, Material, Options, PhotonOptions,
    Projectile, Spec, Spectrum, Step,
};
use rayon::prelude::*;
use serde::{Deserialize, Serialize};
use std::fs::File;
use std::io::{BufWriter, Write};
use std::path::{Path, PathBuf};

const MESH_SPEC_SCHEMA: &str = "actinv-mesh-spec-1";
const MESH_RESULT_SCHEMA: &str = "actinv-mesh-result-1";
const MAX_CHUNK_CELLS: usize = 65_536;
const MAX_THREADS: usize = 256;

fn default_chunk_cells() -> usize {
    64
}

fn default_threads() -> usize {
    1
}

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
    #[serde(default = "default_chunk_cells")]
    pub chunk_cells: usize,
    #[serde(default = "default_threads")]
    pub threads: usize,
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

        // Reuse the ordinary-spec validator for every shared field. The placeholder spectrum
        // is valid by construction and is replaced with each rebinned cell before execution.
        self.cell_spec(vec![1.0, 2.0], vec![0.0]).validate()
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
        }
    }
}

#[derive(Debug, Serialize)]
struct MeshHeader {
    record: &'static str,
    schema: &'static str,
    spec_title: String,
    cell_count: u64,
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
    fn add(&mut self, cell: &MeshCellRecord) -> Result<(), String> {
        self.cells += 1;
        self.source.add(cell.rebin.source_total);
        self.destination.add(cell.rebin.destination_total);
        self.underflow.add(cell.rebin.underflow);
        self.overflow.add(cell.rebin.overflow);
        self.max_closure = self.max_closure.max(cell.rebin.relative_closure);
        let pruned = cell.result["pruned_states"]
            .as_u64()
            .ok_or("ordinary solver result has no numeric pruned_states")?
            as usize;
        self.min_pruned = Some(self.min_pruned.map_or(pruned, |value| value.min(pruned)));
        self.max_pruned = self.max_pruned.max(pruned);
        Ok(())
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

fn solve_cell(
    mesh_spec: &MeshSpec,
    prepared: &PreparedRun,
    source_boundaries: &[f64],
    activation_boundaries: &[f64],
    cell: &FluxCell,
) -> Result<MeshCellRecord, String> {
    let rebinned = rebin_equal_lethargy(
        source_boundaries,
        &cell.flux_per_group,
        activation_boundaries,
    )
    .map_err(|error| format!("cell '{}': {error}", cell.id))?;
    let spec = mesh_spec.cell_spec(
        activation_boundaries.to_vec(),
        rebinned.flux_per_group.clone(),
    );
    spec.validate()
        .map_err(|error| format!("cell '{}': {error}", cell.id))?;
    let result = prepared
        .run(&spec, "mesh")
        .map_err(|error| format!("cell '{}': {error}", cell.id))?;
    let result =
        result_without_timing(result).map_err(|error| format!("cell '{}': {error}", cell.id))?;
    Ok(MeshCellRecord {
        record: "cell",
        ordinal: cell.ordinal,
        id: cell.id.clone(),
        index: cell.index,
        bounds_cm: cell.bounds_cm,
        volume_cm3: cell.volume_cm3,
        source_relative_error: cell.relative_error.clone(),
        rebin: RebinLedger {
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
        },
        result,
    })
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

    let mut stream = FluxStream::open(&spec.flux.path)?;
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
    let prepared = PreparedRun::prepare_inputs(
        &spec.library,
        &spec.decay,
        &spec.photon,
        &spec.fission_yields,
        spec.projectile,
        spec.options.temperature_K,
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

    let mut final_wall_time = 0.0;
    let mut final_rate = 0.0;
    atomic_output(output, |mut output| {
        write_record(&mut output, &header)?;
        let mut totals = MeshTotals::default();
        loop {
            let input_cells = stream.read_chunk(spec.chunk_cells)?;
            if input_cells.is_empty() {
                break;
            }
            let attempts: Vec<Result<MeshCellRecord, String>> = pool.install(|| {
                input_cells
                    .par_iter()
                    .map(|cell| {
                        solve_cell(
                            spec,
                            &prepared,
                            &source_header.energy_boundaries_eV,
                            &activation_boundaries,
                            cell,
                        )
                    })
                    .collect()
            });
            for attempt in attempts {
                let cell = attempt?;
                totals.add(&cell)?;
                write_record(&mut output, &cell)?;
            }
        }
        let source_footer = stream.finish()?;
        if totals.cells != source_header.cell_count {
            return Err(format!(
                "mesh processed {} cells; canonical header declares {}",
                totals.cells, source_header.cell_count
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
        final_wall_time = started.elapsed().as_secs_f64();
        final_rate = totals.cells as f64 / final_wall_time.max(f64::MIN_POSITIVE);
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
            wall_time_s: final_wall_time,
            cells_per_s: final_rate,
        };
        write_record(&mut output, &footer)?;
        output.flush().map_err(|error| error.to_string())
    })?;

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
            }],
            options: Options::default(),
            photon: PhotonOptions::default(),
            fission_yields: FissionYieldOptions::default(),
            chunk_cells: 1,
            threads: 1,
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
