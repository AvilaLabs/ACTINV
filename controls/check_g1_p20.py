#!/usr/bin/env python3
"""Independent reproduction of the P20 G1 coverage census.

Imports no ACTINV production or audit module. Two legs:

1. Aggregate re-derivation — every reported census count is recomputed from
   the committed report's own per-block and per-row detail.
2. Independent re-parse — a deterministic sample of the frozen corpus is
   walked by a from-scratch ENDF-6 MF=33 reader written here (section HEAD,
   NL subsections, NI LIST records; LB->kind mapping), and the per-file
   component/LB/kind/energy-range inventory must equal the census blocks
   byte-for-byte. Skipped only when the corpus is absent (CI).

With ``--self-test`` it mutates a copy of the census and proves rejection.
"""
from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import json
import math
from pathlib import Path
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "protocols/ACTINV-P20_PROTOCOL.md"
CENSUS = ROOT / "results/g0_p20_mf33_census.json.gz"
G0_CHECK = ROOT / "results/g0_p20_check.json"
OUTPUT = ROOT / "results/g1_p20_check.json"

PROTOCOL_SHA256 = "76c2ca2f646f85ea8c4a26f9ed5b2c1b3c49cfb3312a9122e546db4b53164335"
OPENING_COMMIT = "31d4dc3dda8588d1a2075cb1dceb809ace9c98b7"
SAMPLE_STRIDE = 29

