//! Reachable-set and rate-significance pruning, with proven bounds on what rate pruning removed (P148).
//!
//! For every state the rate pass bounds two quantities of the unpruned problem over the whole schedule:
//! the throughput `Θ_i` (initial atoms plus every atom that enters) and the population `S_i = sup_t N_i(t)`.
//! Atoms moving along `j → i` number at most `a_ji T S_j` (largest rate times duration times largest
//! population) and at most `g_ji Θ_j` (the largest share of `j`'s losses the edge can take, times all
//! atoms that ever leave `j`). So `Θ_i ≤ N0_i + Σ_j min(a_ji T S_j, g_ji Θ_j)`; with the inflow-rate bound
//! `F_i = Σ_j a_ji S_j` and `dN_i/dt ≤ F_i − λ_i N_i`, also `S_i ≤ N0_i + F_i/λ_i`. Cyclic components start
//! from a Collatz–Wielandt bound and are tightened by repeating the monotone map; every iterate stays a bound.
use crate::quantity::AtomsPerGram;
use crate::spec::PhysicalStep;
use std::cell::RefCell;
use std::collections::{HashMap, VecDeque};

/// Largest number of sweeps that tighten one cyclic component.
const MAX_SWEEPS: usize = 1000;
/// A sweep that lowers no bound by more than this relative amount ends the tightening.
const SWEEP_TOLERANCE: f64 = 1e-12;
/// Largest number of Neumann steps searched for a cyclic component's contraction certificate.
const CERTIFICATE_ITERATIONS: usize = 1000;

trait ScheduleValue {
    fn duration_seconds(&self) -> f64;
    fn flux_multiplier(&self) -> f64;
}

impl ScheduleValue for (f64, f64) {
    fn duration_seconds(&self) -> f64 {
        self.0
    }

    fn flux_multiplier(&self) -> f64 {
        self.1
    }
}

impl ScheduleValue for PhysicalStep {
    fn duration_seconds(&self) -> f64 {
        self.duration().get()
    }

    fn flux_multiplier(&self) -> f64 {
        self.multiplier().get()
    }
}

/// The assembled problem pruning operates on. Triplets are `(row, column, value)`: `column` is the parent.
pub struct PruneProblem<'a> {
    pub n: usize,
    /// Decay triplets, diagonal `-λ` included.
    pub decay: &'a [(usize, usize, f64)],
    /// One list of feed triplets per schedule step.
    pub feed: &'a [Vec<(usize, usize, f64)>],
    /// One list of removal triplets per schedule step (off-diagonal into the removed sink plus its diagonal).
    pub removal: &'a [Vec<(usize, usize, f64)>],
    /// One list of reaction triplets per spectrum, at unit flux multiplier, diagonal included.
    pub spectra: &'a [Vec<(usize, usize, f64)>],
    pub n0: &'a [f64],
}

/// A state removed by rate pruning, with its population and inflow-rate bounds.
#[derive(Clone, Debug, PartialEq)]
pub struct Dropped {
    pub index: usize,
    pub atoms_bound: f64,
    pub feed_bound: f64,
}

/// Every state's bounds on the unpruned problem; infinite where no bound was established.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct StateBounds {
    pub throughput: Vec<f64>,
    pub population: Vec<f64>,
    pub inflow_rate: Vec<f64>,
    /// States of cyclic components with no contraction, and every state they reach.
    pub unbounded: Vec<usize>,
}

#[derive(Clone, Debug, Default, PartialEq)]
pub struct PruneOutcome {
    /// Kept state indices, ascending.
    pub keep: Vec<usize>,
    pub dropped: Vec<Dropped>,
    /// `(kept state, bound on how far its pruned population falls below the unpruned one)` for every kept
    /// state with a non-zero bound.
    pub kept_shortfall: Vec<(usize, f64)>,
    /// States kept only because no bound was established for them.
    pub unbounded: Vec<usize>,
}

/// One edge of the bound graph: largest rate `a` (1/s) and largest loss share `g` (infinite if unbounded).
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct BoundEdge {
    pub from: usize,
    pub to: usize,
    pub rate: f64,
    pub share: f64,
}

