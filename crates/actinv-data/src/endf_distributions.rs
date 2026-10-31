//! ENDF-6 angular and energy-angle distributions for the kinematic damage method (P154).
//!
//! The activation reader (`activation.rs`) consumes MF=6 laws structurally and has no MF=4 parser; the damage
//! builder needs the distributions themselves. This module reads, from the same checked ENDF primitives:
//!
//! * MF=3 reaction Q values (`QM`, `QI`) from the TAB1 head;
//! * MF=4 angular distributions (`LTT` 0 isotropic, 1 Legendre, 2 tabulated, 3 Legendre then tabulated; `LCT`
//!   reference frame);
//! * MF=6 `LAW=1` continuum energy-angle distributions (`LANG=1` Legendre, `LANG=2` Kalbach-Mann) and `LAW=2`
//!   discrete two-body angular distributions (`LANG=0` Legendre, `LANG=11..15` tabulated).
//!
//! A section whose form is unsupported is kept as an `Err` with its reason, so the damage builder can report the
//! channel as uncovered instead of failing the whole evaluation. Nothing here changes what
//! [`crate::activation::parse_evaluations`] returns.

use crate::endf::{
    bounded_capacity, parse_sections, read_cont_checked, read_list_checked, read_tab1_checked,
    read_tab2_checked, Section,
};
use crate::groups::Tabulated;
use std::collections::BTreeMap;

/// Reaction Q values from the MF=3 TAB1 head (eV).
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct ReactionQ {
    /// Mass-difference Q value (`QM`).
    pub qm_ev: f64,
    /// Reaction Q value for the state reached (`QI`).
    pub qi_ev: f64,
    pub lr: i32,
}

pub fn parse_mf3_q(section: &Section<'_>) -> Result<ReactionQ, String> {
    let (record, _) = read_tab1_checked(&section.lines, 1)?;
    Ok(ReactionQ {
        qm_ev: record.head.c1,
        qi_ev: record.head.c2,
        lr: record.head.l2,
    })
}

/// Interpolate `y` between `(x0, y0)` and `(x1, y1)` at `x` with ENDF interpolation law `law` (1 histogram,
/// 2 lin-lin, 3 lin-log, 4 log-lin, 5 log-log). Laws that need positive arguments fall back to lin-lin when
/// the arguments are not positive.
pub fn interpolate(law: i32, x0: f64, x1: f64, x: f64, y0: f64, y1: f64) -> f64 {
    if x1 <= x0 {
        return y1;
    }
    let linear = (x - x0) / (x1 - x0);
    match law {
        1 => y0,
        3 if x0 > 0.0 && x > 0.0 => y0 + (x / x0).ln() / (x1 / x0).ln() * (y1 - y0),
        4 if y0 > 0.0 && y1 > 0.0 => y0 * (y1 / y0).powf(linear),
        5 if x0 > 0.0 && x > 0.0 && y0 > 0.0 && y1 > 0.0 => {
            y0 * (y1 / y0).powf((x / x0).ln() / (x1 / x0).ln())
        }
        _ => y0 + linear * (y1 - y0),
    }
}

/// One angular distribution at one incident energy, normalized to unit integral on `[-1, 1]`.
#[derive(Clone, Debug, PartialEq)]
pub enum AngularRecord {
    Isotropic,
    /// `a_1 .. a_NL`; `a_0 = 1` is implied: `f(mu) = sum_l (2l+1)/2 a_l P_l(mu)`.
    Legendre(Vec<f64>),
    /// `f(mu)` tabulated over `mu` with the TAB1 interpolation laws.
    Tabulated(Tabulated),
}

static ISOTROPIC: AngularRecord = AngularRecord::Isotropic;

impl AngularRecord {
    /// Probability density at cosine `mu`.
    pub fn pdf(&self, mu: f64) -> f64 {
        match self {
            Self::Isotropic => 0.5,
            Self::Legendre(coefficients) => {
                let mut sum = 0.5;
                let (mut previous, mut current) = (1.0, mu);
                for (index, coefficient) in coefficients.iter().enumerate() {
                    let l = (index + 1) as f64;
                    sum += 0.5 * (2.0 * l + 1.0) * coefficient * current;
                    let next = ((2.0 * l + 1.0) * mu * current - l * previous) / (l + 1.0);
                    previous = current;
                    current = next;
                }
                sum
            }
            // A validated table evaluates for any finite abscissa; zero outside its range.
            Self::Tabulated(table) => table.evaluate(mu).unwrap_or(0.0),
        }
    }

