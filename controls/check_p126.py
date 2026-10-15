#!/usr/bin/env python3
"""P126 gates G0, G2-G6 (protocols/ACTINV-P126_PROTOCOL.md): kerma heating and power-driven schedule steps.

  G0  The SHA-256 of the protocol file is a line of protocols/protocol_hash.txt.
  G1 (python part) `scripts/test_build_kerma_table.py` passes. The Rust half of G1 (fmt, clippy, workspace tests) and
      G7 (CI, committed baselines) are run by the coordinator and are not evaluated here.
  G2  Unchanged by default: `examples/fns_fe_5min.json` gives byte-identical results from the reference and
      candidate binaries once the top-level `ms` is dropped.
  G3  Kerma table from NJOY: `controls/p126_njoy_fe56.py` runs NJOY2016 (RECONR, BROADR 293.6 K, HEATR, GROUPR on the
      709-group structure, 1/E weighting) on the TENDL-2025 Fe-56 evaluation, converts the GENDF with
      `scripts/build_kerma_table.py` and compares the table's Fe56 MT=301 row with the GENDF values this repository's
      checker parses itself (1e-12 relative).
  G4  Kerma heating: a four-step FNS Fe schedule (irradiation, cooling, an irradiation with its own spectrum, a
      second irradiation) with a kerma table and `kerma.top` large enough to list every contributor. For every step
      the heating equals multiplier * sum_i N_i k_i, with N_i the contributor's atoms (checked against the run
      inventory for every non-bulk nuclide), k_i the table-and-spectrum fold recomputed here, within 1e-9. Two
      tables: a deterministic synthetic table (nuclide rows beating element rows, damage-energy rows) and, when G3
      produced it with the library's exact boundaries, the NJOY Fe-56 table.
  G5  Power normalization.
      * Fe: [1 s flux-driven, 300 s at `power_W_per_g`, 300 s at `power_W_cm3`, 60 s decay]. The start composition of each
        power step is the previous step's end state (the flux-driven first step lists the exact start composition), so
        multiplier * heating(start) equals the target within 1e-12; the reported drift equals the one recomputed from
        the end state (1e-12 absolute) and, when `converged`, is within `power_tolerance`.
      * B-10 (thermal group, kerma = loss cross section * 2.79 MeV): heating per atom is proportional to the loss rate,
        so N(t) is exact; this script replays the engine's refinement loop (equal sub-intervals, multiplier set from
        the start population) and compares the reported multipliers, drifts and refinement table (1e-9).
      * `max_substeps: 1` equals a flux-driven run at the start-of-step multiplier: every difference between the two
        results must lie under `ms`, `steps[*].power`, `ledger.kerma` or the certificate (a spec echo).
  G6  Report-only: the same strongly burning B-10 step with `max_substeps` 1, the default 64 and 4096, tabulating the
      end-of-step power error. No verdict.

Overall PASS iff G0, G1 (python), G2, G3, G4 and G5 pass. Runs are serial, without a shell, each under a timeout.

Usage:
  check_p126.py --reference REF_BIN --candidate CAND_BIN [--workdir target/p126-work]
      [--out results/p126_power.json] [--timeout 3600] [--reuse] [--data-dir actinv-data/v1.1.0]
      [--njoy ~/nuclear-data/njoy2016.79-build/njoy] [--njoy-timeout 3600] [--skip-njoy]

Run inside the required systemd cgroup (NJOY and the B-10 refinement runs). Exit 0 on PASS, 1 on FAIL.
"""
import argparse
import copy
import json
import math
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from check_p122 import SYMBOLS, absolutize_paths, canonical, sha256_file, strip_timing  # noqa: E402
from check_p125 import (BARN_CM2, DECAY_FALLBACK_NAME, DECAY_PRIMARY_NAME, EV_J, LIBRARY_NAME, Library,  # noqa: E402
                        ascending_flux, fold, record_file, rel_err, run_binary)

ROOT = HERE.parent
PROTOCOL = ROOT / "protocols" / "ACTINV-P126_PROTOCOL.md"
HASH_LOG = ROOT / "protocols" / "protocol_hash.txt"
FNS_SPEC = ROOT / "examples" / "fns_fe_5min.json"
NJOY_SCRIPT = HERE / "p126_njoy_fe56.py"
KERMA_TEST = ROOT / "scripts" / "test_build_kerma_table.py"
DENSITY_G_CM3 = 7.874

TOL_HEATING = 1e-9
TOL_POWER_START = 1e-12
TOL_DRIFT_ABS = 1e-12
TOL_B10 = 1e-9
Q_B10_EV = 2.79e6  # B-10(n,alpha) total Q value used to scale the synthetic B-10 kerma row
B10_N0 = 1.0e22  # atoms per gram
B10_FLUX = 1.0e14  # thermal group flux, particles cm^-2 s^-1
THERMAL_EV = 0.0253
TOP_ALL = 100000
MAX_LISTED = 20
# Natural iron atom fractions (IUPAC/NIST), used only to size the Fe power target near unit multiplier.
FE_ABUNDANCE = {"Fe54": 0.05845, "Fe56": 0.91754, "Fe57": 0.02119, "Fe58": 0.00282}
FE_ATOMS_PER_G = 6.02214076e23 / 55.845

