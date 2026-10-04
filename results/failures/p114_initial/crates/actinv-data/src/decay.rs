//! ENDF-6 decay sublibrary (MF=8/MT=457): half-lives, modes, mean energies and radiation spectra.
//! The general fields mirror `controls/endf_decay.py`; P7 adds independently controlled spectrum records.
#![allow(non_snake_case)] // elis_eV matches the ENDF field vocabulary used across the crate.
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
/// One record per (ZA, LISO): a second would silently replace the first by file order.
fn insert_unique(out: &mut HashMap<(i32, i32), Nuclide>, nuclide: Nuclide) -> Result<(), String> {
    let key = (nuclide.za, nuclide.liso);
    if out.insert(key, nuclide).is_some() {
        return Err(format!(
            "duplicate decay record for ZA={} LISO={}",
            key.0, key.1
        ));
    }
    Ok(())
}

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
            insert_unique(&mut out, n)?;
            cur = None;
            buf.clear();
        }
    }
    if let (Some(c), false) = (cur, buf.is_empty()) {
        let n = parse_section(c.0, &buf)?;
        insert_unique(&mut out, n)?;
    }
    Ok(out)
}

/// Parse a decay sublibrary (single file, many materials). Key: (ZA, LISO).
pub fn parse_file(path: &str) -> std::io::Result<HashMap<(i32, i32), Nuclide>> {
    let text = std::fs::read_to_string(path)?;
    parse_text(&text).map_err(|error| std::io::Error::new(std::io::ErrorKind::InvalidData, error))
}

/// One curated decay entry in an `actinv-decay-overrides-1` document.
/// Declared fields replace the evaluation's values wholesale; `modes`,
/// when present, replaces the entire decay-mode list. `source` and
/// `reason` are mandatory so an override can never be anonymous — the
/// record lands in the run ledger verbatim.
#[derive(Debug, Clone)]
pub struct DecayOverride {
    /// Nuclide name as written in the file, e.g. "Ta182m2".
    pub name: String,
    /// (ZA, LISO) resolved via the shared nuclide-key parser.
    pub key: (i32, i32),
    pub half_life_s: Option<f64>,
    pub d_half_life_s: Option<f64>,
    pub e_light_eV: Option<f64>,
    pub e_em_eV: Option<f64>,
    pub e_heavy_eV: Option<f64>,
    /// Complete replacement mode list: (rtyp, rfs, q_eV, br).
    pub modes: Option<Vec<Mode>>,
    pub source: String,
    pub reason: String,
}

/// Parse an `actinv-decay-overrides-1` JSON document.
pub fn parse_overrides(text: &str) -> Result<Vec<DecayOverride>, String> {
    let doc: serde_json::Value =
        serde_json::from_str(text).map_err(|error| format!("decay overrides: {error}"))?;
    if doc["schema"].as_str() != Some("actinv-decay-overrides-1") {
        return Err("decay overrides: schema must be 'actinv-decay-overrides-1'".into());
    }
    let entries = doc["overrides"]
        .as_array()
        .ok_or("decay overrides: 'overrides' must be an array")?;
    let mut out = Vec::with_capacity(entries.len());
    for entry in entries {
        let name = entry["nuclide"]
            .as_str()
            .ok_or("decay overrides: entry needs 'nuclide'")?;
        let key = match crate::composition::material_key(name)
            .map_err(|error| format!("decay overrides: '{name}': {error}"))?
        {
            crate::composition::MaterialKey::Nuclide { za, liso, .. } => (za, liso),
            _ => {
                return Err(format!(
                    "decay overrides: '{name}' must be an explicit nuclide"
                ))
            }
        };
        let opt = |field: &str| -> Result<Option<f64>, String> {
            match entry.get(field) {
                None | Some(serde_json::Value::Null) => Ok(None),
                Some(v) => v.as_f64().map(Some).ok_or_else(|| {
                    format!("decay overrides: '{name}' field '{field}' must be numeric")
                }),
            }
        };
        let modes = match entry.get("modes") {
            None | Some(serde_json::Value::Null) => None,
            Some(serde_json::Value::Array(list)) => {
                let mut parsed = Vec::with_capacity(list.len());
                for mode in list {
                    let grab = |field: &str| -> Result<f64, String> {
                        mode[field].as_f64().ok_or_else(|| {
                            format!("decay overrides: '{name}' mode needs numeric '{field}'")
                        })
                    };
                    parsed.push(Mode {
                        rtyp: grab("rtyp")?,
                        rfs: grab("rfs")?,
                        q: grab("q_eV")?,
                        dbr: 0.0,
                        dq: 0.0,
                        br: grab("br")?,
                    });
                }
                Some(parsed)
            }
            _ => {
                return Err(format!(
                    "decay overrides: '{name}' 'modes' must be an array"
                ))
            }
        };
        let source = entry["source"]
            .as_str()
            .filter(|s| !s.is_empty())
            .ok_or("decay overrides: entry needs a non-empty 'source'")?
            .to_string();
        let reason = entry["reason"]
            .as_str()
            .filter(|s| !s.is_empty())
            .ok_or("decay overrides: entry needs a non-empty 'reason'")?
            .to_string();
        let item = DecayOverride {
            name: name.to_string(),
            key,
            half_life_s: opt("half_life_s")?,
            d_half_life_s: opt("d_half_life_s")?,
            e_light_eV: opt("e_light_eV")?,
            e_em_eV: opt("e_em_eV")?,
            e_heavy_eV: opt("e_heavy_eV")?,
            modes,
            source,
            reason,
        };
        if item.half_life_s.is_none()
            && item.d_half_life_s.is_none()
            && item.e_light_eV.is_none()
            && item.e_em_eV.is_none()
            && item.e_heavy_eV.is_none()
            && item.modes.is_none()
        {
            return Err(format!("decay overrides: '{name}' changes no decay field"));
        }
        out.push(item);
    }
    Ok(out)
}

