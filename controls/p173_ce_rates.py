#!/usr/bin/env python3
"""P173 harness (protocols/ACTINV-P173_PROTOCOL.md): ACTINV's 709-group collapsed reaction rates against OpenMC 0.15.3
continuous-energy reaction-rate tallies, on the same ENDF/B-VIII.1 evaluation, in concentric spherical shells around an
isotropic 14.1 MeV point source in vacuum.

Models (concentric shells, solid from the innermost to the outermost listed shell, void inside, vacuum outside):
  S, W, C, K   SS316L(N)-IG, tungsten, CuCrZr, ordinary concrete (PNNL-15870 Rev. 1, with 1 ppm Co and 1 ppm Eu).

Normalisation, both codes: per source particle, per target atom, in barn cm.
  R_openmc  OpenMC reaction-rate tally (nuclide + MT score, multiply_density False, track-length estimator, one energy
            bin 1e-5 eV - 1e9 eV): sum over the shell of the integral of sigma(E) phi(E) dV, in b cm per source particle.
  R_actinv  sum_g sigma_g L_g over the library rows of the target and MT, with L_g OpenMC's 709-group flux tally (track
            length in the shell, cm per source particle) on the library's group boundaries. This is the collapse
            `actinv` performs (sum_g sigma_g phi_g); G1 checks it against `actinv inspect xs --spectrum`.
  R_omg     sum_g sigma_g^OMC L_g, the same flux with group cross sections collapsed by this harness from OpenMC's own
            294 K pointwise HDF5 data (linear-linear, exact piecewise integral) with the flat-lethargy (1/E) weight on
            the fispact-709 boundaries, the weight and groups ACTINV's builder uses. It separates ACTINV's processing
            (r_proc = ln R_actinv/R_omg) from collapse and self-shielding (r_coll = ln R_omg/R_openmc);
            r = r_proc + r_coll.
  The atom density of the target multiplies all three and cancels in the ratios, so it is not used.

Modes (each its own process; none runs unless asked):
  openmc --model {S,W,C,K}     writes results/p173/openmc_<model>.json. Starts the 0.15.3 interpreter itself, one OpenMC
                               run per batch count in its own 6 GiB scope: 4, 8, ..., 256 batches of 1e5 particles,
                               stopping when every rate is above zero with relative standard error <= 0.5%. Same seed,
                               so each run contains the previous one.
  actinv --model {S,W,C,K}     writes results/p173/actinv_<model>.json (library arrays, OpenMC group cross sections and
                               the tallied group flux)
  g1-check                     writes results/p173/g1_check.json (harness collapse against `actinv inspect xs`)
  assemble                     merges the files into results/p173_ce_rates.json
  openmc-run, omc-xs (internal) build and run one OpenMC model; collapse OpenMC's HDF5 cross sections to 709 groups;
                               started by `openmc` with the OpenMC interpreter

Run each mode inside the bounded systemd scope AGENTS.md prescribes (see the protocol for exact commands). Every mode
refuses to start without an enforced cgroup. Nothing here is run by the protocol author.
"""
import argparse
import bisect
import hashlib
import json
import math
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOME = Path.home()
DATA = HOME / "nuclear-data"
# P173_REHEARSAL=1: a pre-registration rehearsal. Every output goes under target/ (never results/), the particle
# number per batch and the batch ceiling are small, and nothing it writes is scored or kept.
REHEARSAL = os.environ.get("P173_REHEARSAL") == "1"
OUT_DIR = ROOT / "target" / "p173-rehearsal" if REHEARSAL else ROOT / "results" / "p173"
RECORD = OUT_DIR / "record.json" if REHEARSAL else ROOT / "results" / "p173_ce_rates.json"
WORK_TAG = "p173-rehearsal" if REHEARSAL else "p173"
SCHEMA = "actinv-p173-ce-rates-1"
GIB = 1 << 30

OPENMC_VERSION = "0.15.3"
OPENMC_ENV = HOME / "micromamba/envs/openmc0153dd"
OPENMC_PY = OPENMC_ENV / "bin/python"
OPENMC_EXEC = OPENMC_ENV / "bin/openmc"
XS_XML = DATA / "endfb-viii.1-hdf5/cross_sections.xml"
XS_ARCHIVE = DATA / "endfb-viii.1-download/endfb81.tar.xz"
ENDF8_NPZ = DATA / "endfb-viii.1-p172/endfb81_all_709g.npz"
ENDF8_INDEX = DATA / "endfb-viii.1-p172/endfb81_all_709g_index.json"
GROUPS = ROOT / "crates/actinv-data/data/fispact_709_groups.json"
ACTINV_BIN = Path(os.environ.get("ACTINV_BIN", ROOT / "target/release/actinv"))

# Frozen inputs: name -> (path, SHA-256). The checker requires the record to hold exactly these.
PINNED = {
    "endf8_library": (ENDF8_NPZ, "ecaca7c072bd76b49e6724dd00aaa43172698f7c6a28e6551c551de39d11de32"),
    "endf8_index": (ENDF8_INDEX, "ff59cccf0ca85e24542054fff035079baefaea79a213e41c94bde2bc647162fa"),
    "endf8_hdf5_archive": (XS_ARCHIVE, "b7ad59cb4a3d76d8a291326093a98507f8d24b6e6af629116d3f7dc85f83c4cb"),
    "cross_sections_xml": (XS_XML, "b8ffb6104a946bf951dc0f3b9a1f57a60f7f57c4b308cd66df4fd3dd4bc557ce"),
    "fispact_709_groups": (GROUPS, "ce4bf523498ffbb38d42aa9415fc014882b8bc7a7ecfb89f859ba3259d8fb34f"),
}
# The release `actinv` binary the G1 collapse check runs; built before registration (the coordinator fills it in).
ACTINV_BIN_SHA256 = "ebeeb25c8f27a6689a2ab8667b699e5abb64d34f3349e4c9b203ee1d1709a725"

