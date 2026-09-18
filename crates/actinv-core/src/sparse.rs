//! Sparse complex CSC matrix and a left-looking (Gilbert–Peierls) LU with partial pivoting.
//! Own implementation (structure follows the classic CSparse `cs_lu`); no external LA crates.
use num_complex::Complex64 as C64;

/// Complex division by Smith's algorithm, identical to CPython's `_Py_c_quot`, so that the Python
/// reference (`controls/cram_ref.py`) and this crate round identically (P1 Amendment B).
#[inline]
pub fn cdiv(a: C64, b: C64) -> C64 {
    let (abs_br, abs_bi) = (b.re.abs(), b.im.abs());
    if abs_br >= abs_bi {
        if abs_br == 0.0 {
            return C64::new(f64::NAN, f64::NAN);
        }
        let ratio = b.im / b.re;
        let denom = b.re + b.im * ratio;
        C64::new((a.re + a.im * ratio) / denom, (a.im - a.re * ratio) / denom)
    } else {
        let ratio = b.re / b.im;
        let denom = b.re * ratio + b.im;
        C64::new((a.re * ratio + a.im) / denom, (a.im * ratio - a.re) / denom)
    }
}

#[derive(Clone, Debug)]
pub struct Csc {
    pub n: usize,
    pub colptr: Vec<usize>,
    pub rowidx: Vec<usize>,
    pub vals: Vec<C64>,
}

impl Csc {
    /// Build from triplets (duplicates summed), n x n.
    pub fn from_triplets(n: usize, trip: &[(usize, usize, C64)]) -> Csc {
        let mut cols: Vec<Vec<(usize, C64)>> = vec![Vec::new(); n];
        for &(i, j, v) in trip {
            cols[j].push((i, v));
        }
        let mut colptr = Vec::with_capacity(n + 1);
        let mut rowidx = Vec::new();
        let mut vals = Vec::new();
        colptr.push(0);
        for c in cols.iter_mut() {
            c.sort_by_key(|e| e.0);
            let mut last: Option<usize> = None;
            for &(i, v) in c.iter() {
                if last == Some(i) {
                    let l = vals.len() - 1;
                    vals[l] += v;
                } else {
                    rowidx.push(i);
                    vals.push(v);
                    last = Some(i);
                }
            }
            colptr.push(rowidx.len());
        }
        Csc {
            n,
            colptr,
            rowidx,
            vals,
        }
    }
    /// M = a*A + s*I  (a, s complex)
    pub fn scale_shift(&self, a: C64, s: C64) -> Csc {
        let mut trip: Vec<(usize, usize, C64)> = Vec::with_capacity(self.vals.len() + self.n);
        for j in 0..self.n {
            for p in self.colptr[j]..self.colptr[j + 1] {
                trip.push((self.rowidx[p], j, a * self.vals[p]));
            }
            trip.push((j, j, s));
        }
        Csc::from_triplets(self.n, &trip)
    }
}

pub struct Lu {
    pub n: usize,
    pub lp: Vec<usize>,
    pub li: Vec<usize>,
    pub lx: Vec<C64>,
    pub up: Vec<usize>,
    pub ui: Vec<usize>,
    pub ux: Vec<C64>,
    pub pinv: Vec<usize>,
}

struct Work {
    xi: Vec<usize>,
    x: Vec<C64>,
    mark: Vec<u32>,
    stamp: u32,
    stack: Vec<usize>,
    pstack: Vec<usize>,
}

/// Depth-first reach of B(:,k)'s pattern through the columns of L already computed (pinv maps row -> L column, usize::MAX = none).
fn reach(
    lp: &[usize],
    li: &[usize],
    b_rows: &[usize],
    pinv: &[usize],
    w: &mut Work,
    n: usize,
) -> usize {
    w.stamp = w.stamp.wrapping_add(1);
    if w.stamp == 0 {
        for m in w.mark.iter_mut() {
            *m = 0;
        }
        w.stamp = 1;
    }
    let stamp = w.stamp;
    let mut top = n;
    for &r in b_rows {
        if w.mark[r] == stamp {
            continue;
        }
        w.stack.clear();
        w.stack.push(r);
        w.pstack.clear();
        w.mark[r] = stamp;
        let jj = pinv[r];
        w.pstack.push(if jj != usize::MAX { lp[jj] } else { 0 });
        while let Some(&j) = w.stack.last() {
            let d = w.stack.len() - 1;
            let jj = pinv[j];
            let mut done = true;
            if jj != usize::MAX {
                let mut p = w.pstack[d];
                let p2 = lp[jj + 1];
                while p < p2 {
                    let i = li[p];
                    p += 1;
                    if w.mark[i] != stamp {
                        w.mark[i] = stamp;
                        w.pstack[d] = p;
                        w.stack.push(i);
                        let ij = pinv[i];
                        w.pstack.push(if ij != usize::MAX { lp[ij] } else { 0 });
                        done = false;
                        break;
                    }
                }
            }
            if done {
                w.stack.pop();
                w.pstack.pop();
                top -= 1;
                w.xi[top] = j;
            }
        }
    }
    top
}