/// Apply curated overrides onto a parsed decay table, in file order.
/// Returns the ledger lines naming each patched nuclide and field.
pub fn apply_overrides(
    nuclides: &mut HashMap<(i32, i32), Nuclide>,
    overrides: &[DecayOverride],
) -> Result<Vec<String>, String> {
    let mut applied = Vec::with_capacity(overrides.len());
    for item in overrides {
        let nuclide = nuclides.get_mut(&item.key).ok_or_else(|| {
            format!(
                "decay overrides: '{}' (ZA={} LISO={}) absent from the decay library",
                item.name, item.key.0, item.key.1
            )
        })?;
        let mut fields = Vec::new();
        if let Some(value) = item.half_life_s {
            if !(value.is_finite() && value > 0.0) {
                return Err(format!(
                    "decay overrides: '{}' half_life_s must be finite and positive",
                    item.name
                ));
            }
            nuclide.half_life = value;
            nuclide.nst = 0;
            fields.push(format!("half_life_s={value}"));
        }
        if let Some(value) = item.d_half_life_s {
            if !(value.is_finite() && value >= 0.0) {
                return Err(format!(
                    "decay overrides: '{}' d_half_life_s must be finite and nonnegative",
                    item.name
                ));
            }
            nuclide.d_half_life = value;
            fields.push(format!("d_half_life_s={value}"));
        }
        for (slot, value) in [
            (0usize, item.e_light_eV),
            (2, item.e_em_eV),
            (4, item.e_heavy_eV),
        ] {
            if let Some(v) = value {
                if !(v.is_finite() && v >= 0.0) {
                    return Err(format!(
                        "decay overrides: '{}' decay energy must be finite and nonnegative",
                        item.name
                    ));
                }
                while nuclide.energies.len() <= slot {
                    nuclide.energies.push(0.0);
                }
                nuclide.energies[slot] = v;
                fields.push(format!("energies[{slot}]={v}"));
            }
        }
        if let Some(modes) = &item.modes {
            let sum: f64 = modes.iter().map(|m| m.br).sum();
            if (sum - 1.0).abs() > 1e-6 {
                return Err(format!(
                    "decay overrides: '{}' branching fractions sum to {sum}, not 1",
                    item.name
                ));
            }
            nuclide.modes = modes.clone();
            fields.push(format!("modes:{}", modes.len()));
        }
        applied.push(format!(
            "{} ({}): {} [{}]",
            item.name,
            item.source,
            fields.join(", "),
            item.reason
        ));
    }
    Ok(applied)
}

