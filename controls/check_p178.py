#!/usr/bin/env python3
"""P178 checker (protocols/ACTINV-P178_PROTOCOL.md): ACTINV's `self_shielding.mixture` on four held-out models against
OpenMC continuous-energy tallies. Reads results/p178_ce_rates.json written by `controls/p178_ce_rates.py assemble`.
G0-G3 are evaluated here; G4 (CI on the pushed evidence commit) is external. The P173 checker's gate functions are
imported and run with P178's pins and tables (importing controls/p178_ce_rates.py rebinds them in the p173 module);
this file adds the mixture-arm checks and the decision rules.

  G0  The protocol's SHA-256 is in protocols/protocol_hash.txt; the record's harness, P173-harness, constants and
      mixture-constants hashes match the files (and the harness' own constants).
  G1  P173's G1 with P178's pins (library, index, stage manifest, binary), OpenMC 0.15.3, a clean tracked tree, the
      bounded cgroup of every mode (the mixture mode too) and OpenMC run, the collapse check; every mixture model used
      the pinned binary and tapes whose SHA-256 match the stage manifest, and the recorded manifest hash is the pin.
  G2  P173's G2 (every listed row scored or reason-coded, r / r_proc / r_coll and their standard errors recompute,
      r = r_proc + r_coll, the batch schedule) plus: every row has a mixture status, r_mix and its standard error
      recompute from R_mixture, and R_mixture equals R_actinv to 1e-12 for a target the mixture does not cover.
  G3  The decision rules (capture outcome, controls, diagnostics, allowed sentences) are recomputed from the record and
      written to results/p178_verdict.json (with --no-write, compared with it).

P178_REHEARSAL=1 reads and writes the rehearsal's paths under target/p178-rehearsal, as the harness does, and accepts
a record whose pins are still placeholders (the record's own digests stand in for them).

Verdict: P178-PASS if G0-G3 hold (G4 external), whatever the outcome.

Usage: check_p178.py [--record FILE] [--no-write]
       check_p178.py --self-test
"""
import argparse
import copy
import json
import math
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "controls"))
import p178_ce_rates as p178  # noqa: E402  (first: rebinds P173's tables, paths and pins to P178's)
import check_p173 as base  # noqa: E402

p173 = p178.p173

PROTOCOL = ROOT / "protocols" / "ACTINV-P178_PROTOCOL.md"
HASH_LOG = base.HASH_LOG
REHEARSAL = p178.rehearsal()
RECORD = p178.RECORD
VERDICT = ROOT / "target/p178-rehearsal/verdict.json" if REHEARSAL else ROOT / "results" / "p178_verdict.json"
HARNESS = ROOT / "controls" / "p178_ce_rates.py"
P173_HARNESS = p178.P173_HARNESS

CAPTURE_MT = 102
MATERIAL_ABS = base.MATERIAL_ABS_R
MATERIAL_SIGMAS = base.MATERIAL_SIGMAS
CONTROL_SHIFT = 0.01  # a control row's |r_mix - r| must stay within this
UNSHIELDED_EXACT = 1e-12  # R_mixture = R_actinv for a target the mixture does not cover
sha256 = base.sha256
good = base.good
close = base.close


def rel_close(a, b, tol):
    return good(a) and good(b) and abs(a - b) <= tol * max(abs(a), abs(b))


def round_up_tenth(percent):
    """`percent` rounded up to 0.1, with a 1e-9 slack on the tenths so float noise does not add a tenth."""
    return math.ceil(percent * 10.0 - 1e-9) / 10.0


# ------------------------------------------------------------------------------------------------------ gates

def g0(record, *, protocol=PROTOCOL, log_text=None, harness=HARNESS, groups=p173.GROUPS, p173_harness=P173_HARNESS):
    problems = base.g0(record, protocol=protocol, log_text=log_text, harness=harness, groups=groups)
    if not protocol.is_file():
        return problems
    arm = record.get("mixture_arm")
    if not isinstance(arm, dict):
        return problems + ["the record has no mixture_arm block"]
    if arm.get("p173_harness_sha256") != sha256(p173_harness):
        problems.append("the record's P173-harness hash is not the P173 harness file's")
    if arm.get("p178_constants_sha256") != p178.constants_digest():
        problems.append("the record's P178 constants hash is not the harness' constants")
    if arm.get("constants_sha256") != p178.mixture_constants_digest():
        problems.append("the record's mixture-constants hash is not the harness' mixture constants")
    return problems


