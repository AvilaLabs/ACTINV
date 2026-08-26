//! Reader for ACTINV 709-group activation libraries (`.npz` = zip of `.npy`), so the Rust core consumes exactly the
//! files the Python builder writes. Bit-identity with numpy is required by the P5 G1 control.
use std::collections::{HashMap, HashSet};
use std::io::{BufReader, Read, Seek, Write};
use std::path::Path;

/// One library row: which target, which reaction, which product, from which ENDF file section.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Row {
    pub target: usize,
    pub mt: i32,
    pub zap: i32,
    pub lfs: i32,
    pub lmf: i32,
}

#[derive(Clone, Debug)]
pub struct Library {
    pub rows: Vec<Row>,
    /// group cross sections, `rows.len()` x `ngroups`, barns
    pub sig: Vec<f64>,
    pub ngroups: usize,
    pub bounds: Vec<f64>,
}

impl Library {
    pub fn validate(&self) -> Result<(), String> {
        if self.ngroups == 0 {
            return Err("activation library has zero energy groups".into());
        }
        if self.bounds.len() != self.ngroups + 1 {
            return Err(format!(
                "activation library has {} boundaries for {} groups",
                self.bounds.len(),
                self.ngroups
            ));
        }
        if self
            .bounds
            .iter()
            .any(|value| !value.is_finite() || *value <= 0.0)
            || self.bounds.windows(2).any(|pair| pair[1] <= pair[0])
        {
            return Err(
                "activation-library boundaries must be finite, positive and increasing".into(),
            );
        }
        let expected = self
            .rows
            .len()
            .checked_mul(self.ngroups)
            .ok_or("activation-library dimensions overflow")?;
        if self.sig.len() != expected {
            return Err(format!(
                "activation library has {} cross sections, expected {expected}",
                self.sig.len()
            ));
        }
        if self
            .sig
            .iter()
            .any(|value| !value.is_finite() || *value < 0.0)
        {
            return Err("activation library contains a nonfinite or negative cross section".into());
        }
        Ok(())
    }
}

