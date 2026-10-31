#!/usr/bin/env python3
"""P175 checker (protocols/ACTINV-P175_PROTOCOL.md): the first-order shutdown-dose source covariance of
`actinv export-r2s-cov` against brute-force sampling. Reads results/p175_r2s_cov.json and the artifacts under
results/p175/ written by controls/p175_r2s_cov.py. G0-G3 are evaluated here; G4 (CI on the pushed evidence commit) is
external. Every statistic is recomputed from the stored covariance documents and sampled photon sources with the
registered formulas of controls/p175_stats.py; nothing is read from the harness' own arithmetic.

  G0  The protocol's SHA-256 (and any protocols/ACTINV-P175_AMENDMENT_*.md) is in protocols/protocol_hash.txt.
  G1  Pins: the registered constants, the input files, the source binary, the crates tree of the source commit, a clean
      tracked tree and the bounded cgroup.
  G2  Fidelity: every registered case once, no failed process, artifacts reproduce their hashes, the exported mean
      equals the nominal re-solve, regions are banded to the registered coverage, and for every scored (case, step,
      response) row |V_first_order / V_sampled - 1| <= k SE_rel + delta_lin; the per-pair total-strength correlations
      agree with the samples.
  G3  The factor columns of the MF=33 channel reproduce the joint covariance of `actinv export-r2s-joint` (cell by
      cell, total photon strength) to 1e-10, plus the pivoted-Cholesky truncation the document reports.

Verdict: P175-PASS if G0-G3 hold (G4 external). The allowed sentence is written only then.

Usage: check_p175.py [--record FILE] [--no-write]
       check_p175.py --self-test
"""
import argparse
import copy
import gzip
import hashlib
import json
import math
import random
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "controls"))
import p175_stats as st  # noqa: E402

PROTOCOL = ROOT / "protocols" / "ACTINV-P175_PROTOCOL.md"
HASH_LOG = ROOT / "protocols" / "protocol_hash.txt"
RECORD = ROOT / "results" / "p175_r2s_cov.json"
VERDICT = ROOT / "results" / "p175_verdict.json"
if st.REHEARSAL:
    RECORD = ROOT / "target" / "p175-rehearsal" / "p175_r2s_cov.json"
    VERDICT = ROOT / "target" / "p175-rehearsal" / "p175_verdict.json"

PINS = {
    "binary_sha256": "9e52e19a76034fa038ac837e926077100d1df81dd99e3c441b19535c9bab8ee9",  # the release build that runs the evidence
    "source_commit": "327af82f23646ee93f3c64a78d336411dfc43042",  # commit whose crates tree built that binary
    "inputs": {
        "base_flux_sha256": "b9b1e8d1a713292507611b17be5afaaa671d734383cf7a0b1d1a68b31311b7e7",
        "fns_spec_sha256": "ed55ed00f6c00fc4a2351e18332e98e7feb33d9b7ca65e7ba861e0e5e8feef39",
        "library_sha256": "ec4c72bf598dc8ad3d533d9cfafdcf493e2d1f949a3e4db6251495659b68cc44",
        "covariance_sha256": "c19dec86b44ad5d90b66c9ab94d53e18641a1d354a89402a4da7986b6c530cde",
    },
}
GIB = 1 << 30
GUI_CRATE = "crates/actinv-gui/"
MEAN_RTOL = 1e-9


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=60).stdout.strip()


def changed_crate_paths(source_commit, head):
    if not head:
        return []
    return git("diff", "--name-only", source_commit, head, "--", "crates").split()


def g0():
    if not PROTOCOL.is_file():
        return [f"missing {PROTOCOL.name}"]
    logged = HASH_LOG.read_text()
    return [f"{path.name} hash not in protocol_hash.txt"
            for path in [PROTOCOL, *sorted(PROTOCOL.parent.glob("ACTINV-P175_AMENDMENT_*.md"))]
            if f"{sha256(path)}  protocols/{path.name}" not in logged]


