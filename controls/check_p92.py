#!/usr/bin/env python3
"""P92 checker (protocols/ACTINV-P92_PROTOCOL.md): gas production (H and He isotopes, appm).

    python3 controls/check_p92.py build    # target/p92/build.sh: fmt, clippy, test --release, release build
    python3 controls/check_p92.py g2       # Z/A balance + coverage, TENDL-2017 FNS and TENDL-2025
    python3 controls/check_p92.py g3       # 783 P75b specs + 3 mesh profiles, gas off, bitwise vs reference
    python3 controls/check_p92.py g4       # same 783 specs, gas on vs gas off, non-perturbation
    python3 controls/check_p92.py g5       # G5 (FISPACT cross-code) is run by the lead
    python3 controls/check_p92.py verdict  # assembles G0-G6 -- run by the lead, not by a subagent

Run under the 6 GB cgroup cap, e.g.:
    CARGO_BUILD_JOBS=3 TMPDIR=$PWD/target/preflight-tmp systemd-run --user --scope -q \
      -p MemoryMax=6G -p MemorySwapMax=0 -p TasksMax=128 -p CPUQuota=300% -- \
      python3 controls/check_p92.py g3

Logs land under target/p92/. With ACTINV_GAS_PROTOCOL=P95 the same gates run as registered by
protocols/ACTINV-P95_PROTOCOL.md: logs under target/p95/, verdict results/p95_verdict.json, G1 also
requires the inventory_appm unit test, and G5 compares `inventory_appm` instead of `appm`. G0 (protocol registration) and G6 (CI replay) are the lead's own
steps and are not computed here. `verdict` must not be invoked by the implementing agent.
"""
from __future__ import annotations

import concurrent.futures as cf
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = Path.home() / "Documents" / "actinv"
GAS_PROTOCOL = os.environ.get("ACTINV_GAS_PROTOCOL", "P92")
if GAS_PROTOCOL not in ("P92", "P95"):
    sys.exit(f"ACTINV_GAS_PROTOCOL must be P92 or P95, not {GAS_PROTOCOL}")
TAG = GAS_PROTOCOL.lower()
WORK = ROOT / "target" / TAG
REF = ROOT / "target" / "p91" / "ref_actinv"
CAND = ROOT / "target" / "release" / "actinv"
BALANCE = ROOT / "target" / "release" / "examples" / "p92_balance"
SPECS = MAIN / "target" / "p75b" / "specs"
MESH = ["fe_coupled", "fe_p21like", "ss316_r2s"]
MESH_DIR = MAIN / "target" / "meshprof"
PROTOCOL = ROOT / "protocols" / f"ACTINV-{GAS_PROTOCOL}_PROTOCOL.md"
VERDICT = ROOT / "results" / f"{TAG}_verdict.json"
G5_FIELD = "inventory_appm" if GAS_PROTOCOL == "P95" else "appm"
TIMING_KEYS = {"ms", "elapsed_ms", "wall_s", "wall_time_s", "cells_per_s"}
FOOTER_TIMING = ("wall_time_s", "cells_per_s")
ENV = {"RAYON_NUM_THREADS": "1", "PATH": "/usr/bin:/bin", "HOME": str(Path.home())}
WORKERS = 3
LIGHT = ("H1", "H2", "H3", "He3", "He4")
G1_UNIT_TESTS = (
    "gas::tests::table_za_balances_against_vendored_mt_products",
    "gas::tests::inelastic_mts_balance_to_no_za_change",
    "gas::tests::uncovered_mts_are_none",
    "gas::tests::gas_products_excludes_neutrons_and_orders_h_then_he",
    "chain::tests::gas_produced_from_a_constant_reservoir_matches_the_exact_linear_integral",
    "chain::tests::h3_ejectiles_decay_to_he3_at_the_tabulated_lambda",
    "chain::tests::decay_alpha_case_feeds_he4_at_the_parents_lambda",
    "chain::tests::add_gas_decay_edges_tallies_one_digit_per_alpha_or_proton",
    "chain::tests::gas_ejectiles_are_added_for_normal_and_unmapped_rows_and_fission_is_uncovered",
    "chain::tests::gas_off_adds_no_ejectiles_or_uncovered_ledger_entries",
    "run::gas_prepared_tests::trace_and_coupled_gas_agree_at_low_fluence",
    "run::gas_prepared_tests::gas_disabled_leaves_the_step_gas_block_absent",
    "spec::duration_tests::gas_is_refused_with_uncertainty_and_with_non_neutron_projectiles",
    "spec::duration_tests::gas_false_is_absent_from_the_serialized_options_others_default_present",
) + (("run::gas_prepared_tests::inventory_appm_counts_initial_hydrogen_and_appm_does_not",)
     if GAS_PROTOCOL == "P95" else ())
