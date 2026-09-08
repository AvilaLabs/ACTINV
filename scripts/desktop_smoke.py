#!/usr/bin/env python3
"""Exercise the actual packaged executable outside its source checkout."""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
from p11_fixtures import make_fixture, specification


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("binary", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--require-render", action="store_true")
    args = parser.parse_args()
    binary, output = args.binary.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="actinv-package-smoke-") as directory:
        work = Path(directory)
        spec = specification(make_fixture(work), mode="trace", cram_order=16)
        spec["options"]["outputs"] += ["photons", "pathways"]
        (work / "problem.json").write_text(json.dumps(spec))
        env = dict(os.environ, ACTINV_GUI_SMOKE_SPEC=str(work / "problem.json"),
                   ACTINV_GUI_SMOKE_OUT=str(work / "out"), ACTINV_GUI_SMOKE_MODEL_ONLY="1")
        model = subprocess.run([str(binary)], cwd=work, env=env, capture_output=True, timeout=120)
        (output / "model.log").write_bytes(model.stdout + model.stderr)
        assert model.returncode == 0 and (work / "out/model-pass.txt").is_file(), "packaged model failed"
        result = json.loads((work / "out/result.json").read_text())
        with (work / "out/inventory.csv").open() as stream:
            rows = list(csv.DictReader(stream))
        inventory = result["steps"][0]["inventory"]
        assert len(rows) == len(inventory)
        assert all(row["nuclide"] == item["nuclide"] and float(row["atoms_per_g"]) == item["atoms_per_g"]
                   for row, item in zip(rows, inventory))
        env.pop("ACTINV_GUI_SMOKE_MODEL_ONLY")
        env.pop("ACTINV_GUI_SMOKE_SPEC")
        env.update(ACTINV_GUI_CAPTURE_DIR=str(output / "captures"),
                   ACTINV_GUI_CAPTURE_RESULT=str(work / "out/result.json"))
        try:
            native = subprocess.run([str(binary)], cwd=work, env=env, capture_output=True, timeout=90)
            native_log, native_code = native.stdout + native.stderr, native.returncode
        except subprocess.TimeoutExpired as error:
            native_log = (error.stdout or b"") + (error.stderr or b"") + b"\nTimed out after 90 seconds"
            native_code = -1
        (output / "native.log").write_bytes(native_log)
        captures = sorted((output / "captures").glob("*.png"))
        rendered = native_code == 0 and len(captures) == 21
        evidence = {
            "launch_target_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
            "model_open_save_data_paths_solve_json_csv": True,
            "native_rendering": rendered, "native_exit_code": native_code,
            "screenshots": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in captures},
            "manual_native_dialogs_and_download_warnings": "not tested by this harness",
        }
        (output / "smoke.json").write_text(json.dumps(evidence, indent=2) + "\n")
        print(json.dumps(evidence, indent=2))
        if args.require_render:
            assert rendered, "native rendering did not complete"


if __name__ == "__main__":
    main()