/// What `capture` records for the P148 probe: the inputs and every derived quantity.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Capture {
    pub n: usize,
    pub decay: Vec<(usize, usize, f64)>,
    pub feed: Vec<Vec<(usize, usize, f64)>>,
    pub removal: Vec<Vec<(usize, usize, f64)>>,
    pub spectra: Vec<Vec<(usize, usize, f64)>>,
    pub n0: Vec<f64>,
    pub durations_s: Vec<f64>,
    pub multipliers: Vec<f64>,
    pub rate_mode: bool,
    pub bmin: f64,
    pub edges: Vec<BoundEdge>,
    pub bounds: StateBounds,
    pub outcome: PruneOutcome,
    /// Mean energy released per decay of each state (eV; zero for states without decay data), recorded by
    /// the caller after pruning.
    pub energy_ev: Vec<f64>,
    /// Name of each state (empty for synthetic problems), recorded by the caller.
    pub names: Vec<String>,
}

thread_local! {
    static CAPTURE: RefCell<Option<Vec<Capture>>> = const { RefCell::new(None) };
}

/// Diagnostic capture for the P148 probe: while active, every pruning call on this thread is recorded.
pub mod capture {
    use super::{Capture, CAPTURE};

    pub fn begin() {
        CAPTURE.with(|cell| *cell.borrow_mut() = Some(Vec::new()));
    }

    /// Ends the capture and returns what was recorded, in call order.
    pub fn take() -> Vec<Capture> {
        CAPTURE.with(|cell| cell.borrow_mut().take().unwrap_or_default())
    }

    pub(crate) fn active() -> bool {
        CAPTURE.with(|cell| cell.borrow().is_some())
    }

    /// Attaches per-state decay energies (eV) and names to the most recent record.
    pub(crate) fn record_states(energy_ev: Vec<f64>, names: Vec<String>) {
        CAPTURE.with(|cell| {
            if let Some(last) = cell
                .borrow_mut()
                .as_mut()
                .and_then(|records| records.last_mut())
            {
                last.energy_ev = energy_ev;
                last.names = names;
            }
        });
    }

    pub(super) fn push(record: Capture) {
        CAPTURE.with(|cell| {
            if let Some(records) = cell.borrow_mut().as_mut() {
                records.push(record);
            }
        });
    }
}

/// Legacy entry: `decay` carries every non-reaction triplet (its diagonal is the loss), `react` one spectrum.
/// Returns (kept indices, dropped (index, population bound, inflow-rate bound)).
pub fn reachable(
    n: usize,
    decay: &[(usize, usize, f64)],
    react: &[(usize, usize, f64)],
    n0: &[f64],
    sched: &[(f64, f64)],
    rate_mode: bool,
    bmin: f64,
) -> (Vec<usize>, Vec<(usize, f64, f64)>) {
    let spectra = [react.to_vec()];
    let problem = PruneProblem {
        n,
        decay,
        feed: &[],
        removal: &[],
        spectra: &spectra,
        n0,
    };
    let outcome = prune_values(&problem, sched, rate_mode, bmin);
    let dropped = outcome
        .dropped
        .iter()
        .map(|d| (d.index, d.atoms_bound, d.feed_bound))
        .collect();
    (outcome.keep, dropped)
}

/// Full outcome on a `(duration s, flux multiplier)` schedule; the P148 probe's entry for synthetic problems.
pub fn prune_schedule(
    problem: &PruneProblem<'_>,
    sched: &[(f64, f64)],
    rate_mode: bool,
    bmin: f64,
) -> PruneOutcome {
    prune_values(problem, sched, rate_mode, bmin)
}

pub(crate) fn reachable_physical(
    problem: &PruneProblem<'_>,
    sched: &[PhysicalStep],
    rate_mode: bool,
    bmin: AtomsPerGram,
) -> PruneOutcome {
    prune_values(problem, sched, rate_mode, bmin.get())
}