# ------------------------------------------------------------------------------------------------------ run schedule
PARTICLES_PER_BATCH = 1000 if REHEARSAL else 100_000
BATCH_START = 4
BATCH_MAX = 8 if REHEARSAL else 256  # 2^8
REL_SE_TARGET = 0.005
SEED_BASE = 173000
THREADS = 2
MEMORY_GIB = 6
LIMIT_S = 6 * 3600  # per OpenMC run
G1_TOLERANCE = 1e-9
SOURCE_ENERGY_EV = 14.1e6

# ------------------------------------------------------------------------------------------------------ materials
# Mass fractions by element; OpenMC expands each element to its natural isotopes. Densities in g/cm3.
MATERIALS = {
    # ITER grade 316L(N)-IG: nominal mid-range of the specification (Cr 17-18, Ni 12, Mo 2.3-2.7, Mn 1.6-2.0), Fe balance
    "ss316ln_ig": {"label": "SS316L(N)-IG", "density_g_cm3": 7.93,
                   "wo": {"Fe": 0.6522, "Cr": 0.175, "Ni": 0.12, "Mo": 0.025, "Mn": 0.018, "Si": 0.005, "N": 0.0007,
                          "Cu": 0.003, "Co": 0.0005, "C": 0.0003, "P": 0.00025, "S": 0.00005}},
    "tungsten": {"label": "Tungsten (natural)", "density_g_cm3": 19.3, "wo": {"W": 1.0}},
    "cucrzr": {"label": "CuCrZr", "density_g_cm3": 8.9, "wo": {"Cu": 0.9915, "Cr": 0.0075, "Zr": 0.0010}},
    # PNNL-15870 Rev. 1 "Concrete, Ordinary" (published fractions sum to 0.999999); dopants added by mass below
    "concrete": {"label": "Ordinary concrete (PNNL-15870 Rev. 1)", "density_g_cm3": 2.3,
                 "wo": {"H": 0.022100, "C": 0.002484, "O": 0.574930, "Na": 0.015208, "Mg": 0.001266, "Al": 0.019953,
                        "Si": 0.304627, "K": 0.010045, "Ca": 0.042951, "Fe": 0.006435},
                 "dopant_wo": {"Co": 1e-6, "Eu": 1e-6}},
}


def final_wo(material):
    """Normalised mass fractions of `material`; the dopants are added by mass and the rest scaled to make room."""
    spec = MATERIALS[material]
    dopants = spec.get("dopant_wo", {})
    base_sum = math.fsum(spec["wo"].values())
    keep = 1.0 - math.fsum(dopants.values())
    out = {el: keep * w / base_sum for el, w in spec["wo"].items()}
    out.update(dopants)
    return out


# ------------------------------------------------------------------------------------------------------ models
SHELLS = {"S": [(10, 12), (20, 25), (40, 50)], "W": [(10, 10.5), (10.5, 12), (15, 20)],
          "C": [(10, 12), (20, 25)], "K": [(50, 60), (100, 120)]}
BASE_MODELS = ("S", "W", "C", "K")
MODEL_ORDER = BASE_MODELS
MODEL_MATERIAL = {"S": "ss316ln_ig", "W": "tungsten", "C": "cucrzr", "K": "concrete"}

MT_SCORE = {16: "(n,2n)", 102: "(n,gamma)", 103: "(n,p)", 107: "(n,a)"}
# (target, MT, product), fixed before any run, from the shutdown-dose literature.
REACTIONS = {
    "S": [("Fe56", 103, "Mn56"), ("Fe54", 103, "Mn54"), ("Mn55", 16, "Mn54"), ("Mn55", 102, "Mn56"),
          ("Ni58", 103, "Co58"), ("Ni60", 103, "Co60"), ("Co59", 102, "Co60"), ("Cr50", 102, "Cr51"),
          ("Fe58", 102, "Fe59"), ("Ni58", 16, "Ni57"), ("Mo98", 102, "Mo99")],
    "W": [("W186", 102, "W187"), ("W184", 102, "W185"), ("W186", 16, "W185"), ("W182", 103, "Ta182"),
          ("W183", 103, "Ta183"), ("W182", 102, "W183"), ("W186", 103, "Ta186")],
    "C": [("Cu63", 102, "Cu64"), ("Cu65", 16, "Cu64"), ("Cu63", 107, "Co60"), ("Cr50", 102, "Cr51"),
          ("Zr90", 16, "Zr89")],
    "K": [("Na23", 102, "Na24"), ("Al27", 107, "Na24"), ("Co59", 102, "Co60"), ("Eu151", 102, "Eu152"),
          ("Mn55", 102, "Mn56"), ("Fe54", 103, "Mn54"), ("Ca44", 102, "Ca45")],
}
ELEMENT_Z = {"Fe": 26, "Mn": 25, "Co": 27, "Ni": 28, "Cr": 24, "Mo": 42, "W": 74, "Ta": 73, "Cu": 29, "Zr": 40,
             "Na": 11, "Al": 13, "Eu": 63, "Ca": 20}


