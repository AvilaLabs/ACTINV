//! Deterministic prepared activation-library artifacts.
//!
//! The source NPZ remains the provenance input. These files are disposable, content-bound
//! performance caches: a compact groupwise representation for arbitrary spectra and an exact
//! spectrum-collapsed representation for ordinary single-spectrum runs.

use crate::library::{
    self, ensure_eof, read_bounds, read_npy_header, read_rows, require_payload_size,
    sha256_verified_member, NpyDtype, ReactionLibrary, Row,
};
use sha2::{Digest, Sha256};
use std::collections::BTreeSet;
use std::fs::{File, OpenOptions};
use std::io::{BufReader, Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};

const PREPARED_MAGIC: &[u8; 8] = b"ACTPLB01";
const COLLAPSED_MAGIC: &[u8; 8] = b"ACTCOL01";
const SCHEMA_VERSION: u32 = 1;
const PREPARED_HEADER_BYTES: usize = 224;
const COLLAPSED_HEADER_BYTES: usize = 288;
const PREPARED_ROW_BYTES: usize = 40;
const COLLAPSED_ROW_BYTES: usize = 24;
const TRAILER_BYTES: usize = 32;
const PREPARED_ALGORITHM: &str =
    "actinv-prepared-library-1\nnpz-f64-spans-v1\nsource-row-order-v1\n";
const COLLAPSED_ALGORITHM: &str =
    "actinv-collapsed-spectrum-1\nopening-collapse-order-v1\nfission-spectrum-average-v1\n";
const LOCK_WAIT: std::time::Duration = std::time::Duration::from_secs(30);
const LOCK_POLL: std::time::Duration = std::time::Duration::from_millis(25);

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct PreparedSpan {
    pub source_row: usize,
    pub first_group: usize,
    pub value_count: usize,
    value_offset: usize,
}

/// Compact groupwise activation data. Leading and trailing positive-zero groups are implicit;
/// every value between the first and last nonzero bit pattern remains byte-exact.
#[derive(Clone, Debug)]
pub struct PreparedLibrary {
    rows: Vec<Row>,
    spans: Vec<PreparedSpan>,
    values: Vec<f64>,
    group_count: usize,
    boundaries_ev: Vec<f64>,
    source_row_count: usize,
}

impl PreparedLibrary {
    pub fn rows(&self) -> &[Row] {
        &self.rows
    }

    pub fn spans(&self) -> &[PreparedSpan] {
        &self.spans
    }

    pub fn values(&self) -> &[f64] {
        &self.values
    }

    pub fn group_count(&self) -> usize {
        self.group_count
    }

    pub fn boundaries_ev(&self) -> &[f64] {
        &self.boundaries_ev
    }

    pub fn source_row_count(&self) -> usize {
        self.source_row_count
    }

    /// Heap storage owned by this materialized selection, excluding allocator bookkeeping.
    pub fn materialized_bytes(&self) -> usize {
        self.rows.capacity() * std::mem::size_of::<Row>()
            + self.spans.capacity() * std::mem::size_of::<PreparedSpan>()
            + self.values.capacity() * std::mem::size_of::<f64>()
            + self.boundaries_ev.capacity() * std::mem::size_of::<f64>()
    }

    pub fn cross_section(&self, row: usize, group: usize) -> f64 {
        let span = self.spans[row];
        if group < span.first_group || group >= span.first_group + span.value_count {
            return 0.0;
        }
        self.values[span.value_offset + group - span.first_group]
    }
}

impl ReactionLibrary for PreparedLibrary {
    fn rows(&self) -> &[Row] {
        &self.rows
    }

    fn group_count(&self) -> usize {
        self.group_count
    }

    fn boundaries_ev(&self) -> &[f64] {
        &self.boundaries_ev
    }

    fn collapse_row(
        &self,
        row: usize,
        phi: &[f64],
        flux_denominator: f64,
        first_flux_group: usize,
        last_flux_group: usize,
    ) -> f64 {
        let mut numerator = 0.0;
        for (group, flux) in phi
            .iter()
            .enumerate()
            .take(last_flux_group)
            .skip(first_flux_group)
        {
            numerator += self.cross_section(row, group) * flux;
        }
        if flux_denominator > 0.0 {
            numerator / flux_denominator
        } else {
            0.0
        }
    }

    fn fission_average_energy_ev(&self, row: usize, phi: &[f64]) -> Result<Option<f64>, String> {
        library::fission_average_energy_ev(self.group_count, &self.boundaries_ev, phi, |group| {
            self.cross_section(row, group)
        })
    }
}

/// Exact one-spectrum activation data. The original rows and group boundaries remain present so
/// all validation, ledger, certificate, and fission-yield behavior is unchanged.
#[derive(Clone, Debug)]
pub struct CollapsedLibrary {
    rows: Vec<Row>,
    group_count: usize,
    boundaries_ev: Vec<f64>,
    flux: Vec<f64>,
    one_group_barns: Vec<f64>,
    fission_average_energy_ev: Vec<f64>,
    fission_average_present: Vec<bool>,
}

impl CollapsedLibrary {
    pub fn rows(&self) -> &[Row] {
        &self.rows
    }

    pub fn group_count(&self) -> usize {
        self.group_count
    }

    pub fn boundaries_ev(&self) -> &[f64] {
        &self.boundaries_ev
    }

    pub fn flux(&self) -> &[f64] {
        &self.flux
    }

    pub fn one_group_barns(&self) -> &[f64] {
        &self.one_group_barns
    }

    pub fn validate_flux(&self, phi: &[f64]) -> Result<(), String> {
        if phi.len() != self.flux.len()
            || phi
                .iter()
                .zip(&self.flux)
                .any(|(actual, cached)| actual.to_bits() != cached.to_bits())
        {
            return Err("collapsed activation cache does not match the run spectrum".into());
        }
        Ok(())
    }
}

impl ReactionLibrary for CollapsedLibrary {
    fn rows(&self) -> &[Row] {
        &self.rows
    }

    fn group_count(&self) -> usize {
        self.group_count
    }

    fn boundaries_ev(&self) -> &[f64] {
        &self.boundaries_ev
    }

    fn collapse_row(
        &self,
        row: usize,
        _phi: &[f64],
        _flux_denominator: f64,
        _first_flux_group: usize,
        _last_flux_group: usize,
    ) -> f64 {
        self.one_group_barns[row]
    }

    fn fission_average_energy_ev(&self, row: usize, _phi: &[f64]) -> Result<Option<f64>, String> {
        Ok(self.fission_average_present[row].then_some(self.fission_average_energy_ev[row]))
    }
}

#[derive(Clone, Copy, Debug)]
struct PreparedDescriptor {
    row: Row,
    first_group: usize,
    value_count: usize,
    value_index: usize,
}

#[derive(Clone, Debug)]
struct PreparedHeader {
    row_count: usize,
    group_count: usize,
    bounds_count: usize,
    rows_offset: u64,
    values_offset: u64,
    bounds_offset: u64,
    value_count: usize,
    dense_value_count: usize,
    payload_end: u64,
    artifact_len: u64,
    library_sha256: [u8; 32],
    index_sha256: [u8; 32],
}

#[derive(Clone, Debug)]
struct CollapsedHeader {
    row_count: usize,
    group_count: usize,
    bounds_count: usize,
    bounds_offset: u64,
    flux_offset: u64,
    rows_offset: u64,
    collapsed_offset: u64,
    fission_offset: u64,
    presence_offset: u64,
    payload_end: u64,
    artifact_len: u64,
    library_sha256: [u8; 32],
    index_sha256: [u8; 32],
    flux_sha256: [u8; 32],
    prepared_sha256: [u8; 32],
}

struct PreparedArchive {
    file: File,
    header: PreparedHeader,
    descriptors: Vec<PreparedDescriptor>,
    boundaries_ev: Vec<f64>,
    integrity_sha256: [u8; 32],
}

fn algorithm_sha256(label: &str) -> [u8; 32] {
    let digest = Sha256::digest(label.as_bytes());
    let mut output = [0u8; 32];
    output.copy_from_slice(&digest);
    output
}

fn decode_sha256(value: &str, name: &str) -> Result<[u8; 32], String> {
    if value.len() != 64 || !value.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return Err(format!("{name} must be exactly 64 hexadecimal digits"));
    }
    let mut output = [0u8; 32];
    for (position, slot) in output.iter_mut().enumerate() {
        *slot = u8::from_str_radix(&value[position * 2..position * 2 + 2], 16)
            .map_err(|_| format!("{name} is not hexadecimal"))?;
    }
    Ok(output)
}

fn encode_sha256(value: &[u8; 32]) -> String {
    value.iter().map(|byte| format!("{byte:02x}")).collect()
}

fn file_sha256(path: &Path) -> Result<[u8; 32], String> {
    let mut file = File::open(path)
        .map_err(|error| format!("cannot open source {}: {error}", path.display()))?;
    let mut hash = Sha256::new();
    let mut buffer = vec![0u8; 1024 * 1024];
    loop {
        let count = file
            .read(&mut buffer)
            .map_err(|error| format!("cannot hash source {}: {error}", path.display()))?;
        if count == 0 {
            break;
        }
        hash.update(&buffer[..count]);
    }
    let mut output = [0u8; 32];
    output.copy_from_slice(&hash.finalize());
    Ok(output)
}

fn verify_source_identity(path: &Path, expected: &[u8; 32]) -> Result<(), String> {
    let actual = file_sha256(path)?;
    if &actual != expected {
        return Err(format!(
            "activation library {} changed before or during prepared-cache creation: expected {}, computed {}",
            path.display(),
            encode_sha256(expected),
            encode_sha256(&actual)
        ));
    }
    Ok(())
}

