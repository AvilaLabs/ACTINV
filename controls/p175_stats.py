"""P175 shared definitions (protocols/ACTINV-P175_PROTOCOL.md): the registered constants, the dose responses R, the
reader of an actinv-r2s-cov-1 document and the sampled-versus-first-order arithmetic. Pure Python, no numpy. Both the
harness (controls/p175_r2s_cov.py) and the checker (controls/check_p175.py) import it, so the numbers the checker judges
are recomputed from the stored artifacts by the same registered formulas, not copied from the harness."""
import gzip
import json
import math
import os

SCHEMA = "actinv-p175-r2s-cov-1"
SAMPLES_SCHEMA = "actinv-p175-samples-1"

# ---- registered constants (protocol: "Registered constants") ------------------------------------------------------
N_SAMPLES = 200
REHEARSAL = os.environ.get("P175_REHEARSAL") == "1"  # protocol "Rehearsal": 8 samples, outputs under target/ only
if REHEARSAL:
    N_SAMPLES = 8
K_SIGMA = 3.0                  # k in |V1/Vs - 1| <= k SE_rel + delta_lin
DELTA_FLOOR = 0.02             # delta_lin = DELTA_FLOOR + rho_D^2 + (MF=33 clipped eigenvalue mass)
RHO_MAX = 0.5                  # rows with first-order relative sigma of D above this are outside the first-order regime
PAIR_FLOOR = 0.01              # floor on (1 - rho^2) in the pair-correlation width
DELTA_PAIR = 0.05              # allowance on a correlation coefficient
UNBANDED_MAX = 1.0e-3          # largest unbanded share of any region's photon source
BAND_REMAINDER = 1.0e-5        # nuclides are banded until the unbanded remainder of each (region, step) is below this
BAND_CAP = 60                  # most banded nuclides per material
G3_RTOL = 1.0e-10              # factor columns against the joint export
STEPS = (2, 3)                 # 1 d and 30 d of cooling
HARD_CELL = 1                  # the cell the high-energy response lives in (cell-b)
FLUX_AMPLITUDE = (1.0, 0.35, 0.10, 0.02)
FLUX_ERROR = (0.03, 0.06, 0.12, 0.25)   # relative tally error, every group with flux, per cell
CELL_IDS = ("cell-a", "cell-b", "cell-c", "cell-d")
STUDY_SEED = {"steel": 175001, "tungsten": 175002}
FLUX_SEED = {"steel": 175101, "tungsten": 175102}
ALL_CHANNELS = ("cross_section_mf33", "decay_constants", "flux")
RESPONSES = ("uniform", "hard_cell_b")

COMPOSITION = {
    "steel": {"FE": 89.07, "CR": 9.0, "W": 1.1, "MN": 0.4, "V": 0.2, "TA": 0.12, "C": 0.11},
    "tungsten": {"W": 100.0},
}
CASES = (
    {"name": "A_steel_shared", "material": "steel", "channels": ["cross_section_mf33", "decay_constants"],
     "flux": False, "joint": True},
    {"name": "B_steel_flux", "material": "steel", "channels": ["flux"], "flux": True, "joint": False},
    {"name": "C_steel_all", "material": "steel", "channels": ["cross_section_mf33", "decay_constants", "flux"],
     "flux": True, "joint": False},
    {"name": "D_tungsten_all", "material": "tungsten", "channels": ["cross_section_mf33", "decay_constants", "flux"],
     "flux": True, "joint": True},
)
# case D when the covariance holds no usable MF=33 block for the tungsten source (protocol: Case D)
D_FALLBACK_CHANNELS = ["decay_constants", "flux"]
BY_NAME = {case["name"]: case for case in CASES}


