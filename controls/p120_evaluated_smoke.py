#!/usr/bin/env python3
"""Optional real-data P120 mapped/direct smoke; does not download data."""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import math
import platform
import uuid
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
from check_p120 import close_vectors, scientific_steps  # noqa: E402
from p105_budget_control import _run  # noqa: E402
from p11_fixtures import sha256, write_json  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-bin", required=True, type=Path,
                        help="absolute path to the ACTINV executable to evaluate")
    parser.add_argument("--data-dir", required=True, type=Path,
                        help="existing catalog data root containing v1.1.0/; no fetch is performed")
    return parser


def _run_record(report: dict, name: str, argv: list[str], work: Path) -> tuple[int | None, str]:
    log = work / f"{name}.log"
    record = {"name": name, "argv": argv, "log": str(log)}
    report["commands"].append(record)
    try:
        result = _run(argv, ROOT, timeout_s=120)
        output = result.stdout + result.stderr
        log.write_text(output)
        record["exit_code"] = result.returncode
        record["log_sha256"] = sha256(log)
        return result.returncode, output
    except Exception as exc:
        output = f"{type(exc).__name__}: {exc}\n"
        log.write_text(output)
        record.update({"exit_code": None, "error": output.strip(), "log_sha256": sha256(log)})
        report["errors"].append(f"{name}: {output.strip()}")
        return None, output


def _sparse_map(values: dict, *, value_name: str, path: str) -> tuple[dict, list[str]]:
    errors = []
    if not isinstance(values, dict):
        return {}, [f"{path}: expected a nuclide-to-value mapping"]
    for nuclide, value in values.items():
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
            errors.append(f"{path}/{nuclide}: invalid {value_name} value {value!r}")
    return values, errors


def _inventory_map(items: list, *, side: str) -> tuple[dict, list[str]]:
    result, errors = {}, []
    if not isinstance(items, list):
        return result, [f"{side}/inventory: expected a list"]
    for index, item in enumerate(items):
        if not isinstance(item, dict) or not isinstance(item.get("nuclide"), str):
            errors.append(f"{side}/inventory/{index}: invalid nuclide record")
            continue
        name = item["nuclide"]
        if name in result:
            errors.append(f"{side}/inventory: duplicate nuclide ID {name}")
            continue
        value = item.get("atoms_per_g")
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
            errors.append(f"{side}/inventory/{name}: invalid atoms_per_g value {value!r}")
            continue
        result[name] = item
    return result, errors


def compare_sparse_steps(actual_steps: list, expected_steps: list, *,
                         rel: float = 1e-12, abs_: float = 1e-12) -> dict:
    """Compare sparse inventory/activity by nuclide, treating omission as zero."""
    result = {"pass": False, "differences": [], "unmatched_inventory": [],
              "unmatched_activity": [], "sparse_zero_convention": True}
    if not isinstance(actual_steps, list) or not isinstance(expected_steps, list) \
            or len(actual_steps) != len(expected_steps):
        result["differences"].append("step counts differ or are invalid")
        return result

    for index, (actual, expected) in enumerate(zip(actual_steps, expected_steps)):
        for field in ("t_s", "flux", "heat_W_per_g"):
            if field not in actual or field not in expected:
                result["differences"].append(f"step{index}/{field}: required field missing")
                continue
            result["differences"].extend(
                close_vectors(actual.get(field), expected.get(field), rel=rel, abs_=abs_,
                              path=f"step{index}/{field}"))

        actual_inventory, errors = _inventory_map(actual.get("inventory"), side=f"actual/step{index}")
        result["differences"].extend(errors)
        expected_inventory, errors = _inventory_map(expected.get("inventory"), side=f"expected/step{index}")
        result["differences"].extend(errors)
        for nuclide in sorted(actual_inventory.keys() | expected_inventory.keys()):
            left, right = actual_inventory.get(nuclide), expected_inventory.get(nuclide)
            left_value = float(left["atoms_per_g"]) if left is not None else 0.0
            right_value = float(right["atoms_per_g"]) if right is not None else 0.0
            if left is None or right is None:
                result["unmatched_inventory"].append({
                    "step": index, "nuclide": nuclide,
                    "actual_atoms_per_g": left_value, "expected_atoms_per_g": right_value,
                })
            if not math.isclose(left_value, right_value, rel_tol=rel, abs_tol=abs_):
                result["differences"].append(
                    f"step{index}/inventory/{nuclide}: {left_value!r} != {right_value!r}")
            if left is not None and right is not None:
                left_meta = {key: value for key, value in left.items()
                             if key not in {"nuclide", "atoms_per_g"}}
                right_meta = {key: value for key, value in right.items()
                              if key not in {"nuclide", "atoms_per_g"}}
                if left_meta != right_meta:
                    result["differences"].append(
                        f"step{index}/inventory/{nuclide}: shared nuclide metadata differs")

        actual_activity, errors = _sparse_map(
            actual.get("activity_Bq_per_g"), value_name="activity_Bq_per_g",
            path=f"actual/step{index}/activity_Bq_per_g")
        result["differences"].extend(errors)
        expected_activity, errors = _sparse_map(
            expected.get("activity_Bq_per_g"), value_name="activity_Bq_per_g",
            path=f"expected/step{index}/activity_Bq_per_g")
        result["differences"].extend(errors)
        for nuclide in sorted(actual_activity.keys() | expected_activity.keys()):
            left_value = float(actual_activity.get(nuclide, 0.0))
            right_value = float(expected_activity.get(nuclide, 0.0))
            if nuclide not in actual_activity or nuclide not in expected_activity:
                result["unmatched_activity"].append({
                    "step": index, "nuclide": nuclide,
                    "actual_Bq_per_g": left_value, "expected_Bq_per_g": right_value,
                })
            if not math.isclose(left_value, right_value, rel_tol=rel, abs_tol=abs_):
                result["differences"].append(
                    f"step{index}/activity_Bq_per_g/{nuclide}: {left_value!r} != {right_value!r}")
    result["pass"] = not result["differences"]
    return result