fn prune_values<S: ScheduleValue>(
    problem: &PruneProblem<'_>,
    sched: &[S],
    rate_mode: bool,
    bmin: f64,
) -> PruneOutcome {
    let n = problem.n;
    let t_total: f64 = sched.iter().map(ScheduleValue::duration_seconds).sum();
    let phi_max = sched
        .iter()
        .map(ScheduleValue::flux_multiplier)
        .fold(0.0, f64::max);
    let decay_loss = decay_loss(n, problem.decay);
    let edges = bound_edges(problem, &decay_loss, phi_max);
    let is_source: Vec<bool> = (0..n)
        .map(|i| problem.n0.get(i).is_some_and(|v| *v > 0.0))
        .collect();

    let (outcome, bounds) = if rate_mode {
        let all = vec![true; n];
        let zero = vec![0.0; n];
        let n0: Vec<f64> = (0..n)
            .map(|i| problem.n0.get(i).copied().unwrap_or(0.0).max(0.0))
            .collect();
        let bounds = propagate(n, &edges, &all, &n0, &zero, &zero, &decay_loss, t_total);
        let mut keep = Vec::new();
        let mut dropped = Vec::new();
        let mut kept_mask = vec![false; n];
        for i in 0..n {
            if is_source[i] || bounds.population[i] >= bmin {
                keep.push(i);
                kept_mask[i] = true;
            } else if bounds.throughput[i] > 0.0 {
                dropped.push(Dropped {
                    index: i,
                    atoms_bound: bounds.population[i],
                    feed_bound: bounds.inflow_rate[i],
                });
            }
        }
        let kept_shortfall = if dropped.is_empty() {
            Vec::new()
        } else {
            shortfall(n, &edges, &kept_mask, &bounds, &decay_loss, t_total)
        };
        let unbounded = bounds
            .unbounded
            .iter()
            .copied()
            .filter(|i| kept_mask[*i])
            .collect();
        (
            PruneOutcome {
                keep,
                dropped,
                kept_shortfall,
                unbounded,
            },
            bounds,
        )
    } else {
        let keep = reachable_set(n, &edges, &is_source);
        (
            PruneOutcome {
                keep,
                ..PruneOutcome::default()
            },
            StateBounds::default(),
        )
    };
    if capture::active() {
        capture::push(Capture {
            n,
            decay: problem.decay.to_vec(),
            feed: problem.feed.to_vec(),
            removal: problem.removal.to_vec(),
            spectra: problem.spectra.to_vec(),
            n0: problem.n0.to_vec(),
            durations_s: sched.iter().map(ScheduleValue::duration_seconds).collect(),
            multipliers: sched.iter().map(ScheduleValue::flux_multiplier).collect(),
            rate_mode,
            bmin,
            edges: edges.clone(),
            bounds,
            outcome: outcome.clone(),
            energy_ev: Vec::new(),
            names: Vec::new(),
        });
    }
    outcome
}

/// Decay constant of every state, from the decay diagonal.
fn decay_loss(n: usize, decay: &[(usize, usize, f64)]) -> Vec<f64> {
    let mut lam = vec![0.0f64; n];
    for &(i, j, v) in decay {
        if i == j && v < 0.0 {
            lam[j] -= v;
        }
    }
    lam
}