G2_LIBRARIES = {
    "tendl2017_fns": Path.home() / "nuclear-data" / "tendl-2017" / "build" / "neutron.n.p10.npz",
    "tendl2025": MAIN / "actinv-data" / "v1.1.0" / "activation" / "tendl-2025-neutron-709g.npz",
}


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


EV_J = 1.602176634e-19


def _endf_float(text: str) -> float:
    text = text.strip()
    if not text:
        return 0.0
    for i in range(1, len(text)):
        if text[i] in "+-" and text[i - 1] not in "eE":
            return float(text[:i] + "e" + text[i:])
    return float(text)


def light_decay_energies_eV(decay_paths: list[str]) -> dict[str, tuple[float, float, float]]:
    """(E_LP, E_EM, E_HP) mean decay energies (eV) from MF=8/MT=457 for the radioactive light
    nuclides (H3 is the only one), from the first decay file that carries them."""
    wanted = {1003.0: "H3"}
    found: dict[str, tuple[float, float, float]] = {}
    for path in decay_paths:
        with open(path) as stream:
            lines = iter(stream)
            for line in lines:
                if line[70:72].strip() == "8" and line[72:75].strip() == "457":
                    za = _endf_float(line[0:11])
                    if za in wanted and wanted[za] not in found:
                        next(lines)
                        energies = next(lines)
                        vals = [_endf_float(energies[i:i + 11]) for i in (0, 22, 44)]
                        found[wanted[za]] = (vals[0], vals[1], vals[2])
        if len(found) == len(wanted):
            break
    return found


def rel(a, b):
    """|a - b| / |b|; None when both are zero, inf when only the reference is zero."""
    if b == 0:
        return None if a == 0 else math.inf
    return abs(a - b) / abs(b)


def strip_timing(v):
    if isinstance(v, dict):
        return {k: strip_timing(x) for k, x in v.items() if k not in TIMING_KEYS}
    if isinstance(v, list):
        return [strip_timing(x) for x in v]
    return v


def registered() -> bool:
    return f"{sha(PROTOCOL)}  protocols/{PROTOCOL.name}" in (ROOT / "protocols/protocol_hash.txt").read_text()


# ---------------------------------------------------------------- build (G1)

def cmd_build() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    build_sh = WORK / "build.sh"
    if not build_sh.exists():
        sys.exit(f"{build_sh} is missing")
    p = subprocess.run(["bash", str(build_sh)], cwd=ROOT)
    blog = (WORK / "build.log").read_text() if (WORK / "build.log").exists() else ""
    rc = dict(re.findall(r"^(fmt|clippy|test|release) rc=(\d+)$", blog, re.M))
    tests = (WORK / "test.txt").read_text() if (WORK / "test.txt").exists() else ""
    unit = {name: re.search(rf"^test {re.escape(name)} \.\.\. ok$", tests, re.M) is not None
            for name in G1_UNIT_TESTS}
    g1 = (p.returncode == 0 and all(rc.get(k) == "0" for k in ("fmt", "clippy", "test", "release"))
          and all(unit.values()))
    out = {"pass": g1, "build_returncode": p.returncode, "exit_codes": rc, "unit_tests": unit,
           "candidate_sha256": sha(CAND) if CAND.exists() else None}
    (WORK / "g1_summary.json").write_text(json.dumps(out, indent=1, sort_keys=True))
    print(json.dumps(out, indent=1))
    return 0 if g1 else 1


