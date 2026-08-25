"""Readers for the FNS/CoNDERC FISPACT-II files (.i, .exp, .out, .nuclides). Own code; units made explicit."""
import re, numpy as np
UNIT_S = {"SECS": 1.0, "MINS": 60.0, "HOURS": 3600.0, "DAYS": 86400.0, "YEARS": 365.25 * 86400.0}
def read_i(path):
    """Return dict: elements {EL: wt%}, mass_kg, density, flux_total, t_irr_s, cooling_cum_s (list)."""
    lines = [l.split() for l in open(path) if l.strip()]
    out = {"elements": {}, "cooling_cum_s": []}; i = 0; flux_on = None; cum = 0.0; phase = "pre"
    while i < len(lines):
        t = lines[i]; k = t[0].upper()
        if k == "MASS":
            out["mass_kg"] = float(t[1]); nel = int(t[2])
            for j in range(nel): el, w = lines[i + 1 + j][0], float(lines[i + 1 + j][1]); out["elements"][el.upper()] = w
            i += nel
        elif k == "DENSITY": out["density"] = float(t[1])
        elif k == "FLUX":
            flux_on = float(t[1])
            if flux_on > 0: out["flux_total"] = flux_on; phase = "irr"
            else: phase = "cool"
        elif k == "TIME":
            val = float(t[1]); unit = t[2].upper() if len(t) > 2 and t[2].upper() in UNIT_S else "SECS"; dt = val * UNIT_S[unit]
            if phase == "irr": out["t_irr_s"] = out.get("t_irr_s", 0.0) + dt
            elif phase == "cool": cum += dt; out["cooling_cum_s"].append(cum)
        i += 1
    return out
def read_exp(path):
    a = np.loadtxt(path, ndmin=2); return {"t_raw": a[:, 0], "heat_uW_g": a[:, 1], "sigma_uW_g": a[:, 2]}
def read_out_heat(path):
    """TOTAL HEAT PRODUCTION (kW) per TIME INTERVAL, with the interval's cooling/irradiation flag."""
    heats = []; cur = None
    for l in open(path, errors="replace"):
        if "TIME INTERVAL" in l:
            cur = {"interval": int(re.search(r"TIME INTERVAL\s+(\d+)", l).group(1)), "cooling": "COOLING TIME" in l}
            m = re.search(r"TIME IS\s+([0-9.E+-]+)\s+SECS", l); cur["dt_s"] = float(m.group(1)) if m else None
        if "TOTAL HEAT PRODUCTION" in l and cur is not None:
            m = re.search(r"TOTAL HEAT PRODUCTION\s+([0-9.E+-]+)\s*kW", l); cur["heat_kW"] = float(m.group(1)); heats.append(cur); cur = None
    return heats
def read_nuclides(path):
    """Per-nuclide heat (kW/kg) vs time (years). Returns times_y, total, {nuclide: array}."""
    names = None; rows = []
    for l in open(path, errors="replace"):
        if l.startswith("# step"):
            rest = re.split(r"\bTotal\b", l, maxsplit=1)[1]  # nuclide columns follow "Total"; symbols are space-padded ("H   3")
            names = [f"{el} {a}" for el, a in re.findall(r"([A-Z][a-z]?)\s*(\d+[a-z]?)", rest)]; continue
        if l.startswith("#") or not l.strip(): continue
        rows.append(l.split())
    arr = np.array([[float(x) for x in r[1:]] for r in rows])  # time, uncert, total, nuclides...
    return {"t_y": arr[:, 0], "uncert": arr[:, 1], "total_kW_kg": arr[:, 2], "nuclides": {n: arr[:, 3 + k] for k, n in enumerate(names)}}
