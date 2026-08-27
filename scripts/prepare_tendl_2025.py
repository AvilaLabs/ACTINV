#!/usr/bin/env python3
"""Safely flatten and hash one official TENDL-2025 s30 archive.

The raw archive, extracted evaluations and detailed manifest stay outside Git.  This
utility exposes the archive's regular files only after every member and byte has
been checked, so an interrupted or malformed extraction cannot be mistaken for a
complete builder input directory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import tarfile


ARCHIVES = {
    "neutron": ("TENDL-n.tgz", 3_517_450_425),
    "proton": ("TENDL-p.tgz", 2_352_215_809),
    "deuteron": ("TENDL-d.tgz", 3_063_536_212),
    "alpha": ("TENDL-a.tgz", 1_604_280_144),
}
FILE_PREFIX = {
    "neutron": "n-",
    "proton": "p-",
    "deuteron": "d-",
    "alpha": "a-",
}
ARCHIVE_BASE_URL = "https://tendl.imperial.ac.uk/tendl_2025/tar_files"
EXPECTED_FILES = 2_850
BLOCK_BYTES = 1024 * 1024


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(BLOCK_BYTES):
            digest.update(block)
    return digest.hexdigest()


def canonical_json(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def validate_member(
    member: tarfile.TarInfo, names: set[str], projectile: str
) -> str | None:
    """Return a safe flat filename for a regular member, or None for a directory."""
    raw = PurePosixPath(member.name)
    if raw.is_absolute() or ".." in raw.parts:
        raise ValueError(f"unsafe archive path: {member.name!r}")
    if member.isdir():
        return None
    if not member.isfile():
        raise ValueError(
            f"unsupported archive member type for {member.name!r}; "
            "only directories and regular files are allowed"
        )
    name = raw.name
    if not name or name in (".", ".."):
        raise ValueError(f"invalid archive filename: {member.name!r}")
    if not name.startswith(FILE_PREFIX[projectile]) or not name.endswith(".tendl"):
        raise ValueError(
            f"unexpected {projectile} s30 evaluation filename: {member.name!r}"
        )
    if name in names:
        raise ValueError(f"duplicate flattened archive filename: {name!r}")
    if member.size <= 0:
        raise ValueError(f"empty evaluation file: {member.name!r}")
    names.add(name)
    return name


def file_manifest_hash(files: list[dict[str, object]]) -> str:
    digest = hashlib.sha256()
    for entry in sorted(files, key=lambda value: str(value["name"]).encode()):
        digest.update(f"{entry['sha256']}  {entry['name']}\n".encode())
    return digest.hexdigest()


def extract_archive(
    projectile: str, archive: Path, destination: Path, manifest_path: Path
) -> dict[str, object]:
    expected_name, expected_size = ARCHIVES[projectile]
    archive = archive.resolve()
    destination = destination.resolve()
    manifest_path = manifest_path.resolve()
    if archive.name != expected_name:
        raise ValueError(f"expected archive name {expected_name!r}, got {archive.name!r}")
    size = archive.stat().st_size
    if size != expected_size:
        raise ValueError(
            f"{archive} has {size} bytes; expected complete archive size {expected_size}"
        )
    if destination.exists() or manifest_path.exists():
        raise FileExistsError(
            "refusing to overwrite an existing destination or manifest: "
            f"{destination}, {manifest_path}"
        )

    destination.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.with_name(f".{destination.name}.extracting-{os.getpid()}")
    staging.mkdir()
    names: set[str] = set()
    files: list[dict[str, object]] = []
    members = 0
    try:
        with tarfile.open(archive, mode="r:gz") as bundle:
            for member in bundle:
                members += 1
                name = validate_member(member, names, projectile)
                if name is None:
                    continue
                source = bundle.extractfile(member)
                if source is None:
                    raise ValueError(f"cannot read regular archive member {member.name!r}")
                output = staging / name
                digest = hashlib.sha256()
                written = 0
                with source, output.open("xb") as stream:
                    while block := source.read(BLOCK_BYTES):
                        digest.update(block)
                        stream.write(block)
                        written += len(block)
                if written != member.size:
                    raise ValueError(
                        f"short extraction for {member.name!r}: {written} != {member.size}"
                    )
                files.append(
                    {
                        "name": name,
                        "archive_path": member.name,
                        "bytes": written,
                        "sha256": digest.hexdigest(),
                    }
                )
        if len(files) != EXPECTED_FILES:
            raise ValueError(
                f"archive has {len(files)} regular files; expected {EXPECTED_FILES}"
            )
        files.sort(key=lambda value: str(value["name"]).encode())
        manifest = {
            "schema": "actinv-tendl-archive-manifest-1",
            "projectile": projectile,
            "official_url": f"{ARCHIVE_BASE_URL}/{expected_name}",
            "archive": expected_name,
            "archive_bytes": size,
            "archive_sha256": sha256_file(archive),
            "archive_members": members,
            "regular_files": len(files),
            "file_manifest_definition": "SHA256 of sorted '<sha256>  <flat-name>\\n' records",
            "file_manifest_sha256": file_manifest_hash(files),
            "files": files,
        }
        manifest_bytes = canonical_json(manifest)
        manifest["manifest_sha256"] = hashlib.sha256(manifest_bytes).hexdigest()
        # The self-hash covers the canonical manifest before the self-hash field is added.
        manifest["manifest_sha256_definition"] = (
            "SHA256 of this canonical JSON without manifest_sha256 and "
            "manifest_sha256_definition"
        )
        temporary_manifest = manifest_path.with_name(
            f".{manifest_path.name}.writing-{os.getpid()}"
        )
        temporary_manifest.write_bytes(canonical_json(manifest))
        os.replace(staging, destination)
        os.replace(temporary_manifest, manifest_path)
        return manifest
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("projectile", choices=sorted(ARCHIVES))
    parser.add_argument("archive", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("manifest", type=Path)
    arguments = parser.parse_args()
    manifest = extract_archive(
        arguments.projectile,
        arguments.archive,
        arguments.destination,
        arguments.manifest,
    )
    summary = {key: value for key, value in manifest.items() if key != "files"}
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
