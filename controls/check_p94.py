#!/usr/bin/env python3
"""P94 checker (protocols/ACTINV-P94_PROTOCOL.md): photonuclear (incident-gamma) activation.

    python3 controls/check_p94.py build    # target/p94/build.sh: fmt, clippy, test --release, release build
    python3 controls/check_p94.py g2       # G2(a) 783 P75b specs + 3 mesh profiles, G2(b) proton rebuild, G2(c) P32 import
    python3 controls/check_p94.py g3       # candidate builds all 2850 TENDL-2025 gamma files
    python3 controls/check_p94.py g4       # independent Python ENDF-6 parser + flat-lethargy integrator
    python3 controls/check_p94.py verdict  # assembles G0-G6 -- run by the lead, not by a subagent

Run under the 6 GB cgroup cap, e.g.:
    CARGO_BUILD_JOBS=3 TMPDIR=$PWD/target/preflight-tmp systemd-run --user --scope -q \
      -p MemoryMax=6G -p MemorySwapMax=0 -p TasksMax=128 -p CPUQuota=300% -- \
      python3 controls/check_p94.py g3

Logs land under target/p94/. G0 (protocol registration), G5 (controls/p94_g5_actinv.py plus the
FISPACT-II side) and G6 (CI replay) are the lead's own steps and are not computed here by `verdict`.
`verdict` must not be invoked by the implementing agent.
"""
from __future__ import annotations

import concurrent.futures as cf
import hashlib
import json
import math
import os
import re
import resource
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
MAIN = Path.home() / "Documents" / "actinv"
# ACTINV_GAMMA_PROTOCOL=P98 selects the P94 successor (protocols/ACTINV-P98_PROTOCOL.md): its own
# work directory and verdict, the same reference binary, and P98's added G1 tests and G3 identity.
# P100 (protocols/ACTINV-P100_PROTOCOL.md) is P98's successor: its own work directory/verdict and
# its own reference binary (target/p100/ref_actinv, per that protocol's Gates section -- P94 and
# P98 keep target/p94/ref_actinv).
GAMMA_PROTOCOL = os.environ.get("ACTINV_GAMMA_PROTOCOL", "P94")
if GAMMA_PROTOCOL not in ("P94", "P98", "P100", "P101"):
    sys.exit(f"unknown ACTINV_GAMMA_PROTOCOL {GAMMA_PROTOCOL}")
WORK = ROOT / "target" / GAMMA_PROTOCOL.lower()
REF = ROOT / "target" / (GAMMA_PROTOCOL.lower() if GAMMA_PROTOCOL in ("P100", "P101") else "p94") / "ref_actinv"
CAND = ROOT / "target" / "release" / "actinv"
SPECS = MAIN / "target" / "p75b" / "specs"
MESH = ["fe_coupled", "fe_p21like", "ss316_r2s"]
MESH_DIR = MAIN / "target" / "meshprof"
PROTOCOL = ROOT / "protocols" / f"ACTINV-{GAMMA_PROTOCOL}_PROTOCOL.md"
VERDICT = ROOT / "results" / f"{GAMMA_PROTOCOL.lower()}_verdict.json"
# P98 G3: with the default profile unchanged, the TENDL-2025 gamma library must be byte-identical
# to P94's candidate build.
P94_G3_NPZ_SHA256 = "d4590b8e65a4814e066083d0b211af36e7c9b2a016ab73a358790e8c2ce16e7d"
TIMING_KEYS = {"ms", "elapsed_ms", "wall_s", "wall_time_s", "cells_per_s"}
FOOTER_TIMING = ("wall_time_s", "cells_per_s")
ENV = {"RAYON_NUM_THREADS": "1", "PATH": "/usr/bin:/bin", "HOME": str(Path.home())}
WORKERS = 3

GAMMA_DATA = Path.home() / "nuclear-data" / "tendl-2025" / "files" / "g"
PROTON_DATA = Path.home() / "nuclear-data" / "tendl-2025" / "files" / "p"
P32_STATEPOINT = Path.home() / "nuclear-data" / "p32-work" / "chain" / "neutron" / "statepoint.10.h5"
P32_TALLY = "1"
P32_SOURCE_RATE = "1e9"