#[derive(PartialEq, Eq)]
struct SourceStamp {
    bytes: u64,
    modified_ns: Option<u128>,
}

fn source_stamp(path: &Path) -> Result<SourceStamp, String> {
    let metadata = std::fs::metadata(path)
        .map_err(|error| format!("cannot inspect source {}: {error}", path.display()))?;
    if !metadata.is_file() {
        return Err(format!(
            "activation library {} is not a regular file",
            path.display()
        ));
    }
    Ok(SourceStamp {
        bytes: metadata.len(),
        modified_ns: metadata
            .modified()
            .ok()
            .and_then(|value| value.duration_since(std::time::UNIX_EPOCH).ok())
            .map(|value| value.as_nanos()),
    })
}

fn flux_sha256(phi: &[f64]) -> [u8; 32] {
    let mut hash = Sha256::new();
    for value in phi {
        hash.update(value.to_le_bytes());
    }
    let mut output = [0u8; 32];
    output.copy_from_slice(&hash.finalize());
    output
}

fn usize_from_u64(value: u64, field: &str) -> Result<usize, String> {
    usize::try_from(value).map_err(|_| format!("prepared artifact {field} exceeds usize"))
}

fn u64_from_usize(value: usize, field: &str) -> Result<u64, String> {
    u64::try_from(value).map_err(|_| format!("prepared artifact {field} exceeds u64"))
}

fn checked_add(left: u64, right: u64, field: &str) -> Result<u64, String> {
    left.checked_add(right)
        .ok_or_else(|| format!("prepared artifact {field} overflows"))
}

fn checked_mul(left: u64, right: u64, field: &str) -> Result<u64, String> {
    left.checked_mul(right)
        .ok_or_else(|| format!("prepared artifact {field} overflows"))
}

struct HeaderReader<'a> {
    bytes: &'a [u8],
    position: usize,
}

impl<'a> HeaderReader<'a> {
    fn new(bytes: &'a [u8]) -> Self {
        Self { bytes, position: 0 }
    }

    fn take<const N: usize>(&mut self, field: &str) -> Result<[u8; N], String> {
        let end = self
            .position
            .checked_add(N)
            .ok_or_else(|| format!("prepared artifact {field} position overflows"))?;
        let slice = self
            .bytes
            .get(self.position..end)
            .ok_or_else(|| format!("prepared artifact header truncates {field}"))?;
        self.position = end;
        let mut output = [0u8; N];
        output.copy_from_slice(slice);
        Ok(output)
    }

    fn u32(&mut self, field: &str) -> Result<u32, String> {
        Ok(u32::from_le_bytes(self.take(field)?))
    }

    fn i32(&mut self, field: &str) -> Result<i32, String> {
        Ok(i32::from_le_bytes(self.take(field)?))
    }

    fn u64(&mut self, field: &str) -> Result<u64, String> {
        Ok(u64::from_le_bytes(self.take(field)?))
    }

    fn zero_tail(&mut self) -> Result<(), String> {
        if self.bytes[self.position..].iter().any(|byte| *byte != 0) {
            return Err("prepared artifact reserved header bytes are nonzero".into());
        }
        self.position = self.bytes.len();
        Ok(())
    }
}

fn push_u32(output: &mut Vec<u8>, value: u32) {
    output.extend_from_slice(&value.to_le_bytes());
}

fn push_u64(output: &mut Vec<u8>, value: u64) {
    output.extend_from_slice(&value.to_le_bytes());
}

fn finish_header(output: &mut Vec<u8>, expected: usize) {
    assert!(output.len() <= expected);
    output.resize(expected, 0);
}

fn read_fixed_header(file: &mut File, bytes: usize, path: &Path) -> Result<Vec<u8>, String> {
    file.seek(SeekFrom::Start(0))
        .map_err(|error| format!("cannot seek {}: {error}", path.display()))?;
    let mut header = vec![0u8; bytes];
    file.read_exact(&mut header)
        .map_err(|error| format!("cannot read {} header: {error}", path.display()))?;
    Ok(header)
}

fn verify_regular_file(path: &Path) -> Result<File, String> {
    let metadata = std::fs::symlink_metadata(path)
        .map_err(|error| format!("cannot inspect {}: {error}", path.display()))?;
    if metadata.file_type().is_symlink() || !metadata.is_file() {
        return Err(format!(
            "prepared cache artifact {} is not a regular file",
            path.display()
        ));
    }
    File::open(path).map_err(|error| format!("cannot open {}: {error}", path.display()))
}

fn verify_integrity(
    file: &mut File,
    path: &Path,
    payload_end: u64,
    artifact_len: u64,
) -> Result<[u8; 32], String> {
    let actual_len = file
        .metadata()
        .map_err(|error| format!("cannot stat {}: {error}", path.display()))?
        .len();
    if actual_len != artifact_len {
        return Err(format!(
            "prepared cache artifact {} length is {actual_len}, declared {artifact_len}",
            path.display()
        ));
    }
    if checked_add(payload_end, TRAILER_BYTES as u64, "integrity trailer")? != artifact_len {
        return Err(format!(
            "prepared cache artifact {} has an invalid integrity-trailer offset",
            path.display()
        ));
    }
    file.seek(SeekFrom::Start(payload_end))
        .map_err(|error| format!("cannot seek {} trailer: {error}", path.display()))?;
    let mut recorded = [0u8; 32];
    file.read_exact(&mut recorded)
        .map_err(|error| format!("cannot read {} integrity trailer: {error}", path.display()))?;
    file.seek(SeekFrom::Start(0))
        .map_err(|error| format!("cannot seek {}: {error}", path.display()))?;
    let mut remaining = payload_end;
    let mut buffer = vec![0u8; 1024 * 1024];
    let mut hash = Sha256::new();
    while remaining > 0 {
        let count = usize::try_from(remaining.min(buffer.len() as u64))
            .map_err(|_| "integrity chunk exceeds usize")?;
        file.read_exact(&mut buffer[..count])
            .map_err(|error| format!("cannot hash {}: {error}", path.display()))?;
        hash.update(&buffer[..count]);
        remaining -= count as u64;
    }
    let computed = hash.finalize();
    if computed.as_slice() != recorded {
        return Err(format!(
            "prepared cache artifact {} failed its SHA-256 integrity trailer",
            path.display()
        ));
    }
    Ok(recorded)
}

fn validate_boundaries(boundaries: &[f64]) -> Result<(), String> {
    if boundaries.len() < 2
        || boundaries
            .iter()
            .any(|value| !value.is_finite() || *value <= 0.0)
        || boundaries.windows(2).any(|pair| pair[1] <= pair[0])
    {
        return Err(
            "prepared activation boundaries must be finite, positive and increasing".into(),
        );
    }
    Ok(())
}

fn parse_prepared_header(bytes: &[u8], path: &Path) -> Result<PreparedHeader, String> {
    let mut reader = HeaderReader::new(bytes);
    if &reader.take::<8>("magic")? != PREPARED_MAGIC {
        return Err(format!(
            "prepared cache artifact {} has the wrong prepared-library magic",
            path.display()
        ));
    }
    let version = reader.u32("schema version")?;
    if version != SCHEMA_VERSION {
        return Err(format!(
            "prepared cache artifact {} has prepared-library schema version {version}, expected {SCHEMA_VERSION}",
            path.display()
        ));
    }
    let header_bytes = reader.u32("header length")? as usize;
    if header_bytes != PREPARED_HEADER_BYTES {
        return Err(format!(
            "prepared cache artifact {} declares a {header_bytes}-byte prepared header, expected {PREPARED_HEADER_BYTES}",
            path.display()
        ));
    }
    let row_count = usize_from_u64(reader.u64("row count")?, "row count")?;
    let group_count = usize_from_u64(reader.u64("group count")?, "group count")?;
    let bounds_count = usize_from_u64(reader.u64("boundary count")?, "boundary count")?;
    let descriptor_bytes = reader.u32("row descriptor size")? as usize;
    if descriptor_bytes != PREPARED_ROW_BYTES {
        return Err(format!(
            "prepared cache artifact {} has {descriptor_bytes}-byte prepared row descriptors, expected {PREPARED_ROW_BYTES}",
            path.display()
        ));
    }
    if reader.u32("flags")? != 0 {
        return Err(format!(
            "prepared cache artifact {} has unsupported prepared flags",
            path.display()
        ));
    }
    let rows_offset = reader.u64("row offset")?;
    let values_offset = reader.u64("value offset")?;
    let bounds_offset = reader.u64("boundary offset")?;
    let value_count = usize_from_u64(reader.u64("value count")?, "value count")?;
    let dense_value_count = usize_from_u64(reader.u64("dense value count")?, "dense value count")?;
    let payload_end = reader.u64("payload end")?;
    let artifact_len = reader.u64("artifact length")?;
    let library_sha256 = reader.take("source-library SHA-256")?;
    let index_sha256 = reader.take("source-index SHA-256")?;
    let algorithm = reader.take::<32>("prepared algorithm SHA-256")?;
    if algorithm != algorithm_sha256(PREPARED_ALGORITHM) {
        return Err(format!(
            "prepared cache artifact {} was created by an incompatible prepared-library algorithm",
            path.display()
        ));
    }
    reader.zero_tail()?;
    if row_count == 0 || group_count == 0 || bounds_count != group_count + 1 {
        return Err(format!(
            "prepared cache artifact {} has invalid row/group/boundary counts",
            path.display()
        ));
    }
    let expected_dense = row_count
        .checked_mul(group_count)
        .ok_or("prepared dense dimensions overflow")?;
    if dense_value_count != expected_dense {
        return Err(format!(
            "prepared cache artifact {} dense value count is {dense_value_count}, expected {expected_dense}",
            path.display()
        ));
    }
    let expected_rows_offset = PREPARED_HEADER_BYTES as u64;
    let expected_values_offset = checked_add(
        expected_rows_offset,
        checked_mul(row_count as u64, PREPARED_ROW_BYTES as u64, "row payload")?,
        "value offset",
    )?;
    let expected_bounds_offset = checked_add(
        expected_values_offset,
        checked_mul(value_count as u64, 8, "value payload")?,
        "boundary offset",
    )?;
    let expected_payload_end = checked_add(
        expected_bounds_offset,
        checked_mul(bounds_count as u64, 8, "boundary payload")?,
        "payload end",
    )?;
    if rows_offset != expected_rows_offset
        || values_offset != expected_values_offset
        || bounds_offset != expected_bounds_offset
        || payload_end != expected_payload_end
        || artifact_len != checked_add(payload_end, TRAILER_BYTES as u64, "artifact length")?
    {
        return Err(format!(
            "prepared cache artifact {} has inconsistent prepared-library offsets or lengths",
            path.display()
        ));
    }
    Ok(PreparedHeader {
        row_count,
        group_count,
        bounds_count,
        rows_offset,
        values_offset,
        bounds_offset,
        value_count,
        dense_value_count,
        payload_end,
        artifact_len,
        library_sha256,
        index_sha256,
    })
}

