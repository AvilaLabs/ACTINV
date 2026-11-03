#!/usr/bin/env python3
"""P185 checker (protocols/ACTINV-P185_PROTOCOL.md). It reads the activation library, the v1.2.0 sidecar and the run
outputs with its own code (nothing from p185_harness.py or ACTINV) and recomputes every band.

  G0  The protocol's SHA-256 is in protocols/protocol_hash.txt.
  G1  Pins, builds (candidate at the registration commit, baseline at BASE, clean, exit 0), bounded scopes, outputs.
  G2  Baseline against candidate: nominals bit-identical; bands without a state-resolved parameter unchanged; no
      block newly excluded; the only rows newly covered are state-resolved.
  G3  Every band of every run equals the checker's own s^T C s.
  G4  Coverage flags, parameters_from_reaction_total, coverage and note; Fe53 (A) and Co58 (B) take the new rule.
  G5  Observation: relative standard deviations, baseline and candidate (recorded, not gated).
  G6  CI green on the evidence commit (checked after the push, not here).

  python3 controls/check_p185.py              check results/p185_record.json and target/p185-work, write the verdict
  python3 controls/check_p185.py --no-write   recompute and compare with the committed verdict
  python3 controls/check_p185.py --selftest
"""
import argparse
import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
PROTOCOL = ROOT / "protocols" / "ACTINV-P185_PROTOCOL.md"
RECORD = ROOT / "results" / "p185_record.json"
VERDICT = ROOT / "results" / "p185_verdict.json"
SCHEMA = "actinv-p185-verdict-1"
BASE = "b7629fb05213c15b21b01ace1adf9627d4aa93cb"
PINS = {
    "activation": "ec4c72bf598dc8ad3d533d9cfafdcf493e2d1f949a3e4db6251495659b68cc44",
    "cov_v120": "c19dec86b44ad5d90b66c9ab94d53e18641a1d354a89402a4da7986b6c530cde",
}
RESPONSES = {"A": ["Fe53", "Mn54", "Mn56", "Cr51", "Mn57"],
             "B": ["Co58", "Co57", "Ni57", "Co60", "Al26", "Na24", "W185", "W187", "Nb92m"]}
MUST_TAKE_RULE = {"A": "Fe53", "B": "Co58"}
STATE_RESOLVED = 10
UNCHANGED_TOL, BAND_TOL, BAND_ABS = 1e-12, 1e-6, 1e-12
NOTE = "isomer-branching (MF=40) uncertainty is not propagated"


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


# ------------------------------------------------------------------------------------------------- data, read-only
def npy(z, name):
    with z.open(name) as f:
        return np.lib.format.read_array(f)


class Library:
    """rows (target, MT, ZAP, LFS, LMF), ascending group bounds, and the cross sections of the rows asked for."""

    def __init__(self, path, wanted=None):
        with zipfile.ZipFile(path) as z:
            self.rows = npy(z, "rows.npy").astype(np.int64)
            self.bounds = npy(z, "bounds.npy").astype(float)
            sig = npy(z, "sig.npy")
        base = {}
        for k in np.flatnonzero(self.rows[:, 2] == -1):
            base[(int(self.rows[k, 0]), int(self.rows[k, 1]))] = int(k)
        self.base = base
        keep = set(range(len(self.rows))) if wanted is None else set(wanted)
        keep |= {base[(int(self.rows[k, 0]), int(self.rows[k, 1]))] for k in list(keep)
                 if (int(self.rows[k, 0]), int(self.rows[k, 1])) in base}
        self.sigma = {k: np.array(sig[k], dtype=float) for k in sorted(keep)}
        del sig


def sidecar(path):
    """{target: [component dict]} from the NPY members."""
    with zipfile.ZipFile(path) as z:
        comp, offsets, grids, values = (npy(z, "components.npy"), npy(z, "grid_offsets.npy"),
                                        npy(z, "grid_values.npy"), npy(z, "values.npy"))
    out = {}
    for row in comp:
        target, mt, mt1, lb, kind, rg, cg, off, n = (int(v) for v in row)
        out.setdefault(target, []).append({
            "target": target, "mt": mt, "mt1": mt1, "lb": lb, "kind": kind,
            "row_grid": np.asarray(grids[offsets[rg]:offsets[rg + 1]], dtype=float),
            "column_grid": np.asarray(grids[offsets[cg]:offsets[cg + 1]], dtype=float),
            "values": np.asarray(values[off:off + n], dtype=float)})
    return out


