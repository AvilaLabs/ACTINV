//! ENDF-6 decay sublibrary (MF=8/MT=457): half-lives, modes, mean energies and radiation spectra.
//! The general fields mirror `controls/endf_decay.py`; P7 adds independently controlled spectrum records.
use crate::endf::{read_list_checked, read_tab1_checked, tail, ContRecord};
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

fn parse_section(mat: i32, lines: &[&str]) -> Result<Nuclide, String> {
    let head = ContRecord::parse(lines.first().copied().ok_or("empty MF=8/MT=457 section")?)?;
    if !head.c1.is_finite() || head.c1 <= 0.0 || !head.c2.is_finite() || head.c2 <= 0.0 {
        return Err(format!("invalid decay HEAD ZA/AWR {}/{}", head.c1, head.c2));
    }
    let za = head.c1.round() as i32;
    if (head.c1 - f64::from(za)).abs() > 1e-8 {
        return Err(format!("nonintegral decay ZA {}", head.c1));
    }
    let nst = i32::try_from(head.n1).map_err(|_| format!("invalid decay NST {}", head.n1))?;
    let nsp = head.n2;
    let (energy_record, i) = read_list_checked(lines, 1)?;
    let (mode_record, mut i) = read_list_checked(lines, i)?;
    let ndk = mode_record.head.n2;
    let mode_values = ndk
        .checked_mul(6)
        .ok_or("decay-mode field count overflows")?;
    if mode_values > mode_record.values.len() {
        return Err(format!(
            "decay mode LIST contains {} fields for {ndk} modes",
            mode_record.values.len()
        ));
    }
    if nsp > lines.len().saturating_sub(i) {
        return Err(format!(
            "decay HEAD declares {nsp} spectra but only {} records remain",
            lines.len().saturating_sub(i)
        ));
    }
    let modes = (0..ndk)
        .map(|k| Mode {
            rtyp: mode_record.values[6 * k],
            rfs: mode_record.values[6 * k + 1],
            q: mode_record.values[6 * k + 2],
            dq: mode_record.values[6 * k + 3],
            br: mode_record.values[6 * k + 4],
            dbr: mode_record.values[6 * k + 5],
        })
        .collect();
    let mut spectra = Vec::with_capacity(nsp);
    for _ in 0..nsp {
        let (spectrum_record, next) = read_list_checked(lines, i)?;
        i = next;
        let styp = spectrum_record.head.c2;
        let lcon = spectrum_record.head.l1;
        let lcov = spectrum_record.head.l2;
        let ner = spectrum_record.head.n2;
        if ner > lines.len().saturating_sub(i) {
            return Err(format!(
                "decay spectrum declares {ner} discrete records but only {} remain",
                lines.len().saturating_sub(i)
            ));
        }
        let mut discrete = Vec::with_capacity(ner);
        if lcon != 1 {
            for _ in 0..ner {
                let (record, next) = read_list_checked(lines, i)?;
                i = next;
                let at = |k: usize| *record.values.get(k).unwrap_or(&0.0);
                discrete.push(DiscreteRadiation {
                    energy: record.head.c1,
                    d_energy: record.head.c2,
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
            let (record, next) = read_tab1_checked(lines, i)?;
            i = next;
            Some(ContinuousRadiation {
                rtyp: record.head.c1,
                interpolation: record.interpolation,
                points: record.points,
            })
        } else {
            None
        };
        // Covariance records are structurally consumed but are not used until P11.
        if matches!(lcov, 1 | 3) && lcon != 0 {
            let (_, next) = read_list_checked(lines, i)?;
            i = next;
        }
        if matches!(lcov, 2 | 3) {
            let (_, next) = read_list_checked(lines, i)?;
            i = next;
        }
        let at = |k: usize| *spectrum_record.values.get(k).unwrap_or(&0.0);
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
    if i != lines.len() {
        return Err(format!(
            "MF=8/MT=457 contains {} unconsumed record(s)",
            lines.len() - i
        ));
    }
    Ok(Nuclide {
        mat,
        za,
        awr: head.c2,
        liso: head.l2,
        nst,
        half_life: energy_record.head.c1,
        d_half_life: energy_record.head.c2,
        energies: energy_record.values,
        modes,
        spectra,
    })
}

/// Parse a decay sublibrary from text. Key: (ZA, LISO).
pub fn parse_text(text: &str) -> Result<HashMap<(i32, i32), Nuclide>, String> {
    let mut out = HashMap::new();
    let mut cur: Option<(i32, i32, i32)> = None;
    let mut buf: Vec<&str> = Vec::new();
    for line in text.lines() {
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
            let n = parse_section(cur.expect("current decay section").0, &buf)?;
            out.insert((n.za, n.liso), n);
            cur = None;
            buf.clear();
        }
    }
    if let (Some(c), false) = (cur, buf.is_empty()) {
        let n = parse_section(c.0, &buf)?;
        out.insert((n.za, n.liso), n);
    }
    Ok(out)
}

/// Parse a decay sublibrary (single file, many materials). Key: (ZA, LISO).
pub fn parse_file(path: &str) -> std::io::Result<HashMap<(i32, i32), Nuclide>> {
    let text = std::fs::read_to_string(path)?;
    parse_text(&text).map_err(|error| std::io::Error::new(std::io::ErrorKind::InvalidData, error))
}

#[cfg(test)]
mod tests {
    use super::parse_text;

    fn record(values: [&str; 6], mat: i32, mf: i32, mt: i32, sequence: i32) -> String {
        let data: String = values
            .into_iter()
            .map(|value| format!("{value:>11}"))
            .collect();
        format!("{data}{mat:>4}{mf:>2}{mt:>3}{sequence:>5}")
    }

    fn minimal_decay(head: [&str; 6]) -> String {
        [
            record(head, 125, 8, 457, 1),
            record(["1.0", "0", "0", "0", "0", "0"], 125, 8, 457, 2),
            record(["0", "0", "0", "0", "0", "0"], 125, 8, 457, 3),
            record(["", "", "", "", "", ""], 125, 8, 0, 99_999),
        ]
        .join("\n")
    }

    #[test]
    fn parses_minimal_decay_section() {
        let parsed = parse_text(&minimal_decay(["26056", "55.45", "0", "0", "0", "0"]))
            .expect("minimal decay section");
        let nuclide = &parsed[&(26_056, 0)];
        assert_eq!(nuclide.mat, 125);
        assert_eq!(nuclide.half_life, 1.0);
        assert!(nuclide.modes.is_empty());
        assert!(nuclide.spectra.is_empty());
    }

    #[test]
    fn rejects_declared_spectra_before_reserving_memory() {
        let error = parse_text(&minimal_decay([
            "26056",
            "55.45",
            "0",
            "0",
            "0",
            "2000000000",
        ]))
        .unwrap_err();
        assert!(error.contains("declares 2000000000 spectra"));
    }
}