fn read_prepared_descriptors(
    file: &mut File,
    header: &PreparedHeader,
    path: &Path,
) -> Result<Vec<PreparedDescriptor>, String> {
    file.seek(SeekFrom::Start(header.rows_offset))
        .map_err(|error| format!("cannot seek {} rows: {error}", path.display()))?;
    let mut descriptors = Vec::new();
    descriptors
        .try_reserve_exact(header.row_count)
        .map_err(|error| format!("cannot allocate prepared row descriptors: {error}"))?;
    let mut expected_value_index = 0usize;
    let mut raw = [0u8; PREPARED_ROW_BYTES];
    for source_row in 0..header.row_count {
        file.read_exact(&mut raw).map_err(|error| {
            format!(
                "cannot read {} row descriptor {source_row}: {error}",
                path.display()
            )
        })?;
        let mut reader = HeaderReader::new(&raw);
        let target = usize_from_u64(reader.u64("target index")?, "target index")?;
        let row = Row {
            target,
            mt: reader.i32("MT")?,
            zap: reader.i32("ZAP")?,
            lfs: reader.i32("LFS")?,
            lmf: reader.i32("LMF")?,
        };
        let first_group = reader.u32("first group")? as usize;
        let value_count = reader.u32("span value count")? as usize;
        let value_index = usize_from_u64(reader.u64("span value index")?, "span value index")?;
        if value_index != expected_value_index {
            return Err(format!(
                "prepared cache artifact {} row {source_row} has a gap or overlap in its value spans",
                path.display()
            ));
        }
        if value_count == 0 {
            if first_group != 0 {
                return Err(format!(
                    "prepared cache artifact {} empty row {source_row} has nonzero first group",
                    path.display()
                ));
            }
        } else if first_group
            .checked_add(value_count)
            .is_none_or(|end| end > header.group_count)
        {
            return Err(format!(
                "prepared cache artifact {} row {source_row} span exceeds its group count",
                path.display()
            ));
        }
        expected_value_index = expected_value_index
            .checked_add(value_count)
            .ok_or("prepared value span count overflows")?;
        descriptors.push(PreparedDescriptor {
            row,
            first_group,
            value_count,
            value_index,
        });
    }
    if expected_value_index != header.value_count {
        return Err(format!(
            "prepared cache artifact {} row spans account for {expected_value_index} values, declared {}",
            path.display(),
            header.value_count
        ));
    }
    Ok(descriptors)
}

fn read_f64_at(
    file: &mut File,
    offset: u64,
    count: usize,
    path: &Path,
    field: &str,
) -> Result<Vec<f64>, String> {
    file.seek(SeekFrom::Start(offset))
        .map_err(|error| format!("cannot seek {} {field}: {error}", path.display()))?;
    let values = library::read_f64_values(file, count)
        .map_err(|error| format!("cannot read {} {field}: {error}", path.display()))?;
    if values.iter().any(|value| !value.is_finite()) {
        return Err(format!(
            "prepared cache artifact {} {field} contains a nonfinite value",
            path.display()
        ));
    }
    Ok(values)
}

fn open_prepared_archive(
    path: &Path,
    expected_library_sha256: &[u8; 32],
    expected_index_sha256: &[u8; 32],
) -> Result<PreparedArchive, String> {
    let mut file = verify_regular_file(path)?;
    let header_bytes = read_fixed_header(&mut file, PREPARED_HEADER_BYTES, path)?;
    let header = parse_prepared_header(&header_bytes, path)?;
    if &header.library_sha256 != expected_library_sha256 {
        return Err(format!(
            "prepared cache artifact {} belongs to source library {}, expected {}",
            path.display(),
            encode_sha256(&header.library_sha256),
            encode_sha256(expected_library_sha256)
        ));
    }
    if &header.index_sha256 != expected_index_sha256 {
        return Err(format!(
            "prepared cache artifact {} belongs to source index {}, expected {}",
            path.display(),
            encode_sha256(&header.index_sha256),
            encode_sha256(expected_index_sha256)
        ));
    }
    let integrity_sha256 =
        verify_integrity(&mut file, path, header.payload_end, header.artifact_len)?;
    let descriptors = read_prepared_descriptors(&mut file, &header, path)?;
    let boundaries_ev = read_f64_at(
        &mut file,
        header.bounds_offset,
        header.bounds_count,
        path,
        "boundaries",
    )?;
    validate_boundaries(&boundaries_ev)?;
    Ok(PreparedArchive {
        file,
        header,
        descriptors,
        boundaries_ev,
        integrity_sha256,
    })
}

fn parse_collapsed_header(bytes: &[u8], path: &Path) -> Result<CollapsedHeader, String> {
    let mut reader = HeaderReader::new(bytes);
    if &reader.take::<8>("magic")? != COLLAPSED_MAGIC {
        return Err(format!(
            "prepared cache artifact {} has the wrong collapsed-spectrum magic",
            path.display()
        ));
    }
    let version = reader.u32("schema version")?;
    if version != SCHEMA_VERSION {
        return Err(format!(
            "prepared cache artifact {} has collapsed schema version {version}, expected {SCHEMA_VERSION}",
            path.display()
        ));
    }
    let header_bytes = reader.u32("header length")? as usize;
    if header_bytes != COLLAPSED_HEADER_BYTES {
        return Err(format!(
            "prepared cache artifact {} declares a {header_bytes}-byte collapsed header, expected {COLLAPSED_HEADER_BYTES}",
            path.display()
        ));
    }
    let row_count = usize_from_u64(reader.u64("row count")?, "row count")?;
    let group_count = usize_from_u64(reader.u64("group count")?, "group count")?;
    let bounds_count = usize_from_u64(reader.u64("boundary count")?, "boundary count")?;
    if reader.u32("row descriptor size")? as usize != COLLAPSED_ROW_BYTES {
        return Err(format!(
            "prepared cache artifact {} has an invalid collapsed row descriptor size",
            path.display()
        ));
    }
    if reader.u32("flags")? != 0 {
        return Err(format!(
            "prepared cache artifact {} has unsupported collapsed flags",
            path.display()
        ));
    }
    let bounds_offset = reader.u64("boundary offset")?;
    let flux_offset = reader.u64("flux offset")?;
    let rows_offset = reader.u64("row offset")?;
    let collapsed_offset = reader.u64("collapsed-value offset")?;
    let fission_offset = reader.u64("fission-energy offset")?;
    let presence_offset = reader.u64("fission-presence offset")?;
    let payload_end = reader.u64("payload end")?;
    let artifact_len = reader.u64("artifact length")?;
    let library_sha256 = reader.take("source-library SHA-256")?;
    let index_sha256 = reader.take("source-index SHA-256")?;
    let flux_sha256 = reader.take("flux SHA-256")?;
    let prepared_sha256 = reader.take("prepared-library SHA-256")?;
    let algorithm = reader.take::<32>("collapsed algorithm SHA-256")?;
    if algorithm != algorithm_sha256(COLLAPSED_ALGORITHM) {
        return Err(format!(
            "prepared cache artifact {} was created by an incompatible collapsed-spectrum algorithm",
            path.display()
        ));
    }
    reader.zero_tail()?;
    if row_count == 0 || group_count == 0 || bounds_count != group_count + 1 {
        return Err(format!(
            "prepared cache artifact {} has invalid collapsed row/group/boundary counts",
            path.display()
        ));
    }
    let expected_bounds = COLLAPSED_HEADER_BYTES as u64;
    let expected_flux = checked_add(
        expected_bounds,
        checked_mul(bounds_count as u64, 8, "collapsed boundary payload")?,
        "collapsed flux offset",
    )?;
    let expected_rows = checked_add(
        expected_flux,
        checked_mul(group_count as u64, 8, "collapsed flux payload")?,
        "collapsed row offset",
    )?;
    let expected_collapsed = checked_add(
        expected_rows,
        checked_mul(
            row_count as u64,
            COLLAPSED_ROW_BYTES as u64,
            "collapsed rows",
        )?,
        "collapsed values offset",
    )?;
    let expected_fission = checked_add(
        expected_collapsed,
        checked_mul(row_count as u64, 8, "collapsed values")?,
        "fission-energy offset",
    )?;
    let expected_presence = checked_add(
        expected_fission,
        checked_mul(row_count as u64, 8, "fission-energy values")?,
        "fission-presence offset",
    )?;
    let expected_end = checked_add(expected_presence, row_count as u64, "collapsed payload end")?;
    if bounds_offset != expected_bounds
        || flux_offset != expected_flux
        || rows_offset != expected_rows
        || collapsed_offset != expected_collapsed
        || fission_offset != expected_fission
        || presence_offset != expected_presence
        || payload_end != expected_end
        || artifact_len != checked_add(payload_end, TRAILER_BYTES as u64, "artifact length")?
    {
        return Err(format!(
            "prepared cache artifact {} has inconsistent collapsed-spectrum offsets or lengths",
            path.display()
        ));
    }
    Ok(CollapsedHeader {
        row_count,
        group_count,
        bounds_count,
        bounds_offset,
        flux_offset,
        rows_offset,
        collapsed_offset,
        fission_offset,
        presence_offset,
        payload_end,
        artifact_len,
        library_sha256,
        index_sha256,
        flux_sha256,
        prepared_sha256,
    })
}

