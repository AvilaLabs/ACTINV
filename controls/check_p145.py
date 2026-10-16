#!/usr/bin/env python3
"""P145: waste classification under nuclear-data uncertainty. Evidence gates U1 to U4 and the verdict. Each gate writes
results/p145/<gate>.json with a boolean `pass` and the candidate's provenance. Children (`actinv waste uncertainty`,
`actinv study run`) run serially with a timeout, one per call, in a staging folder under target/p145 that holds copies
of the case inputs and a link `actinv-data` to the data tree (P145_DATA, default ./actinv-data), so a relative library
path resolves the same way from the current directory and from the spec folder. Launch inside the required systemd
cgroup; the children inherit that scope.

    python3 controls/check_p145.py u1 C1|C2     # kept samples equal study.robustness samples
    python3 controls/check_p145.py u2 C1|C2     # independent tally of the kept samples (uses the u1 run if present)
    python3 controls/check_p145.py u3           # C3 calibration against the closed form
    python3 controls/check_p145.py u4           # refusals, zero-variance invariance, repeat identity
    python3 controls/check_p145.py verdict CI_RUNS.json   # needs results/p145/u5.json from the coordinator
    python3 controls/check_p145.py selftest     # pure Python, no data and no binary

Decisions recorded in every record that uses them:
  * U1 compares each rob_<i>.out.json to the study's byte for byte after deleting the top-level `ms` line (wall-clock
    time, different on every run); `strict_identical` counts the pairs that are identical without that deletion. The
    problem copy staged for U1 carries the title the study gives its case ("<study_id> :: m__s__t"), because
    `spec_title` is part of every result; nothing else in the problem changes.
  * Keys starting with an underscore (`_note`) are removed from the staged copies of a spec and its problem.
  * U2 takes nuclide properties from the spec, or for "from_decay_data" from the decay files named by the problem:
    half-life from MF8/MT457 and alpha_emitting when any decay mode with a positive branching ratio has the digit 4 in
    its RTYP (the rule of the composition-solve command), decay overrides unsupported.
  * Percentiles, count and fraction of "normalized sum > 1" run over the samples whose solve succeeded, whether or not
    their class is unknown (a missing nuclide property or required external H-3 makes the class unknown but leaves the
    calculated sums). PERCENTILES_INCLUDE_UNKNOWN switches that.

Result field names are read in the adapter functions `a_*` below, one per field group; edit only those to align names.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import zipfile
from decimal import Decimal, localcontext
from fractions import Fraction
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results/p145"
WORK = ROOT / "target/p145"
INPUTS = ROOT / "controls/p145_inputs"
PROTOCOL = ROOT / "protocols/ACTINV-P145_PROTOCOL.md"
AMENDMENT = ROOT / "protocols/ACTINV-P145_AMENDMENT_A.md"
AMENDMENT_B = ROOT / "protocols/ACTINV-P145_AMENDMENT_B.md"
RULES_FILE = ROOT / "crates/actinv-core/data/waste_us_nrc_61_55_v1.json"
BUNDLED_RULES_ID = "us-nrc-10cfr61.55-v1"
BINARY = Path(os.environ.get("ACTINV_BIN", ROOT / "target/release/actinv"))
TIMEOUT = int(os.environ.get("P145_TIMEOUT", "3600"))
CASES = {"C1": "c1_fns_fe.json", "C2": "c2_steel.json"}
RESULT_SCHEMA = "actinv-waste-uncertainty-result-1"
SPEC_SCHEMA = "actinv-waste-uncertainty-spec-1"
CLASSES = ["A", "B", "C", "above_class_c"]
ALL_CLASSES = CLASSES + ["unknown"]
PERCENTILES = (5, 50, 95)
PERCENTILES_INCLUDE_UNKNOWN = True
CP_ENDPOINT_TOL = 1e-12
STUDY_KEYS = {"spec", "title", "projectile", "library", "decay", "material", "spectrum", "schedule", "options"}
CASE_ID = "m__s__t"
MS_LINE = re.compile(rb'(?m)^  "ms": [^\n]*\n')
MISSING = object()


# --- provenance ----------------------------------------------------------------------------------------------

def sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def tree_clean() -> bool:
    return git("status", "--porcelain", "--untracked-files=no") == ""


def maybe_sha(path: Path) -> str | None:
    return sha(path) if path.exists() else None


def candidate() -> dict:
    if not BINARY.is_file():
        raise SystemExit(f"binary {BINARY} not found (set ACTINV_BIN or build target/release/actinv)")
    return {"commit": git("rev-parse", "HEAD"), "tree_clean": tree_clean(),
            "binary": str(BINARY.relative_to(ROOT)) if BINARY.is_relative_to(ROOT) else str(BINARY),
            "binary_sha256": sha(BINARY), "protocol_sha256": sha(PROTOCOL), "amendment_sha256": maybe_sha(AMENDMENT),
            "amendment_b_sha256": maybe_sha(AMENDMENT_B)}


def dump(path: Path, doc) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")


def finish(name: str, report: dict) -> int:
    report["worktree_clean_after"] = tree_clean()
    report["pass"] = bool(report.get("pass")) and report["candidate"]["tree_clean"] and report[
        "candidate"]["amendment_sha256"] is not None and report["candidate"]["amendment_b_sha256"] is not None
    dump(OUT / f"{name}.json", report)
    print(json.dumps({"gate": name, "pass": report["pass"], **report.get("summary", {})}, sort_keys=True))
    return 0 if report["pass"] else 1


def run_child(argv: list, cwd: Path, timeout: int = TIMEOUT) -> dict:
    """One serial child. subprocess.run kills and reaps the child when the timeout expires."""
    started = time.monotonic()
    try:
        done = subprocess.run([str(a) for a in argv], cwd=cwd, capture_output=True, text=True, timeout=timeout)
        code, stdout, stderr = done.returncode, done.stdout, done.stderr
    except subprocess.TimeoutExpired as error:
        code, stdout, stderr = None, error.stdout or "", f"timeout after {timeout} s"
    return {"argv": [str(a) for a in argv], "rc": code, "seconds": round(time.monotonic() - started, 3),
            "stdout": stdout, "stderr": stderr}


def brief(child: dict) -> dict:
    return {"argv": child["argv"], "rc": child["rc"], "seconds": child["seconds"],
            "stderr_tail": child["stderr"][-600:], "stdout_bytes": len(child["stdout"])}


# --- staging -------------------------------------------------------------------------------------------------

def data_root() -> Path:
    path = Path(os.environ.get("P145_DATA", ROOT / "actinv-data"))
    if not path.is_dir():
        raise SystemExit(f"data tree {path} not found (set P145_DATA to the actinv-data directory)")
    return path.resolve()


def strip_private(doc: dict) -> dict:
    return {k: v for k, v in doc.items() if not k.startswith("_")}


def fresh_stage(label: str) -> Path:
    stage = WORK / label
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    (stage / "actinv-data").symlink_to(data_root())
    return stage


def resolve_input(spec_dir: Path, relative: str) -> Path:
    """A path named in a committed spec; a path through `actinv-data` is mapped onto the data tree."""
    normal = Path(os.path.normpath(spec_dir / relative))
    if "actinv-data" in normal.parts:
        tail = normal.parts[normal.parts.index("actinv-data") + 1:]
        return data_root().joinpath(*tail)
    return normal


def c3_sidecar(name: str):
    """A writer for a C3 synthetic covariance sidecar named `name` (returns its SHA-256), or None for any other file.
    The sidecars are generated by controls/p145_c3_make.py rather than tracked (P145 Amendment C)."""
    meta = INPUTS / "c3_cases.json"
    if not meta.exists():
        return None
    sigma2 = {entry["covariance"]: entry["sigma2"] for entry in json.loads(meta.read_text())["cases"]}.get(name)
    if sigma2 is None:
        return None
    sys.path.insert(0, str(ROOT / "controls"))
    import p145_c3_make
    return lambda path: p145_c3_make.write_covariance(path, sigma2)


def stage_case(spec_path: Path, stage: Path, *, title: str | None = None, keep: bool = False, edit=None) -> dict:
    """Copy a committed uncertainty spec, its problem and covariance into `stage` as spec.json, problem.json and
    either an `actinv-data/...` path or a copy; apply the optional `edit(spec, problem)` before writing."""
    spec = strip_private(json.loads(spec_path.read_text()))
    problem = strip_private(json.loads((spec_path.parent / spec["problem"]).read_text()))
    if title is not None:
        problem["title"] = title
    sampling = spec["sampling"]
    covariance = sampling.get("covariance")
    sidecar = c3_sidecar(Path(covariance["path"]).name) if covariance else None
    if sidecar is not None:
        # generated, not tracked (Amendment C): the bytes must have the SHA-256 the spec pins
        made = sidecar(stage / Path(covariance["path"]).name)
        if made != covariance["sha256"]:
            raise SystemExit(f"generated {Path(covariance['path']).name} has SHA-256 {made}, not the pinned one")
        covariance["path"] = Path(covariance["path"]).name
    elif covariance:
        source = resolve_input(spec_path.parent, covariance["path"])
        if not source.is_file():
            raise SystemExit(f"covariance {source} not found")
        if sha(source) != covariance["sha256"]:
            raise SystemExit(f"covariance {source} differs from the sha256 in {spec_path.name}")
        try:
            covariance["path"] = "actinv-data/" + source.relative_to(data_root()).as_posix()
        except ValueError:
            shutil.copyfile(source, stage / source.name)
            covariance["path"] = source.name
    spec["problem"] = "problem.json"
    if keep:
        sampling["keep_samples"] = "keep"
        (stage / "keep").mkdir(exist_ok=True)
    else:
        sampling.pop("keep_samples", None)
    if edit is not None:
        edit(spec, problem)
    dump(stage / "problem.json", problem)
    dump(stage / "spec.json", spec)
    return {"stage": stage, "spec": spec, "problem": problem, "source_spec_sha256": sha(spec_path),
            "spec_sha256": sha(stage / "spec.json"), "problem_sha256": sha(stage / "problem.json")}


def source_problem_sha(spec_path: Path) -> str:
    """sha256 of the committed problem file a committed uncertainty spec names."""
    return sha(spec_path.parent / json.loads(spec_path.read_text())["problem"])


def case_path(case: str) -> Path:
    key = case.upper()
    if key not in CASES:
        raise SystemExit(f"unknown case {case!r}; choose from {sorted(CASES)}")
    return INPUTS / CASES[key]


def study_id_for(case: str) -> str:
    return f"p145-u1-{case.lower()}"


def build_study(problem: dict, spec: dict, study_id: str) -> dict:
    """The study.robustness twin of an uncertainty spec's sampling block: one material, one spectrum (spec_ref to the
    staged problem) and one schedule that carries every problem step, so its case spec equals the problem."""
    extra = set(problem) - STUDY_KEYS
    material = problem["material"]
    if extra or set(material) - {"mass_g", "basis", "composition"} or material.get("basis") != "wt_percent":
        raise SystemExit(f"problem uses fields a study case cannot reproduce: {sorted(extra)} {sorted(material)}")
    if problem.get("projectile", "neutron") != "neutron":
        raise SystemExit("problem projectile is not neutron")
    sampling = spec["sampling"]
    robustness = {"samples": sampling["samples"], "seed": sampling["seed"], "channels": sampling["channels"],
                  "responses": ["total_activity_bq_per_g"], "first_order_comparison": False}
    if sampling.get("covariance"):
        robustness["covariance"] = sampling["covariance"]
    return {"study": "actinv-study-1", "study_id": study_id, "library": problem["library"],
            "decay": problem["decay"], "options": problem.get("options", {}),
            "cases": {"materials": [{"name": "m", "mass_g": material["mass_g"],
                                     "composition": material["composition"]}],
                      "spectra": [{"name": "s", "spec_ref": "problem.json"}],
                      "schedules": [{"name": "t", "steps": problem["schedule"]}]},
            "responses": ["total_activity_bq_per_g"], "robustness": robustness}


def run_uncertainty(stage: Path, spec_name: str = "spec.json", out_name: str = "result.json") -> dict:
    return run_child([BINARY, "waste", "uncertainty", spec_name, out_name], stage)


def load_result(stage: Path, name: str = "result.json") -> dict:
    return json.loads((stage / name).read_text())


# --- statistics: regularized incomplete beta, Clopper-Pearson, nearest rank -------------------------------------

def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction of the incomplete beta function (modified Lentz)."""
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 200000):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-16:
            return h
    raise ArithmeticError("incomplete beta continued fraction did not converge")


