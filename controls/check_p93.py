#!/usr/bin/env python3
"""P93 checker (protocols/ACTINV-P93_PROTOCOL.md): transport-tally statistical
error as a first-order flux uncertainty channel.

    python3 controls/check_p93.py build    # fmt/clippy/test/build -> target/p93/g1.json
    python3 controls/check_p93.py g2       # unchanged-behaviour gates -> target/p93/g2.json
    python3 controls/check_p93.py g3       # directional-derivative gate -> target/p93/g3.json
    python3 controls/check_p93.py g4       # real-tally sampling gate -> target/p93/g4.json
    python3 controls/check_p93.py verdict  # assembles G0-G5 -> results/p93_verdict.json

Run order: build, g2, g3, g4, then verdict (verdict also needs G5's CI replay
log, produced separately). g2's nominal-invariance part (c) and g3 share the
same 48-spec flux-only/no-uncertainty run pairs, cached under
target/p93/g3_runs/ so whichever of g2/g3 runs first does the work.

cargo commands run under the memory-capped systemd-run wrapper (see `build`);
actinv invocations do not need the cgroup and run with a modest worker pool.
"""
from __future__ import annotations

import concurrent.futures as cf
import hashlib
import json
import math
import os
import random
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = Path.home() / "Documents" / "actinv"
NUCLEAR_DATA = Path.home() / "nuclear-data"
WORK = ROOT / "target" / "p93"
REF = WORK / "ref_actinv"
CAND = ROOT / "target" / "release" / "actinv"
PROTOCOL = ROOT / "protocols" / "ACTINV-P93_PROTOCOL.md"
VERDICT = ROOT / "results" / "p93_verdict.json"

SPECS_DIR = MAIN / "target" / "p75b" / "specs"
MESH = ["fe_coupled", "fe_p21like", "ss316_r2s"]
MESH_DIR = MAIN / "target" / "meshprof"
EXAMPLES_UNCERTAINTY = [
    ROOT / "examples" / "optimize_ra_steel" / "base_spec.json",
    ROOT / "examples" / "optimize_ra_steel" / "opt_v2_winner.json",
    ROOT / "examples" / "p51_battery" / "corpus_probe.json",
]
TIMING_KEYS = {"ms", "elapsed_ms", "wall_s", "wall_time_s", "cells_per_s"}
WORKERS = 3

LIBRARY_V110 = {
    "path": str(Path.home() / "Documents" / "actinv" / "actinv-data" / "v1.1.0"
                / "activation" / "tendl-2025-neutron-709g.npz"),
    "sha256": "ec4c72bf598dc8ad3d533d9cfafdcf493e2d1f949a3e4db6251495659b68cc44",
}
P32_FLUX = NUCLEAR_DATA / "p32-work" / "chain" / "flux.ndjson"
P32_MESH_SPEC = NUCLEAR_DATA / "p32-work" / "chain" / "mesh_spec.json"
G3_SEED_BASE = 20260930
G3_H = 1e-4
G4_SEED = 20260930
G4_SAMPLES = 64

CG_BUILD = ["systemd-run", "--user", "--scope", "-q", "-p", "MemoryMax=6G",
            "-p", "MemorySwapMax=0", "-p", "TasksMax=128", "-p", "CPUQuota=300%", "--"]


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def env() -> dict:
    return {"RAYON_NUM_THREADS": "1", "PATH": "/usr/bin:/bin", "HOME": str(Path.home())}


def strip_timing(v, parent: str = ""):
    if isinstance(v, dict):
        return {k: strip_timing(x, k) for k, x in v.items()
                if k not in TIMING_KEYS and not k.endswith("_ms")
                and not (parent == "timing" and k.endswith("_s"))}
    if isinstance(v, list):
        return [strip_timing(x, parent) for x in v]
    return v


def run_to_doc(binary: Path, spec_path: Path, cwd: Path | None = None, timeout=600) -> dict:
    """`actinv run` a spec, return {"returncode", "doc" (stripped) or None, "stderr_tail"}."""
    with tempfile.TemporaryDirectory(dir=WORK) as d:
        out = Path(d) / "out.json"
        p = subprocess.run([str(binary), "run", str(spec_path), str(out)], cwd=cwd,
                           capture_output=True, text=True, env=env(), timeout=timeout)
        if p.returncode != 0 or not out.exists():
            return {"returncode": p.returncode, "doc": None, "stderr_tail": p.stderr[-500:]}
        doc = strip_timing(json.loads(out.read_text()))
        return {"returncode": 0, "doc": doc, "stderr_tail": ""}


def run_single_hash(binary: Path, spec_path: Path, cwd: Path | None = None) -> list:
    r = run_to_doc(binary, spec_path, cwd=cwd)
    if r["returncode"] != 0:
        return [r["returncode"], r["stderr_tail"]]
    return [0, hashlib.sha256(json.dumps(r["doc"], sort_keys=True).encode()).hexdigest()]


def run_mesh(binary: Path, spec_path: Path, out_path: Path, cwd: Path, timeout=1800) -> dict:
    t0 = time.monotonic()
    p = subprocess.run([str(binary), "mesh", str(spec_path), str(out_path)], cwd=cwd,
                       capture_output=True, text=True, env=env(), timeout=timeout)
    return {"returncode": p.returncode, "wall_s": time.monotonic() - t0, "stderr_tail": p.stderr[-500:]}


def mesh_digest(path: Path) -> dict:
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
    for key in ("wall_time_s", "cells_per_s"):
        footer.pop(key, None)
    return {"body_sha256": h.hexdigest(), "body_lines": lines, "footer": footer}


# ---------------------------------------------------------------------------
# build (G1)

BUILD_UNIT_TESTS = (
    "flux_direction_sum_reproduces_nominal_matrix_dense_709_group",
    "flux_direction_sum_reproduces_nominal_matrix_coarse_custom",
    "flux_tangent_matches_central_difference_of_plain_cram_steps",
    "low_burnup_relative_uncertainty_of_product_activity_equals_declared_error",
    "plain_spec_without_relative_error_names_the_spectrum",
    "mesh_cell_without_relative_error_names_the_cell",
    "plain_spec_relative_error_honours_descending_reorder",
    "descending_flag_maps_reported_group_index_to_the_declared_order",
    "covariance_is_required_unless_channels_is_exactly_flux",
    "spectrum_relative_error_length_must_match_flux_per_group",
    "spectrum_relative_error_must_be_finite_and_nonnegative",
)


