#!/usr/bin/env python3
"""P146: impurity budgets under nuclear-data uncertainty. Evidence gates B1 to B5, the local half of B6 and the verdict.
Each gate writes results/p146/<gate>_<case>.json (or <gate>.json) with a boolean `pass` and the candidate's provenance.
Children (`actinv waste budget uncertainty`, `actinv waste budget`, `actinv study run`) run serially with a timeout, one
per call, in a staging folder under target/p146 that holds copies of the case inputs and a link `actinv-data` to the data
tree (P146_DATA, then P145_DATA, default ./actinv-data). Launch inside the required systemd cgroup; the children inherit
that scope.

    python3 controls/check_p146.py b1 D1|D2_V16|D2_V36|D2_V64   # one data set per sample: kept factors equal the study's
    python3 controls/check_p146.py b2 CASE                      # exact independent recomputation (uses the b1 run if present)
    python3 controls/check_p146.py b3 CASE                      # verification rows (uses the b1 run if present)
    python3 controls/check_p146.py b4                           # D2 calibration against the closed form and Beta(n-k+1, k)
    python3 controls/check_p146.py b5                           # refusals, zero variance, repeat identity
    python3 controls/check_p146.py b6-collect [STEPS_TSV]       # local B6 record from steps.tsv and logs/ (no binary)
    python3 controls/check_p146.py verdict CI_RUNS.json         # needs the gate records and results/p146/b6.json
    python3 controls/check_p146.py stage CASE DIR               # the staged inputs, expanded, for a development run
    python3 controls/check_p146.py selftest                     # pure Python, no data and no binary

Decisions recorded in every record that uses them:
  * Keys starting with an underscore (`_note`) are removed from the staged copies of the spec, budget and base spec.
  * A committed budget carries `"nuclide_properties": "from_decay_data"`. A budget needs the half-life, Z and alpha flag
    of every nuclide an element solve produces (reach pruning keeps the whole reachable set, thousands of nuclides), so
    staging replaces the token by the properties of every radionuclide (NST = 0, positive half-life) of the primary decay
    file then the fallback decay file named by the base spec, the first file winning for a nuclide, with the rule of the
    P145 checker for alpha emission (a decay mode with positive branching ratio whose RTYP has the digit 4). The staged
    map's SHA-256 and size are recorded. The map is the same for the nominal run, the uncertainty run and the checker.
  * The covariance of D1 is the P145 C2 TENDL file, pinned by SHA-256. The D2 covariance sidecars are not tracked: staging
    generates them with controls/p145_c3_make.py and refuses to continue unless the bytes have the pinned SHA-256
    (P145 Amendment C, d2_cases.json).
  * Where the checker sums floating-point values it adds left to right from zero (Rust's Iterator::sum), not with
    Python's compensated sum(); the balance element's weight percent is 100 minus the sum of the other constituents in
    element-symbol order, as the Rust full_composition.
  * Supremum comparisons use a relative tolerance of 1e-12 (REL_TOL); sums that are both zero are equal.
  * "byte for byte" for the embedded nominal means equal canonical JSON (keys sorted, shortest round-trip floats) with
    the output of a separate `actinv waste budget` run of the same staged budget document, because the embedded value is
    a re-serialization of the same f64 values.
  * The order-statistic index k is the largest index for which the lower end of the two-sided Clopper-Pearson interval at
    the level of (n - k + 1)/n is at least the level, decided exactly as P(Bin(n, level) >= n - k + 1) <= (1 - level)/2
    in 60-digit decimal arithmetic.

The sample draw and the keep file are those of the P146 contract. Result field names are read only in the adapter
functions `a_*` below, one per field group; edit only those to align names. The fields read are:
  result:   schema, spec_sha256 (informational), rule_pack_sha256, confidence_level, samples, seed,
            order_statistic.{k,n,minimum_samples}, nominal, quantities.<q>.{unit,status,reason,
            positive_lower_bound_samples,nominal_supremum,budget,suprema_by_sample,suprema_sorted,sample_status,
            binding_tally[{step,table,column,count}],keep_probability_statement}, failed_samples[{sample,element,error}],
            verification[{quantity,sample,rank,point,value,classes,inside}], verification_pass, coverage
  nominal:  impurities.<El>.others_at_spec and joint_specification_margin
            ({status,feasible,no_response,lower_*,upper_supremum_*,binding_constraint{step,table,column}}),
            basis_solves[{element,steps{step{nuclide:Bq/g}}}]
  keep file sample_<i:04d>.json: sample, factors{flux_factor,rate_scale,decay_scale,yield_scale},
            elements.<El>.{factors,error,steps{step{t_s,activity_bq_per_g{}}},class_constraint_normalized_sums},
            intervals.<q>.{lower,supremum,status,binding{step,table,column}}
  study rob_<i>.json: options.{rate_scale,decay_scale,yield_scale}, spectrum.total (or flux_per_group)
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import random
import re
import shutil
import sys
import tempfile
from collections import Counter, defaultdict
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import check_p145 as p145  # noqa: E402  (the independent evaluator, Clopper-Pearson and incomplete beta)

# the evaluator canonicalizes every nuclide key on every call; the same few thousand keys recur
p145.canonical_nuclide = lru_cache(maxsize=None)(p145.canonical_nuclide)

OUT = ROOT / "results/p146"
WORK = ROOT / "target/p146"
INPUTS = ROOT / "controls/p146_inputs"
PROTOCOL = ROOT / "protocols/ACTINV-P146_PROTOCOL.md"
BINARY = Path(os.environ.get("ACTINV_BIN", ROOT / "target/release/actinv"))
TIMEOUT = int(os.environ.get("P146_TIMEOUT", "7200"))
RESULT_SCHEMA = "actinv-waste-budget-uncertainty-1"
SPEC_SCHEMA = "actinv-waste-budget-uncertainty-spec-1"
BUDGET_SCHEMA = "actinv-waste-budget-1"
PROPERTIES_TOKEN = "from_decay_data"
REL_TOL = 1e-12
ENDPOINT_TOL = 1e-8
BAND = 0.999
# the P145 candidate (0ea4a18) release binary the protocol names as the B6 study-regression reference
REFERENCE_BINARY_SHA256 = "db2d482f3706e12138a118edbed4368ffd821c4adde4b76898ffc19a98663a56"
CASES = {"D1": "d1_steel.json", "D2_V16": "d2_v16.json", "D2_V36": "d2_v36.json", "D2_V64": "d2_v64.json"}
D2_KEYS = ["D2_V16", "D2_V36", "D2_V64"]
STATUS_NAMES = ("bound", "capped", "failed", "infeasible", "no_response")
POINTS = ("0.99_supremum", "1.01_supremum", "0.99_budget")
CLASS_ORDER = {"A": 0, "B": 1, "C": 2, "above_class_c": 3}
JOINT = "joint_scale_factor"
MISSING = p145.MISSING

sha = p145.sha
sha_bytes = p145.sha_bytes
git = p145.git
tree_clean = p145.tree_clean
dump = p145.dump
strip_private = p145.strip_private
brief = p145.brief
pick = p145.pick
phi = p145.phi


# --- provenance ----------------------------------------------------------------------------------------------

def amendments() -> dict:
    return {p.name: sha(p) for p in sorted((ROOT / "protocols").glob("ACTINV-P146_AMENDMENT_*.md"))}


def candidate() -> dict:
    if not BINARY.is_file():
        raise SystemExit(f"binary {BINARY} not found (set ACTINV_BIN or build target/release/actinv)")
    return {"commit": git("rev-parse", "HEAD"), "tree_clean": tree_clean(),
            "binary": str(BINARY.relative_to(ROOT)) if BINARY.is_relative_to(ROOT) else str(BINARY),
            "binary_sha256": sha(BINARY), "protocol_sha256": sha(PROTOCOL), "amendments_sha256": amendments()}


def finish(name: str, report: dict) -> int:
    report["worktree_clean_after"] = tree_clean()
    report["pass"] = bool(report.get("pass")) and report["candidate"]["tree_clean"]
    dump(OUT / f"{name}.json", report)
    print(json.dumps({"gate": name, "pass": report["pass"], **report.get("summary", {})}, sort_keys=True))
    return 0 if report["pass"] else 1


def run_budget_uncertainty(stage: Path, spec: str = "spec.json", out: str = "result.json") -> dict:
    return p145.run_child([BINARY, "waste", "budget", "uncertainty", spec, out], stage, TIMEOUT)


def run_nominal(stage: Path, budget: str = "budget.json", out: str = "nominal_ref.json") -> dict:
    return p145.run_child([BINARY, "waste", "budget", budget, out], stage, TIMEOUT)


def run_study(stage: Path) -> dict:
    return p145.run_child([BINARY, "study", "run", "study.json", "study_out"], stage, TIMEOUT)


def load_json(path: Path):
    return json.loads(Path(path).read_text())


# --- staging -------------------------------------------------------------------------------------------------

def data_root() -> Path:
    path = Path(os.environ.get("P146_DATA", os.environ.get("P145_DATA", ROOT / "actinv-data")))
    if not path.is_dir():
        raise SystemExit(f"data tree {path} not found (set P146_DATA to the actinv-data directory)")
    return path.resolve()


def fresh_stage_at(stage: Path) -> Path:
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    (stage / "actinv-data").symlink_to(data_root())
    return stage


def fresh_stage(label: str) -> Path:
    return fresh_stage_at(WORK / label)


def resolve_input(spec_dir: Path, relative: str) -> Path:
    """A path named in a committed spec; a path through `actinv-data` is mapped onto the data tree."""
    normal = Path(os.path.normpath(spec_dir / relative))
    if "actinv-data" in normal.parts:
        tail = normal.parts[normal.parts.index("actinv-data") + 1:]
        return data_root().joinpath(*tail)
    return normal


def d2_sidecar(name: str):
    """A writer for a D2 synthetic covariance sidecar named `name` (returns its SHA-256), or None for any other file.
    The sidecars are generated by controls/p145_c3_make.py rather than tracked (P145 Amendment C)."""
    meta = INPUTS / "d2_cases.json"
    if not meta.exists():
        return None
    sigma2 = {entry["covariance"]: entry["sigma2"] for entry in load_json(meta)["cases"]}.get(name)
    if sigma2 is None:
        return None
    sys.path.insert(0, str(ROOT / "controls"))
    import p145_c3_make
    return lambda path: p145_c3_make.write_covariance(path, sigma2)


def nuclide_name(za: float, liso: int):
    """(canonical name, Z) of a decay record, or None for the neutron and malformed identities."""
    za = int(round(za))
    z, a = za // 1000, za % 1000
    if not 1 <= z <= len(p145.ELEMENTS) or a < 1:
        return None
    return f"{p145.ELEMENTS[z - 1]}{a}" + (f"m{liso}" if liso > 0 else ""), z


def properties_from_records(records) -> dict:
    """{nuclide: {z, half_life_s, alpha_emitting}} for the radioactive records, the first record of a nuclide winning
    (primary file before fallback file, as `DecayProperties` of the P145 checker)."""
    out, seen = {}, set()
    for record in records:
        ident = (int(round(record["za"])), int(record["liso"]))
        if ident in seen:
            continue
        seen.add(ident)
        named = nuclide_name(record["za"], int(record["liso"]))
        half_life = record["half_life"]
        if named is None or record["nst"] != 0 or not (math.isfinite(half_life) and half_life > 0.0):
            continue
        alpha = any(m["br"] > 0.0 and p145.rtyp_has_alpha(m["rtyp"]) for m in record["modes"])
        out[named[0]] = {"alpha_emitting": bool(alpha), "half_life_s": float(half_life), "z": named[1]}
    return dict(sorted(out.items()))


def property_map(decay: dict, base: Path) -> dict:
    """The nuclide properties of the decay files named by a base spec, parsed once per file set and cached."""
    if decay.get("overrides"):
        raise SystemExit("decay overrides are not supported by the P146 checker")
    files = [base / decay[field] for field in ("primary", "fallback") if decay.get(field)]
    stamp = json.dumps([[str(f.resolve()), f.stat().st_size, f.stat().st_mtime_ns] for f in files])
    cache = WORK / "properties" / (hashlib.sha256(stamp.encode()).hexdigest()[:20] + ".json")
    if cache.exists():
        return load_json(cache)
    parser = p145._endf_decay()

    def records():
        for f in files:
            yield from parser.parse_decay_file(str(f)).values()

    props = properties_from_records(records())
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(props, sort_keys=True))
    return props


def stage_case(spec_path: Path, stage: Path, *, keep: bool = False, edit=None, expand: bool = True) -> dict:
    """Copy a committed budget-uncertainty spec, its budget, base spec and covariance into `stage` as spec.json,
    budget.json, problem.json and either an `actinv-data/...` path or a generated or copied file; expand the
    nuclide-property token; apply the optional `edit(spec, budget, problem)` last."""
    spec = strip_private(load_json(spec_path))
    budget_path = spec_path.parent / spec["budget"]
    budget = strip_private(load_json(budget_path))
    problem_path = budget_path.parent / budget["base_spec"]
    problem = strip_private(load_json(problem_path))
    sampling = spec["sampling"]
    covariance = sampling.get("covariance")
    sidecar = d2_sidecar(Path(covariance["path"]).name) if covariance else None
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
    spec["budget"] = "budget.json"
    budget["base_spec"] = "problem.json"
    if keep:
        sampling["keep_samples"] = "keep"
        (stage / "keep").mkdir(exist_ok=True)
    else:
        sampling.pop("keep_samples", None)
    properties = None
    if expand and budget.get("nuclide_properties") == PROPERTIES_TOKEN:
        properties = property_map(problem["decay"], stage)
        budget["nuclide_properties"] = properties
    if edit is not None:
        edit(spec, budget, problem)
    dump(stage / "problem.json", problem)
    dump(stage / "budget.json", budget)
    dump(stage / "spec.json", spec)
    info = {"stage": stage, "spec": spec, "budget": budget, "problem": problem,
            "source_spec_sha256": sha(spec_path), "source_budget_sha256": sha(budget_path),
            "source_problem_sha256": sha(problem_path), "spec_sha256": sha(stage / "spec.json"),
            "budget_sha256": sha(stage / "budget.json"), "problem_sha256": sha(stage / "problem.json")}
    if properties is not None:
        info["properties_entries"] = len(properties)
        info["properties_sha256"] = sha_bytes(json.dumps(properties, sort_keys=True).encode())
    return info


def case_key(case: str) -> str:
    key = case.upper().replace("-", "_")
    if key in ("V16", "V36", "V64"):
        key = "D2_" + key
    if key not in CASES:
        raise SystemExit(f"unknown case {case!r}; choose from {sorted(CASES)}")
    return key


def case_path(key: str) -> Path:
    return INPUTS / CASES[key]


def study_id_for(key: str) -> str:
    return f"p146-b1-{key.lower()}"


def reload_stage(stage: Path) -> dict:
    return {"stage": stage, "spec": load_json(stage / "spec.json"), "budget": load_json(stage / "budget.json"),
            "problem": load_json(stage / "problem.json")}


def stage_for(key: str, before: dict, tag: str, keep: bool) -> tuple[Path, dict, dict | None]:
    """The b1 staging folder when its run used this binary and these inputs (and kept samples when `keep`);
    otherwise a fresh staging folder and a fresh run of the command. Returns (stage, staged inputs, command or None)."""
    path = case_path(key)
    stage = WORK / f"b1_{key.lower()}"
    meta_path = stage / "meta.json"
    if meta_path.exists() and (stage / "result.json").exists():
        meta = load_json(meta_path)
        spec_doc = load_json(path)
        budget_path = path.parent / spec_doc["budget"]
        problem_path = budget_path.parent / load_json(budget_path)["base_spec"]
        wanted = (before["binary_sha256"], sha(path), sha(budget_path), sha(problem_path), 0, True)
        have = (meta["binary_sha256"], meta["spec_sha256"], meta["budget_sha256"], meta["problem_sha256"], meta["rc"],
                bool(meta.get("keep")) or not keep)
        if have == wanted:
            staged = reload_stage(stage)
            staged.update({"reused_b1_run": True, "source_spec_sha256": sha(path),
                           "source_budget_sha256": sha(budget_path), "source_problem_sha256": sha(problem_path)})
            return stage, staged, None
    stage = fresh_stage(f"{tag}_{key.lower()}")
    staged = stage_case(path, stage, keep=keep)
    staged["reused_b1_run"] = False
    return stage, staged, brief(run_budget_uncertainty(stage))


def write_meta(stage: Path, before: dict, staged: dict, rc, keep: bool) -> None:
    (stage / "meta.json").write_text(json.dumps({
        "binary_sha256": before["binary_sha256"], "spec_sha256": staged["source_spec_sha256"],
        "budget_sha256": staged["source_budget_sha256"], "problem_sha256": staged["source_problem_sha256"],
        "rc": rc, "keep": keep}) + "\n")


# --- exact arithmetic helpers ----------------------------------------------------------------------------------

def nsum(values) -> float:
    """Sequential left-to-right sum from zero, as Rust's Iterator::sum on f64 (Python's sum() compensates)."""
    total = 0.0
    for value in values:
        total += value
    return total


def close(a, b, tol: float = REL_TOL) -> bool:
    if a is None or b is None:
        return a is b
    return a == b or abs(a - b) <= tol * max(abs(a), abs(b))


def key_order(key):
    return key[0], key[1], -1 if key[2] is None else key[2]


def key_text(key) -> str:
    return f"{key[0]}:{key[1]}:{'all' if key[2] is None else key[2]}"


def parse_key(text: str):
    step, table, column = text.split(":")
    return int(step), int(table), None if column == "all" else int(column)


def full_composition(balance: str, matrix: dict, impurities: dict) -> dict:
    """The Rust `full_composition`: matrix plus impurities (summed per element), the balance element taking the rest."""
    composition = dict(matrix)
    for element, weight in impurities.items():
        composition[element] = composition.get(element, 0.0) + weight
    total = nsum(composition[k] for k in sorted(composition))
    rest = 100.0 - total
    if not math.isfinite(total) or not math.isfinite(rest) or rest <= 0.0:
        raise ValueError(f"fixed constituents sum to {total} wt%; balance element must remain positive")
    composition[balance] = rest
    return {k: composition[k] for k in sorted(composition)}


# --- statistics: the order statistic, the beta coverage ----------------------------------------------------------

@lru_cache(maxsize=None)
def tail_ok(m: int, n: int, level: float) -> bool:
    """The lower end of the Clopper-Pearson interval of m successes in n trials is at least `level`, decided as
    P(Bin(n, level) >= m) <= (1 - level)/2 in 60-digit decimal arithmetic."""
    x = Decimal(repr(level))
    return p145.binom_tail_ge(m, n, x) <= (Decimal(1) - x) / 2


def order_index(n: int, level: float):
    """The largest k in 1..n with tail_ok(n - k + 1, n, level); None when no sample count can establish (n too small).
    tail_ok is monotone in m, so the smallest m is found by bisection and k = n + 1 - m."""
    if not tail_ok(n, n, level):
        return None
    low, high = 1, n
    while low < high:
        mid = (low + high) // 2
        if tail_ok(mid, n, level):
            high = mid
        else:
            low = mid + 1
    return n + 1 - low


def beta_quantile(a: float, b: float, p: float) -> float:
    """The x with I_x(a, b) = p, by bisection on the regularized incomplete beta of the P145 checker."""
    low, high = 0.0, 1.0
    for _ in range(200):
        mid = (low + high) / 2.0
        if p145.betai(a, b, mid) < p:
            low = mid
        else:
            high = mid
    return (low + high) / 2.0


def coverage_band(n: int, k: int, band: float = BAND) -> tuple[float, float]:
    """The central `band` of Beta(n - k + 1, k), the coverage distribution of the k-th smallest of n continuous draws."""
    tail = (1.0 - band) / 2.0
    a, b = float(n - k + 1), float(k)
    return beta_quantile(a, b, tail), beta_quantile(a, b, 1.0 - tail)


def keep_probability(x0: float, x: float, sigma2: float) -> float:
    """P(x < x*) with x* = x0 / F and F = exp(sigma z - sigma2/2): Phi((ln(x0/x) + sigma2/2)/sigma). Note the plus sign."""
    return phi((math.log(x0 / x) + sigma2 / 2.0) / math.sqrt(sigma2))


# --- the budget model: the independent twin of waste_budget.rs ---------------------------------------------------

class Edge:
    __slots__ = ("value", "open", "binding")

    def __init__(self, value: float, open_: bool, binding=None):
        self.value, self.open, self.binding = value, open_, binding


class Interval:
    __slots__ = ("lower", "upper", "feasible", "status", "no_response")

    def __init__(self, lower, upper, feasible, status, no_response):
        self.lower, self.upper, self.feasible, self.status, self.no_response = lower, upper, feasible, status, no_response


def interval_for(base: dict, slope: dict, cap: float, endpoint) -> Interval:
    """The interval of x with base[key] + slope[key] x <= 1 for every key, 0 <= x <= cap; `endpoint(key, x)` says
    whether the constraint at x is a strict mixture boundary (more than one contributor). Port of `interval_for`."""
    lower, upper = Edge(0.0, False), Edge(cap, True)
    feasible, status, no_response = cap > 0.0, "limit", True
    for key in sorted(base, key=key_order):
        b = base[key]
        g = slope.get(key, 0.0)
        if g == 0.0:
            if b > 1.0:
                feasible, status = False, "infeasible"
            elif b == 1.0:
                if endpoint(key, 0.0):
                    feasible, status = False, "infeasible"
                elif cap > 0.0 and endpoint(key, cap * 0.5):
                    # the sum is unchanged but the contributor count jumps as soon as a zero-activity material
                    # receives positive mass: only x = 0 remains admissible
                    upper, status = Edge(0.0, False, key), "singleton"
            continue
        no_response = False
        edge = (1.0 - b) / g
        if not math.isfinite(edge):
            feasible, status = False, "nonfinite_boundary"
            continue
        if g > 0.0:
            if edge < 0.0:
                feasible, status = False, "infeasible"
            elif edge <= upper.value:
                is_open = True if edge >= cap else endpoint(key, edge)
                if edge < upper.value:
                    upper = Edge(edge, is_open, key)
                else:
                    if is_open and not upper.open:
                        upper.binding = key
                    upper.open = upper.open or is_open
                    if upper.binding is None:
                        upper.binding = key
        elif edge > upper.value:
            feasible, status = False, "infeasible"
        elif edge >= lower.value:
            is_open = True if edge >= cap else endpoint(key, edge)
            if edge > lower.value:
                lower = Edge(edge, is_open, key)
            else:
                if is_open and not lower.open:
                    lower.binding = key
                lower.open = lower.open or is_open
                if lower.binding is None:
                    lower.binding = key
    if upper.value < lower.value or (upper.value == lower.value and (upper.open or lower.open)):
        feasible, status = False, "infeasible"
    if feasible and upper.binding is None and upper.value == cap:
        status = "composition_bound"
    if no_response and feasible and status != "singleton":
        status = "no_response"
    return Interval(lower, upper, feasible, status, no_response)


class Basis:
    """One element at 100 wt%: Bq/g by target step and the target-class normalized sums by (step, table, column)."""
    __slots__ = ("acts", "sums")

    def __init__(self, acts: dict, sums: dict):
        self.acts, self.sums = acts, sums


class Model:
    """The budget of a staged budget document, computed independently of the Rust source."""

    def __init__(self, doc: dict, base: Path):
        if doc.get("schema") != BUDGET_SCHEMA:
            raise ValueError(f"unsupported waste budget schema {doc.get('schema')!r}")
        declared = doc["nuclide_properties"]
        if not isinstance(declared, dict):
            raise ValueError("nuclide_properties is not expanded; stage the case first")
        self.balance = doc["balance"]
        self.matrix = dict(sorted(doc.get("matrix", {}).items()))
        self.impurities = dict(sorted(doc["impurities"].items()))
        self.targets = [int(t) for t in doc["targets"]]
        self.target_class = doc["target_class"]
        self.waste_type = doc["waste_type"]
        self.mass, self.volume = p145.geometry({"id": "budget", "mass_g": doc["mass_g"],
                                                "displaced_volume_cm3": doc.get("displaced_volume_cm3"),
                                                "density_g_cm3": doc.get("density_g_cm3")})
        self.rules = p145.load_rules(doc["rules"], base)
        self.props = {p145.canonical_nuclide(k): (v["z"], v["half_life_s"], v["alpha_emitting"])
                      for k, v in declared.items()}
        self.external = doc["external_tritium"]
        self.elements = sorted({self.balance} | set(self.matrix) | set(self.impurities))
        self.matrix_total = nsum(self.matrix.values())
        self.impurity_total = nsum(self.impurities.values())
        self.external_sums = self._external_sums()

    # --- evaluation of an inventory ---
    def evaluate_bq(self, bq: dict) -> dict:
        props = {}
        for name, value in bq.items():
            if value != 0.0:
                key = p145.canonical_nuclide(name)
                if key in self.props:
                    props[key] = self.props[key]
        return p145.evaluate(self.rules, self.waste_type, self.mass, self.volume, bq, props)

    def class_sums(self, evaluation: dict, step: int) -> dict:
        return {(step, c["table"], c["column"]): c["normalized_sum"]
                for c in evaluation["constraints"] if c["target_class"] == self.target_class}

    def external_h3(self, step: int) -> float:
        status = self.external.get("status")
        if status == "not_applicable":
            return 0.0
        if status == "declared":
            return float(self.external["activity_bq"][str(step)])
        raise ValueError(f"external tritium status {status!r} is refused by the class budget")

    def _external_sums(self) -> dict:
        sums = {}
        for step in self.targets:
            h3 = self.external_h3(step)
            evaluation = self.evaluate_bq({"H-3": h3} if h3 > 0.0 else {})
            if evaluation["unknown_nuclides"]:
                raise ValueError(f"external H-3 lacks required nuclide properties at step {step}")
            sums.update(self.class_sums(evaluation, step))
        return sums

    def element_basis(self, steps: dict) -> Basis:
        """The basis of one element from its Bq/g by target step ({step: {nuclide: Bq/g}}); ValueError when an
        activity is invalid or a nuclide lacks properties (the class budget refuses incomplete coverage)."""
        sums, acts = {}, {}
        for step in self.targets:
            if step not in steps:
                raise ValueError(f"no activity for step {step}")
            per_g = steps[step]
            for name, value in per_g.items():
                if not (isinstance(value, (int, float)) and math.isfinite(value) and value >= 0.0):
                    raise ValueError(f"activity {name} must be finite and nonnegative")
            evaluation = self.evaluate_bq({name: value * self.mass for name, value in per_g.items()})
            if evaluation["unknown_nuclides"]:
                raise ValueError(f"incomplete property coverage at step {step}: {evaluation['unknown_nuclides'][:5]}")
            sums.update(self.class_sums(evaluation, step))
            acts[step] = per_g
        return Basis(acts, sums)

    # --- linear composition of the element bases ---
    def sums_for(self, basis: dict, composition: dict) -> dict:
        out = {}
        for key in sorted(basis[self.balance].sums, key=key_order):
            out[key] = nsum(w / 100.0 * basis[el].sums.get(key, 0.0) for el, w in composition.items())
        for key, value in self.external_sums.items():
            out[key] = out.get(key, 0.0) + value
        return out

    def scaled_activities(self, basis: dict, composition: dict, step: int) -> dict:
        result = {}
        for element, weight in composition.items():
            for nuclide, per_g in basis[element].acts[step].items():
                result[nuclide] = result.get(nuclide, 0.0) + weight / 100.0 * per_g * self.mass
        return result

    def inventory(self, basis: dict, composition: dict, step: int) -> dict:
        bq = self.scaled_activities(basis, composition, step)
        h3 = self.external_h3(step)
        if h3 > 0.0:
            bq["H3"] = bq.get("H3", 0.0) + h3
        return bq

    def classes_at(self, basis: dict, quantity: str, value: float) -> dict:
        """{str(step): class} of the full material with `quantity` (an impurity, or the joint scale factor) at `value`."""
        if quantity == JOINT:
            impurities = {el: w * value for el, w in self.impurities.items()}
        else:
            impurities = dict(self.impurities)
            impurities[quantity] = value
        composition = full_composition(self.balance, self.matrix, impurities)
        return {str(step): self.evaluate_bq(self.inventory(basis, composition, step))["class"] for step in self.targets}

    def endpoint_fn(self, basis: dict, others: dict, variable: str, joint: bool):
        """`endpoint_open` of the Rust budget: classify the composition at the boundary value and report whether the
        constraint there is a strict mixture boundary; ValueError when the boundary sum is not one."""
        def endpoint(key, edge: float) -> bool:
            if joint:
                impurities = {el: w * edge for el, w in others.items()}
            else:
                impurities = dict(others)
                impurities[variable] = edge
            composition = full_composition(self.balance, self.matrix, impurities)
            evaluation = self.evaluate_bq(self.inventory(basis, composition, key[0]))
            if evaluation["unknown_nuclides"]:
                raise ValueError(f"boundary composition has incomplete coverage at step {key[0]}")
            found = [c for c in evaluation["constraints"] if c["target_class"] == self.target_class
                     and c["table"] == key[1] and c["column"] == key[2]]
            if not found:
                raise ValueError("endpoint has no matching class constraint")
            if abs(found[0]["normalized_sum"] - 1.0) > ENDPOINT_TOL:
                raise ValueError(f"computed endpoint does not meet its normalized class boundary: "
                                 f"{found[0]['normalized_sum']}")
            return bool(found[0]["strict"])
        return endpoint

    def intervals(self, basis: dict) -> dict:
        """{impurity: Interval (others at specification), "joint_scale_factor": Interval}."""
        balance_sums = basis[self.balance].sums
        out = {}
        for impurity in self.impurities:
            others = {k: v for k, v in self.impurities.items() if k != impurity}
            base = self.sums_for(basis, full_composition(self.balance, self.matrix, others))
            imp_sums = basis[impurity].sums
            slope = {key: (imp_sums.get(key, 0.0) - balance_sums.get(key, 0.0)) / 100.0 for key in base}
            cap = 100.0 - nsum(self.matrix.values()) - nsum(others.values())
            out[impurity] = interval_for(base, slope, cap, self.endpoint_fn(basis, others, impurity, False))
        matrix_sums = self.sums_for(basis, full_composition(self.balance, self.matrix, {}))
        if self.impurity_total > 0.0:
            joint_slope = {}
            for key in matrix_sums:
                joint_slope[key] = nsum(w * (basis[el].sums.get(key, 0.0) - balance_sums.get(key, 0.0)) / 100.0
                                        for el, w in self.impurities.items())
            joint_cap = (100.0 - self.matrix_total) / self.impurity_total
            out[JOINT] = interval_for(matrix_sums, joint_slope, joint_cap,
                                      self.endpoint_fn(basis, self.impurities, "", True))
        else:
            out[JOINT] = Interval(Edge(0.0, False), Edge(0.0, False), True, "no_response", True)
        return out

    def quantities(self) -> list:
        return [*self.impurities, JOINT]


def outcome_of(interval: Interval | None) -> dict:
    """One sample's result for one quantity: status, supremum value, binding constraint and lower-bound flag."""
    if interval is None:
        return {"status": "failed", "value": 0.0, "binding": None, "lower": 0.0, "positive_lower": False}
    if not interval.feasible:
        return {"status": "infeasible", "value": 0.0, "binding": None, "lower": 0.0, "positive_lower": False}
    if interval.status == "no_response":
        status = "no_response"
    elif interval.status == "composition_bound":
        status = "capped"
    else:
        status = "bound"
    return {"status": status, "value": None if status == "no_response" else interval.upper.value,
            "binding": interval.upper.binding if status == "bound" else None, "lower": interval.lower.value,
            "positive_lower": interval.lower.value > 0.0}


