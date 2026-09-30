#!/usr/bin/env python3
"""P92 G5: same-data gas appm against FISPACT-II/TENDL-2017 on the CB3 FNS configuration.

Runs the candidate `actinv` CLI with `options.gas: true` on the 132 CB3 FNS specs (built exactly as
controls/cb3_fns.py builds them) and compares the produced appm per species at the end of the
irradiation step with FISPACT-II's printed `APPM OF` values from the frozen TENDL-2017 outputs.

Gate (pre-registered in protocols/ACTINV-P92_PROTOCOL.md): over every (experiment, species) pair
whose FISPACT value is at least 1 % of that experiment's summed gas appm, at least 90 % of the
ratios lie in [0.90, 1.10], and the pooled geometric-mean ratio lies in [0.97, 1.03] for He4 and H1.
"""
from __future__ import annotations

import concurrent.futures as cf
import glob
import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
from harness import fispact_io as fio  # noqa: E402

DATA = Path.home() / "nuclear-data"
FNS = DATA / "conderc-fns/fns"
LIBRARY = DATA / "tendl-2017/build/neutron.n.p10.npz"
LIBRARY_SHA = "218156708ae685db2a50cd7a2c74ca02049b68f266c38a29020b71095717447d"
DECAY_PRIMARY = DATA / "endfb-viii.0-decay/bulk/endf-b-viii-0_decay.dat"
DECAY_FALLBACK = DATA / "jeff-3.3-decay/bulk/jeff-3-3_decay.dat"
OUT = ROOT / "target/p92"  # --out overrides (P95: target/p95)
FIELD = "appm"  # --field overrides (P95: inventory_appm, FISPACT-II's printed APPM convention)
WORK = OUT / "g5"
CANDIDATE = ROOT / "target/release/actinv"
SPECIES = {"He4": ("He", 4), "He3": ("He", 3), "H3": ("H", 3), "H2": ("H", 2), "H1": ("H", 1)}
LOW, HIGH = 0.90, 1.10
FRACTION = 0.90
GEO_LOW, GEO_HIGH = 0.97, 1.03
SHARE_FLOOR = 0.01


def read_flux(path: Path) -> list[float]:
    values: list[float] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            values.extend(float(v) for v in line.split())
        except ValueError:
            break
    return values[:709]


def specification(material: str, experiment: str) -> dict:
    directory = FNS / material
    record = fio.read_i(directory / f"TENDL-2017_{experiment}.i")
    flux = read_flux(directory / f"{experiment}_fluxes")
    cooling = record["cooling_cum_s"]
    schedule = [
        {"dt": f"{record['t_irr_s']:.17e} s", "flux": 1.0},
        {"dt": f"{cooling[0]:.17e} s", "flux": 0.0},
    ]
    schedule.extend(
        {"dt": f"{cooling[i] - cooling[i - 1]:.17e} s", "flux": 0.0} for i in range(1, len(cooling))
    )
    return {
        "spec": "actinv-spec-1",
        "title": f"CB1 FNS {material} {experiment}",
        "projectile": "neutron",
        "library": {"path": str(LIBRARY), "sha256": LIBRARY_SHA},
        "decay": {"primary": str(DECAY_PRIMARY), "fallback": str(DECAY_FALLBACK)},
        "material": {
            "mass_g": float(record["mass_kg"] * 1000.0),
            "basis": "wt_percent",
            "composition": record["elements"],
        },
        "spectrum": {
            "structure": "fispact-709",
            "flux_per_group": flux,
            "total": float(record["flux_total"]),
            "descending": True,
        },
        "schedule": schedule,
        "options": {
            "mode": "auto",
            "prune": "rate",
            "bmin_atoms_per_g": 1.0e-8,
            "temperature_K": 293.6,
            "outputs": ["inventory", "activity", "heat", "ledger", "certificate"],
            "gas": True,
        },
        "fission_yields": {"files": [], "energy": "spectrum_average"},
    }


HEADER = re.compile(r"^1 \* \* \* TIME INTERVAL\s+(\d+)")
APPM = re.compile(r"APPM OF\s+(He|H)\s+(\d)\s+=\s+([0-9.Ee+-]+)")


