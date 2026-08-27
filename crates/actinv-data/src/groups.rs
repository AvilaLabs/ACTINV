//! Energy-group structures, ENDF interpolation and deterministic flat-lethargy collapse.

use crate::endf::CheckedTab1Record;
use serde::Deserialize;
use sha2::{Digest, Sha256};

const PRODUCT_FALLBACK_RELATIVE_TOLERANCE: f64 = 2e-13;
const GAUSS_8_POSITIVE_NODES: [f64; 4] = [
    0.183_434_642_495_649_8,
    0.525_532_409_916_329,
    0.796_666_477_413_626_7,
    0.960_289_856_497_536_3,
];
const GAUSS_8_POSITIVE_WEIGHTS: [f64; 4] = [
    0.362_683_783_378_362,
    0.313_706_645_877_887_3,
    0.222_381_034_453_374_48,
    0.101_228_536_290_376_26,
];
const GAUSS_16_POSITIVE_NODES: [f64; 8] = [
    0.095_012_509_837_637_44,
    0.281_603_550_779_258_9,
    0.458_016_777_657_227_4,
    0.617_876_244_402_643_8,
    0.755_404_408_355_003,
    0.865_631_202_387_831_8,
    0.944_575_023_073_232_6,
    0.989_400_934_991_649_9,
];
const GAUSS_16_POSITIVE_WEIGHTS: [f64; 8] = [
    0.189_450_610_455_068_5,
    0.182_603_415_044_923_6,
    0.169_156_519_395_002_54,
    0.149_595_988_816_576_73,
    0.124_628_971_255_533_87,
    0.095_158_511_682_492_78,
    0.062_253_523_938_647_89,
    0.027_152_459_411_754_096,
];

#[derive(Clone, Debug, PartialEq)]
pub struct Tabulated {
    /// One-based `(NBT, INT)` pairs.
    pub interpolation: Vec<(usize, i32)>,
    pub x: Vec<f64>,
    pub y: Vec<f64>,
}

impl TryFrom<CheckedTab1Record> for Tabulated {
    type Error = String;

    fn try_from(record: CheckedTab1Record) -> Result<Self, Self::Error> {
        let (x, y): (Vec<_>, Vec<_>) = record.points.into_iter().unzip();
        let table = Self {
            interpolation: record.interpolation,
            x,
            y,
        };
        table.validate()?;
        Ok(table)
    }
}

impl Tabulated {
    pub fn validate(&self) -> Result<(), String> {
        if self.x.is_empty() || self.x.len() != self.y.len() {
            return Err("TAB1 needs matching nonempty X/Y arrays".into());
        }
        if self
            .x
            .iter()
            .chain(self.y.iter())
            .any(|value| !value.is_finite())
        {
            return Err("TAB1 contains a nonfinite point".into());
        }
        if self.x.windows(2).any(|pair| pair[1] < pair[0]) {
            return Err("TAB1 abscissae are not nondecreasing".into());
        }
        let mut previous = 0usize;
        for &(nbt, law) in &self.interpolation {
            if nbt <= previous || nbt > self.x.len() {
                return Err(format!(
                    "invalid TAB1 NBT={nbt}; previous={previous}, NP={}",
                    self.x.len()
                ));
            }
            if !(1..=5).contains(&law) {
                return Err(format!("unsupported TAB1 interpolation INT={law}"));
            }
            previous = nbt;
        }
        if previous != self.x.len() {
            return Err(format!(
                "TAB1 interpolation ends at {previous}, expected {}",
                self.x.len()
            ));
        }
        for index in 0..self.x.len() - 1 {
            let law = self.law(index)?;
            if matches!(law, 3 | 5) && (self.x[index] <= 0.0 || self.x[index + 1] <= 0.0) {
                return Err(format!("TAB1 INT={law} requires positive X"));
            }
            if matches!(law, 4 | 5) && (self.y[index] <= 0.0 || self.y[index + 1] <= 0.0) {
                return Err(format!("TAB1 INT={law} requires positive Y"));
            }
        }
        Ok(())
    }

