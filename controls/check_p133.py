#!/usr/bin/env python3
"""P133 checker: trace mode must not hold radioactive initial material constant.

G2  examples/fns_fe_5min.json, a natural KCl variant (K-40 in the bulk) and examples/shielding_demo.json are
    byte-identical between reference and candidate apart from `ms`.
G3  Co-60 1e20 atoms/g, zero flux, one 5.27 y step, mode auto: the candidate selects coupled and reports Co-60
    activity and Ni-60 atoms equal to the analytic decay within 1e-9 (half-life read independently from MT=457).
G4  FNS Fe restart: the candidate's step-1 inventory used as `atoms_per_g` with a 1 d cooling step (auto) agrees with
    the original irradiation followed by the same cooling in coupled mode, within 1e-9 relative on nuclides above
    1e-12 of the total atoms.

Usage: check_p133.py --reference REF --candidate CAND [--workdir target/p133-work]
"""
import argparse
import copy
import hashlib
import json
import math
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GATE_SPEC = ROOT / "examples" / "fns_fe_5min.json"
DEMO_SPEC = ROOT / "examples" / "shielding_demo.json"
MS = re.compile(rb'"ms":\s*[-+0-9.eE]+')


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def run(binary, spec, out, timeout):
    proc = subprocess.run([binary, "run", str(spec), str(out)], cwd=ROOT, capture_output=True, text=True,
                          timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"{binary} failed on {spec}: {proc.stderr.strip()[-400:]}")
    return json.loads(Path(out).read_text())


def absolute(spec):
    spec = copy.deepcopy(spec)
    spec["library"]["path"] = str(ROOT / spec["library"]["path"])
    for key in ("primary", "fallback"):
        if spec.get("decay", {}).get(key):
            spec["decay"][key] = str(ROOT / spec["decay"][key])
    return spec


def endf_half_life(path, za, liso=0):
    """Independent MT=457 read: the HEAD record's ZA and LISO, then T1/2 in the first LIST record."""
    lines = Path(path).read_text().splitlines()
    for i, line in enumerate(lines):
        if line[70:72].strip() == "8" and line[72:75].strip() == "457":
            head_za = endf_float(line[0:11])
            lis_o = round(endf_float(line[33:44]))
            if round(head_za) == za and lis_o == liso:
                return endf_float(lines[i + 1][0:11])
    raise KeyError(f"no MT=457 for ZA {za}")


