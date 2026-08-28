#!/usr/bin/env python3
"""Independently verify P13's embedded data-distribution contract without network access."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "crates/actinv-cli/data/actinv-data-catalog-v1.0.0.json"
NOTICE_PATH = ROOT / "crates/actinv-cli/data/ACTINV-DATA-NOTICE-v1.0.0.md"
RESULT_PATH = ROOT / "results/g1_p13_data_distribution.json"
P10_PATH = ROOT / "results/g7_p10_builds.json"
P11_PATH = ROOT / "results/g6_p11_complete.json"
FNS_PATH = ROOT / "results/fns_certificate.json"

TOP_KEYS = {
    "schema", "catalog_version", "default_bundle", "release_url", "notice", "artifacts", "bundles"
}
ARTIFACT_KEYS = {"id", "role", "path", "bytes", "sha256", "licence", "source"}
SOURCE_REQUIRED_KEYS = {"url", "bytes", "sha256"}
SOURCE_KEYS = SOURCE_REQUIRED_KEYS | {"archive_member"}
BUNDLE_KEYS = {"id", "description", "projectile", "groups", "temperature_K", "artifacts"}
ROLES = {
    "activation-library", "activation-index", "covariance-sidecar", "covariance-index",
    "decay-primary", "decay-fallback", "notice",
}
REQUIRED_ROLES = {
    "activation-library", "activation-index", "decay-primary", "decay-fallback", "notice"
}
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
IDENTIFIER = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
MAX_BYTES = 2_000_000_000


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)


def require_exact_keys(value, expected, label):
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError(f"{label} keys differ: {sorted(set(value) ^ expected)}")


def require_identity(value, label):
    if not isinstance(value.get("bytes"), int) or isinstance(value["bytes"], bool):
        raise ValueError(f"{label} bytes is not an integer")
    if not 0 < value["bytes"] <= MAX_BYTES:
        raise ValueError(f"{label} bytes is outside the bound")
    if not isinstance(value.get("sha256"), str) or not SHA256.fullmatch(value["sha256"]):
        raise ValueError(f"{label} SHA-256 is not lowercase hexadecimal")


def require_https(url, label):
    if not isinstance(url, str) or len(url) > 2048 or any(character.isspace() for character in url):
        raise ValueError(f"{label} URL is not a bounded string")
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError(f"{label} URL is not anonymous HTTPS")


def require_path(value, label, one_name=False):
    if not isinstance(value, str) or not value or len(value) > 240:
        raise ValueError(f"{label} path is empty or oversized")
    path = PurePosixPath(value)
    if (path.is_absolute() or "\\" in value or ":" in value or "//" in value
            or value.endswith("/") or any(part in {"", ".", ".."} for part in path.parts)):
        raise ValueError(f"{label} path is unsafe")
    if one_name and len(path.parts) != 1:
        raise ValueError(f"{label} must be one filename")


def expected_index_path(library_path):
    if not library_path.endswith(".npz"):
        raise ValueError("activation library does not end in .npz")
    return library_path[:-4] + "_index.json"


def expected_covariance_index_path(sidecar_path):
    if not sidecar_path.endswith(".cov.npz"):
        raise ValueError("covariance sidecar does not end in .cov.npz")
    return sidecar_path[:-4] + "_index.json"


def validate_catalog(catalog):
    require_exact_keys(catalog, TOP_KEYS, "catalog")
    if catalog["schema"] != "actinv-data-catalog-1" or catalog["catalog_version"] != "1.0.0":
        raise ValueError("catalog schema/version is not frozen P13 v1.0.0")
    require_https(catalog["release_url"], "release")
    require_path(catalog["notice"], "notice", one_name=True)

    artifacts = {}
    paths = set()
    for position, artifact in enumerate(catalog["artifacts"]):
        require_exact_keys(artifact, ARTIFACT_KEYS, f"artifact {position}")
        identifier = artifact["id"]
        if not isinstance(identifier, str) or not IDENTIFIER.fullmatch(identifier) or identifier in artifacts:
            raise ValueError(f"invalid or duplicate artifact ID {identifier!r}")
        if artifact["role"] not in ROLES:
            raise ValueError(f"artifact {identifier!r} has an invalid role")
        require_path(artifact["path"], f"artifact {identifier}")
        if artifact["path"] in paths:
            raise ValueError(f"duplicate artifact path {artifact['path']!r}")
        paths.add(artifact["path"])
        if not isinstance(artifact["licence"], str) or not artifact["licence"].strip():
            raise ValueError(f"artifact {identifier!r} has no licence")
        require_identity(artifact, f"artifact {identifier}")

        source = artifact["source"]
        if not isinstance(source, dict) or not SOURCE_REQUIRED_KEYS <= set(source) <= SOURCE_KEYS:
            raise ValueError(f"artifact {identifier!r} source keys differ")
        require_https(source["url"], f"artifact {identifier} source")
        require_identity(source, f"artifact {identifier} source")
        if "archive_member" in source:
            require_path(source["archive_member"], f"artifact {identifier} member", one_name=True)
        elif (source["bytes"], source["sha256"]) != (artifact["bytes"], artifact["sha256"]):
            raise ValueError(f"artifact {identifier!r} direct identities differ")
        artifacts[identifier] = artifact

    bundles = set()
    referenced = set()
    for position, bundle in enumerate(catalog["bundles"]):
        require_exact_keys(bundle, BUNDLE_KEYS, f"bundle {position}")
        identifier = bundle["id"]
        if not isinstance(identifier, str) or not IDENTIFIER.fullmatch(identifier) or identifier in bundles:
            raise ValueError(f"invalid or duplicate bundle ID {identifier!r}")
        bundles.add(identifier)
        if not isinstance(bundle["description"], str) or not bundle["description"].strip():
            raise ValueError(f"bundle {identifier!r} has no description")
        projectile = bundle["projectile"]
        expected_groups = "fispact-709" if projectile == "neutron" else "fispact-162"
        expected_temperature = 293.6 if projectile == "neutron" else 0.0
        if (projectile not in {"neutron", "proton", "deuteron", "alpha"}
                or bundle["groups"] != expected_groups
                or bundle["temperature_K"] != expected_temperature):
            raise ValueError(f"bundle {identifier!r} model metadata differs")
        ids = bundle["artifacts"]
        if not isinstance(ids, list) or len(ids) != len(set(ids)):
            raise ValueError(f"bundle {identifier!r} repeats an artifact")
        if any(artifact_id not in artifacts for artifact_id in ids):
            raise ValueError(f"bundle {identifier!r} names an unknown artifact")
        selected = [artifacts[artifact_id] for artifact_id in ids]
        by_role = {artifact["role"]: artifact for artifact in selected}
        if len(by_role) != len(selected) or not REQUIRED_ROLES <= set(by_role):
            raise ValueError(f"bundle {identifier!r} repeats or omits a required role")
        if expected_index_path(by_role["activation-library"]["path"]) != by_role["activation-index"]["path"]:
            raise ValueError(f"bundle {identifier!r} activation index is not adjacent")
        has_covariance = "covariance-sidecar" in by_role or "covariance-index" in by_role
        if has_covariance:
            if (projectile != "neutron" or "covariance-sidecar" not in by_role
                    or "covariance-index" not in by_role
                    or expected_covariance_index_path(by_role["covariance-sidecar"]["path"])
                    != by_role["covariance-index"]["path"]):
                raise ValueError(f"bundle {identifier!r} covariance pair is invalid")
        referenced.update(ids)
    if catalog["default_bundle"] != "tendl-2025-neutron" or catalog["default_bundle"] not in bundles:
        raise ValueError("default bundle differs")
    if referenced != set(artifacts):
        raise ValueError("catalog has an unreferenced artifact")
    return artifacts, bundles


def hash_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def evidence_identities():
    p10 = load_json(P10_PATH)
    p11 = load_json(P11_PATH)
    fns = load_json(FNS_PATH)
    identities = {}
    for projectile, stem in {
        "neutron": "tendl-2025-neutron-709g",
        "proton": "tendl-2025-proton-162g",
        "deuteron": "tendl-2025-deuteron-162g",
        "alpha": "tendl-2025-alpha-162g",
    }.items():
        build = p10["builds"][projectile]
        identities[stem] = build["npz_sha256"]
        identities[stem + "-index"] = build["index_sha256"]
    identity = p11["identity"]
    identities["tendl-2025-neutron-709g-covariance"] = identity["fresh_npz_sha256"]
    identities["tendl-2025-neutron-709g-covariance-index"] = identity["fresh_index_sha256"]
    identities["endfb-viii-0-decay"] = fns["inputs"]["decay_endfb80"]["sha256"]
    identities["jeff-3-3-decay"] = fns["inputs"]["decay_jeff33"]["sha256"]
    return identities


def planted_rejections(catalog):
    mutations = {}

    duplicate = copy.deepcopy(catalog)
    duplicate["artifacts"][1]["id"] = duplicate["artifacts"][0]["id"]
    mutations["duplicate_artifact_id"] = duplicate
    unsafe = copy.deepcopy(catalog)
    unsafe["artifacts"][0]["path"] = "../outside.npz"
    mutations["parent_traversal"] = unsafe
    insecure = copy.deepcopy(catalog)
    insecure["artifacts"][0]["source"]["url"] = "http://example.invalid/data"
    mutations["non_https"] = insecure
    identity = copy.deepcopy(catalog)
    identity["artifacts"][0]["source"]["bytes"] += 1
    mutations["direct_identity_mismatch"] = identity
    role = copy.deepcopy(catalog)
    role["bundles"][0]["artifacts"][1] = role["bundles"][2]["artifacts"][1]
    mutations["non_adjacent_index"] = role
    unknown = copy.deepcopy(catalog)
    unknown["artifacts"][0]["unexpected"] = True
    mutations["unknown_field"] = unknown

    result = {}
    for label, mutation in mutations.items():
        try:
            validate_catalog(mutation)
        except (KeyError, TypeError, ValueError):
            result[label] = True
        else:
            result[label] = False
    return result


def cli_contract(catalog):
    binary = Path(os.environ.get("ACTINV_BIN", ROOT / "target/release/actinv"))
    if not binary.is_file():
        fallback = ROOT / "target/debug/actinv"
        binary = fallback if fallback.is_file() else binary
    manifest = subprocess.run(
        [binary, "data", "manifest"], check=True, cwd=ROOT, capture_output=True
    )
    listed = subprocess.run(
        [binary, "data", "list"], check=True, cwd=ROOT, capture_output=True, text=True
    )
    expected_manifest = CATALOG_PATH.read_bytes()
    list_text = listed.stdout
    return {
        "manifest_byte_identical": manifest.stdout == expected_manifest,
        "list_names_every_bundle": all(bundle["id"] in list_text for bundle in catalog["bundles"]),
        "list_marks_default": f"{catalog['default_bundle']} [default]" in list_text,
        "list_names_release": catalog["release_url"] in list_text,
    }


def main():
    catalog = load_json(CATALOG_PATH)
    artifacts, bundles = validate_catalog(catalog)
    expected = evidence_identities()
    evidence = {
        artifact_id: artifacts[artifact_id]["sha256"] == sha256
        for artifact_id, sha256 in sorted(expected.items())
    }
    notice = artifacts["actinv-data-notice-v1"]
    notice_check = {
        "bytes_match": NOTICE_PATH.stat().st_size == notice["bytes"],
        "sha256_match": hash_file(NOTICE_PATH) == notice["sha256"],
    }
    tracked = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True
    ).stdout.split(b"\0")
    forbidden_tracked = sorted(
        path.decode() for path in tracked if path and (
            path.decode().endswith((".npz", ".zip", ".endf"))
            or path.decode() in {artifact["path"] for artifact in artifacts.values()}
        )
    )
    rejections = planted_rejections(catalog)
    cli = cli_contract(catalog)
    checks = {
        "catalog_valid": True,
        "all_evidence_identities_match": all(evidence.values()),
        "all_planted_mutations_rejected": all(rejections.values()),
        "cli_contract": all(cli.values()),
        "notice_identity": all(notice_check.values()),
        "no_bulk_data_tracked": not forbidden_tracked,
    }
    result = {
        "schema": "actinv-p13-data-distribution-control-1",
        "gate": "P13-G1/G3",
        "catalog_version": catalog["catalog_version"],
        "artifact_count": len(artifacts),
        "bundle_count": len(bundles),
        "evidence_identities": evidence,
        "planted_rejections": rejections,
        "cli": cli,
        "notice": notice_check,
        "forbidden_tracked_files": forbidden_tracked,
        "checks": checks,
        "pass": all(checks.values()),
    }
    RESULT_PATH.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=1, sort_keys=True))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (KeyError, OSError, TypeError, ValueError, subprocess.SubprocessError) as error:
        print(f"P13 data-distribution control failed: {error}", file=sys.stderr)
        raise SystemExit(1)
