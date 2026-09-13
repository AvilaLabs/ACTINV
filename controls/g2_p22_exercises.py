#!/usr/bin/env python3
"""P22-G2: install, memory, runtime and mesh exercises on the candidate.

Legs:

- ``first_use`` — re-runs the committed ``cb1_first_use.py`` unchanged
  (only ``RESULT`` is redirected): the public ``actinv==1.0.0`` PyPI wheel
  must still download, byte-match the release record, install, import and
  run; the pinned ALARA source must still build and pass its sample.
- ``performance`` — re-runs the committed ``cb1_performance.py``
  unchanged. Its internal version guard literally pins ``actinv 1.0.0``,
  so ``ACTINV_BIN`` is pointed at a fresh build of the ``v1.0.0`` tag:
  the process-level legs re-verify the released artifact's envelope on
  today's machine (the frozen 2x/1.25x machine-noise bands), while the
  in-process kernel legs measure the candidate Python module loaded from
  ``python/target/release/libactinv.so``.
- ``v100_build`` — ``git clone`` + ``cargo build --release`` of the
  ``v1.0.0`` tag that feeds the performance leg.
- ``clean_clone`` — ``git clone`` of the head commit and
  ``cargo build --release``; ``--version`` must run and report the
  candidate version. The open-source build claim re-executed.
- ``mesh_1k`` — a fresh 1,000-cell reduced-field mesh run on the pinned
  TENDL-2025 709-group library at ``chunk_cells=64``, ``threads=2``: the
  footer must close and peak RSS must stay within ``1.25x`` the
  P21-recorded 1,000-cell leg (402,251,776 bytes).

Writes ``results/g2_p22_exercises.json``.
"""
from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import traceback


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results/g2_p22_exercises.json"
RESULTS = ROOT / "results"
CONTROLS = ROOT / "controls"
WORK = ROOT / "target" / "p22-g2"

sys.path.insert(0, str(CONTROLS))

import g3_p21_executed as p21  # noqa: E402

MESH_RSS_BOUND = int(402_251_776 * 1.25)
PERF_MEDIAN_BAND = 2.0
PERF_RSS_BAND = 1.25

PROCESS_MEDIAN_PATHS = (
    ("actinv_process_startup", None),
    ("actinv_public_example_warm_cache", None),
    ("identical_data_standalone", "actinv_1_0_0_full_diagnostics"),
    ("identical_data_standalone", "alara_2_9_2_number_density"),
)


def sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def run(arguments: list[str], cwd: Path, env_extra: dict[str, str] | None = None,
        timeout: int = 3600) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(env_extra or {})
    return subprocess.run(
        arguments, cwd=cwd, env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=timeout, check=False,
    )


def clone_build(commit: str, src: Path, target: Path, label: str) -> dict:
    if not src.exists():
        clone = run(
            ["git", "clone", "--no-hardlinks", str(ROOT), str(src)], cwd=WORK
        )
        if clone.returncode != 0:
            raise RuntimeError(f"{label} clone failed: {clone.stderr[-1000:]}")
        checkout = run(["git", "checkout", "--detach", commit], cwd=src)
        if checkout.returncode != 0:
            raise RuntimeError(f"{label} checkout failed: {checkout.stderr[-1000:]}")
    binary = target / "release" / "actinv"
    if not binary.exists():
        build = run(
            ["cargo", "build", "--release", "--locked"],
            cwd=src,
            env_extra={
                "CARGO_TARGET_DIR": str(target),
                "CARGO_BUILD_JOBS": "1",
            },
            timeout=7200,
        )
        if build.returncode != 0:
            raise RuntimeError(f"{label} build failed: {build.stderr[-2000:]}")
    version = run([str(binary), "--version"], cwd=WORK)
    return {
        "commit": commit,
        "source": str(src.relative_to(ROOT)),
        "target_dir": str(target.relative_to(ROOT)),
        "binary_sha256": sha256(binary),
        "binary_bytes": binary.stat().st_size,
        "version_stdout": version.stdout.strip(),
        "version_returncode": version.returncode,
    }