def self_keys(components):
    return {(c["target"], c["mt"]) for cs in components.values() for c in cs if c["mt"] == c["mt1"]}


def covered(row, keys, rule):
    """The coverage rule: a row whose (target, MT) has a self-covariance; the baseline also leaves out MF=10 rows."""
    target, mt, _, _, lmf = (int(v) for v in row)
    return (target, mt) in keys and (rule == "candidate" or lmf != STATE_RESOLVED)


def overlap(bounds, grid):
    """[group, bin] width of the overlap of library group and covariance interval."""
    lo, hi = bounds[:-1, None], bounds[1:, None]
    return np.clip(np.minimum(hi, grid[None, 1:]) - np.maximum(lo, grid[None, :-1]), 0.0, None)


# ----------------------------------------------------------------------------------------------------- the band
def ratio(library, row, key):
    """The row's cross section over its reaction total's, group by group (1 for the total itself)."""
    b = library.base[key]
    if b == row:
        return np.ones(len(library.bounds) - 1)
    s, t = library.sigma[row], library.sigma[b]
    if np.any((t <= 0.0) & (s != 0.0)):
        raise ValueError(f"row {row} is nonzero where its reaction total is zero")
    return np.where(t > 0.0, s / np.where(t > 0.0, t, 1.0), 0.0)


def variance(library, components, phi, sens, excluded):
    """(s^T C s, scale) for sensitivities {row: value} over covered rows, by the collapse's own definitions.

    The quadratic form is linear in the rows of one (target, MT), so each side is the sensitivity-weighted sum of its
    rows' vectors. scale is the same sum with absolute values, for the tolerance."""
    total = phi.sum()
    width = np.diff(library.bounds)
    d = np.where(phi > 0.0, phi / width / total, 0.0) if total > 0.0 else np.zeros_like(phi)
    by_key = {}
    for row, value in sens.items():
        key = (int(library.rows[row, 0]), int(library.rows[row, 1]))
        by_key.setdefault(key, []).append((row, value))
    rel, ab, rel_abs, ab_abs = {}, {}, {}, {}
    for key, items in by_key.items():
        rel[key] = sum(v * library.sigma[r] for r, v in items)
        rel_abs[key] = sum(abs(v) * library.sigma[r] for r, v in items)
    ov = {}
    var, scale = 0.0, 0.0
    for target in sorted({k[0] for k in by_key}):
        for c in components.get(target, []):
            left, right = (target, c["mt"]), (target, c["mt1"])
            if left not in by_key or right not in by_key:
                continue
            if (target, min(c["mt"], c["mt1"]), max(c["mt"], c["mt1"])) in excluded:
                continue
            twice = 2.0 if c["mt"] != c["mt1"] else 1.0
            if c["kind"] in (0, 1):
                vecs = []
                for key, grid in ((left, c["row_grid"]), (right, c["column_grid"])):
                    gk = grid.tobytes()
                    if gk not in ov:
                        ov[gk] = overlap(library.bounds, grid)
                    if c["kind"] == 1:
                        u, ua = rel[key], rel_abs[key]
                    else:
                        if key not in ab:
                            ab[key] = sum(v * ratio(library, r, key) for r, v in by_key[key])
                            ab_abs[key] = sum(abs(v) * ratio(library, r, key) for r, v in by_key[key])
                        u, ua = ab[key], ab_abs[key]
                    vecs.append(((d * u) @ ov[gk], (d * ua) @ ov[gk]))
                m = c["values"].reshape(len(c["row_grid"]) - 1, len(c["column_grid"]) - 1)
                var += twice * float(vecs[0][0] @ m @ vecs[1][0])
                scale += twice * float(vecs[0][1] @ np.abs(m) @ vecs[1][1])
            elif c["kind"] in (8, 9):
                grid, f = c["row_grid"], c["values"]
                gk = grid.tobytes()
                if gk not in ov:
                    ov[gk] = overlap(library.bounds, grid)
                seg = ov[gk]
                cw = np.diff(grid)
                if c["kind"] == 8:
                    s = (seg * (cw * f)[None, :]).sum(axis=1)
                else:
                    s = (seg ** 2 * f[None, :] * (1.0 - seg / cw[None, :])).sum(axis=1)
                for key in (left, right):
                    if key not in ab:
                        ab[key] = sum(v * ratio(library, r, key) for r, v in by_key[key])
                        ab_abs[key] = sum(abs(v) * ratio(library, r, key) for r, v in by_key[key])
                var += twice * float(np.sum(d ** 2 * s * ab[left] * ab[right]))
                scale += twice * float(np.sum(d ** 2 * np.abs(s) * ab_abs[left] * ab_abs[right]))
            else:
                raise ValueError(f"unknown component kind {c['kind']}")
    return var, scale


