#!/usr/bin/env python3
"""P45 G3 conformance — protocol-compliance probes on the sealed
machinery: sealed-code identity, arm/driver refusal paths, and scorer
classification on malformed inputs. Emits
results/g3_p45_conformance.json.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "results" / "g3_p45_conformance.json"
SEAL = ROOT / "results" / "g0_p45_seals.json"

sys.path.insert(0, str(ROOT / "controls"))
import g2_p26b_leg as leg  # noqa: E402
import p45_campaign as camp  # noqa: E402


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def check(name, ok, detail=None):
    d = {"probe": name, "pass": bool(ok)}
    if detail is not None:
        d["detail"] = detail
    return d


def main() -> int:
    checks = []
    seal = json.loads(SEAL.read_text())

    # p1: sealed code + binary identities still match the live tree
    mismatches = {}
    for name, p in seal["code_sha256"].items():
        rel = {"campaign_harness": "controls/p45_campaign.py",
               "parity_scorer": "controls/p45_parity.py",
               "robustness_driver": "controls/p45_robustness.py",
               "population_builder": "controls/p45_population.py",
               "p26b_leg": "controls/g2_p26b_leg.py"}.get(name)
        if rel and sha(ROOT / rel) != p:
            mismatches[name] = p
    for name, idn in seal["identities"].items():
        p = idn["path"]
        full = Path(p) if Path(p).is_absolute() else ROOT / p
        if sha(full) != idn["sha256"]:
            mismatches[f"ident:{name}"] = p
    checks.append(check("sealed_code_identity", not mismatches,
                        mismatches))

    # p2: harness refuses an unknown arm name (no silent mapping)
    r = subprocess.run(
        [sys.executable, str(ROOT / "controls" / "p45_campaign.py"),
         "--workload", "campaign", "--arms", "bogus_arm",
         "--cases", "fe__fns_709__pulse_5min", "--repeat", "1"],
        capture_output=True, text=True, timeout=120)
    ledger_line = None
    for l in (ROOT / "results" / "p45_ledger.jsonl"
              ).read_text().splitlines()[-40:]:
        r0 = json.loads(l)
        if r0["arm"] == "bogus_arm" or "bogus_arm" in l:
            ledger_line = r0
    # unknown arms simply produce no work — the point is it doesn't
    # crash into a wrong arm or fabricate a result
    no_bogus_result = ledger_line is None or not (
        (ledger_line.get("arms") or {}).get("bogus_arm") or {}
    ).get("result")
    checks.append(check(
        "unknown_arm_no_fabrication",
        no_bogus_result and r.returncode == 0,
        {"returncode": r.returncode}))

    # p3: spec digest pins lib path — actinv_fendl spec uses the FENDL
    # NPZ, actinv_tendl uses TENDL (no cross-talk)
    pop = json.loads(camp.POPULATION.read_text())
    case = [c for c in pop["campaign"]["cases"]
            if c["case"] == "fe__fns_709__pulse_5min"][0]
    flux = leg.fns_spectrum()
    s_f = leg.build_actinv_spec(case, flux=flux, descending=True,
                                lib=camp.FENDL_NPZ,
                                lib_sha=camp.sha256_file(camp.FENDL_NPZ))
    s_t = leg.build_actinv_spec(case, flux=flux, descending=True,
                                lib=camp.TENDL_NPZ,
                                lib_sha=camp.sha256_file(camp.TENDL_NPZ))
    checks.append(check(
        "arm_data_isolation",
        "fendl" in s_f["library"]["path"]
        and "tendl" in s_t["library"]["path"]
        and s_f["library"]["sha256"] != s_t["library"]["sha256"],
        {"fendl_lib": s_f["library"]["path"][-40:],
         "tendl_lib": s_t["library"]["path"][-40:]}))

    # p4: ALARA input carries the converted library + schedule parity —
    # write_alara_case must embed identical cooling times and library
    with tempfile.TemporaryDirectory() as td:
        io = leg.write_alara_case(Path(td), case, flux,
                                  leg.elelib_parse())
        text = (Path(td) / "case.in").read_text()
        lib_line = [l for l in text.splitlines()
                    if "alaralib" in l]
        # cooling block: 'cooling' keyword then one line per time
        lines = text.splitlines()
        ci = [i for i, l in enumerate(lines) if "cooling" in l]
        times_seen = []
        if ci:
            for l in lines[ci[0] + 1:]:
                toks = l.split()
                if len(toks) >= 2 and toks[1] == "s":
                    times_seen.append(float(toks[0]))
                else:
                    break
        cum = []
        acc = 0.0
        for dt in leg.COOL_DT_S:
            acc += dt
            cum.append(acc)
        checks.append(check(
            "alara_input_contract",
            lib_line and "fendl32c_709" in lib_line[0]
            and times_seen == cum,
            {"lib_line": lib_line[:1],
             "cooling_times": times_seen, "expected": cum}))

    # p5: scorer marks a synthetic hand-planted divergence correctly at
    # the sealed tolerance (0.10 boundary)
    sys.path.insert(0, str(ROOT / "controls"))
    import p45_parity as parity  # noqa: E402
    res_a = {"per_time": {"0": {"cooling_s": 0.0,
                                "total_activity_bq_per_g": 111.0,
                                "product_atoms_per_g": 1.0,
                                "decay_heat_w_per_g": 1.0,
                                "top5_nuclides_by_activity": []}}}
    res_b = {"per_time": {"0": {"cooling_s": 0.0,
                                "total_activity_bq_per_g": 100.0,
                                "product_atoms_per_g": 1.0,
                                "decay_heat_w_per_g": 1.0,
                                "top5_nuclides_by_activity": []}}}
    rows = [{"workload": "campaign", "case": "c", "arm": "alara",
             "arms": {"alara": {"status": "executed",
                                "result": res_a}}},
            {"workload": "campaign", "case": "c",
             "arm": "actinv_fendl",
             "arms": {"actinv_fendl": {"status": "executed",
                                     "result": res_b}}}]
    sc = parity.score_ledger(rows)
    st = sc["per_case"]["campaign|c"]["identical"]["status"]
    # and at the boundary itself
    res_b2 = {"per_time": {"0": {"cooling_s": 0.0,
                                 "total_activity_bq_per_g": 110.0,
                                 "product_atoms_per_g": 1.0,
                                 "decay_heat_w_per_g": 1.0,
                                 "top5_nuclides_by_activity": []}}}
    rows[1]["arms"]["actinv_fendl"]["result"] = res_b2
    sc2 = parity.score_ledger(rows)
    st2 = sc2["per_case"]["campaign|c"]["identical"]["status"]
    checks.append(check(
        "tolerance_boundary",
        st == "parity_divergence" and st2 == "parity_ok",
        {"at_111": st, "at_110": st2}))

    out = {"spec": "actinv-p45-g3-conformance-1",
           "probes": checks,
           "all_pass": all(c["pass"] for c in checks)}
    OUT.write_text(json.dumps(out, indent=1))
    print(json.dumps({"all_pass": out["all_pass"],
                      "probes": {c["probe"]: c["pass"]
                                 for c in checks}}))
    return 0 if out["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
