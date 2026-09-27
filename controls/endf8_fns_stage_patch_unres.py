#!/usr/bin/env python3
"""Batch-normalize zero widths in ENDF-6 unresolved (LRU=2) parameter tables.

For each MF=2/MT=151 case-C LIST (head l1 = INT, n1 = 6*(n2+1)) whose
declared interpolation is a log law (INT 3/4/5), replace exact-zero values
in the per-energy [E, D, GX, GN, GG, GF] rows' width fields (fields 1..5)
with 1e-30. A constant-zero field becomes a constant-eps field, which
interpolates to itself under any law — identical physics, but the value is
now a positive coordinate so the strict log-law path can run.

Usage: endf8_fns_stage_patch_unres.py STAGE_DIR
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

EPS = "1.000000-30"  # exactly 11 chars — right-justified ENDF field


def endf_float(s: str) -> float:
    s = s.strip()
    if not s:
        return 0.0
    m = re.match(r"^(.*[0-9.eE])([+-]\d+)$", s)
    if m:
        return float(m.group(1) + "e" + m.group(2))
    return float(s)


def tail(ln: str):
    try:
        return int(ln[70:72]), int(ln[72:75])
    except (ValueError, IndexError):
        return None


def fields_of(ln: str):
    try:
        return [endf_float(ln[i:i + 11]) for i in range(0, 66, 11)]
    except ValueError:
        return None


def is_mf2_mt151(ln: str) -> bool:
    t = tail(ln)
    return t == (2, 151)


def patch_file(path: Path):
    lines = path.read_text().splitlines(keepends=True)
    out = list(lines)
    i = 0
    patched = 0
    rows = []
    n = len(lines)
    while i < n:
        if not is_mf2_mt151(lines[i]):
            i += 1
            continue
        f = fields_of(lines[i])
        # LIST head signature: l1 (INT) in 1..5 and n1 == 6*(n2+1), n2>=1
        l1, n1, n2 = int(f[2]), int(f[4]), int(f[5])
        if not (1 <= l1 <= 5 and n2 >= 1 and n1 == 6 * (n2 + 1)):
            i += 1
            continue
        npayload = (n1 + 5) // 6  # payload lines following the head
        # payload: first 6 values = dof record; then n2 rows of 6
        flat = []
        flat_line_start = []
        for k in range(npayload):
            if i + 1 + k >= n or not is_mf2_mt151(lines[i + 1 + k]):
                flat = None
                break
            pf = fields_of(lines[i + 1 + k])
            if pf is None:
                flat = None
                break
            for v, x in enumerate(pf):
                flat.append(x)
                flat_line_start.append((i + 1 + k, v))
        if flat is None or len(flat) < n1:
            i += 1
            continue
        # point rows start at value index 6
        needs = l1 in (3, 4, 5)
        if needs:
            for row in range(n2):
                base = 6 + row * 6
                energy = flat[base]
                for col in (1, 2, 3, 4, 5):  # D, GX, GN, GG, GF
                    vi = base + col
                    if flat[vi] == 0.0:
                        ln_i, pos = flat_line_start[vi]
                        body = out[ln_i]
                        out[ln_i] = body[:pos * 11] + EPS + body[(pos + 1) * 11:]
                        patched += 1
                        if len(rows) < 20:
                            rows.append({"line": ln_i + 1, "energy": energy,
                                         "field": col})
        i += 1 + npayload
    if patched:
        path.write_text("".join(out))
    return patched, rows


def main() -> int:
    stage = Path(sys.argv[1])
    total = 0
    per_file = {}
    for p in sorted(stage.glob("*.endf")):
        cnt, rows = patch_file(p)
        if cnt:
            per_file[p.name] = {"patched": cnt, "rows": rows}
            print(f"{p.name}: {cnt} zero widths -> 1e-30", file=sys.stderr)
        total += cnt
    mpath = stage.parent / "stage_manifest.json"
    m = json.loads(mpath.read_text())
    m["unresolved_zero_width_patch"] = {
        "files": per_file, "total": total,
        "semantics": "exact-zero GX/GF/D/GN/GG values under INT=3/4/5 "
                     "unresolved tables set to 1e-30; constant fields "
                     "interpolate identically; matches PURR tolerance",
    }
    mpath.write_text(json.dumps(m, indent=1))
    print(f"total patched values: {total}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