# ---------------------------------------------------------------------------------------------------- the runs
def spectrum(spec):
    s = spec["spectrum"]
    phi = np.asarray(s["flux_per_group"], dtype=float)
    return phi[::-1].copy() if s.get("descending") else phi


def responses(out):
    """[(step index, t_s, {response: entry}, block)] for steps with an uncertainty block."""
    rows = []
    for i, step in enumerate(out.get("steps", [])):
        block = step.get("uncertainty")
        if block:
            rows.append((i, step.get("t_s"), block.get("responses") or {}, block))
    return rows


def parameters_ok(library, entry, keys, rule):
    """Problems with the descriptors and coverage flags of one response's sensitivities."""
    bad = []
    for s in entry["sensitivities"]:
        p = s["parameter"]
        row = library.rows[p["library_row"]]
        if [p["target"], p["MT"], p["ZAP"], p["LFS"], p["LMF"]] != [int(v) for v in row]:
            bad.append(f"row {p['library_row']}: descriptor differs from the library")
        elif p["covariance_covered"] != covered(row, keys, rule):
            bad.append(f"row {p['library_row']}: covariance_covered {p['covariance_covered']} against the rule")
        if p["spectrum"] != 0:
            bad.append(f"row {p['library_row']}: spectrum {p['spectrum']}")
    return bad


def from_reaction_total(entry):
    return sum(1 for s in entry["sensitivities"]
               if s["parameter"]["covariance_covered"] and not s["parameter"]["covariance_excluded"]
               and s["parameter"]["LMF"] == STATE_RESOLVED and s["value"] != 0.0)


def check_run(library, components, keys, spec, out, rule, problem):
    """(G3 problems, G4 problems, worst relative band difference, {response: affected at every step})."""
    phi = spectrum(spec)
    g3, g4, worst, affected = [], [], 0.0, {}
    steps = responses(out)
    if len(steps) != len(spec["schedule"]):
        g3.append(f"{len(steps)} steps with a band, {len(spec['schedule'])} in the schedule")
    for i, t, block, whole in steps:
        excluded = {(b["target"], min(b["mt"], b["mt1"]), max(b["mt"], b["mt1"])) for b in whole["excluded_blocks"]}
        for name in RESPONSES[problem]:
            entry = block.get(f"activity:{name}")
            if not isinstance(entry, dict):
                g3.append(f"step {i}: no activity:{name}")
                continue
            g4 += [f"step {i} {name}: {x}" for x in parameters_ok(library, entry, keys, rule)[:5]]
            sens = {s["parameter"]["library_row"]: s["value"] for s in entry["sensitivities"]
                    if s["parameter"]["covariance_covered"] and s["value"] != 0.0}
            v, scale = variance(library, components, phi, sens, excluded)
            sd = entry["mf33_standard_uncertainty"]
            reported = sd * sd
            diff = abs(v - reported)
            if diff > BAND_TOL * max(abs(v), reported) + BAND_ABS * scale:
                g3.append(f"step {i} {name}: s^T C s {v:.9e}, reported {reported:.9e}")
            if max(abs(v), reported) > 0.0:
                worst = max(worst, diff / max(abs(v), reported))
            count = from_reaction_total(entry)
            affected[name] = affected.get(name, True) and count > 0
            if rule == "candidate":
                present = "parameters_from_reaction_total" in entry
                mf33 = next((c for c in entry["channels"] if c["channel"] == "cross_section_mf33"), {})
                if count:
                    if entry.get("parameters_from_reaction_total") != count:
                        g4.append(f"step {i} {name}: parameters_from_reaction_total "
                                  f"{entry.get('parameters_from_reaction_total')}, counted {count}")
                    if entry["coverage"] != "partial" or mf33.get("coverage") != "partial":
                        g4.append(f"step {i} {name}: coverage not partial")
                    if NOTE not in (mf33.get("note") or ""):
                        g4.append(f"step {i} {name}: no MF=40 note")
                elif present or mf33.get("note"):
                    g4.append(f"step {i} {name}: field or note present with no state-resolved parameter")
            elif "parameters_from_reaction_total" in entry:
                g4.append(f"step {i} {name}: the baseline reports parameters_from_reaction_total")
    return g3, g4, worst, affected


