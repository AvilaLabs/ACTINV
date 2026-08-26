//! ENDF-6 decay sublibrary (MF=8/MT=457): half-lives, modes, mean energies and radiation spectra.
//! The general fields mirror `controls/endf_decay.py`; P7 adds independently controlled spectrum records.
use crate::endf::{endf_float, fields, read_list, read_tab1, tail};
use std::collections::HashMap;

#[derive(Clone, Debug)]
pub struct Mode {
    pub rtyp: f64,
    pub rfs: f64,
    pub q: f64,
    pub dq: f64,
    pub br: f64,
    pub dbr: f64,
}

#[derive(Clone, Debug)]
pub struct DiscreteRadiation {
    pub energy: f64,
    pub d_energy: f64,
    pub rtyp: f64,
    pub transition_type: f64,
    pub intensity: f64,
    pub d_intensity: f64,
    pub pair_intensity: f64,
    pub d_pair_intensity: f64,
    pub conversion_total: f64,
    pub d_conversion_total: f64,
    pub conversion_k: f64,
    pub d_conversion_k: f64,
    pub conversion_l: f64,
    pub d_conversion_l: f64,
}

#[derive(Clone, Debug)]
pub struct ContinuousRadiation {
    pub rtyp: f64,
    /// ENDF one-based `(NBT, INT)` interpolation ranges.
    pub interpolation: Vec<(usize, i32)>,
    /// `(energy_eV, relative_probability_per_eV)`.
    pub points: Vec<(f64, f64)>,
}

#[derive(Clone, Debug)]
pub struct Spectrum {
    pub styp: f64,
    pub lcon: i32,
    pub lcov: i32,
    pub fd: f64,
    pub d_fd: f64,
    pub average_energy: f64,
    pub d_average_energy: f64,
    pub fc: f64,
    pub d_fc: f64,
    pub discrete: Vec<DiscreteRadiation>,
    pub continuous: Option<ContinuousRadiation>,
}

#[derive(Clone, Debug)]
pub struct Nuclide {
    pub mat: i32,
    pub za: i32,
    /// Target mass relative to the neutron mass, from the ENDF HEAD record.
    pub awr: f64,
    pub liso: i32,
    pub nst: i32,
    pub half_life: f64,
    pub d_half_life: f64,
    /// mean energies as stored: [light, dlight, electromagnetic, dem, heavy, dheavy, ...]
    pub energies: Vec<f64>,
    pub modes: Vec<Mode>,
    pub spectra: Vec<Spectrum>,
}

impl Nuclide {
    pub fn z(&self) -> i32 {
        self.za / 1000
    }
    pub fn a(&self) -> i32 {
        self.za % 1000
    }
    /// Decay constant (1/s); zero for stable nuclides.
    pub fn lambda(&self) -> f64 {
        if self.nst == 1 || self.half_life <= 0.0 {
            0.0
        } else {
            std::f64::consts::LN_2 / self.half_life
        }
    }
    pub fn e_light(&self) -> f64 {
        *self.energies.first().unwrap_or(&0.0)
    }
    pub fn e_em(&self) -> f64 {
        *self.energies.get(2).unwrap_or(&0.0)
    }
    pub fn e_heavy(&self) -> f64 {
        *self.energies.get(4).unwrap_or(&0.0)
    }
}

fn parse_section(mat: i32, lines: &[&str]) -> Option<Nuclide> {
    let f = fields(lines[0]);
    let za = endf_float(f[0]).round() as i32;
    let awr = endf_float(f[1]);
    let liso = f[3].trim().parse::<i32>().unwrap_or(0);
    let nst = f[4].trim().parse::<i32>().unwrap_or(0);
    let nsp = f[5].trim().parse::<usize>().unwrap_or(0);
    let ((t12, dt12, _, _, n2c, _, e), i) = read_list(lines, 1);
    let ((_, _, _, _, _, ndk, dk), mut i) = read_list(lines, i);
    let modes = (0..ndk)
        .map(|k| Mode {
            rtyp: dk[6 * k],
            rfs: dk[6 * k + 1],
            q: dk[6 * k + 2],
            dq: dk[6 * k + 3],
            br: dk[6 * k + 4],
            dbr: dk[6 * k + 5],
        })
        .collect();
    let mut spectra = Vec::with_capacity(nsp);
    for _ in 0..nsp {
        let ((_, styp, lcon, lcov, _, ner, norm), next) = read_list(lines, i);
        i = next;
        let mut discrete = Vec::with_capacity(ner);
        if lcon != 1 {
            for _ in 0..ner {
                let ((energy, d_energy, _, _, _, _, v), next) = read_list(lines, i);
                i = next;
                let at = |k: usize| *v.get(k).unwrap_or(&0.0);
                discrete.push(DiscreteRadiation {
                    energy,
                    d_energy,
                    rtyp: at(0),
                    transition_type: at(1),
                    intensity: at(2),
                    d_intensity: at(3),
                    pair_intensity: at(4),
                    d_pair_intensity: at(5),
                    conversion_total: at(6),
                    d_conversion_total: at(7),
                    conversion_k: at(8),
                    d_conversion_k: at(9),
                    conversion_l: at(10),
                    d_conversion_l: at(11),
                });
            }
        }
        let continuous = if lcon != 0 {
            let ((rtyp, _, _, _, interpolation, points), next) = read_tab1(lines, i);
            i = next;
            Some(ContinuousRadiation {
                rtyp,
                interpolation,
                points,
            })
        } else {
            None
        };
        // Covariance records are structurally consumed but are not used until P11.
        if matches!(lcov, 1 | 3) && lcon != 0 {
            let (_, next) = read_list(lines, i);
            i = next;
        }
        if matches!(lcov, 2 | 3) {
            let (_, next) = read_list(lines, i);
            i = next;
        }
        let at = |k: usize| *norm.get(k).unwrap_or(&0.0);
        spectra.push(Spectrum {
            styp,
            lcon,
            lcov,
            fd: at(0),
            d_fd: at(1),
            average_energy: at(2),
            d_average_energy: at(3),
            fc: at(4),
            d_fc: at(5),
            discrete,
            continuous,
        });
    }
    Some(Nuclide {
        mat,
        za,
        awr,
        liso,
        nst,
        half_life: t12,
        d_half_life: dt12,
        energies: e[..n2c.min(e.len())].to_vec(),
        modes,
        spectra,
    })
}

/// Parse a decay sublibrary (single file, many materials). Key: (ZA, LISO).
pub fn parse_file(path: &str) -> std::io::Result<HashMap<(i32, i32), Nuclide>> {
    let text = std::fs::read_to_string(path)?;
    let lines: Vec<&str> = text.lines().collect();
    let mut out = HashMap::new();
    let mut cur: Option<(i32, i32, i32)> = None;
    let mut buf: Vec<&str> = Vec::new();
    for line in lines {
        let t = match tail(line) {
            Some(t) => t,
            None => continue,
        };
        if t.1 == 8 && t.2 == 457 {
            if cur != Some(t) {
                cur = Some(t);
                buf.clear();
            }
            buf.push(line);
        } else if cur.is_some() && !buf.is_empty() {
            if let Some(n) = parse_section(cur.unwrap().0, &buf) {
                out.insert((n.za, n.liso), n);
            }
            cur = None;
            buf.clear();
        }
    }
    if let (Some(c), false) = (cur, buf.is_empty()) {
        if let Some(n) = parse_section(c.0, &buf) {
            out.insert((n.za, n.liso), n);
        }
    }
    Ok(out)
}
