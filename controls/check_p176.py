#!/usr/bin/env python3
"""P176 checker (protocols/ACTINV-P176_PROTOCOL.md): ACTINV's optional self-shielding against the P173 continuous-energy
tallies. Reads results/p176_selfshield.json written by `controls/p176_selfshield.py assemble`. G0-G3 are evaluated here;
G4 (CI on the pushed evidence commit) is external.

  G0  The protocol's SHA-256 is in protocols/protocol_hash.txt; the record's harness, group-structure and constants
      hashes match the files (and the harness' own constants).
  G1  Pinned inputs (the ACTINV library, the P173 records and OpenMC files, the evaluations, the decay files), the pinned
      P173 `actinv` binary, a clean tree, the pinned Bondarenko table, the bounded cgroup of every mode, shielding active
      in the runs that should have it and absent in the control, and the control arm (A0) reproducing P173's total-row
      rates to 1e-9 relative on every row.
  G2  Fidelity: one run per (arm, region); each run's problem recomputes from the harness to its recorded digest; every
      P173 row is scored in every arm or not scored with a registered reason; each scored row's q, R_ss and r_ss
      recompute from the stored rates and agree with the P173 record.
  G3  The decision rules (removes the capture differences, safe, item 2) are recomputed from the record and written to
      results/p176_verdict.json (with --no-write, compared with it).

Verdict: P176-PASS if G0-G3 hold (G4 external), whatever the outcome.

Usage: check_p176.py [--record FILE] [--no-write]
       check_p176.py --self-test
"""
import argparse
import copy
import json
import math
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "controls"))
if os.environ.get("P173_REHEARSAL") == "1":
    sys.exit("check_p176: P173_REHEARSAL must not be set")
import check_p173 as c173  # noqa: E402
import p173_ce_rates as p173  # noqa: E402
import p176_selfshield as h  # noqa: E402

PROTOCOL = ROOT / "protocols" / "ACTINV-P176_PROTOCOL.md"
HASH_LOG = ROOT / "protocols" / "protocol_hash.txt"
HARNESS = ROOT / "controls" / "p176_selfshield.py"
RECORD = h.RECORD
VERDICT = ROOT / "target/p176-rehearsal/verdict.json" if h.REHEARSAL else ROOT / "results" / "p176_verdict.json"

MATERIAL_ABS_R = h.MATERIAL_ABS_R
MATERIAL_SIGMAS = h.MATERIAL_SIGMAS
CONSISTENCY = 1e-9
SENTENCE_MODELS = ("S", "C")
ARM_WORDS = {"A1": "Bondarenko self-shielding",
             "A2": "geometry self-shielding (a foil of the tallied region's thickness)",
             "A3": "geometry self-shielding (a foil of the whole model body's thickness)"}
sha256, good, close = c173.sha256, c173.good, c173.close


def material(value, se):
    """|x| > 0.05 and |x| > 3 se(x), the rule P173 applies to r, r_proc and r_coll."""
    return abs(value) > MATERIAL_ABS_R and abs(value) > MATERIAL_SIGMAS * se


def is_used(row):
    """A row P173 scored with a converged OpenMC rate, scored in the arm too."""
    return row.get("status") == "scored" and row.get("p173_converged") is True


def row_key(row):
    return (row["arm"], row["model"], row["region"], row["target"], row["mt"])


def expected_keys():
    return {(arm, *key) for arm in h.SCORED_ARMS for key in p173.expected_rows()}


# ------------------------------------------------------------------------------------------------------ gates

def g0(record, *, protocol=PROTOCOL, log_text=None, harness=HARNESS, groups=p173.GROUPS):
    if not protocol.is_file():
        return [f"missing {protocol.name}"]
    log_text = HASH_LOG.read_text() if log_text is None else log_text
    problems = []
    if f"{sha256(protocol)}  protocols/{protocol.name}" not in log_text:
        problems.append(f"{protocol.name} hash not in protocol_hash.txt")
    env = record.get("environment", {})
    if env.get("harness_sha256") != sha256(harness):
        problems.append("the record's harness hash is not the harness file's")
    if env.get("groups_sha256") != sha256(groups):
        problems.append("the record's group-structure hash is not the file's")
    if env.get("constants_sha256") != h.constants_digest():
        problems.append("the record's constants hash is not the harness' constants (arms, rules, evaluations)")
    return problems


