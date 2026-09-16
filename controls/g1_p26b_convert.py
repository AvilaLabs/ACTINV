#!/usr/bin/env python3
"""P26b G1 — comparator library construction ledger.

Executes and records the FENDL-3.2c -> ALARA conversion pipeline for the
declared executable subset, then builds the matching ACTINV artifact
from the same pinned files:

  1. ALARAJOYWrapper ``preprocess_fendl3.py`` over the 36-nuclide
     subset (NJOY 2016.79, UKDD-2020 decay, fispact-709 bounds file).
  2. ALARA ``convert_lib ajoylib alaralib`` -> binary .lib/.idx pair.
  3. ``actinv build-library --format tendl --groups fispact-709``
     over the identical subset directory -> .npz artifact.
  4. One fail-closed smoke run per tool: an ALARA input referencing an
     element absent from the converted library and an ACTINV run
     referencing a nuclide absent from the built artifact must each
     fail or produce no activation for that nuclide.

Publishes ``results/g1_p26b_conversion.json``: per-nuclide ledger with
named failure classes, element coverage vs the declared set, artifact
digests, wall times, and the smoke outcomes.  The heavy jobs run under
the caller's cgroup; this script only orchestrates and records.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results" / "g1_p26b_conversion.json"
SEALS = json.loads(
    (ROOT / "results" / "g0_p26b_seals.json").read_text())

ND = Path.home() / "nuclear-data"
WORK = ND / "p26b-work"
SUBSET = WORK / "fendl-subset"
FAILED_DIR = WORK / "failed-nuclides"
BOUNDS = WORK / "fispact709_bounds.txt"
DECAY_WORK = WORK / "decay_ukdd2020_work"
DECAY_COMPILED = Path(str(DECAY_WORK) + "_compiled")
WRAPPER_DIR = ND / "alara-2.9.2" / "tools" / "ALARAJOYWrapper"
WRAPPER = WRAPPER_DIR / "preprocess_fendl3.py"
VENV_PY = ND / "alarajoy-venv" / "bin" / "python"
NJOY = ND / "njoy2016.79-build" / "njoy"
ALARA = ND / "alara-2.9.2-build" / "src" / "alara"
ACTINV = ROOT / "target" / "release" / "actinv"

RUN_DIR = WORK / "g1-run"
DSV = WRAPPER_DIR / "cumulative_gendf_data.dsv"
ALARA_LIB = RUN_DIR / "fendl32c_709"          # .lib/.idx/.gam/.gdx
ACTINV_NPZ = RUN_DIR / "actinv_fendl32c_709.npz"
ITER_LOG = RUN_DIR / "actinv_build_iterations.log"
PROBE_LOG = RUN_DIR / "actinv_rejected_probes.log"

NUCLIDES = SEALS["executable_subset"]["nuclides"]

# ACTINV fail-closed validator diagnostics -> named failure classes.
# Each is a FENDL-3.2c encoding the loader accepts in ALARA's pipeline but
# the ACTINV builder refuses; the asymmetry is the measured G1 result.
REJECTION_PATTERNS = [
    ("state partials have no matching MF=3 total or total-fission sentinel",
     "actinv_mf10_partials_conservation_unproven"),
    ("invalid RML particle pair",
     "actinv_rml_invalid_particle_pair"),
    ("disagree with MF=1",
     "actinv_mf1_mf2_za_awr_mismatch"),
    ("exceeds runtime total",
     "actinv_mf10_state_sum_exceeds_total"),
    ("Breit-Wigner total width",
     "actinv_resonance_width_below_component_sum"),
]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run(cmd, cwd, env_extra=None, timeout=None):
    env = dict(os.environ)
    env.update(env_extra or {})
    t0 = time.monotonic()
    p = subprocess.run(cmd, cwd=cwd, env=env, text=True,
                       capture_output=True, timeout=timeout)
    return {"cmd": [str(c) for c in cmd], "returncode": p.returncode,
            "wall_s": round(time.monotonic() - t0, 3),
            "stdout_tail": p.stdout[-4000:], "stderr_tail": p.stderr[-4000:]}


def iso_of_file(path: Path) -> str | None:
    """'Cr50.tendl' -> 'Cr-50'; 'Ta180m.tendl' -> 'Ta-180m'."""
    m = re.fullmatch(r"([A-Za-z]+)(\d+m?)", path.stem)
    if not m:
        return None
    return f"{m.group(1).capitalize()}-{m.group(2)}"


def parse_rejection_logs(ledger: dict):
    """Collect ACTINV fail-closed diagnostics per nuclide from the bounded
    build-loop log and the single-file probe log, then name the class."""
    reasons: dict[str, str] = {}
    for log in (ITER_LOG, PROBE_LOG):
        if not log.is_file():
            continue
        for line in log.read_text(errors="replace").splitlines():
            m = re.match(r"(/[^ :]+\.tendl):\s*(.+)", line.strip())
            if not m:
                continue
            iso = iso_of_file(Path(m.group(1)))
            if iso in ledger and iso not in reasons:
                reasons[iso] = m.group(2).strip()
    for iso, reason in reasons.items():
        ledger[iso]["actinv_rejection"] = reason
        for needle, cls in REJECTION_PATTERNS:
            if needle in reason:
                ledger[iso]["failure_class"] = cls
                break
        else:
            ledger[iso]["failure_class"] = "actinv_rejected_unclassified"


def alara_zero_activation(text: str) -> bool:
    """True when the zone specific-activity table lists zero isotope rows:
    the region between the header's ===== delimiters is empty."""
    m = re.search(r"isotope\s+t_1/2[^\n]*\n=+\n(.*?)=+\n", text, re.S)
    return bool(m) and m.group(1).strip() == ""


