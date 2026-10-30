#!/usr/bin/env python3
"""P167 checker (protocols/ACTINV-P167_PROTOCOL.md): ACTINV reaction rates in the NPI Rez 7Li(p,n) fields.
Reads results/p167_rez.json written by controls/p167_rez.py. G0-G3 are evaluated here; G4 (CI on the pushed evidence
commit) is external.

  G0  The protocol's SHA-256 (and any protocols/ACTINV-P167_AMENDMENT_*.md) is in protocols/protocol_hash.txt.
  G1  Pinned PDF, library, index, IRDFF-II archive and tables file; a fresh extraction equals the tables file when the
      PDF is present; the pinned binary; the crates tree of d6f516b; a clean tracked tree; the bounded cgroup.
  G2  Registered spectra and reactions with their statuses; every IRDFF-II fold within 0.35 percentage points of the
      published value; ACTINV rates finite, positive and equal to the independent fold within 1e-9; equal_lethargy
      within 1e-3.
  G3  The summary and allowed sentences are recomputed and written to results/p167_verdict.json (with --no-write,
      compared with it).

Verdict: P167-PASS if G0-G3 hold (G4 external). Accuracy is reported, not gated.

Usage: check_p167.py [--record FILE] [--no-write]
       check_p167.py --selftest
"""
import argparse
import copy
import hashlib
import json
import math
import statistics
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "controls"))
import p167_rez  # noqa: E402

PROTOCOL = ROOT / "protocols" / "ACTINV-P167_PROTOCOL.md"
HASH_LOG = ROOT / "protocols" / "protocol_hash.txt"
RECORD = ROOT / "results" / "p167_rez.json"
VERDICT = ROOT / "results" / "p167_verdict.json"
TABLES = ROOT / "results" / "p167" / "rez_tables.json"

SCHEMA = "actinv-p167-rez-1"
TABLES_SHA256 = "7a2281ba2a2a5b6be0aeb9ab813ee09da1817f0be1daee16cd751b216574921a"
BINARY_SHA256 = "186cc2587bb61766b3b3c426c61cd44f00ed472401026dea2ca3dd595c8d1194"
SOURCE_COMMIT = "d6f516b"
GIB = 1 << 30
SCORED_124 = ["54Fe(n,p)", "47Ti(n,p)", "46Ti(n,p)", "59Co(n,p)", "60Ni(n,p)", "56Fe(n,p)", "24Mg(n,p)", "59Co(n,a)",
              "48Ti(n,p)", "51V(n,a)", "197Au(n,2n)", "58Ni(n,x)57Co", "59Co(n,2n)"]
REGISTERED = {
    "12.4": {"bins": 113, "range": [0.9, 12.2],
             "status": {**{r: "scored" for r in SCORED_124}, "58Ni(n,p)": "normalization",
                        "27Al(n,a)": "normalization"}},
    "14.4": {"bins": 126, "range": [1.0, 13.6],
             "status": {**{r: "scored" for r in SCORED_124 + ["54Fe(n,a)", "19F(n,2n)", "55Mn(n,2n)"]},
                        "58Ni(n,2n)": "secondary", "58Ni(n,p)": "normalization", "27Al(n,a)": "normalization"}},
}
CONTEXT = ("ENDF/B-VIII", "JEFF-3.3", "JENDL-4", "IRDFF-II")


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=60).stdout.strip()


def g0():
    if not PROTOCOL.is_file():
        return [f"missing {PROTOCOL.name}"]
    logged = HASH_LOG.read_text()
    return [f"{path.name} hash not in protocol_hash.txt"
            for path in [PROTOCOL, *sorted(PROTOCOL.parent.glob("ACTINV-P167_AMENDMENT_*.md"))]
            if f"{sha256(path)}  protocols/{path.name}" not in logged]