def g1(record, *, bin_pin=h.ACTINV_BIN_SHA256, table_pin=h.SHIELD_TABLE_SHA256):
    problems = []
    if record.get("schema") != h.SCHEMA:
        problems.append("wrong schema")
    inputs = record.get("inputs", {})
    if set(inputs) != set(h.PINNED):
        problems.append("the recorded inputs are not the pinned set")
    for name, (_, digest) in h.PINNED.items():
        if inputs.get(name, {}).get("sha256") != digest:
            problems.append(f"input {name} hash is not the pin")
    if record.get("endf_files") != {nuc: {"file": name, "sha256": digest} for nuc, (name, digest) in
                                    h.ENDF_FILES.items()}:
        problems.append("the recorded evaluation files are not the pinned ones")
    env = record.get("environment", {})
    if not env.get("clean_tree"):
        problems.append("tracked tree was not clean")
    if env.get("rehearsal") is not False:
        problems.append("the record is not a scored (non-rehearsal) record")
    if bin_pin is None:
        problems.append("the actinv binary digest is not pinned")
    elif env.get("actinv_bin_sha256") != [bin_pin]:
        problems.append("the runs did not all use the pinned P173 actinv binary")
    table = record.get("table", {})
    if table_pin is None:
        problems.append("the Bondarenko table digest is not pinned yet")
    elif table.get("sha256") != table_pin:
        problems.append("the Bondarenko table is not the pinned one")
    if table.get("returncode") != 0 or not table.get("nuclides"):
        problems.append("the table build did not succeed")
    seen = {entry.get("where"): entry.get("cgroup") for entry in env.get("cgroups", [])}
    required = ["table", "assemble"] + [f"arm:{a}:{m}" for a in h.ARM_ORDER for m in p173.MODEL_ORDER]
    for where in required:
        if where not in seen:
            problems.append(f"no cgroup record for {where}")
    for where, cg in sorted(seen.items(), key=lambda kv: str(kv[0])):
        if not p173.cgroup_ok(cg or {}):
            problems.append(f"cgroup of {where} is not the bounded scope")
    # shielding must be active where the arm asks for it and absent in the control
    for run in record.get("runs", []):
        if not run.get("ok"):
            continue
        shielding, name = run.get("shielding"), f"{run.get('arm')}/{run.get('region')}"
        if run.get("arm") == "A0" and shielding is not None:
            problems.append(f"{name}: the control run reports self-shielding")
        elif run.get("arm") in h.SCORED_ARMS and not isinstance(shielding, dict):
            problems.append(f"{name}: the run's ledger has no self-shielding block")
        elif run.get("arm") == "A1" and shielding.get("table_sha256") != table.get("sha256"):
            problems.append(f"{name}: the run used another Bondarenko table")
    # the control reproduces P173's rates
    check = record.get("a0_check", {})
    if check.get("tolerance") != h.G1_TOLERANCE:
        problems.append("the control check's tolerance is not the registered one")
    rows = check.get("rows", [])
    keys = [(r.get("model"), r.get("region"), r.get("target"), r.get("mt")) for r in rows]
    if len(set(keys)) != len(keys) or set(keys) != set(p173.expected_rows()):
        problems.append("the control check does not cover every registered row exactly once")
    for r in rows:
        name = f"{r.get('model')}/{r.get('region')}/{r.get('target')} MT{r.get('mt')}"
        theirs, ours = r.get("p173_total_row_rate_per_atom"), r.get("a0_loss_rate_per_atom")
        if r.get("a0_run_ok") is not True:
            problems.append(f"control check {name}: the control run failed")
        elif theirs is None:
            if ours is not None:
                problems.append(f"control check {name}: a loss channel exists where P173 had no total row")
        elif not (good(ours) and (ours == theirs == 0.0 or abs(ours - theirs) / max(abs(ours), abs(theirs))
                                  <= h.G1_TOLERANCE)):
            problems.append(f"control check {name}: the control's loss rate differs from P173's total-row rate")
    return problems[:200]


def g2(record, p173_rows, *, spec_check=None):
    problems = []
    runs = record.get("runs", [])
    keys = [(r.get("arm"), r.get("region")) for r in runs]
    expected_runs = {(arm, region) for arm in h.ARM_ORDER for m in p173.MODEL_ORDER for region in p173.regions_of(m)}
    if len(set(keys)) != len(keys) or set(keys) != expected_runs:
        problems.append("the runs are not one per (arm, region)")
    run_by = {(r.get("arm"), r.get("region")): r for r in runs}
    for r in runs:
        name = f"{r.get('arm')}/{r.get('region')}"
        if not (isinstance(r.get("spec_sha256"), str) and len(r["spec_sha256"]) == 64):
            problems.append(f"{name}: no problem digest")
        elif spec_check is not None and spec_check(r) != r["spec_sha256"]:
            problems.append(f"{name}: the recorded problem is not the one the harness writes")
        if r.get("ok") is True and (r.get("run_returncode") != 0 or not isinstance(r.get("rates"), dict)):
            problems.append(f"{name}: marked ok without a successful run and its rates")
        if r.get("ok") is not True and r.get("run_returncode") == 0 and not r.get("error"):
            problems.append(f"{name}: a successful run is marked failed without an error")
    rows = record.get("rows", [])
    rkeys = [row_key(r) for r in rows]
    if len(set(rkeys)) != len(rkeys):
        problems.append("duplicate rows")
    if set(rkeys) != expected_keys():
        missing, extra = expected_keys() - set(rkeys), set(rkeys) - expected_keys()
        problems.append(f"rows differ from the registered list: {len(missing)} missing, {len(extra)} unexpected")
    for r in rows:
        name = f"{r.get('arm')}/{r.get('model')}/{r.get('region')}/{r.get('target')} MT{r.get('mt')}"
        base = p173_rows.get((r.get("model"), r.get("region"), r.get("target"), r.get("mt")))
        run, ctl = run_by.get((r.get("arm"), r.get("region"))), run_by.get(("A0", r.get("region")))
        if base is None or run is None or ctl is None:
            problems.append(f"{name}: no P173 row or no run")
            continue
        if r.get("p173_status") != base["status"]:
            problems.append(f"{name}: P173 status differs from the P173 record")
        key = p173.rate_key(r["target"], r["mt"])
        if r.get("dep_rate_arm") != (run.get("rates") or {}).get(key) or \
                r.get("dep_rate_a0") != (ctl.get("rates") or {}).get(key):
            problems.append(f"{name}: stored loss rates differ from the runs' rates")
        if r.get("status") == "not_scored":
            code = r.get("reason_code")
            if code not in h.NOT_SCORED_CODES or not r.get("reason"):
                problems.append(f"{name}: not scored without a registered reason")
            elif (code == "p173_not_scored") != (base["status"] != "scored"):
                problems.append(f"{name}: reason p173_not_scored does not follow the P173 status")
            elif code == "arm_failed" and run.get("ok") is True and ctl.get("ok") is True:
                problems.append(f"{name}: arm_failed although both runs succeeded")
            elif code == "no_loss_row" and (r.get("dep_rate_arm") is not None and r.get("dep_rate_a0") is not None):
                problems.append(f"{name}: no_loss_row although both loss rates exist")
            continue
        if r.get("status") != "scored":
            problems.append(f"{name}: status {r.get('status')!r}")
            continue
        if base["status"] != "scored" or not (run.get("ok") and ctl.get("ok")):
            problems.append(f"{name}: scored although P173 did not score it or a run failed")
            continue
        for mine, theirs in (("R_actinv", "R_actinv"), ("R_openmc", "R_openmc"), ("p173_r", "r"), ("se_r", "se_r"),
                             ("p173_r_coll", "r_coll"), ("se_r_coll", "se_r_coll"), ("p173_r_proc", "r_proc")):
            if r.get(mine) != base[theirs]:
                problems.append(f"{name}: {mine} differs from the P173 record")
        if r.get("p173_converged") is not bool(base["converged"]):
            problems.append(f"{name}: converged flag differs from the P173 record")
        vals = [r.get(k) for k in ("dep_rate_a0", "dep_rate_arm", "q", "R_ss", "r_ss")]
        if not all(good(v) for v in vals) or not (r["dep_rate_a0"] > 0 and r["dep_rate_arm"] > 0):
            problems.append(f"{name}: scored row without finite positive rates")
            continue
        if not close(r["q"], r["dep_rate_arm"] / r["dep_rate_a0"]):
            problems.append(f"{name}: q does not recompute from the loss rates")
        if not close(r["R_ss"], base["R_actinv"] * r["q"]):
            problems.append(f"{name}: R_ss does not recompute")
        if not (close(r["r_ss"], math.log(r["R_ss"] / base["R_openmc"])) and close(r["r_ss"], base["r"] + math.log(r["q"]))):
            problems.append(f"{name}: r_ss does not recompute")
    return problems[:200]