def cmd_build() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    (WORK / "preflight-tmp").mkdir(parents=True, exist_ok=True)
    tmpdir = str(ROOT / "target" / "preflight-tmp")
    os.makedirs(tmpdir, exist_ok=True)
    base_env = dict(os.environ, CARGO_BUILD_JOBS="3", TMPDIR=tmpdir)
    steps = [
        ("fmt", ["cargo", "fmt", "--check"]),
        ("clippy", ["cargo", "clippy", "-p", "actinv-core", "-p", "actinv-data", "-p", "actinv-cli",
                    "--all-targets", "--", "-D", "warnings"]),
        ("test", ["cargo", "test", "--release", "-p", "actinv-core", "-p", "actinv-data"]),
        ("release", ["cargo", "build", "--release", "-p", "actinv-cli"]),
    ]
    build_log = []
    test_text = ""
    for name, cmd in steps:
        p = subprocess.run(CG_BUILD + cmd, cwd=ROOT, capture_output=True, text=True, env=base_env)
        build_log.append(f"=== {name} ===\n{p.stdout}\n{p.stderr}\n{name} rc={p.returncode}\n")
        if name == "test":
            test_text = p.stdout + p.stderr
        print(name, "rc=", p.returncode, flush=True)
        if p.returncode != 0 and name != "test":
            break
    (WORK / "build.log").write_text("\n".join(build_log))
    (WORK / "test.txt").write_text(test_text)
    rc = {}
    for name, _ in steps:
        m = re.search(rf"^{name} rc=(\d+)$", "\n".join(build_log), re.M)
        if m:
            rc[name] = m.group(1)
    unit = {name: re.search(rf"^test \S*{re.escape(name)} \.\.\. ok$", test_text, re.M) is not None
            for name in BUILD_UNIT_TESTS}
    g1 = {
        "rc": rc,
        "pass": all(rc.get(k) == "0" for k in ("fmt", "clippy", "test", "release")) and all(unit.values()),
        "unit_test_ok": unit,
        "candidate_sha256": sha(CAND) if CAND.exists() else None,
        "reference_sha256": sha(REF) if REF.exists() else None,
    }
    (WORK / "g1.json").write_text(json.dumps(g1, indent=1, sort_keys=True))
    print(json.dumps(g1, indent=1))


# ---------------------------------------------------------------------------
# Shared: the 48-spec G3/G2(c) set

G3_RUNS_DIR = WORK / "g3_runs"


def fispact709_boundaries() -> list:
    import numpy as np
    data = np.load(LIBRARY_V110["path"])
    bounds = [float(x) for x in data["bounds"]]
    if bounds[0] > bounds[-1]:
        bounds.reverse()
    assert len(bounds) == 710, f"expected 710 boundaries, got {len(bounds)}"
    return bounds


def build_g3_spec_set() -> dict:
    """Returns {name: spec_dict} for the 48-spec G3/G2(c) set, with
    spectrum.relative_error = 0.05 on every group with flux > 0, and
    uncertainty in flux-only mode requesting activity.total/heat.total."""
    all_specs = sorted(SPECS_DIR.glob("*.json"))
    base = all_specs[::20]
    assert len(base) == 40, f"expected 40 base specs, got {len(base)}"
    out = {}

    def with_flux_channel(spec: dict) -> dict:
        spec = json.loads(json.dumps(spec))
        flux = spec["spectrum"]["flux_per_group"]
        spec["spectrum"]["relative_error"] = [0.05 if v > 0.0 else 0.0 for v in flux]
        spec["uncertainty"] = {"channels": ["flux"], "responses": ["activity.total", "heat.total"]}
        return spec

    for path in base:
        spec = json.loads(path.read_text())
        out[path.stem] = with_flux_channel(spec)

    # The 5 "every 10th fispact-709 boundary, with the flux summed" custom-
    # structure variants are NOT built here: non-mesh runs require
    # flux_per_group.len() == library.group_count() exactly (empirically
    # confirmed: a genuinely coarser custom spec is refused with "spectrum
    # has N groups but prepared activation library has 709"), so they cannot
    # be expressed as single specs at all. See compute_g3_custom10_cases(),
    # which runs them through a one-cell mesh instead -- the only route that
    # both accepts a genuinely coarser source structure and exercises the
    # flux channel's source-group -> library-group rebin mapping.
    for path in base[:3]:
        spec = json.loads(path.read_text())
        flux = spec["spectrum"]["flux_per_group"]
        assert not spec["spectrum"].get("descending", False)
        spec["spectrum"] = dict(spec["spectrum"], flux_per_group=list(reversed(flux)), descending=True)
        out[f"{path.stem}.descending"] = with_flux_channel(spec)

    assert len(out) == 43, f"expected 43 single-spec G3 specs, got {len(out)}"
    return out


# ---------------------------------------------------------------------------
# G3 custom10 variants: one-cell mesh route (exercises the source-group ->
# library-group rebin mapping, which a same-709-group custom spec never
# touches -- see the comment in build_g3_spec_set()).

CUSTOM10_RUNS_DIR = WORK / "g3_custom10_runs"


def spectrum_absolute_flux(spectrum: dict) -> list:
    """Ascending, `total`-scaled absolute flux per group, mirroring
    Spectrum::ascending_flux in crates/actinv-core/src/spec.rs (self.total is
    applied to the group shape before anything else touches it)."""
    flux = list(spectrum["flux_per_group"])
    if spectrum.get("descending", False):
        flux = list(reversed(flux))
    total = spectrum.get("total")
    if total is not None:
        s = sum(flux)
        if s > 0.0:
            k = total / s
            flux = [v * k for v in flux]
    return flux


def absolute_flux_scale(spectrum: dict) -> float:
    """The factor Spectrum::ascending_flux applies to `flux_per_group` for `total` (1 without
    one), summing in ascending order as the Rust code does."""
    total = spectrum.get("total")
    if total is None:
        return 1.0
    flux = list(spectrum["flux_per_group"])
    if spectrum.get("descending", False):
        flux = list(reversed(flux))
    s = sum(flux)
    return total / s if s > 0.0 else 1.0


def coarse_group_indices() -> list:
    """Every 10th fispact-709 boundary index, always including both
    endpoints: 72 boundary indices -> 71 coarse groups (the last block is
    9-wide instead of 10-wide)."""
    idx = list(range(0, 710, 10))
    if idx[-1] != 709:
        idx.append(709)
    return idx


def write_one_cell_flux(boundaries: list, flux_per_group: list, relative_error: list, out_path: Path) -> str:
    """Writes a minimal valid actinv-flux-1 canonical file (one cell) and
    returns its sha256. Matches crates/actinv-core/src/flux.rs's FluxHeader/
    FluxCell/FluxFooter validation exactly: no geometry/volume (so the
    footer omits volume_integrated_flux, consistent with an all_volumes=false
    reader state), flux_total the group sum to well inside the 1e-12
    relative-difference check."""
    header = {
        "record": "header", "schema": "actinv-flux-1",
        "source": {"format": "synthetic-p93-g3-custom10", "path": str(out_path), "sha256": "0" * 64},
        "energy_boundaries_eV": boundaries, "flux_units": "n cm^-2 s^-1", "cell_count": 1,
    }
    flux_total = math.fsum(flux_per_group)
    cell = {"record": "cell", "ordinal": 0, "id": "0", "flux_per_group": flux_per_group,
            "relative_error": relative_error, "flux_total": flux_total}
    footer = {"record": "footer", "cell_count": 1, "flux_sum_over_cells": flux_total}
    with out_path.open("w") as f:
        for rec in (header, cell, footer):
            f.write(json.dumps(rec) + "\n")
    return sha(out_path)


