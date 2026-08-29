//! CRAM in the incomplete-partial-fraction recurrence used by OpenMC's IPFCramSolver
//! (coefficients: Pusa 2016, NSE 182:297; values are read from an input file, never hard-coded here).
use crate::sparse::{lu, Csc};
use num_complex::Complex64 as C64;

pub struct Cram {
    pub alpha0: f64,
    pub theta: Vec<C64>,
    pub alpha: Vec<C64>,
}

pub struct TangentStep {
    pub state: Vec<f64>,
    pub tangents: Vec<Vec<f64>>,
    pub max_fill: usize,
}

/// One step: n(t+dt) from n(t) with real matrix A (CSC, real parts in vals).
pub fn step(a: &Csc, n0: &[f64], dt: f64, c: &Cram) -> Result<(Vec<f64>, usize), String> {
    let n = a.n;
    let mut y: Vec<f64> = n0.to_vec();
    let mut max_fill = 0usize;
    for (th, al) in c.theta.iter().zip(c.alpha.iter()) {
        let m = a.scale_shift(C64::new(dt, 0.0), -*th);
        let f = lu(&m)?;
        let (ln, un) = f.nnz();
        max_fill = max_fill.max(ln + un);
        let b: Vec<C64> = y.iter().map(|v| C64::new(*v, 0.0)).collect();
        let z = f.solve(&b);
        for i in 0..n {
            y[i] += 2.0 * (al * z[i]).re;
        }
    }
    for v in y.iter_mut() {
        *v *= c.alpha0;
    }
    Ok((y, max_fill))
}

/// Apply one CRAM step and differentiate that same recurrence for independent matrix directions.
/// `tangents[p]` is `d n0 / d p`, and `directions[p]` is `d A / d p`.
pub fn step_with_tangents(
    a: &Csc,
    n0: &[f64],
    tangents: &[Vec<f64>],
    directions: &[Csc],
    direction_scale: f64,
    dt: f64,
    c: &Cram,
) -> Result<TangentStep, String> {
    let n = a.n;
    if n0.len() != n {
        return Err(format!(
            "CRAM state has {} entries for an {n}x{n} matrix",
            n0.len()
        ));
    }
    if tangents.len() != directions.len() {
        return Err(format!(
            "CRAM has {} tangent states but {} matrix directions",
            tangents.len(),
            directions.len()
        ));
    }
    if tangents.iter().any(|value| value.len() != n) || directions.iter().any(|value| value.n != n)
    {
        return Err("CRAM tangent state or matrix direction has the wrong dimension".into());
    }
    let mut y = n0.to_vec();
    let mut dy = tangents.to_vec();
    let mut max_fill = 0usize;
    for (theta, alpha) in c.theta.iter().zip(&c.alpha) {
        let matrix = a.scale_shift(C64::new(dt, 0.0), -*theta);
        let factor = lu(&matrix)?;
        let (lower, upper) = factor.nnz();
        max_fill = max_fill.max(lower + upper);
        let right_hand_side: Vec<C64> = y.iter().map(|value| C64::new(*value, 0.0)).collect();
        let z = factor.solve(&right_hand_side);
        for (tangent, direction) in dy.iter_mut().zip(directions) {
            let mut rhs: Vec<C64> = tangent.iter().map(|value| C64::new(*value, 0.0)).collect();
            for (column, solution) in z.iter().enumerate() {
                for entry in direction.colptr[column]..direction.colptr[column + 1] {
                    rhs[direction.rowidx[entry]] -=
                        direction.vals[entry] * solution * dt * direction_scale;
                }
            }
            let dz = factor.solve(&rhs);
            for (value, derivative) in tangent.iter_mut().zip(dz) {
                *value += 2.0 * (*alpha * derivative).re;
            }
        }
        for (value, solution) in y.iter_mut().zip(z) {
            *value += 2.0 * (*alpha * solution).re;
        }
    }
    for value in &mut y {
        *value *= c.alpha0;
    }
    for tangent in &mut dy {
        for value in tangent {
            *value *= c.alpha0;
        }
    }
    Ok(TangentStep {
        state: y,
        tangents: dy,
        max_fill,
    })
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
            for i in 0..n {
                y[i] += 2.0 * (al * z[i]).re;
            }
        }
    }
    for y in ys.iter_mut() {
        for v in y.iter_mut() {
            *v *= c.alpha0;
        }
    }
    Ok(ys)
}