/// Merges duplicate triplets per edge and attaches each edge's largest rate and loss share.
fn bound_edges(problem: &PruneProblem<'_>, decay_loss: &[f64], phi_max: f64) -> Vec<BoundEdge> {
    let mut edges = Vec::new();
    let off_diagonal_sums = |triplets: &[(usize, usize, f64)]| -> Vec<((usize, usize), f64)> {
        let mut sums: HashMap<(usize, usize), f64> = HashMap::new();
        for &(i, j, v) in triplets {
            if i != j && v > 0.0 {
                *sums.entry((j, i)).or_default() += v;
            }
        }
        let mut sums: Vec<_> = sums.into_iter().collect();
        sums.sort_by_key(|(key, _)| *key);
        sums
    };
    // Decay: the share is the branch fraction, the largest at any flux or removal rate.
    for ((j, i), rate) in off_diagonal_sums(problem.decay) {
        let share = if decay_loss[j] > 0.0 {
            rate / decay_loss[j]
        } else {
            f64::INFINITY
        };
        edges.push(BoundEdge {
            from: j,
            to: i,
            rate,
            share,
        });
    }
    // Feed and removal: largest per-step rate. A feed source has no loss term, so its share is unbounded;
    // an atom is removed at most once, so a removal edge takes at most all of its parent's losses.
    for (steps, share) in [(problem.feed, f64::INFINITY), (problem.removal, 1.0)] {
        let mut largest: HashMap<(usize, usize), f64> = HashMap::new();
        for step in steps {
            for ((j, i), rate) in off_diagonal_sums(step) {
                let entry = largest.entry((j, i)).or_default();
                *entry = entry.max(rate);
            }
        }
        let mut largest: Vec<_> = largest.into_iter().collect();
        largest.sort_by_key(|(key, _)| *key);
        for ((j, i), rate) in largest {
            edges.push(BoundEdge {
                from: j,
                to: i,
                rate,
                share,
            });
        }
    }
    // Reactions: per spectrum, rate at the largest multiplier and the share at that multiplier (the share
    // σφ/(λ + Σφ) rises with φ); then the largest over spectra.
    if phi_max > 0.0 {
        let mut largest: HashMap<(usize, usize), (f64, f64)> = HashMap::new();
        for spectrum in problem.spectra {
            let mut removal = vec![0.0f64; problem.n];
            for &(i, j, v) in spectrum {
                if i == j && v < 0.0 {
                    removal[j] -= v;
                }
            }
            for ((j, i), sigma) in off_diagonal_sums(spectrum) {
                let rate = sigma * phi_max;
                let loss = decay_loss[j] + removal[j] * phi_max;
                let share = if loss > 0.0 {
                    rate / loss
                } else {
                    f64::INFINITY
                };
                let entry = largest.entry((j, i)).or_insert((0.0, 0.0));
                entry.0 = entry.0.max(rate);
                entry.1 = entry.1.max(share);
            }
        }
        let mut largest: Vec<_> = largest.into_iter().collect();
        largest.sort_by_key(|(key, _)| *key);
        for ((j, i), (rate, share)) in largest {
            edges.push(BoundEdge {
                from: j,
                to: i,
                rate,
                share,
            });
        }
    }
    edges.retain(|e| e.rate > 0.0);
    edges
}

/// States reachable from a source along edges of positive rate (the exact `reach` set).
fn reachable_set(n: usize, edges: &[BoundEdge], is_source: &[bool]) -> Vec<usize> {
    let mut succ: Vec<Vec<usize>> = vec![Vec::new(); n];
    for e in edges {
        succ[e.from].push(e.to);
    }
    let mut seen = is_source.to_vec();
    let mut queue: VecDeque<usize> = (0..n).filter(|i| is_source[*i]).collect();
    while let Some(j) = queue.pop_front() {
        for &i in &succ[j] {
            if !seen[i] {
                seen[i] = true;
                queue.push_back(i);
            }
        }
    }
    (0..n).filter(|i| seen[*i]).collect()
}

/// Atoms moving along one edge over the schedule, bounded by rate × duration × population and by
/// share × throughput.
fn transfer(edge: &BoundEdge, t_total: f64, population: f64, throughput: f64) -> f64 {
    if throughput <= 0.0 || population <= 0.0 {
        return 0.0;
    }
    let by_rate = edge.rate * t_total * population;
    if edge.share.is_infinite() {
        by_rate
    } else {
        by_rate.min(edge.share * throughput)
    }
}

/// `S_i ≤ min(Θ_i, N0_i + F_i/λ_i)`.
fn population_cap(throughput: f64, n0: f64, inflow_rate: f64, lam: f64) -> f64 {
    if lam > 0.0 {
        throughput.min(n0 + inflow_rate / lam)
    } else {
        throughput
    }
}

