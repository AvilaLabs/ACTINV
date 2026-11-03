#!/usr/bin/env python3
"""P187 checker (protocols/ACTINV-P187_PROTOCOL.md). It reads the run outputs, the sampled draws and the library
with its own code (nothing from p187_harness.py) and computes every statistic itself.

  G0  The protocol's SHA-256 is in protocols/protocol_hash.txt.
  G1  Pins, build at the registration commit, bounded scopes, exits, hashes; the samples file re-extracted from the
      raw outputs; the study's nominal solve equals the first-order run's.
  G2  Every gated (response, step) row: |V1/Vs - 1| <= 3 SE_rel + delta.
  G3  The draws perturb state-resolved rows in both problems; Fe53 (A) and Co58 (B) are gated at 66 s; each problem
      gates a response with a state-resolved parameter.
  G4  CI green on the evidence commit (checked after the push, not here).

  python3 controls/check_p187.py              check results/p187_record.json and target/p187-work, write the verdict
  python3 controls/check_p187.py --selftest
"""
import argparse
import gzip
import hashlib
import json
import math
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
PROTOCOL = ROOT / "protocols" / "ACTINV-P187_PROTOCOL.md"
RECORD = ROOT / "results" / "p187_record.json"
VERDICT = ROOT / "results" / "p187_verdict.json"
SCHEMA = "actinv-p187-verdict-1"
PINS = {
    "activation": "ec4c72bf598dc8ad3d533d9cfafdcf493e2d1f949a3e4db6251495659b68cc44",
    "cov_v120": "c19dec86b44ad5d90b66c9ab94d53e18641a1d354a89402a4da7986b6c530cde",
    "fns_spec": "ed55ed00f6c00fc4a2351e18332e98e7feb33d9b7ca65e7ba861e0e5e8feef39",
}
N_SAMPLES = 200
if os.environ.get("P187_REHEARSAL") == "1":  # protocol "Rehearsal": exits and gates only, nothing written to results/
    N_SAMPLES = 4
    RECORD = ROOT / "target" / "p187-rehearsal" / "p187_record.json"
    VERDICT = ROOT / "target" / "p187-rehearsal" / "p187_verdict.json"
SEED = {"A": 187001, "B": 187002}
RESPONSES = {"A": ["Fe53", "Mn54", "Mn56", "Cr51", "Mn57"],
             "B": ["Co58", "Co57", "Ni57", "Co60", "Al26", "Na24", "W185", "W187", "Nb92m1"]}
MUST_GATE = {"A": "Fe53", "B": "Co58"}
GATE_STEP = 1  # 66 s after shutdown
K_SIGMA, DELTA_FLOOR, RHO_MAX = 3.0, 0.02, 0.5
STATE_RESOLVED = 10


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


# ------------------------------------------------------------------------------------------------- statistics
def sample_stats(values):
    """(mean, Vs with divisor N-1, excess kurtosis, SE_rel of Vs never below normal theory)."""
    d = np.asarray(values, dtype=float)
    n = len(d)
    mean = float(d.mean())
    c = d - mean
    m2 = float(np.mean(c ** 2))
    vs = float(np.sum(c ** 2) / (n - 1))
    kappa = float(np.mean(c ** 4) / m2 ** 2 - 3.0) if m2 > 0 else 0.0
    se = math.sqrt(max(2.0 / (n - 1) + kappa / n, 2.0 / (n - 1)))
    return mean, vs, kappa, se


def judge(nominal, sd, rho, samples, clipped):
    """One (response, step) row: its statistics, whether it is gated, and whether it passes."""
    mean, vs, kappa, se = sample_stats(samples)
    v1 = sd * sd
    row = {"nominal": nominal, "v1": v1, "vs": vs, "rho": rho, "kappa": kappa, "se_rel": se,
           "mean_over_nominal": mean / nominal if nominal else None}
    row["gated"] = bool(nominal > 0.0 and rho is not None and 0.0 < rho <= RHO_MAX and vs > 0.0)
    if row["gated"]:
        delta = DELTA_FLOOR + rho * rho + (clipped or 0.0)
        row.update({"ratio": v1 / vs, "delta": delta, "limit": K_SIGMA * se + delta})
        row["pass"] = abs(v1 / vs - 1.0) <= row["limit"]
    return row


# ---------------------------------------------------------------------------------------------- raw outputs
def activities(path, names):
    out = json.loads(Path(path).read_text())
    return [{n: float(step.get("activity_Bq_per_g", {}).get(n, 0.0)) for n in names} for step in out["steps"]]


