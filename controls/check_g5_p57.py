#!/usr/bin/env python3
"""P57 G5 — independent checker: reparses the persisted G3 outputs with
its own parsers (no shared emit code), recomputes every numeric token from
the actinv-r2s-source-1 bytes, and verifies planted mutations are caught.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import xml.etree.ElementTree as ET
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results/check_g5_p57.json"
CORPUS = ROOT / "results/p52_r2s_source.ndjson"
SRC = ROOT / "results/p57_sources"

OUTS = {"openmc": SRC / "p57_r2s.xml",
        "mcnp": SRC / "p57_r2s.mcnp",
        "serpent": SRC / "p57_r2s.serpent"}


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


def naive_sum(xs):
    """Left-to-right f64 fold — mirrors Rust `Iterator::sum` (Python 3.12+
    `sum()` uses compensated summation and differs at the last ulp)."""
    t = 0.0
    for x in xs:
        t += x
    return t


def nprobs(strengths):
    """The exact ratios s_i/t — the emitted share values."""
    if not strengths:
        return []
    t = naive_sum(strengths)
    return [s / t for s in strengths]


def load_doc():
    recs = [json.loads(l) for l in CORPUS.read_text().splitlines()
            if l.strip()]
    hdr = next(r for r in recs if r.get("record") == "header")
    assert hdr["schema"] == "actinv-r2s-source-1"
    cells = [r for r in recs if r.get("record") == "cell"]
    for c in cells:
        c["_nz"] = [(g["centroid_eV"], g["photons_s"])
                    for g in c.get("groups", [])
                    if g["centroid_eV"] > 0 and g["photons_s"] > 0]
    return hdr, cells


# ---------- independent parsers ----------

def parse_openmc(text):
    root = ET.fromstring(text)
    assert root.tag == "sources"
    out = []
    for e in root:
        if e.tag != "source":
            continue
        space = e.find("space")
        b = [list(map(float, space.find(a).get("parameters").split()))
             for a in "xyz"]
        en = e.find("energy")
        groups = []
        if en is not None:
            vals = en.find("parameters").text.split()
            n = len(vals) // 2
            groups = list(zip(map(float, vals[:n]),
                              map(float, vals[n:])))
        out.append({"strength": float(e.get("strength")),
                    "bounds": b, "groups": groups,
                    "particle": e.get("particle"),
                    "iso": e.find("angle").get("type") == "isotropic"})
    comments = re.findall(r"cell '([^']+)' photons_s=(\S+)", text)
    return out, comments, text


def parse_mcnp(text):
    cells = []
    lines = [l for l in text.splitlines()
             if l.strip() and not l.startswith("c ")]
    i = 0
    while i < len(lines):
        assert lines[i].startswith("SDEF"), lines[i]
        t = lines[i].split()
        w = float(next(x.split("=")[1] for x in t
                       if x.startswith("WGT=")))
        refs = {x.split("=")[0]: int(x.split("=")[1][1:])
                for x in t if x.startswith(("X=", "Y=", "Z=", "ERG="))}
        b = []
        for k in range(3):
            si = lines[i + 1 + 2 * k].split()
            sp = lines[i + 2 + 2 * k].split()
            assert si[0] == f"SI{refs['XYZ'[k]]}" and si[1] == "H"
            assert sp == [f"SP{refs['XYZ'[k]]}", "0", "1"]
            b.append([float(si[2]), float(si[3])])
        sie = lines[i + 7].split()
        spe = lines[i + 8].split()
        assert sie[0] == f"SI{refs['ERG']}" and sie[1] == "L"
        groups = list(zip(map(float, sie[2:]), map(float, spe[1:])))
        cells.append({"wgt": w, "bounds": b, "groups": groups})
        i += 9
    return cells


def parse_serpent(text):
    cells = []
    for l in text.splitlines():
        if not l.startswith("src "):
            continue
        t = l.split()
        b = [[float(t[t.index(ax) + 1]), float(t[t.index(ax) + 2])]
             for ax in ("sx", "sy", "sz")]
        w = float(t[t.index("sw") + 1])
        groups = []
        if "sb" in t:
            k = t.index("sb")
            ne, intt = int(t[k + 1]), int(t[k + 2])
            assert intt == 0
            pairs = t[k + 3:k + 3 + 2 * ne]
            groups = [(float(pairs[j]), float(pairs[j + 1]))
                      for j in range(0, 2 * ne, 2)]
        cells.append({"wgt": w, "bounds": b, "groups": groups})
    return cells


# ---------- verification ----------

def verify(cells, parsed, *, weight_mode, energy_scale) -> list:
    problems = []
    if len(cells) != len(parsed):
        return [f"cell count {len(parsed)} != {len(cells)}"]
    total = naive_sum(c["photons_s"] for c in cells)
    for i, (c, p) in enumerate(zip(cells, parsed)):
        want = c["photons_s"] if weight_mode == "strength" \
            else c["photons_s"] / total
        got = p["strength"] if weight_mode == "strength" else p["wgt"]
        if got != want:
            problems.append(f"cell {i} strength {got} != {want}")
        if p["bounds"] != c["bounds_cm"]:
            problems.append(f"cell {i} bounds {p['bounds']} != "
                            f"{c['bounds_cm']}")
        exp = nprobs([s for _, s in c["_nz"]])
        exp_g = list(zip([e / (1.0 / energy_scale)
                          if energy_scale != 1.0 else e
                          for e, _ in c["_nz"]], exp))
        if p["groups"] != exp_g:
            problems.append(f"cell {i} groups differ")
        if c["_nz"] and p["groups"] and \
                abs(naive_sum(g[1] for g in p["groups"]) - 1.0) > 1e-14:
            problems.append(f"cell {i} probs sum off 1.0")
    return problems


def mutate_strength(text: str) -> str:
    m = re.search(r'strength="([0-9.]+)"', text)
    v = float(m.group(1))
    return text.replace(m.group(0), f'strength="{v * 2}"', 1)


def mutate_prob(text: str) -> str:
    # swap the two largest SP probs on the first SP line after SI*L
    lines = text.splitlines()
    for i, l in enumerate(lines):
        if l.startswith("SP") and " L" in lines[i - 1]:
            t = l.split()
            vals = [float(v) for v in t[1:]]
            big = sorted(range(len(vals)), key=lambda j: -vals[j])[:2]
            vals[big[0]], vals[big[1]] = vals[big[1]], vals[big[0]]
            t[1:] = [display(v) for v in vals]
            lines[i] = " ".join(t)
            return "\n".join(lines) + "\n"
    raise RuntimeError("no SP line")


def mutate_bound(text: str) -> str:
    return re.sub(r"sx 0 1", "sx 0 1.5", text, count=1)


def mutate_sigma_comment(text: str) -> str:
    return re.sub(r"sigma_independent=[0-9.]+", "sigma_independent=0.001",
                  text, count=1)


def check_file(path: Path, kind: str, cells, sha) -> list:
    text = path.read_text()
    problems = []
    if f"input_sha256={sha}" not in text:
        problems.append("input sha missing from provenance")
    if kind == "openmc":
        parsed, comments, raw = parse_openmc(text)
        problems += verify(cells, parsed, weight_mode="strength",
                           energy_scale=1.0)
        if len(comments) != len(cells):
            problems.append("provenance cell comments incomplete")
        for c in cells:
            if f"cell '{c['id']}'" not in raw:
                problems.append(f"cell {c['id']} missing provenance")
    elif kind == "mcnp":
        parsed = parse_mcnp(text)
        problems += verify(cells, parsed, weight_mode="weight",
                           energy_scale=1e-6)
    else:
        parsed = parse_serpent(text)
        problems += verify(cells, parsed, weight_mode="weight",
                           energy_scale=1e-6)
    return problems


def main() -> int:
    hdr, cells = load_doc()
    sha = hashlib.sha256(CORPUS.read_bytes()).hexdigest()
    problems = []

    missing = [k for k, p in OUTS.items() if not p.exists()]
    if missing:
        print(f"missing G3 outputs: {missing}")
        return 1

    for kind, path in OUTS.items():
        problems += [f"{kind}: {m}" for m in check_file(path, kind, cells, sha)]

    # sigma commentary matches the document, exactly
    omtext = OUTS["openmc"].read_text()
    for c in cells:
        want = (f"cell '{c['id']}' photons_s={display(c['photons_s'])} "
                f"sigma_independent={display(c['sigma_photons_s_independent'])} "
                f"sigma_conservative={display(c['sigma_photons_s_conservative'])}")
        if want not in omtext:
            problems.append(f"sigma comment for {c['id']} mismatched")

    # mutations must be caught — run each mutated artifact through the
    # same verification path
    def detect(kind: str, mutated: str) -> bool:
        if kind == "openmc":
            parsed, comments, raw = parse_openmc(mutated)
            ps = verify(cells, parsed, weight_mode="strength",
                        energy_scale=1.0)
            # sigma comment tamper also lives here
            for c in cells:
                want = (f"sigma_independent={display(c['sigma_photons_s_independent'])}")
                if want not in raw:
                    ps.append("sigma comment tampered")
            return bool(ps)
        if kind == "mcnp":
            return bool(verify(cells, parse_mcnp(mutated),
                               weight_mode="weight", energy_scale=1e-6))
        return bool(verify(cells, parse_serpent(mutated),
                           weight_mode="weight", energy_scale=1e-6))

    mutations = {}
    m = mutate_strength(OUTS["openmc"].read_text())
    mutations["strength_scaled"] = detect("openmc", m)
    m = mutate_prob(OUTS["mcnp"].read_text())
    mutations["prob_flipped"] = detect("mcnp", m)
    m = mutate_bound(OUTS["serpent"].read_text())
    mutations["bound_moved"] = detect("serpent", m)
    m = mutate_sigma_comment(OUTS["openmc"].read_text())
    mutations["sigma_comment_tampered"] = detect("openmc", m)

    for k, v in mutations.items():
        if not v:
            problems.append(f"mutation '{k}' NOT detected")

    evidence = {"schema": "actinv-p57-check-g5-1",
                "pass": not problems, "problems": problems,
                "mutations_detected": mutations}
    RESULT.write_text(json.dumps(evidence, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"pass": not problems, "problems": problems[:20],
                      "mutations": mutations}))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
