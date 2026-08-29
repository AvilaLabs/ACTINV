//! Strict ENDF-6 activation-evaluation reader.
//!
//! This module owns the reaction and product records needed by the P10 library builder. It deliberately consumes the
//! complete payload of every supported MF=3/6/8/9/10 section: accepting the useful prefix of a malformed section
//! would make an activation library look complete when it is not.

use crate::endf::{
    parse_sections, read_cont_checked, read_list_checked, read_tab1_checked, read_tab2_checked,
    ContRecord, Section,
};
use crate::groups::Tabulated;
use crate::resonance::{parse_mf2, ResonanceEvaluation};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;

#[derive(Clone, Copy, Debug, Default, Deserialize, Eq, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Projectile {
    #[default]
    Neutron,
    Proton,
    Deuteron,
    Alpha,
}

impl Projectile {
    pub fn parse(value: &str) -> Result<Self, String> {
        match value {
            "neutron" => Ok(Self::Neutron),
            "proton" => Ok(Self::Proton),
            "deuteron" => Ok(Self::Deuteron),
            "alpha" => Ok(Self::Alpha),
            _ => Err(format!(
                "unknown projectile '{value}'; expected neutron, proton, deuteron or alpha"
            )),
        }
    }

    pub fn name(self) -> &'static str {
        match self {
            Self::Neutron => "neutron",
            Self::Proton => "proton",
            Self::Deuteron => "deuteron",
            Self::Alpha => "alpha",
        }
    }

    pub fn is_neutron(self) -> bool {
        self == Self::Neutron
    }

    pub fn nsub(self) -> usize {
        match self {
            Self::Neutron => 10,
            Self::Proton => 10_010,
            Self::Deuteron => 10_020,
            Self::Alpha => 20_040,
        }
    }

    /// Projectile `(Z, A)` for residual-nuclide arithmetic.
    pub fn za(self) -> (i32, i32) {
        match self {
            Self::Neutron => (0, 1),
            Self::Proton => (1, 1),
            Self::Deuteron => (1, 2),
            Self::Alpha => (2, 4),
        }
    }

    fn from_nsub(nsub: usize) -> Result<Self, String> {
        match nsub {
            10 => Ok(Self::Neutron),
            10_010 => Ok(Self::Proton),
            10_020 => Ok(Self::Deuteron),
            20_040 => Ok(Self::Alpha),
            _ => Err(format!("unsupported incident-particle NSUB={nsub}")),
        }
    }

    fn validate_awi(self, awi: f64) -> Result<(), String> {
        let nominal_mass = f64::from(self.za().1);
        if !awi.is_finite() || awi <= 0.0 || (awi - nominal_mass).abs() > 0.1 * nominal_mass {
            return Err(format!(
                "AWI={awi} is inconsistent with {} NSUB={}",
                self.name(),
                self.nsub()
            ));
        }
        Ok(())
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct TargetMetadata {
    pub mat: i32,
    pub za: i32,
    pub awr: f64,
    /// Excitation energy of the evaluated target state (ENDF `ELIS`, eV).
    pub elis_ev: f64,
    /// Physical level number of the evaluated target state (ENDF `LIS`).
    pub lis: i32,
    /// Isomeric ordinal of the evaluated target state (ENDF `LISO`).
    pub liso: i32,
    pub awi: f64,
    pub nsub: usize,
    pub projectile: Projectile,
    /// Temperature declared for the tabulated evaluation by MF=1/MT=451 (K).
    pub evaluation_temperature_k: f64,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct ProductRef {
    pub zap: i32,
    /// Physical excitation energy declared by MF=8 (ENDF `ELFS`, eV).
    pub elfs_ev: f64,
    pub lfs: i32,
    pub lmf: i32,
}

#[derive(Clone, Debug, PartialEq)]
pub struct ProductTable {
    pub zap: i32,
    /// Mass-difference Q value from the MF=9/10 TAB1 head (eV).
    pub qm_ev: f64,
    /// State-specific reaction Q value from the MF=9/10 TAB1 head (eV).
    pub qi_ev: f64,
    pub lfs: i32,
    pub table: Tabulated,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Mf6Product {
    pub zap: i32,
    pub awp: f64,
    pub law: i32,
    pub yield_table: Tabulated,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Evaluation {
    pub metadata: TargetMetadata,
    /// MF=2 MT numbers present in the evaluation. Resonance parsing owns these sections in the next builder layer;
    /// retaining their presence here prevents a caller from silently treating raw MF=3 as a processed neutron file.
    pub mf2_sections: BTreeSet<i32>,
    pub resonance: Option<ResonanceEvaluation>,
    pub mf3: BTreeMap<i32, Tabulated>,
    pub mf6: BTreeMap<i32, Vec<Mf6Product>>,
    pub mf8: BTreeMap<i32, Vec<ProductRef>>,
    pub mf9: BTreeMap<i32, Vec<ProductTable>>,
    pub mf10: BTreeMap<i32, Vec<ProductTable>>,
}

fn exact_nonnegative_i32(value: f64, name: &str) -> Result<i32, String> {
    if !value.is_finite() || value < 0.0 || value > f64::from(i32::MAX) {
        return Err(format!("invalid {name} {value}"));
    }
    let rounded = value.round() as i32;
    if (value - f64::from(rounded)).abs() > 1e-7 {
        return Err(format!("nonintegral {name} {value}"));
    }
    Ok(rounded)
}

fn validate_incident_table(
    table: &Tabulated,
    name: &str,
    nonnegative_y: bool,
) -> Result<(), String> {
    if table.x.iter().any(|energy| *energy < 0.0) {
        return Err(format!("{name} contains a negative incident energy"));
    }
    if nonnegative_y && table.y.iter().any(|value| *value < 0.0) {
        return Err(format!("{name} contains a negative physical value"));
    }
    Ok(())
}

fn metadata(section: &Section<'_>) -> Result<TargetMetadata, String> {
    if section.lines.len() < 4 {
        return Err("MF=1/MT=451 lacks a required target, projectile or temperature record".into());
    }
    let head = ContRecord::parse(section.lines[0])?;
    let target = ContRecord::parse(section.lines[1])?;
    let incident = ContRecord::parse(section.lines[2])?;
    let processing = ContRecord::parse(section.lines[3])?;
    let za = exact_nonnegative_i32(head.c1, "target ZA")?;
    if za == 0 || !head.c2.is_finite() || head.c2 <= 0.0 {
        return Err(format!("invalid target metadata ZA={za}, AWR={}", head.c2));
    }
    if target.l2 < 0 {
        return Err(format!("invalid target LISO={}", target.l2));
    }
    if target.l1 < 0 {
        return Err(format!("invalid target LIS={}", target.l1));
    }
    if !target.c1.is_finite() || target.c1 < 0.0 {
        return Err(format!("invalid target ELIS={} eV", target.c1));
    }
    let projectile = Projectile::from_nsub(incident.n1)?;
    projectile.validate_awi(incident.c1)?;
    if !processing.c1.is_finite() || processing.c1 < 0.0 {
        return Err(format!(
            "invalid evaluation temperature {} K",
            processing.c1
        ));
    }
    Ok(TargetMetadata {
        mat: section.mat,
        za,
        awr: head.c2,
        elis_ev: target.c1,
        lis: target.l1,
        liso: target.l2,
        awi: incident.c1,
        nsub: incident.n1,
        projectile,
        evaluation_temperature_k: processing.c1,
    })
}

fn parse_mf3(section: &Section<'_>) -> Result<Tabulated, String> {
    let (record, next) = read_tab1_checked(&section.lines, 1)?;
    if next != section.lines.len() {
        return Err(format!(
            "{} unconsumed MF=3 records",
            section.lines.len() - next
        ));
    }
    let table = Tabulated::try_from(record)?;
    // Some evaluated elastic (MT=2) backgrounds are intentionally negative. Product-building code applies stricter
    // nonnegativity checks to the activation channels it emits.
    validate_incident_table(&table, "MF=3 TAB1", false)?;
    Ok(table)
}

fn parse_mf8(section: &Section<'_>) -> Result<Vec<ProductRef>, String> {
    let (head, mut next) = read_cont_checked(&section.lines, 0)?;
    if !matches!(head.n2, 0 | 1) {
        return Err(format!("unsupported MF=8 NO={}", head.n2));
    }
    let mut products = Vec::with_capacity(head.n1);
    for _ in 0..head.n1 {
        let product_head;
        if head.n2 == 0 {
            let (record, after) = read_list_checked(&section.lines, next)?;
            product_head = record.head;
            next = after;
        } else {
            let (record, after) = read_cont_checked(&section.lines, next)?;
            product_head = record;
            next = after;
        }
        let zap = if product_head.c1 == -1.0 {
            -1
        } else {
            exact_nonnegative_i32(product_head.c1, "MF=8 ZAP")?
        };
        if product_head.l2 < 0 {
            return Err(format!("invalid MF=8 LFS={}", product_head.l2));
        }
        if !product_head.c2.is_finite() || product_head.c2 < 0.0 {
            return Err(format!("invalid MF=8 ELFS={} eV", product_head.c2));
        }
        if !matches!(product_head.l1, 3 | 6 | 9 | 10) {
            return Err(format!("unsupported MF=8 LMF={}", product_head.l1));
        }
        if zap == -1 && (section.mt != 18 || product_head.l1 != 10 || product_head.l2 != 0) {
            return Err(
                "MF=8 ZAP=-1 is valid only for the MT=18/LMF=10 total-fission sentinel".into(),
            );
        }
        products.push(ProductRef {
            zap,
            elfs_ev: product_head.c2,
            lfs: product_head.l2,
            lmf: product_head.l1,
        });
    }
    if next != section.lines.len() {
        return Err(format!(
            "{} unconsumed MF=8 records",
            section.lines.len() - next
        ));
    }
    Ok(products)
}

fn parse_mf9_or_10(section: &Section<'_>) -> Result<Vec<ProductTable>, String> {
    let (head, mut next) = read_cont_checked(&section.lines, 0)?;
    let mut products = Vec::with_capacity(head.n1);
    for _ in 0..head.n1 {
        let (record, after) = read_tab1_checked(&section.lines, next)?;
        next = after;
        let zap = if record.head.l1 == -1 {
            -1
        } else {
            exact_nonnegative_i32(f64::from(record.head.l1), "product IZAP")?
        };
        if record.head.l2 < 0 {
            return Err(format!("invalid product LFS={}", record.head.l2));
        }
        let lfs = record.head.l2;
        if zap == -1 && (section.mf != 10 || section.mt != 18 || lfs != 0) {
            return Err("IZAP=-1 is valid only for the MF=10/MT=18 total-fission sentinel".into());
        }
        let qm_ev = record.head.c1;
        let qi_ev = record.head.c2;
        let table = Tabulated::try_from(record)?;
        validate_incident_table(&table, "product TAB1", true)?;
        products.push(ProductTable {
            zap,
            qm_ev,
            qi_ev,
            lfs,
            table,
        });
    }
    if next != section.lines.len() {
        return Err(format!(
            "{} unconsumed MF={} records",
            section.lines.len() - next,
            section.mf
        ));
    }
    Ok(products)
}

fn consume_mf6_law(lines: &[&str], index: usize, law: i32) -> Result<usize, String> {
    match law {
        0 | 3 | 4 => Ok(index),
        1 | 2 | 5 => {
            let (tab2, mut next) = read_tab2_checked(lines, index)?;
            for _ in 0..tab2.head.n2 {
                let (_, after) = read_list_checked(lines, next)?;
                next = after;
            }
            Ok(next)
        }
        6 => {
            let (_, next) = read_cont_checked(lines, index)?;
            Ok(next)
        }
        7 => {
            let (energies, mut next) = read_tab2_checked(lines, index)?;
            for _ in 0..energies.head.n2 {
                let (angles, after_angles) = read_tab2_checked(lines, next)?;
                next = after_angles;
                for _ in 0..angles.head.n2 {
                    let (_, after) = read_tab1_checked(lines, next)?;
                    next = after;
                }
            }
            Ok(next)
        }
        _ => Err(format!("unsupported MF=6 LAW={law}")),
    }
}

fn parse_mf6(section: &Section<'_>) -> Result<Vec<Mf6Product>, String> {
    let (head, mut next) = read_cont_checked(&section.lines, 0)?;
    let mut products = Vec::with_capacity(head.n1);
    for _ in 0..head.n1 {
        let (record, after_yield) = read_tab1_checked(&section.lines, next)?;
        let zap = exact_nonnegative_i32(record.head.c1, "MF=6 ZAP")?;
        if !record.head.c2.is_finite() || record.head.c2 < 0.0 {
            return Err(format!("invalid MF=6 AWP={}", record.head.c2));
        }
        let awp = record.head.c2;
        let law = record.head.l2;
        let yield_table = Tabulated::try_from(record)?;
        validate_incident_table(&yield_table, "MF=6 yield", true)?;
        next = consume_mf6_law(&section.lines, after_yield, law)?;
        products.push(Mf6Product {
            zap,
            awp,
            law,
            yield_table,
        });
    }
    if next != section.lines.len() {
        return Err(format!(
            "{} unconsumed MF=6 records",
            section.lines.len() - next
        ));
    }
    Ok(products)
}

fn insert_unique<T>(map: &mut BTreeMap<i32, T>, mt: i32, value: T, mf: i32) -> Result<(), String> {
    if map.insert(mt, value).is_some() {
        Err(format!("duplicate MF={mf}/MT={mt}"))
    } else {
        Ok(())
    }
}

/// Parse all material evaluations in an ENDF tape.
pub fn parse_evaluations(
    text: &str,
    expected_projectile: Option<Projectile>,
) -> Result<Vec<Evaluation>, String> {
    let sections = parse_sections(text)?;
    let mut by_mat: BTreeMap<i32, Vec<&Section<'_>>> = BTreeMap::new();
    for section in &sections {
        by_mat.entry(section.mat).or_default().push(section);
    }
    if by_mat.is_empty() {
        return Err("ENDF tape contains no material sections".into());
    }

    let mut evaluations = Vec::with_capacity(by_mat.len());
    for (mat, material_sections) in by_mat {
        let directory = material_sections
            .iter()
            .find(|section| section.mf == 1 && section.mt == 451)
            .ok_or_else(|| format!("MAT={mat} has no MF=1/MT=451 section"))?;
        let metadata =
            metadata(directory).map_err(|error| format!("MAT={mat}/MF=1/MT=451: {error}"))?;
        if let Some(expected) = expected_projectile {
            if metadata.projectile != expected {
                return Err(format!(
                    "MAT={mat} is {}, expected {}",
                    metadata.projectile.name(),
                    expected.name()
                ));
            }
        }
        let mut evaluation = Evaluation {
            metadata,
            mf2_sections: BTreeSet::new(),
            resonance: None,
            mf3: BTreeMap::new(),
            mf6: BTreeMap::new(),
            mf8: BTreeMap::new(),
            mf9: BTreeMap::new(),
            mf10: BTreeMap::new(),
        };
        for section in material_sections {
            let context = format!("MAT={mat}/MF={}/MT={}", section.mf, section.mt);
            match (section.mf, section.mt) {
                (2, 151) => {
                    if evaluation.resonance.is_some() {
                        return Err(format!("{context}: duplicate resonance evaluation"));
                    }
                    let parsed =
                        parse_mf2(section).map_err(|error| format!("{context}: {error}"))?;
                    if parsed.za != evaluation.metadata.za
                        || (parsed.awr - evaluation.metadata.awr).abs()
                            > 1e-7 * evaluation.metadata.awr.abs().max(1.0)
                    {
                        return Err(format!(
                            "{context}: MF=2 ZA/AWR {}/{} disagree with MF=1 {}/{}",
                            parsed.za, parsed.awr, evaluation.metadata.za, evaluation.metadata.awr
                        ));
                    }
                    evaluation.mf2_sections.insert(151);
                    evaluation.resonance = Some(parsed);
                }
                (2, mt) => {
                    evaluation.mf2_sections.insert(mt);
                }
                (3, mt) => insert_unique(
                    &mut evaluation.mf3,
                    mt,
                    parse_mf3(section).map_err(|error| format!("{context}: {error}"))?,
                    3,
                )?,
                (6, mt) => insert_unique(
                    &mut evaluation.mf6,
                    mt,
                    parse_mf6(section).map_err(|error| format!("{context}: {error}"))?,
                    6,
                )?,
                (8, mt) if !matches!(mt, 454 | 457 | 459) => insert_unique(
                    &mut evaluation.mf8,
                    mt,
                    parse_mf8(section).map_err(|error| format!("{context}: {error}"))?,
                    8,
                )?,
                (8, 454 | 459) if evaluation.metadata.projectile != Projectile::Neutron => {
                    return Err(format!(
                        "{context}: fission-yield sections are unsupported for {} evaluations",
                        evaluation.metadata.projectile.name()
                    ));
                }
                (9, mt) => insert_unique(
                    &mut evaluation.mf9,
                    mt,
                    parse_mf9_or_10(section).map_err(|error| format!("{context}: {error}"))?,
                    9,
                )?,
                (10, mt) => insert_unique(
                    &mut evaluation.mf10,
                    mt,
                    parse_mf9_or_10(section).map_err(|error| format!("{context}: {error}"))?,
                    10,
                )?,
                _ => {}
            }
        }
        evaluations.push(evaluation);
    }
    Ok(evaluations)
}

/// Read and parse all material evaluations in one ENDF file.
pub fn parse_file(
    path: impl AsRef<Path>,
    expected_projectile: Option<Projectile>,
) -> Result<Vec<Evaluation>, String> {
    let path = path.as_ref();
    let text = std::fs::read_to_string(path)
        .map_err(|error| format!("cannot read {}: {error}", path.display()))?;
    parse_evaluations(&text, expected_projectile)
        .map_err(|error| format!("{}: {error}", path.display()))
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

    fn send(mat: i32, mf: i32) -> String {
        record(["", "", "", "", "", ""], mat, mf, 0, 99_999)
    }

    fn basic_tape(extra: &[String]) -> String {
        let mut lines = vec![
            record(["26056", "55.45", "0", "0", "0", "0"], 2631, 1, 451, 1),
            record(["123.5", "0", "2", "1", "0", "0"], 2631, 1, 451, 2),
            record(["0.999", "2e8", "1", "0", "10010", "2025"], 2631, 1, 451, 3),
            record(["0", "0", "0", "0", "0", "0"], 2631, 1, 451, 4),
            send(2631, 1),
        ];
        lines.extend_from_slice(extra);
        lines.join("\n")
    }

    fn tab1(mat: i32, mf: i32, mt: i32, head: [&str; 4]) -> Vec<String> {
        vec![
            record(
                [head[0], head[1], head[2], head[3], "1", "2"],
                mat,
                mf,
                mt,
                2,
            ),
            record(["2", "2", "", "", "", ""], mat, mf, mt, 3),
            record(["1", "0", "2", "1", "", ""], mat, mf, mt, 4),
        ]
    }

    #[test]
    fn parses_reaction_and_all_product_table_families() {
        let mut extra = vec![record(
            ["26056", "55.45", "0", "0", "0", "0"],
            2631,
            3,
            102,
            1,
        )];
        extra.extend(tab1(2631, 3, 102, ["0", "0", "0", "0"]));
        extra.push(send(2631, 3));

        extra.push(record(
            ["26056", "55.45", "0", "0", "1", "1"],
            2631,
            8,
            102,
            1,
        ));
        extra.push(record(
            ["26057", "123.5", "3", "1", "0", "0"],
            2631,
            8,
            102,
            2,
        ));
        extra.push(send(2631, 8));

        for mf in [9, 10] {
            extra.push(record(
                ["26056", "55.45", "0", "0", "1", "0"],
                2631,
                mf,
                102,
                1,
            ));
            extra.extend(tab1(2631, mf, 102, ["1000", "876.5", "26057", "1"]));
            extra.push(send(2631, mf));
        }

        extra.push(record(
            ["26056", "55.45", "0", "0", "1", "0"],
            2631,
            6,
            5,
            1,
        ));
        extra.extend(tab1(2631, 6, 5, ["26057", "56.4", "0", "0"]));
        extra.push(send(2631, 6));

        let evaluations = parse_evaluations(&basic_tape(&extra), Some(Projectile::Proton)).unwrap();
        assert_eq!(evaluations.len(), 1);
        let evaluation = &evaluations[0];
        assert_eq!(evaluation.metadata.za, 26056);
        assert_eq!(evaluation.metadata.elis_ev, 123.5);
        assert_eq!(evaluation.metadata.lis, 2);
        assert_eq!(evaluation.metadata.liso, 1);
        assert_eq!(evaluation.metadata.projectile, Projectile::Proton);
        assert_eq!(evaluation.mf3.len(), 1);
        assert_eq!(evaluation.mf6[&5][0].zap, 26057);
        assert_eq!(evaluation.mf8[&102][0].lmf, 3);
        assert_eq!(evaluation.mf8[&102][0].elfs_ev, 123.5);
        assert_eq!(evaluation.mf9[&102][0].zap, 26057);
        assert_eq!(evaluation.mf9[&102][0].qm_ev, 1000.0);
        assert_eq!(evaluation.mf9[&102][0].qi_ev, 876.5);
        assert_eq!(evaluation.mf10[&102][0].zap, 26057);
    }

    #[test]
    fn rejects_unconsumed_and_projectile_mismatch() {
        let mut section = vec![record(
            ["26056", "55.45", "0", "0", "0", "0"],
            2631,
            3,
            102,
            1,
        )];
        section.extend(tab1(2631, 3, 102, ["0", "0", "0", "0"]));
        section.push(record(["", "", "", "", "", ""], 2631, 3, 102, 5));
        section.push(send(2631, 3));
        let error = parse_evaluations(&basic_tape(&section), None).unwrap_err();
        assert!(error.contains("unconsumed MF=3"), "{error}");

        let error = parse_evaluations(&basic_tape(&[]), Some(Projectile::Alpha)).unwrap_err();
        assert!(error.contains("expected alpha"), "{error}");
    }
}
