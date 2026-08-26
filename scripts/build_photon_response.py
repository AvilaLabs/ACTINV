#!/usr/bin/env python3
"""Build an external, hash-pinnable ACTINV photon-response JSON from NIST tables.

The generated file contains dry-air mass energy-absorption coefficients and elemental
mass attenuation coefficients. It is deterministic for identical downloaded pages:
timestamps and machine-specific paths are deliberately excluded from the payload.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import urllib.request
from pathlib import Path

SCHEMA = "actinv-photon-response-1"
BASE = "https://physics.nist.gov/PhysRefData/XrayMassCoef"
AIR_URL = f"{BASE}/ComTab/air.html"
SYMBOLS = (
    "H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni "
    "Cu Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe "
    "Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg "
    "Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U"
).split()
FLOAT = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)[Ee][+-]?\d+")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch(url: str, cache: Path | None, offline: bool) -> bytes:
    cached = cache / url.rsplit("/", 1)[-1] if cache else None
    if cached and cached.exists():
        return cached.read_bytes()
    if offline:
        raise RuntimeError(f"offline cache miss for {url}")
    request = urllib.request.Request(url, headers={"User-Agent": "ACTINV photon-response builder/1"})
    with urllib.request.urlopen(request, timeout=60) as response:
        data = response.read()
    if cached:
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(data)
    return data


def table_rows(raw: bytes) -> tuple[list[float], list[float], list[float]]:
    text = raw.decode("ascii", errors="replace")
    blocks = re.findall(r"<pre[^>]*>(.*?)</pre>", text, flags=re.IGNORECASE | re.DOTALL)
    # NIST supplies an ASCII PRE table after its HTML table. Using one block prevents
    # the two renderings from being parsed twice.
    candidate = max(blocks, key=len) if blocks else text
    candidate = html.unescape(re.sub(r"<[^>]+>", " ", candidate))
    rows: list[tuple[float, float, float]] = []
    for line in candidate.splitlines():
        fields = FLOAT.findall(line)
        if len(fields) < 3:
            continue
        energy, mu, mu_en = map(float, fields[-3:])
        if energy > 0.0 and mu > 0.0 and mu_en > 0.0:
            rows.append((energy * 1.0e6, mu, mu_en))
    if len(rows) < 20:
        raise RuntimeError(f"NIST table parse found only {len(rows)} numeric rows")
    if any(b[0] < a[0] for a, b in zip(rows, rows[1:])):
        raise RuntimeError("NIST table energies are not nondecreasing")
    return ([row[0] for row in rows], [row[1] for row in rows], [row[2] for row in rows])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="external response JSON to create")
    parser.add_argument(
        "--elements",
        default="Fe",
        help="comma-separated element symbols, or 'all' for NIST Z=1..92 (default: Fe)",
    )
    parser.add_argument("--cache-dir", type=Path, help="optional raw-page cache")
    parser.add_argument("--offline", action="store_true", help="read only from --cache-dir")
    args = parser.parse_args()

    requested = SYMBOLS if args.elements.lower() == "all" else [
        value.strip().capitalize() for value in args.elements.split(",") if value.strip()
    ]
    unknown = sorted(set(requested) - set(SYMBOLS))
    if unknown:
        parser.error(f"NIST elemental tables cover H through U; unknown: {', '.join(unknown)}")
    requested = sorted(set(requested), key=SYMBOLS.index)

    air_raw = fetch(AIR_URL, args.cache_dir, args.offline)
    air_e, _, air_mu_en = table_rows(air_raw)
    provenance: dict[str, object] = {
        "builder": "scripts/build_photon_response.py",
        "source": "NIST X-Ray Mass Attenuation Coefficients, tables 3 and 4",
        "units": {"energy": "eV", "coefficient": "cm2/g"},
        "air_url": AIR_URL,
        "air_page_sha256": sha256(air_raw),
        "element_pages": {},
    }
    element_curves: dict[str, object] = {}
    for symbol in requested:
        z = SYMBOLS.index(symbol) + 1
        url = f"{BASE}/ElemTab/z{z:02d}.html"
        raw = fetch(url, args.cache_dir, args.offline)
        energy, mu, _ = table_rows(raw)
        element_curves[symbol] = {"energy_eV": energy, "values_cm2_g": mu}
        provenance["element_pages"][symbol] = {"url": url, "sha256": sha256(raw)}

    payload = {
        "schema": SCHEMA,
        "provenance": provenance,
        "air_mass_energy_absorption": {"energy_eV": air_e, "values_cm2_g": air_mu_en},
        "element_mass_attenuation": element_curves,
    }
    encoded = (json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(encoded)
    print(f"{args.output}: {len(requested)} element(s), sha256={sha256(encoded)}")


if __name__ == "__main__":
    main()
