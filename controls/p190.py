#!/usr/bin/env python3
"""P190 (protocols/ACTINV-P190_PROTOCOL.md): P178's mixture self-shielding arm with pointwise cross sections exported
from the ENDF/B-VIII.1 OpenMC HDF5 library, in place of reconstructed ENDF-6 evaluations, on P178's four models.

  python3 controls/p190.py run --model {E,I,H,B}   one `actinv run` per region (inside the bounded scope; needs
                                                   ACTINV_BIN and OPENMC_CROSS_SECTIONS); results/p190/actinv_pointwise_<m>.json
  python3 controls/p190.py assemble                results/p190_record.json (inside the bounded scope)
  python3 controls/p190.py check                   results/p190_verdict.json
  python3 controls/p190.py --selftest

P178's and P173's sealed code (p173_ce_rates.py, check_p173.py, p178_ce_rates.py, check_p178.py) is imported unchanged.
The OpenMC reference rates and the unshielded ACTINV rates are P178's committed results/p178 files, read and never
written. Only the mixture arm is new: `self_shielding.mixture.pointwise_files` (one `actinv-pointwise-1` table per
material nuclide at 294 K, written by actinv.openmc.export_pointwise from the library OpenMC's tallies used), every
group shielded. The rates are folded exactly as P178's are: R_mixture = sum_g sigma_g s_g L_g from the ledger's applied
factors; r_mix = ln(R_mixture / R_openmc); P178's decision rule is applied to r_mix.
"""
import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "controls"))
import p178_ce_rates as p178  # noqa: E402  (first: rebinds P173's tables, paths and pins to P178's)
import check_p178 as chk178  # noqa: E402

p173 = p178.p173
if p178.rehearsal():
    sys.exit("p190: P178_REHEARSAL is set; P190 reads P178's scored results and has no rehearsal")

PROTOCOL_REL = "protocols/ACTINV-P190_PROTOCOL.md"
PROTOCOL = ROOT / PROTOCOL_REL
PYSRC = ROOT / "python" / "pysrc"
P178_DIR = ROOT / "results" / "p178"           # read only
P178_RECORD = ROOT / "results" / "p178_ce_rates.json"  # read only
OUT_DIR = ROOT / "results" / "p190"
RECORD = ROOT / "results" / "p190_record.json"
VERDICT = ROOT / "results" / "p190_verdict.json"
SCHEMA_DOC = "actinv-p190-pointwise-1"
SCHEMA_RECORD = "actinv-p190-record-1"
SCHEMA_VERDICT = "actinv-p190-verdict-1"
TABLE_TEMPERATURE_K = 294.0   # the library's stored temperature export_pointwise selects for 293.6 K
MODEL_ORDER = p173.MODEL_ORDER
SEALED = (PROTOCOL_REL, "controls/p190.py", "controls/p173_ce_rates.py", "controls/check_p173.py",
          "controls/p178_ce_rates.py", "controls/check_p178.py")
P178_FILES = tuple([f"openmc_{m}.json" for m in MODEL_ORDER] + [f"actinv_{m}.json" for m in MODEL_ORDER]
                   + ["g1_check.json"])
sha256 = chk178.sha256
assert OUT_DIR != p178.OUT_DIR and RECORD != p178.RECORD, "P190 must not write where P178 reads"


def work_dir(model):
    return ROOT / "target" / f"p190-work-{model}"


# ------------------------------------------------------------------------------------------------------------- run
def import_exporter():
    """actinv.openmc from this checkout without the native extension the package's __init__ loads (as P181's child)."""
    import openmc  # noqa: F401  (ImportError here: not the OpenMC interpreter)
    pkg = types.ModuleType("actinv")
    pkg.__path__ = [str(PYSRC / "actinv")]
    sys.modules["actinv"] = pkg
    from actinv.openmc import export_pointwise
    return export_pointwise


def require_openmc_python(argv):
    """Re-exec under the OpenMC interpreter when `import openmc` is not available (the cgroup carries over)."""
    try:
        import openmc  # noqa: F401
        return
    except ImportError:
        if os.environ.get("P190_REEXEC") == "1":
            sys.exit("p190: `import openmc` fails under the OpenMC interpreter")
        os.environ["P190_REEXEC"] = "1"
        py = str(p173.OPENMC_PY)
        os.execv(py, [py, str(Path(__file__).resolve()), *argv])


