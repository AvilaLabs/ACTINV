#!/usr/bin/env python3
"""P195 checker: gates on results/p195_record.json and the unfold outputs under target/p195-work; writes
results/p195_verdict.json.

  G0  The SHA-256 of the protocol, controls/p195.py and this checker are in protocols/protocol_hash.txt.
  G1  Implementation and provenance: results/p195_g1.json records fmt, check, clippy and the workspace tests passing
      on the record's commit; the record comes from a clean tracked tree; library, index, covariance sidecar,
      IRDFF-II spectrum archive and the committed files it reads match their pins.
  G2  Exactness, recomputed here from each output and its inputs: reaction-rate predictions and Jacobians from the
      library; foil derivatives by central differences (first G3 case); stationarity; the posterior covariance in
      group space; the cross-section covariance by the Python port of the sparse collapse (G4 arms); chi-square,
      pulls, band fluxes, intervals, resolutions and response windows; twelve planted mutations detected.
  G3  Calibration on 40 synthetic truths (gate).
  G4  IRDFF-II measured reaction rates, leave-one-out (reported, never a gate).
  G5  Determinism: two pairs byte-identical apart from the wall time.
  G6  Refusals: each case exits 2 and names its cause.
  G7  CI green on the evidence commit (external).

Verdict: P195-PASS iff G0, G1, G2, G3, G5 and G6 pass (G7 external).
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import random
import re
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "controls"))

PROTOCOL_REL = "protocols/ACTINV-P195_PROTOCOL.md"
HASHED = [PROTOCOL_REL, "controls/p195.py", "controls/check_p195.py"]
HASH_LOG = ROOT / "protocols" / "protocol_hash.txt"
RECORD = ROOT / "results" / "p195_record.json"
G1_RECORD = ROOT / "results" / "p195_g1.json"
VERDICT = ROOT / "results" / "p195_verdict.json"
SCHEMA = "actinv-p195-record-1"

LIBRARY_CATALOG = "catalog:tendl-2025-neutron-709g"
LIBRARY_SHA256 = "ec4c72bf598dc8ad3d533d9cfafdcf493e2d1f949a3e4db6251495659b68cc44"
INDEX_SHA256 = "8bd19b4001c246758e739cd0067a0087e1ce5c2157438dae97bd52e1d3beb3fb"
COVARIANCE_CATALOG = "catalog:tendl-2025-neutron-709g-covariance"
COVARIANCE_SHA256 = "c19dec86b44ad5d90b66c9ab94d53e18641a1d354a89402a4da7986b6c530cde"
DECAY_PRIMARY = "catalog:endfb-viii-0-decay"
DECAY_FALLBACK = "catalog:jeff-3-3-decay"
DATA_DIR = ROOT / "actinv-data" / "v1.2.0"
IRDFF_SPECTRUM_SHA256 = "544c06ec741672c729ee9f2e716935a616bc44f3296001a1394d8760ff817e52"
COMMITTED_SHA256 = {
    "examples/fns_fe_5min.json": "ed55ed00f6c00fc4a2351e18332e98e7feb33d9b7ca65e7ba861e0e5e8feef39",
    "results/g4_p17_diagnostics.json": "c65ad753055923afe77df82a2d532effa1f8c594cdedb7bfbe7344adf747bcaa",
    "controls/p17_irdff.py": "3560feaab45ebd73cd51b599a01f68a47ccd00e6f2e30fd972bcaea95320f11f",
}
FNS_PRIOR = "examples/fns_fe_5min.json"

SEED = 195
PRIOR_SD = 0.30
PRIOR_LENGTH = 1.0
G3_SCHEDULE = [{"dt": "1 h", "flux": 1.0}, {"dt": "1 h", "flux": 0.0}]
G3_STEP = 2
G3_FOILS = [("Au", ["Au198"]), ("Co", ["Co60"]), ("Sc", ["Sc46"]), ("In", ["In115m", "In116m"]),
            ("Ni", ["Co58", "Ni57"]), ("Fe", ["Mn54", "Mn56"]), ("Al", ["Na24"]),
            ("Ti", ["Sc46", "Sc47", "Sc48"]), ("Nb", ["Nb92m"]), ("Zr", ["Zr89"])]
G3_TRUTHS = 40
G3_NOISE = 0.03
G3_COVERAGE_95 = (0.90, 0.99)
G3_COVERAGE_68 = (0.58, 0.78)
FD_STEP = 1e-3
FD_RELATIVE = 1e-4
FD_ABSOLUTE = 1e-9
FIELD_MAT = {"cf252": 9861, "u235": 9228}
FIELD_TABLES = {"cf252": (18, 19), "u235": (20,)}
MAXWELL_KT_EV = {"cf252": 1.2e6, "u235": 1.5e6}
G4_ARMS = [("cf252", "a"), ("cf252", "b"), ("u235", "a"), ("u235", "b")]
G4_TOTAL_FLUX = 1.0
ALPHA = 0.05
CM2_PER_BARN = 1e-24
WALL_KEYS = ("wall_time_s",)

SYMBOLS = ("n H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Br Kr Rb Sr "
           "Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W "
           "Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm").split()

# G6: each refusal case and a word its message must carry
REFUSALS = {
    "projectile": "projectile", "rebin": "rebin", "relative_error": "relative_error",
    "step_spectrum": "own spectrum", "feed": "feed", "power": "power", "wall_loading": "wall",
    "partition": "partition", "uncertainty_block": "uncertainty", "rates_with_self_shielding": "self_shielding",
    "no_sigma": "sigma", "nonpositive_value": "positive", "not_produced": "does not produce",
    "unknown_target": "not in the activation library", "unknown_mt": "no total row",
    "no_production_row": "no production row", "correlation_length": "correlation length",
    "all_fixed": "held fixed",
}


# ------------------------------------------------------------------------------------------------- shared helpers

def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def library_path() -> Path:
    return DATA_DIR / "activation" / "tendl-2025-neutron-709g.npz"


def library_index_path() -> Path:
    return DATA_DIR / "activation" / "tendl-2025-neutron-709g_index.json"


def covariance_path() -> Path:
    return DATA_DIR / "uncertainty" / "tendl-2025-neutron-709g.cov.npz"


_BOUNDS = None


def library_bounds() -> list[float]:
    global _BOUNDS
    if _BOUNDS is None:
        with np.load(library_path(), allow_pickle=False) as archive:
            _BOUNDS = [float(v) for v in archive["bounds"]]
    return _BOUNDS


class Sigma:
    """Group cross sections (barns) and descriptors of the library rows asked for, read once (P185's reader)."""

    def __init__(self, wanted):
        import check_p185 as c185
        self.library = c185.Library(library_path(), wanted=sorted(set(wanted)))

    def row(self, index: int) -> np.ndarray:
        return self.library.sigma[index]

    def descriptor(self, index: int) -> list[int]:
        return [int(v) for v in self.library.rows[index]]


def nuclide_name(za: int, liso: int) -> str:
    z, a = divmod(int(za), 1000)
    name = f"{SYMBOLS[z]}{a}"
    if liso == 1:
        name += "m"
    elif liso > 1:
        name += f"m{liso}"
    return name


def lethargy(bounds, groups) -> np.ndarray:
    b = np.asarray(bounds, dtype=float)
    g = np.asarray(groups, dtype=int)
    return 0.5 * (np.log(b[g]) + np.log(b[g + 1]))


def prior_covariance(bounds, adjustable, sd, length) -> np.ndarray:
    u = lethargy(bounds, adjustable)
    sd = np.full(len(adjustable), sd) if np.isscalar(sd) else np.asarray(sd, dtype=float)
    if length is None:
        return np.diag(sd * sd)
    return np.outer(sd, sd) * np.exp(-np.abs(u[:, None] - u[None, :]) / length)


def maxwellian(bounds, kt) -> list[float]:
    b = np.asarray(bounds, dtype=float)
    # antiderivative of E exp(-E/T): -T exp(-E/T) (E + T)
    f = -kt * np.exp(-b / kt) * (b + kt)
    weights = np.maximum(np.diff(f), 0.0)
    return (weights / weights.sum()).tolist()


def g4_prior(field: str, kind: str, spectra: dict, bounds) -> list[float]:
    if kind == "a":
        other = "u235" if field == "cf252" else "cf252"
        return list(spectra[other])
    return maxwellian(bounds, MAXWELL_KT_EV[field])


def fd_groups(record: dict) -> list[int]:
    """The three groups of largest |J| and three more drawn with seed 195 among the rest with J != 0."""
    groups = record["jacobian"]["groups"]
    values = record["jacobian"]["values"]
    order = sorted(range(len(groups)), key=lambda k: (-abs(values[k]), groups[k]))
    top = [groups[k] for k in order[:3]]
    rest = sorted(groups[k] for k in order[3:])
    rng = random.Random(f"{SEED}:{record['id']}")
    return sorted(top + (rng.sample(rest, 3) if len(rest) >= 3 else rest))


def committed_hashes(root=ROOT) -> dict:
    return {rel: (sha256(root / rel) if (root / rel).is_file() else None) for rel in COMMITTED_SHA256}


def cgroup() -> dict:
    try:
        relative = Path("/proc/self/cgroup").read_text().strip().split("::", 1)[1]
    except (OSError, IndexError):
        return {"path": None}
    base = Path("/sys/fs/cgroup") / relative.lstrip("/")
    record = {"path": relative}
    for name in ("memory.max", "memory.swap.max", "pids.max", "cpu.max"):
        try:
            record[name] = (base / name).read_text().strip()
        except OSError:
            record[name] = None
    return record


def median(values):
    s = sorted(values)
    n = len(s)
    return float("nan") if n == 0 else (s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2]))