fn prepared_header_bytes(header: &PreparedHeader) -> Result<Vec<u8>, String> {
    let mut output = Vec::with_capacity(PREPARED_HEADER_BYTES);
    output.extend_from_slice(PREPARED_MAGIC);
    push_u32(&mut output, SCHEMA_VERSION);
    push_u32(&mut output, PREPARED_HEADER_BYTES as u32);
    push_u64(&mut output, u64_from_usize(header.row_count, "row count")?);
    push_u64(
        &mut output,
        u64_from_usize(header.group_count, "group count")?,
    );
    push_u64(
        &mut output,
        u64_from_usize(header.bounds_count, "boundary count")?,
    );
    push_u32(&mut output, PREPARED_ROW_BYTES as u32);
    push_u32(&mut output, 0);
    push_u64(&mut output, header.rows_offset);
    push_u64(&mut output, header.values_offset);
    push_u64(&mut output, header.bounds_offset);
    push_u64(
        &mut output,
        u64_from_usize(header.value_count, "value count")?,
    );
    push_u64(
        &mut output,
        u64_from_usize(header.dense_value_count, "dense value count")?,
    );
    push_u64(&mut output, header.payload_end);
    push_u64(&mut output, header.artifact_len);
    output.extend_from_slice(&header.library_sha256);
    output.extend_from_slice(&header.index_sha256);
    output.extend_from_slice(&algorithm_sha256(PREPARED_ALGORITHM));
    finish_header(&mut output, PREPARED_HEADER_BYTES);
    Ok(output)
}

fn collapsed_header_bytes(header: &CollapsedHeader) -> Result<Vec<u8>, String> {
    let mut output = Vec::with_capacity(COLLAPSED_HEADER_BYTES);
    output.extend_from_slice(COLLAPSED_MAGIC);
    push_u32(&mut output, SCHEMA_VERSION);
    push_u32(&mut output, COLLAPSED_HEADER_BYTES as u32);
    push_u64(&mut output, u64_from_usize(header.row_count, "row count")?);
    push_u64(
        &mut output,
        u64_from_usize(header.group_count, "group count")?,
    );
    push_u64(
        &mut output,
        u64_from_usize(header.bounds_count, "boundary count")?,
    );
    push_u32(&mut output, COLLAPSED_ROW_BYTES as u32);
    push_u32(&mut output, 0);
    push_u64(&mut output, header.bounds_offset);
    push_u64(&mut output, header.flux_offset);
    push_u64(&mut output, header.rows_offset);
    push_u64(&mut output, header.collapsed_offset);
    push_u64(&mut output, header.fission_offset);
    push_u64(&mut output, header.presence_offset);
    push_u64(&mut output, header.payload_end);
    push_u64(&mut output, header.artifact_len);
    output.extend_from_slice(&header.library_sha256);
    output.extend_from_slice(&header.index_sha256);
    output.extend_from_slice(&header.flux_sha256);
    output.extend_from_slice(&header.prepared_sha256);
    output.extend_from_slice(&algorithm_sha256(COLLAPSED_ALGORITHM));
    finish_header(&mut output, COLLAPSED_HEADER_BYTES);
    Ok(output)
}

fn write_zeros(writer: &mut impl Write, bytes: u64) -> Result<(), String> {
    let buffer = [0u8; 64 * 1024];
    let mut remaining = bytes;
    while remaining > 0 {
        let count = usize::try_from(remaining.min(buffer.len() as u64))
            .map_err(|_| "zero-fill chunk exceeds usize")?;
        writer
            .write_all(&buffer[..count])
            .map_err(|error| format!("cannot write prepared placeholder: {error}"))?;
        remaining -= count as u64;
    }
    Ok(())
}

fn append_integrity_trailer(
    file: &mut File,
    path: &Path,
    payload_end: u64,
) -> Result<[u8; 32], String> {
    file.flush()
        .map_err(|error| format!("cannot flush {}: {error}", path.display()))?;
    file.set_len(payload_end)
        .map_err(|error| format!("cannot size {}: {error}", path.display()))?;
    file.seek(SeekFrom::Start(0))
        .map_err(|error| format!("cannot seek {}: {error}", path.display()))?;
    let mut remaining = payload_end;
    let mut buffer = vec![0u8; 1024 * 1024];
    let mut hash = Sha256::new();
    while remaining > 0 {
        let count = usize::try_from(remaining.min(buffer.len() as u64))
            .map_err(|_| "integrity chunk exceeds usize")?;
        file.read_exact(&mut buffer[..count])
            .map_err(|error| format!("cannot hash {}: {error}", path.display()))?;
        hash.update(&buffer[..count]);
        remaining -= count as u64;
    }
    let digest = hash.finalize();
    let mut output = [0u8; 32];
    output.copy_from_slice(&digest);
    file.seek(SeekFrom::Start(payload_end))
        .map_err(|error| format!("cannot seek {} trailer: {error}", path.display()))?;
    file.write_all(&output)
        .map_err(|error| format!("cannot write {} integrity trailer: {error}", path.display()))?;
    file.sync_all()
        .map_err(|error| format!("cannot sync {}: {error}", path.display()))?;
    Ok(output)
}

fn write_prepared_descriptor(
    writer: &mut impl Write,
    descriptor: &PreparedDescriptor,
) -> Result<(), String> {
    writer
        .write_all(&u64_from_usize(descriptor.row.target, "target index")?.to_le_bytes())
        .map_err(|error| error.to_string())?;
    for value in [
        descriptor.row.mt,
        descriptor.row.zap,
        descriptor.row.lfs,
        descriptor.row.lmf,
    ] {
        writer
            .write_all(&value.to_le_bytes())
            .map_err(|error| error.to_string())?;
    }
    writer
        .write_all(
            &u32::try_from(descriptor.first_group)
                .map_err(|_| "prepared first group exceeds u32")?
                .to_le_bytes(),
        )
        .map_err(|error| error.to_string())?;
    writer
        .write_all(
            &u32::try_from(descriptor.value_count)
                .map_err(|_| "prepared span value count exceeds u32")?
                .to_le_bytes(),
        )
        .map_err(|error| error.to_string())?;
    writer
        .write_all(&u64_from_usize(descriptor.value_index, "value index")?.to_le_bytes())
        .map_err(|error| error.to_string())
}

fn write_collapsed_row(writer: &mut impl Write, row: &Row) -> Result<(), String> {
    writer
        .write_all(&u64_from_usize(row.target, "target index")?.to_le_bytes())
        .map_err(|error| error.to_string())?;
    for value in [row.mt, row.zap, row.lfs, row.lmf] {
        writer
            .write_all(&value.to_le_bytes())
            .map_err(|error| error.to_string())?;
    }
    Ok(())
}

