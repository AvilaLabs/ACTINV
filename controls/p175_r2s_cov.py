#!/usr/bin/env python3
"""P175 harness (protocols/ACTINV-P175_PROTOCOL.md): the first-order shutdown-dose source covariance of
`actinv export-r2s-cov` against brute-force sampling.

For each case of controls/p175_stats.py the harness
  1. builds a four-cell flux file (the FNS iron spectrum of examples/export_r2s at four amplitudes, with tally errors),
  2. runs the nominal mesh, picks the nuclides to band, runs the banded mesh and exports the covariance
     (`actinv export-r2s-cov`, and `actinv export-r2s-joint` where the protocol asks for it) at steps 2 and 3,
  3. draws N = 200 samples and re-solves the mesh for each: cross sections and decay constants by the factors
     `actinv study` draws for `study.robustness` (rob_<i>.json), applied as options.rate_scale and decay_scale; flux by
     independent mean-preserving lognormal factors per (cell, group), s^2 = ln(1 + e^2) (the P93 convention),
  4. stores the sampled photon sources S_i[c][g] and the covariance documents (gzip) under results/p175/ and
     writes results/p175_r2s_cov.json, which controls/check_p175.py judges.
No sampled quantity is judged here. Run inside the bounded cgroup scope of AGENTS.md, one job at a time.
Data tree: ACTINV_DATA (default <repo>/actinv-data), linked into the study stage as `actinv-data`.

Usage: ACTINV_BIN=target/release/actinv p175_r2s_cov.py [--run-dir DIR] [--record FILE] [--cases NAME,...]
                                                         [--resume] [--generate-only]
"""
import argparse
import gzip
import hashlib
import json
import math
import os
import platform
import random
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "controls"))
import p175_stats as st  # noqa: E402

RECORD = ROOT / "results" / "p175_r2s_cov.json"
ARTIFACTS = ROOT / "results" / "p175"
RUN_DIR = ROOT / "target" / "p175-run"
if st.REHEARSAL:
    RECORD = ROOT / "target" / "p175-rehearsal" / "p175_r2s_cov.json"
    ARTIFACTS = ROOT / "target" / "p175-rehearsal" / "p175"
    RUN_DIR = ROOT / "target" / "p175-rehearsal" / "run"
BASE_FLUX = ROOT / "examples" / "export_r2s" / "mesh_flux_boxes.ndjson"
FNS_SPEC = ROOT / "examples" / "fns_fe_5min.json"

PINS = {
    "base_flux_sha256": "b9b1e8d1a713292507611b17be5afaaa671d734383cf7a0b1d1a68b31311b7e7",
    "fns_spec_sha256": "ed55ed00f6c00fc4a2351e18332e98e7feb33d9b7ca65e7ba861e0e5e8feef39",
    "library_sha256": "ec4c72bf598dc8ad3d533d9cfafdcf493e2d1f949a3e4db6251495659b68cc44",
    "covariance_sha256": "c19dec86b44ad5d90b66c9ab94d53e18641a1d354a89402a4da7986b6c530cde",
}
LIBRARY = {"path": "catalog:tendl-2025-neutron-709g", "sha256": PINS["library_sha256"]}
COVARIANCE = {"path": "catalog:tendl-2025-neutron-709g-covariance", "sha256": PINS["covariance_sha256"]}
DECAY = {"primary": "catalog:endfb-viii-0-decay", "fallback": "catalog:jeff-3-3-decay"}
OPTIONS = {"mode": "auto", "prune": "rate", "bmin_atoms_per_g": 1e-08, "temperature_K": 293.6}
SCHEDULE = [{"dt": "300 s", "flux": 1.0}, {"dt": "86400 s", "flux": 0.0}, {"dt": "2592000 s", "flux": 0.0},
            {"dt": "31536000 s", "flux": 0.0}]
SAMPLE_FIELDS = ["mode", "pruned_states", "steps.t_s", "steps.photon_source.groups"]
MESH_TIMEOUT_S = 1800
STUDY_TIMEOUT_S = 3600
STDERR_TAIL = 1500


# ---- plumbing -----------------------------------------------------------------------------------------------------

def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=60).stdout.strip()