def mesh_spec_from_single(base_spec: dict, flux_ref: dict, title: str, with_uncertainty: bool) -> dict:
    """A one-cell actinv-mesh-spec-1 carrying the same library/decay/
    material/schedule/options as `base_spec` (an actinv-spec-1 single spec),
    referencing `flux_ref` for its flux."""
    spec = {
        "spec": "actinv-mesh-spec-1", "title": title, "projectile": base_spec["projectile"],
        "library": base_spec["library"], "decay": base_spec["decay"], "material": base_spec["material"],
        "schedule": base_spec["schedule"], "options": base_spec["options"], "flux": flux_ref,
        "chunk_cells": 1, "threads": 1, "group_workloads": False,
        "cell_result_fields": ["steps", "mode", "pruned_states", "total_states"],
    }
    if with_uncertainty:
        spec["uncertainty"] = {"channels": ["flux"], "responses": ["activity.total", "heat.total"]}
    return spec


def run_mesh_cell_result(binary: Path, spec_path: Path, out_path: Path, cwd: Path, timeout=14400) -> dict:
    """Runs a one-cell mesh spec and returns {"returncode", "result" (the
    sole cell's stripped `result` object, or None), "stderr_tail"}."""
    r = run_mesh(binary, spec_path, out_path, cwd=cwd, timeout=timeout)
    result = None
    if r["returncode"] == 0 and out_path.exists():
        with out_path.open() as f:
            for line in f:
                rec = json.loads(line)
                if rec.get("record") == "cell":
                    result = strip_timing(rec.get("result"))
                    break
    out_path.unlink(missing_ok=True)
    return {"returncode": r["returncode"], "result": result, "stderr_tail": r["stderr_tail"]}


def run_g3_custom10_case(name: str, base_spec: dict, binary: Path = CAND) -> dict:
    """One custom10 G3/G2(c) variant: nominal flux-only + nominal plain (for
    G2(c) invariance and the FD baseline) + 3 seeded directions' (plus,
    minus) plain mesh runs (for G3), all through the one-cell mesh route,
    cached in CUSTOM10_RUNS_DIR/<name>.json keyed by the case's own content
    hash."""
    idx = coarse_group_indices()
    boundaries = [fispact709_boundaries()[i] for i in idx]
    abs_flux = spectrum_absolute_flux(base_spec["spectrum"])
    coarse_flux = [sum(abs_flux[idx[k]:idx[k + 1]]) for k in range(len(idx) - 1)]
    e = [0.05 if v > 0.0 else 0.0 for v in coarse_flux]

    cache_key = {"name": name, "boundaries": boundaries, "coarse_flux": coarse_flux, "e": e,
                 "material": base_spec["material"], "schedule": base_spec["schedule"],
                 "options": base_spec["options"], "library": base_spec["library"], "decay": base_spec["decay"]}
    cache_sha = hashlib.sha256(json.dumps(cache_key, sort_keys=True).encode()).hexdigest()
    CUSTOM10_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    cache = CUSTOM10_RUNS_DIR / f"{name}.json"
    if cache.exists():
        cached = json.loads(cache.read_text())
        if cached.get("cache_sha256") == cache_sha:
            return cached

    with tempfile.TemporaryDirectory(dir=WORK) as d:
        d = Path(d)
        nominal_flux_path = d / "nominal_flux.ndjson"
        nominal_sha = write_one_cell_flux(boundaries, coarse_flux, e, nominal_flux_path)
        nominal_flux_ref = {"path": str(nominal_flux_path), "sha256": nominal_sha}

        flux_only_spec = mesh_spec_from_single(base_spec, nominal_flux_ref, f"{name}.custom10.flux_only", True)
        plain_spec = mesh_spec_from_single(base_spec, nominal_flux_ref, f"{name}.custom10.plain", False)
        flux_only_path = d / "flux_only_mesh.json"
        plain_path = d / "plain_mesh.json"
        flux_only_path.write_text(json.dumps(flux_only_spec))
        plain_path.write_text(json.dumps(plain_spec))
        r_flux_only = run_mesh_cell_result(binary, flux_only_path, d / "flux_only_out.ndjson", cwd=d)
        r_plain = run_mesh_cell_result(binary, plain_path, d / "plain_out.ndjson", cwd=d)

        baseline = None
        if r_plain["result"] is not None:
            baseline = (r_plain["result"].get("mode"), r_plain["result"].get("pruned_states"),
                        r_plain["result"].get("total_states"))

        directions = []
        for direction in range(3):
            # Same stable-digest seeding convention as the single-spec route
            # (see build_g3_spec_set's sibling _g3_direction caller), keyed
            # on ".custom10" so it never collides with that spec's own
            # single-spec directions (base[:5] is also in the 43-spec set
            # when it isn't also chosen for the descending variant).
            name_hash = int(hashlib.sha256(f"{name}.custom10".encode()).hexdigest()[:8], 16)
            seed = G3_SEED_BASE + (name_hash % 1_000_003) + direction
            rng = random.Random(seed)
            z = [rng.gauss(0.0, 1.0) for _ in coarse_flux]
            plus = [v * (1.0 + G3_H * e[g] * z[g]) for g, v in enumerate(coarse_flux)]
            minus = [v * (1.0 - G3_H * e[g] * z[g]) for g, v in enumerate(coarse_flux)]
            plus_path = d / f"plus{direction}_flux.ndjson"
            minus_path = d / f"minus{direction}_flux.ndjson"
            plus_sha = write_one_cell_flux(boundaries, plus, e, plus_path)
            minus_sha = write_one_cell_flux(boundaries, minus, e, minus_path)
            plus_spec = mesh_spec_from_single(base_spec, {"path": str(plus_path), "sha256": plus_sha},
                                              f"{name}.custom10.plus{direction}", False)
            minus_spec = mesh_spec_from_single(base_spec, {"path": str(minus_path), "sha256": minus_sha},
                                               f"{name}.custom10.minus{direction}", False)
            plus_spec_path = d / f"plus{direction}_mesh.json"
            minus_spec_path = d / f"minus{direction}_mesh.json"
            plus_spec_path.write_text(json.dumps(plus_spec))
            minus_spec_path.write_text(json.dumps(minus_spec))
            r_plus = run_mesh_cell_result(binary, plus_spec_path, d / f"plus{direction}_out.ndjson", cwd=d)
            r_minus = run_mesh_cell_result(binary, minus_spec_path, d / f"minus{direction}_out.ndjson", cwd=d)
            directions.append({"direction": direction, "z": z,
                               "plus": {"returncode": r_plus["returncode"], "result": r_plus["result"]},
                               "minus": {"returncode": r_minus["returncode"], "result": r_minus["result"]}})

    result = {
        "cache_sha256": cache_sha, "route": "mesh_one_cell", "boundaries_eV": boundaries,
        "coarse_flux": coarse_flux, "relative_error": e, "baseline": baseline,
        "flux_only": {"returncode": r_flux_only["returncode"], "result": r_flux_only["result"]},
        "plain": {"returncode": r_plain["returncode"], "result": r_plain["result"]},
        "directions": directions,
    }
    cache.write_text(json.dumps(result))
    return result