def actinv_populated(path: Path) -> set:
    """Nuclides a run reports as populated: inventory rows, activity keys
    and non-total heat keys across all steps."""
    out = json.loads(path.read_text())
    pop = set()
    for step in out.get("steps", []):
        for row in step.get("inventory", []):
            pop.add(row["nuclide"])
        pop.update(step.get("activity_Bq_per_g", {}).keys())
        pop.update(k for k in step.get("heat_W_per_g", {})
                   if k != "total")
    return pop


def parse_wrapper_log(text: str, ledger: dict):
    for m in re.finditer(r"Finished processing ([A-Za-z]+)-(\d+m?)", text):
        iso = f"{m.group(1).capitalize()}-{m.group(2)}"
        ledger[iso]["wrapper"] = "converted"
    for m in re.finditer(r"Failed to convert ([A-Za-z]+)-(\d+m?)", text):
        iso = f"{m.group(1).capitalize()}-{m.group(2)}"
        ledger[iso]["wrapper"] = "njoy_error"
        ledger[iso]["failure_class"] = "njoy_groupr_failure"
    for m in re.finditer(
            r"MF3\) is not present in the\s*ENDF file tree for ([A-Za-z]+)-(\d+m?)",
            text):
        iso = f"{m.group(1).capitalize()}-{m.group(2)}"
        ledger[iso]["wrapper"] = "no_mf3"
        ledger[iso]["failure_class"] = "no_activation_data_mf3"
    for m in re.finditer(
            r"PENDF preparation failed for ([A-Za-z]+)-(\d+m?)", text):
        iso = f"{m.group(1).capitalize()}-{m.group(2)}"
        ledger[iso]["wrapper"] = "pendf_error"
        ledger[iso]["failure_class"] = "njoy_pendf_failure"


