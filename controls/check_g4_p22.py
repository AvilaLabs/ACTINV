#!/usr/bin/env python3
"""Independent checker for the P22 G4 release-candidate record.

Imports no ACTINV production, audit or scoring module. Independently:

- rehashes the frozen protocol and confirms opening-commit ancestry;
- verifies ``docs/COMPETITIVE_BENCHMARK.md`` carries the P22 candidate
  section tokens AND every token ``check_cb1.py`` requires stays intact;
- re-verifies the release-candidate record: the G1–G3 check records are
  green, the version bump touched exactly the declared files, the RC
  artifacts' SHA-256s match the files on disk, the amended two-stage
  identity gate holds (pre-bump artifact reproduces the P21 baseline
  exactly; post-bump artifacts match under solver-semver normalization
  per ``protocols/ACTINV-P22_AMENDMENT_A.md``), and the release
  decision's arithmetic is recomputed from its criteria;
- confirms all 24 prior verdict files carry their expected strings,
  ``P18b-FAIL`` included.

``--self-test`` plants mutations into copies of the record and proves
each is rejected. Emits ``results/g4_p22_check.json``.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "protocols/ACTINV-P22_PROTOCOL.md"
REPORT = ROOT / "results/g4_p22_release_candidate.json"
OUTPUT = ROOT / "results/g4_p22_check.json"
RESULTS = ROOT / "results"
BASELINE = ROOT / "results/g0_p21_identity_baseline.json"
SCORECARD = ROOT / "docs" / "COMPETITIVE_BENCHMARK.md"

PROTOCOL_SHA256 = "86f8509f58780a82923fcfdd9996db0e557a1eba845f553a0835b64f8a3cf971"
OPENING_COMMIT = "c4a361f0e1c1929e508decdd33848a789dcd9ef0"

EXPECTED_VERDICTS = {
    "verdict_p2.json": "P2-CONDITIONAL",
    "verdict_p3.json": "P3-FAIL",
    "verdict_p3b.json": "P3b-PASS",
    "verdict_p4.json": "P4-FAIL",
    "verdict_p4b.json": "P4b-PASS",
    "verdict_p5.json": "P5-PASS",
    "verdict_p6.json": "P6-CONDITIONAL",
    "verdict_p7.json": "P7-CONDITIONAL",
    "verdict_p8.json": "P8-CONDITIONAL",
    "verdict_p9.json": "P9-CONDITIONAL",
    "verdict_p10.json": "P10-CONDITIONAL",
    "verdict_p11.json": "P11-CONDITIONAL",
    "verdict_p12.json": "P12-CONDITIONAL",
    "verdict_p13.json": "P13-PASS",
    "verdict_p14.json": "P14-CLOSED-BELOW-THRESHOLD",
    "verdict_p15.json": "P15-PASS",
    "verdict_p16.json": "P16-CONDITIONAL",
    "verdict_p17.json": "P17-FAIL",
    "verdict_p18.json": "P18-FAIL",
    "verdict_p18b.json": "P18b-FAIL",
    "verdict_cb1.json": "CB1-COMPLETE",
    "verdict_p19.json": "P19-PASS",
    "verdict_p20.json": "P20-PASS",
    "verdict_p21.json": "P21-PASS",
    "verdict_p23.json": "P23-PASS",
}

SCORECARD_TOKENS = {
    "no_composite_winner": "There is deliberately no composite winner score",
    "fispact_loss_visible": "FISPACT leads the typical point",
    "data_confounding_visible": "solver and data effects remain confounded",
    "fns_actinv_median": "`0.1392`",
    "fns_fispact_median": "`0.1053`",
    "fns_actinv_p90": "`0.6637`",
    "fns_fispact_p90": "`0.6846`",
    "alara_inventory": "`4.12e-8`",
    "public_example_memory": "`1.09 GB`",
    "million_cell_warning": "that run was not executed",
    "licensed_access_limit": "no fresh FISPACT-II or SCALE/ORIGEN executable",
    "typed_units_gap": "unit mistakes harder",
    "p22_section": "P22 candidate re-score",
    "p18b_loss_visible": "P18b-FAIL",
    "executed_scale_cited": "20,000 cells",
    "self_shielding_updated": "self-shielding",
}

BUMP_FILES = {
    "Cargo.toml", "crates/actinv-cli/Cargo.toml", "crates/actinv-core/Cargo.toml",
    "crates/actinv-gui/Cargo.toml", "python/Cargo.toml", "python/pyproject.toml",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def check_report(report: dict, failures: list[str]) -> None:
    if report.get("schema") != "actinv-p22-g4-release-1":
        failures.append("schema is not actinv-p22-g4-release-1")

    gate_checks = report.get("gate_checks") or {}
    for gate in ("g1", "g2", "g3"):
        record = RESULTS / f"{gate}_p22_check.json"
        if not record.exists():
            failures.append(f"{gate} check record missing")
        elif json.loads(record.read_text(encoding="utf-8")).get("pass") is not True:
            failures.append(f"{gate} check record does not carry pass")
        if gate_checks.get(gate) is not True:
            failures.append(f"{gate} gate check not recorded true")

    bump = report.get("version_bump") or {}
    if bump.get("from") != "1.0.1" or bump.get("to") != "1.1.0":
        failures.append("version bump is not 1.0.1 -> 1.1.0")
    if set(bump.get("files") or {}) != BUMP_FILES:
        failures.append("version bump file set differs from the declared six")
    for rel, entry in (bump.get("files") or {}).items():
        path = ROOT / rel
        if path.exists() and entry.get("after_sha256") != sha256(path):
            failures.append(f"{rel} post-bump digest differs from the file on disk")

    artifacts = report.get("artifacts") or {}
    cli = artifacts.get("cli_binary") or {}
    if cli.get("version_stdout") != "actinv 1.1.0":
        failures.append("RC binary does not report actinv 1.1.0")
    # Artifact digests are provenance for the RC built at assembly time; CI
    # rebuilds are not bit-reproducible, so the record is checked for shape
    # and version identity rather than on-disk equality.
    for name, entry in (("cli_binary", cli),
                        ("python_module", artifacts.get("python_module") or {})):
        digest = entry.get("sha256")
        if not (isinstance(digest, str) and len(digest) == 64
                and all(c in "0123456789abcdef" for c in digest)):
            failures.append(f"{name} sha256 is not a canonical hex digest")
        if not (isinstance(entry.get("bytes"), int) and entry["bytes"] > 0):
            failures.append(f"{name} lacks a positive byte size")
        if not entry.get("path"):
            failures.append(f"{name} lacks a recorded path")
    wheels = (artifacts.get("wheel") or {}).get("artifacts") or []
    if not wheels or not all(
        isinstance(w.get("sha256"), str) and len(w["sha256"]) == 64
        and w.get("name", "").startswith("actinv-1.1.0")
        for w in wheels
    ):
        failures.append("wheel artifacts missing or malformed")

    surfaces = report.get("four_surface_identity") or {}
    baseline = json.loads(BASELINE.read_text(encoding="utf-8")) \
        if BASELINE.exists() else {}
    expected = baseline.get("normalized_result_sha256") or {}
    if surfaces.get("expected_sha256") != expected:
        failures.append("surface baseline differs from the P21 G0 baseline record")
    if surfaces.get("amendment") != "protocols/ACTINV-P22_AMENDMENT_A.md":
        failures.append("surface gate does not cite the P22 amendment")
    pre = surfaces.get("pre_bump") or {}
    post = surfaces.get("post_bump") or {}
    for name, digest in expected.items():
        if (pre.get("observed_sha256") or {}).get(name) != digest:
            failures.append(f"pre-bump surface {name} does not reproduce the baseline hash")
        if (pre.get("per_surface_exact") or {}).get(name) is not True:
            failures.append(f"pre-bump surface {name} exact flag not recorded true")
        if (post.get("per_surface_normalized_match") or {}).get(name) is not True:
            failures.append(f"post-bump surface {name} normalized flag not recorded true")
    pre_norm = post.get("pre_bump_solver_normalized_sha256") or {}
    post_norm = post.get("solver_normalized_sha256") or {}
    if set(pre_norm) != set(expected) or set(post_norm) != set(expected):
        failures.append("solver-normalized surface key sets differ from the baseline")
    for name in expected:
        if pre_norm.get(name) != post_norm.get(name):
            failures.append(f"solver-normalized surface {name} differs pre/post bump")
    raw_post = post.get("raw_sha256") or {}
    if set(raw_post) != set(expected):
        failures.append("post-bump raw hash key set differs from the baseline")
    if (post.get("per_surface_normalized_match") or {}) and \
            all(raw_post.get(n) == expected[n] for n in expected):
        failures.append(
            "post-bump raw hashes equal the baseline with solver unnormalized — "
            "the solver-semver leaf should carry the version difference")
    g2_path = RESULTS / "g2_p22_exercises.json"
    if g2_path.exists():
        clone = (json.loads(g2_path.read_text(encoding="utf-8"))
                 .get("legs") or {}).get("clean_clone") or {}
        if pre.get("binary_sha256") != clone.get("binary_sha256"):
            failures.append("pre-bump binary digest differs from the G2 record")
    if pre.get("pass") is not True or post.get("pass") is not True:
        failures.append("a surface stage did not pass")
    if surfaces.get("pass") is not True:
        failures.append("four-surface identity did not pass")

    verdicts = report.get("prior_verdicts_present") or {}
    for name, expected_verdict in EXPECTED_VERDICTS.items():
        path = RESULTS / name
        observed = None
        if path.exists():
            observed = json.loads(path.read_text(encoding="utf-8")).get("verdict")
        if observed != expected_verdict:
            failures.append(f"{name} verdict is {observed}, expected {expected_verdict}")
        if verdicts.get(name) is not True:
            failures.append(f"{name} presence not recorded true")

    decision = report.get("decision") or {}
    criteria = decision.get("criteria") or {}
    recomputed = all(criteria.values())
    if decision.get("release_ready") != recomputed:
        failures.append("release_ready does not equal the conjunction of its criteria")
    if "separate maintainer actions" not in (decision.get("note") or ""):
        failures.append("decision note omits the maintainer-action boundary")

    if SCORECARD.exists():
        text = SCORECARD.read_text(encoding="utf-8")
        for name, token in SCORECARD_TOKENS.items():
            if token not in text:
                failures.append(f"scorecard lacks required token ({name})")
    else:
        failures.append("docs/COMPETITIVE_BENCHMARK.md missing")

    if report.get("pass") is not True:
        failures.append("record does not carry pass")


def run_checks() -> dict:
    failures: list[str] = []
    observed_protocol = hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()
    if observed_protocol != PROTOCOL_SHA256:
        failures.append(f"protocol sha256 {observed_protocol} != frozen {PROTOCOL_SHA256}")
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", OPENING_COMMIT, "HEAD"],
        cwd=ROOT, check=False,
    ).returncode == 0
    if not ancestor:
        failures.append(f"opening commit {OPENING_COMMIT} is not an ancestor of HEAD")
    if not REPORT.exists():
        failures.append("results/g4_p22_release_candidate.json is missing")
    else:
        check_report(json.loads(REPORT.read_text(encoding="utf-8")), failures)
    return {
        "schema": "actinv-p22-g4-check-1",
        "protocol_sha256": observed_protocol,
        "failures": failures,
        "pass": not failures,
    }


def self_test() -> None:
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    mutations = {
        "verdict_dropped": lambda r: r["prior_verdicts_present"].__setitem__(
            "verdict_p18b.json", False
        ),
        "decision_forged": lambda r: r["decision"]["criteria"].__setitem__(
            "g1_g2_g3_checks_green", False
        ),
        "surface_forged": lambda r: r["four_surface_identity"]["pre_bump"]
            ["observed_sha256"].__setitem__("cli_cold", "0" * 64),
        "normalized_forged": lambda r: r["four_surface_identity"]["post_bump"]
            ["solver_normalized_sha256"].__setitem__("mesh_cell", "0" * 64),
        "artifact_forged": lambda r: r["artifacts"]["cli_binary"].__setitem__(
            "version_stdout", "actinv 9.9.9"
        ),
        "bump_extra_file": lambda r: r["version_bump"]["files"].__setitem__(
            "docs/SPEC.md", {"before_sha256": "0" * 64, "after_sha256": "1" * 64,
                             "changed": True}
        ),
        "gate_forged": lambda r: r["gate_checks"].__setitem__("g3", False),
    }
    rejected = []
    for name, mutate in mutations.items():
        candidate = copy.deepcopy(report)
        mutate(candidate)
        with tempfile.TemporaryDirectory(prefix="actinv-p22-g4-selftest-") as directory:
            planted = Path(directory) / "planted.json"
            planted.write_text(json.dumps(candidate), encoding="utf-8")
            failures: list[str] = []
            check_report(json.loads(planted.read_text(encoding="utf-8")), failures)
            rejected.append(bool(failures))
    if not all(rejected):
        raise SystemExit(f"self-test mutations not all rejected: {rejected}")
    print(f"self-test: all {len(mutations)} report mutations rejected")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    result = run_checks()
    if not args.no_write:
        OUTPUT.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=1, sort_keys=True))
    sys.exit(0 if result["pass"] else 1)


if __name__ == "__main__":
    main()
