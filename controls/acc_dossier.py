"""Accuracy-campaign dossier: per failing FNS experiment, assemble the semantic
evidence an analyst needs — dominant heat nuclides per cooling step (ACTINV and
frozen FISPACT), production channels in the library, decay data used, missing
decay data, cross-version collapse ratios, and unphysicality flags.

Usage: python3 controls/acc_dossier.py Material experiment [Material experiment ...]
Writes results/acc_dossier/<Mat>_<exp>.json and a .md summary.
"""
import os, sys, json, math, subprocess, hashlib, zipfile, io
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "controls"))
sys.path.insert(0, str(ROOT / "controls" / "harness"))
from harness import fispact_io as fio
import decayheat as dh
import decay_sources as ds

FNS = Path(os.path.expanduser("~/nuclear-data/conderc-fns/fns"))
LIB17 = Path(os.path.expanduser(
    os.environ.get("ACTINV_LIBRARY", "~/nuclear-data/tendl-2017/build/neutron.n.p10.npz")))
LIB25 = ROOT / "target/p25c-release/tendl-2025-patched-neutron-709g.npz"
ACTINV = ROOT / "target/debug/actinv"
OUT = ROOT / "target/preflight-tmp/dossier"
DOSS = ROOT / "results/acc_dossier"

ELS = ("H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn "
       "Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce "
       "Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn "
       "Fr Ra Ac Th Pa U Np Pu Am Cm").split()
ZOF = {e: i + 1 for i, e in enumerate(ELS)}
EOF = None


def name_to_key(name):
    import re
    m = re.match(r"([A-Z][a-z]?)(\d+)(?:m(\d+))?$", name)
    if not m:
        return None
    return (ZOF[m.group(1)] * 1000 + int(m.group(2)), int(m.group(3) or 0))


def key_to_name(key):
    za, liso = key
    base = f"{ELS[za // 1000 - 1]}{za % 1000}"
    return base if liso == 0 else f"{base}m{liso}"


def load_npz(path):
    z = zipfile.ZipFile(path)
    rows = np.load(io.BytesIO(z.read("rows.npy")))
    sig = np.load(io.BytesIO(z.read("sig.npy")))
    bounds = np.load(io.BytesIO(z.read("bounds.npy")))
    idx = json.load(open(str(path).replace(".npz", "_index.json")))
    return rows, sig, bounds, idx["targets"]


def build_spec(material, experiment):
    directory = FNS / material
    rec = fio.read_i(directory / f"TENDL-2017_{experiment}.i")
    vals = []
    for line in (directory / f"{experiment}_fluxes").read_text().splitlines():
        try:
            vals += [float(x) for x in line.split()]
        except ValueError:
            break
    flux = vals[:709]
    cooling = rec["cooling_cum_s"]
    schedule = [{"dt": f"{rec['t_irr_s']:.17e} s", "flux": 1.0},
                {"dt": f"{cooling[0]:.17e} s", "flux": 0.0}]
    schedule += [{"dt": f"{cooling[i] - cooling[i - 1]:.17e} s", "flux": 0.0}
                 for i in range(1, len(cooling))]
    lib = LIB17
    sha = hashlib.sha256(lib.read_bytes()).hexdigest()
    spec = {
        "spec": "actinv-spec-1", "title": f"dossier {material} {experiment}",
        "projectile": "neutron",
        "library": {"path": str(lib), "sha256": sha},
        "decay": {"primary": os.path.expanduser("~/nuclear-data/endfb-viii.0-decay/bulk/endf-b-viii-0_decay.dat"),
                  "fallback": os.path.expanduser("~/nuclear-data/jeff-3.3-decay/bulk/jeff-3-3_decay.dat")},
        "material": {"mass_g": float(rec["mass_kg"] * 1000), "basis": "wt_percent",
                     "composition": rec["elements"]},
        "spectrum": {"structure": "fispact-709", "flux_per_group": flux,
                     "total": float(rec["flux_total"]), "descending": True},
        "schedule": schedule,
        "options": {"mode": "auto", "prune": "rate", "bmin_atoms_per_g": 1e-8,
                    "temperature_K": 293.6,
                    "outputs": ["inventory", "activity", "heat", "ledger", "certificate"]},
        "fission_yields": {"files": [], "energy": "spectrum_average"},
    }
    return spec, rec, np.array(flux)


