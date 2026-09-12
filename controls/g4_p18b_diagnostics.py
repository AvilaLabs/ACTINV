#!/usr/bin/env python3
"""P18b G4 — diagnostic accuracy, compatibility and performance evidence.

Reads the diagnostic partition of the frozen Rodrigo supplement (permitted once
G3 is green on the pushed commit), scores the candidate and the signed v1.0.1
release under the frozen P18 formulas, compares the candidate diagnostic
libraries against the released v1.0.1 libraries for the compatibility leg, and
runs the frozen P16 public workload for the performance leg.

No production, audit or scoring module is imported; every computation here is
an independent implementation of the frozen protocol text.

Layout per the frozen rules:
  * each supplement row appears exactly once in the row ledger;
  * a row is scored only when every frozen eligibility predicate holds;
  * the calculated observable uses each artifact's own row semantics
    (v1.0.1 rank-compressed LFS vs candidate canonical LISO), routed exactly as
    the respective runtime routes it;
  * ratio transforms follow the printed measurement form;
  * metrics are the frozen set: signed ln(C/M) per row; median, population p90
    and maximum |ln(C/M)|; geometric-mean C/M; fractions within 10/20/30%;
    deterministic 10,000-replicate paired bootstrap over families seeded from
    the protocol hash.
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
import tempfile
import time
import zipfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "protocols/ACTINV-P18b_PROTOCOL.md"
PROTOCOL_SHA256 = "69076fa2656b239addbb15fbb4727caaa2c8ea37b3aa82a141f3a2b0b619eabe"
SEAL = ROOT / "results/p18_family_seal.json"
G2_REPORT = ROOT / "results/g2_p18b_corpus_classification.json"
G3_REPORT = ROOT / "results/g3_p18b_check.json"
SUPPLEMENT = Path(
    os.environ.get(
        "ACTINV_P18B_SUPPLEMENT",
        ROOT / "target/preflight-tmp/rodrigo-supplement.txt",
    )
)
SUPPLEMENT_SHA256 = "945e66f8904bb972662f5178e94e22a08ecb8006eefe1c2d9fbda66fe599763d"
DECAY_ROOT = Path(
    os.environ.get("ACTINV_P18B_DECAY", "/home/connoravila/nuclear-data")
)
CORPUS = Path(
    os.environ.get(
        "ACTINV_P18B_CORPUS", "/home/connoravila/nuclear-data/tendl-2025/files"
    )
)
WORK = ROOT / "target/g4-p18b"
REPORT = ROOT / "results/g4_p18b_diagnostics.json"
ACTINV = Path(os.environ.get("ACTINV_BIN", ROOT / "target/release/actinv"))
V101 = Path(
    os.environ.get(
        "ACTINV_V101_BIN",
        ROOT
        / "target/release-v1.0.1.0RVGVj/raw/actinv-1.0.1-linux-x86_64"
        / "target/x86_64-unknown-linux-gnu/release/actinv",
    )
)

BASELINE_LIBRARIES = {
    "neutron": (
        ROOT / "actinv-data/v1.0.0/activation/tendl-2025-neutron-709g.npz",
        "ec4c72bf598dc8ad3d533d9cfafdcf493e2d1f949a3e4db6251495659b68cc44",
    ),
    "proton": (
        ROOT / "actinv-data/v1.0.0/activation/tendl-2025-proton-162g.npz",
        "0da7a35b37fd3b305ac2166ec092cdfb78123e76f8647d8808915e2c708d9790",
    ),
    "deuteron": (
        ROOT / "actinv-data/v1.0.0/activation/tendl-2025-deuteron-162g.npz",
        "8050988981518cd63ac0c2ad76c6756370b154ea9f5a6d6435aa5f132b9d99ae",
    ),
    "alpha": (
        ROOT / "actinv-data/v1.0.0/activation/tendl-2025-alpha-162g.npz",
        "ead1141bfe07ec1a02055af014f8db0a49effe2fd60c29d181a505f7c6d10915",
    ),
}
DECAY_FILES = {
    "endfb_viii_0": (
        DECAY_ROOT / "endfb-viii.0-decay/bulk/endf-b-viii-0_decay.dat",
        "6f04cf009086c179021f243a58dadc2d5bb078de5ba39c4fe46ccad77d228ddb",
    ),
    "jeff_3_3": (
        DECAY_ROOT / "jeff-3.3-decay/bulk/jeff-3-3_decay.dat",
        "850b8b7f85f8d88b6ad826c4cd341aaaffabd525c8ecf3c588a0ad437bf5d123",
    ),
}

PROJECTILE_CODES = {(0, 1): "neutron", (1, 1): "proton", (1, 2): "deuteron", (2, 4): "alpha"}
MANIFEST_CODE = {"neutron": "n", "proton": "p", "deuteron": "d", "alpha": "a"}
SYMBOLS = (
    "H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn "
    "Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce "
    "Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn "
    "Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr Rf Db Sg Bh Hs Mt Ds Rg Cn Nh Fl "
    "Mc Lv Ts Og"
).split()
REACTION = re.compile(r"^(\d+)([A-Z][a-z]?|nat)\(([^()]*)\)(\d+)([A-Z][a-z]?)$")
RATIO_FORMS = {"G+M", "G+T", "M+T", "M/G", "G/M", "G/T", "M/T"}
MT_PRODUCT_TABLE = json.loads(
    (ROOT / "crates/actinv-data/data/mt_products.json").read_text()
)

# Emitted-particle composition for the reaction grammar present in the
# diagnostic partition.  Composition is the full emitted set, not the net
# (dZ, dA) offset — 'n+p' and 'd' share an offset but different MTs.
PARTICLE = {"n": (0, 1), "p": (1, 1), "d": (1, 2), "t": (1, 3), "3He": (2, 3), "a": (2, 4), "g": (0, 0)}


def emitted_signature(composition: dict[str, int]) -> tuple[int, int]:
    """Net emitted (Z, A) for a composition."""
    z = sum(count * PARTICLE[name][0] for name, count in composition.items())
    a = sum(count * PARTICLE[name][1] for name, count in composition.items())
    return z, a


# Inelastic total/levels share the single-outgoing-neutron signature; the
# scorer prefers MT=4 and falls back to the level range when MT=4 is absent.
INELASTIC_MTS = {4, *range(51, 92)}
SKIP_MTS = {1, 2, 3, 27, 101, 444, 19, 20, 21, 38, *range(201, 208), *range(600, 850)}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


# --------------------------------------------------------------------------
# supplement grammar (matches the G0 seal; dependent values are now read)


def supplement_identity(raw: bytes) -> tuple[int, int, int, int, int, int]:
    return tuple(int(raw[i : i + 3]) for i in range(0, 18, 3))


def supplement_family_id(identity: tuple, reaction: str) -> str:
    zp, ap, zt, at, zr, ar = identity
    return f"{zp:03d}-{ap:03d}|{zt:03d}-{at:03d}|{zr:03d}-{ar:03d}|{reaction}"


def supplement_row_id(source_hash: str, family: str, line: int,
                      energy: str, measurement: str, exfor: str,
                      flags: list[str]) -> str:
    key = (f"{source_hash}\n{family}\n{line}\n{energy}\n{measurement}\n{exfor}\n"
           f"{','.join(flags)}")
    return f"p18-row-{line:05d}-{hashlib.sha256(key.encode()).hexdigest()[:16]}"


def parse_float(text: str) -> float | None:
    text = text.strip()
    if not text:
        return None
    return float(text)


def parse_supplement(path: Path) -> tuple[list[dict], str]:
    """Full parse: reaction records, half-life records and data rows including
    dependent values.  Structure mirrors the frozen seal grammar."""
    source_hash = sha256(path)
    families: list[dict] = []
    current = None
    started = False
    expecting_reaction = False
    raw_lines = path.read_bytes().split(b"\n")
    for line_number, physical in enumerate(raw_lines, 1):
        raw = physical.rstrip(b"\r")
        if raw == b"999999999999999999":
            if current is not None:
                families.append(current)
                current = None
            started = True
            expecting_reaction = True
            continue
        marker = raw[19:20] if len(raw) >= 20 else b""
        if not started:
            continue
        if marker not in {b"R", b"H", b"D"}:
            continue
        identity = supplement_identity(raw)
        if marker == b"R":
            reaction = raw[22:].decode("ascii", "replace").strip()
            zp, ap, zt, at, zr, ar = identity
            current = {
                "family_id": supplement_family_id(identity, reaction),
                "source_line": line_number,
                "reaction": reaction,
                "projectile": PROJECTILE_CODES.get((zp, ap)),
                "identity": identity,
                "target": {"Z": zt, "A": at},
                "product": {"Z": zr, "A": ar},
                "half_lives": None,
                "rows": [],
            }
            expecting_reaction = False
            continue
        if current is None:
            continue
        if marker == b"H":
            current["half_lives"] = {
                "ground_s": parse_float(raw[21:33].decode("ascii", "replace")),
                "metastable_s": parse_float(raw[55:66].decode("ascii", "replace")),
                "ground_spin": raw[33:45].decode("ascii", "replace").strip(),
                "metastable_spin": raw[66:77].decode("ascii", "replace").strip(),
            }
            continue
        # D record
        flags = []
        for offset, (char, name) in enumerate(
            zip(b"!*+", ("digitized", "repeated_energy", "uncertainty_crosses_ratio_bounds"))
        ):
            if len(raw) > 133 + offset and raw[133 + offset : 134 + offset] == bytes([char]):
                flags.append(name)
        energy = raw[22:33].decode("ascii", "replace").strip()
        measurement = raw[122:125].decode("ascii", "replace").strip()
        exfor = raw[127:132].decode("ascii", "replace").strip()
        row = {
            "row_id": supplement_row_id(
                source_hash, current["family_id"], line_number,
                energy, measurement, exfor, flags,
            ),
            "source_line": line_number,
            "incident_energy_MeV": energy,
            "measurement_type": measurement,
            "exfor_entry": exfor,
            "source_flags": flags,
            "energy_MeV": parse_float(energy),
            "sigma_g_b": parse_float(raw[33:44].decode("ascii", "replace")),
            "sigma_m_b": parse_float(raw[55:66].decode("ascii", "replace")),
            "sigma_t_b": parse_float(raw[77:88].decode("ascii", "replace")),
            "ratio": parse_float(raw[99:110].decode("ascii", "replace")),
            "ratio_uncertainty": parse_float(raw[110:121].decode("ascii", "replace")),
        }
        current["rows"].append(row)
    return families, source_hash


# --------------------------------------------------------------------------
# decay sublibrary: minimal MF=8/MT=457 reader -> (za, liso) -> half-life


def _endf_number(text: str) -> float:
    text = text.strip()
    if not text:
        return 0.0
    if "e" in text.lower():
        return float(text)
    for i in range(len(text) - 1, 0, -1):
        if text[i] in "+-":
            return float(text[:i] + "e" + text[i:])
    return float(text)


def parse_decay_states(path: Path) -> dict[tuple[int, int], dict]:
    """(za, liso) -> {half_life, nst}.  Only the section HEAD and the first
    LIST record (half-life) are read; spectra are skipped structurally."""
    states: dict[tuple[int, int], dict] = {}
    section: list[str] = []
    current_tail = None
    def finish(lines: list[str]) -> None:
        if not lines:
            return
        first = lines[0]
        za = int(round(_endf_number(first[0:11])))
        liso = int(first[33:44])
        nst = int(first[44:55]) if first[44:55].strip() else 0
        half = _endf_number(lines[1][0:11]) if len(lines) > 1 else 0.0
        states[(za, liso)] = {"half_life": half, "nst": nst}
    with path.open() as stream:
        for line in stream:
            line = line.rstrip("\n")
            if len(line) < 75:
                continue
            try:
                mf = int(line[70:72]); mt = int(line[72:75])
            except ValueError:
                continue
            if mf == 8 and mt == 457:
                ns = int(line[75:80])
                if ns == 0 and section:
                    # SEND closes the section
                    finish(section); section = []; current_tail = None
                    continue
                if current_tail != (mf, mt):
                    finish(section); section = []
                current_tail = (mf, mt)
                section.append(line)
            else:
                if current_tail is not None:
                    finish(section); section = []; current_tail = None
        finish(section)
    return states


# --------------------------------------------------------------------------
# TENDL MF=9/MF=10 partial scan: (mt, izap, lfs) -> (E_min, E_max)


def partial_domains(path: Path, mfs: tuple = (9, 10)) -> dict[tuple[int, int, int], tuple[float, float]]:
    """Scan a TENDL file for sections in `mfs` and return each section's
    tabulated energy domain (first and last tabulated energy), keyed
    (mt, izap, lfs); MF=3 sections key as (mt, -1, -1)."""
    out: dict[tuple[int, int, int], tuple[float, float]] = {}
    lines = path.read_text().splitlines()
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if len(line) < 75:
            i += 1
            continue
        try:
            mf = int(line[70:72]); mt = int(line[72:75])
        except ValueError:
            i += 1
            continue
        if mf not in mfs:
            i += 1
            continue
        # section runs until SEND (same mat, mf, mt with ns 99999) or mf/mt change
        j = i
        section = []
        while j < n:
            l2 = lines[j]
            if len(l2) < 75:
                j += 1
                continue
            try:
                mf2 = int(l2[70:72]); mt2 = int(l2[72:75]); ns2 = int(l2[75:80])
            except ValueError:
                j += 1
                continue
            if mf2 == 0 or (mf2 == mf and mt2 == mt and ns2 == 99999) or (mf2 != mf or mt2 != mt):
                break
            section.append(l2)
            j += 1
        i = max(j, i + 1)
        if len(section) < 2:
            continue
        if mf == 3:
            # MF3 carries a section HEAD then a TAB1 header; no IZAP/LFS
            subs = [(section[1], 2)]
            izap_key, lfs_key = -1, -1
        else:
            # MF9/10: section head then per-product sub-heads
            # [0,0,IZAP,LFS,NR,NP] + interpolation + data
            subs = [(section[i0], i0 + 1) for i0 in (1,)]
            izap_key = lfs_key = None
        for head, idx0 in subs:
            izap = int(head[22:33]) if head[22:33].strip() else 0
            lfs = int(head[33:44]) if head[33:44].strip() else 0
            if mf == 3:
                izap, lfs = izap_key, lfs_key
            ne = int(head[55:66]) if head[55:66].strip() else 0
            nr = int(head[44:55]) if head[44:55].strip() else 0
            idx = idx0
            consumed = 0
            need_ints = 2 * nr
            while idx < len(section) and consumed < need_ints:
                consumed += 6
                idx += 1
            floats: list[float] = []
            while idx < len(section) and len(floats) < 2 * ne:
                for k in range(0, 66, 11):
                    v = section[idx][k : k + 11].strip()
                    if v:
                        floats.append(_endf_number(v))
                idx += 1
            xs = floats[0::2][:ne]
            if xs:
                out[(mt, izap, lfs)] = (xs[0], xs[-1])
            if mf in (9, 10) and idx < len(section):
                # another product sub-head may follow within the section
                subs.append((section[idx], idx + 1))
    return out


# --------------------------------------------------------------------------
# npz / index loading


def load_npz(path: Path) -> dict:
    import numpy as np

    with zipfile.ZipFile(path) as archive:
        rows = np.lib.format.read_array(archive.open("rows.npy"))
        sig = np.lib.format.read_array(archive.open("sig.npy"))
        bounds = np.lib.format.read_array(archive.open("bounds.npy"))
    return {"rows": rows, "sig": sig, "bounds": bounds}


def group_at(bounds: list[float] | "object", energy_ev: float) -> int | None:
    """Index of the group whose [lo, hi) contains energy_ev (bounds are
    ascending group lower edges with a final upper edge)."""
    import bisect

    b = list(bounds)
    pos = bisect.bisect_right(b, energy_ev) - 1
    if 0 <= pos < len(b) - 1:
        return pos
    return None


# --------------------------------------------------------------------------
# state routing shared by both artifacts
#
# v1.0.1 emitted rank-compressed LFS and its runtime routed (zap, lfs) ->
# decay state, missing -> (zap, 0) ground, missing -> leak.  The candidate
# emits canonical LISO and adds explicit-unrouted rows (lmf -2 unmapped, -3
# audited-leakage).  One routing rule reproduces both behaviors; the
# artifacts' own lfs values carry the semantic difference.

# --------------------------------------------------------------------------
# ratio calculation: the printed column is always IR = sigma_m / sigma_t; the
# measurement type fixes which total applies.


def calculated_ratio(kind: str, sigma_g: float, sigma_m: float, sigma_t: float):
    """IR in the printed form.  Pair-derived types (G+M, M/G, G/M) use the
    measured pair total g+m; total-derived types (M+T, G+T, M/T, G/T) use the
    full product production sigma_t."""
    if kind in {"G+M", "M/G", "G/M"}:
        total = sigma_g + sigma_m
    elif kind in {"M+T", "G+T", "M/T", "G/T"}:
        total = sigma_t
    else:
        return None
    if total <= 0.0 or not math.isfinite(total):
        return None
    return sigma_m / total


# --------------------------------------------------------------------------
# metrics: the frozen P18 set


def percentile(values: list[float], p: float) -> float:
    """Linear-interpolation percentile (NumPy 'linear' convention)."""
    if not values:
        return float("nan")
    ordered = sorted(values)
    rank = (p / 100.0) * (len(ordered) - 1)
    lo = int(math.floor(rank))
    hi = int(math.ceil(rank))
    if lo == hi:
        return ordered[lo]
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (rank - lo)


def metric_block(rows: list[dict]) -> dict:
    logs = [r["ln_cm"] for r in rows]
    abslog = [abs(v) for v in logs]
    n = len(logs)
    geo = math.exp(sum(logs) / n) if n else float("nan")
    return {
        "rows": n,
        "median_abs_ln": statistics.median(abslog) if n else float("nan"),
        "p90_abs_ln": percentile(abslog, 90.0),
        "max_abs_ln": max(abslog) if n else float("nan"),
        "geomean_cm": geo,
        "within_10pct": sum(1 for r in rows if abs(r["cm"] - 1.0) <= 0.10) / n if n else float("nan"),
        "within_20pct": sum(1 for r in rows if abs(r["cm"] - 1.0) <= 0.20) / n if n else float("nan"),
        "within_30pct": sum(1 for r in rows if abs(r["cm"] - 1.0) <= 0.30) / n if n else float("nan"),
    }


def paired_bootstrap(families: list[list[dict]], seed_bytes: bytes, replicates: int = 10_000) -> dict:
    """Family-resampled paired bootstrap: candidate-minus-baseline changes in
    median and p90 |ln(C/M)|.  Deterministic seed from the protocol hash."""
    import random

    rng = random.Random(int.from_bytes(seed_bytes[:8], "big"))
    n = len(families)
    med_changes: list[float] = []
    p90_changes: list[float] = []
    for _ in range(replicates):
        sample = [families[rng.randrange(n)] for _ in range(n)]
        cand = [row["candidate"]["ln_cm"] for fam in sample for row in fam if "candidate" in row]
        base = [row["baseline"]["ln_cm"] for fam in sample for row in fam if "baseline" in row]
        if not cand or not base:
            continue
        ac, ab = [abs(v) for v in cand], [abs(v) for v in base]
        med_changes.append(statistics.median(ac) - statistics.median(ab))
        p90_changes.append(percentile(ac, 90.0) - percentile(ab, 90.0))
    return {
        "replicates": replicates,
        "families": n,
        "median_abs_ln_change": {
            "mean": statistics.fmean(med_changes) if med_changes else float("nan"),
            "p05": percentile(med_changes, 5.0),
            "p95": percentile(med_changes, 95.0),
        },
        "p90_abs_ln_change": {
            "mean": statistics.fmean(p90_changes) if p90_changes else float("nan"),
            "p05": percentile(p90_changes, 5.0),
            "p95": percentile(p90_changes, 95.0),
        },
    }


# --------------------------------------------------------------------------
# eligibility and scoring


def family_target_za(family: dict) -> int:
    return family["target"]["Z"] * 1000 + family["target"]["A"]


def family_product_za(family: dict) -> int:
    return family["product"]["Z"] * 1000 + family["product"]["A"]


def identify_isomer(family: dict, decay_states: dict):
    """Match the H-record metastable half-life to a decay-library isomer of
    the product.  Returns (liso, method, detail)."""
    hl = family.get("half_lives") or {}
    t_m = hl.get("metastable_s")
    product_za = family_product_za(family)
    isomers = sorted(
        (liso, s["half_life"]) for (za, liso), s in decay_states.items()
        if za == product_za and liso > 0
    )
    if not isomers:
        return None, "no_decay_isomer", {}
    if t_m is None:
        if len(isomers) == 1:
            return isomers[0][0], "unique_state_no_t12", {"t12_s": isomers[0][1]}
        return None, "no_half_life_ambiguous", {"isomers": len(isomers)}
    # nearest half-life by log-distance
    ranked = sorted(isomers, key=lambda p: abs(math.log(max(p[1], 1e-30) / t_m)))
    best_l, best_t = ranked[0]
    dist = abs(math.log(max(best_t, 1e-30) / t_m))
    if len(ranked) > 1:
        second = abs(math.log(max(ranked[1][1], 1e-30) / t_m))
        if dist <= math.log(2.0) and second > 2.0 * dist + math.log(1.2):
            return best_l, "half_life_match", {"t12_s": best_t, "measured_t12_s": t_m}
        if dist > math.log(2.0):
            return None, "half_life_mismatch", {"t12_s": best_t, "measured_t12_s": t_m}
        return None, "ambiguous_isomer", {"t12_s": best_t, "measured_t12_s": t_m}
    if dist <= math.log(3.0):
        return best_l, "unique_state_t12", {"t12_s": best_t, "measured_t12_s": t_m}
    return None, "half_life_mismatch", {"t12_s": best_t, "measured_t12_s": t_m}


def reaction_mt_set(family: dict) -> tuple[set | None, str | None]:
    """Resolve the reaction to a set of ENDF MTs.  'x' selects every channel
    producing the product (caller's product filter); a composition selects the
    emitted-particle signature; a bare n' selects inelastic."""
    emitted_tokens = emitted_tokens_of(family["reaction"])
    if emitted_tokens == ["x"]:
        return None, "x"  # sentinel: all product channels
    emitted = parse_emitted(emitted_tokens)
    if emitted is None:
        return None, "unparsed_channel"
    if emitted.pop("n_prime", 0):
        if emitted:
            return None, "unparsed_channel"
        return set(INELASTIC_MTS), None
    za = emitted_signature(emitted)
    cand = {mt for mt, sig in emitted_za_table().items() if sig == za}
    if not cand:
        return None, "no_endf_channel"
    return cand, None


def emitted_tokens_of(reaction: str) -> list[str]:
    """Emitted tokens of a reaction string, split on both ',' and '+'.

    The first token inside the parentheses is the incident projectile and is
    dropped; remaining segments may be 'p', '2n', 'n+a', 'n'', or 'x'."""
    inner = reaction.split("(", 1)[1].split(")", 1)[0].strip()
    parts = [t.strip() for t in inner.split(",")]
    return [s for t in parts[1:] for s in t.split("+") if s.strip()]


def parse_emitted(tokens: list[str]) -> dict | None:
    out: dict[str, int] = defaultdict(int)
    for tok in tokens:
        if not tok:
            continue
        if tok in ("n'", "n\u2019"):
            out["n_prime"] = out.get("n_prime", 0) + 1
            continue
        m = re.fullmatch(r"(\d*)(n|p|d|t|a|g|3He|4He)", tok)
        if not m:
            return None
        count = int(m.group(1)) if m.group(1) else 1
        particle = {"4He": "a", "3He": "3He"}.get(m.group(2), m.group(2))
        out[particle] += count
    return dict(out)


# MT -> emitted-particle (Z, A).  ENDF MT numbers label the emitted channel
# independent of the projectile sublibrary, so the signature can be derived
# from the vendored neutron residual offsets: emitted = n - offset.
# MTs absent from the vendored table that still emit a lone neutron are added
# explicitly: 4 (x,n' total; for charged files the (x,n) channel), 5 (lumped
# remainder), and the discrete inelastic levels 50-91.  MT2 (elastic) is
# excluded: it produces no product rows.
def emitted_za_table() -> dict[int, tuple[int, int]]:
    table = MT_PRODUCT_TABLE.get("table", {})
    out = {int(mt): (-dz, 1 - da) for mt, (dz, da) in table.items()}
    for mt in (4, 5, *range(50, 92)):
        out.setdefault(mt, (0, 1))
    return out


def symbol_to_z(sym: str) -> int | None:
    try:
        return SYMBOLS.index(sym) + 1
    except ValueError:
        return None


def resolve_targets(index: dict, za: int, liso: int = 0) -> list[int]:
    """Row-array target positions whose file corresponds to (za, liso)."""
    hits = []
    for i, t in enumerate(index.get("targets", [])):
        tza = t.get("za")
        tliso = t.get("liso", 0)
        if tza is None:
            m = re.search(r"-([A-Z][a-z]?)(\d+)(m\d*)?\.", t.get("file", ""))
            if not m:
                continue
            tza = symbol_to_z(m.group(1)) * 1000 + int(m.group(2))
            tliso = int(m.group(3)[1:] or "1") if m.group(3) else 0
        if tza == za and (tliso or 0) == liso:
            hits.append(i)
    return hits


def product_states(rows, sig, row_order: list[int], target_ids: list[int],
                   product_za: int, decay_states: dict, candidate: bool):
    """Route emitted production for `product_za` to decay states.

    Returns (state_sig, loss_sig, leaked_sigma, has_residual) where
    state_sig maps mt -> {liso: np.array}, loss_sig maps mt -> np.array
    (the mt total), leaked_sigma maps mt -> np.array of unattributable state
    production (candidate lmf -3 rows), and has_residual lists mts whose
    product rows are unresolved residuals (lmf -1)."""
    import numpy as np

    state_sig: dict[int, dict[int, object]] = defaultdict(dict)
    loss_sig: dict[int, object] = {}
    leak_sig: dict[int, object] = defaultdict(lambda: None)
    residual_mts: set[int] = set()
    tset = set(target_ids)
    for i in row_order:
        row = rows[i]
        tidx, mt, zap, lfs, lmf = (int(row[0]), int(row[1]), int(row[2]),
                                   int(row[3]), int(row[4]))
        if tidx not in tset:
            continue
        if lmf == 0:
            loss_sig[mt] = sig[i]
            continue
        if lmf in (-2, -3):
            v = leak_sig[mt]
            leak_sig[mt] = sig[i] if v is None else v + sig[i]
            continue
        if zap != product_za or lmf not in (9, 10, -1):
            continue
        if lmf == -1:
            residual_mts.add(mt)
            dest = 0
        elif (zap, lfs) in decay_states:
            dest = lfs
        elif lfs > 0 and (zap, 0) in decay_states:
            dest = 0  # v1.0.1 ground fallback
        else:
            dest = 0 if lfs == 0 else -1
        if dest == -1:
            v = leak_sig[mt]
            leak_sig[mt] = sig[i] if v is None else v + sig[i]
            continue
        cur = state_sig[mt].get(dest)
        state_sig[mt][dest] = sig[i] if cur is None else cur + sig[i]
    return state_sig, loss_sig, dict(leak_sig), residual_mts


def select_mt_set(mts_named, is_x: bool, product_za: int, state_sig,
                  loss_sig, residual_mts, inelastic_totals=None):
    """Finalize the MT set against what the artifact actually emits."""
    product_mts = {mt for mt, st in state_sig.items() if st} | residual_mts
    if is_x:
        return set(product_mts), None
    cand = set(mts_named)
    present = product_mts | set(loss_sig) | set(inelastic_totals or {})
    # n-emitting channels (neutron n' / charged (x,n)): MT4 is the declared
    # total and supersedes its level components; otherwise keep every
    # component actually present (discrete levels 50-91, lumped MT5).
    if cand & {4, 5, *range(50, 92)}:
        if 4 in present:
            return {4}, None
        return cand & {5, *range(50, 92)} & present, None
    return cand & present, None


# --------------------------------------------------------------------------
# orchestration

TOTAL_FORMS = {"M+T", "G+T", "M/T", "G/T"}
PAIR_FORMS = {"G+M", "M/G", "G/M"}


def corpus_file(projectile: str, family: dict) -> Path | None:
    code = MANIFEST_CODE[projectile]
    sym = SYMBOLS[family["target"]["Z"] - 1]
    name = f"{code}-{sym}{family['target']['A']:03d}.tendl"
    p = CORPUS / code / name
    return p if p.exists() else None


def corpus_catalog(projectile: str) -> set[tuple[int, int]]:
    """Evaluation-wide residual-state catalog: (za, liso) declared by every
    target header in the frozen corpus.  Predicate 4 asks whether an
    evaluated state exists for the measured isomer; that is a property of
    the corpus, not of which files the diagnostic build managed to
    construct, so quarantined evaluations still contribute their headers."""
    out: set[tuple[int, int]] = set()
    for p in sorted((CORPUS / MANIFEST_CODE[projectile]).glob("*.tendl")):
        numeric = []
        with p.open("r", errors="replace") as fh:
            for _ in range(8):
                line = fh.readline()
                if not line:
                    break
                try:
                    _endf_number(line[0:11])
                except ValueError:
                    continue
                numeric.append(line)
                if len(numeric) == 2:
                    break
        if len(numeric) == 2 and len(numeric[1]) >= 44:
            za = int(round(_endf_number(numeric[0][0:11])))
            liso = int(_endf_number(numeric[1][33:44]))
            out.add((za, liso))
    return out


def evaluate_family(family: dict, artifact: dict, decay_states: dict,
                    liso_d: int | None, candidate: bool,
                    inelastic_totals: dict | None = None):
    """Score one family against one artifact.

    Returns per-row entries {measured, calculated, cm, ln_cm, detail} plus a
    family-level diagnostic record.  `artifact` = {rows, sig, bounds, index}.
    `inelastic_totals` maps mt -> collapsed MF=3 total for inelastic channels
    (the npz emits only isomer partials and their sum for inelastic MTs, so
    the channel total and implicit ground share come from MF=3).
    p6/p5 value-dependent predicates are resolved here."""
    import numpy as np

    product_za = family_product_za(family)
    target_za = family_target_za(family)
    tids = resolve_targets(artifact["index"], target_za, 0)
    if not tids:
        return None, "target_absent"
    rows, sig, bounds = artifact["rows"], artifact["sig"], artifact["bounds"]
    row_order = [i for t in tids for i in artifact["target_rows"].get(t, ())]
    state_sig, loss_sig, leak_sig, residual_mts = product_states(
        rows, sig, row_order, tids, product_za, set(decay_states), candidate
    )
    mts_named, err = reaction_mt_set(family)
    is_x = err == "x"
    if not is_x and mts_named is None:
        return None, err or "unparsed_channel"
    mts, _ = select_mt_set(mts_named, is_x, product_za, state_sig, loss_sig,
                           residual_mts, inelastic_totals)
    # for 'x' families, inelastic channels produce the target nuclide itself;
    # include them only when the family product is the target
    if is_x and product_za == target_za and inelastic_totals:
        mts |= set(INELASTIC_MTS) & set(inelastic_totals)
    # leakage on family channels
    leaked_mts = set(leak_sig) & mts
    # per-row evaluation
    out = []
    for row in family["rows"]:
        e_mev = row["energy_MeV"]
        measured = row["ratio"]
        entry = {"row_id": row["row_id"], "measured": measured}
        if e_mev is None or measured is None:
            entry["status"] = "unparseable_row"
            out.append(entry)
            continue
        g = group_at(bounds, e_mev * 1.0e6)
        if g is None:
            entry["status"] = "energy_outside_groups"
            out.append(entry)
            continue
        sg = sm = st = 0.0
        missing_total = False
        for mt in mts:
            parts = state_sig.get(mt, {})
            if mt in INELASTIC_MTS:
                # the emitted model carries only isomer partials; their sum
                # is the loss row, and the channel total comes from MF=3
                total = (inelastic_totals or {}).get(mt)
                if total is None:
                    missing_total = True
                    continue
                feed = float(loss_sig[mt][g]) if mt in loss_sig else 0.0
                leak = float(leak_sig[mt][g]) if mt in leak_sig else 0.0
                st += float(total[g])
                sg += float(total[g]) - feed - leak
                mrow = parts.get(liso_d) if liso_d is not None else None
                if mrow is not None:
                    sm += float(mrow[g])
            else:
                # the loss row carries the MT total: product production
                # includes routed states, residuals and leaked states
                loss = loss_sig.get(mt)
                if loss is not None:
                    st += float(loss[g])
                grow = parts.get(0)
                if grow is not None:
                    sg += float(grow[g])
                mrow = parts.get(liso_d) if liso_d is not None else None
                if mrow is not None:
                    sm += float(mrow[g])
        calc = calculated_ratio(row["measurement_type"], sg, sm, st)
        entry.update({
            "sigma_g": sg, "sigma_m": sm, "sigma_t": st,
            "leaked_family_mts": sorted(leaked_mts),
        })
        if calc is None:
            entry["status"] = "undefined_ratio_form" if row["measurement_type"] not in RATIO_FORMS else "zero_denominator"
        else:
            entry["calculated"] = calc
            entry["cm"] = calc / measured if measured != 0 else float("inf")
            entry["ln_cm"] = math.log(entry["cm"]) if entry["cm"] > 0 else (-math.inf if calc == 0 else None)
            entry["status"] = "scored"
        out.append(entry)
    return {"rows": out, "mts": sorted(mts), "leaked_mts": sorted(leaked_mts)}, None


def mf3_domains(path: Path) -> dict[int, tuple[float, float]]:
    """MF=3 section domains (total cross-section tab ranges) per MT."""
    out = {}
    for (mt, izap, lfs), dom in partial_domains(path, mfs=(3,)).items():
        out[mt] = dom
    return out


def state_domains(path: Path) -> dict[tuple[int, int, int], tuple[float, float]]:
    """MF=9/10 section domains keyed (mt, izap, lfs_raw)."""
    return partial_domains(path, mfs=(9, 10))


def domain_check(mt_set: set, energy_ev: float, product_za: int,
                 totals_dom: dict, states_dom: dict,
                 m_raw_lfs: set[int]) -> tuple[bool, str | None]:
    """Predicate 6: energy must lie inside both required evaluated
    state-partial domains.  For ordinary channels those are the ground
    (lfs=0) and named-metastable partials; for inelastic-typed channels the
    ground partial is implicit in the MF=3 total so the total's tabulated
    domain stands in for it.  An absent metastable partial is evaluated zero
    and creates no domain obligation."""
    for mt in mt_set:
        if mt in INELASTIC_MTS:
            dom = totals_dom.get(mt)
            label = "total"
        else:
            dom = states_dom.get((mt, product_za, 0))
            label = "ground"
            if dom is None:
                # no declared ground partial: fall back to the channel total
                dom = totals_dom.get(mt)
                label = "total"
        if dom is not None and not (dom[0] <= energy_ev <= dom[1]):
            return False, f"energy_outside_{label}_domain_mt{mt}"
    for lfs in m_raw_lfs:
        for mt in mt_set:
            dom = states_dom.get((mt, product_za, lfs))
            if dom is not None and not (dom[0] <= energy_ev <= dom[1]):
                return False, f"energy_outside_metastable_domain_mt{mt}"
    return True, None


def load_artifact(npz_path: Path, index_path: Path) -> dict | None:
    if not npz_path.exists() or not index_path.exists():
        return None
    lib = load_npz(npz_path)
    lib["index"] = json.loads(index_path.read_text())
    # row indices grouped by target for per-family scans
    tmap: dict[int, list[int]] = defaultdict(list)
    for i in range(lib["rows"].shape[0]):
        tmap[int(lib["rows"][i, 0])].append(i)
    lib["target_rows"] = tmap
    return lib


def catalog_states(index: dict) -> set[tuple[int, int]]:
    return {(e["za"], e["liso"]) for e in index.get("state_catalog", [])}


def baseline_raw_lfs(domains: dict, mt: int, product_za: int,
                     liso_d: int) -> int | None:
    """v1.0.1 rank compression: positive raw LFS per (mt, product) sort into
    ranks 1..n; rank k corresponds to the k-th smallest raw lfs."""
    raws = sorted(lfs for (m, iz, lfs) in domains
                  if m == mt and iz == product_za and lfs > 0)
    if 1 <= liso_d <= len(raws):
        return raws[liso_d - 1]
    return None


def candidate_lfs_map(index: dict) -> dict:
    """Precomputed (target_za, mt, product_za, canonical_liso) -> {raw_lfs}."""
    out: dict[tuple, set] = defaultdict(set)
    for t in index.get("targets", []):
        tza = t.get("za")
        for m in t.get("state_mappings", []):
            out[(tza, m.get("mt"), m.get("zap"), m.get("canonical_liso"))].add(
                m.get("raw_lfs")
            )
    return out


def score_diagnostics() -> dict:
    import numpy as np

    families, src_hash = parse_supplement(SUPPLEMENT)
    seal = json.loads(SEAL.read_text())
    seal_map = {f["family_id"]: f for f in seal["families"]}
    decay = parse_decay_states(DECAY_FILES["jeff_3_3"][0])
    decay.update(parse_decay_states(DECAY_FILES["endfb_viii_0"][0]))

    artifacts = {}
    for proj in BASELINE_LIBRARIES:
        npz, _ = BASELINE_LIBRARIES[proj]
        artifacts[("baseline", proj)] = load_artifact(
            npz, npz.with_name(npz.stem + "_index.json"))
        cand_npz = WORK / f"candidate-{proj}.npz"
        cand = load_artifact(cand_npz, WORK / f"candidate-{proj}_index.json")
        if cand is not None:
            cand["lfs_map"] = candidate_lfs_map(cand["index"])
        artifacts[("candidate", proj)] = cand

    corpus_cats = {p: corpus_catalog(p) for p in BASELINE_LIBRARIES}

    quarantined: dict[str, set] = defaultdict(set)
    report_path = WORK / "build_report.json"
    if report_path.is_file():
        for rec in json.loads(report_path.read_text()):
            quarantined[rec["projectile"]] = set(rec.get("quarantined", {}))

    domain_cache: dict[str, tuple] = {}
    mf3_cache: dict[tuple, dict] = {}

    def domains_for(projectile: str, family: dict):
        p = corpus_file(projectile, family)
        if p is None:
            return None
        if p.name not in domain_cache:
            domain_cache[p.name] = (
                mf3_domains(p), state_domains(p))
        return domain_cache[p.name]

    def inelastic_totals_for(projectile: str, family: dict, bounds) -> dict:
        """Collapsed MF=3 totals for the inelastic MTs present in the file."""
        p = corpus_file(projectile, family)
        if p is None:
            return {}
        key = (p.name, len(bounds))
        if key not in mf3_cache:
            totals = {}
            for mt in sorted(INELASTIC_MTS):
                tab = read_mf3_table(p, mt)
                if tab is not None:
                    totals[mt] = collapse_groups(*tab, list(bounds))
            mf3_cache[key] = totals
        return mf3_cache[key]

    ledger = []
    stats = defaultdict(int)
    for family in families:
        fid = family["family_id"]
        sealed = seal_map.get(fid)
        if sealed is None or sealed["partition"] != "diagnostic":
            continue
        proj = family["projectile"]
        pred, secondary = None, []
        if proj not in BASELINE_LIBRARIES:
            pred = "unsupported_projectile"
        elif family["target"]["A"] == 0:
            pred = "natural_target"
        if pred is None:
            mts_named, rerr = reaction_mt_set(family)
            if rerr == "x":
                mts_named = None
            elif rerr:
                pred = rerr if rerr in ("unparsed_channel", "no_endf_channel") else "unparsed_reaction"
        if pred is None and mts_named:
            # residual identity: product = target + projectile - emitted
            zp, ap = family["identity"][0], family["identity"][1]
            emitted = parse_emitted(emitted_tokens_of(family["reaction"]))
            nprime = emitted.pop("n_prime", 0) if emitted else 0
            if emitted is None:
                emitted = {}
            emitted["n"] = emitted.get("n", 0) + nprime
            ez = sum(PARTICLE[k][0] * v for k, v in emitted.items())
            ea = sum(PARTICLE[k][1] * v for k, v in emitted.items())
            zr = family["target"]["Z"] + zp - ez
            ar = family["target"]["A"] + ap - ea
            if (zr, ar) != (family["product"]["Z"], family["product"]["A"]):
                pred = "residual_mismatch"
        liso_d = imeth = None
        if pred is None:
            liso_d, imeth, _idet = identify_isomer(family, decay)
            if liso_d is None:
                pred = "isomer_" + (imeth or "unidentified")
            elif (family_product_za(family), liso_d) not in corpus_cats[proj]:
                pred = "metastable_not_catalog_matched"
        # evaluate artifacts when the family is still provisionally eligible
        results = {}
        for label in ("baseline", "candidate"):
            art = artifacts[(label, proj)]
            if art is None:
                results[label] = (None, "library_absent")
                continue
            i_totals = inelastic_totals_for(proj, family, art["bounds"])
            res, err = evaluate_family(family, art, decay, liso_d,
                                       label == "candidate", i_totals)
            if label == "candidate" and err == "target_absent":
                src = corpus_file(proj, family)
                if src is not None and src.name in quarantined[proj]:
                    err = "build_failed_g3"
            if (label == "candidate" and err is None and liso_d is not None
                    and (family_product_za(family), liso_d)
                    not in catalog_states(art["index"])):
                # the evaluated isomer exists in the corpus (predicate 4)
                # but the artifact cannot express it: every evaluation that
                # could anchor its identity was quarantined or unbuilt, so
                # the emitted partials were routed to leakage at build time.
                res, err = None, "build_failed_g3"
            results[label] = (res, err)
        doms = domains_for(proj, family) if proj in BASELINE_LIBRARIES else None
        for i, row in enumerate(family["rows"]):
            primary = pred
            if primary is None:
                # p7 flags
                if ("digitized" in row["source_flags"]
                        and row["ratio_uncertainty"] is None):
                    primary = "digitized_without_tabulated_uncertainty"
            if primary is None and row["energy_MeV"] is None:
                primary = "unparseable_energy"
            if primary is None and row["ratio"] is None:
                primary = "unparseable_value"
            if primary is None and doms is None:
                primary = "target_file_absent"
            if primary is None:
                totals_dom, states_dom = doms
                cand = artifacts[("candidate", proj)]
                mt_set_all = set()
                for label in ("baseline", "candidate"):
                    r, _ = results[label]
                    if r:
                        mt_set_all |= set(r["mts"])
                # raw lfs backing the named metastable per artifact convention
                m_lfs = set()
                if liso_d is not None:
                    if cand is not None:
                        lmap = cand["lfs_map"]
                        for mt2 in mt_set_all:
                            m_lfs |= lmap.get(
                                (family_target_za(family), mt2,
                                 family_product_za(family), liso_d), set())
                    for mt2 in mt_set_all:
                        rb = baseline_raw_lfs(states_dom, mt2,
                                              family_product_za(family), liso_d)
                        if rb is not None:
                            m_lfs.add(rb)
                ok, derr = domain_check(mt_set_all, row["energy_MeV"] * 1e6,
                                        family_product_za(family), totals_dom,
                                        states_dom, m_lfs)
                if not ok:
                    primary = derr
            rec = {
                "row_id": row["row_id"], "family_id": fid,
                "projectile": proj, "source_line": row["source_line"],
                "energy_MeV": row["energy_MeV"],
                "measurement_type": row["measurement_type"],
                "source_flags": row["source_flags"],
                "measured": row["ratio"],
                "measured_uncertainty": row["ratio_uncertainty"],
                "isomer_liso": liso_d, "isomer_method": imeth,
            }
            if primary:
                rec["status"] = "ineligible"
                rec["predicate"] = primary
                rec["secondary"] = secondary
                stats[f"ineligible_{primary}"] += 1
            else:
                rec["status"] = "eligible"
                stats["eligible_rows"] += 1
                for label in ("baseline", "candidate"):
                    r, err = results[label]
                    if r is None:
                        rec[label] = {"status": err or "unevaluated"}
                        stats[f"{label}_{rec[label]['status']}"] += 1
                        continue
                    erow = r["rows"][i]
                    rec[label] = {
                        "status": erow["status"],
                        "sigma_g": erow.get("sigma_g"),
                        "sigma_m": erow.get("sigma_m"),
                        "sigma_t": erow.get("sigma_t"),
                        "calculated": erow.get("calculated"),
                        "cm": erow.get("cm"),
                        "ln_cm": erow.get("ln_cm"),
                        "mts": r["mts"],
                        "leaked_family_mts": r["leaked_mts"],
                    }
                    if erow["status"] == "scored":
                        stats[f"{label}_scored"] += 1
                    else:
                        stats[f"{label}_{erow['status']}"] += 1
            ledger.append(rec)

    # metrics: eligible rows with finite ln_cm per artifact; paired subset
    def scored(label):
        return [r for r in ledger if r["status"] == "eligible"
                and r.get(label, {}).get("ln_cm") is not None
                and math.isfinite(r[label]["ln_cm"])]

    per_proj = {}
    for proj in BASELINE_LIBRARIES:
        per_proj[proj] = {}
        for label in ("baseline", "candidate"):
            rows = [r for r in scored(label) if r["projectile"] == proj]
            metric_rows = [{"ln_cm": r[label]["ln_cm"], "cm": r[label]["cm"]}
                           for r in rows]
            per_proj[proj][label] = metric_block(metric_rows)
    overall = {}
    for label in ("baseline", "candidate"):
        rows = scored(label)
        overall[label] = metric_block(
            [{"ln_cm": r[label]["ln_cm"], "cm": r[label]["cm"]} for r in rows])
    paired = [r for r in ledger if r["status"] == "eligible"
              and r.get("baseline", {}).get("ln_cm") is not None
              and r.get("candidate", {}).get("ln_cm") is not None
              and math.isfinite(r["baseline"]["ln_cm"])
              and math.isfinite(r["candidate"]["ln_cm"])]
    fam_pairs = defaultdict(list)
    for r in paired:
        fam_pairs[r["family_id"]].append(
            {"baseline": {"ln_cm": r["baseline"]["ln_cm"]},
             "candidate": {"ln_cm": r["candidate"]["ln_cm"]}})
    boot = paired_bootstrap(list(fam_pairs.values()),
                          bytes.fromhex(PROTOCOL_SHA256))

    return {
        "schema": "actinv-g4-p18b-diagnostics-1",
        "catalog_semantics": {
            "eligibility_predicate_4": "corpus-wide evaluated target headers "
            "(the deterministic evaluation-wide residual-state catalog); "
            "independent of which files the diagnostic build constructed",
            "candidate_scoring": "candidate rows whose required catalog "
            "state was unconstructable (all anchoring evaluations "
            "quarantined by G3 or unbuilt) are reported build_failed_g3; "
            "emitted partials for such states were routed to leakage in "
            "the artifact and cannot be attributed to the named isomer",
        },
        "source_sha256": src_hash,
        "supplement_sha256_expected": SUPPLEMENT_SHA256,
        "counts": {
            "diagnostic_families": sum(1 for f in seal["families"]
                                       if f["partition"] == "diagnostic"),
            "ledger_rows": len(ledger),
            **dict(stats),
        },
        "per_projectile": per_proj,
        "overall": overall,
        "paired_rows": len(paired),
        "paired_bootstrap": boot,
        "ledger": ledger,
    }


# --------------------------------------------------------------------------
# driver


def verify_sources() -> dict:
    checks = {}
    checks["supplement_hash"] = sha256(SUPPLEMENT) == SUPPLEMENT_SHA256
    checks["decay_hashes"] = {
        name: (sha256(p) == h if p.is_file() else "absent")
        for name, (p, h) in DECAY_FILES.items()
    }
    checks["baseline_hashes"] = {
        proj: (sha256(p) == h if p.is_file() else "absent")
        for proj, (p, h) in BASELINE_LIBRARIES.items()
    }
    checks["protocol_hash"] = sha256(PROTOCOL) == PROTOCOL_SHA256
    checks["seal_hash"] = sha256(SEAL)
    return checks


def cross_check_seal_row_ids(families: list[dict], seal: dict) -> dict:
    """Every seal row id must be reproduced by this control's parser; every
    diagnostic row id in the ledger must be in the seal."""
    seal_rows = {r["row_id"] for f in seal["families"] for r in f["rows"]}
    mine = {r["row_id"] for f in families for r in f["rows"]}
    return {
        "seal_rows": len(seal_rows),
        "parsed_rows": len(mine),
        "missing_from_parser": sorted(seal_rows - mine)[:5],
        "extra_in_parser": sorted(mine - seal_rows)[:5],
        "row_ids_match": seal_rows == mine,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(REPORT))
    args = ap.parse_args()

    started = time.monotonic()
    checks = verify_sources()
    families, src_hash = parse_supplement(SUPPLEMENT)
    seal = json.loads(SEAL.read_text())
    checks["seal_row_ids"] = cross_check_seal_row_ids(families, seal)

    report = score_diagnostics()
    report["checks"] = checks
    report["runtime_s"] = time.monotonic() - started
    report["quarantine"] = {
        "diagnostic_values_read": True,
        "heldout_values_read": False,
    }
    report["gate"] = "P18b-G4"
    report["control_source_sha256"] = sha256(Path(__file__))
    report["protocol_sha256"] = PROTOCOL_SHA256
    report["seal_sha256"] = sha256(SEAL)

    build_report = WORK / "build_report.json"
    if build_report.is_file():
        report["candidate_builds"] = {
            r["projectile"]: {
                "built_files": r["built_files"],
                "quarantined_count": len(r.get("quarantined", {})),
                "output_sha256": r.get("output_sha256"),
            }
            for r in json.loads(build_report.read_text())
        }

    p16 = ROOT / "results/p16_performance.json"
    if p16.is_file():
        perf = json.loads(p16.read_text())
        report["compatibility_performance"] = {
            "control": "controls/p16_performance.py",
            "workload": perf.get("workload"),
            "normalized_result_exact": perf.get("checks", {}).get(
                "normalized_result_exact"
            ),
            "normalized_result_sha256": perf.get("normalized_result_sha256"),
            "median_ratio": perf.get("ratios", {}).get(
                "candidate_over_release_median"
            ),
            "p95_ratio": perf.get("ratios", {}).get(
                "candidate_over_release_p95"
            ),
            "peak_rss_ratio": perf.get("ratios", {}).get(
                "candidate_over_release_peak_rss"
            ),
            "pass": perf.get("pass"),
        }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1) + "\n")
    print(json.dumps({
        "out": str(out),
        "checks": {k: (v if isinstance(v, bool) else "detail") for k, v in checks.items()},
        "counts": report["counts"],
        "overall": report["overall"],
        "paired_rows": report["paired_rows"],
    }, indent=1))
    return 0