def cgroup_ok(cg):
    try:
        quota, period = cg["cpu.max"].split()
        return (int(cg["memory.max"]) <= 6 * GIB and cg["memory.swap.max"] == "0" and int(cg["pids.max"]) <= 128
                and quota != "max" and int(quota) <= 2 * int(period))
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def g1(record, *, pins, source_tree=None):
    problems = []
    if record.get("schema") != st.SCHEMA:
        problems.append("wrong schema")
    if record.get("constants") != json.loads(json.dumps(st.constants())):
        problems.append("recorded constants are not the registered ones")
    environment = record.get("environment", {})
    if environment.get("pins") != pins["inputs"]:
        problems.append("input pins in the record are not the registered ones")
    if len(pins["binary_sha256"]) != 64:
        problems.append("source binary is not registered yet")
    elif environment.get("binary_sha256") != pins["binary_sha256"]:
        problems.append("binary is not the registered source binary")
    if pins["source_commit"].startswith("TO-BE") and source_tree is None:
        problems.append("source commit is not registered yet")
    else:
        expected = source_tree if source_tree is not None else git("rev-parse", f"{pins['source_commit']}:crates")
        if not expected or environment.get("crates_tree") != expected:
            changed = changed_crate_paths(pins["source_commit"], environment.get("git_head", ""))
            if not changed or any(not path.startswith(GUI_CRATE) for path in changed):
                problems.append(f"crates tree differs from {pins['source_commit']}'s outside {GUI_CRATE}")
    if not environment.get("clean_tree"):
        problems.append("tracked tree was not clean")
    if not cgroup_ok(environment.get("cgroup", {})):
        problems.append("harness cgroup is not the bounded scope")
    return problems


def finite(value):
    return isinstance(value, (int, float)) and math.isfinite(value)


def load_artifact(base, entry, what, problems):
    """Parsed content of a recorded artifact, or None with a problem when it is missing or altered."""
    if not isinstance(entry, dict) or "path" not in entry:
        problems.append(f"{what}: no artifact recorded")
        return None
    path = Path(base) / entry["path"]
    if not path.is_file():
        problems.append(f"{what}: {entry['path']} is missing")
        return None
    if sha256(path) != entry.get("sha256"):
        problems.append(f"{what}: {entry['path']} does not reproduce its recorded hash")
        return None
    return path


def case_rows(case, cov_by_step, samples, delta_extra, where, problems):
    """Rows and pair rows of one case; structural problems are appended to `problems`."""
    rows, pairs = [], []
    n_cells = cov_by_step[st.STEPS[0]]["n_cells"]
    for step in st.STEPS:
        cov, series = cov_by_step[step], samples["samples"][str(step)]
        nominal = samples["nominal"][str(step)]
        if cov["bounds"] != samples["group_bounds_eV"]:
            problems.append(f"{where} step {step}: photon group bounds differ between the covariance and the samples")
            continue
        if len(series) != st.N_SAMPLES or any(len(s) != n_cells for s in series):
            problems.append(f"{where} step {step}: {len(series)} samples or a wrong cell count")
            continue
        scale = max((abs(x) for row in nominal for x in row), default=0.0)
        diff = max((abs(a - b) for ra, rb in zip(cov["S"], nominal) for a, b in zip(ra, rb)), default=0.0)
        if not diff <= MEAN_RTOL * scale:
            problems.append(f"{where} step {step}: exported mean differs from the nominal re-solve by {diff:.3g}")
        for c in range(n_cells):
            total = math.fsum(math.fsum(row) for row in [cov["S"][c]])
            if total > 0 and not cov["unbanded"][c] <= st.UNBANDED_MAX * total:
                problems.append(f"{where} step {step}: cell {c} has {cov['unbanded'][c] / total:.2e} of its photon "
                                f"source unbanded (limit {st.UNBANDED_MAX})")
        for name in st.RESPONSES:
            r = st.response(name, cov["bounds"], n_cells)
            v1 = st.first_order(cov, r)[0]
            d0 = st.dot(r, cov["S"])
            row = st.evaluate_row(v1, d0, [st.dot(r, s) for s in series], delta_extra)
            row.update(case=case["name"], step=step, response=name)
            row["channel_variance"] = st.channel_variances(cov, r)
            rows.append(row)
        for pair in st.pair_rows(cov, [[math.fsum(cell) for cell in s] for s in series]):
            pair.update(case=case["name"], step=step)
            pairs.append(pair)
    return rows, pairs