def cgroup_ok(cg):
    try:
        quota, period = cg["cpu.max"].split()
        return (int(cg["memory.max"]) <= 6 * GIB and cg["memory.swap.max"] == "0" and int(cg["pids.max"]) <= 128
                and quota != "max" and int(quota) <= 2 * int(period))
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def g1(record, *, reextract=True, source_tree=None):
    problems = []
    if record.get("schema") != SCHEMA:
        problems.append("wrong schema")
    for name, digest in p167_rez.PINNED.items():
        if record["inputs"].get(name, {}).get("sha256") != digest:
            problems.append(f"input {name} hash is not the pin")
    if record["tables"]["sha256"] != TABLES_SHA256:
        problems.append("tables hash in the record is not the pin")
    if TABLES.is_file() and sha256(TABLES) != TABLES_SHA256:
        problems.append("checked-out tables file is not the pin")
    if reextract and p167_rez.PDF.is_file():
        if json.loads(json.dumps(p167_rez.tables_document())) != json.loads(TABLES.read_text()):
            problems.append("fresh extraction does not equal the tables file")
    environment = record["environment"]
    if environment.get("binary_sha256") != BINARY_SHA256:
        problems.append("binary is not the pinned P163 release binary")
    expected = source_tree if source_tree is not None else git("rev-parse", f"{SOURCE_COMMIT}:crates")
    if environment.get("crates_tree") != expected:
        problems.append(f"crates tree is not {SOURCE_COMMIT}'s")
    if not environment.get("clean_tree"):
        problems.append("tracked tree was not clean")
    if not cgroup_ok(environment.get("cgroup", {})):
        problems.append("harness cgroup is not the bounded scope")
    return problems


def g2(record):
    problems = []
    if set(record["fields"]) != set(REGISTERED):
        return ["field set is not the registered one"]
    for field, data in record["fields"].items():
        registered = REGISTERED[field]
        if data["spectrum_bins"] != registered["bins"] or data["spectrum_range_MeV"] != registered["range"]:
            problems.append(f"{field}: spectrum is not {registered['bins']} bins over {registered['range']} MeV")
        statuses = {row["reaction"]: row["status"] for row in data["rates"]}
        if statuses != registered["status"] or len(data["rates"]) != len(statuses):
            problems.append(f"{field}: reactions or statuses are not the registered ones")
        for row in data["rates"]:
            published = (row.get("published") or {}).get("IRDFF-II")
            if published is not None:
                if row.get("irdff_rate") is None:
                    problems.append(f"{field} {row['reaction']}: no IRDFF-II fold")
                elif abs(100.0 * (row["irdff_rate"] / row["rate_per_s"] - 1.0) - published) > 0.35:
                    problems.append(f"{field} {row['reaction']}: IRDFF-II fold does not reproduce {published}%")
            rate, independent = row.get("actinv_rate"), row.get("independent_fold_rate")
            if not (isinstance(rate, float) and math.isfinite(rate) and rate > 0):
                problems.append(f"{field} {row['reaction']}: ACTINV rate {rate}")
                continue
            if abs(rate - independent) > 1e-9 * independent:
                problems.append(f"{field} {row['reaction']}: ACTINV and the independent fold differ")
            if abs(row["actinv_rate_equal_lethargy"] - rate) > 1e-3 * rate:
                problems.append(f"{field} {row['reaction']}: equal_lethargy sensitivity above 1e-3")
    return problems


def stats(points):
    logs = sorted(abs(math.log(p["ce"])) for p in points)
    n = len(logs)
    return {
        "points": n,
        "geometric_mean_C_over_E": math.exp(math.fsum(math.log(p["ce"]) for p in points) / n),
        "median_abs_log_C_over_E": statistics.median(logs),
        "p90_abs_log_C_over_E": logs[min(n - 1, math.ceil(0.9 * n) - 1)],
        "max_abs_log_C_over_E": logs[-1],
        "within_1_sigma": sum(abs(p["ce"] - 1.0) <= p["sigma"] for p in points) / n,
        "within_2_sigma": sum(abs(p["ce"] - 1.0) <= 2 * p["sigma"] for p in points) / n,
        "within_2_sigma_count": sum(abs(p["ce"] - 1.0) <= 2 * p["sigma"] for p in points),
    }


