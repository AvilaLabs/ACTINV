#!/usr/bin/env python3
"""P194 checker: gates G0-G3 on results/p194_record.json; writes results/p194_verdict.json.

  G0  The SHA-256 of the protocol, controls/p194.py and this checker are in protocols/protocol_hash.txt.
  G1  Provenance: P193's committed record, the four libraries and their indexes, the two ACTINV binaries, the Python
      module, the reused harnesses and the committed results they reproduce match their pins; the record comes from
      the registration commit with a clean tracked tree; not a rehearsal.
  G2  Integrity: the composite's blocks are the ones this checker's block rule chooses from P193's record; every
      block of the composite is bit-identical to its source library's; every table row collapsed with the composite
      equals P193's value for its block's library within 1e-12 relative; 24 `actinv inspect xs` cross-checks agree
      within 1e-9; each TENDL-2025 arm reproduces its committed result within 1e-12 relative; each composite arm
      is complete.
  G3  The measurements, always reported, never a gate: the block rule scored leave-one-facility-out on P192's
      table, and the composite against TENDL-2025 on IRDFF-II, Rez and JAEA-FNS.
  G4  CI green on the evidence commit (external).

Verdict: P194-PASS iff G0, G1 and G2 pass (G4 external).
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "controls"))
import check_p192 as c192  # noqa: E402
import check_p193 as c193  # noqa: E402

PROTOCOL_REL = "protocols/ACTINV-P194_PROTOCOL.md"
HASHED = [PROTOCOL_REL, "controls/p194.py", "controls/check_p194.py"]
RECORD = ROOT / "results" / "p194_record.json"
VERDICT = ROOT / "results" / "p194_verdict.json"
P193_RECORD = ROOT / "results" / "p193_record.json"
SCHEMA = "actinv-p194-record-1"
LIBRARIES = c193.LIBRARIES
DEFAULT = c193.DEFAULT
MARGIN = c193.MARGIN
FACILITIES = c193.FACILITIES
CROSSCHECK_ROWS = 24
TOLERANCE = 1e-12

# committed files this protocol reads or reproduces, and the reused harness code
COMMITTED_SHA256 = {
    "results/p193_record.json": "bae21fa81a77d4c46016a4f4034d3908f180ae359740e2f06e7359a66d2224fd",
    "results/g4_p17_diagnostics.json": "c65ad753055923afe77df82a2d532effa1f8c594cdedb7bfbe7344adf747bcaa",
    "results/p167_rez.json": "8c4772d493f0356b809c34271a9477bce7d6d0cefe1e97b23235b3799850618a",
    "results/p167/rez_tables.json": "7a2281ba2a2a5b6be0aeb9ab813ee09da1817f0be1daee16cd751b216574921a",
    "results/p172/actinv_tendl2025.json": "a4641659de225ddb17e0366bdfda4815c6bd5c9f00ec944aa12595c543f69736",
    "controls/p17_irdff.py": "3560feaab45ebd73cd51b599a01f68a47ccd00e6f2e30fd972bcaea95320f11f",
    "controls/p167_rez.py": "2df4de016f809c244200dc3daba5ee259b5392a56e2d2cbbe4a85e9ac72a5d37",
    "controls/cb2_fns.py": "3b3295d3d0e2b1213a0e9967d731fc5862238cf4219b176c520656dcdedaa4c1",
    "controls/p192.py": "c6366654831c1d08badec8a45507cf6dcfa6431f856e620eff2a5f661b515038",
    "controls/p193.py": "5a2fd99a77eca65f2cd1c7367f3ef97129f375eb5b9fce3f911ad9c4002c0299",
}
BINARY_SHA256 = {
    "table": c192.ACTINV_BINARY_SHA256,  # P192's and P193's
    "rez": "186cc2587bb61766b3b3c426c61cd44f00ed472401026dea2ca3dd595c8d1194",  # P167's
}
MODULE_SHA256 = "f33ea3eded6cfb8fd3734de692182ba4d793f75f026206e34a68871824dd7cc4"  # P172's
P17_INPUT_SHA256 = {
    "pdf": "ba2cd81b9a829368bb4d7a37de26842439ad437b0424586dcbd41074d7552d5f",
    "irdff_group_archive": "6ec2b33c0f67bed46d46be062a24ccedaa5ffea9bbba919958da4b1349f48c85",
    "irdff_spectrum_archive": "544c06ec741672c729ee9f2e716935a616bc44f3296001a1394d8760ff817e52",
}
FNS_INPUT_SHA256 = {
    "decay_primary": "6f04cf009086c179021f243a58dadc2d5bb078de5ba39c4fe46ccad77d228ddb",
    "decay_fallback": "850b8b7f85f8d88b6ad826c4cd341aaaffabd525c8ecf3c588a0ad437bf5d123",
    "fns_archive": "ba1dd6cb150a4aa3e0d81461054aec7d415ef19d946aba8b9886b31de218252d",
}
ARM_COUNTS = {"irdff": 124, "rez": 34, "fns": 132}
LABELS = {"FIRST CLOSER": "COMPOSITE CLOSER", "SECOND CLOSER": "TENDL-2025 CLOSER",
          "NO SIGNIFICANT DIFFERENCE": "NO SIGNIFICANT DIFFERENCE"}


def g0(root=ROOT, log=None):
    saved = c192.HASHED, c192.PROTOCOL_REL
    c192.HASHED = HASHED
    try:
        return c192.g0(root, log)
    finally:
        c192.HASHED, c192.PROTOCOL_REL = saved


def registration_commit():
    saved = c192.PROTOCOL_REL
    c192.PROTOCOL_REL = PROTOCOL_REL
    try:
        return c192.registration_commit()
    finally:
        c192.PROTOCOL_REL = saved


def committed_hashes(root=ROOT):
    return {rel: (c192.sha256(root / rel) if (root / rel).is_file() else None) for rel in COMMITTED_SHA256}


# ------------------------------------------------------------------------------------------------- the block rule

def block_of(row):
    mapping = row["mapping"]
    return f"{mapping['target_za']}:{mapping['mt']}"


def value(row, name):
    return c193.value(row, name)


def competes(name, rows, fact):
    """A library competes for a block if it has a value on every given row and its block has a total row and a
    production row for every product the block's labels name. TENDL-2025, the base, always competes."""
    if not all(value(row, name) for row in rows):
        return False
    if name == DEFAULT:
        return True
    entry = (fact.get("libraries") or {}).get(name) or {}
    return bool(entry.get("total_row")) and bool(entry.get("product_rows")) and all(entry["product_rows"].values())