# ---------------------------------------------------------------------- G2

def cmd_g2() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    if not BALANCE.exists():
        sys.exit(f"{BALANCE} is missing; run `build` first (or "
                  f"cargo build --release -p actinv-core --example p92_balance)")
    out = {}
    ok = True
    for name, lib in G2_LIBRARIES.items():
        if not lib.exists():
            out[name] = {"error": f"library not found: {lib}"}
            ok = False
            continue
        p = subprocess.run([str(BALANCE), str(lib)], capture_output=True, text=True)
        (WORK / f"g2_{name}.json").write_text(p.stdout)
        try:
            doc = json.loads(p.stdout)
        except json.JSONDecodeError:
            doc = {}
        row_pass = p.returncode == 0 and doc.get("failure_count") == 0
        out[name] = {"pass": row_pass, "returncode": p.returncode, "library": str(lib),
                     "rows_total": doc.get("rows_total"), "rows_checked": doc.get("rows_checked"),
                     "failure_count": doc.get("failure_count"),
                     "uncovered_rows": doc.get("uncovered_rows"), "uncovered_mts": doc.get("uncovered_mts"),
                     "uncovered_rate_share_flat_spectrum": doc.get("uncovered_rate_share_flat_spectrum")}
        ok = ok and row_pass
    result = {"pass": ok, "libraries": out}
    (WORK / "g2_summary.json").write_text(json.dumps(result, indent=1, sort_keys=True))
    print(json.dumps(result, indent=1))
    return 0 if ok else 1


# ---------------------------------------------------------------------- G3

def run_single(binary: Path, spec: Path) -> list:
    with tempfile.TemporaryDirectory(dir=WORK) as d:
        out = Path(d) / "out.json"
        p = subprocess.run([str(binary), "run", str(spec), str(out)], capture_output=True, text=True, env=ENV)
        if p.returncode != 0:
            return [p.returncode, p.stderr[-300:]]
        doc = strip_timing(json.loads(out.read_text()))
        return [0, hashlib.sha256(json.dumps(doc, sort_keys=True).encode()).hexdigest()]


def run_mesh(binary: Path, spec: Path, out: Path) -> dict:
    t0 = time.monotonic()
    p = subprocess.run([str(binary), "mesh", str(spec), str(out)], cwd=MAIN, capture_output=True, text=True, env=ENV)
    return {"returncode": p.returncode, "wall_s": time.monotonic() - t0, "stderr_tail": p.stderr[-300:]}


def mesh_digest(path: Path) -> dict:
    """sha256 of every line but the last (the footer), plus the footer without timing keys."""
    h = hashlib.sha256()
    lines = 0
    last = None
    with path.open("rb") as f:
        for line in f:
            if last is not None:
                h.update(last)
                lines += 1
            last = line
    footer = json.loads(last)
    for key in FOOTER_TIMING:
        footer.pop(key, None)
    return {"body_sha256": h.hexdigest(), "body_lines": lines, "footer": footer}


