#!/usr/bin/env python3
"""P19 G3 driver: stage the declared evals into one directory and run
`actinv build-shielding` on it.

Arguments (exact positional argv bound by the adapter):
  materials-manifest  JSON [{"material","file"}...] — the declared test set.
  actinv-executable   Path to the actinv binary under test.
  eval slots          One staged ENDF-6 file per declared material.
  groups              JSON group-structure descriptor (passed to --groups).
  output              Workspace path for `shield_table.json`.

The build is the production code path; this driver only stages inputs and
verifies the emitted artifact's schema + declared-material coverage.
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TEST_SET = ("W-186", "Ag-107", "Ta-181", "Nb-93", "U-238", "Fe-56")


def main() -> int:
    manifest = json.loads(Path(sys.argv[1]).read_text())
    actinv = str(Path(sys.argv[2]).resolve())
    groups_path = sys.argv[-2]
    output = Path(sys.argv[-1])
    evals = sys.argv[3:-2]
    by_name = {Path(p).name: p for p in evals}
    materials = manifest["materials"]
    missing = [m["file"] for m in materials if m["file"] not in by_name]
    if missing:
        print(f"missing staged evals: {missing}", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory(prefix="p19-shield-") as work:
        stage = Path(work) / "evals"
        stage.mkdir()
        for entry in materials:
            shutil.copyfile(by_name[entry["file"]], stage / entry["file"])
        proc = subprocess.run(
            [actinv, "build-shielding", str(stage), str(output),
             "--groups", groups_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=3600,
            check=False,
        )
        print(proc.stdout, file=sys.stderr)
        if proc.returncode != 0:
            return proc.returncode or 3
    table = json.loads(output.read_text())
    if table.get("format") != "actinv-shield-table-1":
        print(f"unexpected format {table.get('format')}", file=sys.stderr)
        return 4
    covered = sorted(table.get("nuclides", {}).keys())
    wanted = {m.replace("-", "") for m in TEST_SET}
    absent = wanted - set(covered)
    if absent:
        print(f"declared materials missing from table: {sorted(absent)}",
              file=sys.stderr)
        return 5
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