/// Numeric LU with partial pivoting (threshold 1.0 => prefer the diagonal when it is the largest magnitude).
pub fn lu(a: &Csc) -> Result<Lu, String> {
    let n = a.n;
    let mut lp = vec![0usize; n + 1];
    let mut li: Vec<usize> = Vec::with_capacity(4 * a.vals.len());
    let mut lx: Vec<C64> = Vec::with_capacity(4 * a.vals.len());
    let mut up = vec![0usize; n + 1];
    let mut ui: Vec<usize> = Vec::with_capacity(4 * a.vals.len());
    let mut ux: Vec<C64> = Vec::with_capacity(4 * a.vals.len());
    let mut pinv = vec![usize::MAX; n];
    let mut w = Work {
        xi: vec![0; n],
        x: vec![C64::new(0.0, 0.0); n],
        mark: vec![0; n],
        stamp: 0,
        stack: Vec::new(),
        pstack: Vec::new(),
    };
    for k in 0..n {
        lp[k] = li.len();
        up[k] = ui.len();
        let b_rows = &a.rowidx[a.colptr[k]..a.colptr[k + 1]];
        // x = L \ A(:,k) on the reach
        let top = reach(&lp, &li, b_rows, &pinv, &mut w, n);
        for p in top..n {
            w.x[w.xi[p]] = C64::new(0.0, 0.0);
        }
        for p in a.colptr[k]..a.colptr[k + 1] {
            w.x[a.rowidx[p]] = a.vals[p];
        }
        for px in top..n {
            let j = w.xi[px];
            let jj = pinv[j];
            if jj == usize::MAX {
                continue;
            }
            let xj = w.x[j]; // L(j,j) = 1
            for p in lp[jj] + 1..lp[jj + 1] {
                let i = li[p];
                w.x[i] -= lx[p] * xj;
            }
        }
        // pivot
        let mut ipiv = usize::MAX;
        let mut amax = -1.0f64;
        for p in top..n {
            let i = w.xi[p];
            if pinv[i] == usize::MAX {
                let t = w.x[i].norm();
                if t > amax {
                    amax = t;
                    ipiv = i;
                }
            } else {
                ui.push(pinv[i]);
                ux.push(w.x[i]);
            }
        }
        if ipiv == usize::MAX || amax <= 0.0 {
            return Err(format!("singular at column {}", k));
        }
        if pinv[k] == usize::MAX && w.x[k].norm() >= amax * 1.0 {
            ipiv = k;
        }
        let pivot = w.x[ipiv];
        ui.push(k);
        ux.push(pivot);
        pinv[ipiv] = k;
        li.push(ipiv);
        lx.push(C64::new(1.0, 0.0));
        for p in top..n {
            let i = w.xi[p];
            if pinv[i] == usize::MAX {
                li.push(i);
                lx.push(cdiv(w.x[i], pivot));
            }
            w.x[i] = C64::new(0.0, 0.0);
        }
    }
    lp[n] = li.len();
    up[n] = ui.len();
    for v in li.iter_mut() {
        *v = pinv[*v];
    }
    Ok(Lu {
        n,
        lp,
        li,
        lx,
        up,
        ui,
        ux,
        pinv,
    })
}

impl Lu {
    /// Solve (L U) x = P b ; returns x (original column numbering; no column permutation used).
    pub fn solve(&self, b: &[C64]) -> Vec<C64> {
        let n = self.n;
        let mut x = vec![C64::new(0.0, 0.0); n];
        for i in 0..n {
            x[self.pinv[i]] = b[i];
        }
        for j in 0..n {
            let xj = cdiv(x[j], self.lx[self.lp[j]]);
            x[j] = xj;
            for p in self.lp[j] + 1..self.lp[j + 1] {
                let i = self.li[p];
                x[i] -= self.lx[p] * xj;
            }
        }
        for j in (0..n).rev() {
            let xj = cdiv(x[j], self.ux[self.up[j + 1] - 1]);
            x[j] = xj;
            for p in self.up[j]..self.up[j + 1] - 1 {
                let i = self.ui[p];
                x[i] -= self.ux[p] * xj;
            }
        }
        x
    }