G1_UNIT_TESTS = (
    # activation.rs (actinv-data)
    "activation::tests::projectile_gamma_parse_name_nsub_za",
    "activation::tests::projectile_gamma_parses_from_endf_metadata",
    "activation::tests::projectile_gamma_rejects_nonzero_awi",
    "activation::tests::projectile_gamma_index_with_neutron_spec_fails_closed",
    "activation::tests::neutron_index_with_gamma_spec_fails_closed",
    "activation::tests::unsupported_nsub_fails_closed",
    # builder.rs (actinv-data)
    "builder::tests::gamma_residual_arithmetic_shifts_by_one_mass_unit",
    "builder::tests::gamma_missing_mf8_resolves_ground_state_from_mt_definition",
    "builder::tests::gamma_mt4_skipped_when_detail_present_but_resolved_when_sole_coverage",
    "builder::tests::gamma_photofission_yields_fail_closed",
    "builder::tests::gamma_build_options_reject_nonzero_temperature",
    "builder::tests::gamma_build_options_require_162_groups",
    # flux.rs (actinv-core)
    "flux::tests::openmc_photon_particle_filter_writes_photon_units_and_label",
    "flux::tests::openmc_unlabelled_and_neutron_filtered_tallies_stay_neutron_units_with_no_particle_key",
    "flux::tests::openmc_unsupported_particle_filter_fails_closed",
    "flux::tests::legacy_unlabelled_neutron_import_is_byte_identical_to_pre_p94",
    # mesh.rs (actinv-core)
    "mesh::tests::gamma_mesh_run_rejects_legacy_unlabelled_neutron_flux",
    "mesh::tests::neutron_mesh_run_rejects_particle_labelled_flux",
    "mesh::tests::gamma_mesh_run_rejects_unlabelled_particle_flux",
    "mesh::tests::charged_mesh_run_rejects_photon_labelled_flux",
    "spec::duration_tests::gamma_spec_requires_zero_kelvin_and_no_fission_yields",
)
# P100 G1 ("P100 includes everything P98 runs") carries P98's additions forward in full.
if GAMMA_PROTOCOL in ("P98", "P100", "P101"):
    G1_UNIT_TESTS += (
        "builder::tests::gamma_zero_izap_photofission_total_is_the_sentinel_under_a_profile",
        "builder::tests::gamma_zero_izap_photofission_total_fails_closed_without_a_profile",
        "builder::tests::gamma_zero_izap_photofission_rejects_other_shapes",
    )

# P100 G1 also adds unit tests whose leaf name starts with "p100_" (exact names are the
# implementer's choice; this checker does not hard-code them, it scans the full `cargo test`
# output -- see g1_p100_tests below). At least this many must run, and every one must pass.
P100_MIN_UNIT_TESTS = 5


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def rel(a, b):
    if b == 0:
        return None if a == 0 else math.inf
    return abs(a - b) / abs(b)


def strip_timing(v):
    if isinstance(v, dict):
        return {k: strip_timing(x) for k, x in v.items() if k not in TIMING_KEYS}
    if isinstance(v, list):
        return [strip_timing(x) for x in v]
    return v


def registered() -> bool:
    return f"{sha(PROTOCOL)}  protocols/{PROTOCOL.name}" in (ROOT / "protocols/protocol_hash.txt").read_text()


def g1_p100_tests(tests_text: str) -> dict:
    """P100 G1 addition: unit tests whose leaf name (after the last '::') starts with 'p100_'.
    These run as part of the same unfiltered `cargo test --release -p actinv-core -p actinv-data`
    sweep that target/p100/build.sh already captures to test.txt (mirroring P94/P98's build.sh),
    so no separate filtered `cargo test ... p100_` invocation is needed here -- every test in the
    crate, including any p100_-prefixed one, is already present in that output. At least
    P100_MIN_UNIT_TESTS must have run, and every one of them must have passed."""
    found = re.findall(r"^test (\S+) \.\.\. (ok|FAILED)$", tests_text, re.M)
    p100 = [(name, status) for name, status in found if name.rsplit("::", 1)[-1].startswith("p100_")]
    ok = len(p100) >= P100_MIN_UNIT_TESTS and all(status == "ok" for _, status in p100)
    return {"pass": ok, "n_ran": len(p100), "min_required": P100_MIN_UNIT_TESTS,
            "all_passed": all(status == "ok" for _, status in p100) if p100 else False,
            "tests": [{"name": name, "status": status} for name, status in p100]}


# ---------------------------------------------------------------- build (G1)

