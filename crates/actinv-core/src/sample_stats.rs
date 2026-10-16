//! Sample statistics for the `study.robustness` Monte Carlo path: distribution summaries,
//! input-output and response-response correlations, and bootstrap convergence diagnostics.
//!
//! Everything here is deterministic. Means and variances are two-pass with a compensation term,
//! percentiles are exact order statistics (no interpolation), Spearman uses average ranks for
//! ties, and the bootstrap draws from a fixed-seed SplitMix64 stream.

use std::cmp::Ordering;

/// Percentiles reported when `statistics.percentiles` is not declared.
pub const DEFAULT_PERCENTILES: [f64; 7] = [2.5, 5.0, 16.0, 50.0, 84.0, 95.0, 97.5];

/// Two-pass mean: the plain mean plus the mean of the residuals it leaves behind.
/// `NaN` for an empty slice.
pub fn mean(x: &[f64]) -> f64 {
    if x.is_empty() {
        return f64::NAN;
    }
    let n = x.len() as f64;
    let m = x.iter().sum::<f64>() / n;
    m + x.iter().map(|v| v - m).sum::<f64>() / n
}

/// Unbiased sample variance (divisor `n - 1`), two-pass. `NaN` for fewer than two values.
pub fn variance(x: &[f64]) -> f64 {
    if x.len() < 2 {
        return f64::NAN;
    }
    let m = mean(x);
    let ss: f64 = x.iter().map(|v| (v - m) * (v - m)).sum();
    let comp: f64 = x.iter().map(|v| v - m).sum();
    (ss - comp * comp / x.len() as f64) / (x.len() - 1) as f64
}

/// Sample standard deviation (see [`variance`]). `NaN` for fewer than two values: one value has no
/// spread estimate, and `NaN.max(0.0)` would otherwise turn it into 0.
pub fn std_dev(x: &[f64]) -> f64 {
    let v = variance(x);
    if v.is_nan() {
        f64::NAN
    } else {
        v.max(0.0).sqrt()
    }
}

/// Fewest samples for which correlations and bootstrap standard errors are reported: with two
/// pairs every non-tied parameter correlates perfectly, and two values bootstrap to nothing.
pub const MIN_SAMPLES: usize = 3;

/// Moment skewness `g1 = m3 / m2^(3/2)` with population central moments (divisor `n`).
/// `None` for fewer than three values or a constant sample.
pub fn skewness(x: &[f64]) -> Option<f64> {
    if x.len() < 3 {
        return None;
    }
    let n = x.len() as f64;
    let m = mean(x);
    let m2 = x.iter().map(|v| (v - m).powi(2)).sum::<f64>() / n;
    let m3 = x.iter().map(|v| (v - m).powi(3)).sum::<f64>() / n;
    (m2 > 0.0).then(|| m3 / (m2 * m2.sqrt()))
}

/// Ascending copy under the IEEE total order.
pub fn sorted(x: &[f64]) -> Vec<f64> {
    let mut s = x.to_vec();
    s.sort_by(|a, b| a.total_cmp(b));
    s
}

/// Exact order-statistic percentile (nearest rank, Hyndman-Fan type 1): the smallest sample
/// value `x_(k)` with `k / n >= p / 100`, i.e. `k = max(1, ceil(n p / 100))`, the rank computed
/// in exact integer arithmetic on the decimal value of `p`. Always returns a value that occurs
/// in the sample. `sorted` must be ascending and non-empty; `p` in `[0, 100]`.
pub fn percentile_sorted(sorted: &[f64], p: f64) -> f64 {
    let n = sorted.len();
    sorted[percentile_rank(n, p).min(n) - 1]
}

