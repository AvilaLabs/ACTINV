#!/usr/bin/env python3
"""Stage ENDF/B-VIII.1 ENDF-6 neutron tapes for the identical-data FNS arm.

Fetches the IAEA mirror per-isotope zips for exactly the nuclides the OpenMC
arm could transmute (chain-reachable AND present in the ENDF/B-VIII.1 HDF5
XS library), extracts each tape as <stage>/<Nuclide>.endf, and writes a
manifest. Deterministic ordering, resumable.

Usage: python3 controls/endf8_fns_stage.py [--stage DIR] [--list-only]
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX_URL = ("https://nds.iaea.org/public/download-endf/"
             "ENDF-B-VIII.1/n-index.htm")
BASE = "https://nds.iaea.org/public/download-endf/ENDF-B-VIII.1/"
XS_XML = Path("/home/connoravila/nuclear-data/endfb-viii.1-hdf5/"
              "cross_sections.xml")
DRIVER_RESULTS = ROOT / "scratch" / "openmc-fns-h2h" / "driver" / \
    "openmc_results.json"
STAGE_DEFAULT = Path.home() / "nuclear-data" / "endfb-viii.1-fns-arm" / \
    "stage"

EL = ("H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe "
      "Co Ni Cu Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In "
      "Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf "
      "Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U").split()
ELZ = {s: z + 1 for z, s in enumerate(EL)}


def nuc_to_key(nuc: str) -> tuple[int, str, int, str] | None:
    """OpenMC name -> (Z, El, A, meta) e.g. Nb90_m1 -> (41,'Nb',90,'m1')."""
    m = re.match(r"^([A-Z][a-z]?)(\d+)(?:_m(\d))?$", nuc)
    if not m:
        return None
    sym, a, meta = m.group(1), int(m.group(2)), m.group(3) or ""
    if sym not in ELZ:
        return None
    return ELZ[sym], sym, a, f"m{meta}" if meta else ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", default=str(STAGE_DEFAULT))
    ap.add_argument("--list-only", action="store_true")
    args = ap.parse_args()
    stage = Path(args.stage)

    res = json.loads(DRIVER_RESULTS.read_text())
    reachable = set()
    for c in res["cases"].values():
        reachable |= set(c.get("atoms_atom_per_cm3") or {})
    import xml.etree.ElementTree as ET
    xs = ET.parse(XS_XML).getroot()
    available = {l.get("materials") for l in xs.iter("library")
                 if l.get("type") == "neutron"}
    needed = sorted(reachable & available)

    ua = {"User-Agent": "curl/8"}
    idx_html = urllib.request.urlopen(
        urllib.request.Request(INDEX_URL, headers=ua),
        timeout=60).read().decode()
    # n_026-Fe-56_2631.zip ; metastable targets carry 'M' (= OpenMC _m1)
    pat = re.compile(
        r'href="n/(n_(\d+)-([A-Za-z]+)-(\d+)([Mm]\d*)?_\d+\.zip)"')
    tape_of = {}
    for href, z, sym, a, meta in pat.findall(idx_html):
        norm = "m1" if meta in ("m", "M") else (meta or "")
        tape_of[(int(z), sym, int(a), norm)] = href

    plan, unstageable = [], []
    for nuc in needed:
        key = nuc_to_key(nuc)
        href = tape_of.get(key) if key else None
        if href:
            plan.append((nuc, href))
        else:
            unstageable.append(nuc)
    print(f"reachable={len(reachable)} xs_available={len(available)} "
          f"needed={len(needed)} staged={len(plan)} "
          f"unstageable={len(unstageable)}", file=sys.stderr)
    if unstageable:
        print("unstageable: " + " ".join(unstageable), file=sys.stderr)
    if args.list_only:
        for nuc, href in plan:
            print(f"{nuc} {href}")
        return 0

    stage.mkdir(parents=True, exist_ok=True)
    fetched = failed = 0
    for i, (nuc, href) in enumerate(plan, 1):
        out = stage / f"{nuc}.endf"
        if out.exists() and out.stat().st_size > 0:
            fetched += 1
            continue
        try:
            blob = urllib.request.urlopen(
                urllib.request.Request(BASE + "n/" + href, headers=ua),
                timeout=120).read()
            inner = zipfile.ZipFile(io.BytesIO(blob)).namelist()
            zf = zipfile.ZipFile(io.BytesIO(blob))
            data = zf.read(inner[0])
            out.write_bytes(data)
            fetched += 1
        except Exception as e:
            failed += 1
            print(f"  FAIL {nuc} {href}: {e}", file=sys.stderr)
        if i % 25 == 0:
            print(f"  {i}/{len(plan)} staged", file=sys.stderr)
    manifest = {"index_url": INDEX_URL, "tapes": len(plan),
                "staged": len(list(stage.glob('*.endf'))),
                "failed": failed, "unstageable": unstageable,
                "files": sorted(p.name for p in stage.glob('*.endf'))}
    (stage.parent / "stage_manifest.json").write_text(
        json.dumps(manifest, indent=1))
    print(json.dumps({k: manifest[k] for k in
                      ("tapes", "staged", "failed")}))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