def parse_nuclide(name):
    """('Fe56') -> (26, 56). Ground states only."""
    symbol = name.rstrip("0123456789")
    return ELEMENT_Z[symbol], int(name[len(symbol):])


def za(name):
    z, a = parse_nuclide(name)
    return z * 1000 + a


def product_za(target, mt):
    z, a = parse_nuclide(target)
    zp, ap = {16: (z, a - 1), 102: (z, a + 1), 103: (z - 1, a), 107: (z - 2, a - 3)}[mt]
    return zp * 1000 + ap


def model_spec(model):
    """The fixed definition of `model`: material, shells, reaction list and seed."""
    return {"model": model, "material": MODEL_MATERIAL[model], "shells": SHELLS[model],
            "reactions": REACTIONS[model], "seed": SEED_BASE + MODEL_ORDER.index(model)}


def layout(shells):
    """[(r_in, r_out, tallied)]: contiguous from the first shell's inner to the last shell's outer radius, the gaps
    between listed shells filled with untallied shells of the same material."""
    listed = {tuple(s) for s in shells}
    edges = sorted({x for s in shells for x in s})
    return [(a, b, (a, b) in listed) for a, b in zip(edges, edges[1:])]


def region_name(model, r_in, r_out):
    return f"{model}:{r_in:g}-{r_out:g}"


def regions_of(model):
    return [region_name(model, a, b) for a, b, tallied in layout(model_spec(model)["shells"]) if tallied]


def rate_key(target, mt):
    return f"{target}|{mt}"


def reaction_label(target, mt, product):
    return f"{target}{MT_SCORE[mt]}{product}"


def expected_rows():
    """Every (model, region, target, MT) the protocol lists, in a fixed order."""
    for model in MODEL_ORDER:
        for region in regions_of(model):
            for target, mt, _ in model_spec(model)["reactions"]:
                yield model, region, target, mt


def constants_digest():
    """SHA-256 of every constant that defines the models, materials, reactions and run schedule (hashed by G0)."""
    blob = {"materials": MATERIALS, "dopants_applied": {m: final_wo(m) for m in MATERIALS},
            "shells": SHELLS, "reactions": REACTIONS,
            "mt_score": MT_SCORE, "source_energy_eV": SOURCE_ENERGY_EV, "particles_per_batch": 100_000,
            "batch_start": BATCH_START, "batch_max": 256, "rel_se_target": REL_SE_TARGET, "seed_base": SEED_BASE,
            "model_order": MODEL_ORDER, "g1_tolerance": G1_TOLERANCE, "weight": "flat-lethargy",
            "omc_temperature": "294K", "omc_interpolation": "lin-lin"}
    return hashlib.sha256(json.dumps(blob, sort_keys=True).encode()).hexdigest()


# ------------------------------------------------------------------------------------------------------ utilities

def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=60).stdout.strip()


def read_text(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def cgroup_record():
    rel = None
    for line in (read_text("/proc/self/cgroup") or "").splitlines():
        parts = line.split(":", 2)
        if len(parts) == 3 and parts[0] == "0":
            rel = parts[2]
    base = Path("/sys/fs/cgroup") / (rel or "").lstrip("/")
    return {"path": rel, **{name: read_text(base / name) for name in
                            ("memory.max", "memory.swap.max", "pids.max", "cpu.max")}}


def cgroup_ok(cg, memory_gib=MEMORY_GIB):
    """Enforced limits: memory at most `memory_gib`, no swap, at most 128 tasks, at most two CPUs."""
    try:
        quota, period = cg["cpu.max"].split()
        return (int(cg["memory.max"]) <= memory_gib * GIB and cg["memory.swap.max"] == "0"
                and int(cg["pids.max"]) <= 128 and quota != "max" and int(quota) <= 2 * int(period))
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def require_bounded():
    cg = cgroup_record()
    if not cgroup_ok(cg):
        sys.exit(f"p173_ce_rates: refusing to run: cgroup is not an enforced bounded scope ({cg})")
    return cg


def environment_record(cg):
    status = git("status", "--porcelain", "--untracked-files=no")
    return {"git_head": git("rev-parse", "HEAD"), "crates_tree": git("rev-parse", "HEAD:crates"),
            "clean_tree": status == "", "cgroup": cg, "harness_sha256": sha256_file(Path(__file__)),
            "groups_sha256": sha256_file(GROUPS), "constants_sha256": constants_digest(),
            "python": platform.python_version(), "platform": platform.platform()}


def write_atomic(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, sort_keys=True) + "\n")
    tmp.replace(path)


def scoped(cmd):
    """`cmd` inside its own transient systemd scope with the AGENTS.md limits, so an out-of-memory kill takes only the
    OpenMC process and not this harness."""
    return ["systemd-run", "--user", "--scope", "-q", "-p", f"MemoryMax={MEMORY_GIB}G", "-p", "MemorySwapMax=0",
            "-p", "TasksMax=128", "-p", "CPUQuota=200%", "--", *[str(c) for c in cmd]]


def groups_ascending():
    """The fispact-709 boundaries in eV, ascending (the order of the library's bounds and of OpenMC's EnergyFilter)."""
    bounds = json.loads(GROUPS.read_text())["boundaries_eV"]
    return [float(v) for v in reversed(bounds)]


def mean_energy_eV(bounds, flux):
    """Flux-weighted mean energy, each group at its logarithmic mean energy (high - low) / ln(high / low), the
    representative energy the solver uses. None for an empty spectrum."""
    num = den = 0.0
    for lo, hi, f in zip(bounds, bounds[1:], flux):
        num += f * (hi - lo) / math.log(hi / lo)
        den += f
    return num / den if den > 0 else None