def read_table(entry):
    """{nuclide, path, sha256, library, temperature_K} of one exported table, read back from the file."""
    path = Path(entry["path"])
    if sha256(path) != entry["sha256"]:
        sys.exit(f"p190: {path} does not hash to the digest export_pointwise returned")
    table = json.loads(path.read_text())
    if table.get("format") != "actinv-pointwise-1":
        sys.exit(f"p190: {path} is not an actinv-pointwise-1 table")
    return {"nuclide": table["nuclide"], "path": str(path), "sha256": entry["sha256"],
            "library": table["source"]["library"], "temperature_K": table["temperature_K"]}


def mixture_block(shielding):
    keys = ("profile", "temperature_K", "covered", "background_estimated", "evaluations_unused", "zero_k_clips",
            "pointwise_temperatures_K")
    return {k: shielding["mixture"].get(k) for k in keys}


def run_model(model, out):
    import numpy as np
    cg = p173.require_bounded()
    xs = os.environ.get("OPENMC_CROSS_SECTIONS")
    if not xs or not Path(xs).is_file():
        sys.exit("p190: OPENMC_CROSS_SECTIONS must name the library's cross_sections.xml")
    xs_sha, pin = sha256(xs), p173.PINNED["cross_sections_xml"][1]
    if xs_sha != pin:
        sys.exit(f"p190: {xs} does not match the cross_sections.xml pin P173/P178 used")
    if not Path(p173.ACTINV_BIN).is_file():
        sys.exit(f"p190: no actinv binary at {p173.ACTINV_BIN}")
    export_pointwise = import_exporter()
    om = json.loads((P178_DIR / f"openmc_{model}.json").read_text())
    final = om["final"]
    if final is None:
        sys.exit(f"p190: no OpenMC result for model {model}")
    lib = np.load(p173.ENDF8_NPZ)
    rows, sig, bounds = lib["rows"].tolist(), lib["sig"], [float(v) for v in lib["bounds"]]
    if bounds != p173.groups_ascending():
        sys.exit("p190: the library's group boundaries are not the fispact-709 boundaries")
    targets = json.loads(p173.ENDF8_INDEX.read_text())["targets"]
    selection = p173.library_selection(rows)
    atom_densities = final["atom_densities_atom_b_cm"]
    names = sorted(n for n, d in atom_densities.items() if float(d) > 0)
    work = work_dir(model)
    work.mkdir(parents=True, exist_ok=True)
    files = export_pointwise(names, work / "tables", p178.MIXTURE_TEMPERATURE_K, cross_sections=xs)
    tables = [read_table(f) for f in files]
    exported = {t["nuclide"] for t in tables}
    without = {n: "not in the OpenMC library's neutron files" for n in names if p178.actinv_nuclide(n) not in exported}
    if not tables:
        sys.exit(f"p190: no pointwise table for any nuclide of model {model}")
    library = {"path": str(p173.ENDF8_NPZ), "sha256": p173.sha256_file(p173.ENDF8_NPZ)}
    decay = {"primary": str(p178.DECAY_PRIMARY), "fallback": str(p178.DECAY_FALLBACK)}
    version = subprocess.run([str(p173.ACTINV_BIN), "--version"], capture_output=True, text=True,
                             timeout=60).stdout.strip()
    reaction_targets = sorted({t for t, _, _ in p173.model_spec(model)["reactions"]})
    regions = {}
    for region, flux in final["flux"].items():
        tag = region.replace(":", "_")
        spec_path, out_path = work / f"{tag}_spec.json", work / f"{tag}_result.json"
        spec = p178.build_mixture_spec(model, region, flux["mean"], atom_densities, [], bounds, library, decay)
        spec["title"] = f"P190 pointwise mixture self-shielding, model {model} region {region}"
        spec["self_shielding"] = {"mixture": {"pointwise_files": [{"path": f["path"], "sha256": f["sha256"]}
                                                                  for f in files]}}
        p173.write_atomic(spec_path, spec)
        t0 = time.monotonic()
        run = subprocess.run([str(p173.ACTINV_BIN), "run", str(spec_path), str(out_path)], capture_output=True,
                             text=True, timeout=p178.RUN_TIMEOUT_S)
        wall = time.monotonic() - t0
        (work / f"{tag}.stderr").write_text(run.stderr)
        if run.returncode != 0:
            sys.exit(f"p190: `actinv run` failed for {region}: {run.stderr[-600:]}")
        shielding = json.loads(out_path.read_text())["ledger"]["shielding"]
        if tuple(shielding.get("channels", ())) != p178.CHANNELS or "mixture" not in shielding:
            sys.exit(f"p190: the ledger of {region} has no mixture self-shielding block")
        applied = shielding["applied_factors"]
        reactions = {p173.rate_key(t, mt): p178.mixture_reaction(rows, sig, targets, selection, t, mt, flux["mean"],
                                                                 flux["std_dev"], applied.get(t))
                     for t, mt, _ in p173.model_spec(model)["reactions"]}
        regions[region] = {
            "wall_s": wall, "spec_sha256": p173.sha256_file(spec_path), "result_sha256": p173.sha256_file(out_path),
            "mixture": mixture_block(shielding),
            "applied_factors": {t: applied[t] for t in reaction_targets if t in applied},
            "reactions": reactions}
    document = {"schema": SCHEMA_DOC, "model": model, "regions": regions, "tables": tables,
                "nuclides_without_table": without,
                "composition_atom_densities_atom_b_cm": p178.composition_from_openmc(atom_densities),
                "temperature_K": p178.MIXTURE_TEMPERATURE_K, "table_temperature_K": TABLE_TEMPERATURE_K,
                "cross_sections_xml": {"path": str(Path(xs).resolve()), "sha256": xs_sha},
                "library_sha256": library["sha256"], "index_sha256": p173.sha256_file(p173.ENDF8_INDEX),
                "actinv_bin": str(p173.ACTINV_BIN), "actinv_bin_sha256": p173.sha256_file(p173.ACTINV_BIN),
                "actinv_version": version, "environment": p173.environment_record(cg)}
    p173.write_atomic(out, document)


