//! CRAM in the incomplete-partial-fraction recurrence used by OpenMC's IPFCramSolver
//! (coefficients: Pusa 2016, NSE 182:297; values are read from an input file, never hard-coded here).
use num_complex::Complex64 as C64;
use crate::sparse::{Csc, lu};

pub struct Cram { pub alpha0: f64, pub theta: Vec<C64>, pub alpha: Vec<C64> }

/// One step: n(t+dt) from n(t) with real matrix A (CSC, real parts in vals).
pub fn step(a: &Csc, n0: &[f64], dt: f64, c: &Cram) -> Result<(Vec<f64>, usize), String> {
    let n = a.n; let mut y: Vec<f64> = n0.to_vec(); let mut max_fill = 0usize;
    for (th, al) in c.theta.iter().zip(c.alpha.iter()) {
        let m = a.scale_shift(C64::new(dt, 0.0), -*th);
        let f = lu(&m)?; let (ln, un) = f.nnz(); max_fill = max_fill.max(ln + un);
        let b: Vec<C64> = y.iter().map(|v| C64::new(*v, 0.0)).collect();
        let z = f.solve(&b);
        for i in 0..n { y[i] += 2.0 * (al * z[i]).re; }
    }
    for v in y.iter_mut() { *v *= c.alpha0; }
    Ok((y, max_fill))
}

/// One CRAM step applied to many right-hand sides at once. The factorisation of (dt*A - theta_k I) is shared across
/// all columns, so k right-hand sides cost k solves per pole rather than k full factorisations. Used by the pathway
/// analysis, where each column is the contribution of one source reaction.
pub fn step_multi(a: &Csc, cols: &[Vec<f64>], dt: f64, c: &Cram) -> Result<Vec<Vec<f64>>, String> {
    let n = a.n;
    let mut ys: Vec<Vec<f64>> = cols.to_vec();
    for (th, al) in c.theta.iter().zip(c.alpha.iter()) {
        let m = a.scale_shift(C64::new(dt, 0.0), -*th);
        let f = lu(&m)?;
        for y in ys.iter_mut() {
            let b: Vec<C64> = y.iter().map(|v| C64::new(*v, 0.0)).collect();
            let z = f.solve(&b);
            for i in 0..n { y[i] += 2.0 * (al * z[i]).re; }
        }
    }
    for y in ys.iter_mut() { for v in y.iter_mut() { *v *= c.alpha0; } }
    Ok(ys)
}