fn build_prepared_artifact(
    source_npz: &str,
    output: &Path,
    library_sha256: [u8; 32],
    index_sha256: [u8; 32],
) -> Result<(), String> {
    let mut archive = library::open_npz(source_npz)?;
    let rows = read_rows(&mut archive)?;
    if rows.is_empty() {
        return Err("activation library has no rows".into());
    }
    let (sig_reader, member_size) = sha256_verified_member(&mut archive, "sig.npy")?;
    let mut sig_reader = BufReader::with_capacity(256 * 1024, sig_reader);
    let sig_header = read_npy_header(&mut sig_reader)?;
    if sig_header.dtype != NpyDtype::F64
        || sig_header.shape.len() != 2
        || sig_header.shape[0] != rows.len()
    {
        return Err(format!(
            "sig must have dtype <f8 and shape ({}, G), got {sig_header:?}",
            rows.len()
        ));
    }
    require_payload_size(&sig_header, member_size, "sig.npy")?;
    let group_count = sig_header.shape[1];
    if group_count == 0 {
        return Err("activation library has zero energy groups".into());
    }
    let rows_offset = PREPARED_HEADER_BYTES as u64;
    let values_offset = checked_add(
        rows_offset,
        checked_mul(rows.len() as u64, PREPARED_ROW_BYTES as u64, "row payload")?,
        "value offset",
    )?;
    let mut file = OpenOptions::new()
        .read(true)
        .write(true)
        .create_new(true)
        .open(output)
        .map_err(|error| format!("cannot create {}: {error}", output.display()))?;
    write_zeros(&mut file, values_offset)?;
    let row_bytes = group_count
        .checked_mul(8)
        .ok_or("prepared source row size overflows")?;
    let mut raw_row = vec![0u8; row_bytes];
    let mut descriptors = Vec::new();
    descriptors
        .try_reserve_exact(rows.len())
        .map_err(|error| format!("cannot allocate prepared row descriptors: {error}"))?;
    let mut value_count = 0usize;
    for (source_row, row) in rows.iter().copied().enumerate() {
        sig_reader
            .read_exact(&mut raw_row)
            .map_err(|error| format!("truncated sig.npy row {source_row}: {error}"))?;
        let mut first = None;
        let mut last = None;
        for (group, encoded) in raw_row.as_chunks::<8>().0.iter().enumerate() {
            let value = f64::from_le_bytes(*encoded);
            if !value.is_finite() || value < 0.0 {
                return Err(format!(
                    "sig.npy contains a nonfinite or negative cross section at row {source_row}, group {group}"
                ));
            }
            // Preserve negative zero and every other non-positive-zero bit pattern exactly.
            if value.to_bits() != 0 {
                first.get_or_insert(group);
                last = Some(group + 1);
            }
        }
        let (first_group, span_count) = match (first, last) {
            (Some(first_group), Some(last_group)) => (first_group, last_group - first_group),
            (None, None) => (0, 0),
            _ => unreachable!("first and last nonzero groups are recorded together"),
        };
        descriptors.push(PreparedDescriptor {
            row,
            first_group,
            value_count: span_count,
            value_index: value_count,
        });
        if span_count > 0 {
            file.write_all(&raw_row[first_group * 8..(first_group + span_count) * 8])
                .map_err(|error| format!("cannot write {} values: {error}", output.display()))?;
            value_count = value_count
                .checked_add(span_count)
                .ok_or("prepared value count overflows")?;
        }
    }
    ensure_eof(&mut sig_reader, "sig.npy")?;
    drop(sig_reader);
    let boundaries_ev = read_bounds(&mut archive, group_count)?;
    validate_boundaries(&boundaries_ev)?;
    let bounds_offset = checked_add(
        values_offset,
        checked_mul(value_count as u64, 8, "value payload")?,
        "boundary offset",
    )?;
    file.seek(SeekFrom::Start(bounds_offset))
        .map_err(|error| format!("cannot seek {} boundaries: {error}", output.display()))?;
    for boundary in &boundaries_ev {
        file.write_all(&boundary.to_le_bytes())
            .map_err(|error| format!("cannot write {} boundaries: {error}", output.display()))?;
    }
    let payload_end = checked_add(
        bounds_offset,
        checked_mul(boundaries_ev.len() as u64, 8, "boundary payload")?,
        "payload end",
    )?;
    let header = PreparedHeader {
        row_count: rows.len(),
        group_count,
        bounds_count: boundaries_ev.len(),
        rows_offset,
        values_offset,
        bounds_offset,
        value_count,
        dense_value_count: sig_header.elements,
        payload_end,
        artifact_len: checked_add(payload_end, TRAILER_BYTES as u64, "artifact length")?,
        library_sha256,
        index_sha256,
    };
    file.seek(SeekFrom::Start(0))
        .map_err(|error| format!("cannot seek {} header: {error}", output.display()))?;
    file.write_all(&prepared_header_bytes(&header)?)
        .map_err(|error| format!("cannot write {} header: {error}", output.display()))?;
    file.seek(SeekFrom::Start(rows_offset))
        .map_err(|error| format!("cannot seek {} rows: {error}", output.display()))?;
    for descriptor in &descriptors {
        write_prepared_descriptor(&mut file, descriptor).map_err(|error| {
            format!("cannot write {} row descriptor: {error}", output.display())
        })?;
    }
    append_integrity_trailer(&mut file, output, payload_end)?;
    Ok(())
}

fn build_collapsed_artifact(
    prepared_path: &Path,
    output: &Path,
    library_sha256: [u8; 32],
    index_sha256: [u8; 32],
    phi: &[f64],
) -> Result<(), String> {
    let mut prepared = open_prepared_archive(prepared_path, &library_sha256, &index_sha256)?;
    if phi.len() != prepared.header.group_count {
        return Err(format!(
            "spectrum has {} groups but prepared activation library has {}",
            phi.len(),
            prepared.header.group_count
        ));
    }
    if phi.iter().any(|value| !value.is_finite() || *value < 0.0) {
        return Err("prepared spectrum fluxes must be finite and nonnegative".into());
    }
    let mut flux_denominator = 0.0;
    for flux in phi {
        flux_denominator += *flux;
    }
    let first_flux_group = phi
        .iter()
        .position(|flux| *flux != 0.0)
        .unwrap_or(phi.len());
    let last_flux_group = phi
        .iter()
        .rposition(|flux| *flux != 0.0)
        .map(|group| group + 1)
        .unwrap_or(first_flux_group);

    prepared
        .file
        .seek(SeekFrom::Start(prepared.header.values_offset))
        .map_err(|error| format!("cannot seek {} values: {error}", prepared_path.display()))?;
    let mut raw_values = Vec::new();
    let mut one_group_barns = Vec::new();
    let mut fission_average_energy_ev = Vec::new();
    let mut fission_average_present = Vec::new();
    for values in [&mut one_group_barns, &mut fission_average_energy_ev] {
        values
            .try_reserve_exact(prepared.header.row_count)
            .map_err(|error| format!("cannot allocate collapsed activation values: {error}"))?;
    }
    fission_average_present
        .try_reserve_exact(prepared.header.row_count)
        .map_err(|error| format!("cannot allocate fission-energy presence values: {error}"))?;
    for (source_row, descriptor) in prepared.descriptors.iter().enumerate() {
        let value_bytes = descriptor
            .value_count
            .checked_mul(8)
            .ok_or("prepared row value bytes overflow")?;
        raw_values.resize(value_bytes, 0);
        prepared.file.read_exact(&mut raw_values).map_err(|error| {
            format!(
                "cannot read {} span values for row {source_row}: {error}",
                prepared_path.display()
            )
        })?;
        for (local, encoded) in raw_values.as_chunks::<8>().0.iter().enumerate() {
            let value = f64::from_le_bytes(*encoded);
            if !value.is_finite() || value < 0.0 {
                return Err(format!(
                    "prepared cache artifact {} has a nonfinite or negative value at row {source_row}, span index {local}",
                    prepared_path.display()
                ));
            }
        }
        let cross_section = |group: usize| {
            if group < descriptor.first_group
                || group >= descriptor.first_group + descriptor.value_count
            {
                return 0.0;
            }
            let local = group - descriptor.first_group;
            let mut encoded = [0u8; 8];
            encoded.copy_from_slice(&raw_values[local * 8..local * 8 + 8]);
            f64::from_le_bytes(encoded)
        };
        let mut numerator = 0.0;
        for (group, flux) in phi
            .iter()
            .enumerate()
            .take(last_flux_group)
            .skip(first_flux_group)
        {
            numerator += cross_section(group) * flux;
        }
        one_group_barns.push(if flux_denominator > 0.0 {
            numerator / flux_denominator
        } else {
            0.0
        });
        if descriptor.row.mt == 18 && descriptor.row.zap == 0 {
            match library::fission_average_energy_ev(
                prepared.header.group_count,
                &prepared.boundaries_ev,
                phi,
                cross_section,
            )? {
                Some(value) => {
                    fission_average_energy_ev.push(value);
                    fission_average_present.push(true);
                }
                None => {
                    fission_average_energy_ev.push(0.0);
                    fission_average_present.push(false);
                }
            }
        } else {
            fission_average_energy_ev.push(0.0);
            fission_average_present.push(false);
        }
    }

    let row_count = prepared.header.row_count;
    let group_count = prepared.header.group_count;
    let bounds_count = prepared.header.bounds_count;
    let bounds_offset = COLLAPSED_HEADER_BYTES as u64;
    let flux_offset = checked_add(
        bounds_offset,
        checked_mul(bounds_count as u64, 8, "collapsed boundaries")?,
        "collapsed flux offset",
    )?;
    let rows_offset = checked_add(
        flux_offset,
        checked_mul(group_count as u64, 8, "collapsed flux")?,
        "collapsed row offset",
    )?;
    let collapsed_offset = checked_add(
        rows_offset,
        checked_mul(
            row_count as u64,
            COLLAPSED_ROW_BYTES as u64,
            "collapsed rows",
        )?,
        "collapsed value offset",
    )?;
    let fission_offset = checked_add(
        collapsed_offset,
        checked_mul(row_count as u64, 8, "collapsed values")?,
        "fission-energy offset",
    )?;
    let presence_offset = checked_add(
        fission_offset,
        checked_mul(row_count as u64, 8, "fission-energy values")?,
        "fission-presence offset",
    )?;
    let payload_end = checked_add(presence_offset, row_count as u64, "collapsed payload end")?;
    let header = CollapsedHeader {
        row_count,
        group_count,
        bounds_count,
        bounds_offset,
        flux_offset,
        rows_offset,
        collapsed_offset,
        fission_offset,
        presence_offset,
        payload_end,
        artifact_len: checked_add(payload_end, TRAILER_BYTES as u64, "artifact length")?,
        library_sha256,
        index_sha256,
        flux_sha256: flux_sha256(phi),
        prepared_sha256: prepared.integrity_sha256,
    };
    let mut file = OpenOptions::new()
        .read(true)
        .write(true)
        .create_new(true)
        .open(output)
        .map_err(|error| format!("cannot create {}: {error}", output.display()))?;
    file.write_all(&collapsed_header_bytes(&header)?)
        .map_err(|error| format!("cannot write {} header: {error}", output.display()))?;
    for value in &prepared.boundaries_ev {
        file.write_all(&value.to_le_bytes())
            .map_err(|error| format!("cannot write {} boundaries: {error}", output.display()))?;
    }
    for value in phi {
        file.write_all(&value.to_le_bytes())
            .map_err(|error| format!("cannot write {} flux: {error}", output.display()))?;
    }
    for descriptor in &prepared.descriptors {
        write_collapsed_row(&mut file, &descriptor.row)
            .map_err(|error| format!("cannot write {} rows: {error}", output.display()))?;
    }
    for value in &one_group_barns {
        file.write_all(&value.to_le_bytes()).map_err(|error| {
            format!(
                "cannot write {} collapsed values: {error}",
                output.display()
            )
        })?;
    }
    for value in &fission_average_energy_ev {
        file.write_all(&value.to_le_bytes()).map_err(|error| {
            format!(
                "cannot write {} fission energies: {error}",
                output.display()
            )
        })?;
    }
    for present in &fission_average_present {
        file.write_all(&[u8::from(*present)]).map_err(|error| {
            format!(
                "cannot write {} fission presence: {error}",
                output.display()
            )
        })?;
    }
    append_integrity_trailer(&mut file, output, payload_end)?;
    Ok(())
}