def cmd_build() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    build_sh = WORK / "build.sh"
    if not build_sh.exists():
        sys.exit(f"{build_sh} is missing")
    p = subprocess.run(["bash", str(build_sh)], cwd=ROOT)
    blog = (WORK / "build.log").read_text() if (WORK / "build.log").exists() else ""
    rc = dict(re.findall(r"^(fmt|clippy|test|release|pymodule|runtime) rc=(\d+)$", blog, re.M))
    tests = (WORK / "test.txt").read_text() if (WORK / "test.txt").exists() else ""
    unit = {name: re.search(rf"^test {re.escape(name)} \.\.\. ok$", tests, re.M) is not None
            for name in G1_UNIT_TESTS}
    runtime_path = WORK / "g1_gamma_runtime.json"
    runtime = json.loads(runtime_path.read_text()) if runtime_path.exists() else {"pass": False}
    p100_tests = g1_p100_tests(tests) if GAMMA_PROTOCOL in ("P100", "P101") else None
    g1 = (p.returncode == 0
          and all(rc.get(k) == "0" for k in ("fmt", "clippy", "test", "release", "pymodule", "runtime"))
          and all(unit.values()) and runtime.get("pass") is True
          and (p100_tests is None or p100_tests["pass"]))
    out = {"pass": g1, "build_returncode": p.returncode, "exit_codes": rc, "unit_tests": unit,
           "gamma_runtime": runtime,
           "candidate_sha256": sha(CAND) if CAND.exists() else None}
    if p100_tests is not None:
        out["p100_unit_tests"] = p100_tests
    (WORK / "g1_summary.json").write_text(json.dumps(out, indent=1, sort_keys=True))
    print(json.dumps(out, indent=1))
    return 0 if g1 else 1


# ---------------------------------------------------------------------- G2

def run_single(binary: Path, spec: Path) -> list:
    with tempfile.TemporaryDirectory(dir=WORK) as d:
        out = Path(d) / "out.json"
        p = subprocess.run([str(binary), "run", str(spec), str(out)], capture_output=True, text=True, env=ENV)
        if p.returncode != 0:
            return [p.returncode, p.stderr[-300:]]
        doc = strip_timing(json.loads(out.read_text()))
        return [0, hashlib.sha256(json.dumps(doc, sort_keys=True).encode()).hexdigest()]


def run_mesh(binary: Path, spec: Path, out: Path) -> dict:
    t0 = time.monotonic()
    p = subprocess.run([str(binary), "mesh", str(spec), str(out)], cwd=MAIN, capture_output=True, text=True, env=ENV)
    return {"returncode": p.returncode, "wall_s": time.monotonic() - t0, "stderr_tail": p.stderr[-300:]}


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
    for key in FOOTER_TIMING:
        footer.pop(key, None)
    return {"body_sha256": h.hexdigest(), "body_lines": lines, "footer": footer}


def g2a() -> dict:
    specs = sorted(SPECS.glob("*.json"))
    if not specs:
        sys.exit(f"no P75b specs found under {SPECS}")

    def both(spec: Path):
        return spec.name, run_single(REF, spec), run_single(CAND, spec)

    single = {}
    t0 = time.monotonic()
    with cf.ThreadPoolExecutor(WORKERS) as ex:
        for i, (name, r, c) in enumerate(ex.map(both, specs)):
            single[name] = {"ref": r, "cand": c}
            if i % 100 == 0:
                print(f"g2a single {i}/{len(specs)} {time.monotonic() - t0:.0f}s", flush=True)
    fails = [n for n, v in single.items() if v["ref"][0] != 0 or v["cand"][0] != 0 or v["ref"][1] != v["cand"][1]]

    mesh = {}
    for c in MESH:
        spec_path = MESH_DIR / f"{c}.json"
        base = json.loads(spec_path.read_text())
        variant = WORK / f"g2a_{c}.t1.json"
        variant.write_text(json.dumps({**base, "threads": 1}))
        outs = {}
        for tag, b in (("ref", REF), ("cand", CAND)):
            out = WORK / f"g2a_mesh_{c}.{tag}.ndjson"
            r = run_mesh(b, variant, out)
            if r["returncode"] == 0:
                r["digest"] = mesh_digest(out)
            out.unlink(missing_ok=True)
            outs[tag] = r
        ok = (outs["ref"]["returncode"] == 0 and outs["cand"]["returncode"] == 0
              and outs["ref"].get("digest") == outs["cand"].get("digest"))
        mesh[c] = {"pass": ok, "ref_returncode": outs["ref"]["returncode"],
                   "cand_returncode": outs["cand"]["returncode"],
                   "ref_digest": outs["ref"].get("digest"), "cand_digest": outs["cand"].get("digest")}
        print("g2a mesh", c, ok, flush=True)

    ok = len(single) == 783 and not fails and all(v["pass"] for v in mesh.values())
    (WORK / "g2a_single.json").write_text(json.dumps(single, indent=1, sort_keys=True))
    return {"pass": ok, "specs": len(single), "differing_or_failed": fails[:20], "mesh": mesh}


# Named builder-identity fields that legitimately differ between two binaries built from the
# same source at different times/paths (per P92/P94 precedent): builder_fingerprint embeds a
# build timestamp/toolchain path in some configurations. Everything else in the index must match.
G2B_IDENTITY_FIELDS = {"builder_fingerprint"}


