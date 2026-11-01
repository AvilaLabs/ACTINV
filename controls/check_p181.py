#!/usr/bin/env python3
"""P181 checker (protocols/ACTINV-P181_PROTOCOL.md): ACTINV and OpenMC 0.15.3's flux mode on a mixed-material mesh as
the element count rises. Reads results/p181_scale.json written by controls/p181_harness.py. G0-G3 are evaluated here;
G4 (CI on the pushed evidence commit) is external.

  G0  The protocol's SHA-256 (and any protocols/ACTINV-P181_AMENDMENT_*.md) is in protocols/protocol_hash.txt.
  G1  Pinned external inputs; actinv 1.7.1 and OpenMC 0.15.3; the harness, protocol, adapter module, its _defaults.py,
      the demo file and the group file equal to this checkout's; a clean tracked tree; the harness in the AGENTS.md
      scope and every run scope at 10 GiB, no swap, at most 2 CPUs; not a smoke record.
  G2  Every planned (arm, size) run or not run by a registered rule (recomputed); both arms complete at 8 elements;
      every complete run has finite step times, a dose, a photon strength and a 709-group flux.
  G3  Quiet machine: no other transient scope of the user at the start, before any run or at the end; median 1-minute
      load average before the runs at most 3.0. Then the summary and the allowed sentences are recomputed and written
      to results/p181_verdict.json (with --no-write, compared with it).

Verdict: P181-PASS if G0-G3 hold (G4 external). The times and the reach are reported, not gated.

Usage: check_p181.py [--record FILE] [--no-write]
       check_p181.py --selftest
"""
import argparse
import hashlib
import json
import math
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "controls"))
import p181_harness as h  # noqa: E402

PROTOCOL = ROOT / "protocols" / "ACTINV-P181_PROTOCOL.md"
HASH_LOG = ROOT / "protocols" / "protocol_hash.txt"
RECORD = h.OUT_DEFAULT
VERDICT = ROOT / "results" / "p181_verdict.json"

ACTINV_VERSION = "actinv 1.7.1"
OPENMC_VERSION = "0.15.3"
MAX_MEDIAN_LOAD = 3.0
DOSE_BAND = (0.90, 1.10)
STRENGTH_FACTOR = 2.0
FLUX_REL = 0.02
GROUPS = 709
# a kill is attributed to the memory limit only with this much in the run's last heartbeat memory.peak
KILL_PEAK_GIB = 9.5
GIB = 1 << 30

# external inputs, P163's and P180's pins
PINS = {
    "chain_xml": "d3b829467cfdda9c624aaeb5709825df378b908ecc63c3f984ade51f8d0444a0",
    "actinv_library": "ec4c72bf598dc8ad3d533d9cfafdcf493e2d1f949a3e4db6251495659b68cc44",
    "decay_primary": "6f04cf009086c179021f243a58dadc2d5bb078de5ba39c4fe46ccad77d228ddb",
    "decay_fallback": "850b8b7f85f8d88b6ad826c4cd341aaaffabd525c8ecf3c588a0ad437bf5d123",
    "cross_sections_xml": "b8ffb6104a946bf951dc0f3b9a1f57a60f7f57c4b308cd66df4fd3dd4bc557ce",
    "actinv_binary": "bd0c21c96a17918d6a21b29553be33877ff3c5b7fa4b92882598d7a37f6458bc",
}
XS_ARCHIVE = "b7ad59cb4a3d76d8a291326093a98507f8d24b6e6af629116d3f7dc85f83c4cb"
REPO_INPUTS = {"harness": "controls/p181_harness.py", "protocol": "protocols/ACTINV-P181_PROTOCOL.md",
               "adapter": "python/pysrc/actinv/openmc.py", "adapter_defaults": "python/pysrc/actinv/_defaults.py",
               "demo_steel_cube": "contrib/openmc_r2s/demo_steel_cube.py",
               "groups_json": "crates/actinv-data/data/fispact_709_groups.json"}
LABEL = {"actinv": "ACTINV", "openmc_flux": "OpenMC 0.15.3 in flux mode"}


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def finite(*values):
    return all(isinstance(v, (int, float)) and math.isfinite(v) for v in values)


def g0():
    if not PROTOCOL.is_file():
        return [f"missing {PROTOCOL.name}"]
    logged = HASH_LOG.read_text()
    return [f"{path.name} hash not in protocol_hash.txt"
            for path in [PROTOCOL, *sorted(PROTOCOL.parent.glob("ACTINV-P181_AMENDMENT_*.md"))]
            if f"{sha256(path)}  protocols/{path.name}" not in logged]


