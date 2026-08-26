#!/usr/bin/env python3
"""Generate data/mt_products.json — the residual (dZ, dA) for each ENDF reaction MT, parsed from openmc.data's reaction
names. Run this when the table needs regenerating; the build reads the vendored file and never imports openmc."""
import os, re, json
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
PART = {"n": (0, -1), "p": (-1, -1), "d": (-1, -2), "t": (-1, -3), "3He": (-2, -3), "a": (-2, -4), "gamma": (0, 0)}
import openmc.data
tab = {}
for mt, name in openmc.data.REACTION_NAME.items():
    m = re.match(r"\(n,(.+)\)$", name)
    if not m: continue
    s = m.group(1)
    if "'" in s or s in ("elastic", "total", "level", "continuum", "anything", "disappear", "absorption", "heating", "damage-energy", "heating-local"): continue
    if not re.fullmatch(r"(\d*(3He|n|p|d|t|a|gamma))+", s): continue
    dz, da, ok = 0, 1, True
    for mult, part in re.findall(r"(\d*)(3He|n|p|d|t|a|gamma)", s):
        if part not in PART: ok = False; break
        k = int(mult) if mult else 1; dz += k * PART[part][0]; da += k * PART[part][1]
    if ok: tab[str(mt)] = [dz, da]
out = {"note": "Residual nuclide offset (dZ, dA) per reaction MT: residual = target + neutron - emitted particles. "
               "Vendored so the library build needs no runtime package; regenerate with controls/gen_mt_products.py.",
       "source": f"openmc.data.REACTION_NAME (OpenMC {openmc.__version__}), MIT licence, parsed for emitted particles",
       "table": tab}
os.makedirs(os.path.join(ROOT, "data"), exist_ok=True)
json.dump(out, open(os.path.join(ROOT, "data", "mt_products.json"), "w"), indent=1)
print(f"data/mt_products.json: {len(tab)} reactions")
