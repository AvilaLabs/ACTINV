#!/usr/bin/env python3
"""P75 runner: linear-response falsification of the activation operator.

Builds the frozen case population (protocols/ACTINV-P75_PROTOCOL.md), runs each case through the
release binary one at a time, extracts the compared quantities, deletes the raw result, and appends
one JSON line per run to a checkpoint file. Re-running skips completed run ids, so an interrupted
execution resumes. Verdicts are derived only by controls/check_p75.py.

    python3 controls/p75_linear_response.py build   # write specs + case manifest
    python3 controls/p75_linear_response.py run [--only ARM] [--limit N]
    python3 controls/p75_linear_response.py profile  # time the heaviest unit, no comparison
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "target" / "p75"
SPECS = WORK / "specs"
RAW = WORK / "raw"
CHECKPOINT = WORK / "runs.jsonl"
MANIFEST = WORK / "cases.json"
BIN = Path(os.environ.get("ACTINV_BIN", ROOT / "target" / "release" / "actinv"))

LIB = ROOT / "actinv-data/v1.1.0/activation/tendl-2025-neutron-709g.npz"
LIB_SHA = "ec4c72bf598dc8ad3d533d9cfafdcf493e2d1f949a3e4db6251495659b68cc44"
DECAY_PRIMARY = ROOT / "actinv-data/v1.1.0/decay/endf-b-viii-0_decay.dat"
DECAY_FALLBACK = ROOT / "actinv-data/v1.1.0/decay/jeff-3-3_decay.dat"
FNS_EXAMPLE = ROOT / "examples/fns_fe_5min.json"

YEAR_S = 3.15576e7
DAY_S = 86400.0
# cumulative cooling times after shutdown (s): 1 h, 1 d, 1e6 s, 30 d, 1 y, 10 y, 100 y
COOLING_CUMULATIVE_S = [3600.0, DAY_S, 1.0e6, 30 * DAY_S, YEAR_S, 10 * YEAR_S, 100 * YEAR_S]
AMPLITUDES = [1.0e10, 1.0e12, 1.0e13, 1.0e14, 1.0e15]
KT_EV = 0.0253

# Representative compositions (wt%). Test materials, not certified specifications.
SS316LN = {"Cr": 17.5, "Ni": 12.25, "Mo": 2.5, "Mn": 1.8, "Si": 0.5, "N": 0.07, "C": 0.03, "Cu": 0.3,
           "Co": 0.05, "Nb": 0.01, "Ta": 0.01, "B": 0.001, "P": 0.025, "S": 0.01}
EUROFER97 = {"Cr": 9.0, "W": 1.1, "Mn": 0.4, "V": 0.2, "Ta": 0.12, "C": 0.11, "N": 0.03, "Si": 0.05,
             "Nb": 0.001, "Mo": 0.005, "Ni": 0.005, "Cu": 0.005, "Co": 0.005, "Al": 0.01, "Ti": 0.01,
             "P": 0.005, "S": 0.005, "B": 0.001}
CONCRETE = {"H": 1.0, "C": 0.1, "O": 52.9107, "Na": 1.6, "Mg": 0.2, "Al": 3.3872, "Si": 33.7021,
            "K": 1.3, "Ca": 4.4, "Fe": 1.4, "Co": 0.001, "Eu": 0.0001}


def _balance_fe(d: dict) -> dict:
    out = dict(d)
    out["Fe"] = round(100.0 - sum(d.values()), 6)
    return out


MATERIALS = {
    "fe": {"Fe": 100.0},
    "w": {"W": 100.0},
    "cu": {"Cu": 100.0},
    "co": {"Co": 100.0},
    "ss316ln": _balance_fe(SS316LN),
    "eurofer97": _balance_fe(EUROFER97),
    "concrete": CONCRETE,
}
MIXTURES = ("ss316ln", "eurofer97", "concrete")
MIX_WEIGHTS = {"fns": 0.2, "flat": 0.5, "maxwell": 0.3}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def group_bounds() -> np.ndarray:
    with zipfile.ZipFile(LIB) as z:
        b = np.load(io.BytesIO(z.read("bounds.npy")))
    assert b.shape == (710,) and np.all(np.diff(b) > 0)
    return b


def spectra() -> dict[str, list[float]]:
    """Unit-total 709-group shapes, ascending energy."""
    b = group_bounds()
    lo, hi = b[:-1], b[1:]
    ex = json.loads(FNS_EXAMPLE.read_text())["spectrum"]
    fns = np.array(ex["flux_per_group"], dtype=float)
    if ex.get("descending"):
        fns = fns[::-1]
    fns = fns / fns.sum()
    # flat per unit lethargy between 1 eV and 1 MeV (clipped per group)
    a, c = np.clip(lo, 1.0, 1.0e6), np.clip(hi, 1.0, 1.0e6)
    flat = np.log(c / a)
    flat = flat / flat.sum()
    # Maxwellian flux E/kT^2 exp(-E/kT): group integral of -(1 + E/kT) exp(-E/kT)
    def prim(e):
        x = e / KT_EV
        return -(1.0 + x) * np.exp(-x)
    maxw = prim(hi) - prim(lo)
    maxw = np.where(maxw > 0, maxw, 0.0)
    maxw = maxw / maxw.sum()
    mix = MIX_WEIGHTS["fns"] * fns + MIX_WEIGHTS["flat"] * flat + MIX_WEIGHTS["maxwell"] * maxw
    return {k: [float(x) for x in v] for k, v in
            {"fns": fns, "flat": flat, "maxwell": maxw, "mix": mix}.items()}


def cooling_steps() -> list[dict]:
    steps, prev = [], 0.0
    for t in COOLING_CUMULATIVE_S:
        steps.append({"dt": f"{t - prev!r} s", "flux": 0.0})
        prev = t
    return steps


def schedules() -> dict[str, dict]:
    s = {
        "s1_1y": [{"dt": f"{YEAR_S!r} s", "flux": 1.0}],
        "s2_5y": [{"dt": f"{5 * YEAR_S!r} s", "flux": 1.0}],
        "s3_pulsed": [],
    }
    for _ in range(5):
        s["s3_pulsed"] += [{"dt": "400.0 s", "flux": 1.0}, {"dt": "1400.0 s", "flux": 0.0}]
    out = {}
    for name, irr in s.items():
        # compared steps (0-based result indices): the last flux-on step, then each cooling step
        last_on = max(i for i, st in enumerate(irr) if st["flux"] > 0)
        steps = irr + cooling_steps()
        compared = [last_on] + list(range(len(irr), len(steps)))
        out[name] = {"steps": steps, "compared": compared}
    return out


def spec(title, composition, shape, total, sched_steps, mode, prune, zero_flux=False, photons=False):
    steps = [dict(st) for st in sched_steps]
    if zero_flux:
        for st in steps:
            st["flux"] = 0.0
    return {
        "spec": "actinv-spec-1",
        "title": title,
        "projectile": "neutron",
        "library": {"path": str(LIB), "sha256": LIB_SHA},
        "decay": {"primary": str(DECAY_PRIMARY), "fallback": str(DECAY_FALLBACK)},
        "material": {"mass_g": 1.0, "basis": "wt_percent", "composition": composition},
        "spectrum": {"structure": "fispact-709", "flux_per_group": shape, "total": total,
                     "descending": False},
        "schedule": steps,
        "options": {"mode": mode, "prune": prune, "bmin_atoms_per_g": 1e-8,
                    "temperature_K": 293.6,
                    "outputs": (["photons"] if photons else []) + ["ledger", "certificate"]},
    }


def build_cases() -> list[dict]:
    sp, sc = spectra(), schedules()
    cases = []

    def add(run_id, arm, material, composition, spectrum, schedule, amplitude, mode, prune, zero=False,
            photons=False):
        doc = spec(run_id, composition, sp[spectrum], amplitude, sc[schedule]["steps"], mode, prune, zero,
                   photons)
        cases.append({"id": run_id, "arm": arm, "material": material, "spectrum": spectrum,
                      "schedule": schedule, "amplitude": amplitude, "mode": mode, "prune": prune,
                      "zero_flux": zero, "compared": sc[schedule]["compared"], "doc": doc})

    for m, comp in MATERIALS.items():
        for s in sc:
            # B: zero-flux background (natural radioactivity of the bulk); the spectrum is irrelevant
            add(f"B__{m}__{s}", "B", m, comp, "fns", s, 1.0, "trace", "reach", zero=True)
            for p in sp:
                # U: unit-amplitude linear operator (trace, exact reachable pruning)
                add(f"U__{m}__{p}__{s}", "U", m, comp, p, s, 1.0, "trace", "reach")
                for a in AMPLITUDES:
                    # T: truth (coupled, exact pruning); S: shipped-default arm (auto, rate pruning)
                    add(f"T__{m}__{p}__{s}__{a:.0e}", "T", m, comp, p, s, a, "coupled", "reach")
                    add(f"S__{m}__{p}__{s}__{a:.0e}", "S", m, comp, p, s, a, "auto", "rate")
    # Y: photon-enabled runs that supply per-nuclide photons/decay and power/decay per group. Photon
    # output is ~20 MB per step, so the population runs without it and composes group sources from
    # activity x this table (per-decay yields are decay-data constants; the checker verifies that
    # every Y run reports identical yields for each nuclide).
    for m, comp in MATERIALS.items():
        add(f"Y__{m}__mix__s1_1y__unit", "Y", m, comp, "mix", "s1_1y", 1.0, "trace", "reach", photons=True)
        for p in ("mix", "maxwell"):
            add(f"Y__{m}__{p}__s2_5y__1e+15", "Y", m, comp, p, "s2_5y", 1.0e15, "coupled", "reach",
                photons=True)
    # C: composition superposition — pure-element unit runs for every mixture element
    elements = sorted({e for m in MIXTURES for e in MATERIALS[m]} - {"Fe", "W", "Cu", "Co"})
    for e in elements:
        add(f"CB__{e}__s1_1y", "CB", e, {e: 100.0}, "fns", "s1_1y", 1.0, "trace", "reach", zero=True)
        for p in ("fns", "mix"):
            add(f"CU__{e}__{p}__s1_1y", "CU", e, {e: 100.0}, p, "s1_1y", 1.0, "trace", "reach")
    return cases


def cmd_build() -> None:
    SPECS.mkdir(parents=True, exist_ok=True)
    cases = build_cases()
    manifest = []
    for c in cases:
        path = SPECS / f"{c['id']}.json"
        data = json.dumps(c["doc"], sort_keys=True, separators=(",", ":")).encode()
        path.write_bytes(data)
        row = {k: v for k, v in c.items() if k != "doc"}
        row["spec_sha256"] = hashlib.sha256(data).hexdigest()
        manifest.append(row)
    MANIFEST.write_text(json.dumps({"cases": manifest}, indent=1, sort_keys=True))
    by_arm = {}
    for c in manifest:
        by_arm[c["arm"]] = by_arm.get(c["arm"], 0) + 1
    print(f"{len(manifest)} cases {by_arm}; manifest sha256 {sha256_file(MANIFEST)}")


def extract(result: dict, compared: list[int]) -> dict:
    steps = []
    for i in compared:
        s = result["steps"][i]
        act = s["activity_Bq_per_g"]
        a_tot = sum(act.values())
        steps.append({
            "index": i,
            "t_s": s["t_s"],
            "fluence_n_cm2": s["fluence_n_cm2"],
            "activity_total": a_tot,
            "heat": s["heat_W_per_g"],
            "total_atoms_per_g": s["total_atoms_per_g"],
            # every nuclide carrying >= 1e-15 of the step's activity
            "activity": {k: v for k, v in act.items() if a_tot > 0 and v >= 1e-15 * a_tot},
        })
    led = result["ledger"]
    return {
        "mode": result["mode"],
        "total_states": result.get("total_states"),
        "pruned_states": result.get("pruned_states"),
        "ledger": {k: led.get(k) for k in (
            "mode", "max_burnup_fraction", "max_burnup_nuclide", "max_burnup_optical_depth",
            "max_product_optical_depth", "max_product_optical_depth_nuclide", "bulk_background_heat_W_per_g")},
        "steps": steps,
    }


def extract_yields(result: dict) -> dict:
    """Per-nuclide photons/decay and W/(Bq) per photon group, from every step where the nuclide is active."""
    table = {}
    for s in result["steps"]:
        ps = s.get("photon_source") or {}
        for e in ps.get("by_nuclide", []):
            a = e["activity_Bq_g"]
            if not (a > 1e-250):
                continue
            ph = [g["photons_s_g"] / a for g in e["groups"]]
            pw = [g["power_W_g"] / a for g in e["groups"]]
            table.setdefault(e["nuclide"], []).append({"photons": ph, "power": pw})
    # keep one representative per nuclide plus the max relative spread seen across steps
    out = {}
    for n, rows in table.items():
        ref = rows[0]
        spread = 0.0
        for r in rows[1:]:
            for x, y in zip(ref["power"] + ref["photons"], r["power"] + r["photons"]):
                if x != 0 or y != 0:
                    spread = max(spread, abs(x - y) / max(abs(x), abs(y)))
        out[n] = {"photons": ref["photons"], "power": ref["power"], "spread": spread, "n": len(rows)}
    active = set()
    for s in result["steps"]:
        active |= {k for k, v in s["activity_Bq_per_g"].items() if v > 1e-250}
    bounds = (result["steps"][0].get("photon_source") or {}).get("boundaries_eV")
    # ACTINV's own per-step group totals and full activity vector, for the G0 composition check
    step_groups = []
    for s in result["steps"]:
        ps = s.get("photon_source") or {}
        step_groups.append({
            "power_W_g": [g["power_W_g"] for g in ps.get("groups", [])],
            "photons_s_g": [g["photons_s_g"] for g in ps.get("groups", [])],
            "activity": {k: v for k, v in s["activity_Bq_per_g"].items() if v > 0},
        })
    return {"yields": out, "active_without_photon_record": sorted(active - set(out)), "boundaries_eV": bounds,
            "step_groups": step_groups}


def run_one(case: dict) -> dict:
    RAW.mkdir(parents=True, exist_ok=True)
    out = RAW / f"{case['id']}.result.json"
    t0 = time.monotonic()
    proc = subprocess.run([str(BIN), "run", str(SPECS / f"{case['id']}.json"), str(out)],
                          capture_output=True, text=True, timeout=3600)
    wall = time.monotonic() - t0
    row = {"id": case["id"], "spec_sha256": case["spec_sha256"], "wall_s": wall,
           "returncode": proc.returncode}
    if proc.returncode != 0:
        row["error"] = (proc.stderr or proc.stdout)[-2000:]
    else:
        row["result_sha256"] = sha256_file(out)
        res = json.loads(out.read_text())
        row["data"] = extract(res, case["compared"])
        if case["arm"] == "Y":
            row["yields"] = extract_yields(res)
        del res
    if out.exists():
        out.unlink()
    return row


def done_ids() -> set[str]:
    if not CHECKPOINT.exists():
        return set()
    ids = set()
    for line in CHECKPOINT.read_text().splitlines():
        if line.strip():
            ids.add(json.loads(line)["id"])
    return ids


def cmd_run(only: str | None, limit: int | None) -> None:
    cases = json.loads(MANIFEST.read_text())["cases"]
    done = done_ids()
    todo = [c for c in cases if c["id"] not in done and (only is None or c["arm"] == only)]
    if limit is not None:
        todo = todo[:limit]
    print(f"binary {BIN} sha256 {sha256_file(BIN)}; {len(done)} done, {len(todo)} to run", flush=True)
    t_start = time.monotonic()
    with open(CHECKPOINT, "a") as f:
        for n, c in enumerate(todo, 1):
            row = run_one(c)
            f.write(json.dumps(row, sort_keys=True) + "\n")
            f.flush()
            if n % 25 == 0 or row["returncode"] != 0:
                el = time.monotonic() - t_start
                print(f"{n}/{len(todo)} {c['id']} rc={row['returncode']} {row['wall_s']:.2f}s "
                      f"elapsed {el:.0f}s", flush=True)


def cmd_profile() -> None:
    """Timing and memory only for the heaviest unit; comparison quantities are not inspected."""
    cases = {c["id"]: c for c in json.loads(MANIFEST.read_text())["cases"]}
    for cid in ("T__ss316ln__mix__s3_pulsed__1e+15", "U__ss316ln__mix__s3_pulsed",
                "S__concrete__mix__s3_pulsed__1e+15", "Y__ss316ln__maxwell__s2_5y__1e+15"):
        c = cases[cid]
        out = RAW / "profile.result.json"
        RAW.mkdir(parents=True, exist_ok=True)
        t0 = time.monotonic()
        proc = subprocess.run(["/usr/bin/time", "-f", "%e s %M kB", str(BIN), "run",
                               str(SPECS / f"{cid}.json"), str(out)], capture_output=True, text=True)
        size = out.stat().st_size if out.exists() else 0
        if out.exists():
            out.unlink()
        print(cid, f"rc={proc.returncode}", proc.stderr.strip().splitlines()[-1],
              f"result {size / 1e6:.1f} MB", f"{time.monotonic() - t0:.2f}s", flush=True)


if __name__ == "__main__":
    argv = sys.argv[1:]
    if not argv:
        sys.exit(__doc__)
    if argv[0] == "build":
        cmd_build()
    elif argv[0] == "run":
        only = argv[argv.index("--only") + 1] if "--only" in argv else None
        limit = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else None
        cmd_run(only, limit)
    elif argv[0] == "profile":
        cmd_profile()
    else:
        sys.exit(__doc__)
