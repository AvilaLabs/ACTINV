#!/usr/bin/env python3
"""P40 G2: class-tag every identical-data divergence census-wide.

For each executed case in the p39 work dirs, recover the FULL per-nuclide
activity vectors for both arms (ALARA stdout table + ACTINV out.json),
decompose the divergence into isomer_branching / quasi_stable_convention
/ other, and report class-cleaned residual equivalence.
"""
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import g2_p26b_leg as leg  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
WORK = Path.home() / "nuclear-data" / "p26b-work" / "p38-run" / "g2-cases-p39"
OUT = ROOT / "results" / "g2_p40_classes.json"
LEDGER = ROOT / "results" / "g2_p39_leg_ledger.jsonl"

Z_BY_SYM = {
    "H": 1, "He": 2, "Li": 3, "Be": 4, "B": 5, "C": 6, "N": 7, "O": 8,
    "F": 9, "Ne": 10, "Na": 11, "Mg": 12, "Al": 13, "Si": 14, "P": 15,
    "S": 16, "Cl": 17, "Ar": 18, "K": 19, "Ca": 20, "Sc": 21, "Ti": 22,
    "V": 23, "Cr": 24, "Mn": 25, "Fe": 26, "Co": 27, "Ni": 28, "Cu": 29,
    "Zn": 30, "Ga": 31, "Ge": 32, "As": 33, "Se": 34, "Br": 35, "Kr": 36,
    "Rb": 37, "Sr": 38, "Y": 39, "Zr": 40, "Nb": 41, "Mo": 42, "Tc": 43,
    "Ru": 44, "Rh": 45, "Pd": 46, "Ag": 47, "Cd": 48, "In": 49, "Sn": 50,
    "Sb": 51, "Te": 52, "I": 53, "Xe": 54, "Cs": 55, "Ba": 56, "La": 57,
    "Hf": 72, "Ta": 73, "W": 74, "Re": 75, "Os": 76, "Ir": 77, "Pt": 78,
    "Au": 79, "Hg": 80, "Pb": 82, "Bi": 83,
}

ISOMER_RE = re.compile(r"^[A-Z][a-z]?\d+m\d+$")
QUASI_STABLE_S = 1e15
SYM_BY_Z = {v: k for k, v in Z_BY_SYM.items()}


def resolve_zlabel(name: str) -> str:
    """'Z22-51' (unresolved ALARA row) -> 'Ti51'; pass others through."""
    m = re.fullmatch(r"Z(\d+)-(\d+)(?:m(\d*)?)?", name)
    if not m:
        return name
    sym = SYM_BY_Z.get(int(m.group(1)))
    if not sym:
        return name
    iso = m.group(3)
    if iso is not None:
        return f"{sym}{m.group(2)}m{iso or '1'}"
    return f"{sym}{m.group(2)}"


def kza_of(name: str):
    """ACTINV-style name -> ALARA idx KZA (Z*10000 + A*10 + LISO)."""
    m = re.fullmatch(r"([A-Z][a-z]?)(\d+)(m(\d+))?", name)
    if not m:
        return None
    z = Z_BY_SYM.get(m.group(1))
    if z is None:
        return None
    return z * 10000 + int(m.group(2)) * 10 + (int(m.group(4) or 0))


def classify(name: str, hl: dict) -> str:
    """Divergence class for a nuclide present in one arm only."""
    if ISOMER_RE.match(name):
        return "isomer_branching"
    k = kza_of(name)
    t = hl.get(k) if k is not None else None
    if t is not None and t > QUASI_STABLE_S:
        return "quasi_stable_convention"
    return "other"


def alara_vectors(case_dir: Path, hl: dict, n_roots: int):
    """Full per-nuclide ALARA activity vectors {name: [act(t)...]}."""
    text = (case_dir / "case.stdout").read_text(errors="replace")
    union = sorted(leg.parse_alara_dump(case_dir / "alara.dmp",
                                        set(hl), n_roots))
    act = leg.parse_output_table(text, "Specific Activity [Bq/g]")
    rows = leg.resolve_isotope_names(act.get("rows", []), union, hl)
    times = act.get("times_s") or []
    vec = {}
    for r in rows:
        n = r.get("name")
        if n:
            vec[resolve_zlabel(leg.actinv_key(n))] = r["values"]
    return times, vec


def actinv_vectors(case_dir: Path, irr_s: float):
    """Full per-nuclide ACTINV activity: {name: {step_index: act}}."""
    d = json.loads((case_dir / "out.json").read_text())
    times, vec = [], {}
    for si, st in enumerate(d.get("steps", [])):
        times.append(st["t_s"] - irr_s)
        for n, a in (st.get("activity_Bq_per_g") or {}).items():
            vec.setdefault(n, {})[si] = a
    return times, vec


