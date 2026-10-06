#!/usr/bin/env python3
"""P120 candidate proof; every invocation retains fresh observations under target.

Synthetic reaction rates and analytic decay are independent of the converter.
This checks input mapping and compatibility, not evaluated-data validation.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import platform
import sys
import time
from pathlib import Path

import numpy as np

from p105_budget_control import _run
from p11_fixtures import ROWS, group_hash, sha256, write_decay, write_json

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "controls/fixtures/p120/rebin.json"
FIXTURE_SHA = "a1e906986c56aa3a395ab2ce384d1dec3473c070ca7003df1c217e2dffba7ef2"
PROTOCOL_SHA = "9707a33b791a57e3435039478c4c475415754d84b66d33fb8dda820b072245de"
P11_SHA = "6281020bd7f489513f10a3330a9205c75ebda3e868520491a887ac47f2842c24"
EV_J = 1.602176634e-19


def frozen_case() -> dict:
    for path, expected in ((FIXTURE, FIXTURE_SHA),
                           (ROOT / "protocols/ACTINV-P120_PROTOCOL.md", PROTOCOL_SHA),
                           (ROOT / "controls/p11_fixtures.py", P11_SHA)):
        if sha256(path) != expected:
            raise ValueError(f"registered source changed: {path.relative_to(ROOT)}")
    return json.loads(FIXTURE.read_text())["kinetics"]


def make_fixture(work: Path) -> dict:
    """Return a fine-grid single-case spec, backed by tiny synthetic files."""
    case = frozen_case()
    work.mkdir(parents=True, exist_ok=True)
    work = work.resolve()
    library = work / "activation.npz"
    bounds = case["library_bounds_eV"]
    np.savez(library, rows=ROWS, sig=np.asarray(case["sigma_barns"], dtype=np.float64),
             bounds=np.asarray(bounds, dtype=np.float64))
    write_json(work / "activation_index.json", {
        "schema": "actinv-library-index-1", "projectile": "neutron", "groups": "custom",
        "group_boundary_sha256": group_hash(bounds), "temperature_K": 293.6,
        "sha256_npz": sha256(library),
        "targets": [{"file": "p120-synthetic.endf", "source_sha256": "1" * 64,
                     "mat": 2631, "za": 26056, "liso": 0, "awr": 55.454, "ledger": []}],
    })
    decay = work / "decay.endf"
    write_decay(decay)
    return {
        "spec": "actinv-spec-1", "title": "P120 synthetic input mapping",
        "projectile": "neutron", "library": {"path": str(library), "sha256": sha256(library)},
        "decay": {"primary": str(decay)},
        "material": {"mass_g": 1.0, "basis": "atoms_per_g",
                     "composition": case["initial_atoms_per_g"]},
        "spectrum": {"structure": "custom", "boundaries_eV": bounds,
                     "flux_per_group": case["expected_library_flux"], "descending": False},
        "schedule": [{"dt": f"{step['dt_s']} s", "flux": step["flux"]}
                     for step in case["schedule"]],
        "options": {"mode": "coupled", "prune": "none", "bmin_atoms_per_g": 0.0,
                    "temperature_K": 293.6, "cram_order": 48,
                    "outputs": ["inventory", "activity", "heat", "ledger", "certificate"]},
    }


def coarse_spec(base: dict) -> dict:
    value = copy.deepcopy(base)
    case = frozen_case()
    value["spectrum"] = {"structure": "custom", "boundaries_eV": case["source_bounds_eV"],
                         "flux_per_group": case["source_shape"],
                         "total": case["source_total_n_cm2_s"], "descending": False,
                         "rebin": "equal_lethargy"}
    return value


def strip_timing(value):
    if isinstance(value, dict):
        return {key: strip_timing(item) for key, item in value.items()
                if key not in {"ms", "elapsed_ms"}}
    if isinstance(value, list):
        return [strip_timing(item) for item in value]
    return value


def scientific_steps(document: dict) -> list:
    return [{key: step[key] for key in
             ("t_s", "flux", "inventory", "activity_Bq_per_g", "heat_W_per_g")}
            for step in document["steps"]]


def close_vectors(actual, expected, *, rel=1e-12, abs_=1e-12, path="") -> list[str]:
    """Compare complete shapes; a missing field or NaN cannot pass."""
    if isinstance(expected, dict):
        if not isinstance(actual, dict) or actual.keys() != expected.keys():
            return [f"{path}: keys differ"]
        return [error for key in expected
                for error in close_vectors(actual[key], expected[key], rel=rel, abs_=abs_,
                                           path=f"{path}/{key}")]
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            return [f"{path}: length differs"]
        return [error for i, item in enumerate(expected)
                for error in close_vectors(actual[i], item, rel=rel, abs_=abs_, path=f"{path}/{i}")]
    if isinstance(expected, (float, int)) and not isinstance(expected, bool):
        if isinstance(actual, bool) or not isinstance(actual, (float, int)) or not math.isfinite(actual) or not math.isclose(
                actual, expected, rel_tol=rel, abs_tol=abs_):
            return [f"{path}: {actual!r} != {expected!r}"]
        return []
    return [] if actual == expected else [f"{path}: {actual!r} != {expected!r}"]


def analytic_steps() -> list[dict]:
    case = frozen_case()
    parent = 1.0
    products = {name: 0.0 for name in case["half_life_s"]}
    output = []
    elapsed = 0.0
    for step in case["schedule"]:
        dt, multiplier = step["dt_s"], step["flux"]
        removal = case["removal_rate_s"] * multiplier
        for name, previous in products.items():
            decay = math.log(2) / case["half_life_s"][name]
            production = case["production_rate_s"][name] * multiplier
            products[name] = (previous * math.exp(-decay * dt)
                              + production * parent * (math.exp(-removal * dt)
                                                       - math.exp(-decay * dt)) / (decay - removal))
        parent *= math.exp(-removal * dt)
        elapsed += dt
        activity = {name: atoms * math.log(2) / case["half_life_s"][name]
                    for name, atoms in products.items()}
        heat = {"alpha": 0.0, "beta": 0.0, "gamma": 0.0}
        for name, bq in activity.items():
            for component, energy in zip(("beta", "gamma", "alpha"),
                                         case["mean_decay_energy_eV"][name]):
                heat[component] += bq * energy * EV_J
        heat["total"] = math.fsum(heat.values())
        output.append({"t_s": elapsed, "atoms": {"Fe56": parent, **products},
                       "activity": {"Fe56": 0.0, **activity}, "heat": heat})
    return output


def check_analytic(document: dict) -> list[str]:
    errors = []
    expected = analytic_steps()
    if len(document.get("steps", [])) != len(expected):
        return ["analytic endpoint count"]
    for i, (step, reference) in enumerate(zip(document["steps"], expected)):
        atoms = {item["nuclide"]: item["atoms_per_g"] for item in step["inventory"]}
        for name, value in reference["atoms"].items():
            errors += close_vectors(atoms.get(name), value, rel=1e-10, path=f"step{i}/{name}/atoms")
            errors += close_vectors(step["activity_Bq_per_g"].get(name, 0.0),
                                    reference["activity"][name], rel=1e-10,
                                    path=f"step{i}/{name}/activity")
        for name, value in reference["heat"].items():
            # W/g is ~1e-14 here: enforce a useful heat threshold as well as
            # the registered inventory/activity threshold, never accept zero.
            errors += close_vectors(step["heat_W_per_g"][name], value, rel=1e-10,
                                    abs_=1e-30, path=f"step{i}/{name}/heat")
        errors += close_vectors(step["t_s"], reference["t_s"], path=f"step{i}/time")
    return errors


class Campaign:
    def __init__(self, work: Path, candidate: Path, reference: Path):
        self.work, self.candidate, self.reference = work, candidate, reference
        self.checks: list[dict] = []
        self.commands: list[dict] = []
        self.observations: dict = {}

    def require(self, name: str, condition: bool, detail=None):
        record = {"name": name, "pass": bool(condition)}
        if detail is not None:
            record["detail"] = detail
        self.checks.append(record)
        if not condition:
            raise AssertionError(f"{name}: {detail}")

    def run(self, name: str, spec: dict, *, reference=False, mesh=False, refuse=False) -> dict | None:
        source, output = self.work / f"{name}.spec.json", self.work / f"{name}.result.json"
        write_json(source, spec)
        if refuse:
            output.write_text("existing-output-sentinel\n")
        argv = [str(self.reference if reference else self.candidate),
                "mesh" if mesh else "run", str(source), str(output)]
        result = _run(argv, ROOT, timeout_s=120)
        raw = (result.stdout + result.stderr).encode()
        log = self.work / f"{name}.log"
        log.write_bytes(raw)
        self.commands.append({"name": name, "argv": argv, "exit_code": result.returncode,
                              "log_sha256": hashlib.sha256(raw).hexdigest(),
                              "input_sha256": sha256(source)})
        if refuse:
            self.require(f"refuse:{name}", result.returncode != 0
                         and output.read_text() == "existing-output-sentinel\n",
                         result.stderr[-1200:])
            return None
        self.require(f"run:{name}", result.returncode == 0, result.stderr[-1200:])
        if mesh:
            cells = [item for item in map(json.loads, output.read_text().splitlines())
                     if item.get("record") == "cell"]
            self.require("one-cell-mesh", len(cells) == 1)
            return cells[0]["result"]
        return json.loads(output.read_text())

    def cached_runs(self, specs: list[dict]) -> list[dict]:
        request_path = self.work / "worker-requests.ndjson"
        requests = [{"schema": "actinv-worker-request-1", "id": i, "op": "run", "spec": spec}
                    for i, spec in enumerate(specs)]
        request_path.write_text("".join(json.dumps(item) + "\n" for item in requests))
        # Constant shell code, with paths passed as positional arguments. exec
        # replaces the shell, so the bounded child's PID is the built CLI.
        argv = ["bash", "-c", 'exec "$1" worker < "$2"', "p120-worker",
                str(self.candidate), str(request_path)]
        result = _run(argv, ROOT, timeout_s=120)
        raw = (result.stdout + result.stderr).encode()
        (self.work / "worker.log").write_bytes(raw)
        self.commands.append({"name": "cached-worker", "argv": argv,
                              "exit_code": result.returncode,
                              "log_sha256": hashlib.sha256(raw).hexdigest(),
                              "input_sha256": sha256(request_path)})
        self.require("worker-exit", result.returncode == 0, result.stderr[-1200:])
        responses = list(map(json.loads, result.stdout.splitlines()))
        self.require("worker-responses", len(responses) == len(specs)
                     and all(item.get("ok") and item.get("id") == i
                             for i, item in enumerate(responses)))
        self.require("worker-cache-reused", [item["timing_ms"]["warm"] for item in responses]
                     == [False, True, True])
        return [item["result"] for item in responses]


def one_cell_mesh(base: dict, source: dict, path: Path) -> dict:
    flux = source["flux_per_group"]
    if source.get("total") is not None:
        flux = [value * source["total"] / math.fsum(flux) for value in flux]
    if source.get("descending"):
        flux = list(reversed(flux))
    total = math.fsum(flux)
    records = [
        {"record": "header", "schema": "actinv-flux-1",
         "source": {"format": "synthetic-p120", "path": str(path), "sha256": "0" * 64},
         "energy_boundaries_eV": source["boundaries_eV"], "flux_units": "n cm^-2 s^-1",
         "cell_count": 1},
        {"record": "cell", "ordinal": 0, "id": "0", "flux_per_group": flux,
         "relative_error": source.get("relative_error", [0.0] * len(flux)), "flux_total": total},
        {"record": "footer", "cell_count": 1, "flux_sum_over_cells": total},
    ]
    path.write_text("".join(json.dumps(record) + "\n" for record in records))
    return {key: copy.deepcopy(base[key]) for key in
            ("title", "projectile", "library", "decay", "material", "schedule", "options")} | {
        "spec": "actinv-mesh-spec-1", "flux": {"path": str(path), "sha256": sha256(path)},
        "chunk_cells": 1, "threads": 1, "group_workloads": False,
        "cell_result_fields": ["steps", "mode", "pruned_states", "total_states"],
    }


def prove(campaign: Campaign):
    base = make_fixture(campaign.work / "fixture")
    direct = campaign.run("direct", base)
    old = campaign.run("reference-default", base, reference=True)
    campaign.require("default-document-unchanged", strip_timing(direct) == strip_timing(old))
    mapped_spec = coarse_spec(base)
    mapped = campaign.run("coarse", mapped_spec)
    campaign.require("analytic-kinetics", not (errors := check_analytic(mapped)), errors)
    vectors = scientific_steps(mapped)
    campaign.require("direct-fine-parity", not (errors := close_vectors(vectors, scientific_steps(direct))), errors)
    campaign.observations["direct_fine_raw_scientific_equality"] = vectors == scientific_steps(direct)
    mesh = campaign.run("mesh", one_cell_mesh(base, mapped_spec["spectrum"],
                                             campaign.work / "flux.ndjson"), mesh=True)
    campaign.require("mesh-parity", not (errors := close_vectors(vectors, scientific_steps(mesh))), errors)
    campaign.require("opt-in-ledger-present", "spectrum_rebin" in mapped["ledger"])
    ledger = mapped["ledger"]["spectrum_rebin"]
    campaign.require("declared-source-retained", ledger["source_spectrum"] == mapped_spec["spectrum"]
                     and ledger["source_boundaries_eV"] == [1, 16]
                     and ledger["destination_boundaries_eV"] == [1, 4, 16]
                     and mapped["certificate"]["spectrum_rebin"] == ledger
                     and ledger["method"] == "equal_lethargy")
    campaign.require("rebin-closure", ledger["source_total"] == 1e24
                     and math.isclose(ledger["destination_total"], 1e24, rel_tol=1e-12)
                     and ledger["underflow"] == 0 and ledger["overflow"] == 0
                     and ledger["relative_closure_error"] <= 1e-12, ledger)
    doubled = copy.deepcopy(mapped_spec)
    doubled["spectrum"]["total"] *= 2
    doubled_direct = campaign.run("double-total", doubled)
    for i, (cached, fresh) in enumerate(zip(campaign.cached_runs([mapped_spec, doubled, mapped_spec]),
                                          [mapped, doubled_direct, mapped])):
        campaign.require(f"cached-fresh-parity:{i}", not (errors := close_vectors(
            scientific_steps(cached), scientific_steps(fresh))), errors)
        campaign.require(f"cached-source-total:{i}", cached["ledger"]["spectrum_rebin"]
                         == fresh["ledger"]["spectrum_rebin"])
    for name, spectrum in (
        ("absolute", mapped_spec["spectrum"] | {"total": None, "flux_per_group": [1e24]}),
        ("normalized-shape", mapped_spec["spectrum"] | {"flux_per_group": [7.0]}),
        ("descending-coarse", mapped_spec["spectrum"] | {"descending": True}),
        ("identity", base["spectrum"] | {"rebin": "equal_lethargy"}),
        ("descending-fine", base["spectrum"] | {"flux_per_group": [5e23, 5e23],
                                               "descending": True, "rebin": "equal_lethargy"}),
    ):
        variant = copy.deepcopy(base)
        variant["spectrum"] = spectrum
        result = campaign.run(name, variant)
        campaign.require(f"parity:{name}", not (errors := close_vectors(
            scientific_steps(result), vectors)), errors)
    unequal = copy.deepcopy(mapped_spec)
    unequal["spectrum"] = {"structure": "custom", "boundaries_eV": [1, 2, 16],
                           "flux_per_group": [6e23, 3e23], "descending": True,
                           "rebin": "equal_lethargy"}
    unequal_direct = copy.deepcopy(base)
    # The high source group spans three log2 intervals; one is below 4 eV
    # and two are above it: [3+6/3, 2*6/3] in units of 1e23.
    unequal_direct["spectrum"]["flux_per_group"] = [5e23, 4e23]
    campaign.require("descending-unequal-nonidentity-parity", not (errors := close_vectors(
        scientific_steps(campaign.run("descending-unequal", unequal)),
        scientific_steps(campaign.run("descending-unequal-direct", unequal_direct)))), errors)
    null = copy.deepcopy(base)
    null["spectrum"]["rebin"] = None
    campaign.require("null-keeps-default", strip_timing(campaign.run("null", null)) == strip_timing(direct))
    for name, patch in (
        ("missing-option", {"rebin": None}), ("unknown-method", {"rebin": "linear"}),
        ("zero-edge", {"boundaries_eV": [0, 16]}), ("out-of-range-low", {"boundaries_eV": [.5, 16]}),
        ("out-of-range-high", {"boundaries_eV": [1, 32]}),
        ("reverse-bounds", {"boundaries_eV": [16, 1]}),
        ("duplicate-bounds", {"boundaries_eV": [1, 1]}),
        ("wrong-length", {"boundaries_eV": [1, 4, 16]}),
        ("negative-flux", {"flux_per_group": [-1]}),
        ("nonfinite-flux", {"flux_per_group": [math.inf]}),
        ("nonfinite-bounds", {"boundaries_eV": [1, math.inf]}),
        ("missing-bounds", {"boundaries_eV": None}),
        ("zero-shape-with-total", {"flux_per_group": [0]}),
        ("named-grid", {"structure": "fispact-709"}),
    ):
        variant = copy.deepcopy(mapped_spec)
        variant["spectrum"].update(patch)
        campaign.run(name, variant, refuse=True)
    charged = copy.deepcopy(mapped_spec)
    charged["projectile"] = "proton"
    campaign.run("non-neutron", charged, refuse=True)
    step_spectrum = copy.deepcopy(mapped_spec)
    step_spectrum["schedule"][0]["spectrum"] = base["spectrum"]
    campaign.run("step-spectrum", step_spectrum, refuse=True)
    step_rebin = copy.deepcopy(base)
    step_rebin["schedule"][0]["spectrum"] = base["spectrum"] | {"rebin": "equal_lethargy"}
    campaign.run("step-only-rebin", step_rebin, refuse=True)
    zero = copy.deepcopy(mapped_spec)
    zero["spectrum"]["total"] = 0.0
    zero_direct = copy.deepcopy(base)
    zero_direct["spectrum"]["flux_per_group"] = [0.0, 0.0]
    zero_result = campaign.run("zero", zero)
    campaign.require("zero-flux-parity", not (errors := close_vectors(
        scientific_steps(zero_result), scientific_steps(campaign.run("zero-direct", zero_direct)))), errors)
    flux_spec = copy.deepcopy(mapped_spec)
    flux_spec["spectrum"]["relative_error"] = [0.1]
    flux_spec["uncertainty"] = {"channels": ["flux"], "responses": ["activity.total", "heat.total"]}
    uncertain = campaign.run("flux-uncertainty", flux_spec)
    campaign.require("one-source-error-parameter", uncertain["ledger"]["uncertainty"]["flux_parameters"] == 1)
    plus, minus = copy.deepcopy(mapped_spec), copy.deepcopy(mapped_spec)
    h = frozen_case()["finite_difference_h"]
    plus["spectrum"]["total"] *= 1 + h
    minus["spectrum"]["total"] *= 1 - h
    plus_result, minus_result = campaign.run("fd-plus", plus), campaign.run("fd-minus", minus)
    for i, step in enumerate(uncertain["steps"]):
        for name in ("activity.total", "heat.total"):
            response = step["uncertainty"]["responses"][name]
            sensitivities = response["flux_sensitivities"]
            campaign.require(f"source-basis:{i}:{name}", len(sensitivities) == 1
                             and sensitivities[0]["parameter"]["lower_bound_eV"] == 1
                             and sensitivities[0]["parameter"]["upper_bound_eV"] == 16)
            def total(document):
                endpoint = document["steps"][i]
                return (math.fsum(endpoint["activity_Bq_per_g"].values()) if name == "activity.total"
                        else endpoint["heat_W_per_g"]["total"])
            fd = (total(plus_result) - total(minus_result)) / (2 * h)
            sensitivity = sensitivities[0]["value"]
            tolerance = 1e-4 * abs(fd) + 1e-10 * abs(response["nominal"])
            campaign.require(f"finite-difference:{i}:{name}", abs(sensitivity - fd) <= tolerance,
                             {"sensitivity": sensitivity, "finite_difference": fd, "tolerance": tolerance})
    missing = copy.deepcopy(flux_spec)
    del missing["spectrum"]["relative_error"]
    campaign.run("missing-errors", missing, refuse=True)
    descending = copy.deepcopy(base)
    descending["spectrum"].update(rebin="equal_lethargy", descending=True,
                                  flux_per_group=[7.5e23, 2.5e23], relative_error=[.2, .1])
    descending["uncertainty"] = flux_spec["uncertainty"]
    ordered = campaign.run("descending-errors", descending)
    parameters = ordered["steps"][0]["uncertainty"]["responses"]["activity.total"]["flux_sensitivities"]
    parameters.sort(key=lambda item: item["parameter"]["group"])
    campaign.require("descending-error-order", [(item["parameter"]["lower_bound_eV"],
                                                item["parameter"]["flux_per_cm2_s"],
                                                item["parameter"]["standard_uncertainty_relative"])
                                               for item in parameters] == [(4, 7.5e23, .2), (1, 2.5e23, .1)])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-bin", type=Path, required=True)
    parser.add_argument("--reference-bin", type=Path, required=True)
    args = parser.parse_args()
    for binary in (args.candidate_bin, args.reference_bin):
        if not binary.is_absolute() or not binary.is_file():
            parser.error("both binaries must be existing absolute paths")
    args.candidate_bin = args.candidate_bin.resolve()
    args.reference_bin = args.reference_bin.resolve()
    if (args.candidate_bin == args.reference_bin
            or sha256(args.candidate_bin) == sha256(args.reference_bin)):
        parser.error("candidate and registered reference must be distinct binaries")
    work = ROOT / "target/p120-controls" / f"{time.time_ns()}"
    work.mkdir(parents=True, exist_ok=False)
    campaign = Campaign(work, args.candidate_bin, args.reference_bin)
    report = {"schema": "actinv-p120-proof-1", "fixture_sha256": FIXTURE_SHA,
              "protocol_sha256": PROTOCOL_SHA, "python": platform.python_version(),
              "numpy": np.__version__, "candidate_bin": str(args.candidate_bin),
              "candidate_sha256": sha256(args.candidate_bin), "reference_bin": str(args.reference_bin),
              "reference_sha256": sha256(args.reference_bin), "checks": campaign.checks,
              "commands": campaign.commands, "evaluated_data": "not used; independent synthetic controls"}
    report["observations"] = campaign.observations
    try:
        prove(campaign)
        report["pass"] = True
    except (AssertionError, ValueError, KeyError, RuntimeError, OSError) as error:
        report.update({"pass": False, "error": f"{type(error).__name__}: {error}"})
    write_json(work / "proof.json", report)
    print(json.dumps({"pass": report["pass"], "checks": len(campaign.checks),
                      "report": str(work / "proof.json"), "error": report.get("error")}, indent=2))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
