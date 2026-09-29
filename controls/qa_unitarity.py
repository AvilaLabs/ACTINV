#!/usr/bin/env python3
"""Unitarity screen for a groupwise activation library.

For a neutron-induced partial cross section, partial-wave unitarity bounds the reaction cross
section at energy E by  sum_{l=0..L} pi * lambdabar^2 * (2l+1) = pi * lambdabar^2 * (L+1)^2,
with lambdabar = hbar / p_cm, p_cm from the neutron–target reduced mass, and L the number of
partial waves that fit inside the nucleus (L = ceil(k R) + 1, R = 1.35 A^(1/3) fm — deliberately
generous). A group average cannot exceed the bound's maximum over the group, which sits at the
group's lower edge. Rows exceeding it are physically impossible values, not data choices.

    python3 controls/qa_unitarity.py LIBRARY.npz [--out results/qa_unitarity_<name>.json]

Streams sig.npy row by row (the library is ~1 GB); memory stays bounded.
"""
from __future__ import annotations

import ast
import hashlib
import json
import math
import struct
import sys
import zipfile
from pathlib import Path

HBARC_EV_CM = 1.973269804e-5  # hbar*c in eV*cm
MN_EV = 939.56542052e6  # neutron rest energy, eV
BARN = 1e-24


def npy_header(f):
    assert f.read(6) == b"\x93NUMPY"
    major = f.read(2)[0]
    (hlen,) = struct.unpack("<H" if major == 1 else "<I", f.read(2 if major == 1 else 4))
    return ast.literal_eval(f.read(hlen).decode("latin1"))


def bound_barns(e_lab_ev: float, awr: float) -> float:
    """Generous partial-wave unitarity ceiling at lab energy e_lab_ev for target mass ratio awr."""
    mu = MN_EV * awr / (awr + 1.0)
    e_cm = e_lab_ev * awr / (awr + 1.0)
    k = math.sqrt(2.0 * mu * e_cm) / HBARC_EV_CM  # 1/cm
    lambdabar = 1.0 / k
    radius = 1.35e-13 * (awr ** (1.0 / 3.0))
    lmax = math.ceil(k * radius) + 1
    return math.pi * lambdabar ** 2 * (lmax + 1) ** 2 / BARN


def main(argv):
    lib = Path(argv[0])
    out = Path(argv[argv.index("--out") + 1]) if "--out" in argv else Path(
        "results") / f"qa_unitarity_{lib.stem}.json"
    index = json.loads(lib.with_name(lib.stem + "_index.json").read_text())
    targets = index["targets"]
    z = zipfile.ZipFile(lib)
    with z.open("rows.npy") as f:
        h = npy_header(f)
        n_rows, n_cols = h["shape"]
        import array
        rows = array.array("q")
        rows.frombytes(f.read(n_rows * n_cols * 8))
    with z.open("bounds.npy") as f:
        h = npy_header(f)
        import array as _a
        bounds = _a.array("d")
        bounds.frombytes(f.read(h["shape"][0] * 8))
    groups = len(bounds) - 1
    import numpy as np
    e_lo = np.asarray(bounds[:-1], dtype=float)
    awr_by_target = np.array([t_["awr"] for t_ in targets], dtype=float)
    rows_np = np.frombuffer(rows.tobytes(), dtype="<i8").reshape(n_rows, n_cols)
    hits = []
    chunk = 512
    with z.open("sig.npy") as f:
        h = npy_header(f)
        assert tuple(h["shape"]) == (n_rows, groups) and h["descr"] == "<f8"
        for i0 in range(0, n_rows, chunk):
            n = min(chunk, n_rows - i0)
            sig = np.frombuffer(f.read(n * groups * 8), dtype="<f8").reshape(n, groups)
            awr = awr_by_target[rows_np[i0:i0 + n, 0]][:, None]
            mu = MN_EV * awr / (awr + 1.0)
            e_cm = e_lo[None, :] * awr / (awr + 1.0)
            k = np.sqrt(2.0 * mu * e_cm) / HBARC_EV_CM
            lmax = np.ceil(k * 1.35e-13 * awr ** (1.0 / 3.0)) + 1.0
            bound = math.pi / k ** 2 * (lmax + 1.0) ** 2 / BARN
            ratio = np.where(sig > 0.0, sig / bound, 0.0)
            worst_g = ratio.argmax(axis=1)
            worst = ratio[np.arange(n), worst_g]
            for j in np.nonzero(worst > 1.0)[0]:
                i, g = i0 + int(j), int(worst_g[j])
                t = int(rows_np[i, 0])
                hits.append({"row": i, "target_file": targets[t]["file"], "za": targets[t]["za"],
                             "liso": targets[t]["liso"], "mt": int(rows_np[i, 1]),
                             "product_za": int(rows_np[i, 2]), "max_ratio_to_bound": float(worst[j]),
                             "group": g, "group_low_eV": float(e_lo[g]), "sigma_barns": float(sig[j, g]),
                             "bound_barns": float(bound[j, g])})
    hits.sort(key=lambda r: -r["max_ratio_to_bound"])
    files = sorted({h_["target_file"] for h_ in hits})
    doc = {"schema": "actinv-qa-unitarity-1", "library": str(lib),
           "library_sha256": hashlib.sha256(lib.read_bytes()).hexdigest(),
           "rows_scanned": n_rows, "rows_violating": len(hits), "target_files_violating": len(files),
           "bound": "pi*lambdabar^2*(L+1)^2, L=ceil(kR)+1, R=1.35 A^(1/3) fm, evaluated at the group lower edge",
           "violations": hits, "files": files}
    out.write_text(json.dumps(doc, indent=1))
    print(f"{n_rows} rows scanned; {len(hits)} rows in {len(files)} target files exceed the unitarity bound")
    for r in hits[:12]:
        print(f"  {r['target_file']:22s} MT{r['mt']:<4d} ratio {r['max_ratio_to_bound']:.3g} at {r['group_low_eV']:.3g} eV "
                    f"({r['sigma_barns']:.3g} b vs {r['bound_barns']:.3g} b)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
