#!/usr/bin/env python3
"""P22-G4: release-candidate assembly on green G1–G3 evidence.

Executed only after the G1, G2 and G3 check records exist and carry
``pass: true``. Steps:

1. Bump the workspace version ``1.0.1`` → ``1.1.0`` (idempotent; the
   measured G1–G3 evidence stays bound to the pre-bump candidate whose
   digest is recorded by ``results/g0_p22_seals.json``).
2. Rebuild the release binary (``target/release/actinv``), the Python
   extension (``python/target/release/libactinv.so``) and the wheel
   (``python/target/wheels/*.whl`` via maturin).
3. Re-run the frozen four-surface normalized-identity battery under the
   amended two-stage gate (``protocols/ACTINV-P22_AMENDMENT_A.md``): the
   hash-pinned pre-bump artifact must reproduce the committed P21 baseline
   exactly, and the rebuilt ``1.1.0`` artifacts must equal it after
   normalizing only ``certificate.solver`` — the documented solver-semver
   leaf — proving the bump is solver-inert.
4. Emit the release decision: ``release_ready`` iff every prior verdict
   file is present (``P18b-FAIL`` included), the G1/G2 comparisons hold,
   the held-out metrics reproduced, and the RC artifacts built and
   identity-proved. Tagging and publishing stay separate maintainer
   actions.

Writes ``results/g4_p22_release_candidate.json``.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results/g4_p22_release_candidate.json"
BASELINE = ROOT / "results/g0_p21_identity_baseline.json"
RESULTS = ROOT / "results"

sys.path.insert(0, str(ROOT / "controls"))

from g0_p21_battery import (  # noqa: E402
    EXAMPLE, GROUPS_JSON, LIBRARY, PYTHON_LIBRARY,
    canonical_sha256, environment, mesh_specification, normalized,
    run_cli, run_python, sha256,
)
from g0_p22_seals import EXPECTED_VERDICTS  # noqa: E402

FROM_VERSION = "1.0.1"
TO_VERSION = "1.1.0"

BUMP_FILES = [
    "Cargo.toml",
    "crates/actinv-cli/Cargo.toml",
    "crates/actinv-core/Cargo.toml",
    "crates/actinv-gui/Cargo.toml",
    "python/Cargo.toml",
    "python/pyproject.toml",
]



CHECK_RECORDS = {
    "g1": "results/g1_p22_check.json",
    "g2": "results/g2_p22_check.json",
    "g3": "results/g3_p22_check.json",
}


def command(arguments: list[str], cwd: Path, env_extra: dict | None = None,
            timeout: int = 3600) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(env_extra or {})
    return subprocess.run(
        [str(a) for a in arguments], cwd=cwd, env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=timeout, check=False,
    )


def apply_bump() -> dict:
    files = {}
    for rel in BUMP_FILES:
        path = ROOT / rel
        before = sha256(path)
        text = path.read_text(encoding="utf-8")
        changed = (
            text.replace('version = "1.0.1"', f'version = "{TO_VERSION}"')
            .replace('version = "=1.0.1"', f'version = "={TO_VERSION}"')
            .replace('version = "= 1.0.1"', f'version = "={TO_VERSION}"')
        )
        if changed != text:
            path.write_text(changed, encoding="utf-8")
        files[rel] = {"before_sha256": before, "after_sha256": sha256(path),
                      "changed": changed != text}
    return files


def build_artifacts() -> dict:
    # The version bump changes workspace member versions; refresh both lockfiles
    # offline so the subsequent --locked builds verify a committed-consistent lock.
    for lock_root in (ROOT, ROOT / "python"):
        refresh = command(
            ["cargo", "update", "--workspace", "--offline"], cwd=lock_root,
            env_extra={"CARGO_BUILD_JOBS": "1"}, timeout=600,
        )
        if refresh.returncode != 0:
            raise RuntimeError(
                f"cargo lockfile refresh failed in {lock_root}: "
                f"{refresh.stderr[-2000:]}"
            )
    build = command(
        ["cargo", "build", "--release", "--locked"], cwd=ROOT,
        env_extra={"CARGO_BUILD_JOBS": "1"}, timeout=7200,
    )
    if build.returncode != 0:
        raise RuntimeError(f"cargo build failed: {build.stderr[-2000:]}")
    py_build = command(
        ["cargo", "build", "--release", "--locked"], cwd=ROOT / "python",
        env_extra={"CARGO_BUILD_JOBS": "1"}, timeout=7200,
    )
    if py_build.returncode != 0:
        raise RuntimeError(f"python build failed: {py_build.stderr[-2000:]}")
    wheel = command(
        [sys.executable, "-m", "maturin", "build", "--release"],
        cwd=ROOT / "python", timeout=3600,
    )
    wheels = sorted((ROOT / "python" / "target" / "wheels").glob("*.whl")) \
        if (ROOT / "python" / "target" / "wheels").exists() else []
    binary = ROOT / "target" / "release" / "actinv"
    module = ROOT / "python" / "target" / "release" / "libactinv.so"
    version = command([str(binary), "--version"], cwd=ROOT)
    return {
        "cli_binary": {
            "path": "target/release/actinv",
            "sha256": sha256(binary),
            "bytes": binary.stat().st_size,
            "version_stdout": version.stdout.strip(),
            "version_returncode": version.returncode,
        },
        "python_module": {
            "path": "python/target/release/libactinv.so",
            "sha256": sha256(module),
            "bytes": module.stat().st_size,
        },
        "wheel": {
            "maturin_returncode": wheel.returncode,
            "artifacts": [
                {"name": w.name, "sha256": sha256(w), "bytes": w.stat().st_size}
                for w in wheels
            ],
        },
        "cargo_lock_sha256": sha256(ROOT / "Cargo.lock"),
        "python_lock_sha256": sha256(ROOT / "python" / "Cargo.lock"),
    }


def solver_normalized(result: dict) -> dict:
    """Normalize the documented solver-semver leaf (P22 Amendment A)."""
    value = dict(result)
    if isinstance(value.get("certificate"), dict):
        certificate = dict(value["certificate"])
        certificate["solver"] = "normalized"
        value["certificate"] = certificate
    return value


def run_surfaces(binary: Path, pylib: Path | None) -> dict:
    """Run the frozen four-surface battery against one binary/module pair."""
    import g0_p21_battery as battery  # noqa: PLC0415
    battery.ACTINV = binary
    if pylib is not None:
        battery.PYTHON_LIBRARY = pylib

    specification = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    if sha256(LIBRARY) != specification["library"]["sha256"]:
        raise RuntimeError("library sha256 does not match the example pin")
    for role in ("primary", "fallback"):
        specification["decay"][role] = str(ROOT / specification["decay"][role])
    specification["library"]["path"] = str(LIBRARY)

    with tempfile.TemporaryDirectory(prefix="actinv-p22-g4-", dir=ROOT / "target") as d:
        work = Path(d)
        cache = work / "cache"
        spec_path = work / "problem.json"
        spec_text = json.dumps(specification, sort_keys=True) + "\n"
        spec_path.write_text(spec_text, encoding="utf-8")
        env = environment(cache)

        cli_cold = normalized(run_cli(spec_path, work / "cli_cold.json", env))
        cli_warm = normalized(run_cli(spec_path, work / "cli_warm.json", env))
        python_result = normalized(run_python(spec_text, env))

        fluxes = work / "fluxes"
        values = specification["spectrum"]["flux_per_group"]
        lines = [
            " ".join(str(v) for v in values[i : i + 6])
            for i in range(0, len(values), 6)
        ]
        fluxes.write_text(
            "\n".join(lines) + "\n0.5\nP22 identity battery cell\n", encoding="utf-8"
        )
        canonical_flux = work / "flux.ndjson"
        completed = subprocess.run(
            [str(binary), "import-flux", "fispact",
             str(fluxes), str(canonical_flux), "--groups", str(GROUPS_JSON)],
            cwd=ROOT, env=env, text=True, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=120, check=False,
        )
        if completed.returncode:
            raise RuntimeError(f"import-flux failed: {completed.stderr[-4000:]}")
        mesh_spec = work / "mesh.json"
        mesh_spec.write_text(
            json.dumps(mesh_specification(specification, canonical_flux), sort_keys=True)
            + "\n", encoding="utf-8",
        )
        mesh_out = work / "mesh_result.ndjson"
        completed = subprocess.run(
            [str(binary), "mesh", str(mesh_spec), str(mesh_out)],
            cwd=ROOT, env=env, text=True, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=300, check=False,
        )
        if completed.returncode:
            raise RuntimeError(f"actinv mesh failed: {completed.stderr[-4000:]}")
        cells = [
            normalized(json.loads(line)["result"])
            for line in mesh_out.read_text(encoding="utf-8").splitlines()
            if json.loads(line).get("record") == "cell"
        ]
        if len(cells) != 1:
            raise RuntimeError(f"expected one mesh cell result, got {len(cells)}")

    return {
        "cli_cold": cli_cold,
        "cli_warm": cli_warm,
        "python": python_result,
        "mesh_cell": cells[0],
    }


def pre_bump_python_extension(clone_src: Path) -> Path:
    """Build the 1.0.1 Python extension inside the G2 clean clone."""
    pydir = clone_src / "python"
    build = command(
        ["cargo", "build", "--release", "--locked"], cwd=pydir,
        env_extra={"CARGO_BUILD_JOBS": "1"}, timeout=7200,
    )
    if build.returncode != 0:
        raise RuntimeError(f"pre-bump python build failed: {build.stderr[-2000:]}")
    module = pydir / "target" / "release" / "libactinv.so"
    if not module.exists():
        raise RuntimeError(f"pre-bump python module missing: {module}")
    return module


def surface_battery() -> dict:
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    expected = baseline["normalized_result_sha256"]

    # Stage 1 (P22 Amendment A): the hash-pinned pre-bump artifact from the G2
    # clean clone must reproduce the G0 baseline exactly — proving the baseline
    # still holds in the current environment.
    g2 = json.loads((RESULTS / "g2_p22_exercises.json").read_text(encoding="utf-8"))
    clone_leg = g2["legs"]["clean_clone"]
    clone_binary = ROOT / clone_leg["target_dir"] / "release" / "actinv"
    if sha256(clone_binary) != clone_leg["binary_sha256"]:
        raise RuntimeError("pre-bump binary sha256 differs from the G2 record")
    clone_pylib = pre_bump_python_extension(ROOT / clone_leg["source"])

    pre = run_surfaces(clone_binary, clone_pylib)
    pre_raw = {k: canonical_sha256(v) for k, v in pre.items()}
    pre_exact = {k: pre_raw[k] == expected[k] for k in expected}

    # Stage 2: the rebuilt 1.1.0 artifacts must equal the stage-1 results after
    # normalizing exactly certificate.solver (the documented solver-semver leaf).
    post = run_surfaces(ROOT / "target/release/actinv", PYTHON_LIBRARY)
    post_raw = {k: canonical_sha256(v) for k, v in post.items()}
    pre_norm = {k: canonical_sha256(solver_normalized(v)) for k, v in pre.items()}
    post_norm = {k: canonical_sha256(solver_normalized(v)) for k, v in post.items()}
    norm_match = {k: pre_norm[k] == post_norm[k] for k in pre_norm}

    return {
        "amendment": "protocols/ACTINV-P22_AMENDMENT_A.md",
        "baseline_record": "results/g0_p21_identity_baseline.json",
        "expected_sha256": expected,
        "pre_bump": {
            "binary": str(clone_binary),
            "binary_sha256": sha256(clone_binary),
            "python_module": str(clone_pylib),
            "python_module_sha256": sha256(clone_pylib),
            "observed_sha256": pre_raw,
            "per_surface_exact": pre_exact,
            "pass": all(pre_exact.values()),
        },
        "post_bump": {
            "binary": "target/release/actinv",
            "python_module": "python/target/release/libactinv.so",
            "raw_sha256": post_raw,
            "solver_normalized_sha256": post_norm,
            "pre_bump_solver_normalized_sha256": pre_norm,
            "per_surface_normalized_match": norm_match,
            "pass": all(norm_match.values()),
        },
        "per_surface_match": {
            k: bool(pre_exact[k] and norm_match[k]) for k in expected
        },
        "pass": bool(all(pre_exact.values()) and all(norm_match.values())),
    }


def decision(verdicts: dict, gate_checks: dict, artifacts: dict,
             surfaces: dict) -> dict:
    verdicts_present = all(verdicts.values())
    gates_green = all(gate_checks.values())
    artifacts_ok = (
        artifacts["cli_binary"]["version_stdout"] == f"actinv {TO_VERSION}"
        and artifacts["cli_binary"]["version_returncode"] == 0
        and len(artifacts["cli_binary"]["sha256"]) == 64
        and len(artifacts["python_module"]["sha256"]) == 64
    )
    surfaces_ok = surfaces["pass"]
    ready = bool(verdicts_present and gates_green and artifacts_ok and surfaces_ok)
    return {
        "release_ready": ready,
        "criteria": {
            "all_prior_verdicts_present": verdicts_present,
            "g1_g2_g3_checks_green": gates_green,
            "rc_artifacts_built_and_identity_proved": artifacts_ok,
            "four_surface_identity_holds": surfaces_ok,
        },
        "note": (
            "a ready decision authorizes nothing by itself: the version tag, "
            "GitHub release and PyPI upload remain separate maintainer actions; "
            "P18b-FAIL stands and blocks its own artifacts only"
        ),
    }


def main() -> None:
    gate_checks = {}
    for name, rel in CHECK_RECORDS.items():
        path = RESULTS / Path(rel).name
        gate_checks[name] = path.exists() and \
            json.loads(path.read_text(encoding="utf-8")).get("pass") is True
    if not all(gate_checks.values()):
        record = {
            "schema": "actinv-p22-g4-release-1",
            "aborted": "G1–G3 check records not all green",
            "gate_checks": gate_checks,
            "pass": False,
        }
        RESULT.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
        print(json.dumps(record, indent=2))
        raise SystemExit(1)

    bump = apply_bump()
    artifacts = build_artifacts()
    surfaces = surface_battery()

    verdicts = {}
    for name, expected in EXPECTED_VERDICTS.items():
        path = RESULTS / name
        observed = None
        if path.exists():
            try:
                observed = json.loads(path.read_text(encoding="utf-8")).get("verdict")
            except json.JSONDecodeError:
                observed = None
        verdicts[name] = observed == expected
    dec = decision(verdicts, gate_checks, artifacts, surfaces)

    record = {
        "schema": "actinv-p22-g4-release-1",
        "version_bump": {
            "from": FROM_VERSION, "to": TO_VERSION, "files": bump,
            "measured_evidence_bound_to": "results/g0_p22_seals.json candidate digests",
        },
        "gate_checks": gate_checks,
        "artifacts": artifacts,
        "four_surface_identity": surfaces,
        "prior_verdicts_present": verdicts,
        "decision": dec,
        "pass": bool(surfaces["pass"] and dec["release_ready"]),
    }
    RESULT.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"pass": record["pass"], "decision": dec,
                      "surfaces": surfaces["per_surface_match"]}, indent=2))
    raise SystemExit(0 if record["pass"] else 1)


if __name__ == "__main__":
    main()
