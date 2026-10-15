#!/usr/bin/env python3
"""P128 gates G2-G4 (protocols/ACTINV-P128_PROTOCOL.md): triton/helion projectiles and the schedule library switch.

  G2  The unmodified gate spec (examples/fns_fe_5min.json) run by the reference and the candidate binary gives
      byte-identical result files once the wall-clock `"ms": <number>` is masked.
  G3  Switch equivalence on real data. Three candidate runs of FNS Fe whose first 300 s irradiation step is split
      into two identical 150 s halves:
        a. run 1 (no override) against run 2 (step 2 names the base library again, with its own spectrum equal to
           the base spectrum): byte-identical after masking `ms` and removing the ledger `step_libraries` block and
           the certificate's `inputs.step_libraries` (neither is expected to exist, since a step that names the
           base library is a return to it, not an entry);
        b. run 3 (step 2 switches to a second real 709-group neutron library): the ledger and certificate record the
           second library's SHA-256, and
        c. step 2 of run 3 is recomputed here from run 3's step-1 inventory with the second library's one-group
           rates (collapsed from its NPZ with this script's own code) and a dense mpmath matrix exponential of the
           coupled system, restricted to the nuclides above 1e-12 of the inventory total at either end of the step.
           Compared at 1e-8 relative on every nuclide above 1e-12 of the step-2 total.
      G3c is reported `not_run` (with the reason) when no second prebuilt library passes the checks listed below.
  G4  Reported as data-limited: no TENDL triton/helion sublibrary is installed. The unit tests that cover the
      synthetic NSUB 10030/20030 tape, the residual arithmetic and the spec/builder paths are listed, and each is
      confirmed to exist in the source tree by text search. Nothing is run.

Second-library choice. Candidates are tried in order (full TENDL-2023, the TENDL-2023 FNS subset, the TENDL-2023 Fe
set, FENDL-3.2c, then any other `*709*.npz` found two levels under ~/nuclear-data). A candidate is accepted only if
its NPZ SHA-256 equals the index's `sha256_npz`, its index temperature equals the spec's `temperature_K`, its index
projectile is neutron (or absent), its group boundaries equal the base library's bitwise, it differs from the base
library, and it has reaction rows for every material nuclide (Fe54, Fe56, Fe57, Fe58). The rejection reason of every
skipped candidate is recorded.

Conventions inferred for G3c (shared with controls/check_p127.py, which supplies the parsers and rate rules):
  * trace mode (the mode the run reports): the material nuclides are constant reservoirs; the tracked states are
    the products plus, as hybrid states, the reservoir nuclides produced by a tracked precursor, whose inventory entry
    is the delta over the constant bulk. The restricted system is dy/dt = M y + s with M the full-removal rate
    matrix among the kept nuclides and s the reaction production from the bulk atoms. Coupled mode would have no
    source term and the Fe atoms in the state.
  * rates are the second library's rows collapsed with the step's spectrum; the step multiplier scales reactions only.
  * the engine prunes by rate (bmin 1e-8 atoms/g); the kept set of nuclides is checked against the larger set of
    every nuclide the run reports (`network_b`) as a sensitivity. The gate uses the directive's 1e-12-of-total set.
  * as a harness check, step 1 of run 3 is recomputed the same way from zero with the BASE library (informational).

Overall PASS iff G2 and G3 pass; INCOMPLETE (exit 2) when only G3c is not_run. Runs are serial, without a shell,
each under a timeout.

Usage:
  check_p128.py --reference REF_BIN --candidate CAND_BIN
      [--workdir target/p128-work] [--out results/p128_libraries.json] [--timeout 3600] [--reuse]
      [--second-library NPZ]
  check_p128.py --self-check      (offline unit checks; runs no binary)

Run inside the required systemd cgroup. Exit 0 on PASS, 1 on FAIL, 2 on INCOMPLETE.
"""
import argparse
import copy
import json
import math
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import mpmath
import numpy as np

import check_p127 as base

ROOT = base.ROOT
GATE_SPEC = base.GATE_SPEC
GATE_SPEC_SHA256 = base.GATE_SPEC_SHA256
PROTOCOL = ROOT / "protocols" / "ACTINV-P128_PROTOCOL.md"

NUCLEAR_DATA = Path("~/nuclear-data").expanduser()
SECOND_LIBRARY_CANDIDATES = [
    ("TENDL-2023, full 709-group neutron library", NUCLEAR_DATA / "tendl-2023" / "actinv_tendl2023_709g.npz"),
    ("TENDL-2023, FNS target subset", NUCLEAR_DATA / "tendl-2023" / "actinv_tendl2023_fns_709g.npz"),
    ("TENDL-2023, Fe set", NUCLEAR_DATA / "tendl-2023" / "actinv_ci_fe_709g.npz"),
    ("FENDL-3.2c, 24-target set", NUCLEAR_DATA / "p26b-work" / "g1-run" / "actinv_fendl32c_709.npz"),
]
MATERIAL_NUCLIDES = ["Fe54", "Fe56", "Fe57", "Fe58"]
SIGNIFICANT_SHARE = 1e-12
STEP_REL_TOL = 1e-8
HALF_STEP_INDEX = 1  # the second half of the split irradiation step

