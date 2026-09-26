#!/usr/bin/env python3
"""P57 G2 — exactness: independently re-derive every emitted token of all
three foreign outputs from the fixture bytes at machine precision.
"""
from __future__ import annotations

import json
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p57_case as p57  # noqa: E402

RESULT = ROOT / "results/g2_p57_exactness.json"

TOL = 1e-15


def close(a: float, b: float, tol=TOL) -> bool:
    return abs(a - b) <= tol * max(1.0, abs(a), abs(b))


def fixture_cells(fixture: str):
    return [json.loads(l) for l in fixture.splitlines()
            if json.loads(l).get("record") == "cell"]


def nonzero_groups(cell):
    return [(g["centroid_eV"], g["photons_s"])
            for g in cell.get("groups", [])
            if g["centroid_eV"] > 0 and g["photons_s"] > 0]


def check_openmc(text: str, cells: list) -> dict:
    checks = {}
    root = ET.fromstring(text)
    srcs = [e for e in root if e.tag == "source"]
    checks["count"] = len(srcs) == len(cells)
    for i, (s, c) in enumerate(zip(srcs, cells)):
        checks[f"strength_{i}"] = close(
            float(s.get("strength")), c["photons_s"], 1e-17)
        space = s.find("space")
        for a, ax in enumerate(("x", "y", "z")):
            lo, hi = space.find(ax).get("parameters").split()
            checks[f"bounds_{ax}_{i}"] = (
                close(float(lo), c["bounds_cm"][a][0], 0) and
                close(float(hi), c["bounds_cm"][a][1], 0) and
                lo == p57.rust_fmt(c["bounds_cm"][a][0]) and
                hi == p57.rust_fmt(c["bounds_cm"][a][1]))
        en = s.find("energy")
        groups = nonzero_groups(c)
        if groups:
            vals = en.find("parameters").text.split()
            n = len(groups)
            xs = [float(v) for v in vals[:n]]
            ps = [float(v) for v in vals[n:]]
            exp_p = p57.probs(groups)
            checks[f"energy_x_{i}"] = all(
                x == g[0] for x, g in zip(xs, groups))
            checks[f"energy_p_{i}"] = all(
                p == e for p, e in zip(ps, exp_p))
            checks[f"energy_p_sum_{i}"] = abs(sum(ps) - 1.0) <= 1e-14
            # token-exact formatting
            checks[f"energy_fmt_{i}"] = (
                vals[:n] == [p57.rust_fmt(g[0]) for g in groups] and
                vals[n:] == [p57.rust_fmt(p) for p in exp_p])
        else:
            checks[f"energy_absent_{i}"] = en is None
    return checks


def check_mcnp(text: str, cells: list) -> dict:
    checks = {}
    lines = [l for l in text.splitlines()
             if l.strip() and not l.startswith("c ")]
    total = sum(c["photons_s"] for c in cells)
    # each cell: SDEF + 3x(SI H, SP) + SI L + SP = 9 lines
    checks["line_count"] = len(lines) == 9 * len(cells)
    for i, c in enumerate(cells):
        block = lines[9 * i:9 * i + 9]
        base = 1 + i * 4
        sdef = block[0].split()
        exp_w = c["photons_s"] / total
        checks[f"sdef_refs_{i}"] = sdef[1:] == [
            f"X=D{base}", f"Y=D{base+1}", f"Z=D{base+2}",
            f"ERG=D{base+3}", f"WGT={p57.rust_fmt(exp_w)}"]
        for a in range(3):
            si = block[1 + 2 * a].split()
            sp = block[2 + 2 * a].split()
            checks[f"si_sp_{a}_{i}"] = (
                si == [f"SI{base+a}", "H",
                       p57.rust_fmt(c["bounds_cm"][a][0]),
                       p57.rust_fmt(c["bounds_cm"][a][1])] and
                sp == [f"SP{base+a}", "0", "1"])
        sie = block[7].split()
        spe = block[8].split()
        groups = nonzero_groups(c)
        if groups:
            exp_p = p57.probs(groups)
            checks[f"erg_x_{i}"] = sie[2:] == [
                p57.rust_fmt(g[0] / 1e6) for g in groups]
            checks[f"erg_p_{i}"] = spe[1:] == [
                p57.rust_fmt(p) for p in exp_p]
        else:
            checks[f"erg_degenerate_{i}"] = (
                sie == [f"SI{base+3}", "L", "1000000"] and
                spe == [f"SP{base+3}", "1"])
    return checks