def wilcoxon(differences):
    """Two-sided Wilcoxon signed-rank test, zeros dropped, normal approximation with tie correction (P192's)."""
    import check_p192 as c192
    return c192.wilcoxon(differences)


def normal_quantile(confidence: float) -> float:
    from statistics import NormalDist
    return NormalDist().inv_cdf(0.5 + 0.5 * confidence)


def rel_close(a, b, tolerance) -> bool:
    a, b = float(a), float(b)
    return abs(a - b) <= tolerance * max(abs(a), abs(b), 1e-300)


# ------------------------------------------------------------------------------------------------- G0, G1

def g0(root=ROOT, log=None) -> list[str]:
    log = HASH_LOG.read_text() if log is None else log
    problems = []
    for rel in HASHED:
        path = Path(root) / rel
        if not path.is_file():
            problems.append(f"missing {rel}")
        elif f"{sha256(path)}  {rel}" not in log:
            problems.append(f"{rel} hash not in protocol_hash.txt")
    return problems


def g1(record: dict, g1_record: dict | None, committed: dict | None = None) -> list[str]:
    problems = []
    if record.get("schema") != SCHEMA:
        problems.append("wrong record schema")
    env = record.get("environment", {})
    if env.get("git_dirty_paths"):
        problems.append("the record was made from a dirty tracked tree")
    inputs = record.get("inputs", {})
    for key, pin in (("library", LIBRARY_SHA256), ("covariance", COVARIANCE_SHA256)):
        if (inputs.get(key) or {}).get("sha256") != pin:
            problems.append(f"{key} SHA-256 differs from its pin")
    if (inputs.get("library") or {}).get("index_sha256") != INDEX_SHA256:
        problems.append("library index SHA-256 differs from its pin")
    if inputs.get("irdff_spectrum_archive") != IRDFF_SPECTRUM_SHA256:
        problems.append("IRDFF-II spectrum archive SHA-256 differs from its pin")
    recorded = inputs.get("committed") or {}
    for rel, pin in COMMITTED_SHA256.items():
        if recorded.get(rel) != pin:
            problems.append(f"{rel} SHA-256 differs from its pin")
    if committed is not None:
        for rel, pin in COMMITTED_SHA256.items():
            if committed.get(rel) != pin:
                problems.append(f"{rel} on disk differs from its pin")
    if g1_record is None:
        problems.append("results/p195_g1.json is missing")
    else:
        if g1_record.get("git_dirty_paths"):
            problems.append("the implementation gates ran on a dirty tracked tree")
        if g1_record.get("git_head") != env.get("git_head"):
            problems.append("the implementation gates ran on another commit")
        if g1_record.get("binary_sha256") != (inputs.get("binary") or {}).get("sha256"):
            problems.append("the implementation gates name another binary")
        for name in ("fmt", "check", "clippy", "test"):
            if (g1_record.get("commands", {}).get(name) or {}).get("exit") != 0:
                problems.append(f"implementation gate {name} did not pass")
    return problems