def g1(record, *, bin_pin, stage_pin, manifest_files):
    """P173's G1 with P178's pins, then the mixture arm. `manifest_files` is the stage manifest's {file: entry}
    (None when it could not be read)."""
    problems = base.g1(record, bin_pin=bin_pin)
    if REHEARSAL:  # a rehearsal runs before the pins are filled in; the other pins are still compared
        placeholders = {f"input {name} hash is not the pin" for name, (_, sha) in p173.PINNED.items()
                        if sha == p178.PLACEHOLDER}
        problems = [p for p in problems if p not in placeholders]
    seen = {entry.get("where") for entry in record.get("environment", {}).get("cgroups", [])}
    for m in p173.MODEL_ORDER:
        if f"actinv_mixture:{m}" not in seen:
            problems.append(f"no cgroup record for actinv_mixture:{m}")
    arm = record.get("mixture_arm")
    if not isinstance(arm, dict):
        return problems + ["the record has no mixture_arm block"]
    if stage_pin is None or arm.get("stage_manifest_sha256") != stage_pin:
        problems.append("the mixture arm's stage-manifest hash is not the pin")
    if manifest_files is None:
        problems.append("the stage manifest could not be read to check the tapes")
    for m in p173.MODEL_ORDER:
        info = arm.get("models", {}).get(m)
        if info is None:
            problems.append(f"no mixture record for model {m}")
            continue
        if bin_pin is None or info.get("actinv_bin_sha256") != bin_pin:
            problems.append(f"mixture model {m} did not run with the pinned actinv binary")
        tapes = info.get("tapes") or []
        if not tapes:
            problems.append(f"mixture model {m} used no evaluation tape")
        for tape in tapes:
            name = Path(str(tape.get("path"))).name
            entry = (manifest_files or {}).get(name)
            if manifest_files is not None and (entry is None or entry.get("sha256") != tape.get("sha256")):
                problems.append(f"mixture model {m}: tape {name} does not match the stage manifest")
    return problems


def g2(record):
    problems = base.g2(record)
    for r in record.get("rows", []):
        name = f"{r.get('model')}/{r.get('region')}/{r.get('target')} MT{r.get('mt')}"
        status = r.get("mixture_status")
        if status not in ("scored", "not_scored"):
            problems.append(f"{name}: mixture status {status!r}")
        elif status == "not_scored":
            if r.get("status") == "scored" and not r.get("mixture_reason"):
                problems.append(f"{name}: mixture not scored without a reason")
        elif r.get("status") != "scored":
            problems.append(f"{name}: mixture scored on a row that is not scored")
        else:
            vals = [r.get(k) for k in ("R_mixture", "R_mixture_se_bound", "r_mix", "se_r_mix", "R_actinv", "R_openmc",
                                       "R_openmc_se")]
            if not all(good(v) for v in vals) or not (r["R_mixture"] > 0 and r["R_actinv"] > 0 and r["R_openmc"] > 0):
                problems.append(f"{name}: scored mixture row without finite positive rates")
                continue
            r_re, se_re = p173.log_ratio(r["R_mixture"], r["R_mixture_se_bound"], r["R_openmc"], r["R_openmc_se"])
            if not (close(r["r_mix"], r_re) and close(r["se_r_mix"], se_re)):
                problems.append(f"{name}: r_mix or its standard error does not recompute from the rates")
            if not isinstance(r.get("mixture_target_covered"), bool):
                problems.append(f"{name}: mixture_target_covered is not recorded")
            elif not r["mixture_target_covered"] and not rel_close(r["R_mixture"], r["R_actinv"], UNSHIELDED_EXACT):
                problems.append(f"{name}: target not covered by the mixture but R_mixture differs from R_actinv")
    return problems[:200]


# ------------------------------------------------------------------------------------------------------ decision rules

def is_used(row):
    """R_openmc at relative standard error <= 0.5 % (the harness' converged target), and r and r_mix both scored."""
    return (row.get("status") == "scored" and row.get("mixture_status") == "scored"
            and good(row.get("R_openmc")) and row["R_openmc"] > 0 and good(row.get("R_openmc_se"))
            and row["R_openmc_se"] / row["R_openmc"] <= p173.REL_SE_TARGET)