def rerun(module_name: str, out_name: str, env_extra: dict[str, str]) -> dict:
    """Import the committed control unchanged; redirect only RESULT.

    The environment is applied before import because the CB1 modules
    resolve ``ACTINV_BIN`` and friends at module scope.
    """
    saved = {key: os.environ.get(key) for key in env_extra}
    os.environ.update(env_extra)
    if module_name in sys.modules:
        raise RuntimeError(
            f"{module_name} is already imported; module-level constants are fixed"
        )
    module = importlib.import_module(module_name)
    target = RESULTS / out_name
    module.RESULT = target
    run = {
        "module": f"controls/{module_name}.py",
        "module_sha256": sha256(CONTROLS / f"{module_name}.py"),
        "out": f"results/{out_name}",
        "env": env_extra,
    }
    try:
        module.main()
        code = 0
    except SystemExit as exit_:
        code = exit_.code if isinstance(exit_.code, int) else (0 if exit_.code is None else 1)
    except Exception:
        run["returncode"] = None
        run["traceback"] = traceback.format_exc()[-2000:]
        return run
    finally:
        for key, prior in saved.items():
            if prior is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = prior
    run["returncode"] = code
    run["written"] = target.exists()
    return run


def first_use_leg() -> dict:
    run_record = rerun("cb1_first_use", "p22_g2_first_use.json", {})
    out_path = RESULTS / "p22_g2_first_use.json"
    leg = {"run": run_record, "pass": False}
    if out_path.exists():
        candidate = json.loads(out_path.read_text(encoding="utf-8"))
        sealed = json.loads((RESULTS / "cb1_first_use.json").read_text(encoding="utf-8"))
        leg["checks"] = candidate.get("checks")
        leg["sealed_checks"] = sealed.get("checks")
        leg["actinv_install_seconds"] = (
            candidate.get("ACTINV", {}).get("clean_environment", {}).get("seconds")
        )
        leg["pass"] = candidate.get("pass") is True
    return leg


def performance_leg(v100_binary: Path) -> dict:
    env = {name: "1" for name in (
        "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS", "RAYON_NUM_THREADS",
    )}
    env["ACTINV_BIN"] = str(v100_binary)
    # ALARA's input lexer reads the dump_file path into char token[64]
    # unbounded; only the short /tmp/<mkdtemp> path shape (as in the sealed
    # CB1 run) survives — a long repo TMPDIR smashes the token buffer.
    env["TMPDIR"] = "/tmp"
    run_record = rerun("cb1_performance", "p22_g2_performance.json", env)
    out_path = RESULTS / "p22_g2_performance.json"
    leg = {"run": run_record, "pass": False, "comparisons": {}}
    if not out_path.exists():
        return leg
    candidate = json.loads(out_path.read_text(encoding="utf-8"))
    sealed = json.loads((RESULTS / "cb1_performance.json").read_text(encoding="utf-8"))

    def dig(obj, top, sub):
        node = obj.get(top) or {}
        return node.get(sub) if sub else node

    ok = candidate.get("pass") is True
    for top, sub in PROCESS_MEDIAN_PATHS:
        label = f"{top}.{sub}" if sub else top
        cnode, snode = dig(candidate, top, sub), dig(sealed, top, sub)
        entry = {}
        for metric, band in (("median_ms", PERF_MEDIAN_BAND),
                             ("peak_rss_bytes", PERF_RSS_BAND)):
            cval, sval = (cnode or {}).get(metric), (snode or {}).get(metric)
            try:
                ratio = float(cval) / float(sval)
            except (TypeError, ValueError, ZeroDivisionError):
                ratio = None
            entry[metric] = {
                "candidate": cval, "sealed": sval, "ratio": ratio,
                "within_band": ratio is not None and ratio <= band,
            }
            if not entry[metric]["within_band"]:
                ok = False
        leg["comparisons"][label] = entry
    kernels = {}
    for crow, srow in zip(
        candidate.get("identical_operator_cram48") or [],
        sealed.get("identical_operator_cram48") or [],
    ):
        key = f"states={crow.get('states')}"
        kernels[key] = {
            impl: {
                "candidate_median_ms": (crow.get(impl) or {}).get("median_ms"),
                "sealed_median_ms": (srow.get(impl) or {}).get("median_ms"),
            }
            for impl in ("actinv_pyo3_cram48", "openmc_python_cram48")
        }
    leg["kernel_medians_informational"] = kernels
    leg["candidate_implementations"] = candidate.get("implementations")
    leg["pass"] = ok
    return leg


