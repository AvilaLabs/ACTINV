#!/usr/bin/env python3
"""Small Nucleide -> ACTINV -> Nucleide activation and photon-source example.

This optional adapter translates one ALARA-style material, 709-group flux,
and schedule fixture through Nucleide's public Python API, runs ACTINV's mesh
solver, then reads ACTINV's newly exported photon source with Nucleide. It is
an interchange demonstration; it does not validate photon energy-bin bounds.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


INPUTS = Path(__file__).resolve().parent / "inputs"
ALARA_FLUX_NAME = "neutron_flux"
ACTINV_NUCLIDE_NAMES = {"Fe56": "FE56"}

if __package__:
    from . import direct_case
else:  # standalone `python contrib/nucleide_r2s/e2e.py`
    import direct_case


def _nucleide():
    try:
        from nucleide import alara, material, r2s
    except ModuleNotFoundError as exc:
        if exc.name != "nucleide":
            raise
        raise RuntimeError("install the optional packages from contrib/nucleide_r2s/requirements.txt") from exc
    return alara, material, r2s


def sha256_file(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def _finite_positive(value: Any, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be finite and positive") from exc
    if not math.isfinite(result) or result <= 0.0:
        raise ValueError(f"{label} must be finite and positive")
    return result


def _box_volume(bounds_cm: list[list[float]]) -> float:
    if len(bounds_cm) != 3 or any(len(axis) != 2 for axis in bounds_cm):
        raise ValueError("bounds_cm must contain [lower, upper] for x, y, and z")
    widths = []
    for axis, pair in zip("xyz", bounds_cm):
        low, high = (float(pair[0]), float(pair[1]))
        if not math.isfinite(low) or not math.isfinite(high) or high <= low:
            raise ValueError(f"invalid {axis} bounds {pair!r}")
        widths.append(high - low)
    return math.prod(widths)


def translate_material(material_doc: dict[str, Any]) -> dict[str, Any]:
    """Use Nucleide's mass mixer, then produce ACTINV's absolute-mass input.

    Nucleide's reference gram amounts are normalized to weight percentages.
    The zone mass is separate: density[g/cm3] * box volume[cm3].
    """
    _, material_api, _ = _nucleide()

    components = material_doc.get("components")
    if not isinstance(components, list) or len(components) != 1:
        raise ValueError("example supports a single component material fixture")
    entry = components[0]
    composition = entry.get("composition_g")
    if not isinstance(composition, dict) or not composition:
        raise ValueError("material component needs composition_g in nuclide grams")
    for name, grams in composition.items():
        _finite_positive(grams, f"composition_g[{name}]")
    fraction = _finite_positive(entry.get("mass_fraction"), "material mass_fraction")
    if fraction != 1.0:
        raise ValueError("single component material mass_fraction must be 1")
    mixed = material_api.mix_by_mass([(composition, fraction)])
    if not mixed:
        raise ValueError("Nucleide returned an empty material composition")

    total_grams = sum(float(value) for value in mixed.values())
    if not math.isfinite(total_grams) or total_grams <= 0.0:
        raise ValueError("Nucleide material composition has no positive mass")
    composition_wt: dict[str, float] = {}
    for nucleide_name, grams in mixed.items():
        canonical = str(nucleide_name)
        actinv_name = ACTINV_NUCLIDE_NAMES.get(canonical)
        if actinv_name is None:
            if "_m" in canonical.lower():
                raise ValueError(
                    f"Nucleide metastable nuclide {canonical!r} has no explicit "
                    "Nucleide-to-ACTINV nuclide mapping"
                )
            raise ValueError(f"no explicit Nucleide-to-ACTINV nuclide mapping for {canonical!r}")
        composition_wt[actinv_name] = composition_wt.get(actinv_name, 0.0) + 100.0 * float(grams) / total_grams

    density = _finite_positive(material_doc.get("density_g_cm3"), "density_g_cm3")
    bounds = material_doc.get("bounds_cm")
    volume = _box_volume(bounds)
    mass = density * volume
    if not math.isfinite(mass) or mass <= 0.0:
        raise ValueError("density × volume does not produce finite positive material mass")
    return {
        "mass_g": mass,
        "basis": "wt_percent",
        "composition": composition_wt,
        "density_g_cm3": density,
        "volume_cm3": volume,
        "bounds_cm": bounds,
    }


def translate_flux(
    deck_doc: dict[str, Any],
    flux_doc: dict[str, Any],
    group_manifest: dict[str, Any],
    library_bounds_ascending: list[float],
) -> dict[str, Any]:
    """Apply the ALARA scalar once and reverse descending groups to ACTINV order.

    This fixture already uses physical, group-integrated ``n cm^-2 s^-1``;
    no transport-tally source-rate normalization is performed here. The
    manifest declares descending ALARA fast-to-thermal order. ACTINV consumes
    ascending energy intervals, so both group edges and values reverse.
    """
    flux_defs = [item for item in deck_doc.get("fluxes", []) if item.get("name") == ALARA_FLUX_NAME]
    if len(flux_defs) != 1:
        raise ValueError(f"expected exactly one ALARA flux definition named {ALARA_FLUX_NAME!r}")
    flux_def = flux_defs[0]
    if str(flux_def.get("format", "default")).lower() != "default":
        raise ValueError("only Nucleide-parsed ALARA default-format flux is supported")
    scale = float(flux_def.get("scale", 1.0))
    if not math.isfinite(scale) or scale <= 0.0:
        raise ValueError("ALARA flux scale must be finite and positive")
    skip = int(flux_def.get("skip", 0))
    intervals = flux_doc.get("intervals", [])
    if skip < 0 or skip >= len(intervals):
        raise ValueError(f"ALARA flux skip {skip} is outside {len(intervals)} parsed intervals")
    if flux_doc.get("groups_per_interval") != group_manifest.get("group_count"):
        raise ValueError("parsed ALARA flux group count does not match the explicit group manifest")
    if group_manifest.get("energy_unit") != "eV":
        raise ValueError("neutron group manifest must use eV boundaries")
    if group_manifest.get("flux_units") != "n cm^-2 s^-1 per group (group-integrated)":
        raise ValueError("flux fixture must declare physical group-integrated n cm^-2 s^-1")
    if group_manifest.get("alara_order") != "descending_fast_to_thermal":
        raise ValueError("unsupported ALARA neutron group order")
    if (group_manifest.get("group_structure") != "fispact-709"
            or group_manifest.get("actinv_order") != "ascending_low_to_high"):
        raise ValueError("neutron manifest must declare fispact-709 in ascending ACTINV order")

    descending = [float(value) for value in group_manifest.get("boundaries_eV", [])]
    if len(descending) != int(group_manifest["group_count"]) + 1:
        raise ValueError("group manifest boundary count does not equal group_count + 1")
    if any(not math.isfinite(value) or value <= 0.0 for value in descending):
        raise ValueError("neutron group boundaries must be finite positive eV values")
    if any(right >= left for left, right in zip(descending, descending[1:])):
        raise ValueError("ALARA neutron boundaries must be strictly descending")
    ascending = list(reversed(descending))
    actual_bounds = [float(value) for value in library_bounds_ascending]
    if len(actual_bounds) != len(ascending) or any(a != b for a, b in zip(actual_bounds, ascending)):
        raise ValueError("Nucleide/ALARA neutron group edges do not exactly match ACTINV library bounds")

    raw = [float(value) for value in intervals[skip]]
    if len(raw) != int(group_manifest["group_count"]):
        raise ValueError("selected ALARA flux interval has the wrong group count")
    if any(not math.isfinite(value) or value < 0.0 for value in raw):
        raise ValueError("flux values must be finite and nonnegative")
    physical_alara = [value * scale for value in raw]
    if any(not math.isfinite(value) for value in physical_alara):
        raise ValueError("scaled physical flux must remain finite")
    physical_actinv = list(reversed(physical_alara))
    total = _finite_positive(sum(physical_actinv), "scaled physical flux total")
    declared_total = _finite_positive(group_manifest.get("physical_flux_total_n_cm2_s"), "declared physical flux total")
    if not math.isclose(total, declared_total, rel_tol=1e-12):
        raise ValueError("scaled parsed flux total disagrees with the manifest physical flux total")
    return {
        "energy_boundaries_eV": ascending,
        "flux_per_group": physical_actinv,
        "flux_total": total,
        "flux_units": "n cm^-2 s^-1",
        "source_scale": scale,
        "skip": skip,
        "input_order": "ALARA descending fast-to-thermal",
        "actinv_order": "ascending low-to-high",
    }


def translate_schedule(deck_text: str, deck_doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Expand through Nucleide, then add the deck's after-shutdown cooling time."""
    alara, _, _ = _nucleide()
    expanded = alara.alara_expand_schedule(deck_text)
    flux_names = {item.get("name") for item in deck_doc.get("fluxes", [])}
    result: list[dict[str, Any]] = []
    for step in expanded:
        duration = _finite_positive(step.get("duration_s"), "expanded ALARA duration_s")
        name = step.get("flux", "")
        cooling = bool(step.get("is_cooling"))
        if cooling:
            result.append({"dt": f"{duration:.17g} s", "flux": 0.0})
        else:
            if name != ALARA_FLUX_NAME or name not in flux_names:
                raise ValueError(f"schedule references unsupported or undefined flux {name!r}")
            # The ALARA scalar has already been applied to the physical flux
            # vector exactly once; the ACTINV schedule multiplier is unity.
            result.append({"dt": f"{duration:.17g} s", "flux": 1.0})
    cooling_times = [float(value) for value in deck_doc.get("cooling_times_s", [])]
    if not cooling_times or any(not math.isfinite(value) or value <= 0 for value in cooling_times):
        raise ValueError("ALARA cooling block must contain positive post-shutdown times")
    if any(right <= left for left, right in zip(cooling_times, cooling_times[1:])):
        raise ValueError("ALARA cooling times must be strictly increasing")
    previous = 0.0
    for time_s in cooling_times:
        duration = time_s - previous
        result.append({"dt": f"{duration:.17g} s", "flux": 0.0})
        previous = time_s
    if not any(float(step["flux"]) > 0.0 for step in result):
        raise ValueError("expanded schedule contains no nonzero irradiation step")
    return result


