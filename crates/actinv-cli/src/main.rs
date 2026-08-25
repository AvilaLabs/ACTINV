//! actinv-solve PROBLEM OUT — schedule solver with reachable-set pruning (P2-G2).
//! Problem file (text):  ACTINV-PROBLEM 1 / n N / decay NNZ (i j v)* / reaction NNZ (i j v)* [per unit flux]
//!   / n0 K (idx val)* / cram ALPHA0 K (theta_re theta_im alpha_re alpha_im)* / schedule S (dt phi)* / prune 0|1
//! Output: ACTINV-RESULT 1 / header line / per step: "step s t T k K" then K lines "idx val".
use std::collections::VecDeque;
use std::io::{BufRead, BufReader, Write};
use num_complex::Complex64 as C64;
use actinv_core::sparse::Csc;
use actinv_core::cram::{Cram, step};

struct Problem { n: usize, decay: Vec<(usize, usize, f64)>, react: Vec<(usize, usize, f64)>, n0: Vec<(usize, f64)>, cram: Cram, sched: Vec<(f64, f64)>, prune: u8, bmin: f64 }

fn parse(path: &str) -> Problem {
    let f = BufReader::new(std::fs::File::open(path).expect("open problem"));
    let mut it = f.lines().map(|l| l.unwrap()).filter(|l| !l.trim().is_empty() && !l.starts_with('#'));
    let hdr = it.next().unwrap(); assert!(hdr.starts_with("ACTINV-PROBLEM"), "bad header");
    let n: usize = it.next().unwrap().split_whitespace().nth(1).unwrap().parse().unwrap();
    let read_trip = |it: &mut dyn Iterator<Item = String>, tag: &str| -> Vec<(usize, usize, f64)> {
        let l = it.next().unwrap(); let p: Vec<&str> = l.split_whitespace().collect(); assert_eq!(p[0], tag);
        let k: usize = p[1].parse().unwrap(); let mut v = Vec::with_capacity(k);
        for _ in 0..k { let l = it.next().unwrap(); let q: Vec<&str> = l.split_whitespace().collect(); v.push((q[0].parse().unwrap(), q[1].parse().unwrap(), q[2].parse().unwrap())); }
        v
    };
    let decay = read_trip(&mut it, "decay"); let react = read_trip(&mut it, "reaction");
    let l = it.next().unwrap(); let p: Vec<&str> = l.split_whitespace().collect(); assert_eq!(p[0], "n0"); let k: usize = p[1].parse().unwrap();
    let mut n0 = Vec::new(); for _ in 0..k { let l = it.next().unwrap(); let q: Vec<&str> = l.split_whitespace().collect(); n0.push((q[0].parse().unwrap(), q[1].parse().unwrap())); }
    let l = it.next().unwrap(); let p: Vec<&str> = l.split_whitespace().collect(); assert_eq!(p[0], "cram"); let alpha0: f64 = p[1].parse().unwrap(); let k: usize = p[2].parse().unwrap();
    let mut theta = Vec::new(); let mut alpha = Vec::new();
    for _ in 0..k { let l = it.next().unwrap(); let q: Vec<f64> = l.split_whitespace().map(|s| s.parse().unwrap()).collect(); theta.push(C64::new(q[0], q[1])); alpha.push(C64::new(q[2], q[3])); }
    let l = it.next().unwrap(); let p: Vec<&str> = l.split_whitespace().collect(); assert_eq!(p[0], "schedule"); let s: usize = p[1].parse().unwrap();
    let mut sched = Vec::new(); for _ in 0..s { let l = it.next().unwrap(); let q: Vec<f64> = l.split_whitespace().map(|x| x.parse().unwrap()).collect(); sched.push((q[0], q[1])); }
    let pl = it.next().unwrap_or_default(); let pt: Vec<&str> = pl.split_whitespace().collect();
    let prune: u8 = pt.get(1).and_then(|x| x.parse().ok()).unwrap_or(0); let bmin: f64 = pt.get(2).and_then(|x| x.parse().ok()).unwrap_or(1e-6);
    Problem { n, decay, react, n0, cram: Cram { alpha0, theta, alpha }, sched, prune, bmin }
}

/// Reachable set from support(n0) over the union pattern (reaction edges only if some step has phi > 0).
fn reachable(p: &Problem) -> Vec<usize> {
    let use_react = p.sched.iter().any(|&(_, phi)| phi > 0.0);
    let mut succ: Vec<Vec<usize>> = vec![Vec::new(); p.n];
    for &(i, j, v) in p.decay.iter() { if i != j && v != 0.0 { succ[j].push(i); } }
    if use_react { for &(i, j, v) in p.react.iter() { if i != j && v != 0.0 { succ[j].push(i); } } }
    let mut seen = vec![false; p.n]; let mut q = VecDeque::new(); let mut out = Vec::new();
    for &(i, v) in p.n0.iter() { if v != 0.0 && !seen[i] { seen[i] = true; q.push_back(i); } }
    while let Some(j) = q.pop_front() { out.push(j); for &i in succ[j].iter() { if !seen[i] { seen[i] = true; q.push_back(i); } } }
    out.sort(); out
}

