#!/usr/bin/env python3
"""P12-G6: bind the v1.0 release payload to its evidence and green CI run."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

import check_p12


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results" / "g6_p12_complete.json"
SESSION = ROOT / "sessions_P12.md"
MANIFEST = ROOT / "MANIFEST.sha256"
GATE_FILES = tuple(check_p12.GATE_FILES[gate] for gate in ("G1", "G2", "G3", "G4", "G5"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def command(arguments: list[str], *, timeout: int = 60) -> str:
    result = subprocess.run(
        arguments,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )
    if result.returncode:
        tail = (result.stdout + result.stderr)[-3000:]
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(arguments)}\n{tail}")
    return result.stdout.strip()


def repository_paths() -> list[str]:
    output = command(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"]
    )
    paths = sorted(path for path in output.split("\0") if path and path != "MANIFEST.sha256")
    if any(Path(path).is_absolute() or ".." in Path(path).parts for path in paths):
        raise RuntimeError("repository inventory contains an unsafe path")
    return paths


def rendered_manifest() -> str:
    return "".join(f"{sha256(ROOT / path)}  ./{path}\n" for path in repository_paths())


def manifest_evidence() -> dict[str, object]:
    expected = rendered_manifest()
    actual = MANIFEST.read_text() if MANIFEST.is_file() else ""
    lines = actual.splitlines()
    entries: dict[str, str] = {}
    valid_lines = True
    duplicates: list[str] = []
    for line in lines:
        match = re.fullmatch(r"([0-9a-f]{64})  \./(.+)", line)
        if match is None:
            valid_lines = False
            continue
        path = match.group(2)
        if path in entries:
            duplicates.append(path)
        entries[path] = match.group(1)
    paths = repository_paths()
    hashes_match = all(entries.get(path) == sha256(ROOT / path) for path in paths)
    exact_inventory = set(entries) == set(paths)
    reproduced = actual == expected
    passed = bool(valid_lines and not duplicates and hashes_match and exact_inventory and reproduced)
    return {
        "entries": len(entries),
        "expected_entries": len(paths),
        "valid_lines": valid_lines,
        "duplicates": duplicates,
        "self_excluded": "MANIFEST.sha256" not in entries,
        "exact_inventory": exact_inventory,
        "hashes_match": hashes_match,
        "regeneration_byte_identical": reproduced,
        "pass": passed,
    }


def github_run(repository: str, run_id: int) -> dict[str, object]:
    raw = json.loads(
        command(["gh", "api", f"repos/{repository}/actions/runs/{run_id}"], timeout=120)
    )
    fields = {
        "run_id": raw.get("id"),
        "run_attempt": raw.get("run_attempt"),
        "name": raw.get("name"),
        "event": raw.get("event"),
        "status": raw.get("status"),
        "conclusion": raw.get("conclusion"),
        "head_branch": raw.get("head_branch"),
        "head_sha": raw.get("head_sha"),
        "workflow_path": raw.get("path"),
        "url": raw.get("html_url"),
        "created_at": raw.get("created_at"),
        "updated_at": raw.get("updated_at"),
    }
    return fields


def session_evidence(
    *, release_commit: str, repository: str, run: dict[str, object], gate_hashes: dict[str, str]
) -> dict[str, object]:
    if not SESSION.is_file():
        return {"file": "sessions_P12.md", "present": False, "pass": False}
    text = SESSION.read_text()
    required = {
        "release_commit": release_commit,
        "repository": f"https://github.com/{repository}.git",
        "run_id": str(run["run_id"]),
        "run_url": str(run["url"]),
        "verdict": "P12-CONDITIONAL",
        **{f"result:{name}": digest for name, digest in gate_hashes.items()},
        **{
            f"protocol:{Path(relative).name}": digest
            for relative, digest in check_p12.PROTOCOL_HASHES.items()
        },
    }
    missing = [name for name, fragment in required.items() if fragment not in text]
    return {
        "file": "sessions_P12.md",
        "sha256": sha256(SESSION),
        "required_fragments": len(required),
        "missing": missing,
        "pass": not missing,
    }


def remote_evidence(release_commit: str, repository: str) -> dict[str, object]:
    origin = command(["git", "remote", "get-url", "origin"])
    expected = f"https://github.com/{repository}.git"
    object_type = command(["git", "cat-file", "-t", release_commit])
    release_tree = command(["git", "rev-parse", f"{release_commit}^{{tree}}"])
    head = command(["git", "rev-parse", "HEAD"])
    origin_head = command(["git", "rev-parse", "origin/master"])
    contains = subprocess.run(
        ["git", "merge-base", "--is-ancestor", release_commit, "origin/master"],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode == 0
    passed = bool(
        origin == expected
        and object_type == "commit"
        and re.fullmatch(r"[0-9a-f]{40}", release_tree)
        and subprocess.run(
            ["git", "merge-base", "--is-ancestor", release_commit, head],
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode
        == 0
        and contains
    )
    return {
        "origin": origin,
        "expected_origin": expected,
        "origin_master": origin_head,
        "release_tree": release_tree,
        "release_is_ancestor_of_head": subprocess.run(
            ["git", "merge-base", "--is-ancestor", release_commit, head],
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode
        == 0,
        "origin_master_contains_release": contains,
        "pass": passed,
    }


def derive(release_commit: str, repository: str, run_id: int) -> dict[str, object]:
    if re.fullmatch(r"[0-9a-f]{40}", release_commit) is None:
        raise ValueError("release commit must be a complete lowercase Git object ID")
    gate_hashes = {name: sha256(ROOT / "results" / name) for name in GATE_FILES}
    run = github_run(repository, run_id)
    github_checks = {
        "run_id": run["run_id"] == run_id,
        "head_sha": run["head_sha"] == release_commit,
        "head_branch": run["head_branch"] in ("master", "main"),
        "workflow": run["name"] == "controls"
        and run["workflow_path"] == ".github/workflows/ci.yml",
        "event": run["event"] == "push",
        "completed": run["status"] == "completed",
        "conclusion": run["conclusion"] == "success",
        "url": isinstance(run["url"], str)
        and run["url"].startswith(f"https://github.com/{repository}/actions/runs/"),
    }
    github = {**run, "checks": github_checks, "pass": all(github_checks.values())}
    preclose = check_p12.derive(through_g5=True)
    existing_verdict = (ROOT / "results" / "verdict_p12.json")
    verdict_byte_identical = existing_verdict.is_file() and (
        existing_verdict.read_text() == check_p12.rendered_verdict()
    )
    manifest = manifest_evidence()
    regeneration = {
        "preclose_rederived": preclose.get("pass") is True,
        "verdict_regeneration_byte_identical": verdict_byte_identical,
        "manifest_regeneration_byte_identical": manifest["regeneration_byte_identical"],
    }
    regeneration["pass"] = all(regeneration.values())
    session = session_evidence(
        release_commit=release_commit,
        repository=repository,
        run=run,
        gate_hashes=gate_hashes,
    )
    remote = remote_evidence(release_commit, repository)
    output = {
        "schema": "actinv-p12-g6-complete-1",
        "gate": "P12-G6",
        "release_commit": release_commit,
        "gate_result_sha256": gate_hashes,
        "protocol_sha256": check_p12.PROTOCOL_HASHES,
        "session": session,
        "manifest": manifest,
        "remote": remote,
        "github": github,
        "regeneration": regeneration,
    }
    output["pass"] = bool(
        preclose.get("pass") is True
        and session["pass"]
        and manifest["pass"]
        and remote["pass"]
        and github["pass"]
        and regeneration["pass"]
    )
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-commit")
    parser.add_argument("--repository", default="AvilaLabs/ACTINV")
    parser.add_argument("--github-run-id", type=int)
    parser.add_argument(
        "--write-manifest",
        action="store_true",
        help="regenerate MANIFEST.sha256 over every repository file except the manifest itself",
    )
    parser.add_argument(
        "--allow-incomplete",
        action="store_true",
        help="write intermediate fixed-point evidence without treating incompleteness as a command failure",
    )
    arguments = parser.parse_args()
    if arguments.write_manifest:
        MANIFEST.write_text(rendered_manifest())
        print(json.dumps(manifest_evidence(), indent=1, sort_keys=True))
        return 0
    if not arguments.release_commit or arguments.github_run_id is None:
        parser.error("--release-commit and --github-run-id are required unless --write-manifest is used")
    output = derive(arguments.release_commit, arguments.repository, arguments.github_run_id)
    payload = json.dumps(output, indent=1, sort_keys=True) + "\n"
    RESULT.write_text(payload)
    print(payload, end="")
    return 0 if output["pass"] or arguments.allow_incomplete else 1


if __name__ == "__main__":
    raise SystemExit(main())