# Unit tests that cover G4 (file, test name); each is confirmed to exist by text search.
G4_TESTS = [
    ("crates/actinv-data/src/activation.rs", "projectile_triton_and_helion_identity"),
    ("crates/actinv-data/src/activation.rs", "triton_and_helion_metadata_parse_from_nsub"),
    ("crates/actinv-data/src/activation.rs", "triton_and_helion_sublibraries_do_not_cross_match"),
    ("crates/actinv-data/src/builder.rs", "triton_and_helion_residual_arithmetic_includes_projectile"),
    ("crates/actinv-data/src/builder.rs", "triton_and_helion_residuals_follow_the_ejectile_mt_numbering"),
    ("crates/actinv-data/src/builder.rs", "triton_and_helion_libraries_use_the_charged_particle_group_structure"),
    ("crates/actinv-core/src/spec.rs", "triton_and_helion_specs_use_the_charged_particle_grid"),
    ("crates/actinv-core/src/run.rs",
     "inventory_carries_across_the_switch_and_the_second_library_applies_only_after_it"),
    ("crates/actinv-core/src/run.rs", "ledger_and_certificate_name_the_switched_library"),
    ("crates/actinv-core/src/run.rs", "single_library_schedule_is_unchanged_and_reports_no_switch"),
]


# ------------------------------------------------------------------------------ specs


def split_schedule(spec):
    """The schedule with its first step duplicated into two halves of half the duration each."""
    first = spec["schedule"][0]
    if first.get("flux", 0.0) <= 0.0 or first.get("spectrum") or first.get("library"):
        raise base.GateError("the first schedule step is not a plain irradiation step")
    half = base.parse_duration(first["dt"]) / 2.0
    step = {"dt": f"{half!r} s", "flux": first["flux"]}
    spec["schedule"] = [dict(step), dict(step)] + spec["schedule"][1:]


def two_step_spec(base_spec, title_suffix):
    spec = copy.deepcopy(base_spec)
    base.absolutize_paths(spec)
    spec["title"] = f"{spec.get('title', 'P128')} [{title_suffix}]"
    split_schedule(spec)
    return spec


def with_library_override(spec, library_ref):
    """Step 2 (the second half) declares `library` and a spectrum equal to the base spectrum."""
    out = copy.deepcopy(spec)
    out["schedule"][HALF_STEP_INDEX]["library"] = copy.deepcopy(library_ref)
    out["schedule"][HALF_STEP_INDEX]["spectrum"] = copy.deepcopy(spec["spectrum"])
    return out


# ------------------------------------------------------------------------------ second-library selection


def check_second_library(npz_path, base_lib, base_sha, temperature_K, material_keys):
    """Return (library or None, info dict with the verdict and every reason)."""
    path = Path(npz_path)
    info = {"path": str(path), "reasons": []}
    reasons = info["reasons"]
    if not path.is_file():
        reasons.append("file not found")
        return None, info
    text = str(path)
    index_path = Path(text[:-4] + "_index.json" if text.endswith(".npz") else text + "_index.json")
    if not index_path.is_file():
        reasons.append("no index JSON beside the NPZ")
        return None, info
    info["sha256"] = base.sha256_file(path)
    info["index"] = base.record_file(index_path)
    try:
        lib = base.Library(path)
    except (base.GateError, ValueError, KeyError, OSError, zipfile.BadZipFile) as error:
        reasons.append(f"cannot load: {type(error).__name__}: {error}")
        return None, info
    index = lib.index
    info["n_targets"] = len(lib.targets)
    info["n_rows"] = int(lib.rows.shape[0])
    info["groups_field"] = index.get("groups")
    info["temperature_K"] = index.get("temperature_K")
    info["projectile"] = index.get("projectile")
    if info["sha256"] == base_sha:
        reasons.append("same NPZ as the base library")
    if index.get("sha256_npz") is not None and index["sha256_npz"].lower() != info["sha256"]:
        reasons.append("index sha256_npz does not match the NPZ")
    if index.get("sha256_npz") is None:
        reasons.append("index has no sha256_npz")
    if index.get("temperature_K") is None or abs(float(index["temperature_K"]) - temperature_K) > 1e-9:
        reasons.append(f"index temperature {index.get('temperature_K')} K is not the spec's {temperature_K} K")
    if index.get("projectile") not in (None, "neutron"):
        reasons.append(f"index projectile {index.get('projectile')!r} is not neutron")
    if lib.ngroups != base_lib.ngroups or not np.array_equal(lib.bounds, base_lib.bounds):
        reasons.append("group boundaries differ from the base library")
    for key in material_keys:
        usable = [r for r in lib.row_indices(key) if int(lib.rows[r, 2]) != -1]
        if not usable:
            reasons.append(f"no reaction rows for {base.name_of(key)}")
    info["accepted"] = not reasons
    return (lib if not reasons else None), info