def g2b() -> dict:
    if not GAMMA_DATA.parent.joinpath("p").exists():
        return {"pass": False, "error": f"proton source not found: {PROTON_DATA}"}
    results = {}
    npz_paths = {}
    idx_paths = {}
    for tag, binary in (("ref", REF), ("cand", CAND)):
        out = WORK / f"g2b_proton_{tag}.npz"
        idx = WORK / f"g2b_proton_{tag}_index.json"
        p = subprocess.run(
            [str(binary), "build-library", str(PROTON_DATA), str(out),
             "--format", "tendl", "--projectile", "proton", "--groups", "fispact-162",
             "--temperature-K", "0", "--workers", "3",
             # The frozen P18b state-sum envelope (added after P10's proton build) refuses a few
             # TENDL-2025 proton files, e.g. p-Ag096, in every binary. The same option on both
             # sides records them in the index's build_failures instead of aborting, and the
             # index comparison below covers that list too.
             "--continue-on-error", "true"],
            capture_output=True, text=True, env=ENV,
        )
        results[tag] = {"returncode": p.returncode, "stdout_tail": p.stdout[-500:], "stderr_tail": p.stderr[-500:]}
        npz_paths[tag] = out
        idx_paths[tag] = idx
    if results["ref"]["returncode"] != 0 or results["cand"]["returncode"] != 0:
        return {"pass": False, "builds": results}
    npz_identical = npz_paths["ref"].exists() and npz_paths["cand"].exists() and \
        sha(npz_paths["ref"]) == sha(npz_paths["cand"])
    index_diff = None
    index_identical_apart_from_identity = False
    if idx_paths["ref"].exists() and idx_paths["cand"].exists():
        ref_idx = json.loads(idx_paths["ref"].read_text())
        cand_idx = json.loads(idx_paths["cand"].read_text())

        def strip_identity(doc):
            if isinstance(doc, dict):
                return {k: strip_identity(v) for k, v in doc.items() if k not in G2B_IDENTITY_FIELDS}
            if isinstance(doc, list):
                return [strip_identity(v) for v in doc]
            return doc

        index_identical_apart_from_identity = strip_identity(ref_idx) == strip_identity(cand_idx)
        if not index_identical_apart_from_identity:
            index_diff = {"ref_keys": sorted(ref_idx.keys()), "cand_keys": sorted(cand_idx.keys())}
    ok = npz_identical and index_identical_apart_from_identity
    return {"pass": ok, "npz_identical": npz_identical,
            "index_identical_apart_from_identity": index_identical_apart_from_identity,
            "identity_fields_excluded": sorted(G2B_IDENTITY_FIELDS),
            "index_diff": index_diff, "builds": results,
            "ref_npz_sha256": sha(npz_paths["ref"]) if npz_paths["ref"].exists() else None,
            "cand_npz_sha256": sha(npz_paths["cand"]) if npz_paths["cand"].exists() else None}


def g2c() -> dict:
    if not P32_STATEPOINT.exists():
        return {"pass": False, "error": f"P32 statepoint not found: {P32_STATEPOINT}"}
    outs = {}
    for tag, binary in (("ref", REF), ("cand", CAND)):
        out = WORK / f"g2c_p32_{tag}.ndjson"
        p = subprocess.run(
            [str(binary), "import-flux", "openmc", str(P32_STATEPOINT), str(out),
             "--tally", P32_TALLY, "--source-rate", P32_SOURCE_RATE],
            capture_output=True, text=True, env=ENV,
        )
        outs[tag] = {"returncode": p.returncode, "path": out, "stderr_tail": p.stderr[-300:]}
    ok = (outs["ref"]["returncode"] == 0 and outs["cand"]["returncode"] == 0
          and outs["ref"]["path"].exists() and outs["cand"]["path"].exists()
          and sha(outs["ref"]["path"]) == sha(outs["cand"]["path"]))
    return {"pass": ok,
            "ref_returncode": outs["ref"]["returncode"], "cand_returncode": outs["cand"]["returncode"],
            "ref_sha256": sha(outs["ref"]["path"]) if outs["ref"]["path"].exists() else None,
            "cand_sha256": sha(outs["cand"]["path"]) if outs["cand"]["path"].exists() else None}