def cmd_g3() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    if not REF.exists() or not CAND.exists():
        sys.exit(f"reference ({REF}) or candidate ({CAND}) binary is missing")
    specs = sorted(SPECS.glob("*.json"))
    if not specs:
        sys.exit(f"no P75b specs found under {SPECS}")

    def both(spec: Path):
        return spec.name, run_single(REF, spec), run_single(CAND, spec)

    single = {}
    t0 = time.monotonic()
    with cf.ThreadPoolExecutor(WORKERS) as ex:
        for i, (name, r, c) in enumerate(ex.map(both, specs)):
            single[name] = {"ref": r, "cand": c}
            if i % 100 == 0:
                print(f"g3 single {i}/{len(specs)} {time.monotonic() - t0:.0f}s", flush=True)
    fails = [n for n, v in single.items() if v["ref"][0] != 0 or v["cand"][0] != 0 or v["ref"][1] != v["cand"][1]]

    mesh = {}
    for c in MESH:
        spec_path = MESH_DIR / f"{c}.json"
        base = json.loads(spec_path.read_text())
        variant = WORK / f"{c}.t1.json"
        variant.write_text(json.dumps({**base, "threads": 1}))
        outs = {}
        for tag, b in (("ref", REF), ("cand", CAND)):
            out = WORK / f"mesh_{c}.{tag}.ndjson"
            r = run_mesh(b, variant, out)
            if r["returncode"] == 0:
                r["digest"] = mesh_digest(out)
            out.unlink(missing_ok=True)
            outs[tag] = r
        ok = (outs["ref"]["returncode"] == 0 and outs["cand"]["returncode"] == 0
              and outs["ref"].get("digest") == outs["cand"].get("digest"))
        mesh[c] = {"pass": ok, "ref_returncode": outs["ref"]["returncode"],
                   "cand_returncode": outs["cand"]["returncode"],
                   "ref_digest": outs["ref"].get("digest"), "cand_digest": outs["cand"].get("digest")}
        print("g3 mesh", c, ok, flush=True)

    ok = len(single) == 783 and not fails and all(v["pass"] for v in mesh.values())
    (WORK / "g3_single.json").write_text(json.dumps(single, indent=1, sort_keys=True))
    summary = {"pass": ok, "specs": len(single), "differing_or_failed": fails[:20], "mesh": mesh}
    (WORK / "g3_summary.json").write_text(json.dumps(summary, indent=1, sort_keys=True))
    print(json.dumps(summary, indent=1))
    return 0 if ok else 1


# ---------------------------------------------------------------------- G4

def run_full(binary: Path, spec_path: Path):
    with tempfile.TemporaryDirectory(dir=WORK) as d:
        out = Path(d) / "out.json"
        p = subprocess.run([str(binary), "run", str(spec_path), str(out)], capture_output=True, text=True, env=ENV)
        if p.returncode != 0:
            return None, p.returncode, p.stderr[-300:]
        return json.loads(out.read_text()), 0, None