    /// Highest Legendre order carried (0 for isotropic and tabulated records).
    pub fn order(&self) -> usize {
        match self {
            Self::Legendre(coefficients) => coefficients.len(),
            _ => 0,
        }
    }

    fn breakpoints(&self, out: &mut Vec<f64>) {
        if let Self::Tabulated(table) = self {
            out.extend(table.x.iter().copied());
        }
    }
}

/// Angular distribution over incident energy: one record per energy plus the interpolation law of each interval.
/// An empty distribution is isotropic everywhere.
#[derive(Clone, Debug, PartialEq)]
pub struct AngularDistribution {
    pub energies: Vec<f64>,
    pub records: Vec<AngularRecord>,
    /// Interpolation law (1..=5) of the interval above `energies[i]`.
    pub laws: Vec<i32>,
}

/// The angular distribution at one incident energy: one or two bracketing records and their blend.
#[derive(Clone, Copy, Debug)]
pub struct AngularAt<'a> {
    lo: &'a AngularRecord,
    hi: Option<&'a AngularRecord>,
    law: i32,
    e0: f64,
    e1: f64,
    e: f64,
}

impl<'a> AngularAt<'a> {
    pub fn pdf(&self, mu: f64) -> f64 {
        let low = self.lo.pdf(mu);
        match self.hi {
            Some(high) => interpolate(self.law, self.e0, self.e1, self.e, low, high.pdf(mu)),
            None => low,
        }
    }

    pub fn order(&self) -> usize {
        self.lo.order().max(self.hi.map_or(0, AngularRecord::order))
    }

    /// Cosines at which the density is not smooth (the tabulated points of both bracketing records).
    pub fn breakpoints(&self, out: &mut Vec<f64>) {
        self.lo.breakpoints(out);
        if let Some(high) = self.hi {
            high.breakpoints(out);
        }
    }
}

impl AngularDistribution {
    pub fn isotropic() -> Self {
        Self {
            energies: Vec::new(),
            records: Vec::new(),
            laws: Vec::new(),
        }
    }

    pub fn is_isotropic(&self) -> bool {
        self.records.iter().all(|record| match record {
            AngularRecord::Isotropic => true,
            AngularRecord::Legendre(coefficients) => coefficients.iter().all(|a| *a == 0.0),
            AngularRecord::Tabulated(_) => false,
        })
    }

    /// The distribution at incident energy `e`: isotropic below the first tabulated energy (as NJOY HEATR),
    /// the last record above the highest, otherwise the bracketing records blended with the interval's law.
    pub fn at(&self, e: f64) -> AngularAt<'_> {
        let isotropic = AngularAt {
            lo: &ISOTROPIC,
            hi: None,
            law: 2,
            e0: e,
            e1: e,
            e,
        };
        if self.energies.is_empty() || e < self.energies[0] {
            return isotropic;
        }
        let last = self.energies.len() - 1;
        if e >= self.energies[last] {
            return AngularAt {
                lo: &self.records[last],
                hi: None,
                law: 2,
                e0: self.energies[last],
                e1: self.energies[last],
                e,
            };
        }
        let index = self.energies.partition_point(|energy| *energy <= e) - 1;
        let law = self.laws.get(index).copied().unwrap_or(2);
        AngularAt {
            lo: &self.records[index],
            hi: (law != 1).then(|| &self.records[index + 1]),
            law,
            e0: self.energies[index],
            e1: self.energies[index + 1],
            e,
        }
    }
}

/// Expand TAB2 `(NBT, INT)` pairs into the interpolation law of each of the `count - 1` intervals.
///
/// Every one of the `count` records that follow needs at least one line, so a count above the
/// `lines_remaining` is refused before any interval is expanded.
fn expand_laws(
    count: usize,
    interpolation: &[(usize, i32)],
    lines_remaining: usize,
) -> Result<Vec<i32>, String> {
    if count > lines_remaining {
        return Err(format!(
            "TAB2 declares {count} records, at most {lines_remaining} lines remain"
        ));
    }
    (0..count.saturating_sub(1))
        .map(|segment| {
            let law = interpolation
                .iter()
                .find_map(|&(nbt, law)| (segment + 2 <= nbt).then_some(law))
                .ok_or_else(|| format!("TAB2 has no interpolation law for interval {segment}"))?;
            if (1..=5).contains(&law) {
                Ok(law)
            } else {
                Err(format!(
                    "unsupported incident-energy interpolation INT={law}"
                ))
            }
        })
        .collect()
}