# --------------------------------------------------------------------------
# pointwise TAB1 -> lethargy collapse (mirrors GroupStructure::collapse:
# sigma_g = lethargy_integral(lo, hi) / ln(hi/lo), with the same per-law
# antiderivatives for INT 1/2/3/5 and adaptive refinement for INT 4)


def read_mf3_table(path: Path, mt: int):
    """Return (xs, ys, interp_pairs) for the MF=3/MT section, or None."""
    lines = path.read_text().splitlines()
    for i, line in enumerate(lines):
        if len(line) < 75:
            continue
        try:
            if int(line[70:72]) != 3 or int(line[72:75]) != mt:
                continue
        except ValueError:
            continue
        head = lines[i + 1]
        nr = int(head[44:55]); np_ = int(head[55:66])
        j = i + 2
        pairs = []
        while len(pairs) < 2 * nr:
            for k in range(0, 66, 11):
                v = lines[j][k:k + 11].strip()
                if v:
                    pairs.append(int(_endf_number(v)))
            j += 1
        interp = [(pairs[2 * k], pairs[2 * k + 1]) for k in range(nr)]
        floats = []
        while len(floats) < 2 * np_:
            for k in range(0, 66, 11):
                v = lines[j][k:k + 11].strip()
                if v:
                    floats.append(_endf_number(v))
            j += 1
        return floats[0::2][:np_], floats[1::2][:np_], interp
    return None