def nominal_view(node: dict) -> dict:
    """The reported nominal interval of a quantity as {upper, lower, status, feasible, no_response, binding}."""
    return node


def aggregate_quantity(outcomes: list, k: int, nominal: dict, joint: bool) -> dict:
    """The expected report of one quantity from its per-sample outcomes (protocol item 4)."""
    n = len(outcomes)
    counts = Counter(o["status"] for o in outcomes)
    positive = sum(1 for o in outcomes if o["positive_lower"])
    nominal_positive = bool(nominal["feasible"]) and nominal["lower"] > 0.0
    no_response = counts.get("no_response", 0)
    unsupported = None
    if 0 < no_response < n:
        unsupported = "samples mix no_response with bounded intervals (the checker does not model that case)"
    if unsupported:
        status = "unsupported"
    elif no_response == n and nominal["no_response"]:
        status = "unbounded"
    elif positive > 0 or nominal_positive:
        status = "not_established"
    else:
        status = "established"
    finite = sorted(o["value"] for o in outcomes if o["value"] is not None)
    tally = Counter(o["binding"] for o in outcomes if o["binding"] is not None)
    return {"unit": "scale_factor" if joint else "wt_pct", "status": status,
            "reason": "positive_lower_bound" if status == "not_established" else None,
            "positive": positive, "nominal_positive": nominal_positive, "nominal_supremum": nominal["upper"],
            "budget": finite[k - 1] if status == "established" else None,
            "by_sample": [o["value"] for o in outcomes], "sorted": finite,
            "sample_status": {name: counts.get(name, 0) for name in STATUS_NAMES}, "tally": dict(tally),
            "unsupported": unsupported, "samples": n}