# ---------------------------------------------------------------------------------------------------------- assemble
def assemble(record_path):
    """P173's assembly from P178's committed files, the P190 documents folded in where P178 folded its mixture
    documents. Nothing is written but `record_path`."""
    docs = {m: json.loads((OUT_DIR / f"actinv_pointwise_{m}.json").read_text()) for m in MODEL_ORDER}
    p173.assemble(record_path)  # reads results/p178 (P178's OUT_DIR), writes only record_path
    document = json.loads(Path(record_path).read_text())
    p178.add_mixture_to_rows(document["rows"], docs)
    document["schema"] = SCHEMA_RECORD
    document.setdefault("environment", {}).setdefault("cgroups", []).extend(
        {"where": f"actinv_pointwise:{m}", "cgroup": d["environment"]["cgroup"]} for m, d in docs.items())
    document["p178_inputs"] = {name: sha256(P178_DIR / name) for name in P178_FILES}
    document["pointwise_arm"] = {
        "temperature_K": p178.MIXTURE_TEMPERATURE_K, "table_temperature_K": TABLE_TEMPERATURE_K,
        "models": {m: {"tables": d["tables"], "nuclides_without_table": d["nuclides_without_table"],
                       "actinv_bin_sha256": d["actinv_bin_sha256"], "actinv_version": d["actinv_version"],
                       "git_head": d["environment"]["git_head"], "clean_tree": d["environment"]["clean_tree"],
                       "cgroup": d["environment"]["cgroup"], "cross_sections_xml": d["cross_sections_xml"],
                       "wall_s": {r: rd["wall_s"] for r, rd in d["regions"].items()},
                       "regions": {r: {k: v for k, v in rd.items() if k not in ("reactions", "applied_factors")}
                                   for r, rd in d["regions"].items()}}
                   for m, d in docs.items()}}
    p173.write_atomic(record_path, document)


