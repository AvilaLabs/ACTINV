#!/usr/bin/env python3
"""P53 G1 mechanics — actinv-r2s-joint-1 emission on the P53 synthetic
fixture (2-group library, photon-bearing Mn57; two cells with
scalar-proportional spectra → physically degenerate full correlation)
plus every rejection path: spec fingerprint mismatch, flux binding
mismatch, unbanded mesh, truncated mesh.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p53_fixture as fx  # noqa: E402
import p53_artifacts as p53a  # noqa: E402

BIN = Path(os.environ.get("ACTINV_BIN", ROOT / "target/debug/actinv"))
OUT = ROOT / "results/g1_p53_mechanics.json"
EMIT_STEP = 2


def run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run([str(BIN)] + args, capture_output=True,
                          text=True, timeout=600)


def load_ndjson(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().splitlines()
            if l.strip()]


def main() -> int:
    checks: dict[str, bool] = {}
    problems: list[str] = []
    with tempfile.TemporaryDirectory(prefix="p53-g1-", dir="target") as d:
        work = Path(d)
        fxmap = fx.make_fixture(work)
        flux = work / "flux.ndjson"
        # proportional spectra → exact full correlation (ρ = 1)
        fx.write_flux(flux, [[1.0, 1.0], [2.0, 2.0]])
        spec_path = work / "mesh_spec.json"
        spec_path.write_text(json.dumps(
            fx.mesh_spec(fxmap, flux), indent=1))
        mesh_out = work / "mesh.ndjson"
        joint_out = work / "joint.ndjson"

        r = run(["mesh", str(spec_path), str(mesh_out)])
        checks["mesh_run"] = r.returncode == 0
        if r.returncode != 0:
            problems.append(f"mesh failed: {r.stderr[-800:]}")
        if checks["mesh_run"]:
            r = run(["export-r2s-joint", str(mesh_out), str(spec_path),
                     str(EMIT_STEP), str(joint_out)])
            checks["export_run"] = r.returncode == 0
            if r.returncode != 0:
                problems.append(
                    f"export-r2s-joint failed: {r.stderr[-800:]}")

        if checks.get("export_run"):
            recs = load_ndjson(joint_out)
            header = recs[0]
            cells = [r for r in recs if r["record"] == "cell"]
            corr = [r for r in recs if r["record"] == "correlation"]
            footer = recs[-1]
            checks["schema"] = header.get("schema") == "actinv-r2s-joint-1"
            checks["binding_fields"] = all(
                k in header for k in
                ("mesh_result_sha256", "spec_fingerprint_sha256",
                 "canonical_flux_sha256", "activation_library_sha256",
                 "covariance_sha256", "scope"))
            checks["two_cells"] = len(cells) == 2
            checks["cells_emit_photons"] = all(
                c["photons_s"] > 0 for c in cells)
            checks["cell_correlated_sigma"] = all(
                isinstance(c.get("sigma_photons_s_correlated"),
                           (int, float))
                and c["sigma_photons_s_correlated"] > 0
                for c in cells)
            checks["corr_record"] = len(corr) == 1
            if corr:
                rho = corr[0]["rho"]
                checks["rho_shape"] = (len(rho) == 2
                                       and all(len(row) == 2
                                               for row in rho))
                checks["rho_diag"] = all(rho[i][i] == 1.0
                                         for i in range(2))
                # proportional spectra → exact full correlation
                checks["rho_degenerate_full"] = (
                    rho[0][1] is not None
                    and abs(rho[0][1] - 1.0) < 1e-9)
            checks["footer_fields"] = all(
                k in footer for k in
                ("sigma_total_independent", "sigma_total_conservative",
                 "sigma_total_correlated", "total_photons_s",
                 "totals_cover"))
            if checks.get("footer_fields"):
                checks["corr_below_consv"] = (
                    footer["sigma_total_correlated"]
                    <= footer["sigma_total_conservative"] * (1 + 1e-9))
                checks["corr_equals_consv_proportional"] = abs(
                    footer["sigma_total_correlated"]
                    - footer["sigma_total_conservative"]) \
                    <= 1e-6 * footer["sigma_total_conservative"]

        # ---- rejections ---------------------------------------------------
        bad_spec = work / "bad_spec.json"
        spec = json.loads(spec_path.read_text())
        spec["title"] = "tampered"
        bad_spec.write_text(json.dumps(spec))
        r = run(["export-r2s-joint", str(mesh_out), str(bad_spec),
                 str(EMIT_STEP), str(work / "x.ndjson")])
        checks["reject_spec_mismatch"] = r.returncode != 0 and (
            "fingerprint" in r.stderr)

        flux2 = work / "flux2.ndjson"
        fx.write_flux(flux2, [[3.0, 1.0], [1.0, 3.0]])
        spec2 = json.loads(spec_path.read_text())
        spec2["flux"] = {"path": str(flux2), "sha256": fx.fx.sha256(flux2)}
        bad2 = work / "bad_spec2.json"
        bad2.write_text(json.dumps(spec2))
        r = run(["export-r2s-joint", str(mesh_out), str(bad2),
                 str(EMIT_STEP), str(work / "x2.ndjson")])
        checks["reject_flux_mismatch"] = r.returncode != 0

        spec_noq = fx.mesh_spec(fxmap, flux, uncertainty=False)
        noq_path = work / "noq_spec.json"
        noq_path.write_text(json.dumps(spec_noq))
        noq_mesh = work / "noq_mesh.ndjson"
        r = run(["mesh", str(noq_path), str(noq_mesh)])
        if r.returncode == 0:
            r = run(["export-r2s-joint", str(noq_mesh), str(noq_path),
                     str(EMIT_STEP), str(work / "x3.ndjson")])
            checks["reject_unbanded"] = r.returncode != 0
        else:
            checks["reject_unbanded"] = False
            problems.append("unbanded mesh run failed unexpectedly")

        if mesh_out.exists():
            lines = [l for l in mesh_out.read_text().splitlines()
                     if l.strip()]
            trunc = work / "trunc.ndjson"
            trunc.write_text("\n".join(lines[:2]) + "\n")
            r = run(["export-r2s-joint", str(trunc), str(spec_path),
                     str(EMIT_STEP), str(work / "x4.ndjson")])
            checks["reject_truncated"] = r.returncode != 0

    problems.extend(k for k, v in checks.items() if not v)
    result = {"pass": not problems, "checks": checks,
              "problems": problems}
    OUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
