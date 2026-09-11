#!/usr/bin/env python3
"""P19 G3 held-out rate battery — runtime consequences of the shield table.

Legs (each a bounded CLI invocation under the caller's cgroup):
- pure Ta-181, flux band on its unresolved range, fixed sigma0=0.1 b:
  the shielded run must move the Ta-182 production observably downward.
- pure W-186, same band, fixed sigma0=1e10 b: the output payload must be
  byte-identical to the unshielded run (sigma0->inf reproduces baseline).
- mesh parity: the shielded/unshielded W-187 activity ratio through
  `actinv mesh` must agree with the ratio through `actinv run` (the two
  surfaces share the fold; a surface-specific drift is what parity catches).

Emits `results/g3_p19_rates.json`. Fails closed when a leg cannot run.
"""
import copy
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results/g3_p19_rates.json"
TABLE = ROOT / "results/g1_p19_shield_artifact.json"
BASE_SPEC = ROOT / "examples/fns_fe_5min.json"
ACTINV = Path(os.environ.get("ACTINV_BIN", ROOT / "target/release/actinv"))
WORK = ROOT / "target/p19_g3_rates"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def run_cli(spec_path: Path, out_path: Path) -> dict:
    proc = subprocess.run(
        [str(ACTINV), "run", str(spec_path), str(out_path)],
        cwd=ROOT, text=True, capture_output=True, check=False, timeout=600,
    )
    output = None
    if out_path.exists():
        output = json.loads(out_path.read_text(encoding="utf-8"))
    return {"returncode": proc.returncode, "output": output}


def run_mesh(spec_path: Path, out_path: Path) -> dict:
    proc = subprocess.run(
        [str(ACTINV), "mesh", str(spec_path), str(out_path)],
        cwd=ROOT, text=True, capture_output=True, check=False, timeout=600,
    )
    rows = []
    if out_path.exists():
        rows = [
            json.loads(line)
            for line in out_path.read_text().splitlines()
            if line.strip()
        ]
    return {"returncode": proc.returncode, "rows": rows}


def normalized(result: dict) -> dict:
    """Physics payload only: strip wall-time and the shielding provenance
    blocks (which correctly record that the run declared the section)."""
    out = copy.deepcopy(result)
    out.pop("ms", None)
    ledger = out.get("ledger")
    if isinstance(ledger, dict):
        ledger.pop("shielding", None)
    cert = out.get("certificate")
    if isinstance(cert, dict):
        cert.pop("shielding", None)
        inputs = cert.get("inputs")
        if isinstance(inputs, dict):
            inputs.pop("shielding_table", None)
    return out


def write_spec(path: Path, spec: dict) -> Path:
    path.write_text(json.dumps(spec), encoding="utf-8")
    return path


def make_spec(*, material: dict, flux_groups: range, shielding: dict | None) -> dict:
    spec = json.loads(BASE_SPEC.read_text(encoding="utf-8"))
    bounds = json.loads(TABLE.read_text(encoding="utf-8"))["group_structure"][
        "boundaries_eV"
    ]
    flux = [0.0] * (len(bounds) - 1)
    for g in flux_groups:
        flux[g] = 1.0e13
    spec["material"] = material
    spec["schedule"] = [{"dt": "1 h", "flux": 1.0}]
    spec["spectrum"] = {
        "structure": "custom",
        "flux_per_group": flux,
        "total": None,
        "boundaries_eV": bounds,
        "descending": False,
    }
    if shielding is not None:
        spec["self_shielding"] = shielding
    return spec


