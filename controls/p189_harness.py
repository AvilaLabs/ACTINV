#!/usr/bin/env python3
"""P189 harness (protocols/ACTINV-P189_PROTOCOL.md): ACTINV's activation step on a 1,000,000-element mesh.

  python controls/p189_harness.py coarse      OpenMC 16^3 709-group flux tally of P181's model -> WORK/coarse.npz
                                              (run with the OpenMC 0.15.3 env, OPENMC_CROSS_SECTIONS set)
  python controls/p189_harness.py fine        100^3 flux file by trilinear interpolation of the coarse tally,
                                              the material of each element by radius, and the mesh spec
  python controls/p189_harness.py run         `actinv mesh` on it (ACTINV_BIN), bounded, with a heartbeat
  python controls/p189_harness.py sample      100 random elements re-solved alone with `actinv run`, compared
  python controls/p189_harness.py assemble    results/p189_record.json

P189_REHEARSAL=1 shrinks the mesh to 20^3 and moves every output but the coarse tally to target/p189-rehearsal.
Run fine, run, sample and assemble inside the bounded scope (6 GiB, no swap, 128 tasks, 4 CPUs).

The coarse tally is an input: the fine mesh's fluxes are interpolated from it, so they are realistic in shape and
magnitude without a million-element transport solution (OpenMC's 709-group tally of a million elements does not fit
the 6 GiB limit).
"""
import hashlib
import json
import math
import os
import platform
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "controls"))
WORK = ROOT / "target" / "p189-work"
GROUPS_JSON = ROOT / "crates" / "actinv-data" / "data" / "fispact_709_groups.json"
COARSE = WORK / "coarse.npz"
COARSE_DIM = 16
COARSE_PARTICLES = 100_000
COARSE_BATCHES = 10
COARSE_SEED = 189
SOURCE_RATE = 1.0e17  # n/s, P181's irradiation source rate

FINE_DIM = 100
REHEARSAL = os.environ.get("P189_REHEARSAL") == "1"
if REHEARSAL:
    FINE_DIM = 20
    WORK = ROOT / "target" / "p189-rehearsal"
RECORD = WORK / "p189_record.json" if REHEARSAL else ROOT / "results" / "p189_record.json"
PROTOCOL = ROOT / "protocols" / "ACTINV-P189_PROTOCOL.md"
CHECKER = ROOT / "controls" / "check_p189.py"
SCHEMA = "actinv-p189-record-1"
R_W, R_CU, R_WATER = 4.0, 5.5, 6.5  # P181's shell radii, cm
MATERIALS = {
    "W": (19.25, {"W": 100.0}),
    "CuCrZr": (8.90, {"CU": 98.9, "CR": 0.8, "ZR": 0.3}),
    "water": (1.0, {"H": 11.19, "O": 88.81}),
    "SS316": (7.93, {"FE": 65.06, "CR": 17.5, "NI": 12.25, "MO": 2.5, "MN": 1.8, "SI": 0.5, "CO": 0.05,
                     "NB": 0.01, "TA": 0.01, "CU": 0.3, "C": 0.03}),
}
SCHEDULE = [{"dt": "31557600.0 s", "flux": 1.0}, {"dt": "1000000.0 s", "flux": 0.0}]
OPTIONS = {"mode": "auto", "prune": "rate", "outputs": ["ledger", "photons"], "temperature_K": 293.6}
LIBRARY = {"path": "catalog:tendl-2025-neutron-709g",
           "sha256": "ec4c72bf598dc8ad3d533d9cfafdcf493e2d1f949a3e4db6251495659b68cc44"}
