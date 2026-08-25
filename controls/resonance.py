"""ACTINV P3-G2: own resolved-resonance parser and reconstruction (ENDF-6 MF=2/MT=151; SLBW LRF=1, MLBW LRF=2,
Reich-Moore LRF=3) from the ENDF-102 Appendix D formulas. Vectorised numpy; 0 K; returns barns.
Unsupported (ledgered by the caller): NRO != 0 (energy-dependent AP), LRF=4/7, LRU=2 with LSSF=0."""
import numpy as np, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from endf_common import fields, endf_float, read_list, sections
KCONST = 2.196771e-3   # k [1e12 cm^-1] = KCONST * AWRI/(AWRI+1) * sqrt(E[eV])
def parse_mf2(path):
    """Return dict: awr, isotopes -> list of ranges with parameters."""
    for (mat, mf, mt), lines in sections(path):
        if mf == 2 and mt == 151: break
    else: return None
    h = fields(lines[0]); za, awr, nis = endf_float(h[0]), endf_float(h[1]), int(h[4]); i = 1; isotopes = []
    for _ in range(nis):
        c = fields(lines[i]); zai, abn, lfw, ner = endf_float(c[0]), endf_float(c[1]), int(c[3]), int(c[4]); i += 1; ranges = []
        for _ in range(ner):
            c = fields(lines[i]); el, eh, lru, lrf, nro, naps = endf_float(c[0]), endf_float(c[1]), int(c[2]), int(c[3]), int(c[4]), int(c[5]); i += 1
            rg = {"EL": el, "EH": eh, "LRU": lru, "LRF": lrf, "NRO": nro, "NAPS": naps}
            if nro != 0:  # TAB1 AP(E): skip it structurally
                from endf_common import read_tab1; _, i = read_tab1(lines, i); rg["unsupported"] = "NRO!=0"
            if lru == 0: c = fields(lines[i]); rg.update({"SPI": endf_float(c[0]), "AP": endf_float(c[1])}); i += 1
            elif lru == 1 and lrf in (1, 2):
                c = fields(lines[i]); spi, ap, nls = endf_float(c[0]), endf_float(c[1]), int(c[4]); i += 1; rg.update({"SPI": spi, "AP": ap, "L": []})
                for _ in range(nls):
                    (awri, qx, l, lrx, n6, nrs, v), i = read_list(lines, i); res = np.array(v).reshape(nrs, 6)
                    rg["L"].append({"AWRI": awri, "QX": qx, "L": l, "LRX": lrx, "ER": res[:, 0], "AJ": res[:, 1], "GT": res[:, 2], "GN": res[:, 3], "GG": res[:, 4], "GF": res[:, 5]})
            elif lru == 1 and lrf == 3:
                c = fields(lines[i]); spi, ap, lad, nls, nlsc = endf_float(c[0]), endf_float(c[1]), int(c[2]), int(c[4]), int(c[5]); i += 1; rg.update({"SPI": spi, "AP": ap, "LAD": lad, "L": []})
                for _ in range(nls):
                    (awri, apl, l, _, n6, nrs, v), i = read_list(lines, i); res = np.array(v).reshape(nrs, 6)
                    rg["L"].append({"AWRI": awri, "APL": apl, "L": l, "ER": res[:, 0], "AJ": res[:, 1], "GN": res[:, 2], "GG": res[:, 3], "GFA": res[:, 4], "GFB": res[:, 5]})
            elif lru == 2:
                c = fields(lines[i]); rg.update({"SPI": endf_float(c[0]), "AP": endf_float(c[1]), "LSSF": int(c[2])}); nls = int(c[4]); i += 1
                if lrf == 1:
                    # LFW=0: per L a LIST; LFW=1: NE energies list then per L per J LIST — skip structurally
                    if lfw == 0:
                        for _ in range(nls): _, i = read_list(lines, i)
                    else:
                        (_, _, _, _, ne, nls2, es), i = read_list(lines, i)
                        for _ in range(nls2):
                            c = fields(lines[i]); njs = int(c[4]); i += 1
                            for _ in range(njs): _, i = read_list(lines, i)
                elif lrf == 2:
                    for _ in range(nls):
                        c = fields(lines[i]); njs = int(c[4]); i += 1
                        for _ in range(njs): _, i = read_list(lines, i)
                else: rg["unsupported"] = f"LRU=2 LRF={lrf}"
            else:
                rg["unsupported"] = f"LRU={lru} LRF={lrf}"; ranges.append(rg)
                break   # length of an unsupported range is unknown: stop parsing this isotope's ranges (recorded)
            ranges.append(rg)
        isotopes.append({"ZAI": zai, "ABN": abn, "LFW": lfw, "ranges": ranges})
    return {"ZA": za, "AWR": awr, "isotopes": isotopes}