def compute_g3_custom10_cases() -> dict:
    all_specs = sorted(SPECS_DIR.glob("*.json"))
    base = all_specs[::20]
    assert len(base) == 40, f"expected 40 base specs, got {len(base)}"
    chosen = base[:5]

    def one(path: Path):
        spec = json.loads(path.read_text())
        return path.stem, run_g3_custom10_case(path.stem, spec)

    out = {}
    t0 = time.monotonic()
    with cf.ThreadPoolExecutor(WORKERS) as ex:
        for i, (name, result) in enumerate(ex.map(one, chosen)):
            out[name] = result
            print(f"g3-custom10 {i + 1}/{len(chosen)} {name} "
                  f"flux_only_rc={result['flux_only']['returncode']} plain_rc={result['plain']['returncode']} "
                  f"{time.monotonic() - t0:.0f}s", flush=True)
    assert len(out) == 5, f"expected 5 custom10 mesh-route cases, got {len(out)}"
    return out


def custom10_g3_cases(name: str, data: dict) -> list:
    """Builds the same {"name","direction","excluded","comparisons":[...]}
    shape as the single-spec route's _g3_direction, from one
    run_g3_custom10_case result, so it slots directly into cmd_g3's
    aggregation."""
    cases = []
    flux_result = data["flux_only"]["result"]
    plain_result = data["plain"]["result"]
    # The cache round-trips through JSON, which turns the baseline tuple into a list; compare
    # as a tuple, the shape the per-direction documents are read into below.
    baseline = tuple(data["baseline"]) if data["baseline"] is not None else None
    if (data["flux_only"]["returncode"] != 0 or data["plain"]["returncode"] != 0
            or flux_result is None or plain_result is None or baseline is None):
        for d in range(3):
            cases.append({"name": f"{name}.custom10", "direction": d, "route": "mesh_one_cell",
                          "excluded": True, "reason": "run failed", "comparisons": []})
        return cases
    for entry in data["directions"]:
        direction, z = entry["direction"], entry["z"]
        case = {"name": f"{name}.custom10", "direction": direction, "route": "mesh_one_cell",
                "excluded": False, "comparisons": []}
        r_plus, r_minus = entry["plus"], entry["minus"]
        if r_plus["returncode"] != 0 or r_minus["returncode"] != 0 or r_plus["result"] is None or r_minus["result"] is None:
            case["excluded"] = True
            case["reason"] = "run failed"
            cases.append(case)
            continue
        doc_plus, doc_minus = r_plus["result"], r_minus["result"]
        if any((doc.get("mode"), doc.get("pruned_states"), doc.get("total_states")) != baseline
               for doc in (doc_plus, doc_minus)):
            case["excluded"] = True
            case["reason"] = "mode/pruned_states/total_states differs"
            cases.append(case)
            continue
        totals_plus = step_totals(doc_plus)
        totals_minus = step_totals(doc_minus)
        for step_index, step in enumerate(flux_result.get("steps", [])):
            responses = (step.get("uncertainty") or {}).get("responses") or {}
            for resp_name in ("activity.total", "heat.total"):
                resp = responses.get(resp_name)
                if resp is None:
                    continue
                sens_by_group = {p["parameter"]["group"]: p for p in resp.get("flux_sensitivities", [])}
                predicted = sum(p["value"] * p["parameter"]["standard_uncertainty_relative"] * z[g]
                                for g, p in sens_by_group.items())
                floor_sum = sum(abs(p["value"] * p["parameter"]["standard_uncertainty_relative"] * z[g])
                                for g, p in sens_by_group.items())
                r_plus_v = totals_plus.get(step_index, {}).get(resp_name)
                r_minus_v = totals_minus.get(step_index, {}).get(resp_name)
                if r_plus_v is None or r_minus_v is None:
                    continue
                fd = (r_plus_v - r_minus_v) / (2.0 * G3_H)
                nominal = resp.get("nominal")
                if nominal is None or nominal == 0.0:
                    continue
                tol = 1e-4 * floor_sum + 1e-10 * abs(nominal)
                diff = abs(predicted - fd)
                case["comparisons"].append({
                    "step": step_index, "response": resp_name,
                    "predicted": predicted, "central_difference": fd, "diff": diff,
                    "tolerance": tol, "ratio": diff / tol if tol > 0 else math.inf,
                    "ok": diff <= tol, "ok_100x": diff <= 100.0 * tol,
                })
        cases.append(case)
    return cases


def step_totals(doc: dict) -> dict:
    """{step_index: {"activity.total": v, "heat.total": v}} from a plain (no
    uncertainty) run's steps, matching the uncertainty responses' own
    selectors."""
    out = {}
    for i, step in enumerate(doc.get("steps", [])):
        act = step.get("activity_Bq_per_g")
        activity_total = sum(act.values()) if isinstance(act, dict) else act
        heat_total = (step.get("heat_W_per_g") or {}).get("total")
        out[i] = {"activity.total": activity_total, "heat.total": heat_total}
    return out


def run_g3_pair(name: str, spec: dict, binary: Path = CAND) -> dict:
    """Runs (nominal plain, flux-only) for one G3/G2(c) spec, cached in
    G3_RUNS_DIR/<name>.json keyed by the spec's own content hash."""
    G3_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    spec_sha = hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()
    cache = G3_RUNS_DIR / f"{name}.json"
    if cache.exists():
        cached = json.loads(cache.read_text())
        if cached.get("spec_sha256") == spec_sha:
            return cached
    plain = dict(spec)
    plain.pop("uncertainty", None)
    with tempfile.TemporaryDirectory(dir=WORK) as d:
        d = Path(d)
        plain_path = d / "plain.json"
        flux_path = d / "flux.json"
        plain_path.write_text(json.dumps(plain))
        flux_path.write_text(json.dumps(spec))
        r_plain = run_to_doc(binary, plain_path, timeout=900)
        r_flux = run_to_doc(binary, flux_path, timeout=14400)
    result = {
        "spec_sha256": spec_sha,
        "plain": {"returncode": r_plain["returncode"], "doc": r_plain["doc"]},
        "flux_only": {"returncode": r_flux["returncode"], "doc": r_flux["doc"]},
    }
    cache.write_text(json.dumps(result))
    return result


def compute_g3_pairs() -> dict:
    spec_set = build_g3_spec_set()
    pairs = {}

    def one(item):
        name, spec = item
        return name, run_g3_pair(name, spec)

    t0 = time.monotonic()
    with cf.ThreadPoolExecutor(WORKERS) as ex:
        for i, (name, result) in enumerate(ex.map(one, spec_set.items())):
            pairs[name] = result
            print(f"g3-pair {i + 1}/{len(spec_set)} {name} "
                  f"plain_rc={result['plain']['returncode']} flux_rc={result['flux_only']['returncode']} "
                  f"{time.monotonic() - t0:.0f}s", flush=True)
    return {"spec_set": spec_set, "pairs": pairs}


# ---------------------------------------------------------------------------
# g2

