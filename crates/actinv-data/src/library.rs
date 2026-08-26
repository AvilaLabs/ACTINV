//! Reader for ACTINV 709-group activation libraries (`.npz` = zip of `.npy`), so the Rust core consumes exactly the
//! files the Python builder writes. Bit-identity with numpy is required by the P5 G1 control.
use std::collections::HashMap;
use std::io::Read;

/// One library row: which target, which reaction, which product, from which ENDF file section.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Row { pub target: usize, pub mt: i32, pub zap: i32, pub lfs: i32, pub lmf: i32 }

pub struct Library {
    pub rows: Vec<Row>,
    /// group cross sections, `rows.len()` x `ngroups`, barns
    pub sig: Vec<f64>,
    pub ngroups: usize,
    pub bounds: Vec<f64>,
}

impl Library {
    pub fn sigma(&self, row: usize) -> &[f64] { &self.sig[row * self.ngroups..(row + 1) * self.ngroups] }
    /// One-group cross section (barns) under a group flux, sum(sig_g phi_g) / sum(phi_g).
    pub fn one_group(&self, row: usize, phi: &[f64]) -> f64 {
        let s = self.sigma(row); let (mut num, mut den) = (0.0, 0.0);
        for g in 0..self.ngroups { num += s[g] * phi[g]; den += phi[g]; }
        if den > 0.0 { num / den } else { 0.0 }
    }
    /// Rows grouped by target index, in file order.
    pub fn by_target(&self) -> HashMap<usize, Vec<usize>> {
        let mut m: HashMap<usize, Vec<usize>> = HashMap::new();
        for (i, r) in self.rows.iter().enumerate() { m.entry(r.target).or_default().push(i); }
        m
    }
}

/// Minimal `.npy` reader for the two dtypes the builder writes: `<i8` (int64) and `<f8` (float64), C order.
fn read_npy(buf: &[u8]) -> Result<(Vec<usize>, bool, Vec<u8>), String> {
    if buf.len() < 10 || &buf[0..6] != b"\x93NUMPY" { return Err("not a .npy file".into()); }
    let (major, hlen_off) = (buf[6], 8usize);
    let hlen = if major == 1 { u16::from_le_bytes([buf[hlen_off], buf[hlen_off + 1]]) as usize } else { u32::from_le_bytes([buf[hlen_off], buf[hlen_off + 1], buf[hlen_off + 2], buf[hlen_off + 3]]) as usize };
    let hstart = if major == 1 { 10 } else { 12 };
    let header = std::str::from_utf8(&buf[hstart..hstart + hlen]).map_err(|e| e.to_string())?;
    let is_f8 = header.contains("<f8");
    if !is_f8 && !header.contains("<i8") { return Err(format!("unsupported dtype in {header}")); }
    if header.contains("'fortran_order': True") { return Err("fortran order not supported".into()); }
    let shape: Vec<usize> = header.split("'shape':").nth(1).ok_or("no shape")?
        .trim_start().trim_start_matches('(').split(')').next().ok_or("bad shape")?
        .split(',').filter_map(|t| t.trim().parse::<usize>().ok()).collect();
    Ok((shape, is_f8, buf[hstart + hlen..].to_vec()))
}

fn as_f64(raw: &[u8]) -> Result<Vec<f64>, String> {
    let (values, remainder) = raw.as_chunks::<8>();
    if !remainder.is_empty() { return Err("truncated f64 array".into()); }
    Ok(values.iter().map(|bytes| f64::from_le_bytes(*bytes)).collect())
}
fn as_i64(raw: &[u8]) -> Result<Vec<i64>, String> {
    let (values, remainder) = raw.as_chunks::<8>();
    if !remainder.is_empty() { return Err("truncated i64 array".into()); }
    Ok(values.iter().map(|bytes| i64::from_le_bytes(*bytes)).collect())
}

/// Read a library written by `controls/tendl_build.py` / `eaflib_build.py`.
pub fn read_npz(path: &str) -> Result<Library, String> {
    let file = std::fs::File::open(path).map_err(|e| e.to_string())?;
    let mut zip = zip::ZipArchive::new(file).map_err(|e| e.to_string())?;
    let mut got: HashMap<String, (Vec<usize>, bool, Vec<u8>)> = HashMap::new();
    for i in 0..zip.len() {
        let mut f = zip.by_index(i).map_err(|e| e.to_string())?;
        let name = f.name().trim_end_matches(".npy").to_string();
        let mut buf = Vec::new(); f.read_to_end(&mut buf).map_err(|e| e.to_string())?;
        got.insert(name, read_npy(&buf)?);
    }
    let (rshape, _, rraw) = got.remove("rows").ok_or("no rows array")?;
    let (sshape, _, sraw) = got.remove("sig").ok_or("no sig array")?;
    let (_, _, braw) = got.remove("bounds").ok_or("no bounds array")?;
    let ri = as_i64(&rraw)?;
    let ncol = *rshape.get(1).unwrap_or(&5);
    let rows: Vec<Row> = ri.chunks_exact(ncol).map(|c| Row { target: c[0] as usize, mt: c[1] as i32, zap: c[2] as i32, lfs: c[3] as i32, lmf: c[4] as i32 }).collect();
    let ngroups = *sshape.get(1).unwrap_or(&709);
    Ok(Library { rows, sig: as_f64(&sraw)?, ngroups, bounds: as_f64(&braw)? })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn truncated_fixed_width_arrays_fail_closed() {
        assert_eq!(as_f64(&[0; 7]).unwrap_err(), "truncated f64 array");
        assert_eq!(as_i64(&[0; 15]).unwrap_err(), "truncated i64 array");
    }
}
