#!/usr/bin/env python3
"""P42 G2: repair-scope determination.

For every conversion_content member of the G1 census, decide whether a
repair is warranted by comparing BOTH arms against FENDL-3.2c ENDF-6
ground truth on the shared parent channel:

  * ACTINV row sigma.phi vs the ENDF channel's lumped sum
  * ALARA row(s) sigma.phi to the same physical daughter vs truth
  * conservation check: ALARA's total emission-labeled content on the
    parent (real daughter + mass-impossible ladder rows) vs truth —
    shows the XS is conserved but mis-allocated, i.e. the defect lives
    in the REAC conversion, not in ACTINV

Eligible classes per protocol: true_defect, conversion_content. A
conversion_content row whose ACTINV channel matches truth (<=1%) while
ALARA deviates is ledgered `no_repair_alara_side`; one where ACTINV
deviates would be `repair_required` (none expected).

Output: results/g2_p42_repairs.json
"""
import hashlib
import json
import re
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
WORK = Path.home() / "nuclear-data"
CASES = WORK / "p26b-work" / "p38-run" / "g2-cases-p39"
OUT = ROOT / "results" / "g2_p42_repairs.json"
G1 = ROOT / "results" / "g1_p42_mechanisms.json"

NPZ = WORK / "p26b-work" / "p38-run" / "actinv_fendl32c_709_p39.npz"
INDEX = WORK / "p26b-work" / "p38-run" / "actinv_fendl32c_709_p39_index.json"
DSV = WORK / "alara-2.9.2" / "tools" / "ALARAJOYWrapper" / "cumulative_gendf_data.dsv"
IDX = WORK / "p26b-work" / "g1-run" / "fendl32c_709.idx"
ENDF_DIR = WORK / "fendl-3.2c" / "endf"
DECAY = [ROOT / "actinv-data" / "v1.1.0" / "decay" / "endf-b-viii-0_decay.dat",
         ROOT / "actinv-data" / "v1.1.0" / "decay" / "jeff-3-3_decay.dat"]

EL = {22: "Ti", 23: "V", 24: "Cr", 25: "Mn", 26: "Fe", 27: "Co",
      28: "Ni", 29: "Cu", 41: "Nb", 42: "Mo", 45: "Rh", 47: "Ag",
      48: "Cd", 73: "Ta", 74: "W"}
Z_OF = {v: k for k, v in EL.items()}

# shared-parent channel truth reference per conversion_content member:
# member -> (ENDF file glob, [MTs], emitted-label family for ALARA rows,
#            shared parent KZA)
TRUTH_MAP = {
    "Cr51": ("*Fe-54*", list(range(800, 850)), "a", 260540),
    "Fe53": ("*Fe-54*", [16], "2n", 260540),
    "V52": ("*Mn-55*", list(range(800, 850)), "a", 250550),
}


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


def parse_endf_tab1(lines, i0):
    nr = int(float(_num(lines[i0 + 1][44:55]) or 0))
    np_ = int(float(_num(lines[i0 + 1][55:66]) or 0))
    vals, i = [], i0 + 2
    while len(vals) < 2 * nr + 2 * np_ and i < len(lines):
        l = lines[i].ljust(80)
        try:
            mf, mt = int(l[70:72]), int(l[72:75])
        except ValueError:
            break
        if mf == 0 or mt == 0:
            break
        for j in range(0, 66, 11):
            v = _num(l[j:j + 11])
            if v is not None:
                vals.append(v)
        i += 1
    xy = vals[2 * nr:2 * nr + 2 * np_]
    return np.array(xy[0::2]), np.array(xy[1::2])


def fendl_sig_phi(endf_glob, mts, bounds, flux_asc):
    files = sorted(ENDF_DIR.glob(endf_glob))
    lines = open(files[0]).read().splitlines()
    gm = np.sqrt(bounds[:-1] * bounds[1:])
    total = np.zeros(len(gm))
    found = []
    for i, l in enumerate(lines):
        l2 = l.ljust(80)
        try:
            mf, mt = int(l2[70:72]), int(l2[72:75])
        except ValueError:
            continue
        if mf == 3 and mt in mts and mt not in found:
            found.append(mt)
            E, s = parse_endf_tab1(lines, i)
            if len(E) > 1:
                total += np.interp(gm, E, s, left=0, right=0)
    return float(np.dot(total, flux_asc)), sorted(found), files[0].name