def endf_float(field):
    s = field.strip()
    if not s:
        return 0.0
    m = re.match(r"^([+-]?[0-9.]+)([+-][0-9]+)$", s)
    if m:
        return float(f"{m.group(1)}e{m.group(2)}")
    return float(s)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reference", required=True)
    ap.add_argument("--candidate", required=True)
    ap.add_argument("--workdir", default="target/p133-work")
    ap.add_argument("--out", default="results/p133_trace_decay.json")
    ap.add_argument("--timeout", type=int, default=3600)
    args = ap.parse_args(argv)
    ref = str(Path(args.reference).resolve())
    cand = str(Path(args.candidate).resolve())
    work = (ROOT / args.workdir).resolve()
    work.mkdir(parents=True, exist_ok=True)
    base = json.loads(GATE_SPEC.read_text())
    gates = {}

    # G2
    kcl = absolute(base)
    kcl["material"] = {"mass_g": 1.0, "basis": "wt_percent", "composition": {"K": 52.45, "CL": 47.55}}
    kcl["title"] = "P133 natural KCl"
    (work / "kcl.json").write_text(json.dumps(kcl))
    g2 = {}
    for label, spec in (("fns_fe_5min", GATE_SPEC), ("kcl", work / "kcl.json"), ("shielding_demo", DEMO_SPEC)):
        a, b = work / f"{label}.ref.json", work / f"{label}.cand.json"
        run(ref, spec, a, args.timeout)
        run(cand, spec, b, args.timeout)
        same = MS.sub(b'"ms":0', a.read_bytes()) == MS.sub(b'"ms":0', b.read_bytes())
        g2[label] = {"identical_apart_from_ms": same, "spec_sha256": sha256(spec)}
    gates["G2"] = {"pass": all(v["identical_apart_from_ms"] for v in g2.values()), "specs": g2}

    # G3
    probe = absolute(base)
    probe["material"] = {"mass_g": 1.0, "basis": "atoms_per_g", "composition": {"Co60": 1.0e20}}
    probe["schedule"] = [{"dt": "5.27 y", "flux": 0.0}]
    probe["title"] = "P133 Co-60 decay probe"
    (work / "co60.json").write_text(json.dumps(probe))
    res = run(cand, work / "co60.json", work / "co60.cand.json", args.timeout)
    t_half = endf_half_life(probe["decay"]["primary"], 27060)
    t = 5.27 * 365.25 * 86400.0
    lam = math.log(2.0) / t_half
    remaining = 1.0e20 * math.exp(-lam * t)
    step = res["steps"][-1]
    inv = {row["nuclide"]: row["atoms_per_g"] for row in step["inventory"]}
    co_act = step["activity_Bq_per_g"].get("Co60", 0.0)
    ni = inv.get("Ni60", 0.0)
    rel_act = abs(co_act - lam * remaining) / (lam * remaining)
    rel_ni = abs(ni - (1.0e20 - remaining)) / (1.0e20 - remaining)
    gates["G3"] = {"pass": res["mode"] == "coupled" and rel_act <= 1e-9 and rel_ni <= 1e-9,
                   "mode": res["mode"], "half_life_s": t_half, "co60_activity": co_act,
                   "co60_expected": lam * remaining, "rel_activity": rel_act, "ni60": ni,
                   "ni60_expected": 1.0e20 - remaining, "rel_ni60": rel_ni,
                   "ledger_bulk_decay": {k: res["ledger"].get(k) for k in ("bulk_decay_fraction_max", "bulk_decay_nuclide")}}

    # G4
    first = absolute(base)
    first["schedule"] = [base["schedule"][0]]
    first["options"] = dict(first.get("options", {}), mode="coupled")
    (work / "fns_step1.json").write_text(json.dumps(first))
    step1 = run(cand, work / "fns_step1.json", work / "fns_step1.cand.json", args.timeout)
    inventory = {row["nuclide"]: row["atoms_per_g"] for row in step1["steps"][-1]["inventory"] if row["atoms_per_g"] > 0}
    restart = absolute(base)
    restart["material"] = {"mass_g": 1.0, "basis": "atoms_per_g", "composition": inventory}
    restart["schedule"] = [{"dt": "1 d", "flux": 0.0}]
    restart["options"] = {k: v for k, v in restart.get("options", {}).items() if k != "mode"}
    restart["title"] = "P133 FNS restart"
    (work / "restart.json").write_text(json.dumps(restart))
    continued = copy.deepcopy(first)
    continued["schedule"] = [base["schedule"][0], {"dt": "1 d", "flux": 0.0}]
    (work / "continued.json").write_text(json.dumps(continued))
    r = run(cand, work / "restart.json", work / "restart.cand.json", args.timeout)
    c = run(cand, work / "continued.json", work / "continued.cand.json", args.timeout)
    ri = {row["nuclide"]: row["atoms_per_g"] for row in r["steps"][-1]["inventory"]}
    ci = {row["nuclide"]: row["atoms_per_g"] for row in c["steps"][-1]["inventory"]}
    total = sum(ci.values())
    worst, bad = 0.0, []
    for name, value in ci.items():
        if value < 1e-12 * total:
            continue
        rel = abs(ri.get(name, 0.0) - value) / value
        worst = max(worst, rel)
        if rel > 1e-9:
            bad.append({"nuclide": name, "continued": value, "restart": ri.get(name, 0.0), "rel": rel})
    # Report only: the 1e-12 cut keeps just the Fe isotopes, so also compare every activity contributor.
    ra, ca = r["steps"][-1]["activity_Bq_per_g"], c["steps"][-1]["activity_Bq_per_g"]
    act_total = sum(ca.values())
    act_rel = {n: abs(ra.get(n, 0.0) - v) / v for n, v in ca.items() if v >= 1e-12 * act_total}
    gates["G4"] = {"pass": not bad and r["mode"] == "coupled", "restart_mode": r["mode"],
                   "compared": sum(1 for v in ci.values() if v >= 1e-12 * total), "worst_relative": worst,
                   "bad": bad[:20],
                   "report_activity": {"compared": len(act_rel), "worst_relative": max(act_rel.values(), default=0.0),
                                       "worst_nuclide": max(act_rel, key=act_rel.get, default=None),
                                       "total_restart": sum(ra.values()), "total_continued": act_total}}

    verdict = all(gates[g]["pass"] for g in ("G2", "G3", "G4"))
    record = {"protocol": "protocols/ACTINV-P133_PROTOCOL.md",
              "protocol_sha256": sha256(ROOT / "protocols/ACTINV-P133_PROTOCOL.md"),
              "reference_sha256": sha256(ref), "candidate_sha256": sha256(cand),
              "checker_sha256": sha256(__file__), "gates": gates,
              "verdict": "PASS" if verdict else "FAIL"}
    out = ROOT / args.out
    out.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    for g in ("G2", "G3", "G4"):
        print(f"P133 {g}: {'PASS' if gates[g]['pass'] else 'FAIL'}")
    print(f"P133: {record['verdict']} ({out})")
    return 0 if verdict else 1


if __name__ == "__main__":
    sys.exit(main())