def cgroup_record():
    try:
        relative = Path("/proc/self/cgroup").read_text().strip().split("::", 1)[1]
    except (OSError, IndexError):
        return {"path": None}
    base = Path("/sys/fs/cgroup") / relative.lstrip("/")
    record = {"path": relative}
    for name in ("memory.max", "memory.swap.max", "pids.max", "cpu.max"):
        try:
            record[name] = (base / name).read_text().strip()
        except OSError:
            record[name] = None
    return record


def run_child(argv, cwd, timeout):
    """One serial child in its own process group, killed and reaped at the timeout. Returns a record."""
    started = time.perf_counter()
    timed_out = False
    with tempfile.TemporaryFile() as stderr:
        proc = subprocess.Popen([str(a) for a in argv], cwd=cwd, stdout=subprocess.DEVNULL, stderr=stderr,
                                start_new_session=True)
        try:
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
        finally:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
        stderr.seek(0, os.SEEK_END)
        stderr.seek(max(0, stderr.tell() - STDERR_TAIL))
        tail = stderr.read().decode("utf-8", "replace")
    return {"argv": [str(a) for a in argv], "rc": proc.returncode, "timed_out": timed_out,
            "wall_s": round(time.perf_counter() - started, 3), "stderr_tail": tail}


def ok(child):
    return child["rc"] == 0 and not child["timed_out"]


def write_json(path, doc):
    Path(path).write_text(json.dumps(doc, indent=1) + "\n")


