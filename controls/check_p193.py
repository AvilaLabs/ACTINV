#!/usr/bin/env python3
"""P193 checker: gates G0-G3 on results/p193_record.json; writes results/p193_verdict.json.

  G0  The SHA-256 of the protocol, controls/p193.py and this checker are in protocols/protocol_hash.txt.
  G1  Provenance: the table, the 23 spectra, the four libraries and their indexes, controls/p192.py (P192's sealed
      file) and the ACTINV binary match their pins; the record comes from the registration commit with a clean
      tracked tree; 365 rows; not a rehearsal.
  G2  Integrity: every row scored or excluded with a registered reason; every TENDL-2025 value equals P192's
      committed value within 1e-12 relative; each cross-checked value agrees with `actinv inspect xs` within 1e-9.
  G3  The measurement, always reported, never a gate: the leave-one-facility-out score of the fixed choice rule.
  G4  CI green on the evidence commit (external).

Verdict: P193-PASS iff G0, G1 and G2 pass (G4 external).
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "controls"))
import check_p192 as c192  # noqa: E402

PROTOCOL_REL = "protocols/ACTINV-P193_PROTOCOL.md"
HASHED = [PROTOCOL_REL, "controls/p193.py", "controls/check_p193.py"]
RECORD = ROOT / "results" / "p193_record.json"
VERDICT = ROOT / "results" / "p193_verdict.json"
P192_RECORD = ROOT / "results" / "p192_record.json"
SCHEMA = "actinv-p193-record-1"
LIBRARIES = ["tendl-2025", "tendl-2023", "tendl-2017", "endfb-viii.1"]
DEFAULT = "tendl-2025"
LIBRARY_SHA256 = {
    "tendl-2025": ("ec4c72bf598dc8ad3d533d9cfafdcf493e2d1f949a3e4db6251495659b68cc44",
                   "8bd19b4001c246758e739cd0067a0087e1ce5c2157438dae97bd52e1d3beb3fb"),
    "tendl-2023": ("a9f90234e42c538676de904c734510c4a62126017459e638ed338d052072e92c",
                   "9bd0910e65d57b1d80252b199b81784f0d1f2e5add88c137b453498f8f0be605"),
    "tendl-2017": ("218156708ae685db2a50cd7a2c74ca02049b68f266c38a29020b71095717447d",
                   "1f1124ab299c95aa0e0fb7caac52c47cb63ca56df688a7bdd9ab2dfecbc62a06"),
    "endfb-viii.1": ("ecaca7c072bd76b49e6724dd00aaa43172698f7c6a28e6551c551de39d11de32",
                     "ff59cccf0ca85e24542054fff035079baefaea79a213e41c94bde2bc647162fa"),
}
P192_HARNESS_REL = "controls/p192.py"
MARGIN = 0.05
CROSSCHECK_ROWS = 24
TOLERANCE_P192 = 1e-12
FACILITIES = ("JAEA-FNS", "FNG", "TUD")


def facility(spectrum):
    return next(f for f in FACILITIES if spectrum.startswith(f))


def g0(root=ROOT, log=None):
    saved = c192.HASHED, c192.PROTOCOL_REL
    c192.HASHED = HASHED
    try:
        return c192.g0(root, log)
    finally:
        c192.HASHED, c192.PROTOCOL_REL = saved


def registration_commit():
    saved = c192.PROTOCOL_REL
    c192.PROTOCOL_REL = PROTOCOL_REL
    try:
        return c192.registration_commit()
    finally:
        c192.PROTOCOL_REL = saved


def p192_harness_sealed(log=None):
    log = c192.HASH_LOG.read_text() if log is None else log
    path = ROOT / P192_HARNESS_REL
    return path.is_file() and f"{c192.sha256(path)}  {P192_HARNESS_REL}" in log


def g1(record, registration, p192_sealed=True):
    problems = []
    if record.get("schema") != SCHEMA:
        problems.append("wrong record schema")
    if record.get("rehearsal"):
        problems.append("a rehearsal record")
    inputs = record.get("inputs", {})
    if (inputs.get("table") or {}).get("sha256") != c192.TABLE_SHA256:
        problems.append("table SHA-256 differs from its pin")
    if (inputs.get("actinv_binary") or {}).get("sha256") != c192.ACTINV_BINARY_SHA256:
        problems.append("binary SHA-256 differs from its pin")
    libraries = inputs.get("libraries") or {}
    for name, (npz, index) in LIBRARY_SHA256.items():
        entry = libraries.get(name) or {}
        if entry.get("sha256") != npz or entry.get("index_sha256") != index:
            problems.append(f"library {name} differs from its pin")
    spectra = record.get("spectra", {})
    if {k: (v or {}).get("sha256") for k, v in spectra.items()} != c192.SPECTRUM_SHA256:
        problems.append("spectra differ from P192's pins")
    if not p192_sealed:
        problems.append("controls/p192.py is not P192's sealed file")
    env = record.get("environment", {})
    if registration is None:
        problems.append("no registration commit found")
    elif env.get("git_head") != registration:
        problems.append("record not made at the registration commit")
    if env.get("git_dirty_paths"):
        problems.append("tracked tree was dirty")
    if len(record.get("rows", [])) != c192.ROWS:
        problems.append(f"record has {len(record.get('rows', []))} rows, not {c192.ROWS}")
    return problems


def g2(record, p192_rows):
    problems = []
    rows = record.get("rows", [])
    reference = {row["row"]: row for row in p192_rows}
    for row in rows:
        status = row.get("status")
        if status not in c192.EXCLUSIONS and status != "scored":
            problems.append(f"row {row.get('row')} has unregistered status {status!r}")
            continue
        ref = reference.get(row.get("row"), {})
        if status != ref.get("status"):
            problems.append(f"row {row.get('row')} status differs from P192")
            continue
        if status != "scored":
            continue
        value = (row.get("values", {}).get(DEFAULT) or {}).get("C_b")
        if not isinstance(value, float) or abs(value - ref["C_actinv_b"]) > TOLERANCE_P192 * ref["C_actinv_b"]:
            problems.append(f"row {row.get('row')}: TENDL-2025 value differs from P192's")
        for name in LIBRARIES:
            c = (row.get("values", {}).get(name) or {}).get("C_b")
            if c is not None and not (math.isfinite(c) and c > 0):
                problems.append(f"row {row.get('row')}: {name} value not positive and finite")
    checks = (record.get("crosscheck") or {}).get("rows", [])
    if len(checks) != CROSSCHECK_ROWS:
        problems.append(f"{len(checks)} cross-checked values, not {CROSSCHECK_ROWS}")
    for check in checks:
        rel = check.get("relative_difference")
        if rel is None or not rel <= c192.CROSSCHECK_TOLERANCE:
            problems.append(f"{check.get('library')} row {check.get('row')}: actinv inspect xs disagrees ({rel})")
    return problems


def value(row, name):
    return (row["values"].get(name) or {}).get("C_b")


def choose(fit_rows, eligible_rows):
    """The fixed rule: the library with the smallest mean |ln C/E| on the fit rows, if it beats TENDL-2025 by more
    than MARGIN; only libraries with a value for every fit row and every row it will be applied to compete."""
    scores = {}
    for name in LIBRARIES:
        if all(value(row, name) for row in fit_rows + eligible_rows):
            scores[name] = sum(abs(math.log(value(row, name) / row["E_b"])) for row in fit_rows) / len(fit_rows)
    best = min(scores, key=lambda name: (scores[name], LIBRARIES.index(name)))
    return best if scores[DEFAULT] - scores[best] > MARGIN else DEFAULT


def lofo(rows):
    scored = [row for row in rows if row.get("status") == "scored"]
    held = []
    for fold in FACILITIES:
        fit = [row for row in scored if facility(row["spectrum"]) != fold]
        test = [row for row in scored if facility(row["spectrum"]) == fold]
        for label in sorted({row["reaction"] for row in test}):
            fit_rows = [row for row in fit if row["reaction"] == label]
            if not fit_rows:
                continue
            test_rows = [row for row in test if row["reaction"] == label]
            chosen = choose(fit_rows, test_rows)
            for row in test_rows:
                held.append({"row": row["row"], "fold": fold, "reaction": label, "chosen": chosen,
                             "E_b": row["E_b"], "E_unc_b": row["E_unc_b"], "C_selected_b": value(row, chosen),
                             "C_default_b": value(row, DEFAULT), "C_fispact_b": row["C_fispact_b"]})
    return held


def paired(rows, a, b):
    d = [abs(math.log(row[a] / row["E_b"])) - abs(math.log(row[b] / row["E_b"])) for row in rows]
    test = c192.wilcoxon(d)
    if test["p"] < c192.ALPHA and test["z"] < 0:
        label = "FIRST CLOSER"
    elif test["p"] < c192.ALPHA and test["z"] > 0:
        label = "SECOND CLOSER"
    else:
        label = "NO SIGNIFICANT DIFFERENCE"
    return {"median_difference": c192.median(d), "wilcoxon": test, "label": label}


def decision(record):
    rows = record.get("rows", [])
    held = lofo(rows)
    if not held:
        return {}
    summary = {key: c192.stats(held, key) for key in ("C_selected_b", "C_default_b", "C_fispact_b")}
    scored = [row for row in rows if row.get("status") == "scored"]
    final = {}
    for label in sorted({row["reaction"] for row in scored}):
        group = [row for row in scored if row["reaction"] == label]
        final[label] = choose(group, group)
    per_library = {}
    for name in LIBRARIES:
        have = [row for row in scored if value(row, name)]
        per_library[name] = {"rows": len(have), "median_abs_ln_CE": c192.median(
            [abs(math.log(value(row, name) / row["E_b"])) for row in have])}
    return {"held_out_rows": len(held),
            "held_out_per_fold": {f: sum(r["fold"] == f for r in held) for f in FACILITIES},
            "chosen_in_folds": {n: sum(r["chosen"] == n for r in held) for n in LIBRARIES},
            "summary": summary,
            "selected_vs_default": paired(held, "C_selected_b", "C_default_b"),
            "selected_vs_fispact": paired(held, "C_selected_b", "C_fispact_b"),
            "default_vs_fispact": paired(held, "C_default_b", "C_fispact_b"),
            "final_choice_counts": {n: sum(v == n for v in final.values()) for n in LIBRARIES},
            "final_choice": final, "per_library_all_rows": per_library, "held_out": held}


def sentences(dec):
    s = dec["summary"]
    vs_default = {"FIRST CLOSER": "the choice brought ACTINV closer to the measurements than TENDL-2025 alone",
                  "SECOND CLOSER": "the choice moved ACTINV further from the measurements than TENDL-2025 alone",
                  "NO SIGNIFICANT DIFFERENCE": "the choice did not change ACTINV's distance from the measurements "
                                               "significantly"}
    vs_fispact = {"FIRST CLOSER": "ACTINV was closer than FISPACT-II",
                  "SECOND CLOSER": "FISPACT-II was closer than ACTINV",
                  "NO SIGNIFICANT DIFFERENCE": "neither ACTINV nor FISPACT-II was significantly closer"}
    a, b = dec["selected_vs_default"], dec["selected_vs_fispact"]
    return [
        f"In a leave-one-facility-out test on the {dec['held_out_rows']} rows of UKAEA's integro-differential "
        f"validation table whose reaction was also measured at another facility, choosing each reaction's evaluation "
        f"among TENDL-2025, TENDL-2023, TENDL-2017 and ENDF/B-VIII.1 by a fixed rule fitted on the other facilities "
        f"gave ACTINV a median |ln(C/E)| of {s['C_selected_b']['median_abs_ln_CE']:.3f}, against "
        f"{s['C_default_b']['median_abs_ln_CE']:.3f} with TENDL-2025 alone and "
        f"{s['C_fispact_b']['median_abs_ln_CE']:.3f} for FISPACT-II's published TENDL-2017 values; "
        f"{vs_default[a['label']]} (p = {a['wilcoxon']['p']:.2g}), and {vs_fispact[b['label']]} "
        f"(p = {b['wilcoxon']['p']:.2g}).",
        "The table's measurements had been compared with ACTINV's TENDL-2025 values before this test (P192); in each "
        "fold the held-out facility played no part in the choice.",
    ]


def evaluate(record, p192_rows, registration=None, log=None, p192_sealed=None):
    gates = {"G0": g0(ROOT, log),
             "G1": g1(record, registration if registration is not None else registration_commit(),
                      p192_harness_sealed(log) if p192_sealed is None else p192_sealed),
             "G2": g2(record, p192_rows)}
    dec = decision(record)
    passed = all(not problems for problems in gates.values())
    return gates, dec, (sentences(dec) if passed and dec else [])


def synthetic():
    base = c192.synthetic_record()
    p192_rows = copy.deepcopy(base["rows"])
    facilities = ["JAEA-FNS-pos3", "FNG-Cr", "TUD-Y"]
    for row, ref in zip(base["rows"], p192_rows):
        row["spectrum"] = ref["spectrum"] = facilities[row["row"] % 3]
        row["reaction"] = ref["reaction"] = f"R{row['row'] // 3}"
        e = row["E_b"]
        row["C_actinv_b"] = ref["C_actinv_b"] = 1.2 * e
        row["values"] = {"tendl-2025": {"C_b": ref["C_actinv_b"]}, "tendl-2023": {"C_b": 1.0 * e},
                         "tendl-2017": {"C_b": 1.3 * e}, "endfb-viii.1": {}}
    base.update(schema=SCHEMA, inputs={
        "table": {"sha256": c192.TABLE_SHA256}, "actinv_binary": {"sha256": c192.ACTINV_BINARY_SHA256},
        "libraries": {n: {"sha256": a, "index_sha256": b} for n, (a, b) in LIBRARY_SHA256.items()}})
    base["crosscheck"] = {"rows": [{"library": "tendl-2023", "row": i, "relative_difference": 0.0}
                                   for i in range(CROSSCHECK_ROWS)]}
    return base, p192_rows


def selftest():
    failures = []
    base, p192_rows = synthetic()
    if g1(base, "reg") or g2(base, p192_rows):
        failures.append(f"clean synthetic record fails: {g1(base, 'reg')} {g2(base, p192_rows)[:3]}")
    dec = decision(base)
    if dec["chosen_in_folds"]["tendl-2023"] != dec["held_out_rows"]:
        failures.append(f"exact library not chosen everywhere: {dec['chosen_in_folds']}")
    if dec["selected_vs_default"]["label"] != "FIRST CLOSER":
        failures.append("a better library is not labelled as helping")
    if not sentences(dec):
        failures.append("no sentences")
    worse = copy.deepcopy(base)
    for row in worse["rows"]:
        row["values"]["tendl-2023"]["C_b"] = row["values"]["tendl-2025"]["C_b"] * 1.03
    if decision(worse)["chosen_in_folds"]["tendl-2025"] != decision(worse)["held_out_rows"]:
        failures.append("a library within the margin replaced the default")
    fit = [{"E_b": 1.0, "values": {"tendl-2025": {"C_b": 1.5}, "tendl-2023": {"C_b": 1.0},
                                   "tendl-2017": {}, "endfb-viii.1": {"C_b": 1.0}}}]
    if choose(fit, fit) != "tendl-2023":
        failures.append("tie not broken by library order")
    missing = [{"E_b": 1.0, "values": {"tendl-2025": {"C_b": 1.5}, "tendl-2023": {"C_b": 1.0},
                                       "tendl-2017": {}, "endfb-viii.1": {}}}]
    applied = [{"E_b": 1.0, "values": {"tendl-2025": {"C_b": 1.5}, "tendl-2023": {},
                                       "tendl-2017": {}, "endfb-viii.1": {}}}]
    if choose(missing, applied) != "tendl-2025":
        failures.append("a library missing the applied row was chosen")

    def broken(name, mutate, gate):
        rec, ref = copy.deepcopy(base), copy.deepcopy(p192_rows)
        registration = mutate(rec, ref) or "reg"
        found = {"G1": g1(rec, registration), "G2": g2(rec, ref)}
        if not found[gate]:
            failures.append(f"not caught by {gate}: {name}")

    broken("rehearsal", lambda r, p: r.update(rehearsal=True), "G1")
    broken("other library", lambda r, p: r["inputs"]["libraries"]["tendl-2017"].update(sha256="x"), "G1")
    broken("other index", lambda r, p: r["inputs"]["libraries"]["tendl-2023"].update(index_sha256="x"), "G1")
    broken("other binary", lambda r, p: r["inputs"]["actinv_binary"].update(sha256="x"), "G1")
    broken("other spectrum", lambda r, p: r["spectra"]["FNG-Cr"].update(sha256="x"), "G1")
    broken("other commit", lambda r, p: r["environment"].update(git_head="x"), "G1")
    broken("dirty tree", lambda r, p: r["environment"].update(git_dirty_paths=" M x"), "G1")
    broken("default drifted", lambda r, p: r["rows"][4]["values"]["tendl-2025"].update(C_b=r["rows"][4]["values"]
                                                                                       ["tendl-2025"]["C_b"] * 1.01),
           "G2")
    broken("status drifted", lambda r, p: p[7].update(status="reaction_unavailable"), "G2")
    broken("negative candidate", lambda r, p: r["rows"][2]["values"]["tendl-2023"].update(C_b=-1.0), "G2")
    broken("crosscheck off", lambda r, p: r["crosscheck"]["rows"][1].update(relative_difference=1e-6), "G2")
    broken("crosscheck missing", lambda r, p: r["crosscheck"]["rows"].pop(), "G2")
    if not g1(base, "reg", p192_sealed=False):
        failures.append("not caught by G1: edited P192 harness")
    log = "\n".join(f"{c192.sha256(ROOT / rel)}  {rel}" for rel in HASHED if (ROOT / rel).is_file())
    if g0(ROOT, log):
        failures.append("G0 fails on a sealed set")
    if not g0(ROOT, log.replace("controls/check_p193.py", "controls/other.py")):
        failures.append("G0 misses an unsealed checker")
    for failure in failures:
        print(f"selftest: {failure}")
    print("selftest: " + ("PASS" if not failures else "FAIL"))
    return not failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--record", type=Path, default=RECORD)
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--gates-only", action="store_true", help="print gate statuses and counts only")
    args = parser.parse_args()
    if args.selftest:
        return 0 if selftest() else 1
    record = json.loads(args.record.read_text())
    p192_rows = json.loads(P192_RECORD.read_text())["rows"]
    gates, dec, sent = evaluate(record, p192_rows)
    passed = all(not problems for problems in gates.values())
    for name, problems in gates.items():
        print(f"{name}: {'PASS' if not problems else 'FAIL'}")
        if not args.gates_only:
            for problem in problems[:20]:
                print(f"  {problem}")
    if args.gates_only:
        print(f"held-out rows {dec.get('held_out_rows')} per fold {dec.get('held_out_per_fold')}")
        return 0 if passed else 1
    verdict = {
        "protocol": "ACTINV-P193",
        "verdict": "P193-PASS (pending G4, CI on the evidence commit)" if passed else "P193-FAIL",
        "gates": {**{name: {"pass": not problems, "problems": problems} for name, problems in gates.items()},
                  "G4": {"pass": None, "problems": ["CI on the evidence commit, checked after the push"]}},
        "decision": dec,
        "allowed_sentences": sent,
    }
    if dec:
        s = dec["summary"]
        print(f"G3: held out {dec['held_out_rows']}; median |ln C/E| selected "
              f"{s['C_selected_b']['median_abs_ln_CE']:.3f}, TENDL-2025 {s['C_default_b']['median_abs_ln_CE']:.3f}, "
              f"FISPACT-II {s['C_fispact_b']['median_abs_ln_CE']:.3f}; selected vs default "
              f"{dec['selected_vs_default']['label']} (p = {dec['selected_vs_default']['wilcoxon']['p']:.2g}); "
              f"selected vs FISPACT-II {dec['selected_vs_fispact']['label']} "
              f"(p = {dec['selected_vs_fispact']['wilcoxon']['p']:.2g})")
    if args.no_write:
        if VERDICT.is_file():
            recorded = json.loads(VERDICT.read_text())
            if recorded.get("decision") != json.loads(json.dumps(dec)) or recorded.get("allowed_sentences") != sent:
                print("verdict file differs from the recomputed decision")
                passed = False
    elif args.record.resolve() == RECORD.resolve():
        VERDICT.write_text(json.dumps(verdict, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
    for sentence in sent:
        print(f"  {sentence}")
    print(verdict["verdict"] if passed else "P193-FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