fn check_ascending(energies: &[f64], what: &str) -> Result<(), String> {
    if energies.iter().any(|energy| !energy.is_finite())
        || energies.windows(2).any(|pair| pair[1] < pair[0])
    {
        Err(format!("{what} incident energies are not ascending"))
    } else {
        Ok(())
    }
}

/// An MF=4 section: the reference frame and the distribution.
#[derive(Clone, Debug, PartialEq)]
pub struct Mf4Data {
    /// 1 laboratory, 2 centre of mass.
    pub lct: i32,
    pub distribution: AngularDistribution,
}

pub fn parse_mf4(section: &Section<'_>) -> Result<Mf4Data, String> {
    let lines = &section.lines;
    let (head, next) = read_cont_checked(lines, 0)?;
    if head.l1 != 0 {
        return Err(format!(
            "MF=4 LVT={} (transformation matrix) is unsupported",
            head.l1
        ));
    }
    let ltt = head.l2;
    let (cont, mut next) = read_cont_checked(lines, next)?;
    let (li, lct) = (cont.l1, cont.l2);
    if !matches!(lct, 1 | 2) {
        return Err(format!("unsupported MF=4 LCT={lct}"));
    }
    if li == 1 || ltt == 0 {
        if next != lines.len() {
            return Err(format!("{} unconsumed MF=4 records", lines.len() - next));
        }
        return Ok(Mf4Data {
            lct,
            distribution: AngularDistribution::isotropic(),
        });
    }
    if !matches!(ltt, 1..=3) {
        return Err(format!("unsupported MF=4 LTT={ltt}"));
    }
    let mut energies = Vec::new();
    let mut records = Vec::new();
    let mut laws: Vec<i32> = Vec::new();
    if matches!(ltt, 1 | 3) {
        let (tab2, after) = read_tab2_checked(lines, next)?;
        next = after;
        let count = tab2.head.n2;
        let block = expand_laws(count, &tab2.interpolation, lines.len().saturating_sub(next))?;
        for _ in 0..count {
            let (list, after) = read_list_checked(lines, next)?;
            next = after;
            energies.push(list.head.c2);
            records.push(AngularRecord::Legendre(list.values));
        }
        laws.extend(block);
    }
    if matches!(ltt, 2 | 3) {
        let (tab2, after) = read_tab2_checked(lines, next)?;
        next = after;
        let count = tab2.head.n2;
        let block = expand_laws(count, &tab2.interpolation, lines.len().saturating_sub(next))?;
        if !energies.is_empty() && count > 0 {
            laws.push(2);
        }
        for _ in 0..count {
            let (tab, after) = read_tab1_checked(lines, next)?;
            next = after;
            energies.push(tab.head.c2);
            records.push(AngularRecord::Tabulated(Tabulated::try_from(tab)?));
        }
        laws.extend(block);
    }
    if next != lines.len() {
        return Err(format!("{} unconsumed MF=4 records", lines.len() - next));
    }
    check_ascending(&energies, "MF=4")?;
    if !energies.is_empty() && laws.len() + 1 != energies.len() {
        return Err("MF=4 interpolation ranges do not match the incident-energy count".into());
    }
    Ok(Mf4Data {
        lct,
        distribution: AngularDistribution {
            energies,
            records,
            laws,
        },
    })
}

