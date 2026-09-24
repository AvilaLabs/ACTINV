#!/usr/bin/env python3
"""P43 G3: the frozen conformance probes — every refusal must be a
named error; the positive probes must apply and ledger cleanly."""
import json
import os
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ACTINV = os.path.join(ROOT, "target", "release", "actinv")
SEALS = json.load(open(os.path.join(ROOT, "results", "g0_p43_seals.json")))
WORK = os.path.expanduser("~/nuclear-data/p43-work")
G3 = os.path.join(WORK, "g3")
OUT = os.path.join(ROOT, "results", "g3_p43_conformance.json")
MECH = os.path.join(WORK, "p43-mech.json")


def cgroup(cmd):
    return ["systemd-run", "--user", "--scope", "-q",
            "-p", "MemoryMax=6G", "-p", "MemorySwapMax=0",
            "-p", "TasksMax=128", "-p", "CPUQuota=200%",
            "--", "env",
            f"TMPDIR={os.path.join(ROOT, 'target', 'preflight-tmp')}",
            "RAYON_NUM_THREADS=2",
            *cmd]


def base_spec():
    # the decay-trace fixture: Mn56 material — chain includes Mn56
    # (radioactive), Fe56 (stable daughter) and can mount yield scales
    # only when the material is fissile, so a second spec uses u235.
    return json.load(open(os.path.join(WORK,
                                       "p43_decay_trace_nominal.json")))


def u_spec():
    return json.load(open(os.path.join(WORK,
                                       "p43_yield_trace_nominal.json")))


def try_spec(spec, name):
    os.makedirs(G3, exist_ok=True)
    sp = os.path.join(G3, f"{name}.json")
    op = os.path.join(G3, f"{name}.out.json")
    json.dump(spec, open(sp, "w"))
    r = subprocess.run(cgroup([ACTINV, "run", sp, op]),
                       capture_output=True, text=True)
    out = json.load(open(op)) if r.returncode == 0 and \
        os.path.isfile(op) else None
    return r, out


def base_study(study_id):
    study = json.load(open(MECH))
    study["study_id"] = study_id
    study["cases"]["schedules"] = [
        s for s in study["cases"]["schedules"]
        if s["name"] == "pulse_5min"]
    study["robustness"]["samples"] = 2
    return study


def try_validate(study, name):
    os.makedirs(G3, exist_ok=True)
    sp = os.path.join(G3, f"{name}.json")
    json.dump(study, open(sp, "w"))
    return subprocess.run(cgroup([ACTINV, "study", "validate", sp]),
                          capture_output=True, text=True)


def scales_ledgered(out):
    # the ledger reports scale-map entry counts; a non-scaled spec
    # ledgered 0, so presence alone proves nothing — count must be >0
    a = (out or {}).get("ledger", {}).get("assembly", {})
    return (a.get("rate_scale", 0) > 0,
            a.get("decay_scale", 0) > 0,
            a.get("yield_scale", 0) > 0)