# --- adapters: the only code that names result and keep fields -------------------------------------------------

def a_order(result: dict) -> dict:
    node = pick(result, "order_statistic")
    return {name: pick(node, name) for name in ("k", "n", "minimum_samples")}


def a_quantities(result: dict) -> dict:
    node = pick(result, "quantities")
    return node if isinstance(node, dict) else {}


def a_quantity(node) -> dict:
    return {"unit": pick(node, "unit"), "status": pick(node, "status"), "reason": pick(node, "reason"),
            "positive": pick(node, "positive_lower_bound_samples"), "nominal_supremum": pick(node, "nominal_supremum"),
            "budget": pick(node, "budget"), "by_sample": pick(node, "suprema_by_sample"),
            "sorted": pick(node, "suprema_sorted"), "sample_status": pick(node, "sample_status"),
            "tally": pick(node, "binding_tally"), "statement": pick(node, "keep_probability_statement")}


def a_tally(rows):
    """{(step, table, column or None): count} from the reported binding tally, or MISSING."""
    if not isinstance(rows, list):
        return MISSING
    out = {}
    try:
        for row in rows:
            column = row["column"]
            out[(int(row["step"]), int(row["table"]), None if column in (None, "all") else int(column))] = int(row["count"])
    except (KeyError, TypeError, ValueError):
        return MISSING
    return out


def a_failed(result: dict):
    node = pick(result, "failed_samples")
    return node if isinstance(node, list) else MISSING


def a_verification(result: dict):
    node = pick(result, "verification")
    return node if isinstance(node, list) else MISSING


def a_nominal(result: dict):
    return pick(result, "nominal")


def a_nominal_interval(nominal: dict, quantity: str) -> dict:
    """{upper, lower, status, feasible, no_response, binding} of a nominal interval, from the P105 result document."""
    if quantity == JOINT:
        node = pick(nominal, "joint_specification_margin")
        lower_key, upper_key = "lower_scale_factor", "upper_supremum_scale_factor"
    else:
        impurity = pick(pick(nominal, "impurities"), quantity)
        node = pick(impurity, "others_at_spec")
        lower_key, upper_key = "lower_wt_pct", "upper_supremum_wt_pct"
    binding = pick(node, "binding_constraint")
    return {"upper": pick(node, upper_key), "lower": pick(node, lower_key), "status": pick(node, "status"),
            "feasible": pick(node, "feasible"), "no_response": pick(node, "no_response"),
            "binding": None if binding is None or binding is MISSING else
            (binding.get("step"), binding.get("table"), binding.get("column"))}


def a_nominal_steps(nominal: dict) -> dict:
    """{element: {step: {nuclide: Bq/g}}} from the nominal result's elemental basis solves."""
    out = {}
    for solve in pick(nominal, "basis_solves") or []:
        out[solve["element"]] = {int(step): acts for step, acts in solve["steps"].items()}
    return out


def a_kept_steps(item: dict) -> dict:
    """{step: {nuclide: Bq/g}} of a kept element."""
    return {int(step): row["activity_bq_per_g"] for step, row in item["steps"].items()}


def a_kept_sums(item: dict):
    node = pick(item, "class_constraint_normalized_sums")
    if not isinstance(node, dict):
        return MISSING
    return {parse_key(text): value for text, value in node.items()}


def a_kept_block(node) -> dict:
    """A factor block normalized to {flux_factor, rate_scale, decay_scale, yield_scale} with string keys."""
    def scale(name):
        value = pick(node, name)
        return {} if value in (MISSING, None) else {str(k): float(v) for k, v in value.items()}
    flux = pick(node, "flux_factor")
    return {"flux_factor": 1.0 if flux in (MISSING, None) else float(flux), "rate_scale": scale("rate_scale"),
            "decay_scale": scale("decay_scale"), "yield_scale": scale("yield_scale")}


def a_kept_interval(doc: dict, quantity: str):
    node = pick(pick(doc, "intervals"), quantity)
    if not isinstance(node, dict):
        return MISSING
    binding = pick(node, "binding")
    return {"lower": pick(node, "lower"), "supremum": pick(node, "supremum"), "status": pick(node, "status"),
            "binding": None if binding in (MISSING, None) else
            (binding.get("step"), binding.get("table"), binding.get("column"))}


def a_study_block(rob: dict, nominal_spectrum: dict) -> dict:
    """The factor block a study sample's perturbed spec carries: the options scale maps and the flux multiplier."""
    options = pick(rob, "options")
    spectrum = pick(rob, "spectrum")

    def scale(name):
        value = pick(options, name)
        return {} if value in (MISSING, None) else {str(k): float(v) for k, v in value.items()}

    if "total" in nominal_spectrum and nominal_spectrum["total"] is not None:
        flux = spectrum["total"] / nominal_spectrum["total"]
    else:
        pairs = [(r, n) for r, n in zip(spectrum["flux_per_group"], nominal_spectrum["flux_per_group"]) if n != 0.0]
        flux = pairs[0][0] / pairs[0][1] if pairs else 1.0
    return {"flux_factor": flux, "rate_scale": scale("rate_scale"), "decay_scale": scale("decay_scale"),
            "yield_scale": scale("yield_scale")}


# --- comparison harness --------------------------------------------------------------------------------------

class Checks(p145.Checks):
    def rel(self, label: str, expected, actual, tol: float = REL_TOL) -> bool:
        if not self.has(label, actual):
            return False
        if expected is None or actual is None:
            return self._record(expected is actual, label, expected, actual)
        return self._record(close(float(expected), float(actual), tol), label, expected, actual)


def canonical_json(doc) -> str:
    return json.dumps(doc, sort_keys=True, separators=(",", ":"))


def compare_sums(mine: dict, kept) -> float:
    """Largest relative difference between independent and kept normalized sums; infinity for different key sets."""
    if kept is MISSING or set(kept) != set(mine):
        return math.inf
    worst = 0.0
    for key, value in mine.items():
        other = kept[key]
        if value != other:
            worst = max(worst, abs(value - other) / max(abs(value), abs(other), 1e-300))
    return worst


def build_basis(model: Model, doc: dict):
    """(basis or None, [(element, error)], worst sum deviation) from a kept sample."""
    errors, basis, deviation = [], {}, 0.0
    elements = pick(doc, "elements")
    elements = elements if isinstance(elements, dict) else {}
    for element in model.elements:
        item = elements.get(element)
        if item is None:
            errors.append((element, "missing from the kept sample"))
            continue
        if item.get("error"):
            errors.append((element, str(item["error"])))
            continue
        try:
            built = model.element_basis(a_kept_steps(item))
        except (ValueError, KeyError, TypeError) as error:
            errors.append((element, f"{type(error).__name__}: {error}"))
            continue
        basis[element] = built
        deviation = max(deviation, compare_sums(built.sums, a_kept_sums(item)))
    return (None if errors else basis), errors, deviation


def analyse_sample(model: Model, doc: dict) -> dict:
    basis, errors, deviation = build_basis(model, doc)
    if basis is None:
        return {"failed": errors, "basis": None, "intervals": None, "deviation": deviation}
    try:
        intervals = model.intervals(basis)
    except (ValueError, KeyError) as error:
        return {"failed": [(None, f"interval: {error}")], "basis": basis, "intervals": None, "deviation": deviation}
    return {"failed": [], "basis": basis, "intervals": intervals, "deviation": deviation}


def keep_path(keep: Path, index: int) -> Path:
    return keep / f"sample_{index:04d}.json"


def compare_quantity(checks: Checks, label: str, expected: dict, reported: dict) -> None:
    checks.eq(f"{label} unit", expected["unit"], reported["unit"])
    checks.eq(f"{label} status", expected["status"], reported["status"])
    if expected["reason"] is not None:
        checks.eq(f"{label} reason", expected["reason"], reported["reason"])
    positive = reported["positive"]
    if checks.has(f"{label} positive_lower_bound_samples", positive):
        allowed = {expected["positive"], expected["positive"] + (1 if expected["nominal_positive"] else 0)}
        checks._record(positive in allowed, f"{label} positive_lower_bound_samples", sorted(allowed), positive)
    checks.rel(f"{label} nominal_supremum", expected["nominal_supremum"], reported["nominal_supremum"])
    checks.rel(f"{label} budget", expected["budget"], reported["budget"])
    for name, key in (("suprema_by_sample", "by_sample"), ("suprema_sorted", "sorted")):
        values = reported[key]
        if not checks.has(f"{label} {name}", values):
            continue
        if not checks.eq(f"{label} {name} length", len(expected[key]), len(values)):
            continue
        bad = [(i, e, v) for i, (e, v) in enumerate(zip(expected[key], values)) if not close(e, v)]
        checks._record(not bad, f"{label} {name}", "within 1e-12 relative", bad[:3])
    checks.eq(f"{label} sample_status", expected["sample_status"], reported["sample_status"])
    tally = a_tally(reported["tally"])
    if checks.has(f"{label} binding_tally", tally):
        checks.eq(f"{label} binding_tally", expected["tally"], tally)
    statement = reported["statement"]
    if checks.has(f"{label} keep_probability_statement", statement):
        checks._record(isinstance(statement, str) and bool(statement), f"{label} keep_probability_statement",
                       "a nonempty string", statement)