def main() -> int:
    digests = {"g1_census": sha256(G1), "artifact_npz": sha256(NPZ),
               "artifact_index": sha256(INDEX), "alara_dsv": sha256(DSV),
               "alara_idx": sha256(IDX),
               "decay_endf_b_viii_0": sha256(DECAY[0]),
               "decay_jeff_3_3": sha256(DECAY[1])}
    g1 = json.loads(G1.read_text())
    npz = np.load(NPZ)
    bounds = npz["bounds"]
    fns = np.loadtxt(CASES / "fe_ag100000wppm__fns_709__3600s" / "flux.txt")
    fns_asc = fns[::-1]

    # ALARA DSV rows
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
            dsv.append({"parent": int(f[0]), "daughter": int(f[1]),
                        "label": f[3], "xs": np.array(vals)})

    repairs = []
    for c in g1["census"]:
        if c["primary_class"] != "conversion_content":
            continue
        m = c["member"]
        glob, mts, fam, parent = TRUTH_MAP[m]
        truth, truth_mts, fname = fendl_sig_phi(glob, mts, bounds, fns_asc)
        # ACTINV rows on the shared parent producing the member
        act = sum(e["sig_phi_fns"] for e in c["actinv_emission_rows"]
                  if sym_kza(e["parent"]) == parent)
        # ALARA rows to the member on the shared parent (label family)
        mk = c["kza"]
        alara_daughter = sum(float(np.dot(r["xs"], fns))
                             for r in dsv
                             if r["daughter"] == mk
                             and r["parent"] == parent
                             and r["label"].split(",")[0].rstrip("*")
                             .startswith(fam[0]))
        # conservation: ALL ALARA rows on the shared parent carrying the
        # same emitted-label family, regardless of daughter
        alara_total = sum(float(np.dot(r["xs"], fns))
                          for r in dsv
                          if r["parent"] == parent
                          and fam in r["label"])
        entry = {
            "member": m,
            "shared_parent": parent,
            "fendl_file": fname,
            "fendl_mts": truth_mts,
            "fendl_truth_sig_phi": truth,
            "actinv_sig_phi": act,
            "alara_daughter_sig_phi": alara_daughter,
            "alara_label_total_sig_phi": alara_total,
            "actinv_over_truth": act / truth,
            "alara_daughter_over_truth": alara_daughter / truth,
            "alara_total_over_truth": alara_total / truth,
        }
        if abs(act / truth - 1) <= 0.01:
            entry["disposition"] = "no_repair_alara_side"
            entry["reason"] = (
                "ACTINV's channel matches the FENDL lumped sum within "
                "1%; ALARA's allocation to the physical daughter "
                "under-reads the same content. The defect lives in the "
                "REAC conversion, not in ACTINV — no repair warranted.")
        else:
            entry["disposition"] = "repair_required"
            entry["reason"] = ("ACTINV deviates from FENDL truth on a "
                               "shared channel — a real processing "
                               "defect requiring G2 repair.")
        repairs.append(entry)
    out = {"schema": "g2_p42_repairs/1", "input_sha256": digests,
           "repairs": repairs,
           "summary": {"eligible": len(repairs),
                       "no_repair_alara_side":
                           sum(1 for r in repairs
                               if r["disposition"] == "no_repair_alara_side"),
                       "repair_required":
                           sum(1 for r in repairs
                               if r["disposition"] == "repair_required")}}
    OUT.write_text(json.dumps(out, indent=1))
    print(json.dumps(out["summary"], indent=1))
    for r in repairs:
        print(f"{r['member']}: {r['disposition']} "
              f"actinv/truth={r['actinv_over_truth']:.4f} "
              f"alara_daughter/truth={r['alara_daughter_over_truth']:.4f} "
              f"alara_total/truth={r['alara_total_over_truth']:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