/// Bounds for the states in `active`, over edges between active states, with initial atoms `n0` and an
/// extra inflow (`extra_atoms` over the schedule, at most `extra_rate` per second) into each state.
#[allow(clippy::too_many_arguments)]
fn propagate(
    n: usize,
    edges: &[BoundEdge],
    active: &[bool],
    n0: &[f64],
    extra_atoms: &[f64],
    extra_rate: &[f64],
    lam: &[f64],
    t_total: f64,
) -> StateBounds {
    let mut pred: Vec<Vec<usize>> = vec![Vec::new(); n];
    let mut succ: Vec<Vec<usize>> = vec![Vec::new(); n];
    for (k, e) in edges.iter().enumerate() {
        if active[e.from] && active[e.to] {
            pred[e.to].push(k);
            succ[e.from].push(e.to);
        }
    }
    let components = strongly_connected(n, &succ, active);
    let mut theta = vec![0.0f64; n];
    let mut pop = vec![0.0f64; n];
    let mut rate = vec![0.0f64; n];
    let mut component_of = vec![usize::MAX; n];
    for (c, members) in components.iter().enumerate() {
        for &i in members {
            component_of[i] = c;
        }
    }
    let mut unbounded = Vec::new();
    // Tarjan emits components in reverse topological order.
    for (c, members) in components.iter().enumerate().rev() {
        // Inflow from outside the component: final values, already computed.
        let mut b = vec![0.0f64; members.len()];
        let mut fext = vec![0.0f64; members.len()];
        for (m, &i) in members.iter().enumerate() {
            b[m] = n0[i] + extra_atoms[i];
            fext[m] = extra_rate[i];
            for &k in &pred[i] {
                let e = &edges[k];
                if component_of[e.from] != c {
                    b[m] += transfer(e, t_total, pop[e.from], theta[e.from]);
                    fext[m] += e.rate * pop[e.from];
                }
            }
        }
        if members.len() == 1 {
            let i = members[0];
            theta[i] = b[0];
            rate[i] = fext[0];
            pop[i] = population_cap(theta[i], n0[i], rate[i], lam[i]);
            if !theta[i].is_finite() {
                unbounded.push(i);
            }
            continue;
        }
        let local: HashMap<usize, usize> =
            members.iter().enumerate().map(|(m, &i)| (i, m)).collect();
        let internal: Vec<Vec<(usize, BoundEdge)>> = members
            .iter()
            .map(|&i| {
                pred[i]
                    .iter()
                    .map(|&k| edges[k])
                    .filter(|e| component_of[e.from] == c)
                    .map(|e| (local[&e.from], e))
                    .collect()
            })
            .collect();
        match cyclic_start(&b, &fext, &internal, t_total) {
            None => {
                for &i in members {
                    theta[i] = f64::INFINITY;
                    pop[i] = f64::INFINITY;
                    rate[i] = f64::INFINITY;
                    unbounded.push(i);
                }
            }
            Some(start) => {
                let mut th = start;
                let mut po = th.clone();
                for _ in 0..MAX_SWEEPS {
                    let mut new_th = th.clone();
                    let mut new_po = po.clone();
                    let mut largest_drop = 0.0f64;
                    for (m, &i) in members.iter().enumerate() {
                        let mut t = b[m];
                        let mut f = fext[m];
                        for (from, e) in &internal[m] {
                            t += transfer(e, t_total, po[*from], th[*from]);
                            f += e.rate * po[*from];
                        }
                        let t = t.min(th[m]);
                        let p = population_cap(t, n0[i], f, lam[i]).min(po[m]);
                        for (old, new) in [(th[m], t), (po[m], p)] {
                            if old > 0.0 {
                                largest_drop = largest_drop.max((old - new) / old);
                            }
                        }
                        new_th[m] = t;
                        new_po[m] = p;
                    }
                    th = new_th;
                    po = new_po;
                    if largest_drop <= SWEEP_TOLERANCE {
                        break;
                    }
                }
                for (m, &i) in members.iter().enumerate() {
                    theta[i] = th[m];
                    pop[i] = po[m];
                    let mut f = fext[m];
                    for (from, e) in &internal[m] {
                        f += e.rate * po[*from];
                    }
                    rate[i] = f;
                }
            }
        }
    }
    // Everything an unbounded state reaches is unbounded too (the propagation already made it infinite).
    unbounded.sort_unstable();
    unbounded.dedup();
    StateBounds {
        throughput: theta,
        population: pop,
        inflow_rate: rate,
        unbounded,
    }
}

