#!/usr/bin/env python3
"""P179 harness (protocols/ACTINV-P179_PROTOCOL.md): P172's identical-data ACTINV arm, rerun on the ENDF/B-VIII.1
709-group library rebuilt after ledger 316's repair of R-matrix-limited capture.

Only the library differs from P172's `actinv_endf8` arm. The ACTINV Python module is the one P172 pinned (f33ea3ed...,
copied to target/p179-bin/libactinv.so in the main checkout and passed as ACTINV_PYTHON_MODULE), and the FNS inputs,
decay files and controls/cb2_fns.py are P172's. P172's TENDL-2025 and OpenMC values are taken from its sealed record.

Modes (each its own process, inside the bounded scope AGENTS.md prescribes):
  actinv        writes results/p179/actinv_endf8_repaired.json (P172's run_actinv_arm with the library redirected)
  library-diff  writes results/p179/library_diff.json: every row of the two libraries' cross-section arrays that differs
  assemble      writes results/p179_endf8_repaired.json: P172's sealed points with actinv_endf8 replaced by the rerun's
                value (P172's kept as actinv_endf8_p172) and each experiment's composition elements

P179_REHEARSAL=1 sends every output under target/p179-rehearsal; nothing it writes is scored or kept.
"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "controls"))
import p172_openmc_fns as p172  # noqa: E402

REHEARSAL = os.environ.get("P179_REHEARSAL") == "1"
OUT_DIR = ROOT / "target" / "p179-rehearsal" if REHEARSAL else ROOT / "results" / "p179"
RECORD = OUT_DIR / "record.json" if REHEARSAL else ROOT / "results" / "p179_endf8_repaired.json"
WORK_TAG = "p179-rehearsal" if REHEARSAL else "p179"
SCHEMA = "actinv-p179-endf8-repaired-1"

REPAIRED_NPZ = p172.DATA / "endfb-viii.1-p178/endfb81_all_709g.npz"
REPAIRED_INDEX = p172.DATA / "endfb-viii.1-p178/endfb81_all_709g_index.json"
P172_RECORD = ROOT / "results/p172_openmc_fns.json"
P172_ARM = ROOT / "results/p172/actinv_endf8.json"
FNS = p172.DATA / "conderc-fns/fns"
ARM_FILE = "actinv_endf8_repaired.json"
DIFF_FILE = "library_diff.json"

# Frozen inputs: name -> (path, SHA-256). P172's library is read only by library-diff.
PINNED = {
    "repaired_library": (REPAIRED_NPZ, "a964a44ef106b692d170ba6085859ca2827ff967b3e9a6ddc60ca8fa9ac92051"),
    "repaired_index": (REPAIRED_INDEX, "b5a4b668324a92bd96e8e90c5feb521a79ca8838e42a517ac080652074b7f41a"),
    "p172_library": (p172.ENDF8_NPZ, p172.PINNED["endf8_library"][1]),
    "p172_index": (p172.ENDF8_INDEX, p172.PINNED["endf8_index"][1]),
    "p172_record": (P172_RECORD, "2b4dd74a4d82db43e3ccc752268d2f44b7a6f7597ddbbec5eb5221a76cd2c23b"),
    "p172_arm": (P172_ARM, "080025915012854f33601bd30ee6e7ce37f6302d5f8de13443b7b0b3bb9278dd"),
    "decay_primary": p172.PINNED["decay_primary"],
    "decay_fallback": p172.PINNED["decay_fallback"],
    "fns_archive": p172.PINNED["fns_archive"],
}
MODULE_SHA256 = p172.ACTINV_MODULE_SHA256  # P172's module, unchanged

# Ledger 316: the 15 R-matrix-limited targets whose capture the repair changed (ZA), and the elements that carry them.
REPAIRED_TARGETS = (14028, 14029, 14030, 17035, 20040, 23051, 26054, 26057, 29063, 29065, 45103, 74182, 74183, 74184,
                    74186)
REPAIRED_ELEMENTS = ("CA", "CL", "CU", "FE", "RH", "SI", "V", "W")


def require_pins(names):
    bad = [name for name in names if p172.sha256_file(PINNED[name][0]) != PINNED[name][1]]
    module = p172.ACTINV_MODULE
    if p172.sha256_file(module) != MODULE_SHA256:
        bad.append(f"module {module}")
    if bad:
        sys.exit(f"p179_endf8_repaired: refusing to run: not the pinned file: {', '.join(bad)}")


def run_actinv(out):
    require_pins(["repaired_library", "repaired_index", "decay_primary", "decay_fallback", "fns_archive"])
    p172.ENDF8_NPZ, p172.ENDF8_INDEX = REPAIRED_NPZ, REPAIRED_INDEX
    p172.WORK_TAG = WORK_TAG
    p172.run_actinv_arm("endf8", out)


def library_diff(out):
    """Rows of `sig` that differ between P172's library and the repaired one, by (target ZA, MT, product ZA, state)."""
    import numpy as np
    require_pins(["repaired_library", "p172_library"])
    old, new = np.load(p172.ENDF8_NPZ), np.load(REPAIRED_NPZ)
    same_layout = (sorted(old.files) == sorted(new.files) and old["rows"].shape == new["rows"].shape
                   and bool((old["rows"] == new["rows"]).all()) and bool((old["bounds"] == new["bounds"]).all()))
    rows = []
    if same_layout:
        for r in np.nonzero((old["sig"] != new["sig"]).any(axis=1))[0]:
            _, mt, product, state, _ = (int(v) for v in old["rows"][r])
            rows.append({"row": int(r), "mt": mt, "product_za": product, "state": state})
    p172.write_atomic(out, {"same_layout": same_layout, "differing_rows": rows,
                            "sig_rows": int(old["sig"].shape[0])})


