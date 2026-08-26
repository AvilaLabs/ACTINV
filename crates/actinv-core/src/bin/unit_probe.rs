//! Minimal probe: a constant unit source feeding one product. Exact answer: unit stays 1, product = rate*dt.
use actinv_core::sparse::Csc; use actinv_core::cram::{Cram, step};
use num_complex::Complex64 as C64;
fn main() {
    use actinv_core::cram_coeffs::{CRAM16_ALPHA, CRAM16_ALPHA0, CRAM16_THETA};
    let c = Cram { alpha0: CRAM16_ALPHA0,
        theta: CRAM16_THETA.iter().map(|(r, i)| C64::new(*r, *i)).collect(),
        alpha: CRAM16_ALPHA.iter().map(|(r, i)| C64::new(*r, *i)).collect() };
    // state 0 = product (decay lambda), state 1 = unit source
    for (rate, lam, dt) in [(1.1e7, 0.0, 300.0), (1.1e7, 7.466e-5, 300.0), (1.0, 0.0, 1.0)] {
        let mut trip = vec![(0usize, 1usize, C64::new(rate, 0.0))];
        if lam > 0.0 { trip.push((0, 0, C64::new(-lam, 0.0))); }
        let a = Csc::from_triplets(2, &trip);
        let (y, _) = step(&a, &[0.0, 1.0], dt, &c).unwrap();
        let exact = if lam > 0.0 { rate / lam * (1.0 - (-lam * dt).exp()) } else { rate * dt };
        println!("rate {rate:e} lambda {lam:e} dt {dt}: product {:.6e} (exact {:.6e})  unit {:.6e} (exact 1)", y[0], exact, y[1]);
    }
}