def summary(record):
    scored, diagnostics = [], []
    for field, data in record["fields"].items():
        for row in data["rates"]:
            ce = row["actinv_rate"] / row["rate_per_s"]
            entry = {"field": field, "reaction": row["reaction"], "status": row["status"], "ce": ce,
                     "measured": row["rate_per_s"], "calculated": row["actinv_rate"]}
            if row["status"] == "scored":
                published = row["published"]
                entry["sigma"] = published["uncertainty_percent"] / 100.0
                entry["context"] = {name: (None if published[name] is None else 1.0 + published[name] / 100.0)
                                    for name in CONTEXT}
                scored.append(entry)
            else:
                entry["sigma"] = row["uncertainty_percent"] / 100.0
                diagnostics.append(entry)

    def view(points):
        result = {"ACTINV_TENDL-2025": stats(points)}
        for name in CONTEXT:
            covered = [dict(p, ce=p["context"][name]) for p in points if p["context"][name] is not None]
            result[name] = stats(covered)
        return result

    result = {
        "pooled": view(scored),
        "by_field": {field: view([p for p in scored if p["field"] == field]) for field in sorted(REGISTERED)},
        "points": [{k: p[k] for k in ("field", "reaction", "measured", "calculated", "ce", "sigma")} for p in scored],
        "diagnostics": diagnostics,
    }
    pooled = result["pooled"]
    actinv = pooled["ACTINV_TENDL-2025"]
    sentences = [
        f"Against {actinv['points']} measured reaction rates in two quasi-monoenergetic 7Li(p,n) neutron fields at "
        f"NPI Řež (12.4 and 14.4 MeV protons; Matěj et al., 2023), ACTINV with its TENDL-2025 library has a median "
        f"|ln C/E| of {actinv['median_abs_log_C_over_E']:.3f} and a geometric-mean C/E of "
        f"{actinv['geometric_mean_C_over_E']:.3f}.",
        f"On the same points the authors' calculations give a median |ln C/E| of "
        f"{pooled['ENDF/B-VIII']['median_abs_log_C_over_E']:.3f} with ENDF/B-VIII.0, "
        f"{pooled['JEFF-3.3']['median_abs_log_C_over_E']:.3f} with JEFF-3.3 and "
        f"{pooled['JENDL-4']['median_abs_log_C_over_E']:.3f} with JENDL-4, and "
        f"{pooled['IRDFF-II']['median_abs_log_C_over_E']:.3f} with IRDFF-II on the "
        f"{pooled['IRDFF-II']['points']} points it covers.",
        f"{actinv['within_2_sigma_count']} of the {actinv['points']} ACTINV values are within 2σ of the measurement.",
    ]
    return result, sentences


def evaluate(record, *, reextract=True, source_tree=None):
    gates = {"G0": g0(), "G1": g1(record, reextract=reextract, source_tree=source_tree), "G2": g2(record)}
    result, sentences = summary(record) if not gates["G2"] else ({}, [])
    gates["G3"] = [] if sentences else ["summary not computed (fidelity failed)"]
    return gates, result, sentences