def b2_compare(model: Model, spec: dict, result: dict, keep: Path, reference_nominal) -> tuple[Checks, dict]:
    """Protocol B2: recompute from the kept per-element results and compare with the reported result."""
    checks = Checks()
    n = spec["sampling"]["samples"]
    level = float(spec.get("confidence_level", 0.95))
    checks.eq("schema", RESULT_SCHEMA, pick(result, "schema"))
    checks.eq("confidence_level", level, pick(result, "confidence_level"))
    checks.eq("samples", n, pick(result, "samples"))
    checks.eq("seed", spec["sampling"]["seed"], pick(result, "seed"))
    if model.rules.id == p145.BUNDLED_RULES_ID:
        checks.eq("rule_pack_sha256", sha(p145.RULES_FILE), pick(result, "rule_pack_sha256"))
    k = order_index(n, level)
    minimum = p145.minimum_samples(level)
    order = a_order(result)
    checks._record(k is not None, "a budget exists at this sample count", f">= {minimum} samples", n)
    checks.eq("order_statistic.n", n, order["n"])
    checks.eq("order_statistic.k", k, order["k"])
    checks.eq("order_statistic.minimum_samples", minimum, order["minimum_samples"])
    details = {"k": k, "minimum_samples": minimum, "n": n}
    # the reported nominal equals a separate nominal run, and the independent nominal matches it
    nominal = a_nominal(result)
    if checks.has("nominal", nominal):
        if reference_nominal is not None:
            checks._record(canonical_json(nominal) == canonical_json(reference_nominal),
                           "nominal equals the separate `actinv waste budget` output (canonical JSON)", "equal", "differs")
        nominal_basis = {}
        try:
            for element, steps in a_nominal_steps(nominal).items():
                nominal_basis[element] = model.element_basis(steps)
            nominal_intervals = model.intervals(nominal_basis)
        except (ValueError, KeyError) as error:
            checks._record(False, "independent nominal recomputation", "an interval set", str(error))
            nominal_intervals = {}
        worst = 0.0
        for quantity, interval in nominal_intervals.items():
            reported = a_nominal_interval(nominal, quantity)
            checks.eq(f"nominal {quantity} feasible", interval.feasible, reported["feasible"])
            checks.eq(f"nominal {quantity} status", interval.status, reported["status"])
            if interval.feasible:
                checks.rel(f"nominal {quantity} supremum", interval.upper.value, reported["upper"])
                checks.rel(f"nominal {quantity} lower", interval.lower.value, reported["lower"])
                checks.eq(f"nominal {quantity} binding", interval.upper.binding, reported["binding"])
                if reported["upper"] not in (MISSING, None) and interval.upper.value:
                    worst = max(worst, abs(interval.upper.value - reported["upper"]) / abs(interval.upper.value))
        details["nominal_worst_relative_difference"] = worst
        details["nominal_intervals"] = {q: {"status": i.status, "feasible": i.feasible, "upper": i.upper.value,
                                           "lower": i.lower.value} for q, i in nominal_intervals.items()}
    else:
        nominal_intervals = {}
    # per-sample recomputation
    quantities = model.quantities()
    outcomes = {q: [] for q in quantities}
    failed_expected, failed_elements, deviation, kept_missing = set(), set(), 0.0, []
    for index in range(n):
        path = keep_path(keep, index)
        if not path.exists():
            kept_missing.append(index)
            for q in quantities:
                outcomes[q].append(outcome_of(None))
            failed_expected.add(index)
            continue
        doc = load_json(path)
        checks.eq(f"sample {index} index", index, pick(doc, "sample"))
        analysis = analyse_sample(model, doc)
        deviation = max(deviation, analysis["deviation"])
        if analysis["failed"]:
            failed_expected.add(index)
            failed_elements.update((index, e) for e, _ in analysis["failed"])
        for q in quantities:
            iv = None if analysis["intervals"] is None else analysis["intervals"][q]
            outcomes[q].append(outcome_of(iv))
            if iv is None:
                continue
            kept = a_kept_interval(doc, q)
            label = f"sample {index} {q}"
            if kept is MISSING:
                checks.has(f"{label} kept interval", MISSING)
                continue
            mine = outcomes[q][-1]
            checks.eq(f"{label} kept status", mine["status"], kept["status"])
            if mine["status"] in ("bound", "capped"):
                checks.rel(f"{label} kept supremum", mine["value"], kept["supremum"])
                checks.rel(f"{label} kept lower", mine["lower"], kept["lower"])
            if mine["status"] == "bound":
                checks.eq(f"{label} kept binding", mine["binding"], kept["binding"])
    checks._record(not kept_missing, "every sample has a kept file", [], kept_missing[:10])
    checks._record(deviation <= REL_TOL, "kept element normalized sums equal the independent sums within 1e-12",
                   REL_TOL, deviation)
    details.update({"worst_kept_sum_deviation": deviation, "failed_samples": sorted(failed_expected)[:50],
                    "failed_count": len(failed_expected), "kept_missing": kept_missing[:20]})
    reported_quantities = a_quantities(result)
    expected_quantities = {}
    for q in quantities:
        nominal_view_q = (a_nominal_interval(nominal, q) if nominal is not MISSING and nominal is not None
                          and q in nominal_intervals else None)
        if nominal_view_q is None or nominal_view_q["upper"] is MISSING:
            interval = nominal_intervals.get(q)
            nominal_view_q = {"upper": None if interval is None else interval.upper.value,
                              "lower": 0.0 if interval is None else interval.lower.value,
                              "feasible": interval is not None and interval.feasible,
                              "no_response": interval is not None and interval.no_response}
        expected = aggregate_quantity(outcomes[q], k or 1, nominal_view_q, q == JOINT)
        expected_quantities[q] = expected
        if not checks.has(f"quantity {q}", reported_quantities.get(q, MISSING)):
            continue
        checks._record(expected["unsupported"] is None, f"{q} modelled case", None, expected["unsupported"])
        compare_quantity(checks, q, expected, a_quantity(reported_quantities[q]))
    checks._record(set(reported_quantities) == set(quantities), "reported quantity names", sorted(quantities),
                   sorted(reported_quantities))
    # failed samples
    failed = a_failed(result)
    if checks.has("failed_samples", failed):
        reported_indices = {int(row["sample"]) for row in failed}
        checks.eq("failed sample indices", sorted(failed_expected), sorted(reported_indices))
        known = {(i, e) for i, e in failed_elements}
        unknown = [(row["sample"], row.get("element")) for row in failed
                   if row.get("element") is not None and (int(row["sample"]), row["element"]) not in known]
        checks._record(not unknown, "every reported failed element failed in the kept sample", [], unknown[:5])
    coverage = pick(result, "coverage")
    if checks.has("coverage block", coverage):
        checks._record(isinstance(coverage, dict) and bool(coverage), "coverage block is a nonempty object", "object",
                       coverage)
    details["quantities"] = {q: {"status": e["status"], "budget": e["budget"], "nominal_supremum": e["nominal_supremum"],
                                 "sample_status": e["sample_status"]} for q, e in expected_quantities.items()}
    return checks, details


def classes_inside(classes, target: str) -> bool:
    return isinstance(classes, dict) and bool(classes) and all(
        c in CLASS_ORDER and CLASS_ORDER[c] <= CLASS_ORDER[target] for c in classes.values())


def classes_outside(classes, target: str) -> bool:
    """Not inside and in a later class at some target: no unknown class, at least one class after the target class."""
    return (isinstance(classes, dict) and bool(classes) and all(c in CLASS_ORDER for c in classes.values())
            and any(CLASS_ORDER[c] > CLASS_ORDER[target] for c in classes.values()))


def b3_check(model: Model, spec: dict, result: dict, keep: Path) -> tuple[Checks, dict]:
    """Protocol B3: every verification row is inside at 0.99 points and outside at 1.01 points, and the required rank
    set is covered. Works from the reported suprema (B2 proves them) and, as a cross-check, predicts every row's classes
    from the kept element bases by the linear composition."""
    checks = Checks()
    n = spec["sampling"]["samples"]
    level = float(spec.get("confidence_level", 0.95))
    k = order_index(n, level)
    checks.eq("verification_pass", True, pick(result, "verification_pass"))
    rows = a_verification(result)
    if not checks.has("verification rows", rows):
        return checks, {"rows": 0}
    reported = a_quantities(result)
    by_quantity = defaultdict(list)
    for row in rows:
        by_quantity[pick(row, "quantity")].append(row)
    checks._record(set(by_quantity) <= set(model.quantities()), "rows name known quantities", sorted(model.quantities()),
                   sorted(map(str, by_quantity)))
    cache, predictions, unpredictable, details = {}, 0, 0, {"rows": len(rows), "quantities": {}}

    def predicted(sample: int, quantity: str, value: float):
        nonlocal predictions, unpredictable
        if sample not in cache:
            cache[sample] = build_basis(model, load_json(keep_path(keep, sample)))[0]
        basis = cache[sample]
        if basis is None:
            unpredictable += 1
            return None
        try:
            classes = model.classes_at(basis, quantity, value)
        except ValueError:
            unpredictable += 1
            return None
        predictions += 1
        return classes

    for quantity in model.quantities():
        info = a_quantity(reported.get(quantity))
        mine = by_quantity.get(quantity, [])
        status = info["status"]
        if status != "established":
            checks.eq(f"{quantity} rows when not established", 0, len(mine))
            continue
        by_sample, ordered, budget = info["by_sample"], info["sorted"], info["budget"]
        if not (checks.has(f"{quantity} suprema", by_sample) and checks.has(f"{quantity} sorted", ordered)):
            continue
        positive = lambda v: v is not None and math.isfinite(v) and v > 0.0
        wanted_ranks = [r for r in range(k - 2, k + 3) if 1 <= r <= n and positive(ordered[r - 1])]
        rows_by_point = {point: [r for r in mine if pick(r, "point") == point] for point in POINTS}
        checks._record(sum(len(v) for v in rows_by_point.values()) == len(mine), f"{quantity} rows use known points",
                       list(POINTS), sorted({str(pick(r, "point")) for r in mine}))
        seen = Counter((pick(r, "sample"), pick(r, "point")) for r in mine)
        checks._record(all(c == 1 for c in seen.values()), f"{quantity} rows are unique per sample and point", 1,
                       [key for key, c in seen.items() if c > 1][:3])
        # the rank convention: sorted[rank - 1] (one-based) or sorted[rank] (zero-based) is the row sample's supremum
        sup_rows = rows_by_point["0.99_supremum"] + rows_by_point["1.01_supremum"]

        def fits(offset):
            try:
                return all(close(ordered[int(pick(r, "rank")) - offset], by_sample[int(pick(r, "sample"))]) for r in sup_rows)
            except (IndexError, TypeError, ValueError):
                return False

        one_based = fits(1)
        if not one_based and not fits(0):
            checks._record(False, f"{quantity} rank matches the sample's place in the sorted suprema", "one- or zero-based",
                           "neither")
        base = 1 if one_based else 0
        # P146 Amendment A: above a capped supremum (the balance limit binds, no class constraint) the 1.01 point is
        # not a composition; the command lists it as skipped, and the kept interval must say capped
        capped_ranks = set()
        skipped = pick(result, "verification_skipped")
        for r in skipped if isinstance(skipped, list) else []:  # absent: nothing skipped (Amendment B)
            if pick(r, "quantity") != quantity or pick(r, "point") != "1.01_supremum":
                continue
            sample, rank = int(pick(r, "sample")), int(pick(r, "rank")) + (1 - base)
            kept = a_kept_interval(load_json(keep_path(keep, sample)), quantity)
            checks._record(kept is not MISSING and kept["status"] in ("capped", "composition_bound"),
                           f"{quantity} sample {sample} 1.01 point skipped only when capped", "capped",
                           None if kept is MISSING else kept["status"])
            checks._record(rank in wanted_ranks, f"{quantity} skipped rank {rank} is a required rank", wanted_ranks, rank)
            capped_ranks.add(rank)
        for point, factor in (("0.99_supremum", 0.99), ("1.01_supremum", 1.01)):
            by_rank = defaultdict(list)
            for r in rows_by_point[point]:
                by_rank[pick(r, "rank")].append(r)
            required = [r for r in wanted_ranks if point == "0.99_supremum" or r not in capped_ranks]
            checks.eq(f"{quantity} {point} ranks covered", sorted(required),
                      sorted(int(rank) + (1 - base) for rank in by_rank if isinstance(rank, int)))
            for rank, group in by_rank.items():
                checks._record(len(group) == 1, f"{quantity} {point} one row per rank", 1, len(group))
            for r in rows_by_point[point]:
                sample, value = int(pick(r, "sample")), pick(r, "value")
                label = f"{quantity} sample {sample} {point}"
                checks.rel(f"{label} value", factor * by_sample[sample], value)
                classes = pick(r, "classes")
                if point == "0.99_supremum":
                    checks._record(classes_inside(classes, model.target_class), f"{label} inside", "inside", classes)
                    checks.eq(f"{label} inside flag", True, pick(r, "inside"))
                else:
                    checks._record(classes_outside(classes, model.target_class), f"{label} outside, in a later class",
                                   "later class", classes)
                    checks.eq(f"{label} inside flag", False, pick(r, "inside"))
                guess = predicted(sample, quantity, value) if isinstance(value, (int, float)) else None
                if guess is not None:
                    checks.eq(f"{label} classes equal the linear prediction", guess, classes)
        # every sample at rank k or above is classified inside at 0.99 times the reported budget
        budget_rows = rows_by_point["0.99_budget"]
        checks.eq(f"{quantity} 0.99_budget row count", n - k + 1, len(budget_rows))
        above = {i for i, v in enumerate(by_sample) if v is not None and v > budget}
        atleast = {i for i, v in enumerate(by_sample) if v is not None and v >= budget}
        got = {int(pick(r, "sample")) for r in budget_rows}
        checks._record(above <= got <= atleast, f"{quantity} 0.99_budget samples are exactly those at rank k or above",
                       "above <= rows <= at least", {"missing": sorted(above - got)[:5], "extra": sorted(got - atleast)[:5]})
        for r in budget_rows:
            sample = int(pick(r, "sample"))
            label = f"{quantity} sample {sample} 0.99_budget"
            checks.rel(f"{label} value", 0.99 * budget, pick(r, "value"))
            classes = pick(r, "classes")
            checks._record(classes_inside(classes, model.target_class), f"{label} inside", "inside", classes)
            checks.eq(f"{label} inside flag", True, pick(r, "inside"))
            guess = predicted(sample, quantity, pick(r, "value")) if isinstance(pick(r, "value"), (int, float)) else None
            if guess is not None:
                checks.eq(f"{label} classes equal the linear prediction", guess, classes)
        details["quantities"][quantity] = {"ranks": wanted_ranks, "budget_rows": len(budget_rows), "rank_base": base,
                                           "rows": len(mine)}
    details.update({"linear_predictions": predictions, "unpredictable_rows": unpredictable})
    return checks, details


# --- B1 -----------------------------------------------------------------------------------------------------

def full_material_problem(problem: dict, budget: dict) -> dict:
    """The base spec with its material replaced by the full budget material at specification, which is what the
    sampling plan of the budget is built from."""
    composition = full_composition(budget["balance"], dict(sorted(budget.get("matrix", {}).items())),
                                   dict(sorted(budget["impurities"].items())))
    out = copy.deepcopy(problem)
    out["material"] = {"mass_g": problem["material"]["mass_g"], "basis": "wt_percent", "composition": composition}
    return out


def build_study(problem: dict, budget: dict, spec: dict, study_id: str) -> dict:
    """The study.robustness twin of a budget uncertainty spec's sampling block: the full material at specification,
    the spectrum of the staged base spec (spec_ref) and a schedule that carries every base-spec step."""
    return p145.build_study(full_material_problem(problem, budget), spec, study_id)


def expected_channels(sampling: dict) -> dict:
    channels = sampling.get("channels", {})
    return {"rate_scale": bool(channels.get("cross_section_mf33")), "decay_scale": bool(channels.get("decay_constants")),
            "yield_scale": bool(channels.get("fission_yields")), "flux_factor": bool(channels.get("flux_rel_std"))}


