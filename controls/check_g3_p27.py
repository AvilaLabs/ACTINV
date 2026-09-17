#!/usr/bin/env python3
"""G3 checker for P27: verify the adversarial battery record and
independently replay the pure-python rejections.

Checks: the battery record exists, covers exactly the 12 classes frozen at
G0, and every item is recorded rejected; the receipt/failure surfaces used
by the battery still reject (re-derived here, not trusted); the committed
interchange package digests still bind real bytes.
"""
import copy
import glob
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "interchange"))
import actinv_core_adapter as A  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.expanduser("~/nuclear-data/p27-work")


def sh(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def main():
    fs = []
    g0 = json.load(open(os.path.join(REPO, "results", "g0_p27_seals.json")))
    frozen = set(g0["adversarial_battery"])
    battery = json.load(open(os.path.join(REPO, "results",
                                          "g3_p27_battery.json")))

    seen = {i["item"] for i in battery["battery"]}
    if seen != frozen:
        fs.append(f"battery items {sorted(seen)} != frozen {sorted(frozen)}")
    for i in battery["battery"]:
        if not i["rejected"]:
            fs.append(f"{i['item']} recorded as ACCEPTED")

    # independent replays of the pure-python rejection surfaces
    recp = os.path.join(WORK, "interchange", "ws2")
    receipts = sorted(glob.glob(os.path.join(recp, "*", "receipt.json")))
    if len(receipts) != 8:
        fs.append(f"expected 8 receipts, found {len(receipts)}")
    manifest = json.load(open(os.path.join(WORK, "study", "run1",
                                           "manifest.json")))
    spec_digests = {c["case_id"]: c["spec_sha256"]
                    for c in manifest["cases"]}
    actinv_sha = sh(os.path.join(REPO, "target", "release", "actinv"))

    replays = 0
    for rp in receipts:
        rec = json.load(open(rp))
        cid = os.path.basename(os.path.dirname(rp))
        st, ev, fail = A.interpret_receipt(
            rec, expected_spec_sha256=spec_digests[cid],
            expected_binary_sha256=actinv_sha)
        if st != "executed" or fail:
            fs.append(f"{cid}: genuine receipt not accepted: {fail}")
        replays += 1
        # forged spec digest must be refused
        st2, _, f2 = A.interpret_receipt(
            copy.deepcopy(rec), expected_spec_sha256="0" * 64,
            expected_binary_sha256=actinv_sha)
        if st2 == "executed" and not f2:
            fs.append(f"{cid}: forged spec digest accepted")
        # wrong binary must be refused
        st3, _, f3 = A.interpret_receipt(
            copy.deepcopy(rec), expected_spec_sha256=spec_digests[cid],
            expected_binary_sha256="0" * 64)
        if st3 == "executed" and not f3:
            fs.append(f"{cid}: wrong executable accepted")

    # package document digests still bind real bytes
    ev = json.load(open(os.path.join(REPO, "results",
                                     "g2_p27_interchange.json")))
    pkg = json.load(open(os.path.join(ev["package_dir"], "package.json")))
    for d in pkg["documents"]:
        p = os.path.join(ev["package_dir"], d["path"])
        if not os.path.isfile(p) or d["sha256"] != "sha256:" + sh(p):
            fs.append(f"package doc {d['path']} digest drift")

    result = {
        "gate": "G3", "phase": "P27",
        "pass": not fs,
        "failures": fs,
        "receipts_replayed": replays,
        "battery": {"planted": battery["planted"],
                    "rejected": battery["rejected"]},
    }
    json.dump(result, open(os.path.join(REPO, "results",
                                        "g3_p27_check.json"), "w"),
              indent=2, sort_keys=True)
    print(json.dumps(result, indent=1))
    sys.exit(0 if result["pass"] else 1)


if __name__ == "__main__":
    main()