def choose_second_library(base_lib, base_sha, temperature_K, material_keys, explicit):
    """First accepted candidate in the preference order. Returns (library, chosen info or None, all attempts)."""
    candidates = []
    if explicit:
        candidates.append(("explicit --second-library", Path(explicit)))
    candidates += SECOND_LIBRARY_CANDIDATES
    seen = {str(p) for _n, p in candidates}
    if NUCLEAR_DATA.is_dir():
        extras = sorted(set(NUCLEAR_DATA.glob("*/*709*.npz")) | set(NUCLEAR_DATA.glob("*/*/*709*.npz")))
        for path in extras:
            if str(path) not in seen and "pristine" not in path.name:
                candidates.append(("discovered under ~/nuclear-data", path))
                seen.add(str(path))
    attempts = []
    for label, path in candidates:
        lib, info = check_second_library(path, base_lib, base_sha, temperature_K, material_keys)
        info["label"] = label
        attempts.append(info)
        if lib is not None:
            return lib, info, attempts
    return None, None, attempts


# ------------------------------------------------------------------------------ the independent step solve


def solve_step(net, nodes, y0, bulk, dt, multiplier, dps=None):
    """Advance the kept nuclides `nodes` (a list of (ZA, LISO)) from `y0` ({key: atoms/g}) by `dt` seconds.
    Trace form (`bulk` is {key: atoms/g}): dy/dt = M y + s, with M[i, j] the decay plus multiplier * reaction rate
    j -> i among the kept nuclides, M[j, j] = -(lambda_j + multiplier * loss_j) (the FULL removal rate, whether or
    not its products are kept), and s_i the reaction/decay production from the constant bulk atoms. Coupled form
    (`bulk` None): s = 0. Solved by a dense mpmath exponential of the augmented matrix [[M, s/c], [0, 0]] applied to
    [y0; c] with c = max(s) (an exact rescaling that keeps the augmented matrix O(1) however large the bulk is).
    Returns (end values {key: atoms/g}, same by a double-precision scipy exponential)."""
    n = len(nodes)
    index = {k: i for i, k in enumerate(nodes)}
    m = [[0.0] * (n + 1) for _ in range(n + 1)]
    for j, key in enumerate(nodes):
        node = net.node(key)
        m[j][j] -= net.removal(key, multiplier)
        for to, rate in node["decay"].items():
            if to in index:
                m[index[to]][j] += rate
        for to, rate in node["react"].items():
            if to in index:
                m[index[to]][j] += multiplier * rate
    source = [0.0] * n
    for key, atoms in (bulk or {}).items():
        node = net.node(key)
        for to, rate in node["decay"].items():
            if to in index:
                source[index[to]] += rate * atoms
        for to, rate in node["react"].items():
            if to in index:
                source[index[to]] += multiplier * rate * atoms
    scale = max(source) if source and max(source) > 0.0 else 1.0
    for i in range(n):
        m[i][n] = source[i] / scale
    start = [y0.get(k, 0.0) for k in nodes] + [scale]
    a = mpmath.matrix(n + 1, n + 1)
    for i in range(n + 1):
        for j in range(n + 1):
            if m[i][j] != 0.0:
                a[i, j] = mpmath.mpf(m[i][j]) * mpmath.mpf(dt)
    v = mpmath.expm(a) * mpmath.matrix([mpmath.mpf(x) for x in start])
    precise = {k: float(v[i]) for k, i in index.items()}
    try:
        from scipy.linalg import expm
        e = expm(np.array(m) * dt)
        double = {k: float((e @ np.array(start))[i]) for k, i in index.items()}
    except ImportError:
        double = None
    return precise, double


def nodes_above(inventories, total_by_inventory, share):
    """Nuclide keys whose atoms reach `share` of the inventory total in at least one of the inventories."""
    keys = set()
    for inventory, total in zip(inventories, total_by_inventory):
        for name, atoms in inventory.items():
            if atoms > 0.0 and atoms >= share * total:
                keys.add(base.key_of(name))
    return keys


def inventory_of(step):
    return {e["nuclide"]: e["atoms_per_g"] for e in step["inventory"]}