DECAY = {"primary": "catalog:endfb-viii-0-decay", "fallback": "catalog:jeff-3-3-decay"}
CELL_RESULT_FIELDS = ["mode", "steps.photon_source.groups"]
THREADS = 4
RUN_LIMIT_S = 24 * 3600
HEARTBEAT_S = 30.0
SAMPLE_N = 100
SAMPLE_SEED = 189
SAMPLE_TIMEOUT_S = 600


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def coarse():
    """OpenMC flux per coarse element and library group, in n/cm^2/s at SOURCE_RATE, on P181's model."""
    import numpy as np
    import openmc
    import p181_harness as p181

    WORK.mkdir(parents=True, exist_ok=True)
    bounds = np.sort(np.asarray(json.loads(GROUPS_JSON.read_text())["boundaries_eV"], dtype=float))
    model, mesh, _ = p181.build_model(COARSE_PARTICLES)
    model.settings.batches = COARSE_BATCHES
    model.settings.seed = COARSE_SEED
    mesh.dimension = (COARSE_DIM,) * 3
    tally = openmc.Tally(name="p189 coarse flux")
    tally.filters = [openmc.MeshFilter(mesh), openmc.EnergyFilter(bounds), openmc.ParticleFilter(["neutron"])]
    tally.scores = ["flux"]
    model.tallies = [tally]
    run_dir = WORK / "coarse-run"
    run_dir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    statepoint = model.run(cwd=run_dir, threads=4, output=False)
    with openmc.StatePoint(statepoint) as sp:
        result = sp.get_tally(name="p189 coarse flux")
        mean = np.asarray(result.mean, dtype=float).reshape(COARSE_DIM ** 3, len(bounds) - 1)
        rel = np.asarray(result.std_dev, dtype=float).reshape(mean.shape)
    volume = (2.0 * p181.CUBE_HALF / COARSE_DIM) ** 3
    flux = mean / volume * SOURCE_RATE  # MeshFilter orders x fastest
    with np.errstate(divide="ignore", invalid="ignore"):
        rel = np.where(mean > 0.0, rel / mean, 0.0)
    np.savez(COARSE, flux=flux, relative_error=rel, boundaries_eV=bounds, dim=COARSE_DIM,
             cube_half_cm=p181.CUBE_HALF, source_rate=SOURCE_RATE)
    print(json.dumps({"coarse": str(COARSE), "sha256": sha256_file(COARSE), "wall_s": time.monotonic() - started,
                      "openmc": openmc.__version__, "zero_bins": int((flux <= 0.0).sum()), "bins": int(flux.size)}))
    return 0


def write_json(path, document):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(document, indent=1, sort_keys=True) + "\n")
    os.replace(tmp, path)


def now_utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def git_text(*args):
    p = subprocess.run(["git", *args], capture_output=True, text=True, cwd=ROOT, timeout=60)
    return p.stdout.strip()


def material_name(radius):
    if radius < R_W:
        return "W"
    if radius < R_CU:
        return "CuCrZr"
    return "water" if radius < R_WATER else "SS316"


def material_spec(name, volume):
    density, composition = MATERIALS[name]
    return {"mass_g": volume * density, "basis": "wt_percent", "composition": dict(composition),
            "density_g_cm3": density}


def axis_weights(centres, coarse_centres):
    """Per fine centre: lower coarse index and the weight of the upper neighbour, clamped to the outermost centres."""
    import numpy as np

    h = coarse_centres[1] - coarse_centres[0]
    position = np.clip((centres - coarse_centres[0]) / h, 0.0, len(coarse_centres) - 1.0)
    lower = np.minimum(np.floor(position).astype(int), len(coarse_centres) - 2)
    return lower, position - lower


