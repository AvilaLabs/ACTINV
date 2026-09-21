//! P1-G2 probe: read matrix + n0 + dt + CRAM coefficients from a text file, run one CRAM-16 step,
//! time it over `reps` repetitions, write the result. Usage: cram_probe IN OUT [reps]
//! With `phases` as a 4th arg, print a per-pole phase breakdown instead.
use actinv_core::cram::{step, Cram};
use actinv_core::sparse::{lu, refinement_stats, Csc};
use num_complex::Complex64 as C64;
use std::io::{BufRead, BufReader, Write};

fn phases(a: &Csc, n0: &[f64], dt: f64, c: &Cram) {
    use std::time::Instant;
    let n = a.n;
    let mut y = n0.to_vec();
    let (mut t_shift, mut t_lu, mut t_solve) = (0.0, 0.0, 0.0);
    let mut stats0 = [0u64; 5];
    let mut max_fill = 0usize;
    for (th, al) in c.theta.iter().zip(c.alpha.iter()) {
        let t = Instant::now();
        let m = a.scale_shift(C64::new(dt, 0.0), -*th);
        t_shift += t.elapsed().as_secs_f64() * 1e3;
        let t = Instant::now();
        let f = lu(&m).unwrap();
        t_lu += t.elapsed().as_secs_f64() * 1e3;
        let (ln, un) = f.nnz();
        max_fill = max_fill.max(ln + un);
        let b: Vec<C64> = y.iter().map(|v| C64::new(*v, 0.0)).collect();
        let s0 = refinement_stats();
        let t = Instant::now();
        let z = f.solve_refined(&m, &b).unwrap();
        t_solve += t.elapsed().as_secs_f64() * 1e3;
        let s1 = refinement_stats();
        for i in 0..5 {
            stats0[i] += s1[i] - s0[i];
        }
        if s1[4] > s0[4] + 1 && std::env::var("CRAM_PROBE_DIAG").is_ok() {
            eprintln!("    pole took {} iters", s1[4] - s0[4]);
        }
        if s1[0] == s0[0] && std::env::var("CRAM_PROBE_DIAG").is_ok() {
            // Flagged solve: report the worst componentwise residual rows.
            let mut r = b.clone();
            let mut mag = vec![0.0f64; n];
            for (col, v) in z.iter().enumerate() {
                for e in m.colptr[col]..m.colptr[col + 1] {
                    let row = m.rowidx[e];
                    r[row] -= m.vals[e] * v;
                    mag[row] += m.vals[e].norm() * v.norm();
                }
            }
            let mut rows: Vec<(f64, usize, f64, f64)> = (0..n)
                .map(|i| {
                    let d = b[i].norm() + mag[i];
                    (r[i].norm() / d.max(1e-320), i, r[i].norm(), d)
                })
                .collect();
            rows.sort_by(|x, y| x.0.partial_cmp(&y.0).unwrap());
            for &(ratio, i, rn, d) in rows.iter().rev().take(6) {
                eprintln!(
                    "    row {i}: |r|={rn:.3e} denom={d:.3e} ratio={ratio:.3e} |z|={:.3e}",
                    z[i].norm()
                );
            }
        }
        for i in 0..n {
            y[i] += 2.0 * (al * z[i]).re;
        }
    }
    println!(
        "poles={} fast={} ref_resid={} ref_range={} ref_growth={} iters={} | shift={:.3}ms lu={:.3}ms solve={:.3}ms fill={}",
        c.theta.len(), stats0[0], stats0[1], stats0[2], stats0[3],
        stats0[4], t_shift, t_lu, t_solve, max_fill
    );
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let reps: usize = args.get(3).map(|s| s.parse().unwrap()).unwrap_or(20);
    let do_phases = args.get(4).map(|s| s == "phases").unwrap_or(false);
    let f = BufReader::new(std::fs::File::open(&args[1]).unwrap());
    let mut it = f.lines().map(|l| l.unwrap());
    let hdr: Vec<usize> = it
        .next()
        .unwrap()
        .split_whitespace()
        .map(|s| s.parse().unwrap())
        .collect();
    let (n, nnz) = (hdr[0], hdr[1]);
    let mut trip = Vec::with_capacity(nnz);
    for _ in 0..nnz {
        let l = it.next().unwrap();
        let p: Vec<&str> = l.split_whitespace().collect();
        trip.push((
            p[0].parse::<usize>().unwrap(),
            p[1].parse::<usize>().unwrap(),
            C64::new(p[2].parse::<f64>().unwrap(), 0.0),
        ));
    }
    let a = Csc::from_triplets(n, &trip);
    let dt: f64 = it.next().unwrap().trim().parse().unwrap();
    let n0: Vec<f64> = {
        let l = it.next().unwrap();
        l.split_whitespace().map(|s| s.parse().unwrap()).collect()
    };
    assert_eq!(n0.len(), n);
    let alpha0: f64 = it.next().unwrap().trim().parse().unwrap();
    let k: usize = it.next().unwrap().trim().parse().unwrap();
    let mut theta = Vec::new();
    let mut alpha = Vec::new();
    for _ in 0..k {
        let l = it.next().unwrap();
        let p: Vec<f64> = l.split_whitespace().map(|s| s.parse().unwrap()).collect();
        theta.push(C64::new(p[0], p[1]));
        alpha.push(C64::new(p[2], p[3]));
    }
    let c = Cram {
        alpha0,
        theta,
        alpha,
    };
    if do_phases {
        for _ in 0..reps {
            phases(&a, &n0, dt, &c);
        }
        return;
    }
    let (y, fill) = step(&a, &n0, dt, &c).unwrap();
    let t0 = std::time::Instant::now();
    let mut chk = 0.0;
    for _ in 0..reps {
        let (yy, _) = step(&a, &n0, dt, &c).unwrap();
        chk += yy[0];
    }
    let ms = t0.elapsed().as_secs_f64() * 1e3 / reps as f64;
    let mut out = std::fs::File::create(&args[2]).unwrap();
    writeln!(
        out,
        "# n={} nnz={} max_LU_nnz={} ms_per_step={:.4} reps={} chk={:e}",
        n, nnz, fill, ms, reps, chk
    )
    .unwrap();
    for v in y {
        writeln!(out, "{:.17e}", v).unwrap();
    }
    println!(
        "n={} nnz={} max_LU_nnz={} ms_per_step={:.4}",
        n, nnz, fill, ms
    );
}