def joint_problems(case, cov_by_step, record_case, where):
    """G3 for one case: MF=33 factor columns against the joint export."""
    problems = []
    for step in st.STEPS:
        joint = record_case.get("joint", {}).get(str(step))
        if joint is None:
            problems.append(f"{where} step {step}: no joint export recorded")
            continue
        cov = cov_by_step[step]
        detail = st.mf33_detail(cov) or {}
        tf = detail.get("truncated_trace_fraction") or 0.0
        order = detail.get("dense_order") or 0
        c_t = st.total_strength_covariance(cov, channel="cross_section_mf33")
        trace = math.fsum(x * x for rec in cov["shared"] if rec["channel"] == "cross_section_mf33"
                          for row in rec["column"] for x in row)
        allowance = order * tf * trace / max(1.0 - tf, 1e-300)
        n = cov["n_cells"]
        sigma, rho = joint["cell_sigma_correlated"], joint["rho"]
        if len(sigma) != n or len(rho) != n:
            problems.append(f"{where} step {step}: joint export has {len(sigma)} cells, covariance {n}")
            continue
        total = sum(c_t[i][j] for i in range(n) for j in range(n))
        if abs(total - joint["sigma_total_correlated"] ** 2) > st.G3_RTOL * total + allowance:
            problems.append(f"{where} step {step}: total MF=33 variance {total:.12g} vs joint "
                            f"{joint['sigma_total_correlated'] ** 2:.12g}")
        for i in range(n):
            for j in range(n):
                joint_cov = (rho[i][j] * sigma[i] * sigma[j]) if rho[i][j] is not None else 0.0
                scale = sigma[i] * sigma[j]
                if abs(c_t[i][j] - joint_cov) > st.G3_RTOL * scale + allowance:
                    problems.append(f"{where} step {step}: MF=33 covariance of cells {i},{j} is {c_t[i][j]:.12g}, "
                                    f"joint export gives {joint_cov:.12g}")
    return problems