/// `max(1, ceil(n p / 100))` with `p` taken as the shortest decimal that rounds to the same
/// `f64` (so 2.2 is 2.2, not 2.20000000000000017...), scaled to an integer.
fn percentile_rank(n: usize, p: f64) -> usize {
    if p.is_nan() || p <= 0.0 {
        return 1;
    }
    // shortest round-trip digits: "d.ddde-x" style, normalised to digits * 10^-scale
    let text = format!("{p:e}");
    let (mantissa, exponent) = text.split_once('e').unwrap_or((text.as_str(), "0"));
    let exponent: i32 = exponent.parse().unwrap_or(0);
    let (whole, frac) = mantissa.split_once('.').unwrap_or((mantissa, ""));
    let digits: u128 = format!("{whole}{frac}").parse().unwrap_or(0);
    // p = digits * 10^(exponent - frac.len())
    let power = exponent - frac.len() as i32;
    // k = ceil(n * digits * 10^power / 100) = ceil(n * digits / 10^(2 - power))
    let numerator = n as u128 * digits;
    let shift = 2 - power;
    let k = if shift <= 0 {
        // p is a large integer multiple of 10^|shift|: never exceeds 100 in range, keep it exact anyway
        numerator.saturating_mul(10u128.pow(shift.unsigned_abs().min(30)))
    } else if shift > 38 {
        // the quotient is below 1 for any realistic n: ceil of a positive number
        u128::from(numerator > 0)
    } else {
        let divisor = 10u128.pow(shift as u32);
        numerator / divisor + u128::from(!numerator.is_multiple_of(divisor))
    };
    (k.min(n as u128) as usize).max(1)
}

/// Fixed-width histogram over `[min, max]`; the last bin is closed on the right.
#[derive(Debug, Clone, PartialEq)]
pub struct Histogram {
    pub edges: Vec<f64>,
    pub counts: Vec<u64>,
}

/// `bins` equal-width bins between the sample min and max. `None` for an empty sample or zero
/// bins. A constant sample puts every value in the first bin (all edges equal).
pub fn histogram(x: &[f64], bins: usize) -> Option<Histogram> {
    if x.is_empty() || bins == 0 {
        return None;
    }
    let lo = x.iter().copied().fold(f64::INFINITY, f64::min);
    let hi = x.iter().copied().fold(f64::NEG_INFINITY, f64::max);
    let width = hi - lo;
    let mut counts = vec![0u64; bins];
    for &v in x {
        let idx = if width > 0.0 {
            (((v - lo) / width * bins as f64).floor() as usize).min(bins - 1)
        } else {
            0
        };
        counts[idx] += 1;
    }
    let edges = (0..=bins)
        .map(|i| {
            if i == bins {
                hi
            } else {
                lo + width * i as f64 / bins as f64
            }
        })
        .collect();
    Some(Histogram { edges, counts })
}

/// Average (mid) ranks, 1-based: tied values share the mean of the ranks they span.
pub fn average_ranks(x: &[f64]) -> Vec<f64> {
    let mut order: Vec<usize> = (0..x.len()).collect();
    order.sort_by(|&a, &b| x[a].total_cmp(&x[b]).then(a.cmp(&b)));
    let mut ranks = vec![0.0; x.len()];
    let mut i = 0;
    while i < order.len() {
        let mut j = i + 1;
        while j < order.len() && x[order[j]].total_cmp(&x[order[i]]) == Ordering::Equal {
            j += 1;
        }
        let avg = (i + 1 + j) as f64 / 2.0;
        for &k in &order[i..j] {
            ranks[k] = avg;
        }
        i = j;
    }
    ranks
}

/// Pearson correlation, two-pass. `None` for fewer than two pairs, a constant input, unequal
/// lengths or a nonfinite result. Clamped to `[-1, 1]` against rounding.
pub fn pearson(x: &[f64], y: &[f64]) -> Option<f64> {
    if x.len() != y.len() || x.len() < 2 {
        return None;
    }
    let (mx, my) = (mean(x), mean(y));
    let (mut sxy, mut sxx, mut syy) = (0.0, 0.0, 0.0);
    for (a, b) in x.iter().zip(y) {
        let (da, db) = (a - mx, b - my);
        sxy += da * db;
        sxx += da * da;
        syy += db * db;
    }
    if !(sxx > 0.0 && syy > 0.0) {
        return None;
    }
    let r = sxy / (sxx.sqrt() * syy.sqrt());
    r.is_finite().then(|| r.clamp(-1.0, 1.0))
}

