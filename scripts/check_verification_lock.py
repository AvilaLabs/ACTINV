#!/usr/bin/env python3
"""Require the linked production dependency graph to match ACTINV's lockfile."""
from pathlib import Path
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def production_graph(lock: dict) -> set[tuple[str, str, str, str]]:
    packages = lock["package"]
    by_name: dict[str,list[dict]] = {}
    for package in packages:
        by_name.setdefault(package["name"],[]).append(package)
    seen = set()
    pending = [by_name[name][0] for name in ("actinv-core","actinv-data")]
    while pending:
        package = pending.pop()
        key = (package["name"],package["version"],package.get("source",""),package.get("checksum",""))
        if key in seen:
            continue
        seen.add(key)
        for dependency in package.get("dependencies",[]):
            name,*rest = dependency.split()
            candidates = by_name[name]
            if rest:
                candidates = [p for p in candidates if p["version"] == rest[0]]
            if len(candidates) != 1:
                raise ValueError(f"ambiguous dependency {dependency}")
            pending.append(candidates[0])
    return seen


def check() -> None:
    application = production_graph(tomllib.loads((ROOT / "Cargo.lock").read_text()))
    verification = production_graph(tomllib.loads((ROOT / "verification/Cargo.lock").read_text()))
    # The application workspace enables additional optional dependencies through
    # the desktop. Every production dependency reached here must match the app;
    # desktop-only feature dependencies need not be enabled by these harnesses.
    if verification - application:
        extra = sorted((n,v) for n,v,_,_ in verification-application)
        raise ValueError(f"verification production dependency versions differ: {extra}")


if __name__ == "__main__":
    check()
    print("verification production dependency graph matches Cargo.lock")