def penetration_shift(l, rho):
    r2 = rho * rho
    if l == 0: return rho, np.zeros_like(rho)
    if l == 1: return rho * r2 / (1 + r2), -1.0 / (1 + r2)
    if l == 2: d = 9 + 3 * r2 + r2 * r2; return rho * r2 * r2 / d, -(18 + 3 * r2) / d
    if l == 3: d = 225 + 45 * r2 + 6 * r2 * r2 + r2 ** 3; return rho * r2 ** 3 / d, -(675 + 90 * r2 + 6 * r2 * r2) / d
    if l == 4: d = 11025 + 1575 * r2 + 135 * r2 * r2 + 10 * r2 ** 3 + r2 ** 4; return rho * r2 ** 4 / d, -(44100 + 4725 * r2 + 270 * r2 * r2 + 10 * r2 ** 3) / d
    raise ValueError("l>4")
def phase_shift(l, rho):
    if l == 0: return rho
    if l == 1: return rho - np.arctan(rho)
    if l == 2: return rho - np.arctan(3 * rho / (3 - rho * rho))
    if l == 3: return rho - np.arctan((15 * rho - rho ** 3) / (15 - 6 * rho * rho))
    if l == 4: return rho - np.arctan((105 * rho - 10 * rho ** 3) / (105 - 45 * rho * rho + rho ** 4))
    raise ValueError("l>4")
def reconstruct_range(rg, E, awr_file, chunk=2000):
    """Memory-bounded wrapper: evaluates in energy chunks (each chunk allocates ~chunk × n_resonances complex arrays)."""
    E = np.asarray(E, float)
    if E.size <= chunk: return _reconstruct_range(rg, E, awr_file)
    parts = [_reconstruct_range(rg, E[i:i + chunk], awr_file) for i in range(0, E.size, chunk)]
    if parts[0] is None: return None
    return {k: np.concatenate([p_[k] for p_ in parts]) for k in parts[0]}