def scope_ok(cg, memory_gib):
    ok, _ = h.cgroup_enforced(cg or {}, memory_gib)
    return ok


def g1(record, repo=None):
    problems = []
    repo = repo if repo is not None else {k: sha256(ROOT / v) for k, v in REPO_INPUTS.items()}
    if record.get("schema") != h.SCHEMA:
        problems.append("wrong schema")
    if record.get("smoke"):
        problems.append("a smoke record")
    inputs = record.get("inputs", {})
    for name, digest in {**PINS, **repo}.items():
        if inputs.get(name, {}).get("sha256") != digest:
            problems.append(f"input {name} hash is not the pin")
    env = record.get("environment", {})
    if env.get("xs_archive_sha256") != XS_ARCHIVE:
        problems.append("cross-section archive digest is not the pin")
    if not str(env.get("actinv_version", "")).startswith(ACTINV_VERSION):
        problems.append(f"ACTINV is not {ACTINV_VERSION}")
    if env.get("openmc_version") != OPENMC_VERSION:
        problems.append(f"OpenMC is not {OPENMC_VERSION}")
    if env.get("git_dirty_paths"):
        problems.append("tracked tree was not clean")
    if not scope_ok(env.get("cgroup"), 6):
        problems.append("harness cgroup is not the bounded scope")
    if record.get("plan", {}).get("memory_max_gib") != h.MEMORY_MAX_GIB or \
            record.get("plan", {}).get("limit_s") != h.LIMIT_S or record.get("plan", {}).get("dims") != h.DIMS:
        problems.append("plan differs from the registered limits or sizes")
    for a in record.get("attempts", []):
        cg = a.get("cgroup")
        if cg is not None and not (scope_ok(cg, h.MEMORY_MAX_GIB) and int(cg["memory_max"]) == h.MEMORY_MAX_GIB * GIB):
            problems.append(f"{a['arm']} at {a['elements']}: run scope is not 10 GiB / no swap / 2 CPUs")
    return problems


def complete_ok(a):
    c = a.get("config", {})
    return (a.get("complete") and finite(a.get("step1_s"), a.get("step2_s"), a.get("step3_s"),
                                         a.get("dose_rate_Sv_h"), a.get("photons_per_s"),
                                         c.get("flux_sum_n_cm_per_src"))
            and a["dose_rate_Sv_h"] > 0 and a["photons_per_s"] > 0 and c.get("energy_groups_in_flux") == GROUPS)


def g2(record):
    problems = []
    attempts = record.get("attempts", [])
    skips = record.get("skips", [])
    seen = []
    replay = []
    for dim in h.DIMS:
        for arm in h.ARM_ORDER:
            ran = [a for a in attempts if a["arm"] == arm and a["dim"] == dim]
            skipped = [s for s in skips if s["arm"] == arm and s["dim"] == dim]
            if len(ran) + len(skipped) != 1:
                problems.append(f"{arm} at {dim ** 3}: {len(ran)} runs and {len(skipped)} rule records")
                continue
            expected = h.decide(arm, dim, replay, False)
            if ran:
                if expected is not None:
                    problems.append(f"{arm} at {dim ** 3}: run although rule '{expected['rule']}' applied")
                replay.append(ran[0])
            else:
                if expected is None or expected["rule"] != skipped[0]["rule"]:
                    problems.append(f"{arm} at {dim ** 3}: not run, but the rules allowed it")
            seen.append((arm, dim))
    for arm in h.ARM_ORDER:
        first = next((a for a in attempts if a["arm"] == arm and a["dim"] == h.DIMS[0]), None)
        if first is None or not first.get("complete"):
            problems.append(f"{arm} did not complete at {h.DIMS[0] ** 3} elements")
    for a in attempts:
        if a.get("complete") and not complete_ok(a):
            problems.append(f"{a['arm']} at {a['elements']}: complete run lacks finite times, dose, strength or flux")
        if a.get("steps12_complete") and not finite(a.get("step1_s"), a.get("step2_s")):
            problems.append(f"{a['arm']} at {a['elements']}: steps 1 and 2 marked complete without times")
    return problems