/// One incident energy of an MF=6 `LAW=1` distribution. The first `nd` outgoing energies are discrete lines
/// whose `f0` is a probability; the rest are a tabulated density. `angular` holds `na` values per outgoing
/// energy: `LANG=1` Legendre moments `f_1 .. f_NA`, or `LANG=2` the Kalbach-Mann `r` (and `a` when `na == 2`).
#[derive(Clone, Debug, PartialEq)]
pub struct Law1Energy {
    pub energy: f64,
    pub nd: usize,
    pub na: usize,
    pub eprime: Vec<f64>,
    pub f0: Vec<f64>,
    pub angular: Vec<f64>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Law1 {
    pub lang: i32,
    /// 1 histogram, 2 linear outgoing-energy interpolation.
    pub lep: i32,
    pub energies: Vec<Law1Energy>,
    /// Interpolation law of the interval above `energies[i]`.
    pub laws: Vec<i32>,
}

#[derive(Clone, Debug, PartialEq)]
pub enum Mf6Body {
    Law1(Law1),
    Law2(AngularDistribution),
    /// A law the damage method does not read (the number is the LAW).
    Other(i32),
}

#[derive(Clone, Debug, PartialEq)]
pub struct Mf6Particle {
    pub zap: i32,
    pub awp: f64,
    pub law: i32,
    pub yield_table: Tabulated,
    pub body: Mf6Body,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Mf6Data {
    /// 1 laboratory, 2 centre of mass, 3 laboratory for A <= 4 and centre of mass for heavier products.
    pub lct: i32,
    pub products: Vec<Mf6Particle>,
}

fn parse_law1(lines: &[&str], index: usize) -> Result<(Mf6Body, usize), String> {
    let (tab2, mut next) = read_tab2_checked(lines, index)?;
    let lang = tab2.head.l1;
    let lep = tab2.head.l2;
    let count = tab2.head.n2;
    let laws = expand_laws(count, &tab2.interpolation, lines.len().saturating_sub(next))?;
    if !matches!(lep, 1 | 2) {
        return Err(format!("unsupported MF=6 LEP={lep}"));
    }
    let mut energies = Vec::with_capacity(bounded_capacity(count));
    for _ in 0..count {
        let (list, after) = read_list_checked(lines, next)?;
        next = after;
        let (nd, na, nw, nep) = (list.head.l1, list.head.l2, list.head.n1, list.head.n2);
        let nd = usize::try_from(nd).map_err(|_| format!("negative MF=6 ND={nd}"))?;
        let na = usize::try_from(na).map_err(|_| format!("negative MF=6 NA={na}"))?;
        let stride = na + 2;
        if Some(nw) != nep.checked_mul(stride) || nd > nep {
            return Err(format!(
                "MF=6 LAW=1 list has NW={nw}, NEP={nep}, NA={na}, ND={nd}"
            ));
        }
        let mut eprime = Vec::with_capacity(bounded_capacity(nep));
        let mut f0 = Vec::with_capacity(bounded_capacity(nep));
        let mut angular = Vec::with_capacity(bounded_capacity(nep.saturating_mul(na)));
        for tuple in list.values.chunks_exact(stride) {
            eprime.push(tuple[0]);
            f0.push(tuple[1]);
            angular.extend_from_slice(&tuple[2..]);
        }
        energies.push(Law1Energy {
            energy: list.head.c2,
            nd,
            na,
            eprime,
            f0,
            angular,
        });
    }
    let incident: Vec<f64> = energies.iter().map(|e| e.energy).collect();
    check_ascending(&incident, "MF=6 LAW=1")?;
    Ok((
        Mf6Body::Law1(Law1 {
            lang,
            lep,
            energies,
            laws,
        }),
        next,
    ))
}

fn parse_law2(lines: &[&str], index: usize) -> Result<(Mf6Body, usize), String> {
    let (tab2, mut next) = read_tab2_checked(lines, index)?;
    let count = tab2.head.n2;
    let laws = expand_laws(count, &tab2.interpolation, lines.len().saturating_sub(next))?;
    let mut energies = Vec::with_capacity(bounded_capacity(count));
    let mut records = Vec::with_capacity(bounded_capacity(count));
    for _ in 0..count {
        let (list, after) = read_list_checked(lines, next)?;
        next = after;
        energies.push(list.head.c2);
        let lang = list.head.l1;
        records.push(match lang {
            0 => AngularRecord::Legendre(list.values),
            11..=15 => {
                if list.values.len() % 2 != 0 || list.values.is_empty() {
                    return Err("MF=6 LAW=2 tabulated list has an odd length".into());
                }
                let (mu, f): (Vec<f64>, Vec<f64>) = list
                    .values
                    .as_chunks::<2>()
                    .0
                    .iter()
                    .map(|&[mu, f]| (mu, f))
                    .unzip();
                let table = Tabulated {
                    interpolation: vec![(mu.len(), lang - 10)],
                    x: mu,
                    y: f,
                };
                table.validate()?;
                AngularRecord::Tabulated(table)
            }
            _ => return Err(format!("unsupported MF=6 LAW=2 LANG={lang}")),
        });
    }
    check_ascending(&energies, "MF=6 LAW=2")?;
    Ok((
        Mf6Body::Law2(AngularDistribution {
            energies,
            records,
            laws,
        }),
        next,
    ))
}

pub fn parse_mf6_data(section: &Section<'_>) -> Result<Mf6Data, String> {
    let lines = &section.lines;
    let (head, mut next) = read_cont_checked(lines, 0)?;
    let lct = head.l2;
    let mut products = Vec::with_capacity(bounded_capacity(head.n1));
    for _ in 0..head.n1 {
        let (record, after_yield) = read_tab1_checked(lines, next)?;
        let zap = record.head.c1.round() as i32;
        let awp = record.head.c2;
        let law = record.head.l2;
        let yield_table = Tabulated::try_from(record)?;
        let (body, after) = match law {
            1 => parse_law1(lines, after_yield)?,
            2 => parse_law2(lines, after_yield)?,
            _ => (
                Mf6Body::Other(law),
                crate::activation::consume_mf6_law(lines, after_yield, law)?,
            ),
        };
        next = after;
        products.push(Mf6Particle {
            zap,
            awp,
            law,
            yield_table,
            body,
        });
    }
    if next != lines.len() {
        return Err(format!("{} unconsumed MF=6 records", lines.len() - next));
    }
    Ok(Mf6Data { lct, products })
}

/// The damage-relevant distributions of one material.
#[derive(Clone, Debug, Default)]
pub struct DamageEndf {
    pub q: BTreeMap<i32, ReactionQ>,
    pub mf4: BTreeMap<i32, Result<Mf4Data, String>>,
    pub mf6: BTreeMap<i32, Result<Mf6Data, String>>,
}

/// Read the damage-relevant sections of every material on a tape, keyed by MAT.
pub fn parse_damage_endf(text: &str) -> Result<BTreeMap<i32, DamageEndf>, String> {
    let mut materials: BTreeMap<i32, DamageEndf> = BTreeMap::new();
    for section in parse_sections(text)? {
        let entry = materials.entry(section.mat).or_default();
        match section.mf {
            3 => {
                if let Ok(q) = parse_mf3_q(&section) {
                    entry.q.insert(section.mt, q);
                }
            }
            4 => {
                entry.mf4.insert(section.mt, parse_mf4(&section));
            }
            6 => {
                entry.mf6.insert(section.mt, parse_mf6_data(&section));
            }
            _ => {}
        }
    }
    Ok(materials)
}

#[cfg(test)]
pub(crate) mod test_support {
    /// One 80-column ENDF record.
    pub fn record(values: [&str; 6], mat: i32, mf: i32, mt: i32, sequence: i32) -> String {
        let data: String = values
            .into_iter()
            .map(|value| format!("{value:>11}"))
            .collect();
        format!("{data}{mat:>4}{mf:>2}{mt:>3}{sequence:>5}")
    }