def fine():
    """WORK/flux.ndjson (actinv-flux-1), WORK/mesh_spec.json and WORK/fine.json."""
    import numpy as np

    with np.load(COARSE) as z:
        coarse_flux, bounds = z["flux"], z["boundaries_eV"]
        dim, half = int(z["dim"]), float(z["cube_half_cm"])
    groups = len(bounds) - 1
    if coarse_flux.shape != (dim ** 3, groups):
        raise SystemExit(f"p189_harness: coarse flux has shape {coarse_flux.shape}")
    cube = coarse_flux.reshape(dim, dim, dim, groups)  # [z, y, x, group]
    n = FINE_DIM
    step = 2.0 * half / n
    edges = -half + step * np.arange(n + 1)
    centres = -half + step * (np.arange(n) + 0.5)
    coarse_centres = -half + (2.0 * half / dim) * (np.arange(dim) + 0.5)
    lower, upper = axis_weights(centres, coarse_centres)
    volume = step ** 3
    bounds_list = [float(b) for b in bounds]
    WORK.mkdir(parents=True, exist_ok=True)
    flux_path = WORK / "flux.ndjson"
    header = {
        "record": "header", "schema": "actinv-flux-1",
        "source": {"format": "openmc-mesh-tally-trilinear", "path": str(COARSE.relative_to(ROOT)),
                   "sha256": sha256_file(COARSE)},
        "energy_boundaries_eV": bounds_list, "flux_units": "n cm^-2 s^-1", "cell_count": n ** 3,
        "geometry": {"kind": "regular", "dimension": [n, n, n],
                     "axis_boundaries_cm": [[float(e) for e in edges]] * 3},
    }
    digest = hashlib.sha256()
    totals, volume_totals, names, counts = [], [], {}, {}
    started = time.monotonic()
    with open(flux_path, "wb") as out:
        def emit(text):
            data = (text + "\n").encode()
            digest.update(data)
            out.write(data)

        emit(json.dumps(header))
        for k in range(n):
            plane = (1.0 - upper[k]) * cube[lower[k]] + upper[k] * cube[lower[k] + 1]  # [y, x, group]
            rows = (1.0 - upper)[:, None, None] * plane[lower] + upper[:, None, None] * plane[lower + 1]  # [j, y, g]
            slab = ((1.0 - upper)[None, :, None] * rows[:, lower] + upper[None, :, None] * rows[:, lower + 1])
            for j in range(n):
                for i in range(n):
                    ordinal = i + n * j + n * n * k
                    row = slab[j, i].tolist()
                    total = math.fsum(row)
                    name = material_name(math.sqrt(centres[i] ** 2 + centres[j] ** 2 + centres[k] ** 2))
                    counts[name] = counts.get(name, 0) + 1
                    if name != "SS316":
                        names[f"e{ordinal}"] = name
                    totals.append(total)
                    volume_totals.append(total * volume)
                    box = [[float(edges[a]), float(edges[a + 1])] for a in (i, j, k)]
                    emit('{"record": "cell", "ordinal": %d, "id": "e%d", "index": [%d, %d, %d], "bounds_cm": %s, '
                         '"volume_cm3": %r, "flux_per_group": %s, "flux_total": %r}'
                         % (ordinal, ordinal, i + 1, j + 1, k + 1, json.dumps(box), volume, json.dumps(row), total))
            print(f"fine: z-slab {k + 1}/{n}, {time.monotonic() - started:.0f} s", flush=True)
        footer = {"record": "footer", "cell_count": n ** 3, "flux_sum_over_cells": math.fsum(totals),
                  "volume_integrated_flux": math.fsum(volume_totals)}
        emit(json.dumps(footer))
    spec = {
        "spec": "actinv-mesh-spec-1", "title": "P189: 100^3 mesh, four materials, interpolated OpenMC flux",
        "projectile": "neutron", "library": LIBRARY, "decay": DECAY,
        "material": material_spec("SS316", volume),
        "materials": {cell: material_spec(name, volume) for cell, name in names.items()},
        "flux": {"path": str(flux_path), "sha256": digest.hexdigest()},
        "schedule": SCHEDULE, "options": OPTIONS, "cell_result_fields": CELL_RESULT_FIELDS,
        "threads": THREADS, "resume": True,
    }
    write_json(WORK / "mesh_spec.json", spec)
    write_json(WORK / "fine.json", {
        "fine_dim": n, "cells": n ** 3, "cell_volume_cm3": volume, "material_cells": counts,
        "coarse": {"path": str(COARSE.relative_to(ROOT)), "sha256": header["source"]["sha256"]},
        "flux": {"path": str(flux_path.relative_to(ROOT)), "sha256": digest.hexdigest(),
                 "bytes": flux_path.stat().st_size, "flux_sum_over_cells": footer["flux_sum_over_cells"]},
        "mesh_spec": {"path": str((WORK / "mesh_spec.json").relative_to(ROOT)),
                      "sha256": sha256_file(WORK / "mesh_spec.json")},
        "wall_s": time.monotonic() - started})
    print(json.dumps({"flux": str(flux_path), "sha256": digest.hexdigest(), "cells": n ** 3}))
    return 0


