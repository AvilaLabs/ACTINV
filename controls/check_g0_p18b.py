#!/usr/bin/env python3
"""Independent checker for the P18b authority, provenance, and quarantine seal."""

from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import io
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "protocols/ACTINV-P18b_PROTOCOL.md"
P18_PROTOCOL = ROOT / "protocols/ACTINV-P18_PROTOCOL.md"
P18_AMENDMENT = ROOT / "protocols/ACTINV-P18_AMENDMENT_1.md"
P18_AUDIT = ROOT / "results/g2_p18_corpus_audit.json"
P18_CHANGED = ROOT / "results/g2_p18_changed_identities.json.gz"
RESULT = ROOT / "results/g0_p18b_seal.json"
SOURCE_MANIFEST = ROOT / "results/p18b_source_manifest.json.gz"
OUTPUT = ROOT / "results/g0_p18b_check.json"
CONTROL = ROOT / "controls/g0_p18b_seal.py"
HASH_LOG = ROOT / "protocols/protocol_hash.txt"

BASE_COMMIT = "d3456890cf0c4b9221ebf17f6630ef8b4fe768cc"
OPENING_COMMIT = "bf540efc3cd9525d17f69a525ab6732c648bfe93"
PROTOCOL_SHA256 = "69076fa2656b239addbb15fbb4727caaa2c8ea37b3aa82a141f3a2b0b619eabe"
P18_PROTOCOL_SHA256 = "002afb038bbbf1ad0bdb34149971f8d3f33a3e2590c6d04ced87bb5ada046e09"
P18_AMENDMENT_SHA256 = "8eb3f3bc657a49ebeff7cc5d7ca124cb4e4debbf094fee9d6417c01f740aa9e0"
MANUAL_SHA256 = "77a0fee413c3b1d5d74a161ed9fe7f77bbcbc58a654304851b7b2b400183d022"
IAEA_COMMIT = "c2a6718bd831b5c8a6e975beb1946954b1d73c40"
IAEA_FILES = {
    "fizcon/fizcon.f": {
        "bytes": 351_060,
        "sha256": "15eac8dbcc1f1c0b8825d9e2a487d7e26f4717ccacad373f226a01c721e7527e",
    },
    "checkr/checkr.f": {
        "bytes": 211_322,
        "sha256": "739169c525663a3a80d62f8047243b6d3a0d2b36e05cf95a7336ae58363d684e",
    },
    "README.md": {
        "bytes": 893,
        "sha256": "b31bb9034edc43ad3ef623eebc154361fad7131a56e4cc087ab52760843423fe",
    },
    "LICENSE.MIT": {
        "bytes": 1_145,
        "sha256": "f9d773e3ae7e2b9136e8e14b6cdfeac38044595b7a5f1dcdb5cdb6614565cb87",
    },
}
P18_EVIDENCE = {
    "session": "c782d5f27a88286e44aa019c0d645fd665811204d9158fbff63bdbedb6de8f54",
    "verdict": "e9dbea1e2e5ade98cb14afa7f73b2eb9dd82da183364a75ea8d36dade1eb8863",
    "audit": "e20fba865c36131f27bce7ac110957336c55d9c8455e79a8ddac0edde66df9cb",
    "changed_identities": "606347ce4d12788451be3a4a3765bfa5305613631eb37ea5de30d163546e083e",
    "family_seal": "fb2fd35b02aa4d9629d9740d638b7650f97e8ba9d8c4dcd70ee238c31c45dfed",
    "g0": "e419e8eeb506c28440744e8a3ceb6922f1d066b8d673f534ce0567dc59f82af3",
}
MANIFEST_SHA256 = {
    "alpha": "e3aaf11e60c46b43361796c2c297bab4fb714fe57ab26a315594f2b4799dfdbf",
    "deuteron": "afb52c55b2a1babca998cc3d8af0f7004c64f85d160e3c5aabf16a05839355d9",
    "neutron": "a6d17f996153d2671c0c51bfb6303e2a87a5af03e0696bfb34d668a31dbfb2a2",
    "proton": "98a8bd55784c326b8696de91f494111326378e776a975a512e59806a8c9ec2ef",
}
MANIFEST_PATHS = {
    "alpha": "staging/TENDL-a.manifest.json",
    "deuteron": "staging/TENDL-d.manifest.json",
    "neutron": "staging/TENDL-n-working.manifest.json",
    "proton": "staging/TENDL-p.manifest.json",
}
SAMPLE_SEED = "ACTINV-P18b-IAEA-SAMPLE-v1"
FORBIDDEN_KEYS = {
    "dependent_value",
    "measured_ratio",
    "ratio_value",
    "uncertainty_value",
    "calculated_ratio",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def deterministic_gzip(payload: bytes) -> bytes:
    output = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=output, compresslevel=9, mtime=0) as stream:
        stream.write(payload)
    return output.getvalue()


