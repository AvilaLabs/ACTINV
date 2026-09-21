#!/usr/bin/env python3
"""Ordinate-level MF=10/MF=9-vs-MF=3 audit over the TENDL-2025 eviction ledger.

For every file in the P41 build's failure ledger, re-parse the flagged
(MT, MF) section from the unmodified source file using fixed-width ENDF
fields, and check whether the emitted-state sum exceeds the MF=3 total at an
*explicit tabulated ordinate* (any energy at which an MF=10/MF=9 subsection
declares a value), as opposed to only between ordinates under lin-lin
interpolation.

MF=10 subsections carry production cross sections: consistency requires
sum_s sigma_s(E) <= sigma_MT(E).
MF=9 subsections carry state multiplicities: production sigma_s = m_s * sigma_MT,
so consistency requires sum_s m_s(E) <= 1 (times the runtime total).

Classification per file:
  ordinate_violation            - excess >0.01% at a declared ordinate
  ordinate_violation_zero_total - emitted >0 where MF=3 total is 0
  clean_at_ordinates            - excess only under interpolation
  unparsed/section_absent/...   - audit could not resolve the section

Output: results/p41_tendl_mf10_ordinate_audit.json
"""
from __future__ import annotations

import bisect
import json
import re
from pathlib import Path

ND = Path.home() / "nuclear-data"
BUILD = ND / "tendl-2025-patched" / "build"
FILES = ND / "tendl-2025-patched" / "files" / "n"
OUT = Path("/home/connoravila/Documents/actinv/results/p41_tendl_mf10_ordinate_audit.json")

FAIL_RE = re.compile(r"MT(\d+)/MF=(\d+) ZAP=(\d+)")
TOL = 1e-4  # 0.01% ordinate tolerance for printed precision


def f11(line: str, i: int):
    s = line[i * 11 : (i + 1) * 11].strip().replace(" ", "")
    if not s:
        return None
    if "e" not in s.lower():
        for k in range(1, len(s)):
            if s[k] in "+-":
                s = s[:k] + "e" + s[k:]
                break
    try:
        return float(s)
    except ValueError:
        return None


def fields(line: str):
    return [f11(line, k) for k in range(6)]


def pairs(lines, start: int, np_: int):
    vals = []
    i = start
    while len(vals) < 2 * np_ and i < len(lines):
        for v in fields(lines[i]):
            if v is not None:
                vals.append(v)
        i += 1
    return vals[0::2][:np_], vals[1::2][:np_]


def interp(es, xs, e):
    if e <= es[0]:
        return xs[0]
    if e >= es[-1]:
        return xs[-1]
    j = bisect.bisect_right(es, e) - 1
    t = (e - es[j]) / (es[j + 1] - es[j])
    return xs[j] + t * (xs[j + 1] - xs[j])


def section_lines(lines, mf: int, mt: int):
    return [
        i
        for i, l in enumerate(lines)
        if len(l) > 74
        and l[70:72].strip() == str(mf)
        and l[72:75].strip() == str(mt)
        and l[75:80].strip().isdigit()
    ]


def audit_file(path: Path, mt: int, mf: int):
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as e:
        return "unreadable", str(e)

    i3 = section_lines(lines, 3, mt)
    iX = section_lines(lines, mf, mt)
    if not i3 or not iX:
        return "section_absent", f"mf3={len(i3)} mf{mf}={len(iX)}"

    # MF=3 TAB1: head(i3[0]), control(i3[1]) -> NR,NP; data from i3[3]
    try:
        ctrl = fields(lines[i3[1]])
        np3 = int(ctrl[5])
        e3, x3 = pairs(lines, i3[3], np3)
    except Exception as e:
        return "mf3_parse_failed", str(e)

    # Walk MF=9/10 subsections structurally: each subsection head is a
    # control line (C1, C2, ZAP, LFS, NR, NP); then NBT/INT; then pairs.
    subs = []
    i = iX[0] + 1
    while i <= iX[-1]:
        f = fields(lines[i])
        if (
            f[2] is not None
            and f[2] > 1000
            and f[4] is not None
            and f[5] is not None
            and f[4] >= 1
            and f[5] >= 1
            and float(f[2]).is_integer()
            and float(f[4]).is_integer()
            and float(f[5]).is_integer()
        ):
            zap, lfs, np_ = int(f[2]), int(f[3] or 0), int(f[5])
            es, xs = pairs(lines, i + 2, np_)
            subs.append((es, xs, zap, lfs))
            i += 2 + (2 * np_ + 5) // 6
        else:
            i += 1
    if not subs:
        return "no_subsections", "0"

    ords = sorted({e for es, _, _, _ in subs for e in es})
    n_viol = 0
    worst = (0.0, 0.0)
    for e in ords:
        s = sum(interp(es, xs, e) for es, xs, _, _ in subs)
        if mf == 9:
            # state multiplicities must sum to <=1 for single-residual
            # reactions regardless of the reaction cross section level
            rel = s - 1.0
            if rel > TOL:
                n_viol += 1
                if rel > worst[0]:
                    worst = (rel, e)
        else:
            t3 = interp(e3, x3, e)
            if t3 <= 0:
                if s > 0:
                    return "ordinate_violation_zero_total", {
                        "energy_eV": e, "emitted_sum_barn": s, "mf3_total_barn": t3
                    }
                continue
            rel = s / t3 - 1.0
            if rel > TOL:
                n_viol += 1
                if rel > worst[0]:
                    worst = (rel, e)
    if n_viol:
        return "ordinate_violation", {
            "max_relative_excess": worst[0],
            "at_energy_eV": worst[1],
            "n_violating_ordinates": n_viol,
            "n_ordinates": len(ords),
        }
    return "clean_at_ordinates", {"n_ordinates": len(ords)}


def main():
    record = json.loads((BUILD / "BUILD_RECORD.json").read_text())
    ledger = record["failure_ledger"]
    out = {}
    classes = {}
    for fname, entry in sorted(ledger.items()):
        msg = entry.get("message", "") if isinstance(entry, dict) else str(entry)
        m = FAIL_RE.search(msg)
        if not m:
            cls = "nonfinite_endf_number" if "nonfinite" in msg else "unparsed_message"
            out[fname] = {"class": cls, "message": msg[:160]}
            classes[cls] = classes.get(cls, 0) + 1
            continue
        mt, mf, zap = int(m.group(1)), int(m.group(2)), int(m.group(3))
        src = FILES / fname
        cls, detail = audit_file(src, mt, mf)
        if mf == 9 and cls == "ordinate_violation":
            cls = "ordinate_multiplicity_excess"
        out[fname] = {
            "class": cls, "mt": mt, "mf": mf, "zap": zap, "detail": detail,
        }
        classes[cls] = classes.get(cls, 0) + 1

    payload = {
        "schema": "actinv-p41-ordinate-audit-1",
        "corpus": "tendl-2025-patched neutron s30",
        "ordinate_tolerance": TOL,
        "files_audited": len(out),
        "class_counts": classes,
        "files": out,
    }
    OUT.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"files": len(out), "classes": classes}, indent=1))


if __name__ == "__main__":
    main()