def compare_step(computed, reported, share, label):
    """Every nuclide above `share` of the reported total, in either the computed or the reported step."""
    total = math.fsum(reported.values())
    rows, worst, worst_name = [], 0.0, None
    names = set(reported) | {base.name_of(k) for k in computed}
    by_name = {base.name_of(k): v for k, v in computed.items()}
    n_compared = 0
    for name in sorted(names):
        a, b = reported.get(name, 0.0), by_name.get(name, 0.0)
        if a < share * total and b < share * total:
            continue
        n_compared += 1
        rel = base.rel_diff(a, b)
        if rel > worst:
            worst, worst_name = rel, name
        rows.append({"nuclide": name, "reported": a, "recomputed": b, "relative": rel})
    rows.sort(key=lambda r: -r["relative"])
    return {"label": label, "reported_total_atoms_per_g": total, "n_compared": n_compared,
            "worst_relative": worst, "worst_nuclide": worst_name, "worst_rows": rows[:base.MAX_LISTED],
            "n_failing": sum(1 for r in rows if r["relative"] > STEP_REL_TOL),
            "pass": bool(n_compared > 0 and all(r["relative"] <= STEP_REL_TOL for r in rows))}


# ------------------------------------------------------------------------------ gates


def gate_g2(ref_path, cand_path, spec_sha_ok):
    out = base.gate_g2(ref_path, cand_path, spec_sha_ok)
    raw = Path(cand_path).read_bytes()
    out["no_step_libraries_key_in_candidate"] = b'"step_libraries"' not in raw
    out["pass"] = bool(out["pass"] and out["no_step_libraries_key_in_candidate"])
    return out


def strip_switch_blocks(result):
    """Result with the wall-clock and the two step_libraries blocks removed; returns (result, removed paths)."""
    result = copy.deepcopy(result)
    removed = []
    ledger = result.get("ledger")
    if isinstance(ledger, dict) and "step_libraries" in ledger:
        del ledger["step_libraries"]
        removed.append("ledger.step_libraries")
    inputs = (result.get("certificate") or {}).get("inputs")
    if isinstance(inputs, dict) and "step_libraries" in inputs:
        del inputs["step_libraries"]
        removed.append("certificate.inputs.step_libraries")
    result["ms"] = 0
    return result, removed


def gate_g3a(run1_path, run2_path):
    raw1, raw2 = Path(run1_path).read_bytes(), Path(run2_path).read_bytes()
    masked1, n1 = base.mask_ms(raw1)
    masked2, n2 = base.mask_ms(raw2)
    stripped1, removed1 = strip_switch_blocks(json.loads(raw1))
    stripped2, removed2 = strip_switch_blocks(json.loads(raw2))
    text1, text2 = json.dumps(stripped1, indent=1), json.dumps(stripped2, indent=1)
    equal = text1 == text2
    out = {"ms_masked": [n1, n2], "raw_byte_identical_after_ms_mask": masked1 == masked2,
           "blocks_removed_run1": removed1, "blocks_removed_run2": removed2,
           "equal_after_removing_step_libraries": equal,
           "ledger_step_spectra": [stripped1["ledger"]["schedule"].get("step_spectra"),
                                   stripped2["ledger"]["schedule"].get("step_spectra")],
           "steps": [len(stripped1["steps"]), len(stripped2["steps"])]}
    if not equal:
        out["first_difference"] = base.first_byte_difference(text1.encode(), text2.encode())
    out["pass"] = bool(equal and n1 == 1 and n2 == 1)
    return out


def ledger_checks(result3, lib2_sha, lib2_index_sha, base_steps):
    """The switched library's hashes in the ledger and certificate (G3b)."""
    out = {}
    block = (result3.get("ledger") or {}).get("step_libraries")
    out["ledger_block_present"] = isinstance(block, dict)
    libraries = block.get("libraries", []) if isinstance(block, dict) else []
    out["ledger_libraries"] = len(libraries)
    if libraries:
        entry = libraries[0]
        out["ledger_library_sha256"] = entry.get("library_sha256")
        out["ledger_steps_served"] = entry.get("steps")
        out["ledger_sha_matches"] = entry.get("library_sha256", "").lower() == lib2_sha
        out["ledger_steps_expected"] = list(range(HALF_STEP_INDEX + 1, base_steps + 1))
        out["ledger_steps_match"] = entry.get("steps") == out["ledger_steps_expected"]
    certificate = ((result3.get("certificate") or {}).get("inputs") or {}).get("step_libraries")
    out["certificate_entries"] = len(certificate) if isinstance(certificate, list) else None
    if isinstance(certificate, list) and certificate:
        out["certificate_library_sha256"] = (certificate[0].get("library") or {}).get("sha256")
        out["certificate_index_sha256"] = (certificate[0].get("library_index") or {}).get("sha256")
        out["certificate_sha_matches"] = (out["certificate_library_sha256"] or "").lower() == lib2_sha
        out["certificate_index_sha_matches"] = (out["certificate_index_sha256"] or "").lower() == lib2_index_sha
    out["pass"] = bool(out.get("ledger_sha_matches") and out.get("ledger_steps_match") and
                       out.get("certificate_sha_matches") and out.get("certificate_index_sha_matches")
                       and out["ledger_libraries"] == 1 and out["certificate_entries"] == 1)
    return out