NAME = re.compile(r"^([A-Z][a-z]?)(\d+)(?:m(\d+))?$")
SYMBOL_Z = {symbol: z for z, symbol in enumerate(SYMBOLS, start=1)}


def key_of(name):
    match = NAME.match(name)
    if not match:
        raise ValueError(f"unrecognised nuclide name {name!r}")
    return SYMBOL_Z[match.group(1)] * 1000 + int(match.group(2)), int(match.group(3) or 0)


# ------------------------------------------------------------------------------ kerma tables


class Table:
    """A kerma table as the engine reads it: a nuclide row beats its element's row; heating rows and damage rows."""

    def __init__(self, data):
        self.data = data
        self.bounds = data["boundaries_eV"]
        self.heating = self._rows(data["targets"])
        self.damage = self._rows(data.get("damage_energy_targets", {}))

    @staticmethod
    def _rows(raw):
        nuclides, elements = {}, {}
        for name, row in raw.items():
            if NAME.match(name):
                nuclides[key_of(name)] = row
            else:
                elements[SYMBOL_Z[name]] = row
        return nuclides, elements

    @staticmethod
    def _lookup(rows, key):
        nuclides, elements = rows
        return nuclides.get(key) or elements.get(key[0] // 1000)

    def heating_row(self, key):
        return self._lookup(self.heating, key)

    def damage_row(self, key):
        return self._lookup(self.damage, key)


def eV_per_s(row, flux):
    """KermaTable fold: sum_g K_g * (flux_g * 1e-24)."""
    acc = 0.0
    for k, f in zip(row, flux):
        acc += k * (f * BARN_CM2)
    return acc


def synthetic_fe_table(bounds):
    """Deterministic positive rows: nuclide rows for the four iron isotopes, element rows for Fe, Mn, Cr and Co (so
    most activation products are covered through the element fallback), and damage-energy rows for Fe56 and Fe."""
    def row(base, phase):
        return [base * (1.0 + 0.5 * math.sin(0.11 * g + phase)) for g in range(len(bounds) - 1)]
    return {
        "format": "actinv-kerma-table-1",
        "source": {"citation": "synthetic deterministic table for the P126 checker (not evaluated data)",
                   "edition": "controls/check_p126.py synthetic_fe_table", "url": "local"},
        "projectile": "neutron", "group_structure": "fispact-709", "boundaries_eV": bounds,
        "units": "kerma_barn_eV_per_group",
        "targets": {"Fe54": row(2.0e6, 0.1), "Fe56": row(3.0e6, 0.7), "Fe57": row(2.5e6, 1.3), "Fe58": row(2.2e6, 1.9),
                    "Fe": row(2.7e6, 2.5), "Mn": row(2.9e6, 3.1), "Cr": row(2.4e6, 3.7), "Co": row(2.6e6, 4.3)},
        "damage_energy_targets": {"Fe56": row(4.0e5, 0.4), "Fe": row(3.5e5, 0.9)},
        "temperature_K": 293.6,
    }


# ------------------------------------------------------------------------------ specs


def fe_spec(base, table_ref, schedule, *, title, kerma_extra=None, density=DENSITY_G_CM3):
    spec = copy.deepcopy(base)
    spec["title"] = title
    spec["material"]["density_g_cm3"] = density
    spec["kerma"] = {"table": {"path": table_ref["path"], "sha256": table_ref["sha256"]}, "top": TOP_ALL}
    spec["kerma"].update(kerma_extra or {})
    spec["schedule"] = schedule
    return spec


def rolled_spectrum(base):
    flux = list(base["spectrum"]["flux_per_group"])
    shifted = flux[7:] + flux[:7]
    return {"structure": base["spectrum"]["structure"], "flux_per_group": shifted, "total": 5.0e9,
            "descending": base["spectrum"].get("descending", False)}


def g4_schedule(base):
    return [{"dt": "300 s", "flux": 1.0}, {"dt": "60 s", "flux": 0.0},
            {"dt": "1 h", "flux": 0.5, "spectrum": rolled_spectrum(base)}, {"dt": "30 s", "flux": 2.0}]


# ------------------------------------------------------------------------------ G4


def check_heating(result, base, schedule, table, bulk_names=("Fe54", "Fe56", "Fe57", "Fe58")):
    """Recompute every step's kerma block. Returns the gate record."""
    steps = result["steps"]
    out = {"tolerance": TOL_HEATING, "steps": []}
    worst, problems = 0.0, []
    if len(steps) != len(schedule) or any("kerma" not in s for s in steps):
        out["pass"] = False
        out["error"] = "the candidate result lacks a kerma block on every step"
        return out
    base_flux = ascending_flux(base["spectrum"])
    for si, (step, scheduled) in enumerate(zip(steps, schedule)):
        block = step["kerma"]
        flux = ascending_flux(scheduled["spectrum"]) if "spectrum" in scheduled else base_flux
        mult = scheduled["flux"]
        inventory = {row["nuclide"]: row["atoms_per_g"] for row in step["inventory"]}
        contributors = {c["nuclide"]: c for c in block["top_contributors"]}
        entry = {"step": si + 1, "multiplier": mult, "n_contributors": len(contributors)}
        errs = {"multiplier_echo": rel_err(block["flux_multiplier"], mult)}
        total, total_damage = 0.0, 0.0
        # every inventory nuclide with a row must be listed with the inventory's atoms (the non-bulk ones exactly)
        listed_missing = []
        for name, atoms in inventory.items():
            key = key_of(name)
            if table.heating_row(key) is not None and mult > 0.0 and atoms > 0.0 and name not in contributors:
                k = eV_per_s(table.heating_row(key), flux)
                if k != 0.0:
                    listed_missing.append(name)
        entry["inventory_nuclides_with_rows_missing_from_list"] = listed_missing[:MAX_LISTED]
        worst_atoms = 0.0
        for name, c in contributors.items():
            key = key_of(name)
            row = table.heating_row(key)
            if row is None:
                problems.append(f"step {si + 1}: {name} listed without a table row")
                continue
            k_w = eV_per_s(row, flux) * EV_J
            expected = mult * k_w * c["atoms_per_g"]
            errs[f"heating:{name}"] = rel_err(c["heating_W_per_g"], expected)
            total += expected
            drow = table.damage_row(key)
            if drow is not None:
                total_damage += eV_per_s(drow, flux) * c["atoms_per_g"]
            if name in inventory and name not in bulk_names:
                worst_atoms = max(worst_atoms, rel_err(c["atoms_per_g"], inventory[name]))
        entry["worst_contributor_atoms_vs_inventory"] = worst_atoms
        errs["total_heating"] = rel_err(block["heating_W_per_g"], total)
        errs["other_heating_zero"] = 0.0 if abs(block["other_heating_W_per_g"]) <= 1e-12 * abs(total) + 1e-300 else math.inf
        errs["n_contributing"] = 0.0 if block["n_contributing_nuclides"] == len(contributors) else math.inf
        density = base["material"].get("density_g_cm3") if "density_g_cm3" in base["material"] else DENSITY_G_CM3
        errs["heating_W_cm3"] = rel_err(block.get("heating_W_cm3"), total * density) if density else 0.0
        if table.damage[0] or table.damage[1]:
            errs["damage_energy"] = rel_err(block.get("damage_energy_eV_per_g_s"), mult * total_damage)
        covered = block["covered_atom_fraction"]
        errs["covered_fraction_in_unit_interval"] = 0.0 if 0.0 < covered <= 1.0 else math.inf
        entry["heating_W_per_g_run"] = block["heating_W_per_g"]
        entry["heating_W_per_g_expected"] = total
        entry["covered_atom_fraction"] = covered
        entry["worst_relative_error"] = max(errs.values())
        entry["error_by_quantity"] = {k: v for k, v in sorted(errs.items(), key=lambda kv: -kv[1])[:5]}
        worst = max(worst, entry["worst_relative_error"], worst_atoms)
        if listed_missing:
            problems.append(f"step {si + 1}: {len(listed_missing)} inventory nuclides with rows are not listed")
        out["steps"].append(entry)
    out["worst_relative_error"] = worst
    out["problems"] = problems
    out["pass"] = bool(worst <= TOL_HEATING and not problems)
    return out


# ------------------------------------------------------------------------------ G5 and G6


def all_differences(a, b, path="", out=None, limit=400):
    out = [] if out is None else out
    if len(out) >= limit:
        return out
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                out.append(f"{path}/{k}")
            else:
                all_differences(a[k], b[k], f"{path}/{k}", out, limit)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"{path}[]")
        for i, (x, y) in enumerate(zip(a, b)):
            all_differences(x, y, f"{path}[{i}]", out, limit)
    elif a != b:
        out.append(path)
    return out


