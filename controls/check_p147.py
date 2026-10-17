#!/usr/bin/env python3
"""P147: mixed packages of discrete items and encapsulation (NRC concentration-averaging BTP). Evidence gates E1 to E5, the
local half of E6 and the verdict. Each gate writes results/p147/<gate>.json with a boolean `pass` and the candidate's
provenance. Children (`actinv waste package`, `actinv waste`, `actinv run`) run serially with a timeout, one per call, in a
staging folder under target/p147 (the activation runs also in a folder that links `actinv-data` to the data tree: P147_DATA,
then P146_DATA, P145_DATA, default ./actinv-data). Launch inside the required systemd cgroup; the children inherit that scope.

    python3 controls/check_p147.py e1                   # independent recomputation: synthetic battery + activation packages
    python3 controls/check_p147.py e2                   # bundled BTP data and the BTP examples against the frozen review
    python3 controls/check_p147.py e3                   # below, at and above every threshold
    python3 controls/check_p147.py e4                   # reductions to `actinv waste`, split pieces, averaged items
    python3 controls/check_p147.py e5                   # refusals, no output file, byte-identical repeat
    python3 controls/check_p147.py e6-p103              # the P103 control equals its committed record (Amendment B)
    python3 controls/check_p147.py e6-collect [STEPS_TSV]   # local E6 record from steps.tsv and logs/ (no binary)
    python3 controls/check_p147.py verdict CI_RUNS.json     # needs the gate records and results/p147/e6.json
    python3 controls/check_p147.py stage CASE DIR       # A1..A6 or a synthetic package by name, for a development run
    python3 controls/check_p147.py selftest             # pure Python, no data and no binary

The independent algorithm (the functions up to `expected_package`) follows protocols/ACTINV-P147_PROTOCOL.md and the BTP source
review in docs/maintainers/WASTE_BTP_SOURCE_REVIEW.md. It reuses the P145 evaluator (check_p145.evaluate) for every CFR class. Result field names
are read only in the adapter functions `a_*` below, one per field group; edit only those to align names. The fields read are:
  result:   schema, spec_sha256 (informational), rule_pack_sha256, btp_sha256 (when the bundled file exists), screen,
            encapsulated, readings (non-empty list of strings), not_claimed (strings naming blending, alternative
            approaches, 61.58, site licence, 61.56, centring, shipment), steps
  step:     step, t_s, package_class, items, sections, groups, encapsulation
  item:     id, waste_type, volume_cm3, mass_g, small, total_activity_bq, own_class, activities_bq{nuclide: Bq}, section_of
  section:  component, pieces[], merged, split_out[]
  group:    waste_type, items[], screen_applied, restrictive_table, table1_sum, table2_sum, primary_gamma_share,
            primary_gamma_controlling, candidates, mixture_class, removed_items[{id, own_class}]
  candidate: class, small_pool{items[], table2_sum, passes} or null, item_tests, removed[], remainder_class,
            remainder_volume_cm3, remainder_mass_g, qualifies
  item test: id, passes, primary_gamma{limit_met, table2_sum, factor, failing[]}, others[{nuclide, limit_met}]
  encapsulation: waste_volume_cm3, loading, credited_volume_cm3, credited_mass_g, status, class, class_c_cap{class, passes}
  `actinv waste` (E4): components[0].targets[]{step, class, evaluation.constraints[]{table, target_class, normalized_sum}}

Decisions recorded in every record that uses them (the protocol and the contract leave these open):
  * Keys starting with an underscore (`_note`) are removed from the staged copies of the problems and package specs. The token
    `"nuclide_properties": "from_decay_data"` is replaced, as in P146, by the half-life, Z and alpha flag of every
    radionuclide of the primary then fallback decay file of the LO problem (first file wins; alpha emission is a decay mode with
    positive branching ratio whose RTYP has the digit 4); the map is cached under target/p146/properties.
  * Classes, statuses, choices (`limit_met`), flags and counts are compared exactly; every number within 1e-12 relative
    (a number that is null in the independent result is accepted as null or absent). Where the checker sums floating-point
    values it adds left to right from zero, items and nuclides in sorted order, as Rust's Iterator::sum.
  * Candidate loop: for X in A, B, C every item is tested at X's limits, failing items are removed, and the remainder's averaged
    class Y is compared with X. The mixture class is the lowest X with Y <= X, or with no item left (an empty remainder
    qualifies); none qualifies: `above_class_c`. The package class is the highest of the mixture classes and the own classes of
    the items removed at the mixture class. So an item that fails at Class A while no other item remains gives a Class A mixture
    and the package takes the item's own class. A result may list candidates only up to the first qualifying class.
  * The class limit of a Table 1 nuclide is 0.1 x the Table 1 value for X = A or B and the Table 1 value for X = C; a Table 2
    nuclide uses the column of X; a null column has no limit for X and the nuclide is not tested there.
  * Restrictive table and controlling share are computed on the averaged concentrations of the group, with the table sums
    relative to the Table 1 values and to the Table 2 column of the averaged class (the Class C column when it is above
    Class C). When both tables give the same class the table with the greater raw sum is restrictive (ties go to Table 2); the
    primary-gamma share is the share of that table's sum, 0 when the sum is 0, and controls when it is strictly above 1/2.
    `table2_sum` is the Table 2 sum of that column.
  * limit_met: the BTP Table 2 (or 3) value takes precedence over the Factor when both are met; a pooled item under 280 cm3
    takes the pool's sum and result. `others` lists the radionuclides of concern (concentration above 1 percent of the class
    limit) that are not primary gammas, the alpha transuranic group once under its own name; a nuclide with no row for the
    waste type or no limit at X is not listed.
  * Sections: pieces of one component are one item when no piece exceeds BTP Table 3 and no piece under 280 cm3 exceeds BTP
    Table 2, with the class column of the whole component; failing pieces stay separate items.
  * Encapsulation: waste volume declared or the sum of the item volumes; loading = waste volume / total volume (>= 0.14
    credits the whole container up to 331 ft3; otherwise at most 0.2 m3 and 500 kg); the encapsulant credit is
    min(encapsulant mass, min(2.5, declared density) x (total - waste volume)); the Class C cap is checked per waste-type
    group over the whole container volume and the total mass, the highest group class decides; the encapsulation `class` is
    the highest mixture class of the groups classified over the credited volume and mass.
  * An unknown group (an item with a nuclide that has no properties) makes the package `unknown`; its other fields are not
    compared.
  * E3 builds each at case from exactly representable values (volumes of 1 m3, integer or literal activities, integer limits).
    Where equivalent orders of floating-point operations disagree at the boundary the case is listed in
    `construction_not_robust` and is only required to run.
"""
from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import math
import os
import random
import re
import shutil
import sys
import tempfile
from collections import Counter
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import check_p145 as p145  # noqa: E402  (the independent 10 CFR 61.55 evaluator)
import check_p146 as p146  # noqa: E402  (staging helpers, properties from decay files, step-log readers)

OUT = ROOT / "results/p147"
WORK = ROOT / "target/p147"
INPUTS = ROOT / "controls/p147_inputs"
PROTOCOL = ROOT / "protocols/ACTINV-P147_PROTOCOL.md"
BTP_FILE = ROOT / "crates/actinv-core/data/waste_us_nrc_btp_2015_v1.json"
BTP_MIRROR = ROOT / "data/waste_us_nrc_btp_2015_v1.json"
RULES_FILE = p145.RULES_FILE
BINARY = Path(os.environ.get("ACTINV_BIN", ROOT / "target/release/actinv"))
TIMEOUT = int(os.environ.get("P147_TIMEOUT", "900"))
SPEC_SCHEMA = "actinv-waste-package-spec-1"
RESULT_SCHEMA = "actinv-waste-package-1"
BTP_ID = "us-nrc-ca-btp-2015-rev1-v1"
BUNDLED = p145.BUNDLED_RULES_ID
PROPERTIES_TOKEN = "from_decay_data"
REL_TOL = 1e-12
MISSING = p145.MISSING
SKIP = object()  # an expected field the checker does not compare (a documented reading the contract leaves open)
# the candidate classes of the loop; a class column of a BTP table is indexed in this order
CLASSES = ["A", "B", "C"]
RANK = {"A": 0, "B": 1, "C": 2, "above_class_c": 3}
BTP_TYPES = ["activated_metal", "contaminated_material", "design_radioactivity_component", "sealed_source",
             "cartridge_filter"]
# Decisions on readings the protocol and the contract leave open (each is recorded in the gate records):
#   TIE_NORMALIZED: when both CFR tables give the same class, "the table with the greater sum of fractions" compares the
#   raw sums (Table 1 relative to the Table 1 values, Table 2 relative to the column of the averaged class), as the
#   contract words it. True would compare the Table 1 sum relative to its Class A/B boundary (0.1 x) instead.
TIE_NORMALIZED = False

sha = p145.sha
sha_bytes = p145.sha_bytes
git = p145.git
tree_clean = p145.tree_clean
dump = p145.dump
brief = p145.brief
pick = p145.pick
nsum = p146.nsum
close = p146.close
canon = p145.canonical_nuclide

# --- the BTP data of the frozen source review (docs/maintainers/WASTE_BTP_SOURCE_REVIEW.md) -----------------------

EXPECTED_BTP = {
    "schema": "actinv-waste-btp-1",
    "id": BTP_ID,
    "source": {
        "title": "Concentration Averaging and Encapsulation Branch Technical Position, Revision 1, Volume 1",
        "adams": "ML12254B065",
        "sha256_2015_original": "4e0e3df3017b0f546630c94fbbd637c7fb4ee124f830afa575dfc05ad67ba7eb",
        "review": "docs/maintainers/WASTE_BTP_SOURCE_REVIEW.md",
    },
    # V1 Sec. 3.3.2.2 p.22: "the primary gamma-emitting nuclides are cobalt-60 (60Co), niobium-94 (94Nb), and cesium-137"
    "primary_gamma": ["Co-60", "Nb-94", "Cs-137"],
    # BTP Table 2 (V1 p.25): 60Co 5.2 TBq (140 Ci) | No limit | No limit; 94Nb 37 MBq (1 mCi) for A, B and C;
    # 137Cs 266 MBq (7.2 mCi) | 27 GBq (0.72 Ci) | 4.8 TBq (130 Ci). Columns are Class A, B, C; None is "No limit".
    "table2_bq": {"Co-60": [5.2e12, None, None], "Nb-94": [3.7e7, 3.7e7, 3.7e7], "Cs-137": [2.66e8, 2.7e10, 4.8e12]},
    # BTP Table 3 (V1 p.26): 3H 0.3 TBq (8 Ci) for Class A or B and No limit for Class C; 14C 0.04 TBq (1 Ci) | 0.4 TBq
    # (10 Ci); 59Ni 0.15 TBq (4 Ci) | 1.5 TBq (40 Ci); 63Ni 0.26 TBq (7 Ci) | 55 TBq (1500 Ci); alpha-emitting
    # transuranics with half-life above 5 years (excluding 241Pu and 242Cm) 111 MBq (3 mCi) | 1.1 GBq (30 mCi).
    "table3_bq": {"H-3": [3.0e11, 3.0e11, None], "C-14": [4.0e10, 4.0e10, 4.0e11],
                  "Ni-59": [1.5e11, 1.5e11, 1.5e12], "Ni-63": [2.6e11, 2.6e11, 5.5e13],
                  "alpha_transuranic_gt5y": [1.11e8, 1.11e8, 1.1e9]},
    "thresholds": {
        # V1 p.22: "If each item has an activity less than 37 MBq (1 mCi)" (strictly less, total activity: protocol)
        "screen_activity_bq": 3.7e7,
        # V1 p.26: "items larger than 280 cc (0.01 ft3) may be treated individually ... smaller than 280 cc ... grouped"
        "small_item_cm3": 280.0,
        # V1 p.25: "Factor of 2" (primary gammas classification controlling) and "Factor of 10" (otherwise)
        "factor_controlling": 2.0, "factor_other": 10.0,
        # V1 p.25: "contribute more than 50 percent of the sum of fractions in the more restrictive table"
        "controlling_share": 0.5,
        # V1 p.45 (App. A): radionuclides of concern exceed "1 percent" of the class limit
        "concern_fraction": 0.01,
        # V1 p.30: "minimum waste loading ... is at least 14 percent"; "up to 9.5 m3 (331 ft3)" (9.4 m3 in Fig. 5 and V2);
        # the protocol uses 331 ft3 = 331 x 28316.846592 cm3 = 9372876.221952 cm3
        "encapsulation_min_loading": 0.14, "encapsulation_max_volume_cm3": 9372876.221952,
        # V1 p.30: "the maximum volume or mass used ... should be 0.2 m3 or 500 kg" (low waste loading)
        "low_loading_max_volume_cm3": 200000.0, "low_loading_max_mass_g": 500000.0,
        # V1 p.31 fn.43: "credit for the actual density of the encapsulating material ... if the density is less than
        # 2.5 g/cc"
        "encapsulant_max_density_g_cm3": 2.5,
    },
}

# the printed SI value and the printed customary value (Ci) of every BTP Table 2 and Table 3 entry, for the SI to
# customary consistency check of E2
REVIEW_PRINTED = {
    "table2": {"Co-60": [(5.2e12, 140.0), None, None], "Nb-94": [(3.7e7, 1e-3)] * 3,
               "Cs-137": [(2.66e8, 7.2e-3), (2.7e10, 0.72), (4.8e12, 130.0)]},
    "table3": {"H-3": [(3.0e11, 8.0), (3.0e11, 8.0), None], "C-14": [(4.0e10, 1.0), (4.0e10, 1.0), (4.0e11, 10.0)],
               "Ni-59": [(1.5e11, 4.0), (1.5e11, 4.0), (1.5e12, 40.0)],
               "Ni-63": [(2.6e11, 7.0), (2.6e11, 7.0), (5.5e13, 1500.0)],
               "alpha_transuranic_gt5y": [(1.11e8, 3e-3), (1.11e8, 3e-3), (1.1e9, 3e-2)]},
}

# nuclide properties of the synthetic packages: canonical key -> (z, half_life_s, alpha_emitting)
SYN_PROPS = {
    "H3": (1, 3.8878e8, False), "C14": (6, 1.8081e11, False), "Fe55": (26, 8.64e7, False),
    "Mn54": (25, 2.70e7, False), "Co58": (27, 6.12e6, False), "Co60": (27, 1.6637e8, False),
    "Ni59": (28, 2.397e12, False), "Ni63": (28, 3.1558e9, False), "Sr90": (38, 9.08e8, False),
    "Nb94": (41, 6.4e11, False), "Tc99": (43, 6.66e12, False), "I129": (53, 4.95e14, False),
    "Cs137": (55, 9.49e8, False), "Pu241": (94, 4.52e8, False), "Pu239": (94, 7.61e11, True),
    "Am241": (95, 1.364e10, True),
}


def props_json(props: dict) -> dict:
    return {k: {"z": v[0], "half_life_s": v[1], "alpha_emitting": v[2]} for k, v in sorted(props.items())}


def props_from_json(doc: dict) -> dict:
    return {canon(k): (v["z"], v["half_life_s"], v["alpha_emitting"]) for k, v in doc.items()}


def amendments() -> dict:
    return {p.name: sha(p) for p in sorted((ROOT / "protocols").glob("ACTINV-P147_AMENDMENT_*.md"))}


def candidate() -> dict:
    if not BINARY.is_file():
        raise SystemExit(f"binary {BINARY} not found (set ACTINV_BIN or build target/release/actinv)")
    return {"commit": git("rev-parse", "HEAD"), "tree_clean": tree_clean(),
            "binary": str(BINARY.relative_to(ROOT)) if BINARY.is_relative_to(ROOT) else str(BINARY),
            "binary_sha256": sha(BINARY), "protocol_sha256": sha(PROTOCOL), "amendments_sha256": amendments()}


def finish(name: str, report: dict) -> int:
    report["worktree_clean_after"] = tree_clean()
    report["pass"] = bool(report.get("pass")) and report["candidate"]["tree_clean"]
    dump(OUT / f"{name}.json", report)
    print(json.dumps({"gate": name, "pass": report["pass"], **report.get("summary", {})}, sort_keys=True))
    return 0 if report["pass"] else 1


def load_json(path):
    return json.loads(Path(path).read_text())


def data_root() -> Path:
    path = Path(os.environ.get("P147_DATA", os.environ.get("P146_DATA", os.environ.get("P145_DATA", ROOT / "actinv-data"))))
    if not path.is_dir():
        raise SystemExit(f"data tree {path} not found (set P147_DATA to the actinv-data directory)")
    return path.resolve()


def fresh_dir(path: Path, data: bool = False) -> Path:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    if data:
        (path / "actinv-data").symlink_to(data_root())
    return path


def write_json(path: Path, doc) -> None:
    path.write_text(json.dumps(doc, indent=1, sort_keys=False) + "\n")


class Checks(p146.Checks):
    def flag(self, label: str, ok: bool, expected, actual) -> bool:
        return self._record(bool(ok), label, expected, actual)

    def note(self, label: str, value) -> None:
        """A recorded observation that is not a pass or fail check."""
        if not hasattr(self, "notes"):
            self.notes = {}
        self.notes[label] = value

    def report(self) -> dict:
        out = super().report()
        out["notes"] = dict(getattr(self, "notes", {}))
        return out


def rel_ok(a, b, tol: float = REL_TOL) -> bool:
    return close(float(a), float(b), tol)


def dig(obj, *path):
    """obj[path[0]][path[1]]... with MISSING for anything absent (lists are indexed by integer)."""
    for key in path:
        if isinstance(obj, dict) and key in obj:
            obj = obj[key]
        elif isinstance(obj, list) and isinstance(key, int) and 0 <= key < len(obj):
            obj = obj[key]
        else:
            return MISSING
    return obj


# --- branch accounting ---------------------------------------------------------------------------------------------

HITS: Counter = Counter()

BRANCHES = [
    # items, groups and sections (protocol items 1 and 3)
    "item.result_activity", "item.declared_activity", "item.cartridge_filter", "item.small", "item.large",
    "item.exact_280", "item.mass_based_nuclide", "group.single_type", "group.multi_type", "group.unknown",
    "section.none", "section.merged", "section.split_table3", "section.split_table2_small",
    "section.one_left", "section.all_split",
    # screens (BTP 3.3.2.1)
    "screen.none", "screen.37_applied", "screen.37_fallthrough", "screen.highest",
    # restrictive table and controlling share (BTP p.25)
    "restrict.table1_higher", "restrict.table2_higher", "restrict.tie_table1", "restrict.tie_table2",
    "controlling.true", "controlling.false", "controlling.zero_total",
    # candidate class loop (protocol item 2)
    "cand.A_qualifies", "cand.B_qualifies", "cand.C_qualifies", "cand.none_qualifies", "cand.empty_remainder",
    "cand.remainder_above_x",
    # primary gammas: BTP Table 2 (individual, pooled) or the Factor of 2 or 10
    "pg.table2_individual_pass", "pg.table2_individual_fail", "pg.pool_pass", "pg.pool_fail", "pg.factor2_met",
    "pg.factor10_met", "pg.fail", "pg.vacuous_factor", "pg.no_limit_skipped", "pg.unlisted", "pg.not_concerned",
    # other nuclides: BTP Table 3 or the Factor of 10
    "oth.table3_met", "oth.factor10_met", "oth.fail", "oth.not_concerned", "oth.L_null", "oth.unlisted",
    "oth.not_in_table3_factor_only", "oth.tru_group",
    # encapsulation (protocol item 4)
    "enc.credited", "enc.low_loading", "enc.alt_required", "enc.cap_exceeded", "enc.density_cap_binds",
    "enc.declared_density_lower", "enc.encapsulant_mass_binds", "enc.waste_volume_declared",
    "enc.waste_volume_summed", "enc.low_volume_capped", "enc.low_volume_total", "enc.low_mass_capped",
    "enc.low_mass_under", "enc.loading_at_threshold", "enc.total_at_max", "enc.mixed_rule_sets",
    # package class (protocol item 5)
    "pkg.class_from_group", "pkg.class_from_removed", "pkg.unknown",
]


def hit(name: str) -> None:
    HITS[name] += 1


def rank(cls: str) -> int:
    return RANK[cls]


def highest(classes) -> str:
    classes = list(classes)
    if "unknown" in classes:
        return "unknown"
    return max(classes, key=lambda c: RANK[c])


def class_index(cls: str) -> int:
    """Candidate index and Table 2 / Table 3 column of a class (above Class C uses the Class C column)."""
    return {"A": 0, "B": 1, "C": 2, "above_class_c": 2}[cls]


_RULES = []


def get_rules():
    if not _RULES:
        _RULES.append(p145.Rules(RULES_FILE))
    return _RULES[0]


class Ctx:
    """Rule pack, nuclide properties and BTP data for one package."""

    def __init__(self, props: dict, rules=None, btp: dict | None = None):
        self.rules = rules or get_rules()
        self.props = dict(props)
        self.btp = btp or EXPECTED_BTP
        self.pg = [canon(n) for n in self.btp["primary_gamma"]]
        self.t2 = {canon(n): v for n, v in self.btp["table2_bq"].items()}
        self.t3 = {(n if n == p145.ALPHA else canon(n)): v for n, v in self.btp["table3_bq"].items()}
        self.th = self.btp["thresholds"]
        self.mem = {}


def eval_type(btype: str) -> str:
    return "activated_metal" if btype == "activated_metal" else "general"


# --- the spec as the algorithm reads it ------------------------------------------------------------------------------

def normalize_package(spec: dict, base: Path | None, files: dict | None = None) -> dict:
    """Items with per-step Bq, from a package spec; `activity.result` names resolve against `base` or, for a synthetic
    package, are looked up in `files` ({name: result document})."""
    if spec.get("schema") != SPEC_SCHEMA:
        raise ValueError(f"unsupported schema {spec.get('schema')!r}")
    targets = [int(t) for t in spec["targets"]]
    props = spec["nuclide_properties"]
    if not isinstance(props, dict):
        raise ValueError("nuclide_properties is not expanded; stage the case first")
    cache, t_s, items = {}, {}, []
    for raw in spec["items"]:
        btype = raw["waste_type"]
        mass = float(raw["mass_g"])
        vol = float(raw["envelope_volume_cm3"] if btype == "cartridge_filter" else raw["displaced_volume_cm3"])
        activity, bq = raw["activity"], {}
        if "result" in activity:
            key = activity["result"]
            if key not in cache:
                doc = files[key] if files is not None and key in files else load_json(Path(base) / key)
                cache[key] = {int(r["step"]): r for r in doc["steps"]}
            for s in targets:
                row = cache[key][s]
                bq[s] = {canon(n): v * mass for n, v in row["activity_Bq_per_g"].items()}
                t_s.setdefault(s, row["t_s"])
            source = "result"
        else:
            for s in targets:
                bq[s] = {canon(n): float(v) for n, v in activity["declared_bq"][str(s)].items()}
            source = "declared"
        items.append({"id": raw["id"], "type": btype, "wt": eval_type(btype), "mass": mass, "vol": vol,
                      "section_of": raw.get("section_of"), "bq": bq, "source": source})
    return {"targets": targets, "props": props_from_json(props), "screen": spec.get("screen", "none"),
            "enc": spec.get("encapsulation"), "items": items, "t_s": t_s, "spec": spec}


# --- evaluator pieces ---------------------------------------------------------------------------------------------

