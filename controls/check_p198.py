#!/usr/bin/env python3
"""P198 checker (protocols/ACTINV-P198_PROTOCOL.md). It reads the activation library, the v1.2.0 sidecar and the run
outputs with its own code (nothing from p198_harness.py or ACTINV). The data readers and the band recomputation are
P185's sealed checker, imported unchanged, with its rule for a row nonzero where its reaction total is zero replaced
by P198's (the row takes no share there).

  G0  The protocol's, harness's and checker's SHA-256 are in protocols/protocol_hash.txt.
  G1  Pins, builds (candidate at the registration commit, baseline at BASE, clean, exit 0), bounded scopes, outputs.
  G2  A and B, baseline against candidate: nominals, sensitivities and bands bit-identical; same excluded blocks
      and uncovered rows.
  G3  Every covered parameter's uncovered_rate_fraction equals the checker's; every band equals its s^T C s.
  G4  Reporting: the new count, largest share, coverage and note; baseline C fails with the old error and
      candidate C runs; Co60 in C is partial at every step.
  G5  Observation: relative standard deviations and largest shares (recorded, not gated).
  G6  CI green on the evidence commit (checked after the push, not here).

  python3 controls/check_p198.py              check results/p198_record.json and target/p198-work, write the verdict
  python3 controls/check_p198.py --no-write   recompute and compare with the committed verdict
  python3 controls/check_p198.py --selftest
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "controls"))
import check_p185 as p185  # noqa: E402

PROTOCOL = ROOT / "protocols" / "ACTINV-P198_PROTOCOL.md"
SEALED = ("protocols/ACTINV-P198_PROTOCOL.md", "controls/p198_harness.py", "controls/check_p198.py",
          "controls/check_p185.py", "controls/p185_harness.py")
RECORD = ROOT / "results" / "p198_record.json"
VERDICT = ROOT / "results" / "p198_verdict.json"
SCHEMA = "actinv-p198-verdict-1"
BASE = "6cd018eebfdd83b2ce28e8fc7a723fbeb9f61472"
PINS = dict(p185.PINS, bracket="32811eb64f86bd8457ca0b68b708268be4721dfd4af78348b1fc81c47d191f25")
RESPONSES = {
    "A": ["Fe53", "Mn54", "Mn56", "Cr51", "Mn57"],
    "B": ["Co58", "Co57", "Ni57", "Co60", "Al26", "Na24", "W185", "W187", "Nb92m1"],
    "C": ["Co60", "Mn54", "Co58", "Cr51", "Fe55", "Mn56"],
}
LIMIT = 0.01
SHARE_TOL = 1e-12
OLD_ERROR = "is nonzero where base row"
MF32_NOTE = "MF=32"
MF40_NOTE = p185.NOTE


def ratio(library, row, key):
    """P198's rule: the row's share of its reaction total, and 0 where the total is zero."""
    b = library.base[key]
    if b == row:
        return np.ones(len(library.bounds) - 1)
    s, t = library.sigma[row], library.sigma[b]
    return np.where(t > 0.0, s / np.where(t > 0.0, t, 1.0), 0.0)


p185.ratio = ratio


# ------------------------------------------------------------------------------------------------- coverage share
def reaches(library, components, target, mt):
    """Per library group: True where (target, MT)'s self-covariance has a positive variance on an overlapping bin."""
    out = np.zeros(len(library.bounds) - 1, dtype=bool)
    for c in components.get(target, []):
        if c["mt"] != mt or c["mt1"] != mt:
            continue
        grid = c["row_grid"]
        n = len(grid) - 1
        if c["kind"] in (0, 1):
            if not np.array_equal(c["row_grid"], c["column_grid"]) or len(c["values"]) != n * n:
                continue
            positive = np.diag(c["values"].reshape(n, n)) > 0.0
        elif c["kind"] in (8, 9):
            positive = c["values"] > 0.0
        else:
            raise ValueError(f"unknown component kind {c['kind']}")
        out |= ((p185.overlap(library.bounds, grid) > 0.0) & positive[None, :]).any(axis=1)
    return out


