#!/usr/bin/env python3
"""P25c G3 — post-patch census and coverage projection.

Three measurements, all against the sealed G0/G1/G2 evidence:

* **Post-patch defect rescan.**  Re-runs the frozen P25 decimal-oracle
  ``discriminate`` on the patched-corpus version of every sealed file;
  records the post-patch class per file and the corpus defect-class
  delta.  Byte-identical files carry their unpatched class forward.

* **Union coverage, both corpora.**  Bounded per-file builds (300 s,
  ``target/release/actinv`` = the shipped 1.1.0 builder) for every
  union-population file (47 IRDFF ∪ 37 isomeric-row targets) plus every
  product-state anchor file needed by the held-out isomeric fold — run
  for BOTH the pinned source corpus and the patched corpus, so the
  coverage delta is measured, not assumed.

* **Projection.**  Publishes projected post-patch construction coverage
  over the IRDFF-II and union sets for the Amendment-1 floor freeze.

Writes ``results/g3_p25c_census.json``.  All builds are read-only on the
corpora; scratch artifacts go under ``target/p25c-g3/``.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RESULTS = REPO / "results"
sys.path.insert(0, str(REPO / "controls"))
from g2_p25_traces import discriminate  # noqa: E402

SEALS = RESULTS / "g0_p25c_seals.json"
SIGNATURE = RESULTS / "g1_p25c_signature.json"
PATCH = RESULTS / "g2_p25c_patch.json"
UNION = RESULTS / "g4_p25b_union.json"
HELDOUT = RESULTS / "g5_p18b_heldout.json"

SOURCE_ROOT = Path.home() / "nuclear-data" / "tendl-2025" / "files" / "n"
PATCHED_ROOT = Path.home() / "nuclear-data" / "tendl-2025-patched" / "files" / "n"
ACTINV = REPO / "target" / "release" / "actinv"
WORK = REPO / "target" / "p25c-g3"
PER_FILE_BOUND_S = 300.0

ELEMENT_SYMBOLS = (
    "H", "He", "Li", "Be", "B", "C", "N", "O", "F", "Ne", "Na", "Mg",
    "Al", "Si", "P", "S", "Cl", "Ar", "K", "Ca", "Sc", "Ti", "V", "Cr",
    "Mn", "Fe", "Co", "Ni", "Cu", "Zn", "Ga", "Ge", "As", "Se", "Br",
    "Kr", "Rb", "Sr", "Y", "Zr", "Nb", "Mo", "Tc", "Ru", "Rh", "Pd",
    "Ag", "Cd", "In", "Sn", "Sb", "Te", "I", "Xe", "Cs", "Ba", "La",
    "Ce", "Pr", "Nd", "Pm", "Sm", "Eu", "Gd", "Tb", "Dy", "Ho", "Er",
    "Tm", "Yb", "Lu", "Hf", "Ta", "W", "Re", "Os", "Ir", "Pt", "Au",
    "Hg", "Tl", "Pb", "Bi", "Po", "At", "Rn", "Fr", "Ra", "Ac", "Th",
    "Pa", "U", "Np", "Pu", "Am", "Cm", "Bk", "Cf", "Es", "Fm", "Md",
    "No", "Lr", "Rf", "Db", "Sg", "Bh", "Hs", "Mt", "Ds", "Rg", "Cn",
    "Nh", "Fl", "Mc", "Lv", "Ts", "Og",
)
LISO_SUFFIX = {0: "", 1: "m", 2: "n"}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def target_filename(za: int, liso: int = 0) -> str:
    z, a = za // 1000, za % 1000
    return f"n-{ELEMENT_SYMBOLS[z - 1]}{a:03d}{LISO_SUFFIX[liso]}.tendl"


def needed_products() -> dict[tuple[int, int], set]:
    need: dict[tuple[int, int], set] = defaultdict(set)
    for r in json.loads(HELDOUT.read_text())["ledger"]:
        if r["projectile"] != "neutron" or r["status"] != "eligible":
            continue
        if not r.get("isomer_liso"):
            continue
        seg = r["family_id"].split("|")
        pz, pa = seg[2].split("-")
        need[(int(pz), int(pa))].add(r["isomer_liso"])
    return dict(need)


def run_build(src: Path, out: Path, cache: Path) -> dict:
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            [str(ACTINV), "build-library", str(src), str(out),
             "--format", "tendl", "--projectile", "neutron",
             "--groups", "fispact-709", "--temperature-K", "293.6",
             "--cache", str(cache)],
            capture_output=True, text=True, timeout=PER_FILE_BOUND_S,
            cwd=REPO)
        return {"exit": proc.returncode,
                "seconds": round(time.monotonic() - t0, 3),
                "message": (proc.stderr.strip() or proc.stdout.strip())[-600:]}
    except subprocess.TimeoutExpired:
        return {"exit": "timeout", "seconds": round(time.monotonic() - t0, 3),
                "message": f"timeout {PER_FILE_BOUND_S}s"}


def build_set(root: Path, jobs: list[tuple[str, str]], tag: str) -> dict:
    """jobs: list of (role, filename). Returns {filename: build record}."""
    cache = WORK / f"cache-{tag}"
    cache.mkdir(parents=True, exist_ok=True)
    out = {}
    for i, (role, fname) in enumerate(jobs, 1):
        src = root / fname
        scratch = WORK / f"scratch-{tag}.npz"
        if not src.exists():
            out[fname] = {"role": role, "class": "corpus_incomplete",
                          "file_sha256": None}
            continue
        res = run_build(src, scratch, cache)
        scratch.unlink(missing_ok=True)
        out[fname] = {"role": role, "file_sha256": sha256(src), **res,
                      "class": "ok" if res["exit"] == 0
                      else "construction_error"}
        print(f"  [{tag} {i}/{len(jobs)}] {fname}: {out[fname]['class']}",
              flush=True)
    return out


def main() -> int:
    t0 = time.time()
    seals = json.loads(SEALS.read_text())
    sig = json.loads(SIGNATURE.read_text())
    patch = json.loads(PATCH.read_text())
    union = json.loads(UNION.read_text())

    # --- A. post-patch oracle rescan of the sealed population -----------
    rescan = {}
    for name, det in sorted(seals["sealed_population"]["files"].items()):
        pre = sig["files"][name]
        pfile = PATCHED_ROOT / name
        rec = {"pre_class": pre["class"],
               "patched": pre["class"] == "confirmed_leak_signature"}
        try:
            post = discriminate(pfile)
            rec["post_kinds"] = sorted(post.get("kinds", []))
            rec["post_worst_relative_excess"] = str(
                post.get("worst_relative_excess"))
        except Exception as exc:
            rec["post_kinds"] = ["oracle_error"]
            rec["oracle_error"] = str(exc)[:300]
        rescan[name] = rec
        print(f"  rescan {name}: {pre['class']} -> {rec['post_kinds']}",
              flush=True)

    # --- B. union + anchor build jobs ----------------------------------
    irdff = [int(x) for x in union["population"]["irdff_targets"]]
    isomeric = [int(x) for x in union["population"]["isomeric_targets"]]
    union_t = [int(x) for x in union["population"]["union_targets"]]
    anchors = needed_products()

    jobs: list[tuple[str, str]] = []
    seen = set()
    for za in union_t:
        fn = target_filename(za)
        if fn not in seen:
            seen.add(fn)
            role = "irdff" if za in irdff else "isomeric"
            jobs.append((role, fn))
    for (z, a), lisos in sorted(anchors.items()):
        for liso in sorted(lisos):
            fn = target_filename(z * 1000 + a, liso)
            if fn not in seen:
                seen.add(fn)
                jobs.append(("anchor", fn))
    # also the ground-state file of each anchor product (may be needed)
    for (z, a) in sorted(anchors):
        fn = target_filename(z * 1000 + a, 0)
        if fn not in seen:
            seen.add(fn)
            jobs.append(("anchor_ground", fn))

    WORK.mkdir(parents=True, exist_ok=True)
    print(f"P25c G3: {len(jobs)} files x 2 corpora", flush=True)
    patched_builds = build_set(PATCHED_ROOT, jobs, "patched")
    source_builds = build_set(SOURCE_ROOT, jobs, "source")

    # --- C. coverage accounting -----------------------------------------
    irdff_files = {fn for role, fn in jobs if role == "irdff"}
    union_files = {fn for role, fn in jobs if role in ("irdff", "isomeric")}
    anchor_files = {fn for role, fn in jobs if role.startswith("anchor")}

    def coverage(builds):
        return {
            "irdff_built": sum(1 for f in irdff_files
                             if builds.get(f, {}).get("class") == "ok"),
            "union_built": sum(1 for f in union_files
                             if builds.get(f, {}).get("class") == "ok"),
            "anchors_built": sum(1 for f in anchor_files
                               if builds.get(f, {}).get("class") == "ok"),
            "corpus_incomplete": sorted(
                f for f, b in builds.items()
                if b["class"] == "corpus_incomplete"),
        }

    cov = {
        "irdff_total": len(irdff_files),
        "union_total": len(union_files),
        "anchors_total": len(anchor_files),
        "patched": coverage(patched_builds),
        "source": coverage(source_builds),
    }
    recovered = sorted(
        f for f, b in patched_builds.items()
        if b["class"] == "ok"
        and source_builds.get(f, {}).get("class") != "ok"
    )
    newly_broken = sorted(
        f for f, b in patched_builds.items()
        if b["class"] != "ok"
        and source_builds.get(f, {}).get("class") == "ok"
    )

    record = {
        "schema": "actinv-p25c-g3-census-1",
        "gate": "P25c-G3",
        "seals_sha256": sha256(SEALS),
        "signature_sha256": sha256(SIGNATURE),
        "patch_sha256": sha256(PATCH),
        "builder": str(ACTINV),
        "builder_version": subprocess.run(
            [str(ACTINV), "--version"], capture_output=True,
            text=True).stdout.strip(),
        "per_file_bound_s": PER_FILE_BOUND_S,
        "rescan": rescan,
        "jobs": [{"role": r, "file": f} for r, f in jobs],
        "builds": {"patched": patched_builds, "source": source_builds},
        "coverage": cov,
        "recovered_files": recovered,
        "newly_broken_files": newly_broken,
        "elapsed_s": round(time.time() - t0, 3),
    }
    out_path = RESULTS / "g3_p25c_census.json"
    out_path.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"coverage": cov, "recovered": len(recovered),
                      "newly_broken": len(newly_broken),
                      "elapsed_s": record["elapsed_s"]}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