def main() -> int:
    args = _parser().parse_args()
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    work = ROOT / "target" / "p120-evaluated" / f"{stamp}-{uuid.uuid4().hex[:8]}"
    work.mkdir(parents=True, exist_ok=False)
    report = {
        "schema": "actinv-p120-evaluated-smoke-1", "pass": False,
        "script_sha256": sha256(Path(__file__)),
        "created_utc": stamp, "work_dir": str(work),
        "software": {"python": platform.python_version(), "numpy": np.__version__},
        "candidate_bin": str(args.candidate_bin), "data_dir": str(args.data_dir),
        "control_sha256": sha256(Path(__file__).resolve()),
        "commands": [], "checks": {}, "errors": [],
        "scope": "evaluated-data mapping smoke; not measurement or physics validation",
    }
    try:
        if not args.candidate_bin.is_absolute():
            raise ValueError("--candidate-bin must be an absolute path")
        binary = args.candidate_bin.resolve(strict=True)
        if not binary.is_file():
            raise ValueError(f"candidate binary is not a file: {binary}")
        data_root = args.data_dir.resolve(strict=True)
        if not data_root.is_dir():
            raise ValueError(f"--data-dir is not a directory: {data_root}")
        report["candidate_bin"] = str(binary)
        report["data_dir"] = str(data_root)
        report["candidate_sha256"] = sha256(binary)

        version_code, version_text = _run_record(report, "version", [str(binary), "--version"], work)
        report["software"]["actinv_version_output"] = version_text.strip()
        if version_code != 0:
            raise RuntimeError(f"ACTINV version query failed with exit {version_code}")

        verify_argv = [str(binary), "data", "verify", "tendl-2025-neutron", "--output", str(data_root)]
        verify_code, verify_text = _run_record(report, "data-verify", verify_argv, work)
        if verify_code != 0:
            raise RuntimeError(f"existing TENDL bundle verification failed with exit {verify_code}")
        verification = json.loads(verify_text)
        if verification.get("catalog_version") != "1.1.0":
            raise ValueError("ACTINV data verify did not report catalog version 1.1.0")
        if verification.get("bundle") != "tendl-2025-neutron":
            raise ValueError("ACTINV data verify returned an unexpected bundle")
        report["data"] = {"catalog_version": verification["catalog_version"],
                           "bundle": verification["bundle"],
                           "evaluation": "TENDL-2025", "verified_files": verification.get("files", [])}

        version_root = data_root / "v1.1.0"
        library = version_root / "activation/tendl-2025-neutron-709g.npz"
        index = library.with_name("tendl-2025-neutron-709g_index.json")
        decay = version_root / "decay/endf-b-viii-0_decay.dat"
        fallback = version_root / "decay/jeff-3-3_decay.dat"
        artifacts = (library, index, decay, fallback)
        missing = [str(path) for path in artifacts if not path.is_file()]
        if missing:
            raise FileNotFoundError("required verified artifacts are missing: " + ", ".join(missing))
        report["data"]["artifacts"] = {str(path): sha256(path) for path in artifacts}
        with np.load(library) as archive:
            bounds = [float(value) for value in archive["bounds"].tolist()]
        if bounds[0] > bounds[-1]:
            bounds.reverse()
        if len(bounds) < 2 or any(not math.isfinite(value) or value <= 0 for value in bounds) \
                or any(a >= b for a, b in zip(bounds, bounds[1:])):
            raise ValueError("activation library energy boundaries are not positive and strictly ascending")

        low, high, total = 1.0, 14.0e6, 1.0e12
        inside = low >= bounds[0] and high <= bounds[-1]
        report["checks"]["source_range_inside_library"] = {
            "pass": inside, "source_boundaries_eV": [low, high],
            "library_boundaries_eV": [bounds[0], bounds[-1]],
        }
        if not inside:
            raise ValueError("source boundaries [1 eV, 14 MeV] fall outside the activation library range")

        # Independent reference: constant flux per unit lethargy over 1 eV–14 MeV.
        fine_flux = []
        for left, right in zip(bounds, bounds[1:]):
            overlap_low, overlap_high = max(left, low), min(right, high)
            fraction = (math.log(overlap_high / overlap_low) / math.log(high / low)
                        if overlap_high > overlap_low else 0.0)
            fine_flux.append(total * fraction)
        normalized = math.isclose(math.fsum(fine_flux), total, rel_tol=1e-12, abs_tol=1e-6)
        report["checks"]["uniform_lethargy_flux_normalization"] = {
            "pass": normalized, "assumption": "constant flux per unit lethargy within each source group",
            "source_total_n_cm-2-s-1": total,
            "fine_group_total_n_cm-2-s-1": math.fsum(fine_flux),
            "library_group_count": len(fine_flux),
        }
        if not normalized:
            raise ValueError("uniform-lethargy group integration failed flux normalization")

        mapped = {
            "spec": "actinv-spec-1", "title": "P120 evaluated-data mapping smoke",
            "projectile": "neutron", "library": {"path": str(library), "sha256": sha256(library)},
            "decay": {"primary": str(decay), "fallback": str(fallback)},
            "material": {"basis": "wt_percent", "mass_g": 1.0, "composition": {"Fe56": 100}},
            "spectrum": {"structure": "custom", "boundaries_eV": [low, high],
                         "flux_per_group": [total], "rebin": "equal_lethargy"},
            "schedule": [{"dt": "300 s", "flux": 1.0}, {"dt": "3600 s", "flux": 0.0}],
            "options": {"mode": "coupled", "prune": "reach", "cram_order": 48,
                        "bmin_atoms_per_g": 0.0, "temperature_K": 293.6},
        }
        direct = copy.deepcopy(mapped)
        direct["title"] = "P120 evaluated-data explicit-group reference"
        direct["spectrum"] = {"structure": "custom", "boundaries_eV": bounds,
                              "flux_per_group": fine_flux, "descending": False}
        outputs = {}
        for name, problem in (("mapped", mapped), ("direct", direct)):
            source, output = work / f"{name}.json", work / f"{name}.result.json"
            write_json(source, problem)
            argv = [str(binary), "run", str(source), str(output)]
            code, _ = _run_record(report, name, argv, work)
            report["commands"][-1]["input_sha256"] = sha256(source)
            report["commands"][-1]["output"] = str(output)
            if code != 0:
                report["errors"].append(f"{name} calculation failed with exit {code}")
                continue
            if not output.is_file():
                report["errors"].append(f"{name} calculation exited successfully without result output")
                continue
            report["commands"][-1]["output_sha256"] = sha256(output)
            outputs[name] = json.loads(output.read_text())

        if len(outputs) == 2:
            mapped_steps = scientific_steps(outputs["mapped"])
            direct_steps = scientific_steps(outputs["direct"])
            raw_errors = close_vectors(mapped_steps, direct_steps, rel=1e-12, abs_=1e-12)
            sparse_comparison = compare_sparse_steps(mapped_steps, direct_steps,
                                                     rel=1e-12, abs_=1e-12)
            errors = sparse_comparison["differences"]
            strengths = [math.fsum(float(value) for value in step["activity_Bq_per_g"].values())
                         for step in outputs["mapped"]["steps"]]
            positive = bool(strengths) and all(math.isfinite(value) and value > 0 for value in strengths)
            report["checks"]["mapped_matches_independent_explicit_groups"] = {
                "pass": not errors, "relative_tolerance": 1e-12, "absolute_tolerance": 1e-12,
                "scientific_fields": ["time", "flux", "inventory", "activity", "heat"],
                "inventory_activity_convention": "sparse nuclide maps; missing IDs compare as zero",
                "differences": errors,
            }
            report["checks"]["raw_full_shape_observation"] = {
                "pass": not raw_errors, "differences": raw_errors,
                "meaning": "diagnostic only; sparse omission differences are checked below",
            }
            report["checks"]["sparse_inventory_activity_details"] = sparse_comparison
            report["checks"]["positive_mapped_activity"] = {
                "pass": positive, "total_activity_Bq_per_g_by_endpoint": strengths,
            }
            report["checks"]["mapped_heat_W_per_g_by_endpoint"] = [
                step["heat_W_per_g"]["total"] for step in outputs["mapped"]["steps"]]
            if not positive:
                report["errors"].append("mapped calculation did not produce positive activity at every endpoint")
            if errors:
                report["errors"].append("mapped/direct complete scientific vectors differ")
        report["pass"] = not report["errors"]
    except Exception as exc:
        report["errors"].append(f"{type(exc).__name__}: {exc}")
    finally:
        report["pass"] = not report["errors"]
        write_json(work / "report.json", report)

    print(json.dumps(report, indent=2))
    print(f"report: {work / 'report.json'}")
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
