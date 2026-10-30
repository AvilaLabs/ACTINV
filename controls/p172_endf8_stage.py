#!/usr/bin/env python3
"""Stage the ENDF/B-VIII.1 ENDF-6 neutron tapes for P172's identical-data ACTINV arm.

The selection is every neutron nuclide in OpenMC's ENDF/B-VIII.1 HDF5 library (`cross_sections.xml`), so the ACTINV
arm and the OpenMC arms read the same evaluations. (P171's library was staged from the nuclides reachable in a
21-experiment OpenMC run and lacked 15 FNS elements; see ledger entry 293.)

Source: NNDC's ENDF/B-VIII.1 incident-neutron release archive,
https://www.nndc.bnl.gov/endf-releases/releases/B-VIII.1/neutrons/neutrons-version.VIII.1.tar.gz, extracted to
SOURCE. Each selected tape is copied unchanged to <stage>/<OpenMC name>.endf and its SHA-256 recorded in
<stage>/../stage_manifest.json. A nuclide OpenMC has and the archive lacks stops the staging.

Usage: python3 controls/p172_endf8_stage.py [--source DIR] [--stage DIR]
"""
import argparse
import hashlib
import json
import re
import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

DATA = Path.home() / "nuclear-data"
XS_XML = DATA / "endfb-viii.1-hdf5/cross_sections.xml"
ARCHIVE = DATA / "endfb-viii.1-endf6/neutrons-version.VIII.1.tar.gz"
ARCHIVE_SHA256 = "decff90016bfb5c25c8d4c7fb8d81f94ff2f104853165f0fe6a21bf61c4164e4"
SOURCE_DEFAULT = DATA / "endfb-viii.1-endf6/neutrons-version.VIII.1"
STAGE_DEFAULT = DATA / "endfb-viii.1-p172/stage"
TAPE = re.compile(r"^n-(\d+)_([A-Za-z]+)_(\d+)(m\d)?\.endf$")  # n-077_Ir_191.endf, n-047_Ag_110m1.endf


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def openmc_name(filename):
    """NNDC tape name -> OpenMC nuclide name (Ir191, Ag110_m1), or None."""
    match = TAPE.match(filename)
    if not match:
        return None
    _, symbol, a, meta = match.groups()
    return f"{symbol}{int(a)}" + (f"_{meta}" if meta else "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", type=Path, default=SOURCE_DEFAULT)
    ap.add_argument("--stage", type=Path, default=STAGE_DEFAULT)
    args = ap.parse_args()
    if sha256_file(ARCHIVE) != ARCHIVE_SHA256:
        sys.exit(f"p172_endf8_stage: {ARCHIVE} is not the pinned NNDC archive")
    root = ET.parse(XS_XML).getroot()
    wanted = sorted({lib.get("materials") for lib in root.iter("library") if lib.get("type") == "neutron"})
    tapes = {openmc_name(p.name): p for p in args.source.glob("n-*.endf") if openmc_name(p.name)}
    missing = [n for n in wanted if n not in tapes]
    if missing:
        sys.exit(f"p172_endf8_stage: the archive lacks {len(missing)} OpenMC nuclides: {' '.join(missing)}")
    if args.stage.exists() and any(args.stage.iterdir()):
        sys.exit(f"p172_endf8_stage: {args.stage} is not empty")
    args.stage.mkdir(parents=True, exist_ok=True)
    files = {}
    for name in wanted:
        out = args.stage / f"{name}.endf"
        shutil.copyfile(tapes[name], out)
        files[out.name] = {"source": tapes[name].name, "sha256": sha256_file(out)}
    manifest = {"selection": "every neutron nuclide in OpenMC's ENDF/B-VIII.1 cross_sections.xml",
                "cross_sections_xml_sha256": sha256_file(XS_XML), "archive": ARCHIVE.name,
                "archive_sha256": ARCHIVE_SHA256, "archive_tapes": len(tapes),
                "not_selected": sorted(set(tapes) - set(wanted)), "staged": len(files), "files": files}
    (args.stage.parent / "stage_manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
    print(json.dumps({k: manifest[k] for k in ("archive_tapes", "staged", "not_selected")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