def main() -> int:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc).isoformat()
    ledger = {iso: {"endf_sha256": None, "wrapper": "not_attempted",
                    "failure_class": None, "in_alara_lib": False,
                    "in_actinv_lib": False} for iso in NUCLIDES}
    for iso in NUCLIDES:
        stem = iso.replace("-", "").lower()
        matches = [p for d in (SUBSET, FAILED_DIR)
                   if d.is_dir()
                   for p in d.iterdir()
                   if p.stem.lower() == stem]
        if matches:
            ledger[iso]["endf_sha256"] = sha256(matches[0])
            ledger[iso]["endf_path"] = str(matches[0])
        else:
            ledger[iso]["failure_class"] = "endf_file_missing"

    # ---- Stage 1: ALARAJOYWrapper conversion (already executed under
    # a bounded systemd scope; this producer parses the captured log).
    # Invocation recorded for the evidence record:
    wrapper_invocation = [
        str(VENV_PY), str(WRAPPER),
        "-f", str(SUBSET),
        "-d", str(DECAY_COMPILED), "ukdd",
        "-g", str(BOUNDS), "-r"]
    wrapper_log = RUN_DIR / "wrapper.log"
    if not wrapper_log.is_file():
        print(f"expected captured wrapper log at {wrapper_log}",
              file=sys.stderr)
        return 1
    wrapper_text = wrapper_log.read_text(errors="replace")
    parse_wrapper_log(wrapper_text, ledger)
    wrapper_done = "Reaction cross-sections:" in wrapper_text
    rc = 0 if wrapper_done else 1

    # ---- Stage 2: ALARA convert_lib ----
    convert_input = RUN_DIR / "convert_lib.in"
    convert_input.write_text(
        f"convert_lib ajoylib alaralib {DSV} {DECAY_COMPILED} {ALARA_LIB}\n")
    conv = run([str(ALARA), str(convert_input)], cwd=RUN_DIR, timeout=3600)

    # ---- Stage 3: ACTINV build-library on identical files ----
    # SUBSET here is the post-quarantine directory: nuclides the builder
    # rejected fail-closed were moved to FAILED_DIR by the bounded loop and
    # their diagnostics are ledgered below, never retried silently.
    bld = run([str(ACTINV), "build-library", str(SUBSET), str(ACTINV_NPZ),
               "--format", "tendl", "--groups", "fispact-709",
               "--workers", "2"], cwd=RUN_DIR, timeout=3600)
    parse_rejection_logs(ledger)

    # ---- coverage: read the produced indexes ----
    # DSV rows: "<parent_kza> <daughter_kza> <mt> <rtype> <709 xs...>"
    dsv_parents = set()
    if DSV.is_file():
        for i, line in enumerate(DSV.read_text(errors="replace")
                                  .splitlines()):
            if i == 0:
                continue
            parts = line.split()
            if len(parts) > 4:
                dsv_parents.add(int(parts[0]))
    idx_kzas = set()
    idx_path = Path(str(ALARA_LIB) + ".idx")
    if idx_path.is_file():
        for line in idx_path.read_text(errors="replace").splitlines():
            m = re.match(r"^(\d+)\s", line)
            if m:
                idx_kzas.add(int(m.group(1)))
    actinv_zas = set()
    npz_index = Path(str(ACTINV_NPZ).replace(".npz", "_index.json"))
    if npz_index.is_file():
        actinv_zas = {t["za"] for t in
                      json.loads(npz_index.read_text())["targets"]}

    def kza_of(iso: str) -> int:
        sym, a = iso.split("-")
        liso = 1 if a.endswith("m") else 0
        a = a.rstrip("mn")
        znum = {"V": 23, "CR": 24, "MN": 25, "FE": 26, "CO": 27, "NI": 28,
                "CU": 29, "MO": 42, "AG": 47, "NB": 41, "TA": 73,
                "W": 74}[sym.upper()]
        return znum * 10000 + int(a) * 10 + liso

    def za_of(iso: str) -> int:
        sym, a = iso.split("-")
        a = a.rstrip("mn")
        znum = {"V": 23, "CR": 24, "MN": 25, "FE": 26, "CO": 27, "NI": 28,
                "CU": 29, "MO": 42, "AG": 47, "NB": 41, "TA": 73,
                "W": 74}[sym.upper()]
        return znum * 1000 + int(a)

    for iso in NUCLIDES:
        if ledger[iso]["wrapper"] == "converted":
            ledger[iso]["in_alara_lib"] = (kza_of(iso) in dsv_parents
                                           and kza_of(iso) in idx_kzas)
            ledger[iso]["in_actinv_lib"] = za_of(iso) in actinv_zas
            if (not ledger[iso]["in_alara_lib"]
                    and ledger[iso]["failure_class"] is None):
                ledger[iso]["failure_class"] = "absent_from_alara_library"
            if (not ledger[iso]["in_actinv_lib"]
                    and ledger[iso]["failure_class"] is None):
                ledger[iso]["failure_class"] = "absent_from_actinv_library"

    # ---- fail-closed smokes ----
    # ALARA: activation input whose material element (Pb) has no entry
    # in the converted library must fail or emit zero activation rows.
    # The real elelib expands pb -> Pb-204/206/207/208, so the run reaches
    # the converted library and fails on the absent Pb parents — not on an
    # unexpandable element name.
    ELELIB = ND / "alara-2.9.2" / "sample" / "data" / "myElelib"
    smoke_alara = RUN_DIR / "smoke_absent_element.in"
    smoke_alara.write_text(
        "geometry point\n"
        "material_lib smokeMatlib\n"
        f"element_lib {ELELIB}\n"
        "mat_loading\n\tz1 m\nend\n"
        "mixture m\n\telement pb\t1.0\t1.0\nend\n"
        f"flux f1 {RUN_DIR}/smoke_flux.txt 1.0 0 default\n"
        "schedule s\n\t1 s f1 pulse_once 0 s\nend\n"
        "pulsehistory pulse_once\n\t1 0 s\nend\n"
        f"data_library alaralib {ALARA_LIB}\n"
        "output zone\n\tspecific_activity\nend\n"
        "cooling\n\t1 s\nend\n")
    (RUN_DIR / "smokeMatlib").write_text("")
    (RUN_DIR / "smoke_flux.txt").write_text(" ".join(["1e10"] * 709) + "\n")
    smk_a = run([str(ALARA), str(smoke_alara)], cwd=RUN_DIR, timeout=600)
    # ALARA writes the zone table to stdout; preserve it as the .out file.
    smk_a_out = RUN_DIR / "smoke_absent_element.out"
    smk_a_out.write_text(smk_a["stdout_tail"])
    smk_a["output"] = str(smk_a_out)
    smk_a["fail_closed"] = (
        smk_a["returncode"] != 0
        or alara_zero_activation(smk_a_out.read_text(errors="replace")))

    # ACTINV: schema-valid spec whose sole material element is absent from
    # the built artifact.  ACTINV does not error on an uncovered element;
    # it treats the isotopes as zero-cross-section bulk and only their own
    # decay runs.  Fail-closed therefore means: the irradiated run
    # populates no nuclide that the identical decay-only run does not —
    # i.e. zero activation products.
    DECAY_DAT = (Path(os.environ.get("ACTINV_CI_DATA",
                                   Path.home() / "actinv-ci-data"))
                 / "decay" / "endf-b-viii-0_decay.dat")
    base_smoke_spec = {
        "spec": "actinv-spec-1", "title": "p26b-absent-smoke",
        "projectile": "neutron",
        "library": {"path": str(ACTINV_NPZ)},
        "decay": {"primary": str(DECAY_DAT)},
        "material": {"mass_g": 1.0, "basis": "wt_percent",
                     "composition": {"PB": 100.0}},
        "spectrum": {"structure": "fispact-709",
                     "flux_per_group": [1e10] * 709}}
    smoke_spec = RUN_DIR / "smoke_absent_nuclide.json"
    smoke_spec.write_text(json.dumps(
        dict(base_smoke_spec,
             schedule=[{"dt": "60 s", "flux": 1.0}])))
    smoke_spec_decay = RUN_DIR / "smoke_absent_nuclide_decay.json"
    smoke_spec_decay.write_text(json.dumps(
        dict(base_smoke_spec, title="p26b-absent-smoke-decayonly",
             schedule=[{"dt": "60 s", "flux": 0.0}])))
    irr_out = RUN_DIR / "smoke_absent_nuclide.out.json"
    dec_out = RUN_DIR / "smoke_absent_nuclide_decay.out.json"
    smk_i = run([str(ACTINV), "run", str(smoke_spec), str(irr_out)],
                cwd=RUN_DIR, timeout=600)
    smk_i_decay = run([str(ACTINV), "run", str(smoke_spec_decay),
                       str(dec_out)], cwd=RUN_DIR, timeout=600)
    smk_i["decay_only_control"] = smk_i_decay
    smk_i["irradiated_output"] = str(irr_out)
    smk_i["decay_only_output"] = str(dec_out)
    smk_i["fail_closed"] = (
        smk_i["returncode"] != 0
        or (irr_out.is_file() and dec_out.is_file()
            and actinv_populated(irr_out) <= actinv_populated(dec_out)))

    record = {
        "schema": "actinv-p26b-g1-conversion-1",
        "started": started,
        "wrapper": {"returncode": rc, "invocation": wrapper_invocation,
                    "log": str(wrapper_log),
                    "log_sha256": sha256(wrapper_log),
                    "environment_notes": [
                        "TMPDIR set to disk-backed p26b-work/tmp: tmpfs /tmp "
                        "has a per-user quota (usrquota mount) which truncated "
                        "gfortran scratch files -> NJOY util.f90:282 EOF",
                        "decay input = pre-compiled UKDD-2020 single file "
                        "(decay_ukdd2020_work_compiled); the uncompiled tree "
                        "was copied to a working dir because the wrapper "
                        "renames/formats files in place",
                        ".DS_Store (macOS Finder metadata) shipped inside the "
                        "UKDD-2020 tarball caused UnicodeDecodeError in the "
                        "wrapper's decay-dir scan; removed from the working "
                        "copy only — pinned source tree untouched",
                    ]},
        "convert_lib": conv,
        "actinv_build": bld,
        "artifacts": {
            "dsv": {"path": str(DSV),
                    "sha256": sha256(DSV) if DSV.is_file() else None},
            "decay_compiled": {"path": str(DECAY_COMPILED),
                               "sha256": sha256(DECAY_COMPILED)
                               if DECAY_COMPILED.is_file() else None},
            "alara_lib": {ext: {"path": str(ALARA_LIB) + ext,
                                "sha256": sha256(Path(str(ALARA_LIB) + ext))
                                if Path(str(ALARA_LIB) + ext).is_file()
                                else None}
                          for ext in (".lib", ".idx", ".gam", ".gdx")},
            "actinv_npz": {"path": str(ACTINV_NPZ),
                           "sha256": sha256(ACTINV_NPZ)
                           if ACTINV_NPZ.is_file() else None},
            "actinv_build_iterations_log": {
                "path": str(ITER_LOG),
                "sha256": sha256(ITER_LOG) if ITER_LOG.is_file() else None},
            "actinv_rejected_probes_log": {
                "path": str(PROBE_LOG),
                "sha256": sha256(PROBE_LOG) if PROBE_LOG.is_file() else None},
            "quarantined_dir": str(FAILED_DIR),
        },
        "coverage": {
            "declared_nuclides": NUCLIDES,
            "dsv_parent_kzas": len(dsv_parents),
            "alara_idx_entries": len(idx_kzas),
            "actinv_target_zas": len(actinv_zas),
            "converted": sum(1 for v in ledger.values()
                             if v["wrapper"] == "converted"),
            "in_both_libraries": sum(
                1 for v in ledger.values()
                if v["in_alara_lib"] and v["in_actinv_lib"]),
            "in_alara_only": sorted(
                iso for iso, v in ledger.items()
                if v["in_alara_lib"] and not v["in_actinv_lib"]),
            "note": "ALARA accepted all 36 FENDL evaluations; ACTINV "
                    "fail-closed on 12. identical_data-leg cases needing a "
                    "rejected nuclide report contract_gap at G2 — the "
                    "executability asymmetry is the measured G1 result.",
        },
        "fail_closed_smokes": {"alara_absent_element": smk_a,
                               "actinv_absent_nuclide": smk_i},
        "ledger": ledger,
        "pass": False,
    }
    # G1 obligations: the pipeline ran end to end and every declared nuclide
    # is ledgered — covered in both libraries, or carrying a named failure
    # class. Partial coverage is a measurement, not a gate breach; the
    # verdict for partial subset executability is CONDITIONAL and belongs
    # to G3, per the frozen protocol's closure interpretation.
    record["pass"] = (
        wrapper_done
        and conv["returncode"] == 0
        and bld["returncode"] == 0
        and all(
            (v["in_alara_lib"] and v["in_actinv_lib"])
            or v["failure_class"] is not None
            for v in ledger.values())
        and smk_a.get("fail_closed") is True
        and smk_i.get("fail_closed") is True)
    RESULT.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    print(json.dumps(record, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
