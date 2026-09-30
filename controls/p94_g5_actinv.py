#!/usr/bin/env python3
"""P94 G5 (ACTINV side only): builds the 8 raw TENDL-2017 gamma evaluations (Fe-56, Cu-63,
Ni-58, Nb-93, W-186, Ta-181, Al-27, Pb-208) with the candidate binary, then computes one-group
residual-production values under the protocol's three fixed CCFE-162 spectra, writing
target/p94/g5_actinv.json.

This script does NOT touch the FISPACT-II side (the pinned tal2017-g/gxs-162 object) or compute
a pass/fail verdict -- that comparison, and G5's pass/fail, is the lead's own step per the P94
implementation instructions. This only prepares and runs the ACTINV half.

Usage:
    python3 controls/p94_g5_actinv.py

Spectra (protocols/ACTINV-P94_PROTOCOL.md, G5), as per-CCFE-162-group weights w_g such that the
one-group value is sum(w_g * sigma_g) / sum(w_g):
  - gdr_flat_8_30_MeV: flat in lethargy on 8-30 MeV. w_g = ln(min(Ehi,30MeV)/max(Elo,8MeV)) for
    the overlap of group g with [8, 30] MeV, else 0.
  - brems_20_MeV: lethargy width x (1 - E/20 MeV) on 8-20 MeV, evaluated at the group geometric
    centre. w_g = ln(Ehi_g/Elo_g) * (1 - Ec_g/20MeV) for groups whose geometric centre Ec_g =
    sqrt(Elo_g*Ehi_g) falls in [8, 20] MeV, else 0.
  - hard_60_MeV: log-normal in energy, centred at 60 MeV with sigma(lnE) 0.30, on 30-200 MeV.
    w_g = integral over the group (clipped to [30,200] MeV) of the lognormal PDF in E with
    mu=ln(60 MeV), sigma=0.30.
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import numpy.lib.format as npfmt
from scipy.integrate import quad

ROOT = Path(__file__).resolve().parents[1]
DECAY = Path.home() / "Documents" / "actinv" / "actinv-data" / "v1.1.0" / "decay"
WORK = ROOT / "target" / os.environ.get("ACTINV_GAMMA_PROTOCOL", "P94").lower()
CAND = ROOT / "target" / "release" / "actinv"
SOURCE = Path.home() / "nuclear-data" / "tendl-2017" / "files" / "g"
GROUPS_JSON = ROOT / "crates" / "actinv-data" / "data" / "fispact_162_groups.json"
EXPECTED_FILES = {"Fe056", "Cu063", "Ni058", "Nb093", "W186", "Ta181", "Al027", "Pb208"}
MEV = 1.0e6
ENV = {"RAYON_NUM_THREADS": "1", "PATH": "/usr/bin:/bin", "HOME": str(Path.home())}


def sha(p: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with p.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def read_npz_library(npz_path: Path):
    with zipfile.ZipFile(npz_path) as zf:
        with zf.open("rows.npy") as f:
            rows = npfmt.read_array(f)
        with zf.open("sig.npy") as f:
            sig = npfmt.read_array(f)
        with zf.open("bounds.npy") as f:
            bounds = npfmt.read_array(f)
    return rows, sig, bounds


def weights_gdr(boundaries):
    lo_w, hi_w = 8 * MEV, 30 * MEV
    out = []
    for lo, hi in zip(boundaries[:-1], boundaries[1:]):
        a, b = max(lo, lo_w), min(hi, hi_w)
        out.append(math.log(b / a) if b > a else 0.0)
    return out


def weights_brems(boundaries):
    lo_w, hi_w = 8 * MEV, 20 * MEV
    out = []
    for lo, hi in zip(boundaries[:-1], boundaries[1:]):
        ec = math.sqrt(lo * hi)
        if lo_w <= ec <= hi_w:
            out.append(math.log(hi / lo) * (1.0 - ec / hi_w))
        else:
            out.append(0.0)
    return out


def weights_hard(boundaries):
    lo_w, hi_w = 30 * MEV, 200 * MEV
    mu = math.log(60 * MEV)
    sigma = 0.30

    def pdf(e):
        return math.exp(-((math.log(e) - mu) ** 2) / (2 * sigma * sigma)) / (e * sigma * math.sqrt(2 * math.pi))

    out = []
    for lo, hi in zip(boundaries[:-1], boundaries[1:]):
        a, b = max(lo, lo_w), min(hi, hi_w)
        if b > a:
            val, _ = quad(pdf, a, b, epsabs=0.0, epsrel=1e-10, limit=100)
            out.append(val)
        else:
            out.append(0.0)
    return out


SPECTRA = {
    "gdr_flat_8_30_MeV": weights_gdr,
    "brems_20_MeV": weights_brems,
    "hard_60_MeV": weights_hard,
}


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    if not CAND.exists():
        sys.exit(f"candidate binary missing: {CAND}")
    if not SOURCE.exists():
        sys.exit(f"TENDL-2017 gamma source not found: {SOURCE}")
    files = sorted(SOURCE.glob("*"))
    found = {f.stem.split("-")[-1] if "-" in f.stem else f.stem for f in files}
    report = {"source_dir": str(SOURCE), "n_files_on_disk": len(files),
              "files": [f.name for f in files]}

    out_npz = WORK / "g5_gamma_2017.npz"
    idx_path = WORK / "g5_gamma_2017_index.json"
    out_npz.unlink(missing_ok=True)
    idx_path.unlink(missing_ok=True)
    p = subprocess.run(
        [str(CAND), "build-library", str(SOURCE), str(out_npz),
         "--format", "tendl", "--projectile", "gamma", "--groups", "fispact-162",
         "--temperature-K", "0",
         # Same decay-data isomer mapping as the G3 build (controls/check_p94.py).
         "--decay", str(DECAY / "endf-b-viii-0_decay.dat"),
         "--decay-fallback", str(DECAY / "jeff-3-3_decay.dat"),
         # The default profile (none) fails closed on g-Cu063 MT106/MF=10 ZAP=27060 group 121
         # (state sum 2.13e-14 b over MF=3 total 1.56e-14 b; run 1 kept as
         # g5_actinv_run1_default_profile_failclosed.json). The shipped TENDL ingestion profile
         # reconciles such sums at collapse time and records every group as state_sum_normalized
         # in the index ledger; it is applied to all 8 files alike.
         "--profile", "tendl"],
        capture_output=True, text=True, env=ENV,
    )
    report["build"] = {"returncode": p.returncode, "stdout_tail": p.stdout[-800:],
                        "stderr_tail": p.stderr[-800:]}
    if p.returncode != 0 or not out_npz.exists() or not idx_path.exists():
        report["pass"] = False
        (WORK / "g5_actinv.json").write_text(json.dumps(report, indent=1, sort_keys=True))
        print(json.dumps({"pass": False, "build_returncode": p.returncode}, indent=1))
        return 1

    idx = json.loads(idx_path.read_text())
    target_za_by_index = [t["za"] for t in idx["targets"]]
    rows, sig, bounds = read_npz_library(out_npz)
    bounds = list(bounds)

    boundaries = json.loads(GROUPS_JSON.read_text())["boundaries_eV"]
    if boundaries[0] > boundaries[-1]:
        boundaries = list(reversed(boundaries))
    if bounds != boundaries:
        report["bounds_mismatch"] = True

    weights = {name: fn(bounds) for name, fn in SPECTRA.items()}

    nuclide_rows = {}
    for i in range(rows.shape[0]):
        ti, mt, zap, lfs, lmf = (int(x) for x in rows[i])
        za = target_za_by_index[ti]
        nuclide_rows.setdefault(za, []).append((mt, zap, lfs, sig[i]))

    result = {}
    for za, rowlist in sorted(nuclide_rows.items()):
        per_spectrum = {}
        for spec_name, w in weights.items():
            denom = sum(w)
            entries = []
            total_production = 0.0
            row_vals = []
            for mt, zap, lfs, vals in rowlist:
                num = sum(wi * vi for wi, vi in zip(w, vals))
                one_group = (num / denom) if denom > 0 else 0.0
                row_vals.append((mt, zap, lfs, one_group))
                total_production += one_group
            for mt, zap, lfs, one_group in row_vals:
                share = (one_group / total_production) if total_production > 0 else 0.0
                entries.append({"mt": mt, "zap": zap, "lfs": lfs, "one_group_barns": one_group,
                                 "share_of_summed_residual_production": share,
                                 "above_1e-3_threshold": share >= 1e-3})
            per_spectrum[spec_name] = {"spectrum_weight_sum": denom,
                                        "total_summed_residual_production_barns": total_production,
                                        "rows": entries}
        result[str(za)] = per_spectrum

    report["pass"] = True
    report["candidate_sha256"] = sha(CAND)
    report["targets_built"] = target_za_by_index
    report["n_rows"] = int(rows.shape[0])
    report["spectra"] = result
    (WORK / "g5_actinv.json").write_text(json.dumps(report, indent=1, sort_keys=True, default=str))
    print(json.dumps({"pass": True, "targets_built": target_za_by_index, "n_rows": int(rows.shape[0])}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
