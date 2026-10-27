//! ENDF-6 neutron-induced fission-product yields (MF=8/MT=454 and MT=459).
//!
//! Independent yields are the production source. Cumulative yields enter a depletion matrix only when the spec asks
//! for `fission_yields.yields: "cumulative"`, which also reports the double counting that introduces; otherwise
//! they are retained so callers can inspect and cross-check the evaluation.
use crate::endf::{fields, read_list_checked, read_tab1_checked, tail, ContRecord};
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

/// Which MF=8 yield set a selection reads: MT=454 independent or MT=459 cumulative.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum YieldKind {
    Independent,
    Cumulative,
}

impl YieldKind {
    pub fn name(self) -> &'static str {
        match self {
            Self::Independent => "independent",
            Self::Cumulative => "cumulative",
        }
    }
}

#[derive(Clone, Debug)]
pub struct EffectiveYields {
    pub requested_energy_ev: f64,
    pub lower_energy_ev: f64,
    pub upper_energy_ev: f64,
    pub upper_weight: f64,
    pub clamped: bool,
    pub products: BTreeMap<NuclideKey, f64>,
    /// Per-product standard uncertainties propagated through the same linear
    /// interpolation as `products`, treating the two endpoint evaluations as
    /// independent: sigma = hypot((1-w)*sigma_lo, w*sigma_hi). A product absent
    /// from an endpoint contributes a zero term, mirroring its zero value.
    pub uncertainties: BTreeMap<NuclideKey, f64>,
    pub sum: f64,
}

struct RawList {
    energy: f64,
    n_values: usize,
    n_products: usize,
    values: Vec<f64>,
}

// The crate's canonical ENDF field parsers: one correctly rounded decimal conversion (the
// private copy multiplied a parsed mantissa by 10^exp, rounding twice) and blank-is-zero
// integers, matching the Python reference used for bit-identity controls.
fn parse_float(field: &str) -> Result<f64, String> {
    crate::endf::parse_endf_float(field)
}

fn parse_i32(field: &str, name: &str) -> Result<i32, String> {
    crate::endf::parse_endf_i32(field).map_err(|error| format!("ENDF {name}: {error}"))
}

fn parse_usize(field: &str, name: &str) -> Result<usize, String> {
    crate::endf::parse_endf_usize(field).map_err(|error| format!("ENDF {name}: {error}"))
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
    parse_lines(&lines)
}