ALLOWED_DIFFERENCE = re.compile(
    r"^(/ms|/steps\[\d+\]/power(/.*)?|/ledger/kerma(/.*)?|/certificate(/.*)?)$")  # the certificate echoes the spec


def fe_power_spec(base, table_ref, bounds_table, power):
    """Power-driven Fe schedule. `power` is the W/g target chosen so that the multiplier is near one."""
    schedule = [{"dt": "1 s", "flux": 1.0}, {"dt": "300 s", "power_W_per_g": power},
                {"dt": "300 s", "power_W_cm3": power * DENSITY_G_CM3}, {"dt": "60 s", "flux": 0.0}]
    return fe_spec(base, table_ref, schedule, title="P126 G5: power-driven Fe steps"), schedule


def check_fe_power(result, base, table, power):
    """G5 for the Fe case."""
    steps = result["steps"]
    flux = ascending_flux(base["spectrum"])
    total_flux = sum(flux)
    out = {"requested_W_per_g": power, "tolerance_start_power": TOL_POWER_START, "tolerance_drift_abs": TOL_DRIFT_ABS,
           "steps": []}
    problems, worst_start, worst_drift = [], 0.0, 0.0

    def heating_unit(step):
        """Heating at unit multiplier (W/g) of the end state of a step, from its contributors."""
        return sum(eV_per_s(table.heating_row(key_of(c["nuclide"])), flux) * EV_J * c["atoms_per_g"]
                   for c in step["kerma"]["top_contributors"])

    for si in (1, 2):
        step, previous = steps[si], steps[si - 1]
        block = step.get("power")
        if block is None:
            problems.append(f"step {si + 1} carries no power block")
            continue
        requested = block["requested_W_per_g"]
        mult0 = block["flux_multiplier_per_substep"][0]
        start = mult0 * heating_unit(previous)
        start_err = rel_err(start, requested)
        end_unit = heating_unit(step)
        last = block["flux_multiplier_per_substep"][-1]
        drift = abs(last * end_unit - requested) / requested
        entry = {"step": si + 1, "requested_W_per_g": requested, "requested_W_cm3": block.get("requested_W_cm3"),
                 "multiplier_first_substep": mult0, "substeps": block["substeps"], "converged": block["converged"],
                 "tolerance": block["tolerance"], "max_power_drift_run": block["max_power_drift"],
                 "last_interval_end_drift_recomputed": drift, "start_power_relative_error": start_err,
                 "estimated_flux_multiplier": block["estimated_flux_multiplier"],
                 "refinement": block["refinement"]}
        worst_start = max(worst_start, start_err)
        if block["substeps"] == 1:
            diff = abs(block["max_power_drift"] - drift)
            entry["drift_absolute_difference"] = diff
            worst_drift = max(worst_drift, diff)
        else:
            entry["drift_absolute_difference"] = None
            if drift > block["max_power_drift"] + TOL_DRIFT_ABS:
                problems.append(f"step {si + 1}: the recomputed last-interval drift exceeds the reported maximum")
        if block["converged"] != (block["max_power_drift"] <= block["tolerance"]):
            problems.append(f"step {si + 1}: converged flag disagrees with drift and tolerance")
        if block["converged"] and block["max_power_drift"] > block["tolerance"]:
            problems.append(f"step {si + 1}: converged but the drift exceeds the tolerance")
        mean = sum(block["flux_multiplier_per_substep"]) / len(block["flux_multiplier_per_substep"])
        entry["mean_multiplier_error"] = rel_err(block["mean_flux_multiplier"], mean)
        entry["step_flux_equals_mean"] = rel_err(step["flux"], block["mean_flux_multiplier"])
        entry["flux_per_substep_error"] = max(rel_err(f, m * total_flux) for f, m in zip(
            block["flux_per_substep_cm2_s"], block["flux_multiplier_per_substep"]))
        entry["kerma_flux_multiplier_is_last"] = rel_err(step["kerma"]["flux_multiplier"], last)
        if block.get("requested_W_cm3") is not None:
            entry["w_cm3_to_w_g"] = rel_err(requested, block["requested_W_cm3"] / DENSITY_G_CM3)
        out["steps"].append(entry)
        for key in ("mean_multiplier_error", "step_flux_equals_mean", "flux_per_substep_error",
                    "kerma_flux_multiplier_is_last"):
            if entry[key] > 1e-12:
                problems.append(f"step {si + 1}: {key} = {entry[key]:.3e}")
        if entry.get("w_cm3_to_w_g", 0.0) > 1e-15:
            problems.append(f"step {si + 1}: power_W_cm3 / density differs from the reported W/g")
    for si in (0, 3):
        if steps[si].get("power") is not None:
            problems.append(f"step {si + 1} is flux-driven but carries a power block")
    if steps[3]["kerma"]["heating_W_per_g"] != 0.0:
        problems.append("step 4 is a zero-flux step with nonzero kerma heating")
    out["worst_start_power_error"] = worst_start
    out["worst_drift_difference"] = worst_drift
    out["problems"] = problems
    out["pass"] = bool(worst_start <= TOL_POWER_START and worst_drift <= TOL_DRIFT_ABS and not problems)
    return out