def g2_g3(record, *, base=ROOT):
    """(G2 problems, G3 problems, rows, pair rows, per-case notes)."""
    p2, p3, rows, pairs, notes = [], [], [], [], []
    by_name = {}
    for entry in record.get("cases", []):
        by_name.setdefault(entry.get("name"), []).append(entry)
    for case in st.CASES:
        found = by_name.get(case["name"], [])
        if len(found) != 1:
            p2.append(f"{case['name']}: {len(found)} records")
            continue
        entry, where = found[0], case["name"]
        local = []
        if entry.get("failures"):
            p2.append(f"{where}: a process failed: {entry['failures'][0].get('stage')}")
            continue
        channels = entry.get("channels")
        if case["material"] == "tungsten" and "cross_section_mf33" in case["channels"]:
            rank = entry.get("mf33_rank")
            substituted = bool(entry.get("substituted"))
            if substituted != (not rank):
                local.append("the tungsten substitution does not follow from the MF=33 rank")
            expected = st.D_FALLBACK_CHANNELS if substituted else case["channels"]
        else:
            expected = case["channels"]
        if channels != expected:
            local.append(f"channels {channels}, expected {expected}")
        if entry.get("material") != case["material"]:
            local.append("wrong material")
        if entry.get("band_cap_hit"):
            local.append("the banded-nuclide cap was hit")
        if not entry.get("responses"):
            local.append("no banded responses recorded")
        artifacts = entry.get("artifacts", {})
        cov_by_step = {}
        for step in st.STEPS:
            path = load_artifact(base, artifacts.get("cov", {}).get(str(step)), f"{where} step {step} covariance",
                                 local)
            if path is not None:
                try:
                    cov_by_step[step] = st.parse_cov(st.read_text(path))
                except (ValueError, KeyError, OSError) as error:
                    local.append(f"{where} step {step}: covariance unreadable: {error}")
        samples = None
        path = load_artifact(base, artifacts.get("samples"), f"{where} samples", local)
        if path is not None:
            try:
                samples = json.loads(st.read_text(path))
            except (ValueError, OSError) as error:
                local.append(f"{where}: samples unreadable: {error}")
        if samples is not None and (samples.get("schema") != st.SAMPLES_SCHEMA or samples.get("case") != where
                                    or samples.get("n") != st.N_SAMPLES):
            local.append(f"{where}: samples file is not the registered {st.N_SAMPLES}-sample set")
            samples = None
        for step, cov in cov_by_step.items():
            names = {rec["channel"] for rec in cov["shared"]}
            allowed = {c for c in channels or [] if c != "flux"}
            if not names <= allowed:
                local.append(f"{where} step {step}: shared columns of channels {sorted(names - allowed)} were not asked")
            if ("flux" in (channels or [])) != bool(cov["independent"]):
                local.append(f"{where} step {step}: independent flux columns do not follow the channels")
        delta_extra = 0.0
        if "cross_section_mf33" in (channels or []):
            clipped = (entry.get("sampler") or {}).get("xs_clipped")
            if not (finite(clipped) and clipped >= 0.0):
                local.append(f"{where}: the sampler's clipped eigenvalue mass is not recorded")
            else:
                delta_extra = clipped
        if not local and len(cov_by_step) == len(st.STEPS) and samples is not None:
            case_rows_, case_pairs = case_rows(case, cov_by_step, samples, delta_extra, where, local)
            rows += case_rows_
            pairs += case_pairs
            for name in st.RESPONSES:
                if not any(r["response"] == name and r["scored"] for r in case_rows_):
                    local.append(f"{where}: no scored row for response {name}")
            for r in case_rows_:
                if r["scored"] and not r["pass"]:
                    local.append(f"{where} step {r['step']} {r['response']}: V1/Vs = {r['ratio']:.4g}, deviation "
                                 f"{r['deviation']:.3f} > limit {r['limit']:.3f}")
            for pr in case_pairs:
                if not pr["pass"]:
                    local.append(f"{where} step {pr['step']} cells {pr['pair']}: correlation {pr['rho_first_order']:.3f} "
                                 f"first order vs {pr['rho_sampled']:.3f} sampled (limit {pr['limit']:.3f})")
            if case["joint"] and "cross_section_mf33" in channels:
                p3 += joint_problems(case, cov_by_step, entry, where)
            notes.append({"case": where, "channels": channels, "substituted": bool(entry.get("substituted")),
                          "pruned_states_changed_samples": entry.get("pruned_states_changed_samples")})
        p2 += local
    extra = [n for n in by_name if n not in st.BY_NAME]
    if extra:
        p2.append(f"unregistered cases {extra}")
    return p2, p3, rows, pairs, notes


def summary(rows, pairs, notes):
    scored = [r for r in rows if r["scored"] and r.get("deviation") is not None]
    worst = max(scored, key=lambda r: r["deviation"]) if scored else None
    ses = [r["se_rel"] for r in rows]
    result = {
        "rows": [{k: r[k] for k in ("case", "step", "response", "scored", "reason", "D0", "V_first_order",
                                    "V_sampled", "ratio", "deviation", "limit", "se_rel", "rho_D", "delta_lin",
                                    "pass", "channel_variance") if k in r} for r in rows],
        "pairs": pairs, "cases": notes, "scored_rows": len(scored), "unscored_rows": len(rows) - len(scored),
        "worst_deviation": None if worst is None else {k: worst[k] for k in ("case", "step", "response", "deviation")},
        "se_rel_range": [min(ses), max(ses)] if ses else None,
    }
    sentences = []
    if worst is not None:
        x = math.ceil(100.0 * worst["deviation"])
        sentences.append(f"ACTINV's exported shutdown-dose source covariance reproduces the sampled dose variance within "
                         f"{x}% for the tested cases.")
        sentences.append(f"Each case was sampled {st.N_SAMPLES} times, so each sampled variance is itself uncertain by "
                         f"{100 * min(ses):.0f}-{100 * max(ses):.0f}%; the agreement is {len(scored)} (case, step, response) rows "
                         f"within {st.K_SIGMA:g} sampling errors plus a registered linearisation allowance.")
        tungsten = next((n for n in notes if n["case"].startswith("D_")), None)
        if tungsten is not None:
            sentences.append("The tungsten case propagated MF=33 cross sections, half-lives and flux-tally noise."
                             if not tungsten["substituted"] else
                             "The tungsten case did not propagate MF=33 (no usable covariance block); it covers half-lives "
                             "and flux-tally noise only.")
        sentences.append("The tested channels are MF=33 cross sections, half-lives and flux-tally noise, to first order, "
                         "without the photon-tally term; fission yields were not exercised and the cases are synthetic.")
    return result, sentences


