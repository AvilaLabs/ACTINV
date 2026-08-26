#![allow(non_snake_case)] // JSON wire names carry their physical units (energy_boundaries_eV, volume_cm3)
//! P8 flux interchange, source readers and conservative group conversion.
//!
//! `actinv-flux-1` is newline-delimited JSON: one header, ordered cells, one footer.  The
//! reader and writer both validate the stream so a truncated file cannot be mistaken for a
//! complete transport-to-activation handoff.

use hdf5_pure::{
    AttrValue, ChunkCacheConfig, File as Hdf5File, FileAccessProperties, MetadataCacheConfig,
};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, HashSet};
use std::fs::{File, OpenOptions};
use std::io::{BufRead, BufReader, BufWriter, Read, Write};
use std::path::{Path, PathBuf};
use std::time::UNIX_EPOCH;

const FLUX_SCHEMA: &str = "actinv-flux-1";
const FLUX_UNITS: &str = "n cm^-2 s^-1";
const PARTICLE_FLUX_UNITS: &str = "particles cm^-2 s^-1";

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct FileProvenance {
    pub path: String,
    pub sha256: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct FluxSource {
    pub format: String,
    pub path: String,
    pub sha256: String,
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub selector: BTreeMap<String, serde_json::Value>,
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub normalization: BTreeMap<String, serde_json::Value>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub auxiliary_inputs: Vec<FileProvenance>,
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub metadata: BTreeMap<String, serde_json::Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct FluxGeometry {
    pub kind: String,
    pub dimension: [usize; 3],
    pub axis_boundaries_cm: [Vec<f64>; 3],
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct EnergyFloor {
    pub original_lower_eV: f64,
    pub substituted_lower_eV: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct FluxHeader {
    pub record: String,
    pub schema: String,
    pub source: FluxSource,
    pub energy_boundaries_eV: Vec<f64>,
    pub flux_units: String,
    pub cell_count: u64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub geometry: Option<FluxGeometry>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub energy_floor: Option<EnergyFloor>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct FluxCell {
    pub record: String,
    pub ordinal: u64,
    pub id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub index: Option<[usize; 3]>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub bounds_cm: Option<[[f64; 2]; 3]>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub volume_cm3: Option<f64>,
    pub flux_per_group: Vec<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub relative_error: Option<Vec<f64>>,
    pub flux_total: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct FluxFooter {
    pub record: String,
    pub cell_count: u64,
    pub flux_sum_over_cells: f64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub volume_integrated_flux: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub source_total_checks_max_relative_error: Option<f64>,
}

#[derive(Debug, Clone, Serialize)]
pub struct ImportSummary {
    pub output: String,
    pub sha256: String,
    pub source_sha256: String,
    pub cells: u64,
    pub groups: usize,
    pub flux_sum_over_cells: f64,
}

#[derive(Debug, Clone, Serialize)]
pub struct RebinResult {
    pub flux_per_group: Vec<f64>,
    pub underflow: f64,
    pub overflow: f64,
    pub source_total: f64,
    pub destination_total: f64,
    pub relative_closure: f64,
    pub exact_grid: bool,
}

#[derive(Clone, Copy, Debug, Default)]
struct Neumaier {
    sum: f64,
    correction: f64,
}

impl Neumaier {
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

fn compensated(values: &[f64]) -> f64 {
    let mut sum = Neumaier::default();
    for &value in values {
        sum.add(value);
    }
    sum.total()
}

fn relative_difference(a: f64, b: f64) -> f64 {
    (a - b).abs() / a.abs().max(b.abs()).max(f64::MIN_POSITIVE)
}

fn valid_hash(value: &str) -> bool {
    value.len() == 64 && value.bytes().all(|byte| byte.is_ascii_hexdigit())
}

fn validate_boundaries(boundaries: &[f64]) -> Result<(), String> {
    if boundaries.len() < 2
        || boundaries
            .iter()
            .any(|value| !value.is_finite() || *value <= 0.0)
        || boundaries.windows(2).any(|pair| pair[1] <= pair[0])
    {
        return Err("energy boundaries must be finite, positive and strictly increasing".into());
    }
    Ok(())
}

fn validate_geometry(geometry: &FluxGeometry) -> Result<(), String> {
    if !matches!(geometry.kind.as_str(), "regular" | "rectilinear") {
        return Err(format!(
            "unsupported canonical geometry kind '{}'",
            geometry.kind
        ));
    }
    for axis in 0..3 {
        let values = &geometry.axis_boundaries_cm[axis];
        if geometry.dimension[axis] == 0 || values.len() != geometry.dimension[axis] + 1 {
            return Err(format!(
                "geometry axis {axis} has {} boundaries for dimension {}",
                values.len(),
                geometry.dimension[axis]
            ));
        }
        if values.iter().any(|value| !value.is_finite())
            || values.windows(2).any(|pair| pair[1] <= pair[0])
        {
            return Err(format!(
                "geometry axis {axis} boundaries are not finite and increasing"
            ));
        }
    }
    let cells = geometry
        .dimension
        .iter()
        .try_fold(1u64, |n, value| n.checked_mul(*value as u64))
        .ok_or("geometry cell count overflows u64")?;
    if cells == 0 {
        return Err("geometry has no cells".into());
    }
    Ok(())
}

impl FluxHeader {
    pub fn validate(&self) -> Result<(), String> {
        if self.record != "header" || self.schema != FLUX_SCHEMA {
            return Err("canonical flux must begin with an actinv-flux-1 header".into());
        }
        if self.flux_units != FLUX_UNITS && self.flux_units != PARTICLE_FLUX_UNITS {
            return Err(format!(
                "unsupported canonical flux units '{}'",
                self.flux_units
            ));
        }
        if self.cell_count == 0 {
            return Err("canonical flux declares zero cells".into());
        }
        validate_boundaries(&self.energy_boundaries_eV)?;
        if self.source.format.is_empty()
            || self.source.path.is_empty()
            || !valid_hash(&self.source.sha256)
        {
            return Err("canonical source needs format, path and a 64-digit SHA-256".into());
        }
        if self
            .source
            .auxiliary_inputs
            .iter()
            .any(|item| item.path.is_empty() || !valid_hash(&item.sha256))
        {
            return Err("canonical auxiliary input needs path and a 64-digit SHA-256".into());
        }
        if let Some(geometry) = &self.geometry {
            validate_geometry(geometry)?;
            let count = geometry.dimension.iter().product::<usize>() as u64;
            if count != self.cell_count {
                return Err(format!(
                    "geometry contains {count} cells but header declares {}",
                    self.cell_count
                ));
            }
        }
        if let Some(floor) = &self.energy_floor {
            if floor.original_lower_eV != 0.0
                || !floor.substituted_lower_eV.is_finite()
                || floor.substituted_lower_eV <= 0.0
                || floor.substituted_lower_eV != self.energy_boundaries_eV[0]
            {
                return Err("invalid canonical zero-energy floor record".into());
            }
        }
        Ok(())
    }
}

fn validate_cell(header: &FluxHeader, cell: &FluxCell, ordinal: u64) -> Result<(), String> {
    if cell.record != "cell" || cell.ordinal != ordinal || cell.id.is_empty() {
        return Err(format!(
            "invalid or out-of-order canonical cell at ordinal {ordinal}"
        ));
    }
    let groups = header.energy_boundaries_eV.len() - 1;
    if cell.flux_per_group.len() != groups {
        return Err(format!(
            "cell {} has {} flux values; header requires {groups}",
            cell.id,
            cell.flux_per_group.len()
        ));
    }
    if cell
        .flux_per_group
        .iter()
        .any(|value| !value.is_finite() || *value < 0.0)
    {
        return Err(format!("cell {} has nonfinite or negative flux", cell.id));
    }
    if let Some(errors) = &cell.relative_error {
        if errors.len() != groups
            || errors
                .iter()
                .any(|value| !value.is_finite() || *value < 0.0)
        {
            return Err(format!("cell {} has invalid relative errors", cell.id));
        }
    }
    if cell
        .volume_cm3
        .is_some_and(|value| !value.is_finite() || value <= 0.0)
    {
        return Err(format!("cell {} has invalid volume", cell.id));
    }
    if let Some(index) = cell.index {
        if index.contains(&0) {
            return Err(format!("cell {} has a zero spatial index", cell.id));
        }
        if let Some(geometry) = &header.geometry {
            if (0..3).any(|axis| index[axis] > geometry.dimension[axis]) {
                return Err(format!("cell {} index lies outside the geometry", cell.id));
            }
        }
    }
    if let Some(bounds) = cell.bounds_cm {
        if bounds.iter().flatten().any(|value| !value.is_finite())
            || bounds.iter().any(|pair| pair[1] <= pair[0])
        {
            return Err(format!("cell {} has invalid spatial bounds", cell.id));
        }
    }
    let total = compensated(&cell.flux_per_group);
    if !cell.flux_total.is_finite() || relative_difference(total, cell.flux_total) > 1e-12 {
        return Err(format!(
            "cell {} flux_total does not equal its group sum: {} vs {}",
            cell.id, cell.flux_total, total
        ));
    }
    Ok(())
}

struct CanonicalWriter<W: Write> {
    output: W,
    header: FluxHeader,
    next_ordinal: u64,
    sum: Neumaier,
    volume_sum: Neumaier,
    all_volumes: bool,
    seen_ids: HashSet<String>,
}

impl<W: Write> CanonicalWriter<W> {
    fn new(mut output: W, header: FluxHeader) -> Result<Self, String> {
        header.validate()?;
        serde_json::to_writer(&mut output, &header).map_err(|error| error.to_string())?;
        output.write_all(b"\n").map_err(|error| error.to_string())?;
        Ok(Self {
            output,
            header,
            next_ordinal: 0,
            sum: Neumaier::default(),
            volume_sum: Neumaier::default(),
            all_volumes: true,
            seen_ids: HashSet::new(),
        })
    }

    fn write_cell(&mut self, mut cell: FluxCell) -> Result<(), String> {
        cell.record = "cell".into();
        cell.ordinal = self.next_ordinal;
        cell.flux_total = compensated(&cell.flux_per_group);
        validate_cell(&self.header, &cell, self.next_ordinal)?;
        if !self.seen_ids.insert(cell.id.clone()) {
            return Err(format!("duplicate canonical cell ID '{}'", cell.id));
        }
        self.sum.add(cell.flux_total);
        match cell.volume_cm3 {
            Some(volume) => self.volume_sum.add(cell.flux_total * volume),
            None => self.all_volumes = false,
        }
        serde_json::to_writer(&mut self.output, &cell).map_err(|error| error.to_string())?;
        self.output
            .write_all(b"\n")
            .map_err(|error| error.to_string())?;
        self.next_ordinal += 1;
        Ok(())
    }

    fn finish(
        mut self,
        source_total_checks_max_relative_error: Option<f64>,
    ) -> Result<FluxFooter, String> {
        if self.next_ordinal != self.header.cell_count {
            return Err(format!(
                "wrote {} canonical cells; header declares {}",
                self.next_ordinal, self.header.cell_count
            ));
        }
        if source_total_checks_max_relative_error
            .is_some_and(|value| !value.is_finite() || value < 0.0)
        {
            return Err("invalid source total check".into());
        }
        let footer = FluxFooter {
            record: "footer".into(),
            cell_count: self.next_ordinal,
            flux_sum_over_cells: self.sum.total(),
            volume_integrated_flux: self.all_volumes.then(|| self.volume_sum.total()),
            source_total_checks_max_relative_error,
        };
        serde_json::to_writer(&mut self.output, &footer).map_err(|error| error.to_string())?;
        self.output
            .write_all(b"\n")
            .map_err(|error| error.to_string())?;
        self.output.flush().map_err(|error| error.to_string())?;
        Ok(footer)
    }
}

#[derive(Debug, Clone)]
struct FileSnapshot {
    length: u64,
    modified_ns: u128,
    sha256: String,
}

fn metadata_signature(path: &Path) -> Result<(u64, u128), String> {
    let metadata = std::fs::metadata(path)
        .map_err(|error| format!("cannot stat {}: {error}", path.display()))?;
    let modified_ns = metadata
        .modified()
        .ok()
        .and_then(|time| time.duration_since(UNIX_EPOCH).ok())
        .map_or(0, |duration| duration.as_nanos());
    Ok((metadata.len(), modified_ns))
}

fn hash_snapshot(path: &Path) -> Result<FileSnapshot, String> {
    let (length, modified_ns) = metadata_signature(path)?;
    let mut file =
        File::open(path).map_err(|error| format!("cannot hash {}: {error}", path.display()))?;
    let mut hasher = Sha256::new();
    let mut buffer = [0u8; 1024 * 1024];
    loop {
        let count = file
            .read(&mut buffer)
            .map_err(|error| format!("cannot hash {}: {error}", path.display()))?;
        if count == 0 {
            break;
        }
        hasher.update(&buffer[..count]);
    }
    let snapshot = FileSnapshot {
        length,
        modified_ns,
        sha256: format!("{:x}", hasher.finalize()),
    };
    ensure_unchanged(path, &snapshot)?;
    Ok(snapshot)
}

fn ensure_unchanged(path: &Path, snapshot: &FileSnapshot) -> Result<(), String> {
    let (length, modified_ns) = metadata_signature(path)?;
    if length != snapshot.length || modified_ns != snapshot.modified_ns {
        return Err(format!("input changed while importing: {}", path.display()));
    }
    Ok(())
}

pub fn sha256_file(path: impl AsRef<Path>) -> Result<String, String> {
    Ok(hash_snapshot(path.as_ref())?.sha256)
}

fn temporary_sibling(output: &Path) -> Result<PathBuf, String> {
    let name = output
        .file_name()
        .and_then(|value| value.to_str())
        .ok_or_else(|| format!("invalid output path {}", output.display()))?;
    let parent = output.parent().unwrap_or_else(|| Path::new("."));
    for sequence in 0..1000u32 {
        let candidate = parent.join(format!(
            ".{name}.actinv-{}-{sequence}.tmp",
            std::process::id()
        ));
        if !candidate.exists() {
            return Ok(candidate);
        }
    }
    Err(format!(
        "cannot allocate a temporary sibling for {}",
        output.display()
    ))
}

pub(crate) fn atomic_output<T>(
    output: &Path,
    operation: impl FnOnce(BufWriter<File>) -> Result<T, String>,
) -> Result<T, String> {
    let temporary = temporary_sibling(output)?;
    let file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&temporary)
        .map_err(|error| format!("cannot create {}: {error}", temporary.display()))?;
    let result = operation(BufWriter::new(file));
    match result {
        Ok(value) => {
            std::fs::rename(&temporary, output).map_err(|error| {
                let _ = std::fs::remove_file(&temporary);
                format!("cannot publish {}: {error}", output.display())
            })?;
            Ok(value)
        }
        Err(error) => {
            let _ = std::fs::remove_file(&temporary);
            Err(error)
        }
    }
}

fn apply_energy_floor(
    mut boundaries: Vec<f64>,
    energy_floor_eV: Option<f64>,
) -> Result<(Vec<f64>, Option<EnergyFloor>), String> {
    if boundaries.len() < 2 {
        return Err("source energy grid has fewer than two boundaries".into());
    }
    if boundaries[0] == 0.0 {
        let floor = energy_floor_eV.ok_or(
            "source energy grid begins at zero; supply an explicit positive --energy-floor-eV",
        )?;
        if !floor.is_finite() || floor <= 0.0 || floor >= boundaries[1] {
            return Err("energy floor must be finite, positive and below the next boundary".into());
        }
        boundaries[0] = floor;
        validate_boundaries(&boundaries)?;
        return Ok((
            boundaries,
            Some(EnergyFloor {
                original_lower_eV: 0.0,
                substituted_lower_eV: floor,
            }),
        ));
    }
    if energy_floor_eV.is_some() {
        return Err(
            "--energy-floor-eV is only valid when the source lower boundary is zero".into(),
        );
    }
    validate_boundaries(&boundaries)?;
    Ok((boundaries, None))
}

pub fn rebin_equal_lethargy(
    source_boundaries: &[f64],
    source_flux: &[f64],
    destination_boundaries: &[f64],
) -> Result<RebinResult, String> {
    validate_boundaries(source_boundaries)?;
    validate_boundaries(destination_boundaries)?;
    if source_boundaries.len() != source_flux.len() + 1 {
        return Err("source group count does not match its boundaries".into());
    }
    if source_flux
        .iter()
        .any(|value| !value.is_finite() || *value < 0.0)
    {
        return Err("source flux contains a nonfinite or negative value".into());
    }
    if source_boundaries == destination_boundaries {
        let total = compensated(source_flux);
        return Ok(RebinResult {
            flux_per_group: source_flux.to_vec(),
            underflow: 0.0,
            overflow: 0.0,
            source_total: total,
            destination_total: total,
            relative_closure: 0.0,
            exact_grid: true,
        });
    }

    let mut destination = vec![Neumaier::default(); destination_boundaries.len() - 1];
    let mut underflow = Neumaier::default();
    let mut overflow = Neumaier::default();
    let target_low = destination_boundaries[0];
    let target_high = *destination_boundaries.last().expect("validated boundaries");

    for (group, &flux) in source_flux.iter().enumerate() {
        if flux == 0.0 {
            continue;
        }
        let low = source_boundaries[group];
        let high = source_boundaries[group + 1];
        let width = (high / low).ln();
        if low < target_low {
            let overlap_high = high.min(target_low);
            if overlap_high > low {
                underflow.add(flux * (overlap_high / low).ln() / width);
            }
        }
        if high > target_high {
            let overlap_low = low.max(target_high);
            if high > overlap_low {
                overflow.add(flux * (high / overlap_low).ln() / width);
            }
        }
        let mut destination_group = destination_boundaries.partition_point(|value| *value <= low);
        destination_group = destination_group.saturating_sub(1);
        while destination_group < destination.len() {
            let overlap_low = low.max(destination_boundaries[destination_group]);
            let overlap_high = high.min(destination_boundaries[destination_group + 1]);
            if overlap_high > overlap_low {
                destination[destination_group]
                    .add(flux * (overlap_high / overlap_low).ln() / width);
            }
            if destination_boundaries[destination_group + 1] >= high {
                break;
            }
            destination_group += 1;
        }
    }

    let flux_per_group: Vec<f64> = destination.into_iter().map(Neumaier::total).collect();
    let source_total = compensated(source_flux);
    let destination_total = compensated(&flux_per_group);
    let underflow = underflow.total();
    let overflow = overflow.total();
    let accounted = destination_total + underflow + overflow;
    let relative_closure = relative_difference(source_total, accounted);
    if relative_closure > 1e-12 {
        return Err(format!(
            "equal-lethargy rebin failed conservation: source {source_total}, accounted {accounted}, relative {relative_closure}"
        ));
    }
    Ok(RebinResult {
        flux_per_group,
        underflow,
        overflow,
        source_total,
        destination_total,
        relative_closure,
        exact_grid: false,
    })
}

pub struct FluxStream {
    reader: BufReader<File>,
    pub header: FluxHeader,
    next_ordinal: u64,
    sum: Neumaier,
    volume_sum: Neumaier,
    all_volumes: bool,
    footer: Option<FluxFooter>,
    line_number: usize,
    seen_ids: HashSet<String>,
}

impl FluxStream {
    pub fn open(path: impl AsRef<Path>) -> Result<Self, String> {
        let path = path.as_ref();
        let file = File::open(path)
            .map_err(|error| format!("cannot open canonical flux {}: {error}", path.display()))?;
        let mut reader = BufReader::new(file);
        let mut line = String::new();
        if reader
            .read_line(&mut line)
            .map_err(|error| error.to_string())?
            == 0
        {
            return Err("canonical flux is empty".into());
        }
        let header: FluxHeader = serde_json::from_str(line.trim_end())
            .map_err(|error| format!("canonical header: {error}"))?;
        header.validate()?;
        Ok(Self {
            reader,
            header,
            next_ordinal: 0,
            sum: Neumaier::default(),
            volume_sum: Neumaier::default(),
            all_volumes: true,
            footer: None,
            line_number: 1,
            seen_ids: HashSet::new(),
        })
    }

    pub fn read_chunk(&mut self, limit: usize) -> Result<Vec<FluxCell>, String> {
        if limit == 0 {
            return Err("canonical chunk size must be positive".into());
        }
        if self.footer.is_some() {
            return Ok(Vec::new());
        }
        let mut cells = Vec::with_capacity(limit);
        while cells.len() < limit {
            let mut line = String::new();
            let bytes = self
                .reader
                .read_line(&mut line)
                .map_err(|error| error.to_string())?;
            if bytes == 0 {
                return Err("canonical flux ended before its footer".into());
            }
            self.line_number += 1;
            if line.trim().is_empty() {
                return Err(format!(
                    "blank canonical record at line {}",
                    self.line_number
                ));
            }
            let value: serde_json::Value = serde_json::from_str(line.trim_end())
                .map_err(|error| format!("canonical line {}: {error}", self.line_number))?;
            match value.get("record").and_then(serde_json::Value::as_str) {
                Some("cell") => {
                    let cell: FluxCell = serde_json::from_value(value).map_err(|error| {
                        format!("canonical cell line {}: {error}", self.line_number)
                    })?;
                    validate_cell(&self.header, &cell, self.next_ordinal)?;
                    if !self.seen_ids.insert(cell.id.clone()) {
                        return Err(format!("duplicate canonical cell ID '{}'", cell.id));
                    }
                    self.sum.add(cell.flux_total);
                    match cell.volume_cm3 {
                        Some(volume) => self.volume_sum.add(cell.flux_total * volume),
                        None => self.all_volumes = false,
                    }
                    self.next_ordinal += 1;
                    if self.next_ordinal > self.header.cell_count {
                        return Err("canonical flux contains more cells than declared".into());
                    }
                    cells.push(cell);
                }
                Some("footer") => {
                    let footer: FluxFooter = serde_json::from_value(value)
                        .map_err(|error| format!("canonical footer: {error}"))?;
                    self.validate_footer(&footer)?;
                    self.footer = Some(footer);
                    break;
                }
                Some(record) => return Err(format!("unknown canonical record '{record}'")),
                None => {
                    return Err(format!(
                        "canonical line {} has no record type",
                        self.line_number
                    ))
                }
            }
        }
        Ok(cells)
    }

    fn validate_footer(&mut self, footer: &FluxFooter) -> Result<(), String> {
        if footer.record != "footer"
            || footer.cell_count != self.next_ordinal
            || footer.cell_count != self.header.cell_count
        {
            return Err("canonical footer cell count does not close the header and records".into());
        }
        if relative_difference(footer.flux_sum_over_cells, self.sum.total()) > 1e-12 {
            return Err("canonical footer flux sum does not close the cell records".into());
        }
        match (footer.volume_integrated_flux, self.all_volumes) {
            (Some(value), true) if relative_difference(value, self.volume_sum.total()) <= 1e-12 => {
            }
            (None, false) => {}
            _ => return Err("canonical footer volume-integrated total is inconsistent".into()),
        }
        if footer
            .source_total_checks_max_relative_error
            .is_some_and(|value| !value.is_finite() || value < 0.0)
        {
            return Err("canonical footer contains an invalid source total check".into());
        }
        Ok(())
    }

    pub fn footer(&self) -> Option<&FluxFooter> {
        self.footer.as_ref()
    }

    pub fn finish(mut self) -> Result<FluxFooter, String> {
        while self.footer.is_none() {
            let cells = self.read_chunk(1024)?;
            if cells.is_empty() && self.footer.is_none() {
                return Err("canonical flux did not produce a footer".into());
            }
        }
        let mut trailing = String::new();
        loop {
            trailing.clear();
            if self
                .reader
                .read_line(&mut trailing)
                .map_err(|error| error.to_string())?
                == 0
            {
                break;
            }
            if !trailing.trim().is_empty() {
                return Err("canonical flux has records after its footer".into());
            }
        }
        self.footer.ok_or_else(|| "canonical footer missing".into())
    }
}

fn group_boundaries_json(path: &Path) -> Result<Vec<f64>, String> {
    let text = std::fs::read_to_string(path)
        .map_err(|error| format!("cannot read group structure {}: {error}", path.display()))?;
    let value: serde_json::Value = serde_json::from_str(&text)
        .map_err(|error| format!("group structure {}: {error}", path.display()))?;
    let array = value
        .as_array()
        .or_else(|| {
            value
                .get("boundaries_eV")
                .and_then(serde_json::Value::as_array)
        })
        .ok_or("group structure needs an array or boundaries_eV array")?;
    let boundaries: Vec<f64> = array
        .iter()
        .map(|item| item.as_f64().ok_or("group boundary is not numeric"))
        .collect::<Result<_, _>>()?;
    if boundaries.len() < 2
        || boundaries
            .iter()
            .any(|value| !value.is_finite() || *value <= 0.0)
        || boundaries.windows(2).any(|pair| pair[1] >= pair[0])
    {
        return Err(
            "FISPACT group boundaries must be finite, positive and strictly descending".into(),
        );
    }
    Ok(boundaries)
}

fn parse_fispact_fluxes(path: &Path, groups: usize) -> Result<(Vec<f64>, f64, String), String> {
    let file = File::open(path)
        .map_err(|error| format!("cannot read FISPACT fluxes {}: {error}", path.display()))?;
    let mut lines = BufReader::new(file).lines();
    let mut flux = Vec::with_capacity(groups);
    while flux.len() < groups {
        let line = lines
            .next()
            .ok_or("FISPACT fluxes ended before all group values")?
            .map_err(|error| error.to_string())?;
        if line.trim().is_empty() {
            return Err("FISPACT fluxes contains a blank line before wall loading".into());
        }
        let before = flux.len();
        for token in line.split_whitespace() {
            let value: f64 = token
                .replace(['D', 'd'], "E")
                .parse()
                .map_err(|_| format!("non-numeric FISPACT group value '{token}'"))?;
            if !value.is_finite() || value < 0.0 {
                return Err("FISPACT group values must be finite and nonnegative".into());
            }
            flux.push(value);
            if flux.len() > groups {
                return Err("FISPACT group-value record also contains wall/title data".into());
            }
        }
        if flux.len() == before {
            return Err("empty FISPACT group-value line".into());
        }
    }
    let wall_line = lines
        .next()
        .ok_or("FISPACT fluxes has no first-wall loading")?
        .map_err(|error| error.to_string())?;
    let wall_tokens: Vec<&str> = wall_line.split_whitespace().collect();
    if wall_tokens.len() != 1 {
        return Err("FISPACT first-wall loading must occupy its own line".into());
    }
    let wall_loading: f64 = wall_tokens[0]
        .replace(['D', 'd'], "E")
        .parse()
        .map_err(|_| "invalid FISPACT first-wall loading")?;
    if !wall_loading.is_finite() || wall_loading < 0.0 {
        return Err("FISPACT first-wall loading must be finite and nonnegative".into());
    }
    let title = lines
        .next()
        .ok_or("FISPACT fluxes has no identifying title")?
        .map_err(|error| error.to_string())?;
    if title.trim().is_empty() {
        return Err("FISPACT fluxes identifying title is blank".into());
    }
    if title.len() > 100 {
        return Err("FISPACT spectrum title exceeds 100 characters".into());
    }
    for line in lines {
        if !line.map_err(|error| error.to_string())?.trim().is_empty() {
            return Err("FISPACT fluxes contains data after its title".into());
        }
    }
    Ok((flux, wall_loading, title))
}

pub fn import_fispact(
    source_path: impl AsRef<Path>,
    group_path: impl AsRef<Path>,
    output_path: impl AsRef<Path>,
) -> Result<ImportSummary, String> {
    let source_path = source_path.as_ref();
    let group_path = group_path.as_ref();
    let output_path = output_path.as_ref();
    let source_snapshot = hash_snapshot(source_path)?;
    let group_snapshot = hash_snapshot(group_path)?;
    let mut descending_boundaries = group_boundaries_json(group_path)?;
    let groups = descending_boundaries.len() - 1;
    let (mut descending_flux, wall_loading, title) = parse_fispact_fluxes(source_path, groups)?;
    descending_boundaries.reverse();
    descending_flux.reverse();
    validate_boundaries(&descending_boundaries)?;
    ensure_unchanged(source_path, &source_snapshot)?;
    ensure_unchanged(group_path, &group_snapshot)?;

    let mut metadata = BTreeMap::new();
    metadata.insert(
        "first_wall_loading_MW_m2".into(),
        serde_json::json!(wall_loading),
    );
    metadata.insert("title".into(), serde_json::json!(title));
    let header = FluxHeader {
        record: "header".into(),
        schema: FLUX_SCHEMA.into(),
        source: FluxSource {
            format: "fispact-fluxes".into(),
            path: source_path.display().to_string(),
            sha256: source_snapshot.sha256.clone(),
            selector: BTreeMap::new(),
            normalization: BTreeMap::from([(
                "mode".into(),
                serde_json::json!("absolute-values-from-file"),
            )]),
            auxiliary_inputs: vec![FileProvenance {
                path: group_path.display().to_string(),
                sha256: group_snapshot.sha256,
            }],
            metadata,
        },
        energy_boundaries_eV: descending_boundaries,
        flux_units: FLUX_UNITS.into(),
        cell_count: 1,
        geometry: None,
        energy_floor: None,
    };
    let footer = atomic_output(output_path, |output| {
        let mut writer = CanonicalWriter::new(output, header)?;
        writer.write_cell(FluxCell {
            record: String::new(),
            ordinal: 0,
            id: "0".into(),
            index: None,
            bounds_cm: None,
            volume_cm3: None,
            flux_per_group: descending_flux,
            relative_error: None,
            flux_total: 0.0,
        })?;
        writer.finish(Some(0.0))
    })?;
    let output_sha = sha256_file(output_path)?;
    Ok(ImportSummary {
        output: output_path.display().to_string(),
        sha256: output_sha,
        source_sha256: source_snapshot.sha256,
        cells: 1,
        groups,
        flux_sum_over_cells: footer.flux_sum_over_cells,
    })
}

fn hdf5_context<T>(result: Result<T, hdf5_pure::Error>, context: &str) -> Result<T, String> {
    result.map_err(|error| format!("OpenMC statepoint {context}: {error}"))
}

fn hdf5_i64(file: &Hdf5File, path: &str) -> Result<Vec<i64>, String> {
    let dataset = hdf5_context(file.dataset(path), &format!("dataset '{path}'"))?;
    hdf5_context(dataset.read_i64(), &format!("reading '{path}'"))
}

fn hdf5_i64_scalar(file: &Hdf5File, path: &str) -> Result<i64, String> {
    let values = hdf5_i64(file, path)?;
    if values.len() != 1 {
        return Err(format!(
            "OpenMC statepoint dataset '{path}' must be scalar, found {} values",
            values.len()
        ));
    }
    Ok(values[0])
}

fn hdf5_f64(file: &Hdf5File, path: &str) -> Result<Vec<f64>, String> {
    let dataset = hdf5_context(file.dataset(path), &format!("dataset '{path}'"))?;
    hdf5_context(dataset.read_f64(), &format!("reading '{path}'"))
}

fn clean_hdf5_string(value: String) -> String {
    value.trim_matches('\0').trim().to_string()
}

fn hdf5_strings(file: &Hdf5File, path: &str) -> Result<Vec<String>, String> {
    let dataset = hdf5_context(file.dataset(path), &format!("dataset '{path}'"))?;
    let values = hdf5_context(dataset.read_string(), &format!("reading '{path}'"))?;
    Ok(values.into_iter().map(clean_hdf5_string).collect())
}

fn hdf5_string(file: &Hdf5File, path: &str) -> Result<String, String> {
    let values = hdf5_strings(file, path)?;
    if values.len() != 1 {
        return Err(format!(
            "OpenMC statepoint dataset '{path}' must contain one string, found {}",
            values.len()
        ));
    }
    Ok(values.into_iter().next().expect("length checked"))
}

fn required_attr_string<'a>(
    attrs: &'a std::collections::HashMap<String, AttrValue>,
    name: &str,
) -> Result<&'a str, String> {
    attrs
        .get(name)
        .and_then(AttrValue::as_str)
        .map(|value| value.trim_matches('\0').trim())
        .ok_or_else(|| format!("OpenMC statepoint has no string '{name}' attribute"))
}

fn required_attr_i64(
    attrs: &std::collections::HashMap<String, AttrValue>,
    name: &str,
) -> Result<i64, String> {
    attrs
        .get(name)
        .and_then(AttrValue::as_i64)
        .ok_or_else(|| format!("OpenMC statepoint has no integer '{name}' attribute"))
}

fn openmc_mesh_geometry(file: &Hdf5File, mesh_id: i64) -> Result<(FluxGeometry, Vec<f64>), String> {
    let base = format!("tallies/meshes/mesh {mesh_id}");
    let group = hdf5_context(file.group(&base), &format!("mesh group '{base}'"))?;
    let attrs = hdf5_context(group.attrs(), &format!("attributes for '{base}'"))?;
    if let Some(id) = attrs.get("id").and_then(AttrValue::as_i64) {
        if id != mesh_id {
            return Err(format!(
                "OpenMC mesh group {mesh_id} declares mismatched ID {id}"
            ));
        }
    }
    let kind = hdf5_string(file, &format!("{base}/type"))?;
    if !matches!(kind.as_str(), "regular" | "rectilinear") {
        return Err(format!(
            "unsupported OpenMC mesh type '{kind}'; expected 3D regular or rectilinear Cartesian mesh"
        ));
    }
    let raw_dimension = hdf5_i64(file, &format!("{base}/dimension"))?;
    if raw_dimension.len() != 3 || raw_dimension.iter().any(|value| *value <= 0) {
        return Err(format!(
            "unsupported OpenMC {kind} mesh dimension {raw_dimension:?}; expected three positive dimensions"
        ));
    }
    let dimension = [
        usize::try_from(raw_dimension[0]).map_err(|_| "OpenMC mesh dimension overflows usize")?,
        usize::try_from(raw_dimension[1]).map_err(|_| "OpenMC mesh dimension overflows usize")?,
        usize::try_from(raw_dimension[2]).map_err(|_| "OpenMC mesh dimension overflows usize")?,
    ];

    let axis_boundaries_cm = if kind == "rectilinear" {
        let grids = [
            hdf5_f64(file, &format!("{base}/x_grid"))?,
            hdf5_f64(file, &format!("{base}/y_grid"))?,
            hdf5_f64(file, &format!("{base}/z_grid"))?,
        ];
        for axis in 0..3 {
            if grids[axis].len() != dimension[axis] + 1 {
                return Err(format!(
                    "OpenMC rectilinear mesh axis {axis} has {} boundaries; expected {}",
                    grids[axis].len(),
                    dimension[axis] + 1
                ));
            }
            validate_spatial_boundaries(&grids[axis], &format!("mesh axis {axis}"))?;
        }
        grids
    } else {
        let lower = hdf5_f64(file, &format!("{base}/lower_left"))?;
        let upper = hdf5_f64(file, &format!("{base}/upper_right"))?;
        let width = hdf5_f64(file, &format!("{base}/width"))?;
        if lower.len() != 3 || upper.len() != 3 || width.len() != 3 {
            return Err("OpenMC regular mesh coordinates must be three-dimensional".into());
        }
        let mut grids = [Vec::new(), Vec::new(), Vec::new()];
        for axis in 0..3 {
            if !lower[axis].is_finite()
                || !upper[axis].is_finite()
                || !width[axis].is_finite()
                || width[axis] <= 0.0
                || upper[axis] <= lower[axis]
            {
                return Err("OpenMC regular mesh coordinates and widths are invalid".into());
            }
            let calculated_upper = (dimension[axis] as f64).mul_add(width[axis], lower[axis]);
            if relative_difference(calculated_upper, upper[axis]) > 1e-12 {
                return Err(format!(
                    "OpenMC regular mesh axis {axis} width/dimension does not close its upper-right coordinate"
                ));
            }
            grids[axis] = (0..=dimension[axis])
                .map(|index| (index as f64).mul_add(width[axis], lower[axis]))
                .collect();
            grids[axis][dimension[axis]] = upper[axis];
        }
        grids
    };

    let mut volumes = Vec::with_capacity(dimension.iter().product());
    for k in 0..dimension[2] {
        for j in 0..dimension[1] {
            for i in 0..dimension[0] {
                volumes.push(
                    (axis_boundaries_cm[0][i + 1] - axis_boundaries_cm[0][i])
                        * (axis_boundaries_cm[1][j + 1] - axis_boundaries_cm[1][j])
                        * (axis_boundaries_cm[2][k + 1] - axis_boundaries_cm[2][k]),
                );
            }
        }
    }
    Ok((
        FluxGeometry {
            kind,
            dimension,
            axis_boundaries_cm,
        },
        volumes,
    ))
}

fn openmc_result_pair(
    sum: f64,
    sum_sq: f64,
    realizations: u64,
    scale: f64,
) -> Result<(f64, Option<f64>), String> {
    if !sum.is_finite() || !sum_sq.is_finite() || sum < 0.0 || sum_sq < 0.0 {
        return Err("OpenMC tally contains a nonfinite or negative sum/sum-of-squares".into());
    }
    let n = realizations as f64;
    let mean = sum / n;
    let flux = mean * scale;
    if !flux.is_finite() || flux < 0.0 {
        return Err("OpenMC tally normalization produced invalid physical flux".into());
    }
    if realizations < 2 {
        return Ok((flux, None));
    }
    let variance_numerator = sum_sq / n - mean * mean;
    let tolerance = 64.0 * f64::EPSILON * (sum_sq / n).abs().max((mean * mean).abs());
    if variance_numerator < -tolerance {
        return Err(format!(
            "OpenMC tally sum-of-squares gives negative variance {variance_numerator}"
        ));
    }
    let standard_deviation = (variance_numerator.max(0.0) / (n - 1.0)).sqrt();
    let relative_error = if mean == 0.0 {
        0.0
    } else {
        standard_deviation / mean.abs()
    };
    if !relative_error.is_finite() || relative_error < 0.0 {
        return Err("OpenMC tally produced an invalid relative error".into());
    }
    Ok((flux, Some(relative_error)))
}

fn openmc_cell(
    ordinal: usize,
    geometry: &FluxGeometry,
    volume: f64,
    flux_per_group: Vec<f64>,
    relative_error: Option<Vec<f64>>,
) -> FluxCell {
    let nx = geometry.dimension[0];
    let ny = geometry.dimension[1];
    let i = ordinal % nx;
    let j = (ordinal / nx) % ny;
    let k = ordinal / (nx * ny);
    FluxCell {
        record: String::new(),
        ordinal: 0,
        id: format!("{},{},{}", i + 1, j + 1, k + 1),
        index: Some([i + 1, j + 1, k + 1]),
        bounds_cm: Some([
            [
                geometry.axis_boundaries_cm[0][i],
                geometry.axis_boundaries_cm[0][i + 1],
            ],
            [
                geometry.axis_boundaries_cm[1][j],
                geometry.axis_boundaries_cm[1][j + 1],
            ],
            [
                geometry.axis_boundaries_cm[2][k],
                geometry.axis_boundaries_cm[2][k + 1],
            ],
        ]),
        volume_cm3: Some(volume),
        flux_per_group,
        relative_error,
        flux_total: 0.0,
    }
}

pub fn import_openmc(
    source_path: impl AsRef<Path>,
    output_path: impl AsRef<Path>,
    tally_id: i64,
    source_rate: f64,
    energy_floor_eV: Option<f64>,
    window_rows: usize,
) -> Result<ImportSummary, String> {
    require_source_rate(source_rate)?;
    if tally_id <= 0 {
        return Err("OpenMC tally ID must be positive".into());
    }
    if window_rows == 0 {
        return Err("OpenMC HDF5 row window must be positive".into());
    }
    let source_path = source_path.as_ref();
    let output_path = output_path.as_ref();
    let snapshot = hash_snapshot(source_path)?;
    let access = FileAccessProperties::new()
        .with_metadata_cache(
            MetadataCacheConfig::new(8 * 1024 * 1024).with_max_entry_bytes(64 * 1024),
        )
        .with_chunk_cache(ChunkCacheConfig::from_h5p_cache(32, 2 * 1024 * 1024));
    let file = hdf5_context(
        Hdf5File::open_streaming_with_options(source_path, access),
        &format!("opening '{}'", source_path.display()),
    )?;
    let root_attrs = hdf5_context(file.root().attrs(), "reading root attributes")?;
    let filetype = required_attr_string(&root_attrs, "filetype")?;
    if filetype != "statepoint" {
        return Err(format!(
            "unsupported OpenMC HDF5 filetype '{filetype}'; expected statepoint"
        ));
    }
    let version = root_attrs
        .get("version")
        .and_then(AttrValue::to_i64s)
        .ok_or("OpenMC statepoint has no integer version attribute")?;
    if version.len() != 2 || version[0] != 18 {
        return Err(format!(
            "unsupported OpenMC statepoint format version {version:?}; expected major version 18"
        ));
    }
    if required_attr_i64(&root_attrs, "tallies_present")? != 1 {
        return Err("OpenMC statepoint declares that no tallies are present".into());
    }

    let tally_base = format!("tallies/tally {tally_id}");
    let tally_group = hdf5_context(
        file.group(&tally_base),
        &format!("selected tally group '{tally_base}'"),
    )?;
    let tally_attrs = hdf5_context(
        tally_group.attrs(),
        &format!("attributes for '{tally_base}'"),
    )?;
    if tally_attrs
        .get("internal")
        .and_then(AttrValue::as_i64)
        .is_some_and(|value| value != 0)
    {
        return Err(format!(
            "unsupported OpenMC tally {tally_id}: internal tally has no user flux result"
        ));
    }
    let tally_datasets = hdf5_context(
        tally_group.datasets(),
        &format!("dataset list for '{tally_base}'"),
    )?;
    if tally_datasets.iter().any(|name| name == "derivative") {
        return Err(format!(
            "unsupported OpenMC tally {tally_id}: derivatives are not flux interchange inputs"
        ));
    }
    let scores = hdf5_strings(&file, &format!("{tally_base}/score_bins"))?;
    if scores != ["flux"] {
        return Err(format!(
            "unsupported OpenMC tally {tally_id} scores {scores:?}; expected exactly ['flux']"
        ));
    }
    let nuclides = hdf5_strings(&file, &format!("{tally_base}/nuclides"))?;
    if nuclides != ["total"] {
        return Err(format!(
            "unsupported OpenMC tally {tally_id} nuclides {nuclides:?}; expected exactly ['total']"
        ));
    }
    if hdf5_i64_scalar(&file, &format!("{tally_base}/n_score_bins"))? != 1 {
        return Err(format!(
            "unsupported OpenMC tally {tally_id}: expected one score bin"
        ));
    }
    let estimator = hdf5_string(&file, &format!("{tally_base}/estimator"))?;
    if estimator != "tracklength" {
        return Err(format!(
            "unsupported OpenMC flux estimator '{estimator}'; expected tracklength"
        ));
    }
    let realizations_raw = hdf5_i64_scalar(&file, &format!("{tally_base}/n_realizations"))?;
    let realizations = u64::try_from(realizations_raw)
        .ok()
        .filter(|value| *value > 0)
        .ok_or("OpenMC tally n_realizations must be positive")?;
    if hdf5_i64_scalar(&file, &format!("{tally_base}/n_filters"))? != 2 {
        return Err(format!(
            "unsupported OpenMC tally {tally_id}: expected exactly MeshFilter and EnergyFilter"
        ));
    }
    let filter_ids = hdf5_i64(&file, &format!("{tally_base}/filters"))?;
    if filter_ids.len() != 2 || filter_ids[0] == filter_ids[1] {
        return Err(format!(
            "unsupported OpenMC tally {tally_id} filter IDs {filter_ids:?}; expected two distinct filters"
        ));
    }
    let mut filter_types = Vec::with_capacity(2);
    for id in &filter_ids {
        let base = format!("tallies/filters/filter {id}");
        let filter_group = hdf5_context(file.group(&base), &format!("filter group '{base}'"))?;
        let datasets = hdf5_context(
            filter_group.datasets(),
            &format!("dataset list for '{base}'"),
        )?;
        if datasets
            .iter()
            .any(|name| matches!(name.as_str(), "translation" | "rotation"))
        {
            return Err(format!(
                "unsupported OpenMC filter {id}: translated or rotated mesh filters have no canonical Cartesian bounds"
            ));
        }
        filter_types.push(hdf5_string(&file, &format!("{base}/type"))?);
    }
    if !matches!(filter_types.as_slice(), [mesh, energy] if mesh == "mesh" && energy == "energy")
        && !matches!(filter_types.as_slice(), [energy, mesh] if energy == "energy" && mesh == "mesh")
    {
        return Err(format!(
            "unsupported OpenMC tally {tally_id} filters {filter_types:?}; expected exactly mesh and energy in either order"
        ));
    }
    let mesh_position = filter_types
        .iter()
        .position(|value| value == "mesh")
        .expect("validated filter types");
    let energy_position = 1 - mesh_position;
    let mesh_filter_id = filter_ids[mesh_position];
    let energy_filter_id = filter_ids[energy_position];
    let mesh_filter_base = format!("tallies/filters/filter {mesh_filter_id}");
    let energy_filter_base = format!("tallies/filters/filter {energy_filter_id}");
    let mesh_ids = hdf5_i64(&file, &format!("{mesh_filter_base}/bins"))?;
    if mesh_ids.len() != 1 || mesh_ids[0] <= 0 {
        return Err(format!(
            "OpenMC MeshFilter {mesh_filter_id} must reference exactly one mesh ID"
        ));
    }
    let mesh_id = mesh_ids[0];
    let (geometry, volumes) = openmc_mesh_geometry(&file, mesh_id)?;
    let cell_count = geometry.dimension.iter().product::<usize>();
    if hdf5_i64_scalar(&file, &format!("{mesh_filter_base}/n_bins"))? != cell_count as i64 {
        return Err(format!(
            "OpenMC MeshFilter {mesh_filter_id} bin count does not match its mesh dimensions"
        ));
    }
    let source_boundaries = hdf5_f64(&file, &format!("{energy_filter_base}/bins"))?;
    let (boundaries_eV, energy_floor) = apply_energy_floor(source_boundaries, energy_floor_eV)?;
    let group_count = boundaries_eV.len() - 1;
    if hdf5_i64_scalar(&file, &format!("{energy_filter_base}/n_bins"))? != group_count as i64 {
        return Err(format!(
            "OpenMC EnergyFilter {energy_filter_id} bin count does not match its boundaries"
        ));
    }
    if mesh_position == 0 && group_count > window_rows {
        return Err(format!(
            "OpenMC HDF5 row window {window_rows} is smaller than the {group_count}-group cell row; increase --window-rows"
        ));
    }

    let results = hdf5_context(
        file.dataset(&format!("{tally_base}/results")),
        "opening selected tally results",
    )?;
    let shape = hdf5_context(results.shape(), "reading selected tally results shape")?;
    let expected_rows = cell_count
        .checked_mul(group_count)
        .ok_or("OpenMC tally dimensions overflow")?;
    if shape != [expected_rows as u64, 1, 2] {
        return Err(format!(
            "unsupported OpenMC tally results shape {shape:?}; expected [{expected_rows}, 1, 2]"
        ));
    }
    ensure_unchanged(source_path, &snapshot)?;

    let mut metadata = BTreeMap::from([
        (
            "statepoint_format".into(),
            serde_json::json!({"major": version[0], "minor": version[1]}),
        ),
        ("mesh_id".into(), serde_json::json!(mesh_id)),
        (
            "filter_order".into(),
            serde_json::json!(filter_types.clone()),
        ),
        ("hdf5_window_rows".into(), serde_json::json!(window_rows)),
    ]);
    if let Some(openmc_version) = root_attrs
        .get("openmc_version")
        .and_then(AttrValue::to_i64s)
    {
        metadata.insert("openmc_version".into(), serde_json::json!(openmc_version));
    }
    let header = FluxHeader {
        record: "header".into(),
        schema: FLUX_SCHEMA.into(),
        source: FluxSource {
            format: "openmc-statepoint-18".into(),
            path: source_path.display().to_string(),
            sha256: snapshot.sha256.clone(),
            selector: BTreeMap::from([
                ("tally_id".into(), serde_json::json!(tally_id)),
                ("mesh_filter_id".into(), serde_json::json!(mesh_filter_id)),
                (
                    "energy_filter_id".into(),
                    serde_json::json!(energy_filter_id),
                ),
            ]),
            normalization: BTreeMap::from([
                ("source_rate_per_s".into(), serde_json::json!(source_rate)),
                (
                    "operation".into(),
                    serde_json::json!("sum / n_realizations * source_rate / cell_volume"),
                ),
            ]),
            auxiliary_inputs: Vec::new(),
            metadata,
        },
        energy_boundaries_eV: boundaries_eV,
        flux_units: FLUX_UNITS.into(),
        cell_count: cell_count as u64,
        geometry: Some(geometry.clone()),
        energy_floor,
    };

    let footer = atomic_output(output_path, |output| {
        let mut writer = CanonicalWriter::new(output, header)?;
        if mesh_position == 0 {
            let chunk_cells = (window_rows / group_count).max(1);
            for start_cell in (0..cell_count).step_by(chunk_cells) {
                let count = chunk_cells.min(cell_count - start_cell);
                let start_row = start_cell * group_count;
                let row_count = count * group_count;
                let values = hdf5_context(
                    results.read_f64_rows(start_row as u64, row_count as u64),
                    "reading bounded mesh-major tally result rows",
                )?;
                if values.len() != row_count * 2 {
                    return Err("OpenMC bounded result read returned the wrong value count".into());
                }
                for local_cell in 0..count {
                    let ordinal = start_cell + local_cell;
                    let mut flux_per_group = Vec::with_capacity(group_count);
                    let mut errors = Vec::with_capacity(group_count);
                    for group in 0..group_count {
                        let pair = (local_cell * group_count + group) * 2;
                        let (flux, error) = openmc_result_pair(
                            values[pair],
                            values[pair + 1],
                            realizations,
                            source_rate / volumes[ordinal],
                        )?;
                        flux_per_group.push(flux);
                        if let Some(error) = error {
                            errors.push(error);
                        }
                    }
                    let relative_error = (realizations >= 2).then_some(errors);
                    writer.write_cell(openmc_cell(
                        ordinal,
                        &geometry,
                        volumes[ordinal],
                        flux_per_group,
                        relative_error,
                    ))?;
                }
            }
        } else {
            let chunk_cells = window_rows.min(cell_count);
            for start_cell in (0..cell_count).step_by(chunk_cells) {
                let count = chunk_cells.min(cell_count - start_cell);
                let mut chunk_flux = vec![vec![0.0; group_count]; count];
                let mut chunk_errors =
                    (realizations >= 2).then(|| vec![vec![0.0; group_count]; count]);
                for group in 0..group_count {
                    let start_row = group * cell_count + start_cell;
                    let values = hdf5_context(
                        results.read_f64_rows(start_row as u64, count as u64),
                        "reading bounded energy-major tally result rows",
                    )?;
                    if values.len() != count * 2 {
                        return Err(
                            "OpenMC bounded result read returned the wrong value count".into()
                        );
                    }
                    for local_cell in 0..count {
                        let ordinal = start_cell + local_cell;
                        let pair = local_cell * 2;
                        let (flux, error) = openmc_result_pair(
                            values[pair],
                            values[pair + 1],
                            realizations,
                            source_rate / volumes[ordinal],
                        )?;
                        chunk_flux[local_cell][group] = flux;
                        if let (Some(errors), Some(error)) = (&mut chunk_errors, error) {
                            errors[local_cell][group] = error;
                        }
                    }
                }
                for local_cell in 0..count {
                    let ordinal = start_cell + local_cell;
                    writer.write_cell(openmc_cell(
                        ordinal,
                        &geometry,
                        volumes[ordinal],
                        std::mem::take(&mut chunk_flux[local_cell]),
                        chunk_errors
                            .as_mut()
                            .map(|errors| std::mem::take(&mut errors[local_cell])),
                    ))?;
                }
            }
        }
        writer.finish(None)
    })?;
    ensure_unchanged(source_path, &snapshot)?;
    Ok(ImportSummary {
        output: output_path.display().to_string(),
        sha256: sha256_file(output_path)?,
        source_sha256: snapshot.sha256,
        cells: cell_count as u64,
        groups: group_count,
        flux_sum_over_cells: footer.flux_sum_over_cells,
    })
}

fn require_source_rate(source_rate: f64) -> Result<(), String> {
    if !source_rate.is_finite() || source_rate <= 0.0 {
        return Err("source rate must be finite and positive, in source s^-1".into());
    }
    Ok(())
}

fn parse_mcnp_float(token: &str, context: &str) -> Result<f64, String> {
    let normalized = token.replace(['D', 'd'], "E");
    let value = normalized.parse::<f64>().or_else(|original_error| {
        let bytes = normalized.as_bytes();
        let exponent = (1..bytes.len()).rev().find(|&index| {
            matches!(bytes[index], b'+' | b'-')
                && bytes[index - 1].is_ascii_digit()
                && !matches!(bytes[index - 1], b'E' | b'e')
        });
        let Some(index) = exponent else {
            return Err(original_error);
        };
        let mut repaired = normalized.clone();
        repaired.insert(index, 'E');
        repaired.parse::<f64>()
    });
    let value = value.map_err(|_| format!("invalid MCNP {context} value '{token}'"))?;
    if !value.is_finite() {
        return Err(format!("nonfinite MCNP {context} value '{token}'"));
    }
    Ok(value)
}

fn parse_values_after_colon(line: &str, context: &str) -> Result<Vec<f64>, String> {
    let (_, values) = line
        .split_once(':')
        .ok_or_else(|| format!("MCNP {context} line has no colon"))?;
    let parsed: Vec<f64> = values
        .split_whitespace()
        .map(|token| parse_mcnp_float(token, context))
        .collect::<Result<_, _>>()?;
    if parsed.len() < 2 {
        return Err(format!("MCNP {context} has fewer than two boundaries"));
    }
    Ok(parsed)
}

fn validate_spatial_boundaries(boundaries: &[f64], axis: &str) -> Result<(), String> {
    if boundaries.len() < 2
        || boundaries.iter().any(|value| !value.is_finite())
        || boundaries.windows(2).any(|pair| pair[1] <= pair[0])
    {
        return Err(format!(
            "MCNP {axis} boundaries must be finite and strictly increasing"
        ));
    }
    Ok(())
}

fn selected_mcnp_block<'a>(
    lines: &'a [&'a str],
    prefix: &str,
    tally_id: u64,
) -> Result<&'a [&'a str], String> {
    let starts: Vec<(usize, u64)> = lines
        .iter()
        .enumerate()
        .filter_map(|(index, line)| {
            let trimmed = line.trim_start();
            let remainder = trimmed.strip_prefix(prefix)?;
            let id = remainder.split_whitespace().next()?.parse().ok()?;
            Some((index, id))
        })
        .collect();
    let selected = starts
        .iter()
        .position(|(_, id)| *id == tally_id)
        .ok_or_else(|| format!("MCNP tally {tally_id} was not found"))?;
    let begin = starts[selected].0;
    let end = starts
        .get(selected + 1)
        .map_or(lines.len(), |(index, _)| *index);
    Ok(&lines[begin..end])
}

#[derive(Debug)]
struct MeshtalParsed {
    boundaries_eV: Vec<f64>,
    energy_floor: Option<EnergyFloor>,
    geometry: FluxGeometry,
    cells: Vec<FluxCell>,
    total_check: Option<f64>,
}

fn parse_meshtal_block(
    block: &[&str],
    source_rate: f64,
    energy_floor_eV: Option<f64>,
) -> Result<MeshtalParsed, String> {
    let description = block
        .get(1..)
        .and_then(|rest| rest.iter().find(|line| !line.trim().is_empty()))
        .ok_or("MCNP meshtal tally has no particle description")?
        .trim()
        .to_ascii_lowercase();
    if !description.starts_with("neutron  mesh tally")
        && !description.starts_with("neutron mesh tally")
    {
        return Err(format!(
            "unsupported MCNP meshtal particle/type description '{description}'; expected neutron mesh tally"
        ));
    }
    if block.iter().any(|line| {
        let value = line.to_ascii_lowercase();
        value.contains("time bin") || value.contains("collision bin") || value.contains("user bin")
    }) {
        return Err("unsupported MCNP meshtal time/collision/user bin dimension".into());
    }
    if block.iter().any(|line| {
        let trimmed = line.trim_start();
        trimmed.starts_with("R direction:")
            || trimmed.starts_with("Theta direction")
            || trimmed.starts_with("origin at")
    }) {
        return Err(
            "unsupported MCNP meshtal cylindrical or spherical geometry; expected XYZ".into(),
        );
    }
    if block.iter().any(|line| {
        let value = line.to_ascii_lowercase();
        value.contains("dose response")
            || value.contains("response function")
            || value.contains("tally multiplier")
            || value.contains("matrix format")
            || value.contains("cuview")
            || value.contains("xdmf")
    }) {
        return Err("unsupported MCNP meshtal response, multiplier or non-COL format".into());
    }

    let boundary_line = |name: &str| -> Result<&str, String> {
        block
            .iter()
            .copied()
            .find(|line| line.trim_start().starts_with(name))
            .ok_or_else(|| format!("MCNP meshtal has no {name} boundary record"))
    };
    let x = parse_values_after_colon(boundary_line("X direction:")?, "X direction")?;
    let y = parse_values_after_colon(boundary_line("Y direction:")?, "Y direction")?;
    let z = parse_values_after_colon(boundary_line("Z direction:")?, "Z direction")?;
    validate_spatial_boundaries(&x, "X")?;
    validate_spatial_boundaries(&y, "Y")?;
    validate_spatial_boundaries(&z, "Z")?;
    let source_energy =
        parse_values_after_colon(boundary_line("Energy bin boundaries:")?, "energy boundary")?;
    if source_energy.windows(2).any(|pair| pair[1] <= pair[0])
        || source_energy.iter().any(|value| *value < 0.0)
    {
        return Err("MCNP meshtal energy boundaries must be nonnegative and increasing".into());
    }
    let energy_eV: Vec<f64> = source_energy.iter().map(|value| value * 1e6).collect();
    let (boundaries_eV, energy_floor) = apply_energy_floor(energy_eV, energy_floor_eV)?;

    let header_index = block
        .iter()
        .position(|line| line.contains("Result") && line.contains("Rel Error"))
        .ok_or("MCNP meshtal has no traditional column data header")?;
    let header_tokens: Vec<&str> = block[header_index].split_whitespace().collect();
    let valid_header = matches!(
        header_tokens.as_slice(),
        ["Energy", "X", "Y", "Z", "Result", "Rel", "Error"]
            | ["Energy", "X", "Y", "Z", "Result", "Rel", "Error", "Volume", "Rslt", "*", "Vol"]
    );
    if !valid_header {
        return Err(format!(
            "unsupported MCNP meshtal columns '{}'; expected energy-resolved rectangular COL flux",
            block[header_index].trim()
        ));
    }

    let dimension = [x.len() - 1, y.len() - 1, z.len() - 1];
    let cell_count = dimension.iter().product::<usize>();
    let group_count = boundaries_eV.len() - 1;
    let data_lines: Vec<&str> = block[header_index + 1..]
        .iter()
        .copied()
        .filter(|line| !line.trim().is_empty())
        .collect();
    let group_rows = cell_count
        .checked_mul(group_count)
        .ok_or("MCNP meshtal dimensions overflow")?;
    if data_lines.len() < group_rows {
        return Err(format!(
            "truncated MCNP meshtal: {} data rows, expected at least {group_rows}",
            data_lines.len()
        ));
    }

    let mut flux = vec![vec![0.0; group_count]; cell_count];
    let mut errors = vec![vec![0.0; group_count]; cell_count];
    for group in 0..group_count {
        for i in 0..dimension[0] {
            for j in 0..dimension[1] {
                for k in 0..dimension[2] {
                    let source_ordinal =
                        group * cell_count + (i * dimension[1] + j) * dimension[2] + k;
                    let tokens: Vec<&str> = data_lines[source_ordinal].split_whitespace().collect();
                    if tokens.len() != 6 && tokens.len() != 8 {
                        return Err(format!(
                            "malformed MCNP meshtal data row '{}'",
                            data_lines[source_ordinal]
                        ));
                    }
                    if tokens[0] == "Total" {
                        return Err(
                            "MCNP meshtal energy rows are truncated before Total rows".into()
                        );
                    }
                    let energy = parse_mcnp_float(tokens[0], "energy")?;
                    let expected_energy = source_energy[group + 1];
                    if relative_difference(energy, expected_energy) > 1e-9 {
                        return Err(format!(
                            "MCNP meshtal energy row {energy} does not match boundary {expected_energy}"
                        ));
                    }
                    let centers = [
                        parse_mcnp_float(tokens[1], "X center")?,
                        parse_mcnp_float(tokens[2], "Y center")?,
                        parse_mcnp_float(tokens[3], "Z center")?,
                    ];
                    let expected = [
                        (x[i] + x[i + 1]) / 2.0,
                        (y[j] + y[j + 1]) / 2.0,
                        (z[k] + z[k + 1]) / 2.0,
                    ];
                    if (0..3).any(|axis| relative_difference(centers[axis], expected[axis]) > 1e-9)
                    {
                        return Err(
                            "MCNP meshtal spatial rows are missing, duplicated or out of order"
                                .into(),
                        );
                    }
                    let value = parse_mcnp_float(tokens[4], "result")?;
                    let error = parse_mcnp_float(tokens[5], "relative error")?;
                    if value < 0.0 || error < 0.0 {
                        return Err(
                            "MCNP meshtal result and relative error must be nonnegative".into()
                        );
                    }
                    let canonical_ordinal = i + dimension[0] * (j + dimension[1] * k);
                    flux[canonical_ordinal][group] = value * source_rate;
                    errors[canonical_ordinal][group] = error;
                }
            }
        }
    }

    let total_lines = &data_lines[group_rows..];
    let mut total_check: Option<f64> = None;
    if !total_lines.is_empty() {
        if total_lines.len() != cell_count {
            return Err(format!(
                "MCNP meshtal has {} Total rows; expected {cell_count}",
                total_lines.len()
            ));
        }
        for i in 0..dimension[0] {
            for j in 0..dimension[1] {
                for k in 0..dimension[2] {
                    let source_ordinal = (i * dimension[1] + j) * dimension[2] + k;
                    let tokens: Vec<&str> =
                        total_lines[source_ordinal].split_whitespace().collect();
                    if (tokens.len() != 6 && tokens.len() != 8) || tokens[0] != "Total" {
                        return Err(format!(
                            "malformed MCNP meshtal Total row '{}'",
                            total_lines[source_ordinal]
                        ));
                    }
                    let centers = [
                        parse_mcnp_float(tokens[1], "X center")?,
                        parse_mcnp_float(tokens[2], "Y center")?,
                        parse_mcnp_float(tokens[3], "Z center")?,
                    ];
                    let expected = [
                        (x[i] + x[i + 1]) / 2.0,
                        (y[j] + y[j + 1]) / 2.0,
                        (z[k] + z[k + 1]) / 2.0,
                    ];
                    if (0..3).any(|axis| relative_difference(centers[axis], expected[axis]) > 1e-9)
                    {
                        return Err(
                            "MCNP meshtal Total rows are missing, duplicated or out of order"
                                .into(),
                        );
                    }
                    let printed = parse_mcnp_float(tokens[4], "Total result")? * source_rate;
                    let canonical_ordinal = i + dimension[0] * (j + dimension[1] * k);
                    let summed = compensated(&flux[canonical_ordinal]);
                    let difference = relative_difference(printed, summed);
                    if difference > 1e-12 {
                        return Err(format!(
                            "MCNP meshtal Total for cell ({},{},{}) differs from parsed energy-bin sum by {difference}",
                            i + 1,
                            j + 1,
                            k + 1
                        ));
                    }
                    total_check = Some(total_check.unwrap_or(0.0).max(difference));
                }
            }
        }
    }

    let mut cells = Vec::with_capacity(cell_count);
    for k in 0..dimension[2] {
        for j in 0..dimension[1] {
            for i in 0..dimension[0] {
                let ordinal = i + dimension[0] * (j + dimension[1] * k);
                let bounds = [[x[i], x[i + 1]], [y[j], y[j + 1]], [z[k], z[k + 1]]];
                let volume = (bounds[0][1] - bounds[0][0])
                    * (bounds[1][1] - bounds[1][0])
                    * (bounds[2][1] - bounds[2][0]);
                cells.push(FluxCell {
                    record: String::new(),
                    ordinal: 0,
                    id: format!("{},{},{}", i + 1, j + 1, k + 1),
                    index: Some([i + 1, j + 1, k + 1]),
                    bounds_cm: Some(bounds),
                    volume_cm3: Some(volume),
                    flux_per_group: std::mem::take(&mut flux[ordinal]),
                    relative_error: Some(std::mem::take(&mut errors[ordinal])),
                    flux_total: 0.0,
                });
            }
        }
    }
    Ok(MeshtalParsed {
        boundaries_eV,
        energy_floor,
        geometry: FluxGeometry {
            kind: "rectilinear".into(),
            dimension,
            axis_boundaries_cm: [x, y, z],
        },
        cells,
        total_check,
    })
}

pub fn import_meshtal(
    source_path: impl AsRef<Path>,
    output_path: impl AsRef<Path>,
    tally_id: u64,
    source_rate: f64,
    energy_floor_eV: Option<f64>,
) -> Result<ImportSummary, String> {
    require_source_rate(source_rate)?;
    let source_path = source_path.as_ref();
    let output_path = output_path.as_ref();
    let snapshot = hash_snapshot(source_path)?;
    let text = std::fs::read_to_string(source_path).map_err(|error| {
        format!(
            "cannot read MCNP meshtal {}: {error}",
            source_path.display()
        )
    })?;
    let lines: Vec<&str> = text.lines().collect();
    let block = selected_mcnp_block(&lines, "Mesh Tally Number", tally_id)?;
    let parsed = parse_meshtal_block(block, source_rate, energy_floor_eV)?;
    ensure_unchanged(source_path, &snapshot)?;

    let groups = parsed.boundaries_eV.len() - 1;
    let cell_count = parsed.cells.len() as u64;
    let header = FluxHeader {
        record: "header".into(),
        schema: FLUX_SCHEMA.into(),
        source: FluxSource {
            format: "mcnp-meshtal-col".into(),
            path: source_path.display().to_string(),
            sha256: snapshot.sha256.clone(),
            selector: BTreeMap::from([("tally_id".into(), serde_json::json!(tally_id))]),
            normalization: BTreeMap::from([
                ("source_rate_per_s".into(), serde_json::json!(source_rate)),
                ("source_units".into(), serde_json::json!("particle^-1")),
            ]),
            auxiliary_inputs: Vec::new(),
            metadata: BTreeMap::from([(
                "source_total_rows".into(),
                serde_json::json!(parsed.total_check.is_some()),
            )]),
        },
        energy_boundaries_eV: parsed.boundaries_eV,
        flux_units: FLUX_UNITS.into(),
        cell_count,
        geometry: Some(parsed.geometry),
        energy_floor: parsed.energy_floor,
    };
    let footer = atomic_output(output_path, |output| {
        let mut writer = CanonicalWriter::new(output, header)?;
        for cell in parsed.cells {
            writer.write_cell(cell)?;
        }
        writer.finish(parsed.total_check)
    })?;
    Ok(ImportSummary {
        output: output_path.display().to_string(),
        sha256: sha256_file(output_path)?,
        source_sha256: snapshot.sha256,
        cells: cell_count,
        groups,
        flux_sum_over_cells: footer.flux_sum_over_cells,
    })
}

#[derive(Debug)]
struct MctalDimension {
    suffix: Option<char>,
    count: usize,
    values: Vec<f64>,
}

fn mctal_dimension_header(line: &str) -> Option<(char, Option<char>, usize)> {
    let mut tokens = line.split_whitespace();
    let keyword = tokens.next()?;
    if keyword.len() > 2 || keyword.is_empty() {
        return None;
    }
    let bytes = keyword.as_bytes();
    let dimension = bytes[0] as char;
    if !matches!(
        dimension,
        'f' | 'd' | 'u' | 's' | 'm' | 'c' | 'r' | 'e' | 't'
    ) {
        return None;
    }
    let suffix = (bytes.len() == 2).then(|| bytes[1] as char);
    if suffix.is_some_and(|value| !matches!(value, 'c' | 't')) {
        return None;
    }
    let count = tokens.next()?.parse().ok()?;
    Some((dimension, suffix, count))
}

#[derive(Debug)]
struct MctalParsed {
    boundaries_eV: Vec<f64>,
    energy_floor: Option<EnergyFloor>,
    cells: Vec<FluxCell>,
    total_check: Option<f64>,
}

fn parse_mctal_block(
    block: &[&str],
    tally_id: u64,
    source_rate: f64,
    energy_floor_eV: Option<f64>,
) -> Result<MctalParsed, String> {
    if tally_id % 10 != 4 {
        return Err(format!(
            "unsupported MCNP tally {tally_id}; expected an F4:N cell-flux tally"
        ));
    }
    let header: Vec<&str> = block
        .first()
        .ok_or("empty MCNP mctal tally block")?
        .split_whitespace()
        .collect();
    if header.len() < 3 || header[0] != "tally" {
        return Err("malformed MCNP mctal tally header".into());
    }
    let particle_type: i64 = header[2]
        .parse()
        .map_err(|_| "invalid MCNP mctal particle type")?;
    let mut cursor = 1usize;
    if particle_type == -1 {
        let particle_line = block
            .get(cursor)
            .ok_or("truncated MCNP mctal particle vector")?;
        let particles: Vec<i64> = particle_line
            .split_whitespace()
            .map(|token| {
                token
                    .parse()
                    .map_err(|_| "invalid MCNP mctal particle vector")
            })
            .collect::<Result<_, _>>()?;
        if particles.first() != Some(&1) || particles.iter().skip(1).any(|value| *value != 0) {
            return Err(
                "unsupported MCNP mctal particle selection; expected neutron-only F4:N".into(),
            );
        }
        cursor += 1;
    } else if particle_type != 1 {
        return Err(format!(
            "unsupported MCNP mctal particle type {particle_type}; expected neutron F4:N"
        ));
    }

    while cursor < block.len() && mctal_dimension_header(block[cursor]).is_none() {
        if block[cursor].trim_start().starts_with("vals") {
            return Err("MCNP mctal tally has no dimension records".into());
        }
        cursor += 1;
    }
    let mut dimensions: BTreeMap<char, MctalDimension> = BTreeMap::new();
    while cursor < block.len() {
        let Some((name, suffix, count)) = mctal_dimension_header(block[cursor]) else {
            break;
        };
        if dimensions.contains_key(&name) {
            return Err(format!("duplicate MCNP mctal {name} dimension"));
        }
        cursor += 1;
        let mut values = Vec::new();
        while cursor < block.len()
            && mctal_dimension_header(block[cursor]).is_none()
            && !block[cursor].trim_start().starts_with("vals")
        {
            for token in block[cursor].split_whitespace() {
                values.push(parse_mcnp_float(token, "dimension")?);
            }
            cursor += 1;
        }
        dimensions.insert(
            name,
            MctalDimension {
                suffix,
                count,
                values,
            },
        );
    }
    if dimensions.contains_key(&'r') {
        return Err("unsupported MCNP mctal radial/mesh dimension; expected cell F4:N".into());
    }
    for required in ['f', 'd', 'u', 's', 'm', 'c', 'e', 't'] {
        if !dimensions.contains_key(&required) {
            return Err(format!(
                "MCNP mctal tally is missing its {required} dimension"
            ));
        }
    }
    for name in ['d', 'u', 's', 'c', 't'] {
        let dimension = &dimensions[&name];
        if dimension.count > 1 || dimension.suffix.is_some() {
            return Err(format!(
                "unsupported MCNP mctal {name} dimension; expected a singleton without total/cumulative bins"
            ));
        }
    }
    let multiplier = &dimensions[&'m'];
    if multiplier.count != 0 || multiplier.suffix.is_some() {
        return Err("unsupported MCNP mctal multiplier dimension; expected m 0".into());
    }
    let spatial = &dimensions[&'f'];
    if spatial.count == 0 || spatial.suffix.is_some() || spatial.values.len() != spatial.count {
        return Err("MCNP mctal F dimension must contain one or more explicit cell IDs".into());
    }
    let mut cell_ids = Vec::with_capacity(spatial.count);
    for value in &spatial.values {
        if *value <= 0.0 || value.fract() != 0.0 || *value > u64::MAX as f64 {
            return Err("MCNP mctal F dimension contains a non-integral cell ID".into());
        }
        cell_ids.push(*value as u64);
    }
    let mut unique_ids = cell_ids.clone();
    unique_ids.sort_unstable();
    unique_ids.dedup();
    if unique_ids.len() != cell_ids.len() {
        return Err("MCNP mctal F dimension contains duplicate cell IDs".into());
    }

    let energy = &dimensions[&'e'];
    if energy.suffix == Some('c') {
        return Err("unsupported MCNP mctal cumulative energy bins".into());
    }
    let has_total = energy.suffix == Some('t');
    let group_count = if has_total {
        energy
            .count
            .checked_sub(1)
            .ok_or("MCNP mctal total-only E dimension has no energy groups")?
    } else {
        energy.count
    };
    if group_count == 0 || energy.values.len() != group_count {
        return Err(format!(
            "MCNP mctal E dimension declares {group_count} groups but provides {} upper boundaries",
            energy.values.len()
        ));
    }
    if energy.values.iter().any(|value| *value <= 0.0)
        || energy.values.windows(2).any(|pair| pair[1] <= pair[0])
    {
        return Err("MCNP mctal energy upper boundaries must be positive and increasing".into());
    }
    let mut boundaries_eV = Vec::with_capacity(group_count + 1);
    boundaries_eV.push(0.0);
    boundaries_eV.extend(energy.values.iter().map(|value| value * 1e6));
    let (boundaries_eV, energy_floor) = apply_energy_floor(boundaries_eV, energy_floor_eV)?;

    let vals_line = block
        .get(cursor)
        .ok_or("truncated MCNP mctal before vals")?
        .trim_start();
    if !vals_line.starts_with("vals") {
        return Err("MCNP mctal tally has no vals record".into());
    }
    if vals_line != "vals" {
        if vals_line.starts_with("vals pert") {
            return Err("unsupported MCNP mctal perturbation values".into());
        }
        return Err(format!("unsupported MCNP mctal vals record '{vals_line}'"));
    }
    cursor += 1;
    let mut values = Vec::new();
    while cursor < block.len() {
        let line = block[cursor].trim_start();
        if line.starts_with("tfc") || line.starts_with("kcode") || line.starts_with("tally") {
            break;
        }
        if line.starts_with("vals") {
            return Err("unsupported MCNP mctal perturbation or duplicate vals record".into());
        }
        for token in line.split_whitespace() {
            values.push(parse_mcnp_float(token, "value/error")?);
        }
        cursor += 1;
    }
    let energy_bins = group_count + usize::from(has_total);
    let expected_values = spatial
        .count
        .checked_mul(energy_bins)
        .and_then(|count| count.checked_mul(2))
        .ok_or("MCNP mctal dimensions overflow")?;
    if values.len() != expected_values {
        return Err(format!(
            "truncated or overlong MCNP mctal vals: {} numbers, expected {expected_values}",
            values.len()
        ));
    }

    let mut cells = Vec::with_capacity(spatial.count);
    let mut total_check: Option<f64> = None;
    for (cell, id) in cell_ids.into_iter().enumerate() {
        let mut flux_per_group = Vec::with_capacity(group_count);
        let mut relative_error = Vec::with_capacity(group_count);
        for group in 0..group_count {
            let pair = (cell * energy_bins + group) * 2;
            let value = values[pair];
            let error = values[pair + 1];
            if value < 0.0 || error < 0.0 {
                return Err(
                    "MCNP mctal flux values and relative errors must be nonnegative".into(),
                );
            }
            flux_per_group.push(value * source_rate);
            relative_error.push(error);
        }
        if has_total {
            let pair = (cell * energy_bins + group_count) * 2;
            let printed = values[pair] * source_rate;
            let printed_error = values[pair + 1];
            if printed < 0.0 || printed_error < 0.0 {
                return Err("MCNP mctal Total and relative error must be nonnegative".into());
            }
            let difference = relative_difference(printed, compensated(&flux_per_group));
            if difference > 1e-12 {
                return Err(format!(
                    "MCNP mctal Total for cell {id} differs from parsed energy-bin sum by {difference}"
                ));
            }
            total_check = Some(total_check.unwrap_or(0.0).max(difference));
        }
        cells.push(FluxCell {
            record: String::new(),
            ordinal: 0,
            id: id.to_string(),
            index: None,
            bounds_cm: None,
            volume_cm3: None,
            flux_per_group,
            relative_error: Some(relative_error),
            flux_total: 0.0,
        });
    }
    Ok(MctalParsed {
        boundaries_eV,
        energy_floor,
        cells,
        total_check,
    })
}

pub fn import_mctal(
    source_path: impl AsRef<Path>,
    output_path: impl AsRef<Path>,
    tally_id: u64,
    source_rate: f64,
    energy_floor_eV: Option<f64>,
) -> Result<ImportSummary, String> {
    require_source_rate(source_rate)?;
    let source_path = source_path.as_ref();
    let output_path = output_path.as_ref();
    let snapshot = hash_snapshot(source_path)?;
    let text = std::fs::read_to_string(source_path)
        .map_err(|error| format!("cannot read MCNP mctal {}: {error}", source_path.display()))?;
    let lines: Vec<&str> = text.lines().collect();
    let block = selected_mcnp_block(&lines, "tally", tally_id)?;
    let parsed = parse_mctal_block(block, tally_id, source_rate, energy_floor_eV)?;
    ensure_unchanged(source_path, &snapshot)?;

    let groups = parsed.boundaries_eV.len() - 1;
    let cell_count = parsed.cells.len() as u64;
    let header = FluxHeader {
        record: "header".into(),
        schema: FLUX_SCHEMA.into(),
        source: FluxSource {
            format: "mcnp-mctal-f4-n".into(),
            path: source_path.display().to_string(),
            sha256: snapshot.sha256.clone(),
            selector: BTreeMap::from([("tally_id".into(), serde_json::json!(tally_id))]),
            normalization: BTreeMap::from([
                ("source_rate_per_s".into(), serde_json::json!(source_rate)),
                ("source_units".into(), serde_json::json!("particle^-1")),
            ]),
            auxiliary_inputs: Vec::new(),
            metadata: BTreeMap::from([(
                "source_total_bin".into(),
                serde_json::json!(parsed.total_check.is_some()),
            )]),
        },
        energy_boundaries_eV: parsed.boundaries_eV,
        flux_units: FLUX_UNITS.into(),
        cell_count,
        geometry: None,
        energy_floor: parsed.energy_floor,
    };
    let footer = atomic_output(output_path, |output| {
        let mut writer = CanonicalWriter::new(output, header)?;
        for cell in parsed.cells {
            writer.write_cell(cell)?;
        }
        writer.finish(parsed.total_check)
    })?;
    Ok(ImportSummary {
        output: output_path.display().to_string(),
        sha256: sha256_file(output_path)?,
        source_sha256: snapshot.sha256,
        cells: cell_count,
        groups,
        flux_sum_over_cells: footer.flux_sum_over_cells,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn exact_grid_rebin_is_bit_identical() {
        let boundaries = [1.0, 2.0, 4.0, 8.0];
        let flux = [1.0, 2.0, 3.0];
        let result = rebin_equal_lethargy(&boundaries, &flux, &boundaries).unwrap();
        assert!(result.exact_grid);
        assert_eq!(result.flux_per_group, flux);
        assert_eq!(result.relative_closure, 0.0);
    }

    #[test]
    fn fispact_requires_a_nonblank_identifying_title() {
        let path = std::env::temp_dir().join(format!(
            "actinv-fispact-blank-title-{}.fluxes",
            std::process::id()
        ));
        std::fs::write(&path, "1.0\n2.0\n0.0\n\n").unwrap();
        let error = parse_fispact_fluxes(&path, 2).unwrap_err();
        std::fs::remove_file(path).unwrap();
        assert_eq!(error, "FISPACT fluxes identifying title is blank");
    }

    #[test]
    fn split_rebin_conserves_with_outside_flux() {
        let result = rebin_equal_lethargy(&[1.0, 4.0, 16.0], &[2.0, 6.0], &[2.0, 8.0]).unwrap();
        assert_eq!(result.flux_per_group.len(), 1);
        assert!((result.underflow - 1.0).abs() < 1e-15);
        assert!((result.flux_per_group[0] - 4.0).abs() < 1e-15);
        assert!((result.overflow - 3.0).abs() < 1e-15);
        assert!(result.relative_closure <= 1e-15);
    }

    #[test]
    fn meshtal_restores_i_fastest_canonical_order() {
        let text = r#" Mesh Tally Number        24
 neutron  mesh tally.

 Tally bin boundaries:
    X direction: 0.0 1.0 2.0
    Y direction: 0.0 1.0
    Z direction: 0.0 1.0 2.0
    Energy bin boundaries: 0.0 1.0E-6 2.0E-6

   Energy X Y Z Result Rel Error
 1.0E-6 0.5 0.5 0.5 1.0 0.1
 1.0E-6 0.5 0.5 1.5 2.0 0.2
 1.0E-6 1.5 0.5 0.5 3.0 0.3
 1.0E-6 1.5 0.5 1.5 4.0 0.4
 2.0E-6 0.5 0.5 0.5 10.0 0.01
 2.0E-6 0.5 0.5 1.5 20.0 0.02
 2.0E-6 1.5 0.5 0.5 30.0 0.03
 2.0E-6 1.5 0.5 1.5 40.0 0.04
 Total 0.5 0.5 0.5 11.0 0.01
 Total 0.5 0.5 1.5 22.0 0.02
 Total 1.5 0.5 0.5 33.0 0.03
 Total 1.5 0.5 1.5 44.0 0.04
"#;
        let lines: Vec<&str> = text.lines().collect();
        let parsed = parse_meshtal_block(&lines, 10.0, Some(0.5)).unwrap();
        assert_eq!(parsed.boundaries_eV, [0.5, 1.0, 2.0]);
        assert_eq!(parsed.geometry.dimension, [2, 1, 2]);
        assert_eq!(parsed.cells[0].id, "1,1,1");
        assert_eq!(parsed.cells[1].id, "2,1,1");
        assert_eq!(parsed.cells[2].id, "1,1,2");
        assert_eq!(parsed.cells[3].id, "2,1,2");
        assert_eq!(parsed.cells[0].flux_per_group, [10.0, 100.0]);
        assert_eq!(parsed.cells[1].flux_per_group, [30.0, 300.0]);
        assert_eq!(parsed.cells[2].flux_per_group, [20.0, 200.0]);
        assert_eq!(parsed.cells[3].flux_per_group, [40.0, 400.0]);
        assert_eq!(parsed.total_check, Some(0.0));
    }

    #[test]
    fn mctal_pairs_values_and_drops_checked_total_bin() {
        let text = r#"tally 4 -1 0 0
 1 0 0
f 2
 101 102
d 1
u 0
s 0
m 0
c 0
et 3
 1.0E-6 2.0E-6
t 0
vals
 1.0 0.1 2.0 0.2 3.0 0.3
 4.0 0.4 5.0 0.5 9.0 0.6
tfc 0 1 1 1 1 1 1 3 1
"#;
        let lines: Vec<&str> = text.lines().collect();
        let parsed = parse_mctal_block(&lines, 4, 10.0, Some(0.5)).unwrap();
        assert_eq!(parsed.boundaries_eV, [0.5, 1.0, 2.0]);
        assert_eq!(parsed.cells.len(), 2);
        assert_eq!(parsed.cells[0].id, "101");
        assert_eq!(parsed.cells[0].flux_per_group, [10.0, 20.0]);
        assert_eq!(parsed.cells[0].relative_error, Some(vec![0.1, 0.2]));
        assert_eq!(parsed.cells[1].id, "102");
        assert_eq!(parsed.cells[1].flux_per_group, [40.0, 50.0]);
        assert_eq!(parsed.total_check, Some(0.0));
    }

    #[test]
    fn mcnp_three_digit_exponent_is_supported() {
        assert_eq!(
            parse_mcnp_float("2.14182-103", "test").unwrap(),
            2.14182e-103
        );
        assert_eq!(parse_mcnp_float("1.0D+03", "test").unwrap(), 1000.0);
    }
}
