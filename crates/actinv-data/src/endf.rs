//! ENDF-6 primitives: the fixed-width record layout and the library's own float notation (`1.234-5` = 1.234e-5).

/// Parse an ENDF numeric field: standard floats plus the exponent-without-`e` form used throughout ENDF.
pub fn endf_float(s: &str) -> f64 {
    let t = s.trim();
    if t.is_empty() { return 0.0; }
    if let Ok(v) = t.parse::<f64>() { return v; }
    // find an exponent sign after the first character (e.g. "1.234-5", "-1.234+5")
    let b = t.as_bytes();
    for i in 1..b.len() {
        if (b[i] == b'+' || b[i] == b'-') && b[i - 1] != b'e' && b[i - 1] != b'E' {
            let (m, e) = t.split_at(i);
            if let (Ok(mv), Ok(ev)) = (m.parse::<f64>(), e.parse::<i32>()) { return mv * 10f64.powi(ev); }
        }
    }
    0.0
}

/// The six 11-character data fields of an ENDF record.
pub fn fields(line: &str) -> [&str; 6] {
    let mut out = [""; 6];
    for (i, o) in out.iter_mut().enumerate() {
        let (a, b) = (i * 11, (i + 1) * 11);
        *o = if line.len() >= b { &line[a..b] } else if line.len() > a { &line[a..] } else { "" };
    }
    out
}

/// (MAT, MF, MT) from the tail of an ENDF record, if present.
pub fn tail(line: &str) -> Option<(i32, i32, i32)> {
    if line.len() < 75 { return None; }
    let mat = line[66..70].trim().parse::<i32>().ok()?;
    let mf = line[70..72].trim().parse::<i32>().ok()?;
    let mt = line[72..75].trim().parse::<i32>().ok()?;
    Some((mat, mf, mt))
}

/// Read a LIST record starting at `i`: returns ((C1, C2, L1, L2, N1, N2, values), next index).
pub fn read_list(lines: &[&str], mut i: usize) -> ((f64, f64, i32, i32, usize, usize, Vec<f64>), usize) {
    let f = fields(lines[i]);
    let (c1, c2) = (endf_float(f[0]), endf_float(f[1]));
    let l1 = f[2].trim().parse::<i32>().unwrap_or(0);
    let l2 = f[3].trim().parse::<i32>().unwrap_or(0);
    let n1 = f[4].trim().parse::<usize>().unwrap_or(0);
    let n2 = f[5].trim().parse::<usize>().unwrap_or(0);
    i += 1;
    let mut vals = Vec::with_capacity(n1);
    while vals.len() < n1 {
        let g = fields(lines[i]);
        for k in 0..6 { if vals.len() < n1 { vals.push(endf_float(g[k])); } }
        i += 1;
    }
    ((c1, c2, l1, l2, n1, n2, vals), i)
}