def quiet(record):
    problems = []
    env = record.get("environment", {})
    if env.get("other_scopes_start"):
        problems.append(f"other transient scopes at the start: {env['other_scopes_start']}")
    if env.get("other_scopes_end"):
        problems.append(f"other transient scopes at the end: {env['other_scopes_end']}")
    loads = []
    for a in record.get("attempts", []):
        before = a.get("before", {})
        if before.get("other_scopes"):
            problems.append(f"other transient scopes before {a['arm']} at {a['elements']}")
        if before.get("load_average"):
            loads.append(before["load_average"][0])
    if not loads or statistics.median(loads) > MAX_MEDIAN_LOAD:
        problems.append(f"median 1-minute load average before the runs above {MAX_MEDIAN_LOAD}")
    return problems


# ------------------------------------------------------------------------------------------------------ summary

def working_set_gib(a):
    rss = a.get("peak_rss_mib") or {}
    vals = [v for v in rss.values() if finite(v)]
    return max(vals) / 1024.0 if vals else None


def run_row(a):
    c = a.get("config", {})
    beat = a.get("last_heartbeat") or {}
    return {"arm": a["arm"], "elements": a["elements"], "status": a["status"], "regions": c.get("n_regions"),
            "step1_s": a.get("step1_s"), "step2_s": a.get("step2_s"), "step3_s": a.get("step3_s"),
            "h1_s": h.h1(a), "wall_s": a.get("wall_s"), "steps12_complete": bool(a.get("steps12_complete")),
            "complete": bool(a.get("complete")), "working_set_gib": working_set_gib(a),
            "memory_peak_gib": (a["memory_peak_bytes"] / GIB) if finite(a.get("memory_peak_bytes")) else None,
            "dose_rate_Sv_h": a.get("dose_rate_Sv_h"), "photons_per_s": a.get("photons_per_s"),
            "flux_sum": c.get("flux_sum_n_cm_per_src"),
            "headroom_ok": bool((a.get("before") or {}).get("headroom", {}).get("ok")),
            "last_heartbeat": {"phase": beat.get("phase"),
                               "memory_peak_gib": (beat["memory_peak_bytes"] / GIB)
                               if finite(beat.get("memory_peak_bytes")) else None} if beat else None}


def accuracy(act, omc):
    """Checks at a size where both completed: [] if they pass."""
    problems = []
    ratio = act["dose_rate_Sv_h"] / omc["dose_rate_Sv_h"]
    if not DOSE_BAND[0] <= ratio <= DOSE_BAND[1]:
        problems.append(f"dose ratio {ratio:.4f} outside {DOSE_BAND}")
    s = act["photons_per_s"] / omc["photons_per_s"]
    if not 1.0 / STRENGTH_FACTOR <= s <= STRENGTH_FACTOR:
        problems.append(f"photon strength ratio {s:.3f} outside a factor of {STRENGTH_FACTOR}")
    f = act["flux_sum"] / omc["flux_sum"]
    if abs(f - 1.0) > FLUX_REL:
        problems.append(f"flux sum ratio {f:.4f} outside {FLUX_REL:.0%}")
    return ratio, problems


def reach(rows, skips, arm, size_ok):
    """(largest elements that completed steps 1-2 and passed the size's checks, its row, reason for the next size)."""
    best, best_row, reason = None, None, None
    for dim in h.DIMS:
        e = dim ** 3
        row = rows.get((arm, e))
        if row is None:
            skip = next((s for s in skips if s["arm"] == arm and s["dim"] == dim), None)
            if skip and skip["rule"] == "projection":
                reason = {"elements": e, "why": "projection"}
            elif skip:
                reason = reason or {"elements": e, "why": "stopped"}
            break
        if row["steps12_complete"] and size_ok(arm, e):
            best, best_row = e, row
            if not row["complete"]:
                # stopped in step 3: counts for steps 1-2, and the larger sizes follow the rules
                pass
            continue
        if not row["steps12_complete"]:
            if row["status"] == "killed":
                beat = row["last_heartbeat"] or {}
                memory = row["headroom_ok"] and (beat.get("memory_peak_gib") or 0) >= KILL_PEAK_GIB
                reason = {"elements": e, "why": "memory" if memory else "system"}
            elif row["status"] == "timeout":
                reason = {"elements": e, "why": "time"}
            else:
                reason = {"elements": e, "why": "failure"}
        else:
            reason = {"elements": e, "why": "accuracy"}
        break
    return best, best_row, reason


def seconds(x):
    return f"{x:.1f}" if x < 100 else f"{x:,.0f}"


