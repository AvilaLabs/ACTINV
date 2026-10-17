//! `actinv waste package` — concentration averaging of one container of discrete items under the NRC
//! Concentration Averaging and Encapsulation Branch Technical Position (Revision 1, Volume 1, February 2015).
//!
//! Each target step is classified in four layers: the nominal Part 61 evaluator for every item and every average;
//! the item tests of BTP Section 3.3.2 (BTP Table 2, BTP Table 3, the Factors of 2 and 10) at each candidate class;
//! sectioned components (Section 3.3.2.3) and the optional screens (Section 3.3.2.1); and encapsulation credit
//! (Section 3.3.4). The readings the BTP leaves open are fixed in `protocols/ACTINV-P147_PROTOCOL.md` and listed in
//! every result. Concentrations and limits come from the evaluator's own rows; this module adds no Part 61 data.

use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;

use actinv_core::waste::{
    evaluate_component, normalize_nuclide_key, Coverage, Evaluation, NuclideProperties, RulePack,
    WasteClass, WasteGeometry, WasteType,
};
use actinv_core::waste_btp::{BtpData, ALPHA_TRU_KEY};
use serde::Deserialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

use crate::waste::{self, ComponentCell, ComponentSpec, ExternalTritium, InputResults};

const SPEC_SCHEMA: &str = "actinv-waste-package-spec-1";
const RESULT_SCHEMA: &str = "actinv-waste-package-1";
const BUNDLED_BTP_ID: &str = "us-nrc-ca-btp-2015-rev1-v1";
/// Candidate mixture classes in the order the loop walks them.
const CANDIDATES: [WasteClass; 3] = [WasteClass::A, WasteClass::B, WasteClass::C];

const READINGS: [&str; 9] = [
    "Encapsulation volume: 331 ft3 = 9.373 m3 (the BTP text says 9.5 m3, Figure 5 and Volume 2 say 9.4 m3); the loading threshold is at least 14 %.",
    "Class limit in the Factors of 2 and 10: the limit of the class being tested for the mixture; for a CFR Table 1 nuclide 0.1 times the Table 1 value for Class A and B and the Table 1 value for Class C, for a CFR Table 2 nuclide that class's column; where the column has no limit the factor test does not apply to that nuclide in that class, and the BTP is not cited for it.",
    "Radionuclides of concern: a nuclide whose concentration in the item exceeds 1 % of its class limit for the class being tested.",
    "37 MBq screen: the total activity of the item, strictly less than 37 MBq.",
    "280 cc: items of 280 cm3 or more are individual for BTP Table 2, smaller items are pooled.",
    "Inequalities: a Factor test passes when the concentration is strictly less than the factor times the limit; a BTP Table 2 sum of fractions passes at 1 or less and a BTP Table 3 activity at the table value or less; the class determination keeps the evaluator's CFR conventions.",
    "BTP Table 3 transuranic row: the per-item sum of the alpha-emitting transuranics with half-life above 5 years, excluding Pu-241 and Cm-242.",
    "Waste loading: the sum of the encapsulated items' displaced (or envelope) volumes over the total encapsulated volume, unless the waste volume is declared.",
    "0.2 m3 applies to volume-based limits and 500 kg to mass-based limits, each capped at the actual encapsulated volume or credited mass; the encapsulant density credit is capped at 2.5 g/cm3.",
];

const NOT_CLAIMED: [&str; 7] = [
    "blending of homogeneous wastes (BTP 3.2)",
    "alternative approaches (BTP 3.8)",
    "10 CFR 61.58 authorizations",
    "site licence conditions, which override the BTP (BTP 3.9)",
    "10 CFR 61.56 structural stability",
    "centring procedures, compatibility documentation and encapsulation topical reports",
    "not a shipment classification or a disposal acceptance",
];

const CHOICES: [&str; 9] = [
    "Sectioned pieces are merged or split out for each candidate class, using the BTP Table 2 and Table 3 columns of that class; under a screen they use the class of the whole group's average.",
    "All three candidate classes are evaluated and reported; the mixture class is the lowest candidate whose remainder class does not exceed it, and a candidate whose items all fail qualifies with an empty remainder.",
    "The alpha transuranic group is one entry per item: its concern level and Factor of 10 use the members' summed concentration against the CFR Table 1 transuranic row, and BTP Table 3 the summed activity.",
    "A nuclide with two evaluator rows (Cm-242) is tested against every row that has a limit, and passes only if every row passes.",
    "An encapsulated package is one waste form over all its items. When its items carry both rule-row sets (activated metal and general), it is evaluated, item tests included, under each set and the higher class is kept; screens are not available with encapsulation.",
    "Items that fail their tests are classified alone, without encapsulation credit, and enter the package class.",
    "An item of a sectioned component that is alone in the package is refused: every piece of a component must be in the package.",
    "Declared activity is total Bq per nuclide and step; result-based activity is the per-gram activity of the target step times the item mass.",
    "A step without a stored timestamp (all activity declared) reports t_s as null.",
];

// ---------------------------------------------------------------------------------------------------------------
// spec

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct PackageSpec {
    schema: String,
    rules: String,
    #[serde(default)]
    btp: Option<String>,
    targets: Vec<u64>,
    nuclide_properties: BTreeMap<String, NuclideProperties>,
    #[serde(default)]
    screen: Option<String>,
    #[serde(default)]
    encapsulation: Option<EncapsulationSpec>,
    items: Vec<ItemSpec>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ItemSpec {
    id: String,
    waste_type: String,
    #[serde(default)]
    mass_g: Option<f64>,
    #[serde(default)]
    displaced_volume_cm3: Option<f64>,
    #[serde(default)]
    envelope_volume_cm3: Option<f64>,
    #[serde(default)]
    section_of: Option<String>,
    /// Defaults to true when the package has an encapsulation block.
    #[serde(default)]
    encapsulated: Option<bool>,
    activity: ActivitySpec,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ActivitySpec {
    #[serde(default)]
    result: Option<String>,
    #[serde(default)]
    cells: Vec<ComponentCell>,
    #[serde(default)]
    declared_bq: Option<BTreeMap<String, BTreeMap<String, f64>>>,
}

#[derive(Clone, Copy, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct EncapsulationSpec {
    total_volume_cm3: f64,
    encapsulant_mass_g: f64,
    encapsulant_density_g_cm3: f64,
    #[serde(default)]
    waste_volume_cm3: Option<f64>,
}

/// The five BTP waste types, in the order groups are reported.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
enum BtpType {
    ActivatedMetal,
    ContaminatedMaterial,
    DesignRadioactivityComponent,
    SealedSource,
    CartridgeFilter,
}

impl BtpType {
    fn parse(name: &str) -> Option<Self> {
        Some(match name {
            "activated_metal" => Self::ActivatedMetal,
            "contaminated_material" => Self::ContaminatedMaterial,
            "design_radioactivity_component" => Self::DesignRadioactivityComponent,
            "sealed_source" => Self::SealedSource,
            "cartridge_filter" => Self::CartridgeFilter,
            _ => return None,
        })
    }

    fn name(self) -> &'static str {
        match self {
            Self::ActivatedMetal => "activated_metal",
            Self::ContaminatedMaterial => "contaminated_material",
            Self::DesignRadioactivityComponent => "design_radioactivity_component",
            Self::SealedSource => "sealed_source",
            Self::CartridgeFilter => "cartridge_filter",
        }
    }

    /// The rule-pack applicability of the type: only activated metal has its own Part 61 rows.
    fn rules(self) -> WasteType {
        match self {
            Self::ActivatedMetal => WasteType::ActivatedMetal,
            _ => WasteType::General,
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Screen {
    None,
    Activity37Mbq,
    HighestItem,
}

impl Screen {
    fn parse(value: Option<&str>) -> Result<Self, String> {
        match value {
            None | Some("none") => Ok(Self::None),
            Some("activity_37mbq") => Ok(Self::Activity37Mbq),
            Some("highest_item") => Ok(Self::HighestItem),
            Some(other) => Err(format!(
                "unknown screen '{other}' (expected none, activity_37mbq or highest_item)"
            )),
        }
    }

    fn name(self) -> &'static str {
        match self {
            Self::None => "none",
            Self::Activity37Mbq => "activity_37mbq",
            Self::HighestItem => "highest_item",
        }
    }
}

// ---------------------------------------------------------------------------------------------------------------
// pure model: items, units, limits

/// One discrete item at one target step: whole-item activity in Bq per canonical nuclide.
#[derive(Clone, Debug)]
struct Item {
    id: String,
    btp_type: BtpType,
    volume_cm3: f64,
    mass_g: f64,
    acts: BTreeMap<String, f64>,
    section_of: Option<String>,
}

/// What the BTP tests treat as one item: an item, or the pieces of a sectioned component merged into it.
#[derive(Clone, Debug)]
struct Unit {
    id: String,
    volume_cm3: f64,
    mass_g: f64,
    acts: BTreeMap<String, f64>,
    /// ids of the merged pieces; empty for an ordinary item
    pieces: Vec<String>,
}

impl Item {
    fn unit(&self) -> Unit {
        Unit {
            id: self.id.clone(),
            volume_cm3: self.volume_cm3,
            mass_g: self.mass_g,
            acts: self.acts.clone(),
            pieces: Vec::new(),
        }
    }
}

struct Ctx<'a> {
    rules: &'a RulePack,
    btp: &'a BtpData,
    raw_properties: &'a BTreeMap<String, NuclideProperties>,
    /// the properties under canonical nuclide keys
    properties: BTreeMap<String, NuclideProperties>,
}

impl<'a> Ctx<'a> {
    fn new(
        rules: &'a RulePack,
        btp: &'a BtpData,
        raw_properties: &'a BTreeMap<String, NuclideProperties>,
    ) -> Result<Self, String> {
        let mut properties = BTreeMap::new();
        for (raw, value) in raw_properties {
            properties.insert(normalize_nuclide_key(raw)?, *value);
        }
        Ok(Self {
            rules,
            btp,
            raw_properties,
            properties,
        })
    }

    fn evaluate(
        &self,
        waste_type: WasteType,
        volume_cm3: f64,
        mass_g: f64,
        acts: &BTreeMap<String, f64>,
    ) -> Result<Evaluation, String> {
        evaluate_component(
            self.rules,
            waste_type,
            WasteGeometry {
                mass_g,
                displaced_volume_cm3: volume_cm3,
            },
            acts,
            self.raw_properties,
        )
    }

    /// Member of the BTP Table 3 transuranic group: alpha-emitting, half-life above 5 years, not Pu-241 or Cm-242.
    fn is_tru(&self, nuclide: &str) -> bool {
        nuclide != "Pu241"
            && nuclide != "Cm242"
            && self.properties.get(nuclide).is_some_and(|p| {
                p.z > 92 && p.alpha_emitting && p.half_life_s / self.rules.year_s > 5.0
            })
    }
}

fn rank(class: WasteClass) -> u8 {
    match class {
        WasteClass::A => 0,
        WasteClass::B => 1,
        WasteClass::C => 2,
        WasteClass::AboveClassC => 3,
        WasteClass::Unknown => 4,
    }
}

/// The higher of two classes; `unknown` outranks everything.
fn worst(a: WasteClass, b: WasteClass) -> WasteClass {
    if rank(b) > rank(a) {
        b
    } else {
        a
    }
}

fn totals<'a>(units: impl IntoIterator<Item = &'a Unit>) -> (f64, f64, BTreeMap<String, f64>) {
    let mut volume = 0.0;
    let mut mass = 0.0;
    let mut acts = BTreeMap::new();
    for unit in units {
        volume += unit.volume_cm3;
        mass += unit.mass_g;
        for (nuclide, bq) in &unit.acts {
            *acts.entry(nuclide.clone()).or_insert(0.0) += bq;
        }
    }
    (volume, mass, acts)
}

fn total_activity(acts: &BTreeMap<String, f64>) -> f64 {
    acts.values().fold(0.0, |sum, bq| sum + bq)
}

/// Sum of fractions of the primary gammas against BTP Table 2 for a class index; nuclides without a limit are
/// skipped.
fn table2_sum(btp: &BtpData, acts: &BTreeMap<String, f64>, class: usize) -> f64 {
    btp.primary_gammas().iter().fold(0.0, |sum, nuclide| {
        match btp.table2_limit_bq(nuclide, class) {
            Some(limit) => sum + acts.get(nuclide).copied().unwrap_or(0.0) / limit,
            None => sum,
        }
    })
}

/// BTP Table 3 for a class index: no nuclide, and not the transuranic group, exceeds its activity.
fn table3_ok(ctx: &Ctx, acts: &BTreeMap<String, f64>, class: usize) -> bool {
    for (nuclide, columns) in ctx.btp.table3_nuclides() {
        if let Some(limit) = columns[class] {
            if acts.get(nuclide).copied().unwrap_or(0.0) > limit {
                return false;
            }
        }
    }
    if let Some(limit) = ctx.btp.table3_tru_limit_bq(class) {
        let tru = acts
            .iter()
            .filter(|(nuclide, _)| ctx.is_tru(nuclide))
            .fold(0.0, |sum, (_, bq)| sum + bq);
        if tru > limit {
            return false;
        }
    }
    true
}

/// One evaluator row of a nuclide with its limit for each candidate class.
struct RowLimit {
    concentration: f64,
    /// class limit for Class A, B and C in the row's unit; `None` is no limit
    limits: [Option<f64>; 3],
}

type Rows = BTreeMap<String, Vec<RowLimit>>;