def write_gz(path, text):
    with open(path, "wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as stream:
            stream.write(text.encode("utf-8"))


# ---- inputs -------------------------------------------------------------------------------------------------------

def read_ndjson(path):
    with open(path, encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def base_flux_cells():
    """Four cells: the cell-a spectrum of the export_r2s example at the registered amplitudes, with tally errors."""
    if sha256(BASE_FLUX) != PINS["base_flux_sha256"]:
        raise SystemExit("examples/export_r2s/mesh_flux_boxes.ndjson is not the pinned file")
    records = read_ndjson(BASE_FLUX)
    header, cell_a = records[0], records[1]
    cells = []
    for c, (amp, err) in enumerate(zip(st.FLUX_AMPLITUDE, st.FLUX_ERROR)):
        flux = [phi * amp for phi in cell_a["flux_per_group"]]
        cells.append({"id": st.CELL_IDS[c], "bounds": [[float(c), float(c + 1)], [0.0, 1.0], [0.0, 1.0]],
                      "flux": flux, "err": [err if phi > 0 else 0.0 for phi in flux]})
    return header, cells


def write_flux(path, header, cells, with_errors):
    head = {k: v for k, v in header.items() if k != "record"}
    head = {"record": "header", **head, "cell_count": len(cells)}
    lines = [json.dumps(head)]
    total_all = 0.0
    for ordinal, cell in enumerate(cells):
        total = math.fsum(cell["flux"])
        total_all += total
        rec = {"record": "cell", "ordinal": ordinal, "id": cell["id"], "bounds_cm": cell["bounds"], "volume_cm3": 1.0,
               "flux_per_group": cell["flux"], "flux_total": total}
        if with_errors:
            rec["relative_error"] = cell["err"]
        lines.append(json.dumps(rec))
    lines.append(json.dumps({"record": "footer", "cell_count": len(cells), "flux_sum_over_cells": total_all,
                             "volume_integrated_flux": total_all}))
    Path(path).write_text("\n".join(lines) + "\n")
    return sha256(path)


def draw_flux(cells, seed, index):
    """One sample of the flux channel: independent mean-preserving lognormal factors, s^2 = ln(1 + e^2)."""
    rng = random.Random(seed * 1_000_003 + index)
    out = []
    for cell in cells:
        flux = []
        for phi, err in zip(cell["flux"], cell["err"]):
            if phi > 0 and err > 0:
                s2 = math.log1p(err * err)
                flux.append(phi * math.exp(-0.5 * s2 + math.sqrt(s2) * rng.gauss(0.0, 1.0)))
            else:
                flux.append(phi)
        out.append(dict(cell, flux=flux))
    return out


def mesh_spec(material, flux_path, flux_sha, *, uncertainty=None, options=None, fields=False):
    spec = {"spec": "actinv-mesh-spec-1", "title": "P175 " + material, "projectile": "neutron", "library": LIBRARY,
            "decay": DECAY,
            "material": {"mass_g": 1.0, "basis": "wt_percent", "composition": st.COMPOSITION[material]},
            "schedule": SCHEDULE, "options": dict(OPTIONS, **(options or {})),
            "flux": {"path": str(flux_path), "sha256": flux_sha}, "threads": 1}
    if uncertainty:
        spec["uncertainty"] = uncertainty
    if fields:
        spec["cell_result_fields"] = SAMPLE_FIELDS
    return spec


def uncertainty_block(channels, responses):
    block = {"channels": list(channels), "responses": [f"activity:{name}" for name in responses],
             "confidence_level": 0.95}
    if "cross_section_mf33" in channels:
        block["covariance"] = COVARIANCE
    return block


# ---- mesh output --------------------------------------------------------------------------------------------------

def cell_records(path):
    records = read_ndjson(path)
    if records[0].get("record") != "header" or records[-1].get("record") != "footer":
        raise ValueError(f"{path} is not a complete mesh result")
    return records[0], [r for r in records if r.get("record") == "cell"]


def step_record(cell, step):
    return next(s for s in cell["result"]["steps"] if s["step"] == step)


def photon_groups(cell, step):
    return step_record(cell, step)["photon_source"]["groups"]


def extract_sources(path, expect_cells):
    """S[step][cell][g] and the photon group bounds of a mesh result."""
    _, cells = cell_records(path)
    if len(cells) != expect_cells:
        raise ValueError(f"{len(cells)} cells, expected {expect_cells}")
    out, bounds = {}, None
    for step in st.STEPS:
        rows = []
        for cell in cells:
            groups = photon_groups(cell, step)
            b = [g["low_eV"] for g in groups] + [groups[-1]["high_eV"]]
            if bounds is None:
                bounds = b
            elif b != bounds:
                raise ValueError("photon group bounds differ between cells or steps")
            rows.append([g.get("photons_s", 0.0) for g in groups])
        out[step] = rows
    pruned = [cell["result"].get("pruned_states") for cell in cells]
    return out, bounds, pruned


def choose_bands(nominal_path, n_cells):
    """Nuclides to band: per (cell, step) in descending photon strength until the remainder is below
    BAND_REMAINDER of that region's source; the union, strongest first, at most BAND_CAP."""
    _, cells = cell_records(nominal_path)
    share = {}
    for cell in cells:
        for step in st.STEPS:
            entries = step_record(cell, step)["photon_source"].get("by_nuclide") or []
            strength = sorted(((sum(g.get("photons_s", 0.0) for g in e["groups"]), e["nuclide"]) for e in entries),
                              reverse=True)
            total = math.fsum(s for s, _ in strength)
            if not total > 0:
                continue
            left = total
            for s, name in strength:
                if left <= st.BAND_REMAINDER * total:
                    break
                share[name] = max(share.get(name, 0.0), s / total)
                left -= s
    ranked = sorted(share, key=lambda n: (-share[n], n))
    return ranked[:st.BAND_CAP], len(ranked) > st.BAND_CAP


# ---- the study sampler --------------------------------------------------------------------------------------------

def find_key(root, key):
    for path in sorted(Path(root).rglob("*.json")):
        if path.name.startswith("rob_") or path.stat().st_size > 64 << 20:
            continue
        try:
            doc = json.loads(path.read_text())
        except ValueError:
            continue
        stack = [doc]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                if item.get(key) is not None:
                    return item[key]
                stack.extend(item.values())
            elif isinstance(item, list):
                stack.extend(item)
    return None


def run_study(material, stage, channels, binary):
    """`actinv study run` with study.robustness: its draws of the MF=33 and decay factors, one rob_<i>.json each."""
    stage.mkdir(parents=True, exist_ok=True)
    if sha256(FNS_SPEC) != PINS["fns_spec_sha256"]:
        raise SystemExit("examples/fns_fe_5min.json is not the pinned file")
    shutil.copyfile(FNS_SPEC, stage / "fns_fe_5min.json")
    data = Path(os.environ.get("ACTINV_DATA", ROOT / "actinv-data")).resolve()
    if not data.is_dir():
        raise SystemExit(f"data tree {data} not found (set ACTINV_DATA)")
    link = stage / "actinv-data"
    if not link.is_symlink():
        link.symlink_to(data)
    robustness = {"samples": st.N_SAMPLES, "seed": st.STUDY_SEED[material],
                  "channels": {"cross_section_mf33": "cross_section_mf33" in channels,
                               "decay_constants": "decay_constants" in channels},
                  "responses": ["total_activity_bq_per_g"], "first_order_comparison": False}
    if "cross_section_mf33" in channels:
        robustness["covariance"] = COVARIANCE
    study = {"study": "actinv-study-1", "study_id": f"p175-{material}", "library": LIBRARY, "decay": DECAY,
             "options": OPTIONS,
             "cases": {"materials": [{"name": material, "mass_g": 1.0, "composition": st.COMPOSITION[material]}],
                       "spectra": [{"name": "fns", "spec_ref": "fns_fe_5min.json"}],
                       "schedules": [{"name": "irr", "steps": SCHEDULE}]},
            "responses": ["total_activity_bq_per_g"], "robustness": robustness}
    write_json(stage / "study.json", study)
    out = stage / "study_out"
    if out.exists():
        shutil.rmtree(out)
    child = run_child([binary, "study", "run", "study.json", "study_out"], stage, STUDY_TIMEOUT_S)
    if not ok(child):
        return child, None, None
    case_dir = out / "cases" / f"{material}__fns__irr"
    blocks = []
    for i in range(st.N_SAMPLES):
        rob = json.loads((case_dir / f"rob_{i}.json").read_text())
        options = rob.get("options") or {}
        blocks.append({name: {k: float(v) for k, v in (options.get(name) or {}).items()}
                       for name in ("rate_scale", "decay_scale", "yield_scale")})
    info = {"seed": st.STUDY_SEED[material], "channels": sorted(channels),
            "xs_clipped": find_key(out, "negative_eigenvalue_mass_clipped_relative"),
            "rate_rows": len({k for b in blocks for k in b["rate_scale"]}),
            "decay_nuclides": len({k for b in blocks for k in b["decay_scale"]}),
            "yield_pairs": len({k for b in blocks for k in b["yield_scale"]})}
    return child, blocks, info


# ---- one case -----------------------------------------------------------------------------------------------------

def export_cov(binary, banded_out, banded_spec, step, out, cwd):
    return run_child([binary, "export-r2s-cov", banded_out, banded_spec, step, out], cwd, MESH_TIMEOUT_S)


def read_joint(path):
    records = read_ndjson(path)
    corr = next(r for r in records if r.get("record") == "correlation")
    footer = next(r for r in records if r.get("record") == "footer")
    return {"cell_sigma_correlated": corr["cell_sigma_correlated"], "rho": corr["rho"],
            "sigma_total_correlated": footer["sigma_total_correlated"]}


def run_case(case, ctx):
    binary, run_dir, header, base_cells = ctx["binary"], ctx["run_dir"], ctx["header"], ctx["cells"]
    material, name = case["material"], case["name"]
    channels = list(case["channels"])
    work = run_dir / name
    work.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    record = {"name": name, "material": material, "channels": channels, "substituted": False, "failures": [],
              "joint": {}, "artifacts": {"cov": {}}}

    nominal_flux = work / "flux_nominal.ndjson"
    flux_sha = write_flux(nominal_flux, header, base_cells, with_errors=True)
    record["flux_nominal_sha256"] = flux_sha

    # nominal mesh: the nuclides to band (once per material)
    bands_file = run_dir / f"bands_{material}.json"
    if not bands_file.is_file():
        nominal_dir = run_dir / f"nominal_{material}"
        nominal_dir.mkdir(parents=True, exist_ok=True)
        spec_path = nominal_dir / "spec.json"
        write_json(spec_path, mesh_spec(material, nominal_flux, flux_sha))
        child = run_child([binary, "mesh", spec_path, nominal_dir / "out.ndjson"], ROOT, MESH_TIMEOUT_S)
        if not ok(child):
            record["failures"].append({"stage": "nominal mesh", **child})
            return record
        names, capped = choose_bands(nominal_dir / "out.ndjson", len(base_cells))
        write_json(bands_file, {"nuclides": names, "cap_hit": capped})
    bands = json.loads(bands_file.read_text())
    record["responses"] = bands["nuclides"]
    record["band_cap_hit"] = bands["cap_hit"]

    # banded mesh and the exports; tungsten drops MF=33 if the export finds no usable block
    def banded(channels_now):
        spec = mesh_spec(material, nominal_flux, flux_sha,
                         uncertainty=uncertainty_block(channels_now, bands["nuclides"]))
        spec_path = work / "banded_spec.json"
        write_json(spec_path, spec)
        child = run_child([binary, "mesh", spec_path, work / "banded.ndjson"], ROOT, MESH_TIMEOUT_S)
        return spec_path, child

    spec_path, child = banded(channels)
    if not ok(child):
        record["failures"].append({"stage": "banded mesh", **child})
        return record
    cov_files = {}
    for step in st.STEPS:
        cov_files[step] = work / f"cov_step{step}.ndjson"
        child = export_cov(binary, work / "banded.ndjson", spec_path, str(step), cov_files[step], ROOT)
        if not ok(child):
            record["failures"].append({"stage": f"export-r2s-cov step {step}", **child})
            return record
    if material == "tungsten" and "cross_section_mf33" in channels:
        cov = st.parse_cov(st.read_text(cov_files[st.STEPS[0]]))
        detail = st.mf33_detail(cov) or {}
        record["mf33_rank"] = detail.get("rank")
        if not detail.get("rank"):
            channels = list(st.D_FALLBACK_CHANNELS)
            record.update(channels=channels, substituted=True)
            spec_path, child = banded(channels)
            if not ok(child):
                record["failures"].append({"stage": "banded mesh (fallback)", **child})
                return record
            for step in st.STEPS:
                child = export_cov(binary, work / "banded.ndjson", spec_path, str(step), cov_files[step], ROOT)
                if not ok(child):
                    record["failures"].append({"stage": f"export-r2s-cov step {step} (fallback)", **child})
                    return record
    if case["joint"] and "cross_section_mf33" in channels:
        for step in st.STEPS:
            joint_out = work / f"joint_step{step}.ndjson"
            child = run_child([binary, "export-r2s-joint", work / "banded.ndjson", spec_path, str(step), joint_out],
                              ROOT, MESH_TIMEOUT_S)
            if not ok(child):
                record["failures"].append({"stage": f"export-r2s-joint step {step}", **child})
                return record
            record["joint"][str(step)] = read_joint(joint_out)
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    for step in st.STEPS:
        target = ARTIFACTS / f"{name}_step{step}_cov.ndjson.gz"
        write_gz(target, cov_files[step].read_text())
        record["artifacts"]["cov"][str(step)] = {"path": str(target.relative_to(ROOT)), "sha256": sha256(target)}

    # sampler draws of the nuclear-data factors (shared by the steel cases)
    nuclear = [c for c in channels if c in ("cross_section_mf33", "decay_constants")]
    blocks, info = None, None
    if nuclear:
        study_key = f"study_{material}_{'+'.join(nuclear)}"
        cache = run_dir / f"{study_key}.json"
        if cache.is_file():
            cached = json.loads(cache.read_text())
            blocks, info = cached["blocks"], cached["info"]
        else:
            child, blocks, info = run_study(material, run_dir / study_key, nuclear, binary)
            if blocks is None:
                record["failures"].append({"stage": "study", **child})
                return record
            write_json(cache, {"blocks": blocks, "info": info})
        record["sampler"] = info

    # the sampled reruns
    _, nominal_cells = cell_records(work / "banded.ndjson")
    nominal_sources, bounds, nominal_pruned = extract_sources(work / "banded.ndjson", len(base_cells))
    samples = {str(step): [] for step in st.STEPS}
    pruned_changed = 0
    flux_dir = work / "flux"
    flux_dir.mkdir(exist_ok=True)
    for i in range(st.N_SAMPLES):
        options = {}
        if blocks is not None:
            options = {k: v for k, v in blocks[i].items() if v}
        if case["flux"]:
            sample_flux = flux_dir / f"flux_{i}.ndjson"
            sample_sha = write_flux(sample_flux, header, draw_flux(base_cells, st.FLUX_SEED[material], i),
                                    with_errors=False)
        else:
            sample_flux, sample_sha = nominal_flux, flux_sha
        sample_spec = work / "sample_spec.json"
        write_json(sample_spec, mesh_spec(material, sample_flux, sample_sha, options=options, fields=True))
        sample_out = work / "sample_out.ndjson"
        child = run_child([binary, "mesh", sample_spec, sample_out], ROOT, MESH_TIMEOUT_S)
        if case["flux"]:
            sample_flux.unlink()
        if not ok(child):
            record["failures"].append({"stage": f"sample {i}", **child})
            return record
        try:
            sources, sample_bounds, pruned = extract_sources(sample_out, len(base_cells))
        except (OSError, ValueError, KeyError, StopIteration) as error:
            record["failures"].append({"stage": f"sample {i} unreadable", "error": repr(error)})
            return record
        if sample_bounds != bounds:
            record["failures"].append({"stage": f"sample {i}", "error": "photon group bounds changed"})
            return record
        pruned_changed += pruned != nominal_pruned
        for step in st.STEPS:
            samples[str(step)].append(sources[step])
        if (i + 1) % 25 == 0:
            print(f"{name}: {i + 1}/{st.N_SAMPLES} samples", flush=True)
    sample_path = ARTIFACTS / f"{name}_samples.json.gz"
    document = {"schema": st.SAMPLES_SCHEMA, "case": name, "n": st.N_SAMPLES,
                "cells": [c["id"] for c in nominal_cells], "group_bounds_eV": bounds,
                "nominal": {str(step): nominal_sources[step] for step in st.STEPS}, "samples": samples}
    write_gz(sample_path, json.dumps(document, separators=(",", ":")))
    record["artifacts"]["samples"] = {"path": str(sample_path.relative_to(ROOT)), "sha256": sha256(sample_path)}
    record["pruned_states_changed_samples"] = pruned_changed
    record["wall_s"] = round(time.perf_counter() - started, 1)
    print(f"{name}: done in {record['wall_s']} s", flush=True)
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, default=RUN_DIR)
    parser.add_argument("--record", type=Path, default=RECORD)
    parser.add_argument("--cases", help="comma-separated case names (default: all; a partial record fails the checker)")
    parser.add_argument("--resume", action="store_true", help="reuse the cached nominal bands and study draws")
    parser.add_argument("--generate-only", action="store_true", help="write the flux files and stop")
    args = parser.parse_args()
    if not os.environ.get("ACTINV_BIN"):
        sys.exit("ACTINV_BIN is required")
    binary = Path(os.environ["ACTINV_BIN"]).resolve()
    if not binary.is_file():
        sys.exit(f"ACTINV_BIN {binary} is not a file")
    selected = args.cases.split(",") if args.cases else [case["name"] for case in st.CASES]
    unknown = [n for n in selected if n not in st.BY_NAME]
    if unknown:
        sys.exit(f"unknown cases {unknown}")
    run_dir = args.run_dir.resolve()
    if not args.resume and run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    header, cells = base_flux_cells()
    if args.generate_only:
        write_flux(run_dir / "flux_nominal.ndjson", header, cells, with_errors=True)
        print(f"inputs written under {run_dir}")
        return
    status = git("status", "--porcelain", "--untracked-files=no")
    environment = {"binary_sha256": sha256(binary), "git_head": git("rev-parse", "HEAD"),
                   "crates_tree": git("rev-parse", "HEAD:crates"), "clean_tree": status == "",
                   "cgroup": cgroup_record(), "python": platform.python_version(), "platform": platform.platform(),
                   "pins": PINS, "options": OPTIONS, "schedule": SCHEDULE}
    ctx = {"binary": binary, "run_dir": run_dir, "header": header, "cells": cells}
    records = [run_case(st.BY_NAME[n], ctx) for n in selected]
    document = {"schema": st.SCHEMA, "protocol": "ACTINV-P175", "environment": environment,
                "constants": st.constants(), "cases": records}
    args.record.write_text(json.dumps(document, indent=1) + "\n")
    print(f"wrote {args.record}")


if __name__ == "__main__":
    main()