def cmd_g2() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    if not REF.exists() or not CAND.exists():
        sys.exit(f"reference ({REF}) or candidate ({CAND}) binary is missing")
    a = g2a()
    (WORK / "g2a_summary.json").write_text(json.dumps(a, indent=1, sort_keys=True))
    print("g2a", json.dumps({k: v for k, v in a.items() if k != "differing_or_failed" or True}, indent=1))
    b = g2b()
    (WORK / "g2b_summary.json").write_text(json.dumps(b, indent=1, sort_keys=True))
    print("g2b", json.dumps(b, indent=1))
    c = g2c()
    (WORK / "g2c_summary.json").write_text(json.dumps(c, indent=1, sort_keys=True))
    print("g2c", json.dumps(c, indent=1))
    ok = a["pass"] and b["pass"] and c["pass"]
    result = {"pass": ok, "a_p75b_and_mesh": a, "b_proton_rebuild": b, "c_p32_import": c}
    (WORK / "g2_summary.json").write_text(json.dumps(result, indent=1, sort_keys=True))
    print(json.dumps({"pass": ok}, indent=1))
    return 0 if ok else 1


# ---------------------------------------------------------------------- G3

DECAY_PRIMARY = MAIN / "actinv-data" / "v1.1.0" / "decay" / "endf-b-viii-0_decay.dat"
DECAY_FALLBACK = MAIN / "actinv-data" / "v1.1.0" / "decay" / "jeff-3-3_decay.dat"
# P100 G3: the prefix every "MF=3 threshold extension" ledger line must start with, naming the MT
# it was applied to (protocols/ACTINV-P100_PROTOCOL.md G3; the same prefix controls/p94_g4_endf.py
# matches for its own ledger-count comparison).
P100_EXTENSION_LEDGER_PREFIX = re.compile(r"^MT(\d+): MF=3 threshold extension:")


def read_npz_library(npz_path: Path):
    """Plain zip-of-npy read (rows/sig/bounds), same layout as p94_g4_endf.read_npz_library."""
    import zipfile
    import numpy.lib.format as npfmt
    with zipfile.ZipFile(npz_path) as zf:
        with zf.open("rows.npy") as f:
            rows = npfmt.read_array(f)
        with zf.open("sig.npy") as f:
            sig = npfmt.read_array(f)
        with zf.open("bounds.npy") as f:
            bounds = npfmt.read_array(f)
    return rows, sig, bounds


def g3_p100_extension_vs_p98_baseline(cand_npz: Path, cand_idx: dict) -> dict:
    """P100 G3: the TENDL-2025 gamma .npz must equal P98's candidate build (the frozen
    P94_G3_NPZ_SHA256 baseline) row for row, except rows whose (target ZA, MT) carries an
    'MF=3 threshold extension' ledger line in the candidate's own index. Rows are keyed by
    (target ZA, MT, ZAP, LFS, LMF); because rows can repeat under one key, the comparison is of
    the multiset of sig rows per key (exact float equality), not a single row per key."""
    baseline_npz = ROOT / "target" / "p98" / "g3_gamma.npz"
    baseline_idx_path = baseline_npz.with_name(baseline_npz.stem + "_index.json")
    report = {"pass": False, "baseline_npz": str(baseline_npz), "baseline_index": str(baseline_idx_path)}
    if not baseline_npz.exists() or not baseline_idx_path.exists():
        report["error"] = f"P98 baseline missing: {baseline_npz} and/or {baseline_idx_path}"
        return report
    baseline_sha256 = sha(baseline_npz)
    report["baseline_npz_sha256"] = baseline_sha256
    report["baseline_sha256_verified"] = baseline_sha256 == P94_G3_NPZ_SHA256
    if not report["baseline_sha256_verified"]:
        report["error"] = (f"P98 baseline g3_gamma.npz sha256 {baseline_sha256} != pinned "
                            f"{P94_G3_NPZ_SHA256}")
        return report

    cand_rows, cand_sig, cand_bounds = read_npz_library(cand_npz)
    base_rows, base_sig, base_bounds = read_npz_library(baseline_npz)
    base_idx = json.loads(baseline_idx_path.read_text())
    bounds_identical = bool(np.array_equal(cand_bounds, base_bounds))

    def keyed(rows, sig, idx):
        # Each sig row is reduced to the SHA-256 of its exact float64 bytes, so the multiset
        # comparison stays exact while ~5e5 x 162 values are never held as Python floats (the
        # first P100 G3 run, which did, was killed by the 6 GB cap before any comparison).
        za_by_index = [t["za"] for t in idx["targets"]]
        contiguous = np.ascontiguousarray(sig, dtype="<f8")
        out = {}
        for i in range(rows.shape[0]):
            ti, mt, zap, lfs, lmf = (int(x) for x in rows[i])
            key = (za_by_index[ti], mt, zap, lfs, lmf)
            out.setdefault(key, []).append(hashlib.sha256(contiguous[i].tobytes()).digest())
        return out

    cand_by_key = keyed(cand_rows, cand_sig, cand_idx)
    base_by_key = keyed(base_rows, base_sig, base_idx)

    extended_pairs = set()
    n_extension_lines = 0
    for t in cand_idx.get("targets", []):
        za = t["za"]
        for line in t.get("ledger", []):
            m = P100_EXTENSION_LEDGER_PREFIX.match(line)
            if m:
                n_extension_lines += 1
                extended_pairs.add((za, int(m.group(1))))

    diffs = []
    unexplained = []
    for key in sorted(set(cand_by_key) | set(base_by_key)):
        za, mt, zap, lfs, lmf = key
        if sorted(cand_by_key.get(key, [])) == sorted(base_by_key.get(key, [])):
            continue
        diffs.append(key)
        if (za, mt) not in extended_pairs:
            unexplained.append(key)

    changed_pairs = sorted({(za, mt) for za, mt, zap, lfs, lmf in diffs})
    ok = bounds_identical and not unexplained
    report.update({
        "pass": ok,
        "bounds_identical": bounds_identical,
        "n_extension_ledger_lines": n_extension_lines,
        "n_changed_target_mt_pairs": len(changed_pairs),
        "changed_target_mt_pairs_sample": [list(p) for p in changed_pairs[:20]],
        "n_diff_keys": len(diffs),
        "n_unexplained_diff_keys": len(unexplained),
        "unexplained_examples": [list(k) for k in unexplained[:20]],
        "diff_examples": [list(k) for k in diffs[:20]],
    })
    return report