def fispact_end_of_irradiation(path: Path) -> dict[str, float]:
    """APPM block of the last interval before the first cooling interval."""
    blocks: list[tuple[bool, dict[str, float]]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if HEADER.match(line):
            blocks.append(("COOLING TIME" in line, {}))
            continue
        m = APPM.search(line)
        if m and blocks:
            name = f"{m.group(1)}{m.group(2)}"
            blocks[-1][1][name] = float(m.group(3))
    irradiation = [b for cooling, b in blocks if not cooling and b]
    if not irradiation:
        raise ValueError(f"{path}: no irradiation APPM block")
    return irradiation[-1]


def run_one(item: tuple[str, str]) -> dict:
    material, experiment = item
    spec_path = WORK / f"{material}_{experiment}.spec.json"
    out_path = WORK / f"{material}_{experiment}.out.json"
    spec_path.write_text(json.dumps(specification(material, experiment)))
    proc = subprocess.run([str(CANDIDATE), "run", str(spec_path), str(out_path)],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        return {"material": material, "experiment": experiment, "error": proc.stderr[-2000:]}
    result = json.loads(out_path.read_text())
    gas = result["steps"][0].get("gas")
    out_path.unlink()
    if gas is None:
        return {"material": material, "experiment": experiment, "error": "no gas block on step 0"}
    actinv = {s: gas[s][FIELD] for s in SPECIES}
    fispact = fispact_end_of_irradiation(FNS / material / f"TENDL-2017_{experiment}.out")
    return {"material": material, "experiment": experiment, "actinv_appm": actinv,
            "fispact_appm": fispact, "ledger_gas": result.get("ledger", {}).get("gas")}


def main() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    experiments = [
        (m.name, Path(p).stem)
        for m in sorted(p for p in FNS.iterdir() if p.is_dir())
        for p in sorted(glob.glob(str(m / "*.exp")))
    ]
    assert len(experiments) == 132, len(experiments)
    with cf.ThreadPoolExecutor(max_workers=3) as pool:
        records = list(pool.map(run_one, experiments))
    errors = [r for r in records if "error" in r]
    pairs = []
    for r in records:
        if "error" in r:
            continue
        total = sum(r["fispact_appm"].get(s, 0.0) for s in SPECIES)
        for s in SPECIES:
            f = r["fispact_appm"].get(s, 0.0)
            a = r["actinv_appm"][s]
            pairs.append({"material": r["material"], "experiment": r["experiment"], "species": s,
                          "fispact": f, "actinv": a,
                          "ratio": a / f if f > 0 else None,
                          "gated": total > 0 and f >= SHARE_FLOOR * total})
    gated = [p for p in pairs if p["gated"]]
    inside = [p for p in gated if p["ratio"] is not None and LOW <= p["ratio"] <= HIGH]
    frac = len(inside) / len(gated) if gated else 0.0
    geo = {}
    for s in SPECIES:
        rs = [p["ratio"] for p in gated if p["species"] == s and p["ratio"] and p["ratio"] > 0]
        geo[s] = {"pairs": len(rs),
                  "geometric_mean": math.exp(sum(math.log(x) for x in rs) / len(rs)) if rs else None}
    geo_ok = all(geo[s]["geometric_mean"] is not None and GEO_LOW <= geo[s]["geometric_mean"] <= GEO_HIGH
                 for s in ("He4", "H1"))
    zero_actinv = [p for p in gated if not p["actinv"] or p["actinv"] <= 0]
    tail = sorted(gated, key=lambda p: -abs(math.log(p["ratio"])) if p["ratio"] and p["ratio"] > 0 else -1e9)[:25]
    out = {
        "gate": "G5",
        "experiments": len(records),
        "errors": errors,
        "gated_pairs": len(gated),
        "gated_pairs_within_10pct": len(inside),
        "fraction_within": frac,
        "fraction_pass": frac >= FRACTION,
        "geometric_mean_by_species": geo,
        "geometric_mean_pass": geo_ok,
        "gated_pairs_with_nonpositive_actinv": len(zero_actinv),
        "tail": tail,
        "all_pairs": pairs,
        "pass": not errors and frac >= FRACTION and geo_ok,
    }
    out["actinv_field"] = FIELD
    (OUT / "g5.json").write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    print(json.dumps({k: out[k] for k in ("experiments", "gated_pairs", "fraction_within",
                                          "geometric_mean_by_species", "pass")}, indent=1))
    print("errors", len(errors))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--field", choices=("appm", "inventory_appm"), default=FIELD)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    FIELD, OUT, WORK = args.field, args.out, args.out / "g5"
    main()