def decay_parents(target_key, recs):
    """Nuclides whose decay modes feed target_key (za,liso)."""
    tza, tliso = target_key
    parents = []
    for r in recs.values():
        pza, pliso = int(round(r["za"])), r["liso"]
        if (pza, pliso) == target_key:
            continue
        for md in r.get("modes", []):
            rtyp = md["rtyp"]
            # daughter ZA by decay type (ENDF RTYP)
            dza = None
            if rtyp == 1:   dza = pza + 1000          # beta- : Z+1, A same
            elif rtyp == 2: dza = pza - 1000          # beta+/EC: Z-1, A same
            elif rtyp == 3: dza = pza                 # IT: same ZA
            elif rtyp == 4: dza = pza - 2004          # alpha: Z-2, A-4
            elif rtyp == 5: dza = pza - 1             # n emission: A-1
            elif rtyp == 7: dza = pza - 1001          # p emission: Z-1, A-1
            elif rtyp == 9: dza = pza + 998           # d emission: Z-1, A-2
            if dza is None:
                continue
            dliso = int(md.get("rfs", 0))
            if dza == tza and dliso == tliso:
                parents.append({"nuclide": key_to_name((pza, pliso)), "rtyp": rtyp, "br": md["br"]})
    return parents


def dossier(material, experiment):
    directory = FNS / material
    spec, rec, flux_desc = build_spec(material, experiment)
    spath = OUT / f"spec_{material}_{experiment}.json"
    opath = OUT / f"out_{material}_{experiment}.json"
    spath.write_text(json.dumps(spec))
    env = dict(os.environ, TMPDIR=str(ROOT / "target/preflight-tmp"))
    r = subprocess.run(
        ["systemd-run", "--user", "--scope", "-p", "MemoryMax=6G", "-p", "MemorySwapMax=0",
         "-p", "TasksMax=128", "-p", "CPUQuota=200%", "--", "env",
         f"TMPDIR={ROOT / 'target/preflight-tmp'}", str(ACTINV), "run", str(spath), str(opath)],
        capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        return {"error": r.stderr[-800:] or r.stdout[-800:]}
    out = json.load(open(opath))

    meas = fio.read_exp(directory / f"{experiment}.exp")
    t_irr = rec["t_irr_s"]
    cooling_cum = rec["cooling_cum_s"]
    meas_heat = meas["heat_uW_g"]

    # per-nuclide heat at each cooling step
    D = dh.decay_table()
    steps = out["steps"][1:]
    per_step = []
    recs, prov, _ = ds.merged_records()
    for i, st in enumerate(steps):
        inv = {(x["Z"] * 1000 + x["A"], x["LISO"]): x["atoms_per_g"] for x in st["inventory"]}
        tot, per, missing = dh.heat_W_per_g(inv)
        top = sorted(per.items(), key=lambda kv: -kv[1])[:6]
        per_step.append({
            "cooling_s": cooling_cum[i] if i < len(cooling_cum) else None,
            "total_heat_uW_g": tot * 1e6,
            "measured_uW_g": float(meas_heat[i]) if i < len(meas_heat) else None,
            "top": [(key_to_name(k), v * 1e6) for k, v in top],
            "top_keys": [k for k, _ in top],
            "missing_decay_nuclides": [(key_to_name((m["za"], m["liso"])), m["atoms_per_g"]) for m in missing],
        })

    # union of dominant nuclides across steps
    dominant = {}
    for ps in per_step:
        for k, h in zip(ps["top_keys"], [h for _, h in ps["top"]]):
            dominant.setdefault(k, 0.0)
            dominant[k] += h

    # production rows for each dominant nuclide + decay info
    r17, s17, b17, t17 = load_npz(LIB17)
    phi = flux_desc[::-1]
    P = phi.sum()
    nuclide_info = {}
    hi_mask = b17[:-1] >= 1e7
    for key in dominant:
        za, liso = key
        prod = []
        for i, (tt, mt, zap, lfs, lmf) in enumerate(r17):
            if int(zap) == za and int(lfs) == liso:
                tza = int(t17[tt]["za"])
                collapsed = float((s17[i] * phi).sum() / P)
                hi_max = float(s17[i][hi_mask].max())
                flags = []
                if int(mt) == 102 and hi_max > 0.3:
                    flags.append("capture_bump_gt_0.3b_above_10MeV")
                prod.append({"target_za": tza, "target": key_to_name((tza, 0)),
                             "mt": int(mt), "collapsed_b": collapsed,
                             "max_sigma_gt10MeV_b": hi_max, "flags": flags})
        d = D.get(key)
        parents = decay_parents(key, recs)
        nuclide_info[key_to_name(key)] = {
            "za": za, "liso": liso,
            "production_rows": prod,
            "decay_parents": parents,
            "decay": ({"half_life_s": (LN2 / d["lambda"] if d["lambda"] > 0 else None),
                       "E_light_eV": d["E_light"], "E_EM_eV": d["E_EM"],
                       "E_heavy_eV": d["E_heavy"], "source": d["source"], "stable": d["nst"] == 1}
                      if d else None),
            "decay_record_present": key in D,
        }

    # CE trajectory
    ce = []
    for ps in per_step:
        if ps["measured_uW_g"] and ps["measured_uW_g"] > 0:
            ce.append(ps["total_heat_uW_g"] / ps["measured_uW_g"])
        else:
            ce.append(None)
    xs = [math.log(ps["cooling_s"]) for ps in per_step if ps["cooling_s"]]
    ys = [math.log(c) for c in ce if c]
    n = min(len(xs), len(ys))
    slope = None
    if n >= 3:
        sx, sy = sum(xs[:n]), sum(ys[:n])
        sxx = sum(x * x for x in xs[:n]); sxy = sum(x * y for x, y in zip(xs[:n], ys[:n]))
        slope = (n * sxy - sx * sy) / (n * sxx - sx * sx)

    # FISPACT per-nuclide heat (frozen reference)
    nuc_path = directory / f"TENDL-2017_{experiment}.nuclides"
    fispact_nuc = None
    if nuc_path.exists():
        nr = fio.read_nuclides(nuc_path)
        fispact_nuc = {"t_y": nr["t_y"].tolist(), "names": list(nr["nuclides"].keys()),
                       "total_kW_kg": nr["total_kW_kg"].tolist()}

    return {"material": material, "experiment": experiment, "t_irr_s": t_irr,
            "ce_per_point": ce, "ce_slope_dlnCE_dlnt": slope,
            "per_step": [{k: v for k, v in ps.items() if k != "top_keys"} for ps in per_step],
            "dominant_nuclides": nuclide_info,
            "fispact_nuclides": fispact_nuc,
            "ledger_flags": {k: out["ledger"].get(k) for k in
                             ("products_no_evaluated_decay_data", "targets_absent_from_decay_library",
                              "composition_isotopes_absent_from_decay_library", "decay_daughters_missing")}}


LN2 = math.log(2.0)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    DOSS.mkdir(parents=True, exist_ok=True)
    args = sys.argv[1:]
    for i in range(0, len(args), 2):
        mat, exp = args[i], args[i + 1]
        print(f"=== {mat} {exp} ===", flush=True)
        d = dossier(mat, exp)
        jpath = DOSS / f"{mat}_{exp}.json"
        jpath.write_text(json.dumps(d, indent=1, default=str))
        if "error" in d:
            print("  FAILED:", d["error"][:200])
            continue
        for ps in d["per_step"][:4]:
            print(f"  t={ps['cooling_s']:>9.1f}s calc={ps['total_heat_uW_g']:.3g} meas={ps['measured_uW_g']:.3g} top={[f'{n}:{v:.2g}' for n, v in ps['top'][:3]]}")
        last = d["per_step"][-1]
        print(f"  t={last['cooling_s']:>9.1f}s calc={last['total_heat_uW_g']:.3g} meas={last['measured_uW_g']:.3g} top={[f'{n}:{v:.2g}' for n, v in last['top'][:3]]}")
        for name, ni in d["dominant_nuclides"].items():
            fl = [f for pr in ni["production_rows"] for f in pr["flags"]]
            if fl or not ni["decay_record_present"]:
                print(f"  ! {name}: flags={fl} decay_record={ni['decay_record_present']}")
        print(f"  wrote {jpath}")


if __name__ == "__main__":
    main()
