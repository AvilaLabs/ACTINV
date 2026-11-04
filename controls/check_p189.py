#!/usr/bin/env python3
"""P189 checker (protocols/ACTINV-P189_PROTOCOL.md). It reads results/p189_record.json and the flux file's footer with
its own code (nothing from p189_harness.py).

  G0  The protocol, the harness and this checker are sealed in protocols/protocol_hash.txt.
  G1  Provenance: run from the registration commit on a clean tree, one binary, 6 GiB / no swap / 4 CPUs / 128 tasks.
  G2  Completeness: exit 0, 1,000,000 cell records, the footer's count, every ordinal exactly once.
  G3  Memory: the run's peak is at most 6 GiB (the scope enforces it; the value is recorded).
  G4  All 100 sampled cells equal their stand-alone results exactly.
  G5  Flux conservation: the mesh footer's source and destination sums equal the flux file's sum to 1e-12 relative,
      and no rebin closure is above zero.
  G6  CI green on the evidence commit (checked after the push, not here).

  python3 controls/check_p189.py              check results/p189_record.json, write results/p189_verdict.json
  python3 controls/check_p189.py --selftest
"""
import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROTOCOL = "protocols/ACTINV-P189_PROTOCOL.md"
SEALED = (PROTOCOL, "controls/p189_harness.py", "controls/check_p189.py")
RECORD = ROOT / "results" / "p189_record.json"
VERDICT = ROOT / "results" / "p189_verdict.json"
SCHEMA = "actinv-p189-verdict-1"
CELLS = 1_000_000
SAMPLES = 100
GIB = 1 << 30
SUM_TOLERANCE = 1e-12
COARSE_SHA256 = "88315fcdd77cf6ddc222954eb5dff52e7351e9a6f9d9d9a5bba469576c10a8bf"  # the protocol's pin
CGROUP = {"memory_max": str(6 * GIB), "memory_swap_max": "0", "cpu_max": "400000 100000", "pids_max": "128"}
SENTENCE = ("On one laptop (Intel i3-N305, 4 cores, 6 GiB memory limit), ACTINV's activation step solved a "
            "1,000,000-element mesh (four materials, 709 groups, a distinct flux in every element, interpolated "
            "from an OpenMC tally) in {hours:.1f} h with a peak memory of {gib:.2f} GiB; 100 randomly chosen "
            "elements reproduced their stand-alone results exactly.")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def relative_difference(a, b):
    return abs(a - b) / max(abs(a), abs(b), sys.float_info.min)


def flux_footer(path):
    """The last line of an actinv-flux-1 file, parsed."""
    with open(path, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - 4096))
        return json.loads(f.read().splitlines()[-1])


def attempts(record):
    return record["run"]["attempts"]


# ------------------------------------------------------------------------------------------------------- gates
def gate_g0(sealed_text, digests):
    sealed = {p: d in sealed_text for p, d in digests.items()}
    return {"pass": all(sealed.values()), "sealed": sealed, "sha256": digests.get(PROTOCOL)}


def gate_g1(record, registration, digests):
    runs = attempts(record)
    cgroups = [a.get("cgroup") or {} for a in runs]
    binary = record["inputs"]["binary"]["sha256"]
    checks = {
        "not_rehearsal": record.get("rehearsal") is False and record.get("fine_dim") == 100,
        "files_sealed": record.get("protocol_sha256") == digests[PROTOCOL]
        and record.get("harness_sha256") == digests["controls/p189_harness.py"]
        and record.get("checker_sha256") == digests["controls/check_p189.py"],
        "registration_commit": bool(registration) and bool(runs)
        and all(a.get("git_head") == registration for a in runs),
        "clean_tree": bool(runs) and all(a.get("git_dirty") == "" for a in runs),
        "binary": bool(binary) and all(a.get("binary_sha256") == binary for a in runs)
        and record["sample"].get("binary_sha256") == binary,
        "cgroup": bool(cgroups) and all(all(c.get(k) == v for k, v in CGROUP.items()) for c in cgroups),
        "coarse_pin": (record["inputs"].get("coarse") or {}).get("sha256") == COARSE_SHA256,
    }
    return {"pass": all(checks.values()), "registration": registration, **checks}


def gate_g2(record):
    scan, runs = record["mesh_scan"], attempts(record)
    footer, header = scan.get("footer") or {}, scan.get("header") or {}
    checks = {
        "exit_code_0": bool(runs) and runs[-1].get("exit_code") == 0 and not runs[-1].get("timed_out"),
        "cell_records": scan.get("cell_records") == CELLS,
        "footer_count": footer.get("cell_count") == CELLS and header.get("cell_count") == CELLS,
        "ordinals_exactly_once": scan.get("ordinals_exactly_once") is True,
    }
    return {"pass": all(checks.values()), "cell_records": scan.get("cell_records"), **checks}