    fn law(&self, segment: usize) -> Result<i32, String> {
        // Segment `i` ends at one-based point i+2 and belongs to the first range whose NBT includes it.
        let endpoint = segment + 2;
        self.interpolation
            .iter()
            .find_map(|&(nbt, law)| (endpoint <= nbt).then_some(law))
            .ok_or_else(|| format!("TAB1 has no interpolation law for segment {segment}"))
    }

    fn segment_value(&self, segment: usize, value: f64) -> Result<f64, String> {
        let (x1, x2) = (self.x[segment], self.x[segment + 1]);
        let (y1, y2) = (self.y[segment], self.y[segment + 1]);
        if x2 == x1 {
            return Ok(y2);
        }
        let linear = (value - x1) / (x2 - x1);
        Ok(match self.law(segment)? {
            1 => y1,
            2 => y1 + linear * (y2 - y1),
            3 => y1 + (value / x1).ln() / (x2 / x1).ln() * (y2 - y1),
            4 => y1 * (y2 / y1).powf(linear),
            5 => y1 * (y2 / y1).powf((value / x1).ln() / (x2 / x1).ln()),
            law => return Err(format!("unsupported TAB1 interpolation INT={law}")),
        })
    }

    /// Represent this table on one breakpoint-bounded interval as powers of `t=E/low` when its ENDF law permits
    /// an exact product antiderivative. `None` retains the general adaptive path.
    fn interval_power_terms(&self, low: f64, high: f64) -> Result<Option<Vec<(f64, f64)>>, String> {
        let middle = low + 0.5 * (high - low);
        if middle < self.x[0] || middle > self.x[self.x.len() - 1] {
            return Ok(Some(vec![(0.0, 0.0)]));
        }
        let upper = self.x.partition_point(|point| *point <= middle);
        let segment = upper.saturating_sub(1).min(self.x.len() - 2);
        let (x1, x2) = (self.x[segment], self.x[segment + 1]);
        if x2 <= x1 {
            return Err("product-collapse interval selected a zero-width TAB1 segment".into());
        }
        Ok(match self.law(segment)? {
            1 => Some(vec![(self.y[segment], 0.0)]),
            2 => {
                let slope = (self.y[segment + 1] - self.y[segment]) / (x2 - x1);
                let value_at_low = self.y[segment] + slope * (low - x1);
                let scaled_slope = slope * low;
                Some(vec![
                    (value_at_low - scaled_slope, 0.0),
                    (scaled_slope, 1.0),
                ])
            }
            5 => {
                let y1 = self.y[segment];
                let y2 = self.y[segment + 1];
                let log_x_span = ((x2 - x1) / x1).ln_1p();
                let power = ((y2 - y1) / y1).ln_1p() / log_x_span;
                let value_at_low = y1 * (power * ((low - x1) / x1).ln_1p()).exp();
                Some(vec![(value_at_low, power)])
            }
            3 | 4 => None,
            law => return Err(format!("unsupported TAB1 interpolation INT={law}")),
        })
    }

    fn stable_segment_value(&self, segment: usize, value: f64) -> Result<f64, String> {
        let (x1, x2) = (self.x[segment], self.x[segment + 1]);
        let (y1, y2) = (self.y[segment], self.y[segment + 1]);
        if x2 <= x1 {
            return Err("product-collapse interval selected a zero-width TAB1 segment".into());
        }
        let from_left = value - x1 <= x2 - value;
        Ok(match self.law(segment)? {
            1 => y1,
            2 => {
                if from_left {
                    y1 + (value - x1) / (x2 - x1) * (y2 - y1)
                } else {
                    y2 + (x2 - value) / (x2 - x1) * (y1 - y2)
                }
            }
            3 => {
                let span = ((x2 - x1) / x1).ln_1p();
                if from_left {
                    y1 + ((value - x1) / x1).ln_1p() / span * (y2 - y1)
                } else {
                    y2 + ((x2 - value) / value).ln_1p() / span * (y1 - y2)
                }
            }
            4 => {
                if from_left {
                    y1 * (((y2 - y1) / y1).ln_1p() * (value - x1) / (x2 - x1)).exp()
                } else {
                    y2 * (((y1 - y2) / y2).ln_1p() * (x2 - value) / (x2 - x1)).exp()
                }
            }
            5 => {
                let span = ((x2 - x1) / x1).ln_1p();
                if from_left {
                    y1 * (((y2 - y1) / y1).ln_1p() * ((value - x1) / x1).ln_1p() / span).exp()
                } else {
                    y2 * (((y1 - y2) / y2).ln_1p() * ((x2 - value) / value).ln_1p() / span).exp()
                }
            }
            law => return Err(format!("unsupported TAB1 interpolation INT={law}")),
        })
    }

