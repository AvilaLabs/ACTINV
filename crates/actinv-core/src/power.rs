#![allow(non_snake_case)] // JSON wire names carry their physical units (heating_W_per_g).
//! Power normalization and kerma heating (P126).
//!
//! A power-driven step fixes the heating power of the irradiated material, `P = phi * sum_i
//! N_i(t) * k_i`, where `phi` is the flux multiplier on the step's spectrum, `N_i` the atoms per
//! gram of nuclide `i` and `k_i` its heating per atom at unit multiplier (the group fold of the
//! kerma table with the step's spectrum). FISPACT-II rescales the flux once per step from the
//! start-of-step composition. Here the step is split into equal sub-intervals and the multiplier
//! is renormalized at the start of each from the composition then reached; the split is refined
//! (doubling, capped at `max_substeps`) until the end-of-interval power of every sub-interval is
//! within `tolerance` of the request. The solver is generic over the heating function and the
//! propagator so the CRAM step machinery stays in `run.rs`.
use crate::quantity::{ParticleFlux, RatePerBarnSecond};
use crate::run::{name_of, EV};
use actinv_data::kerma::KermaTable;
use serde::Serialize;

/// One refinement attempt: the sub-interval count tried and its worst power drift.
#[derive(Debug, Clone, Serialize)]
pub struct PowerRefinement {
    pub substeps: usize,
    pub max_power_drift: f64,
}

/// Result of solving one power-driven step.
#[derive(Debug, Clone)]
pub struct PowerSolve {
    /// State at the end of the step (the accepted attempt).
    pub state: Vec<f64>,
    /// Flux multiplier applied in each sub-interval, in time order.
    pub multipliers: Vec<f64>,
    /// Equal sub-interval length in seconds.
    pub substep_s: f64,
    /// Largest relative departure of a sub-interval's end power from the request.
    pub max_drift: f64,
    /// True when `max_drift` is within the tolerance.
    pub converged: bool,
    /// Every attempt, coarsest first.
    pub refinement: Vec<PowerRefinement>,
    /// Negative atoms clipped by the propagator in the accepted attempt.
    pub zeroed_atoms: f64,
}

impl PowerSolve {
    pub fn substeps(&self) -> usize {
        self.multipliers.len()
    }

    /// Time-weighted mean flux multiplier over the step.
    pub fn mean_multiplier(&self) -> f64 {
        if self.multipliers.is_empty() {
            0.0
        } else {
            self.multipliers.iter().sum::<f64>() / self.multipliers.len() as f64
        }
    }
}

