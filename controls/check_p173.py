#!/usr/bin/env python3
"""P173 checker (protocols/ACTINV-P173_PROTOCOL.md): ACTINV's 709-group reaction rates against OpenMC continuous-energy
tallies. Reads results/p173_ce_rates.json written by `controls/p173_ce_rates.py assemble`. G0-G3 are evaluated here; G4
(CI on the pushed evidence commit) is external.

  G0  The protocol's SHA-256 is in protocols/protocol_hash.txt; the record's harness, group-structure and constants
      hashes match the files (and the harness' own constants).
  G1  Pinned inputs, OpenMC 0.15.3, the pinned `actinv` binary, a clean tracked tree, the bounded cgroup of every mode
      and every OpenMC run, and the harness collapse agreeing with `actinv inspect xs` to 1e-9 relative on one region of
      every model.
  G2  Fidelity: every listed (model, region, reaction) is scored or reported as not scored with a reason; each scored
      row's r, r_coll and their standard errors recompute from its rates, r_proc recomputes, and r = r_proc + r_coll;
      the batch schedule is 4, 8, ..., 256 batches of 1e5 and stops only when converged or at 256.
  G3  The decision rules (correction on r_coll, processing review on r_proc, agreement sentence on r) are recomputed
      from the record and written to results/p173_verdict.json (with --no-write, compared with it).

Verdict: P173-PASS if G0-G3 hold (G4 external), whatever the agreement.

Usage: check_p173.py [--record FILE] [--no-write]
       check_p173.py --self-test
"""
import argparse
import copy
import hashlib
import json
import os
import math
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "controls"))
import p173_ce_rates as p173  # noqa: E402

PROTOCOL = ROOT / "protocols" / "ACTINV-P173_PROTOCOL.md"
HASH_LOG = ROOT / "protocols" / "protocol_hash.txt"
# P173_REHEARSAL=1 reads and writes the rehearsal's paths under target/, as the harness does.
REHEARSAL = os.environ.get("P173_REHEARSAL") == "1"
RECORD = ROOT / "target/p173-rehearsal/record.json" if REHEARSAL else ROOT / "results" / "p173_ce_rates.json"
VERDICT = ROOT / "target/p173-rehearsal/verdict.json" if REHEARSAL else ROOT / "results" / "p173_verdict.json"
HARNESS = ROOT / "controls" / "p173_ce_rates.py"