    /// Evaluate inside one shared-breakpoint interval directly from its normalized log-energy coordinate. Endpoint
    /// values come from the source segment selected by the interval midpoint; interpolation between them never
    /// reconstructs and subtracts nearly equal absolute energies.
    fn interval_value_at_log_fraction(
        &self,
        low: f64,
        high: f64,
        fraction: f64,
    ) -> Result<f64, String> {
        let middle = low + 0.5 * (high - low);
        if middle < self.x[0] || middle > self.x[self.x.len() - 1] {
            return Ok(0.0);
        }
        let upper = self.x.partition_point(|point| *point <= middle);
        let segment = upper.saturating_sub(1).min(self.x.len() - 2);
        let low_value = self.stable_segment_value(segment, low)?;
        let high_value = self.stable_segment_value(segment, high)?;
        let log_span = ((high - low) / low).ln_1p();
        let energy_fraction = (fraction * log_span).exp_m1() / log_span.exp_m1();
        let blend = |position: f64| {
            if position <= 0.5 {
                low_value + position * (high_value - low_value)
            } else {
                high_value + (1.0 - position) * (low_value - high_value)
            }
        };
        let log_blend = |position: f64| {
            if position <= 0.5 {
                low_value * (((high_value - low_value) / low_value).ln_1p() * position).exp()
            } else {
                high_value
                    * (((low_value - high_value) / high_value).ln_1p() * (1.0 - position)).exp()
            }
        };
        Ok(match self.law(segment)? {
            1 => low_value,
            2 => blend(energy_fraction),
            3 => blend(fraction),
            4 => log_blend(energy_fraction),
            5 => log_blend(fraction),
            law => return Err(format!("unsupported TAB1 interpolation INT={law}")),
        })
    }

    /// Evaluate with zero outside the tabulated domain and right-continuous ENDF double-point handling.
    pub fn evaluate(&self, value: f64) -> Result<f64, String> {
        if !value.is_finite() {
            return Err("cannot evaluate TAB1 at a nonfinite value".into());
        }
        if value < self.x[0] || value > self.x[self.x.len() - 1] {
            return Ok(0.0);
        }
        if value == self.x[self.x.len() - 1] {
            return Ok(self.y[self.y.len() - 1]);
        }
        let upper = self.x.partition_point(|point| *point <= value);
        let segment = upper.saturating_sub(1).min(self.x.len() - 2);
        self.segment_value(segment, value)
    }

    /// Integral of sigma(E) dE/E on a positive subinterval. Interpolation discontinuities are exact breakpoints.
    pub fn lethargy_integral(&self, low: f64, high: f64) -> Result<f64, String> {
        if !low.is_finite() || !high.is_finite() || low <= 0.0 || high < low {
            return Err(format!(
                "invalid lethargy integration interval [{low}, {high}]"
            ));
        }
        if high == low || high <= self.x[0] || low >= self.x[self.x.len() - 1] {
            return Ok(0.0);
        }
        let low = low.max(self.x[0]);
        let high = high.min(self.x[self.x.len() - 1]);
        let mut total = 0.0;
        for segment in 0..self.x.len() - 1 {
            let (x1, x2) = (self.x[segment], self.x[segment + 1]);
            if x2 <= low || x1 >= high || x2 <= x1 {
                continue;
            }
            let a = low.max(x1);
            let b = high.min(x2);
            if b <= a {
                continue;
            }
            let (y1, y2) = (self.y[segment], self.y[segment + 1]);
            let ratio_minus_one = (b - a) / a;
            let log_ratio = ratio_minus_one.ln_1p();
            total += match self.law(segment)? {
                1 => y1 * log_ratio,
                2 => {
                    let slope = (y2 - y1) / (x2 - x1);
                    let value_at_a = self.segment_value(segment, a)?;
                    value_at_a * log_ratio + slope * a * x_minus_ln_1p(ratio_minus_one)
                }
                3 => {
                    let value_at_a = self.segment_value(segment, a)?;
                    let value_at_b = self.segment_value(segment, b)?;
                    0.5 * (value_at_a + value_at_b) * log_ratio
                }
                4 => adaptive_log_integral(
                    |energy| self.segment_value(segment, energy),
                    a,
                    b,
                    2e-14,
                )?,
                5 => {
                    let power = (y2 / y1).ln() / (x2 / x1).ln();
                    let value_at_a = self.segment_value(segment, a)?;
                    value_at_a * log_ratio * expm1_over_x(power * log_ratio)
                }
                law => return Err(format!("unsupported TAB1 interpolation INT={law}")),
            };
        }
        Ok(total)
    }
}