def membership(ctx: Ctx, wt: str, key: str):
    """((Table 1 row, is_alpha_group) or None, Table 2 row or None) by the evaluator's row membership."""
    k = (wt, key)
    if k in ctx.mem:
        return ctx.mem[k]
    z, half_life_s, alpha = ctx.props[key]
    years = half_life_s / ctx.rules.year_s
    t1_rows, t2_rows = ctx.rules.applicable(1, wt), ctx.rules.applicable(2, wt)
    named = [r for r in t1_rows if r["selector"] != p145.ALPHA and canon(r["selector"]) == key]
    group = [r for r in t1_rows if r["selector"] == p145.ALPHA]
    if named:
        t1 = (named[0], False)
    elif group and z > 92 and alpha and years > 5.0:
        t1 = (group[0], True)
    else:
        t1 = None
    named2 = [r for r in t2_rows if r["selector"] != p145.SHORT and canon(r["selector"]) == key]
    short = [r for r in t2_rows if r["selector"] == p145.SHORT]
    t2 = named2[0] if named2 else (short[0] if short and years < 5.0 else None)
    ctx.mem[k] = (t1, t2)
    return ctx.mem[k]


def limit_rows(ctx: Ctx, wt: str, key: str) -> list:
    t1, t2 = membership(ctx, wt, key)
    return [r for r in ((t1[0] if t1 else None), t2) if r is not None]


def fraction(ctx: Ctx, bq: float, row: dict, limit: float, mass: float, vol: float) -> float:
    ci = ctx.rules.ci_bq
    if row["unit"] == "Ci/m3":
        return bq / (limit * (ci / 1.0e6) * vol)
    return bq / (limit * (ci / 1.0e9) * mass)


def conc(ctx: Ctx, bq: float, row: dict, mass: float, vol: float) -> float:
    """Concentration in the row's unit, formed as the evaluator does (activity over Ci, then over the volume or mass)."""
    ci = ctx.rules.ci_bq
    if row["unit"] == "Ci/m3":
        return bq / ci / (vol * 1.0e-6)
    return bq / (ci / 1.0e9) / mass


def class_limit(row: dict, xi: int):
    """Limit of a row for candidate class index xi: 0.1 x the Table 1 value for Class A and B, the Table 1 value for
    Class C; the Table 2 column (None where the column has no limit)."""
    if row["table"] == 1:
        return row["limits"][0] * (0.1 if xi < 2 else 1.0)
    return row["limits"][xi]


def table_class(ev: dict, table: int) -> str:
    for target in CLASSES:
        if all(c["passes"] for c in ev["constraints"] if c["target_class"] == target and c["table"] == table):
            return target
    return "above_class_c"


def sum_bq(dicts) -> dict:
    dicts = list(dicts)
    keys = sorted({k for d in dicts for k in d})
    return {k: nsum(d.get(k, 0.0) for d in dicts) for k in keys}


def ev_full(ctx: Ctx, wt: str, mass: float, vol: float, bq: dict) -> dict:
    """The evaluator's class and constraints for one inventory, with the raw table sums and their primary-gamma part."""
    nz = {k: v for k, v in bq.items() if v != 0.0}
    props = {k: ctx.props[k] for k in nz if k in ctx.props}
    ev = p145.evaluate(ctx.rules, wt, mass, vol, nz, props)
    t1 = pg1 = 0.0
    t2, pg2 = [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]
    for key in sorted(nz):
        if key not in props:
            continue
        m1, m2 = membership(ctx, wt, key)
        if m1 is not None:
            f = fraction(ctx, nz[key], m1[0], m1[0]["limits"][0], mass, vol)
            t1 += f
            if key in ctx.pg:
                pg1 += f
        if m2 is not None:
            for col in range(3):
                limit = m2["limits"][col]
                if limit is not None:
                    f = fraction(ctx, nz[key], m2, limit, mass, vol)
                    t2[col] += f
                    if key in ctx.pg:
                        pg2[col] += f
    out = {"class": ev["class"], "unknown": ev["unknown_nuclides"], "constraints": ev["constraints"], "t1": t1,
           "t2": t2, "pg1": pg1, "pg2": pg2}
    if ev["class"] != "unknown":
        out["c1"], out["c2"] = table_class(ev, 1), table_class(ev, 2)
    return out


def t2_sum(ctx: Ctx, bq: dict, xi: int) -> float:
    """BTP Table 2 sum of fractions of the primary gammas of an inventory for the class column xi (None entries skipped)."""
    total = 0.0
    for key in ctx.pg:
        limit = ctx.t2[key][xi]
        if limit is not None:
            total += bq.get(key, 0.0) / limit
    return total


def table3_ok(ctx: Ctx, wt: str, piece: dict, col: int) -> bool:
    """No Table 3 nuclide of the piece exceeds its BTP Table 3 value in column col (the alpha group is summed)."""
    for key in sorted(ctx.t3):
        if key == p145.ALPHA:
            continue
        limit = ctx.t3[key][col]
        if limit is not None and piece["bq"].get(key, 0.0) > limit:
            return False
    tru = nsum(piece["bq"][k] for k in sorted(piece["bq"])
               if k in ctx.props and (membership(ctx, wt, k)[0] or (None, False))[1])
    limit = ctx.t3[p145.ALPHA][col]
    return limit is None or tru <= limit


def make_entity(members: list, eid: str) -> dict:
    vol = nsum(m["vol"] for m in members)
    mass = nsum(m["mass"] for m in members)
    bq = members[0]["bq"] if len(members) == 1 else sum_bq([m["bq"] for m in members])
    return {"id": eid, "members": [m["id"] for m in members], "wt": members[0]["wt"], "vol": vol, "mass": mass,
            "bq": bq, "total": nsum(m["total"] for m in members)}


def build_entities(ctx: Ctx, wt: str, gitems: list, xi: int):
    """The items the BTP tests see at candidate class index xi. Sectioned pieces of one component are one item when no
    piece exceeds BTP Table 3 and no piece under 280 cm3 exceeds BTP Table 2 in that class's column (BTP 3.3.2.3, the
    same class whose limits every item test uses); the merged item takes the component's id and the place of its first
    kept piece."""
    sections, merged = {}, {}
    small_limit = ctx.th["small_item_cm3"]
    for comp in sorted({i["section_of"] for i in gitems if i["section_of"] is not None}):
        pieces = [p for p in gitems if p["section_of"] == comp]
        rest, split = [], []
        for piece in pieces:
            ok3 = table3_ok(ctx, wt, piece, xi)
            ok2 = piece["vol"] >= small_limit or t2_sum(ctx, piece["bq"], xi) <= 1.0
            if not ok3:
                hit("section.split_table3")
            if ok3 and not ok2:
                hit("section.split_table2_small")
            (rest if ok3 and ok2 else split).append(piece)
        if len(rest) >= 2:
            hit("section.merged")
            merged[comp] = rest
        elif len(rest) == 1 and len(pieces) > 1:
            hit("section.one_left")
        elif not rest:
            hit("section.all_split")
        sections[comp] = {"pieces": [p["id"] for p in pieces], "merged": len(rest) >= 2,
                          "split_out": [p["id"] for p in split]}
    entities, placed = [], set()
    for item in gitems:
        comp = item["section_of"]
        if comp is None:
            hit("section.none")
        if comp in merged and any(m is item for m in merged[comp]):
            if comp not in placed:
                placed.add(comp)
                entities.append(make_entity(merged[comp], comp))
        else:
            entities.append(make_entity([item], item["id"]))
    for e in entities:
        e["small"] = e["vol"] < small_limit
        e["ev"] = ev_full(ctx, wt, e["mass"], e["vol"], e["bq"])
        e["own"] = e["ev"]["class"]
    return entities, sections


def restriction(ctx: Ctx, avg: dict) -> dict:
    """The restrictive CFR table and the primary-gamma share of its sum (BTP p.25)."""
    cls = avg["class"]
    col = class_index(cls)
    c1, c2 = avg["c1"], avg["c2"]
    t1, t2 = avg["t1"], avg["t2"][col]
    if RANK[c1] > RANK[c2]:
        hit("restrict.table1_higher")
        table = 1
    elif RANK[c2] > RANK[c1]:
        hit("restrict.table2_higher")
        table = 2
    else:
        left = t1 / (0.1 if cls in ("A", "B") else 1.0) if TIE_NORMALIZED else t1
        table = 1 if left > t2 else 2
        hit("restrict.tie_table1" if table == 1 else "restrict.tie_table2")
    total = t1 if table == 1 else t2
    pg = avg["pg1"] if table == 1 else avg["pg2"][col]
    if total > 0.0:
        share = pg / total
    else:
        share = 0.0
        hit("controlling.zero_total")
    controlling = share > ctx.th["controlling_share"]
    hit("controlling.true" if controlling else "controlling.false")
    return {"restrictive_table": table, "table1_sum": t1, "table2_sum": t2, "primary_gamma_share": share,
            "primary_gamma_controlling": controlling}


def pg_block(ctx: Ctx, wt: str, e: dict, xi: int, factor: float, pool):
    """Primary-gamma test of one item at candidate class index xi: BTP Table 2 sum of fractions (pooled for items under
    280 cm3), or the Factor of 2 or 10 per nuclide against the class limit; returns (record, passes)."""
    th = ctx.th
    if e["small"]:
        t2s, ok2 = pool["table2_sum"], pool["passes"]
    else:
        t2s = t2_sum(ctx, e["bq"], xi)
        ok2 = t2s <= 1.0
        hit("pg.table2_individual_pass" if ok2 else "pg.table2_individual_fail")
    failing, tested = [], 0
    for key in ctx.pg:
        b = e["bq"].get(key, 0.0)
        if not b > 0.0 or key not in ctx.props:
            continue
        rows = limit_rows(ctx, wt, key)
        if not rows:
            hit("pg.unlisted")
            continue
        usable = bad = False
        for row in rows:
            limit = class_limit(row, xi)
            if limit is None:
                continue
            usable = True
            c = conc(ctx, b, row, e["mass"], e["vol"])
            if c > th["concern_fraction"] * limit:
                tested += 1
                if not c < factor * limit:
                    bad = True
            else:
                hit("pg.not_concerned")
        if not usable:
            hit("pg.no_limit_skipped")
        if bad:
            failing.append(key)
    factor_ok = not failing
    if ok2:
        met = "table2"
    elif factor_ok:
        met = "factor2" if factor == th["factor_controlling"] else "factor10"
        hit("pg.factor2_met" if met == "factor2" else "pg.factor10_met")
        if tested == 0:
            hit("pg.vacuous_factor")
    else:
        met = None
        hit("pg.fail")
    return {"limit_met": met, "table2_sum": t2s, "factor": int(factor), "failing": sorted(failing)}, ok2 or factor_ok


def others_block(ctx: Ctx, wt: str, e: dict, xi: int) -> dict:
    """{nuclide: limit met or None} for the radionuclides of concern that are not primary gammas: BTP Table 3 activity
    or the Factor of 10 (Table 1 rows only for those absent from Table 3). The alpha transuranic group is one entry."""
    th = ctx.th
    entries, tru = [], []
    for key in sorted(e["bq"]):
        b = e["bq"][key]
        if not b > 0.0 or key in ctx.pg or key not in ctx.props:
            continue
        t1, _t2 = membership(ctx, wt, key)
        if t1 is not None and t1[1]:
            tru.append(key)
            continue
        rows = limit_rows(ctx, wt, key)
        if not rows:
            hit("oth.unlisted")
            continue
        entries.append((key, b, rows, key if key in ctx.t3 else None))
    if tru:
        hit("oth.tru_group")
        entries.append((p145.ALPHA, nsum(e["bq"][k] for k in tru), [membership(ctx, wt, tru[0])[0][0]], p145.ALPHA))
    records = {}
    for name, b, rows, t3key in entries:
        limits = [(row, class_limit(row, xi)) for row in rows]
        limits = [(row, limit) for row, limit in limits if limit is not None]
        if not limits:
            hit("oth.L_null")
            continue
        if not any(conc(ctx, b, row, e["mass"], e["vol"]) > th["concern_fraction"] * limit for row, limit in limits):
            hit("oth.not_concerned")
            continue
        factor_ok = all(conc(ctx, b, row, e["mass"], e["vol"]) < th["factor_other"] * limit for row, limit in limits)
        limit3 = ctx.t3[t3key][xi] if t3key else None
        if limit3 is not None and b <= limit3:
            met = "table3"
            hit("oth.table3_met")
        elif factor_ok:
            met = "factor10"
            hit("oth.factor10_met")
            if t3key is None:
                hit("oth.not_in_table3_factor_only")
        else:
            met = None
            hit("oth.fail")
        records[name] = met
    return records


def candidate_at(ctx: Ctx, wt: str, gitems: list, xi: int, factor: float, den):
    """Candidate class index xi: (record, the items tested, the section records)."""
    entities, sections = build_entities(ctx, wt, gitems, xi)
    smalls = [e for e in entities if e["small"]]
    pool = None
    if smalls:
        pool_bq = {k: nsum(e["bq"].get(k, 0.0) for e in smalls) for k in ctx.pg}
        total = t2_sum(ctx, pool_bq, xi)
        pool = {"items": [e["id"] for e in smalls], "table2_sum": total, "passes": total <= 1.0}
        hit("pg.pool_pass" if pool["passes"] else "pg.pool_fail")
    tests, kept = {}, []
    for e in entities:
        block, pg_ok = pg_block(ctx, wt, e, xi, factor, pool)
        others = others_block(ctx, wt, e, xi)
        passes = pg_ok and all(v is not None for v in others.values())
        tests[e["id"]] = {"passes": passes, "primary_gamma": block, "others": others}
        if passes:
            kept.append(e)
    removed = [e["id"] for e in entities if not tests[e["id"]]["passes"]]
    if kept:
        mass = den[0] if den else nsum(e["mass"] for e in kept)
        vol = den[1] if den else nsum(e["vol"] for e in kept)
        remainder = ev_full(ctx, wt, mass, vol, sum_bq([e["bq"] for e in kept]))["class"]
        qualifies = RANK[remainder] <= xi
        if not qualifies:
            hit("cand.remainder_above_x")
    else:
        hit("cand.empty_remainder")
        remainder, mass, vol, qualifies = None, None, None, True
    return ({"small_pool": pool, "item_tests": tests, "removed": removed, "remainder_class": remainder,
             "remainder_volume_cm3": vol, "remainder_mass_g": mass, "qualifies": qualifies}, entities, sections)


def run_group(ctx: Ctx, btype: str, gitems: list, screen: str, den):
    """One waste-type group: (record, section records, own classes of removed items, mixture class). The group lists
    its original item ids; the sections are those of the chosen candidate (under a screen, of the average's class)."""
    wt = gitems[0]["wt"]
    ids = [i["id"] for i in gitems]
    if any(i["own"] == "unknown" for i in gitems):
        hit("group.unknown")
        record = {"items": ids, "screen_applied": SKIP, "restrictive_table": SKIP, "table1_sum": SKIP,
                  "table2_sum": SKIP, "primary_gamma_share": SKIP, "primary_gamma_controlling": SKIP,
                  "candidates": SKIP, "mixture_class": "unknown", "removed_items": SKIP}
        return record, {}, [], "unknown"
    mass_sum = nsum(i["mass"] for i in gitems)
    vol_sum = nsum(i["vol"] for i in gitems)
    dm, dv = den if den else (mass_sum, vol_sum)
    avg = ev_full(ctx, wt, dm, dv, sum_bq([i["bq"] for i in gitems]))
    star = class_index(avg["class"])
    if screen == "activity_37mbq":
        entities, sections = build_entities(ctx, wt, gitems, star)
        if all(e["total"] < ctx.th["screen_activity_bq"] for e in entities):
            hit("screen.37_applied")
            return ({"items": ids, "screen_applied": "activity_37mbq", "restrictive_table": SKIP, "table1_sum": SKIP,
                     "table2_sum": SKIP, "primary_gamma_share": SKIP, "primary_gamma_controlling": SKIP,
                     "candidates": {}, "mixture_class": avg["class"], "removed_items": {}}, sections, [],
                    avg["class"])
        hit("screen.37_fallthrough")
    elif screen == "highest_item":
        hit("screen.highest")
        entities, sections = build_entities(ctx, wt, gitems, star)
        mixture = highest(e["own"] for e in entities)
        return ({"items": ids, "screen_applied": "highest_item", "restrictive_table": SKIP, "table1_sum": SKIP,
                 "table2_sum": SKIP, "primary_gamma_share": SKIP, "primary_gamma_controlling": SKIP,
                 "candidates": {}, "mixture_class": mixture, "removed_items": {}}, sections, [], mixture)
    else:
        hit("screen.none")
    info = restriction(ctx, avg)
    factor = ctx.th["factor_controlling"] if info["primary_gamma_controlling"] else ctx.th["factor_other"]
    candidates, tested, secs, mixture = {}, {}, {}, None
    for xi, name in enumerate(CLASSES):
        candidates[name], tested[name], secs[name] = candidate_at(ctx, wt, gitems, xi, factor, den)
        if candidates[name]["qualifies"] and mixture is None:
            mixture = name
            hit(f"cand.{name}_qualifies")
    # when no class qualifies the group is above Class C and the items removed at Class C are reported
    pick = mixture or CLASSES[-1]
    if mixture is None:
        hit("cand.none_qualifies")
        mixture = "above_class_c"
    by_id = {e["id"]: e for e in tested[pick]}
    own = [by_id[i]["own"] for i in candidates[pick]["removed"]]
    removed = {i: by_id[i]["own"] for i in candidates[pick]["removed"]}
    record = {"items": ids, "screen_applied": None, **info, "candidates": candidates, "mixture_class": mixture,
              "removed_items": removed}
    return record, secs[pick], own, mixture


def enc_rule_sets(items: list) -> list:
    """The rule-row sets an encapsulated package is evaluated under: general and/or activated metal, in that order."""
    return [w for w in ("general", "activated_metal") if any(i["wt"] == w for i in items)]


def enc_groups(ctx: Ctx, items: list) -> dict:
    """An encapsulated package is one waste form: one group over every item per rule-row set, each item evaluated
    under that set (own class included), labelled as the command labels it."""
    sets = enc_rule_sets(items)
    out = {}
    for w in sets:
        label = "encapsulated" if len(sets) == 1 else f"encapsulated_{w}_rules"
        out[label] = [{**i, "wt": w, "ev": (ev := ev_full(ctx, w, i["mass"], i["vol"], i["bq"])), "own": ev["class"]}
                      for i in items]
    if len(sets) > 1:
        hit("enc.mixed_rule_sets")
    return out


def enc_credit(ctx: Ctx, enc: dict, items: list, groups: dict) -> dict:
    """The volume and mass credit of BTP 3.3.4 and the Class C cap, before the groups are classified."""
    th = ctx.th
    total = float(enc["total_volume_cm3"])
    declared = enc.get("waste_volume_cm3")
    if declared is None:
        hit("enc.waste_volume_summed")
        waste = nsum(i["vol"] for i in items)
    else:
        hit("enc.waste_volume_declared")
        waste = float(declared)
    loading = waste / total
    dens_cap = min(th["encapsulant_max_density_g_cm3"], float(enc["encapsulant_density_g_cm3"]))
    if float(enc["encapsulant_density_g_cm3"]) < th["encapsulant_max_density_g_cm3"]:
        hit("enc.declared_density_lower")
    allowed = dens_cap * (total - waste)
    mass_enc = float(enc["encapsulant_mass_g"])
    hit("enc.density_cap_binds" if allowed < mass_enc else "enc.encapsulant_mass_binds")
    credit = min(mass_enc, allowed)
    total_mass = nsum(i["mass"] for i in items) + credit
    # one waste form: every item's activity over the whole container, under each rule-row set the items carry
    cap_classes = [ev_full(ctx, w, total_mass, total, sum_bq([i["bq"] for i in items]))["class"]
                   for w in enc_rule_sets(items)]
    cap_class = highest(cap_classes)
    cap_ok = cap_class in CLASSES
    if loading == th["encapsulation_min_loading"]:
        hit("enc.loading_at_threshold")
    if total == th["encapsulation_max_volume_cm3"]:
        hit("enc.total_at_max")
    if loading >= th["encapsulation_min_loading"]:
        if total > th["encapsulation_max_volume_cm3"]:
            status = "alternative_approach_required"
            hit("enc.alt_required")
        elif not cap_ok:
            status = "exceeds_class_c_cap"
            hit("enc.cap_exceeded")
        else:
            status = "credited"
            hit("enc.credited")
    elif not cap_ok:
        status = "exceeds_class_c_cap"
        hit("enc.cap_exceeded")
    else:
        status = "low_loading"
        hit("enc.low_loading")
    # the credit follows the loading and the volume; an exceeded Class C cap is reported with the credit it had
    kind = ("alternative" if total > th["encapsulation_max_volume_cm3"] else "full") \
        if loading >= th["encapsulation_min_loading"] else "low"
    if kind == "full":
        cvol, cmass = total, total_mass
    elif kind == "low":
        cvol = min(total, th["low_loading_max_volume_cm3"])
        cmass = min(total_mass, th["low_loading_max_mass_g"])
        hit("enc.low_volume_capped" if total > th["low_loading_max_volume_cm3"] else "enc.low_volume_total")
        hit("enc.low_mass_capped" if total_mass > th["low_loading_max_mass_g"] else "enc.low_mass_under")
    else:
        cvol = cmass = None
    return {"waste_volume_cm3": waste, "loading": loading, "credited_volume_cm3": cvol, "credited_mass_g": cmass,
            "status": status, "class": None, "class_c_cap": {"class": cap_class, "passes": cap_ok}}


def expected_step(ctx: Ctx, pkg: dict, step: int) -> dict:
    """The reported quantities of one target step (the shape of NStep), from the independent algorithm."""
    th = ctx.th
    items = []
    for it in pkg["items"]:
        bq = it["bq"][step]
        ev = ev_full(ctx, it["wt"], it["mass"], it["vol"], bq)
        small = it["vol"] < th["small_item_cm3"]
        hit("item.small" if small else "item.large")
        if it["vol"] == th["small_item_cm3"]:
            hit("item.exact_280")
        hit(f"item.{it['source']}_activity")
        if it["type"] == "cartridge_filter":
            hit("item.cartridge_filter")
        items.append({**{k: it[k] for k in ("id", "type", "wt", "mass", "vol", "section_of")}, "bq": bq, "ev": ev,
                      "own": ev["class"], "small": small, "total": nsum(bq[k] for k in sorted(bq))})
        if any(k in ctx.props and (membership(ctx, it["wt"], k)[0] or (None, False))[1]
               for k, v in bq.items() if v > 0.0):
            hit("item.mass_based_nuclide")
    groups = {}
    for it in items:
        groups.setdefault(it["type"], []).append(it)
    hit("group.multi_type" if len(groups) > 1 else "group.single_type")
    enc, den = None, None
    if pkg["enc"] is not None:
        if any(i["own"] == "unknown" for i in items):
            enc = {"waste_volume_cm3": SKIP, "loading": SKIP, "credited_volume_cm3": SKIP, "credited_mass_g": SKIP,
                   "status": SKIP, "class": SKIP, "class_c_cap": SKIP}
        else:
            enc = enc_credit(ctx, pkg["enc"], items, groups)
            if enc["credited_volume_cm3"] is not None:
                den = (enc["credited_mass_g"], enc["credited_volume_cm3"])
                groups = enc_groups(ctx, items)
            else:
                groups = {}
    records, sections, removed_own, mixtures = {}, {}, [], []
    for btype, gitems in groups.items():
        record, secs, own, mixture = run_group(ctx, btype, gitems, pkg["screen"], den)
        records[btype] = record
        for comp, sec in secs.items():
            sections.setdefault(comp, sec)  # an encapsulated package reports its first rule set's sections
        removed_own += own
        mixtures.append(mixture)
    if enc is not None and enc["class"] is None and enc["status"] in ("credited", "low_loading"):
        enc["class"] = highest(mixtures)
    package_class = highest(mixtures + removed_own) if mixtures + removed_own else "unknown"
    if enc is not None and enc["status"] == "exceeds_class_c_cap":
        enc["class"] = "above_class_c"
        package_class = "above_class_c"
    elif enc is not None and enc["status"] == "alternative_approach_required":
        package_class = "unknown"
    if package_class == "unknown":
        hit("pkg.unknown")
    elif removed_own and RANK[highest(removed_own)] > RANK[highest(mixtures)]:
        hit("pkg.class_from_removed")
    else:
        hit("pkg.class_from_group")
    return {"step": step, "t_s": pkg["t_s"].get(step, SKIP), "package_class": package_class,
            "items": {i["id"]: {"waste_type": i["type"], "volume_cm3": i["vol"], "mass_g": i["mass"],
                                "small": i["small"], "total_activity_bq": i["total"], "own_class": i["own"],
                                "activities_bq": dict(i["bq"]), "section_of": i["section_of"]} for i in items},
            "sections": sections, "groups": records, "encapsulation": enc}