# ------------------------------------------------------------------------------------------------------ the collapse

def collapse(sig_row, flux):
    """sum_g sigma_g L_g (barn cm per source particle for a track-length flux L_g in cm per source particle)."""
    return math.fsum(float(s) * float(f) for s, f in zip(sig_row, flux))


def _log_moment(x1, x2):
    """(ln(x2/x1), (x2 - x1) - x1 ln(x2/x1)) for 0 < x1 < x2, the second without cancellation for close x1, x2."""
    t = (x2 - x1) / x1
    ln = math.log1p(t)
    if t < 1e-2:  # t - ln(1 + t) = t^2/2 - t^3/3 + ...
        series = math.fsum((-1.0) ** k * t ** k / k for k in range(2, 12))
        return ln, x1 * series
    return ln, x1 * (t - ln)


def lethargy_group_average(energy, xs, bounds):
    """Group cross sections sigma_g = (integral over the group of sigma(E) dE/E) / ln(E_hi/E_lo) of a pointwise table,
    exactly: sigma is linear in E between grid points (ENDF/HDF5 lin-lin), so each piece, split at the group
    boundaries, integrates in closed form: y1 ln(x2/x1) + slope [(x2 - x1) - x1 ln(x2/x1)]. Zero outside the table's
    energy range. `energy` ascending (equal neighbours are discontinuities); `bounds` ascending."""
    n = len(energy)
    out = []
    for lo, hi in zip(bounds, bounds[1:]):
        a, b = max(lo, energy[0]), min(hi, energy[-1])
        terms = []
        if b > a:
            i = max(bisect.bisect_right(energy, a) - 1, 0)
            while i < n - 1 and energy[i] < b:
                e1, e2 = energy[i], energy[i + 1]
                if e2 > e1:
                    x1, x2 = max(e1, a), min(e2, b)
                    if x2 > x1:
                        slope = (xs[i + 1] - xs[i]) / (e2 - e1)
                        y1 = xs[i] + slope * (x1 - e1)
                        ln, rest = _log_moment(x1, x2)
                        terms.append(y1 * ln + slope * rest)
                i += 1
        out.append(math.fsum(terms) / math.log(hi / lo))
    return out


def library_selection(rows):
    """{(target index, MT): (product row indices, total row indices)}. A product row has ZAP >= 0 (one per final
    state of one product); a total row has ZAP -1."""
    out = {}
    for i, row in enumerate(rows):
        prod, tot = out.setdefault((row[0], row[1]), ([], []))
        (prod if row[2] >= 0 else tot).append(i)
    return out


def ground_state_index(targets, target):
    wanted = za(target)
    for i, info in enumerate(targets):
        if info["za"] == wanted and info["liso"] == 0:
            return i
    return None


def actinv_reaction(rows, sig, targets, selection, target, mt, flux, flux_sd, sigma_omc=None):
    """ACTINV's rate for one reaction in one region: the sum over every product-state row of the target and MT of
    sum_g sigma_g L_g (a total row only when the library has no product row). `flux_sd` is the tally's standard error
    per group; the rate's standard error is bounded by sum_g sigma_g sd_g (the groups taken as fully correlated, which
    is the larger value). With `sigma_omc` (OpenMC's group cross sections for the same reaction) it also gives
    R_omg = sum_g sigma_g^OMC L_g, its bound, and the standard error of ln(R_actinv / R_omg). Both sums use the same
    L_g, so d ln(R_actinv/R_omg) = sum_g (a_g/R_a - o_g/R_o) dL_g, and with the groups fully correlated
    se = sum_g |a_g/R_a - o_g/R_o| sd_g, where a_g is ACTINV's summed group cross section."""
    t = ground_state_index(targets, target)
    if t is None:
        return {"status": "not_scored", "reason_code": "reaction_absent_actinv",
                "reason": f"{target} is not a ground-state target of the ACTINV library"}
    prod, tot = selection.get((t, mt), ([], []))
    use = prod or tot
    if not use:
        return {"status": "not_scored", "reason_code": "reaction_absent_actinv",
                "reason": f"the ACTINV library has no row for {target} MT {mt}"}
    rate = math.fsum(collapse(sig[i], flux) for i in use)
    bound = math.fsum(math.fsum(float(s) * float(d) for s, d in zip(sig[i], flux_sd)) for i in use)
    zaps = sorted({rows[i][2] for i in use})
    result = {"status": "ok", "R_actinv": rate, "R_actinv_se_bound": bound, "rows": use, "used_total_row": not prod,
              "product_zaps": zaps, "total_row_R_actinv": (collapse(sig[tot[0]], flux) if prod and tot else None),
              "R_omg": None, "R_omg_se_bound": None, "se_r_proc": None}
    if sigma_omc is not None:
        omg = collapse(sigma_omc, flux)
        result["R_omg"] = omg
        result["R_omg_se_bound"] = math.fsum(float(s) * float(d) for s, d in zip(sigma_omc, flux_sd))
        if rate > 0 and omg > 0:
            a_g = [math.fsum(float(sig[i][g]) for i in use) for g in range(len(flux))]
            result["se_r_proc"] = math.fsum(abs(a / rate - o / omg) * float(d)
                                            for a, o, d in zip(a_g, sigma_omc, flux_sd))
    return result


def log_ratio(r_actinv, se_actinv, r_openmc, se_openmc):
    """r = ln(R_actinv / R_openmc) and its first-order standard error, the two rates taken as independent."""
    return math.log(r_actinv / r_openmc), math.hypot(se_actinv / r_actinv, se_openmc / r_openmc)