/// Solve one step of duration `dt_s` from `y0` at constant power `target`.
///
/// `heating_per_unit(y)` is the power at unit flux multiplier for state `y` (same unit as
/// `target`). `propagate(y, multiplier, h)` advances `y` by `h` seconds at that constant
/// multiplier and returns the new state with the atoms it clipped. The multiplier of each
/// sub-interval is `target / heating_per_unit(state at its start)`, so power equals the target at
/// every sub-interval start; the drift measured is the end-of-interval power's relative departure.
pub fn solve_power_step<H, P>(
    y0: &[f64],
    dt_s: f64,
    target: f64,
    tolerance: f64,
    max_substeps: usize,
    heating_per_unit: H,
    mut propagate: P,
) -> Result<PowerSolve, String>
where
    H: Fn(&[f64]) -> f64,
    P: FnMut(&[f64], f64, f64) -> Result<(Vec<f64>, f64), String>,
{
    if !target.is_finite() || target <= 0.0 {
        return Err("power target must be finite and positive".into());
    }
    if !dt_s.is_finite() || dt_s < 0.0 {
        return Err("power-driven step duration must be finite and nonnegative".into());
    }
    if max_substeps == 0 {
        return Err("max_substeps must be at least 1".into());
    }
    let mut n = 1usize;
    let mut refinement = Vec::new();
    loop {
        let h = dt_s / n as f64;
        let mut y = y0.to_vec();
        let mut multipliers = Vec::with_capacity(n);
        let mut max_drift = 0.0f64;
        let mut zeroed = 0.0f64;
        for k in 0..n {
            let rate = heating_per_unit(&y);
            if !rate.is_finite() || rate <= 0.0 {
                let refined = if n > 1 {
                    format!(
                        "; the {n}-way refinement of this step began after a coarser attempt of \
                         {} sub-interval(s) had run",
                        n / 2
                    )
                } else {
                    String::new()
                };
                let location = format!("at the start of sub-interval {} of {n}", k + 1);
                return Err(if rate.is_nan() || rate.is_infinite() {
                    format!(
                        "power-driven step: the kerma heating {location} is not finite \
                         (heating per unit flux multiplier {rate}){refined}"
                    )
                } else if rate < 0.0 {
                    format!(
                        "power-driven step: the net kerma heating {location} is negative \
                         (heating per unit flux multiplier {rate}): the table rows of the \
                         populated nuclides sum below zero under this spectrum, so no positive \
                         flux delivers a positive power{refined}"
                    )
                } else {
                    format!(
                        "power-driven step: the material has no kerma heating {location} \
                         (heating per unit flux multiplier {rate}); the kerma table covers none \
                         of the populated nuclides or the spectrum is empty{refined}"
                    )
                });
            }
            let multiplier = target / rate;
            let (next, clipped) = propagate(&y, multiplier, h)?;
            zeroed += clipped;
            let end_rate = heating_per_unit(&next);
            let drift = (multiplier * end_rate - target).abs() / target;
            if !drift.is_finite() {
                return Err("power-driven step: the end-of-interval power is not finite".into());
            }
            max_drift = max_drift.max(drift);
            multipliers.push(multiplier);
            y = next;
        }
        refinement.push(PowerRefinement {
            substeps: n,
            max_power_drift: max_drift,
        });
        let converged = max_drift <= tolerance;
        if converged || n >= max_substeps {
            return Ok(PowerSolve {
                state: y,
                multipliers,
                substep_s: h,
                max_drift,
                converged,
                refinement,
                zeroed_atoms: zeroed,
            });
        }
        n = (2 * n).min(max_substeps);
    }
}

/// Per-step power-normalization record; present only on power-driven steps.
#[derive(Debug, Clone, Serialize)]
pub struct PowerStepOut {
    pub requested_W_per_g: f64,
    /// The request as written, when it was given in W cm^-3.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub requested_W_cm3: Option<f64>,
    pub tolerance: f64,
    pub substeps: usize,
    pub max_substeps: usize,
    pub substep_duration_s: f64,
    /// Largest relative departure of a sub-interval's end power from the request.
    pub max_power_drift: f64,
    pub converged: bool,
    /// Multiplier implied by the start-of-run composition; used only for pruning and the burnup
    /// mode decision before the solve.
    pub estimated_flux_multiplier: f64,
    pub mean_flux_multiplier: f64,
    pub flux_multiplier_per_substep: Vec<f64>,
    /// Achieved total flux per sub-interval (particles cm^-2 s^-1).
    pub flux_per_substep_cm2_s: Vec<f64>,
    pub refinement: Vec<PowerRefinement>,
}

impl PowerStepOut {
    /// `spectrum_total_cm2_s` is the step spectrum's total flux at unit multiplier.
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        solve: &PowerSolve,
        requested_w_per_g: f64,
        requested_w_cm3: Option<f64>,
        tolerance: f64,
        max_substeps: usize,
        estimated_flux_multiplier: f64,
        spectrum_total_cm2_s: f64,
    ) -> Self {
        Self {
            requested_W_per_g: requested_w_per_g,
            requested_W_cm3: requested_w_cm3,
            tolerance,
            substeps: solve.substeps(),
            max_substeps,
            substep_duration_s: solve.substep_s,
            max_power_drift: solve.max_drift,
            converged: solve.converged,
            estimated_flux_multiplier,
            mean_flux_multiplier: solve.mean_multiplier(),
            flux_multiplier_per_substep: solve.multipliers.clone(),
            flux_per_substep_cm2_s: solve
                .multipliers
                .iter()
                .map(|multiplier| multiplier * spectrum_total_cm2_s)
                .collect(),
            refinement: solve.refinement.clone(),
        }
    }
}