def gate_g3c(result1, result3, spec3, lib2, base_lib):
    """Independent recomputation of step 2 of run 3 from its step-1 inventory with the second library's rates."""
    mode = result3.get("mode")
    if mode not in ("trace", "coupled"):
        raise base.GateError(f"run 3 reports mode {mode!r}")
    schedule = base.schedule_of(spec3, allow_overrides=True)
    dt1, fl1 = schedule[0]
    dt2, fl2 = schedule[HALF_STEP_INDEX]
    steps = result3["steps"]
    inv1, inv2 = inventory_of(steps[0]), inventory_of(steps[HALF_STEP_INDEX])
    total1, total2 = math.fsum(inv1.values()), math.fsum(inv2.values())
    phi = base.ascending_flux(spec3["schedule"][HALF_STEP_INDEX]["spectrum"])
    base_phi = base.ascending_flux(spec3["spectrum"])
    isotopes = base.load_isotopes()
    bulk_all = base.bulk_atoms_per_g(spec3, isotopes)
    bulk = bulk_all if mode == "trace" else None
    primary, fallback = base.decay_files(spec3)

    set_a = nodes_above([inv1, inv2], [total1, total2], SIGNIFICANT_SHARE)
    set_b = {base.key_of(n) for n in set(inv1) | set(inv2)}
    needed = set_a | set_b | set(bulk_all)
    records, chain_keys, decay_info = base.load_decay(primary, fallback, needed)
    out = {"mode": mode, "step_index": HALF_STEP_INDEX + 1, "dt_s": dt2, "multiplier": fl2,
           "step1_total_atoms_per_g": total1, "step2_total_atoms_per_g": total2,
           "significance_share": SIGNIFICANT_SHARE, "tolerance_relative": STEP_REL_TOL, "decay": decay_info,
           "second_library_rows": int(lib2.rows.shape[0]), "network_a_nodes": len(set_a),
           "network_b_nodes": len(set_b)}
    net2 = base.Network(records, chain_keys, lib2, phi)
    net2.prepare(sorted(needed))
    out["fission_rows_among_nodes"] = sorted(base.name_of(k) for k in set_b if net2.node(k)["fission"] != 0.0)
    y1 = {base.key_of(n): v for n, v in inv1.items()}
    # the switch is the only thing that differs from run 1, so the step-1 inventory must be run 1's own
    out["step1_inventory_equals_run1"] = bool(result1 is not None and
                                              inventory_of(result1["steps"][0]) == inv1)
    precise_a, double_a = solve_step(net2, sorted(set_a), y1, bulk, dt2, fl2)
    precise_b, double_b = solve_step(net2, sorted(set_b), y1, bulk, dt2, fl2)
    out["network_a"] = compare_step(precise_a, inv2, SIGNIFICANT_SHARE, "nuclides above 1e-12, kept set A")
    out["network_b"] = compare_step(precise_b, inv2, SIGNIFICANT_SHARE, "nuclides above 1e-12, kept set B")
    if double_a is not None:
        out["network_a"]["scipy_vs_mpmath_worst_relative"] = max(
            (base.rel_diff(double_a[k], precise_a[k]) for k in precise_a if precise_a[k] > 0.0), default=0.0)
    # sensitivity: the same step with the BASE library's rates (must NOT reproduce the switched result unless the
    # two libraries agree; reported so a vacuous comparison is visible)
    net_base = base.Network(records, chain_keys, base_lib, base_phi)
    net_base.prepare(sorted(needed))
    wrong_a, _ = solve_step(net_base, sorted(set_a), y1, bulk, dt2, fl2)
    out["step2_with_base_library_rates"] = compare_step(wrong_a, inv2, SIGNIFICANT_SHARE,
                                                        "base-library rates against the switched result")
    out["libraries_distinguishable"] = bool(out["step2_with_base_library_rates"]["worst_relative"] > STEP_REL_TOL)
    # harness check: step 1 from zero with the base library against run 3's own step 1 (informational)
    nodes1 = sorted(nodes_above([inv1], [total1], SIGNIFICANT_SHARE))
    first, _ = solve_step(net_base, nodes1, {}, bulk, dt1, fl1)
    out["harness_step1_base_library"] = compare_step(first, inv1, SIGNIFICANT_SHARE,
                                                     "step 1 from zero, base library (informational)")
    out["pass"] = bool(out["network_a"]["pass"] and out["libraries_distinguishable"])
    return out


