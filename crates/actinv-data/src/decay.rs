//! ENDF-6 decay sublibrary (MF=8/MT=457): half-lives, decay modes with branching ratios, and mean decay energies.
//! Mirrors controls/endf_decay.py exactly; G1 requires the two to agree to 1e-12 on every material.
use std::collections::HashMap;
use crate::endf::{endf_float, fields, read_list, tail};

#[derive(Clone, Debug)]
pub struct Mode { pub rtyp: f64, pub rfs: f64, pub q: f64, pub dq: f64, pub br: f64, pub dbr: f64 }

#[derive(Clone, Debug)]
pub struct Nuclide {
    pub mat: i32, pub za: i32, pub liso: i32, pub nst: i32,
    pub half_life: f64, pub d_half_life: f64,
    /// mean energies as stored: [light, dlight, electromagnetic, dem, heavy, dheavy, ...]
    pub energies: Vec<f64>,
    pub modes: Vec<Mode>,
}

impl Nuclide {
    pub fn z(&self) -> i32 { self.za / 1000 }
    pub fn a(&self) -> i32 { self.za % 1000 }
    /// Decay constant (1/s); zero for stable nuclides.
    pub fn lambda(&self) -> f64 { if self.nst == 1 || self.half_life <= 0.0 { 0.0 } else { std::f64::consts::LN_2 / self.half_life } }
    pub fn e_light(&self) -> f64 { *self.energies.first().unwrap_or(&0.0) }
    pub fn e_em(&self) -> f64 { *self.energies.get(2).unwrap_or(&0.0) }
    pub fn e_heavy(&self) -> f64 { *self.energies.get(4).unwrap_or(&0.0) }
}

fn parse_section(mat: i32, lines: &[&str]) -> Option<Nuclide> {
    let f = fields(lines[0]);
    let za = endf_float(f[0]).round() as i32;
    let liso = f[3].trim().parse::<i32>().unwrap_or(0);
    let nst = f[4].trim().parse::<i32>().unwrap_or(0);
    let ((t12, dt12, _, _, n2c, _, e), i) = read_list(lines, 1);
    let ((_, _, _, _, _, ndk, dk), _) = read_list(lines, i);
    let modes = (0..ndk).map(|k| Mode {
        rtyp: dk[6 * k], rfs: dk[6 * k + 1], q: dk[6 * k + 2], dq: dk[6 * k + 3], br: dk[6 * k + 4], dbr: dk[6 * k + 5],
    }).collect();
    Some(Nuclide { mat, za, liso, nst, half_life: t12, d_half_life: dt12, energies: e[..n2c.min(e.len())].to_vec(), modes })
}

/// Parse a decay sublibrary (single file, many materials). Key: (ZA, LISO).
pub fn parse_file(path: &str) -> std::io::Result<HashMap<(i32, i32), Nuclide>> {
    let text = std::fs::read_to_string(path)?;
    let lines: Vec<&str> = text.lines().collect();
    let mut out = HashMap::new();
    let mut cur: Option<(i32, i32, i32)> = None;
    let mut buf: Vec<&str> = Vec::new();
    for line in lines {
        let t = match tail(line) { Some(t) => t, None => continue };
        if t.1 == 8 && t.2 == 457 {
            if cur != Some(t) { cur = Some(t); buf.clear(); }
            buf.push(line);
        } else if cur.is_some() && !buf.is_empty() {
            if let Some(n) = parse_section(cur.unwrap().0, &buf) { out.insert((n.za, n.liso), n); }
            cur = None; buf.clear();
        }
    }
    if let (Some(c), false) = (cur, buf.is_empty()) {
        if let Some(n) = parse_section(c.0, &buf) { out.insert((n.za, n.liso), n); }
    }
    Ok(out)
}