def compare(base, cand, library, problem):
    """G2 problems for one problem's baseline and candidate outputs."""
    bad = []
    bs, cs = responses(base), responses(cand)
    if [x[:2] for x in bs] != [x[:2] for x in cs]:
        return ["steps differ"]
    for (i, _, bblock, bwhole), (_, _, cblock, cwhole) in zip(bs, cs):
        bex = {(b["target"], b["mt"], b["mt1"], b["reason"]) for b in bwhole["excluded_blocks"]}
        cex = {(b["target"], b["mt"], b["mt1"], b["reason"]) for b in cwhole["excluded_blocks"]}
        if cex - bex:
            bad.append(f"step {i}: newly excluded {sorted(cex - bex)[:5]}")
        bun, cun = set(bwhole["uncovered_library_rows"]), set(cwhole["uncovered_library_rows"])
        if cun - bun:
            bad.append(f"step {i}: newly uncovered rows {sorted(cun - bun)[:5]}")
        if any(int(library.rows[r, 4]) != STATE_RESOLVED for r in bun - cun):
            bad.append(f"step {i}: a row that is not state-resolved became covered")
        for name in RESPONSES[problem]:
            b, c = bblock.get(f"activity:{name}"), cblock.get(f"activity:{name}")
            if not (isinstance(b, dict) and isinstance(c, dict)):
                bad.append(f"step {i} {name}: missing")
                continue
            if b["nominal"] != c["nominal"]:
                bad.append(f"step {i} {name}: nominal {b['nominal']!r} against {c['nominal']!r}")
            if not from_reaction_total(c):
                x, y = b["mf33_standard_uncertainty"], c["mf33_standard_uncertainty"]
                if abs(x - y) > UNCHANGED_TOL * max(abs(x), abs(y)):
                    bad.append(f"step {i} {name}: band {x!r} against {y!r} with no state-resolved parameter")
    return bad


def table(base, cand, problem):
    """{response: [(t_s, baseline relative SD, candidate relative SD)]}."""
    out = {}
    for (_, t, bblock, _), (_, _, cblock, _) in zip(responses(base), responses(cand)):
        for name in RESPONSES[problem]:
            b, c = bblock.get(f"activity:{name}") or {}, cblock.get(f"activity:{name}") or {}
            out.setdefault(name, []).append((t, b.get("relative_standard_uncertainty"),
                                             c.get("relative_standard_uncertainty")))
    return out


# ------------------------------------------------------------------------------------------------------- gates
def bounded(cg):
    try:
        quota, period = cg["cpu_max"].split()
        return (int(cg["memory_max"]) <= 6 << 30 and int(cg["memory_swap_max"]) == 0 and int(cg["pids_max"]) <= 128
                and int(quota) / int(period) <= 4.0 + 1e-9)
    except (TypeError, ValueError, KeyError, AttributeError):
        return False