def library_bounds(path: Path) -> list[float]:
    """Read ACTINV's actual group edges from the NPZ activation library."""
    try:
        import numpy as np
    except ImportError as exc:  # pragma: no cover - environment-specific
        raise RuntimeError("numpy is required to inspect ACTINV .npz library group edges") from exc
    with np.load(path, allow_pickle=False) as archive:
        values = [float(item) for item in archive["bounds"]]
    if len(values) != 710 or any(right <= left for left, right in zip(values, values[1:])):
        raise ValueError("example expects ACTINV's ascending fispact-709 library")
    return values


def load_nucleide_inputs(input_dir: Path, activation_library: Path) -> dict[str, Any]:
    """Parse all Nucleide-side files and return the translated ACTINV inputs."""
    alara, _, r2s = _nucleide()
    material_doc = json.loads((input_dir / "material.json").read_text())
    manifest = json.loads((input_dir / "neutron_groups.json").read_text())
    deck_text = (input_dir / "activation.alara").read_text()
    flux_text = (input_dir / "flux.txt").read_text()
    deck = alara.alara_parse_deck(deck_text)
    # Nucleide 0.16's Python deck summary omits mat_loading entries. Restrict
    # this opaque block to the one supported binding; do not infer a mixture.
    loading = re.findall(r"(?ms)^[ \t]*mat_loading[ \t]*\n(.*?)^[ \t]*end[ \t]*$", deck_text)
    if len(loading) != 1 or loading[0].split() != ["zone1", "mix1"]:
        raise ValueError("mat_loading must bind exactly zone1 to mix1 in this example")
    parsed_flux = alara.alara_parse_flux(flux_text, ALARA_FLUX_NAME)
    workflow = r2s.r2s_from_deck(deck_text)
    if workflow.get("steps") != [{"zone": "zone1", "flux": ALARA_FLUX_NAME}]:
        raise ValueError("Nucleide R2S adapter did not resolve fixture mapping zone1 → neutron_flux")
    mix = next((item for item in deck.get("mixtures", []) if item.get("name") == "mix1"), None)
    if mix is None:
        raise ValueError("Nucleide ALARA deck parser did not find fixture mixture mix1")
    if mix.get("entries") != [{"kind": "material", "name": "fe56", "rel_density": 1.0, "vol_fraction": 1.0}]:
        raise ValueError("ALARA mixture mix1 must map material fe56 at unit relative density and volume fraction")
    if parsed_flux.get("name") != ALARA_FLUX_NAME:
        raise ValueError("Nucleide flux parser returned a name inconsistent with the ALARA deck")
    flux_defs = deck.get("fluxes", [])
    if len(flux_defs) != 1 or flux_defs[0].get("name") != ALARA_FLUX_NAME:
        raise ValueError("example requires exactly one neutron_flux definition")
    if flux_defs[0].get("file") != "flux.txt":
        raise ValueError("ALARA deck must reference the checked-in flux.txt fixture")
    material = translate_material(material_doc)
    flux = translate_flux(deck, parsed_flux, manifest, library_bounds(activation_library))
    schedule = translate_schedule(deck_text, deck)
    cooling_s = float(deck["cooling_times_s"][-1])
    elapsed_s = sum(float(step["dt"].split()[0]) for step in schedule)
    return {"material": material, "flux": flux, "schedule": schedule,
            "shutdown_t_s": elapsed_s - cooling_s, "cooling_time_s": cooling_s}


