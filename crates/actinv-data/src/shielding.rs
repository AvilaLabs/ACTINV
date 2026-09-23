//! Deterministic finite-dilution self-shielding factors from ENDF-6 MF=2 LRU=2
//! unresolved-resonance statistics.
//!
//! This module is a deterministic port of the NJOY2016 PURR pipeline
//! (`purr.f90`): `rdf2un`'s energy-node construction, `rdf3un`'s smooth
//! backgrounds, `unresx`'s ladder parameters and analytic infinite-dilution
//! cross sections, `ladr2`'s resonance ladders, and `unrest`'s zoned Voigt
//! accumulation with probability-table moments. Every Monte-Carlo draw is
//! replaced by a fixed stratified-quantile stream `((off + j*STEP) mod N +
//! 1/2)/N` — a fixed permutation of the N-point quantile grid, so identical
//! inputs produce byte-identical tables and no RNG state exists anywhere.
//!
//! Scope follows the P19 protocol: unresolved-range Bondarenko factors for
//! total/elastic/fission/capture on the declared sigma0 x temperature grid.

// The numeric kernels below are line-faithful ports of purr.f90: index loops,
// the published constant tables, and the (ladder, sigma0, temperature)
// argument order are kept explicit so the translation stays auditable.
#![allow(clippy::needless_range_loop)]
#![allow(clippy::too_many_arguments)]
#![allow(clippy::type_complexity)]
#![allow(clippy::approx_constant)]

use crate::resonance::{
    self, RangeData, ResonanceEvaluation, ResonanceRange, UnresolvedCase, UnresolvedPoint,
    UnresolvedSequence,
};
use std::collections::BTreeSet;

const PI: f64 = std::f64::consts::PI;
const EV_ERG: f64 = 1.602_176_634e-12; // erg per eV (exact, SI 2019)
const CLIGHT: f64 = 2.997_924_58e10; // cm/s
const AMASSN_AMU: f64 = 1.008_664_915_95; // neutron mass in amu
const AMU_G: f64 = 931.494_102_42e6 * EV_ERG / (CLIGHT * CLIGHT); // g per amu
const HBAR_ERG_S: f64 = 6.582_119_569e-16 * EV_ERG; // erg s
/// PURR's parameter-extraction temperature (K).
pub const TREF_K: f64 = 300.0;
const CON1: f64 = 2901.34; // unresx Doppler-width constant
const SIGMIN: f64 = 1.0e-5; // rdf3un competition threshold (barns)

/// PURR wave number: k = CWAVEN * (A/(A+1)) * sqrt(E[eV]); k carries the 1e-12
/// meter-to-barn scale inside the 4*pi/k^2 prefactors exactly as PURR computes.
fn cwaven() -> f64 {
    (2.0 * AMASSN_AMU * AMU_G * EV_ERG).sqrt() * 1.0e-12 / HBAR_ERG_S
}

/// PURR's egridu unresolved-node densification grid (78 points).
const EGRIDU: [f64; 78] = [
    1.0e1, 1.25e1, 1.5e1, 1.7e1, 2.0e1, 2.5e1, 3.0e1, 3.5e1, 4.0e1, 5.0e1, 6.0e1, 7.2e1, 8.5e1,
    1.0e2, 1.25e2, 1.5e2, 1.7e2, 2.0e2, 2.5e2, 3.0e2, 3.5e2, 4.0e2, 5.0e2, 6.0e2, 7.2e2, 8.5e2,
    1.0e3, 1.25e3, 1.5e3, 1.7e3, 2.0e3, 2.5e3, 3.0e3, 3.5e3, 4.0e3, 5.0e3, 6.0e3, 7.2e3, 8.5e3,
    1.0e4, 1.25e4, 1.5e4, 1.7e4, 2.0e4, 2.5e4, 3.0e4, 3.5e4, 4.0e4, 5.0e4, 6.0e4, 7.2e4, 8.5e4,
    1.0e5, 1.25e5, 1.5e5, 1.7e5, 2.0e5, 2.5e5, 3.0e5, 3.5e5, 4.0e5, 5.0e5, 6.0e5, 7.2e5, 8.5e5,
    1.0e6, 1.25e6, 1.5e6, 1.7e6, 2.0e6, 2.5e6, 3.0e6, 3.5e6, 4.0e6, 5.0e6, 6.0e6, 7.2e6, 8.5e6,
];

/// ladr2's discrete chi-square quantile table, transcribed row-major:
/// `CHISQ[n][dof]` for draw bin n in 0..20 and degrees of freedom dof in 0..4.
const CHISQ: [[f64; 4]; 20] = [
    [1.31003e-3, 0.0508548, 0.206832, 0.459462],
    [9.19501e-3, 0.156167, 0.470719, 0.893735],
    [0.0250905, 0.267335, 0.691933, 1.21753],
    [0.049254, 0.38505, 0.901674, 1.50872],
    [0.0820892, 0.510131, 1.10868, 1.78605],
    [0.124169, 0.643564, 1.31765, 2.05854],
    [0.176268, 0.786543, 1.53193, 2.33194],
    [0.239417, 0.940541, 1.75444, 2.61069],
    [0.314977, 1.1074, 1.98812, 2.89878],
    [0.404749, 1.28947, 2.23621, 3.20032],
    [0.511145, 1.48981, 2.50257, 3.51995],
    [0.637461, 1.71249, 2.79213, 3.86331],
    [0.788315, 1.96314, 3.11143, 4.23776],
    [0.970419, 2.24984, 3.46967, 4.65345],
    [1.194, 2.58473, 3.88053, 5.12533],
    [1.47573, 2.98744, 4.36586, 5.67712],
    [1.84547, 3.49278, 4.96417, 6.35044],
    [2.36522, 4.17238, 5.75423, 7.22996],
    [3.20371, 5.21888, 6.94646, 8.541],
    [5.58201, 7.99146, 10.0048, 11.8359],
];

/// gnrx's MC2-2 quadrature weights (Fortran column-major transcribed to
/// `[point][dof]` with dof in 0..4).
const QW: [[f64; 4]; 10] = [
    [0.11120413, 0.033773418, 3.3376214e-4, 1.7623788e-3],
    [0.23546798, 0.079932171, 0.018506108, 0.021517749],
    [0.28440987, 0.12835937, 0.12309946, 0.080979849],
    [0.22419127, 0.17652616, 0.29918923, 0.18797998],
    [0.10967668, 0.21347043, 0.33431475, 0.30156335],
    [0.030493789, 0.21154965, 0.17766657, 0.29616091],
    [0.0042930874, 0.13365186, 0.042695894, 0.10775649],
    [2.5827047e-4, 0.022630659, 4.0760575e-3, 2.5171914e-3],
    [4.9031965e-6, 1.6313638e-5, 1.1766115e-4, 8.9630388e-10],
    [1.4079206e-8, 2.745383e-31, 5.0989546e-7, 0.0],
];

/// gnrx's MC2-2 quadrature points (same layout as [`QW`]).
const QP: [[f64; 4]; 10] = [
    [3.0013465e-3, 1.3219203e-2, 1.0004488e-3, 0.013219203],
    [7.8592886e-2, 7.2349624e-2, 0.026197629, 0.072349624],
    [0.43282415, 0.19089473, 0.14427472, 0.19089473],
    [1.3345267, 0.39528842, 0.44484223, 0.39528842],
    [3.0481846, 0.74083443, 1.0160615, 0.74083443],
    [5.8263198, 1.3498293, 1.9421066, 1.3498293],
    [9.9452656, 2.5297983, 3.3150885, 2.5297983],
    [15.782128, 5.2384894, 5.2607092, 5.2384894],
    [23.996824, 13.821772, 7.9989414, 13.821772],
    [36.216208, 75.647525, 12.072069, 75.647525],
];

// ---------------------------------------------------------------------------
// Deterministic stratified-quantile draw stream (replaces PURR's rann).
//
// Draw j of a stream returns ((offset + j*STEP) mod N + 1/2)/N with odd STEP:
// a fixed permutation of the N-point stratified quantile grid — every quantile
// is consumed exactly once per N draws, in a deterministic stream order. The
// declared N, STEP and offset rule are recorded in the emitted table's method
// block; there is no RNG state anywhere in the build.
// ---------------------------------------------------------------------------

/// Declared stratified-grid size for ladder draw streams.
pub const NSTRAT: usize = 8192;
/// Permutation step; odd, hence coprime with NSTRAT, and chosen as
/// `round(NSTRAT/phi^2)` (the golden-ratio conjugate) so each stream's
/// quantile walk is low-discrepancy — steps near N/2 or N/3 collapse
/// consecutive draws onto degenerate residues.
pub const STRAT_STEP: usize = 3127;
/// Ladder count per energy node (PURR's nladr convention).
pub const NLADR: usize = 64;
/// Observation-grid points over the estimator window (PURR's nsamp analogue).
pub const NGRID: usize = 8192;
/// Probability-table bins (the FENDL deck value).
pub const NBIN: usize = 20;
/// Maximum resonances per ladder (PURR's nermax).
pub const NERMAX: usize = 1000;

struct Stratified {
    state: usize,
    offset: usize,
}

impl Stratified {
    /// One stream per (ladder, sequence, draw purpose). The purpose lane
    /// decorrelates consecutive draws: a resonance's position and width
    /// quantiles must sample the product distribution, not a 1D lattice —
    /// a single interleaved stream locks draw j and j+1 to a fixed u-offset.
    fn new(ladder: usize, sequence: usize, purpose: usize) -> Self {
        let offset = (ladder * 4096 + sequence * 977 + purpose * 311) % NSTRAT;
        Self { state: 0, offset }
    }
    fn draw(&mut self) -> f64 {
        let j = self.state;
        self.state += 1;
        ((self.offset + j * STRAT_STEP) % NSTRAT) as f64 / NSTRAT as f64 + 0.5 / NSTRAT as f64
    }
}

/// PURR's `sigfig`: round to `ndig` significant figures, then shade by `idig`
/// last-place units, times a fixed bias.
fn sigfig(x: f64, ndig: i32, idig: i32) -> f64 {
    const BIAS: f64 = 1.000_000_000_000_1;
    if x == 0.0 {
        return 0.0;
    }
    let aa = x.abs().log10();
    let mut ipwr = aa as i32;
    if aa < 0.0 {
        ipwr -= 1;
    }
    ipwr = ndig - 1 - ipwr;
    let ten = 10.0f64;
    let mut ii = (x * ten.powi(ipwr) + ten.powi(ndig - 11)).round() as i64;
    if ii >= 10i64.pow(ndig as u32) {
        ii /= 10;
        ipwr -= 1;
    }
    ii += idig as i64;
    ii as f64 * ten.powi(-ipwr) * BIAS
}

// ---------------------------------------------------------------------------
// uw2: complex probability integral w(z), faithful port of PURR's evaluator.
// Returns (Re w, Im w) for z = rez + i*aim1.
// ---------------------------------------------------------------------------