# ------------------------------------------------------------------------------------------------------ decisions

def round_up_tenth(percent):
    return math.ceil(percent * 10.0 - 1e-9) / 10.0


def arm_summary(arm, rows):
    """The decision quantities of one arm from its rows."""
    mine = [r for r in rows if r["arm"] == arm]
    # P173's material-collapse rows: scored, converged, |r_coll| > 0.05 and > 3 se
    collapse = [r for r in mine if r.get("p173_converged") is True and "p173_r_coll" in r
                and material(r["p173_r_coll"], r["se_r_coll"])]
    # P173's used rows that were not material in r
    population = [r for r in mine if r.get("p173_converged") is True and "p173_r" in r
                  and not material(r["p173_r"], r["se_r"])]
    material_collapse_after = [r for r in collapse if r["status"] == "scored" and material(r["r_ss"], r["se_r"])]
    collapse_unscored = [r for r in collapse if r["status"] != "scored"]
    removes = bool(collapse) and not material_collapse_after and not collapse_unscored
    harmed = [r for r in population if r["status"] == "scored" and material(r["r_ss"], r["se_r"])]
    unassessed = [r for r in population if r["status"] != "scored"]
    unassessable = [r for r in unassessed if r["reason_code"] in ("arm_failed", "rate_zero")]
    safe = not harmed and not unassessable
    used = [r for r in mine if is_used(r)]
    capture = [r for r in used if r["mt"] == 102 and r["model"] in SENTENCE_MODELS]
    capture_listed = [r for r in mine if r["mt"] == 102 and r["model"] in SENTENCE_MODELS]

    def brief(r):
        return {"region": r["region"], "reaction": r["reaction"], "p173_r": r.get("p173_r"),
                "p173_r_coll": r.get("p173_r_coll"), "r_ss": r.get("r_ss"), "se_r": r.get("se_r"), "q": r.get("q"),
                "reason_code": r.get("reason_code")}

    def max_abs(rs):
        return max((abs(r["r_ss"]) for r in rs), default=None)
    return {
        "rows_used": len(used), "rows_not_scored": sum(r["status"] == "not_scored" for r in mine),
        "not_scored_by_reason": {code: sum(r.get("reason_code") == code for r in mine) for code in h.NOT_SCORED_CODES},
        "p173_material_collapse_rows": [brief(r) for r in collapse],
        "material_collapse_rows_after": [brief(r) for r in material_collapse_after],
        "material_collapse_rows_not_scored": [brief(r) for r in collapse_unscored],
        "removes_capture_differences": removes,
        "p173_non_material_rows": len(population), "rows_made_material": [brief(r) for r in harmed],
        "rows_unassessed": [brief(r) for r in unassessed], "safe": safe,
        "max_abs_r_ss_material_collapse_rows": max_abs([r for r in collapse if r["status"] == "scored"]),
        "max_abs_r_ss_used": max_abs(used),
        "by_model": {m: {"rows_used": sum(r["model"] == m for r in used),
                         "max_abs_r_ss": max_abs([r for r in used if r["model"] == m])} for m in p173.MODEL_ORDER},
        "max_percent_capture_steel_and_cucrzr": (max(math.expm1(abs(r["r_ss"])) * 100.0 for r in capture)
                                                 if capture else None),
        "capture_rows_listed_steel_and_cucrzr": len(capture_listed), "capture_rows_used_steel_and_cucrzr": len(capture),
        "candidate": removes and safe,
    }


def summary(record):
    rows = record["rows"]
    arms = {arm: arm_summary(arm, rows) for arm in h.SCORED_ARMS}
    candidates = [arm for arm, s in arms.items() if s["candidate"]]
    sentences = []
    for arm in candidates:
        s = arms[arm]
        if not s["rows_used"] or s["max_percent_capture_steel_and_cucrzr"] is None:
            continue
        x = round_up_tenth(s["max_percent_capture_steel_and_cucrzr"])
        sentences.append(f"With its optional {ARM_WORDS[arm]}, ACTINV's 709-group capture rates in the tested steel and "
                         f"copper-alloy shells agree with OpenMC continuous-energy tallies on the same data within "
                         f"{x:.1f}%.")
        uncovered = s["capture_rows_listed_steel_and_cucrzr"] - s["capture_rows_used_steel_and_cucrzr"]
        if uncovered:
            sentences.append(f"{uncovered} of {s['capture_rows_listed_steel_and_cucrzr']} listed capture rows in those "
                             f"shells were not scored or did not reach a {100 * p173.REL_SE_TARGET:.1f}% standard error "
                             f"and are not covered by that sentence.")
    return {"arms": arms, "candidate_arms": candidates, "item2_proceeds": not candidates,
            "self_shielding_default_for_r2s_and_mesh_is_a_later_protocol": bool(candidates)}, sentences