def b10_setup(lib, flux_unit):
    """Loss cross section of B-10 (every ZAP = -1 row), its one-group removal rate at unit multiplier, and the
    heating per atom at unit multiplier for the synthetic row K_g = loss_g * Q."""
    rows = [i for i in lib.rows_of((5010, 0)) if lib.rows[i][2] == -1]
    sig = lib.stream(rows)
    loss = [0.0] * lib.n_groups
    for i in rows:
        for g, value in enumerate(sig[i]):
            loss[g] += value
    return loss, len(rows)


def simulate_b10(rho, k_w, n0, power, duration, tolerance, max_substeps):
    """The engine's refinement loop for a single-nuclide material whose heating is proportional to its population:
    equal sub-intervals, multiplier set from the start population, exact exponential decay inside each."""
    n, attempts = 1, []
    while True:
        h = duration / n
        population = n0
        multipliers, drifts = [], []
        for _ in range(n):
            multiplier = power / (k_w * population)
            nxt = population * math.exp(-rho * multiplier * h)
            drifts.append(abs(multiplier * k_w * nxt - power) / power)
            multipliers.append(multiplier)
            population = nxt
        attempts.append({"substeps": n, "max_power_drift": max(drifts), "multipliers": multipliers, "end": population})
        if max(drifts) <= tolerance or n >= max_substeps:
            return attempts
        n = min(2 * n, max_substeps)