def expected_package(ctx: Ctx, pkg: dict) -> dict:
    return {step: expected_step(ctx, pkg, step) for step in pkg["targets"]}


# --- adapters: the only code that names the fields of the Rust result ------------------------------------------------

NOT_CLAIMED_PHRASES = ["blending", "alternative approaches", "61.58", "site licence", "61.56", "centring", "shipment"]
NOT_TESTED = ("no_limit", "not_tested", "not_concern", "not_of_concern")


def by_key(rows, key: str):
    """{row[key]: row} for a list of objects, MISSING when rows is not a list or a key repeats or is absent."""
    if not isinstance(rows, list):
        return MISSING
    out = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get(key), (str, int)) or row[key] in out:
            return MISSING
        out[row[key]] = row
    return out


def safe_sorted(values):
    """A sorted list, or MISSING when the values cannot be ordered (a malformed result)."""
    try:
        return sorted(values)
    except TypeError:
        return MISSING


def sorted_list(value):
    return safe_sorted(value) if isinstance(value, list) else MISSING


def canon_name(name):
    if name == p145.ALPHA or not isinstance(name, str):
        return name
    try:
        return canon(name)
    except ValueError:
        return name


def a_activities(value):
    if not isinstance(value, dict):
        return MISSING
    return {canon_name(k): v for k, v in value.items()}


def a_items(rows):
    table = by_key(rows, "id")
    if table is MISSING:
        return MISSING
    return {i: {"waste_type": pick(r, "waste_type"), "volume_cm3": pick(r, "volume_cm3"), "mass_g": pick(r, "mass_g"),
                "small": pick(r, "small"), "total_activity_bq": pick(r, "total_activity_bq"),
                "own_class": pick(r, "own_class"), "activities_bq": a_activities(pick(r, "activities_bq")),
                "section_of": pick(r, "section_of")} for i, r in table.items()}


def a_sections(rows):
    table = by_key(rows, "component")
    if table is MISSING:
        return MISSING
    return {c: {"pieces": sorted_list(pick(r, "pieces")), "merged": pick(r, "merged"),
                "split_out": sorted_list(pick(r, "split_out"))} for c, r in table.items()}


def a_pool(value):
    if value is None or value is MISSING:
        return value
    return {"items": sorted_list(pick(value, "items")), "table2_sum": pick(value, "table2_sum"),
            "passes": pick(value, "passes")}


def a_others(rows):
    if not isinstance(rows, list):
        return MISSING
    out = {}
    for row in rows:
        name = canon_name(pick(row, "nuclide"))
        out[name] = pick(row, "limit_met")
    return out


def a_tests(rows):
    table = by_key(rows, "id")
    if table is MISSING:
        return MISSING
    out = {}
    for i, t in table.items():
        pg = pick(t, "primary_gamma")
        failing = pick(pg, "failing")
        out[i] = {"passes": pick(t, "passes"),
                  "primary_gamma": {"limit_met": pick(pg, "limit_met"), "table2_sum": pick(pg, "table2_sum"),
                                    "factor": pick(pg, "factor"),
                                    "failing": safe_sorted(canon_name(n) for n in failing)
                                    if isinstance(failing, list) else MISSING},
                  "others": a_others(pick(t, "others"))}
    return out


def a_candidates(rows):
    table = by_key(rows, "class")
    if table is MISSING:
        return MISSING
    return {c: {"small_pool": a_pool(pick(r, "small_pool")), "item_tests": a_tests(pick(r, "item_tests")),
                "removed": sorted_list(pick(r, "removed")), "remainder_class": pick(r, "remainder_class"),
                "remainder_volume_cm3": pick(r, "remainder_volume_cm3"),
                "remainder_mass_g": pick(r, "remainder_mass_g"), "qualifies": pick(r, "qualifies")}
            for c, r in table.items()}


def a_removed(rows):
    table = by_key(rows, "id")
    if table is MISSING:
        return MISSING
    return {i: pick(r, "own_class") for i, r in table.items()}


def a_groups(rows):
    table = by_key(rows, "waste_type")
    if table is MISSING:
        return MISSING
    return {t: {"items": sorted_list(pick(g, "items")), "screen_applied": pick(g, "screen_applied"),
                "restrictive_table": pick(g, "restrictive_table"), "table1_sum": pick(g, "table1_sum"),
                "table2_sum": pick(g, "table2_sum"), "primary_gamma_share": pick(g, "primary_gamma_share"),
                "primary_gamma_controlling": pick(g, "primary_gamma_controlling"),
                "candidates": a_candidates(pick(g, "candidates")), "mixture_class": pick(g, "mixture_class"),
                "removed_items": a_removed(pick(g, "removed_items"))} for t, g in table.items()}


def a_encapsulation(value):
    if value is None or value is MISSING:
        return value
    cap = pick(value, "class_c_cap")
    return {"waste_volume_cm3": pick(value, "waste_volume_cm3"), "loading": pick(value, "loading"),
            "credited_volume_cm3": pick(value, "credited_volume_cm3"),
            "credited_mass_g": pick(value, "credited_mass_g"), "status": pick(value, "status"),
            "class": pick(value, "class"),
            "class_c_cap": {"class": pick(cap, "class"), "passes": pick(cap, "passes")}}


def a_step(block: dict) -> dict:
    return {"step": pick(block, "step"), "t_s": pick(block, "t_s"), "package_class": pick(block, "package_class"),
            "items": a_items(pick(block, "items")), "sections": a_sections(pick(block, "sections")),
            "groups": a_groups(pick(block, "groups")), "encapsulation": a_encapsulation(pick(block, "encapsulation"))}


def a_top(result: dict) -> dict:
    return {name: pick(result, name) for name in ("schema", "spec_sha256", "rule_pack_sha256", "btp_sha256", "screen",
                                                  "encapsulated", "readings", "not_claimed")}


def a_steps(result: dict) -> dict:
    rows = pick(result, "steps")
    out = {}
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict) and isinstance(row.get("step"), int):
                out[row["step"]] = a_step(row)
    return out


# --- comparison --------------------------------------------------------------------------------------------------

def disc(ck: Checks, label: str, exp, got) -> None:
    """Exact comparison of a class, status, flag or count; an expected None accepts a null or an absent field."""
    if exp is SKIP:
        return
    if exp is None:
        ck.flag(label, got is None or got is MISSING, None, got)
        return
    if isinstance(exp, bool):
        if ck.has(label, got):
            ck.flag(label, got is exp, exp, got)
        return
    ck.eq(label, exp, got)


def num(ck: Checks, label: str, exp, got) -> None:
    """A number within 1e-12 relative; an expected None accepts a null or an absent field."""
    if exp is SKIP:
        return
    if exp is None:
        ck.flag(label, got is None or got is MISSING, None, got)
        return
    if got is MISSING:
        ck.has(label, got)
        return
    ck.flag(label, isinstance(got, (int, float)) and not isinstance(got, bool) and rel_ok(exp, got), exp, got)


def same_set(ck: Checks, label: str, exp, got) -> bool:
    if not ck.has(label, got):
        return False
    return ck.flag(label, safe_sorted(exp) == safe_sorted(got) != MISSING, safe_sorted(exp), safe_sorted(got))


def compare_tests(ck: Checks, tag: str, exp: dict, got) -> None:
    if not same_set(ck, f"{tag} item_tests ids", exp, got):
        return
    for iid, et in exp.items():
        gt = got[iid]
        t = f"{tag} test {iid}"
        disc(ck, f"{t} passes", et["passes"], gt["passes"])
        epg, gpg = et["primary_gamma"], gt["primary_gamma"]
        disc(ck, f"{t} limit_met", epg["limit_met"], gpg["limit_met"])
        num(ck, f"{t} table2_sum", epg["table2_sum"], gpg["table2_sum"])
        num(ck, f"{t} factor", epg["factor"], gpg["factor"])
        disc(ck, f"{t} failing", epg["failing"], gpg["failing"])
        go = gt["others"]
        if ck.has(f"{t} others", go):
            for name, met in et["others"].items():
                if ck.has(f"{t} other {name}", go.get(name, MISSING)):
                    disc(ck, f"{t} other {name}", met, go[name])
            extra = [n for n, v in go.items() if n not in et["others"] and v not in NOT_TESTED]
            ck.flag(f"{t} no other nuclide is tested", not extra, [], extra)


def compare_candidate(ck: Checks, tag: str, ec: dict, gc: dict) -> None:
    ep, gp = ec["small_pool"], gc["small_pool"]
    if ep is None:
        ck.flag(f"{tag} no small pool", gp is None or gp is MISSING, None, gp)
    elif ck.has(f"{tag} small_pool", gp):
        if gp is None:
            ck.flag(f"{tag} small_pool present", False, "a pool", None)
        else:
            same_set(ck, f"{tag} pool items", ep["items"], gp["items"])
            num(ck, f"{tag} pool table2_sum", ep["table2_sum"], gp["table2_sum"])
            disc(ck, f"{tag} pool passes", ep["passes"], gp["passes"])
    compare_tests(ck, tag, ec["item_tests"], gc["item_tests"])
    same_set(ck, f"{tag} removed", ec["removed"], gc["removed"])
    disc(ck, f"{tag} remainder_class", ec["remainder_class"], gc["remainder_class"])
    num(ck, f"{tag} remainder_volume_cm3", ec["remainder_volume_cm3"], gc["remainder_volume_cm3"])
    num(ck, f"{tag} remainder_mass_g", ec["remainder_mass_g"], gc["remainder_mass_g"])
    disc(ck, f"{tag} qualifies", ec["qualifies"], gc["qualifies"])


def compare_group(ck: Checks, tag: str, eg: dict, gg: dict) -> None:
    disc(ck, f"{tag} mixture_class", eg["mixture_class"], gg["mixture_class"])
    if eg["items"] is not SKIP:
        same_set(ck, f"{tag} items", eg["items"], gg["items"])
    disc(ck, f"{tag} screen_applied", eg["screen_applied"], gg["screen_applied"])
    for name in ("restrictive_table", "primary_gamma_controlling"):
        disc(ck, f"{tag} {name}", eg[name], gg[name])
    for name in ("table1_sum", "table2_sum", "primary_gamma_share"):
        num(ck, f"{tag} {name}", eg[name], gg[name])
    if eg["removed_items"] is not SKIP and ck.has(f"{tag} removed_items", gg["removed_items"]):
        ck.flag(f"{tag} removed_items", eg["removed_items"] == gg["removed_items"], eg["removed_items"],
                gg["removed_items"])
    if eg["candidates"] is SKIP or eg["screen_applied"] is not None:
        return
    gc = gg["candidates"]
    if not ck.has(f"{tag} candidates", gc):
        return
    first = next((n for n in CLASSES if eg["candidates"][n]["qualifies"]), "C")
    needed = CLASSES[:CLASSES.index(first) + 1]
    ck.flag(f"{tag} candidate classes cover A to {first}", all(n in gc for n in needed), needed, sorted(gc))
    for name in CLASSES:
        if name in gc:
            compare_candidate(ck, f"{tag} cand {name}", eg["candidates"][name], gc[name])


def compare_encapsulation(ck: Checks, tag: str, ee, ge) -> None:
    if ee is None:
        ck.flag(f"{tag} no encapsulation block", ge is None or ge is MISSING, None, ge)
        return
    if not ck.has(f"{tag} encapsulation", ge) or ge is None:
        ck.flag(f"{tag} encapsulation present", ge is not None, "a block", ge)
        return
    for name in ("waste_volume_cm3", "loading", "credited_volume_cm3", "credited_mass_g"):
        num(ck, f"{tag} enc {name}", ee[name], ge[name])
    disc(ck, f"{tag} enc status", ee["status"], ge["status"])
    disc(ck, f"{tag} enc class", ee["class"], ge["class"])
    if ee["class_c_cap"] is not SKIP:
        disc(ck, f"{tag} enc cap class", ee["class_c_cap"]["class"], ge["class_c_cap"]["class"])
        disc(ck, f"{tag} enc cap passes", ee["class_c_cap"]["passes"], ge["class_c_cap"]["passes"])


def compare_step(ck: Checks, label: str, exp: dict, got: dict) -> None:
    disc(ck, f"{label} package_class", exp["package_class"], got["package_class"])
    num(ck, f"{label} t_s", exp["t_s"], got["t_s"])
    items = got["items"]
    if same_set(ck, f"{label} item ids", exp["items"], items):
        for iid, e in exp["items"].items():
            g, tag = items[iid], f"{label} item {iid}"
            for name in ("waste_type", "small", "own_class", "section_of"):
                disc(ck, f"{tag} {name}", e[name], g[name])
            for name in ("volume_cm3", "mass_g", "total_activity_bq"):
                num(ck, f"{tag} {name}", e[name], g[name])
            if ck.has(f"{tag} activities_bq", g["activities_bq"]):
                en = {k: v for k, v in e["activities_bq"].items() if v != 0.0}
                gn = {k: v for k, v in g["activities_bq"].items() if v != 0.0}
                ck.flag(f"{tag} activity nuclides", set(en) == set(gn), sorted(set(en) - set(gn))[:5],
                        sorted(set(gn) - set(en))[:5])
                bad = [(k, en[k], gn[k]) for k in en if k in gn
                       and not (isinstance(gn[k], (int, float)) and rel_ok(en[k], gn[k]))]
                ck.flag(f"{tag} activities_bq", not bad, "within 1e-12 relative", bad[:3])
    sections = got["sections"]
    if same_set(ck, f"{label} section components", exp["sections"], sections):
        for comp, e in exp["sections"].items():
            g, tag = sections[comp], f"{label} section {comp}"
            same_set(ck, f"{tag} pieces", e["pieces"], g["pieces"])
            disc(ck, f"{tag} merged", e["merged"], g["merged"])
            if e["split_out"] is not SKIP:
                same_set(ck, f"{tag} split_out", e["split_out"], g["split_out"])
    groups = got["groups"]
    if same_set(ck, f"{label} group waste types", exp["groups"], groups):
        for btype, eg in exp["groups"].items():
            compare_group(ck, f"{label} group {btype}", eg, groups[btype])
    compare_encapsulation(ck, label, exp["encapsulation"], got["encapsulation"])


def compare_top(ck: Checks, label: str, pkg: dict, spec_path: Path, got: dict) -> None:
    disc(ck, f"{label} schema", RESULT_SCHEMA, got["schema"])
    disc(ck, f"{label} screen", pkg["screen"], got["screen"])
    disc(ck, f"{label} encapsulated", pkg["enc"] is not None, got["encapsulated"])
    # informational: the contract does not say whether the digest is of the file bytes or of a canonical form
    ck.note(f"{label} spec_sha256_is_file_sha256", got["spec_sha256"] == sha(spec_path))
    disc(ck, f"{label} rule_pack_sha256", sha(RULES_FILE), got["rule_pack_sha256"])
    if BTP_FILE.is_file():
        disc(ck, f"{label} btp_sha256", sha(BTP_FILE), got["btp_sha256"])
    readings = got["readings"]
    if ck.has(f"{label} readings", readings):
        ck.flag(f"{label} readings are a nonempty list of strings",
                isinstance(readings, list) and bool(readings) and all(isinstance(r, str) for r in readings),
                "nonempty list", readings)
    claimed = got["not_claimed"]
    if ck.has(f"{label} not_claimed", claimed):
        text = [c.lower() for c in claimed if isinstance(c, str)] if isinstance(claimed, list) else []
        missing = [p for p in NOT_CLAIMED_PHRASES if not any(p in t for t in text)]
        ck.flag(f"{label} not_claimed names every exclusion", not missing, [], missing)


def compare_result(ck: Checks, label: str, pkg: dict, expected: dict, spec_path: Path, result: dict) -> None:
    compare_top(ck, label, pkg, spec_path, a_top(result))
    steps = a_steps(result)
    if same_set(ck, f"{label} steps", expected, steps):
        for step, exp in expected.items():
            compare_step(ck, f"{label} step {step}", exp, steps[step])


# --- the contract-shaped document built from an expected result (selftest only) --------------------------------------

def unskip(value):
    return None if value is SKIP else value


def contract_candidate(name: str, c: dict) -> dict:
    pool = c["small_pool"]
    return {"class": name, "small_pool": pool,
            "item_tests": [{"id": i, "passes": t["passes"], "primary_gamma": dict(t["primary_gamma"]),
                            "others": [{"nuclide": n, "limit_met": m} for n, m in t["others"].items()]}
                           for i, t in c["item_tests"].items()],
            "removed": list(c["removed"]), "remainder_class": c["remainder_class"],
            "remainder_volume_cm3": c["remainder_volume_cm3"], "remainder_mass_g": c["remainder_mass_g"],
            "qualifies": c["qualifies"]}


def contract_group(btype: str, g: dict) -> dict:
    candidates = g["candidates"]
    removed = g["removed_items"]
    return {"waste_type": btype, "items": unskip(g["items"]), "screen_applied": unskip(g["screen_applied"]),
            "restrictive_table": unskip(g["restrictive_table"]), "table1_sum": unskip(g["table1_sum"]),
            "table2_sum": unskip(g["table2_sum"]), "primary_gamma_share": unskip(g["primary_gamma_share"]),
            "primary_gamma_controlling": unskip(g["primary_gamma_controlling"]),
            "candidates": [] if candidates is SKIP else [contract_candidate(n, c) for n, c in candidates.items()],
            "mixture_class": g["mixture_class"],
            "removed_items": [] if removed is SKIP else [{"id": i, "own_class": c} for i, c in removed.items()]}


def contract_step(n: dict) -> dict:
    enc = n["encapsulation"]
    if enc is not None:
        enc = {k: unskip(v) for k, v in enc.items()}
    return {"step": n["step"], "t_s": unskip(n["t_s"]), "package_class": n["package_class"],
            "items": [{"id": i, **copy.deepcopy(v)} for i, v in n["items"].items()],
            "sections": [{"component": c, "pieces": list(s["pieces"]), "merged": unskip(s["merged"]),
                          "split_out": unskip(s["split_out"])} for c, s in n["sections"].items()],
            "groups": [contract_group(t, g) for t, g in n["groups"].items()], "encapsulation": enc}


def contract_result(pkg: dict, expected: dict, spec_sha: str) -> dict:
    """A result document in the field names of the contract, built from the independent result. Used only by the self
    test, and as the reference for the schema this checker assumes."""
    return {"schema": RESULT_SCHEMA, "spec_sha256": spec_sha, "rule_pack_sha256": sha(RULES_FILE),
            "btp_sha256": sha(BTP_FILE) if BTP_FILE.is_file() else "0" * 64, "screen": pkg["screen"],
            "encapsulated": pkg["enc"] is not None, "steps": [contract_step(expected[s]) for s in pkg["targets"]],
            "readings": ["reading one", "reading two"],
            "not_claimed": ["blending (BTP 3.2)", "alternative approaches (BTP 3.8)", "10 CFR 61.58",
                            "site licence conditions", "10 CFR 61.56 stability",
                            "centring and compatibility documentation",
                            "not a shipment classification or disposal acceptance"]}


# --- synthetic packages -------------------------------------------------------------------------------------------------

AM, CM, DRC, SS, CF = "activated_metal", "contaminated_material", "design_radioactivity_component", "sealed_source", \
    "cartridge_filter"
BIG = 1.0e6  # cm3 (1 m3): the volume at which Ci/m3 and Bq/Ci differ by exact factors
DENSITY = {AM: 7.9}  # g/cm3 of the default item mass; every other type 2.0
STEP2 = 0.4  # activity of the second target step relative to the first
SYN_CTX: list = []


def syn_ctx() -> Ctx:
    if not SYN_CTX:
        SYN_CTX.append(Ctx(SYN_PROPS))
    return SYN_CTX[0]


def unit_row(wt: str, key: str):
    """The row whose unit a concentration of `key` is stated in: its Table 1 row, else its Table 2 row, else None."""
    t1, t2 = membership(syn_ctx(), wt, key)
    return t1[0] if t1 else t2


def bq_conc(wt: str, key: str, conc: float, vol: float, mass: float) -> float:
    """Activity in Bq of a concentration in the unit of the nuclide's row (Ci/m3 over the volume, nCi/g over the mass)."""
    row = unit_row(wt, key)
    if row is None:
        raise ValueError(f"{key} has no row for {wt}")
    ci = get_rules().ci_bq
    return conc * (ci / 1.0e6) * vol if row["unit"] == "Ci/m3" else conc * (ci / 1.0e9) * mass


def mk(iid: str, vol: float, conc: dict | None = None, bq: dict | None = None, *, kind: str = AM, mass: float | None = None,
       section: str | None = None, source: str = "declared", d2: float = STEP2) -> dict:
    """An item: concentrations (in the unit of each nuclide's row) and/or activities in Bq at the first target step."""
    wt = eval_type(kind)
    mass = float(vol) * DENSITY.get(kind, 2.0) if mass is None else float(mass)
    first = {k: bq_conc(wt, k, c, vol, mass) for k, c in (conc or {}).items()}
    first.update(bq or {})
    return {"id": iid, "kind": kind, "vol": float(vol), "mass": mass, "section": section, "source": source,
            "bq": [first, {k: v * d2 for k, v in first.items()}]}


def run_result_doc(item: dict, targets) -> dict:
    """A run result (the input of `actinv waste`) holding the item's activities per gram at the target steps."""
    steps = []
    for i, s in enumerate(targets):
        steps.append({"step": s, "t_s": 1.0e8 * (i + 1),
                      "activity_Bq_per_g": {k: v / item["mass"] for k, v in item["bq"][i].items()}})
    return {"entry_point": "p147", "mode": "coupled", "steps": steps}


def enc_block(total, mass, density, waste=None) -> dict:
    return {"total_volume_cm3": float(total), "encapsulant_mass_g": float(mass),
            "encapsulant_density_g_cm3": float(density), "waste_volume_cm3": None if waste is None else float(waste)}


def make_package(name: str, items: list, *, screen: str = "none", enc: dict | None = None, targets=(1, 2)) -> dict:
    """{name, spec, files}: the spec document and the run results its result-activity items name."""
    files, raws = {}, []
    for it in items:
        raw = {"id": it["id"], "waste_type": it["kind"], "mass_g": it["mass"]}
        raw["envelope_volume_cm3" if it["kind"] == CF else "displaced_volume_cm3"] = it["vol"]
        if it["section"]:
            raw["section_of"] = it["section"]
        if it["source"] == "result":
            fname = f"{name}_{it['id']}.json"
            files[fname] = run_result_doc(it, targets)
            raw["activity"] = {"result": fname}
        else:
            raw["activity"] = {"declared_bq": {str(s): it["bq"][i] for i, s in enumerate(targets)}}
        raws.append(raw)
    spec = {"schema": SPEC_SCHEMA, "rules": BUNDLED, "targets": list(targets),
            "nuclide_properties": props_json(SYN_PROPS), "screen": screen, "encapsulation": enc, "items": raws}
    return {"name": name, "spec": spec, "files": files}