def validate_fixture(translated: dict[str, Any]) -> None:
    """Keep the runnable example equivalent to its independent literal control."""
    material = translated["material"]
    if (material["composition"] != {"FE56": 100.0}
            or material["mass_g"] != 15.748
            or material["density_g_cm3"] != 7.874
            or material["volume_cm3"] != 2.0
            or material["bounds_cm"] != [[0, 2], [0, 1], [0, 1]]):
        raise ValueError("example requires the supplied pure Fe56, 15.748 g, 2 cm3 fixture")
    expected_flux = [0.0] * 709
    expected_flux[620] = 1.0e12
    if translated["flux"]["flux_per_group"] != expected_flux:
        raise ValueError("example requires 1e12 n/cm2/s in ascending neutron group 620 only")
    if translated["schedule"] != [{"dt": "300 s", "flux": 1.0}, {"dt": "3600 s", "flux": 0.0}]:
        raise ValueError("example requires 300 s irradiation followed by 3600 s cooling")


def _call(command: list[str], *, timeout_s: float = 60.0) -> subprocess.CompletedProcess[str]:
    """Run one reviewed ACTINV command with a hard timeout and captured output."""
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=timeout_s, check=False)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"command timed out after {timeout_s:g}s: {' '.join(command)}") from exc


def _checked_call(command: list[str]) -> subprocess.CompletedProcess[str]:
    completed = _call(command)
    if completed.returncode:
        raise RuntimeError(f"command failed ({completed.returncode}): {' '.join(command)}\n{completed.stderr[-4000:]}")
    return completed