def make_mesh_spec(*, material: dict, flux: list, bounds: list,
                   shielding: dict | None, flux_path: Path) -> dict:
    flux_path.write_text(
        json.dumps(
            {
                "record": "header",
                "schema": "actinv-flux-1",
                "source": {
                    "format": "p19-g3-control",
                    "path": "controls/p19_g3_rates.py",
                    "sha256": "0" * 64,
                },
                "energy_boundaries_eV": bounds,
                "flux_units": "n cm^-2 s^-1",
                "cell_count": 1,
            }
        )
        + "\n"
        + json.dumps(
            {
                "record": "cell",
                "ordinal": 0,
                "id": "cell-a",
                "flux_per_group": flux,
                "flux_total": sum(flux),
            }
        )
        + "\n"
        + json.dumps(
            {
                "record": "footer",
                "cell_count": 1,
                "flux_sum_over_cells": sum(flux),
            }
        )
        + "\n"
    )
    spec = {
        "spec": "actinv-mesh-spec-1",
        "title": "p19 g3 mesh parity",
        "projectile": "neutron",
        "library": {
            "path": "actinv-data/v1.0.0/activation/tendl-2025-neutron-709g.npz",
            "sha256": "ec4c72bf598dc8ad3d533d9cfafdcf493e2d1f949a3e4db6251495659b68cc44",
        },
        "decay": {
            "primary": "actinv-data/v1.0.0/decay/endf-b-viii-0_decay.dat",
            "fallback": "actinv-data/v1.0.0/decay/jeff-3-3_decay.dat",
        },
        "flux": {"path": str(flux_path.relative_to(ROOT)),
                 "sha256": sha256(flux_path)},
        "material": material,
        "schedule": [{"dt": "1 h", "flux": 1.0}],
        "chunk_cells": 1,
        "threads": 1,
    }
    if shielding is not None:
        spec["self_shielding"] = shielding
    return spec


def activity(out: dict, nuclide: str):
    try:
        return out["steps"][0]["activity_Bq_per_g"].get(nuclide)
    except (KeyError, IndexError, TypeError):
        return None


def mesh_activity(rows: list, nuclide: str):
    """Pull a nuclide's per-cell activity out of the first mesh result row."""
    for row in rows:
        if not isinstance(row, dict):
            continue
        for container in (row, row.get("result", {})):
            steps = container.get("steps")
            if steps:
                value = steps[0].get("activity_Bq_per_g", {}).get(nuclide)
                if value is not None:
                    return value
    return None