/// Spearman rank correlation: Pearson on average ranks, so ties are handled exactly.
pub fn spearman(x: &[f64], y: &[f64]) -> Option<f64> {
    if x.len() != y.len() {
        return None;
    }
    pearson(&average_ranks(x), &average_ranks(y))
}

/// Keep the positions where both series have a finite value.
pub fn complete_pairs(x: &[Option<f64>], y: &[Option<f64>]) -> (Vec<f64>, Vec<f64>) {
    x.iter()
        .zip(y)
        .filter_map(|(a, b)| match (a, b) {
            (Some(a), Some(b)) if a.is_finite() && b.is_finite() => Some((*a, *b)),
            _ => None,
        })
        .unzip()
}

/// Pairwise-complete Pearson matrix over aligned series (`None` entries are missing samples).
/// Entry `[i][j]` is `None` when fewer than [`MIN_SAMPLES`] complete pairs exist or either side is constant.
pub fn pearson_matrix(series: &[Vec<Option<f64>>]) -> Vec<Vec<Option<f64>>> {
    let k = series.len();
    let mut out = vec![vec![None; k]; k];
    for i in 0..k {
        for j in i..k {
            let (a, b) = complete_pairs(&series[i], &series[j]);
            let r = if a.len() < MIN_SAMPLES {
                None
            } else {
                pearson(&a, &b)
            };
            out[i][j] = r;
            out[j][i] = r;
        }
    }
    out
}

/// Distribution summary of one response sample.
#[derive(Debug, Clone, PartialEq)]
pub struct Summary {
    pub n: usize,
    pub mean: f64,
    pub std: f64,
    pub skewness: Option<f64>,
    pub min: f64,
    pub max: f64,
    /// `(p, value)` in the order requested
    pub percentiles: Vec<(f64, f64)>,
}

/// Summarize a sample. `None` for an empty sample.
pub fn summarize(x: &[f64], percentiles: &[f64]) -> Option<Summary> {
    if x.is_empty() {
        return None;
    }
    let s = sorted(x);
    Some(Summary {
        n: x.len(),
        mean: mean(x),
        std: std_dev(x),
        skewness: skewness(x),
        min: s[0],
        max: s[s.len() - 1],
        percentiles: percentiles
            .iter()
            .map(|&p| (p, percentile_sorted(&s, p)))
            .collect(),
    })
}

/// SplitMix64: tiny fixed-seed stream for the bootstrap.
struct SplitMix(u64);

impl SplitMix {
    fn next_u64(&mut self) -> u64 {
        self.0 = self.0.wrapping_add(0x9E37_79B9_7F4A_7C15);
        let mut z = self.0;
        z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
        z ^ (z >> 31)
    }
    /// uniform index in `[0, n)`
    fn index(&mut self, n: usize) -> usize {
        let u = (self.next_u64() >> 11) as f64 * (1.0 / (1u64 << 53) as f64);
        ((u * n as f64) as usize).min(n - 1)
    }
}

/// Bootstrap standard errors of the sample std and of each requested percentile.
#[derive(Debug, Clone, PartialEq)]
pub struct BootstrapSe {
    pub resamples: usize,
    pub std_se: f64,
    /// `(p, standard error)` in the order requested
    pub percentile_se: Vec<(f64, f64)>,
}

