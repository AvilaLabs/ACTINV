//! Conservative classification envelopes for declared whole-component activity boxes.
//!
//! These bounds are deterministic input intervals, not probability distributions or propagated
//! nuclear-data uncertainty. The interval evaluator reuses the nominal Part 61 evaluator at each
//! endpoint and widens contributor counts naturally by evaluating lower and upper inventories
//! separately.

use crate::waste::{
    evaluate_component, ClassConstraint, Coverage, Evaluation, NuclideProperties, RulePack,
    WasteClass, WasteGeometry, WasteType,
};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ActivityInterval {
    pub lower_bq: f64,
    pub upper_bq: f64,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct RowFractionEndpoint {
    pub concentration: f64,
    pub limit: Option<f64>,
    pub fraction: Option<f64>,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct RowFractionRange {
    pub table: u8,
    pub column: Option<u8>,
    pub row_id: String,
    pub nuclide: String,
    pub unit: String,
    pub lower: RowFractionEndpoint,
    pub upper: RowFractionEndpoint,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ConstraintEndpoint {
    pub source_sum_fraction: f64,
    pub normalized_sum: f64,
    pub normalized_margin: f64,
    pub strict: bool,
    pub passes: bool,
    pub contributor_count: usize,
    pub contributors: Vec<String>,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ConstraintRange {
    pub target_class: WasteClass,
    pub table: u8,
    /// Table 1 uses `None`; Table 2 uses its one-based column number.
    pub column: Option<u8>,
    pub lower: ConstraintEndpoint,
    pub upper: ConstraintEndpoint,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct BoundsEvaluation {
    pub lower: Evaluation,
    pub upper: Evaluation,
    pub coverage: Coverage,
    /// Ordered superset of classes admitted by the endpoint bounds, or `[Unknown]` when incomplete.
    pub class_envelope: Vec<WasteClass>,
    pub class_is_stable: bool,
    pub stable_class: Option<WasteClass>,
    /// `Some(true)` for a complete envelope; `None` where coverage cannot support one.
    pub conservative_superset: Option<bool>,
    /// Canonical ACTINV keys and their declared total-activity bounds in Bq.
    pub evaluated_activity_bounds_bq: BTreeMap<String, ActivityInterval>,
    pub row_fraction_ranges: Vec<RowFractionRange>,
    /// The six Table 1/Table 2 constraints for Classes A, B and C.
    pub constraint_ranges: Vec<ConstraintRange>,
}

/// Evaluate a conservative class envelope from declared whole-component nuclide activities.
///
/// `activity_bounds_bq` maps nuclides to simultaneous rectangular `[lower_bq, upper_bq]`
/// intervals. The caller merges any separately declared external H-3 interval before this call.
/// A missing property record for any upper-positive nuclide makes both endpoints unknown while
/// preserving each endpoint's calculated-only known-subset classification.
pub fn evaluate_component_bounds(
    pack: &RulePack,
    waste_type: WasteType,
    geometry: WasteGeometry,
    activity_bounds_bq: &BTreeMap<String, ActivityInterval>,
    properties: &BTreeMap<String, NuclideProperties>,
    declared_coverage: Coverage,
) -> Result<BoundsEvaluation, String> {
    if !geometry.mass_g.is_finite() || geometry.mass_g <= 0.0 {
        return Err("component mass must be positive and finite".into());
    }
    if !geometry.displaced_volume_cm3.is_finite() || geometry.displaced_volume_cm3 <= 0.0 {
        return Err("component displaced volume must be positive and finite".into());
    }

    let mut canonical_bounds = BTreeMap::new();
    let mut lower_total = 0.0;
    let mut upper_total = 0.0;
    for (raw, interval) in activity_bounds_bq {
        if !interval.lower_bq.is_finite()
            || !interval.upper_bq.is_finite()
            || interval.lower_bq < 0.0
            || interval.upper_bq < interval.lower_bq
        {
            return Err(format!(
                "activity interval for '{raw}' must be finite and satisfy 0 <= lower_bq <= upper_bq"
            ));
        }
        let key = crate::waste::normalize_nuclide_key(raw)?;
        if canonical_bounds.insert(key.clone(), *interval).is_some() {
            return Err(format!(
                "duplicate activity keys after identity normalization: '{key}'"
            ));
        }
        lower_total = finite_total(lower_total, interval.lower_bq, "lower activity total")?;
        upper_total = finite_total(upper_total, interval.upper_bq, "upper activity total")?;
    }
    // Ensure the raw endpoint maps are themselves finite before nominal evaluation.
    let lower_activities = canonical_bounds
        .iter()
        .map(|(key, interval)| (key.clone(), interval.lower_bq))
        .collect::<BTreeMap<_, _>>();
    let upper_activities = canonical_bounds
        .iter()
        .map(|(key, interval)| (key.clone(), interval.upper_bq))
        .collect::<BTreeMap<_, _>>();
    let mut lower = evaluate_component(pack, waste_type, geometry, &lower_activities, properties)?;
    let mut upper = evaluate_component(pack, waste_type, geometry, &upper_activities, properties)?;

    let unknown_upper: BTreeSet<String> = upper.unknown_nuclides.iter().cloned().collect();
    let complete = declared_coverage == Coverage::Complete && unknown_upper.is_empty();
    if !complete {
        let mut unknown = unknown_upper;
        unknown.extend(lower.unknown_nuclides.iter().cloned());
        unknown.extend(upper.unknown_nuclides.iter().cloned());
        force_unknown(&mut lower, &unknown);
        force_unknown(&mut upper, &unknown);
    }
    let coverage = if complete {
        Coverage::Complete
    } else {
        Coverage::Incomplete
    };

    let (class_envelope, class_is_stable, stable_class, conservative_superset) = if complete {
        let envelope = class_range(lower.class, upper.class)?;
        let stable = lower.class == upper.class;
        (envelope, stable, stable.then_some(lower.class), Some(true))
    } else {
        (vec![WasteClass::Unknown], false, None, None)
    };
    let row_fraction_ranges = row_ranges(&lower, &upper)?;
    let constraint_ranges = constraint_ranges(&lower, &upper)?;

    // Totals are intentionally only checked for overflow; their sum has no classification meaning.
    let _ = (lower_total, upper_total);
    Ok(BoundsEvaluation {
        lower,
        upper,
        coverage,
        class_envelope,
        class_is_stable,
        stable_class,
        conservative_superset,
        evaluated_activity_bounds_bq: canonical_bounds,
        row_fraction_ranges,
        constraint_ranges,
    })
}

fn finite_total(current: f64, value: f64, what: &str) -> Result<f64, String> {
    let total = current + value;
    if total.is_finite() {
        Ok(total)
    } else {
        Err(format!("{what} overflows finite range"))
    }
}

fn force_unknown(evaluation: &mut Evaluation, unknown: &BTreeSet<String>) {
    evaluation.class = WasteClass::Unknown;
    evaluation.coverage = Coverage::Incomplete;
    evaluation.unknown_nuclides = unknown.iter().cloned().collect();
}

fn class_rank(class: WasteClass) -> Result<u8, String> {
    match class {
        WasteClass::A => Ok(0),
        WasteClass::B => Ok(1),
        WasteClass::C => Ok(2),
        WasteClass::AboveClassC => Ok(3),
        WasteClass::Unknown => Err("cannot order an unknown class in a complete envelope".into()),
    }
}

fn class_at(rank: u8) -> WasteClass {
    match rank {
        0 => WasteClass::A,
        1 => WasteClass::B,
        2 => WasteClass::C,
        _ => WasteClass::AboveClassC,
    }
}

fn class_range(lower: WasteClass, upper: WasteClass) -> Result<Vec<WasteClass>, String> {
    let low = class_rank(lower)?;
    let high = class_rank(upper)?;
    if low > high {
        return Err(format!(
            "nonmonotone class endpoints: lower is {lower:?}, upper is {upper:?}"
        ));
    }
    Ok((low..=high).map(class_at).collect())
}

fn row_ranges(lower: &Evaluation, upper: &Evaluation) -> Result<Vec<RowFractionRange>, String> {
    type Key = (u8, Option<u8>, String, String, String);
    type RowPair<'a> = (
        Option<&'a crate::waste::RowFraction>,
        Option<&'a crate::waste::RowFraction>,
    );
    let mut rows: BTreeMap<Key, RowPair<'_>> = BTreeMap::new();
    for row in &lower.row_fractions {
        let key = (
            row.table,
            row.column,
            row.row_id.clone(),
            row.nuclide.clone(),
            row.unit.clone(),
        );
        rows.entry(key).or_default().0 = Some(row);
    }
    for row in &upper.row_fractions {
        let key = (
            row.table,
            row.column,
            row.row_id.clone(),
            row.nuclide.clone(),
            row.unit.clone(),
        );
        rows.entry(key).or_default().1 = Some(row);
    }
    rows.into_iter()
        .map(|((table, column, row_id, nuclide, unit), (lo, hi))| {
            let exemplar = lo.or(hi).ok_or("empty row range")?;
            let lower = endpoint_row(lo, exemplar);
            let upper = endpoint_row(hi, exemplar);
            Ok(RowFractionRange {
                table,
                column,
                row_id,
                nuclide,
                unit,
                lower,
                upper,
            })
        })
        .collect()
}

fn endpoint_row(
    row: Option<&crate::waste::RowFraction>,
    exemplar: &crate::waste::RowFraction,
) -> RowFractionEndpoint {
    match row {
        Some(value) => RowFractionEndpoint {
            concentration: value.concentration,
            limit: value.limit,
            fraction: value.fraction,
        },
        None => RowFractionEndpoint {
            concentration: 0.0,
            limit: exemplar.limit,
            fraction: exemplar.limit.map(|_| 0.0),
        },
    }
}

fn constraint_ranges(
    lower: &Evaluation,
    upper: &Evaluation,
) -> Result<Vec<ConstraintRange>, String> {
    let mut ranges = Vec::with_capacity(6);
    for (target_class, table, column) in [
        (WasteClass::A, 1, None),
        (WasteClass::A, 2, Some(1)),
        (WasteClass::B, 1, None),
        (WasteClass::B, 2, Some(2)),
        (WasteClass::C, 1, None),
        (WasteClass::C, 2, Some(3)),
    ] {
        let lo = find_constraint(&lower.constraints, target_class, table, column)?;
        let hi = find_constraint(&upper.constraints, target_class, table, column)?;
        ranges.push(ConstraintRange {
            target_class,
            table,
            column,
            lower: ConstraintEndpoint::from(lo),
            upper: ConstraintEndpoint::from(hi),
        });
    }
    Ok(ranges)
}

fn find_constraint(
    constraints: &[ClassConstraint],
    target: WasteClass,
    table: u8,
    column: Option<u8>,
) -> Result<&ClassConstraint, String> {
    constraints
        .iter()
        .find(|item| item.target_class == target && item.table == table && item.column == column)
        .ok_or_else(|| format!("missing Table {table} class {target:?} constraint"))
}

impl From<&ClassConstraint> for ConstraintEndpoint {
    fn from(item: &ClassConstraint) -> Self {
        Self {
            source_sum_fraction: item.source_sum_fraction,
            normalized_sum: item.normalized_sum,
            normalized_margin: item.normalized_margin,
            strict: item.strict,
            passes: item.passes,
            contributor_count: item.contributor_count,
            contributors: item.contributors.clone(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn evaluate(
        activities: &[(&str, f64, f64)],
        properties: &[(&str, NuclideProperties)],
        coverage: Coverage,
        waste_type: WasteType,
    ) -> Result<BoundsEvaluation, String> {
        let pack = RulePack::bundled()?;
        let bounds = activities
            .iter()
            .map(|(key, lower, upper)| {
                (
                    (*key).to_string(),
                    ActivityInterval {
                        lower_bq: *lower,
                        upper_bq: *upper,
                    },
                )
            })
            .collect();
        let props = properties
            .iter()
            .map(|(key, value)| ((*key).to_string(), *value))
            .collect();
        evaluate_component_bounds(
            &pack,
            waste_type,
            WasteGeometry {
                mass_g: 1.0,
                displaced_volume_cm3: 1.0,
            },
            &bounds,
            &props,
            coverage,
        )
    }

    fn props(z: i32, half_life_s: f64, alpha_emitting: bool) -> NuclideProperties {
        NuclideProperties {
            z,
            half_life_s,
            alpha_emitting,
        }
    }

    #[test]
    fn complete_zero_and_unlisted_intervals_are_stable_a() {
        let empty = evaluate(&[], &[], Coverage::Complete, WasteType::General).unwrap();
        assert_eq!(empty.lower.class, WasteClass::A);
        assert_eq!(empty.upper.class, WasteClass::A);
        assert_eq!(empty.class_envelope, vec![WasteClass::A]);
        assert_eq!(empty.stable_class, Some(WasteClass::A));
        assert_eq!(empty.conservative_superset, Some(true));

        let unlisted = evaluate(
            &[("Fe55", 100.0, 200.0)],
            &[("Fe55", props(26, 5.0 * 31_557_600.0, false))],
            Coverage::Complete,
            WasteType::General,
        )
        .unwrap();
        assert_eq!(unlisted.class_envelope, vec![WasteClass::A]);
        assert!(unlisted.class_is_stable);
        assert_eq!(unlisted.lower.unlisted_activity_bq, 100.0);
        assert_eq!(unlisted.upper.unlisted_activity_bq, 200.0);
    }

    #[test]
    fn incomplete_empty_inventory_is_unknown_but_keeps_calculated_class() {
        let result = evaluate(&[], &[], Coverage::Incomplete, WasteType::General).unwrap();
        assert_eq!(result.coverage, Coverage::Incomplete);
        assert_eq!(result.class_envelope, vec![WasteClass::Unknown]);
        assert_eq!(result.lower.class, WasteClass::Unknown);
        assert_eq!(result.upper.class, WasteClass::Unknown);
        assert_eq!(result.lower.calculated_only_class, WasteClass::A);
        assert_eq!(result.conservative_superset, None);
        assert_eq!(result.stable_class, None);
    }

    #[test]
    fn zero_crossing_contributor_makes_exact_table1_boundary_strict() {
        let result = evaluate(
            &[
                ("C14", 0.05 * 8.0 * 37_000.0, 0.05 * 8.0 * 37_000.0),
                ("Tc99", 0.0, 5_550.0),
            ],
            &[
                ("C14", props(6, 10.0 * 31_557_600.0, false)),
                ("Tc99", props(43, 10.0 * 31_557_600.0, false)),
            ],
            Coverage::Complete,
            WasteType::General,
        )
        .unwrap();
        assert_eq!(result.lower.class, WasteClass::A);
        assert_eq!(result.upper.class, WasteClass::C);
        assert_eq!(
            result.class_envelope,
            vec![WasteClass::A, WasteClass::B, WasteClass::C]
        );
        let t1_a = result
            .constraint_ranges
            .iter()
            .find(|x| x.target_class == WasteClass::A && x.table == 1)
            .unwrap();
        assert!(!t1_a.lower.strict);
        assert!(t1_a.upper.strict);
        assert_eq!(t1_a.upper.contributor_count, 2);
        assert_eq!(t1_a.upper.normalized_sum, 1.0);
        assert!(!t1_a.upper.passes);
    }

    #[test]
    fn upper_positive_unknown_metadata_forces_both_endpoints_unknown() {
        let result = evaluate(
            &[("C14", 0.0, 100.0)],
            &[],
            Coverage::Complete,
            WasteType::General,
        )
        .unwrap();
        assert_eq!(result.lower.class, WasteClass::Unknown);
        assert_eq!(result.upper.class, WasteClass::Unknown);
        assert_eq!(result.lower.calculated_only_class, WasteClass::A);
        assert_eq!(result.upper.unknown_nuclides, vec!["C14"]);
        assert_eq!(result.class_envelope, vec![WasteClass::Unknown]);
        assert!(!result.class_is_stable);
        assert_eq!(result.stable_class, None);
    }

    #[test]
    fn table2_null_columns_preserve_null_fraction_ranges() {
        let result = evaluate(
            &[("Co60", 5_180_000.0, 10_360_000.0)],
            &[("Co60", props(27, 31_557_600.0, false))],
            Coverage::Complete,
            WasteType::General,
        )
        .unwrap();
        let co60 = result
            .row_fraction_ranges
            .iter()
            .find(|r| r.nuclide == "Co60")
            .unwrap();
        assert_eq!(co60.lower.fraction, Some(0.2));
        assert_eq!(co60.upper.fraction, Some(0.4));
        let co60_col2 = result
            .row_fraction_ranges
            .iter()
            .find(|r| r.nuclide == "Co60" && r.column == Some(2))
            .unwrap();
        assert_eq!(co60_col2.lower.fraction, None);
        assert_eq!(co60_col2.upper.fraction, None);
        for (class, column) in [(WasteClass::B, 2), (WasteClass::C, 3)] {
            let constraint = result
                .constraint_ranges
                .iter()
                .find(|x| x.target_class == class && x.table == 2 && x.column == Some(column))
                .unwrap();
            assert_eq!(constraint.lower.normalized_sum, 0.0);
            assert_eq!(constraint.upper.normalized_sum, 0.0);
            assert!(!constraint.lower.strict && !constraint.upper.strict);
            assert!(
                constraint.lower.contributors.is_empty()
                    && constraint.upper.contributors.is_empty()
            );
        }
        assert!(result.constraint_ranges.iter().all(
            |x| x.lower.normalized_margin.is_finite() && x.upper.normalized_margin.is_finite()
        ));
    }

    #[test]
    fn aliases_invalid_intervals_bad_properties_and_overflow_are_rejected() {
        let pack = RulePack::bundled().unwrap();
        let geometry = WasteGeometry {
            mass_g: 1.0,
            displaced_volume_cm3: 1.0,
        };
        let properties = BTreeMap::from([("C14".to_string(), props(6, 10.0, false))]);
        let aliases = BTreeMap::from([
            (
                "C14".to_string(),
                ActivityInterval {
                    lower_bq: 0.0,
                    upper_bq: 1.0,
                },
            ),
            (
                "C-14".to_string(),
                ActivityInterval {
                    lower_bq: 0.0,
                    upper_bq: 1.0,
                },
            ),
        ]);
        assert!(evaluate_component_bounds(
            &pack,
            WasteType::General,
            geometry,
            &aliases,
            &properties,
            Coverage::Complete
        )
        .is_err());
        let invalid = BTreeMap::from([(
            "C14".to_string(),
            ActivityInterval {
                lower_bq: 2.0,
                upper_bq: 1.0,
            },
        )]);
        assert!(evaluate_component_bounds(
            &pack,
            WasteType::General,
            geometry,
            &invalid,
            &properties,
            Coverage::Complete
        )
        .is_err());
        let overflow = BTreeMap::from([
            (
                "C14".to_string(),
                ActivityInterval {
                    lower_bq: 0.0,
                    upper_bq: f64::MAX,
                },
            ),
            (
                "Tc99".to_string(),
                ActivityInterval {
                    lower_bq: 0.0,
                    upper_bq: f64::MAX,
                },
            ),
        ]);
        assert!(evaluate_component_bounds(
            &pack,
            WasteType::General,
            geometry,
            &overflow,
            &properties,
            Coverage::Complete
        )
        .is_err());
        let bad_properties = BTreeMap::from([("C14".to_string(), props(7, 10.0, false))]);
        assert!(evaluate_component_bounds(
            &pack,
            WasteType::General,
            geometry,
            &BTreeMap::new(),
            &bad_properties,
            Coverage::Complete
        )
        .is_err());
    }

    #[test]
    fn row_ranges_fill_absent_endpoint_with_zero_and_constraint_margins_are_signed() {
        let limit_activity = 0.2 * 8.0 * 37_000.0;
        let result = evaluate(
            &[("C14", 0.0, limit_activity)],
            &[("C14", props(6, 10.0 * 31_557_600.0, false))],
            Coverage::Complete,
            WasteType::General,
        )
        .unwrap();
        let row = result
            .row_fraction_ranges
            .iter()
            .find(|x| x.nuclide == "C14")
            .unwrap();
        assert_eq!(row.lower.concentration, 0.0);
        assert_eq!(row.lower.fraction, Some(0.0));
        assert!(row.upper.fraction.unwrap() > 0.0);
        let a_t1 = result
            .constraint_ranges
            .iter()
            .find(|x| x.target_class == WasteClass::A && x.table == 1)
            .unwrap();
        assert!(a_t1.lower.normalized_margin > 0.0);
        assert!(a_t1.upper.normalized_margin < 0.0);
    }
}