def build_row(model, region, target, mt, product, mean_energy, plan, rate, actinv):
    """One scored or not-scored row of the record from the OpenMC plan and rate and the ACTINV reaction result."""
    row = {"model": model, "region": region, "target": target, "mt": mt, "reaction": reaction_label(target, mt, product),
           "mean_energy_eV": mean_energy}

    def not_scored(code, reason):
        row.update(status="not_scored", reason_code=code, reason=reason)
        return row
    if plan.get("status") != "tallied":
        return not_scored(plan.get("reason_code", "reaction_absent_openmc"), plan.get("reason", "not tallied"))
    if actinv.get("status") != "ok":
        return not_scored(actinv["reason_code"], actinv["reason"])
    ro, so = rate["mean"], rate["std_dev"]
    ra, sa = actinv["R_actinv"], actinv["R_actinv_se_bound"]
    rg, sg = actinv.get("R_omg"), actinv.get("R_omg_se_bound")
    row.update(R_openmc=ro, R_openmc_se=so, R_actinv=ra, R_actinv_se_bound=sa, R_omg=rg, R_omg_se_bound=sg,
               rel_se_openmc=(so / ro if ro > 0 else None), product_zaps_actinv=actinv["product_zaps"],
               product_matches=actinv["product_zaps"] == [product_za(target, mt)],
               total_row_R_actinv=actinv["total_row_R_actinv"])
    if not ro > 0:
        return not_scored("openmc_zero", "OpenMC's rate is zero after the last batch")
    row["converged"] = so / ro <= REL_SE_TARGET
    if not ra > 0:
        return not_scored("actinv_zero", "ACTINV's rate is zero where OpenMC's is positive")
    if rg is None:
        return not_scored("reaction_absent_openmc", "OpenMC's HDF5 data have no 294 K table for the reaction")
    if not rg > 0:
        return not_scored("omg_zero", "the collapsed OpenMC cross section is zero where OpenMC's rate is positive")
    r, se = log_ratio(ra, sa, ro, so)
    r_coll, se_coll = log_ratio(rg, sg, ro, so)
    row.update(status="scored", reason_code=None, reason=None, r=r, se_r=se, r_proc=math.log(ra / rg),
               se_r_proc=actinv["se_r_proc"], r_coll=r_coll, se_r_coll=se_coll)
    return row


def convergence(rates, plan):
    """Over the tallied rates of every region: how many are zero, how many are above the target and the largest
    relative standard error among the positive ones."""
    zero = over = total = 0
    worst = 0.0
    for per_region in rates.values():
        for key, rate in per_region.items():
            if plan[key].get("status") != "tallied":
                continue
            total += 1
            if not rate["mean"] > 0:
                zero += 1
                continue
            rel = rate["std_dev"] / rate["mean"]
            worst = max(worst, rel)
            over += rel > REL_SE_TARGET
    return {"rates": total, "zero": zero, "over_target": over, "max_rel_se": worst,
            "all_converged": total > 0 and zero == 0 and over == 0}


# ------------------------------------------------------------------------------------------------------ OpenMC runs

def run_openmc_model(model, out):
    cg = require_bounded()
    probe = subprocess.run([str(OPENMC_PY), "-c", "import openmc; print(openmc.__version__)"], capture_output=True,
                           text=True, timeout=120)
    version = probe.stdout.strip()
    if version != OPENMC_VERSION:
        sys.exit(f"p173_ce_rates: expected OpenMC {OPENMC_VERSION}, interpreter reports {version!r}")
    work = ROOT / "target" / f"{WORK_TAG}-openmc-{model}"
    (work / "tmp").mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, OPENMC_CROSS_SECTIONS=str(XS_XML), OMP_NUM_THREADS=str(THREADS), OPENBLAS_NUM_THREADS="1",
               MKL_NUM_THREADS="1", PYTHONHASHSEED="0", TMPDIR=str(work / "tmp"))
    iterations, final, batches, started = [], None, BATCH_START, time.monotonic()
    xs_job = work / "omc_xs"
    xs_job.mkdir(parents=True, exist_ok=True)
    (xs_job / "job.json").write_text(json.dumps({"model": model, "result": str(xs_job / "result.json")}))
    r = subprocess.run(scoped([OPENMC_PY, Path(__file__).resolve(), "omc-xs", xs_job / "job.json"]),
                       capture_output=True, text=True, timeout=LIMIT_S, env=env)
    if r.returncode != 0:
        sys.exit(f"p173_ce_rates: OpenMC cross-section collapse failed for model {model}: {r.stderr[-400:]}")
    sigma_omc = json.loads((xs_job / "result.json").read_text())
    while True:
        sub = work / f"b{batches}"
        sub.mkdir(parents=True, exist_ok=True)
        job = {"model": model, "batches": batches, "particles_per_batch": PARTICLES_PER_BATCH,
               "work_dir": str(sub / "run"), "result": str(sub / "result.json")}
        (sub / "job.json").write_text(json.dumps(job))
        t0 = time.monotonic()
        r = subprocess.run(scoped([OPENMC_PY, Path(__file__).resolve(), "openmc-run", sub / "job.json"]),
                           capture_output=True, text=True, timeout=LIMIT_S, env=env)
        (sub / "run.stdout").write_text(r.stdout)
        (sub / "run.stderr").write_text(r.stderr)
        info = {"batches": batches, "particles": batches * PARTICLES_PER_BATCH, "returncode": r.returncode,
                "wall_s": time.monotonic() - t0}
        if r.returncode != 0:
            info["stderr_tail"] = r.stderr[-400:]
            iterations.append(info)
            break
        final = json.loads((sub / "result.json").read_text())
        info.update(convergence(final["rates"], final["plan"]), child_cgroup=final["cgroup"],
                    model_xml_sha256=final["model_xml_sha256"])
        iterations.append(info)
        if info["all_converged"] or batches >= BATCH_MAX:
            break
        batches *= 2
    document = {"schema": SCHEMA + "-openmc", "model": model, "openmc_version": version,
                "python_executable": str(OPENMC_PY), "particles_per_batch": PARTICLES_PER_BATCH,
                "batch_max": BATCH_MAX, "rel_se_target": REL_SE_TARGET, "wall_s": time.monotonic() - started,
                "iterations": iterations, "final": final, "sigma_omc": sigma_omc,
                "environment": environment_record(cg)}
    write_atomic(out, document)
    if final is None:
        sys.exit(f"p173_ce_rates: OpenMC run failed for model {model}: {iterations[-1].get('stderr_tail')}")