def binary():
    exe = os.environ.get("ACTINV_BIN")
    if not exe or not Path(exe).is_file():
        raise SystemExit("p189_harness: set ACTINV_BIN to the actinv binary")
    return Path(exe), sha256_file(exe)


def own_cgroup_base(p181):
    return Path("/sys/fs/cgroup") / (p181.cgroup_record()["path"] or "").lstrip("/")


def snapshot(p181, base, output):
    cur, peak = p181.read_text(base / "memory.current"), p181.read_text(base / "memory.peak")
    return {"time_utc": now_utc(), "memory_current_bytes": int(cur) if cur and cur.isdigit() else None,
            "memory_peak_bytes": int(peak) if peak and peak.isdigit() else None,
            "output_bytes": output.stat().st_size if output.exists() else 0}


def reap(proc):
    """Terminate, then kill, then wait: the child is always reaped."""
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
    proc.wait()


def run():
    import p181_harness as p181

    exe, exe_sha = binary()
    spec, output = WORK / "mesh_spec.json", WORK / "mesh_out.ndjson"
    if not spec.is_file():
        raise SystemExit("p189_harness: run `fine` first")
    previous = json.loads((WORK / "run.json").read_text())["attempts"] if (WORK / "run.json").is_file() else []
    argv = [str(exe), "mesh", str(spec), str(output)]
    cg = p181.cgroup_record()
    base = own_cgroup_base(p181)
    attempt = {
        "argv": argv, "start_utc": now_utc(), "resumed": output.exists(), "cgroup": cg,
        "lscpu_model": p181.lscpu_model(), "load_average_start": list(os.getloadavg()),
        "other_scopes_start": p181.other_scopes(cg["path"]), "binary_sha256": exe_sha,
        "git_head": git_text("rev-parse", "HEAD"), "git_dirty": git_text("status", "--porcelain", "--untracked-files=no"),
        "limit_s": RUN_LIMIT_S}
    stderr_path, stdout_path = WORK / f"run_{len(previous)}.stderr", WORK / f"run_{len(previous)}.stdout"
    started = time.monotonic()
    timed_out = False
    with open(stdout_path, "wb") as so, open(stderr_path, "wb") as se:
        proc = subprocess.Popen(argv, stdout=so, stderr=se, cwd=ROOT)
        try:
            while True:
                p181.write_atomic(WORK / "heartbeat.json", snapshot(p181, base, output))
                try:
                    proc.wait(timeout=HEARTBEAT_S)
                    break
                except subprocess.TimeoutExpired:
                    if time.monotonic() - started > RUN_LIMIT_S:
                        timed_out = True
                        break
        finally:
            reap(proc)
    final = snapshot(p181, base, output)
    p181.write_atomic(WORK / "heartbeat.json", final)
    attempt.update({
        "end_utc": now_utc(), "wall_s": time.monotonic() - started, "exit_code": proc.returncode,
        "timed_out": timed_out, "memory_peak_bytes": final["memory_peak_bytes"],
        "output_bytes": final["output_bytes"], "load_average_end": list(os.getloadavg()),
        "other_scopes_end": p181.other_scopes(cg["path"]),
        "stdout_tail": stdout_path.read_text(errors="replace")[-2000:],
        "stderr_tail": stderr_path.read_text(errors="replace")[-2000:]})
    write_json(WORK / "run.json", {"attempts": previous + [attempt]})
    print(json.dumps({"exit_code": proc.returncode, "wall_s": attempt["wall_s"], "timed_out": timed_out}))
    return 0 if proc.returncode == 0 else 1


