#!/usr/bin/env python3
"""Remove only six archived initial gate outputs after complete byte proof."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "controls")]
import run_p117_gate

ARCHIVE = ROOT / "results/failures/p118_initial"
DISCOVERY_SHA256 = "21a94cd256bbbb858d9cfc9ebb342e15f3ada0b6d8e73ea39bf9112ac837f080"

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def safe_file(base, relative):
    rel = PurePosixPath(relative)
    assert relative and not rel.is_absolute() and ".." not in rel.parts
    assert "\\" not in relative and rel.as_posix() == relative
    cursor = base
    assert not cursor.is_symlink()
    for part in rel.parts:
        cursor = cursor / part
        assert not cursor.is_symlink(), relative
    assert cursor.is_file(), relative
    cursor.resolve(strict=True).relative_to(base.resolve(strict=True))
    return cursor

def main():
    resources, errors = run_p117_gate.inspect_resources()
    errors += run_p117_gate._resource_snapshot_errors(resources)
    print(json.dumps({"resources": resources, "errors": errors}, sort_keys=True), flush=True)
    assert not errors, errors
    raw = safe_file(ROOT, "results/failures/p118_initial/discovery.json").read_bytes()
    assert sha(raw) == DISCOVERY_SHA256
    discovery = json.loads(raw)
    assert discovery["schema"] == "actinv-p118-initial-preservation-1"
    files = discovery["preserved_files_sha256"]
    assert isinstance(files, dict) and len(files) == 15
    physical = set()
    for path in ARCHIVE.rglob("*"):
        assert not path.is_symlink(), path
        if path.is_file():
            physical.add(path.relative_to(ARCHIVE).as_posix())
        else:
            assert path.is_dir(), path
    assert physical == set(files) | {"discovery.json"}
    for relative, digest in files.items():
        assert sha(safe_file(ARCHIVE, relative).read_bytes()) == digest, relative
    paths = []
    for gate in ("rust_fmt", "recorder_regressions"):
        paths.extend([f"results/quality/p118/{gate}.json",
                      f"results/quality/p118/{gate}.log", f"target/p118-{gate}.log"])
    for relative in paths:
        assert sha(safe_file(ROOT, relative).read_bytes()) == files[relative], relative
    # Validate all six paths before unlinking any, and retain all archive bytes.
    for relative in paths:
        safe_file(ROOT, relative).unlink()
    print(json.dumps({"removed_exact_generated_paths": paths,
                      "archive_discovery_sha256": DISCOVERY_SHA256,
                      "retained_archive_file_count": 16}, sort_keys=True))

if __name__ == "__main__":
    main()
