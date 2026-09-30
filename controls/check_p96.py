#!/usr/bin/env python3
"""P96 checker (protocols/ACTINV-P96_PROTOCOL.md): P93's flux channel, G3 revised.

    python3 controls/check_p96.py g3            # the gate: fresh spec set (offset 10, seed 20261001)
    python3 controls/check_p96.py g3-p93-set    # reported only: P93's set re-scored under rule (b)
    python3 controls/check_p96.py verdict       # G0-G5, lead only

G3 differs from P93's in two ways:
- the spec set starts at index 10 of the sorted P75b specs, with seed base 20261001;
- each comparison is excluded, and counted, when its central difference is not converged, i.e.
  FD(h' = 10h) differs from FD(h) by more than the comparison's own tolerance (rule (b)).
R(+/-) are plain runs with the absolute group flux phi_g (1 +/- h e_g z_g) and no `total`, as
P93's text says. The run helpers and flux-file writers come from controls/check_p93.py (with its
two checker fixes); logs land in target/p96/.
"""
from __future__ import annotations

import concurrent.futures as cf
import hashlib
import json
import math
import os
import random
import re
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import check_p93 as p93  # noqa: E402

# Two workers: three concurrent flux-only runs on the heavy specs exceeded a 6 GB cap (OOM, 15:14).
p93.WORKERS = 2

P93_WORK = ROOT / "target" / "p93"
# ACTINV_FLUX_PROTOCOL=P97 runs the same G3 for P97: logs in target/p97, verdict
# results/p97_verdict.json, and the candidate pin is the candidate that P97's G1 (check_p93.py
# build, rerun on the fixed branch) recorded, so G1-G4 must all be evidence on that one binary.
FLUX_PROTOCOL = os.environ.get("ACTINV_FLUX_PROTOCOL", "P96")
if FLUX_PROTOCOL not in ("P96", "P97"):
    sys.exit(f"ACTINV_FLUX_PROTOCOL must be P96 or P97, not {FLUX_PROTOCOL}")
WORK = ROOT / "target" / FLUX_PROTOCOL.lower()
PROTOCOL = ROOT / "protocols" / f"ACTINV-{FLUX_PROTOCOL}_PROTOCOL.md"
VERDICT = ROOT / "results" / f"{FLUX_PROTOCOL.lower()}_verdict.json"
CANDIDATE_SHA = ("d12ba06a0118ad1109e660061d1fdb0bdb75cf47be13ecd1b5a030286c11751a"
                 if FLUX_PROTOCOL == "P96"
                 else json.loads((P93_WORK / "g1.json").read_text())["candidate_sha256"])
H = 1e-4
H_WIDE = 1e-3
SETS = {
    "g3": {"offset": 10, "count": 39, "seed": 20261001},
    "g3-p93-set": {"offset": 0, "count": 40, "seed": 20260930},
}
RESPONSES = ("activity.total", "heat.total")


def with_flux_channel(spec: dict) -> dict:
    spec = json.loads(json.dumps(spec))
    flux = spec["spectrum"]["flux_per_group"]
    spec["spectrum"]["relative_error"] = [0.05 if v > 0.0 else 0.0 for v in flux]
    spec["uncertainty"] = {"channels": ["flux"], "responses": list(RESPONSES)}
    return spec


def spec_set(offset: int, count: int) -> tuple[dict, list]:
    base = sorted(p93.SPECS_DIR.glob("*.json"))[offset::20]
    assert len(base) == count, f"expected {count} base specs, got {len(base)}"
    out = {path.stem: with_flux_channel(json.loads(path.read_text())) for path in base}
    for path in base[:3]:
        spec = json.loads(path.read_text())
        assert not spec["spectrum"].get("descending", False)
        flux = spec["spectrum"]["flux_per_group"]
        spec["spectrum"] = dict(spec["spectrum"], flux_per_group=list(reversed(flux)), descending=True)
        out[f"{path.stem}.descending"] = with_flux_channel(spec)
    return out, base[:5]


def seeded_z(name: str, seed_base: int, direction: int, n: int) -> list:
    name_hash = int(hashlib.sha256(name.encode()).hexdigest()[:8], 16)
    rng = random.Random(seed_base + (name_hash % 1_000_003) + direction)
    return [rng.gauss(0.0, 1.0) for _ in range(n)]