def cmd_g2() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    log = {"reference_sha256": sha(REF), "candidate_sha256": sha(CAND)}

    # (a) 783 P75b specs, bitwise, plus 3 mesh profiles at 1 thread.
    specs = sorted(SPECS_DIR.glob("*.json"))

    def both_single(spec_path: Path):
        return spec_path.name, run_single_hash(REF, spec_path), run_single_hash(CAND, spec_path)

    single = {}
    t0 = time.monotonic()
    with cf.ThreadPoolExecutor(WORKERS) as ex:
        for i, (name, r, c) in enumerate(ex.map(both_single, specs)):
            single[name] = {"ref": r, "cand": c}
            if i % 100 == 0:
                print(f"p75b {i}/{len(specs)} {time.monotonic() - t0:.0f}s", flush=True)
    print(f"p75b done {time.monotonic() - t0:.0f}s", flush=True)

    mesh_results = {}
    for name in MESH:
        spec = MESH_DIR / f"{name}.json"
        base = json.loads(spec.read_text())
        base["threads"] = 1
        variant = WORK / f"mesh_{name}.t1.json"
        variant.write_text(json.dumps(base))
        cell = {}
        for tag, binary in (("ref", REF), ("cand", CAND)):
            out = WORK / f"mesh_{name}.{tag}.ndjson"
            r = run_mesh(binary, variant, out, cwd=MAIN)
            if r["returncode"] == 0:
                r["digest"] = mesh_digest(out)
            out.unlink(missing_ok=True)
            cell[tag] = r
            print("mesh", name, tag, r["returncode"], f"{r['wall_s']:.1f}s", flush=True)
        mesh_results[name] = cell

    # (b) examples/ specs carrying uncertainty, bitwise.
    examples_results = {}
    for path in EXAMPLES_UNCERTAINTY:
        r = {"ref": run_single_hash(REF, path, cwd=ROOT), "cand": run_single_hash(CAND, path, cwd=ROOT)}
        examples_results[str(path.relative_to(ROOT))] = r
        print("examples", path.name, r["ref"][0], r["cand"][0], flush=True)

    # (c) nominal invariance on the G3 spec set (candidate only): 43 single
    # specs plus the 5 custom10 variants, which run through the one-cell
    # mesh route (see build_g3_spec_set / compute_g3_custom10_cases).
    g3_pairs = compute_g3_pairs()
    custom10 = compute_g3_custom10_cases()
    invariance = {}
    for name, result in g3_pairs["pairs"].items():
        plain_doc, flux_doc = result["plain"]["doc"], result["flux_only"]["doc"]
        entry = {"ok": plain_doc is not None and flux_doc is not None}
        if entry["ok"]:
            entry["bitwise_identical"] = (strip_flux_block(flux_doc) == strip_flux_block(plain_doc))
            entry.update(compare_invariance(plain_doc, flux_doc))
        invariance[name] = entry
    for name, data in custom10.items():
        plain_result, flux_result = data["plain"]["result"], data["flux_only"]["result"]
        entry = {"ok": plain_result is not None and flux_result is not None, "route": "mesh_one_cell",
                 "bitwise_identical": None}
        if entry["ok"]:
            entry.update(compare_invariance(plain_result, flux_result))
        invariance[f"{name}.custom10"] = entry
    (WORK / "g3_pairs_cache_used_by_g2.json").write_text(
        json.dumps({"spec_names": sorted(g3_pairs["spec_set"]),
                    "custom10_mesh_route_names": sorted(f"{n}.custom10" for n in custom10)}, indent=1))

    g2a_pass = (len(single) == len(specs)
                and all(v["ref"] == v["cand"] for v in single.values())
                and all(v["ref"][0] == 0 and v["cand"][0] == 0 for v in single.values())
                and all(mesh_results[n]["ref"].get("digest") == mesh_results[n]["cand"].get("digest")
                        and mesh_results[n]["ref"]["returncode"] == 0
                        for n in MESH))
    g2b_pass = all(v["ref"] == v["cand"] and v["ref"][0] == 0 for v in examples_results.values())
    g2c_pass = all(v["ok"] and v["within_tolerance"] for v in invariance.values())

    g2 = {
        "a": {"pass": g2a_pass, "p75b_count": len(single),
              "p75b_mismatches": [n for n, v in single.items() if v["ref"] != v["cand"]][:20],
              "mesh": {n: {"pass": mesh_results[n]["ref"].get("digest") == mesh_results[n]["cand"].get("digest")
                          and mesh_results[n]["ref"]["returncode"] == 0
                          and mesh_results[n]["cand"]["returncode"] == 0}
                      for n in MESH}},
        "b": {"pass": g2b_pass, "examples": {k: {"match": v["ref"] == v["cand"]} for k, v in examples_results.items()}},
        "c": {"pass": g2c_pass, "specs": len(invariance),
              "bitwise_identical_count": sum(1 for v in invariance.values() if v.get("bitwise_identical")),
              "failures": [n for n, v in invariance.items() if not (v["ok"] and v["within_tolerance"])][:20]},
    }
    g2["pass"] = g2a_pass and g2b_pass and g2c_pass
    (WORK / "g2.json").write_text(json.dumps(g2, indent=1, sort_keys=True))
    (WORK / "g2_raw.json").write_text(json.dumps(
        {"single_mismatches": {n: v for n, v in single.items() if v["ref"] != v["cand"]},
         "mesh": mesh_results, "examples": examples_results, "invariance": invariance},
        indent=1, sort_keys=True))
    print(json.dumps({"a": g2a_pass, "b": g2b_pass, "c": g2c_pass, "pass": g2["pass"]}, indent=1))


def strip_flux_block(doc: dict) -> dict:
    """Removes the flux channel's own contribution from a flux-only run's
    document so it can be compared against the plain (no-uncertainty) run's
    document for exact structural identity outside the uncertainty block."""
    doc = json.loads(json.dumps(doc))
    for step in doc.get("steps", []):
        step.pop("uncertainty", None)
    doc.pop("ledger", None)
    doc.pop("certificate", None)
    return doc


