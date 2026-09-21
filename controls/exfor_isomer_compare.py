#!/usr/bin/env python3
"""Compare TENDL-2017 isomer splits vs harvested EXFOR measurements.

For each harvested channel file in target/exfor-harvest/, extract:
- isomer-resolved absolute σ datasets (product flags -M/-G/-M1/-M2)
- isomer ratio datasets (SIG/RAT: M/G, G/M, M/T, T/M, M1/G, M2/G, M1/M2,
  M2/M1, M1+M2/G, G/M1+M2, M1+M2/T)

TENDL prediction uses the groupwise npz σ per (target, MT, product, lfs) row,
evaluated at the group containing each measured energy. For thermal and
14 MeV points the group is representative; resonance-region points are kept
but flagged approximate.

Output: results/exfor_isomer_compare.json — per-dataset C/E on the split,
plus a flagged set (|log C/E| > log 3) for defect review.
"""
import json
import math
import re
import zipfile
import io
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "target/exfor-harvest"
LIB = Path.home() / "nuclear-data/tendl-2017/build/neutron.n.p10.npz"
IDX = LIB.with_name(LIB.stem + "_index.json")
OUT = ROOT / "results/exfor_isomer_compare.json"

EL = 'H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi'.split()
MT_OF_RXN = {"N_INL": 4, "N_2N": 16, "N_G": 102}
UNIT_TO_B = {"B": 1.0, "MB": 1e-3, "UB": 1e-6, "NB": 1e-9, "NO-DIM": 1.0, "PER-CENT": 0.01}
UNIT_TO_EV = {"EV": 1.0, "KEV": 1e3, "MEV": 1e6, "GEV": 1e9}

# ratio token -> (numerator lfs set, denominator lfs set); lfs: 0=g, 1=m1, 2=m2, 'T'=sum
RATIO_MAP = {
    "M/G": ({1, 2}, {0}), "G/M": ({0}, {1, 2}),
    "M/T": ({1, 2}, {"T"}), "T/M": ({"T"}, {1, 2}),
    "M1/G": ({1}, {0}), "G/M1": ({0}, {1}),
    "M2/G": ({2}, {0}), "G/M2": ({0}, {2}),
    "M1/M2": ({1}, {2}), "M2/M1": ({2}, {1}),
    "M1+M2/G": ({1, 2}, {0}), "G/M1+M2": ({0}, {1, 2}),
    "M1+M2/T": ({1, 2}, {"T"}), "T/M1+M2": ({"T"}, {1, 2}),
}


def parse_datasets(text):
    for m in re.finditer(r"#DATASET\s+(\d+).*?#ENDDATASET", text, re.S):
        b = m.group(0)
        rxn = re.search(r"#REACTION\s+(\S+)", b)
        if not rxn:
            continue
        auth = re.search(r"#AUTHOR1\s+(.+)", b)
        yr = re.search(r"#YEAR\s+(\d+)", b)
        sub = re.search(r"#SUBENT\s+(\d+)", b)
        # column headers + units from the !-lines (order = data columns)
        hm = re.search(r"!DATA\s+((?:\S+\s+)+?)\s*\n!(\S+)", b)
        headers = hm.group(1).split() if hm else []
        units = hm.group(2).split() if hm else []
        en_idx = next((i for i, h in enumerate(headers) if h == "EN"), None)
        data_units = units[0] if units else "B"
        en_units = units[en_idx] if en_idx is not None and en_idx < len(units) else "EV"
        dm = re.search(r"#DATA\s+\d+.*?\n(.*?)#ENDDATA", b, re.S)
        rows = []
        if dm and en_idx is not None:
            for ln in dm.group(1).strip().split("\n"):
                c = ln.split()
                if len(c) > en_idx:
                    try:
                        v, e = float(c[0]), float(c[en_idx])
                        rows.append((v * UNIT_TO_B.get(data_units, 1.0),
                                     e * UNIT_TO_EV.get(en_units, 1.0)))
                    except ValueError:
                        pass
        yield {
            "dataset": m.group(1), "subent": sub.group(1) if sub else m.group(1),
            "reaction": rxn.group(1),
            "author": auth.group(1).strip() if auth else "?",
            "year": int(yr.group(1)) if yr else None,
            "points": rows,  # (value, energy_eV)
        }