def compare(flux_steps: list, totals: dict, z: list) -> list:
    """One comparison per (step, response): prediction from the flux-only run's sensitivities,
    FD(h) and FD(h') from the plain perturbed runs, rule (b) evaluated first."""
    out = []
    for step_index, step in enumerate(flux_steps):
        responses = (step.get("uncertainty") or {}).get("responses") or {}
        for name in RESPONSES:
            resp = responses.get(name)
            if resp is None:
                continue
            nominal = resp.get("nominal")
            if nominal is None or nominal == 0.0:
                continue
            terms = [p["value"] * p["parameter"]["standard_uncertainty_relative"] * z[p["parameter"]["group"]]
                     for p in resp.get("flux_sensitivities", [])]
            predicted = math.fsum(terms)
            tol = 1e-4 * math.fsum(abs(t) for t in terms) + 1e-10 * abs(nominal)
            values = {tag: totals[tag].get(step_index, {}).get(name) for tag in totals}
            if any(v is None for v in values.values()):
                continue
            fd = (values["plus"] - values["minus"]) / (2.0 * H)
            fd_wide = (values["plus_wide"] - values["minus_wide"]) / (2.0 * H_WIDE)
            converged = abs(fd - fd_wide) <= tol
            diff = abs(predicted - fd)
            out.append({
                "step": step_index, "response": name, "nominal": nominal,
                "predicted": predicted, "central_difference": fd, "central_difference_wide": fd_wide,
                "reference_converged": converged, "diff": diff, "tolerance": tol,
                "ratio": diff / tol if tol > 0 else math.inf,
                "ok": diff <= tol, "ok_100x": diff <= 100.0 * tol,
            })
    return out


def perturbed_single(spec: dict, e: list, z: list, h: float, sign: int) -> dict:
    k = p93.absolute_flux_scale(spec["spectrum"])
    out = json.loads(json.dumps(spec))
    out.pop("uncertainty", None)
    out["spectrum"]["flux_per_group"] = [v * k * (1.0 + sign * h * e[g] * z[g])
                                         for g, v in enumerate(spec["spectrum"]["flux_per_group"])]
    out["spectrum"].pop("total", None)
    return out


def single_direction(work: Path, name: str, spec: dict, pair: dict, seed_base: int, direction: int) -> dict:
    case = {"name": name, "direction": direction, "route": "single", "excluded": False, "comparisons": []}
    plain_doc, flux_doc = pair["plain"]["doc"], pair["flux_only"]["doc"]
    if plain_doc is None or flux_doc is None:
        return dict(case, excluded=True, reason="nominal run failed")
    e = spec["spectrum"]["relative_error"]
    z = seeded_z(name, seed_base, direction, len(e))
    baseline = (plain_doc["mode"], plain_doc["pruned_states"], plain_doc["total_states"])
    docs = {}
    with tempfile.TemporaryDirectory(dir=work) as d:
        for tag, h, sign in (("plus", H, 1), ("minus", H, -1), ("plus_wide", H_WIDE, 1), ("minus_wide", H_WIDE, -1)):
            path = Path(d) / f"{tag}.json"
            path.write_text(json.dumps(perturbed_single(spec, e, z, h, sign)))
            r = p93.run_to_doc(p93.CAND, path, timeout=900)
            if r["returncode"] != 0 or r["doc"] is None:
                return dict(case, excluded=True, reason=f"{tag} run failed")
            docs[tag] = r["doc"]
    if any((doc["mode"], doc["pruned_states"], doc["total_states"]) != baseline for doc in docs.values()):
        return dict(case, excluded=True, reason="mode/pruned_states/total_states differs")
    case["comparisons"] = compare(flux_doc["steps"], {t: p93.step_totals(doc) for t, doc in docs.items()}, z)
    return case