# ------------------------------------------------------------------------------------------------- G2 exactness

def dense_jacobian(record: dict, position: dict, n: int) -> np.ndarray:
    row = np.zeros(n)
    for g, v in zip(record["groups"], record["values"]):
        row[position[g]] = v
    return row


def reconstruct(doc: dict, measurements_in: dict, bounds) -> dict:
    """The quantities the exactness checks compare, rebuilt from the output's raw fields and the input."""
    groups = doc["groups"]
    adjustable = [g for g, flag in enumerate(groups["adjustable"]) if flag]
    position = {g: a for a, g in enumerate(adjustable)}
    n = len(adjustable)
    phi0 = np.asarray(groups["prior_flux"], dtype=float)
    phi = np.asarray(groups["posterior_flux"], dtype=float)
    prior = measurements_in["prior"]
    sd = prior["relative_uncertainty"]
    if not np.isscalar(sd):
        sd = [sd[g] for g in adjustable]
    corr = prior["correlation"]
    length = corr["length"] if corr["kind"] == "lethargy_exponential" else None
    p0 = prior_covariance(bounds, adjustable, sd, length)
    meas = doc["measurements"]
    m = len(meas)
    jac = np.array([dense_jacobian(r["jacobian"], position, n) for r in meas])
    jac0 = np.array([dense_jacobian(r["prior_jacobian"], position, n) for r in meas])
    y = np.log([r["measured"] for r in meas])
    f = np.log([r["posterior_prediction"] for r in meas])
    f0 = np.log([r["prior_prediction"] for r in meas])
    c = np.asarray(doc["measurement_covariance_ln"], dtype=float)
    x = np.log(phi[adjustable])
    x0 = np.log(phi0[adjustable])
    return {"adjustable": adjustable, "position": position, "n": n, "m": m, "phi0": phi0, "phi": phi, "p0": p0,
            "jac": jac, "jac0": jac0, "y": y, "f": f, "f0": f0, "c": c, "x": x, "x0": x0}


