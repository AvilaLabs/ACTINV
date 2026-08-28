//! ENDF-6 neutron-induced fission-product yields (MF=8/MT=454 and MT=459).
//!
//! Independent yields are the production source. Cumulative yields are retained only so callers can inspect and
//! cross-check the evaluation; they must never be substituted into a depletion matrix.
use crate::endf::{fields, tail};
use std::collections::{BTreeMap, HashSet};

pub type NuclideKey = (i32, i32);

#[derive(Clone, Copy, Debug)]
pub struct YieldValue {
    pub value: f64,
    pub uncertainty: f64,
}

#[derive(Clone, Debug)]
pub struct YieldTable {
    pub energy_ev: f64,
    pub products: BTreeMap<NuclideKey, YieldValue>,
    pub sum: f64,
}

#[derive(Clone, Debug)]
pub struct FissionYields {
    pub parent: NuclideKey,
    pub awr: f64,
    pub independent: Vec<YieldTable>,
    pub cumulative: Vec<YieldTable>,
}

#[derive(Clone, Debug)]
pub struct EffectiveYields {
    pub requested_energy_ev: f64,
    pub lower_energy_ev: f64,
    pub upper_energy_ev: f64,
    pub upper_weight: f64,
    pub clamped: bool,
    pub products: BTreeMap<NuclideKey, f64>,
    pub sum: f64,
}

struct RawList {
    energy: f64,
    n_values: usize,
    n_products: usize,
    values: Vec<f64>,
}

fn parse_float(field: &str) -> Result<f64, String> {
    let text = field.trim();
    if text.is_empty() {
        return Ok(0.0);
    }
    if let Ok(value) = text.parse::<f64>() {
        return value
            .is_finite()
            .then_some(value)
            .ok_or_else(|| format!("nonfinite ENDF value '{text}'"));
    }
    let bytes = text.as_bytes();
    for index in 1..bytes.len() {
        if matches!(bytes[index], b'+' | b'-') && !matches!(bytes[index - 1], b'e' | b'E') {
            let (mantissa, exponent) = text.split_at(index);
            if let (Ok(mantissa), Ok(exponent)) = (mantissa.parse::<f64>(), exponent.parse::<i32>())
            {
                let value = mantissa * 10f64.powi(exponent);
                return value
                    .is_finite()
                    .then_some(value)
                    .ok_or_else(|| format!("nonfinite ENDF value '{text}'"));
            }
        }
    }
    Err(format!("invalid ENDF number '{text}'"))
}

fn parse_i32(field: &str, name: &str) -> Result<i32, String> {
    field
        .trim()
        .parse::<i32>()
        .map_err(|_| format!("invalid ENDF {name} '{}'", field.trim()))
}

fn parse_usize(field: &str, name: &str) -> Result<usize, String> {
    field
        .trim()
        .parse::<usize>()
        .map_err(|_| format!("invalid ENDF {name} '{}'", field.trim()))
}