fn uw2(rez: f64, aim1: f64) -> (f64, f64) {
    const C1: f64 = 1.1283792;
    const C2: f64 = 1.5;
    const UP: f64 = 1.0e15;
    const DN: f64 = 1.0e-15;
    const BRK1: f64 = 1.25;
    const BRK2: f64 = 5.0;
    const BRK3: f64 = 1.863636;
    const BRK4: f64 = 4.1;
    const BRK5: f64 = 1.71;
    const BRK6: f64 = 2.89;
    const BRK7: f64 = 1.18;
    const BRK8: f64 = 5.76;
    const BRK9: f64 = 1.5;
    const EPS: f64 = 1.0e-7;

    let rpi = PI.sqrt();
    let mut aimz = aim1.abs();
    let abrez = rez.abs();
    if abrez + aimz == 0.0 {
        return (1.0, 0.0);
    }
    let r2 = rez * rez;
    let ai2 = aimz * aimz;

    // Fortran region selection: reach label 350 (kw=1 path) when
    //   c1 OR c2 OR (r2+brk5*ai2 >= brk6 AND r2+brk7*ai2 < brk8 AND aimz >= brk9)
    // and kw stays 1 only for aim1 >= 0; otherwise the Taylor branch runs.
    let reach350 = abrez + BRK1 * aimz - BRK2 > 0.0
        || abrez + BRK3 * aimz - BRK4 > 0.0
        || (r2 + BRK5 * ai2 - BRK6 >= 0.0 && r2 + BRK7 * ai2 - BRK8 < 0.0 && aimz - BRK9 >= 0.0);
    let kw_asymptotic = reach350 && aim1 >= 0.0;
    if !kw_asymptotic {
        aimz = aim1;
    }

    let mut rew;
    let mut aimw;
    if kw_asymptotic {
        // Asymptotic continued fraction (Fortran labels 370/380/390).
        let rv = 2.0 * (r2 - ai2);
        let ak = 4.0 * rez * aimz;
        let mut c = -C1 * aimz;
        let mut d = C1 * rez;
        let mut a = 0.0;
        let mut b = 0.0;
        let mut am = rv - 1.0;
        let mut el = ak;
        let mut g = 1.0;
        let mut h = 0.0;
        let mut aak = 1.0;
        let mut iterations = 0usize;
        rew = 0.0;
        aimw = 0.0;
        loop {
            iterations += 1;
            assert!(
                iterations < 100_000,
                "uw2 asymptotic branch failed to converge"
            );
            // label 380: coefficients for this level
            let ajtemp = 2.0 * aak;
            let temp4 = (1.0 - ajtemp) * ajtemp;
            let ajp = rv - (4.0 * aak + 1.0);
            // label 480: shared recurrence
            let mut tempc = ajp * c + temp4 * a - ak * d;
            let mut tempd = ajp * d + temp4 * b + ak * c;
            let mut temel = ajp * el + temp4 * h + ak * am;
            let mut tempm = ajp * am + temp4 * g - ak * el;
            a = c;
            b = d;
            g = am;
            h = el;
            c = tempc;
            d = tempd;
            am = tempm;
            el = temel;
            let mag = tempm.abs() + temel.abs();
            if mag >= UP {
                for v in [
                    &mut c, &mut d, &mut am, &mut el, &mut tempc, &mut tempd, &mut tempm,
                    &mut temel,
                ] {
                    *v *= DN;
                }
            } else if mag <= DN {
                for v in [
                    &mut c, &mut d, &mut am, &mut el, &mut tempc, &mut tempd, &mut tempm,
                    &mut temel,
                ] {
                    *v *= UP;
                }
            }
            // label 390: next level + convergence
            aak += 1.0;
            let pr = rew;
            let pim = aimw;
            let amagn = tempm * tempm + temel * temel;
            rew = (tempc * tempm + tempd * temel) / amagn;
            aimw = (tempm * tempd - temel * tempc) / amagn;
            if (rew - pr).abs() < EPS {
                if rez == 0.0 {
                    return (rew, 0.0);
                }
                if (aimw - pim).abs() < EPS {
                    return (rew, aimw);
                }
            }
        }
    } else {
        // Taylor series branch (Fortran labels 420/430/440/470).
        let temp1 = r2 + ai2;
        let temp2 = 2.0 * temp1 * temp1;
        let aj = -(r2 - ai2) / temp2;
        let ak = 2.0 * rez * aimz / temp2;
        let mut c = 0.0;
        let mut b = 0.0;
        let mut d = 0.0;
        let mut a = 1.0;
        let mut g = 0.0;
        let mut h = 0.0;
        let mut am = 1.0;
        let mut el = 0.0;
        let mut ajsig = 0.0;
        let sigp = C2;
        let expon = (temp2 * aj).exp();
        let expc = expon * (temp2 * ak).cos();
        let exps = -expon * (temp2 * ak).sin();
        let mut sig2p = 2.0 * sigp;
        let mut iterations = 0usize;
        rew = 0.0;
        aimw = 0.0;
        loop {
            iterations += 1;
            assert!(iterations < 100_000, "uw2 Taylor branch failed to converge");
            // label 430: coefficients for this term
            let aj4sig = 4.0 * ajsig;
            let aj4sm1 = aj4sig - 1.0;
            let temp3 = 1.0 / (aj4sm1 * (aj4sig + 3.0));
            let tt4 = sig2p * (2.0 * ajsig - 1.0);
            let temp4 = tt4 / (aj4sm1 * (aj4sig + 1.0) * (aj4sig - 3.0) * aj4sm1);
            let ajp = aj + temp3;
            // label 480: shared recurrence
            let mut tempc = ajp * c + temp4 * a - ak * d;
            let mut tempd = ajp * d + temp4 * b + ak * c;
            let mut temel = ajp * el + temp4 * h + ak * am;
            let mut tempm = ajp * am + temp4 * g - ak * el;
            a = c;
            b = d;
            g = am;
            h = el;
            c = tempc;
            d = tempd;
            am = tempm;
            el = temel;
            let mag = tempm.abs() + temel.abs();
            if mag >= UP {
                for v in [
                    &mut c, &mut d, &mut am, &mut el, &mut tempc, &mut tempd, &mut tempm,
                    &mut temel,
                ] {
                    *v *= DN;
                }
            } else if mag <= DN {
                for v in [
                    &mut c, &mut d, &mut am, &mut el, &mut tempc, &mut tempd, &mut tempm,
                    &mut temel,
                ] {
                    *v *= UP;
                }
            }
            // label 440: term contribution + convergence
            ajsig += 1.0;
            let temp7 = rpi * (am * am + el * el);
            let refr = (aimz * (c * am + d * el) - rez * (am * d - c * el)) / temp7 / temp1;
            let aimf = (aimz * (am * d - c * el) + rez * (c * am + d * el)) / temp7 / temp1;
            let pr = rew;
            let pim = aimw;
            rew = expc - refr;
            aimw = exps - aimf;
            if (rew - pr).abs() < EPS {
                if rez == 0.0 {
                    return (rew, 0.0);
                }
                if (aimw - pim).abs() < EPS {
                    return (rew, aimw);
                }
            }
            // label 470
            sig2p = 2.0 * ajsig;
        }
    }
}

/// PURR's Voigt lookup tables (`uwtab2`): `tr`/`ti` on the coarse 0.1 y-grid
/// covering y >= 0.5 and `trs`/`tis` on the fine 0.02 y-grid for y < 0.5.
/// Fortran `(i, j)` indices map to `[i-1][j-1]` here.
struct VoigtTables {
    tr: [[f64; 27]; 41],
    ti: [[f64; 27]; 41],
    trs: [[f64; 27]; 41],
    tis: [[f64; 27]; 41],
}

fn build_voigt_tables() -> VoigtTables {
    let mut tables = VoigtTables {
        tr: [[0.0; 27]; 41],
        ti: [[0.0; 27]; 41],
        trs: [[0.0; 27]; 41],
        tis: [[0.0; 27]; 41],
    };
    let mut ax = [0.0f64; 41];
    ax[0] = -0.1;
    ax[1] = 0.0;
    for i in 2..41 {
        ax[i] = ax[i - 1] + 0.1;
    }
    let mut ay = [0.0f64; 27];
    ay[0] = 0.4;
    ay[1] = 0.5;
    for j in 2..27 {
        ay[j] = ay[j - 1] + 0.1;
    }
    for i in 1..41 {
        for j in 0..27 {
            let (rew, aimw) = uw2(ax[i], ay[j]);
            tables.tr[i][j] = rew;
            tables.ti[i][j] = aimw;
        }
    }
    for j in 0..27 {
        tables.tr[0][j] = tables.tr[2][j];
        tables.ti[0][j] = -tables.ti[2][j];
        tables.ti[1][j] = 0.0;
    }
    let mut ays = [0.0f64; 27];
    ays[0] = -0.02;
    ays[1] = 0.0;
    for j in 2..27 {
        ays[j] = ays[j - 1] + 0.02;
    }
    for i in 1..41 {
        for j in 0..27 {
            let (rew, aimw) = uw2(ax[i], ays[j]);
            tables.trs[i][j] = rew;
            tables.tis[i][j] = aimw;
        }
    }
    for j in 0..27 {
        tables.trs[0][j] = tables.trs[2][j];
        tables.tis[0][j] = -tables.tis[2][j];
        tables.tis[1][j] = 0.0;
    }
    tables
}

// ---------------------------------------------------------------------------
// gnrx: MC2-2 ten-point fluctuation integrals (faithful port).
//   id=1 elastic (x_j^2 numerator), id=2 capture (x_j), id=3 fission (x_j*x_k).
//   mu -> neutron dof, nu -> fission dof, lamda -> competitive dof.
// ---------------------------------------------------------------------------

