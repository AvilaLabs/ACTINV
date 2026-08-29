#!/usr/bin/env python3
"""Seal P18b authorities, source-file provenance, and the output-free IAEA sample."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "protocols/ACTINV-P18b_PROTOCOL.md"
P18_PROTOCOL = ROOT / "protocols/ACTINV-P18_PROTOCOL.md"
P18_AMENDMENT = ROOT / "protocols/ACTINV-P18_AMENDMENT_1.md"
P18_SESSION = ROOT / "results/session_p18.json"
P18_VERDICT = ROOT / "results/verdict_p18.json"
P18_AUDIT = ROOT / "results/g2_p18_corpus_audit.json"
P18_CHANGED = ROOT / "results/g2_p18_changed_identities.json.gz"
P18_SEAL = ROOT / "results/p18_family_seal.json"
P18_G0 = ROOT / "results/g0_p18_seal.json"
RESULT = ROOT / "results/g0_p18b_seal.json"
SOURCE_MANIFEST = ROOT / "results/p18b_source_manifest.json.gz"
CHECKER = ROOT / "controls/check_g0_p18b.py"
HASH_LOG = ROOT / "protocols/protocol_hash.txt"

DATA_ROOT = Path(
    os.environ.get("ACTINV_P18_DATA_ROOT", "/home/connoravila/nuclear-data/tendl-2025")
)
IAEA_ROOT = Path(
    os.environ.get(
        "ACTINV_P18B_IAEA_UTILS", ROOT / "target/p18b-reference/endf-utility-codes"
    )
)
MANUAL = Path(
    os.environ.get("ACTINV_P18_ENDF_MANUAL", "/tmp/actinv-endf-manual-2024.pdf")
)

BASE_COMMIT = "d3456890cf0c4b9221ebf17f6630ef8b4fe768cc"
OPENING_COMMIT = "bf540efc3cd9525d17f69a525ab6732c648bfe93"
P18_CLOSURE_RUN = 33_258_605_964
P18_CLOSURE_JOB = 99_116_546_827
OPENING_RUN = 33_259_343_493
OPENING_JOB = 99_118_481_014
PROTOCOL_SHA256 = "69076fa2656b239addbb15fbb4727caaa2c8ea37b3aa82a141f3a2b0b619eabe"
P18_PROTOCOL_SHA256 = "002afb038bbbf1ad0bdb34149971f8d3f33a3e2590c6d04ced87bb5ada046e09"
P18_AMENDMENT_SHA256 = "8eb3f3bc657a49ebeff7cc5d7ca124cb4e4debbf094fee9d6417c01f740aa9e0"
MANUAL_SHA256 = "77a0fee413c3b1d5d74a161ed9fe7f77bbcbc58a654304851b7b2b400183d022"
IAEA_COMMIT = "c2a6718bd831b5c8a6e975beb1946954b1d73c40"
IAEA_FILES = {
    "fizcon/fizcon.f": "15eac8dbcc1f1c0b8825d9e2a487d7e26f4717ccacad373f226a01c721e7527e",
    "checkr/checkr.f": "739169c525663a3a80d62f8047243b6d3a0d2b36e05cf95a7336ae58363d684e",
    "README.md": "b31bb9034edc43ad3ef623eebc154361fad7131a56e4cc087ab52760843423fe",
    "LICENSE.MIT": "f9d773e3ae7e2b9136e8e14b6cdfeac38044595b7a5f1dcdb5cdb6614565cb87",
}
P18_EVIDENCE = {
    "session": (P18_SESSION, "c782d5f27a88286e44aa019c0d645fd665811204d9158fbff63bdbedb6de8f54"),
    "verdict": (P18_VERDICT, "e9dbea1e2e5ade98cb14afa7f73b2eb9dd82da183364a75ea8d36dade1eb8863"),
    "audit": (P18_AUDIT, "e20fba865c36131f27bce7ac110957336c55d9c8455e79a8ddac0edde66df9cb"),
    "changed_identities": (
        P18_CHANGED,
        "606347ce4d12788451be3a4a3765bfa5305613631eb37ea5de30d163546e083e",
    ),
    "family_seal": (
        P18_SEAL,
        "fb2fd35b02aa4d9629d9740d638b7650f97e8ba9d8c4dcd70ee238c31c45dfed",
    ),
    "g0": (P18_G0, "e419e8eeb506c28440744e8a3ceb6922f1d066b8d673f534ce0567dc59f82af3"),
}
SAMPLE_SEED = "ACTINV-P18b-IAEA-SAMPLE-v1"
MANIFESTS = {
    "neutron": {
        "path": "staging/TENDL-n-working.manifest.json",
        "sha256": "a6d17f996153d2671c0c51bfb6303e2a87a5af03e0696bfb34d668a31dbfb2a2",
        "frozen_sha256": "b578ab395c6c71d7727dfb0513e88effd96692862664a6279802638531239b67",
    },
    "proton": {
        "path": "staging/TENDL-p.manifest.json",
        "sha256": "98a8bd55784c326b8696de91f494111326378e776a975a512e59806a8c9ec2ef",
    },
    "deuteron": {
        "path": "staging/TENDL-d.manifest.json",
        "sha256": "afb52c55b2a1babca998cc3d8af0f7004c64f85d160e3c5aabf16a05839355d9",
    },
    "alpha": {
        "path": "staging/TENDL-a.manifest.json",
        "sha256": "e3aaf11e60c46b43361796c2c297bab4fb714fe57ab26a315594f2b4799dfdbf",
    },
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


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


def command(arguments: list[str], cwd: Path = ROOT) -> str:
    completed = subprocess.run(
        arguments,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=120,
        check=False,
    )
    require(completed.returncode == 0, f"command failed: {' '.join(arguments)}")
    return completed.stdout.strip()


def is_ancestor(ancestor: str, descendant: str) -> bool:
    return (
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", ancestor, descendant],
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        ).returncode
        == 0
    )


def workflow(run_id: int, job_id: int, head_sha: str) -> dict[str, Any]:
    fields = "status,conclusion,headSha,headBranch,event,createdAt,updatedAt,url,jobs"
    value = json.loads(
        command(
            [
                "gh",
                "run",
                "view",
                str(run_id),
                "--repo",
                "AvilaLabs/ACTINV",
                "--json",
                fields,
            ]
        )
    )
    jobs = [job for job in value.pop("jobs") if job.get("databaseId") == job_id]
    require(len(jobs) == 1, f"workflow {run_id} job identity")
    require(value["status"] == "completed" and value["conclusion"] == "success", "workflow")
    require(value["headSha"] == head_sha and value["headBranch"] == "master", "workflow head")
    value["job_id"] = job_id
    value["job_conclusion"] = jobs[0]["conclusion"]
    value["substantive_steps"] = sum(
        step.get("conclusion") == "success"
        and not step.get("name", "").startswith("Post ")
        and step.get("name") != "Complete job"
        for step in jobs[0].get("steps", [])
    )
    return value


def source_rows() -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    corpora: dict[str, Any] = {}
    lookup: dict[str, dict[str, Any]] = {}
    for projectile, config in MANIFESTS.items():
        path = DATA_ROOT / config["path"]
        require(sha256(path) == config["sha256"], f"{projectile} source manifest")
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = []
        for item in payload["files"]:
            source_hash = item.get("working_sha256", item.get("sha256"))
            row = {
                "name": item["name"],
                "bytes": item["bytes"],
                "source_sha256": source_hash,
            }
            if "official_sha256" in item:
                row["official_sha256"] = item["official_sha256"]
                row["byte_identical"] = item["byte_identical"]
            rows.append(row)
        rows.sort(key=lambda row: row["name"])
        require(len(rows) == 2_850 and len({row["name"] for row in rows}) == 2_850, "inventory")
        lookup[projectile] = {row["name"]: row for row in rows}
        identity = {
            "source_manifest": config["path"],
            "source_manifest_sha256": config["sha256"],
            "files": rows,
        }
        if "frozen_sha256" in config:
            identity["frozen_manifest_sha256"] = config["frozen_sha256"]
        corpora[projectile] = identity
    artifact = {"schema": "actinv-p18b-source-manifest-1", "corpora": corpora}
    return artifact, lookup


def changed_identities() -> list[dict[str, Any]]:
    with gzip.open(P18_CHANGED, "rt", encoding="utf-8") as stream:
        value = json.load(stream)
    require(isinstance(value, list) and len(value) == 45_320, "P18 changed identity inventory")
    return value


def sample_rows(lookup: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    selected: dict[tuple[str, str], set[str]] = {}
    for projectile, rows in lookup.items():
        ranked = sorted(
            rows.values(),
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
        name = corpus["worst"]["file"]
        selected.setdefault((projectile, name), set()).add("p18_worst")

    for row in changed_identities():
        if row["decision"] == "mf8_q_excitation_conflict":
            selected.setdefault((row["projectile"], row["file"]), set()).add(
                "p18_excitation_conflict"
            )

    output = []
    for (projectile, name), reasons in sorted(selected.items()):
        source = lookup[projectile].get(name)
        require(source is not None, f"sample file absent from {projectile} manifest: {name}")
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


def official_identity() -> dict[str, Any]:
    require(command(["git", "rev-parse", "HEAD"], IAEA_ROOT) == IAEA_COMMIT, "IAEA commit")
    files = {}
    for relative, expected in IAEA_FILES.items():
        path = IAEA_ROOT / relative
        actual = sha256(path)
        require(actual == expected, f"IAEA file changed: {relative}")
        files[relative] = {"bytes": path.stat().st_size, "sha256": actual}
    return {
        "repository": "https://github.com/IAEA-NDS/ENDF-utility-codes.git",
        "commit": IAEA_COMMIT,
        "files": files,
        "checker_output_read": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-write", action="store_true")
    arguments = parser.parse_args()

    require(sha256(PROTOCOL) == PROTOCOL_SHA256, "P18b protocol")
    require(sha256(P18_PROTOCOL) == P18_PROTOCOL_SHA256, "P18 protocol")
    require(sha256(P18_AMENDMENT) == P18_AMENDMENT_SHA256, "P18 amendment")
    require(sha256(MANUAL) == MANUAL_SHA256, "ENDF manual")
    require(is_ancestor(BASE_COMMIT, OPENING_COMMIT), "P18 closure ancestry")
    require(is_ancestor(OPENING_COMMIT, "HEAD"), "P18b opening ancestry")
    require(
        f"{PROTOCOL_SHA256}  protocols/ACTINV-P18b_PROTOCOL.md"
        in HASH_LOG.read_text(encoding="utf-8").splitlines(),
        "P18b hash log",
    )
    evidence_hashes = {}
    for name, (path, expected) in P18_EVIDENCE.items():
        actual = sha256(path)
        require(actual == expected, f"P18 evidence changed: {name}")
        evidence_hashes[name] = actual

    artifact, lookup = source_rows()
    artifact_bytes = canonical(artifact)
    compressed = deterministic_gzip(artifact_bytes)
    if not arguments.no_write:
        SOURCE_MANIFEST.write_bytes(compressed)
    require(SOURCE_MANIFEST.read_bytes() == compressed, "source manifest is not deterministic")
    sample = sample_rows(lookup)
    sample_counts = {
        projectile: sum(row["projectile"] == projectile for row in sample)
        for projectile in sorted(MANIFESTS)
    }
    checks = {
        "protocol_and_hash_log": True,
        "p18_closure_and_evidence": True,
        "manual_and_iaea_authorities": True,
        "source_inventory": sum(len(rows) for rows in lookup.values()) == 11_400,
        "sample_frozen_without_output": True,
        "diagnostic_values_quarantined": True,
        "heldout_values_quarantined": True,
        "new_checkpoint_classification_quarantined": True,
    }
    result = {
        "schema": "actinv-p18b-g0-seal-1",
        "gate": "P18b-G0",
        "protocol_sha256": PROTOCOL_SHA256,
        "base_commit": BASE_COMMIT,
        "opening_commit": OPENING_COMMIT,
        "workflows": {
            "p18_closure": workflow(P18_CLOSURE_RUN, P18_CLOSURE_JOB, BASE_COMMIT),
            "p18b_opening": workflow(OPENING_RUN, OPENING_JOB, OPENING_COMMIT),
        },
        "p18_evidence_sha256": evidence_hashes,
        "manual": {"bytes": MANUAL.stat().st_size, "sha256": MANUAL_SHA256},
        "iaea_utility_codes": official_identity(),
        "source_manifest": {
            "artifact": "results/p18b_source_manifest.json.gz",
            "files": 11_400,
            "canonical_bytes": len(artifact_bytes),
            "canonical_sha256": hashlib.sha256(artifact_bytes).hexdigest(),
            "compressed_bytes": len(compressed),
            "artifact_sha256": hashlib.sha256(compressed).hexdigest(),
            "external_manifest_sha256": {
                projectile: config["sha256"] for projectile, config in MANIFESTS.items()
            },
        },
        "official_checker_sample": {
            "seed": SAMPLE_SEED,
            "selection": "25 lowest SHA-256 ranks per projectile plus all P18 worst/conflict files",
            "fixture_policy": "all generated P18b-G1 fixtures",
            "rows": sample,
            "counts": sample_counts,
            "sha256": hashlib.sha256(canonical(sample)).hexdigest(),
            "checker_output_read": False,
        },
        "quarantine": {
            "rodrigo_diagnostic_values_read": False,
            "rodrigo_heldout_values_read": False,
            "new_checkpoint_classification_read": False,
            "official_checker_output_read": False,
        },
        "control_source_sha256": sha256(Path(__file__)),
        "checker_source_sha256": sha256(CHECKER),
        "checks": checks,
        "pass": all(checks.values()),
    }
    if not arguments.no_write:
        RESULT.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "schema": result["schema"],
                "sample_counts": sample_counts,
                "sample_files": len(sample),
                "source_files": 11_400,
                "checks": checks,
                "pass": result["pass"],
            },
            indent=1,
            sort_keys=True,
        )
    )
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