def material(value, se):
    return abs(value) > MATERIAL_ABS and abs(value) > MATERIAL_SIGMAS * se


def is_material(row, which="r"):
    """For a used row, |x| > 0.05 and |x| > 3 se(x), x being r or r_mix."""
    return is_used(row) and material(row[which], row["se_" + which])


def brief(row):
    return {"region": row["region"], "reaction": row["reaction"], "r": row["r"], "se_r": row["se_r"],
            "r_mix": row["r_mix"], "se_r_mix": row["se_r_mix"]}


def diagnostic(row):
    """Reported, not decided on: the mixture rate with processing removed (r_mix - r_proc), the effective scale, the
    groups scaled and the smallest factor. `processing_limited`: a capture row material in r_mix whose remainder after
    removing processing is not material (se(r_mix) taken as its error)."""
    d = {**brief(row), "r_proc": row.get("r_proc"), "r_mix_minus_r_proc": None,
         "scale_effective": row.get("scale_effective"), "groups_scaled": row.get("mixture_groups_scaled"),
         "min_scale": row.get("mixture_min_scale"), "target_covered": row.get("mixture_target_covered"),
         "processing_limited": False}
    if good(row.get("r_proc")):
        d["r_mix_minus_r_proc"] = row["r_mix"] - row["r_proc"]
        if row["mt"] == CAPTURE_MT and is_material(row, "r_mix"):
            d["processing_limited"] = not material(d["r_mix_minus_r_proc"], row["se_r_mix"])
    return d


def sentences_for(outcome, rows_used_capture, n_capture_listed, x_percent, y_percent):
    """The protocol's allowed public sentences for `outcome` (empty for NOT QUALIFIED or without a used capture row)."""
    if outcome not in ("QUALIFIED", "NOTHING TO CORRECT") or x_percent is None:
        return []
    x, y = round_up_tenth(x_percent), round_up_tenth(y_percent)
    if outcome == "QUALIFIED":
        first = ("On four fusion materials held out from its development (Eurofer-97, Inconel 718, a W-Ni-Fe heavy "
                 "alloy and copper), ACTINV's mixture self-shielding brought every listed resonance-capture rate "
                 f"within {x:.1f}% of OpenMC's continuous-energy rate on the same ENDF/B-VIII.1 data, where the "
                 f"unshielded rates differed by up to {y:.1f}%.")
    else:
        first = ("On four held-out fusion materials, ACTINV's unshielded resonance-capture rates were already within "
                 f"{y:.1f}% of OpenMC's continuous-energy rates on the same data, and its mixture self-shielding "
                 f"kept them within {x:.1f}%.")
    out = [first]
    unused = n_capture_listed - rows_used_capture
    if unused:
        out.append(f"{unused} of {n_capture_listed} listed capture rows were not scored or did not reach a "
                   f"{100 * p173.REL_SE_TARGET:.1f}% standard error and are not covered by that sentence.")
    return out