fn read_collapsed_rows(
    file: &mut File,
    header: &CollapsedHeader,
    path: &Path,
) -> Result<Vec<Row>, String> {
    file.seek(SeekFrom::Start(header.rows_offset))
        .map_err(|error| format!("cannot seek {} rows: {error}", path.display()))?;
    let mut rows = Vec::new();
    rows.try_reserve_exact(header.row_count)
        .map_err(|error| format!("cannot allocate collapsed rows: {error}"))?;
    let mut raw = [0u8; COLLAPSED_ROW_BYTES];
    for source_row in 0..header.row_count {
        file.read_exact(&mut raw).map_err(|error| {
            format!(
                "cannot read {} collapsed row {source_row}: {error}",
                path.display()
            )
        })?;
        let mut reader = HeaderReader::new(&raw);
        rows.push(Row {
            target: usize_from_u64(reader.u64("target index")?, "target index")?,
            mt: reader.i32("MT")?,
            zap: reader.i32("ZAP")?,
            lfs: reader.i32("LFS")?,
            lmf: reader.i32("LMF")?,
        });
    }
    Ok(rows)
}

fn read_collapsed_artifact(
    path: &Path,
    expected_library_sha256: &[u8; 32],
    expected_index_sha256: &[u8; 32],
    expected_phi: &[f64],
) -> Result<CollapsedLibrary, String> {
    let mut file = verify_regular_file(path)?;
    let header_bytes = read_fixed_header(&mut file, COLLAPSED_HEADER_BYTES, path)?;
    let header = parse_collapsed_header(&header_bytes, path)?;
    if &header.library_sha256 != expected_library_sha256 {
        return Err(format!(
            "prepared cache artifact {} belongs to source library {}, expected {}",
            path.display(),
            encode_sha256(&header.library_sha256),
            encode_sha256(expected_library_sha256)
        ));
    }
    if &header.index_sha256 != expected_index_sha256 {
        return Err(format!(
            "prepared cache artifact {} belongs to source index {}, expected {}",
            path.display(),
            encode_sha256(&header.index_sha256),
            encode_sha256(expected_index_sha256)
        ));
    }
    let expected_flux_sha256 = flux_sha256(expected_phi);
    if header.flux_sha256 != expected_flux_sha256 {
        return Err(format!(
            "prepared cache artifact {} belongs to a different flux spectrum",
            path.display()
        ));
    }
    verify_integrity(&mut file, path, header.payload_end, header.artifact_len)?;
    let boundaries_ev = read_f64_at(
        &mut file,
        header.bounds_offset,
        header.bounds_count,
        path,
        "boundaries",
    )?;
    validate_boundaries(&boundaries_ev)?;
    let flux = read_f64_at(
        &mut file,
        header.flux_offset,
        header.group_count,
        path,
        "flux",
    )?;
    if flux.len() != expected_phi.len()
        || flux
            .iter()
            .zip(expected_phi)
            .any(|(stored, expected)| stored.to_bits() != expected.to_bits())
    {
        return Err(format!(
            "prepared cache artifact {} flux bits do not match its requested spectrum",
            path.display()
        ));
    }
    if flux.iter().any(|value| *value < 0.0) {
        return Err(format!(
            "prepared cache artifact {} contains a negative flux",
            path.display()
        ));
    }
    let rows = read_collapsed_rows(&mut file, &header, path)?;
    let one_group_barns = read_f64_at(
        &mut file,
        header.collapsed_offset,
        header.row_count,
        path,
        "collapsed cross sections",
    )?;
    if one_group_barns.iter().any(|value| *value < 0.0) {
        return Err(format!(
            "prepared cache artifact {} contains a negative collapsed cross section",
            path.display()
        ));
    }
    let fission_average_energy_ev = read_f64_at(
        &mut file,
        header.fission_offset,
        header.row_count,
        path,
        "fission-average energies",
    )?;
    file.seek(SeekFrom::Start(header.presence_offset))
        .map_err(|error| format!("cannot seek {} fission presence: {error}", path.display()))?;
    let mut presence = vec![0u8; header.row_count];
    file.read_exact(&mut presence)
        .map_err(|error| format!("cannot read {} fission presence: {error}", path.display()))?;
    if presence.iter().any(|value| !matches!(value, 0 | 1)) {
        return Err(format!(
            "prepared cache artifact {} has an invalid fission-presence value",
            path.display()
        ));
    }
    for (source_row, ((row, energy), present)) in rows
        .iter()
        .zip(&fission_average_energy_ev)
        .zip(&presence)
        .enumerate()
    {
        if *present == 1 {
            if row.mt != 18 || row.zap != 0 || *energy <= 0.0 {
                return Err(format!(
                    "prepared cache artifact {} has an invalid fission-energy record at row {source_row}",
                    path.display()
                ));
            }
        } else if energy.to_bits() != 0 {
            return Err(format!(
                "prepared cache artifact {} has an unmarked fission energy at row {source_row}",
                path.display()
            ));
        }
    }
    let library = CollapsedLibrary {
        rows,
        group_count: header.group_count,
        boundaries_ev,
        flux,
        one_group_barns,
        fission_average_energy_ev,
        fission_average_present: presence.into_iter().map(|value| value == 1).collect(),
    };
    library.validate_flux(expected_phi)?;
    Ok(library)
}

fn materialize_prepared(
    mut archive: PreparedArchive,
    targets: Option<&BTreeSet<usize>>,
    path: &Path,
) -> Result<PreparedLibrary, String> {
    let selected_count = archive
        .descriptors
        .iter()
        .filter(|descriptor| targets.is_none_or(|targets| targets.contains(&descriptor.row.target)))
        .count();
    let selected_values = archive
        .descriptors
        .iter()
        .filter(|descriptor| targets.is_none_or(|targets| targets.contains(&descriptor.row.target)))
        .try_fold(0usize, |total, descriptor| {
            total
                .checked_add(descriptor.value_count)
                .ok_or("selected prepared value count overflows")
        })?;
    let mut rows = Vec::new();
    let mut spans = Vec::new();
    rows.try_reserve_exact(selected_count)
        .map_err(|error| format!("cannot allocate selected prepared rows: {error}"))?;
    spans
        .try_reserve_exact(selected_count)
        .map_err(|error| format!("cannot allocate selected prepared spans: {error}"))?;
    let mut values = Vec::new();
    values
        .try_reserve_exact(selected_values)
        .map_err(|error| format!("cannot allocate selected prepared values: {error}"))?;

    if let Some(targets) = targets {
        for (source_row, descriptor) in archive.descriptors.iter().enumerate() {
            if !targets.contains(&descriptor.row.target) {
                continue;
            }
            let local_offset = values.len();
            if descriptor.value_count > 0 {
                let byte_offset = checked_add(
                    archive.header.values_offset,
                    checked_mul(descriptor.value_index as u64, 8, "selected value offset")?,
                    "selected value offset",
                )?;
                archive
                    .file
                    .seek(SeekFrom::Start(byte_offset))
                    .map_err(|error| {
                        format!(
                            "cannot seek {} selected row {source_row}: {error}",
                            path.display()
                        )
                    })?;
                let selected = library::read_f64_values(&mut archive.file, descriptor.value_count)
                    .map_err(|error| {
                        format!(
                            "cannot read {} selected row {source_row}: {error}",
                            path.display()
                        )
                    })?;
                if selected
                    .iter()
                    .any(|value| !value.is_finite() || *value < 0.0)
                {
                    return Err(format!(
                        "prepared cache artifact {} selected row {source_row} contains a nonfinite or negative cross section",
                        path.display()
                    ));
                }
                values.extend(selected);
            }
            rows.push(descriptor.row);
            spans.push(PreparedSpan {
                source_row,
                first_group: descriptor.first_group,
                value_count: descriptor.value_count,
                value_offset: local_offset,
            });
        }
    } else {
        archive
            .file
            .seek(SeekFrom::Start(archive.header.values_offset))
            .map_err(|error| format!("cannot seek {} values: {error}", path.display()))?;
        values = library::read_f64_values(&mut archive.file, archive.header.value_count)
            .map_err(|error| format!("cannot read {} values: {error}", path.display()))?;
        if values
            .iter()
            .any(|value| !value.is_finite() || *value < 0.0)
        {
            return Err(format!(
                "prepared cache artifact {} contains a nonfinite or negative cross section",
                path.display()
            ));
        }
        for (source_row, descriptor) in archive.descriptors.iter().enumerate() {
            rows.push(descriptor.row);
            spans.push(PreparedSpan {
                source_row,
                first_group: descriptor.first_group,
                value_count: descriptor.value_count,
                value_offset: descriptor.value_index,
            });
        }
    }
    Ok(PreparedLibrary {
        rows,
        spans,
        values,
        group_count: archive.header.group_count,
        boundaries_ev: archive.boundaries_ev,
        source_row_count: archive.header.row_count,
    })
}

