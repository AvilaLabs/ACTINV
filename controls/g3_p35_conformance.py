#!/usr/bin/env python3
"""P35 G3 conformance: the four frozen negative controls exercised
against the real checker logic with tampered environments."""
import copy
import importlib.util
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(ROOT, "results")
OUT = os.path.join(RES, "g3_p35_conformance.json")

_spec = importlib.util.spec_from_file_location(
    "g2c", os.path.join(ROOT, "controls", "check_g2_p35.py"))
_g2 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_g2)


def run_check(ev, res_dir=None, g1=None):
    """Run the real check() with patched globals."""
    old_res, old_g1 = _g2.RES, _g2.G1
    try:
        if res_dir:
            _g2.RES = res_dir
        if g1 is not None:
            _g2.G1 = g1
        fs = []
        try:
            _g2.check(ev, fs)
        except Exception:
            fs = ["crashed"]
        return fs
    finally:
        _g2.RES, _g2.G1 = old_res, old_g1


def main():
    ev = json.load(open(os.path.join(RES, "g2_p35_matrix.json")))
    probes = {}

    # --- upgraded_verdict: verdict file claims P30-PASS ---------------
    with tempfile.TemporaryDirectory(
            dir=os.path.join(ROOT, "target", "preflight-tmp")) as td:
        # mirror the results tree the check reads
        for f in os.listdir(RES):
            if f.startswith(("verdict_p", "g0_p35_seals")):
                shutil.copy(os.path.join(RES, f), td)
        v = json.load(open(os.path.join(td, "verdict_p30.json")))
        v["verdict"] = "P30-PASS"
        json.dump(v, open(os.path.join(td, "verdict_p30.json"), "w"))
        fs = run_check(ev, res_dir=td)
        probes["upgraded_verdict"] = {
            "rejected": bool(fs), "failures": fs}

    # --- omitted_family: matrix without spatial_handoff ---------------
    v = copy.deepcopy(ev)
    v["matrix"].pop("spatial_handoff")
    fs = run_check(v)
    probes["omitted_family"] = {"rejected": bool(fs), "failures": fs}

    # --- forged_reproduction: second run claims non-matching digests --
    g1 = json.load(open(os.path.join(RES, "g1_p35_battery.json")))
    g1f = copy.deepcopy(g1)
    g1f["reproduction"]["run_2"]["digests"]["fe__fns_709__pulse_5min"] \
        = "0" * 64
    g1f["reproduction"]["identical"] = False
    fs = run_check(ev, g1=g1f)
    probes["forged_reproduction"] = {
        "rejected": bool(fs), "failures": fs}

    # --- unsupported_claim: release asserts a blocked capability ------
    v = copy.deepcopy(ev)
    v["release_recommendation"]["ship"].append(
        "spatial source handoff to R2S chains")
    fs = run_check(v)
    probes["unsupported_claim"] = {"rejected": bool(fs), "failures": fs}

    all_rej = all(p["rejected"] for p in probes.values())
    json.dump({"gate": "G3", "phase": "P35", "probes": probes,
               "all_rejected": all_rej},
              open(OUT, "w"), indent=2, sort_keys=True)
    print(json.dumps({k: p["rejected"] for k, p in probes.items()},
                     indent=1))


if __name__ == "__main__":
    main()
