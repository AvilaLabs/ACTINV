"""Coordinator-only exact input staging; payloads remain in ignored target."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import sys
import tempfile

ROOT = Path.cwd()
sys.path.insert(0, str(ROOT / "scripts"))
import fetch_ci_seed

manifest = fetch_ci_seed.load_manifest()
fetch_ci_seed.validate_authorities(manifest)
destination = ROOT / "target/p117-ci-seed"
if destination.exists() or destination.is_symlink():
    raise ValueError("refusing to replace an existing seed staging directory")
destination.mkdir()
evidence = []
for entry in manifest["files"]:
    rel = Path(entry["path"])
    if rel.parts[0] == "tendl" or rel.name == "endf-b-viii-0_decay.dat":
        source = Path("/home/connoravila/actinv-ci-data") / rel
    elif rel.name == "jeff-3-3_decay.dat":
        source = Path("/home/connoravila/Documents/actinv/actinv-data/v1.1.0") / rel
    elif rel.as_posix() == "fns/fns.zip":
        source = Path("/home/connoravila/nuclear-data/conderc-fns/fns.zip")
    else:
        raise ValueError("unexpected seed entry")
    if source.is_symlink() or not source.is_file():
        raise ValueError(f"seed source is not a regular file: {source}")
    final = destination / rel
    final.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_temp = tempfile.mkstemp(prefix=".staging-", dir=final.parent)
    temporary = Path(raw_temp)
    try:
        h = hashlib.sha256()
        count = 0
        with source.open("rb") as incoming, os.fdopen(descriptor, "wb") as outgoing:
            while chunk := incoming.read(1 << 20):
                count += len(chunk)
                if count > entry["bytes"]:
                    raise ValueError("oversized source")
                h.update(chunk)
                outgoing.write(chunk)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        if count != entry["bytes"] or h.hexdigest() != entry["sha256"]:
            raise ValueError(f"source identity mismatch: {source}")
        os.link(temporary, final)
        evidence.append({"path": rel.as_posix(), "bytes": count, "sha256": h.hexdigest()})
    finally:
        temporary.unlink(missing_ok=True)
assert len(evidence) == 13 and sum(row["bytes"] for row in evidence) == 166789318
release_metadata = ROOT / "target/p117-ci-release"
release_metadata.mkdir()
shutil.copyfile(ROOT / "docs/maintainers/CI_DATA_CACHE_NOTICE.md", release_metadata / "NOTICE.md")
shutil.copyfile(ROOT / "scripts/ci_data_seed.json", release_metadata / "ci_data_seed.json")
print(json.dumps({"files": evidence, "total_bytes": 166789318}, sort_keys=True, indent=2))