def choose_block(fit_rows, applied_rows, fact):
    """P193's rule over a whole (target, MT) block: the competing library with the smallest mean |ln C/E| over the
    fit rows replaces TENDL-2025 only if it beats TENDL-2025's score by more than MARGIN."""
    scores = {}
    for name in LIBRARIES:
        if competes(name, fit_rows + applied_rows, fact):
            scores[name] = sum(abs(math.log(value(row, name) / row["E_b"])) for row in fit_rows) / len(fit_rows)
    if DEFAULT not in scores:
        return DEFAULT
    best = min(scores, key=lambda name: (scores[name], LIBRARIES.index(name)))
    return best if scores[DEFAULT] - scores[best] > MARGIN else DEFAULT


def scored_rows(p193_rows):
    return [row for row in p193_rows if row.get("status") == "scored"]


def final_blocks(p193_rows, facts):
    """The shipped choice: the block rule fitted on every scored row of each block."""
    rows = scored_rows(p193_rows)
    out = {}
    for block in sorted({block_of(row) for row in rows}):
        group = [row for row in rows if block_of(row) == block]
        out[block] = choose_block(group, group, facts.get(block, {}))
    return out


def lofo_blocks(p193_rows, facts):
    """Leave-one-facility-out: each fold's choice per block is fitted on the other facilities' rows of that block;
    the held-out rows are P193's, those of the fold whose reaction label was also measured at another facility."""
    rows = scored_rows(p193_rows)
    held = []
    for fold in FACILITIES:
        fit = [row for row in rows if c193.facility(row["spectrum"]) != fold]
        test = [row for row in rows if c193.facility(row["spectrum"]) == fold]
        fit_labels = {row["reaction"] for row in fit}
        for block in sorted({block_of(row) for row in test}):
            fit_rows = [row for row in fit if block_of(row) == block]
            applied = [row for row in test if block_of(row) == block]
            if not fit_rows:
                continue
            chosen = choose_block(fit_rows, applied, facts.get(block, {}))
            for row in applied:
                if row["reaction"] not in fit_labels:
                    continue
                held.append({"row": row["row"], "fold": fold, "reaction": row["reaction"], "block": block,
                             "chosen": chosen, "E_b": row["E_b"], "E_unc_b": row["E_unc_b"],
                             "C_selected_b": value(row, chosen), "C_default_b": value(row, DEFAULT),
                             "C_fispact_b": row["C_fispact_b"]})
    return held


# ---------------------------------------------------------------------------------------------------- the scores

def abs_log(c, e):
    return abs(math.log(c / e))


def median_abs_log(pairs):
    return c192.median([abs_log(c, e) for c, e in pairs]) if pairs else None