#[derive(Clone, Debug)]
pub struct CacheArtifactPaths {
    pub directory: PathBuf,
    pub prepared: PathBuf,
    pub collapsed: PathBuf,
}

pub fn default_cache_root() -> Result<PathBuf, String> {
    if let Some(explicit) = std::env::var_os("ACTINV_CACHE_DIR") {
        if explicit.is_empty() {
            return Err("ACTINV_CACHE_DIR is empty".into());
        }
        let path = PathBuf::from(explicit);
        if !path.is_absolute() {
            return Err("ACTINV_CACHE_DIR must be an absolute path".into());
        }
        return Ok(path);
    }
    #[cfg(windows)]
    if let Some(local) = std::env::var_os("LOCALAPPDATA") {
        let path = PathBuf::from(local);
        if path.is_absolute() {
            return Ok(path.join("ACTINV"));
        }
    }
    if let Some(xdg) = std::env::var_os("XDG_CACHE_HOME") {
        let path = PathBuf::from(xdg);
        if path.is_absolute() {
            return Ok(path.join("actinv"));
        }
    }
    if let Some(home) = std::env::var_os("HOME") {
        let path = PathBuf::from(home);
        if path.is_absolute() {
            return Ok(path.join(".cache").join("actinv"));
        }
    }
    Err("cannot determine ACTINV cache directory; set ACTINV_CACHE_DIR to an absolute path".into())
}

pub fn cache_artifact_paths(
    cache_root: &Path,
    library_sha256: &str,
    index_sha256: &str,
    phi: &[f64],
) -> Result<CacheArtifactPaths, String> {
    let library = decode_sha256(library_sha256, "activation-library SHA-256")?;
    let index = decode_sha256(index_sha256, "activation-index SHA-256")?;
    if !cache_root.is_absolute() {
        return Err("ACTINV cache root must be an absolute path".into());
    }
    let directory = cache_root.join("prepared-v1").join(format!(
        "{}-{}",
        encode_sha256(&library),
        encode_sha256(&index)
    ));
    let flux = encode_sha256(&flux_sha256(phi));
    Ok(CacheArtifactPaths {
        prepared: directory.join("library.actp"),
        collapsed: directory.join(format!("spectrum-{flux}.actc")),
        directory,
    })
}

struct ArtifactLock {
    path: PathBuf,
}

impl Drop for ArtifactLock {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.path);
    }
}

fn lock_path(final_path: &Path) -> Result<PathBuf, String> {
    let name = final_path
        .file_name()
        .and_then(|name| name.to_str())
        .ok_or_else(|| format!("cache path {} has no UTF-8 filename", final_path.display()))?;
    Ok(final_path.with_file_name(format!(".{name}.lock")))
}

fn acquire_artifact_lock(final_path: &Path) -> Result<Option<ArtifactLock>, String> {
    let lock_path = lock_path(final_path)?;
    let started = std::time::Instant::now();
    loop {
        if final_path.try_exists().map_err(|error| {
            format!(
                "cannot inspect cache artifact {}: {error}",
                final_path.display()
            )
        })? {
            return Ok(None);
        }
        match OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&lock_path)
        {
            Ok(mut lock) => {
                writeln!(lock, "pid={} schema={SCHEMA_VERSION}", std::process::id()).map_err(
                    |error| format!("cannot write cache lock {}: {error}", lock_path.display()),
                )?;
                lock.sync_all().map_err(|error| {
                    format!("cannot sync cache lock {}: {error}", lock_path.display())
                })?;
                return Ok(Some(ArtifactLock { path: lock_path }));
            }
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {
                if let Ok(metadata) = std::fs::symlink_metadata(&lock_path) {
                    if metadata.file_type().is_symlink() || !metadata.is_file() {
                        return Err(format!(
                            "cache publication lock {} is not a regular file",
                            lock_path.display()
                        ));
                    }
                }
                if started.elapsed() >= LOCK_WAIT {
                    return Err(format!(
                        "timed out waiting for cache publication lock {}",
                        lock_path.display()
                    ));
                }
                std::thread::sleep(LOCK_POLL);
            }
            Err(error) => {
                return Err(format!(
                    "cannot create cache publication lock {}: {error}",
                    lock_path.display()
                ));
            }
        }
    }
}

#[cfg(unix)]
fn sync_directory(path: &Path) -> Result<(), String> {
    File::open(path)
        .and_then(|directory| directory.sync_all())
        .map_err(|error| format!("cannot sync cache directory {}: {error}", path.display()))
}

#[cfg(not(unix))]
fn sync_directory(_path: &Path) -> Result<(), String> {
    Ok(())
}

fn ensure_artifact(
    final_path: &Path,
    validate: impl Fn(&Path) -> Result<(), String>,
    build: impl Fn(&Path) -> Result<(), String>,
) -> Result<(), String> {
    if final_path.try_exists().map_err(|error| {
        format!(
            "cannot inspect cache artifact {}: {error}",
            final_path.display()
        )
    })? {
        return validate(final_path);
    }
    let parent = final_path
        .parent()
        .ok_or_else(|| format!("cache artifact {} has no parent", final_path.display()))?;
    std::fs::create_dir_all(parent).map_err(|error| {
        format!(
            "cannot create cache directory {}: {error}",
            parent.display()
        )
    })?;
    let Some(lock) = acquire_artifact_lock(final_path)? else {
        return validate(final_path);
    };
    if final_path.try_exists().map_err(|error| {
        format!(
            "cannot inspect cache artifact {}: {error}",
            final_path.display()
        )
    })? {
        validate(final_path)?;
        drop(lock);
        return Ok(());
    }
    let temporary = library::temporary_sibling(final_path)?;
    let result = (|| {
        build(&temporary)?;
        if final_path.try_exists().map_err(|error| {
            format!(
                "cannot inspect cache artifact {}: {error}",
                final_path.display()
            )
        })? {
            return Err(format!(
                "cache artifact {} appeared while its publication lock was held",
                final_path.display()
            ));
        }
        std::fs::rename(&temporary, final_path).map_err(|error| {
            format!(
                "cannot publish cache artifact {} as {}: {error}",
                temporary.display(),
                final_path.display()
            )
        })?;
        sync_directory(parent)
    })();
    if result.is_err() {
        let _ = std::fs::remove_file(&temporary);
    }
    drop(lock);
    result
}

fn ensure_prepared_path(
    source_npz: &str,
    final_path: &Path,
    library_sha256: [u8; 32],
    index_sha256: [u8; 32],
) -> Result<(), String> {
    ensure_artifact(
        final_path,
        |path| open_prepared_archive(path, &library_sha256, &index_sha256).map(|_| ()),
        |path| {
            let source = Path::new(source_npz);
            let before = source_stamp(source)?;
            build_prepared_artifact(source_npz, path, library_sha256, index_sha256)?;
            let after = source_stamp(source)?;
            if before != after {
                return Err(format!(
                    "activation library {} changed during prepared-cache creation",
                    source.display()
                ));
            }
            Ok(())
        },
    )
}

/// Ensure and load the compact groupwise cache using the platform/default cache root.
pub fn load_or_prepare_groupwise(
    source_npz: &str,
    library_sha256: &str,
    index_sha256: &str,
) -> Result<PreparedLibrary, String> {
    load_or_prepare_groupwise_in(
        &default_cache_root()?,
        source_npz,
        library_sha256,
        index_sha256,
    )
}

pub fn load_or_prepare_groupwise_in(
    cache_root: &Path,
    source_npz: &str,
    library_sha256: &str,
    index_sha256: &str,
) -> Result<PreparedLibrary, String> {
    let library_digest = decode_sha256(library_sha256, "activation-library SHA-256")?;
    verify_source_identity(Path::new(source_npz), &library_digest)?;
    load_or_prepare_groupwise_verified_in(cache_root, source_npz, library_sha256, index_sha256)
}

/// Load groupwise prepared data immediately after the caller has verified the complete source
/// NPZ against `library_sha256`.
pub fn load_or_prepare_groupwise_after_sha256_verification(
    source_npz: &str,
    library_sha256: &str,
    index_sha256: &str,
) -> Result<PreparedLibrary, String> {
    load_or_prepare_groupwise_verified_in(
        &default_cache_root()?,
        source_npz,
        library_sha256,
        index_sha256,
    )
}

fn load_or_prepare_groupwise_verified_in(
    cache_root: &Path,
    source_npz: &str,
    library_sha256: &str,
    index_sha256: &str,
) -> Result<PreparedLibrary, String> {
    let empty_flux = [];
    let paths = cache_artifact_paths(cache_root, library_sha256, index_sha256, &empty_flux)?;
    let library_sha256 = decode_sha256(library_sha256, "activation-library SHA-256")?;
    let index_sha256 = decode_sha256(index_sha256, "activation-index SHA-256")?;
    ensure_prepared_path(source_npz, &paths.prepared, library_sha256, index_sha256)?;
    let archive = open_prepared_archive(&paths.prepared, &library_sha256, &index_sha256)?;
    materialize_prepared(archive, None, &paths.prepared)
}