/// The evaluator's rows by nuclide: CFR Table 1 rows carry 0.1 times the table value for Class A and B and the
/// table value for Class C, CFR Table 2 rows their three columns.
fn rows_of(evaluation: &Evaluation) -> Rows {
    let mut rows: Rows = BTreeMap::new();
    for row in &evaluation.row_fractions {
        let list = rows.entry(row.nuclide.clone()).or_default();
        match row.table {
            1 => {
                let limits = match row.limit {
                    Some(value) => [Some(0.1 * value), Some(0.1 * value), Some(value)],
                    None => [None; 3],
                };
                list.push(RowLimit {
                    concentration: row.concentration,
                    limits,
                });
            }
            _ => {
                let column = usize::from(row.column.unwrap_or(1)).saturating_sub(1);
                if column == 0 {
                    list.push(RowLimit {
                        concentration: row.concentration,
                        limits: [row.limit, None, None],
                    });
                } else if let Some(last) = list.last_mut() {
                    last.limits[column.min(2)] = row.limit;
                }
            }
        }
    }
    rows
}

// ---------------------------------------------------------------------------------------------------------------
// the group average and the controlling test (BTP p.25)

struct Averaged {
    evaluation: Evaluation,
    restrictive_table: u8,
    table1_sum: f64,
    table2_sum: f64,
    share: f64,
    controlling: bool,
}

/// The class a CFR table alone gives: the lowest class whose constraint of that table passes.
fn table_class(evaluation: &Evaluation, table: u8) -> WasteClass {
    for target in CANDIDATES {
        if evaluation
            .constraints
            .iter()
            .filter(|c| c.table == table && c.target_class == target)
            .all(|c| c.passes)
        {
            return target;
        }
    }
    WasteClass::AboveClassC
}

fn constraint_sum(evaluation: &Evaluation, table: u8, target: WasteClass) -> f64 {
    evaluation
        .constraints
        .iter()
        .find(|c| c.table == table && c.target_class == target)
        .map_or(0.0, |c| c.source_sum_fraction)
}

/// Average the group over its summed volume and mass, and decide whether the primary gamma emitters are
/// classification-controlling: more than the controlling share of the more restrictive CFR table's sum of fractions.
/// The group average over its own volume and mass, or over `credit` (credited volume, credited mass) for an
/// encapsulated waste form, whose class is decided over the credited geometry.
fn averaged(
    ctx: &Ctx,
    waste_type: WasteType,
    units: &[Unit],
    credit: Option<(f64, f64)>,
) -> Result<Averaged, String> {
    let (own_volume, own_mass, acts) = totals(units);
    let (volume, mass) = credit.unwrap_or((own_volume, own_mass));
    let evaluation = ctx.evaluate(waste_type, volume, mass, &acts)?;
    let t1_class = table_class(&evaluation, 1);
    let t2_class = table_class(&evaluation, 2);
    // CFR Table 2 is summed against the column of the averaged class
    let column = usize::from(rank(evaluation.class).min(2));
    let table1_sum = constraint_sum(&evaluation, 1, WasteClass::A);
    let table2_sum = constraint_sum(&evaluation, 2, CANDIDATES[column]);
    let restrictive_table = if rank(t1_class) != rank(t2_class) {
        if rank(t1_class) > rank(t2_class) {
            1
        } else {
            2
        }
    } else if table1_sum > table2_sum {
        1
    } else {
        2
    };
    let (sum, table_column) = if restrictive_table == 1 {
        (table1_sum, None)
    } else {
        (table2_sum, Some(column as u8 + 1))
    };
    let primary = ctx.btp.primary_gammas();
    let primary_fraction = evaluation
        .row_fractions
        .iter()
        .filter(|r| {
            r.table == restrictive_table
                && (restrictive_table == 1 || r.column == table_column)
                && primary.contains(&r.nuclide)
        })
        .filter_map(|r| r.fraction)
        .fold(0.0, |total, fraction| total + fraction);
    let share = if sum > 0.0 {
        primary_fraction / sum
    } else {
        0.0
    };
    Ok(Averaged {
        evaluation,
        restrictive_table,
        table1_sum,
        table2_sum,
        share,
        controlling: share > ctx.btp.thresholds.controlling_share,
    })
}

// ---------------------------------------------------------------------------------------------------------------
// item tests (BTP 3.3.2.2)

struct PrimaryTest {
    limit_met: Option<String>,
    table2_sum: f64,
    factor: f64,
    /// primary gammas of concern at this class, which the factor test covered
    tested: Vec<String>,
    failing: Vec<String>,
}

struct OtherTest {
    nuclide: String,
    limit_met: Option<String>,
}

struct ItemTest {
    passes: bool,
    primary: PrimaryTest,
    others: Vec<OtherTest>,
}

/// Test one item at candidate class index `x`. `table2_sum` is the item's own sum of fractions, or the small-item
/// pool's when the item is under the small-item size.
fn test_unit(
    ctx: &Ctx,
    unit: &Unit,
    rows: &Rows,
    x: usize,
    controlling: bool,
    table2_sum: f64,
) -> ItemTest {
    let thresholds = &ctx.btp.thresholds;
    let factor = if controlling {
        thresholds.factor_controlling
    } else {
        thresholds.factor_other
    };
    let primary = ctx.btp.primary_gammas();

    let mut tested = Vec::new();
    let mut failing = Vec::new();
    for nuclide in primary {
        if unit.acts.get(nuclide).copied().unwrap_or(0.0) <= 0.0 {
            continue;
        }
        let mut concerned = false;
        let mut fails = false;
        for row in rows.get(nuclide).into_iter().flatten() {
            let Some(limit) = row.limits[x] else { continue };
            if row.concentration > thresholds.concern_fraction * limit {
                concerned = true;
                fails |= row.concentration >= factor * limit;
            }
        }
        if concerned {
            tested.push(nuclide.clone());
        }
        if fails {
            failing.push(nuclide.clone());
        }
    }
    let table2_ok = table2_sum <= 1.0;
    let factor_ok = failing.is_empty();
    let limit_met = if table2_ok {
        Some("table2".to_string())
    } else if factor_ok {
        Some(format!("factor{factor}"))
    } else {
        None
    };

    let mut others = Vec::new();
    for (nuclide, nuclide_rows) in rows {
        if primary.contains(nuclide) || ctx.is_tru(nuclide) {
            continue;
        }
        let limited: Vec<(&RowLimit, f64)> = nuclide_rows
            .iter()
            .filter_map(|row| row.limits[x].map(|limit| (row, limit)))
            .collect();
        if !limited
            .iter()
            .any(|(row, limit)| row.concentration > thresholds.concern_fraction * limit)
        {
            continue;
        }
        let activity = unit.acts.get(nuclide).copied().unwrap_or(0.0);
        // a nuclide outside BTP Table 3 has the factor only; "No limit" in Table 3 is met
        let table3_pass = match ctx.btp.table3_limit_bq(nuclide, x) {
            Some(Some(limit)) => activity <= limit,
            Some(None) => true,
            None => false,
        };
        let factor10_pass = limited
            .iter()
            .all(|(row, limit)| row.concentration < thresholds.factor_other * limit);
        others.push(OtherTest {
            nuclide: nuclide.clone(),
            limit_met: other_limit_met(table3_pass, factor10_pass, thresholds.factor_other),
        });
    }
    // the transuranic group is one BTP Table 3 entry and one CFR Table 1 row: its activity and its concentration are
    // the members' sums per item
    let members: Vec<&String> = rows.keys().filter(|n| ctx.is_tru(n)).collect();
    let member_rows: Vec<&RowLimit> = members.iter().filter_map(|n| rows[*n].first()).collect();
    let concentration: f64 = member_rows.iter().map(|row| row.concentration).sum();
    let group_limit = member_rows.first().and_then(|row| row.limits[x]);
    if let Some(limit) =
        group_limit.filter(|&limit| concentration > thresholds.concern_fraction * limit)
    {
        let activity = members.iter().fold(0.0, |sum, n| {
            sum + unit.acts.get(*n).copied().unwrap_or(0.0)
        });
        let table3_pass = ctx
            .btp
            .table3_tru_limit_bq(x)
            .is_none_or(|limit| activity <= limit);
        let factor10_pass = concentration < thresholds.factor_other * limit;
        others.push(OtherTest {
            nuclide: ALPHA_TRU_KEY.to_string(),
            limit_met: other_limit_met(table3_pass, factor10_pass, thresholds.factor_other),
        });
    }
    others.sort_by(|a, b| a.nuclide.cmp(&b.nuclide));

    let passes = limit_met.is_some() && others.iter().all(|o| o.limit_met.is_some());
    ItemTest {
        passes,
        primary: PrimaryTest {
            limit_met,
            table2_sum,
            factor,
            tested,
            failing,
        },
        others,
    }
}

fn other_limit_met(table3_ok: bool, factor_ok: bool, factor: f64) -> Option<String> {
    if table3_ok {
        Some("table3".to_string())
    } else if factor_ok {
        Some(format!("factor{factor}"))
    } else {
        None
    }
}

// ---------------------------------------------------------------------------------------------------------------
// sectioned components (BTP 3.3.2.3)

struct SectionInfo {
    component: String,
    pieces: Vec<String>,
    merged: bool,
    split_out: Vec<String>,
}

impl SectionInfo {
    fn json(&self) -> Value {
        json!({
            "component": self.component,
            "pieces": self.pieces,
            "merged": self.merged,
            "split_out": self.split_out,
        })
    }
}

/// The units the BTP tests treat as items at candidate class index `x`. The pieces of a sectioned component form
/// one unit when they can be merged: pieces over BTP Table 3, or under the small-item size and over BTP Table 2,
/// are separate items, and the rest merge when at least two remain.
fn build_units(ctx: &Ctx, items: &[Item], x: usize) -> (Vec<Unit>, Vec<SectionInfo>) {
    let small = ctx.btp.thresholds.small_item_cm3;
    let mut by_section: BTreeMap<&str, Vec<usize>> = BTreeMap::new();
    for (index, item) in items.iter().enumerate() {
        if let Some(section) = &item.section_of {
            by_section.entry(section.as_str()).or_default().push(index);
        }
    }
    let mut info = Vec::new();
    let mut merged_pieces: BTreeMap<&str, Vec<usize>> = BTreeMap::new();
    for (section, pieces) in &by_section {
        let mut keep = Vec::new();
        let mut split = Vec::new();
        for &index in pieces {
            let piece = &items[index];
            let fine = table3_ok(ctx, &piece.acts, x)
                && (piece.volume_cm3 >= small || table2_sum(ctx.btp, &piece.acts, x) <= 1.0);
            if fine {
                keep.push(index);
            } else {
                split.push(index);
            }
        }
        let merged = keep.len() >= 2;
        info.push(SectionInfo {
            component: (*section).to_string(),
            pieces: pieces.iter().map(|&i| items[i].id.clone()).collect(),
            merged,
            split_out: split.iter().map(|&i| items[i].id.clone()).collect(),
        });
        if merged {
            merged_pieces.insert(*section, keep);
        }
    }
    let mut units = Vec::new();
    let mut placed: BTreeSet<&str> = BTreeSet::new();
    for (index, item) in items.iter().enumerate() {
        let merged_section = item.section_of.as_deref().filter(|section| {
            merged_pieces
                .get(section)
                .is_some_and(|m| m.contains(&index))
        });
        match merged_section {
            Some(section) => {
                if placed.insert(section) {
                    let members = &merged_pieces[section];
                    let parts: Vec<Unit> = members.iter().map(|&i| items[i].unit()).collect();
                    let (volume_cm3, mass_g, acts) = totals(&parts);
                    units.push(Unit {
                        id: section.to_string(),
                        volume_cm3,
                        mass_g,
                        acts,
                        pieces: parts.iter().map(|p| p.id.clone()).collect(),
                    });
                }
            }
            None => units.push(item.unit()),
        }
    }
    (units, info)
}

// ---------------------------------------------------------------------------------------------------------------
// the candidate loop (BTP 3.3.2.2, p.26)

struct Candidate {
    json: Value,
    sections: Vec<Value>,
    qualifies: bool,
    /// removed items with the class each has alone
    removed: Vec<(String, WasteClass)>,
}