def _result_values(step: dict[str, Any], label: str) -> tuple[list[float], list[tuple[float, ...]], list[float], float, float]:
    """Require the full photon grid and a real Mn56 activation response."""
    try:
        photon = step["photon_source"]
        bounds = [float(value) for value in photon["boundaries_eV"]]
        groups = photon["groups"]
        grid = [tuple(float(group[key]) for key in ("low_eV", "high_eV", "centroid_eV"))
                for group in groups]
        strengths = [float(group["photons_s"]) for group in groups]
        total = _finite_positive(photon["total_photons_s"], f"{label} photon total")
        activity_values = [value for name, value in step["activity_Bq_per_g"].items()
                           if name.upper() == "MN56"]
        if len(activity_values) != 1:
            raise ValueError(f"{label} must contain exactly one Mn56 activity")
        activity = _finite_positive(activity_values[0], f"{label} Mn56 activity")
        _finite_positive(step["t_s"], f"{label} result time")
        _finite_positive(step["step"], f"{label} result step")
    except (KeyError, TypeError) as exc:
        raise ValueError(f"{label} is missing required photon, activity, or time fields") from exc
    if photon.get("group_structure") != "fispact-24" or len(groups) != 24 or len(bounds) != 25:
        raise ValueError(f"{label} must contain the complete fispact-24 photon grid")
    if (any(not math.isfinite(value) or value < 0.0 for value in bounds)
            or any(b <= a for a, b in zip(bounds, bounds[1:]))):
        raise ValueError(f"{label} photon boundaries must be finite and strictly ascending")
    if any(not math.isfinite(value) or value < 0.0 for value in strengths):
        raise ValueError(f"{label} photon group strengths must be finite and nonnegative")
    for i, (low, high, centroid) in enumerate(grid):
        # ACTINV writes centroid=0 for an empty group (photon.rs); it has no
        # source-weighted centroid even when the group's lower bound is >0.
        valid_centroid = low <= centroid <= high if strengths[i] > 0 else centroid == 0.0
        if low != bounds[i] or high != bounds[i + 1] or not math.isfinite(centroid) or not valid_centroid:
            raise ValueError(f"{label} photon group {i} has inconsistent bounds or centroid")
    if not math.isclose(sum(strengths), total, rel_tol=1e-12, abs_tol=1e-12):
        raise ValueError(f"{label} photon groups do not conserve its total")
    return bounds, grid, strengths, total, activity