def first_order(path, names):
    out = json.loads(Path(path).read_text())
    rows = []
    for step in out["steps"]:
        block = (step.get("uncertainty") or {}).get("responses") or {}
        rows.append({n: block.get(f"activity:{n}") or {} for n in names})
    return rows


def library_lmf(path):
    with zipfile.ZipFile(path) as z:
        with z.open("rows.npy") as f:
            return np.lib.format.read_array(f)[:, 4]


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

    build, first, sample = record["build"], record["first"], record["sample"]
    registration = subprocess.run(["git", "log", "--diff-filter=A", "--format=%H", "--",
                                   "protocols/ACTINV-P187_PROTOCOL.md"], capture_output=True, text=True,
                                  cwd=ROOT).stdout.split()
    head = registration[-1] if registration else None
    if N_SAMPLES != 200:  # rehearsal: before registration, the build's own commit stands in
        head = build["git_head"]
    exe_sha = build["binary_sha256"]
    samples_path = ROOT / record["samples_file"]["path"]
    g1 = {
        "pins": record["pins"] == PINS and record["n_samples"] == N_SAMPLES and record["seed"] == SEED,
        "build": build["exit"] == 0 and build["git_head"] == head and build["git_dirty"] == "" and bool(exe_sha),
        "same_commit": first["git_head"] == head and sample["git_head"] == head,
        "bounded": bounded(build["cgroup"]) and bounded(first["cgroup"]) and bounded(sample["cgroup"]),
        "first_runs": sorted(first["runs"]) == ["A", "B"]
        and all(r["exit"] == 0 and r["binary_sha256"] == exe_sha and r["output_sha256"]
                and sha256(r["output"]) == r["output_sha256"] and sha256(r["spec"]) == r["spec_sha256"]
                for r in first["runs"].values()),
        "studies": sorted(sample["runs"]) == ["A", "B"]
        and all(r["exit"] == 0 and r["binary_sha256"] == exe_sha and len(r.get("samples", [])) == N_SAMPLES
                for r in sample["runs"].values()),
        "samples_file": sha256(samples_path) == record["samples_file"]["sha256"],
    }
    if not all(g1.values()):
        gates["G1"] = {"pass": False, **g1}
        return {"schema": SCHEMA, "gates": gates, "verdict": "P187-FAIL", "record_sha256": sha256(RECORD)}
    with gzip.open(samples_path) as f:
        stored = json.loads(f.read())["problems"]

    # re-extract from the raw outputs, and compare with the first-order run
    lmf = library_lmf(json.loads(Path(first["runs"]["A"]["spec"]).read_text())["library"]["path"])
    raw_problems, rows, state_rows = [], {}, {}
    for problem, names in RESPONSES.items():
        run, case = sample["runs"][problem], Path(sample["runs"][problem]["case"])
        if sha256(case / "out.json") != run["nominal_output_sha256"]:
            raw_problems.append(f"{problem}: nominal output changed")
        nominal = activities(case / "out.json", names)
        draws, rows_seen = [], set()
        for i, entry in enumerate(run["samples"]):
            draw, out = case / f"rob_{i}.json", case / f"rob_{i}.out.json"
            if sha256(draw) != entry["draw_sha256"] or sha256(out) != entry["output_sha256"]:
                raw_problems.append(f"{problem}: sample {i} changed")
                continue
            rows_seen |= {int(k) for k in (json.loads(draw.read_text()).get("options") or {}).get("rate_scale") or {}}
            draws.append(activities(out, names))
        state_rows[problem] = sum(1 for r in rows_seen if int(lmf[r]) == STATE_RESOLVED)
        if stored[problem]["responses"] != names or stored[problem]["nominal"] != nominal \
                or stored[problem]["samples"] != draws:
            raw_problems.append(f"{problem}: the samples file differs from the raw outputs")
        fo = first_order(first["runs"][problem]["output"], names)
        if len(fo) != len(nominal):
            raw_problems.append(f"{problem}: {len(fo)} first-order steps, {len(nominal)} sampled")
            continue
        clipped = run.get("clipped_relative")
        for s, (fo_step, nom_step) in enumerate(zip(fo, nominal)):
            for name in names:
                e = fo_step[name]
                if e.get("nominal") != nom_step[name]:
                    raw_problems.append(f"{problem} step {s} {name}: study nominal {nom_step[name]!r}, "
                                        f"first-order {e.get('nominal')!r}")
                    continue
                row = judge(nom_step[name], e.get("mf33_standard_uncertainty", 0.0),
                            e.get("relative_standard_uncertainty"), [d[s][name] for d in draws], clipped)
                row["from_reaction_total"] = e.get("parameters_from_reaction_total", 0)
                rows[f"{problem}/{name}/{s}"] = row
    gates["G1"] = {"pass": not raw_problems, **g1, "problems": raw_problems[:20]}

    gated = {k: r for k, r in rows.items() if r["gated"]}
    failed = sorted(k for k, r in gated.items() if not r["pass"])
    ratios = [r["ratio"] for r in gated.values()]
    gates["G2"] = {"pass": bool(gated) and not failed, "gated": len(gated), "failed": failed,
                   "ratio_range": [min(ratios), max(ratios)] if ratios else None,
                   "clipped_relative": {p: sample["runs"][p].get("clipped_relative") for p in RESPONSES}}
    g3 = []
    for problem, nuclide in MUST_GATE.items():
        if state_rows.get(problem, 0) == 0:
            g3.append(f"{problem}: no state-resolved row is perturbed")
        if not rows.get(f"{problem}/{nuclide}/{GATE_STEP}", {}).get("gated"):
            g3.append(f"{problem}: {nuclide} is not gated at 66 s")
        if not any(r["from_reaction_total"] > 0 for k, r in gated.items() if k.startswith(f"{problem}/")):
            g3.append(f"{problem}: no gated response has a state-resolved parameter")
    gates["G3"] = {"pass": not g3, "problems": g3, "state_resolved_rows_perturbed": state_rows}
    gates["G4"] = {"pass": None, "note": "CI green on the evidence commit, checked after the push"}
    verdict = {"schema": SCHEMA, "gates": gates, "rows": rows, "record_sha256": sha256(RECORD)}
    passed = all(gates[g]["pass"] for g in ("G0", "G1", "G2", "G3"))
    verdict["verdict"] = "P187-PASS (pending G4)" if passed else "P187-FAIL"
    if passed:
        verdict["allowed_sentences"] = sentences(gated)
    return verdict