/// Test every unit at candidate class `x`, remove the failures and classify the remainder on its averaged
/// concentrations. `credit` replaces the remainder's volume and mass with the credited geometry of an encapsulated
/// package.
fn candidate(
    ctx: &Ctx,
    waste_type: WasteType,
    items: &[Item],
    averaged: &Averaged,
    x: usize,
    credit: Option<(f64, f64)>,
) -> Result<Candidate, String> {
    let small_cm3 = ctx.btp.thresholds.small_item_cm3;
    let (units, sections) = build_units(ctx, items, x);
    let mut evaluations = Vec::with_capacity(units.len());
    for unit in &units {
        evaluations.push(ctx.evaluate(waste_type, unit.volume_cm3, unit.mass_g, &unit.acts)?);
    }
    let small: Vec<usize> = (0..units.len())
        .filter(|&i| units[i].volume_cm3 < small_cm3)
        .collect();
    let pool_sum = if small.is_empty() {
        None
    } else {
        let (_, _, pooled) = totals(small.iter().map(|&i| &units[i]));
        Some(table2_sum(ctx.btp, &pooled, x))
    };

    let mut tests = Vec::with_capacity(units.len());
    let mut test_json = Vec::with_capacity(units.len());
    for (i, unit) in units.iter().enumerate() {
        let rows = rows_of(&evaluations[i]);
        let is_small = small.contains(&i);
        let sum = match pool_sum {
            Some(pooled) if is_small => pooled,
            _ => table2_sum(ctx.btp, &unit.acts, x),
        };
        let test = test_unit(ctx, unit, &rows, x, averaged.controlling, sum);
        test_json.push(item_test_json(&test, unit, is_small));
        tests.push(test);
    }

    let removed: Vec<(String, WasteClass)> = units
        .iter()
        .zip(&tests)
        .zip(&evaluations)
        .filter(|((_, test), _)| !test.passes)
        .map(|((unit, _), evaluation)| (unit.id.clone(), evaluation.class))
        .collect();
    let passing: Vec<&Unit> = units
        .iter()
        .zip(&tests)
        .filter(|(_, test)| test.passes)
        .map(|(unit, _)| unit)
        .collect();
    let remainder = if passing.is_empty() {
        None
    } else {
        let (volume, mass, acts) = totals(passing.iter().copied());
        let (volume, mass) = credit.unwrap_or((volume, mass));
        Some((ctx.evaluate(waste_type, volume, mass, &acts)?, volume, mass))
    };
    let qualifies = remainder
        .as_ref()
        .is_none_or(|(evaluation, _, _)| rank(evaluation.class) <= (x as u8));

    let section_values = section_json_for(&sections);
    let small_ids: Vec<&str> = small.iter().map(|&i| units[i].id.as_str()).collect();
    let removed_ids: Vec<&str> = removed.iter().map(|(id, _)| id.as_str()).collect();
    let pool =
        pool_sum.map(|sum| json!({"items": small_ids, "table2_sum": sum, "passes": sum <= 1.0}));
    let json = json!({
        "class": CANDIDATES[x],
        "small_pool": pool,
        "sections": section_values,
        "item_tests": test_json,
        "removed": removed_ids,
        "remainder_class": remainder.as_ref().map(|(evaluation, _, _)| evaluation.class),
        "remainder_volume_cm3": remainder.as_ref().map(|(_, volume, _)| *volume),
        "remainder_mass_g": remainder.as_ref().map(|(_, _, mass)| *mass),
        "qualifies": qualifies,
    });
    Ok(Candidate {
        json,
        sections: section_values,
        qualifies,
        removed,
    })
}

fn section_json_for(sections: &[SectionInfo]) -> Vec<Value> {
    sections.iter().map(SectionInfo::json).collect()
}

fn item_test_json(test: &ItemTest, unit: &Unit, small: bool) -> Value {
    let mut value = json!({
        "id": unit.id,
        "passes": test.passes,
        "small": small,
        "primary_gamma": {
            "limit_met": test.primary.limit_met,
            "table2_sum": test.primary.table2_sum,
            "factor": test.primary.factor,
            "tested": test.primary.tested,
            "failing": test.primary.failing,
        },
        "others": test.others.iter().map(|other| json!({
            "nuclide": other.nuclide,
            "limit_met": other.limit_met,
        })).collect::<Vec<_>>(),
    });
    if !unit.pieces.is_empty() {
        value["pieces"] = json!(unit.pieces);
    }
    value
}

// ---------------------------------------------------------------------------------------------------------------
// one group of one waste type

struct GroupOut {
    json: Value,
    class: WasteClass,
    removed: Vec<(String, WasteClass)>,
    sections: Vec<Value>,
}

/// Classify the items of one waste type (BTP 3.3.2): the screens when selected, else the candidate-class loop.
fn classify_group(
    ctx: &Ctx,
    label: &str,
    waste_type: WasteType,
    items: &[Item],
    screen: Screen,
    credit: Option<(f64, f64)>,
) -> Result<GroupOut, String> {
    let thresholds = &ctx.btp.thresholds;
    let ids: Vec<&str> = items.iter().map(|item| item.id.as_str()).collect();
    let mut unknown = Vec::new();
    for item in items {
        let evaluation = ctx.evaluate(waste_type, item.volume_cm3, item.mass_g, &item.acts)?;
        if evaluation.coverage != Coverage::Complete {
            unknown.push(item.id.clone());
        }
    }
    if !unknown.is_empty() {
        return Ok(GroupOut {
            json: json!({
                "waste_type": label,
                "items": ids,
                "screen_applied": null,
                "candidates": [],
                "mixture_class": WasteClass::Unknown,
                "removed_items": [],
                "unknown_items": unknown,
            }),
            class: WasteClass::Unknown,
            removed: Vec::new(),
            sections: Vec::new(),
        });
    }

    let all_units: Vec<Unit> = items.iter().map(Item::unit).collect();
    let average = averaged(ctx, waste_type, &all_units, credit)?;
    // a screen merges sections at the class of the whole group's average
    let star = usize::from(rank(average.evaluation.class).min(2));
    let screened = match screen {
        Screen::None => None,
        Screen::Activity37Mbq => {
            let (units, sections) = build_units(ctx, items, star);
            units
                .iter()
                .all(|unit| total_activity(&unit.acts) < thresholds.screen_activity_bq)
                .then_some((average.evaluation.class, "activity_37mbq", sections))
        }
        Screen::HighestItem => {
            let (units, sections) = build_units(ctx, items, star);
            let mut class = WasteClass::A;
            for unit in &units {
                let own = ctx.evaluate(waste_type, unit.volume_cm3, unit.mass_g, &unit.acts)?;
                class = worst(class, own.class);
            }
            Some((class, "highest_item", sections))
        }
    };

    type Parts = (
        Option<&'static str>,
        WasteClass,
        Vec<(String, WasteClass)>,
        Vec<Value>,
        Vec<Value>,
    );
    let (screen_applied, mixture, removed, section_values, candidates): Parts = match screened {
        Some((class, name, sections)) => (
            Some(name),
            class,
            Vec::new(),
            section_json_for(&sections),
            Vec::new(),
        ),
        None => {
            let mut first = None;
            let mut results = Vec::with_capacity(CANDIDATES.len());
            for x in 0..CANDIDATES.len() {
                let result = candidate(ctx, waste_type, items, &average, x, credit)?;
                if first.is_none() && result.qualifies {
                    first = Some(x);
                }
                results.push(result);
            }
            // when no class qualifies the group is above Class C; the items removed at Class C are reported
            let pick = first.unwrap_or(CANDIDATES.len() - 1);
            let mixture = match first {
                Some(x) => CANDIDATES[x],
                None => WasteClass::AboveClassC,
            };
            let removed = results[pick].removed.clone();
            let sections = results[pick].sections.clone();
            let candidates: Vec<Value> = results.into_iter().map(|result| result.json).collect();
            (None, mixture, removed, sections, candidates)
        }
    };
    let json = json!({
        "waste_type": label,
        "items": ids,
        "screen_applied": screen_applied,
        "restrictive_table": average.restrictive_table,
        "table1_sum": average.table1_sum,
        "table2_sum": average.table2_sum,
        "primary_gamma_share": average.share,
        "primary_gamma_controlling": average.controlling,
        "averaged_class": average.evaluation.class,
        "candidates": candidates,
        "mixture_class": mixture,
        "removed_items": removed.iter().map(|(id, class)| json!({"id": id, "own_class": class})).collect::<Vec<_>>(),
    });
    Ok(GroupOut {
        json,
        class: mixture,
        removed,
        sections: section_values,
    })
}

// ---------------------------------------------------------------------------------------------------------------
// encapsulation (BTP 3.3.4)

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Credit {
    /// loading at least the minimum and the container no larger than the limit
    Full,
    /// loading under the minimum: at most 0.2 m3 and 500 kg
    LowLoading,
    /// loading at least the minimum in a container above the volume limit
    Alternative,
}

struct Capsule {
    waste_volume_cm3: f64,
    loading: f64,
    credit: Credit,
    credited_volume_cm3: f64,
    credited_mass_g: f64,
    /// encapsulant mass credited, after the density cap
    mass_credit_g: f64,
    /// item mass plus the credited encapsulant, before the 500 kg cap
    total_mass_g: f64,
}

/// The volume and mass the encapsulated package is averaged over.
fn capsule(
    btp: &BtpData,
    enc: &EncapsulationSpec,
    item_volume_cm3: f64,
    item_mass_g: f64,
) -> Capsule {
    let t = &btp.thresholds;
    let waste_volume_cm3 = enc.waste_volume_cm3.unwrap_or(item_volume_cm3);
    let loading = waste_volume_cm3 / enc.total_volume_cm3;
    let density = enc
        .encapsulant_density_g_cm3
        .min(t.encapsulant_max_density_g_cm3);
    let mass_credit_g = enc
        .encapsulant_mass_g
        .min(density * (enc.total_volume_cm3 - waste_volume_cm3));
    let total_mass_g = item_mass_g + mass_credit_g;
    let (credit, credited_volume_cm3, credited_mass_g) = if loading >= t.encapsulation_min_loading {
        if enc.total_volume_cm3 <= t.encapsulation_max_volume_cm3 {
            (Credit::Full, enc.total_volume_cm3, total_mass_g)
        } else {
            (Credit::Alternative, 0.0, 0.0)
        }
    } else {
        (
            Credit::LowLoading,
            enc.total_volume_cm3.min(t.low_loading_max_volume_cm3),
            total_mass_g.min(t.low_loading_max_mass_g),
        )
    };
    Capsule {
        waste_volume_cm3,
        loading,
        credit,
        credited_volume_cm3,
        credited_mass_g,
        mass_credit_g,
        total_mass_g,
    }
}

// ---------------------------------------------------------------------------------------------------------------
// one target step

struct StepClass {
    groups: Vec<Value>,
    sections: Vec<Value>,
    class: WasteClass,
    status: &'static str,
    encapsulation: Value,
}

fn plain_step(ctx: &Ctx, screen: Screen, items: &[Item]) -> Result<StepClass, String> {
    let mut by_type: BTreeMap<BtpType, Vec<Item>> = BTreeMap::new();
    for item in items {
        by_type.entry(item.btp_type).or_default().push(item.clone());
    }
    let mut class = WasteClass::A;
    let mut groups = Vec::new();
    let mut sections = Vec::new();
    for (btp_type, group_items) in &by_type {
        let group = classify_group(
            ctx,
            btp_type.name(),
            btp_type.rules(),
            group_items,
            screen,
            None,
        )?;
        class = worst(class, group.class);
        for (_, own) in &group.removed {
            class = worst(class, *own);
        }
        sections.extend(group.sections);
        groups.push(group.json);
    }
    Ok(StepClass {
        groups,
        sections,
        class,
        status: if class == WasteClass::Unknown {
            "unknown"
        } else {
            "classified"
        },
        encapsulation: Value::Null,
    })
}

fn encapsulated_step(
    ctx: &Ctx,
    enc: &EncapsulationSpec,
    items: &[Item],
) -> Result<StepClass, String> {
    // One waste form. When its items carry both rule-row sets (activated metal and general), it is evaluated under
    // each set and the higher class is kept, so neither set's limits are escaped by mixing.
    let mut rule_sets: Vec<WasteType> = Vec::new();
    for waste_type in [WasteType::General, WasteType::ActivatedMetal] {
        if items.iter().any(|item| item.btp_type.rules() == waste_type) {
            rule_sets.push(waste_type);
        }
    }
    let units: Vec<Unit> = items.iter().map(Item::unit).collect();
    let (item_volume, item_mass, all_acts) = totals(&units);
    let cap = capsule(ctx.btp, enc, item_volume, item_mass);
    // the package average over the waste and the whole encapsulant must not exceed Class C
    let mut cap_class = WasteClass::A;
    let mut unknown = false;
    for &waste_type in &rule_sets {
        let cap_evaluation = ctx.evaluate(
            waste_type,
            enc.total_volume_cm3,
            cap.total_mass_g,
            &all_acts,
        )?;
        unknown |= cap_evaluation.coverage != Coverage::Complete;
        cap_class = worst(cap_class, cap_evaluation.class);
    }
    let cap_passes = !unknown && cap_class != WasteClass::AboveClassC;

    let mut group_outs = Vec::new();
    if cap.credit != Credit::Alternative {
        for &waste_type in &rule_sets {
            let label = match (rule_sets.len(), waste_type) {
                (1, _) => "encapsulated",
                (_, WasteType::ActivatedMetal) => "encapsulated_activated_metal_rules",
                _ => "encapsulated_general_rules",
            };
            group_outs.push(classify_group(
                ctx,
                label,
                waste_type,
                items,
                Screen::None,
                Some((cap.credited_volume_cm3, cap.credited_mass_g)),
            )?);
        }
    }
    let (class, status, block_class) = if unknown {
        (WasteClass::Unknown, "unknown", json!(WasteClass::Unknown))
    } else if cap.credit == Credit::Alternative {
        (
            WasteClass::Unknown,
            "alternative_approach_required",
            Value::Null,
        )
    } else if !cap_passes {
        (
            WasteClass::AboveClassC,
            "exceeds_class_c_cap",
            json!(WasteClass::AboveClassC),
        )
    } else {
        let mut class = WasteClass::A;
        let mut group_class = WasteClass::A;
        for group in &group_outs {
            group_class = worst(group_class, group.class);
            class = worst(class, group.class);
            for (_, own) in &group.removed {
                class = worst(class, *own);
            }
        }
        (class, "classified", json!(group_class))
    };
    let block_status = if cap.credit == Credit::Alternative {
        "alternative_approach_required"
    } else if status == "exceeds_class_c_cap" {
        "exceeds_class_c_cap"
    } else if cap.credit == Credit::LowLoading {
        "low_loading"
    } else {
        "credited"
    };
    let credited = cap.credit != Credit::Alternative;
    let encapsulation = json!({
        "waste_volume_cm3": cap.waste_volume_cm3,
        "loading": cap.loading,
        "total_volume_cm3": enc.total_volume_cm3,
        "credited_volume_cm3": credited.then_some(cap.credited_volume_cm3),
        "credited_mass_g": credited.then_some(cap.credited_mass_g),
        "encapsulant_mass_credit_g": cap.mass_credit_g,
        "status": block_status,
        "class": block_class,
        "class_c_cap": {"class": cap_class, "passes": cap_passes},
    });
    let mut groups = Vec::new();
    let mut sections = Vec::new();
    for (index, group) in group_outs.into_iter().enumerate() {
        groups.push(group.json);
        if index == 0 {
            sections = group.sections;
        }
    }
    Ok(StepClass {
        groups,
        sections,
        class,
        status,
        encapsulation,
    })
}

