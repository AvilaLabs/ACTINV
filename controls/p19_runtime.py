#!/usr/bin/env python3
"""P19 G2 producer battery — the self_shielding run plane.

Builds a pure-W186 spec whose flat flux band covers the 4.3-25 keV unresolved
range, then exercises every G2 leg through the release CLI:

- absent-section byte identity (payload modulo wall-clock `ms`)
- fixed dilution at sigma0 = 0.1 b (the deep-dip end)
- composition dilution on a W186+Fe mix and on pure W186
- uncovered naming + require_shielding_complete fail-closed
- uncertainty + self_shielding rejection
- table sha256 mismatch rejection
- group-boundary mismatch rejection
- the mesh-spec surface

Evidence: results/g2_p19_runtime.json. The table artifact is the frozen
results/g1_p19_shield_artifact.json (hash-pinned by sha256 in every spec).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
ACTINV = ROOT / "target/release/actinv"
TABLE = ROOT / "results/g1_p19_shield_artifact.json"
BASE_SPEC = ROOT / "examples/fns_fe_5min.json"
ARTIFACT = ROOT / "results/g1_p19_shield_artifact.json"
RESULT = ROOT / "results/g2_p19_runtime.json"
WORK = ROOT / "target/p19_g2_runtime"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def run_cli(spec_path: Path, out_path: Path) -> dict:
    completed = subprocess.run(
        [str(ACTINV), "run", str(spec_path), str(out_path)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=600,
    )
    return {
        "returncode": completed.returncode,
        "stderr": completed.stderr.strip(),
        "stdout": completed.stdout.strip(),
        "output": json.loads(out_path.read_text()) if out_path.exists() else None,
    }


def payload_without_clock(result: dict) -> dict:
    out = dict(result)
    out.pop("ms", None)
    return out


def make_spec(*, material: dict, flux_groups: range, shielding: dict | None) -> dict:
    spec = json.loads(BASE_SPEC.read_text(encoding="utf-8"))
    bounds = json.loads(ARTIFACT.read_text(encoding="utf-8"))["group_structure"][
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


def main() -> None:
    if not ACTINV.exists():
        raise SystemExit("release actinv binary missing — build first")
    if not TABLE.exists():
        raise SystemExit("g1 shield artifact missing — run controls/p19_shielding.py")
    table_sha = sha256(TABLE)
    WORK.mkdir(parents=True, exist_ok=True)
    shield_table_ref = {"path": str(TABLE.relative_to(ROOT)), "sha256": table_sha}

    w186 = {"mass_g": 1.0, "basis": "wt_percent", "composition": {"W186": 100.0}}
    # W-186 unresolved range 4.34-25 keV -> groups 431-469.
    w_groups = range(425, 475)

    checks: dict[str, bool] = {}
    details: dict = {"table_sha256": table_sha}

    started = time.monotonic()

    # -- leg 1: absent-section byte identity -------------------------------
    plain_spec = WORK / "w186_plain.json"
    plain_spec.write_text(
        json.dumps(make_spec(material=w186, flux_groups=w_groups, shielding=None))
    )
    plain_a = run_cli(plain_spec, WORK / "plain_a.json")
    plain_b = run_cli(plain_spec, WORK / "plain_b.json")
    checks["absent_deterministic"] = (
        plain_a["returncode"] == 0
        and plain_b["returncode"] == 0
        and payload_without_clock(plain_a["output"])
        == payload_without_clock(plain_b["output"])
    )

    # -- leg 2: fixed dilution sigma0=0.1 changes the W187 rate ------------
    fixed_spec = WORK / "w186_fixed.json"
    fixed_spec.write_text(
        json.dumps(
            make_spec(
                material=w186,
                flux_groups=w_groups,
                shielding={
                    "table": shield_table_ref,
                    "dilution": "fixed",
                    "sigma0_b": 0.1,
                },
            )
        )
    )
    fixed = run_cli(fixed_spec, WORK / "fixed_out.json")
    w187_plain = plain_a["output"]["steps"][0]["activity_Bq_per_g"].get("W187")
    w187_fixed = None
    if fixed["returncode"] == 0 and fixed["output"]:
        w187_fixed = fixed["output"]["steps"][0]["activity_Bq_per_g"].get("W187")
    ratio = (w187_fixed / w187_plain) if (w187_plain and w187_fixed) else None
    checks["fixed_dilution_changes_rate"] = ratio is not None and 0.5 < ratio < 0.9
    details["w187_ratio_sigma0_0p1"] = ratio
    shielded_ledger = (fixed["output"] or {}).get("ledger", {}).get("shielding", {})
    checks["ledger_records_shielding"] = bool(
        shielded_ledger.get("table_sha256") == table_sha
        and shielded_ledger.get("sigma0_eff_b", {}).get("W186") == 0.1
        and shielded_ledger.get("dilution") == "fixed"
    )
    checks["certificate_records_shielding"] = bool(
        (fixed["output"] or {})
        .get("certificate", {})
        .get("shielding", {})
        .get("table_sha256")
        == table_sha
    )

    # -- leg 3: composition dilution on a W186/Fe mix ----------------------
    mix_spec = WORK / "mix_comp.json"
    mix_spec.write_text(
        json.dumps(
            make_spec(
                material={
                    "mass_g": 1.0,
                    "basis": "wt_percent",
                    "composition": {"W186": 10.0, "Fe": 90.0},
                },
                flux_groups=w_groups,
                shielding={"table": shield_table_ref, "dilution": "composition"},
            )
        )
    )
    mix = run_cli(mix_spec, WORK / "mix_out.json")
    mix_led = (mix["output"] or {}).get("ledger", {}).get("shielding", {})
    sigma0_eff = mix_led.get("sigma0_eff_b", {})
    checks["composition_dilution_computes_sigma0"] = bool(
        mix["returncode"] == 0
        and sigma0_eff.get("W186", 0) > 10.0
        and sigma0_eff.get("Fe56", 0) > 0.0
        and "Fe54" in mix_led.get("sigma_p_estimated", [])
    )
    details["composition_sigma0_eff_b"] = sigma0_eff

    # -- leg 4: uncovered naming + fail-closed ------------------------------
    fe_spec = WORK / "fe_named.json"
    fe_spec.write_text(
        json.dumps(
            make_spec(
                material={
                    "mass_g": 1.0,
                    "basis": "wt_percent",
                    "composition": {"FE": 100.0},
                },
                flux_groups=range(540, 640),
                shielding={
                    "table": shield_table_ref,
                    "dilution": "fixed",
                    "sigma0_b": 1.0,
                },
            )
        )
    )
    fe = run_cli(fe_spec, WORK / "fe_out.json")
    fe_led = (fe["output"] or {}).get("ledger", {}).get("shielding", {})
    checks["uncovered_named"] = fe_led.get("shielding_uncovered") == [
        "Fe54",
        "Fe57",
        "Fe58",
    ]

    fe_req_spec = WORK / "fe_require.json"
    fe_req = json.loads(fe_spec.read_text())
    fe_req["options"]["require_shielding_complete"] = True
    fe_req_spec.write_text(json.dumps(fe_req))
    fe_req_result = run_cli(fe_req_spec, WORK / "fe_req_out.json")
    checks["require_complete_fails_closed"] = (
        fe_req_result["returncode"] != 0
        and "lack table coverage" in fe_req_result["stderr"]
    )

    # -- leg 5: uncertainty + self_shielding is rejected --------------------
    unc_spec = WORK / "uncertain.json"
    unc = json.loads(fixed_spec.read_text())
    unc["uncertainty"] = {"covariance": {"path": "x", "sha256": "0" * 64}}
    unc_spec.write_text(json.dumps(unc))
    unc_result = run_cli(unc_spec, WORK / "unc_out.json")
    checks["uncertainty_combination_rejected"] = (
        unc_result["returncode"] != 0
        and "uncertainty" in unc_result["stderr"].lower()
    )

    # -- leg 6: sha256 mismatch rejected ------------------------------------
    bad_sha_spec = WORK / "bad_sha.json"
    bad_sha = json.loads(fixed_spec.read_text())
    bad_sha["self_shielding"]["table"]["sha256"] = "0" * 64
    bad_sha_spec.write_text(json.dumps(bad_sha))
    bad_sha_result = run_cli(bad_sha_spec, WORK / "bad_sha_out.json")
    checks["sha_mismatch_rejected"] = bad_sha_result["returncode"] != 0

    # -- leg 7: group-boundary mismatch rejected ----------------------------
    tampered = WORK / "tampered_table.json"
    tampered_data = json.loads(TABLE.read_text())
    tampered_data["group_structure"]["boundaries_eV"][10] *= 1.000001
    tampered.write_text(json.dumps(tampered_data))
    bounds_spec = WORK / "bad_bounds.json"
    bad_bounds = json.loads(fixed_spec.read_text())
    bad_bounds["self_shielding"]["table"] = {
        "path": str(tampered.relative_to(ROOT)),
        "sha256": sha256(tampered),
    }
    bounds_spec.write_text(json.dumps(bad_bounds))
    bounds_result = run_cli(bounds_spec, WORK / "bad_bounds_out.json")
    checks["boundary_mismatch_rejected"] = (
        bounds_result["returncode"] != 0
        and "boundaries" in bounds_result["stderr"].lower()
    )

    # -- leg 8: mesh-spec surface -------------------------------------------
    mesh_flux = WORK / "mesh_flux.ndjson"
    bounds = json.loads(ARTIFACT.read_text())["group_structure"]["boundaries_eV"]
    cell_flux = [1.0e13 if 425 <= g < 475 else 0.0 for g in range(len(bounds) - 1)]
    mesh_flux.write_text(
        json.dumps(
            {
                "record": "header",
                "schema": "actinv-flux-1",
                "source": {
                    "format": "p19-g2-control",
                    "path": "controls/p19_runtime.py",
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
                "flux_per_group": cell_flux,
                "flux_total": sum(cell_flux),
            }
        )
        + "\n"
        + json.dumps(
            {
                "record": "footer",
                "cell_count": 1,
                "flux_sum_over_cells": sum(cell_flux),
            }
        )
        + "\n"
    )
    mesh_spec = WORK / "mesh_shielded.json"
    mesh_spec.write_text(
        json.dumps(
            {
                "spec": "actinv-mesh-spec-1",
                "title": "p19 mesh shielding leg",
                "projectile": "neutron",
                "library": {
                    "path": "actinv-data/v1.0.0/activation/tendl-2025-neutron-709g.npz",
                    "sha256": "ec4c72bf598dc8ad3d533d9cfafdcf493e2d1f949a3e4db6251495659b68cc44",
                },
                "decay": {
                    "primary": "actinv-data/v1.0.0/decay/endf-b-viii-0_decay.dat",
                    "fallback": "actinv-data/v1.0.0/decay/jeff-3-3_decay.dat",
                },
                "flux": {"path": str(mesh_flux.relative_to(ROOT)), "sha256": sha256(mesh_flux)},
                "material": w186,
                "schedule": [{"dt": "1 h", "flux": 1.0}],
                "self_shielding": {
                    "table": shield_table_ref,
                    "dilution": "fixed",
                    "sigma0_b": 0.1,
                },
                "chunk_cells": 1,
                "threads": 1,
            }
        )
    )
    mesh_out = WORK / "mesh_out.ndjson"
    mesh_result = subprocess.run(
        [str(ACTINV), "mesh", str(mesh_spec), str(mesh_out)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=600,
    )
    checks["mesh_surface_accepts_section"] = mesh_result.returncode == 0 and mesh_out.exists()

    elapsed = time.monotonic() - started
    record = {
        "schema": "actinv-p19-g2-runtime-1",
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