def compare(rows, composite, base):
    """Paired test of the composite against TENDL-2025 on rows with both values; rows the composite leaves unchanged
    give zero differences, which the Wilcoxon test drops."""
    usable = [row for row in rows if row.get(composite) and row.get(base) and row.get("E")]
    d = [abs_log(row[composite], row["E"]) - abs_log(row[base], row["E"]) for row in usable]
    test = c192.wilcoxon(d)
    if test["n"] == 0:
        label = "UNCHANGED"
    elif test["p"] < c192.ALPHA and test["z"] < 0:
        label = "COMPOSITE CLOSER"
    elif test["p"] < c192.ALPHA and test["z"] > 0:
        label = "TENDL-2025 CLOSER"
    else:
        label = "NO SIGNIFICANT DIFFERENCE"
    return {"rows": len(usable), "changed_rows": test["n"],
            "median_abs_ln_CE": {"composite": median_abs_log([(row[composite], row["E"]) for row in usable]),
                                 "tendl-2025": median_abs_log([(row[base], row["E"]) for row in usable])},
            "wilcoxon": test, "label": label}


def arm_rows(record):
    arms = record.get("arms", {})
    irdff = [{"E": row["measured_mb"], "base": row["base"]["C"], "composite": row["composite"]["C"]}
             for row in arms.get("irdff", {}).get("rows", [])]
    rez = [{"E": row["E"], "base": row["base"]["C"], "composite": row["composite"]["C"]}
           for row in arms.get("rez", {}).get("rows", []) if row.get("status") == "scored"]
    fns = [{"E": pair["measured_uW_g"], "base": pair["base"], "composite": pair["composite"],
            "fispact": pair.get("fispact_tendl2017_uW_g")} for pair in arms.get("fns", {}).get("pairs", [])]
    return {"irdff": irdff, "rez": rez, "fns": fns}


def decision(record, p193_rows):
    facts = record.get("blocks", {})
    held = lofo_blocks(p193_rows, facts)
    if not held:
        return {}
    final = final_blocks(p193_rows, facts)
    summary = {key: c192.stats(held, key) for key in ("C_selected_b", "C_default_b", "C_fispact_b")}
    rows = scored_rows(p193_rows)
    in_sample = [{"E_b": row["E_b"], "E_unc_b": row["E_unc_b"], "C_composite_b": value(row, final[block_of(row)]),
                  "C_default_b": value(row, DEFAULT), "C_fispact_b": row["C_fispact_b"]} for row in rows]
    arms = arm_rows(record)
    non_regression = {name: compare(rows_, "composite", "base") for name, rows_ in arms.items()}
    fns_vs_fispact = [row for row in arms["fns"] if row.get("fispact")]
    fns_fispact = c193.paired([{"E_b": row["E"], "a": row["composite"], "b": row["fispact"]}
                               for row in fns_vs_fispact], "a", "b") if fns_vs_fispact else None
    changed = {block: name for block, name in final.items() if name != DEFAULT}
    return {
        "blocks": len(final), "changed_blocks": len(changed),
        "changed_blocks_per_library": {n: sum(v == n for v in changed.values()) for n in LIBRARIES if n != DEFAULT},
        "final_choice": final,
        "held_out_rows": len(held),
        "held_out_per_fold": {f: sum(r["fold"] == f for r in held) for f in FACILITIES},
        "chosen_in_folds": {n: sum(r["chosen"] == n for r in held) for n in LIBRARIES},
        "summary": summary,
        "selected_vs_default": c193.paired(held, "C_selected_b", "C_default_b"),
        "selected_vs_fispact": c193.paired(held, "C_selected_b", "C_fispact_b"),
        "default_vs_fispact": c193.paired(held, "C_default_b", "C_fispact_b"),
        "in_sample": {key: c192.stats(in_sample, key) for key in ("C_composite_b", "C_default_b", "C_fispact_b")},
        "non_regression": non_regression,
        "non_regression_holds": all(v["label"] != "TENDL-2025 CLOSER" for v in non_regression.values()),
        "fns_composite_vs_fispact": ({"pairs": len(fns_vs_fispact),
                                      "median_abs_ln_CE": {
                                          "composite": median_abs_log([(r["composite"], r["E"]) for r in fns_vs_fispact]),
                                          "fispact": median_abs_log([(r["fispact"], r["E"]) for r in fns_vs_fispact])},
                                      "test": fns_fispact} if fns_fispact else None),
        "held_out": held,
    }


# ------------------------------------------------------------------------------------------------------- gates