def evaluate(record, *, pins=PINS, base=ROOT, source_tree=None, use_g0=True):
    p2, p3, rows, pairs, notes = g2_g3(record, base=base)
    gates = {"G0": g0() if use_g0 else [], "G1": g1(record, pins=pins, source_tree=source_tree), "G2": p2, "G3": p3}
    result, sentences = summary(rows, pairs, notes) if rows else ({}, [])
    if any(gates.values()):
        sentences = []
    return gates, result, sentences


# ---------------------------------------------------------------------------------------------------------- selftest

SYN_BOUNDS = [1.0e4, 5.0e5, 1.0e6, 3.0e6]


def _syn_cov(channels, rng, n_cells=4, ng=3, mf33_scale=1.0):
    """A synthetic cov document: shared columns per nuclear channel, one independent column set per cell for flux."""
    s = [[1000.0 * (c + 1) * (g + 1) for g in range(ng)] for c in range(n_cells)]
    shared = []
    for ch in channels:
        if ch == "flux":
            continue
        for k in range(2):
            col = [[(0.04 if ch == "cross_section_mf33" else 0.02) * mf33_scale * s[c][g] * (1 + 0.3 * k)
                    * (1 if (c + g + k) % 2 else 0.5) for g in range(ng)] for c in range(n_cells)]
            shared.append({"record": "shared", "label": f"{ch}:{k}", "channel": ch, "column": col})
    independent = {}
    if "flux" in channels:
        for c in range(n_cells):
            independent[c] = [[0.05 * s[c][g] * (1 if g == j else 0.2) for g in range(ng)] for j in range(2)]
    regions = [{"ordinal": c, "id": st.CELL_IDS[c]} for c in range(n_cells)]
    header = {"record": "header", "schema": "actinv-r2s-cov-1", "group_bounds_eV": SYN_BOUNDS,
              "channels": [{"channel": "cross_section_mf33", "block": "shared",
                            "detail": {"rank": 2, "dense_order": 6, "truncated_trace_fraction": 0.0}}]
              if "cross_section_mf33" in channels else []}
    return {"header": header, "S": s, "regions": regions, "unbanded": [0.0] * n_cells, "shared": shared,
            "independent": independent, "footer": {}, "bounds": SYN_BOUNDS, "n_cells": n_cells}


def _write_cov(path, cov):
    lines = [json.dumps(cov["header"]),
             json.dumps({"record": "mean", "regions": cov["regions"], "S": cov["S"],
                         "unbanded_photons_s": cov["unbanded"]})]
    lines += [json.dumps(rec) for rec in cov["shared"]]
    for c, cols in cov["independent"].items():
        lines.append(json.dumps({"record": "independent", "ordinal": c, "id": st.CELL_IDS[c], "channel": "flux",
                                 "columns": cols}))
    lines.append(json.dumps({"record": "footer"}))
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        stream.write("\n".join(lines) + "\n")


def _draw_samples(cov, rng, n):
    out = []
    for _ in range(n):
        s = [row[:] for row in cov["S"]]
        for rec in cov["shared"]:
            z = rng.gauss(0.0, 1.0)
            for c, row in enumerate(rec["column"]):
                for g, x in enumerate(row):
                    s[c][g] += z * x
        for c, cols in cov["independent"].items():
            for col in cols:
                z = rng.gauss(0.0, 1.0)
                for g, x in enumerate(col):
                    s[c][g] += z * x
        out.append(s)
    return out


