#!/usr/bin/env python3
"""P23 G1 producer: analytic and metamorphic battery for schedule feed/removal.

Independent control: parses the ENDF-6 decay file for half-lives and branch
ratios itself (no ACTINV production, audit or scoring module), builds the small
nuclide networks by hand, and solves each schedule step with the dense augmented
matrix exponential. ACTINV results are compared at 1e-9 relative -- CRAM-16 is
far tighter, so this bound is conservative.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import numpy as np
from scipy.linalg import expm


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results/g1_p23_feed_removal.json"
ACTINV = Path(os.environ.get("ACTINV_BIN", ROOT / "target/release/actinv"))
EXAMPLE = ROOT / "examples/fns_fe_5min.json"
TOL = 1e-9

SYMBOLS = {
    26: "Fe", 27: "Co", 28: "Ni", 25: "Mn", 24: "Cr", 29: "Cu", 23: "V",
}


def name_of(za: int, liso: int = 0) -> str:
    return SYMBOLS[za // 1000] + str(za % 1000) + (f"m{liso}" if liso else "")


def endf_field(line: str, index: int) -> float:
    """One 11-character ENDF field; blank is 0, exponent markers are optional."""
    text = line[11 * index : 11 * index + 11].strip()
    if not text:
        return 0.0
    text = text.replace("D", "E").replace("d", "E")
    mantissa = text
    for pos in range(len(text) - 1, 0, -1):
        if text[pos] in "+-" and text[pos - 1] not in "eE+-":
            mantissa = text[:pos] + "E" + text[pos:]
            break
    return float(mantissa)


def parse_decay(path: Path) -> dict[tuple[int, int], dict]:
    """Minimal MF=8/MT=457 reader: (ZA, LISO) -> {half_life_s, stable, modes}.

    Reads the HEAD record, the half-life LIST and the decay-mode LIST; spectra
    are stepped over by their declared record counts. Enough for every nuclide
    this battery touches.
    """
    nuclides: dict[int, dict] = {}
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    i = 0
    in_457 = False
    while i < len(lines):
        line = lines[i]
        is_457 = len(line) >= 75 and line[70:72].strip() == "8" and line[72:75].strip() == "457"
        # a section HEAD is the first 457 record after a non-457 record; spectra
        # and other sub-records inside a section share the MF/MT tail fields
        if not is_457 or in_457:
            in_457 = is_457
            i += 1
            continue
        in_457 = True
        za = int(round(endf_field(line, 0)))
        liso = int(endf_field(line, 3))
        nst = int(endf_field(line, 4))
        nsp = int(endf_field(line, 5))
        j = i + 1
        t12 = endf_field(lines[j], 0)
        n_energies = int(endf_field(lines[j], 4))
        j += math.ceil(n_energies / 6) + 1
        # mode LIST head: NPL mode fields (at least one 6-field block), N2 = NDK real modes
        ndk = int(endf_field(lines[j], 5))
        n_mode_fields = int(endf_field(lines[j], 4))
        j += 1
        vals: list[float] = []
        while len(vals) < n_mode_fields:
            for k in range(6):
                vals.append(endf_field(lines[j], k))
            j += 1
        modes = [(vals[6 * k], vals[6 * k + 1], vals[6 * k + 4]) for k in range(ndk)]
        nuclides[(za, liso)] = {
            "half_life_s": t12,
            "stable": nst == 1 or t12 == 0.0 or not math.isfinite(t12),
            "modes": modes,
        }
        # resume the line-by-line header scan right after the mode LIST; spectra
        # and any other records simply fail the MF=8/MT=457 check
        i = j
    return nuclides


def lambda_of(entry: dict) -> float:
    if entry["stable"]:
        return 0.0
    return math.log(2.0) / entry["half_life_s"]


def run_actinv(spec: dict, work: Path, env: dict[str, str], name: str) -> dict:
    spec_path = work / f"{name}.json"
    out_path = work / f"{name}.out.json"
    spec_path.write_text(json.dumps(spec, sort_keys=True) + "\n", encoding="utf-8")
    completed = subprocess.run(
        [str(ACTINV), "run", str(spec_path), str(out_path)],
        cwd=ROOT, env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=600, check=False,
    )
    if completed.returncode:
        raise RuntimeError(f"actinv run {name} failed: {completed.stderr[-4000:]}")
    return json.loads(out_path.read_text(encoding="utf-8"))


def close(a: float, b: float, tol: float = TOL) -> bool:
    return bool(abs(a - b) <= tol * max(abs(a), abs(b), 1.0))


RTYP_STEP = {1: (1, 0), 2: (-1, 0), 3: (0, 0), 4: (-2, -4), 5: (0, -1), 7: (-1, -1)}


def decay_daughter(key: tuple[int, int], rtyp: float, rfs: float):
    """(ZA,LISO) parent + ENDF RTYP/RFS -> (ZA,LISO) daughter or None.

    Same published convention as the solver: decimal RTYP is a chain of
    elementary (dZ,dA) steps; an isomer daughter key falls back to the ground
    state when the evaluated file has no such isomer entry.
    """
    digits = [int(c) for c in f"{rtyp:.6f}".rstrip("0").rstrip(".") if c.isdigit()]
    z, a = key[0] // 1000, key[0] % 1000
    for d in digits:
        step = RTYP_STEP.get(d)
        if step is None or d == 6:
            return None
        z, a = z + step[0], a + step[1]
    return (z * 1000 + a, int(round(rfs)))


def daughter_key(decay: dict, parent: tuple[int, int], rtyp: float, rfs: float):
    daughter = decay_daughter(parent, rtyp, rfs)
    if daughter is None:
        return None
    if daughter in decay:
        return daughter
    ground = (daughter[0], 0)
    return ground if ground in decay else None


def dense_solve(decay: dict[tuple[int, int], dict], seeds: list[tuple[int, int]],
                schedule: list[tuple[float, dict[tuple[int, int], float], dict[tuple[int, int], float]]]):
    """(order, per-step y): toy network over the reachable set + a sink state.

    Each step is (dt_s, feed {key: rate}, removal {key: rate}); the inhomogeneous
    feed enters through an augmented column, removal through diagonal loss into
    the final sink row.
    """
    order: dict[tuple[int, int], int] = {}
    frontier = list(seeds)
    while frontier:
        key = frontier.pop()
        if key in order or key not in decay:
            continue
        order[key] = len(order)
        for rtyp, rfs, br in decay[key]["modes"]:
            if br <= 0.0:
                continue
            daughter = daughter_key(decay, key, rtyp, rfs)
            if daughter is not None and daughter not in order:
                frontier.append(daughter)
    n = len(order)
    sink = n
    A = np.zeros((n + 1, n + 1))
    for key, i in order.items():
        lam = lambda_of(decay[key])
        A[i, i] -= lam
        for rtyp, rfs, br in decay[key]["modes"]:
            if br <= 0.0:
                continue
            daughter = daughter_key(decay, key, rtyp, rfs)
            if daughter is not None and daughter in order:
                A[order[daughter], i] += lam * br
    y = np.zeros(n + 1)
    per_step = []
    for dt, feed, removal in schedule:
        As = np.array(A)
        for key, i in order.items():
            extra = removal.get(key, 0.0)
            if extra:
                As[i, i] -= extra
                As[sink, i] += extra
        src = np.zeros(n + 1)
        for key, rate in feed.items():
            src[order[key]] += rate
        aug = np.zeros((n + 2, n + 2))
        aug[: n + 1, : n + 1] = As
        aug[: n + 1, n + 1] = src
        y = (expm(aug * dt) @ np.concatenate([y, [1.0]]))[: n + 1]
        per_step.append(y.copy())
    return order, per_step


def main() -> None:
    specification = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    specification["library"]["path"] = str(ROOT / specification["library"]["path"])
    for role in ("primary", "fallback"):
        specification["decay"][role] = str(ROOT / specification["decay"][role])
    decay = parse_decay(Path(specification["decay"]["primary"]))
    co60, ni60, fe56 = (27060, 0), (28060, 0), (26056, 0)
    for needed in (co60, ni60, fe56):
        if needed not in decay:
            raise RuntimeError(f"decay file lacks {needed}")
    lam_co60 = lambda_of(decay[co60])
    if lam_co60 <= 0.0:
        raise RuntimeError("Co-60 must be radioactive in the decay file")

    env = os.environ.copy()
    checks: dict[str, bool] = {}
    details: dict[str, object] = {}
    preflight = ROOT / "target/preflight-tmp"
    preflight.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(dir=preflight, prefix="p23-g1-") as directory:
        work = Path(directory)
        env["ACTINV_CACHE_DIR"] = str(work / "cache")
        env["RAYON_NUM_THREADS"] = "1"

        def base_spec(title: str) -> dict:
            s = json.loads(json.dumps(specification))
            s["title"] = title
            s["spectrum"]["flux_per_group"] = [0.0] * 709
            s["spectrum"].pop("total", None)
            return s

        # ---- feed stable Fe-56 (tracked-reservoir path) and decaying Co-60
        s_stable, s_decay, t1 = 1.0e6, 1.0e12, 1.0e8
        spec = base_spec("p23-g1 feed")
        spec["schedule"] = [{"dt": f"{t1} s", "flux": 0.0,
                             "feed": {"Co60": s_decay, "Fe56": s_stable}}]
        result = run_actinv(spec, work, env, "case_feed")
        inv = {n["nuclide"]: n["atoms_per_g"] for n in result["steps"][0]["inventory"]}
        expected_co = s_decay / lam_co60 * (1.0 - math.exp(-lam_co60 * t1))
        expected_ni = s_decay * t1 - expected_co
        checks["feed_decaying_matches_analytic"] = close(inv["Co60"], expected_co)
        checks["feed_stable_matches_analytic"] = close(inv["Fe56"], s_stable * t1)
        checks["feed_daughter_matches_analytic"] = close(inv["Ni60"], expected_ni)
        details["case_feed"] = {
            "computed": {k: inv[k] for k in ("Co60", "Ni60", "Fe56")},
            "analytic": {"Co60": expected_co, "Ni60": expected_ni, "Fe56": s_stable * t1},
        }

        # dense augmented-expm cross-check on the same case
        order, per_step = dense_solve(
            decay, [co60, fe56], [(t1, {co60: s_decay, fe56: s_stable}, {})]
        )
        y = per_step[0]
        checks["dense_expm_agrees_feed"] = all(
            close(inv[name_of(z[0], z[1])], y[order[z]]) for z in (co60, ni60, fe56)
        )
        details["dense_feed"] = {
            name_of(z[0], z[1]): y[order[z]] for z in (co60, ni60, fe56)
        }

        # ---- coupled-mode removal of initially stocked Co-60
        n0, r, t = 1.0e15, 1.0e-8, 1.0e7
        spec = base_spec("p23-g1 removal")
        spec["material"] = {"mass_g": 1.0, "basis": "atoms_per_g",
                            "composition": {"Co60": n0}}
        spec["options"] = {"mode": "coupled"}
        spec["schedule"] = [{"dt": f"{t} s", "flux": 0.0, "removal": {"Co60": r}}]
        result = run_actinv(spec, work, env, "case_removal")
        step = result["steps"][0]
        inv = {n["nuclide"]: n["atoms_per_g"] for n in step["inventory"]}
        leff = lam_co60 + r
        exp_co = n0 * math.exp(-leff * t)
        exp_removed = n0 * r / leff * (1.0 - math.exp(-leff * t))
        exp_ni = n0 * lam_co60 / leff * (1.0 - math.exp(-leff * t))
        checks["removal_state_matches"] = close(inv["Co60"], exp_co)
        checks["removal_sink_matches"] = close(step["removed_atoms_per_g"], exp_removed)
        checks["removal_daughter_matches"] = close(inv["Ni60"], exp_ni)
        checks["removal_conserves_atoms"] = close(
            inv["Co60"] + inv["Ni60"] + step["removed_atoms_per_g"], n0, 1e-12
        )
        details["case_removal"] = {
            "computed": {"Co60": inv["Co60"], "Ni60": inv["Ni60"],
                         "removed": step["removed_atoms_per_g"]},
            "analytic": {"Co60": exp_co, "Ni60": exp_ni, "removed": exp_removed},
        }

        # ---- feed, then element removal of Ni plus nuclide removal of Co-60
        spec = base_spec("p23-g1 element removal")
        spec["schedule"] = [
            {"dt": "1e7 s", "flux": 0.0, "feed": {"Co60": 1.0e10}},
            {"dt": "1e7 s", "flux": 0.0, "removal": {"Ni": 2.0e-9, "Co60": 1.0e-9}},
        ]
        result = run_actinv(spec, work, env, "case_element")
        order, per_step = dense_solve(
            decay, [co60],
            [(1e7, {co60: 1.0e10}, {}),
             (1e7, {}, {co60: 1.0e-9, ni60: 2.0e-9})],
        )
        step2 = result["steps"][1]
        inv2 = {n["nuclide"]: n["atoms_per_g"] for n in step2["inventory"]}
        checks["element_removal_dense_agrees"] = (
            close(inv2["Co60"], per_step[1][order[co60]])
            and close(inv2["Ni60"], per_step[1][order[ni60]])
            and close(step2["removed_atoms_per_g"], per_step[1][len(order)])
        )
        details["case_element"] = {
            "computed": {"Co60": inv2["Co60"], "Ni60": inv2["Ni60"],
                         "removed": step2["removed_atoms_per_g"]},
            "dense": {"Co60": per_step[1][order[co60]],
                      "Ni60": per_step[1][order[ni60]],
                      "removed": per_step[1][len(order)]},
        }

        # ---- reservoir exemption: removal of every bulk Fe isotope in trace mode
        spec = base_spec("p23-g1 exempt")
        spec["schedule"] = [
            {"dt": "1e7 s", "flux": 0.0, "feed": {"Co60": 1.0e10}},
            {"dt": "1e7 s", "flux": 0.0, "removal": {"Fe": 1.0e-6, "Co60": 1.0e-8}},
        ]
        result = run_actinv(spec, work, env, "case_exempt")
        fr = result["ledger"]["feed_removal"]
        checks["reservoir_exempt_ledgered"] = sorted(fr["removal_reservoir_exempt"]) == [
            "Fe54", "Fe56", "Fe57", "Fe58"
        ]
        order, per_step = dense_solve(
            decay, [co60],
            [(1e7, {co60: 1.0e10}, {}), (1e7, {}, {co60: 1.0e-8})],
        )
        checks["exempt_sink_matches_dense"] = close(
            result["steps"][1]["removed_atoms_per_g"], per_step[1][len(order)]
        )

        # ---- feed during irradiation: exact fed-atom conservation identity
        spec = json.loads(json.dumps(specification))
        spec["title"] = "p23-g1 irradiated feed"
        feed_rate = 1.0e9
        spec["schedule"] = [{"dt": "300.0 s", "flux": 1.0, "feed": {"Co60": feed_rate}}]
        fed = run_actinv(spec, work, env, "case_irr_feed")
        spec["schedule"] = [{"dt": "300.0 s", "flux": 1.0}]
        unfed = run_actinv(spec, work, env, "case_irr_nofeed")
        fed_total = fed["steps"][0]["total_atoms_per_g"]
        unfed_total = unfed["steps"][0]["total_atoms_per_g"]
        checks["irradiated_feed_conserves_fed_atoms"] = close(
            fed_total - unfed_total, feed_rate * 300.0, 1e-12
        )
        details["case_irradiated"] = {
            "fed_minus_unfed": fed_total - unfed_total,
            "expected": feed_rate * 300.0,
        }

        # ---- metamorphic: splitting a constant-feed step is invariant
        spec = base_spec("p23-g1 split")
        spec["schedule"] = [{"dt": "1e8 s", "flux": 0.0, "feed": {"Co60": 1.0e9}}]
        whole = run_actinv(spec, work, env, "case_whole")
        spec["schedule"] = [{"dt": "5e7 s", "flux": 0.0, "feed": {"Co60": 1.0e9}},
                            {"dt": "5e7 s", "flux": 0.0, "feed": {"Co60": 1.0e9}}]
        split = run_actinv(spec, work, env, "case_split")
        whole_inv = sorted(whole["steps"][-1]["inventory"], key=lambda n: n["nuclide"])
        split_inv = sorted(split["steps"][-1]["inventory"], key=lambda n: n["nuclide"])
        checks["feed_split_invariant"] = (
            [n["nuclide"] for n in whole_inv] == [n["nuclide"] for n in split_inv]
            and all(
                close(a["atoms_per_g"], b["atoms_per_g"])
                for a, b in zip(whole_inv, split_inv)
            )
        )

        # ---- declared-but-empty maps are identical to absent fields
        spec_a = base_spec("p23-g1 identity")
        spec_a["schedule"] = [{"dt": "1e6 s", "flux": 0.0}]
        a = run_actinv(spec_a, work, env, "case_absent")
        spec_b = json.loads(json.dumps(spec_a))
        spec_b["schedule"] = [{"dt": "1e6 s", "flux": 0.0, "feed": {}, "removal": {}}]
        b = run_actinv(spec_b, work, env, "case_empty")
        a.pop("ms")
        b.pop("ms")
        checks["empty_maps_byte_identical"] = a == b
        checks["empty_maps_omit_sink_field"] = "removed_atoms_per_g" not in b["steps"][0]

        # ---- named refusals
        def expect_error(spec: dict, needle: str, name: str) -> bool:
            p = work / f"err_{name}.json"
            p.write_text(json.dumps(spec, sort_keys=True) + "\n", encoding="utf-8")
            completed = subprocess.run(
                [str(ACTINV), "run", str(p), str(work / f"err_{name}.out.json")],
                cwd=ROOT, env=env, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300, check=False,
            )
            return completed.returncode != 0 and needle in (
                completed.stdout + completed.stderr
            )

        bad = base_spec("err")
        bad["schedule"] = [{"dt": "1 s", "flux": 0.0, "feed": {"Fe": 1.0}}]
        checks["feed_element_key_rejected"] = expect_error(
            bad, "must name an explicit nuclide", "feed_element"
        )
        bad["schedule"] = [{"dt": "1 s", "flux": 0.0, "feed": {"Xx99": 1.0}}]
        checks["feed_unknown_nuclide_rejected"] = expect_error(
            bad, "unknown element symbol", "feed_unknown"
        )
        bad["schedule"] = [{"dt": "1 s", "flux": 0.0, "removal": {"Og295": 1.0}}]
        checks["removal_absent_nuclide_rejected"] = expect_error(
            bad, "absent from the decay chain", "removal_none"
        )
        bad["schedule"] = [{"dt": "1 s", "flux": 0.0, "removal": {"Co60": -1.0}}]
        checks["negative_removal_rejected"] = expect_error(
            bad, "finite and nonnegative", "removal_negative"
        )
        # NaN cannot serialize into the problem file; serde_json refuses the
        # bare NaN literal at parse time, before validation runs
        nan_path = work / "err_feed_nan.json"
        nan_path.write_text(
            json.dumps({**base_spec("err"),
                        "schedule": [{"dt": "1 s", "flux": 0.0,
                                      "feed": {"Co60": 1.0}}]},
                       sort_keys=True).replace('"feed": {"Co60": 1.0}',
                                               '"feed": {"Co60": NaN}'),
            encoding="utf-8",
        )
        completed = subprocess.run(
            [str(ACTINV), "run", str(nan_path), str(work / "err_feed_nan.out.json")],
            cwd=ROOT, env=env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300, check=False,
        )
        checks["nan_feed_rejected"] = completed.returncode != 0

    evidence = {
        "schema": "actinv-p23-g1-feed-removal-1",
        "binary": str(ACTINV),
        "tolerance": TOL,
        "checks": checks,
        "details": details,
        "pass": all(checks.values()),
    }
    RESULT.write_text(json.dumps(evidence, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(checks, indent=2, sort_keys=True))
    sys.exit(0 if evidence["pass"] else 1)


if __name__ == "__main__":
    main()