def mesh_leg(work: Path) -> dict:
    work.mkdir(parents=True, exist_ok=True)
    canonical = work / "flux-1000.ndjson"
    if not canonical.exists():
        p21.write_canonical_flux(canonical, p21.distinct_spectra(1_000))
    spec = p21.compact_spec(canonical)
    record = p21.run_mesh(spec, work, "p22-mesh-1k", timeout=3_600)
    record["closed_footer"] = record["cells"] == 1_000
    record["rss_bound_bytes"] = MESH_RSS_BOUND
    record["rss_within_bound"] = record["peak_rss_bytes"] <= MESH_RSS_BOUND
    record["pass"] = bool(record["closed_footer"] and record["rss_within_bound"])
    return record


def main() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
        stdout=subprocess.PIPE, check=True,
    ).stdout.strip()
    v100_commit = subprocess.run(
        ["git", "rev-parse", "v1.0.0^{commit}"], cwd=ROOT, text=True,
        stdout=subprocess.PIPE, check=True,
    ).stdout.strip()

    legs = {}
    try:
        legs["v100_build"] = clone_build(
            v100_commit, WORK / "src-v1.0.0", WORK / "target-v1.0.0", "v1.0.0"
        )
    except Exception:
        legs["v100_build"] = {"pass": False, "traceback": traceback.format_exc()[-2000:]}

    v100_binary = WORK / "target-v1.0.0" / "release" / "actinv"
    if v100_binary.exists() and legs["v100_build"].get("version_returncode") == 0:
        try:
            legs["performance"] = performance_leg(v100_binary)
        except Exception:
            legs["performance"] = {"pass": False, "traceback": traceback.format_exc()[-2000:]}
    else:
        legs["performance"] = {"pass": False, "error": "v1.0.0 binary unavailable"}

    try:
        legs["first_use"] = first_use_leg()
    except Exception:
        legs["first_use"] = {"pass": False, "traceback": traceback.format_exc()[-2000:]}

    try:
        legs["clean_clone"] = clone_build(
            head, WORK / "src-head", WORK / "target-head", "head"
        )
        legs["clean_clone"]["pass"] = bool(
            legs["clean_clone"]["version_returncode"] == 0
            and legs["clean_clone"]["version_stdout"].startswith("actinv ")
        )
    except Exception:
        legs["clean_clone"] = {"pass": False, "traceback": traceback.format_exc()[-2000:]}

    try:
        legs["mesh_1k"] = mesh_leg(WORK / "mesh")
    except Exception:
        legs["mesh_1k"] = {"pass": False, "traceback": traceback.format_exc()[-2000:]}

    legs["v100_build"]["pass"] = bool(
        legs["v100_build"].get("version_stdout") == "actinv 1.0.0"
        and legs["v100_build"].get("version_returncode") == 0
    )

    record = {
        "schema": "actinv-p22-g2-exercises-1",
        "mesh_rss_bound_bytes": MESH_RSS_BOUND,
        "perf_median_band": PERF_MEDIAN_BAND,
        "perf_rss_band": PERF_RSS_BAND,
        "hardware": p21.hardware_record(),
        "legs": legs,
        "pass": all(bool(leg.get("pass")) for leg in legs.values()),
    }
    RESULT.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(
        {"pass": record["pass"], "legs": {n: l.get("pass") for n, l in legs.items()}},
        indent=2,
    ))
    raise SystemExit(0 if record["pass"] else 1)


if __name__ == "__main__":
    main()