_ORDINAL = re.compile(rb'^\{"record":\s*"cell",\s*"ordinal":\s*(\d+)')


def line_ordinal(line):
    m = _ORDINAL.match(line)
    return int(m.group(1)) if m else None


def prune(value, tails):
    """The keys of a JSON object named by the dotted-path tails; a tail that ends keeps the key whole."""
    out = {}
    for head in {tail[0] for tail in tails}:
        if head not in value:
            continue
        rests = [tail[1:] for tail in tails if tail[0] == head]
        out[head] = value[head] if any(not r for r in rests) else prune(value[head], rests)
    return out


def project(result, fields):
    """What `actinv mesh` keeps of a cell's result for these cell_result_fields."""
    out = {f: result[f] for f in fields if not f.startswith("steps.") and f in result and result[f] is not None}
    tails = [f[len("steps."):].split(".") for f in fields if f.startswith("steps.")]
    if tails:
        steps = []
        for step in result["steps"]:
            kept = {"step": step["step"]}
            kept.update(prune({k: v for k, v in step.items() if v is not None}, tails))
            steps.append(kept)
        out["steps"] = steps
    return out


def first_difference(a, b, path=""):
    """Path and values of the first place two JSON documents differ, or None."""
    if isinstance(a, dict) and isinstance(b, dict):
        for key in sorted(set(a) | set(b)):
            if key not in a or key not in b:
                return f"{path}/{key}: present in one only"
            found = first_difference(a[key], b[key], f"{path}/{key}")
            if found:
                return found
        return None
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return f"{path}: lengths {len(a)} and {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            found = first_difference(x, y, f"{path}[{i}]")
            if found:
                return found
        return None
    return None if a == b and type(a) is type(b) else f"{path}: {a!r} vs {b!r}"


def collect(path, wanted):
    """Parsed cell records of a file with one record per line, for the wanted ordinals, in one pass."""
    found, left = {}, set(wanted)
    with open(path, "rb") as handle:
        for line in handle:
            ordinal = line_ordinal(line)
            if ordinal in left:
                found[ordinal] = json.loads(line)
                left.discard(ordinal)
                if not left:
                    break
    return found


def sample():
    import numpy as np

    exe, exe_sha = binary()
    spec = json.loads((WORK / "mesh_spec.json").read_text())
    cells = FINE_DIM ** 3
    wanted = sorted(int(o) for o in np.random.default_rng(SAMPLE_SEED).choice(cells, size=min(SAMPLE_N, cells),
                                                                              replace=False))
    flux = collect(WORK / "flux.ndjson", wanted)
    mesh = collect(WORK / "mesh_out.ndjson", wanted)
    header = json.loads(open(WORK / "flux.ndjson", "rb").readline())
    work = WORK / "sample"
    work.mkdir(parents=True, exist_ok=True)
    rows = []
    for ordinal in wanted:
        cell_id = flux[ordinal]["id"]
        material = (spec.get("materials") or {}).get(cell_id, spec["material"])
        point = {
            "spec": "actinv-spec-1", "title": spec["title"], "projectile": spec["projectile"],
            "library": spec["library"], "decay": spec["decay"], "material": material,
            "spectrum": {"structure": "custom", "flux_per_group": flux[ordinal]["flux_per_group"],
                         "boundaries_eV": header["energy_boundaries_eV"], "descending": False},
            "schedule": spec["schedule"], "options": spec["options"]}
        spec_path, out_path = work / f"s{ordinal}.json", work / f"s{ordinal}.out.json"
        write_json(spec_path, point)
        name = next((n for n, (_, comp) in MATERIALS.items() if comp == material["composition"]), None)
        row = {"ordinal": ordinal, "id": cell_id, "material": name, "equal": False, "first_difference": None}
        try:
            p = subprocess.run([str(exe), "run", str(spec_path), str(out_path)], capture_output=True, text=True,
                               cwd=ROOT, timeout=SAMPLE_TIMEOUT_S)
            if p.returncode != 0:
                row["first_difference"] = f"actinv run exit {p.returncode}: {p.stderr[-300:]}"
            else:
                alone = project(json.loads(out_path.read_text()), spec["cell_result_fields"])
                row["first_difference"] = first_difference(alone, mesh[ordinal]["result"])
                row["equal"] = row["first_difference"] is None
        except subprocess.TimeoutExpired:
            row["first_difference"] = f"actinv run timed out after {SAMPLE_TIMEOUT_S} s"
        rows.append(row)
        print(f"sample: {ordinal} {name} equal={row['equal']}", flush=True)
    write_json(WORK / "sample.json", {"seed": SAMPLE_SEED, "binary_sha256": exe_sha,
                                      "cell_result_fields": spec["cell_result_fields"], "rows": rows})
    return 0 if all(r["equal"] for r in rows) else 1