def share(library, components, phi, row, cache):
    """The share of the row's reaction rate phi*sigma in groups the covariance does not reach."""
    target, mt = int(library.rows[row, 0]), int(library.rows[row, 1])
    if (target, mt) not in cache:
        cache[(target, mt)] = reaches(library, components, target, mt)
    rate = phi * library.sigma[row]
    outside = ~cache[(target, mt)]
    base = library.base.get((target, mt))
    if base is not None and base != row:
        outside |= library.sigma[base] <= 0.0
    total = rate[rate != 0.0].sum()
    return float(rate[outside & (rate != 0.0)].sum() / total) if total > 0.0 else 0.0


def propagated(entry):
    return [s for s in entry["sensitivities"]
            if s["parameter"]["covariance_covered"] and not s["parameter"]["covariance_excluded"]
            and s["value"] != 0.0]


# ---------------------------------------------------------------------------------------------------- the runs
def check_run(library, components, spec, out, problem, candidate):
    """(G3 problems, G4 problems, worst band difference, worst share difference, {response: [(t, rsd, largest)]})."""
    phi = p185.spectrum(spec)
    g3, g4, worst, worst_share, observed, cache = [], [], 0.0, 0.0, {}, {}
    steps = p185.responses(out)
    if len(steps) != len(spec["schedule"]):
        g3.append(f"{len(steps)} steps with a band, {len(spec['schedule'])} in the schedule")
    for i, t, block, whole in steps:
        excluded = {(b["target"], min(b["mt"], b["mt1"]), max(b["mt"], b["mt1"])) for b in whole["excluded_blocks"]}
        for name in RESPONSES[problem]:
            entry = block.get(f"activity:{name}")
            if not isinstance(entry, dict):
                g3.append(f"step {i}: no activity:{name}")
                continue
            sens = {s["parameter"]["library_row"]: s["value"] for s in entry["sensitivities"]
                    if s["parameter"]["covariance_covered"] and s["value"] != 0.0}
            v, scale = p185.variance(library, components, phi, sens, excluded)
            sd = entry["mf33_standard_uncertainty"]
            reported = sd * sd
            diff = abs(v - reported)
            if diff > p185.BAND_TOL * max(abs(v), reported) + p185.BAND_ABS * scale:
                g3.append(f"step {i} {name}: s^T C s {v:.9e}, reported {reported:.9e}")
            if max(abs(v), reported) > 0.0:
                worst = max(worst, diff / max(abs(v), reported))
            largest, counted = 0.0, 0
            for s in entry["sensitivities"]:
                p = s["parameter"]
                field = p.get("uncovered_rate_fraction")
                if not candidate:
                    if field is not None:
                        g4.append(f"step {i} {name}: the baseline reports uncovered_rate_fraction")
                    continue
                if field == 0.0:
                    g4.append(f"step {i} {name}: row {p['library_row']} reports a zero share")
                if not p["covariance_covered"]:
                    if field is not None:
                        g4.append(f"step {i} {name}: uncovered row {p['library_row']} reports a share")
                    continue
                mine = share(library, components, phi, p["library_row"], cache)
                got = field or 0.0
                worst_share = max(worst_share, abs(mine - got))
                if abs(mine - got) > SHARE_TOL:
                    g3.append(f"step {i} {name}: row {p['library_row']} share {got!r}, checker {mine!r}")
                if not p["covariance_excluded"] and s["value"] != 0.0:
                    largest = max(largest, got)
                    counted += got >= LIMIT
            mf33 = next((c for c in entry["channels"] if c["channel"] == "cross_section_mf33"), {})
            note = mf33.get("note") or ""
            if candidate:
                if entry.get("parameters_with_uncovered_energies", 0) != counted:
                    g4.append(f"step {i} {name}: parameters_with_uncovered_energies "
                              f"{entry.get('parameters_with_uncovered_energies')}, counted {counted}")
                if entry.get("largest_uncovered_rate_fraction", 0.0) != largest:
                    g4.append(f"step {i} {name}: largest_uncovered_rate_fraction "
                              f"{entry.get('largest_uncovered_rate_fraction')}, counted {largest}")
                states = p185.from_reaction_total(entry)
                if counted or states:
                    if entry["coverage"] != "partial" or mf33.get("coverage") != "partial":
                        g4.append(f"step {i} {name}: coverage not partial")
                if bool(counted) != (MF32_NOTE in note):
                    g4.append(f"step {i} {name}: MF=32 note {'missing' if counted else 'present'}")
                if bool(states) != (MF40_NOTE in note):
                    g4.append(f"step {i} {name}: MF=40 note does not match the state-resolved count")
            else:
                for key in ("parameters_with_uncovered_energies", "largest_uncovered_rate_fraction"):
                    if key in entry:
                        g4.append(f"step {i} {name}: the baseline reports {key}")
            observed.setdefault(name, []).append((t, entry.get("relative_standard_uncertainty"), largest,
                                                  entry["coverage"]))
    return g3, g4, worst, worst_share, observed


