#!/usr/bin/env python3
"""P26b G0 — opening seal, identity pins, executable-subset freeze.

Binds the frozen P26b protocol hash, the opening commit, all prior
verdicts asserted verbatim, and the refreshed comparator census.  Pins
the identities the replan depends on: the ALARA 2.9.2 binary, NJOY
2016.79, the FENDL-3.2c corpus (manifest digest plus a re-derived file
sample), the fetched UKDD-2020 decay library, the maintainer's Avila
Core checkout (present on this workstation now, unlike at P26 G0), and
the ACTINV HEAD.  Freezes the executable-subset declaration and seals
the ``p26b_qualifying`` evidence partition before any conversion or
measurement runs.

Writes ``results/g0_p26b_seals.json``.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results" / "g0_p26b_seals.json"
PROTOCOL = ROOT / "protocols" / "ACTINV-P26b_PROTOCOL.md"
PROTOCOL_SHA256 = "a1c433c842824c97637f5673a68c26b388dac1f8a237c009417ccb8d10d55327"
OPENING_COMMIT = "79b069ddaadc104b4d6d2131311b64202894b2d8"

EXPECTED_VERDICTS = {
    "verdict_p2.json": "P2-CONDITIONAL",
    "verdict_p3.json": "P3-FAIL",
    "verdict_p3b.json": "P3b-PASS",
    "verdict_p4.json": "P4-FAIL",
    "verdict_p4b.json": "P4b-PASS",
    "verdict_p5.json": "P5-PASS",
    "verdict_p6.json": "P6-CONDITIONAL",
    "verdict_p7.json": "P7-CONDITIONAL",
    "verdict_p8.json": "P8-CONDITIONAL",
    "verdict_p9.json": "P9-CONDITIONAL",
    "verdict_p10.json": "P10-CONDITIONAL",
    "verdict_p11.json": "P11-CONDITIONAL",
    "verdict_p12.json": "P12-CONDITIONAL",
    "verdict_p13.json": "P13-PASS",
    "verdict_p14.json": "P14-CLOSED-BELOW-THRESHOLD",
    "verdict_p15.json": "P15-PASS",
    "verdict_p16.json": "P16-CONDITIONAL",
    "verdict_p17.json": "P17-FAIL",
    "verdict_p18.json": "P18-FAIL",
    "verdict_p18b.json": "P18b-FAIL",
    "verdict_cb1.json": "CB1-COMPLETE",
    "verdict_p19.json": "P19-PASS",
    "verdict_p20.json": "P20-PASS",
    "verdict_p21.json": "P21-PASS",
    "verdict_p22.json": "P22-PASS",
    "verdict_p23.json": "P23-PASS",
    "verdict_p24.json": "P24-CONDITIONAL",
    "verdict_p25.json": "P25-FAIL",
    "verdict_p25b.json": "P25b-FAIL",
    "verdict_p25c.json": "P25c-PASS",
    "verdict_p26.json": "P26-FAIL",
}

ND = Path.home() / "nuclear-data"
ALARA_BIN = ND / "alara-2.9.2-build" / "src" / "alara"
NJOY_BIN = ND / "njoy2016.79-build" / "njoy"
FENDL = ND / "fendl-3.2c"
UKDD = ND / "ukdd-2020"
CORE_CHECKOUT = Path.home() / "Documents" / "Avila-Labs" / "project-north-star"
CORE_BIN = CORE_CHECKOUT / "target" / "release" / "avila-core"
ACTINV_BIN = ROOT / "target" / "release" / "actinv"

# Frozen at G0 before any conversion: the contract element set and the
# exact FENDL-3.2c nuclide files the conversion will attempt.
CONTRACT_ELEMENTS = {
    "base": {"FE": ["Fe-54", "Fe-56", "Fe-57", "Fe-58"],
             "W": ["W-180", "W-182", "W-183", "W-184", "W-186"]},
    "impurity": {"AG": ["Ag-107", "Ag-109"], "CO": ["Co-59"],
                 "CR": ["Cr-50", "Cr-52", "Cr-53", "Cr-54"],
                 "CU": ["Cu-63", "Cu-65"], "MN": ["Mn-55"],
                 "MO": ["Mo-92", "Mo-94", "Mo-95", "Mo-96", "Mo-97",
                        "Mo-98", "Mo-100"],
                 "NB": ["Nb-93"],
                 "NI": ["Ni-58", "Ni-60", "Ni-61", "Ni-62", "Ni-64"],
                 "TA": ["Ta-180m", "Ta-181"], "V": ["V-50", "V-51"]},
}

# Manifest sample re-derived at G0 (spread across the nuclide set).
FENDL_SAMPLE = [
    "endf/n_2631_26-Fe-56.endf", "endf/n_2831_28-Ni-60.endf",
    "endf/n_4237_42-Mo-96.endf", "endf/n_7443_74-W-186.endf",
    "endf/n_4725_47-Ag-107.endf", "endf/n_2725_27-Co-59.endf",
]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git(*args: str, cwd: Path = ROOT) -> str:
    return subprocess.run(["git", *args], cwd=cwd, text=True,
                          capture_output=True).stdout.strip()


def comparator_census() -> dict:
    probes = {
        "fispact": (["fispact", "fispact-II", "fispact2"], []),
        "openmc": (["openmc"], []),
        "alara": (["alara", "ALARA"], [ALARA_BIN]),
        "scale_origen": (["scale", "origen", "origen-rs"], []),
        "njoy": (["njoy", "njoy2016"], [NJOY_BIN]),
        "actinv": (["actinv"], [ACTINV_BIN]),
    }
    out = {}
    for name, (exes, known) in probes.items():
        found = [e for e in exes if shutil.which(e)]
        local = [str(p) for p in known
                 if p.is_file() and os.access(p, os.X_OK)]
        mods = []
        for mod in {"openmc": ["openmc"], "actinv": ["actinv"]}.get(name, []):
            r = subprocess.run([sys.executable, "-c", f"import {mod}"],
                               capture_output=True)
            if r.returncode == 0:
                mods.append(mod)
        out[name] = {"executables": found, "local_executables": local,
                     "python_modules": mods,
                     "status": "executable" if (found or mods or local)
                     else "not_available"}
    return out


def fendl_identity() -> dict:
    manifest = FENDL / "MANIFEST.sha256"
    endf_manifest = FENDL / "MANIFEST_endf.sha256"
    entries = {}
    if manifest.is_file():
        for line in manifest.read_text().splitlines():
            if "  ./" in line:
                digest, rel = line.split("  ./", 1)
                entries[rel] = digest
    if endf_manifest.is_file():
        for line in endf_manifest.read_text().splitlines():
            if "  " in line:
                digest, base = line.split("  ", 1)
                entries.setdefault("endf/" + base, digest)
    sample = {}
    ok = True
    for rel in FENDL_SAMPLE:
        path = FENDL / rel
        if not path.is_file():
            sample[rel] = "missing"
            ok = False
            continue
        got = sha256(path)
        want = entries.get(rel)
        sample[rel] = {"sha256": got, "manifest_sha256": want,
                       "match": got == want}
        ok = ok and (got == want)
    return {"path": str(FENDL),
            "manifest_sha256": sha256(manifest) if manifest.is_file() else None,
            "endf_manifest_sha256": sha256(endf_manifest)
            if endf_manifest.is_file() else None,
            "endf_file_count": len(list((FENDL / "endf").glob("*.endf"))),
            "sample_rederivation": sample, "sample_ok": ok}


def ukdd_identity() -> dict:
    tarball = UKDD / "decay2020.tar.bz2"
    tree = UKDD / "decay_2020"
    index = UKDD / "decay_2020_index.txt"
    files = sorted(p for p in tree.iterdir()
                   if p.is_file()) if tree.is_dir() else []
    tree_hash = hashlib.sha256()
    for p in files:
        tree_hash.update(p.name.encode())
        tree_hash.update(sha256(p).encode())
    return {"status": "pinned" if files else "unavailable",
            "path": str(UKDD),
            "source_url": "https://www.oecd-nea.org/dbdata/fispact/decay2020.tar.bz2",
            "tarball_sha256": sha256(tarball) if tarball.is_file() else None,
            "index_sha256": sha256(index) if index.is_file() else None,
            "nuclide_file_count": len(files),
            "tree_sha256": tree_hash.hexdigest() if files else None,
            "retrieved": datetime.fromtimestamp(
                tarball.stat().st_mtime, tz=timezone.utc).isoformat()
            if tarball.is_file() else None}


def core_identity() -> dict:
    if not (CORE_CHECKOUT / ".git").exists():
        return {"status": "unavailable", "path": None, "head": None,
                "consequence": "No maintainer checkout of Avila Core."}
    head = git("rev-parse", "HEAD", cwd=CORE_CHECKOUT)
    dirty = git("status", "--porcelain", cwd=CORE_CHECKOUT).splitlines()
    profile = None
    if CORE_BIN.is_file():
        r = subprocess.run([str(CORE_BIN), "semantic-profile"],
                           capture_output=True, text=True)
        if r.returncode == 0:
            profile = json.loads(r.stdout).get("semantic_profile")
    return {"status": "pinned", "path": str(CORE_CHECKOUT), "head": head,
            "binary_sha256": sha256(CORE_BIN) if CORE_BIN.is_file() else None,
            "semantic_profile": profile,
            "working_tree_state": ("dirty: " + "; ".join(dirty)
                                   if dirty else "clean"),
            "note": ("Another session is actively editing this checkout; "
                     "the pin records the commit identity and the dirty "
                     "state at seal time.  The binary hash identifies the "
                     "runner actually used.")}


def executable_subset() -> dict:
    nuclides = [iso for group in CONTRACT_ELEMENTS.values()
                for isos in group.values() for iso in isos]
    all_files = {p.name for p in (FENDL / "endf").glob("*.endf")}
    missing = {}
    for group in CONTRACT_ELEMENTS.values():
        for el, isos in group.items():
            for iso in isos:
                sym = iso.replace("-", "-")
                expected = [n for n in all_files
                            if n.lower().endswith("-" + sym.lower() + ".endf")]
                if not expected:
                    missing.setdefault(el, []).append(iso)
    return {
        "contract_elements": CONTRACT_ELEMENTS,
        "nuclide_count": len(nuclides),
        "nuclides": sorted(nuclides),
        "fendl_files_missing": missing,
        "subset_rule": (
            "A contract case is in the executable subset iff every natural-"
            "element isotope of its material is present in the converted "
            "ALARA library.  With the declared 36-nuclide set fully "
            "converted, the expected subset is the complete eligible "
            "population: all 1000 W-CAMPAIGN cases plus all 16 W-MATCMP "
            "cases (1016 total).  W-R2S is excluded by protocol (no "
            "transport comparator)."),
        "frozen_at": "G0, before any conversion or measurement",
    }


def main() -> int:
    failures: list[str] = []
    if sha256(PROTOCOL) != PROTOCOL_SHA256:
        failures.append("protocol sha256 mismatch")
    if subprocess.run(["git", "merge-base", "--is-ancestor",
                       OPENING_COMMIT, "HEAD"], cwd=ROOT).returncode != 0:
        failures.append("opening commit is not an ancestor of HEAD")

    verdicts = {}
    for name, expected in EXPECTED_VERDICTS.items():
        path = ROOT / "results" / name
        if not path.exists():
            failures.append(f"missing verdict {name}")
            continue
        got = json.loads(path.read_text()).get("verdict")
        verdicts[name] = got
        if got != expected:
            failures.append(f"{name} verdict {got} != {expected}")

    census = comparator_census()
    if census["alara"]["status"] != "executable":
        failures.append("alara not executable — replan item (a) blocked")
    if census["njoy"]["status"] != "executable":
        failures.append("njoy not executable — conversion path blocked")
    if census["openmc"]["status"] == "executable":
        pass  # honest re-check: would change replan item (d)
    else:
        census["openmc"]["note"] = ("absent — replan item (d) stays "
                                    "unmeasurable, inherited by P32")

    fendl = fendl_identity()
    if not fendl["sample_ok"]:
        failures.append("FENDL-3.2c manifest sample mismatch")
    ukdd = ukdd_identity()
    if ukdd["status"] != "pinned":
        failures.append("UKDD-2020 decay library not pinned")
    subset = executable_subset()
    if subset["fendl_files_missing"]:
        failures.append(f"FENDL files missing for {subset['fendl_files_missing']}")

    record = {
        "schema": "actinv-p26b-g0-seal-1",
        "phase": "P26b",
        "gate": "G0",
        "protocol_sha256": PROTOCOL_SHA256,
        "opening_commit": OPENING_COMMIT,
        "seal_commit": git("rev-parse", "HEAD"),
        "prior_verdicts": verdicts,
        "identity_pins": {
            "actinv_head": git("rev-parse", "HEAD"),
            "actinv_binary_sha256": sha256(ACTINV_BIN)
            if ACTINV_BIN.is_file() else None,
            "alara": {"path": str(ALARA_BIN),
                      "sha256": sha256(ALARA_BIN)
                      if ALARA_BIN.is_file() else None,
                      "version": "2.9.2"},
            "njoy": {"path": str(NJOY_BIN),
                     "sha256": sha256(NJOY_BIN)
                     if NJOY_BIN.is_file() else None,
                     "version": "2016.79"},
            "fendl_3_2c": fendl,
            "ukdd_2020": ukdd,
            "avila_core": core_identity(),
        },
        "comparator_census": census,
        "executable_subset": subset,
        "partition": {
            "p26b_qualifying": {
                "consumers": ["P26b verdict gate only"],
                "consumption": "once, at G3",
                "contents": "comparator-leg outputs and wall times measured under the frozen subset",
            },
            "diagnostic": {"consumption": "unlimited within P26b"},
        },
        "replan_disposition": {
            "a_comparator_dataset": "executable — FENDL-3.2c + ALARAJOY + NJOY + UKDD-2020 all pinned",
            "b_headroom_rescope": "deferred to G3 roadmap entry",
            "c_practitioner_study": "unestablished — maintainer's, not executable here",
            "d_transport_comparator": "unmeasurable — OpenMC absent, inherited by P32",
        },
        "pass": not failures,
        "failures": failures,
    }
    RESULT.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    print(json.dumps(record, indent=1, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