/// Nonparametric bootstrap: `resamples` with-replacement draws of the sample (fixed `seed`), the
/// standard error of each statistic being the sample std of its replicates. `None` for fewer
/// than [`MIN_SAMPLES`] values or fewer than two resamples.
pub fn bootstrap_se(
    x: &[f64],
    percentiles: &[f64],
    resamples: usize,
    seed: u64,
) -> Option<BootstrapSe> {
    let n = x.len();
    if n < MIN_SAMPLES || resamples < 2 {
        return None;
    }
    let mut rng = SplitMix(seed);
    let mut stds = Vec::with_capacity(resamples);
    let mut pcts: Vec<Vec<f64>> = vec![Vec::with_capacity(resamples); percentiles.len()];
    let mut buf = vec![0.0; n];
    for _ in 0..resamples {
        for slot in buf.iter_mut() {
            *slot = x[rng.index(n)];
        }
        stds.push(std_dev(&buf));
        buf.sort_by(|a, b| a.total_cmp(b));
        for (col, &p) in pcts.iter_mut().zip(percentiles) {
            col.push(percentile_sorted(&buf, p));
        }
    }
    Some(BootstrapSe {
        resamples,
        std_se: std_dev(&stds),
        percentile_se: percentiles
            .iter()
            .zip(&pcts)
            .map(|(&p, col)| (p, std_dev(col)))
            .collect(),
    })
}

/// Sampled input columns (one column per parameter, one entry per sample) with their average
/// ranks, ready to be correlated against many responses.
#[derive(Debug, Clone)]
pub struct RankedInputs {
    cols: Vec<Vec<f64>>,
    ranks: Vec<Vec<f64>>,
}

/// One parameter's correlation with a response.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Correlation {
    pub parameter: usize,
    pub pearson: f64,
    pub spearman: f64,
}

impl RankedInputs {
    pub fn new(cols: Vec<Vec<f64>>) -> Self {
        let ranks = cols.iter().map(|c| average_ranks(c)).collect();
        Self { cols, ranks }
    }

    pub fn n_parameters(&self) -> usize {
        self.cols.len()
    }

    /// Restrict every column to the given sample positions (ranks are recomputed).
    pub fn subset(&self, rows: &[usize]) -> Self {
        Self::new(
            self.cols
                .iter()
                .map(|c| rows.iter().map(|&i| c[i]).collect())
                .collect(),
        )
    }

    /// Pearson and Spearman correlation of every parameter with `y` (one entry per sample).
    /// Parameters that are constant over the sample, or give a nonfinite result, are `None`.
    pub fn correlate(&self, y: &[f64]) -> Vec<Option<(f64, f64)>> {
        let ry = average_ranks(y);
        self.cols
            .iter()
            .zip(&self.ranks)
            .map(|(c, rc)| Some((pearson(c, y)?, pearson(rc, &ry)?)))
            .collect()
    }
}

/// Rank correlations by `|spearman|` descending (ties: `|pearson|` descending, then parameter
/// index ascending) and keep the first `top`. `None` entries are dropped.
pub fn top_correlations(all: &[Option<(f64, f64)>], top: usize) -> Vec<Correlation> {
    let mut v: Vec<Correlation> = all
        .iter()
        .enumerate()
        .filter_map(|(parameter, c)| {
            c.map(|(pearson, spearman)| Correlation {
                parameter,
                pearson,
                spearman,
            })
        })
        .collect();
    v.sort_by(|a, b| {
        b.spearman
            .abs()
            .total_cmp(&a.spearman.abs())
            .then(b.pearson.abs().total_cmp(&a.pearson.abs()))
            .then(a.parameter.cmp(&b.parameter))
    });
    v.truncate(top);
    v
}

#[cfg(test)]
mod tests {
    use super::*;

    fn close(a: f64, b: f64) {
        assert!((a - b).abs() <= 1e-12 * b.abs().max(1.0), "{a} vs {b}");
    }

    #[test]
    fn mean_and_variance_by_hand() {
        let x = [2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0];
        close(mean(&x), 5.0);
        // sum of squared deviations is 32, divisor n - 1 = 7
        close(variance(&x), 32.0 / 7.0);
        close(std_dev(&x), (32.0f64 / 7.0).sqrt());
        assert!(mean(&[]).is_nan());
        assert!(variance(&[1.0]).is_nan());
    }

    #[test]
    fn variance_survives_a_large_offset() {
        // naive sum-of-squares loses every digit here; two-pass does not
        let x = [1e9 + 4.0, 1e9 + 7.0, 1e9 + 13.0, 1e9 + 16.0];
        close(variance(&x), 30.0);
    }