    /// Solve and refine against the original matrix, reusing this factorization.
    ///
    /// A small normwise residual is insufficient when row pivoting mixes a tiny
    /// state with a large background. Compute every residual component with
    /// compensated sums and fused-product error terms, then solve for the
    /// correction. No component is discarded based on its population or sign.
    /// Five iterations bound the work (the same cap used by LAPACK GERFS);
    /// convergence stops earlier when every correction falls below its row's
    /// backward-error floor. This is not a forward-error/conditioning bound.
    ///
    /// A fast path skips refinement only where the solve demonstrably behaved:
    /// every row's componentwise backward error below `OMEGA_TOL`, no trace
    /// component more than 1e-12 below the largest populated component, and
    /// no solution amplification beyond `GROWTH_TOL` times the right-hand
    /// side. Any flagged solve takes the full refinement path.
    pub fn solve_refined(&self, a: &Csc, b: &[C64]) -> Result<Vec<C64>, String> {
        if a.n != self.n || b.len() != self.n {
            return Err("refined solve matrix/right-hand-side dimension mismatch".into());
        }
        let finite = |v: &C64| v.re.is_finite() && v.im.is_finite();
        if !b.iter().all(finite) {
            return Err("refined solve has a non-finite right-hand side".into());
        }
        let mut x = self.solve(b);
        if !x.iter().all(finite) {
            return Err("non-finite linear solution".into());
        }
        // Fast path: skip refinement only where the solve demonstrably
        // behaved. A normwise residual gate is insufficient (a phantom
        // component can hide under a large legitimate residual), so the
        // check is componentwise: every row must satisfy the LAPACK-style
        // backward-error bound |r_i| <= OMEGA_TOL * (|b_i| + |A||x|_i),
        // and the solution must not have amplified the right-hand side.
        // OMEGA_TOL is calibrated, not guessed: the phantom-parent
        // generators this fix guards produce componentwise backward
        // errors of order 1, while clean solves measure <= ~1e-8, so
        // 1e-6 sits in a multi-decade separation gap. Flagged solves take
        // the full compensated-refinement path below.
        const OMEGA_TOL: f64 = 1.0e-6;
        const GROWTH_TOL: f64 = 1.0e6;
        const RANGE_TOL: f64 = 1.0e-12;
        let b_norm = b.iter().map(|v| v.norm()).fold(0.0_f64, f64::max);
        let x_norm = x.iter().map(|v| v.norm()).fold(0.0_f64, f64::max);
        // Mixed-scale states are the regime this fix exists for: a trace
        // component formed by cancellation of huge inputs can carry a large
        // forward error despite a small componentwise backward error. Any
        // populated component more than 1e12 below the largest engages
        // refinement. Zero components are not populated by definition.
        let x_min_nonzero = x
            .iter()
            .map(|v| v.norm())
            .filter(|&v| v > 0.0)
            .fold(f64::INFINITY, f64::min);
        let single_scale = x_min_nonzero >= RANGE_TOL * x_norm;
        // Compensated residual and per-row input magnitude, computed once:
        // the gate's componentwise check and the first refinement iteration
        // share this vector so a flagged solve does not pay for a second
        // matrix product.
        let mut residual = b.to_vec();
        let mut tail = vec![C64::new(0.0, 0.0); self.n];
        let mut magnitude = vec![0.0_f64; self.n];
        for (column, value) in x.iter().enumerate() {
            let vn = value.norm();
            for entry in a.colptr[column]..a.colptr[column + 1] {
                let row = a.rowidx[entry];
                let coefficient = a.vals[entry];
                let (sum, error) = (&mut residual[row], &mut tail[row]);
                add_product(&mut sum.re, &mut error.re, -coefficient.re, value.re);
                add_product(&mut sum.re, &mut error.re, coefficient.im, value.im);
                add_product(&mut sum.im, &mut error.im, -coefficient.re, value.im);
                add_product(&mut sum.im, &mut error.im, -coefficient.im, value.re);
                magnitude[row] += coefficient.norm() * vn;
            }
        }
        for (value, error) in residual.iter_mut().zip(tail) {
            *value += error;
        }
        if !residual.iter().all(finite) {
            return Err("non-finite linear residual".into());
        }
        let backward_ok = residual
            .iter()
            .zip(b.iter())
            .zip(&magnitude)
            .all(|((&r, &bi), &mag)| {
                let denom = bi.norm() + mag;
                if denom > 0.0 {
                    r.norm() <= OMEGA_TOL * denom
                } else {
                    r.norm() == 0.0
                }
            });
        if backward_ok && single_scale && x_norm <= GROWTH_TOL * b_norm.max(f64::MIN_POSITIVE) {
            return Ok(x);
        }
        let mut residual_computed = true;
        for _ in 0..5 {
            if !residual_computed {
                residual.copy_from_slice(b);
                let mut tail = vec![C64::new(0.0, 0.0); self.n];
                for (column, value) in x.iter().enumerate() {
                    for entry in a.colptr[column]..a.colptr[column + 1] {
                        let row = a.rowidx[entry];
                        let coefficient = a.vals[entry];
                        let (sum, error) = (&mut residual[row], &mut tail[row]);
                        add_product(&mut sum.re, &mut error.re, -coefficient.re, value.re);
                        add_product(&mut sum.re, &mut error.re, coefficient.im, value.im);
                        add_product(&mut sum.im, &mut error.im, -coefficient.re, value.im);
                        add_product(&mut sum.im, &mut error.im, -coefficient.im, value.re);
                    }
                }
                for (value, error) in residual.iter_mut().zip(tail) {
                    *value += error;
                }
                if !residual.iter().all(finite) {
                    return Err("non-finite linear residual".into());
                }
            }
            residual_computed = false;
            let correction = self.solve(&residual);
            if !correction.iter().all(finite) {
                return Err("non-finite linear refinement correction".into());
            }
            let mut changed = false;
            let mut resolvable_correction = true;
            for ((value, delta), (bi, &mag)) in
                x.iter_mut().zip(correction).zip(b.iter().zip(&magnitude))
            {
                let denom = bi.norm() + mag;
                // A correction below a row's own backward-error floor cannot
                // move anything the refinement protects; above it, the row is
                // still live and refinement continues.
                resolvable_correction &= if denom > 0.0 {
                    delta.norm() <= 1.0e-14 * denom
                } else {
                    delta.norm() == 0.0
                };
                let corrected = *value + delta;
                changed |= corrected != *value;
                *value = corrected;
            }
            if !x.iter().all(finite) {
                return Err("non-finite refined linear solution".into());
            }
            if !changed || resolvable_correction {
                break;
            }
        }
        Ok(x)
    }
    pub fn nnz(&self) -> (usize, usize) {
        (self.li.len(), self.ui.len())
    }
}