def _synthetic(folder):
    """A passing record with its artifacts under `folder`."""
    rng = random.Random(175)
    cases = []
    for case in st.CASES:
        cov = _syn_cov(case["channels"], rng)
        entry = {"name": case["name"], "material": case["material"], "channels": list(case["channels"]),
                 "substituted": False, "failures": [], "responses": ["Mn56"], "joint": {},
                 "artifacts": {"cov": {}}, "pruned_states_changed_samples": 0}
        if "cross_section_mf33" in case["channels"]:
            entry["sampler"] = {"xs_clipped": 0.0}
        if case["material"] == "tungsten" and "cross_section_mf33" in case["channels"]:
            entry["mf33_rank"] = 2
        series = _draw_samples(cov, rng, st.N_SAMPLES)
        for step in st.STEPS:
            path = Path(folder) / f"{case['name']}_step{step}_cov.ndjson.gz"
            _write_cov(path, cov)
            entry["artifacts"]["cov"][str(step)] = {"path": path.name, "sha256": sha256(path)}
            if case["joint"]:
                c_t = st.total_strength_covariance(cov, channel="cross_section_mf33")
                sigma = [math.sqrt(c_t[i][i]) for i in range(cov["n_cells"])]
                entry["joint"][str(step)] = {
                    "cell_sigma_correlated": sigma,
                    "rho": [[c_t[i][j] / (sigma[i] * sigma[j]) for j in range(cov["n_cells"])]
                            for i in range(cov["n_cells"])],
                    "sigma_total_correlated": math.sqrt(sum(sum(r) for r in c_t))}
        doc = {"schema": st.SAMPLES_SCHEMA, "case": case["name"], "n": st.N_SAMPLES,
               "cells": list(st.CELL_IDS), "group_bounds_eV": SYN_BOUNDS,
               "nominal": {str(step): cov["S"] for step in st.STEPS},
               "samples": {str(step): series for step in st.STEPS}}
        path = Path(folder) / f"{case['name']}_samples.json.gz"
        with gzip.open(path, "wt", encoding="utf-8") as stream:
            stream.write(json.dumps(doc))
        entry["artifacts"]["samples"] = {"path": path.name, "sha256": sha256(path)}
        cases.append(entry)
    return {"schema": st.SCHEMA, "protocol": "ACTINV-P175",
            "environment": {"binary_sha256": "b" * 64, "crates_tree": "tree", "clean_tree": True,
                            "pins": PINS["inputs"],
                            "cgroup": {"memory.max": str(6 * GIB), "memory.swap.max": "0", "pids.max": "128",
                                       "cpu.max": "200000 100000"}},
            "constants": json.loads(json.dumps(st.constants())), "cases": cases}


def _rewrite_cov(folder, entry, mutate):
    for step in st.STEPS:
        info = entry["artifacts"]["cov"][str(step)]
        path = Path(folder) / info["path"]
        cov = st.parse_cov(st.read_text(path))
        mutate(cov)
        _write_cov(path, cov)
        info["sha256"] = sha256(path)