def peak_bytes(record):
    peaks = [a.get("memory_peak_bytes") for a in attempts(record)]
    return max(peaks) if peaks and all(isinstance(p, int) for p in peaks) else None


def gate_g3(record):
    peak = peak_bytes(record)
    return {"pass": peak is not None and peak <= 6 * GIB, "memory_peak_bytes": peak, "limit_bytes": 6 * GIB}


def gate_g4(record):
    rows = record["sample"].get("rows") or []
    unequal = [r["ordinal"] for r in rows if r.get("equal") is not True]
    return {"pass": len(rows) == SAMPLES and len({r["ordinal"] for r in rows}) == SAMPLES and not unequal,
            "sampled": len(rows), "unequal_ordinals": unequal,
            "materials": sorted({str(r.get("material")) for r in rows})}


def gate_g5(record, flux_sum):
    footer = (record["mesh_scan"].get("footer") or {})
    try:
        source = relative_difference(footer["source_flux_sum_over_cells"], flux_sum)
        destination = relative_difference(footer["destination_flux_sum_over_cells"], flux_sum)
        closure = footer["max_rebin_relative_closure"]
    except (KeyError, TypeError):
        return {"pass": False, "note": "the mesh footer lacks a flux sum or the closure"}
    return {"pass": source <= SUM_TOLERANCE and destination <= SUM_TOLERANCE and closure == 0,
            "flux_file_sum": flux_sum, "source_relative_difference": source,
            "destination_relative_difference": destination, "max_rebin_relative_closure": closure}


def judge(record, sealed_text, digests, registration, flux_sum):
    gates = {"G0": gate_g0(sealed_text, digests), "G1": gate_g1(record, registration, digests),
             "G2": gate_g2(record), "G3": gate_g3(record), "G4": gate_g4(record), "G5": gate_g5(record, flux_sum),
             "G6": {"pass": None, "note": "CI green on the evidence commit, checked after the push"}}
    passed = all(gates[g]["pass"] for g in ("G0", "G1", "G2", "G3", "G4", "G5"))
    verdict = {"schema": SCHEMA, "gates": gates, "verdict": "P189-PASS (pending G6)" if passed else "P189-FAIL"}
    if passed:
        hours = sum(a["wall_s"] for a in attempts(record)) / 3600.0
        verdict["allowed_sentence"] = SENTENCE.format(hours=hours, gib=peak_bytes(record) / GIB)
    return verdict


def check():
    record = json.loads(RECORD.read_text())
    digests = {p: sha256(ROOT / p) for p in SEALED}
    registration = subprocess.run(["git", "log", "--diff-filter=A", "--format=%H", "--", PROTOCOL],
                                  capture_output=True, text=True, cwd=ROOT).stdout.split()
    flux_path = ROOT / record["inputs"]["flux"]["path"]
    flux_sum = flux_footer(flux_path)["flux_sum_over_cells"] if flux_path.is_file() \
        else record["inputs"]["fine"]["flux"]["flux_sum_over_cells"]
    verdict = judge(record, (ROOT / "protocols" / "protocol_hash.txt").read_text(), digests,
                    registration[-1] if registration else None, flux_sum)
    verdict["record_sha256"] = sha256(RECORD)
    return verdict


# ---------------------------------------------------------------------------------------------------- selftest
def synthetic():
    binary = "b" * 64
    digests = {PROTOCOL: "1" * 64, "controls/p189_harness.py": "2" * 64, "controls/check_p189.py": "3" * 64}
    attempt = {"git_head": "c0ffee", "git_dirty": "", "binary_sha256": binary, "cgroup": dict(CGROUP),
               "exit_code": 0, "timed_out": False, "wall_s": 7200.0, "memory_peak_bytes": 5 * GIB}
    record = {
        "rehearsal": False, "fine_dim": 100, "protocol_sha256": digests[PROTOCOL],
        "harness_sha256": digests["controls/p189_harness.py"], "checker_sha256": digests["controls/check_p189.py"],
        "inputs": {"binary": {"sha256": binary}, "coarse": {"sha256": COARSE_SHA256}},
        "run": {"attempts": [attempt]},
        "mesh_scan": {"header": {"cell_count": CELLS}, "cell_records": CELLS, "ordinals_exactly_once": True,
                      "footer": {"cell_count": CELLS, "source_flux_sum_over_cells": 1.0e12,
                                 "destination_flux_sum_over_cells": 1.0e12, "max_rebin_relative_closure": 0.0}},
        "sample": {"binary_sha256": binary,
                   "rows": [{"ordinal": i, "material": "SS316", "equal": True} for i in range(SAMPLES)]}}
    return record, digests


