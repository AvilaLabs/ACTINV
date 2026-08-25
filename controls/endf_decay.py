#!/usr/bin/env python3
"""ACT-P0 G-B1: own ENDF-6 decay parser (MF=8/MT=457) for a multi-material file, plus the control
against openmc.data.Decay (same file). Writes results/gb1_decay.json."""
import re, sys, os, json, math, random, time
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
_F = re.compile(r"^\s*([+-]?\d*\.?\d*)([+-]\d+)\s*$")
def endf_float(s):
    s = s.strip()
    if not s: return 0.0
    try: return float(s)
    except ValueError:
        m = _F.match(s)
        if m: return float(m.group(1) + "e" + m.group(2))
        raise
def fields(line): return [line[i*11:(i+1)*11] for i in range(6)]
def read_list(lines, i):
    f = fields(lines[i]); c1, c2 = endf_float(f[0]), endf_float(f[1]); l1, l2, n1, n2 = (int(x) for x in f[2:6]); i += 1
    vals = []
    while len(vals) < n1:
        vals += [endf_float(x) for x in fields(lines[i])[:min(6, n1 - len(vals))]]; i += 1
    return (c1, c2, l1, l2, n1, n2, vals), i
def parse_decay_file(path):
    """Return dict MAT -> record; only MF=8/MT=457 sections are parsed (spectra skipped)."""
    out = {}; cur = None; buf = []
    with open(path, errors="replace") as fh:
        for line in fh:
            if len(line) < 75: continue
            try: mat, mf, mt = int(line[66:70]), int(line[70:72]), int(line[72:75])
            except ValueError: continue
            key = (mat, mf, mt)
            if mf == 8 and mt == 457:
                if cur != key: cur, buf = key, []
                buf.append(line.rstrip("\n"))
            elif cur is not None and buf:
                out[cur[0]] = parse_section(cur[0], buf); cur, buf = None, []
    if cur is not None and buf: out[cur[0]] = parse_section(cur[0], buf)
    return out
def parse_section(mat, lines):
    f = fields(lines[0]); za, awr = endf_float(f[0]), endf_float(f[1]); lis, liso, nst, nsp = (int(x) for x in f[2:6])
    (t12, dt12, _, _, n2c, _, e), i = read_list(lines, 1)
    (spi, par, _, _, n6, ndk, dk), i = read_list(lines, i)
    modes = [{"rtyp": dk[6*k], "rfs": dk[6*k+1], "q": dk[6*k+2], "dq": dk[6*k+3], "br": dk[6*k+4], "dbr": dk[6*k+5]} for k in range(ndk)]
    return {"mat": mat, "za": za, "awr": awr, "lis": lis, "liso": liso, "nst": nst, "nsp": nsp, "half_life": t12, "d_half_life": dt12,
            "energies": e[:n2c], "spin": spi, "parity": par, "ndk": ndk, "modes": modes}
if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~/nuclear-data/endfb-viii.0-decay/bulk/endf-b-viii-0_decay.dat")
    t0 = time.time(); recs = parse_decay_file(path); t_own = time.time() - t0
    nrad = sum(1 for r in recs.values() if r["nst"] == 0)
    print(f"own parser: {len(recs)} materials ({nrad} radioactive) in {t_own:.1f}s", file=sys.stderr)
    # ---- control vs openmc.data (same file)
    import openmc.data
    from openmc.data.endf import get_evaluations
    t0 = time.time(); evs = {ev.material: ev for ev in get_evaluations(path)}; t_omc = time.time() - t0
    print(f"openmc get_evaluations: {len(evs)} in {t_omc:.1f}s", file=sys.stderr)
    rng = random.Random(20260825); mats = sorted(recs); sample = rng.sample(mats, 200)
    tol = 1e-12; mism = []; checked = 0
    def rel(a, b): return abs(a - b) / max(abs(a), abs(b), 1e-300)
    def nom(x): return float(getattr(x, "n", x))
    for mat in sample:
        r = recs[mat]; d = openmc.data.Decay(evs[mat]); checked += 1
        hl = nom(d.half_life) if d.half_life is not None else 0.0
        if rel(hl, r["half_life"]) > tol: mism.append((mat, "half_life", hl, r["half_life"]))
        brs_o = sorted(nom(m.branching_ratio) for m in d.modes); brs_p = sorted(m["br"] for m in r["modes"])
        if len(brs_o) != len(brs_p) or any(rel(a, b) > tol for a, b in zip(brs_o, brs_p)): mism.append((mat, "branching", brs_o, brs_p))
        qs_o = sorted(nom(m.energy) for m in d.modes); qs_p = sorted(m["q"] for m in r["modes"])
        if any(rel(a, b) > tol for a, b in zip(qs_o, qs_p)): mism.append((mat, "Q", qs_o, qs_p))
        if r["nst"] == 0:  # Amendment B §4: stable nuclides carry zero energies; openmc returns {}
            en_o = sorted(nom(v) for v in d.average_energies.values()); en_p = sorted(r["energies"][0::2])
            if len(en_o) != len(en_p) or any(rel(a, b) > tol for a, b in zip(en_o, en_p)): mism.append((mat, "energies", en_o, en_p))
    res = {"file": path, "n_materials": len(recs), "n_radioactive": nrad, "t_own_s": t_own, "t_openmc_s": t_omc, "sample_seed": 20260825, "n_checked": checked,
           "tolerance_rel": tol, "n_mismatch": len(mism), "mismatches": [list(map(str, m)) for m in mism[:50]],
           "example": recs[sample[0]], "openmc_average_energy_keys": list(openmc.data.Decay(evs[sample[0]]).average_energies.keys())}
    json.dump(res, open(os.path.join(ROOT, "results", "gb1_decay.json"), "w"), indent=1)
    print(json.dumps({k: v for k, v in res.items() if k not in ("example", "mismatches")}, indent=1)); print("mismatches (first 10):", mism[:10])