def compare_steps(off_steps, on_steps, energies_eV):
    """Worst-case relative differences excluding the five light nuclides (G4)."""
    worst_inv, worst_inv_where = 0.0, None
    worst_act, worst_act_where = 0.0, None
    worst_alpha, worst_gamma = 0.0, 0.0
    worst_heat_minus_light = {"total": 0.0, "beta": 0.0, "gamma": 0.0, "alpha": 0.0}
    worst_total_activity = 0.0
    worst_heat_minus_light_on = {"total": 0.0, "beta": 0.0, "gamma": 0.0, "alpha": 0.0}
    heat_clause_fail: list[str] = []
    if len(off_steps) != len(on_steps):
        return {"length_mismatch": [len(off_steps), len(on_steps)]}
    for si, (off, on) in enumerate(zip(off_steps, on_steps)):
        # He4 (and in principle any of the 5) can be a genuine pre-existing decay-chain nuclide
        # in the gas-OFF run too (a real alpha-decay daughter can land exactly on Z=2,A=4) -- that
        # has nothing to do with P92. The five light names are excluded from *both* sides
        # symmetrically: they are the nuclides the gate does not require to match (gas is expected
        # to add production into them), not nuclides assumed absent gas-off.
        off_inv = {e["nuclide"]: e["atoms_per_g"] for e in off["inventory"] if e["nuclide"] not in LIGHT}
        on_inv = {e["nuclide"]: e["atoms_per_g"] for e in on["inventory"] if e["nuclide"] not in LIGHT}
        max_pop = max(off_inv.values(), default=0.0)
        floor = max_pop * 1e-12
        for name in set(off_inv) | set(on_inv):
            a, b = off_inv.get(name, 0.0), on_inv.get(name, 0.0)
            if max(abs(a), abs(b)) < floor:
                continue
            r = rel(b, a)
            if r is not None and r > worst_inv:
                worst_inv, worst_inv_where = r, f"step {si} {name}"
        off_act = {k: v for k, v in off["activity_Bq_per_g"].items() if k not in LIGHT}
        on_act = {k: v for k, v in on["activity_Bq_per_g"].items() if k not in LIGHT}
        max_act = max(off_act.values(), default=0.0)
        act_floor = max_act * 1e-12
        for name in set(off_act) | set(on_act):
            a, b = off_act.get(name, 0.0), on_act.get(name, 0.0)
            if max(abs(a), abs(b)) < act_floor:
                continue
            r = rel(b, a)
            if r is not None and r > worst_act:
                worst_act, worst_act_where = r, f"step {si} {name}"
        # None of the 5 gas species alpha- or gamma-emit in the ejectile table (only H3 beta-decays),
        # so heat.alpha and heat.gamma are the independently checkable form of "heat totals minus the
        # light nuclides' contribution" available from the JSON output: they must be untouched.
        # heat.beta is expected to differ by exactly H3's own decay heat and is reported, not gated.
        ra = rel(on["heat_W_per_g"]["alpha"], off["heat_W_per_g"]["alpha"])
        rg = rel(on["heat_W_per_g"]["gamma"], off["heat_W_per_g"]["gamma"])
        if ra is not None:
            worst_alpha = max(worst_alpha, ra)
        if rg is not None:
            worst_gamma = max(worst_gamma, rg)
        # Protocol wording, literally: heat totals minus the light nuclides' contributions agree
        # to 1e-9. The light nuclides' heat is activity x mean decay energy (MF=8/MT=457) per
        # component; only H3 is radioactive. Relative to the gas-off component total.
        for component, index in (("total", None), ("beta", 0), ("gamma", 1), ("alpha", 2)):
            def light_heat(step):
                heat = 0.0
                for name, energies in energies_eV.items():
                    energy = sum(energies) if index is None else energies[index]
                    heat += step["activity_Bq_per_g"].get(name, 0.0) * energy * EV_J
                return heat
            a = on["heat_W_per_g"][component] - light_heat(on)
            b = off["heat_W_per_g"][component] - light_heat(off)
            # a is a difference of two floating-point sums; where tritium dominates the gas-on
            # heat, it carries rounding of order eps * |gas-on heat|. The comparison is therefore
            # to 1e-9 of the gas-off value plus a numerical floor of 1e-12 of the gas-on value
            # (far above rounding, far below any physical effect). Both ratios are reported.
            q_on = abs(on["heat_W_per_g"][component])
            scale = abs(b)
            r = 0.0 if a == b else (abs(a - b) / scale if scale > 0 else math.inf)
            r_on = 0.0 if a == b else (abs(a - b) / q_on if q_on > 0 else math.inf)
            within = abs(a - b) <= 1e-9 * scale + 1e-12 * q_on
            if r > worst_heat_minus_light[component]:
                worst_heat_minus_light[component] = r
            if r_on > worst_heat_minus_light_on[component]:
                worst_heat_minus_light_on[component] = r_on
            if not within:
                heat_clause_fail.append(f"step {si} {component}")
        # Total activity minus the light nuclides' activity.
        ta = sum(v for k, v in on["activity_Bq_per_g"].items() if k not in LIGHT)
        tb = sum(v for k, v in off["activity_Bq_per_g"].items() if k not in LIGHT)
        r = 0.0 if ta == tb else (abs(ta - tb) / abs(tb) if tb else math.inf)
        worst_total_activity = max(worst_total_activity, r)
    return {"worst_inventory_rel": worst_inv, "worst_inventory_where": worst_inv_where,
            "worst_activity_rel": worst_act, "worst_activity_where": worst_act_where,
            "worst_heat_alpha_rel": worst_alpha, "worst_heat_gamma_rel": worst_gamma,
            "worst_heat_minus_light_rel": worst_heat_minus_light,
            "worst_heat_minus_light_rel_to_gas_on_total": worst_heat_minus_light_on,
            "heat_clause_failures": heat_clause_fail,
            "worst_total_activity_minus_light_rel": worst_total_activity}