/// One decay-sublibrary state, from the second MF=1/MT=451 record of each
/// material: excitation energy `elis_eV`, evaluator level index `lis` and
/// isomer ordinal `liso`. The chain index keys states on (ZA, LISO), but
/// cross-section evaluations declare products by LIS-like labels and their
/// own per-file LISO values that need not agree with the decay sublibrary's
/// numbering — TENDL-2017's Ta-182M file declares LISO=1 for the state
/// ENDF/B-VIII decay numbers LIS=29 / LISO=2 (the 519.58 keV, 15.8-minute
/// level). LIS and ELIS are the cross-library identifiers.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct DecayStateEntry {
    pub liso: i32,
    pub lis: i32,
    pub elis_eV: f64,
}

/// Scan a decay sublibrary for its MF=1/MT=451 state records and return each
/// nuclide's states keyed by ZA, ordered by LISO. Only record two of each
/// material's MF=1/MT=451 section carries (ELIS, LIS, LISO); the record
/// sequence number in columns 75-79 identifies it.
pub fn state_table(text: &str) -> Result<HashMap<i32, Vec<DecayStateEntry>>, String> {
    let mut out: HashMap<i32, Vec<DecayStateEntry>> = HashMap::new();
    let mut material_za: HashMap<i32, i32> = HashMap::new();
    for line in text.lines() {
        let Some((mat, mf, mt)) = tail(line) else {
            continue;
        };
        if mf != 1 || mt != 451 {
            continue;
        }
        let sequence: i32 = line
            .get(75..80)
            .and_then(|field| field.trim().parse().ok())
            .unwrap_or(-1);
        match sequence {
            1 => {
                material_za.insert(mat, ContRecord::parse(line)?.c1.round() as i32);
            }
            2 => {
                let record = ContRecord::parse(line)?;
                if let Some(&za) = material_za.get(&mat) {
                    out.entry(za).or_default().push(DecayStateEntry {
                        liso: record.l2,
                        lis: record.l1,
                        elis_eV: record.c1,
                    });
                }
            }
            _ => {}
        }
    }
    for states in out.values_mut() {
        states.sort_by_key(|state| state.liso);
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::{parse_text, Nuclide};

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
    fn duplicate_records_are_an_error_not_last_wins() {
        let section = minimal_decay(["26056", "55.45", "0", "0", "0", "0"]);
        let error = parse_text(&format!("{section}\n{section}")).unwrap_err();
        assert!(
            error.contains("duplicate decay record for ZA=26056"),
            "{error}"
        );
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

    #[test]
    fn state_table_reads_elis_lis_liso_from_mf1() {
        // Ta-182's three decay materials: ground plus the two isomers the
        // sublibrary numbers by level index (LIS=1 -> LISO=1 at 16.263 keV;
        // LIS=29 -> LISO=2 at 519.587 keV).
        let text = [
            record(["73182", "180.4", "0", "0", "0", "0"], 2659, 1, 451, 1),
            record(["0", "1.0", "0", "0", "0", "6"], 2659, 1, 451, 2),
            record(["73182", "180.4", "-1", "0", "0", "0"], 2660, 1, 451, 1),
            record(["16263", "1.0", "1", "1", "0", "6"], 2660, 1, 451, 2),
            record(["73182", "180.4", "-1", "0", "0", "0"], 2661, 1, 451, 1),
            record(["519587", "1.0", "29", "2", "0", "6"], 2661, 1, 451, 2),
        ]
        .join("\n");
        let table = super::state_table(&text).expect("state table");
        assert_eq!(
            table[&73182],
            vec![
                super::DecayStateEntry {
                    liso: 0,
                    lis: 0,
                    elis_eV: 0.0
                },
                super::DecayStateEntry {
                    liso: 1,
                    lis: 1,
                    elis_eV: 16_263.0
                },
                super::DecayStateEntry {
                    liso: 2,
                    lis: 29,
                    elis_eV: 519_587.0
                },
            ]
        );
    }

    fn two_nuclide_map() -> std::collections::HashMap<(i32, i32), Nuclide> {
        let nu = |za: i32, awr: f64| Nuclide {
            mat: 100,
            za,
            awr,
            liso: 0,
            nst: 0,
            half_life: 3.0,
            d_half_life: 0.0,
            energies: vec![0.7e6, 0.0, 1.1e6, 0.0, 0.2e6, 0.0],
            modes: vec![],
            spectra: vec![],
        };
        let mut m = std::collections::HashMap::new();
        m.insert((25057, 0), nu(25057, 56.45));
        m.insert((25056, 0), nu(25056, 55.46));
        m
    }

    #[test]
    fn overrides_parse_and_apply() {
        let mut map = two_nuclide_map();
        let doc = r#"{"schema":"actinv-decay-overrides-1","overrides":[
            {"nuclide":"Mn57","half_life_s":6.0,"source":"test","reason":"t12 patch"},
            {"nuclide":"Mn56","e_em_eV":2.2e6,"source":"test","reason":"energy patch"}
        ]}"#;
        let ovs = super::parse_overrides(doc).unwrap();
        let applied = super::apply_overrides(&mut map, &ovs).unwrap();
        assert_eq!(applied.len(), 2);
        let mn57 = &map[&(25057, 0)];
        assert_eq!(mn57.half_life, 6.0);
        let mn56 = &map[&(25056, 0)];
        assert_eq!(mn56.energies[2], 2.2e6);
        assert!(applied[0].contains("Mn57") && applied[0].contains("half_life_s"));
        assert!(applied[0].contains("test") && applied[0].contains("t12 patch"));
    }

    #[test]
    fn overrides_reject_bad_documents() {
        // wrong schema
        assert!(super::parse_overrides(r#"{"schema":"x","overrides":[]}"#).is_err());
        // missing overrides array
        assert!(super::parse_overrides(r#"{"schema":"actinv-decay-overrides-1"}"#).is_err());
        // non-explicit nuclide name
        assert!(super::parse_overrides(r#"{"schema":"actinv-decay-overrides-1","overrides":[{"nuclide":"Fe","half_life_s":1.0,"source":"s","reason":"r"}]}"#).is_err());
        // no changed fields
        assert!(super::parse_overrides(r#"{"schema":"actinv-decay-overrides-1","overrides":[{"nuclide":"Mn57","source":"s","reason":"r"}]}"#).is_err());
        // missing source / reason
        assert!(super::parse_overrides(r#"{"schema":"actinv-decay-overrides-1","overrides":[{"nuclide":"Mn57","half_life_s":1.0,"reason":"r"}]}"#).is_err());
        assert!(super::parse_overrides(r#"{"schema":"actinv-decay-overrides-1","overrides":[{"nuclide":"Mn57","half_life_s":1.0,"source":"s"}]}"#).is_err());
        // non-numeric field
        assert!(super::parse_overrides(r#"{"schema":"actinv-decay-overrides-1","overrides":[{"nuclide":"Mn57","half_life_s":"six","source":"s","reason":"r"}]}"#).is_err());
    }

    #[test]
    fn overrides_reject_unsafe_values() {
        let mut map = two_nuclide_map();
        // unknown nuclide
        let bad = super::parse_overrides(r#"{"schema":"actinv-decay-overrides-1","overrides":[{"nuclide":"Mn99","half_life_s":1.0,"source":"s","reason":"r"}]}"#).unwrap();
        assert!(super::apply_overrides(&mut map, &bad).is_err());
        // non-positive half-life
        let bad = super::parse_overrides(r#"{"schema":"actinv-decay-overrides-1","overrides":[{"nuclide":"Mn57","half_life_s":-1.0,"source":"s","reason":"r"}]}"#).unwrap();
        assert!(super::apply_overrides(&mut map, &bad).is_err());
        // negative energy
        let bad = super::parse_overrides(r#"{"schema":"actinv-decay-overrides-1","overrides":[{"nuclide":"Mn57","e_light_eV":-2.0,"source":"s","reason":"r"}]}"#).unwrap();
        assert!(super::apply_overrides(&mut map, &bad).is_err());
        // branching fractions must sum to ~1
        let bad = super::parse_overrides(r#"{"schema":"actinv-decay-overrides-1","overrides":[{"nuclide":"Mn57","modes":[{"rtyp":1.0,"rfs":0.0,"br":0.4,"q_eV":1e6}],"source":"s","reason":"r"}]}"#).unwrap();
        assert!(super::apply_overrides(&mut map, &bad).is_err());
    }
}