def summary(record):
    rows = record["rows"]
    used = [r for r in rows if is_used(r)]
    capture_listed = [r for r in rows if r["mt"] == CAPTURE_MT]
    capture = [r for r in used if r["mt"] == CAPTURE_MT]
    controls = [r for r in used if r["mt"] != CAPTURE_MT]
    mat_r = [r for r in capture if is_material(r, "r")]
    mat_mix = [r for r in capture if is_material(r, "r_mix")]
    if mat_mix:
        capture_outcome = "NOT QUALIFIED"
    elif mat_r:
        capture_outcome = "QUALIFIED"
    else:
        capture_outcome = "NOTHING TO CORRECT"
    failed = []
    for r in controls:
        shift = r["r_mix"] - r["r"]
        why = []
        if abs(shift) > CONTROL_SHIFT:
            why.append(f"|r_mix - r| = {abs(shift):.4f} > {CONTROL_SHIFT}")
        if is_material(r, "r_mix") and not is_material(r, "r"):
            why.append("material in r_mix but not in r")
        if why:
            failed.append({**brief(r), "r_mix_minus_r": shift, "failed": why})
    controls_hold = not failed
    if capture_outcome == "NOT QUALIFIED" or not controls_hold:
        outcome = "NOT QUALIFIED"
    else:
        outcome = capture_outcome
    diagnostics = [{"model": r["model"], "mt": r["mt"], "kind": "capture" if r["mt"] == CAPTURE_MT else "control",
                    **diagnostic(r)} for r in used]
    x = max((math.expm1(abs(r["r_mix"])) * 100.0 for r in capture), default=None)
    y = max((math.expm1(abs(r["r"])) * 100.0 for r in capture), default=None)
    arm = record["mixture_arm"]["models"]

    def max_abs(which, model):
        return max((abs(r[which]) for r in capture if r["model"] == model), default=None)
    not_scored = {}
    for r in rows:
        if r["status"] == "not_scored":
            not_scored[r["reason_code"]] = not_scored.get(r["reason_code"], 0) + 1
    result = {
        "outcome": outcome, "capture_outcome": capture_outcome, "controls_hold": controls_hold,
        "controls_failed": failed,
        "rows": len(rows), "rows_used": len(used), "capture_rows_listed": len(capture_listed),
        "capture_rows_used": len(capture), "control_rows_used": len(controls),
        "rows_not_scored": sum(r["status"] == "not_scored" for r in rows), "not_scored_by_reason": not_scored,
        "rows_mixture_not_scored": sum(r["status"] == "scored" and r["mixture_status"] != "scored" for r in rows),
        "material_capture_r_rows": [brief(r) for r in mat_r],
        "material_capture_r_mix_rows": [brief(r) for r in mat_mix],
        "processing_limited_rows": [{"model": d["model"], **{k: d[k] for k in ("region", "reaction", "r_mix",
                                     "r_mix_minus_r_proc")}} for d in diagnostics if d["processing_limited"]],
        "max_percent_r_capture": y, "max_percent_r_mix_capture": x,
        "by_model": {m: {"capture_rows_used": sum(r["model"] == m for r in capture),
                         "max_abs_r_capture": max_abs("r", m), "max_abs_r_mix_capture": max_abs("r_mix", m),
                         "nuclides_without_tape": arm.get(m, {}).get("nuclides_without_tape"),
                         "zero_k_clips": {region: data["mixture"]["zero_k_clips"]
                                          for region, data in arm.get(m, {}).get("regions", {}).items()
                                          if data.get("mixture", {}).get("zero_k_clips")}}
                     for m in p173.MODEL_ORDER},
        "diagnostics": diagnostics,
    }
    return result, sentences_for(outcome, len(capture), len(capture_listed), x, y)


def load_manifest():
    try:
        return json.loads(p178.STAGE_MANIFEST.read_text())["files"]
    except (OSError, ValueError, KeyError):
        return None


def current_pins(record):
    """(binary pin, stage-manifest pin) from the harness. A rehearsal with placeholders takes the record's own values,
    so it still checks that the modes agree with one another."""
    bin_pin = p178.ACTINV_BIN_SHA256_P178
    stage_pin = p178.PINNED["stage_manifest"][1]
    if REHEARSAL:
        if bin_pin == p178.PLACEHOLDER:
            bin_pin = record.get("environment", {}).get("actinv_bin_sha256")
        if stage_pin == p178.PLACEHOLDER:
            stage_pin = record.get("mixture_arm", {}).get("stage_manifest_sha256")
    return (None if bin_pin == p178.PLACEHOLDER else bin_pin), (None if stage_pin == p178.PLACEHOLDER else stage_pin)


def evaluate(record, *, g0_args=None, pins=None, manifest_files="load"):
    bin_pin, stage_pin = current_pins(record) if pins is None else pins
    if manifest_files == "load":
        manifest_files = load_manifest()
    gates = {"G0": g0(record, **(g0_args or {})),
             "G1": g1(record, bin_pin=bin_pin, stage_pin=stage_pin, manifest_files=manifest_files),
             "G2": g2(record)}
    if gates["G2"] or gates["G0"] and not isinstance(record.get("mixture_arm"), dict):
        result, sentences = {}, []
        gates["G3"] = ["summary not computed (fidelity failed)"]
    else:
        result, sentences = summary(record)
        gates["G3"] = []
    return gates, result, sentences


# ------------------------------------------------------------------------------------------------------ self-test