/// Starting bound for a cyclic component from a vector `v > 0` with `C v ≤ q v`, `q < 1`, where
/// `C[m][from] = min(a T, g)` is the coefficient of `from`'s throughput in m's (`Θ ≤ b + C Θ`). With
/// `t = max_m Θ_m / v_m`: `Θ_m ≤ b_m + t q v_m`, so `t ≤ max_m(b_m / v_m) / (1 − q)` and
/// `Θ_m ≤ v_m max(b / v) / (1 − q)`. `v` is the Neumann iterate `v_{k+1} = 1 + C v_k`, `v_0 = 1`, for which
/// `C v_k = v_{k+1} − 1`; the first `k` with `q_k = max_m (v_{k+1,m} − 1) / v_{k,m} < 1` is used. `None` if
/// none is found within the iteration limit (no contraction, `ρ(C) ≥ 1` or too close to it).
fn cyclic_start(
    b: &[f64],
    fext: &[f64],
    internal: &[Vec<(usize, BoundEdge)>],
    t_total: f64,
) -> Option<Vec<f64>> {
    let size = b.len();
    if b.iter().chain(fext).any(|v| !v.is_finite()) {
        return None;
    }
    let coefficient = |e: &BoundEdge| -> f64 {
        let by_rate = e.rate * t_total;
        if e.share.is_infinite() {
            by_rate
        } else {
            by_rate.min(e.share)
        }
    };
    let mut v = vec![1.0f64; size];
    for _ in 0..CERTIFICATE_ITERATIONS {
        let next: Vec<f64> = internal
            .iter()
            .map(|row| {
                1.0 + row
                    .iter()
                    .map(|(from, e)| coefficient(e) * v[*from])
                    .sum::<f64>()
            })
            .collect();
        if next.iter().any(|x| !x.is_finite()) {
            return None;
        }
        let q = next
            .iter()
            .zip(&v)
            .map(|(next, v)| (next - 1.0) / v)
            .fold(0.0f64, f64::max);
        if q < 1.0 {
            let scale = b.iter().zip(&v).map(|(b, v)| b / v).fold(0.0f64, f64::max) / (1.0 - q);
            return Some(v.iter().map(|v| v * scale).collect());
        }
        v = next;
    }
    None
}

/// Bound on how far each kept state's pruned population falls below the unpruned one: the error obeys
/// the same inequalities with no initial atoms and the inflow the cut edges carried.
fn shortfall(
    n: usize,
    edges: &[BoundEdge],
    kept: &[bool],
    bounds: &StateBounds,
    lam: &[f64],
    t_total: f64,
) -> Vec<(usize, f64)> {
    let mut extra_atoms = vec![0.0f64; n];
    let mut extra_rate = vec![0.0f64; n];
    for e in edges {
        if !kept[e.from] && kept[e.to] && bounds.throughput[e.from] > 0.0 {
            extra_atoms[e.to] += transfer(
                e,
                t_total,
                bounds.population[e.from],
                bounds.throughput[e.from],
            );
            extra_rate[e.to] += e.rate * bounds.population[e.from];
        }
    }
    let zero = vec![0.0f64; n];
    let error = propagate(
        n,
        edges,
        kept,
        &zero,
        &extra_atoms,
        &extra_rate,
        lam,
        t_total,
    );
    (0..n)
        .filter(|i| kept[*i] && error.population[*i] > 0.0)
        .map(|i| (i, error.population[i]))
        .collect()
}

