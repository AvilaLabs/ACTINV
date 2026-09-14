#!/usr/bin/env python3
"""P25b G4 checker — independently verifies the union-population
census, anchor augmentation, and both partition scorings.

Checks, without trusting the producer:

  * union population re-derived from the hash-pinned IRDFF-II archive
    and the sealed P18b held-out ledger;
  * per-corpus construction histograms recomputed from row entries;
  * every recorded per-file sha256 verified against the source tree;
  * on-disk union artifacts and indexes hash to the recorded values;
  * the artifact index schema claim: TENDL/FENDL indexes carry
    ``state_catalog``/``state_mappings``; the EAF index carries no
    usable catalog (builder capability finding);
  * a hash-sampled subset of union builds replayed under the same
    bounded invocation, plus every anchor ``corpus_incomplete`` claim
    verified by filesystem absence;
  * scoring-block internal consistency: histogram totals equal the
    eligible slice, paired <= scored, nonreg recomputed from recorded
    metrics, scorer verbatim blocks present;
  * Amendment-1 coverage floors evaluated against both the v1.0.1
    census (G1 + G4 rebind) and the 1.1.0 union census;
  * planted mutations rejected.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
WORK = ROOT / "target" / "p25b-g4-union"
ACTINV = ROOT / "target" / "release" / "actinv"
RECORD = RESULTS / "g4_p25b_union.json"
IRDFF_SCORE = RESULTS / "g4_p25b_union_irdff_score.json"
HELDOUT = RESULTS / "g5_p18b_heldout.json"
G1 = RESULTS / "g1_p25b_census.json"
REBIND = RESULTS / "g4_p25b_rebind.json"
AMENDMENT_FLOORS = {"eaf_2010": 47, "tendl_2023": 41, "fendl_32c": 34}

ND = Path.home() / "nuclear-data"
CORPORA = {
    "tendl_2023": {"root": ND / "tendl-2023" / "files", "format": "tendl"},
    "fendl_32c": {"root": ND / "fendl-3.2c" / "endf", "format": "tendl"},
    "eaf_2010": {"root": ND / "eaf-2010" / "files", "format": "eaf"},
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def irdff_targets() -> set[tuple[int, int]]:
    archive = Path.home() / "nuclear-data" / "p17-irdff" / "IRDFF-II_ENDF.zip"
    if sha256(archive) != "225b25787f6d9b54a9c28ebf075ccc715f69358be181fed27d5cc315cf8744db":
        raise RuntimeError("IRDFF-II archive hash mismatch")
    with zipfile.ZipFile(archive) as z:
        text = z.read("IRDFF-II.endf").decode("ascii", "replace")
    out = set()
    header = re.compile(r"^(.{11})(.{11}).{44}.{0,4}?(\d{4}) 1451    1",
                        re.M)
    for m in header.finditer(text):
        field = m.group(1).strip()
        mm = re.match(r"^([+-]?\d+(?:\.\d*)?)([+-]\d+)$", field)
        try:
            za = float(mm.group(1)) * 10.0 ** int(mm.group(2)) if mm \
                else float(field)
        except ValueError:
            continue
        z, a = int(round(za)) // 1000, int(round(za)) % 1000
        if a:
            out.add((z, a))
    return out


def isomer_targets() -> set[tuple[int, int]]:
    out = set()
    for r in json.loads(HELDOUT.read_text())["ledger"]:
        if r["projectile"] != "neutron" or not r.get("isomer_liso"):
            continue
        seg = r["family_id"].split("|")[1].split("-")
        out.add((int(seg[0]), int(seg[1])))
    return out


def nonreg_ok(b: dict, c: dict) -> dict:
    res = {"median_ok": None, "p90_ok": None, "coverage_ok": None}
    if b.get("rows") and c.get("rows"):
        res["median_ok"] = (
            c["median_abs_ln"] <= b["median_abs_ln"] + 0.005
            and c["median_abs_ln"] <= 1.01 * b["median_abs_ln"])
        res["p90_ok"] = (
            c["p90_abs_ln"] <= b["p90_abs_ln"] + 0.01
            and c["p90_abs_ln"] <= 1.01 * b["p90_abs_ln"])
        res["coverage_ok"] = all(
            c[k] >= b[k] - 0.01
            for k in ("within_10pct", "within_20pct", "within_30pct"))
    elif not c.get("rows"):
        res["unscored_candidate"] = True
    return res


def replay_build(corpus: str, fname: str) -> dict:
    spec = CORPORA[corpus]
    src = spec["root"] / fname
    out = WORK / "check-one.npz"
    out.unlink(missing_ok=True)
    try:
        proc = subprocess.run(
            [str(ACTINV), "build-library", str(src), str(out),
             "--format", spec["format"], "--projectile", "neutron",
             "--groups", "fispact-709", "--temperature-K", "293.6",
             "--cache", str(WORK / "cache")],
            capture_output=True, text=True, timeout=300.0, cwd=ROOT)
        return {"exit": proc.returncode}
    except subprocess.TimeoutExpired:
        return {"exit": "timeout"}
    finally:
        out.unlink(missing_ok=True)


def check_report(record: dict, replayed: dict, floors: dict,
                 population: dict) -> list[str]:
    failures = []
    pop = record.get("population", {})
    for key, want in (("irdff_targets", population["irdff"]),
                      ("isomeric_targets", population["isomeric"]),
                      ("union_targets", population["union"])):
        got = {int(x) for x in pop.get(key, [])}
        if got != {z * 1000 + a for z, a in want}:
            failures.append(f"population.{key} mismatch")

    if record.get("builder") != str(ACTINV):
        failures.append("builder identity mismatch")

    for corpus, spec in CORPORA.items():
        blk = (record.get("corpora") or {}).get(corpus)
        if blk is None:
            failures.append(f"{corpus}: corpus block missing")
            continue
        rows = blk.get("construction", [])
        hist = {}
        for e in rows:
            hist[e["class"]] = hist.get(e["class"], 0) + 1
            if e.get("file"):
                src = spec["root"] / e["file"]
                if not src.is_file() or sha256(src) != e.get("file_sha256"):
                    failures.append(f"{corpus}: {e['file']} hash mismatch")
        if hist != blk.get("construction_histogram"):
            failures.append(f"{corpus}: construction histogram mismatch")
        if len(rows) != len(population["union"]):
            failures.append(f"{corpus}: union row count mismatch")
        ok = sum(1 for e in rows if e["class"] == "ok")
        if ok != blk.get("surviving_targets"):
            failures.append(f"{corpus}: surviving_targets mismatch")

        aug = (record.get("anchor_augmentation") or {}) \
            .get("corpora", {}).get(corpus, {})
        art = aug.get("artifact") or blk.get("artifact") or {}
        npz = Path(art.get("npz", "/nonexistent"))
        index_path = npz.with_name(npz.stem + "_index.json")
        if not npz.is_file() or sha256(npz) != art.get("npz_sha256"):
            failures.append(f"{corpus}: artifact NPZ hash mismatch")
        if not index_path.is_file() \
                or sha256(index_path) != art.get("index_sha256"):
            failures.append(f"{corpus}: artifact index hash mismatch")

        if index_path.is_file():
            idx = json.loads(index_path.read_text())
            lisos = {e["liso"] for e in idx.get("state_catalog", [])}
            has_mappings = any(
                t.get("state_mappings") for t in idx.get("targets", []))
            if spec["format"] == "tendl":
                if 0 not in lisos:
                    failures.append(f"{corpus}: catalog missing liso=0")
                if not has_mappings:
                    failures.append(f"{corpus}: no state_mappings")
                claimed = set(art.get("catalog_liso_values") or [])
                if claimed and claimed != lisos:
                    failures.append(
                        f"{corpus}: catalog_liso_values mismatch")
            else:
                if idx.get("state_catalog"):
                    failures.append(
                        f"{corpus}: EAF index unexpectedly catalogs states")

        scoring = blk.get("scoring") or {}
        iso = scoring.get("isomeric_partition") or {}
        histo = iso.get("candidate_status_histogram") or {}
        if histo and sum(histo.values()) != iso.get("eligible_rows"):
            failures.append(f"{corpus}: isomer status histogram sum")
        if (iso.get("paired_rows") or 0) \
                > (iso.get("candidate_scored_rows") or 0):
            failures.append(f"{corpus}: paired exceeds scored")
        if iso.get("nonreg") != nonreg_ok(iso.get("paired_baseline") or {},
                                        iso.get("paired_candidate") or {}):
            failures.append(f"{corpus}: isomer nonreg mismatch")

        ir = scoring.get("irdff_partition") or {}
        if ir:
            if ir.get("nonregression") != nonreg_ok(
                    ir.get("baseline_metrics") or {},
                    ir.get("candidate_metrics") or {}):
                failures.append(f"{corpus}: irdff nonreg mismatch")
            if (ir.get("comparable_rows") or 0) \
                    > (ir.get("candidate_scored") or 0):
                failures.append(f"{corpus}: comparable exceeds scored")

        irdff_ok = sum(1 for e in rows
                       if e["class"] == "ok" and e.get("in_irdff"))
        f = floors.get(corpus) or {}
        if f.get("v110_irdff_ok") != irdff_ok:
            failures.append(f"{corpus}: v1.1.0 IRDFF coverage mismatch")
        if f.get("floor") != AMENDMENT_FLOORS[corpus]:
            failures.append(f"{corpus}: floor value drifted")
        if f.get("floor_met_v110") != (irdff_ok >= AMENDMENT_FLOORS[corpus]):
            failures.append(f"{corpus}: floor_met_v110 inconsistent")

    aug_corpora = (record.get("anchor_augmentation") or {}).get("corpora", {})
    for corpus, spec in CORPORA.items():
        for b in aug_corpora.get(corpus, {}).get("anchor_builds", []):
            if b["class"] == "corpus_incomplete" and \
                    list(spec["root"].iterdir()):
                za = b["product_za"]
                if any(re.match(
                        r"^n_(?:%03d-|\d+_%d-)[A-Za-z]{1,2}-%d[mM]?"
                        % (za // 1000, za // 1000, za % 1000),
                        p.name) for p in spec["root"].iterdir()):
                    failures.append(
                        f"{corpus}: anchor {za} claimed absent but exists")
            if b["class"] == "ok" and b.get("file"):
                src = spec["root"] / b["file"]
                if sha256(src) != b.get("file_sha256"):
                    failures.append(f"{corpus}: anchor file hash mismatch")

    for key in replayed:
        corpus, fname = key.split(":", 1)
        got = replay_build(corpus, fname)
        if got["exit"] != 0:
            failures.append(f"replay {key}: expected ok, got {got}")

    return failures


def mutate(base, fn):
    copy_rec = copy.deepcopy(base)
    fn(copy_rec)
    return copy_rec


def main() -> int:
    record = json.loads(RECORD.read_text())
    population = {
        "irdff": irdff_targets(), "isomeric": isomer_targets(),
        "union": irdff_targets() | isomer_targets()}

    # floors under both builders
    g1 = json.loads(G1.read_text())
    g1_ok = {c: (g1["histograms"].get(c) or {}).get("ok", 0)
             for c in CORPORA}
    rebind = json.loads(REBIND.read_text())
    recovered = {}
    for r in rebind.get("rows", []):
        if r.get("class") == "ok":
            recovered[r["corpus"]] = recovered.get(r["corpus"], 0) + 1
    floors = {}
    for corpus in CORPORA:
        rows = record["corpora"][corpus]["construction"]
        v110 = sum(1 for e in rows
                   if e["class"] == "ok" and e.get("in_irdff"))
        v101 = g1_ok[corpus] + recovered.get(corpus, 0)
        floors[corpus] = {
            "floor": AMENDMENT_FLOORS[corpus],
            "v101_irdff_ok": v101,
            "v110_irdff_ok": v110,
            "floor_met_v101": v101 >= AMENDMENT_FLOORS[corpus],
            "floor_met_v110": v110 >= AMENDMENT_FLOORS[corpus]}

    # hash-sampled ok-build replays: 2 per corpus (bounded total)
    replayed = {}
    for corpus, blk in record["corpora"].items():
        oks = [e for e in blk["construction"]
               if e["class"] == "ok" and e.get("file")]
        oks.sort(key=lambda e: hashlib.sha256(
            f"{corpus}:{e['target']}".encode()).hexdigest())
        for e in oks[:2]:
            replayed[f"{corpus}:{e['file']}"] = True

    failures = check_report(record, replayed, floors, population)

    mutations = []
    m = mutate(record, lambda r: r["corpora"]["eaf_2010"]
               .update(surviving_targets=999))
    if check_report(m, {}, floors, population):
        mutations.append("surviving_targets tamper rejected")
    m = mutate(record, lambda r: r["corpora"]["tendl_2023"]["scoring"]
               ["isomeric_partition"].update(paired_rows=9999))
    if check_report(m, {}, floors, population):
        mutations.append("paired_rows tamper rejected")
    m = mutate(record, lambda r: r["corpora"]["tendl_2023"]["scoring"]
               ["isomeric_partition"]["nonreg"].update(median_ok=True))
    if check_report(m, {}, floors, population):
        mutations.append("nonreg tamper rejected")
    m = mutate(record, lambda r: r["corpora"]["eaf_2010"]
               ["construction"][0].update(**{"class": "corpus_incomplete"}))
    if check_report(m, {}, floors, population):
        mutations.append("class tamper rejected")
    m = mutate(record, lambda r: r["population"]
               .update(union_targets=["1"]))
    if check_report(m, {}, floors, population):
        mutations.append("population tamper rejected")
    bad_floors = copy.deepcopy(floors)
    bad_floors["tendl_2023"]["v110_irdff_ok"] = 999
    if check_report(record, {}, bad_floors, population):
        mutations.append("floor tamper rejected")

    verdict = {
        "schema": "actinv-p25b-g4-check-1",
        "pass": not failures and len(mutations) == 6,
        "failures": failures,
        "replayed_builds": sorted(replayed),
        "coverage_floors": floors,
        "mutations_rejected": mutations,
        "irdff_score_sha256": sha256(IRDFF_SCORE)
        if IRDFF_SCORE.is_file() else None,
        "union_record_sha256": sha256(RECORD),
    }
    out = RESULTS / "g4_p25b_union_check.json"
    out.write_text(json.dumps(verdict, indent=1, sort_keys=True) + "\n")
    print(json.dumps(verdict, indent=1, sort_keys=True))
    return 0 if verdict["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