fn classify_step(
    ctx: &Ctx,
    loaded: &Loaded,
    step: u64,
    t_s: Option<f64>,
    items: &[Item],
) -> Result<Value, String> {
    let small_cm3 = ctx.btp.thresholds.small_item_cm3;
    let mut item_rows = Vec::with_capacity(items.len());
    for item in items {
        let own = ctx.evaluate(
            item.btp_type.rules(),
            item.volume_cm3,
            item.mass_g,
            &item.acts,
        )?;
        item_rows.push(json!({
            "id": item.id,
            "waste_type": item.btp_type.name(),
            "volume_cm3": item.volume_cm3,
            "mass_g": item.mass_g,
            "small": item.volume_cm3 < small_cm3,
            "total_activity_bq": total_activity(&item.acts),
            "own_class": own.class,
            "activities_bq": item.acts,
            "section_of": item.section_of,
        }));
    }
    let outcome = match &loaded.encapsulation {
        Some(enc) => encapsulated_step(ctx, enc, items)?,
        None => plain_step(ctx, loaded.screen, items)?,
    };
    Ok(json!({
        "step": step,
        "t_s": t_s,
        "package_class": outcome.class,
        "status": outcome.status,
        "items": item_rows,
        "sections": outcome.sections,
        "groups": outcome.groups,
        "encapsulation": outcome.encapsulation,
    }))
}

// ---------------------------------------------------------------------------------------------------------------
// loading

/// An item with its activity at every target step.
struct ItemBase {
    id: String,
    btp_type: BtpType,
    volume_cm3: f64,
    mass_g: f64,
    section_of: Option<String>,
    acts: BTreeMap<u64, BTreeMap<String, f64>>,
}

struct Loaded {
    spec_sha256: String,
    rules_name: String,
    rules: RulePack,
    btp: BtpData,
    targets: Vec<u64>,
    properties: BTreeMap<String, NuclideProperties>,
    screen: Screen,
    encapsulation: Option<EncapsulationSpec>,
    items: Vec<ItemBase>,
    t_s: BTreeMap<u64, Option<f64>>,
    inputs: Vec<Value>,
}

