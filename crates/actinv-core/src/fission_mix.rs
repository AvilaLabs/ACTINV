//! P153 fission-yield selection arithmetic: the rate-weighted mixing weights over the tabulated incident
//! energies, and the cumulative-yield overlap report. Pure functions over their arguments; the run supplies the
//! library's fission rows and the chain.
use crate::chain::Chain;
use actinv_data::fission::hat_weights;
use std::collections::{BTreeMap, HashMap};

/// Lethargy midpoint of a group, `sqrt(E_lo * E_hi)` (eV): the energy the hat functions are evaluated at.
pub fn group_midpoint_ev(low_ev: f64, high_ev: f64) -> f64 {
    (low_ev * high_ev).sqrt()
}

/// Weights `w_k = sum_g r_g h_k(E_g) / sum_g r_g` of the tabulated incident energies `grid` (ascending, eV) for
/// fission-rate groups `(E_g, r_g)`. The rates are normalised before the hat functions are applied, so a spectrum
/// confined to one group reproduces that energy's `hat_weights` bit for bit. `None` when the total rate is not
/// positive (no fission in the spectrum).
pub fn rate_weights(grid: &[f64], groups: &[(f64, f64)]) -> Option<Vec<f64>> {
    let total: f64 = groups.iter().map(|&(_, rate)| rate).sum();
    if !(total > 0.0 && total.is_finite()) || grid.is_empty() {
        return None;
    }
    let mut weights = vec![0.0; grid.len()];
    for &(energy, rate) in groups {
        if rate == 0.0 {
            continue;
        }
        let share = rate / total;
        for (weight, hat) in weights.iter_mut().zip(hat_weights(grid, energy)) {
            if hat != 0.0 {
                *weight += share * hat;
            }
        }
    }
    Some(weights)
}

/// Direct decay precursors of every chain state: `precursors[product]` lists the chain states whose decay leads
/// to it in one step (a positive off-diagonal decay edge; leakage and the unit state are not nuclides).
pub fn decay_precursors(chain: &Chain) -> HashMap<(i32, i32), Vec<(i32, i32)>> {
    let mut out: HashMap<(i32, i32), Vec<(i32, i32)>> = HashMap::new();
    for &(row, column, value) in &chain.decay {
        if row == column
            || value <= 0.0
            || row == chain.leak
            || column == chain.leak
            || row == chain.unit
            || column == chain.unit
        {
            continue;
        }
        out.entry(chain.keys[row])
            .or_default()
            .push(chain.keys[column]);
    }
    out
}

/// What cumulative yields count twice for one parent. A product is *overlapping* when it receives a positive
/// yield, is a chain state, and has a direct decay precursor that also receives a positive yield from the same
/// parent: the cumulative yield already includes the precursor's feeding, and the chain adds it again.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct CumulativeOverlap {
    /// Sum of the cumulative yields of every product.
    pub summed_yield: f64,
    /// Sum of the yields of the overlapping products.
    pub overlap_yield: f64,
    pub overlap_products: usize,
    /// Products with a positive yield.
    pub products_with_yield: usize,
}

impl CumulativeOverlap {
    /// `overlap_yield / summed_yield`, 0 when the sum is not positive.
    pub fn share(&self) -> f64 {
        if self.summed_yield > 0.0 {
            self.overlap_yield / self.summed_yield
        } else {
            0.0
        }
    }
}