# Frozen corpus location (mirrors controls/g4_p18b_diagnostics.py).
CORPUS_ROOT = Path.home() / "nuclear-data/tendl-2025/files/n"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def is_hex64(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def git(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=ROOT, text=True, capture_output=True, check=False
    )
    if completed.returncode:
        raise RuntimeError(f"git {' '.join(args)} failed: {completed.stderr.strip()}")
    return completed.stdout.strip()


def _float(text: str) -> float:
    try:
        return float(text)
    except ValueError:
        # ENDF Fortran format may omit the exponent letter (" 3.909400+4").
        for i in range(len(text) - 1, 0, -1):
            if text[i] in "+-":
                return float(text[:i] + "e" + text[i:])
        raise


def _fields(line: str):
    return (
        _float(line[0:11]),
        _float(line[11:22]),
        int(line[22:33]),
        int(line[33:44]),
        int(line[44:55]),
        int(line[55:66]),
    )


def _tail(line: str):
    return int(line[70:72]), int(line[72:75])


def _kind(lb: int) -> str:
    if lb <= 4:
        return "absolute"
    if lb in (5, 6):
        return "relative"
    if lb == 8:
        return "short_range_8"
    if lb == 9:
        return "short_range_9"
    return f"unsupported_{lb}"


def walk_mf33(path: Path):
    """Independent ENDF-6 MF=33 walker.

    Returns (za, liso, sections, components, per-(mt,mt1) block dict) where
    each block carries components, lb/kind multisets and row energy bounds.
    Only the structure needed to reproduce the census is decoded; F tables
    are skipped.
    """
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    za = int(_float(lines[1][0:11]))
    liso = int(_float(lines[2][33:44]))
    blocks: dict[tuple[int, int], dict] = {}
    sections = 0
    components = 0
    i = 0
    n = len(lines)
    while i < n:
        mf, mt = _tail(lines[i])
        if mf != 33 or mt <= 0:
            i += 1
            continue
        end = i
        while end < n and _tail(lines[end]) == (33, mt):
            end += 1
        sections += 1
        seg = lines[i:end]
        i = end
        _, _, l1, l2, n1, nl = _fields(seg[0])
        if l1 != 0 or l2 != 0 or n1 != 0:
            raise ValueError("MF=33 HEAD reserved fields nonzero")
        pos = 1
        for _ in range(nl):
            xmf1, xlfs1, mat1, mt1, nc, ni = _fields(seg[pos])
            pos += 1
            if nc != 0:
                raise ValueError("NC sub-subsection present")
            for _ in range(ni):
                _, _, _lt, lb, nt, np_ = _fields(seg[pos])
                pos += 1
                nvals = nt
                vals = []
                consumed = 0
                take = min(nvals, np_ if lb in (5, 6) else 2 * np_)
                while consumed < take:
                    for k in range(0, 66, 11):
                        chunk = seg[pos][k : k + 11].strip()
                        if chunk:
                            vals.append(_float(chunk))
                    consumed += 6
                    pos += 1
                pos += (nvals - consumed + 5) // 6
                if lb in (5, 6):
                    e_lo, e_hi = vals[0], vals[np_ - 1]
                else:
                    e_lo, e_hi = vals[0], vals[2 * (np_ - 1)]
                key = f"{mt},{mt1}"
                block = blocks.setdefault(
                    key,
                    {"components": 0, "lbs": {}, "kinds": {}, "lo": math.inf, "hi": -math.inf},
                )
                block["components"] += 1
                block["lbs"][str(lb)] = block["lbs"].get(str(lb), 0) + 1
                kind = _kind(lb)
                block["kinds"][kind] = block["kinds"].get(kind, 0) + 1
                block["lo"] = min(block["lo"], e_lo)
                block["hi"] = max(block["hi"], e_hi)
                components += 1
    return za, liso, sections, components, blocks


def rederive_aggregates(census: dict, failures: list[str]) -> None:
    body = census.get("census") or {}
    blocks = body.get("blocks") or {}
    derived = {
        "files_with_mf33": len({k.split(",")[0] + "," + k.split(",")[1] for k in blocks}),
        "mf33_sections": len({",".join(k.split(",")[:3]) for k in blocks}),
        "components": sum(b["components"] for b in blocks.values()),
        "lb_counts": {},
        "kind_counts": {},
    }
    for b in blocks.values():
        for lb, count in b["lbs"].items():
            derived["lb_counts"][lb] = derived["lb_counts"].get(lb, 0) + count
        for kind, count in b["kinds"].items():
            derived["kind_counts"][kind] = derived["kind_counts"].get(kind, 0) + count
    for field in ("files_with_mf33", "mf33_sections", "components"):
        if body.get(field) != derived[field]:
            failures.append(
                f"census {field} {body.get(field)} != re-derived {derived[field]}"
            )
    for field in ("lb_counts", "kind_counts"):
        if body.get(field) != derived[field]:
            failures.append(f"census {field} differs from re-derived multisets")
    overlap = census.get("energy_overlap") or {}
    rows = overlap.get("rows") or []
    covered = [r for r in rows if r.get("cov_present")]
    full = [r for r in covered if (r.get("overlap") or 0.0) >= 0.999]
    if overlap.get("self_block_rows") != len(rows):
        failures.append("self_block_rows != len(rows)")
    if overlap.get("cov_present_rows") != len(covered):
        failures.append("cov_present_rows != covered rows")
    if overlap.get("full_domain_cover_rows") != len(full):
        failures.append("full_domain_cover_rows != re-derived count")
    mean = sum(r["overlap"] for r in covered) / len(covered) if covered else None
    if mean is None or abs(overlap.get("mean_overlap") - mean) > 1e-12:
        failures.append("mean_overlap differs from re-derived value")
    core = census.get("core_case") or {}
    total, covered_mass = core.get("sensitivity_mass_total"), core.get(
        "sensitivity_mass_covered"
    )
    if (
        isinstance(total, (int, float))
        and isinstance(covered_mass, (int, float))
        and total
    ):
        if abs(covered_mass / total - core.get("sensitivity_mass_fraction", -1)) > 1e-12:
            failures.append("core sensitivity_mass_fraction inconsistent")
    else:
        failures.append("core case lacks sensitivity masses")


def reproduce_sample(census: dict, failures: list[str]) -> int:
    if not CORPUS_ROOT.exists():
        return 0
    blocks = (census.get("census") or {}).get("blocks") or {}
    files = sorted(p.name for p in CORPUS_ROOT.glob("*.tendl"))
    if (census.get("census") or {}).get("files") != len(files):
        failures.append(f"census files != corpus listing {len(files)}")
    checked = 0
    for index, name in enumerate(files):
        if index % SAMPLE_STRIDE != 0:
            continue
        checked += 1
        try:
            za, liso, sections, components, mine = walk_mf33(CORPUS_ROOT / name)
        except Exception as error:  # noqa: BLE001 - defect is a finding
            failures.append(f"independent walk of {name} failed: {error}")
            continue
        prefix = f"{za},{liso},"
        theirs = {k[len(prefix):]: v for k, v in blocks.items() if k.startswith(prefix)}
        if len({k.split(",")[0] for k in theirs}) != sections:
            failures.append(
                f"{name}: section count {len({k.split(',')[0] for k in theirs})} != {sections}"
            )
        if sum(b["components"] for b in theirs.values()) != components:
            failures.append(f"{name}: component count mismatch")
        for suffix, want in mine.items():
            got = theirs.get(suffix)
            if got is None:
                failures.append(f"{name}: census lacks block {prefix}{suffix}")
                continue
            if got["components"] != want["components"] or got["lbs"] != want["lbs"] or got["kinds"] != want["kinds"]:
                failures.append(f"{name} block {suffix}: component inventory differs")
            if abs(got["row_e_lo"] - want["lo"]) > 1e-9 * max(1.0, abs(want["lo"])) or abs(
                got["row_e_hi"] - want["hi"]
            ) > 1e-9 * max(1.0, abs(want["hi"])):
                failures.append(f"{name} block {suffix}: energy range differs")
    return checked


def check_census(census: dict, failures: list[str]) -> dict:
    if census.get("schema") != "actinv-p20-g0-census-1":
        failures.append("census schema is not actinv-p20-g0-census-1")
    if census.get("protocol_sha256") != PROTOCOL_SHA256:
        failures.append("census does not bind the frozen protocol hash")
    for field in ("probe_sha256", "covariance_index_sha256"):
        if not is_hex64(census.get(field)):
            failures.append(f"census lacks a valid {field}")
    rederive_aggregates(census, failures)
    sampled = reproduce_sample(census, failures)
    return {"independent_files_rechecked": sampled}


def run_checks() -> dict:
    failures: list[str] = []
    observed_protocol = sha256(PROTOCOL)
    if observed_protocol != PROTOCOL_SHA256:
        failures.append(f"protocol sha256 {observed_protocol} != frozen {PROTOCOL_SHA256}")
    head = git("rev-parse", "HEAD")
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", OPENING_COMMIT, "HEAD"],
        cwd=ROOT,
        check=False,
    ).returncode == 0
    if ancestor is False:
        failures.append(f"opening commit {OPENING_COMMIT} is not an ancestor of {head}")
    protocol_diff = git(
        "diff", f"{OPENING_COMMIT}..HEAD", "--", str(PROTOCOL.relative_to(ROOT))
    )
    if protocol_diff:
        failures.append("protocol changed after the opening commit")
    if not G0_CHECK.exists():
        failures.append("missing G0 check record")
    elif json.loads(G0_CHECK.read_text(encoding="utf-8")).get("pass") is not True:
        failures.append("G0 check record does not pass")
    sampled = 0
    if not CENSUS.exists():
        failures.append("MF=33 census inventory is missing")
    else:
        with gzip.open(CENSUS, "rt") as stream:
            sampled = check_census(json.load(stream), failures)[
                "independent_files_rechecked"
            ]
    return {
        "schema": "actinv-p20-g1-check-1",
        "protocol_sha256": observed_protocol,
        "head_commit": head,
        "independent_files_rechecked": sampled,
        "failures": failures,
        "pass": not failures,
    }