def compare_factors(keep: Path, cases: Path, n: int, elements: list, problem: dict, sampling: dict) -> dict:
    """B1: every sample's factor block is identical across its element solves and equals the study sample's."""
    nominal_spectrum = problem["spectrum"]
    mismatches, summary = [], Counter()
    for index in range(n):
        path, rob_path = keep_path(keep, index), cases / f"rob_{index}.json"
        if not path.exists():
            mismatches.append({"sample": index, "what": "no kept file"})
            continue
        if not rob_path.exists():
            mismatches.append({"sample": index, "what": "no study rob file"})
            continue
        doc, rob = load_json(path), load_json(rob_path)
        block = a_kept_block(pick(doc, "factors"))
        theirs = a_study_block(rob, nominal_spectrum)
        for name in ("rate_scale", "decay_scale", "yield_scale"):
            if block[name] != theirs[name]:
                mismatches.append({"sample": index, "what": f"{name} differs from the study's",
                                   "kept_entries": len(block[name]), "study_entries": len(theirs[name])})
        if not close(block["flux_factor"], theirs["flux_factor"]):
            mismatches.append({"sample": index, "what": "flux factor differs from the study's",
                               "kept": block["flux_factor"], "study": theirs["flux_factor"]})
        items = pick(doc, "elements")
        items = items if isinstance(items, dict) else {}
        for element in elements:
            item = items.get(element)
            if item is None:
                mismatches.append({"sample": index, "what": f"element {element} missing"})
                continue
            applied = pick(item, "factors")
            if applied is MISSING or applied is None:
                if item.get("error"):
                    summary["errored_elements_without_factors"] += 1
                    continue
                mismatches.append({"sample": index, "what": f"element {element} has no factors"})
                continue
            if a_kept_block(applied) != block:
                mismatches.append({"sample": index, "what": f"element {element} factors differ from the sample's"})
            else:
                summary["element_blocks_identical"] += 1
        summary["rate_scale_nonempty"] += bool(block["rate_scale"])
        summary["decay_scale_nonempty"] += bool(block["decay_scale"])
        summary["yield_scale_nonempty"] += bool(block["yield_scale"])
        summary["flux_factor_not_one"] += block["flux_factor"] != 1.0
        summary["samples_compared"] += 1
    wanted = expected_channels(sampling)
    nontrivial = {name: (not enabled) or summary[f"{name}_nonempty" if name != "flux_factor" else "flux_factor_not_one"] > 0
                  for name, enabled in wanted.items()}
    return {"mismatches": mismatches[:40], "mismatch_count": len(mismatches), "counts": dict(summary),
            "channels_enabled": wanted, "channel_exercised": nontrivial}


def b1(case: str) -> int:
    key = case_key(case)
    path = case_path(key)
    before = candidate()
    stage = fresh_stage(f"b1_{key.lower()}")
    staged = stage_case(path, stage, keep=True)
    spec, budget, problem = staged["spec"], staged["budget"], staged["problem"]
    n = spec["sampling"]["samples"]
    study_id = study_id_for(key)
    dump(stage / "study.json", build_study(problem, budget, spec, study_id))
    unc = run_budget_uncertainty(stage)
    stu = run_study(stage) if unc["rc"] == 0 else None
    report = {"schema": "actinv-p146-b1-1", "case": key, "candidate": before, "spec": path.name,
              "spec_sha256": staged["source_spec_sha256"], "budget_sha256": staged["source_budget_sha256"],
              "source_problem_sha256": staged["source_problem_sha256"], "staged_spec_sha256": staged["spec_sha256"],
              "staged_budget_sha256": staged["budget_sha256"], "staged_problem_sha256": staged["problem_sha256"],
              "properties_entries": staged.get("properties_entries"), "properties_sha256": staged.get("properties_sha256"),
              "study_sha256": sha(stage / "study.json"), "budget_uncertainty": brief(unc),
              "study": brief(stu) if stu else None, "samples": n}
    ok = False
    if stu is not None and stu["rc"] == 0:
        elements = sorted({budget["balance"]} | set(budget.get("matrix", {})) | set(budget["impurities"]))
        outcome = compare_factors(stage / "keep", stage / "study_out/cases" / p145.CASE_ID, n, elements, problem,
                                  spec["sampling"])
        files = sorted(p.name for p in (stage / "keep").glob("*"))
        report.update({"comparison": outcome, "keep_files": len(files),
                       "keep_files_expected": [keep_path(Path("."), i).name for i in range(n)] == files,
                       "result_sha256": sha(stage / "result.json")})
        ok = (outcome["mismatch_count"] == 0 and all(outcome["channel_exercised"].values())
              and outcome["counts"].get("samples_compared", 0) == n and report["keep_files_expected"])
    report["pass"] = ok and before["tree_clean"]
    comparison = report.get("comparison", {})
    report["summary"] = {"case": key, "samples": n, "mismatches": comparison.get("mismatch_count"),
                         "channel_exercised": comparison.get("channel_exercised")}
    write_meta(stage, before, staged, unc["rc"], True)
    return finish(f"b1_{key.lower()}", report)


# --- B2 and B3 ----------------------------------------------------------------------------------------------

def b2(case: str) -> int:
    key = case_key(case)
    path = case_path(key)
    before = candidate()
    stage, staged, ran = stage_for(key, before, "b2", keep=True)
    spec, budget = staged["spec"], staged["budget"]
    report = {"schema": "actinv-p146-b2-1", "case": key, "candidate": before, "spec": path.name,
              "spec_sha256": sha(path), "rule_pack": str(p145.RULES_FILE.relative_to(ROOT)),
              "rule_pack_sha256": sha(p145.RULES_FILE), "reused_b1_run": staged["reused_b1_run"],
              "command": ran, "assumptions": {"rel_tol": REL_TOL, "endpoint_tol": ENDPOINT_TOL}}
    ok = False
    if ran is None or ran["rc"] == 0:
        nominal_ref = None
        if not (stage / "nominal_ref.json").exists():
            report["nominal_command"] = brief(run_nominal(stage))
        if (stage / "nominal_ref.json").exists():
            nominal_ref = load_json(stage / "nominal_ref.json")
        result = load_json(stage / "result.json")
        model = Model(budget, stage)
        checks, details = b2_compare(model, spec, result, stage / spec["sampling"]["keep_samples"], nominal_ref)
        outcome = checks.report()
        report.update({"result_sha256": sha(stage / "result.json"), "check_report": outcome, "details": details,
                       "nominal_reference_present": nominal_ref is not None})
        ok = (outcome["checks"] > 0 and outcome["failed"] == 0 and not outcome["missing_fields"]
              and nominal_ref is not None)
    report["pass"] = ok and before["tree_clean"]
    report["summary"] = {"case": key, **{k: report.get("check_report", {}).get(k) for k in ("checks", "failed")},
                         "missing_fields": len(report.get("check_report", {}).get("missing_fields", []))}
    return finish(f"b2_{key.lower()}", report)


def b3(case: str) -> int:
    key = case_key(case)
    path = case_path(key)
    before = candidate()
    stage, staged, ran = stage_for(key, before, "b3", keep=True)
    spec, budget = staged["spec"], staged["budget"]
    report = {"schema": "actinv-p146-b3-1", "case": key, "candidate": before, "spec": path.name,
              "spec_sha256": sha(path), "reused_b1_run": staged["reused_b1_run"], "command": ran}
    ok = False
    if ran is None or ran["rc"] == 0:
        result = load_json(stage / "result.json")
        model = Model(budget, stage)
        checks, details = b3_check(model, spec, result, stage / spec["sampling"]["keep_samples"])
        outcome = checks.report()
        report.update({"result_sha256": sha(stage / "result.json"), "check_report": outcome, "details": details})
        ok = (outcome["checks"] > 0 and outcome["failed"] == 0 and not outcome["missing_fields"]
              and details.get("rows", 0) > 0)
    report["pass"] = ok and before["tree_clean"]
    report["summary"] = {"case": key, **{k: report.get("check_report", {}).get(k) for k in ("checks", "failed")},
                         "rows": report.get("details", {}).get("rows")}
    return finish(f"b3_{key.lower()}", report)


# --- B4 -----------------------------------------------------------------------------------------------------

def b4() -> int:
    before = candidate()
    meta = load_json(INPUTS / "d2_cases.json")
    band = meta["band"]
    impurity = meta["impurity"]
    report = {"schema": "actinv-p146-b4-1", "candidate": before, "closed_form": meta["closed_form"],
              "coverage_distribution": meta["coverage_distribution"],
              "interval": f"the keep probability of the closed form at the reported budget must lie inside the central "
                          f"{band * 100:g} % of Beta(n - k + 1, k), by bisection on the regularized incomplete beta",
              "cases": []}
    ok = True
    for entry in meta["cases"]:
        key = f"D2_{entry['id'].upper()}"
        stage, staged, ran = stage_for(key, before, "b4", keep=False)
        row = {"id": entry["id"], "spec_sha256": staged["source_spec_sha256"], "command": ran,
               "reused_b1_run": staged["reused_b1_run"]}
        case_ok = False
        if ran is None or ran["rc"] == 0:
            result = load_json(stage / "result.json")
            node = a_quantity(a_quantities(result).get(impurity))
            order = a_order(result)
            nominal = a_nominal(result)
            nominal_value = a_nominal_interval(nominal, impurity)["upper"] if nominal is not MISSING else MISSING
            x0, budget = node["nominal_supremum"], node["budget"]
            n, k = order["n"], order["k"]
            row.update({"result_sha256": sha(stage / "result.json"), "n": n, "k": k, "x0": x0, "budget": budget,
                        "status": node["status"], "nominal_supremum_in_nominal_block": nominal_value})
            if (node["status"] == "established" and isinstance(budget, float) and budget > 0.0 and isinstance(x0, float)
                    and isinstance(n, int) and isinstance(k, int) and x0 > 0.0):
                sigma2 = p145.covariance_sigma2(stage / entry["covariance"])  # the generated, SHA-verified copy
                p = keep_probability(x0, budget, sigma2)
                low, high = coverage_band(n, k, band)
                by_sample = [v for v in node["by_sample"] if v is not None and v > 0.0]
                implied = [math.log(x0 / v) for v in by_sample]  # ln F, mean -sigma2/2, sd sigma
                mean = sum(implied) / len(implied) if implied else math.nan
                se = math.sqrt(sigma2 / len(implied)) if implied else math.nan
                row.update({"sigma2": sigma2, "sigma2_matches_cases_file": sigma2 == entry["sigma2"],
                            "keep_probability": p, "band": [low, high], "band_level": band,
                            "keep_probability_inside_band": low <= p <= high,
                            "nominal_supremum_matches_nominal_block": close(x0, nominal_value)
                            if nominal_value is not MISSING else None,
                            "implied_ln_f_mean": mean, "expected_ln_f_mean": -sigma2 / 2.0,
                            "implied_ln_f_mean_standard_errors": (mean + sigma2 / 2.0) / se if implied else None})
                case_ok = row["keep_probability_inside_band"] and row["sigma2_matches_cases_file"] is True
        row["pass"] = case_ok
        ok &= case_ok
        report["cases"].append(row)
    report["pass"] = ok and before["tree_clean"]
    report["summary"] = {"cases": len(report["cases"]), "inside": [r.get("keep_probability_inside_band")
                                                                    for r in report["cases"]]}
    return finish("b4", report)


# --- B5 -----------------------------------------------------------------------------------------------------

def edit_base(spec, budget, problem):
    spec["sampling"]["samples"] = 100


def edit_nominal_power_step(spec, budget, problem):
    edit_base(spec, budget, problem)
    problem["schedule"].append({"dt": "1 d", "power_W_cm3": 1.0})


def edit_nominal_balance_in_matrix(spec, budget, problem):
    edit_base(spec, budget, problem)
    budget["matrix"] = {budget["balance"]: 1.0}


def edit_nominal_missing_properties(spec, budget, problem):
    edit_base(spec, budget, problem)
    budget["nuclide_properties"].pop("Co60", None)


def edit_composition_channel(spec, budget, problem):
    edit_base(spec, budget, problem)
    spec["sampling"]["channels"]["composition_rel_std"] = {"Co": 0.1}


def edit_no_channel(spec, budget, problem):
    edit_base(spec, budget, problem)
    spec["sampling"]["channels"] = {}


def edit_few_samples(spec, budget, problem):
    spec["sampling"]["samples"] = 19


def edit_confidence(spec, budget, problem):
    edit_base(spec, budget, problem)
    spec["confidence_level"] = 1.0


def edit_too_small_for_level(spec, budget, problem):
    spec["sampling"]["samples"] = 71


def edit_schema(spec, budget, problem):
    edit_base(spec, budget, problem)
    spec["schema"] = "actinv-waste-budget-uncertainty-spec-0"


def edit_zero_variance(spec, budget, problem):
    spec["sampling"]["samples"] = 100
    spec["sampling"]["channels"] = {"flux_rel_std": ZERO_VARIANCE_FLUX_REL_STD}
    spec["sampling"].pop("covariance", None)


ZERO_VARIANCE_FLUX_REL_STD = 1e-14
REFUSALS = {
    "nominal_power_driven_step": (edit_nominal_power_step, ("power",)),
    "nominal_balance_in_matrix": (edit_nominal_balance_in_matrix, ("balance",)),
    "nominal_incomplete_properties": (edit_nominal_missing_properties, ("propert", "coverage")),
    "composition_channel": (edit_composition_channel, ("composition",)),
    "no_channel": (edit_no_channel, ("channel",)),
    "fewer_than_20_samples": (edit_few_samples, ("20", "samples")),
    "confidence_out_of_range": (edit_confidence, ("confidence",)),
    "too_few_samples_for_level": (edit_too_small_for_level, ("72", "establish")),
    "wrong_schema": (edit_schema, ("schema",)),
}


