#!/usr/bin/env python3
"""P172 harness (protocols/ACTINV-P172_PROTOCOL.md): decay heat after 14 MeV irradiation, ACTINV against OpenMC depletion,
on every JAEA FNS experiment of the CoNDERC corpus (132 experiments).

Arms (the measured points and cooling times are the ones of controls/cb2_fns.py):
  actinv_tendl2025  ACTINV with the shipped TENDL-2025 library of data catalog 1.2.0, through controls/cb2_fns.py.
  actinv_endf8      ACTINV with an ENDF/B-VIII.1 709-group library built from every nuclide in OpenMC's ENDF/B-VIII.1
                    HDF5 library (557 tapes, NNDC release archive, controls/p172_endf8_stage.py), through the same
                    script, so only the library differs from the first arm.
  openmc            OpenMC 0.15.3 MicroXS.from_multigroup_flux -> IndependentOperator -> PredictorIntegrator, ENDF/B-VIII.1
                    HDF5 cross sections and OpenMC's published ENDF/B-VIII.1 fast-spectrum depletion chain
                    (chain_endfb81_fast.xml, openmc.org/depletion-chains), run by controls/openmc_fns_driver.py (the
                    driver of the exploratory head-to-head, unmodified). Decay heat is sum(N * lambda * E_decay) with
                    the chain's own decay energies (light + electromagnetic + heavy mean energies, no neutrino), which
                    are the components ACTINV sums for its total heat.
  openmc_p32        Sensitivity 1: the same, with the P32 chain (Chain.from_endf on ENDF/B-VIII.0 decay data and the
                    TENDL-2025 neutron files for reaction channels, no isomer branching). The 21-experiment exploratory
                    record was made with it.
  openmc_isomer     Sensitivity 2: the published chain plus isomer branching ratios for (n,2n), (n,3n), (n,p) and (n,a)
                    taken from the ENDF/B-VIII.1 709-group library (flux-weighted per experiment flux). The published
                    chain's own (n,g) ratios are kept. (n,n') isomer channels cannot be expressed in an OpenMC chain and
                    stay absent.

Modes (each its own process; none runs unless asked):
  actinv --arm {tendl2025,endf8}   writes results/p172/actinv_<arm>.json
  openmc [--chain-variant {official,p32,isomer}]   writes results/p172/openmc[_p32|_isomer].json (starts the OpenMC
                                   0.15.3 interpreter itself, one driver process per chunk, each in its own 6 GiB scope)
  assemble                         merges the arm files into results/p172_openmc_fns.json
  make-isomer-chain (internal)     builds one chain file; run by `openmc --isomer` with the OpenMC interpreter

Run each mode inside the bounded systemd scope AGENTS.md prescribes (see the protocol for exact commands). The OpenMC
modes refuse to start without an enforced cgroup. Nothing here is run by the protocol author.
"""
import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "controls"))

HOME = Path.home()
DATA = HOME / "nuclear-data"
# P172_REHEARSAL=1: the pre-registration rehearsal. Every output goes under target/ (never results/), the OpenMC arms run
# only the smallest flux-group chunk, and nothing it writes is scored or kept.
REHEARSAL = os.environ.get("P172_REHEARSAL") == "1"
OUT_DIR = ROOT / "target" / "p172-rehearsal" if REHEARSAL else ROOT / "results" / "p172"
RECORD = OUT_DIR / "record.json" if REHEARSAL else ROOT / "results" / "p172_openmc_fns.json"
WORK_TAG = "p172-rehearsal" if REHEARSAL else "p172"
SCHEMA = "actinv-p172-openmc-fns-1"
GIB = 1 << 30

