#!/usr/bin/env python3
"""P42 G3: re-census and residual-decomposition report.

G2 ledgered zero repairs (all conversion_content rows are ALARA-side:
ACTINV matches FENDL truth within 0.3%). Therefore the identical census
inputs re-run through the unchanged layer must reproduce P40's record
byte-identically — verified here by digest — and the residual
decomposition maps every P42 member divergence onto its mechanism
class.

The report additionally carries a corrected-extraction overlay: the
P26b stdout resolver can mis-assign element-ambiguous '-NN' rows when
several isobars coexist (Ti-53 vs Cr-53, Ti-55 vs Cr-55); the G1 census
case_evidence values use t_1/2-matched rows and are the authoritative
per-case arm values for the 13 members.

Output: results/g3_p42_recensus.json
"""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "results" / "g3_p42_recensus.json"
P40 = ROOT / "results" / "g2_p40_classes.json"
G1 = ROOT / "results" / "g1_p42_mechanisms.json"
G2 = ROOT / "results" / "g2_p42_repairs.json"
VERDICT40 = ROOT / "results" / "verdict_p40.json"


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    before = sha256(P40)
    # Re-run the identical census (unchanged layer; zero repairs).
    r = subprocess.run([sys.executable,
                        str(ROOT / "controls" / "g2_p40_classes.py")],
                       capture_output=True, text=True)
    after = sha256(P40)
    byte_identical = (before == after)

    g1 = json.loads(G1.read_text())
    g2 = json.loads(G2.read_text())
    p40 = json.loads(P40.read_text())

    # Per-nuclide ledger: every P42 member with its mechanism class and
    # repair disposition.
    dispos = {e["member"]: e["disposition"] for e in g2["repairs"]}
    ledger = []
    for c in g1["census"]:
        ledger.append({
            "member": c["member"],
            "p40_class": c["p40_class"],
            "mechanism": c["primary_class"],
            "repair": dispos.get(c["member"], "not_eligible"),
            "worst_case": c["worst_case"],
            "per_material_class": c["per_material_class"]})
    by_class = {}
    for e in ledger:
        by_class.setdefault(e["mechanism"], []).append(e["member"])

    # Per-class before/after shares: unchanged (no repairs), but report
    # the mechanism attribution of P40's class map members.
    rep = {
        "schema": "g3_p42_recensus/1",
        "input_sha256": {
            "p40_classes_before": before,
            "p40_classes_after_recensus": after,
            "g1_census": sha256(G1),
            "g2_repairs": sha256(G2),
            "p40_verdict": sha256(VERDICT40)},
        "byte_identical_to_p40": byte_identical,
        "recensus_returncode": r.returncode,
        "n_cases": p40["n_cases"],
        "mechanism_attribution": {
            "members": ledger,
            "by_class": by_class,
            "alara_heritage_count": len(by_class.get("alara_heritage", [])),
            "conversion_content_count":
                len(by_class.get("conversion_content", [])),
            "true_defect_count": len(by_class.get("true_defect", [])),
            "unresolved_count": len(by_class.get("unresolved", []))},
        "equivalence": {
            "worst_raw_dev": p40["worst_raw_dev"],
            "worst_clean_dev": p40["worst_clean_dev"],
            "worst_other_share": p40["worst_other_share"]},
        "extraction_overlay_note": (
            "G1's t_1/2-matched stdout parser found ALARA values for "
            "Ti-53/Ti-55 that the P26b element-resolution dropped "
            "(isobar ambiguity); P40's arm-only shares for those rows "
            "understate ALARA's side. Corrected per-case values are in "
            "g1_p42_mechanisms.json case_evidence. No ACTINV-side "
            "values changed."),
    }
    OUT.write_text(json.dumps(rep, indent=1))
    print(json.dumps({"byte_identical": byte_identical,
                      "returncode": r.returncode,
                      "by_class": by_class}, indent=1))
    return 0 if byte_identical else 1


if __name__ == "__main__":
    sys.exit(main())
