//! Conservative projection of declared affine responses over bounded compositions.
//!
//! This module treats coordinate names as opaque. The CLI is responsible for validating that
//! they are canonical natural-element identities before calling the numerical core.

use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct WeightInterval {
    pub lower_wt_percent: f64,
    pub upper_wt_percent: f64,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ResponseProjection {
    pub lower_bq: f64,
    pub upper_bq: f64,
    pub min_witness_wt_percent: BTreeMap<String, f64>,
    pub max_witness_wt_percent: BTreeMap<String, f64>,
    pub lower_dual_lambda: f64,
    pub upper_dual_lambda: f64,
}

#[derive(Clone, Copy, Debug)]
struct Interval {
    lo: f64,
    hi: f64,
}

impl Interval {
    fn exact(value: f64) -> Self {
        Self {
            lo: value,
            hi: value,
        }
    }

    fn sub(self, rhs: Self) -> Result<Self, String> {
        let lo = sub_down(self.lo, rhs.hi)?;
        let hi = sub_up(self.hi, rhs.lo)?;
        Ok(Self { lo, hi })
    }

    fn mul(self, rhs: Self) -> Result<Self, String> {
        let products = [
            self.lo * rhs.lo,
            self.lo * rhs.hi,
            self.hi * rhs.lo,
            self.hi * rhs.hi,
        ];
        if products.iter().any(|value| !value.is_finite()) {
            return Err("non-finite intermediate in composition bound".into());
        }
        let minimum = products.iter().copied().fold(f64::INFINITY, f64::min);
        let maximum = products.iter().copied().fold(f64::NEG_INFINITY, f64::max);
        Ok(Self {
            lo: round_down(minimum),
            hi: round_up(maximum),
        })
    }
}

/// Project one nuclide's affine response over a bounded wt-percent simplex.
///
/// `response_bq_per_g` gives the response for each pure (100 wt%) coordinate. The feasible
/// compositions satisfy every supplied interval and sum to exactly 100 wt%. The returned Bq
/// bounds enclose the exact extrema for the binary floating-point input values.
pub fn project_response(
    weights: &BTreeMap<String, WeightInterval>,
    response_bq_per_g: &BTreeMap<String, f64>,
    mass_g: f64,
) -> Result<ResponseProjection, String> {
    if weights.is_empty() || weights.len() > 64 {
        return Err("composition must contain between 1 and 64 coordinates".into());
    }
    if !mass_g.is_finite() || mass_g <= 0.0 {
        return Err("component mass must be positive and finite".into());
    }
    if weights.keys().ne(response_bq_per_g.keys()) {
        return Err("composition and response coordinate sets must match exactly".into());
    }

    let mut lower_sum = 0.0;
    let mut upper_sum = 0.0;
    for (name, bound) in weights {
        if name.is_empty()
            || !bound.lower_wt_percent.is_finite()
            || !bound.upper_wt_percent.is_finite()
            || bound.lower_wt_percent < 0.0
            || bound.upper_wt_percent > 100.0
            || bound.lower_wt_percent > bound.upper_wt_percent
        {
            return Err(format!("invalid wt-percent interval for '{name}'"));
        }
        let response = response_bq_per_g[name];
        if !response.is_finite() || response < 0.0 {
            return Err(format!(
                "response for '{name}' must be finite and nonnegative"
            ));
        }
        lower_sum += bound.lower_wt_percent;
        upper_sum += bound.upper_wt_percent;
        if !lower_sum.is_finite() || !upper_sum.is_finite() {
            return Err("composition interval totals overflow".into());
        }
    }
    if exact_sum_cmp_target(weights.values().map(|bound| bound.lower_wt_percent), 100.0)?
        == std::cmp::Ordering::Greater
        || exact_sum_cmp_target(weights.values().map(|bound| bound.upper_wt_percent), 100.0)?
            == std::cmp::Ordering::Less
    {
        return Err("composition intervals do not admit a 100 wt-percent total".into());
    }

    let min_witness = greedy_witness(weights, response_bq_per_g, false)?;
    let max_witness = greedy_witness(weights, response_bq_per_g, true)?;
    if response_bq_per_g.values().all(|value| *value == 0.0) {
        return Ok(ResponseProjection {
            lower_bq: 0.0,
            upper_bq: 0.0,
            min_witness_wt_percent: min_witness,
            max_witness_wt_percent: max_witness,
            lower_dual_lambda: 0.0,
            upper_dual_lambda: 0.0,
        });
    }

    let mut candidates = vec![0.0];
    let mut coefficient_intervals = BTreeMap::new();
    for (name, response) in response_bq_per_g {
        let divided_value = *response / 100.0;
        let divided = if *response == 0.0 {
            Interval::exact(0.0)
        } else if divided_value.is_finite() {
            Interval {
                lo: round_down(divided_value),
                hi: round_up(divided_value),
            }
        } else {
            return Err("non-finite normalized response coefficient".into());
        };
        coefficient_intervals.insert(name.clone(), divided);
        // Any finite multiplier is a valid dual certificate. Use the nearest f64 to c[i];
        // the interval above still encloses the exact normalized coefficient in the formula.
        candidates.push(*response / 100.0);
    }

    let mut best_lower = f64::NEG_INFINITY;
    let mut best_lower_lambda = 0.0;
    let mut best_upper = f64::INFINITY;
    let mut best_upper_lambda = 0.0;
    for lambda in candidates {
        if !lambda.is_finite() {
            return Err("non-finite dual multiplier".into());
        }
        let lambda_term = Interval::exact(lambda).mul(Interval::exact(100.0))?;
        let mut lower_certificate = lambda_term.lo;
        let mut upper_certificate = lambda_term.hi;
        for (name, bound) in weights {
            let difference = coefficient_intervals[name].sub(Interval::exact(lambda))?;
            let at_lower = difference.mul(Interval::exact(bound.lower_wt_percent))?;
            let at_upper = difference.mul(Interval::exact(bound.upper_wt_percent))?;
            let lower_term = at_lower.lo.min(at_upper.lo);
            let upper_term = at_lower.hi.max(at_upper.hi);
            lower_certificate = add_down(lower_certificate, lower_term)?;
            upper_certificate = add_up(upper_certificate, upper_term)?;
        }
        if lower_certificate > best_lower {
            best_lower = lower_certificate;
            best_lower_lambda = lambda;
        }
        if upper_certificate < best_upper {
            best_upper = upper_certificate;
            best_upper_lambda = lambda;
        }
    }

    // Responses and weights are nonnegative, so zero is an exact physical lower bound.
    let lower_bq = Interval::exact(best_lower.max(0.0))
        .mul(Interval::exact(mass_g))?
        .lo
        .max(0.0);
    let upper_bq = Interval::exact(best_upper).mul(Interval::exact(mass_g))?.hi;
    if !lower_bq.is_finite() || !upper_bq.is_finite() || lower_bq > upper_bq {
        return Err("non-finite or inconsistent projected response bounds".into());
    }
    Ok(ResponseProjection {
        lower_bq,
        upper_bq,
        min_witness_wt_percent: min_witness,
        max_witness_wt_percent: max_witness,
        lower_dual_lambda: best_lower_lambda,
        upper_dual_lambda: best_upper_lambda,
    })
}

fn greedy_witness(
    weights: &BTreeMap<String, WeightInterval>,
    response: &BTreeMap<String, f64>,
    maximize: bool,
) -> Result<BTreeMap<String, f64>, String> {
    let mut witness: BTreeMap<String, f64> = weights
        .iter()
        .map(|(name, bound)| (name.clone(), bound.lower_wt_percent))
        .collect();
    let lower_sum = weights
        .values()
        .map(|bound| bound.lower_wt_percent)
        .sum::<f64>();
    let mut remainder = 100.0 - lower_sum;
    let mut order: Vec<&String> = weights.keys().collect();
    order.sort_by(|left, right| {
        let left_coefficient = response[left.as_str()];
        let right_coefficient = response[right.as_str()];
        let coefficient_order = if left_coefficient == right_coefficient {
            std::cmp::Ordering::Equal
        } else if maximize {
            right_coefficient.total_cmp(&left_coefficient)
        } else {
            left_coefficient.total_cmp(&right_coefficient)
        };
        coefficient_order.then_with(|| left.cmp(right))
    });
    for name in order {
        if remainder <= 0.0 {
            break;
        }
        let bound = weights[name];
        let capacity = bound.upper_wt_percent - bound.lower_wt_percent;
        let addition = remainder.min(capacity);
        *witness.get_mut(name).expect("coordinate initialized") += addition;
        remainder -= addition;
    }
    if remainder.abs() > 1.0e-10 {
        return Err("could not construct a feasible composition witness".into());
    }
    validate_witness(weights, &witness)?;
    Ok(witness)
}

fn validate_witness(
    weights: &BTreeMap<String, WeightInterval>,
    witness: &BTreeMap<String, f64>,
) -> Result<(), String> {
    if witness.len() != weights.len() {
        return Err("composition witness has the wrong coordinate set".into());
    }
    let mut total = 0.0;
    for (name, bound) in weights {
        let value = *witness
            .get(name)
            .ok_or_else(|| format!("composition witness omits '{name}'"))?;
        if !value.is_finite()
            || value < bound.lower_wt_percent - 1.0e-10
            || value > bound.upper_wt_percent + 1.0e-10
        {
            return Err(format!("composition witness violates '{name}' bounds"));
        }
        total += value;
    }
    if !total.is_finite() || (total - 100.0).abs() > 1.0e-10 {
        return Err("composition witness does not sum to 100 wt-percent".into());
    }
    Ok(())
}

/// Compare the exact sum of finite binary64 inputs with a finite target using an error-free
/// floating-point expansion. Feasibility must not inherit the looser witness roundoff tolerance.
fn exact_sum_cmp_target(
    values: impl Iterator<Item = f64>,
    target: f64,
) -> Result<std::cmp::Ordering, String> {
    let mut expansion: Vec<f64> = Vec::new();
    for value in values.chain(std::iter::once(-target)) {
        if !value.is_finite() {
            return Err("non-finite value in composition feasibility sum".into());
        }
        let mut q = value;
        let mut next = Vec::with_capacity(expansion.len() + 1);
        for component in expansion {
            let sum = q + component;
            if !sum.is_finite() {
                return Err("composition feasibility sum overflow".into());
            }
            let virtual_component = sum - q;
            let virtual_q = sum - virtual_component;
            let component_roundoff = component - virtual_component;
            let q_roundoff = q - virtual_q;
            let error = q_roundoff + component_roundoff;
            if error != 0.0 {
                next.push(error);
            }
            q = sum;
        }
        if q != 0.0 || next.is_empty() {
            next.push(q);
        }
        expansion = next;
    }
    Ok(expansion
        .iter()
        .rev()
        .find(|component| **component != 0.0)
        .copied()
        .unwrap_or(0.0)
        .total_cmp(&0.0))
}

fn add_down(left: f64, right: f64) -> Result<f64, String> {
    checked_down(left + right)
}

fn add_up(left: f64, right: f64) -> Result<f64, String> {
    checked_up(left + right)
}

fn sub_down(left: f64, right: f64) -> Result<f64, String> {
    checked_down(left - right)
}

fn sub_up(left: f64, right: f64) -> Result<f64, String> {
    checked_up(left - right)
}

fn checked_down(value: f64) -> Result<f64, String> {
    if value.is_finite() {
        Ok(round_down(value))
    } else {
        Err("non-finite intermediate in composition bound".into())
    }
}

fn checked_up(value: f64) -> Result<f64, String> {
    if value.is_finite() {
        Ok(round_up(value))
    } else {
        Err("non-finite intermediate in composition bound".into())
    }
}

fn round_down(value: f64) -> f64 {
    if value.is_nan() || value == f64::NEG_INFINITY {
        value
    } else if value == 0.0 {
        -f64::from_bits(1)
    } else if value > 0.0 {
        f64::from_bits(value.to_bits() - 1)
    } else {
        f64::from_bits(value.to_bits() + 1)
    }
}

fn round_up(value: f64) -> f64 {
    if value.is_nan() || value == f64::INFINITY {
        value
    } else if value == 0.0 {
        f64::from_bits(1)
    } else if value > 0.0 {
        f64::from_bits(value.to_bits() + 1)
    } else {
        f64::from_bits(value.to_bits() - 1)
    }
}

#[cfg(test)]
mod tests {
    use super::{project_response, WeightInterval};
    use std::collections::BTreeMap;

    fn bounds(pairs: &[(&str, f64, f64)]) -> BTreeMap<String, WeightInterval> {
        pairs
            .iter()
            .map(|(name, lower, upper)| {
                (
                    (*name).into(),
                    WeightInterval {
                        lower_wt_percent: *lower,
                        upper_wt_percent: *upper,
                    },
                )
            })
            .collect()
    }

    fn rates(pairs: &[(&str, f64)]) -> BTreeMap<String, f64> {
        pairs
            .iter()
            .map(|(name, value)| ((*name).into(), *value))
            .collect()
    }

    #[test]
    fn bounded_two_coordinate_projection_has_witnesses_and_dual_bounds() {
        let weights = bounds(&[("A", 20.0, 60.0), ("B", 40.0, 80.0)]);
        let response = rates(&[("A", 10.0), ("B", 0.0)]);
        let result = project_response(&weights, &response, 2.0).unwrap();
        assert!(result.lower_bq <= 4.0);
        assert!(result.upper_bq >= 12.0);
        assert!((result.lower_bq - 4.0).abs() <= 1.0e-12);
        assert!((result.upper_bq - 12.0).abs() <= 1.0e-12);
        assert_eq!(result.min_witness_wt_percent["A"], 20.0);
        assert_eq!(result.max_witness_wt_percent["A"], 60.0);
        assert!(result.lower_dual_lambda.is_finite());
        assert!(result.upper_dual_lambda.is_finite());
    }

    #[test]
    fn coefficient_ties_use_canonical_coordinate_order_for_both_witnesses() {
        let weights = bounds(&[("B", 0.0, 100.0), ("A", 0.0, 100.0)]);
        let response = rates(&[("B", 5.0), ("A", 5.0)]);
        let result = project_response(&weights, &response, 1.0).unwrap();
        assert_eq!(result.min_witness_wt_percent["A"], 100.0);
        assert_eq!(result.max_witness_wt_percent["A"], 100.0);
        assert!(result.lower_bq <= 5.0);
        assert!(result.upper_bq >= 5.0);
    }

    #[test]
    fn all_zero_response_is_exact_zero_and_still_has_feasible_witnesses() {
        let weights = bounds(&[("Si", 0.0, 100.0), ("Fe", 0.0, 100.0)]);
        let response = rates(&[("Si", 0.0), ("Fe", 0.0)]);
        let result = project_response(&weights, &response, 1.0).unwrap();
        assert_eq!(result.lower_bq, 0.0);
        assert_eq!(result.upper_bq, 0.0);
        assert_eq!(result.min_witness_wt_percent, result.max_witness_wt_percent);
    }

    #[test]
    fn invalid_boxes_coordinate_sets_and_overflow_are_refused() {
        let weights = bounds(&[("A", 80.0, 90.0), ("B", 0.0, 9.0)]);
        let response = rates(&[("A", 1.0), ("B", 2.0)]);
        assert!(project_response(&weights, &response, 1.0).is_err());
        let weights = bounds(&[("A", 0.0, 100.0)]);
        assert!(project_response(&weights, &response, 1.0).is_err());
        let response = rates(&[("A", f64::MAX)]);
        assert!(project_response(&weights, &response, f64::MAX).is_err());
    }

    #[test]
    fn feasibility_uses_exact_binary_sum_not_tolerant_witness_roundoff() {
        let third = 100.0 / 3.0;
        assert_eq!(third + third + third, 100.0);
        let weights = bounds(&[
            ("A", third, 100.0),
            ("B", third, 100.0),
            ("C", third, 100.0),
        ]);
        let response = rates(&[("A", 0.0), ("B", 0.0), ("C", 0.0)]);
        assert!(project_response(&weights, &response, 1.0).is_err());
    }

    #[test]
    fn subnormal_response_is_enclosed_and_signed_zero_stays_exact_zero() {
        let weights = bounds(&[("A", 100.0, 100.0)]);
        let minimum_subnormal = f64::from_bits(1);
        let response = rates(&[("A", minimum_subnormal)]);
        let result = project_response(&weights, &response, 1.0).unwrap();
        assert!(result.lower_bq >= 0.0);
        assert!(result.lower_bq <= minimum_subnormal);
        assert!(result.upper_bq >= minimum_subnormal);
        assert!(result.upper_bq.is_finite());

        let signed_zero_response = rates(&[("A", -0.0)]);
        let zero = project_response(&weights, &signed_zero_response, 1.0).unwrap();
        assert_eq!(zero.lower_bq, 0.0);
        assert_eq!(zero.upper_bq, 0.0);
    }

    #[test]
    fn signed_zero_coefficients_use_canonical_tie_order() {
        let weights = bounds(&[("B", 0.0, 100.0), ("A", 0.0, 100.0)]);
        let response = rates(&[("A", 0.0), ("B", -0.0)]);
        let result = project_response(&weights, &response, 1.0).unwrap();
        assert_eq!(result.min_witness_wt_percent["A"], 100.0);
        assert_eq!(result.max_witness_wt_percent["A"], 100.0);
        assert_eq!(result.lower_bq, 0.0);
        assert_eq!(result.upper_bq, 0.0);
    }
}