def g1(record, registration, hashes=None):
    hashes = committed_hashes() if hashes is None else hashes
    problems = []
    if record.get("schema") != SCHEMA:
        problems.append("wrong record schema")
    if record.get("rehearsal"):
        problems.append("a rehearsal record")
    for rel, pin in COMMITTED_SHA256.items():
        if hashes.get(rel) != pin:
            problems.append(f"{rel} differs from its pin")
    inputs = record.get("inputs", {})
    if (inputs.get("p193_record") or {}).get("sha256") != COMMITTED_SHA256["results/p193_record.json"]:
        problems.append("the record did not read P193's committed record")
    libraries = inputs.get("libraries") or {}
    for name, (npz, index) in c193.LIBRARY_SHA256.items():
        entry = libraries.get(name) or {}
        if entry.get("sha256") != npz or entry.get("index_sha256") != index:
            problems.append(f"library {name} differs from its pin")
    binaries = inputs.get("binaries") or {}
    for role, pin in BINARY_SHA256.items():
        if (binaries.get(role) or {}).get("sha256") != pin:
            problems.append(f"{role} binary differs from its pin")
    if (inputs.get("python_module") or {}).get("sha256") != MODULE_SHA256:
        problems.append("Python module differs from its pin")
    if {k: (inputs.get("irdff") or {}).get(k) for k in P17_INPUT_SHA256} != P17_INPUT_SHA256:
        problems.append("IRDFF-II inputs differ from their pins")
    if {k: (inputs.get("fns") or {}).get(k) for k in FNS_INPUT_SHA256} != FNS_INPUT_SHA256:
        problems.append("FNS inputs differ from their pins")
    env = record.get("environment", {})
    if registration is None:
        problems.append("no registration commit found")
    elif env.get("git_head") != registration:
        problems.append("record not made at the registration commit")
    if env.get("git_dirty_paths"):
        problems.append("tracked tree was dirty")
    return problems


def close(a, b):
    return isinstance(a, float) and isinstance(b, float) and abs(a - b) <= TOLERANCE * max(abs(b), 1e-300)


def g2(record, p193_rows, committed):
    """committed: {"irdff": {row_id: C}, "rez": {(field, reaction): C}, "fns": [[material, experiment, time_s, C]]}."""
    problems = []
    facts = record.get("blocks", {})
    expected = final_blocks(p193_rows, facts)
    if record.get("choice") != expected:
        problems.append("the composite's blocks are not the block rule's choice")
    if set(facts) != set(expected):
        problems.append("block facts do not cover the table's blocks")
    verification = record.get("composite", {}).get("verification", {})
    if verification.get("mismatched_blocks") != 0 or verification.get("bounds_equal") is not True:
        problems.append(f"composite differs from its sources: {verification}")
    if verification.get("replaced_blocks") != sum(v != DEFAULT for v in expected.values()):
        problems.append("composite replaced a different number of blocks")
    reference = {row["row"]: row for row in p193_rows}
    table = {row["row"]: row for row in record.get("table", [])}
    for row in scored_rows(p193_rows):
        mine = table.get(row["row"], {})
        want = value(reference[row["row"]], expected.get(block_of(row), DEFAULT))
        if not close(mine.get("C_b"), want):
            problems.append(f"table row {row['row']}: composite value differs from its block's library")
    checks = (record.get("crosscheck") or {}).get("rows", [])
    if len(checks) != CROSSCHECK_ROWS:
        problems.append(f"{len(checks)} cross-checked values, not {CROSSCHECK_ROWS}")
    for check in checks:
        rel = check.get("relative_difference")
        if rel is None or not rel <= c192.CROSSCHECK_TOLERANCE:
            problems.append(f"row {check.get('row')}: actinv inspect xs disagrees ({rel})")
    arms = record.get("arms", {})
    irdff = arms.get("irdff", {}).get("rows", [])
    if len(irdff) != ARM_COUNTS["irdff"]:
        problems.append(f"IRDFF-II arm has {len(irdff)} rows")
    for row in irdff:
        base, want = row["base"]["C"], committed["irdff"].get(row["row_id"], "missing")
        if not (close(base, want) or (base is None and want is None)):
            problems.append(f"IRDFF-II {row['row_id']}: TENDL-2025 does not reproduce P17")
        if base is None:  # P17's TENDL-2025 has no value either; the composite must not invent one
            if row["composite"]["C"] is not None or row["composite"].get("reason") != row["base"].get("reason"):
                problems.append(f"IRDFF-II {row['row_id']}: composite differs on a row TENDL-2025 cannot value")
        elif not (isinstance(row["composite"]["C"], float) and row["composite"]["C"] > 0):
            problems.append(f"IRDFF-II {row['row_id']}: no composite value")
    rez = arms.get("rez", {}).get("rows", [])
    if len(rez) != ARM_COUNTS["rez"]:
        problems.append(f"Rez arm has {len(rez)} rows")
    for row in rez:
        if not close(row["base"]["C"], committed["rez"].get((row["field"], row["reaction"]))):
            problems.append(f"Rez {row['field']} {row['reaction']}: TENDL-2025 does not reproduce P167")
        if not (isinstance(row["composite"]["C"], float) and row["composite"]["C"] > 0):
            problems.append(f"Rez {row['field']} {row['reaction']}: no composite value")
    fns = arms.get("fns", {})
    pairs = fns.get("pairs", [])
    mine = [[p["material"], p["experiment"], p["time_s"], p["base"]] for p in pairs]
    if len(mine) != len(committed["fns"]) or any(
            a[:3] != b[:3] or not close(a[3], b[3]) for a, b in zip(mine, committed["fns"])):
        problems.append("FNS: TENDL-2025 does not reproduce P172's ACTINV arm")
    for name in ("base", "composite"):
        summary = fns.get(name, {})
        if summary.get("experiments") != ARM_COUNTS["fns"] or summary.get("certificate_inputs_match") is not True:
            problems.append(f"FNS {name} arm incomplete: {summary}")
    if any(not (isinstance(p["composite"], float) and p["composite"] > 0) for p in pairs):
        problems.append("FNS: a composite value is missing")
    return problems