/// One nuclide's share of the instantaneous heating.
#[derive(Debug, Clone, Serialize)]
pub struct KermaContributor {
    pub nuclide: String,
    pub atoms_per_g: f64,
    pub heating_W_per_g: f64,
    /// `heating_W_per_g` over the material's total kerma heating.
    pub fraction: f64,
}

/// Per-step instantaneous kerma heating at the end of the step.
#[derive(Debug, Clone, Serialize)]
pub struct KermaStepOut {
    /// Flux multiplier the heating is evaluated at (the last sub-interval's for a power step).
    pub flux_multiplier: f64,
    pub heating_W_per_g: f64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub heating_W_cm3: Option<f64>,
    /// Table MT=444 damage-energy production, when the table carries it.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub damage_energy_eV_per_g_s: Option<f64>,
    /// Nuclides with nonzero heating.
    pub n_contributing_nuclides: usize,
    /// Largest contributors by magnitude, in descending order.
    pub top_contributors: Vec<KermaContributor>,
    /// Heating of the contributors not listed.
    pub other_heating_W_per_g: f64,
    /// Fraction of the step's atoms carried by nuclides with a kerma row; the rest contribute
    /// no heating.
    pub covered_atom_fraction: f64,
}

/// Rank per-nuclide heating into a step record. `contributions` holds
/// `((ZA, LISO), atoms_per_g, heating_W_per_g)` for every covered nuclide.
#[allow(clippy::too_many_arguments)]
pub fn summarize_kerma(
    contributions: &[((i32, i32), f64, f64)],
    covered_atoms_per_g: f64,
    total_atoms_per_g: f64,
    top: usize,
    flux_multiplier: f64,
    density_g_cm3: Option<f64>,
    damage_energy_eV_per_g_s: Option<f64>,
) -> KermaStepOut {
    let mut active: Vec<&((i32, i32), f64, f64)> = contributions
        .iter()
        .filter(|entry| entry.2 != 0.0)
        .collect();
    active.sort_by(|left, right| {
        right
            .2
            .abs()
            .total_cmp(&left.2.abs())
            .then(left.0.cmp(&right.0))
    });
    let total = active.iter().fold(0.0f64, |sum, entry| sum + entry.2);
    let listed = active
        .iter()
        .take(top)
        .fold(0.0f64, |sum, entry| sum + entry.2);
    KermaStepOut {
        flux_multiplier,
        heating_W_per_g: total,
        heating_W_cm3: density_g_cm3.map(|density| total * density),
        damage_energy_eV_per_g_s,
        n_contributing_nuclides: active.len(),
        top_contributors: active
            .iter()
            .take(top)
            .map(|entry| KermaContributor {
                nuclide: name_of(entry.0 .0, entry.0 .1),
                atoms_per_g: entry.1,
                heating_W_per_g: entry.2,
                fraction: if total != 0.0 { entry.2 / total } else { 0.0 },
            })
            .collect(),
        other_heating_W_per_g: total - listed,
        covered_atom_fraction: if total_atoms_per_g > 0.0 {
            covered_atoms_per_g / total_atoms_per_g
        } else {
            1.0
        },
    }
}

/// Group flux converted to the reaction rate of a one-barn cross section (s^-1), the weights the
/// kerma table folds against.
pub fn rate_per_barn_s(flux_ascending: &[f64]) -> Vec<f64> {
    flux_ascending
        .iter()
        .map(|flux| {
            RatePerBarnSecond::from_particle_flux(ParticleFlux::sum_groups(std::slice::from_ref(
                flux,
            )))
            .get()
        })
        .collect()
}