fn parse_lines(lines: &[&str]) -> Result<FissionYields, String> {
    let independent_lines = section(lines, 8, 454)?
        .ok_or_else(|| "fission-yield evaluation has no MF=8/MT=454 section".to_string())?;
    let (parent_za, awr, independent) = yield_section(&independent_lines, 454)?;

    let parent_liso = section(lines, 1, 451)?
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

    let cumulative = match section(lines, 8, 459)? {
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

/// Parse every MF=8/MT=454 evaluation of an ENDF tape that may hold several materials, as the ENDF/B
/// spontaneous-fission yield (`sfy`) sublibrary does. Materials without an MF=8/MT=454 section are
/// skipped; a tape with none is an error. A single-material file parses to one evaluation.
pub fn parse_tape_text(text: &str) -> Result<Vec<FissionYields>, String> {
    let mut groups: Vec<(i32, Vec<&str>)> = Vec::new();
    for line in text.lines() {
        let Some((mat, mf, _)) = tail(line) else {
            continue;
        };
        if mat <= 0 || mf <= 0 {
            continue;
        }
        if let Some((current, lines)) = groups.last_mut() {
            if *current == mat {
                lines.push(line);
                continue;
            }
        }
        groups.push((mat, vec![line]));
    }
    let mut out = Vec::new();
    for (mat, lines) in &groups {
        if !lines.iter().any(|line| tail(line) == Some((*mat, 8, 454))) {
            continue;
        }
        out.push(parse_lines(lines).map_err(|error| format!("MAT {mat}: {error}"))?);
    }
    if out.is_empty() {
        return Err("no MF=8/MT=454 fission-yield evaluation found".into());
    }
    Ok(out)
}

/// Parse one ENDF fission-yield evaluation.
pub fn parse_file(path: &str) -> Result<FissionYields, String> {
    let text = std::fs::read_to_string(path)
        .map_err(|error| format!("cannot read fission yields {path}: {error}"))?;
    parse_text(&text).map_err(|error| format!("{path}: {error}"))
}

/// A spontaneous-fission neutron multiplicity read from ENDF MF=1.
#[derive(Clone, Debug, PartialEq)]
pub struct SpontaneousNubar {
    pub nubar: f64,
    /// Section the value came from: 452 (total) when present, else 456 (prompt).
    pub mt: i32,
    /// How the value was taken from the section, e.g. "LNU=1 polynomial C0".
    pub basis: String,
}

/// The records of one material that the multiplicity reader needs.
#[derive(Default)]
struct NubarMaterial<'a> {
    mat: i32,
    mt451: Vec<&'a str>,
    mt452: Vec<&'a str>,
    mt456: Vec<&'a str>,
    mt457_head: Option<&'a str>,
}

fn insert_nubar(
    out: &mut BTreeMap<NuclideKey, SpontaneousNubar>,
    material: &NubarMaterial<'_>,
) -> Result<(), String> {
    let (mt, lines) = if !material.mt452.is_empty() {
        (452, &material.mt452)
    } else if !material.mt456.is_empty() {
        (456, &material.mt456)
    } else {
        return Ok(());
    };
    let mat = material.mat;
    let head = ContRecord::parse(lines[0]).map_err(|error| format!("MAT {mat}: {error}"))?;
    let za = head.c1.round() as i32;
    if za <= 0 || (head.c1 - f64::from(za)).abs() > 1e-8 {
        return Err(format!("MAT {mat}: invalid MF=1/MT={mt} ZA {}", head.c1));
    }
    // HEAD = (ZA, AWR, 0, LNU, 0, 0); LNU 1 is a polynomial in the incident energy, LNU 2 a table.
    // Spontaneous fission has no incident particle, so the value is taken at E = 0.
    let (nubar, basis) = match head.l2 {
        1 => {
            let (list, _) = read_list_checked(lines.as_slice(), 1)
                .map_err(|error| format!("MAT {mat}: MF=1/MT={mt}: {error}"))?;
            let c0 = *list
                .values
                .first()
                .ok_or_else(|| format!("MAT {mat}: MF=1/MT={mt} polynomial has no coefficients"))?;
            (c0, "LNU=1 polynomial C0".to_string())
        }
        2 => {
            let (table, _) = read_tab1_checked(lines.as_slice(), 1)
                .map_err(|error| format!("MAT {mat}: MF=1/MT={mt}: {error}"))?;
            let &(energy, value) = table
                .points
                .first()
                .ok_or_else(|| format!("MAT {mat}: MF=1/MT={mt} table has no points"))?;
            (value, format!("LNU=2 table at {energy:.6e} eV"))
        }
        other => {
            return Err(format!(
                "MAT {mat}: MF=1/MT={mt} has unsupported LNU {other}"
            ));
        }
    };
    if !nubar.is_finite() || nubar < 0.0 {
        return Err(format!("MAT {mat}: invalid spontaneous nubar {nubar}"));
    }
    // The decay sublibrary carries the state in the MF=8/MT=457 HEAD (L2); a sublibrary without it
    // carries it in the second MF=1/MT=451 record, as the fission-yield files do.
    let state_line = material
        .mt457_head
        .or_else(|| material.mt451.get(1).copied());
    let liso = match state_line {
        Some(line) => {
            ContRecord::parse(line)
                .map_err(|error| format!("MAT {mat}: {error}"))?
                .l2
        }
        None => 0,
    };
    if liso < 0 {
        return Err(format!("MAT {mat}: invalid LISO {liso}"));
    }
    let entry = SpontaneousNubar { nubar, mt, basis };
    if out.insert((za, liso), entry).is_some() {
        return Err(format!(
            "duplicate spontaneous nubar for ZA={za} LISO={liso}"
        ));
    }
    Ok(())
}

/// Read the spontaneous-fission neutron multiplicity of every material of an ENDF tape that carries
/// MF=1/MT=452 (total) or, failing that, MT=456 (prompt): the ENDF/B decay sublibrary holds the
/// spontaneous-fission value there (Cf-252 carries 3.7676 in MT=452). Materials with neither are
/// skipped. Do not point this at a neutron sublibrary: its MF=1 is the induced-fission multiplicity.
pub fn parse_spontaneous_nubar_text(
    text: &str,
) -> Result<BTreeMap<NuclideKey, SpontaneousNubar>, String> {
    let mut out = BTreeMap::new();
    let mut material = NubarMaterial::default();
    for line in text.lines() {
        let Some((mat, mf, mt)) = tail(line) else {
            continue;
        };
        if mat <= 0 {
            continue;
        }
        if mat != material.mat {
            insert_nubar(&mut out, &material)?;
            material = NubarMaterial {
                mat,
                ..NubarMaterial::default()
            };
        }
        match (mf, mt) {
            (1, 451) if material.mt451.len() < 2 => material.mt451.push(line),
            (1, 452) => material.mt452.push(line),
            (1, 456) => material.mt456.push(line),
            (8, 457) if material.mt457_head.is_none() => material.mt457_head = Some(line),
            _ => {}
        }
    }
    insert_nubar(&mut out, &material)?;
    Ok(out)
}

/// Piecewise-linear hat functions on an ascending incident-energy `grid`, evaluated at `energy_ev`: the
/// weights `FissionYields::effective` applies at one energy (h_1 = 1 at or below the first point, h_K = 1 at or
/// above the last, two adjacent weights `1 - w` and `w` between). They sum to 1.
pub fn hat_weights(grid: &[f64], energy_ev: f64) -> Vec<f64> {
    let mut weights = vec![0.0; grid.len()];
    let (Some(&first), Some(&last)) = (grid.first(), grid.last()) else {
        return weights;
    };
    if energy_ev <= first {
        weights[0] = 1.0;
    } else if energy_ev >= last {
        weights[grid.len() - 1] = 1.0;
    } else {
        let upper = grid.partition_point(|&point| point <= energy_ev);
        let weight = (energy_ev - grid[upper - 1]) / (grid[upper] - grid[upper - 1]);
        weights[upper - 1] = 1.0 - weight;
        weights[upper] = weight;
    }
    weights
}

impl FissionYields {
    /// The yield tables of one kind, ascending in incident energy.
    pub fn tables(&self, kind: YieldKind) -> &[YieldTable] {
        match kind {
            YieldKind::Independent => &self.independent,
            YieldKind::Cumulative => &self.cumulative,
        }
    }

    /// Tabulated incident energies (eV), ascending. MT=454 and MT=459 share one grid (checked at parse).
    pub fn energy_grid(&self) -> Vec<f64> {
        self.independent
            .iter()
            .map(|table| table.energy_ev)
            .collect()
    }

    /// Select, clamp or linearly interpolate independent yields at an incident energy.
    pub fn effective(&self, energy_ev: f64) -> Result<EffectiveYields, String> {
        self.effective_kind(YieldKind::Independent, energy_ev)
    }

    /// [`Self::effective`] for either yield set.
    pub fn effective_kind(
        &self,
        kind: YieldKind,
        energy_ev: f64,
    ) -> Result<EffectiveYields, String> {
        if !energy_ev.is_finite() || energy_ev < 0.0 {
            return Err(format!("invalid fission-yield incident energy {energy_ev}"));
        }
        let tables = self.tables(kind);
        let first = tables
            .first()
            .ok_or_else(|| format!("fission-yield evaluation has no {} tables", kind.name()))?;
        let last = tables.last().unwrap();
        let (lower, upper, weight, clamped) = if energy_ev <= first.energy_ev {
            (first, first, 0.0, energy_ev < first.energy_ev)
        } else if energy_ev >= last.energy_ev {
            (last, last, 0.0, energy_ev > last.energy_ev)
        } else {
            let upper_index = tables.partition_point(|table| table.energy_ev <= energy_ev);
            let lower = &tables[upper_index - 1];
            let upper = &tables[upper_index];
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
            .iter()
            .map(|&key| {
                let low = lower.products.get(&key).map_or(0.0, |value| value.value);
                let high = upper.products.get(&key).map_or(0.0, |value| value.value);
                (key, low * (1.0 - weight) + high * weight)
            })
            .collect();
        let uncertainties: BTreeMap<_, _> = keys
            .into_iter()
            .map(|key| {
                let low = lower
                    .products
                    .get(&key)
                    .map_or(0.0, |value| value.uncertainty);
                let high = upper
                    .products
                    .get(&key)
                    .map_or(0.0, |value| value.uncertainty);
                (key, ((1.0 - weight) * low).hypot(weight * high))
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
            uncertainties,
            sum,
        })
    }

    /// Mix the tabulated yield sets of one kind: `Y = sum_k w_k Y_k` over `(incident energy, weight)` pairs,
    /// each energy one of the tabulated energies (matched bitwise) and the weights nonnegative summing to 1.
    /// The product set is the union over the tables with nonzero weight (a product absent from one counts as
    /// zero yield and zero uncertainty); uncertainties combine as `sqrt(sum w_k^2 sigma_k^2)`. The reported
    /// `requested_energy_ev` is the weight-mean energy, `lower`/`upper` the lowest and highest tabulated energy
    /// with nonzero weight, `upper_weight` the weight of the upper one (0 when they coincide), `clamped` false.
    pub fn mixed(
        &self,
        kind: YieldKind,
        weights: &[(f64, f64)],
    ) -> Result<EffectiveYields, String> {
        let tables = self.tables(kind);
        if tables.is_empty() {
            return Err(format!(
                "fission-yield evaluation has no {} tables",
                kind.name()
            ));
        }
        let mut total = 0.0;
        let mut selected: Vec<(&YieldTable, f64)> = Vec::new();
        for &(energy, weight) in weights {
            if !weight.is_finite() || weight < 0.0 {
                return Err(format!("invalid fission-yield mixing weight {weight}"));
            }
            let table = tables
                .iter()
                .find(|table| table.energy_ev.to_bits() == energy.to_bits())
                .ok_or_else(|| {
                    format!("mixing energy {energy} eV is not a tabulated incident energy")
                })?;
            total += weight;
            if weight > 0.0 {
                selected.push((table, weight));
            }
        }
        if selected.is_empty() || (total - 1.0).abs() > 1e-9 {
            return Err(format!(
                "fission-yield mixing weights must sum to 1, got {total}"
            ));
        }
        let mut keys: Vec<_> = selected
            .iter()
            .flat_map(|(table, _)| table.products.keys().copied())
            .collect();
        keys.sort_unstable();
        keys.dedup();
        let mut products = BTreeMap::new();
        let mut uncertainties = BTreeMap::new();
        for key in keys {
            let mut value = 0.0;
            let mut sigma = 0.0f64;
            for (table, weight) in &selected {
                let entry = table.products.get(&key);
                value += weight * entry.map_or(0.0, |entry| entry.value);
                sigma = sigma.hypot(weight * entry.map_or(0.0, |entry| entry.uncertainty));
            }
            products.insert(key, value);
            uncertainties.insert(key, sigma);
        }
        let sum: f64 = products.values().sum();
        let (lower, _) = selected[0];
        let (upper, upper_weight) = *selected.last().unwrap();
        let upper_weight = if std::ptr::eq(lower, upper) {
            0.0
        } else {
            upper_weight
        };
        Ok(EffectiveYields {
            requested_energy_ev: selected
                .iter()
                .map(|(table, weight)| weight * table.energy_ev)
                .sum(),
            lower_energy_ev: lower.energy_ev,
            upper_energy_ev: upper.energy_ev,
            upper_weight,
            clamped: false,
            products,
            uncertainties,
            sum,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fields_use_the_canonical_endf_parsers() {
        // One correctly rounded conversion, identical to Rust's (and Python's) literal parse.
        for (text, expected) in [
            ("1.400000+7", 1.4e7_f64),
            ("2.530000-2", 2.53e-2),
            ("-1.234567+5", -1.234567e5),
            ("6.022141+23", 6.022141e23),
        ] {
            assert_eq!(
                parse_float(text).unwrap().to_bits(),
                expected.to_bits(),
                "{text}"
            );
        }
        // Blank integer fields are zero (ENDF convention), not an error.
        assert_eq!(parse_i32("           ", "LISO").unwrap(), 0);
        assert!(parse_usize("         -1", "NFP").is_err());
    }

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

    fn table_with_sigma(energy_ev: f64, products: &[(NuclideKey, f64, f64)]) -> YieldTable {
        let products: BTreeMap<_, _> = products
            .iter()
            .map(|&(key, value, uncertainty)| (key, YieldValue { value, uncertainty }))
            .collect();
        YieldTable {
            energy_ev,
            sum: products.values().map(|value| value.value).sum(),
            products,
        }
    }

    fn three_table_yields() -> FissionYields {
        FissionYields {
            parent: (92_235, 0),
            awr: 233.0,
            independent: vec![
                table_with_sigma(0.0253, &[((53_135, 0), 1.0, 0.1), ((54_135, 0), 1.0, 0.2)]),
                table_with_sigma(5.0e5, &[((53_135, 0), 0.5, 0.3), ((55_135, 0), 1.5, 0.4)]),
                table_with_sigma(1.4e7, &[((53_135, 0), 0.25, 0.5)]),
            ],
            cumulative: vec![
                table_with_sigma(0.0253, &[((53_135, 0), 2.0, 0.0)]),
                table_with_sigma(5.0e5, &[((53_135, 0), 3.0, 0.0)]),
                table_with_sigma(1.4e7, &[((53_135, 0), 4.0, 0.0)]),
            ],
        }
    }

    #[test]
    fn hat_weights_sum_to_one_and_clamp() {
        let grid = [0.0253, 5.0e5, 1.4e7];
        assert_eq!(hat_weights(&grid, 1.0e-3), vec![1.0, 0.0, 0.0]);
        assert_eq!(hat_weights(&grid, 2.0e7), vec![0.0, 0.0, 1.0]);
        assert_eq!(hat_weights(&grid, 5.0e5), vec![0.0, 1.0, 0.0]);
        let inner = hat_weights(&grid, 1.0e6);
        assert_eq!(inner[0], 0.0);
        assert!((inner.iter().sum::<f64>() - 1.0).abs() < 1e-15);
        assert!(inner[1] > 0.0 && inner[2] > 0.0);
        assert_eq!(hat_weights(&[3.0], 9.0), vec![1.0]);
    }

    #[test]
    fn mixed_with_one_bracketing_pair_reproduces_effective() {
        let yields = three_table_yields();
        let energy = 1.0e6;
        let hats = hat_weights(&yields.energy_grid(), energy);
        let weights: Vec<(f64, f64)> = yields.energy_grid().into_iter().zip(hats).collect();
        let mixed = yields.mixed(YieldKind::Independent, &weights).unwrap();
        let single = yields.effective(energy).unwrap();
        assert_eq!(mixed.products, single.products);
        assert_eq!(mixed.sum, single.sum);
        for (key, sigma) in &single.uncertainties {
            assert_eq!(mixed.uncertainties[key], *sigma);
        }
        // below the first energy: one table, exactly
        let clamped = yields
            .mixed(YieldKind::Independent, &[(0.0253, 1.0), (5.0e5, 0.0)])
            .unwrap();
        assert_eq!(clamped.products, yields.effective(1.0e-3).unwrap().products);
        assert_eq!(clamped.upper_weight, 0.0);
    }

    #[test]
    fn mixed_uses_union_of_products_and_combines_uncertainties_in_quadrature() {
        let yields = three_table_yields();
        let mixed = yields
            .mixed(
                YieldKind::Independent,
                &[(0.0253, 0.25), (5.0e5, 0.5), (1.4e7, 0.25)],
            )
            .unwrap();
        assert_eq!(mixed.products.len(), 3);
        assert_eq!(mixed.products[&(53_135, 0)], 0.25 + 0.25 + 0.0625);
        assert_eq!(mixed.products[&(54_135, 0)], 0.25);
        assert_eq!(mixed.products[&(55_135, 0)], 0.75);
        let expected: f64 =
            ((0.25f64 * 0.1).powi(2) + (0.5f64 * 0.3).powi(2) + (0.25f64 * 0.5).powi(2)).sqrt();
        assert!((mixed.uncertainties[&(53_135, 0)] - expected).abs() < 1e-15);
        // a product absent from every other table carries only its own term
        assert!((mixed.uncertainties[&(54_135, 0)] - 0.25 * 0.2).abs() < 1e-15);
        let mean = 0.25 * 0.0253 + 0.5 * 5.0e5 + 0.25 * 1.4e7;
        assert!((mixed.requested_energy_ev - mean).abs() < 1e-6);
    }

    #[test]
    fn mixed_reads_cumulative_tables_and_rejects_bad_weights() {
        let yields = three_table_yields();
        let cumulative = yields
            .mixed(YieldKind::Cumulative, &[(0.0253, 0.5), (5.0e5, 0.5)])
            .unwrap();
        assert_eq!(cumulative.products[&(53_135, 0)], 2.5);
        assert!(yields
            .mixed(YieldKind::Independent, &[(0.0253, 0.5)])
            .is_err());
        assert!(yields.mixed(YieldKind::Independent, &[(1.0, 1.0)]).is_err());
        assert!(yields
            .mixed(YieldKind::Independent, &[(0.0253, -0.5), (5.0e5, 1.5)])
            .is_err());
        let none = FissionYields {
            cumulative: Vec::new(),
            ..three_table_yields()
        };
        assert!(none.effective_kind(YieldKind::Cumulative, 1.0).is_err());
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

    /// One spontaneous-fission yield material: two products of yield 1 each at E = 0.
    fn sfy_material(mat: i32, za: &str, product_a: &str, product_b: &str) -> Vec<String> {
        vec![
            record([za, "250", "0", "0", "0", "0"], mat, 1, 451, 1),
            record(["0", "0", "0", "0", "0", "0"], mat, 1, 451, 2),
            record(["", "", "", "", "", ""], mat, 1, 0, 99_999),
            record([za, "250", "1", "0", "0", "0"], mat, 8, 454, 1),
            record(["0", "0", "0", "0", "8", "2"], mat, 8, 454, 2),
            record(
                [product_a, "0", "1.0", "0.1", product_b, "0"],
                mat,
                8,
                454,
                3,
            ),
            record(["1.0", "0.1", "", "", "", ""], mat, 8, 454, 4),
            record(["", "", "", "", "", ""], mat, 8, 0, 99_999),
            record(["", "", "", "", "", ""], mat, 0, 0, 0),
        ]
    }

    #[test]
    fn tape_parser_reads_every_material_of_a_spontaneous_yield_sublibrary() {
        let mut lines = sfy_material(3644, "98252", "54140", "38094");
        lines.extend(sfy_material(9643, "96244", "55137", "39090"));
        let parsed = parse_tape_text(&lines.join("\n")).expect("two-material tape");
        assert_eq!(parsed.len(), 2);
        assert_eq!(parsed[0].parent, (98_252, 0));
        assert_eq!(parsed[1].parent, (96_244, 0));
        let at_zero = parsed[0].effective(0.0).unwrap();
        assert_eq!(at_zero.products[&(54_140, 0)], 1.0);
        assert_eq!(at_zero.products[&(38_094, 0)], 1.0);
        assert_eq!(at_zero.sum, 2.0);
        assert!(!at_zero.clamped);
        // A single-material file is the one-evaluation case of the same reader.
        let single = parse_tape_text(&sfy_material(3644, "98252", "54140", "38094").join("\n"))
            .expect("single material");
        assert_eq!(single.len(), 1);
        assert!(parse_tape_text("").unwrap_err().contains("MF=8/MT=454"));
    }

    fn nubar_material(
        mat: i32,
        za: &str,
        liso: &str,
        section: Option<(i32, &str, Vec<[&str; 6]>)>,
    ) -> Vec<String> {
        let mut lines = vec![
            record([za, "249.916", "-1", "1", "0", "1"], mat, 1, 451, 1),
            record(["0", "1", "0", "0", "0", "6"], mat, 1, 451, 2),
            record(["", "", "", "", "", ""], mat, 1, 0, 99_999),
        ];
        if let Some((mt, lnu, payload)) = section {
            lines.push(record([za, "249.916", "0", lnu, "0", "0"], mat, 1, mt, 1));
            for (index, values) in payload.into_iter().enumerate() {
                lines.push(record(values, mat, 1, mt, index as i32 + 2));
            }
            lines.push(record(["", "", "", "", "", ""], mat, 1, 0, 99_999));
        }
        lines.push(record([za, "249.916", "0", liso, "0", "4"], mat, 8, 457, 1));
        lines.push(record(["", "", "", "", "", ""], mat, 8, 0, 99_999));
        lines.push(record(["", "", "", "", "", ""], mat, 0, 0, 0));
        lines
    }

    #[test]
    fn spontaneous_nubar_reads_polynomial_and_table_and_prefers_total_over_prompt() {
        // Layout of the ENDF/B-VIII.0 decay sublibrary's Cf-252 MF=1/MT=452 (a one-term polynomial).
        let mut lines = nubar_material(
            3644,
            "98252",
            "0",
            Some((
                452,
                "1",
                vec![
                    ["0", "0", "0", "0", "1", "0"],
                    ["3.7676", "", "", "", "", ""],
                ],
            )),
        );
        // A material with only the prompt multiplicity.
        lines.extend(nubar_material(
            9237,
            "92238",
            "0",
            Some((
                456,
                "1",
                vec![["0", "0", "0", "0", "1", "0"], ["2.0", "", "", "", "", ""]],
            )),
        ));
        // A tabulated multiplicity on an isomer: the state comes from the MF=8/MT=457 HEAD.
        lines.extend(nubar_material(
            9547,
            "95242",
            "1",
            Some((
                452,
                "2",
                vec![
                    ["0", "0", "0", "0", "1", "2"],
                    ["2", "2", "", "", "", ""],
                    [
                        "1.000000-5",
                        "2.500000+0",
                        "2.000000+7",
                        "2.900000+0",
                        "",
                        "",
                    ],
                ],
            )),
        ));
        // No multiplicity at all: skipped, not an error.
        lines.extend(nubar_material(2631, "26056", "0", None));
        let parsed = parse_spontaneous_nubar_text(&lines.join("\n")).expect("nubar tape");
        assert_eq!(parsed.len(), 3);
        assert_eq!(
            parsed[&(98_252, 0)],
            SpontaneousNubar {
                nubar: 3.7676,
                mt: 452,
                basis: "LNU=1 polynomial C0".into(),
            }
        );
        assert_eq!(parsed[&(92_238, 0)].mt, 456);
        assert_eq!(parsed[&(92_238, 0)].nubar, 2.0);
        assert_eq!(parsed[&(95_242, 1)].nubar, 2.5);
        assert_eq!(parsed[&(95_242, 1)].basis, "LNU=2 table at 1.000000e-5 eV");
    }

    #[test]
    fn spontaneous_nubar_rejects_an_unsupported_representation() {
        let lines = nubar_material(
            3644,
            "98252",
            "0",
            Some((
                452,
                "3",
                vec![
                    ["0", "0", "0", "0", "1", "0"],
                    ["3.7676", "", "", "", "", ""],
                ],
            )),
        );
        let error = parse_spontaneous_nubar_text(&lines.join("\n")).unwrap_err();
        assert!(error.contains("unsupported LNU 3"), "{error}");
    }
}