def check_b10_case(result, label, rho, k_w, n0, power, duration, tolerance, max_substeps):
    """Compare one B-10 run with the replayed refinement loop. Returns the record (the G6 table row included)."""
    step = result["steps"][0]
    block = step["power"]
    attempts = simulate_b10(rho, k_w, n0, power, duration, tolerance, max_substeps)
    final = attempts[-1]
    out = {"label": label, "max_substeps": max_substeps, "substeps_run": block["substeps"],
           "substeps_expected": final["substeps"], "converged_run": block["converged"],
           "converged_expected": final["max_power_drift"] <= tolerance,
           "max_power_drift_run": block["max_power_drift"], "max_power_drift_expected": final["max_power_drift"]}
    out["refinement_run"] = block["refinement"]
    out["refinement_expected"] = [{"substeps": a["substeps"], "max_power_drift": a["max_power_drift"]} for a in attempts]
    problems = []
    if len(block["refinement"]) != len(attempts):
        problems.append("refinement length differs")
    drift_err = 0.0
    for got, exp in zip(block["refinement"], attempts):
        if got["substeps"] != exp["substeps"]:
            problems.append(f"refinement substeps {got['substeps']} vs {exp['substeps']}")
        drift_err = max(drift_err, abs(got["max_power_drift"] - exp["max_power_drift"]) /
                        max(exp["max_power_drift"], 1e-6))
    mult_err = 0.0
    if block["substeps"] == final["substeps"]:
        mult_err = max(rel_err(a, b) for a, b in zip(block["flux_multiplier_per_substep"], final["multipliers"]))
    else:
        mult_err = math.inf
    # power at every sub-interval start, with this script's population and the run's multiplier
    population, start_err = n0, 0.0
    h = duration / block["substeps"]
    for m in block["flux_multiplier_per_substep"]:
        start_err = max(start_err, abs(m * k_w * population - power) / power)
        population *= math.exp(-rho * m * h)
    end_power = step["kerma"]["heating_W_per_g"]
    out.update({
        "multiplier_relative_error_worst": mult_err, "refinement_drift_error_worst": drift_err,
        "start_power_relative_error_worst": start_err, "end_power_W_per_g_run": end_power,
        "end_power_relative_error_run": (end_power - power) / power,
        "end_power_relative_error_expected": (final["multipliers"][-1] * k_w * final["end"] - power) / power,
        "population_end_expected": final["end"], "population_end_over_start": final["end"] / n0,
        "mean_flux_multiplier": block["mean_flux_multiplier"], "first_multiplier": block["flux_multiplier_per_substep"][0],
        "last_multiplier": block["flux_multiplier_per_substep"][-1]})
    out["problems"] = problems
    out["pass"] = bool(not problems and mult_err <= TOL_B10 and drift_err <= 1e-6 and start_err <= TOL_B10
                       and out["converged_run"] == out["converged_expected"])
    return out


# ------------------------------------------------------------------------------ gates


def gate_g0():
    digest = sha256_file(PROTOCOL)
    name = f"protocols/{PROTOCOL.name}"
    hit = [line for line in HASH_LOG.read_text(encoding="utf-8").splitlines()
           if line.startswith(digest) and line.split()[-1] == name]
    out = {"protocol": name, "sha256": digest, "in_protocol_hash_txt": bool(hit)}
    try:
        log = subprocess.run(["git", "log", "--format=%H", f"-S{digest}", "--", "protocols/protocol_hash.txt"],
                             cwd=str(ROOT), capture_output=True, text=True, timeout=120)
        out["first_commit_listing_the_hash"] = log.stdout.split()[-1] if log.stdout.split() else None
    except (OSError, subprocess.TimeoutExpired) as error:
        out["git_error"] = f"{type(error).__name__}: {error}"
    out["pass"] = bool(hit)
    return out


def gate_g1_python(timeout):
    try:
        proc = subprocess.run([sys.executable, str(KERMA_TEST)], cwd=str(ROOT), capture_output=True, text=True,
                              timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"pass": False, "error": f"{type(error).__name__}: {error}"}
    return {"script": record_file(KERMA_TEST), "exit_code": proc.returncode, "output_tail": proc.stderr.splitlines()[-6:],
            "pass": proc.returncode == 0}


def gate_g2(ref, cand):
    if ref is None or cand is None:
        return {"pass": False, "error": "a gate run failed; see runs"}
    ref_s, ref_dropped = strip_timing(ref)
    cand_s, cand_dropped = strip_timing(cand)
    equal = canonical(ref_s) == canonical(cand_s)
    out = {"canonical_equal_without_timing": equal, "timing_keys_dropped": sorted(set(ref_dropped + cand_dropped)),
           "steps": len(ref.get("steps", [])), "pass": equal}
    if not equal:
        out["differences"] = all_differences(ref_s, cand_s)[:MAX_LISTED]
    return out


