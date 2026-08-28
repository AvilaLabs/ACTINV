#!/usr/bin/env python3
"""Verify the externally staged data-v1.0.0 assets and record only compact identities."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "crates/actinv-cli/data/actinv-data-catalog-v1.0.0.json"
G1_PATH = ROOT / "results/g1_p13_data_distribution.json"
RESULT_PATH = ROOT / "results/g4_p13_release_stage.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_inventory(path: Path, numeric: bool):
    values = {}
    lines = path.read_text(encoding="utf-8").splitlines()
    for line in lines:
        parts = line.split("  ", 1)
        if len(parts) != 2 or not parts[0] or not parts[1] or parts[1] in values:
            raise ValueError(f"malformed or duplicate inventory line in {path.name!r}")
        values[parts[1]] = int(parts[0]) if numeric else parts[0]
    if list(values) != sorted(values):
        raise ValueError(f"{path.name!r} is not sorted by asset name")
    return values


def main(stage: Path) -> int:
    if stage.is_symlink() or not stage.is_dir():
        raise ValueError("stage must be a real directory")
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    g1 = json.loads(G1_PATH.read_text(encoding="utf-8"))
    if not g1.get("pass") or not all(g1.get("evidence_identities", {}).values()):
        raise ValueError("P13 catalog/evidence control is not green")

    expected = {}
    for artifact in catalog["artifacts"]:
        source_path = urlsplit(artifact["source"]["url"])
        if source_path.hostname == "github.com":
            expected[Path(source_path.path).name] = {
                "artifact_id": artifact["id"],
                "bytes": artifact["bytes"],
                "sha256": artifact["sha256"],
            }
    expected[CATALOG_PATH.name] = {
        "artifact_id": "embedded-catalog",
        "bytes": CATALOG_PATH.stat().st_size,
        "sha256": sha256(CATALOG_PATH),
    }
    actual_names = {path.name for path in stage.iterdir() if path.is_file()}
    allowed_names = set(expected) | {"SHA256SUMS", "SIZES"}
    unexpected = sorted(actual_names ^ allowed_names)
    directories = sorted(path.name for path in stage.iterdir() if not path.is_file())

    identities = {}
    for name, identity in sorted(expected.items()):
        path = stage / name
        identities[name] = {
            "artifact_id": identity["artifact_id"],
            "bytes": path.stat().st_size if path.is_file() else None,
            "expected_bytes": identity["bytes"],
            "sha256": sha256(path) if path.is_file() else None,
            "expected_sha256": identity["sha256"],
        }
        identities[name]["pass"] = (
            identities[name]["bytes"] == identity["bytes"]
            and identities[name]["sha256"] == identity["sha256"]
        )

    hashes = parse_inventory(stage / "SHA256SUMS", numeric=False)
    sizes = parse_inventory(stage / "SIZES", numeric=True)
    inventories_match = (
        set(hashes) == set(expected)
        and set(sizes) == set(expected)
        and all(hashes[name] == identity["sha256"] for name, identity in expected.items())
        and all(sizes[name] == identity["bytes"] for name, identity in expected.items())
    )
    checks = {
        "exact_asset_inventory": not unexpected and not directories,
        "all_payload_identities_match": all(item["pass"] for item in identities.values()),
        "inventories_match": inventories_match,
        "official_decay_archives_not_rehosted": all(
            artifact["source"].get("archive_member") is None
            or Path(urlsplit(artifact["source"]["url"]).path).name not in actual_names
            for artifact in catalog["artifacts"]
        ),
    }
    result = {
        "schema": "actinv-p13-release-stage-control-1",
        "gate": "P13-G4",
        "catalog_version": catalog["catalog_version"],
        "asset_count": len(expected),
        "total_payload_bytes": sum(identity["bytes"] for identity in expected.values()),
        "identities": identities,
        "sha256sums_sha256": sha256(stage / "SHA256SUMS"),
        "sizes_sha256": sha256(stage / "SIZES"),
        "unexpected_entries": unexpected + directories,
        "checks": checks,
        "pass": all(checks.values()),
    }
    RESULT_PATH.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=1, sort_keys=True))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"usage: {Path(sys.argv[0]).name} STAGING_DIRECTORY", file=sys.stderr)
        raise SystemExit(2)
    try:
        raise SystemExit(main(Path(sys.argv[1])))
    except (KeyError, OSError, TypeError, ValueError) as error:
        print(f"P13 release-stage control failed: {error}", file=sys.stderr)
        raise SystemExit(1)