    #[test]
    fn skewness_by_hand() {
        // deviations -2, -1, 3: m2 = 14/3, m3 = 6
        let g1 = 6.0 / ((14.0 / 3.0) * (14.0f64 / 3.0).sqrt());
        close(skewness(&[1.0, 2.0, 6.0]).unwrap(), g1);
        close(skewness(&[1.0, 2.0, 3.0, 4.0, 5.0]).unwrap(), 0.0);
        assert!(skewness(&[1.0, 2.0]).is_none());
        assert!(skewness(&[3.0, 3.0, 3.0]).is_none());
        // mirror image flips the sign
        close(skewness(&[-1.0, -2.0, -6.0]).unwrap(), -g1);
    }

    #[test]
    fn percentiles_are_nearest_rank_order_statistics() {
        let s: Vec<f64> = (1..=10).map(f64::from).collect();
        assert_eq!(percentile_sorted(&s, 0.0), 1.0);
        assert_eq!(percentile_sorted(&s, 2.5), 1.0); // ceil(0.25) = 1
        assert_eq!(percentile_sorted(&s, 10.0), 1.0); // ceil(1) = 1, exactly on a rank
        assert_eq!(percentile_sorted(&s, 10.1), 2.0);
        assert_eq!(percentile_sorted(&s, 50.0), 5.0);
        assert_eq!(percentile_sorted(&s, 84.0), 9.0); // ceil(8.4) = 9
        assert_eq!(percentile_sorted(&s, 95.0), 10.0); // ceil(9.5) = 10
        assert_eq!(percentile_sorted(&s, 100.0), 10.0);
        // 40 values: 2.5 % is exactly rank 1, 97.5 % exactly rank 39
        let t: Vec<f64> = (1..=40).map(f64::from).collect();
        assert_eq!(percentile_sorted(&t, 2.5), 1.0);
        assert_eq!(percentile_sorted(&t, 97.5), 39.0);
        assert_eq!(percentile_sorted(&[7.0], 99.0), 7.0);
    }

    #[test]
    fn summary_sorts_and_reports_extremes() {
        let x = [9.0, 1.0, 5.0, 3.0, 7.0];
        let s = summarize(&x, &[50.0, 100.0]).unwrap();
        assert_eq!(s.n, 5);
        close(s.mean, 5.0);
        close(s.std, 10.0f64.sqrt());
        assert_eq!((s.min, s.max), (1.0, 9.0));
        assert_eq!(s.percentiles, vec![(50.0, 5.0), (100.0, 9.0)]);
        close(s.skewness.unwrap(), 0.0);
        assert!(summarize(&[], &[50.0]).is_none());
    }

    #[test]
    fn histogram_bins_and_edges() {
        let x = [0.0, 1.0, 2.0, 3.0, 4.0];
        let h = histogram(&x, 2).unwrap();
        assert_eq!(h.edges, vec![0.0, 2.0, 4.0]);
        // 2.0 falls on the interior edge and goes right; the maximum lands in the last bin
        assert_eq!(h.counts, vec![2, 3]);
        let c = histogram(&[5.0, 5.0], 3).unwrap();
        assert_eq!(c.counts, vec![2, 0, 0]);
        assert_eq!(c.edges, vec![5.0; 4]);
        assert!(histogram(&x, 0).is_none());
        assert!(histogram(&[], 3).is_none());
        assert_eq!(histogram(&x, 4).unwrap().counts.iter().sum::<u64>(), 5);
    }

    #[test]
    fn average_ranks_share_tied_positions() {
        assert_eq!(average_ranks(&[10.0, 20.0, 30.0]), vec![1.0, 2.0, 3.0]);
        assert_eq!(
            average_ranks(&[1.0, 2.0, 2.0, 3.0]),
            vec![1.0, 2.5, 2.5, 4.0]
        );
        assert_eq!(average_ranks(&[5.0, 5.0, 5.0]), vec![2.0, 2.0, 2.0]);
        assert_eq!(average_ranks(&[3.0, 1.0, 2.0]), vec![3.0, 1.0, 2.0]);
    }

