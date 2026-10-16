#![allow(non_snake_case)] // field names are the JSON wire format (Z, A, LISO)
//! FISPACT-II DEPLETION parity: for every initial-material nuclide, how much of it each step
//! depleted and which channels removed it. Emitted as `steps[].summary.depletion` only when the
//! spec's `summary.depletion` is true; absence leaves results byte-identical.
//!
//! The per-channel rates are the solver's own. A reaction channel is the sum of the nuclide's
//! activation-library loss rows with one MT, collapsed by `chain::reaction_rates` under the
//! step's spectrum (shielding, `rate_scale` and every other rate switch included) and multiplied
//! by the flux multiplier the step ran at; the decay channel is the effective decay constant the
//! solver used. The channels therefore sum exactly to the nuclide's total removal rate, the
//! diagonal of the step matrix, and the shares sum to one.
//!
//! With an MF=33 covariance each reaction channel also carries the relative standard uncertainty
//! of its one-group rate: the covariance block of the loss rows collapsed under the step's flux
//! (`CovarianceLibrary::collapse_weighted_multi`, the same collapse the uncertainty bands use),
//! contracted with the channel's rate weights.
use crate::chain::LossRate;
use crate::pathways::reaction_label;
use crate::run::name_of;
use actinv_data::covariance::CollapsedCovariance;
use serde::Serialize;
use std::collections::{BTreeMap, HashMap};

/// One removal channel of one nuclide, in one step.
#[derive(Debug, Clone, Serialize)]
pub struct DepletionChannel {
    /// `decay`, or `MT<n>` for a reaction channel.
    pub channel: String,
    /// `decay` or `reaction`.
    pub kind: &'static str,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub mt: Option<i32>,
    /// Conventional reaction name for a neutron projectile (`(n,g)`), else `MT<n>`.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub reaction: Option<String>,
    /// Removal rate per atom (s^-1) under the step's flux, averaged over a power step.
    pub rate_per_s: f64,
    /// `rate_per_s` over the nuclide's total removal rate.
    pub share: f64,
    /// Relative standard uncertainty of `rate_per_s`; present only with MF=33 covariance and
    /// only for a channel the covariance (or, for decay, the half-life uncertainty) covers.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub relative_standard_uncertainty: Option<f64>,
}

/// One initial nuclide's depletion over one step.
#[derive(Debug, Clone, Serialize)]
pub struct DepletionNuclide {
    pub nuclide: String,
    pub Z: i32,
    pub A: i32,
    pub LISO: i32,
    /// Atoms per gram of initial material at t = 0.
    pub initial_atoms_per_g: f64,
    pub atoms_start_per_g: f64,
    pub atoms_end_per_g: f64,
    /// `atoms_start_per_g - atoms_end_per_g` over this step: net of ingrowth from other
    /// nuclides, and of a partition or removal applied at the step start.
    pub depleted_atoms_per_g: f64,
    pub depleted_fraction: f64,
    /// `initial_atoms_per_g - atoms_end_per_g`.
    pub cumulative_depleted_atoms_per_g: f64,
    pub cumulative_depleted_fraction: f64,
    /// Total removal rate per atom (s^-1): decay plus every reaction channel.
    pub removal_rate_per_s: f64,
    /// Relative standard uncertainty of `removal_rate_per_s`, including the correlation between
    /// reaction channels; present only when every channel is covered.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub removal_rate_relative_standard_uncertainty: Option<f64>,
    /// Share of `removal_rate_per_s` carried by channels with an uncertainty; present with
    /// MF=33 covariance.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub uncertainty_covered_share: Option<f64>,
    /// Descending by rate, ties decay first then by MT.
    pub channels: Vec<DepletionChannel>,
}

/// The depletion table of one step.
#[derive(Debug, Clone, Serialize)]
pub struct StepDepletion {
    /// `coupled`: the solver's evolved populations. `trace_analytic`: trace mode holds the
    /// initial material constant, so the reservoir is depleted analytically,
    /// N(t) = N(0) exp(-integral of the removal rate), the same first-order law as the ledger's
    /// `max_burnup_fraction`; ingrowth from other nuclides is not included.
    pub model: &'static str,
    pub nuclides: Vec<DepletionNuclide>,
}

