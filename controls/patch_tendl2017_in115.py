#!/usr/bin/env python3
"""Reproducible TENDL-2017 In-115 capture-bump repair.

Defect: In-115 (ZA 49115) MF=3/MT=102 carries a spurious high-energy capture
bump rising to ~5.4 b at 14–18 MeV — physically impossible (capture at >10 MeV
is mb-scale and declining) and confirmed corrected to mb-scale in TENDL-2025.
The defect propagates to the three MF=10 emitted-state rows (lfs 0,1,2) whose
sum equals the aggregate row.

Repair: freeze each affected row at its pre-bump value (the last group before
E > 8.71 MeV) for all groups above 8.71 MeV. Conservative — keeps a flat mb-scale
tail rather than inventing a decline slope. Verified: In_2000exp_5min geoCE
29.19 -> 1.95 on the scored 132-experiment pass (results/cb3_fns_tendl2017_infix.json).

Usage: python3 controls/patch_tendl2017_in115.py [IN_NPZ] [OUT_NPZ]
Defaults: stock build -> <same dir>/neutron.n.p10.infix.npz
Writes OUT with _index.json sidecar copied with corrected sha + a manifest.
"""
import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
STOCK = Path.home() / "nuclear-data/tendl-2017/build/neutron.n.p10.npz"
E_CUT = 8.7e6  # bump onset; freeze all groups whose bin starts at/above this energy
TARGET_ZA = 49115
MT = 102


def sha256(path):
    h = hashlib.sha256()
    h.update(Path(path).read_bytes())
    return h.hexdigest()


def main():
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else STOCK
    dst = Path(sys.argv[2]) if len(sys.argv) > 2 else src.with_name(src.stem + ".infix.npz")
    zin = zipfile.ZipFile(src)
    arrays = {n: np.load(io.BytesIO(zin.read(n))) for n in zin.namelist()}
    idx_path = src.with_name(src.stem + "_index.json")
    idx = json.loads(idx_path.read_text())
    za_by_tidx = {k: t["za"] for k, t in enumerate(idx["targets"])}
    rows, sig, bounds = arrays["rows.npy"], arrays["sig.npy"], arrays["bounds.npy"]
    g0 = int(np.nonzero(bounds[:-1] >= E_CUT)[0][0])  # first group whose bin starts at/above cut
    hi = bounds[:-1] >= E_CUT
    patched = []
    for i in range(len(rows)):
        if za_by_tidx[int(rows[i, 0])] == TARGET_ZA and int(rows[i, 1]) == MT:
            old_max = float(sig[i][hi].max())
            if old_max < 0.5:  # second In-115 row-set (rows 29123-26) is already clean
                continue
            freeze = sig[i, g0 - 1]  # last group below the cut
            sig[i, g0:] = freeze
            patched.append({
                "row": int(i), "mt": MT, "product": int(rows[i, 2]), "lfs": int(rows[i, 3]),
                "first_patched_group": g0, "E_onset_eV": float(bounds[g0]),
                "frozen_value_b": float(freeze), "old_max_b": old_max,
            })
    assert len(patched) == 4, f"expected 4 defective rows, patched {len(patched)}"
    agg = [p for p in patched if p["product"] == -1][0]
    states = sum(p["frozen_value_b"] for p in patched if p["product"] != -1)
    assert abs(agg["frozen_value_b"] - states) < 1e-12, "aggregate != state sum"
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for n, a in arrays.items():
            buf = io.BytesIO()
            np.save(buf, a)
            zout.writestr(n, buf.getvalue())
    idx["source_npz_sha256"] = sha256(src)
    idx["sha256"] = sha256(dst)
    idx["patch"] = {
        "id": "tendl-2017-in115-capture-bump",
        "description": "In-115 MT=102 spurious high-energy capture bump frozen at pre-bump value",
        "evidence": "capture >5.4b at 14-18MeV exceeds physical bound (mb-scale declining); "
                    "TENDL-2025 eval is mb-scale; verified In_2000exp_5min geoCE 29.19->1.95",
        "rows": patched,
    }
    out_idx = dst.with_name(dst.stem + "_index.json")
    out_idx.write_text(json.dumps(idx, indent=1))
    print(json.dumps({"out": str(dst), "index": str(out_idx),
                      "sha256": idx["sha256"], "rows_patched": len(patched)}, indent=1))


if __name__ == "__main__":
    main()