def committed_values(root=ROOT):
    g4 = json.loads((root / "results/g4_p17_diagnostics.json").read_text())
    irdff = {row["row_id"]: row["calculations"]["actinv_tendl2025"]["value"] for row in g4["rows"]
             if str(row.get("family", "")).startswith("irdff")}
    p167 = json.loads((root / "results/p167_rez.json").read_text())
    rez = {(field, row["reaction"]): row["actinv_rate"] for field, data in p167["fields"].items()
           for row in data["rates"]}
    p172 = json.loads((root / "results/p172/actinv_tendl2025.json").read_text())
    fns = [[r["material"], r["experiment"], p["time_s"], p["actinv_tendl2025_uW_g"]] for r in p172["records"]
           for p in r["pairs"]]
    return {"irdff": irdff, "rez": rez, "fns": fns}


# -------------------------------------------------------------------------------------------------- sentences

def fmt(x):
    return f"{x:.3f}"


def sentences(dec):
    s, nr = dec["summary"], dec["non_regression"]
    vs_default = {"FIRST CLOSER": "the block choice brought ACTINV closer to the measurements than TENDL-2025 alone",
                  "SECOND CLOSER": "the block choice moved ACTINV further from the measurements than TENDL-2025 alone",
                  "NO SIGNIFICANT DIFFERENCE": "the block choice did not change ACTINV's distance from the "
                                               "measurements significantly"}
    vs_fispact = {"FIRST CLOSER": "ACTINV was closer than FISPACT-II",
                  "SECOND CLOSER": "FISPACT-II was closer than ACTINV",
                  "NO SIGNIFICANT DIFFERENCE": "neither ACTINV nor FISPACT-II was significantly closer"}
    clause = {"COMPOSITE CLOSER": "the composite was closer", "TENDL-2025 CLOSER": "TENDL-2025 was closer",
              "NO SIGNIFICANT DIFFERENCE": "neither was significantly closer",
              "UNCHANGED": "no value changed"}
    names = {"irdff": "IRDFF-II's {n} spectrum-averaged cross sections", "rez": "the {n} Rez activation rates",
             "fns": "the {n:,} JAEA-FNS decay-heat points"}
    a, b = dec["selected_vs_default"], dec["selected_vs_fispact"]
    parts = []
    for key in ("irdff", "rez", "fns"):
        v = nr[key]
        parts.append(f"{names[key].format(n=v['rows'])}, {fmt(v['median_abs_ln_CE']['composite'])} against "
                     f"{fmt(v['median_abs_ln_CE']['tendl-2025'])} ({clause[v['label']]}, "
                     f"{v['changed_rows']} values changed, p = {v['wilcoxon']['p']:.2g})")
    return [
        f"A composite ACTINV library, TENDL-2025 with {dec['changed_blocks']} of the {dec['blocks']} target-reaction "
        f"blocks measured in UKAEA's integro-differential validation table taken whole from TENDL-2017, TENDL-2023 or "
        f"ENDF/B-VIII.1 by a fixed rule, scored a median |ln(C/E)| of {fmt(s['C_selected_b']['median_abs_ln_CE'])} "
        f"in a leave-one-facility-out test on the table's {dec['held_out_rows']} rows whose reaction was also measured "
        f"at another facility, against {fmt(s['C_default_b']['median_abs_ln_CE'])} with TENDL-2025 alone and "
        f"{fmt(s['C_fispact_b']['median_abs_ln_CE'])} for FISPACT-II's published TENDL-2017 values; "
        f"{vs_default[a['label']]} (p = {a['wilcoxon']['p']:.2g}), and {vs_fispact[b['label']]} "
        f"(p = {b['wilcoxon']['p']:.2g}).",
        "On data scored before with TENDL-2025, the composite's median |ln(C/E)| was: " + "; ".join(parts) + ".",
        "None of these data was independent of earlier work: the table was compared with TENDL-2025 before (P192), "
        "the JAEA-FNS decay heat was measured at one of the table's three facilities, and the other two sets were "
        "scored before (P17, P167); in each fold of the table test the held-out facility played no part in the choice.",
    ]