def b5() -> int:
    before = candidate()
    base_spec = INPUTS / "d2_v36.json"
    report = {"schema": "actinv-p146-b5-1", "candidate": before, "base_spec": base_spec.name,
              "base_spec_sha256": sha(base_spec)}
    control_stage = fresh_stage("b5_control")
    stage_case(base_spec, control_stage, edit=edit_base)
    control = run_budget_uncertainty(control_stage)
    report["control_accepted"] = {"command": brief(control), "accepted": control["rc"] == 0}
    refusals, messages = {}, {}
    for name, (edit, words) in REFUSALS.items():
        stage = fresh_stage(f"b5_{name}")
        stage_case(base_spec, stage, keep=True, edit=edit)
        files_before = p145.listing(stage)
        child = run_budget_uncertainty(stage)
        message = child["stderr"].replace(str(stage), "<stage>").strip()
        messages[name] = message
        refusals[name] = {"command": brief(child), "exit_nonzero": child["rc"] not in (0, None),
                          "exit_is_error_not_panic": child["rc"] in (1, 2),
                          "no_output_written": not (stage / "result.json").exists(),
                          "no_samples_kept": not any((stage / "keep").glob("*")),
                          "nothing_else_created": p145.listing(stage) == files_before,
                          "message_present": bool(message) and "panicked" not in message,
                          "message_mentions_topic_hint": any(w in message.lower() for w in words),
                          "message": message[:400]}
    distinct = len(set(messages.values())) == len(messages) and all(messages.values())
    report["refusals"] = refusals
    report["messages_pairwise_distinct"] = distinct
    refusal_ok = distinct and all(all(v for k, v in r.items() if isinstance(v, bool) and k != "message_mentions_topic_hint")
                                  for r in refusals.values())
    # zero variance: the flux channel at a relative standard deviation of 1e-14 leaves every sample at the nominal
    # response to 1e-13, so the budget is the nominal supremum within 1e-12 relative
    stage = fresh_stage("b5_zero_variance")
    staged = stage_case(base_spec, stage, edit=edit_zero_variance)
    child = run_budget_uncertainty(stage)
    zero = {"command": brief(child), "flux_rel_std": ZERO_VARIANCE_FLUX_REL_STD, "checks": []}
    zero_ok = False
    if child["rc"] == 0:
        result = load_json(stage / "result.json")
        model_quantities = [*staged["budget"]["impurities"], JOINT]
        zero_ok = True
        for q in model_quantities:
            node = a_quantity(a_quantities(result).get(q))
            budget, nominal_sup = node["budget"], node["nominal_supremum"]
            ok_q = (node["status"] == "established" and isinstance(budget, float) and isinstance(nominal_sup, float)
                    and close(budget, nominal_sup, REL_TOL))
            spread = [v for v in (node["by_sample"] if isinstance(node["by_sample"], list) else []) if v is not None]
            all_near = bool(spread) and all(close(v, nominal_sup, REL_TOL) for v in spread)
            zero["checks"].append({"quantity": q, "status": node["status"], "budget": budget,
                                   "nominal_supremum": nominal_sup, "budget_equals_nominal": ok_q,
                                   "every_sample_near_nominal": all_near})
            zero_ok &= ok_q and all_near
    zero["pass"] = zero_ok
    report["zero_variance"] = zero
    # repeat: the same spec twice gives byte-identical result files
    repeat_stage = fresh_stage("b5_repeat")
    stage_case(base_spec, repeat_stage, edit=edit_base)
    first = run_budget_uncertainty(repeat_stage, "spec.json", "result_a.json")
    second = run_budget_uncertainty(repeat_stage, "spec.json", "result_b.json")
    repeat = {"first": brief(first), "second": brief(second), "identical": False}
    if first["rc"] == 0 and second["rc"] == 0:
        a, b = (repeat_stage / "result_a.json").read_bytes(), (repeat_stage / "result_b.json").read_bytes()
        repeat.update({"identical": a == b, "sha256": sha_bytes(a), "bytes": len(a)})
    report["repeat"] = repeat
    report["pass"] = control["rc"] == 0 and refusal_ok and zero_ok and repeat["identical"] and before["tree_clean"]
    report["summary"] = {"control_accepted": control["rc"] == 0, "refusals_ok": refusal_ok,
                         "distinct_messages": distinct, "zero_variance": zero_ok, "repeat_identical": repeat["identical"]}
    return finish("b5", report)


# --- B6 (local half) and the verdict ---------------------------------------------------------------------------

B6_REQUIRED = ["build_cli", "b6_study_regression", "b6_p105_children", "b6_p105_check", "b6_p105_verdict", "b6_fmt",
               "b6_clippy_native", "b6_clippy_wasm", "b6_workspace_tests", "b6_binding_fmt", "b6_binding_check",
               "b6_binding_clippy", "b6_module_build", "b6_module_install", "b6_module_tests", "b6_handbook",
               "b6_p13_data_distribution"]
TEST_RESULT = re.compile(r"test result: (ok|FAILED)\. (\d+) passed; (\d+) failed")


def read_steps(path: Path) -> dict:
    steps = {}
    for line in path.read_text().splitlines():
        if line.strip():
            name, argv, code = line.split("\t", 2)[0], line.split("\t")[1], line.split("\t")[-1]
            steps[name] = {"argv": argv, "exit": int(code)}
    return steps


def count_tests(log: Path) -> tuple[int, int]:
    passed = failed = 0
    if log.exists():
        for match in TEST_RESULT.finditer(log.read_text(errors="replace")):
            passed += int(match.group(2))
            failed += int(match.group(3))
    return passed, failed


def b6_collect(steps_path: str | None = None) -> int:
    path = Path(steps_path) if steps_path else OUT / "steps.tsv"
    steps = read_steps(path)
    logs = path.parent / "logs"
    passed, failed = count_tests(logs / "b6_workspace_tests.log")
    regression_path = OUT / "b6_study_regression.json"
    regression = {"present": regression_path.exists()}
    regression_ok = False
    if regression_path.exists():
        record = load_json(regression_path)
        gates = record.get("gates", {})
        regression.update({name: bool(gates.get(name, {}).get("pass")) for name in ("G2", "G3", "G4")})
        regression.update({"G2_differences": len(gates.get("G2", {}).get("differences", [])),
                           "reference_binary_sha256": record.get("reference_sha256"),
                           "candidate_binary_sha256": record.get("candidate_sha256"), "verdict": record.get("verdict")})
        regression_ok = (record.get("verdict") == "PASS" and all(regression[g] for g in ("G2", "G3", "G4"))
                         and record.get("reference_sha256") == REFERENCE_BINARY_SHA256
                         and BINARY.is_file() and record.get("candidate_sha256") == sha(BINARY))
    missing = [name for name in B6_REQUIRED if name not in steps]
    bad = [name for name in B6_REQUIRED if name in steps and steps[name]["exit"] != 0]
    other = {name: s["exit"] for name, s in steps.items() if name not in B6_REQUIRED}
    clean = tree_clean()
    record = {"schema": "actinv-p146-b6-1", "commit": git("rev-parse", "HEAD"),
              "note": "local half of B6; the verdict also requires CI green on the pushed evidence commit",
              "steps": {name: s for name, s in steps.items() if name in B6_REQUIRED}, "gate_step_exits": other,
              "missing_steps": missing, "failed_steps": bad, "tests_passed": passed, "tests_failed": failed,
              "study_regression": regression, "tree_clean_tracked": clean, "protocol_sha256": sha(PROTOCOL),
              "binary_sha256": sha(BINARY) if BINARY.is_file() else None,
              "pass": (not missing and not bad and failed == 0 and passed > 0 and regression_ok and clean
                       and all(code == 0 for code in other.values()))}
    dump(OUT / "b6.json", record)
    print(json.dumps({"gate": "b6", "pass": record["pass"], "missing": missing, "failed": bad, "tests_passed": passed,
                      "tests_failed": failed, "regression": regression_ok}, sort_keys=True))
    return 0 if record["pass"] else 1


def gate_names() -> list:
    names = []
    for gate in ("b1", "b2", "b3"):
        names += [f"{gate}_{key.lower()}" for key in CASES]
    return names + ["b4", "b5"]


def verdict(ci_path: str) -> int:
    names = gate_names()
    records = {n: load_json(OUT / f"{n}.json") for n in names}
    b6 = load_json(OUT / "b6.json")
    ci = load_json(Path(ci_path))
    runs = ci.get("runs", [])
    ci_green = bool(runs) and all(r.get("conclusion") == "success" for r in runs) and any(
        r.get("name") == "controls" for r in runs)

    def ok(*keys):
        return all(records[k].get("pass") is True for k in keys)

    gates = {"B1": ok(*[f"b1_{k.lower()}" for k in CASES]), "B2": ok(*[f"b2_{k.lower()}" for k in CASES]),
             "B3": ok(*[f"b3_{k.lower()}" for k in CASES]), "B4": ok("b4"), "B5": ok("b5"),
             "B6": b6.get("pass") is True and ci_green}
    evidence = [records[n]["candidate"] for n in names]
    identity = {(c["commit"], c["binary_sha256"], c["protocol_sha256"], json.dumps(c["amendments_sha256"], sort_keys=True))
                for c in evidence}
    consistent = (len(identity) == 1 and evidence[0]["protocol_sha256"] == sha(PROTOCOL)
                  and b6.get("commit") == evidence[0]["commit"] and b6.get("binary_sha256") == evidence[0]["binary_sha256"])
    record = {"schema": "actinv-p146-verdict-1", "protocol_sha256": sha(PROTOCOL), "amendments_sha256": amendments(),
              "gate_file_sha256": {n: sha(OUT / f"{n}.json") for n in [*names, "b6"]}, "gates": gates,
              "evidence_candidates_consistent": consistent, "candidate": evidence[0], "ci": ci,
              "verdict": "P146-PASS" if all(gates.values()) and consistent else "P146-FAIL"}
    dump(OUT.parent / "p146_verdict.json", record)
    print(json.dumps({"verdict": record["verdict"], "gates": gates, "consistent": consistent}))
    return 0 if record["verdict"] == "P146-PASS" else 1


def stage_command(case: str, directory: str) -> int:
    """Stage a case with the properties expanded, for a development run of `actinv waste budget budget.json`."""
    key = case_key(case)
    stage = fresh_stage_at(Path(directory).resolve())
    staged = stage_case(case_path(key), stage, keep=True)
    print(json.dumps({"stage": str(stage), "properties_entries": staged.get("properties_entries"),
                      "properties_sha256": staged.get("properties_sha256"),
                      "run": f"cd {stage} && actinv waste budget budget.json nominal.json"}))
    return 0


# --- self test (no data, no binary) ----------------------------------------------------------------------------

def synthetic_budget(rules, density: float = 7.9, mass: float = 1000.0) -> tuple[dict, dict]:
    """A small budget document (Fe balance, Ni matrix, Co and Nb impurities, two targets) and per-g activities of the
    pure elements chosen so that, at 100 wt%, the Table 2 Column 1 sums are Fe 0.4, Ni 2.0, Co 400 and the Table 1
    normalized sum of Nb is 200 at step 1 and half of that at step 2."""
    volume = mass / density
    per_cm3 = rules.ci_bq / 1.0e6  # Bq per cm3 for 1 Ci/m3

    def per_g(fraction, limit):
        return fraction * limit * per_cm3 * volume / mass

    elements = {"Fe": {"Fe55": per_g(0.4, 700.0)}, "Ni": {"Ni63": per_g(2.0, 35.0)},
                "Co": {"Co60": per_g(400.0, 700.0)}, "Nb": {"Nb94": per_g(20.0, 0.2)}}
    properties = {"Fe55": {"z": 26, "half_life_s": 8.6e7, "alpha_emitting": False},
                  "Ni63": {"z": 28, "half_life_s": 3.16e9, "alpha_emitting": False},
                  "Co60": {"z": 27, "half_life_s": 1.6634e8, "alpha_emitting": False},
                  "Nb94": {"z": 41, "half_life_s": 6.4e11, "alpha_emitting": False},
                  "H3": {"z": 1, "half_life_s": 3.887e8, "alpha_emitting": False}}
    doc = {"schema": BUDGET_SCHEMA, "base_spec": "problem.json", "balance": "Fe", "matrix": {"Ni": 8.0},
           "impurities": {"Co": 0.05, "Nb": 0.01}, "targets": [1, 2], "rules": p145.BUNDLED_RULES_ID,
           "target_class": "A", "mass_g": mass, "density_g_cm3": density, "waste_type": "activated_metal",
           "nuclide_properties": properties, "external_tritium": {"status": "not_applicable"}}
    return doc, elements


def synthetic_steps(per_g: dict, scale: float) -> dict:
    return {"1": {"t_s": 1.0, "activity_bq_per_g": {k: v * scale for k, v in per_g.items()}},
            "2": {"t_s": 2.0, "activity_bq_per_g": {k: v * scale * 0.5 for k, v in per_g.items()}}}


def synthetic_nominal(model: Model, basis: dict, sources: dict) -> dict:
    """A nominal budget result in the field names the adapters read, from the model."""
    intervals = model.intervals(basis)

    def node(interval, joint):
        lower_key, upper_key = ("lower_scale_factor", "upper_supremum_scale_factor") if joint else (
            "lower_wt_pct", "upper_supremum_wt_pct")
        binding = interval.upper.binding
        return {"status": interval.status, "feasible": interval.feasible, "no_response": interval.no_response,
                lower_key: interval.lower.value, upper_key: interval.upper.value,
                "binding_constraint": None if binding is None else {"step": binding[0], "table": binding[1],
                                                                    "column": binding[2]}}

    return {"schema": "actinv-waste-budget-result-1",
            "basis_solves": [{"element": el, "steps": {str(step): acts for step, acts in basis[el].acts.items()}}
                             for el in model.elements],
            "impurities": {q: {"others_at_spec": node(intervals[q], False)} for q in model.impurities},
            "joint_specification_margin": node(intervals[JOINT], True)}


def reference_result(model: Model, spec: dict, keep: Path, nominal: dict) -> dict:
    """The result document of the contract, built from the model and the kept samples. Used only by the self test, and
    as the reference for the schema this checker assumes."""
    n = spec["sampling"]["samples"]
    level = float(spec.get("confidence_level", 0.95))
    k = order_index(n, level)
    quantities = model.quantities()
    outcomes = {q: [] for q in quantities}
    analyses, failed = {}, []
    for index in range(n):
        analysis = analyse_sample(model, load_json(keep_path(keep, index)))
        analyses[index] = analysis
        if analysis["failed"]:
            failed.append({"sample": index, "element": analysis["failed"][0][0], "error": str(analysis["failed"][0][1])})
        for q in quantities:
            outcomes[q].append(outcome_of(None if analysis["intervals"] is None else analysis["intervals"][q]))
    report, ranked = {}, {}
    for q in quantities:
        nominal_view_q = a_nominal_interval(nominal, q)
        expected = aggregate_quantity(outcomes[q], k, nominal_view_q, q == JOINT)
        ranked[q] = expected
        report[q] = {"unit": expected["unit"], "status": expected["status"], "reason": expected["reason"],
                     "positive_lower_bound_samples": expected["positive"], "nominal_supremum": expected["nominal_supremum"],
                     "budget": expected["budget"], "suprema_by_sample": expected["by_sample"],
                     "suprema_sorted": expected["sorted"], "sample_status": expected["sample_status"],
                     "binding_tally": [{"step": s, "table": t, "column": c, "count": cnt}
                                       for (s, t, c), cnt in sorted(expected["tally"].items(), key=lambda x: key_order(x[0]))],
                     "keep_probability_statement": "x below budget keeps class <= A in at least gamma of data draws, "
                                                   "at confidence gamma"}
    rows = []
    for q in quantities:
        expected = ranked[q]
        if expected["status"] != "established":
            continue
        order = sorted(range(n), key=lambda i: (expected["by_sample"][i], i))
        for rank in range(max(1, k - 2), min(n, k + 2) + 1):
            sample = order[rank - 1]
            x = expected["by_sample"][sample]
            if not (x is not None and x > 0.0):
                continue
            for point, factor in (("0.99_supremum", 0.99), ("1.01_supremum", 1.01)):
                classes = model.classes_at(analyses[sample]["basis"], q, factor * x)
                rows.append({"quantity": q, "sample": sample, "rank": rank, "point": point, "value": factor * x,
                             "classes": classes, "inside": classes_inside(classes, model.target_class)})
        for rank in range(k, n + 1):
            sample = order[rank - 1]
            value = 0.99 * expected["budget"]
            classes = model.classes_at(analyses[sample]["basis"], q, value)
            rows.append({"quantity": q, "sample": sample, "rank": rank, "point": "0.99_budget", "value": value,
                         "classes": classes, "inside": classes_inside(classes, model.target_class)})
    return {"schema": RESULT_SCHEMA, "spec_sha256": "0" * 64, "rule_pack_sha256": sha(p145.RULES_FILE),
            "confidence_level": level, "samples": n, "seed": spec["sampling"]["seed"],
            "order_statistic": {"k": k, "n": n, "minimum_samples": p145.minimum_samples(level)}, "nominal": nominal,
            "quantities": report, "failed_samples": failed, "verification": rows, "verification_skipped": [],
            "verification_pass": all(r["inside"] == r["point"].startswith("0.99") for r in rows),
            "coverage": {"channels": ["flux_rel_std"], "statement": "conditional on the sampled channels"},
            "not_claimed": ["linearity in composition assumed per sample"]}


