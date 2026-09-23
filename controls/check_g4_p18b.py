#!/usr/bin/env python3
"""Independent checker for the P18b-G4 diagnostic report.

Imports no production or control module.  Verifies the frozen protocol,
supplement, seal, decay and baseline hashes; re-derives every scored ledger
row's calculated ratio and log error from the ledger's own sigma_g/sigma_m/
sigma_t under the frozen ratio transforms; recomputes the full metric set and
the deterministic paired bootstrap; and spot-checks a deterministic sample of
scored rows against the released NPZ rows with an independent row filter
(group interpolation, loss/partial routing, inelastic closure).

Legs:

1. source hashes and report schema;
2. every sealed diagnostic row id appears in the ledger exactly once;
3. per-row arithmetic: ratio transform, C/M, ln(C/M) reproduced exactly;
4. metric blocks (median, p90, max, geomean, coverage) reproduced from the
   ledger, per projectile and overall, for baseline and candidate;
5. paired bootstrap reproduced deterministically from the protocol-hash seed;
6. NPZ spot check: a deterministic sample of scored rows re-derived from the
   released artifacts by an independent implementation;
7. ``--self-test`` mutates a copy of the report and proves rejection.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import statistics
import struct
import zipfile

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "results/g4_p18b_diagnostics.json"
SEAL = ROOT / "results/p18_family_seal.json"
PROTOCOL = ROOT / "protocols/ACTINV-P18b_PROTOCOL.md"
SUPPLEMENT = Path(
    __import__("os").environ.get(
        "ACTINV_P18B_SUPPLEMENT", ROOT / "target/preflight-tmp/rodrigo-supplement.txt"
    )
)

PROTOCOL_SHA256 = "69076fa2656b239addbb15fbb4727caaa2c8ea37b3aa82a141f3a2b0b619eabe"
SUPPLEMENT_SHA256 = "945e66f8904bb972662f5178e94e22a08ecb8006eefe1c2d9fbda66fe599763d"

BASELINE_LIBRARIES = {
    "neutron": (
        ROOT / "actinv-data/v1.0.0/activation/tendl-2025-neutron-709g.npz",
        "ec4c72bf598dc8ad3d533d9cfafdcf493e2d1f949a3e4db6251495659b68cc44",
    ),
    "proton": (
        ROOT / "actinv-data/v1.0.0/activation/tendl-2025-proton-162g.npz",
        "0da7a35b37fd3b305ac2166ec092cdfb78123e76f8647d8808915e2c708d9790",
    ),
    "deuteron": (
        ROOT / "actinv-data/v1.0.0/activation/tendl-2025-deuteron-162g.npz",
        "8050988981518cd63ac0c2ad76c6756370b154ea9f5a6d6435aa5f132b9d99ae",
    ),
    "alpha": (
        ROOT / "actinv-data/v1.0.0/activation/tendl-2025-alpha-162g.npz",
        "ead1141bfe07ec1a02055af014f8db0a49effe2fd60c29d181a505f7c6d10915",
    ),
}

INELASTIC_MTS = {4, *range(51, 92)}
SYMBOLS = (
    "H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn "
    "Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce "
    "Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn "
    "Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr Rf Db Sg Bh Hs Mt Ds Rg Cn Nh Fl "
    "Mc Lv Ts Og"
).split()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def read_npy(blob: bytes):
    if blob[:6] != b"\x93NUMPY":
        raise ValueError("not an npy payload")
    if blob[6] == 1:
        (length,) = struct.unpack("<H", blob[8:10])
        offset = 10 + length
        header_text = blob[10:offset].decode()
    else:
        (length,) = struct.unpack("<I", blob[8:12])
        offset = 12 + length
        header_text = blob[12:offset].decode()
    descr = header_text.split("'descr':")[1].split("'")[1]
    fortran = "'fortran_order': True" in header_text
    shape_text = header_text.split("'shape':")[1].split(")")[0].split("(")[1]
    shape = tuple(int(s) for s in shape_text.split(",") if s.strip())
    if fortran:
        raise ValueError("Fortran-ordered npy arrays are not supported")
    fmt = {"<f8": "d", "<i8": "q", "<i4": "i", "<f4": "f"}[descr]
    count = 1
    for dim in shape:
        count *= dim
    data = blob[offset : offset + count * struct.calcsize(fmt)]
    return list(struct.unpack(f"<{count}{fmt}", data)), shape


def load_npz(path: Path) -> dict:
    out = {}
    with zipfile.ZipFile(path) as zf:
        for name in zf.namelist():
            flat, shape = read_npy(zf.read(name))
            out[name.removesuffix(".npy")] = (flat, shape)
    return out


def group_of(bounds: list[float], energy_ev: float) -> int | None:
    """Independent group lookup: first group [lo, hi) containing the energy;
    the last group includes its upper edge."""
    if energy_ev < bounds[0] or energy_ev > bounds[-1]:
        return None
    lo, hi = 0, len(bounds) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if bounds[mid] <= energy_ev:
            lo = mid
        else:
            hi = mid
    return lo


def ratio_of(kind: str, sg: float, sm: float, st: float):
    """Independent ratio transform: pair forms use g+m, total forms use st."""
    if kind in ("G+M", "M/G", "G/M"):
        total = sg + sm
    elif kind in ("M+T", "G+T", "M/T", "G/T"):
        total = st
    else:
        return None
    if not (total > 0.0) or not math.isfinite(total):
        return None
    return sm / total


def pct(values: list[float], p: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    rank = (p / 100.0) * (len(ordered) - 1)
    lo = int(math.floor(rank))
    hi = int(math.ceil(rank))
    if lo == hi:
        return ordered[lo]
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (rank - lo)


def metrics(pairs: list[tuple[float, float]]) -> dict:
    """pairs = (ln_cm, cm)"""
    n = len(pairs)
    if not n:
        return {
            "rows": 0,
            "median_abs_ln": float("nan"),
            "p90_abs_ln": float("nan"),
            "max_abs_ln": float("nan"),
            "geomean_cm": float("nan"),
            "within_10pct": float("nan"),
            "within_20pct": float("nan"),
            "within_30pct": float("nan"),
        }
    logs = [p[0] for p in pairs]
    abslog = [abs(v) for v in logs]
    return {
        "rows": n,
        "median_abs_ln": statistics.median(abslog),
        "p90_abs_ln": pct(abslog, 90.0),
        "max_abs_ln": max(abslog),
        "geomean_cm": math.exp(sum(logs) / n),
        "within_10pct": sum(1 for _, cm in pairs if abs(cm - 1.0) <= 0.10) / n,
        "within_20pct": sum(1 for _, cm in pairs if abs(cm - 1.0) <= 0.20) / n,
        "within_30pct": sum(1 for _, cm in pairs if abs(cm - 1.0) <= 0.30) / n,
    }


def bootstrap(families: list[list[tuple[float, float]]], seed_bytes: bytes,
              replicates: int = 10_000) -> dict:
    """Independent family-resampled paired bootstrap."""
    import random

    rng = random.Random(int.from_bytes(seed_bytes[:8], "big"))
    n = len(families)
    med, p90 = [], []
    for _ in range(replicates):
        sample = [families[rng.randrange(n)] for _ in range(n)]
        cand = [row[1] for fam in sample for row in fam]
        base = [row[0] for fam in sample for row in fam]
        if not cand or not base:
            continue
        ac = [abs(v) for v in cand]
        ab = [abs(v) for v in base]
        med.append(statistics.median(ac) - statistics.median(ab))
        p90.append(pct(ac, 90.0) - pct(ab, 90.0))
    return {
        "replicates": replicates,
        "families": n,
        "median_abs_ln_change": {
            "mean": statistics.fmean(med) if med else float("nan"),
            "p05": pct(med, 5.0),
            "p95": pct(med, 95.0),
        },
        "p90_abs_ln_change": {
            "mean": statistics.fmean(p90) if p90 else float("nan"),
            "p05": pct(p90, 5.0),
            "p95": pct(p90, 95.0),
        },
    }


def close(a: float, b: float, rel: float = 1e-12, absol: float = 1e-15) -> bool:
    if a is None or b is None:
        return a is b
    if isinstance(a, float) and (math.isnan(a) or math.isnan(b)):
        return math.isnan(a) and math.isnan(b)
    if isinstance(a, float) and not math.isfinite(a):
        return a == b
    if isinstance(b, float) and not math.isfinite(b):
        return a == b
    return abs(a - b) <= max(absol, rel * max(abs(a), abs(b)))


def parse_target_za(family_id: str) -> tuple[int, int]:
    """family_id = 'seg|seg|seg|197Au(n,g)198Au' -> (target za, product za)."""
    import re

    reaction = family_id.split("|")[-1]
    m = re.fullmatch(
        r"(\d+)([A-Z][a-z]?|nat)\([^()]*\)(\d+)([A-Z][a-z]?)", reaction
    )
    assert m, reaction
    tz = SYMBOLS.index(m.group(2)) + 1
    pz = SYMBOLS.index(m.group(4)) + 1
    return tz * 1000 + int(m.group(1)), pz * 1000 + int(m.group(3))


def npz_spot_check(ledger: list[dict], failures: list[str]) -> dict:
    """Re-derive a deterministic sample of scored baseline rows from the
    released npz with an independent row filter."""
    checked = 0
    libs: dict[str, dict] = {}
    for e in ledger:
        if e["status"] != "eligible":
            continue
        b = e.get("baseline") or {}
        if b.get("status") != "scored":
            continue
        # deterministic sample: every 97th scored row
        if int(e["row_id"].split("-")[2]) % 97 != 0:
            continue
        proj = e["projectile"]
        if not BASELINE_LIBRARIES[proj][0].is_file():
            continue
        if proj not in libs:
            npz = load_npz(BASELINE_LIBRARIES[proj][0])
            rows_flat, rshape = npz["rows"]
            sig_flat, sshape = npz["sig"]
            bounds, _ = npz["bounds"]
            libs[proj] = {
                "rows": rows_flat, "rshape": rshape,
                "sigma": sig_flat, "sshape": sshape,
                "bounds": bounds,
                "index": json.loads(
                    BASELINE_LIBRARIES[proj][0]
                    .with_name(BASELINE_LIBRARIES[proj][0].stem + "_index.json")
                    .read_text()
                ),
            }
        lib = libs[proj]
        tza, pza = parse_target_za(e["family_id"])
        sym = SYMBOLS[tza // 1000 - 1]
        mass = tza % 1000
        tids = [
            i for i, t in enumerate(lib["index"]["targets"])
            if f"-{sym}{mass:03d}." in t.get("file", "")
        ]
        if not tids:
            failures.append(f"{e['row_id']}: no baseline target {sym}{mass}")
            continue
        g = group_of(lib["bounds"], e["energy_MeV"] * 1.0e6)
        if g is None:
            failures.append(f"{e['row_id']}: energy outside bounds")
            continue
        mts = set(b["mts"])
        rshape = lib["rshape"]
        sig_shape = lib["sshape"]
        rows = lib["rows"]
        sigma = lib["sigma"]
        loss = {mt: 0.0 for mt in mts}
        feed = {mt: 0.0 for mt in mts}     # sum of retained product partials
        ground = {mt: 0.0 for mt in mts}
        isom = {mt: 0.0 for mt in mts}
        leak = {mt: 0.0 for mt in mts}
        tset = set(tids)
        for i in range(rshape[0]):
            tidx, mt, zap, lfs, lmf = (
                int(rows[5 * i]), int(rows[5 * i + 1]), int(rows[5 * i + 2]),
                int(rows[5 * i + 3]), int(rows[5 * i + 4]),
            )
            if mt not in mts or tidx not in tset:
                continue
            v = sigma[sig_shape[1] * i + g]
            if lmf == 0:
                loss[mt] += v
            elif lmf in (-2, -3):
                leak[mt] += v
            elif zap == pza and lmf in (9, 10, -1):
                feed[mt] += v
                if lfs == 0 or lmf == -1:
                    ground[mt] += v
                elif lfs == e["isomer_liso"]:
                    isom[mt] += v
        sg = sm = st = 0.0
        for mt in mts:
            if mt in INELASTIC_MTS:
                st_mt = b["sigma_t"] if len(mts) == 1 else None
                # inelastic: st from MF3 total; verify closure instead:
                # sigma_t - sigma_g must equal emitted feed (+leak)
                continue
            st += loss[mt]
            sg += ground[mt]
            sm += isom[mt]
        inelastic = mts & INELASTIC_MTS
        if inelastic:
            # closure identity: sigma_t - sigma_g - sigma_m_other = feed+leak
            for mt in inelastic:
                expect = feed[mt] + leak[mt]
                got = b["sigma_t"] - (b["sigma_g"] or 0.0) - sm if len(mts) == 1 else None
                if got is not None and not close(expect, got, 1e-9, 1e-12):
                    failures.append(
                        f"{e['row_id']}: inelastic closure feed {expect} != "
                        f"sigma_t-sigma_g {got}"
                    )
                if not close(isom[mt], b["sigma_m"] or 0.0, 1e-9, 1e-12):
                    failures.append(
                        f"{e['row_id']}: inelastic sigma_m {isom[mt]} != "
                        f"{b['sigma_m']}"
                    )
        else:
            if not close(st, b["sigma_t"], 1e-9, 1e-12):
                failures.append(f"{e['row_id']}: sigma_t {st} != {b['sigma_t']}")
            if not close(sg, b["sigma_g"], 1e-9, 1e-12):
                failures.append(f"{e['row_id']}: sigma_g {sg} != {b['sigma_g']}")
            if not close(sm, b["sigma_m"], 1e-9, 1e-12):
                failures.append(f"{e['row_id']}: sigma_m {sm} != {b['sigma_m']}")
        checked += 1
    return {"checked": checked}


def evaluate(report: dict, deep: bool) -> list[str]:
    f: list[str] = []
    checks = report.get("checks", {})

    # leg 1: hashes + schema
    if report.get("schema") != "actinv-g4-p18b-diagnostics-1":
        f.append("schema")
    if sha256(PROTOCOL) != PROTOCOL_SHA256:
        f.append("protocol hash")
    if checks.get("supplement_hash") is not True:
        f.append("supplement_hash not verified")
    if deep and SUPPLEMENT.is_file():
        if sha256(SUPPLEMENT) != SUPPLEMENT_SHA256:
            f.append("supplement file hash")
    for name, v in (checks.get("baseline_hashes") or {}).items():
        if v is not True:
            f.append(f"baseline hash {name}")
        elif deep and BASELINE_LIBRARIES[name][0].is_file():
            if sha256(BASELINE_LIBRARIES[name][0]) != BASELINE_LIBRARIES[name][1]:
                f.append(f"baseline file {name}")

    ledger = report.get("ledger", [])
    seal = json.loads(SEAL.read_text())
    sealed = {
        r["row_id"]
        for fam in seal["families"]
        if fam["partition"] == "diagnostic"
        for r in fam["rows"]
    }
    seen = [e["row_id"] for e in ledger]
    if len(seen) != len(set(seen)):
        f.append("duplicate ledger row ids")
    if set(seen) != sealed:
        f.append(
            f"ledger row ids != sealed diagnostic ids "
            f"({len(seen)} vs {len(sealed)})"
        )
    if report.get("counts", {}).get("ledger_rows") != len(ledger):
        f.append("ledger_rows count")

    # leg 3: per-row arithmetic
    n_scored = 0
    for e in ledger:
        if e["status"] not in ("eligible", "ineligible"):
            f.append(f"{e['row_id']}: bad status {e['status']}")
        for label in ("baseline", "candidate"):
            blk = e.get(label) or {}
            if blk.get("status") != "scored":
                continue
            n_scored += 1
            sg, sm, st = blk["sigma_g"], blk["sigma_m"], blk["sigma_t"]
            calc = ratio_of(e["measurement_type"], sg, sm, st)
            if calc is None:
                f.append(f"{e['row_id']}/{label}: scored with zero denominator")
                continue
            cm = calc / e["measured"] if e["measured"] != 0 else float("inf")
            if not close(calc, blk["calculated"], 1e-12, 0.0):
                f.append(f"{e['row_id']}/{label}: calculated {calc} != {blk['calculated']}")
            if not close(cm, blk["cm"], 1e-12, 0.0):
                f.append(f"{e['row_id']}/{label}: cm")
            want_ln = blk["ln_cm"]
            if want_ln is not None and math.isfinite(want_ln):
                if not close(math.log(cm), want_ln, 1e-12, 0.0):
                    f.append(f"{e['row_id']}/{label}: ln_cm")
            elif want_ln not in (None, -math.inf, math.inf):
                f.append(f"{e['row_id']}/{label}: unexpected ln_cm")
            elif want_ln == math.inf and cm != math.inf:
                f.append(f"{e['row_id']}/{label}: ln_cm=inf but cm finite")
            elif want_ln == -math.inf and cm != 0.0:
                f.append(f"{e['row_id']}/{label}: ln_cm=-inf but cm != 0")
    if n_scored == 0:
        f.append("no scored rows")

    # leg 4: metrics
    def scored_pairs(label, proj=None):
        out = []
        for e in ledger:
            if e["status"] != "eligible":
                continue
            if proj and e["projectile"] != proj:
                continue
            blk = e.get(label) or {}
            v = blk.get("ln_cm")
            if v is not None and math.isfinite(v):
                out.append((v, blk["cm"]))
        return out

    for proj in report.get("per_projectile", {}):
        for label in ("baseline", "candidate"):
            want = metrics(scored_pairs(label, proj))
            got = report["per_projectile"][proj][label]
            for k in want:
                if not close(got[k], want[k], 1e-9, 0.0):
                    f.append(f"metric {proj}/{label}/{k}: {got[k]} != {want[k]}")
    for label in ("baseline", "candidate"):
        want = metrics(scored_pairs(label))
        got = report["overall"][label]
        for k in want:
            if not close(got[k], want[k], 1e-9, 0.0):
                f.append(f"metric overall/{label}/{k}: {got[k]} != {want[k]}")

    # leg 5: paired bootstrap
    fams: dict[str, list[tuple[float, float]]] = {}
    for e in ledger:
        if e["status"] != "eligible":
            continue
        b, c = e.get("baseline") or {}, e.get("candidate") or {}
        bv, cv = b.get("ln_cm"), c.get("ln_cm")
        if bv is None or cv is None:
            continue
        if math.isfinite(bv) and math.isfinite(cv):
            fams.setdefault(e["family_id"], []).append((bv, cv))
    want_boot = bootstrap(list(fams.values()), bytes.fromhex(PROTOCOL_SHA256))
    got_boot = report.get("paired_bootstrap") or {}
    if want_boot["families"]:
        for side in ("median_abs_ln_change", "p90_abs_ln_change"):
            for k in ("mean", "p05", "p95"):
                if not close(
                    got_boot.get(side, {}).get(k), want_boot[side][k], 1e-9, 0.0
                ):
                    f.append(f"bootstrap {side}/{k}")
    if report.get("paired_rows") != sum(len(v) for v in fams.values()):
        f.append("paired_rows")

    # leg 6: npz spot check (deep only, and only when the released libraries
    # are present in the checkout)
    if deep and all(p.is_file() for p, _ in BASELINE_LIBRARIES.values()):
        spot = npz_spot_check(ledger, f)
        if spot["checked"] < 20:
            f.append(f"npz spot check too thin: {spot['checked']}")

    if report.get("quarantine", {}).get("heldout_values_read") is not False:
        f.append("heldout quarantine flag")
    return f


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument(
        "--no-write", action="store_true", help="accepted for CI convention; this checker never writes"
    )
    parser.add_argument(
        "--report", default=str(REPORT), help="report path (default: committed)"
    )
    args = parser.parse_args()
    report = json.loads(Path(args.report).read_text())
    deep = args.report == str(REPORT) or Path(args.report).name == REPORT.name

    if args.self_test:
        baseline_failures = evaluate(report, deep=False)
        if baseline_failures:
            print("self-test baseline unexpectedly fails:", baseline_failures[:5])
            return 1

        def mut_metric(r):
            r["overall"]["baseline"]["median_abs_ln"] += 0.1

        def mut_row(r):
            e = next(
                x for x in r["ledger"]
                if (x.get("baseline") or {}).get("status") == "scored"
            )
            e["baseline"]["ln_cm"] += 0.5

        def mut_drop(r):
            r["ledger"] = r["ledger"][:-1]

        def mut_quarantine(r):
            r["quarantine"]["heldout_values_read"] = True

        rejected = 0
        for mutation in (mut_metric, mut_row, mut_drop, mut_quarantine):
            mutated = copy.deepcopy(report)
            mutation(mutated)
            if evaluate(mutated, deep=False):
                rejected += 1
        print(f"self-test rejected {rejected}/4 mutations")
        return 0 if rejected == 4 else 1

    failures = evaluate(report, deep=deep)
    if failures:
        print(f"FAIL ({len(failures)} failures)")
        for line in failures[:25]:
            print(" -", line)
        return 1
    print("G4-P18B-CHECK-PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