def summary(record):
    rows = {(a["arm"], a["elements"]): run_row(a) for a in record.get("attempts", [])}
    sizes = []
    common = None
    checks = {}
    for dim in h.DIMS:
        e = dim ** 3
        act, omc = rows.get(("actinv", e)), rows.get(("openmc_flux", e))
        entry = {"elements": e}
        if act and omc and act["complete"] and omc["complete"]:
            ratio, problems = accuracy(act, omc)
            entry.update({"dose_ratio": ratio, "accuracy_problems": problems,
                          "h1_ratio_openmc_over_actinv": omc["h1_s"] / act["h1_s"],
                          "working_set_ratio_openmc_over_actinv":
                              (omc["working_set_gib"] / act["working_set_gib"])
                              if act["working_set_gib"] and omc["working_set_gib"] else None})
            checks[e] = not problems
            if not problems:
                common = (e, omc)
        sizes.append(entry)
    for dim in h.DIMS:
        e = dim ** 3
        act = rows.get(("actinv", e))
        if e in checks or act is None or not act["complete"]:
            continue
        omc = rows.get(("openmc_flux", e))
        if omc is not None and omc["complete"]:
            continue
        if common is None:
            checks[e] = False
            continue
        ratio = act["dose_rate_Sv_h"] / common[1]["dose_rate_Sv_h"]
        checks[e] = DOSE_BAND[0] <= ratio <= DOSE_BAND[1]
        next(s for s in sizes if s["elements"] == e).update(
            {"actinv_dose_vs_openmc_at": common[0], "dose_ratio_vs_common": ratio})

    def size_ok(arm, e):
        row = rows[(arm, e)]
        if not row["complete"]:
            return True  # stopped in step 3: no dose to check; the sentence says so
        return checks.get(e, True) if arm == "openmc_flux" else checks.get(e, False)

    reaches = {}
    for arm in h.ARM_ORDER:
        best, row, reason = reach(rows, record.get("skips", []), arm, size_ok)
        reaches[arm] = {"elements": best, "regions": row["regions"] if row else None,
                        "step3_complete": bool(row and row["complete"]), "next": reason}
    result = {"cpu_model": record.get("environment", {}).get("cpu_model"),
              "runs": [rows[k] for k in sorted(rows, key=lambda k: (k[1], h.ARM_ORDER.index(k[0])))],
              "skips": record.get("skips", []), "sizes": sizes, "reach": reaches}

    sentences = []
    for s in sizes:
        e = s["elements"]
        if checks.get(e) and ("actinv", e) in rows and ("openmc_flux", e) in rows and "dose_ratio" in s:
            act, omc = rows[("actinv", e)], rows[("openmc_flux", e)]
            sentences.append(
                f"On a mixed-material model (a tungsten sphere in CuCrZr, water and steel, 14.1 MeV source) meshed into "
                f"{e:,} elements ({act['regions']:,} activation regions), with the same transport and two CPU threads, "
                f"ACTINV 1.7.1 took {seconds(act['h1_s'])} s from model to photon source and OpenMC 0.15.3 in flux mode "
                f"{seconds(omc['h1_s'])} s, with peak working memory of {act['working_set_gib']:.1f} GiB and "
                f"{omc['working_set_gib']:.1f} GiB (one run each).")
    ra, ro = reaches["actinv"], reaches["openmc_flux"]
    if ra["elements"] and ro["elements"]:
        text = (f"Inside the same {h.MEMORY_MAX_GIB} GiB memory limit and {h.LIMIT_S // 3600}-hour limit per run, "
                f"ACTINV completed meshes up to {ra['elements']:,} elements ({ra['regions']:,} regions) and OpenMC "
                f"0.15.3 in flux mode up to {ro['elements']:,} elements ({ro['regions']:,} regions)")
        for arm in h.ARM_ORDER:
            r = reaches[arm]
            nxt = r["next"]
            who = "ACTINV" if arm == "actinv" else "OpenMC"
            if not r["step3_complete"]:
                text += f"; {who}'s photon transport step did not complete at {r['elements']:,} elements"
            if nxt is None:
                continue
            e = nxt["elements"]
            text += {"memory": f"; at {e:,} elements {who} was stopped by its memory limit",
                     "time": f"; at {e:,} elements {who} was stopped by the time limit",
                     "failure": f"; at {e:,} elements {who} failed",
                     "system": f"; {who} was stopped by the system at {e:,} elements",
                     "projection": f"; {who} was not run at {e:,} elements because its time at the previous size "
                                   f"projected beyond the limit",
                     "stopped": f"; {who} was not run at {e:,} elements",
                     "accuracy": f"; at {e:,} elements {who}'s result failed the accuracy checks"}[nxt["why"]]
        sentences.append(text + ".")
    return result, sentences


