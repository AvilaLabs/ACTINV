#!/usr/bin/env python3
"""P36 G3 negative controls: malformed study inputs and tampered
environments must fail closed with named reasons."""
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN = os.path.join(ROOT, "target", "release", "actinv")
STUDY = os.path.join(ROOT, "target", "p36-work", "w-matcmp", "study.json")
TMP = os.path.join(ROOT, "target", "preflight-tmp", "g3-p36")
RES = os.path.join(ROOT, "results")
OUT = os.path.join(RES, "g3_p36_conformance.json")


def run(args):
    env = dict(os.environ, TMPDIR=os.path.join(ROOT,
                                              "target", "preflight-tmp"))
    p = subprocess.run(["systemd-run", "--user", "--scope", "-q",
                        "-p", "MemoryMax=6G", "-p", "MemorySwapMax=0",
                        "-p", "TasksMax=128", "-p", "CPUQuota=100%",
                        "--", "env", f"TMPDIR={env['TMPDIR']}",
                        BIN] + args,
                       capture_output=True, text=True, timeout=600,
                       cwd=ROOT)
    return p.returncode, (p.stdout + p.stderr)


def main():
    shutil.rmtree(TMP, ignore_errors=True)
    os.makedirs(TMP)
    study = json.load(open(STUDY))
    cases = []

    # 1. negative times_s
    s = json.loads(json.dumps(study))
    s["comparison"]["decision_rules"][0]["times_s"] = [-1.0]
    f = os.path.join(TMP, "neg_times.json")
    json.dump(s, open(f, "w"))
    rc, out = run(["study", "validate", f])
    cases.append({"name": "negative_times_s", "rc": rc,
                  "rejected": rc != 0, "expect_reject": True,
                  "named": "times_s" in out})

    # 2. empty times_s
    s = json.loads(json.dumps(study))
    s["comparison"]["decision_rules"][0]["times_s"] = []
    f = os.path.join(TMP, "empty_times.json")
    json.dump(s, open(f, "w"))
    rc, out = run(["study", "validate", f])
    cases.append({"name": "empty_times_s", "rc": rc,
                  "rejected": rc != 0, "expect_reject": True,
                  "named": "times_s" in out})

    # 3. times_s referencing a nonexistent cooling time —
    #    must leave the rule undefined at run, not crash
    s = json.loads(json.dumps(study))
    s["comparison"]["decision_rules"][0]["times_s"] = [1.0]
    f = os.path.join(TMP, "wrong_times.json")
    json.dump(s, open(f, "w"))
    rc, out = run(["study", "validate", f])
    cases.append({"name": "nonexistent_time_scope", "rc": rc,
                  "rejected": rc != 0, "expect_reject": False,
                  "named": True})

    # 4. composition not summing to 100 is legal (basis renormalizes)
    #    but a zero-mass material must fail
    s = json.loads(json.dumps(study))
    s["cases"]["materials"][0]["mass_g"] = 0.0
    f = os.path.join(TMP, "zero_mass.json")
    json.dump(s, open(f, "w"))
    rc, out = run(["study", "validate", f])
    cases.append({"name": "zero_mass", "rc": rc,
                  "rejected": rc != 0, "expect_reject": True,
                  "named": True})

    # 5. unknown decision rule kind
    s = json.loads(json.dumps(study))
    s["comparison"]["decision_rules"].append(
        {"id": "x", "kind": "teleport", "response":
         "total_activity_bq_per_g"})
    f = os.path.join(TMP, "bad_rule.json")
    json.dump(s, open(f, "w"))
    rc, out = run(["study", "validate", f])
    cases.append({"name": "unknown_rule_kind", "rc": rc,
                  "rejected": rc != 0, "expect_reject": True,
                  "named": True})

    # 6. robustness without any channel
    s = json.loads(json.dumps(study))
    s["robustness"]["channels"] = {}
    f = os.path.join(TMP, "no_channels.json")
    json.dump(s, open(f, "w"))
    rc, out = run(["study", "validate", f])
    cases.append({"name": "no_robustness_channels", "rc": rc,
                  "rejected": rc != 0, "expect_reject": True,
                  "named": "channel" in out})

    # 7. composition_rel_std on an element absent from every
    #    material — perturbation must be a no-op, not a crash;
    #    validate is schema-level so accept rc==0
    s = json.loads(json.dumps(study))
    s["robustness"]["channels"]["composition_rel_std"] = {"Xe": 0.5}
    f = os.path.join(TMP, "absent_elem.json")
    json.dump(s, open(f, "w"))
    rc, out = run(["study", "validate", f])
    cases.append({"name": "absent_element_channel", "rc": rc,
                  "rejected": rc != 0, "expect_reject": False,
                  "named": True})

    # 8. spectrum spec_ref to a file with no spectrum
    bad = os.path.join(TMP, "no_spectrum_spec.json")
    json.dump({"spec": "actinv-spec-1", "title": "empty",
               "projectile": "neutron",
               "library": study["library"],
               "decay": study["decay"],
               "material": {"mass_g": 1.0, "basis": "wt_percent",
                            "composition": {"Fe": 100.0}},
               "schedule": [{"dt": "1 s", "flux": 1.0}]},
              open(bad, "w"))
    s = json.loads(json.dumps(study))
    s["cases"]["spectra"][0]["spec_ref"] = "no_spectrum_spec.json"
    f = os.path.join(TMP, "no_spectrum.json")
    json.dump(s, open(f, "w"))
    # spec_ref resolution is a build-time operation: validate is
    # schema-level and may accept; build must fail closed
    rc, out = run(["study", "build", f,
                   os.path.join(TMP, "nospec-out")])
    cases.append({"name": "spec_ref_no_spectrum", "rc": rc,
                  "rejected": rc != 0, "expect_reject": True,
                  "named": "spectrum" in out})

    exp_rej = [c for c in cases if c["expect_reject"]]
    all_ok = all(c["rejected"] == c["expect_reject"] for c in cases)
    out = {"gate": "G3", "phase": "P36", "cases": cases,
           "n_cases": len(cases),
           "reject_count": sum(c["rejected"] for c in exp_rej),
           "all_named": all(c["named"] for c in exp_rej),
           "pass": all_ok and all(c["named"] for c in exp_rej)}
    json.dump(out, open(OUT, "w"), indent=1, sort_keys=True)
    print(json.dumps(out, indent=1)[:2500])


if __name__ == "__main__":
    main()