/// Neumaier accumulation plus the error-free product remainder (when finite).
fn add_product(sum: &mut f64, tail: &mut f64, left: f64, right: f64) {
    let product = left * right;
    let updated = *sum + product;
    *tail += if sum.abs() >= product.abs() {
        (*sum - updated) + product
    } else {
        (product - updated) + *sum
    };
    *tail += left.mul_add(right, -product);
    *sum = updated;
}

#[cfg(test)]
mod tests {
    use super::{lu, Csc};
    use num_complex::Complex64 as C64;

    #[test]
    fn refinement_preserves_zero_component_next_to_large_background() {
        let matrix = Csc::from_triplets(
            2,
            &[
                (0, 0, C64::new(-90.0, -20.0)),
                (1, 0, C64::new(100.0, 0.0)),
                (1, 1, C64::new(10.0, -20.0)),
            ],
        );
        let factor = lu(&matrix).unwrap();
        assert_eq!(factor.pinv, vec![1, 0]);
        let rhs = [C64::new(0.0, 0.0), C64::new(1.0e24, 0.0)];
        let result = factor.solve_refined(&matrix, &rhs).unwrap();
        assert!(result[0].norm() <= 1.0e-6);
        let expected = super::cdiv(rhs[1], C64::new(10.0, -20.0));
        assert!((result[1] - expected).norm() / expected.norm() <= 1.0e-12);
    }

    #[test]
    fn refinement_supports_complex_system_requiring_pivoting() {
        let matrix = Csc::from_triplets(
            2,
            &[
                (1, 0, C64::new(3.0, -2.0)),
                (0, 1, C64::new(2.0, 1.0)),
                (1, 1, C64::new(1.0, 0.5)),
            ],
        );
        let expected = [C64::new(1.0, -3.0), C64::new(2.0, 0.25)];
        let rhs = [
            C64::new(2.0, 1.0) * expected[1],
            C64::new(3.0, -2.0) * expected[0] + C64::new(1.0, 0.5) * expected[1],
        ];
        let factor = lu(&matrix).unwrap();
        assert_eq!(factor.pinv, vec![1, 0]);
        let actual = factor.solve_refined(&matrix, &rhs).unwrap();
        for (a, b) in actual.iter().zip(expected) {
            assert!((*a - b).norm() / b.norm() <= 1.0e-12);
        }
        let mut residual = rhs;
        let mut scale = rhs.map(|v| v.norm());
        for (column, value) in actual.iter().enumerate() {
            for entry in matrix.colptr[column]..matrix.colptr[column + 1] {
                let row = matrix.rowidx[entry];
                residual[row] -= matrix.vals[entry] * value;
                scale[row] += matrix.vals[entry].norm() * value.norm();
            }
        }
        for (r, denominator) in residual.iter().zip(scale) {
            assert!(r.norm() / denominator <= 1.0e-12);
        }
        assert!(factor.solve_refined(&matrix, &rhs[..1]).is_err());
        assert!(factor
            .solve_refined(&matrix, &[C64::new(f64::NAN, 0.0); 2])
            .is_err());
    }
}