def write_package(pkg: dict, folder: Path) -> Path:
    """The spec and its result files in `folder`; returns the spec path."""
    folder.mkdir(parents=True, exist_ok=True)
    for fname, doc in pkg["files"].items():
        write_json(folder / fname, doc)
    path = folder / "spec.json"
    write_json(path, pkg["spec"])
    return path


def expected_of(spec: dict, base: Path | None, files: dict | None = None):
    """(normalized package, independent expected result per step) for a spec, reading result files from `files` or `base`."""
    pkg = normalize_package(spec, base, files)
    return pkg, expected_package(Ctx(pkg["props"]), pkg)


def hits_of(pkg: dict) -> Counter:
    """The branches one package reaches (the module counter is restored)."""
    global HITS
    saved, HITS = HITS, Counter()
    try:
        expected_of(pkg["spec"], None, pkg["files"])
        return HITS
    finally:
        local, HITS = HITS, saved
        HITS.update(local)


# --- the directed packages: each is built to reach named branches (by hand analysis; the coverage report is the proof) ----

def directed() -> list:
    P = []
    add = P.append
    # clean Class A, Co60 below the concern level; result-file activity; Table 2 ties and primary gammas control
    add(make_package("d01_clean_a", [mk("a1", 1000, {"Co60": 5}, source="result"), mk("a2", 1000, {"Co60": 5, "Ni63": 0.1}),
                                     mk("a3", 2000, {"Co60": 3})]))
    # CFR Tables 1 and 2 give Class A, Table 1 has the greater sum (tie, Table 1); Tc99 has no BTP Table 3 entry
    add(make_package("d02_tie_table1", [mk("t1", 1000, {"Tc99": 0.2, "Co60": 5}), mk("t2", 1000, {"Tc99": 0.2, "Co60": 5})]))
    # Table 1 gives Class C, Table 2 Class A: Table 1 is restrictive and no primary gamma controls (Factor of 10); C qualifies
    add(make_package("d03_table1_c", [mk("c1", 1000, {"Tc99": 0.5, "Cs137": 0.3}), mk("c2", 1000, {"Tc99": 0.5, "Cs137": 0.3})]))
    # Cs137 at 20 Ci/m3 in items of 300 cm3: Class B mixture; Co60 and H3 have no Class B limit
    add(make_package("d04_cs_b", [mk(f"s{i}", 300, {"Cs137": 20, "Co60": 3, "H3": 1}) for i in (1, 2, 3)]))
    # small items pooled under BTP Table 2
    add(make_package("d05_pool_pass", [mk(f"p{i}", 100, {"Cs137": 5}) for i in (1, 2, 3)] + [mk("big", 5000, {"Co60": 5})]))
    add(make_package("d06_pool_fail", [mk(f"p{i}", 100, {"Cs137": 50}) for i in (1, 2, 3)]))
    # Factor of 2 (primary gamma controlling) and Factor of 10 (not controlling) after BTP Table 2 fails
    add(make_package("d07_factor2", [mk("f1", 6000, bq={"Cs137": 3.0e8})]))
    add(make_package("d08_factor10", [mk("f1", 6000, bq={"Cs137": 3.0e8}), mk("f2", 6000, {"Tc99": 1.0})]))
    # a primary gamma below the concern level that still exceeds BTP Table 2 (vacuous Factor test)
    add(make_package("d09_vacuous", [mk("n1", 1.0e7, bq={"Nb94": 5.0e7})]))
    # primary gamma and Ni59 with no CFR row for general waste; no CFR sum at all
    add(make_package("d10_unlisted", [mk("s1", 400, bq={"Nb94": 5.0e7, "Ni59": 1.0e9}, kind=SS)]))
    # BTP Table 3 exceeded: Factor of 10 met, and both fail
    add(make_package("d11_ni63_factor10", [mk("n1", 50000, bq={"Ni63": 3.0e11})]))
    add(make_package("d12_ni63_fail", [mk("n1", 50000, bq={"Ni63": 7.4e11})]))
    # alpha transuranics (mass-based group), Pu241, Co60 in a general item
    add(make_package("d13_tru", [mk("u1", 1000, {"Pu239": 2.0, "Am241": 1.0, "Pu241": 50, "Co60": 3}, kind=CM)]))
    # a nuclide without nuclide properties: the group and the package are unknown
    add(make_package("d14_unknown", [mk("k1", 1000, {"Co60": 5}), mk("k2", 1000, bq={"Zn65": 1.0e6, "Co60": 1.0e6})]))
    # five waste types; an item of exactly 280 cm3; a cartridge filter (envelope volume)
    add(make_package("d15_types", [mk("m1", 280.0, {"Co60": 5}), mk("c1", 150, {"Co60": 5}, kind=CM),
                                   mk("f1", 500, {"Cs137": 0.5}, kind=CF), mk("s1", 120, {"Cs137": 2}, kind=SS),
                                   mk("dr1", 900, {"Co60": 5}, kind=DRC)]))
    # sectioning: merged pieces and unsectioned items (a lone piece is refused, E5)
    add(make_package("d16_sec_merged", [mk("p1", 150, {"Co60": 5}, section="comp-a"), mk("p2", 200, {"Co60": 5}, section="comp-a"),
                                        mk("q1", 300, {"Co60": 5}), mk("o1", 400, {"Co60": 5})]))
    add(make_package("d17_sec_table3", [mk("p1", 10000, bq={"Ni63": 3.0e11}, section="c"),
                                        mk("p2", 10000, bq={"Ni63": 1.0e9}, section="c")]))
    add(make_package("d18_sec_all_split", [mk("p1", 1.0e8, bq={"Ni63": 6.0e13}, section="c"),
                                           mk("p2", 1.0e8, bq={"Ni63": 6.0e13}, section="c")]))
    add(make_package("d19_sec_table2_small", [mk("p1", 100, bq={"Cs137": 3.0e8}, section="c"),
                                              mk("p2", 20000, bq={"Co60": 1.0e6}, section="c")]))
    # the screens of BTP 3.3.2.1
    add(make_package("d20_screen37_applied", [mk(f"s{i}", 200 + 150 * i, bq={"Co60": 1.0e6 * i}) for i in (1, 2, 3)],
                     screen="activity_37mbq"))
    add(make_package("d21_screen37_fallthrough", [mk("s1", 1000, {"Co60": 5}), mk("s2", 1000, bq={"Co60": 1.0e6})],
                     screen="activity_37mbq"))
    add(make_package("d22_screen_highest", [mk("h1", 1000, {"Co60": 5}), mk("h2", 500, {"Cs137": 20}),
                                            mk("h3", 800, {"Tc99": 0.5})], screen="highest_item"))
    # every item passes at every class and the average is above Class C: no candidate class qualifies
    add(make_package("d23_above_c", [mk(f"a{i}", 1000, {"Tc99": 2.0, "I129": 0.05}) for i in (1, 2)]))
    # a hot sealed source removed from its own group; the package takes its class
    add(make_package("d24_removed_hot", [mk(f"s{i}", 300, {"Cs137": 20}) for i in (1, 2, 3)]
                     + [mk("src", 300, bq={"Cs137": 5.0e11}, kind=SS)]))
    # encapsulation (three 1000 cm3 pieces: 3000 cm3 of waste)
    three = [mk(f"w{i}", 1000, {"Co60": 5}) for i in (1, 2, 3)]
    add(make_package("d30_enc_credited", three, enc=enc_block(20000, 40000, 2.0)))
    add(make_package("d31_enc_low", three, enc=enc_block(100000, 100000, 2.5, 10000)))
    add(make_package("d32_enc_low_capped", three, enc=enc_block(1.0e6, 2.0e6, 2.5, 1.0e5)))
    add(make_package("d33_enc_alt", three, enc=enc_block(1.0e7, 1.0e7, 2.5, 2.0e6)))
    add(make_package("d34_enc_cap", [mk("w1", 1000, {"Co60": 5}), mk("src", 300, bq={"Cs137": 1.0e17}, kind=SS)],
                     enc=enc_block(20000, 30000, 2.0)))
    add(make_package("d35_enc_at_14", three, enc=enc_block(100000, 50000, 2.0, 14000)))
    add(make_package("d36_enc_at_max", three, enc=enc_block(9372876.221952, 1.0e6, 2.0, 2.0e6)))
    add(make_package("d37_enc_multi", three + [mk("src", 300, bq={"Cs137": 5.0e8}, kind=SS)], enc=enc_block(30000, 50000, 2.3)))
    return P


# --- the seeded random packages and the coverage-guided extension -------------------------------------------------------

KINDS = [AM, CM, SS, DRC, CF]
KIND_WEIGHTS = [55, 15, 10, 8, 12]
VOLUMES = [8.0, 40.0, 120.0, 250.0, 279.999, 280.0, 281.0, 400.0, 900.0, 3000.0, 9000.0, 40000.0]
NUCLIDES = sorted(SYN_PROPS)
BATTERY_SEED = 147001
RANDOM_BASE = 30
EXTENSION_LIMIT = 3000


def reference_conc(wt: str, key: str):
    """The largest limit of the nuclide's row (the Class C value), or None when it has no row for the waste type."""
    row = unit_row(wt, key)
    if row is None:
        return None
    return max(x for x in row["limits"] if x is not None)


def random_item(rng: random.Random, iid: str, kind: str) -> dict:
    wt = eval_type(kind)
    vol = rng.choice(VOLUMES) * rng.choice([1.0, 1.0, 1.0, 10.0, 100.0])
    mass = vol * DENSITY.get(kind, 2.0) * rng.choice([0.5, 1.0, 1.0, 2.0])
    conc, bq = {}, {}
    for key in rng.sample(NUCLIDES, rng.randint(1, 4)):
        ref = reference_conc(wt, key)
        if ref is None:
            bq[key] = 10.0 ** rng.uniform(6.0, 12.0)
        else:
            conc[key] = ref * 10.0 ** rng.uniform(-4.5, -0.2)
    return mk(iid, vol, conc, bq, kind=kind, mass=mass, source="result" if rng.random() < 0.2 else "declared",
              d2=rng.choice([0.1, STEP2, 0.9]))


def random_package(rng: random.Random, name: str) -> dict:
    kinds = rng.choices(KINDS, weights=KIND_WEIGHTS, k=rng.choice([1, 2, 3, 3, 4, 5, 6]))
    items = [random_item(rng, f"i{n}", kind) for n, kind in enumerate(kinds)]
    for kind in sorted(set(kinds)):
        same = [it for it in items if it["kind"] == kind]
        if len(same) >= 2 and rng.random() < 0.3:
            for it in same[:rng.choice([2, 2, 3])]:
                it["section"] = f"c-{kind[:3]}"
    screen = rng.choices(["none", "activity_37mbq", "highest_item"], weights=[70, 15, 15])[0]
    enc = None
    if rng.random() < 0.25:
        waste = sum(it["vol"] for it in items)
        total = waste / rng.choice([0.03, 0.1, 0.139, 0.2, 0.3, 0.8])
        if rng.random() < 0.2:
            total = rng.choice([9372876.221952, 1.0e7])
        density = rng.choice([1.5, 2.0, 2.4, 2.5, 3.0])
        declared = None if rng.random() < 0.6 else min(total, waste * rng.uniform(0.8, 1.2))
        enc = enc_block(total, total * density * rng.uniform(0.3, 1.2), density, declared)
    # the screens are not available with encapsulation (BTP Figure 5 has no screening step; refused, E5)
    return make_package(name, items, screen="none" if enc else screen, enc=enc)


def coverage_of(counts: Counter) -> list:
    return [b for b in BRANCHES if counts[b] == 0]


def build_battery() -> tuple[list, dict]:
    """(packages, report): the directed packages, RANDOM_BASE seeded random packages, then random packages kept only when
    they reach a branch not yet reached, until every branch is reached or EXTENSION_LIMIT candidates are tried."""
    packages, errors, total = [], [], Counter()

    def admit(pkg: dict, force: bool) -> bool:
        try:
            counts = hits_of(pkg)
        except Exception as error:  # a model failure on a generated package is reported, not hidden
            errors.append({"package": pkg["name"], "error": f"{type(error).__name__}: {error}"})
            return False
        if force or any(counts[b] and not total[b] for b in BRANCHES):
            packages.append(pkg)
            total.update(counts)
            return True
        return False

    for pkg in directed():
        admit(pkg, True)
    rng = random.Random(BATTERY_SEED)
    for n in range(RANDOM_BASE):
        admit(random_package(rng, f"r{n:03d}"), True)
    tried = 0
    while coverage_of(total) and tried < EXTENSION_LIMIT:
        admit(random_package(rng, f"x{tried:04d}"), False)
        tried += 1
    report = {"packages": len(packages), "directed": sum(1 for p in packages if p["name"].startswith("d")),
              "random": sum(1 for p in packages if p["name"].startswith("r")),
              "extension": sum(1 for p in packages if p["name"].startswith("x")), "extension_candidates_tried": tried,
              "seed": BATTERY_SEED, "branch_hits": {b: total[b] for b in BRANCHES}, "uncovered": coverage_of(total),
              "generator_errors": errors}
    return packages, report


# --- running the candidate and comparing ---------------------------------------------------------------------------

strip_private = p145.strip_private
ACT_TIMEOUT = int(os.environ.get("P147_ACT_TIMEOUT", "3600"))
SIDES = ("below", "at", "above")
# the activation runs behind the committed package specs: result file name -> problem
ACT_RESULTS = {"act_lo_result.json": "a_lo_problem.json", "act_hi_result.json": "a_hi_problem.json",
               "act_nbco_result.json": "a_nbco_problem.json"}
ACT_CASES = {"A1": "a1_steel_pieces.json", "A2": "a2_nbco_mix.json", "A3": "a3_encapsulated_low.json",
             "A4": "a4_encapsulated_credited.json", "A5": "a5_screen_37mbq.json", "A6": "a6_screen_highest.json"}


def run_package(stage: Path, spec: str = "spec.json", out: str = "out.json") -> dict:
    return p145.run_child([BINARY, "waste", "package", spec, out], stage, TIMEOUT)


def run_classify(stage: Path, spec: str, out: str) -> dict:
    return p145.run_child([BINARY, "waste", spec, out], stage, TIMEOUT)


def run_activation(stage: Path, problem: str, out: str) -> dict:
    return p145.run_child([BINARY, "run", problem, out], stage, ACT_TIMEOUT)


def check_stage(name: str, stage: Path, spec_name: str = "spec.json", out_name: str = "out.json"):
    """Compare the reported result in `stage` with the independent recomputation of the spec it was made from."""
    spec_path = stage / spec_name
    pkg, expected = expected_of(load_json(spec_path), stage)
    result = load_json(stage / out_name)
    ck = Checks()
    compare_result(ck, name, pkg, expected, spec_path, result)
    return ck, pkg, expected, result


def summarize(ck: Checks) -> dict:
    out = ck.report()
    return {"checks": out["checks"], "failed": out["failed"], "failures": out["failures"][:8],
            "missing_fields": out["missing_fields"], "notes": out["notes"]}


def clean(summary: dict) -> bool:
    return summary["checks"] > 0 and summary["failed"] == 0 and not summary["missing_fields"]


# --- activation runs ---------------------------------------------------------------------------------------------

def activation_stage(before: dict) -> tuple[Path, list]:
    """The staging folder holding the three `actinv run` results behind the activation packages; an earlier folder is
    reused when it was made by this binary from these problems. Returns (folder, run records or [])."""
    stage = WORK / "act"
    wanted = {"binary_sha256": before["binary_sha256"], "problems": {r: sha(INPUTS / p) for r, p in ACT_RESULTS.items()}}
    meta = stage / "meta.json"
    if meta.exists() and all((stage / r).exists() for r in ACT_RESULTS):
        have = load_json(meta)
        if {k: have.get(k) for k in wanted} == wanted and all(rc == 0 for rc in have.get("rcs", [1])):
            return stage, []
    fresh_dir(stage, data=True)
    runs, rcs = [], []
    for result, problem in ACT_RESULTS.items():
        write_json(stage / problem, strip_private(load_json(INPUTS / problem)))
        child = run_activation(stage, problem, result)
        runs.append({"result": result, **brief(child)})
        rcs.append(child["rc"])
    write_json(meta, {**wanted, "rcs": rcs})
    return stage, runs


def stage_activation_case(key: str, stage: Path, expand: bool = True) -> dict:
    """The committed package spec with the nuclide-property token replaced by the properties of the decay files named by
    the LO problem; written to <stage>/<key>.json."""
    source = INPUTS / ACT_CASES[key]
    spec = strip_private(load_json(source))
    info = {"case": key, "source_sha256": sha(source)}
    if expand and spec.get("nuclide_properties") == PROPERTIES_TOKEN:
        props = p146.property_map(load_json(INPUTS / "a_lo_problem.json")["decay"], stage)
        spec["nuclide_properties"] = props
        info.update({"properties_entries": len(props),
                     "properties_sha256": sha_bytes(json.dumps(props, sort_keys=True).encode())})
    write_json(stage / f"{key.lower()}.json", spec)
    info["staged_sha256"] = sha(stage / f"{key.lower()}.json")
    return info


def hits_for(spec: dict, base: Path) -> Counter:
    """Branch hits of a staged spec (result files read from `base`); the module counter is restored."""
    global HITS
    saved, HITS = HITS, Counter()
    try:
        expected_of(spec, base)
        return HITS
    finally:
        local, HITS = HITS, saved
        HITS.update(local)


# --- E1 ------------------------------------------------------------------------------------------------------------

def e1() -> int:
    before = candidate()
    packages, battery = build_battery()
    names = [p["name"] for p in packages]
    battery["names_unique"] = len(set(names)) == len(names)
    battery["battery_sha256"] = sha_bytes(json.dumps([[p["name"], p["spec"], p["files"]] for p in packages],
                                                     sort_keys=True).encode())
    report = {"schema": "actinv-p147-e1-1", "candidate": before, "rule_pack": str(RULES_FILE.relative_to(ROOT)),
              "rule_pack_sha256": sha(RULES_FILE), "btp_sha256": sha(BTP_FILE) if BTP_FILE.is_file() else None,
              "readings": {"tie_normalized": TIE_NORMALIZED}, "rel_tol": REL_TOL, "battery": battery}
    rows = []
    for pkg in packages:
        stage = fresh_dir(WORK / "e1" / pkg["name"])
        write_package(pkg, stage)
        child = run_package(stage)
        row = {"name": pkg["name"], "command": brief(child), "pass": False}
        if child["rc"] == 0 and (stage / "out.json").exists():
            ck, _pkg, _expected, _result = check_stage(pkg["name"], stage)
            row.update(summarize(ck))
            row["result_sha256"] = sha(stage / "out.json")
            row["pass"] = clean(row)
        rows.append(row)
    report["packages"] = rows
    battery_ok = (len(rows) >= 60 and all(r["pass"] for r in rows) and not battery["uncovered"]
                  and not battery["generator_errors"] and battery["names_unique"])
    # the activation packages
    stage, runs = activation_stage(before)
    act = {"runs": runs, "runs_ok": all(r["rc"] == 0 for r in runs), "cases": []}
    act_hits, normalized = Counter(), []
    if all(r["rc"] == 0 for r in runs) and all((stage / r).exists() for r in ACT_RESULTS):
        for key in ACT_CASES:
            info = stage_activation_case(key, stage)
            child = run_package(stage, f"{key.lower()}.json", f"{key.lower()}_out.json")
            row = {**info, "command": brief(child), "pass": False}
            if child["rc"] == 0 and (stage / f"{key.lower()}_out.json").exists():
                ck, pkg, expected, result = check_stage(key, stage, f"{key.lower()}.json", f"{key.lower()}_out.json")
                row.update(summarize(ck))
                row["result_sha256"] = sha(stage / f"{key.lower()}_out.json")
                row["expected_package_class"] = {str(s): e["package_class"] for s, e in expected.items()}
                row["reported_package_class"] = {str(s): v["package_class"] for s, v in a_steps(result).items()}
                act_hits.update(hits_for(load_json(stage / f"{key.lower()}.json"), stage))
                normalized.append(pkg)
                row["pass"] = clean(row)
            act["cases"].append(row)
    flags = {"piece_under_280_cm3": any(i["vol"] < 280.0 and i["type"] == AM for p in normalized for i in p["items"]),
             "two_or_more_steps": bool(normalized) and all(len(p["targets"]) >= 2 for p in normalized),
             "encapsulated_declared_cs137_source": any(
                 p["enc"] is not None and any(i["type"] == SS and i["source"] == "declared"
                                              and any("Cs137" in b for b in i["bq"].values()) for i in p["items"])
                 for p in normalized),
             "several_piece_sizes": len({i["vol"] for p in normalized for i in p["items"] if i["type"] == AM}) >= 4}
    act.update({"flags": flags, "branch_hits": {b: act_hits[b] for b in BRANCHES}})
    act_ok = (act["runs_ok"] and len(act["cases"]) >= 3 and all(c["pass"] for c in act["cases"])
              and len(act["cases"]) == len(ACT_CASES) and all(flags.values()))
    report["activation"] = act
    report["pass"] = battery_ok and act_ok and before["tree_clean"]
    report["summary"] = {"packages": len(rows), "packages_passed": sum(r["pass"] for r in rows),
                         "uncovered_branches": battery["uncovered"], "generator_errors": len(battery["generator_errors"]),
                         "activation_cases": len(act["cases"]), "activation_passed": sum(c["pass"] for c in act["cases"])}
    return finish("e1", report)


# --- case machinery shared by E2 and E3 ---------------------------------------------------------------------------------

FIRST_STEP = 1  # the packages of E2 and E3 have the single target step 1


def path_eq(path: tuple, value, tol: float | None = None):
    """An assertion on a normalized step: the value at `path` equals `value` (None: null; a float: within `tol` relative)."""
    def check(st) -> bool:
        got = dig(st, *path)
        if got is MISSING:
            return False
        if value is None:
            return got is None
        if tol is not None:
            return isinstance(got, (int, float)) and not isinstance(got, bool) and close(float(value), float(got), tol)
        return got == value and type(got) is type(value) if isinstance(value, bool) else got == value
    return check


def pg_path(btype: str, cls: str, iid: str, *tail) -> tuple:
    return ("groups", btype, "candidates", cls, "item_tests", iid, "primary_gamma", *tail)


def other_present(btype: str, cls: str, iid: str, nuclide: str, present: bool):
    def check(st) -> bool:
        others = dig(st, "groups", btype, "candidates", cls, "item_tests", iid, "others")
        return isinstance(others, dict) and (nuclide in others) == present
    return check


def other_met(btype: str, cls: str, iid: str, nuclide: str, met):
    return path_eq(("groups", btype, "candidates", cls, "item_tests", iid, "others", nuclide), met)


def pool_present(btype: str, cls: str, present: bool):
    def check(st) -> bool:
        pool = dig(st, "groups", btype, "candidates", cls, "small_pool")
        return pool is not MISSING and (pool is not None) == present
    return check


def atmost(path: tuple, limit: float, *, inclusive: bool = True):
    def check(st) -> bool:
        got = dig(st, *path)
        return isinstance(got, (int, float)) and not isinstance(got, bool) and (got <= limit if inclusive else got < limit)
    return check


def above(path: tuple, limit: float):
    def check(st) -> bool:
        got = dig(st, *path)
        return isinstance(got, (int, float)) and not isinstance(got, bool) and got > limit
    return check


def conc_variants(bq: float, vol: float) -> list:
    """Concentration in Ci/m3 formed in several equivalent orders (a robust boundary construction gives one value)."""
    ci = get_rules().ci_bq
    return [bq / ci / (vol * 1.0e-6), bq / (ci / 1.0e6) / vol, bq / (vol * 1.0e-6 * ci), bq / (ci * (vol / 1.0e6))]