OPENMC_VERSION = "0.15.3"
OPENMC_PY = HOME / "micromamba/envs/openmc0153dd/bin/python"
CHAIN_XML = DATA / "openmc-chains/chain_endfb81_fast.xml"  # OpenMC's published chain, the primary one
CHAIN_P32_XML = DATA / "p32-work/chain/depletion/chain.xml"
CHAIN_NAME = "chain_endfb81_fast.xml"
XS_XML = DATA / "endfb-viii.1-hdf5/cross_sections.xml"
XS_ARCHIVE = DATA / "endfb-viii.1-download/endfb81.tar.xz"
FNS_ZIP = DATA / "conderc-fns/fns.zip"
TENDL25_NPZ = ROOT / "actinv-data/v1.2.0/activation/tendl-2025-neutron-709g.npz"
TENDL25_INDEX = ROOT / "actinv-data/v1.2.0/activation/tendl-2025-neutron-709g_index.json"
ENDF8_NPZ = DATA / "endfb-viii.1-p172/endfb81_all_709g.npz"
ENDF8_INDEX = DATA / "endfb-viii.1-p172/endfb81_all_709g_index.json"
ENDF8_STAGE_MANIFEST = DATA / "endfb-viii.1-p172/stage_manifest.json"
DECAY_PRIMARY = DATA / "endfb-viii.0-decay/bulk/endf-b-viii-0_decay.dat"
DECAY_FALLBACK = DATA / "jeff-3.3-decay/bulk/jeff-3-3_decay.dat"
ABUNDANCE = ROOT / "results/tables/abundance_mass.json"
GROUPS = ROOT / "crates/actinv-data/data/fispact_709_groups.json"
DRIVER = ROOT / "controls/openmc_fns_driver.py"
H2H_SCRIPT = ROOT / "controls/openmc_fns_headtohead.py"
CB2_SCRIPT = ROOT / "controls/cb2_fns.py"
CB2_MODULE = ROOT / "python/target/release/libactinv.so"
SEALED_H2H = ROOT / "results/openmc_fns_headtohead.json"
ACTINV_MODULE = Path(os.environ.get("ACTINV_PYTHON_MODULE", CB2_MODULE))

# Frozen inputs: name -> (path, SHA-256). The checker requires the record to hold exactly these.
PINNED = {
    "chain_xml": (CHAIN_XML, "28abc04814d1d4e58890617b4ddf0c7378fbc3d737b4d3de916321b752c4ad06"),
    "chain_p32_xml": (CHAIN_P32_XML, "d3b829467cfdda9c624aaeb5709825df378b908ecc63c3f984ade51f8d0444a0"),
    "cross_sections_xml": (XS_XML, "b8ffb6104a946bf951dc0f3b9a1f57a60f7f57c4b308cd66df4fd3dd4bc557ce"),
    "endf8_hdf5_archive": (XS_ARCHIVE, "b7ad59cb4a3d76d8a291326093a98507f8d24b6e6af629116d3f7dc85f83c4cb"),
    "fns_archive": (FNS_ZIP, "ba1dd6cb150a4aa3e0d81461054aec7d415ef19d946aba8b9886b31de218252d"),
    "tendl2025_library": (TENDL25_NPZ, "ec4c72bf598dc8ad3d533d9cfafdcf493e2d1f949a3e4db6251495659b68cc44"),
    "tendl2025_index": (TENDL25_INDEX, "8bd19b4001c246758e739cd0067a0087e1ce5c2157438dae97bd52e1d3beb3fb"),
    "endf8_stage_manifest": (ENDF8_STAGE_MANIFEST, "d16b9233e7d8855cf9f08be3a1b5c79b945454f8f21d0f66996adc7a937f861e"),
    "endf8_library": (ENDF8_NPZ, "ecaca7c072bd76b49e6724dd00aaa43172698f7c6a28e6551c551de39d11de32"),
    "endf8_index": (ENDF8_INDEX, "ff59cccf0ca85e24542054fff035079baefaea79a213e41c94bde2bc647162fa"),
    "decay_primary": (DECAY_PRIMARY, "6f04cf009086c179021f243a58dadc2d5bb078de5ba39c4fe46ccad77d228ddb"),
    "decay_fallback": (DECAY_FALLBACK, "850b8b7f85f8d88b6ad826c4cd341aaaffabd525c8ecf3c588a0ad437bf5d123"),
    "abundance_mass": (ABUNDANCE, "285b38a823dcee398a1dcab798c3b5a17d6b7ae5817edc663ed502314367a87b"),
    "sealed_h2h_record": (SEALED_H2H, "2854484244b7198a7d3e5d7c8f0aafb2a311b7934a3c9152883eda000ba1d45d"),
}
# Built before registration from crates tree fd259e18 (see the protocol).
ACTINV_MODULE_SHA256 = "f33ea3eded6cfb8fd3734de692182ba4d793f75f026206e34a68871824dd7cc4"
MODULE_CRATES_TREE = "fd259e18ecdeaff326672fce811996610a599bc1"

