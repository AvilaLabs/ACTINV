#!/usr/bin/env python3
"""Install exact, externally hosted CI data assets from the pinned seed manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import tempfile
import time
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "scripts/ci_data_seed.json"
RELEASE = "ci-data-cache-2026-10-04-v1"
RELEASE_BASE = f"https://github.com/AvilaLabs/ACTINV/releases/download/{RELEASE}/"
TOTAL_BYTES = 166_789_318
SOCKET_TIMEOUT_S = 30
TOTAL_DEADLINE_S = 120
CHUNK_BYTES = 1024 * 1024
MAX_MANIFEST_BYTES = 128 * 1024

CI_SHA_PATH = ROOT / "scripts/ci_data.sha256"
CATALOG_PATH = ROOT / "crates/actinv-cli/data/actinv-data-catalog-v1.1.0.json"
FNS_CASE_PATH = ROOT / "examples/fns_iron/case.json"
TENDL_BASE = "https://www-nds.iaea.org/public/download-endf/TENDL-2023/n/"
TENDL_BYTES = {
    "n_026-Fe-52M_2620.dat": 4_669_080,
    "n_026-Fe-52_2619.dat": 3_393_406,
    "n_026-Fe-53M_2623.dat": 4_527_794,
    "n_026-Fe-53_2622.dat": 3_725_670,
    "n_026-Fe-54_2625.dat": 3_594_962,
    "n_026-Fe-55_2628.dat": 3_592_666,
    "n_026-Fe-56_2631.dat": 3_938_788,
    "n_026-Fe-57_2634.dat": 3_884_914,
    "n_026-Fe-58_2637.dat": 3_179_140,
    "n_026-Fe-59_2640.dat": 3_411_938,
}
DECAY_SOURCES = {
    "decay/endf-b-viii-0_decay.dat": (
        "https://www-nds.iaea.org/public/download-endf/ENDF-B-VIII.0/_backup-by-NSUB/zip/endf-b-viii-0_decay.sublib.zip",
        "6f04cf009086c179021f243a58dadc2d5bb078de5ba39c4fe46ccad77d228ddb",
    ),
    "decay/jeff-3-3_decay.dat": (
        "https://www-nds.iaea.org/public/download-endf/JEFF-3.3/_backup-by-NSUB/zip/jeff-3-3_decay.sublib.zip",
        "850b8b7f85f8d88b6ad826c4cd341aaaffabd525c8ecf3c588a0ad437bf5d123",
    ),
}
FNS_PATH = "fns/fns.zip"
PROVIDERS = {
    "tendl": "IAEA Nuclear Data Section (TENDL-2023)",
    "endf": "IAEA Nuclear Data Section (ENDF/B-VIII.0)",
    "jeff": "IAEA Nuclear Data Section (JEFF-3.3)",
    "fns": "IAEA CoNDERC (JAERI FNS source archive)",
}


class SeedError(ValueError):
    """A manifest, source, or installed seed does not match its pinned identity."""


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SeedError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _read_json(path: Path, cap: int):
    metadata = path.lstat()
    if path.is_symlink() or not path.is_file() or metadata.st_size > cap:
        raise SeedError(f"unsafe or oversized JSON file: {path}")
    with path.open("rb") as stream:
        raw = stream.read(cap + 1)
    if len(raw) > cap:
        raise SeedError(f"JSON file grew beyond its {cap}-byte limit: {path}")
    try:
        return json.loads(raw, object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SeedError(f"invalid JSON file {path}: {error}") from error


def load_manifest(manifest_path=None):
    """Load the tracked manifest while rejecting duplicate keys and unsafe files."""
    return _read_json(Path(manifest_path or MANIFEST_PATH), MAX_MANIFEST_BYTES)


def _sha256_file(path: Path, maximum=None):
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(CHUNK_BYTES), b""):
            size += len(block)
            if maximum is not None and size > maximum:
                raise SeedError(f"file grew beyond its declared {maximum}-byte size: {path}")
            digest.update(block)
    return size, digest.hexdigest()


def _expected_authorities():
    ci_hashes = {}
    for line_number, line in enumerate(CI_SHA_PATH.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        fields = line.split()
        if len(fields) != 2 or len(fields[0]) != 64:
            raise SeedError(f"malformed pinned CI checksum on line {line_number}")
        digest, logical = fields
        if logical in ci_hashes:
            raise SeedError(f"duplicate CI checksum path: {logical}")
        ci_hashes[logical] = digest

    expected_ci_paths = {f"tendl/{name}" for name in _tendl_names()}
    expected_ci_paths.add("decay/endf-b-viii-0_decay.dat")
    if set(ci_hashes) != expected_ci_paths:
        raise SeedError("tracked CI checksum population differs from the frozen eleven files")

    expected = {}
    for logical, digest in ci_hashes.items():
        if not logical.startswith("tendl/"):
            continue
        filename = PurePosixPath(logical).name
        if filename not in TENDL_BYTES:
            raise SeedError(f"unexpected TENDL source file: {filename}")
        expected[logical] = (digest, TENDL_BASE + filename.removesuffix(".dat") + ".zip",
                             PROVIDERS["tendl"], TENDL_BYTES[filename])

    catalog = _read_json(CATALOG_PATH, 1024 * 1024)
    artifacts = {artifact["id"]: artifact for artifact in catalog.get("artifacts", [])}
    for logical, artifact_id in (
        ("decay/endf-b-viii-0_decay.dat", "endfb-viii-0-decay"),
        ("decay/jeff-3-3_decay.dat", "jeff-3-3-decay"),
    ):
        artifact = artifacts.get(artifact_id)
        if not isinstance(artifact, dict):
            raise SeedError(f"catalog is missing pinned decay artifact {artifact_id}")
        source = artifact.get("source")
        if not isinstance(source, dict) or source.get("archive_member") != PurePosixPath(logical).name:
            raise SeedError(f"catalog decay source/member changed: {artifact_id}")
        if logical == "decay/endf-b-viii-0_decay.dat" and ci_hashes.get(logical) != artifact["sha256"]:
            raise SeedError("ENDF decay identity differs between the CI checksum and native catalog")
        provider = PROVIDERS["endf"] if logical.endswith("endf-b-viii-0_decay.dat") else PROVIDERS["jeff"]
        expected[logical] = (artifact["sha256"], source["url"], provider, artifact["bytes"])

    fns = _read_json(FNS_CASE_PATH, 128 * 1024)
    expected[FNS_PATH] = (fns["archive_sha256"], fns["archive_url"], PROVIDERS["fns"],
                          14_839_792)
    return expected


def validate_authorities(manifest):
    """Bind all seed entries to existing tracked data identities and source URLs."""
    if not isinstance(manifest, dict) or set(manifest) != {
        "schema", "release", "total_bytes", "files"
    }:
        raise SeedError("seed manifest has unexpected or missing top-level fields")
    if manifest["schema"] != "actinv-ci-data-seed-1" or manifest["release"] != RELEASE:
        raise SeedError("seed schema or release identity is unsupported")
    if type(manifest["total_bytes"]) is not int or manifest["total_bytes"] != TOTAL_BYTES:
        raise SeedError("seed manifest total byte count differs from the frozen identity")
    if not isinstance(manifest["files"], list) or len(manifest["files"]) != 13:
        raise SeedError("seed must contain exactly thirteen assets")

    authorities = _expected_authorities()
    if set(authorities) != {
        *(f"tendl/{name}" for name in _tendl_names()),
        "decay/endf-b-viii-0_decay.dat", "decay/jeff-3-3_decay.dat", FNS_PATH,
    }:
        raise SeedError("tracked data authorities do not match the frozen 13-file population")

    paths = set()
    total = 0
    for entry in manifest["files"]:
        if not isinstance(entry, dict) or set(entry) != {
            "path", "bytes", "sha256", "url", "provider", "source_url"
        }:
            raise SeedError("seed file entry has unexpected or missing fields")
        logical = entry["path"]
        _safe_relative(logical)
        if logical in paths:
            raise SeedError(f"duplicate seed path: {logical}")
        paths.add(logical)
        if type(entry["bytes"]) is not int or entry["bytes"] <= 0:
            raise SeedError(f"invalid byte count for {logical}")
        if (not isinstance(entry["sha256"], str) or len(entry["sha256"]) != 64
                or any(ch not in "0123456789abcdef" for ch in entry["sha256"])):
            raise SeedError(f"invalid SHA-256 for {logical}")
        authority = authorities.get(logical)
        if (authority is None or entry["sha256"] != authority[0]
                or entry["source_url"] != authority[1] or entry["provider"] != authority[2]
                or entry["bytes"] != authority[3]):
            raise SeedError(f"source identity for {logical} disagrees with tracked authority")
        basename = PurePosixPath(logical).name
        if entry["url"] != RELEASE_BASE + basename:
            raise SeedError(f"release URL for {logical} is not the fixed versioned asset")
        total += entry["bytes"]
    if paths != set(authorities) or total != TOTAL_BYTES:
        raise SeedError("seed asset population or total byte count changed")
    return True


def _tendl_names():
    return [line.strip() for line in (ROOT / "scripts/ci_tendl_files.txt").read_text().splitlines()
            if line.strip()]


def _safe_relative(value):
    if not isinstance(value, str) or "\\" in value:
        raise SeedError(f"unsafe relative path: {value!r}")
    path = PurePosixPath(value)
    if (path.is_absolute() or not path.parts or path.as_posix() != value
            or any(part in ("", ".", "..") for part in path.parts)):
        raise SeedError(f"unsafe relative path: {value!r}")
    return path


def _real_directory(path: Path, *, create: bool):
    if path.exists() or path.is_symlink():
        if path.is_symlink() or not path.is_dir():
            raise SeedError(f"destination component is not a real directory: {path}")
    elif create:
        path.mkdir()
    else:
        raise SeedError(f"directory does not exist: {path}")


def _directory_tree(path: Path, *, create: bool):
    if ".." in path.parts:
        raise SeedError(f"destination path contains parent traversal: {path}")
    absolute = path.absolute()
    cursor = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        cursor = cursor / part
        _real_directory(cursor, create=create)
    return absolute


def _ensure_parent(root: Path, relative: PurePosixPath):
    root = _directory_tree(root, create=False)
    parent = root
    for part in relative.parts[:-1]:
        parent = parent / part
        _real_directory(parent, create=True)
    return parent / relative.parts[-1]


def _verify_file(path: Path, entry):
    _directory_tree(path.parent, create=False)
    if path.is_symlink() or not path.is_file():
        raise SeedError(f"existing seed destination is not a regular file: {path}")
    if path.lstat().st_size != entry["bytes"]:
        raise SeedError(f"existing seed destination has the wrong size and was left unchanged: {path}")
    size, digest = _sha256_file(path, entry["bytes"])
    if size != entry["bytes"] or digest != entry["sha256"]:
        raise SeedError(f"existing seed destination is corrupt and was left unchanged: {path}")


def _source_stream(entry, offline_root):
    if offline_root is not None:
        relative = _safe_relative(entry["path"])
        source = _directory_tree(Path(offline_root), create=False)
        for part in relative.parts:
            source = source / part
            if source.is_symlink():
                raise SeedError(f"offline seed path contains a symlink: {source}")
        if not source.is_file():
            raise SeedError(f"offline seed asset is missing: {source}")
        if source.lstat().st_size != entry["bytes"]:
            raise SeedError(f"offline seed asset has the wrong byte count: {source}")
        return source.open("rb")
    request = Request(entry["url"], headers={
        "User-Agent": "ACTINV-CI-Seed/1 (+https://github.com/AvilaLabs/ACTINV)",
        "Accept-Encoding": "identity",
    })
    return _open_release(request)


class _HttpsReleaseRedirect(HTTPRedirectHandler):
    _hosts = {
        "github.com", "release-assets.githubusercontent.com",
        "objects.githubusercontent.com", "github-releases.githubusercontent.com",
    }

    def redirect_request(self, request, response, code, message, headers, new_url):
        parsed = urlsplit(new_url)
        if (parsed.scheme != "https" or parsed.hostname not in self._hosts
                or parsed.username is not None or parsed.password is not None
                or parsed.port not in (None, 443)):
            raise SeedError("release asset redirect left the pinned HTTPS hosts")
        return super().redirect_request(request, response, code, message, headers, new_url)


def _open_release(request):
    return build_opener(_HttpsReleaseRedirect()).open(request, timeout=SOCKET_TIMEOUT_S)


def _validate_offline_tree(root, manifest):
    root = _directory_tree(Path(root), create=False)
    expected = {entry["path"] for entry in manifest["files"]}
    expected_directories = {
        "/".join(PurePosixPath(path).parts[:index])
        for path in expected
        for index in range(1, len(PurePosixPath(path).parts))
    }
    actual = set()
    for directory, child_directories, filenames in os.walk(root, followlinks=False):
        directory_path = Path(directory)
        for child in child_directories:
            child_path = directory_path / child
            if child_path.is_symlink() or not child_path.is_dir():
                raise SeedError(f"offline seed contains an unsafe directory: {child_path}")
            if child_path.relative_to(root).as_posix() not in expected_directories:
                raise SeedError(f"offline seed contains an unexpected directory: {child_path}")
        for filename in filenames:
            path = directory_path / filename
            if path.is_symlink() or not path.is_file():
                raise SeedError(f"offline seed contains a non-regular file: {path}")
            relative = path.relative_to(root).as_posix()
            if relative not in expected:
                raise SeedError(f"offline seed contains an unexpected file: {relative}")
            actual.add(relative)
    if actual != expected:
        missing = sorted(expected - actual)
        raise SeedError(f"offline seed is incomplete: {missing}")


def _install_file(entry, destination, offline_root):
    if destination.exists() or destination.is_symlink():
        _verify_file(destination, entry)
        return "reused"
    _directory_tree(destination.parent, create=True)
    # Re-check all parent components after creation; a symlink must never redirect publication.
    cursor = destination.parent
    while cursor != cursor.parent:
        if cursor.is_symlink() or not cursor.is_dir():
            raise SeedError(f"unsafe destination parent: {cursor}")
        cursor = cursor.parent

    temporary_name = None
    digest = hashlib.sha256()
    count = 0
    deadline = time.monotonic() + TOTAL_DEADLINE_S
    try:
        with _source_stream(entry, offline_root) as source:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{destination.name}.", suffix=".part", dir=destination.parent
            )
            with os.fdopen(descriptor, "wb") as output:
                while True:
                    if time.monotonic() > deadline:
                        raise SeedError(f"transfer exceeded {TOTAL_DEADLINE_S}-second deadline")
                    read = getattr(source, "read1", source.read)
                    block = read(CHUNK_BYTES)
                    if time.monotonic() > deadline:
                        raise SeedError(f"transfer exceeded {TOTAL_DEADLINE_S}-second deadline")
                    if not block:
                        break
                    count += len(block)
                    if count > entry["bytes"]:
                        raise SeedError(f"asset exceeds declared size: {entry['path']}")
                    digest.update(block)
                    output.write(block)
                if count != entry["bytes"] or digest.hexdigest() != entry["sha256"]:
                    raise SeedError(f"truncated or changed asset: {entry['path']}")
                output.flush()
                os.fsync(output.fileno())
        # Refuse a concurrent destination rather than replace it without verification.
        if destination.exists() or destination.is_symlink():
            _verify_file(destination, entry)
            return "reused"
        os.link(temporary_name, destination)
        return "installed"
    except SeedError:
        raise
    except Exception as error:
        raise SeedError(f"asset transfer failed for {entry['path']}: {error}") from error
    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass


def install(mode, destination, fns_archive=None, offline_root=None):
    manifest = load_manifest()
    validate_authorities(manifest)
    if offline_root is not None:
        _validate_offline_tree(offline_root, manifest)
    if mode not in ("controls", "fns", "all"):
        raise SeedError(f"unknown installation mode: {mode}")
    if mode == "fns" and fns_archive is None:
        raise SeedError("fns mode requires --fns-archive")
    if mode != "fns" and fns_archive is not None:
        raise SeedError("--fns-archive is only valid with fns mode")

    destination = _directory_tree(Path(destination), create=True)
    entries = manifest["files"]
    if mode == "controls":
        selected = [entry for entry in entries if entry["path"].startswith(("tendl/", "decay/"))
                    and entry["path"] != "decay/jeff-3-3_decay.dat"]
        placements = [(entry, _safe_relative(entry["path"])) for entry in selected]
    elif mode == "fns":
        selected = [entry for entry in entries if entry["path"].startswith("decay/")
                    or entry["path"] == FNS_PATH]
        placements = []
        for entry in selected:
            if entry["path"] == FNS_PATH:
                archive = Path(fns_archive)
                _directory_tree(archive.parent, create=True)
                # The archive is published through the same verified atomic writer.
                placements.append((entry, None, archive))
            else:
                placements.append((entry, PurePosixPath("v1.1.0") / _safe_relative(entry["path"]), None))
    else:
        selected = entries
        placements = [(entry, _safe_relative(entry["path"])) for entry in selected]

    statuses = []
    for item in placements:
        if mode == "fns":
            entry, relative, external_destination = item
            target = external_destination if external_destination is not None else _ensure_parent(destination, relative)
        else:
            entry, relative = item
            target = _ensure_parent(destination, relative)
        statuses.append(_install_file(entry, target, offline_root))
    return {"schema": "actinv-ci-data-seed-install-1", "mode": mode,
            "release": manifest["release"], "files": len(statuses),
            "installed": statuses.count("installed"), "reused": statuses.count("reused")}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("controls", "fns", "all"), required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--fns-archive", type=Path)
    parser.add_argument("--offline-root", type=Path)
    args = parser.parse_args(argv)
    report = install(args.mode, args.destination, args.fns_archive, args.offline_root)
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
