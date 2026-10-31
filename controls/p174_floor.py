#!/usr/bin/env python3
"""P174 harness (protocols/ACTINV-P174_PROTOCOL.md): below-floor states in activity and heat totals.

Four arms run from one binary, selected by the spec field `options.below_floor`:
  status_quo  the field is absent (today's behaviour)
  A           "bound"        below-floor states leave the activity, heat and dose totals; their bound is reported
  B           "refine"       as A, and a step whose bound exceeds REL_TOL of its total is re-solved at CRAM-48 first
  C           "equilibrium"  below-floor states that meet five conditions (threshold, parents known, no cycle, no
                             memory, rate separation) are replaced by their production-limited value; the rest get
                             B's treatment: the step is refined at CRAM-48 when B's trigger fires, then bounded

Modes (each merges its section into results/p174_floor.json):
  make-refs    held-out references, mpmath (needs mpmath; the only mode that does), -> results/p174/refs
  solver       P168's 19 cases and the six held-out cases, CRAM-16 and CRAM-48, every arm, REPEATS timed runs each,
               plus screen runs of case 5d and the held-out cases
  nonregression  every tracked actinv-spec-1 example and control fixture: the master binary (ACTINV_BASE_BIN) and the
               three arms of the candidate binary (ACTINV_BIN), `steps[*]` compared ignoring added fields
  all          solver, then nonregression
Needs numpy for the one-group libraries (as controls/p168_limits.py). Run inside the bounded cgroup scope of AGENTS.md,
one job at a time. Reuses controls/p168 (cases, references, problem writer, comparison) and p168_limits.run_actinv.

Usage: ACTINV_BIN=target/release/actinv ACTINV_BASE_BIN=<master binary> p174_floor.py {solver|nonregression|all}
                [--run-dir DIR] [--record FILE] [--cases NAME,...] [--repeats N]
       p174_floor.py make-refs [--out DIR]
       p174_floor.py --self-test
"""
import argparse
import hashlib
import json
import math
import os
import platform
import re
import subprocess
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "controls"))
import p168_limits as base  # noqa: E402  run_actinv, tail, sha256, git, cgroup_record
from p168 import cases as p168_cases, compare, to_actinv  # noqa: E402

P168_REFS = ROOT / "results" / "p168" / "refs"
P168_MANIFEST = ROOT / "results" / "p168" / "refs_manifest.json"
P174_REFS = ROOT / "results" / "p174" / "refs"
P174_MANIFEST = ROOT / "results" / "p174" / "refs_manifest.json"
RECORD = ROOT / "results" / "p174_floor.json"
RUN_DIR = ROOT / "target" / "p174-run"
SCHEMA = "actinv-p174-floor-1"
BASE_COMMIT = "b1a1cc4"  # the commit P174 is registered on; candidate sources are the crates/ files that differ from it

ARMS = {"status_quo": None, "A": "bound", "B": "refine", "C": "equilibrium"}  # arm -> options.below_floor (None: absent)
CANDIDATES = ("A", "B", "C")
RECONSTRUCT_THRESHOLD = 50.0  # C reconstructs a below-floor state only if lambda x step length is at least this
SEPARATION_RATIO = 50.0  # ... and only if its removal rate is at least this multiple of that of each resolved ancestor feeding it
MEMORY_FRACTION = 1e-6  # ... and only if its start-of-step value, decayed over the step, is at most this share of the result
ACTINIDE_CASES = ("h7_u238_chain_real", "h8_th232_chain_real")  # "no real activity lost": totals within TOL_TOTAL
ORDERS = (16, 48)
REPEATS = 5  # timed runs per solver case, order and arm (the first is scored)
NONREG_REPEATS = 3
TIMEOUT_S = 1800
REL_TOL = 1e-6  # B refines a step whose bound exceeds this share of the step's reported activity total
TOL_TOTAL = 1e-6  # a total holds when its relative error is at most this
NOISE_S = 0.020  # wall-time differences below this measure process start-up, not the solver
NOISE_FRACTION = 0.10  # ... and so do differences below this share of the status quo's median wall time
COST_RATIO = 2.0  # B's wall time against the status quo's where B refines
SCREEN_BMIN = 1e-8  # options.screen.bmin_atoms_per_g of the screen runs (the default pruning bound)
ENERGIES_EV = {"e_light_eV": 1.0e5, "e_em_eV": 2.0e5, "e_heavy_eV": 3.0e5}  # every radioactive synthetic nuclide
EV = 1.602176634e-19  # crates/actinv-core/src/run.rs
ALPHA0 = {16: 2.124853710495224e-16, 48: 2.258038182743983e-47}  # crates/actinv-core/src/cram_coeffs.rs
OUTPUTS = ["inventory", "activity", "heat", "ledger", "certificate"]
GROUP_PUBLIC = "public"  # top-level examples/*.json: every one must run
GROUP_FIXTURE = "fixture"  # other tracked specs under examples/ and controls/: listed when the master binary cannot run them


def constants():
    return {"arms": ARMS, "orders": list(ORDERS), "repeats": REPEATS, "nonregression_repeats": NONREG_REPEATS,
            "timeout_s": TIMEOUT_S, "rel_tol": REL_TOL, "tol_total": TOL_TOTAL, "noise_s": NOISE_S,
            "noise_fraction": NOISE_FRACTION, "cost_ratio": COST_RATIO,
            "reconstruct_threshold": RECONSTRUCT_THRESHOLD, "separation_ratio": SEPARATION_RATIO, "memory_fraction": MEMORY_FRACTION,
            "actinide_cases": list(ACTINIDE_CASES), "screen_bmin_atoms_per_g": SCREEN_BMIN,
            "energies_eV": ENERGIES_EV, "ev_J": EV, "alpha0": {str(k): v for k, v in ALPHA0.items()},
            "base_commit": BASE_COMMIT}


