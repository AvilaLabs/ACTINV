#!/usr/bin/env python3
"""Stage only the immutable files named by ACTINV's v1.0.0 data catalog."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "crates/actinv-cli/data/actinv-data-catalog-v1.0.0.json"
NOTICE_PATH = ROOT / "crates/actinv-cli/data/ACTINV-DATA-NOTICE-v1.0.0.md"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify(path: Path, expected_bytes: int, expected_sha256: str) -> None:
    if not path.is_file():
        raise ValueError(f"missing release input: {path}")
    actual_bytes = path.stat().st_size
    if actual_bytes != expected_bytes:
        raise ValueError(f"{path} has {actual_bytes} bytes; expected {expected_bytes}")
    actual_sha256 = sha256(path)
    if actual_sha256 != expected_sha256:
        raise ValueError(f"{path} has SHA-256 {actual_sha256}; expected {expected_sha256}")


def local_source(artifact, p10_dir: Path, covariance: Path, covariance_index: Path) -> Path:
    identifier = artifact["id"]
    mapping = {
        "tendl-2025-neutron-709g": p10_dir / "neutron.n.p10.npz",
        "tendl-2025-neutron-709g-index": p10_dir / "neutron.n.p10_index.json",
        "tendl-2025-proton-162g": p10_dir / "proton.n.p10.npz",
        "tendl-2025-proton-162g-index": p10_dir / "proton.n.p10_index.json",
        "tendl-2025-deuteron-162g": p10_dir / "deuteron.n.p10.npz",
        "tendl-2025-deuteron-162g-index": p10_dir / "deuteron.n.p10_index.json",
        "tendl-2025-alpha-162g": p10_dir / "alpha.n.p10.npz",
        "tendl-2025-alpha-162g-index": p10_dir / "alpha.n.p10_index.json",
        "tendl-2025-neutron-709g-covariance": covariance,
        "tendl-2025-neutron-709g-covariance-index": covariance_index,
        "actinv-data-notice-v1": NOTICE_PATH,
    }
    try:
        return mapping[identifier]
    except KeyError as error:
        raise ValueError(f"refusing to stage unrecognized release artifact {identifier!r}") from error


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify and stage ACTINV data-v1.0.0 release assets without raw nuclear-data inputs."
    )
    parser.add_argument("p10_directory", type=Path, help="directory containing the exact P10 full-build files")
    parser.add_argument("covariance", type=Path, help="exact P11 complete covariance NPZ")
    parser.add_argument("covariance_index", type=Path, help="matching P11 covariance index JSON")
    parser.add_argument("output", type=Path, help="new or empty staging directory")
    args = parser.parse_args()

    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    if catalog.get("schema") != "actinv-data-catalog-1" or catalog.get("catalog_version") != "1.0.0":
        raise ValueError("refusing to stage an unexpected catalog schema or version")
    if ".." in args.output.parts:
        raise ValueError("staging destination must not contain parent traversal")
    for component in reversed((args.output, *args.output.parents)):
        if component.is_symlink() or (component.exists() and not component.is_dir()):
            raise ValueError(f"staging destination component is not a real directory: {component}")
    if args.output.exists() and any(args.output.iterdir()):
        raise ValueError(f"staging directory is not empty: {args.output}")
    args.output.mkdir(parents=True, exist_ok=True)
    if args.output.is_symlink() or not args.output.is_dir():
        raise ValueError(f"staging destination is not a real directory: {args.output}")

    staged = []
    for artifact in catalog["artifacts"]:
        source_url = urlsplit(artifact["source"]["url"])
        if source_url.hostname != "github.com":
            continue
        asset_name = Path(source_url.path).name
        if not asset_name or "/releases/download/data-v1.0.0/" not in source_url.path:
            raise ValueError(f"artifact {artifact['id']!r} has an unexpected release URL")
        source = local_source(artifact, args.p10_directory, args.covariance, args.covariance_index)
        verify(source, artifact["bytes"], artifact["sha256"])
        destination = args.output / asset_name
        shutil.copyfile(source, destination)
        verify(destination, artifact["bytes"], artifact["sha256"])
        staged.append((asset_name, artifact["bytes"], artifact["sha256"]))

    catalog_asset = args.output / CATALOG_PATH.name
    shutil.copyfile(CATALOG_PATH, catalog_asset)
    staged.append((catalog_asset.name, catalog_asset.stat().st_size, sha256(catalog_asset)))
    staged.sort()
    (args.output / "SHA256SUMS").write_text(
        "".join(f"{digest}  {name}\n" for name, _, digest in staged), encoding="utf-8"
    )
    (args.output / "SIZES").write_text(
        "".join(f"{size}  {name}\n" for name, size, _ in staged), encoding="utf-8"
    )
    print(json.dumps({
        "schema": "actinv-data-release-stage-1",
        "catalog_version": catalog["catalog_version"],
        "output": str(args.output),
        "assets": [
            {"name": name, "bytes": size, "sha256": digest}
            for name, size, digest in staged
        ],
        "pass": True,
    }, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
