#!/usr/bin/env python3
"""Create the hash-pinned P10 neutron working corpus from the official extraction.

The official TENDL-2025 files remain untouched.  P10 Amendment D permits exactly
two repairs in Pb-208 aggregate MF=3 sections; every other output byte must match
the official per-file manifest.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil


EXPECTED_FILES = 2_850
BLOCK_BYTES = 1024 * 1024
OFFICIAL_DETAILED_MANIFEST_SHA256 = (
    "b578ab395c6c71d7727dfb0513e88effd96692862664a6279802638531239b67"
)
OFFICIAL_FILE_MANIFEST_SHA256 = (
    "f38df7c49da6cef8ac3d23c45c81dfb394829eefd38ee4af0db6dde92f0beaa4"
)
PB208_NAME = "n-Pb208.tendl"
PB208_SHA256 = "32249bf71ee52a159ef8f94a4cb85d5c456aba13e1a4c4d9129c2304b6dc4137"
PB208_OLD_LINES = {
    781: b" 6.000000+5 4.262064-7 8.000000+5 4.925328-7 1.000000+6        NaN8237 3  1   36\n",
    1613: b" 6.000000+5 4.262064-7 8.000000+5 4.925328-7 1.000000+6        NaN8237 3  3   36\n",
}
REPLACEMENT_FIELD = b" 4.925328-7"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(BLOCK_BYTES):
            digest.update(block)
    return digest.hexdigest()


def canonical_json(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def compact_manifest_hash(files: list[dict[str, object]]) -> str:
    digest = hashlib.sha256()
    for entry in sorted(files, key=lambda value: str(value["name"]).encode()):
        digest.update(f"{entry['working_sha256']}  {entry['name']}\n".encode())
    return digest.hexdigest()


def repair_pb208(source: bytes) -> tuple[bytes, list[dict[str, object]]]:
    lines = source.splitlines(keepends=True)
    repairs = []
    for line_number, expected in PB208_OLD_LINES.items():
        if line_number > len(lines) or lines[line_number - 1] != expected:
            raise ValueError(f"Pb-208 line {line_number} does not match Amendment D")
        if expected.count(b"        NaN") != 1:
            raise AssertionError("frozen Pb-208 record does not contain one NaN field")
        replacement = expected.replace(b"        NaN", REPLACEMENT_FIELD)
        if len(replacement) != len(expected):
            raise AssertionError("Pb-208 replacement changed the fixed-width record length")
        lines[line_number - 1] = replacement
        repairs.append(
            {
                "line": line_number,
                "mat": 8237,
                "mf": 3,
                "mt": 1 if line_number == 781 else 3,
                "energy_eV": 1_000_000,
                "original_field": "NaN",
                "replacement_field": REPLACEMENT_FIELD.decode().strip(),
                "rule": "carry forward the immediately preceding finite left-branch value",
            }
        )
    if b"        NaN" in b"".join(lines):
        raise ValueError("Pb-208 contains an undeclared NaN field")
    return b"".join(lines), repairs


def copy_regular(source: Path, output: Path, expected_sha256: str) -> tuple[int, str]:
    before = source.stat()
    source_digest = hashlib.sha256()
    output_digest = hashlib.sha256()
    written = 0
    with source.open("rb") as input_stream, output.open("xb") as output_stream:
        while block := input_stream.read(BLOCK_BYTES):
            source_digest.update(block)
            output_digest.update(block)
            output_stream.write(block)
            written += len(block)
    after = source.stat()
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise ValueError(f"source changed while copied: {source}")
    actual = source_digest.hexdigest()
    if actual != expected_sha256:
        raise ValueError(f"official source hash mismatch for {source.name}: {actual}")
    return written, output_digest.hexdigest()


def derive_working_corpus(
    source: Path, official_manifest_path: Path, destination: Path, manifest_path: Path
) -> dict[str, object]:
    source = source.resolve()
    official_manifest_path = official_manifest_path.resolve()
    destination = destination.resolve()
    manifest_path = manifest_path.resolve()
    if destination.exists() or manifest_path.exists():
        raise FileExistsError(
            "refusing to overwrite an existing destination or manifest: "
            f"{destination}, {manifest_path}"
        )
    if source == destination or source in destination.parents:
        raise ValueError("working destination must not be the official source or its child")
    if destination in manifest_path.parents:
        raise ValueError("manifest must be outside the unpublished destination directory")
    if sha256_file(official_manifest_path) != OFFICIAL_DETAILED_MANIFEST_SHA256:
        raise ValueError("official detailed neutron manifest hash mismatch")
    official = json.loads(official_manifest_path.read_text())
    if (
        official.get("projectile") != "neutron"
        or official.get("regular_files") != EXPECTED_FILES
        or official.get("file_manifest_sha256") != OFFICIAL_FILE_MANIFEST_SHA256
    ):
        raise ValueError("official detailed neutron manifest metadata mismatch")
    expected = {str(entry["name"]): entry for entry in official["files"]}
    if len(expected) != EXPECTED_FILES or expected.get(PB208_NAME, {}).get("sha256") != PB208_SHA256:
        raise ValueError("official detailed neutron manifest file inventory mismatch")

    entries = sorted(source.iterdir(), key=lambda path: path.name.encode())
    if [path.name for path in entries] != sorted(expected, key=str.encode):
        raise ValueError("official neutron directory does not match its detailed manifest")
    for path in entries:
        metadata = path.stat(follow_symlinks=False)
        if path.is_symlink() or not path.is_file() or metadata.st_nlink < 1:
            raise ValueError(f"official neutron input is not a regular file: {path}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.with_name(f".{destination.name}.preparing-{os.getpid()}")
    staging.mkdir()
    files: list[dict[str, object]] = []
    repairs: list[dict[str, object]] = []
    try:
        for path in entries:
            output = staging / path.name
            declared = str(expected[path.name]["sha256"])
            if path.name == PB208_NAME:
                raw = path.read_bytes()
                if hashlib.sha256(raw).hexdigest() != declared:
                    raise ValueError("official Pb-208 hash mismatch")
                repaired, repairs = repair_pb208(raw)
                output.write_bytes(repaired)
                written = len(repaired)
                working_sha256 = hashlib.sha256(repaired).hexdigest()
            else:
                written, working_sha256 = copy_regular(path, output, declared)
            files.append(
                {
                    "name": path.name,
                    "bytes": written,
                    "official_sha256": declared,
                    "working_sha256": working_sha256,
                    "byte_identical": declared == working_sha256,
                }
            )
        if len(files) != EXPECTED_FILES or len(repairs) != 2:
            raise ValueError("derived neutron corpus has an unexpected file or repair count")
        manifest = {
            "schema": "actinv-tendl-neutron-working-manifest-1",
            "protocol_amendment": "ACTINV-P10_AMENDMENT_D.md",
            "official_detailed_manifest_sha256": OFFICIAL_DETAILED_MANIFEST_SHA256,
            "official_file_manifest_sha256": OFFICIAL_FILE_MANIFEST_SHA256,
            "preparation_program_sha256": sha256_file(Path(__file__).resolve()),
            "files": files,
            "regular_files": len(files),
            "byte_identical_files": sum(bool(entry["byte_identical"]) for entry in files),
            "working_file_manifest_definition": (
                "SHA256 of sorted '<working_sha256>  <flat-name>\\n' records"
            ),
            "working_file_manifest_sha256": compact_manifest_hash(files),
            "repairs": repairs,
        }
        temporary_manifest = manifest_path.with_name(
            f".{manifest_path.name}.writing-{os.getpid()}"
        )
        temporary_manifest.write_bytes(canonical_json(manifest))
        os.replace(staging, destination)
        os.replace(temporary_manifest, manifest_path)
        manifest["detailed_working_manifest_sha256"] = sha256_file(manifest_path)
        return manifest
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("official_manifest", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("working_manifest", type=Path)
    arguments = parser.parse_args()
    manifest = derive_working_corpus(
        arguments.source,
        arguments.official_manifest,
        arguments.destination,
        arguments.working_manifest,
    )
    summary = {key: value for key, value in manifest.items() if key != "files"}
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