def robust(lhs: list, rhs: list, strict: bool) -> bool:
    """True when every pairing of the left and right values decides `lhs < rhs` (strict) or `lhs <= rhs` the same way."""
    decisions = {(l < r) if strict else (l <= r) for l in lhs for r in rhs}
    return len(decisions) == 1


def run_case(root: Path, case: dict) -> dict:
    """Stage one case, run the candidate, compare it with the independent result, and evaluate the case's assertions on
    both the independent result (the construction) and the reported result."""
    stage = fresh_dir(root / case["name"])
    write_package(case["package"], stage)
    child = run_package(stage)
    row = {"name": case["name"], "threshold": case.get("threshold"), "side": case.get("side"),
           "command": brief(child), "robust_construction": case.get("robust", True), "pass": False}
    if child["rc"] != 0 or not (stage / "out.json").exists():
        return row
    ck, pkg, expected, result = check_stage(case["name"], stage)
    row.update(summarize(ck))
    model_step = expected[FIRST_STEP]
    reported_step = a_steps(result).get(FIRST_STEP, MISSING)
    row["model_assertions"] = {label: bool(fn(model_step)) for label, fn in case["asserts"]}
    row["reported_assertions"] = {label: bool(fn(reported_step)) if reported_step is not MISSING else False
                                  for label, fn in case["asserts"]}
    if case.get("robust", True):
        row["pass"] = (clean(row) and all(row["model_assertions"].values()) and all(row["reported_assertions"].values()))
    else:
        # a boundary whose value depends on the order of floating-point operations: the run must still succeed and the
        # comparison fields away from the boundary must agree; neither side of the boundary is required
        row["pass"] = row["command"]["rc"] == 0 and not row["missing_fields"]
    return row


def run_cases(root: Path, cases: list) -> list:
    return [run_case(root, c) for c in cases]


# --- E2: source fidelity ------------------------------------------------------------------------------------------------

REVIEW = ROOT / "docs/maintainers/WASTE_BTP_SOURCE_REVIEW.md"
# printed values of the frozen review that the bundled file must carry (BTP Tables 2 and 3, thresholds)
REVIEW_QUOTES = [
    "5.2 TBq (140 Ci)", "266 MBq (7.2 mCi)", "27 GBq (0.72 Ci)", "4.8 TBq (130 Ci)", "37 MBq (1 mCi)",
    "0.3 TBq (8 Ci)", "0.04 TBq (1 Ci)", "0.4 TBq (10 Ci)", "0.15 TBq (4 Ci)", "1.5 TBq (40 Ci)", "0.26 TBq (7 Ci)",
    "55 TBq (1500 Ci)", "111 MBq (3 mCi)", "1.1 GBq (30 mCi)", "331 ft3", "14 percent", "0.2 m3 or 500 kg",
    "2.5 g/cc", "more than 50 percent", "1 percent of the concentration", "280 cc", "less than 37 MBq",
]
CI_BQ = 3.7e10
SI_CUSTOMARY_TOL = 0.10  # the BTP prints rounded customary values (0.04 TBq is 1.08 Ci)
DERIVED_TOL = 0.07  # the BTP's Table 3 values rounded from limit x 0.2 m3 (Ni-63 Class C: 1400 Ci computed, 1500 printed)


def e2_data_checks(ck: Checks) -> dict:
    """The bundled BTP data against the frozen review (no binary)."""
    detail = {}
    present = BTP_FILE.is_file()
    ck.flag("bundled BTP file exists", present, str(BTP_FILE.relative_to(ROOT)), present)
    if not present:
        return detail
    bundled = load_json(BTP_FILE)
    detail["worst_si_customary_relative_difference"] = e2_value_checks(ck, bundled)
    mirror = BTP_MIRROR.is_file() and BTP_MIRROR.read_bytes() == BTP_FILE.read_bytes()
    ck.flag("data/ mirror is byte-identical", mirror, "identical", "differs or missing")
    detail["btp_sha256"] = sha(BTP_FILE)
    # the review text carries the printed values
    text = " ".join(REVIEW.read_text().split())
    missing = [q for q in REVIEW_QUOTES if q not in text]
    ck.flag("review carries every printed value", not missing, [], missing)
    detail["review_sha256"] = sha(REVIEW)
    return detail


def e2_value_checks(ck: Checks, bundled: dict) -> float:
    """BTP data (a parsed file) against the frozen review values; returns the worst SI against customary difference."""
    ck.flag("bundled BTP data equals the frozen review values", bundled == EXPECTED_BTP, "EXPECTED_BTP", "differs")
    for top in ("schema", "id", "primary_gamma", "table2_bq", "table3_bq", "source"):
        ck.flag(f"bundled {top}", bundled.get(top) == EXPECTED_BTP[top], EXPECTED_BTP[top], bundled.get(top))
    for name, value in EXPECTED_BTP["thresholds"].items():
        got = bundled.get("thresholds", {}).get(name, MISSING)
        ck.flag(f"threshold {name}", got is not MISSING and got == value, value, got)
    ck.flag("no extra thresholds", set(bundled.get("thresholds", {})) == set(EXPECTED_BTP["thresholds"]),
            sorted(EXPECTED_BTP["thresholds"]), sorted(bundled.get("thresholds", {})))
    # 331 ft3 in cm3, exactly: (12 x 2.54 cm)^3 = 28316.846592 cm3
    ft3 = Decimal("30.48") ** 3
    ck.flag("1 ft3 is 28316.846592 cm3", ft3 == Decimal("28316.846592"), "28316.846592", str(ft3))
    ck.flag("331 ft3 is 9372876.221952 cm3", Decimal(331) * ft3 == Decimal("9372876.221952"), "9372876.221952",
            str(Decimal(331) * ft3))
    ck.flag("threshold equals 331 ft3", float(Decimal(331) * ft3) == bundled["thresholds"]["encapsulation_max_volume_cm3"],
            float(Decimal(331) * ft3), bundled["thresholds"]["encapsulation_max_volume_cm3"])
    ck.flag("0.2 m3 and 500 kg", bundled["thresholds"]["low_loading_max_volume_cm3"] == 0.2e6
            and bundled["thresholds"]["low_loading_max_mass_g"] == 5.0e5, "2e5 cm3 and 5e5 g", "differs")
    # SI against customary: the table entries and their printed customary values agree within the BTP's rounding
    worst = 0.0
    for table in ("table2", "table3"):
        for name, columns in REVIEW_PRINTED[table].items():
            data = bundled[f"{table}_bq"][name]
            for column, printed in enumerate(columns):
                if printed is None:
                    ck.flag(f"{table} {name} column {column} has no limit", data[column] is None, None, data[column])
                    continue
                si, ci = printed
                ck.flag(f"{table} {name} column {column} SI value", data[column] == si, si, data[column])
                rel = abs(data[column] / CI_BQ - ci) / ci
                worst = max(worst, rel)
                ck.flag(f"{table} {name} column {column} SI vs customary", rel <= SI_CUSTOMARY_TOL, ci, data[column] / CI_BQ)
    return worst


def e2_table3_consistency(ck: Checks) -> dict:
    """BTP Table 3 against the bundled CFR limits times 0.2 m3 (volume-based) or 0.2 m3 x 1.5 g/cm3 (mass-based), BTP V2
    p.84-85. H-3 and Ni-63 (activated metal) are the protocol's checks; the others are reported."""
    rules = get_rules()
    ci = rules.ci_bq

    def row(table: int, selector: str, applicability: str):
        exact = [r for r in rules.rows if (r["table"], r["selector"], r["applicability"]) == (table, selector, applicability)]
        shared = [r for r in rules.rows if (r["table"], r["selector"], r["applicability"]) == (table, selector, "all")]
        return (exact or shared or [None])[0]

    btp = load_json(BTP_FILE)["table3_bq"] if BTP_FILE.is_file() else EXPECTED_BTP["table3_bq"]
    volume_m3, mass_g = 0.2, 0.2e6 * 1.5
    rows = []

    def compare(label, derived_bq, printed, required):
        rel = abs(derived_bq - printed) / printed
        rows.append({"entry": label, "derived_bq": derived_bq, "btp_bq": printed, "relative_difference": rel,
                     "required": required})
        if required:
            ck.flag(f"Table 3 {label} within {DERIVED_TOL:.0%} of the CFR limit x basis", rel <= DERIVED_TOL, derived_bq,
                    printed)

    h3 = row(2, "H-3", "general")
    compare("H-3 Class A/B", h3["limits"][0] * ci * volume_m3, btp["H-3"][0], True)
    ck.flag("H-3 Class C has no CFR limit and no BTP Table 3 limit", h3["limits"][2] is None and btp["H-3"][2] is None,
            None, btp["H-3"][2])
    ni = row(2, "Ni-63", "activated_metal")
    compare("Ni-63 Class A/B", ni["limits"][0] * ci * volume_m3, btp["Ni-63"][0], True)
    compare("Ni-63 Class C", ni["limits"][2] * ci * volume_m3, btp["Ni-63"][2], True)
    tru = row(1, "alpha_transuranic_gt5y", "all")
    compare("alpha TRU Class A/B", tru["limits"][0] * 0.1 * (ci / 1.0e9) * mass_g, btp["alpha_transuranic_gt5y"][0], True)
    compare("alpha TRU Class C", tru["limits"][0] * (ci / 1.0e9) * mass_g, btp["alpha_transuranic_gt5y"][2], True)
    c14 = row(1, "C-14", "general")
    compare("C-14 (general limit) Class C", c14["limits"][0] * ci * volume_m3, btp["C-14"][2], False)
    ni59 = row(1, "Ni-59", "activated_metal")
    compare("Ni-59 Class C", ni59["limits"][0] * ci * volume_m3, btp["Ni-59"][2], False)
    return {"basis": "limit x 0.2 m3 (volume-based) or limit x 0.2 m3 x 1.5 g/cm3 (mass-based)", "tolerance": DERIVED_TOL,
            "rows": rows}


def e2_cases() -> list:
    """BTP Examples 1 and 2, the footnote-36 controlling case and the 3.8.2 example, as packages for the candidate."""
    cases = []
    nb = (lambda iid, vol, conc=None, **kw: mk(iid, vol, conc, **kw))
    bulk1 = mk("bulk", BIG, {"Co60": 1})
    bulk2 = mk("bulk", BIG, {"Co60": 1, "Tc99": 0.05})

    def example(tag, items, controlling, expect):
        label = "factor2" if controlling else "factor10"
        asserts = [("Nb-94 item test", expect),
                   ("primary gammas control" if controlling else "primary gammas do not control",
                    path_eq(("groups", AM, "primary_gamma_controlling"), controlling))]
        return {"name": tag, "threshold": f"BTP example ({label})", "package": make_package(f"e2_{tag}", items, targets=(1,)),
                "asserts": asserts}

    # Example 1, Class A mixture of items over 280 cc, Nb-94 controlling: 37 MBq (Table 2) or 2 x the Class A limit
    # (0.02 Ci/m3), i.e. 0.04 Ci/m3
    c = example("ex1_table2_37mbq", [nb("nb", 1000, bq={"Nb94": 3.7e7}), bulk1], True,
                path_eq(pg_path(AM, "A", "nb", "limit_met"), "table2"))
    c["asserts"] += [("Table 2 sum is 1", path_eq(pg_path(AM, "A", "nb", "table2_sum"), 1.0, 1e-12)),
                     ("mixture is Class A", path_eq(("groups", AM, "mixture_class"), "A"))]
    cases.append(c)
    c = example("ex1_table2_above", [nb("nb", 1000, bq={"Nb94": 3.7e7 * 1.0001}), bulk1], True,
                path_eq(pg_path(AM, "A", "nb", "limit_met"), None))
    cases.append(c)
    c = example("ex1_factor2_0p039", [nb("nb", 5.0e4, {"Nb94": 0.039}), bulk1], True,
                path_eq(pg_path(AM, "A", "nb", "limit_met"), "factor2"))
    c["asserts"].append(("mixture is Class A", path_eq(("groups", AM, "mixture_class"), "A")))
    cases.append(c)
    cases.append(example("ex1_factor2_0p041", [nb("nb", 5.0e4, {"Nb94": 0.041}), bulk1], True,
                         path_eq(pg_path(AM, "A", "nb", "limit_met"), None)))
    # Example 2, primary gammas not controlling: up to 10 x the Class A limit, 0.2 Ci/m3
    c = example("ex2_factor10_0p19", [nb("nb", 6000, {"Nb94": 0.19}), bulk2], False,
                path_eq(pg_path(AM, "A", "nb", "limit_met"), "factor10"))
    c["asserts"].append(("mixture is Class A", path_eq(("groups", AM, "mixture_class"), "A")))
    cases.append(c)
    cases.append(example("ex2_factor10_0p21", [nb("nb", 6000, {"Nb94": 0.21}), bulk2], False,
                         path_eq(pg_path(AM, "A", "nb", "limit_met"), None)))
    # footnote 36: Table 2 sum 0.3, Co-60 and Cs-137 together 0.2, 0.2/0.3 = 67 percent
    fn36 = make_package("e2_fn36", [mk("m1", BIG, {"Co60": 70, "Cs137": 0.1, "Ni63": 3.5})], targets=(1,))
    cases.append({"name": "fn36_controlling_67_percent", "threshold": "BTP footnote 36", "package": fn36, "asserts": [
        ("Table 2 is restrictive", path_eq(("groups", AM, "restrictive_table"), 2)),
        ("Table 2 sum is 0.3", path_eq(("groups", AM, "table2_sum"), 0.3, 1e-9)),
        ("primary gamma share is 0.2/0.3", path_eq(("groups", AM, "primary_gamma_share"), 0.2 / 0.3, 1e-9)),
        ("primary gammas control", path_eq(("groups", AM, "primary_gamma_controlling"), True))]})
    # section 3.8.2: 33 TBq of Cs-137 in 0.5 m3 is within the Class C limit of 4600 Ci/m3
    e382 = make_package("e2_382", [mk("src", 300, bq={"Cs137": 3.3e13}, kind=SS, mass=2000.0)], targets=(1,),
                        enc=enc_block(5.0e5, 1.0e6, 2.5, 1000.0))
    cases.append({"name": "sec_3_8_2_33_tbq_in_0p5_m3", "threshold": "BTP 3.8.2 example", "package": e382, "asserts": [
        ("inside the Class C cap", path_eq(("encapsulation", "class_c_cap", "passes"), True)),
        ("cap class is C", path_eq(("encapsulation", "class_c_cap", "class"), "C")),
        ("loading under 14 percent", path_eq(("encapsulation", "status"), "low_loading")),
        ("credited volume is 0.2 m3", path_eq(("encapsulation", "credited_volume_cm3"), 2.0e5, 1e-12))]})
    return cases


def e2() -> int:
    before = candidate()
    ck = Checks()
    detail = e2_data_checks(ck)
    consistency = e2_table3_consistency(ck)
    data_report = ck.report()
    cases = e2_cases()
    rows = run_cases(WORK / "e2", cases)
    report = {"schema": "actinv-p147-e2-1", "candidate": before, "data_checks": data_report, "data_detail": detail,
              "table3_consistency": consistency, "cases": rows, "review": str(REVIEW.relative_to(ROOT)),
              "tolerances": {"si_customary": SI_CUSTOMARY_TOL, "table3_derivation": DERIVED_TOL}}
    data_ok = data_report["checks"] > 0 and data_report["failed"] == 0 and not data_report["missing_fields"]
    cases_ok = bool(rows) and all(r["pass"] for r in rows)
    report["pass"] = data_ok and cases_ok and before["tree_clean"]
    report["summary"] = {"data_checks": data_report["checks"], "data_failed": data_report["failed"],
                         "cases": len(rows), "cases_passed": sum(r["pass"] for r in rows)}
    return finish("e2", report)


# --- E3: boundaries -------------------------------------------------------------------------------------------------------

CI = 3.7e10  # Bq per Ci, the bundled rule pack's value
THRESHOLDS = ["37 MBq screen", "280 cm3 small item", "BTP Table 2 sum of fractions 1 (individual)",
              "BTP Table 2 sum of fractions 1 (pooled)", "Factor of 2", "Factor of 10 (primary gamma)",
              "Factor of 10 (other nuclide)", "50 percent controlling share", "1 percent concern level",
              "BTP Table 3 value (Ni-63)", "BTP Table 3 value (alpha TRU)", "14 percent loading", "9.373 m3",
              "0.2 m3", "500 kg", "2.5 g/cm3", "Class C cap"]


def sides(threshold: str, tag: str, values, build, expect, *, robust_at=None) -> list:
    """The three cases of one threshold. build(name, value) is the package; expect(index, value) the assertions (index 0
    below, 1 at, 2 above); robust_at(value) says whether the construction of the at case is independent of the order of
    floating-point operations."""
    cases = []
    for index, (side, value) in enumerate(zip(SIDES, values)):
        name = f"{tag}_{side}"
        cases.append({"name": name, "threshold": threshold, "side": side, "package": build(f"e3_{name}", value),
                      "asserts": expect(index, value),
                      "robust": True if robust_at is None or side != "at" else bool(robust_at(value))})
    return cases


def share_variants(cs_conc: float) -> list:
    """The primary-gamma share of the Table 2 sum for Cs-137 at `cs_conc` Ci/m3 and Ni-63 at 8.75 Ci/m3 over 1 m3, with
    the two fractions formed in two orders and added in two orders."""
    bq_cs, bq_ni = cs_conc * CI, 8.75 * CI
    out = []
    for f_cs, f_ni in ((bq_cs / (1.0 * (CI / 1.0e6) * BIG), bq_ni / (35.0 * (CI / 1.0e6) * BIG)),
                       (bq_cs / CI / (BIG * 1.0e-6) / 1.0, bq_ni / CI / (BIG * 1.0e-6) / 35.0)):
        out += [f_cs / (f_cs + f_ni), f_cs / (f_ni + f_cs)]
    return out


def e3_cases() -> list:
    cases = []
    T = (1,)

    # 37 MBq: strictly less applies the screen; the item's total activity
    cases += sides("37 MBq screen", "screen37", (3.69999e7, 3.7e7, 3.7000001e7),
                   lambda n, v: make_package(n, [mk("s1", 1000, bq={"Co60": v})], screen="activity_37mbq", targets=T),
                   lambda k, v: [("screen applied" if k == 0 else "screen falls through",
                                  path_eq(("groups", AM, "screen_applied"), "activity_37mbq" if k == 0 else None))])
    # 280 cm3: smaller than 280 is pooled, 280 and larger is individual
    cases += sides("280 cm3 small item", "small280", (279.999, 280.0, 280.001),
                   lambda n, v: make_package(n, [mk("s1", v, {"Co60": 5})], targets=T),
                   lambda k, v: [("small flag", path_eq(("items", "s1", "small"), k == 0)),
                                 ("pool present only for a small item", pool_present(AM, "A", k == 0))])
    # BTP Table 2 sum of fractions: 1 passes ("limits"), above fails and the Factor of 2 then decides
    def table2_individual(n, v):
        return make_package(n, [mk("s1", BIG, bq={"Cs137": v})], targets=T)
    cases += sides("BTP Table 2 sum of fractions 1 (individual)", "t2sum", (2.65999e8, 2.66e8, 2.660001e8), table2_individual,
                   lambda k, v: [("limit met", path_eq(pg_path(AM, "A", "s1", "limit_met"), ("table2", "table2", "factor2")[k])),
                                 ("sum of fractions", atmost(pg_path(AM, "A", "s1", "table2_sum"), 1.0) if k < 2
                                  else above(pg_path(AM, "A", "s1", "table2_sum"), 1.0))],
                   robust_at=lambda v: robust([v / 2.66e8, v * (1.0 / 2.66e8)], [1.0], strict=False))
    # the pool of items under 280 cm3: two items of 1.33e8 Bq make the sum of fractions exactly 1
    cases += sides("BTP Table 2 sum of fractions 1 (pooled)", "t2pool", (1.329995e8, 1.33e8, 1.330005e8),
                   lambda n, v: make_package(n, [mk("p1", 100, bq={"Cs137": v}), mk("p2", 100, bq={"Cs137": v})], targets=T),
                   lambda k, v: [("pool passes", path_eq(("groups", AM, "candidates", "A", "small_pool", "passes"), k < 2)),
                                 ("item limit met", path_eq(pg_path(AM, "A", "p1", "limit_met"), "table2" if k < 2 else None))],
                   robust_at=lambda v: robust([(v + v) / 2.66e8, v / 2.66e8 + v / 2.66e8, (v + v) * (1.0 / 2.66e8)], [1.0],
                                              strict=False))
    # Factor of 2: the primary gamma controls and its concentration must be less than 2 x the Class A limit (1 Ci/m3)
    cases += sides("Factor of 2", "factor2", (1.999999, 2.0, 2.000001),
                   lambda n, v: make_package(n, [mk("s1", BIG, bq={"Cs137": v * CI})], targets=T),
                   lambda k, v: [("limit met", path_eq(pg_path(AM, "A", "s1", "limit_met"), "factor2" if k == 0 else None)),
                                 ("primary gammas control", path_eq(("groups", AM, "primary_gamma_controlling"), True))],
                   robust_at=lambda v: robust(conc_variants(v * CI, BIG), [2.0 * 1.0], strict=True))
    # Factor of 10 for a primary gamma that does not control (Tc-99 dominates CFR Table 1)
    cases += sides("Factor of 10 (primary gamma)", "factor10pg", (9.999999, 10.0, 10.000001),
                   lambda n, v: make_package(n, [mk("cs", BIG, bq={"Cs137": v * CI}), mk("tc", BIG, {"Tc99": 2.0})], targets=T),
                   lambda k, v: [("limit met", path_eq(pg_path(AM, "A", "cs", "limit_met"), "factor10" if k == 0 else None)),
                                 ("primary gammas do not control", path_eq(("groups", AM, "primary_gamma_controlling"), False))],
                   robust_at=lambda v: robust(conc_variants(v * CI, BIG), [10.0 * 1.0], strict=True))
    # Factor of 10 for a nuclide that is not a primary gamma (Ni-63 above its BTP Table 3 value; Class A limit 35 Ci/m3)
    cases += sides("Factor of 10 (other nuclide)", "factor10other", (349.9999, 350.0, 350.0001),
                   lambda n, v: make_package(n, [mk("n1", BIG, bq={"Ni63": v * CI})], targets=T),
                   lambda k, v: [("limit met", other_met(AM, "A", "n1", "Ni63", "factor10" if k == 0 else None)),
                                 ("item passes", path_eq(("groups", AM, "candidates", "A", "item_tests", "n1", "passes"),
                                                         k == 0))],
                   robust_at=lambda v: robust(conc_variants(v * CI, BIG), [10.0 * 35.0], strict=True))
    # 50 percent: Cs-137 (primary gamma) and Ni-63 each 0.25 of the Class A column of CFR Table 2 at the at case
    ni = 8.75 * CI
    cases += sides("50 percent controlling share", "share50", (0.24, 0.25, 0.26),
                   lambda n, v: make_package(n, [mk("m1", BIG, bq={"Cs137": v * CI, "Ni63": ni})], targets=T),
                   lambda k, v: [("controlling only above one half", path_eq(("groups", AM, "primary_gamma_controlling"), k == 2)),
                                 ("share", path_eq(("groups", AM, "primary_gamma_share"), v / (v + 0.25), 1e-12))],
                   robust_at=lambda v: robust(share_variants(v), [0.5], strict=False))
    # 1 percent of the class limit: H-3 (Class A column of CFR Table 2: 40 Ci/m3) enters the item tests above 0.4 Ci/m3
    cases += sides("1 percent concern level", "concern1", (1.4799963e10, 1.48e10, 1.4800037e10),
                   lambda n, v: make_package(n, [mk("h1", BIG, bq={"H3": v})], targets=T),
                   lambda k, v: [("H-3 of concern only above 1 percent", other_present(AM, "A", "h1", "H3", k == 2))],
                   robust_at=lambda v: robust([-c for c in conc_variants(v, BIG)],
                                              [-0.01 * 40.0, -40.0 * 0.01, -40.0 / 100.0], strict=True))
    # BTP Table 3 for Ni-63, Class A column 2.6e11 Bq
    cases += sides("BTP Table 3 value (Ni-63)", "t3ni63", (2.59999e11, 2.6e11, 2.600001e11),
                   lambda n, v: make_package(n, [mk("n1", BIG, bq={"Ni63": v})], targets=T),
                   lambda k, v: [("limit met", other_met(AM, "A", "n1", "Ni63", ("table3", "table3", "factor10")[k]))])
    # BTP Table 3 for the alpha transuranics, summed per item: 1.11e8 Bq
    cases += sides("BTP Table 3 value (alpha TRU)", "t3alpha", (5.0999e7, 5.1e7, 5.1001e7),
                   lambda n, v: make_package(n, [mk("u1", BIG, bq={"Pu239": 6.0e7, "Am241": v}, kind=CM, mass=1.0e7)],
                                             targets=T),
                   lambda k, v: [("limit met", other_met(CM, "A", "u1", p145.ALPHA, ("table3", "table3", "factor10")[k]))])
    # encapsulation: waste loading 14 percent
    three = [mk(f"w{i}", 1000, {"Co60": 5}) for i in (1, 2, 3)]
    cases += sides("14 percent loading", "loading14", (13999.0, 14000.0, 14001.0),
                   lambda n, v: make_package(n, three, enc=enc_block(100000, 50000, 2.0, v), targets=T),
                   lambda k, v: [("status", path_eq(("encapsulation", "status"), "low_loading" if k == 0 else "credited")),
                                 ("loading", path_eq(("encapsulation", "loading"), v / 100000.0, 1e-12))])
    # encapsulation: 331 ft3
    cases += sides("9.373 m3", "volume331", (9372876.2, 9372876.221952, 9372876.3),
                   lambda n, v: make_package(n, three, enc=enc_block(v, 1.0e6, 2.0, 2.0e6), targets=T),
                   lambda k, v: [("status", path_eq(("encapsulation", "status"),
                                                    "alternative_approach_required" if k == 2 else "credited"))])
    # encapsulation: 0.2 m3 at a low loading
    cases += sides("0.2 m3", "low02", (199999.0, 200000.0, 200001.0),
                   lambda n, v: make_package(n, three, enc=enc_block(v, 1.0e5, 2.5, 1.0e4), targets=T),
                   lambda k, v: [("status", path_eq(("encapsulation", "status"), "low_loading")),
                                 ("credited volume", path_eq(("encapsulation", "credited_volume_cm3"), min(v, 2.0e5), 1e-12))])
    # encapsulation: 500 kg at a low loading (item 100 kg + encapsulant)
    cases += sides("500 kg", "low500", (399999.0, 400000.0, 400001.0),
                   lambda n, v: make_package(n, [mk("w1", 1000, {"Co60": 5}, mass=1.0e5)], enc=enc_block(190000, v, 2.5, 1.0e4),
                                             targets=T),
                   lambda k, v: [("status", path_eq(("encapsulation", "status"), "low_loading")),
                                 ("credited mass", path_eq(("encapsulation", "credited_mass_g"), min(1.0e5 + v, 5.0e5), 1e-12))])
    # encapsulation: the encapsulant density credit stops at 2.5 g/cm3
    cases += sides("2.5 g/cm3", "density25", (2.4999, 2.5, 2.5001),
                   lambda n, v: make_package(n, [mk("w1", 1000, {"Co60": 5}, mass=7900.0)], enc=enc_block(20000, 1.0e6, v, 4000.0),
                                             targets=T),
                   lambda k, v: [("status", path_eq(("encapsulation", "status"), "credited")),
                                 ("credited mass", path_eq(("encapsulation", "credited_mass_g"),
                                                           7900.0 + min(2.5, v) * 16000.0, 1e-12))])
    # the Class C cap: 4600 Ci/m3 of Cs-137 over 1 m3 is 1.702e14 Bq, equal to the limit
    cases += sides("Class C cap", "cap", (1.7019983e14, 1.702e14, 1.7020017e14),
                   lambda n, v: make_package(n, [mk("src", 100, bq={"Cs137": v}, kind=SS, mass=1000.0)],
                                             enc=enc_block(1.0e6, 1.0e6, 2.5, 2.0e5), targets=T),
                   lambda k, v: [("cap passes", path_eq(("encapsulation", "class_c_cap", "passes"), k < 2)),
                                 ("status", path_eq(("encapsulation", "status"),
                                                    "credited" if k < 2 else "exceeds_class_c_cap"))],
                   robust_at=lambda v: robust([v / (4600.0 * (CI / 1.0e6) * BIG), v / 4600.0 / (CI / 1.0e6) / BIG],
                                              [1.0], strict=False))
    return cases