def constants():
    return {"n_samples": N_SAMPLES, "k_sigma": K_SIGMA, "delta_floor": DELTA_FLOOR, "rho_max": RHO_MAX,
            "pair_floor": PAIR_FLOOR, "delta_pair": DELTA_PAIR, "unbanded_max": UNBANDED_MAX,
            "band_remainder": BAND_REMAINDER, "band_cap": BAND_CAP, "g3_rtol": G3_RTOL, "steps": list(STEPS),
            "hard_cell": HARD_CELL, "flux_amplitude": list(FLUX_AMPLITUDE), "flux_error": list(FLUX_ERROR),
            "study_seed": STUDY_SEED, "flux_seed": FLUX_SEED, "composition": COMPOSITION,
            "cases": [{k: v for k, v in c.items()} for c in CASES], "d_fallback_channels": D_FALLBACK_CHANNELS}


# ---- dose responses -----------------------------------------------------------------------------------------------

def response(name, bounds, n_cells):
    """R[c][g], dose per source photon of region c and group g (the protocol's two registered responses)."""
    ng = len(bounds) - 1
    if name == "uniform":
        return [[1.0] * ng for _ in range(n_cells)]
    if name == "hard_cell_b":
        weights = [((bounds[g] + bounds[g + 1]) / 2.0 / 1.0e6) ** 2 for g in range(ng)]
        return [list(weights) if c == HARD_CELL else [0.0] * ng for c in range(n_cells)]
    raise ValueError(f"unknown response {name}")


def dot(r, s):
    return math.fsum(x * y for rc, sc in zip(r, s) for x, y in zip(rc, sc))


# ---- the actinv-r2s-cov-1 document ---------------------------------------------------------------------------------

def read_text(path):
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as stream:
        return stream.read()


def parse_cov(text):
    """Header, mean, shared columns and independent columns of an actinv-r2s-cov-1 stream."""
    header = mean = footer = None
    shared, independent = [], []
    for line in text.splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        kind = rec.get("record")
        if kind == "header":
            header = rec
        elif kind == "mean":
            mean = rec
        elif kind == "shared":
            shared.append(rec)
        elif kind == "independent":
            independent.append(rec)
        elif kind == "footer":
            footer = rec
    if header is None or mean is None or footer is None or header.get("schema") != "actinv-r2s-cov-1":
        raise ValueError("not a complete actinv-r2s-cov-1 document")
    ordinals = [region["ordinal"] for region in mean["regions"]]
    cols = {}
    for rec in independent:
        cols[ordinals.index(rec["ordinal"])] = rec["columns"]
    return {"header": header, "S": mean["S"], "regions": mean["regions"], "unbanded": mean["unbanded_photons_s"],
            "shared": shared, "independent": cols, "footer": footer,
            "bounds": header["group_bounds_eV"], "n_cells": len(mean["S"])}


def first_order(cov, r):
    """(V_total, V_shared, V_independent) of D = sum R S: sum_k (R.L_k)^2 + sum_c sum_j (R_c.M_cj)^2."""
    v_shared = math.fsum(dot(r, rec["column"]) ** 2 for rec in cov["shared"])
    v_indep = 0.0
    for c, columns in cov["independent"].items():
        v_indep += math.fsum(math.fsum(a * b for a, b in zip(r[c], col)) ** 2 for col in columns)
    return v_shared + v_indep, v_shared, v_indep


def channel_variances(cov, r):
    """Variance of R^T L L^T R by the channel each shared column carries (reported only)."""
    out = {}
    for rec in cov["shared"]:
        out[rec["channel"]] = out.get(rec["channel"], 0.0) + dot(r, rec["column"]) ** 2
    out["flux"] = first_order(cov, r)[2]
    return out


def total_strength_covariance(cov, channel=None):
    """Cell-by-cell covariance of the total photon strength T_c = sum_g S[c][g] from the factor columns; `channel`
    restricts the shared columns to one channel (independent columns are left out when it is given)."""
    n = cov["n_cells"]
    c_t = [[0.0] * n for _ in range(n)]
    for rec in cov["shared"]:
        if channel is not None and rec["channel"] != channel:
            continue
        a = [math.fsum(row) for row in rec["column"]]
        for i in range(n):
            for j in range(n):
                c_t[i][j] += a[i] * a[j]
    if channel is None:
        for c, columns in cov["independent"].items():
            for col in columns:
                c_t[c][c] += math.fsum(col) ** 2
    return c_t