def compare_invariance(plain_doc: dict, flux_doc: dict) -> dict:
    """G2(c): inventory nuclides above 1e-12 of the step max, and the
    activity/heat totals, agree to relative 1e-12 between the flux-only run
    and the plain run."""
    tol = 1e-12
    mismatches = []
    plain_steps = plain_doc.get("steps", [])
    flux_steps = flux_doc.get("steps", [])
    if len(plain_steps) != len(flux_steps):
        return {"within_tolerance": False, "reason": "step count differs"}
    for i, (p, f) in enumerate(zip(plain_steps, flux_steps)):
        p_inv = {e["nuclide"]: e["atoms_per_g"] for e in p.get("inventory", [])}
        f_inv = {e["nuclide"]: e["atoms_per_g"] for e in f.get("inventory", [])}
        step_max = max((abs(v) for v in p_inv.values()), default=0.0)
        threshold = step_max * 1e-12
        names = {n for n, v in p_inv.items() if abs(v) > threshold} | \
                {n for n, v in f_inv.items() if abs(v) > threshold}
        for n in names:
            pv, fv = p_inv.get(n, 0.0), f_inv.get(n, 0.0)
            scale = max(abs(pv), abs(fv), 1e-300)
            if abs(pv - fv) > tol * scale:
                mismatches.append(f"step{i} inventory {n}: {pv} vs {fv}")
        p_act = p.get("activity_Bq_per_g")
        f_act = f.get("activity_Bq_per_g")
        p_act_total = sum(p_act.values()) if isinstance(p_act, dict) else p_act
        f_act_total = sum(f_act.values()) if isinstance(f_act, dict) else f_act
        if p_act_total is not None and f_act_total is not None:
            scale = max(abs(p_act_total), abs(f_act_total), 1e-300)
            if abs(p_act_total - f_act_total) > tol * scale:
                mismatches.append(f"step{i} activity.total: {p_act_total} vs {f_act_total}")
        p_heat = (p.get("heat_W_per_g") or {}).get("total")
        f_heat = (f.get("heat_W_per_g") or {}).get("total")
        if p_heat is not None and f_heat is not None:
            scale = max(abs(p_heat), abs(f_heat), 1e-300)
            if abs(p_heat - f_heat) > tol * scale:
                mismatches.append(f"step{i} heat.total: {p_heat} vs {f_heat}")
    return {"within_tolerance": not mismatches, "mismatches": mismatches[:20]}


# ---------------------------------------------------------------------------
# g3

def cmd_g3() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    g3_data = compute_g3_pairs()
    spec_set, pairs = g3_data["spec_set"], g3_data["pairs"]
    custom10 = compute_g3_custom10_cases()

    cases = []
    excluded = 0
    total_cases = 0
    for name, data in custom10.items():
        for case in custom10_g3_cases(name, data):
            cases.append(case)
            total_cases += 1
            if case["excluded"]:
                excluded += 1
    with cf.ThreadPoolExecutor(WORKERS) as ex:
        futures = {}
        for name, spec in spec_set.items():
            pair = pairs[name]
            plain_doc, flux_doc = pair["plain"]["doc"], pair["flux_only"]["doc"]
            if plain_doc is None or flux_doc is None:
                excluded += 3
                total_cases += 3
                continue
            flux = spec["spectrum"]["flux_per_group"]
            e = spec["spectrum"]["relative_error"]
            baseline = (plain_doc["mode"], plain_doc["pruned_states"], plain_doc["total_states"])
            for direction in range(3):
                total_cases += 1
                # Python's built-in hash() is salted per process (not
                # reproducible across runs); a stable digest keeps the seed
                # deterministic run to run, as "seeded" requires.
                name_hash = int(hashlib.sha256(name.encode()).hexdigest()[:8], 16)
                seed = G3_SEED_BASE + (name_hash % 1_000_003) + direction
                rng = random.Random(seed)
                z = [rng.gauss(0.0, 1.0) for _ in flux]
                # The protocol perturbs the absolute group flux phi_g (after `total` scaling,
                # the flux channel's own parameter). Leaving `total` in the perturbed spec would
                # renormalize the perturbation away, so the perturbed runs carry the absolute
                # flux (scaled exactly as Spectrum::ascending_flux does) and no `total`.
                k = absolute_flux_scale(spec["spectrum"])
                plus = [v * k * (1.0 + G3_H * e[g] * z[g]) for g, v in enumerate(flux)]
                minus = [v * k * (1.0 - G3_H * e[g] * z[g]) for g, v in enumerate(flux)]
                spec_plus = json.loads(json.dumps(spec))
                spec_plus.pop("uncertainty", None)
                spec_plus["spectrum"]["flux_per_group"] = plus
                spec_plus["spectrum"].pop("total", None)
                spec_minus = json.loads(json.dumps(spec))
                spec_minus.pop("uncertainty", None)
                spec_minus["spectrum"]["flux_per_group"] = minus
                spec_minus["spectrum"].pop("total", None)
                fut = ex.submit(_g3_direction, name, direction, spec_plus, spec_minus,
                                baseline, flux_doc, z, e)
                futures[fut] = (name, direction)
        t0 = time.monotonic()
        for i, fut in enumerate(cf.as_completed(futures)):
            name, direction = futures[fut]
            case = fut.result()
            cases.append(case)
            if case["excluded"]:
                excluded += 1
            if i % 20 == 0:
                print(f"g3-direction {i + 1}/{len(futures)} {time.monotonic() - t0:.0f}s", flush=True)

    exclusion_rate = excluded / total_cases if total_cases else 1.0
    comparisons = [c for case in cases if not case["excluded"] for c in case["comparisons"]]
    within = [c for c in comparisons if c["ok"]]
    within_100x = [c for c in comparisons if c["ok_100x"]]
    pass_rate = len(within) / len(comparisons) if comparisons else 0.0
    g3 = {
        "pass": (exclusion_rate <= 0.05 and pass_rate >= 0.99
                 and len(within_100x) == len(comparisons)),
        "total_cases": total_cases,
        "excluded": excluded,
        "exclusion_rate": exclusion_rate,
        "comparisons": len(comparisons),
        "within_tolerance": len(within),
        "pass_rate": pass_rate,
        "within_100x_tolerance": len(within_100x),
        "worst": sorted(comparisons, key=lambda c: -c["ratio"])[:10] if comparisons else [],
        "custom10_mesh_route": {
            "note": ("The 5 custom-structure variants (every 10th fispact-709 boundary, flux "
                     "summed into each block) run through a one-cell mesh, not a single actinv "
                     "run: single specs must declare exactly the activation library's group count "
                     "(709) even for structure=\"custom\" -- confirmed empirically, a genuinely "
                     "71-group custom spec is refused with 'spectrum has 71 groups but prepared "
                     "activation library has 709' -- so a source structure coarser than the "
                     "library cannot be expressed as a single spec at all. The one-cell mesh route "
                     "accepts a genuinely coarser source (a 71-group flux file) and is rebinned "
                     "onto the library's 709 groups by rebin_equal_lethargy, so it is also the only "
                     "way to exercise the flux channel's source-group -> library-group rebin "
                     "mapping in the sensitivity directions; a same-709-group custom spec never "
                     "touches that mapping."),
            "specs": sorted(custom10.keys()),
            "cases": sum(1 for c in cases if c.get("route") == "mesh_one_cell"),
            "excluded": sum(1 for c in cases if c.get("route") == "mesh_one_cell" and c["excluded"]),
        },
        "descending_variant_specs": sorted(name for name in spec_set if name.endswith(".descending")),
    }
    (WORK / "g3.json").write_text(json.dumps(g3, indent=1, sort_keys=True))
    (WORK / "g3_cases.json").write_text(json.dumps(cases, indent=1, sort_keys=True))
    print(json.dumps({k: v for k, v in g3.items() if k != "worst"}, indent=1))


