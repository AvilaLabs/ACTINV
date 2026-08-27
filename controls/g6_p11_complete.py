#!/usr/bin/env python3
"""P11-G6: independently close the complete MF=33 corpus and regression gate."""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
import os
from pathlib import Path
import re
import zipfile

import numpy as np

from p11_covariance import parse_mf33


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results" / "g6_p11_complete.json"
SOURCE = Path(
    os.environ.get(
        "ACTINV_P11_TENDL_N",
        Path.home() / "nuclear-data" / "tendl-2025" / "files" / "n-working",
    )
)
ACTIVATION = Path(
    os.environ.get(
        "ACTINV_P11_ACTIVATION",
        Path.home()
        / "nuclear-data"
        / "tendl-2025"
        / "builds"
        / "full"
        / "neutron.n.p10.npz",
    )
)
FRESH = Path(os.environ.get("ACTINV_P11_COVARIANCE_FRESH", ROOT / "target/p11-full-current-fresh.cov.npz"))
CACHED = Path(os.environ.get("ACTINV_P11_COVARIANCE_CACHED", ROOT / "target/p11-profile.cov.npz"))
FRESH_TIME = Path(os.environ.get("ACTINV_P11_COVARIANCE_FRESH_TIME", ROOT / "target/p11-full-current-fresh.time"))
CACHED_PROFILE = Path(os.environ.get("ACTINV_P11_COVARIANCE_CACHED_PROFILE", ROOT / "target/p11-profile-metrics.json"))
SCAN_CACHE = ROOT / "target/p11-independent-mf33-scan.json"
MEMORY_LIMIT_BYTES = 2 * 1024**3
ARRAY_LIMIT_BYTES = 1_000_000_000


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def byte_identical(left: Path, right: Path) -> bool:
    if left.stat().st_size != right.stat().st_size:
        return False
    with left.open("rb") as left_stream, right.open("rb") as right_stream:
        while True:
            left_block = left_stream.read(1024 * 1024)
            right_block = right_stream.read(1024 * 1024)
            if left_block != right_block:
                return False
            if not left_block:
                return True


def index_path(path: Path) -> Path:
    return path.with_name(path.stem + "_index.json")


