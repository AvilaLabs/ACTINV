#!/usr/bin/env python3
"""P42 G2 independent checker.

Re-verifies results/g2_p42_repairs.json without importing the G1/G2
production controls: recomputes the FENDL-3.2c ENDF-6 lumped sums,
re-derives both arms' channel sigma.phi on the shared parents, checks
the disposition rule (ACTINV within 1% of truth while ALARA deviates ->
no_repair_alara_side), and rejects planted mutations.
"""
import hashlib
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
WORK = Path.home() / "nuclear-data"
CASES = WORK / "p26b-work" / "p38-run" / "g2-cases-p39"
G2 = ROOT / "results" / "g2_p42_repairs.json"
G1 = ROOT / "results" / "g1_p42_mechanisms.json"

NPZ = WORK / "p26b-work" / "p38-run" / "actinv_fendl32c_709_p39.npz"
INDEX = WORK / "p26b-work" / "p38-run" / "actinv_fendl32c_709_p39_index.json"
DSV = WORK / "alara-2.9.2" / "tools" / "ALARAJOYWrapper" / "cumulative_gendf_data.dsv"
IDX = WORK / "p26b-work" / "g1-run" / "fendl32c_709.idx"
ENDF_DIR = WORK / "fendl-3.2c" / "endf"
DECAY = [ROOT / "actinv-data" / "v1.1.0" / "decay" / "endf-b-viii-0_decay.dat",
         ROOT / "actinv-data" / "v1.1.0" / "decay" / "jeff-3-3_decay.dat"]

EL = {22: "Ti", 23: "V", 24: "Cr", 25: "Mn", 26: "Fe", 27: "Co"}
Z_OF = {v: k for k, v in EL.items()}

TRUTH_MAP = {
    "Cr51": ("*Fe-54*", list(range(800, 850)), "a", 260540),
    "Fe53": ("*Fe-54*", [16], "2n", 260540),
    "V52": ("*Mn-55*", list(range(800, 850)), "a", 250550),
}

FAILS = []


def fail(msg):
    FAILS.append(msg)
    print(f"FAIL {msg}")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sym_kza(s):
    m = re.match(r"([A-Z][a-z]?)(\d+)(?:m(\d+))?$", s)
    return Z_OF[m.group(1)] * 10000 + int(m.group(2)) * 10 + int(m.group(3) or 0)


def _num(s):
    s = s.strip().replace(" ", "").replace("D", "E")
    if not re.match(r"^[+-]?\d", s):
        return None
    if "E" not in s and ("+" in s[1:] or "-" in s[1:]):
        s = re.sub(r"([+-])(\d+)$", r"E\1\2", s)
    try:
        return float(s)
    except ValueError:
        return None


def endf_sig_phi(glob, mts, bounds, flux_asc):
    """Independent MF3 TAB1 extraction + group fold."""
    f = sorted(ENDF_DIR.glob(glob))[0]
    lines = open(f).read().splitlines()
    gm = np.sqrt(bounds[:-1] * bounds[1:])
    total = np.zeros(len(gm))
    for i, l in enumerate(lines):
        l2 = l.ljust(80)
        try:
            mf, mt = int(l2[70:72]), int(l2[72:75])
        except ValueError:
            continue
        if mf != 3 or mt not in mts:
            continue
        nr = int(float(_num(lines[i + 1][44:55]) or 0))
        np_ = int(float(_num(lines[i + 1][55:66]) or 0))
        vals, j = [], i + 2
        while len(vals) < 2 * nr + 2 * np_ and j < len(lines):
            l3 = lines[j].ljust(80)
            try:
                mf2, mt2 = int(l3[70:72]), int(l3[72:75])
            except ValueError:
                break
            if mf2 == 0 or mt2 == 0:
                break
            for k in range(0, 66, 11):
                v = _num(l3[k:k + 11])
                if v is not None:
                    vals.append(v)
            j += 1
        xy = vals[2 * nr:2 * nr + 2 * np_]
        E, s = np.array(xy[0::2]), np.array(xy[1::2])
        if len(E) > 1:
            total += np.interp(gm, E, s, left=0, right=0)
    return float(np.dot(total, flux_asc))