def gate_g3(args, workdir, record):
    if args.skip_njoy:
        return {"pass": False, "skipped": "--skip-njoy", "summary": None}
    summary_path = workdir / "njoy" / "njoy_fe56.json"
    command = [sys.executable, str(NJOY_SCRIPT), "--workdir", str(workdir / "njoy"), "--out", str(summary_path),
               "--timeout", str(args.njoy_timeout), "--njoy", str(Path(args.njoy).expanduser()),
               "--library", str(args.library_path)]
    if args.reuse:
        command.append("--reuse")
    log = workdir / "njoy.log"
    info = {"command": [str(c) for c in command]}
    try:
        with open(log, "wb") as handle:
            proc = subprocess.run(command, cwd=str(ROOT), stdout=handle, stderr=subprocess.STDOUT,
                                  timeout=args.njoy_timeout * 4 + 600)
        info["exit_code"] = proc.returncode
    except subprocess.TimeoutExpired:
        info["error"] = "timed out"
        return {"pass": False, **info}
    except OSError as error:
        info["error"] = f"{type(error).__name__}: {error}"
        return {"pass": False, **info}
    if not summary_path.exists():
        return {"pass": False, "error": f"no summary written; see {log}", **info}
    summary = json.loads(summary_path.read_text())
    record["runs"]["g3_njoy_summary"] = record_file(summary_path)
    keep = {k: summary.get(k) for k in ("weighting", "temperature_K", "heatr_local", "reconr_tolerance", "njoy", "endf",
                                        "evaluation", "deck", "tapes", "table", "criteria", "error", "groups")}
    keep["mt301"] = (summary.get("checks") or {}).get("mt301")
    keep["mt444_report_only"] = (summary.get("checks") or {}).get("mt444_report_only")
    keep["pendf_collapse_report_only"] = summary.get("pendf_collapse_report_only")
    return {"pass": bool(summary.get("pass")), "summary_file": str(summary_path), **info, "summary": keep,
            "table_for_g4": summary.get("table") if summary.get("pass") else None}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reference", required=True, help="binary built from the P126 base commit")
    ap.add_argument("--candidate", required=True, help="binary built from the P126 implementation commit")
    ap.add_argument("--workdir", default="target/p126-work")
    ap.add_argument("--out", default="results/p126_power.json")
    ap.add_argument("--timeout", type=int, default=3600, help="seconds allowed per run")
    ap.add_argument("--reuse", action="store_true", help="reuse existing outputs in the workdir")
    ap.add_argument("--data-dir", default="actinv-data/v1.1.0", help="installed actinv-data directory")
    ap.add_argument("--njoy", default="~/nuclear-data/njoy2016.79-build/njoy")
    ap.add_argument("--njoy-timeout", type=int, default=3600)
    ap.add_argument("--skip-njoy", action="store_true", help="do not run NJOY (G3 then fails)")
    args = ap.parse_args(argv)
    args.reference = str(Path(args.reference).resolve())
    args.candidate = str(Path(args.candidate).resolve())

    def resolve(path):
        path = Path(path).expanduser()
        return path if path.is_absolute() else ROOT / path

    data_dir = resolve(args.data_dir)
    workdir = resolve(args.workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    out_path = resolve(args.out)
    args.library_path = data_dir / "activation" / LIBRARY_NAME

    record = {"protocol": "ACTINV-P126", "checker": record_file(Path(__file__).resolve()),
              "njoy_script": record_file(NJOY_SCRIPT), "protocol_file": record_file(PROTOCOL),
              "reference_binary": record_file(args.reference), "candidate_binary": record_file(args.candidate),
              "fns_spec": record_file(FNS_SPEC),
              "inputs": {"library": record_file(args.library_path),
                         "library_index": record_file(data_dir / "activation" / LIBRARY_NAME.replace(".npz", "_index.json")),
                         "decay_primary": record_file(data_dir / "decay" / DECAY_PRIMARY_NAME),
                         "decay_fallback": record_file(data_dir / "decay" / DECAY_FALLBACK_NAME)},
              "runs": {}, "gates": {},
              "external_gates": {"G1_rust": "fmt, clippy -D warnings, workspace tests: run by the coordinator",
                                 "G7": "CI on the pushed commit and the committed baselines: run by the coordinator"}}

    def execute(label, binary, spec_path, cwd):
        out_file = workdir / f"{label}.json"
        log_file = workdir / f"{label}.log"
        info = {"binary": str(binary), "spec": record_file(spec_path), "cwd": str(cwd)}
        record["runs"][label] = info
        if not (args.reuse and out_file.exists()):
            try:
                code = run_binary(binary, spec_path, out_file, log_file, cwd, args.timeout)
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
        info["output"] = record_file(out_file)
        return json.loads(out_file.read_text())

    def write_spec(label, spec):
        path = workdir / f"{label}.spec.json"
        path.write_text(json.dumps(spec, indent=1) + "\n")
        return path

    def write_table(label, data):
        path = workdir / f"{label}.json"
        path.write_text(json.dumps(data, indent=1) + "\n")
        return record_file(path)

    record["gates"]["G0"] = gate_g0()
    record["gates"]["G1_python"] = gate_g1_python(600)

    # ---- G2
    ref = execute("g2_reference", args.reference, FNS_SPEC, ROOT)
    cand = execute("g2_candidate", args.candidate, FNS_SPEC, ROOT)
    record["gates"]["G2"] = gate_g2(ref, cand)

    # ---- G3 (NJOY)
    record["gates"]["G3"] = gate_g3(args, workdir, record)

    # ---- shared inputs
    base = json.loads(FNS_SPEC.read_text())
    absolutize_paths(base)
    lib = Library(str(args.library_path), record["inputs"]["library_index"]["path"])
    flux = ascending_flux(base["spectrum"])
    table_ref = write_table("synthetic_fe_kerma", synthetic_fe_table(lib.bounds))
    synthetic = Table(json.loads(Path(table_ref["path"]).read_text()))
    record["synthetic_table"] = table_ref

    # ---- G4
    schedule = g4_schedule(base)
    g4 = {"cases": {}}
    spec = fe_spec(base, table_ref, schedule, title="P126 G4: kerma heating, synthetic table")
    result = execute("g4_synthetic_candidate", args.candidate, write_spec("g4_synthetic", spec), workdir)
    if result is not None:
        try:
            g4["cases"]["synthetic"] = check_heating(result, spec, schedule, synthetic)
        except (KeyError, ValueError, IndexError, ZeroDivisionError) as error:
            g4["cases"]["synthetic"] = {"pass": False, "error": f"{type(error).__name__}: {error}"}
    else:
        g4["cases"]["synthetic"] = {"pass": False, "error": "the candidate run failed; see runs"}
    njoy_table = record["gates"]["G3"].get("table_for_g4")
    boundaries_exact = bool(((record["gates"]["G3"].get("summary") or {}).get("criteria") or {}).get(
        "tape_boundaries_equal_library"))
    if njoy_table and boundaries_exact:
        spec = fe_spec(base, njoy_table, schedule, title="P126 G4: kerma heating, NJOY Fe-56 table")
        result = execute("g4_njoy_candidate", args.candidate, write_spec("g4_njoy", spec), workdir)
        if result is not None:
            try:
                g4["cases"]["njoy_fe56"] = check_heating(result, spec, schedule,
                                                         Table(json.loads(Path(njoy_table["path"]).read_text())))
            except (KeyError, ValueError, IndexError, ZeroDivisionError) as error:
                g4["cases"]["njoy_fe56"] = {"pass": False, "error": f"{type(error).__name__}: {error}"}
        else:
            g4["cases"]["njoy_fe56"] = {"pass": False, "error": "the candidate run failed; see runs"}
    else:
        g4["cases"]["njoy_fe56"] = {"pass": None, "skipped": "G3 produced no table with the library's exact boundaries",
                                    "blocking": False}
    g4["pass"] = all(case["pass"] for case in g4["cases"].values() if case.get("pass") is not None)
    record["gates"]["G4"] = g4

    # ---- G5: Fe
    h_unit = sum(FE_ATOMS_PER_G * FE_ABUNDANCE[name] * eV_per_s(synthetic.heating_row(key_of(name)), flux) * EV_J
                 for name in FE_ABUNDANCE)
    power = float(f"{h_unit:.4g}")  # W per gram at the FNS flux (multiplier one), rounded to four figures
    fe_spec_main, _fe_schedule = fe_power_spec(base, table_ref, synthetic, power)
    g5 = {"fe_power_target_W_per_g": power, "fe_unit_multiplier_heating_W_per_g": h_unit}
    result = execute("g5_fe_candidate", args.candidate, write_spec("g5_fe", fe_spec_main), workdir)
    if result is not None:
        try:
            g5["fe"] = check_fe_power(result, fe_spec_main, synthetic, power)
        except (KeyError, ValueError, IndexError, ZeroDivisionError, TypeError) as error:
            g5["fe"] = {"pass": False, "error": f"{type(error).__name__}: {error}"}
    else:
        g5["fe"] = {"pass": False, "error": "the candidate run failed; see runs"}

    # ---- G5: max_substeps 1 equals the flux-driven run at the start-of-step multiplier
    cap_schedule = [{"dt": "300 s", "power_W_per_g": power}]
    cap_spec = fe_spec(base, table_ref, cap_schedule, title="P126 G5: one power step, max_substeps 1",
                       kerma_extra={"max_substeps": 1})
    cap = execute("g5_cap1_candidate", args.candidate, write_spec("g5_cap1", cap_spec), workdir)
    equality = {"pass": False, "error": "the capped power run failed; see runs"}
    if cap is not None:
        try:
            multiplier = cap["steps"][0]["power"]["flux_multiplier_per_substep"][0]
            flux_spec = fe_spec(base, table_ref, [{"dt": "300 s", "flux": multiplier}],
                                title="P126 G5: one power step, max_substeps 1", kerma_extra={"max_substeps": 1})
            flux_run = execute("g5_flux_candidate", args.candidate, write_spec("g5_flux", flux_spec), workdir)
            if flux_run is not None:
                differences = all_differences(cap, flux_run)
                outside = [d for d in differences if not ALLOWED_DIFFERENCE.match(d)]
                equality = {"start_of_step_multiplier": multiplier,
                            "estimated_multiplier_equals_start_multiplier":
                                cap["steps"][0]["power"]["estimated_flux_multiplier"] == multiplier,
                            "n_differences": len(differences), "differences_outside_allowed": outside[:MAX_LISTED],
                            "allowed": "/ms, /steps[*]/power, /ledger/kerma, /certificate (spec echo)", "pass": not outside}
            else:
                equality = {"pass": False, "error": "the flux-driven run failed; see runs"}
        except (KeyError, IndexError, TypeError) as error:
            equality = {"pass": False, "error": f"{type(error).__name__}: {error}"}
    g5["cap1_equals_flux_driven"] = equality

    # ---- G5 B-10 and G6
    b10 = {}
    try:
        loss, n_loss_rows = b10_setup(lib, None)
        thermal = next(g for g in range(lib.n_groups) if lib.bounds[g] <= THERMAL_EV < lib.bounds[g + 1])
        spectrum = [0.0] * lib.n_groups
        spectrum[thermal] = B10_FLUX
        rho = fold(loss, spectrum)  # removal rate per atom per second at unit multiplier
        row = [value * Q_B10_EV for value in loss]
        k_w = eV_per_s(row, spectrum) * EV_J  # heating per atom (W) at unit multiplier
        power_b10 = B10_N0 * k_w  # initial multiplier one
        duration = round(0.5 / rho / 1000.0) * 1000.0  # half the exhaustion time at constant power
        b10_table = {
            "format": "actinv-kerma-table-1",
            "source": {"citation": "synthetic: B-10 loss cross section times 2.79 MeV (P126 checker)",
                       "edition": "controls/check_p126.py", "url": "local"},
            "projectile": "neutron", "group_structure": "fispact-709", "boundaries_eV": lib.bounds,
            "units": "kerma_barn_eV_per_group", "targets": {"B10": row}, "temperature_K": 293.6}
        b10_ref = write_table("synthetic_b10_kerma", b10_table)
        b10_spec = copy.deepcopy(base)
        b10_spec["title"] = "P126 G5/G6: B-10 at high thermal flux, power-driven"
        b10_spec["material"] = {"mass_g": 1.0, "basis": "atoms_per_g", "composition": {"B10": B10_N0}}
        b10_spec["spectrum"] = {"structure": "fispact-709", "flux_per_group": spectrum, "descending": False}
        b10_spec["options"] = {"mode": "coupled", "prune": "rate", "bmin_atoms_per_g": 1e-8, "temperature_K": 293.6}
        b10_spec["schedule"] = [{"dt": f"{duration:.1f} s", "power_W_per_g": power_b10}]
        b10["setup"] = {"loss_rows": n_loss_rows, "thermal_group": thermal, "thermal_group_bounds_eV": [
            lib.bounds[thermal], lib.bounds[thermal + 1]], "flux_cm2_s": B10_FLUX, "removal_rate_per_atom_s": rho,
            "heating_per_atom_W": k_w, "power_W_per_g": power_b10, "duration_s": duration, "n0_atoms_per_g": B10_N0,
            "exhaustion_time_s_at_constant_power": 1.0 / rho, "q_eV": Q_B10_EV, "table": b10_ref}
        b10["cases"] = {}
        for label, cap_value in (("max_substeps_1", 1), ("default_64", None), ("max_substeps_4096", 4096)):
            spec = copy.deepcopy(b10_spec)
            spec["kerma"] = {"table": {"path": b10_ref["path"], "sha256": b10_ref["sha256"]}}
            if cap_value is not None:
                spec["kerma"]["max_substeps"] = cap_value
            run = execute(f"g5_b10_{label}_candidate", args.candidate, write_spec(f"g5_b10_{label}", spec), workdir)
            if run is None:
                b10["cases"][label] = {"pass": False, "error": "the candidate run failed; see runs"}
                continue
            try:
                b10["cases"][label] = check_b10_case(run, label, rho, k_w, B10_N0, power_b10, duration, 1e-3,
                                                     cap_value or 64)
            except (KeyError, IndexError, TypeError, ValueError) as error:
                b10["cases"][label] = {"pass": False, "error": f"{type(error).__name__}: {error}"}
        b10["pass"] = all(case.get("pass") for case in b10["cases"].values())
    except (KeyError, ValueError, StopIteration, OSError, ZeroDivisionError) as error:
        b10["pass"] = False
        b10["error"] = f"{type(error).__name__}: {error}"
    g5["b10"] = b10
    g5["pass"] = bool(g5["fe"].get("pass") and equality.get("pass") and b10.get("pass"))
    record["gates"]["G5"] = g5

    # ---- G6 (report only)
    table = []
    for label, case in (b10.get("cases") or {}).items():
        if "end_power_relative_error_run" in case:
            table.append({"max_substeps": case["max_substeps"], "substeps_used": case["substeps_run"],
                          "converged": case["converged_run"], "reported_max_power_drift": case["max_power_drift_run"],
                          "end_of_step_power_error_run": case["end_power_relative_error_run"],
                          "end_of_step_power_error_replay": case["end_power_relative_error_expected"],
                          "population_end_over_start": case["population_end_over_start"],
                          "first_multiplier": case["first_multiplier"], "last_multiplier": case["last_multiplier"]})
    record["gates"]["G6"] = {"report_only": True, "case": "B-10, thermal flux, power held constant for half the "
                             "exhaustion time", "table": table}

    blocking = ("G0", "G1_python", "G2", "G3", "G4", "G5")
    overall = all(bool(record["gates"][g].get("pass")) for g in blocking)
    record["verdict"] = "PASS" if overall else "FAIL"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    for gate in blocking:
        print(f"P126 {gate}: {'PASS' if record['gates'][gate].get('pass') else 'FAIL'}")
    for row in table:
        print(f"  G6 max_substeps {row['max_substeps']:>5}: substeps {row['substeps_used']:>5} converged "
              f"{row['converged']}, end-of-step power error {row['end_of_step_power_error_run']:+.3e}")
    print(f"P126 verdict: {record['verdict']} ({out_path})")
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
