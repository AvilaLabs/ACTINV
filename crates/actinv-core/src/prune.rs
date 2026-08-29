//! Reachable-set and rate-significance pruning, with the bound on what rate pruning removed.
use crate::quantity::AtomsPerGram;
use crate::spec::PhysicalStep;
use std::collections::VecDeque;

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

/// Returns (kept indices, dropped (index, atom bound, feed bound)).
pub fn reachable(
    n: usize,
    decay: &[(usize, usize, f64)],
    react: &[(usize, usize, f64)],
    n0: &[f64],
    sched: &[(f64, f64)],
    rate_mode: bool,
    bmin: f64,
) -> (Vec<usize>, Vec<(usize, f64, f64)>) {
    reachable_values(n, decay, react, n0, sched, rate_mode, bmin)
}

pub(crate) fn reachable_physical(
    n: usize,
    decay: &[(usize, usize, f64)],
    react: &[(usize, usize, f64)],
    n0: &[f64],
    sched: &[PhysicalStep],
    rate_mode: bool,
    bmin: AtomsPerGram,
) -> (Vec<usize>, Vec<(usize, f64, f64)>) {
    reachable_values(n, decay, react, n0, sched, rate_mode, bmin.get())
}

fn reachable_values<S: ScheduleValue>(
    n: usize,
    decay: &[(usize, usize, f64)],
    react: &[(usize, usize, f64)],
    n0: &[f64],
    sched: &[S],
    rate_mode: bool,
    bmin: f64,
) -> (Vec<usize>, Vec<(usize, f64, f64)>) {
    let t_total: f64 = sched.iter().map(ScheduleValue::duration_seconds).sum();
    let phi_max = sched
        .iter()
        .map(ScheduleValue::flux_multiplier)
        .fold(0.0, f64::max);
    let mut lam = vec![0.0f64; n];
    for (i, j, v) in decay {
        if i == j {
            lam[*j] = -v;
        }
    }
    let mut succ: Vec<Vec<(usize, f64, bool)>> = vec![Vec::new(); n];
    for (i, j, v) in decay {
        if i != j && *v > 0.0 {
            succ[*j].push((*i, *v, true));
        }
    }
    if phi_max > 0.0 {
        for (i, j, v) in react {
            if i != j && *v > 0.0 {
                succ[*j].push((*i, v * phi_max, false));
            }
        }
    }
    let (mut b, mut f) = (vec![0.0f64; n], vec![0.0f64; n]);
    let mut src = vec![false; n];
    let mut q = VecDeque::new();
    for (i, v) in n0.iter().enumerate() {
        if *v > 0.0 {
            b[i] = *v;
            f[i] = f64::INFINITY;
            src[i] = true;
            q.push_back(i);
        }
    }
    while let Some(j) = q.pop_front() {
        for (i, r, is_decay) in succ[j].clone() {
            let (cb, cf) = if src[j] {
                (r * t_total, r)
            } else if is_decay {
                let br = if lam[j] > 0.0 { r / lam[j] } else { 1.0 };
                ((r * t_total).min(1.0), f[j].min(lam[j] * b[j]) * br)
            } else {
                ((r * t_total).min(1.0), b[j] * r)
            };
            let (nb, nf) = (b[j] * cb, cf);
            let mut changed = false;
            if nb > b[i] {
                b[i] = nb;
                changed = true;
            }
            if nf > f[i] {
                f[i] = nf;
                changed = true;
            }
            if changed && !src[i] {
                q.push_back(i);
            }
        }
    }
    let (mut keep, mut dropped) = (Vec::new(), Vec::new());
    for i in 0..n {
        if !rate_mode {
            if b[i] > 0.0 || src[i] {
                keep.push(i);
            }
        } else if b[i] >= bmin || src[i] {
            keep.push(i);
        } else if b[i] > 0.0 {
            dropped.push((i, b[i], f[i]));
        }
    }
    (keep, dropped)
}