def correlation(c_t):
    n = len(c_t)
    sd = [math.sqrt(max(c_t[i][i], 0.0)) for i in range(n)]
    return [[(c_t[i][j] / (sd[i] * sd[j]) if sd[i] > 0 and sd[j] > 0 else None) for j in range(n)] for i in range(n)]


def mf33_detail(cov):
    for ch in cov["header"].get("channels", []):
        if ch.get("channel") == "cross_section_mf33":
            return ch.get("detail") or {}
    return None


# ---- sampled statistics --------------------------------------------------------------------------------------------

def moments(xs):
    n = len(xs)
    mean = math.fsum(xs) / n
    dev = [x - mean for x in xs]
    m2 = math.fsum(d * d for d in dev) / n
    m4 = math.fsum(d ** 4 for d in dev) / n
    var = m2 * n / (n - 1)
    excess = (m4 / (m2 * m2) - 3.0) if m2 > 0 else 0.0
    return mean, var, excess


def se_rel_variance(n, excess):
    """Relative standard error of a sample variance, 2/(n-1) + excess kurtosis/n, never below the normal value."""
    return math.sqrt(max(2.0 / (n - 1) + excess / n, 2.0 / (n - 1)))


def pearson(xs, ys):
    n = len(xs)
    mx, my = math.fsum(xs) / n, math.fsum(ys) / n
    sxx = math.fsum((x - mx) ** 2 for x in xs)
    syy = math.fsum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    return math.fsum((x - mx) * (y - my) for x, y in zip(xs, ys)) / math.sqrt(sxx * syy)


def evaluate_row(v1, d0, samples, delta_extra):
    """One (case, step, response) row: the registered tolerance and verdict."""
    mean, var_s, excess = moments(samples)
    n = len(samples)
    se = se_rel_variance(n, excess)
    row = {"n": n, "D0": d0, "V_first_order": v1, "V_sampled": var_s, "sampled_mean": mean,
           "excess_kurtosis": excess, "se_rel": se}
    if not (d0 > 0 and math.isfinite(d0)):
        row.update(scored=False, reason="the nominal response is zero")
        return row
    rho = math.sqrt(v1) / d0
    row["rho_D"] = rho
    row["delta_lin"] = DELTA_FLOOR + rho * rho + delta_extra
    if rho > RHO_MAX:
        row.update(scored=False, reason=f"first-order relative sigma {rho:.3f} is above {RHO_MAX}")
        return row
    if not var_s > 0:  # no spread at all: only a first-order variance of zero agrees
        row.update(scored=True, ratio=None, deviation=None, limit=None, **{"pass": v1 == 0.0})
        return row
    ratio = v1 / var_s
    limit = K_SIGMA * se + row["delta_lin"]
    row.update(scored=True, ratio=ratio, deviation=abs(ratio - 1.0), limit=limit,
               **{"pass": abs(ratio - 1.0) <= limit})
    return row


def pair_rows(cov, totals):
    """Per-cell-pair check of the total-strength correlation: first order from the factor columns against the samples.
    `totals[sample][cell]` is T_c of that sample."""
    n = len(totals)
    c_t = total_strength_covariance(cov)
    rho1 = correlation(c_t)
    cells = cov["n_cells"]
    rows = []
    for i in range(cells):
        for j in range(i + 1, cells):
            if rho1[i][j] is None:
                continue
            rho_s = pearson([t[i] for t in totals], [t[j] for t in totals])
            if rho_s is None:
                continue
            width = max(1.0 - rho1[i][j] ** 2, 1.0 - rho_s ** 2, PAIR_FLOOR)
            limit = K_SIGMA * width / math.sqrt(n - 1) + DELTA_PAIR
            rows.append({"pair": [i, j], "rho_first_order": rho1[i][j], "rho_sampled": rho_s, "limit": limit,
                         "pass": abs(rho1[i][j] - rho_s) <= limit})
    return rows