def custom10_cases(work: Path, name: str, base_spec: dict, seed_base: int) -> list:
    idx = p93.coarse_group_indices()
    boundaries = [p93.fispact709_boundaries()[i] for i in idx]
    abs_flux = p93.spectrum_absolute_flux(base_spec["spectrum"])
    coarse = [sum(abs_flux[idx[k]:idx[k + 1]]) for k in range(len(idx) - 1)]
    e = [0.05 if v > 0.0 else 0.0 for v in coarse]
    label = f"{name}.custom10"
    cases = []
    with tempfile.TemporaryDirectory(dir=work) as d:
        d = Path(d)

        def mesh_run(tag: str, flux: list, with_uncertainty: bool) -> dict:
            flux_path = d / f"{tag}_flux.ndjson"
            flux_sha = p93.write_one_cell_flux(boundaries, flux, e, flux_path)
            mesh = p93.mesh_spec_from_single(base_spec, {"path": str(flux_path), "sha256": flux_sha},
                                             f"{label}.{tag}", with_uncertainty)
            spec_path = d / f"{tag}_mesh.json"
            spec_path.write_text(json.dumps(mesh))
            return p93.run_mesh_cell_result(p93.CAND, spec_path, d / f"{tag}_out.ndjson", cwd=d)

        flux_only = mesh_run("flux_only", coarse, True)
        plain = mesh_run("plain", coarse, False)
        if flux_only["result"] is None or plain["result"] is None:
            return [{"name": label, "direction": k, "route": "mesh_one_cell", "excluded": True,
                     "reason": "nominal run failed", "comparisons": []} for k in range(3)]
        baseline = (plain["result"].get("mode"), plain["result"].get("pruned_states"),
                    plain["result"].get("total_states"))
        for direction in range(3):
            case = {"name": label, "direction": direction, "route": "mesh_one_cell",
                    "excluded": False, "comparisons": []}
            z = seeded_z(label, seed_base, direction, len(coarse))
            results = {}
            for tag, h, sign in (("plus", H, 1), ("minus", H, -1), ("plus_wide", H_WIDE, 1), ("minus_wide", H_WIDE, -1)):
                flux = [v * (1.0 + sign * h * e[g] * z[g]) for g, v in enumerate(coarse)]
                r = mesh_run(f"{tag}{direction}", flux, False)
                if r["result"] is None:
                    case.update(excluded=True, reason=f"{tag} run failed")
                    break
                results[tag] = r["result"]
            if not case["excluded"]:
                if any((doc.get("mode"), doc.get("pruned_states"), doc.get("total_states")) != baseline
                       for doc in results.values()):
                    case.update(excluded=True, reason="mode/pruned_states/total_states differs")
                else:
                    case["comparisons"] = compare(flux_only["result"]["steps"],
                                                  {t: p93.step_totals(doc) for t, doc in results.items()}, z)
            cases.append(case)
    return cases


def run_g3(set_name: str) -> dict:
    cfg = SETS[set_name]
    work = WORK / set_name
    work.mkdir(parents=True, exist_ok=True)
    specs, custom_base = spec_set(cfg["offset"], cfg["count"])
    candidate = p93.sha(p93.CAND)

    def pair(item):
        name, spec = item
        plain = dict(spec)
        plain.pop("uncertainty", None)
        with tempfile.TemporaryDirectory(dir=work) as d:
            plain_path, flux_path = Path(d) / "plain.json", Path(d) / "flux.json"
            plain_path.write_text(json.dumps(plain))
            flux_path.write_text(json.dumps(spec))
            r_plain = p93.run_to_doc(p93.CAND, plain_path, timeout=900)
            r_flux = p93.run_to_doc(p93.CAND, flux_path, timeout=14400)
        return name, {"plain": r_plain, "flux_only": r_flux}

    t0 = time.monotonic()
    pairs = {}
    with cf.ThreadPoolExecutor(p93.WORKERS) as ex:
        for i, (name, result) in enumerate(ex.map(pair, specs.items())):
            pairs[name] = result
            print(f"{set_name} pair {i + 1}/{len(specs)} {name} {time.monotonic() - t0:.0f}s", flush=True)

    cases = []
    with cf.ThreadPoolExecutor(p93.WORKERS) as ex:
        futures = [ex.submit(single_direction, work, name, spec, pairs[name], cfg["seed"], k)
                   for name, spec in specs.items() for k in range(3)]
        futures += [ex.submit(custom10_cases, work, path.stem, json.loads(path.read_text()), cfg["seed"])
                     for path in custom_base]
        for i, fut in enumerate(cf.as_completed(futures)):
            result = fut.result()
            cases.extend(result if isinstance(result, list) else [result])
            if i % 20 == 0:
                print(f"{set_name} direction-batch {i + 1}/{len(futures)} {time.monotonic() - t0:.0f}s", flush=True)

    total_cases = len(cases)
    excluded_a = sum(1 for c in cases if c["excluded"])
    comparisons = [cm for c in cases if not c["excluded"] for cm in c["comparisons"]]
    excluded_b = [cm for cm in comparisons if not cm["reference_converged"]]
    remaining = [cm for cm in comparisons if cm["reference_converged"]]
    within = sum(1 for cm in remaining if cm["ok"])
    within_100x = sum(1 for cm in remaining if cm["ok_100x"])
    rate_a = excluded_a / total_cases if total_cases else 1.0
    rate_b = len(excluded_b) / len(comparisons) if comparisons else 1.0
    pass_rate = within / len(remaining) if remaining else 0.0

    peaks = {}
    for c in cases:
        for cm in c["comparisons"]:
            key = (c["name"], cm["response"])
            peaks[key] = max(peaks.get(key, 0.0), abs(cm["nominal"]))
    excluded_dyn = sorted(abs(cm["nominal"]) / peaks[(c["name"], cm["response"])]
                          for c in cases if not c["excluded"] for cm in c["comparisons"]
                          if not cm["reference_converged"])
    summary = {
        "set": set_name, **cfg, "candidate_sha256": candidate,
        "candidate_matches_p93": candidate == CANDIDATE_SHA,
        "total_cases": total_cases, "excluded_cases_a": excluded_a, "exclusion_rate_a": rate_a,
        "comparisons": len(comparisons), "excluded_comparisons_b": len(excluded_b),
        "exclusion_rate_b": rate_b, "remaining": len(remaining), "within_tolerance": within,
        "pass_rate": pass_rate, "within_100x_tolerance": within_100x,
        "excluded_b_abs_R_over_peak_max": excluded_dyn[-1] if excluded_dyn else None,
        "excluded_b_abs_R_over_peak_quantiles": [excluded_dyn[int(q * (len(excluded_dyn) - 1))]
                                                 for q in (0.0, 0.5, 1.0)] if excluded_dyn else [],
        "worst_remaining": sorted(remaining, key=lambda cm: -cm["ratio"])[:10],
        "exclusion_reasons_a": sorted({c.get("reason") for c in cases if c["excluded"]} - {None}),
    }
    summary["pass"] = (candidate == CANDIDATE_SHA and rate_a <= 0.05 and rate_b <= 0.05
                       and pass_rate >= 0.99 and within_100x == len(remaining) and bool(remaining))
    (WORK / f"{set_name}.json").write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n")
    (WORK / f"{set_name}_cases.json").write_text(json.dumps(cases, sort_keys=True) + "\n")
    print(json.dumps({k: v for k, v in summary.items() if k != "worst_remaining"}, indent=1))
    return summary


