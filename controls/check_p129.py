#!/usr/bin/env python3
"""P129 gates G2, G3, G4 and G5 (protocols/ACTINV-P129_PROTOCOL.md).

  G2  examples/shielding_demo.json and examples/fns_fe_5min.json give byte-identical results (apart from `ms`)
      under the reference and candidate binaries.
  G3  Independent recomputation of the geometry self-shielding factors of a W-186 foil (0.01 cm and 0.1 cm). A
      second ENDF-6 MF=2/MT=151 reader (SLBW/MLBW LRF=1/2 and Reich-Moore LRF=3, written here, not shared with the
      solver) rebuilds the per-resonance peak cross section, z, G(z) and the (Gamma_gamma/E^2) g Gamma_n/Gamma
      weighted group means on the library group bounds; they must match the candidate ledger within 1e-9 relative.
      Every factor must lie in [A2, 1] and the 0.1 cm factor must not exceed the 0.01 cm factor in any group.
  G4  Table mode (shielding_demo): a `dilution_overrides` entry equal to the ledger's computed sigma0 changes
      nothing but the excluded keys; ten times that sigma0 moves the factor toward one.
  G5  Report only: tabulated FISPACT-II foil factors next to ACTINV's, when a table is supplied.

The overall verdict is PASS only if G2, G3 and G4 all ran and passed.

Usage:
  check_p129.py --reference BIN --candidate BIN [--workdir target/p129-work] [--out results/p129_shielding.json]
                [--gates G2,G3,G4,G5] [--timeout SECONDS] [--nuclear-data DIR] [--resonance-file PATH]
                [--fispact-factors JSON]

Runs go through subprocess (no shell), serially, from the repository root, each under --timeout. Run inside the
required systemd cgroup with a disk-backed TMPDIR. Exit 0 only on PASS.
"""
import argparse
import bisect
import hashlib
import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "protocols" / "ACTINV-P129_PROTOCOL.md"
DEMO_SPEC = ROOT / "examples" / "shielding_demo.json"
FNS_SPEC = ROOT / "examples" / "fns_fe_5min.json"
DEFAULT_TIMEOUT_S = 3600

# --- constants, each with its provenance --------------------------------------------------------------------
# Universal sigmoid curve of Martinho, Goncalves and Salgado, FISPACT-II manual Eqs. 44-48, Table 25.
A1, A2, Z0, P = 1.000, 0.060, 2.70, 0.82
# ENDF-6 formats manual wave number: k = K_WAVE * AWRI/(AWRI+1) * sqrt(E_eV), in 1/sqrt(barn), so that pi/k^2 is a
# cross section in barns. This is the manual's rounded constant (shielding.rs uses the same value). A CODATA
# derivation, sqrt(2 m_n c^2)/(hbar c) * 10 (fm^-1 -> sqrt(barn)^-1), gives 2.19681e-3, 1.7e-5 higher; the checker
# reports that gap but uses the ENDF value, because that is the convention the solver and the ENDF manual share.
K_WAVE = 2.196771e-3
# CODATA 2018 neutron mass energy (MeV) and hbar*c (MeV fm), for the informational cross-check only.
MN_C2_MEV = 939.56542052
HBAR_C_MEV_FM = 197.3269804
K_WAVE_SANITY_REL = 1e-4
# SI 2019 Avogadro constant and the neutron mass in u that composition.rs multiplies the evaluated AWR by, so the
# atomic mass of an explicit nuclide is AWR(decay evaluation) * NEUTRON_MASS_AMU.
AVOGADRO = 6.02214076e23
NEUTRON_MASS_AMU = 1.00866491595
BARN_CM2 = 1.0e-24
MATCH_REL = 1e-9
FOIL_THICKNESS_CM = (0.01, 0.1)
DENSITY_W_G_CM3 = 19.3
W186_ZA = 74186

# Keys that G4 allows to differ between the plain run and the override run, with the reason for each.
G4_EXCLUDED = {
    "ms": "top-level wall-clock timing",
    "dilution_overrides": "the added entry echoed under ledger.shielding / certificate.shielding (override run only)",
    "spec / spec_sha256 / *fingerprint*": "an echoed spec or its fingerprint or hash, if the build emits one",
}


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def rel_diff(a, b):
    scale = max(abs(a), abs(b))
    return 0.0 if scale == 0.0 else abs(a - b) / scale


# ---------------------------------------------------------------- independent ENDF-6 MF=2 reader


def endf_float(field):
    s = field.strip()
    if not s:
        return 0.0
    try:
        return float(s)
    except ValueError:
        m = re.fullmatch(r"([+-]?\d*\.?\d*)([+-]\d+)", s)
        if not m:
            raise ValueError(f"unreadable ENDF number {field!r}")
        return float(m.group(1) + "e" + m.group(2))