# ---------------------------------------------------------------------------------------------- held-out cases
# Each: name, description, mode, mass (g), nuclides (name, Z, A, LISO, half-life in s as an exact decimal string with at
# most 7 significant digits, or None for stable), decays (name -> [(daughter, RTYP, RFS, branching)]), reactions
# (source, MT, [(product, sigma in b)]), flux (n/cm2/s), initial atoms/g, schedule [(duration s, flux multiplier, label)],
# frozen (trace-mode reservoir nuclides). Changing anything here changes the protocol.
def _triple(prefix, za, half_life, n0, steps, desc):
    names = [f"{symbol}{za[1]}" for symbol in prefix]
    nuclides = [(names[0], za[0], za[1], 0, half_life), (names[1], za[0] + 1, za[1], 0, half_life),
                (names[2], za[0] + 2, za[1], 0, half_life), (names[3], za[0] + 3, za[1], 0, None)]
    decays = {names[i]: [(names[i + 1], 1, 0, "1")] for i in range(3)}
    return {"desc": desc, "mode": "coupled", "mass": 1.0, "nuclides": nuclides, "decays": decays, "reactions": [],
            "flux": "0", "n0": {names[0]: n0}, "schedule": [(d, "0", f"decay {d} s") for d in steps], "frozen": ()}


def _pulsed():
    schedule = []
    for pulse in range(1, 21):
        schedule.append(("0.001", "1", f"pulse {pulse} on"))
        schedule.append(("0.099", "0", f"pulse {pulse} off"))
    schedule.append(("1", "0", "cool 1 s"))
    return {"desc": "Ir-191(n,g)Ir-192 (sigma 100 b, flux 1e18) with a synthetic Ir-192 half-life of 0.01 s -> Pt-192; "
                    "20 pulses of 1 ms every 0.1 s, then one 1 s cooling step (100 half-lives)",
            "mode": "coupled", "mass": 1.0,
            "nuclides": [("Ir191", 77, 191, 0, None), ("Ir192", 77, 192, 0, "0.01"), ("Pt192", 78, 192, 0, None)],
            "decays": {"Ir192": [("Pt192", 1, 0, "1")]},
            "reactions": [("Ir191", 102, [("Ir192", "100")])], "flux": "1e18", "n0": {"Ir191": "1e22"},
            "schedule": schedule, "frozen": ()}


def _real_chain(desc, nuclides, decays, n0, steps):
    """A natural decay chain with ENDF/B-VIII.0 half-lives and branchings (see the protocol for what is omitted)."""
    return {"desc": desc, "mode": "coupled", "mass": 1.0, "nuclides": nuclides, "decays": decays, "reactions": [],
            "flux": "0", "n0": n0, "schedule": [(d, "0", f"decay {d} s") for d in steps], "frozen": (), "dps": 500}


# ENDF/B-VIII.0 (actinv-data/v1.2.0/decay/endf-b-viii-0_decay.dat, MF8/MT457) half-lives in s and branching ratios.
# Omitted, because a branch needs RTYP 1.5 or 6 (which the synthetic writer does not express) or is below 1e-7:
# spontaneous fission of U-238, U-234, Th-230, Th-232; beta-neutron of Tl-210 (8.5e-5); alpha of Pb-210 (1.9e-8);
# beta of At-218 (1e-3). Bi-210's alpha branch 1.32e-6 is written 1.3e-6 so that 1 minus it is a 7-digit field.
H7_NUCLIDES = [("U238", 92, 238, 0, "1.40999e17"), ("Th234", 90, 234, 0, "2082240"), ("Pa234m1", 91, 234, 1, "69.54"),
               ("Pa234", 91, 234, 0, "24120"), ("U234", 92, 234, 0, "7.74739e12"), ("Th230", 90, 230, 0, "2.37881e12"),
               ("Ra226", 88, 226, 0, "5.04922e10"), ("Rn222", 86, 222, 0, "330350.4"), ("Po218", 84, 218, 0, "185.88"),
               ("At218", 85, 218, 0, "1.5"), ("Pb214", 82, 214, 0, "1608"), ("Bi214", 83, 214, 0, "1194"),
               ("Po214", 84, 214, 0, "0.0001643"), ("Tl210", 81, 210, 0, "78"), ("Pb210", 82, 210, 0, "7.005787e8"),
               ("Bi210", 83, 210, 0, "433036.8"), ("Po210", 84, 210, 0, "1.195569e7"), ("Tl206", 81, 206, 0, "252.12"),
               ("Pb206", 82, 206, 0, None)]
H7_DECAYS = {"U238": [("Th234", 4, 0, "1")], "Th234": [("Pa234m1", 1, 1, "1")],
             "Pa234m1": [("U234", 1, 0, "0.9984"), ("Pa234", 3, 0, "0.0016")], "Pa234": [("U234", 1, 0, "1")],
             "U234": [("Th230", 4, 0, "1")], "Th230": [("Ra226", 4, 0, "1")], "Ra226": [("Rn222", 4, 0, "1")],
             "Rn222": [("Po218", 4, 0, "1")], "Po218": [("Pb214", 4, 0, "0.9998"), ("At218", 1, 0, "0.0002")],
             "At218": [("Bi214", 4, 0, "1")], "Pb214": [("Bi214", 1, 0, "1")],
             "Bi214": [("Po214", 1, 0, "0.99979"), ("Tl210", 4, 0, "0.00021")], "Po214": [("Pb210", 4, 0, "1")],
             "Tl210": [("Pb210", 1, 0, "1")], "Pb210": [("Bi210", 1, 0, "1")],
             "Bi210": [("Po210", 1, 0, "0.9999987"), ("Tl206", 4, 0, "0.0000013")], "Po210": [("Pb206", 4, 0, "1")],
             "Tl206": [("Pb206", 1, 0, "1")]}