def omc_xs(job_path):
    """Run with the OpenMC interpreter: OpenMC's own 294 K pointwise cross section of every listed reaction collapsed to
    the fispact-709 groups with the flat-lethargy weight (`lethargy_group_average`). null for a reaction the HDF5 file
    does not hold."""
    import h5py
    job = json.loads(Path(job_path).read_text())
    bounds = groups_ascending()
    out = {}
    for target, mt, _ in model_spec(job["model"])["reactions"]:
        with h5py.File(XS_XML.parent / "neutron" / f"{target}.h5", "r") as f:
            reactions = f[target]["reactions"]
            name = f"reaction_{mt:03d}"
            if name not in reactions or "294K" not in reactions[name]:
                out[rate_key(target, mt)] = None
                continue
            dataset = reactions[name]["294K"]["xs"]
            start = int(dataset.attrs["threshold_idx"])
            xs = [float(v) for v in dataset[()]]
            energy = [float(v) for v in f[target]["energy"]["294K"][start:start + len(xs)]]
        out[rate_key(target, mt)] = lethargy_group_average(energy, xs, bounds)
    write_atomic(job["result"], out)


def openmc_run(job_path):
    """Run with the OpenMC interpreter: build `job['model']`, run it at `job['batches']` batches, write the tallies."""
    import h5py
    import openmc
    job = json.loads(Path(job_path).read_text())
    spec = model_spec(job["model"])
    work = Path(job["work_dir"])
    work.mkdir(parents=True, exist_ok=True)
    bounds = groups_ascending()

    material = openmc.Material(name=MATERIALS[spec["material"]]["label"])
    for element, fraction in final_wo(spec["material"]).items():
        material.add_element(element, fraction, "wo", cross_sections=str(XS_XML))
    material.set_density("g/cm3", MATERIALS[spec["material"]]["density_g_cm3"])
    atom_densities = {k: float(v) for k, v in material.get_nuclide_atom_densities().items()}

    lay = layout(spec["shells"])
    spheres = [openmc.Sphere(r=float(r)) for r in [lay[0][0]] + [b for _, b, _ in lay]]
    spheres[-1].boundary_type = "vacuum"
    cells = [openmc.Cell(name="void", region=-spheres[0])]
    tally_cells = []
    for i, (a, b, tallied) in enumerate(lay):
        cell = openmc.Cell(name=f"shell {a:g}-{b:g}", fill=material, region=+spheres[i] & -spheres[i + 1])
        cells.append(cell)
        if tallied:
            tally_cells.append((region_name(job["model"], a, b), cell))
    geometry = openmc.Geometry(openmc.Universe(cells=cells))
    cell_filter = openmc.CellFilter([cell for _, cell in tally_cells])

    flux = openmc.Tally(name="flux")
    flux.filters = [cell_filter, openmc.EnergyFilter(bounds)]
    flux.scores = ["flux"]
    flux.estimator = "tracklength"
    tallies = [flux]

    plan, by_mt = {}, {}
    for target, mt, _ in spec["reactions"]:
        key = rate_key(target, mt)
        if target not in atom_densities:
            plan[key] = {"status": "not_tallied", "reason_code": "target_absent",
                         "reason": f"{target} is not in the material composition"}
            continue
        with h5py.File(XS_XML.parent / "neutron" / f"{target}.h5", "r") as f:
            has = f"reaction_{mt:03d}" in f[target]["reactions"]
        if not has:
            plan[key] = {"status": "not_tallied", "reason_code": "reaction_absent_openmc",
                         "reason": f"MT {mt} is not in OpenMC's evaluation of {target}"}
            continue
        plan[key] = {"status": "tallied"}
        by_mt.setdefault(mt, set()).add(target)
    rr_tallies = {}
    for mt, targets in sorted(by_mt.items()):
        tally = openmc.Tally(name=f"rr{mt}")
        tally.filters = [cell_filter, openmc.EnergyFilter([bounds[0], bounds[-1]])]
        tally.nuclides = sorted(targets)
        tally.scores = [MT_SCORE[mt]]
        tally.multiply_density = False
        tally.estimator = "tracklength"
        tallies.append(tally)
        rr_tallies[mt] = tally

    settings = openmc.Settings()
    settings.run_mode = "fixed source"
    settings.particles = int(job["particles_per_batch"])
    settings.batches = int(job["batches"])
    settings.seed = spec["seed"]
    settings.source = openmc.IndependentSource(
        space=openmc.stats.Point((0.0, 0.0, 0.0)), angle=openmc.stats.Isotropic(),
        energy=openmc.stats.Discrete([SOURCE_ENERGY_EV], [1.0]), particle="neutron")
    materials = openmc.Materials([material])
    materials.cross_sections = str(XS_XML)
    model = openmc.Model(geometry=geometry, materials=materials, settings=settings,
                         tallies=openmc.Tallies(tallies))
    t0 = time.monotonic()
    sp_path = model.run(cwd=str(work), threads=THREADS, openmc_exec=str(OPENMC_EXEC))
    wall = time.monotonic() - t0

    result_flux, result_rates = {}, {name: {} for name, _ in tally_cells}
    with openmc.StatePoint(sp_path) as sp:
        t_flux = sp.tallies[flux.id]
        order = [int(b) for b in t_flux.filters[0].bins]
        mean, sd = t_flux.get_reshaped_data("mean"), t_flux.get_reshaped_data("std_dev")  # (cells, groups, 1, 1)
        for name, cell in tally_cells:
            ci = order.index(cell.id)
            result_flux[name] = {"mean": [float(v) for v in mean[ci, :, 0, 0]],
                                 "std_dev": [float(v) for v in sd[ci, :, 0, 0]]}
        for mt, tally in rr_tallies.items():
            t = sp.tallies[tally.id]
            order = [int(b) for b in t.filters[0].bins]
            m, s = t.get_reshaped_data("mean"), t.get_reshaped_data("std_dev")  # (cells, 1, nuclides, 1)
            for name, cell in tally_cells:
                ci = order.index(cell.id)
                for ni, nuclide in enumerate(t.nuclides):
                    result_rates[name][rate_key(nuclide, mt)] = {"mean": float(m[ci, 0, ni, 0]),
                                                                 "std_dev": float(s[ci, 0, ni, 0])}
    for name in result_rates:  # a reaction that was not tallied has no rate; keep the key set complete
        for key in plan:
            result_rates[name].setdefault(key, {"mean": 0.0, "std_dev": 0.0})
    document = {"model": job["model"], "batches": int(job["batches"]), "particles_per_batch": settings.particles,
                "seed": spec["seed"], "wall_s": wall, "cgroup": cgroup_record(), "openmc_version": openmc.__version__,
                "atom_densities_atom_b_cm": atom_densities,
                "density_g_cm3": MATERIALS[spec["material"]]["density_g_cm3"],
                "regions": [{"region": name, "cell_id": cell.id} for name, cell in tally_cells],
                "flux": result_flux, "rates": result_rates, "plan": plan,
                "model_xml_sha256": sha256_file(Path(work) / "model.xml")}
    write_atomic(job["result"], document)