/// Stable `x - ln(1+x)` for nonnegative x, including narrow energy intervals.
fn x_minus_ln_1p(value: f64) -> f64 {
    if value.abs() >= 1e-3 {
        return value - value.ln_1p();
    }
    let mut power = value * value;
    let mut sum = 0.5 * power;
    for denominator in 3..=12 {
        power *= value;
        let term = power / f64::from(denominator);
        if denominator % 2 == 0 {
            sum += term;
        } else {
            sum -= term;
        }
    }
    sum
}

/// Stable `(exp(x)-1)/x` with its analytic value at zero.
fn expm1_over_x(value: f64) -> f64 {
    if value.abs() < 1e-8 {
        1.0 + value * (0.5 + value * (1.0 / 6.0 + value / 24.0))
    } else {
        value.exp_m1() / value
    }
}

fn simpson<F>(function: &mut F, a: f64, b: f64) -> Result<f64, String>
where
    F: FnMut(f64) -> Result<f64, String>,
{
    let middle = 0.5 * (a + b);
    Ok((b - a) * (function(a)? + 4.0 * function(middle)? + function(b)?) / 6.0)
}

fn adaptive_simpson<F>(
    function: &mut F,
    a: f64,
    b: f64,
    whole: f64,
    tolerance: f64,
    depth: usize,
) -> Result<f64, String>
where
    F: FnMut(f64) -> Result<f64, String>,
{
    let middle = 0.5 * (a + b);
    let left = simpson(function, a, middle)?;
    let right = simpson(function, middle, b)?;
    let delta = left + right - whole;
    if depth == 0 || delta.abs() <= 15.0 * tolerance {
        return Ok(left + right + delta / 15.0);
    }
    Ok(
        adaptive_simpson(function, a, middle, left, 0.5 * tolerance, depth - 1)?
            + adaptive_simpson(function, middle, b, right, 0.5 * tolerance, depth - 1)?,
    )
}

/// Integrate `sigma(E) dE/E` by changing variables to `u=ln(E)`.
pub fn adaptive_log_integral<F>(
    mut function: F,
    low: f64,
    high: f64,
    relative_tolerance: f64,
) -> Result<f64, String>
where
    F: FnMut(f64) -> Result<f64, String>,
{
    if high <= low {
        return Ok(0.0);
    }
    let log_span = ((high - low) / low).ln_1p();
    // A TAB1 may have a double-point discontinuity at either bound. Point values have zero measure, so sample the
    // interior side instead of letting Simpson assign finite weight to the opposite side of the jump.
    let mut transformed = |u: f64| {
        let energy = if u == 0.0 {
            low.next_up()
        } else if u == log_span {
            high.next_down()
        } else {
            low * u.exp()
        };
        function(energy)
    };
    let whole = simpson(&mut transformed, 0.0, log_span)?;
    let tolerance = relative_tolerance * whole.abs().max(log_span * 1e-30);
    adaptive_simpson(&mut transformed, 0.0, log_span, whole, tolerance, 24)
}

