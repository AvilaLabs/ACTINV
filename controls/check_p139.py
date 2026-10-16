#!/usr/bin/env python3
"""P139 gates G2, G3, G4 and G5 (protocols/ACTINV-P139_PROTOCOL.md).

  G2  examples/fns_fe_5min.json, examples/shielding_demo.json and the P129 geometry case on the TENDL-2025 W-186
      LRF=2 file (foil 0.01 cm and 0.1 cm) give byte-identical results (apart from `ms`) under the reference and
      candidate binaries.
  G3  Independent recomputation of the geometry self-shielding factors of a W-186 foil (0.01 cm and 0.1 cm) from the
      ENDF/B-VIII.1 evaluation, whose resolved range is R-matrix limited (LRF=7). A second MF=2 reader (written here,
      not shared with the solver; LRF 1-3 from check_p129) parses particle pairs, spin groups, channels and
      resonance widths, classifies channels by pair MT (2 neutron, 102 capture, others total only), takes |Gamma| for
      IFG=0 or 2 P gamma^2 for IFG=1, g = (2J+1)/(2(2I+1)), and rebuilds z, G(z) and the weighted group means. They
      must match the candidate ledger within 1e-9 relative; every factor lies in [A2, 1] and falls monotonically
      from 0.01 cm to 0.1 cm.
  G4  The TENDL-2025 W-186 LRF=2 resolved range is transcribed into two synthetic ENDF-6 LRF=7 (KRM=3) tapes of the
      same resonances: IFG=0 (widths) and IFG=1 (reduced-width amplitudes). Both are hash-pinned as the resonance
      file; the candidate's group factors from each must equal those from the original LRF=2 file within 1e-12
      (IFG=0) and 1e-9 (IFG=1) relative in every group.
  G5  Report only: ENDF/B-VIII.1 (LRF=7) against TENDL-2025 (LRF=2) group factors for both foils, and ENDF/B-VIII.0
      (LRF=7) when its run succeeds.

G0, G1 and G6 are recorded as external by the coordinator. The verdict is PASS only if G2, G3 and G4 all ran and
passed.

Usage:
  check_p139.py --reference BIN --candidate BIN [--workdir target/p139-work] [--out results/p139_lrf7.json]
                [--gates G2,G3,G4,G5] [--timeout SECONDS] [--nuclear-data DIR] [--awri-source pair|head]
  check_p139.py --self-check [--workdir target/p139-work] [--nuclear-data DIR]

--self-check runs no binary: it parses the ENDF/B-VIII.1 and TENDL-2025 files, writes the two synthetic tapes, parses
them back and checks the derived (E, g, Gamma_n, Gamma_gamma, Gamma) per resonance and the group factors against the
LRF=2 originals.

Runs go through subprocess (no shell), serially, from the repository root, each under --timeout. Run inside the
required systemd cgroup with a disk-backed TMPDIR. Exit 0 only on PASS.
"""
import argparse
import hashlib
import json
import math
import os
import re
import statistics
import subprocess
import sys
from decimal import Decimal, ROUND_HALF_EVEN, getcontext
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_p129 as base  # noqa: E402  (P129 checker: MF=2 LRF 1-3 reader, G(z) model, runner, comparison)

getcontext().prec = 60

ROOT = base.ROOT
PROTOCOL = ROOT / "protocols" / "ACTINV-P139_PROTOCOL.md"
DEMO_SPEC = base.DEMO_SPEC
FNS_SPEC = base.FNS_SPEC
DEFAULT_TIMEOUT_S = 3600
W186_ZA = base.W186_ZA
FOIL_THICKNESS_CM = base.FOIL_THICKNESS_CM
K_WAVE = base.K_WAVE
IFG0_TOL = 1e-12
IFG1_TOL = 1e-9
MAT = 7443
# Q value (eV) of the synthetic competitive pair. Amendment A: Q = 0 keeps the channel open at every resonance energy,
# so it carries the LRF=2 total's excess under IFG=0 and IFG=1 alike (a channel closed at a resonance contributes no
# width). The pair carries PNT=0, so no kinematic quantity of it enters a width.
COMPETITIVE_Q_EV = 0.0
COMPETITIVE_MT = 51


def sha256_file(path):
    return base.sha256_file(path)


def rel_diff(a, b):
    return base.rel_diff(a, b)


# ---------------------------------------------------------------- ENDF-6 number and record formatting


def dec(raw):
    """Exact Decimal of an ENDF numeric field: plain, E-form, or the exponent-without-E form ('1.883000+1')."""
    s = raw.strip()
    if not s:
        return Decimal(0)
    m = re.fullmatch(r"([+-]?(?:\d+\.?\d*|\.\d+))(?:[eE]([+-]?\d+)|([+-]\d+))?", s)
    if not m:
        raise ValueError(f"unreadable ENDF number {raw!r}")
    exp = m.group(2) or m.group(3)
    return Decimal(m.group(1) + ("E" + exp if exp else ""))


def _plain(x):
    """Longest-mantissa plain-decimal field of x (11 columns or fewer). The zero before the point of a number below
    one is dropped ('.1581138830'), which Fortran F11 input and the solver's reader both accept and which buys one
    more significant digit."""
    sign = "-" if x < 0 else ""
    ax = abs(x)
    for nd in range(10, -1, -1):
        s = format(ax.quantize(Decimal(1).scaleb(-nd), rounding=ROUND_HALF_EVEN), "f")
        if s.startswith("0."):
            s = s[1:]
        s = sign + s
        if len(s) <= 11:
            return s
    return None


def _exponent_form(x):
    """ENDF exponent field ('d.dddddd+e', no E) of x, 11 columns or fewer: the shortest mantissa of at least seven
    significant digits that is exact, else the longest mantissa that fits."""
    sign = "-" if x < 0 else ""
    ax = abs(x)
    e = ax.adjusted()
    fitting = []
    for k in range(0, 10):
        mant = ax.scaleb(-e).quantize(Decimal(1).scaleb(-k), rounding=ROUND_HALF_EVEN)
        e2 = e
        if mant >= 10:
            mant, e2 = (mant / 10).quantize(Decimal(1).scaleb(-k), rounding=ROUND_HALF_EVEN), e + 1
        s = sign + format(mant, "f") + f"{e2:+d}"
        if len(s) <= 11:
            fitting.append((k, s))
    for k, s in fitting:
        if k >= 6 and dec(s) == x:
            return s
    return fitting[-1][1] if fitting else None


def fmt_field(x):
    """The 11-column ENDF field closest to x. Of an equally close plain and exponent form the exponent form is used,
    as in ENDF files. Returns (field, represented_exactly)."""
    x = x if isinstance(x, Decimal) else Decimal(repr(float(x)))
    if x == 0:
        return " 0.000000+0", True
    best = None
    for form in (_exponent_form(x), _plain(x)):
        if form is None:
            continue
        err = abs(dec(form) - x)
        if best is None or err < best[0]:
            best = (err, form)
    return best[1].rjust(11), best[0] == 0


def _cell(v):
    if isinstance(v, str):
        if len(v) != 11:
            raise ValueError(f"verbatim ENDF field {v!r} is not 11 columns")
        return v
    if isinstance(v, int):
        return f"{v:>11d}"
    return fmt_field(v)[0]


def record(values, mat, mf, mt, seq):
    """One 80-column ENDF record: six 11-column cells (int, float/Decimal, or a verbatim 11-column str) + tail."""
    cells = [_cell(v) for v in values] + [" " * 11] * (6 - len(values))
    return "".join(cells) + f"{mat:4d}{mf:2d}{mt:3d}{seq:5d}"


def blank_cont(mat, mf, mt, seq):
    return " " * 66 + f"{mat:4d}{mf:2d}{mt:3d}{seq:5d}"


class Section:
    """Sequentially numbered records of one MF/MT section."""

    def __init__(self, mat, mf, mt):
        self.mat, self.mf, self.mt, self.lines = mat, mf, mt, []

    def cont(self, *values):
        self.lines.append(record(values, self.mat, self.mf, self.mt, len(self.lines) + 1))

    def listing(self, head, payload):
        """LIST record: head = (C1, C2, L1, L2, N2); NPL = len(payload) is written as N1."""
        self.cont(head[0], head[1], head[2], head[3], len(payload), head[4])
        payload = [float(v) if isinstance(v, int) else v for v in payload]
        for k in range(0, len(payload), 6):
            chunk = list(payload[k:k + 6])
            self.cont(*(chunk + [0.0] * (6 - len(chunk))))

    def text(self, s):
        self.lines.append(s.ljust(66)[:66] + f"{self.mat:4d}{self.mf:2d}{self.mt:3d}{len(self.lines) + 1:5d}")

    def send(self):
        return blank_cont(self.mat, self.mf, 0, 99999)


# ---------------------------------------------------------------- independent ENDF-6 MF=2 reader (LRF 1-3 and 7)


