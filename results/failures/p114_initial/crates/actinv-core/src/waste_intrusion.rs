//! Conditional, non-regulatory screening against the pinned February 2026
//! draft NUREG-1556 Table 8-5. This is an evidence organizer, not an
//! acceptance, dose, or compliance calculation.

use crate::waste::{
    normalize_nuclide_key, NuclideProperties, RulePack, WasteClass, WasteGeometry, WasteType,
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};

const DRAFT_PACK: &str = include_str!("../data/waste_us_nrc_nureg1556_v22_draft_2026_02_v1.json");
const FIVE_YEARS_S: f64 = 5.0 * 31_557_600.0;
const CI_BQ_CM3: f64 = 37_000.0;
const NCI_BQ_G: f64 = 37.0;

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum InventoryCoverage {
    Complete,
    Incomplete,
    Unknown,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum WasteForm {
    Metal,
    CementOrPolymer,
    Soil,
    Equipment,
    Rubble,
    Resin,
    Ash,
    Calcined,
    Other,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum MembershipCoverage {
    Complete,
    Incomplete,
}

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct WacLimit {
    pub value: f64,
    pub unit: ConcentrationUnit,
}

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct WacConcentration {
    pub value: f64,
    pub unit: ConcentrationUnit,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ConcentrationUnit {
    #[serde(rename = "Ci/m3")]
    CiPerM3,
    #[serde(rename = "nCi/g")]
    NCiPerG,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(tag = "status", deny_unknown_fields)]
pub enum WacEntry {
    #[serde(rename = "listed")]
    Listed { limit: WacLimit },
    #[serde(rename = "listed_no_numeric_limit")]
    ListedNoNumericLimit,
    #[serde(rename = "unlisted")]
    Unlisted,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct SiteWacDeclaration {
    pub source: String,
    pub membership_coverage: MembershipCoverage,
    pub nuclides: BTreeMap<String, WacEntry>,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct DotRqDeclaration {
    pub source: String,
    pub coverage: MembershipCoverage,
    pub nuclides: BTreeMap<String, f64>,
}

#[derive(Clone, Debug)]
pub struct IntrusionDeclarations {
    pub waste_form: WasteForm,
    pub inventory_coverage: InventoryCoverage,
    pub unbounded_inventory_reasons: Vec<String>,
    pub required_h3_missing: bool,
    pub site_wac: Option<SiteWacDeclaration>,
    pub dot_rq: Option<DotRqDeclaration>,
}

/// Validated declarations preserve caller wording and contain canonical lookup
/// maps. Public declaration fields are intended for transparent serialization.
#[derive(Clone, Debug)]
pub struct ValidatedDeclarations {
    pub waste_form: WasteForm,
    pub inventory_coverage: InventoryCoverage,
    pub unbounded_inventory_reasons: Vec<String>,
    pub required_h3_missing: bool,
    pub site_wac: Option<SiteWacDeclaration>,
    pub dot_rq: Option<DotRqDeclaration>,
    pub(crate) wac_by_nuclide: BTreeMap<String, WacEntry>,
    pub(crate) rq_by_nuclide: BTreeMap<String, f64>,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct DraftPackWire {
    schema: String,
    id: String,
    version: u32,
    source_title: String,
    source_adams: String,
    source_status: String,
    source_url: String,
    source_page: String,
    source_as_of: String,
    source_pdf_sha256: String,
    source_text_sha256: String,
    ci_bq: f64,
    year_s: f64,
    table_id: String,
    table_caption: String,
    source_locations: Vec<String>,
    row_interpretations: Value,
    printed_defects_preserved: Vec<Value>,
    rows: Vec<DraftRow>,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct DraftRow {
    id: String,
    label: String,
    selector: String,
    members: Vec<String>,
    applicability: String,
    unit: String,
    limits: Vec<Option<f64>>,
    basis_note: String,
}

#[derive(Clone, Debug)]
pub struct DraftRulePack {
    wire: DraftPackWire,
    sha256: String,
}

impl DraftRulePack {
    pub fn bundled() -> Result<Self, String> {
        let wire: DraftPackWire = serde_json::from_str(DRAFT_PACK)
            .map_err(|e| format!("bundled draft rule pack JSON: {e}"))?;
        if wire.schema != "actinv-waste-draft-pack-1"
            || wire.id != "us-nrc-nureg1556-v22-draft-2026-02-v1"
            || wire.version != 1
            || wire.source_status != "draft_report_for_comment"
            || wire.table_id != "8-5"
            || wire.ci_bq != 37_000_000_000.0
            || wire.year_s != 31_557_600.0
            || wire.rows.len() != 25
        {
            return Err("bundled draft rule pack identity/constants/row count mismatch".into());
        }
        let mut ids = BTreeSet::new();
        for row in &wire.rows {
            if row.id.is_empty()
                || !ids.insert(row.id.as_str())
                || !matches!(
                    row.applicability.as_str(),
                    "all" | "general" | "activated_metal"
                )
                || !matches!(row.unit.as_str(), "Ci/m3" | "nCi/g")
                || row.limits.len() != 3
                || row
                    .limits
                    .iter()
                    .flatten()
                    .any(|x| !x.is_finite() || *x <= 0.0)
                || row.selector.is_empty()
                || row.label.is_empty()
                || row.basis_note.is_empty()
            {
                return Err(format!("invalid bundled draft row {}", row.id));
            }
            for member in &row.members {
                normalize_nuclide_key(member)?;
            }
        }
        let sha256 = sha256(DRAFT_PACK.as_bytes());
        if sha256 != "071c238917ac0f3cb3436df958e78ac5868f1de4ed39b0c25781e8f2b7a4ffc7" {
            return Err(
                "bundled draft rule pack bytes do not match the frozen source identity".into(),
            );
        }
        Ok(Self { wire, sha256 })
    }

    pub fn identity(&self) -> Value {
        let w = &self.wire;
        json!({"schema":w.schema,"id":w.id,"version":w.version,
            "source_title":w.source_title,"source_adams":w.source_adams,
            "source_status":w.source_status,"source_url":w.source_url,
            "source_page":w.source_page,"source_as_of":w.source_as_of,
            "source_pdf_sha256":w.source_pdf_sha256,"source_text_sha256":w.source_text_sha256,
            "ci_bq":w.ci_bq,"year_s":w.year_s,
            "table_id":w.table_id,"table_caption":w.table_caption,
            "source_locations":w.source_locations,"row_interpretations":w.row_interpretations,
            "printed_defects_preserved":w.printed_defects_preserved,"sha256":self.sha256})
    }
}

pub fn validate_declarations(d: IntrusionDeclarations) -> Result<ValidatedDeclarations, String> {
    if d.site_wac.as_ref().is_some_and(|x| x.nuclides.len() > 1024)
        || d.dot_rq.as_ref().is_some_and(|x| x.nuclides.len() > 1024)
    {
        return Err("WAC and DOT RQ maps are limited to 1024 entries".into());
    }
    if d.unbounded_inventory_reasons
        .iter()
        .any(|x| x.trim().is_empty())
        || d.unbounded_inventory_reasons
            .iter()
            .collect::<BTreeSet<_>>()
            .len()
            != d.unbounded_inventory_reasons.len()
    {
        return Err("unbounded inventory reasons must be distinct nonempty strings".into());
    }
    if (d.inventory_coverage == InventoryCoverage::Complete)
        != d.unbounded_inventory_reasons.is_empty()
    {
        return Err(
            "complete inventory requires no reasons; incomplete/unknown coverage requires reasons"
                .into(),
        );
    }
    let mut wac_by_nuclide = BTreeMap::new();
    if let Some(wac) = &d.site_wac {
        if wac.source.trim().is_empty() {
            return Err("site_wac source must be nonempty".into());
        }
        for (raw, entry) in &wac.nuclides {
            if let WacEntry::Listed { limit } = entry {
                if !limit.value.is_finite() || limit.value < 0.0 {
                    return Err(format!("invalid WAC limit for {raw}"));
                }
            }
            let key = normalize_nuclide_key(raw)?;
            if wac_by_nuclide.insert(key.clone(), entry.clone()).is_some() {
                return Err(format!("duplicate WAC nuclide after normalization: {key}"));
            }
        }
    }
    let mut rq_by_nuclide = BTreeMap::new();
    if let Some(rq) = &d.dot_rq {
        if rq.source.trim().is_empty() {
            return Err("dot_rq source must be nonempty".into());
        }
        for (raw, value) in &rq.nuclides {
            if !value.is_finite() || *value <= 0.0 {
                return Err(format!("DOT RQ for {raw} must be positive finite Bq"));
            }
            let key = normalize_nuclide_key(raw)?;
            if rq_by_nuclide.insert(key.clone(), *value).is_some() {
                return Err(format!(
                    "duplicate DOT RQ nuclide after normalization: {key}"
                ));
            }
        }
    }
    Ok(ValidatedDeclarations {
        waste_form: d.waste_form,
        inventory_coverage: d.inventory_coverage,
        unbounded_inventory_reasons: d.unbounded_inventory_reasons,
        required_h3_missing: d.required_h3_missing,
        site_wac: d.site_wac,
        dot_rq: d.dot_rq,
        wac_by_nuclide,
        rq_by_nuclide,
    })
}

#[derive(Clone, Debug)]
pub struct IntrusionTargetInput<'a> {
    pub part61_rules: &'a RulePack,
    pub waste_type: WasteType,
    pub geometry: WasteGeometry,
    pub activation_activities_bq: &'a BTreeMap<String, f64>,
    pub external_tritium_activity_bq: f64,
    pub nuclide_properties: &'a BTreeMap<String, NuclideProperties>,
    pub nominal_class: WasteClass,
    pub declarations: &'a ValidatedDeclarations,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum TriState {
    True,
    False,
    Indeterminate,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum AssessmentIndicator {
    ReviewIndicated,
    Indeterminate,
    NotIndicatedByImplementedChecks,
}

#[derive(Clone, Debug, Serialize)]
pub struct PredicateResult {
    pub status: TriState,
    pub reasons: Vec<String>,
    pub evidence: Value,
}

#[derive(Clone, Debug, Serialize)]
pub struct NuclideScreen {
    pub nuclide: String,
    pub activity_bq: f64,
    pub concentration_bq_cm3: f64,
    pub activity_share: Option<f64>,
    pub part61_listing: &'static str,
    pub wac_listing: &'static str,
    pub wac_concentration: Option<WacConcentration>,
    pub presence: TriState,
    pub predicates: BTreeMap<&'static str, PredicateResult>,
}

#[derive(Clone, Debug, Serialize)]
pub struct DraftRowScreen {
    pub row_id: String,
    pub members: Vec<String>,
    pub presence: TriState,
    pub membership_complete: bool,
    pub status: TriState,
    pub unit: String,
    pub selected_class: Option<WasteClass>,
    pub limit: Option<f64>,
    pub known_activity_bq: f64,
    pub known_concentration: f64,
    pub relation: &'static str,
    pub assessment_indicator: AssessmentIndicator,
    pub applicability: TriState,
    pub contributions_bq: BTreeMap<String, f64>,
}

#[derive(Clone, Debug, Serialize)]
pub struct IntrusionTargetEvaluation {
    pub nominal_class: WasteClass,
    pub inventory_coverage: InventoryCoverage,
    pub unbounded_inventory_reasons: Vec<String>,
    pub inventory_activity_bq: BTreeMap<String, f64>,
    pub external_tritium_activity_bq: f64,
    pub known_total_activity_bq: f64,
    pub container_total_activity_bq: Option<f64>,
    pub container_denominator_complete: bool,
    pub denominator_missing_reasons: Vec<String>,
    pub dot_rq_mixture_ratios: BTreeMap<String, f64>,
    pub dot_rq_mixture_ratio: f64,
    pub dot_rq_mixture_complete: bool,
    pub nuclides: Vec<NuclideScreen>,
    pub rows: Vec<DraftRowScreen>,
    pub assessment_indicator: AssessmentIndicator,
}

pub fn evaluate_intrusion_target(
    draft: &DraftRulePack,
    input: IntrusionTargetInput<'_>,
) -> Result<IntrusionTargetEvaluation, String> {
    if (input.declarations.waste_form == WasteForm::Metal)
        != (input.waste_type == WasteType::ActivatedMetal)
    {
        return Err(
            "metal waste form and activated_metal nominal type must be selected together".into(),
        );
    }
    let g = input.geometry;
    if !g.mass_g.is_finite()
        || g.mass_g <= 0.0
        || !g.displaced_volume_cm3.is_finite()
        || g.displaced_volume_cm3 <= 0.0
    {
        return Err("intrusion screen geometry must be positive and finite".into());
    }
    if !input.external_tritium_activity_bq.is_finite() || input.external_tritium_activity_bq < 0.0 {
        return Err("external tritium activity must be finite nonnegative Bq".into());
    }
    let mut activation = BTreeMap::new();
    for (raw, value) in input.activation_activities_bq {
        if !value.is_finite() || *value < 0.0 {
            return Err(format!("activity for {raw} must be finite nonnegative Bq"));
        }
        let key = normalize_nuclide_key(raw)?;
        if activation.insert(key.clone(), *value).is_some() {
            return Err(format!(
                "duplicate activity nuclide after normalization: {key}"
            ));
        }
    }
    let mut props = BTreeMap::new();
    for (raw, prop) in input.nuclide_properties {
        let key = normalize_nuclide_key(raw)?;
        if prop.z <= 0 || !prop.half_life_s.is_finite() || prop.half_life_s <= 0.0 {
            return Err(format!("invalid properties for {key}"));
        }
        if props.insert(key.clone(), *prop).is_some() {
            return Err(format!(
                "duplicate nuclide properties after normalization: {key}"
            ));
        }
    }
    if props.len() > 1024 {
        return Err("nuclide properties are limited to 1024 entries".into());
    }
    for (key, prop) in &props {
        let _ = input
            .part61_rules
            .nuclide_listed(input.waste_type, key, Some(*prop))?;
    }
    let mut inventory = activation;
    if input.external_tritium_activity_bq > 0.0 {
        let entry = inventory.entry("H3".to_string()).or_insert(0.0);
        *entry += input.external_tritium_activity_bq;
        if !entry.is_finite() {
            return Err("H-3 inventory activity overflow".into());
        }
    }
    inventory.retain(|_, v| *v > 0.0);
    if inventory.len() > 1024 {
        return Err("positive target inventory is limited to 1024 nuclides".into());
    }
    let mut known_total = 0.0;
    for value in inventory.values() {
        known_total += *value;
        if !known_total.is_finite() {
            return Err("whole-container activity sum overflow".into());
        }
    }
    let mut missing = input.declarations.unbounded_inventory_reasons.clone();
    if input.declarations.inventory_coverage != InventoryCoverage::Complete {
        missing.push("caller_inventory_coverage_incomplete_or_unknown".into());
    }
    for key in inventory.keys() {
        if !props.contains_key(key) {
            missing.push(format!("missing_nuclide_properties:{key}"));
        }
    }
    if input.declarations.required_h3_missing {
        missing.push("required_external_tritium_not_declared".into());
    }
    missing.sort();
    missing.dedup();
    let denominator_complete = missing.is_empty();
    let total = if denominator_complete {
        Some(known_total)
    } else {
        None
    };
    let rq_coverage_complete = input
        .declarations
        .dot_rq
        .as_ref()
        .is_some_and(|d| d.coverage == MembershipCoverage::Complete);
    let mut ratios = BTreeMap::new();
    let mut ratio_complete = rq_coverage_complete && denominator_complete;
    for (key, activity) in &inventory {
        if let Some(rq) = input.declarations.rq_by_nuclide.get(key) {
            let value = activity / rq;
            if !value.is_finite() {
                return Err(format!("DOT RQ mixture ratio overflow for {key}"));
            }
            ratios.insert(key.clone(), value);
        } else {
            ratio_complete = false;
        }
    }
    let mixture = ratios.values().sum::<f64>();
    if !mixture.is_finite() {
        return Err("DOT RQ mixture sum overflow".into());
    }
    let mut nuclides = Vec::new();
    let mut presence_by_name = BTreeMap::new();
    for (key, activity) in &inventory {
        let concentration = *activity / g.displaced_volume_cm3;
        if !concentration.is_finite() {
            return Err(format!("concentration overflow for {key}"));
        }
        let prop = props.get(key).copied();
        let part61 = input
            .part61_rules
            .nuclide_listed(input.waste_type, key, prop)?;
        let p61 = listing(part61);
        let wac_entry = input.declarations.wac_by_nuclide.get(key);
        let wac_listing = match wac_entry {
            Some(WacEntry::Listed { .. } | WacEntry::ListedNoNumericLimit) => "listed",
            Some(WacEntry::Unlisted) => "unlisted",
            None if input
                .declarations
                .site_wac
                .as_ref()
                .is_some_and(|w| w.membership_coverage == MembershipCoverage::Complete) =>
            {
                "unlisted"
            }
            None => "unknown",
        };
        let wac_membership = match wac_listing {
            "listed" => Some(true),
            "unlisted" => Some(false),
            _ => None,
        };
        let c2_boundary = 260_000.0 * g.displaced_volume_cm3;
        if !c2_boundary.is_finite() {
            return Err("criterion-2 concentration boundary overflow".into());
        }
        let wac_limit = match wac_entry {
            Some(WacEntry::Listed { limit }) => Some(*limit),
            _ => None,
        };
        let wac_conc = match wac_limit {
            Some(limit) => Some(WacConcentration {
                value: concentration_for(*activity, limit.unit, g)?,
                unit: limit.unit,
            }),
            _ => None,
        };
        let wac_pred = if let Some(limit) = wac_limit {
            let c = concentration_for(*activity, limit.unit, g)?;
            let threshold = 0.01 * limit.value;
            let denominator = match limit.unit {
                ConcentrationUnit::CiPerM3 => g.displaced_volume_cm3 * CI_BQ_CM3,
                ConcentrationUnit::NCiPerG => g.mass_g * NCI_BQ_G,
            };
            let boundary = 0.01 * limit.value * denominator;
            if c.is_finite() && threshold.is_finite() && boundary.is_finite() {
                pred(
                    *activity > boundary,
                    format!(
                        "concentration={c} is {} 0.01*limit={threshold}",
                        if *activity > boundary {
                            "above"
                        } else {
                            "not above"
                        }
                    ),
                    json!({"concentration":c,"unit":limit.unit,"wac_limit":limit.value,"threshold":threshold,"source":input.declarations.site_wac.as_ref().map(|x|x.source.as_str())}),
                )
            } else {
                return Err(format!("WAC concentration overflow for {key}"));
            }
        } else {
            ind(
                "numeric WAC limit absent",
                json!({"concentration":null,"unit":null,"wac_limit":null,"threshold":null,"source":input.declarations.site_wac.as_ref().map(|x|x.source.as_str())}),
            )
        };
        let p2e = json!({"concentration_bq_cm3":concentration,"threshold_bq_cm3":260000.0,"part61_listing":p61,"wac_listing":wac_listing,"interpretation":"consensus_either_or_both"});
        let p2 = if *activity <= c2_boundary {
            pred(false, "at or below 260000 Bq/cm3".into(), p2e)
        } else {
            match (part61, wac_membership) {
                (Some(false), Some(false)) => pred(
                    true,
                    "above threshold and unlisted in both sources".into(),
                    p2e,
                ),
                (Some(true), Some(true)) => pred(false, "listed in both sources".into(), p2e),
                _ => ind("membership consensus unresolved above threshold", p2e),
            }
        };
        let individual_ratio = ratios.get(key).copied();
        let p3 = if let Some(rq) = input.declarations.rq_by_nuclide.get(key) {
            if activity >= rq {
                pred(
                    true,
                    "individual activity at or above RQ".into(),
                    json!({"activity_bq":activity,"individual_rq_bq":rq,"individual_ratio":individual_ratio,"known_mixture_ratio":mixture,"mixture_complete":ratio_complete,"source":input.declarations.dot_rq.as_ref().map(|x|x.source.as_str())}),
                )
            } else if ratio_complete && mixture < 1.0 {
                pred(
                    false,
                    "individual below RQ and complete mixture below one".into(),
                    json!({"activity_bq":activity,"individual_rq_bq":rq,"individual_ratio":individual_ratio,"known_mixture_ratio":mixture,"mixture_complete":ratio_complete,"source":input.declarations.dot_rq.as_ref().map(|x|x.source.as_str())}),
                )
            } else {
                ind(
                    "RQ or mixture coverage unresolved",
                    json!({"activity_bq":activity,"individual_rq_bq":rq,"individual_ratio":individual_ratio,"known_mixture_ratio":mixture,"mixture_complete":ratio_complete,"source":input.declarations.dot_rq.as_ref().map(|x|x.source.as_str())}),
                )
            }
        } else {
            ind(
                "individual RQ missing",
                json!({"activity_bq":activity,"individual_rq_bq":null,"individual_ratio":null,"known_mixture_ratio":mixture,"mixture_complete":ratio_complete,"source":input.declarations.dot_rq.as_ref().map(|x|x.source.as_str())}),
            )
        };
        let p4 = if let Some(container) = total {
            pred(
                *activity >= 0.01 * container,
                "complete container share comparison".into(),
                json!({"activity_bq":activity,"container_total_activity_bq":container,"threshold_bq":0.01*container,"container_denominator_complete":true}),
            )
        } else {
            ind(
                "container denominator incomplete",
                json!({"activity_bq":activity,"container_total_activity_bq":null,"threshold_bq":null,"container_denominator_complete":false}),
            )
        };
        let mut predicates = BTreeMap::new();
        predicates.insert("wac_fraction", wac_pred);
        predicates.insert("unlisted_concentration", p2);
        predicates.insert("dot_rq", p3);
        predicates.insert("container_share", p4);
        let presence = tri_or(predicates.values().map(|x| &x.status));
        presence_by_name.insert(key.clone(), presence);
        let share = total.map(|x| activity / x);
        nuclides.push(NuclideScreen {
            nuclide: key.clone(),
            activity_bq: *activity,
            concentration_bq_cm3: concentration,
            activity_share: share,
            part61_listing: p61,
            wac_listing,
            wac_concentration: wac_conc,
            presence,
            predicates,
        });
    }
    let class_col = match input.nominal_class {
        WasteClass::A => Some(0),
        WasteClass::B => Some(1),
        WasteClass::C => Some(2),
        _ => None,
    };
    let mut rows_out = Vec::new();
    for row in &draft.wire.rows {
        if row.applicability == "general" && input.waste_type == WasteType::ActivatedMetal {
            continue;
        }
        if row.applicability == "activated_metal" && input.waste_type != WasteType::ActivatedMetal {
            continue;
        }
        let selected_class = match class_col {
            Some(0) => Some(WasteClass::A),
            Some(1) => Some(WasteClass::B),
            Some(_) => Some(WasteClass::C),
            None => None,
        };
        let limit = class_col.and_then(|i| row.limits[i]);
        let mut members = BTreeSet::new();
        for raw in &row.members {
            members.insert(normalize_nuclide_key(raw)?);
        }
        let mut membership_complete = true;
        if row.selector == "half_life_lt5y" {
            for key in inventory.keys() {
                match props.get(key) {
                    Some(p) if p.half_life_s < FIVE_YEARS_S => {
                        members.insert(key.clone());
                    }
                    Some(_) => {}
                    None => membership_complete = false,
                }
            }
        }
        let matched: Vec<String> = members
            .into_iter()
            .filter(|n| inventory.contains_key(n))
            .collect();
        let mut amount = 0.0;
        let mut contributions = BTreeMap::new();
        for n in &matched {
            let v = inventory[n];
            amount += v;
            if !amount.is_finite() {
                return Err(format!("draft row {} sum overflow", row.id));
            }
            contributions.insert(n.clone(), v);
        }
        let presence = tri_or(matched.iter().filter_map(|n| presence_by_name.get(n)));
        let presence = if !membership_complete && !matches!(presence, TriState::True) {
            TriState::Indeterminate
        } else {
            presence
        };
        let conc = if row.unit == "Ci/m3" {
            amount / g.displaced_volume_cm3 / CI_BQ_CM3
        } else {
            amount / g.mass_g / NCI_BQ_G
        };
        if !conc.is_finite() {
            return Err(format!("draft row {} concentration overflow", row.id));
        }
        let limit_activity = limit.map(|l| row_limit_activity(l, &row.unit, g));
        if limit_activity.is_some_and(|x| !x.is_finite()) {
            return Err(format!("draft row {} activity limit overflow", row.id));
        }
        let relation = match (class_col, limit_activity) {
            (None, _) => "unknown",
            (_, None) => "no_numeric_limit",
            (Some(_), Some(boundary)) if amount < boundary => "below",
            (Some(_), Some(boundary)) if amount == boundary => "at_limit",
            _ => "above",
        };
        let status = if !membership_complete
            && !(matches!(presence, TriState::True) && relation == "above")
        {
            TriState::Indeterminate
        } else {
            match presence {
                TriState::False => TriState::False,
                TriState::Indeterminate => TriState::Indeterminate,
                TriState::True if relation == "above" => TriState::True,
                TriState::True if relation == "below" || relation == "no_numeric_limit" => {
                    TriState::False
                }
                _ => TriState::Indeterminate,
            }
        };
        let row_indicator = if matches!(presence, TriState::True) && relation == "above" {
            AssessmentIndicator::ReviewIndicated
        } else if !membership_complete
            || matches!(presence, TriState::Indeterminate)
            || (matches!(presence, TriState::True) && matches!(relation, "at_limit" | "unknown"))
        {
            AssessmentIndicator::Indeterminate
        } else {
            AssessmentIndicator::NotIndicatedByImplementedChecks
        };
        rows_out.push(DraftRowScreen {
            row_id: row.id.clone(),
            members: matched,
            presence,
            membership_complete,
            status,
            unit: row.unit.clone(),
            selected_class,
            limit,
            known_activity_bq: amount,
            known_concentration: conc,
            relation,
            assessment_indicator: row_indicator,
            applicability: presence,
            contributions_bq: contributions,
        });
    }
    let mut clear_unlisted = false;
    for n in &nuclides {
        if matches!(n.presence, TriState::True)
            && draft_listing(
                &draft.wire,
                &n.nuclide,
                props.get(&n.nuclide).copied(),
                input.waste_type,
                input.declarations.waste_form,
            )? == Some(false)
        {
            clear_unlisted = true;
        }
    }
    let clear_row = rows_out
        .iter()
        .any(|r| matches!(r.presence, TriState::True) && r.relation == "above");
    let clear = input.declarations.waste_form == WasteForm::Other || clear_unlisted || clear_row;
    let unresolved = !denominator_complete
        || class_col.is_none()
        || nuclides
            .iter()
            .any(|n| matches!(n.presence, TriState::Indeterminate))
        || rows_out.iter().any(|r| {
            !r.membership_complete
                || matches!(r.presence, TriState::Indeterminate)
                || (matches!(r.presence, TriState::True)
                    && matches!(r.relation, "at_limit" | "unknown"))
        });
    let indicator = if clear {
        AssessmentIndicator::ReviewIndicated
    } else if unresolved {
        AssessmentIndicator::Indeterminate
    } else {
        AssessmentIndicator::NotIndicatedByImplementedChecks
    };
    Ok(IntrusionTargetEvaluation {
        nominal_class: input.nominal_class,
        inventory_coverage: input.declarations.inventory_coverage,
        unbounded_inventory_reasons: input.declarations.unbounded_inventory_reasons.clone(),
        inventory_activity_bq: inventory,
        external_tritium_activity_bq: input.external_tritium_activity_bq,
        known_total_activity_bq: known_total,
        container_total_activity_bq: total,
        container_denominator_complete: denominator_complete,
        denominator_missing_reasons: missing,
        dot_rq_mixture_ratios: ratios,
        dot_rq_mixture_ratio: mixture,
        dot_rq_mixture_complete: ratio_complete,
        nuclides,
        rows: rows_out,
        assessment_indicator: indicator,
    })
}

fn concentration_for(
    activity: f64,
    unit: ConcentrationUnit,
    g: WasteGeometry,
) -> Result<f64, String> {
    let value = match unit {
        ConcentrationUnit::CiPerM3 => activity / g.displaced_volume_cm3 / CI_BQ_CM3,
        ConcentrationUnit::NCiPerG => activity / g.mass_g / NCI_BQ_G,
    };
    if value.is_finite() {
        Ok(value)
    } else {
        Err("WAC concentration overflow".into())
    }
}
fn row_limit_activity(limit: f64, unit: &str, g: WasteGeometry) -> f64 {
    limit
        * match unit {
            "Ci/m3" => g.displaced_volume_cm3 * CI_BQ_CM3,
            _ => g.mass_g * NCI_BQ_G,
        }
}
fn draft_listing(
    pack: &DraftPackWire,
    key: &str,
    properties: Option<NuclideProperties>,
    waste_type: WasteType,
    form: WasteForm,
) -> Result<Option<bool>, String> {
    let mut unknown = false;
    for row in &pack.rows {
        if row.applicability == "general" && waste_type == WasteType::ActivatedMetal {
            continue;
        }
        if row.applicability == "activated_metal" && form != WasteForm::Metal {
            continue;
        }
        if row
            .members
            .iter()
            .any(|member| normalize_nuclide_key(member).ok().as_deref() == Some(key))
        {
            return Ok(Some(true));
        }
        if row.selector == "half_life_lt5y" {
            match properties {
                None => unknown = true,
                Some(p) if p.half_life_s < FIVE_YEARS_S => return Ok(Some(true)),
                Some(_) => {}
            }
        }
    }
    Ok(if unknown { None } else { Some(false) })
}
fn listing(value: Option<bool>) -> &'static str {
    match value {
        Some(true) => "listed",
        Some(false) => "unlisted",
        None => "unknown",
    }
}
fn pred(value: bool, reason: String, evidence: Value) -> PredicateResult {
    PredicateResult {
        status: if value {
            TriState::True
        } else {
            TriState::False
        },
        reasons: vec![reason],
        evidence,
    }
}
fn ind(reason: &str, evidence: Value) -> PredicateResult {
    PredicateResult {
        status: TriState::Indeterminate,
        reasons: vec![reason.to_string()],
        evidence,
    }
}
fn tri_or<'a>(values: impl Iterator<Item = &'a TriState>) -> TriState {
    let mut saw = false;
    let mut all_false = true;
    for v in values {
        saw = true;
        if matches!(v, TriState::True) {
            return TriState::True;
        }
        if !matches!(v, TriState::False) {
            all_false = false
        }
    }
    if !saw || all_false {
        TriState::False
    } else {
        TriState::Indeterminate
    }
}
fn sha256(bytes: &[u8]) -> String {
    use sha2::{Digest, Sha256};
    Sha256::digest(bytes)
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    const EXPECTED: &[(&str, &str, [Option<f64>; 3])] = &[
        (
            "short_half_life_lt5y_sum",
            "Ci/m3",
            [Some(700.0), None, None],
        ),
        ("H-3", "Ci/m3", [Some(40.0), Some(110_000_000.0), None]),
        ("C-14", "Ci/m3", [Some(0.8), Some(0.8), Some(8.0)]),
        (
            "C-14_activated_metal",
            "Ci/m3",
            [Some(8.0), Some(8.0), Some(80.0)],
        ),
        ("Cl-36", "Ci/m3", [Some(110.0), Some(110.0), Some(1100.0)]),
        ("Co-60", "Ci/m3", [Some(700.0), Some(67000.0), None]),
        ("Ni-59", "Ci/m3", [Some(2.2), Some(2.2), Some(22.0)]),
        (
            "Ni-59_activated_metal",
            "Ci/m3",
            [Some(22.0), Some(22.0), Some(220.0)],
        ),
        ("Ni-63", "Ci/m3", [Some(3.5), Some(70.0), Some(700.0)]),
        (
            "Ni-63_activated_metal",
            "Ci/m3",
            [Some(35.0), Some(700.0), Some(7000.0)],
        ),
        ("Sr-90", "Ci/m3", [Some(0.04), Some(150.0), Some(7000.0)]),
        ("Nb-94", "Ci/m3", [Some(0.02), Some(0.02), Some(0.2)]),
        (
            "Nb-94_activated_metal",
            "Ci/m3",
            [Some(0.2), Some(0.2), Some(2.0)],
        ),
        ("Tc-99", "Ci/m3", [Some(0.3), Some(0.3), Some(3.0)]),
        ("I-129", "Ci/m3", [Some(0.008), Some(0.008), Some(0.08)]),
        ("Cs-135", "Ci/m3", [Some(84.0), Some(84.0), Some(840.0)]),
        ("Cs-137", "Ci/m3", [Some(1.0), Some(44.0), Some(460.0)]),
        ("Eu-152", "Ci/m3", [Some(0.06), Some(6.2), None]),
        (
            "Eu-154",
            "Ci/m3",
            [Some(0.02), Some(1.5), Some(5_100_000.0)],
        ),
        ("U-235", "Ci/m3", [Some(0.04), Some(0.04), Some(0.4)]),
        ("U-238", "Ci/m3", [Some(0.05), Some(0.05), Some(0.5)]),
        ("Np-237", "nCi/g", [Some(10.0), Some(10.0), Some(100.0)]),
        ("Pu-241", "nCi/g", [Some(350.0), Some(350.0), Some(3500.0)]),
        (
            "alpha_transuranic_gt5y",
            "nCi/g",
            [Some(10.0), Some(10.0), Some(100.0)],
        ),
        (
            "Cm-242",
            "nCi/g",
            [Some(2000.0), Some(2000.0), Some(20000.0)],
        ),
    ];

    #[test]
    fn bundled_table_has_exact_25_rows_and_all_75_class_cells() {
        let pack = DraftRulePack::bundled().unwrap();
        assert_eq!(pack.wire.rows.len(), 25);
        for ((id, unit, limits), row) in EXPECTED.iter().zip(&pack.wire.rows) {
            assert_eq!(&row.id, id);
            assert_eq!(&row.unit, unit);
            assert_eq!(row.limits.as_slice(), limits);
        }
        assert_eq!(
            pack.wire.rows.iter().map(|r| r.limits.len()).sum::<usize>(),
            75
        );
        assert_eq!(
            pack.wire
                .rows
                .iter()
                .find(|r| r.id == "Cs-137")
                .unwrap()
                .limits[2],
            Some(460.0)
        );
        assert_eq!(
            pack.wire
                .rows
                .iter()
                .find(|r| r.id == "Nb-94_activated_metal")
                .unwrap()
                .limits,
            [Some(0.2), Some(0.2), Some(2.0)]
        );
    }

    #[test]
    fn all_75_row_class_cells_compare_below_equal_and_above_boundaries() {
        // Classes are injected here to check every printed cell. The CLI uses
        // computed Part 61 classes; some table cells cannot occur end to end.
        let rules = RulePack::bundled().unwrap();
        let draft = DraftRulePack::bundled().unwrap();
        let decl_general = validate_declarations(IntrusionDeclarations {
            waste_form: WasteForm::Equipment,
            inventory_coverage: InventoryCoverage::Complete,
            unbounded_inventory_reasons: vec![],
            required_h3_missing: false,
            site_wac: None,
            dot_rq: None,
        })
        .unwrap();
        let decl_metal = validate_declarations(IntrusionDeclarations {
            waste_form: WasteForm::Metal,
            inventory_coverage: InventoryCoverage::Complete,
            unbounded_inventory_reasons: vec![],
            required_h3_missing: false,
            site_wac: None,
            dot_rq: None,
        })
        .unwrap();
        let geometry = WasteGeometry {
            mass_g: 1.0,
            displaced_volume_cm3: 1.0,
        };
        let z_for = |key: &str| -> i32 {
            match actinv_data::composition::material_key(key).unwrap() {
                actinv_data::composition::MaterialKey::Nuclide { za, .. } => za / 1000,
                _ => panic!("test member must be a nuclide: {key}"),
            }
        };
        for (row_index, (expected_id, expected_unit, expected_limits)) in
            EXPECTED.iter().enumerate()
        {
            let row = draft
                .wire
                .rows
                .iter()
                .find(|row| row.id == *expected_id)
                .unwrap();
            let is_metal = row.applicability == "activated_metal";
            let declarations = if is_metal { &decl_metal } else { &decl_general };
            let waste_type = if is_metal {
                WasteType::ActivatedMetal
            } else {
                WasteType::General
            };
            assert_eq!(row.unit, *expected_unit);
            let key = row
                .members
                .first()
                .map(|x| normalize_nuclide_key(x).unwrap())
                .unwrap_or_else(|| "Xe135".into());
            let props = BTreeMap::from([(
                key.clone(),
                NuclideProperties {
                    z: z_for(&key),
                    half_life_s: if key == "Xe135" { 1.0e4 } else { 1.0e9 },
                    alpha_emitting: key.starts_with("Pu")
                        || key.starts_with("Am")
                        || key.starts_with("Cm"),
                },
            )]);
            for (column, limit) in expected_limits.iter().enumerate() {
                let class = [WasteClass::A, WasteClass::B, WasteClass::C][column];
                let scenarios: Vec<(f64, &str)> = match limit {
                    Some(value) => vec![
                        (*value * 0.99, "below"),
                        (*value, "at_limit"),
                        (*value * 1.01, "above"),
                    ],
                    None => vec![(1.0, "no_numeric_limit")],
                };
                for (concentration, want) in scenarios {
                    let activity = if row.unit == "Ci/m3" {
                        concentration * geometry.displaced_volume_cm3 * CI_BQ_CM3
                    } else {
                        concentration * geometry.mass_g * NCI_BQ_G
                    };
                    let activities = BTreeMap::from([(key.clone(), activity)]);
                    let actual = evaluate_intrusion_target(
                        &draft,
                        IntrusionTargetInput {
                            part61_rules: &rules,
                            waste_type,
                            geometry,
                            activation_activities_bq: &activities,
                            external_tritium_activity_bq: 0.0,
                            nuclide_properties: &props,
                            nominal_class: class,
                            declarations,
                        },
                    )
                    .unwrap();
                    let selected = actual
                        .rows
                        .iter()
                        .find(|candidate| candidate.row_id == row.id)
                        .unwrap();
                    assert_eq!(
                        selected.selected_class,
                        Some(class),
                        "row {} column {}",
                        row_index,
                        column
                    );
                    assert_eq!(
                        selected.relation, want,
                        "row {} column {} concentration {}",
                        row_index, column, concentration
                    );
                    if let Some(value) = limit {
                        assert!(
                            (selected.known_concentration - concentration).abs()
                                <= 1e-12 * value.abs().max(1.0)
                        );
                    }
                }
            }
        }
    }

    #[test]
    fn external_tritium_is_merged_once_and_keeps_separate_evidence() {
        let rules = RulePack::bundled().unwrap();
        let draft = DraftRulePack::bundled().unwrap();
        let activity = BTreeMap::from([("H3".to_string(), 10.0)]);
        let props = BTreeMap::from([(
            "H3".to_string(),
            NuclideProperties {
                z: 1,
                half_life_s: 3.887e8,
                alpha_emitting: false,
            },
        )]);
        let decl = validate_declarations(IntrusionDeclarations {
            waste_form: WasteForm::Equipment,
            inventory_coverage: InventoryCoverage::Complete,
            unbounded_inventory_reasons: vec![],
            required_h3_missing: false,
            site_wac: None,
            dot_rq: None,
        })
        .unwrap();
        let result = evaluate_intrusion_target(
            &draft,
            IntrusionTargetInput {
                part61_rules: &rules,
                waste_type: WasteType::General,
                geometry: WasteGeometry {
                    mass_g: 1.0,
                    displaced_volume_cm3: 1.0,
                },
                activation_activities_bq: &activity,
                external_tritium_activity_bq: 10.0,
                nuclide_properties: &props,
                nominal_class: WasteClass::A,
                declarations: &decl,
            },
        )
        .unwrap();
        assert_eq!(result.inventory_activity_bq.get("H3"), Some(&20.0));
        assert_eq!(result.external_tritium_activity_bq, 10.0);
        assert_eq!(result.known_total_activity_bq, 20.0);
        assert_eq!(result.container_total_activity_bq, Some(20.0));
    }

    #[test]
    fn strict_wac_and_share_boundaries_keep_the_frozen_comparators() {
        let rules = RulePack::bundled().unwrap();
        let draft = DraftRulePack::bundled().unwrap();
        let props = BTreeMap::from([(
            "C14".to_string(),
            NuclideProperties {
                z: 6,
                half_life_s: 1.808e11,
                alpha_emitting: false,
            },
        )]);
        for (amount, want) in [(37.0, false), (37.037, true)] {
            let activities = BTreeMap::from([("C14".to_string(), amount)]);
            let wac = SiteWacDeclaration {
                source: "synthetic".into(),
                membership_coverage: MembershipCoverage::Complete,
                nuclides: BTreeMap::from([(
                    "C14".into(),
                    WacEntry::Listed {
                        limit: WacLimit {
                            value: 100.0,
                            unit: ConcentrationUnit::NCiPerG,
                        },
                    },
                )]),
            };
            let decl = validate_declarations(IntrusionDeclarations {
                waste_form: WasteForm::Equipment,
                inventory_coverage: InventoryCoverage::Complete,
                unbounded_inventory_reasons: vec![],
                required_h3_missing: false,
                site_wac: Some(wac),
                dot_rq: None,
            })
            .unwrap();
            let out = evaluate_intrusion_target(
                &draft,
                IntrusionTargetInput {
                    part61_rules: &rules,
                    waste_type: WasteType::General,
                    geometry: WasteGeometry {
                        mass_g: 1.0,
                        displaced_volume_cm3: 1.0,
                    },
                    activation_activities_bq: &activities,
                    external_tritium_activity_bq: 0.0,
                    nuclide_properties: &props,
                    nominal_class: WasteClass::A,
                    declarations: &decl,
                },
            )
            .unwrap();
            assert_eq!(
                matches!(
                    out.nuclides[0].predicates["wac_fraction"].status,
                    TriState::True
                ),
                want
            );
        }
    }

    #[test]
    fn unknown_short_group_membership_remains_conditional_below_the_known_limit() {
        let rules = RulePack::bundled().unwrap();
        let draft = DraftRulePack::bundled().unwrap();
        let activities = BTreeMap::from([("Ni63".to_string(), 1.0), ("Xe135".to_string(), 1.0)]);
        let props = BTreeMap::from([(
            "Ni63".to_string(),
            NuclideProperties {
                z: 28,
                half_life_s: 1.0e7,
                alpha_emitting: false,
            },
        )]);
        let wac = SiteWacDeclaration {
            source: "synthetic".into(),
            membership_coverage: MembershipCoverage::Complete,
            nuclides: BTreeMap::from([(
                "Ni63".into(),
                WacEntry::Listed {
                    limit: WacLimit {
                        value: 1.0e-9,
                        unit: ConcentrationUnit::NCiPerG,
                    },
                },
            )]),
        };
        let declarations = validate_declarations(IntrusionDeclarations {
            waste_form: WasteForm::Equipment,
            inventory_coverage: InventoryCoverage::Complete,
            unbounded_inventory_reasons: vec![],
            required_h3_missing: false,
            site_wac: Some(wac),
            dot_rq: None,
        })
        .unwrap();
        let result = evaluate_intrusion_target(
            &draft,
            IntrusionTargetInput {
                part61_rules: &rules,
                waste_type: WasteType::General,
                geometry: WasteGeometry {
                    mass_g: 1.0,
                    displaced_volume_cm3: 1.0,
                },
                activation_activities_bq: &activities,
                external_tritium_activity_bq: 0.0,
                nuclide_properties: &props,
                nominal_class: WasteClass::A,
                declarations: &declarations,
            },
        )
        .unwrap();
        let row = result
            .rows
            .iter()
            .find(|row| row.row_id == "short_half_life_lt5y_sum")
            .unwrap();
        assert_eq!(row.members, vec!["Ni63"]);
        assert!(!row.membership_complete);
        assert_eq!(row.relation, "below");
        assert_eq!(row.assessment_indicator, AssessmentIndicator::Indeterminate);
        assert_eq!(
            result.assessment_indicator,
            AssessmentIndicator::Indeterminate
        );
    }

    #[test]
    fn malformed_declarations_and_alias_duplicates_are_refused() {
        let bad = IntrusionDeclarations {
            waste_form: WasteForm::Equipment,
            inventory_coverage: InventoryCoverage::Complete,
            unbounded_inventory_reasons: vec!["incomplete".into()],
            required_h3_missing: false,
            site_wac: None,
            dot_rq: None,
        };
        assert!(validate_declarations(bad).is_err());
        let dup = SiteWacDeclaration {
            source: "source".into(),
            membership_coverage: MembershipCoverage::Complete,
            nuclides: BTreeMap::from([
                ("H3".into(), WacEntry::Unlisted),
                ("H-3".into(), WacEntry::Unlisted),
            ]),
        };
        assert!(validate_declarations(IntrusionDeclarations {
            waste_form: WasteForm::Equipment,
            inventory_coverage: InventoryCoverage::Complete,
            unbounded_inventory_reasons: vec![],
            required_h3_missing: false,
            site_wac: Some(dup),
            dot_rq: None
        })
        .is_err());
    }
}