def exactness(doc: dict, measurements_in: dict, bounds, sigma: Sigma | None = None,
              c_xs_reference: np.ndarray | None = None, c_xs0_diag: np.ndarray | None = None) -> list[str]:
    problems = []
    try:
        r = reconstruct(doc, measurements_in, bounds)
    except (KeyError, ValueError, TypeError) as error:
        return [f"cannot reconstruct the output: {error}"]
    meas = doc["measurements"]
    # reaction rates from the library
    if sigma is not None:
        for i, record in enumerate(meas):
            if record["kind"] != "reaction_rate":
                continue
            index = record["library_row"]["index"]
            if sigma.descriptor(index) != [record["library_row"][k] for k in ("target", "MT", "ZAP", "LFS", "LMF")]:
                problems.append(f"{record['id']}: library row descriptor differs")
                continue
            s = sigma.row(index) * CM2_PER_BARN
            for label, flux, key, jac in (("posterior", r["phi"], "posterior_prediction", r["jac"][i]),
                                          ("prior", r["phi0"], "prior_prediction", r["jac0"][i])):
                value = float(np.dot(s, flux))
                if not rel_close(value, record[key], 1e-10):
                    problems.append(f"{record['id']}: {label} prediction differs from the library fold")
                expected = s[r["adjustable"]] * flux[r["adjustable"]] / value
                scale = np.max(np.abs(expected))
                if np.max(np.abs(expected - jac)) > 1e-10 * scale:
                    problems.append(f"{record['id']}: {label} Jacobian differs from the library fold")
    # stationarity
    p0_inv = np.linalg.inv(r["p0"])
    c_inv_r = np.linalg.solve(r["c"], r["y"] - r["f"])
    lhs = r["jac"].T @ c_inv_r
    rhs = p0_inv @ (r["x"] - r["x0"])
    scale = max(np.max(np.abs(lhs)), np.max(np.abs(rhs)), 1e-300)
    if np.max(np.abs(lhs - rhs)) > 1e-6 * scale:
        problems.append(f"not stationary: {np.max(np.abs(lhs - rhs)) / scale:.3e} of the largest term")
    # posterior covariance in group space
    precision = r["jac"].T @ np.linalg.solve(r["c"], r["jac"]) + p0_inv
    p = np.linalg.inv(precision)
    groups = doc["groups"]
    for a, g in enumerate(r["adjustable"]):
        if not rel_close(math.sqrt(max(p[a, a], 0.0)), groups["posterior_sd_ln"][g], 1e-6):
            problems.append(f"group {g}: posterior sd differs")
            break
    for a, g in enumerate(r["adjustable"]):
        if not rel_close(math.sqrt(r["p0"][a, a]), groups["prior_sd_ln"][g], 1e-9):
            problems.append(f"group {g}: prior sd differs")
            break
        resolution = 1.0 - p[a, a] / r["p0"][a, a]
        if abs(resolution - groups["resolution"][g]) > 1e-6 * max(abs(resolution), 1e-3):
            problems.append(f"group {g}: resolution differs")
            break
    # bands
    multiplier = doc["method"]["normal_multiplier"]
    if not rel_close(multiplier, normal_quantile(doc["method"]["confidence_level"]), 1e-9):
        problems.append("normal multiplier differs from the confidence level's quantile")
    band_sd = []
    for band in doc["bands"]:
        first, end = band["first_group"], band["end_group"]
        if bounds[first] != band["lower_eV"] or bounds[end] != band["upper_eV"]:
            problems.append("band edges differ from the group boundaries")
        members = [g for g in range(first, end) if g in r["position"]]
        idx = [r["position"][g] for g in members]
        w = r["phi"][members]
        w0 = r["phi0"][members]
        variance = float(w @ p[np.ix_(idx, idx)] @ w) if idx else 0.0
        variance0 = float(w0 @ r["p0"][np.ix_(idx, idx)] @ w0) if idx else 0.0
        flux = float(np.sum(r["phi"][first:end]))
        flux0 = float(np.sum(r["phi0"][first:end]))
        sd_post = math.sqrt(max(variance, 0.0))
        band_sd.append((idx, w, sd_post))
        checks = (("posterior_flux", flux, 1e-9), ("prior_flux", flux0, 1e-9), ("posterior_sd", sd_post, 1e-6),
                  ("prior_sd", math.sqrt(max(variance0, 0.0)), 1e-9))
        for key, value, tol in checks:
            if not rel_close(value, band[key], tol):
                problems.append(f"band {band['lower_eV']:.3g}-{band['upper_eV']:.3g} eV: {key} differs")
        lo, hi = band["interval"]
        if not (rel_close(lo, band["posterior_flux"] - multiplier * band["posterior_sd"], 1e-9)
                and rel_close(hi, band["posterior_flux"] + multiplier * band["posterior_sd"], 1e-9)):
            problems.append("band interval differs from flux ± z sd")
    for u, (iu, wu, su) in enumerate(band_sd):
        for v, (iv, wv, sv) in enumerate(band_sd):
            emitted = doc["band_correlation"][u][v]
            if not iu or not iv or su == 0.0 or sv == 0.0:
                continue
            value = float(wu @ p[np.ix_(iu, iv)] @ wv) / (su * sv)
            if emitted is None or abs(value - emitted) > 1e-6:
                problems.append(f"band correlation ({u}, {v}) differs")
    # chi-square, pulls, windows
    residual = r["y"] - r["f"]
    data_term = float(residual @ np.linalg.solve(r["c"], residual))
    dx = r["x"] - r["x0"]
    prior_term = float(dx @ np.linalg.solve(r["p0"], dx))
    consistency = doc["consistency"]
    if not rel_close(data_term, consistency["data_term"], 1e-9):
        problems.append("chi-square data term differs")
    if abs(prior_term - consistency["prior_term"]) > 1e-9 * max(prior_term, 1e-12) + 1e-12:
        problems.append("chi-square prior term differs")
    if not rel_close(consistency["chi_square"], data_term + prior_term, 1e-9):
        problems.append("chi-square differs")
    if consistency["degrees_of_freedom"] != r["m"]:
        problems.append("degrees of freedom differ")
    k_post = r["jac"] @ p @ r["jac"].T
    k_prior0 = r["jac0"] @ r["p0"] @ r["jac0"].T
    for i, record in enumerate(meas):
        variance0 = record["prior_measurement_variance_ln"] + k_prior0[i, i]
        pull0 = (r["y"][i] - r["f0"][i]) / math.sqrt(variance0)
        if not rel_close(pull0, record["prior_pull"], 1e-9):
            problems.append(f"{record['id']}: prior pull differs")
        variance = r["c"][i, i] - k_post[i, i]
        if variance > 0.0:
            pull = residual[i] / math.sqrt(variance)
            if record["posterior_pull"] is None or not rel_close(pull, record["posterior_pull"], 1e-6):
                problems.append(f"{record['id']}: posterior pull differs")
        u = record["sigma"] / record["measured"]
        own = u * u + record["model_relative_uncertainty"] ** 2
        c_xs_ii = r["c"][i, i] - own
        if abs(math.sqrt(max(c_xs_ii, 0.0)) - record["cross_section_relative_sd"]) > 1e-9 * max(
                record["cross_section_relative_sd"], 1e-6):
            problems.append(f"{record['id']}: cross-section sd differs from the covariance diagonal")
        s = r["jac"][i] * math.exp(r["f"][i])
        total = s.sum()
        for key, level in (("e05", 0.05), ("e50", 0.5), ("e95", 0.95)):
            cumulative = np.cumsum(s)
            hit = int(np.argmax(cumulative >= level * total)) if total > 0 else None
            expected = bounds[r["adjustable"][hit] + 1] if hit is not None else None
            if record["response_window_eV"][key] != expected:
                problems.append(f"{record['id']}: response window {key} differs")
        if c_xs0_diag is not None:
            if not rel_close(record["prior_measurement_variance_ln"], own + c_xs0_diag[i], 1e-9):
                problems.append(f"{record['id']}: prior measurement variance differs from the collapse")
    if c_xs_reference is not None:
        own = np.diag([(rec["sigma"] / rec["measured"]) ** 2 + rec["model_relative_uncertainty"] ** 2
                       for rec in meas])
        emitted = r["c"] - own
        scale = np.max(np.abs(c_xs_reference))
        if np.max(np.abs(emitted - c_xs_reference)) > 1e-9 * max(scale, 1e-300):
            problems.append("cross-section covariance differs from the Python collapse")
    return problems


def finite_difference_problems(doc: dict, runs: list[dict]) -> list[str]:
    problems = []
    by_key = {(run["foil"], run["group"], run["sign"]): run["activities"] for run in runs}
    checked = 0
    for record in doc["measurements"]:
        if record["kind"] != "activity":
            continue
        m = record["posterior_prediction"]
        position = dict(zip(record["jacobian"]["groups"], record["jacobian"]["values"]))
        for g in fd_groups(record):
            plus = by_key.get((record["foil"], g, 1), {}).get(record["nuclide"])
            minus = by_key.get((record["foil"], g, -1), {}).get(record["nuclide"])
            if plus is None or minus is None:
                problems.append(f"{record['id']}: no finite-difference runs for group {g}")
                continue
            estimate = (plus - minus) / (math.exp(FD_STEP) - math.exp(-FD_STEP))
            emitted = position[g] * m
            if abs(estimate - emitted) > max(FD_RELATIVE * abs(emitted), FD_ABSOLUTE * m):
                problems.append(f"{record['id']}: group {g} derivative {emitted:.6e} vs difference {estimate:.6e}")
            checked += 1
    if checked == 0:
        problems.append("no finite-difference comparison ran")
    return problems