EXPERIMENTS = 132
MT_REACTION = {16: "(n,2n)", 17: "(n,3n)", 102: "(n,gamma)", 103: "(n,p)", 107: "(n,a)"}
ARMS_PRIMARY = ("actinv_tendl2025", "actinv_endf8", "openmc")
ARM_OPENMC_ISOMER = "openmc_isomer"
ARM_OPENMC_P32 = "openmc_p32"
ISOMER_REACTIONS = ("(n,2n)", "(n,3n)", "(n,p)", "(n,a)")  # (n,gamma) ratios stay the published chain's
BOUNDARY_TOL = 0.02  # a measurement time matches a cooling boundary within max(2%, 1 s), as in the head-to-head
LIMIT_S = 4 * 3600  # per driver process
MEMORY_GIB = 6  # every arm, as AGENTS.md


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=60).stdout.strip()


def read_text(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def cgroup_record():
    rel = None
    for line in (read_text("/proc/self/cgroup") or "").splitlines():
        parts = line.split(":", 2)
        if len(parts) == 3 and parts[0] == "0":
            rel = parts[2]
    base = Path("/sys/fs/cgroup") / (rel or "").lstrip("/")
    return {"path": rel, **{name: read_text(base / name) for name in
                            ("memory.max", "memory.swap.max", "pids.max", "cpu.max")}}


def cgroup_ok(cg, memory_gib=6):
    """Enforced limits: memory at most `memory_gib`, no swap, at most 128 tasks, at most two CPUs."""
    try:
        quota, period = cg["cpu.max"].split()
        return (int(cg["memory.max"]) <= memory_gib * GIB and cg["memory.swap.max"] == "0"
                and int(cg["pids.max"]) <= 128 and quota != "max" and int(quota) <= 2 * int(period))
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def require_bounded(memory_gib):
    cg = cgroup_record()
    if not cgroup_ok(cg, memory_gib):
        sys.exit(f"p172_openmc_fns: refusing to run: cgroup is not an enforced bounded scope ({cg})")
    return cg


def environment_record(cg):
    status = git("status", "--porcelain", "--untracked-files=no")
    return {"git_head": git("rev-parse", "HEAD"), "crates_tree": git("rev-parse", "HEAD:crates"),
            "clean_tree": status == "", "cgroup": cg,
            "harness_sha256": sha256_file(Path(__file__)), "driver_sha256": sha256_file(DRIVER),
            "head_to_head_script_sha256": sha256_file(H2H_SCRIPT), "cb2_script_sha256": sha256_file(CB2_SCRIPT),
            "groups_sha256": sha256_file(GROUPS)}


def write_atomic(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, sort_keys=True) + "\n")
    tmp.replace(path)


# ------------------------------------------------------------------------------------------------------ ACTINV arms

def run_actinv_arm(arm, out):
    """controls/cb2_fns.py, imported, with only the library, work directory and result path redirected. The sealed
    results/cb2_fns.json is never touched."""
    library, index = {"tendl2025": (TENDL25_NPZ, TENDL25_INDEX), "endf8": (ENDF8_NPZ, ENDF8_INDEX)}[arm]
    cg = require_bounded(6)
    os.environ["ACTINV_LIBRARY"] = str(library)
    os.environ["ACTINV_CB1_FNS_WORK"] = str(ROOT / "target" / f"{WORK_TAG}-actinv-{arm}")
    import cb2_fns  # noqa: E402  (loads the Python module named by ACTINV_PYTHON_MODULE at import)
    cb2_fns.RESULT = Path(out)
    cb2_fns.RESULT.parent.mkdir(parents=True, exist_ok=True)  # cb2_fns writes without creating it (P171 Amendment A)
    cb2_fns.EXPECTED["library"] = sha256_file(library)
    cb2_fns.EXPECTED["library_index"] = sha256_file(index)
    try:
        cb2_fns.main()
        exit_code = 0
    except SystemExit as stop:  # cb2_fns always ends with SystemExit; P171 lost its record block to it
        exit_code = stop.code if isinstance(stop.code, int) else (0 if stop.code is None else 1)
    record = json.loads(Path(out).read_text())
    record["p172"] = {"arm": arm, "module": str(ACTINV_MODULE), "module_sha256": sha256_file(ACTINV_MODULE),
                      "cb2_exit_code": exit_code, "environment": environment_record(cg)}
    write_atomic(out, record)


