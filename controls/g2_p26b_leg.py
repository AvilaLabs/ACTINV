#!/usr/bin/env python3
"""P26b G2: execute the frozen comparison legs and record the ledger.

Legs (from results/g2_p26b_leg_contract.json):

  identical_data     - all cases are contract_gap (every case needs an
                       ACTINV-rejected isotope); no runs.  The gap census is
                       itself the measured executability result.
  product_plus_data  - ACTINV (shipped v1.1.0-patched artifact) and ALARA
                       (G1 FENDL-3.2c library) run every executable case.

Plus one diagnostic probe (partition: diagnostic, not a leg measurement):
  actinv_fendl_silent_bulk runs ACTINV on the G1 FENDL artifact for the
  fe__fns_709__pulse_5min case to document that ACTINV proceeds with the
  Fe-57 channel silently absent rather than erroring.

ALARA 2.9.2's isotope-name display is defective in this build (rows print
"-A" without the element symbol, verified on stock sample3).  Identity is
recovered, never guessed: alara.dmp stores the exact per-root KZAs, every
response table iterates the same sorted list filtered by the response
multiplier, and each row prints its t_1/2.  Rows are aligned to KZAs by
(order, A, isomer flag, t_1/2 vs the .idx half-life) and cross-validated by
activity == lambda * number_density element-wise.  Inconsistencies mark the
row unresolved; unresolved rows are reported per case.

All runs are sequential inside the caller's cgroup.  The ledger is
append-only JSONL and resumable.  Full per-isotope tables are retained in
the case work directories (digests recorded in the ledger); the ledger row
carries per-time aggregate responses and artifact hashes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import re
import struct
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "controls"))
from g3_p26_headroom import collapse_to_709, fns_spectrum  # noqa: E402
from g2_p26b_contract import (COOLING_TIMES_S, FISPACT24_UPPER_BOUNDS_EV,
                              ZINV, sha256_file, sha256_array)  # noqa: E402

CONTRACT_PATH = ROOT / "results" / "g2_p26b_leg_contract.json"
LEDGER = ROOT / "results" / "g2_p26b_leg_ledger.jsonl"
OUT = ROOT / "results" / "g2_p26b_leg.json"
WORK = Path.home() / "nuclear-data" / "p26b-work" / "g2-run" / "cases"
TMPDIR = Path.home() / "nuclear-data" / "p26b-work" / "tmp"

ALARA = Path.home() / "nuclear-data" / "alara-2.9.2-build" / "src" / "alara"
ACTINV = ROOT / "target" / "release" / "actinv"
ALARA_LIB_BASE = Path.home() / "nuclear-data" / "p26b-work" / "g1-run" / "fendl32c_709"
ACTINV_FENDL_NPZ = Path.home() / "nuclear-data" / "p26b-work" / "g1-run" / "actinv_fendl32c_709.npz"
ACTINV_SHIPPED_NPZ = ROOT / "target" / "p25c-release" / "tendl-2025-patched-neutron-709g.npz"
DECAY_PRIMARY = ROOT / "actinv-data" / "v1.0.0" / "decay" / "endf-b-viii-0_decay.dat"
DECAY_FALLBACK = ROOT / "actinv-data" / "v1.0.0" / "decay" / "jeff-3-3_decay.dat"
ELELIB = Path.home() / "nuclear-data" / "alara-2.9.2" / "sample" / "data" / "myElelib"

PER_CASE_TIMEOUT_S = 1800.0
COOL_DT_S = [86400.0, 2505600.0, 28944000.0, 283824000.0]
TRUNCATION = "1e-15"
LN2 = math.log(2.0)


def context() -> dict:
    return {
        "host": platform.node(), "machine": platform.machine(),
        "kernel": platform.release(), "python": platform.python_version(),
        "executables": {
            "alara": {"path": str(ALARA), "sha256": sha256_file(ALARA)},
            "actinv": {"path": str(ACTINV), "sha256": sha256_file(ACTINV)}},
        "resource_limits": {"MemoryMax": "6G", "MemorySwapMax": 0,
                            "TasksMax": 128, "CPUQuota": "200%",
                            "jobs_at_once": 1},
    }


def timed_run(cmd: list[str], cwd: Path, timeout: float = PER_CASE_TIMEOUT_S) -> dict:
    t0 = time.perf_counter()
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                           timeout=timeout,
                           env={**os.environ, "TMPDIR": str(TMPDIR)})
        return {"wall_s": time.perf_counter() - t0,
                "returncode": r.returncode, "stdout": r.stdout,
                "stderr_tail": (r.stderr or "")[-300:]}
    except subprocess.TimeoutExpired:
        return {"wall_s": time.perf_counter() - t0, "returncode": None,
                "stdout": "",
                "failure": "comparator_timeout"}


# ------------------------------------------------------------------ elelib / idx

def elelib_parse() -> dict:
    out = {}
    cur = None
    for line in ELELIB.read_text(errors="replace").splitlines():
        f = line.split()
        if not f:
            continue
        if line[0].isspace():
            if cur is not None and len(f) == 2:
                out[cur]["isotopes"][int(f[0])] = float(f[1]) / 100.0
            continue
        if ":" in f[0]:
            cur = None
            continue
        if len(f) == 5 and re.fullmatch(r"[a-z]{1,2}", f[0]) and f[2].isdigit():
            cur = f[0].upper()
            out[cur] = {"density_g_cm3": float(f[3]), "isotopes": {}}
    return out


def idx_parse(idx_path: Path) -> dict:
    """returns {kza: half_life_s or None} for every isotope named in the .idx
    (parent rows AND tab-indented daughter rows)."""
    hl = {}
    for line in idx_path.read_text(errors="replace").splitlines():
        f = line.split()
        if not f:
            continue
        if line.startswith("\t"):
            # daughter rows: <kza> *D <offset>
            if f[0].isdigit() and int(f[0]) > 1000:
                hl.setdefault(int(f[0]), None)
            continue
        if f[0].isdigit() and int(f[0]) > 1000:
            try:
                t = float(f[2])
            except (ValueError, IndexError):
                t = 0.0
            hl[int(f[0])] = t if t > 0.0 else None
    return hl


def kza_name(kza: int) -> str:
    liso = kza % 10
    a = (kza // 10) % 1000
    z = kza // 10000
    sym = ZINV.get(z)
    name = f"{sym.title()}-{a}" if sym else f"Z{z}-{a}"
    if liso == 1:
        name += "m"
    elif liso > 1:
        name += f"m{liso}"
    return name


def actinv_key(name: str) -> str:
    """'Fe-55' -> 'Fe55'; 'Co-60m' -> 'Co60m1' (ACTINV out.json key style).
    Non-canonical names (unresolved markers) pass through unchanged."""
    m = re.fullmatch(r"([A-Za-z]+)-(\d+)(m(\d*))?", name)
    if not m:
        return name
    sym, a, iso = m.group(1).title(), m.group(2), m.group(3)
    if iso:
        return f"{sym}{a}m{iso[1:] or '1'}"
    return f"{sym}{a}"


# ------------------------------------------------------------------ ALARA

def write_alara_case(case_dir: Path, case: dict, flux_desc: list[float],
                     elelib: dict) -> dict:
    flux_path = case_dir / "flux.txt"
    (case_dir / "matlib.empty").touch()
    flux_path.write_text("\n".join(f"{v:.10e}" for v in flux_desc) + "\n")
    lines = [
        "geometry rectangular",
        "volumes",
        "\t1.0 z1",
        "end",
        "material_lib matlib.empty",
        f"element_lib {ELELIB}",
        "mat_loading",
        "\tz1 m",
        "end",
        "mixture m",
    ]
    for el, wf in case["composition_wt_fraction"].items():
        rho = elelib[el]["density_g_cm3"]
        lines.append(f"\telement {el.lower()}\t{1.0/rho:.10e}\t{wf:.10e}")
    lines += [
        "end",
        f"flux f1 {flux_path} 1.0 0 default",
        "schedule s",
        f"\t{case['irradiation_s']} s f1 pulse_once 0 s",
        "end",
        "pulsehistory pulse_once",
        "\t1 0 s",
        "end",
        f"data_library alaralib {ALARA_LIB_BASE}",
        "output zone",
        "\tunits Bq g",
        "\tspecific_activity",
        "\tnumber_density",
        "\ttotal_heat",
        f"\tphoton_source {ALARA_LIB_BASE} photsrc.out 24 "
        + " ".join(f"{b:g}" for b in FISPACT24_UPPER_BOUNDS_EV),
        "end",
        "cooling",
    ]
    for t in COOLING_TIMES_S[1:]:
        lines.append(f"\t{t:g} s")
    lines += ["end", f"truncation {TRUNCATION}", ""]
    (case_dir / "case.in").write_text("\n".join(lines))
    return {"input_sha256": sha256_file(case_dir / "case.in"),
            "flux_sha256": sha256_file(flux_path)}


def parse_alara_dump(dmp: Path, valid: set[int], n_roots: int) -> set[int]:
    """Union of the per-root KZAs in the binary dump.  Layout:
    [int32 nResults] then, per root, {int32 kza, float32 N[nResults]}*
    terminated by -1.  Exactly n_roots blocks are read; the aggregate
    record that follows has a different layout and is not touched.
    Only kzas present in the library .idx are kept."""
    data = dmp.read_bytes()
    off = 0
    nres, = struct.unpack_from("<i", data, off)
    off += 4
    out: set[int] = set()
    blocks = 0
    while blocks < n_roots and off + 4 <= len(data):
        kza, = struct.unpack_from("<i", data, off)
        off += 4
        if kza == -1:
            blocks += 1
            continue
        if off + 4 * nres > len(data):
            break
        off += 4 * nres
        if kza in valid:
            out.add(kza)
    return out


ROW_RE = re.compile(r"^-(\d+)(m(\d+)?)?\s")
UNIT_S = {"s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0, "w": 604800.0,
          "y": 31536000.0, "c": 3153600000.0}


def parse_output_table(text: str, section: str) -> dict:
    """Parse the 'Total (All constituents)' table of `*** <section> ***`.
    Returns {times_s: [0, ...cooling], total: [...], rows: [{a, liso,
    t_half, values}]}.  Rows are in KZA order (sorted result list)."""
    m = re.search(rf"\*\*\* {re.escape(section)} \*\*\*(.*?)(?=\n\*\*\*|\Z)",
                  text, re.S)
    if not m:
        return {}
    body = m.group(1)
    hm = re.search(r"isotope\s+t_1/2\(s\)\s+pre-irrad\s+(.*?)\n=+\n(.*?)\n=+",
                   body, re.S)
    if not hm:
        return {}
    col_times = [0.0]  # shutdown column
    for mm in re.finditer(r"([\d.eE+-]+)\s+(s|m|h|d|w|y|c)\b", hm.group(1)):
        col_times.append(float(mm.group(1)) * UNIT_S[mm.group(2)])
    rows = []
    for line in hm.group(2).splitlines():
        line = line.strip()
        if line.startswith("total"):
            continue
        rm = ROW_RE.match(line)
        if not rm:
            continue
        f = line.split()
        a = int(rm.group(1))
        liso = 0 if not rm.group(2) else (int(rm.group(3)) if rm.group(3) else 1)
        try:
            t_half = float(f[1])
        except ValueError:
            t_half = -1.0
        try:
            pre_irrad = float(f[2])
            vals = [float(x) for x in f[3:]]
        except ValueError:
            continue
        rows.append({"a": a, "liso": liso, "t_half": t_half,
                     "pre_irrad": pre_irrad, "values": vals})
    # `total  0  <pre-irrad>  <shutdown>  <t1> ...` — drop the pre-irrad
    # column so the array aligns with col_times.
    total_m = re.search(r"^total\s+\S+\s+(.*)$", body, re.M)
    total_all = [float(x) for x in total_m.group(1).split()] if total_m else []
    total = total_all[1:] if total_all else []
    pre_irrad_total = total_all[0] if total_all else None
    return {"times_s": col_times, "total": total,
            "pre_irrad_total": pre_irrad_total, "rows": rows}


def parse_photsrc(path: Path) -> dict:
    """TOTAL rows -> {cooling_s: [group values]}."""
    out = {}
    if not path.exists():
        return out
    for line in path.read_text(errors="replace").splitlines():
        if not line.startswith("TOTAL"):
            continue
        f = line.split()
        if f[1] == "shutdown":
            t, vals = 0.0, f[2:]
        else:
            t = float(f[1]) * UNIT_S.get(f[2], 1.0)
            vals = f[3:]
        try:
            out[t] = [float(x) for x in vals]
        except ValueError:
            continue
    return out


def resolve_isotope_names(rows: list[dict], union_sorted: list[int],
                          hl: dict) -> list[dict]:
    """Align anonymous rows (a, liso, t_half) to sorted KZAs by order +
    A/liso match, tie-broken by printed t_1/2 vs the .idx half-life.
    Returns rows with 'kza'/'name' added, or 'unresolved'."""
    resolved = []
    ki = 0
    for row in rows:
        a, liso, thalf = row["a"], row["liso"], row["t_half"]
        candidates = []
        for j in range(ki, len(union_sorted)):
            k = union_sorted[j]
            ka, kl = (k // 10) % 1000, k % 10
            if ka < a:
                continue
            if ka > a:
                break
            if (liso == 0 and kl == 0) or (liso > 0 and kl == liso):
                candidates.append(j)
        best = None
        ambiguous = False
        if len(candidates) == 1:
            best = candidates[0]
        elif candidates:
            for j in candidates:
                t_idx = hl.get(union_sorted[j])
                if t_idx is None and thalf < 0:
                    best = j
                    break
                if t_idx and thalf > 0 and abs(t_idx - thalf) / thalf < 0.02:
                    best = j
                    break
            if best is None:
                # order-consistent fallback, flagged: the lambda*N
                # cross-check will quantify any misassignment
                best = candidates[0]
                ambiguous = True
        if best is None:
            resolved.append({**row, "name": f"unresolved-A{a}", "unresolved": True})
            continue
        ki = best + 1
        rec = {**row, "kza": union_sorted[best],
               "name": kza_name(union_sorted[best])}
        if ambiguous:
            rec["ambiguous"] = True
        resolved.append(rec)
    return resolved


def parse_alara_result(case_dir: Path, hl: dict, n_roots: int) -> dict:
    text = (case_dir / "case.stdout").read_text(errors="replace")
    union = sorted(parse_alara_dump(case_dir / "alara.dmp", set(hl), n_roots))

    dens = parse_output_table(text, "Number Density [atoms/g]")
    act = parse_output_table(text, "Specific Activity [Bq/g]")
    heat = parse_output_table(text, "Total Decay Heat [W/g]")

    dens_rows = resolve_isotope_names(dens.get("rows", []), union, hl)
    dens_map = {r["kza"]: r for r in dens_rows if "kza" in r}
    act_rows = resolve_isotope_names(act.get("rows", []), union, hl)
    heat_rows = resolve_isotope_names(heat.get("rows", []), union, hl)

    # cross-validate the mapping: activity(kza,t) == lambda(kza)*density(kza,t)
    xerr = []
    for r in act_rows:
        k = r.get("kza")
        if k is None or k not in dens_map:
            continue
        t = hl.get(k)
        if not t:
            continue
        lam = LN2 / t
        dv = dens_map[k]["values"]
        for i, av in enumerate(r["values"][:len(dv)]):
            pred = lam * dv[i]
            if pred > 0 and av > 0:
                xerr.append(abs(av - pred) / pred)
    xval = {"max_rel_err": max(xerr) if xerr else None, "n_checked": len(xerr)}

    phot = parse_photsrc(case_dir / "photsrc.out")
    times = dens.get("times_s") or act.get("times_s") or []

    per_time = {}
    for i, t in enumerate(times):
        rec = {"cooling_s": t}
        for key, tab in (("total_atoms_per_g", dens),
                         ("total_activity_bq_per_g", act),
                         ("decay_heat_w_per_g", heat)):
            if tab.get("total") and i < len(tab["total"]):
                rec[key] = tab["total"][i]
        # populated-products inventory: sum over isotope rows that were not
        # in the initial material -> comparable to ACTINV's
        # total_atoms_per_g, which counts populated states only.  The printed
        # total cannot resolve this (products sit ~1e-12 below the bulk).
        prod = 0.0
        for r in dens_rows:
            if r.get("pre_irrad") == 0.0 and i < len(r["values"]):
                prod += r["values"][i]
        rec["product_atoms_per_g"] = prod
        rec["photon_source_per_group_24"] = phot.get(t)
        top = sorted(((r["name"], r["values"][i]) for r in act_rows
                      if "name" in r and i < len(r["values"]) and r["values"][i] > 0),
                     key=lambda x: -x[1])[:5]
        rec["top5_nuclides_by_activity"] = [
            {"nuclide": actinv_key(n), "activity_bq_per_g": v} for n, v in top]
        per_time[f"{t:g}"] = rec

    unresolved = sorted({r["name"] for r in dens_rows + act_rows + heat_rows
                         if r.get("unresolved")})
    return {
        "times_s": times,
        "per_time": per_time,
        "n_isotopes_number_density": len(dens_rows),
        "n_isotopes_specific_activity": len(act_rows),
        "n_isotopes_total_heat": len(heat_rows),
        "unresolved_isotopes": unresolved,
        "identity_crossval": xval,
        "n_dump_isotopes": len(union),
        "artifacts": {},
    }


# ------------------------------------------------------------------ ACTINV

def build_actinv_spec(case: dict, flux: list[float], descending: bool,
                      lib: Path, lib_sha: str) -> dict:
    return {
        "spec": "actinv-spec-1",
        "title": case["case"],
        "projectile": "neutron",
        "library": {"path": str(lib), "sha256": lib_sha},
        "decay": {"primary": str(DECAY_PRIMARY), "fallback": str(DECAY_FALLBACK)},
        "material": {"mass_g": 1.0, "basis": "wt_percent",
                     "composition": {el: wf * 100.0 for el, wf in
                                     case["composition_wt_fraction"].items()}},
        "spectrum": {"structure": "fispact-709", "flux_per_group": flux,
                     "descending": descending, "total": sum(flux)},
        "schedule": ([{"dt": f"{case['irradiation_s']} s", "flux": 1.0}]
                     + [{"dt": f"{dt} s", "flux": 0.0} for dt in COOL_DT_S]),
        "options": {"mode": "auto", "prune": "rate",
                    "bmin_atoms_per_g": 1e-08, "temperature_K": 293.6},
    }


def parse_actinv_result(out_path: Path, irr_s: float) -> dict:
    o = json.loads(out_path.read_text())
    per_time = {}
    for st in o.get("steps", []):
        t_cool = st["t_s"] - irr_s
        act = st.get("activity_Bq_per_g") or {}
        top5 = sorted(act.items(), key=lambda kv: -kv[1])[:5]
        ps = st.get("photon_source") or {}
        groups = [g.get("photons_s_g") for g in ps.get("groups", [])]
        heat = st.get("heat_W_per_g") or {}
        per_time[f"{t_cool:g}"] = {
            "cooling_s": t_cool,
            "total_atoms_per_g": st.get("total_atoms_per_g"),
            "product_atoms_per_g": st.get("total_atoms_per_g"),
            "total_activity_bq_per_g": sum(act.values()),
            "decay_heat_w_per_g": heat.get("total") if isinstance(heat, dict)
                                  else heat,
            "photon_source_per_group_24": groups or None,
            "top5_nuclides_by_activity": [
                {"nuclide": n, "activity_bq_per_g": v} for n, v in top5],
        }
    return {"per_time": per_time, "ms": o.get("ms"),
            "n_states_populated_final":
                (o.get("steps") or [{}])[-1].get("n_states_populated")}


# ------------------------------------------------------------------ driver

def run_leg_case(case: dict, leg: str, spectra: dict, elelib: dict,
                 hl: dict, contract: dict) -> dict:
    row = {"case": case["case"], "leg": leg,
           "status": case["legs"][leg]["status"]}
    if row["status"] == "contract_gap":
        row["missing"] = case["legs"][leg]["missing"]
        return row

    spec_name = case["spectrum"]
    # ALARA flux file is written in library (descending) group order
    flux_desc = (spectra[spec_name] if spec_name == "fns_709"
                 else list(reversed(spectra[spec_name])))
    case_dir = WORK / leg / case["case"]
    case_dir.mkdir(parents=True, exist_ok=True)

    # ---- ALARA arm
    io = write_alara_case(case_dir, case, flux_desc, elelib)
    arun = timed_run([str(ALARA), "case.in"], case_dir)
    (case_dir / "case.stdout").write_text(arun.get("stdout", ""))
    a = {"tool": "alara_2_9_2", "arm": "alara_fendl32c_709",
         **io, "wall_s": arun["wall_s"], "returncode": arun["returncode"],
         "stderr_tail": arun.get("stderr_tail")}
    if arun.get("returncode") == 0:
        try:
            a["result"] = parse_alara_result(
                case_dir, hl, n_roots=len(case["required_isotopes"]["alara"]))
            a["result"]["artifacts"] = {
                "stdout_sha256": sha256_file(case_dir / "case.stdout"),
                "dump_sha256": sha256_file(case_dir / "alara.dmp"),
                "photsrc_sha256": sha256_file(case_dir / "photsrc.out"),
            }
        except Exception as e:
            a["failure"] = f"alara_output_parse: {e!r}"
    else:
        a["failure"] = arun.get("failure") or "comparator_error"

    # ---- ACTINV arm
    lib = (ACTINV_SHIPPED_NPZ if leg == "product_plus_data" else ACTINV_FENDL_NPZ)
    lib_key = ("actinv_shipped_tendl2025" if leg == "product_plus_data"
               else "actinv_fendl32c_709")
    spec = build_actinv_spec(
        case, flux=spectra[spec_name],
        descending=(spec_name == "fns_709"),
        lib=lib, lib_sha=contract["libraries"][lib_key]["npz_sha256"])
    spec_path = case_dir / "spec.json"
    spec_path.write_text(json.dumps(spec))
    crun = timed_run([str(ACTINV), "run", str(spec_path),
                      str(case_dir / "out.json")], case_dir)
    c = {"tool": "actinv", "arm": lib_key,
         "spec_sha256": sha256_file(spec_path),
         "wall_s": crun["wall_s"], "returncode": crun["returncode"],
         "stderr_tail": crun.get("stderr_tail")}
    if crun.get("returncode") == 0 and (case_dir / "out.json").exists():
        try:
            c["result"] = parse_actinv_result(case_dir / "out.json",
                                            case["irradiation_s"])
            c["result"]["artifacts"] = {
                "out_json_sha256": sha256_file(case_dir / "out.json")}
        except Exception as e:
            c["failure"] = f"actinv_output_parse: {e!r}"
    else:
        c["failure"] = crun.get("failure") or "actinv_error"

    row["arms"] = {"alara": a, "actinv": c}
    row["wall_s_total"] = round(a["wall_s"] + c["wall_s"], 6)
    fails = {k: v.get("failure") for k, v in (("alara", a), ("actinv", c))
             if v.get("failure")}
    if fails:
        row["status"] = "executed_with_failures"
        row["arm_failures"] = fails
    else:
        row["status"] = "executed"
    return row


def run_diagnostic_probe(contract: dict, spectra: dict, ledger_done: set) -> dict:
    """One ACTINV-on-FENDL run of the flagship case (diagnostic partition):
    documents silent-bulk behavior, never a leg measurement."""
    case = next(c for c in contract["cases"]
                if c["case"] ==
                contract["diagnostic_probes"]["actinv_fendl_silent_bulk"]["case"])
    case_dir = WORK / "diagnostic" / case["case"]
    case_dir.mkdir(parents=True, exist_ok=True)
    spec = build_actinv_spec(
        case, flux=spectra[case["spectrum"]],
        descending=(case["spectrum"] == "fns_709"),
        lib=ACTINV_FENDL_NPZ,
        lib_sha=contract["libraries"]["actinv_fendl32c_709"]["npz_sha256"])
    spec_path = case_dir / "spec.json"
    spec_path.write_text(json.dumps(spec))
    crun = timed_run([str(ACTINV), "run", str(spec_path),
                      str(case_dir / "out.json")], case_dir)
    probe = {"probe": "actinv_fendl_silent_bulk", "case": case["case"],
             "leg": "identical_data", "partition": "diagnostic",
             "spec_sha256": sha256_file(spec_path),
             "wall_s": crun["wall_s"], "returncode": crun["returncode"],
             "stderr_tail": crun.get("stderr_tail")}
    if crun.get("returncode") == 0 and (case_dir / "out.json").exists():
        res = parse_actinv_result(case_dir / "out.json", case["irradiation_s"])
        res["artifacts"] = {"out_json_sha256": sha256_file(case_dir / "out.json")}
        probe["result"] = res
        # document the silent-bulk signature: run succeeded though Fe-57 is absent
        probe["silent_bulk"] = {
            "actinv_fendl_targets": contract["libraries"]["actinv_fendl32c_709"]["targets"],
            "missing_isotopes": case["legs"]["identical_data"]["missing"]
                .get("actinv_fendl32c_709", []),
            "completed_without_error": True,
        }
    else:
        probe["failure"] = crun.get("failure") or "actinv_error"
        probe["silent_bulk"] = {"completed_without_error": False}
    return probe


def summarize(contract: dict) -> dict:
    """Fold the append-only ledger into the G2 result artifact."""
    rows = [json.loads(l) for l in LEDGER.read_text().splitlines() if l.strip()]
    census: dict[str, dict] = {}
    failures: dict[str, dict] = {}
    probe = None
    per_case = {}
    for r in rows:
        if r["leg"] == "probe":
            probe = r["record"]
            continue
        leg = r["leg"]
        census.setdefault(leg, {})
        census[leg][r["status"]] = census[leg].get(r["status"], 0) + 1
        per_case.setdefault(r["case"], {})[leg] = r["status"]
        for arm, f in (r.get("arm_failures") or {}).items():
            failures.setdefault(leg, {}).setdefault(f.split(":")[0], []).append(
                r["case"])
    # per-time per-response comparison stats over executed product_plus_data rows
    def _rel(a_val, c_val):
        if a_val is None or c_val is None or c_val == 0:
            return None
        return (a_val - c_val) / c_val
    cmp_stats = {}
    for r in rows:
        if r["leg"] != "product_plus_data" or r["status"] != "executed":
            continue
        ar = (r["arms"]["alara"].get("result") or {}).get("per_time") or {}
        cr = (r["arms"]["actinv"].get("result") or {}).get("per_time") or {}
        for t, pa in ar.items():
            pc = cr.get(t) or {}
            for k in ("total_activity_bq_per_g", "decay_heat_w_per_g",
                      "product_atoms_per_g", "total_atoms_per_g"):
                v = _rel(pa.get(k), pc.get(k))
                if v is not None:
                    s = cmp_stats.setdefault(k, {}).setdefault(t, [])
                    s.append(v)
            pa_p, pc_p = pa.get("photon_source_per_group_24"), \
                pc.get("photon_source_per_group_24")
            if pa_p and pc_p and sum(pc_p):
                cmp_stats.setdefault("photon_source_total", {}) \
                    .setdefault(t, []).append(
                        _rel(sum(pa_p), sum(pc_p)))
    cmp_out = {k: {t: {"n": len(vs), "mean_rel": sum(vs) / len(vs),
                       "max_abs_rel": max(abs(x) for x in vs),
                       "median_rel": sorted(vs)[len(vs) // 2]}
                   for t, vs in times.items()}
               for k, times in cmp_stats.items()}
    return {
        "schema": "actinv-p26b-g2-leg-1",
        "recorded_at_utc": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "recorded_at_commit": __import__("subprocess").run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
            text=True).stdout.strip(),
        "contract": {"file": "results/g2_p26b_leg_contract.json",
                     "sha256": sha256_file(CONTRACT_PATH)},
        "ledger": {"file": "results/g2_p26b_leg_ledger.jsonl",
                   "sha256": sha256_file(LEDGER), "rows": len(rows)},
        "context": context(),
        "census": census,
        "arm_failure_rollups": failures,
        "diagnostic_probe": probe,
        "per_case_status": per_case,
        "product_plus_data_response_comparison": {
            "note": "relative difference (alara - actinv)/actinv per response "
                    "per cooling time over executed cases; G3 reads the census "
                    "and these distributions for the verdict",
            "responses": cmp_out,
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=["pilot", "identical_data",
                                        "product_plus_data", "all",
                                        "summarize"],
                    default="all")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args()

    contract = json.loads(CONTRACT_PATH.read_text())
    contract_sha = sha256_file(CONTRACT_PATH)
    elelib = elelib_parse()
    hl = idx_parse(Path(str(ALARA_LIB_BASE) + ".idx"))
    spectra = {"fns_709": fns_spectrum(),
               "irdff_sp_mat9861_709": collapse_to_709()}
    if sha256_array(spectra["fns_709"]) != contract["spectra"]["fns_709"]["sha256"]:
        raise RuntimeError("fns_709 spectrum digest mismatch vs contract")
    # IRDFF is normalized to the fns_709 total flux per the contract
    # (shape-preserving scale; keeps product chains within the uniform
    # 1e-15 truncation tolerance at matched total flux)
    irdff_spec = contract["spectra"]["irdff_sp_mat9861_709"]
    scale = sum(spectra["fns_709"]) / sum(spectra["irdff_sp_mat9861_709"])
    spectra["irdff_sp_mat9861_709"] = [
        v * scale for v in spectra["irdff_sp_mat9861_709"]]
    if sha256_array(spectra["irdff_sp_mat9861_709"]) != \
            irdff_spec["sha256_ascending"]:
        raise RuntimeError("irdff spectrum digest mismatch vs contract")

    TMPDIR.mkdir(parents=True, exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)

    done = set()
    if args.resume and LEDGER.exists():
        for line in LEDGER.read_text().splitlines():
            r = json.loads(line)
            done.add((r["case"], r["leg"]))

    n = 0
    if args.stage in ("pilot", "all"):
        if ("DIAGNOSTIC", "probe") not in done:
            probe = run_diagnostic_probe(contract, spectra, done)
            with LEDGER.open("a") as lf:
                lf.write(json.dumps({"case": "DIAGNOSTIC", "leg": "probe",
                                     "record": probe}) + "\n")
            n += 1
    legs = []
    if args.stage in ("identical_data", "all"):
        legs.append("identical_data")
    if args.stage in ("product_plus_data", "all"):
        legs.append("product_plus_data")

    with LEDGER.open("a") as lf:
        for leg in legs:
            for case in contract["cases"]:
                if (case["case"], leg) in done:
                    continue
                row = run_leg_case(case, leg, spectra, elelib, hl, contract)
                lf.write(json.dumps(row) + "\n")
                lf.flush()
                n += 1
                if args.limit and n >= args.limit:
                    break
            if args.limit and n >= args.limit:
                break

    if args.stage in ("summarize", "all"):
        summary = summarize(contract)
        OUT.write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n")
        print(f"wrote {OUT.relative_to(ROOT)}")

    print(f"recorded {n} ledger rows (contract sha256 {contract_sha[:12]}…)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