def e3() -> int:
    before = candidate()
    cases = e3_cases()
    rows = run_cases(WORK / "e3", cases)
    covered = {t: sorted(r["side"] for r in rows if r["threshold"] == t) for t in THRESHOLDS}
    complete = all(covered[t] == sorted(SIDES) for t in THRESHOLDS)
    fragile = [r["name"] for r in rows if not r["robust_construction"]]
    report = {"schema": "actinv-p147-e3-1", "candidate": before, "thresholds": THRESHOLDS, "sides_by_threshold": covered,
              "cases": rows, "construction_not_robust": fragile,
              "note": "an at case is built from exactly representable values (1 m3 volumes, integer or literal activities); "
                      "where a pairing of equivalent floating-point orders disagrees it is listed in "
                      "construction_not_robust and only required to run"}
    report["pass"] = complete and bool(rows) and all(r["pass"] for r in rows) and before["tree_clean"]
    report["summary"] = {"cases": len(rows), "passed": sum(r["pass"] for r in rows), "thresholds_with_three_sides": complete,
                         "construction_not_robust": fragile}
    return finish("e3", report)


# --- E4: reductions -----------------------------------------------------------------------------------------------------

ACT_PROPS: dict = {}
CLASSIFY_SCHEMA = "actinv-waste-spec-1"
SUM_REL = 1e-12


def a_classify(result: dict):
    """{step: {class, sums{(table, target class): normalized sum}}} of the first component of an `actinv waste` result."""
    comps = pick(result, "components")
    if not isinstance(comps, list) or not comps:
        return MISSING
    rows = pick(comps[0], "targets")
    if not isinstance(rows, list):
        return MISSING
    out = {}
    for row in rows:
        evaluation = pick(row, "evaluation")
        sums = {}
        if evaluation is not MISSING:
            for c in p145.find_constraints(evaluation):
                sums[(c["table"], c.get("target_class"))] = c["normalized_sum"]
        step = pick(row, "step")
        if isinstance(step, int):
            out[step] = {"class": pick(row, "class"), "sums": sums}
    return out


def classify_doc(result_name: str, cid: str, mass: float, vol: float, kind: str, targets, props: dict) -> dict:
    return {"schema": CLASSIFY_SCHEMA, "rules": BUNDLED, "input": result_name, "targets": list(targets),
            "nuclide_properties": props,
            "components": [{"id": cid, "mass_g": mass, "displaced_volume_cm3": vol, "waste_type": eval_type(kind),
                            "external_tritium": {"status": "not_applicable"}}]}


def compare_single(ck: Checks, label: str, pkg_result: dict, cls_result: dict, btype: str) -> None:
    """A one-item package against `actinv waste` of the same component: the class and the sums of fractions, exactly."""
    steps, cls = a_steps(pkg_result), a_classify(cls_result)
    if not ck.has(f"{label} classification", cls):
        return
    ck.flag(f"{label} the same steps", sorted(steps) == sorted(cls), sorted(cls), sorted(steps))
    for step, c in cls.items():
        s = steps.get(step, MISSING)
        tag = f"{label} step {step}"
        if not ck.has(f"{tag} package step", s):
            continue
        ck.flag(f"{tag} package class equals the component class", s["package_class"] == c["class"], c["class"],
                s["package_class"])
        if c["class"] == "unknown":
            continue
        group = dig(s, "groups", btype)
        if not ck.has(f"{tag} group", group):
            continue
        t1 = c["sums"].get((1, "C"), MISSING)
        t2 = c["sums"].get((2, c["class"] if c["class"] in CLASSES else "C"), MISSING)
        for name, want in (("table1_sum", t1), ("table2_sum", t2)):
            got = group[name]
            if ck.has(f"{tag} component {name}", want) and ck.has(f"{tag} package {name}", got):
                ck.flag(f"{tag} {name} is the component's sum", got == want, want, got)
                ck.note(f"{tag} {name} relative difference", 0.0 if got == want else abs(got - want) / max(abs(got), abs(want)))


def compare_split(ck: Checks, label: str, whole: dict, pieces: dict, btype: str, must_pass: bool) -> None:
    """Splitting an item into identical pieces leaves the package class, the mixture class and the sums unchanged, and the
    pieces are removed exactly when the whole is (every piece has the whole's concentration and the pooled sum of fractions
    of the pieces is the whole's). With `must_pass` the item passes at the mixture class, so no piece is removed."""
    a, b = a_steps(whole), a_steps(pieces)
    ck.flag(f"{label} the same steps", sorted(a) == sorted(b), sorted(a), sorted(b))
    for step in sorted(set(a) & set(b)):
        tag = f"{label} step {step}"
        ck.flag(f"{tag} package class", a[step]["package_class"] == b[step]["package_class"], a[step]["package_class"],
                b[step]["package_class"])
        ga, gb = dig(a[step], "groups", btype), dig(b[step], "groups", btype)
        if not (ck.has(f"{tag} whole group", ga) and ck.has(f"{tag} pieces group", gb)):
            continue
        ck.flag(f"{tag} mixture class", ga["mixture_class"] == gb["mixture_class"], ga["mixture_class"], gb["mixture_class"])
        for name in ("table1_sum", "table2_sum", "primary_gamma_share"):
            if ck.has(f"{tag} {name} (whole)", ga[name]) and ck.has(f"{tag} {name} (pieces)", gb[name]):
                ck.flag(f"{tag} {name}", close(ga[name], gb[name], SUM_REL), ga[name], gb[name])
        mix = gb["mixture_class"]
        if mix in CLASSES and ck.has(f"{tag} candidates", gb["candidates"]) and mix in gb["candidates"] \
                and ck.has(f"{tag} whole candidates", ga["candidates"]) and mix in ga["candidates"]:
            removed, removed_whole = gb["candidates"][mix]["removed"], ga["candidates"][mix]["removed"]
            ck.flag(f"{tag} the pieces are removed exactly when the whole is", (removed == []) == (removed_whole == []),
                    removed_whole, removed)
            if must_pass:
                ck.flag(f"{tag} no piece is removed at the mixture class", removed == [], [], removed)


def compare_average(ck: Checks, label: str, pkg_result: dict, cls_result: dict, btype: str, model_class: dict) -> None:
    """A package whose items all pass: the mixture class is the class of the averaged concentrations, the component class
    of the summed item, and the independent evaluator's class."""
    steps, cls = a_steps(pkg_result), a_classify(cls_result)
    if not ck.has(f"{label} classification", cls):
        return
    for step, c in cls.items():
        s = steps.get(step, MISSING)
        tag = f"{label} step {step}"
        if not ck.has(f"{tag} package step", s):
            continue
        group = dig(s, "groups", btype)
        if not ck.has(f"{tag} group", group):
            continue
        mix = group["mixture_class"]
        ck.flag(f"{tag} mixture class is the averaged component class", mix == c["class"], c["class"], mix)
        ck.flag(f"{tag} mixture class is the independent evaluator's averaged class", mix == model_class[step],
                model_class[step], mix)
        ck.flag(f"{tag} package class", s["package_class"] == c["class"], c["class"], s["package_class"])
        if mix in CLASSES and ck.has(f"{tag} candidates", group["candidates"]) and mix in group["candidates"]:
            removed = group["candidates"][mix]["removed"]
            ck.flag(f"{tag} no item is removed", removed == [], [], removed)
        t1 = c["sums"].get((1, "C"), MISSING)
        t2 = c["sums"].get((2, c["class"] if c["class"] in CLASSES else "C"), MISSING)
        for name, want in (("table1_sum", t1), ("table2_sum", t2)):
            if ck.has(f"{tag} component {name}", want) and ck.has(f"{tag} package {name}", group[name]):
                ck.flag(f"{tag} {name} is the averaged component's sum", close(want, group[name], 1e-10), want, group[name])


def combined_item(items: list, targets, iid: str = "combined") -> dict:
    """The sum of AM items as one component with the same per-gram activities as their total: a result-activity item."""
    mass = nsum(i["mass"] for i in items)
    vol = nsum(i["vol"] for i in items)
    bq = []
    for k in range(len(targets)):
        keys = sorted({n for i in items for n in i["bq"][k]})
        bq.append({n: nsum(i["bq"][k].get(n, 0.0) for i in items) for n in keys})
    return {"id": iid, "kind": AM, "vol": vol, "mass": mass, "section": None, "source": "result", "bq": bq}


def model_classes(pkg_spec: dict, files: dict, item: dict, targets) -> dict:
    """{step: class} of the independent evaluator for the item's total activity over its volume and mass."""
    ctx = Ctx(props_from_json(pkg_spec["nuclide_properties"]))
    return {s: ev_full(ctx, eval_type(item["kind"]), item["mass"], item["vol"], item["bq"][i])["class"]
            for i, s in enumerate(targets)}


def e4_synthetic_singles() -> list:
    return [("s1_class_a", mk("x", 1000, {"Co60": 5}, source="result")),
            ("s2_class_c_table1", mk("x", 1000, {"Tc99": 0.5}, source="result")),
            ("s3_class_b", mk("x", 300, {"Cs137": 20}, source="result")),
            ("s4_above_c_source", mk("x", 300, bq={"Cs137": 1.5e12}, kind=SS, source="result")),
            ("s5_mass_based", mk("x", 1000, {"Pu239": 2.0, "Am241": 1.0, "Pu241": 50, "Co60": 3}, kind=CM, source="result")),
            ("s6_above_c_passing", mk("x", 1000, {"Tc99": 2.0, "I129": 0.05}, source="result")),
            ("s7_unknown_nuclide", mk("x", 1000, bq={"Zn65": 1.0e6, "Co60": 1.0e6}, source="result")),
            ("s8_cartridge_filter", mk("x", 500, {"Cs137": 0.5}, kind=CF, source="result"))]


def e4_run_single(root: Path, tag: str, item: dict) -> dict:
    """One-item package and `actinv waste` of the same component, synthetic activities."""
    stage = fresh_dir(root / tag)
    targets = (1, 2)
    pkg = make_package(f"e4a_{tag}", [item], targets=targets)
    write_package(pkg, stage)
    (name,) = pkg["files"]
    props = props_json(SYN_PROPS)
    write_json(stage / "classify.json", classify_doc(name, "x", item["mass"], item["vol"], item["kind"], targets, props))
    a = run_package(stage)
    b = run_classify(stage, "classify.json", "classify_out.json")
    row = {"case": tag, "package": brief(a), "classify": brief(b), "pass": False}
    if a["rc"] == 0 and b["rc"] == 0:
        ck = Checks()
        compare_single(ck, tag, load_json(stage / "out.json"), load_json(stage / "classify_out.json"), item["kind"])
        row.update(summarize(ck))
        row["pass"] = clean(row)
    return row


def activation_props(stage: Path) -> dict:
    if "props" not in ACT_PROPS:
        ACT_PROPS["props"] = p146.property_map(load_json(INPUTS / "a_lo_problem.json")["decay"], stage)
    return ACT_PROPS["props"]


ACT_SINGLES = [("lo_piece", "act_lo_result.json", 3000.0, 379.747), ("hi_piece", "act_hi_result.json", 1500.0, 189.873),
               ("nbco_piece", "act_nbco_result.json", 2500.0, 316.456)]
ACT_STEPS = [11, 12, 13]


def activation_spec(result: str, items: list, props: dict) -> dict:
    return {"schema": SPEC_SCHEMA, "rules": BUNDLED, "targets": ACT_STEPS, "nuclide_properties": props, "screen": "none",
            "encapsulation": None,
            "items": [{"id": iid, "waste_type": AM, "mass_g": mass, "displaced_volume_cm3": vol,
                       "activity": {"result": result}} for iid, mass, vol in items]}


def e4_run_activation_single(stage: Path, tag: str, result: str, mass: float, vol: float) -> dict:
    props = activation_props(stage)
    write_json(stage / f"e4a_{tag}.json", activation_spec(result, [("x", mass, vol)], props))
    write_json(stage / f"e4a_{tag}_cls.json", classify_doc(result, "x", mass, vol, AM, ACT_STEPS, props))
    a = run_package(stage, f"e4a_{tag}.json", f"e4a_{tag}_out.json")
    b = run_classify(stage, f"e4a_{tag}_cls.json", f"e4a_{tag}_cls_out.json")
    row = {"case": tag, "package": brief(a), "classify": brief(b), "pass": False}
    if a["rc"] == 0 and b["rc"] == 0:
        ck = Checks()
        compare_single(ck, tag, load_json(stage / f"e4a_{tag}_out.json"), load_json(stage / f"e4a_{tag}_cls_out.json"), AM)
        row.update(summarize(ck))
        row["pass"] = clean(row)
    return row


def split_item(item: dict, n: int) -> list:
    """n identical pieces of an item (n a power of two, so that every division is exact)."""
    return [{**item, "id": f"{item['id']}-{k}", "vol": item["vol"] / n, "mass": item["mass"] / n,
             "bq": [{nuc: b / n for nuc, b in step.items()} for step in item["bq"]]} for k in range(n)]


def e4_run_split(root: Path, tag: str, item: dict, n: int) -> dict:
    stage = fresh_dir(root / f"{tag}_n{n}")
    targets = (1, 2)
    whole_dir, split_dir = fresh_dir(stage / "whole"), fresh_dir(stage / "split")
    write_package(make_package(f"e4b_{tag}_whole", [item], targets=targets), whole_dir)
    write_package(make_package(f"e4b_{tag}_split", split_item(item, n), targets=targets), split_dir)
    a, b = run_package(whole_dir), run_package(split_dir)
    row = {"case": f"{tag}_n{n}", "whole": brief(a), "split": brief(b), "pass": False}
    if a["rc"] == 0 and b["rc"] == 0:
        ck = Checks()
        compare_split(ck, row["case"], load_json(whole_dir / "out.json"), load_json(split_dir / "out.json"), item["kind"], True)
        # the pieces are also checked against the independent recomputation
        for folder, name in ((whole_dir, "whole"), (split_dir, "split")):
            sub, *_ = check_stage(f"{row['case']} {name}", folder)
            ck.total += sub.total
            ck.failures += sub.failures
            ck.missing |= sub.missing
        row.update(summarize(ck))
        row["pass"] = clean(row)
    return row


def e4_run_split_activation(stage: Path, tag: str, result: str, mass: float, vol: float, n: int) -> dict:
    props = activation_props(stage)
    write_json(stage / f"e4b_{tag}_whole.json", activation_spec(result, [("x", mass, vol)], props))
    write_json(stage / f"e4b_{tag}_n{n}.json",
               activation_spec(result, [(f"x-{k}", mass / n, vol / n) for k in range(n)], props))
    a = run_package(stage, f"e4b_{tag}_whole.json", f"e4b_{tag}_whole_out.json")
    b = run_package(stage, f"e4b_{tag}_n{n}.json", f"e4b_{tag}_n{n}_out.json")
    row = {"case": f"{tag}_n{n}", "whole": brief(a), "split": brief(b), "pass": False}
    if a["rc"] == 0 and b["rc"] == 0:
        ck = Checks()
        compare_split(ck, row["case"], load_json(stage / f"e4b_{tag}_whole_out.json"),
                      load_json(stage / f"e4b_{tag}_n{n}_out.json"), AM, False)
        row.update(summarize(ck))
        row["pass"] = clean(row)
    return row


def e4_averages() -> list:
    return [("p1_co60", [mk("a1", 1000, {"Co60": 5}, source="result"), mk("a2", 2000, {"Co60": 3}, source="result"),
                         mk("a3", 500, {"Co60": 1}, source="result")]),
            ("p2_cs_class_b", [mk(f"s{i}", 300, {"Cs137": 20}, source="result") for i in (1, 2, 3)]),
            ("p3_class_c", [mk("c1", 1000, {"Tc99": 0.5, "Cs137": 0.3}, source="result"),
                            mk("c2", 1000, {"Tc99": 0.5, "Cs137": 0.3}, source="result")])]


def e4_run_average(root: Path, tag: str, items: list) -> dict:
    stage = fresh_dir(root / tag)
    targets = (1, 2)
    pkg = make_package(f"e4c_{tag}", items, targets=targets)
    write_package(pkg, stage)
    whole = combined_item(items, targets)
    write_json(stage / "combined_result.json", run_result_doc(whole, targets))
    props = props_json(SYN_PROPS)
    write_json(stage / "classify.json", classify_doc("combined_result.json", "combined", whole["mass"], whole["vol"], AM,
                                                     targets, props))
    a = run_package(stage)
    b = run_classify(stage, "classify.json", "classify_out.json")
    row = {"case": tag, "package": brief(a), "classify": brief(b), "pass": False}
    if a["rc"] == 0 and b["rc"] == 0:
        ck = Checks()
        model = model_classes(pkg["spec"], pkg["files"], whole, targets)
        compare_average(ck, tag, load_json(stage / "out.json"), load_json(stage / "classify_out.json"), AM, model)
        row.update(summarize(ck))
        row["expected_class"] = {str(k): v for k, v in model.items()}
        row["pass"] = clean(row)
    return row


def e4() -> int:
    before = candidate()
    root = WORK / "e4"
    singles = [e4_run_single(root / "single", tag, item) for tag, item in e4_synthetic_singles()]
    splits = [e4_run_split(root / "split", tag, item, n) for tag, item in
              (("s1_class_a", mk("x", 1000, {"Co60": 5}, source="result")),
               ("s2_class_c_table1", mk("x", 1000, {"Tc99": 0.5}, source="result")),
               ("s3_class_b", mk("x", 300, {"Cs137": 20}, source="result"))) for n in (2, 4)]
    averages = [e4_run_average(root / "average", tag, items) for tag, items in e4_averages()]
    stage, runs = activation_stage(before)
    act_singles, act_splits = [], []
    if all(r["rc"] == 0 for r in runs) and all((stage / r).exists() for r in ACT_RESULTS):
        act_singles = [e4_run_activation_single(stage, tag, result, mass, vol) for tag, result, mass, vol in ACT_SINGLES]
        act_splits = [e4_run_split_activation(stage, tag, result, mass, vol, n)
                      for tag, result, mass, vol in ACT_SINGLES[:2] for n in (2, 4)]
    report = {"schema": "actinv-p147-e4-1", "candidate": before, "activation_runs": runs,
              "single_item": singles + act_singles, "split": splits + act_splits, "all_pass": averages,
              "reading": "a one-item package equals `actinv waste` of the same component: the class, and the CFR Table 1 "
                         "sum (the Class C normalized sum) and the Table 2 sum of the component's class column, bit for bit",
              "sum_rel_tol": SUM_REL}
    rows = report["single_item"] + report["split"] + report["all_pass"]
    report["pass"] = (len(singles) == 8 and len(act_singles) == len(ACT_SINGLES) and len(splits) == 6
                      and len(act_splits) == 4 and len(averages) == 3 and all(r["pass"] for r in rows)
                      and before["tree_clean"])
    report["summary"] = {"single_item": len(report["single_item"]), "split": len(report["split"]),
                         "all_pass": len(averages), "passed": sum(r["pass"] for r in rows), "of": len(rows)}
    return finish("e4", report)