def check_serpent(text: str, cells: list) -> dict:
    checks = {}
    lines = [l for l in text.splitlines() if l.startswith("src ")]
    total = sum(c["photons_s"] for c in cells)
    checks["line_count"] = len(lines) == len(cells)
    for i, c in enumerate(cells):
        t = lines[i].split()
        exp_w = c["photons_s"] / total
        checks[f"weight_{i}"] = t[t.index("sw") + 1] == p57.rust_fmt(exp_w)
        for a, ax in enumerate(("sx", "sy", "sz")):
            k = t.index(ax)
            checks[f"{ax}_{i}"] = (
                t[k + 1] == p57.rust_fmt(c["bounds_cm"][a][0]) and
                t[k + 2] == p57.rust_fmt(c["bounds_cm"][a][1]))
        groups = nonzero_groups(c)
        if groups:
            k = t.index("sb")
            ne, intt = int(t[k + 1]), int(t[k + 2])
            pairs = t[k + 3:]
            exp_p = p57.probs(groups)
            checks[f"sb_{i}"] = (
                ne == len(groups) and intt == 0 and
                len(pairs) == 2 * ne and
                pairs[0::2] == [p57.rust_fmt(g[0] / 1e6)
                                for g in groups] and
                pairs[1::2] == [p57.rust_fmt(p) for p in exp_p])
        else:
            k = t.index("se")
            checks[f"se_{i}"] = float(t[k + 1]) == 1.0
        checks[f"name_{i}"] = t[1].startswith("s_") and \
            t[1].endswith(f"_{i}")
    return checks


def check_provenance(text: str, prefix: str, cells: list,
                     input_sha: str) -> dict:
    checks = {}
    checks["emitter"] = f"{prefix} actinv-source-adapter-1" in text or \
        f"{prefix}\nactinv-source-adapter-1" in text
    checks["sha"] = f"input_sha256={input_sha}" in text
    checks["step"] = "step=2" in text
    checks["total"] = f"total_photons_s={p57.rust_fmt(15.0)}" in text
    for c in cells:
        frag = f"cell '{c['id']}' photons_s={p57.rust_fmt(c['photons_s'])}"
        checks[f"prov_{c['id']}"] = frag in text
        if "sigma_photons_s_independent" in c:
            frag2 = (f"sigma_independent={p57.rust_fmt(c['sigma_photons_s_independent'])}"
                     f" sigma_conservative={p57.rust_fmt(c['sigma_photons_s_conservative'])}")
            checks[f"prov_sigma_{c['id']}"] = frag2 in text
        else:
            checks[f"prov_sigma_{c['id']}"] = "unbanded" in text.split(
                frag)[1].split("\n")[0]
    return checks


def main() -> int:
    import hashlib
    fixture = p57.fixture_r2s()
    cells = fixture_cells(fixture)
    sha = hashlib.sha256(fixture.encode()).hexdigest()
    checks = {}

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        outs = {}
        for fmt in ("openmc", "mcnp", "serpent"):
            out = td / f"out.{fmt}"
            r = p57.run_export(fmt, fixture, out)
            assert r.returncode == 0, r.stderr
            outs[fmt] = out.read_text()

    checks.update({f"openmc_{k}": v
                   for k, v in check_openmc(outs["openmc"], cells).items()})
    checks.update({f"mcnp_{k}": v
                   for k, v in check_mcnp(outs["mcnp"], cells).items()})
    checks.update({f"serpent_{k}": v
                   for k, v in check_serpent(outs["serpent"], cells).items()})
    checks.update({f"omprov_{k}": v for k, v in check_provenance(
        outs["openmc"], "", cells, sha).items()})
    checks.update({f"mcprov_{k}": v for k, v in check_provenance(
        outs["mcnp"], "c", cells, sha).items()})
    checks.update({f"spprov_{k}": v for k, v in check_provenance(
        outs["serpent"], "%", cells, sha).items()})

    evidence = {"schema": "actinv-p57-g2-exactness-1",
                "pass": all(checks.values()), "checks": checks}
    RESULT.write_text(json.dumps(evidence, indent=1, sort_keys=True) + "\n")
    failed = [k for k, v in checks.items() if not v]
    print(json.dumps({"pass": evidence["pass"], "n": len(checks),
                      "failed": failed}))
    return 0 if evidence["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