def self_test() -> None:
    with gzip.open(CENSUS, "rt") as stream:
        census = json.load(stream)
    mutations = {
        "component_count_flip": lambda v: v["census"].__setitem__("components", 1),
        "lb_count_flip": lambda v: v["census"]["lb_counts"].__setitem__("5", 1),
        "row_drop": lambda v: v["energy_overlap"]["rows"].pop(0),
        "mean_flip": lambda v: v["energy_overlap"].__setitem__("mean_overlap", 0.1),
        "fraction_flip": lambda v: v["core_case"].__setitem__(
            "sensitivity_mass_fraction", 0.99
        ),
    }
    rejected = []
    for name, mutate in mutations.items():
        candidate = copy.deepcopy(census)
        mutate(candidate)
        failures: list[str] = []
        rederive_aggregates(candidate, failures)
        rejected.append(bool(failures))
    if not all(rejected):
        raise SystemExit(f"self-test mutations not all rejected: {rejected}")
    print(f"self-test: all {len(mutations)} census mutations rejected")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    result = run_checks()
    if not args.no_write:
        OUTPUT.write_text(
            json.dumps(result, indent=1, sort_keys=True) + "\n", encoding="utf-8"
        )
    print(json.dumps(result, indent=1, sort_keys=True))
    raise SystemExit(0 if result["pass"] else 1)


if __name__ == "__main__":
    main()
