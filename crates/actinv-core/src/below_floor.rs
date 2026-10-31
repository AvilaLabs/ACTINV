#![allow(non_snake_case)] // field names are the JSON wire format (activity_Bq_per_g, heat_W_per_g)
//! ACTINV-P174: what a step does with the states that fall below its numerical floor
//! (`numerical_floor_atoms_per_g`, alpha0 x max N of the solve that produced the step).
//!
//! `options.below_floor` selects the policy. `report` is the status quo and never reaches this module.
//! `bound` (A) takes the below-floor states out of the activity, heat and dose totals and reports the
//! bound their contribution cannot exceed. `refine` (B) does the same after re-solving the step at CRAM-48
//! when that bound is material. `equilibrium` (C) first reconstructs the short-lived below-floor states
//! from their production, `N = P / rho`, wherever the five conditions of the protocol hold, and falls
//! back to B for the rest. The protocol (protocols/ACTINV-P174_PROTOCOL.md) is the specification.
use crate::sparse::Csc;
use std::collections::{BTreeMap, HashSet};

/// A bound is material when it exceeds this fraction of the step's reported activity total.
pub const REL_TOL: f64 = 1e-6;
/// C reconstructs a state only if its decay constant times the step length is at least this.
pub const RECONSTRUCT_THRESHOLD: f64 = 50.0;
/// C reconstructs a state only if its removal rate is at least this multiple of that of every nearest
/// resolved ancestor.
pub const SEPARATION_RATIO: f64 = 50.0;
/// C reconstructs a state only if the start-of-step population, decayed over the step, is at most this
/// fraction of the reconstructed value.
pub const MEMORY_FRACTION: f64 = 1e-6;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Policy {
    /// Today's behaviour: below-floor states stay in the totals, flagged.
    Report,
    /// A.
    Bound,
    /// B.
    Refine,
    /// C.
    Equilibrium,
}

impl Policy {
    pub fn parse(value: &str) -> Result<Policy, String> {
        match value {
            "report" => Ok(Policy::Report),
            "bound" => Ok(Policy::Bound),
            "refine" => Ok(Policy::Refine),
            "equilibrium" => Ok(Policy::Equilibrium),
            other => Err(format!(
                "unknown options.below_floor '{other}' (report, bound, refine or equilibrium)"
            )),
        }
    }
    pub fn name(self) -> &'static str {
        match self {
            Policy::Report => "report",
            Policy::Bound => "bound",
            Policy::Refine => "refine",
            Policy::Equilibrium => "equilibrium",
        }
    }
}

/// Why relaxed below-floor states were not reconstructed, one count per state, by the first condition
/// it failed in the order cycle, parents, memory, rate separation.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Fallbacks {
    pub cycle: usize,
    pub parent: usize,
    pub memory: usize,
    pub separation: usize,
}

/// C's part of a step's `below_floor_bound` block.
#[derive(Debug, Clone, serde::Serialize)]
pub struct Reconstruction {
    pub n_reconstructed: usize,
    pub reconstructed_states: Vec<String>,
    pub reconstruct_threshold: f64,
    pub n_cycle_fallback: usize,
    pub n_parent_fallback: usize,
    pub n_memory_fallback: usize,
    pub n_separation_fallback: usize,
}

/// The step's `below_floor_bound` block; present only when `options.below_floor` is not `report`.
#[derive(Debug, Clone, serde::Serialize)]
pub struct BelowFloorBound {
    pub policy: &'static str,
    /// CRAM order of the solve that produced the step's state (48 for a refined step).
    pub order: u8,
    /// True only for a CRAM-16 step re-solved at CRAM-48.
    pub refined: bool,
    /// States that left the totals (for C, the bounded-only states).
    pub n_states_excluded: usize,
    pub numerical_floor_atoms_per_g: f64,
    /// `sum lambda_i x floor` over the excluded states.
    pub activity_Bq_per_g: f64,
    pub heat_W_per_g: f64,
    /// Radiological response id -> bound; null without a radiological section.
    pub dose: Option<BTreeMap<String, f64>>,
    #[serde(flatten)]
    pub reconstruction: Option<Reconstruction>,
    /// The activity bound by nuclide, for the screen certificate; not serialized.
    #[serde(skip)]
    pub activity_by_nuclide: BTreeMap<String, f64>,
}