def compare(base, cand, problem):
    """G2 problems for one problem's baseline and candidate outputs."""
    bad = []
    bs, cs = p185.responses(base), p185.responses(cand)
    if [x[:2] for x in bs] != [x[:2] for x in cs]:
        return ["steps differ"]
    for (i, _, bblock, bwhole), (_, _, cblock, cwhole) in zip(bs, cs):
        for key in ("excluded_blocks", "uncovered_library_rows"):
            if bwhole[key] != cwhole[key]:
                bad.append(f"step {i}: {key} differ")
        for name in RESPONSES[problem]:
            b, c = bblock.get(f"activity:{name}"), cblock.get(f"activity:{name}")
            if not (isinstance(b, dict) and isinstance(c, dict)):
                bad.append(f"step {i} {name}: missing")
                continue
            for key in ("nominal", "mf33_standard_uncertainty"):
                if b[key] != c[key]:
                    bad.append(f"step {i} {name}: {key} {b[key]!r} against {c[key]!r}")
            bsens = [(s["parameter"]["library_row"], s["value"]) for s in b["sensitivities"]]
            csens = [(s["parameter"]["library_row"], s["value"]) for s in c["sensitivities"]]
            if bsens != csens:
                bad.append(f"step {i} {name}: sensitivities differ")
    return bad