H8_NUCLIDES = [("Th232", 90, 232, 0, "4.43384e17"), ("Ra228", 88, 228, 0, "1.814562e8"), ("Ac228", 89, 228, 0, "22140"),
               ("Th228", 90, 228, 0, "6.033813e7"), ("Ra224", 88, 224, 0, "316224"), ("Rn220", 86, 220, 0, "55.6"),
               ("Po216", 84, 216, 0, "0.145"), ("Pb212", 82, 212, 0, "38304"), ("Bi212", 83, 212, 0, "3633"),
               ("Po212", 84, 212, 0, "2.99e-7"), ("Tl208", 81, 208, 0, "183.18"), ("Pb208", 82, 208, 0, None)]
H8_DECAYS = {"Th232": [("Ra228", 4, 0, "1")], "Ra228": [("Ac228", 1, 0, "1")], "Ac228": [("Th228", 1, 0, "1")],
             "Th228": [("Ra224", 4, 0, "1")], "Ra224": [("Rn220", 4, 0, "1")], "Rn220": [("Po216", 4, 0, "1")],
             "Po216": [("Pb212", 4, 0, "1")], "Pb212": [("Bi212", 1, 0, "1")],
             "Bi212": [("Po212", 1, 0, "0.6406"), ("Tl208", 4, 0, "0.3594")], "Po212": [("Pb208", 4, 0, "1")],
             "Tl208": [("Pb208", 1, 0, "1")]}

HELD_OUT = {
    "h1_triple_T10s": _triple(("Mn", "Fe", "Co", "Ni"), (25, 62), "10", "1e22", ("10", "50", "150", "300", "1000"),
                              "Mn-62 -> Fe-62 -> Co-62 -> stable Ni-62, three members of half-life 10 s (Jordan block "
                              "of size 3), decay only; cumulative 1, 6, 21, 51, 151 half-lives"),
    "h2_triple_T3600s": _triple(("Nb", "Mo", "Tc", "Ru"), (41, 90), "3600", "1e20",
                                ("3600", "7200", "216000", "72000", "360000"),
                                "Nb-90 -> Mo-90 -> Tc-90 -> stable Ru-90, three members of half-life 3600 s, decay "
                                "only; cumulative 1, 3, 63, 83, 183 half-lives"),
    "h3_triple_T1e6s": _triple(("Sb", "Te", "I", "Xe"), (51, 124), "1000000", "1e24",
                               ("100000", "40000000", "50000000", "100000000"),
                               "Sb-124 -> Te-124 -> I-124 -> stable Xe-124, three members of half-life 1e6 s, decay "
                               "only; cumulative 0.1, 40.1, 90.1, 190.1 half-lives"),
    "h4_isomer_cooled_1d": {
        "desc": "Co-58m1 (synthetic half-life 60 s) at 1e20 atoms/g, decaying by isomeric transition to Co-58 g "
                "(70.86 d) -> Fe-58; no irradiation, cooled 600 s, 3000 s and 82800 s (1 d in all)",
        "mode": "coupled", "mass": 1.0,
        "nuclides": [("Co58m1", 27, 58, 1, "60"), ("Co58", 27, 58, 0, "6122304"), ("Fe58", 26, 58, 0, None)],
        "decays": {"Co58m1": [("Co58", 3, 0, "1")], "Co58": [("Fe58", 2, 0, "1")]},
        "reactions": [], "flux": "0", "n0": {"Co58m1": "1e20"},
        "schedule": [("600", "0", "cool 10 min"), ("3000", "0", "cool 1 h"), ("82800", "0", "cool 1 d")],
        "frozen": ()},
    "h5_pulsed_1s_cool": _pulsed(),
    "h7_u238_chain_real": _real_chain(
        "U-238 series to Pb-206 with ENDF/B-VIII.0 half-lives and branchings (19 nuclides), 1 g of pure U-238 "
        "(2.5298e21 atoms/g), decay only, cooled to 1e6 y in steps of 100 y, 9900 y, 90000 y and 900000 y; Ra-226 onward "
        "reaches equilibrium with Th-230 and the short-lived members sit far below the CRAM-16 scale",
        H7_NUCLIDES, H7_DECAYS, {"U238": "2.5298e21"},
        ("3155760000", "312420240000", "2840184000000", "28401840000000")),
    "h8_th232_chain_real": _real_chain(
        "Th-232 series to Pb-208 with ENDF/B-VIII.0 half-lives and branchings (12 nuclides), 1 g of pure Th-232 "
        "(2.5953e21 atoms/g), decay only, cooled to 1e4 y in steps of 10 y, 90 y, 900 y and 9000 y",
        H8_NUCLIDES, H8_DECAYS, {"Th232": "2.5953e21"}, ("315576000", "2840184000", "28401840000", "284018400000")),
    "h6_trace_near_floor": {
        "desc": "Trace mode, flux 1e14. Bulk Mn-55 at 1e22 atoms/g (13 b -> Mn-56, 9284.04 s) with impurities Co-59 at "
                "3e6 atoms/g (37 b -> Co-60, 5.2714 y) and Sc-45 at 2e5 atoms/g (27 b -> Sc-46, 83.79 d); irradiate 1 h "
                "then cool 1 h, 1 d, 10 d. After the irradiation the CRAM-16 scale is about 8.7 atoms/g, between "
                "Sc-46 (1.9 atoms/g) and Co-60 (40 atoms/g)",
        "mode": "trace", "mass": 1.0,
        "nuclides": [("Mn55", 25, 55, 0, None), ("Mn56", 25, 56, 0, "9284.04"), ("Fe56", 26, 56, 0, None),
                     ("Co59", 27, 59, 0, None), ("Co60", 27, 60, 0, "166352700"), ("Ni60", 28, 60, 0, None),
                     ("Sc45", 21, 45, 0, None), ("Sc46", 21, 46, 0, "7239456"), ("Ti46", 22, 46, 0, None)],
        "decays": {"Mn56": [("Fe56", 1, 0, "1")], "Co60": [("Ni60", 1, 0, "1")], "Sc46": [("Ti46", 1, 0, "1")]},
        "reactions": [("Mn55", 102, [("Mn56", "13")]), ("Co59", 102, [("Co60", "37")]),
                      ("Sc45", 102, [("Sc46", "27")])],
        "flux": "1e14", "n0": {"Mn55": "1e22", "Co59": "3e6", "Sc45": "2e5"},
        "schedule": [("3600", "1", "irradiate 1 h"), ("3600", "0", "cool 1 h"), ("82800", "0", "cool 1 d"),
                     ("777600", "0", "cool 10 d")],
        "frozen": ("Mn55", "Co59", "Sc45")},
}


