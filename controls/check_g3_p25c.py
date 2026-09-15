#!/usr/bin/env python3
"""Independent checker for P25c G3 — post-patch census and floor freeze.

Imports no production module.  Rehashes every input, verifies every built
file's recorded SHA-256 against the live corpora and the sealed manifests,
recomputes all coverage/recovery accounting, re-runs the frozen signature
census live over the patched sealed population (asserting zero residual
leak-signature hits), verifies the frozen amendment floors, and rejects
planted mutations.
"""
from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RESULTS = REPO / "results"
sys.path.insert(0, str(REPO / "controls"))
from g1_p25c_signature import census_file  # noqa: E402
import g1_p25c_signature  # noqa: E402

SEALS = RESULTS / "g0_p25c_seals.json"
SIGNATURE = RESULTS / "g1_p25c_signature.json"
PATCH = RESULTS / "g2_p25c_patch.json"
RECORD = RESULTS / "g3_p25c_census.json"
PATCHED_MANIFEST = RESULTS / "g2_p25c_patched_manifest.sha256"
SOURCE_MANIFEST = RESULTS / "g0_p25c_manifest_tendl_2025_n.txt"
SOURCE_ROOT = Path.home() / "nuclear-data" / "tendl-2025" / "files" / "n"
PATCHED_ROOT = Path.home() / "nuclear-data" / "tendl-2025-patched" / "files" / "n"

FLOORS = {"irdff": 29, "union": 43, "anchors": 41}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_manifest(path: Path) -> dict:
    out = {}
    for line in path.read_text().splitlines():
        digest, name = line.split(None, 1)
        out[name.strip()] = digest
    return out


def coverage(builds: dict, jobs: list) -> dict:
    irdff = {f for r, f in jobs if r == "irdff"}
    union = {f for r, f in jobs if r in ("irdff", "isomeric")}
    anchor = {f for r, f in jobs if r.startswith("anchor")}
    return {
        "irdff_built": sum(1 for f in irdff
                         if builds.get(f, {}).get("class") == "ok"),
        "union_built": sum(1 for f in union
                         if builds.get(f, {}).get("class") == "ok"),
        "anchors_built": sum(1 for f in anchor
                           if builds.get(f, {}).get("class") == "ok"),
        "corpus_incomplete": sorted(
            f for f, b in builds.items()
            if b["class"] == "corpus_incomplete"),
    }


def check_report(record: dict, live: dict) -> list[str]:
    failures = []
    if record.get("schema") != "actinv-p25c-g3-census-1":
        failures.append("schema")
    for key, path in (("seals_sha256", SEALS),
                      ("signature_sha256", SIGNATURE),
                      ("patch_sha256", PATCH)):
        if record.get(key) != sha256(path):
            failures.append(f"{key} mismatch")
    jobs = [(j["role"], j["file"]) for j in record.get("jobs", [])]
    builds = record.get("builds", {})
    for leg in ("patched", "source"):
        if set(builds.get(leg, {})) != {f for _r, f in jobs}:
            failures.append(f"{leg}: build set != job set")
        if coverage(builds.get(leg, {}), jobs) != \
                record.get("coverage", {}).get(leg):
            failures.append(f"coverage[{leg}] recompute mismatch")
        for fname, b in builds.get(leg, {}).items():
            if b.get("file_sha256") != live[leg].get(fname):
                failures.append(f"{leg}/{fname}: recorded sha != live")
            if live[f"{leg}_manifest"].get(fname) != live[leg].get(fname):
                failures.append(f"{leg}/{fname}: not in sealed manifest")
    rec = sorted(f for f, b in builds.get("patched", {}).items()
                 if b["class"] == "ok"
                 and builds.get("source", {}).get(f, {}).get("class") != "ok")
    if rec != sorted(record.get("recovered_files", [])):
        failures.append("recovered_files recompute mismatch")
    nb = sorted(f for f, b in builds.get("patched", {}).items()
                if b["class"] != "ok"
                and builds.get("source", {}).get(f, {}).get("class") == "ok")
    if nb != sorted(record.get("newly_broken_files", [])):
        failures.append("newly_broken_files recompute mismatch")
    for name, det in record.get("rescan", {}).items():
        if det["pre_class"] != live["pre_classes"].get(name):
            failures.append(f"rescan {name}: pre_class != G1 class")
        if live["pre_classes"].get(name) == "confirmed_leak_signature" \
                and not det.get("patched"):
            failures.append(f"rescan {name}: signature file not patched")
    cov = record.get("coverage", {}).get("patched", {})
    if cov.get("irdff_built", -1) < FLOORS["irdff"]:
        failures.append("F2 irdff floor")
    if cov.get("union_built", -1) < FLOORS["union"]:
        failures.append("F2 union floor")
    if cov.get("anchors_built", -1) < FLOORS["anchors"]:
        failures.append("F2 anchors floor")
    if record.get("newly_broken_files"):
        failures.append("F1 regression present")
    if len(record.get("recovered_files", [])) < 1:
        failures.append("F3 no recovery")
    if live["residual_signature"]:
        failures.append(f"F4 residual leak signature: "
                        f"{live['residual_signature']}")
    return failures