def check():
    record = json.loads(RECORD.read_text())
    gates = {}
    digest = sha256(PROTOCOL)
    gates["G0"] = {"pass": digest in (ROOT / "protocols" / "protocol_hash.txt").read_text(), "sha256": digest}

    build, observe = record["build"], record["observe"]
    registration = subprocess.run(["git", "log", "--diff-filter=A", "--format=%H", "--",
                                   "protocols/ACTINV-P185_PROTOCOL.md"], capture_output=True, text=True,
                                  cwd=ROOT).stdout.split()
    runs = observe["runs"]
    g1 = {
        "pins": record["pins"] == PINS and record["base"] == BASE,
        "build_exit": build["candidate"]["exit"] == 0 and build["baseline"]["exit"] == 0,
        "registration_commit": bool(registration) and build["candidate"]["git_head"] == registration[-1]
        and observe["git_head"] == registration[-1],
        "baseline_commit": build["baseline"]["git_head"] == BASE,
        "clean_trees": build["candidate"]["git_dirty"] == "" and build["baseline"]["git_dirty"] == "",
        "bounded": bounded(build["cgroup"]) and bounded(observe["cgroup"]),
        "runs": sorted(runs) == ["baseline_A", "baseline_B", "candidate_A", "candidate_B"]
        and all(r["exit"] == 0 and r["binary_sha256"] == build[r["binary"]]["binary_sha256"] for r in runs.values()),
        "outputs": all(r["output_sha256"] and sha256(r["output"]) == r["output_sha256"]
                       and sha256(r["spec"]) == r["spec_sha256"] for r in runs.values()),
    }
    gates["G1"] = {"pass": all(g1.values()), **g1}
    if not g1["outputs"] or not g1["runs"]:
        return {"schema": SCHEMA, "gates": gates, "verdict": "P185-FAIL", "record_sha256": sha256(RECORD)}

    specs = {k: json.loads(Path(r["spec"]).read_text()) for k, r in runs.items()}
    if any(sha256(s["library"]["path"]) != PINS["activation"] or sha256(s["uncertainty"]["covariance"]["path"])
           != PINS["cov_v120"] for s in specs.values()):
        raise SystemExit("check_p185: a run's library or sidecar is not the pinned file")
    outs = {k: json.loads(Path(r["output"]).read_text()) for k, r in runs.items()}
    wanted = {s["parameter"]["library_row"] for out in outs.values() for _, _, block, _ in responses(out)
              for e in block.values() for s in e.get("sensitivities", [])}
    library = Library(specs["candidate_A"]["library"]["path"], wanted)
    components = sidecar(specs["candidate_A"]["uncertainty"]["covariance"]["path"])
    keys = self_keys(components)

    g2, g3, g4, worst, affected, observed = [], [], [], 0.0, {}, {}
    for problem in RESPONSES:
        g2 += [f"{problem}: {x}" for x in compare(outs[f"baseline_{problem}"], outs[f"candidate_{problem}"], library,
                                                   problem)]
        for rule in ("baseline", "candidate"):
            name = f"{rule}_{problem}"
            p3, p4, w, aff = check_run(library, components, keys, specs[name], outs[name], rule, problem)
            g3 += [f"{name}: {x}" for x in p3]
            g4 += [f"{name}: {x}" for x in p4]
            worst = max(worst, w)
            if rule == "candidate":
                affected[problem] = sorted(n for n, a in aff.items() if a)
        observed[problem] = table(outs[f"baseline_{problem}"], outs[f"candidate_{problem}"], problem)
    for problem, nuclide in MUST_TAKE_RULE.items():
        if nuclide not in affected.get(problem, []):
            g4.append(f"{problem}: {nuclide} does not take the new rule at every step")
    gates["G2"] = {"pass": not g2, "problems": g2[:20]}
    gates["G3"] = {"pass": not g3, "problems": g3[:20], "worst_relative_difference": worst}
    gates["G4"] = {"pass": not g4, "problems": g4[:20], "affected_responses": affected}
    gates["G5"] = {"pass": True, "observations": observed}
    gates["G6"] = {"pass": None, "note": "CI green on the evidence commit, checked after the push"}
    verdict = {"schema": SCHEMA, "gates": gates, "record_sha256": sha256(RECORD)}
    passed = all(gates[g]["pass"] for g in ("G0", "G1", "G2", "G3", "G4", "G5"))
    verdict["verdict"] = "P185-PASS (pending G6)" if passed else "P185-FAIL"
    if passed:
        verdict["allowed_sentences"] = sentences(observed, affected)
    return verdict