/// Tarjan's strongly connected components over the active states (iterative), in reverse topological order.
fn strongly_connected(n: usize, succ: &[Vec<usize>], active: &[bool]) -> Vec<Vec<usize>> {
    let mut index = vec![usize::MAX; n];
    let mut low = vec![0usize; n];
    let mut on_stack = vec![false; n];
    let mut stack = Vec::new();
    let mut components = Vec::new();
    let mut next = 0usize;
    for root in 0..n {
        if !active[root] || index[root] != usize::MAX {
            continue;
        }
        let mut call: Vec<(usize, usize)> = vec![(root, 0)];
        index[root] = next;
        low[root] = next;
        next += 1;
        stack.push(root);
        on_stack[root] = true;
        while let Some(&mut (v, ref mut child)) = call.last_mut() {
            if *child < succ[v].len() {
                let w = succ[v][*child];
                *child += 1;
                if index[w] == usize::MAX {
                    index[w] = next;
                    low[w] = next;
                    next += 1;
                    stack.push(w);
                    on_stack[w] = true;
                    call.push((w, 0));
                } else if on_stack[w] {
                    low[v] = low[v].min(index[w]);
                }
            } else {
                call.pop();
                if let Some(&(parent, _)) = call.last() {
                    low[parent] = low[parent].min(low[v]);
                }
                if low[v] == index[v] {
                    let mut component = Vec::new();
                    while let Some(w) = stack.pop() {
                        on_stack[w] = false;
                        component.push(w);
                        if w == v {
                            break;
                        }
                    }
                    component.sort_unstable();
                    components.push(component);
                }
            }
        }
    }
    components
}

#[cfg(test)]
mod tests {
    use super::*;

    fn rate_prune(
        n: usize,
        decay: &[(usize, usize, f64)],
        react: &[(usize, usize, f64)],
        n0: &[f64],
        sched: &[(f64, f64)],
        bmin: f64,
    ) -> PruneOutcome {
        let spectra = [react.to_vec()];
        let problem = PruneProblem {
            n,
            decay,
            feed: &[],
            removal: &[],
            spectra: &spectra,
            n0,
        };
        prune_values(&problem, sched, true, bmin)
    }

    /// P148 R4: two parents each feed below the threshold, together above it. The v1.0.0 maximum dropped
    /// the daughter; the sum keeps it.
    #[test]
    fn converging_paths_sum_instead_of_taking_the_largest() {
        // 0 is the source; 1 and 2 are made from it by two reactions; both decay fully into 3.
        let (sigma, lam) = (1e-10, 1e-3);
        let react = [(1, 0, sigma), (2, 0, sigma), (0, 0, -2.0 * sigma)];
        let decay = [(1, 1, -lam), (3, 1, lam), (2, 2, -lam), (3, 2, lam)];
        let n0 = [1e8, 0.0, 0.0, 0.0];
        let sched = [(1e4, 1.0)];
        // Each parent passes at most σ T N0 = 1e2 atoms; the daughter receives up to 2e2.
        let outcome = rate_prune(4, &decay, &react, &n0, &sched, 1.5e2);
        assert!(outcome.keep.contains(&3), "{outcome:?}");
        // The v1.0.0 rule (maximum over parents) gave 1e2 and dropped it.
        let true_daughter = 2.0 * (100.0 - sigma * 1e8 / lam * (1.0 - (-lam * 1e4_f64).exp()));
        assert!(true_daughter > 1.5e2);
    }

    #[test]
    fn population_bound_dominates_a_closed_form_chain() {
        // Source 0 -> 1 by reaction σ; 1 decays with λ. Exact N1(t) = σN0/λ (1 - e^{-λt}) during irradiation.
        let (sigma, lam, n0v, t) = (1e-9, 1e-2, 1e10, 1e3);
        let react = [(1, 0, sigma), (0, 0, -sigma)];
        let decay = [(1, 1, -lam), (2, 1, lam)];
        let outcome = rate_prune(3, &decay, &react, &[n0v, 0.0, 0.0], &[(t, 1.0)], 1e30);
        let exact = sigma * n0v / lam * (1.0 - (-lam * t).exp());
        let dropped = outcome
            .dropped
            .iter()
            .find(|d| d.index == 1)
            .expect("state 1 dropped at 1e30");
        assert!(
            dropped.atoms_bound >= exact,
            "{} < {exact}",
            dropped.atoms_bound
        );
        // The equilibrium cap keeps it within a factor of a few of the exact value.
        assert!(dropped.atoms_bound <= 1.5 * sigma * n0v / lam);
    }

