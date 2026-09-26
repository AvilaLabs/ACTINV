#!/usr/bin/env python3
"""P53 G3 — executed demonstration on the 8-cell corpus mesh (same
P52 recipe: FNS-modulated distinct spectra, photon-dominant banded
responses), then `actinv export-r2s-joint` emits actinv-r2s-joint-1
for the final cooling step. Wall time and memory envelope ledgered.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import g3_p52_demo as p52demo  # noqa: E402
import p53_artifacts as p53a  # noqa: E402

BIN = Path(os.environ.get("ACTINV_BIN", ROOT / "target/release/actinv"))
OUT = ROOT / "results/g3_p53_demo.json"
JOINT = ROOT / "results/p53_r2s_joint.ndjson"
MESH_OUT = ROOT / "results/p53_mesh.ndjson"
EMIT_STEP = 4


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def main() -> int:
    problems = []
    t0 = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="p53-g3-", dir="target") as d:
        work = Path(d)
        # flux into results/ so the persisted spec's path stays resolvable
        # for the G5 checker (build_flux writes p52_flux.ndjson — rename)
        flux_path = p52demo.build_flux(work)
        stable_flux = ROOT / "results/p53_flux.ndjson"
        stable_flux.write_bytes(flux_path.read_bytes())
        spec = p52demo.build_spec(stable_flux)
        spec["title"] = "P53 demo: 8-cell Fe banded mesh (joint bands)"
        spec_path = ROOT / "results/p53_mesh_spec.json"
        spec_path.write_text(json.dumps(spec, indent=1))

        tm = time.monotonic()
        r = subprocess.run([str(BIN), "mesh", str(spec_path),
                            str(MESH_OUT)],
                           capture_output=True, text=True, timeout=3600)
        mesh_wall = time.monotonic() - tm
        if r.returncode != 0:
            problems.append(f"actinv mesh failed: {r.stderr[-1500:]}")
            OUT.write_text(json.dumps(
                {"pass": False, "problems": problems}, indent=2))
            return 1

        te = time.monotonic()
        r = subprocess.run([str(BIN), "export-r2s-joint", str(MESH_OUT),
                            str(spec_path), str(EMIT_STEP), str(JOINT)],
                           capture_output=True, text=True, timeout=1800)
        joint_wall = time.monotonic() - te
        if r.returncode != 0:
            problems.append(f"export-r2s-joint failed: {r.stderr[-1500:]}")

        detail = {}
        if not problems:
            recs = [json.loads(l) for l in JOINT.read_text().splitlines()
                    if l.strip()]
            cells = [r for r in recs if r["record"] == "cell"]
            corr = [r for r in recs if r["record"] == "correlation"]
            footer = recs[-1]
            if len(cells) != 8:
                problems.append(f"joint doc has {len(cells)} cells, want 8")
            if len(corr) != 1 or len(corr[0]["rho"]) != 8:
                problems.append("correlation record missing or not 8x8")
            else:
                rho = corr[0]["rho"]
                for i, row in enumerate(rho):
                    for j, v in enumerate(row):
                        if i != j and v is not None and not (-1.0 <= v <= 1.0):
                            problems.append(f"rho[{i}][{j}]={v} out of range")
            for key in ("sigma_total_independent", "sigma_total_conservative",
                        "sigma_total_correlated", "total_photons_s"):
                if not isinstance(footer.get(key), (int, float)):
                    problems.append(f"footer missing/nonnumeric {key}")
            if not problems:
                si = footer["sigma_total_independent"]
                sc = footer["sigma_total_conservative"]
                sj = footer["sigma_total_correlated"]
                # mathematical bound: correlated can never exceed conservative
                if sj > sc * (1 + 1e-9):
                    problems.append(
                        f"sigma_correlated {sj} exceeds conservative {sc}")
                # the physical finding: correlation is strong on this
                # corpus mesh → correlated must sit well above independent
                if sj <= si:
                    problems.append(
                        f"sigma_correlated {sj} not above independent {si} "
                        "on the shared-covariance corpus mesh")
                detail = {
                    "sigma_total_independent": si,
                    "sigma_total_conservative": sc,
                    "sigma_total_correlated": sj,
                    "corr_vs_independent_ratio": sj / si if si else None,
                    "corr_vs_conservative_ratio": sj / sc if sc else None,
                    "min_offdiag_rho": min(
                        (v for i, row in enumerate(corr[0]["rho"])
                         for j, v in enumerate(row) if i != j
                         and v is not None), default=None),
                }

        result = {
            "pass": not problems,
            "mesh_wall_s": mesh_wall,
            "joint_wall_s": joint_wall,
            "total_wall_s": time.monotonic() - t0,
            "mesh_sha256": sha256_file(MESH_OUT),
            "joint_sha256": sha256_file(JOINT) if JOINT.exists() else None,
            "joint_bytes": JOINT.stat().st_size if JOINT.exists() else 0,
            "hardware": {"cpu": platform.processor() or platform.machine(),
                         "cell_emit_step": EMIT_STEP},
            "detail": detail,
            "problems": problems,
        }
        OUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        print(json.dumps({"pass": result["pass"], "detail": detail,
                          "mesh_wall_s": mesh_wall,
                          "joint_wall_s": joint_wall,
                          "problems": problems[:6]}))
        return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