def installed_triton_helion():
    """Paths under ~/nuclear-data (two levels) and actinv-data whose names mention a triton or helion library."""
    found = []
    roots = [(NUCLEAR_DATA, 2), (ROOT / "actinv-data", 4)]
    for root, depth in roots:
        if not root.is_dir():
            continue
        for level in range(1, depth + 1):
            for path in root.glob("/".join(["*"] * level)):
                if re.search(r"triton|helion", path.name, re.IGNORECASE):
                    found.append(str(path))
    return sorted(found)[:base.MAX_LISTED]


def g4_report():
    found, missing = [], []
    for rel, name in G4_TESTS:
        text = (ROOT / rel).read_text()
        (found if re.search(rf"fn {re.escape(name)}\(", text) else missing).append({"file": rel, "test": name})
    return {"status": "data_limited", "pass": None,
            "reason": ("no TENDL triton or helion sublibrary is installed under ~/nuclear-data or actinv-data, so "
                       "no real-data triton/helion library can be built or run; the protocol reports this gate as "
                       "data-limited and upgrades it when real data is installed"),
            "covered_by_unit_tests": found, "tests_not_found_in_source": missing,
            "note": ("the synthetic NSUB 10030/20030 tape builds and its residuals follow hand arithmetic in the "
                     "tests above; their execution belongs to the G1 workspace test run, not to this checker"),
            "triton_helion_paths_found": installed_triton_helion()}


# ------------------------------------------------------------------------------ self-check (no binary)


class _StubLibrary:
    """Two rows for target 1001: a loss row and a product row to 2001, at fixed per-atom rates."""

    def __init__(self, rate):
        self.rate = rate

    def collapse(self, keys, phi):
        return {k: [(103, -1, -1, 0, self.rate), (103, 2001, 0, 10, self.rate)] if k == (1001, 0) else []
                for k in keys}


def self_check():
    failures = []

    def expect(cond, what):
        if not cond:
            failures.append(what)

    rate, bulk_atoms, lam, dt = 2.0e-3, 1.0e22, 5.0e-3, 400.0
    records = {(2001, 0): {"nst": 0, "half_life": math.log(2.0) / lam, "modes": [(1.0, 0.0, 1.0)]},
               (3001, 0): {"nst": 1, "half_life": 0.0, "modes": []}}
    chain = {(1001, 0), (2001, 0), (3001, 0)}
    net = base.Network(records, chain, _StubLibrary(rate), np.ones(2))
    net.prepare([(1001, 0), (2001, 0)])
    # trace: reservoir 1001 feeds 2001, which decays to 3001: y2' = bulk*rate - lam*y2, y3' = lam*y2
    precise, double = solve_step(net, [(2001, 0), (3001, 0)], {(2001, 0): 1.0e9}, {(1001, 0): bulk_atoms}, dt, 0.5)
    s = bulk_atoms * rate * 0.5
    y2 = 1.0e9 * math.exp(-lam * dt) + s / lam * (1.0 - math.exp(-lam * dt))
    expect(base.rel_diff(precise[(2001, 0)], y2) < 1e-12, f"trace reservoir node: {precise[(2001, 0)]} vs {y2}")
    y3 = 1.0e9 + s * dt - y2  # atoms are conserved between 2001 and 3001
    expect(base.rel_diff(precise[(3001, 0)], y3) < 1e-9, f"decay daughter: {precise[(3001, 0)]} vs {y3}")
    expect(double is not None and base.rel_diff(double[(2001, 0)], y2) < 1e-10, "scipy cross-check")
    # coupled: 1001 itself is a node and is depleted at the full removal rate (the loss row)
    precise, _ = solve_step(net, [(1001, 0), (2001, 0)], {(1001, 0): 1.0e20}, None, dt, 1.0)
    n1 = 1.0e20 * math.exp(-rate * dt)
    expect(base.rel_diff(precise[(1001, 0)], n1) < 1e-12, "coupled depletion")
    y2c = 1.0e20 * rate / (lam - rate) * (math.exp(-rate * dt) - math.exp(-lam * dt))
    expect(base.rel_diff(precise[(2001, 0)], y2c) < 1e-10, "coupled Bateman daughter")
    # the 1e-12-of-total node selection and the comparison rule
    inv = {"Mn56": 1.0e10, "Co58": 1.0e-3, "Fe55": 2.0e10}
    expect(nodes_above([inv], [math.fsum(inv.values())], 1e-12) == {(25056, 0), (26055, 0)}, "node selection")
    expect(compare_step({(25056, 0): 1.0e10 * (1 + 1e-10)}, {"Mn56": 1.0e10}, 1e-12, "x")["pass"], "compare pass")
    expect(not compare_step({(25056, 0): 1.0e10 * (1 + 1e-6)}, {"Mn56": 1.0e10}, 1e-12, "x")["pass"], "compare fail")
    # switch-block stripping
    sample = {"ledger": {"step_libraries": {}, "schedule": {}}, "certificate": {"inputs": {"step_libraries": []}},
              "ms": 5.0}
    stripped, removed = strip_switch_blocks(sample)
    expect(removed == ["ledger.step_libraries", "certificate.inputs.step_libraries"] and stripped["ms"] == 0 and
           "step_libraries" not in stripped["ledger"], "strip_switch_blocks")
    spec = {"schedule": [{"dt": "300.0 s", "flux": 1.0}, {"dt": "66.0 s", "flux": 0.0}]}
    split_schedule(spec)
    expect([s["dt"] for s in spec["schedule"]] == ["150.0 s", "150.0 s", "66.0 s"], "schedule split")
    tests = g4_report()
    expect(not tests["tests_not_found_in_source"],
           f"G4 tests missing from the source: {tests['tests_not_found_in_source']}")
    for failure in failures:
        print(f"self-check FAIL: {failure}")
    print(f"P128 self-check: {'PASS' if not failures else 'FAIL'}")
    return 0 if not failures else 1