fn section<'a>(lines: &'a [&'a str], mf: i32, mt: i32) -> Result<Option<Vec<&'a str>>, String> {
    let mut out = Vec::new();
    let mut seen = false;
    let mut ended = false;
    for line in lines {
        match tail(line) {
            Some((_, got_mf, got_mt)) if got_mf == mf && got_mt == mt => {
                if ended {
                    return Err(format!("duplicate MF={mf}/MT={mt} section"));
                }
                seen = true;
                out.push(*line);
            }
            Some((_, got_mf, 0)) if seen && got_mf == mf => ended = true,
            _ if seen && !ended => {
                return Err(format!("MF={mf}/MT={mt} section ended without SEND"));
            }
            _ => {}
        }
    }
    if seen && !ended {
        return Err(format!("MF={mf}/MT={mt} section ended without SEND"));
    }
    Ok(seen.then_some(out))
}

fn list_record(lines: &[&str], index: usize) -> Result<(RawList, usize), String> {
    let line = lines
        .get(index)
        .ok_or_else(|| "truncated ENDF LIST header".to_string())?;
    if line.len() < 66 {
        return Err("truncated ENDF LIST header".into());
    }
    let header = fields(line);
    let energy = parse_float(header[0])?;
    let n_values = parse_usize(header[4], "NPL")?;
    let n_products = parse_usize(header[5], "NFP")?;
    let records = n_values.div_ceil(6);
    let payload_start = index
        .checked_add(1)
        .ok_or("ENDF LIST payload index overflows")?;
    let payload_end = payload_start
        .checked_add(records)
        .ok_or("ENDF LIST payload length overflows")?;
    if payload_end > lines.len() {
        return Err("truncated ENDF LIST payload".into());
    }
    let mut values = Vec::with_capacity(n_values);
    for line in &lines[payload_start..payload_end] {
        if line.len() < 66 {
            return Err("truncated ENDF LIST payload record".into());
        }
        for field in fields(line) {
            if values.len() == n_values {
                break;
            }
            values.push(parse_float(field)?);
        }
    }
    if values.len() != n_values {
        return Err("truncated ENDF LIST payload".into());
    }
    Ok((
        RawList {
            energy,
            n_values,
            n_products,
            values,
        },
        payload_end,
    ))
}

fn yield_section(lines: &[&str], mt: i32) -> Result<(i32, f64, Vec<YieldTable>), String> {
    let head = lines
        .first()
        .ok_or_else(|| format!("empty MF=8/MT={mt} section"))?;
    if head.len() < 66 {
        return Err(format!("truncated MF=8/MT={mt} HEAD"));
    }
    let f = fields(head);
    let za_value = parse_float(f[0])?;
    let za = za_value.round() as i32;
    if za <= 0 || (za_value - f64::from(za)).abs() > 1e-8 {
        return Err(format!("invalid fission parent ZA {za_value}"));
    }
    let awr = parse_float(f[1])?;
    if awr <= 0.0 {
        return Err(format!("invalid fission parent AWR {awr}"));
    }
    let n_energies = parse_i32(f[2], "LE")?;
    if n_energies <= 0 {
        return Err(format!("MF=8/MT={mt} has no incident energies"));
    }
    let n_energies = usize::try_from(n_energies)
        .map_err(|_| format!("invalid MF=8/MT={mt} incident-energy count"))?;
    if n_energies > lines.len().saturating_sub(1) {
        return Err(format!(
            "MF=8/MT={mt} declares {n_energies} incident energies but only {} records remain",
            lines.len().saturating_sub(1)
        ));
    }
    let mut index = 1usize;
    let mut tables = Vec::with_capacity(n_energies);
    let mut seen_energies = HashSet::new();
    for _ in 0..n_energies {
        let (record, next) = list_record(lines, index)?;
        index = next;
        let RawList {
            energy,
            n_values,
            n_products,
            values,
        } = record;
        if !energy.is_finite() || energy < 0.0 {
            return Err(format!("invalid incident energy {energy}"));
        }
        let expected_values = n_products
            .checked_mul(4)
            .ok_or("fission-yield product field count overflows")?;
        if n_values != expected_values {
            return Err(format!(
                "MF=8/MT={mt} LIST has NPL={n_values}, expected 4*NFP={}",
                expected_values
            ));
        }
        if !seen_energies.insert(energy.to_bits()) {
            return Err(format!("duplicate incident energy {energy}"));
        }
        let mut products = BTreeMap::new();
        let (product_values, remainder) = values.as_chunks::<4>();
        debug_assert!(remainder.is_empty());
        for values in product_values {
            let za_value = values[0];
            let product_za = za_value.round() as i32;
            let state_value = values[1];
            let state = state_value.round() as i32;
            let value = values[2];
            let uncertainty = values[3];
            if product_za <= 0
                || (za_value - f64::from(product_za)).abs() > 1e-8
                || state < 0
                || (state_value - f64::from(state)).abs() > 1e-8
            {
                return Err(format!(
                    "invalid fission product ({za_value}, {state_value})"
                ));
            }
            if !value.is_finite() || value < 0.0 || !uncertainty.is_finite() || uncertainty < 0.0 {
                return Err(format!("invalid fission yield for ({product_za}, {state})"));
            }
            if products
                .insert((product_za, state), YieldValue { value, uncertainty })
                .is_some()
            {
                return Err(format!(
                    "duplicate fission product ({product_za}, {state}) at {energy} eV"
                ));
            }
        }
        let sum: f64 = products.values().map(|value| value.value).sum();
        if mt == 454 && (sum - 2.0).abs() > 1e-6 {
            return Err(format!(
                "independent fission yields at {energy} eV sum to {sum:.17e}, not 2 within 1e-6"
            ));
        }
        tables.push(YieldTable {
            energy_ev: energy,
            products,
            sum,
        });
    }
    if index != lines.len() {
        return Err(format!(
            "MF=8/MT={mt} contains {} unconsumed records",
            lines.len() - index
        ));
    }
    tables.sort_by(|left, right| left.energy_ev.total_cmp(&right.energy_ev));
    Ok((za, awr, tables))
}

/// Parse one ENDF fission-yield evaluation from text.
pub fn parse_text(text: &str) -> Result<FissionYields, String> {
    let lines: Vec<&str> = text.lines().collect();
    let independent_lines = section(&lines, 8, 454)?
        .ok_or_else(|| "fission-yield evaluation has no MF=8/MT=454 section".to_string())?;
    let (parent_za, awr, independent) = yield_section(&independent_lines, 454)?;

    let parent_liso = section(&lines, 1, 451)?
        .and_then(|section| section.get(1).copied())
        .map(fields)
        .map(|values| parse_i32(values[3], "LISO"))
        .transpose()?
        .ok_or_else(|| {
            "fission-yield evaluation has no MF=1/MT=451 target-state record".to_string()
        })?;
    if parent_liso < 0 {
        return Err(format!("invalid fission parent LISO {parent_liso}"));
    }

    let cumulative = match section(&lines, 8, 459)? {
        Some(section) => {
            let (za, cumulative_awr, tables) = yield_section(&section, 459)?;
            if za != parent_za || cumulative_awr.to_bits() != awr.to_bits() {
                return Err("MT=454 and MT=459 parent records differ".into());
            }
            if tables.len() != independent.len()
                || tables
                    .iter()
                    .zip(&independent)
                    .any(|(left, right)| left.energy_ev.to_bits() != right.energy_ev.to_bits())
            {
                return Err("MT=454 and MT=459 incident-energy grids differ".into());
            }
            tables
        }
        None => Vec::new(),
    };
    Ok(FissionYields {
        parent: (parent_za, parent_liso),
        awr,
        independent,
        cumulative,
    })
}

/// Parse one ENDF fission-yield evaluation.
pub fn parse_file(path: &str) -> Result<FissionYields, String> {
    let text = std::fs::read_to_string(path)
        .map_err(|error| format!("cannot read fission yields {path}: {error}"))?;
    parse_text(&text).map_err(|error| format!("{path}: {error}"))
}

impl FissionYields {
    /// Select, clamp or linearly interpolate independent yields at an incident energy.
    pub fn effective(&self, energy_ev: f64) -> Result<EffectiveYields, String> {
        if !energy_ev.is_finite() || energy_ev < 0.0 {
            return Err(format!("invalid fission-yield incident energy {energy_ev}"));
        }
        let first = self
            .independent
            .first()
            .ok_or("fission-yield evaluation has no independent tables")?;
        let last = self.independent.last().unwrap();
        let (lower, upper, weight, clamped) = if energy_ev <= first.energy_ev {
            (first, first, 0.0, energy_ev < first.energy_ev)
        } else if energy_ev >= last.energy_ev {
            (last, last, 0.0, energy_ev > last.energy_ev)
        } else {
            let upper_index = self
                .independent
                .partition_point(|table| table.energy_ev <= energy_ev);
            let lower = &self.independent[upper_index - 1];
            let upper = &self.independent[upper_index];
            let weight = (energy_ev - lower.energy_ev) / (upper.energy_ev - lower.energy_ev);
            (lower, upper, weight, false)
        };
        let mut keys: Vec<_> = lower
            .products
            .keys()
            .chain(upper.products.keys())
            .copied()
            .collect();
        keys.sort_unstable();
        keys.dedup();
        let products: BTreeMap<_, _> = keys
            .into_iter()
            .map(|key| {
                let low = lower.products.get(&key).map_or(0.0, |value| value.value);
                let high = upper.products.get(&key).map_or(0.0, |value| value.value);
                (key, low * (1.0 - weight) + high * weight)
            })
            .collect();
        let sum: f64 = products.values().sum();
        Ok(EffectiveYields {
            requested_energy_ev: energy_ev,
            lower_energy_ev: lower.energy_ev,
            upper_energy_ev: upper.energy_ev,
            upper_weight: weight,
            clamped,
            products,
            sum,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn record(values: [&str; 6], mat: i32, mf: i32, mt: i32, sequence: i32) -> String {
        let data: String = values
            .into_iter()
            .map(|value| format!("{value:>11}"))
            .collect();
        format!("{data}{mat:>4}{mf:>2}{mt:>3}{sequence:>5}")
    }

    fn minimal_evaluation(le: &str, npl: &str, include_payload: bool) -> String {
        let mut lines = vec![
            record(["92235", "233", "0", "0", "0", "0"], 9237, 1, 451, 1),
            record(["0", "0", "0", "0", "0", "0"], 9237, 1, 451, 2),
            record(["", "", "", "", "", ""], 9237, 1, 0, 99_999),
            record(["92235", "233", le, "0", "0", "0"], 9237, 8, 454, 1),
            record(["0.0253", "0", "0", "0", npl, "1"], 9237, 8, 454, 2),
        ];
        if include_payload {
            lines.push(record(["53135", "0", "2", "0.1", "", ""], 9237, 8, 454, 3));
        }
        lines.push(record(["", "", "", "", "", ""], 9237, 8, 0, 99_999));
        lines.join("\n")
    }

    fn table(energy_ev: f64, products: &[(NuclideKey, f64)]) -> YieldTable {
        let products: BTreeMap<_, _> = products
            .iter()
            .map(|(key, value)| {
                (
                    *key,
                    YieldValue {
                        value: *value,
                        uncertainty: 0.0,
                    },
                )
            })
            .collect();
        YieldTable {
            energy_ev,
            sum: products.values().map(|value| value.value).sum(),
            products,
        }
    }

    #[test]
    fn effective_yields_use_union_and_zero_for_absent_endpoint() {
        let yields = FissionYields {
            parent: (92_235, 0),
            awr: 233.0,
            independent: vec![
                table(1.0, &[((53_135, 0), 1.0), ((54_135, 0), 1.0)]),
                table(3.0, &[((53_135, 0), 0.5), ((55_135, 0), 1.5)]),
            ],
            cumulative: Vec::new(),
        };
        let effective = yields.effective(2.0).unwrap();
        assert_eq!(effective.upper_weight, 0.5);
        assert_eq!(effective.products[&(53_135, 0)], 0.75);
        assert_eq!(effective.products[&(54_135, 0)], 0.5);
        assert_eq!(effective.products[&(55_135, 0)], 0.75);
        assert_eq!(effective.sum, 2.0);
        assert!(yields.effective(0.0).unwrap().clamped);
        assert!(yields.effective(4.0).unwrap().clamped);
    }

    #[test]
    fn parses_minimal_independent_yield_evaluation() {
        let parsed = parse_text(&minimal_evaluation("1", "4", true)).expect("minimal yields");
        assert_eq!(parsed.parent, (92_235, 0));
        assert_eq!(parsed.independent.len(), 1);
        assert_eq!(parsed.independent[0].sum, 2.0);
    }

    #[test]
    fn rejects_truncated_declared_payload_before_reserving_memory() {
        let error = parse_text(&minimal_evaluation("1", "2000000000", false)).unwrap_err();
        assert!(error.contains("truncated ENDF LIST payload"));
    }

    #[test]
    fn rejects_declared_incident_energies_before_reserving_memory() {
        let error = parse_text(&minimal_evaluation("2000000000", "4", true)).unwrap_err();
        assert!(error.contains("declares 2000000000 incident energies"));
    }
}
