#!/usr/bin/env python3
"""Every file hash an example declares must match the file it names.

`actinv validate --hashes` needs the nuclear data, which CI does not install, so this checks what can be checked
without it: a declared `{path, sha256}` naming a tracked repository file is compared with that file's SHA-256, and one
naming `actinv-data/vX.Y.Z/...` or `catalog:ID` with the bundled data catalog. Other references (raw evaluations
outside the repository) are listed as not checkable. Found by the P129 gate run: a regenerated artifact had silently
invalidated `examples/shielding_demo.json`.

Run by `controls/check_public_examples.py` (the CI "Public examples are portable" step). Usage:
check_example_hashes.py
"""
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOG = re.compile(r"^actinv-data/v(\d+\.\d+\.\d+)/(.+)$")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def references(node, where=""):
    if isinstance(node, dict):
        if isinstance(node.get("sha256"), str) and isinstance(node.get("path"), str):
            yield where or "/", node["path"], node["sha256"]
        for key, value in node.items():
            yield from references(value, f"{where}/{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from references(value, f"{where}/{index}")


def catalog_hashes(version):
    path = ROOT / "crates" / "actinv-cli" / "data" / f"actinv-data-catalog-v{version}.json"
    if not path.is_file():
        return None
    return {artifact["path"]: artifact["sha256"] for artifact in json.loads(path.read_text())["artifacts"]}


def catalog_ids():
    """`catalog:ID` resolves through the newest bundled catalog."""
    catalogs = sorted((ROOT / "crates" / "actinv-cli" / "data").glob("actinv-data-catalog-v*.json"),
                      key=lambda p: tuple(int(x) for x in p.stem.rsplit("-v", 1)[1].split(".")))
    if not catalogs:
        return {}
    return {artifact["id"]: artifact["sha256"] for artifact in json.loads(catalogs[-1].read_text())["artifacts"]}


def survey():
    """(checked count, references not checkable here, mismatch messages)."""
    listed = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True, timeout=60)
    tracked = set(listed.stdout.decode().split("\0"))
    checked, unchecked, bad = 0, [], []
    for example in sorted(p for p in (ROOT / "examples").rglob("*.json")
                          if p.relative_to(ROOT).as_posix() in tracked):
        name = example.relative_to(ROOT).as_posix()
        for where, path, declared in references(json.loads(example.read_text())):
            match = CATALOG.match(path)
            if path in tracked:
                actual = sha256(ROOT / path)
            elif path.startswith("catalog:"):
                actual = catalog_ids().get(path[len("catalog:"):])
                if actual is None:
                    bad.append(f"{name} {where}: {path} is not in the bundled data catalog")
                    continue
            elif match and catalog_hashes(match.group(1)) is not None:
                actual = catalog_hashes(match.group(1)).get(match.group(2))
                if actual is None:
                    bad.append(f"{name} {where}: {path} is not in the v{match.group(1)} data catalog")
                    continue
            else:
                unchecked.append(f"{name} {where}: {path}")
                continue
            checked += 1
            if actual != declared:
                bad.append(f"{name} {where}: {path} declares {declared}, file is {actual}")
    return checked, unchecked, bad


def main():
    checked, unchecked, bad = survey()
    for line in unchecked:
        print(f"not checkable without external data: {line}")
    for line in bad:
        print(f"MISMATCH {line}")
    print(f"example hashes: {checked} checked, {len(unchecked)} external, {len(bad)} mismatched")
    return 1 if bad or checked == 0 else 0


if __name__ == "__main__":
    sys.exit(main())
