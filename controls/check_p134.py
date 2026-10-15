#!/usr/bin/env python3
"""P134 checker: trace-mode pathway attribution must use each step's multiplier and spectrum.

G2  examples/fns_fe_5min.json and examples/shielding_demo.json are byte-identical between reference and candidate
    apart from `ms`.
G3  FNS Fe in trace mode with (a) flux 0.37 on the irradiation step, (b) irradiate-cool-irradiate, (c) a softened
    step spectrum on the irradiation step: `pathway_closure` <= 1e-9, and for every nuclide above 1e-12 of the step's
    tracked inventory the full decomposition total equals the inventory within 1e-9. Listed chains are truncated at
    1e-6 of their nuclide for reporting, but each carries `fraction` of the complete decomposition, so
    atoms_per_g / fraction recovers the full sum; every listed chain of a nuclide must give the same total.
G4  For (a), every pathway's atoms in the irradiation step equal 0.37 x the flux-1.0 run's within 1e-9.

Usage: check_p134.py --reference REF --candidate CAND [--workdir target/p134-work]
"""
import argparse
import copy
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GATE_SPEC = ROOT / "examples" / "fns_fe_5min.json"
DEMO_SPEC = ROOT / "examples" / "shielding_demo.json"
MS = re.compile(rb'"ms":\s*[-+0-9.eE]+')
TOL = 1e-9


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
    spec["options"] = dict(spec.get("options", {}), mode="trace")
    return spec


def closure_check(result):
    worst, bad, compared = 0.0, [], 0
    for si, (step, per) in enumerate(zip(result["steps"], result["pathways"])):
        inventory = {row["nuclide"]: row["atoms_per_g"] for row in step["inventory"]}
        total = sum(inventory.values())
        for nuclide, chains in per.items():
            atoms = inventory.get(nuclide, 0.0)
            if atoms < 1e-12 * total or not chains:
                continue
            totals = [c["atoms_per_g"] / c["fraction"] for c in chains if c["fraction"] > 0]
            spread = (max(totals) - min(totals)) / max(totals)
            rel = abs(totals[0] - atoms) / atoms
            compared += 1
            worst = max(worst, rel, spread)
            if rel > TOL or spread > TOL:
                bad.append({"step": si + 1, "nuclide": nuclide, "inventory": atoms, "decomposition": totals[0],
                            "rel": rel, "spread": spread})
    return {"pass": result["pathway_closure"] <= TOL and not bad and compared > 0, "mode": result["mode"],
            "pathway_closure": result["pathway_closure"], "compared": compared, "worst": worst, "bad": bad[:20]}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reference", required=True)
    ap.add_argument("--candidate", required=True)
    ap.add_argument("--workdir", default="target/p134-work")
    ap.add_argument("--out", default="results/p134_pathway_attribution.json")
    ap.add_argument("--timeout", type=int, default=3600)
    args = ap.parse_args(argv)
    ref = str(Path(args.reference).resolve())
    cand = str(Path(args.candidate).resolve())
    work = (ROOT / args.workdir).resolve()
    work.mkdir(parents=True, exist_ok=True)
    base = json.loads(GATE_SPEC.read_text())
    gates = {}

    g2 = {}
    for label, spec in (("fns_fe_5min", GATE_SPEC), ("shielding_demo", DEMO_SPEC)):
        a, b = work / f"{label}.ref.json", work / f"{label}.cand.json"
        run(ref, spec, a, args.timeout)
        run(cand, spec, b, args.timeout)
        same = MS.sub(b'"ms":0', a.read_bytes()) == MS.sub(b'"ms":0', b.read_bytes())
        g2[label] = {"identical_apart_from_ms": same, "spec_sha256": sha256(spec)}
    gates["G2"] = {"pass": all(v["identical_apart_from_ms"] for v in g2.values()), "specs": g2}

    unit = absolute(base)
    scaled = absolute(base)
    scaled["schedule"][0] = dict(scaled["schedule"][0], flux=0.37)
    pulsed = absolute(base)
    pulsed["schedule"] = [base["schedule"][0], {"dt": "600 s", "flux": 0.0}, base["schedule"][0]] + base["schedule"][1:]
    softened = absolute(base)
    step_spectrum = copy.deepcopy(base["spectrum"])
    groups = step_spectrum["flux_per_group"]
    # halve the upper half of the groups (descending order puts the fast groups first) and keep the total unknown
    step_spectrum["flux_per_group"] = [v * 0.5 if i < len(groups) // 2 else v for i, v in enumerate(groups)]
    step_spectrum.pop("total", None)
    softened["schedule"][0] = dict(softened["schedule"][0], spectrum=step_spectrum)
    results = {}
    for label, spec in (("unit", unit), ("scaled", scaled), ("pulsed", pulsed), ("softened", softened)):
        path = work / f"{label}.json"
        path.write_text(json.dumps(spec))
        results[label] = run(cand, path, work / f"{label}.cand.json", args.timeout)
    g3 = {label: closure_check(results[label]) for label in ("scaled", "pulsed", "softened")}
    gates["G3"] = {"pass": all(v["pass"] and v["mode"] == "trace" for v in g3.values()), "cases": g3}

    worst, bad, compared = 0.0, [], 0
    for nuclide, chains in results["unit"]["pathways"][0].items():
        other = {(c["from"], c["first_product"]): c["atoms_per_g"] for c in results["scaled"]["pathways"][0].get(nuclide, [])}
        for c in chains:
            key = (c["from"], c["first_product"])
            if key not in other:
                continue
            rel = abs(other[key] - 0.37 * c["atoms_per_g"]) / (0.37 * c["atoms_per_g"])
            compared += 1
            worst = max(worst, rel)
            if rel > TOL:
                bad.append({"nuclide": nuclide, "from": key[0], "first_product": key[1], "rel": rel})
    gates["G4"] = {"pass": compared > 0 and not bad, "compared": compared, "worst_relative": worst, "bad": bad[:20]}

    verdict = all(gates[g]["pass"] for g in ("G2", "G3", "G4"))
    record = {"protocol": "protocols/ACTINV-P134_PROTOCOL.md",
              "protocol_sha256": sha256(ROOT / "protocols/ACTINV-P134_PROTOCOL.md"),
              "reference_sha256": sha256(ref), "candidate_sha256": sha256(cand),
              "checker_sha256": sha256(__file__), "gates": gates, "verdict": "PASS" if verdict else "FAIL"}
    out = ROOT / args.out
    out.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    for g in ("G2", "G3", "G4"):
        print(f"P134 {g}: {'PASS' if gates[g]['pass'] else 'FAIL'}")
    for label, v in g3.items():
        print(f"  G3/{label}: closure {v['pathway_closure']:.3e}, {v['compared']} nuclides, worst {v['worst']:.3e}")
    print(f"  G4: {compared} pathways, worst {worst:.3e}")
    print(f"P134: {record['verdict']} ({out})")
    return 0 if verdict else 1


if __name__ == "__main__":
    sys.exit(main())