def compare_results(direct_step: dict[str, Any], mesh_step: dict[str, Any]) -> dict[str, Any]:
    """Compare the fixed case's nominal outputs at its expected final time."""
    direct_bounds, direct_grid, direct_groups, direct_total, direct_activity = _result_values(direct_step, "direct result")
    mesh_bounds, mesh_grid, mesh_groups, mesh_total, mesh_activity = _result_values(mesh_step, "mesh result")
    if direct_bounds != mesh_bounds or direct_grid != mesh_grid:
        raise RuntimeError("direct and mesh photon grid metadata differ")
    if direct_step["step"] != mesh_step["step"] or direct_step["t_s"] != mesh_step["t_s"]:
        raise RuntimeError("direct and mesh result times differ")
    if direct_step["step"] != 2 or direct_step["t_s"] != 3900.0:
        raise ValueError("fixed example must finish at step 2, time 3900 s")
    if not math.isclose(direct_total, mesh_total, rel_tol=1e-9, abs_tol=1e-12):
        raise RuntimeError(f"direct and mesh photon totals differ: {direct_total} vs {mesh_total}")
    if any(not math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-12) for a, b in zip(direct_groups, mesh_groups)):
        raise RuntimeError("direct and mesh photon group strengths differ")
    if not math.isclose(direct_activity, mesh_activity, rel_tol=1e-9, abs_tol=1e-12):
        raise RuntimeError(f"direct and mesh Mn56 activities differ: {direct_activity} vs {mesh_activity}")
    return {
        "direct_photons_s": direct_total, "mesh_photons_s": mesh_total,
        "direct_photon_groups_s": direct_groups, "mesh_photon_groups_s": mesh_groups,
        "photon_group_structure": "fispact-24", "photon_boundaries_eV": direct_bounds,
        "max_group_relative_error": max((abs(a - b) / max(a, b) if max(a, b) > 0 else 0.0)
                                        for a, b in zip(direct_groups, mesh_groups)),
        "direct_mn56_activity_bq_g": direct_activity, "mesh_mn56_activity_bq_g": mesh_activity,
        "relative_error": abs(direct_total - mesh_total) / direct_total,
    }