# ------------------------------------------------------------------------------------------------------------ check
def registration_commits():
    """Commits that added the P190 protocol, newest first (`git log --diff-filter=A`; the last is the registration)."""
    return subprocess.run(["git", "log", "--diff-filter=A", "--format=%H", "--", PROTOCOL_REL],
                          capture_output=True, text=True, cwd=ROOT).stdout.split()


def table_problems(model, tables, xs_dir, disk):
    """Every table: its source library inside the cross_sections.xml directory, 294 K, a digest, and (when the file
    exists and `disk`) the digest of the file."""
    problems = []
    if not tables:
        return [f"{model}: no pointwise table"]
    dirs = {Path(xs_dir), Path(xs_dir).resolve()}
    for t in tables:
        name = f"{model}/{t.get('nuclide')}"
        library = Path(str(t.get("library")))
        if not any(d == library or d in library.parents for d in dirs):
            problems.append(f"{name}: source library {library} is not under {xs_dir}")
        temp = t.get("temperature_K")
        if not chk178.good(temp) or abs(temp - TABLE_TEMPERATURE_K) > 1e-9:
            problems.append(f"{name}: table temperature {temp} is not {TABLE_TEMPERATURE_K}")
        digest = t.get("sha256")
        if not (isinstance(digest, str) and len(digest) == 64):
            problems.append(f"{name}: no table digest")
        elif disk and Path(str(t.get("path"))).is_file() and sha256(t["path"]) != digest:
            problems.append(f"{name}: the table file does not hash to the recorded digest")
    return problems


def g1_problems(record, registration, xs_pin, disk=True):
    problems = []
    arm = record.get("pointwise_arm")
    if not isinstance(arm, dict):
        return ["the record has no pointwise_arm block"]
    if record.get("schema") != SCHEMA_RECORD:
        problems.append("wrong schema")
    models = arm.get("models", {})
    bins = {models.get(m, {}).get("actinv_bin_sha256") for m in MODEL_ORDER}
    if len(bins) != 1 or None in bins:
        problems.append(f"the four models did not run with one actinv binary: {sorted(map(str, bins))}")
    xs = {json.dumps(models.get(m, {}).get("cross_sections_xml"), sort_keys=True) for m in MODEL_ORDER}
    if len(xs) != 1:
        problems.append("the models used different cross_sections.xml files")
    seen = {e.get("where"): e.get("cgroup") for e in record.get("environment", {}).get("cgroups", [])}
    for m in MODEL_ORDER:
        info = models.get(m)
        if info is None:
            problems.append(f"no pointwise record for model {m}")
            continue
        if not registration:
            problems.append("the P190 registration commit is not in the history")
        elif info.get("git_head") != registration[-1]:
            problems.append(f"model {m}: ran at {info.get('git_head')}, not the registration commit {registration[-1]}")
        if info.get("clean_tree") is not True:
            problems.append(f"model {m}: tracked tree was not clean")
        entry = info.get("cross_sections_xml") or {}
        if entry.get("sha256") != xs_pin:
            problems.append(f"model {m}: cross_sections.xml is not the pin P173/P178 used")
        problems += table_problems(m, info.get("tables"), Path(str(entry.get("path", "/"))).parent, disk)
        for name, cg in ((f"actinv_pointwise:{m}", seen.get(f"actinv_pointwise:{m}")), (f"record:{m}", info.get("cgroup"))):
            if not p173.cgroup_ok(cg or {}):
                problems.append(f"cgroup of {name} is not the bounded scope")
        walls = info.get("wall_s") or {}
        if sorted(walls) != sorted(p173.regions_of(m)) or not all(chk178.good(w) and w >= 0 for w in walls.values()):
            problems.append(f"model {m}: wall times are not recorded for every region")
    return problems


