#!/usr/bin/env python3
"""P20 G2 producer: PSD/asymmetry exclusion and absent-section byte identity.

Leg A re-runs the frozen identity-battery CLI surface on the example spec
(which carries no `uncertainty` section) and requires the normalized result
to be byte-identical to the G0-pinned hash.

Leg B drives the released binary against synthetic single-component
covariance sidecars planted on one (target, MT) pair with two defective
collapsed blocks — an asymmetric self block and a symmetric-indefinite
(non-PSD) self block — and requires each to be excluded with its named
reason, the parameters flagged `covariance_excluded`, and the band still
emitted.

Leg C verifies the real full-corpus Core run produces no exclusions and
that every emitted band is numerically identical to the G0 reference.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results/g2_p20_defects.json"
ACTINV = Path(os.environ.get("ACTINV_BIN", ROOT / "target/release/actinv"))
EXAMPLE = ROOT / "examples/fns_fe_5min.json"
G0_BASELINE = ROOT / "results/g0_p20_identity_baseline.json"
CORE_SPEC = ROOT / "target/p20-core/spec.json"
CORE_RESULT_G0 = ROOT / "target/p20-core/result.json"
CORE_RESULT_G2 = ROOT / "target/p20-core/result-g2.json"
REAL_INDEX = ROOT / "target/p11-full-v2-repro.cov_index.json"
WORK = ROOT / "target/p20-g2"

DEFECT_TARGET = 1389  # n-Mn051.tendl; three covered library rows under MT=102
DEFECT_MT = 102
GRID = [1.0e-5, 1.0e6, 2.0e7]
CASES = {
    "asymmetric_block": [0.04, 0.5, 0.001, 0.09],
    "non_positive_semidefinite": [0.04, 0.5, 0.5, 0.04],
}
THREADS = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "RAYON_NUM_THREADS",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_sha256(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def normalized(result: dict) -> dict:
    value = dict(result)
    value.pop("ms", None)
    if "entry_point" in value:
        value["entry_point"] = "normalized"
    if isinstance(value.get("certificate"), dict):
        value["certificate"] = dict(value["certificate"])
        value["certificate"]["entry_point"] = "normalized"
    return value


def environment(cache: Path) -> dict[str, str]:
    value = os.environ.copy()
    value["ACTINV_CACHE_DIR"] = str(cache)
    for name in THREADS:
        value[name] = "1"
    return value


def run_cli(spec: Path, output: Path, env: dict[str, str]) -> dict:
    completed = subprocess.run(
        [str(ACTINV), "run", str(spec), str(output)],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=600,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(f"actinv run failed: {completed.stderr[-4000:]}")
    return json.loads(output.read_text(encoding="utf-8"))


def npy(values: list, descr: str, shape: tuple) -> bytes:
    shape_repr = f"({shape[0]},)" if len(shape) == 1 else repr(shape)
    header = (
        "{'descr': '" + descr + "', 'fortran_order': False, 'shape': "
        + shape_repr + ", }"
    ).encode()
    pad = 64 - ((10 + len(header) + 1) % 64)
    header += b" " * pad + b"\n"
    out = b"\x93NUMPY\x01\x00" + struct.pack("<H", len(header)) + header
    if descr == "<f8":
        out += struct.pack(f"<{len(values)}d", *values)
    elif descr == "<i8":
        out += struct.pack(f"<{len(values)}q", *values)
    else:
        raise ValueError(descr)
    return out


def write_sidecar(path: Path, matrix: list[float]) -> None:
    components = [DEFECT_TARGET, DEFECT_MT, DEFECT_MT, 5, 1, 0, 0, 0, 4]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as archive:
        archive.writestr("components.npy", npy(components, "<i8", (1, 9)))
        archive.writestr("grid_offsets.npy", npy([0, len(GRID)], "<i8", (2,)))
        archive.writestr("grid_values.npy", npy(GRID, "<f8", (len(GRID),)))
        archive.writestr("values.npy", npy(matrix, "<f8", (len(matrix),)))


def write_index(path: Path, sidecar_sha: str) -> None:
    index = json.loads(REAL_INDEX.read_text(encoding="utf-8"))
    for target in index["targets"]:
        target["mf33_sections"] = 0
        target["components"] = 0
        target["lb_counts"] = {}
    defect = index["targets"][DEFECT_TARGET]
    defect["mf33_sections"] = 1
    defect["components"] = 1
    defect["lb_counts"] = {"5": 1}
    index["files"] = 1
    index["files_with_mf33"] = 1
    index["mf33_sections"] = 1
    index["components"] = 1
    index["lb_counts"] = {"5": 1}
    index["sha256_npz"] = sidecar_sha
    index["builder_fingerprint"] = "p20-g2-synthetic-defect-sidecar"
    index["source_manifest_sha256"] = "0" * 64
    path.write_text(json.dumps(index, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def leg_byte_identity(env: dict[str, str], work: Path) -> dict:
    specification = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    specification["library"]["path"] = str(ROOT / specification["library"]["path"])
    for role in ("primary", "fallback"):
        specification["decay"][role] = str(ROOT / specification["decay"][role])
    spec_path = work / "identity.json"
    spec_path.write_text(json.dumps(specification, sort_keys=True) + "\n", encoding="utf-8")
    result = normalized(run_cli(spec_path, work / "identity_out.json", env))
    observed = canonical_sha256(result)
    baseline = json.loads(G0_BASELINE.read_text(encoding="utf-8"))
    expected = baseline["normalized_result_sha256"]["cli_cold"]
    return {
        "spec_sha256": canonical_sha256(specification),
        "observed_sha256": observed,
        "expected_sha256": expected,
        "byte_identical": observed == expected,
    }


def leg_defects(env: dict[str, str], work: Path) -> dict:
    cases = {}
    for reason, matrix in CASES.items():
        npz = work / f"defect_{reason}.cov.npz"
        write_sidecar(npz, matrix)
        sidecar_sha = sha256(npz)
        write_index(npz.with_name(npz.name.replace(".cov.npz", ".cov_index.json")), sidecar_sha)
        spec = json.loads(CORE_SPEC.read_text(encoding="utf-8"))
        spec["uncertainty"]["covariance"] = {"path": str(npz), "sha256": sidecar_sha}
        spec_path = work / f"defect_{reason}.json"
        spec_path.write_text(json.dumps(spec, sort_keys=True) + "\n", encoding="utf-8")
        result = run_cli(spec_path, work / f"defect_{reason}_out.json", env)
        result_sha = sha256(work / f"defect_{reason}_out.json")
        step = result["steps"][-1].get("uncertainty")
        if step is None:
            raise RuntimeError(f"{reason}: no uncertainty section emitted")
        blocks = step.get("excluded_blocks")
        expected = {
            "target": DEFECT_TARGET,
            "mt": DEFECT_MT,
            "mt1": DEFECT_MT,
            "reason": reason,
        }
        matched = [
            b
            for b in (blocks or [])
            if all(b.get(k) == v for k, v in expected.items())
        ]
        if len(matched) != 1 or len(blocks) != 1:
            raise RuntimeError(
                f"{reason}: expected exactly one {reason} exclusion, got {blocks}"
            )
        defect_value = matched[0].get("measured_defect")
        if reason == "asymmetric_block" and not (
            isinstance(defect_value, (int, float)) and defect_value > 0.0
        ):
            raise RuntimeError(f"{reason}: measured defect {defect_value} not positive")
        if reason == "non_positive_semidefinite" and not (
            isinstance(defect_value, (int, float)) and defect_value < 0.0
        ):
            raise RuntimeError(f"{reason}: measured defect {defect_value} not negative")
        flagged = set()
        for band in step["responses"].values():
            for record in band["sensitivities"]:
                parameter = record["parameter"]
                if parameter["target"] == DEFECT_TARGET and parameter["MT"] == DEFECT_MT:
                    if parameter.get("covariance_excluded") is not True:
                        raise RuntimeError(
                            f"{reason}: row {parameter['library_row']} lacks "
                            "covariance_excluded"
                        )
                    flagged.add(parameter["library_row"])
            if band["coverage"] == "complete":
                raise RuntimeError(f"{reason}: a band reported complete coverage")
            if band["mf33_standard_uncertainty"] != 0.0:
                raise RuntimeError(
                    f"{reason}: excluded block still contributes to the band"
                )
        if len(flagged) < 2:
            raise RuntimeError(f"{reason}: fewer than two parameters flagged")
        channels = {
            entry["channel"]: entry["status"]
            for band in step["responses"].values()
            for entry in band["channels"]
        }
        if channels.get("cross_section_mf33") != "propagated" or set(channels) != {
            "cross_section_mf33",
            "decay_constants",
            "fission_yields",
            "uncovered_remainder",
        }:
            raise RuntimeError(f"{reason}: channel breakdown malformed: {channels}")
        cases[reason] = {
            "spec_sha256": sha256(spec_path),
            "sidecar_sha256": sidecar_sha,
            "result_sha256": result_sha,
            "excluded_block": matched[0],
            "excluded_library_rows": sorted(flagged),
            "uncovered_library_rows": len(step["uncovered_library_rows"]),
        }
    return cases


def leg_clean_reference() -> dict:
    if not CORE_RESULT_G0.is_file() or not CORE_RESULT_G2.is_file():
        return {"status": "skipped", "reason": "reference results absent"}
    before = json.loads(CORE_RESULT_G0.read_text(encoding="utf-8"))
    after = json.loads(CORE_RESULT_G2.read_text(encoding="utf-8"))
    excluded = []
    mismatches = []
    for step_index, (b_step, a_step) in enumerate(zip(before["steps"], after["steps"])):
        b_unc = b_step.get("uncertainty")
        a_unc = a_step.get("uncertainty")
        if (b_unc is None) != (a_unc is None):
            mismatches.append(f"step {step_index} uncertainty presence differs")
            continue
        if a_unc is None:
            continue
        excluded.extend(a_unc.get("excluded_blocks") or [])
        for name, a_band in a_unc["responses"].items():
            b_band = (b_unc or {}).get("responses", {}).get(name)
            if b_band is None:
                mismatches.append(f"step {step_index} response {name} absent before")
                continue
            for field in (
                "nominal",
                "mf33_standard_uncertainty",
                "normal_interval",
                "conservative_interval",
                "coverage",
                "covered_parameters",
                "total_parameters",
            ):
                if a_band[field] != b_band[field]:
                    mismatches.append(
                        f"step {step_index} {name}.{field}: {a_band[field]} != {b_band[field]}"
                    )
    return {
        "status": "compared",
        "g0_result_sha256": sha256(CORE_RESULT_G0),
        "g2_result_sha256": sha256(CORE_RESULT_G2),
        "excluded_blocks": excluded,
        "numeric_mismatches": mismatches,
        "bands_identical": not mismatches,
        "no_exclusions": excluded == [],
    }


def main() -> None:
    if not ACTINV.is_file():
        raise RuntimeError(f"release binary missing: {ACTINV}")
    WORK.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cache-", dir=WORK) as cache:
        env = environment(Path(cache))
        report = {
            "schema": "actinv-p20-g2-defects-1",
            "protocol_sha256": "76c2ca2f646f85ea8c4a26f9ed5b2c1b3c49cfb3312a9122e546db4b53164335",
            "binary_sha256": sha256(ACTINV),
            "absent_section_byte_identity": leg_byte_identity(env, WORK),
            "defect_cases": leg_defects(env, WORK),
            "clean_corpus_reference": leg_clean_reference(),
        }
    checks = {
        "absent_section_byte_identity": report["absent_section_byte_identity"][
            "byte_identical"
        ],
        "defect_cases_excluded": len(report["defect_cases"]) == len(CASES),
        "clean_reference": (
            report["clean_corpus_reference"].get("bands_identical") is True
            and report["clean_corpus_reference"].get("no_exclusions") is True
        ),
    }
    report["checks"] = checks
    report["pass"] = all(checks.values())
    RESULT.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=1, sort_keys=True))
    raise SystemExit(0 if report["pass"] else 1)


if __name__ == "__main__":
    main()