# ------------------------------------------------------------------------------ main


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reference", help="release binary built from the P128 base commit (2f3daa4)")
    ap.add_argument("--candidate", help="release binary built from the P128 implementation commit")
    ap.add_argument("--workdir", default="target/p128-work")
    ap.add_argument("--out", default="results/p128_libraries.json")
    ap.add_argument("--timeout", type=int, default=3600, help="seconds allowed per run")
    ap.add_argument("--second-library", help="NPZ of the second neutron library (default: first accepted candidate)")
    ap.add_argument("--reuse", action="store_true", help="reuse existing outputs in the workdir")
    ap.add_argument("--self-check", action="store_true", help="offline unit checks; runs no binary")
    args = ap.parse_args(argv)
    if args.self_check:
        return self_check()
    if not args.reference or not args.candidate:
        ap.error("--reference and --candidate are required")
    # Runs execute from the workdir as well as the repo root, so binaries must be absolute.
    args.reference = str(Path(args.reference).resolve())
    args.candidate = str(Path(args.candidate).resolve())

    workdir = Path(args.workdir)
    if not workdir.is_absolute():
        workdir = ROOT / workdir
    workdir.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = ROOT / out_path

    record = {"protocol": "ACTINV-P128", "checker": base.record_file(Path(__file__).resolve()),
              "protocol_file": base.record_file(PROTOCOL), "isotope_table": base.record_file(base.TABLES_RS),
              "reference_binary": base.record_file(args.reference),
              "candidate_binary": base.record_file(args.candidate),
              "gate_spec": base.record_file(GATE_SPEC), "runs": {}, "gates": {}}
    spec_ok = record["gate_spec"]["sha256"] == GATE_SPEC_SHA256
    record["gate_spec"]["matches_protocol_sha256"] = spec_ok
    base_spec = json.loads(GATE_SPEC.read_text())

    def execute(label, binary, spec_path, cwd):
        out_file = workdir / f"{label}.json"
        log_file = workdir / f"{label}.log"
        info = {"binary": str(binary), "spec": base.record_file(spec_path), "cwd": str(cwd)}
        record["runs"][label] = info
        if not (args.reuse and out_file.exists()):
            try:
                code = base.run_binary(binary, spec_path, out_file, log_file, cwd, args.timeout)
            except subprocess.TimeoutExpired:
                info["error"] = f"timed out after {args.timeout} s"
                return None
            except OSError as error:
                info["error"] = f"{type(error).__name__}: {error}"
                return None
            info["exit_code"] = code
            if code != 0:
                info["error"] = f"exit code {code}; see {log_file}"
                return None
        info["output"] = base.record_file(out_file)
        return out_file

    def guarded(function, *call_args):
        try:
            return function(*call_args)
        except (base.GateError, OSError, ValueError, KeyError, IndexError, TypeError, ZeroDivisionError,
                zipfile.BadZipFile) as error:
            return {"pass": False, "error": f"{type(error).__name__}: {error}"}

    # ---- G2
    ref_file = execute("g2_reference", args.reference, GATE_SPEC, ROOT)
    cand_file = execute("g2_candidate", args.candidate, GATE_SPEC, ROOT)
    if ref_file is not None and cand_file is not None:
        record["gates"]["G2"] = guarded(gate_g2, ref_file, cand_file, spec_ok)
    else:
        record["gates"]["G2"] = {"pass": False, "error": "a gate run failed; see runs"}

    # ---- G3: runs 1 and 2, then the second library and run 3
    spec1 = two_step_spec(base_spec, "P128 two halves")
    record["data_inputs"] = base.data_inputs(spec1)
    spec2 = with_library_override(spec1, spec1["library"])
    paths = {}
    for label, spec in (("run1", spec1), ("run2", spec2)):
        paths[label] = workdir / f"{label}.spec.json"
        paths[label].write_text(json.dumps(spec, indent=1) + "\n")
    run1_file = execute("run1_two_halves", args.candidate, paths["run1"], workdir)
    run2_file = execute("run2_same_library_override", args.candidate, paths["run2"], workdir)
    g3 = {"split": "schedule[0] (300 s irradiation) duplicated into two 150 s halves; the override is on step 2"}
    g3["a_run1_vs_run2"] = guarded(gate_g3a, run1_file, run2_file) if run1_file and run2_file else \
        {"pass": False, "error": "run 1 or run 2 failed; see runs"}

    temperature = float((base_spec.get("options") or {}).get("temperature_K", 293.6))
    material_keys = [base.key_of(n) for n in MATERIAL_NUCLIDES]
    base_lib = base.Library(spec1["library"]["path"])
    base_sha = spec1["library"].get("sha256") or base.sha256_file(spec1["library"]["path"])
    second_lib, chosen, attempts = choose_second_library(base_lib, base_sha, temperature, material_keys,
                                                         args.second_library)
    record["second_library_search"] = {"preference_order": [label for label, _p in SECOND_LIBRARY_CANDIDATES],
                                       "attempts": attempts, "chosen": chosen}
    c = {"pass": None, "status": "not_run"}
    if second_lib is None:
        c["reason"] = ("no prebuilt second 709-group neutron library with an index passes the acceptance checks; "
                       "see second_library_search.attempts for each candidate's rejection reasons")
        g3["b_run3_ledger"] = {"pass": None, "status": "not_run"}
    else:
        spec3 = with_library_override(spec1, {"path": str(second_lib.path), "sha256": chosen["sha256"]})
        spec3["title"] = f"{spec1['title']} [library switch]"
        paths["run3"] = workdir / "run3.spec.json"
        paths["run3"].write_text(json.dumps(spec3, indent=1) + "\n")
        run3_file = execute("run3_second_library", args.candidate, paths["run3"], workdir)
        if run3_file is None:
            c = {"pass": False, "status": "error", "error": "run 3 failed; see runs"}
            g3["b_run3_ledger"] = {"pass": False, "error": "run 3 failed; see runs"}
        else:
            result3 = json.loads(run3_file.read_text())
            result1 = json.loads(run1_file.read_text()) if run1_file else None
            g3["b_run3_ledger"] = guarded(ledger_checks, result3, chosen["sha256"], chosen["index"]["sha256"],
                                          len(spec3["schedule"]))
            c = guarded(gate_g3c, result1, result3, spec3, second_lib, base_lib)
            c.setdefault("status", "run")
    g3["c_run3_recompute"] = c
    a_ok = g3["a_run1_vs_run2"].get("pass") is True
    complete = c.get("status") == "run"
    g3["complete"] = complete
    if complete:
        g3["pass"] = bool(a_ok and c.get("pass") is True and g3["b_run3_ledger"].get("pass") is True)
    else:
        # G3a alone cannot close the gate: with G3c not run the gate is open (None), or failed if G3a failed
        g3["pass"] = None if (a_ok and c.get("status") == "not_run") else False
    record["gates"]["G3"] = g3

    # ---- G4
    record["gates"]["G4"] = guarded(g4_report)

    gate_ok = {g: bool(record["gates"][g].get("pass")) for g in ("G2", "G3")}
    if gate_ok["G2"] and record["gates"]["G3"]["pass"] is None:
        record["verdict"], code = "INCOMPLETE", 2
    elif all(gate_ok.values()):
        record["verdict"], code = "PASS", 0
    else:
        record["verdict"], code = "FAIL", 1
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    print(f"P128 G2: {'PASS' if gate_ok['G2'] else 'FAIL'}")
    print(f"P128 G3: {'PASS' if gate_ok['G3'] else 'FAIL'} (a: {g3['a_run1_vs_run2'].get('pass')}, "
          f"b: {g3['b_run3_ledger'].get('pass')}, c: {c.get('status')}/{c.get('pass')})")
    if c.get("network_a"):
        print(f"  G3c: {c['network_a']['n_compared']} nuclides, worst relative {c['network_a']['worst_relative']:.3e} "
              f"({c['network_a']['worst_nuclide']}); set B worst {c['network_b']['worst_relative']:.3e}")
    print(f"P128 G4: data-limited ({len(record['gates']['G4'].get('covered_by_unit_tests', []))} covering unit tests)")
    print(f"P128 verdict: {record['verdict']} ({out_path})")
    return code


if __name__ == "__main__":
    sys.exit(main())
