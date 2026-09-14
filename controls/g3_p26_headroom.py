#!/usr/bin/env python3
"""P26 G3: executable coverage census and headroom measurement under the
frozen G2 contract.

Stages (run inside the enforced cgroup, one job at a time):

  --stage fast      coverage census + W-MATCMP timings + W-R2S timing +
                    task decomposition record   (~minutes)
  --stage campaign  W-CAMPAIGN 1000-case grid, two modes, resumable ledger
                    (per_invocation CLI and batched in-process prototype)

Every published number carries its execution context: hardware, executable
identity+hash, cache state, resource limits and the frozen contract hash.
Nothing here edits production code; the batched mode is the `prototypes/`
runner invoked under the installed v1.0.1 interpreter.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTRACT = ROOT / "results" / "g2_p26_contract.json"
G2_CHECK = ROOT / "results" / "g2_p26_check.json"
WORK = ROOT / "target" / "preflight-tmp" / "p26"
LEDGER = ROOT / "results" / "g3_p26_campaign_ledger.jsonl"
OUT = ROOT / "results" / "g3_p26_headroom.json"
COVERAGE_OUT = ROOT / "results" / "g3_p26_coverage.json"
TASKS_OUT = ROOT / "results" / "g3_p26_task_decomposition.json"

ACTINV_V101 = Path.home() / ".local" / "bin" / "actinv"
VENV_PY = Path.home() / ".local" / "share" / "pipx" / "venvs" / "actinv" / "bin" / "python"
ACTINV_CAND = ROOT / "target" / "release" / "actinv"
ALARA = Path.home() / "nuclear-data" / "alara-2.9.2-build" / "src" / "alara"
ALARA_LIB = Path.home() / "nuclear-data" / "alara-2.9.2" / "sample" / "data" / "truncated_fendlg-2.0_175_for_samples_only"
ALARA_SAMPLE = Path.home() / "nuclear-data" / "alara-2.9.2" / "sample"

PER_CASE_TIMEOUT_S = 1800.0

# contract-declared cooling milestones; schedule steps are cumulative deltas
COOL_DT_S = [86400.0, 2505600.0, 28944000.0, 283824000.0]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for blk in iter(lambda: fh.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def context() -> dict:
    cpu = "unknown"
    for line in Path("/proc/cpuinfo").read_text().splitlines():
        if line.startswith("model name"):
            cpu = line.split(":", 1)[1].strip()
            break
    mem = "unknown"
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemTotal"):
            mem = line.split(":", 1)[1].strip()
    return {
        "host": platform.node(), "machine": platform.machine(),
        "kernel": platform.release(), "cpu": cpu, "mem": mem,
        "python": sys.version.split()[0],
        "executables": {
            "actinv_v1_0_1": {"path": str(ACTINV_V101), "sha256": sha256_file(ACTINV_V101)},
            "actinv_candidate": {"path": str(ACTINV_CAND), "sha256": sha256_file(ACTINV_CAND)},
            "alara_2_9_2": {"path": str(ALARA), "sha256": sha256_file(ALARA)},
            "venv_python": {"path": str(VENV_PY), "sha256": sha256_file(VENV_PY)},
        },
        "resource_limits": {"MemoryMax": "6G", "MemorySwapMax": 0, "TasksMax": 128,
                            "CPUQuota": "200%", "jobs_at_once": 1,
                            "enforced_by": "systemd-run --user --scope (caller)"},
        "tmpdir": str(WORK),
        "cache_state_note": ("cold_process = fresh process with OS page cache as-is; "
                             "warm_process = immediate repeat with binary and data in page cache"),
    }


def collapse_to_709() -> list[float]:
    """Frozen Amendment-1 rule: overlap-conserving collapse of the MAT 9861
    histogram onto the NPZ `bounds` edges."""
    contract = json.loads(CONTRACT.read_text())
    src = contract["workloads"]["W-MATCMP"]["eligible_population"]["spectra"]["irdff_sp_mat9861_709"]
    z = zipfile.ZipFile(src["source"]["archive"])
    lines = z.read(src["source"]["member"]).decode("utf-8", "replace").splitlines()
    target = src["source"]["mat"]

    def flds(s):
        return [s[i:i + 11] for i in range(0, 66, 11)]

    from endf_common import endf_float as ef

    xs, ys = [], []
    i = 0
    while i < len(lines):
        l = lines[i]
        if len(l) >= 75 and l[66:70].strip() == str(target) and l[70:72].strip() == "3" \
                and l[72:75].strip() == "261":
            i += 1  # section HEAD line; the TAB1 header follows
            f = flds(lines[i])
            nr, np_ = int(f[4]), int(f[5])
            i += 1 + (nr + 5) // 6
            while len(xs) < np_:
                f = flds(lines[i]); i += 1
                for k in range(0, 6, 2):
                    if len(xs) < np_:
                        xs.append(ef(f[k])); ys.append(ef(f[k + 1]))
            break
        i += 1
    if not xs:
        raise RuntimeError("MAT 9861 MT=261 spectrum not found")

    import numpy as np
    npz = np.load(ROOT / src["derivation"]["boundary_source"]["file"])
    edges = npz[src["derivation"]["boundary_source"]["array"]].astype(float).tolist()
    out = []
    j = 0
    for g in range(len(edges) - 1):
        lo, hi = edges[g], edges[g + 1]
        total = 0.0
        while j < len(ys) - 1 and xs[j + 1] <= lo:
            j += 1
        k = j
        while k < len(ys) - 1 and xs[k] < hi:
            total += ys[k] * (min(xs[k + 1], hi) - max(xs[k], lo))
            k += 1
        out.append(total)
    return out


def fns_spectrum() -> list[float]:
    return json.loads((ROOT / "examples" / "fns_fe_5min.json").read_text())["spectrum"]["flux_per_group"]


def material_percent(comp: dict, basis: str) -> dict:
    if basis == "wt_percent":
        return comp
    if basis == "wt_fraction":
        return {k: v * 100.0 for k, v in comp.items()}
    raise ValueError(basis)


def build_spec(title: str, composition_pct: dict, flux: list[float],
               irradiation_s: float) -> dict:
    base = json.loads((ROOT / "examples" / "fns_fe_5min.json").read_text())
    spec = {
        "spec": "actinv-spec-1",
        "title": title,
        "projectile": "neutron",
        "library": base["library"],
        "decay": base["decay"],
        "material": {"mass_g": 1.0, "basis": "wt_percent", "composition": composition_pct},
        "spectrum": {"structure": "fispact-709", "flux_per_group": flux},
        "schedule": ([{"dt": f"{irradiation_s} s", "flux": 1.0}]
                     + [{"dt": f"{dt} s", "flux": 0.0} for dt in COOL_DT_S]),
        "options": base.get("options", {"mode": "auto", "prune": "rate",
                                        "bmin_atoms_per_g": 1e-08, "temperature_K": 293.6}),
    }
    return spec


def timed_run(cmd: list[str], cwd: Path, timeout: float = PER_CASE_TIMEOUT_S) -> dict:
    t0 = time.perf_counter()
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        wall = time.perf_counter() - t0
        return {"wall_s": wall, "returncode": r.returncode,
                "stderr_tail": (r.stderr or "")[-300:]}
    except subprocess.TimeoutExpired:
        return {"wall_s": time.perf_counter() - t0, "returncode": None,
                "failure": "comparator_timeout" if "alara" in cmd[0] else "actinv_error"}


def gen_matcmp_specs(contract: dict, spectra: dict) -> dict[str, Path]:
    pop = contract["workloads"]["W-MATCMP"]["eligible_population"]
    specdir = WORK / "matcmp_specs"
    specdir.mkdir(parents=True, exist_ok=True)
    out = {}
    for case in pop["cases"]:
        m = pop["materials"][case["material"]]
        sc = pop["schedules"][case["schedule"]]
        comp = material_percent(m["composition"], m["basis"])
        spec = build_spec(case["case"], comp, spectra[case["spectrum"]], sc["irradiations_s"][0])
        p = specdir / f"{case['case']}.json"
        p.write_text(json.dumps(spec))
        out[case["case"]] = p
    return out


def gen_campaign_specs(contract: dict, spectra: dict) -> Path:
    pop = contract["workloads"]["W-CAMPAIGN"]["eligible_population"]
    specdir = WORK / "campaign_specs"
    specdir.mkdir(parents=True, exist_ok=True)
    for case in pop["cases"]:
        el = case["material_rule"]["impurity"]
        c = case["material_rule"]["concentration_wppm"]
        imp_pct = c * 1e-4  # wppm -> wt_percent
        comp = {"FE": 100.0 - imp_pct, el: imp_pct}
        spec = build_spec(case["case"], comp, spectra[case["spectrum"]],
                          case["schedule_rule"]["irradiation_s"])
        (specdir / f"{case['case']}.json").write_text(json.dumps(spec))
    return specdir


def response_summary(result: dict) -> dict:
    steps = result.get("steps", [])
    out = []
    for step in steps:
        act = step.get("activity_Bq_per_g") or {}
        top5 = sorted(act, key=lambda n: -(act.get(n) or 0.0))[:5]
        photon = step.get("photon_source") or {}
        out.append({"t_s": step.get("t_s"),
                    "total_activity_bq_per_g": sum(act.values()),
                    "decay_heat_w_per_g": step.get("heat_W_per_g"),
                    "photon_source_total_per_g": sum(photon.get("values", []) or []),
                    "top5_nuclides_by_activity": top5})
    return {"per_step": out}


def run_cli_cases(specs: dict[str, Path], tool_path: Path, tool_name: str) -> list[dict]:
    rows = []
    for name, spec_path in specs.items():
        for cache_state in ("cold_process", "warm_process"):
            out_path = spec_path.with_suffix(f".{tool_name}.{cache_state}.out.json")
            r = timed_run([str(tool_path), "run", str(spec_path), str(out_path)], cwd=ROOT)
            row = {"case": name, "tool": tool_name, "cache_state": cache_state,
                   "wall_s": r["wall_s"], "returncode": r["returncode"]}
            if r["returncode"] == 0 and out_path.is_file():
                data = out_path.read_bytes()
                row["output_sha256"] = hashlib.sha256(data).hexdigest()
                row["response"] = response_summary(json.loads(data))
            else:
                row["failure"] = "actinv_error" if "actinv" in tool_name else "comparator_error"
                row["stderr_tail"] = r.get("stderr_tail", "")
            rows.append(row)
    return rows


def alara_probe() -> dict:
    """Run the official truncated-data sample once and census which contract
    parents the shipped library covers."""
    lib_parents = set()
    for line in ALARA_LIB.read_text(errors="replace").splitlines():
        tok = line[:8].strip()
        if tok.isdigit() and len(tok) >= 5:
            lib_parents.add(int(tok))
    needed_elements = {"FE": 26, "CO": 27, "NB": 41, "W": 74, "NI": 28, "MO": 42,
                       "AG": 47, "TA": 73, "V": 23, "CU": 29, "CR": 24, "MN": 25}
    element_coverage = {el: any(p // 10000 == z for p in lib_parents)
                        for el, z in needed_elements.items()}
    probe = {"sample_run": None}
    probe_dir = WORK / "alara_probe"
    if probe_dir.exists():
        import shutil
        shutil.rmtree(probe_dir)
    import shutil as sh
    sh.copytree(ALARA_SAMPLE, probe_dir)
    (probe_dir / "output").mkdir(exist_ok=True)
    (probe_dir / "dump_files").mkdir(exist_ok=True)
    conv = timed_run([str(ALARA), "sample1"], cwd=probe_dir, timeout=300.0)
    run = timed_run([str(ALARA), "sample3"], cwd=probe_dir, timeout=300.0)
    probe["sample_run"] = {
        "conversion_returncode": conv["returncode"], "run_returncode": run["returncode"],
        "conversion_wall_s": conv["wall_s"], "run_wall_s": run["wall_s"],
        "stderr_tail": (conv.get("stderr_tail", "") + run.get("stderr_tail", ""))[-400:],
    }
    probe["truncated_library_parent_count"] = len(lib_parents)
    probe["truncated_library_parents"] = sorted(lib_parents)
    probe["contract_element_coverage"] = element_coverage
    probe["coverage"] = "contract_gap" if not all(element_coverage.values()) else "partial"
    return probe


def stage_fast(contract: dict) -> dict:
    spectra = {"fns_709": fns_spectrum(), "irdff_sp_mat9861_709": collapse_to_709()}
    matcmp_specs = gen_matcmp_specs(contract, spectra)

    coverage = {"contract_sha256": sha256_file(CONTRACT), "per_comparator": {}}
    # actinv coverage: validate every case on both executables
    for tool_name, tool in (("actinv_v1_0_1", ACTINV_V101), ("actinv_candidate", ACTINV_CAND)):
        per_case = {}
        for name, sp in matcmp_specs.items():
            r = timed_run([str(tool), "validate", str(sp)], cwd=ROOT, timeout=120.0)
            per_case[name] = "ok" if r["returncode"] == 0 else "actinv_error"
        coverage["per_comparator"][tool_name] = {
            "W-MATCMP": per_case,
            "W-CAMPAIGN": "expressible_by_same_spec_schema; per-case runs in campaign ledger",
            "W-R2S": "ok" if (ROOT / "examples" / "mesh_demo.json").is_file() else "contract_gap",
        }
    probe = alara_probe()
    coverage["per_comparator"]["alara_2_9_2"] = {
        "probe": probe,
        "W-MATCMP": {name: "contract_gap" for name in matcmp_specs},
        "W-CAMPAIGN": "contract_gap (truncated library lacks all contract elements)",
        "W-R2S": "contract_gap (no distributed-source handoff executed)",
    }
    for name in ("fispact_ii", "scale_origen", "openmc"):
        coverage["per_comparator"][name] = {
            "W-MATCMP": "comparator_unavailable",
            "W-CAMPAIGN": "comparator_unavailable",
            "W-R2S": "comparator_unavailable"}
    coverage["per_comparator"]["njoy_2016_79"] = {
        "note": "data-processing comparator only; no equivalent-output leg in contract",
        "W-MATCMP": "not_applicable", "W-CAMPAIGN": "not_applicable", "W-R2S": "not_applicable"}
    COVERAGE_OUT.write_text(json.dumps(coverage, indent=1, sort_keys=True) + "\n")

    timings = {"W-MATCMP": {}, "W-R2S": {}}
    timings["W-MATCMP"]["actinv_v1_0_1"] = run_cli_cases(matcmp_specs, ACTINV_V101, "actinv_v1_0_1")
    timings["W-MATCMP"]["actinv_candidate"] = run_cli_cases(matcmp_specs, ACTINV_CAND, "actinv_candidate")

    mesh = ROOT / "examples" / "mesh_demo.json"
    rows = []
    for cache_state in ("cold_process", "warm_process"):
        r = timed_run([str(ACTINV_CAND), "mesh", str(mesh),
                       str(WORK / f"mesh.{cache_state}.out.ndjson")], cwd=ROOT)
        out_file = WORK / f"mesh.{cache_state}.out.ndjson"
        rows.append({"case": "mesh_demo_3cell", "tool": "actinv_candidate",
                     "cache_state": cache_state, "wall_s": r["wall_s"],
                     "returncode": r["returncode"],
                     "stderr_tail": r.get("stderr_tail", ""),
                     "output_sha256": sha256_file(out_file) if out_file.is_file() else None})
    timings["W-R2S"]["actinv_candidate"] = rows
    timings["W-R2S"]["comparators"] = "comparator_unavailable (predeclared)"

    tasks = {
        "schema": "actinv-p26-g3-tasks-1",
        "partition": "diagnostic",
        "note": "no practitioner study is recorded; this decomposition supports design only "
                "and cannot qualify the 50% hands-on target",
        "interpretation_tasks": [
            {"id": "T1", "task": "identify the nuclides dominating each requested response at each cooling time"},
            {"id": "T2", "task": "determine whether the material ranking survives the declared impurity uncertainty"},
            {"id": "T3", "task": "identify which nuclear-data channels control the dominant nuclides"},
            {"id": "T4", "task": "package inputs, data identities and outputs so a colleague reproduces the result"},
            {"id": "T5", "task": "hand a distributed photon source to an external transport code and verify normalization"},
        ],
        "rubric": {"completion": "all required artifacts produced", "timeout_s": 3600,
                   "consequential_error": "a wrong conclusion that survives the participant's own checks"},
    }
    TASKS_OUT.write_text(json.dumps(tasks, indent=1, sort_keys=True) + "\n")

    return {"coverage": coverage, "timings": timings, "spectra_sha256": {
        "fns_709": hashlib.sha256(json.dumps(spectra["fns_709"]).encode()).hexdigest(),
        "irdff_sp_mat9861_709": hashlib.sha256(json.dumps(spectra["irdff_sp_mat9861_709"]).encode()).hexdigest(),
    }}


def campaign_done() -> set[tuple[str, str]]:
    done = set()
    if LEDGER.is_file():
        for line in LEDGER.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                done.add((r["case"], r["mode"]))
    return done


def stage_campaign(contract: dict) -> None:
    spectra = {"fns_709": fns_spectrum(), "irdff_sp_mat9861_709": collapse_to_709()}
    specdir = gen_campaign_specs(contract, spectra)
    done = campaign_done()
    specs = sorted(p for p in specdir.glob("*.json") if ".out." not in p.name)

    with LEDGER.open("a") as ledger:
        for spec_path in specs:
            case = spec_path.stem
            if (case, "per_invocation") not in done:
                out_path = spec_path.with_suffix(".cli.out.json")
                r = timed_run([str(ACTINV_V101), "run", str(spec_path), str(out_path)], cwd=ROOT)
                row = {"case": case, "mode": "per_invocation", "tool": "actinv_v1_0_1",
                       "wall_s": r["wall_s"], "returncode": r["returncode"]}
                if r["returncode"] == 0 and out_path.is_file():
                    data = out_path.read_bytes()
                    row["output_sha256"] = hashlib.sha256(data).hexdigest()
                    row["response"] = response_summary(json.loads(data))
                else:
                    row["failure"] = "actinv_error"
                    row["stderr_tail"] = r.get("stderr_tail", "")
                ledger.write(json.dumps(row, sort_keys=True) + "\n")
                ledger.flush()

    batch_out = WORK / "campaign_batched.jsonl"
    batched_done = set()
    if batch_out.is_file():
        for line in batch_out.read_text().splitlines():
            if line.strip():
                batched_done.add(json.loads(line)["case"])
    remaining = [str(specdir)] + [str(batch_out)]
    r = timed_run([str(VENV_PY), str(ROOT / "prototypes" / "p26_campaign.py"),
                   str(specdir), str(batch_out)], cwd=ROOT, timeout=28800.0)
    with LEDGER.open("a") as ledger:
        if batch_out.is_file():
            for line in batch_out.read_text().splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                if (row["case"], "batched_inprocess") not in done:
                    ledger.write(json.dumps(row, sort_keys=True) + "\n")
        ledger.write(json.dumps({"case": "__batch_driver__", "mode": "batched_inprocess",
                                 "wall_s": r["wall_s"], "returncode": r["returncode"]},
                                sort_keys=True) + "\n")


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    contract = json.loads(CONTRACT.read_text())
    frozen = json.loads(G2_CHECK.read_text())["contract_sha256"]
    if sha256_file(CONTRACT) != frozen:
        raise RuntimeError("contract changed since G2 freeze — amendment required")

    stages = sys.argv[1:] or ["fast"]
    record = json.loads(OUT.read_text()) if OUT.is_file() else {}
    record.update({"schema": "actinv-p26-g3-headroom-1",
                   "protocol_sha256": "0dd9be843e4e195d3f3ebb9a9084f233af3d0eedf5045bbb48d77d665f5d1e06",
                   "contract_sha256": frozen,
                   "context": context()})
    record["stages_run"] = sorted(set(record.get("stages_run", [])) | set(stages))
    if "fast" in stages:
        record.update(stage_fast(contract))
    if "campaign" in stages:
        stage_campaign(contract)
        record["campaign_ledger"] = str(LEDGER.relative_to(ROOT))
        record["campaign_records"] = sum(1 for l in LEDGER.read_text().splitlines() if l.strip())
    OUT.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"stages": stages, "out": str(OUT)}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