# ------------------------------------------------------------------------------------------------------ ACTINV arm

def run_actinv_model(model, out):
    import numpy as np
    cg = require_bounded()
    om = json.loads((OUT_DIR / f"openmc_{model}.json").read_text())
    final = om["final"]
    omc = om["sigma_omc"]
    if final is None:
        sys.exit(f"p173_ce_rates: no OpenMC result for model {model}")
    lib = np.load(ENDF8_NPZ)
    rows, sig, bounds = lib["rows"].tolist(), lib["sig"], [float(v) for v in lib["bounds"]]
    if bounds != groups_ascending():
        sys.exit("p173_ce_rates: the library's group boundaries are not the fispact-709 boundaries")
    targets = json.loads(ENDF8_INDEX.read_text())["targets"]
    selection = library_selection(rows)
    regions = {}
    for region, flux in final["flux"].items():
        regions[region] = {rate_key(t, mt): actinv_reaction(rows, sig, targets, selection, t, mt, flux["mean"],
                                                            flux["std_dev"], omc.get(rate_key(t, mt)))
                           for t, mt, _ in model_spec(model)["reactions"]}
    document = {"schema": SCHEMA + "-actinv", "model": model, "regions": regions, "bounds_match_groups": True,
                "library_sha256": sha256_file(ENDF8_NPZ), "index_sha256": sha256_file(ENDF8_INDEX),
                "environment": environment_record(cg)}
    write_atomic(out, document)


# ------------------------------------------------------------------------------------------------------ G1 collapse check

def inspect_rate(rows_json, mt):
    """The rate per target atom `actinv inspect xs` reports for one MT: the sum over its product rows (ZAP >= 0), or
    over its total rows when there is none. Returns None when the output has no row of that MT."""
    chosen = [r for r in rows_json if r["mt"] == mt]
    prod = [r for r in chosen if r["product_za"] >= 0]
    use = prod or chosen
    return math.fsum(r["reaction_rate_per_target_atom_per_s"] for r in use) if use else None