def elements(material, experiment):
    from harness import fispact_io as fio
    return sorted(fio.read_i(FNS / material / f"TENDL-2017_{experiment}.i")["elements"])


def assemble(record_path):
    sealed = json.loads(P172_RECORD.read_text())
    arm = json.loads((OUT_DIR / ARM_FILE).read_text())
    by = {(r["material"], r["experiment"]): r for r in arm["records"]}
    experiments = []
    for e in sealed["experiments"]:
        rec = by.get((e["material"], e["experiment"]))
        points = []
        for n, p in enumerate(e["points"]):
            point = {key: p.get(key) for key in ("time_s", "measured", "sigma", "actinv_tendl2025", "openmc",
                                                 "openmc_boundary_match")}
            point["actinv_endf8_p172"] = p.get("actinv_endf8")
            if rec is not None and n < len(rec["pairs"]):
                q = rec["pairs"][n]
                point["actinv_endf8"] = q["actinv_tendl2025_uW_g"]  # cb2_fns names its value after its first arm
                point["actinv_endf8_time_s"], point["actinv_endf8_measured"] = q["time_s"], q["measured_uW_g"]
            points.append(point)
        experiments.append({"material": e["material"], "experiment": e["experiment"], "n_points": len(points),
                            "points": points, "actinv_endf8_points": None if rec is None else len(rec["pairs"]),
                            "openmc_status": e.get("openmc_status"),
                            "elements": elements(e["material"], e["experiment"])})
    environment = arm["p172"]["environment"]
    document = {
        "schema": SCHEMA,
        "inputs": {name: {"path": str(path), "sha256": p172.sha256_file(path)} for name, (path, _) in PINNED.items()},
        "environment": {**environment, "module_sha256": arm["p172"]["module_sha256"],
                        "p179_harness_sha256": p172.sha256_file(Path(__file__)),
                        "actinv_version": arm.get("actinv_version")},
        "p172_environment": {key: sealed["environment"].get(key)
                             for key in ("harness_sha256", "cb2_script_sha256", "groups_sha256", "module_sha256")},
        "actinv_record": {"checks": arm["checks"], "pass": arm["pass"], "cb2_exit_code": arm["p172"]["cb2_exit_code"],
                          "sha256": p172.sha256_file(OUT_DIR / ARM_FILE)},
        "library_diff": json.loads((OUT_DIR / DIFF_FILE).read_text()),
        "experiments": experiments}
    p172.write_atomic(record_path, document)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="mode", required=True)
    for name in ("actinv", "library-diff", "assemble"):
        sub.add_parser(name)
    args = parser.parse_args(argv)
    p172.require_bounded(6)
    if args.mode == "actinv":
        run_actinv(OUT_DIR / ARM_FILE)
    elif args.mode == "library-diff":
        library_diff(OUT_DIR / DIFF_FILE)
    else:
        assemble(RECORD)


if __name__ == "__main__":
    main()