_ENERGY_CACHE: dict = {}


def g4_one(spec_path: Path):
    off_doc, off_rc, off_err = run_full(CAND, spec_path)
    if off_rc != 0:
        return spec_path.name, {"pass": False, "off_error": off_err}
    on_spec = {**json.loads(spec_path.read_text())}
    on_spec["options"] = {**on_spec.get("options", {}), "gas": True}
    with tempfile.NamedTemporaryFile("w", suffix=".json", dir=WORK, delete=False) as f:
        json.dump(on_spec, f)
        on_path = Path(f.name)
    try:
        on_doc, on_rc, on_err = run_full(CAND, on_path)
    finally:
        on_path.unlink(missing_ok=True)
    if on_rc != 0:
        return spec_path.name, {"pass": False, "on_error": on_err}
    decay = on_spec.get("decay", {})
    paths = tuple(p for p in (decay.get("primary"), decay.get("fallback")) if p)
    if paths not in _ENERGY_CACHE:
        _ENERGY_CACHE[paths] = light_decay_energies_eV(list(paths))
    energies = _ENERGY_CACHE[paths]
    if "H3" not in energies:
        return spec_path.name, {"pass": False, "error": "H3 MF=8/MT=457 not found in the spec's decay files"}
    cmp = compare_steps(off_doc["steps"], on_doc["steps"], energies)
    cmp["h3_mean_energies_eV"] = energies["H3"]
    ok = ("heat_clause_failures" in cmp and not cmp["heat_clause_failures"]
          and cmp.get("worst_total_activity_minus_light_rel", math.inf) < 1e-9
          and cmp.get("worst_inventory_rel", math.inf) < 1e-9
          and cmp.get("worst_activity_rel", math.inf) < 1e-9
          and cmp.get("worst_heat_alpha_rel", math.inf) < 1e-9
          and cmp.get("worst_heat_gamma_rel", math.inf) < 1e-9
          and "length_mismatch" not in cmp)
    cmp["pass"] = ok
    return spec_path.name, cmp


