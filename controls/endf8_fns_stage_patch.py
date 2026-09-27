#!/usr/bin/env python3
"""Batch-normalize ENDF-6 Breit-Wigner GT fields in staged tapes.

Scans each staged tape's MF=2/MT=151 resolved-resonance records
(LRF=1/2/3 L-groups): where GT < GN+GG+GF by more than field-rounding but
less than PATCH_CAP relative, rewrite GT to the component sum. This is the
same value ACTINV's reconstruction uses (`max(total, components)` per
P10 Amendment D); it only pre-empts the strict validator. Rows exceeding
the cap are reported and left untouched (real inconsistencies).

Usage: endf8_fns_stage_patch.py STAGE_DIR
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REL_TOL = 1e-6          # ~ENDF 11-char field rounding, relative
PATCH_CAP = 5e-4        # max relative deficit we will normalize


def endf_float(s: str) -> float:
    s = s.strip()
    if not s:
        return 0.0
    m = re.match(r"^(.*[0-9.eE])([+-]\d+)$", s)
    if m:
        return float(m.group(1) + "e" + m.group(2))
    return float(s)


def endf_str(v: float) -> str:
    """Write an ENDF-6 11-char field matching the tape's style."""
    if v == 0.0:
        return " 0.000000+0"
    mant, exp = f"{v:.6E}".split("E")
    e = int(exp)
    # ENDF form keeps 2-3 exponent digits without 'E'
    if abs(e) < 10:
        s = f"{mant}{'+' if e >= 0 else '-'}{abs(e):01d}"
    else:
        s = f"{mant[:-1]}{'+' if e >= 0 else '-'}{abs(e):02d}"
        # drop a mantissa digit if too wide
        if len(s) > 11:
            mant = f"{v:.5E}".split("E")[0]
            s = f"{mant}{'+' if e >= 0 else '-'}{abs(e):02d}"
    if len(s) > 11:
        mant = f"{v:.4E}".split("E")[0]
        s = f"{mant}{'+' if e >= 0 else '-'}{abs(e):02d}"
    return s.rjust(11)


def tail(ln: str):
    try:
        return int(ln[70:72]), int(ln[72:75])
    except (ValueError, IndexError):
        return None


def fields(ln: str):
    try:
        return [endf_float(ln[i:i + 11]) for i in range(0, 66, 11)]
    except ValueError:
        return None


def patch_file(path: Path):
    lines = path.read_text().splitlines(keepends=True)
    out = []
    in_mf2 = False
    patched = flagged = 0
    patch_rows = []
    i = 0
    n = len(lines)
    while i < n:
        ln = lines[i]
        t = tail(ln)
        if t:
            mf, mt = t
            if mf != 2:
                in_mf2 = False
            elif mt == 151:
                in_mf2 = True
        out.append(ln)
        if in_mf2:
            f = fields(ln)
            if f is not None and f[2] > 0.0 and f[3] > 0.0:
                comp = f[3] + f[4] + f[5]
                deficit = comp - f[2]
                if deficit > REL_TOL * max(abs(f[2]), abs(comp)):
                    rel = deficit / max(abs(f[2]), abs(comp), 1e-30)
                    if rel <= PATCH_CAP:
                        new = endf_str(comp)
                        assert len(new) == 11
                        # out[-1] is ln including newline
                        body = out[-1][:22] + new + out[-1][33:]
                        out[-1] = body
                        patched += 1
                        patch_rows.append(
                            {"line": i + 1, "gt": f[2], "components": comp})
                    else:
                        flagged += 1
                        print(f"  {path.name}:{i+1} LARGE deficit "
                              f"GT={f[2]} vs comp={comp} (rel {rel:.3g}) "
                              "— left untouched", file=sys.stderr)
        i += 1
    if patched:
        path.write_text("".join(out))
    return patched, flagged, patch_rows


def main() -> int:
    stage = Path(sys.argv[1])
    total_p = total_f = 0
    manifest_patch = {}
    for p in sorted(stage.glob("*.endf")):
        np_, nf, rows = patch_file(p)
        if np_ or nf:
            manifest_patch[p.name] = {"patched": np_, "flagged": nf,
                                      "rows": rows[:20]}
            print(f"{p.name}: patched {np_} resonance rows"
                  + (f" FLAGGED {nf}" if nf else ""), file=sys.stderr)
        total_p += np_
        total_f += nf
    mpath = stage.parent / "stage_manifest.json"
    m = json.loads(mpath.read_text())
    m["bw_gt_batch_patch"] = {
        "files": manifest_patch,
        "total_rows": total_p, "flagged_rows": total_f,
        "semantics": "GT set to GN+GG+GF; identical to builder's "
                     "max(total, components) effective width",
    }
    mpath.write_text(json.dumps(m, indent=1))
    print(f"total patched rows: {total_p} (flagged {total_f})",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