MUTATIONS = [
    ("posterior flux", lambda d: d["groups"]["posterior_flux"].__setitem__(_first_adj(d),
                                                                           d["groups"]["posterior_flux"][_first_adj(d)] * 1.01)),
    ("posterior sd", lambda d: d["groups"]["posterior_sd_ln"].__setitem__(_first_adj(d),
                                                                          d["groups"]["posterior_sd_ln"][_first_adj(d)] * 1.01)),
    ("prior sd", lambda d: d["groups"]["prior_sd_ln"].__setitem__(_first_adj(d), 0.31)),
    ("resolution", lambda d: d["groups"]["resolution"].__setitem__(_first_adj(d), 0.5)),
    ("band flux", lambda d: d["bands"][-1].__setitem__("posterior_flux", d["bands"][-1]["posterior_flux"] * 1.01)),
    ("band sd", lambda d: d["bands"][-1].__setitem__("posterior_sd", d["bands"][-1]["posterior_sd"] * 1.01)),
    ("band interval", lambda d: d["bands"][-1].__setitem__("interval", [0.0, 1.0])),
    ("chi-square", lambda d: d["consistency"].__setitem__("chi_square", d["consistency"]["chi_square"] + 1.0)),
    ("pull", lambda d: d["measurements"][0].__setitem__("prior_pull", d["measurements"][0]["prior_pull"] + 0.1)),
    ("window", lambda d: d["measurements"][0]["response_window_eV"].__setitem__("e50", 1.0)),
    ("jacobian", lambda d: _scale_largest(d["measurements"][0]["jacobian"]["values"], 1.01)),
    ("covariance", lambda d: d["measurement_covariance_ln"][0].__setitem__(
        0, d["measurement_covariance_ln"][0][0] * 1.01)),
]


def _scale_largest(values, factor):
    k = max(range(len(values)), key=lambda i: abs(values[i]))
    values[k] *= factor


def _first_adj(doc):
    return next(g for g, flag in enumerate(doc["groups"]["adjustable"]) if flag)


def mutation_problems(doc: dict, measurements_in: dict, bounds) -> list[str]:
    problems = []
    for name, mutate in MUTATIONS:
        mutated = copy.deepcopy(doc)
        mutate(mutated)
        if not exactness(mutated, measurements_in, bounds):
            problems.append(f"mutation '{name}' was not detected")
    return problems


_SIDECAR = None


def c_xs_collapse(doc: dict, sigma: Sigma) -> tuple[np.ndarray, np.ndarray]:
    """Relative cross-section covariance of a reaction-rate-only output at the accepted point, and its diagonal at the
    prior, from P185's reading of the collapse (state-resolved rows take their reaction's covariance), with the
    excluded blocks the output names. Each rate's gradient is the total flux at its row (in cm² per barn); the
    bilinear form comes from the quadratic one by polarization."""
    import check_p185 as c185
    global _SIDECAR
    if _SIDECAR is None:
        components = c185.sidecar(covariance_path())
        _SIDECAR = (components, c185.self_keys(components))
    components, keys = _SIDECAR
    library = sigma.library
    meas = doc["measurements"]
    rows = [rec["library_row"]["index"] for rec in meas]
    out = []
    for which, key, flux_key in (("posterior", "posterior_prediction", "posterior_flux"),
                                 ("prior", "prior_prediction", "prior_flux")):
        phi = np.asarray(doc["groups"][flux_key], dtype=float)
        excluded = {(b["target"], min(b["MT"], b["MT1"]), max(b["MT"], b["MT1"]))
                    for b in doc["cross_section_excluded_blocks"][which]}
        gradient = phi.sum() * CM2_PER_BARN
        sens = [{row: gradient} if c185.covered(library.rows[row], keys, "candidate") else {} for row in rows]

        def form(s):
            return c185.variance(library, components, phi, s, excluded)[0] if s else 0.0

        n = len(meas)
        own = [form(s) for s in sens]
        absolute = np.zeros((n, n))
        for i in range(n):
            absolute[i, i] = own[i]
            for j in range(i + 1 if which == "posterior" else n, n):
                both = dict(sens[i])
                for row, value in sens[j].items():
                    both[row] = both.get(row, 0.0) + value
                absolute[i, j] = absolute[j, i] = 0.5 * (form(both) - own[i] - own[j])
        m = np.array([rec[key] for rec in meas])
        out.append(absolute / np.outer(m, m))
    return out[0], np.diag(out[1])


# ------------------------------------------------------------------------------------------------- G3, G4

def load_case(case: dict) -> tuple[dict, dict]:
    directory = ROOT / case["dir"]
    return (json.loads((directory / "unfold.json").read_text()),
            json.loads((directory / "measurements.json").read_text()))


def g3_statistics(record: dict, docs: list[dict], truths: list[list[float]]) -> dict:
    z_values, ln_post, ln_prior, per_band = [], [], [], {}
    for doc, truth in zip(docs, truths):
        truth = np.asarray(truth)
        for band in doc["bands"]:
            if band["prior_flux"] <= 0.0:
                continue
            true_flux = float(np.sum(truth[band["first_group"]:band["end_group"]]))
            z = (band["posterior_flux"] - true_flux) / band["posterior_sd"] if band["posterior_sd"] > 0 else math.inf
            key = f"{band['lower_eV']:.4g}-{band['upper_eV']:.4g}"
            per_band.setdefault(key, {"z": [], "ln_post": [], "ln_prior": []})
            entry = per_band[key]
            entry["z"].append(z)
            entry["ln_post"].append(math.log(band["posterior_flux"] / true_flux))
            entry["ln_prior"].append(math.log(band["prior_flux"] / true_flux))
            z_values.append(z)
            ln_post.append(entry["ln_post"][-1])
            ln_prior.append(entry["ln_prior"][-1])

    def summary(z, lp, lq):
        n = len(z)
        return {"pairs": n, "within_1.96": sum(abs(v) <= 1.96 for v in z) / n if n else None,
                "within_1": sum(abs(v) <= 1.0 for v in z) / n if n else None,
                "rms_ln_posterior": math.sqrt(sum(v * v for v in lp) / n) if n else None,
                "rms_ln_prior": math.sqrt(sum(v * v for v in lq) / n) if n else None}

    chi = [doc["consistency"]["chi_square_per_degree_of_freedom"] for doc in docs]
    return {"overall": summary(z_values, ln_post, ln_prior),
            "bands": {k: summary(v["z"], v["ln_post"], v["ln_prior"]) for k, v in per_band.items()},
            "chi_square_per_dof": {"median": median(chi), "min": min(chi) if chi else None,
                                   "max": max(chi) if chi else None},
            "wall_s": {"median_per_unfold": median([c["wall_s"] for c in record["g3"]["cases"]])}}