def sentences(gated):
    lo, hi = min(r["ratio"] for r in gated.values()), max(r["ratio"] for r in gated.values())
    states = sorted({k.split("/")[1] for k, r in gated.items() if r["from_reaction_total"] > 0})
    return [
        "On two FNS-spectrum point problems (iron, and a nickel, cobalt, aluminium, tungsten and niobium mixture) "
        f"with TENDL-2025's MF=33 covariances, ACTINV's first-order activity band agreed with the spread of "
        f"{N_SAMPLES} sampled solves within the registered limit on all {len(gated)} tested (nuclide, time) pairs; "
        f"the ratio of first-order to sampled variance ranged from {lo:.3g} to {hi:.3g}.",
        "The tested nuclides with state-resolved (MF=10) production, which take their reaction's covariance, are "
        f"{', '.join(states)}."]


# ---------------------------------------------------------------------------------------------------- selftest
def selftest():
    rng = np.random.default_rng(187)
    x = rng.normal(size=200000)
    mean, vs, kappa, se = sample_stats(x)
    assert abs(vs - 1.0) < 0.02 and abs(kappa) < 0.05 and abs(se - math.sqrt(2 / 199999)) < 1e-4
    _, _, kappa_t, se_t = sample_stats(rng.standard_t(6, size=200000))
    assert kappa_t > 1.0 and se_t > math.sqrt(2 / 199999), "heavy tails widen SE_rel"
    assert sample_stats([1.0, 2.0, 3.0])[1] == 1.0, "divisor N - 1"
    draws = 10.0 * (1.0 + 0.1 * rng.normal(size=200))
    _, vs200, _, _ = sample_stats(draws)
    ok = judge(10.0, 1.0, 0.1, draws, 0.0)
    assert ok["gated"] and abs(ok["ratio"] - 1.0 / vs200) < 1e-15 and ok["pass"]
    bad = judge(10.0, 2.0, 0.2, draws, 0.0)
    assert bad["gated"] and not bad["pass"], "a band twice the spread fails"
    assert not judge(10.0, 6.0, 0.6, draws, 0.0)["gated"], "outside the first-order regime"
    assert not judge(0.0, 0.0, None, [0.0] * 200, 0.0)["gated"], "no activity"
    assert judge(10.0, 1.0, 0.1, draws, 0.3)["limit"] > ok["limit"], "clipped mass widens delta"
    assert bounded({"memory_max": str(6 << 30), "memory_swap_max": "0", "pids_max": "128", "cpu_max": "400000 100000"})
    assert not bounded({"memory_max": "max", "memory_swap_max": "0", "pids_max": "128", "cpu_max": "400000 100000"})
    print("check_p187 selftest: PASS")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        selftest()
        return 0
    verdict = check()
    VERDICT.write_text(json.dumps(verdict, indent=1, sort_keys=True) + "\n")
    for gate, body in verdict["gates"].items():
        print(gate, body["pass"])
    print(verdict["verdict"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