def evaluate(record, p173_rows, *, g0_args=None, bin_pin=h.ACTINV_BIN_SHA256, table_pin=h.SHIELD_TABLE_SHA256,
             spec_check=None):
    gates = {"G0": g0(record, **(g0_args or {})), "G1": g1(record, bin_pin=bin_pin, table_pin=table_pin),
             "G2": g2(record, p173_rows, spec_check=spec_check)}
    if gates["G2"]:
        result, sentences = {}, []
        gates["G3"] = ["summary not computed (fidelity failed)"]
    else:
        result, sentences = summary(record)
        gates["G3"] = []
    return gates, result, sentences


def load_p173_rows():
    return {(r["model"], r["region"], r["target"], r["mt"]): r for r in json.loads(h.P173_RECORD.read_text())["rows"]}


def real_spec_check(record):
    """spec_check(run) -> the digest the harness' problem for that run has, from the pinned P173 data."""
    data = h.load_p173_data()
    table = (Path("shield_table.json"), record.get("table", {}).get("sha256"))
    model_of = {region: m for m in p173.MODEL_ORDER for region in p173.regions_of(m)}

    def check(run):
        model = model_of[run["region"]]
        spec = h.build_spec(run["arm"], model, run["region"], data, table)
        return h.spec_digest(spec)
    return check


# ------------------------------------------------------------------------------------------------------ self-test

CG = {"memory.max": str(6 * p173.GIB), "memory.swap.max": "0", "pids.max": "128", "cpu.max": "200000 100000"}
COLLAPSE_ROWS = [("S", region, {("Mn55", 102), ("Co59", 102), ("Mo98", 102)}) for region in p173.regions_of("S")] + \
                [("C", region, {("Cu63", 102)}) for region in p173.regions_of("C")]


def synthetic_p173(harness_sha, groups_sha):
    """A P173 record whose 11 material-collapse rows are the ones the real P173 found, everything else agreeing."""
    record = c173.synthetic_record(harness_sha, groups_sha)
    for model, region, reactions in COLLAPSE_ROWS:
        c173.set_rates(record, model, region, reactions, actinv=1.08, omg=1.08)
    for row in record["rows"]:
        row["total_row_R_actinv"] = row["R_actinv"]
    return {(r["model"], r["region"], r["target"], r["mt"]): r for r in record["rows"]}


def synthetic(p173_rows, harness_sha, groups_sha, q_of, *, table_pin="t" * 64, bin_pin="b" * 64):
    """A P176 record: `q_of(arm, p173_row)` is each arm's loss-rate ratio to the control."""
    cgroups = [{"where": "table", "cgroup": dict(CG)}, {"where": "assemble", "cgroup": dict(CG)}]
    cgroups += [{"where": f"arm:{a}:{m}", "cgroup": dict(CG)} for a in h.ARM_ORDER for m in p173.MODEL_ORDER]
    runs, rows, a0_rows = [], [], []
    for arm in h.ARM_ORDER:
        for model in p173.MODEL_ORDER:
            for region in p173.regions_of(model):
                rates = {}
                for target, mt, _ in p173.model_spec(model)["reactions"]:
                    base = p173_rows[(model, region, target, mt)]
                    rates[p173.rate_key(target, mt)] = base["R_actinv"] * 1e-24 * (1.0 if arm == "A0" else q_of(arm, base))
                shielding = None if arm == "A0" else ({"table_sha256": table_pin} if arm == "A1" else {"geometry": {}})
                runs.append({"arm": arm, "model": model, "region": region, "spec_sha256": "s" * 64, "ok": True,
                             "validate_returncode": 0, "run_returncode": 0, "rates": rates, "shielding": shielding})
    run_by = {(r["arm"], r["region"]): r for r in runs}
    for (model, region, target, mt), base in p173_rows.items():
        key = p173.rate_key(target, mt)
        a0_rows.append({"model": model, "region": region, "target": target, "mt": mt,
                        "p173_total_row_rate_per_atom": base["total_row_R_actinv"] * 1e-24 if base.get(
                            "total_row_R_actinv") is not None else None,
                        "a0_loss_rate_per_atom": run_by[("A0", region)]["rates"][key], "a0_run_ok": True})
        for arm in h.SCORED_ARMS:
            rows.append(h.build_ss_row(arm, base, run_by[("A0", region)]["rates"][key],
                                       run_by[(arm, region)]["rates"][key], True))
    return {"schema": h.SCHEMA,
            "inputs": {k: {"sha256": v} for k, (_, v) in h.PINNED.items()},
            "endf_files": {nuc: {"file": n, "sha256": d} for nuc, (n, d) in h.ENDF_FILES.items()},
            "environment": {"harness_sha256": harness_sha, "groups_sha256": groups_sha,
                            "constants_sha256": h.constants_digest(), "clean_tree": True, "rehearsal": False,
                            "actinv_bin_sha256": [bin_pin], "cgroups": cgroups},
            "table": {"returncode": 0, "sha256": table_pin, "nuclides": ["Mn55"]},
            "runs": runs, "a0_check": {"tolerance": h.G1_TOLERANCE, "rows": a0_rows}, "rows": rows}


def undo_collapse(arm, base):
    """q that removes the collapse difference of a material-collapse row and leaves the others alone."""
    return math.exp(-base["r"]) if material(base["r_coll"], base["se_r_coll"]) else 1.0