MATERIAL_ABS_R = 0.05
MATERIAL_SIGMAS = 3.0
MIN_MATERIAL_REACTIONS = 2
NOT_SCORED_CODES = {"target_absent", "reaction_absent_openmc", "reaction_absent_actinv", "openmc_zero", "actinv_zero", "omg_zero"}
CONSISTENCY = 1e-9


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def good(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def close(a, b, tol=CONSISTENCY):
    return good(a) and good(b) and abs(a - b) <= tol * max(1.0, abs(a), abs(b))


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
    if env.get("constants_sha256") != p173.constants_digest():
        problems.append("the record's constants hash is not the harness' constants (models, materials, reactions)")
    return problems


def g1(record, *, bin_pin=p173.ACTINV_BIN_SHA256):
    problems = []
    if record.get("schema") != p173.SCHEMA:
        problems.append("wrong schema")
    for name, (_, digest) in p173.PINNED.items():
        if record.get("inputs", {}).get(name, {}).get("sha256") != digest:
            problems.append(f"input {name} hash is not the pin")
    env = record.get("environment", {})
    if env.get("openmc_version") != p173.OPENMC_VERSION:
        problems.append(f"OpenMC is not {p173.OPENMC_VERSION}")
    if not env.get("clean_tree"):
        problems.append("tracked tree was not clean")
    if env.get("groups_match_library") is not True:
        problems.append("the library's group boundaries were not confirmed equal to fispact-709")
    if bin_pin is None:
        problems.append("the actinv binary digest is not pinned yet")
    elif env.get("actinv_bin_sha256") != bin_pin:
        problems.append("the G1 collapse check did not run with the pinned actinv binary")
    seen = {entry.get("where"): entry.get("cgroup") for entry in env.get("cgroups", [])}
    required = ["g1_check"] + [f"{kind}:{m}" for m in p173.MODEL_ORDER for kind in ("openmc", "actinv")]
    for where in required:
        if where not in seen:
            problems.append(f"no cgroup record for {where}")
    for m in p173.MODEL_ORDER:
        if not any(w.startswith(f"openmc_child:{m}:") for w in seen):
            problems.append(f"no cgroup record of an OpenMC run of model {m}")
    for where, cg in sorted(seen.items(), key=lambda kv: str(kv[0])):
        if not p173.cgroup_ok(cg or {}):
            problems.append(f"cgroup of {where} is not the bounded scope")
    check = record.get("g1_check", {})
    if check.get("tolerance") != p173.G1_TOLERANCE:
        problems.append("the collapse check's tolerance is not the registered one")
    by_model = {c.get("model"): c for c in check.get("checks", [])}
    for m in p173.MODEL_ORDER:
        c = by_model.get(m)
        if c is None:
            problems.append(f"no collapse check for model {m}")
            continue
        if c.get("region") != p173.regions_of(m)[0]:
            problems.append(f"collapse check of model {m} is not on its first region")
        reactions = c.get("reactions", [])
        if not reactions:
            problems.append(f"collapse check of model {m} compared no reaction")
        for reaction in reactions:
            ours, theirs = reaction.get("harness_rate_per_atom"), reaction.get("actinv_rate_per_atom")
            if not (good(ours) and good(theirs)):
                problems.append(f"collapse check {m} {reaction.get('target')}: no comparable rate")
            elif not (ours == theirs == 0.0 or abs(ours - theirs) / max(abs(ours), abs(theirs)) <= p173.G1_TOLERANCE):
                problems.append(f"collapse check {m} {reaction.get('target')} MT {reaction.get('mt')}: "
                                f"harness and actinv differ by more than {p173.G1_TOLERANCE:g}")
        if not (good(c.get("max_rel_diff")) and c["max_rel_diff"] <= p173.G1_TOLERANCE):
            problems.append(f"collapse check of model {m}: recorded maximum difference is not within tolerance")
    return problems


def g2(record):
    problems = []
    models = record.get("models", {})
    for m in p173.MODEL_ORDER:
        info = models.get(m)
        if info is None:
            problems.append(f"no model record for {m}")
            continue
        its = info.get("iterations", [])
        if not its or [it.get("batches") for it in its] != [p173.BATCH_START * 2 ** i for i in range(len(its))]:
            problems.append(f"{m}: batch schedule is not 4, 8, 16, ...")
            continue
        if any(it.get("returncode") != 0 for it in its):
            problems.append(f"{m}: an OpenMC run failed")
        # The harness's constants (1e5 and 256 batches; smaller under P173_REHEARSAL=1).
        per_batch = p173.PARTICLES_PER_BATCH
        if info.get("particles_per_batch") != per_batch or any(it.get("particles") != it["batches"] * per_batch
                                                                for it in its):
            problems.append(f"{m}: particles per batch is not 1e5")
        last = its[-1]
        if info.get("final_batches") != last["batches"]:
            problems.append(f"{m}: final result is not the last iteration")
        if last["batches"] > p173.BATCH_MAX or (last["batches"] < p173.BATCH_MAX
                                                 and last.get("all_converged") is not True):
            problems.append(f"{m}: stopped before 256 batches without every rate converged")
        if any(it.get("all_converged") is True for it in its[:-1]):
            problems.append(f"{m}: continued after every rate converged")
    expected = list(p173.expected_rows())
    rows = record.get("rows", [])
    keys = [(r.get("model"), r.get("region"), r.get("target"), r.get("mt")) for r in rows]
    if len(set(keys)) != len(keys):
        problems.append("duplicate rows")
    if set(keys) != set(expected):
        missing, extra = set(expected) - set(keys), set(keys) - set(expected)
        problems.append(f"rows differ from the registered list: {len(missing)} missing, {len(extra)} unexpected")
    for r in rows:
        name = f"{r.get('model')}/{r.get('region')}/{r.get('target')} MT{r.get('mt')}"
        if r.get("status") == "not_scored":
            if r.get("reason_code") not in NOT_SCORED_CODES or not r.get("reason"):
                problems.append(f"{name}: not scored without a registered reason")
        elif r.get("status") == "scored":
            vals = [r.get(k) for k in ("R_actinv", "R_openmc", "R_omg", "R_actinv_se_bound", "R_openmc_se",
                                       "R_omg_se_bound", "r", "se_r", "r_proc", "se_r_proc", "r_coll", "se_r_coll")]
            if not all(good(v) for v in vals) or not (r["R_actinv"] > 0 and r["R_openmc"] > 0 and r["R_omg"] > 0):
                problems.append(f"{name}: scored row without finite positive rates")
                continue
            r_re, se_re = p173.log_ratio(r["R_actinv"], r["R_actinv_se_bound"], r["R_openmc"], r["R_openmc_se"])
            if not (close(r["r"], r_re) and close(r["se_r"], se_re)):
                problems.append(f"{name}: r or its standard error does not recompute from the rates")
            c_re, sc_re = p173.log_ratio(r["R_omg"], r["R_omg_se_bound"], r["R_openmc"], r["R_openmc_se"])
            if not (close(r["r_coll"], c_re) and close(r["se_r_coll"], sc_re)):
                problems.append(f"{name}: r_coll or its standard error does not recompute from the rates")
            if not close(r["r_proc"], math.log(r["R_actinv"] / r["R_omg"])) or r["se_r_proc"] < 0:
                problems.append(f"{name}: r_proc does not recompute from the rates")
            if not close(r["r"], r["r_proc"] + r["r_coll"]):
                problems.append(f"{name}: r is not r_proc + r_coll")
            if r.get("converged") is not (r["R_openmc_se"] / r["R_openmc"] <= p173.REL_SE_TARGET):
                problems.append(f"{name}: converged flag does not follow the relative standard error")
        else:
            problems.append(f"{name}: status {r.get('status')!r}")
    return problems[:200]


def is_used(row):
    return row.get("status") == "scored" and row.get("converged") is True


def is_material(row, which="r"):
    """For a used row, |x| > 0.05 and |x| > 3 se(x), x being r (total), r_proc or r_coll. A converged OpenMC rate where
    ACTINV has none (`actinv_zero`) is material for r and r_proc, not r_coll."""
    if is_used(row):
        return abs(row[which]) > MATERIAL_ABS_R and abs(row[which]) > MATERIAL_SIGMAS * row["se_" + which]
    return (which != "r_coll" and row.get("status") == "not_scored" and row.get("reason_code") == "actinv_zero"
            and good(row.get("rel_se_openmc")) and row["rel_se_openmc"] <= p173.REL_SE_TARGET)


def row_id(row):
    return (row["model"], row["region"], row["target"], row["mt"])


def brief(row, which):
    return {"region": row["region"], "reaction": row["reaction"], which: row.get(which),
            "se_" + which: row.get("se_" + which), "reason_code": row.get("reason_code")}


def summary(record):
    rows = record["rows"]
    used = [r for r in rows if is_used(r)]
    material = {which: [r for r in rows if is_material(r, which)] for which in ("r", "r_proc", "r_coll")}
    by_region = {}
    for r in material["r_coll"]:
        by_region.setdefault(r["region"], set()).add((r["target"], r["mt"]))
    triggering = sorted(region for region, reactions in by_region.items() if len(reactions) >= MIN_MATERIAL_REACTIONS)
    not_scored = {}
    for r in rows:
        if r["status"] == "not_scored":
            not_scored[r["reason_code"]] = not_scored.get(r["reason_code"], 0) + 1
    percent = max((math.expm1(abs(r["r"])) * 100.0 for r in used), default=None)

    def max_abs(which, model):
        return max((abs(r[which]) for r in used if r["model"] == model), default=None)
    result = {
        "rows": len(rows), "rows_used": len(used),
        "rows_scored_not_converged": sum(r["status"] == "scored" and not r["converged"] for r in rows),
        "rows_not_scored": sum(r["status"] == "not_scored" for r in rows), "not_scored_by_reason": not_scored,
        "material_total_rows": [brief(r, "r") for r in material["r"]],
        "material_processing_rows": [brief(r, "r_proc") for r in material["r_proc"]],
        "material_collapse_rows": [brief(r, "r_coll") for r in material["r_coll"]],
        "regions_with_two_or_more_material_collapse_reactions": triggering,
        "correction_warranted": bool(triggering),
        "processing_review_warranted": bool(material["r_proc"]),
        "max_abs_r_used": max((abs(r["r"]) for r in used), default=None),
        "max_percent_used": percent,
        "by_model": {m: {"rows_used": sum(r["model"] == m for r in used),
                         "max_abs_r": max_abs("r", m), "max_abs_r_proc": max_abs("r_proc", m),
                         "max_abs_r_coll": max_abs("r_coll", m),
                         "mean_energy_eV": record["models"][m]["mean_energy_eV"]}
                     for m in p173.MODEL_ORDER},
    }
    sentences = []
    if not material["r"] and used:
        x = math.ceil(percent * 10.0 - 1e-9) / 10.0
        sentences.append("On the same ENDF/B-VIII.1 data, ACTINV's 709-group reaction rates agree with OpenMC's "
                         f"continuous-energy rates within {x:.1f}% for the listed reactions.")
        uncovered = len(rows) - len(used)
        if uncovered:
            sentences.append(f"{uncovered} of {len(rows)} listed rows were not scored or did not reach a "
                             f"{100 * p173.REL_SE_TARGET:.1f}% standard error and are not covered by that sentence.")
    return result, sentences


def evaluate(record, *, g0_args=None, bin_pin=p173.ACTINV_BIN_SHA256):
    gates = {"G0": g0(record, **(g0_args or {})), "G1": g1(record, bin_pin=bin_pin), "G2": g2(record)}
    if gates["G2"]:
        result, sentences = {}, []
        gates["G3"] = ["summary not computed (fidelity failed)"]
    else:
        result, sentences = summary(record)
        gates["G3"] = []
    return gates, result, sentences


# ------------------------------------------------------------------------------------------------------ self-test

def synthetic_record(harness_sha, groups_sha):
    cg = {"memory.max": str(6 * p173.GIB), "memory.swap.max": "0", "pids.max": "128", "cpu.max": "200000 100000"}
    cgroups = [{"where": "g1_check", "cgroup": dict(cg)}]
    for m in p173.MODEL_ORDER:
        cgroups += [{"where": f"openmc:{m}", "cgroup": dict(cg)}, {"where": f"openmc_child:{m}:4", "cgroup": dict(cg)},
                    {"where": f"actinv:{m}", "cgroup": dict(cg)}]
    record = {"schema": p173.SCHEMA,
              "inputs": {k: {"sha256": v} for k, (_, v) in p173.PINNED.items()},
              "environment": {"harness_sha256": harness_sha, "groups_sha256": groups_sha,
                              "constants_sha256": p173.constants_digest(), "openmc_version": p173.OPENMC_VERSION,
                              "clean_tree": True, "groups_match_library": True, "actinv_bin_sha256": "b" * 64,
                              "cgroups": cgroups},
              "g1_check": {"tolerance": p173.G1_TOLERANCE, "checks": []}, "models": {}, "rows": []}
    for m in p173.MODEL_ORDER:
        spec = p173.model_spec(m)
        record["models"][m] = {
            "final_batches": 4, "particles_per_batch": 100_000,
            "iterations": [{"batches": 4, "particles": 400_000, "returncode": 0, "all_converged": True}],
            "mean_energy_eV": {region: 1.0e6 for region in p173.regions_of(m)}}
        record["g1_check"]["checks"].append({
            "model": m, "region": p173.regions_of(m)[0], "max_rel_diff": 1e-12,
            "reactions": [{"target": spec["reactions"][0][0], "mt": spec["reactions"][0][1],
                           "harness_rate_per_atom": 2.0e-24, "actinv_rate_per_atom": 2.0e-24 * (1 + 1e-12)}]})
    for model, region, target, mt in p173.expected_rows():
        product = next(p for t, k, p in p173.model_spec(model)["reactions"] if (t, k) == (target, mt))
        row = {"model": model, "region": region, "target": target, "mt": mt,
               "reaction": p173.reaction_label(target, mt, product), "status": "scored", "reason_code": None,
               "reason": None, "R_actinv": 1.01, "R_actinv_se_bound": 0.001, "R_omg": 1.005, "R_omg_se_bound": 0.001,
               "R_openmc": 1.0, "R_openmc_se": 0.002, "rel_se_openmc": 0.002, "converged": True, "se_r_proc": 0.001,
               "mean_energy_eV": 1.0e6}
        refresh(row)
        record["rows"].append(row)
    return record


def refresh(row):
    """Recompute r, r_proc, r_coll and the standard errors of a synthetic row from its rates."""
    row["r"], row["se_r"] = p173.log_ratio(row["R_actinv"], row["R_actinv_se_bound"], row["R_openmc"], row["R_openmc_se"])
    row["r_coll"], row["se_r_coll"] = p173.log_ratio(row["R_omg"], row["R_omg_se_bound"], row["R_openmc"],
                                                     row["R_openmc_se"])
    row["r_proc"] = math.log(row["R_actinv"] / row["R_omg"])


def set_rates(record, model, region, reactions, actinv=None, omg=None):
    """Set the listed (target, MT) rows of a region to R_actinv / R_openmc = `actinv` and R_omg / R_openmc = `omg`."""
    for row in record["rows"]:
        if row["model"] == model and row["region"] == region and (row["target"], row["mt"]) in reactions:
            if actinv is not None:
                row["R_actinv"] = actinv * row["R_openmc"]
            if omg is not None:
                row["R_omg"] = omg * row["R_openmc"]
            refresh(row)


def selftest():
    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        protocol, harness, groups = tmp / "ACTINV-P173_PROTOCOL.md", tmp / "harness.py", tmp / "groups.json"
        protocol.write_text("protocol text")
        harness.write_text("harness text")
        groups.write_text("groups text")
        log_text = f"{sha256(protocol)}  protocols/{protocol.name}\n"
        g0_args = {"protocol": protocol, "log_text": log_text, "harness": harness, "groups": groups}
        pin = "b" * 64

        def fresh():
            return synthetic_record(sha256(harness), sha256(groups))

        def run(label, mutate, gate, args=None, pin_=pin):
            trial = fresh()
            mutate(trial)
            gates, result, sentences = evaluate(trial, g0_args=dict(g0_args, **(args or {})), bin_pin=pin_)
            bad = [name for name, problems in gates.items() if problems and name != "G3"]
            if gate is None and bad:
                failures.append(f"{label}: expected pass, failed {bad}: {[gates[b][:3] for b in bad]}")
            if gate is not None and gate not in bad:
                failures.append(f"{label}: expected {gate} to fail")
            return result, sentences

        total = len(list(p173.expected_rows()))
        if total != 78:
            failures.append(f"row count {total}, expected 78")
        result, sentences = run("clean", lambda r: None, None)
        if (result["rows"] != total or result["rows_used"] != total or result["correction_warranted"]
                or result["processing_review_warranted"] or result["material_total_rows"] or len(sentences) != 1
                or "within 1.0% for the listed reactions" not in sentences[0]):
            failures.append(f"clean summary wrong: {result.get('rows')} {result.get('correction_warranted')} {sentences}")
        # two reactions of one region with material collapse error (processing agrees): correction warranted, no
        # processing review, no sentence
        both = {("W186", 102), ("W184", 102)}

        def collapse_only(r, reactions=both, region="W:10-10.5"):
            set_rates(r, "W", region, reactions, actinv=0.7, omg=0.7)
        result, sentences = run("two material collapse", collapse_only, None)
        if (not result["correction_warranted"] or result["regions_with_two_or_more_material_collapse_reactions"]
                != ["W:10-10.5"] or result["processing_review_warranted"] or sentences):
            failures.append(f"collapse rule wrong: {result}")
        result, _ = run("one material collapse", lambda r: collapse_only(r, {("W186", 102)}), None)
        if result["correction_warranted"] or len(result["material_collapse_rows"]) != 1:
            failures.append("one material collapse reaction must not warrant a correction")
        result, _ = run("two regions", lambda r: (collapse_only(r, {("W186", 102)}),
                                                  collapse_only(r, {("W186", 102)}, "W:10.5-12")), None)
        if result["correction_warranted"]:
            failures.append("one reaction in two regions must not warrant a correction")
        # a processing difference only (R_omg equals OpenMC): processing review, no correction, no sentence
        result, sentences = run("processing only", lambda r: set_rates(r, "S", "S:10-12", {("Fe56", 103), ("Fe54", 103)},
                                                                        actinv=1.3, omg=1.0), None)
        if (result["correction_warranted"] or not result["processing_review_warranted"]
                or len(result["material_processing_rows"]) != 2 or result["material_collapse_rows"] or sentences):
            failures.append(f"processing rule wrong: {result}")
        # both: the correction and the review are independent
        def both_kinds(r):
            set_rates(r, "C", "C:10-12", {("Cu63", 102), ("Cr50", 102)}, actinv=1.5, omg=0.8)
        result, _ = run("both kinds", both_kinds, None)
        if not (result["correction_warranted"] and result["processing_review_warranted"]):
            failures.append("a row can be material in both r_proc and r_coll")
        # a material difference below 5% is not material
        result, sentences = run("below 5 percent", lambda r: set_rates(r, "S", "S:10-12", {("Fe56", 103)}, actinv=1.04,
                                                                        omg=1.04), None)
        if result["material_total_rows"] or result["material_collapse_rows"]:
            failures.append("a 4% difference must not be material")

        def noisy(r):
            set_rates(r, "S", "S:10-12", {("Fe56", 103)}, actinv=1.2, omg=1.2)
            for row in r["rows"]:
                if (row["region"], row["target"], row["mt"]) == ("S:10-12", "Fe56", 103):
                    row.update(R_openmc_se=0.3, rel_se_openmc=0.3, converged=False)
                    refresh(row)
        result, sentences = run("not converged is excluded", noisy, None)
        if result["rows_used"] != total - 1 or result["material_total_rows"] or len(sentences) != 2:
            failures.append(f"a row that never converged must be reported and excluded: {result.get('rows_used')} {sentences}")

        def actinv_zero(r):
            for row in r["rows"]:
                if row["region"] == "S:10-12" and row["target"] in ("Fe56", "Fe54") and row["mt"] == 103:
                    row.update(status="not_scored", reason_code="actinv_zero", reason="ACTINV's rate is zero")
                    for k in ("r", "se_r", "converged", "r_proc", "se_r_proc", "r_coll", "se_r_coll"):
                        row.pop(k)
        result, sentences = run("actinv zero is material", actinv_zero, None)
        if (not result["processing_review_warranted"] or result["correction_warranted"] or sentences
                or len(result["material_total_rows"]) != 2):
            failures.append("a zero ACTINV rate against a converged OpenMC rate must count for r and r_proc only")

        def target_absent(r):
            for row in r["rows"]:
                if row["model"] == "K" and row["target"] == "Mn55":
                    row.update(status="not_scored", reason_code="target_absent", reason="Mn55 is not in the material")
                    for k in ("r", "se_r", "converged", "R_actinv", "R_openmc", "r_proc", "se_r_proc", "r_coll",
                              "se_r_coll", "R_omg"):
                        row.pop(k)
        result, sentences = run("not scored with a reason", target_absent, None)
        if result["rows_not_scored"] != 2 or len(sentences) != 2:
            failures.append(f"not-scored rows must be counted and flagged in the sentence: {result.get('rows_not_scored')}")
        # failures
        run("protocol hash", lambda r: None, "G0", args={"log_text": ""})
        run("harness hash", lambda r: r["environment"].update(harness_sha256="0" * 64), "G0")
        run("groups hash", lambda r: r["environment"].update(groups_sha256="0" * 64), "G0")
        run("constants hash", lambda r: r["environment"].update(constants_sha256="0" * 64), "G0")
        run("schema", lambda r: r.update(schema="x"), "G1")
        run("pin", lambda r: r["inputs"]["endf8_library"].update(sha256="0" * 64), "G1")
        run("version", lambda r: r["environment"].update(openmc_version="0.16.0"), "G1")
        run("dirty", lambda r: r["environment"].update(clean_tree=False), "G1")
        run("groups", lambda r: r["environment"].update(groups_match_library=False), "G1")
        run("binary", lambda r: r["environment"].update(actinv_bin_sha256="0" * 64), "G1")
        run("unpinned binary", lambda r: None, "G1", pin_=None)
        run("cgroup", lambda r: r["environment"]["cgroups"][1]["cgroup"].update({"memory.swap.max": "max"}), "G1")
        run("child cgroup", lambda r: r["environment"]["cgroups"][2]["cgroup"].update(
            {"memory.max": str(9 * p173.GIB)}), "G1")
        run("child cgroup missing", lambda r: r["environment"].update(
            cgroups=[c for c in r["environment"]["cgroups"] if not c["where"].startswith("openmc_child:W:")]), "G1")
        run("g1 difference", lambda r: r["g1_check"]["checks"][2]["reactions"][0].update(
            actinv_rate_per_atom=2.0e-24 * (1 + 1e-6)), "G1")
        run("g1 recorded max", lambda r: r["g1_check"]["checks"][3].update(max_rel_diff=1e-3), "G1")
        run("g1 model missing", lambda r: r["g1_check"]["checks"].pop(), "G1")
        run("g1 no reactions", lambda r: r["g1_check"]["checks"][0].update(reactions=[]), "G1")
        run("g1 tolerance", lambda r: r["g1_check"].update(tolerance=1e-6), "G1")
        run("row missing", lambda r: r["rows"].pop(5), "G2")
        run("row duplicated", lambda r: r["rows"].append(dict(r["rows"][0])), "G2")
        run("thin-sample row is not registered", lambda r: r["rows"].append(dict(r["rows"][0], model="T-S")), "G2")
        run("not scored without reason", lambda r: r["rows"][4].update(status="not_scored", reason_code=None, reason=""),
            "G2")
        run("r does not recompute", lambda r: r["rows"][7].update(r=0.5), "G2")
        run("identity r = r_proc + r_coll", lambda r: r["rows"][6].update(r_proc=r["rows"][6]["r_proc"] + 0.01), "G2")
        run("r_coll does not recompute", lambda r: r["rows"][8].update(r_coll=r["rows"][8]["r_coll"] + 0.01,
                                                                       r=r["rows"][8]["r"] + 0.01), "G2")
        run("r_coll se does not recompute", lambda r: r["rows"][8].update(se_r_coll=0.5), "G2")
        run("missing R_omg", lambda r: r["rows"][2].update(R_omg=None), "G2")
        run("converged flag", lambda r: r["rows"][9].update(R_openmc_se=0.5), "G2")
        run("nan rate", lambda r: r["rows"][3].update(R_actinv=float("nan")), "G2")
        run("model missing", lambda r: r["models"].pop("K"), "G2")
        run("schedule", lambda r: r["models"]["S"]["iterations"][0].update(batches=8), "G2")
        run("early stop", lambda r: r["models"]["W"]["iterations"][0].update(all_converged=False), "G2")
        run("particles", lambda r: r["models"]["C"].update(particles_per_batch=5000), "G2")
        run("run failed", lambda r: r["models"]["S"]["iterations"][0].update(returncode=1), "G2")
        run("continued after converged", lambda r: r["models"]["K"].update(
            iterations=[{"batches": 4, "particles": 400000, "returncode": 0, "all_converged": True},
                        {"batches": 8, "particles": 800000, "returncode": 0, "all_converged": True}],
            final_batches=8), "G2")
        # harness constants: the tables are consistent
        if p173.MODEL_ORDER != ("S", "W", "C", "K"):
            failures.append("the models are S, W, C, K only")
        for base in p173.BASE_MODELS:
            for target, mt, product in p173.REACTIONS[base]:
                if p173.product_za(target, mt) != p173.za(product):
                    failures.append(f"reaction table: {target} MT {mt} does not give {product}")
        for material in p173.MATERIALS:
            if abs(math.fsum(p173.final_wo(material).values()) - 1.0) > 1e-12:
                failures.append(f"{material} mass fractions do not sum to 1")
        if p173.final_wo("concrete")["Co"] != 1e-6 or p173.final_wo("concrete")["Eu"] != 1e-6:
            failures.append("concrete dopants are not 1 ppm by mass")
        for model in p173.MODEL_ORDER:
            lay = p173.layout(p173.model_spec(model)["shells"])
            if any(b != lay[i + 1][0] for i, (_, b, _) in enumerate(lay[:-1])) or any(a >= b for a, b, _ in lay):
                failures.append(f"{model}: shells are not contiguous")
            if len(p173.regions_of(model)) != len(p173.model_spec(model)["shells"]):
                failures.append(f"{model}: a listed shell is missing from the layout")
        if [p173.model_spec(m)["seed"] for m in p173.MODEL_ORDER] != [173000, 173001, 173002, 173003]:
            failures.append("model seeds are not 173000 + 0..3")
        # the pure harness functions
        sig = [[0.0, 2.0, 4.0], [1.0, 0.0, 0.0], [3.0, 3.0, 3.0], [1.0, 0.0, 1.0], [5.0, 5.0, 5.0]]
        rows = [[0, 103, 25056, 0, -1], [0, 103, -1, -1, 0], [1, 16, 74185, 0, 10], [1, 16, 74185, 1, 10],
                [2, 102, -1, -1, 0]]
        targets = [{"za": 26056, "liso": 0}, {"za": 74186, "liso": 0}, {"za": 27059, "liso": 1}]
        selection = p173.library_selection(rows)
        flux, sd = [1.0, 2.0, 3.0], [0.1, 0.1, 0.1]
        a = p173.actinv_reaction(rows, sig, targets, selection, "Fe56", 103, flux, sd)
        if a["status"] != "ok" or a["rows"] != [0] or abs(a["R_actinv"] - 16.0) > 1e-12 or a["used_total_row"]:
            failures.append(f"product-row collapse wrong: {a}")
        if abs(a["total_row_R_actinv"] - 1.0) > 1e-12 or a["product_zaps"] != [25056] or a["R_omg"] is not None:
            failures.append(f"total-row cross-check wrong: {a}")
        # R_omg and the processing standard error: identical group cross sections give r_proc = 0 with no error;
        # a scaled copy gives ln 2 with no error (the flux enters both identically)
        a = p173.actinv_reaction(rows, sig, targets, selection, "Fe56", 103, flux, sd, sigma_omc=sig[0])
        if abs(a["R_omg"] - 16.0) > 1e-12 or abs(a["se_r_proc"]) > 1e-12 or abs(a["R_omg_se_bound"] - 0.6) > 1e-12:
            failures.append(f"R_omg for identical cross sections wrong: {a}")
        a = p173.actinv_reaction(rows, sig, targets, selection, "Fe56", 103, flux, sd, sigma_omc=[0.0, 1.0, 2.0])
        if abs(a["R_omg"] - 8.0) > 1e-12 or abs(a["se_r_proc"]) > 1e-12:
            failures.append(f"a proportional OpenMC cross section must give no processing error: {a}")
        a = p173.actinv_reaction(rows, sig, targets, selection, "Fe56", 103, flux, sd, sigma_omc=[0.0, 2.0, 0.0])
        # a = (0,2,4)/16 weights flux (1,2,3); o = (0,2,0)/4: |a/Ra - o/Ro| = (0, 0.125-0.5, 0.25-0)
        if abs(a["se_r_proc"] - 0.1 * (0.375 + 0.25)) > 1e-12:
            failures.append(f"processing standard error wrong: {a['se_r_proc']}")
        a = p173.actinv_reaction(rows, sig, targets, selection, "W186", 16, flux, sd)
        if a["status"] != "ok" or a["rows"] != [2, 3] or abs(a["R_actinv"] - 22.0) > 1e-12 \
                or a["product_zaps"] != [74185]:
            failures.append(f"summing product states wrong: {a}")
        a = p173.actinv_reaction(rows, sig, targets, selection, "Co59", 102, flux, sd)
        if a["status"] != "not_scored" or a["reason_code"] != "reaction_absent_actinv":
            failures.append("a metastable-only target must not be taken as the ground state")
        a = p173.actinv_reaction(rows, sig, targets, selection, "Fe56", 107, flux, sd)
        if a["status"] != "not_scored":
            failures.append("a missing MT must be reported as not scored")
        row = p173.build_row("S", "S:10-12", "Mn55", 16, "Mn54", 1e6, {"status": "not_tallied",
                             "reason_code": "target_absent", "reason": "absent"}, {"mean": 0.0, "std_dev": 0.0}, {})
        if row["status"] != "not_scored" or row["reason_code"] != "target_absent":
            failures.append("an OpenMC plan that did not tally must give a not-scored row")
        ok = {"status": "ok", "R_actinv": 2.0, "R_actinv_se_bound": 0.0, "product_zaps": [25054],
              "total_row_R_actinv": None, "R_omg": 1.5, "R_omg_se_bound": 0.0, "se_r_proc": 0.0}
        row = p173.build_row("S", "S:10-12", "Mn55", 16, "Mn54", 1e6, {"status": "tallied"},
                             {"mean": 1.0, "std_dev": 0.001}, ok)
        if (row["status"] != "scored" or abs(row["r"] - math.log(2.0)) > 1e-12 or not row["converged"]
                or abs(row["r_proc"] - math.log(2.0 / 1.5)) > 1e-12 or abs(row["r_coll"] - math.log(1.5)) > 1e-12
                or abs(row["r"] - row["r_proc"] - row["r_coll"]) > 1e-12):
            failures.append(f"build_row wrong: {row}")
        row = p173.build_row("S", "S:10-12", "Mn55", 16, "Mn54", 1e6, {"status": "tallied"},
                             {"mean": 0.0, "std_dev": 0.0}, ok)
        if row["reason_code"] != "openmc_zero":
            failures.append("a zero OpenMC rate must be reported as such")
        row = p173.build_row("S", "S:10-12", "Mn55", 16, "Mn54", 1e6, {"status": "tallied"},
                             {"mean": 1.0, "std_dev": 0.001}, dict(ok, R_omg=0.0))
        if row["reason_code"] != "omg_zero":
            failures.append("a zero collapsed OpenMC cross section must be reported as such")
        conv = p173.convergence({"R": {"a|1": {"mean": 1.0, "std_dev": 0.001}, "b|1": {"mean": 1.0, "std_dev": 0.1}}},
                                {"a|1": {"status": "tallied"}, "b|1": {"status": "tallied"}})
        if conv["all_converged"] or conv["over_target"] != 1:
            failures.append("convergence must flag a rate above 0.5%")
        conv = p173.convergence({"R": {"a|1": {"mean": 1.0, "std_dev": 0.001}, "b|1": {"mean": 0.0, "std_dev": 0.0}}},
                                {"a|1": {"status": "tallied"}, "b|1": {"status": "not_tallied"}})
        if not conv["all_converged"]:
            failures.append("an untallied reaction must not hold up convergence")
        energy = p173.mean_energy_eV([1.0, 2.0, 4.0], [1.0, 1.0])
        if abs(energy - (1.0 / math.log(2.0) + 2.0 / math.log(2.0)) / 2.0) > 1e-12:
            failures.append("flux-weighted mean energy wrong")
        rows_json = [{"mt": 16, "product_za": 74185, "reaction_rate_per_target_atom_per_s": 1.0},
                     {"mt": 16, "product_za": 74185, "reaction_rate_per_target_atom_per_s": 2.0},
                     {"mt": 102, "product_za": -1, "reaction_rate_per_target_atom_per_s": 5.0}]
        if p173.inspect_rate(rows_json, 16) != 3.0 or p173.inspect_rate(rows_json, 102) != 5.0 \
                or p173.inspect_rate(rows_json, 107) is not None:
            failures.append("inspect_rate does not follow the harness' row selection")
        failures += group_average_checks()
    print("selftest:", "PASS" if not failures else "FAIL")
    for failure in failures:
        print("  " + failure)
    return not failures


def dense_reference(energy, xs, lo, hi, steps=4000):
    """sigma_g by Simpson's rule in u = ln E on every sub-interval between grid points and group edges (the integrand
    sigma(e^u) is smooth there)."""
    cuts = sorted({lo, hi, *[e for e in energy if lo < e < hi]})
    total = 0.0
    for x1, x2 in zip(cuts, cuts[1:]):
        if x2 <= energy[0] or x1 >= energy[-1]:
            continue

        def sigma(e):
            for i in range(len(energy) - 1):
                if energy[i] <= e <= energy[i + 1] and energy[i + 1] > energy[i]:
                    return xs[i] + (xs[i + 1] - xs[i]) * (e - energy[i]) / (energy[i + 1] - energy[i])
            return 0.0
        u1, u2 = math.log(x1), math.log(x2)
        h = (u2 - u1) / steps
        def f(u):
            return sigma(math.exp(u))
        acc = f(u1 + 1e-14 * (u2 - u1)) + f(u2 - 1e-14 * (u2 - u1))
        for k in range(1, steps):
            acc += (4 if k % 2 else 2) * f(u1 + k * h)
        total += acc * h / 3.0
    return total / math.log(hi / lo)


def group_average_checks():
    failures = []
    bounds = [0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 17.0]
    flat = p173.lethargy_group_average([0.1, 100.0], [3.0, 3.0], bounds)
    if any(abs(v - 3.0) > 1e-12 for v in flat):
        failures.append(f"a constant cross section must give the constant: {flat}")
    # 1/v-like pieces (linear in E between points), a ramp from a threshold and a jump (equal energies)
    energy = [0.7, 1.5, 3.0, 3.0, 5.0, 9.0, 12.0, 14.0]
    xs = [4.0, 2.0, 1.0, 5.0, 3.0, 0.5, 0.0, 7.0]
    got = p173.lethargy_group_average(energy, xs, bounds)
    for g, (lo, hi) in enumerate(zip(bounds, bounds[1:])):
        ref = dense_reference(energy, xs, lo, hi)
        if abs(got[g] - ref) > 1e-9 * max(1.0, abs(ref)):
            failures.append(f"group {g}: {got[g]!r} against dense reference {ref!r}")
    # exact against a closed form: sigma = E on [1, 4] gives (4 - 1) / ln 4 in one group
    one = p173.lethargy_group_average([1.0, 4.0], [1.0, 4.0], [1.0, 4.0])[0]
    if abs(one - 3.0 / math.log(4.0)) > 1e-14:
        failures.append(f"linear closed form wrong: {one}")
    # close grid points (series branch) against the same closed form
    tiny = p173.lethargy_group_average([1.0, 1.0 + 1e-6], [1.0, 1.0 + 1e-6], [1.0, 1.0 + 1e-6])[0]
    if abs(tiny - 1e-6 / math.log1p(1e-6)) > 1e-14:
        failures.append(f"close-point integral wrong: {tiny}")
    # outside the table the cross section is zero
    zero = p173.lethargy_group_average([2.0, 3.0], [1.0, 1.0], [0.5, 1.0, 2.0, 3.0, 5.0])
    if zero[0] != 0.0 or zero[1] != 0.0 or abs(zero[2] - 1.0) > 1e-14 or zero[3] != 0.0:
        failures.append(f"zero outside the table wrong: {zero}")
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
    gates, result, sentences = evaluate(record)
    passed = all(not problems for problems in gates.values())
    verdict = {
        "protocol": "ACTINV-P173",
        "verdict": "P173-PASS (pending G4, CI on the evidence commit)" if passed else "P173-FAIL",
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
    print(verdict["verdict"] if passed else "P173-FAIL")
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