def cmd_verdict() -> int:
    """Assembles G0-G5; lead only."""
    def carried(name: str) -> dict:
        path = P93_WORK / f"{name}.json"
        if not path.exists():
            return {"pass": False, "note": f"{path} missing"}
        value = json.loads(path.read_text())
        recorded = CANDIDATE_SHA in json.dumps(value)
        # check_p93's g2.json does not record the candidate hash; there the provenance is that the
        # current candidate is P93's and was built before the result file was written.
        built_before = (p93.CAND.exists() and p93.sha(p93.CAND) == CANDIDATE_SHA
                        and p93.CAND.stat().st_mtime < path.stat().st_mtime)
        return {"pass": value.get("pass") is True and (recorded or built_before),
                "source": str(path.relative_to(ROOT)), "candidate_recorded": recorded,
                "candidate_built_before_result": built_before}

    registered = f"{p93.sha(PROTOCOL)}  protocols/{PROTOCOL.name}" in (ROOT / "protocols/protocol_hash.txt").read_text()
    g3_path = WORK / "g3.json"
    g3 = (json.loads(g3_path.read_text()) if g3_path.exists()
          else {"pass": False, "note": "NOT RUN (stopped before any comparison was computed)"})
    replay = WORK / "ci_replay_summary.log"
    steps = re.findall(r"^STEP (\d+) (\S+)$", replay.read_text(), re.M) if replay.exists() else []
    failed = [name for code, name in steps if code != "0"]
    verdict = {
        "protocol": f"ACTINV-{FLUX_PROTOCOL}",
        "inputs": {"protocol_sha256": p93.sha(PROTOCOL), "candidate_sha256": p93.sha(p93.CAND)},
        "G0": {"pass": registered},
        "G1": carried("g1"), "G2": carried("g2"), "G4": carried("g4"),
        "G3": {k: v for k, v in g3.items() if k != "worst_remaining"},
        "G5": {"pass": bool(steps) and not failed, "steps": len(steps), "failed": failed},
    }
    verdict["pass"] = all(verdict[g].get("pass") for g in ("G0", "G1", "G2", "G3", "G4", "G5"))
    VERDICT.write_text(json.dumps(verdict, indent=1, sort_keys=True) + "\n")
    print(json.dumps(verdict, indent=1))
    return 0


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command in SETS:
        WORK.mkdir(parents=True, exist_ok=True)
        sys.exit(0 if run_g3(command)["pass"] else 1)
    if command == "verdict":
        sys.exit(cmd_verdict())
    sys.exit(__doc__)
