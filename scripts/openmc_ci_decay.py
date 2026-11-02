"""Install the default bundle's decay files from ACTINV's CI data cache, for the openmc workflow.

The catalog's decay sources are on the IAEA NDS site, which answers automated downloads with a
browser challenge (HTTP 403), so CI takes the same files from the CI-cache release that
``fetch_ci_seed.py`` uses. Each file must match the installed catalog's size and SHA-256;
``actinv data fetch`` then finds them valid and downloads only the rest.

    python scripts/openmc_ci_decay.py DATA_DIR
"""
import hashlib
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

CACHE = "https://github.com/AvilaLabs/ACTINV/releases/download/ci-data-cache-2026-10-04-v1/"
ROLES = ("decay-primary", "decay-fallback")


def main(argv):
    root = Path(argv[1])
    catalog = json.loads(subprocess.run(["actinv", "data", "manifest"], check=True, capture_output=True,
                                        text=True).stdout)
    bundle = next(b for b in catalog["bundles"] if b["id"] == catalog["default_bundle"])
    artifacts = {a["id"]: a for a in catalog["artifacts"]}
    for artifact in (artifacts[i] for i in bundle["artifacts"]):
        if artifact["role"] not in ROLES:
            continue
        target = root / f"v{catalog['catalog_version']}" / artifact["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(CACHE + target.name, timeout=60) as response:
            payload = response.read()
        digest = hashlib.sha256(payload).hexdigest()
        if len(payload) != artifact["bytes"] or digest != artifact["sha256"]:
            raise SystemExit(f"{target.name}: {len(payload)} bytes, sha256 {digest}; the catalog has "
                             f"{artifact['bytes']} bytes, {artifact['sha256']}")
        target.write_bytes(payload)
        print(f"{artifact['id']}: {target}")


if __name__ == "__main__":
    main(sys.argv)