fn gauss_legendre_unit_integral<F>(
    function: &mut F,
    positive_nodes: &[f64],
    positive_weights: &[f64],
) -> Result<f64, String>
where
    F: FnMut(f64) -> Result<f64, String>,
{
    let mut total = 0.0;
    let mut correction = 0.0;
    for (&node, &weight) in positive_nodes.iter().zip(positive_weights) {
        let left = function(0.5 * (1.0 - node))?;
        let right = function(0.5 * (1.0 + node))?;
        let value = weight * (left + right);
        let next = total + value;
        correction += if total.abs() >= value.abs() {
            (total - next) + value
        } else {
            (value - next) + total
        };
        total = next;
    }
    Ok(0.5 * (total + correction))
}

fn product_fallback_integral(tables: &[&Tabulated], low: f64, high: f64) -> Result<f64, String> {
    let log_span = ((high - low) / low).ln_1p();
    let mut integrand = |fraction| {
        tables.iter().try_fold(1.0, |product, table| {
            Ok(product * table.interval_value_at_log_fraction(low, high, fraction)?)
        })
    };
    let order_8 = log_span
        * gauss_legendre_unit_integral(
            &mut integrand,
            &GAUSS_8_POSITIVE_NODES,
            &GAUSS_8_POSITIVE_WEIGHTS,
        )?;
    let order_16 = log_span
        * gauss_legendre_unit_integral(
            &mut integrand,
            &GAUSS_16_POSITIVE_NODES,
            &GAUSS_16_POSITIVE_WEIGHTS,
        )?;
    let tolerance = PRODUCT_FALLBACK_RELATIVE_TOLERANCE
        * order_16.abs().max(((high - low) / low).ln_1p() * 1e-30);
    if order_16.is_finite() && (order_16 - order_8).abs() <= tolerance {
        Ok(order_16)
    } else {
        let whole = simpson(&mut integrand, 0.0, 1.0)?;
        let tolerance = PRODUCT_FALLBACK_RELATIVE_TOLERANCE * whole.abs().max(1e-30);
        Ok(log_span * adaptive_simpson(&mut integrand, 0.0, 1.0, whole, tolerance, 24)?)
    }
}