def sentences(observed, affected):
    out = ["In ACTINV's MF=33 band, a state-resolved (MF=10) product takes its reaction's covariance, fully "
           "correlated with the reaction total; its isomer-branching (MF=40) uncertainty is not propagated, and the "
           "band's coverage is reported partial."]
    materials = {"A": "iron", "B": "a nickel, cobalt, aluminium, tungsten and niobium mixture"}
    for problem, rows in observed.items():
        for nuclide in affected[problem]:
            t, a, b = rows[nuclide][1]
            out.append(f"For {materials[problem]} in the FNS spectrum after 5 minutes' irradiation, the "
                       f"cross-section part of the relative standard deviation of the {nuclide} activity 66 s after "
                       f"shutdown is {100 * a:.3g}% without state-resolved coverage and {100 * b:.3g}% with it "
                       "(TENDL-2025 covariances, isomer branching not included).")
    return out


# ---------------------------------------------------------------------------------------------------- selftest
def brute(library, components, phi, sens, excluded):
    """The collapse written out loop by loop, as the runtime does it: C over the rows, then s^T C s."""
    rows = sorted(sens)
    n, total = len(rows), phi.sum()
    c = np.zeros((n, n))
    bounds = library.bounds
    for target, comps in components.items():
        for comp in comps:
            if (target, min(comp["mt"], comp["mt1"]), max(comp["mt"], comp["mt1"])) in excluded:
                continue
            for i, li in enumerate(rows):
                for j, rj in enumerate(rows):
                    if (int(library.rows[li, 0]), int(library.rows[li, 1])) != (target, comp["mt"]) or \
                            (int(library.rows[rj, 0]), int(library.rows[rj, 1])) != (target, comp["mt1"]):
                        continue
                    value = 0.0
                    if comp["kind"] in (0, 1):
                        vec = []
                        for row, grid in ((li, comp["row_grid"]), (rj, comp["column_grid"])):
                            key = (int(library.rows[row, 0]), int(library.rows[row, 1]))
                            mult = library.sigma[row] if comp["kind"] == 1 else ratio(library, row, key)
                            v = np.zeros(len(grid) - 1)
                            for g in range(len(phi)):
                                dens = phi[g] / (bounds[g + 1] - bounds[g]) / total
                                for b in range(len(grid) - 1):
                                    w = max(min(bounds[g + 1], grid[b + 1]) - max(bounds[g], grid[b]), 0.0)
                                    v[b] += dens * w * mult[g]
                            vec.append(v)
                        m = comp["values"].reshape(len(comp["row_grid"]) - 1, len(comp["column_grid"]) - 1)
                        value = vec[0] @ m @ vec[1]
                    else:
                        grid, f = comp["row_grid"], comp["values"]
                        lr = ratio(library, li, (target, comp["mt"]))
                        rr = ratio(library, rj, (target, comp["mt1"]))
                        for g in range(len(phi)):
                            gw = bounds[g + 1] - bounds[g]
                            for b in range(len(grid) - 1):
                                seg = max(min(bounds[g + 1], grid[b + 1]) - max(bounds[g], grid[b]), 0.0)
                                if seg == 0.0:
                                    continue
                                cw = grid[b + 1] - grid[b]
                                lw = phi[g] * seg / gw / total
                                var = f[b] * cw / seg if comp["kind"] == 8 else f[b] * (1.0 - seg / cw)
                                value += lw * lw * lr[g] * rr[g] * var
                    c[i, j] += value
                    if comp["mt"] != comp["mt1"]:
                        c[j, i] += value
    s = np.array([sens[r] for r in rows])
    return float(s @ c @ s)


