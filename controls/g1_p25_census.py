#!/usr/bin/env python3
"""P25-G1: complete cause census.

Enumerates every dropped, quarantined, zero-predicted or ineligible
outcome across all four projectile corpora into the frozen P25
taxonomy, and re-derives every quarantined file's failure message —
no reason may be carried forward as "quarantined in an earlier run".

Sources (all already public evidence):

- ``results/g5_p18b_heldout.json`` — the sealed 1,945-row ledger,
  1,859 eligible rows, per-row baseline/candidate outcomes;
- ``target/g4-p18b/failed-<projectile>/`` — the 397 quarantined
  evaluation files, re-built one file at a time through the unchanged
  P18b-era builder (``target/release/actinv``, builder unchanged since
  commit f66e45d) to capture each file's own failure message;
- each file's MF=8 declared product states vs MF=10 tabulated states —
  a static declaration scan that needs no build.

Per quarantined file the census records the failure line, parsed
magnitudes (MT, ZAP, group, emitted sum, runtime total, relative and
absolute excess) and a first-hit taxonomy class. Mechanism traces at
G2 assign final classes; the census's job is complete enumeration with
no silent drops.

The build loop is resumable: results accumulate in
``target/p25-census/failures-<projectile>.json`` and already-recorded
files are skipped. Writes ``results/g1_p25_census.json``.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
import subprocess
import time


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results/g1_p25_census.json"
HELDOUT = ROOT / "results/g5_p18b_heldout.json"
FAILED_DIR = ROOT / "target/g4-p18b"
WORK = ROOT / "target/p25-census"
ACTINV = ROOT / "target/release/actinv"

PARAMS = {
    "neutron": ("fispact-709", "293.6"),
    "proton": ("fispact-162", "0"),
    "deuteron": ("fispact-162", "0"),
    "alpha": ("fispact-162", "0"),
}
CODE = {"neutron": "n", "proton": "p", "deuteron": "d", "alpha": "a"}

SYMBOLS = (
    "H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn "
    "Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce "
    "Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn "
    "Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr Rf Db Sg Bh Hs Mt Ds Rg Cn Nh Fl "
    "Mc Lv Ts Og"
).split()

# Absolute excess at or below this magnitude has no physical weight for
# any held-out measurement (the observed floor-value artifact is
# 1e-20 barn carried by two co-equal floor states). Recorded, not
# asserted — G2 adjudicates final classes.
TINY_ABS_B = 1e-15

CONSERVATION_RE = re.compile(
    r"MT(\d+)/MF=(\d+) ZAP=(\d+) group (\d+): emitted state sum "
    r"([0-9.eE+-]+) barn exceeds runtime total ([0-9.eE+-]+) barn "
    r"by relative excess ([0-9.eE+-]+)"
)


def endf_num(field: str) -> float | None:
    f = field.strip()
    if not f:
        return None
    try:
        return float(re.sub(r"(?<=\d)([+-])(\d+)$", r"e\1\2", f))
    except ValueError:
        return None


def family_target_file(projectile: str, family_id: str) -> str | None:
    """`000-001|028-062|027-062|62Ni(n,p)62Co` -> `n-Ni062.tendl`."""
    try:
        target = family_id.split("|")[1]
        zt, at = int(target.split("-")[0]), int(target.split("-")[1])
        return f"{CODE[projectile]}-{SYMBOLS[zt - 1]}{at:03d}.tendl"
    except (IndexError, ValueError):
        return None


def mf8_declared(path: Path) -> dict[int, set[tuple[int, int]]]:
    """MF=8 sections: mt -> {(zap, lfs)} declared product states."""
    lines = path.read_bytes().decode("ascii", "replace").splitlines()
    out: dict[int, set[tuple[int, int]]] = {}
    i = 0
    while i < len(lines):
        ln = lines[i]
        if len(ln) >= 75:
            try:
                mf = int(ln[70:72]); mt = int(ln[72:75])
            except ValueError:
                i += 1
                continue
            if mf == 8 and mt != 0:
                nsp = int(endf_num(ln[44:55]) or 0)
                j = i + 1
                prods: set[tuple[int, int]] = set()
                while j < len(lines) and len(prods) < nsp:
                    l2 = lines[j]
                    try:
                        mf2 = int(l2[70:72]); mt2 = int(l2[72:75])
                    except (ValueError, IndexError):
                        j += 1
                        continue
                    if mf2 != 8 or mt2 != mt:
                        break
                    zap = endf_num(l2[0:11]); lfs = endf_num(l2[33:44])
                    if zap is not None:
                        prods.add((int(zap), int(lfs or 0)))
                    j += 1
                out[mt] = prods
                i = j
                continue
        i += 1
    return out


def mf10_tabulated(path: Path) -> dict[int, set[tuple[int, int]]]:
    """MF=10 sections: mt -> {(zap, lfs)} tabulated state records.

    A state-record header is a line whose field-3 is a ZAP (>=1000) and
    field-6 is a point count NP, *immediately followed* by a TAB1
    interpolation line whose first field equals NP — this distinguishes
    headers from (E, sigma) data lines inside the same MF=10 section.
    """
    lines = path.read_bytes().decode("ascii", "replace").splitlines()
    out: dict[int, set[tuple[int, int]]] = {}
    for i, ln in enumerate(lines[:-1]):
        if len(ln) < 75:
            continue
        try:
            mf = int(ln[70:72]); mt = int(ln[72:75])
        except ValueError:
            continue
        if mf != 10 or mt == 0:
            continue
        zap = endf_num(ln[22:33]); lfs = endf_num(ln[33:44])
        np_ = endf_num(ln[55:66])
        if zap is None or np_ is None or zap < 1000:
            continue
        if np_ < 1 or np_ != int(np_):
            continue
        nxt = lines[i + 1]
        try:
            mf2 = int(nxt[70:72]); mt2 = int(nxt[72:75])
        except (ValueError, IndexError):
            continue
        if mf2 != 10 or mt2 != mt:
            continue
        if endf_num(nxt[0:11]) != np_:
            continue
        out.setdefault(mt, set()).add((int(zap), int(lfs or 0)))
    return out


INELASTIC_MTS = {4, *range(51, 92)}
DATA = Path("/home/connoravila/nuclear-data/tendl-2025/files")
CORPUS_DIR = {
    "neutron": DATA / "n-working", "proton": DATA / "p",
    "deuteron": DATA / "d", "alpha": DATA / "a",
}


def target_za(filename: str) -> int | None:
    m = re.match(r"[npda]-([A-Z][a-z]?)(\d+)([mn]?)", filename)
    if not m or m.group(1) not in SYMBOLS:
        return None
    return (SYMBOLS.index(m.group(1)) + 1) * 1000 + int(m.group(2))


def inelastic_residual_scan() -> dict:
    """Corpus-wide: for every MF=8 declaration at an MT the builder's
    `inelastic()` treats as same-residual (4, 51-91), does the declared
    product ZAP equal the target ZA?  A different ZAP means the MT is
    a particle-emission channel to another nuclide, and same-residual
    handling would be a processing bug."""
    out: dict[str, dict] = {}
    for projectile, cdir in CORPUS_DIR.items():
        same = diff = 0
        diff_examples, same_examples = [], []
        mts_seen: set[int] = set()
        for source in sorted(cdir.glob("*.tendl")):
            tgt = target_za(source.name)
            if tgt is None:
                continue
            decl = mf8_declared(source)
            inel = {mt: s for mt, s in decl.items() if mt in INELASTIC_MTS}
            if not inel:
                continue
            mts_seen |= set(inel)
            zaps = {zp for states in inel.values() for zp, _ in states}
            if zaps == {tgt}:
                same += 1
                if len(same_examples) < 5:
                    same_examples.append(source.name)
            else:
                diff += 1
                if len(diff_examples) < 5:
                    diff_examples.append({
                        "file": source.name, "target_za": tgt,
                        "declared_zaps": sorted(zaps),
                        "mts": sorted(inel),
                    })
        out[projectile] = {
            "files_with_inelastic_mt_mf8": same + diff,
            "same_residual": same, "different_residual": diff,
            "inelastic_mts_present": sorted(mts_seen),
            "same_examples": same_examples,
            "different_examples": diff_examples,
        }
    return out


def classify_failure(message: str) -> dict:
    m = CONSERVATION_RE.search(message)
    if m:
        mt, lmf, zap, group = (int(m.group(k)) for k in range(1, 5))
        emitted, total = float(m.group(5)), float(m.group(6))
        rel = float(m.group(7))
        abs_excess = emitted - total
        cls = (
            "tiny_absolute_discrepancy"
            if abs_excess <= TINY_ABS_B
            else "conservation_excess_untraced"
        )
        return {
            "class": cls,
            "mt": mt, "lmf": lmf, "zap": zap, "group": group,
            "emitted_sum_barn": emitted, "runtime_total_barn": total,
            "relative_excess": rel, "absolute_excess_barn": abs_excess,
        }
    if "conflicting duplicate MF=8" in message:
        return {"class": "state_catalog_mapping"}
    if "conflicts with QM-QI" in message:
        # MF=8 ELFS disagrees with the Q-value-implied excitation —
        # the LFS→ELFS identity chain is internally inconsistent in the
        # source evaluation (P18's MF8-vs-Q conflict class).
        return {"class": "state_catalog_conflict"}
    if "invalid negative product" in message:
        return {"class": "genuine_source_inconsistency"}
    if "nonfinite or negative" in message:
        return {"class": "genuine_source_inconsistency"}
    return {"class": "unclassified"}


def rederive_failures(projectile: str) -> dict:
    """Re-run each quarantined file through the unchanged builder."""
    failed_dir = FAILED_DIR / f"failed-{projectile}"
    cache_out = WORK / f"failures-{projectile}.json"
    cache_out.parent.mkdir(parents=True, exist_ok=True)
    prior = json.loads(cache_out.read_text()) if cache_out.exists() else {}
    groups, temperature = PARAMS[projectile]
    cache_dir = WORK / f"cache-{projectile}"
    cache_dir.mkdir(exist_ok=True)
    files = sorted(failed_dir.glob("*.tendl"))
    done = 0
    for source in files:
        if source.name in prior:
            done += 1
            continue
        scratch = WORK / f"scratch-{projectile}-{source.stem}.npz"
        started = time.monotonic()
        try:
            completed = subprocess.run(
                [
                    str(ACTINV), "build-library", str(source), str(scratch),
                    "--format", "tendl", "--projectile", projectile,
                    "--groups", groups, "--temperature-K", temperature,
                    "--workers", "2", "--cache", str(cache_dir),
                ],
                cwd=ROOT, text=True, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, timeout=900,
            )
            line = (
                completed.stdout.strip().splitlines()[-1]
                if completed.stdout.strip()
                else f"<exit {completed.returncode} with no output>"
            )
            entry = {
                "exit": completed.returncode,
                "message": line[:600],
                "elapsed_s": round(time.monotonic() - started, 3),
            }
            entry.update(classify_failure(line))
        except subprocess.TimeoutExpired:
            entry = {
                "exit": -1, "message": "<timeout 900s>",
                "elapsed_s": 900.0, "class": "unclassified",
            }
        prior[source.name] = entry
        done += 1
        cache_out.write_text(json.dumps(prior, indent=1, sort_keys=True))
        scratch.unlink(missing_ok=True)
        print(f"{projectile} [{done}/{len(files)}] {source.name}: "
              f"{entry.get('class', '?')}", flush=True)
    return prior


def staging_status(projectile: str, filename: str | None) -> str:
    """Why a construction-failed row's file has no build: quarantined
    (staged, failed, moved to failed-*), never staged (in the corpus but
    absent from both inputs-* and failed-* — the P18b staging omission),
    or absent from the sealed corpus entirely."""
    if filename is None:
        return "unmappable_family"
    if (FAILED_DIR / f"failed-{projectile}" / filename).exists():
        return "quarantined"
    if (FAILED_DIR / f"inputs-{projectile}" / filename).exists():
        return "staged_built"  # staged and not quarantined — should not happen for a failed row
    if (CORPUS_DIR[projectile] / filename).exists():
        return "never_staged"
    return "no_source_evaluation"


def row_outcome(row: dict) -> tuple[str, str | None]:
    """One named outcome per ledger row — nothing may silently drop."""
    if row["status"] != "eligible":
        return f"eligibility:{row['status']}", None
    cand = row.get("candidate") or {}
    status = cand.get("status")
    if status == "scored":
        cm = cand.get("cm")
        ln = cand.get("ln_cm")
        if cm == 0 or (ln is not None and not math.isfinite(ln)):
            return "zero_prediction_scored", None
        return "scored", None
    if status == "build_failed_g3":
        fname = family_target_file(row["projectile"], row["family_id"])
        return f"construction_failed:{staging_status(row['projectile'], fname)}", fname
    if status is None:
        return "construction_failed:unbuilt", None
    return f"undefined_ratio:{status}", None


def main() -> None:
    heldout = json.loads(HELDOUT.read_text(encoding="utf-8"))
    ledger = heldout["ledger"]

    # ---- row-level outcome census ------------------------------------
    outcomes: dict[str, dict[str, int]] = {}
    file_rows: dict[str, dict[str, int]] = {}
    zero_rows: list[str] = []
    for row in ledger:
        outcome, fname = row_outcome(row)
        proj = row["projectile"]
        outcomes.setdefault(proj, {})[outcome] = (
            outcomes.setdefault(proj, {}).get(outcome, 0) + 1
        )
        if fname:
            file_rows.setdefault(fname, {})[proj] = (
                file_rows.setdefault(fname, {}).get(proj, 0) + 1
            )
        if outcome == "zero_prediction_scored":
            zero_rows.append(row["row_id"])

    # ---- per-file failure re-derivation (resumable) -------------------
    failures: dict[str, dict] = {}
    for projectile in PARAMS:
        failures[projectile] = rederive_failures(projectile)

    # ---- static MF=8/MF=10 declaration scan on quarantined files -----
    declaration_scan: dict[str, dict] = {}
    for projectile in PARAMS:
        for source in sorted(
            (FAILED_DIR / f"failed-{projectile}").glob("*.tendl")
        ):
            declared = mf8_declared(source)
            tabulated = mf10_tabulated(source)
            mismatches = []
            for mt, states in tabulated.items():
                decl = declared.get(mt, set())
                missing = states - decl
                if missing:
                    mismatches.append({
                        "mt": mt,
                        "tabulated_not_declared": sorted(map(list, missing)),
                    })
            declaration_scan[source.name] = {
                "projectile": projectile,
                "mf8_declared_states": sum(len(v) for v in declared.values()),
                "mf10_tabulated_states": sum(len(v) for v in tabulated.values()),
                "mf10_not_in_mf8": mismatches,
            }

    # ---- corpus-wide inelastic-residual identity scan -----------------
    inelastic_scan = inelastic_residual_scan()

    # ---- taxonomy rollup ----------------------------------------------
    # Reclassify from stored messages at assembly so classifier fixes do
    # not require re-running builds (the cached entries persist).
    class_counts: dict[str, dict[str, int]] = {}
    for projectile, files in failures.items():
        counts: dict[str, int] = {}
        for name, entry in files.items():
            entry.update(classify_failure(entry["message"]))
            counts[entry.get("class", "unclassified")] = (
                counts.get(entry.get("class", "unclassified"), 0) + 1
            )
        class_counts[projectile] = counts

    record = {
        "schema": "actinv-p25-census-1",
        "gate": "P25-G1",
        "row_outcomes": outcomes,
        "zero_prediction_rows": zero_rows,
        "construction_failed_files": file_rows,
        "file_failures": {
            p: {n: e for n, e in files.items()}
            for p, files in failures.items()
        },
        "failure_class_counts": class_counts,
        "declaration_scan": declaration_scan,
        "inelastic_residual_scan": inelastic_scan,
        "taxonomy": {
            "classes": [
                "processing_bug", "state_catalog_mapping",
                "state_catalog_conflict",
                "tiny_absolute_discrepancy", "genuine_source_inconsistency",
                "conservation_excess_untraced", "zero_prediction_scored",
                "eligibility", "unclassified",
            ],
            "tiny_absolute_threshold_barn": TINY_ABS_B,
        },
        "sealed_counts_reference": heldout["counts"],
        "pass": None,  # census is evidence; G3 assigns repairability
    }
    RESULT.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "row_outcomes": outcomes,
        "failure_class_counts": class_counts,
        "quarantined_files": {p: len(f) for p, f in failures.items()},
        "inelastic_residual_scan": {
            p: {k: v[k] for k in ("same_residual", "different_residual")}
            for p, v in inelastic_scan.items()
        },
    }, indent=2))


if __name__ == "__main__":
    main()