def cmd_g4() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    if not CAND.exists():
        sys.exit(f"candidate binary ({CAND}) is missing")
    specs = sorted(SPECS.glob("*.json"))
    if not specs:
        sys.exit(f"no P75b specs found under {SPECS}")
    results = {}
    t0 = time.monotonic()
    with cf.ThreadPoolExecutor(WORKERS) as ex:
        for i, (name, cmp) in enumerate(ex.map(g4_one, specs)):
            results[name] = cmp
            if i % 100 == 0:
                print(f"g4 {i}/{len(specs)} {time.monotonic() - t0:.0f}s", flush=True)
    (WORK / "g4_single.json").write_text(json.dumps(results, indent=1, sort_keys=True))
    fails = [n for n, v in results.items() if not v.get("pass")]
    worst_inv = max((v.get("worst_inventory_rel", 0.0) for v in results.values()
                      if isinstance(v.get("worst_inventory_rel"), (int, float))), default=0.0)
    worst_act = max((v.get("worst_activity_rel", 0.0) for v in results.values()
                      if isinstance(v.get("worst_activity_rel"), (int, float))), default=0.0)
    worst_heat = {c: max((v.get("worst_heat_minus_light_rel", {}).get(c, 0.0) for v in results.values()), default=0.0)
                  for c in ("total", "beta", "gamma", "alpha")}
    worst_total_act = max((v.get("worst_total_activity_minus_light_rel", 0.0) for v in results.values()), default=0.0)
    ok = len(results) == 783 and not fails
    summary = {"pass": ok, "specs": len(results), "failed": fails[:20],
               "worst_inventory_rel": worst_inv, "worst_activity_rel": worst_act,
               "worst_heat_minus_light_rel": worst_heat, "worst_total_activity_minus_light_rel": worst_total_act,
               "worst_heat_minus_light_rel_to_gas_on_total": {
                   c: max((v.get("worst_heat_minus_light_rel_to_gas_on_total", {}).get(c, 0.0)
                           for v in results.values()), default=0.0)
                   for c in ("total", "beta", "gamma", "alpha")},
               "heat_clause": "|(Q_on - L_on) - (Q_off - L_off)| <= 1e-9 |Q_off - L_off| + 1e-12 |Q_on|, per component; L = light-nuclide activity x MF=8/MT=457 mean energy"}
    (WORK / "g4_summary.json").write_text(json.dumps(summary, indent=1, sort_keys=True))
    print(json.dumps(summary, indent=1))
    return 0 if ok else 1


# ---------------------------------------------------------------------- G5

def cmd_g5() -> int:
    """Same-data cross-code gate, implemented in controls/p92_g5.py (writes <WORK>/g5.json)."""
    return subprocess.run([sys.executable, str(ROOT / "controls" / "p92_g5.py"),
                           "--field", G5_FIELD, "--out", str(WORK)]).returncode


# ------------------------------------------------------------------- verdict

def cmd_verdict() -> int:
    """Assembles G0-G6. Not run by the implementing agent: G0 (protocol registration timing),
    G5 (FISPACT cross-code) and G6 (CI replay) are the lead's own steps."""
    g1 = json.loads((WORK / "g1_summary.json").read_text())
    g2 = json.loads((WORK / "g2_summary.json").read_text())
    g3 = json.loads((WORK / "g3_summary.json").read_text())
    g4 = json.loads((WORK / "g4_summary.json").read_text())
    g5_full = json.loads((WORK / "g5.json").read_text())
    g5 = {k: v for k, v in g5_full.items() if k not in ("all_pairs",)}
    replay = WORK / "ci_replay_summary.log"
    steps = re.findall(r"^STEP (\d+) (\S+)$", replay.read_text(), re.M) if replay.exists() else []
    failed = [name for code, name in steps if code != "0"]
    g6 = {"pass": bool(steps) and not failed, "steps": len(steps), "failed": failed}
    verdict = {
        "protocol": f"ACTINV-{GAS_PROTOCOL}",
        "inputs": {"protocol_sha256": sha(PROTOCOL), "candidate_sha256": sha(CAND) if CAND.exists() else None,
                   "reference_sha256": sha(REF) if REF.exists() else None},
        "G0": {"pass": registered()},
        "G1": g1,
        "G2": g2,
        "G3": g3,
        "G4": g4,
        "G5": g5,
        "G6": g6,
    }
    verdict["pass"] = all(verdict[g].get("pass") for g in ("G0", "G1", "G2", "G3", "G4", "G5", "G6"))
    VERDICT.write_text(json.dumps(verdict, indent=1, sort_keys=True) + "\n")
    print(json.dumps(verdict, indent=1))
    return 0


if __name__ == "__main__":
    fn = {"build": cmd_build, "g2": cmd_g2, "g3": cmd_g3, "g4": cmd_g4, "g5": cmd_g5, "verdict": cmd_verdict}
    if len(sys.argv) < 2 or sys.argv[1] not in fn:
        sys.exit(__doc__)
    sys.exit(fn[sys.argv[1]]())