    /// Format a float in ENDF's 11-column form with a full-precision exponent.
    pub fn num(value: f64) -> String {
        format!("{value:.6e}")
    }

    /// Pack values six to a record with sequence numbers starting at `start`.
    pub fn packed(values: &[String], mat: i32, mf: i32, mt: i32, start: &mut i32) -> Vec<String> {
        let mut out = Vec::new();
        for chunk in values.chunks(6) {
            let mut fields = [""; 6];
            for (slot, value) in fields.iter_mut().zip(chunk) {
                *slot = value.as_str();
            }
            out.push(record(fields, mat, mf, mt, *start));
            *start += 1;
        }
        out
    }
}

#[cfg(test)]
mod tests {
    use super::test_support::*;
    use super::*;

    #[test]
    fn expand_laws_refuses_a_count_beyond_the_remaining_lines() {
        let huge = usize::MAX / 2;
        let error = expand_laws(huge, &[(huge, 2)], 10).unwrap_err();
        assert!(error.contains("lines remain"), "{error}");
        assert_eq!(expand_laws(3, &[(3, 2)], 10).unwrap(), vec![2, 2]);
    }

    fn section<'a>(mf: i32, mt: i32, lines: &'a [String]) -> Section<'a> {
        Section {
            mat: 2631,
            mf,
            mt,
            lines: lines.iter().map(String::as_str).collect(),
        }
    }