def g3_problems(record: dict, stats: dict) -> list[str]:
    problems = []
    cases = record["g3"]["cases"]
    if len(cases) != G3_TRUTHS:
        problems.append(f"{len(cases)} G3 cases, not {G3_TRUTHS}")
    for case in cases:
        if case["exit"] != 0:
            problems.append(f"G3 case {case['case']} exited {case['exit']}")
    overall = stats["overall"]
    if overall["pairs"] == 0:
        return problems + ["no G3 band pairs"]
    if not G3_COVERAGE_95[0] <= overall["within_1.96"] <= G3_COVERAGE_95[1]:
        problems.append(f"95% coverage {overall['within_1.96']:.3f} outside {G3_COVERAGE_95}")
    if not G3_COVERAGE_68[0] <= overall["within_1"] <= G3_COVERAGE_68[1]:
        problems.append(f"one-sd coverage {overall['within_1']:.3f} outside {G3_COVERAGE_68}")
    if not overall["rms_ln_posterior"] < overall["rms_ln_prior"]:
        problems.append("the posterior band fluxes are not closer to the truth than the prior's")
    return problems


def g4_measurements(record: dict, sigma: Sigma, reference_spectra: dict | None) -> dict:
    arms = {}
    for name, arm in record["g4"].items():
        failed = [arm["full"]] * (arm["full"]["exit"] != 0) + [loo for loo in arm.get("loo", []) if loo["exit"] != 0]
        if failed:
            arms[name] = {"unavailable": f"{len(failed)} unfolds of this arm did not finish"}
            continue
        full_doc, _ = load_case(arm["full"])
        by_id = {rec["id"]: rec for rec in full_doc["measurements"]}
        rows = []
        for loo in arm.get("loo", []):
            left = loo["left_out"]
            row = next(r for r in arm["rows"] if r["row_id"] == left)
            doc, _ = load_case(loo)
            s = sigma.row(by_id[left]["library_row"]["index"]) * CM2_PER_BARN
            e = row["E_mb"] * 1e-27 * G4_TOTAL_FLUX
            prediction = float(np.dot(s, doc["groups"]["posterior_flux"]))
            prior = by_id[left]["prior_prediction"]
            entry = {"row_id": left, "label": row["label"], "E": e, "loo": prediction, "prior": prior,
                     "abs_ln_loo": abs(math.log(prediction / e)), "abs_ln_prior": abs(math.log(prior / e))}
            if reference_spectra is not None:
                reference = float(np.dot(s, reference_spectra[arm["field"]])) * G4_TOTAL_FLUX
                entry["abs_ln_reference"] = abs(math.log(reference / e))
            rows.append(entry)
        d = [r["abs_ln_loo"] - r["abs_ln_prior"] for r in rows]
        test = wilcoxon(d)
        med = median(d)
        if test["p"] < ALPHA and med < 0:
            label = "UNFOLD CLOSER"
        elif test["p"] < ALPHA and med > 0:
            label = "PRIOR CLOSER"
        else:
            label = "NO SIGNIFICANT DIFFERENCE"
        bands = []
        if reference_spectra is not None:
            ref = np.asarray(reference_spectra[arm["field"]]) * G4_TOTAL_FLUX
            for band in full_doc["bands"]:
                true_flux = float(np.sum(ref[band["first_group"]:band["end_group"]]))
                if band["posterior_sd"] > 0 and true_flux > 0:
                    bands.append({"lower_eV": band["lower_eV"], "upper_eV": band["upper_eV"],
                                  "z_posterior": (band["posterior_flux"] - true_flux) / band["posterior_sd"],
                                  "ratio_posterior": band["posterior_flux"] / true_flux,
                                  "ratio_prior": band["prior_flux"] / true_flux})
        arms[name] = {"rows": len(rows), "median_abs_ln_prior": median([r["abs_ln_prior"] for r in rows]),
                      "median_abs_ln_loo": median([r["abs_ln_loo"] for r in rows]),
                      "median_abs_ln_reference": median([r["abs_ln_reference"] for r in rows])
                      if reference_spectra is not None else None,
                      "wilcoxon": test, "median_difference": med, "label": label,
                      "full_chi_square_per_dof": full_doc["consistency"]["chi_square_per_degree_of_freedom"],
                      "bands_against_reference": bands, "per_row": rows}
    return arms


# ------------------------------------------------------------------------------------------------- G5, G6

def strip_wall(text: str) -> str:
    return re.sub(r'\n\s*"wall_time_s": [^\n]*', "", text)


def g5_problems(record: dict) -> list[str]:
    problems = []
    if len(record.get("g5", [])) != 2:
        problems.append("G5 needs two pairs")
    for pair in record.get("g5", []):
        if pair["second"]["exit"] != 0 or not (ROOT / pair["first"] / "unfold.json").is_file():
            problems.append(f"{pair['label']}: a run did not finish")
            continue
        first = (ROOT / pair["first"] / "unfold.json").read_text()
        second = (ROOT / pair["second"]["dir"] / "unfold.json").read_text()
        if strip_wall(first) != strip_wall(second):
            problems.append(f"{pair['label']}: outputs differ beyond the wall time")
    return problems


def g6_problems(record: dict) -> list[str]:
    problems = []
    seen = {case["case"]: case for case in record.get("g6", [])}
    for name, needle in REFUSALS.items():
        case = seen.get(name)
        if case is None:
            problems.append(f"refusal {name} not run")
        elif case["exit"] != 2:
            problems.append(f"refusal {name} exited {case['exit']}")
        elif needle not in case["stderr"]:
            problems.append(f"refusal {name}: message lacks '{needle}'")
    return problems


# ------------------------------------------------------------------------------------------------- verdict

def fmt(x, digits=3):
    return f"{x:.{digits}f}"