/// Rate-significance pruning (P3-G3): bound B(i) = max_j B(j)·c_ji with c = r·T from the unit/source nodes (support of n0,
/// whose "atoms" are their n0 values) and min(1, r·T) elsewhere; nodes with B < bmin (atoms) are dropped. Returns (kept, dropped_count, dropped_bound_sum).
fn reachable_rate(p: &Problem, bmin: f64) -> (Vec<usize>, usize, f64, Vec<(usize, f64, f64)>) {
    // B(i): bound on atoms that could ever reach i; F(i): bound on the feed rate (atoms/s) into i — flux through a chain
    // cannot grow, so F(i) = max_j [decay: min(F_j, lambda_j B_j)·BR ; reaction: B_j·r·phi ; source: v].
    let t_total: f64 = p.sched.iter().map(|&(dt, _)| dt).sum();
    let phi_max = p.sched.iter().map(|&(_, phi)| phi).fold(0.0, f64::max);
    let mut lam = vec![0.0f64; p.n];
    for &(i, j, v) in p.decay.iter() { if i == j { lam[j] = -v; } }
    let mut succ: Vec<Vec<(usize, f64, bool)>> = vec![Vec::new(); p.n];   // (target, rate, is_decay)
    for &(i, j, v) in p.decay.iter() { if i != j && v > 0.0 { succ[j].push((i, v, true)); } }
    if phi_max > 0.0 { for &(i, j, v) in p.react.iter() { if i != j && v > 0.0 { succ[j].push((i, v * phi_max, false)); } } }
    let mut b = vec![0.0f64; p.n]; let mut f = vec![0.0f64; p.n]; let mut src = vec![false; p.n]; let mut q = VecDeque::new();
    for &(i, v) in p.n0.iter() { if v > 0.0 { b[i] = v; f[i] = f64::INFINITY; src[i] = true; q.push_back(i); } }
    while let Some(j) = q.pop_front() {
        for &(i, r, is_decay) in succ[j].iter() {
            let (cb, cf) = if src[j] { (r * t_total, r) } else if is_decay { let br = if lam[j] > 0.0 { r / lam[j] } else { 1.0 }; ((r * t_total).min(1.0), f[j].min(lam[j] * b[j]) * br) } else { ((r * t_total).min(1.0), b[j] * r) };
            let nb = b[j] * cb; let nf = cf; let mut changed = false;
            if nb > b[i] { b[i] = nb; changed = true; }
            if nf > f[i] { f[i] = nf; changed = true; }
            if changed && !src[i] { q.push_back(i); }
        }
    }
    let mut keep = Vec::new(); let mut dropped = 0usize; let mut dsum = 0.0; let mut dlist = Vec::new();
    for i in 0..p.n { if b[i] >= bmin || src[i] { keep.push(i); } else if b[i] > 0.0 { dropped += 1; dsum += b[i]; dlist.push((i, b[i], f[i])); } }
    (keep, dropped, dsum, dlist)
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.len() < 3 { eprintln!("usage: actinv-solve PROBLEM OUT"); std::process::exit(2); }
    let p = parse(&args[1]);
    let (keep, dropped, dsum, dlist): (Vec<usize>, usize, f64, Vec<(usize, f64, f64)>) = match p.prune { 2 => reachable_rate(&p, p.bmin), 1 => (reachable(&p), 0, 0.0, Vec::new()), _ => ((0..p.n).collect(), 0, 0.0, Vec::new()) };
    let m = keep.len();
    let mut pos = vec![usize::MAX; p.n]; for (k, &g) in keep.iter().enumerate() { pos[g] = k; }
    let sub = |t: &Vec<(usize, usize, f64)>| -> Vec<(usize, usize, C64)> { t.iter().filter(|&&(i, j, _)| pos[i] != usize::MAX && pos[j] != usize::MAX).map(|&(i, j, v)| (pos[i], pos[j], C64::new(v, 0.0))).collect() };
    let dsub = sub(&p.decay); let rsub = sub(&p.react);
    let mut y = vec![0.0f64; m]; for &(i, v) in p.n0.iter() { if pos[i] != usize::MAX { y[pos[i]] = v; } }
    let mut out = std::fs::File::create(&args[2]).expect("create out");
    let t_all = std::time::Instant::now(); let mut t_cum = 0.0; let mut per_step_ms = Vec::new();
    let mut lines: Vec<String> = Vec::new();
    for (s, &(dt, phi)) in p.sched.iter().enumerate() {
        let t0 = std::time::Instant::now();
        let mut trip = dsub.clone();
        if phi > 0.0 { for &(i, j, v) in rsub.iter() { trip.push((i, j, v * phi)); } }
        let a = Csc::from_triplets(m, &trip);
        let (yy, _) = step(&a, &y, dt, &p.cram).expect("cram step");
        y = yy; t_cum += dt; per_step_ms.push(t0.elapsed().as_secs_f64() * 1e3);
        let nz: Vec<(usize, f64)> = y.iter().enumerate().filter(|(_, v)| **v != 0.0).map(|(k, v)| (keep[k], *v)).collect();
        lines.push(format!("step {} t {:.17e} k {}", s + 1, t_cum, nz.len()));
        for (g, v) in nz { lines.push(format!("{} {:.17e}", g, v)); }
    }
    let ms_total = t_all.elapsed().as_secs_f64() * 1e3;
    writeln!(out, "ACTINV-RESULT 1").unwrap();
    writeln!(out, "n {} pruned {} steps {} ms_total {:.4} ms_per_step_max {:.4} prune_mode {} bmin {:e} dropped_nodes {} dropped_bound_atoms {:.6e}", p.n, m, p.sched.len(), ms_total, per_step_ms.iter().cloned().fold(0.0, f64::max), p.prune, p.bmin, dropped, dsum).unwrap();
    for l in lines { writeln!(out, "{}", l).unwrap(); }
    writeln!(out, "dropped {}", dlist.len()).unwrap();
    for (i, b, f) in dlist { writeln!(out, "{} {:.6e} {:.6e}", i, b, f).unwrap(); }
    println!("n={} pruned={} steps={} ms_total={:.4}", p.n, m, p.sched.len(), ms_total);
}