    /// MF=4 LTT=1 (Legendre) with LCT=2 and two incident energies.
    fn mf4_legendre() -> Vec<String> {
        let mut lines = vec![
            record(["26056", "55.45", "0", "1", "0", "0"], 2631, 4, 2, 1),
            record(["0", "55.45", "0", "2", "0", "0"], 2631, 4, 2, 2),
            record(["0", "0", "0", "0", "1", "2"], 2631, 4, 2, 3),
            record(["2", "2", "", "", "", ""], 2631, 4, 2, 4),
        ];
        lines.push(record(["0", "1.0e6", "0", "0", "2", "0"], 2631, 4, 2, 5));
        lines.push(record(["0.3", "0.1", "", "", "", ""], 2631, 4, 2, 6));
        lines.push(record(["0", "2.0e6", "0", "0", "1", "0"], 2631, 4, 2, 7));
        lines.push(record(["0.6", "", "", "", "", ""], 2631, 4, 2, 8));
        lines
    }

    #[test]
    fn mf4_legendre_parses_and_interpolates() {
        let lines = mf4_legendre();
        let parsed = parse_mf4(&section(4, 2, &lines)).unwrap();
        assert_eq!(parsed.lct, 2);
        assert_eq!(parsed.distribution.energies, vec![1.0e6, 2.0e6]);
        assert_eq!(parsed.distribution.laws, vec![2]);
        // below the first energy the distribution is isotropic
        let below = parsed.distribution.at(5.0e5);
        assert!((below.pdf(0.7) - 0.5).abs() < 1e-15);
        // at the first energy: f(mu) = 1/2 + 3/2 * 0.3 mu + 5/2 * 0.1 P2(mu)
        let first = parsed.distribution.at(1.0e6);
        let mu: f64 = 0.4;
        let expected = 0.5 + 1.5 * 0.3 * mu + 2.5 * 0.1 * 0.5 * (3.0 * mu * mu - 1.0);
        assert!((first.pdf(mu) - expected).abs() < 1e-12);
        // halfway the pdf is the mean of the two records
        let middle = parsed.distribution.at(1.5e6);
        let other = 0.5 + 1.5 * 0.6 * mu;
        assert!((middle.pdf(mu) - 0.5 * (expected + other)).abs() < 1e-12);
        // normalized: the integral over mu of a Legendre expansion is 1
        let mut sum = 0.0;
        let n = 2000;
        for i in 0..n {
            let x = -1.0 + (i as f64 + 0.5) * 2.0 / n as f64;
            sum += first.pdf(x) * 2.0 / n as f64;
        }
        assert!((sum - 1.0).abs() < 1e-6);
    }

    #[test]
    fn mf4_isotropic_flag_and_tabulated_and_mixed() {
        let iso = vec![
            record(["26056", "55.45", "0", "0", "0", "0"], 2631, 4, 2, 1),
            record(["0", "55.45", "1", "2", "0", "0"], 2631, 4, 2, 2),
        ];
        let parsed = parse_mf4(&section(4, 2, &iso)).unwrap();
        assert!(parsed.distribution.is_isotropic());

        // LTT=3: one Legendre energy then one tabulated energy
        let lines = vec![
            record(["26056", "55.45", "0", "3", "0", "0"], 2631, 4, 51, 1),
            record(["0", "55.45", "0", "1", "0", "0"], 2631, 4, 51, 2),
            record(["0", "0", "0", "0", "1", "1"], 2631, 4, 51, 3),
            record(["1", "2", "", "", "", ""], 2631, 4, 51, 4),
            record(["0", "1.0e6", "0", "0", "1", "0"], 2631, 4, 51, 5),
            record(["0.2", "", "", "", "", ""], 2631, 4, 51, 6),
            record(["0", "0", "0", "0", "1", "1"], 2631, 4, 51, 7),
            record(["1", "2", "", "", "", ""], 2631, 4, 51, 8),
            record(["0", "3.0e6", "0", "0", "1", "3"], 2631, 4, 51, 9),
            record(["3", "2", "", "", "", ""], 2631, 4, 51, 10),
            record(["-1", "0.25", "0", "0.5", "1", "0.75"], 2631, 4, 51, 11),
        ];
        let parsed = parse_mf4(&section(4, 51, &lines)).unwrap();
        assert_eq!(parsed.lct, 1);
        assert_eq!(parsed.distribution.energies, vec![1.0e6, 3.0e6]);
        assert!(matches!(
            parsed.distribution.records[1],
            AngularRecord::Tabulated(_)
        ));
        // the junction interval is linear in energy
        assert_eq!(parsed.distribution.laws, vec![2]);
        // the tabulated record is evaluated through its own table
        let at = parsed.distribution.at(3.0e6);
        assert!((at.pdf(-1.0) - 0.25).abs() < 1e-12);
        assert!((at.pdf(0.0) - 0.5).abs() < 1e-12);
        let mut breaks = Vec::new();
        at.breakpoints(&mut breaks);
        assert_eq!(breaks, vec![-1.0, 0.0, 1.0]);
    }