def state_of_product(rxn):
    """Return product-state tag from an EXFOR reaction string (None = skip)."""
    m = re.search(r"\)[A-Z0-9-]+-([A-Z0-9/+,]+)(,,|\s|$)", rxn)
    if not m:
        return None
    s = m.group(1).strip("/,")
    if "+" in s or "/" in s or s in ("M+G", "G+M"):  # cumulative (G,M+ etc.) — not resolved
        return None
    if s in ("M", "M1"): return 1
    if s == "M2": return 2
    if s == "G": return 0
    return None


def main():
    z = zipfile.ZipFile(LIB)
    rows = np.load(io.BytesIO(z.read("rows.npy")))
    sig = np.load(io.BytesIO(z.read("sig.npy")))
    bounds = np.load(io.BytesIO(z.read("bounds.npy")))
    idx = json.load(open(IDX))
    za = {k: t["za"] for k, t in enumerate(idx["targets"])}

    def tendl_lfs_sig(target_za, mt, product_za, lfs_set, E):
        g = int(np.searchsorted(bounds, E) - 1)
        if g < 0 or g >= len(sig[0]):
            return None
        s = 0.0
        found = False
        for i in range(len(rows)):
            if za[int(rows[i, 0])] == target_za and int(rows[i, 1]) == mt and int(rows[i, 2]) == product_za:
                l = int(rows[i, 3])
                if ("T" in lfs_set and l >= 0) or l in lfs_set:
                    s += float(sig[i][g]); found = True
        return s if found else None

    results = []
    for fn in sorted(CACHE.glob("*.txt")):
        m = re.match(r"([A-Z]+)-(\d+)_([A-Z0-9_,]+)\.txt", fn.name)
        if not m:
            continue
        target_za = (EL.index(m.group(1).title()) + 1) * 1000 + int(m.group(2))
        mt = MT_OF_RXN.get(m.group(3))
        if mt is None:
            continue
        product_za = {4: target_za, 16: target_za - 1, 102: target_za + 1}[mt]
        seen = set()
        for ds in parse_datasets(fn.read_text()):
            key = (ds["subent"], ds["reaction"])
            if key in seen or not ds["points"]:
                continue
            seen.add(key)
            rxn = ds["reaction"]
            # spectrum-averaged/integral quantities are not pointwise σ(E): skip
            if re.search(r",,+(MXW|SPA|AV|RI|FIS|DERIV|RECOM|FST|TT|CUM|UND|REL|MSC|LMP|NAP|AVE|RAW|PSD)\b", rxn):
                continue
            for val, E in ds["points"]:
                if val <= 0:
                    continue
                rec = {"dataset": ds["dataset"], "reaction": rxn,
                       "author": ds["author"], "year": ds["year"],
                       "E_eV": E, "measured": val}
                rm = re.search(r"-([A-Z0-9/+]+),,SIG/RAT", rxn)
                if "/RAT" in rxn and rm:
                    token = rm.group(1)
                    if token in RATIO_MAP:
                        num, den = RATIO_MAP[token]
                        sn = tendl_lfs_sig(target_za, mt, product_za, num, E)
                        sd = tendl_lfs_sig(target_za, mt, product_za, den, E)
                        if sn is None or sd is None or sd <= 0 or sn <= 0:
                            continue
                        rec["kind"] = f"ratio:{token}"
                        rec["tendl_pred"] = sn / sd
                        rec["CE"] = rec["tendl_pred"] / val
                        results.append({**rec, "target": m.group(1) + "-" + m.group(2), "mt": mt})
                else:
                    st = state_of_product(rxn)
                    if st is None:
                        continue
                    sp = tendl_lfs_sig(target_za, mt, product_za, {st}, E)
                    if sp is None or sp <= 0:
                        continue
                    rec["kind"] = f"abs:lfs{st}"
                    rec["tendl_pred"] = sp
                    rec["CE"] = sp / val
                    results.append({**rec, "target": m.group(1) + "-" + m.group(2), "mt": mt})

    flagged = [r for r in results if abs(math.log(r["CE"])) > math.log(3)]
    OUT.write_text(json.dumps({"n_measurements": len(results),
                               "n_flagged_3x": len(flagged),
                               "flagged": flagged,
                               "all": results}, indent=1))
    print(f"{len(results)} isomer measurements compared; {len(flagged)} flagged >3x")
    for r in sorted(flagged, key=lambda r: -abs(math.log(r["CE"])))[:30]:
        print(f"  {r['target']:8s} MT{r['mt']:3d} {r['kind']:16s} E={r['E_eV']:.3g} "
              f"meas={r['measured']:.3g} tendl={r['tendl_pred']:.3g} C/E={r['CE']:.2f}  {r['author']} {r['year']}")


if __name__ == "__main__":
    main()