def _g3_direction(name, direction, spec_plus, spec_minus, baseline, flux_doc, z, e):
    with tempfile.TemporaryDirectory(dir=WORK) as d:
        d = Path(d)
        pp = d / "plus.json"
        pm = d / "minus.json"
        pp.write_text(json.dumps(spec_plus))
        pm.write_text(json.dumps(spec_minus))
        r_plus = run_to_doc(CAND, pp, timeout=900)
        r_minus = run_to_doc(CAND, pm, timeout=900)
    case = {"name": name, "direction": direction, "excluded": False, "comparisons": []}
    if r_plus["returncode"] != 0 or r_minus["returncode"] != 0 or r_plus["doc"] is None or r_minus["doc"] is None:
        case["excluded"] = True
        case["reason"] = "run failed"
        return case
    doc_plus, doc_minus = r_plus["doc"], r_minus["doc"]
    for doc in (doc_plus, doc_minus):
        if (doc["mode"], doc["pruned_states"], doc["total_states"]) != baseline:
            case["excluded"] = True
            case["reason"] = "mode/pruned_states/total_states differs"
            return case
    totals_plus = step_totals(doc_plus)
    totals_minus = step_totals(doc_minus)
    for step_index, step in enumerate(flux_doc.get("steps", [])):
        responses = (step.get("uncertainty") or {}).get("responses") or {}
        for resp_name in ("activity.total", "heat.total"):
            resp = responses.get(resp_name)
            if resp is None:
                continue
            sens_by_group = {p["parameter"]["group"]: p for p in resp.get("flux_sensitivities", [])}
            predicted = sum(p["value"] * p["parameter"]["standard_uncertainty_relative"] * z[g]
                            for g, p in sens_by_group.items())
            floor_sum = sum(abs(p["value"] * p["parameter"]["standard_uncertainty_relative"] * z[g])
                            for g, p in sens_by_group.items())
            r_plus_v = totals_plus.get(step_index, {}).get(resp_name)
            r_minus_v = totals_minus.get(step_index, {}).get(resp_name)
            if r_plus_v is None or r_minus_v is None:
                continue
            fd = (r_plus_v - r_minus_v) / (2.0 * G3_H)
            nominal = resp.get("nominal")
            if nominal is None or nominal == 0.0:
                continue
            tol = 1e-4 * floor_sum + 1e-10 * abs(nominal)
            diff = abs(predicted - fd)
            case["comparisons"].append({
                "step": step_index, "response": resp_name,
                "predicted": predicted, "central_difference": fd, "diff": diff,
                "tolerance": tol, "ratio": diff / tol if tol > 0 else math.inf,
                "ok": diff <= tol, "ok_100x": diff <= 100.0 * tol,
            })
    return case


# ---------------------------------------------------------------------------
# g4

def load_p32_mesh_spec() -> dict:
    spec = json.loads(P32_MESH_SPEC.read_text())
    spec["library"] = dict(LIBRARY_V110)
    spec["flux"] = {"path": str(P32_FLUX), "sha256": sha(P32_FLUX)}
    spec["cell_result_fields"] = ["steps"]
    return spec


def load_flux_cells() -> list:
    cells = []
    with P32_FLUX.open() as f:
        for line in f:
            rec = json.loads(line)
            if rec.get("record") == "cell":
                cells.append(rec)
    return cells


def write_perturbed_flux(cells: list, header_lines: list, footer_line: str, seed: int, out_path: Path) -> None:
    rng = random.Random(seed)
    with out_path.open("w") as f:
        for line in header_lines:
            f.write(line)
        total = 0.0
        for cell in cells:
            new = list(cell["flux_per_group"])
            errors = cell.get("relative_error") or [0.0] * len(new)
            for g, v in enumerate(new):
                e = errors[g] if g < len(errors) else 0.0
                if v > 0.0 and e > 0.0:
                    s2 = math.log(1.0 + e * e)
                    new[g] = v * math.exp(-0.5 * s2 + math.sqrt(s2) * rng.gauss(0.0, 1.0))
            rec = dict(cell)
            rec["flux_per_group"] = new
            rec["flux_total"] = sum(new)
            total += rec["flux_total"]
            f.write(json.dumps(rec) + "\n")
        footer = json.loads(footer_line)
        footer["flux_sum_over_cells"] = total
        if "volume_integrated_flux" in footer:
            footer["volume_integrated_flux"] = total
        f.write(json.dumps(footer) + "\n")


def mesh_activities(result_path: Path) -> dict:
    per = {}
    with result_path.open() as f:
        for line in f:
            rec = json.loads(line)
            if rec.get("record") != "cell":
                continue
            steps = (rec.get("result") or {}).get("steps") or []
            vals = {}
            for i, st in enumerate(steps):
                act = st.get("activity_Bq_per_g")
                heat = (st.get("heat_W_per_g") or {}).get("total")
                vals[i] = {
                    "activity.total": sum(act.values()) if isinstance(act, dict) else act,
                    "heat.total": heat,
                }
            per[rec["ordinal"]] = vals
    return per


def mesh_flux_channel_variance(result_path: Path) -> dict:
    """{cell_ordinal: {step: {response: combined_standard_uncertainty}}}"""
    per = {}
    with result_path.open() as f:
        for line in f:
            rec = json.loads(line)
            if rec.get("record") != "cell":
                continue
            steps = (rec.get("result") or {}).get("steps") or []
            vals = {}
            for i, st in enumerate(steps):
                responses = (st.get("uncertainty") or {}).get("responses") or {}
                vals[i] = {name: (responses.get(name) or {}).get("combined_standard_uncertainty")
                          for name in ("activity.total", "heat.total")}
            per[rec["ordinal"]] = vals
    return per