    #[test]
    fn pearson_by_hand_and_extremes() {
        close(
            pearson(&[1.0, 2.0, 3.0, 4.0, 5.0], &[2.0, 4.0, 5.0, 4.0, 5.0]).unwrap(),
            6.0 / 60.0f64.sqrt(),
        );
        let x = [1.0, 2.0, 3.0, 4.0];
        close(pearson(&x, &[10.0, 20.0, 30.0, 40.0]).unwrap(), 1.0);
        close(pearson(&x, &[-1.0, -2.0, -3.0, -4.0]).unwrap(), -1.0);
        assert!(pearson(&x, &[2.0; 4]).is_none());
        assert!(pearson(&x, &[1.0, 2.0]).is_none());
        assert!(pearson(&[1.0], &[1.0]).is_none());
    }

    #[test]
    fn spearman_with_ties_and_monotone_nonlinearity() {
        // ranks x = 1, 2.5, 2.5, 4 and y = 1, 3, 2, 4: r = 4.5 / sqrt(4.5 * 5)
        close(
            spearman(&[1.0, 2.0, 2.0, 3.0], &[1.0, 3.0, 2.0, 4.0]).unwrap(),
            0.9f64.sqrt(),
        );
        // monotone but strongly nonlinear: Spearman is exactly 1, Pearson is not
        let x = [1.0, 2.0, 3.0, 4.0, 5.0];
        let y = [1.0, 8.0, 27.0, 64.0, 1000.0];
        close(spearman(&x, &y).unwrap(), 1.0);
        assert!(pearson(&x, &y).unwrap() < 0.99);
        close(spearman(&x, &[5.0, 4.0, 3.0, 2.0, 1.0]).unwrap(), -1.0);
    }

    #[test]
    fn complete_pairs_drop_missing_and_nonfinite() {
        let a = [Some(1.0), None, Some(3.0), Some(f64::NAN)];
        let b = [Some(4.0), Some(5.0), Some(6.0), Some(7.0)];
        assert_eq!(complete_pairs(&a, &b), (vec![1.0, 3.0], vec![4.0, 6.0]));
    }

    #[test]
    fn pearson_matrix_is_symmetric_with_unit_diagonal() {
        let a = vec![Some(1.0), Some(2.0), Some(3.0), Some(4.0)];
        let b = vec![Some(2.0), Some(4.0), Some(6.0), Some(8.0)];
        let c = vec![Some(4.0), Some(3.0), None, Some(1.0)];
        let flat = vec![Some(1.0); 4];
        let m = pearson_matrix(&[a, b, c, flat]);
        close(m[0][0].unwrap(), 1.0);
        close(m[0][1].unwrap(), 1.0);
        close(m[1][0].unwrap(), 1.0);
        // pairs (1,4), (2,3), (4,1) are exactly anti-correlated
        close(m[0][2].unwrap(), -1.0);
        assert_eq!(m[0][2], m[2][0]);
        // a constant series has no defined correlation, not even with itself
        assert!(m[3][3].is_none() && m[0][3].is_none());
    }

    #[test]
    fn bootstrap_is_deterministic_and_sane() {
        let x: Vec<f64> = (0..50).map(|i| ((i * 37) % 50) as f64).collect();
        let p = [50.0, 95.0];
        let a = bootstrap_se(&x, &p, 64, 11).unwrap();
        let b = bootstrap_se(&x, &p, 64, 11).unwrap();
        assert_eq!(a, b);
        assert_ne!(a, bootstrap_se(&x, &p, 64, 12).unwrap());
        assert_eq!(a.resamples, 64);
        assert_eq!(a.percentile_se.len(), 2);
        assert!(a.std_se > 0.0 && a.std_se < std_dev(&x));
        // a constant sample has no sampling spread at all
        let c = bootstrap_se(&[2.0; 10], &p, 16, 3).unwrap();
        assert_eq!(c.std_se, 0.0);
        assert!(c.percentile_se.iter().all(|&(_, s)| s == 0.0));
        assert!(bootstrap_se(&x, &p, 1, 3).is_none());
        assert!(bootstrap_se(&[1.0], &p, 8, 3).is_none());
    }