def fake_endf_line(fields, number):
    return "".join(f"{f:>11}" for f in fields) + f"{2525:>4}{2:>2}{151:>3}{number:>5}"


def selftest():
    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        protocol, harness, groups = tmp / "ACTINV-P176_PROTOCOL.md", tmp / "harness.py", tmp / "groups.json"
        protocol.write_text("protocol text")
        harness.write_text("harness text")
        groups.write_text("groups text")
        log_text = f"{sha256(protocol)}  protocols/{protocol.name}\n"
        g0_args = {"protocol": protocol, "log_text": log_text, "harness": harness, "groups": groups}
        pin, tpin = "b" * 64, "t" * 64
        hs, gs = sha256(harness), sha256(groups)
        base_rows = synthetic_p173(hs, gs)
        collapse_keys = {k for k, r in base_rows.items() if material(r["r_coll"], r["se_r_coll"])}
        if len(collapse_keys) != 11:
            failures.append(f"synthetic P173 has {len(collapse_keys)} material-collapse rows, expected 11")

        def fresh(q_of):
            return synthetic(base_rows, hs, gs, q_of)

        def run(label, q_of, mutate, gate, *, args=None, pin_=pin, tpin_=tpin):
            trial = fresh(q_of)
            mutate(trial)
            gates, result, sentences = evaluate(trial, base_rows, g0_args=dict(g0_args, **(args or {})), bin_pin=pin_,
                                                table_pin=tpin_)
            bad = [name for name, problems in gates.items() if problems and name != "G3"]
            if gate is None and bad:
                failures.append(f"{label}: expected pass, failed {bad}: {[gates[b][:3] for b in bad]}")
            if gate is not None and gate not in bad:
                failures.append(f"{label}: expected {gate} to fail")
            return result, sentences

        null = lambda arm, base: 1.0  # noqa: E731
        # no arm changes anything: the 11 rows stay material, item 2 proceeds, no sentence
        result, sentences = run("null arms", null, lambda r: None, None)
        if (not result or result["candidate_arms"] or not result["item2_proceeds"] or sentences
                or any(result["arms"][a]["removes_capture_differences"] for a in h.SCORED_ARMS)
                or len(result["arms"]["A1"]["p173_material_collapse_rows"]) != 11
                or result["arms"]["A1"]["rows_used"] != 78):
            failures.append(f"null arms wrong: {result}")
        # an arm that removes all 11 and touches nothing else is a candidate and earns its sentence
        result, sentences = run("A1 removes", lambda a, b: undo_collapse(a, b) if a == "A1" else 1.0,
                                lambda r: None, None)
        a1 = result["arms"]["A1"]
        if (result["candidate_arms"] != ["A1"] or result["item2_proceeds"] or not a1["removes_capture_differences"]
                or not a1["safe"] or a1["material_collapse_rows_after"] or len(sentences) != 1
                or not sentences[0].startswith("With its optional Bondarenko self-shielding, ACTINV's 709-group capture")
                or "within 1.0%" not in sentences[0]):
            failures.append(f"A1 removes: wrong: {a1.get('removes_capture_differences')} {sentences}")
        # removes but harms a non-material row (tungsten capture shifted by 8%): not a candidate
        harm = ("W", "W:10-10.5", "W186", 102)

        def harmful(arm, base):
            if arm != "A2":
                return 1.0
            return 1.08 if (base["model"], base["region"], base["target"], base["mt"]) == harm else undo_collapse(arm, base)
        result, sentences = run("removes but harms", harmful, lambda r: None, None)
        a2 = result["arms"]["A2"]
        if a2["candidate"] or not a2["removes_capture_differences"] or a2["safe"] or len(a2["rows_made_material"]) != 1 \
                or sentences or not result["item2_proceeds"]:
            failures.append(f"removes-but-harms wrong: {a2}")
        # safe but removes only ten of eleven
        miss = ("S", "S:20-25", "Mo98", 102)
        result, _ = run("one left", lambda a, b: 1.0 if (b["model"], b["region"], b["target"], b["mt"]) == miss
                        else undo_collapse(a, b), lambda r: None, None)
        if result["arms"]["A3"]["removes_capture_differences"] or result["candidate_arms"]:
            failures.append("a material collapse row left in place must stop the arm from removing the differences")
        # overshoot: q too small leaves |r_ss| > 0.05 the other way
        result, _ = run("overshoot", lambda a, b: undo_collapse(a, b) * 0.85 if undo_collapse(a, b) != 1.0 else 1.0,
                        lambda r: None, None)
        if result["candidate_arms"]:
            failures.append("an overshoot beyond 5% must not count as removing the difference")
        # within tolerance but not exact: |r_ss| 0.04 counts as removed
        result, _ = run("within tolerance", lambda a, b: math.exp(-b["r"] + 0.04) if undo_collapse(a, b) != 1.0 else 1.0,
                        lambda r: None, None)
        if sorted(result["candidate_arms"]) != ["A1", "A2", "A3"]:
            failures.append(f"a residual of 4% is within the tolerance: {result['candidate_arms']}")

        # a row that cannot be assessed (no loss channel) is listed and does not block, but a failed arm does
        def no_channel(r):
            key = p173.rate_key("Fe56", 103)
            for run_ in r["runs"]:
                if run_["region"] == "S:10-12" and run_["arm"] in ("A0", "A1"):
                    run_["rates"][key] = None
            for row in r["rows"]:
                if row["region"] == "S:10-12" and row["target"] == "Fe56" and row["mt"] == 103:
                    if row["arm"] == "A1":
                        row.update(h.build_ss_row("A1", base_rows[("S", "S:10-12", "Fe56", 103)], None, None, True))
                    row["dep_rate_a0"] = None
                    if row["arm"] != "A1":
                        row.update(h.build_ss_row(row["arm"], base_rows[("S", "S:10-12", "Fe56", 103)], None,
                                                  row["dep_rate_arm"], True))
            for entry in r["a0_check"]["rows"]:
                if (entry["region"], entry["target"], entry["mt"]) == ("S:10-12", "Fe56", 103):
                    entry.update(p173_total_row_rate_per_atom=None, a0_loss_rate_per_atom=None)
        result, _ = run("no loss channel", lambda a, b: undo_collapse(a, b) if a == "A1" else 1.0, no_channel, None)
        a1 = result["arms"]["A1"]
        if len(a1["rows_unassessed"]) != 1 or not a1["candidate"] or a1["not_scored_by_reason"]["no_loss_row"] != 1:
            failures.append(f"a missing loss channel must be listed and not block: {a1['rows_unassessed']}")

        def failed_arm(r):
            for run_ in r["runs"]:
                if run_["arm"] == "A2" and run_["region"] == "W:15-20":
                    run_.update(ok=False, run_returncode=1, rates=None, shielding=None)
            for row in r["rows"]:
                if row["arm"] == "A2" and row["region"] == "W:15-20":
                    row.update(h.build_ss_row("A2", base_rows[(row["model"], row["region"], row["target"], row["mt"])],
                                              row["dep_rate_a0"], None, False))
                    row["dep_rate_arm"] = None
        result, _ = run("failed arm run", lambda a, b: undo_collapse(a, b), failed_arm, None)
        if result["arms"]["A2"]["candidate"] or result["arms"]["A2"]["safe"] or not result["arms"]["A1"]["candidate"]:
            failures.append("an arm whose run failed cannot be declared safe, and must not affect other arms")
        # sentence value: the largest capture residual in steel and CuCrZr, rounded up
        result, sentences = run("sentence value", lambda a, b: math.exp(-b["r"] + 0.0431) if a == "A1" and
                                undo_collapse(a, b) != 1.0 else (1.0 if a != "A1" else 1.0),
                                lambda r: None, None)
        if not sentences or "within 4.5%" not in sentences[0]:
            failures.append(f"sentence percentage wrong: {sentences}")
        # failures
        run("protocol hash", null, lambda r: None, "G0", args={"log_text": ""})
        run("harness hash", null, lambda r: r["environment"].update(harness_sha256="0" * 64), "G0")
        run("groups hash", null, lambda r: r["environment"].update(groups_sha256="0" * 64), "G0")
        run("constants hash", null, lambda r: r["environment"].update(constants_sha256="0" * 64), "G0")
        run("schema", null, lambda r: r.update(schema="x"), "G1")
        run("input pin", null, lambda r: r["inputs"]["p173_record"].update(sha256="0" * 64), "G1")
        run("input missing", null, lambda r: r["inputs"].pop("p173_verdict"), "G1")
        run("endf pin", null, lambda r: r["endf_files"]["Fe56"].update(sha256="0" * 64), "G1")
        run("dirty", null, lambda r: r["environment"].update(clean_tree=False), "G1")
        run("rehearsal record", null, lambda r: r["environment"].update(rehearsal=True), "G1")
        run("binary", null, lambda r: r["environment"].update(actinv_bin_sha256=["0" * 64]), "G1")
        run("two binaries", null, lambda r: r["environment"].update(actinv_bin_sha256=[pin, "0" * 64]), "G1")
        run("unpinned binary", null, lambda r: None, "G1", pin_=None)
        run("unpinned table", null, lambda r: None, "G1", tpin_=None)
        run("other table", null, lambda r: r["table"].update(sha256="0" * 64), "G1")
        run("table failed", null, lambda r: r["table"].update(returncode=1), "G1")
        run("cgroup", null, lambda r: r["environment"]["cgroups"][3]["cgroup"].update({"memory.swap.max": "max"}), "G1")
        run("cgroup missing", null, lambda r: r["environment"].update(
            cgroups=[c for c in r["environment"]["cgroups"] if c["where"] != "arm:A2:W"]), "G1")
        run("control reports shielding", null, lambda r: next(
            x for x in r["runs"] if x["arm"] == "A0").update(shielding={"x": 1}), "G1")
        run("arm without shielding", null, lambda r: next(
            x for x in r["runs"] if x["arm"] == "A2").update(shielding=None), "G1")
        run("another table in A1", null, lambda r: next(
            x for x in r["runs"] if x["arm"] == "A1").update(shielding={"table_sha256": "0" * 64}), "G1")
        run("control differs from P173", null, lambda r: r["a0_check"]["rows"][5].update(
            a0_loss_rate_per_atom=r["a0_check"]["rows"][5]["a0_loss_rate_per_atom"] * (1 + 1e-6)), "G1")
        run("control extra channel", null, lambda r: r["a0_check"]["rows"][5].update(p173_total_row_rate_per_atom=None),
            "G1")
        run("control run failed", null, lambda r: r["a0_check"]["rows"][2].update(a0_run_ok=False), "G1")
        run("control row missing", null, lambda r: r["a0_check"]["rows"].pop(), "G1")
        run("control tolerance", null, lambda r: r["a0_check"].update(tolerance=1e-6), "G1")
        run("row missing", null, lambda r: r["rows"].pop(5), "G2")
        run("row duplicated", null, lambda r: r["rows"].append(dict(r["rows"][0])), "G2")
        run("unregistered arm row", null, lambda r: r["rows"].append(dict(r["rows"][0], arm="A9")), "G2")
        run("run missing", null, lambda r: r["runs"].pop(7), "G2")
        run("run duplicated", null, lambda r: r["runs"].append(dict(r["runs"][0])), "G2")
        run("run ok without rc", null, lambda r: r["runs"][4].update(run_returncode=1), "G2")
        run("not scored without reason", null, lambda r: r["rows"][4].update(status="not_scored", reason_code=None,
                                                                              reason=""), "G2")
        run("wrong reason", null, lambda r: r["rows"][4].update(status="not_scored", reason_code="p173_not_scored",
                                                                 reason="x"), "G2")
        run("q does not recompute", null, lambda r: r["rows"][7].update(q=1.5), "G2")
        run("r_ss does not recompute", null, lambda r: r["rows"][8].update(r_ss=r["rows"][8]["r_ss"] + 0.01), "G2")
        run("R_ss does not recompute", null, lambda r: r["rows"][8].update(R_ss=r["rows"][8]["R_ss"] * 1.01), "G2")
        run("P173 field altered", null, lambda r: r["rows"][9].update(p173_r=r["rows"][9]["p173_r"] + 0.1), "G2")
        run("P173 rate altered", null, lambda r: r["rows"][9].update(R_openmc=r["rows"][9]["R_openmc"] * 2), "G2")
        run("loss rate differs from the run", null, lambda r: r["rows"][6].update(dep_rate_arm=1.0), "G2")
        run("nan rate", null, lambda r: r["rows"][3].update(R_ss=float("nan")), "G2")
        run("problem digest", null, lambda r: r["runs"][3].update(spec_sha256="short"), "G2")
        # the problem digest is checked against the harness when a checker is given
        trial = fresh(null)
        gates, _, _ = evaluate(trial, base_rows, g0_args=g0_args, spec_check=lambda run_: "0" * 64)
        if not gates["G2"]:
            failures.append("a recorded problem that the harness does not reproduce must fail G2")
        failures += harness_checks(tmp)
    print("selftest:", "PASS" if not failures else "FAIL")
    for failure in failures:
        print("  " + failure)
    return not failures