/// Square centimetres per barn, from the quantity layer that holds the crate's one barn factor.
pub(crate) fn square_cm_per_barn() -> f64 {
    RatePerBarnSecond::from_particle_flux(ParticleFlux::sum_groups(&[1.0])).get()
}

/// Heating of one atom at unit flux multiplier in W, and its MT=444 damage energy in eV/s.
/// `None` for the first when the table has no row for the state.
pub fn per_atom_rates(
    table: &KermaTable,
    za: i32,
    liso: i32,
    rate_per_barn_s: &[f64],
) -> Option<(f64, Option<f64>)> {
    let heating_eV_per_s = table.heating_eV_per_s(za, liso, rate_per_barn_s)?;
    Some((
        heating_eV_per_s * EV,
        table.damage_energy_eV_per_s(za, liso, rate_per_barn_s),
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn one_nuclide_table() -> KermaTable {
        KermaTable::from_json(
            &serde_json::json!({
                "format": "actinv-kerma-table-1",
                "source": {"citation": "synthetic", "edition": "test", "url": "local"},
                "projectile": "neutron",
                "group_structure": "test-2g",
                "boundaries_eV": [1.0e-5, 1.0, 1.0e6],
                "units": "kerma_barn_eV_per_group",
                "targets": {"Fe56": [2.0e3, 5.0e5]},
                "damage_energy_targets": {"Fe56": [1.0e3, 4.0e4]},
            })
            .to_string(),
            "neutron",
        )
        .unwrap()
    }

    #[test]
    fn flux_reproduces_the_requested_power_for_a_one_nuclide_table() {
        let table = one_nuclide_table();
        let phi = [3.0e13, 7.0e12];
        let rates = rate_per_barn_s(&phi);
        let (watts_per_atom, damage) = per_atom_rates(&table, 26056, 0, &rates).unwrap();
        // closed form: (K1 phi1 + K2 phi2) * (cm^2 per barn) * 1.602176634e-19 J/eV
        let expected = (2.0e3 * 3.0e13 + 5.0e5 * 7.0e12)
            * crate::power::square_cm_per_barn()
            * 1.602176634e-19;
        assert!((watts_per_atom - expected).abs() < 1e-12 * expected);
        let expected_damage =
            (1.0e3 * 3.0e13 + 4.0e4 * 7.0e12) * crate::power::square_cm_per_barn();
        assert!((damage.unwrap() - expected_damage).abs() < 1e-12 * expected_damage);
        assert!(per_atom_rates(&table, 28058, 0, &rates).is_none());

        // 1 gram of iron-56 is 6.022e23 / 55.9 atoms; any composition shows the same identity
        let atoms_per_g = 1.077e22;
        let heating_per_unit = |y: &[f64]| watts_per_atom * y[0];
        let target = 0.25; // W/g
        let solved = solve_power_step(
            &[atoms_per_g],
            10.0,
            target,
            1e-3,
            8,
            heating_per_unit,
            |y, _, _| Ok((y.to_vec(), 0.0)),
        )
        .unwrap();
        assert_eq!(solved.substeps(), 1);
        let multiplier = solved.multipliers[0];
        let power = multiplier * heating_per_unit(&[atoms_per_g]);
        assert!((power - target).abs() < 1e-12 * target);
        assert!((multiplier - target / (watts_per_atom * atoms_per_g)).abs() < 1e-12 * multiplier);
    }

    #[test]
    fn static_composition_does_not_substep() {
        let solved = solve_power_step(
            &[1.0e20, 0.0],
            1.0e6,
            2.0,
            1e-6,
            64,
            |y| 1.0e-12 * y[0],
            |y, _, _| Ok((y.to_vec(), 0.0)),
        )
        .unwrap();
        assert_eq!(solved.substeps(), 1);
        assert!(solved.converged);
        assert!(solved.max_drift < 1e-14);
        assert_eq!(solved.refinement.len(), 1);
        assert_eq!(solved.state, vec![1.0e20, 0.0]);
    }

    /// One burning nuclide: heating = N, burn rate per unit multiplier b = 1, so at constant
    /// power P the exact inventory is N0 - P t.
    fn burning(dt: f64, tolerance: f64, max_substeps: usize) -> Result<PowerSolve, String> {
        solve_power_step(
            &[1.0],
            dt,
            1.0,
            tolerance,
            max_substeps,
            |y| y[0],
            |y, multiplier, h| Ok((vec![y[0] * (-multiplier * h).exp()], 0.0)),
        )
    }

    #[test]
    fn fast_composition_change_triggers_substepping() {
        // N falls from 1 to 0.5 over the step: one constant-flux interval drifts by tens of percent
        let solved = burning(0.5, 5.0e-2, 64).unwrap();
        assert!(solved.substeps() > 1, "substeps {}", solved.substeps());
        assert!(solved.converged);
        assert!(solved.max_drift <= 5.0e-2);
        assert!(solved.refinement.len() > 1);
        assert_eq!(solved.refinement[0].substeps, 1);
        assert!(solved.refinement[0].max_power_drift > 5.0e-2);
        // constant power P = 1 removes P*t = 0.5 atoms: N(t) = 0.5
        assert!(
            (solved.state[0] - 0.5).abs() < 2.0e-2,
            "{}",
            solved.state[0]
        );
        // the multiplier grows as the inventory burns
        assert!(solved.multipliers.last() > solved.multipliers.first());
        // a slowly changing inventory at the same tolerance stays at one interval
        let slow = burning(1.0e-4, 5.0e-2, 64).unwrap();
        assert_eq!(slow.substeps(), 1);
    }

    #[test]
    fn substep_cap_is_honoured_and_reported_unconverged() {
        let solved = burning(0.5, 1.0e-9, 4).unwrap();
        assert_eq!(solved.substeps(), 4);
        assert!(!solved.converged);
        assert!(solved.max_drift > 1.0e-9);
        assert_eq!(solved.refinement.last().unwrap().substeps, 4);
        // a cap that is not a power of two is reached exactly
        let odd = burning(0.5, 1.0e-9, 5).unwrap();
        assert_eq!(odd.substeps(), 5);
    }

    #[test]
    fn zero_heating_and_bad_inputs_are_named_errors() {
        let dead = solve_power_step(
            &[0.0],
            1.0,
            1.0,
            1e-3,
            4,
            |y| y[0],
            |y, _, _| Ok((y.to_vec(), 0.0)),
        )
        .unwrap_err();
        assert!(dead.contains("no kerma heating"));
        assert!(burning(1.0, 1e-3, 0).is_err());
        assert!(solve_power_step(
            &[1.0],
            1.0,
            0.0,
            1e-3,
            4,
            |y| y[0],
            |y, _, _| Ok((y.to_vec(), 0.0))
        )
        .is_err());
        // zero duration returns the initial state with the multiplier that sets the power
        let instant = solve_power_step(
            &[2.0],
            0.0,
            1.0,
            1e-3,
            4,
            |y| y[0],
            |y, _, _| Ok((y.to_vec(), 0.0)),
        )
        .unwrap();
        assert_eq!(instant.state, vec![2.0]);
        assert_eq!(instant.multipliers, vec![0.5]);
    }

    #[test]
    fn clipped_atoms_are_those_of_the_accepted_attempt() {
        let solved = solve_power_step(
            &[1.0],
            0.5,
            1.0,
            5.0e-2,
            64,
            |y| y[0],
            |y, multiplier, h| Ok((vec![y[0] * (-multiplier * h).exp()], 1.0e-3)),
        )
        .unwrap();
        // every sub-interval of the accepted attempt clips 1e-3; earlier attempts do not add
        assert!((solved.zeroed_atoms - 1.0e-3 * solved.substeps() as f64).abs() < 1e-15);
    }

    #[test]
    fn kerma_summary_ranks_by_magnitude_and_conserves_the_total() {
        let contributions = vec![
            ((26056, 0), 9.0e21, 3.0),
            ((26054, 0), 5.0e20, 1.0),
            ((27059, 0), 1.0e18, -0.5),
            ((26057, 0), 2.0e20, 0.0),
            ((25055, 0), 1.0e17, 0.25),
        ];
        let out = summarize_kerma(
            &contributions,
            9.7e21,
            1.0e22,
            2,
            1.5,
            Some(7.874),
            Some(1.0e9),
        );
        assert_eq!(out.n_contributing_nuclides, 4);
        assert!((out.heating_W_per_g - 3.75).abs() < 1e-15);
        assert!((out.heating_W_cm3.unwrap() - 3.75 * 7.874).abs() < 1e-12);
        assert_eq!(out.damage_energy_eV_per_g_s, Some(1.0e9));
        assert_eq!(out.top_contributors.len(), 2);
        assert_eq!(out.top_contributors[0].nuclide, "Fe56");
        assert_eq!(out.top_contributors[1].nuclide, "Fe54");
        assert!((out.top_contributors[0].fraction - 3.0 / 3.75).abs() < 1e-15);
        let listed: f64 = out.top_contributors.iter().map(|c| c.heating_W_per_g).sum();
        assert!((listed + out.other_heating_W_per_g - out.heating_W_per_g).abs() < 1e-15);
        assert!((out.covered_atom_fraction - 0.97).abs() < 1e-15);
        assert_eq!(out.flux_multiplier, 1.5);
        // no density, no per-cm3 figure
        let bare = summarize_kerma(&[], 0.0, 0.0, 3, 0.0, None, None);
        assert!(bare.heating_W_cm3.is_none());
        assert_eq!(bare.heating_W_per_g, 0.0);
        assert_eq!(bare.covered_atom_fraction, 1.0);
        let json = serde_json::to_value(&bare).unwrap();
        assert!(json.get("heating_W_cm3").is_none());
        assert!(json.get("damage_energy_eV_per_g_s").is_none());
    }

    #[test]
    fn p140_pf_f5_negative_and_nan_heating_have_their_own_messages() {
        let keep = |y: &[f64], _: f64, _: f64| Ok((y.to_vec(), 0.0));
        // a legal negative table row: the table does cover the nuclide
        let negative = solve_power_step(&[1.0], 1.0, 1.0, 1e-3, 4, |y| -y[0], keep).unwrap_err();
        assert!(negative.contains("negative"), "{negative}");
        assert!(!negative.contains("covers none"), "{negative}");
        let not_a_number =
            solve_power_step(&[1.0], 1.0, 1.0, 1e-3, 4, |_| f64::NAN, keep).unwrap_err();
        assert!(!not_a_number.contains("covers none"), "{not_a_number}");
        assert!(
            not_a_number.contains("finite") || not_a_number.contains("NaN"),
            "{not_a_number}"
        );
        // the genuine no-coverage case keeps the coverage wording
        let dead = solve_power_step(&[0.0], 1.0, 1.0, 1e-3, 4, |y| y[0], keep).unwrap_err();
        assert!(dead.contains("no kerma heating"), "{dead}");
    }

    #[test]
    fn p140_pf_f5_heating_that_vanishes_during_refinement_names_the_refinement() {
        // one sub-interval completes (and drifts by 50 %), so the solver refines; the two-way
        // split then reaches zero heating at the start of its second sub-interval
        let error = solve_power_step(
            &[1.0],
            1.0,
            1.0,
            1e-3,
            4,
            |y| y[0],
            |_, _, h| Ok((vec![if h > 0.75 { 0.5 } else { 0.0 }], 0.0)),
        )
        .unwrap_err();
        assert!(error.contains("sub-interval 2 of 2"), "{error}");
        assert!(
            error.contains("refin"),
            "the message must say a coarser attempt had run: {error}"
        );
    }
}