def _stats_tests(failures):
    # variance of an exactly known sample, the SE formula and the row tolerance
    mean, var, excess = st.moments([1.0, 2.0, 3.0, 4.0])
    if abs(mean - 2.5) > 1e-15 or abs(var - 5.0 / 3.0) > 1e-14 or abs(excess - (-1.36)) > 1e-12:
        failures.append(f"moments wrong: {mean} {var} {excess}")
    if abs(st.se_rel_variance(200, -1.0) - math.sqrt(2.0 / 199)) > 1e-15:
        failures.append("SE_rel must never fall below the normal value")
    if abs(st.se_rel_variance(200, 6.0) - math.sqrt(2.0 / 199 + 6.0 / 200)) > 1e-15:
        failures.append("SE_rel must add the kurtosis term")
    xs = [float(i % 7) for i in range(200)]
    var_s = st.moments(xs)[1]
    row = st.evaluate_row(var_s * 1.2, 10.0, xs, 0.0)
    if not row["scored"] or abs(row["ratio"] - 1.2) > 1e-12 or abs(row["delta_lin"] - (st.DELTA_FLOOR + var_s * 1.2 / 100)) > 1e-12:
        failures.append(f"row arithmetic wrong: {row}")
    far = st.evaluate_row(var_s * 3.0, 10.0, xs, 0.0)
    if far["pass"]:
        failures.append("a factor of three must fail")
    if st.evaluate_row(var_s, 0.0, xs, 0.0)["scored"] or st.evaluate_row(1e4, 10.0, xs, 0.0)["scored"]:
        failures.append("a zero response and a rho above the first-order regime must be unscored")
    if st.evaluate_row(1e-4, 10.0, [5.0] * 200, 0.0)["pass"]:
        failures.append("zero spread must fail against a nonzero first-order variance")
    # the dose responses
    r = st.response("hard_cell_b", SYN_BOUNDS, 4)
    if any(r[c][g] != 0.0 for c in (0, 2, 3) for g in range(3)) or abs(r[1][0] - (2.55e5 / 1e6) ** 2) > 1e-12:
        failures.append(f"hard response wrong: {r}")
    # the factor-column arithmetic against a brute-force quadratic form
    rng = random.Random(7)
    cov = _syn_cov(list(st.ALL_CHANNELS), rng)
    r = st.response("uniform", SYN_BOUNDS, 4)
    flat = [(c, g) for c in range(4) for g in range(3)]
    dense = [[0.0] * 12 for _ in range(12)]
    for rec in cov["shared"]:
        v = [rec["column"][c][g] for c, g in flat]
        for a in range(12):
            for b in range(12):
                dense[a][b] += v[a] * v[b]
    for c, cols in cov["independent"].items():
        for col in cols:
            v = [col[g] if cc == c else 0.0 for cc, g in flat]
            for a in range(12):
                for b in range(12):
                    dense[a][b] += v[a] * v[b]
    brute = sum(dense[a][b] for a in range(12) for b in range(12))
    if abs(st.first_order(cov, r)[0] - brute) > 1e-9 * brute:
        failures.append("first-order variance differs from the dense quadratic form")
    c_t = st.total_strength_covariance(cov)
    if abs(sum(sum(row) for row in c_t) - brute) > 1e-9 * brute:
        failures.append("total-strength covariance differs from the dense sum")
    if abs(st.pearson([1, 2, 3, 4], [2, 4, 6, 8]) - 1.0) > 1e-14 or st.pearson([1, 1, 1], [1, 2, 3]) is not None:
        failures.append("pearson wrong")