# --- E5: refusals and determinism ----------------------------------------------------------------------------------------

def e5_base() -> dict:
    return make_package("e5_base", [mk("a", 1000, {"Co60": 5}, source="result"), mk("b", 300, {"Cs137": 2}, kind=SS)],
                        targets=(1, 2))


def _item(spec, index=0):
    return spec["items"][index]


def r_schema(spec, files):
    spec["schema"] = "actinv-waste-package-spec-9"


def r_screen(spec, files):
    spec["screen"] = "median_item"


def r_type(spec, files):
    _item(spec)["waste_type"] = "ion_exchange_resin"


def r_no_mass(spec, files):
    del _item(spec)["mass_g"]


def r_zero_mass(spec, files):
    _item(spec)["mass_g"] = 0.0


def r_no_volume(spec, files):
    del _item(spec)["displaced_volume_cm3"]


def r_negative_volume(spec, files):
    _item(spec)["displaced_volume_cm3"] = -5.0


def r_filter_displaced(spec, files):
    _item(spec, 1)["waste_type"] = "cartridge_filter"  # keeps displaced_volume_cm3


def r_other_envelope(spec, files):
    item = _item(spec)
    item["envelope_volume_cm3"] = item.pop("displaced_volume_cm3")


def r_duplicate(spec, files):
    _item(spec, 1)["id"] = _item(spec)["id"]


def r_empty(spec, files):
    spec["items"] = []


def r_enc_total(spec, files):
    spec["encapsulation"] = enc_block(0.0, 1000.0, 2.0)


def r_enc_mass(spec, files):
    spec["encapsulation"] = enc_block(10000.0, -1.0, 2.0)


def r_enc_density(spec, files):
    spec["encapsulation"] = enc_block(10000.0, 1000.0, 0.0)


def r_loading(spec, files):
    spec["encapsulation"] = enc_block(1000.0, 1000.0, 2.0, 2000.0)


def r_section_types(spec, files):
    _item(spec)["section_of"] = "component-1"
    _item(spec, 1)["section_of"] = "component-1"


def r_section_alone(spec, files):
    _item(spec)["section_of"] = "component-1"


def r_enc_screen(spec, files):
    spec["screen"] = "highest_item"
    spec["encapsulation"] = enc_block(10000.0, 1000.0, 2.0)


def r_declared_step(spec, files):
    declared = _item(spec, 1)["activity"]["declared_bq"]
    declared["5"] = dict(declared["1"])


def r_target_absent(spec, files):
    spec["targets"] = [1, 2, 9]


def r_no_targets(spec, files):
    spec["targets"] = []


def r_target_zero(spec, files):
    spec["targets"] = [0, 1]


def r_missing_result(spec, files):
    _item(spec)["activity"]["result"] = "no_such_result.json"


def r_negative_activity(spec, files):
    declared = _item(spec, 1)["activity"]["declared_bq"]
    declared["1"] = {"Cs137": -1.0}


def r_unknown_field(spec, files):
    spec["unexpected_field"] = 1


def r_item_flag(spec, files):
    _item(spec)["encapsulated"] = False


# name -> (edit, words of which the message should contain at least one (a hint, not a gate))
REFUSALS_E5 = {
    "wrong_schema": (r_schema, ("schema",)), "unknown_screen": (r_screen, ("screen",)),
    "unknown_waste_type": (r_type, ("waste type", "waste_type")), "missing_mass": (r_no_mass, ("mass",)),
    "non_positive_mass": (r_zero_mass, ("mass",)), "missing_volume": (r_no_volume, ("volume",)),
    "negative_volume": (r_negative_volume, ("volume",)),
    "filter_without_envelope_volume": (r_filter_displaced, ("envelope",)),
    "non_filter_with_envelope_volume": (r_other_envelope, ("displaced",)), "duplicate_item_ids": (r_duplicate, ("duplicate",)),
    "empty_items": (r_empty, ("item",)), "encapsulation_non_positive_total": (r_enc_total, ("encapsulat", "total")),
    "encapsulation_non_positive_mass": (r_enc_mass, ("encapsulant", "mass")),
    "encapsulation_non_positive_density": (r_enc_density, ("density",)),
    "loading_above_one": (r_loading, ("loading", "waste volume")),
    "sectioned_pieces_of_different_types": (r_section_types, ("section",)),
    "sectioned_piece_without_its_component": (r_section_alone, ("section", "piece")),
    "screen_with_encapsulation": (r_enc_screen, ("screen", "encapsulat")),
    "declared_step_not_in_targets": (r_declared_step, ("step", "target")),
    "target_step_absent": (r_target_absent, ("step", "target")), "no_targets": (r_no_targets, ("target",)),
    "target_step_zero": (r_target_zero, ("step", "target")), "missing_result_file": (r_missing_result, ("read", "result", "file")),
    "negative_declared_activity": (r_negative_activity, ("negative", "nonnegative", "finite", "activity")),
}
# readings of the protocol the contract does not fix: reported, not gated
OPTIONAL_REFUSALS_E5 = {
    "unknown_field": (r_unknown_field, ("unknown", "unexpected")),
    "per_item_encapsulated_flag": (r_item_flag, ("encapsulated", "unknown", "field")),
}


def e5_refusal(root: Path, name: str, edit, words) -> dict:
    stage = fresh_dir(root / name)
    pkg = e5_base()
    spec = copy.deepcopy(pkg["spec"])
    edit(spec, pkg["files"])
    write_package({"spec": spec, "files": pkg["files"]}, stage)
    before = p145.listing(stage)
    child = run_package(stage)
    message = child["stderr"].replace(str(stage), "<stage>").strip()
    return {"name": name, "command": brief(child), "exit_nonzero": child["rc"] not in (0, None),
            "exit_is_error_not_panic": child["rc"] in (1, 2), "no_output_written": not (stage / "out.json").exists(),
            "nothing_else_created": p145.listing(stage) == before,
            "message_present": bool(message) and "panicked" not in message,
            "message_mentions_topic_hint": any(w in message.lower() for w in words), "message": message[:400]}


def e5() -> int:
    before = candidate()
    root = WORK / "e5"
    report = {"schema": "actinv-p147-e5-1", "candidate": before}
    # control: the unedited base is accepted and matches the independent recomputation
    control_dir = fresh_dir(root / "control")
    base = e5_base()
    write_package(base, control_dir)
    control = run_package(control_dir)
    control_row = {"command": brief(control), "accepted": control["rc"] == 0, "pass": False}
    if control["rc"] == 0:
        ck, *_ = check_stage("e5_control", control_dir)
        control_row.update(summarize(ck))
        control_row["pass"] = clean(control_row)
    report["control"] = control_row
    gated = [e5_refusal(root, n, e, w) for n, (e, w) in REFUSALS_E5.items()]
    optional = [e5_refusal(root, n, e, w) for n, (e, w) in OPTIONAL_REFUSALS_E5.items()]
    messages = [r["message"] for r in gated]
    distinct = len(set(messages)) == len(messages) and all(messages)
    gate_keys = ("exit_nonzero", "exit_is_error_not_panic", "no_output_written", "nothing_else_created", "message_present")
    refusal_ok = distinct and all(all(r[k] for k in gate_keys) for r in gated)
    shared = {}
    for r in gated:
        shared.setdefault(r["message"], []).append(r["name"])
    report.update({"refusals": gated, "optional_refusals": optional, "messages_pairwise_distinct": distinct,
                   "cases_sharing_a_message": [names for names in shared.values() if len(names) > 1],
                   "hints_missed": [r["name"] for r in gated if not r["message_mentions_topic_hint"]]})
    # determinism: the same spec twice into two files, byte for byte; a third run into the first name again
    repeat_dir = fresh_dir(root / "repeat")
    write_package(base, repeat_dir)
    first = run_package(repeat_dir, "spec.json", "out_a.json")
    second = run_package(repeat_dir, "spec.json", "out_b.json")
    third = run_package(repeat_dir, "spec.json", "out_a.json")
    repeat = {"first": brief(first), "second": brief(second), "third": brief(third), "identical": False}
    if all(c["rc"] == 0 for c in (first, second, third)):
        a, b, c = [(repeat_dir / n).read_bytes() for n in ("out_a.json", "out_b.json", "out_a.json")]
        repeat.update({"identical": a == b == c, "sha256": sha_bytes(a), "bytes": len(a)})
    report["repeat"] = repeat
    report["pass"] = (control_row["pass"] and refusal_ok and repeat["identical"] and before["tree_clean"]
                      and len(gated) >= 20)
    report["summary"] = {"control_accepted": control_row["accepted"], "refusals": len(gated), "refusals_ok": refusal_ok,
                         "distinct_messages": distinct, "repeat_identical": repeat["identical"],
                         "hints_missed": report["hints_missed"]}
    return finish("e5", report)


# --- E6 (local half), the verdict and staging ----------------------------------------------------------------------------

E6_REQUIRED = ["build_cli", "e6_selftest", "e6_p103_check", "e6_p105_children", "e6_p105_check", "e6_p105_verdict",
               "e6_p145_selftest", "e6_fmt", "e6_clippy_native", "e6_clippy_wasm", "e6_workspace_tests", "e6_binding_fmt",
               "e6_binding_check", "e6_binding_clippy", "e6_module_build", "e6_module_install", "e6_module_tests",
               "e6_handbook", "e6_p13_data_distribution"]
# committed controls and records that P147 must not change: the P103 and P105 waste controls and seals, the P145 checker and
# its committed U2 records, the P146 records, and the bundled 10 CFR 61.55 rule pack
UNCHANGED_PATHS = ["results/g0_p103_seals.json", "results/g0_p105_successor.json", "results/g0_p105_successor_v1.json",
                   "results/g0b_p103_vectors.json", "results/g0b_p103_vectors_v1.json", "results/g0b_p103_vectors_v2.json",
                   "results/g0c_p103_runtime_controls.json", "results/g0c_p103_runtime_controls_v1.json",
                   "results/g1_p103_classes.json", "results/g1_p105_classes.json", "results/p145", "results/p146",
                   "controls/check_p103.py", "controls/check_p105.py", "controls/check_p105_verdict.py",
                   "controls/check_p145.py", "controls/check_p146.py", "data/waste_us_nrc_61_55_v1.json",
                   "crates/actinv-core/data/waste_us_nrc_61_55_v1.json"]
U2_RECORDS = ["results/p145/u2_c1.json", "results/p145/u2_c2.json"]
read_steps = p146.read_steps
count_tests = p146.count_tests


def registration_commit() -> str | None:
    """The commit that added the P147 protocol: the baseline the committed controls are compared with."""
    out = git("log", "--diff-filter=A", "--format=%H", "--", "protocols/ACTINV-P147_PROTOCOL.md")
    lines = out.split()
    return lines[-1] if lines else None


def committed_controls() -> dict:
    """The committed waste controls and the P145 U2 records are unchanged since the registration commit, and the U2
    records still say they passed (the recomputation itself is the P145 checker's selftest and the committed result)."""
    base = registration_commit()
    changed = git("diff", "--name-only", base, "HEAD", "--", *UNCHANGED_PATHS).split() if base else None
    records = {}
    for rel in U2_RECORDS:
        path = ROOT / rel
        records[rel] = {"present": path.is_file(), "sha256": sha(path) if path.is_file() else None,
                        "pass": bool(path.is_file() and load_json(path).get("pass") is True)}
    return {"registration_commit": base, "changed_since_registration": changed, "u2_records": records,
            "pass": base is not None and changed == [] and all(r["pass"] for r in records.values())}


def p103_identity(stdout: str) -> dict:
    """Amendment B: the P103 control stays as committed when its G0 document is sealed with every check passing and its
    G1 document equals the committed G1 record (persisted_result_matches), whatever that record's own pass flag."""
    docs, rest, decoder = {}, stdout, json.JSONDecoder()
    while rest.strip():
        rest = rest.lstrip()
        try:
            doc, end = decoder.raw_decode(rest)
        except json.JSONDecodeError:
            break
        rest = rest[end:]
        if isinstance(doc, dict) and isinstance(doc.get("schema"), str):
            docs[doc["schema"]] = doc
    g0, g1 = docs.get("actinv-p103-g0-seal-1"), docs.get("actinv-p103-g1-classes-1")
    checks = g0.get("checks") if isinstance(g0, dict) else None
    failing = sorted(k for k, v in checks.items() if not (isinstance(v, dict) and v.get("pass") is True)) \
        if isinstance(checks, dict) else None
    g0_ok = (isinstance(g0, dict) and g0.get("sealed") is True and g0.get("verdict") == "P103-G0-SEALED"
             and failing == [])
    g1_ok = isinstance(g1, dict) and g1.get("persisted_result_matches") is True
    return {"documents": sorted(docs), "g0_sealed": g0_ok, "g0_failing_checks": failing,
            "g1_matches_committed_record": g1_ok, "g1_record_pass_flag": g1.get("pass") if isinstance(g1, dict) else None,
            "pass": g0_ok and g1_ok}


def e6_p103() -> int:
    child = p145.run_child([sys.executable, ROOT / "controls/check_p103.py", "--no-write"], ROOT)
    record = {"schema": "actinv-p147-e6-p103-1", "command": brief(child), **p103_identity(child["stdout"])}
    print(json.dumps(record, indent=2, sort_keys=True))
    return 0 if record["pass"] else 1


def e6_collect(steps_path: str | None = None) -> int:
    path = Path(steps_path) if steps_path else OUT / "steps.tsv"
    steps = read_steps(path)
    logs = path.parent / "logs"
    passed, failed = count_tests(logs / "e6_workspace_tests.log")
    missing = [name for name in E6_REQUIRED if name not in steps]
    bad = [name for name in E6_REQUIRED if name in steps and steps[name]["exit"] != 0]
    other = {name: s["exit"] for name, s in steps.items() if name not in E6_REQUIRED}
    controls = committed_controls()
    clean_tree = tree_clean()
    gates_present = [f"e{n}" for n in range(1, 6) if (OUT / f"e{n}.json").is_file()]
    record = {"schema": "actinv-p147-e6-1", "commit": git("rev-parse", "HEAD"),
              "note": "local half of E6; the verdict also requires CI green on the pushed evidence commit",
              "steps": {name: s for name, s in steps.items() if name in E6_REQUIRED}, "gate_step_exits": other,
              "missing_steps": missing, "failed_steps": bad, "tests_passed": passed, "tests_failed": failed,
              "committed_controls": controls, "gate_records_present": gates_present, "tree_clean_tracked": clean_tree,
              "protocol_sha256": sha(PROTOCOL), "amendments_sha256": amendments(),
              "binary_sha256": sha(BINARY) if BINARY.is_file() else None,
              "pass": (not missing and not bad and failed == 0 and passed > 0 and controls["pass"] and clean_tree
                       and all(code == 0 for code in other.values()) and len(gates_present) == 5)}
    dump(OUT / "e6.json", record)
    print(json.dumps({"gate": "e6", "pass": record["pass"], "missing": missing, "failed": bad, "tests_passed": passed,
                      "tests_failed": failed, "controls_unchanged": controls["pass"]}, sort_keys=True))
    return 0 if record["pass"] else 1


GATE_NAMES = ["e1", "e2", "e3", "e4", "e5"]


def verdict(ci_path: str) -> int:
    records = {n: load_json(OUT / f"{n}.json") for n in GATE_NAMES}
    e6 = load_json(OUT / "e6.json")
    ci = load_json(Path(ci_path))
    runs = ci.get("runs", [])
    ci_green = bool(runs) and all(r.get("conclusion") == "success" for r in runs) and any(
        r.get("name") == "controls" for r in runs)
    gates = {name.upper(): records[name].get("pass") is True for name in GATE_NAMES}
    gates["E6"] = e6.get("pass") is True and ci_green
    evidence = [records[n]["candidate"] for n in GATE_NAMES]
    identity = {(c["commit"], c["binary_sha256"], c["protocol_sha256"], json.dumps(c["amendments_sha256"], sort_keys=True))
                for c in evidence}
    consistent = (len(identity) == 1 and evidence[0]["protocol_sha256"] == sha(PROTOCOL)
                  and e6.get("commit") == evidence[0]["commit"] and e6.get("binary_sha256") == evidence[0]["binary_sha256"])
    record = {"schema": "actinv-p147-verdict-1", "protocol_sha256": sha(PROTOCOL), "amendments_sha256": amendments(),
              "gate_file_sha256": {n: sha(OUT / f"{n}.json") for n in [*GATE_NAMES, "e6"]}, "gates": gates,
              "evidence_candidates_consistent": consistent, "candidate": evidence[0], "ci": ci,
              "verdict": "P147-PASS" if all(gates.values()) and consistent else "P147-FAIL"}
    dump(OUT.parent / "p147_verdict.json", record)
    print(json.dumps({"verdict": record["verdict"], "gates": gates, "consistent": consistent}))
    return 0 if record["verdict"] == "P147-PASS" else 1


def stage_command(case: str, directory: str) -> int:
    """Stage a case for a development run: an activation case A1..A6 (the three activation problems, the expanded spec, and
    the commands), or a synthetic package of the battery, E2 or E3 by name (spec and result files only)."""
    target = Path(directory).resolve()
    key = case.upper()
    if key in ACT_CASES:
        stage = fresh_dir(target, data=True)
        for result, problem in ACT_RESULTS.items():
            write_json(stage / problem, strip_private(load_json(INPUTS / problem)))
        info = stage_activation_case(key, stage)
        runs = " && ".join(f"actinv run {problem} {result}" for result, problem in ACT_RESULTS.items())
        print(json.dumps({"stage": str(stage), **info,
                          "run": f"cd {stage} && {runs} && actinv waste package {key.lower()}.json {key.lower()}_out.json"}))
        return 0
    packages = {p["name"]: p for p in build_battery()[0]}
    packages.update({c["name"]: c["package"] for c in e2_cases()})
    packages.update({c["name"]: c["package"] for c in e3_cases()})
    if case not in packages:
        raise SystemExit(f"unknown case {case!r}; choose from {sorted(ACT_CASES)} or one of {len(packages)} package names")
    stage = fresh_dir(target)
    write_package(packages[case], stage)
    print(json.dumps({"stage": str(stage), "case": case, "run": f"cd {stage} && actinv waste package spec.json out.json"}))
    return 0


# --- self test (no data, no binary) -----------------------------------------------------------------------------------------

def _g(doc, i=0):
    return doc["steps"][0]["groups"][i]


def _c(doc, cls, i=0):
    return next(c for c in _g(doc, i)["candidates"] if c["class"] == cls)


def _t(doc, cls, iid, i=0):
    return next(t for t in _c(doc, cls, i)["item_tests"] if t["id"] == iid)


def _i(doc, iid):
    return next(x for x in doc["steps"][0]["items"] if x["id"] == iid)


def _other(choices, value):
    return next(x for x in choices if x != value)


def _scale(holder, key, factor):
    holder[key] = holder[key] * factor


def selftest_mutations() -> list:
    """(name, package, mutation of a contract-shaped result). Each mutation must make the comparison report a failure."""
    def drop_other(doc):
        _t(doc, "A", "s1")["others"] = []

    def drop_candidate_a(doc):
        _g(doc)["candidates"] = [c for c in _g(doc)["candidates"] if c["class"] != "A"]

    def extra_other(doc):
        _t(doc, "B", "s1")["others"].append({"nuclide": "Sr90", "limit_met": "table3"})

    def delete_own_class(doc):
        del _i(doc, "s1")["own_class"]

    def drop_activity(doc):
        del _i(doc, "s1")["activities_bq"]["H3"]

    def pool_without_item(doc):
        pool = _c(doc, "A")["small_pool"]
        pool["items"] = pool["items"][:-1]

    def section(doc):
        return doc["steps"][0]["sections"][0]

    def encap(doc):
        return doc["steps"][0]["encapsulation"]

    def top_claims(doc):
        doc["not_claimed"] = [c for c in doc["not_claimed"] if "61.58" not in c]

    ms = [
        ("package class", "d04_cs_b", lambda d: d["steps"][0].__setitem__("package_class", "C")),
        ("item own class", "d04_cs_b", lambda d: _i(d, "s1").__setitem__("own_class", "A")),
        ("item volume (1e-9 relative)", "d04_cs_b", lambda d: _scale(_i(d, "s1"), "volume_cm3", 1 + 1e-9)),
        ("item small flag", "d04_cs_b", lambda d: _i(d, "s1").__setitem__("small", not _i(d, "s1")["small"])),
        ("item activity (1e-9 relative)", "d04_cs_b", lambda d: _scale(_i(d, "s1")["activities_bq"], "Cs137", 1 + 1e-9)),
        ("item activity nuclide dropped", "d04_cs_b", drop_activity),
        ("item field removed", "d04_cs_b", delete_own_class),
        ("mixture class", "d04_cs_b", lambda d: _g(d).__setitem__("mixture_class", "C")),
        ("restrictive table", "d04_cs_b", lambda d: _g(d).__setitem__("restrictive_table", 1)),
        ("table 2 sum (1e-9 relative)", "d04_cs_b", lambda d: _scale(_g(d), "table2_sum", 1 + 1e-9)),
        ("primary gamma controlling", "d04_cs_b",
         lambda d: _g(d).__setitem__("primary_gamma_controlling", not _g(d)["primary_gamma_controlling"])),
        ("primary gamma share (1e-9 relative)", "d04_cs_b", lambda d: _scale(_g(d), "primary_gamma_share", 1 + 1e-9)),
        ("candidate qualifies", "d04_cs_b", lambda d: _c(d, "B").__setitem__("qualifies", not _c(d, "B")["qualifies"])),
        ("candidate remainder class", "d04_cs_b",
         lambda d: _c(d, "A").__setitem__("remainder_class", _other(["A", "B", "C"], _c(d, "A")["remainder_class"]))),
        ("candidate remainder volume (1e-9 relative)", "d04_cs_b", lambda d: _scale(_c(d, "A"), "remainder_volume_cm3", 1 + 1e-9)),
        ("candidate removed list", "d04_cs_b", lambda d: _c(d, "A").__setitem__("removed", ["s1"])),
        ("candidate missing", "d04_cs_b", drop_candidate_a),
        ("item test passes", "d04_cs_b", lambda d: _t(d, "B", "s1").__setitem__("passes", False)),
        ("item test passes as an integer", "d04_cs_b", lambda d: _t(d, "B", "s1").__setitem__("passes", 1)),
        ("primary gamma limit met", "d04_cs_b",
         lambda d: _t(d, "A", "s1")["primary_gamma"].__setitem__(
             "limit_met", _other(["table2", "factor2", "factor10", None], _t(d, "A", "s1")["primary_gamma"]["limit_met"]))),
        ("primary gamma failing list", "d04_cs_b", lambda d: _t(d, "A", "s1")["primary_gamma"].__setitem__("failing", ["Co60", "Cs137"])),
        ("primary gamma table 2 sum (1e-9 relative)", "d04_cs_b",
         lambda d: _scale(_t(d, "A", "s1")["primary_gamma"], "table2_sum", 1 + 1e-9)),
        ("other nuclide limit met", "d04_cs_b",
         lambda d: _t(d, "A", "s1")["others"][0].__setitem__("limit_met", "factor10")),
        ("other nuclide dropped", "d04_cs_b", drop_other),
        ("extra tested other nuclide", "d04_cs_b", extra_other),
        ("group removed", "d04_cs_b", lambda d: d["steps"][0].__setitem__("groups", [])),
        ("step removed", "d04_cs_b", lambda d: d.__setitem__("steps", [])),
        ("small pool passes", "d05_pool_pass", lambda d: _c(d, "A")["small_pool"].__setitem__(
            "passes", not _c(d, "A")["small_pool"]["passes"])),
        ("small pool members", "d05_pool_pass", pool_without_item),
        ("small pool sum (1e-9 relative)", "d05_pool_pass", lambda d: _scale(_c(d, "A")["small_pool"], "table2_sum", 1 + 1e-9)),
        ("section merged", "d16_sec_merged", lambda d: section(d).__setitem__("merged", not section(d)["merged"])),
        ("section split out", "d16_sec_merged", lambda d: section(d).__setitem__("split_out", ["p1"])),
        ("section pieces", "d16_sec_merged", lambda d: section(d).__setitem__("pieces", ["p1"])),
        ("encapsulation status", "d30_enc_credited", lambda d: encap(d).__setitem__("status", "low_loading")),
        ("encapsulation loading (1e-9 relative)", "d30_enc_credited", lambda d: _scale(encap(d), "loading", 1 + 1e-9)),
        ("encapsulation credited volume", "d30_enc_credited", lambda d: encap(d).__setitem__("credited_volume_cm3", None)),
        ("encapsulation class", "d30_enc_credited",
         lambda d: encap(d).__setitem__("class", _other(["A", "B", "C"], encap(d)["class"]))),
        ("class C cap passes", "d30_enc_credited", lambda d: encap(d)["class_c_cap"].__setitem__("passes", False)),
        ("encapsulation block removed", "d30_enc_credited", lambda d: d["steps"][0].__setitem__("encapsulation", None)),
        ("screen applied", "d20_screen37_applied", lambda d: _g(d).__setitem__("screen_applied", None)),
        ("top-level screen", "d20_screen37_applied", lambda d: d.__setitem__("screen", "none")),
        ("removed item class", "d06_pool_fail", lambda d: _g(d)["removed_items"][0].__setitem__("own_class", "B")),
        ("step time (1e-9 relative)", "d01_clean_a", lambda d: _scale(d["steps"][0], "t_s", 1 + 1e-9)),
        ("rule pack digest", "d04_cs_b", lambda d: d.__setitem__("rule_pack_sha256", "0" * 64)),
        ("schema", "d04_cs_b", lambda d: d.__setitem__("schema", "actinv-waste-package-0")),
        ("encapsulated flag", "d04_cs_b", lambda d: d.__setitem__("encapsulated", True)),
        ("exclusions not claimed", "d04_cs_b", top_claims),
        ("readings emptied", "d04_cs_b", lambda d: d.__setitem__("readings", [])),
    ]
    return ms