def _reconstruct_range(rg, E, awr_file):
    """Return dict of arrays (barns) at energies E (eV): 'elastic', 'capture', 'fission', 'total' from resonances only
    (the MF=3 background must be added by the caller)."""
    E = np.asarray(E, float); el = np.zeros_like(E); cap = np.zeros_like(E); fis = np.zeros_like(E)
    if rg.get("unsupported") or rg["LRU"] != 1 or rg["LRF"] not in (1, 2, 3): return None
    spi, ap, naps = rg["SPI"], rg["AP"], rg["NAPS"]
    for Lg in rg["L"]:
        awri = Lg["AWRI"]; l = Lg["L"]; k = KCONST * awri / (awri + 1.0) * np.sqrt(E); a_calc = 0.123 * awri ** (1.0 / 3.0) + 0.08
        apl = Lg.get("APL", 0.0) or ap
        a_pen = ap if naps == 1 else a_calc            # channel radius for penetrability/shift
        if rg["LRF"] == 3 and naps == 1: a_pen = apl
        a_phi = apl                                     # scattering radius for the hard-sphere phase
        P, S = penetration_shift(l, k * a_pen); phi = phase_shift(l, k * a_phi)
        kr = KCONST * awri / (awri + 1.0) * np.sqrt(np.abs(Lg["ER"])); Pr, Sr = penetration_shift(l, kr * a_pen)
        pik2 = np.pi / (k * k); sin2 = np.sin(phi) ** 2
        Js = np.unique(Lg["AJ"]); gsum = 0.0
        for J in Js:
            m = Lg["AJ"] == J; gJ = (2 * abs(J) + 1) / (2 * (2 * spi + 1)); gsum += gJ
            ER = Lg["ER"][m]; GN = Lg["GN"][m]; GG = Lg["GG"][m]; Prm = Pr[m]; Srm = Sr[m]
            Gn = GN[None, :] * P[:, None] / Prm[None, :]                     # energy-dependent neutron width
            if rg["LRF"] in (1, 2):
                GF = Lg["GF"][m]; GT = Lg["GT"][m]; GX = np.maximum(GT - GN - GG - GF, 0.0)   # competitive width (constant; LRX handled as constant)
                Gtot = Gn + (GG + GF + GX)[None, :]
                Ep = ER[None, :] + GN[None, :] * (Srm[None, :] - S[:, None]) / (2 * Prm[None, :])
                den = (E[:, None] - Ep) ** 2 + Gtot ** 2 / 4
                cap += pik2 * gJ * np.sum(Gn * GG[None, :] / den, axis=1); fis += pik2 * gJ * np.sum(Gn * GF[None, :] / den, axis=1)
                if rg["LRF"] == 1:
                    el += pik2 * gJ * np.sum((Gn * Gn - 2 * Gn * Gtot * sin2[:, None] + 2 * (E[:, None] - Ep) * Gn * np.sin(2 * phi)[:, None]) / den, axis=1)
                else:
                    U = np.exp(-2j * phi) * (1 + 1j * np.sum(Gn / (Ep - E[:, None] - 1j * Gtot / 2), axis=1))
                    el += pik2 * gJ * (np.abs(1 - U) ** 2 - 4 * sin2)
            else:  # Reich-Moore
                GFA = Lg["GFA"][m]; GFB = Lg["GFB"][m]; has_f = np.any(GFA != 0) or np.any(GFB != 0)
                d = ER[None, :] - E[:, None] - 1j * GG[None, :] / 2
                if not has_f:
                    K = 0.5 * np.sum(Gn / d, axis=1); W = 1.0 / (1 - 1j * K); U = np.exp(-2j * phi) * (2 * W - 1)
                    tot = 2 * pik2 * gJ * (1 - U.real); e_ = pik2 * gJ * np.abs(1 - U) ** 2
                    el += e_ - pik2 * gJ * 4 * sin2; cap += tot - e_
                else:
                    sa = np.sign(GFA) * np.sqrt(np.abs(GFA)); sb = np.sign(GFB) * np.sqrt(np.abs(GFB)); sn = np.sqrt(Gn)
                    n = E.size; K = np.zeros((n, 3, 3), complex)
                    amp = [sn, np.broadcast_to(sa[None, :], sn.shape), np.broadcast_to(sb[None, :], sn.shape)]
                    for c1 in range(3):
                        for c2 in range(3): K[:, c1, c2] = 0.5 * np.sum(amp[c1] * amp[c2] / d, axis=1)
                    I3 = np.eye(3)[None, :, :]; W = np.linalg.inv(I3 - 1j * K); Om = np.array([np.exp(-1j * phi), np.ones_like(phi), np.ones_like(phi)]).T  # (n,3)
                    U = Om[:, :, None] * (2 * W - I3) * Om[:, None, :]
                    tot = 2 * pik2 * gJ * (1 - U[:, 0, 0].real); e_ = pik2 * gJ * np.abs(1 - U[:, 0, 0]) ** 2; f_ = pik2 * gJ * (np.abs(U[:, 0, 1]) ** 2 + np.abs(U[:, 0, 2]) ** 2)
                    el += e_ - pik2 * gJ * 4 * sin2; fis += f_; cap += tot - e_ - f_
        el += pik2 * (2 * l + 1) * 4 * sin2   # hard-sphere for all J (the per-J terms above subtracted 4 sin^2 once each)
    return {"elastic": el, "capture": cap, "fission": fis, "total": el + cap + fis}
def resonance_energies(rg):
    return np.concatenate([np.abs(Lg["ER"]) for Lg in rg.get("L", [])]) if rg.get("L") else np.array([])
