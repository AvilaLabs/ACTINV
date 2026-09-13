#!/usr/bin/env python3
"""P25 G4 — bounded repair: rebuild all four staged corpora under the repaired
builder and record per-repair evidence.

The staged population is exactly the P18b candidate construction set:
``target/g4-p18b/inputs-{p}`` (survivors) union ``target/g4-p18b/failed-{p}``
(quarantined). Nothing is added or removed; the P25 staging census proved the
set is complete for the eligible ledger.

Phase 1 builds every staged file individually into a shared checkpoint cache
(resumable); phase 2 runs the aggregate directory build over the passing
files, which applies the cross-source state catalog. Every emitted artifact
index is scanned for the four named P25 repair diagnostics
(``floor_reconciled``, ``interp_artifact_reconciled``,
``missing_total_self_comparator``, ``elfs_qm_qi_conflict_resolved``) and the
record verifies repairs applied only to files whose G2 mechanism class
carries the matching demonstrated defect.

Writes ``results/g4_p25_repairs.json``. Resumable: rerun to continue.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACTINV = Path(os.environ.get("ACTINV_BIN", ROOT / "target/release/actinv"))
P18B = ROOT / "target/g4-p18b"
WORK = ROOT / "target/p25-g4"
OUT = ROOT / "results/g4_p25_repairs.json"

PARAMS = {
    "neutron": ("fispact-709", "293.6"),
    "proton": ("fispact-162", "0"),
    "deuteron": ("fispact-162", "0"),
    "alpha": ("fispact-162", "0"),
}

# named repair diagnostics -> the G2 mechanism classes they may fire for
REPAIR_TOKENS = (
    "floor_reconciled",
    "interp_artifact_reconciled",
    "missing_total_self_comparator",
    "elfs_qm_qi_conflict_resolved",
)
REPAIRABLE_CLASSES = {
    "floor_artifact_only",
    "missing_total_or_grid_contract",
    "grid_density_interpolation_artifact",
}
GENUINE = {
    "genuine_source_inconsistency:zero_total_with_partials",
    "genuine_source_inconsistency:gridpoint_excess",
}


def staged_files(projectile: str) -> list[Path]:
    files = sorted((P18B / f"inputs-{projectile}").glob("*.tendl"))
    files += sorted((P18B / f"failed-{projectile}").glob("*.tendl"))
    return files


def run_build(source: Path, output: Path, projectile: str, groups: str,
              temperature: str, cache: Path, timeout: int = 3600):
    return subprocess.run(
        [
            str(ACTINV), "build-library", str(source), str(output),
            "--format", "tendl", "--projectile", projectile,
            "--groups", groups, "--temperature-K", temperature,
            "--workers", "2", "--cache", str(cache),
        ],
        cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        timeout=timeout,
    )


def build_projectile(projectile: str, jobs: int) -> dict:
    groups, temperature = PARAMS[projectile]
    files = staged_files(projectile)
    cache = WORK / f"cache-{projectile}"
    cache.mkdir(parents=True, exist_ok=True)
    pass_dir = WORK / f"pass-{projectile}"
    pass_dir.mkdir(exist_ok=True)
    failures_path = WORK / f"failures-{projectile}.json"
    failures: dict[str, str] = (
        json.loads(failures_path.read_text()) if failures_path.exists() else {}
    )
    todo = [f for f in files if f.name not in failures
            and not (pass_dir / f.name).exists()]
    started = time.monotonic()

    def attempt(source: Path):
        scratch = Path(tempfile.mkdtemp(
            dir=ROOT / "target/preflight-tmp", prefix=f"g4p25-{source.stem}-"))
        try:
            return source, run_build(
                source, scratch / "one.npz", projectile, groups,
                temperature, cache, timeout=3600)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

    done = len(files) - len(todo)
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        for source, completed in pool.map(attempt, todo):
            done += 1
            if completed.returncode == 0:
                os.link(source, pass_dir / source.name)
            else:
                line = (completed.stdout.strip().splitlines()[-1]
                        if completed.stdout.strip()
                        else f"<exit {completed.returncode} no output>")
                failures[source.name] = line[:600]
                failures_path.write_text(json.dumps(
                    failures, indent=1, sort_keys=True))
            if done % 10 == 0 or completed.returncode != 0:
                print(f"{projectile} [{done}/{len(files)}] {source.name}: "
                      f"{'ok' if completed.returncode == 0 else 'FAIL'}",
                      flush=True)

    # phase 2: aggregate build over passing files (cross-source catalog)
    output = WORK / f"candidate-{projectile}.npz"
    completed = run_build(pass_dir, output, projectile, groups,
                          temperature, cache)
    if completed.returncode != 0:
        raise RuntimeError(
            f"{projectile}: aggregate build failed:\n{completed.stdout[-3000:]}")

    # P18b-shaped build report so the frozen scorers can consume the new
    # workspace unchanged; built_names records the exact shipped set.
    br_path = WORK / "build_report.json"
    br = json.loads(br_path.read_text()) if br_path.exists() else []
    br = [e for e in br if e.get("projectile") != projectile]
    br.append({
        "projectile": projectile,
        "built_files": len(list(pass_dir.glob("*.tendl"))),
        "built_names": sorted(p.name for p in pass_dir.glob("*.tendl")),
        "quarantined": failures,
        "output": str(output),
        "output_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "index": str(output.with_name(f"{output.stem}_index.json")),
    })
    br_path.write_text(json.dumps(br, indent=1) + "\n")

    index = json.loads(
        output.with_name(f"{output.stem}_index.json").read_text())
    repair_files: dict[str, set] = {t: set() for t in REPAIR_TOKENS}
    for target in index.get("targets", []):
        for line in target.get("ledger", []):
            for token in REPAIR_TOKENS:
                if token in line:
                    repair_files[token].add(Path(target["file"]).name)
    return {
        "projectile": projectile,
        "elapsed_s": round(time.monotonic() - started, 3),
        "staged_files": len(files),
        "built_files": len(list(pass_dir.glob("*.tendl"))),
        "failures": failures,
        "repair_diagnostics": {
            t: sorted(v) for t, v in repair_files.items()},
        "output": str(output),
        "output_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "index": str(output.with_name(f"{output.stem}_index.json")),
        "index_emission_model": index.get("emission_model"),
    }


def main() -> int:
    jobs = int(os.environ.get("G4_BUILD_JOBS", "2"))
    (ROOT / "target/preflight-tmp").mkdir(parents=True, exist_ok=True)
    traces = json.loads(
        (ROOT / "results/g2_p25_traces.json").read_text())
    record = json.loads(OUT.read_text()) if OUT.exists() else {}
    record.update({
        "schema": "actinv-p25-g4-repairs-1",
        "gate": "P25-G4",
        "projectiles": {},
    })
    projs = sys.argv[1:] or list(PARAMS)
    for projectile in projs:
        result = build_projectile(projectile, jobs)
        record["projectiles"][projectile] = result
        print(f"{projectile}: built {result['built_files']}/"
              f"{result['staged_files']}, {len(result['failures'])} failed, "
              f"{result['elapsed_s']} s", flush=True)
        OUT.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")

    # class-application verification: every file repaired must carry a
    # demonstrated repairable class; every repaired diagnostic must be
    # consistent with the file's G2 final class.
    verdict = {}
    for projectile, res in record["projectiles"].items():
        files = traces["files"][projectile]
        repaired = [n for n in files if n not in res["failures"]]
        still_failed = set(res["failures"])
        mismatches = []
        for name in repaired:
            cls = files[name]["final_class"]
            if cls not in REPAIRABLE_CLASSES:
                mismatches.append((name, cls))
        for token, names in res["repair_diagnostics"].items():
            for name in names:
                cls = files.get(name, {}).get("final_class")
                if cls in GENUINE:
                    mismatches.append((name, token, cls))
        verdict[projectile] = {
            "repaired_quarantined": len(repaired),
            "still_quarantined": sorted(still_failed),
            "still_quarantined_classes": dict(Counter(
                files[n]["final_class"] for n in still_failed
                if n in files)),
            "class_application_mismatches": mismatches,
        }
    record["class_application"] = verdict
    OUT.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