fn gnrx(
    galpha: f64,
    gbeta: f64,
    gamma: f64,
    mu: i32,
    nu: i32,
    lamda: i32,
    df: f64,
    id: i32,
) -> f64 {
    if galpha <= 0.0 || gamma <= 0.0 || gbeta < 0.0 || (gbeta <= 0.0 && df < 0.0) {
        return 0.0;
    }
    let col = |dof: i32| -> usize { (dof.clamp(1, 4) - 1) as usize };
    let mut s = 0.0;
    if gbeta <= 0.0 {
        if df <= 0.0 {
            for j in 0..10 {
                let xj = QP[j][col(mu)];
                let wj = QW[j][col(mu)];
                match id {
                    1 => s += wj * xj * xj / (galpha * xj + gamma),
                    2 => s += wj * xj / (galpha * xj + gamma),
                    _ => {}
                }
            }
        } else {
            for j in 0..10 {
                let xj = QP[j][col(mu)];
                let wj = QW[j][col(mu)];
                for k in 0..10 {
                    let xk = QP[k][col(lamda)];
                    let wk = QW[k][col(lamda)];
                    match id {
                        1 => s += wj * wk * xj * xj / (galpha * xj + gamma + df * xk),
                        2 => s += wj * wk * xj / (galpha * xj + gamma + df * xk),
                        _ => {}
                    }
                }
            }
        }
    } else if df <= 0.0 {
        if df >= 0.0 {
            for j in 0..10 {
                let xj = QP[j][col(mu)];
                let wj = QW[j][col(mu)];
                for k in 0..10 {
                    let xk = QP[k][col(nu)];
                    let wk = QW[k][col(nu)];
                    match id {
                        1 => s += wj * wk * xj * xj / (galpha * xj + gbeta * xk + gamma),
                        2 => s += wj * wk * xj / (galpha * xj + gbeta * xk + gamma),
                        3 => s += wj * wk * xj * xk / (galpha * xj + gbeta * xk + gamma),
                        _ => {}
                    }
                }
            }
        }
    } else {
        for j in 0..10 {
            let xj = QP[j][col(mu)];
            let wj = QW[j][col(mu)];
            for k in 0..10 {
                let xk = QP[k][col(nu)];
                let wk = QW[k][col(nu)];
                for l in 0..10 {
                    let xl = QP[l][col(lamda)];
                    let wl = QW[l][col(lamda)];
                    match id {
                        1 => {
                            s += wj * wk * wl * xj * xj
                                / (galpha * xj + gbeta * xk + gamma + df * xl)
                        }
                        2 => s += wj * wk * wl * xj / (galpha * xj + gbeta * xk + gamma + df * xl),
                        3 => {
                            s += wj * wk * wl * xj * xk
                                / (galpha * xj + gbeta * xk + gamma + df * xl)
                        }
                        _ => {}
                    }
                }
            }
        }
    }
    s
}

/// PURR's `unfac2`: penetrability factor `v_l = nu * P_l/rho` (PURR's sign
/// convention folds the neutron degrees of freedom in) and the hard-sphere
/// phase shift.
fn unfac2(l: i32, rho: f64, rhoc: f64, amun: f64) -> (f64, f64) {
    match l {
        0 => (amun, rhoc),
        1 => {
            let r2 = rho * rho;
            (amun * r2 / (1.0 + r2), rhoc - rhoc.atan())
        }
        2 => {
            let r2 = rho * rho;
            let r4 = r2 * r2;
            (
                amun * r4 / (9.0 + 3.0 * r2 + r4),
                rhoc - (3.0 * rhoc).atan2(3.0 - rhoc * rhoc),
            )
        }
        _ => (0.0, rhoc),
    }
}

/// Energy-dependent unresolved parameters at `energy` (PURR `intr2`), tolerant
/// of non-positive table entries: a zero or negative width endpoint makes the
/// log laws undefined; PURR's terp1 returns whatever the floating-point logs
/// produce and the downstream dof/small-width checks absorb it. The
/// deterministic analog is linear interpolation between the clamped values,
/// which is exact for the common case (both endpoints zero, or a dof=0 field
/// that PURR kills with `<1e-8` anyway).
fn unresolved_point_at_shield(
    sequence: &UnresolvedSequence,
    energy: f64,
) -> Result<UnresolvedPoint, String> {
    if sequence.points.is_empty() {
        return Err("unresolved sequence has no parameter points".into());
    }
    if sequence.points[0].energy.is_none() {
        return Ok(sequence.points[0].clone());
    }
    let first = sequence.points[0]
        .energy
        .ok_or("mixed unresolved energies")?;
    let last = sequence.points[sequence.points.len() - 1]
        .energy
        .ok_or("mixed unresolved energies")?;
    if energy <= first {
        return Ok(sequence.points[0].clone());
    }
    if energy >= last {
        return Ok(sequence.points[sequence.points.len() - 1].clone());
    }
    let upper = sequence
        .points
        .partition_point(|p| p.energy.is_some_and(|v| v <= energy));
    let (left, right) = (&sequence.points[upper - 1], &sequence.points[upper]);
    let x1 = left.energy.unwrap();
    let x2 = right.energy.unwrap();
    let law = sequence.interpolation;
    let interp = |y1: f64, y2: f64| -> Result<f64, String> {
        let fraction = (energy - x1) / (x2 - x1);
        Ok(match law {
            1 => y1,
            2 => y1 + fraction * (y2 - y1),
            3 => y1 + (energy / x1).ln() / (x2 / x1).ln() * (y2 - y1),
            4 => {
                if y1 <= 0.0 || y2 <= 0.0 {
                    (y1.max(0.0)) + fraction * (y2.max(0.0) - y1.max(0.0))
                } else {
                    y1 * (y2 / y1).powf(fraction)
                }
            }
            5 => {
                if y1 <= 0.0 || y2 <= 0.0 {
                    (y1.max(0.0)) + fraction * (y2.max(0.0) - y1.max(0.0))
                } else {
                    y1 * (y2 / y1).powf((energy / x1).ln() / (x2 / x1).ln())
                }
            }
            _ => return Err(format!("unsupported unresolved interpolation INT={law}")),
        })
    };
    Ok(UnresolvedPoint {
        energy: Some(energy),
        spacing: interp(left.spacing, right.spacing)?,
        competitive: interp(left.competitive, right.competitive)?,
        neutron: interp(left.neutron, right.neutron)?,
        capture: interp(left.capture, right.capture)?,
        fission: interp(left.fission, right.fission)?,
    })
}

// ---------------------------------------------------------------------------
// Per-(l,J) ladder parameters at one energy: the unresx output record.
// ---------------------------------------------------------------------------

#[derive(Clone, Debug)]
struct SeqParams {
    csz: f64,
    cth: f64,
    cc2p: f64,
    cs2p: f64,
    cgn: f64,
    cgg: f64,
    cgf: f64,
    cgx: f64,
    dbar: f64,
    ndfn: i32,
    ndff: i32,
    ndfx: i32,
}

struct Unresx {
    seqs: Vec<SeqParams>,
    spot: f64,
    /// [total(0, filled later), elastic, fission, capture]
    sigi: [f64; 4],
}

/// One MF=2 LRU=2 range contributing at an energy.
struct UnresolvedSection<'a> {
    abundance: f64,
    naps: i32,
    range: &'a ResonanceRange,
}

fn unresolved_sections(eval: &ResonanceEvaluation) -> Vec<UnresolvedSection<'_>> {
    let mut sections = Vec::new();
    for isotope in &eval.isotopes {
        for range in &isotope.ranges {
            if matches!(range.data, RangeData::Unresolved(_)) {
                sections.push(UnresolvedSection {
                    abundance: isotope.abundance,
                    naps: range.naps,
                    range,
                });
            }
        }
    }
    sections
}

