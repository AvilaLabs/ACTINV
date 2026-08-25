"""ACTINV P3-G2: Doppler broadening by the SIGMA1 method (Cullen): exact kernel integration for a cross section that is
linear in energy between grid points; 1/v extrapolation below the first point; constant above the last.
Memory-bounded: output points are processed in chunks and only input segments within |x - y| <= WINDOW contribute
(the Gaussian kernel is < 1e-28 beyond 8 half-widths). Peak memory ~ chunk × window points."""
import numpy as np
from scipy.special import erf
KB = 8.617333262e-5  # eV/K
SQPI = np.sqrt(np.pi); WINDOW = 8.0; CHUNK = 128
def _F(n, t):
    e = np.exp(-t * t)
    if n == 0: return SQPI / 2 * erf(t)
    if n == 1: return -e / 2
    if n == 2: return SQPI / 4 * erf(t) - t * e / 2
    if n == 3: return -(t * t + 1) * e / 2
    if n == 4: return 3 * SQPI / 8 * erf(t) - (t ** 3 / 2 + 3 * t / 4) * e
    raise ValueError
_FINF = {0: SQPI / 2, 1: 0.0, 2: SQPI / 4, 3: 0.0, 4: 3 * SQPI / 8}
def broaden(E, sig, T, awr, Eout=None):
    E = np.asarray(E, float); sig = np.asarray(sig, float); Eout = E if Eout is None else np.asarray(Eout, float)
    kT = KB * T / awr; x = np.sqrt(E / kT); y = np.sqrt(Eout / kT)
    dE = np.diff(E); okseg = dE > 0; b_all = np.where(okseg, np.diff(sig) / np.where(okseg, dE, 1.0), 0.0); a_all = sig[:-1] - b_all * E[:-1]; b_all = b_all * kT   # slope per unit x^2; zero-length segments (double points) carry no weight
    out = np.zeros_like(y)
    for c0 in range(0, y.size, CHUNK):
        yc = y[c0:c0 + CHUNK]
        for sign in (+1.0, -1.0):
            s = sign; yy = (s * yc)[:, None]
            # segments whose t-range [x_k - s*y, x_{k+1} - s*y] intersects [-WINDOW, WINDOW] for some y in the chunk
            ylo, yhi = (s * yc).min(), (s * yc).max()
            k0 = max(0, np.searchsorted(x, ylo - WINDOW) - 1); k1 = min(x.size - 1, np.searchsorted(x, yhi + WINDOW) + 1)
            if k1 > k0:
                xs0 = x[k0:k1]; xs1 = x[k0 + 1:k1 + 1]; a = a_all[k0:k1]; b = b_all[k0:k1]
                t_lo = xs0[None, :] - yy; t_hi = xs1[None, :] - yy
                c0_ = a * yy ** 2 + b * yy ** 4; c1 = 2 * a * yy + 4 * b * yy ** 3; c2 = a + 6 * b * yy ** 2; c3 = 4 * b * yy; c4 = b * np.ones_like(yy)
                # F_n(t_hi) - F_n(t_lo) for n = 0..4 from one exp and one erf per array (identical algebra to _F)
                acc = np.zeros_like(t_lo)
                for t, sg in ((t_hi, 1.0), (t_lo, -1.0)):
                    e = np.exp(-t * t); er = erf(t)
                    F0 = SQPI / 2 * er; F1 = -e / 2; F2 = SQPI / 4 * er - t * e / 2; F3 = -(t * t + 1) * e / 2; F4 = 3 * SQPI / 8 * er - (t ** 3 / 2 + 3 * t / 4) * e
                    acc += sg * (c0_ * F0 + c1 * F1 + c2 * F2 + c3 * F3 + c4 * F4)
                out[c0:c0 + CHUNK] += sign * acc.sum(axis=1)
            # 1/v tail below x_0 (integrand sig_0 x_0 x) and constant tail above x_N (integrand sig_N x^2); both exact
            t0 = -s * yc; t1 = x[0] - s * yc; y1 = yy[:, 0]
            out[c0:c0 + CHUNK] += sign * sig[0] * x[0] * (y1 * (_F(0, t1) - _F(0, t0)) + (_F(1, t1) - _F(1, t0)))
            tN = x[-1] - s * yc
            out[c0:c0 + CHUNK] += sign * sig[-1] * (y1 ** 2 * (_FINF[0] - _F(0, tN)) + 2 * y1 * (_FINF[1] - _F(1, tN)) + (_FINF[2] - _F(2, tN)))
    return out / (y * y * SQPI)