def synthetic_record(harness_sha, groups_sha, p173_harness_sha):
    """P173's synthetic record (143 rows here, every one used and unremarkable) with the mixture arm added: every
    R_mixture is 1.005 R_openmc (r_mix 0.005), the target covered."""
    record = base.synthetic_record(harness_sha, groups_sha)
    cg = {"memory.max": str(6 * p173.GIB), "memory.swap.max": "0", "pids.max": "128", "cpu.max": "200000 100000"}
    record["environment"]["cgroups"] += [{"where": f"actinv_mixture:{m}", "cgroup": dict(cg)}
                                         for m in p173.MODEL_ORDER]
    record["mixture_arm"] = {
        "constants_sha256": p178.mixture_constants_digest(), "p178_constants_sha256": p178.constants_digest(),
        "p173_harness_sha256": p173_harness_sha, "stage_manifest_sha256": "d" * 64, "temperature_K": 293.6,
        "profile": "endfb8",
        "models": {m: {"tapes": [{"nuclide": "Fe56", "path": "/stage/Fe56.endf", "sha256": "c" * 64}],
                       "nuclides_without_tape": {"C12": "first energy range is LRU=0"},
                       "actinv_bin_sha256": "b" * 64, "actinv_version": "actinv 1.7.1", "regions": {}}
                   for m in p173.MODEL_ORDER}}
    for row in record["rows"]:
        row.update(R_mixture=1.005, R_mixture_se_bound=0.001, mixture_target_covered=True, mixture_groups_scaled=5,
                   mixture_min_scale=0.9, mixture_status="scored", mixture_reason=None)
        refresh_mix(row)
    return record


def refresh_mix(row):
    """Recompute r_mix, its standard error and the effective scale of a synthetic row from its rates."""
    row["r_mix"], row["se_r_mix"] = p173.log_ratio(row["R_mixture"], row["R_mixture_se_bound"], row["R_openmc"],
                                                   row["R_openmc_se"])
    row["scale_effective"] = row["R_mixture"] / row["R_actinv"]


def tweak(record, model, region, reactions, *, actinv=None, omg=None, mix=None):
    """Set the listed (target, MT) rows of a region to R_actinv / R_openmc = `actinv`, R_omg / R_openmc = `omg` and
    R_mixture / R_openmc = `mix` (None leaves a rate alone), then recompute every derived quantity."""
    base.set_rates(record, model, region, reactions, actinv=actinv, omg=omg)
    for row in record["rows"]:
        if row["model"] == model and row["region"] == region and (row["target"], row["mt"]) in reactions:
            if mix is not None:
                row["R_mixture"] = mix * row["R_openmc"]
            refresh_mix(row)


