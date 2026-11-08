#!/usr/bin/env python3
"""P195 harness: run `actinv unfold` on the protocol's cases and record what the checker needs.

`rehearse --actinv-bin BIN` runs every path below at a small size with another seed, into target/p195-rehearsal.
`g1 --actinv-bin BIN` runs the cargo gates (fmt, check, clippy, workspace tests) on the current commit and writes
results/p195_g1.json. `run --actinv-bin BIN` runs, in this order:
  G3  40 synthetic truths drawn from the FNS iron prior (seed 195), each with foil activities from a plain ACTINV
      run at the truth plus 3% log-normal noise, unfolded from the prior;
  G2  for every foil of the first G3 case, plain runs at its accepted spectrum with ln phi_g moved by +-1e-3 in the
      groups the checker's selection rule names;
  G4  the IRDFF-II Cf-252 and U-235 fields, two priors each, one full-data unfold and one leave-one-out unfold per
      row, every row a reaction rate on its P17 production mapping;
  G5  the first G3 case and the Cf-252 (a) full-data arm unfolded again;
  G6  every refusal case, for its exit status and message.
Every unfold output is kept under target/p195-work (untracked) and named in results/p195_record.json with its
SHA-256. Prints only counts. Child processes run with a bounded wait and are killed on timeout.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import copy
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "controls"))
import check_p195 as c  # noqa: E402
import p17_irdff  # noqa: E402

WORK = ROOT / "target" / "p195-work"
# Every spectrum a run sees writes a collapsed artifact (about 7 MB) to the prepared-data cache; the protocol's
# runs use their own cache, removed at the end, so the user's cache does not grow by thousands of entries.
CACHE = WORK / "cache"
RECORD = c.RECORD
# The registered run uses the protocol's seed and sizes. `rehearse` changes these (another seed, two truths, two
# leave-one-out rows per arm, its own work directory and record) to exercise every path before registration.
SEED = c.SEED
TRUTHS = c.G3_TRUTHS
LOO_LIMIT = None
UNFOLD_TIMEOUT_S = 3600
RUN_TIMEOUT_S = 900
WORKERS = 3
# A committed self-shielding table (P19), so the self-shielding refusal case is otherwise a valid problem.
SHIELD_TABLE = {"path": "results/g1_p19_shield_artifact.json",
                "sha256": "8f46bde47a9ac390057aa4a9eb1af36ec5d043536693f8a6c96efb212e1c9fe1"}


# ------------------------------------------------------------------------------------------------- processes

def call(args: list[str], timeout: float, cwd: Path = ROOT) -> dict:
    """Run a child with a bounded wait; on timeout subprocess.run kills and reaps it."""
    started = time.perf_counter()
    try:
        env = {**os.environ, "ACTINV_CACHE_DIR": str(CACHE)}
        done = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False, env=env)
        return {"exit": done.returncode, "stderr": done.stderr[-4000:], "stdout": done.stdout[-4000:],
                "wall_s": time.perf_counter() - started}
    except subprocess.TimeoutExpired:
        return {"exit": None, "stderr": f"timed out after {timeout} s", "stdout": "",
                "wall_s": time.perf_counter() - started}


def write_json(path: Path, value) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=1) + "\n")
    return path


def unfold(binary: Path, problem: dict, measurements: dict, case_dir: Path) -> dict:
    if case_dir.exists():
        shutil.rmtree(case_dir)
    problem_path = write_json(case_dir / "problem.json", problem)
    input_path = write_json(case_dir / "measurements.json", measurements)
    out_path = case_dir / "unfold.json"
    result = call([str(binary), "unfold", str(problem_path), str(input_path), str(out_path)], UNFOLD_TIMEOUT_S)
    result["dir"] = str(case_dir.relative_to(ROOT))
    result["inputs_sha256"] = {"problem": c.sha256(problem_path), "measurements": c.sha256(input_path)}
    result["output_sha256"] = c.sha256(out_path) if out_path.is_file() else None
    return result


def plain_activities(binary: Path, problem: dict, work: Path, nuclides: list[str], step: int) -> dict:
    """Activities (Bq/g) of the named nuclides at a step, from a plain run."""
    work.mkdir(parents=True, exist_ok=True)
    problem_path = write_json(work / "problem.json", problem)
    out_path = work / "result.json"
    result = call([str(binary), "run", str(problem_path), str(out_path)], RUN_TIMEOUT_S)
    if result["exit"] != 0:
        raise RuntimeError(f"plain run failed in {work}: {result['stderr'][-500:]}")
    steps = json.loads(out_path.read_text())["steps"]
    activity = steps[step - 1]["activity_Bq_per_g"]
    out = {name: float(activity.get(name, 0.0)) for name in nuclides}
    shutil.rmtree(work)
    return out


# ------------------------------------------------------------------------------------------------- problems

def g3_problem(material: dict, flux_ascending: list[float]) -> dict:
    return {
        "spec": "actinv-spec-1", "title": "P195 G3 foil", "projectile": "neutron",
        "library": {"path": c.LIBRARY_CATALOG, "sha256": c.LIBRARY_SHA256},
        "decay": {"primary": c.DECAY_PRIMARY, "fallback": c.DECAY_FALLBACK},
        "material": material,
        "spectrum": {"structure": "fispact-709", "flux_per_group": flux_ascending},
        "schedule": c.G3_SCHEDULE,
    }


def foil_material(element: str) -> dict:
    return {"mass_g": 1.0, "basis": "wt_percent", "composition": {element.upper(): 100.0}}


def prior_flux_fns() -> list[float]:
    spectrum = json.loads((ROOT / c.FNS_PRIOR).read_text())["spectrum"]
    flux = list(spectrum["flux_per_group"])
    if spectrum.get("descending"):
        flux.reverse()
    total = spectrum.get("total")
    if total is not None:
        scale = total / sum(flux)
        flux = [v * scale for v in flux]
    return flux


def g4_problem(prior: list[float]) -> dict:
    return {
        "spec": "actinv-spec-1", "title": "P195 G4 field", "projectile": "neutron",
        "library": {"path": c.LIBRARY_CATALOG, "sha256": c.LIBRARY_SHA256},
        "decay": {"primary": c.DECAY_PRIMARY, "fallback": c.DECAY_FALLBACK},
        "material": foil_material("Fe"),
        "spectrum": {"structure": "fispact-709", "flux_per_group": prior},
        "schedule": [{"dt": "1 s", "flux": 1.0}],
        "uncertainty": {"covariance": {"path": c.COVARIANCE_CATALOG, "sha256": c.COVARIANCE_SHA256},
                        "confidence_level": 0.95},
    }


# ------------------------------------------------------------------------------------------------- G3

def g3_cases(binary: Path) -> tuple[dict, list[dict]]:
    bounds = c.library_bounds()
    phi0 = prior_flux_fns()
    adjustable = [g for g, v in enumerate(phi0) if v > 0.0]
    p0 = c.prior_covariance(bounds, adjustable, c.PRIOR_SD, c.PRIOR_LENGTH)
    chol = np.linalg.cholesky(p0)
    # Drop any declared measurement the prior does not produce, before the first truth is drawn.
    foils, dropped = [], []
    for element, nuclides in c.G3_FOILS:
        values = plain_activities(binary, g3_problem(foil_material(element), phi0), WORK / "g3-prior" / element,
                                  nuclides, c.G3_STEP)
        kept = [n for n in nuclides if values[n] > 0.0]
        dropped += [f"{element}:{n}" for n in nuclides if values[n] <= 0.0]
        if kept:
            foils.append((element, kept))
    rng = np.random.default_rng(SEED)
    n_measurements = sum(len(n) for _, n in foils)
    draws = []
    for t in range(TRUTHS):
        xi = rng.standard_normal(len(adjustable))
        eps = rng.standard_normal(n_measurements)
        draws.append((xi, eps))
    jobs = []
    for t, (xi, eps) in enumerate(draws):
        shift = chol @ xi
        truth = list(phi0)
        for a, g in enumerate(adjustable):
            truth[g] = phi0[g] * math.exp(shift[a])
        jobs.append((t, truth, eps))

    def one(job):
        t, truth, eps = job
        case_dir = WORK / "g3" / f"case-{t:02d}"
        measured = []
        k = 0
        foils_in = []
        for element, nuclides in foils:
            values = plain_activities(binary, g3_problem(foil_material(element), truth),
                                      WORK / "g3-truth" / f"{t:02d}-{element}", nuclides, c.G3_STEP)
            entries = []
            for n in nuclides:
                value = values[n] * math.exp(c.G3_NOISE * eps[k])
                k += 1
                entries.append({"nuclide": n, "step": c.G3_STEP, "activity_Bq_per_g": value,
                                "sigma_Bq_per_g": c.G3_NOISE * value})
                measured.append({"foil": element, "nuclide": n, "true": values[n], "measured": value})
            foils_in.append({"id": element, "material": foil_material(element), "measurements": entries})
        measurements = {"format": "actinv-unfold-input-1",
                        "prior": {"relative_uncertainty": c.PRIOR_SD,
                                  "correlation": {"kind": "lethargy_exponential", "length": c.PRIOR_LENGTH}},
                        "foils": foils_in}
        problem = g3_problem(foil_material("Fe"), phi0)
        result = unfold(binary, problem, measurements, case_dir)
        write_json(case_dir / "truth.json", {"flux_ascending": truth})
        result.update({"case": t, "truth_sha256": c.sha256(case_dir / "truth.json"), "measured": measured})
        return result

    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
        cases = list(pool.map(one, jobs))
    meta = {"foils": [{"element": e, "nuclides": n} for e, n in foils], "dropped": dropped,
            "adjustable_groups": len(adjustable), "measurements": n_measurements}
    return meta, cases


def g2_finite_differences(binary: Path, case: dict) -> list[dict]:
    out = json.loads((ROOT / case["dir"] / "unfold.json").read_text())
    phi = out["groups"]["posterior_flux"]
    problem = json.loads((ROOT / case["dir"] / "problem.json").read_text())
    measurements = json.loads((ROOT / case["dir"] / "measurements.json").read_text())
    by_foil: dict[str, list[dict]] = {}
    for record in out["measurements"]:
        by_foil.setdefault(record["foil"], []).append(record)
    jobs = []
    for foil in measurements["foils"]:
        records = by_foil[foil["id"]]
        groups = sorted({g for record in records for g in c.fd_groups(record)})
        for g in groups:
            for sign in (+1, -1):
                jobs.append((foil, records, g, sign))

    def one(job):
        foil, records, g, sign = job
        moved = list(phi)
        moved[g] = phi[g] * math.exp(sign * c.FD_STEP)
        spec = copy.deepcopy(problem)
        spec["material"] = foil["material"]
        spec["spectrum"] = {"structure": "fispact-709", "flux_per_group": moved}
        nuclides = [r["nuclide"] for r in records]
        values = plain_activities(binary, spec, WORK / "g2-fd" / f"{foil['id']}-{g}-{'p' if sign > 0 else 'm'}",
                                  nuclides, c.G3_STEP)
        return {"foil": foil["id"], "group": g, "sign": sign, "activities": values}

    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
        return list(pool.map(one, jobs))


# ------------------------------------------------------------------------------------------------- G4

def field_spectra() -> dict:
    bounds = c.library_bounds()
    with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as tmp:
        spectrum_file = p17_irdff.extract_exact(p17_irdff.SPECTRUM_ARCHIVE, "IRDFF-II_sp.g", Path(tmp))
        catalog = p17_irdff.spectrum_catalog(spectrum_file)
    spectra = {}
    for field, mat in c.FIELD_MAT.items():
        weights = np.diff(p17_irdff.histogram_cumulative(catalog[mat], np.asarray(bounds)))
        spectra[field] = (weights / weights.sum()).tolist()
    return spectra


def g4_rows() -> dict:
    """The rows P17 scored with a TENDL-2025 production mapping, by field."""
    diagnostics = json.loads((ROOT / "results" / "g4_p17_diagnostics.json").read_text())
    index = json.loads(c.library_index_path().read_text())
    targets = [(int(t["za"]), int(t.get("liso", 0))) for t in index["targets"]]
    rows = {field: [] for field in c.FIELD_TABLES}
    for row in diagnostics["rows"]:
        source = row["source_record"]
        table = source.get("table")
        field = next((f for f, tables in c.FIELD_TABLES.items() if table in tables), None)
        if field is None or not row["family"].startswith("irdff_table_"):
            continue
        mapping = (source.get("production_mapping") or {}).get("library_row")
        if mapping is None:
            continue
        target_index, mt, zap, lfs, _ = mapping
        za, liso = targets[target_index]
        rows[field].append({
            "row_id": row["row_id"], "label": source["label"], "table": table,
            "target": c.nuclide_name(za, liso), "mt": int(mt),
            "product": None if zap == -1 else c.nuclide_name(int(zap), int(lfs)),
            "library_row": [int(v) for v in mapping],
            "E_mb": float(source["measured_mb"]), "E_unc_percent": float(source["experimental_uncertainty_percent"]),
        })
    return rows


def rate_entry(row: dict) -> dict:
    value = row["E_mb"] * 1e-27 * c.G4_TOTAL_FLUX
    entry = {"id": row["row_id"], "target": row["target"], "mt": row["mt"], "rate_per_atom_s": value,
             "sigma_per_atom_s": value * row["E_unc_percent"] / 100.0}
    if row["product"] is not None:
        entry["product"] = row["product"]
    return entry


def g4_arms(binary: Path) -> dict:
    spectra = field_spectra()
    bounds = c.library_bounds()
    rows = g4_rows()
    arms = {}
    jobs = []
    for field, prior_kind in c.G4_ARMS:
        prior = c.g4_prior(field, prior_kind, spectra, bounds)
        problem = g4_problem(prior)
        field_rows = rows[field]
        base = {"format": "actinv-unfold-input-1",
                "prior": {"relative_uncertainty": c.PRIOR_SD,
                          "correlation": {"kind": "lethargy_exponential", "length": c.PRIOR_LENGTH}}}
        arm = f"{field}-{prior_kind}"
        arms[arm] = {"field": field, "prior": prior_kind, "rows": field_rows}
        jobs.append((arm, "full", problem, {**base, "reaction_rates": [rate_entry(r) for r in field_rows]}))
        for k, left in enumerate(field_rows[:LOO_LIMIT]):
            kept = [rate_entry(r) for r in field_rows if r is not left]
            jobs.append((arm, left["row_id"], problem, {**base, "reaction_rates": kept}))

    def one(job):
        arm, which, problem, measurements = job
        name = "full" if which == "full" else "loo-" + which.replace(":", "_")
        result = unfold(binary, problem, measurements, WORK / "g4" / arm / name)
        result.update({"arm": arm, "left_out": None if which == "full" else which})
        return result

    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = list(pool.map(one, jobs))
    for result in results:
        arm = arms[result["arm"]]
        if result["left_out"] is None:
            arm["full"] = result
        else:
            arm.setdefault("loo", []).append(result)
    return arms


# ------------------------------------------------------------------------------------------------- G5, G6

def determinism(binary: Path, g3_first: dict, g4_full: dict) -> list[dict]:
    out = []
    for label, case in (("g3-case-00", g3_first), ("g4-cf252-a", g4_full)):
        problem = json.loads((ROOT / case["dir"] / "problem.json").read_text())
        measurements = json.loads((ROOT / case["dir"] / "measurements.json").read_text())
        again = unfold(binary, problem, measurements, WORK / "g5" / label)
        out.append({"label": label, "first": case["dir"], "second": again})
    return out


def refusals(binary: Path) -> list[dict]:
    phi0 = prior_flux_fns()
    base_problem = g3_problem(foil_material("Ni"), phi0)
    base_input = {"format": "actinv-unfold-input-1",
                  "prior": {"relative_uncertainty": 0.3, "correlation": {"kind": "lethargy_exponential", "length": 1.0}},
                  "foils": [{"id": "ni", "measurements": [{"nuclide": "Co58", "step": 2, "activity_Bq_per_g": 1e4,
                                                             "sigma_Bq_per_g": 300.0}]}]}
    rate_input = {**base_input, "foils": [],
                  "reaction_rates": [{"id": "r", "target": "Ni58", "mt": 103, "rate_per_atom_s": 1e-14,
                                      "sigma_per_atom_s": 1e-15}]}
    cases = []

    def case(name, problem_edit=None, input_edit=None, base=None):
        problem = copy.deepcopy(base_problem)
        measurements = copy.deepcopy(base if base is not None else base_input)
        if problem_edit:
            problem_edit(problem)
        if input_edit:
            input_edit(measurements)
        cases.append((name, problem, measurements))

    def spectrum(edit):
        return lambda p: edit(p["spectrum"])

    def step(edit):
        return lambda p: edit(p["schedule"][0])

    def first_measurement(edit):
        return lambda m: edit(m["foils"][0]["measurements"][0])

    def first_rate(edit):
        return lambda m: edit(m["reaction_rates"][0])

    case("projectile", lambda p: p.update(projectile="proton", options={"temperature_K": 0.0}))
    case("rebin", spectrum(lambda s: s.update(rebin={}, boundaries_eV=c.library_bounds())))
    case("relative_error", spectrum(lambda s: s.update(relative_error=[0.1] * 709)))
    case("step_spectrum", step(lambda s: s.update(spectrum={"structure": "fispact-709",
                                                            "flux_per_group": [1.0] * 709})))
    case("feed", step(lambda s: s.update(feed={"Fe56": 1.0})))
    case("power", step(lambda s: s.update(flux=0.0, power_W_per_g=1.0)))
    case("wall_loading", step(lambda s: (s.pop("flux"), s.update(wall_loading_MW_m2=1.0))))
    case("partition", step(lambda s: s.update(partition={"Ni": 0.5})))
    case("uncertainty_block", lambda p: p.update(uncertainty={"channels": ["flux"], "responses": ["activity:Co58"]}))
    case("rates_with_self_shielding", lambda p: p.update(self_shielding={"table": SHIELD_TABLE, "dilution": "fixed",
                                                                          "sigma0_b": 10.0}), base=rate_input)
    case("no_sigma", input_edit=first_measurement(lambda m: m.pop("sigma_Bq_per_g")))
    case("nonpositive_value", input_edit=first_measurement(lambda m: m.update(activity_Bq_per_g=0.0)))
    case("not_produced", input_edit=first_measurement(lambda m: m.update(nuclide="Au198")))
    case("unknown_target", input_edit=first_rate(lambda r: r.update(target="Ni99")), base=rate_input)
    case("unknown_mt", input_edit=first_rate(lambda r: r.update(mt=999)), base=rate_input)
    case("no_production_row", input_edit=first_rate(lambda r: r.update(product="Fe55")), base=rate_input)
    case("correlation_length", input_edit=lambda m: m["prior"].update(correlation={"kind": "lethargy_exponential",
                                                                                  "length": 0.0}))
    case("all_fixed", input_edit=lambda m: m["prior"].update(relative_uncertainty=0.0))
    out = []
    for name, problem, measurements in cases:
        result = unfold(binary, problem, measurements, WORK / "g6" / name)
        out.append({"case": name, "exit": result["exit"], "stderr": result["stderr"][-600:]})
    return out


# ------------------------------------------------------------------------------------------------- run

def run(args: argparse.Namespace) -> int:
    started = time.perf_counter()
    binary = Path(args.actinv_bin).resolve()
    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True)
    CACHE.mkdir()
    meta, g3 = g3_cases(binary)
    fd = g2_finite_differences(binary, g3[0])
    g4 = g4_arms(binary)
    g5 = determinism(binary, g3[0], g4["cf252-a"]["full"])
    g6 = refusals(binary)
    git = lambda *a: subprocess.run(["git", "-C", str(ROOT), *a], capture_output=True, text=True).stdout.strip()
    record = {
        "schema": c.SCHEMA,
        "inputs": {
            "binary": {"path": str(binary), "sha256": c.sha256(binary)},
            "library": {"path": str(c.library_path()), "sha256": c.sha256(c.library_path()),
                        "index_sha256": c.sha256(c.library_index_path())},
            "covariance": {"path": str(c.covariance_path()), "sha256": c.sha256(c.covariance_path())},
            "committed": c.committed_hashes(),
            "irdff_spectrum_archive": c.sha256(p17_irdff.SPECTRUM_ARCHIVE),
        },
        "environment": {"git_head": git("rev-parse", "HEAD"),
                        "git_dirty_paths": git("status", "--porcelain", "--untracked-files=no"),
                        "cgroup": c.cgroup(), "wall_s": None},
        "g3": {**meta, "cases": g3},
        "g2_finite_differences": {"case": g3[0]["dir"], "runs": fd},
        "g4": g4,
        "g5": g5,
        "g6": g6,
    }
    record["environment"]["wall_s"] = time.perf_counter() - started
    write_json(RECORD, record)
    shutil.rmtree(CACHE, ignore_errors=True)
    failed = sum(1 for case in g3 if case["exit"] != 0)
    g4_failed = sum(1 for a in g4.values() for u in [a["full"], *a.get("loo", [])] if u["exit"] != 0)
    g5_failed = sum(1 for pair in g5 if pair["second"]["exit"] != 0)
    print(f"p195: wrote {RECORD}: g3 {len(g3)} cases ({failed} nonzero exits), fd {len(fd)} runs, "
          f"g4 {sum(1 + len(a.get('loo', [])) for a in g4.values())} unfolds ({g4_failed} nonzero exits), "
          f"g5 {g5_failed} nonzero exits, g6 {len(g6)} cases; {record['environment']['wall_s']:.0f} s")
    return 0


CARGO_GATES = {
    "fmt": ["cargo", "fmt", "--all", "--", "--check"],
    "check": ["cargo", "check", "--workspace", "--tests"],
    "clippy": ["cargo", "clippy", "--workspace", "--all-targets", "--", "-D", "warnings"],
    "test": ["cargo", "test", "--workspace"],
}
CARGO_TIMEOUT_S = 5400


def g1(args: argparse.Namespace) -> int:
    """Run the implementation gates on the current commit and record their exit statuses."""
    binary = Path(args.actinv_bin).resolve()
    git = lambda *a: subprocess.run(["git", "-C", str(ROOT), *a], capture_output=True, text=True).stdout.strip()
    commands = {}
    for name, command in CARGO_GATES.items():
        result = call(command, CARGO_TIMEOUT_S)
        commands[name] = {"command": " ".join(command), "exit": result["exit"], "wall_s": result["wall_s"],
                          "stderr_tail": result["stderr"][-1500:]}
        print(f"p195 g1: {name} exit {result['exit']}")
    record = {"git_head": git("rev-parse", "HEAD"),
              "git_dirty_paths": git("status", "--porcelain", "--untracked-files=no"),
              "binary_sha256": c.sha256(binary), "cgroup": c.cgroup(), "commands": commands}
    write_json(c.G1_RECORD, record)
    return 0 if all(v["exit"] == 0 for v in commands.values()) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("run", "g1", "rehearse"):
        sub.add_parser(name).add_argument("--actinv-bin", required=True,
                                          help="the evidence candidate's release binary")
    args = parser.parse_args()
    if args.command == "rehearse":
        global WORK, CACHE, RECORD, SEED, TRUTHS, LOO_LIMIT
        WORK = ROOT / "target" / "p195-rehearsal"
        CACHE = WORK / "cache"
        RECORD = WORK / "record.json"
        SEED, TRUTHS, LOO_LIMIT = c.SEED + 9000, 2, 2
        return run(args)
    return run(args) if args.command == "run" else g1(args)


if __name__ == "__main__":
    sys.exit(main())
