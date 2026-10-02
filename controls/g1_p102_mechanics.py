#!/usr/bin/env python3
"""P102 G1 — mechanics: the fixture r2s-source emits the ALARA directory
with reconcilable structure, and every documented refusal fires with no
partial output.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p102_case as p102  # noqa: E402

RESULT = ROOT / "results/g1_p102_mechanics.json"


def main() -> int:
    checks = {}
    fixture = p102.fixture_r2s()
    cells = [json.loads(l) for l in fixture.splitlines()
             if json.loads(l).get("record") == "cell"]

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        out = td / "alara_out"
        r = p102.run_export_alara(fixture, out, td)
        checks["emits_ok"] = r.returncode == 0 and out.is_dir()
        if not checks["emits_ok"]:
            print("export failed:", r.stderr)

        files = sorted(p for p in out.iterdir() if p.suffix == ".photonSrc")
        checks["file_count_equals_cell_count"] = len(files) == len(cells)
        index = json.loads((out / "actinv-alara-index.json").read_text())

        rows_per_file = []
        group_counts = set()
        for f in files:
            lines = [l for l in f.read_text().splitlines() if l.strip()]
            rows_per_file.append(len(lines))
            toks = lines[0].split("\t")
            checks[f"total_row_{f.name}"] = toks[0] == "TOTAL"
            group_counts.add(len(toks) - 2)
        checks["one_row_per_file"] = all(n == 1 for n in rows_per_file)
        checks["group_count_identical_across_files"] = len(group_counts) == 1
        checks["grid_length_matches_declared"] = (
            len(index["group_centroids_eV"]) == next(iter(group_counts)))

        checks["index_has_required_fields"] = all(
            k in index for k in (
                "emitter", "format", "input_sha256", "step", "step_t_s",
                "shutdown_t_s", "cooling_s", "time_token", "units",
                "group_order", "group_centroids_eV", "cells"))
        checks["index_cells_complete"] = all(
            all(k in c for k in (
                "file", "id", "ordinal", "bounds_cm", "volume_cm3",
                "photons_s", "sigma_photons_s_independent",
                "sigma_photons_s_conservative", "nonzero_groups", "sha256"))
            for c in index["cells"])

        # zero-strength cell writes a row of zeros, not silence
        zero_file = next(f for f in files if "gamma-zero" in f.name)
        zero_vals = zero_file.read_text().split("\t")[2:]
        checks["zero_cell_all_zeros"] = all(
            float(v) == 0.0 for v in zero_vals)

        # ---- refusals, each named, no partial output ----
        def expect_fail(name, *, doc=None, shutdown_t_s=p102.SHUTDOWN_T_S,
                        extra_args=None, check_dir_absent=True):
            out_r = td / f"refuse_{name}"
            r = p102.run_export_alara(doc if doc is not None else fixture,
                                      out_r, td, shutdown_t_s=shutdown_t_s,
                                      extra_args=extra_args)
            ok = r.returncode != 0
            if check_dir_absent:
                ok = ok and not out_r.exists()
            return ok, r.stderr

        ok, msg = expect_fail("no_shutdown_flag", shutdown_t_s=None)
        checks["reject_missing_shutdown_flag"] = ok and "--shutdown-t-s" in msg

        ok, msg = expect_fail("negative_cooling", shutdown_t_s=20000.0)
        checks["reject_negative_cooling"] = ok and "negative" in msg

        ok, msg = expect_fail("unequal_step_t_s",
                              doc=p102.fixture_unequal_step_t_s())
        checks["reject_unequal_step_t_s"] = ok and "step_t_s" in msg

        ok, msg = expect_fail("bad_volume", doc=p102.fixture_bad_volume())
        checks["reject_bad_volume"] = ok and "volume_cm3" in msg

        ok, msg = expect_fail(
            "wrong_schema",
            doc=fixture.replace("actinv-r2s-source-1", "actinv-other-9"))
        checks["reject_wrong_schema"] = ok

        ok, msg = expect_fail(
            "missing_footer",
            doc="\n".join(fixture.splitlines()[:-1]) + "\n")
        checks["reject_missing_footer"] = ok

        # non-empty OUT_DIR: pre-populate, then refuse (dir legitimately
        # exists afterward, so don't check_dir_absent)
        nonempty = td / "refuse_nonempty"
        nonempty.mkdir()
        (nonempty / "stale.txt").write_text("x")
        r = p102.run_export_alara(fixture, nonempty, td)
        checks["reject_nonempty_out_dir"] = (
            r.returncode != 0 and
            sorted(p.name for p in nonempty.iterdir()) == ["stale.txt"])

    evidence = {"schema": "actinv-p102-g1-mechanics-1",
                "pass": all(checks.values()), "checks": checks}
    RESULT.write_text(json.dumps(evidence, indent=1, sort_keys=True) + "\n")
    print(json.dumps(checks, indent=1, sort_keys=True))
    return 0 if evidence["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