def selftest():
    record = {"schema": SCHEMA, "inputs": {k: {"sha256": v} for k, v in p167_rez.PINNED.items()},
              "tables": {"sha256": TABLES_SHA256},
              "environment": {"binary_sha256": BINARY_SHA256, "crates_tree": "tree", "clean_tree": True,
                              "cgroup": {"memory.max": str(6 * GIB), "memory.swap.max": "0", "pids.max": "128",
                                         "cpu.max": "200000 100000"}},
              "fields": {}}
    for field, registered in REGISTERED.items():
        rows = []
        for reaction, status in registered["status"].items():
            published = {"IRDFF-II": 2.0, "JEFF-3.3": 10.0, "JENDL-4": -10.0, "ENDF/B-VIII": 5.0,
                         "uncertainty_percent": 5.0} if status == "scored" else None
            rows.append({"reaction": reaction, "status": status, "rate_per_s": 1e-18, "uncertainty_percent": 4.0,
                         "published": published, "irdff_rate": 1.02e-18 if published else None,
                         "actinv_rate": 1.04e-18, "independent_fold_rate": 1.04e-18,
                         "actinv_rate_equal_lethargy": 1.04e-18})
        record["fields"][field] = {"spectrum_bins": registered["bins"], "spectrum_range_MeV": registered["range"],
                                   "rates": rows}
    failures = []

    def run(label, mutate, gate):
        trial = copy.deepcopy(record)
        mutate(trial)
        gates, result, sentences = evaluate(trial, reextract=False, source_tree="tree")
        bad = [name for name in ("G1", "G2") if gates[name]]
        if gate is None and bad:
            failures.append(f"{label}: expected pass, failed {bad}: {[gates[b] for b in bad]}")
        if gate is not None and gate not in bad:
            failures.append(f"{label}: expected {gate} to fail")
        return result, sentences

    result, sentences = run("clean", lambda r: None, None)
    actinv = result["pooled"]["ACTINV_TENDL-2025"]
    if (actinv["points"] != 29 or abs(actinv["geometric_mean_C_over_E"] - 1.04) > 1e-12
            or abs(actinv["median_abs_log_C_over_E"] - math.log(1.04)) > 1e-12 or actinv["within_1_sigma"] != 1.0):
        failures.append(f"clean summary wrong: {actinv}")
    if abs(result["pooled"]["JENDL-4"]["geometric_mean_C_over_E"] - 0.9) > 1e-12:
        failures.append("context summary wrong")
    if len(result["diagnostics"]) != 5 or "29 ACTINV values are within 2σ" not in sentences[2]:
        failures.append(f"diagnostics or sentences wrong: {len(result['diagnostics'])}, {sentences[2]}")
    run("binary", lambda r: r["environment"].update(binary_sha256="0" * 64), "G1")
    run("library", lambda r: r["inputs"]["library"].update(sha256="0" * 64), "G1")
    run("cgroup", lambda r: r["environment"]["cgroup"].update({"pids.max": "max"}), "G1")
    run("dirty", lambda r: r["environment"].update(clean_tree=False), "G1")
    run("bins", lambda r: r["fields"]["12.4"].update(spectrum_bins=112), "G2")
    run("dropped", lambda r: r["fields"]["14.4"]["rates"].pop(0), "G2")
    run("status", lambda r: r["fields"]["14.4"]["rates"][16].update(status="scored"), "G2")
    run("irdff", lambda r: r["fields"]["12.4"]["rates"][0].update(irdff_rate=1.03e-18), "G2")
    run("fold", lambda r: r["fields"]["12.4"]["rates"][3].update(independent_fold_rate=1.05e-18), "G2")
    run("nan", lambda r: r["fields"]["14.4"]["rates"][2].update(actinv_rate=float("nan")), "G2")
    run("lethargy", lambda r: r["fields"]["14.4"]["rates"][5].update(actinv_rate_equal_lethargy=1.1e-18), "G2")
    one = stats([{"ce": 1.11, "sigma": 0.05}])
    if one["within_1_sigma"] != 0.0 or one["within_2_sigma"] != 0.0:
        failures.append(f"sigma bands wrong: {one}")
    print("selftest:", "PASS" if not failures else "FAIL")
    for failure in failures:
        print("  " + failure)
    return not failures


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--record", type=Path, default=RECORD)
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        sys.exit(0 if selftest() else 1)
    record = json.loads(args.record.read_text())
    gates, result, sentences = evaluate(record)
    passed = all(not problems for problems in gates.values())
    verdict = {
        "protocol": "ACTINV-P167",
        "verdict": "P167-PASS (pending G4, CI on the evidence commit)" if passed else "P167-FAIL",
        "gates": {name: {"pass": not problems, "problems": problems} for name, problems in gates.items()},
        "summary": result,
        "allowed_sentences": sentences,
    }
    for name, problems in gates.items():
        print(f"{name}: {'PASS' if not problems else 'FAIL'}")
        for problem in problems[:20]:
            print(f"  {problem}")
    if args.no_write:
        if VERDICT.is_file():
            recorded = json.loads(VERDICT.read_text())
            if recorded.get("summary") != json.loads(json.dumps(result)) or recorded.get(
                    "allowed_sentences") != sentences:
                print("G3: FAIL (verdict file differs from the recomputed summary)")
                passed = False
    else:
        VERDICT.write_text(json.dumps(verdict, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
    for sentence in sentences:
        print("  " + sentence)
    print(verdict["verdict"] if passed else "P167-FAIL")
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