def selftest():
    rng = np.random.default_rng(185)

    class Lib:
        pass
    lib = Lib()
    lib.bounds = np.array([1.0, 2.0, 3.5, 5.0, 8.0])
    # target 0: MT 16 total + two MF=10 states; MT 102 total; target 1: MT 103 total
    lib.rows = np.array([[0, 16, -1, -1, 0], [0, 16, 26053, 0, 10], [0, 16, 26053, 1, 10], [0, 102, -1, -1, 0],
                         [1, 103, -1, -1, 0]], dtype=np.int64)
    lib.sigma = {0: np.array([2.0, 4.0, 3.0, 1.0]), 1: np.array([1.5, 1.0, 2.0, 0.25]),
                 2: np.array([0.5, 3.0, 1.0, 0.75]), 3: np.array([0.3, 0.2, 0.1, 0.05]),
                 4: np.array([0.0, 1.0, 2.0, 3.0])}
    lib.base = {(0, 16): 0, (0, 102): 3, (1, 103): 4}
    g1, g2 = np.array([1.0, 3.0, 5.0, 8.0]), np.array([1.0, 2.5, 8.0])
    a = rng.normal(size=(3, 3))
    comps = {0: [{"target": 0, "mt": 16, "mt1": 16, "kind": 1, "row_grid": g1, "column_grid": g1,
                  "values": (a @ a.T).ravel() * 0.01},
                 {"target": 0, "mt": 16, "mt1": 102, "kind": 1, "row_grid": g1, "column_grid": g2,
                  "values": rng.normal(size=6) * 0.002},
                 {"target": 0, "mt": 102, "mt1": 102, "kind": 0, "row_grid": g2, "column_grid": g2,
                  "values": np.array([0.004, 0.001, 0.001, 0.002])},
                 {"target": 0, "mt": 16, "mt1": 16, "kind": 8, "row_grid": np.array([1.0, 4.0, 8.0]),
                  "column_grid": np.array([1.0, 4.0, 8.0]), "values": np.array([0.03, 0.01])}],
             1: [{"target": 1, "mt": 103, "mt1": 103, "kind": 9, "row_grid": g2, "column_grid": g2,
                  "values": np.array([0.02, 0.05])}]}
    phi = np.array([1.0, 0.0, 2.0, 0.5])
    sens = {0: 0.7, 1: -1.3, 2: 2.1, 3: 0.4, 4: -0.9}
    for excluded in (set(), {(0, 16, 102)}, {(0, 16, 16)}):
        v, scale = variance(lib, comps, phi, sens, excluded)
        b = brute(lib, comps, phi, sens, excluded)
        assert abs(v - b) <= 1e-12 * max(abs(b), 1e-300) + 1e-15, (v, b, excluded)
        assert scale >= abs(v) - 1e-15
    # the state rows summing to the total make the total's band the band of the states' sum
    v_total, _ = variance(lib, {0: comps[0][:1]}, phi, {0: 1.0}, set())
    v_states, _ = variance(lib, {0: comps[0][:1]}, phi, {1: 1.0, 2: 1.0}, set())
    assert abs(v_total - v_states) <= 1e-12 * v_total
    keys = self_keys(comps)
    assert covered(lib.rows[1], keys, "candidate") and not covered(lib.rows[1], keys, "baseline")
    assert covered(lib.rows[0], keys, "baseline") and not covered(np.array([2, 16, -1, -1, 0]), keys, "candidate")
    lib.sigma[5] = np.array([1.0, 0.0, 0.0, 0.0])
    lib.rows = np.vstack([lib.rows, [1, 103, 1001, 0, 10]])
    try:
        ratio(lib, 5, (1, 103))
        raise AssertionError("a row nonzero where its total is zero must be refused")
    except ValueError:
        pass
    assert bounded({"memory_max": str(6 << 30), "memory_swap_max": "0", "pids_max": "128", "cpu_max": "400000 100000"})
    assert not bounded({"memory_max": "max", "memory_swap_max": "0", "pids_max": "128", "cpu_max": "400000 100000"})
    assert spectrum({"spectrum": {"flux_per_group": [1, 2, 3], "descending": True}}).tolist() == [3.0, 2.0, 1.0]
    print("check_p185 selftest: PASS")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        selftest()
        return 0
    verdict = check()
    text = json.dumps(verdict, indent=1, sort_keys=True) + "\n"
    if args.no_write:
        same = VERDICT.is_file() and VERDICT.read_text() == text
        print(f"{verdict['verdict']}; committed verdict {'matches' if same else 'DIFFERS'}")
        return 0 if same else 1
    VERDICT.write_text(text)
    for gate, body in verdict["gates"].items():
        print(gate, body["pass"])
    print(verdict["verdict"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