    #[test]
    fn isomer_cycle_contracts_and_bounds_hold() {
        // 0 -> 1 (isomer) by reaction; 1 -> 2 by IT with branch 1; 2 -> 1 by (n,n'); 2 stable otherwise.
        let react = [(1, 0, 1e-12), (0, 0, -1e-12), (1, 2, 1e-13), (2, 2, -1e-13)];
        let decay = [(1, 1, -1e-4), (2, 1, 1e-4)];
        let outcome = rate_prune(3, &decay, &react, &[1e20, 0.0, 0.0], &[(1e6, 1.0)], 1e30);
        assert!(outcome.unbounded.is_empty());
        assert_eq!(outcome.dropped.len(), 2);
        assert!(outcome
            .dropped
            .iter()
            .all(|d| d.atoms_bound.is_finite() && d.atoms_bound > 0.0));
    }

    #[test]
    fn a_component_without_contraction_is_kept_whole() {
        // A unit-share cycle with long exposure: 1 <-> 2 by decay with branch 1 both ways; 3 downstream.
        let decay = [
            (1, 1, -1.0),
            (2, 1, 1.0),
            (2, 2, -1.0),
            (1, 2, 1.0),
            (3, 2, 1e-30),
        ];
        let react = [(1, 0, 1e-20), (0, 0, -1e-20)];
        let outcome = rate_prune(
            4,
            &decay,
            &react,
            &[1.0, 0.0, 0.0, 0.0],
            &[(1e3, 1.0)],
            1e10,
        );
        assert_eq!(outcome.keep, vec![0, 1, 2, 3]);
        assert_eq!(outcome.unbounded, vec![1, 2, 3]);
    }

    #[test]
    fn reach_mode_is_exact_reachability() {
        let react = [(1, 0, 1e-30), (0, 0, -1e-30)];
        let decay = [(2, 1, 1.0), (1, 1, -1.0)];
        let spectra = [react.to_vec()];
        let problem = PruneProblem {
            n: 4,
            decay: &decay,
            feed: &[],
            removal: &[],
            spectra: &spectra,
            n0: &[1.0, 0.0, 0.0, 0.0],
        };
        let outcome = prune_values(&problem, &[(1.0, 1.0)], false, 1.0);
        assert_eq!(outcome.keep, vec![0, 1, 2]);
        assert!(outcome.dropped.is_empty());
    }

    #[test]
    fn kept_shortfall_covers_the_cut_inflow() {
        // 0 -> 1 strongly (kept); 0 -> 2 weakly (dropped); 2 -> 1 by decay: 1 loses that inflow.
        let react = [(1, 0, 1e-6), (2, 0, 1e-20), (0, 0, -(1e-6 + 1e-20))];
        let decay = [(2, 2, -1.0), (1, 2, 1.0), (1, 1, -1e-6)];
        let outcome = rate_prune(3, &decay, &react, &[1e10, 0.0, 0.0], &[(1e3, 1.0)], 1e-3);
        assert_eq!(outcome.keep, vec![0, 1]);
        let shortfall = outcome
            .kept_shortfall
            .iter()
            .find(|(i, _)| *i == 1)
            .unwrap()
            .1;
        // All atoms that could pass 2 -> 1: at most σ T N0 = 1e-20 * 1e3 * 1e10.
        assert!((shortfall - 1e-7).abs() <= 1e-7 * 1e-12, "{shortfall}");
    }

    #[test]
    fn capture_records_inputs_and_outcome() {
        capture::begin();
        let react = [(1, 0, 1e-10), (0, 0, -1e-10)];
        let decay = [(1, 1, -1.0)];
        let _ = rate_prune(2, &decay, &react, &[1.0, 0.0], &[(1.0, 1.0)], 1.0);
        let records = capture::take();
        assert_eq!(records.len(), 1);
        assert_eq!(records[0].outcome.keep, vec![0]);
        assert!(capture::take().is_empty());
    }
}
