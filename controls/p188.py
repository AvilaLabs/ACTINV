#!/usr/bin/env python3
"""P188 (protocols/ACTINV-P188_PROTOCOL.md): the IRDFF-II covariance sidecar rebuilt with IRDFF-II's short-range
(LB=8) variances unscaled, checked as P184 checked its sidecar and sampled as P187 sampled the shipped one.

  python3 controls/p188.py stage | build | observe     P184's harness steps with the LB=8 change (target/p188-work)
  python3 controls/p188.py sample                      P187's harness steps on the new sidecar (target/p188-work/sampling)
  python3 controls/p188.py assemble                    results/p188_record.json, results/p188_samples.json.gz
  python3 controls/p188.py check                       results/p188_verdict.json
  python3 controls/p188.py --selftest

P184's and P187's sealed code (p184_covariance.py, p184_harness.py, check_p184.py, p187_harness.py, check_p187.py) is
imported unchanged. The only change to the method: each IRDFF-II LB=8 block is carried over as IRDFF-II gives it.
ENDF-102 defines LB=8 F as an absolute variance, and ACTINV applies it so (ledger 341). P184 scaled F by r².
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "controls"))
import check_p184 as chk184  # noqa: E402
import check_p187 as chk187  # noqa: E402
import p184_covariance as pc  # noqa: E402
import p184_harness as har184  # noqa: E402
import p187_harness as har187  # noqa: E402

PROTOCOL = ROOT / "protocols" / "ACTINV-P188_PROTOCOL.md"
WORK = ROOT / "target" / "p188-work"
SAMPLING = WORK / "sampling"
RECORD = ROOT / "results" / "p188_record.json"
SAMPLES = ROOT / "results" / "p188_samples.json.gz"
VERDICT = ROOT / "results" / "p188_verdict.json"
SEED = {"A": 188001, "B": 188002}
SEALED = ("protocols/ACTINV-P184_PROTOCOL.md", "controls/p184_covariance.py", "controls/p184_harness.py",
          "controls/check_p184.py", "protocols/ACTINV-P187_PROTOCOL.md", "controls/p187_harness.py",
          "controls/check_p187.py", "controls/p188.py")
_replacement, _expected = pc.replacement, chk184.expected


# ------------------------------------------------------------------------------------------------- the change
def replacement(irdff_lines, tendl_lines, library, za, mt, irdff_section):
    """P184's replacement with IRDFF-II's LB=8 blocks unscaled."""
    out = _replacement(irdff_lines, tendl_lines, library, za, mt, irdff_section)
    out["lb8"] = list(pc.irdff_matrix(irdff_section, mt)[2])
    return out


def expected(irdff_xy, group, tendl_mf3_xy, grid, c_i, short):
    """check_p184's recomputation with IRDFF-II's LB=8 blocks unscaled."""
    c, _ = _expected(irdff_xy, group, tendl_mf3_xy, grid, c_i, short)
    return c, [(energies, f) for energies, f in short]


def registration(protocol_path):
    real_run = subprocess.run

    def run(cmd, *args, **kwargs):
        if cmd[:4] == ["git", "log", "--diff-filter=A", "--format=%H"]:
            cmd = cmd[:-1] + [protocol_path]
        return real_run(cmd, *args, **kwargs)
    return real_run, run


def configure():
    pc.replacement = replacement
    har184.WORK, har184.SIDECAR, har184.RECORD = WORK, WORK / "p184.cov.npz", WORK / "p184_part_record.json"
    har184.SCHEMA = "actinv-p188-sidecar-record-1"
    chk184.expected = expected
    chk184.WORK, chk184.RECORD, chk184.PROTOCOL = WORK, WORK / "p184_part_record.json", PROTOCOL
    har187.WORK, har187.RECORD, har187.SAMPLES = SAMPLING, SAMPLING / "p187_part_record.json", SAMPLES
    har187.SEED, chk187.SEED = dict(SEED), dict(SEED)
    har187.SCHEMA = "actinv-p188-sampling-record-1"
    chk187.RECORD, chk187.PROTOCOL = SAMPLING / "p187_part_record.json", PROTOCOL


def sidecar():
    """The new sidecar and its SHA-256, as the build recorded it."""
    build = json.loads((WORK / "build.json").read_text())
    path = WORK / "p184.cov.npz"
    sha = chk184.sha256(path)
    if build["exit"] != 0 or sha != build["sidecar_sha256"]:
        sys.exit("p188: the sidecar is missing or changed since the build")
    return path, sha


def point_sampling_at(path, sha):
    har187.COV_V120 = path
    har187.PINS = dict(har187.PINS, cov_v120=sha)
    chk187.PINS = dict(chk187.PINS, cov_v120=sha)


# ------------------------------------------------------------------------------------------------------ steps
def sample():
    path, sha = sidecar()
    point_sampling_at(path, sha)
    for step in ("build", "first", "sample", "assemble"):
        code = har187.main([step])
        if code:
            return code
    return 0


def assemble():
    har184.assemble()
    record = {"schema": "actinv-p188-record-1", "seed": SEED,
              "sidecar_part": json.loads((WORK / "p184_part_record.json").read_text()),
              "sampling_part": json.loads((SAMPLING / "p187_part_record.json").read_text())}
    RECORD.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    print(f"record -> {RECORD.relative_to(ROOT)}")


def check():
    record = json.loads(RECORD.read_text())
    for part, path in (("sidecar_part", WORK / "p184_part_record.json"),
                       ("sampling_part", SAMPLING / "p187_part_record.json")):
        if json.loads(path.read_text()) != record[part]:
            sys.exit(f"p188: {path.name} differs from the committed record")
    path, sha = sidecar()
    point_sampling_at(path, sha)
    real_run, run = registration("protocols/ACTINV-P188_PROTOCOL.md")
    subprocess.run = run
    try:
        v184 = chk184.check()
        v187 = chk187.check()
    finally:
        subprocess.run = real_run
    gates = {}
    sealed_text = (ROOT / "protocols" / "protocol_hash.txt").read_text()
    sealed = {p: chk184.sha256(ROOT / p) in sealed_text for p in SEALED}
    gates["G0"] = {"pass": v184["gates"]["G0"]["pass"] and all(sealed.values()),
                   "sha256": v184["gates"]["G0"]["sha256"], "sealed": sealed}
    for g in ("G1", "G2", "G3", "G4", "G5"):
        gates[g] = v184["gates"][g]
    gates["G6"] = {"pass": all(v187["gates"][g]["pass"] for g in ("G1", "G2", "G3")),
                   "sampling": {g: v187["gates"][g] for g in ("G1", "G2", "G3")},
                   "sidecar_sha256": sha, "rows": v187.get("rows")}
    gates["G7"] = {"pass": None, "note": "CI green on the evidence commit, checked after the push"}
    passed = all(gates[g]["pass"] for g in ("G0", "G1", "G2", "G3", "G4", "G5", "G6"))
    verdict = {"schema": "actinv-p188-verdict-1", "gates": gates, "record_sha256": chk184.sha256(RECORD),
               "replaced_reactions": v184["replaced_reactions"], "target_nuclides": v184["target_nuclides"],
               "verdict": "P188-PASS (pending G7)" if passed else "P188-FAIL"}
    if passed:
        verdict["allowed_sentences"] = sentences(v184, v187)
    return verdict


def sentences(v184, v187):
    n, m = v184["replaced_reactions"], v184["target_nuclides"]
    out = [f"In ACTINV's P188 sidecar, {n} TENDL-2025 reactions on {m} target nuclides take IRDFF-II's covariance, "
           "expressed about TENDL-2025's cross section and including the difference between the two evaluations, "
           "with IRDFF-II's short-range variances as IRDFF-II gives them; every other covariance is TENDL-2025's, "
           "unchanged."]
    for sentence in v184.get("allowed_sentences", [])[1:]:
        out.append(sentence.replace("P184's", "P188's"))
    rows = [r for r in v187["rows"].values() if r["gated"]]
    lo, hi = min(r["ratio"] for r in rows), max(r["ratio"] for r in rows)
    out.append("With P188's sidecar, on two FNS-spectrum point problems (iron, and a nickel, cobalt, aluminium, "
               "tungsten and niobium mixture), ACTINV's first-order activity band agreed with the spread of 200 "
               f"sampled solves within the registered limit on all {len(rows)} tested (nuclide, time) pairs; the "
               f"ratio of first-order to sampled variance ranged from {lo:.3g} to {hi:.3g}.")
    return out


def selftest():
    import numpy as np
    configure()
    assert pc.replacement is replacement and chk184.expected is expected
    grid = np.array([1.0, 2.0, 3.0])
    c_i = np.array([[0.01, 0.002], [0.002, 0.04]])
    same = (np.array([1.0, 3.0]), np.array([1.0, 1.0]))
    double = (np.array([0.5, 1.5, 2.5, 4.0]), np.array([2.0, 2.0, 2.0]))
    short = [(np.array([1.0, 1.5, 3.0]), np.array([0.1, 0.2]))]
    c, blocks = expected(same, double, same, grid, c_i, short)
    c0, scaled = _expected(same, double, same, grid, c_i, short)
    assert np.array_equal(c, c0), "the LB=5 part is P184's"
    assert np.allclose(scaled[0][1], [0.025, 0.05]) and np.array_equal(blocks[0][1], [0.1, 0.2]), "LB=8 unscaled"
    section = {"mtl": 0, "subs": [{"mt1": 102, "mat1": 0, "blocks": [
        {"lb": 5, "lt": 1, "ne": 3, "values": [1.0, 2.0, 3.0, 0.01, 0.002, 0.04]},
        {"lb": 8, "lt": 0, "ne": 3, "values": [1.0, 0.1, 1.5, 0.2, 3.0, 0.0]}]}]}
    raw = pc.irdff_matrix(section, 102)[2]
    assert np.array_equal(raw[0][1], [0.1, 0.2])
    assert har184.WORK == WORK and har187.WORK == SAMPLING and har187.SEED == SEED == chk187.SEED
    chk184.selftest.__globals__["expected"] = _expected  # P184's selftest checks P184's own formula
    try:
        chk184.selftest()
    finally:
        chk184.selftest.__globals__["expected"] = expected
    chk187.selftest()
    print("p188 selftest: PASS")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["--selftest"]:
        selftest()
        return 0
    configure()
    if argv and argv[0] in ("stage", "build", "observe"):
        return har184.main(argv[:1]) or 0
    if argv and argv[0] == "sample":
        return sample()
    if argv and argv[0] == "assemble":
        assemble()
        return 0
    if argv and argv[0] == "check":
        verdict = check()
        VERDICT.write_text(json.dumps(verdict, indent=1, sort_keys=True) + "\n")
        for gate, body in verdict["gates"].items():
            print(gate, body["pass"])
        print(verdict["verdict"])
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
