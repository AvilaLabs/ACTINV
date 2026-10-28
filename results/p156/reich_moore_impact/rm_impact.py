"""Old vs new 0 K resonance reconstruction for the evaluations with signed AJ in an LRF=3 range."""
import math, subprocess, sys, json
old_bin, new_bin, tendl = sys.argv[1], sys.argv[2], sys.argv[3]
cases = {"n-Al027.tendl": 845001.0, "n-Cl037.tendl": 1.2e6, "n-Mn055.tendl": 125000.0, "n-Si029.tendl": 1.300001e6,
         "n-U235.tendl": 2250.0}
out = {}
for f, eh in cases.items():
    n = 12000
    es = [1e-5 * (eh / 1e-5) ** (k / (n - 1)) for k in range(n)] + [0.0253]
    res = {}
    for lab, b in (("old", old_bin), ("new", new_bin)):
        txt = subprocess.run([b, "resonance-xs", f"{tendl}/{f}"] + [f"{e:.10e}" for e in es], capture_output=True,
                             text=True, check=True).stdout
        rows = [list(map(float, l.split()[1:])) for l in txt.splitlines() if l.startswith("X ")]
        res[lab] = rows
    o, nw = res["old"], res["new"]
    item = {}
    for idx, name in ((1, "elastic"), (2, "capture"), (3, "fission")):
        def ri(rows, lo):
            s = 0.0
            for a, b in zip(rows[:-2], rows[1:-1]):
                if a[0] < lo: continue
                s += 0.5 * (a[idx] + b[idx]) * math.log(b[0] / a[0])
            return s
        ro, rn = ri(o, 0.5), ri(nw, 0.5)
        if ro == 0 and rn == 0: continue
        th_o, th_n = o[-1][idx], nw[-1][idx]
        worst = max((abs(b[idx] / a[idx] - 1), a[0]) for a, b in zip(o[:-1], nw[:-1]) if a[idx] > 1e-3 * max(r[idx] for r in o))
        item[name] = {"thermal_old": th_o, "thermal_new": th_n, "thermal_rel": (th_n / th_o - 1) if th_o else None,
                      "res_integral_above_0.5eV_rel": (rn / ro - 1) if ro else None,
                      "worst_pointwise_rel": worst[0], "worst_at_eV": worst[1]}
    out[f] = item
print(json.dumps(out, indent=1))