    #[test]
    fn mf4_rejects_trailing_records_and_unknown_forms() {
        let mut lines = mf4_legendre();
        lines.push(record(["0", "0", "0", "0", "0", "0"], 2631, 4, 2, 9));
        assert!(parse_mf4(&section(4, 2, &lines))
            .unwrap_err()
            .contains("unconsumed"));
        let mut bad = mf4_legendre();
        bad[1] = record(["0", "55.45", "0", "3", "0", "0"], 2631, 4, 2, 2);
        assert!(parse_mf4(&section(4, 2, &bad)).is_err());
    }

    /// MF=6 with one `LAW=1` Kalbach-Mann product (NA=1: r only) and one `LAW=2` product.
    fn mf6_law1_and_law2() -> Vec<String> {
        let mut seq = 1;
        let mut lines = vec![record(
            ["26056", "55.45", "0", "3", "2", "0"],
            2631,
            6,
            91,
            seq,
        )];
        seq += 1;
        // product 1: neutron, LAW=1, yield 1
        lines.push(record(["1", "1", "0", "1", "1", "2"], 2631, 6, 91, seq));
        seq += 1;
        lines.push(record(["2", "2", "", "", "", ""], 2631, 6, 91, seq));
        seq += 1;
        lines.push(record(
            ["1.0e6", "1", "2.0e8", "1", "", ""],
            2631,
            6,
            91,
            seq,
        ));
        seq += 1;
        // TAB2: LANG=2, LEP=2, NR=1, NE=2
        lines.push(record(["0", "0", "2", "2", "1", "2"], 2631, 6, 91, seq));
        seq += 1;
        lines.push(record(["2", "2", "", "", "", ""], 2631, 6, 91, seq));
        seq += 1;
        for (energy, tuples) in [
            (
                "1.0e6",
                [[0.0, 0.0, 0.0], [1.0e5, 2.0e-5, 0.1], [2.0e5, 0.0, 0.2]],
            ),
            (
                "2.0e6",
                [[0.0, 0.0, 0.0], [2.0e5, 1.0e-5, 0.1], [4.0e5, 0.0, 0.2]],
            ),
        ] {
            lines.push(record(["0", energy, "0", "1", "9", "3"], 2631, 6, 91, seq));
            seq += 1;
            let values: Vec<String> = tuples
                .iter()
                .flat_map(|t| t.iter().map(|v| num(*v)))
                .collect();
            lines.extend(packed(&values, 2631, 6, 91, &mut seq));
        }
        // product 2: alpha, LAW=2 with LANG=0 at one energy
        lines.push(record(
            ["2004", "3.968", "0", "2", "1", "2"],
            2631,
            6,
            91,
            seq,
        ));
        seq += 1;
        lines.push(record(["2", "2", "", "", "", ""], 2631, 6, 91, seq));
        seq += 1;
        lines.push(record(
            ["1.0e6", "1", "2.0e8", "1", "", ""],
            2631,
            6,
            91,
            seq,
        ));
        seq += 1;
        lines.push(record(["0", "0", "0", "0", "1", "1"], 2631, 6, 91, seq));
        seq += 1;
        lines.push(record(["1", "2", "", "", "", ""], 2631, 6, 91, seq));
        seq += 1;
        lines.push(record(["0", "1.0e6", "0", "0", "2", "2"], 2631, 6, 91, seq));
        seq += 1;
        lines.push(record(["0.1", "0.05", "", "", "", ""], 2631, 6, 91, seq));
        lines
    }