def harness_checks(tmp):
    """The pure functions of the harness."""
    failures = []
    if set(h.ARM_ORDER) != set(h.ARMS) or h.SCORED_ARMS != ("A1", "A2", "A3"):
        failures.append("arm tables are inconsistent")
    if not h.ENDF_FILES or any(len(d) != 64 for _, d in h.ENDF_FILES.values()):
        failures.append("evaluation pins malformed")
    if h.MATERIAL_ABS_R != c173.MATERIAL_ABS_R or h.MATERIAL_SIGMAS != c173.MATERIAL_SIGMAS:
        failures.append("the materiality thresholds differ from P173's")
    if MATERIAL_ABS_R != 0.05 or MATERIAL_SIGMAS != 3.0 or h.G1_TOLERANCE != 1e-9:
        failures.append("thresholds are not the registered ones")
    # fake evaluations: Fe56 with a resolved first range, C12 with a scattering-radius-only first range
    endf = tmp / "endf"
    endf.mkdir()
    fe = [fake_endf_line(["2.605600+4", "5.545443+1", "0", "0", "1", "0"], 1),
          fake_endf_line(["2.605600+4", "1.000000+0", "0", "0", "1", "0"], 2),
          fake_endf_line(["1.000000-5", "8.500000+5", "1", "3", "0", "1"], 3)]
    c12 = [fake_endf_line(["6.012000+3", "1.1900000+1", "0", "0", "1", "0"], 1),
           fake_endf_line(["6.012000+3", "1.000000+0", "0", "0", "1", "0"], 2),
           fake_endf_line(["1.000000-5", "1.000000+5", "0", "0", "0", "0"], 3)]
    (endf / h.ENDF_FILES["Fe56"][0]).write_text("\n".join(["x" * 80] + fe + ["y" * 80]) + "\n")
    (endf / h.ENDF_FILES["C12"][0]).write_text("\n".join(c12) + "\n")
    if h.mf2_first_range(fe) != (1, 1, 1, 3, 1e-5, 8.5e5) or h.mf2_first_range(c12)[2] != 0:
        failures.append(f"MF2 first range wrong: {h.mf2_first_range(fe)}")
    if not h.has_resolved_range(endf / h.ENDF_FILES["Fe56"][0]) or h.has_resolved_range(endf / h.ENDF_FILES["C12"][0]):
        failures.append("resolved-range detection wrong")
    atoms = {"Fe56": 0.05, "C12": 1e-4}
    flux = [0.5] * (len(p173.groups_ascending()) - 1)
    data = {m: {"flux": {region: flux for region in p173.regions_of(m)}, "atoms": dict(atoms), "density": 7.93}
            for m in p173.MODEL_ORDER}
    files = h.geometry_files("S", data, endf)
    if [n for n, _, _ in files] != ["Fe56"]:
        failures.append(f"geometry files must hold resolved-range nuclides only: {files}")
    spec0 = h.build_spec("A0", "S", "S:10-12", data)
    if "self_shielding" in spec0 or spec0["summary"] != h.SUMMARY or spec0["schedule"] != [h.STEP] \
            or spec0["spectrum"]["descending"] or len(spec0["spectrum"]["boundaries_eV"]) != 710:
        failures.append("the control problem must carry no self_shielding and the registered step and summary")
    atoms_back = spec0["material"]["composition"]["Fe56"] * spec0["material"]["density_g_cm3"] / 1e24
    if abs(atoms_back - 0.05) > 1e-15 or spec0["material"]["basis"] != "atoms_per_g":
        failures.append("atom densities do not convert back from atoms per gram")
    spec1 = h.build_spec("A1", "S", "S:10-12", data, (tmp / "t.json", "a" * 64))
    if spec1["self_shielding"] != {"table": {"path": str(tmp / "t.json"), "sha256": "a" * 64}, "dilution": "composition"}:
        failures.append(f"A1 problem wrong: {spec1['self_shielding']}")
    lengths = {}
    for arm in ("A2", "A3"):
        for model in p173.MODEL_ORDER:
            for region in p173.regions_of(model):
                lengths[(arm, region)] = h.build_spec(arm, model, region, data, endf_dir=endf)[
                    "self_shielding"]["geometry"]["length_cm"]
    want = {("A2", "S:10-12"): 2, ("A2", "S:20-25"): 5, ("A2", "S:40-50"): 10, ("A2", "W:10-10.5"): 0.5,
            ("A2", "W:15-20"): 5, ("A2", "C:20-25"): 5, ("A2", "K:100-120"): 20,
            ("A3", "S:10-12"): 40, ("A3", "S:40-50"): 40, ("A3", "W:10.5-12"): 10, ("A3", "C:10-12"): 15,
            ("A3", "K:50-60"): 70}
    for key, value in want.items():
        if abs(lengths[key] - value) > 1e-12:
            failures.append(f"foil thickness {key}: {lengths[key]}, expected {value}")
    geo = h.build_spec("A2", "S", "S:10-12", data, endf_dir=endf)["self_shielding"]["geometry"]
    if geo["shape"] != "foil" or "reactions" in geo or "density_g_cm3" in geo or len(geo["resonance_files"]) != 1:
        failures.append(f"geometry problem wrong: {geo}")
    # the digest ignores where files live and sees everything else
    moved = copy.deepcopy(spec1)
    moved["library"]["path"] = "/elsewhere/" + Path(moved["library"]["path"]).name
    moved["self_shielding"]["table"]["path"] = "/elsewhere/t.json"
    if h.spec_digest(moved) != h.spec_digest(spec1):
        failures.append("the problem digest must not depend on directories")
    changed = copy.deepcopy(spec1)
    changed["spectrum"]["flux_per_group"][3] += 1.0
    if h.spec_digest(changed) == h.spec_digest(spec1):
        failures.append("the problem digest must see the flux")
    # reading a result
    step = {"summary": {"depletion": {"nuclides": [
        {"nuclide": "Mn55", "channels": [{"channel": "decay", "kind": "decay", "mt": None, "rate_per_s": 1.0},
                                         {"channel": "MT102", "kind": "reaction", "mt": 102, "rate_per_s": 3.5e-24}]},
        {"nuclide": "Fe56", "channels": [{"channel": "MT16", "kind": "reaction", "mt": 16, "rate_per_s": 2e-30}]}]}}}
    result = {"steps": [{"summary": {}}, step],
              "ledger": {"shielding": {"table_sha256": "a", "applied_factors": {"Mn55": {"300": [1, 1, 1, 0.8],
                                                                                          "301": [1, 1, 1, 0.9]}}}}}
    rates = h.depletion_rates(result)
    if rates != {("Mn55", 102): 3.5e-24, ("Fe56", 16): 2e-30}:
        failures.append(f"depletion rates wrong: {rates}")
    mapped = h.reaction_rates_of(result, "S")
    if mapped["Mn55|102"] != 3.5e-24 or mapped["Fe56|103"] is not None:
        failures.append(f"a reaction without a loss channel must read as None: {mapped}")
    try:
        h.depletion_rates({"steps": [step, step]})
        failures.append("two depletion steps must be refused")
    except ValueError:
        pass
    summary_ = h.shielding_summary(result)
    if summary_["applied_factors_summary"]["Mn55"] != {"groups": 2, "min_factor": 0.8} or "applied_factors" in summary_:
        failures.append(f"shielding summary wrong: {summary_}")
    no_fission = h.shielding_summary({"ledger": {"shielding": {"applied_factors": {"Fe56": {"5": [0.9, 0.95, 0, 0.7]}}}}})
    if no_fission["applied_factors_summary"]["Fe56"] != {"groups": 1, "min_factor": 0.7}:
        failures.append(f"a zero fission factor must not set the minimum: {no_fission}")
    if h.shielding_summary({"ledger": {}}) is not None:
        failures.append("a run without shielding must give None")
    # row construction
    pr = {"model": "S", "region": "S:10-12", "target": "Mn55", "mt": 102, "reaction": "Mn55(n,gamma)Mn56",
          "status": "scored", "R_actinv": 2.0, "R_openmc": 1.8, "r": math.log(2.0 / 1.8), "se_r": 0.01,
          "r_coll": math.log(2.0 / 1.8), "se_r_coll": 0.01, "r_proc": 0.0, "converged": True,
          "total_row_R_actinv": 2.0}
    row = h.build_ss_row("A1", pr, 2.0e-24, 1.8e-24, True)
    if row["status"] != "scored" or abs(row["q"] - 0.9) > 1e-12 or abs(row["R_ss"] - 1.8) > 1e-12 \
            or abs(row["r_ss"]) > 1e-12:
        failures.append(f"build_ss_row wrong: {row}")
    for args, code in (((2e-24, None, True), "no_loss_row"), ((None, 1e-24, True), "no_loss_row"),
                       ((2e-24, 1e-24, False), "arm_failed"), ((0.0, 1e-24, True), "rate_zero")):
        if h.build_ss_row("A1", pr, *args)["reason_code"] != code:
            failures.append(f"build_ss_row reason for {args}")
    if h.build_ss_row("A1", dict(pr, status="not_scored", reason_code="target_absent"), 1e-24, 1e-24, True)[
            "reason_code"] != "p173_not_scored":
        failures.append("a P173 row that was not scored must carry p173_not_scored")
    return failures


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--record", type=Path, default=RECORD)
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--self-test", "--selftest", dest="selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        sys.exit(0 if selftest() else 1)
    record = json.loads(args.record.read_text())
    gates, result, sentences = evaluate(record, load_p173_rows(), spec_check=real_spec_check(record))
    passed = all(not problems for problems in gates.values())
    verdict = {
        "protocol": "ACTINV-P176",
        "verdict": "P176-PASS (pending G4, CI on the evidence commit)" if passed else "P176-FAIL",
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
    else:
        VERDICT.write_text(json.dumps(verdict, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
    for sentence in sentences:
        print("  " + sentence)
    print(verdict["verdict"] if passed else "P176-FAIL")
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