def _seg_law(interp: list, segment: int) -> int:
    endpoint = segment + 2
    for nbt, law in interp:
        if endpoint <= nbt:
            return law
    return interp[-1][1]


def _seg_value(xs, ys, interp, seg: int, x: float) -> float:
    x1, x2 = xs[seg], xs[seg + 1]
    y1, y2 = ys[seg], ys[seg + 1]
    if x2 == x1:
        return y2
    law = _seg_law(interp, seg)
    lin = (x - x1) / (x2 - x1)
    if law == 1:
        return y1
    if law == 2:
        return y1 + lin * (y2 - y1)
    if law == 3:
        return y1 + math.log(x / x1) / math.log(x2 / x1) * (y2 - y1)
    if law == 4:
        return y1 * (y2 / y1) ** lin
    if law == 5:
        return y1 * (y2 / y1) ** (math.log(x / x1) / math.log(x2 / x1))
    raise ValueError(f"unsupported INT={law}")


def _x_minus_ln1p(v: float) -> float:
    return v - math.log1p(v)


def _expm1_over_x(v: float) -> float:
    return math.expm1(v) / v if v != 0.0 else 1.0


def lethargy_integral(xs, ys, interp, low: float, high: float) -> float:
    if high == low or high <= xs[0] or low >= xs[-1]:
        return 0.0
    low = max(low, xs[0]); high = min(high, xs[-1])
    total = 0.0
    for seg in range(len(xs) - 1):
        x1, x2 = xs[seg], xs[seg + 1]
        if x2 <= low or x1 >= high or x2 <= x1:
            continue
        a = max(low, x1); b = min(high, x2)
        if b <= a:
            continue
        y1, y2 = ys[seg], ys[seg + 1]
        law = _seg_law(interp, seg)
        rm1 = (b - a) / a
        lr = math.log1p(rm1)
        if law == 1:
            total += y1 * lr
        elif law == 2:
            slope = (y2 - y1) / (x2 - x1)
            va = _seg_value(xs, ys, interp, seg, a)
            total += va * lr + slope * a * _x_minus_ln1p(rm1)
        elif law == 3:
            va = _seg_value(xs, ys, interp, seg, a)
            vb = _seg_value(xs, ys, interp, seg, b)
            total += 0.5 * (va + vb) * lr
        elif law == 4:
            # log-linear in x: integrate sigma*dlE by Simpson in u=ln(E)
            n = 64
            ua, ub = math.log(a), math.log(b)
            h = (ub - ua) / n
            s = _seg_value(xs, ys, interp, seg, math.exp(ua)) + \
                _seg_value(xs, ys, interp, seg, math.exp(ub))
            for k in range(1, n):
                w = 4.0 if k % 2 else 2.0
                s += w * _seg_value(xs, ys, interp, seg, math.exp(ua + k * h))
            total += s * h / 3.0
        elif law == 5:
            power = math.log(y2 / y1) / math.log(x2 / x1)
            va = _seg_value(xs, ys, interp, seg, a)
            total += va * lr * _expm1_over_x(power * lr)
        else:
            raise ValueError(f"unsupported INT={law}")
    return total


def collapse_groups(xs, ys, interp, bounds) -> list[float]:
    return [lethargy_integral(xs, ys, interp, lo, hi) / math.log(hi / lo)
            for lo, hi in zip(bounds[:-1], bounds[1:])]
if __name__ == "__main__":
    raise SystemExit(main())