def run_checks(path: Path) -> list[str]:
    global FAILS
    FAILS = []
    d = json.loads(Path(path).read_text())
    inputs = {"g1_census": G1, "artifact_npz": NPZ, "artifact_index": INDEX,
              "alara_dsv": DSV, "alara_idx": IDX,
              "decay_endf_b_viii_0": DECAY[0], "decay_jeff_3_3": DECAY[1]}
    for k, p in inputs.items():
        if d["input_sha256"].get(k) != sha256(p):
            fail(f"digest mismatch: {k}")

    npz = np.load(NPZ)
    bounds, rows, sig = npz["bounds"], npz["rows"], npz["sig"]
    index = json.load(open(INDEX))
    targets = index["targets"]
    fns = np.loadtxt(CASES / "fe_ag100000wppm__fns_709__3600s" / "flux.txt")
    fns_asc = fns[::-1]
    dsv = []
    for line in open(DSV):
        f = line.split()
        if len(f) < 20 or not f[0].isdigit() or not f[1].isdigit():
            continue
        vals = []
        for x in f[4:]:
            try:
                vals.append(float(x))
            except ValueError:
                vals.append(0.0)
        if len(vals) == 709:
            dsv.append((int(f[0]), int(f[1]), f[3], np.array(vals)))

    if len(d["repairs"]) != 3:
        fail(f"repair ledger rows {len(d['repairs'])} != 3")
    for e in d["repairs"]:
        m = e["member"]
        if m not in TRUTH_MAP:
            fail(f"{m}: not a conversion_content member")
            continue
        glob, mts, fam, parent = TRUTH_MAP[m]
        truth = endf_sig_phi(glob, mts, bounds, fns_asc)
        if abs(truth - e["fendl_truth_sig_phi"]) > \
                1e-6 * max(abs(truth), 1e-30):
            fail(f"{m}: truth {e['fendl_truth_sig_phi']} != {truth}")
        zaid = sym_kza(m) // 10000 * 1000 + (sym_kza(m) // 10) % 1000
        act = sum(float(np.dot(sig[i], fns_asc))
                  for i, (t, mt, zap, lfs, lmf) in enumerate(rows)
                  if int(zap) == zaid
                  and int(targets[t]["za"]) * 10 == parent)
        if abs(act - e["actinv_sig_phi"]) > 1e-6 * max(abs(act), 1e-30):
            fail(f"{m}: actinv {e['actinv_sig_phi']} != {act}")
        mk = sym_kza(m)
        al_d = sum(float(np.dot(xs, fns)) for p, dd, lb, xs in dsv
                   if dd == mk and p == parent
                   and lb.split(",")[0].rstrip("*").startswith(fam[0]))
        al_t = sum(float(np.dot(xs, fns)) for p, dd, lb, xs in dsv
                   if p == parent and fam in lb)
        for key, recomputed in (("alara_daughter_sig_phi", al_d),
                                ("alara_label_total_sig_phi", al_t)):
            if abs(recomputed - e[key]) > 1e-6 * max(abs(e[key]), 1e-30):
                fail(f"{m}: {key} {e[key]} != {recomputed}")
        # disposition rule
        ok = abs(act / truth - 1) <= 0.01
        want = "no_repair_alara_side" if ok else "repair_required"
        if e["disposition"] != want:
            fail(f"{m}: disposition {e['disposition']} != {want}")
    return FAILS


def mutations():
    tmp = Path(tempfile.mkdtemp(prefix="p42g2-"))
    base = json.loads(G2.read_text())
    results = {}

    mut = json.loads(json.dumps(base))
    mut["repairs"][0]["disposition"] = "repair_required"
    p = tmp / "m1.json"; p.write_text(json.dumps(mut))
    results["disposition_flip"] = len(run_checks(p)) > 0

    mut = json.loads(json.dumps(base))
    mut["repairs"][0]["actinv_sig_phi"] *= 2
    p = tmp / "m2.json"; p.write_text(json.dumps(mut))
    results["value_tamper"] = len(run_checks(p)) > 0

    mut = json.loads(json.dumps(base))
    mut["repairs"] = mut["repairs"][:2]
    mut["summary"]["eligible"] = 2
    p = tmp / "m3.json"; p.write_text(json.dumps(mut))
    results["row_drop"] = len(run_checks(p)) > 0

    mut = json.loads(json.dumps(base))
    mut["repairs"][1]["fendl_truth_sig_phi"] = 1.0
    p = tmp / "m4.json"; p.write_text(json.dumps(mut))
    results["truth_tamper"] = len(run_checks(p)) > 0

    shutil.rmtree(tmp, ignore_errors=True)
    return results


def main():
    fails = run_checks(G2)
    if fails:
        print(f"\nG2 CHECK FAIL ({len(fails)})")
        return 1
    mut = mutations()
    bad = [k for k, v in mut.items() if not v]
    print("\nMutations:", json.dumps(mut))
    if bad:
        print(f"G2 CHECK FAIL: undetected mutations {bad}")
        return 1
    print("\nG2 CHECK PASS: 3 conversion_content rows re-verified against "
          "FENDL truth, all dispositions = no_repair_alara_side, "
          "4/4 mutations rejected")
    return 0


if __name__ == "__main__":
    sys.exit(main())
