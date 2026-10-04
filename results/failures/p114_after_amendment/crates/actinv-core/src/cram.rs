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
        let z = f.solve_refined(&m, &b)?;
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
/// `tangents[p]` is `d n0 / d p`, `directions[p]` is `d A / d p` before the
/// schedule's flux multiplier, and `direction_scales[p]` is the multiplier each
/// direction carries this step (flux-scaled reaction directions, unscaled decay).
pub fn step_with_tangents(
    a: &Csc,
    n0: &[f64],
    tangents: &[Vec<f64>],
    directions: &[Csc],
    direction_scales: &[f64],
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
    if tangents.len() != directions.len() || direction_scales.len() != directions.len() {
        return Err(format!(
            "CRAM has {} tangent states, {} matrix directions and {} direction scales",
            tangents.len(),
            directions.len(),
            direction_scales.len()
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
        let z = factor.solve_refined(&matrix, &right_hand_side)?;
        for ((tangent, direction), &direction_scale) in
            dy.iter_mut().zip(directions).zip(direction_scales)
        {
            let mut rhs: Vec<C64> = tangent.iter().map(|value| C64::new(*value, 0.0)).collect();
            for (column, solution) in z.iter().enumerate() {
                for entry in direction.colptr[column]..direction.colptr[column + 1] {
                    rhs[direction.rowidx[entry]] -=
                        direction.vals[entry] * solution * dt * direction_scale;
                }
            }
            let dz = factor.solve_refined(&matrix, &rhs)?;
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
            let z = f.solve_refined(&m, &b)?;
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
    use super::{step, step_multi, step_with_tangents, Cram};
    use crate::cram_coeffs::{
        CRAM16_ALPHA, CRAM16_ALPHA0, CRAM16_THETA, CRAM48_ALPHA, CRAM48_ALPHA0, CRAM48_THETA,
    };
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

    fn cram48() -> Cram {
        Cram {
            alpha0: CRAM48_ALPHA0,
            theta: CRAM48_THETA.iter().map(|&(r, i)| C64::new(r, i)).collect(),
            alpha: CRAM48_ALPHA.iter().map(|&(r, i)| C64::new(r, i)).collect(),
        }
    }

    #[test]
    fn stable_background_cannot_create_absent_radioactive_parent() {
        for coefficients in [cram16(), cram48()] {
            for (parent, daughter) in [(0, 1), (1, 0)] {
                let generator = Csc::from_triplets(
                    2,
                    &[
                        (parent, parent, C64::new(-1.0, 0.0)),
                        (daughter, parent, C64::new(1.0, 0.0)),
                    ],
                );
                for dt in [1.0e-6, 1.0, 100.0, 1.0e4, 1.0e8, 1.0e12] {
                    for background in [1.0, 1.0e12, 1.0e21, 1.0e24] {
                        let mut initial = vec![0.0; 2];
                        initial[daughter] = background;
                        let scalar = step(&generator, &initial, dt, &coefficients).unwrap().0;
                        let multi = step_multi(
                            &generator,
                            std::slice::from_ref(&initial),
                            dt,
                            &coefficients,
                        )
                        .unwrap();
                        let tangent = step_with_tangents(
                            &generator,
                            &initial,
                            &[vec![0.0; 2]],
                            std::slice::from_ref(&generator),
                            &[1.0],
                            dt,
                            &coefficients,
                        )
                        .unwrap();
                        for state in [&scalar, &multi[0], &tangent.state] {
                            assert!(state[parent].abs() <= 1.0e-6,
                                "absent parent = {}, dt = {dt}, background = {background}, parent index = {parent}, poles = {}",
                                state[parent], coefficients.theta.len());
                            assert!((state[daughter] - background).abs() / background <= 1.0e-12);
                        }
                        assert!(tangent.tangents[0].iter().all(|v| v.abs() <= 1.0e-6));
                    }
                }
            }
        }
    }

    #[test]
    fn trace_daughter_matches_bateman_despite_large_stable_background() {
        let slow = 1.0e-14;
        let fast = 1.0;
        for order in [
            [0, 1, 2],
            [0, 2, 1],
            [1, 0, 2],
            [1, 2, 0],
            [2, 0, 1],
            [2, 1, 0],
        ] {
            let [parent, daughter, stable] = order;
            let generator = Csc::from_triplets(
                3,
                &[
                    (parent, parent, C64::new(-slow, 0.0)),
                    (daughter, parent, C64::new(slow, 0.0)),
                    (daughter, daughter, C64::new(-fast, 0.0)),
                    (stable, daughter, C64::new(fast, 0.0)),
                ],
            );
            for coefficients in [cram16(), cram48()] {
                for dt in [1.0, 100.0, 1.0e6, 1.0e12] {
                    let mut initial = vec![0.0; 3];
                    initial[parent] = 1.0e18;
                    initial[stable] = 1.0e24;
                    let result = step(&generator, &initial, dt, &coefficients).unwrap().0;
                    let expected_parent = initial[parent] * (-slow * dt).exp();
                    let expected_daughter = initial[parent] * slow / (fast - slow)
                        * ((-slow * dt).exp() - (-fast * dt).exp());
                    assert!((result[parent] - expected_parent).abs() / expected_parent <= 1.0e-10);
                    assert!(
                        (result[daughter] - expected_daughter).abs() / expected_daughter <= 1.0e-10,
                        "daughter {}, expected {expected_daughter}, order {order:?}, dt {dt}",
                        result[daughter]
                    );
                    assert!(
                        (result.iter().sum::<f64>() - initial.iter().sum::<f64>()).abs()
                            / initial.iter().sum::<f64>()
                            <= 1.0e-12
                    );
                }
            }
        }
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
            &[1.0],
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