def evaluate(record, repo=None):
    gates = {"G0": g0(), "G1": g1(record, repo), "G2": g2(record)}
    gates["G3"] = quiet(record) if not gates["G2"] else ["fidelity failed"]
    result, sentences = summary(record) if not gates["G2"] else ({}, [])
    return gates, result, sentences


# ------------------------------------------------------------------------------------------------------ self-test

def synthetic_record():
    harness_cg = {"path": "/x/run-h.scope", "memory_max": str(6 * GIB), "memory_swap_max": "0", "pids_max": "128",
                  "cpu_max": "200000 100000"}
    run_cg = dict(harness_cg, memory_max=str(10 * GIB))
    repo = {k: "r" * 64 for k in REPO_INPUTS}
    record = {"schema": h.SCHEMA, "smoke": False,
              "inputs": {k: {"sha256": v} for k, v in {**PINS, **repo}.items()},
              "environment": {"xs_archive_sha256": XS_ARCHIVE, "actinv_version": "actinv 1.7.1",
                              "openmc_version": OPENMC_VERSION, "git_dirty_paths": "", "cgroup": harness_cg,
                              "other_scopes_start": [], "other_scopes_end": [], "cpu_model": "x"},
              "plan": {"memory_max_gib": h.MEMORY_MAX_GIB, "limit_s": h.LIMIT_S, "dims": h.DIMS},
              "attempts": [], "skips": []}
    per_element = {"actinv": 0.1, "openmc_flux": 0.5}  # OpenMC is projected out at 32,768 elements
    for dim in h.DIMS:
        e = dim ** 3
        for arm in h.ARM_ORDER:
            expected = h.decide(arm, dim, record["attempts"], False)
            if expected is not None:
                record["skips"].append({"arm": arm, "dim": dim, "elements": e, **expected})
                continue
            t = per_element[arm] * e
            a = {"arm": arm, "dim": dim, "elements": e, "status": "ok", "complete": True, "steps12_complete": True,
                 "phase_done": "step3", "wall_s": 2 * t, "step1_s": t / 2, "step2_s": t / 2, "step3_s": 5.0,
                 "dose_rate_Sv_h": 1.0 if arm == "actinv" else 1.03, "photons_per_s": 1e15,
                 "config": {"n_regions": int(e * 1.2), "energy_groups_in_flux": GROUPS,
                            "flux_sum_n_cm_per_src": 50.0},
                 "cgroup": run_cg, "memory_peak_bytes": 4 * GIB,
                 "peak_rss_mib": {"self": 1024.0 if arm == "actinv" else 6500.0, "children": 10.0},
                 "before": {"load_average": [1.0, 1.0, 1.0], "other_scopes": [],
                            "headroom": {"ok": True}}}
            record["attempts"].append(a)
    return record, repo


