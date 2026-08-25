//! Sparse complex CSC matrix and a left-looking (Gilbert–Peierls) LU with partial pivoting.
//! Own implementation (structure follows the classic CSparse `cs_lu`); no external LA crates.
use num_complex::Complex64 as C64;

/// Complex division by Smith's algorithm, identical to CPython's `_Py_c_quot`, so that the Python
/// reference (`controls/cram_ref.py`) and this crate round identically (P1 Amendment B).
#[inline]
pub fn cdiv(a: C64, b: C64) -> C64 {
    let (abs_br, abs_bi) = (b.re.abs(), b.im.abs());
    if abs_br >= abs_bi {
        if abs_br == 0.0 { return C64::new(f64::NAN, f64::NAN); }
        let ratio = b.im / b.re; let denom = b.re + b.im * ratio;
        C64::new((a.re + a.im * ratio) / denom, (a.im - a.re * ratio) / denom)
    } else {
        let ratio = b.re / b.im; let denom = b.re * ratio + b.im;
        C64::new((a.re * ratio + a.im) / denom, (a.im * ratio - a.re) / denom)
    }
}

#[derive(Clone, Debug)]
pub struct Csc { pub n: usize, pub colptr: Vec<usize>, pub rowidx: Vec<usize>, pub vals: Vec<C64> }

impl Csc {
    /// Build from triplets (duplicates summed), n x n.
    pub fn from_triplets(n: usize, trip: &[(usize, usize, C64)]) -> Csc {
        let mut cols: Vec<Vec<(usize, C64)>> = vec![Vec::new(); n];
        for &(i, j, v) in trip { cols[j].push((i, v)); }
        let mut colptr = Vec::with_capacity(n + 1); let mut rowidx = Vec::new(); let mut vals = Vec::new();
        colptr.push(0);
        for c in cols.iter_mut() {
            c.sort_by_key(|e| e.0);
            let mut last: Option<usize> = None;
            for &(i, v) in c.iter() {
                if last == Some(i) { let l = vals.len() - 1; vals[l] += v; } else { rowidx.push(i); vals.push(v); last = Some(i); }
            }
            colptr.push(rowidx.len());
        }
        Csc { n, colptr, rowidx, vals }
    }
    /// M = a*A + s*I  (a, s complex)
    pub fn scale_shift(&self, a: C64, s: C64) -> Csc {
        let mut trip: Vec<(usize, usize, C64)> = Vec::with_capacity(self.vals.len() + self.n);
        for j in 0..self.n { for p in self.colptr[j]..self.colptr[j + 1] { trip.push((self.rowidx[p], j, a * self.vals[p])); } trip.push((j, j, s)); }
        Csc::from_triplets(self.n, &trip)
    }
}

pub struct Lu { pub n: usize, pub lp: Vec<usize>, pub li: Vec<usize>, pub lx: Vec<C64>, pub up: Vec<usize>, pub ui: Vec<usize>, pub ux: Vec<C64>, pub pinv: Vec<usize> }

struct Work { xi: Vec<usize>, x: Vec<C64>, mark: Vec<u32>, stamp: u32, stack: Vec<usize>, pstack: Vec<usize> }

/// Depth-first reach of B(:,k)'s pattern through the columns of L already computed (pinv maps row -> L column, usize::MAX = none).
fn reach(lp: &[usize], li: &[usize], b_rows: &[usize], pinv: &[usize], w: &mut Work, n: usize) -> usize {
    w.stamp = w.stamp.wrapping_add(1); if w.stamp == 0 { for m in w.mark.iter_mut() { *m = 0; } w.stamp = 1; }
    let stamp = w.stamp; let mut top = n;
    for &r in b_rows {
        if w.mark[r] == stamp { continue; }
        w.stack.clear(); w.stack.push(r); w.pstack.clear();
        w.mark[r] = stamp; let jj = pinv[r]; w.pstack.push(if jj != usize::MAX { lp[jj] } else { 0 });
        while let Some(&j) = w.stack.last() {
            let d = w.stack.len() - 1; let jj = pinv[j]; let mut done = true;
            if jj != usize::MAX {
                let mut p = w.pstack[d]; let p2 = lp[jj + 1];
                while p < p2 {
                    let i = li[p]; p += 1;
                    if w.mark[i] != stamp { w.mark[i] = stamp; w.pstack[d] = p; w.stack.push(i); let ij = pinv[i]; w.pstack.push(if ij != usize::MAX { lp[ij] } else { 0 }); done = false; break; }
                }
            }
            if done { w.stack.pop(); w.pstack.pop(); top -= 1; w.xi[top] = j; }
        }
    }
    top
}