def main():
    res = {}

    # decay_scale on a nuclide absent from the chain -> named error
    # (the archive has 3873 states — Pu239 IS in it; H999 parses as
    # an explicit nuclide key but no such nuclide exists)
    spec = base_spec()
    spec["options"]["decay_scale"] = {"H999": 1.5}
    r, _ = try_spec(spec, "decay_absent")
    res["decay_scale_absent_nuclide"] = {
        "rejected": r.returncode != 0 and "not in the decay chain"
        in r.stderr + r.stdout,
        "reason": (r.stderr + r.stdout).strip()[:200],
    }

    # decay_scale on a stable chain member (Fe56, lambda=0) -> named
    # error
    spec = base_spec()
    spec["options"]["decay_scale"] = {"Fe56": 1.5}
    r, _ = try_spec(spec, "decay_stable")
    res["decay_scale_stable_nuclide"] = {
        "rejected": r.returncode != 0 and "is stable"
        in r.stderr + r.stdout,
        "reason": (r.stderr + r.stdout).strip()[:200],
    }

    # yield_scale on a pair absent from the effective yields -> named
    # error (Te144 is in the file but outside the bracketing tables)
    spec = u_spec()
    spec["options"]["yield_scale"] = {"U235:Te144": 2.0}
    r, _ = try_spec(spec, "yield_absent")
    res["yield_scale_absent_pair"] = {
        "rejected": r.returncode != 0 and "absent from the effective"
        in r.stderr + r.stdout,
        "reason": (r.stderr + r.stdout).strip()[:200],
    }

    # fission_yields channel on a study with no fissile material ->
    # refused at validation
    st = base_study("p43-neg-nofissile")
    st["cases"]["materials"] = [
        m for m in st["cases"]["materials"] if m["name"] != "u235"]
    st["robustness"]["channels"] = {"fission_yields": True}
    r = try_validate(st, "nofissile")
    res["fission_channel_no_fissile_case"] = {
        "rejected": r.returncode != 0 and "fission_yields"
        in r.stderr + r.stdout,
        "reason": (r.stderr + r.stdout).strip()[:200],
    }

    # decay_scale + yield_scale on a plain (non-robustness) spec:
    # accepted and ledgered
    spec = u_spec()
    spec["options"]["decay_scale"] = {"I135": 1.5}
    spec["options"]["yield_scale"] = {"U235:I135": 2.0}
    r, out = try_spec(spec, "plain_scales")
    rsl, dsl, ysl = scales_ledgered(out)
    res["scales_on_plain_spec"] = {
        "rejected": False,
        "accepted_and_ledgered": r.returncode == 0 and dsl and ysl,
        "reason": (r.stderr + r.stdout).strip()[:200],
    }

    # all three scale maps on one spec: all apply, all ledgered
    spec = u_spec()
    spec["options"]["rate_scale"] = {"0": 1.0}
    spec["options"]["decay_scale"] = {"I135": 1.2}
    spec["options"]["yield_scale"] = {"U235:I135": 1.5}
    r, out = try_spec(spec, "all_scales")
    rsl, dsl, ysl = scales_ledgered(out)
    res["all_scales_combined"] = {
        "rejected": False,
        "all_ledgered": r.returncode == 0 and rsl and dsl and ysl,
        "reason": (r.stderr + r.stdout).strip()[:200],
    }

    # vocabulary refusals on the study schema
    st = base_study("p43-neg-chan")
    st["robustness"]["channels"]["bogus"] = True
    r = try_validate(st, "neg_chan")
    res["unknown_channel"] = {
        "rejected": r.returncode != 0,
        "reason": (r.stderr + r.stdout).strip()[:200],
    }
    st = base_study("p43-neg-std")
    st["robustness"]["channels"]["flux_rel_std"] = -0.5
    r = try_validate(st, "neg_std")
    res["negative_std"] = {
        "rejected": r.returncode != 0,
        "reason": (r.stderr + r.stdout).strip()[:200],
    }
    acc = []
    for tag, n in (("low", 1), ("high", 4097)):
        st = base_study(f"p43-neg-samples-{tag}")
        st["robustness"]["samples"] = n
        r = try_validate(st, f"neg_samples_{tag}")
        acc.append(r.returncode != 0)
    res["samples_out_of_range"] = {
        "rejected": all(acc),
        "reason": {"low": acc[0], "high": acc[1]},
    }

    # mutated digests fail resume closed: flip a recorded spec_sha in
    # the progress file -> that sample re-executes rather than resuming
    import shutil
    src = os.path.join(WORK, "g2", "resume")
    dst = os.path.join(G3, "resume_mut")
    if os.path.isdir(dst):
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    os.remove(os.path.join(dst, "study_record.json"))
    ndp = os.path.join(dst, "cases",
                       "fe__fns_709__pulse_5min", "rob_samples.ndjson")
    lines = open(ndp).read().splitlines()
    rec = json.loads(lines[1])
    rec["spec_sha256"] = "0" * 64
    lines[1] = json.dumps(rec)
    open(ndp, "w").write("\n".join(lines) + "\n")
    r = subprocess.run(
        cgroup([ACTINV, "study", "run",
                os.path.join(dst, "study.json"), dst]),
        capture_output=True, text=True)
    rec2 = json.load(open(os.path.join(dst, "study_record.json")))
    case = rec2["cases"][0]
    res["mutated_digest_fails_resume"] = {
        "rejected": False,
        "re_executed_exactly_one": r.returncode == 0
        and case["robustness"]["n_reused_samples"] == 3
        and case["robustness"]["samples"] == 4,
        "reason": (r.stderr + r.stdout).strip()[:200],
    }

    # prepared-run reuse on the G1 record: distinct signatures, not
    # per-solve
    rec = json.load(open(os.path.join(ROOT, "results",
                                      "g1_p43_mechanics.json")))
    g1rec = json.load(open(rec["record_path"]))
    n_prepared = g1rec.get("prepared_runs")
    n_cases = len(g1rec["cases"])
    res["prepared_run_reuse"] = {
        "rejected": False,
        "prepared_runs": n_prepared,
        "cases": n_cases,
        "reused": n_prepared is not None and n_prepared <= n_cases,
        "interpretation": "one PreparedRun per distinct signature "
                          "across nominal + samples + attribution",
    }

    ok = True
    for k, v in res.items():
        if "accepted_and_ledgered" in v:
            ok &= v["accepted_and_ledgered"]
        elif "all_ledgered" in v:
            ok &= v["all_ledgered"]
        elif "re_executed_exactly_one" in v:
            ok &= v["re_executed_exactly_one"]
        elif "reused" in v:
            ok &= v["reused"]
        else:
            ok &= v["rejected"]
    record = {"gate": "G3", "phase": "P43", "probes": res, "pass": ok}
    json.dump(record, open(OUT, "w"), indent=2, sort_keys=True)
    print(json.dumps({k: {kk: vv for kk, vv in v.items()
                          if kk != "reason"}
                      for k, v in res.items()}, indent=1))


if __name__ == "__main__":
    main()