def p178_problems(record, committed, directory=None):
    """The record's P178-derived parts are P178's committed ones: the input files hash as recorded, and every row's
    OpenMC and unshielded rates equal the committed P178 record's."""
    directory = P178_DIR if directory is None else Path(directory)
    problems = []
    for name, digest in (record.get("p178_inputs") or {}).items():
        if name not in P178_FILES or (directory / name).is_file() and sha256(directory / name) != digest:
            problems.append(f"p178 input {name} differs from the recorded digest")
    if sorted(record.get("p178_inputs") or {}) != sorted(P178_FILES):
        problems.append("the record does not list every P178 input file")
    key = lambda r: (r["model"], r["region"], r["target"], r["mt"])  # noqa: E731
    old = {key(r): r for r in committed.get("rows", [])}
    for r in record.get("rows", []):
        o = old.get(key(r))
        if o is None or any(o.get(k) != r.get(k) for k in ("status", "R_openmc", "R_openmc_se", "R_actinv", "r")):
            problems.append(f"{key(r)}: not P178's committed OpenMC/unshielded values")
    if len(old) != len(record.get("rows", [])):
        problems.append("the row count differs from P178's")
    return problems[:50]


def g2_gate(record):
    """P178's G2 (fidelity of r and r_mix to their rates) and its capture and control decision rules on r_mix.
    Returns (problems, result, outcome-ok)."""
    problems = chk178.g2(record)
    if problems:
        return problems, {}, False
    arm = record["pointwise_arm"]["models"]
    view = dict(record, mixture_arm={"models": {m: {"nuclides_without_tape": arm[m]["nuclides_without_table"],
                                                    "regions": arm[m]["regions"]} for m in MODEL_ORDER}})
    result, _ = chk178.summary(view)
    ok = result["outcome"] in ("QUALIFIED", "NOTHING TO CORRECT") and result["capture_rows_used"] > 0
    return problems, result, ok


def sentences(record, result):
    capture = [r for r in record["rows"] if chk178.is_used(r) and r["mt"] == chk178.CAPTURE_MT]
    x = chk178.round_up_tenth(max(math.expm1(abs(r["r_mix"])) * 100.0 for r in capture))
    wall = chk178.round_up_tenth(max(w for m in MODEL_ORDER
                                     for w in record["pointwise_arm"]["models"][m]["wall_s"].values()))
    out = ("On P178's four models, ACTINV's mixture self-shielding with pointwise cross sections exported from "
           "OpenMC's ENDF/B-VIII.1 HDF5 library at 294 K, instead of reconstructed ENDF-6 evaluations, brought every "
           f"listed resonance-capture rate within {x:.1f}% of OpenMC's continuous-energy rate on the same data; each "
           f"region's run took at most {wall:.1f} s.")
    out = [out]
    unused = result["capture_rows_listed"] - result["capture_rows_used"]
    if unused:
        out.append(f"{unused} of {result['capture_rows_listed']} listed capture rows were not scored or did not reach "
                   f"a {100 * p173.REL_SE_TARGET:.1f}% standard error and are not covered by the sentence above.")
    return out


def check(record=None, *, registration=None, log_text=None, disk=True, committed=None):
    record = json.loads(RECORD.read_text()) if record is None else record
    registration = registration_commits() if registration is None else registration
    log_text = (ROOT / "protocols" / "protocol_hash.txt").read_text() if log_text is None else log_text
    committed = json.loads(P178_RECORD.read_text()) if committed is None else committed
    gates = {}
    sealed = {p: (ROOT / p).is_file() and sha256(ROOT / p) in log_text for p in SEALED}
    gates["G0"] = {"pass": all(sealed.values()), "sealed": sealed}
    xs_pin = p173.PINNED["cross_sections_xml"][1]
    p1 = g1_problems(record, registration, xs_pin, disk) + p178_problems(record, committed)
    gates["G1"] = {"pass": not p1, "problems": p1[:50], "registration_commit": registration[-1:] or None}
    p2, result, ok = g2_gate(record)
    gates["G2"] = {"pass": not p2 and ok, "problems": p2[:50], "outcome": result.get("outcome"),
                   "controls_hold": result.get("controls_hold")}
    gates["G3"] = {"pass": None, "note": "CI green on the evidence commit, checked after the push"}
    passed = all(gates[g]["pass"] for g in ("G0", "G1", "G2"))
    verdict = {"schema": SCHEMA_VERDICT, "protocol": "ACTINV-P190", "gates": gates, "summary": result,
               "verdict": "P190-PASS (pending G3)" if passed else "P190-FAIL"}
    if passed:
        verdict["allowed_sentences"] = sentences(record, result)
    return verdict


