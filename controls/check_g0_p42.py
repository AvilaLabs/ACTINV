#!/usr/bin/env python3
"""P42 G0 checker: seals exist, digests verify, no premature content."""
import copy
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(ROOT, "results")
SEALS_F = os.path.join(RES, "g0_p42_seals.json")
OUT = os.path.join(RES, "g0_p42_check.json")

EVIDENCE_FILES = ("out.json", "case.stdout", "alara.dmp")


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def check(seals, fs):
    if seals.get("schema") != "actinv-p42-g0-1" or seals.get("phase") != "P42":
        fs.append("schema/phase wrong")
    if sha(os.path.join(ROOT, "protocols", "ACTINV-P42_PROTOCOL.md")) \
            != seals.get("protocol_sha256"):
        fs.append("protocol digest drifted")

    # P40 verdict must be asserted verbatim and its digest must verify
    pv = seals.get("prior_verdicts", {})
    v40_p = os.path.join(RES, "verdict_p40.json")
    if pv.get("verdict_p40.json") != "P40-CONDITIONAL":
        fs.append("P40 not closed CONDITIONAL as prerequisite")
    if pv.get("verdict_p40_sha256") != sha(v40_p):
        fs.append("P40 verdict digest drifted")

    # artifact identity must match the P40 seal exactly
    art = seals.get("identities", {}).get("actinv_fendl32c_artifact", {})
    if not os.path.isfile(art.get("path", "")):
        fs.append("artifact missing")
    elif sha(art["path"]) != art.get("sha256"):
        fs.append("artifact digest drifted")
    if art.get("sha256") != art.get("expected_sha256"):
        fs.append("artifact identity != P40 seal")

    # census case-dir tree must rehash identically
    cen = seals.get("census", {})
    case_root = cen.get("case_root", "")
    if cen.get("files_hashed", 0) <= 0 or cen.get("missing_files"):
        fs.append("census seal incomplete")
    tree = hashlib.sha256()
    n = 0
    try:
        cases = sorted((p for p in os.scandir(case_root) if p.is_dir()),
                       key=lambda p: p.name)
    except OSError:
        cases = []
        fs.append("case root unreadable")
    for cdir in cases:
        for name in EVIDENCE_FILES:
            fp = os.path.join(cdir.path, name)
            if not os.path.isfile(fp):
                continue
            tree.update(cdir.name.encode())
            tree.update(name.encode())
            tree.update(sha(fp).encode())
            n += 1
    if tree.hexdigest() != cen.get("tree_sha256"):
        fs.append("census tree digest drifted")
    if n != cen.get("files_hashed"):
        fs.append("census file count drifted")

    # frozen vocabulary and open-class membership are the P40 names
    vocab = seals.get("frozen_vocabulary", [])
    if len(vocab) != 8 or len(set(vocab)) != 8:
        fs.append("frozen vocabulary malformed")
    oc = seals.get("open_classes", {})
    if set(oc) != {"short_lived_products_absent_in_actinv",
                   "common_nuclide_magnitude"}:
        fs.append("open-class keys wrong")
    if len(oc.get("common_nuclide_magnitude", {}).get("members", [])) != 8:
        fs.append("magnitude-class membership drifted")
    if len(oc.get("short_lived_products_absent_in_actinv", {})
           .get("members", [])) != 5:
        fs.append("short-lived-class membership drifted")

    # no fresh case content may already sit in committed results
    for fname in os.listdir(RES):
        if fname.startswith("g1_p42") or fname.startswith("g2_p42") \
                or fname.startswith("g3_p42") or fname.startswith("g4_p42"):
            fs.append(f"premature P42 evidence file: {fname}")

    # protocol ledger line must exist
    ledger = open(os.path.join(ROOT, "protocols", "protocol_hash.txt"),
                  encoding="utf-8").read()
    if "ACTINV-P42_PROTOCOL.md" not in ledger:
        fs.append("protocol_hash.txt lacks the P42 ledger line")


def main():
    seals = json.load(open(SEALS_F))
    fs = []
    check(seals, fs)
    planted = rejected = 0
    for label, mut in [
        ("phase", lambda v: v.__setitem__("phase", "P41")),
        ("verdict", lambda v: v["prior_verdicts"]
         .__setitem__("verdict_p40.json", "P40-PASS")),
        ("artifact", lambda v: v["identities"]["actinv_fendl32c_artifact"]
         .__setitem__("sha256", "0" * 64)),
        ("tree", lambda v: v["census"].__setitem__("tree_sha256", "0" * 64)),
        ("vocab", lambda v: v.__setitem__("frozen_vocabulary",
                                        v["frozen_vocabulary"][:-1])),
        ("members", lambda v: v["open_classes"]["common_nuclide_magnitude"]
         .__setitem__("members", ["Cr51"])),
    ]:
        planted += 1
        v = copy.deepcopy(seals)
        mut(v)
        mfs = []
        try:
            check(v, mfs)
        except Exception:
            mfs = ["crashed"]
        rejected += 1 if mfs else 0
        if not mfs:
            print("  UNREJECTED", label, file=sys.stderr)
    out = {"gate": "G0", "phase": "P42", "failures": fs,
           "mutation_self_test": {"planted": planted,
                                  "rejected": rejected},
           "pass": not fs and planted == rejected}
    json.dump(out, open(OUT, "w"), indent=1, sort_keys=True)
    print(json.dumps(out, indent=1))
    sys.exit(0 if out["pass"] else 1)


if __name__ == "__main__":
    main()
