#!/usr/bin/env python3
"""P54 G1 mechanics — actinv-clearance-1 emission on the P53/P54 synthetic
fixture in both input shapes (mesh NDJSON and run-result JSON), plus every
rejection path: unbanded run, missing activity output, step out of range,
malformed limits file, bad confidence, unknown flag.
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
import p54_fixture as p54fx  # noqa: E402

BIN = Path(os.environ.get("ACTINV_BIN", ROOT / "target/debug/actinv"))
OUT = ROOT / "results/g1_p54_mechanics.json"
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
    with tempfile.TemporaryDirectory(prefix="p54-g1-", dir="target") as d:
        work = Path(d)
        fxmap = fx.make_fixture(work)
        flux = work / "flux.ndjson"
        fx.write_flux(flux, [[1.0, 1.0], [2.0, 2.0]])
        spec_path = work / "mesh_spec.json"
        spec_path.write_text(json.dumps(
            fx.mesh_spec(fxmap, flux), indent=1))
        mesh_out = work / "mesh.ndjson"
        cl_out = work / "clearance.ndjson"

        r = run(["mesh", str(spec_path), str(mesh_out)])
        checks["mesh_run"] = r.returncode == 0
        if r.returncode != 0:
            problems.append(f"mesh failed: {r.stderr[-800:]}")

        # ---- mesh input -------------------------------------------------
        if checks["mesh_run"]:
            r = run(["clearance", str(mesh_out), str(EMIT_STEP),
                     str(cl_out)])
            checks["clearance_mesh"] = r.returncode == 0
            if r.returncode != 0:
                problems.append(f"clearance failed: {r.stderr[-800:]}")

        if checks.get("clearance_mesh"):
            recs = load_ndjson(cl_out)
            header, cells, footer = recs[0], [
                r for r in recs if r["record"] == "clearance"], recs[-1]
            checks["schema"] = header.get("schema") == "actinv-clearance-1"
            checks["header_fields"] = all(
                k in header for k in ("input_sha256", "step", "limits",
                                      "confidence_threshold",
                                      "probability_model",
                                      "clearance_rule"))
            if "limits" in header:
                checks["limits_fields"] = all(
                    k in header["limits"] for k in
                    ("sha256", "source", "count"))
                checks["limits_count"] = header["limits"]["count"] == 277
            checks["two_cells"] = len(cells) == 2
            checks["cell_fields"] = all(
                all(k in c for k in (
                    "cell_index", "cell_id", "sum_ratio",
                    "sigma_sum_independent", "sigma_sum_conservative",
                    "p_clear_interval", "classification", "clears",
                    "dominant_ratio", "dominant_sigma", "coverage",
                    "nuclides")) for c in cells)
            checks["coverage_fields"] = all(
                all(k in c["coverage"] for k in (
                    "banded_ratio_share", "unbanded_ratio_share",
                    "unregulated_activity_share", "regulated_count"))
                for c in cells)
            checks["classification_valid"] = all(
                c["classification"] in (
                    "deterministic_clear", "deterministic_fail",
                    "clears_certified", "fails_certified",
                    "indeterminate") for c in cells)
            checks["mn56_regulated"] = all(
                any(n["nuclide"] == "Mn56" and n.get("in_table")
                    for n in c["nuclides"]) for c in cells)
            # Mn57 is absent from the IAEA table → unregulated share > 0
            checks["mn57_unregulated"] = all(
                any(n["nuclide"] == "Mn57" and not n.get("in_table")
                    for n in c["nuclides"]) for c in cells)
            checks["unregulated_share_positive"] = all(
                c["coverage"]["unregulated_activity_share"] > 0
                for c in cells)
            checks["footer_fields"] = all(
                k in footer for k in (
                    "cells_evaluated", "cells_certified_clear",
                    "cells_certified_fail", "cells_indeterminate",
                    "cells_deterministic", "totals_cover"))
            checks["footer_count"] = footer.get("cells_evaluated") == 2
            checks["interval_ordered"] = all(
                c["p_clear_interval"][0] <= c["p_clear_interval"][1]
                for c in cells)

        # ---- run-result input -------------------------------------------
        run_spec_path = work / "run_spec.json"
        run_spec_path.write_text(json.dumps(p54fx.run_spec(fxmap)))
        run_out = work / "run_result.json"
        r = run(["run", str(run_spec_path), str(run_out)])
        checks["run_doc"] = r.returncode == 0
        if r.returncode != 0:
            problems.append(f"run failed: {r.stderr[-800:]}")
        if checks["run_doc"]:
            r = run(["clearance", str(run_out), str(EMIT_STEP),
                     str(work / "cl_run.ndjson")])
            checks["clearance_run_doc"] = r.returncode == 0
            if checks["clearance_run_doc"]:
                recs = load_ndjson(work / "cl_run.ndjson")
                checks["run_doc_one_cell"] = len(
                    [r for r in recs if r["record"] == "clearance"]) == 1

        # ---- rejections -------------------------------------------------
        noq_spec = p54fx.run_spec(fxmap, uncertainty=False)
        noq_path = work / "noq_spec.json"
        noq_path.write_text(json.dumps(noq_spec))
        noq_out = work / "noq_result.json"
        r = run(["run", str(noq_path), str(noq_out)])
        if r.returncode == 0:
            r = run(["clearance", str(noq_out), str(EMIT_STEP),
                     str(work / "x1.ndjson")])
            checks["reject_unbanded"] = r.returncode != 0
        else:
            checks["reject_unbanded"] = False
            problems.append("unbanded run failed unexpectedly")

        # a run doc stripped of activity_Bq_per_g must be rejected
        if run_out.exists():
            stripped = json.loads(run_out.read_text())
            for s in stripped["steps"]:
                s.pop("activity_Bq_per_g", None)
            noact_out = work / "noact_result.json"
            noact_out.write_text(json.dumps(stripped))
            r = run(["clearance", str(noact_out), str(EMIT_STEP),
                     str(work / "x2.ndjson")])
            checks["reject_no_activity"] = r.returncode != 0

        r = run(["clearance", str(mesh_out), "99",
                 str(work / "x3.ndjson")])
        checks["reject_bad_step"] = r.returncode != 0

        bad_limits = work / "bad_limits.json"
        bad_limits.write_text(json.dumps({
            "schema": "actinv-clearance-limits-1",
            "limits": {"Fe-55": -1.0}}))
        r = run(["clearance", str(mesh_out), str(EMIT_STEP),
                 "--limits", str(bad_limits), str(work / "x4.ndjson")])
        checks["reject_bad_limits"] = r.returncode != 0

        r = run(["clearance", str(mesh_out), str(EMIT_STEP),
                 "--confidence", "1.5", str(work / "x5.ndjson")])
        checks["reject_bad_confidence"] = r.returncode != 0

        r = run(["clearance", str(mesh_out), str(EMIT_STEP),
                 "--bogus", str(work / "x6.ndjson")])
        checks["reject_unknown_flag"] = r.returncode != 0

        custom_limits = work / "custom_limits.json"
        custom_limits.write_text(json.dumps({
            "schema": "actinv-clearance-limits-1",
            "source": "test override",
            "limits": {"Mn-56": 1.0e9}}))
        r = run(["clearance", str(mesh_out), str(EMIT_STEP),
                 "--limits", str(custom_limits),
                 str(work / "custom.ndjson")])
        checks["accept_custom_limits"] = r.returncode == 0
        if checks["accept_custom_limits"]:
            recs = load_ndjson(work / "custom.ndjson")
            hdr = recs[0]
            checks["custom_sha_recorded"] = (
                hdr["limits"]["source"] == "test override"
                and len(hdr["limits"]["sha256"]) == 64)

    problems.extend(k for k, v in checks.items() if not v)
    result = {"pass": not problems, "checks": checks,
              "problems": problems}
    OUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