def load(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def forbidden_key_present(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            str(key).lower() in FORBIDDEN_KEYS or forbidden_key_present(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(forbidden_key_present(item) for item in value)
    return False


def decode_manifest(payload: bytes) -> dict[str, Any] | None:
    try:
        value = json.loads(gzip.decompress(payload))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def validate_manifest(value: dict[str, Any]) -> list[str]:
    errors = []
    if set(value) != {"schema", "corpora"} or value.get("schema") != "actinv-p18b-source-manifest-1":
        return ["manifest_schema"]
    corpora = value.get("corpora")
    if not isinstance(corpora, dict) or set(corpora) != set(MANIFEST_SHA256):
        return ["manifest_corpora"]
    for projectile in sorted(MANIFEST_SHA256):
        corpus = corpora[projectile]
        expected_keys = {"source_manifest", "source_manifest_sha256", "files"}
        if projectile == "neutron":
            expected_keys.add("frozen_manifest_sha256")
        if set(corpus) != expected_keys:
            errors.append(f"{projectile}_manifest_schema")
            continue
        if corpus["source_manifest"] != MANIFEST_PATHS[projectile]:
            errors.append(f"{projectile}_manifest_path")
        if corpus["source_manifest_sha256"] != MANIFEST_SHA256[projectile]:
            errors.append(f"{projectile}_manifest_hash")
        if projectile == "neutron" and corpus["frozen_manifest_sha256"] != (
            "b578ab395c6c71d7727dfb0513e88effd96692862664a6279802638531239b67"
        ):
            errors.append("neutron_frozen_manifest_hash")
        rows = corpus["files"]
        if not isinstance(rows, list) or len(rows) != 2_850:
            errors.append(f"{projectile}_file_count")
            continue
        if [row.get("name") for row in rows] != sorted(row.get("name") for row in rows):
            errors.append(f"{projectile}_file_order")
        if len({row.get("name") for row in rows}) != 2_850:
            errors.append(f"{projectile}_file_unique")
        for row in rows:
            keys = {"name", "bytes", "source_sha256"}
            if projectile == "neutron":
                keys |= {"official_sha256", "byte_identical"}
            if set(row) != keys:
                errors.append(f"{projectile}_file_schema")
                break
            if not isinstance(row["name"], str) or not isinstance(row["bytes"], int) or row["bytes"] <= 0:
                errors.append(f"{projectile}_file_identity")
                break
            hashes = [row["source_sha256"]]
            if projectile == "neutron":
                hashes.append(row["official_sha256"])
                if not isinstance(row["byte_identical"], bool):
                    errors.append("neutron_byte_identity")
            if any(
                not isinstance(item, str)
                or len(item) != 64
                or any(character not in "0123456789abcdef" for character in item)
                for item in hashes
            ):
                errors.append(f"{projectile}_file_hash")
                break
    if forbidden_key_present(value):
        errors.append("manifest_dependent_field")
    return sorted(set(errors))


def conflict_rows() -> list[dict[str, Any]]:
    with gzip.open(P18_CHANGED, "rt", encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, list) or len(value) != 45_320:
        raise ValueError("changed identity inventory")
    return [row for row in value if row.get("decision") == "mf8_q_excitation_conflict"]


def derive_sample(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    lookup: dict[str, dict[str, dict[str, Any]]] = {}
    selected: dict[tuple[str, str], set[str]] = {}
    for projectile, corpus in manifest["corpora"].items():
        lookup[projectile] = {row["name"]: row for row in corpus["files"]}
        ranked = sorted(
            corpus["files"],
            key=lambda row: (
                hashlib.sha256(
                    f"{SAMPLE_SEED}\n{projectile}\n{row['source_sha256']}".encode()
                ).hexdigest(),
                row["name"],
            ),
        )
        for row in ranked[:25]:
            selected.setdefault((projectile, row["name"]), set()).add("hash_sample")
    audit = json.loads(P18_AUDIT.read_text(encoding="utf-8"))
    for projectile, corpus in audit["corpora"].items():
        selected.setdefault((projectile, corpus["worst"]["file"]), set()).add("p18_worst")
    for row in conflict_rows():
        selected.setdefault((row["projectile"], row["file"]), set()).add(
            "p18_excitation_conflict"
        )
    output = []
    for (projectile, name), reasons in sorted(selected.items()):
        source = lookup.get(projectile, {}).get(name)
        if source is None:
            raise ValueError(f"sample source missing: {projectile}/{name}")
        output.append(
            {
                "projectile": projectile,
                "file": name,
                "source_sha256": source["source_sha256"],
                "reasons": sorted(reasons),
                "rank_sha256": hashlib.sha256(
                    f"{SAMPLE_SEED}\n{projectile}\n{source['source_sha256']}".encode()
                ).hexdigest(),
            }
        )
    return output


def workflow_valid(
    value: Any,
    run_id: int,
    job_id: int,
    head_sha: str,
    created_at: str,
    updated_at: str,
    substantive_steps: int,
) -> bool:
    return bool(
        isinstance(value, dict)
        and value.get("status") == "completed"
        and value.get("conclusion") == "success"
        and value.get("headSha") == head_sha
        and value.get("headBranch") == "master"
        and value.get("event") == "push"
        and value.get("job_id") == job_id
        and value.get("job_conclusion") == "success"
        and value.get("substantive_steps") == substantive_steps
        and value.get("url") == f"https://github.com/AvilaLabs/ACTINV/actions/runs/{run_id}"
        and value.get("createdAt") == created_at
        and value.get("updatedAt") == updated_at
    )


def validate_result(
    result: dict[str, Any], manifest: dict[str, Any], compressed: bytes
) -> list[str]:
    errors = validate_manifest(manifest)
    if result.get("schema") != "actinv-p18b-g0-seal-1" or result.get("gate") != "P18b-G0":
        errors.append("result_schema")
    if result.get("protocol_sha256") != PROTOCOL_SHA256:
        errors.append("protocol")
    if result.get("base_commit") != BASE_COMMIT or result.get("opening_commit") != OPENING_COMMIT:
        errors.append("commits")
    workflows = result.get("workflows", {})
    if not workflow_valid(
        workflows.get("p18_closure"),
        33_258_605_964,
        99_116_546_827,
        BASE_COMMIT,
        "2026-08-29T14:49:26Z",
        "2026-08-29T14:57:56Z",
        43,
    ):
        errors.append("p18_closure_workflow")
    if not workflow_valid(
        workflows.get("p18b_opening"),
        33_259_343_493,
        99_118_481_014,
        OPENING_COMMIT,
        "2026-08-29T15:06:33Z",
        "2026-08-29T15:13:51Z",
        43,
    ):
        errors.append("p18b_opening_workflow")
    if result.get("p18_evidence_sha256") != P18_EVIDENCE:
        errors.append("p18_evidence")
    if result.get("manual") != {"bytes": 2_940_583, "sha256": MANUAL_SHA256}:
        errors.append("manual")
    iaea = result.get("iaea_utility_codes", {})
    if iaea != {
        "repository": "https://github.com/IAEA-NDS/ENDF-utility-codes.git",
        "commit": IAEA_COMMIT,
        "files": IAEA_FILES,
        "checker_output_read": False,
    }:
        errors.append("iaea_authority")

    canonical_manifest = canonical(manifest)
    source = result.get("source_manifest", {})
    expected_source = {
        "artifact": "results/p18b_source_manifest.json.gz",
        "files": 11_400,
        "canonical_bytes": len(canonical_manifest),
        "canonical_sha256": hashlib.sha256(canonical_manifest).hexdigest(),
        "compressed_bytes": len(compressed),
        "artifact_sha256": hashlib.sha256(compressed).hexdigest(),
        "external_manifest_sha256": MANIFEST_SHA256,
    }
    if source != expected_source:
        errors.append("source_manifest_identity")
    if deterministic_gzip(canonical_manifest) != compressed:
        errors.append("source_manifest_determinism")

    expected_sample = derive_sample(manifest)
    sample = result.get("official_checker_sample", {})
    expected_counts = {
        projectile: sum(row["projectile"] == projectile for row in expected_sample)
        for projectile in sorted(MANIFEST_SHA256)
    }
    if sample != {
        "seed": SAMPLE_SEED,
        "selection": "25 lowest SHA-256 ranks per projectile plus all P18 worst/conflict files",
        "fixture_policy": "all generated P18b-G1 fixtures",
        "rows": expected_sample,
        "counts": expected_counts,
        "sha256": hashlib.sha256(canonical(expected_sample)).hexdigest(),
        "checker_output_read": False,
    }:
        errors.append("sample")
    if forbidden_key_present(sample):
        errors.append("sample_dependent_field")
    quarantine = result.get("quarantine")
    if quarantine != {
        "rodrigo_diagnostic_values_read": False,
        "rodrigo_heldout_values_read": False,
        "new_checkpoint_classification_read": False,
        "official_checker_output_read": False,
    }:
        errors.append("quarantine")
    checks = result.get("checks", {})
    if set(checks) != {
        "protocol_and_hash_log",
        "p18_closure_and_evidence",
        "manual_and_iaea_authorities",
        "source_inventory",
        "sample_frozen_without_output",
        "diagnostic_values_quarantined",
        "heldout_values_quarantined",
        "new_checkpoint_classification_quarantined",
    } or not all(value is True for value in checks.values()):
        errors.append("checks")
    if result.get("control_source_sha256") != sha256(CONTROL):
        errors.append("control_source")
    if result.get("checker_source_sha256") != sha256(Path(__file__)):
        errors.append("checker_source")
    if result.get("pass") is not True:
        errors.append("pass")
    return sorted(set(errors))


def mutation_plants(
    result: dict[str, Any], manifest: dict[str, Any], compressed: bytes
) -> dict[str, bool]:
    cases: dict[str, tuple[dict[str, Any], dict[str, Any], bytes]] = {}
    for name in (
        "protocol",
        "workflow",
        "quarantine",
        "sample",
        "conflict_reason",
        "dependent_field",
        "manifest",
    ):
        cases[name] = (copy.deepcopy(result), copy.deepcopy(manifest), compressed)
    cases["protocol"][0]["protocol_sha256"] = "0" * 64
    cases["workflow"][0]["workflows"]["p18b_opening"]["conclusion"] = "failure"
    cases["quarantine"][0]["quarantine"]["rodrigo_heldout_values_read"] = True
    cases["sample"][0]["official_checker_sample"]["rows"].pop()
    conflict = next(
        row
        for row in cases["conflict_reason"][0]["official_checker_sample"]["rows"]
        if "p18_excitation_conflict" in row["reasons"]
    )
    conflict["reasons"].remove("p18_excitation_conflict")
    cases["dependent_field"][0]["official_checker_sample"]["rows"][0]["measured_ratio"] = 0.5
    cases["manifest"][1]["corpora"]["alpha"]["files"][0]["source_sha256"] = "0" * 64
    return {
        name: bool(validate_result(planted, planted_manifest, planted_bytes))
        for name, (planted, planted_manifest, planted_bytes) in cases.items()
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-write", action="store_true")
    arguments = parser.parse_args()
    errors: list[str] = []
    try:
        if sha256(PROTOCOL) != PROTOCOL_SHA256:
            errors.append("protocol_bytes")
        if sha256(P18_PROTOCOL) != P18_PROTOCOL_SHA256:
            errors.append("p18_protocol_bytes")
        if sha256(P18_AMENDMENT) != P18_AMENDMENT_SHA256:
            errors.append("p18_amendment_bytes")
        if (
            f"{PROTOCOL_SHA256}  protocols/ACTINV-P18b_PROTOCOL.md"
            not in HASH_LOG.read_text(encoding="utf-8").splitlines()
        ):
            errors.append("hash_log")
        compressed = SOURCE_MANIFEST.read_bytes()
        manifest = decode_manifest(compressed)
        result = load(RESULT)
        if manifest is None or result is None:
            raise ValueError("missing or malformed G0 evidence")
        errors.extend(validate_result(result, manifest, compressed))
        plants = mutation_plants(result, manifest, compressed)
    except (KeyError, OSError, StopIteration, TypeError, ValueError) as error:
        errors.append(str(error))
        plants = {}
    checks = {
        "protocol_and_authorities": not any(
            item in errors
            for item in (
                "protocol_bytes",
                "p18_protocol_bytes",
                "p18_amendment_bytes",
                "hash_log",
                "protocol",
                "manual",
                "iaea_authority",
            )
        ),
        "source_manifest": not any(item.startswith(("manifest", "alpha_", "deuteron_", "neutron_", "proton_", "source_manifest")) for item in errors),
        "sample": "sample" not in errors and "sample_dependent_field" not in errors,
        "workflows_and_p18_evidence": not any(
            item in errors
            for item in ("commits", "p18_closure_workflow", "p18b_opening_workflow", "p18_evidence")
        ),
        "quarantine": "quarantine" not in errors,
        "source_binding": "control_source" not in errors and "checker_source" not in errors,
        "mutation_plants": len(plants) == 7 and all(plants.values()),
        "complete_record": not errors,
    }
    output = {
        "schema": "actinv-p18b-g0-independent-check-1",
        "gate": "P18b-G0",
        "errors": sorted(set(errors)),
        "mutation_plants": plants,
        "checks": checks,
        "pass": all(checks.values()),
    }
    if not arguments.no_write:
        OUTPUT.write_text(json.dumps(output, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(output, indent=1, sort_keys=True))
    return 0 if output["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