def betai(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta I_x(a, b) in double precision."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    front = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def binom_tail_ge(k: int, n: int, x: Decimal) -> Decimal:
    """P(Bin(n, x) >= k) = I_x(k, n - k + 1) by summing the pmf in 60-digit decimal arithmetic."""
    with localcontext() as ctx:
        ctx.prec = 60
        q = Decimal(1) - x
        ratio = x / q
        pmf = q ** n
        total = Decimal(0)
        for j in range(n + 1):
            if j >= k:
                total += pmf
            pmf = pmf * (n - j) / (j + 1) * ratio
        return total


def _solve_tail(k: int, n: int, target: Decimal) -> float:
    """The x in (0, 1) with P(Bin(n, x) >= k) = target, by bisection in decimal arithmetic."""
    with localcontext() as ctx:
        ctx.prec = 60
        low, high = Decimal(0), Decimal(1)
        for _ in range(80):
            mid = (low + high) / 2
            if binom_tail_ge(k, n, mid) < target:
                low = mid
            else:
                high = mid
        return float((low + high) / 2)


@lru_cache(maxsize=None)
def cp_interval(k: int, n: int, level: float) -> tuple[float, float]:
    """Two-sided equal-tailed Clopper-Pearson interval (Amendment A): lower end the alpha/2 quantile of
    Beta(k, n - k + 1) (0 for k = 0), upper end the 1 - alpha/2 quantile of Beta(k + 1, n - k) (1 for k = n)."""
    half = (Decimal(1) - Decimal(repr(level))) / 2
    lower = 0.0 if k == 0 else _solve_tail(k, n, half)
    upper = 1.0 if k == n else _solve_tail(k + 1, n, Decimal(1) - half)
    return lower, upper


def percentile_rank(n: int, p) -> int:
    """max(1, ceil(n p / 100)) in exact arithmetic on the decimal value of p (Hyndman-Fan type 1)."""
    return max(1, math.ceil(Fraction(repr(p)) * n / 100))


def percentile(sorted_values: list[float], p) -> float:
    return sorted_values[min(percentile_rank(len(sorted_values), p), len(sorted_values)) - 1]


def minimum_samples(level: float) -> int:
    """Smallest n with (alpha/2)^(1/n) >= level: every sample at most X is needed to establish X."""
    return math.ceil(math.log((1.0 - level) / 2.0) / math.log(level))


def established_class(cumulative_counts: dict, n: int, level: float):
    """First class X (A, B, C, above Class C) whose cumulative Clopper-Pearson lower end is at least `level`."""
    near = []
    for cls in CLASSES:
        lower = cp_interval(cumulative_counts[cls], n, level)[0]
        if abs(lower - level) < 1e-9:
            near.append(cls)
        if lower >= level:
            return cls, near
    return None, near


def needed_samples(k: int, n: int, level: float, limit: int = 1_000_000):
    """Amendment A: for p_hat = k/n > level, the smallest n' <= limit for which k' = floor(p_hat n') establishes the
    class (lower end of the interval >= level, i.e. I_level(k', n' - k' + 1) <= alpha/2). Full scan when the crossing
    is below 30 000, otherwise bisection on the crossing and an exhaustive scan of a window around it."""
    if k <= level * n:
        return None, "p_hat does not exceed the confidence level"
    half = (1.0 - level) / 2.0

    def ok(m: int) -> bool:
        kk = (k * m) // n
        return kk >= 1 and betai(kk, m - kk + 1, level) <= half

    hi = n
    while hi <= limit and not ok(hi):
        hi *= 2
    if hi > limit:
        if not ok(limit):
            return None, f"not established by {limit} samples"
        hi = limit
    if hi <= 30000:
        for m in range(n, hi + 1):
            if ok(m):
                return m, "full scan"
    low = max(n, hi // 2)
    while hi - low > 1:
        mid = (low + hi) // 2
        low, hi = (low, mid) if ok(mid) else (mid, hi)
    window = max(300, hi // 50)
    for m in range(max(n, hi - window), min(limit, hi + window) + 1):
        if ok(m):
            return m, f"bisection and exhaustive window of {window}"
    return None, "no n' in the search window"


# --- the rule pack and the nominal evaluator (independent of the Rust source) -----------------------------------

ELEMENTS = ("H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Br Kr Rb Sr Y "
            "Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re "
            "Os Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr Rf Db Sg Bh Hs Mt Ds "
            "Rg Cn Nh Fl Mc Lv Ts Og").split()
Z_OF = {symbol: index + 1 for index, symbol in enumerate(ELEMENTS)}
NUCLIDE = re.compile(r"^([A-Za-z]{1,2})(\d+)(?:[mM](\d*))?$")
SHORT, ALPHA = "half_life_lt5y", "alpha_transuranic_gt5y"


def canonical_nuclide(raw: str) -> str:
    """Rule-pack dashed keys ('C-14') and ACTINV keys ('C14', 'Ag110m1') to the canonical ACTINV key."""
    text = raw.replace("-", "", 1) if "-" in raw else raw
    match = NUCLIDE.match(text)
    if not match or match.group(1).capitalize() not in Z_OF:
        raise ValueError(f"invalid nuclide identity {raw!r}")
    symbol, mass, state = match.group(1).capitalize(), int(match.group(2)), match.group(3)
    if state is None:
        return f"{symbol}{mass}"
    return f"{symbol}{mass}m{int(state) if state else 1}"


def nuclide_za_liso(name: str) -> tuple[int, int]:
    match = NUCLIDE.match(name)
    symbol, mass, state = match.group(1).capitalize(), int(match.group(2)), match.group(3)
    return Z_OF[symbol] * 1000 + mass, 0 if state is None else (int(state) if state else 1)


class Rules:
    """The bundled 10 CFR 61.55 rule pack, read from crates/actinv-core/data/waste_us_nrc_61_55_v1.json."""

    def __init__(self, path: Path):
        doc = json.loads(path.read_text())
        self.id, self.ci_bq, self.year_s, self.rows = doc["id"], doc["ci_bq"], doc["year_s"], doc["rows"]
        self.sha256 = sha(path)

    def applicable(self, table: int, waste_type: str):
        return [r for r in self.rows if r["table"] == table and r["applicability"] in ("all", waste_type)]


def load_rules(rules: str, base: Path) -> Rules:
    return Rules(RULES_FILE if rules == BUNDLED_RULES_ID else base / rules)


def passes(normalized: float, contributors: int) -> bool:
    """A mixture of more than one named contributor must stay strictly below 1; a single one may equal 1."""
    return normalized < 1.0 if contributors > 1 else normalized <= 1.0


def evaluate(rules: Rules, waste_type: str, mass_g: float, volume_cm3: float, activities_bq: dict, properties: dict):
    """The Part 61 evaluation of one inventory. `properties` maps canonical key -> (z, half_life_s, alpha_emitting).

    Returns {"class", "calculated_only_class", "unknown_nuclides", "constraints"}; constraints is a list of
    {target_class, table, column, normalized_sum, strict, passes, contributors} in the evaluator's A, B, C order.
    Sums accumulate over nuclides in sorted key order, as the Rust BTreeMap iteration does.
    """
    ci, year = rules.ci_bq, rules.year_s
    activity, props = {}, {canonical_nuclide(k): v for k, v in properties.items()}
    for raw, value in activities_bq.items():
        key = canonical_nuclide(raw)
        if key in activity:
            raise ValueError(f"duplicate activity key {key}")
        activity[key] = value
    unknown, nuclides = [], {}
    for key in sorted(activity):
        if activity[key] == 0.0:
            continue
        if key in props:
            nuclides[key] = (activity[key], props[key])
        else:
            unknown.append(key)

    def fraction(bq: float, row: dict, limit: float) -> float:
        if row["unit"] == "Ci/m3":
            return bq / (limit * (ci / 1.0e6) * volume_cm3)
        return bq / (limit * (ci / 1.0e9) * mass_g)

    t1_sum, t1_n, t2_sums, t2_n = 0.0, 0, [0.0, 0.0, 0.0], [0, 0, 0]
    t1_rows, t2_rows = rules.applicable(1, waste_type), rules.applicable(2, waste_type)
    for key in sorted(nuclides):
        bq, (z, half_life_s, alpha_emitting) = nuclides[key]
        years = half_life_s / year
        named = [r for r in t1_rows if r["selector"] != ALPHA and canonical_nuclide(r["selector"]) == key]
        alpha = [r for r in t1_rows if r["selector"] == ALPHA]
        if named:
            t1_sum += fraction(bq, named[0], named[0]["limits"][0])
            t1_n += 1
        elif alpha and z > 92 and alpha_emitting and years > 5.0:
            t1_sum += fraction(bq, alpha[0], alpha[0]["limits"][0])
            t1_n += 1
        named2 = [r for r in t2_rows if r["selector"] != SHORT and canonical_nuclide(r["selector"]) == key]
        short = [r for r in t2_rows if r["selector"] == SHORT]
        row2 = named2[0] if named2 else (short[0] if short and years < 5.0 else None)
        if row2 is not None:
            for column in range(3):
                limit = row2["limits"][column]
                if limit is not None:
                    t2_sums[column] += fraction(bq, row2, limit)
                    t2_n[column] += 1
    constraints = []
    for index, target in enumerate(CLASSES[:3]):
        threshold = 1.0 if target == "C" else 0.1
        for table, column, total, count in ((1, None, t1_sum, t1_n), (2, index + 1, t2_sums[index], t2_n[index])):
            normalized = total / threshold if table == 1 else total
            constraints.append({"target_class": target, "table": table, "column": column,
                                "normalized_sum": normalized, "contributors": count, "strict": count > 1,
                                "passes": passes(normalized, count)})
    calculated = "above_class_c"
    for target in CLASSES[:3]:
        if all(c["passes"] for c in constraints if c["target_class"] == target):
            calculated = target
            break
    return {"class": "unknown" if unknown else calculated, "calculated_only_class": calculated,
            "unknown_nuclides": unknown, "constraints": constraints}


# --- nuclide properties from the decay files --------------------------------------------------------------------

def _endf_decay():
    spec = importlib.util.spec_from_file_location("p145_endf_decay", ROOT / "controls/endf_decay.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rtyp_has_alpha(rtyp: float) -> bool:
    return "4" in f"{rtyp:.6f}".rstrip("0").rstrip(".")


class DecayProperties:
    """Half-life, Z and alpha emission from the problem's primary and fallback decay files (first file wins)."""

    def __init__(self, decay: dict, base: Path):
        if decay.get("overrides"):
            raise SystemExit("decay overrides are not supported by the P145 checker")
        parser = _endf_decay()
        self.records = {}
        for field in ("primary", "fallback"):
            if decay.get(field):
                for record in parser.parse_decay_file(str(base / decay[field])).values():
                    self.records.setdefault((int(round(record["za"])), record["liso"]), record)

    def lookup(self, name: str):
        record = self.records.get(nuclide_za_liso(name))
        if record is None:
            return None
        if record["nst"] == 1 or not record["half_life"] > 0.0:
            raise ValueError(f"positive activity for stable or invalid decay state {name}")
        alpha = any(m["br"] > 0.0 and rtyp_has_alpha(m["rtyp"]) for m in record["modes"])
        return int(round(record["za"])) // 1000, record["half_life"], alpha


class Properties:
    def __init__(self, declared, problem: dict, base: Path):
        self.declared = None
        self.decay = None
        self.cache = {}
        if declared == "from_decay_data":
            self.decay = DecayProperties(problem["decay"], base)
        else:
            self.declared = {canonical_nuclide(k): (v["z"], v["half_life_s"], v["alpha_emitting"])
                             for k, v in declared.items()}

    def for_nuclides(self, names) -> dict:
        found = {}
        for name in names:
            key = canonical_nuclide(name)
            if self.declared is not None:
                if key in self.declared:
                    found[key] = self.declared[key]
            else:
                if key not in self.cache:
                    self.cache[key] = self.decay.lookup(key)
                if self.cache[key] is not None:
                    found[key] = self.cache[key]
        return found


# --- the independent tally ------------------------------------------------------------------------------------

def geometry(component: dict) -> tuple[float, float]:
    mass = component["mass_g"]
    volume = component.get("displaced_volume_cm3")
    density = component.get("density_g_cm3")
    if (volume is None) == (density is None):
        raise ValueError(f"component {component['id']} needs exactly one of displaced_volume_cm3 and density_g_cm3")
    return mass, volume if volume is not None else mass / density


def merge_external(component: dict, step: int, activities: dict):
    """Declared external H-3 added once, as the nominal command does; returns (activities, external_required)."""
    external = component["external_tritium"]
    status = external["status"]
    if status == "not_applicable":
        return activities, False
    if status == "required":
        return activities, True
    value = external["activity_bq"][str(step)]
    merged = dict(activities)
    old = 0.0
    for key in list(merged):
        if canonical_nuclide(key) == "H3":
            old = merged.pop(key)
    merged["H3"] = old + value
    return merged, False


class Tally:
    """Per component and target step: classify every kept sample and aggregate (expected values for the result)."""

    def __init__(self, spec: dict, problem: dict, base: Path, keep: Path):
        self.spec, self.base, self.keep = spec, base, keep
        self.n = spec["sampling"]["samples"]
        self.level = float(spec.get("confidence_level", 0.95))
        self.rules = load_rules(spec["rules"], base)
        self.properties = Properties(spec["nuclide_properties"], problem, base)
        self.anomalies = []

    def sample_steps(self, index: int):
        """step -> activity_Bq_per_g for one kept sample, or None when the sample has no file (a failed solve)."""
        path = self.keep / f"rob_{index}.out.json"
        if not path.exists():
            return None
        doc = json.loads(path.read_text())
        return {row["step"]: row["activity_Bq_per_g"] for row in doc["steps"]}

    def run(self) -> dict:
        samples = [self.sample_steps(i) for i in range(self.n)]
        table = {}
        for component in self.spec["components"]:
            mass, volume = geometry(component)
            for step in self.spec["targets"]:
                evals = []
                for index, steps in enumerate(samples):
                    if steps is None:
                        evals.append(None)
                        continue
                    if step not in steps:
                        self.anomalies.append(f"sample {index} has no step {step}")
                        evals.append(None)
                        continue
                    bq = {name: value * mass for name, value in steps[step].items()}
                    bq, required = merge_external(component, step, bq)
                    try:
                        props = self.properties.for_nuclides([k for k, v in bq.items() if v != 0.0])
                    except ValueError as error:
                        self.anomalies.append(f"sample {index}: {error}")
                        props = {}
                    result = evaluate(self.rules, component["waste_type"], mass, volume, bq, props)
                    if required:
                        result["class"] = "unknown"
                    evals.append(result)
                table[(component["id"], step)] = self.aggregate(evals)
        return table

    def aggregate(self, evals: list) -> dict:
        n, level = self.n, self.level
        counts = {c: 0 for c in ALL_CLASSES}
        for e in evals:
            counts["unknown" if e is None else e["class"]] += 1
        cumulative, running = {}, 0
        for cls in CLASSES:
            running += counts[cls]
            cumulative[cls] = running
        established, near = established_class(cumulative, n, level)
        block = {"samples": n, "failed": sum(e is None for e in evals), "counts": counts,
                 "probabilities": {c: counts[c] / n for c in ALL_CLASSES},
                 "intervals": {c: cp_interval(counts[c], n, level) for c in ALL_CLASSES},
                 "cumulative_counts": cumulative,
                 "cumulative_probabilities": {c: cumulative[c] / n for c in CLASSES},
                 "cumulative_intervals": {c: cp_interval(cumulative[c], n, level) for c in CLASSES},
                 "established": established, "established_near_threshold": near,
                 "minimum_samples": minimum_samples(level), "needed": None, "needed_note": None, "constraints": {}}
        if established is None:
            for cls in CLASSES:
                if cumulative[cls] / n > level:
                    block["needed"], block["needed_note"] = needed_samples(cumulative[cls], n, level)
                    block["needed_class"] = cls
                    break
        pool = [e for e in evals if e is not None and (PERCENTILES_INCLUDE_UNKNOWN or e["class"] != "unknown")]
        keys = sorted({(c["table"], c["column"], c["target_class"]) for e in pool for c in e["constraints"]},
                      key=lambda k: (k[0], k[1] or 0, k[2]))
        for key in keys:
            values = sorted(c["normalized_sum"] for e in pool for c in e["constraints"]
                            if (c["table"], c["column"], c["target_class"]) == key)
            over = sum(1 for v in values if v > 1.0)
            block["constraints"][key] = {"n": len(values),
                                         "percentiles": {p: percentile(values, p) for p in PERCENTILES},
                                         "count_gt1": over, "fraction_gt1": over / len(values)}
        return block


def earliest_steps(table: dict, component: str, targets: list) -> dict:
    """Per class, the earliest target step at which it is the established class ('equals') and the earliest at which
    the established class is at most that class ('at_most')."""
    ordered = sorted(targets)
    equals = {c: None for c in CLASSES}
    at_most = {c: None for c in CLASSES}
    for step in ordered:
        est = table[(component, step)]["established"]
        if est is None:
            continue
        if equals[est] is None:
            equals[est] = step
        for cls in CLASSES[CLASSES.index(est):]:
            if at_most[cls] is None:
                at_most[cls] = step
    return {"equals": equals, "at_most": at_most}


# --- adapters: the only code that names result fields ---------------------------------------------------------

def pick(obj, *names):
    if isinstance(obj, dict):
        for name in names:
            if name in obj:
                return obj[name]
    return MISSING


def a_components(result: dict) -> dict:
    comps = pick(result, "components")
    return {c["id"]: c for c in comps} if isinstance(comps, list) else {}


def a_targets(component: dict) -> dict:
    rows = pick(component, "targets")
    return {int(t["step"]): t for t in rows} if isinstance(rows, list) else {}


def a_nominal_class(target: dict):
    nominal = pick(target, "nominal")
    if nominal is MISSING:
        return MISSING
    value = pick(nominal, "class", "nominal_class")
    return nominal if isinstance(nominal, str) else value


def find_constraints(obj):
    """Every dict under `obj` that looks like a class constraint (has table and normalized_sum)."""
    if isinstance(obj, dict):
        if "table" in obj and "normalized_sum" in obj and not isinstance(obj["normalized_sum"], dict):
            yield obj
        for value in obj.values():
            yield from find_constraints(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from find_constraints(value)


def a_nominal_sum(target: dict, table: int, column, target_class: str):
    nominal = pick(target, "nominal")
    for c in find_constraints(nominal if nominal is not MISSING else {}):
        if c["table"] == table and c.get("column") == column and c.get("target_class") == target_class:
            return c["normalized_sum"]
    return MISSING


def a_sample_counts(target: dict):
    samples = pick(target, "samples", "n_samples")
    failed = pick(target, "failed_samples", "failures")
    if isinstance(failed, (list, dict)):
        failed = len(failed)
    return samples, failed


def interval_of(entry):
    if isinstance(entry, (list, tuple)) and len(entry) == 2:
        return tuple(entry)
    low = pick(entry, "lower", "lo", "lower_bound", "low")
    high = pick(entry, "upper", "hi", "upper_bound", "high")
    return (low, high) if low is not MISSING and high is not MISSING else MISSING


def entry_interval(entry):
    """The interval of a class entry: a nested interval object, or `lower`/`upper` beside count and probability."""
    nested = pick(entry, "interval", "ci", "confidence_interval", "clopper_pearson")
    return entry if nested is MISSING and isinstance(entry, dict) else nested


def class_entries(source) -> dict:
    """{class: entry dict} from a map keyed by class name (a leading 'at_most_' is ignored)."""
    if not isinstance(source, dict):
        return {}
    return {k.removeprefix("at_most_"): v for k, v in source.items()}


def a_class_stats(target: dict) -> dict:
    """class -> {count, probability, interval} for A, B, C, above_class_c, unknown."""
    nested = class_entries(pick(target, "classes", "class_statistics", "class_distribution"))
    counts = class_entries(pick(target, "class_counts", "counts"))
    probs = class_entries(pick(target, "class_probabilities", "probabilities"))
    cis = class_entries(pick(target, "class_intervals", "intervals", "confidence_intervals"))
    out = {}
    for cls in ALL_CLASSES:
        entry = nested.get(cls)
        out[cls] = {"count": pick(entry, "count", "n") if entry is not None else counts.get(cls, MISSING),
                    "probability": pick(entry, "probability", "p") if entry is not None else probs.get(cls, MISSING),
                    "interval": interval_of(entry_interval(entry) if entry is not None else cis.get(cls, MISSING))}
    return out


def a_cumulative_stats(target: dict) -> dict:
    """class -> {count, probability, interval} for P(class at most X), X in A, B, C, above_class_c."""
    nested = class_entries(pick(target, "at_most_class", "cumulative", "cumulative_classes", "at_most", "class_at_most"))
    out = {}
    for cls in CLASSES:
        entry = nested.get(cls)
        out[cls] = {"count": pick(entry, "count", "n"), "probability": pick(entry, "probability", "p"),
                    "interval": interval_of(entry_interval(entry))}
    return out


def a_established(target: dict) -> dict:
    """{class, needed, minimum_samples}; class is a class name or 'not_established'."""
    est = pick(target, "established_class", "established")
    cls = pick(est, "class", "value") if isinstance(est, dict) else est
    source = est if isinstance(est, dict) else target
    needed = pick(source, "samples_needed", "needed_samples", "n_needed", "samples_needed_if_observed_held")
    if isinstance(needed, dict):  # {"status", "class", "observed_frequency", "samples_needed"}
        needed = needed.get("samples_needed")
    return {"class": cls,
            "needed": needed,
            "minimum_samples": pick(target, "minimum_samples", "min_samples_to_establish")}


def pct_value(source, p: int):
    percentiles = pick(source, "percentiles")
    for holder in (percentiles, source):
        value = pick(holder, f"p{p}", f"p{p:02d}", str(p), f"{p}.0", f"percentile_{p}")
        if value is not MISSING:
            return value
    return MISSING


def a_constraints(target: dict) -> list:
    """One dict per reported table constraint: table, column, target_class, p5, p50, p95, count_gt1, fraction_gt1."""
    rows = pick(target, "constraints", "table_constraints")
    out = []
    for c in rows if isinstance(rows, list) else []:
        stats = pick(c, "normalized_sum", "normalized_sum_percentiles")
        stats = stats if isinstance(stats, dict) else c
        out.append({"table": pick(c, "table"), "column": pick(c, "column") if "column" in c else None,
                    "target_class": pick(c, "target_class"),
                    "p5": pct_value(stats, 5), "p50": pct_value(stats, 50), "p95": pct_value(stats, 95),
                    "count_gt1": pick(c, "count_gt_1", "count_above_1", "count_above_one", "count_exceeding_1",
                                      "exceed_count"),
                    "fraction_gt1": pick(c, "fraction_gt_1", "fraction_above_1", "fraction_above_one",
                                         "probability_above_one", "p_gt_1", "exceed_fraction")})
    return out


def a_earliest(component: dict):
    return pick(component, "earliest_established_step", "earliest_established", "earliest_step")


def a_coverage(result: dict):
    return pick(result, "coverage")


# --- comparison against the adapters ---------------------------------------------------------------------------

class Checks:
    def __init__(self):
        self.total = 0
        self.failures = []
        self.missing = set()

    def _record(self, ok: bool, label: str, expected, actual) -> bool:
        self.total += 1
        if not ok:
            self.failures.append({"check": label, "expected": expected, "actual": actual})
        return ok

    def has(self, label: str, value) -> bool:
        if value is MISSING:
            self.missing.add(label)
            self.total += 1
            self.failures.append({"check": label, "expected": "a field", "actual": "missing"})
            return False
        return True

    def eq(self, label: str, expected, actual) -> bool:
        if not self.has(label, actual):
            return False
        if isinstance(expected, float) and isinstance(actual, (int, float)):
            return self._record(float(actual) == expected, label, expected, actual)
        return self._record(actual == expected, label, expected, actual)

    def near(self, label: str, expected, actual, tol: float) -> bool:
        if not self.has(label, actual):
            return False
        return self._record(abs(float(actual) - expected) <= tol, label, expected, actual)

    def report(self) -> dict:
        return {"checks": self.total, "failed": len(self.failures), "failures": self.failures[:60],
                "missing_fields": sorted(self.missing)}


def compare_target(checks: Checks, label: str, exp: dict, block: dict, level: float) -> None:
    samples, failed = a_sample_counts(block)
    checks.eq(f"{label} samples", exp["samples"], samples)
    checks.eq(f"{label} failed_samples", exp["failed"], failed)
    stats = a_class_stats(block)
    for cls in ALL_CLASSES:
        checks.eq(f"{label} count[{cls}]", exp["counts"][cls], stats[cls]["count"])
        checks.eq(f"{label} probability[{cls}]", exp["probabilities"][cls], stats[cls]["probability"])
        if checks.has(f"{label} interval[{cls}]", stats[cls]["interval"]):
            for end, name in enumerate(("lower", "upper")):
                checks.near(f"{label} cp_{name}[{cls}]", exp["intervals"][cls][end], stats[cls]["interval"][end],
                            CP_ENDPOINT_TOL)
    cumulative = a_cumulative_stats(block)
    for cls in CLASSES:
        checks.eq(f"{label} cumulative_count[{cls}]", exp["cumulative_counts"][cls], cumulative[cls]["count"])
        checks.eq(f"{label} cumulative_probability[{cls}]", exp["cumulative_probabilities"][cls],
                  cumulative[cls]["probability"])
        if checks.has(f"{label} cumulative_interval[{cls}]", cumulative[cls]["interval"]):
            for end, name in enumerate(("lower", "upper")):
                checks.near(f"{label} cumulative_cp_{name}[{cls}]", exp["cumulative_intervals"][cls][end],
                            cumulative[cls]["interval"][end], CP_ENDPOINT_TOL)
    est = a_established(block)
    checks.eq(f"{label} established_class", exp["established"] or "not_established", est["class"])
    if est["minimum_samples"] is not MISSING:
        checks.eq(f"{label} minimum_samples", exp["minimum_samples"], est["minimum_samples"])
    if exp["established"] is None and exp["needed_note"] is not None:
        # a class with p_hat above the level exists: the result must say how many samples would establish it
        wanted = exp["needed"]
        if est["needed"] is not MISSING and est["needed"] is not None:
            checks.eq(f"{label} samples_needed", wanted, est["needed"])
        elif wanted is not None:
            checks.has(f"{label} samples_needed", MISSING)
    reported = a_constraints(block)
    checks.has(f"{label} constraints", reported if reported else MISSING)
    for entry in reported:
        keys = [(entry["table"], entry["column"], entry["target_class"])]
        if entry["target_class"] is MISSING or entry["target_class"] is None:
            keys = [(entry["table"], entry["column"], c) for c in ("A", "C")] if entry["table"] == 1 else [
                (entry["table"], entry["column"], c) for c in CLASSES[:3]]
        stats_by_key = [exp["constraints"].get(k) for k in keys]
        stats_by_key = [s for s in stats_by_key if s is not None]
        tag = f"{label} constraint[T{entry['table']}c{entry['column']}]"
        if not stats_by_key:
            checks._record(False, f"{tag} is a constraint the evaluator produces", keys, None)
            continue
        # a table-1 entry without a target class is matched against the first normalization it equals
        chosen = next((s for s in stats_by_key if entry["p50"] == s["percentiles"][50]), stats_by_key[0])
        for p in PERCENTILES:
            checks.eq(f"{tag} p{p}", chosen["percentiles"][p], entry[f"p{p}"])
        checks.eq(f"{tag} count_gt1", chosen["count_gt1"], entry["count_gt1"])
        checks.eq(f"{tag} fraction_gt1", chosen["fraction_gt1"], entry["fraction_gt1"])


def compare_result(spec: dict, result: dict, table: dict) -> Checks:
    checks = Checks()
    checks.eq("schema", RESULT_SCHEMA, pick(result, "schema"))
    level = float(spec.get("confidence_level", 0.95))
    reported_level = pick(result, "confidence_level")
    if reported_level is not MISSING:
        checks.eq("confidence_level", level, reported_level)
    pack_sha = pick(result, "rule_pack_sha256")
    if pack_sha is not MISSING:
        checks.eq("rule_pack_sha256", sha(RULES_FILE), pack_sha)
    comps = a_components(result)
    for component in spec["components"]:
        block = comps.get(component["id"])
        if not checks.has(f"component {component['id']}", block if block is not None else MISSING):
            continue
        targets = a_targets(block)
        for step in spec["targets"]:
            label = f"{component['id']}@{step}"
            if not checks.has(f"{label} target block", targets.get(step, MISSING)):
                continue
            compare_target(checks, label, table[(component["id"], step)], targets[step], level)
        earliest = a_earliest(block)
        if checks.has(f"{component['id']} earliest_established_step", earliest):
            wanted = earliest_steps(table, component["id"], spec["targets"])
            norm = {k.removeprefix("at_most_"): (None if v is None else int(v)) for k, v in earliest.items()}
            matched = [name for name, expected in wanted.items()
                       if all(norm.get(c) == expected[c] for c in CLASSES)]
            checks._record(bool(matched), f"{component['id']} earliest_established_step", wanted, norm)
    coverage = a_coverage(result)
    if checks.has("coverage block", coverage):
        checks._record(isinstance(coverage, dict) and bool(coverage), "coverage block is a nonempty object",
                       "object", coverage)
    return checks


# --- gates -----------------------------------------------------------------------------------------------------

def strip_ms(data: bytes) -> tuple[bytes, int]:
    stripped, count = MS_LINE.subn(b"", data)
    return stripped, count


def top_level_diff(a: bytes, b: bytes) -> list:
    left, right = json.loads(a), json.loads(b)
    return sorted(k for k in set(left) | set(right) if left.get(k) != right.get(k) and k != "ms")


def u1(case: str) -> int:
    key = case.upper()
    path = case_path(key)
    before = candidate()
    stage = fresh_stage(f"u1_{key.lower()}")
    study_id = study_id_for(key)
    staged = stage_case(path, stage, title=f"{study_id} :: {CASE_ID}", keep=True)
    spec, problem = staged["spec"], staged["problem"]
    n = spec["sampling"]["samples"]
    study = build_study(problem, spec, study_id)
    dump(stage / "study.json", study)
    unc = run_uncertainty(stage)
    stu = run_child([BINARY, "study", "run", "study.json", "study_out"], stage) if unc["rc"] == 0 else None
    report = {"schema": "actinv-p145-u1-1", "case": key, "candidate": before, "spec": path.name,
              "spec_sha256": staged["source_spec_sha256"], "source_problem_sha256": source_problem_sha(path),
              "staged_spec_sha256": staged["spec_sha256"], "staged_problem_sha256": staged["problem_sha256"],
              "study_sha256": sha(stage / "study.json"),
              "uncertainty": brief(unc), "study": brief(stu) if stu else None, "samples": n}
    ok = False
    if stu is not None and stu["rc"] == 0:
        keep, cases = stage / "keep", stage / "study_out/cases" / CASE_ID
        strict = relaxed = 0
        both_failed, mismatched, only_unc, only_study, ms_lines = [], [], [], [], 0
        for i in range(n):
            a, b = keep / f"rob_{i}.out.json", cases / f"rob_{i}.out.json"
            if not a.exists() and not b.exists():
                both_failed.append(i)
            elif a.exists() != b.exists():
                (only_unc if a.exists() else only_study).append(i)
            else:
                da, db = a.read_bytes(), b.read_bytes()
                sa, ca = strip_ms(da)
                sb, cb = strip_ms(db)
                ms_lines += ca + cb
                if da == db:
                    strict += 1
                if sa == sb and ca == 1 and cb == 1:
                    relaxed += 1
                else:
                    mismatched.append({"sample": i, "differing_top_level_keys": top_level_diff(da, db),
                                       "ms_lines": [ca, cb]})
        report.update({"strict_identical": strict, "identical_except_ms": relaxed,
                       "failed_in_both": both_failed, "only_in_uncertainty": only_unc, "only_in_study": only_study,
                       "mismatched": mismatched[:20], "mismatched_count": len(mismatched),
                       "keep_dir_files": len(list(keep.glob("*"))),
                       "uncertainty_result_sha256": maybe_sha(stage / "result.json")})
        ok = relaxed > 0 and relaxed + len(both_failed) == n and not mismatched and not only_unc and not only_study
    report["pass"] = ok and before["tree_clean"]
    report["summary"] = {"case": key, "samples": n, "identical_except_ms": report.get("identical_except_ms"),
                         "strict_identical": report.get("strict_identical"),
                         "mismatched": report.get("mismatched_count")}
    (stage / "meta.json").write_text(json.dumps({"binary_sha256": before["binary_sha256"],
                                                 "spec_sha256": staged["source_spec_sha256"],
                                                 "rc": unc["rc"]}) + "\n")
    return finish(f"u1_{key.lower()}", report)


def reuse_or_run(key: str, before: dict) -> tuple[Path, dict, dict]:
    """The u1 staging folder when its run used this binary and spec; otherwise a fresh run of the command."""
    path = case_path(key)
    stage = WORK / f"u1_{key.lower()}"
    meta_path = stage / "meta.json"
    if meta_path.exists() and (stage / "result.json").exists():
        meta = json.loads(meta_path.read_text())
        if (meta["binary_sha256"], meta["spec_sha256"], meta["rc"]) == (before["binary_sha256"], sha(path), 0):
            staged = {"spec": json.loads((stage / "spec.json").read_text()),
                      "problem": json.loads((stage / "problem.json").read_text()), "reused_u1_run": True}
            return stage, staged, {}
    stage = fresh_stage(f"u2_{key.lower()}")
    staged = stage_case(path, stage, title=f"{study_id_for(key)} :: {CASE_ID}", keep=True)
    staged["reused_u1_run"] = False
    unc = run_uncertainty(stage)
    return stage, staged, brief(unc)


def u2(case: str) -> int:
    key = case.upper()
    path = case_path(key)
    before = candidate()
    stage, staged, ran = reuse_or_run(key, before)
    spec, problem = staged["spec"], staged["problem"]
    report = {"schema": "actinv-p145-u2-1", "case": key, "candidate": before, "spec": path.name,
              "spec_sha256": sha(path), "source_problem_sha256": source_problem_sha(path),
              "rule_pack": str(RULES_FILE.relative_to(ROOT)),
              "rule_pack_sha256": sha(RULES_FILE), "reused_u1_run": staged["reused_u1_run"], "command": ran or None,
              "assumptions": {"percentiles_include_unknown": PERCENTILES_INCLUDE_UNKNOWN,
                              "endpoint_tol": CP_ENDPOINT_TOL}}
    ok = False
    if not ran or ran["rc"] == 0:
        result = load_result(stage)
        started = time.monotonic()
        tally = Tally(spec, problem, stage, stage / spec["sampling"]["keep_samples"])
        table = tally.run()
        checks = compare_result(spec, result, table)
        outcome = checks.report()
        missing = sum(1 for b in table.values() if b["failed"])
        report.update({"result_sha256": sha(stage / "result.json"),
                       "recount_seconds": round(time.monotonic() - started, 1),
                       "check_report": outcome, "anomalies": tally.anomalies[:20],
                       "tally": {f"{c}@{s}": {"counts": b["counts"], "established": b["established"],
                                              "near_threshold": b["established_near_threshold"],
                                              "needed": b["needed"], "needed_note": b["needed_note"],
                                              "failed": b["failed"]} for (c, s), b in sorted(table.items())},
                       "targets_with_failed_samples": missing})
        ok = outcome["checks"] > 0 and outcome["failed"] == 0 and not outcome["missing_fields"] and not tally.anomalies
    report["pass"] = ok and before["tree_clean"]
    report["summary"] = {"case": key, **{k: report.get("check_report", {}).get(k) for k in ("checks", "failed")},
                         "missing_fields": len(report.get("check_report", {}).get("missing_fields", []))}
    return finish(f"u2_{key.lower()}", report)


def read_npy_values(data: bytes) -> list:
    header_length = struct.unpack("<H", data[8:10])[0] if data[6] == 1 else struct.unpack("<I", data[8:12])[0]
    start = (10 if data[6] == 1 else 12) + header_length
    descr = "q" if b"'<i8'" in data[:start] else "d"
    return list(struct.unpack("<" + descr * ((len(data) - start) // 8), data[start:]))


def covariance_sigma2(path: Path) -> float:
    """The relative variance of a rank-one synthetic sidecar: every value of its single component equals sigma2."""
    with zipfile.ZipFile(path) as archive:
        values = read_npy_values(archive.read("values.npy"))
        components = read_npy_values(archive.read("components.npy"))
    if len(components) != 9 or len(set(values)) != 1:
        raise SystemExit(f"{path.name} is not a single-component constant-block sidecar")
    return values[0]


def phi(x: float) -> float:
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def u3() -> int:
    before = candidate()
    meta = json.loads((INPUTS / "c3_cases.json").read_text())
    level = meta["confidence"]
    report = {"schema": "actinv-p145-u3-1", "candidate": before, "closed_form": meta["closed_form"],
              "interval": "two-sided equal-tailed Clopper-Pearson interval at 99.9 % of the observed count of "
                          "normalized sums above 1; the closed-form probability evaluated at the nominal sum the "
                          "run computed must lie inside it", "cases": []}
    ok = True
    for entry in meta["cases"]:
        spec_path = INPUTS / entry["spec"]
        stage = fresh_stage(f"u3_{entry['id']}")
        staged = stage_case(spec_path, stage)
        n = staged["spec"]["sampling"]["samples"]
        step = staged["spec"]["targets"][0]
        child = run_uncertainty(stage)
        row = {"id": entry["id"], "spec_sha256": staged["source_spec_sha256"], "command": brief(child), "samples": n}
        case_ok = False
        if child["rc"] == 0:
            result = load_result(stage)
            comp = next(iter(a_components(result).values()), {})
            block = a_targets(comp).get(step, {})
            s0 = a_nominal_sum(block, 2, 1, "A")
            column1 = [c for c in a_constraints(block) if c["table"] == 2 and c["column"] == 1]
            count = column1[0]["count_gt1"] if column1 else MISSING
            fraction = column1[0]["fraction_gt1"] if column1 else MISSING
            samples, failed = a_sample_counts(block)
            class_a = a_class_stats(block).get("A", {}).get("count", MISSING)
            row.update({"result_sha256": sha(stage / "result.json"),
                        "nominal_normalized_sum": None if s0 is MISSING else s0,
                        "count_above_1": None if count is MISSING else count,
                        "reported_failed": None if failed is MISSING else failed,
                        "class_a_count": None if class_a is MISSING else class_a})
            if s0 is not MISSING and count is not MISSING and failed == 0 and samples == n and s0 > 0.0:
                sigma2 = covariance_sigma2(INPUTS / entry["covariance"])
                closed = phi((math.log(s0) - sigma2 / 2.0) / math.sqrt(sigma2))
                low, high = cp_interval(count, n, level)
                placed = abs(closed - entry["target_probability"]) <= 0.15
                spread = math.sqrt(max(closed * (1 - closed), 1e-300) / n)
                row.update({"sigma2": sigma2, "closed_form_probability": closed,
                            "target_probability": entry["target_probability"],
                            "placement_within_0.15": placed, "observed_frequency": count / n,
                            "reported_fraction_matches": fraction == count / n, "interval_level": level,
                            "interval": [low, high], "closed_form_inside_interval": low <= closed <= high,
                            "class_a_count_is_n_minus_exceedances": class_a == n - count,
                            "standard_errors_from_closed_form": (count / n - closed) / spread})
                case_ok = (row["closed_form_inside_interval"] and placed and row["reported_fraction_matches"]
                           and row["class_a_count_is_n_minus_exceedances"])
        row["pass"] = case_ok
        ok &= case_ok
        report["cases"].append(row)
    report["pass"] = ok and before["tree_clean"]
    report["summary"] = {"cases": len(report["cases"]),
                         "inside": [r.get("closed_form_inside_interval") for r in report["cases"]]}
    return finish("u3", report)


def edit_gas(spec, problem):
    problem.setdefault("options", {})["gas"] = True


def edit_library_switch(spec, problem):
    problem["schedule"].append({"dt": "1 d", "flux": 0.0, "library": problem["library"], "projectile": "neutron",
                                "spectrum": problem["spectrum"]})


def edit_power_step(spec, problem):
    problem["material"]["density_g_cm3"] = 8.9
    problem["schedule"].append({"dt": "1 d", "power_W_cm3": 1.0})


def edit_no_channel(spec, problem):
    spec["sampling"]["channels"] = {}


def edit_few_samples(spec, problem):
    spec["sampling"]["samples"] = 19


def edit_base(spec, problem):
    spec["sampling"]["samples"] = 20
    spec["sampling"]["channels"]["flux_rel_std"] = 0.1


def edit_zero_variance(spec, problem):
    spec["sampling"]["samples"] = 20
    spec["sampling"]["channels"] = {"composition_rel_std": {"Co": 0.0}}
    spec["sampling"].pop("covariance", None)


REFUSALS = {"gas": (edit_gas, ("gas",)), "schedule_library_switch": (edit_library_switch, ("librar",)),
            "power_driven_step": (edit_power_step, ("power",)), "no_channel": (edit_no_channel, ("channel",)),
            "fewer_than_20_samples": (edit_few_samples, ("20", "samples"))}


def listing(stage: Path) -> set:
    """Relative names of every file and folder in a staging folder, without following the data link."""
    names = set()
    for folder, dirs, files in os.walk(stage, followlinks=False):
        for name in dirs + files:
            relative = (Path(folder) / name).relative_to(stage)
            if relative != Path("actinv-data"):
                names.add(str(relative))
    return names


def u4() -> int:
    before = candidate()
    base_spec = INPUTS / "c3_p50.json"
    report = {"schema": "actinv-p145-u4-1", "candidate": before, "base_spec": base_spec.name,
              "base_spec_sha256": sha(base_spec)}
    # refusals: the unmodified base (20 samples, two channels) must run, each mutation must not
    control_stage = fresh_stage("u4_control")
    stage_case(base_spec, control_stage, edit=edit_base)
    control = run_uncertainty(control_stage)
    report["control_accepted"] = {"command": brief(control), "accepted": control["rc"] == 0}
    refusals, messages = {}, {}
    for name, (edit, words) in REFUSALS.items():
        stage = fresh_stage(f"u4_{name}")

        def combined(spec, problem, edit=edit):
            edit_base(spec, problem)
            edit(spec, problem)

        stage_case(base_spec, stage, keep=True, edit=combined)
        files_before = listing(stage)
        child = run_uncertainty(stage)
        message = child["stderr"].replace(str(stage), "<stage>").strip()
        messages[name] = message
        refusals[name] = {"command": brief(child), "exit_nonzero": child["rc"] not in (0, None),
                          "exit_is_error_not_panic": child["rc"] in (1, 2),
                          "no_output_written": not (stage / "result.json").exists(),
                          "no_samples_kept": not any((stage / "keep").glob("*")),
                          "nothing_else_created": listing(stage) == files_before,
                          "message_present": bool(message) and "panicked" not in message,
                          "message_mentions_topic_hint": any(w in message.lower() for w in words),
                          "message": message[:400]}
    distinct = len(set(messages.values())) == len(messages) and all(messages.values())
    report["refusals"] = refusals
    report["messages_pairwise_distinct"] = distinct
    refusal_ok = distinct and all(
        all(v for k, v in r.items() if isinstance(v, bool) and k != "message_mentions_topic_hint")
        for r in refusals.values())
    # zero variance: a composition channel with relative std 0 leaves every sample at the nominal inventory
    stage = fresh_stage("u4_zero_variance")
    staged = stage_case(base_spec, stage, edit=edit_zero_variance)
    child = run_uncertainty(stage)
    zero = {"command": brief(child), "checks": []}
    zero_ok = False
    if child["rc"] == 0:
        result = load_result(stage)
        comps = a_components(result)
        zero_ok = True
        for component in staged["spec"]["components"]:
            for step in staged["spec"]["targets"]:
                block = a_targets(comps[component["id"]]).get(step, {})
                nominal = a_nominal_class(block)
                stats = a_class_stats(block)
                s0 = a_nominal_sum(block, 2, 1, "A")
                row = {"component": component["id"], "step": step,
                       "nominal_class": None if nominal is MISSING else nominal,
                       "probability_of_nominal": None, "constraints_equal_nominal": None}
                if nominal is not MISSING and nominal in stats:
                    p = stats[nominal]["probability"]
                    row["probability_of_nominal"] = None if p is MISSING else p
                    cons = [c for c in a_constraints(block) if c["table"] == 2 and c["column"] == 1]
                    row["constraints_equal_nominal"] = bool(cons) and s0 is not MISSING and all(
                        c[f"p{q}"] is not MISSING and abs(c[f"p{q}"] - s0) <= 1e-12 * max(abs(s0), 1e-300)
                        for c in cons for q in PERCENTILES)
                row["ok"] = row["probability_of_nominal"] == 1.0 and row["constraints_equal_nominal"] is True
                zero["checks"].append(row)
                zero_ok &= row["ok"]
    zero["pass"] = zero_ok
    report["zero_variance"] = zero
    # repeat: the same spec twice gives byte-identical result files
    repeat_stage = fresh_stage("u4_repeat")
    stage_case(base_spec, repeat_stage, edit=edit_base)
    first = run_uncertainty(repeat_stage, "spec.json", "result_a.json")
    second = run_uncertainty(repeat_stage, "spec.json", "result_b.json")
    repeat = {"first": brief(first), "second": brief(second), "identical": False}
    if first["rc"] == 0 and second["rc"] == 0:
        a, b = (repeat_stage / "result_a.json").read_bytes(), (repeat_stage / "result_b.json").read_bytes()
        repeat.update({"identical": a == b, "sha256": sha_bytes(a), "bytes": len(a)})
        if a != b:
            repeat["differing_top_level_keys"] = top_level_diff(a, b)
    report["repeat"] = repeat
    report["pass"] = (control["rc"] == 0 and refusal_ok and zero_ok and repeat["identical"] and before["tree_clean"])
    report["summary"] = {"control_accepted": control["rc"] == 0, "refusals_ok": refusal_ok,
                         "distinct_messages": distinct, "zero_variance": zero_ok,
                         "repeat_identical": repeat["identical"]}
    return finish("u4", report)


def verdict(ci_path: str) -> int:
    names = ["u1_c1", "u1_c2", "u2_c1", "u2_c2", "u3", "u4", "u5"]
    records = {n: json.loads((OUT / f"{n}.json").read_text()) for n in names}
    ci = json.loads(Path(ci_path).read_text())
    runs = ci.get("runs", [])
    ci_green = bool(runs) and all(r.get("conclusion") == "success" for r in runs) and any(
        r.get("name") == "controls" for r in runs)

    def ok(*keys):
        return all(records[k].get("pass") is True for k in keys)

    gates = {"U1": ok("u1_c1", "u1_c2"), "U2": ok("u2_c1", "u2_c2"), "U3": ok("u3"), "U4": ok("u4"),
             "U5": ok("u5") and ci_green}
    evidence = [records[n]["candidate"] for n in names[:-1]]
    consistent = len({(c["commit"], c["binary_sha256"], c["protocol_sha256"], c["amendment_sha256"])
                      for c in evidence}) == 1 and evidence[0]["protocol_sha256"] == sha(PROTOCOL)
    record = {"schema": "actinv-p145-verdict-1", "protocol_sha256": sha(PROTOCOL),
              "amendment_sha256": maybe_sha(AMENDMENT),
              "gate_file_sha256": {n: sha(OUT / f"{n}.json") for n in names},
              "gates": gates, "evidence_candidates_consistent": consistent, "candidate": evidence[0], "ci": ci,
              "verdict": "P145-PASS" if all(gates.values()) and consistent else "P145-FAIL"}
    dump(OUT.parent / "p145_verdict.json", record)
    print(json.dumps({"verdict": record["verdict"], "gates": gates, "consistent": consistent}))
    return 0 if record["verdict"] == "P145-PASS" else 1


# --- self test (no data, no binary) ----------------------------------------------------------------------------

def reference_result(spec: dict, table: dict) -> dict:
    """A result document in the field names the adapters read, built from the independent tally. Used only by the
    self test, and as the reference for the schema this checker assumes."""
    components = []
    for component in spec["components"]:
        targets = []
        for step in spec["targets"]:
            b = table[(component["id"], step)]
            constraints = [{"table": t, "column": c, "target_class": k,
                            "normalized_sum": {"p5": v["percentiles"][5], "p50": v["percentiles"][50],
                                               "p95": v["percentiles"][95]},
                            "count_gt_1": v["count_gt1"], "fraction_gt_1": v["fraction_gt1"]}
                           for (t, c, k), v in b["constraints"].items()]
            targets.append({
                "step": step, "nominal": {"class": "A", "evaluation": {"constraints": [
                    {"table": 2, "column": 1, "target_class": "A", "normalized_sum": 0.5}]}},
                "samples": b["samples"], "failed_samples": [{"sample": i, "error": "x"} for i in range(b["failed"])],
                "classes": {c: {"count": b["counts"][c], "probability": b["probabilities"][c],
                                "ci": list(b["intervals"][c])} for c in ALL_CLASSES},
                "cumulative": {c: {"count": b["cumulative_counts"][c], "probability": b["cumulative_probabilities"][c],
                                   "ci": list(b["cumulative_intervals"][c])} for c in CLASSES},
                "established_class": b["established"] or "not_established", "samples_needed": b["needed"],
                "minimum_samples": b["minimum_samples"], "constraints": constraints})
        earliest = earliest_steps(table, component["id"], spec["targets"])["equals"]
        components.append({"id": component["id"], "targets": targets, "earliest_established_step": earliest})
    return {"schema": RESULT_SCHEMA, "confidence_level": spec.get("confidence_level", 0.95),
            "rule_pack_sha256": sha(RULES_FILE), "components": components,
            "coverage": {"channels": sorted(spec["sampling"]["channels"]), "statement": "conditional"}}


def selftest() -> int:
    import copy
    import random
    import tempfile

    failures = []

    def expect(ok, label):
        if not ok:
            failures.append(label)

    # Clopper-Pearson against closed forms and a textbook value, double precision against decimal
    lo, hi = cp_interval(5, 20, 0.95)
    expect(abs(lo - 0.0866) < 5e-5 and abs(hi - 0.4910) < 5e-5, f"cp(5,20) {lo} {hi}")
    expect(abs(cp_interval(0, 72, 0.95)[1] - (1.0 - 0.025 ** (1 / 72))) < 1e-13, "cp(0,72)")
    expect(abs(cp_interval(72, 72, 0.95)[0] - 0.025 ** (1 / 72)) < 1e-13, "cp(72,72)")
    for k, n in ((1, 30), (7, 100), (93, 100), (400, 1000)):
        lo, hi = cp_interval(k, n, 0.99)
        expect(abs(betai(k, n - k + 1, lo) - 0.005) < 1e-11 and abs(betai(k + 1, n - k, hi) - 0.995) < 1e-11,
               f"beta({k},{n})")
        expect(abs(float(binom_tail_ge(k, n, Decimal(repr(lo)))) - 0.005) < 1e-12, f"tail({k},{n})")
    expect(minimum_samples(0.95) == 72, "minimum samples at 0.95 is 72")
    all_72 = {"A": 72, "B": 72, "C": 72, "above_class_c": 72}
    expect(established_class(all_72, 72, 0.95)[0] == "A", "72 of 72 establish A")
    all_71 = {"A": 71, "B": 71, "C": 71, "above_class_c": 71}
    expect(established_class(all_71, 71, 0.95)[0] is None, "71 of 71 do not")
    a_fails = {"A": 90, "B": 100, "C": 100, "above_class_c": 100}
    expect(established_class(a_fails, 100, 0.95)[0] == "B", "B after A fails")
    n_needed, note = needed_samples(96, 100, 0.95)
    k_needed = (96 * n_needed) // 100 if n_needed else 0
    expect(n_needed is not None and n_needed > 100 and betai(k_needed, n_needed - k_needed + 1, 0.95) <= 0.025,
           f"needed_samples {n_needed} {note}")
    expect(needed_samples(94, 100, 0.95)[0] is None, "p_hat below level cannot establish")
    # nearest rank
    expect([percentile_rank(100, p) for p in PERCENTILES] == [5, 50, 95], "ranks n=100")
    expect([percentile_rank(20, p) for p in PERCENTILES] == [1, 10, 19], "ranks n=20")
    expect([percentile_rank(21, p) for p in PERCENTILES] == [2, 11, 20], "ranks n=21")
    expect(percentile_rank(7, 2.2) == 1 and percentile_rank(1000, 2.2) == 22, "decimal p")
    # evaluator on the bundled rule pack, hand-computed fractions (ci = 3.7e10, nCi/g and Ci/m3 units)
    rules = Rules(RULES_FILE)
    co60 = {"Co60": (27, 1.6634e8, False)}
    ci = rules.ci_bq
    one_ci_m3 = ci / 1.0e6  # Bq per cm3 for 1 Ci/m3

    def cls(bq, props, wt="general", mass=1000.0, volume=1000.0):
        return evaluate(rules, wt, mass, volume, bq, props)["class"]

    expect(cls({"Co60": 0.5 * 700 * one_ci_m3 * 1000}, co60) == "A", "Co60 at half the limit is A")
    expect(cls({"Co60": 1.5 * 700 * one_ci_m3 * 1000}, co60) == "B", "Co60 above Column 1 only is B")
    nb = {"Nb94": (41, 6.4e11, False)}
    expect(cls({"Nb94": 0.01 * 0.2 * one_ci_m3 * 1000}, nb, "activated_metal") == "A", "Nb94 5 percent of A boundary")
    expect(cls({"Nb94": 0.5 * 0.2 * one_ci_m3 * 1000}, nb, "activated_metal") == "C", "Nb94 half of its limit is C")
    expect(cls({"Nb94": 2.0 * 0.2 * one_ci_m3 * 1000}, nb, "activated_metal") == "above_class_c",
           "Nb94 twice is above C")
    expect(cls({"Nb94": 0.5 * 0.2 * one_ci_m3 * 1000}, nb, "general") == "A", "Nb94 has no general-waste row")
    expect(cls({"Co60": 1.0, "Mn99": 1.0}, co60) == "unknown", "missing properties give unknown")
    ev = evaluate(rules, "general", 1000.0, 1000.0, {"Co60": 1.0, "Mn54": 1.0},
                  {**co60, "Mn54": (25, 2.7e7, False)})
    expect(ev["class"] == "A" and ev["constraints"][1]["contributors"] == 2 and ev["constraints"][1]["strict"],
           "short-lived row joins Column 1")
    expect(passes(1.0, 1) and not passes(1.0, 2) and passes(0.999, 2) and not passes(1.001, 1), "strictness rule")
    expect(canonical_nuclide("Ni-63") == "Ni63" and canonical_nuclide("Ag110m") == "Ag110m1", "nuclide identities")
    expect(rtyp_has_alpha(4.0) and rtyp_has_alpha(1.4) and not rtyp_has_alpha(1.0) and not rtyp_has_alpha(2.0),
           "alpha RTYP")
    # end to end with synthetic kept samples; every perturbation of the result must be caught
    with tempfile.TemporaryDirectory() as folder:
        base = Path(folder)
        (base / "keep").mkdir()
        rng = random.Random(145)
        n = 200
        spec = {"schema": SPEC_SCHEMA, "problem": "problem.json", "rules": BUNDLED_RULES_ID, "targets": [1, 2, 3],
                "nuclide_properties": {"Co60": {"z": 27, "half_life_s": 1.6634e8, "alpha_emitting": False},
                                       "Nb94": {"z": 41, "half_life_s": 6.4e11, "alpha_emitting": False},
                                       "H3": {"z": 1, "half_life_s": 3.887e8, "alpha_emitting": False}},
                "components": [{"id": "blk", "mass_g": 1000.0, "density_g_cm3": 8.0, "waste_type": "activated_metal",
                                "external_tritium": {"status": "declared", "source": "s", "excludes_activation": True,
                                                    "activity_bq": {"1": 5.0e6, "2": 5.0e6, "3": 5.0e6}}}],
                "sampling": {"channels": {"flux_rel_std": 0.1}, "samples": n, "seed": 1, "keep_samples": "keep"},
                "confidence_level": 0.95}
        limit_nb = 0.2 * one_ci_m3 * (1000.0 / 8.0)
        for i in range(n):
            if i == 17:
                continue  # a failed solve: no file
            steps = []
            for step, scale in ((1, 0.003), (2, 0.04), (3, 0.9)):
                steps.append({"step": step, "t_s": float(step), "activity_Bq_per_g": {
                    "Nb94": scale * limit_nb / 1000.0 * math.exp(rng.gauss(0, 0.3)),
                    "Co60": 1.0e3 * math.exp(rng.gauss(0, 0.2)), "H3": 10.0 * rng.random()}})
            (base / "keep" / f"rob_{i}.out.json").write_text(json.dumps({"steps": steps, "ms": 1.0}))
        problem = {"decay": {}}
        table = Tally(spec, problem, base, base / "keep").run()
        expect(all(b["failed"] == 1 for b in table.values()), "one failed sample in every block")
        expect(table[("blk", 1)]["established"] == "A" or table[("blk", 1)]["established"] is None, "tally runs")
        result = reference_result(spec, table)
        expect(compare_result(spec, result, table).report()["failed"] == 0, "reference result passes")
        classes = {s: table[("blk", s)]["counts"] for s in (1, 2, 3)}
        expect(len({tuple(c.items()) for c in classes.values()}) > 1, f"steps differ in class mix {classes}")

        def mutated(path, value):
            doc = copy.deepcopy(result)
            holder = doc
            for key in path[:-1]:
                holder = holder[key]
            holder[path[-1]] = value
            return doc

        step3 = ["components", 0, "targets", 2]
        cases = {
            "count": mutated(step3 + ["classes", "B", "count"], table[("blk", 3)]["counts"]["B"] + 1),
            "probability": mutated(step3 + ["classes", "A", "probability"], 0.123),
            "cp lower": mutated(step3 + ["classes", "A", "ci"], [table[("blk", 3)]["intervals"]["A"][0] + 1e-9,
                                                               table[("blk", 3)]["intervals"]["A"][1]]),
            "cumulative cp upper": mutated(step3 + ["cumulative", "C", "ci"], [0.0, 0.5]),
            "established": mutated(step3 + ["established_class"], "C"),
            "failed": mutated(step3 + ["failed_samples"], []),
            "samples": mutated(step3 + ["samples"], n + 1),
            "p50": mutated(step3 + ["constraints", 0, "normalized_sum", "p50"], 0.123456),
            "count over one": mutated(step3 + ["constraints", 0, "count_gt_1"], 3),
            "earliest": mutated(["components", 0, "earliest_established_step"],
                                {"A": 3, "B": 3, "C": 3, "above_class_c": 3}),
            "coverage": mutated(["coverage"], {}),
            "schema": mutated(["schema"], "other"),
            "rule pack": mutated(["rule_pack_sha256"], "0" * 64),
        }
        for label, doc in cases.items():
            expect(compare_result(spec, doc, table).report()["failed"] > 0, f"mutation not caught: {label}")
        broken = copy.deepcopy(result)
        del broken["components"][0]["targets"][1]["classes"]
        outcome = compare_result(spec, broken, table).report()
        expect(outcome["failed"] > 0 and outcome["missing_fields"], "a renamed field group is reported as missing")
        # the study-side comparison helpers
        a = b'{\n  "x": 1,\n  "ms": 12.5\n}\n'
        expect(strip_ms(a) == (b'{\n  "x": 1,\n}\n', 1) and strip_ms(b'{"ms": 1}')[1] == 0, "ms line removal")
    # the C3 sidecars on disk, when generated: three members, one constant block, closed form monotone in s0
    meta_path = INPUTS / "c3_cases.json"
    if meta_path.exists():
        scratch = Path(tempfile.mkdtemp(prefix="p145-selftest-", dir=ROOT / "target"))
        for entry in json.loads(meta_path.read_text())["cases"]:
            sidecar = scratch / entry["covariance"]
            expect(c3_sidecar(entry["covariance"])(sidecar) == entry["covariance_sha256"],
                   f"generated sidecar of {entry['id']} has the pinned SHA-256")
            sigma2 = covariance_sigma2(sidecar)
            expect(sigma2 == entry["sigma2"], f"sigma2 of {entry['id']}")
            s0 = entry["target_s0"]
            expect(abs(phi((math.log(s0) - sigma2 / 2) / math.sqrt(sigma2)) - entry["target_probability"]) < 1e-9,
                   f"closed form at target s0 for {entry['id']}")
    print(json.dumps({"selftest": "fail" if failures else "pass", "failures": failures}))
    return 1 if failures else 0


def main(argv: list) -> int:
    command = argv[1] if len(argv) > 1 else ""
    if command == "u1" and len(argv) == 3:
        return u1(argv[2])
    if command == "u2" and len(argv) == 3:
        return u2(argv[2])
    if command == "u3" and len(argv) == 2:
        return u3()
    if command == "u4" and len(argv) == 2:
        return u4()
    if command == "verdict" and len(argv) == 3:
        return verdict(argv[2])
    if command == "selftest" and len(argv) == 2:
        return selftest()
    raise SystemExit(__doc__)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