/// One solve of a step: the state over the matrix positions (negatives already zeroed), the floor
/// alpha0 x max N of that solve and the CRAM order that produced it.
pub struct Solve {
    pub state: Vec<f64>,
    pub floor: f64,
    pub zeroed: f64,
    pub order: u8,
}

/// What the policy needs to know about a step; every slice has one entry per matrix position.
pub struct StepProblem<'a> {
    /// The matrix the step was solved with (decay, reactions x flux x multiplier, feed, removal).
    pub matrix: &'a Csc,
    /// The state at the start of the step.
    pub start: &'a [f64],
    pub dt: f64,
    /// True for nuclide states; false for the leakage, unit-source and removal states.
    pub member: &'a [bool],
    /// Decay constant of each state (0 for a stable or non-nuclide state).
    pub lambda: &'a [f64],
    /// Decay heat per decay of each state, in joules.
    pub joules_per_decay: &'a [f64],
    /// Activity the totals carry besides the tracked states (the constant bulk of trace mode).
    pub bulk_activity: f64,
    pub equilibrium: bool,
    /// False where one matrix does not describe the step (power-driven steps).
    pub can_reconstruct: bool,
}

pub struct Treatment {
    /// States below the floor of the solve, before any treatment.
    pub n_below: usize,
    /// States that leave the totals.
    pub excluded: Vec<bool>,
    /// Reconstructed states (position, `N_rec`), parents first.
    pub reconstructed: Vec<(usize, f64)>,
    pub fallbacks: Fallbacks,
    /// The solved state with the reconstructed values in place.
    pub state: Vec<f64>,
}

pub struct Outcome {
    /// The solve whose state the step reports: the first, or the CRAM-48 re-solve.
    pub solve: Solve,
    pub refined: bool,
    pub treatment: Treatment,
    pub activity_bound: f64,
    pub heat_bound: f64,
    /// The activity total the step reports after the treatment.
    pub reported_activity: f64,
}

impl Outcome {
    /// The bound exceeds `REL_TOL` of the reported activity total (a zero total with a positive bound counts).
    pub fn material(&self) -> bool {
        self.activity_bound > REL_TOL * self.reported_activity
    }
}

/// Parents of every state (`parents[d]` = `(j, rate)` for `A_dj > 0`, `j != d`) and the removal rate
/// `-A_dd` of every state.
fn graph(a: &Csc) -> (Vec<Vec<(usize, f64)>>, Vec<f64>) {
    let mut parents: Vec<Vec<(usize, f64)>> = vec![Vec::new(); a.n];
    let mut rho = vec![0.0f64; a.n];
    for (j, span) in a.colptr.windows(2).enumerate() {
        for p in span[0]..span[1] {
            let (i, v) = (a.rowidx[p], a.vals[p].re);
            if i == j {
                rho[j] = -v;
            } else if v > 0.0 {
                parents[i].push((j, v));
            }
        }
    }
    (parents, rho)
}

/// Strongly connected components (Tarjan, iterative) of the graph whose edge j -> i means j feeds i.
/// Returns each state's component and each component's size; a component is numbered after every
/// component it feeds, so descending numbers run parents first.
fn components(parents: &[Vec<(usize, f64)>]) -> (Vec<usize>, Vec<usize>) {
    let n = parents.len();
    let mut children: Vec<Vec<usize>> = vec![Vec::new(); n];
    for (i, list) in parents.iter().enumerate() {
        for &(j, _) in list {
            children[j].push(i);
        }
    }
    let mut index = vec![usize::MAX; n];
    let mut low = vec![0usize; n];
    let mut on_stack = vec![false; n];
    let mut stack: Vec<usize> = Vec::new();
    let mut component = vec![usize::MAX; n];
    let mut sizes: Vec<usize> = Vec::new();
    let mut next = 0usize;
    let mut calls: Vec<(usize, usize)> = Vec::new();
    for root in 0..n {
        if index[root] != usize::MAX {
            continue;
        }
        index[root] = next;
        low[root] = next;
        next += 1;
        stack.push(root);
        on_stack[root] = true;
        calls.push((root, 0));
        while let Some(frame) = calls.last_mut() {
            let v = frame.0;
            if frame.1 < children[v].len() {
                let w = children[v][frame.1];
                frame.1 += 1;
                if index[w] == usize::MAX {
                    index[w] = next;
                    low[w] = next;
                    next += 1;
                    stack.push(w);
                    on_stack[w] = true;
                    calls.push((w, 0));
                } else if on_stack[w] {
                    low[v] = low[v].min(index[w]);
                }
            } else {
                calls.pop();
                if let Some(caller) = calls.last() {
                    let u = caller.0;
                    low[u] = low[u].min(low[v]);
                }
                if low[v] == index[v] {
                    let id = sizes.len();
                    let mut size = 0usize;
                    while let Some(w) = stack.pop() {
                        on_stack[w] = false;
                        component[w] = id;
                        size += 1;
                        if w == v {
                            break;
                        }
                    }
                    sizes.push(size);
                }
            }
        }
    }
    (component, sizes)
}