def sentences(stats: dict, arms: dict) -> list[str]:
    o = stats["overall"]
    if any("unavailable" in a for a in arms.values()):
        return None
    per_arm = "; ".join(
        f"{name.replace('cf252', 'Cf-252').replace('u235', 'U-235')} prior ({'the other field' if name.endswith('-a') else 'a Maxwellian'}): "
        f"{fmt(a['median_abs_ln_prior'])} → {fmt(a['median_abs_ln_loo'])}, {a['label']}, p = {a['wilcoxon']['p']:.2g}"
        for name, a in arms.items())
    return [
        "`actinv unfold` estimates a neutron spectrum from foil activities, modelled with a full ACTINV solve of each "
        "foil's irradiation history, and from reaction rates, carrying the library's cross-section covariance into the "
        "fit.",
        f"On {G3_TRUTHS} synthetic spectra drawn from its prior, {100 * o['within_1.96']:.0f}% of the true band fluxes "
        f"fell inside its 95% intervals and {100 * o['within_1']:.0f}% inside one standard deviation, and its band "
        f"fluxes were closer to the truth than the prior's (root-mean-square log error {fmt(o['rms_ln_posterior'])} "
        f"against {fmt(o['rms_ln_prior'])}).",
        "On IRDFF-II's measured spectrum-averaged cross sections in the Cf-252 and U-235 fission fields, predicting each "
        f"reaction left out of the fit, the median |ln(C/E)| was {per_arm}; the Cf-252 reference spectrum used similar "
        "measurements in its evaluation.",
    ]


def evaluate(record: dict, full: bool = True) -> dict:
    bounds = library_bounds()
    gates = {"G0": g0(), "G1": g1(record, json.loads(G1_RECORD.read_text()) if G1_RECORD.is_file() else None,
                                  committed_hashes())}
    g2 = []
    wanted = set()
    for arm in record["g4"].values():
        if arm["full"]["exit"] == 0:
            wanted |= {rec["library_row"]["index"] for rec in load_case(arm["full"])[0]["measurements"]}
    sigma = Sigma(wanted)
    g3_docs, truths = [], []
    for case in record["g3"]["cases"]:
        if case["exit"] != 0:
            continue
        directory = ROOT / case["dir"]
        if sha256(directory / "unfold.json") != case["output_sha256"]:
            g2.append(f"G3 case {case['case']}: output differs from its recorded SHA-256")
            continue
        doc, measurements_in = load_case(case)
        g3_docs.append(doc)
        truths.append(json.loads((directory / "truth.json").read_text())["flux_ascending"])
        if full:
            g2 += [f"G3 case {case['case']}: {p}" for p in exactness(doc, measurements_in, bounds)]
    first_doc, first_in = load_case(record["g3"]["cases"][0])
    g2 += [f"finite differences: {p}" for p in finite_difference_problems(first_doc, record["g2_finite_differences"]["runs"])]
    g2 += mutation_problems(first_doc, first_in, bounds)
    reference_spectra = None
    try:
        import p195 as harness  # noqa: F401  (the harness's field spectra, rebinned as in P17)
        reference_spectra = harness.field_spectra()
    except Exception as error:  # the spectra are context only
        g2.append(f"cannot rebuild the IRDFF-II field spectra: {error}")
    for name, arm in record["g4"].items():
        full_case = arm["full"]
        if full_case["exit"] != 0:
            g2.append(f"G4 arm {name}: full-data unfold exited {full_case['exit']}")
            continue
        doc, measurements_in = load_case(full_case)
        c_ref, c0_diag = c_xs_collapse(doc, sigma) if full else (None, None)
        g2 += [f"G4 arm {name}: {p}" for p in exactness(doc, measurements_in, bounds, sigma, c_ref, c0_diag)]
        for loo in arm.get("loo", []):
            if loo["exit"] != 0:
                g2.append(f"G4 arm {name}: leave-one-out {loo['left_out']} exited {loo['exit']}")
    gates["G2"] = g2
    stats = g3_statistics(record, g3_docs, truths)
    gates["G3"] = g3_problems(record, stats)
    arms = g4_measurements(record, sigma, reference_spectra)
    gates["G5"] = g5_problems(record)
    gates["G6"] = g6_problems(record)
    passed = all(not gates[k] for k in ("G0", "G1", "G2", "G3", "G5", "G6"))
    return {"verdict": "P195-PASS" if passed else "P195-FAIL", "gates": gates,
            "g3": stats, "g4": arms, "sentences": sentences(stats, arms) if passed else None,
            "external": {"G7": "CI green on the evidence commit"}}


# ------------------------------------------------------------------------------------------------- selftest