    #[test]
    fn input_correlations_rank_by_abs_spearman() {
        let y = vec![1.0, 2.0, 3.0, 4.0, 5.0];
        let ranked = RankedInputs::new(vec![
            vec![5.0, 4.0, 3.0, 2.0, 1.0],      // anti-correlated
            vec![2.0, 2.0, 2.0, 2.0, 2.0],      // constant: dropped
            vec![10.0, 20.0, 30.0, 40.0, 50.0], // perfectly correlated
            vec![1.0, 3.0, 2.0, 5.0, 4.0],      // partial
        ]);
        assert_eq!(ranked.n_parameters(), 4);
        let all = ranked.correlate(&y);
        assert!(all[1].is_none());
        let top = top_correlations(&all, 10);
        let order: Vec<usize> = top.iter().map(|c| c.parameter).collect();
        // the two exact lines come first (their order is a rounding-level tie), then the partial
        assert_eq!(order.len(), 3);
        assert!(order[..2].contains(&0) && order[..2].contains(&2));
        assert_eq!(order[2], 3);
        for c in &top[..2] {
            close(c.spearman.abs(), 1.0);
            close(c.pearson.abs(), 1.0);
        }
        // ranks 1,3,2,5,4 vs 1..5: sum d^2 = 4, rho = 1 - 6*4/(5*24) = 0.8
        close(top[2].spearman, 0.8);
        assert_eq!(top_correlations(&all, 2).len(), 2);
        assert!(top_correlations(&all, 0).is_empty());
    }

    #[test]
    fn subset_matches_direct_computation() {
        let ranked = RankedInputs::new(vec![vec![1.0, 9.0, 2.0, 8.0, 3.0]]);
        let sub = ranked.subset(&[0, 2, 4]);
        let y = [10.0, 20.0, 30.0];
        let got = sub.correlate(&y)[0].unwrap();
        close(got.0, pearson(&[1.0, 2.0, 3.0], &y).unwrap());
        close(got.1, 1.0);
    }

    #[test]
    fn p140_ps_f1_std_of_a_single_value_is_undefined() {
        // the unbiased spread of one value does not exist: NaN (variance already says so), never 0
        let sd = std_dev(&[3.0]);
        assert!(sd.is_nan(), "std_dev of one value is {sd}");
        assert!(std_dev(&[]).is_nan());
        let one = summarize(&[3.0], &[50.0]).unwrap();
        assert!(one.std.is_nan(), "summary std of one value is {}", one.std);
        // two equal values do have a spread estimate, and it is exactly zero
        assert_eq!(std_dev(&[3.0, 3.0]), 0.0);
    }

    #[test]
    fn p140_ps_f11_percentile_rank_is_exact_for_decimal_p() {
        // k = ceil(n p / 100) in exact arithmetic; the float product n * p lands just above the integer
        // for these decimals: (1500, 2.2) -> 33 (not 34), (750, 4.4) -> 33, (10500, 95.4) -> 10017
        let ramp = |n: usize| (1..=n).map(|v| v as f64).collect::<Vec<_>>();
        assert_eq!(percentile_sorted(&ramp(1500), 2.2), 33.0);
        assert_eq!(percentile_sorted(&ramp(750), 4.4), 33.0);
        assert_eq!(percentile_sorted(&ramp(10500), 95.4), 10017.0);
        // the cases that were already right stay right
        assert_eq!(percentile_sorted(&ramp(40), 2.5), 1.0);
        assert_eq!(percentile_sorted(&ramp(40), 97.5), 39.0);
        assert_eq!(percentile_sorted(&ramp(8), 50.0), 4.0);
        assert_eq!(percentile_sorted(&ramp(8), 0.0), 1.0);
        assert_eq!(percentile_sorted(&ramp(8), 100.0), 8.0);
    }
}