def check():
    record = json.loads(RECORD.read_text())
    gates = {}
    sealed = (ROOT / "protocols" / "protocol_hash.txt").read_text()
    g0 = {path: p185.sha256(ROOT / path) in sealed for path in SEALED}
    gates["G0"] = {"pass": all(g0.values()), **g0}

    build, observe = record["build"], record["observe"]
    registration = subprocess.run(["git", "log", "--diff-filter=A", "--format=%H", "--",
                                   "protocols/ACTINV-P198_PROTOCOL.md"], capture_output=True, text=True,
                                  cwd=ROOT).stdout.split()
    runs = observe["runs"]
    expected = sorted(f"{b}_{p}" for b in ("baseline", "candidate") for p in RESPONSES)
    must_run = [k for k in expected if k != "baseline_C"]
    g1 = {
        "pins": record["pins"] == PINS and record["base"] == BASE,
        "build_exit": build["candidate"]["exit"] == 0 and build["baseline"]["exit"] == 0,
        "registration_commit": bool(registration) and build["candidate"]["git_head"] == registration[-1]
        and observe["git_head"] == registration[-1],
        "baseline_commit": build["baseline"]["git_head"] == BASE,
        "clean_trees": build["candidate"]["git_dirty"] == "" and build["baseline"]["git_dirty"] == "",
        "bounded": p185.bounded(build["cgroup"]) and p185.bounded(observe["cgroup"]),
        "runs": sorted(runs) == expected
        and all(r["binary_sha256"] == build[r["binary"]]["binary_sha256"] for r in runs.values())
        and all(runs[k]["exit"] == 0 for k in must_run),
        "outputs": all(runs[k]["output_sha256"] and p185.sha256(runs[k]["output"]) == runs[k]["output_sha256"]
                       for k in must_run)
        and all(p185.sha256(r["spec"]) == r["spec_sha256"] for r in runs.values()),
    }
    gates["G1"] = {"pass": all(g1.values()), **g1}
    if not g1["outputs"] or not g1["runs"]:
        return {"schema": SCHEMA, "gates": gates, "verdict": "P198-FAIL", "record_sha256": p185.sha256(RECORD)}

    specs = {k: json.loads(Path(runs[k]["spec"]).read_text()) for k in expected}
    if any(p185.sha256(s["library"]["path"]) != PINS["activation"]
           or p185.sha256(s["uncertainty"]["covariance"]["path"]) != PINS["cov_v120"] for s in specs.values()):
        raise SystemExit("check_p198: a run's library or sidecar is not the pinned file")
    outs = {k: json.loads(Path(runs[k]["output"]).read_text()) for k in must_run}
    wanted = {s["parameter"]["library_row"] for out in outs.values() for _, _, block, _ in p185.responses(out)
              for e in block.values() for s in e.get("sensitivities", [])}
    library = p185.Library(specs["candidate_A"]["library"]["path"], wanted)
    components = p185.sidecar(specs["candidate_A"]["uncertainty"]["covariance"]["path"])

    g2, g3, g4, worst, worst_share, observed = [], [], [], 0.0, 0.0, {}
    for problem in ("A", "B"):
        g2 += [f"{problem}: {x}" for x in compare(outs[f"baseline_{problem}"], outs[f"candidate_{problem}"], problem)]
    for name in must_run:
        problem, candidate = name[-1], name.startswith("candidate")
        p3, p4, w, ws, obs = check_run(library, components, specs[name], outs[name], problem, candidate)
        g3 += [f"{name}: {x}" for x in p3]
        g4 += [f"{name}: {x}" for x in p4]
        worst, worst_share = max(worst, w), max(worst_share, ws)
        observed[name] = obs
    old = runs["baseline_C"]
    if old["exit"] == 0 or OLD_ERROR not in old["stderr"]:
        g4.append(f"baseline_C: exit {old['exit']} without the old error")
    co60 = observed["candidate_C"].get("Co60", [])
    if not co60 or any(row[3] != "partial" or row[2] < LIMIT for row in co60):
        g4.append("candidate_C: Co60 is not partial at every step")
    gates["G2"] = {"pass": not g2, "problems": g2[:20]}
    gates["G3"] = {"pass": not g3, "problems": g3[:20], "worst_relative_band_difference": worst,
                   "worst_share_difference": worst_share}
    gates["G4"] = {"pass": not g4, "problems": g4[:20], "baseline_C_exit": old["exit"],
                   "baseline_C_error": old["stderr"][-300:]}
    gates["G5"] = {"pass": True, "observations": observed}
    gates["G6"] = {"pass": None, "note": "CI green on the evidence commit, checked after the push"}
    verdict = {"schema": SCHEMA, "gates": gates, "record_sha256": p185.sha256(RECORD)}
    passed = all(gates[g]["pass"] for g in ("G0", "G1", "G2", "G3", "G4", "G5"))
    verdict["verdict"] = "P198-PASS (pending G6)" if passed else "P198-FAIL"
    if passed:
        verdict["allowed_sentences"] = sentences(observed, outs["candidate_C"])
    return verdict


def capture_share(out):
    """At the end of irradiation, the share of the Co-59 capture parameter (target Co59, MT 102) with the largest
    absolute sensitivity in Co60's band."""
    _, _, block, _ = p185.responses(out)[0]
    capture = [s for s in propagated(block["activity:Co60"])
               if s["parameter"]["target_nuclide"] == "Co59" and s["parameter"]["MT"] == 102]
    top = max(capture, key=lambda s: abs(s["value"]))
    return top["parameter"].get("uncovered_rate_fraction", 0.0)


