#!/usr/bin/env python3
"""P15 G4: exact CLI/Python warm-cache identity and bounded Python memory."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results/p15_interfaces.json"
ACTINV = Path(os.environ.get("ACTINV_BIN", ROOT / "target/release/actinv"))
PYTHON = Path(os.environ.get("ACTINV_PYTHON", sys.executable))
PYTHON_LIBRARY = Path(
    os.environ.get("ACTINV_PYTHON_LIBRARY", ROOT / "python/target/release/libactinv.so")
)
DATA = Path(os.environ.get("ACTINV_DATA_ROOT", "/home/connoravila/nuclear-data"))
LIBRARY = Path(
    os.environ.get("ACTINV_LIBRARY", DATA / "tendl-2025/builds/full/neutron.n.p10.npz")
)
DECAY_PRIMARY = Path(
    os.environ.get(
        "ACTINV_ENDF_DECAY", DATA / "endfb-viii.0-decay/bulk/endf-b-viii-0_decay.dat"
    )
)
DECAY_FALLBACK = Path(
    os.environ.get("ACTINV_JEFF_DECAY", DATA / "jeff-3.3-decay/bulk/jeff-3-3_decay.dat")
)
LIBRARY_SHA256 = "ec4c72bf598dc8ad3d533d9cfafdcf493e2d1f949a3e4db6251495659b68cc44"
THREADS = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "RAYON_NUM_THREADS",
)


def python_run(spec: Path, output: Path, extension: Path) -> None:
    module_spec = importlib.util.spec_from_file_location("actinv", extension)
    if module_spec is None or module_spec.loader is None:
        raise RuntimeError(f"cannot load Python extension {extension}")
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    result = module.run(spec.read_text(encoding="utf-8"))
    output.write_text(result, encoding="utf-8")


def environment(cache: Path) -> dict[str, str]:
    value = os.environ.copy()
    value["ACTINV_CACHE_DIR"] = str(cache)
    for name in THREADS:
        value[name] = "1"
    return value


def timed(arguments: list[str | Path], cache: Path) -> int:
    completed = subprocess.run(
        ["/usr/bin/time", "-v", *(str(value) for value in arguments)],
        cwd=ROOT,
        env=environment(cache),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=120,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(f"timed command failed: {completed.stderr[-4000:]}")
    match = re.search(r"Maximum resident set size \(kbytes\):\s*(\d+)", completed.stderr)
    if match is None:
        raise RuntimeError("GNU time did not report peak RSS")
    return int(match.group(1)) * 1024


def normalized(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    value.pop("ms", None)
    value["entry_point"] = "normalized"
    value["certificate"]["entry_point"] = "normalized"
    return value


def canonical_sha256(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def cache_inventory(cache: Path) -> list[dict[str, object]]:
    records = []
    for path in sorted(path for path in cache.rglob("*") if path.is_file()):
        records.append(
            {
                "relative_path": path.relative_to(cache).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    return records


def main() -> None:
    if len(sys.argv) == 5 and sys.argv[1] == "--python-run":
        python_run(Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4]))
        return
    with tempfile.TemporaryDirectory(prefix="actinv-p15-g4-") as directory:
        work = Path(directory)
        specification = json.loads((ROOT / "examples/fns_fe_5min.json").read_text(encoding="utf-8"))
        specification["library"] = {"path": str(LIBRARY), "sha256": LIBRARY_SHA256}
        specification["decay"] = {
            "primary": str(DECAY_PRIMARY),
            "fallback": str(DECAY_FALLBACK),
        }
        spec = work / "public.json"
        spec.write_text(json.dumps(specification, sort_keys=True) + "\n", encoding="utf-8")
        cache = work / "cache"
        cold = work / "cold.json"
        completed = subprocess.run(
            [str(ACTINV), "run", str(spec), str(cold)],
            cwd=ROOT,
            env=environment(cache),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
            check=False,
        )
        if completed.returncode:
            raise RuntimeError(completed.stderr[-4000:])
        before_python = cache_inventory(cache)

        cli_output = work / "cli.json"
        python_output = work / "python.json"
        cli_peak_rss = timed([ACTINV, "run", spec, cli_output], cache)
        python_peak_rss = timed(
            [PYTHON, Path(__file__), "--python-run", spec, python_output, PYTHON_LIBRARY], cache
        )
        after_python = cache_inventory(cache)
        cli_raw = json.loads(cli_output.read_text(encoding="utf-8"))
        python_raw = json.loads(python_output.read_text(encoding="utf-8"))
        cli = normalized(cli_output)
        python = normalized(python_output)
        certificate = python["certificate"]["inputs"]
        checks = {
            "normalized_result_exact": cli == python,
            "entry_points_exact": [cli_raw["entry_point"], python_raw["entry_point"]]
            == ["cli", "python"],
            "certificate_library_source_preserved": certificate["library"]["path"]
            == str(LIBRARY)
            and certificate["library"]["sha256"] == LIBRARY_SHA256,
            "python_created_no_second_cache": before_python == after_python,
            "python_peak_below_512_mib": python_peak_rss <= 512 * 1024**2,
            "python_overhead_below_192_mib": python_peak_rss <= cli_peak_rss + 192 * 1024**2,
        }
        evidence = {
            "schema": "actinv-p15-interface-identity-1",
            "cli_peak_rss_bytes": cli_peak_rss,
            "python_peak_rss_bytes": python_peak_rss,
            "python_minus_cli_peak_rss_bytes": python_peak_rss - cli_peak_rss,
            "normalized_result_sha256": {
                "cli": canonical_sha256(cli),
                "python": canonical_sha256(python),
            },
            "cache": after_python,
            "checks": checks,
            "pass": all(checks.values()),
        }
    RESULT.write_text(json.dumps(evidence, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(evidence, indent=1, sort_keys=True))
    if not evidence["pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