/// `unresx`: ladder parameters, potential scattering and analytic
/// infinite-dilution cross sections at energy `e`, parameters evaluated at
/// PURR's reference temperature.
fn unresx_at(sections: &[UnresolvedSection<'_>], e: f64, t: f64) -> Result<Unresx, String> {
    let mut seqs = Vec::new();
    let mut spot = 0.0;
    let mut sigi = [0.0f64; 4];
    let e2 = e.sqrt();
    for section in sections {
        let range = section.range;
        if !(e >= range.energy_min && e <= range.energy_max) {
            continue;
        }
        let RangeData::Unresolved(unresolved) = &range.data else {
            continue;
        };
        let abn = section.abundance;
        // PURR: when NRO=1 the scattering radius table replaces ay outright;
        // our `scattering_radius` helper does exactly that.
        let ay = resonance::scattering_radius(range, unresolved.ap, e)?;
        let mut seen_l: BTreeSet<i32> = BTreeSet::new();
        for sequence in &unresolved.sequences {
            let awri = sequence.awri;
            if awri <= 0.0 {
                return Err("unresolved sequence has nonpositive AWRI".into());
            }
            let point = unresolved_point_at_shield(sequence, e)?;
            let rat = awri / (awri + 1.0);
            let aw = awri * AMASSN_AMU;
            // Channel radius: NAPS=0 computes it, NAPS=1 uses the (possibly
            // NRO=1-interpolated) scattering radius, NAPS=2&NRO=1 uses the
            // constant CONT value — exactly PURR's arry(inow+1) choice.
            let aa = match section.naps {
                0 => 0.123 * aw.cbrt() + 0.08,
                1 => ay,
                2 if range.scattering_radius.is_some() => unresolved.ap,
                naps => return Err(format!("unsupported unresolved NAPS={naps}")),
            };
            let k = cwaven() * rat * e2;
            let ab = 4.0 * PI / (k * k);
            let rho = k * aa;
            let rhoc = k * ay;
            let amun = f64::from(sequence.neutron_dof);
            let (vl, ps) = unfac2(sequence.l, rho, rhoc, amun);
            let gj = (2.0 * sequence.spin.abs() + 1.0) / (4.0 * unresolved.spin.abs() + 2.0);
            let nu = sequence.neutron_dof;
            let mu = sequence.fission_dof;
            let lu = sequence.competitive_dof;
            let gnx = point.neutron * vl * e2 * amun / nu.max(1) as f64;
            let mut gfx = point.fission;
            let ggx = point.capture;
            let mut gxx = point.competitive;
            if mu <= 0 {
                gfx = 0.0;
            }
            if lu <= 0 {
                gxx = 0.0;
            }
            // PURR's post-interpolation small-width kills.
            if gxx < 1.0e-8 {
                gxx = 0.0;
            }
            if gfx < 1.0e-8 {
                gfx = 0.0;
            }
            if point.spacing <= 0.0 {
                return Err("unresolved sequence has nonpositive spacing".into());
            }
            if seen_l.insert(sequence.l) {
                spot += abn * ab * f64::from(2 * sequence.l + 1) * ps.sin().powi(2);
            }
            let tp = if t == 0.0 { 1.0 } else { t };
            let params = SeqParams {
                csz: abn * ab * gj,
                cth: (CON1 * awri / (e * tp)).sqrt(),
                cc2p: (2.0 * ps).cos(),
                cs2p: (2.0 * ps).sin(),
                cgn: gnx,
                cgg: ggx,
                cgf: gfx,
                cgx: gxx,
                dbar: point.spacing,
                ndfn: nu,
                ndff: mu,
                ndfx: lu,
            };
            let gs = gnrx(
                params.cgn, params.cgf, params.cgg, nu, mu, lu, params.cgx, 1,
            );
            let gc = gnrx(
                params.cgn, params.cgf, params.cgg, nu, mu, lu, params.cgx, 2,
            );
            let gfi = gnrx(
                params.cgn, params.cgf, params.cgg, nu, mu, lu, params.cgx, 3,
            );
            let temp = abn * PI * ab * gj * params.cgn / (2.0 * params.dbar);
            sigi[1] += temp * (gs * params.cgn - 2.0 * ps.sin().powi(2));
            sigi[2] += temp * gfi * params.cgf;
            sigi[3] += temp * gc * params.cgg;
            seqs.push(params);
        }
    }
    Ok(Unresx { seqs, spot, sigi })
}

// ---------------------------------------------------------------------------
// ladr2: one resonance ladder over [elow, ehigh] with stratified draws.
// ---------------------------------------------------------------------------

struct LadderResonance {
    er: f64,
    gnr: f64,
    gfr: f64,
    ggr: f64,
    gt: f64,
}

fn ladr2(
    seq: &SeqParams,
    draws: &mut [Stratified; 4],
    elow: f64,
    ehigh: f64,
    nermax: usize,
) -> Result<Vec<LadderResonance>, String> {
    const START: f64 = 19.9999;
    let dcon = seq.dbar * (4.0 / PI).sqrt();
    let mut resonances: Vec<LadderResonance> = Vec::new();
    let mut er = elow + dcon * draws[0].draw();
    loop {
        if resonances.len() >= nermax {
            return Err("ladr2: too many resonances in ladder".into());
        }
        if !resonances.is_empty() {
            er += dcon * (-draws[0].draw().ln()).sqrt();
        }
        if er > ehigh {
            break;
        }
        let mut gn = seq.cgn;
        let mut gf = seq.cgf;
        let mut gx = seq.cgx;
        let gg = seq.cgg;
        let ndf = seq.ndfn;
        if ndf > 0 {
            gn /= ndf as f64;
        }
        let n = ((1.0 + START * draws[1].draw()) as usize).clamp(1, 20);
        gn *= CHISQ[n - 1][(ndf.clamp(1, 4) - 1) as usize];
        if seq.cgf != 0.0 && seq.ndff != 0 {
            let ndf = seq.ndff;
            if ndf > 0 {
                gf /= ndf as f64;
            }
            let n = ((1.0 + START * draws[2].draw()) as usize).clamp(1, 20);
            gf *= CHISQ[n - 1][(ndf.clamp(1, 4) - 1) as usize];
        }
        if seq.cgx != 0.0 && seq.ndfx != 0 {
            let ndf = seq.ndfx;
            if ndf > 0 {
                gx /= ndf as f64;
            }
            let dn = 20.0 - 20.0 / 10000.0;
            let n = ((1.0 + dn * draws[3].draw()) as usize).clamp(1, 20);
            gx *= CHISQ[n - 1][(ndf.clamp(1, 4) - 1) as usize];
        }
        let gt = gn + gf + gg + gx;
        resonances.push(LadderResonance {
            er,
            gnr: gn / gt,
            gfr: gf / gt,
            ggr: gg / gt,
            gt,
        });
    }
    Ok(resonances)
}

// ---------------------------------------------------------------------------
// unrest: cross-section accumulation on a fixed uniform grid over the
// estimator window, Bondarenko moments, probability table, and the final
// renormalization to the analytic infinite-dilution values (the emitted
// MT=152 convention).
// ---------------------------------------------------------------------------

pub(crate) struct BondarenkoResult {
    /// `sigf[channel][sigma0][temp]`; channels 0..4 = total, elastic, fission,
    /// capture, p1-total.
    pub(crate) sigf: Vec<Vec<Vec<f64>>>,
    /// Ladder-averaged unshielded (total, elastic, fission, capture);
    /// emitted per node as a sampling diagnostic.
    pub(crate) mean_unshielded: [f64; 4],
    /// Percent standard deviation across ladders (same ordering).
    pub(crate) ladder_sigma_percent: [f64; 4],
    pub(crate) nres: usize,
    /// Direct-sampling Bondarenko xs (pre-ptable estimate, same channels).
    pub(crate) sigf_direct: Vec<Vec<Vec<f64>>>,
    /// MT=153-equivalent probability tables per temperature:
    /// `ptable[temp] = (bounds[nbin], [prob, total, elastic, fission, capture][nbin])`.
    pub(crate) ptable: Vec<([f64; 20], [[f64; 20]; 5])>,
}

#[allow(clippy::too_many_arguments)]
fn unrest(
    seqs: &[SeqParams],
    bkg: [f64; 4],
    spot: f64,
    sigi: [f64; 4],
    tables: &VoigtTables,
    sig0: &[f64],
    temps: &[f64],
    nladr: usize,
    ngrid: usize,
    nbin: usize,
    nermax: usize,
) -> Result<BondarenkoResult, String> {
    const CON1R: f64 = 31.83;
    const CON2R: f64 = 20.0;
    const BREAK1: f64 = 0.5;
    const BREAK2: f64 = 3.9;
    const BIG: f64 = 1.0e6;
    const C1: f64 = 0.5641895835;
    const C2R: f64 = 0.2752551;
    const C3: f64 = 2.724745;
    const C4: f64 = 0.5124242;
    const C5: f64 = 0.05176536;
    const D1: f64 = 0.4613135;
    const D2: f64 = 0.1901635;
    const D3: f64 = 0.09999216;
    const D4: f64 = 1.7844927;
    const D5: f64 = 0.002883894;
    const D6: f64 = 5.5253437;
    const NAVOID: usize = 300;

    let nsig0 = sig0.len();
    let ntemp = temps.len();
    let rpi = PI.sqrt();
    let dmin = seqs.iter().map(|s| s.dbar).fold(f64::INFINITY, f64::min);
    let dbarin: f64 = seqs.iter().map(|s| 1.0 / s.dbar).sum();
    if !dmin.is_finite() || dmin <= 0.0 || dbarin <= 0.0 {
        return Err("unrest: no resonance sequences".into());
    }
    let erange = 9.0 * nermax as f64 * dmin / 10.0;
    let nres = (erange * dbarin) as usize;
    let elow = 10.0;
    let ehigh = elow + erange;
    let emin = elow + NAVOID as f64 / dbarin;
    let emax = ehigh - NAVOID as f64 / dbarin;
    let espan = emax - emin;
    if nres == 0 || emax < emin {
        return Err("unrest: bad estimator window (increase dmin)".into());
    }
    let nrest = nres.saturating_sub(2 * NAVOID);

    let mut bval = vec![vec![vec![0.0f64; ntemp]; nsig0]; 7];
    let mut tabl = vec![vec![vec![0.0f64; ntemp]; 5]; nbin];
    let mut tval = vec![vec![0.0f64; ntemp]; nbin];
    let mut tmin = vec![0.0f64; ntemp];
    let mut tmax = vec![0.0f64; ntemp];
    let mut tsum = vec![0.0f64; ntemp];
    let mut bounds_init = vec![false; ntemp];

    let mut tav = 0.0;
    let mut tvar = 0.0;
    let mut eav = 0.0;
    let mut evar = 0.0;
    let mut fav = 0.0;
    let mut fvar = 0.0;
    let mut cav = 0.0;
    let mut cvar = 0.0;

    // Observation grid: PURR draws a fresh random 10000-point grid per ladder;
    // the deterministic port uses a uniform grid whose cell phase rotates per
    // ladder inside the loop below, so the ensemble sees ngrid*nladr distinct
    // sorted positions — the same coverage, declared and reproducible.
    let mut es: Vec<f64> = (0..ngrid)
        .map(|i| emin + espan * (i as f64 + 0.5) / ngrid as f64)
        .collect();
    let mut xs = vec![0.0f64; ngrid];
    let mut cap = vec![vec![0.0f64; ngrid]; ntemp];
    let mut fis = vec![vec![0.0f64; ngrid]; ntemp];
    let mut els = vec![vec![0.0f64; ngrid]; ntemp];

    for iladr in 0..nladr {
        // Rotate the observation grid's cell phase for this ladder.
        let phase = ((iladr * STRAT_STEP) % NSTRAT) as f64 / NSTRAT as f64;
        for (i, v) in es.iter_mut().enumerate() {
            *v = emin + espan * (i as f64 + phase) / ngrid as f64;
        }
        for it in 0..ntemp {
            for ie in 0..ngrid {
                cap[it][ie] = 0.0;
                fis[it][ie] = 0.0;
                els[it][ie] = spot;
            }
        }
        for (k, seq) in seqs.iter().enumerate() {
            let mut draws: [Stratified; 4] = std::array::from_fn(|p| Stratified::new(iladr, k, p));
            let resonances = ladr2(seq, &mut draws, elow, ehigh, nermax)?;
            for (itemp, &temp) in temps.iter().enumerate() {
                let ctx = seq.cth * (TREF_K / temp).sqrt();
                for res in &resonances {
                    let chek1 = CON1R * res.gt;
                    let chek2 = CON2R / ctx;
                    let delr = chek1 + chek1.max(chek2);
                    let elo = res.er - delr;
                    let ehi = res.er + delr;
                    let i0 = es.partition_point(|&v| v < elo);
                    let i7 = es.partition_point(|&v| v <= ehi).saturating_sub(1);
                    if i0 >= ngrid || i7 < i0 || i7 >= ngrid {
                        continue;
                    }
                    let y = ctx * res.gt / 2.0;
                    let yy = y * y;
                    let szy = seq.csz * res.gnr * rpi * y;
                    let cc2 = szy * (seq.cc2p - 1.0 + res.gnr);
                    let cs2 = szy * seq.cs2p;
                    let ccg = szy * res.ggr;
                    let ccf = szy * res.gfr;
                    for ie in i0..=i7 {
                        xs[ie] = ctx * (es[ie] - res.er);
                    }
                    for ie in i0..=i7 {
                        let x = xs[ie];
                        let ax = x.abs();
                        // PURR's piecewise Voigt scheme: asymptotic for
                        // |x|>100 or y>100; rational-1 for |x|>6 or y>6;
                        // rational-2 for |x|>3.9 or y>3; table interpolation
                        // otherwise (trs/tis for y<0.5, tr/ti for y>=0.5).
                        let (rew, aimw) = if y > 100.0 || ax > 100.0 {
                            let test = x * x + yy;
                            let a1 = C1 / test;
                            (y * a1, x * a1)
                        } else if ax > 6.0 || y > 6.0 {
                            let a1 = x * x - yy;
                            let a2 = 2.0 * x * y;
                            let a3 = a2 * a2;
                            let temp1 = a2 * x;
                            let temp2 = a2 * y;
                            let a4 = a1 - C2R;
                            let a5 = a1 - C3;
                            let f1 = C4 / (a4 * a4 + a3);
                            let f2 = C5 / (a5 * a5 + a3);
                            (
                                f1 * (temp1 - a4 * y) + f2 * (temp1 - a5 * y),
                                f1 * (a4 * x + temp2) + f2 * (a5 * x + temp2),
                            )
                        } else if ax > BREAK2 || y > 3.0 {
                            let a1 = x * x - yy;
                            let a2 = 2.0 * x * y;
                            let a3 = a2 * a2;
                            let temp1 = a2 * x;
                            let temp2 = a2 * y;
                            let a4 = a1 - D2;
                            let a5 = a1 - D4;
                            let a6 = a1 - D6;
                            let e1 = D1 / (a4 * a4 + a3);
                            let e2 = D3 / (a5 * a5 + a3);
                            let e3 = D5 / (a6 * a6 + a3);
                            (
                                e1 * (temp1 - a4 * y)
                                    + e2 * (temp1 - a5 * y)
                                    + e3 * (temp1 - a6 * y),
                                e1 * (a4 * x + temp2)
                                    + e2 * (a5 * x + temp2)
                                    + e3 * (a6 * x + temp2),
                            )
                        } else {
                            // Table interpolation into the w tables; Fortran
                            // 1-based (i, j) indices map to [i-1][j-1].
                            let (tr, ti) = if y >= BREAK1 {
                                (&tables.tr, &tables.ti)
                            } else {
                                (&tables.trs, &tables.tis)
                            };
                            let aki = if x < 0.0 { -1.0 } else { 1.0 };
                            let tempor = 10.0 * ax;
                            let ii = tempor as usize;
                            let i = ii + 2; // Fortran i
                            let p = tempor - ii as f64;
                            let p2 = p * p;
                            let hp = 0.5 * p;
                            let hp2 = 0.5 * p2;
                            let a2 = hp2 - hp;
                            let (j, n, q) = if y >= BREAK1 {
                                let tempor = 10.0 * y;
                                let jj = tempor as usize;
                                (jj - 3, jj - 4, tempor - jj as f64)
                            } else {
                                let tempor = 50.0 * y;
                                let jj = tempor as usize;
                                (jj + 2, jj + 1, tempor - jj as f64)
                            };
                            let q2 = q * q;
                            let hq = 0.5 * q;
                            let hq2 = 0.5 * q2;
                            let a1 = hq2 - hq;
                            let pq = p * q;
                            let a3 = 1.0 + pq - p2 - q2;
                            let a4 = hp2 - pq + hp;
                            let a5 = hq2 - pq + hq;
                            // t(fi, fj): Fortran indices; clamp only at the
                            // declared table bounds (PURR reads past them; the
                            // stratified port clamps deterministically).
                            let g = |t: &[[f64; 27]; 41], fi: usize, fj: usize| -> f64 {
                                t[(fi - 1).min(40)][(fj.max(1) - 1).min(26)]
                            };
                            let rew = a1 * g(tr, i, n)
                                + a2 * g(tr, i - 1, j)
                                + a3 * g(tr, i, j)
                                + a4 * g(tr, i + 1, j)
                                + a5 * g(tr, i, j + 1)
                                + pq * g(tr, i + 1, j + 1);
                            let aimw = a1 * g(ti, i, n)
                                + a2 * g(ti, i - 1, j)
                                + a3 * g(ti, i, j)
                                + a4 * g(ti, i + 1, j)
                                + a5 * g(ti, i, j + 1)
                                + pq * g(ti, i + 1, j + 1);
                            (rew, aimw * aki)
                        };
                        cap[itemp][ie] += ccg * rew;
                        fis[itemp][ie] += ccf * rew;
                        els[itemp][ie] += cc2 * rew + cs2 * aimw;
                    }
                }
            }
        }

        // Eliminate negative elastic cross sections.
        for it in 0..ntemp {
            for ie in 0..ngrid {
                if els[it][ie] < -bkg[1] {
                    els[it][ie] = -bkg[1] + 1.0 / BIG;
                }
            }
        }

        // Ladder means at the first temperature (PURR's reporting convention).
        let mut totf = 0.0;
        let mut elsf = 0.0;
        let mut capf = 0.0;
        let mut fisf = 0.0;
        for ie in 0..ngrid {
            totf += els[0][ie] + fis[0][ie] + cap[0][ie] + bkg[0];
            elsf += els[0][ie] + bkg[1];
            capf += cap[0][ie] + bkg[3];
            fisf += fis[0][ie] + bkg[2];
        }
        totf /= ngrid as f64;
        elsf /= ngrid as f64;
        capf /= ngrid as f64;
        fisf /= ngrid as f64;
        tav += totf;
        tvar += totf * totf;
        eav += elsf;
        evar += elsf * elsf;
        fav += fisf;
        fvar += fisf * fisf;
        cav += capf;
        cvar += capf * capf;

        // nmode==1 per-ladder renormalization is dead code in NJOY2016
        // (nmode is hardwired to 0); the emitted path renormalizes below.

        for itemp in 0..ntemp {
            if !bounds_init[itemp] {
                // Probability-table bounds from the first ladder's totals.
                let mut totals: Vec<f64> = (0..ngrid)
                    .map(|ie| els[itemp][ie] + fis[itemp][ie] + cap[itemp][ie] + bkg[0])
                    .collect();
                totals.sort_by(f64::total_cmp);
                tmin[itemp] = totals[0];
                tmax[itemp] = totals[ngrid - 1];
                let nebin = (ngrid as f64 / (nbin as f64 - 10.0 + 1.76)) as usize;
                let mut ibin = (nebin / 200).max(1);
                for i in 1..nbin {
                    if ibin > ngrid {
                        ibin = ngrid;
                    }
                    tval[i - 1][itemp] = totals[ibin.min(ngrid) - 1];
                    if i > 1 && tval[i - 1][itemp] <= tval[i - 2][itemp] {
                        tval[i - 1][itemp] = tval[i - 2][itemp] + tval[i - 2][itemp] / 20.0;
                    }
                    if i == 1 {
                        ibin += nebin / 40;
                    }
                    if i == 2 {
                        ibin += nebin / 10;
                    }
                    if i == 3 {
                        ibin += nebin / 4;
                    }
                    if i == 4 {
                        ibin += nebin / 2;
                    }
                    if i > 4 && i < nbin - 5 {
                        ibin += nebin;
                    }
                    if i == nbin - 5 {
                        ibin += nebin / 2;
                    }
                    if i == nbin - 4 {
                        ibin += nebin / 4;
                    }
                    if i == nbin - 3 {
                        ibin += nebin / 10;
                    }
                    if i == nbin - 2 {
                        ibin += nebin / 40;
                    }
                    if i == nbin - 1 {
                        ibin += nebin / 200;
                    }
                }
                tval[nbin - 1][itemp] = BIG;
                for i in 0..nbin {
                    for j in 0..5 {
                        tabl[i][j][itemp] = 0.0;
                    }
                }
                tsum[itemp] = 0.0;
                bounds_init[itemp] = true;
            }

            let column: Vec<f64> = tval.iter().map(|v| v[itemp]).collect();
            for ie in 0..ngrid {
                let tot = els[itemp][ie] + fis[itemp][ie] + cap[itemp][ie] + bkg[0];
                if tot < tmin[itemp] {
                    tmin[itemp] = tot;
                }
                if tot > tmax[itemp] {
                    tmax[itemp] = tot;
                }
                // PURR: ii = search position, +1 unless exact, clamp >=1.
                let bin = column.partition_point(|&v| v <= tot).min(nbin - 1);
                tsum[itemp] += 1.0;
                tabl[bin][0][itemp] += 1.0;
                tabl[bin][1][itemp] += tot;
                tabl[bin][2][itemp] += els[itemp][ie] + bkg[1];
                tabl[bin][3][itemp] += fis[itemp][ie] + bkg[2];
                tabl[bin][4][itemp] += cap[itemp][ie] + bkg[3];
                for (i, &s0) in sig0.iter().enumerate() {
                    let tem = s0 / (s0 + tot);
                    bval[0][i][itemp] += tot * tem;
                    bval[1][i][itemp] += (els[itemp][ie] + bkg[1]) * tem;
                    bval[2][i][itemp] += (fis[itemp][ie] + bkg[2]) * tem;
                    bval[3][i][itemp] += (cap[itemp][ie] + bkg[3]) * tem;
                    bval[4][i][itemp] += tot * tem * tem;
                    bval[5][i][itemp] += tem;
                    bval[6][i][itemp] += tem * tem;
                }
            }
        }
    }

    // Ladder statistics.
    let n = nladr as f64;
    tav /= n;
    let argt = (tvar / n - tav * tav).max(0.0);
    eav /= n;
    let arge = (evar / n - eav * eav).max(0.0);
    fav /= n;
    let argf = (fvar / n - fav * fav).max(0.0);
    cav /= n;
    let argc = (cvar / n - cav * cav).max(0.0);
    let ladder_sigma = [
        100.0 * argt.sqrt() / tav,
        100.0 * arge.sqrt() / eav,
        if fav != 0.0 {
            100.0 * argf.sqrt() / fav
        } else {
            0.0
        },
        100.0 * argc.sqrt() / cav,
    ];
    let mut sigi_full = sigi;
    sigi_full[0] = sigi[1] + sigi[2] + sigi[3] + spot + bkg[0];
    sigi_full[1] += spot + bkg[1];
    sigi_full[2] += bkg[2];
    sigi_full[3] += bkg[3];

    // Direct-sampling Bondarenko cross sections (first pass). PURR's
    // "direct sampling" table; MT=152 later carries the ptable-derived
    // values after the second pass below overwrites sigf.
    let mut sigf = vec![vec![vec![0.0f64; ntemp]; nsig0]; 5];
    for i in 0..nsig0 {
        for it in 0..ntemp {
            for j in 0..5 {
                let num = bval[j][i][it];
                let den = if j == 4 {
                    bval[6][i][it]
                } else {
                    bval[5][i][it]
                };
                if den != 0.0 {
                    sigf[j][i][it] = num / den;
                }
            }
        }
    }

    // Probability-table normalization.
    for i in 0..nbin {
        for it in 0..ntemp {
            tval[nbin - 1][it] = tmax[it];
            let denom = if tabl[i][0][it] == 0.0 {
                1.0
            } else {
                tabl[i][0][it]
            };
            tabl[i][0][it] /= tsum[it];
            for j in 1..5 {
                tabl[i][j][it] /= denom;
            }
        }
    }

    let mut sigf_direct = sigf.clone();
    // Bondarenko cross sections from the probability table.
    for i in 0..nsig0 {
        for j in 0..7 {
            for it in 0..ntemp {
                bval[j][i][it] = 0.0;
            }
        }
        for it in 0..ntemp {
            for j in 0..nbin {
                if tabl[j][0][it] != 0.0 {
                    let den = sig0[i] / (sig0[i] + tabl[j][1][it]);
                    let ttt = tabl[j][0][it];
                    bval[0][i][it] += ttt * tabl[j][1][it] * den;
                    bval[1][i][it] += ttt * tabl[j][2][it] * den;
                    bval[2][i][it] += ttt * tabl[j][3][it] * den;
                    bval[3][i][it] += ttt * tabl[j][4][it] * den;
                    bval[4][i][it] += ttt * tabl[j][1][it] * den * den;
                    bval[5][i][it] += ttt * den;
                    bval[6][i][it] += ttt * den * den;
                }
            }
            for j in 0..5 {
                let den = if j == 4 {
                    bval[6][i][it]
                } else {
                    bval[5][i][it]
                };
                if den != 0.0 {
                    sigf[j][i][it] = bval[j][i][it] / den;
                }
            }
        }
    }

    // Capture the sampled probability table pre-renormalization: the emitted
    // bins are the raw ladder-sampled sigma_t distribution, so an independent
    // reader can reconstruct the Bondarenko moments and apply the renorm
    // itself (PURR's MT=153 emits the post-renorm table; we record both ends
    // of the convention — sampled bins here, renormalized moments in sigf).
    let mut ptable = Vec::with_capacity(ntemp);
    for it in 0..ntemp {
        let mut bounds = [0.0f64; 20];
        let mut vals = [[0.0f64; 20]; 5];
        for i in 0..nbin {
            bounds[i] = tval[i][it];
            for j in 0..5 {
                vals[j][i] = tabl[i][j][it];
            }
        }
        ptable.push((bounds, vals));
    }

    // Renormalize the probability table and the Bondarenko cross sections to
    // the analytic infinite-dilution values (PURR's emitted MT=152 convention:
    // the sigma0=1e10 column reproduces sigi exactly).
    for it in 0..ntemp {
        for i in 1..5 {
            for j in 0..nbin {
                if sigf[i - 1][0][it] != 0.0 {
                    tabl[j][i][it] *= sigi_full[i - 1] / sigf[i - 1][0][it];
                }
            }
        }
    }
    for it in 0..ntemp {
        for i in 0..nsig0 {
            for j in 0..5 {
                let k = if j == 4 { 0 } else { j };
                if sigf_direct[j][0][it] != 0.0 {
                    sigf_direct[j][i][it] *= sigi_full[k] / sigf_direct[j][0][it];
                }
            }
        }
    }
    for it in 0..ntemp {
        for i in (0..nsig0).rev() {
            for j in 0..5 {
                let k = if j == 4 { 0 } else { j };
                if sigf[j][0][it] != 0.0 {
                    sigf[j][i][it] *= sigi_full[k] / sigf[j][0][it];
                }
            }
        }
    }

    Ok(BondarenkoResult {
        sigf,
        sigf_direct,
        mean_unshielded: [tav, eav, fav, cav],
        ladder_sigma_percent: ladder_sigma,
        nres: nrest,
        ptable,
    })
}

// ---------------------------------------------------------------------------
// rdf2un's energy-node construction (PURR eunr list): shaded range edges,
// first-(l,j) interior parameter energies, egridu densification, and resolved/
// unresolved overlap marking. Returns nodes with the same meaning as PURR's
// eunr: negative values mark out-of-unresolved-range overlap nodes; callers use
// abs(node) for evaluation.
// ---------------------------------------------------------------------------

const ETOP: f64 = 5.0e6;
const WIDE: f64 = 1.26;

fn ilist2(list: &mut Vec<f64>, e: f64) {
    match list.binary_search_by(|v| v.total_cmp(&e)) {
        Ok(_) => {}
        Err(pos) => list.insert(pos, e),
    }
}

/// Build PURR's eunr node list for an evaluation. Returns `(node, covered)`:
/// `covered` is false for overlap-marked nodes (negative in PURR).
pub(crate) fn eunr_nodes(eval: &ResonanceEvaluation) -> Vec<(f64, bool)> {
    let mut eunr: Vec<f64> = vec![ETOP];
    let mut indep = false;
    let mut elr = 0.0f64;
    let mut ehr = ETOP;
    for isotope in &eval.isotopes {
        for range in &isotope.ranges {
            let el = sigfig(range.energy_min, 7, 0);
            let eh = sigfig(range.energy_max, 7, 0);
            match &range.data {
                RangeData::Unresolved(unresolved) => {
                    if eh < ehr {
                        ehr = eh;
                    }
                    ilist2(&mut eunr, sigfig(el, 7, -1));
                    ilist2(&mut eunr, sigfig(el, 7, 1));
                    ilist2(&mut eunr, sigfig(eh, 7, -1));
                    ilist2(&mut eunr, sigfig(eh, 7, 1));
                    match unresolved.case {
                        UnresolvedCase::A => indep = true,
                        UnresolvedCase::B | UnresolvedCase::C => {
                            // Interior parameter energies of the first
                            // (l, j) sequence become nodes.
                            if let Some(first) = unresolved.sequences.first() {
                                for (k, point) in first.points.iter().enumerate() {
                                    if k == 0 || k + 1 == first.points.len() {
                                        continue;
                                    }
                                    if let Some(enow) = point.energy {
                                        if enow >= el && enow <= eh {
                                            ilist2(&mut eunr, enow);
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
                _ => {
                    if eh > elr {
                        elr = eh;
                    }
                }
            }
        }
    }

    // Densification pass.
    let mut i = 1usize;
    if eunr.len() < 3 {
        return Vec::new();
    }
    let mut elast = eunr[1];
    loop {
        i += 1;
        if i >= eunr.len() {
            break;
        }
        let enext = eunr[i];
        if enext >= ETOP {
            break;
        }
        if enext >= WIDE * elast || indep {
            let mut et = elast;
            loop {
                let enut = EGRIDU
                    .iter()
                    .copied()
                    .find(|&g| g > et + et / 100.0)
                    .unwrap_or(enext);
                et = enut;
                if et >= enext {
                    break;
                }
                eunr.insert(i, et);
                i += 1;
            }
        }
        elast = eunr[i];
    }

    // Mark out-of-range nodes (PURR stores them negative) and drop the lowest
    // edge node, the highest edge node, and the etop sentinel.
    let mut out = Vec::with_capacity(eunr.len().saturating_sub(3));
    for k in 1..eunr.len().saturating_sub(2) {
        let et = eunr[k];
        let covered = et >= elr && et <= ehr;
        out.push((et, covered));
    }
    out
}

// ---------------------------------------------------------------------------
// rdf3un's smooth backgrounds: MF=3 MT 1/2/18/102 evaluated at the eunr nodes
// (first/last nodes nudged inside), with the LSSF=1 rule that replaces the
// partial-channel backgrounds by the competitive residual.
// ---------------------------------------------------------------------------

/// Evaluate one MF=3 section at `e`, or zero when absent.
fn mf3_at(eval: &crate::activation::Evaluation, mt: i32, e: f64) -> Result<f64, String> {
    match eval.mf3.get(&mt) {
        Some(table) => table.evaluate(e),
        None => Ok(0.0),
    }
}

/// `rdf3un` port: per-node backgrounds [total, elastic, fission, capture].
/// `lssf_one` is the last unresolved section's LSSF flag (PURR's module-global
/// semantics). Returns an error on malformed structure; degenerate competition
/// bookkeeping follows PURR's message-then-zero rule.
pub(crate) fn unresolved_backgrounds(
    eval: &crate::activation::Evaluation,
    nodes: &[(f64, bool)],
    lssf_one: bool,
) -> Result<Vec<[f64; 4]>, String> {
    let nunr = nodes.len();
    let mut sb = vec![[0.0f64; 4]; nunr];
    for (ie, &(node, _)) in nodes.iter().enumerate() {
        let mut e = node.abs();
        if ie == 0 {
            e *= 1.00001;
        }
        if ie == nunr - 1 {
            e *= 0.99999;
        }
        sb[ie][0] = mf3_at(eval, 1, e)?;
        sb[ie][1] = mf3_at(eval, 2, e)?;
        sb[ie][2] = mf3_at(eval, 18, e)?;
        sb[ie][3] = mf3_at(eval, 102, e)?;
    }
    if !lssf_one {
        return Ok(sb);
    }
    // LSSF=1: MF=3 already contains the unresolved averages, so the "background"
    // is only the competitive residual total - elastic - fission - capture.
    // Detect the competition onset exactly as PURR does: the first node where
    // the residual exceeds `small`, then require a competitive-reaction
    // threshold below the last node.
    let mut icx = 0usize;
    for (ie, row) in sb.iter().enumerate() {
        let residual = row[0] - row[1] - row[2] - row[3];
        if icx == 0 && residual > SIGMIN {
            icx = ie;
        }
    }
    let ecomp = if icx > 0 {
        nodes[icx].0.abs()
    } else {
        f64::INFINITY
    };
    // A competitive reaction exists when some MF=3 section (MT>4 excluding
    // fission/capture) has a threshold below the last node.
    let elast_node = nodes.last().map(|(e, _)| e.abs()).unwrap_or(0.0);
    let mut has_competition = false;
    for (&mt, table) in &eval.mf3 {
        if mt > 4 && mt != 18 && mt != 19 && mt != 102 {
            if let Some(&first_e) = table.x.first() {
                if 1.00001 * first_e < elast_node {
                    has_competition = true;
                }
            }
        }
    }
    for (ie, row) in sb.iter_mut().enumerate() {
        let total = row[0];
        let mut residual = row[0] - row[1] - row[2] - row[3];
        let tol = 1.0e-6 * total;
        if residual > tol {
            if !has_competition || nodes[ie].0.abs() < ecomp {
                residual = 0.0;
            }
        } else {
            residual = 0.0;
        }
        *row = [residual, 0.0, 0.0, 0.0];
    }
    Ok(sb)
}

// ---------------------------------------------------------------------------
// Public driver: per-node Bondarenko factor computation for one evaluation.
// ---------------------------------------------------------------------------

/// Per-node result: PURR MT=152-equivalent Bondarenko cross sections.
#[derive(Clone, Debug)]
pub struct ShieldNode {
    pub energy_ev: f64,
    /// True when the node lies inside a declared unresolved range.
    pub covered: bool,
    /// Potential scattering (barns) at this node.
    pub sigma_p_b: f64,
    /// Analytic infinite-dilution values [total, elastic, fission, capture].
    pub infinite_dilution_b: [f64; 4],
    /// `sigf[channel][sigma0][temp]` — channels total/elastic/fission/capture.
    /// `None` when the node has no unresolved coverage.
    pub sigf: Option<Vec<Vec<Vec<f64>>>>,
    /// Probability tables per temperature: (bin bounds, [prob,σt,σe,σf,σγ]).
    pub ptable: Option<Vec<([f64; 20], [[f64; 20]; 5])>>,
    /// Direct-sampling Bondarenko xs (same layout as `sigf`), for the
    /// ptable-vs-direct diagnostic.
    pub sigf_direct: Option<Vec<Vec<Vec<f64>>>>,
    /// Ladder-averaged unshielded xs (total, elastic, fission, capture).
    pub mean_unshielded: [f64; 4],
    /// Stratified-ensemble percent standard deviation across ladders
    /// (total, elastic, fission, capture).
    pub ladder_sigma_percent: [f64; 4],
    pub ladder_count: usize,
    pub resonances_per_ladder: usize,
}

/// Frozen P19 grids.
pub const SIGMA0_B: [f64; 10] = [
    1.0e10, 1.0e5, 1.0e4, 1.0e3, 1.0e2, 1.0e1, 3.0, 1.0, 0.3, 0.1,
];
pub const TEMPERATURES_K: [f64; 4] = [293.6, 600.0, 900.0, 1200.0];

/// Run the deterministic PURR-equivalent over one parsed evaluation.
/// Returns one [`ShieldNode`] per eunr node (PURR's energy list).
pub(crate) fn shield_evaluation(
    eval: &crate::activation::Evaluation,
    sig0: &[f64],
    temps: &[f64],
    nladr: usize,
) -> Result<Vec<ShieldNode>, String> {
    let Some(resonance) = &eval.resonance else {
        return Ok(Vec::new());
    };
    let sections = unresolved_sections(resonance);
    if sections.is_empty() {
        return Ok(Vec::new());
    }
    let nodes = eunr_nodes(resonance);
    if nodes.is_empty() {
        return Ok(Vec::new());
    }
    // PURR's lssf is the flag of the last unresolved section read.
    let lssf_one = sections
        .last()
        .map(|s| match &s.range.data {
            RangeData::Unresolved(u) => !u.add_to_background,
            _ => false,
        })
        .unwrap_or(false);
    let backgrounds = unresolved_backgrounds(eval, &nodes, lssf_one)?;
    let tables = build_voigt_tables();

    let mut out = Vec::with_capacity(nodes.len());
    for (ie, &(node, covered)) in nodes.iter().enumerate() {
        let e = node.abs();
        let bkg = backgrounds[ie];
        let params = unresx_at(&sections, e, TREF_K)?;
        if params.seqs.is_empty() {
            out.push(ShieldNode {
                energy_ev: e,
                covered,
                sigma_p_b: params.spot,
                infinite_dilution_b: [bkg[0], bkg[1], bkg[2], bkg[3]],
                sigf: None,
                ptable: None,
                sigf_direct: None,
                mean_unshielded: [0.0; 4],
                ladder_sigma_percent: [0.0; 4],
                ladder_count: 0,
                resonances_per_ladder: 0,
            });
            continue;
        }
        let spot = params.spot;
        let sigi = params.sigi;
        let result = unrest(
            &params.seqs,
            bkg,
            spot,
            sigi,
            &tables,
            sig0,
            temps,
            nladr,
            NGRID,
            NBIN,
            NERMAX,
        )?;
        let mut sigi_full = sigi;
        sigi_full[0] = sigi[1] + sigi[2] + sigi[3] + spot + bkg[0];
        sigi_full[1] += spot + bkg[1];
        sigi_full[2] += bkg[2];
        sigi_full[3] += bkg[3];
        out.push(ShieldNode {
            energy_ev: e,
            covered,
            sigma_p_b: spot,
            infinite_dilution_b: sigi_full,
            sigf: Some(result.sigf),
            ptable: Some(result.ptable),
            sigf_direct: Some(result.sigf_direct),
            mean_unshielded: result.mean_unshielded,
            ladder_sigma_percent: result.ladder_sigma_percent,
            ladder_count: nladr,
            resonances_per_ladder: result.nres,
        });
    }
    Ok(out)
}

// ---------------------------------------------------------------------------
// Group collapse: lethargy-weighted trapezoid over nodes inside the group∩range
// segment, matching the library's unresolved-average convention.
// ---------------------------------------------------------------------------

/// One collapsed group entry in the emitted table.
#[derive(Clone, Debug)]
pub struct ShieldGroup {
    pub group: usize,
    /// Fraction of the group's lethargy width covered by unresolved ranges.
    pub overlap_fraction: f64,
    /// Lethargy-collapsed potential scattering (barns).
    pub sigma_p_b: f64,
    /// Lethargy-collapsed infinite-dilution xs [total, elastic, fission, capture].
    pub infinite_dilution_b: [f64; 4],
    /// `factors[channel][sigma0][temp]` = sigma_x(sigma0,T)/sigma_x(inf,T);
    /// channel order total/elastic/fission/capture.
    pub factors: Vec<Vec<Vec<f64>>>,
    /// `shielded_b[channel][sigma0][temp]` = lethargy-collapsed Bondarenko
    /// cross sections over the covered segments (barns).
    pub shielded_b: Vec<Vec<Vec<f64>>>,
    /// Lethargy mean of the smooth MF=3 section over the group's uncovered
    /// part [total, elastic, fission, capture]; zero when fully covered.
    pub background_b: [f64; 4],
    /// `weight_mean[sigma0][temp]` — segment mean of the Bondarenko weight
    /// w = sigma0/(sigma0+sigma_t) from the per-node probability tables.
    pub weight_mean: Vec<Vec<f64>>,
    /// `group_unshielded_b[channel]` = c·seg_inf + (1−c)·bkg — the model's
    /// full-group infinite-dilution cross section.
    pub group_unshielded_b: [f64; 4],
    /// `group_shielded_b[channel][sigma0][temp]` — the full-group Bondarenko
    /// cross section: covered segments carry the pointwise weight, the
    /// uncovered part is suppressed by sigma0/(sigma0 + bkg_total).
    pub group_shielded_b: Vec<Vec<Vec<f64>>>,
    /// `group_factors[channel][sigma0][temp]` = group_shielded/group_unshielded;
    /// 1.0 for channels whose unshielded group value vanishes.
    pub group_factors: Vec<Vec<Vec<f64>>>,
}

fn lethargy_trapezoid(nodes: &[f64], values: &[f64], lo: f64, hi: f64) -> f64 {
    // Integrate value vs u=ln(E) over [lo,hi]; log-linear interpolation at
    // segment edges, trapezoids across interior nodes.
    debug_assert!(lo < hi && !nodes.is_empty());
    let interp = |e: f64| -> f64 {
        if e <= nodes[0] {
            return values[0];
        }
        if e >= *nodes.last().unwrap() {
            return *values.last().unwrap();
        }
        let up = nodes.partition_point(|&v| v <= e);
        let i = up - 1;
        let f = (e.ln() - nodes[i].ln()) / (nodes[i + 1].ln() - nodes[i].ln());
        values[i] + f * (values[i + 1] - values[i])
    };
    let mut xs: Vec<f64> = vec![lo];
    xs.extend(nodes.iter().copied().filter(|&v| v > lo && v < hi));
    xs.push(hi);
    let mut area = 0.0;
    for w in xs.windows(2) {
        let (a, b) = (w[0], w[1]);
        area += 0.5 * (interp(a) + interp(b)) * (b.ln() - a.ln());
    }
    area / (hi.ln() - lo.ln())
}

/// Lethargy mean of one MF=3 section over [a,b]: trapezoid in ln(E) on the
/// union of the interval edges and the table's knots inside it.
fn mf3_lethargy_mean(
    eval: &crate::activation::Evaluation,
    mt: i32,
    a: f64,
    b: f64,
) -> Result<f64, String> {
    if a >= b {
        return Ok(0.0);
    }
    let knots: Vec<f64> = match eval.mf3.get(&mt) {
        Some(t) => {
            let mut v: Vec<f64> = t.x.iter().copied().filter(|&e| e > a && e < b).collect();
            v.insert(0, a);
            v.push(b);
            v
        }
        None => vec![a, b],
    };
    let mut area = 0.0;
    for w in knots.windows(2) {
        let (x0, x1) = (w[0], w[1]);
        let y0 = mf3_at(eval, mt, x0)?;
        let y1 = mf3_at(eval, mt, x1)?;
        area += 0.5 * (y0 + y1) * (x1.ln() - x0.ln());
    }
    Ok(area / (b.ln() - a.ln()))
}

/// Per-node Bondarenko weight and weighted-moment helpers from the emitted
/// probability table (conditional bin means). The table is stored pre-renorm,
/// so each call first recovers PURR's emitted convention: the sigma_t and
/// sigma_x columns are scaled by the ratios that put the sigma0=1e10 column
/// exactly on the analytic infinite-dilution values. Nodes without a table
/// use the smooth mean sigma_t.
/// Returns (w_mean, xw_mean) = (<w>, <sigma_x*w>) at (sigma0, temp t).
fn node_w_and_xw(
    node: &ShieldNode,
    channel: usize,
    sig0: f64,
    sig0_max: f64,
    t: usize,
) -> (f64, f64) {
    let (prob, tot, xs) = match &node.ptable {
        Some(pt) => {
            let (_bounds, vals) = &pt[t];
            (&vals[0][..], &vals[1][..], &vals[channel + 1][..])
        }
        None => {
            let w = sig0 / (sig0 + node.infinite_dilution_b[0]);
            return (w, node.infinite_dilution_b[channel] * w);
        }
    };
    // Renorm ratios at the infinite-dilution end of the grid.
    let mut raw_den = 0.0;
    let mut raw_tot = 0.0;
    let mut raw_xs = 0.0;
    for i in 0..prob.len() {
        let w = sig0_max / (sig0_max + tot[i]);
        raw_den += prob[i] * w;
        raw_tot += prob[i] * tot[i] * w;
        raw_xs += prob[i] * xs[i] * w;
    }
    if raw_den <= 0.0 {
        return (0.0, 0.0);
    }
    let raw_tot_mean = raw_tot / raw_den;
    let raw_xs_mean = raw_xs / raw_den;
    let r_tot = if raw_tot_mean > 0.0 {
        node.infinite_dilution_b[0] / raw_tot_mean
    } else {
        1.0
    };
    let r_xs = if raw_xs_mean > 0.0 {
        node.infinite_dilution_b[channel] / raw_xs_mean
    } else {
        0.0
    };
    let mut w_acc = 0.0;
    let mut xw_acc = 0.0;
    for i in 0..prob.len() {
        let w = sig0 / (sig0 + tot[i] * r_tot);
        w_acc += prob[i] * w;
        xw_acc += prob[i] * xs[i] * r_xs * w;
    }
    (w_acc, xw_acc)
}

/// Collapse per-node results onto a group structure. `GroupStructure` boundaries are
/// ascending (group 0 = lowest energy); the collapse itself is order-agnostic.
pub(crate) fn collapse_to_groups(
    nodes: &[ShieldNode],
    ranges: &[(f64, f64)],
    groups: &crate::groups::GroupStructure,
    sig0: &[f64],
    temps: &[f64],
    eval: &crate::activation::Evaluation,
) -> Result<Vec<ShieldGroup>, String> {
    let bounds = &groups.boundaries_ev;
    let n_groups = bounds.len().saturating_sub(1);
    let mut out = Vec::new();
    for g in 0..n_groups {
        let (hi, lo) = (bounds[g].max(bounds[g + 1]), bounds[g].min(bounds[g + 1]));
        // Segment = group ∩ union of unresolved ranges.
        let mut segs: Vec<(f64, f64)> = Vec::new();
        for &(el, eh) in ranges {
            let a = lo.max(el);
            let b = hi.min(eh);
            if a < b {
                segs.push((a, b));
            }
        }
        if segs.is_empty() {
            continue;
        }
        segs.sort_by(|a, b| a.0.total_cmp(&b.0));
        let group_width = hi.ln() - lo.ln();
        let covered: f64 = segs.iter().map(|(a, b)| b.ln() - a.ln()).sum();
        let overlap = if group_width > 0.0 {
            covered / group_width
        } else {
            0.0
        };
        // Node energies inside the segments (covered nodes only).
        let inside: Vec<&ShieldNode> = nodes
            .iter()
            .filter(|n| {
                n.covered
                    && segs
                        .iter()
                        .any(|&(a, b)| n.energy_ev >= a && n.energy_ev <= b)
            })
            .collect();
        if inside.is_empty() {
            continue;
        }
        let ens: Vec<f64> = inside.iter().map(|n| n.energy_ev).collect();
        let mut factors = vec![vec![vec![0.0f64; temps.len()]; sig0.len()]; 4];
        let mut shielded = vec![vec![vec![0.0f64; temps.len()]; sig0.len()]; 4];
        let mut sigma_p = 0.0;
        let mut inf = [0.0f64; 4];
        // Collapse each segment independently and combine with lethargy weight.
        // The trapezoid interpolates log-linearly at interior nodes and extends
        // end values to the segment edges, so the whole segment contributes.
        for &(a, b) in &segs {
            let seg_nodes: Vec<f64> = ens.iter().copied().filter(|&v| v >= a && v <= b).collect();
            if seg_nodes.is_empty() {
                continue;
            }
            let weight = (b.ln() - a.ln()) / covered;
            let collapse = |pick: &dyn Fn(&ShieldNode) -> f64| -> f64 {
                let vals: Vec<f64> = inside
                    .iter()
                    .filter(|n| seg_nodes.contains(&n.energy_ev))
                    .map(|n| pick(n))
                    .collect();
                if seg_nodes.len() == 1 {
                    vals[0]
                } else {
                    lethargy_trapezoid(&seg_nodes, &vals, a, b)
                }
            };
            sigma_p += weight * collapse(&|n| n.sigma_p_b);
            for c in 0..4 {
                let sigi_c = collapse(&|n| n.infinite_dilution_b[c]);
                inf[c] += weight * sigi_c;
                for (i, _) in sig0.iter().enumerate() {
                    for (t, _) in temps.iter().enumerate() {
                        let xs_c = collapse(&|n| {
                            n.sigf
                                .as_ref()
                                .map(|s| s[c][i][t])
                                .unwrap_or(n.infinite_dilution_b[c])
                        });
                        shielded[c][i][t] += weight * xs_c;
                        if sigi_c != 0.0 {
                            factors[c][i][t] += weight * xs_c / sigi_c;
                        }
                    }
                }
            }
        }
        // Full-group Bondarenko fold: the covered segments contribute their
        // probability-table moments; the uncovered part contributes its MF=3
        // background suppressed by the uniform weight sigma0/(sigma0+bkg_t).
        let mut uncovered: Vec<(f64, f64)> = Vec::new();
        let mut edge = lo;
        for &(a, b) in &segs {
            if a > edge {
                uncovered.push((edge, a));
            }
            edge = b;
        }
        if edge < hi {
            uncovered.push((edge, hi));
        }
        let rest_width: f64 = uncovered.iter().map(|(a, b)| b.ln() - a.ln()).sum();
        let mut background = [0.0f64; 4];
        if rest_width > 0.0 {
            for (c, mt) in [1, 2, 18, 102].iter().enumerate() {
                let mut acc = 0.0;
                for &(a, b) in &uncovered {
                    acc += mf3_lethargy_mean(eval, *mt, a, b)? * (b.ln() - a.ln());
                }
                background[c] = acc / rest_width;
            }
        }
        // Segment mean weight and weighted moment via the node ptables.
        let sig0_max = sig0[0];
        let mut weight_mean = vec![vec![0.0f64; temps.len()]; sig0.len()];
        let mut xw_seg = vec![vec![vec![0.0f64; temps.len()]; sig0.len()]; 4];
        for &(a, b) in &segs {
            let seg_nodes: Vec<f64> = ens.iter().copied().filter(|&v| v >= a && v <= b).collect();
            if seg_nodes.is_empty() {
                continue;
            }
            let wseg = (b.ln() - a.ln()) / covered;
            let seg_inside: Vec<&ShieldNode> = inside
                .iter()
                .filter(|n| seg_nodes.contains(&n.energy_ev))
                .copied()
                .collect();
            for (i, &s0) in sig0.iter().enumerate() {
                for (t, _) in temps.iter().enumerate() {
                    let (wm_vals, xw_vals): (Vec<f64>, [Vec<f64>; 4]) = if seg_nodes.len() == 1 {
                        let (wm, _) = node_w_and_xw(seg_inside[0], 0, s0, sig0_max, t);
                        let mut xv: [Vec<f64>; 4] = Default::default();
                        for c in 0..4 {
                            xv[c] = vec![node_w_and_xw(seg_inside[0], c, s0, sig0_max, t).1];
                        }
                        (vec![wm], xv)
                    } else {
                        let wm: Vec<f64> = seg_inside
                            .iter()
                            .map(|n| node_w_and_xw(n, 0, s0, sig0_max, t).0)
                            .collect();
                        let mut xv: [Vec<f64>; 4] = Default::default();
                        for c in 0..4 {
                            xv[c] = seg_inside
                                .iter()
                                .map(|n| node_w_and_xw(n, c, s0, sig0_max, t).1)
                                .collect();
                        }
                        (wm, xv)
                    };
                    weight_mean[i][t] += wseg
                        * if wm_vals.len() == 1 {
                            wm_vals[0]
                        } else {
                            lethargy_trapezoid(&seg_nodes, &wm_vals, a, b)
                        };
                    for c in 0..4 {
                        xw_seg[c][i][t] += wseg
                            * if xw_vals[c].len() == 1 {
                                xw_vals[c][0]
                            } else {
                                lethargy_trapezoid(&seg_nodes, &xw_vals[c], a, b)
                            };
                    }
                }
            }
        }
        let bkg_t = background[0];
        let mut group_unshielded = [0.0f64; 4];
        let mut group_shielded = vec![vec![vec![0.0f64; temps.len()]; sig0.len()]; 4];
        let mut group_factors = vec![vec![vec![1.0f64; temps.len()]; sig0.len()]; 4];
        for c in 0..4 {
            group_unshielded[c] = overlap * inf[c] + (1.0 - overlap) * background[c];
            if group_unshielded[c] == 0.0 {
                continue;
            }
            for (i, &s0) in sig0.iter().enumerate() {
                let w_rest = s0 / (s0 + bkg_t);
                for t in 0..temps.len() {
                    if i == 0 {
                        // The infinite-dilution column is the reference by
                        // definition; pin it exactly rather than leaving the
                        // ~1e-10 float residual of the weight fold.
                        group_shielded[c][i][t] = group_unshielded[c];
                        group_factors[c][i][t] = 1.0;
                        continue;
                    }
                    let num = covered * xw_seg[c][i][t] + rest_width * background[c] * w_rest;
                    let den = covered * weight_mean[i][t] + rest_width * w_rest;
                    if den > 0.0 {
                        group_shielded[c][i][t] = num / den;
                        group_factors[c][i][t] = group_shielded[c][i][t] / group_unshielded[c];
                    }
                }
            }
        }
        out.push(ShieldGroup {
            group: g,
            overlap_fraction: overlap,
            sigma_p_b: sigma_p,
            infinite_dilution_b: inf,
            factors,
            shielded_b: shielded,
            background_b: background,
            weight_mean,
            group_unshielded_b: group_unshielded,
            group_shielded_b: group_shielded,
            group_factors,
        });
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stratified_draws_are_a_fixed_permutation_of_the_quantile_grid() {
        let mut stream = Stratified::new(3, 1, 2);
        let mut seen = vec![false; NSTRAT];
        for _ in 0..NSTRAT {
            let u = stream.draw();
            assert!(u > 0.0 && u < 1.0);
            let cell = (u * NSTRAT as f64) as usize;
            assert!(!seen[cell], "quantile cell {cell} drawn twice");
            seen[cell] = true;
        }
        assert!(seen.iter().all(|&v| v));
        // Deterministic: a fresh stream reproduces the same sequence.
        let mut again = Stratified::new(3, 1, 2);
        assert_eq!(stream.draw(), again.draw());
    }

    #[test]
    fn stratified_streams_differ_by_purpose() {
        let mut a = Stratified::new(0, 0, 0);
        let mut b = Stratified::new(0, 0, 1);
        assert_ne!(a.draw(), b.draw());
    }

    #[test]
    fn uw2_asymptotic_matches_c1_over_x2() {
        // Deep wing: rew -> y*c1/(x^2+y^2), aimw -> x*c1/(x^2+y^2).
        let (rew, aimw) = uw2(200.0, 0.3);
        let expect_re = 0.3 * 0.5641895835 / (200.0 * 200.0 + 0.09);
        assert!((rew - expect_re).abs() / expect_re < 1e-4);
        assert!(aimw > 0.0);
        // Symmetry: real part even in x, imaginary odd.
        let (rew_neg, aimw_neg) = uw2(-200.0, 0.3);
        assert_eq!(rew, rew_neg);
        assert_eq!(aimw, -aimw_neg);
    }

    #[test]
    fn gnrx_identity_consistent_widths() {
        // Constant widths (dof=0 on all) reduce to the plain ratio.
        // galpha/(galpha+gamma) via id=2: sum w*x/(gn*x+gg) with the x draws
        // collapsing is not exact, but the ratio must stay inside (0,1).
        let s = gnrx(1.0, 0.0, 1.0, 1, 0, 0, 0.0, 2);
        assert!(s > 0.0 && s < 1.0);
        // The fluctuation moment falls as the neutron width dominates: a
        // larger mean gn suppresses the capture share.
        let s_hi = gnrx(1.0e6, 0.0, 1.0, 1, 0, 0, 0.0, 2);
        assert!(s_hi < s);
    }

    #[test]
    fn shield_node_uncovered_has_no_sigf() {
        // A node outside every unresolved range carries no table.
        let node = ShieldNode {
            energy_ev: 1.0,
            covered: false,
            sigma_p_b: 0.0,
            infinite_dilution_b: [1.0; 4],
            sigf: None,
            ptable: None,
            sigf_direct: None,
            mean_unshielded: [0.0; 4],
            ladder_sigma_percent: [0.0; 4],
            ladder_count: 0,
            resonances_per_ladder: 0,
        };
        assert!(node.sigf.is_none());
    }
}