/// Sorts the below-floor states of `solve` out: those that leave the totals and, for C, those that are
/// reconstructed from their production.
pub fn treat(problem: &StepProblem, solve: &Solve) -> Treatment {
    let n = solve.state.len();
    let below: Vec<bool> = (0..n)
        .map(|k| problem.member[k] && solve.state[k] > 0.0 && solve.state[k] < solve.floor)
        .collect();
    let n_below = below.iter().filter(|flag| **flag).count();
    let mut treatment = Treatment {
        n_below,
        excluded: below.clone(),
        reconstructed: Vec::new(),
        fallbacks: Fallbacks::default(),
        state: solve.state.clone(),
    };
    if !problem.equilibrium || n_below == 0 {
        return treatment;
    }
    let (parents, rho) = graph(problem.matrix);
    let (component, sizes) = components(&parents);
    let mut order: Vec<usize> = (0..n).filter(|k| below[*k]).collect();
    order.sort_by(|a, b| component[*b].cmp(&component[*a]));
    let mut rebuilt = vec![false; n];
    for d in order {
        // 1. relaxed. The only condition that counts no fallback: a state that is not relaxed is no
        // candidate. The conditions after it are tested in the order cycle (3), parents (2), memory (4),
        // separation (5), so that a state of a cycle is counted as one and not as a parent failure.
        if problem.lambda[d] * problem.dt < RECONSTRUCT_THRESHOLD {
            continue;
        }
        if !problem.can_reconstruct {
            treatment.fallbacks.separation += 1;
            continue;
        }
        // 3. not in a cycle (self-loops are not edges here)
        if sizes[component[d]] > 1 {
            treatment.fallbacks.cycle += 1;
            continue;
        }
        // 2. parents known: every parent with a population is resolved or already reconstructed
        let known = parents[d]
            .iter()
            .all(|&(j, _)| solve.state[j] <= 0.0 || !below[j] || rebuilt[j]);
        if !known {
            treatment.fallbacks.parent += 1;
            continue;
        }
        let production: f64 = parents[d]
            .iter()
            .filter(|&&(j, _)| solve.state[j] > 0.0)
            .map(|&(j, rate)| rate * treatment.state[j])
            .sum();
        let removal = rho[d];
        if removal <= 0.0 {
            treatment.fallbacks.separation += 1;
            continue;
        }
        let reconstructed = production / removal;
        // 4. no memory of the initial inventory (at most 0 when nothing feeds the state)
        let remembered = problem.start[d] * (-removal * problem.dt).exp();
        if remembered > MEMORY_FRACTION * reconstructed || reconstructed <= 0.0 {
            // a reconstructed value of exactly zero would leave the inventory and the count
            treatment.fallbacks.memory += 1;
            continue;
        }
        // 5. rate separation from every nearest resolved ancestor, walking up through reconstructed parents
        let mut separated = true;
        let mut seen: HashSet<usize> = HashSet::from([d]);
        let mut walk = vec![d];
        'ancestors: while let Some(k) = walk.pop() {
            for &(j, _) in &parents[k] {
                if solve.state[j] <= 0.0 || !seen.insert(j) {
                    continue;
                }
                if rebuilt[j] {
                    walk.push(j);
                } else if removal < SEPARATION_RATIO * rho[j] {
                    separated = false;
                    break 'ancestors;
                }
            }
        }
        if !separated {
            treatment.fallbacks.separation += 1;
            continue;
        }
        rebuilt[d] = true;
        treatment.state[d] = reconstructed;
        treatment.excluded[d] = false;
        treatment.reconstructed.push((d, reconstructed));
    }
    treatment
}