/// The overlap of one parent's cumulative `products` (yield per fission, `sum` their total) in a chain.
pub fn cumulative_overlap(
    products: &BTreeMap<(i32, i32), f64>,
    sum: f64,
    chain: &Chain,
    precursors: &HashMap<(i32, i32), Vec<(i32, i32)>>,
) -> CumulativeOverlap {
    let mut overlap_yield = 0.0;
    let mut overlap_products = 0;
    let mut products_with_yield = 0;
    for (product, &yield_value) in products {
        if yield_value <= 0.0 {
            continue;
        }
        products_with_yield += 1;
        if !chain.index.contains_key(product) {
            continue;
        }
        let fed_by_yielded_precursor = precursors.get(product).is_some_and(|list| {
            list.iter()
                .any(|precursor| products.get(precursor).is_some_and(|&value| value > 0.0))
        });
        if fed_by_yielded_precursor {
            overlap_yield += yield_value;
            overlap_products += 1;
        }
    }
    CumulativeOverlap {
        summed_yield: sum,
        overlap_yield,
        overlap_products,
        products_with_yield,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::chain::ChainLedger;

    const GRID: [f64; 3] = [0.0253, 5.0e5, 1.4e7];

    #[test]
    fn weights_sum_to_one() {
        let groups = [(1.0e-2, 3.0), (2.0e5, 1.0), (8.0e6, 2.0), (1.5e7, 0.5)];
        let weights = rate_weights(&GRID, &groups).unwrap();
        assert_eq!(weights.len(), 3);
        assert!((weights.iter().sum::<f64>() - 1.0).abs() < 1e-14);
        assert!(weights.iter().all(|&weight| weight >= 0.0));
    }

    #[test]
    fn one_group_reproduces_the_hat_weights_exactly() {
        let energy = group_midpoint_ev(1.0e6, 4.0e6);
        assert_eq!(energy, 2.0e6);
        let weights = rate_weights(&GRID, &[(energy, 7.5e-9)]).unwrap();
        assert_eq!(weights, hat_weights(&GRID, energy));
    }

    #[test]
    fn energies_outside_the_grid_clamp_to_its_ends() {
        assert_eq!(
            rate_weights(&GRID, &[(1.0e-5, 2.0)]).unwrap(),
            vec![1.0, 0.0, 0.0]
        );
        assert_eq!(
            rate_weights(&GRID, &[(3.0e7, 2.0)]).unwrap(),
            vec![0.0, 0.0, 1.0]
        );
        // the mean of a thermal and a fast group is the rate-weighted mix of the two end tables
        let weights = rate_weights(&GRID, &[(1.0e-5, 1.0), (3.0e7, 3.0)]).unwrap();
        assert_eq!(weights, vec![0.25, 0.0, 0.75]);
    }

    #[test]
    fn no_fission_rate_gives_no_weights() {
        assert!(rate_weights(&GRID, &[]).is_none());
        assert!(rate_weights(&GRID, &[(1.0e6, 0.0)]).is_none());
    }

    fn toy_chain() -> Chain {
        // states: A (parent product, decays to B), B (decays to C), C (stable), D (stable, unfed)
        let keys = vec![(1, 0), (2, 0), (3, 0), (4, 0)];
        Chain {
            index: keys.iter().enumerate().map(|(i, &k)| (k, i)).collect(),
            keys,
            lambda: vec![1.0, 1.0, 0.0, 0.0],
            // (row, column): the daughter row, the decaying column; plus loss diagonals and a leakage entry
            decay: vec![
                (1, 0, 1.0),
                (2, 1, 1.0),
                (0, 0, -1.0),
                (1, 1, -1.0),
                (5, 2, 0.5),
            ],
            leak: 5,
            unit: 4,
            n: 6,
            ledger: ChainLedger::default(),
        }
    }

    #[test]
    fn cumulative_overlap_counts_products_fed_by_a_yielded_precursor() {
        let chain = toy_chain();
        let precursors = decay_precursors(&chain);
        assert_eq!(precursors[&(2, 0)], vec![(1, 0)]);
        assert_eq!(precursors[&(3, 0)], vec![(2, 0)]);
        assert!(!precursors.contains_key(&(1, 0)));
        let products: BTreeMap<_, _> = [
            ((1, 0), 0.5),
            ((2, 0), 0.25),
            ((3, 0), 0.125),
            ((4, 0), 0.0625),
            ((9, 0), 0.0625),
        ]
        .into_iter()
        .collect();
        let sum: f64 = products.values().sum();
        let overlap = cumulative_overlap(&products, sum, &chain, &precursors);
        // B is fed by A and C by B, both yielded; A has no precursor; D has none; (9,0) is not in the chain
        assert_eq!(overlap.overlap_products, 2);
        assert_eq!(overlap.overlap_yield, 0.375);
        assert_eq!(overlap.products_with_yield, 5);
        assert_eq!(overlap.summed_yield, 1.0);
        assert_eq!(overlap.share(), 0.375);
        // a precursor with no yield of its own does not count
        let mut without_b = products.clone();
        without_b.remove(&(2, 0));
        let sum: f64 = without_b.values().sum();
        let overlap = cumulative_overlap(&without_b, sum, &chain, &precursors);
        assert_eq!(overlap.overlap_products, 0);
        assert_eq!(overlap.share(), 0.0);
    }
}
