#!/usr/bin/env python3
"""P24 G3 independent checker.

Re-derives the consumed-partition diagnostic re-score without importing
``p24_definitions``, ``p24_scorer`` or the P17 held-out machinery:

  - protocol/module/artifact identities are re-hashed;
  - consumed tables are re-parsed from the pinned PDF with an independent
    grammar implementation, and every P17 source id must appear exactly
    once in the diagnostic row ledger;
  - the pulse-limit EOI reconstruction is recomputed from the pinned
    decay archive with independent arithmetic;
  - inclusion reasons are re-derived from first principles (cover ->
    unsupported_self_shielding; monitor row -> monitor_identity_not_
    predictive; bare MT-102 capture outside LB44 -> unsupported);
  - candidate ``variant_target_unavailable`` rows must correspond exactly
    to the G0 construction-failure ledger;
  - Ag109g must land on the declared LFS=2 partial;
  - planted mutations of the record are rejected.

Exit status is nonzero on any failure.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import subprocess
import sys
import tempfile
import zipfile
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RESULTS = REPO / "results"
RECORD = RESULTS / "g3_p24_diagnostic.json"
P17_RECORD = RESULTS / "g5_p17_heldout.json"
G1_RECORD = RESULTS / "g1_p24_definitions.json"
G2_AUDIT = RESULTS / "g2_p24_scorer_audit.json"
G0_BUILD = RESULTS / "g0_p24_candidate_build.json"
PROTOCOL = REPO / "protocols" / "ACTINV-P24_PROTOCOL.md"
PROTOCOL_SHA256 = "ee703d208b43c3dc91fef41a259d748d343865e130f37266128685cf21001ef7"
DEF_MODULE = REPO / "controls" / "p24_definitions.py"
SCORER = REPO / "controls" / "p24_scorer.py"
DECAY = Path.home() / "nuclear-data/p17-irdff/IRDFF-II_dd_ENDF.zip"
PAPER = Path.home() / "nuclear-data/p17-irdff/IRDFF-II_primary_1909.03336.pdf"
CANDIDATE_NPZ = REPO / "target" / "p24-g0" / "candidate-neutron.npz"

ELEMENT_Z = {e: z for z, e in enumerate(
    "H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe "
    "Co Ni Cu Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In "
    "Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf "
    "Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am "
    "Cm Bk Cf".split(), start=1)}
UNSUPPORTED_COVERS = {"Cd", "Cdtk", "Cdtk/B4C", "Cdna"}
TABLE_PAGES = {23: 77, 25: 79, 36: 90}
EXPECTED_ROWS = {23: 40, 25: 33, 36: 21}
MONITOR_HALF_LIFE_S = 6122300.0

failures: list[str] = []


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def pdf_page_text(page: int) -> str:
    out = subprocess.run(
        ["pdftotext", "-f", str(page), "-l", str(page), "-layout", str(PAPER), "-"],
        capture_output=True, text=True)
    return out.stdout


def parse_si_table_independent(table: int) -> list[dict]:
    """Own implementation of the consumed 9-column SI grammar."""
    rows = []
    for line in pdf_page_text(TABLE_PAGES[table]).splitlines():
        parts = line.split()
        if len(parts) != 9 or "-" not in parts[0]:
            continue
        if re.fullmatch(r"[A-Za-z0-9]+-(?:bare|Cd|Cdna|Cdtk|Cdtk/B4C)", parts[0]) is None:
            continue
        try:
            vals = [float(x) for x in parts[1:]]
        except ValueError:
            continue
        rxn, cover = parts[0].split("-", 1)
        rows.append({"label": parts[0], "reaction_label": rxn, "cover": cover,
                     "measured_EOI_per_atom": vals[1],
                     "experimental_uncertainty_percent": vals[2],
                     "published_spectral_index": vals[4],
                     "published_SI_C_over_E": vals[6]})
    return rows


def parse_maxwellian_independent() -> list[dict]:
    rows = []
    for line in pdf_page_text(TABLE_PAGES[36]).splitlines():
        parts = line.split()
        if len(parts) < 11:
            continue
        m = re.fullmatch(r"([A-Z][a-z]?\d+[a-z]*)", parts[0])
        if m is None:
            continue
        try:
            vals = [float(x) for x in parts[1:5]] + [float(parts[6]), float(parts[7])]
        except ValueError:
            continue
        rows.append({"label": parts[0], "measured_mb": vals[2],
                     "kT_lab_keV": vals[1]})
    return rows


def ef(s: str) -> float:
    s = str(s).strip()
    if not s:
        return 0.0
    try:
        return float(s)
    except ValueError:
        return float(re.sub(r"([+-])\s*(\d+)$", r"e\1\2", s))


def independent_decay_identities(member: Path) -> dict[tuple[int, int], float]:
    out: dict[tuple[int, int], tuple[int, float]] = {}
    cur, buf = None, []
    def flush():
        nonlocal cur, buf
        if cur is None or not buf:
            return
        mat, mf, mt = cur
        if mf != 8 or mt != 457:
            return
        f = [buf[0][i * 11:(i + 1) * 11] for i in range(6)]
        za, liso = int(ef(f[0])), int(f[3])
        t12 = ef([buf[1][i * 11:(i + 1) * 11] for i in range(6)][0])
        if (za, liso) not in out or mat > out[(za, liso)][0]:
            out[(za, liso)] = (mat, t12)
    with open(member, errors="replace") as fh:
        for line in fh:
            if len(line) < 75:
                continue
            try:
                mat, mf, mt = int(line[66:70]), int(line[70:72]), int(line[72:75])
            except ValueError:
                continue
            if mf == 0 or mt == 0 or mat <= 0:
                flush(); cur, buf = None, []
                continue
            if (mat, mf, mt) != cur:
                flush(); cur, buf = (mat, mf, mt), []
            buf.append(line)
    flush()
    return {k: v[1] for k, v in out.items()}


def product_za_for(mt: int, target_za: int) -> int | None:
    z, a = target_za // 1000, target_za % 1000
    return {4: target_za, 16: z * 1000 + a - 1, 17: z * 1000 + a - 2,
            102: z * 1000 + a + 1, 103: (z - 1) * 1000 + a,
            104: z * 1000 + a + 1, 105: z * 1000 + a + 2,
            106: (z - 1) * 1000 + a + 1, 107: (z - 2) * 1000 + a - 3}.get(mt)


def suffix_class(label: str) -> tuple[int, int, str] | None:
    """(target_za, mt, suffix_class) for simple labels; None for rml*."""
    if label.startswith("rml"):
        return None
    for suf in ("gg", "gm", "nm", "2m", "pm", "g", "f", "p", "a", "2", "m", "n"):
        if label.endswith(suf):
            m = re.fullmatch(r"([A-Z][a-z]?)(\d+)", label[: -len(suf)])
            if m is None:
                return None
            z = ELEMENT_Z[m.group(1)]
            mt = {"g": 102, "gg": 102, "gm": 102, "m": 102, "nm": 4, "n": 4,
                  "f": 18, "p": 103, "pm": 103, "a": 107, "2": 16, "2m": 16}[suf]
            cls = ("explicit_ground" if suf == "gg"
                   else "isomer" if suf in {"gm", "nm", "m", "n", "2m", "pm"}
                   else "bare")
            return (z * 1000 + int(m.group(2)), mt, cls)
    return None


def check_report(record: dict) -> list[str]:
    local = []
    if not record.get("protocol_sha256_verified"):
        local.append("protocol_sha256_verified is false")
    if not record.get("pass"):
        local.append("record pass is false")
    if not record.get("completeness_vs_p17", {}).get("complete"):
        local.append("completeness_vs_p17 is not complete")
    land = record.get("falsification_row_landing", {})
    if not land.get("ag109g_binds_declared_lfs2_partial"):
        local.append("Ag109g does not land on the declared LFS=2 partial")
    if not land.get("monitor_rows_ledgered_not_scored"):
        local.append("monitor rows not ledgered")
    rows = record.get("rows", [])
    if len(rows) != 94:
        local.append(f"row count {len(rows)} != 94")
    if len({r["source_id"] for r in rows}) != len(rows):
        local.append("duplicate source ids in the ledger")
    return local


def main() -> int:
    if sha256(PROTOCOL) != PROTOCOL_SHA256:
        failures.append("protocol sha256 mismatch")
    record = json.loads(RECORD.read_text())
    g1 = json.loads(G1_RECORD.read_text())
    g2 = json.loads(G2_AUDIT.read_text())

    if record["module_hashes"]["controls/p24_definitions.py"] != sha256(DEF_MODULE):
        failures.append("definition module hash mismatch vs live file")
    if record["module_hashes"]["controls/p24_scorer.py"] != sha256(SCORER):
        failures.append("scorer hash mismatch vs live file")
    if g1["frozen_definition_module"]["sha256"] != sha256(DEF_MODULE):
        failures.append("definition module hash differs from G1 freeze")
    if not g2.get("pass") or g2["freeze"]["scorer_sha256"] != sha256(SCORER):
        failures.append("G2 audit not green or scorer not the audited file")

    seal = json.loads((RESULTS / "g0_p24_seals.json").read_text())["artifacts"]
    ai = record.get("artifact_identities", {})
    if ai.get("candidate_npz") != seal.get("candidate_npz_sha256"):
        failures.append("candidate NPZ identity differs from G0 seal")
    if ai.get("baseline_npz") != seal.get("baseline_npz_sha256"):
        failures.append("baseline NPZ identity differs from G0 seal")
    if CANDIDATE_NPZ.exists() and sha256(CANDIDATE_NPZ) != ai.get("candidate_npz"):
        failures.append("candidate NPZ hash mismatch vs record")

    # --- independent re-parse and reason re-derivation -------------------
    with tempfile.TemporaryDirectory(dir=REPO / "target" / "preflight-tmp") as td:
        with zipfile.ZipFile(DECAY) as zf:
            zf.extract("IRDFF-II_dd.endf", td)
        decays = independent_decay_identities(Path(td) / "IRDFF-II_dd.endf")

    lam_m = math.log(2.0) / decays[(27058, 0)]

    alias = {(e["target_za"], e["mt"], e["interpreted_suffix"]): e["binding"]
             for e in g1["alias_table"]}
    rows = {r["source_id"]: r for r in record["rows"]}
    my_indexed = []
    for table in (23, 25):
        for i, s in enumerate(parse_si_table_independent(table), 1):
            s["table"] = table
            s["table_row"] = i
            my_indexed.append(s)
    if len(my_indexed) != 73:
        failures.append(f"independent re-parse got {len(my_indexed)} SI rows != 73")

    monitor_eoi = {}
    for s in my_indexed:
        if s["label"].startswith("Ni58p"):
            monitor_eoi[s["table"]] = s["measured_EOI_per_atom"]

    n_reason_mismatch = 0
    resid_max = (None, 0.0)
    n_scored_si = 0
    for s in my_indexed:
        sid = f"IRDFF-II:Table-{s['table']}:row-{s['table_row']:03d}"
        rec = rows.get(sid)
        if rec is None:
            failures.append(f"{sid} missing from the ledger")
            continue
        # independent inclusion reason
        lab = s["reaction_label"]
        info = suffix_class(lab)
        mt = info[1] if info else 18  # rml* are fission foils
        field = "SPR-III central cavity" if s["table"] == 23 else "ACRR central cavity"
        if s["label"].startswith("Ni58p"):
            want = "monitor_identity_not_predictive"
        elif s["cover"] in UNSUPPORTED_COVERS:
            want = "unsupported_self_shielding"
        elif info is None:
            want = "unmapped_target_reaction_product"
        elif info not in alias or alias[info].get("status") != "bound":
            want = "undefined_state_alias"
        elif mt == 102 and field != "LB44 Maxwellian":
            want = "unsupported_self_shielding"
        else:
            want = "scored"
        got = rec["inclusion"]["reason"]
        if got != want:
            n_reason_mismatch += 1
            failures.append(f"{sid}: reason {got} != independently derived {want}")
        if want != "scored":
            continue
        n_scored_si += 1
        # independent pulse-limit reconstruction
        b = alias[info]
        ratio = s["measured_EOI_per_atom"] / monitor_eoi[s["table"]]
        if info[1] == 18 or lab.startswith("rml"):
            mine = ratio * lam_m
        else:
            leg = (b.get("decay_leg") or {})
            if leg.get("status") != "bound":
                failures.append(f"{sid}: scored row has unbound decay leg")
                continue
            pza = b.get("product_za") or product_za_for(mt, info[0])
            hl = decays.get((int(pza), int(leg["decay_product_liso"])))
            if not hl:
                failures.append(f"{sid}: no decay half-life for product")
                continue
            mine = ratio * lam_m / (math.log(2.0) / hl)
        got_v = rec["experimental"]["value"]
        if got_v is None or abs(got_v - mine) / mine > 1e-9:
            failures.append(f"{sid}: experimental {got_v} != independent {mine}")
        inferred = s["published_spectral_index"] / s["published_SI_C_over_E"]
        r_ = abs(mine - inferred) / inferred
        if r_ > resid_max[1]:
            resid_max = (sid, r_)

    # --- Maxwellian family ------------------------------------------------
    h3 = parse_maxwellian_independent()
    if len(h3) != EXPECTED_ROWS[36]:
        failures.append(f"independent T36 re-parse got {len(h3)} != 21")
    h3_ids = {f"IRDFF-II:Table-36:row-{i:03d}" for i in range(1, len(h3) + 1)}
    for sid in h3_ids:
        rec = rows.get(sid)
        if rec is None:
            failures.append(f"{sid} missing from the ledger")
            continue
        if rec["inclusion"]["reason"] != "scored":
            failures.append(f"{sid}: Maxwellian row not scored ({rec['inclusion']['reason']})")
        v = rec["experimental"]["value"]
        i = int(sid.rsplit("-", 1)[1]) - 1
        if v is None or abs(v - h3[i]["measured_mb"]) / h3[i]["measured_mb"] > 1e-9:
            failures.append(f"{sid}: experimental != measured_mb")

    # --- candidate unavailability must equal the G0 construction ledger ---
    build = json.loads(G0_BUILD.read_text())
    failed_zas = set()
    for name in build.get("construction_failed_names", []):
        m = re.fullmatch(r"n-([A-Z][a-z]?)(\d+)m?\.tendl", name)
        if m:
            failed_zas.add(ELEMENT_Z[m.group(1)] * 1000 + int(m.group(2)))
    unavailable_num_zas = set()
    unavailable_mon_zas = set()
    unattributed = []
    for r in record["rows"]:
        c = r["calculations"].get("candidate", {})
        if c.get("reason") != "variant_target_unavailable":
            continue
        legs = c.get("unavailable_legs")
        if legs:
            for leg in legs:
                if leg["leg"] == "numerator":
                    unavailable_num_zas.add(int(leg["target_za"]))
                else:
                    unavailable_mon_zas.add(int(leg["target_za"]))
            continue
        # Maxwellian SACS rows: the unavailable target is the row's own
        lab = r["source_record"].get("reaction_label") or r["source_record"].get("label")
        info = suffix_class(lab) if lab else None
        if info:
            unavailable_num_zas.add(info[0])
        else:
            unattributed.append(r["row_id"])
    if unattributed:
        failures.append(f"unattributed candidate-unavailable rows: {unattributed}")
    extra = unavailable_num_zas - failed_zas
    if extra:
        failures.append(
            f"candidate numerator-unavailable targets outside the construction "
            f"ledger: {sorted(extra)}")
    if unavailable_mon_zas - {28058}:
        failures.append(f"unexpected monitor targets unavailable: {sorted(unavailable_mon_zas)}")
    if not unavailable_mon_zas <= failed_zas:
        failures.append("monitor target missing but not in the construction ledger")

    # --- structural + mutations ------------------------------------------
    failures.extend(check_report(record))
    mutations = 0
    rejected = 0
    plants = [
        lambda r: r.update({"pass": False}),
        lambda r: r["completeness_vs_p17"].update({"complete": False}),
        lambda r: r["falsification_row_landing"].update(
            {"ag109g_binds_declared_lfs2_partial": False}),
        lambda r: r["rows"].pop(),
        lambda r: r["rows"].append(copy.deepcopy(r["rows"][0])),
    ]
    for plant in plants:
        m_ = copy.deepcopy(record)
        plant(m_)
        mutations += 1
        if check_report(m_):
            rejected += 1
    if rejected != mutations:
        failures.append(f"mutation self-test: {rejected}/{mutations} rejected")

    result = {
        "schema": "actinv-p24-g3-check-1",
        "pass": not failures,
        "failures": failures,
        "protocol_sha256": PROTOCOL_SHA256,
        "independent_rescore": {
            "si_rows_reparsed": len(my_indexed),
            "si_rows_scored": n_scored_si,
            "maxwellian_rows_reparsed": len(h3),
            "worst_pulse_residual": {"row": resid_max[0], "residual": resid_max[1]},
            "reason_mismatches": n_reason_mismatch,
            "candidate_unavailable_numerator_targets": sorted(unavailable_num_zas),
            "candidate_unavailable_monitor_targets": sorted(unavailable_mon_zas),
            "construction_failed_targets": sorted(failed_zas),
        },
        "mutation_self_test": {"planted": mutations, "rejected": rejected},
    }
    out = RESULTS / "g3_p24_check.json"
    out.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")
    print(json.dumps(result, indent=1, sort_keys=True))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