def scan_mesh(path):
    """One streamed pass over the mesh output: header fields, cell count, ordinal check, footer verbatim."""
    import numpy as np

    header = footer = None
    count, in_order, out_of_range, duplicates = 0, True, 0, 0
    seen = None
    with open(path, "rb") as handle:
        for line in handle:
            if header is None:
                header = json.loads(line)
                seen = np.zeros(int(header["cell_count"]), dtype=bool)
                continue
            ordinal = line_ordinal(line)
            if ordinal is not None:
                if ordinal >= len(seen):
                    out_of_range += 1
                else:
                    duplicates += int(seen[ordinal])
                    seen[ordinal] = True
                in_order = in_order and ordinal == count
                count += 1
            elif line.startswith(b'{"record":'):
                footer = json.loads(line)
    for key in ("source_energy_boundaries_eV", "activation_energy_boundaries_eV"):
        header[key] = {"groups": len(header[key]) - 1, "first_eV": header[key][0], "last_eV": header[key][-1]}
    return {"header": header, "cell_records": count, "ordinals_exactly_once": bool(
        seen is not None and seen.all() and not out_of_range and not duplicates and count == len(seen)),
            "ordinals_in_order": in_order, "ordinals_out_of_range": out_of_range, "ordinals_duplicated": duplicates,
            "footer": footer}


def file_entry(path):
    return {"path": str(Path(path).relative_to(ROOT)), "sha256": sha256_file(path), "bytes": Path(path).stat().st_size}


def assemble():
    run_doc = json.loads((WORK / "run.json").read_text())
    sample_doc = json.loads((WORK / "sample.json").read_text())
    fine_doc = json.loads((WORK / "fine.json").read_text())
    spec = json.loads((WORK / "mesh_spec.json").read_text())
    exe = Path(run_doc["attempts"][-1]["argv"][0])
    record = {
        "schema": SCHEMA, "rehearsal": REHEARSAL, "fine_dim": FINE_DIM,
        "protocol_sha256": sha256_file(PROTOCOL), "harness_sha256": sha256_file(Path(__file__)),
        "checker_sha256": sha256_file(CHECKER) if CHECKER.is_file() else None,
        "git_head": git_text("rev-parse", "HEAD"),
        "git_dirty": git_text("status", "--porcelain", "--untracked-files=no"),
        "inputs": {"coarse": file_entry(COARSE), "flux": file_entry(WORK / "flux.ndjson"),
                   "mesh_spec": file_entry(WORK / "mesh_spec.json"),
                   "binary": {"path": str(exe), "sha256": sha256_file(exe)},
                   "library": spec["library"], "decay": spec["decay"], "fine": fine_doc},
        "run": run_doc, "mesh_scan": scan_mesh(WORK / "mesh_out.ndjson"), "sample": sample_doc,
        "environment": {"python": platform.python_version(), "platform": platform.platform(),
                        "lscpu_model": run_doc["attempts"][-1].get("lscpu_model")}}
    write_json(RECORD, record)
    print(json.dumps({"record": str(RECORD), "sha256": sha256_file(RECORD)}))
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    steps = {"coarse": coarse, "fine": fine, "run": run, "sample": sample, "assemble": assemble}
    if len(argv) == 1 and argv[0] in steps:
        return steps[argv[0]]()
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