def selftest():
    failures = []
    _stats_tests(failures)
    pins = dict(PINS, binary_sha256="b" * 64, source_commit="abcdef1")
    with tempfile.TemporaryDirectory() as temporary:
        base = Path(temporary) / "clean"
        base.mkdir()
        record = _synthetic(base)

        def run(label, mutate, gate, *, cov_mutation=None, case=None):
            trial = copy.deepcopy(record)
            folder = Path(temporary) / f"t_{abs(hash(label))}"
            folder.mkdir()
            for f in base.iterdir():
                (folder / f.name).write_bytes(f.read_bytes())
            if cov_mutation is not None:
                entry = next(e for e in trial["cases"] if e["name"] == case)
                _rewrite_cov(folder, entry, cov_mutation)
            mutate(trial, folder)
            gates, result, sentences = evaluate(trial, pins=pins, base=folder, source_tree="tree", use_g0=False)
            bad = [name for name in ("G1", "G2", "G3") if gates[name]]
            if gate is None and bad:
                failures.append(f"{label}: expected pass, failed {bad}: {[gates[b][:3] for b in bad]}")
            if gate is not None and gate not in bad:
                failures.append(f"{label}: expected {gate} to fail")
            if gate is not None and sentences:
                failures.append(f"{label}: a failed run must not earn the sentence")
            return result, sentences

        result, sentences = run("clean", lambda r, f: None, None)
        if not sentences or "within" not in sentences[0] or result["unscored_rows"] != 0 \
                or result["scored_rows"] != len(st.CASES) * len(st.STEPS) * len(st.RESPONSES):
            failures.append(f"clean summary wrong: {sentences[:1]} {result.get('scored_rows')}")
        run("binary", lambda r, f: r["environment"].update(binary_sha256="0" * 64), "G1")
        run("dirty", lambda r, f: r["environment"].update(clean_tree=False), "G1")
        run("cgroup", lambda r, f: r["environment"]["cgroup"].update({"memory.max": str(8 * GIB)}), "G1")
        run("constants", lambda r, f: r["constants"].update(n_samples=100), "G1")
        run("input pin", lambda r, f: r["environment"]["pins"].update(covariance_sha256="0" * 64), "G1")
        run("crates", lambda r, f: r["environment"].update(crates_tree="other"), "G1")
        run("missing case", lambda r, f: r["cases"].pop(), "G2")
        run("duplicate case", lambda r, f: r["cases"].append(copy.deepcopy(r["cases"][0])), "G2")
        run("failed process", lambda r, f: r["cases"][0].update(failures=[{"stage": "sample 3"}]), "G2")
        run("channels", lambda r, f: r["cases"][1].update(channels=["flux", "decay_constants"]), "G2")
        run("tampered samples", lambda r, f: (f / r["cases"][0]["artifacts"]["samples"]["path"]).write_bytes(b"x"), "G2")
        run("substitution without cause", lambda r, f: r["cases"][3].update(substituted=True), "G2")
        run("cap hit", lambda r, f: r["cases"][0].update(band_cap_hit=True), "G2")
        run("clipped mass absent", lambda r, f: r["cases"][0]["sampler"].update(xs_clipped=None), "G2")
        # a covariance too large by 60% in std is far outside k SE + delta
        run("shared columns scaled", lambda r, f: None, "G2", case="A_steel_shared",
            cov_mutation=lambda cov: [rec.update(column=[[x * 1.6 for x in row] for row in rec["column"]])
                                      for rec in cov["shared"]])
        run("flux columns dropped", lambda r, f: None, "G2", case="B_steel_flux",
            cov_mutation=lambda cov: cov["independent"].clear())
        run("unbanded source", lambda r, f: None, "G2", case="C_steel_all",
            cov_mutation=lambda cov: cov.update(unbanded=[1e6] * cov["n_cells"]))
        run("mean not the re-solve", lambda r, f: None, "G2", case="C_steel_all",
            cov_mutation=lambda cov: cov["S"][0].__setitem__(0, cov["S"][0][0] * 1.001))
        # G3: a perturbed joint export, and a truncation that explains a small difference
        run("joint off", lambda r, f: r["cases"][0]["joint"]["2"].update(
            sigma_total_correlated=r["cases"][0]["joint"]["2"]["sigma_total_correlated"] * (1 + 1e-6)), "G3")
        run("joint missing", lambda r, f: r["cases"][3]["joint"].pop("3"), "G3")

        def pair_off(cov):
            for rec in cov["shared"]:
                rec["column"] = [row[:] if c != 2 else [0.0] * len(row) for c, row in enumerate(rec["column"])]
        run("pair correlation", lambda r, f: None, "G2", case="A_steel_shared", cov_mutation=pair_off)
    print("selftest:", "PASS" if not failures else "FAIL")
    for failure in failures:
        print("  " + failure)
    return not failures


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--record", type=Path, default=RECORD)
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--self-test", "--selftest", dest="self_test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        sys.exit(0 if selftest() else 1)
    record = json.loads(args.record.read_text())
    gates, result, sentences = evaluate(record)
    passed = all(not problems for problems in gates.values())
    verdict = {
        "protocol": "ACTINV-P175",
        "verdict": "P175-PASS (pending G4, CI on the evidence commit)" if passed else "P175-FAIL",
        "gates": {name: {"pass": not problems, "problems": problems} for name, problems in gates.items()},
        "summary": result,
        "allowed_sentences": sentences,
    }
    for name, problems in gates.items():
        print(f"{name}: {'PASS' if not problems else 'FAIL'}")
        for problem in problems[:20]:
            print(f"  {problem}")
    if args.no_write:
        if VERDICT.is_file():
            recorded = json.loads(VERDICT.read_text())
            if recorded.get("summary") != json.loads(json.dumps(result)) or recorded.get(
                    "allowed_sentences") != sentences:
                print("verdict file differs from the recomputed summary")
                passed = False
    else:
        VERDICT.write_text(json.dumps(verdict, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
    for sentence in sentences:
        print("  " + sentence)
    print(verdict["verdict"] if passed else "P175-FAIL")
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