# --------------------------------------------------------------------------------------------------------- selftest
def synthetic(registration="a" * 40):
    record = chk178.synthetic_record("h", "g", "p")
    record["schema"] = SCHEMA_RECORD
    record.pop("mixture_arm")
    cg = {"memory.max": str(6 * p173.GIB), "memory.swap.max": "0", "pids.max": "128", "cpu.max": "200000 100000"}
    record["environment"]["cgroups"] = [e for e in record["environment"]["cgroups"]
                                        if not e["where"].startswith("actinv_mixture")]
    xs = {"path": "/data/hdf5/cross_sections.xml", "sha256": p173.PINNED["cross_sections_xml"][1]}
    models = {}
    for m in MODEL_ORDER:
        record["environment"]["cgroups"].append({"where": f"actinv_pointwise:{m}", "cgroup": dict(cg)})
        walls = {r: 1.5 for r in p173.regions_of(m)}
        models[m] = {"tables": [{"nuclide": "Fe56", "path": "/nonexistent/Fe56_294K.json", "sha256": "c" * 64,
                                 "library": "/data/hdf5/Fe56.h5", "temperature_K": 294.0}],
                     "nuclides_without_table": {}, "actinv_bin_sha256": "b" * 64, "actinv_version": "actinv 1.7.2",
                     "git_head": registration, "clean_tree": True, "cgroup": dict(cg), "cross_sections_xml": dict(xs),
                     "wall_s": walls, "regions": {r: {"wall_s": w, "mixture": {"zero_k_clips": None}}
                                                  for r, w in walls.items()}}
    record["pointwise_arm"] = {"temperature_K": 293.6, "table_temperature_K": 294.0, "models": models}
    record["p178_inputs"] = {name: "0" * 64 for name in P178_FILES}
    return record