/// Numeric LU with partial pivoting (threshold 1.0 => prefer the diagonal when it is the largest magnitude).
pub fn lu(a: &Csc) -> Result<Lu, String> {
    let n = a.n;
    let mut lp = vec![0usize; n + 1]; let mut li: Vec<usize> = Vec::with_capacity(4 * a.vals.len()); let mut lx: Vec<C64> = Vec::with_capacity(4 * a.vals.len());
    let mut up = vec![0usize; n + 1]; let mut ui: Vec<usize> = Vec::with_capacity(4 * a.vals.len()); let mut ux: Vec<C64> = Vec::with_capacity(4 * a.vals.len());
    let mut pinv = vec![usize::MAX; n];
    let mut w = Work { xi: vec![0; n], x: vec![C64::new(0.0, 0.0); n], mark: vec![0; n], stamp: 0, stack: Vec::new(), pstack: Vec::new() };
    for k in 0..n {
        lp[k] = li.len(); up[k] = ui.len();
        let b_rows = &a.rowidx[a.colptr[k]..a.colptr[k + 1]];
        // x = L \ A(:,k) on the reach
        let top = reach(&lp, &li, b_rows, &pinv, &mut w, n);
        for p in top..n { w.x[w.xi[p]] = C64::new(0.0, 0.0); }
        for p in a.colptr[k]..a.colptr[k + 1] { w.x[a.rowidx[p]] = a.vals[p]; }
        for px in top..n {
            let j = w.xi[px]; let jj = pinv[j]; if jj == usize::MAX { continue; }
            let xj = w.x[j]; // L(j,j) = 1
            for p in lp[jj] + 1..lp[jj + 1] { let i = li[p]; w.x[i] -= lx[p] * xj; }
        }
        // pivot
        let mut ipiv = usize::MAX; let mut amax = -1.0f64;
        for p in top..n {
            let i = w.xi[p];
            if pinv[i] == usize::MAX { let t = w.x[i].norm(); if t > amax { amax = t; ipiv = i; } }
            else { ui.push(pinv[i]); ux.push(w.x[i]); }
        }
        if ipiv == usize::MAX || amax <= 0.0 { return Err(format!("singular at column {}", k)); }
        if pinv[k] == usize::MAX && w.x[k].norm() >= amax * 1.0 { ipiv = k; }
        let pivot = w.x[ipiv];
        ui.push(k); ux.push(pivot);
        pinv[ipiv] = k; li.push(ipiv); lx.push(C64::new(1.0, 0.0));
        for p in top..n { let i = w.xi[p]; if pinv[i] == usize::MAX { li.push(i); lx.push(cdiv(w.x[i], pivot)); } w.x[i] = C64::new(0.0, 0.0); }
    }
    lp[n] = li.len(); up[n] = ui.len();
    for v in li.iter_mut() { *v = pinv[*v]; }
    Ok(Lu { n, lp, li, lx, up, ui, ux, pinv })
}

impl Lu {
    /// Solve (L U) x = P b ; returns x (original column numbering; no column permutation used).
    pub fn solve(&self, b: &[C64]) -> Vec<C64> {
        let n = self.n; let mut x = vec![C64::new(0.0, 0.0); n];
        for i in 0..n { x[self.pinv[i]] = b[i]; }
        for j in 0..n { let xj = cdiv(x[j], self.lx[self.lp[j]]); x[j] = xj; for p in self.lp[j] + 1..self.lp[j + 1] { let i = self.li[p]; x[i] -= self.lx[p] * xj; } }
        for j in (0..n).rev() { let xj = cdiv(x[j], self.ux[self.up[j + 1] - 1]); x[j] = xj; for p in self.up[j]..self.up[j + 1] - 1 { let i = self.ui[p]; x[i] -= self.ux[p] * xj; } }
        x
    }
    pub fn nnz(&self) -> (usize, usize) { (self.li.len(), self.ui.len()) }
}