def selftest():
    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        protocol, harness, groups = tmp / "ACTINV-P178_PROTOCOL.md", tmp / "harness.py", tmp / "groups.json"
        p173_harness = tmp / "p173_harness.py"
        for f in (protocol, harness, groups, p173_harness):
            f.write_text(f"{f.name} text")
        log_text = f"{sha256(protocol)}  protocols/{protocol.name}\n"
        g0_args = {"protocol": protocol, "log_text": log_text, "harness": harness, "groups": groups,
                   "p173_harness": p173_harness}
        pins = ("b" * 64, "d" * 64)
        manifest = {"Fe56.endf": {"sha256": "c" * 64}}

        def fresh():
            return synthetic_record(sha256(harness), sha256(groups), sha256(p173_harness))

        def run(label, mutate, gate, args=None, pins_=pins, manifest_=manifest):
            trial = fresh()
            mutate(trial)
            gates, result, sentences = evaluate(trial, g0_args=dict(g0_args, **(args or {})), pins=pins_,
                                                manifest_files=manifest_)
            bad = [name for name, problems in gates.items() if problems and name != "G3"]
            if gate is None and bad:
                failures.append(f"{label}: expected pass, failed {bad}: {[gates[b][:3] for b in bad]}")
            if gate is not None and gate not in bad:
                failures.append(f"{label}: expected {gate} to fail")
            return result, sentences

        def rows_of(r, region, target, mt):
            return [x for x in r["rows"] if (x["region"], x["target"], x["mt"]) == (region, target, mt)]

        total = len(list(p173.expected_rows()))
        capture_total = sum(mt == CAPTURE_MT for _, _, _, mt in p173.expected_rows())
        if (total, capture_total) != (143, 73):
            failures.append(f"row counts {total}/{capture_total}, expected 143/73")
        h0, b0 = p173.regions_of("H")[0], p173.regions_of("B")[0]
        # rounding helper
        for value, want in ((0.0, 0.0), (1.0, 1.0), (1.0 + 1e-12, 1.0), (1.0000001, 1.1), (2.34, 2.4), (20.0, 20.0)):
            if round_up_tenth(value) != want:
                failures.append(f"round_up_tenth({value}) = {round_up_tenth(value)}, expected {want}")
        # clean: nothing material, controls hold -> NOTHING TO CORRECT with both numbers in the sentence
        result, sentences = run("clean", lambda r: None, None)
        if (result.get("outcome") != "NOTHING TO CORRECT" or not result["controls_hold"]
                or result["rows_used"] != total or result["capture_rows_used"] != capture_total
                or len(sentences) != 1 or "already within 1.0% of OpenMC's continuous-energy rates" not in sentences[0]
                or "kept them within 0.5%." not in sentences[0]):
            failures.append(f"clean summary wrong: {result.get('outcome')} {sentences}")
        if result.get("by_model", {}).get("E", {}).get("nuclides_without_tape") != {"C12": "first energy range is LRU=0"}:
            failures.append("nuclides without a tape must be reported per model")
        # QUALIFIED: a capture row material in r (20 %) that the mixture removes
        result, sentences = run("qualified", lambda r: tweak(r, "H", h0, {("W186", 102)}, actinv=1.2, omg=1.2, mix=1.0),
                                None)
        if result.get("outcome") != "QUALIFIED" or len(sentences) != 1 or sentences[0] != (
                "On four fusion materials held out from its development (Eurofer-97, Inconel 718, a W-Ni-Fe heavy alloy "
                "and copper), ACTINV's mixture self-shielding brought every listed resonance-capture rate within 0.5% "
                "of OpenMC's continuous-energy rate on the same ENDF/B-VIII.1 data, where the unshielded rates "
                "differed by up to 20.0%."):
            failures.append(f"QUALIFIED outcome or sentence wrong: {result.get('outcome')} {sentences}")
        # the second sentence: a capture row that did not reach 0.5 % is counted, not covered
        def unused_capture(r):
            tweak(r, "H", h0, {("W186", 102)}, actinv=1.2, omg=1.2, mix=1.0)
            for row in rows_of(r, h0, "W182", 102):
                row.update(R_openmc_se=0.1, rel_se_openmc=0.1, converged=False)
                base.refresh(row)
                refresh_mix(row)
        result, sentences = run("capture row not used", unused_capture, None)
        if (result.get("capture_rows_used") != capture_total - 1 or len(sentences) != 2
                or not sentences[1].startswith(f"1 of {capture_total} listed capture rows")):
            failures.append(f"an unused capture row must be counted in a second sentence: {sentences}")
        # NOT QUALIFIED by a capture row material in r_mix (overcorrection), whatever r says
        result, sentences = run("overcorrected", lambda r: tweak(r, "B", b0, {("Cu63", 102)}, mix=0.8), None)
        if result.get("outcome") != "NOT QUALIFIED" or sentences or len(result["material_capture_r_mix_rows"]) != 1:
            failures.append(f"a capture row material in r_mix must be NOT QUALIFIED: {result.get('outcome')}")
        result, sentences = run("qualified-looking but r_mix material",
                                lambda r: (tweak(r, "H", h0, {("W186", 102)}, actinv=1.2, omg=1.2, mix=1.0),
                                           tweak(r, "B", b0, {("Cu63", 102)}, mix=1.2)), None)
        if result.get("outcome") != "NOT QUALIFIED" or sentences:
            failures.append("one material r_mix row outweighs a corrected one")
        # NOT QUALIFIED by controls although the capture rows are corrected
        def control_moves(r):
            tweak(r, "H", h0, {("W186", 102)}, actinv=1.2, omg=1.2, mix=1.0)
            tweak(r, "B", b0, {("Cu63", 107)}, mix=1.05)  # r_mix 0.049 (not material) but 0.039 from r
        result, sentences = run("controls fail", control_moves, None)
        if (result.get("outcome") != "NOT QUALIFIED" or result["capture_outcome"] != "QUALIFIED"
                or result["controls_hold"] or len(result["controls_failed"]) != 1 or sentences
                or result["controls_failed"][0]["reaction"] != "Cu63(n,a)Co60"):
            failures.append(f"a control moved by the mixture must make NOTHING/QUALIFIED NOT QUALIFIED: {result.get('outcome')} "
                            f"{result.get('controls_failed')}")
        result, _ = run("controls fail, nothing to correct", lambda r: tweak(r, "B", b0, {("Cu63", 107)}, mix=1.05), None)
        if result.get("outcome") != "NOT QUALIFIED" or result["capture_outcome"] != "NOTHING TO CORRECT":
            failures.append("controls that fail also stop NOTHING TO CORRECT")
        # the second control clause: a shift within 0.01 that carries r_mix over the materiality line while r is below it
        def crosses(r):
            tweak(r, "B", b0, {("Cu63", 107)}, actinv=math.exp(0.0499), omg=math.exp(0.0499), mix=math.exp(0.0505))
        result, _ = run("control crosses materiality", crosses, None)
        if result.get("outcome") != "NOT QUALIFIED" or "material in r_mix but not in r" not in " ".join(
                result["controls_failed"][0]["failed"] if result.get("controls_failed") else []):
            failures.append(f"a control material in r_mix only must fail: {result.get('controls_failed')}")
        # a control material in both r and r_mix and unmoved holds
        def both_material(r):
            tweak(r, "B", b0, {("Cu63", 107)}, actinv=1.2, omg=1.2, mix=1.2)
        result, _ = run("control material in both", both_material, None)
        if not result.get("controls_hold") or result.get("outcome") != "NOTHING TO CORRECT":
            failures.append("a control material in r and r_mix and unmoved must hold")
        # processing_limited: material in r_mix, but r_mix - r_proc is zero (R_omg carries the whole difference)
        result, _ = run("processing limited", lambda r: tweak(r, "B", b0, {("Cu63", 102)}, actinv=1.0, omg=1.25, mix=0.8),
                        None)
        flagged = result.get("processing_limited_rows", [])
        if len(flagged) != 1 or flagged[0]["reaction"] != "Cu63(n,gamma)Cu64" or abs(flagged[0]["r_mix_minus_r_proc"]) > 1e-12:
            failures.append(f"processing_limited row not flagged: {flagged}")
        result, _ = run("not processing limited", lambda r: tweak(r, "B", b0, {("Cu63", 102)}, actinv=1.0, omg=1.0, mix=0.8),
                        None)
        if result.get("processing_limited_rows") or len(result["material_capture_r_mix_rows"]) != 1:
            failures.append("a row whose r_mix is not explained by processing must not be flagged")
        # rounding in the sentence: r_mix 2.34 %, r 3.01 % -> within 2.4%, up to 3.1%
        result, sentences = run("rounding", lambda r: [tweak(r, "H", h0, {("W186", 102)}, actinv=1.0301, omg=1.0301,
                                                              mix=1.0234)] + [
            tweak(r, "H", h0, {(t, 102)}, mix=1.0234) for t in ("W184", "W182", "W183")], None)
        if not sentences or "within 2.4%" not in sentences[0] or "within 3.1%" not in sentences[0]:
            failures.append(f"percentages must round up to 0.1: {sentences}")
        # diagnostics carry the scale columns
        d0 = next((d for d in result.get("diagnostics", []) if d["mt"] == CAPTURE_MT), {})
        if not {"scale_effective", "groups_scaled", "min_scale", "r_mix_minus_r_proc"} <= set(d0):
            failures.append("diagnostics are missing columns")
        # rows not scored or without a mixture rate are excluded from the used set
        def mixture_unscored(r):
            for row in rows_of(r, b0, "Cu63", 102):
                row.update(mixture_status="not_scored", mixture_reason="no ledger factors", r_mix=None, se_r_mix=None)
        result, sentences = run("mixture not scored is excluded", mixture_unscored, None)
        if result.get("capture_rows_used") != capture_total - 1 or result.get("rows_mixture_not_scored") != 1 or len(sentences) != 2:
            failures.append(f"a row without a mixture rate must be excluded and counted: {result.get('capture_rows_used')}")
        # gate failures: G0
        run("protocol hash", lambda r: None, "G0", args={"log_text": ""})
        run("harness hash", lambda r: r["environment"].update(harness_sha256="0" * 64), "G0")
        run("p173 harness hash", lambda r: r["mixture_arm"].update(p173_harness_sha256="0" * 64), "G0")
        run("p178 constants hash", lambda r: r["mixture_arm"].update(p178_constants_sha256="0" * 64), "G0")
        run("mixture constants hash", lambda r: r["mixture_arm"].update(constants_sha256="0" * 64), "G0")
        run("p173 constants hash", lambda r: r["environment"].update(constants_sha256="0" * 64), "G0")
        run("no mixture arm", lambda r: r.pop("mixture_arm"), "G0")
        # G1
        run("binary pin", lambda r: r["environment"].update(actinv_bin_sha256="0" * 64), "G1")
        run("unpinned binary", lambda r: None, "G1", pins_=(None, "d" * 64))
        run("mixture binary", lambda r: r["mixture_arm"]["models"]["H"].update(actinv_bin_sha256="0" * 64), "G1")
        run("stage manifest pin", lambda r: r["mixture_arm"].update(stage_manifest_sha256="0" * 64), "G1")
        run("tape hash", lambda r: r["mixture_arm"]["models"]["B"]["tapes"][0].update(sha256="0" * 64), "G1")
        run("tape not in manifest", lambda r: r["mixture_arm"]["models"]["B"]["tapes"][0].update(path="/s/Zz99.endf"), "G1")
        run("no tapes", lambda r: r["mixture_arm"]["models"]["E"].update(tapes=[]), "G1")
        run("mixture model missing", lambda r: r["mixture_arm"]["models"].pop("I"), "G1")
        run("manifest unreadable", lambda r: None, "G1", manifest_=None)
        run("mixture cgroup missing", lambda r: r["environment"].update(
            cgroups=[c for c in r["environment"]["cgroups"] if c["where"] != "actinv_mixture:E"]), "G1")
        run("mixture cgroup unbounded", lambda r: next(c for c in r["environment"]["cgroups"]
                                                       if c["where"] == "actinv_mixture:I")["cgroup"].update(
            {"memory.swap.max": "max"}), "G1")
        run("p173 check still applies", lambda r: r["environment"].update(clean_tree=False), "G1")
        # G2
        run("p173 fidelity still applies", lambda r: r["rows"][7].update(r=0.5), "G2")
        run("r_mix does not recompute", lambda r: r["rows"][3].update(r_mix=0.4), "G2")
        run("se_r_mix does not recompute", lambda r: r["rows"][3].update(se_r_mix=0.5), "G2")
        run("R_mixture changed after r_mix", lambda r: r["rows"][4].update(R_mixture=1.2), "G2")
        run("mixture status missing", lambda r: r["rows"][5].pop("mixture_status"), "G2")
        run("mixture status invalid", lambda r: r["rows"][5].update(mixture_status="ok"), "G2")
        run("mixture not scored without reason", lambda r: r["rows"][6].update(mixture_status="not_scored",
                                                                              mixture_reason=None), "G2")
        run("R_mixture missing", lambda r: r["rows"][2].update(R_mixture=None), "G2")
        run("uncovered target changed", lambda r: r["rows"][8].update(mixture_target_covered=False), "G2")
        run("coverage not recorded", lambda r: r["rows"][8].update(mixture_target_covered=None), "G2")

        def uncovered_equal(r):
            row = r["rows"][9]
            row.update(mixture_target_covered=False, R_mixture=row["R_actinv"])
            refresh_mix(row)
        run("uncovered target equal to R_actinv", uncovered_equal, None)
        # --no-write comparison material and the record is not mutated by evaluation
        trial = fresh()
        snapshot = copy.deepcopy(trial)
        evaluate(trial, g0_args=g0_args, pins=pins, manifest_files=manifest)
        if trial != snapshot:
            failures.append("evaluation must not mutate the record")
    print("selftest:", "PASS" if not failures else "FAIL")
    for failure in failures:
        print("  " + failure)
    return not failures


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
        "protocol": "ACTINV-P178",
        "verdict": "P178-PASS (pending G4, CI on the evidence commit)" if passed else "P178-FAIL",
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
        VERDICT.parent.mkdir(parents=True, exist_ok=True)
        VERDICT.write_text(json.dumps(verdict, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
    if result:
        print(f"outcome: {result['outcome']} (capture rows: {result['capture_outcome']}; controls "
              f"{'hold' if result['controls_hold'] else 'FAIL'})")
    for sentence in sentences:
        print("  " + sentence)
    print(verdict["verdict"] if passed else "P178-FAIL")
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