def selftest():
    import copy
    import tempfile
    failures = []
    reg = ["a" * 40]
    xs_pin = p173.PINNED["cross_sections_xml"][1]
    committed = copy.deepcopy(synthetic(reg[-1]))
    b0 = p173.regions_of("B")[0]

    def gates(record, registration=reg):
        g1 = g1_problems(record, registration, xs_pin, disk=False)
        p2, result, ok = g2_gate(record)
        return g1, p2, result, ok

    def expect(label, record, *, g1=None, g2=None, registration=reg):
        a, b, result, ok = gates(record, registration)
        if g1 is not None and bool(a) != g1:
            failures.append(f"{label}: G1 problems {a[:2]}")
        if g2 is not None and bool(b or not ok) != g2:
            failures.append(f"{label}: G2 problems {b[:2]} outcome {result.get('outcome')}")
        return result

    result = expect("clean", synthetic(), g1=False, g2=False)
    if result.get("outcome") != "NOTHING TO CORRECT" or not result["controls_hold"]:
        failures.append(f"clean outcome {result.get('outcome')}")
    text = sentences(synthetic(), result)
    if len(text) != 1 or "within 0.5% of OpenMC's continuous-energy rate on the same data; each region's run took " \
            "at most 1.5 s." not in text[0] or "at 294 K" not in text[0]:
        failures.append(f"sentence wrong: {text}")
    # a capture row material in r_mix: not qualified
    rec = synthetic()
    chk178.tweak(rec, "B", b0, {("Cu63", 102)}, mix=0.8)
    result = expect("material capture", rec, g1=False, g2=True)
    if result.get("outcome") != "NOT QUALIFIED" or len(result["material_capture_r_mix_rows"]) != 1:
        failures.append(f"material capture row not caught: {result.get('outcome')}")
    # a control moved by the mixture
    rec = synthetic()
    chk178.tweak(rec, "B", b0, {("Cu63", 107)}, mix=1.05)
    result = expect("control shift", rec, g1=False, g2=True)
    if result.get("controls_hold") is not False:
        failures.append("a control shift must break the controls")
    # fidelity: r_mix that does not recompute
    rec = synthetic()
    rec["rows"][3]["r_mix"] = 0.4
    expect("r_mix recompute", rec, g2=True)
    # G1
    expect("registration commit", synthetic("e" * 40), g1=True)
    expect("no registration", synthetic(), g1=True, registration=[])
    rec = synthetic()
    rec["pointwise_arm"]["models"]["H"]["clean_tree"] = False
    expect("dirty tree", rec, g1=True)
    rec = synthetic()
    rec["pointwise_arm"]["models"]["I"]["actinv_bin_sha256"] = "0" * 64
    expect("two binaries", rec, g1=True)
    rec = synthetic()
    rec["pointwise_arm"]["models"]["E"]["tables"][0]["library"] = "/elsewhere/Fe56.h5"
    expect("library elsewhere", rec, g1=True)
    rec = synthetic()
    rec["pointwise_arm"]["models"]["E"]["tables"][0]["temperature_K"] = 600.0
    expect("table temperature", rec, g1=True)
    rec = synthetic()
    rec["pointwise_arm"]["models"]["E"]["tables"] = []
    expect("no tables", rec, g1=True)
    rec = synthetic()
    for m in MODEL_ORDER:
        rec["pointwise_arm"]["models"][m]["cross_sections_xml"]["sha256"] = "0" * 64
    expect("xml pin", rec, g1=True)
    rec = synthetic()
    next(c for c in rec["environment"]["cgroups"] if c["where"] == "actinv_pointwise:B")["cgroup"]["memory.swap.max"] = "max"
    expect("cgroup", rec, g1=True)
    rec = synthetic()
    rec["pointwise_arm"]["models"]["B"]["wall_s"].pop(b0)
    expect("wall time", rec, g1=True)
    # the table file on disk must hash as recorded
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "Fe56_294K.json"
        path.write_text("{}")
        tables = [{"nuclide": "Fe56", "path": str(path), "sha256": sha256(path), "library": "/data/hdf5/Fe56.h5",
                   "temperature_K": 294.0}]
        if table_problems("E", tables, "/data/hdf5", True):
            failures.append("a table that matches its file and library must pass")
        tables[0]["sha256"] = "0" * 64
        if not table_problems("E", tables, "/data/hdf5", True):
            failures.append("a table file that changed must fail")
    rec = synthetic()
    rec["rows"][0]["R_openmc"] *= 1.1
    if not any("committed" in p for p in p178_problems(rec, committed, "/nonexistent")):
        failures.append("a changed OpenMC rate must be caught")
    if p178_problems(synthetic(), committed, "/nonexistent"):
        failures.append(f"unchanged rows must pass: {p178_problems(synthetic(), committed, '/nonexistent')[:2]}")
    # check() end to end on a synthetic record, with hashes of the real sealed files supplied
    log_text = "\n".join(f"{sha256(ROOT / p)}  {p}" for p in SEALED if (ROOT / p).is_file())
    verdict = check(synthetic(), registration=reg, log_text=log_text, disk=False, committed=committed)
    if verdict["gates"]["G2"]["pass"] is not True:
        failures.append(f"check(): G2 on a clean synthetic record: {verdict['gates']['G2']}")
    if verdict["gates"]["G3"]["pass"] is not None:
        failures.append("G3 must be None")
    print("p190 selftest:", "PASS" if not failures else "FAIL")
    for failure in failures:
        print("  " + failure)
    return not failures


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv == ["--selftest"]:
        return 0 if selftest() else 1
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="mode", required=True)
    sub.add_parser("run").add_argument("--model", choices=list(MODEL_ORDER), required=True)
    sub.add_parser("assemble")
    sub.add_parser("check")
    args = ap.parse_args(argv)
    if args.mode == "run":
        require_openmc_python(argv)
        run_model(args.model, OUT_DIR / f"actinv_pointwise_{args.model}.json")
    elif args.mode == "assemble":
        p173.require_bounded()
        assemble(RECORD)
        print(f"record -> {RECORD.relative_to(ROOT)}")
    else:
        verdict = check()
        VERDICT.parent.mkdir(parents=True, exist_ok=True)
        VERDICT.write_text(json.dumps(verdict, indent=1, sort_keys=True) + "\n")
        for gate, body in verdict["gates"].items():
            print(gate, body["pass"])
        print(verdict["verdict"])
        for sentence in verdict.get("allowed_sentences", []):
            print("  " + sentence)
    return 0


if __name__ == "__main__":
    sys.exit(main())