def case_list():
    """Every scored case: P168's 19 (selection) then the six held-out, as dicts with name, mode, pulses, trace, set."""
    out = [dict(case, set="selection") for case in p168_cases.CASES]
    out += [{"name": name, "mode": spec["mode"], "pulses": None, "trace": spec["mode"] == "trace", "set": "heldout"}
            for name, spec in HELD_OUT.items()]
    return out


def refs_dir(case_set):
    return P168_REFS if case_set == "selection" else P174_REFS


def make_refs(out_dir):
    """Write the held-out references (30 digits, mpmath at 300 digits cross-checked at 600) and their manifest."""
    import mpmath as mp
    from p168 import make_cases as mc, refs
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, spec in HELD_OUT.items():
        nucs = [mc.nuc(n, z, a, liso, half) for n, z, a, liso, half in spec["nuclides"]]
        sched = [mc.sched_item(dt, mult, label) for dt, mult, label in spec["schedule"]]
        reactions = [{"src": src, "mt": mt, "products": list(products)} for src, mt, products in spec["reactions"]]
        model = refs.Model(nucs, spec["decays"], reactions, spec["flux"])
        steps = [(s["dt_s"], s["flux_multiplier"]) for s in sched]
        result, worst = refs.checked_propagate(model, spec["n0"], steps, spec.get("dps", 300), spec["frozen"])
        extra = {"actinv_mode": spec["mode"], "precision_cross_check_max_rel_diff": refs.fmt(worst, 3)}
        doc = refs.case_json(name, spec["desc"], model, spec["mass"], spec["n0"], sched, result, extra)
        refs.dump(doc, out_dir / f"{name}.json")
        print(f"{name}.json  (cross-check {mp.nstr(worst, 3)})", flush=True)
    files = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out_dir.glob("*.json"))}
    manifest = {"schema": "actinv-p174-refs-1", "mpmath_version": mp.__version__,
                "digits": {"working": "300, cross-checked at 600", "recorded": 30},
                "generator": "controls/p174_floor.py make-refs", "files": files}
    (out_dir.parent / "refs_manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    print(f"manifest written to {out_dir.parent / 'refs_manifest.json'}")


# ------------------------------------------------------------------------------------------------- problem files
def write_overrides(ref_doc, path):
    """An actinv-decay-overrides-1 document giving every radioactive synthetic nuclide the same mean decay energies,
    so that heat is a response of the synthetic cases (the synthetic ENDF files carry none)."""
    entries = [dict({"nuclide": n["name"], "source": "P174 synthetic energies",
                     "reason": "heat is a response of the synthetic cases"}, **ENERGIES_EV)
               for n in ref_doc["nuclides"] if n["half_life_s"]]
    Path(path).write_text(json.dumps({"schema": "actinv-decay-overrides-1", "overrides": entries}, indent=1))
    return Path(path)


def build_problem(ref_doc, case, order, arm, out, screen):
    """P168's problem for the case and order, plus decay energies, the arm's policy and (screen runs) options.screen."""
    out = Path(out)
    info = to_actinv.generate(ref_doc, out, order, case["mode"], case["pulses"])
    problem = json.loads(info["problem"].read_text())
    problem["decay"]["overrides"] = str(write_overrides(ref_doc, out / "overrides.json").resolve())
    options = problem["options"]
    options["outputs"] = OUTPUTS
    if ARMS[arm] is not None:
        options["below_floor"] = ARMS[arm]
    if screen:
        options["prune"] = "rate"
        options["screen"] = {"bmin_atoms_per_g": SCREEN_BMIN}
    info["problem"].write_text(json.dumps(problem, indent=1))
    info["problem_sha256"] = base.sha256(info["problem"])
    return info


# ------------------------------------------------------------------------------------------------- extraction
def _bound_record(raw):
    block = raw.get("below_floor_bound")
    if block is None:
        return None
    return {"policy": block.get("policy"), "order": block.get("order"), "refined": block.get("refined"),
            "n_states_excluded": block.get("n_states_excluded"),
            "numerical_floor_atoms_per_g": block.get("numerical_floor_atoms_per_g"),
            "activity_Bq_per_g": block.get("activity_Bq_per_g"), "heat_W_per_g": block.get("heat_W_per_g"),
            "dose": block.get("dose"), "n_reconstructed": block.get("n_reconstructed"),
            "reconstructed_states": block.get("reconstructed_states"),
            "reconstruct_threshold": block.get("reconstruct_threshold"),
            "n_cycle_fallback": block.get("n_cycle_fallback"), "n_parent_fallback": block.get("n_parent_fallback"),
            "n_memory_fallback": block.get("n_memory_fallback"),
            "n_separation_fallback": block.get("n_separation_fallback")}


def extract_steps(reference, raw_steps, scheduled, certified=None):
    """Per kept reference step, the raw numbers the checker recomputes every metric from. `raw_steps` are ACTINV's
    result steps (a leading t = 0 entry is dropped); `certified` is result['screen']['certified'] for screen runs.
    Returns (kept, summary over all steps)."""
    dropped = 1 if raw_steps and float(raw_steps[0]["t_s"]) == 0.0 else 0
    body = raw_steps[dropped:]
    if len(body) != scheduled:
        raise ValueError(f"result has {len(body)} steps, schedule has {scheduled}")
    by_key = {key: name for name, key in reference.keys.items()}
    lam = {name: float(value) for name, value in reference.lam.items()}
    summary = {"n_steps_reconstructed": sum(1 for s in body if (s.get("below_floor_bound") or {}).get("n_reconstructed")),
               "n_steps_flagged": sum(1 for s in body if s.get("n_states_below_floor", 0) > 0),
               "max_n_below": max((s.get("n_states_below_floor", 0) for s in body), default=0),
               "n_steps_bound_present": sum(1 for s in body if s.get("below_floor_bound") is not None),
               "refined_step_ids": [s["step"] for s in body if (s.get("below_floor_bound") or {}).get("refined")]}
    kept = []
    for ref_step in reference.steps:
        index = ref_step["step"] - 1
        raw = body[index]
        floor = float(raw["numerical_floor_atoms_per_g"])
        table = raw.get("activity_Bq_per_g", {})
        excluded, n_excluded, inventory_max, unmapped = [], 0, 0.0, 0
        table_dev, excluded_reported = 0.0, 0.0
        block = raw.get("below_floor_bound") or {}
        rebuilt_actinv = set(block.get("reconstructed_states") or [])  # C's reconstructed states are kept, not excluded
        reconstructed = []
        for entry in raw["inventory"]:
            atoms = float(entry["atoms_per_g"])
            inventory_max = max(inventory_max, atoms)
            rebuilt = entry["nuclide"] in rebuilt_actinv
            below = 0.0 < atoms < floor and not rebuilt
            n_excluded += below
            name = by_key.get((entry["Z"], entry["A"], entry["LISO"]))
            if name is None:
                unmapped += 1
                continue
            rate = lam.get(name, 0.0)
            if rebuilt:
                reconstructed.append(name)
            reported = float(table.get(entry["nuclide"], 0.0))
            if below:
                excluded.append(name)
                excluded_reported = max(excluded_reported, abs(reported))
            elif rate > 0.0:
                table_dev = max(table_dev, abs(reported - rate * atoms) / (rate * atoms))
        record = {"step": ref_step["step"], "step_id": raw["step"], "t_s": float(raw["t_s"]), "floor": floor,
                  "n_below": int(raw["n_states_below_floor"]), "n_excluded_inventory": n_excluded,
                  "excluded": excluded, "reconstructed": reconstructed,
                  "n_reconstructed_listed": len(rebuilt_actinv),
                  "dt_s": float(raw["t_s"]) - (float(body[index - 1]["t_s"]) if index else 0.0),
                  "unmapped": unmapped, "inventory_max": inventory_max,
                  "activity_total": math.fsum(float(v) for v in table.values()),
                  "heat_total": float(raw["heat_W_per_g"]["total"]), "table_dev": table_dev,
                  "excluded_reported_activity_max": excluded_reported, "bound": _bound_record(raw), "cert": None}
        if certified is not None:
            entry = certified.get(str(dropped + index), {})
            record["cert"] = {response: {k: entry[response].get(k) for k in ("edge", "lower", "upper", "bound_lower",
                                                                              "bound_upper")}
                              for response in ("activity.total", "heat.total") if response in entry}
        kept.append(record)
    return kept, summary


def p168_diagnostic(reference, body, trace, scheduled):
    """P168's inventory metrics for the run (information only; the candidates leave the inventory rows alone, B's
    refined steps excepted)."""
    try:
        scored = compare.compare_run(reference, body, trace, scheduled)
        return {"status": compare.status_of(scored["worst"]),
                "worst": {m: {"value": w["value"], "step": w["step"], "nuclide": w["nuclide"]}
                          for m, w in scored["worst"].items()}}
    except (ValueError, KeyError, TypeError, IndexError) as error:
        return {"error": str(error)}


# ----------------------------------------------------------------------------------------------- solver section
def median(values):
    ordered = sorted(values)
    n = len(ordered)
    return ordered[n // 2] if n % 2 else 0.5 * (ordered[n // 2 - 1] + ordered[n // 2])


def score_run(binary_sha, case, order, arm, screen, info, reference, result_path, stderr_path, status, timed_out, walls):
    record = {"set": case["set"], "case": case["name"], "cram_order": order, "arm": arm, "screen": screen,
              "mode": case["mode"], "steps_scheduled": info["steps"], "binary_sha256": binary_sha,
              "exit_status": status, "timed_out": timed_out, "wall_s": walls, "stderr_tail": base.tail(stderr_path),
              "result_bytes": result_path.stat().st_size if result_path.is_file() else None, "ms_internal": None,
              "ran": False, "reason": None, "steps": None, "summary": None, "ledger_below_floor": None,
              "p168": None}
    if status != 0 or timed_out:
        record["reason"] = "timeout" if timed_out else f"exit status {status}"
        return record
    try:
        document = json.loads(result_path.read_text())
        certified = (document.get("screen") or {}).get("certified") if screen else None
        kept, summary = extract_steps(reference, document["steps"], info["steps"], certified)
        record.update(ran=True, steps=kept, summary=summary, ms_internal=document.get("ms"),
                      ledger_below_floor=(document.get("ledger") or {}).get("below_floor"))
        if not screen:
            body = document["steps"][1:] if document["steps"] and float(document["steps"][0]["t_s"]) == 0.0 \
                else document["steps"]
            record["p168"] = p168_diagnostic(reference, body, case["trace"], info["steps"])
    except (OSError, ValueError, KeyError, TypeError) as error:
        record["reason"] = f"unreadable result: {error}"
    return record


def solver_runs(binary, binary_sha, case, order, run_dir, repeats, screen):
    ref_doc = json.loads((refs_dir(case["set"]) / f"{case['name']}.json").read_text())
    tag = "screen" if screen else "plain"
    outs, infos = {}, {}
    for arm in ARMS:
        outs[arm] = run_dir / case["name"] / f"cram{order}" / tag / arm
        infos[arm] = build_problem(ref_doc, case, order, arm, outs[arm], screen)
    reference = compare.Reference(ref_doc, infos["status_quo"]["steps"])
    walls = {arm: [] for arm in ARMS}
    first = {}
    for repeat in range(repeats):
        for arm in ARMS:  # interleaved, so a slow period touches every arm alike
            if repeat and first[arm][0] != 0:
                continue
            result = outs[arm] / ("result.json" if repeat == 0 else "result_repeat.json")
            result.unlink(missing_ok=True)
            status, timed_out, wall = base.run_actinv(binary, outs[arm] / "problem.json", result,
                                                      outs[arm] / "stderr.txt", TIMEOUT_S)
            walls[arm].append(wall)
            if repeat == 0:
                first[arm] = (status, timed_out)
            else:
                result.unlink(missing_ok=True)
    records = []
    for arm in ARMS:
        result = outs[arm] / "result.json"
        record = score_run(binary_sha, case, order, arm, screen, infos[arm], reference, result,
                           outs[arm] / "stderr.txt", first[arm][0], first[arm][1], walls[arm])
        result.unlink(missing_ok=True)
        records.append(record)
        print(f"{case['name']} cram{order} {arm}{' screen' if screen else ''}: "
              f"{'ran' if record['ran'] else 'FAILED ' + str(record['reason'])} median {median(walls[arm]):.3f} s"
              + (f", {record['summary']['n_steps_flagged']} flagged steps" if record["ran"] else ""), flush=True)
    return records


def solver_section(binary, binary_sha, run_dir, selected, repeats):
    runs = []
    for case in case_list():
        if case["name"] not in selected:
            continue
        for order in ORDERS:
            runs += solver_runs(binary, binary_sha, case, order, run_dir, repeats, False)
        if case["name"] in screen_cases():
            for order in ORDERS:
                runs += solver_runs(binary, binary_sha, case, order, run_dir, 1, True)
    return {"constants": constants(), "runs": runs, "partial": set(selected) != {c["name"] for c in case_list()}}


def screen_cases():
    return ["5d_defective_decay_triple", *HELD_OUT]


# -------------------------------------------------------------------------------------- nonregression section
def contains(old, new):
    """True when `new` holds every value `old` holds, recursively (new may carry added keys; lists must keep their
    length; scalars must be equal, a bool only equal to a bool)."""
    if isinstance(old, dict):
        return isinstance(new, dict) and all(k in new and contains(v, new[k]) for k, v in old.items())
    if isinstance(old, list):
        return isinstance(new, list) and len(old) == len(new) and all(contains(a, b) for a, b in zip(old, new))
    if isinstance(old, bool) or isinstance(new, bool):
        return type(old) is type(new) and old == new
    return old == new


def differing_steps(old_steps, new_steps):
    """Indices (0-based) of steps whose old content is not contained in the new one; a length change is -1."""
    if len(old_steps) != len(new_steps):
        return [-1]
    return [i for i, (a, b) in enumerate(zip(old_steps, new_steps)) if not contains(a, b)]


def discover():
    """Tracked JSON files under examples/ and controls/ whose top level is an actinv-spec-1 problem."""
    listed = subprocess.run(["git", "ls-files", "examples", "controls"], cwd=ROOT, capture_output=True, text=True,
                            timeout=60).stdout.split("\n")
    found = []
    for path in sorted(p for p in listed if p.endswith(".json")):
        try:
            document = json.loads((ROOT / path).read_text())
        except (OSError, ValueError):
            continue
        if isinstance(document, dict) and document.get("spec") == "actinv-spec-1":
            group = GROUP_PUBLIC if re.fullmatch(r"examples/[^/]+\.json", path) else GROUP_FIXTURE
            found.append({"path": path, "group": group, "spec_sha256": base.sha256(ROOT / path)})
    return found


def steps_hash(steps):
    return hashlib.sha256(json.dumps(steps, sort_keys=True).encode()).hexdigest()


def run_example(binary, problem, run_dir, label, repeats):
    """Run one problem `repeats` times; returns the first run's steps and a record of exits, wall times and hashes."""
    out = run_dir / label
    out.mkdir(parents=True, exist_ok=True)
    walls, hashes, exits, steps = [], [], [], None
    for repeat in range(repeats):
        result = out / f"result{repeat}.json"
        result.unlink(missing_ok=True)
        status, timed_out, wall = base.run_actinv(binary, problem, result, out / "stderr.txt", TIMEOUT_S)
        walls.append(wall)
        exits.append("timeout" if timed_out else status)
        if status == 0 and not timed_out:
            try:
                document = json.loads(result.read_text())
                hashes.append(steps_hash(document["steps"]))
                if steps is None:
                    steps = document["steps"]
            except (OSError, ValueError, KeyError):
                exits[-1] = "unreadable"
        result.unlink(missing_ok=True)
    refined = sum(1 for s in steps if (s.get("below_floor_bound") or {}).get("refined")) if steps else None
    rebuilt = sum(1 for s in steps if (s.get("below_floor_bound") or {}).get("n_reconstructed")) if steps else None
    return steps, {"exits": exits, "wall_s": walls, "steps_sha256": hashes, "n_steps": len(steps) if steps else None,
                   "n_refined": refined, "n_reconstructed_steps": rebuilt,
                   "stderr_tail": base.tail(out / "stderr.txt") if any(e != 0 for e in exits) else ""}


def nonregression_section(binary, binary_sha, base_binary, base_sha, run_dir, repeats):
    examples = []
    for item in discover():
        path = ROOT / item["path"]
        label = item["path"].replace("/", "__")
        spec = json.loads(path.read_text())
        steps_base, record_base = run_example(base_binary, path, run_dir, f"{label}.base", 1)
        entry = dict(item, base=record_base, runnable=steps_base is not None and record_base["exits"] == [0],
                     arms={}, flagged_steps=[], diff_steps={})
        if not entry["runnable"]:
            examples.append(entry)
            print(f"{item['path']}: not runnable on the master binary ({record_base['exits']})", flush=True)
            continue
        entry["flagged_steps"] = [i for i, s in enumerate(steps_base) if s.get("n_states_below_floor", 0) > 0]
        arm_steps = {}
        for arm, policy in ARMS.items():
            problem = path
            if policy is not None:
                patched = json.loads(json.dumps(spec))
                patched.setdefault("options", {})["below_floor"] = policy
                problem = run_dir / f"{label}.{arm}.problem.json"
                problem.write_text(json.dumps(patched, indent=1))
            arm_steps[arm], entry["arms"][arm] = run_example(binary, problem, run_dir, f"{label}.{arm}", repeats)
            if arm_steps[arm] is not None:
                entry["diff_steps"][arm] = differing_steps(steps_base, arm_steps[arm])
        examples.append(entry)
        print(f"{item['path']}: {len(steps_base)} steps, {len(entry['flagged_steps'])} flagged; differing steps "
              f"{ {a: len(d) for a, d in entry['diff_steps'].items()} }", flush=True)
    return {"constants": constants(), "base_binary_sha256": base_sha, "binary_sha256": binary_sha,
            "examples": examples}


# --------------------------------------------------------------------------------------------------- environment
def candidate_sources():
    """Hashes of the crates/ files (GUI crate excepted) that differ from the registration commit, tracked or not."""
    changed = base.git("diff", "--name-only", BASE_COMMIT, "--", "crates").split()
    changed += base.git("ls-files", "--others", "--exclude-standard", "crates").split()
    return {p: base.sha256(ROOT / p) for p in sorted(set(changed))
            if not p.startswith("crates/actinv-gui/") and (ROOT / p).is_file()}


def environment(binary_sha, base_sha):
    status = base.git("status", "--porcelain", "--untracked-files=no")
    env = {"binary_sha256": binary_sha, "base_binary_sha256": base_sha, "git_head": base.git("rev-parse", "HEAD"),
           "crates_tree": base.git("rev-parse", "HEAD:crates"), "clean_tree": status == "",
           "cgroup": base.cgroup_record(), "python": platform.python_version(), "platform": platform.platform(),
           "harness_sha256": base.sha256(Path(__file__)), "candidate_sources": candidate_sources(),
           "refs_manifests": {"p168": base.sha256(P168_MANIFEST),
                              "p174": base.sha256(P174_MANIFEST) if P174_MANIFEST.is_file() else None}}
    try:
        import numpy
        env["numpy"] = numpy.__version__
    except ImportError:
        sys.exit("numpy is required to write the one-group libraries")
    return env


def binary_from(name):
    if not os.environ.get(name):
        sys.exit(f"{name} is required")
    path = Path(os.environ[name]).resolve()
    if not path.is_file():
        sys.exit(f"{name} {path} is not a file")
    return path


def merge_record(path, sections, env):
    document = {"schema": SCHEMA, "protocol": "ACTINV-P174"}
    if Path(path).is_file():
        previous = json.loads(Path(path).read_text())
        if previous.get("schema") == SCHEMA:
            document = previous
    document["environment"] = env
    document.update(sections)
    Path(path).write_text(json.dumps(document, indent=1) + "\n")
    print(f"wrote {path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", nargs="?", choices=["make-refs", "solver", "nonregression", "all"])
    parser.add_argument("--run-dir", type=Path, default=RUN_DIR)
    parser.add_argument("--record", type=Path, default=RECORD)
    parser.add_argument("--out", type=Path, default=P174_REFS)
    parser.add_argument("--cases", help="comma-separated case names (a partial record fails the checker)")
    parser.add_argument("--repeats", type=int, default=REPEATS)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        sys.exit(0 if self_test() else 1)
    if args.mode is None:
        parser.error("a mode is required")
    if args.mode == "make-refs":
        make_refs(args.out)
        return
    args.run_dir, args.record = args.run_dir.resolve(), args.record.resolve()
    os.chdir(ROOT)  # problems name data by repository-relative path
    binary = binary_from("ACTINV_BIN")
    binary_sha = base.sha256(binary)
    base_binary = binary_from("ACTINV_BASE_BIN")
    env = environment(binary_sha, base.sha256(base_binary))
    args.run_dir.mkdir(parents=True, exist_ok=True)
    sections = {}
    if args.mode in ("solver", "all"):
        names = args.cases.split(",") if args.cases else [c["name"] for c in case_list()]
        unknown = [n for n in names if n not in {c["name"] for c in case_list()}]
        if unknown:
            sys.exit(f"unknown cases {unknown}")
        sections["solver"] = solver_section(binary, binary_sha, args.run_dir, set(names), args.repeats)
    if args.mode in ("nonregression", "all"):
        sections["nonregression"] = nonregression_section(binary, binary_sha, base_binary, env["base_binary_sha256"],
                                                          args.run_dir, NONREG_REPEATS)
    merge_record(args.record, sections, env)


# --------------------------------------------------------------------------------------------------- self-test
def _fake_reference():
    doc = {"case": "fake", "nuclides": [
        {"name": "P", "za": 27058, "liso": 0, "half_life_s": "1000"},
        {"name": "D", "za": 28058, "liso": 0, "half_life_s": "1000"},
        {"name": "S", "za": 29058, "liso": 0, "half_life_s": None}],
        "reference_steps": [{"step": 1, "label": "a", "t_end_s": "10",
                             "inventory_atoms_per_g": {"P": "1e10", "D": "1e5", "S": "1e12"}},
                            {"step": 2, "label": "b", "t_end_s": "20",
                             "inventory_atoms_per_g": {"P": "1e9", "D": "1e4", "S": "1e12"}}]}
    return compare.Reference(doc, 2)


def self_test():
    failures = []

    def check(label, condition):
        if not condition:
            failures.append(label)

    # contains(): added keys are ignored, any changed value or length is not
    check("added key", contains({"a": 1, "b": [1, {"c": 2}]}, {"a": 1, "b": [1, {"c": 2, "d": 3}], "e": 0}))
    check("changed value", not contains({"a": 1}, {"a": 2}))
    check("missing key", not contains({"a": 1}, {}))
    check("list length", not contains({"a": [1, 2]}, {"a": [1, 2, 3]}))
    check("bool is not int", not contains({"a": True}, {"a": 1}))
    check("int equals float", contains({"a": 1}, {"a": 1.0}))
    check("step lengths", differing_steps([{"a": 1}], [{"a": 1}, {"a": 1}]) == [-1])
    check("differing index", differing_steps([{"a": 1}, {"a": 2}], [{"a": 1}, {"a": 3}]) == [1])
    # extract_steps on a synthetic result: step 1 has D below the floor, excluded from the activity table
    reference = _fake_reference()
    lam = math.log(2) / 1000.0

    def raw_step(step, t, p, d, floor, n_below, excluded_in_table, bound):
        table = {"P": lam * p}
        if excluded_in_table:
            table["D"] = lam * d
        step_out = {"step": step, "t_s": t, "numerical_floor_atoms_per_g": floor, "n_states_below_floor": n_below,
                    "inventory": [{"nuclide": "P", "Z": 27, "A": 58, "LISO": 0, "atoms_per_g": p},
                                  {"nuclide": "D", "Z": 28, "A": 58, "LISO": 0, "atoms_per_g": d},
                                  {"nuclide": "S", "Z": 29, "A": 58, "LISO": 0, "atoms_per_g": 1e12}],
                    "activity_Bq_per_g": table, "heat_W_per_g": {"total": 1e-9}}
        if bound is not None:
            step_out["below_floor_bound"] = bound
        return step_out

    bound = {"policy": "bound", "order": 16, "refined": False, "n_states_excluded": 1,
             "numerical_floor_atoms_per_g": 2e6, "activity_Bq_per_g": 1.0, "heat_W_per_g": 1e-12, "dose": None}
    steps = [raw_step(1, 0.0, 0, 0, 0, 0, True, None),  # a leading t = 0 entry is dropped
             raw_step(1, 10.0, 1e10, 1e5, 2e6, 1, False, bound),
             raw_step(2, 20.0, 1e9, 1e4, 2e6, 1, False, bound)]
    kept, summary = extract_steps(reference, steps, 2)
    check("kept steps", [k["step"] for k in kept] == [1, 2])
    check("excluded", kept[0]["excluded"] == ["D"] and kept[0]["n_excluded_inventory"] == 1)
    check("excluded not in table", kept[0]["excluded_reported_activity_max"] == 0.0)
    check("table consistent", kept[0]["table_dev"] < 1e-15)
    check("activity total", abs(kept[0]["activity_total"] - lam * 1e10) < 1e-18)
    check("bound recorded", kept[0]["bound"]["activity_Bq_per_g"] == 1.0 and summary["n_steps_bound_present"] == 2)
    check("summary", summary["n_steps_flagged"] == 2 and summary["refined_step_ids"] == [])
    leaking = [steps[0], raw_step(1, 10.0, 1e10, 1e5, 2e6, 1, True, bound), steps[2]]
    kept2, _ = extract_steps(reference, leaking, 2)
    check("excluded reported detected", kept2[0]["excluded_reported_activity_max"] > 0.0)
    wrong = [steps[0], raw_step(1, 10.0, 1e10, 1e5, 2e6, 1, False, bound)]
    try:
        extract_steps(reference, wrong, 2)
        check("short result rejected", False)
    except ValueError:
        pass
    check("median", median([3, 1, 2]) == 2 and median([1, 2, 3, 4]) == 2.5)
    # the held-out definitions agree with their schedule arithmetic and with ENDF's 7-digit half-lives
    for name, spec in HELD_OUT.items():
        for nuclide in spec["nuclides"]:
            if nuclide[4] is not None:
                check(f"{name} half-life {nuclide[4]} has at most 7 digits",
                      len(Decimal(nuclide[4]).normalize().as_tuple().digits) <= 7)
        names = {n[0] for n in spec["nuclides"]}
        check(f"{name} decays name known nuclides", all(k in names and all(d[0] in names for d in v)
                                                         for k, v in spec["decays"].items()))
        check(f"{name} initial inventory names known nuclides", set(spec["n0"]) <= names)
        check(f"{name} has a schedule", len(spec["schedule"]) >= 3)
    check("pulsed schedule", len(HELD_OUT["h5_pulsed_1s_cool"]["schedule"]) == 41)
    check("three triples share one half-life",
          all(len({n[4] for n in HELD_OUT[k]["nuclides"] if n[4]}) == 1 for k in
              ("h1_triple_T10s", "h2_triple_T3600s", "h3_triple_T1e6s")))
    # the overrides document and the arm selector
    with tempfile.TemporaryDirectory() as temporary:
        doc = {"nuclides": [{"name": "A", "half_life_s": "5"}, {"name": "B", "half_life_s": None}]}
        text = json.loads(write_overrides(doc, Path(temporary) / "o.json").read_text())
        check("overrides cover radioactive nuclides only", [e["nuclide"] for e in text["overrides"]] == ["A"])
        check("overrides carry the energies", all(e["e_em_eV"] == ENERGIES_EV["e_em_eV"] for e in text["overrides"]))
    check("arms", ARMS == {"status_quo": None, "A": "bound", "B": "refine", "C": "equilibrium"})
    check("case list", len(case_list()) == 19 + 8 and len({c["name"] for c in case_list()}) == 27)
    check("actinide cases exist", all(n in HELD_OUT for n in ACTINIDE_CASES))
    check("branchings sum to one", all(
        sum(Decimal(m[3]) for m in modes) == 1 for chain in (H7_DECAYS, H8_DECAYS) for modes in chain.values()))
    check("chains are closed", all(d[0] in {n[0] for n in nuc} for nuc, dec in ((H7_NUCLIDES, H7_DECAYS),
                                                                               (H8_NUCLIDES, H8_DECAYS))
                                   for modes in dec.values() for d in modes))
    print("p174_floor self-test:", "PASS" if not failures else "FAIL")
    for failure in failures:
        print("  " + failure)
    return not failures


if __name__ == "__main__":
    main()