def sentences(observed, out_c):
    t, rsd, _, _ = observed["candidate_C"]["Co60"][0]
    largest = capture_share(out_c)
    return [
        "In ACTINV's MF=33 band, a covered parameter that takes 1% or more of its reaction rate at energies where "
        "the covariance is zero (in TENDL-2025, the resonance range, whose uncertainty is MF=32) leaves the band's "
        "coverage partial, and the band reports how many such parameters it has and the largest share.",
        "A state-resolved (MF=10) row that is nonzero where its reaction total is zero no longer stops the run; it "
        "takes no covariance there and that rate is reported outside the band.",
        f"For Learn ACTINV's steel bracket with 0.1 wt% cobalt after 30 days in its moderated field, the "
        f"cross-section part of the relative standard deviation of the Co60 activity is {100 * rsd:.2g}%, and "
        f"{100 * largest:.3g}% of the Co-59 capture rate behind it is at energies TENDL-2025's MF=33 covariance "
        "does not reach.",
    ]


# ---------------------------------------------------------------------------------------------------- selftest
def selftest():
    class Lib:
        pass
    lib = Lib()
    lib.bounds = np.array([1.0, 2.0, 5.0])
    lib.rows = np.array([[0, 102, -1, -1, 0], [0, 107, -1, -1, 0], [0, 107, 17038, 0, 10]], dtype=np.int64)
    lib.sigma = {0: np.array([2.0, 4.0]), 1: np.array([0.0, 4.0]), 2: np.array([1.0, 2.0])}
    lib.base = {(0, 102): 0, (0, 107): 1}
    grid = np.array([1.0, 3.0, 5.0])
    comps = {0: [{"target": 0, "mt": 102, "mt1": 102, "kind": 1, "row_grid": grid, "column_grid": grid,
                  "values": np.array([0.0, 0.0, 0.0, 0.09])},
                 {"target": 0, "mt": 107, "mt1": 107, "kind": 0, "row_grid": np.array([1.0, 5.0]),
                  "column_grid": np.array([1.0, 5.0]), "values": np.array([0.25])}]}
    # the same cases as the runtime's unit tests (covariance.rs)
    cache = {}
    assert abs(share(lib, comps, np.array([1.0, 3.0]), 0, cache) - 2.0 / 14.0) < 1e-15
    assert share(lib, comps, np.array([0.0, 3.0]), 0, {}) == 0.0
    assert share(lib, comps, np.array([1.0, 0.0]), 0, {}) == 1.0
    assert share(lib, comps, np.array([1.0, 3.0]), 1, {}) == 0.0
    assert abs(share(lib, comps, np.array([1.0, 3.0]), 2, {}) - 1.0 / 7.0) < 1e-15
    assert ratio(lib, 2, (0, 107)).tolist() == [0.0, 0.5]
    v, _ = p185.variance(lib, comps, np.array([1.0, 3.0]), {2: 1.0}, set())
    assert abs(v - 0.25 * 0.375 ** 2) < 1e-15, v
    v, _ = p185.variance(lib, comps, np.array([1.0, 3.0]), {1: 1.0}, set())
    assert abs(v - 0.25) < 1e-15, v
    # an LB=8 factor reaches only the bins where it is positive
    comps[0].append({"target": 0, "mt": 16, "mt1": 16, "kind": 8, "row_grid": grid, "column_grid": grid,
                     "values": np.array([0.0, 0.5])})
    assert reaches(lib, comps, 0, 16).tolist() == [False, True]
    assert p185.ratio is ratio
    print("check_p198 selftest: PASS")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        selftest()
        return 0
    verdict = check()
    text = json.dumps(verdict, indent=1, sort_keys=True) + "\n"
    if args.no_write:
        same = VERDICT.is_file() and VERDICT.read_text() == text
        print(f"{verdict['verdict']}; committed verdict {'matches' if same else 'DIFFERS'}")
        return 0 if same else 1
    VERDICT.write_text(text)
    for gate, body in verdict["gates"].items():
        print(gate, body["pass"])
    print(verdict["verdict"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