fn sha256(bytes: &[u8]) -> String {
    Sha256::digest(bytes)
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

fn positive(value: Option<f64>, id: &str, field: &str) -> Result<f64, String> {
    match value {
        None => Err(format!("item {id}: {field} is missing")),
        Some(v) if v.is_finite() && v > 0.0 => Ok(v),
        Some(_) => Err(format!("item {id}: {field} must be finite and positive")),
    }
}

fn selected_btp(btp: Option<&str>, base: &Path) -> Result<BtpData, String> {
    match btp {
        None => BtpData::bundled(),
        Some(id) if id == BUNDLED_BTP_ID => BtpData::bundled(),
        Some(path) => {
            let file = base.join(path);
            let text = std::fs::read_to_string(&file)
                .map_err(|error| format!("cannot read BTP data {}: {error}", file.display()))?;
            BtpData::parse(&text)
        }
    }
}

/// Canonical nuclide keys, finite nonnegative activities, no duplicates after normalization.
fn normalize_activities(
    id: &str,
    step: u64,
    raw: impl IntoIterator<Item = (String, f64)>,
) -> Result<BTreeMap<String, f64>, String> {
    let mut acts = BTreeMap::new();
    for (name, bq) in raw {
        if !bq.is_finite() || bq < 0.0 {
            return Err(format!(
                "item {id}: activity of {name} at step {step} must be finite and nonnegative"
            ));
        }
        let key = normalize_nuclide_key(&name).map_err(|error| format!("item {id}: {error}"))?;
        if acts.insert(key.clone(), bq).is_some() {
            return Err(format!(
                "item {id}: duplicate activity keys after identity normalization: '{key}'"
            ));
        }
    }
    Ok(acts)
}

fn load(text: &str, base: &Path) -> Result<Loaded, String> {
    let spec: PackageSpec = serde_json::from_str(text)
        .map_err(|error| format!("cannot parse waste package spec: {error}"))?;
    if spec.schema != SPEC_SCHEMA {
        return Err(format!(
            "unsupported waste package schema '{}'",
            spec.schema
        ));
    }
    if spec.targets.is_empty() {
        return Err("targets must name at least one result step".into());
    }
    waste::validate_targets(&spec.targets)?;
    let screen = Screen::parse(spec.screen.as_deref())?;
    if spec.items.is_empty() {
        return Err("items must contain at least one item".into());
    }

    // items: ids, types, geometry
    let mut ids = BTreeSet::new();
    let mut geometry = Vec::with_capacity(spec.items.len());
    for item in &spec.items {
        if item.id.trim().is_empty() {
            return Err("item id must not be empty".into());
        }
        if !ids.insert(item.id.as_str()) {
            return Err(format!("duplicate item id '{}'", item.id));
        }
        let btp_type = BtpType::parse(&item.waste_type).ok_or_else(|| {
            format!(
                "item {}: unknown waste_type '{}' (expected activated_metal, contaminated_material, design_radioactivity_component, sealed_source or cartridge_filter)",
                item.id, item.waste_type
            )
        })?;
        let mass = positive(item.mass_g, &item.id, "mass_g")?;
        let volume = if btp_type == BtpType::CartridgeFilter {
            if item.displaced_volume_cm3.is_some() {
                return Err(format!(
                    "item {}: a cartridge_filter takes envelope_volume_cm3 (BTP Table 4), not displaced_volume_cm3",
                    item.id
                ));
            }
            positive(item.envelope_volume_cm3, &item.id, "envelope_volume_cm3")?
        } else {
            if item.envelope_volume_cm3.is_some() {
                return Err(format!(
                    "item {}: only a cartridge_filter takes envelope_volume_cm3; use displaced_volume_cm3",
                    item.id
                ));
            }
            positive(item.displaced_volume_cm3, &item.id, "displaced_volume_cm3")?
        };
        geometry.push((btp_type, volume, mass));
    }

    // sectioned components
    let mut sections: BTreeMap<&str, Vec<(&str, BtpType)>> = BTreeMap::new();
    for (item, (btp_type, _, _)) in spec.items.iter().zip(&geometry) {
        if let Some(section) = &item.section_of {
            if section.trim().is_empty() {
                return Err(format!("item {}: section_of must not be empty", item.id));
            }
            sections
                .entry(section.as_str())
                .or_default()
                .push((item.id.as_str(), *btp_type));
        }
    }
    for (section, pieces) in &sections {
        if let Some((other, _)) = pieces.iter().find(|(_, t)| *t != pieces[0].1) {
            return Err(format!(
                "section_of '{section}': pieces '{}' and '{other}' have different waste types",
                pieces[0].0
            ));
        }
        if pieces.len() < 2 {
            return Err(format!(
                "section_of '{section}' has only one piece in the package: all pieces of a sectioned component must be in the same package (BTP 3.3.2.3)"
            ));
        }
    }

    // encapsulation
    let encapsulation = spec.encapsulation;
    match &encapsulation {
        Some(enc) => {
            for (name, value) in [
                ("total_volume_cm3", enc.total_volume_cm3),
                ("encapsulant_mass_g", enc.encapsulant_mass_g),
                ("encapsulant_density_g_cm3", enc.encapsulant_density_g_cm3),
            ] {
                if !(value.is_finite() && value > 0.0) {
                    return Err(format!("encapsulation {name} must be finite and positive"));
                }
            }
            if let Some(value) = enc.waste_volume_cm3 {
                if !(value.is_finite() && value > 0.0) {
                    return Err("encapsulation waste_volume_cm3 must be finite and positive".into());
                }
            }
            let encapsulated = spec
                .items
                .iter()
                .filter(|item| item.encapsulated.unwrap_or(true))
                .count();
            if encapsulated == 0 {
                return Err(
                    "the spec has an encapsulation block but no item is encapsulated".into(),
                );
            }
            if encapsulated != spec.items.len() {
                return Err("encapsulated and unencapsulated items cannot share a package: the encapsulation block applies to every item".into());
            }
            let item_volume: f64 = geometry.iter().map(|(_, volume, _)| volume).sum();
            let waste_volume = enc.waste_volume_cm3.unwrap_or(item_volume);
            if waste_volume > enc.total_volume_cm3 {
                return Err(format!(
                    "encapsulation waste volume {waste_volume} cm3 exceeds the total encapsulated volume {} cm3 (waste loading above 1)",
                    enc.total_volume_cm3
                ));
            }
            if screen != Screen::None {
                return Err(format!(
                    "screen '{}' is not available with encapsulation (BTP Figure 5 has no screening step)",
                    screen.name()
                ));
            }
        }
        None => {
            if let Some(item) = spec
                .items
                .iter()
                .find(|item| item.encapsulated == Some(true))
            {
                return Err(format!(
                    "item {} is marked encapsulated but the spec has no encapsulation block",
                    item.id
                ));
            }
        }
    }

    let rules = waste::load_rule_pack(&spec.rules, base)?;
    let btp = selected_btp(spec.btp.as_deref(), base)?;

    // activities
    let mut result_cache: BTreeMap<String, InputResults> = BTreeMap::new();
    let mut t_s: BTreeMap<u64, Option<f64>> = spec.targets.iter().map(|&s| (s, None)).collect();
    let mut inputs = Vec::new();
    let mut items = Vec::with_capacity(spec.items.len());
    for (item, (btp_type, volume_cm3, mass_g)) in spec.items.into_iter().zip(geometry) {
        let ActivitySpec {
            result,
            cells,
            declared_bq,
        } = item.activity;
        let mut per_step: BTreeMap<u64, (Option<f64>, BTreeMap<String, f64>)> = BTreeMap::new();
        match (result, declared_bq) {
            (Some(_), Some(_)) => {
                return Err(format!(
                    "item {}: activity declares both result and declared_bq",
                    item.id
                ))
            }
            (None, None) => {
                return Err(format!(
                    "item {}: activity needs result or declared_bq",
                    item.id
                ))
            }
            (Some(path), None) => {
                if !result_cache.contains_key(&path) {
                    let file = base.join(&path);
                    let bytes = std::fs::read(&file).map_err(|error| {
                        format!(
                            "item {}: cannot read result {}: {error}",
                            item.id,
                            file.display()
                        )
                    })?;
                    let input = waste::load_input_bytes(&bytes)
                        .map_err(|error| format!("item {}: {error}", item.id))?;
                    result_cache.insert(path.clone(), input);
                }
                let input = &result_cache[&path];
                let component = ComponentSpec {
                    id: item.id.clone(),
                    mass_g,
                    displaced_volume_cm3: Some(volume_cm3),
                    density_g_cm3: None,
                    waste_type: btp_type.rules(),
                    cells,
                    external_tritium: ExternalTritium::NotApplicable,
                };
                let found = waste::activity_for_component(input, &component, &spec.targets)?;
                for (step, (time, activities)) in found {
                    let acts = normalize_activities(&item.id, step, activities)?;
                    per_step.insert(step, (Some(time), acts));
                }
                inputs.push(json!({
                    "item": item.id,
                    "result": path,
                    "kind": input.kind,
                    "sha256": input.source_sha256,
                }));
            }
            (None, Some(declared)) => {
                if !cells.is_empty() {
                    return Err(format!(
                        "item {}: cells apply only to a mesh result, not to declared_bq",
                        item.id
                    ));
                }
                for (key, activities) in declared {
                    let step: u64 = key.parse().map_err(|_| {
                        format!(
                            "item {}: declared_bq step '{key}' is not a step number",
                            item.id
                        )
                    })?;
                    if !spec.targets.contains(&step) {
                        return Err(format!(
                            "item {}: declared_bq step {step} is not a target step",
                            item.id
                        ));
                    }
                    let acts = normalize_activities(&item.id, step, activities)?;
                    if per_step.insert(step, (None, acts)).is_some() {
                        return Err(format!("item {}: declared_bq repeats step {step}", item.id));
                    }
                }
                for step in &spec.targets {
                    if !per_step.contains_key(step) {
                        return Err(format!(
                            "item {}: declared_bq has no activity for target step {step}",
                            item.id
                        ));
                    }
                }
            }
        }
        let mut acts = BTreeMap::new();
        for (step, (time, activities)) in per_step {
            if let Some(time) = time {
                let previous = t_s.get(&step).copied().flatten();
                if let Some(previous) = previous.filter(|previous| *previous != time) {
                    return Err(format!(
                        "item {}: result timestamp {time} s at step {step} differs from {previous} s of another item",
                        item.id
                    ));
                }
                t_s.insert(step, Some(time));
            }
            acts.insert(step, activities);
        }
        items.push(ItemBase {
            id: item.id,
            btp_type,
            volume_cm3,
            mass_g,
            section_of: item.section_of,
            acts,
        });
    }

    Ok(Loaded {
        spec_sha256: sha256(text.as_bytes()),
        rules_name: spec.rules,
        rules,
        btp,
        targets: spec.targets,
        properties: spec.nuclide_properties,
        screen,
        encapsulation,
        items,
        t_s,
        inputs,
    })
}

fn evaluate(loaded: &Loaded) -> Result<Value, String> {
    let ctx = Ctx::new(&loaded.rules, &loaded.btp, &loaded.properties)?;
    let mut steps = Vec::with_capacity(loaded.targets.len());
    for &step in &loaded.targets {
        let items: Vec<Item> = loaded
            .items
            .iter()
            .map(|base| Item {
                id: base.id.clone(),
                btp_type: base.btp_type,
                volume_cm3: base.volume_cm3,
                mass_g: base.mass_g,
                acts: base.acts[&step].clone(),
                section_of: base.section_of.clone(),
            })
            .collect();
        steps.push(classify_step(
            &ctx,
            loaded,
            step,
            loaded.t_s[&step],
            &items,
        )?);
    }
    Ok(json!({
        "schema": RESULT_SCHEMA,
        "spec_sha256": loaded.spec_sha256,
        "rules": loaded.rules_name,
        "rule_pack_sha256": loaded.rules.sha256,
        "btp": loaded.btp.id,
        "btp_sha256": loaded.btp.sha256,
        "targets": loaded.targets,
        "screen": loaded.screen.name(),
        "encapsulated": loaded.encapsulation.is_some(),
        "inputs": loaded.inputs,
        "steps": steps,
        "readings": READINGS,
        "choices": CHOICES,
        "not_claimed": NOT_CLAIMED,
    }))
}

pub fn run(path: &str, output: Option<&str>) -> Result<Value, String> {
    let source = Path::new(path);
    let base = source.parent().unwrap_or_else(|| Path::new("."));
    let text = std::fs::read_to_string(source)
        .map_err(|error| format!("cannot read waste package spec {path}: {error}"))?;
    let result = run_doc(&text, base)?;
    waste::output_doc(&result, output)?;
    Ok(result)
}

pub(crate) fn run_doc(text: &str, base: &Path) -> Result<Value, String> {
    evaluate(&load(text, base)?)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;
    use std::sync::atomic::{AtomicUsize, Ordering};

    const YEAR_S: f64 = 31_557_600.0;
    const CI_BQ: f64 = 37_000_000_000.0;

    struct Scratch(PathBuf);

    impl Scratch {
        fn new() -> Self {
            static NEXT: AtomicUsize = AtomicUsize::new(0);
            let path = std::env::temp_dir().join(format!(
                "actinv-waste-package-{}-{}",
                std::process::id(),
                NEXT.fetch_add(1, Ordering::Relaxed)
            ));
            std::fs::create_dir_all(&path).unwrap();
            Self(path)
        }
    }

    impl Drop for Scratch {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }

    struct Fixture {
        rules: RulePack,
        btp: BtpData,
        properties: BTreeMap<String, NuclideProperties>,
    }

    impl Fixture {
        fn new() -> Self {
            let entries: [(&str, i32, f64, bool); 11] = [
                ("Co60", 27, 5.27, false),
                ("Nb94", 41, 2.03e4, false),
                ("Cs137", 55, 30.17, false),
                ("Sr90", 38, 28.8, false),
                ("H3", 1, 12.32, false),
                ("C14", 6, 5700.0, false),
                ("Ni59", 28, 7.6e4, false),
                ("Ni63", 28, 100.1, false),
                ("Am241", 95, 432.6, true),
                ("Pu239", 94, 24110.0, true),
                ("Pu241", 94, 14.3, false),
            ];
            let properties = entries
                .iter()
                .map(|(name, z, years, alpha)| {
                    (
                        (*name).to_string(),
                        NuclideProperties {
                            z: *z,
                            half_life_s: years * YEAR_S,
                            alpha_emitting: *alpha,
                        },
                    )
                })
                .collect();
            Self {
                rules: RulePack::bundled().unwrap(),
                btp: BtpData::bundled().unwrap(),
                properties,
            }
        }

        fn ctx(&self) -> Ctx<'_> {
            Ctx::new(&self.rules, &self.btp, &self.properties).unwrap()
        }
    }

    /// Bq in `volume_cm3` that make `ci_per_m3`.
    fn bq(ci_per_m3: f64, volume_cm3: f64) -> f64 {
        ci_per_m3 * CI_BQ * volume_cm3 * 1.0e-6
    }

    fn item(id: &str, btp_type: BtpType, volume_cm3: f64, acts: &[(&str, f64)]) -> Item {
        Item {
            id: id.to_string(),
            btp_type,
            volume_cm3,
            mass_g: volume_cm3 * 7.8,
            acts: acts.iter().map(|(n, v)| ((*n).to_string(), *v)).collect(),
            section_of: None,
        }
    }

    fn piece(id: &str, section: &str, volume_cm3: f64, acts: &[(&str, f64)]) -> Item {
        Item {
            section_of: Some(section.to_string()),
            ..item(id, BtpType::ActivatedMetal, volume_cm3, acts)
        }
    }

    /// Test one item alone at class index `x`.
    fn test_alone(fx: &Fixture, unit: &Item, x: usize, controlling: bool) -> ItemTest {
        let ctx = fx.ctx();
        let evaluation = ctx
            .evaluate(
                unit.btp_type.rules(),
                unit.volume_cm3,
                unit.mass_g,
                &unit.acts,
            )
            .unwrap();
        let rows = rows_of(&evaluation);
        // an item alone is its own pool
        let sum = table2_sum(ctx.btp, &unit.acts, x);
        test_unit(&ctx, &unit.unit(), &rows, x, controlling, sum)
    }

    fn group(fx: &Fixture, items: &[Item], screen: Screen) -> GroupOut {
        classify_group(
            &fx.ctx(),
            "activated_metal",
            WasteType::ActivatedMetal,
            items,
            screen,
            None,
        )
        .unwrap()
    }

    // ---- BTP examples 1 and 2 -------------------------------------------------------------------------------

    #[test]
    fn example_1_nb94_activity_limit_of_37_mbq_and_factor_2_concentration() {
        let fx = Fixture::new();
        // items of 0.1 m3, over 280 cc, in a Class A mixture (candidate index 0), Nb-94 controlling (factor 2)
        let at_limit = item("a", BtpType::ActivatedMetal, 1.0e5, &[("Nb94", 3.7e7)]);
        let test = test_alone(&fx, &at_limit, 0, true);
        assert!(test.passes);
        assert_eq!(test.primary.limit_met.as_deref(), Some("table2"));
        assert_eq!(test.primary.table2_sum, 1.0);

        // just over 37 MBq: BTP Table 2 fails, the concentration (0.01 Ci/m3) is under 2 x 0.02 Ci/m3
        let over = item(
            "b",
            BtpType::ActivatedMetal,
            1.0e5,
            &[("Nb94", 3.7e7 + 1.0)],
        );
        let test = test_alone(&fx, &over, 0, true);
        assert!(test.passes);
        assert_eq!(test.primary.limit_met.as_deref(), Some("factor2"));
        assert!(test.primary.table2_sum > 1.0);

        // 1 m3 items: the 0.04 Ci/m3 boundary (two times the Class A limit of 0.02 Ci/m3)
        let below = item(
            "c",
            BtpType::ActivatedMetal,
            1.0e6,
            &[("Nb94", bq(0.0399, 1.0e6))],
        );
        let test = test_alone(&fx, &below, 0, true);
        assert!(test.passes);
        assert_eq!(test.primary.limit_met.as_deref(), Some("factor2"));
        let above = item(
            "d",
            BtpType::ActivatedMetal,
            1.0e6,
            &[("Nb94", bq(0.0401, 1.0e6))],
        );
        let test = test_alone(&fx, &above, 0, true);
        assert!(!test.passes);
        assert_eq!(test.primary.limit_met, None);
        assert_eq!(test.primary.failing, ["Nb94"]);
    }

    #[test]
    fn example_2_nb94_factor_10_concentration_when_not_controlling() {
        let fx = Fixture::new();
        // ten times the Class A limit: 0.2 Ci/m3
        let below = item(
            "a",
            BtpType::ActivatedMetal,
            1.0e6,
            &[("Nb94", bq(0.199, 1.0e6))],
        );
        let test = test_alone(&fx, &below, 0, false);
        assert!(test.passes);
        assert_eq!(test.primary.limit_met.as_deref(), Some("factor10"));
        assert_eq!(test.primary.factor, 10.0);
        let above = item(
            "b",
            BtpType::ActivatedMetal,
            1.0e6,
            &[("Nb94", bq(0.201, 1.0e6))],
        );
        assert!(!test_alone(&fx, &above, 0, false).passes);
        // the same concentration fails when Nb-94 is controlling
        assert!(!test_alone(&fx, &below, 0, true).passes);
        // an activity of 37 MBq or less always meets BTP Table 2, however concentrated
        let dense = item("c", BtpType::ActivatedMetal, 300.0, &[("Nb94", 3.7e7)]);
        let test = test_alone(&fx, &dense, 0, false);
        assert_eq!(test.primary.limit_met.as_deref(), Some("table2"));
    }

    #[test]
    fn class_limit_used_by_the_factors_follows_the_candidate_class() {
        let fx = Fixture::new();
        // Nb-94 at 0.3 Ci/m3: over 2 x 0.02 and 2 x 0.02 (Class A, B), under 2 x 0.2 (Class C)
        let hot = item(
            "hot",
            BtpType::ActivatedMetal,
            1.0e6,
            &[("Nb94", bq(0.3, 1.0e6))],
        );
        assert!(!test_alone(&fx, &hot, 0, true).passes);
        assert!(!test_alone(&fx, &hot, 1, true).passes);
        assert!(test_alone(&fx, &hot, 2, true).passes);
        // 0.401 Ci/m3 is over 2 x 0.2 Ci/m3
        let hotter = item(
            "hot",
            BtpType::ActivatedMetal,
            1.0e6,
            &[("Nb94", bq(0.401, 1.0e6))],
        );
        assert!(!test_alone(&fx, &hotter, 2, true).passes);
    }

    #[test]
    fn primary_gamma_below_the_concern_level_or_without_a_limit_is_not_factor_tested() {
        let fx = Fixture::new();
        // Co-60 has no CFR Table 2 limit in Class B and C: the factor test does not apply, and BTP Table 2 has no
        // limit either, so the sum of fractions is 0
        let co = item("co", BtpType::SealedSource, 1.0e5, &[("Co60", 1.0e14)]);
        let test = test_alone(&fx, &co, 1, true);
        assert!(test.passes);
        assert_eq!(test.primary.limit_met.as_deref(), Some("table2"));
        // a primary gamma at 1 % of the class limit or less is not of concern; above it is
        let tiny = item(
            "t",
            BtpType::ActivatedMetal,
            1.0e6,
            &[("Nb94", bq(0.0002, 1.0e6) * 0.99)],
        );
        assert!(test_alone(&fx, &tiny, 0, true).primary.tested.is_empty());
        let concerned = item(
            "c",
            BtpType::ActivatedMetal,
            1.0e6,
            &[("Nb94", bq(0.0002, 1.0e6) * 1.01)],
        );
        assert_eq!(
            test_alone(&fx, &concerned, 0, true).primary.tested,
            ["Nb94"]
        );
    }

    // ---- BTP Table 3 and the other nuclides -------------------------------------------------------------------

    #[test]
    fn other_nuclides_meet_table_3_or_factor_10() {
        let fx = Fixture::new();
        // Ni-63 (activated metal, CFR Table 2 Class A 35 Ci/m3): 1 m3, BTP Table 3 Class A is 2.6e11 Bq
        let within = item("a", BtpType::ActivatedMetal, 1.0e6, &[("Ni63", 2.6e11)]);
        let test = test_alone(&fx, &within, 0, false);
        assert!(test.passes);
        assert_eq!(test.others.len(), 1);
        assert_eq!(test.others[0].nuclide, "Ni63");
        assert_eq!(test.others[0].limit_met.as_deref(), Some("table3"));
        // over Table 3 but under 10 x 35 Ci/m3 (1.0e13 Bq in 1 m3 is 270 Ci/m3)
        let factor = item("b", BtpType::ActivatedMetal, 1.0e6, &[("Ni63", 1.0e13)]);
        let test = test_alone(&fx, &factor, 0, false);
        assert_eq!(test.others[0].limit_met.as_deref(), Some("factor10"));
        // over both
        let both = item(
            "c",
            BtpType::ActivatedMetal,
            1.0e6,
            &[("Ni63", bq(351.0, 1.0e6))],
        );
        let test = test_alone(&fx, &both, 0, false);
        assert!(!test.passes);
        assert_eq!(test.others[0].limit_met, None);
        // not in Table 3 (Sr-90, 0.04 Ci/m3 in Class A): the factor of 10 only
        let sr = item(
            "d",
            BtpType::SealedSource,
            1.0e6,
            &[("Sr90", bq(0.39, 1.0e6))],
        );
        let test = test_alone(&fx, &sr, 0, false);
        assert_eq!(test.others[0].limit_met.as_deref(), Some("factor10"));
        let sr_over = item(
            "e",
            BtpType::SealedSource,
            1.0e6,
            &[("Sr90", bq(0.41, 1.0e6))],
        );
        assert!(!test_alone(&fx, &sr_over, 0, false).passes);
    }

    #[test]
    fn transuranics_are_one_table_3_entry_per_item() {
        let fx = Fixture::new();
        // Am-241 and Pu-239 sum to 1.2e8 Bq, over the Class A/B limit of 1.11e8 Bq, in a 1 kg item
        let mut over = item(
            "a",
            BtpType::SealedSource,
            128.0,
            &[("Am241", 6.0e7), ("Pu239", 6.0e7)],
        );
        over.mass_g = 1000.0;
        let test = test_alone(&fx, &over, 0, false);
        // 1.2e8 Bq is 3.2 mCi in 1000 g: 3243 nCi/g, over 10 x (100 nCi/g x 0.1)
        assert_eq!(test.others.len(), 1);
        assert_eq!(test.others[0].nuclide, ALPHA_TRU_KEY);
        // each nuclide alone is under 1.11e8 Bq
        let mut within = item("b", BtpType::SealedSource, 128.0, &[("Am241", 1.0e8)]);
        within.mass_g = 1000.0;
        let test = test_alone(&fx, &within, 0, false);
        assert_eq!(test.others[0].limit_met.as_deref(), Some("table3"));
        // the summed activity is over Table 3, and the summed concentration is over the factor of 10
        assert_eq!(test_alone(&fx, &over, 0, false).others[0].limit_met, None);
        // Pu-241 is not a member of the transuranic row: the factor of 10 only
        let mut pu241 = item("c", BtpType::SealedSource, 128.0, &[("Pu241", 1.0e6)]);
        pu241.mass_g = 1000.0;
        let test = test_alone(&fx, &pu241, 0, false);
        assert_eq!(test.others.len(), 1);
        assert_eq!(test.others[0].nuclide, "Pu241");
        assert_eq!(test.others[0].limit_met.as_deref(), Some("factor10"));
    }

    // ---- controlling share (BTP footnote 36) ------------------------------------------------------------------

    fn general_unit(acts: &[(&str, f64)]) -> Vec<Unit> {
        vec![item("u", BtpType::SealedSource, 1.0e6, acts).unit()]
    }

    #[test]
    fn footnote_36_two_of_three_tenths_control() {
        let fx = Fixture::new();
        // CFR Table 2 Class A in 1 m3: Co-60 0.1, Cs-137 0.1, Sr-90 0.1 -> sum 0.3, primary gammas 0.2
        let units = general_unit(&[
            ("Co60", bq(70.0, 1.0e6)),
            ("Cs137", bq(0.1, 1.0e6)),
            ("Sr90", bq(0.004, 1.0e6)),
        ]);
        let average = averaged(&fx.ctx(), WasteType::General, &units, None).unwrap();
        assert_eq!(average.restrictive_table, 2);
        assert!((average.table2_sum - 0.3).abs() < 1e-12);
        assert!((average.share - 2.0 / 3.0).abs() < 1e-12);
        assert!(average.controlling);
        // with Sr-90 at 0.3 the primary gammas are 0.2 of 0.5 (40 %)
        let units = general_unit(&[
            ("Co60", bq(70.0, 1.0e6)),
            ("Cs137", bq(0.1, 1.0e6)),
            ("Sr90", bq(0.012, 1.0e6)),
        ]);
        let average = averaged(&fx.ctx(), WasteType::General, &units, None).unwrap();
        assert!((average.share - 0.4).abs() < 1e-12);
        assert!(!average.controlling);
    }

    #[test]
    fn the_more_restrictive_cfr_table_is_the_one_giving_the_higher_class() {
        let fx = Fixture::new();
        // Table 1: Tc-99 at 2 Ci/m3 is 0.667 of 3 (Class C by Table 1); Table 2: Cs-137 at 0.1 Ci/m3 is Class A
        let units = general_unit(&[("Cs137", bq(0.1, 1.0e6))]);
        let mut acts = units[0].acts.clone();
        acts.insert("Tc99".into(), bq(2.0, 1.0e6));
        let mut props = fx.properties.clone();
        props.insert(
            "Tc99".into(),
            NuclideProperties {
                z: 43,
                half_life_s: 2.1e5 * YEAR_S,
                alpha_emitting: false,
            },
        );
        let ctx = Ctx::new(&fx.rules, &fx.btp, &props).unwrap();
        let unit = Unit {
            acts,
            ..units[0].clone()
        };
        let average = averaged(&ctx, WasteType::General, &[unit], None).unwrap();
        assert_eq!(average.restrictive_table, 1);
        // Cs-137 (table 2 only) does not contribute to Table 1
        assert_eq!(average.share, 0.0);
        assert!(!average.controlling);
    }

    // ---- small-item pooling -----------------------------------------------------------------------------------

    fn filler() -> Item {
        item("filler", BtpType::ActivatedMetal, 1.0e7, &[])
    }

    #[test]
    fn small_items_are_pooled_for_table_2() {
        let fx = Fixture::new();
        let small = |id: &str| item(id, BtpType::ActivatedMetal, 100.0, &[("Nb94", 1.5e7)]);
        // two small items: 3.0e7 Bq in the pool, 0.81 of the Class A value
        let out = group(&fx, &[filler(), small("s1"), small("s2")], Screen::None);
        let candidate_a = &out.json["candidates"][0];
        assert_eq!(candidate_a["small_pool"]["items"], json!(["s1", "s2"]));
        assert!(
            (candidate_a["small_pool"]["table2_sum"].as_f64().unwrap() - 3.0e7 / 3.7e7).abs()
                < 1e-12
        );
        assert_eq!(candidate_a["small_pool"]["passes"], true);
        assert_eq!(
            candidate_a["item_tests"][1]["primary_gamma"]["limit_met"],
            "table2"
        );
        assert_eq!(out.json["mixture_class"], "A");
        assert!(out.removed.is_empty());

        // three: 4.5e7 Bq pooled is over 37 MBq, and each item (4 Ci/m3) is over the factor, so all are removed
        let out = group(
            &fx,
            &[filler(), small("s1"), small("s2"), small("s3")],
            Screen::None,
        );
        let candidate_a = &out.json["candidates"][0];
        assert_eq!(candidate_a["small_pool"]["passes"], false);
        assert_eq!(candidate_a["removed"], json!(["s1", "s2", "s3"]));
        assert_eq!(
            candidate_a["item_tests"][1]["primary_gamma"]["limit_met"],
            Value::Null
        );
        assert_eq!(out.removed.len(), 3);
    }

    #[test]
    fn an_item_of_exactly_280_cm3_is_individual() {
        let fx = Fixture::new();
        let at = item("at", BtpType::ActivatedMetal, 280.0, &[("Nb94", 3.0e7)]);
        let below = item(
            "below",
            BtpType::ActivatedMetal,
            279.999,
            &[("Nb94", 3.0e7)],
        );
        let out = group(&fx, &[filler(), at, below], Screen::None);
        let candidate_a = &out.json["candidates"][0];
        assert_eq!(candidate_a["small_pool"]["items"], json!(["below"]));
        assert_eq!(candidate_a["item_tests"][1]["small"], false);
        assert_eq!(candidate_a["item_tests"][2]["small"], true);
    }

    // ---- removal of failing items -----------------------------------------------------------------------------

    #[test]
    fn failing_items_are_removed_and_classified_alone() {
        let fx = Fixture::new();
        let hot = item(
            "hot",
            BtpType::ActivatedMetal,
            1.0e6,
            &[("Nb94", bq(0.5, 1.0e6))],
        );
        let items = [item("filler", BtpType::ActivatedMetal, 2.0e6, &[]), hot];
        let out = group(&fx, &items, Screen::None);
        // the average (0.167 Ci/m3) is Class C; without the hot item the remainder is Class A
        assert_eq!(out.json["averaged_class"], "C");
        assert_eq!(out.json["primary_gamma_controlling"], true);
        assert_eq!(out.json["mixture_class"], "A");
        assert_eq!(out.json["candidates"].as_array().unwrap().len(), 3);
        assert_eq!(out.json["candidates"][0]["qualifies"], true);
        assert_eq!(out.json["candidates"][0]["removed"], json!(["hot"]));
        assert_eq!(
            out.json["removed_items"],
            json!([{"id": "hot", "own_class": "above_class_c"}])
        );
        // the package takes the class of the removed item
        let step = plain_step(&fx.ctx(), Screen::None, &items).unwrap();
        assert_eq!(step.class, WasteClass::AboveClassC);
        assert_eq!(step.status, "classified");
    }

    #[test]
    fn tested_class_changes_the_items_that_pass() {
        let fx = Fixture::new();
        // 0.1 Ci/m3 fails the Class A and B tests (2 x 0.02) and passes the Class C test (2 x 0.2)
        let moderate = item(
            "m",
            BtpType::ActivatedMetal,
            1.0e6,
            &[("Nb94", bq(0.1, 1.0e6))],
        );
        let items = [
            item("filler", BtpType::ActivatedMetal, 2.0e6, &[]),
            moderate,
        ];
        let out = group(&fx, &items, Screen::None);
        assert_eq!(out.json["candidates"][0]["removed"], json!(["m"]));
        assert_eq!(out.json["candidates"][2]["removed"], json!([]));
        assert_eq!(out.json["candidates"][2]["remainder_class"], "C");
        assert_eq!(out.json["candidates"][2]["qualifies"], true);
        // the lowest qualifying class is A (the remainder is the filler alone); the item alone is Class C
        assert_eq!(out.json["mixture_class"], "A");
        assert_eq!(out.removed, vec![("m".to_string(), WasteClass::C)]);
    }

    #[test]
    fn all_items_passing_gives_the_class_of_the_averaged_concentrations() {
        let fx = Fixture::new();
        let items = [
            item(
                "a",
                BtpType::ActivatedMetal,
                1.0e6,
                &[("Nb94", bq(0.01, 1.0e6))],
            ),
            item(
                "b",
                BtpType::ActivatedMetal,
                1.0e6,
                &[("Nb94", bq(0.02, 1.0e6))],
            ),
        ];
        let out = group(&fx, &items, Screen::None);
        // both pass the Class A factor of 2 (0.04 Ci/m3); the average is 0.015 Ci/m3
        assert_eq!(out.json["candidates"][0]["removed"], json!([]));
        assert_eq!(out.json["averaged_class"], "A");
        assert_eq!(out.json["mixture_class"], "A");
        assert!(out.removed.is_empty());
        assert_eq!(out.json["candidates"][0]["remainder_class"], "A");
    }

    #[test]
    fn splitting_an_item_into_identical_passing_pieces_keeps_the_class() {
        let fx = Fixture::new();
        let whole = item(
            "w",
            BtpType::ActivatedMetal,
            1.0e6,
            &[("Nb94", bq(0.03, 1.0e6))],
        );
        let halves = [
            item(
                "h1",
                BtpType::ActivatedMetal,
                5.0e5,
                &[("Nb94", bq(0.03, 5.0e5))],
            ),
            item(
                "h2",
                BtpType::ActivatedMetal,
                5.0e5,
                &[("Nb94", bq(0.03, 5.0e5))],
            ),
        ];
        let one = group(&fx, &[whole], Screen::None);
        let two = group(&fx, &halves, Screen::None);
        assert_eq!(one.json["mixture_class"], two.json["mixture_class"]);
        assert_eq!(one.json["averaged_class"], two.json["averaged_class"]);
    }

    #[test]
    fn a_single_item_package_has_the_class_of_the_item() {
        let fx = Fixture::new();
        for ci_m3 in [0.001, 0.03, 0.12, 0.5] {
            let acts = [("Nb94", bq(ci_m3, 1.0e6))];
            let only = item("only", BtpType::ActivatedMetal, 1.0e6, &acts);
            let alone = fx
                .ctx()
                .evaluate(WasteType::ActivatedMetal, 1.0e6, only.mass_g, &only.acts)
                .unwrap();
            let step = plain_step(&fx.ctx(), Screen::None, &[only]).unwrap();
            assert_eq!(step.class, alone.class, "{ci_m3}");
        }
    }

    // ---- sectioned components ---------------------------------------------------------------------------------

    #[test]
    fn sectioned_pieces_merge_into_the_original_component() {
        let fx = Fixture::new();
        let items: Vec<Item> = (1..=4)
            .map(|i| piece(&format!("p{i}"), "rod", 100.0, &[("Nb94", 1.0e7)]))
            .collect();
        let (units, info) = build_units(&fx.ctx(), &items, 0);
        assert_eq!(units.len(), 1);
        assert_eq!(units[0].id, "rod");
        assert_eq!(units[0].pieces, ["p1", "p2", "p3", "p4"]);
        assert_eq!(units[0].volume_cm3, 400.0);
        assert_eq!(units[0].acts["Nb94"], 4.0e7);
        assert!(info[0].merged);
        assert!(info[0].split_out.is_empty());
        // the merged component is above 280 cm3: it is tested individually (and fails: 4.0e7 Bq, 2.7 Ci/m3)
        let out = group(&fx, &items, Screen::None);
        assert_eq!(out.json["candidates"][0]["item_tests"][0]["id"], "rod");
        assert_eq!(out.json["candidates"][0]["item_tests"][0]["small"], false);
        assert_eq!(
            out.json["candidates"][0]["item_tests"][0]["pieces"],
            json!(["p1", "p2", "p3", "p4"])
        );
        assert_eq!(out.sections[0]["merged"], true);
    }

    #[test]
    fn sectioned_pieces_over_table_2_or_table_3_are_split_out() {
        let fx = Fixture::new();
        let items = vec![
            piece("p1", "rod", 100.0, &[("Nb94", 1.0e7)]),
            piece("p2", "rod", 100.0, &[("Nb94", 1.0e7)]),
            // a piece under 280 cm3 over BTP Table 2 (Nb-94 37 MBq)
            piece("p3", "rod", 100.0, &[("Nb94", 3.8e7)]),
            // a piece over BTP Table 3 (Ni-63 2.6e11 Bq in Class A)
            piece("p4", "rod", 5000.0, &[("Ni63", 2.7e11)]),
            // a large piece is exempt from BTP Table 2
            piece("p5", "rod", 5000.0, &[("Nb94", 3.8e7)]),
        ];
        let (units, info) = build_units(&fx.ctx(), &items, 0);
        assert!(info[0].merged);
        assert_eq!(info[0].pieces, ["p1", "p2", "p3", "p4", "p5"]);
        assert_eq!(info[0].split_out, ["p3", "p4"]);
        let ids: Vec<&str> = units.iter().map(|u| u.id.as_str()).collect();
        assert_eq!(ids, ["rod", "p3", "p4"]);
        assert_eq!(units[0].pieces, ["p1", "p2", "p5"]);
        // the Table 3 limit of Class B and C differs: Ni-63 at 2.7e11 Bq is within the Class C limit 5.5e13 Bq
        let (_, info) = build_units(&fx.ctx(), &items, 2);
        assert_eq!(info[0].split_out, ["p3"]);
        // with only one piece left the component is not merged
        let two = vec![
            piece("q1", "rod", 100.0, &[("Nb94", 1.0e7)]),
            piece("q2", "rod", 100.0, &[("Nb94", 3.8e7)]),
        ];
        let (units, info) = build_units(&fx.ctx(), &two, 0);
        assert!(!info[0].merged);
        assert_eq!(info[0].split_out, ["q2"]);
        assert_eq!(units.len(), 2);
    }

    // ---- screens ----------------------------------------------------------------------------------------------

    #[test]
    fn the_37_mbq_screen_averages_when_every_item_is_below_the_activity() {
        let fx = Fixture::new();
        let below = [
            item("a", BtpType::ActivatedMetal, 1.0e6, &[("Nb94", 3.0e7)]),
            item("b", BtpType::ActivatedMetal, 1.0e6, &[("Nb94", 3.0e7)]),
        ];
        let out = group(&fx, &below, Screen::Activity37Mbq);
        assert_eq!(out.json["screen_applied"], "activity_37mbq");
        assert_eq!(out.json["mixture_class"], "A");
        assert_eq!(out.json["candidates"], json!([]));
        // exactly 37 MBq is not below it: the item tests run instead
        let at = [
            item("a", BtpType::ActivatedMetal, 1.0e6, &[("Nb94", 3.7e7)]),
            item("b", BtpType::ActivatedMetal, 1.0e6, &[("Nb94", 1.0)]),
        ];
        let out = group(&fx, &at, Screen::Activity37Mbq);
        assert_eq!(out.json["screen_applied"], Value::Null);
        assert_eq!(out.json["candidates"].as_array().unwrap().len(), 3);
        // the total activity counts, not only the primary gammas
        let total = [item(
            "a",
            BtpType::ActivatedMetal,
            1.0e6,
            &[("Nb94", 2.0e7), ("Ni63", 2.0e7)],
        )];
        assert_eq!(
            group(&fx, &total, Screen::Activity37Mbq).json["screen_applied"],
            Value::Null
        );
    }

    #[test]
    fn the_highest_item_screen_takes_the_highest_item_class() {
        let fx = Fixture::new();
        let items = [
            item("filler", BtpType::ActivatedMetal, 1.0e7, &[]),
            item(
                "hot",
                BtpType::ActivatedMetal,
                1.0e6,
                &[("Nb94", bq(0.1, 1.0e6))],
            ),
        ];
        let out = group(&fx, &items, Screen::HighestItem);
        assert_eq!(out.json["screen_applied"], "highest_item");
        // the average is Class A; the hot item alone is Class C
        assert_eq!(out.json["averaged_class"], "A");
        assert_eq!(out.json["mixture_class"], "C");
        assert!(out.removed.is_empty());
    }

    // ---- encapsulation ----------------------------------------------------------------------------------------

    fn enc(total: f64, mass: f64, density: f64, waste: Option<f64>) -> EncapsulationSpec {
        EncapsulationSpec {
            total_volume_cm3: total,
            encapsulant_mass_g: mass,
            encapsulant_density_g_cm3: density,
            waste_volume_cm3: waste,
        }
    }

    #[test]
    fn loading_of_14_percent_gets_the_whole_encapsulated_volume() {
        let btp = BtpData::bundled().unwrap();
        let full = capsule(&btp, &enc(1.0e6, 1.0e6, 2.0, None), 2.0e5, 1000.0);
        assert_eq!(full.credit, Credit::Full);
        assert_eq!(full.loading, 0.2);
        assert_eq!(full.credited_volume_cm3, 1.0e6);
        assert_eq!(full.mass_credit_g, 1.0e6);
        assert_eq!(full.credited_mass_g, 1.0e6 + 1000.0);
        // the loading threshold is inclusive: 14 %
        let boundary = capsule(&btp, &enc(100.0, 100.0, 2.0, None), 14.0, 1.0);
        assert_eq!(boundary.loading, 0.14);
        assert_eq!(boundary.credit, Credit::Full);
        let under = capsule(&btp, &enc(100.0, 100.0, 2.0, None), 13.9, 1.0);
        assert_eq!(under.credit, Credit::LowLoading);
    }

    #[test]
    fn loading_under_14_percent_is_capped_at_0_2_m3_and_500_kg() {
        let btp = BtpData::bundled().unwrap();
        let low = capsule(&btp, &enc(1.0e6, 1.0e6, 2.0, None), 1.0e5, 1000.0);
        assert_eq!(low.credit, Credit::LowLoading);
        assert_eq!(low.credited_volume_cm3, 2.0e5);
        assert_eq!(low.credited_mass_g, 5.0e5);
        // never more than the encapsulated volume or mass
        let small = capsule(&btp, &enc(1.0e5, 2.0e5, 2.0, Some(1.0e3)), 50.0, 100.0);
        assert_eq!(small.credit, Credit::LowLoading);
        assert_eq!(small.credited_volume_cm3, 1.0e5);
        assert_eq!(small.mass_credit_g, 2.0e5_f64.min(2.0 * (1.0e5 - 1.0e3)));
        assert_eq!(small.credited_mass_g, small.total_mass_g);
        assert_eq!(small.waste_volume_cm3, 1.0e3);
    }

    #[test]
    fn containers_over_331_cubic_feet_need_an_alternative_approach() {
        let btp = BtpData::bundled().unwrap();
        let limit = btp.thresholds.encapsulation_max_volume_cm3;
        let at = capsule(&btp, &enc(limit, 1.0, 2.0, Some(limit * 0.5)), 1.0, 1.0);
        assert_eq!(at.credit, Credit::Full);
        let over = capsule(
            &btp,
            &enc(limit * 1.000001, 1.0, 2.0, Some(limit * 0.5)),
            1.0,
            1.0,
        );
        assert_eq!(over.credit, Credit::Alternative);
        // below the loading threshold the large container still gets the 0.2 m3 cap
        let low = capsule(&btp, &enc(limit * 1.5, 1.0, 2.0, Some(1.0e3)), 1.0, 1.0);
        assert_eq!(low.credit, Credit::LowLoading);
    }

    #[test]
    fn encapsulant_density_credit_is_capped_at_2_5() {
        let btp = BtpData::bundled().unwrap();
        // 1e6 cm3 total, 2e5 waste: 8e5 cm3 of encapsulant at 3.0 g/cm3 is credited at 2.5 g/cm3
        let heavy = capsule(&btp, &enc(1.0e6, 3.0 * 8.0e5, 3.0, Some(2.0e5)), 2.0e5, 1.0);
        assert_eq!(heavy.mass_credit_g, 2.5 * 8.0e5);
        // a lighter declared density is taken as declared
        let light = capsule(&btp, &enc(1.0e6, 3.0 * 8.0e5, 1.5, Some(2.0e5)), 2.0e5, 1.0);
        assert_eq!(light.mass_credit_g, 1.5 * 8.0e5);
        // and the encapsulant mass itself caps the credit
        let thin = capsule(&btp, &enc(1.0e6, 1000.0, 2.0, Some(2.0e5)), 2.0e5, 1.0);
        assert_eq!(thin.mass_credit_g, 1000.0);
    }

    fn source(id: &str, volume_cm3: f64, mass_g: f64, acts: &[(&str, f64)]) -> Item {
        Item {
            mass_g,
            ..item(id, BtpType::SealedSource, volume_cm3, acts)
        }
    }

    #[test]
    fn encapsulation_averages_a_passing_source_over_the_credited_volume() {
        let fx = Fixture::new();
        // 2.0e8 Bq Cs-137 (BTP Table 2 Class A: 2.66e8) in 100 cm3: 54 Ci/m3 alone, Class C
        let items = [source("src", 100.0, 500.0, &[("Cs137", 2.0e8)])];
        let alone = fx
            .ctx()
            .evaluate(WasteType::General, 100.0, 500.0, &items[0].acts)
            .unwrap();
        assert_eq!(alone.class, WasteClass::C);
        // low loading: credited 0.2 m3 and 500 kg
        let step = encapsulated_step(&fx.ctx(), &enc(5.0e5, 1.0e6, 2.0, None), &items).unwrap();
        assert_eq!(step.class, WasteClass::A);
        assert_eq!(step.status, "classified");
        assert_eq!(step.encapsulation["status"], "low_loading");
        assert_eq!(step.encapsulation["credited_volume_cm3"], 2.0e5);
        assert_eq!(step.encapsulation["credited_mass_g"], 5.0e5);
        assert_eq!(step.encapsulation["class"], "A");
        assert_eq!(step.encapsulation["class_c_cap"]["passes"], true);
        assert_eq!(step.groups.len(), 1);
        // loading 100 % of 100 cm3: the whole volume is credited
        let step = encapsulated_step(&fx.ctx(), &enc(100.0, 1.0, 2.0, Some(50.0)), &items).unwrap();
        assert_eq!(step.encapsulation["status"], "credited");
        assert_eq!(step.encapsulation["credited_volume_cm3"], 100.0);
    }

    #[test]
    fn encapsulation_does_not_credit_an_item_that_fails_its_tests() {
        let fx = Fixture::new();
        // the BTP section 3.8.2 source: 33 TBq Cs-137 in 0.5 m3 is inside the Class C cap
        let items = [source("src", 1000.0, 1.0e4, &[("Cs137", 3.3e13)])];
        let step = encapsulated_step(&fx.ctx(), &enc(5.0e5, 1.0e6, 2.0, None), &items).unwrap();
        assert_eq!(step.encapsulation["class_c_cap"]["class"], "C");
        assert_eq!(step.encapsulation["class_c_cap"]["passes"], true);
        // but the item is over the BTP Table 2 and factor limits: it is removed and classified alone
        let group = &step.groups[0];
        assert_eq!(group["candidates"][2]["removed"], json!(["src"]));
        assert_eq!(step.class, WasteClass::AboveClassC);
    }

    #[test]
    fn the_class_c_cap_applies_to_the_whole_package() {
        let fx = Fixture::new();
        // 4.8e12 Bq Cs-137 meets BTP Table 2 Class C, but 1000 cm3 of package is 130 000 Ci/m3
        let items = [source("src", 500.0, 1000.0, &[("Cs137", 4.8e12)])];
        let step = encapsulated_step(&fx.ctx(), &enc(1000.0, 1.0, 2.0, None), &items).unwrap();
        assert_eq!(step.status, "exceeds_class_c_cap");
        assert_eq!(step.encapsulation["status"], "exceeds_class_c_cap");
        assert_eq!(step.encapsulation["class_c_cap"]["passes"], false);
        assert_eq!(step.encapsulation["class_c_cap"]["class"], "above_class_c");
        assert_eq!(step.class, WasteClass::AboveClassC);
    }

    #[test]
    fn mass_based_limits_use_the_credited_mass() {
        let fx = Fixture::new();
        // Am-241 1.0e6 Bq (27 uCi) in a 10 g source; table 3 allows 1.11e8 Bq
        let items = [source("am", 2.0, 10.0, &[("Am241", 1.0e6)])];
        let step = encapsulated_step(&fx.ctx(), &enc(5.0e5, 1.0e6, 2.0, None), &items).unwrap();
        assert_eq!(step.encapsulation["credited_mass_g"], 5.0e5);
        assert_eq!(step.class, WasteClass::A);
    }

    #[test]
    fn container_over_the_volume_limit_has_no_class() {
        let fx = Fixture::new();
        let items = [source("src", 4.0e6, 1.0e6, &[("Cs137", 1.0e6)])];
        let step = encapsulated_step(&fx.ctx(), &enc(1.0e7, 1.0e6, 2.0, None), &items).unwrap();
        assert_eq!(step.status, "alternative_approach_required");
        assert_eq!(step.class, WasteClass::Unknown);
        assert_eq!(step.encapsulation["class"], Value::Null);
        assert_eq!(step.encapsulation["credited_volume_cm3"], Value::Null);
        assert!(step.groups.is_empty());
    }

    #[test]
    fn unknown_coverage_makes_the_group_and_package_unknown() {
        let fx = Fixture::new();
        let items = [
            item("a", BtpType::ActivatedMetal, 1.0e6, &[("Nb94", 1.0e6)]),
            item("b", BtpType::ActivatedMetal, 1.0e6, &[("Fe55", 1.0e6)]),
        ];
        let step = plain_step(&fx.ctx(), Screen::None, &items).unwrap();
        assert_eq!(step.class, WasteClass::Unknown);
        assert_eq!(step.status, "unknown");
        assert_eq!(step.groups[0]["unknown_items"], json!(["b"]));
        assert_eq!(step.groups[0]["mixture_class"], "unknown");
    }

    #[test]
    fn package_class_is_the_highest_of_the_waste_type_groups() {
        let fx = Fixture::new();
        let items = [
            item(
                "metal",
                BtpType::ActivatedMetal,
                1.0e6,
                &[("Nb94", bq(0.001, 1.0e6))],
            ),
            source("src", 1.0e6, 1.0e6, &[("Cs137", bq(3.0, 1.0e6))]),
            source("src2", 1.0e6, 1.0e6, &[]),
        ];
        let step = plain_step(&fx.ctx(), Screen::None, &items).unwrap();
        assert_eq!(step.groups.len(), 2);
        assert_eq!(step.groups[0]["waste_type"], "activated_metal");
        assert_eq!(step.groups[0]["mixture_class"], "A");
        assert_eq!(step.groups[1]["waste_type"], "sealed_source");
        // 3 Ci/m3 Cs-137 in 1 m3 fails the Class A tests (BTP Table 2 and twice the 1 Ci/m3 limit) and is removed
        assert_eq!(step.groups[1]["averaged_class"], "B");
        assert_eq!(
            step.groups[1]["removed_items"],
            json!([{"id": "src", "own_class": "B"}])
        );
        assert_eq!(step.class, WasteClass::B);
    }

    // ---- end to end: spec, refusals, determinism -------------------------------------------------------------

    fn spec() -> Value {
        json!({
            "schema": SPEC_SCHEMA,
            "rules": "us-nrc-10cfr61.55-v1",
            "targets": [2, 3],
            "nuclide_properties": {
                "Nb94": {"z": 41, "half_life_s": 2.03e4 * YEAR_S, "alpha_emitting": false},
                "Cs137": {"z": 55, "half_life_s": 30.17 * YEAR_S, "alpha_emitting": false},
            },
            "items": [
                {"id": "a", "waste_type": "activated_metal", "mass_g": 7800.0,
                 "displaced_volume_cm3": 1000.0,
                 "activity": {"declared_bq": {"2": {"Nb94": 1.0e6}, "3": {"Nb-94": 5.0e5}}}},
                {"id": "b", "waste_type": "cartridge_filter", "mass_g": 500.0,
                 "envelope_volume_cm3": 2000.0,
                 "activity": {"declared_bq": {"2": {"Cs137": 1.0e6}, "3": {"Cs137": 5.0e5}}}},
            ],
        })
    }

    fn refused(spec: &Value) -> String {
        run_doc(&spec.to_string(), Path::new(".")).unwrap_err()
    }

    #[test]
    fn a_declared_spec_runs_and_the_result_is_deterministic() {
        let first = run_doc(&spec().to_string(), Path::new(".")).unwrap();
        let second = run_doc(&spec().to_string(), Path::new(".")).unwrap();
        assert_eq!(first.to_string(), second.to_string());
        assert_eq!(first["schema"], RESULT_SCHEMA);
        assert_eq!(first["screen"], "none");
        assert_eq!(first["encapsulated"], false);
        assert_eq!(first["btp"], BUNDLED_BTP_ID);
        assert_eq!(first["steps"].as_array().unwrap().len(), 2);
        let step = &first["steps"][0];
        assert_eq!(step["step"], 2);
        assert_eq!(step["t_s"], Value::Null);
        assert_eq!(step["items"][0]["activities_bq"]["Nb94"], 1.0e6);
        assert_eq!(
            first["steps"][1]["items"][0]["activities_bq"]["Nb94"],
            5.0e5
        );
        assert_eq!(step["items"][1]["volume_cm3"], 2000.0);
        assert_eq!(step["groups"].as_array().unwrap().len(), 2);
        assert_eq!(step["encapsulation"], Value::Null);
        assert_eq!(first["readings"].as_array().unwrap().len(), READINGS.len());
        assert_eq!(
            first["not_claimed"].as_array().unwrap().len(),
            NOT_CLAIMED.len()
        );
        assert_eq!(first["spec_sha256"].as_str().unwrap().len(), 64);
        assert_eq!(first["btp_sha256"], BtpData::bundled().unwrap().sha256);
    }

    #[test]
    fn a_run_result_gives_per_gram_activity_times_mass() {
        let scratch = Scratch::new();
        let run = json!({"entry_point": "test", "mode": "coupled", "steps": [
            {"step": 2, "t_s": 100.0, "activity_Bq_per_g": {"Nb94": 10.0}},
            {"step": 3, "t_s": 200.0, "activity_Bq_per_g": {"Nb94": 5.0}},
        ]});
        std::fs::write(scratch.0.join("run.json"), run.to_string()).unwrap();
        let mut doc = spec();
        doc["items"] = json!([
            {"id": "a", "waste_type": "activated_metal", "mass_g": 1000.0, "displaced_volume_cm3": 128.0,
             "activity": {"result": "run.json"}},
            {"id": "b", "waste_type": "activated_metal", "mass_g": 300.0, "displaced_volume_cm3": 38.0,
             "activity": {"result": "run.json"}},
        ]);
        let result = run_doc(&doc.to_string(), &scratch.0).unwrap();
        assert_eq!(result["steps"][0]["t_s"], 100.0);
        assert_eq!(result["steps"][1]["t_s"], 200.0);
        assert_eq!(
            result["steps"][0]["items"][0]["activities_bq"]["Nb94"],
            10_000.0
        );
        assert_eq!(
            result["steps"][1]["items"][1]["activities_bq"]["Nb94"],
            1500.0
        );
        assert_eq!(result["inputs"].as_array().unwrap().len(), 2);
        assert_eq!(result["steps"][0]["items"][1]["small"], true);
    }

    #[test]
    fn refusals_have_distinct_messages() {
        let mut messages = BTreeSet::new();
        let mut check = |label: &str, edit: &dyn Fn(&mut Value), expect: &str| {
            let mut doc = spec();
            edit(&mut doc);
            let message = refused(&doc);
            assert!(message.contains(expect), "{label}: {message}");
            assert!(messages.insert(message), "{label}: repeated message");
        };
        check(
            "schema",
            &|d| d["schema"] = json!("nope"),
            "unsupported waste package schema",
        );
        check(
            "empty items",
            &|d| d["items"] = json!([]),
            "at least one item",
        );
        check(
            "targets",
            &|d| d["targets"] = json!([0]),
            "positive one-based",
        );
        check(
            "screen",
            &|d| d["screen"] = json!("middle"),
            "unknown screen",
        );
        check(
            "type",
            &|d| d["items"][0]["waste_type"] = json!("rubble"),
            "unknown waste_type",
        );
        check(
            "duplicate",
            &|d| d["items"][1]["id"] = json!("a"),
            "duplicate item id",
        );
        check(
            "empty id",
            &|d| d["items"][1]["id"] = json!(" "),
            "id must not be empty",
        );
        check(
            "mass missing",
            &|d| d["items"][0]["mass_g"] = Value::Null,
            "mass_g is missing",
        );
        check(
            "mass zero",
            &|d| d["items"][0]["mass_g"] = json!(0.0),
            "mass_g must be finite and positive",
        );
        check(
            "volume missing",
            &|d| d["items"][0]["displaced_volume_cm3"] = Value::Null,
            "displaced_volume_cm3 is missing",
        );
        check(
            "volume negative",
            &|d| d["items"][0]["displaced_volume_cm3"] = json!(-1.0),
            "displaced_volume_cm3 must be finite and positive",
        );
        check(
            "filter volume",
            &|d| d["items"][1]["envelope_volume_cm3"] = Value::Null,
            "envelope_volume_cm3 is missing",
        );
        check(
            "filter displaced",
            &|d| d["items"][1]["displaced_volume_cm3"] = json!(10.0),
            "takes envelope_volume_cm3",
        );
        check(
            "metal envelope",
            &|d| d["items"][0]["envelope_volume_cm3"] = json!(10.0),
            "only a cartridge_filter",
        );
        check(
            "activity both",
            &|d| d["items"][0]["activity"]["result"] = json!("x.json"),
            "both result and declared_bq",
        );
        check(
            "activity none",
            &|d| d["items"][0]["activity"] = json!({}),
            "needs result or declared_bq",
        );
        check(
            "step not target",
            &|d| d["items"][0]["activity"]["declared_bq"]["9"] = json!({}),
            "not a target step",
        );
        check(
            "step missing",
            &|d| {
                d["items"][0]["activity"]["declared_bq"]
                    .as_object_mut()
                    .unwrap()
                    .remove("3");
            },
            "no activity for target step 3",
        );
        check(
            "step key",
            &|d| d["items"][0]["activity"]["declared_bq"]["x"] = json!({}),
            "not a step number",
        );
        check(
            "negative activity",
            &|d| d["items"][0]["activity"]["declared_bq"]["2"]["Nb94"] = json!(-1.0),
            "nonnegative",
        );
        check(
            "alias duplicate",
            &|d| d["items"][0]["activity"]["declared_bq"]["2"]["Nb-94"] = json!(1.0),
            "duplicate activity keys",
        );
        check("unknown key", &|d| d["extra"] = json!(1), "unknown field");
    }

    #[test]
    fn section_refusals() {
        let mut doc = spec();
        doc["items"][0]["section_of"] = json!("rod");
        assert!(refused(&doc).contains("only one piece"));
        doc["items"][1]["section_of"] = json!("rod");
        assert!(refused(&doc).contains("different waste types"));
        doc["items"][1]["waste_type"] = json!("activated_metal");
        doc["items"][1]["displaced_volume_cm3"] = json!(10.0);
        doc["items"][1]["envelope_volume_cm3"] = Value::Null;
        assert!(run_doc(&doc.to_string(), Path::new(".")).is_ok());
    }

    #[test]
    fn encapsulation_refusals() {
        let block = json!({"total_volume_cm3": 1.0e5, "encapsulant_mass_g": 1.0e5, "encapsulant_density_g_cm3": 2.0});
        let mut doc = spec();
        doc["encapsulation"] = block.clone();
        assert!(run_doc(&doc.to_string(), Path::new(".")).is_ok());

        let mut mixed = doc.clone();
        mixed["items"][1]["encapsulated"] = json!(false);
        assert!(refused(&mixed).contains("cannot share a package"));
        let mut none = doc.clone();
        none["items"][0]["encapsulated"] = json!(false);
        none["items"][1]["encapsulated"] = json!(false);
        assert!(refused(&none).contains("no item is encapsulated"));
        let mut flagged = spec();
        flagged["items"][0]["encapsulated"] = json!(true);
        assert!(refused(&flagged).contains("no encapsulation block"));

        for field in [
            "total_volume_cm3",
            "encapsulant_mass_g",
            "encapsulant_density_g_cm3",
        ] {
            let mut bad = doc.clone();
            bad["encapsulation"][field] = json!(0.0);
            assert!(refused(&bad).contains(field), "{field}");
        }
        let mut waste_volume = doc.clone();
        waste_volume["encapsulation"]["waste_volume_cm3"] = json!(-1.0);
        assert!(refused(&waste_volume).contains("waste_volume_cm3"));
        let mut loading = doc.clone();
        loading["encapsulation"]["waste_volume_cm3"] = json!(2.0e5);
        assert!(refused(&loading).contains("waste loading above 1"));
        // the item volumes (1000 + 2000 cm3) are the waste volume unless declared
        let mut summed = doc.clone();
        summed["encapsulation"]["total_volume_cm3"] = json!(3000.0);
        assert!(run_doc(&summed.to_string(), Path::new(".")).is_ok());
        summed["encapsulation"]["total_volume_cm3"] = json!(2999.0);
        assert!(refused(&summed).contains("waste loading above 1"));
        let mut screen = doc;
        screen["screen"] = json!("highest_item");
        assert!(refused(&screen).contains("not available with encapsulation"));
    }

    #[test]
    fn a_refusal_writes_nothing() {
        let scratch = Scratch::new();
        let mut doc = spec();
        doc["items"][0]["waste_type"] = json!("rubble");
        let path = scratch.0.join("spec.json");
        std::fs::write(&path, doc.to_string()).unwrap();
        let out = scratch.0.join("out.json");
        let error = run(path.to_str().unwrap(), Some(out.to_str().unwrap())).unwrap_err();
        assert!(error.contains("unknown waste_type"));
        assert!(!out.exists());
        // and an accepted spec writes the same document it returns
        std::fs::write(&path, spec().to_string()).unwrap();
        let result = run(path.to_str().unwrap(), Some(out.to_str().unwrap())).unwrap();
        let written: Value = serde_json::from_str(&std::fs::read_to_string(&out).unwrap()).unwrap();
        assert_eq!(written["schema"], RESULT_SCHEMA);
        assert_eq!(written["spec_sha256"], result["spec_sha256"]);
        assert_eq!(written["steps"].as_array().unwrap().len(), 2);
    }

    #[test]
    fn mismatched_timestamps_and_missing_files_are_refused() {
        let scratch = Scratch::new();
        let run = |t: f64| {
            json!({"entry_point": "t", "mode": "coupled", "steps": [
            {"step": 2, "t_s": t, "activity_Bq_per_g": {"Nb94": 1.0}},
            {"step": 3, "t_s": t + 1.0, "activity_Bq_per_g": {"Nb94": 1.0}}]})
        };
        std::fs::write(scratch.0.join("r1.json"), run(10.0).to_string()).unwrap();
        std::fs::write(scratch.0.join("r2.json"), run(20.0).to_string()).unwrap();
        let mut doc = spec();
        doc["items"][0]["activity"] = json!({"result": "r1.json"});
        doc["items"][1] = json!({"id": "b", "waste_type": "activated_metal", "mass_g": 5.0,
            "displaced_volume_cm3": 1.0, "activity": {"result": "r2.json"}});
        let error = run_doc(&doc.to_string(), &scratch.0).unwrap_err();
        assert!(error.contains("differs from"), "{error}");
        doc["items"][1]["activity"] = json!({"result": "absent.json"});
        let error = run_doc(&doc.to_string(), &scratch.0).unwrap_err();
        assert!(error.contains("cannot read result"), "{error}");
    }
}
