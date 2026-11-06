#!/usr/bin/env python3
"""P192 checker: gates G0-G3 on results/p192_record.json; writes results/p192_verdict.json.

  G0  The SHA-256 of the protocol, controls/p192.py and this checker are in protocols/protocol_hash.txt.
  G1  Provenance: the table, the 23 spectra, the TENDL-2025 library and index, and the ACTINV binary match their pins;
      the record comes from the registration commit with a clean tracked tree; 365 rows; not a rehearsal.
  G2  Integrity: every row scored or excluded with a registered reason; at least 80% of rows scored; each of the 24
      seeded rows re-collapsed by `actinv inspect xs` agrees with the harness within 1e-9 relative.
  G3  The measurement, always reported, never a gate: per-code median |ln C/E|, geometric-mean C/E, share within
      twice the experimental uncertainty, root-mean-square z; the paired Wilcoxon signed-rank comparison of
      |ln C/E| (ACTINV against FISPACT-II's published values) on all scored rows and on the FNG and TU Dresden rows.
  G4  CI green on the evidence commit (external).

Verdict: P192-PASS iff G0, G1 and G2 pass (G4 external). The allowed sentences are filled from the record.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROTOCOL_REL = "protocols/ACTINV-P192_PROTOCOL.md"
HASHED = [PROTOCOL_REL, "controls/p192.py", "controls/check_p192.py"]
HASH_LOG = ROOT / "protocols" / "protocol_hash.txt"
RECORD = ROOT / "results" / "p192_record.json"
VERDICT = ROOT / "results" / "p192_verdict.json"
SCHEMA = "actinv-p192-record-1"
ROWS = 365
MIN_SCORED_FRACTION = 0.80
CROSSCHECK_ROWS = 24
CROSSCHECK_TOLERANCE = 1e-9
ALPHA = 0.05
TABLE_SHA256 = "2ec3f7f24f40d8cd171b80273f879e339825bf3c028eaebf2017cb735fb64238"
LIBRARY_SHA256 = "ec4c72bf598dc8ad3d533d9cfafdcf493e2d1f949a3e4db6251495659b68cc44"
INDEX_SHA256 = "8bd19b4001c246758e739cd0067a0087e1ce5c2157438dae97bd52e1d3beb3fb"
ACTINV_BINARY_SHA256 = "fa21795bd0137d8870f435d2618ceadd1493bec54e05f6b526eb8978151c8662"  # built from df1d494's sources
SPECTRUM_SHA256 = {
    "FNG-Cr": "b8961128740993aa6bdcc1fd1147a8cd613ba2f1ad7edd9d487f795032156292",
    "FNG-CuCrZr": "dc0ed20e03e95e49336406adf11329e910c3e5288665781fc34816571be544bc",
    "FNG-Dy": "85ccd64a67a8e327b8c5bc4c878205720badb1df13535276a71200e186a6b864",
    "FNG-Mo": "44356d32f04fab56112f42a17bf21625ca4186ed574c0f34e353d9c96957db88",
    "FNG-Re": "2a41561b4d1687d51c8544521314de579c1af88a97e3a7bf236dd352b1a6f175",
    "FNG-ScSmGd": "904e8ecd1fb04ea3ad5c4cf45767cfd7f063e143fb56e349aeb3019188d492e4",
    "FNG-SiC": "4d1a2273c400cf6e8aee7fa72d2ec0a89cbf015db2fa5bb6889e70e235e73bf4",
    "FNG-Sn": "08375e543ab2e30bbbfd1bf2839e5777eb8a8b6fb089236e2d46f3d4bfa44574",
    "FNG-Ta": "2b041c672ec37c352921505795cf3aca369643f94f0eda3c30fd260c5d40d29c",
    "FNG-Y": "4c907ddb76aa88926c92f7ab49ef587d9e5f147dc0e6ffe50483415a6e24d7d4",
    "FNG-eurofer": "47cd3187ad1bbbf71d0f828bc07f36625eaaf60ba39fb25eab5edf5773db8df2",
    "FNG-f82h": "0080351435fb9172250b15ff02465de33e3ff5c9a7af510cc16687491c481947",
    "FNG-hafnium": "1c6eab917318e794d723785664cb23acff963219ecbfa440894303a04295152e",
    "FNG-heat": "0b72e2f9b4ac4fceb73ef5abe1722fdafd4f6d72b41386c4e44c09bf98e1b3b3",
    "FNG-tung": "d4da39d83b2d08bee064aaae39ac2fee9e0ca3eaf69fe7782cae397e2adaf50b",
    "FNG-vanad": "43bb59c99be15ea93c16b28434075a034656438c58b455c33da66ede1fee558a",
    "JAEA-FNS-pos3": "f0537e41f88ac79f58fb1e867fab1d1b8d040b7b1fe180ba0d0780e21e266d29",
    "JAEA-FNS-pos7": "e367f6eda5cd1a2f521a8779e351475b9d03199db6762fd2b9c0a104a980eb25",
    "TUD-CuCrZr": "a0e00f8505fd9ce20a19444628f5131c1276badf4d66d00ab2fb060118565e1f",
    "TUD-Er": "98fd4fb0e5b0c885deff4eee170015db0fbf156a4cf0863790b7db9c508cbdc9",
    "TUD-Pb": "d06b76196eb7b36de2d30098970111865e61c500f32a7637373db7c2f5ec7ada",
    "TUD-Ta": "f547f972a12b7b99474134c9a28508911d9317282416ded24de09128a399850c",
    "TUD-Y": "8d97a4d51654dc7218cbd169e86655755b6d773a4eb858c8e6f7662e673afed5",
}
EXCLUSIONS = {"label_unparsed", "label_inconsistent", "target_unavailable", "reaction_unavailable",
              "state_unavailable", "nonpositive_calculated_value", "nonpositive_experimental_value"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def g0(root=ROOT, log=None):
    log = HASH_LOG.read_text() if log is None else log
    problems = []
    for rel in HASHED:
        path = Path(root) / rel
        if not path.is_file():
            problems.append(f"missing {rel}")
        elif f"{sha256(path)}  {rel}" not in log:
            problems.append(f"{rel} hash not in protocol_hash.txt")
    return problems


def registration_commit():
    out = subprocess.run(["git", "log", "--diff-filter=A", "--format=%H", "--", PROTOCOL_REL], capture_output=True,
                         text=True, cwd=ROOT).stdout.split()
    return out[-1] if out else None  # git log lists newest first


def g1(record, registration):
    problems = []
    if record.get("schema") != SCHEMA:
        problems.append("wrong record schema")
    if record.get("rehearsal"):
        problems.append("a rehearsal record")
    inputs = record.get("inputs", {})
    for key, pin in (("table", TABLE_SHA256), ("library", LIBRARY_SHA256), ("index", INDEX_SHA256),
                     ("actinv_binary", ACTINV_BINARY_SHA256)):
        if (inputs.get(key) or {}).get("sha256") != pin:
            problems.append(f"{key} SHA-256 differs from its pin")
    spectra = record.get("spectra", {})
    if set(spectra) != set(SPECTRUM_SHA256):
        problems.append("the record's spectra are not the 23 pinned ones")
    for name, pin in SPECTRUM_SHA256.items():
        if (spectra.get(name) or {}).get("sha256") != pin:
            problems.append(f"spectrum {name} SHA-256 differs from its pin")
    env = record.get("environment", {})
    if registration is None:
        problems.append("no registration commit found")
    elif env.get("git_head") != registration:
        problems.append("record not made at the registration commit")
    if env.get("git_dirty_paths"):
        problems.append("tracked tree was dirty")
    if len(record.get("rows", [])) != ROWS:
        problems.append(f"record has {len(record.get('rows', []))} rows, not {ROWS}")
    return problems


def g2(record):
    problems = []
    rows = record.get("rows", [])
    for row in rows:
        status = row.get("status")
        if status == "scored":
            if not all(isinstance(row.get(k), (int, float)) and math.isfinite(row[k]) and row[k] > 0
                       for k in ("C_actinv_b", "C_actinv_equal_energy_b", "E_b", "C_fispact_b")):
                problems.append(f"row {row.get('row')} scored without positive finite values")
        elif status not in EXCLUSIONS:
            problems.append(f"row {row.get('row')} has unregistered status {status!r}")
    scored = sum(row.get("status") == "scored" for row in rows)
    if not rows or scored < MIN_SCORED_FRACTION * len(rows):
        problems.append(f"{scored} of {len(rows)} rows scored, below {MIN_SCORED_FRACTION:.0%}")
    checks = (record.get("crosscheck") or {}).get("rows", [])
    if len(checks) != CROSSCHECK_ROWS:
        problems.append(f"{len(checks)} cross-checked rows, not {CROSSCHECK_ROWS}")
    for check in checks:
        rel = check.get("relative_difference")
        if rel is None or not rel <= CROSSCHECK_TOLERANCE:
            problems.append(f"row {check.get('row')}: actinv inspect xs disagrees with the harness ({rel})")
    return problems


def median(values):
    s = sorted(values)
    n = len(s)
    return float("nan") if n == 0 else (s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2]))


def wilcoxon(differences):
    """Two-sided Wilcoxon signed-rank test, zeros dropped, normal approximation with tie correction."""
    d = [x for x in differences if x != 0.0]
    n = len(d)
    if n == 0:
        return {"n": 0, "W_plus": 0.0, "z": 0.0, "p": 1.0}
    order = sorted(range(n), key=lambda i: abs(d[i]))
    ranks = [0.0] * n
    ties = 0.0
    i = 0
    while i < n:
        j = i
        while j + 1 < n and abs(d[order[j + 1]]) == abs(d[order[i]]):
            j += 1
        rank = 0.5 * (i + j) + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = rank
        t = j - i + 1
        ties += t ** 3 - t
        i = j + 1
    w_plus = sum(r for r, x in zip(ranks, d) if x > 0)
    mean = n * (n + 1) / 4.0
    var = n * (n + 1) * (2 * n + 1) / 24.0 - ties / 48.0
    z = (w_plus - mean) / math.sqrt(var) if var > 0 else 0.0
    p = math.erfc(abs(z) / math.sqrt(2.0))
    return {"n": n, "W_plus": w_plus, "z": z, "p": p}


def stats(rows, key):
    r = [math.log(row[key] / row["E_b"]) for row in rows]
    z = [(row[key] - row["E_b"]) / row["E_unc_b"] for row in rows if row["E_unc_b"] > 0]
    within = [abs(row[key] - row["E_b"]) <= 2.0 * row["E_unc_b"] for row in rows]
    return {"n": len(rows), "median_abs_ln_CE": median([abs(x) for x in r]),
            "geometric_mean_CE": math.exp(sum(r) / len(r)) if r else float("nan"),
            "within_2u_fraction": sum(within) / len(within) if within else float("nan"),
            "rms_z": math.sqrt(sum(x * x for x in z) / len(z)) if z else float("nan")}


def compare(rows, key="C_actinv_b"):
    d = [abs(math.log(row[key] / row["E_b"])) - abs(math.log(row["C_fispact_b"] / row["E_b"])) for row in rows]
    test = wilcoxon(d)
    med = median(d)
    if test["p"] < ALPHA and med < 0:
        label = "ACTINV CLOSER"
    elif test["p"] < ALPHA and med > 0:
        label = "FISPACT-II CLOSER"
    else:
        label = "NO SIGNIFICANT DIFFERENCE"
    return {"median_paired_difference": med, "wilcoxon": test, "label": label,
            "actinv": stats(rows, key), "fispact": stats(rows, "C_fispact_b")}


def decision(record):
    rows = [row for row in record.get("rows", []) if row.get("status") == "scored"]
    if not rows:
        return {}
    held = [row for row in rows if not row["spectrum"].startswith("JAEA-FNS")]
    by_facility = {}
    for prefix in ("FNG", "TUD", "JAEA-FNS"):
        subset = [row for row in rows if row["spectrum"].startswith(prefix)]
        if subset:
            by_facility[prefix] = compare(subset)
    return {"rows_total": len(record["rows"]), "rows_scored": len(rows),
            "excluded": {s: sum(r.get("status") == s for r in record["rows"]) for s in sorted(EXCLUSIONS)},
            "all": compare(rows), "fng_tud": compare(held), "by_facility": by_facility,
            "equal_energy_sensitivity": compare(rows, "C_actinv_equal_energy_b")}


def sentences(dec):
    a, h = dec["all"], dec["fng_tud"]
    clause = {"ACTINV CLOSER": "ACTINV's values were closer to the measurements",
              "FISPACT-II CLOSER": "FISPACT-II's values were closer to the measurements",
              "NO SIGNIFICANT DIFFERENCE": "neither code's values were significantly closer to the measurements"}
    return [
        f"On {dec['rows_scored']} of the 365 measured effective cross sections in UKAEA's integro-differential "
        f"validation table distributed by the IAEA's CoNDERC (FNG, TU Dresden and JAEA-FNS fusion spectra), ACTINV "
        f"with its default TENDL-2025 library had a median |ln(C/E)| of {a['actinv']['median_abs_ln_CE']:.3f} against "
        f"{a['fispact']['median_abs_ln_CE']:.3f} for FISPACT-II with TENDL-2017 as published in the same table; "
        f"{clause[a['label']]} (paired Wilcoxon signed-rank test, two-sided p = {a['wilcoxon']['p']:.2g}).",
        f"Within twice the experimental uncertainty were {100 * a['actinv']['within_2u_fraction']:.0f}% of ACTINV's "
        f"values and {100 * a['fispact']['within_2u_fraction']:.0f}% of FISPACT-II's; on the {h['actinv']['n']} FNG "
        f"and TU Dresden values alone, {clause[h['label']]} (p = {h['wilcoxon']['p']:.2g}).",
        "The calculated values are spectrum-averaged cross sections, so the comparison measures the nuclear data each "
        "code used (TENDL-2025 against TENDL-2017) more than the codes themselves.",
    ]


def evaluate(record, registration=None, root=ROOT, log=None):
    gates = {"G0": g0(root, log), "G1": g1(record, registration if registration is not None
                                           else registration_commit()), "G2": g2(record)}
    dec = decision(record)
    passed = all(not problems for problems in gates.values())
    return gates, dec, (sentences(dec) if passed and dec else [])


def synthetic_record():
    rows = []
    spectra = sorted(SPECTRUM_SHA256)
    for i in range(ROWS):
        e = 0.01 * (1 + i % 7)
        rows.append({"row": i, "reaction": "Fe-56(n,p)Mn-56", "spectrum": spectra[i % len(spectra)], "E_b": e,
                     "E_unc_b": 0.05 * e, "CE_fispact_table": 1.1, "C_fispact_b": 1.1 * e, "status": "scored",
                     "C_actinv_b": (1.02 + 0.001 * (i % 5)) * e, "C_actinv_equal_energy_b": 1.03 * e})
    return {"schema": SCHEMA, "rehearsal": False,
            "inputs": {"table": {"sha256": TABLE_SHA256}, "library": {"sha256": LIBRARY_SHA256},
                       "index": {"sha256": INDEX_SHA256}, "actinv_binary": {"sha256": ACTINV_BINARY_SHA256}},
            "environment": {"git_head": "reg", "git_dirty_paths": ""},
            "spectra": {name: {"sha256": pin} for name, pin in SPECTRUM_SHA256.items()},
            "crosscheck": {"rows": [{"row": i, "relative_difference": 1e-15} for i in range(CROSSCHECK_ROWS)]},
            "rows": rows}


def selftest():
    failures = []
    base = synthetic_record()
    gates = {"G1": g1(base, "reg"), "G2": g2(base)}
    if any(gates.values()):
        failures.append(f"clean synthetic record fails: {gates}")
    dec = decision(base)
    if dec["all"]["label"] != "ACTINV CLOSER":
        failures.append(f"clear ACTINV gain labelled {dec['all']['label']}")
    if not sentences(dec):
        failures.append("no sentences on a clean record")
    flipped = copy.deepcopy(base)
    for row in flipped["rows"]:
        row["C_actinv_b"], row["C_fispact_b"] = row["C_fispact_b"], row["C_actinv_b"]
    if decision(flipped)["all"]["label"] != "FISPACT-II CLOSER":
        failures.append("clear FISPACT-II gain not labelled")
    same = copy.deepcopy(base)
    for row in same["rows"]:
        row["C_actinv_b"] = row["C_fispact_b"]
    if decision(same)["all"]["label"] != "NO SIGNIFICANT DIFFERENCE":
        failures.append("identical values not labelled NO SIGNIFICANT DIFFERENCE")
    test = wilcoxon([1.0, 2.0, -3.0, 4.0, 5.0, 6.0, -7.0, 8.0, 9.0, 10.0])
    if not (abs(test["W_plus"] - 45.0) < 1e-12 and abs(test["p"] - 0.0745) < 0.002):
        failures.append(f"wilcoxon arithmetic wrong: {test}")

    def broken(name, mutate, gate):
        rec = copy.deepcopy(base)
        registration = mutate(rec) or "reg"
        found = {"G1": g1(rec, registration), "G2": g2(rec)}
        if not found[gate]:
            failures.append(f"not caught by {gate}: {name}")

    broken("rehearsal", lambda r: r.update(rehearsal=True), "G1")
    broken("other table", lambda r: r["inputs"]["table"].update(sha256="x"), "G1")
    broken("other library", lambda r: r["inputs"]["library"].update(sha256="x"), "G1")
    broken("other binary", lambda r: r["inputs"]["actinv_binary"].update(sha256="x"), "G1")
    broken("other spectrum", lambda r: r["spectra"]["FNG-Cr"].update(sha256="x"), "G1")
    broken("missing spectrum", lambda r: r["spectra"].pop("TUD-Y"), "G1")
    broken("other commit", lambda r: r["environment"].update(git_head="x"), "G1")
    broken("dirty tree", lambda r: r["environment"].update(git_dirty_paths=" M x"), "G1")
    broken("short table", lambda r: r["rows"].pop(), "G1")
    broken("crosscheck off", lambda r: r["crosscheck"]["rows"][3].update(relative_difference=1e-6), "G2")
    broken("crosscheck missing", lambda r: r["crosscheck"]["rows"].pop(), "G2")
    broken("crosscheck failed", lambda r: r["crosscheck"]["rows"][0].update(relative_difference=None), "G2")
    broken("unknown status", lambda r: r["rows"][5].update(status="dropped"), "G2")
    broken("negative value", lambda r: r["rows"][5].update(C_actinv_b=-1.0), "G2")
    broken("low coverage", lambda r: [row.update(status="reaction_unavailable") for row in r["rows"][:80]], "G2")
    log = "\n".join(f"{sha256(ROOT / rel)}  {rel}" for rel in HASHED if (ROOT / rel).is_file())
    if g0(ROOT, log):
        failures.append("G0 fails on a sealed set")
    if not g0(ROOT, log.replace("controls/check_p192.py", "controls/other.py")):
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
    gates, dec, sent = evaluate(record)
    passed = all(not problems for problems in gates.values())
    for name, problems in gates.items():
        print(f"{name}: {'PASS' if not problems else 'FAIL'}")
        if not args.gates_only:
            for problem in problems[:20]:
                print(f"  {problem}")
    if args.gates_only:
        print(f"rows scored {dec.get('rows_scored')} of {dec.get('rows_total')}; excluded {dec.get('excluded')}")
        return 0 if passed else 1
    verdict = {
        "protocol": "ACTINV-P192",
        "verdict": "P192-PASS (pending G4, CI on the evidence commit)" if passed else "P192-FAIL",
        "gates": {**{name: {"pass": not problems, "problems": problems} for name, problems in gates.items()},
                  "G4": {"pass": None, "problems": ["CI on the evidence commit, checked after the push"]}},
        "decision": dec,
        "allowed_sentences": sent,
    }
    if dec:
        a = dec["all"]
        print(f"G3: median |ln C/E| ACTINV {a['actinv']['median_abs_ln_CE']:.3f}, FISPACT-II "
              f"{a['fispact']['median_abs_ln_CE']:.3f}; {a['label']} (p = {a['wilcoxon']['p']:.2g}); FNG+TUD "
              f"{dec['fng_tud']['label']}")
    if args.no_write:
        if VERDICT.is_file():
            recorded = json.loads(VERDICT.read_text())
            if (recorded.get("decision") != json.loads(json.dumps(dec))
                    or recorded.get("allowed_sentences") != sent):
                print("verdict file differs from the recomputed decision")
                passed = False
    elif args.record.resolve() == RECORD.resolve():
        VERDICT.write_text(json.dumps(verdict, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
    for sentence in sent:
        print(f"  {sentence}")
    print(verdict["verdict"] if passed else "P192-FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