class Cursor:
    def __init__(self, lines):
        self.lines, self.i = lines, 0

    def cont(self):
        raw = base.fields(self.lines[self.i])
        self.i += 1
        return raw

    def listing(self):
        head = self.cont()
        npl = base.endf_int(head[4])
        raw = []
        for _ in range(-(-npl // 6)):
            raw.extend(self.cont())
        return head, raw[:npl]

    def tab1(self):
        head = self.cont()
        nr, npts = base.endf_int(head[4]), base.endf_int(head[5])
        self.i += -(-nr // 3) + -(-npts // 3)
        return head


def mf2_lines_streamed(path):
    """MF=2/MT=151 payload lines (SEND dropped) of the first material, read line by line."""
    out, mat = [], None
    with open(path, "r") as f:
        for line in f:
            if line[70:72].strip() != "2" or line[72:75].strip() != "151":
                if out and line[70:72].strip() not in ("2", ""):
                    break
                continue
            this = line[66:70].strip()
            if mat is None:
                mat = this
            if this != mat:
                break
            out.append(line.rstrip("\n"))
    return out


def parse_rml_range(cur):
    """The body of a LRU=1/LRF=7 range, after its range CONT record."""
    ctl = cur.cont()
    ifg, krm, njs = base.endf_int(ctl[2]), base.endf_int(ctl[3]), base.endf_int(ctl[4])
    if ifg not in (0, 1) or krm != 3:
        raise ValueError(f"LRF=7 IFG={ifg} KRM={krm}: only IFG 0/1 with KRM=3 is handled")
    head, raw = cur.listing()
    npp = base.endf_int(head[2])
    if base.endf_int(head[4]) != 12 * npp or base.endf_int(head[5]) != 2 * npp:
        raise ValueError("particle-pair LIST counts disagree with NPP")
    vals = [base.endf_float(x) for x in raw]
    pairs = []
    for k in range(0, len(vals), 12):
        v = vals[k:k + 12]
        pairs.append({"ma": v[0], "mb": v[1], "za": int(round(v[2])), "zb": int(round(v[3])), "ia": v[4], "ib": v[5],
                      "q": v[6], "pnt": int(round(v[7])), "shf": int(round(v[8])), "mt": int(round(v[9])),
                      "pa": v[10], "pb": v[11]})
    groups = []
    for _ in range(njs):
        head, raw = cur.listing()
        kbk, kps = base.endf_int(head[2]), base.endf_int(head[3])
        nch = base.endf_int(head[5])
        if kbk or kps:
            raise ValueError("R-matrix background (KBK) or phase-shift (KPS) extension records are not handled")
        if base.endf_int(head[4]) != 6 * nch:
            raise ValueError("spin-group channel LIST has NPL != 6*NCH")
        v = [base.endf_float(x) for x in raw]
        channels = []
        for k in range(0, len(v), 6):
            c = v[k:k + 6]
            ipp = int(round(c[0]))
            if not 1 <= ipp <= len(pairs):
                raise ValueError(f"channel particle-pair index {ipp} out of range")
            channels.append({"pair": ipp - 1, "l": int(round(c[1])), "s": c[2], "bnd": c[3], "ape": c[4],
                             "apt": c[5]})
        rhead, rraw = cur.listing()
        nrs = base.endf_int(rhead[3])
        per = 6 * (-(-(nch + 1) // 6))
        if nrs > 0 and (base.endf_int(rhead[5]) != nrs or base.endf_int(rhead[4]) != per * nrs):
            raise ValueError("resonance LIST counts disagree with NRS and the channel count")
        rv = [base.endf_float(x) for x in rraw]
        resonances = [{"E": rv[k], "widths": rv[k + 1:k + 1 + nch]} for k in range(0, per * nrs, per)]
        groups.append({"aj": base.endf_float(head[0]), "pj": base.endf_float(head[1]), "channels": channels,
                       "resonances": resonances})
    return {"ifg": ifg, "krm": krm, "pairs": pairs, "groups": groups}


def parse_mf2_any(lines):
    """Resolved ranges (LRU=1, LRF=1/2/3/7) of an MF=2/MT=151 section, raw ENDF fields retained for LRF 1-3.
    Stops at the first unresolved or unrecognised range. `consumed` is true when every record was read."""
    cur = Cursor(lines)
    head = cur.cont()
    out = {"za": int(round(base.endf_float(head[0]))), "awr": base.endf_float(head[1]), "za_raw": head[0],
           "awr_raw": head[1], "isotopes": [], "consumed": False}
    nis = base.endf_int(head[4])
    stopped = False
    for _ in range(nis):
        iso = cur.cont()
        entry = {"zai": int(round(base.endf_float(iso[0]))), "zai_raw": iso[0], "ranges": [], "stopped": None}
        out["isotopes"].append(entry)
        for _ in range(base.endf_int(iso[4])):
            if stopped:
                break
            rng = cur.cont()
            item = {"el": base.endf_float(rng[0]), "eh": base.endf_float(rng[1]), "el_raw": rng[0], "eh_raw": rng[1],
                    "lru": base.endf_int(rng[2]), "lrf": base.endf_int(rng[3]), "nro": base.endf_int(rng[4]),
                    "naps": base.endf_int(rng[5])}
            if item["nro"] == 1:
                cur.tab1()
            if item["lru"] == 1 and item["lrf"] in (1, 2, 3):
                ctl = cur.cont()
                item.update({"spi": base.endf_float(ctl[0]), "ap": base.endf_float(ctl[1]), "ap_raw": ctl[1],
                             "spi_raw": ctl[0], "groups": []})
                for _ in range(base.endf_int(ctl[4])):
                    lh, raw = cur.listing()
                    nrs = base.endf_int(lh[5])
                    if base.endf_int(lh[4]) != 6 * nrs:
                        raise ValueError("LRF=1/2/3 LIST has NPL != 6*NRS")
                    res = []
                    for k in range(0, len(raw), 6):
                        r = raw[k:k + 6]
                        v = [base.endf_float(x) for x in r]
                        if item["lrf"] in (1, 2):
                            res.append({"E": v[0], "J": v[1], "GT": v[2], "GN": v[3], "GG": v[4], "GF": v[5], "raw": r})
                        else:
                            res.append({"E": v[0], "J": v[1], "GN": v[2], "GG": v[3], "GFA": v[4], "GFB": v[5],
                                        "raw": r})
                    item["groups"].append({"awri": base.endf_float(lh[0]), "awri_raw": lh[0],
                                           "l": base.endf_int(lh[2]), "lrx": base.endf_int(lh[3]), "resonances": res})
            elif item["lru"] == 1 and item["lrf"] == 7:
                item["rml"] = parse_rml_range(cur)
            elif item["lru"] == 2:
                entry["stopped"] = "unresolved range reached"
                stopped = True
            else:
                entry["stopped"] = f"unsupported LRU={item['lru']}/LRF={item['lrf']}"
                stopped = True
            entry["ranges"].append(item)
        if stopped:
            break
    out["consumed"] = (cur.i == len(lines)) and not stopped
    return out


def parse_file_any(path):
    lines = mf2_lines_streamed(path)
    if not lines:
        raise ValueError(f"{path}: no MF=2/MT=151 section")
    return parse_mf2_any(lines)


# ---------------------------------------------------------------- hard-sphere penetrability (ENDF-6 formulas)
#
# Conventions for the IFG=1 conversion Gamma_c = 2 P_c(E_r) gamma_c^2, and how this reader relates to the solver
# (crates/actinv-data/src/resonance.rs: rml_reduced_amplitude, rml_wave_coefficient) and to NJOY2016 (samm.f90: fxradi,
# betset), which was read for the cross-check (the ENDF-102 text itself is not on this machine):
#   * P_c uses the TRUE channel radius APT (the effective radius APE enters only the phase shift); radii are in
#     1e-12 cm and are used as listed. A PNT=1 channel with APT <= 0 is an error in the solver; NJOY substitutes
#     0.123 A^(1/3) + 0.08 only when it converts LRF 1-3 data, never for LRF=7.
#   * rho = k_c APT with k_c = K_WAVE sqrt(mu_c (M_B/(M_A+M_B))_entrance / M_A,entrance) sqrt(|E_r - E_threshold|),
#     mu_c = pair M_A M_B/(M_A+M_B), E_threshold = -Q/(M_B/(M_A+M_B))_entrance (0 for the neutron pair). For the
#     entrance pair this is K_WAVE AWRI/(AWRI+1) sqrt(|E_r|) with AWRI = M_B/M_A of the MT=2 pair, not the AWR of the
#     MF=1/MF=2 HEAD record (ENDF/B-VIII.1 W-186: 184.3462 against 184.357).
#   * PNT=0 pairs and the eliminated photon pair (MT=102) have P = 1: Gamma = 2 gamma^2 (the solver writes the
#     eliminated-capture amplitude as sqrt(|Gamma|/2); NJOY betapr = sqrt(|Gamma|/2) for lpent <= 0).
#   * Coulomb pairs are not handled here (the solver has them); W-186 has none.
#   * The target spin I of g = (2J+1)/(2(2I+1)) is the spin of the heavier particle of the MT=2 pair (IB in ENDF/B-VIII.1
#     W-186, where IA = 1/2 is the neutron); the spin group's J is |AJ| because a negative AJ encodes parity.


def hard_sphere_p(l, rho):
    """Blatt-Weisskopf penetrability P_l(rho) from the Lane-Thomas recurrence of the ENDF-6 formats manual:
         P_0 = rho,  S_0 = 0,
         P_l = rho^2 P_{l-1} / ((l - S_{l-1})^2 + P_{l-1}^2),
         S_l = rho^2 (l - S_{l-1}) / ((l - S_{l-1})^2 + P_{l-1}^2) - l.
    Written independently of the closed forms the solver uses; `penetrability_closed_form_check` compares them."""
    p, s = rho, 0.0
    for n in range(1, l + 1):
        d = (n - s) ** 2 + p * p
        p, s = rho * rho * p / d, rho * rho * (n - s) / d - n
    return p


def hard_sphere_p_closed(l, rho):
    r2 = rho * rho
    if l == 0:
        return rho
    if l == 1:
        return rho * r2 / (1.0 + r2)
    if l == 2:
        return rho * r2 * r2 / (9.0 + 3.0 * r2 + r2 * r2)
    if l == 3:
        return rho * r2 ** 3 / (225.0 + 45.0 * r2 + 6.0 * r2 * r2 + r2 ** 3)
    if l == 4:
        return rho * r2 ** 4 / (11025.0 + 1575.0 * r2 + 135.0 * r2 * r2 + 10.0 * r2 ** 3 + r2 ** 4)
    raise ValueError("closed form only for l <= 4")


def penetrability_closed_form_check():
    worst = 0.0
    for l in range(5):
        for rho in (1e-4, 3e-3, 0.05, 0.4, 1.0, 2.5, 9.0):
            worst = max(worst, rel_diff(hard_sphere_p(l, rho), hard_sphere_p_closed(l, rho)))
    return worst


# ---------------------------------------------------------------- widths and lines from LRF=7 and LRF=1-3


def pair_class(pair):
    """Channel class by particle-pair MT: neutron (2), capture (102), everything else counts only toward Gamma."""
    return "neutron" if pair["mt"] == 2 else ("capture" if pair["mt"] == 102 else "other")


def entrance_pair(rml):
    for p in rml["pairs"]:
        if p["mt"] == 2:
            if p["ma"] <= 0.0 or p["mb"] <= 0.0:
                raise ValueError("neutron entrance pair has invalid masses")
            return p
    raise ValueError("no MT=2 neutron entrance pair")


def lab_to_cm(pair):
    return pair["mb"] / (pair["ma"] + pair["mb"])


def wave_coefficient(pair, ent):
    """k_c / sqrt(E_c): K_WAVE * sqrt(mu_c * (M_B/(M_A+M_B))_entrance / M_A,entrance), mu_c = pair M_B/(M_A+M_B) * M_A.
    For the neutron entrance pair this is K_WAVE * AWRI/(AWRI+1) with AWRI = M_B/M_A."""
    if pair["ma"] <= 0.0 or pair["mb"] <= 0.0:
        raise ValueError(f"pair MT={pair['mt']} has invalid masses for a wave number")
    return K_WAVE * math.sqrt(lab_to_cm(pair) * pair["ma"] * lab_to_cm(ent) / ent["ma"])


def channel_penetrability(rml, pair, channel, e_res, ent):
    """P_c at the resonance energy: 1 for a photon pair or PNT=0, else the hard-sphere P_l at rho = k_c a_true with
    k_c from the energy above the pair's threshold, |E_r + Q/(M_B/(M_A+M_B))_entrance|, the convention of the
    solver's reconstruction (rml_reduced_amplitude). Charged pairs are not handled."""
    if pair["mt"] == 102 or pair["pnt"] == 0:
        return 1.0
    if pair["za"] != 0 and pair["zb"] != 0:
        raise NotImplementedError(f"Coulomb penetrability for pair MT={pair['mt']}")
    if channel["apt"] <= 0.0:
        raise ValueError(f"pair MT={pair['mt']} needs a positive true radius")
    threshold = -pair["q"] / lab_to_cm(ent)
    k = wave_coefficient(pair, ent) * math.sqrt(abs(e_res - threshold))
    return hard_sphere_p(channel["l"], k * channel["apt"])


def rml_resonances(rml, awri_source, head_awr):
    """Every resonance of an LRF=7 range as a dict: E, J, SPI, AWRI, GN, GG, GAMMA and `usable`. Widths are |Gamma_c|
    (IFG=0) or 2 P_c gamma_c^2 (IFG=1); GN sums the MT=2 channels, GG the MT=102 channels, GAMMA every channel."""
    ent = entrance_pair(rml)
    target_spin = abs(ent["ib"]) if ent["mb"] >= ent["ma"] else abs(ent["ia"])
    awri = ent["mb"] / ent["ma"] if awri_source == "pair" else head_awr
    out = []
    for gi, grp in enumerate(rml["groups"]):
        for res in grp["resonances"]:
            gn = gg = total = 0.0
            for channel, value in zip(grp["channels"], res["widths"]):
                pair = rml["pairs"][channel["pair"]]
                if rml["ifg"] == 0:
                    width = abs(value)
                else:
                    width = 2.0 * channel_penetrability(rml, pair, channel, res["E"], ent) * value * value
                total += width
                cls = pair_class(pair)
                if cls == "neutron":
                    gn += width
                elif cls == "capture":
                    gg += width
            out.append({"E": res["E"], "J": abs(grp["aj"]), "SPI": target_spin, "AWRI": awri, "GN": gn, "GG": gg,
                        "GAMMA": total, "spin_group": gi,
                        "usable": res["E"] > 0.0 and gn > 0.0 and gg > 0.0 and awri > 0.0})
    return out


def legacy_resonances(rng):
    """Every resonance of a LRF=1/2/3 range as the same dicts; GAMMA as check_p129 (max(GT, components) or the
    Reich-Moore sum)."""
    out = []
    for gi, group in enumerate(rng["groups"]):
        for r in group["resonances"]:
            if rng["lrf"] in (1, 2):
                gamma = max(r["GT"], r["GN"] + r["GG"] + r["GF"])
            else:
                gamma = r["GN"] + r["GG"] + abs(r["GFA"]) + abs(r["GFB"])
            out.append({"E": r["E"], "J": r["J"], "SPI": rng["spi"], "AWRI": group["awri"], "GN": r["GN"],
                        "GG": r["GG"], "GAMMA": gamma, "spin_group": gi, "l": group["l"],
                        "usable": r["E"] > 0.0 and r["GN"] > 0.0 and r["GG"] > 0.0 and group["awri"] > 0.0})
    return out


def all_resonances(parsed, awri_source="pair", za=W186_ZA):
    """(resonances, ranges summary) of the resolved ranges of the isotope `za`, in file order."""
    isotopes = [iso for iso in parsed["isotopes"] if iso["zai"] == za]
    if not isotopes and len(parsed["isotopes"]) == 1:
        isotopes = parsed["isotopes"]
    out, summary = [], []
    for iso in isotopes:
        for rng in iso["ranges"]:
            if rng["lru"] != 1:
                continue
            if rng["lrf"] == 7:
                rows = rml_resonances(rng["rml"], awri_source, parsed["awr"])
                summary.append({"lrf": 7, "ev": [rng["el"], rng["eh"]], "ifg": rng["rml"]["ifg"],
                                "krm": rng["rml"]["krm"], "pairs": len(rng["rml"]["pairs"]),
                                "spin_groups": len(rng["rml"]["groups"]), "resonances": len(rows)})
            elif rng["lrf"] in (1, 2, 3):
                rows = legacy_resonances(rng)
                summary.append({"lrf": rng["lrf"], "ev": [rng["el"], rng["eh"]], "resonances": len(rows)})
            else:
                raise ValueError(f"resolved range {rng['el']}-{rng['eh']} eV has LRF={rng['lrf']}")
            # P140 Amendment A repair 4 (SG-01): a level outside its range's [EL, EH] feeds the in-range cross
            # section but is not a resolved peak, so it is not a shielding line.
            for r in rows:
                r["outside_range"] = not (rng["el"] <= r["E"] <= rng["eh"])
                if r["outside_range"]:
                    r["usable"] = False
            summary[-1]["outside_range"] = sum(1 for r in rows if r["outside_range"])
            out.extend(rows)
    return out, summary


def usable(rows):
    return [r for r in rows if r["usable"]]


def statistical(r):
    return (2.0 * abs(r["J"]) + 1.0) / (2.0 * (2.0 * r["SPI"] + 1.0))


# ---------------------------------------------------------------- the TENDL LRF=2 -> synthetic LRF=7 transcription


def lrf2_source(parsed):
    """The single LRF=2 resolved range of a transcription source, with its L-groups resorted into (L, J) spin groups."""
    ranges = [r for iso in parsed["isotopes"] for r in iso["ranges"] if r["lru"] == 1]
    if len(ranges) != 1 or ranges[0]["lrf"] != 2:
        raise ValueError("the transcription source must have exactly one resolved range, with LRF=2")
    rng = ranges[0]
    awris = {g["awri"] for g in rng["groups"]}
    if len(awris) != 1:
        raise ValueError(f"L-groups carry different AWRI {sorted(awris)}; one entrance pair cannot represent them")
    awri_raw = rng["groups"][0]["awri_raw"]
    groups = {}
    for g in rng["groups"]:
        for r in g["resonances"]:
            groups.setdefault((g["l"], r["J"]), []).append(r)
    return rng, awri_raw, rng["groups"][0]["awri"], dict(sorted(groups.items()))


def channel_spin(l, j, spin):
    """The channel spin S = I +- 1/2 with |L - S| <= J <= L + S."""
    for s in sorted({abs(spin - 0.5), spin + 0.5}):
        if abs(l - s) <= j + 1e-9 and j <= l + s + 1e-9:
            return s
    raise ValueError(f"no channel spin for L={l}, J={j}, I={spin}")


def build_synthetic(parsed, ifg, head="evaluation"):
    """A complete ENDF-6 tape (TPID, MF1/MT451, MF2/MT151, SEND/FEND/MEND/TEND) holding the LRF=2 resolved range of
    `parsed` as one LRF=7 KRM=3 range. Returns (text, info). Channels per spin group, in order: the eliminated
    capture channel (pair MT=102, photon, PNT=0), the neutron channel (pair MT=2, PNT=1, radii = AP), a fission
    channel when any GF is non-zero (MT=18, PNT=0) and a competitive channel (MT=51, PNT=0) carrying
    max(0, GT - GN - GG - GF) when any is positive. IFG=0 lists the widths; IFG=1 lists gamma = sqrt(Gamma/(2P)) with
    P(|E_r|) from `hard_sphere_p` (P = 1 for the PNT=0 channels). The neutron pair has M_A = 1, M_B = AWRI of the
    LRF=2 L-groups, so the entrance pair reproduces the LRF=2 wave number. The competitive and fission channels reuse
    the neutron channel's L and channel spin and its radii, as stand-ins: only their widths enter the derivation.
    `head` sets the AWR of the MF=1/MF=2 HEAD ('evaluation': the source's HEAD AWR, 184.3569 for TENDL-2025 W-186,
    which differs from the L-group AWRI 184.4022; 'group': the L-group AWRI)."""
    rng, awri_raw, awri, groups = lrf2_source(parsed)
    spi = rng["spi"]
    z = parsed["za"] // 1000
    any_fission = any(dec(r["raw"][5]) != 0 for rs in groups.values() for r in rs)
    excess = {}
    for key, rs in groups.items():
        for r in rs:
            d = dec(r["raw"][2]) - (dec(r["raw"][3]) + dec(r["raw"][4]) + dec(r["raw"][5]))
            excess[id(r)] = d if d > 0 else Decimal(0)
    any_excess = any(v > 0 for v in excess.values())

    def pair(ma, mb, zb, ia, ib, q, pnt, mt, pb):
        return [ma, mb, 0, zb, ia, ib, q, pnt, 0, mt, 0, pb]

    compound = dec(awri_raw) + 1
    pairs = [pair(0.0, compound, z, 1.0, spi, 0.0, 0, 102, 0.0),
             pair(1.0, awri_raw, z, 0.5, rng["spi_raw"], 0.0, 1, 2, 1.0)]
    cls = ["capture", "neutron"]
    if any_fission:
        pairs.append(pair(0.0, compound, z, 0.0, spi, 0.0, 0, 18, 0.0))
        cls.append("fission")
    if any_excess:
        pairs.append(pair(1.0, awri_raw, z, 0.5, 2.0, COMPETITIVE_Q_EV, 0, COMPETITIVE_MT, 1.0))
        cls.append("competitive")
    ap = rng["ap_raw"]

    # synthetic MF=2 section
    awr_head = parsed["awr_raw"] if head == "evaluation" else awri_raw
    s2 = Section(MAT, 2, 151)
    s2.cont(parsed["za_raw"], awr_head, 0, 0, 1, 0)
    s2.cont(parsed["isotopes"][0]["zai_raw"], 1.0, 0, 0, 1, 0)
    s2.cont(rng["el_raw"], rng["eh_raw"], 1, 7, 0, rng["naps"])
    s2.cont(0.0, 0.0, ifg, 3, len(groups), 0)
    flat = [v for p in pairs for v in p]
    s2.listing((0.0, 0.0, len(pairs), 0, 2 * len(pairs)), flat)
    inexact, quant = [], {"capture": 0.0, "neutron": 0.0, "fission": 0.0, "competitive": 0.0}
    n_resonances = 0
    for (l, j), rs in groups.items():
        j_raw = rs[0]["raw"][1]
        s = channel_spin(l, j, spi)
        channels = [1, 0, 0.0, 0.0, 0.0, 0.0]
        for k, name in enumerate(cls[1:], start=2):
            channels += [k, l, s, 0.0, ap, ap]
        s2.listing((j_raw, 0.0, 0, 0, len(cls)), channels)
        per = 6 * (-(-(len(cls) + 1) // 6))
        rows = []
        for r in rs:
            e_raw, gg_raw, gn_raw, gf_raw = r["raw"][0], r["raw"][4], r["raw"][3], r["raw"][5]
            widths = {"capture": dec(gg_raw), "neutron": dec(gn_raw), "fission": dec(gf_raw),
                      "competitive": excess[id(r)]}
            if ifg == 0:
                fields = [gg_raw, gn_raw]
                if any_fission:
                    fields.append(gf_raw)
                if any_excess:
                    cell, exact = fmt_field(widths["competitive"])
                    fields.append(cell)
                    if not exact:
                        inexact.append((e_raw.strip(), "competitive"))
            else:
                p_n = hard_sphere_p(l, K_WAVE * float(compound - 1) / float(compound) * math.sqrt(abs(float(dec(e_raw))))
                                    * float(dec(ap)))
                fields = []
                for name in cls:
                    w = float(widths[name])
                    p = p_n if name == "neutron" else 1.0
                    amp = math.sqrt(w / (2.0 * p)) if w > 0.0 else 0.0
                    cell, _ = fmt_field(amp)
                    fields.append(cell)
                    if amp > 0.0:
                        decoded = 2.0 * p * float(dec(cell)) ** 2
                        quant[name] = max(quant[name], rel_diff(decoded, w))
            row = [e_raw] + fields
            row += [0.0] * (per - len(row))
            rows.extend(row)
            n_resonances += 1
        # one six-word line per resonance (at most five channels), so the LIST head has L2 = N2 = NRS, as in the
        # ENDF/B-VIII.1 file
        assert per == 6, "more than five channels would need N2 != NRS"
        s2.listing((0.0, 0.0, 0, len(rs), len(rs)), rows)

    # MF=1/MT=451
    s1 = Section(MAT, 1, 451)
    notes = ["P139 G4 synthetic LRF=7 KRM=3 tape of a W-186 LRF=2 resolved range",
             f"IFG={ifg}: " + ("partial widths as listed" if ifg == 0 else "reduced-width amplitudes sqrt(Gamma/2P)"),
             "channels: capture MT=102, neutron MT=2" + (", fission MT=18" if any_fission else "")
             + (f", competitive MT={COMPETITIVE_MT}" if any_excess else "")]
    s1.cont(parsed["za_raw"], awr_head, 1, 0, 0, 1)
    s1.cont(0.0, 0.0, 0, 0, 0, 6)
    s1.cont(1.0, 2.0e7, 0, 0, 10, 8)
    s1.cont(0.0, 0.0, 0, 0, len(notes), 2)
    for line in notes:
        s1.text(line)
    nc1 = len(s1.lines) + 2
    s1.cont(0.0, 0.0, 1, 451, nc1, 0)
    s1.cont(0.0, 0.0, 2, 151, len(s2.lines), 0)

    tpid = ("synthetic P139 LRF=7 tape").ljust(66) + f"{1:4d}{0:2d}{0:3d}{0:5d}"
    out = [tpid] + s1.lines + [s1.send(), blank_cont(MAT, 0, 0, 0)] + s2.lines + [s2.send(), blank_cont(MAT, 0, 0, 0),
                                                                                 blank_cont(0, 0, 0, 0),
                                                                                 blank_cont(-1, 0, 0, 0)]
    info = {"ifg": ifg, "spin_groups": [{"l": l, "j": j, "resonances": len(rs)} for (l, j), rs in groups.items()],
            "resonances": n_resonances, "channels": cls, "pairs": [p[9] for p in pairs],
            "fission_pair": any_fission, "competitive_pair": any_excess, "awri": awri_raw.strip(),
            "head_awr": awr_head.strip(), "inexact_fields": inexact[:10], "inexact_field_count": len(inexact),
            "max_quantisation_error": quant if ifg == 1 else None}
    return "\n".join(out) + "\n", info


def validate_tape(text):
    """A Python port of what the solver's parse_sections / parse_evaluations require of a one-material tape: every
    record 80 columns with numeric MAT/MF/MT, sections ended by SEND, MF=1/MT=451 with four control records, an MF=2
    section that parses to its last record, and MF=2 and MF=1 agreeing on ZA and AWR. Raises ValueError."""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    sections, active, seen = {}, None, set()
    for n, line in enumerate(lines, start=1):
        if len(line) != 80 or not line.isascii():
            raise ValueError(f"line {n}: record is not 80 ASCII columns")
        mat, mf, mt = int(line[66:70]), int(line[70:72]), int(line[72:75])
        if active and (mat, mf, mt) == active:
            sections[active].append(line)
        elif active and mat == active[0] and mf == active[1] and mt == 0:
            active = None
        elif active:
            raise ValueError(f"line {n}: section changed without SEND")
        elif mat > 0 and mf > 0 and mt > 0:
            if (mat, mf, mt) in seen:
                raise ValueError(f"line {n}: duplicate section")
            seen.add((mat, mf, mt))
            active = (mat, mf, mt)
            sections[active] = [line]
    if active:
        raise ValueError("section ended without SEND")
    mf1, mf2 = sections.get((MAT, 1, 451)), sections.get((MAT, 2, 151))
    if not mf1 or len(mf1) < 4 or not mf2:
        raise ValueError("missing MF=1/MT=451 (>= 4 records) or MF=2/MT=151")
    h1, h2 = base.fields(mf1[0]), base.fields(mf2[0])
    if base.endf_float(h1[0]) != base.endf_float(h2[0]):
        raise ValueError("MF=1 and MF=2 ZA disagree")
    if abs(base.endf_float(h1[1]) - base.endf_float(h2[1])) > 1e-7 * max(abs(base.endf_float(h1[1])), 1.0):
        raise ValueError("MF=1 and MF=2 AWR disagree")
    inc = base.fields(mf1[2])
    if base.endf_int(inc[4]) != 10:
        raise ValueError("MF=1 NSUB is not 10 (neutron)")
    for sec in (mf1, mf2):
        for k, line in enumerate(sec, start=1):
            if int(line[75:80]) != k:
                raise ValueError(f"sequence number {line[75:80]!r} out of order at record {k}")
    parsed = parse_mf2_any(mf2)
    if not parsed["consumed"]:
        raise ValueError("MF=2/MT=151 has unconsumed records")
    return parsed


# ---------------------------------------------------------------- runs, specs and the model


def tag_of(t):
    return f"{t}".replace(".", "p")


def make_spec(label, thickness, resonance_path, workdir):
    spec = base.build_w186_spec(thickness, resonance_path, sha256_file(resonance_path))
    spec["title"] = f"P139 {label}: W-186 foil {thickness} cm, geometry self-shielding"
    path = workdir / f"{label}_w186_foil_{tag_of(thickness)}.json"
    path.write_text(json.dumps(spec, indent=1) + "\n")
    return path


def data_files(nuclear_data):
    nd = Path(nuclear_data)
    return {
        "endfb_viii1": nd / "endfb-viii.1-fns-arm/pristine/W186.endf",
        "endfb_viii0": nd / "endfb-viii.0-n-test/n_7443_74-W-186.dat",
        "tendl_2025": nd / "tendl-2025/files/n/n-W186.tendl",
    }


def group_bounds():
    bounds = json.loads(DEMO_SPEC.read_text())["spectrum"]["boundaries_eV"]
    if any(b <= a for a, b in zip(bounds, bounds[1:])):
        raise ValueError("group boundaries from shielding_demo.json are not ascending")
    return bounds


def atoms_per_gram_w186():
    """N_A / (AWR_decay m_n) with the decay-library AWR the solver multiplies by the neutron mass in u."""
    base_spec = json.loads(FNS_SPEC.read_text())
    decay_path = base_spec["decay"]["primary"]
    decay_path = decay_path if os.path.isabs(decay_path) else str(ROOT / decay_path)
    awr = base.decay_awr(decay_path, W186_ZA)
    return base.AVOGADRO / (awr * base.NEUTRON_MASS_AMU), awr, decay_path


def number_density(allow_fallback_awr=None):
    try:
        apg, awr, path = atoms_per_gram_w186()
        source = path
    except (OSError, ValueError):
        if allow_fallback_awr is None:
            raise
        awr = allow_fallback_awr
        apg = base.AVOGADRO / (awr * base.NEUTRON_MASS_AMU)
        source = "fallback: evaluation HEAD AWR (decay library not present in this checkout)"
    return apg * base.DENSITY_W_G_CM3, {"decay_awr": awr, "source": source}


def ledger_factors(result):
    geo = base.shielding_ledger(result).get("geometry")
    if not isinstance(geo, dict):
        raise ValueError("ledger.shielding has no geometry block")
    return {row["group"]: row for row in geo["factors"]["W186"]}


def table_diff(a, b):
    """(worst relative factor difference over common groups, group of the worst, groups only in a, groups only in b,
    resonance-count mismatches, group-bound mismatches)."""
    worst, where = 0.0, None
    counts, bounds = [], []
    for g in sorted(set(a) & set(b)):
        err = rel_diff(a[g]["factor"], b[g]["factor"])
        if err > worst:
            worst, where = err, g
        if a[g]["resonances"] != b[g]["resonances"]:
            counts.append({"group": g, "a": a[g]["resonances"], "b": b[g]["resonances"]})
        if a[g]["energy_min_eV"] != b[g]["energy_min_eV"] or a[g]["energy_max_eV"] != b[g]["energy_max_eV"]:
            bounds.append(g)
    return worst, where, sorted(set(a) - set(b)), sorted(set(b) - set(a)), counts, bounds


GUARDED = (RuntimeError, ValueError, KeyError, IndexError, OSError, subprocess.TimeoutExpired, json.JSONDecodeError)


# ---------------------------------------------------------------- G2


def tendl_specs(files, workdir):
    return [(f"tendl_w186_foil_{tag_of(t)}", make_spec("tendl", t, files["tendl_2025"], workdir))
            for t in FOIL_THICKNESS_CM]


def gate_g2(runner, reference, candidate, files, workdir):
    out = {"specs": {}}
    cases = [(FNS_SPEC.stem, FNS_SPEC), (DEMO_SPEC.stem, DEMO_SPEC)] + tendl_specs(files, workdir)
    ok = True
    for name, spec in cases:
        entry = {"spec": str(spec), "spec_sha256": sha256_file(spec)}
        try:
            raw_ref, ref = runner.run("reference", reference, spec, name)
            raw_cand, cand = runner.run("candidate", candidate, spec, name)
            parsed_equal = {k: v for k, v in ref.items() if k != "ms"} == {k: v for k, v in cand.items() if k != "ms"}
            masked_equal = base.MS_FIELD.sub(b'"ms":0', raw_ref) == base.MS_FIELD.sub(b'"ms":0', raw_cand)
            entry.update({"raw_bytes_identical": raw_ref == raw_cand, "parsed_equal_without_ms": parsed_equal,
                          "bytes_equal_after_masking_ms": masked_equal, "reference_ms": ref.get("ms"),
                          "candidate_ms": cand.get("ms"),
                          "reference_output_sha256": hashlib.sha256(raw_ref).hexdigest(),
                          "candidate_output_sha256": hashlib.sha256(raw_cand).hexdigest()})
            if not parsed_equal:
                entry["first_differences"] = [base.fmt_path(p) for p in base.diff_leaves(
                    {k: v for k, v in ref.items() if k != "ms"}, {k: v for k, v in cand.items() if k != "ms"})[:10]]
            entry["pass"] = bool(parsed_equal and masked_equal)
        except GUARDED as error:
            entry["error"] = f"{type(error).__name__}: {error}"
            entry["pass"] = False
        out["specs"][name] = entry
        ok = ok and entry["pass"]
    out["pass"] = ok
    return out


# ---------------------------------------------------------------- G3


def monotone_block(factors):
    thin, thick = FOIL_THICKNESS_CM
    mono = {"pass": False}
    if thin in factors and thick in factors:
        a, b = factors[thin], factors[thick]
        common = sorted(set(a) & set(b))
        violations = [{"group": g, "thin": a[g], "thick": b[g]} for g in common if b[g] > a[g] + 1e-15]
        mono.update({"groups_compared": len(common), "same_group_set": set(a) == set(b),
                     "violations": violations[:10], "strictly_lower_in": sum(1 for g in common if b[g] < a[g]),
                     "min_thin": min(a.values()), "min_thick": min(b.values())})
        mono["pass"] = bool(common) and not violations and mono["same_group_set"] and mono["strictly_lower_in"] > 0
    return mono


def gate_g3(runner, candidate, files, workdir, awri_source):
    path = files["endfb_viii1"]
    sha = sha256_file(path)
    parsed = parse_file_any(path)
    rows, summary = all_resonances(parsed, awri_source)
    lines = usable(rows)
    alt_source = "head" if awri_source == "pair" else "pair"
    alt_rows, _ = all_resonances(parsed, alt_source)
    alt_lines = usable(alt_rows)
    rml = next(r["rml"] for iso in parsed["isotopes"] for r in iso["ranges"] if r.get("lrf") == 7)
    ent = entrance_pair(rml)
    out = {"file": {"path": str(path), "sha256": sha, "head_awr": parsed["awr"]},
           "ranges": summary, "ifg": rml["ifg"], "krm": rml["krm"],
           "particle_pairs": [{"mt": p["mt"], "pnt": p["pnt"], "ma": p["ma"], "mb": p["mb"], "ia": p["ia"],
                               "ib": p["ib"], "class": pair_class(p)} for p in rml["pairs"]],
           "spin_groups": [{"j": g["aj"], "channels": [pair_class(rml["pairs"][c["pair"]]) for c in g["channels"]],
                            "resonances": len(g["resonances"])} for g in rml["groups"]],
           "target_spin": usable(rows)[0]["SPI"], "awri_source": awri_source,
           "awri_used": {"pair_MB_over_MA": ent["mb"] / ent["ma"], "head": parsed["awr"]},
           "resonances_total": len(rows), "resonances_usable": len(lines),
           "resonances_skipped": {"non_positive_energy": sum(1 for r in rows if r["E"] <= 0.0),
                                  "outside_range": sum(1 for r in rows if r["E"] > 0.0 and r["outside_range"]),
                                  "no_neutron_or_capture_width": sum(1 for r in rows if r["E"] > 0.0
                                                                     and not r["outside_range"]
                                                                     and not r["usable"])},
           "energy_range_eV": [min(r["E"] for r in lines), max(r["E"] for r in lines)]}
    bounds = group_bounds()
    n_indep, material = number_density()
    out["material"] = material
    per, factors, ok = {}, {}, True
    for t in FOIL_THICKNESS_CM:
        spec = make_spec("viii1", t, path, workdir)
        entry = {"spec": str(spec), "spec_sha256": sha256_file(spec)}
        try:
            _, result = runner.run("candidate", candidate, spec, f"viii1_w186_foil_{tag_of(t)}")
            cmp_, factors[t], got = base.compare_geometry(result, t, lines, bounds, n_indep, sha)
            entry.update(cmp_)
            alt = base.group_factors(alt_lines, bounds, n_indep, 1.5 * t)
            entry[f"alternative_awri_{alt_source}_worst_relative_error"] = max(
                (rel_diff(got[g]["factor"], alt[g][0]) for g in set(got) & set(alt)), default=None)
        except GUARDED as error:
            entry["error"] = f"{type(error).__name__}: {error}"
            entry["pass"] = False
        per[str(t)] = entry
        ok = ok and entry["pass"]
    out["thicknesses"] = per
    out["monotonic_with_thickness"] = monotone_block(factors)
    out["pass"] = bool(ok and out["monotonic_with_thickness"]["pass"])
    out["_factors"] = {str(t): f for t, f in factors.items()}
    return out


# ---------------------------------------------------------------- G4


def gate_g4(runner, candidate, files, workdir, head="evaluation"):
    out = {"source": {"path": str(files["tendl_2025"]), "sha256": sha256_file(files["tendl_2025"])}}
    parsed = parse_file_any(files["tendl_2025"])
    legacy, _ = all_resonances(parsed)
    bounds = group_bounds()
    n_indep, material = number_density(parsed["awr"])
    out["material"] = material
    ok = True
    out["files"] = {}
    for ifg, tol in ((0, IFG0_TOL), (1, IFG1_TOL)):
        label = f"syn_ifg{ifg}"
        text, info = build_synthetic(parsed, ifg, head)
        validate_tape(text)
        syn_path = workdir / f"p139_synthetic_w186_ifg{ifg}.endf"
        syn_path.write_text(text)
        sha = sha256_file(syn_path)
        block = {"path": str(syn_path), "sha256": sha, "bytes": syn_path.stat().st_size, "tolerance": tol,
                 "build": info, "thicknesses": {}}
        for t in FOIL_THICKNESS_CM:
            entry = {}
            try:
                spec = make_spec(label, t, syn_path, workdir)
                entry.update({"spec": str(spec), "spec_sha256": sha256_file(spec)})
                _, syn = runner.run("candidate", candidate, spec, f"{label}_w186_foil_{tag_of(t)}")
                tendl_spec = make_spec("tendl", t, files["tendl_2025"], workdir)
                _, orig = runner.run("candidate", candidate, tendl_spec, f"tendl_w186_foil_{tag_of(t)}")
                a, b = ledger_factors(syn), ledger_factors(orig)
                worst, where, only_syn, only_orig, counts, bnds = table_diff(a, b)
                mine = base.group_factors(usable(legacy), bounds, n_indep, 1.5 * t)
                py_worst = max((rel_diff(b[g]["factor"], mine[g][0]) for g in set(b) & set(mine)), default=None)
                led = base.shielding_ledger(syn)
                problems = []
                if led.get("resonance_files_sha256") != [sha]:
                    problems.append("resonance_files_sha256 is not the synthetic file's hash")
                if only_syn or only_orig:
                    problems.append(f"group sets differ: synthetic-only {only_syn[:10]}, LRF=2-only {only_orig[:10]}")
                if counts:
                    problems.append("resonance counts differ")
                if bnds:
                    problems.append("group energy bounds differ")
                if not a:
                    problems.append("no groups")
                if worst > tol:
                    problems.append(f"factor mismatch {worst:.3e} > {tol:g} in group {where}")
                entry.update({"groups": len(a), "groups_lrf2": len(b), "worst_relative_difference": worst,
                              "worst_group": where, "resonance_count_mismatches": counts[:10],
                              "group_bound_mismatches": bnds[:10],
                              "python_model_vs_candidate_lrf2_worst": py_worst,
                              "problems": problems, "pass": not problems})
            except GUARDED as error:
                entry["error"] = f"{type(error).__name__}: {error}"
                entry["pass"] = False
            block["thicknesses"][str(t)] = entry
            ok = ok and entry["pass"]
        out["files"][f"ifg{ifg}"] = block
    out["pass"] = ok
    return out


# ---------------------------------------------------------------- G5


def gate_g5(runner, candidate, files, workdir, g3, include_viii0=True):
    out = {"report_only": True, "pass": None, "thicknesses": {}}
    lines_out = []
    viii0 = {}
    if include_viii0:
        for t in FOIL_THICKNESS_CM:
            try:
                spec = make_spec("viii0", t, files["endfb_viii0"], workdir)
                _, res = runner.run("candidate", candidate, spec, f"viii0_w186_foil_{tag_of(t)}")
                viii0[t] = ledger_factors(res)
            except GUARDED as error:
                out.setdefault("viii0_errors", {})[str(t)] = f"{type(error).__name__}: {error}"
    for t in FOIL_THICKNESS_CM:
        entry = {}
        try:
            _, r1 = runner.run("candidate", candidate, make_spec("viii1", t, files["endfb_viii1"], workdir),
                               f"viii1_w186_foil_{tag_of(t)}")
            _, r2 = runner.run("candidate", candidate, make_spec("tendl", t, files["tendl_2025"], workdir),
                               f"tendl_w186_foil_{tag_of(t)}")
            a, b = ledger_factors(r1), ledger_factors(r2)
            c = viii0.get(t)
            groups = sorted(set(a) | set(b) | set(c or ()))
            rows = []
            for g in groups:
                row = {"group": g, "factor_viii1": a[g]["factor"] if g in a else None,
                       "factor_tendl2025": b[g]["factor"] if g in b else None,
                       "factor_viii0": c[g]["factor"] if c and g in c else None}
                src = a.get(g) or b.get(g) or (c or {}).get(g)
                row["energy_min_eV"], row["energy_max_eV"] = src["energy_min_eV"], src["energy_max_eV"]
                row["resonances_viii1"] = a[g]["resonances"] if g in a else None
                row["resonances_tendl2025"] = b[g]["resonances"] if g in b else None
                if g in a and g in b:
                    row["relative_difference_viii1_vs_tendl"] = (a[g]["factor"] - b[g]["factor"]) / b[g]["factor"]
                rows.append(row)
            diffs = [abs(r["relative_difference_viii1_vs_tendl"]) for r in rows
                     if "relative_difference_viii1_vs_tendl" in r]
            entry["summary"] = {
                "groups_viii1": len(a), "groups_tendl2025": len(b), "groups_common": len(diffs),
                "groups_viii1_only": sum(1 for r in rows if r["factor_tendl2025"] is None and r["factor_viii1"] is not None),
                "groups_tendl2025_only": sum(1 for r in rows if r["factor_viii1"] is None and r["factor_tendl2025"] is not None),
                "median_abs_relative_difference": statistics.median(diffs) if diffs else None,
                "max_abs_relative_difference": max(diffs) if diffs else None,
                "mean_factor_viii1": statistics.fmean(v["factor"] for v in a.values()) if a else None,
                "mean_factor_tendl2025": statistics.fmean(v["factor"] for v in b.values()) if b else None,
                "min_factor_viii1": min(v["factor"] for v in a.values()) if a else None,
                "min_factor_tendl2025": min(v["factor"] for v in b.values()) if b else None}
            if c:
                entry["summary"]["groups_viii0"] = len(c)
                entry["summary"]["min_factor_viii0"] = min(v["factor"] for v in c.values())
            entry["table"] = rows
            lines_out.append(f"# foil {t} cm: group, E_min, E_max, VIII.1, TENDL-2025, VIII.0, (VIII.1-TENDL)/TENDL")
            for r in rows:
                fmt = lambda v: "      -      " if v is None else f"{v:13.6e}"
                rd = r.get("relative_difference_viii1_vs_tendl")
                lines_out.append(f"{r['group']:5d} {r['energy_min_eV']:12.5e} {r['energy_max_eV']:12.5e} "
                                 f"{fmt(r['factor_viii1'])} {fmt(r['factor_tendl2025'])} {fmt(r['factor_viii0'])} "
                                 + ("      -     " if rd is None else f"{rd:+12.5e}"))
        except GUARDED as error:
            entry["error"] = f"{type(error).__name__}: {error}"
        out["thicknesses"][str(t)] = entry
    table_path = workdir / "p139_g5_table.txt"
    table_path.write_text("\n".join(lines_out) + "\n")
    out["table_file"] = str(table_path)
    return out


# ---------------------------------------------------------------- self-check


def fission_variant(parsed, count=4):
    """A deep copy of a LRF=2 source in which `count` resonances per L-group get a fission width and a total that
    exceeds the component sum by a competitive remainder: a self-check input only."""
    import copy
    out = copy.deepcopy(parsed)
    rng = next(r for iso in out["isotopes"] for r in iso["ranges"] if r["lru"] == 1)
    for group in rng["groups"]:
        for r in [x for x in group["resonances"] if x["E"] > 0][:count]:
            gf = dec(r["raw"][3]) * Decimal("0.125") + Decimal("0.0123456")
            gt = dec(r["raw"][3]) + dec(r["raw"][4]) + gf + Decimal("0.0004321")
            r["raw"][5], r["raw"][2] = fmt_field(gf)[0], fmt_field(gt)[0]
            r["GF"], r["GT"] = float(dec(r["raw"][5])), float(dec(r["raw"][2]))
    return out




def self_check(workdir, nuclear_data):
    files = data_files(nuclear_data)
    problems = []
    workdir.mkdir(parents=True, exist_ok=True)

    def say(msg):
        print(msg)

    def need(cond, msg):
        if not cond:
            problems.append(msg)
            say(f"  FAIL: {msg}")

    # 1. penetrability recurrence against the closed forms
    pc = penetrability_closed_form_check()
    say(f"penetrability: recurrence vs closed forms (l=0..4, 7 rho values), worst relative difference {pc:.2e}")
    need(pc < 1e-12, "hard-sphere penetrability recurrence disagrees with the closed forms")
    p5 = hard_sphere_p(5, 0.7)
    need(0.0 < p5 < hard_sphere_p(4, 0.7), "P_5(0.7) is not below P_4(0.7)")

    # 2. field formatter
    for text in ("1.883000+1", "-5.319459+2", "0.000000+0", "5.344730-2", "1.0000000-12"):
        x = dec(text)
        cell, exact = fmt_field(x)
        need(exact and dec(cell) == x and len(cell) == 11, f"fmt_field does not round-trip {text!r} -> {cell!r}")
    cell, exact = fmt_field(0.158113883)
    need(len(cell) == 11 and abs(float(dec(cell)) - 0.158113883) < 5e-10, "fmt_field(0.158113883)")

    # 3. ENDF/B-VIII.1 (LRF=7)
    for key in ("endfb_viii1", "endfb_viii0"):
        path = files[key]
        parsed = parse_file_any(path)
        rows, summary = all_resonances(parsed)
        rml = next(r["rml"] for iso in parsed["isotopes"] for r in iso["ranges"] if r.get("lrf") == 7)
        ent = entrance_pair(rml)
        us = usable(rows)
        say(f"{key}: {path.name} sha256 {sha256_file(path)[:16]}  HEAD AWR {parsed['awr']}  ranges {summary}")
        say(f"  IFG={rml['ifg']} KRM={rml['krm']} NJS={len(rml['groups'])} pairs "
            f"{[(p['mt'], p['pnt'], pair_class(p)) for p in rml['pairs']]}  entrance MB/MA={ent['mb'] / ent['ma']:.6f} "
            f"target spin {usable(rows)[0]['SPI']}")
        for gi, g in enumerate(rml["groups"]):
            say(f"  spin group {gi}: J={g['aj']} channels {[pair_class(rml['pairs'][c['pair']]) for c in g['channels']]} "
                f"L={[c['l'] for c in g['channels']]} resonances {len(g['resonances'])}")
        say(f"  resonances {len(rows)}, usable {len(us)}, E<=0 {sum(1 for r in rows if r['E'] <= 0)}, "
            f"E range {min(r['E'] for r in us):.4g}..{max(r['E'] for r in us):.6g} eV")
        need(rml["ifg"] == 0 and rml["krm"] == 3, f"{key}: expected IFG=0 KRM=3")
        need(bool(us), f"{key}: no usable resonances")
        # IFG=0 invariants of the derivation
        for r in rows:
            need(r["GAMMA"] >= r["GN"] + r["GG"] - 1e-300, f"{key}: Gamma below Gamma_n + Gamma_gamma")
            if len(problems) > 5:
                break

    # 4. TENDL (LRF=2): own parser against check_p129's
    tendl = files["tendl_2025"]
    parsed = parse_file_any(tendl)
    legacy, lsum = all_resonances(parsed)
    old = base.resonance_lines(base.parse_mf2_file(tendl), W186_ZA)
    us = usable(legacy)
    same = len(us) == len(old) and all(
        all(rel_diff(a[k], b[k]) <= 1e-15 for k in ("E", "J", "SPI", "AWRI", "GN", "GG", "GAMMA"))
        for a, b in zip(us, old))
    say(f"tendl_2025: {tendl.name} sha256 {sha256_file(tendl)[:16]}  ranges {lsum}  resonances {len(legacy)}, usable "
        f"{len(us)}; check_p129 reader gives {len(old)} usable, identical: {same}")
    need(same, "the LRF=1-3 resonances differ between this reader and check_p129's")

    # 5. synthetic tapes
    bounds = group_bounds()
    n_indep, material = number_density(parsed["awr"])
    say(f"number density N = {n_indep:.6e} /cm3 ({material['source']})")
    for ifg, tol in ((0, IFG0_TOL), (1, IFG1_TOL)):
        text, info = build_synthetic(parsed, ifg)
        path = workdir / f"p139_synthetic_w186_ifg{ifg}.endf"
        path.write_text(text)
        back = validate_tape(text)
        rml = next(r["rml"] for iso in back["isotopes"] for r in iso["ranges"] if r.get("lrf") == 7)
        syn, _ = all_resonances(back, "pair")
        say(f"synthetic IFG={ifg}: {path} sha256 {sha256_file(path)[:16]} {len(text.splitlines())} records; "
            f"spin groups {info['spin_groups']}; pairs MT {info['pairs']}; channels {info['channels']}; "
            f"competitive channel {info['competitive_pair']}, fission {info['fission_pair']}; "
            f"competitive fields not exactly representable {info['inexact_field_count']}")
        need(rml["ifg"] == ifg and rml["krm"] == 3, f"IFG={ifg}: header read back wrong")
        need(len(syn) == len(legacy), f"IFG={ifg}: {len(syn)} resonances read back, expected {len(legacy)}")
        # match resonance-by-resonance on (E, J)
        key = lambda r: (r["E"], r["J"])
        a_sorted, b_sorted = sorted(legacy, key=key), sorted(syn, key=key)
        need(len({key(r) for r in legacy}) == len(legacy), "duplicate (E, J) in the LRF=2 source")
        worst = {k: 0.0 for k in ("E", "g", "GN", "GG", "GAMMA")}
        flag_mismatch = 0
        for x, y in zip(a_sorted, b_sorted):
            if key(x) != key(y):
                need(False, f"IFG={ifg}: resonance ordering mismatch {key(x)} vs {key(y)}")
                break
            worst["E"] = max(worst["E"], rel_diff(x["E"], y["E"]))
            worst["g"] = max(worst["g"], rel_diff(statistical(x), statistical(y)))
            for k in ("GN", "GG", "GAMMA"):
                worst[k] = max(worst[k], rel_diff(x[k], y[k]))
            flag_mismatch += x["usable"] != y["usable"]
        say(f"  per resonance, worst relative difference to the LRF=2 original: "
            + ", ".join(f"{k} {v:.2e}" for k, v in worst.items()) + f"; usable-flag mismatches {flag_mismatch}")
        if info["max_quantisation_error"]:
            say(f"  IFG=1 field quantisation, worst relative error of the decoded width per channel: "
                + ", ".join(f"{k} {v:.2e}" for k, v in info["max_quantisation_error"].items()))
        need(all(v <= tol for v in worst.values()), f"IFG={ifg}: per-resonance difference above {tol:g}")
        need(flag_mismatch == 0, f"IFG={ifg}: usable flags differ")
        for t in FOIL_THICKNESS_CM:
            ref = base.group_factors(usable(legacy), bounds, n_indep, 1.5 * t)
            got = base.group_factors(usable(syn), bounds, n_indep, 1.5 * t)
            w = max((rel_diff(ref[g][0], got[g][0]) for g in ref), default=1.0)
            need(set(ref) == set(got), f"IFG={ifg} foil {t}: group sets differ")
            say(f"  foil {t} cm: {len(ref)} groups, worst relative group-factor difference {w:.2e} (limit {tol:g})")
            need(w <= tol, f"IFG={ifg} foil {t}: group-factor difference {w:.3e} above {tol:g}")
        # the same lines through the other AWRI convention are different by construction (informational)
        alt, _ = all_resonances(back, "head")
        say(f"  head-AWR convention would use AWRI {alt[0]['AWRI']} against the pair ratio {syn[0]['AWRI']}")
    # 6. a variant of the source with fission widths, to exercise the fission-pair path W-186 does not have
    fissile = fission_variant(parsed)
    flegacy, _ = all_resonances(fissile)
    for ifg, tol in ((0, IFG0_TOL), (1, IFG1_TOL)):
        text, info = build_synthetic(fissile, ifg)
        back = validate_tape(text)
        syn, _ = all_resonances(back, "pair")
        key = lambda r: (r["E"], r["J"])
        worst = max((max(rel_diff(x[k], y[k]) for k in ("GN", "GG", "GAMMA"))
                     for x, y in zip(sorted(flegacy, key=key), sorted(syn, key=key))), default=1.0)
        say(f"fission variant IFG={ifg}: channels {info['channels']} pairs MT {info['pairs']}, "
            f"{sum(1 for ch in fissile['isotopes'][0]['ranges'][0]['groups'] for r in ch['resonances'] if r['GF'] > 0)} "
            f"resonances with GF>0, worst per-resonance relative difference {worst:.2e}")
        need(info["fission_pair"] and "fission" in info["channels"] and len(syn) == len(flegacy) and worst <= tol,
             f"fission variant IFG={ifg}")
        need([x["usable"] for x in sorted(flegacy, key=key)] == [y["usable"] for y in sorted(syn, key=key)],
             f"fission variant IFG={ifg}: usable flags differ")
    say("SELF-CHECK " + ("PASS" if not problems else f"FAIL ({len(problems)} problem(s))"))
    return 0 if not problems else 1


# ---------------------------------------------------------------- driver


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reference", help="release binary of the base commit")
    ap.add_argument("--candidate", help="release binary of the implementation commit")
    ap.add_argument("--workdir", default="target/p139-work")
    ap.add_argument("--out", default="results/p139_lrf7.json")
    ap.add_argument("--gates", default="G2,G3,G4,G5", help="comma-separated subset; the verdict is INCOMPLETE "
                    "unless G2, G3 and G4 all run")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S, help="seconds per run")
    ap.add_argument("--nuclear-data", default=str(Path.home() / "nuclear-data"))
    ap.add_argument("--awri-source", choices=("pair", "head"), default="pair",
                    help="AWRI the independent G3 model uses for k: the entrance pair's M_B/M_A (default) or the "
                    "MF=2 HEAD AWR")
    ap.add_argument("--synthetic-head", choices=("evaluation", "group"), default="evaluation",
                    help="AWR written in the HEAD of the synthetic G4 tapes: the TENDL file's HEAD AWR (default; the "
                    "entrance pair M_B/M_A carries the L-group AWRI) or the L-group AWRI")
    ap.add_argument("--no-viii0", action="store_true", help="skip the report-only ENDF/B-VIII.0 runs of G5")
    ap.add_argument("--self-check", action="store_true", help="run no binary: parse, transcribe and re-read")
    args = ap.parse_args(argv)

    workdir = Path(args.workdir)
    workdir = workdir if workdir.is_absolute() else ROOT / workdir
    if args.self_check:
        return self_check(workdir, args.nuclear_data)
    if not args.reference or not args.candidate:
        print("--reference and --candidate are required (or use --self-check)", file=sys.stderr)
        return 2
    gates = {g.strip().upper() for g in args.gates.split(",") if g.strip()}
    unknown = gates - {"G2", "G3", "G4", "G5"}
    if unknown:
        print(f"unknown gate(s): {sorted(unknown)}", file=sys.stderr)
        return 2
    reference, candidate = Path(args.reference).resolve(), Path(args.candidate).resolve()
    for label, binary in (("reference", reference), ("candidate", candidate)):
        if not binary.is_file():
            print(f"{label} binary {binary} does not exist", file=sys.stderr)
            return 2
    files = data_files(args.nuclear_data)
    for key, path in files.items():
        if not path.is_file():
            print(f"data file {key} {path} does not exist", file=sys.stderr)
            return 2
    workdir.mkdir(parents=True, exist_ok=True)

    record_ = {
        "protocol": "ACTINV-P139", "protocol_path": str(PROTOCOL.relative_to(ROOT)),
        "protocol_sha256": sha256_file(PROTOCOL),
        "reference": {"path": str(reference), "sha256": sha256_file(reference)},
        "candidate": {"path": str(candidate), "sha256": sha256_file(candidate)},
        "checker_sha256": sha256_file(__file__),
        "specs": {"shielding_demo": sha256_file(DEMO_SPEC), "fns_fe_5min": sha256_file(FNS_SPEC)},
        "data_files": {k: {"path": str(p), "sha256": sha256_file(p), "bytes": p.stat().st_size}
                       for k, p in files.items()},
        "external_gates": {"G0": "protocol hash committed before evidence: recorded by the coordinator",
                           "G1": "fmt, clippy -D warnings, workspace tests: recorded by the coordinator",
                           "G6": "CI green and run-output baselines unchanged: recorded by the coordinator"},
        "awri_source": args.awri_source, "synthetic_head": args.synthetic_head, "timeout_s": args.timeout, "gates_run": sorted(gates), "gates": {},
    }
    runner = base.Runner(workdir, args.timeout)
    g3_result = None

    def guarded(fn):
        try:
            return fn()
        except GUARDED + (NotImplementedError,) as error:
            return {"error": f"{type(error).__name__}: {error}", "pass": False}

    if "G2" in gates:
        record_["gates"]["G2"] = guarded(lambda: gate_g2(runner, reference, candidate, files, workdir))
    if "G3" in gates:
        g3_result = guarded(lambda: gate_g3(runner, candidate, files, workdir, args.awri_source))
        record_["gates"]["G3"] = g3_result
    if "G4" in gates:
        record_["gates"]["G4"] = guarded(lambda: gate_g4(runner, candidate, files, workdir, args.synthetic_head))
    if "G5" in gates:
        record_["gates"]["G5"] = guarded(lambda: gate_g5(runner, candidate, files, workdir, g3_result,
                                                         not args.no_viii0))
    if g3_result is not None:
        g3_result.pop("_factors", None)
    g4_files = record_["gates"].get("G4", {}).get("files", {})
    for key, block in g4_files.items():
        record_["data_files"][f"synthetic_{key}"] = {"path": block["path"], "sha256": block["sha256"],
                                                     "bytes": block["bytes"]}

    required = ("G2", "G3", "G4")
    if not all(g in gates for g in required):
        verdict = "INCOMPLETE"
    elif all(record_["gates"][g].get("pass") is True for g in required):
        verdict = "PASS"
    else:
        verdict = "FAIL"
    record_["verdict"] = verdict
    out = Path(args.out)
    out = out if out.is_absolute() else ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record_, indent=2, sort_keys=True) + "\n")
    for g in sorted(record_["gates"]):
        gate = record_["gates"][g]
        if g == "G5":
            sums = {t: v.get("summary") for t, v in gate.get("thicknesses", {}).items() if v.get("summary")}
            parts = [f"{t} cm {x['groups_common']} common groups, max |rel diff| {x['max_abs_relative_difference']:.3g}"
                     for t, x in sums.items() if x.get("max_abs_relative_difference") is not None]
            text = "REPORT (" + ("; ".join(parts) if parts else gate.get("error", "no data")) + ")"
        else:
            text = "PASS" if gate.get("pass") is True else "FAIL" + (f" ({gate['error']})" if "error" in gate else "")
        print(f"P139 {g}: {text}")
    print(f"P139: {verdict} ({out})")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