/// Load selected target indices from an existing prepared artifact without allocating unselected
/// cross-section values. The returned rows remain in original source order and expose their
/// original row numbers through [`PreparedSpan::source_row`].
pub fn read_prepared_targets(
    path: &Path,
    library_sha256: &str,
    index_sha256: &str,
    targets: &BTreeSet<usize>,
) -> Result<PreparedLibrary, String> {
    let library_sha256 = decode_sha256(library_sha256, "activation-library SHA-256")?;
    let index_sha256 = decode_sha256(index_sha256, "activation-index SHA-256")?;
    let archive = open_prepared_archive(path, &library_sha256, &index_sha256)?;
    materialize_prepared(archive, Some(targets), path)
}

/// Ensure and load the exact spectrum-collapsed cache using the platform/default cache root.
pub fn load_or_prepare_collapsed(
    source_npz: &str,
    library_sha256: &str,
    index_sha256: &str,
    phi: &[f64],
) -> Result<CollapsedLibrary, String> {
    load_or_prepare_collapsed_in(
        &default_cache_root()?,
        source_npz,
        library_sha256,
        index_sha256,
        phi,
    )
}

pub fn load_or_prepare_collapsed_in(
    cache_root: &Path,
    source_npz: &str,
    library_sha256: &str,
    index_sha256: &str,
    phi: &[f64],
) -> Result<CollapsedLibrary, String> {
    let library_digest = decode_sha256(library_sha256, "activation-library SHA-256")?;
    verify_source_identity(Path::new(source_npz), &library_digest)?;
    load_or_prepare_collapsed_verified_in(cache_root, source_npz, library_sha256, index_sha256, phi)
}

/// Load exact collapsed data immediately after the caller has verified the complete source NPZ
/// against `library_sha256`.
pub fn load_or_prepare_collapsed_after_sha256_verification(
    source_npz: &str,
    library_sha256: &str,
    index_sha256: &str,
    phi: &[f64],
) -> Result<CollapsedLibrary, String> {
    load_or_prepare_collapsed_verified_in(
        &default_cache_root()?,
        source_npz,
        library_sha256,
        index_sha256,
        phi,
    )
}

fn load_or_prepare_collapsed_verified_in(
    cache_root: &Path,
    source_npz: &str,
    library_sha256: &str,
    index_sha256: &str,
    phi: &[f64],
) -> Result<CollapsedLibrary, String> {
    let paths = cache_artifact_paths(cache_root, library_sha256, index_sha256, phi)?;
    let library_sha256 = decode_sha256(library_sha256, "activation-library SHA-256")?;
    let index_sha256 = decode_sha256(index_sha256, "activation-index SHA-256")?;
    if paths.collapsed.try_exists().map_err(|error| {
        format!(
            "cannot inspect cache artifact {}: {error}",
            paths.collapsed.display()
        )
    })? {
        return read_collapsed_artifact(&paths.collapsed, &library_sha256, &index_sha256, phi);
    }
    ensure_prepared_path(source_npz, &paths.prepared, library_sha256, index_sha256)?;
    ensure_artifact(
        &paths.collapsed,
        |path| read_collapsed_artifact(path, &library_sha256, &index_sha256, phi).map(|_| ()),
        |path| build_collapsed_artifact(&paths.prepared, path, library_sha256, index_sha256, phi),
    )?;
    read_collapsed_artifact(&paths.collapsed, &library_sha256, &index_sha256, phi)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::library::{read_npz, write_npz, Library};

    fn scratch(name: &str) -> PathBuf {
        let stamp = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let path = std::env::temp_dir().join(format!(
            "actinv-prepared-{name}-{}-{stamp}",
            std::process::id()
        ));
        std::fs::create_dir(&path).unwrap();
        path
    }

    fn sha256_file(path: &Path) -> String {
        let mut file = File::open(path).unwrap();
        let mut hash = Sha256::new();
        let mut buffer = [0u8; 4096];
        loop {
            let count = file.read(&mut buffer).unwrap();
            if count == 0 {
                break;
            }
            hash.update(&buffer[..count]);
        }
        format!("{:x}", hash.finalize())
    }

    fn fixture() -> Library {
        Library {
            rows: vec![
                Row {
                    target: 0,
                    mt: 102,
                    zap: 26_057,
                    lfs: 0,
                    lmf: 3,
                },
                Row {
                    target: 1,
                    mt: 18,
                    zap: 0,
                    lfs: 0,
                    lmf: 3,
                },
                Row {
                    target: 2,
                    mt: 103,
                    zap: 25_056,
                    lfs: 0,
                    lmf: 3,
                },
            ],
            sig: vec![
                0.0, 2.0, 0.0, 4.0, 0.0, -0.0, 1.0, 0.0, 3.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
            ],
            ngroups: 5,
            bounds: vec![1.0, 2.0, 4.0, 8.0, 16.0, 32.0],
        }
    }

    #[test]
    fn deterministic_exact_round_trip_and_indexed_selection() {
        let scratch = scratch("round-trip");
        let source = scratch.join("source.npz");
        write_npz(&source, &fixture()).unwrap();
        let library_sha = sha256_file(&source);
        let index_sha = "11".repeat(32);
        let phi = [0.0, 2.0, 3.0, 0.0, 5.0];
        let first_root = scratch.join("cache-a");
        let second_root = scratch.join("cache-b");
        let source_text = source.to_str().unwrap();

        let first =
            load_or_prepare_collapsed_in(&first_root, source_text, &library_sha, &index_sha, &phi)
                .unwrap();
        let second =
            load_or_prepare_collapsed_in(&second_root, source_text, &library_sha, &index_sha, &phi)
                .unwrap();
        let dense = read_npz(source_text).unwrap();
        assert_eq!(first.rows(), dense.rows);
        assert_eq!(first.boundaries_ev(), dense.bounds);
        assert_eq!(first.flux(), phi);
        assert_eq!(first.one_group_barns(), second.one_group_barns());
        let denominator = phi.iter().sum();
        for row in 0..dense.rows.len() {
            let expected = dense.collapse_row(row, &phi, denominator, 1, 5);
            assert_eq!(first.one_group_barns()[row].to_bits(), expected.to_bits());
            let expected_energy = dense.fission_average_energy_ev(row, &phi).unwrap();
            let actual_energy = first.fission_average_energy_ev(row, &phi).unwrap();
            assert_eq!(
                actual_energy.map(f64::to_bits),
                (dense.rows[row].mt == 18 && dense.rows[row].zap == 0)
                    .then_some(expected_energy)
                    .flatten()
                    .map(f64::to_bits)
            );
        }

        let first_paths =
            cache_artifact_paths(&first_root, &library_sha, &index_sha, &phi).unwrap();
        let second_paths =
            cache_artifact_paths(&second_root, &library_sha, &index_sha, &phi).unwrap();
        assert_eq!(
            std::fs::read(&first_paths.prepared).unwrap(),
            std::fs::read(&second_paths.prepared).unwrap()
        );
        assert_eq!(
            std::fs::read(&first_paths.collapsed).unwrap(),
            std::fs::read(&second_paths.collapsed).unwrap()
        );

        let groupwise =
            load_or_prepare_groupwise_in(&first_root, source_text, &library_sha, &index_sha)
                .unwrap();
        for row in 0..dense.rows.len() {
            for group in 0..dense.ngroups {
                assert_eq!(
                    groupwise.cross_section(row, group).to_bits(),
                    dense.sigma(row)[group].to_bits()
                );
            }
        }
        let selected = read_prepared_targets(
            &first_paths.prepared,
            &library_sha,
            &index_sha,
            &BTreeSet::from([1]),
        )
        .unwrap();
        assert_eq!(selected.rows(), &[dense.rows[1]]);
        assert_eq!(selected.spans()[0].source_row, 1);
        for group in 0..dense.ngroups {
            assert_eq!(
                selected.cross_section(0, group).to_bits(),
                dense.sigma(1)[group].to_bits()
            );
        }
        std::fs::remove_dir_all(scratch).unwrap();
    }

    #[test]
    fn corrupted_final_artifact_fails_instead_of_regenerating() {
        let scratch = scratch("corrupt");
        let source = scratch.join("source.npz");
        write_npz(&source, &fixture()).unwrap();
        let library_sha = sha256_file(&source);
        let index_sha = "22".repeat(32);
        let phi = [1.0; 5];
        let cache = scratch.join("cache");
        load_or_prepare_collapsed_in(
            &cache,
            source.to_str().unwrap(),
            &library_sha,
            &index_sha,
            &phi,
        )
        .unwrap();
        let paths = cache_artifact_paths(&cache, &library_sha, &index_sha, &phi).unwrap();
        let original = std::fs::read(&paths.collapsed).unwrap();
        let mut corrupted = original.clone();
        let position = COLLAPSED_HEADER_BYTES + 8;
        corrupted[position] ^= 1;
        std::fs::write(&paths.collapsed, &corrupted).unwrap();
        let error = load_or_prepare_collapsed_in(
            &cache,
            source.to_str().unwrap(),
            &library_sha,
            &index_sha,
            &phi,
        )
        .unwrap_err();
        assert!(error.contains("integrity trailer"), "{error}");
        assert_eq!(std::fs::read(&paths.collapsed).unwrap(), corrupted);
        std::fs::remove_dir_all(scratch).unwrap();
    }
}