def run_g1_check(out):
    cg = require_bounded()
    work = ROOT / "target" / f"{WORK_TAG}-g1"
    work.mkdir(parents=True, exist_ok=True)
    version = subprocess.run([str(ACTINV_BIN), "--version"], capture_output=True, text=True, timeout=60).stdout.strip()
    bounds = groups_ascending()
    checks = []
    for model in MODEL_ORDER:
        om = json.loads((OUT_DIR / f"openmc_{model}.json").read_text())
        am = json.loads((OUT_DIR / f"actinv_{model}.json").read_text())
        region = regions_of(model)[0]
        flux = om["final"]["flux"][region]["mean"]
        spectrum = work / f"{model.replace(':', '_')}_spectrum.json"
        spectrum.write_text(json.dumps({"structure": "custom", "boundaries_eV": bounds, "flux_per_group": flux,
                                        "descending": False}))
        reactions, max_diff = [], 0.0
        for target in sorted({t for t, _, _ in model_spec(model)["reactions"]}):
            result = work / f"{model}_{target}.json"
            run = subprocess.run([str(ACTINV_BIN), "inspect", "xs", str(ENDF8_NPZ), "--target", target, "--spectrum",
                                  str(spectrum), "--out", str(result)], capture_output=True, text=True, timeout=300)
            if run.returncode != 0:
                reactions.append({"target": target, "error": run.stderr[-300:]})
                max_diff = math.inf
                continue
            rows_json = json.loads(result.read_text())["reactions"]
            for t, mt, _ in model_spec(model)["reactions"]:
                res = am["regions"][region][rate_key(t, mt)]
                if t != target or res.get("status") != "ok":
                    continue
                theirs = inspect_rate(rows_json, mt)
                ours = res["R_actinv"] * 1e-24
                diff = math.inf if theirs is None else (0.0 if ours == theirs == 0.0 else
                                                        abs(ours - theirs) / max(abs(ours), abs(theirs)))
                max_diff = max(max_diff, diff)
                reactions.append({"target": t, "mt": mt, "harness_rate_per_atom": ours,
                                  "actinv_rate_per_atom": theirs, "rel_diff": diff})
        checks.append({"model": model, "region": region, "reactions": reactions, "max_rel_diff": max_diff})
    document = {"schema": SCHEMA + "-g1", "tolerance": G1_TOLERANCE, "actinv_bin": str(ACTINV_BIN),
                "actinv_bin_sha256": sha256_file(ACTINV_BIN), "actinv_version": version, "checks": checks,
                "environment": environment_record(cg)}
    write_atomic(out, document)


# ------------------------------------------------------------------------------------------------------ assembly

def assemble(record_path):
    bounds = groups_ascending()
    inputs = {name: {"path": str(path), "sha256": sha256_file(path)} for name, (path, _) in PINNED.items()}
    g1 = json.loads((OUT_DIR / "g1_check.json").read_text())
    rows, models, cgroups = [], {}, []
    openmc_version = None
    for model in MODEL_ORDER:
        om = json.loads((OUT_DIR / f"openmc_{model}.json").read_text())
        am = json.loads((OUT_DIR / f"actinv_{model}.json").read_text())
        final, spec = om["final"], model_spec(model)
        openmc_version = om["openmc_version"]
        cgroups.append({"where": f"openmc:{model}", "cgroup": om["environment"]["cgroup"]})
        cgroups += [{"where": f"openmc_child:{model}:{it['batches']}", "cgroup": it.get("child_cgroup")}
                    for it in om["iterations"]]
        cgroups.append({"where": f"actinv:{model}", "cgroup": am["environment"]["cgroup"]})
        energies = {region: mean_energy_eV(bounds, flux["mean"]) for region, flux in final["flux"].items()}
        for region in regions_of(model):
            for target, mt, product in spec["reactions"]:
                key = rate_key(target, mt)
                rows.append(build_row(model, region, target, mt, product, energies[region], final["plan"][key],
                                      final["rates"][region][key], am["regions"][region][key]))
        models[model] = {"material": spec["material"], "density_g_cm3": final["density_g_cm3"],
                         "atom_densities_atom_b_cm": final["atom_densities_atom_b_cm"],
                         "final_batches": final["batches"], "particles_per_batch": final["particles_per_batch"],
                         "iterations": [{k: v for k, v in it.items() if k not in ("child_cgroup", "stderr_tail")}
                                        for it in om["iterations"]],
                         "total_flux_cm_per_source": {r: math.fsum(f["mean"]) for r, f in final["flux"].items()},
                         "mean_energy_eV": energies, "wall_s": om["wall_s"]}
    cgroups.append({"where": "g1_check", "cgroup": g1["environment"]["cgroup"]})
    env = environment_record(require_bounded())
    document = {"schema": SCHEMA, "inputs": inputs,
                "environment": {**env, "openmc_version": openmc_version, "openmc_python": str(OPENMC_PY),
                                "cgroups": cgroups, "groups_match_library": True,
                                "actinv_bin_sha256": g1["actinv_bin_sha256"], "actinv_version": g1["actinv_version"]},
                "normalisation": "per source particle, per target atom, barn cm",
                "g1_check": {"tolerance": g1["tolerance"], "checks": g1["checks"]},
                "models": models, "rows": rows}
    write_atomic(record_path, document)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="mode", required=True)
    for name in ("openmc", "actinv"):
        p = sub.add_parser(name)
        p.add_argument("--model", choices=["S", "W", "C", "K"], required=True)
    sub.add_parser("g1-check")
    sub.add_parser("assemble")
    for name in ("openmc-run", "omc-xs"):
        sub.add_parser(name).add_argument("job")
    args = ap.parse_args(argv)
    if args.mode == "openmc":
        run_openmc_model(args.model, OUT_DIR / f"openmc_{args.model}.json")
    elif args.mode == "actinv":
        run_actinv_model(args.model, OUT_DIR / f"actinv_{args.model}.json")
    elif args.mode == "g1-check":
        run_g1_check(OUT_DIR / "g1_check.json")
    elif args.mode == "assemble":
        assemble(RECORD)
    elif args.mode == "omc-xs":
        omc_xs(args.job)
    else:
        openmc_run(args.job)


if __name__ == "__main__":
    main()