/// An initial-material nuclide the table follows.
#[derive(Debug, Clone, Copy)]
pub struct Tracked {
    pub key: (i32, i32),
    /// Chain column of the nuclide.
    pub column: usize,
    /// Atoms per gram of initial material.
    pub initial_atoms: f64,
    /// Effective decay constant (s^-1) the solver uses, decay_scale and half-life overrides
    /// included.
    pub lambda: f64,
    /// Relative standard uncertainty of the decay constant, when the data gives one.
    pub lambda_relative: Option<f64>,
}

/// The collapsed MF=33 covariance of the loss rows of the tracked nuclides, one parameter per
/// (spectrum, library row), with the nominal one-group cross section the collapse produced.
#[derive(Debug, Clone, Default)]
pub struct LossCovariance {
    index: HashMap<(usize, usize), usize>,
    sigma_b: Vec<f64>,
    /// Row-major `n x n` covariance in barn^2.
    covariance_barn2: Vec<f64>,
}

impl LossCovariance {
    /// Select the parameters of a joint collapse whose library row `wanted` accepts.
    pub fn from_collapsed(collapsed: &CollapsedCovariance, wanted: impl Fn(usize) -> bool) -> Self {
        let total = collapsed.row_indices.len();
        let selected: Vec<usize> = (0..total)
            .filter(|&parameter| wanted(collapsed.row_indices[parameter]))
            .collect();
        let n = selected.len();
        let mut covariance_barn2 = vec![0.0; n * n];
        for (a, &left) in selected.iter().enumerate() {
            for (b, &right) in selected.iter().enumerate() {
                covariance_barn2[a * n + b] = collapsed.covariance_barn2[left * total + right];
            }
        }
        Self {
            index: selected
                .iter()
                .enumerate()
                .map(|(a, &parameter)| {
                    (
                        (
                            collapsed.param_spectrum[parameter],
                            collapsed.row_indices[parameter],
                        ),
                        a,
                    )
                })
                .collect(),
            sigma_b: selected
                .iter()
                .map(|&parameter| collapsed.one_group_barns[parameter])
                .collect(),
            covariance_barn2,
        }
    }

    /// Variance (s^-2) of the summed rate of the given loss rows under one spectrum. `rows` holds
    /// (library row, rate per atom in s^-1 including the flux multiplier); each rate scales with
    /// its row's one-group cross section, so d(rate)/d(sigma) = rate / sigma. None unless every
    /// row is covered with a positive nominal cross section.
    pub fn variance(&self, spectrum: usize, rows: &[(usize, f64)]) -> Option<f64> {
        let n = self.sigma_b.len();
        let mut weights: Vec<(usize, f64)> = Vec::with_capacity(rows.len());
        for &(row, rate) in rows {
            let a = *self.index.get(&(spectrum, row))?;
            let sigma = self.sigma_b[a];
            if sigma.is_nan() || sigma <= 0.0 {
                return None;
            }
            weights.push((a, rate / sigma));
        }
        let mut variance = 0.0;
        for &(a, wa) in &weights {
            for &(b, wb) in &weights {
                variance += wa * wb * self.covariance_barn2[a * n + b];
            }
        }
        Some(variance.max(0.0))
    }
}

/// Follows the initial nuclides from step to step and builds each step's table.
pub struct DepletionTracker {
    analytic: bool,
    neutron: bool,
    tracked: Vec<Tracked>,
    /// Atoms per gram at the end of the previous step (the initial atoms before the first).
    previous: Vec<f64>,
    /// Per spectrum slot (0 the base spectrum, then one per distinct step override), the loss
    /// rates of the tracked columns.
    losses: Vec<HashMap<usize, Vec<LossRate>>>,
    covariance: Option<LossCovariance>,
}

struct Channel {
    mt: Option<i32>,
    rate: f64,
    /// (library row, rate) of each loss row summed into a reaction channel.
    rows: Vec<(usize, f64)>,
}

impl DepletionTracker {
    /// `analytic` selects the trace-mode reservoir model. `slot_rates[s]` are the loss rates
    /// recorded under spectrum slot `s`.
    pub fn new(
        analytic: bool,
        neutron: bool,
        mut tracked: Vec<Tracked>,
        slot_rates: Vec<Vec<LossRate>>,
        covariance: Option<LossCovariance>,
    ) -> Self {
        // Largest first, ties by (ZA, LISO): a fixed order for the table.
        tracked.sort_by(|a, b| {
            b.initial_atoms
                .total_cmp(&a.initial_atoms)
                .then_with(|| a.key.cmp(&b.key))
        });
        let previous = tracked.iter().map(|t| t.initial_atoms).collect();
        let losses = slot_rates
            .into_iter()
            .map(|rates| {
                let mut by_column: HashMap<usize, Vec<LossRate>> = HashMap::new();
                for rate in rates {
                    if tracked.iter().any(|t| t.column == rate.column) {
                        by_column.entry(rate.column).or_default().push(rate);
                    }
                }
                by_column
            })
            .collect();
        Self {
            analytic,
            neutron,
            tracked,
            previous,
            losses,
            covariance,
        }
    }