/// The activity the step reports after the treatment: the bulk plus every kept nuclide state.
fn reported_activity(problem: &StepProblem, treatment: &Treatment) -> f64 {
    let mut total = problem.bulk_activity;
    for (k, value) in treatment.state.iter().enumerate() {
        if problem.member[k] && *value > 0.0 && !treatment.excluded[k] {
            total += problem.lambda[k] * value;
        }
    }
    total
}

/// `sum lambda x floor` (activity) and the matching heat over the excluded states.
fn bounds(problem: &StepProblem, excluded: &[bool], floor: f64) -> (f64, f64) {
    let (mut activity, mut heat) = (0.0f64, 0.0f64);
    for (k, flag) in excluded.iter().enumerate() {
        if *flag {
            let a = problem.lambda[k] * floor;
            activity += a;
            heat += a * problem.joules_per_decay[k];
        }
    }
    (activity, heat)
}

/// Treats a step's first solve and, when `refine` is given and the bound is material, the CRAM-48
/// re-solve it returns. The re-solve is treated with the same rules against its own floor.
pub fn resolve_step(
    problem: &StepProblem,
    first: Solve,
    refine: Option<&dyn Fn() -> Result<Solve, String>>,
) -> Result<Outcome, String> {
    let evaluate = |solve: Solve, refined: bool| -> Outcome {
        let treatment = treat(problem, &solve);
        let (activity_bound, heat_bound) = bounds(problem, &treatment.excluded, solve.floor);
        let reported_activity = reported_activity(problem, &treatment);
        Outcome {
            solve,
            refined,
            treatment,
            activity_bound,
            heat_bound,
            reported_activity,
        }
    };
    let outcome = evaluate(first, false);
    match refine {
        Some(resolve) if outcome.material() => Ok(evaluate(resolve()?, true)),
        _ => Ok(outcome),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use num_complex::Complex64 as C64;
    use std::cell::Cell;

    const J: f64 = 1.0e-13;

    fn matrix(n: usize, edges: &[(usize, usize, f64)]) -> Csc {
        let trip: Vec<(usize, usize, C64)> = edges
            .iter()
            .map(|&(i, j, v)| (i, j, C64::new(v, 0.0)))
            .collect();
        Csc::from_triplets(n, &trip)
    }

    /// P -> D -> S with S stable: states 0, 1, 2.
    fn chain3(lp: f64, ld: f64) -> Csc {
        matrix(3, &[(0, 0, -lp), (1, 0, lp), (1, 1, -ld), (2, 1, ld)])
    }

    fn solve(state: &[f64], floor: f64, order: u8) -> Solve {
        Solve {
            state: state.to_vec(),
            floor,
            zeroed: 0.0,
            order,
        }
    }

    fn problem<'a>(
        a: &'a Csc,
        start: &'a [f64],
        dt: f64,
        lambda: &'a [f64],
        member: &'a [bool],
        joules: &'a [f64],
        equilibrium: bool,
    ) -> StepProblem<'a> {
        StepProblem {
            matrix: a,
            start,
            dt,
            member,
            lambda,
            joules_per_decay: joules,
            bulk_activity: 0.0,
            equilibrium,
            can_reconstruct: true,
        }
    }

    fn close(a: f64, b: f64) -> bool {
        (a - b).abs() <= 1e-12 * a.abs().max(b.abs())
    }

    #[test]
    fn policy_parses_the_four_values_and_refuses_the_rest() {
        for (text, policy) in [
            ("report", Policy::Report),
            ("bound", Policy::Bound),
            ("refine", Policy::Refine),
            ("equilibrium", Policy::Equilibrium),
        ] {
            assert_eq!(Policy::parse(text).unwrap(), policy);
            assert_eq!(policy.name(), text);
        }
        assert!(Policy::parse("clip")
            .unwrap_err()
            .contains("options.below_floor"));
        assert!(Policy::parse("").is_err());
    }

    // P (resolved, lambda 1e-18) feeds D (lambda 1), which feeds the stable S. After 1e3 s the true D is
    // lambda_P N_P / lambda_D = 100 atoms/g, far under the floor 1e4 set by P.
    const START: [f64; 3] = [1.0e20, 0.0, 0.0];

    #[test]
    fn a_drops_the_below_floor_state_from_the_totals_and_bounds_it() {
        let a = chain3(1.0e-18, 1.0);
        let (lambda, member, joules) = ([1.0e-18, 1.0, 0.0], [true; 3], [J; 3]);
        let p = problem(&a, &START, 1.0e3, &lambda, &member, &joules, false);
        let outcome = resolve_step(&p, solve(&[1.0e20, 37.0, 1.0e5], 1.0e4, 16), None).unwrap();
        assert_eq!(outcome.treatment.n_below, 1);
        assert_eq!(outcome.treatment.excluded, vec![false, true, false]);
        assert!(outcome.treatment.reconstructed.is_empty());
        // the state itself is untouched: it stays in the inventory and evolves on
        assert_eq!(outcome.treatment.state, vec![1.0e20, 37.0, 1.0e5]);
        assert!(close(outcome.activity_bound, 1.0e4));
        assert!(close(outcome.heat_bound, 1.0e4 * J));
        assert!(close(outcome.reported_activity, 100.0));
        assert!(outcome.material());
        assert!(!outcome.refined);
    }

    #[test]
    fn b_refines_when_the_bound_is_material_and_applies_the_new_floor() {
        let a = chain3(1.0e-18, 1.0);
        let (lambda, member, joules) = ([1.0e-18, 1.0, 0.0], [true; 3], [J; 3]);
        let p = problem(&a, &START, 1.0e3, &lambda, &member, &joules, false);
        let calls = Cell::new(0);
        let refine = || -> Result<Solve, String> {
            calls.set(calls.get() + 1);
            Ok(solve(&[1.0e20, 100.0, 1.0e5], 50.0, 48))
        };
        let outcome =
            resolve_step(&p, solve(&[1.0e20, 37.0, 1.0e5], 1.0e4, 16), Some(&refine)).unwrap();
        assert_eq!(calls.get(), 1);
        assert!(outcome.refined);
        assert_eq!(outcome.solve.order, 48);
        assert_eq!(outcome.solve.floor, 50.0);
        assert_eq!(outcome.treatment.n_below, 0);
        assert_eq!(outcome.activity_bound, 0.0);
        assert!(close(outcome.reported_activity, 200.0));
        assert!(!outcome.material());
    }

    #[test]
    fn b_leaves_a_bound_that_stays_material_after_the_refinement() {
        let a = chain3(1.0e-18, 1.0);
        let (lambda, member, joules) = ([1.0e-18, 1.0, 0.0], [true; 3], [J; 3]);
        let p = problem(&a, &START, 1.0e3, &lambda, &member, &joules, false);
        let refine = || -> Result<Solve, String> { Ok(solve(&[1.0e20, 100.0, 1.0e5], 5.0e3, 48)) };
        let outcome =
            resolve_step(&p, solve(&[1.0e20, 37.0, 1.0e5], 1.0e4, 16), Some(&refine)).unwrap();
        assert!(outcome.refined);
        assert!(close(outcome.activity_bound, 5.0e3));
        assert!(outcome.material(), "the ledger names this step");
    }

    #[test]
    fn b_does_not_refine_a_bound_within_the_tolerance() {
        // total activity 1e17 Bq/g against a bound of 10
        let a = chain3(1.0e-3, 1.0e-3);
        let (lambda, member, joules) = ([1.0e-3, 1.0e-3, 0.0], [true; 3], [J; 3]);
        let p = problem(&a, &START, 1.0e5, &lambda, &member, &joules, false);
        let refine = || -> Result<Solve, String> { panic!("a non-material bound must not refine") };
        let outcome =
            resolve_step(&p, solve(&[1.0e20, 37.0, 1.0e19], 1.0e4, 16), Some(&refine)).unwrap();
        assert!(!outcome.refined);
        assert!(!outcome.material());
    }

    #[test]
    fn c_reconstructs_the_short_lived_state_from_its_production() {
        let a = chain3(1.0e-18, 1.0);
        let (lambda, member, joules) = ([1.0e-18, 1.0, 0.0], [true; 3], [J; 3]);
        let p = problem(&a, &START, 1.0e3, &lambda, &member, &joules, true);
        let refine =
            || -> Result<Solve, String> { panic!("C reconstructs and pays no refinement") };
        let outcome =
            resolve_step(&p, solve(&[1.0e20, 37.0, 1.0e5], 1.0e4, 16), Some(&refine)).unwrap();
        assert_eq!(outcome.treatment.n_below, 1);
        assert_eq!(outcome.treatment.reconstructed.len(), 1);
        let (position, value) = outcome.treatment.reconstructed[0];
        assert_eq!(position, 1);
        assert!(close(value, 100.0), "{value}");
        assert!(close(outcome.treatment.state[1], 100.0));
        assert_eq!(outcome.treatment.excluded, vec![false; 3]);
        assert_eq!(outcome.treatment.fallbacks, Fallbacks::default());
        assert_eq!(outcome.activity_bound, 0.0);
        // the reconstructed state is in the total, like a resolved one
        assert!(close(outcome.reported_activity, 200.0));
        assert!(!outcome.refined);
    }

    #[test]
    fn c_with_equal_half_lives_falls_back_on_separation_and_then_refines() {
        // lambda_D = lambda_P: quasi-steady state is invalid however large lambda x step is
        let a = chain3(1.0e-3, 1.0e-3);
        let (lambda, member, joules) = ([1.0e-3, 1.0e-3, 0.0], [true; 3], [J; 3]);
        let start = [5.0e4, 0.0, 0.0];
        let p = problem(&a, &start, 1.0e5, &lambda, &member, &joules, true);
        let first = solve(&[5.0e4, 37.0, 2.0e4], 1.0e4, 16);
        let alone = treat(&p, &first);
        assert_eq!(alone.fallbacks.separation, 1);
        assert!(alone.reconstructed.is_empty());
        assert_eq!(alone.excluded, vec![false, true, false]);

        let calls = Cell::new(0);
        let refine = || -> Result<Solve, String> {
            calls.set(calls.get() + 1);
            Ok(solve(&[5.0e4, 3.0e4, 2.0e4], 1.0e2, 48))
        };
        let outcome = resolve_step(&p, first, Some(&refine)).unwrap();
        assert_eq!(
            calls.get(),
            1,
            "C does what B does where the conditions fail"
        );
        assert!(outcome.refined);
        assert_eq!(outcome.treatment.n_below, 0);
        assert_eq!(outcome.treatment.fallbacks, Fallbacks::default());
    }

    #[test]
    fn c_refuses_states_in_a_cycle() {
        // R feeds X; X and Y feed each other
        let a = matrix(
            3,
            &[
                (0, 0, -1.0e-18),
                (1, 0, 1.0e-18),
                (1, 1, -1.0),
                (2, 1, 0.9),
                (2, 2, -1.0),
                (1, 2, 0.1),
            ],
        );
        let (lambda, member, joules) = ([1.0e-18, 1.0, 1.0], [true; 3], [J; 3]);
        let start = [1.0e20, 0.0, 0.0];
        let p = problem(&a, &start, 1.0e3, &lambda, &member, &joules, true);
        let outcome = resolve_step(&p, solve(&[1.0e20, 5.0, 7.0], 1.0e4, 16), None).unwrap();
        assert_eq!(outcome.treatment.fallbacks.cycle, 2);
        assert_eq!(outcome.treatment.fallbacks.parent, 0);
        assert!(outcome.treatment.reconstructed.is_empty());
        assert_eq!(outcome.treatment.excluded, vec![false, true, true]);
    }

    #[test]
    fn c_refuses_a_state_that_remembers_its_initial_inventory() {
        // lambda x step = 60, so exp(-60) x 1e30 = 8.8e3 against 1e-6 x 100
        let a = chain3(1.0e-18, 1.0);
        let (lambda, member, joules) = ([1.0e-18, 1.0, 0.0], [true; 3], [J; 3]);
        let remembered = [1.0e20, 1.0e30, 0.0];
        let p = problem(&a, &remembered, 60.0, &lambda, &member, &joules, true);
        let outcome = resolve_step(&p, solve(&[1.0e20, 37.0, 1.0e5], 1.0e4, 16), None).unwrap();
        assert_eq!(outcome.treatment.fallbacks.memory, 1);
        assert!(outcome.treatment.reconstructed.is_empty());
        assert_eq!(outcome.treatment.excluded, vec![false, true, false]);
        // the same step without the initial inventory reconstructs
        let p = problem(&a, &START, 60.0, &lambda, &member, &joules, true);
        let outcome = resolve_step(&p, solve(&[1.0e20, 37.0, 1.0e5], 1.0e4, 16), None).unwrap();
        assert_eq!(outcome.treatment.fallbacks, Fallbacks::default());
        assert_eq!(outcome.treatment.reconstructed.len(), 1);
    }

    #[test]
    fn c_does_not_reconstruct_a_state_nothing_feeds_to_zero() {
        // no parent has a population: production is 0, and N = 0 would leave the inventory
        let a = chain3(1.0e-18, 1.0);
        let (lambda, member, joules) = ([1.0e-18, 1.0, 0.0], [true; 3], [J; 3]);
        let start = [0.0, 0.0, 0.0];
        let p = problem(&a, &start, 1.0e3, &lambda, &member, &joules, true);
        let outcome = resolve_step(&p, solve(&[0.0, 37.0, 1.0e5], 1.0e4, 16), None).unwrap();
        assert_eq!(outcome.treatment.fallbacks.memory, 1);
        assert!(outcome.treatment.reconstructed.is_empty());
    }

    // R -> D1 -> D2 -> S; D1 is the fast one, D2 slower than D1 but far faster than R.
    fn chain4(lr: f64) -> Csc {
        matrix(
            4,
            &[
                (0, 0, -lr),
                (1, 0, lr),
                (1, 1, -1.0),
                (2, 1, 1.0),
                (2, 2, -0.01),
                (3, 2, 0.01),
            ],
        )
    }

    #[test]
    fn c_measures_rate_separation_against_the_resolved_ancestor() {
        let a = chain4(1.0e-18);
        let (lambda, member, joules) = ([1.0e-18, 1.0, 0.01, 0.0], [true; 4], [J; 4]);
        let start = [1.0e20, 0.0, 0.0, 0.0];
        let p = problem(&a, &start, 1.0e4, &lambda, &member, &joules, true);
        // D1 = 100, D2 = 1e4 under the floor 1e5
        let first = solve(&[1.0e20, 3.0, 4.0, 1.0e6], 1.0e5, 16);
        let outcome = resolve_step(&p, first, None).unwrap();
        assert_eq!(outcome.treatment.n_below, 2);
        assert_eq!(outcome.treatment.fallbacks, Fallbacks::default());
        let positions: Vec<usize> = outcome
            .treatment
            .reconstructed
            .iter()
            .map(|r| r.0)
            .collect();
        assert_eq!(positions, vec![1, 2], "parents first");
        assert!(close(outcome.treatment.state[1], 100.0));
        assert!(close(outcome.treatment.state[2], 1.0e4));
        // D2 against its reconstructed parent D1 alone would fail (0.01 < 50 x 1); against R it passes

        // a resolved ancestor only 10x slower than D2 rejects D2 and keeps D1
        let a = chain4(1.0e-3);
        let lambda = [1.0e-3, 1.0, 0.01, 0.0];
        let p = problem(&a, &start, 1.0e4, &lambda, &member, &joules, true);
        let first = solve(&[1.0e20, 3.0, 4.0, 1.0e6], 1.0e5, 16);
        let outcome = resolve_step(&p, first, None).unwrap();
        assert_eq!(outcome.treatment.fallbacks.separation, 1);
        let positions: Vec<usize> = outcome
            .treatment
            .reconstructed
            .iter()
            .map(|r| r.0)
            .collect();
        assert_eq!(positions, vec![1]);
        assert_eq!(outcome.treatment.excluded, vec![false, false, true, false]);
    }

    #[test]
    fn c_counts_a_parent_fallback_below_a_state_that_failed() {
        // D1 remembers its initial inventory, so D2 has a parent that is neither resolved nor reconstructed
        let a = matrix(
            4,
            &[
                (0, 0, -1.0e-18),
                (1, 0, 1.0e-18),
                (1, 1, -1.0),
                (2, 1, 1.0),
                (2, 2, -1.0),
                (3, 2, 1.0),
            ],
        );
        let (lambda, member, joules) = ([1.0e-18, 1.0, 1.0, 0.0], [true; 4], [J; 4]);
        let start = [1.0e20, 1.0e30, 0.0, 0.0];
        let p = problem(&a, &start, 60.0, &lambda, &member, &joules, true);
        let first = solve(&[1.0e20, 3.0, 4.0, 1.0e6], 1.0e5, 16);
        let outcome = resolve_step(&p, first, None).unwrap();
        assert_eq!(outcome.treatment.fallbacks.memory, 1);
        assert_eq!(outcome.treatment.fallbacks.parent, 1);
        assert!(outcome.treatment.reconstructed.is_empty());
        assert_eq!(outcome.treatment.excluded, vec![false, true, true, false]);
    }

    #[test]
    fn sinks_and_the_unit_source_are_never_members_but_the_unit_state_is_a_parent() {
        // position 0 is the unit source (population 1, no loss), 1 the product it feeds, 2 the leakage sink
        let a = matrix(3, &[(1, 0, 100.0), (1, 1, -1.0), (2, 1, 1.0)]);
        let (lambda, member, joules) = ([0.0, 1.0, 0.0], [false, true, false], [J; 3]);
        let start = [1.0, 0.0, 0.0];
        let p = problem(&a, &start, 1.0e3, &lambda, &member, &joules, true);
        // floor 1e3 (set by the bulk of other states): the product, 100, is below it
        let outcome = resolve_step(&p, solve(&[1.0, 12.0, 5.0], 1.0e3, 16), None).unwrap();
        assert_eq!(
            outcome.treatment.n_below, 1,
            "the unit and leakage states are not counted"
        );
        assert_eq!(outcome.treatment.reconstructed.len(), 1);
        assert!(close(outcome.treatment.state[1], 100.0));
        assert_eq!(outcome.treatment.state[0], 1.0);
        assert_eq!(outcome.treatment.state[2], 5.0);
    }

    #[test]
    fn a_power_driven_step_reconstructs_nothing_and_counts_the_fallback() {
        let a = chain3(1.0e-18, 1.0);
        let (lambda, member, joules) = ([1.0e-18, 1.0, 0.0], [true; 3], [J; 3]);
        let mut p = problem(&a, &START, 1.0e3, &lambda, &member, &joules, true);
        p.can_reconstruct = false;
        let outcome = resolve_step(&p, solve(&[1.0e20, 37.0, 1.0e5], 1.0e4, 16), None).unwrap();
        assert!(outcome.treatment.reconstructed.is_empty());
        assert_eq!(outcome.treatment.fallbacks.separation, 1);
        assert_eq!(outcome.treatment.excluded, vec![false, true, false]);
    }

    #[test]
    fn the_block_serializes_only_the_policys_fields() {
        let mut block = BelowFloorBound {
            policy: "bound",
            order: 16,
            refined: false,
            n_states_excluded: 1,
            numerical_floor_atoms_per_g: 1.0e4,
            activity_Bq_per_g: 2.0,
            heat_W_per_g: 3.0,
            dose: None,
            reconstruction: None,
            activity_by_nuclide: BTreeMap::from([("Mn56".to_string(), 2.0)]),
        };
        let value = serde_json::to_value(&block).unwrap();
        let mut keys: Vec<&str> = value
            .as_object()
            .unwrap()
            .keys()
            .map(String::as_str)
            .collect();
        keys.sort_unstable();
        assert_eq!(
            keys,
            vec![
                "activity_Bq_per_g",
                "dose",
                "heat_W_per_g",
                "n_states_excluded",
                "numerical_floor_atoms_per_g",
                "order",
                "policy",
                "refined"
            ]
        );
        assert!(value["dose"].is_null());

        block.policy = "equilibrium";
        block.dose = Some(BTreeMap::from([("contact".to_string(), 0.5)]));
        block.reconstruction = Some(Reconstruction {
            n_reconstructed: 1,
            reconstructed_states: vec!["Mn56".into()],
            reconstruct_threshold: RECONSTRUCT_THRESHOLD,
            n_cycle_fallback: 0,
            n_parent_fallback: 0,
            n_memory_fallback: 0,
            n_separation_fallback: 0,
        });
        let value = serde_json::to_value(&block).unwrap();
        assert_eq!(value["n_reconstructed"], serde_json::json!(1));
        assert_eq!(value["reconstructed_states"], serde_json::json!(["Mn56"]));
        assert_eq!(value["reconstruct_threshold"], serde_json::json!(50.0));
        assert_eq!(value["n_separation_fallback"], serde_json::json!(0));
        assert_eq!(value["dose"]["contact"], serde_json::json!(0.5));
        assert!(value.get("activity_by_nuclide").is_none());
    }
}