/// Direct lethargy integral for products of histogram, lin-lin and log-log factors on one shared-breakpoint
/// interval. The normalized power representation avoids the non-terminating practical cost of recursively
/// quadraturing fine EAF MF=3(log-log) × MF=9(lin-lin) tables.
fn exact_power_product_integral(
    tables: &[&Tabulated],
    low: f64,
    high: f64,
) -> Result<Option<f64>, String> {
    let mut terms = vec![(1.0, 0.0)];
    for table in tables {
        let Some(factors) = table.interval_power_terms(low, high)? else {
            return Ok(None);
        };
        let mut product = Vec::with_capacity(terms.len() * factors.len());
        for &(left_coefficient, left_power) in &terms {
            for &(right_coefficient, right_power) in &factors {
                let coefficient = left_coefficient * right_coefficient;
                let power = left_power + right_power;
                if let Some((existing, _)) =
                    product
                        .iter_mut()
                        .find(|(_, existing_power): &&mut (f64, f64)| {
                            existing_power.to_bits() == power.to_bits()
                        })
                {
                    *existing += coefficient;
                } else {
                    product.push((coefficient, power));
                }
            }
        }
        terms = product;
        if terms.len() > 64 {
            return Ok(None);
        }
    }

    let log_ratio = ((high - low) / low).ln_1p();
    let mut sum = 0.0;
    let mut correction = 0.0;
    let mut absolute_sum = 0.0;
    for (coefficient, power) in terms {
        let value = coefficient * log_ratio * expm1_over_x(power * log_ratio);
        if !value.is_finite() {
            return Ok(None);
        }
        absolute_sum += value.abs();
        let next = sum + value;
        correction += if sum.abs() >= value.abs() {
            (sum - next) + value
        } else {
            (value - next) + sum
        };
        sum = next;
    }
    let total = sum + correction;
    // A power expansion can contain cancelling signed terms even though every source table is nonnegative. Retain
    // adaptive quadrature when its forward conditioning is not comfortably inside the 2e-12 G1 criterion.
    if !total.is_finite()
        || total < 0.0
        || (absolute_sum > 0.0 && absolute_sum > 4096.0 * total.max(f64::MIN_POSITIVE))
    {
        Ok(None)
    } else {
        Ok(Some(total))
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct GroupStructure {
    pub name: String,
    pub boundaries_ev: Vec<f64>,
}

#[derive(Deserialize)]
struct GroupJson {
    structure: String,
    #[serde(rename = "boundaries_eV")]
    boundaries_ev: Vec<f64>,
}

impl GroupStructure {
    pub fn fispact_709() -> Result<Self, String> {
        Self::from_json(include_str!("../data/fispact_709_groups.json"))
    }

    pub fn fispact_162() -> Result<Self, String> {
        Self::from_json(include_str!("../data/fispact_162_groups.json"))
    }

    pub fn from_json(text: &str) -> Result<Self, String> {
        let parsed: GroupJson =
            serde_json::from_str(text).map_err(|error| format!("invalid group JSON: {error}"))?;
        let mut boundaries = parsed.boundaries_ev;
        if boundaries.len() >= 2 && boundaries[0] > boundaries[boundaries.len() - 1] {
            boundaries.reverse();
        }
        let groups = Self {
            name: parsed.structure,
            boundaries_ev: boundaries,
        };
        groups.validate()?;
        Ok(groups)
    }

    pub fn validate(&self) -> Result<(), String> {
        if self.boundaries_ev.len() < 2 {
            return Err("group structure needs at least two boundaries".into());
        }
        if self
            .boundaries_ev
            .iter()
            .any(|value| !value.is_finite() || *value <= 0.0)
            || self.boundaries_ev.windows(2).any(|pair| pair[1] <= pair[0])
        {
            return Err("group boundaries must be finite, positive and strictly increasing".into());
        }
        Ok(())
    }

    pub fn groups(&self) -> usize {
        self.boundaries_ev.len() - 1
    }

    pub fn hash(&self) -> String {
        let mut hasher = Sha256::new();
        hasher.update(b"ACTINV-GROUP-BOUNDARIES-v1\0");
        for value in &self.boundaries_ev {
            hasher.update(value.to_le_bytes());
        }
        format!("{:x}", hasher.finalize())
    }

    pub fn collapse(&self, table: &Tabulated) -> Result<Vec<f64>, String> {
        self.boundaries_ev
            .windows(2)
            .map(|pair| Ok(table.lethargy_integral(pair[0], pair[1])? / (pair[1] / pair[0]).ln()))
            .collect()
    }

    pub fn collapse_product(&self, tables: &[&Tabulated]) -> Result<Vec<f64>, String> {
        if tables.is_empty() {
            return Err("cannot collapse an empty product".into());
        }
        self.boundaries_ev
            .windows(2)
            .map(|group| {
                let mut breaks = vec![group[0], group[1]];
                for table in tables {
                    breaks.extend(
                        table
                            .x
                            .iter()
                            .copied()
                            .filter(|value| *value > group[0] && *value < group[1]),
                    );
                }
                breaks.sort_by(f64::total_cmp);
                breaks.dedup_by(|left, right| left.to_bits() == right.to_bits());
                let mut integral = 0.0;
                let mut correction = 0.0;
                for interval in breaks.windows(2) {
                    let value = if let Some(exact) =
                        exact_power_product_integral(tables, interval[0], interval[1])?
                    {
                        exact
                    } else {
                        product_fallback_integral(tables, interval[0], interval[1])?
                    };
                    let next = integral + value;
                    correction += if integral.abs() >= value.abs() {
                        (integral - next) + value
                    } else {
                        (value - next) + integral
                    };
                    integral = next;
                }
                Ok((integral + correction) / (group[1] / group[0]).ln())
            })
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn table(law: i32, x: [f64; 2], y: [f64; 2]) -> Tabulated {
        Tabulated {
            interpolation: vec![(2, law)],
            x: x.to_vec(),
            y: y.to_vec(),
        }
    }

    fn stable_linline_product_average(
        first: &Tabulated,
        second: &Tabulated,
        low: f64,
        high: f64,
    ) -> f64 {
        let mut total = 0.0;
        let mut correction = 0.0;
        for segment in 0..first.x.len() - 1 {
            let left = first.x[segment];
            let right = first.x[segment + 1];
            let ratio = (right - left) / left;
            let log_ratio = ratio.ln_1p();
            let integral_t = x_minus_ln_1p(ratio) / ratio;
            let mut power = ratio;
            let mut integral_t2 = power / 3.0;
            for denominator in 4..=14 {
                power *= -ratio;
                integral_t2 += power / f64::from(denominator);
            }
            let first_delta = first.y[segment + 1] - first.y[segment];
            let second_delta = second.y[segment + 1] - second.y[segment];
            let value = first.y[segment] * second.y[segment] * log_ratio
                + (first.y[segment] * second_delta + second.y[segment] * first_delta) * integral_t
                + first_delta * second_delta * integral_t2;
            let next = total + value;
            correction += if total.abs() >= value.abs() {
                (total - next) + value
            } else {
                (value - next) + total
            };
            total = next;
        }
        (total + correction) / ((high - low) / low).ln_1p()
    }

    #[test]
    fn official_group_counts_and_order() {
        let g709 = GroupStructure::fispact_709().unwrap();
        let g162 = GroupStructure::fispact_162().unwrap();
        assert_eq!(g709.groups(), 709);
        assert_eq!(g162.groups(), 162);
        assert_eq!(g162.boundaries_ev[0], 5.0e3);
        assert_eq!(*g162.boundaries_ev.last().unwrap(), 1.0e9);
    }

    #[test]
    fn many_interval_product_fallback_respects_group_tolerance() {
        let low = 33_113.11;
        let high = 34_673.69;
        let intervals = 364usize;
        let x: Vec<_> = (0..=intervals)
            .map(|index| low + (high - low) * index as f64 / intervals as f64)
            .collect();
        let first = Tabulated {
            interpolation: vec![(x.len(), 2)],
            x: x.clone(),
            y: (0..=intervals)
                .map(|index| 0.02 + ((37 * index) % 101) as f64 / 100.0)
                .collect(),
        };
        let second = Tabulated {
            interpolation: vec![(x.len(), 2)],
            x,
            y: (0..=intervals)
                .map(|index| 0.03 + ((61 * index + 17) % 103) as f64 / 100.0)
                .collect(),
        };
        assert!(
            exact_power_product_integral(&[&first, &second], first.x[0], first.x[1])
                .unwrap()
                .is_none()
        );
        let groups = GroupStructure {
            name: "synthetic-product-fallback".into(),
            boundaries_ev: vec![low, high],
        };
        let actual = groups.collapse_product(&[&first, &second]).unwrap()[0];
        let expected = stable_linline_product_average(&first, &second, low, high);
        assert!((actual - expected).abs() / expected.abs() <= 2e-12);
    }

    #[test]
    fn narrow_log_intervals_use_a_local_coordinate() {
        let low = 33_113.11;
        let high = 34_673.69;
        let intervals = 364usize;
        let mut actual = 0.0;
        let mut expected = 0.0;
        for index in 0..intervals {
            let left = low + (high - low) * index as f64 / intervals as f64;
            let right = low + (high - low) * (index + 1) as f64 / intervals as f64;
            let value = 0.03 + ((61 * index + 17) % 103) as f64 / 100.0;
            actual += adaptive_log_integral(|_| Ok(value), left, right, 1e-14).unwrap();
            expected += value * ((right - left) / left).ln_1p();
        }
        assert!((actual - expected).abs() / expected.abs() <= 2e-15);
    }

    #[test]
    fn narrow_product_fallback_uses_a_normalized_coordinate() {
        let low = 5_000.0;
        let high = 5_001.0;
        let intervals = 1_000usize;
        let x: Vec<_> = (0..=intervals)
            .map(|index| low + (high - low) * index as f64 / intervals as f64)
            .collect();
        let first = Tabulated {
            interpolation: vec![(x.len(), 2)],
            x: x.clone(),
            y: (0..=intervals)
                .map(|index| 0.01 + ((37 * index + 11) % 101) as f64 / 100.0)
                .collect(),
        };
        let second = Tabulated {
            interpolation: vec![(x.len(), 2)],
            x,
            y: (0..=intervals)
                .map(|index| 0.02 + ((61 * index + 29) % 103) as f64 / 100.0)
                .collect(),
        };
        assert!(
            exact_power_product_integral(&[&first, &second], first.x[0], first.x[1])
                .unwrap()
                .is_none()
        );
        let groups = GroupStructure {
            name: "synthetic-narrow-product".into(),
            boundaries_ev: vec![low, high],
        };
        let actual = groups.collapse_product(&[&first, &second]).unwrap()[0];
        let expected = stable_linline_product_average(&first, &second, low, high);
        assert!((actual - expected).abs() / expected.abs() <= 2e-12);
    }

    #[test]
    fn interpolation_laws_integrate_in_lethargy() {
        let low = 1.0;
        let high = 4.0;
        for law in 1..=5 {
            let t = table(law, [low, high], [2.0, 8.0]);
            t.validate().unwrap();
            let own = t.lethargy_integral(low, high).unwrap();
            let reference = adaptive_log_integral(|e| t.evaluate(e), low, high, 1e-14).unwrap();
            assert!((own - reference).abs() <= 2e-11 * reference.abs());
        }
    }

    #[test]
    fn double_points_are_right_continuous() {
        let table = Tabulated {
            interpolation: vec![(4, 2)],
            x: vec![1.0, 2.0, 2.0, 3.0],
            y: vec![1.0, 1.0, 4.0, 4.0],
        };
        table.validate().unwrap();
        assert_eq!(table.evaluate(2.0).unwrap(), 4.0);
        let expected = (2.0f64 / 1.0).ln() + 4.0 * (3.0f64 / 2.0).ln();
        assert!((table.lethargy_integral(1.0, 3.0).unwrap() - expected).abs() < 1e-14);
    }

    #[test]
    fn power_product_uses_the_exact_lethargy_antiderivative() {
        let power = table(5, [1.0, 4.0], [1.0, 16.0]);
        let linear = table(2, [1.0, 4.0], [5.0, 14.0]);
        let groups = GroupStructure {
            name: "product-control".into(),
            boundaries_ev: vec![1.0, 4.0],
        };
        // sigma(E)=E^2(2+3E), so integral sigma(E)dE/E from 1 to 4 is exactly 78.
        let actual = groups.collapse_product(&[&power, &linear]).unwrap()[0];
        let expected = 78.0 / 4.0f64.ln();
        assert!((actual - expected).abs() <= 2e-15 * expected);
    }

    #[test]
    fn power_product_respects_internal_factor_breakpoints() {
        let power = table(5, [1.0, 4.0], [1.0, 16.0]);
        let linear = Tabulated {
            interpolation: vec![(3, 2)],
            x: vec![1.0, 2.0, 4.0],
            y: vec![5.0, 8.0, 14.0],
        };
        linear.validate().unwrap();
        let groups = GroupStructure {
            name: "product-breakpoint-control".into(),
            boundaries_ev: vec![1.0, 4.0],
        };
        let actual = groups.collapse_product(&[&power, &linear]).unwrap()[0];
        let expected = 78.0 / 4.0f64.ln();
        assert!((actual - expected).abs() <= 2e-15 * expected);
    }

    #[test]
    fn non_power_product_retains_adaptive_quadrature() {
        let log_linear = table(3, [1.0, 4.0], [2.0, 8.0]);
        let linear = table(2, [1.0, 4.0], [5.0, 14.0]);
        assert!(
            exact_power_product_integral(&[&log_linear, &linear], 1.0, 4.0)
                .unwrap()
                .is_none()
        );
        let groups = GroupStructure {
            name: "product-fallback-control".into(),
            boundaries_ev: vec![1.0, 4.0],
        };
        let actual = groups.collapse_product(&[&log_linear, &linear]).unwrap()[0];
        assert!(actual.is_finite() && actual > 0.0);
    }
}