# ------------------------------------------------------------------------------------------------------ OpenMC arms

def isomer_branch_ratios(rows, sig, targets, flux_asc, namer, chain_names):
    """{reaction: {parent: {target: ratio}}} from a 709-group library (rows: target, MT, ZAP, LFS, LMF).

    For every target and every MT of MT_REACTION with product rows, the share of the flux-weighted rate that goes to
    each product state: LFS 0 is the ground state, LFS k > 0 is isomer k, `namer(Z, A, k)` its name. A state that is not
    a chain nuclide is folded into the ground state, and so is any remainder; shares are normalised to sum 1. Only
    parents with at least one isomer share above zero are returned."""
    flux = [float(v) for v in flux_asc]
    out = {}
    for t, info in enumerate(targets):
        parent = namer(info["za"] // 1000, info["za"] % 1000, info["liso"])
        if parent not in chain_names:
            continue
        for mt, reaction in MT_REACTION.items():
            sel = [i for i in range(len(rows)) if rows[i][0] == t and rows[i][1] == mt]
            total = [i for i in sel if rows[i][2] < 0]
            products = [i for i in sel if rows[i][2] >= 0]
            if len(total) != 1 or not products:
                continue
            rate_total = sum(f * s for f, s in zip(flux, sig[total[0]]))
            if rate_total <= 0.0:
                continue
            zaps = sorted({rows[i][2] for i in products})
            if len(zaps) != 1:
                continue  # more than one product ZA (a lumped channel): not an isomer split of one product
            zap = zaps[0]
            ground = namer(zap // 1000, zap % 1000, 0)
            if ground not in chain_names:
                continue
            ratios = {}
            for i in products:
                lfs = rows[i][3]
                state = ground if lfs <= 0 else namer(zap // 1000, zap % 1000, lfs)
                if state not in chain_names:
                    state = ground
                ratios[state] = ratios.get(state, 0.0) + sum(f * s for f, s in zip(flux, sig[i])) / rate_total
            ratios.setdefault(ground, 0.0)
            if not any(v > 0.0 for k, v in ratios.items() if k != ground):
                continue
            ratios[ground] += max(0.0, 1.0 - sum(ratios.values()))
            norm = sum(ratios.values())
            out.setdefault(reaction, {})[parent] = {k: v / norm for k, v in ratios.items()}
    return out


def make_isomer_chain(job_path):
    """Run with the OpenMC interpreter: the published chain plus isomer branching from the ENDF/B-VIII.1 library."""
    import numpy as np
    import openmc.data
    import openmc.deplete as d
    job = json.loads(Path(job_path).read_text())
    chain = d.Chain.from_xml(job["chain_file"])
    lib = np.load(job["library"])
    index = json.loads(Path(job["library_index"]).read_text())
    names = {n.name for n in chain.nuclides}
    ratios = isomer_branch_ratios(lib["rows"].tolist(), lib["sig"], index["targets"], job["flux_ascending"],
                                  lambda z, a, m: openmc.data.gnds_name(z, a, m), names)
    ratios = {k: v for k, v in ratios.items() if k in ISOMER_REACTIONS}
    for reaction, branch in ratios.items():
        chain.set_branch_ratios(branch, reaction=reaction, strict=False)
    chain.export_to_xml(job["out"])
    Path(job["out"] + ".ratios.json").write_text(json.dumps(ratios, indent=1, sort_keys=True))
    print(json.dumps({"reactions": {k: len(v) for k, v in ratios.items()}}))


def scoped(cmd):
    """`cmd` inside its own transient systemd scope with the AGENTS.md limits, so an out-of-memory kill takes only the
    driver process and not this harness."""
    return ["systemd-run", "--user", "--scope", "-q", "-p", f"MemoryMax={MEMORY_GIB}G", "-p", "MemorySwapMax=0",
            "-p", "TasksMax=128", "-p", "CPUQuota=200%", "--", *[str(c) for c in cmd]]


def run_chunk(base_job, cases, sub, env, depth=0):
    """One driver process for `cases` (same flux group). If it fails (an out-of-memory kill included) the chunk is split
    by material into halves and each half is run; a single material that still fails is recorded as a failure.
    Returns [(job, results or None, info)]."""
    sub.mkdir(parents=True, exist_ok=True)
    job = dict(base_job, work_dir=str(sub / "driver"), cases=cases)
    (sub / "job.json").write_text(json.dumps(job))
    t0 = time.monotonic()
    r = subprocess.run(scoped([OPENMC_PY, DRIVER, sub / "job.json"]), capture_output=True, text=True,
                       timeout=LIMIT_S, env=env)
    (sub / "driver.stdout").write_text(r.stdout)
    (sub / "driver.stderr").write_text(r.stderr)
    info = {"cases": len(cases), "wall_s": time.monotonic() - t0, "split_depth": depth, "returncode": r.returncode,
            "stderr_tail": r.stderr[-300:] if r.returncode else ""}
    if r.returncode == 0:
        results_path = sub / "driver" / "openmc_results.json"
        res = json.loads(results_path.read_text())
        info.update(results_sha256=sha256_file(results_path), session_init_s=res.get("session_init_s"),
                    nuclides_tracked=res.get("nuclides_tracked"),
                    xs_missing_reachable=len(res.get("xs_missing_reachable") or []),
                    dropped_seed_isotopes=res.get("dropped_seed_isotopes"))
        return [(job, res, info)]
    materials = sorted({c["case"].split("_")[0] for c in cases})
    if len(materials) == 1:
        return [(job, None, info)]
    half = set(materials[:len(materials) // 2])
    out = [(job, None, dict(info, split_into_halves=True))]
    for part, label in ((half, "a"), (set(materials) - half, "b")):
        out += run_chunk(base_job, [c for c in cases if c["case"].split("_")[0] in part], sub / label, env, depth + 1)
    return out


def run_openmc_arm(variant, out):
    import openmc_fns_headtohead as h
    cg = require_bounded(MEMORY_GIB)
    probe = subprocess.run([str(OPENMC_PY), "-c", "import openmc; print(openmc.__version__)"], capture_output=True,
                           text=True, timeout=120)
    version = probe.stdout.strip()
    if version != OPENMC_VERSION:
        sys.exit(f"p172_openmc_fns: expected OpenMC {OPENMC_VERSION}, interpreter reports {version!r}")
    work = ROOT / "target" / f"{WORK_TAG}-openmc-{variant}"
    work.mkdir(parents=True, exist_ok=True)
    experiments = sorted((d.name, p.stem) for d in h.FNS.iterdir() if d.is_dir() for p in d.glob("*.exp"))
    if len(experiments) != EXPERIMENTS:
        sys.exit(f"p172_openmc_fns: expected {EXPERIMENTS} experiments, found {len(experiments)}")
    job, meta = h.build_job(experiments, work)
    if REHEARSAL:  # the smallest flux-group chunk only
        sizes = {g["name"]: sum(c["group"] == g["name"] for c in job["cases"]) for g in job["groups"]}
        job["groups"] = [g for g in job["groups"] if g["name"] == min(sizes, key=sizes.get)]
    chain_base = CHAIN_P32_XML if variant == "p32" else CHAIN_XML
    env = dict(os.environ, OPENMC_CROSS_SECTIONS=str(XS_XML), OMP_NUM_THREADS="2", OPENBLAS_NUM_THREADS="1",
               MKL_NUM_THREADS="1", PYTHONHASHSEED="0", TMPDIR=str(work / "tmp"))
    (work / "tmp").mkdir(exist_ok=True)
    chunks = []  # (base job, cases, directory)
    for group in job["groups"]:
        sub = work / f"group_{group['name']}"
        sub.mkdir(exist_ok=True)
        chain_path = chain_base
        if variant == "isomer":
            chain_path = sub / "chain_isomer.xml"
            spec = {"chain_file": str(CHAIN_XML), "library": str(ENDF8_NPZ), "library_index": str(ENDF8_INDEX),
                    "flux_ascending": group["flux_descending"][::-1], "out": str(chain_path)}
            (sub / "chain_job.json").write_text(json.dumps(spec))
            r = subprocess.run(scoped([OPENMC_PY, Path(__file__).resolve(), "make-isomer-chain",
                                       sub / "chain_job.json"]), capture_output=True, text=True, timeout=3600, env=env)
            if r.returncode != 0:
                sys.exit(f"isomer chain failed:\n{r.stderr[-3000:]}")
        chunks.append((dict(job, chain_file=str(chain_path), groups=[group]),
                       [c for c in job["cases"] if c["group"] == group["name"]], sub))
    records, run_info, started = [], [], time.monotonic()
    for base_job, cases, sub in chunks:
        for sub_job, res, info in run_chunk(base_job, cases, sub, env):
            info["chain_file_sha256"] = sha256_file(Path(sub_job["chain_file"]))
            run_info.append(info)
            if res is None:
                continue
            hl, edec = res.get("half_life_s") or {}, res.get("decay_energy_ev") or {}
            for case in sub_job["cases"]:
                m, o = meta[case["case"]], res["cases"].get(case["case"]) or {}
                rec = {"material": m["material"], "experiment": m["experiment"], "status": o.get("status", "absent"),
                       "cooling_cum_s": m["cooling_cum_s"], "reason": o.get("reason")}
                if o.get("status") == "executed":
                    heat, no_energy = h.heat_uW_g(o["atoms_atom_per_cm3"], hl, edec)
                    rec.update(heat_uW_g=heat, deplete_s=o.get("deplete_s"),
                               n_decaying_without_energy=len(no_energy),
                               composition_dropped_from_chain=o.get("composition_dropped_from_chain"))
                records.append(rec)
    done = {(r["material"], r["experiment"]) for r in records}
    for material, experiment in experiments:  # a case that never ran is recorded, not omitted
        if (material, experiment) not in done:
            records.append({"material": material, "experiment": experiment, "status": "not_run",
                            "cooling_cum_s": meta[f"{material}_{experiment}"]["cooling_cum_s"], "reason": "chunk failed"})
    document = {"schema": SCHEMA + "-openmc", "arm": {"official": "openmc", "p32": ARM_OPENMC_P32,
                                                      "isomer": ARM_OPENMC_ISOMER}[variant],
                "openmc_version": version, "python_executable": str(OPENMC_PY),
                "chain_file": str(CHAIN_P32_XML if variant == "p32" else CHAIN_XML), "wall_s": time.monotonic() - started,
                "run": run_info, "environment": environment_record(cg),
                "records": sorted(records, key=lambda r: (r["material"], r["experiment"]))}
    write_atomic(out, document)


# ------------------------------------------------------------------------------------------------------ assembly

def match_cooling_index(cooling, t):
    """Index of the cooling boundary nearest to the measurement time and whether it matches within tolerance."""
    s = min(range(len(cooling)), key=lambda i: abs(cooling[i] - t))
    return s, abs(cooling[s] - t) <= max(BOUNDARY_TOL * t, 1.0)


def assemble(record_path):
    arm_a = json.loads((OUT_DIR / "actinv_tendl2025.json").read_text())
    arm_e = json.loads((OUT_DIR / "actinv_endf8.json").read_text())
    arm_o = json.loads((OUT_DIR / "openmc.json").read_text())
    optional = {}  # sensitivity arms that were run: key in the points -> arm file
    for name in (ARM_OPENMC_P32, ARM_OPENMC_ISOMER):
        path = OUT_DIR / f"{name}.json"
        if path.is_file():
            optional[name] = (path, json.loads(path.read_text()))
    by = lambda recs: {(r["material"], r["experiment"]): r for r in recs}  # noqa: E731
    e_by, o_by = by(arm_e["records"]), by(arm_o["records"])
    opt_by = {name: by(doc["records"]) for name, (_, doc) in optional.items()}
    experiments = []
    for rec in arm_a["records"]:
        key = (rec["material"], rec["experiment"])
        e_rec, o_rec = e_by.get(key), o_by.get(key)
        points = []
        for n, p in enumerate(rec["pairs"]):
            point = {"time_s": p["time_s"], "measured": p["measured_uW_g"], "sigma": p["sigma_uW_g"],
                     "actinv_tendl2025": p["actinv_tendl2025_uW_g"]}
            if e_rec is not None and n < len(e_rec["pairs"]):
                q = e_rec["pairs"][n]
                point["actinv_endf8"] = q["actinv_tendl2025_uW_g"]
                point["actinv_endf8_time_s"], point["actinv_endf8_measured"] = q["time_s"], q["measured_uW_g"]
            sources = [("openmc", o_rec)] + [(name, opt_by[name].get(key)) for name in optional]
            for name, src in sources:
                if src is not None and src.get("status") == "executed":
                    s, matched = match_cooling_index(src["cooling_cum_s"], p["time_s"])
                    point[name] = src["heat_uW_g"][s + 2] if s + 2 < len(src["heat_uW_g"]) else None
                    point[name + "_boundary_match"] = matched
            points.append(point)
        entry = {"material": key[0], "experiment": key[1], "n_points": len(points), "points": points,
                 "actinv_endf8_points": None if e_rec is None else len(e_rec["pairs"]),
                 "openmc_status": None if o_rec is None else o_rec["status"],
                 "openmc_deplete_s": None if o_rec is None else o_rec.get("deplete_s")}
        for name in optional:
            src = opt_by[name].get(key)
            entry[name + "_status"] = None if src is None else src["status"]
        experiments.append(entry)
    inputs = {name: {"path": str(path), "sha256": sha256_file(path)} for name, (path, _) in PINNED.items()}
    sealed = json.loads(SEALED_H2H.read_text())
    sealed_points = {}
    for r in sealed["records"]:
        for p in r["pairs"]:
            if "openmc_endfb8_uW_g" in p:
                sealed_points[(r["material"], r["experiment"], p["time_s"])] = p["openmc_endfb8_uW_g"]
    reproduction = []  # the sealed record used the P32 chain, so only the P32 arm is comparable with it
    for e in experiments:
        for p in e["points"]:
            ref = sealed_points.get((e["material"], e["experiment"], p["time_s"]))
            if ref and ref > 0 and (p.get(ARM_OPENMC_P32) or 0) > 0:
                reproduction.append(abs(math.log(p[ARM_OPENMC_P32] / ref)))
    env_a = arm_a["p172"]["environment"]
    document = {
        "schema": SCHEMA, "inputs": inputs,
        "environment": {**env_a, "module_sha256": arm_a["p172"]["module_sha256"],
                        "endf8_module_sha256": arm_e["p172"]["module_sha256"],
                        "openmc_version": arm_o["openmc_version"], "openmc_python": arm_o["python_executable"],
                        "openmc_environment": arm_o["environment"],
                        "actinv_versions": [arm_a.get("actinv_version"), arm_e.get("actinv_version")]},
        "actinv_records": {"tendl2025": {"checks": arm_a["checks"], "pass": arm_a["pass"],
                                         "sha256": sha256_file(OUT_DIR / "actinv_tendl2025.json")},
                           "endf8": {"checks": arm_e["checks"], "pass": arm_e["pass"],
                                     "sha256": sha256_file(OUT_DIR / "actinv_endf8.json")}},
        "openmc_run": {"run": arm_o["run"], "wall_s": arm_o["wall_s"],
                       "sha256": sha256_file(OUT_DIR / "openmc.json"),
                       "sensitivity_runs": {name: {"run": doc["run"], "sha256": sha256_file(path)}
                                            for name, (path, doc) in optional.items()}},
        "sensitivity_arms": sorted(optional),
        "reproduction_vs_openmc_0_16_record": (
            {"points": len(reproduction), "max_abs_log_ratio": max(reproduction) if reproduction else None}
            if ARM_OPENMC_P32 in optional else None),
        "experiments": experiments}
    write_atomic(record_path, document)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="mode", required=True)
    a = sub.add_parser("actinv")
    a.add_argument("--arm", choices=["tendl2025", "endf8"], required=True)
    o = sub.add_parser("openmc")
    o.add_argument("--chain-variant", choices=["official", "p32", "isomer"], default="official")
    sub.add_parser("assemble")
    m = sub.add_parser("make-isomer-chain")
    m.add_argument("job")
    args = ap.parse_args(argv)
    if args.mode == "actinv":
        run_actinv_arm(args.arm, OUT_DIR / f"actinv_{args.arm}.json")
    elif args.mode == "openmc":
        name = {"official": "openmc", "p32": ARM_OPENMC_P32, "isomer": ARM_OPENMC_ISOMER}[args.chain_variant]
        run_openmc_arm(args.chain_variant, OUT_DIR / f"{name}.json")
    elif args.mode == "assemble":
        assemble(RECORD)
    else:
        make_isomer_chain(args.job)


if __name__ == "__main__":
    main()