def selftest() -> int:
    global OUT
    failures = []

    def expect(ok, label):
        if not ok:
            failures.append(label)

    # --- the battery: size, determinism, coverage of every branch --------------------------------------------------------
    packages, battery = build_battery()
    expect(len(packages) >= 60, f"battery has {len(packages)} packages")
    expect(not battery["uncovered"], f"branches never reached: {battery['uncovered']}")
    expect(not battery["generator_errors"], f"generator errors: {battery['generator_errors'][:3]}")
    expect(len({p["name"] for p in packages}) == len(packages), "package names are unique")
    again, _ = build_battery()
    expect(json.dumps([[p["name"], p["spec"], p["files"]] for p in packages], sort_keys=True)
           == json.dumps([[p["name"], p["spec"], p["files"]] for p in again], sort_keys=True), "the battery is deterministic")
    expect(len(set(BRANCHES)) == len(BRANCHES), "branch names are unique")
    expect(any(p["files"] for p in packages) and any(p["spec"]["encapsulation"] for p in packages), "result and encapsulation packages")
    by_name = {p["name"]: p for p in packages}

    def step_of(name, step=1):
        return expected_of(by_name[name]["spec"], None, by_name[name]["files"])[1][step]

    # --- the model against hand analysis of the directed packages --------------------------------------------------------
    s = step_of("d01_clean_a")
    expect(s["package_class"] == "A" and s["groups"][AM]["restrictive_table"] == 2
           and s["groups"][AM]["primary_gamma_controlling"] is True, "d01 is Class A, Table 2, controlling")
    g = step_of("d02_tie_table1")["groups"][AM]
    expect(g["restrictive_table"] == 1 and g["primary_gamma_controlling"] is False and g["mixture_class"] == "A",
           "d02 ties on Class A and Table 1 has the greater sum")
    g = step_of("d03_table1_c")["groups"][AM]
    expect(g["mixture_class"] == "C" and not g["candidates"]["A"]["qualifies"] and g["candidates"]["C"]["qualifies"]
           and g["restrictive_table"] == 1, "d03 qualifies at Class C under Table 1")
    s = step_of("d04_cs_b")
    g = s["groups"][AM]
    expect(s["package_class"] == "B" and g["mixture_class"] == "B" and g["restrictive_table"] == 2
           and g["primary_gamma_controlling"] is True and not g["candidates"]["A"]["qualifies"], "d04 is a Class B mixture")
    s = step_of("d06_pool_fail")
    g = s["groups"][AM]
    expect(g["mixture_class"] == "A" and s["package_class"] == "C" and g["candidates"]["A"]["removed"] == ["p1", "p2", "p3"]
           and g["candidates"]["A"]["small_pool"]["passes"] is False, "d06 removes every item and the package takes Class C")
    t = step_of("d07_factor2")["groups"][AM]["candidates"]["A"]["item_tests"]["f1"]["primary_gamma"]
    expect(t["limit_met"] == "factor2", "d07 meets the Factor of 2")
    t = step_of("d08_factor10")["groups"][AM]["candidates"]["A"]["item_tests"]["f1"]["primary_gamma"]
    expect(t["limit_met"] == "factor10", "d08 meets the Factor of 10")
    t = step_of("d09_vacuous")["groups"][AM]["candidates"]["A"]["item_tests"]["n1"]
    expect(t["passes"] is True and t["primary_gamma"]["limit_met"] in ("factor2", "factor10"), "d09 passes the Factor test")
    t = step_of("d10_unlisted")["groups"][SS]["candidates"]["A"]["item_tests"]["s1"]
    expect(t["passes"] is True and "Ni59" not in t["others"], "d10 skips a nuclide without a row")
    expect(step_of("d11_ni63_factor10")["groups"][AM]["candidates"]["A"]["item_tests"]["n1"]["others"]["Ni63"] == "factor10",
           "d11 meets the Factor of 10 for Ni-63")
    expect(step_of("d12_ni63_fail")["groups"][AM]["candidates"]["A"]["item_tests"]["n1"]["others"]["Ni63"] is None,
           "d12 fails for Ni-63")
    expect(step_of("d13_tru")["groups"][CM]["candidates"]["A"]["item_tests"]["u1"]["others"].get(p145.ALPHA) == "table3",
           "d13 meets BTP Table 3 for the alpha transuranics")
    s = step_of("d14_unknown")
    expect(s["package_class"] == "unknown" and s["groups"][AM]["mixture_class"] == "unknown", "d14 is unknown")
    expect(step_of("d15_types")["items"]["m1"]["small"] is False, "d15 an item of exactly 280 cm3 is not small")
    sections = step_of("d16_sec_merged")["sections"]
    expect(set(sections) == {"comp-a"} and sections["comp-a"]["merged"] is True, "d16 sections")
    expect(step_of("d17_sec_table3")["sections"]["c"]["split_out"] == ["p1"], "d17 splits the piece over BTP Table 3")
    expect(step_of("d18_sec_all_split")["sections"]["c"]["split_out"] == ["p1", "p2"], "d18 splits both pieces")
    expect(step_of("d19_sec_table2_small")["sections"]["c"]["split_out"] == ["p1"], "d19 splits the small piece over Table 2")
    expect(step_of("d20_screen37_applied")["groups"][AM]["screen_applied"] == "activity_37mbq", "d20 applies the 37 MBq screen")
    expect(step_of("d21_screen37_fallthrough")["groups"][AM]["screen_applied"] is None, "d21 falls through")
    expect(step_of("d22_screen_highest")["groups"][AM]["mixture_class"] == "C", "d22 takes the highest item class")
    s = step_of("d23_above_c")
    expect(s["groups"][AM]["mixture_class"] == "above_class_c" and s["package_class"] == "above_class_c", "d23 is above Class C")
    expect(step_of("d24_removed_hot")["package_class"] == "above_class_c", "d24 takes the removed source's class")
    for name, status in (("d30_enc_credited", "credited"), ("d31_enc_low", "low_loading"), ("d32_enc_low_capped", "low_loading"),
                         ("d33_enc_alt", "alternative_approach_required"), ("d34_enc_cap", "exceeds_class_c_cap"),
                         ("d35_enc_at_14", "credited"), ("d36_enc_at_max", "credited")):
        expect(step_of(name)["encapsulation"]["status"] == status, f"{name} has status {status}")
    e = step_of("d32_enc_low_capped")["encapsulation"]
    expect(e["credited_volume_cm3"] == 2.0e5 and e["credited_mass_g"] == 5.0e5, "d32 caps at 0.2 m3 and 500 kg")

    # --- the boundary constructions agree with the independent model ---------------------------------------------------
    construction = 0
    for case in e2_cases() + e3_cases():
        if not case.get("robust", True):
            continue
        model = expected_of(case["package"]["spec"], None, case["package"]["files"])[1][FIRST_STEP]
        for label, fn in case["asserts"]:
            construction += 1
            expect(fn(model), f"{case['name']}: {label} (model)")
    e3 = e3_cases()
    expect({c["threshold"] for c in e3} == set(THRESHOLDS), "E3 covers every threshold")
    expect(all([c["side"] for c in e3 if c["threshold"] == t] == list(SIDES) for t in THRESHOLDS), "E3 has three sides each")
    expect(len({c["name"] for c in e3}) == len(e3), "E3 case names are unique")
    expect(robust([1.0, 1.0], [2.0], True) and not robust([1.0, 3.0], [2.0], True), "robust() decides consistently")
    expect(all(conc_variants(7.4e10, BIG)[0] == v for v in conc_variants(7.4e10, BIG)), "1 m3 concentrations are exact")

    # --- E2 data checks on the frozen values, and on mutated copies ------------------------------------------------------
    ck = Checks()
    e2_value_checks(ck, copy.deepcopy(EXPECTED_BTP))
    expect(ck.report()["failed"] == 0, f"the frozen BTP values pass E2: {ck.report()['failures'][:2]}")
    for label, mutate in (("Cs-137 Class B value", lambda d: d["table2_bq"]["Cs-137"].__setitem__(1, 2.7e9)),
                          ("loading threshold", lambda d: d["thresholds"].__setitem__("encapsulation_min_loading", 0.15)),
                          ("331 ft3", lambda d: d["thresholds"].__setitem__("encapsulation_max_volume_cm3", 9.5e6)),
                          ("primary gammas", lambda d: d["primary_gamma"].pop()),
                          ("Co-60 has a Class B limit", lambda d: d["table2_bq"]["Co-60"].__setitem__(1, 1e12))):
        doc = copy.deepcopy(EXPECTED_BTP)
        mutate(doc)
        sub = Checks()
        e2_value_checks(sub, doc)
        expect(sub.report()["failed"] > 0, f"E2 notices a changed {label}")
    ck = Checks()
    consistency = e2_table3_consistency(ck)
    expect(ck.report()["failed"] == 0 and len(consistency["rows"]) == 7, f"Table 3 consistency: {ck.report()['failures'][:2]}")
    expect(REVIEW.is_file() and not [q for q in REVIEW_QUOTES if q not in " ".join(REVIEW.read_text().split())],
           "the review carries the printed values")

    # --- the comparison catches deliberate mutations and accepts the independent result ---------------------------------
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        cache = {}

        def prepared(name):
            if name not in cache:
                pkg = by_name[name]
                spec_path = tmp / f"{name}.json"
                write_json(spec_path, pkg["spec"])
                normalized, expected = expected_of(pkg["spec"], None, pkg["files"])
                cache[name] = (normalized, expected, spec_path, contract_result(normalized, expected, sha(spec_path)))
            return cache[name]

        def compare(name, doc):
            normalized, expected, spec_path, _base = prepared(name)
            ck = Checks()
            compare_result(ck, name, normalized, expected, spec_path, doc)
            return ck.report()

        mutations = selftest_mutations()
        for name in sorted({m[1] for m in mutations} | {"d01_clean_a", "d14_unknown", "d33_enc_alt", "d18_sec_all_split",
                                                          "d21_screen37_fallthrough", "d22_screen_highest", "d23_above_c"}):
            report = compare(name, copy.deepcopy(prepared(name)[3]))
            expect(report["failed"] == 0 and not report["missing_fields"], f"the independent result passes {name}: "
                   f"{report['failures'][:2]}")
        expect(len(mutations) >= 20, "at least 20 mutations")
        caught = 0
        for label, name, mutate in mutations:
            doc = copy.deepcopy(prepared(name)[3])
            try:
                mutate(doc)
                report = compare(name, doc)
            except Exception as error:  # a mutation that cannot be applied or a comparison that crashes is a defect here
                expect(False, f"mutation '{label}' raised {type(error).__name__}: {error}")
                continue
            if report["failed"] > 0:
                caught += 1
            else:
                expect(False, f"mutation '{label}' went unnoticed")
        # a difference inside the stated tolerance is accepted
        for label, name, mutate in (("item volume", "d04_cs_b", lambda d: _scale(_i(d, "s1"), "volume_cm3", 1 + 1e-14)),
                                    ("table 2 sum", "d04_cs_b", lambda d: _scale(_g(d), "table2_sum", 1 - 1e-14)),
                                    ("remainder mass", "d04_cs_b", lambda d: _scale(_c(d, "B"), "remainder_mass_g", 1 + 1e-14))):
            doc = copy.deepcopy(prepared(name)[3])
            mutate(doc)
            report = compare(name, doc)
            expect(report["failed"] == 0, f"a 1e-14 relative change of the {label} is within tolerance")
        # missing and empty results are reported, not raised
        try:
            for doc in ({}, {"steps": "none"}, {"steps": [None, 3], "schema": 1}):
                report = compare("d04_cs_b", doc)
                expect(report["failed"] > 0, "an empty result fails")
        except Exception as error:
            expect(False, f"an empty result raised {type(error).__name__}: {error}")
        # the adapters read by name and tolerate a missing group or step
        expect(a_steps({"steps": [{"step": 1}]})[1]["groups"] is MISSING, "adapter: missing groups are MISSING")
        expect(a_others([{"nuclide": "Ni-63", "limit_met": "table3"}]) == {"Ni63": "table3"}, "adapter: nuclide names are canonical")
        expect(by_key([{"id": "a"}, {"id": "a"}], "id") is MISSING, "adapter: duplicate ids are refused")

        # --- a one-item package against `actinv waste` of the component ---------------------------------------------------
        item = mk("x", 300, {"Cs137": 20}, source="result")
        single = make_package("e4_self", [item], targets=(1, 2))
        spec_path = tmp / "single.json"
        write_json(spec_path, single["spec"])
        normalized, expected = expected_of(single["spec"], None, single["files"])
        result = contract_result(normalized, expected, sha(spec_path))
        ctx = Ctx(normalized["props"])
        member = normalized["items"][0]
        comp = {"components": [{"targets": []}]}
        for step in (1, 2):
            ev = ev_full(ctx, AM, member["mass"], member["vol"], member["bq"][step])
            comp["components"][0]["targets"].append({"step": step, "class": ev["class"], "evaluation": {"constraints": [
                {k: c[k] for k in ("target_class", "table", "column", "normalized_sum")} for c in ev["constraints"]]}})
        ck = Checks()
        compare_single(ck, "single", result, comp, AM)
        expect(ck.report()["failed"] == 0 and ck.total > 0, f"a one-item package equals the component: {ck.report()['failures'][:2]}")
        comp2 = copy.deepcopy(comp)
        first = comp2["components"][0]["targets"][0]
        for c in first["evaluation"]["constraints"]:
            if c["table"] == 2 and c["target_class"] == first["class"]:
                c["normalized_sum"] *= 1.0 + 2.0 ** -50
        ck = Checks()
        compare_single(ck, "single", result, comp2, AM)
        expect(ck.report()["failed"] > 0, "a one-bit difference in a sum is noticed")
        comp3 = copy.deepcopy(comp)
        comp3["components"][0]["targets"][1]["class"] = "C"
        ck = Checks()
        compare_single(ck, "single", result, comp3, AM)
        expect(ck.report()["failed"] > 0, "a different class is noticed")
        # split pieces and combined items are exact for powers of two
        pieces = split_item(item, 4)
        expect(len(pieces) == 4 and nsum(p["bq"][0]["Cs137"] for p in pieces) == item["bq"][0]["Cs137"]
               and nsum(p["mass"] for p in pieces) == item["mass"], "split pieces add up")
        whole = combined_item(pieces, (1, 2))
        expect(whole["mass"] == item["mass"] and whole["bq"][0]["Cs137"] == item["bq"][0]["Cs137"], "combined item equals the whole")
        whole_ck, split_ck = Checks(), Checks()
        pa, ea = expected_of(make_package("e4_w", [item], targets=(1, 2))["spec"], None,
                             make_package("e4_w", [item], targets=(1, 2))["files"])
        pb, eb = expected_of(make_package("e4_s", pieces, targets=(1, 2))["spec"], None,
                             make_package("e4_s", pieces, targets=(1, 2))["files"])
        ra = contract_result(pa, ea, "0" * 64)
        rb = contract_result(pb, eb, "0" * 64)
        compare_split(whole_ck, "split", ra, rb, AM, True)
        expect(whole_ck.report()["failed"] == 0, f"the independent result passes the split reduction: "
               f"{whole_ck.report()['failures'][:2]}")
        rb2 = copy.deepcopy(rb)
        rb2["steps"][0]["groups"][0]["mixture_class"] = "C"
        compare_split(split_ck, "split", ra, rb2, AM, True)
        expect(split_ck.report()["failed"] > 0, "a changed mixture class breaks the split reduction")

    # --- E5 edits ------------------------------------------------------------------------------------------------------
    base = e5_base()
    expect(len(REFUSALS_E5) >= 20, "at least 20 refusals")
    for name, (edit, words) in {**REFUSALS_E5, **OPTIONAL_REFUSALS_E5}.items():
        spec = copy.deepcopy(base["spec"])
        try:
            edit(spec, base["files"])
        except Exception as error:
            expect(False, f"refusal edit {name} raised {type(error).__name__}: {error}")
            continue
        expect(spec != base["spec"], f"refusal edit {name} changes the spec")
    expect(normalize_package(base["spec"], None, base["files"])["targets"] == [1, 2], "the E5 base is a valid package")

    # --- verdict logic on fabricated records -----------------------------------------------------------------------------
    saved = OUT
    try:
        with tempfile.TemporaryDirectory() as tmp:
            OUT = Path(tmp) / "p147"
            cand = {"commit": "c0", "tree_clean": True, "binary": "b", "binary_sha256": "bb", "protocol_sha256": sha(PROTOCOL),
                    "amendments_sha256": amendments()}

            def records(gates=None, e6=None, drift=None):
                for n in GATE_NAMES:
                    c = dict(cand, commit="c1") if n == drift else cand
                    dump(OUT / f"{n}.json", {"pass": True if gates is None or n not in gates else gates[n], "candidate": c})
                dump(OUT / "e6.json", {"pass": True, "commit": "c0", "binary_sha256": "bb", **(e6 or {})})

            ok_ci = {"runs": [{"name": "controls", "conclusion": "success"}, {"name": "ci", "conclusion": "success"}]}
            cases = (("all green", {}, ok_ci, 0),
                     ("a gate fails", {"gates": {"e3": False}}, ok_ci, 1),
                     ("E6 fails", {"e6": {"pass": False}}, ok_ci, 1),
                     ("E6 for another commit", {"e6": {"commit": "c9"}}, ok_ci, 1),
                     ("evidence from two commits", {"drift": "e4"}, ok_ci, 1),
                     ("CI red", {}, {"runs": [{"name": "controls", "conclusion": "failure"}]}, 1),
                     ("CI without controls", {}, {"runs": [{"name": "ci", "conclusion": "success"}]}, 1),
                     ("CI absent", {}, {"runs": []}, 1))
            for label, kwargs, ci, want in cases:
                records(**kwargs)
                ci_path = Path(tmp) / "ci.json"
                ci_path.write_text(json.dumps(ci))
                with contextlib.redirect_stdout(io.StringIO()):
                    code = verdict(str(ci_path))
                expect(code == want, f"verdict: {label} gives {want}")
            steps = Path(tmp) / "steps.tsv"
            steps.write_text("build_cli\tcargo build\t0\ne6_fmt\tcargo fmt\t1\n")
            parsed = read_steps(steps)
            expect(parsed["build_cli"]["exit"] == 0 and parsed["e6_fmt"]["exit"] == 1, "steps.tsv is read")
            log = Path(tmp) / "t.log"
            log.write_text("test result: ok. 10 passed; 0 failed\ntest result: FAILED. 3 passed; 2 failed\n")
            expect(count_tests(log) == (13, 2), "test counts are summed")
    finally:
        OUT = saved
    expect(len(E6_REQUIRED) == len(set(E6_REQUIRED)), "E6 step names are unique")

    # Amendment B: the P103 step passes on a sealed G0 and a G1 recomputation equal to the committed record
    g0 = {"schema": "actinv-p103-g0-seal-1", "sealed": True, "verdict": "P103-G0-SEALED",
          "checks": {"a": {"pass": True}, "b": {"pass": True}}}
    g1 = {"schema": "actinv-p103-g1-classes-1", "pass": False, "persisted_result_matches": True}

    def text(*docs):
        return "".join(json.dumps(d, indent=2) + "\n" for d in docs)

    expect(p103_identity(text(g0, g1))["pass"] is True, "p103: sealed G0 and the committed G1 record pass")
    expect(p103_identity(text(g0, {**g1, "persisted_result_matches": False}))["pass"] is False,
           "p103: a G1 recomputation that differs from the committed record fails")
    expect(p103_identity(text(g0))["pass"] is False, "p103: a missing G1 document fails")
    expect(p103_identity(text({**g0, "sealed": False}, g1))["pass"] is False, "p103: an unsealed G0 fails")
    expect(p103_identity(text({**g0, "checks": {"a": {"pass": False}}}, g1))["pass"] is False,
           "p103: a failing G0 check fails")
    expect(p103_identity("")["pass"] is False and p103_identity("not json")["pass"] is False,
           "p103: no output fails")

    mutation_count = len(selftest_mutations())
    print(json.dumps({"selftest": "fail" if failures else "pass", "battery_packages": len(packages),
                      "branches": len(BRANCHES), "mutations": mutation_count, "boundary_assertions_checked": construction,
                      "failures": failures}, sort_keys=True))
    return 1 if failures else 0


def main(argv: list) -> int:
    command = argv[1] if len(argv) > 1 else ""
    gates = {"e1": e1, "e2": e2, "e3": e3, "e4": e4, "e5": e5}
    if command in gates and len(argv) == 2:
        return gates[command]()
    if command == "e6-p103" and len(argv) == 2:
        return e6_p103()
    if command == "e6-collect" and len(argv) in (2, 3):
        return e6_collect(argv[2] if len(argv) == 3 else None)
    if command == "verdict" and len(argv) == 3:
        return verdict(argv[2])
    if command == "stage" and len(argv) == 4:
        return stage_command(argv[2], argv[3])
    if command == "selftest" and len(argv) == 2:
        return selftest()
    raise SystemExit(__doc__)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