def main() -> int:
    failures: list[str] = []
    seals = json.loads(SEALS.read_text())
    sig = json.loads(SIGNATURE.read_text())
    record = json.loads(RECORD.read_text())

    patch = json.loads(PATCH.read_text())
    if seals["source_corpus"]["manifest_sha256"] != sha256(SOURCE_MANIFEST):
        failures.append("source manifest hash != G0 seal")
    if patch["manifest_sha256"] != sha256(PATCHED_MANIFEST):
        failures.append("patched manifest hash != G2 record")
    live = {
        "source_manifest": load_manifest(SOURCE_MANIFEST),
        "patched_manifest": load_manifest(PATCHED_MANIFEST),
        "pre_classes": {n: r["class"] for n, r in sig["files"].items()},
    }
    for leg, root in (("patched", PATCHED_ROOT), ("source", SOURCE_ROOT)):
        live[leg] = {f: sha256(root / f) for f in record["builds"][leg]}
        live[f"{leg}_manifest"] = live[
            "patched_manifest" if leg == "patched" else "source_manifest"]

    # sealed-population byte integrity: non-patched sealed files identical
    patch_pop = {n for n, c in live["pre_classes"].items()
                 if c == "confirmed_leak_signature"}
    for name in seals["sealed_population"]["files"]:
        if name not in patch_pop and live["source_manifest"].get(name) != \
                live["patched_manifest"].get(name):
            failures.append(f"{name}: unpatched sealed file diverged")

    # live signature re-census over the patched sealed population (F4)
    g1_p25c_signature.SOURCE_ROOT = PATCHED_ROOT
    live["residual_signature"] = sorted(
        n for n, det in seals["sealed_population"]["files"].items()
        if census_file(n, det)["class"] == "confirmed_leak_signature")

    failures.extend(check_report(record, live))

    mutations = rejected = 0
    plants = [
        lambda r: r["coverage"]["patched"].update({"irdff_built": 47}),
        lambda r: r.update({"recovered_files": []}),
        lambda r: r.update({"newly_broken_files": ["n-Na023.tendl"]}),
        lambda r: r["builds"]["patched"]["n-Sc045.tendl"]
                  .update({"class": "construction_error"}),
        lambda r: r["rescan"]["n-Sc045.tendl"].update(
            {"pre_class": "self_channel_only"}),
        lambda r: r.update({"seals_sha256": "0" * 64}),
        lambda r: r["builds"]["source"]["n-Y088.tendl"]
                  .update({"file_sha256": "0" * 64}),
        lambda r: r["jobs"].pop(),
    ]
    for plant in plants:
        m = copy.deepcopy(record)
        plant(m)
        mutations += 1
        if check_report(m, live):
            rejected += 1
        else:
            print("MUTATION NOT REJECTED")
    if rejected != mutations:
        failures.append(f"mutation self-test: {rejected}/{mutations} rejected")

    result = {
        "schema": "actinv-p25c-g3-check-1",
        "pass": not failures,
        "failures": failures,
        "files_live_verified": sum(
            len(record["builds"][l]) for l in ("patched", "source")),
        "residual_signature_files": live["residual_signature"],
        "mutation_self_test": {"planted": mutations, "rejected": rejected},
    }
    out = RESULTS / "g3_p25c_check.json"
    out.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")
    print(json.dumps(result, indent=1, sort_keys=True))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
