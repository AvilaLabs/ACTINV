#!/usr/bin/env python3
"""P144: waste classification in the desktop and the browser workbench. Runs gates V1 and V2 on the specs and results
the desktop page saved, and `verdict` derives the disposition from the gate records (V3-V5 are written by the
coordinator as results/p144/v3.json, v4.json and v5.json, each with a boolean `pass`) and the CI runs of the pushed
evidence commit. Launch it inside the required systemd cgroup; the child runs serially.

    python3 controls/check_p144.py v1 PAGE_SPEC.json PAGE_RESULT.json
    python3 controls/check_p144.py v2 PAGE_SPEC.json[,PAGE_SPEC2.json] DECAY_FILE [FALLBACK_DECAY_FILE]
    python3 controls/check_p144.py verdict CI_RUNS.json
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from p144_decay_reader import alpha_emitting, read, z

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results/p144"
PROTOCOL = ROOT / "protocols/ACTINV-P144_PROTOCOL.md"
BINARY = Path(os.environ.get("ACTINV_BIN", ROOT / "target/release/actinv"))
SCRATCH = ROOT / "target/p144"

SYMBOLS = ("H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Br Kr Rb Sr Y "
           "Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re "
           "Os Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr Rf Db Sg Bh Hs Mt Ds "
           "Rg Cn Nh Fl Mc Lv Ts Og").split()
assert len(SYMBOLS) == 118
Z_OF = {symbol.upper(): number for number, symbol in enumerate(SYMBOLS, start=1)}
NAME = re.compile(r"^([A-Za-z]{1,2})-?(\d+)(?:[mM](\d*))?$")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def write(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")


def parse_name(name: str) -> tuple[int, int]:
    """`Co60` -> (27060, 0); `Am242m` or `Am242m1` -> (95242, 1); `Am242m2` -> (95242, 2)."""
    found = NAME.match(name)
    if not found or found.group(1).upper() not in Z_OF:
        raise ValueError(f"unrecognised nuclide name {name!r}")
    symbol, mass, state = found.groups()
    liso = 0 if state is None else int(state or 1)
    return Z_OF[symbol.upper()] * 1000 + int(mass), liso


def find_properties(spec: dict) -> dict:
    """`nuclide_properties` of a waste spec, or of the one embedded in an intrusion-screen or twin document."""
    for holder in (spec, spec.get("waste_spec"), spec.get("waste")):
        if isinstance(holder, dict) and isinstance(holder.get("nuclide_properties"), dict):
            return holder["nuclide_properties"]
    raise ValueError("no nuclide_properties in the spec")


def v2(spec_paths: str, primary: str, fallback: str | None) -> int:
    # ACTINV's run reads the primary file and adds, from the fallback, only the (za, liso) keys the primary lacks.
    tables = [("primary", read(primary))] + ([("fallback", read(fallback))] if fallback else [])
    rows, mismatches = [], []
    for spec_path in (Path(p) for p in spec_paths.split(",")):
        properties = find_properties(json.loads(spec_path.read_text()))
        for name in sorted(properties):
            page = properties[name]
            za, liso = parse_name(name)
            source, record = next(((label, table[(za, liso)]) for label, table in tables if (za, liso) in table),
                                  (None, None))
            row = {"spec": spec_path.name, "nuclide": name, "za": za, "liso": liso, "source": source,
                   "page": {key: page.get(key) for key in ("z", "half_life_s", "alpha_emitting")}}
            if record is None:
                row["reader"] = None
                mismatches.append({"spec": spec_path.name, "nuclide": name, "field": "decay_record", "page": None,
                                   "reader": "absent from the decay files"})
            else:
                row["reader"] = {"z": z(za), "half_life_s": record["half_life_s"],
                                 "alpha_emitting": alpha_emitting(record["modes"]), "modes": record["modes"]}
                for field in ("z", "alpha_emitting"):
                    if page.get(field) != row["reader"][field]:
                        mismatches.append({"spec": spec_path.name, "nuclide": name, "field": field,
                                           "page": page.get(field), "reader": row["reader"][field]})
                # Both parse the same ENDF text, so the half-lives must be the same double, bit for bit.
                theirs = row["reader"]["half_life_s"]
                if not (math.isfinite(theirs) and page.get("half_life_s") == theirs):
                    mismatches.append({"spec": spec_path.name, "nuclide": name, "field": "half_life_s",
                                       "page": repr(page.get("half_life_s")), "reader": repr(theirs)})
            rows.append(row)
    count = lambda label: sum(1 for r in rows if r["source"] == label)
    inputs = {str(p): sha(Path(p)) for p in (*spec_paths.split(","), primary, *([fallback] if fallback else []))}
    report = {"schema": "actinv-p144-v2-1", "inputs_sha256": inputs, "tolerance": {"half_life_relative": 0},
              "counts": {"nuclides": len(rows), "from_primary": count("primary"), "from_fallback": count("fallback"),
                         "absent": count(None), "mismatches": len(mismatches)},
              "mismatches": mismatches, "rows": rows}
    report["pass"] = bool(rows) and not mismatches
    # Infinity (a stable nuclide) is not JSON; a stable nuclide in the page's properties is a mismatch already.
    write(OUT / "v2.json", json.loads(json.dumps(report, default=str).replace("Infinity", '"inf"')))
    print(json.dumps({"v2_pass": report["pass"], **report["counts"]}))
    return 0 if report["pass"] else 1


def v1(spec_path: str, result_path: str) -> int:
    spec, expected = Path(spec_path).resolve(), Path(result_path).resolve()
    SCRATCH.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f"v1_{spec.parent.name}_", dir=SCRATCH))
    out = work / "waste-result.json"
    try:
        child = subprocess.run([str(BINARY), "waste", str(spec), str(out)], cwd=ROOT, capture_output=True, text=True,
                               timeout=600)
        code, stderr = child.returncode, child.stderr[-2000:]
    except subprocess.TimeoutExpired:
        code, stderr = None, "timeout after 600 s"
    produced = out.read_bytes() if out.exists() else None
    saved = expected.read_bytes()
    report = {"schema": "actinv-p144-v1-1", "commit": git("rev-parse", "HEAD"), "binary": str(BINARY),
              "binary_sha256": sha(BINARY), "spec": str(spec), "spec_sha256": sha(spec),
              "argv": ["actinv", "waste", spec.name, "OUT"], "exit": code, "stderr_tail": stderr,
              "out": str(out.relative_to(ROOT)), "out_sha256": hashlib.sha256(produced).hexdigest() if produced else None,
              "page_result": str(expected), "page_result_sha256": hashlib.sha256(saved).hexdigest(),
              "bytes": {"out": len(produced) if produced is not None else None, "page_result": len(saved)}}
    report["pass"] = code == 0 and produced is not None and produced == saved
    # One record per case folder: the page always saves its spec as waste-spec.json.
    write(OUT / f"v1_{spec.parent.name}.json", report)
    print(json.dumps({"v1_pass": report["pass"], "spec": spec.name, "exit": code}))
    return 0 if report["pass"] else 1


def verdict(ci_path: str) -> int:
    v1_records = [json.loads(p.read_text()) for p in sorted(OUT.glob("v1_*.json"))]
    gate_pass = {n: json.loads((OUT / f"{n}.json").read_text()).get("pass") is True for n in ("v2", "v3", "v4", "v5")}
    ci = json.loads(Path(ci_path).read_text())
    runs = ci.get("runs", [])
    names = {r.get("name") for r in runs}
    ci_green = bool(runs) and all(r.get("conclusion") == "success" for r in runs) and {
        "controls", "desktop builds", "Build browser workbench"} <= names
    gates = {"V1": len(v1_records) >= 2 and all(r.get("pass") is True for r in v1_records), "V2": gate_pass["v2"],
             "V3": gate_pass["v3"], "V4": gate_pass["v4"], "V5": gate_pass["v5"] and ci_green}
    record = {"schema": "actinv-p144-verdict-1", "protocol_sha256": sha(PROTOCOL), "commit": git("rev-parse", "HEAD"),
              "gates": gates, "v1_specs": [r.get("spec") for r in v1_records], "ci_green": ci_green, "ci": ci,
              "verdict": "P144-PASS" if all(gates.values()) else "P144-FAIL"}
    write(ROOT / "results/p144_verdict.json", record)
    print(json.dumps({"verdict": record["verdict"], "gates": gates}))
    return 0 if record["verdict"] == "P144-PASS" else 1


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["v1"] and len(args) == 3:
        raise SystemExit(v1(args[1], args[2]))
    if args[:1] == ["v2"] and len(args) in (3, 4):
        raise SystemExit(v2(args[1], args[2], args[3] if len(args) == 4 else None))
    if args[:1] == ["verdict"] and len(args) == 2:
        raise SystemExit(verdict(args[1]))
    raise SystemExit(__doc__)