def cmd_g3() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    if not CAND.exists():
        sys.exit(f"candidate binary ({CAND}) is missing")
    if not GAMMA_DATA.exists():
        sys.exit(f"gamma source not found: {GAMMA_DATA}")
    targets = sorted(GAMMA_DATA.glob("*"))
    out = WORK / "g3_gamma.npz"
    out.unlink(missing_ok=True)
    idx_path = WORK / "g3_gamma_index.json"
    idx_path.unlink(missing_ok=True)

    t0 = time.monotonic()
    usage_before = resource.getrusage(resource.RUSAGE_CHILDREN)
    p = subprocess.run(
        ["/usr/bin/time", "-v", str(CAND), "build-library", str(GAMMA_DATA), str(out),
         "--format", "tendl", "--projectile", "gamma", "--groups", "fispact-162",
         "--temperature-K", "0", "--workers", "3",
         # Isomer ordinals come from the decay data the runtime pairs with the library. Without
         # it the builder falls back to TENDL's isomeric-target catalog plus rank mapping, which
         # can put two physical states on one isomer label (e.g. Ta-182 16 keV and 519 keV).
         "--decay", str(DECAY_PRIMARY), "--decay-fallback", str(DECAY_FALLBACK)],
        capture_output=True, text=True, env=ENV,
    )
    wall_s = time.monotonic() - t0
    usage_after = resource.getrusage(resource.RUSAGE_CHILDREN)
    peak_rss_kb = None
    m = re.search(r"Maximum resident set size \(kbytes\):\s*(\d+)", p.stderr)
    if m:
        peak_rss_kb = int(m.group(1))
    else:
        peak_rss_kb = max(usage_before.ru_maxrss, usage_after.ru_maxrss)

    build_ok = p.returncode == 0 and out.exists() and idx_path.exists()
    report = {
        "pass": False, "returncode": p.returncode, "wall_s": wall_s, "peak_rss_kb": peak_rss_kb,
        "n_targets_on_disk": len(targets), "stdout_tail": p.stdout[-1000:], "stderr_tail": p.stderr[-2000:],
    }
    if not build_ok:
        (WORK / "g3_summary.json").write_text(json.dumps(report, indent=1, sort_keys=True))
        print(json.dumps(report, indent=1))
        return 1

    idx = json.loads(idx_path.read_text())
    target_list = idx.get("targets", [])
    all_ledger = [entry for t in target_list for entry in t.get("ledger", [])]
    missing_mf8_rows = sum(1 for entry in all_ledger if "missing_mf8_ground_state_delta" in entry)
    mt4_dedup_skips = sum(1 for entry in all_ledger if entry.startswith("MT4: gamma total"))
    detail_skips_for_declared_mt4 = sum(1 for entry in all_ledger if "duplicates MT4, which carries" in entry)
    # Protocol G3: zero silent unsupported fallbacks and zero convergence flags, defined as in
    # P10's build gate (controls/g7_p10_builds.py): ledger lines naming "unsupported" or "converg".
    unsupported = [entry for entry in all_ledger if "unsupported" in entry.lower()]
    convergence = [entry for entry in all_ledger if "converg" in entry.lower()]
    photofission_sentinel_targets = sum(
        1 for t in target_list
        if any(e.startswith("MT18: no MF=3 total; the MF=10 IZAP=-1 total-fission sentinel") for e in t.get("ledger", []))
    )
    mt_set = set()
    zap_set = set()
    for t in target_list:
        for sm in t.get("state_mappings", []):
            mt_set.add(sm.get("mt"))
            zap_set.add(sm.get("zap"))
    build_failures = idx.get("build_failures", [])

    # Smoke-test: the library must load in a gamma spec (minimal one-step prepared run), using
    # the real actinv-spec-1 schema (crates/actinv-cli/data/iron-example.json as the template):
    # library/decay paths, material.composition keyed by an explicit SymbolA nuclide with
    # basis="atoms_per_g", spectrum.structure="fispact-162" (162 groups, matching the gamma
    # library), and a one-step schedule.
    smoke_spec = WORK / "g3_smoke_spec.json"
    smoke_out = WORK / "g3_smoke_out.json"
    smoke = {"pass": False}
    fe56 = next((t for t in target_list if t["za"] == 26056), None)
    if fe56 is not None:
        ngroups = 162
        flux_per_group = [0.0] * ngroups
        flux_per_group[ngroups // 2] = 1.0e10
        spec = {
            "spec": "actinv-spec-1",
            "title": "P94 G3 smoke test",
            "projectile": "gamma",
            "library": {"path": str(out), "sha256": sha(out)},
            "decay": {
                "primary": str(MAIN / "actinv-data" / "v1.1.0" / "decay" / "endf-b-viii-0_decay.dat"),
                "fallback": str(MAIN / "actinv-data" / "v1.1.0" / "decay" / "jeff-3-3_decay.dat"),
            },
            "material": {"mass_g": 1.0, "basis": "atoms_per_g", "composition": {"Fe56": 1.0e20}},
            "spectrum": {"structure": "fispact-162", "flux_per_group": flux_per_group},
            "schedule": [{"dt": "3600.0 s", "flux": 1.0}, {"dt": "3600.0 s", "flux": 0.0}],
            "options": {"mode": "auto", "prune": "rate", "bmin_atoms_per_g": 1e-08, "temperature_K": 0.0},
        }
        smoke_spec.write_text(json.dumps(spec))
        sp = subprocess.run([str(CAND), "run", str(smoke_spec), str(smoke_out)], capture_output=True, text=True, env=ENV)
        smoke = {"pass": sp.returncode == 0, "returncode": sp.returncode, "za": 26056,
                  "stdout_tail": sp.stdout[-500:], "stderr_tail": sp.stderr[-800:]}
    else:
        smoke = {"pass": False, "error": "Fe-56 (ZA 26056) not found in the built target list"}

    npz_sha256 = sha(out)
    identical_to_p94 = npz_sha256 == P94_G3_NPZ_SHA256
    ok = (p.returncode == 0 and not build_failures and len(target_list) == len(targets) == 2850
          and not unsupported and not convergence and smoke["pass"]
          and (GAMMA_PROTOCOL != "P98" or identical_to_p94))
    p100_extension = None
    if GAMMA_PROTOCOL in ("P100", "P101"):
        p100_extension = g3_p100_extension_vs_p98_baseline(out, idx)
        ok = ok and p100_extension["pass"]
    report.update({
        "pass": ok,
        "n_targets_built": len(target_list),
        "n_build_failures": len(build_failures),
        "build_failures_sample": build_failures[:10],
        "n_rows": idx.get("n_rows"),
        "n_distinct_mt": len(mt_set),
        "n_distinct_zap": len(zap_set),
        "n_rows_resolved_by_missing_mf8_rule": missing_mf8_rows,
        "n_mt4_dedup_skips": mt4_dedup_skips,
        "n_mt50_91_skips_for_declared_mt4": detail_skips_for_declared_mt4,
        "unsupported_ledger_entries": unsupported[:20],
        "n_unsupported_ledger_entries": len(unsupported),
        "convergence_ledger_entries": convergence[:20],
        "n_convergence_ledger_entries": len(convergence),
        "n_photofission_sentinel_only_targets": photofission_sentinel_targets,
        "npz_sha256": npz_sha256,
        "npz_identical_to_p94_candidate_build": identical_to_p94,
        "smoke_test_gamma_spec": smoke,
    })
    if p100_extension is not None:
        report["p100_mf3_extension_vs_p98_baseline"] = p100_extension
    (WORK / "g3_summary.json").write_text(json.dumps(report, indent=1, sort_keys=True))
    print(json.dumps({k: v for k, v in report.items() if k not in ("stdout_tail", "stderr_tail")}, indent=1))
    return 0 if ok else 1


# ---------------------------------------------------------------------- G4

def cmd_g4() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    script = ROOT / "controls" / "p94_g4_endf.py"
    if not script.exists():
        sys.exit(f"{script} is missing")
    lib = WORK / "g3_gamma.npz"
    if not lib.exists():
        sys.exit(f"{lib} is missing; run g3 first to build the gamma library")

    if GAMMA_PROTOCOL not in ("P100", "P101"):
        p = subprocess.run([sys.executable, str(script), "--library", str(lib),
                             "--out", str(WORK / "g4_summary.json")])
        if (WORK / "g4_summary.json").exists():
            print((WORK / "g4_summary.json").read_text())
        return p.returncode

    # P100 G4: the existing TENDL-2025 run (default --dataset/--profile, unchanged), plus a
    # second independent run on the 8 TENDL-2017 inputs against the --profile tendl G5 build
    # (target/p100/g5_gamma_2017.npz, built by controls/p94_g5_actinv.py under
    # ACTINV_GAMMA_PROTOCOL=P100). Both must pass. Combined into g4_summary.json so cmd_verdict
    # needs no protocol-specific change.
    out2025 = WORK / "g4_2025_summary.json"
    p1 = subprocess.run([sys.executable, str(script), "--library", str(lib), "--out", str(out2025)])
    r2025 = json.loads(out2025.read_text()) if out2025.exists() else {"pass": False, "returncode": p1.returncode}

    lib2017 = WORK / "g5_gamma_2017.npz"
    out2017 = WORK / "g4_2017_summary.json"
    if not lib2017.exists():
        r2017 = {"pass": False, "error": f"{lib2017} is missing; build the TENDL-2017 G5 library "
                                          f"first (ACTINV_GAMMA_PROTOCOL=P100 "
                                          f"python3 controls/p94_g5_actinv.py)"}
    else:
        p2 = subprocess.run([sys.executable, str(script), "--library", str(lib2017),
                              "--dataset", "tendl2017", "--profile-normalize", "--out", str(out2017)])
        r2017 = json.loads(out2017.read_text()) if out2017.exists() else {"pass": False, "returncode": p2.returncode}

    combined = {"pass": bool(r2025.get("pass")) and bool(r2017.get("pass")),
                "tendl2025": r2025, "tendl2017": r2017}
    (WORK / "g4_summary.json").write_text(json.dumps(combined, indent=1, sort_keys=True))
    print(json.dumps({"pass": combined["pass"], "tendl2025_pass": r2025.get("pass"),
                       "tendl2017_pass": r2017.get("pass")}, indent=1))
    return 0 if combined["pass"] else 1


# ------------------------------------------------------------------- verdict

def cmd_verdict() -> int:
    """Assembles G0-G6. Not run by the implementing agent."""
    g1 = json.loads((WORK / "g1_summary.json").read_text())
    g2 = json.loads((WORK / "g2_summary.json").read_text())
    g3 = json.loads((WORK / "g3_summary.json").read_text())
    g4 = json.loads((WORK / "g4_summary.json").read_text())
    # G5 is the cross-code comparison (target/p94/g5.json, written by the lead's FISPACT-side
    # script from g5_actinv.json and the extracted gxs-162 records); g5_actinv.json alone is
    # only ACTINV's side of it.
    g5 = json.loads((WORK / "g5.json").read_text()) if (WORK / "g5.json").exists() else {"pass": False, "note": "NOT RUN"}
    replay = WORK / "ci_replay_summary.log"
    steps = re.findall(r"^STEP (\d+) (\S+)$", replay.read_text(), re.M) if replay.exists() else []
    failed = [name for code, name in steps if code != "0"]
    g6 = {"pass": bool(steps) and not failed, "steps": len(steps), "failed": failed}
    verdict = {
        "protocol": f"ACTINV-{GAMMA_PROTOCOL}",
        "inputs": {"protocol_sha256": sha(PROTOCOL), "candidate_sha256": sha(CAND) if CAND.exists() else None,
                   "reference_sha256": sha(REF) if REF.exists() else None},
        "G0": {"pass": registered()},
        "G1": g1, "G2": g2, "G3": g3, "G4": g4, "G5": g5, "G6": g6,
    }
    verdict["pass"] = all(verdict[g].get("pass") for g in ("G0", "G1", "G2", "G3", "G4", "G5", "G6"))
    VERDICT.write_text(json.dumps(verdict, indent=1, sort_keys=True) + "\n")
    print(json.dumps(verdict, indent=1))
    return 0


if __name__ == "__main__":
    fn = {"build": cmd_build, "g2": cmd_g2, "g3": cmd_g3, "g4": cmd_g4, "verdict": cmd_verdict}
    if len(sys.argv) < 2 or sys.argv[1] not in fn:
        sys.exit(__doc__)
    sys.exit(fn[sys.argv[1]]())