def selftest():
    import copy

    record, digests = synthetic()
    sealed = " ".join(digests.values())

    def run(mutate=None, sealed_text=sealed, registration="c0ffee", flux_sum=1.0e12):
        rec = copy.deepcopy(record)
        if mutate:
            mutate(rec)
        return judge(rec, sealed_text, digests, registration, flux_sum)

    ok = run()
    assert ok["verdict"] == "P189-PASS (pending G6)" and ok["gates"]["G6"]["pass"] is None
    assert "in 2.0 h with a peak memory of 5.00 GiB" in ok["allowed_sentence"]

    def fails(gate, **kwargs):
        v = run(**kwargs)
        assert v["verdict"] == "P189-FAIL" and v["gates"][gate]["pass"] is False, (gate, v["gates"][gate])
        assert "allowed_sentence" not in v

    fails("G0", sealed_text=sealed.replace("2" * 64, ""))
    fails("G1", registration="deadbeef")
    fails("G1", registration=None)
    fails("G1", mutate=lambda r: r["run"]["attempts"][0].update(git_dirty=" M controls/x.py"))
    fails("G1", mutate=lambda r: r["run"]["attempts"][0].update(binary_sha256="d" * 64))
    fails("G1", mutate=lambda r: r["sample"].update(binary_sha256="d" * 64))
    fails("G1", mutate=lambda r: r["run"]["attempts"][0]["cgroup"].update(memory_max="max"))
    fails("G1", mutate=lambda r: r["run"]["attempts"][0]["cgroup"].update(memory_swap_max="max"))
    fails("G1", mutate=lambda r: r["run"]["attempts"][0]["cgroup"].update(cpu_max="800000 100000"))
    fails("G1", mutate=lambda r: r["run"]["attempts"][0]["cgroup"].update(pids_max="max"))
    fails("G1", mutate=lambda r: r.update(rehearsal=True, fine_dim=20))
    fails("G1", mutate=lambda r: r.update(harness_sha256="9" * 64))
    fails("G1", mutate=lambda r: r["inputs"]["coarse"].update(sha256="8" * 64))
    fails("G2", mutate=lambda r: r["run"]["attempts"][0].update(exit_code=137))
    fails("G2", mutate=lambda r: r["mesh_scan"].update(cell_records=CELLS - 1))
    fails("G2", mutate=lambda r: r["mesh_scan"]["footer"].update(cell_count=CELLS - 1))
    fails("G2", mutate=lambda r: r["mesh_scan"].update(ordinals_exactly_once=False))
    fails("G3", mutate=lambda r: r["run"]["attempts"][0].update(memory_peak_bytes=6 * GIB + 1))
    fails("G3", mutate=lambda r: r["run"]["attempts"][0].update(memory_peak_bytes=None))
    fails("G4", mutate=lambda r: r["sample"]["rows"][7].update(equal=False))
    fails("G4", mutate=lambda r: r["sample"].update(rows=r["sample"]["rows"][:99]))
    fails("G5", flux_sum=1.0e12 * (1.0 + 1e-9))
    fails("G5", mutate=lambda r: r["mesh_scan"]["footer"].update(max_rebin_relative_closure=1e-16))
    fails("G5", mutate=lambda r: r["mesh_scan"]["footer"].pop("destination_flux_sum_over_cells"))
    assert run(flux_sum=1.0e12 * (1.0 + 5e-13))["gates"]["G5"]["pass"], "within 1e-12 passes"
    two = run(mutate=lambda r: r["run"]["attempts"].append(
        {**r["run"]["attempts"][0], "wall_s": 3600.0, "memory_peak_bytes": 4 * GIB}))
    assert two["verdict"].startswith("P189-PASS") and "in 3.0 h with a peak memory of 5.00 GiB" in two["allowed_sentence"]
    assert relative_difference(0.0, 0.0) == 0.0
    print("check_p189 selftest: PASS")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        selftest()
        return 0
    verdict = check()
    VERDICT.write_text(json.dumps(verdict, indent=1, sort_keys=True) + "\n")
    for gate, body in verdict["gates"].items():
        print(gate, body["pass"])
    print(verdict["verdict"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
