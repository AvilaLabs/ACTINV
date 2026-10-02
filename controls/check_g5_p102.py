#!/usr/bin/env python3
"""P102 G5 — independent checker: reparses the persisted G3 output with its
own parser (no shared code with the Rust writer or the G1-G4 controls),
recomputes every number from the actinv-r2s-source-1 corpus, and rejects
planted mutations: a density scaled, a group swapped, the time token
edited, an index sha edited, and a file removed.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results/check_g5_p102.json"
CORPUS = ROOT / "results/p52_r2s_source.ndjson"
SRC = ROOT / "results/p102_alara"
SHUTDOWN_T_S = 4000.0


# ---------- independent arithmetic (own implementation) ----------

def display(x: float) -> str:
    """Rust `Display` shortest-round-trip, no exponent notation."""
    if x == 0.0:
        import math
        return "-0" if math.copysign(1.0, x) < 0 else "0"
    r = repr(x)
    if "e" in r or "E" in r:
        return format(Decimal(r), "f")
    return r[:-2] if r.endswith(".0") else r


def sanitize(raw: str) -> str:
    out = []
    for b in raw.encode("utf-8"):
        ch = chr(b)
        out.append(ch if ch.isascii() and (ch.isalnum() or ch in "._-") else "_")
    return "".join(out)


def load_doc():
    recs = [json.loads(l) for l in CORPUS.read_text().splitlines() if l.strip()]
    hdr = next(r for r in recs if r.get("record") == "header")
    assert hdr["schema"] == "actinv-r2s-source-1"
    cells = [r for r in recs if r.get("record") == "cell"]
    step_t_s = {c["step_t_s"] for c in cells}
    assert len(step_t_s) == 1, "corpus does not carry one step_t_s basis"
    step_t_s = next(iter(step_t_s))
    cooling = step_t_s - SHUTDOWN_T_S
    time_token = "shutdown" if cooling == 0.0 else f"{display(cooling)} s"
    grid = []
    for c in cells:
        for g in c.get("groups", []):
            if g["centroid_eV"] > 0 and g["photons_s"] > 0 and \
                    g["centroid_eV"] not in grid:
                grid.append(g["centroid_eV"])
    grid.sort()
    return hdr, cells, step_t_s, cooling, time_token, grid


# ---------- independent parser (no shared code with the writer) ----------

def parse_photon_src(text: str) -> dict:
    lines = [l for l in text.split("\n") if l.strip()]
    assert len(lines) == 1, f"expected exactly one row, got {len(lines)}"
    toks = lines[0].split("\t")
    assert toks[0] == "TOTAL"
    return {"nuclide": toks[0], "time": toks[1],
            "densities": [float(v) for v in toks[2:]]}


# ---------- verification ----------

def expected_row(cell, grid, time_token):
    nz = {g["centroid_eV"]: g["photons_s"] for g in cell.get("groups", [])
          if g["centroid_eV"] > 0 and g["photons_s"] > 0}
    densities = [nz.get(e, 0.0) / cell["volume_cm3"] for e in grid]
    return densities


def verify_all(cells, grid, time_token, step_t_s) -> list:
    problems = []
    index = json.loads((SRC / "actinv-alara-index.json").read_text())
    if len(index["cells"]) != len(cells):
        return [f"index cell count {len(index['cells'])} != {len(cells)}"]
    if index["group_centroids_eV"] != grid:
        problems.append("index group grid differs from independent union")
    width = 1 if len(cells) <= 1 else len(str(len(cells) - 1))
    for i, (c, entry) in enumerate(zip(cells, index["cells"])):
        name = f"{i:0{width}d}_{sanitize(c['id'])}.photonSrc"
        if entry["file"] != name:
            problems.append(f"cell {i} file name {entry['file']} != {name}")
            continue
        path = SRC / name
        if not path.exists():
            problems.append(f"cell {i} file {name} missing")
            continue
        text = path.read_text()
        if entry["sha256"] != hashlib.sha256(text.encode()).hexdigest():
            problems.append(f"cell {i} index sha256 mismatch")
        parsed = parse_photon_src(text)
        if parsed["time"] != time_token:
            problems.append(f"cell {i} time token {parsed['time']} != {time_token}")
        exp = expected_row(c, grid, time_token)
        if parsed["densities"] != exp:
            problems.append(f"cell {i} densities differ: {parsed['densities']} != {exp}")
        reconstructed = sum(d * c["volume_cm3"] for d in parsed["densities"])
        if abs(reconstructed - c["photons_s"]) > 1e-9 * max(1.0, abs(c["photons_s"])):
            problems.append(f"cell {i} conservation off: {reconstructed} != {c['photons_s']}")
        if entry["bounds_cm"] != c["bounds_cm"]:
            problems.append(f"cell {i} bounds mismatch")
        if entry["volume_cm3"] != c["volume_cm3"]:
            problems.append(f"cell {i} volume mismatch")
        if entry["photons_s"] != c["photons_s"]:
            problems.append(f"cell {i} photons_s mismatch")
    return problems


# ---------- mutation detection ----------

def mutate_density(path: Path) -> str:
    text = path.read_text()
    toks = text.rstrip("\n").split("\t")
    toks[2] = display(float(toks[2]) * 2.0 + 1.0)
    return "\t".join(toks) + "\n"


def mutate_group_swap(path: Path) -> str:
    """Swap two density columns that actually differ (a swap of two equal
    zeros would be a silent no-op mutation)."""
    text = path.read_text()
    toks = text.rstrip("\n").split("\t")
    densities = toks[2:]
    i = j = None
    for a in range(len(densities)):
        for b in range(a + 1, len(densities)):
            if densities[a] != densities[b]:
                i, j = a, b
                break
        if i is not None:
            break
    if i is None:
        raise RuntimeError("no two distinct density columns to swap")
    densities[i], densities[j] = densities[j], densities[i]
    return "\t".join(toks[:2] + densities) + "\n"


def mutate_time_token(path: Path) -> str:
    text = path.read_text()
    toks = text.rstrip("\n").split("\t")
    toks[1] = "shutdown" if toks[1] != "shutdown" else "1 s"
    return "\t".join(toks) + "\n"


def detect(cells, grid, time_token, mutate, target_index) -> bool:
    """Apply `mutate` to one target file, re-verify, restore, return whether
    the mutation was caught."""
    index = json.loads((SRC / "actinv-alara-index.json").read_text())
    name = index["cells"][target_index]["file"]
    path = SRC / name
    original = path.read_bytes()
    try:
        path.write_text(mutate(path))
        problems = verify_all(cells, grid, time_token, None)
        return bool(problems)
    finally:
        path.write_bytes(original)


def detect_index_sha_edit(cells, grid, time_token) -> bool:
    index_path = SRC / "actinv-alara-index.json"
    original = index_path.read_bytes()
    try:
        index = json.loads(original)
        index["cells"][0]["sha256"] = "0" * 64
        index_path.write_text(json.dumps(index))
        problems = verify_all(cells, grid, time_token, None)
        return bool(problems)
    finally:
        index_path.write_bytes(original)


def detect_file_removed(cells, grid, time_token) -> bool:
    index = json.loads((SRC / "actinv-alara-index.json").read_text())
    name = index["cells"][0]["file"]
    path = SRC / name
    original = path.read_bytes()
    try:
        path.unlink()
        problems = verify_all(cells, grid, time_token, None)
        return bool(problems)
    finally:
        path.write_bytes(original)


def main() -> int:
    hdr, cells, step_t_s, cooling, time_token, grid = load_doc()
    problems = []

    if not SRC.exists() or not any(SRC.iterdir()):
        print(f"missing G3 output directory: {SRC}")
        return 1

    problems += verify_all(cells, grid, time_token, step_t_s)

    mutations = {}
    mutations["density_scaled"] = detect(cells, grid, time_token, mutate_density, 0)
    mutations["group_swapped"] = detect(cells, grid, time_token, mutate_group_swap, 0)
    mutations["time_token_edited"] = detect(cells, grid, time_token, mutate_time_token, 0)
    mutations["index_sha_edited"] = detect_index_sha_edit(cells, grid, time_token)
    mutations["file_removed"] = detect_file_removed(cells, grid, time_token)

    for k, v in mutations.items():
        if not v:
            problems.append(f"mutation '{k}' NOT detected")

    evidence = {"schema": "actinv-p102-check-g5-1",
                "pass": not problems, "problems": problems,
                "mutations_detected": mutations}
    RESULT.write_text(json.dumps(evidence, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"pass": not problems, "problems": problems[:20],
                      "mutations": mutations}))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