def run_example(args: argparse.Namespace) -> dict[str, Any]:
    actinv = Path(args.actinv_bin).resolve()
    data_root = Path(args.data_root).resolve()
    input_dir = Path(args.inputs).resolve()
    output = Path(args.out).resolve()
    if not actinv.is_file():
        raise ValueError(f"ACTINV binary does not exist: {actinv}")
    if output.exists():
        raise ValueError(f"output directory must not already exist: {output}")

    capabilities = []
    for probe_args, capability in ((["export-r2s"], "export-r2s"),
                                   (["export-source", "alara"], "export-source alara")):
        probe = _call([str(actinv), *probe_args], timeout_s=10.0)
        if capability not in probe.stdout + probe.stderr:
            raise RuntimeError(
                f"ACTINV binary lacks required `{capability}` capability; use the pinned source build in README.md"
            )
        capabilities.append(capability)

    verified = _checked_call([str(actinv), "data", "verify", "tendl-2025-neutron", "--output", str(data_root)])
    verification = json.loads(verified.stdout)
    if verification.get("catalog_version") != "1.1.0":
        raise RuntimeError(f"example expects ACTINV data catalog 1.1.0, got {verification.get('catalog_version')!r}")
    artifacts = {item["id"]: item for item in verification.get("files", [])}
    required = {
        "tendl-2025-neutron-709g", "tendl-2025-neutron-709g-index",
        "endfb-viii-0-decay", "jeff-3-3-decay",
    }
    missing = required - artifacts.keys()
    if missing:
        raise RuntimeError(f"verified data bundle omitted required artifacts: {sorted(missing)}")
    for artifact in artifacts.values():
        path = Path(artifact["path"]).resolve()
        if not path.is_file() or sha256_file(path) != artifact["sha256"]:
            raise RuntimeError(f"ACTINV-verified artifact changed or disappeared: {path}")
    library = Path(artifacts["tendl-2025-neutron-709g"]["path"]).resolve()
    decay_primary = Path(artifacts["endfb-viii-0-decay"]["path"]).resolve()
    decay_fallback = Path(artifacts["jeff-3-3-decay"]["path"]).resolve()
    translated = load_nucleide_inputs(input_dir, library)
    validate_fixture(translated)
    output.mkdir(parents=True)
    mat = translated["material"]
    flux = translated["flux"]
    flux_path = output / "flux.ndjson"
    header = {
        "record": "header", "schema": "actinv-flux-1",
        "source": {"format": "nucleide-alara-default-flux", "path": str(input_dir / "flux.txt"),
                   "sha256": sha256_file(input_dir / "flux.txt"),
                   "normalization": {"declared_units": "n cm^-2 s^-1 per group", "deck_scalar_applied": flux["source_scale"],
                                    "transport_source_rate_conversion": "none; fixture is physical flux"}},
        "energy_boundaries_eV": flux["energy_boundaries_eV"],
        "flux_units": "n cm^-2 s^-1", "cell_count": 1,
        "geometry": {"kind": "rectilinear", "dimension": [1, 1, 1],
                     "axis_boundaries_cm": [list(axis) for axis in mat["bounds_cm"]]},
    }
    cell = {"record": "cell", "ordinal": 0, "id": "zone1", "index": [1, 1, 1],
            "bounds_cm": mat["bounds_cm"], "volume_cm3": mat["volume_cm3"],
            "flux_per_group": flux["flux_per_group"], "flux_total": flux["flux_total"],
            "relative_error": [0.0] * len(flux["flux_per_group"])}
    footer = {"record": "footer", "cell_count": 1,
              "flux_sum_over_cells": flux["flux_total"],
              "volume_integrated_flux": flux["flux_total"] * mat["volume_cm3"]}
    flux_path.write_text("\n".join(json.dumps(row, separators=(",", ":")) for row in (header, cell, footer)) + "\n")
    spec = {
        "spec": "actinv-mesh-spec-1", "title": "Nucleide to ACTINV one-zone R2S example",
        "projectile": "neutron", "library": {"path": str(library), "sha256": artifacts["tendl-2025-neutron-709g"]["sha256"]},
        "decay": {"primary": str(decay_primary), "fallback": str(decay_fallback)},
        "material": {"mass_g": mat["mass_g"], "basis": mat["basis"], "composition": mat["composition"]},
        "schedule": translated["schedule"],
        "options": {"mode": "auto", "prune": "rate", "outputs": ["photons"],
                    "bmin_atoms_per_g": 1e-12, "temperature_K": 293.6},
        "photon": {"group_structure": "fispact-24"},
        "threads": 1, "chunk_cells": 1,
        "flux": {"path": str(flux_path), "sha256": sha256_file(flux_path)},
        "uncertainty": {"channels": ["flux"], "responses": ["activity:Mn56"],
                        "require_complete": False},
        "cell_result_fields": ["mode", "steps", "ledger"],
    }
    spec_path = output / "mesh.json"
    spec_path.write_text(json.dumps(spec, indent=2) + "\n")
    result_path = output / "mesh_result.ndjson"
    started = time.monotonic()
    _checked_call([str(actinv), "mesh", str(spec_path), str(result_path)])
    if not result_path.is_file() or result_path.stat().st_size == 0:
        raise RuntimeError("ACTINV mesh reported success but produced no result")

    source_path = output / "r2s-source.ndjson"
    _checked_call([str(actinv), "export-r2s", str(result_path), str(len(translated["schedule"])), str(source_path)])
    source_header = json.loads(source_path.read_text().splitlines()[0])
    if source_header.get("mesh_result_sha256") != sha256_file(result_path):
        raise RuntimeError("R2S source is not bound to the freshly calculated mesh result")
    alara_dir = output / "photon-source"
    alara_dir.mkdir()
    _checked_call([str(actinv), "export-source", "alara", str(source_path), str(alara_dir),
                   "--shutdown-t-s", str(translated["shutdown_t_s"])])
    index_path = alara_dir / "actinv-alara-index.json"
    index = json.loads(index_path.read_text())
    if len(index.get("cells", [])) != 1 or index["cells"][0].get("id") != "zone1":
        raise RuntimeError("ACTINV ALARA exporter must return exactly cell zone1")
    cell_entry = index["cells"][0]
    photon_path = alara_dir / cell_entry["file"]
    if not photon_path.is_file():
        raise RuntimeError("ACTINV exporter did not create a photon source from this mesh result")

    alara, _, r2s = _nucleide()
    photon_text = photon_path.read_text()
    cooling_time_s = float(index["cooling_s"])
    if (cooling_time_s != translated["cooling_time_s"]
            or float(index["shutdown_t_s"]) != translated["shutdown_t_s"]):
        raise RuntimeError("ALARA exporter cooling/shutdown times differ from translated schedule")
    sums = r2s.photon_group_sums(photon_text, ["TOTAL"], cooling_time_s)
    density_strength = _finite_positive(alara.alara_photon_total_strength(photon_text), "Nucleide photon source density")
    if not math.isclose(sum(sums["sums"]), density_strength, rel_tol=1e-12, abs_tol=1e-12):
        raise RuntimeError("Nucleide group reader total disagrees with its ALARA photon reader")
    volume = _finite_positive(cell_entry["volume_cm3"], "exported cell volume")
    if volume != mat["volume_cm3"]:
        raise RuntimeError("exported photon-source volume differs from input zone")
    reconstructed = density_strength * volume
    declared = _finite_positive(cell_entry["photons_s"], "exported photon strength")
    relative_error = abs(reconstructed - declared) / declared
    tag = r2s.tag_zone_strength([reconstructed], [0], split=False)
    if not math.isclose(reconstructed, declared, rel_tol=1e-10, abs_tol=1e-12):
        raise RuntimeError(f"photon source density × volume fails conservation: {reconstructed} vs {declared}")
    if not math.isclose(float(tag["total"]), declared, rel_tol=1e-12, abs_tol=1e-12):
        raise RuntimeError("Nucleide zone tagging does not conserve the full-cell photon strength")

    # The literal control is independent of Nucleide fixtures and adapter files.
    direct_spec = direct_case.make_spec(library, decay_primary, decay_fallback)
    direct_spec_path = output / "direct-spec.json"
    direct_spec_path.write_text(json.dumps(direct_spec, indent=2) + "\n")
    direct_result_path = output / "direct-result.json"
    _checked_call([str(actinv), "run", str(direct_spec_path), str(direct_result_path)])
    direct_result = json.loads(direct_result_path.read_text())
    direct_step = direct_result["steps"][-1]

    mesh_records = [json.loads(line) for line in result_path.read_text().splitlines() if line.strip()]
    mesh_cell = next((record for record in mesh_records
                      if record.get("record") == "cell" and record.get("id") == "zone1"), None)
    if mesh_cell is None:
        raise RuntimeError("ACTINV mesh result is missing cell zone1")
    mesh_step = mesh_cell["result"]["steps"][-1]
    comparison = compare_results(direct_step, mesh_step)
    if not math.isclose(declared, comparison["mesh_photons_s"], rel_tol=1e-12):
        raise RuntimeError("exported photon source differs from the fresh mesh photon total")

    version_probe = _call([str(actinv), "--version"])
    version_text = version_probe.stdout.strip()
    if not version_text or "usage: actinv" in version_text.lower():
        version_text = "CLI has no --version command; identify by actinv_binary_sha256"
    report = {
        "schema": "actinv-nucleide-e2e-report-1",
        "actinv_version": version_text,
        "nucleide_version": importlib.metadata.version("nucleide"),
        "numpy_version": importlib.metadata.version("numpy"),
        "python_version": sys.version.split()[0],
        "actinv_binary_sha256": sha256_file(actinv), "actinv_capabilities": capabilities,
        "data_catalog_version": verification["catalog_version"],
        "verified_artifacts": {key: {"path": value["path"], "sha256": value["sha256"]}
                               for key, value in artifacts.items()},
        "nucleide_input_api": "alara_parse_deck + alara_parse_flux + alara_expand_schedule + material.mix_by_mass",
        "material_mass_g": mat["mass_g"], "density_g_cm3": mat["density_g_cm3"],
        "volume_cm3": volume, "schedule": translated["schedule"],
        "physical_flux_total_n_cm2_s": flux["flux_total"],
        "flux_deck_scale": flux["source_scale"],
        "input_sha256": {name: sha256_file(input_dir / name)
                         for name in ("material.json", "activation.alara", "flux.txt",
                                      "neutron_groups.json")},
        "physical_flux_populated_group_ascending": [i for i, value in enumerate(flux["flux_per_group"]) if value > 0],
        "photon_reader_group_count": len(sums["sums"]), "cooling_time_s": cooling_time_s,
        "declared_photons_s": declared, "reconstructed_density_times_volume_photons_s": reconstructed,
        "relative_conservation_error": relative_error,
        "nucleide_tag_zone_total_photons_s": tag["total"],
        "direct_comparison": comparison,
        "mesh_result_sha256": sha256_file(result_path), "photon_source_sha256": sha256_file(photon_path),
        "elapsed_s": time.monotonic() - started,
    }
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"fresh ACTINV mesh result: {result_path}")
    print(f"Nucleide read {len(sums['sums'])} photon groups at {cooling_time_s:g} s cooling")
    print(f"photon strength: {declared:.12g} photons/s; density×volume reconstruction: "
          f"{reconstructed:.12g}; relative error {relative_error:.3e}")
    print(f"software: ACTINV {report['actinv_version']}; Nucleide {report['nucleide_version']}")
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--actinv-bin", required=True,
                        help="ACTINV executable with mesh/export-r2s/export-source alara")
    parser.add_argument("--data-root", required=True,
                        help="ACTINV data directory passed to `actinv data verify`")
    parser.add_argument("--inputs", default=str(INPUTS), help="directory containing copies of the fixed example inputs")
    parser.add_argument("--out", required=True, help="new output directory (must not exist)")
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        run_example(_parser().parse_args(argv))
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"nucleide/ACTINV example failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