    /// The table of one step run under spectrum slot `slot` at flux multiplier `multiplier` for
    /// `dt_s` seconds. `end_atoms(column)` gives the evolved atoms per gram at the step end; it
    /// is not read in the analytic model.
    pub fn step(
        &mut self,
        slot: usize,
        multiplier: f64,
        dt_s: f64,
        end_atoms: &dyn Fn(usize) -> f64,
    ) -> StepDepletion {
        let mut nuclides = Vec::with_capacity(self.tracked.len());
        for (tracked, previous) in self.tracked.iter().zip(self.previous.iter_mut()) {
            let mut by_mt: BTreeMap<i32, Vec<(usize, f64)>> = BTreeMap::new();
            if multiplier > 0.0 {
                let recorded = self
                    .losses
                    .get(slot)
                    .and_then(|columns| columns.get(&tracked.column));
                for loss in recorded.into_iter().flatten() {
                    let rate = loss.rate_per_s * multiplier;
                    if rate > 0.0 {
                        by_mt
                            .entry(loss.mt)
                            .or_default()
                            .push((loss.library_row, rate));
                    }
                }
            }
            let mut channels: Vec<Channel> = Vec::with_capacity(by_mt.len() + 1);
            if tracked.lambda > 0.0 {
                channels.push(Channel {
                    mt: None,
                    rate: tracked.lambda,
                    rows: Vec::new(),
                });
            }
            for (mt, rows) in by_mt {
                channels.push(Channel {
                    mt: Some(mt),
                    rate: rows.iter().map(|row| row.1).sum(),
                    rows,
                });
            }
            channels.sort_by(|a, b| b.rate.total_cmp(&a.rate).then_with(|| a.mt.cmp(&b.mt)));
            let total: f64 = channels.iter().map(|channel| channel.rate).sum();

            let covariance = self.covariance.as_ref();
            let relative = |channel: &Channel| -> Option<f64> {
                let covariance = covariance?;
                match channel.mt {
                    None => tracked.lambda_relative,
                    Some(_) => covariance
                        .variance(slot, &channel.rows)
                        .map(|variance| variance.sqrt() / channel.rate),
                }
            };
            let uncertainties: Vec<Option<f64>> = channels.iter().map(relative).collect();
            let (total_relative, covered_share) = match covariance {
                Some(covariance) if total > 0.0 => {
                    let covered: f64 = channels
                        .iter()
                        .zip(&uncertainties)
                        .filter(|(_, relative)| relative.is_some())
                        .map(|(channel, _)| channel.rate)
                        .sum();
                    let all_rows: Vec<(usize, f64)> = channels
                        .iter()
                        .filter(|channel| channel.mt.is_some())
                        .flat_map(|channel| channel.rows.iter().copied())
                        .collect();
                    // Reaction channels of one nuclide are correlated through the cross-MT
                    // covariance blocks, so their joint variance is taken over all rows at once.
                    let joint = if uncertainties.iter().all(Option::is_some) {
                        covariance.variance(slot, &all_rows).map(|variance| {
                            let decay = channels
                                .iter()
                                .zip(&uncertainties)
                                .filter(|(channel, _)| channel.mt.is_none())
                                .map(|(channel, relative)| {
                                    (channel.rate * relative.unwrap_or(0.0)).powi(2)
                                })
                                .sum::<f64>();
                            (variance + decay).sqrt() / total
                        })
                    } else {
                        None
                    };
                    (joint, Some(covered / total))
                }
                Some(_) => (None, Some(0.0)),
                None => (None, None),
            };

            let start = *previous;
            let end = if self.analytic {
                start * (-total * dt_s).exp()
            } else {
                end_atoms(tracked.column)
            };
            *previous = end;
            let fraction =
                |depleted: f64, base: f64| if base > 0.0 { depleted / base } else { 0.0 };
            nuclides.push(DepletionNuclide {
                nuclide: name_of(tracked.key.0, tracked.key.1),
                Z: tracked.key.0 / 1000,
                A: tracked.key.0 % 1000,
                LISO: tracked.key.1,
                initial_atoms_per_g: tracked.initial_atoms,
                atoms_start_per_g: start,
                atoms_end_per_g: end,
                depleted_atoms_per_g: start - end,
                depleted_fraction: fraction(start - end, start),
                cumulative_depleted_atoms_per_g: tracked.initial_atoms - end,
                cumulative_depleted_fraction: fraction(
                    tracked.initial_atoms - end,
                    tracked.initial_atoms,
                ),
                removal_rate_per_s: total,
                removal_rate_relative_standard_uncertainty: total_relative,
                uncertainty_covered_share: covered_share,
                channels: channels
                    .iter()
                    .zip(uncertainties)
                    .map(|(channel, relative)| DepletionChannel {
                        channel: match channel.mt {
                            None => "decay".to_owned(),
                            Some(mt) => format!("MT{mt}"),
                        },
                        kind: if channel.mt.is_none() {
                            "decay"
                        } else {
                            "reaction"
                        },
                        mt: channel.mt,
                        reaction: channel.mt.map(|mt| reaction_label(mt, self.neutron)),
                        rate_per_s: channel.rate,
                        share: channel.rate / total,
                        relative_standard_uncertainty: relative,
                    })
                    .collect(),
            });
        }
        StepDepletion {
            model: if self.analytic {
                "trace_analytic"
            } else {
                "coupled"
            },
            nuclides,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use actinv_data::covariance::{ComponentKind, CovarianceLibrary, ParsedComponent};
    use actinv_data::library::{Library, Row};

    const COLUMN: usize = 3;

    fn loss(row: usize, mt: i32, rate: f64) -> LossRate {
        LossRate {
            column: COLUMN,
            library_row: row,
            mt,
            rate_per_s: rate,
        }
    }

    fn tracked(lambda: f64, lambda_relative: Option<f64>) -> Tracked {
        Tracked {
            key: (26_056, 0),
            column: COLUMN,
            initial_atoms: 1.0e20,
            lambda,
            lambda_relative,
        }
    }

    fn close(actual: f64, expected: f64) {
        assert!(
            (actual - expected).abs() <= 1e-12 * expected.abs().max(1e-300),
            "{actual} vs {expected}"
        );
    }

    #[test]
    fn channels_are_ranked_and_shares_sum_to_one() {
        // Two (n,g) loss rows (summed into one channel), one (n,2n) row, and decay.
        let rates = vec![loss(0, 102, 0.2), loss(1, 102, 0.1), loss(2, 16, 0.4)];
        let mut tracker =
            DepletionTracker::new(false, true, vec![tracked(0.05, None)], vec![rates], None);
        let step = tracker.step(0, 2.0, 1.0, &|_| 4.0e19);
        assert_eq!(step.model, "coupled");
        let nuclide = &step.nuclides[0];
        let names: Vec<&str> = nuclide
            .channels
            .iter()
            .map(|c| c.channel.as_str())
            .collect();
        // multiplier 2: MT16 0.8, MT102 0.6, decay 0.05
        assert_eq!(names, ["MT16", "MT102", "decay"]);
        close(nuclide.channels[0].rate_per_s, 0.8);
        close(nuclide.channels[1].rate_per_s, 0.6);
        close(nuclide.channels[2].rate_per_s, 0.05);
        close(nuclide.removal_rate_per_s, 1.45);
        let shares: f64 = nuclide.channels.iter().map(|c| c.share).sum();
        assert!((shares - 1.0).abs() < 1e-12, "{shares}");
        close(nuclide.channels[2].share, 0.05 / 1.45);
        assert_eq!(nuclide.channels[0].reaction.as_deref(), Some("(n,2n)"));
        assert_eq!(nuclide.channels[1].reaction.as_deref(), Some("(n,g)"));
        assert_eq!(nuclide.channels[2].kind, "decay");
        assert!(nuclide.channels[2].mt.is_none());
        assert!(nuclide.channels[0].relative_standard_uncertainty.is_none());
        assert!(nuclide.uncertainty_covered_share.is_none());
        close(nuclide.depleted_atoms_per_g, 1.0e20 - 4.0e19);
        close(nuclide.depleted_fraction, 0.6);
        close(nuclide.cumulative_depleted_fraction, 0.6);
    }

    #[test]
    fn step_start_is_the_previous_step_end_and_cumulative_depletion_accumulates() {
        let mut tracker = DepletionTracker::new(
            false,
            true,
            vec![tracked(0.0, None)],
            vec![vec![loss(0, 102, 0.5)]],
            None,
        );
        let first = tracker.step(0, 1.0, 1.0, &|_| 8.0e19);
        let second = tracker.step(0, 1.0, 1.0, &|_| 4.0e19);
        let (first, second) = (&first.nuclides[0], &second.nuclides[0]);
        close(second.atoms_start_per_g, first.atoms_end_per_g);
        close(second.depleted_fraction, 0.5);
        close(second.cumulative_depleted_fraction, 0.6);
        close(second.cumulative_depleted_atoms_per_g, 6.0e19);
    }

    #[test]
    fn zero_flux_leaves_only_decay_and_no_flux_no_decay_leaves_nothing() {
        let rates = vec![loss(0, 102, 0.5)];
        let mut tracker = DepletionTracker::new(
            false,
            true,
            vec![tracked(0.25, None)],
            vec![rates.clone()],
            None,
        );
        let cooling = tracker.step(0, 0.0, 1.0, &|_| 1.0e20);
        assert_eq!(cooling.nuclides[0].channels.len(), 1);
        assert_eq!(cooling.nuclides[0].channels[0].share, 1.0);
        let mut stable =
            DepletionTracker::new(false, true, vec![tracked(0.0, None)], vec![rates], None);
        let idle = stable.step(0, 0.0, 1.0, &|_| 1.0e20);
        assert!(idle.nuclides[0].channels.is_empty());
        assert_eq!(idle.nuclides[0].removal_rate_per_s, 0.0);
        assert_eq!(idle.nuclides[0].depleted_fraction, 0.0);
    }

    #[test]
    fn analytic_reservoir_follows_the_first_order_law_across_steps() {
        let mut tracker = DepletionTracker::new(
            true,
            true,
            vec![tracked(0.0, None)],
            vec![vec![loss(0, 102, 0.5), loss(1, 16, 1.0)]],
            None,
        );
        let first = tracker.step(0, 1.0, 2.0, &|_| {
            panic!("the analytic model reads no state")
        });
        assert_eq!(first.model, "trace_analytic");
        close(first.nuclides[0].atoms_end_per_g, 1.0e20 * (-3.0f64).exp());
        let second = tracker.step(0, 0.5, 1.0, &|_| {
            panic!("the analytic model reads no state")
        });
        close(
            second.nuclides[0].atoms_end_per_g,
            1.0e20 * (-3.0f64).exp() * (-0.75f64).exp(),
        );
        close(
            second.nuclides[0].atoms_start_per_g,
            first.nuclides[0].atoms_end_per_g,
        );
    }

    #[test]
    fn slots_select_their_own_rates() {
        let mut tracker = DepletionTracker::new(
            false,
            true,
            vec![tracked(0.0, None)],
            vec![vec![loss(0, 102, 0.5)], vec![loss(0, 102, 0.125)]],
            None,
        );
        close(
            tracker.step(0, 1.0, 1.0, &|_| 1.0).nuclides[0].removal_rate_per_s,
            0.5,
        );
        close(
            tracker.step(1, 1.0, 1.0, &|_| 1.0).nuclides[0].removal_rate_per_s,
            0.125,
        );
    }

    /// Library of one target with two reactions, each a loss row plus a product row, 1 b in
    /// every group (MT102) and 2 b (MT16); four 1 eV wide groups.
    fn synthetic_library() -> Library {
        let row = |mt: i32, zap: i32| Row {
            target: 0,
            mt,
            zap,
            lfs: 0,
            lmf: if zap == -1 { 0 } else { -1 },
        };
        let mut sig = Vec::new();
        sig.extend([1.0; 4]);
        sig.extend([1.0; 4]);
        sig.extend([2.0; 4]);
        sig.extend([2.0; 4]);
        Library {
            rows: vec![row(102, -1), row(102, 26_057), row(16, -1), row(16, 26_055)],
            sig,
            ngroups: 4,
            bounds: vec![1.0, 2.0, 3.0, 4.0, 5.0],
        }
    }

    fn relative_component(mt: i32, mt1: i32, value: f64) -> (usize, ParsedComponent) {
        (
            0,
            ParsedComponent {
                mat: 2631,
                mt,
                mt1,
                lb: 1,
                kind: ComponentKind::Relative,
                row_grid: vec![1.0, 5.0],
                column_grid: vec![1.0, 5.0],
                values: vec![value],
            },
        )
    }

    fn collapse(library: &Library, components: Vec<(usize, ParsedComponent)>) -> LossCovariance {
        let covariance = CovarianceLibrary::from_parsed(components).expect("valid covariance");
        let phi = [1.25e23; 4];
        let collapsed = covariance
            .collapse_weighted_multi(library, &[&phi[..]], &[0, 1, 2, 3], &|_, _| 1.0)
            .expect("collapse succeeds");
        LossCovariance::from_collapsed(&collapsed, |row| library.rows[row].zap == -1)
    }

    #[test]
    fn channel_uncertainty_is_the_flux_weighted_collapse_of_the_relative_covariance() {
        // A one-bin relative covariance spanning the library: every group is perfectly
        // correlated, so the collapsed rate carries the declared relative sigma exactly.
        let library = synthetic_library();
        let covariance = collapse(
            &library,
            vec![
                relative_component(102, 102, 0.04),
                relative_component(16, 16, 0.09),
            ],
        );
        // rates: MT102 1 b x 5e23 = 0.5, MT16 2 b x 5e23 = 1.0
        let rates = vec![loss(0, 102, 0.5), loss(2, 16, 1.0)];
        let mut tracker = DepletionTracker::new(
            false,
            true,
            vec![tracked(0.25, Some(0.1))],
            vec![rates],
            Some(covariance),
        );
        let step = tracker.step(0, 1.0, 1.0, &|_| 1.0e19);
        let nuclide = &step.nuclides[0];
        let by_name = |name: &str| {
            nuclide
                .channels
                .iter()
                .find(|channel| channel.channel == name)
                .and_then(|channel| channel.relative_standard_uncertainty)
                .expect("channel has an uncertainty")
        };
        assert!((by_name("MT102") - 0.2).abs() < 1e-12);
        assert!((by_name("MT16") - 0.3).abs() < 1e-12);
        assert!((by_name("decay") - 0.1).abs() < 1e-15);
        // No cross-MT block: the channels are independent, so the variances add.
        let variance = (0.5f64 * 0.2).powi(2) + (1.0f64 * 0.3).powi(2) + (0.25f64 * 0.1).powi(2);
        let total = 1.75f64;
        close(nuclide.removal_rate_per_s, total);
        let relative = nuclide
            .removal_rate_relative_standard_uncertainty
            .expect("every channel is covered");
        assert!(
            (relative - variance.sqrt() / total).abs() < 1e-12,
            "{relative}"
        );
        close(nuclide.uncertainty_covered_share.unwrap(), 1.0);
    }

    #[test]
    fn cross_mt_correlation_enters_the_total_but_not_the_channel_uncertainties() {
        // A cross block at correlation 0.5 (relative covariance 0.5 x 0.2 x 0.3 = 0.03) adds
        // 2 x 0.5 x (0.5 x 0.2) x (1.0 x 0.3) to the variance of the total rate.
        let library = synthetic_library();
        let covariance = collapse(
            &library,
            vec![
                relative_component(102, 102, 0.04),
                relative_component(16, 16, 0.09),
                relative_component(16, 102, 0.03),
            ],
        );
        let rates = vec![loss(0, 102, 0.5), loss(2, 16, 1.0)];
        let mut tracker = DepletionTracker::new(
            false,
            true,
            vec![tracked(0.0, None)],
            vec![rates],
            Some(covariance),
        );
        let nuclide = &tracker.step(0, 1.0, 1.0, &|_| 1.0e19).nuclides[0];
        let relative = nuclide
            .removal_rate_relative_standard_uncertainty
            .expect("every channel is covered");
        let variance = 0.01 + 0.09 + 0.03;
        assert!(
            (relative - f64::sqrt(variance) / 1.5).abs() < 1e-12,
            "{relative}"
        );
        // the channel uncertainties themselves are unchanged by the cross block
        let channel = nuclide
            .channels
            .iter()
            .find(|c| c.channel == "MT16")
            .unwrap();
        assert!((channel.relative_standard_uncertainty.unwrap() - 0.3).abs() < 1e-12);
    }

    #[test]
    fn an_uncovered_channel_leaves_its_uncertainty_and_the_total_out() {
        // Only MT102 has covariance; MT16 and a decay constant without an uncertainty do not.
        let library = synthetic_library();
        let covariance = collapse(&library, vec![relative_component(102, 102, 0.04)]);
        let rates = vec![loss(0, 102, 0.5), loss(2, 16, 1.0)];
        let mut tracker = DepletionTracker::new(
            false,
            true,
            vec![tracked(0.5, None)],
            vec![rates],
            Some(covariance),
        );
        let nuclide = &tracker.step(0, 1.0, 1.0, &|_| 1.0e19).nuclides[0];
        let rel = |name: &str| {
            nuclide
                .channels
                .iter()
                .find(|channel| channel.channel == name)
                .unwrap()
                .relative_standard_uncertainty
        };
        assert!((rel("MT102").unwrap() - 0.2).abs() < 1e-12);
        assert!(rel("MT16").is_none());
        assert!(rel("decay").is_none());
        assert!(nuclide.removal_rate_relative_standard_uncertainty.is_none());
        close(nuclide.uncertainty_covered_share.unwrap(), 0.5 / 2.0);
    }

    #[test]
    fn serialized_channel_omits_absent_optional_keys() {
        let mut tracker = DepletionTracker::new(
            false,
            true,
            vec![tracked(0.0, None)],
            vec![vec![loss(0, 102, 0.5)]],
            None,
        );
        let step = tracker.step(0, 1.0, 1.0, &|_| 1.0e19);
        let value = serde_json::to_value(&step).unwrap();
        let nuclide = &value["nuclides"][0];
        assert!(nuclide
            .get("removal_rate_relative_standard_uncertainty")
            .is_none());
        assert!(nuclide.get("uncertainty_covered_share").is_none());
        assert!(nuclide["channels"][0]
            .get("relative_standard_uncertainty")
            .is_none());
        assert_eq!(nuclide["channels"][0]["reaction"], "(n,g)");
        assert_eq!(nuclide["nuclide"], "Fe56");
    }

    // ---- P140 H1 regression tests (hardening pass) ----

    fn relative_close(actual: f64, expected: f64, tolerance: f64) {
        assert!(
            (actual - expected).abs() <= tolerance * expected.abs(),
            "{actual:e} vs {expected:e}"
        );
    }

    #[test]
    fn p140_dep_f4_trace_analytic_fraction_keeps_precision_for_tiny_exponents() {
        // Trace mode is chosen below a burnup of 1e-6, so R dt is tiny. The depleted fraction is
        // -expm1(-R dt) to full precision; `start - start exp(-R dt)` is good to ~1e-16 only.
        let mut tracker = DepletionTracker::new(
            true,
            true,
            vec![tracked(0.0, None)],
            vec![vec![loss(0, 102, 3.0e-12)]],
            None,
        );
        let expected_step = -f64::exp_m1(-3.0e-12);
        let first = tracker.step(0, 1.0, 1.0, &|_| {
            panic!("the analytic model reads no state")
        });
        let first = &first.nuclides[0];
        relative_close(first.depleted_fraction, expected_step, 1e-9);
        relative_close(first.depleted_atoms_per_g, 1.0e20 * expected_step, 1e-9);
        relative_close(first.cumulative_depleted_fraction, expected_step, 1e-9);
        relative_close(
            first.cumulative_depleted_atoms_per_g,
            1.0e20 * expected_step,
            1e-9,
        );
        let second = tracker.step(0, 1.0, 1.0, &|_| {
            panic!("the analytic model reads no state")
        });
        let second = &second.nuclides[0];
        relative_close(second.depleted_fraction, expected_step, 1e-9);
        relative_close(
            second.cumulative_depleted_fraction,
            -f64::exp_m1(-6.0e-12),
            1e-9,
        );
        relative_close(
            second.cumulative_depleted_atoms_per_g,
            1.0e20 * -f64::exp_m1(-6.0e-12),
            1e-9,
        );
    }

    #[test]
    fn p140_dep_f4_trace_analytic_fraction_is_not_zero_below_machine_epsilon() {
        // R dt = 1e-20 is far below f64 epsilon: start - start exp(-R dt) is exactly 0.0, yet the
        // removal rate is positive, so the fraction must be 1e-20, not 0.
        let mut tracker = DepletionTracker::new(
            true,
            true,
            vec![tracked(0.0, None)],
            vec![vec![loss(0, 102, 1.0e-20)]],
            None,
        );
        let step = tracker.step(0, 1.0, 1.0, &|_| {
            panic!("the analytic model reads no state")
        });
        let nuclide = &step.nuclides[0];
        assert!(nuclide.removal_rate_per_s > 0.0);
        relative_close(nuclide.depleted_fraction, 1.0e-20, 1e-9);
        relative_close(nuclide.cumulative_depleted_fraction, 1.0e-20, 1e-9);
        relative_close(nuclide.depleted_atoms_per_g, 1.0e20 * 1.0e-20, 1e-9);
    }

    #[test]
    fn p140_dep_negzero_removal_rate_is_positive_zero() {
        // A stable nuclide with the flux off has no channel; the empty float sum is -0.0, which
        // serialises as "-0.0". The documented value is a plain 0.0.
        let mut tracker = DepletionTracker::new(
            false,
            true,
            vec![tracked(0.0, None)],
            vec![vec![loss(0, 102, 0.5)]],
            None,
        );
        let step = tracker.step(0, 0.0, 1.0, &|_| 1.0e20);
        let nuclide = &step.nuclides[0];
        assert!(nuclide.channels.is_empty());
        assert_eq!(nuclide.removal_rate_per_s, 0.0);
        assert!(
            nuclide.removal_rate_per_s.is_sign_positive(),
            "removal_rate_per_s is negative zero"
        );
        let json = serde_json::to_string(&step).unwrap();
        assert!(!json.contains("-0.0"), "{json}");
    }

    #[test]
    fn p140_dep_f3_excluded_covariance_block_is_not_reported_as_zero_uncertainty() {
        // MT102 carries a negative relative variance: its self block is non-positive-semidefinite,
        // so the collapse zeroes and excludes it. That is "not covered", not "uncertainty 0".
        let library = synthetic_library();
        let covariance = collapse(
            &library,
            vec![
                relative_component(102, 102, -0.04),
                relative_component(16, 16, 0.09),
            ],
        );
        let rates = vec![loss(0, 102, 0.5), loss(2, 16, 1.0)];
        let mut tracker = DepletionTracker::new(
            false,
            true,
            vec![tracked(0.25, Some(0.1))],
            vec![rates],
            Some(covariance),
        );
        let nuclide = &tracker.step(0, 1.0, 1.0, &|_| 1.0e19).nuclides[0];
        let channel = |name: &str| {
            nuclide
                .channels
                .iter()
                .find(|channel| channel.channel == name)
                .unwrap()
        };
        // the healthy block is unaffected
        assert!((channel("MT16").relative_standard_uncertainty.unwrap() - 0.3).abs() < 1e-12);
        assert_eq!(
            channel("MT102").relative_standard_uncertainty,
            None,
            "an excluded block must not read as uncertainty 0"
        );
        // covered: MT16 (1.0) and decay (0.25) of the total 1.75
        close(nuclide.uncertainty_covered_share.unwrap(), 1.25 / 1.75);
        assert!(
            nuclide.removal_rate_relative_standard_uncertainty.is_none(),
            "the joint total needs every channel covered"
        );
    }

    #[test]
    fn p140_dep_f9a_nan_variance_is_not_clamped_to_zero() {
        // `f64::max` returns the non-NaN operand, so a NaN variance used to become Some(0.0).
        let covariance = LossCovariance {
            index: HashMap::from([((0, 0), 0)]),
            sigma_b: vec![1.0],
            covariance_barn2: vec![f64::NAN],
        };
        assert_eq!(covariance.variance(0, &[(0, 0.5)]), None);
        let mut tracker = DepletionTracker::new(
            false,
            true,
            vec![tracked(0.0, None)],
            vec![vec![loss(0, 102, 0.5)]],
            Some(covariance),
        );
        let nuclide = &tracker.step(0, 1.0, 1.0, &|_| 1.0e19).nuclides[0];
        assert_eq!(nuclide.channels[0].relative_standard_uncertainty, None);
        assert!(nuclide.removal_rate_relative_standard_uncertainty.is_none());
    }

    #[test]
    fn p140_dep_f9b_zero_removal_omits_the_covered_share() {
        // A stable nuclide with the flux off: 0 of 0 is undefined, so the key is left out.
        let mut tracker = DepletionTracker::new(
            false,
            true,
            vec![tracked(0.0, None)],
            vec![vec![loss(0, 102, 0.5)]],
            Some(LossCovariance::default()),
        );
        let step = tracker.step(0, 0.0, 1.0, &|_| 1.0e20);
        let nuclide = &step.nuclides[0];
        assert_eq!(nuclide.removal_rate_per_s, 0.0);
        assert_eq!(nuclide.uncertainty_covered_share, None);
        let json = serde_json::to_value(&step).unwrap();
        assert!(json["nuclides"][0]
            .get("uncertainty_covered_share")
            .is_none());
    }
}