def evaluate(record, p193_rows, committed, registration=None, log=None, hashes=None):
    gates = {"G0": g0(ROOT, log),
             "G1": g1(record, registration if registration is not None else registration_commit(), hashes),
             "G2": g2(record, p193_rows, committed)}
    dec = decision(record, p193_rows)
    passed = all(not problems for problems in gates.values())
    return gates, dec, (sentences(dec) if passed and dec else [])


# ---------------------------------------------------------------------------------------------------- selftest

def synthetic():
    rec193, _ = c193.synthetic()
    p193_rows = rec193["rows"]
    for row in p193_rows:
        row["status"] = "scored"
        row["mapping"] = {"target_za": 1000 + row["row"] // 6, "mt": 16}
        row["values"]["endfb-viii.1"] = {"C_b": 1.0 * row["E_b"]}
    facts = {}
    for row in p193_rows:
        facts[block_of(row)] = {"labels": [], "libraries": {
            n: {"total_row": True, "product_rows": {"x": True}} for n in LIBRARIES}}
    # ENDF/B-VIII.1 matches exactly but lacks product rows, so it must never be chosen
    for fact in facts.values():
        fact["libraries"]["endfb-viii.1"]["product_rows"] = {"x": False}
    final = final_blocks(p193_rows, facts)
    table = [{"row": row["row"], "C_b": value(row, final[block_of(row)])} for row in p193_rows]
    irdff = [{"row_id": f"irdff:t18:r{i:03d}", "measured_mb": 10.0, "base": {"C": 11.0},
              "composite": {"C": 11.0 if i % 2 else 10.5}} for i in range(ARM_COUNTS["irdff"])]
    irdff[0]["base"], irdff[0]["composite"] = ({"C": None, "reason": "variant_target_unavailable"},
                                               {"C": None, "reason": "variant_target_unavailable"})
    rez = [{"field": "14.4", "reaction": f"R{i}", "status": "scored" if i < 29 else "normalization", "E": 1.0,
            "base": {"C": 1.1}, "composite": {"C": 1.1}} for i in range(ARM_COUNTS["rez"])]
    pairs = [{"material": "Fe", "experiment": "x", "time_s": float(i), "measured_uW_g": 1.0, "base": 1.2,
              "composite": 1.25, "fispact_tendl2017_uW_g": 1.1} for i in range(40)]
    record = {
        "schema": SCHEMA, "rehearsal": False,
        "inputs": {"p193_record": {"sha256": COMMITTED_SHA256["results/p193_record.json"]},
                   "libraries": {n: {"sha256": a, "index_sha256": b} for n, (a, b) in c193.LIBRARY_SHA256.items()},
                   "binaries": {k: {"sha256": v} for k, v in BINARY_SHA256.items()},
                   "python_module": {"sha256": MODULE_SHA256}, "irdff": dict(P17_INPUT_SHA256),
                   "fns": dict(FNS_INPUT_SHA256)},
        "environment": {"git_head": "reg", "git_dirty_paths": ""},
        "blocks": facts, "choice": final,
        "composite": {"verification": {"mismatched_blocks": 0, "bounds_equal": True,
                                       "replaced_blocks": sum(v != DEFAULT for v in final.values())}},
        "table": table,
        "crosscheck": {"rows": [{"row": i, "relative_difference": 0.0} for i in range(CROSSCHECK_ROWS)]},
        "arms": {"irdff": {"rows": irdff}, "rez": {"rows": rez},
                 "fns": {"pairs": pairs, "base": {"experiments": 132, "certificate_inputs_match": True},
                         "composite": {"experiments": 132, "certificate_inputs_match": True}}},
    }
    committed = {"irdff": {row["row_id"]: (11.0 if row["base"]["C"] else None) for row in irdff},
                 "rez": {(row["field"], row["reaction"]): 1.1 for row in rez},
                 "fns": [[p["material"], p["experiment"], p["time_s"], 1.2] for p in pairs]}
    return record, p193_rows, committed


def selftest():
    failures = []
    record, p193_rows, committed = synthetic()
    hashes = dict(COMMITTED_SHA256)
    clean = g1(record, "reg", hashes) + g2(record, p193_rows, committed)
    if clean:
        failures.append(f"clean synthetic record fails: {clean[:3]}")
    dec = decision(record, p193_rows)
    if set(dec["final_choice"].values()) != {"tendl-2023"}:
        failures.append(f"best competing library not chosen: {set(dec['final_choice'].values())}")
    if dec["selected_vs_default"]["label"] != "FIRST CLOSER":
        failures.append("a better block library is not labelled as helping")
    nr = dec["non_regression"]
    if (nr["irdff"]["label"], nr["rez"]["label"], nr["fns"]["label"]) != (
            "COMPOSITE CLOSER", "UNCHANGED", "TENDL-2025 CLOSER") or dec["non_regression_holds"]:
        failures.append(f"non-regression labels wrong: {[v['label'] for v in nr.values()]}")
    if not sentences(dec) or len(sentences(dec)) != 3:
        failures.append("no sentences")
    fact = {"libraries": {n: {"total_row": True, "product_rows": {"x": True}} for n in LIBRARIES}}
    fit = [{"E_b": 1.0, "values": {"tendl-2025": {"C_b": 1.5}, "tendl-2023": {"C_b": 1.0},
                                   "tendl-2017": {}, "endfb-viii.1": {"C_b": 1.0}}}]
    if choose_block(fit, fit, fact) != "tendl-2023":
        failures.append("tie not broken by library order")
    no_total = copy.deepcopy(fact)
    no_total["libraries"]["tendl-2023"]["total_row"] = False
    if choose_block(fit, fit, no_total) != "endfb-viii.1":
        failures.append("a library without a total row competed")
    close_fit = [{"E_b": 1.0, "values": {"tendl-2025": {"C_b": 1.04}, "tendl-2023": {"C_b": 1.0},
                                         "tendl-2017": {}, "endfb-viii.1": {}}}]
    if choose_block(close_fit, close_fit, fact) != DEFAULT:
        failures.append("a library within the margin replaced the default")
    two = [{"E_b": 1.0, "values": {"tendl-2025": {"C_b": 1.0}, "tendl-2023": {"C_b": 2.0},
                                   "tendl-2017": {}, "endfb-viii.1": {}}},
           {"E_b": 1.0, "values": {"tendl-2025": {"C_b": 1.5}, "tendl-2023": {"C_b": 1.0},
                                   "tendl-2017": {}, "endfb-viii.1": {}}}]
    if choose_block(two, two, fact) != DEFAULT:
        failures.append("a block was switched for one label at another label's expense")

    def broken(name, mutate, gate):
        rec, rows, comm = copy.deepcopy(record), copy.deepcopy(p193_rows), copy.deepcopy(committed)
        hs = dict(hashes)
        registration = mutate(rec, rows, comm, hs) or "reg"
        found = {"G1": g1(rec, registration, hs), "G2": g2(rec, rows, comm)}
        if not found[gate]:
            failures.append(f"not caught by {gate}: {name}")

    broken("rehearsal", lambda r, p, c, h: r.update(rehearsal=True), "G1")
    broken("other library", lambda r, p, c, h: r["inputs"]["libraries"]["tendl-2017"].update(sha256="x"), "G1")
    broken("other binary", lambda r, p, c, h: r["inputs"]["binaries"]["rez"].update(sha256="x"), "G1")
    broken("other module", lambda r, p, c, h: r["inputs"]["python_module"].update(sha256="x"), "G1")
    broken("other IRDFF input", lambda r, p, c, h: r["inputs"]["irdff"].update(pdf="x"), "G1")
    broken("other FNS input", lambda r, p, c, h: r["inputs"]["fns"].update(fns_archive="x"), "G1")
    broken("edited harness", lambda r, p, c, h: h.update({"controls/cb2_fns.py": "x"}), "G1")
    broken("edited P193 record", lambda r, p, c, h: h.update({"results/p193_record.json": "x"}), "G1")
    broken("other commit", lambda r, p, c, h: r["environment"].update(git_head="x"), "G1")
    broken("dirty tree", lambda r, p, c, h: r["environment"].update(git_dirty_paths=" M x"), "G1")
    broken("other choice", lambda r, p, c, h: r["choice"].update({next(iter(r["choice"])): "tendl-2017"}), "G2")
    broken("composite drift", lambda r, p, c, h: r["composite"]["verification"].update(mismatched_blocks=1), "G2")
    broken("table drift", lambda r, p, c, h: r["table"][3].update(C_b=r["table"][3]["C_b"] * 1.001), "G2")
    broken("crosscheck off", lambda r, p, c, h: r["crosscheck"]["rows"][1].update(relative_difference=1e-6), "G2")
    broken("crosscheck missing", lambda r, p, c, h: r["crosscheck"]["rows"].pop(), "G2")
    broken("IRDFF not reproduced", lambda r, p, c, h: r["arms"]["irdff"]["rows"][5]["base"].update(C=11.1), "G2")
    broken("IRDFF value invented", lambda r, p, c, h: r["arms"]["irdff"]["rows"][0]["composite"].update(C=9.0),
           "G2")
    broken("IRDFF value lost", lambda r, p, c, h: r["arms"]["irdff"]["rows"][0]["base"].update(C=11.0), "G2")
    broken("IRDFF row missing", lambda r, p, c, h: r["arms"]["irdff"]["rows"].pop(), "G2")
    broken("Rez not reproduced", lambda r, p, c, h: r["arms"]["rez"]["rows"][2]["base"].update(C=1.2), "G2")
    broken("FNS not reproduced", lambda r, p, c, h: r["arms"]["fns"]["pairs"][7].update(base=1.3), "G2")
    broken("FNS pair missing", lambda r, p, c, h: r["arms"]["fns"]["pairs"].pop(), "G2")
    broken("FNS arm short", lambda r, p, c, h: r["arms"]["fns"]["composite"].update(experiments=131), "G2")
    broken("FNS composite missing", lambda r, p, c, h: r["arms"]["fns"]["pairs"][3].update(composite=None), "G2")
    log = "\n".join(f"{c192.sha256(ROOT / rel)}  {rel}" for rel in HASHED if (ROOT / rel).is_file())
    if g0(ROOT, log):
        failures.append("G0 fails on a sealed set")
    if not g0(ROOT, log.replace("controls/check_p194.py", "controls/other.py")):
        failures.append("G0 misses an unsealed checker")
    for failure in failures:
        print(f"selftest: {failure}")
    print("selftest: " + ("PASS" if not failures else "FAIL"))
    return not failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--record", type=Path, default=RECORD)
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--gates-only", action="store_true", help="print gate statuses and counts only")
    args = parser.parse_args()
    if args.selftest:
        return 0 if selftest() else 1
    record = json.loads(args.record.read_text())
    p193_rows = json.loads(P193_RECORD.read_text())["rows"]
    gates, dec, sent = evaluate(record, p193_rows, committed_values())
    passed = all(not problems for problems in gates.values())
    for name, problems in gates.items():
        print(f"{name}: {'PASS' if not problems else 'FAIL'}")
        if not args.gates_only:
            for problem in problems[:20]:
                print(f"  {problem}")
    if args.gates_only:
        print(f"blocks {dec.get('blocks')} changed {dec.get('changed_blocks')}; held-out rows "
              f"{dec.get('held_out_rows')} per fold {dec.get('held_out_per_fold')}")
        return 0 if passed else 1
    verdict = {
        "protocol": "ACTINV-P194",
        "verdict": "P194-PASS (pending G4, CI on the evidence commit)" if passed else "P194-FAIL",
        "gates": {**{name: {"pass": not problems, "problems": problems} for name, problems in gates.items()},
                  "G4": {"pass": None, "problems": ["CI on the evidence commit, checked after the push"]}},
        "decision": dec,
        "allowed_sentences": sent,
    }
    if dec:
        s, nr = dec["summary"], dec["non_regression"]
        print(f"G3: {dec['changed_blocks']} of {dec['blocks']} blocks changed; held out {dec['held_out_rows']}; "
              f"median |ln C/E| block choice {fmt(s['C_selected_b']['median_abs_ln_CE'])}, TENDL-2025 "
              f"{fmt(s['C_default_b']['median_abs_ln_CE'])}, FISPACT-II {fmt(s['C_fispact_b']['median_abs_ln_CE'])}; "
              f"vs default {dec['selected_vs_default']['label']} (p = {dec['selected_vs_default']['wilcoxon']['p']:.2g})"
              f"; vs FISPACT-II {dec['selected_vs_fispact']['label']} "
              f"(p = {dec['selected_vs_fispact']['wilcoxon']['p']:.2g})")
        for key, v in nr.items():
            print(f"G3: {key}: composite {v['median_abs_ln_CE']['composite']}, TENDL-2025 "
                  f"{v['median_abs_ln_CE']['tendl-2025']}, {v['changed_rows']} changed, {v['label']}")
    if args.no_write:
        if VERDICT.is_file():
            recorded = json.loads(VERDICT.read_text())
            if recorded.get("decision") != json.loads(json.dumps(dec)) or recorded.get("allowed_sentences") != sent:
                print("verdict file differs from the recomputed decision")
                passed = False
    elif args.record.resolve() == RECORD.resolve():
        VERDICT.write_text(json.dumps(verdict, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
    for sentence in sent:
        print(f"  {sentence}")
    print(verdict["verdict"] if passed else "P194-FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