def synthetic_doc():
    """A tiny unfold solved here: 4 groups, 2 reaction-rate-like linear measurements, exponential prior."""
    bounds = [1.0, 10.0, 100.0, 1000.0, 10000.0]
    phi0 = np.array([1.0, 2.0, 1.5, 0.5])
    k = np.array([[0.2, 0.5, 0.3, 0.0], [0.0, 0.1, 0.4, 0.9]])
    measured = np.array([1.6, 1.1])
    sigma = 0.03 * measured
    p0 = prior_covariance(bounds, [0, 1, 2, 3], 0.3, 1.0)
    x0 = np.log(phi0)
    y = np.log(measured)
    c = np.diag((sigma / measured) ** 2)
    x = x0.copy()
    for _ in range(100):
        m = k @ np.exp(x)
        jac = k * np.exp(x)[None, :] / m[:, None]
        a = jac @ p0 @ jac.T + c
        w = np.linalg.solve(a, y - np.log(m) + jac @ (x - x0))
        x_new = x0 + p0 @ jac.T @ w
        if np.max(np.abs(x_new - x)) < 1e-14:
            x = x_new
            break
        x = x_new
    phi = np.exp(x)
    m = k @ phi
    jac = k * phi[None, :] / m[:, None]
    m0 = k @ phi0
    jac0 = k * phi0[None, :] / m0[:, None]
    p = np.linalg.inv(jac.T @ np.linalg.solve(c, jac) + np.linalg.inv(p0))
    z = 1.959963984540054
    edges = [(0, 2), (2, 4)]
    bands = []
    for first, end in edges:
        idx = list(range(first, end))
        w = phi[idx]
        sd = math.sqrt(float(w @ p[np.ix_(idx, idx)] @ w))
        sd0 = math.sqrt(float(phi0[idx] @ p0[np.ix_(idx, idx)] @ phi0[idx]))
        flux = float(phi[first:end].sum())
        bands.append({"first_group": first, "end_group": end, "lower_eV": bounds[first], "upper_eV": bounds[end],
                      "prior_flux": float(phi0[first:end].sum()), "prior_sd": sd0, "posterior_flux": flux,
                      "posterior_sd": sd, "interval": [flux - z * sd, flux + z * sd]})
    corr = [[float(phi[list(range(*eu))] @ p[np.ix_(list(range(*eu)), list(range(*ev)))] @ phi[list(range(*ev))])
             / (bands[u]["posterior_sd"] * bands[v]["posterior_sd"]) for v, ev in enumerate(edges)]
            for u, eu in enumerate(edges)]
    residual = y - np.log(m)
    data_term = float(residual @ np.linalg.solve(c, residual))
    prior_term = float((x - x0) @ np.linalg.solve(p0, x - x0))
    k_post = jac @ p @ jac.T
    k0 = jac0 @ p0 @ jac0.T
    records = []
    for i in range(2):
        s = jac[i] * m[i]
        cumulative = np.cumsum(s)
        window = {key: bounds[int(np.argmax(cumulative >= level * s.sum())) + 1]
                  for key, level in (("e05", 0.05), ("e50", 0.5), ("e95", 0.95))}
        records.append({"id": f"r{i}", "kind": "synthetic", "measured": float(measured[i]), "sigma": float(sigma[i]),
                        "model_relative_uncertainty": 0.0, "prior_prediction": float(m0[i]),
                        "posterior_prediction": float(m[i]),
                        "prior_pull": float((y[i] - math.log(m0[i])) / math.sqrt(c[i, i] + k0[i, i])),
                        "posterior_pull": float(residual[i] / math.sqrt(c[i, i] - k_post[i, i])),
                        "prior_measurement_variance_ln": float(c[i, i]), "cross_section_relative_sd": 0.0,
                        "response_window_eV": window,
                        "jacobian": {"groups": [g for g in range(4) if jac[i, g] != 0],
                                     "values": [float(v) for v in jac[i] if v != 0]},
                        "prior_jacobian": {"groups": [g for g in range(4) if jac0[i, g] != 0],
                                           "values": [float(v) for v in jac0[i] if v != 0]}})
    doc = {"method": {"normal_multiplier": z, "confidence_level": 0.95},
           "groups": {"adjustable": [True] * 4, "prior_flux": phi0.tolist(), "posterior_flux": phi.tolist(),
                      "prior_sd_ln": np.sqrt(np.diag(p0)).tolist(), "posterior_sd_ln": np.sqrt(np.diag(p)).tolist(),
                      "resolution": (1 - np.diag(p) / np.diag(p0)).tolist()},
           "bands": bands, "band_correlation": corr, "measurements": records,
           "measurement_covariance_ln": c.tolist(),
           "consistency": {"chi_square": data_term + prior_term, "data_term": data_term, "prior_term": prior_term,
                           "degrees_of_freedom": 2, "chi_square_per_degree_of_freedom": (data_term + prior_term) / 2}}
    measurements_in = {"prior": {"relative_uncertainty": 0.3,
                                 "correlation": {"kind": "lethargy_exponential", "length": 1.0}}}
    return doc, measurements_in, bounds


def selftest() -> int:
    failures = []

    def expect(condition, message):
        if not condition:
            failures.append(message)

    doc, measurements_in, bounds = synthetic_doc()
    problems = exactness(doc, measurements_in, bounds)
    expect(not problems, f"a consistent synthetic unfold fails exactness: {problems}")
    for name, mutate in MUTATIONS:
        mutated = copy.deepcopy(doc)
        mutate(mutated)
        expect(exactness(mutated, measurements_in, bounds), f"mutation {name} not detected")
    expect(nuclide_name(49115, 1) == "In115m" and nuclide_name(26056, 0) == "Fe56"
           and nuclide_name(72178, 2) == "Hf178m2", "nuclide names")
    record = {"id": "x", "jacobian": {"groups": list(range(20)), "values": [float(v) for v in range(1, 21)]}}
    chosen = fd_groups(record)
    expect(chosen == fd_groups(record) and len(chosen) == 6 and {17, 18, 19} <= set(chosen), "fd group rule")
    mw = maxwellian([1.0, 1e5, 1e6, 1e7, 1e9], 1.3e6)
    expect(abs(sum(mw) - 1.0) < 1e-12 and all(v >= 0 for v in mw), "maxwellian normalisation")
    p = prior_covariance([1.0, math.e, math.e ** 3], [0, 1], 0.3, 1.0)
    expect(abs(p[0, 1] - 0.09 * math.exp(-1.5)) < 1e-15, "prior kernel")
    expect(strip_wall('{\n "a": 1,\n "wall_time_s": 2.5\n}') == strip_wall('{\n "a": 1,\n "wall_time_s": 9\n}'),
           "wall-time strip")
    fake = {"g6": [{"case": n, "exit": 2, "stderr": f"unfold: {w}"} for n, w in REFUSALS.items()]}
    expect(not g6_problems(fake), "g6 accepts the expected refusals")
    fake["g6"][0]["exit"] = 1
    expect(g6_problems(fake), "g6 rejects a wrong exit code")
    stats = {"overall": {"pairs": 10, "within_1.96": 0.95, "within_1": 0.7, "rms_ln_posterior": 0.1,
                         "rms_ln_prior": 0.2}}
    rec = {"g3": {"cases": [{"case": t, "exit": 0} for t in range(G3_TRUTHS)]}}
    expect(not g3_problems(rec, stats), "g3 passes a calibrated set")
    stats["overall"]["within_1.96"] = 0.8
    expect(g3_problems(rec, stats), "g3 rejects under-coverage")
    print(f"check_p195 selftest: {len(failures)} failures")
    for failure in failures:
        print(f"  {failure}")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--record", type=Path, default=RECORD, help="another record (a rehearsal); default the protocol's")
    parser.add_argument("--no-write", action="store_true", help="do not write results/p195_verdict.json")
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    record = json.loads(args.record.read_text())
    verdict = evaluate(record)
    if not args.no_write:
        VERDICT.write_text(json.dumps(verdict, indent=1, sort_keys=True) + "\n")
    for gate in ("G0", "G1", "G2", "G3", "G5", "G6"):
        problems = verdict["gates"][gate]
        print(f"{gate}: {'PASS' if not problems else 'FAIL'} ({len(problems)} problems)")
        for problem in problems[:10]:
            print(f"  {problem}")
    print(verdict["verdict"])
    return 0 if verdict["verdict"] == "P195-PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
