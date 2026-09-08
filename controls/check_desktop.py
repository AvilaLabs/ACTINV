#!/usr/bin/env python3
"""Independent CLI/desktop parity control; see protocols/desktop-interface-v1.md."""
import csv
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from p11_fixtures import make_fixture, specification

ROOT = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix="actinv-desktop-") as directory:
    work = Path(directory)
    spec = specification(make_fixture(work), mode="trace", cram_order=16)
    spec["options"]["outputs"] += ["photons", "pathways"]
    problem = work / "problem.json"
    problem.write_text(json.dumps(spec))
    env = dict(os.environ, ACTINV_DESKTOP_CONTROL_SPEC=str(problem),
               ACTINV_DESKTOP_CONTROL_RESULT=str(work / "desktop.json"))
    subprocess.run(["cargo", "test", "-p", "actinv-gui", "model::integration_control::desktop_worker_preserves_solver_output",
                    "--", "--ignored", "--exact"], cwd=ROOT, env=env, check=True)
    subprocess.run(["cargo", "run", "-p", "actinv-cli", "--bin", "actinv", "--", "run", str(problem), str(work / "cli.json")], cwd=ROOT, check=True)
    desktop = json.loads((work / "desktop.json").read_text())
    cli = json.loads((work / "cli.json").read_text())
    keys = ["steps", "pathways", "ledger", "mode", "total_states", "pruned_states"]
    checks = {key: desktop[key] == cli[key] for key in keys}
    # Certificates are compared in full apart from explicit interface labels.
    def certificate(value):
        if isinstance(value, dict):
            return {k: certificate(v) for k, v in value.items() if k not in ("entry_point", "entrypoint")}
        if isinstance(value, list):
            return [certificate(v) for v in value]
        return value
    checks["certificate"] = certificate(desktop["certificate"]) == certificate(cli["certificate"])
    with (work / "desktop.csv").open() as stream:
        rows = list(csv.DictReader(stream))
    expected = desktop["steps"][0]
    checks["csv"] = len(rows) == len(expected["inventory"]) and all(
        row["nuclide"] == nuclide["nuclide"]
        and int(row["step"]) == 1 and float(row["time_s"]) == expected["t_s"]
        and float(row["atoms_per_g"]) == nuclide["atoms_per_g"]
        and float(row["activity_Bq_per_g"]) == expected["activity_Bq_per_g"].get(row["nuclide"], 0.0)
        for row, nuclide in zip(rows, expected["inventory"])
    )
    assert all(checks.values()), checks
    evidence = {"protocol_sha256": hashlib.sha256((ROOT / "protocols/desktop-interface-v1.md").read_bytes()).hexdigest(),
                "fixture": "existing P11 synthetic trace CRAM-16 with photons and pathways", "checks": checks, "pass": True}
    (ROOT / "results/desktop-interface-v1.json").write_text(json.dumps(evidence, indent=2) + "\n")
    print(json.dumps(evidence, indent=2))
