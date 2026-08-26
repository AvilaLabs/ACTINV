//! Energy-group structures, ENDF interpolation and deterministic flat-lethargy collapse.

use crate::endf::CheckedTab1Record;
use serde::Deserialize;
use sha2::{Digest, Sha256};

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
    let (a, b) = (low.ln(), high.ln());
    // A TAB1 may have a double-point discontinuity at either bound. Point values have zero measure, so sample the
    // interior side instead of letting Simpson assign finite weight to the opposite side of the jump.
    let mut transformed = |u: f64| {
        let interior = if u == a {
            a.next_up()
        } else if u == b {
            b.next_down()
        } else {
            u
        };
        function(interior.exp())
    };
    let whole = simpson(&mut transformed, a, b)?;
    let tolerance = relative_tolerance * whole.abs().max((b - a) * 1e-30);
    adaptive_simpson(&mut transformed, a, b, whole, tolerance, 24)
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
        Self::from_json(include_str!("../../../data/fispact_709_groups.json"))
    }

    pub fn fispact_162() -> Result<Self, String> {
        Self::from_json(include_str!("../../../data/fispact_162_groups.json"))
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
                for interval in breaks.windows(2) {
                    integral += adaptive_log_integral(
                        |energy| {
                            tables.iter().try_fold(1.0, |product, table| {
                                Ok(product * table.evaluate(energy)?)
                            })
                        },
                        interval[0],
                        interval[1],
                        2e-12,
                    )?;
                }
                Ok(integral / (group[1] / group[0]).ln())
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
}