def main() -> None:
    if not ACTINV.exists():
        raise SystemExit("release actinv binary missing — build first")
    if not TABLE.exists():
        raise SystemExit("g1 shield artifact missing — run controls/p19_shielding.py")
    table_sha = sha256(TABLE)
    WORK.mkdir(parents=True, exist_ok=True)
    shield_ref = {"path": str(TABLE.relative_to(ROOT)), "sha256": table_sha}
    started = time.monotonic()
    checks: dict[str, bool] = {}
    details: dict = {"table_sha256": table_sha}
    bounds = json.loads(TABLE.read_text(encoding="utf-8"))["group_structure"][
        "boundaries_eV"
    ]

    ta181 = {"mass_g": 1.0, "basis": "wt_percent", "composition": {"Ta181": 100.0}}
    w186 = {"mass_g": 1.0, "basis": "wt_percent", "composition": {"W186": 100.0}}
    # Flux bands restricted to each nuclide's covered groups so the held-out
    # signal is the shielding, not the spectrum width.
    ta_groups = range(430, 470)
    w_groups = range(425, 475)

    # -- leg 1: Ta-181 held-out deep-dilution rate change -------------------
    ta_plain_spec = write_spec(
        WORK / "ta_plain.json",
        make_spec(material=ta181, flux_groups=ta_groups, shielding=None))
    ta_deep_spec = write_spec(
        WORK / "ta_deep.json",
        make_spec(material=ta181, flux_groups=ta_groups,
                  shielding={"table": shield_ref, "dilution": "fixed",
                             "sigma0_b": 0.1}))
    ta_plain = run_cli(ta_plain_spec, WORK / "ta_plain_out.json")
    ta_deep = run_cli(ta_deep_spec, WORK / "ta_deep_out.json")
    ta182_plain = activity(ta_plain["output"] or {}, "Ta182")
    ta182_deep = activity(ta_deep["output"] or {}, "Ta182")
    ta_ratio = (ta182_deep / ta182_plain) if (ta182_plain and ta182_deep) else None
    details["ta182_ratio_sigma0_0.1"] = ta_ratio
    checks["ta181_heldout_deep_rate_moves"] = (
        ta_ratio is not None and 0.3 < ta_ratio < 0.98
    )

    # -- leg 2: sigma0=1e10 reproduces unshielded bytes ----------------------
    w_plain_spec = write_spec(
        WORK / "w_plain.json",
        make_spec(material=w186, flux_groups=w_groups, shielding=None))
    w_inf_spec = write_spec(
        WORK / "w_inf.json",
        make_spec(material=w186, flux_groups=w_groups,
                  shielding={"table": shield_ref, "dilution": "fixed",
                             "sigma0_b": 1.0e10}))
    w_plain = run_cli(w_plain_spec, WORK / "w_plain_out.json")
    w_inf = run_cli(w_inf_spec, WORK / "w_inf_out.json")
    checks["sigma0_inf_reproduces_baseline"] = (
        w_plain["returncode"] == 0
        and w_inf["returncode"] == 0
        and normalized(w_plain["output"] or {}) == normalized(w_inf["output"] or {})
    )

    # -- leg 3: mesh parity — same shielded/unshielded ratio ----------------
    w_deep_spec = write_spec(
        WORK / "w_deep.json",
        make_spec(material=w186, flux_groups=w_groups,
                  shielding={"table": shield_ref, "dilution": "fixed",
                             "sigma0_b": 0.1}))
    w_deep = run_cli(w_deep_spec, WORK / "w_deep_out.json")
    flux = [0.0] * (len(bounds) - 1)
    for g in w_groups:
        flux[g] = 1.0e13
    mesh_deep_spec = write_spec(
        WORK / "w_mesh_deep.json",
        make_mesh_spec(
            material=w186, flux=flux, bounds=bounds,
            shielding={"table": shield_ref, "dilution": "fixed", "sigma0_b": 0.1},
            flux_path=WORK / "mesh_flux_deep.json"))
    mesh_inf_spec = write_spec(
        WORK / "w_mesh_inf.json",
        make_mesh_spec(
            material=w186, flux=flux, bounds=bounds,
            shielding={"table": shield_ref, "dilution": "fixed", "sigma0_b": 1.0e10},
            flux_path=WORK / "mesh_flux_inf.json"))
    mesh_deep = run_mesh(mesh_deep_spec, WORK / "w_mesh_deep.ndjson")
    mesh_inf = run_mesh(mesh_inf_spec, WORK / "w_mesh_inf.ndjson")
    w187_spec_ratio = None
    w187_deep = activity(w_deep["output"] or {}, "W187")
    w187_inf = activity(w_inf["output"] or {}, "W187")
    if w187_deep and w187_inf:
        w187_spec_ratio = w187_deep / w187_inf
    w187_mesh_deep = mesh_activity(mesh_deep["rows"], "W187")
    w187_mesh_inf = mesh_activity(mesh_inf["rows"], "W187")
    w187_mesh_ratio = None
    if w187_mesh_deep and w187_mesh_inf:
        w187_mesh_ratio = w187_mesh_deep / w187_mesh_inf
    details["w187_ratio_spec_surface"] = w187_spec_ratio
    details["w187_ratio_mesh_surface"] = w187_mesh_ratio
    parity_ok = (
        w187_spec_ratio is not None
        and w187_mesh_ratio is not None
        and abs(w187_spec_ratio - w187_mesh_ratio) / w187_spec_ratio < 0.02
    )
    checks["mesh_parity_rate_ratio"] = parity_ok

    elapsed = time.monotonic() - started
    record = {
        "schema": "actinv-p19-g3-rates-1",
        "table_sha256": table_sha,
        "checks": checks,
        "details": details,
        "wall_seconds": round(elapsed, 2),
        "pass": all(checks.values()),
    }
    RESULT.write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"pass": record["pass"], "checks": checks}, indent=1))


if __name__ == "__main__":
    main()