def endf_int(field):
    s = field.strip()
    if not s:
        return 0
    try:
        return int(s)
    except ValueError:
        return int(round(float(s)))


def fields(line):
    return [line[11 * i:11 * (i + 1)] for i in range(6)]


def mf2_mt151_lines(text):
    """The MF=2/MT=151 lines of the first material in an ENDF-6 tape, or [] when it has none."""
    out, mat = [], None
    for line in text.splitlines():
        if line[70:72].strip() != "2" or line[72:75].strip() != "151":
            continue
        this = line[66:70].strip()
        if mat is None:
            mat = this
        if this != mat:
            break
        out.append(line)
    return [ln for ln in out if line_mt_nonzero(ln)]


def line_mt_nonzero(line):
    return line[72:75].strip() != "0"


def read_cont(lines, i):
    f = fields(lines[i])
    return (endf_float(f[0]), endf_float(f[1]), endf_int(f[2]), endf_int(f[3]), endf_int(f[4]), endf_int(f[5])), i + 1


def read_list(lines, i):
    head, i = read_cont(lines, i)
    npl = head[4]
    values = []
    for _ in range(-(-npl // 6)):
        values.extend(endf_float(x) for x in fields(lines[i]))
        i += 1
    return head, values[:npl], i


def read_tab1(lines, i):
    head, i = read_cont(lines, i)
    nr, npts = head[4], head[5]
    return head, i + (-(-nr // 3)) + (-(-npts // 3))


def parse_mf2(lines):
    """Resolved (LRU=1) ranges of an MF=2/MT=151 section. Parsing of a range list stops at the first unresolved
    (LRU=2) range, which must come last by energy, and at an LRF=7 or unknown-LRF range, which is flagged."""
    head, i = read_cont(lines, 0)
    out = {"za": int(round(head[0])), "awr": head[1], "isotopes": []}
    for _ in range(head[4]):
        iso, i = read_cont(lines, i)
        entry = {"zai": int(round(iso[0])), "abundance": iso[1], "lfw": iso[3], "ranges": [],
                 "stopped": None}
        out["isotopes"].append(entry)
        stop = False
        for _ in range(iso[4]):
            if stop:
                break
            rng, i = read_cont(lines, i)
            el, eh, lru, lrf, nro = rng[0], rng[1], rng[2], rng[3], rng[4]
            if nro == 1:
                _, i = read_tab1(lines, i)
            item = {"el": el, "eh": eh, "lru": lru, "lrf": lrf}
            if lru == 0:
                _, i = read_cont(lines, i)
            elif lru == 1 and lrf in (1, 2, 3):
                ctl, i = read_cont(lines, i)
                item["spi"], item["ap"], item["groups"] = ctl[0], ctl[1], []
                for _ in range(ctl[4]):
                    lh, values, i = read_list(lines, i)
                    if lh[4] != 6 * lh[5]:
                        raise ValueError(f"LRF={lrf} LIST has NPL={lh[4]}, expected 6*NRS={6 * lh[5]}")
                    res = []
                    for k in range(0, len(values), 6):
                        v = values[k:k + 6]
                        if lrf in (1, 2):
                            res.append({"E": v[0], "J": v[1], "GT": v[2], "GN": v[3], "GG": v[4], "GF": v[5]})
                        else:
                            res.append({"E": v[0], "J": v[1], "GN": v[2], "GG": v[3], "GFA": v[4], "GFB": v[5]})
                    item["groups"].append({"awri": lh[0], "l": lh[2], "lrx": lh[3], "resonances": res})
            elif lru == 1 and lrf == 7:
                entry["stopped"] = "LRF=7 (R-matrix limited) range; rejected by the solver"
                stop = True
            elif lru == 2:
                entry["stopped"] = "unresolved range reached"
                stop = True
            else:
                entry["stopped"] = f"unsupported LRU={lru}/LRF={lrf}"
                stop = True
            entry["ranges"].append(item)
    return out


def parse_mf2_file(path):
    text = Path(path).read_text()
    lines = mf2_mt151_lines(text)
    if not lines:
        raise ValueError("no MF=2/MT=151 section")
    return parse_mf2(lines)


def resonance_lines(parsed, za):
    """Usable resolved resonances in file order: dicts with E, J, SPI, AWRI, GN, GG and Gamma. A resonance is
    unusable (skipped, as the solver does) when E <= 0 or GN <= 0 or GG <= 0. Gamma is the listed total GT, raised
    to the component sum when rounding leaves it below it (LRF=1/2), or GN+GG+|GFA|+|GFB| (Reich-Moore)."""
    isotopes = [iso for iso in parsed["isotopes"] if iso["zai"] == za]
    if not isotopes and len(parsed["isotopes"]) == 1:
        isotopes = parsed["isotopes"]
    out = []
    for iso in isotopes:
        for rng in iso["ranges"]:
            if rng["lru"] != 1:
                continue
            if rng["lrf"] not in (1, 2, 3):
                raise ValueError(f"resolved range {rng['el']}-{rng['eh']} eV has LRF={rng['lrf']}")
            for group in rng["groups"]:
                for r in group["resonances"]:
                    if rng["lrf"] in (1, 2):
                        gamma = max(r["GT"], r["GN"] + r["GG"] + r["GF"])
                    else:
                        gamma = r["GN"] + r["GG"] + abs(r["GFA"]) + abs(r["GFB"])
                    if r["E"] <= 0.0 or r["GN"] <= 0.0 or r["GG"] <= 0.0 or group["awri"] <= 0.0:
                        continue
                    out.append({"E": r["E"], "J": r["J"], "SPI": rng["spi"], "AWRI": group["awri"],
                                "GN": r["GN"], "GG": r["GG"], "GAMMA": gamma})
    return out


# ---------------------------------------------------------------- the model, recomputed


def sigmoid(z):
    """G(z) = (A1 - A2) / (1 + (z/z0)^p) + A2, FISPACT-II manual Eq. 44; G -> A1 for z <= 0."""
    if z <= 0.0:
        return A1
    return (A1 - A2) / (1.0 + (z / Z0) ** P) + A2


def peak_cross_section_b(line):
    """Single-level Breit-Wigner peak 4 pi/k^2 * g * Gn/Gamma in barns, g = (2|J|+1)/(2(2I+1))."""
    g = (2.0 * abs(line["J"]) + 1.0) / (2.0 * (2.0 * line["SPI"] + 1.0))
    k = K_WAVE * line["AWRI"] / (line["AWRI"] + 1.0) * math.sqrt(line["E"])
    return 4.0 * math.pi / (k * k) * (g * line["GN"] / line["GAMMA"])


def group_factors(lines, bounds, number_density, effective_length_cm):
    """group -> (weighted mean G, resonance count). z = N sigma_peak y sqrt(Gamma_gamma/Gamma) with sigma_peak in
    cm^2; the weight is (Gamma_gamma/E^2) g Gamma_n/Gamma. Resonances outside [bounds[0], bounds[-1]) are dropped."""
    sums = {}
    for line in lines:
        group = bisect.bisect_right(bounds, line["E"]) - 1
        if group < 0 or group >= len(bounds) - 1:
            continue
        peak = peak_cross_section_b(line)
        z = number_density * peak * BARN_CM2 * effective_length_cm * math.sqrt(line["GG"] / line["GAMMA"])
        g = (2.0 * abs(line["J"]) + 1.0) / (2.0 * (2.0 * line["SPI"] + 1.0))
        weight = line["GG"] / (line["E"] * line["E"]) * (g * line["GN"] / line["GAMMA"])
        acc = sums.setdefault(group, [0.0, 0.0, 0])
        acc[0] += weight * sigmoid(z)
        acc[1] += weight
        acc[2] += 1
    return {grp: (acc[0] / acc[1], acc[2]) for grp, acc in sums.items() if acc[1] > 0.0}


def decay_awr(decay_path, za):
    """AWR of the ground state (LISO=0) of `za` in an ENDF-6 decay tape: the first MF=1/MT=451 HEAD with that ZA."""
    want = float(za)
    with open(decay_path, "r") as f:
        pending = None
        for line in f:
            if pending is not None:
                if endf_int(line[33:44]) == 0:
                    return pending
                pending = None
                continue
            if line[70:75] == " 1451" and line[75:80] == "    1":
                try:
                    if endf_float(line[0:11]) == want:
                        pending = endf_float(line[11:22])
                except ValueError:
                    continue
    raise ValueError(f"no ground-state MF=1/MT=451 HEAD for ZA={za} in {decay_path}")


# ---------------------------------------------------------------- running the binaries


def run_binary(binary, spec_path, out_path, log_path, timeout):
    """`binary run SPEC OUT` from the repository root. Returns the exit code; a timeout kills and reaps the child."""
    if out_path.exists():
        out_path.unlink()
    with open(log_path, "wb") as log:
        proc = subprocess.run([str(binary), "run", str(spec_path), str(out_path)], cwd=ROOT, env=dict(os.environ),
                              stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, timeout=timeout,
                              check=False)
    return proc.returncode


class Runner:
    def __init__(self, workdir, timeout):
        self.workdir, self.timeout, self.cache = workdir, timeout, {}

    def run(self, label, binary, spec_path, name):
        key = (label, str(spec_path))
        if key in self.cache:
            return self.cache[key]
        out = self.workdir / f"{name}.{label}.json"
        log = self.workdir / f"{name}.{label}.log"
        code = run_binary(binary, spec_path, out, log, self.timeout)
        if code != 0:
            raise RuntimeError(f"{label} exited {code} on {spec_path.name}; see {log}")
        raw = out.read_bytes()
        self.cache[key] = (raw, json.loads(raw))
        return self.cache[key]


# ---------------------------------------------------------------- comparison helpers


MS_FIELD = re.compile(rb'"ms"\s*:\s*[-+0-9.eE]+')


def diff_leaves(a, b, path=()):
    """Paths (tuples) at which two JSON values differ; a key present on one side only is a difference."""
    out = []
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                out.append(path + (k,))
            else:
                out.extend(diff_leaves(a[k], b[k], path + (k,)))
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(path)
        else:
            for i, (x, y) in enumerate(zip(a, b)):
                out.extend(diff_leaves(x, y, path + (i,)))
    elif a != b or type(a) is not type(b):
        out.append(path)
    return out


def fmt_path(path):
    return "/" + "/".join(str(p) for p in path)


def g4_excluded_reason(path):
    """Why a differing path is excluded from the G4 comparison, or None when it is not."""
    if path == ("ms",):
        return G4_EXCLUDED["ms"]
    last = path[-1] if path else ""
    if last == "dilution_overrides" and len(path) >= 2 and path[-2] == "shielding":
        return G4_EXCLUDED["dilution_overrides"]
    if isinstance(last, str) and (last in ("spec", "spec_sha256") or "fingerprint" in last):
        return G4_EXCLUDED["spec / spec_sha256 / *fingerprint*"]
    return None


def shielding_ledger(result):
    ledger = result.get("ledger")
    if not isinstance(ledger, dict) or not isinstance(ledger.get("shielding"), dict):
        raise ValueError("result has no ledger.shielding block")
    return ledger["shielding"]


# ---------------------------------------------------------------- G2


def gate_g2(runner, reference, candidate):
    out = {"specs": {}}
    ok = True
    for spec in (DEMO_SPEC, FNS_SPEC):
        name = spec.stem
        entry = {"spec_sha256": sha256_file(spec)}
        try:
            raw_ref, ref = runner.run("reference", reference, spec, name)
            raw_cand, cand = runner.run("candidate", candidate, spec, name)
            ref_x = {k: v for k, v in ref.items() if k != "ms"}
            cand_x = {k: v for k, v in cand.items() if k != "ms"}
            parsed_equal = ref_x == cand_x
            raw_equal = MS_FIELD.sub(b'"ms":0', raw_ref) == MS_FIELD.sub(b'"ms":0', raw_cand)
            entry.update({"raw_bytes_identical": raw_ref == raw_cand, "parsed_equal_without_ms": parsed_equal,
                          "bytes_equal_after_masking_ms": raw_equal, "reference_ms": ref.get("ms"),
                          "candidate_ms": cand.get("ms"),
                          "reference_output_sha256": hashlib.sha256(raw_ref).hexdigest(),
                          "candidate_output_sha256": hashlib.sha256(raw_cand).hexdigest()})
            if not parsed_equal:
                entry["first_differences"] = [fmt_path(p) for p in diff_leaves(ref_x, cand_x)[:10]]
            entry["pass"] = bool(parsed_equal and raw_equal)
        except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as error:
            entry["error"] = f"{type(error).__name__}: {error}"
            entry["pass"] = False
        out["specs"][name] = entry
        ok = ok and entry["pass"]
    out["pass"] = ok
    return out


# ---------------------------------------------------------------- G3


def candidate_files(nuclear_data):
    """(label, path, may_be_selected) for W-186 neutron evaluations in the local data tree, preference order."""
    nd = Path(nuclear_data)
    return [
        ("TENDL-2025", nd / "tendl-2025/files/n/n-W186.tendl", True),
        ("ENDF/B-VIII.0", nd / "endfb-viii.0-n-test/n_7443_74-W-186.dat", True),
        ("ENDF/B-VIII.1", nd / "endfb-viii.1-fns-arm/pristine/W186.endf", False),
        ("FENDL-3.2c", nd / "fendl-3.2c/endf/n_7443_74-W-186.endf", False),
        ("TENDL-2023", nd / "tendl-2023/files/n_074-W-186_7443.dat", False),
        ("TENDL-2017", nd / "tendl-2017/files/n_7443_74-W-186.dat", False),
    ]


def survey_candidates(nuclear_data):
    rows = []
    for label, path, selectable in candidate_files(nuclear_data):
        row = {"library": label, "path": str(path), "selectable": selectable, "usable": False}
        if not path.is_file():
            row["note"] = "absent"
            rows.append(row)
            continue
        row["sha256"] = sha256_file(path)
        row["bytes"] = path.stat().st_size
        try:
            parsed = parse_mf2_file(path)
            ranges, stopped = [], []
            for iso in parsed["isotopes"]:
                for r in iso["ranges"]:
                    ranges.append({"lru": r["lru"], "lrf": r["lrf"], "ev": [r["el"], r["eh"]]})
                if iso["stopped"]:
                    stopped.append(iso["stopped"])
            row["ranges"], row["stopped_at"] = ranges, stopped
            lines = resonance_lines(parsed, W186_ZA) if not any("LRF=7" in s for s in stopped) else []
            row["usable_resonances"] = len(lines)
            row["usable"] = bool(lines) and not any("LRF=7" in s or "unsupported" in s for s in stopped)
            if not row["usable"]:
                row["note"] = "no usable BW/Reich-Moore resonances (LRF=7 or no MF=2 resolved range)"
        except (ValueError, IndexError, OSError) as error:
            row["note"] = f"MF=2 parse failed: {error}"
        rows.append(row)
    return rows


def pick_resonance_file(rows, override):
    if override:
        path = Path(override).resolve()
        for row in rows:
            if Path(row["path"]) == path:
                return row
        parsed = parse_mf2_file(path)
        return {"library": "override", "path": str(path), "sha256": sha256_file(path), "usable": True,
                "usable_resonances": len(resonance_lines(parsed, W186_ZA))}
    for row in rows:
        if row["selectable"] and row["usable"]:
            return row
    raise ValueError("no usable TENDL-2025 or ENDF/B-VIII.0 W-186 resonance file under the nuclear-data tree")


def build_w186_spec(thickness_cm, resonance_path, resonance_sha):
    """The fns_fe_5min library, decay data, spectrum and options with a pure W-186 foil. Data paths absolute."""
    base = json.loads(FNS_SPEC.read_text())

    def absolute(p):
        return str(p if os.path.isabs(p) else (ROOT / p))

    spec = {
        "spec": base["spec"],
        "title": f"P129 G3: W-186 foil {thickness_cm} cm, geometry self-shielding",
        "projectile": base["projectile"],
        "library": {"path": absolute(base["library"]["path"]), "sha256": base["library"]["sha256"]},
        "decay": {k: absolute(v) for k, v in base["decay"].items()},
        "material": {"mass_g": 1.0, "basis": "wt_percent", "composition": {"W186": 100.0}},
        "spectrum": base["spectrum"],
        "schedule": [{"dt": "300.0 s", "flux": 1.0}],
        "options": base["options"],
        "self_shielding": {"geometry": {
            "shape": "foil", "length_cm": thickness_cm, "density_g_cm3": DENSITY_W_G_CM3,
            "resonance_files": [{"path": str(resonance_path), "sha256": resonance_sha}],
        }},
    }
    return spec


def compare_geometry(result, thickness_cm, lines, bounds, n_indep, resonance_sha):
    """One thickness: the candidate ledger against the independent recomputation."""
    led = shielding_ledger(result)
    geo = led.get("geometry")
    if not isinstance(geo, dict):
        raise ValueError("ledger.shielding has no geometry block")
    y = 1.5 * thickness_cm
    n_ledger = geo["number_density_per_cm3"]["W186"]
    mine = group_factors(lines, bounds, n_indep, y)
    mine_ledger_n = group_factors(lines, bounds, n_ledger, y)
    got = {row["group"]: row for row in geo["factors"]["W186"]}
    entry = {
        "thickness_cm": thickness_cm,
        "effective_length_cm": {"ledger": geo["effective_length_cm"], "expected": y},
        "number_density_per_cm3": {"ledger": n_ledger, "independent": n_indep,
                                   "relative_difference": rel_diff(n_ledger, n_indep)},
        "groups_ledger": len(got), "groups_independent": len(mine),
        "resonances_counted": sum(c for _, c in mine.values()),
    }
    problems = []
    if geo["shape"] != "foil" or geo["reactions"] != "all":
        problems.append(f"shape/reactions {geo['shape']}/{geo['reactions']}")
    if rel_diff(geo["effective_length_cm"], y) > 1e-15:
        problems.append("effective length")
    if rel_diff(geo["density_g_cm3"], DENSITY_W_G_CM3) > 1e-15:
        problems.append("density")
    if led.get("resonance_files_sha256") != [resonance_sha]:
        problems.append("resonance_files_sha256")
    if led.get("shielding_uncovered"):
        problems.append(f"shielding_uncovered {led['shielding_uncovered']}")
    if entry["number_density_per_cm3"]["relative_difference"] > MATCH_REL:
        problems.append("number density")
    if set(got) != set(mine):
        problems.append(f"group sets differ: ledger-only {sorted(set(got) - set(mine))[:10]}, "
                        f"independent-only {sorted(set(mine) - set(got))[:10]}")
    worst, worst_ledger_n, worst_group, count_mismatch, bounds_mismatch, applied_mismatch = 0.0, 0.0, None, [], [], []
    applied = led["applied_factors"].get("W186", {})
    for grp in sorted(set(got) & set(mine)):
        factor, count = mine[grp]
        row = got[grp]
        err = rel_diff(row["factor"], factor)
        if err > worst:
            worst, worst_group = err, grp
        worst_ledger_n = max(worst_ledger_n, rel_diff(row["factor"], mine_ledger_n[grp][0]))
        if row["resonances"] != count:
            count_mismatch.append({"group": grp, "ledger": row["resonances"], "independent": count})
        if rel_diff(row["energy_min_eV"], bounds[grp]) > 1e-12 or rel_diff(row["energy_max_eV"], bounds[grp + 1]) > 1e-12:
            bounds_mismatch.append(grp)
        if applied.get(str(grp)) != [row["factor"]] * 4:
            applied_mismatch.append(grp)
    entry.update({"worst_relative_error": worst, "worst_group": worst_group,
                  "worst_relative_error_using_ledger_density": worst_ledger_n,
                  "resonance_count_mismatches": count_mismatch[:10],
                  "group_bound_mismatches": bounds_mismatch[:10],
                  "applied_factor_mismatches": applied_mismatch[:10]})
    if worst > MATCH_REL:
        problems.append(f"factor mismatch, worst {worst:.3e} in group {worst_group}")
    if count_mismatch:
        problems.append("resonance counts")
    if bounds_mismatch:
        problems.append("group energy bounds")
    if applied_mismatch:
        problems.append("applied_factors differ from geometry factors")
    values = [row["factor"] for row in got.values()]
    entry["factor_range"] = [min(values), max(values)] if values else None
    entry["all_in_range"] = bool(values) and all(A2 - 1e-12 <= v <= A1 + 1e-12 for v in values)
    if not entry["all_in_range"]:
        problems.append("a factor lies outside [0.06, 1]")
    entry["problems"] = problems
    entry["pass"] = not problems
    return entry, {grp: row["factor"] for grp, row in got.items()}, got


def gate_g3(runner, candidate, args, workdir):
    out = {}
    rows = survey_candidates(args.nuclear_data)
    out["candidates"] = rows
    chosen = pick_resonance_file(rows, args.resonance_file)
    out["selected"] = {"library": chosen["library"], "path": chosen["path"], "sha256": chosen["sha256"],
                       "usable_resonances": chosen["usable_resonances"]}
    parsed = parse_mf2_file(chosen["path"])
    lines = resonance_lines(parsed, W186_ZA)
    out["file"] = {"awr": parsed["awr"], "usable_resonances": len(lines),
                   "ranges": [{"lru": r["lru"], "lrf": r["lrf"], "ev": [r["el"], r["eh"]]}
                              for iso in parsed["isotopes"] for r in iso["ranges"]],
                   "energy_range_eV": [min(r["E"] for r in lines), max(r["E"] for r in lines)]}
    # Constants.
    k_codata = math.sqrt(2.0 * MN_C2_MEV * 1e6) / (HBAR_C_MEV_FM * 1e6) * 10.0
    out["constants"] = {"K_WAVE_endf": K_WAVE, "K_WAVE_codata_derived": k_codata,
                        "relative_gap": rel_diff(K_WAVE, k_codata), "sanity_limit": K_WAVE_SANITY_REL,
                        "sigmoid": {"A1": A1, "A2": A2, "z0": Z0, "p": P}, "avogadro": AVOGADRO,
                        "neutron_mass_amu": NEUTRON_MASS_AMU}
    # Group structure: the demo spec's custom boundaries (the library's 709 groups), cross-checked per group against
    # the ledger's reported energy ranges.
    bounds = json.loads(DEMO_SPEC.read_text())["spectrum"]["boundaries_eV"]
    if any(b <= a for a, b in zip(bounds, bounds[1:])):
        raise ValueError("group boundaries from shielding_demo.json are not ascending")
    out["group_boundaries"] = {"source": "examples/shielding_demo.json spectrum.boundaries_eV", "count": len(bounds)}
    # Number density from density and atoms per gram: N = rho * NA / (AWR_decay * m_n).
    base = json.loads(FNS_SPEC.read_text())
    decay_path = base["decay"]["primary"]
    decay_path = decay_path if os.path.isabs(decay_path) else str(ROOT / decay_path)
    awr = decay_awr(decay_path, W186_ZA)
    mass_u = awr * NEUTRON_MASS_AMU
    atoms_per_gram = AVOGADRO * (100.0 / 100.0) / mass_u
    out["material"] = {"nuclide": "W186", "decay_awr": awr, "atomic_mass_u": mass_u,
                       "atoms_per_gram": atoms_per_gram, "density_g_cm3": DENSITY_W_G_CM3,
                       "decay_library": decay_path}
    n_indep = atoms_per_gram * DENSITY_W_G_CM3
    per_thickness, factors, ok = {}, {}, True
    for t in FOIL_THICKNESS_CM:
        tag = f"{t}".replace(".", "p")
        spec = build_w186_spec(t, chosen["path"], chosen["sha256"])
        spec_path = workdir / f"w186_foil_{tag}.json"
        spec_path.write_text(json.dumps(spec, indent=1) + "\n")
        entry = {"spec": str(spec_path), "spec_sha256": sha256_file(spec_path)}
        try:
            _, result = runner.run("candidate", candidate, spec_path, f"w186_foil_{tag}")
            cmp_, factors[t], _ = compare_geometry(result, t, lines, bounds, n_indep, chosen["sha256"])
            entry.update(cmp_)
        except (RuntimeError, ValueError, KeyError, OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as error:
            entry["error"] = f"{type(error).__name__}: {error}"
            entry["pass"] = False
        per_thickness[str(t)] = entry
        ok = ok and entry["pass"]
    out["thicknesses"] = per_thickness
    # Monotonic with thickness, and not vacuous.
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
    out["monotonic_with_thickness"] = mono
    out["k_wave_sanity"] = rel_diff(K_WAVE, k_codata) < K_WAVE_SANITY_REL
    out["pass"] = bool(ok and mono["pass"] and out["k_wave_sanity"])
    out["_factors"] = {str(t): {str(g): v for g, v in f.items()} for t, f in factors.items()}
    return out


# ---------------------------------------------------------------- G4


def applied_for(result, nuclide):
    return shielding_ledger(result)["applied_factors"][nuclide]


def gate_g4(runner, candidate, workdir):
    out = {"excluded_keys": G4_EXCLUDED}
    base_raw, base = runner.run("candidate", candidate, DEMO_SPEC, DEMO_SPEC.stem)
    led = shielding_ledger(base)
    sigma0 = led["sigma0_eff_b"]
    if not sigma0:
        raise ValueError("base run reports no sigma0_eff_b: no covered nuclide")
    nuclide = sorted(sigma0)[0]
    computed = sigma0[nuclide]
    rows = applied_for(base, nuclide)
    # The covered group with the largest capture suppression: the most room to move toward one.
    group = min(rows, key=lambda g: rows[g][3])
    base_factors = rows[group]
    out.update({"nuclide": nuclide, "group": int(group), "computed_sigma0_b": computed,
                "base_factors": base_factors, "covered_groups": len(rows)})
    if min(base_factors) >= 1.0:
        raise ValueError("every covered group has factor 1; the tenfold check would be vacuous")

    def variant(label, value):
        spec = json.loads(DEMO_SPEC.read_text())
        spec["self_shielding"]["dilution_overrides"] = {nuclide: {str(int(group)): value}}
        path = workdir / f"shielding_demo_override_{label}.json"
        path.write_text(json.dumps(spec, indent=1) + "\n")
        _, result = runner.run("candidate", candidate, path, f"shielding_demo_override_{label}")
        return path, result

    # (a) override equal to the computed sigma0.
    path_eq, equal = variant("equal", computed)
    diffs = diff_leaves(base, equal)
    excluded, unexplained = [], []
    for p in diffs:
        reason = g4_excluded_reason(p)
        if reason:
            excluded.append({"path": fmt_path(p), "reason": reason})
        else:
            unexplained.append(fmt_path(p))
    echoed = shielding_ledger(equal).get("dilution_overrides")
    echo_ok = echoed == {nuclide: {str(int(group)): computed}}
    out["equal_override"] = {
        "spec": str(path_eq), "excluded_differences": excluded, "unexplained_differences": unexplained[:20],
        "ledger_echoes_override": echo_ok, "pass": bool(not unexplained and echo_ok),
    }
    # (b) ten times the computed sigma0.
    path_x10, tenfold = variant("x10", 10.0 * computed)
    f10 = applied_for(tenfold, nuclide)
    channel_rows = []
    closer_ok = True
    for c, name in enumerate(("total", "elastic", "fission", "capture")):
        f0, f1 = base_factors[c], f10[group][c]
        closer = abs(1.0 - f1) < abs(1.0 - f0) if abs(1.0 - f0) > 1e-12 else abs(1.0 - f1) <= 1e-12
        channel_rows.append({"channel": name, "base": f0, "x10": f1, "closer_to_one": closer})
        # The capture channel carries the suppression; the others must at least not move away.
        closer_ok = closer_ok and (closer if name == "capture" else abs(1.0 - f1) <= abs(1.0 - f0) + 1e-12)
    others_same = all(f10[g] == rows[g] for g in rows if g != group)
    out["tenfold_override"] = {"spec": str(path_x10), "sigma0_b": 10.0 * computed, "channels": channel_rows,
                               "other_groups_unchanged": others_same,
                               "pass": bool(closer_ok and others_same)}
    out["pass"] = bool(out["equal_override"]["pass"] and out["tenfold_override"]["pass"])
    return out


# ---------------------------------------------------------------- G5


def gate_g5(g3, fispact_path):
    out = {"report_only": True, "pass": None}
    factors = g3.get("_factors") if g3 else None
    if not fispact_path:
        out["available"] = False
        out["note"] = ("No tabulated FISPACT-II geometry factor for a foil case is available in this checkout "
                       "(no FISPACT-II manual table or output was supplied via --fispact-factors); nothing to "
                       "tabulate beside ACTINV's factors. Reported, not gated.")
        if factors:
            out["actinv_minimum_factor_per_thickness"] = {
                t: {"group": int(min(f, key=f.get)), "factor": min(f.values())} for t, f in factors.items() if f}
        return out
    table = json.loads(Path(fispact_path).read_text())
    out["available"] = True
    out["source"] = table.get("source")
    rows = []
    for case in table.get("cases", []):
        t = str(case["thickness_cm"])
        f = (factors or {}).get(t, {})
        grp = case.get("group")
        rows.append({"thickness_cm": case["thickness_cm"], "group": grp, "fispact_factor": case["factor"],
                     "actinv_factor": f.get(str(grp)) if grp is not None else None})
    out["rows"] = rows
    return out


# ---------------------------------------------------------------- driver


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reference", required=True, help="release binary of the base commit")
    ap.add_argument("--candidate", required=True, help="release binary of the implementation commit")
    ap.add_argument("--workdir", default="target/p129-work")
    ap.add_argument("--out", default="results/p129_shielding.json")
    ap.add_argument("--gates", default="G2,G3,G4,G5", help="comma-separated subset; the verdict is INCOMPLETE "
                    "unless G2, G3 and G4 all run")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S, help="seconds per run")
    ap.add_argument("--nuclear-data", default=str(Path.home() / "nuclear-data"))
    ap.add_argument("--resonance-file", help="W-186 ENDF-6 file to use instead of the surveyed choice")
    ap.add_argument("--fispact-factors", help="JSON {source, cases:[{thickness_cm, group, factor}]} for G5")
    args = ap.parse_args(argv)

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
    workdir = Path(args.workdir)
    workdir = (workdir if workdir.is_absolute() else ROOT / workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    record = {
        "protocol": "ACTINV-P129", "protocol_sha256": sha256_file(PROTOCOL),
        "reference": {"path": str(reference), "sha256": sha256_file(reference)},
        "candidate": {"path": str(candidate), "sha256": sha256_file(candidate)},
        "specs": {"shielding_demo": sha256_file(DEMO_SPEC), "fns_fe_5min": sha256_file(FNS_SPEC)},
        "timeout_s": args.timeout, "gates_run": sorted(gates), "gates": {},
    }
    runner = Runner(workdir, args.timeout)
    g3_result = None

    def guarded(name, fn):
        try:
            return fn()
        except (RuntimeError, ValueError, KeyError, IndexError, OSError, subprocess.TimeoutExpired,
                json.JSONDecodeError) as error:
            return {"error": f"{type(error).__name__}: {error}", "pass": False}

    if "G2" in gates:
        record["gates"]["G2"] = guarded("G2", lambda: gate_g2(runner, reference, candidate))
    if "G3" in gates:
        g3_result = guarded("G3", lambda: gate_g3(runner, candidate, args, workdir))
        record["gates"]["G3"] = g3_result
    if "G4" in gates:
        record["gates"]["G4"] = guarded("G4", lambda: gate_g4(runner, candidate, workdir))
    if "G5" in gates:
        record["gates"]["G5"] = guarded("G5", lambda: gate_g5(g3_result, args.fispact_factors))
    if g3_result is not None:
        g3_result.pop("_factors", None)

    required = ("G2", "G3", "G4")
    if not all(g in gates for g in required):
        verdict = "INCOMPLETE"
    elif all(record["gates"][g].get("pass") is True for g in required):
        verdict = "PASS"
    else:
        verdict = "FAIL"
    record["verdict"] = verdict
    out = Path(args.out)
    out = out if out.is_absolute() else ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    for g in sorted(record["gates"]):
        print(f"P129 {g}: {record['gates'][g].get('pass')}")
    print(f"P129: {verdict} ({out})")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