impl Library {
    pub fn sigma(&self, row: usize) -> &[f64] {
        &self.sig[row * self.ngroups..(row + 1) * self.ngroups]
    }
    /// One-group cross section (barns) under a group flux, sum(sig_g phi_g) / sum(phi_g).
    pub fn one_group(&self, row: usize, phi: &[f64]) -> f64 {
        let s = self.sigma(row);
        let (mut num, mut den) = (0.0, 0.0);
        for g in 0..self.ngroups {
            num += s[g] * phi[g];
            den += phi[g];
        }
        if den > 0.0 {
            num / den
        } else {
            0.0
        }
    }
    /// Rows grouped by target index, in file order.
    pub fn by_target(&self) -> HashMap<usize, Vec<usize>> {
        let mut m: HashMap<usize, Vec<usize>> = HashMap::new();
        for (i, r) in self.rows.iter().enumerate() {
            m.entry(r.target).or_default().push(i);
        }
        m
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum NpyDtype {
    I64,
    F64,
}

#[derive(Clone, Debug)]
struct NpyHeader {
    shape: Vec<usize>,
    dtype: NpyDtype,
    elements: usize,
}

/// Read only an NPY header. Payloads are decoded directly into their final vectors so a large `sig` member never
/// exists simultaneously as raw bytes and as `f64` values.
fn read_npy_header(reader: &mut impl Read) -> Result<NpyHeader, String> {
    let mut preamble = [0u8; 8];
    reader
        .read_exact(&mut preamble)
        .map_err(|error| format!("truncated .npy preamble: {error}"))?;
    if &preamble[..6] != b"\x93NUMPY" {
        return Err("not a .npy file".into());
    }
    let header_length = match preamble[6] {
        1 => {
            let mut bytes = [0u8; 2];
            reader
                .read_exact(&mut bytes)
                .map_err(|error| format!("truncated .npy v1 header length: {error}"))?;
            u16::from_le_bytes(bytes) as usize
        }
        2 | 3 => {
            let mut bytes = [0u8; 4];
            reader
                .read_exact(&mut bytes)
                .map_err(|error| format!("truncated .npy v2/v3 header length: {error}"))?;
            u32::from_le_bytes(bytes) as usize
        }
        major => {
            return Err(format!("unsupported .npy version {major}.{}", preamble[7]));
        }
    };
    if header_length > 1024 * 1024 {
        return Err(format!(
            "unreasonably large .npy header ({header_length} bytes)"
        ));
    }
    let mut raw_header = vec![0u8; header_length];
    reader
        .read_exact(&mut raw_header)
        .map_err(|error| format!("truncated .npy header: {error}"))?;
    let header = std::str::from_utf8(&raw_header)
        .map_err(|error| format!("non-UTF-8 .npy header: {error}"))?;
    let dtype = if header.contains("'descr': '<i8'") {
        NpyDtype::I64
    } else if header.contains("'descr': '<f8'") {
        NpyDtype::F64
    } else {
        return Err(format!("unsupported dtype in {header}"));
    };
    if !header.contains("'fortran_order': False") {
        return Err("only C-order .npy arrays are supported".into());
    }
    let shape_text = header
        .split("'shape':")
        .nth(1)
        .ok_or(".npy header has no shape")?
        .trim_start()
        .strip_prefix('(')
        .ok_or("malformed .npy shape")?
        .split(')')
        .next()
        .ok_or("malformed .npy shape")?;
    let mut shape = Vec::new();
    for field in shape_text.split(',') {
        let field = field.trim();
        if field.is_empty() {
            continue;
        }
        shape.push(
            field
                .parse::<usize>()
                .map_err(|_| format!("invalid .npy dimension '{field}'"))?,
        );
    }
    if shape.is_empty() {
        return Err("scalar .npy arrays are unsupported".into());
    }
    let elements = shape.iter().try_fold(1usize, |product, dimension| {
        product.checked_mul(*dimension).ok_or(".npy shape overflow")
    })?;
    Ok(NpyHeader {
        shape,
        dtype,
        elements,
    })
}

fn read_i64(reader: &mut impl Read) -> Result<i64, String> {
    let mut bytes = [0u8; 8];
    reader
        .read_exact(&mut bytes)
        .map_err(|error| format!("truncated i64 array: {error}"))?;
    Ok(i64::from_le_bytes(bytes))
}

fn read_f64_values(reader: &mut impl Read, count: usize) -> Result<Vec<f64>, String> {
    const VALUES_PER_CHUNK: usize = 8192;
    let mut values = Vec::new();
    values
        .try_reserve_exact(count)
        .map_err(|error| format!("cannot allocate {count} f64 values: {error}"))?;
    let mut bytes = [0u8; VALUES_PER_CHUNK * 8];
    let mut remaining = count;
    while remaining > 0 {
        let chunk_values = remaining.min(VALUES_PER_CHUNK);
        let chunk_bytes = chunk_values * 8;
        reader
            .read_exact(&mut bytes[..chunk_bytes])
            .map_err(|error| format!("truncated f64 array: {error}"))?;
        values.extend(
            bytes[..chunk_bytes]
                .as_chunks::<8>()
                .0
                .iter()
                .map(|bytes| f64::from_le_bytes(*bytes)),
        );
        remaining -= chunk_values;
    }
    Ok(values)
}

fn ensure_eof(reader: &mut impl Read, name: &str) -> Result<(), String> {
    let mut extra = [0u8; 1];
    if reader
        .read(&mut extra)
        .map_err(|error| format!("cannot finish {name}: {error}"))?
        != 0
    {
        return Err(format!("{name} has trailing payload bytes"));
    }
    Ok(())
}

fn validate_members<R: Read + Seek>(archive: &mut zip::ZipArchive<R>) -> Result<(), String> {
    const MAX_ARRAY_BYTES: u64 = 1_000_000_000;
    let mut names = HashSet::new();
    for index in 0..archive.len() {
        let member = archive.by_index(index).map_err(|error| error.to_string())?;
        if member.size() >= MAX_ARRAY_BYTES {
            return Err(format!(
                "{} is {} bytes; a single activation-library array must be below {MAX_ARRAY_BYTES} bytes",
                member.name(),
                member.size()
            ));
        }
        if !matches!(member.name(), "rows.npy" | "sig.npy" | "bounds.npy") {
            return Err(format!(
                "unexpected activation-library member '{}'",
                member.name()
            ));
        }
        if !names.insert(member.name().to_string()) {
            return Err(format!(
                "duplicate activation-library member '{}'",
                member.name()
            ));
        }
    }
    for required in ["rows.npy", "sig.npy", "bounds.npy"] {
        if !names.contains(required) {
            return Err(format!("activation library has no {required}"));
        }
    }
    Ok(())
}

fn read_rows<R: Read + Seek>(archive: &mut zip::ZipArchive<R>) -> Result<Vec<Row>, String> {
    let member = archive
        .by_name("rows.npy")
        .map_err(|error| error.to_string())?;
    let mut reader = BufReader::with_capacity(64 * 1024, member);
    let header = read_npy_header(&mut reader)?;
    if header.dtype != NpyDtype::I64 || header.shape.len() != 2 || header.shape[1] != 5 {
        return Err(format!(
            "rows must have dtype <i8 and shape (N, 5), got {header:?}"
        ));
    }
    let mut rows = Vec::new();
    rows.try_reserve_exact(header.shape[0])
        .map_err(|error| format!("cannot allocate {} rows: {error}", header.shape[0]))?;
    for _ in 0..header.shape[0] {
        let values = [
            read_i64(&mut reader)?,
            read_i64(&mut reader)?,
            read_i64(&mut reader)?,
            read_i64(&mut reader)?,
            read_i64(&mut reader)?,
        ];
        let target = usize::try_from(values[0])
            .map_err(|_| format!("invalid negative target index {}", values[0]))?;
        let as_i32 = |value: i64, name: &str| {
            i32::try_from(value).map_err(|_| format!("row {name} value {value} exceeds i32"))
        };
        rows.push(Row {
            target,
            mt: as_i32(values[1], "MT")?,
            zap: as_i32(values[2], "ZAP")?,
            lfs: as_i32(values[3], "LFS")?,
            lmf: as_i32(values[4], "LMF")?,
        });
    }
    debug_assert_eq!(header.elements, rows.len() * 5);
    ensure_eof(&mut reader, "rows.npy")?;
    Ok(rows)
}

fn read_sig<R: Read + Seek>(
    archive: &mut zip::ZipArchive<R>,
    expected_rows: usize,
) -> Result<(Vec<f64>, usize), String> {
    let member = archive
        .by_name("sig.npy")
        .map_err(|error| error.to_string())?;
    let mut reader = BufReader::with_capacity(64 * 1024, member);
    let header = read_npy_header(&mut reader)?;
    if header.dtype != NpyDtype::F64 || header.shape.len() != 2 || header.shape[0] != expected_rows
    {
        return Err(format!(
            "sig must have dtype <f8 and shape ({expected_rows}, G), got {header:?}"
        ));
    }
    let values = read_f64_values(&mut reader, header.elements)?;
    ensure_eof(&mut reader, "sig.npy")?;
    Ok((values, header.shape[1]))
}

fn read_bounds<R: Read + Seek>(
    archive: &mut zip::ZipArchive<R>,
    ngroups: usize,
) -> Result<Vec<f64>, String> {
    let member = archive
        .by_name("bounds.npy")
        .map_err(|error| error.to_string())?;
    let mut reader = BufReader::with_capacity(64 * 1024, member);
    let header = read_npy_header(&mut reader)?;
    if header.dtype != NpyDtype::F64 || header.shape != [ngroups + 1] {
        return Err(format!(
            "bounds must have dtype <f8 and shape ({},), got {header:?}",
            ngroups + 1
        ));
    }
    let values = read_f64_values(&mut reader, header.elements)?;
    ensure_eof(&mut reader, "bounds.npy")?;
    Ok(values)
}

fn open_npz(path: &str) -> Result<zip::ZipArchive<std::fs::File>, String> {
    let file = std::fs::File::open(path).map_err(|error| error.to_string())?;
    let mut archive = zip::ZipArchive::new(file).map_err(|error| error.to_string())?;
    validate_members(&mut archive)?;
    Ok(archive)
}

/// Read a library written by ACTINV or the legacy NumPy builders with one final allocation for the cross sections.
pub fn read_npz(path: &str) -> Result<Library, String> {
    let mut archive = open_npz(path)?;
    let rows = read_rows(&mut archive)?;
    let (sig, ngroups) = read_sig(&mut archive, rows.len())?;
    let bounds = read_bounds(&mut archive, ngroups)?;
    let library = Library {
        rows,
        sig,
        ngroups,
        bounds,
    };
    library.validate()?;
    Ok(library)
}

/// Stream an NPZ and retain only one target's rows. This is intended for low-memory controls and diagnostics.
pub fn read_npz_target(path: &str, target: usize) -> Result<Library, String> {
    let mut archive = open_npz(path)?;
    let all_rows = read_rows(&mut archive)?;
    let selected: Vec<bool> = all_rows.iter().map(|row| row.target == target).collect();
    let rows: Vec<Row> = all_rows
        .into_iter()
        .filter(|row| row.target == target)
        .collect();

    let member = archive
        .by_name("sig.npy")
        .map_err(|error| error.to_string())?;
    let mut reader = BufReader::with_capacity(64 * 1024, member);
    let header = read_npy_header(&mut reader)?;
    if header.dtype != NpyDtype::F64 || header.shape.len() != 2 || header.shape[0] != selected.len()
    {
        return Err(format!(
            "sig must have dtype <f8 and shape ({}, G), got {header:?}",
            selected.len()
        ));
    }
    let ngroups = header.shape[1];
    let row_bytes = ngroups
        .checked_mul(8)
        .ok_or("target group-row byte count overflows")?;
    let mut raw_row = vec![0u8; row_bytes];
    let mut sig = Vec::new();
    sig.try_reserve_exact(
        rows.len()
            .checked_mul(ngroups)
            .ok_or("selected target dimensions overflow")?,
    )
    .map_err(|error| format!("cannot allocate selected target groups: {error}"))?;
    for keep in selected {
        reader
            .read_exact(&mut raw_row)
            .map_err(|error| format!("truncated sig.npy row: {error}"))?;
        if keep {
            sig.extend(
                raw_row
                    .as_chunks::<8>()
                    .0
                    .iter()
                    .map(|bytes| f64::from_le_bytes(*bytes)),
            );
        }
    }
    ensure_eof(&mut reader, "sig.npy")?;
    drop(reader);
    let bounds = read_bounds(&mut archive, ngroups)?;
    Ok(Library {
        rows,
        sig,
        ngroups,
        bounds,
    })
}

fn write_npy_header(writer: &mut impl Write, dtype: &str, shape: &[usize]) -> Result<(), String> {
    let shape_text = if shape.len() == 1 {
        format!("({},)", shape[0])
    } else {
        format!(
            "({})",
            shape
                .iter()
                .map(usize::to_string)
                .collect::<Vec<_>>()
                .join(", ")
        )
    };
    let dictionary =
        format!("{{'descr': '{dtype}', 'fortran_order': False, 'shape': {shape_text}, }}");
    let padding = (64 - ((10 + dictionary.len() + 1) % 64)) % 64;
    let header_length = dictionary
        .len()
        .checked_add(padding + 1)
        .and_then(|length| u16::try_from(length).ok())
        .ok_or(".npy v1 header is too large")?;
    writer
        .write_all(b"\x93NUMPY\x01\x00")
        .map_err(|e| e.to_string())?;
    writer
        .write_all(&header_length.to_le_bytes())
        .map_err(|e| e.to_string())?;
    writer
        .write_all(dictionary.as_bytes())
        .map_err(|e| e.to_string())?;
    for _ in 0..padding {
        writer.write_all(b" ").map_err(|e| e.to_string())?;
    }
    writer.write_all(b"\n").map_err(|e| e.to_string())
}

fn temporary_sibling(path: &Path) -> Result<std::path::PathBuf, String> {
    use std::sync::atomic::{AtomicU64, Ordering};
    static NEXT_TEMPORARY: AtomicU64 = AtomicU64::new(0);
    let parent = path.parent().unwrap_or_else(|| Path::new("."));
    let name = path
        .file_name()
        .and_then(|name| name.to_str())
        .ok_or_else(|| format!("output path '{}' has no UTF-8 filename", path.display()))?;
    let nonce = NEXT_TEMPORARY.fetch_add(1, Ordering::Relaxed);
    Ok(parent.join(format!(".{name}.{}.{nonce}.tmp", std::process::id())))
}

/// Write the stable ACTINV `rows`, `sig` and `bounds` NPZ wire format with fixed ZIP metadata.
pub fn write_npz(path: impl AsRef<Path>, library: &Library) -> Result<(), String> {
    use zip::write::SimpleFileOptions;
    use zip::{CompressionMethod, DateTime, ZipWriter};

    library.validate()?;
    let path = path.as_ref();
    let temporary = temporary_sibling(path)?;
    let result = (|| {
        let file = std::fs::File::create(&temporary)
            .map_err(|error| format!("cannot create {}: {error}", temporary.display()))?;
        let mut archive = ZipWriter::new(file);
        let timestamp =
            DateTime::from_date_and_time(1980, 1, 1, 0, 0, 0).map_err(|error| error.to_string())?;
        let options = SimpleFileOptions::default()
            .compression_method(CompressionMethod::Deflated)
            .compression_level(Some(6))
            .last_modified_time(timestamp)
            .unix_permissions(0o600);

        archive
            .start_file("rows.npy", options)
            .map_err(|error| error.to_string())?;
        write_npy_header(&mut archive, "<i8", &[library.rows.len(), 5])?;
        for row in &library.rows {
            for value in [
                i64::try_from(row.target).map_err(|_| "target index exceeds i64")?,
                i64::from(row.mt),
                i64::from(row.zap),
                i64::from(row.lfs),
                i64::from(row.lmf),
            ] {
                archive
                    .write_all(&value.to_le_bytes())
                    .map_err(|error| error.to_string())?;
            }
        }

        archive
            .start_file("sig.npy", options)
            .map_err(|error| error.to_string())?;
        write_npy_header(&mut archive, "<f8", &[library.rows.len(), library.ngroups])?;
        for value in &library.sig {
            archive
                .write_all(&value.to_le_bytes())
                .map_err(|error| error.to_string())?;
        }

        archive
            .start_file("bounds.npy", options)
            .map_err(|error| error.to_string())?;
        write_npy_header(&mut archive, "<f8", &[library.bounds.len()])?;
        for value in &library.bounds {
            archive
                .write_all(&value.to_le_bytes())
                .map_err(|error| error.to_string())?;
        }
        let file = archive.finish().map_err(|error| error.to_string())?;
        file.sync_all()
            .map_err(|error| format!("cannot sync {}: {error}", temporary.display()))?;
        std::fs::rename(&temporary, path).map_err(|error| {
            format!(
                "cannot publish {} as {}: {error}",
                temporary.display(),
                path.display()
            )
        })
    })();
    if result.is_err() {
        let _ = std::fs::remove_file(&temporary);
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn truncated_fixed_width_arrays_fail_closed() {
        let error = read_i64(&mut std::io::Cursor::new([0; 7])).unwrap_err();
        assert!(error.contains("truncated i64 array"), "{error}");
        let error = read_f64_values(&mut std::io::Cursor::new([0; 15]), 2).unwrap_err();
        assert!(error.contains("truncated f64 array"), "{error}");
    }

    #[test]
    fn deterministic_npz_round_trip() {
        let library = Library {
            rows: vec![
                Row {
                    target: 0,
                    mt: 102,
                    zap: 26057,
                    lfs: 0,
                    lmf: 3,
                },
                Row {
                    target: 1,
                    mt: 103,
                    zap: 25056,
                    lfs: 0,
                    lmf: -1,
                },
            ],
            sig: vec![1.25, 0.0, 3.0, 4.0],
            ngroups: 2,
            bounds: vec![1.0, 2.0, 4.0],
        };
        let nonce = format!(
            "{}-{}",
            std::process::id(),
            std::thread::current().name().unwrap_or("test")
        );
        let first = std::env::temp_dir().join(format!("actinv-library-{nonce}-a.npz"));
        let second = std::env::temp_dir().join(format!("actinv-library-{nonce}-b.npz"));
        write_npz(&first, &library).unwrap();
        write_npz(&second, &library).unwrap();
        assert_eq!(
            std::fs::read(&first).unwrap(),
            std::fs::read(&second).unwrap()
        );
        let loaded = read_npz(first.to_str().unwrap()).unwrap();
        assert_eq!(loaded.rows, library.rows);
        assert_eq!(loaded.sig, library.sig);
        assert_eq!(loaded.bounds, library.bounds);
        let selected = read_npz_target(first.to_str().unwrap(), 1).unwrap();
        assert_eq!(selected.rows, vec![library.rows[1]]);
        assert_eq!(selected.sig, vec![3.0, 4.0]);
        std::fs::remove_file(first).unwrap();
        std::fs::remove_file(second).unwrap();
    }
}