def selftest():
    failures = []
    record, repo = synthetic_record()
    gates, result, sentences = evaluate(record, repo)
    if any(gates[g] for g in ("G1", "G2", "G3")):
        failures.append(f"clean synthetic record fails: {gates}")
    else:
        ra, ro = result["reach"]["actinv"], result["reach"]["openmc_flux"]
        if ra["elements"] != 32768 or ra["next"] is not None:
            failures.append(f"ACTINV reach wrong: {ra}")
        if ro["next"] is None or ro["next"]["why"] != "projection":
            failures.append(f"OpenMC reach wrong: {ro}")
        if not any("8 elements" in s and "ACTINV 1.7.1 took" in s for s in sentences):
            failures.append(f"size sentence missing: {sentences}")
        if "because its time at the previous size projected beyond the limit" not in sentences[-1]:
            failures.append(f"reach sentence wrong: {sentences[-1]}")

    def kill_openmc_at(rec, dim, peak, headroom=True):
        """OpenMC killed at `dim` with a heartbeat peak; later OpenMC runs become stop-rule skips."""
        for a in rec["attempts"]:
            if a["arm"] == "openmc_flux" and a["dim"] == dim:
                a.update(status="killed", complete=False, steps12_complete=False, phase_done="step1",
                         step2_s=None, step3_s=None, dose_rate_Sv_h=None,
                         last_heartbeat={"phase": "step2", "memory_peak_bytes": int(peak * GIB)})
                a["before"]["headroom"]["ok"] = headroom
        rec["attempts"] = [a for a in rec["attempts"] if not (a["arm"] == "openmc_flux" and a["dim"] > dim)]
        rec["skips"] = [s for s in rec["skips"] if s["arm"] != "openmc_flux"]
        for d in h.DIMS:
            if d > dim:
                rec["skips"].append({"arm": "openmc_flux", "dim": d, "elements": d ** 3, "rule": "stop"})

    rec, repo = synthetic_record()
    kill_openmc_at(rec, 16, 9.9)
    gates, result, sentences = evaluate(rec, repo)
    if gates["G2"] or result["reach"]["openmc_flux"]["elements"] != 512 or \
            result["reach"]["openmc_flux"]["next"]["why"] != "memory" or "stopped by its memory limit" not in sentences[-1]:
        failures.append(f"memory kill not reported: {gates['G2']} {result.get('reach')}")
    rec, repo = synthetic_record()
    kill_openmc_at(rec, 16, 6.0)
    _, result, sentences = evaluate(rec, repo)
    if result["reach"]["openmc_flux"]["next"]["why"] != "system":
        failures.append("a kill below the limit attributed to memory")
    rec, repo = synthetic_record()
    kill_openmc_at(rec, 16, 9.9, headroom=False)
    _, result, _ = evaluate(rec, repo)
    if result["reach"]["openmc_flux"]["next"]["why"] != "system":
        failures.append("a kill without headroom attributed to memory")
    rec, repo = synthetic_record()
    for a in rec["attempts"]:
        if a["arm"] == "actinv" and a["dim"] == 4:
            a["dose_rate_Sv_h"] = 0.5
    _, result, sentences = evaluate(rec, repo)
    if any("64 elements" in s for s in sentences[:-1]) or result["reach"]["actinv"]["elements"] != 8:
        failures.append("a dose outside the band still earned a sentence or reach")

    cases = {
        "other binary": lambda r: r["inputs"]["actinv_binary"].update(sha256="0" * 64),
        "harness changed": lambda r: r["inputs"]["harness"].update(sha256="0" * 64),
        "smoke": lambda r: r.update(smoke=True),
        "other OpenMC": lambda r: r["environment"].update(openmc_version="0.16.0"),
        "other ACTINV": lambda r: r["environment"].update(actinv_version="actinv 1.7.0"),
        "dirty tree": lambda r: r["environment"].update(git_dirty_paths=" M x"),
        "unbounded harness": lambda r: r["environment"]["cgroup"].update(cpu_max="max 100000"),
        "run scope 12 GiB": lambda r: r["attempts"][3].update(cgroup=dict(r["attempts"][3]["cgroup"],
                                                                         memory_max=str(12 * GIB))),
        "run missing": lambda r: r["attempts"].pop(2),
        "run despite the rule": lambda r: r["skips"].pop(),
        "8 elements incomplete": lambda r: r["attempts"][0].update(complete=False),
        "no dose": lambda r: r["attempts"][1].update(dose_rate_Sv_h=None),
        "wrong groups": lambda r: r["attempts"][1]["config"].update(energy_groups_in_flux=708),
        "other scope before": lambda r: r["attempts"][2]["before"].update(other_scopes=["run-x.scope"]),
        "other scope at end": lambda r: r["environment"].update(other_scopes_end=["run-y.scope"]),
        "busy machine": lambda r: [a["before"].update(load_average=[5.0]) for a in r["attempts"]],
    }
    for name, mutate in cases.items():
        rec, repo = synthetic_record()
        mutate(rec)
        gates, _, _ = evaluate(rec, repo)
        if not (gates["G1"] or gates["G2"] or gates["G3"]):
            failures.append(f"not caught: {name}")
    for failure in failures:
        print("selftest:", failure)
    print("selftest:", "PASS" if not failures else "FAIL")
    return not failures


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--record", type=Path, default=RECORD)
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        sys.exit(0 if selftest() else 1)
    record = json.loads(args.record.read_text())
    gates, result, sentences = evaluate(record)
    passed = all(not problems for problems in gates.values())
    if not passed:
        sentences = []
    verdict = {
        "protocol": "ACTINV-P181",
        "verdict": "P181-PASS (pending G4, CI on the evidence commit)" if passed else "P181-FAIL",
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
                print("G3: FAIL (verdict file differs from the recomputed summary)")
                passed = False
    elif args.record.resolve() == RECORD.resolve():
        VERDICT.write_text(json.dumps(verdict, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
    print("outcome:")
    for sentence in sentences:
        print(f"  {sentence}")
    print(verdict["verdict"] if passed else "P181-FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
