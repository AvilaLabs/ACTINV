//! P1-G2 probe: read matrix + n0 + dt + CRAM coefficients from a text file, run one CRAM-16 step,
//! time it over `reps` repetitions, write the result. Usage: cram_probe IN OUT [reps]
use actinv_core::cram::{step, Cram};
use actinv_core::sparse::Csc;
use num_complex::Complex64 as C64;
use std::io::{BufRead, BufReader, Write};
fn main() {
    let args: Vec<String> = std::env::args().collect();
    let reps: usize = args.get(3).map(|s| s.parse().unwrap()).unwrap_or(20);
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