def cmd_g4() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    g4_work = WORK / "g4"
    g4_work.mkdir(parents=True, exist_ok=True)

    lines = P32_FLUX.read_text().splitlines(keepends=True)
    header_lines = [ln for ln in lines if json.loads(ln).get("record") == "header"]
    footer_line = next(ln for ln in lines if json.loads(ln).get("record") == "footer")
    cells = load_flux_cells()

    # Flux-only mesh run (candidate).
    flux_only_spec = load_p32_mesh_spec()
    flux_only_spec["uncertainty"] = {"channels": ["flux"],
                                     "responses": ["activity.total", "heat.total"]}
    flux_only_spec_path = g4_work / "flux_only_mesh_spec.json"
    flux_only_spec_path.write_text(json.dumps(flux_only_spec))
    flux_only_out = g4_work / "flux_only_result.ndjson"
    t0 = time.monotonic()
    r_flux_only = run_mesh(CAND, flux_only_spec_path, flux_only_out, cwd=g4_work, timeout=3600)
    print("flux-only mesh", r_flux_only["returncode"], f"{r_flux_only['wall_s']:.1f}s", flush=True)
    first_order = mesh_flux_channel_variance(flux_only_out) if r_flux_only["returncode"] == 0 else {}
    nominal_from_flux_only = {}
    if r_flux_only["returncode"] == 0:
        nominal_from_flux_only = mesh_activities(flux_only_out)
    flux_only_out.unlink(missing_ok=True)

    # Nominal (no-uncertainty) mesh run, for timing comparison.
    nominal_spec = load_p32_mesh_spec()
    nominal_spec_path = g4_work / "nominal_mesh_spec.json"
    nominal_spec_path.write_text(json.dumps(nominal_spec))
    nominal_out = g4_work / "nominal_result.ndjson"
    r_nominal = run_mesh(CAND, nominal_spec_path, nominal_out, cwd=g4_work, timeout=1800)
    print("nominal mesh", r_nominal["returncode"], f"{r_nominal['wall_s']:.1f}s", flush=True)
    nominal_out.unlink(missing_ok=True)

    # K perturbed samples, plain (no uncertainty), candidate.
    def one_sample(i):
        flux_path = g4_work / f"flux_pert{i}.ndjson"
        write_perturbed_flux(cells, header_lines, footer_line, G4_SEED + i, flux_path)
        spec = load_p32_mesh_spec()
        spec["flux"] = {"path": str(flux_path), "sha256": sha(flux_path)}
        spec_path = g4_work / f"mesh_pert{i}.json"
        spec_path.write_text(json.dumps(spec))
        out_path = g4_work / f"mesh_pert{i}.ndjson"
        r = run_mesh(CAND, spec_path, out_path, cwd=g4_work, timeout=1800)
        acts = mesh_activities(out_path) if r["returncode"] == 0 else {}
        for p in (flux_path, spec_path, out_path):
            p.unlink(missing_ok=True)
        return i, r["returncode"], acts

    samples = {}
    t0 = time.monotonic()
    with cf.ThreadPoolExecutor(WORKERS) as ex:
        for i, (idx, rc, acts) in enumerate(ex.map(one_sample, range(G4_SAMPLES))):
            samples[idx] = {"returncode": rc, "activities": acts}
            if i % 8 == 0:
                print(f"g4-sample {i + 1}/{G4_SAMPLES} {time.monotonic() - t0:.0f}s", flush=True)
    sample_wall_s = time.monotonic() - t0

    failed_samples = [i for i, s in samples.items() if s["returncode"] != 0]
    ratios = []
    per_cell = {}
    for cell_ordinal, steps in first_order.items():
        per_cell[cell_ordinal] = {}
        for step_index, responses in steps.items():
            per_cell[cell_ordinal][step_index] = {}
            for resp_name, fo_sigma in responses.items():
                if fo_sigma is None or fo_sigma <= 0.0:
                    continue
                vals = []
                for i, s in samples.items():
                    if s["returncode"] != 0:
                        continue
                    v = s["activities"].get(cell_ordinal, {}).get(step_index, {}).get(resp_name)
                    if v is not None:
                        vals.append(v)
                if len(vals) < 2:
                    continue
                mu = sum(vals) / len(vals)
                sample_var = sum((v - mu) ** 2 for v in vals) / (len(vals) - 1)
                fo_var = fo_sigma * fo_sigma
                ratio = sample_var / fo_var if fo_var > 0 else None
                sd_ratio = math.sqrt(sample_var) / fo_sigma
                entry = {"sample_var": sample_var, "first_order_var": fo_var,
                        "variance_ratio": ratio, "std_ratio": sd_ratio, "n": len(vals)}
                per_cell[cell_ordinal][step_index][resp_name] = entry
                ratios.append({"cell": cell_ordinal, "step": step_index, "response": resp_name, **entry})

    by_step_response = {}
    for r in ratios:
        key = (r["step"], r["response"])
        by_step_response.setdefault(key, []).append(r)
    summary = {}
    for (step_index, resp_name), rs in by_step_response.items():
        mean_var_ratio = sum(r["variance_ratio"] for r in rs) / len(rs)
        in_band = sum(1 for r in rs if 0.75 <= r["std_ratio"] <= 1.33)
        summary[f"step{step_index}:{resp_name}"] = {
            "mean_variance_ratio": mean_var_ratio,
            "mean_variance_ratio_pass": 0.90 <= mean_var_ratio <= 1.10,
            "cells": len(rs),
            "std_ratio_in_band": in_band,
            "std_ratio_in_band_fraction": in_band / len(rs) if rs else 0.0,
            "std_ratio_pass": (in_band / len(rs)) >= 0.90 if rs else False,
        }

    g4 = {
        "pass": bool(summary) and all(v["mean_variance_ratio_pass"] and v["std_ratio_pass"]
                                      for v in summary.values()) and not failed_samples
                and r_flux_only["returncode"] == 0,
        "flux_only_mesh": {"returncode": r_flux_only["returncode"], "wall_s": r_flux_only["wall_s"]},
        "nominal_mesh": {"returncode": r_nominal["returncode"], "wall_s": r_nominal["wall_s"]},
        "samples": G4_SAMPLES, "seed": G4_SEED, "sample_wall_s_total": sample_wall_s,
        "failed_samples": failed_samples,
        "summary": summary,
    }
    (WORK / "g4.json").write_text(json.dumps(g4, indent=1, sort_keys=True))
    (WORK / "g4_per_cell.json").write_text(json.dumps(per_cell, indent=1, sort_keys=True))
    print(json.dumps({"pass": g4["pass"], "summary": summary,
                      "flux_only_wall_s": r_flux_only["wall_s"], "nominal_wall_s": r_nominal["wall_s"]},
                     indent=1))


# ---------------------------------------------------------------------------
# verdict (assembled here; NOT run by the implementer — Connor runs it after
# the CI replay / G5)

def cmd_verdict() -> int:
    registered = f"{sha(PROTOCOL)}  protocols/ACTINV-P93_PROTOCOL.md" in \
        (ROOT / "protocols/protocol_hash.txt").read_text()
    g1 = json.loads((WORK / "g1.json").read_text())
    g2 = json.loads((WORK / "g2.json").read_text())
    g3 = json.loads((WORK / "g3.json").read_text())
    g4 = json.loads((WORK / "g4.json").read_text())
    ci_log = WORK / "ci_replay_summary.log"
    steps = re.findall(r"^STEP (\d+) (\S+)$", ci_log.read_text(), re.M) if ci_log.exists() else []
    failed = [name for code, name in steps if code != "0"]
    g5 = {"pass": bool(steps) and not failed, "steps": len(steps), "failed": failed}
    verdict = {
        "protocol": "ACTINV-P93",
        "inputs": {"protocol_sha256": sha(PROTOCOL), "reference_sha256": g1.get("reference_sha256"),
                  "candidate_sha256": g1.get("candidate_sha256")},
        "G0": {"pass": registered},
        "G1": g1,
        "G2": g2,
        "G3": g3,
        "G4": g4,
        "G5": g5,
        "pass": registered and g1["pass"] and g2["pass"] and g3["pass"] and g4["pass"] and g5["pass"],
    }
    VERDICT.write_text(json.dumps(verdict, indent=1, sort_keys=True) + "\n")
    print(json.dumps({k: (v.get("pass") if isinstance(v, dict) else v) for k, v in verdict.items()}, indent=1))
    return 0 if verdict["pass"] else 1


if __name__ == "__main__":
    fn = {"build": cmd_build, "g2": cmd_g2, "g3": cmd_g3, "g4": cmd_g4, "verdict": cmd_verdict}
    if len(sys.argv) < 2 or sys.argv[1] not in fn:
        sys.exit(__doc__)
    sys.exit(fn[sys.argv[1]]())