#[cfg(test)]
mod tests {
    use super::{step, step_with_tangents, Cram};
    use crate::cram_coeffs::{CRAM16_ALPHA, CRAM16_ALPHA0, CRAM16_THETA};
    use crate::sparse::Csc;
    use num_complex::Complex64 as C64;

    fn cram16() -> Cram {
        Cram {
            alpha0: CRAM16_ALPHA0,
            theta: CRAM16_THETA
                .iter()
                .map(|(real, imaginary)| C64::new(*real, *imaginary))
                .collect(),
            alpha: CRAM16_ALPHA
                .iter()
                .map(|(real, imaginary)| C64::new(*real, *imaginary))
                .collect(),
        }
    }

    fn matrix(parameter: f64) -> Csc {
        Csc::from_triplets(
            2,
            &[
                (0, 0, C64::new(-parameter, 0.0)),
                (1, 0, C64::new(parameter, 0.0)),
                (1, 1, C64::new(-0.05, 0.0)),
            ],
        )
    }

    #[test]
    fn recurrence_tangent_matches_five_point_difference() {
        let coefficients = cram16();
        let direction = Csc::from_triplets(
            2,
            &[(0, 0, C64::new(-1.0, 0.0)), (1, 0, C64::new(1.0, 0.0))],
        );
        let initial = [1.0, 0.0];
        let nominal = 0.2;
        let duration = 0.7;
        let result = step_with_tangents(
            &matrix(nominal),
            &initial,
            &[vec![0.0; 2]],
            &[direction],
            1.0,
            duration,
            &coefficients,
        )
        .expect("differentiate CRAM");
        let h = 1.0e-4;
        let values = [-2.0, -1.0, 1.0, 2.0].map(|offset| {
            step(
                &matrix(nominal + offset * h),
                &initial,
                duration,
                &coefficients,
            )
            .expect("finite-difference CRAM")
            .0
        });
        for (state, analytic) in result.tangents[0].iter().enumerate() {
            let finite = (values[0][state] - 8.0 * values[1][state] + 8.0 * values[2][state]
                - values[3][state])
                / (12.0 * h);
            let scale = finite.abs().max(1.0e-14);
            assert!((*analytic - finite).abs() / scale < 2.0e-9);
        }
    }

    #[test]
    fn decay_semigroup_matches_analytic_parent_and_daughter() {
        let coefficients = cram16();
        let decay_constant = std::f64::consts::LN_2 / 100.0;
        let generator = Csc::from_triplets(
            2,
            &[
                (0, 0, C64::new(-decay_constant, 0.0)),
                (1, 0, C64::new(decay_constant, 0.0)),
            ],
        );
        let initial = [1.0e20, 0.0];
        let unsplit = step(&generator, &initial, 300.0, &coefficients)
            .expect("unsplit decay")
            .0;
        let first = step(&generator, &initial, 100.0, &coefficients)
            .expect("first decay partition")
            .0;
        let split = step(&generator, &first, 200.0, &coefficients)
            .expect("second decay partition")
            .0;
        let expected_parent = initial[0] * (-decay_constant * 300.0).exp();
        let expected_daughter = initial[0] - expected_parent;
        for (actual, expected) in unsplit.iter().zip([expected_parent, expected_daughter]) {
            assert!((actual - expected).abs() / expected < 5.0e-11);
        }
        for (one, partitioned) in unsplit.iter().zip(split) {
            assert!((one - partitioned).abs() / one.abs().max(1.0) < 5.0e-11);
        }
    }
}