def canonical_sha256(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def source_manifest(records: list[dict[str, object]]) -> str:
    digest = hashlib.sha256()
    unique = {str(record["file"]): str(record["source_sha256"]) for record in records}
    for filename, file_hash in sorted(unique.items()):
        digest.update(filename.encode())
        digest.update(b"\0")
        digest.update(file_hash.encode())
        digest.update(b"\0")
    return digest.hexdigest()


def parser_sha256() -> str:
    digest = hashlib.sha256()
    digest.update(b"ACTINV-P11-INDEPENDENT-MF33-SCAN-v1\0")
    for relative in ("controls/p11_covariance.py", "controls/endf_common.py"):
        digest.update((ROOT / relative).read_bytes())
    return digest.hexdigest()


def independent_scan(activation_index: dict[str, object]) -> dict[str, object]:
    """Hash and parse all raw sources without importing the Rust implementation."""
    targets = activation_index["targets"]
    expected_manifest = source_manifest(targets)
    key = {
        "schema": "actinv-p11-independent-mf33-scan-1",
        "source_directory": str(SOURCE.resolve()),
        "activation_index_sha256": sha256(index_path(ACTIVATION)),
        "expected_source_manifest_sha256": expected_manifest,
        "parser_sha256": parser_sha256(),
    }
    if os.environ.get("ACTINV_P11_REUSE_SCAN") == "1" and SCAN_CACHE.exists():
        cached = json.loads(SCAN_CACHE.read_text())
        if all(cached.get(name) == value for name, value in key.items()) and cached.get("pass"):
            return cached

    expected_by_file: dict[str, list[tuple[int, dict[str, object]]]] = defaultdict(list)
    for target, record in enumerate(targets):
        expected_by_file[str(record["file"])].append((target, record))
    files = sorted(path for path in SOURCE.iterdir() if path.is_file())
    errors: list[str] = []
    actual_names = [path.name for path in files]
    if actual_names != sorted(expected_by_file):
        missing = sorted(set(expected_by_file) - set(actual_names))
        extra = sorted(set(actual_names) - set(expected_by_file))
        errors.append(f"source filename mismatch: missing={missing[:10]}, extra={extra[:10]}")

    target_inventory: list[dict[str, object] | None] = [None] * len(targets)
    totals = Counter()
    files_with_mf33 = 0
    for ordinal, path in enumerate(files, 1):
        expected = expected_by_file.get(path.name)
        if expected is None:
            continue
        file_hash = sha256(path)
        for target, record in expected:
            if file_hash != record["source_sha256"]:
                errors.append(f"{path.name}: SHA-256 differs from activation target {target}")
        try:
            components = parse_mf33(path)
        except Exception as error:  # the result must retain every failing filename and context
            errors.append(f"{path.name}: {type(error).__name__}: {error}")
            continue
        if components:
            files_with_mf33 += 1
        by_mat: dict[int, list[dict[str, object]]] = defaultdict(list)
        for component in components:
            by_mat[int(component["mat"])].append(component)
        expected_mats = {int(record["mat"]): (target, record) for target, record in expected}
        foreign = sorted(set(by_mat) - set(expected_mats))
        if foreign:
            errors.append(f"{path.name}: MF=33 material(s) absent from activation index: {foreign}")
        for mat, (target, record) in expected_mats.items():
            selected = by_mat.get(mat, [])
            sections = len({int(component["mt"]) for component in selected})
            lb_counts = Counter(int(component["lb"]) for component in selected)
            inventory = {
                "target": target,
                "file": path.name,
                "source_sha256": file_hash,
                "mat": mat,
                "za": int(record["za"]),
                "liso": int(record["liso"]),
                "mf33_sections": sections,
                "components": len(selected),
                "lb_counts": {str(lb): count for lb, count in sorted(lb_counts.items())},
            }
            target_inventory[target] = inventory
            totals["mf33_sections"] += sections
            totals["components"] += len(selected)
            for lb, count in lb_counts.items():
                totals[f"lb_{lb}"] += count
        if ordinal % 250 == 0:
            print(f"independent MF=33 scan: {ordinal}/{len(files)} files", flush=True)

    if any(record is None for record in target_inventory):
        errors.append(
            f"{sum(record is None for record in target_inventory)} activation targets lack independent inventory"
        )
    complete_inventory = [record for record in target_inventory if record is not None]
    result = {
        **key,
        "files": len(files),
        "files_with_mf33": files_with_mf33,
        "files_without_mf33": len(files) - files_with_mf33,
        "targets": len(complete_inventory),
        "mf33_sections": totals["mf33_sections"],
        "components": totals["components"],
        "lb_counts": {
            key.removeprefix("lb_"): value
            for key, value in sorted(totals.items())
            if key.startswith("lb_")
        },
        "source_manifest_sha256": source_manifest(complete_inventory),
        "target_inventory_sha256": canonical_sha256(complete_inventory),
        "errors": errors[:100],
        "error_count": len(errors),
        "pass": not errors
        and len(files) == 2850
        and len(complete_inventory) == len(targets)
        and source_manifest(complete_inventory) == expected_manifest,
        # Retained in the ignored cache so the committed result can compare every target while
        # publishing only the compact inventory identity below.
        "target_inventory": complete_inventory,
    }
    SCAN_CACHE.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")
    return result


def current_builder_fingerprint() -> str:
    digest = hashlib.sha256()
    digest.update(b"ACTINV-COVARIANCE-BUILDER-v1\0")
    for relative in (
        "crates/actinv-data/src/covariance.rs",
        "crates/actinv-data/src/endf.rs",
        "crates/actinv-data/src/activation.rs",
        "crates/actinv-data/src/library.rs",
    ):
        digest.update((ROOT / relative).read_bytes())
    return digest.hexdigest()


def parse_time(path: Path) -> dict[str, object]:
    text = path.read_text()
    fields: dict[str, object] = {}
    patterns = {
        "command": r'Command being timed: "(.*)"',
        "elapsed": r"Elapsed \(wall clock\) time .*: (\S+)",
        "maximum_rss_kib": r"Maximum resident set size \(kbytes\): (\d+)",
        "major_page_faults": r"Major \(requiring I/O\) page faults: (\d+)",
        "swaps": r"Swaps: (\d+)",
        "exit_status": r"Exit status: (\d+)",
    }
    for name, pattern in patterns.items():
        match = re.search(pattern, text)
        if match is None:
            raise ValueError(f"{path}: missing GNU time field {name}")
        fields[name] = match.group(1)
    fields["maximum_rss_kib"] = int(fields["maximum_rss_kib"])
    fields["maximum_rss_bytes"] = int(fields["maximum_rss_kib"]) * 1024
    for name in ("major_page_faults", "swaps", "exit_status"):
        fields[name] = int(fields[name])
    fields["command"] = (
        str(fields["command"])
        .replace(str(SOURCE), "<EXTERNAL_TENDL_2025_NEUTRON>")
        .replace(str(ACTIVATION), "<EXTERNAL_TENDL_2025_NEUTRON_LIBRARY>")
        .replace(str(ROOT), "<ROOT>")
    )
    fields["cache_hits"] = 0 if " --cache " not in f" {fields['command']} " else None
    return fields


def sidecar_evidence(
    fresh_index: dict[str, object], activation_index: dict[str, object]
) -> tuple[dict[str, object], dict[str, object]]:
    with zipfile.ZipFile(FRESH) as archive:
        member_sizes = {info.filename: info.file_size for info in archive.infolist()}
    with np.load(FRESH, allow_pickle=False) as archive:
        descriptors = np.asarray(archive["components"], dtype=np.int64)
    with np.load(ACTIVATION, allow_pickle=False) as archive:
        rows = np.asarray(archive["rows"], dtype=np.int64)

    target_count = len(activation_index["targets"])
    component_counts = np.bincount(descriptors[:, 0], minlength=target_count)
    section_counts = np.bincount(
        np.unique(descriptors[:, :2], axis=0)[:, 0], minlength=target_count
    )
    lb_counts = Counter(map(int, descriptors[:, 3]))
    target_lb_counts: dict[int, Counter[int]] = defaultdict(Counter)
    for target, lb in descriptors[:, [0, 3]]:
        target_lb_counts[int(target)][int(lb)] += 1

    inventory_mismatches = []
    for target, record in enumerate(fresh_index["targets"]):
        observed_lb = {str(lb): count for lb, count in sorted(target_lb_counts[target].items())}
        if (
            int(component_counts[target]) != record["components"]
            or int(section_counts[target]) != record["mf33_sections"]
            or observed_lb != record["lb_counts"]
        ):
            inventory_mismatches.append(target)

    self_covariance = {
        (int(target), int(mt))
        for target, mt, mt1 in descriptors[:, :3]
        if mt == mt1
    }
    lmf10 = 0
    covered = 0
    missing_self = 0
    missing_by_lmf: Counter[int] = Counter()
    for target, mt, _zap, _lfs, lmf in rows:
        if int(lmf) == 10:
            lmf10 += 1
        elif (int(target), int(mt)) in self_covariance:
            covered += 1
        else:
            missing_self += 1
            missing_by_lmf[int(lmf)] += 1
    coverage = {
        "activation_rows": int(rows.shape[0]),
        "eligible_non_mf10_rows": int(rows.shape[0]) - lmf10,
        "covered_rows": covered,
        "uncovered_mf10_rows": lmf10,
        "uncovered_missing_mf33_self_rows": missing_self,
        "uncovered_missing_self_by_lmf": {
            str(lmf): count for lmf, count in sorted(missing_by_lmf.items())
        },
        "covered_fraction_of_eligible": covered / max(int(rows.shape[0]) - lmf10, 1),
        "self_covariance_target_mt_pairs": len(self_covariance),
    }
    sidecar = {
        "descriptor_shape": list(map(int, descriptors.shape)),
        "component_counts_match_index": not inventory_mismatches,
        "inventory_mismatch_targets": inventory_mismatches[:20],
        "lb_counts": {str(lb): count for lb, count in sorted(lb_counts.items())},
        "member_uncompressed_bytes": member_sizes,
        "maximum_member_uncompressed_bytes": max(member_sizes.values()),
        "every_member_below_one_gigabyte": max(member_sizes.values()) < ARRAY_LIMIT_BYTES,
    }
    return sidecar, coverage


def evidence(name: str) -> dict[str, object]:
    path = ROOT / "results" / name
    value = json.loads(path.read_text())
    return {"file": name, "sha256": sha256(path), "pass": bool(value.get("pass"))}


def main() -> None:
    activation_index_file = index_path(ACTIVATION)
    activation_index = json.loads(activation_index_file.read_text())
    fresh_index_file, cached_index_file = index_path(FRESH), index_path(CACHED)
    fresh_index = json.loads(fresh_index_file.read_text())
    cached_index = json.loads(cached_index_file.read_text())

    scan = independent_scan(activation_index)
    scan_inventory = scan.pop("target_inventory")
    independent_index_mismatches = []
    for independent, recorded in zip(scan_inventory, fresh_index["targets"]):
        for field in (
            "target",
            "file",
            "source_sha256",
            "mat",
            "za",
            "liso",
            "mf33_sections",
            "components",
            "lb_counts",
        ):
            if independent[field] != recorded[field]:
                independent_index_mismatches.append([independent["target"], field])

    fresh_hash, cached_hash = sha256(FRESH), sha256(CACHED)
    fresh_index_hash, cached_index_hash = sha256(fresh_index_file), sha256(cached_index_file)
    activation_hash, activation_index_hash = sha256(ACTIVATION), sha256(activation_index_file)
    builder_fingerprint = current_builder_fingerprint()
    sidecar, coverage = sidecar_evidence(fresh_index, activation_index)
    fresh_profile = parse_time(FRESH_TIME)
    cached_profile = json.loads(CACHED_PROFILE.read_text())
    profiles = {
        "fresh": fresh_profile,
        "cached": cached_profile,
        "memory_limit_bytes": MEMORY_LIMIT_BYTES,
        "fresh_below_memory_limit": fresh_profile["maximum_rss_bytes"] < MEMORY_LIMIT_BYTES,
        "cached_below_memory_limit": cached_profile["maximum_rss_bytes"] < MEMORY_LIMIT_BYTES,
        "zero_swaps": fresh_profile["swaps"] == 0 and cached_profile["swaps"] == 0,
    }

    prior_verdicts = {}
    for name, expected in {
        "verdict_p5.json": "P5-PASS",
        "verdict_p6.json": "P6-CONDITIONAL",
        "verdict_p7.json": "P7-CONDITIONAL",
        "verdict_p8.json": "P8-CONDITIONAL",
        "verdict_p9.json": "P9-CONDITIONAL",
        "verdict_p10.json": "P10-CONDITIONAL",
    }.items():
        path = ROOT / "results" / name
        actual = json.loads(path.read_text()).get("verdict")
        prior_verdicts[name] = {
            "sha256": sha256(path),
            "expected": expected,
            "actual": actual,
            "pass": actual == expected,
        }
    regression = {
        "evidence": {
            name: evidence(name)
            for name in (
                "g6_p11_quality.json",
                "ci_end_to_end.json",
                "g1_self_contained.json",
                "check_release_notes.json",
                "check_dependencies.json",
            )
        },
        "prior_verdicts": prior_verdicts,
    }
    regression["pass"] = all(item["pass"] for item in regression["evidence"].values()) and all(
        item["pass"] for item in prior_verdicts.values()
    )

    required_documentation = {
        "README.md": ["MF=33 nuclear-data band", "actinv build-covariance", "P11-CONDITIONAL"],
        "docs/SPEC.md": ["uncertainty.covariance", "cram_order", "require_complete"],
        "docs/METHOD.md": ["LB=0--6, 8 and 9", "No covariance value is projected"],
        "docs/DATA.md": ["TENDL-2025 MF=33 covariance", "34f2048782bd50e4cab69e269826215632675514dd88c2bad1fe70ee92ce1ac4"],
        "docs/LEDGER.md": ["MF=33 nuclear-data band", "absent_cross_parameter_pairs"],
        "docs/VALIDATION.md": ["P11 covariance, sensitivities and uncertainty controls", "2.7266e-4"],
        "docs/P11_G6_EXECUTION.md": ["1,095,648 KiB", "c19dec86b44ad5d90b66c9ab94d53e18641a1d354a89402a4da7986b6c530cde"],
    }
    documentation = {}
    for relative, fragments in required_documentation.items():
        text = (ROOT / relative).read_text()
        missing = [fragment for fragment in fragments if fragment not in text]
        documentation[relative] = {"missing": missing, "pass": not missing}

    identity = {
        "fresh_npz_sha256": fresh_hash,
        "cached_npz_sha256": cached_hash,
        "fresh_index_sha256": fresh_index_hash,
        "cached_index_sha256": cached_index_hash,
        "npz_byte_identical": byte_identical(FRESH, CACHED),
        "index_byte_identical": byte_identical(fresh_index_file, cached_index_file),
        "activation_library_sha256": activation_hash,
        "activation_index_sha256": activation_index_hash,
        "builder_fingerprint": builder_fingerprint,
    }
    expected_counts = {
        "files": fresh_index["files"],
        "files_with_mf33": fresh_index["files_with_mf33"],
        "mf33_sections": fresh_index["mf33_sections"],
        "components": fresh_index["components"],
        "lb_counts": fresh_index["lb_counts"],
    }
    scan_counts = {name: scan[name] for name in expected_counts}
    consistency = {
        "independent_counts_match_index": scan_counts == expected_counts,
        "independent_target_inventory_matches_index": not independent_index_mismatches
        and len(scan_inventory) == len(fresh_index["targets"]),
        "independent_target_inventory_mismatches": independent_index_mismatches[:20],
        "source_manifest_matches": scan["source_manifest_sha256"]
        == fresh_index["source_manifest_sha256"],
        "activation_library_matches": activation_hash == fresh_index["activation_library_sha256"],
        "activation_index_matches": activation_index_hash == fresh_index["activation_index_sha256"],
        "sidecar_hash_matches": fresh_hash == fresh_index["sha256_npz"],
        "builder_fingerprint_matches": builder_fingerprint == fresh_index["builder_fingerprint"],
        "group_hash_matches": activation_index["group_boundary_sha256"]
        == fresh_index["group_boundary_sha256"],
    }

    passed = (
        bool(scan["pass"])
        and all(value for key, value in consistency.items() if key != "independent_target_inventory_mismatches")
        and identity["npz_byte_identical"]
        and identity["index_byte_identical"]
        and fresh_index == cached_index
        and not sidecar["inventory_mismatch_targets"]
        and sidecar["lb_counts"] == fresh_index["lb_counts"]
        and sidecar["every_member_below_one_gigabyte"]
        and profiles["fresh_below_memory_limit"]
        and profiles["cached_below_memory_limit"]
        and profiles["zero_swaps"]
        and fresh_profile["exit_status"] == 0
        and fresh_profile["cache_hits"] == 0
        and cached_profile["exit_status"] == 0
        and cached_profile["cache_hits"] == 2850
        and regression["pass"]
        and all(item["pass"] for item in documentation.values())
    )
    published_scan = dict(scan)
    published_scan["source_directory"] = "<EXTERNAL_TENDL_2025_NEUTRON>"
    output = {
        "schema": "actinv-p11-g6-complete-1",
        "gate": "P11-G6",
        "identity": identity,
        "independent_scan": published_scan,
        "expected_counts": expected_counts,
        "consistency": consistency,
        "sidecar": sidecar,
        "coverage": coverage,
        "profiles": profiles,
        "regression": regression,
        "documentation": documentation,
        "pass": passed,
    }
    RESULT.write_text(json.dumps(output, indent=1, sort_keys=True) + "\n")
    print(json.dumps(output, indent=1, sort_keys=True))
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