def selftest() -> int:
    failures = []

    def expect(ok, label):
        if not ok:
            failures.append(label)

    # the order statistic: bisection on the exact tail against a brute-force scan of the Clopper-Pearson lower ends
    for n in (72, 100):
        brute = [k for k in range(1, n + 1) if p145.cp_interval(n - k + 1, n, 0.95)[0] >= 0.95]
        expect(order_index(n, 0.95) == (max(brute) if brute else None), f"order index n={n}")
    for n, level in ((150, 0.95), (600, 0.99), (1024, 0.95)):
        found = order_index(n, level)  # the window around k is enough: the lower end falls with k (monotone)
        window = range(max(1, found - 2), min(n, found + 2) + 1)
        expect(all((p145.cp_interval(n - k + 1, n, level)[0] >= level) == (k <= found) for k in window),
               f"order index n={n} level={level}")
    expect(order_index(71, 0.95) is None and order_index(72, 0.95) == 1, "k = 1 exactly at the minimum sample count")
    expect(p145.minimum_samples(0.95) == 72 and p145.minimum_samples(0.99) == math.ceil(
        math.log(0.005) / math.log(0.99)), "minimum samples")
    expect(all(order_index(n, 0.95) <= order_index(n + 40, 0.95) for n in (80, 200, 500)), "k grows with n")
    # the Beta coverage band: round trip, binomial identity and a hand value
    a, b = 986.0, 39.0
    q = beta_quantile(a, b, 0.0005)
    expect(abs(p145.betai(a, b, q) - 0.0005) < 1e-9, "beta quantile round trip")
    expect(abs(p145.betai(a, b, 0.96) - float(p145.binom_tail_ge(986, 1024, Decimal("0.96")))) < 1e-9,
           "I_x(n-k+1, k) is the binomial tail")
    low, high = coverage_band(1024, 39)
    expect(0.0 < low < 0.95 < high < 1.0 and abs(p145.betai(986.0, 39.0, high) - 0.9995) < 1e-9, "coverage band")
    low1, high1 = coverage_band(100, 1)  # Beta(100, 1) has the cdf x^100
    expect(abs(low1 - 0.0005 ** 0.01) < 1e-9 and abs(high1 - 0.9995 ** 0.01) < 1e-9, "band for k = 1")
    # the B4 closed form with the plus sign: x = x0 exp(sigma2/2) is the median of the keep distribution
    for sigma2 in (0.16, 0.36, 0.64):
        x0 = 0.27
        expect(abs(keep_probability(x0, x0 * math.exp(sigma2 / 2.0), sigma2) - 0.5) < 1e-12, f"median at sigma2={sigma2}")
        expect(abs(keep_probability(x0, x0, sigma2) - phi(math.sqrt(sigma2) / 2.0)) < 1e-12, "value at x0")
        expect(keep_probability(x0, x0 / 2, sigma2) > keep_probability(x0, x0, sigma2), "decreasing in x")
    rng = random.Random(146)
    sigma2, x0, level = 0.36, 1.0, 0.8
    draws = [x0 / math.exp(math.sqrt(sigma2) * rng.gauss(0.0, 1.0) - sigma2 / 2.0) for _ in range(40000)]
    xs = sorted(draws)[len(draws) // 5]
    expect(abs(sum(1 for v in draws if v > xs) / len(draws) - keep_probability(x0, xs, sigma2)) < 0.01,
           "closed form against a Monte Carlo draw")
    # the interval algorithm against the Rust unit tests and a dense scan
    never = lambda key, edge: False
    key = (1, 1, None)
    iv = interval_for({key: 2.0}, {key: -1.0}, 3.0, never)
    expect(iv.feasible and iv.lower.value == 1.0 and iv.upper.value == 3.0, "negative gradient creates a lower bound")
    expect(iv.status == "composition_bound" and iv.lower.value > 0.0, "positive lower bound, upper at the cap")
    expect(not interval_for({key: 1.1}, {}, 3.0, never).feasible, "infeasible constant")
    expect(not interval_for({key: 1.0}, {}, 3.0, lambda k_, e: True).feasible, "strict boundary at zero is infeasible")
    singleton = interval_for({key: 1.0}, {key: 0.0}, 10.0, lambda k_, v: v > 0.0)
    expect(singleton.feasible and singleton.status == "singleton" and singleton.upper.value == 0.0
           and not singleton.upper.open, "constant threshold with positive strictness is a closed zero singleton")
    expect(interval_for({key: 0.25}, {key: 0.0}, 2.0, never).status == "no_response", "no response")
    capped = interval_for({key: 0.0}, {key: -0.1}, 2.0, never)
    expect(capped.feasible and capped.status == "composition_bound" and capped.upper.value == 2.0 and capped.upper.open
           and outcome_of(capped)["status"] == "capped", "balance cap")
    bound = interval_for({key: 0.5}, {key: 0.01}, 100.0, never)
    expect(bound.upper.value == 50.0 and bound.upper.binding == key and outcome_of(bound)["status"] == "bound"
           and outcome_of(bound)["binding"] == key, "upper bound and binding")
    edge_at_cap = (1.0 - 0.5) / 0.005
    exact = interval_for({key: 0.5}, {key: 0.005}, edge_at_cap, never)
    expect(exact.upper.value == edge_at_cap and exact.upper.binding == key and exact.status == "limit",
           "an edge equal to the cap binds without being a composition bound")
    beyond = interval_for({key: 0.5}, {key: 0.001}, 100.0, never)
    expect(beyond.upper.value == 100.0 and beyond.upper.binding is None and beyond.status == "composition_bound",
           "an edge beyond the cap leaves the composition bound")
    tie = interval_for({(1, 1, None): 0.5, (1, 2, 1): 0.5}, {(1, 1, None): 0.01, (1, 2, 1): 0.01}, 100.0,
                       lambda k_, e: k_ == (1, 2, 1))
    expect(tie.upper.value == 50.0 and tie.upper.open and tie.upper.binding == (1, 2, 1), "equal edges: the open one binds")
    expect(not interval_for({key: 0.5}, {key: float("nan")}, 10.0, never).feasible, "nonfinite boundary")
    scan = random.Random(1461)
    for trial in range(60):
        keys = [(1, 1, None), (1, 2, 1), (2, 1, None), (2, 2, 1)][:scan.randint(1, 4)]
        base = {k_: scan.uniform(0.0, 1.5) for k_ in keys}
        slope = {k_: scan.choice([-1.0, 1.0, 0.0]) * scan.uniform(0.001, 0.2) for k_ in keys}
        cap = scan.uniform(1.0, 100.0)
        result = interval_for(base, slope, cap, never)
        grid = [cap * i / 4000.0 for i in range(4000)]
        feasible_points = [x for x in grid if all(base[k_] + slope[k_] * x <= 1.0 + 1e-12 for k_ in keys)]
        if result.feasible and result.upper.value - result.lower.value > 3.0 * cap / 4000.0:
            inside = feasible_points
            expect(bool(inside) and abs(min(inside) - result.lower.value) <= cap / 3999.0 + 1e-9
                   and abs(max(inside) - min(result.upper.value, cap)) <= cap / 3999.0 + 1e-9,
                   f"dense scan trial {trial}: {result.lower.value} {result.upper.value} vs "
                   f"{(min(inside), max(inside)) if inside else None}")
        elif not result.feasible:
            expect(not feasible_points, f"dense scan trial {trial}: infeasible but points exist")
    # sequential sums and the balance rest
    expect(nsum([0.1] * 10) == 0.9999999999999999 and nsum([]) == 0.0, "sequential sum")
    expect(full_composition("Fe", {"Ni": 8.0, "Cr": 18.0}, {"Co": 0.05})["Fe"] == 100.0 - (0.05 + 18.0 + 8.0),
           "balance rest in symbol order")
    try:
        full_composition("Fe", {"Cr": 90.0}, {"Ni": 10.0})
        expect(False, "a non-positive balance is refused")
    except ValueError:
        pass
    # the model on a synthetic basis: the supremum puts the binding sum at one, 0.99 is inside and 1.01 outside
    rules = p145.Rules(p145.RULES_FILE)
    doc, sources = synthetic_budget(rules)
    model = Model(doc, ROOT)
    nominal_basis = {el: model.element_basis({1: sources[el], 2: {k_: v * 0.5 for k_, v in sources[el].items()}})
                     for el in model.elements}
    nominal_intervals = model.intervals(nominal_basis)
    co, nb, joint = nominal_intervals["Co"], nominal_intervals["Nb"], nominal_intervals[JOINT]
    base_co = 100.0 - 8.0 - 0.01
    expected_co = (1.0 - (base_co / 100.0 * 0.4 + 8.0 / 100.0 * 2.0)) / ((400.0 - 0.4) / 100.0)
    expect(co.feasible and abs(co.upper.value - expected_co) <= 1e-12 * expected_co and co.upper.binding == (1, 2, 1),
           f"Co supremum {co.upper.value} against {expected_co}")
    expect(nb.feasible and abs(nb.upper.value - 0.5) <= 1e-12 and nb.upper.binding == (1, 1, None), "Nb supremum")
    expect(joint.feasible and joint.status == "limit" and joint.upper.binding is not None, "joint scale factor")
    for quantity, interval in (("Co", co), ("Nb", nb), (JOINT, joint)):
        inside = model.classes_at(nominal_basis, quantity, 0.99 * interval.upper.value)
        outside = model.classes_at(nominal_basis, quantity, 1.01 * interval.upper.value)
        expect(classes_inside(inside, "A") and classes_outside(outside, "A"), f"{quantity} 0.99/1.01 classes {inside} {outside}")
    # a positive lower bound: an impurity with no activity in a balance that is over the limit by itself
    over = dict(sources)
    over["Fe"] = {"Fe55": sources["Fe"]["Fe55"] * 5.0}
    bad_basis = {el: model.element_basis({1: over[el], 2: {k_: v * 0.5 for k_, v in over[el].items()}})
                 for el in model.elements}
    lowered = model.intervals(bad_basis)
    expect(not lowered["Co"].feasible and not lowered[JOINT].feasible
           and outcome_of(lowered["Co"])["status"] == "infeasible", "a balance over the limit by itself is infeasible")
    zero_basis = dict(bad_basis)
    zero_basis["Nb"] = model.element_basis({1: {"Nb94": 0.0}, 2: {"Nb94": 0.0}})
    positive_lower = model.intervals(zero_basis)["Nb"]
    expect(positive_lower.feasible and positive_lower.lower.value > 0.0 and outcome_of(positive_lower)["positive_lower"],
           f"a response below the balance's gives a positive lower bound {positive_lower.lower.value}")
    # end to end with synthetic kept samples; every perturbation must be caught
    with tempfile.TemporaryDirectory(dir=_scratch()) as folder:
        base = Path(folder)
        (base / "keep").mkdir()
        gen = random.Random(146)
        n = 200
        spec = {"schema": SPEC_SCHEMA, "budget": "budget.json", "confidence_level": 0.95,
                "sampling": {"channels": {"flux_rel_std": 0.1}, "samples": n, "seed": 7, "keep_samples": "keep"}}
        for index in range(n):
            factors = {"Fe": math.exp(0.05 * gen.gauss(0, 1)), "Ni": math.exp(0.05 * gen.gauss(0, 1)),
                       "Co": math.exp(0.2 * gen.gauss(0, 1)), "Nb": math.exp(0.3 * gen.gauss(0, 1))}
            if index == 23:
                factors["Ni"] = 10.0  # the matrix alone is over the limit: infeasible for every quantity
            block = {"flux_factor": 1.0, "rate_scale": {}, "decay_scale": {}, "yield_scale": {}}
            elements = {}
            for element in model.elements:
                steps = synthetic_steps(sources[element], factors[element])
                built = model.element_basis(a_kept_steps({"steps": steps}))
                elements[element] = {"factors": block, "error": None, "steps": steps,
                                     "class_constraint_normalized_sums": {key_text(k_): v for k_, v in built.sums.items()}}
            if index == 17:
                elements["Nb"] = {"factors": block, "error": "synthetic solve failure", "steps": None,
                                  "class_constraint_normalized_sums": None}
            sample_basis = {el: model.element_basis(a_kept_steps({"steps": elements[el]["steps"]}))
                            for el in model.elements if elements[el]["error"] is None}
            intervals = {}
            if len(sample_basis) == len(model.elements):
                for q, interval in model.intervals(sample_basis).items():
                    outcome = outcome_of(interval)
                    intervals[q] = {"lower": outcome["lower"], "supremum": outcome["value"], "status": outcome["status"],
                                    "binding": None if outcome["binding"] is None else {
                                        "step": outcome["binding"][0], "table": outcome["binding"][1],
                                        "column": outcome["binding"][2]}}
            keep_path(base / "keep", index).write_text(json.dumps(
                {"sample": index, "factors": block, "elements": elements, "intervals": intervals}))
        nominal = synthetic_nominal(model, nominal_basis, sources)
        result = reference_result(model, spec, base / "keep", nominal)
        checks, details = b2_compare(model, spec, result, base / "keep", nominal)
        outcome = checks.report()
        expect(outcome["failed"] == 0 and outcome["checks"] > 1000 and not outcome["missing_fields"],
               f"reference result passes B2: {outcome['failed']} failed of {outcome['checks']} {outcome['failures'][:2]}")
        expect(details["failed_count"] == 1 and details["failed_samples"] == [17] and details["k"] == order_index(n, 0.95), f"failed samples {details}")
        checks3, details3 = b3_check(model, spec, result, base / "keep")
        outcome3 = checks3.report()
        expect(outcome3["failed"] == 0 and outcome3["checks"] > 100 and details3["linear_predictions"] > 100,
               f"reference result passes B3: {outcome3['failures'][:2]} {details3}")
        # Amendment B: a result without the verification_skipped member is read as nothing skipped
        absent = {key: value for key, value in result.items() if key != "verification_skipped"}
        outcome3b = b3_check(model, spec, absent, base / "keep")[0].report()
        expect(outcome3b["failed"] == 0 and outcome3b["checks"] == outcome3["checks"],
               f"B3 without verification_skipped: {outcome3b['failures'][:2]}")
        statuses = result["quantities"]["Co"]["sample_status"]
        expect(statuses["failed"] == 1 and statuses["infeasible"] == 1 and statuses["bound"] == n - 2,
               f"sample statuses {statuses}")
        expect(result["quantities"]["Co"]["budget"] > 0.0 and result["order_statistic"]["k"] >= 2,
               "the budget is a positive order statistic")

        def mutated(path, value):
            doc_ = copy.deepcopy(result)
            holder = doc_
            for part in path[:-1]:
                holder = holder[part]
            holder[path[-1]] = value
            return doc_

        co_node = result["quantities"]["Co"]
        first = next(i for i, v in enumerate(co_node["suprema_by_sample"]) if v and v > 0.0)
        mutations = {
            "by sample": mutated(["quantities", "Co", "suprema_by_sample", first], co_node["suprema_by_sample"][first] * (1 + 1e-9)),
            "sorted": mutated(["quantities", "Co", "suprema_sorted", 5], co_node["suprema_sorted"][5] * (1 + 1e-9)),
            "budget": mutated(["quantities", "Co", "budget"], co_node["budget"] * (1 + 1e-9)),
            "k": mutated(["order_statistic", "k"], result["order_statistic"]["k"] + 1),
            "minimum samples": mutated(["order_statistic", "minimum_samples"], 73),
            "tally": mutated(["quantities", "Nb", "binding_tally", 0, "count"], 1),
            "sample status": mutated(["quantities", "Nb", "sample_status", "bound"], n),
            "nominal supremum": mutated(["quantities", "Co", "nominal_supremum"], co_node["nominal_supremum"] * 1.001),
            "nominal block": mutated(["nominal", "impurities", "Co", "others_at_spec", "upper_supremum_wt_pct"], 1.0),
            "status": mutated(["quantities", "Nb", "status"], "not_established"),
            "failed samples": mutated(["failed_samples"], []),
            "samples": mutated(["samples"], n + 1),
            "schema": mutated(["schema"], "other"),
            "coverage": mutated(["coverage"], {}),
            "rule pack": mutated(["rule_pack_sha256"], "0" * 64),
            "joint budget": mutated(["quantities", JOINT, "budget"], 1.0),
        }
        for label, doc_ in mutations.items():
            expect(b2_compare(model, spec, doc_, base / "keep", nominal)[0].report()["failed"] > 0,
                   f"B2 mutation not caught: {label}")
        expect(b2_compare(model, spec, result, base / "keep", {"other": 1})[0].report()["failed"] > 0,
               "B2 mutation not caught: reference nominal")
        rows = result["verification"]
        flip = next(i for i, r in enumerate(rows) if r["point"] == "1.01_supremum")
        inside_row = next(i for i, r in enumerate(rows) if r["point"] == "0.99_supremum")
        budget_row = next(i for i, r in enumerate(rows) if r["point"] == "0.99_budget")
        verification = {
            "outside flag": mutated(["verification", flip, "inside"], True),
            "outside class": mutated(["verification", flip, "classes"], {"1": "A", "2": "A"}),
            "inside class": mutated(["verification", inside_row, "classes"], {"1": "B", "2": "B"}),
            "value": mutated(["verification", inside_row, "value"], rows[inside_row]["value"] * 1.001),
            "budget value": mutated(["verification", budget_row, "value"], rows[budget_row]["value"] * 1.01),
            "dropped row": mutated(["verification"], rows[:inside_row] + rows[inside_row + 1:]),
            "dropped budget row": mutated(["verification"], rows[:budget_row] + rows[budget_row + 1:]),
            "pass flag": mutated(["verification_pass"], False),
            "unknown point": mutated(["verification", flip, "point"], "1.02_supremum"),
            "rank": mutated(["verification", inside_row, "rank"], rows[inside_row]["rank"] + 3),
        }
        for label, doc_ in verification.items():
            expect(b3_check(model, spec, doc_, base / "keep")[0].report()["failed"] > 0,
                   f"B3 mutation not caught: {label}")
        broken = copy.deepcopy(result)
        del broken["quantities"]["Co"]["suprema_sorted"]
        expect(b2_compare(model, spec, broken, base / "keep", nominal)[0].report()["missing_fields"],
               "a renamed field is reported as missing")
        # the factor comparison of B1 against a synthetic study folder
        cases = base / "cases"
        cases.mkdir()
        nominal_spectrum = {"total": 1.0e12}
        sampling = {"channels": {"cross_section_mf33": True, "flux_rel_std": 0.05}}
        for index in range(3):
            block = {"flux_factor": 1.0 + 0.01 * index, "rate_scale": {"528": 0.9 + 0.01 * index},
                     "decay_scale": {}, "yield_scale": {}}
            item = {"factors": block, "error": None}
            keep_path(base / "keep", index).write_text(json.dumps(
                {"sample": index, "factors": block, "elements": {el: item for el in ("Fe", "Ni")}}))
            (cases / f"rob_{index}.json").write_text(json.dumps(
                {"options": {"rate_scale": block["rate_scale"]}, "spectrum": {"total": 1.0e12 * (1.0 + 0.01 * index)}}))
        good = compare_factors(base / "keep", cases, 3, ["Fe", "Ni"], {"spectrum": nominal_spectrum}, sampling)
        expect(good["mismatch_count"] == 0 and all(good["channel_exercised"].values())
               and good["counts"]["element_blocks_identical"] == 6, f"B1 identical factors {good}")
        (cases / "rob_1.json").write_text(json.dumps(
            {"options": {"rate_scale": {"528": 0.5}}, "spectrum": {"total": 1.0e12 * 1.01}}))
        expect(compare_factors(base / "keep", cases, 3, ["Fe", "Ni"], {"spectrum": nominal_spectrum}, sampling)["mismatch_count"] == 1,
               "B1 catches a different rate scale")
        doc1 = load_json(keep_path(base / "keep", 2))
        doc1["elements"]["Ni"] = {"factors": {**doc1["factors"], "flux_factor": 1.5}, "error": None}
        keep_path(base / "keep", 2).write_text(json.dumps(doc1))
        expect(compare_factors(base / "keep", cases, 3, ["Fe", "Ni"], {"spectrum": nominal_spectrum}, sampling)["mismatch_count"] == 2,
               "B1 catches a different element block")
        trivial = compare_factors(base / "keep", cases, 3, ["Fe"], {"spectrum": nominal_spectrum},
                                  {"channels": {"decay_constants": True}})
        expect(not all(trivial["channel_exercised"].values()), "B1 reports an enabled channel that never varies")
    # properties from decay records, and the study twin of a staged case
    records = [
        {"za": 27060.0, "liso": 0, "nst": 0, "half_life": 1.66e8, "modes": [{"br": 1.0, "rtyp": 1.0}]},
        {"za": 27060.0, "liso": 1, "nst": 0, "half_life": 628.0, "modes": [{"br": 1.0, "rtyp": 3.0}]},
        {"za": 26056.0, "liso": 0, "nst": 1, "half_life": 0.0, "modes": []},
        {"za": 95241.0, "liso": 0, "nst": 0, "half_life": 1.4e10, "modes": [{"br": 1.0, "rtyp": 4.0}]},
        {"za": 1.0, "liso": 0, "nst": 0, "half_life": 600.0, "modes": []},
        {"za": 27060.0, "liso": 0, "nst": 0, "half_life": 99.0, "modes": []},  # a later record of the same nuclide
    ]
    props = properties_from_records(records)
    expect(set(props) == {"Co60", "Co60m1", "Am241"} and props["Co60"]["half_life_s"] == 1.66e8
           and props["Am241"]["alpha_emitting"] and props["Am241"]["z"] == 95 and not props["Co60"]["alpha_emitting"],
           f"properties from records {props}")
    ok_rules = p145.Rules(p145.RULES_FILE)
    ev = p145.evaluate(ok_rules, "activated_metal", 1000.0, 126.58, {"Am241": 1.0}, {"Am241": (95, 1.4e10, True)})
    expect(ev["constraints"][0]["contributors"] == 1, "alpha property reaches the evaluator")
    # staging and the generated D2 sidecars against a temporary folder (no nuclear data needed)
    meta = load_json(INPUTS / "d2_cases.json")
    c3 = {c["sigma2"]: c["covariance_sha256"] for c in load_json(ROOT / "controls/p145_inputs/c3_cases.json")["cases"]}
    for entry in meta["cases"]:
        expect(c3.get(entry["sigma2"]) == entry["covariance_sha256"], f"{entry['id']} sidecar pin equals the P145 C3 pin")
        spec_doc = load_json(INPUTS / entry["spec"])
        expect(spec_doc["sampling"]["covariance"] == {"path": entry["covariance"], "sha256": entry["covariance_sha256"]}
               and spec_doc["sampling"]["seed"] == entry["seed"] and spec_doc["sampling"]["samples"] == meta["samples"]
               and spec_doc["sampling"]["channels"] == {"cross_section_mf33": True}, f"{entry['id']} spec matches cases file")
    expect(len({e["seed"] for e in meta["cases"]}) == 3, "distinct D2 seeds")
    with tempfile.TemporaryDirectory(dir=_scratch()) as folder:
        stage = Path(folder) / "stage"
        stage.mkdir()
        staged = stage_case(INPUTS / "d2_v36.json", stage, keep=True, expand=False)
        entry = next(e for e in meta["cases"] if e["id"] == "v36")
        expect((stage / entry["covariance"]).is_file() and sha(stage / entry["covariance"]) == entry["covariance_sha256"],
               "the sidecar is generated into the staging folder with the pinned SHA-256")
        expect(abs(p145.covariance_sigma2(stage / entry["covariance"]) - 0.36) == 0.0, "sigma2 of the generated sidecar")
        spec_s, budget_s, problem_s = staged["spec"], staged["budget"], staged["problem"]
        expect(spec_s["budget"] == "budget.json" and budget_s["base_spec"] == "problem.json"
               and spec_s["sampling"]["keep_samples"] == "keep" and (stage / "keep").is_dir()
               and spec_s["sampling"]["covariance"]["path"] == entry["covariance"], "staged paths")
        expect(not any(k_.startswith("_") for d in (spec_s, budget_s, problem_s) for k_ in d), "private keys stripped")
        expect(budget_s["nuclide_properties"] == PROPERTIES_TOKEN, "token kept when expansion is off")
        expect(all((stage / name).is_file() for name in ("spec.json", "budget.json", "problem.json")), "staged files")
        edited = Path(folder) / "edited"
        edited.mkdir()
        staged2 = stage_case(INPUTS / "d2_v36.json", edited, edit=edit_zero_variance, expand=False)
        expect("covariance" not in staged2["spec"]["sampling"] and staged2["spec"]["sampling"]["samples"] == 100
               and "keep_samples" not in staged2["spec"]["sampling"], "edit hook runs last and keep is off")
        # the study twin of the staged case: full material at specification, the spectrum by reference
        study = build_study(problem_s, budget_s, staged["spec"], "p146-selftest")
        material = study["cases"]["materials"][0]
        expect(material["composition"] == {"Al": 100.0 - 0.05, "Co": 0.05} and study["robustness"]["samples"] == 1024
               and study["robustness"]["covariance"]["sha256"] == entry["covariance_sha256"]
               and study["cases"]["spectra"] == [{"name": "s", "spec_ref": "problem.json"}], f"study twin {material}")
        # a covariance whose bytes are not the pinned ones is refused
        wrong = Path(folder) / "wrong"
        wrong.mkdir()
        for name in ("d2_budget.json", "d2_problem.json"):
            shutil.copyfile(INPUTS / name, wrong / name)
        doc_w = load_json(INPUTS / "d2_v36.json")
        doc_w["sampling"]["covariance"]["sha256"] = "0" * 64
        (wrong / "d2_v36.json").write_text(json.dumps(doc_w))
        refused = Path(folder) / "refused"
        refused.mkdir()
        try:
            stage_case(wrong / "d2_v36.json", refused, expand=False)
            expect(False, "a wrong pinned SHA-256 is refused")
        except SystemExit:
            pass
    # the committed inputs agree with each other
    for key, name in CASES.items():
        spec_doc = strip_private(load_json(INPUTS / name))
        budget_doc = strip_private(load_json(INPUTS / spec_doc["budget"]))
        problem_doc = strip_private(load_json(INPUTS / budget_doc["base_spec"]))
        expect(spec_doc["schema"] == SPEC_SCHEMA and budget_doc["schema"] == BUDGET_SCHEMA
               and budget_doc["nuclide_properties"] == PROPERTIES_TOKEN, f"{key} schemas")
        composition = full_composition(budget_doc["balance"], budget_doc.get("matrix", {}), budget_doc["impurities"])
        listed = problem_doc["material"]["composition"]
        expect(set(listed) == set(composition) and all(abs(listed[e] - composition[e]) < 1e-9 for e in composition),
               f"{key} base-spec material is the budget material at specification")
        expect(max(budget_doc["targets"]) <= len(problem_doc["schedule"]), f"{key} targets are schedule steps")
        expect("composition_rel_std" not in spec_doc["sampling"]["channels"], f"{key} has no composition channel")
        expect(order_index(spec_doc["sampling"]["samples"], spec_doc.get("confidence_level", 0.95)) is not None,
               f"{key} has enough samples for a budget")
        expect(budget_doc["balance"] not in budget_doc.get("matrix", {}) and budget_doc["balance"] not in budget_doc["impurities"],
               f"{key} balance is not a constituent")
        build_study(problem_doc, budget_doc, spec_doc, "p146-selftest")  # accepted by the study twin
    # the gate record names and the B6 step list
    expect(len(gate_names()) == 14 and "b3_d2_v64" in gate_names(), "gate names")
    expect(count_tests(Path("/nonexistent/log")) == (0, 0), "missing test log")
    with tempfile.TemporaryDirectory(dir=_scratch()) as folder:
        tsv = Path(folder) / "steps.tsv"
        tsv.write_text("build_cli\tcargo build --release\t0\nb6_fmt\tcargo fmt --all -- --check\t0\nb1_d1\tpython3 x\t1\n")
        read = read_steps(tsv)
        expect(read["build_cli"] == {"argv": "cargo build --release", "exit": 0} and read["b1_d1"]["exit"] == 1, "steps.tsv")
        log = Path(folder) / "log"
        log.write_text("test result: ok. 12 passed; 0 failed; 0 ignored\ntest result: FAILED. 3 passed; 2 failed; 0\n")
        expect(count_tests(log) == (15, 2), "test counts")
    print(json.dumps({"selftest": "fail" if failures else "pass", "failures": failures}))
    return 1 if failures else 0


def _scratch() -> Path:
    path = ROOT / "target"
    path.mkdir(exist_ok=True)
    return path


def main(argv: list) -> int:
    command = argv[1] if len(argv) > 1 else ""
    if command in ("b1", "b2", "b3") and len(argv) == 3:
        return {"b1": b1, "b2": b2, "b3": b3}[command](argv[2])
    if command == "b4" and len(argv) == 2:
        return b4()
    if command == "b5" and len(argv) == 2:
        return b5()
    if command == "b6-collect" and len(argv) in (2, 3):
        return b6_collect(argv[2] if len(argv) == 3 else None)
    if command == "verdict" and len(argv) == 3:
        return verdict(argv[2])
    if command == "stage" and len(argv) == 4:
        return stage_command(argv[2], argv[3])
    if command == "selftest" and len(argv) == 2:
        return selftest()
    raise SystemExit(__doc__)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