def case_report(case: dict, hl: dict):
    cdir = WORK / case["case"]
    if not (cdir / "out.json").exists() or not (cdir / "case.stdout").exists():
        return None
    n_roots = len(case["required_isotopes"]["alara"])
    at, avec = alara_vectors(cdir, hl, n_roots)
    ct, cvec = actinv_vectors(cdir, case["irradiation_s"])
    rep = {"case": case["case"], "times": {}, "worst": {}}
    for i, t in enumerate(at):
        amap = {n: v[i] for n, v in avec.items()
                if i < len(v) and v[i] > 0}
        j = min(range(len(ct)), key=lambda k: abs(ct[k] - t))
        cmap = {}
        if abs(ct[j] - t) <= max(1.0, 0.01 * t):
            cmap = {n: vals.get(j, 0.0) for n, vals in cvec.items()
                    if vals.get(j, 0.0) > 0}
        classes_a = defaultdict(dict)   # alara-only nuclides
        for n, a in amap.items():
            if cmap.get(n, 0.0) <= 0.0:
                classes_a[classify(n, hl)][n] = a
        classes_c = defaultdict(dict)   # actinv-only nuclides
        for n, v in cmap.items():
            if amap.get(n, 0.0) <= 0.0 and v > 0:
                classes_c[classify(n, hl)][n] = v
        tot_a = sum(amap.values())
        tot_c = sum(cmap.values())
        floor_names = ("isomer_branching", "quasi_stable_convention")
        floor_a = sum(sum(d.values()) for k, d in classes_a.items()
                      if k in floor_names)
        floor_c = sum(sum(d.values()) for k, d in classes_c.items()
                      if k in floor_names)
        other_a = sum(classes_a["other"].values())
        other_c = sum(classes_c["other"].values())
        clean_a = tot_a - floor_a - other_a
        clean_c = tot_c - floor_c - other_c
        divergent_common = {}
        for n in amap:
            cv = cmap.get(n, 0.0)
            if cv > 0:
                a, lo = amap[n], min(amap[n], cv)
                if lo > 0 and amap[n] > tot_a * 0.005 \
                        and abs(amap[n] - cv) / max(amap[n], cv) > 0.02:
                    divergent_common[n] = [amap[n], cv]
        rep["times"][f"{t:g}"] = {
            "total_alara": tot_a, "total_actinv": tot_c,
            "ratio": tot_a / tot_c if tot_c > 0 else None,
            "class_shares_alara": {
                k: sum(d.values()) / tot_a if tot_a else 0.0
                for k, d in classes_a.items()},
            "class_shares_actinv": {
                k: sum(d.values()) / tot_c if tot_c else 0.0
                for k, d in classes_c.items()},
            "class_members_alara": {k: sorted(d)
                                    for k, d in classes_a.items()},
            "class_members_actinv": {k: sorted(d)
                                     for k, d in classes_c.items()},
            "clean_ratio": clean_a / clean_c if clean_c > 0 else None,
            "other_share_alara": other_a / tot_a if tot_a else 0.0,
            "other_share_actinv": other_c / tot_c if tot_c else 0.0,
            "divergent_common": divergent_common,
        }
    for t, r in rep["times"].items():
        for k in ("ratio", "clean_ratio"):
            v = r.get(k)
            if v is not None and (rep["worst"].get(k) is None
                                  or abs(v - 1.0)
                                  > abs(rep["worst"][k]["value"] - 1.0)):
                rep["worst"][k] = {"t": t, "value": v}
        for k in ("other_share_alara", "other_share_actinv"):
            v = r.get(k)
            if v is not None and (rep["worst"].get(k) is None
                                  or v > rep["worst"][k]["value"]):
                rep["worst"][k] = {"t": t, "value": v}
    return rep


def main():
    contract = json.loads(leg.CONTRACT_PATH.read_text())
    hl = leg.idx_parse(Path(str(leg.ALARA_LIB_BASE) + ".idx"))
    executed = set()
    for line in LEDGER.read_text().splitlines():
        r = json.loads(line)
        if r["record"]["status"].startswith(("executed", "tolerance")):
            executed.add(r["case"])
    cases = [c for c in contract["cases"] if c["case"] in executed]
    reports, skips = [], []
    for c in cases:
        rep = case_report(c, hl)
        (reports if rep else skips).append(rep or c["case"])
    # aggregate
    worst_clean, worst_raw, worst_other = 0.0, 0.0, 0.0
    other_members = Counter()
    iso_members = Counter()
    qs_members = Counter()
    div_common = Counter()
    per_case = []
    for rep in reports:
        wc = max((abs(r["clean_ratio"] - 1.0)
                  for r in rep["times"].values()
                  if r["clean_ratio"] is not None), default=0.0)
        wr = max((abs(r["ratio"] - 1.0)
                  for r in rep["times"].values()
                  if r["ratio"] is not None), default=0.0)
        wo = max((max(r["other_share_alara"], r["other_share_actinv"])
                  for r in rep["times"].values()), default=0.0)
        worst_clean = max(worst_clean, wc)
        worst_raw = max(worst_raw, wr)
        worst_other = max(worst_other, wo)
        for r in rep["times"].values():
            for n in r["divergent_common"]:
                div_common[n] += 1
            for k, members in list(r["class_members_alara"].items()) \
                    + list(r["class_members_actinv"].items()):
                tgt = {"isomer_branching": iso_members,
                       "quasi_stable_convention": qs_members,
                       "other": other_members}.get(k)
                if tgt is not None:
                    for m in members:
                        tgt[m] += 1
        per_case.append({"case": rep["case"],
                         "worst_raw_dev": wr, "worst_clean_dev": wc,
                         "worst_other_share": wo})
    out = {
        "schema": "actinv-p40-g2-1",
        "n_cases": len(reports), "skipped": skips,
        "worst_raw_dev": worst_raw,
        "worst_clean_dev": worst_clean,
        "worst_other_share": worst_other,
        "isomer_members": dict(iso_members.most_common(20)),
        "quasi_stable_members": dict(qs_members.most_common(20)),
        "other_members": dict(other_members.most_common(40)),
        "divergent_common_members": dict(div_common.most_common(40)),
        "per_case": per_case,
    }
    OUT.write_text(json.dumps(out, indent=1))
    print(json.dumps({"n_cases": out["n_cases"],
                      "worst_raw_dev": worst_raw,
                      "worst_clean_dev": worst_clean,
                      "worst_other_share": worst_other,
                      "skipped": len(skips)}, indent=1))


if __name__ == "__main__":
    main()
