#!/usr/bin/env python3
"""Check or refresh the tracked-file manifest; --index hashes exactly what will be committed."""
import argparse
import hashlib
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {"MANIFEST.sha256", "results/g6_p12_complete.json", "results/verdict_p12.json"}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="Refresh instead of check")
    parser.add_argument("--index", action="store_true", help="Hash staged blobs, not working files")
    args = parser.parse_args()
    paths = sorted(p for p in subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0") if p and p not in EXCLUDED)
    entries = []
    for path in paths:
        content = subprocess.check_output(["git", "show", f":{path}"], cwd=ROOT) if args.index else (ROOT / path).read_bytes()
        entries.append(f"{hashlib.sha256(content).hexdigest()}  ./{path}\n")
    expected = "".join(entries)
    manifest = ROOT / "MANIFEST.sha256"
    if args.write:
        manifest.write_text(expected, encoding="utf-8")
        print(f"Refreshed {len(entries)} entries. Stage MANIFEST.sha256 before committing.")
    elif manifest.read_text(encoding="utf-8") != expected:
        raise SystemExit("Tracked-file manifest is stale. Stage intended changes, run python scripts/refresh_manifest.py --write --index, then stage MANIFEST.sha256.")
    else:
        print(f"Manifest matches all {len(entries)} tracked files.")

if __name__ == "__main__":
    main()