    #[test]
    fn mf6_law1_kalbach_and_law2_are_retained() {
        let lines = mf6_law1_and_law2();
        let parsed = parse_mf6_data(&section(6, 91, &lines)).unwrap();
        assert_eq!(parsed.lct, 3);
        assert_eq!(parsed.products.len(), 2);
        let neutron = &parsed.products[0];
        assert_eq!((neutron.zap, neutron.law), (1, 1));
        let Mf6Body::Law1(law1) = &neutron.body else {
            panic!("expected LAW=1");
        };
        assert_eq!((law1.lang, law1.lep), (2, 2));
        assert_eq!(law1.energies.len(), 2);
        assert_eq!(law1.laws, vec![2]);
        let first = &law1.energies[0];
        assert_eq!((first.nd, first.na), (0, 1));
        assert_eq!(first.eprime, vec![0.0, 1.0e5, 2.0e5]);
        assert_eq!(first.f0, vec![0.0, 2.0e-5, 0.0]);
        assert_eq!(first.angular, vec![0.0, 0.1, 0.2]);
        let alpha = &parsed.products[1];
        assert_eq!((alpha.zap, alpha.law), (2004, 2));
        let Mf6Body::Law2(distribution) = &alpha.body else {
            panic!("expected LAW=2");
        };
        assert_eq!(distribution.energies, vec![1.0e6]);
        let at = distribution.at(1.0e6);
        assert!(
            (at.pdf(0.5) - (0.5 + 1.5 * 0.1 * 0.5 + 2.5 * 0.05 * 0.5 * (3.0 * 0.25 - 1.0))).abs()
                < 1e-12
        );
    }

    #[test]
    fn mf6_malformed_list_length_is_an_error() {
        let mut lines = mf6_law1_and_law2();
        // NW = 9 declared for NEP=3, NA=1 is right; corrupt NEP
        lines[6] = record(["0", "1.0e6", "0", "1", "9", "4"], 2631, 6, 91, 7);
        let error = parse_mf6_data(&section(6, 91, &lines)).unwrap_err();
        assert!(error.contains("NW=9"), "{error}");
    }

    #[test]
    fn mf3_q_values_come_from_the_tab1_head() {
        let lines = vec![
            record(["26056", "55.45", "0", "0", "0", "0"], 2631, 3, 51, 1),
            record(["0", "-8.46778e5", "0", "0", "1", "2"], 2631, 3, 51, 2),
            record(["2", "2", "", "", "", ""], 2631, 3, 51, 3),
            record(["1.0e6", "1", "2.0e8", "2", "", ""], 2631, 3, 51, 4),
        ];
        let q = parse_mf3_q(&section(3, 51, &lines)).unwrap();
        assert_eq!(q.qm_ev, 0.0);
        assert_eq!(q.qi_ev, -8.46778e5);
    }

    #[test]
    fn tape_parse_keeps_failures_as_errors() {
        let send = record(["", "", "", "", "", ""], 2631, 4, 0, 99_999);
        let mut lines = mf4_legendre();
        lines.push(send.clone());
        lines.extend([
            record(["26056", "55.45", "0", "2", "0", "0"], 2631, 4, 51, 1),
            record(["0", "55.45", "0", "2", "0", "0"], 2631, 4, 51, 2),
            send,
        ]);
        let text = lines.join("\n");
        let materials = parse_damage_endf(&text).unwrap();
        let material = &materials[&2631];
        assert!(material.mf4[&2].is_ok());
        assert!(material.mf4[&51].is_err());
    }

    #[test]
    fn interpolation_laws() {
        assert_eq!(interpolate(1, 0.0, 1.0, 0.5, 2.0, 4.0), 2.0);
        assert_eq!(interpolate(2, 0.0, 1.0, 0.5, 2.0, 4.0), 3.0);
        let log_lin = interpolate(4, 0.0, 1.0, 0.5, 1.0, 4.0);
        assert!((log_lin - 2.0).abs() < 1e-12);
        let log_log = interpolate(5, 1.0, 4.0, 2.0, 1.0, 4.0);
        assert!((log_log - 2.0).abs() < 1e-12);
    }
}
