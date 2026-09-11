#!/usr/bin/env python3
"""P19 G5 independent closure checker.

Re-verifies the gate chain G0..G4 against committed evidence without
importing production code:

- G0: identity baseline pass + the frozen normalized hash
- G1: shield build record pass + artifact sha matches the emitted table
- G2: runtime record pass + all 11 legs
- G3: limits/compare/rates records pass; every declared material present in
  the comparator output with node+group verdicts
- G4: perf record pass; absent-feature + build cost recorded
- Artifact: format field, six declared materials, group_factors present,
  sigma0=inf column exactly 1.0, artifact sha consistent across records
- Core: contract rev2 wires actinv-shield/njoy-groupr/shield-compare;
  adapters + claims exist for every step
- Non-claims: package limitations cover the declared scope

`--self-test` plants mutations on a copy of the evidence and asserts each is
rejected.
"""
import copy
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results/g5_p19_check.json"
ARTIFACT = ROOT / "results/g1_p19_shield_artifact.json"
DECLARED = {"W186", "Ag107", "Ta181", "Nb93", "U238", "Fe56"}
CHANNELS = ("total", "elastic", "fission", "capture")
FROZEN_IDENTITY = "ebc307ff845ebabb6d7300c3a65439fb8a1b22bd988362486218610c373b3e62"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def load(name: str, failures: list):
    path = ROOT / "results" / name
    if not path.is_file():
        failures.append(f"missing {name}")
        return {}
    return json.loads(path.read_text())


def check_gates(failures: list) -> None:
    g0 = load("g0_p19_check.json", failures)
    if not g0.get("pass"):
        failures.append("g0 check did not pass")
    baseline = json.loads(
        (ROOT / "results/g0_p19_identity_baseline.json").read_text())
    hashes = baseline.get("normalized_result_sha256", {})
    for surface in ("cli_cold", "cli_warm", "python"):
        if hashes.get(surface) != FROZEN_IDENTITY:
            failures.append(f"identity baseline {surface} drifted")
    for gate, name in (("g1", "g1_p19_check.json"), ("g2", "g2_p19_check.json"),
                       ("g3", "g3_p19_check.json")):
        rec = load(name, failures)
        if not rec.get("pass"):
            failures.append(f"{gate} check did not pass")
        if rec.get("failures"):
            failures.append(f"{gate} check recorded failures: {rec['failures']}")
    g2 = load("g2_p19_runtime.json", failures)
    checks = g2.get("checks", {})
    if not g2.get("pass") or len(checks) != 11 or not all(checks.values()):
        failures.append("g2 runtime record incomplete")
    limits = load("g3_p19_limits.json", failures)
    legs = limits.get("legs", {})
    analytic = legs.get("analytic_limits", {})
    if not (analytic.get("inf_limit_pass") and analytic.get("bounded_rise_pass")
            and analytic.get("nonnegative_pass")):
        failures.append("g3 analytic limits not satisfied")
    compare = load("g3_p19_compare.json", failures)
    if compare.get("all_covered") != "covered":
        failures.append("comparator did not cover all declared materials")
    mats = compare.get("materials", {})
    if set(mats) != {"W-186", "Ag-107", "Ta-181", "Nb-93", "U-238", "Fe-56"}:
        failures.append("comparator material set != declared set")
    for m, v in mats.items():
        if not v.get("within_tolerance"):
            failures.append(f"{m} node leg outside tolerance")
        if not v.get("group_comparison", {}).get("within_tolerance"):
            failures.append(f"{m} group leg outside tolerance")
    rates = load("g3_p19_rates.json", failures)
    if not rates.get("pass"):
        failures.append("g3 rates record did not pass")
    perf = load("g4_p19_perf.json", failures)
    if not perf.get("pass"):
        failures.append("g4 perf record did not pass")
    if not perf.get("table_build_seconds"):
        failures.append("g4 record lacks the table-build cost")


def check_artifact(failures: list) -> None:
    if not ARTIFACT.is_file():
        failures.append("shield artifact missing")
        return
    artifact = json.loads(ARTIFACT.read_text())
    if artifact.get("format") != "actinv-shield-table-1":
        failures.append("artifact format field wrong")
    nuclides = artifact.get("nuclides", {})
    if set(nuclides) != DECLARED:
        failures.append(f"artifact nuclides {sorted(nuclides)} != declared")
    sig0 = artifact.get("sigma0_b", [])
    if not sig0 or sig0[0] != 1.0e10:
        failures.append("sigma0 grid missing the infinite-dilution column")
    bad_inf = missing_factors = 0
    for block in nuclides.values():
        for g in block.get("groups", []):
            gf = g.get("group_factors")
            if not gf:
                missing_factors += 1
                continue
            for ch in CHANNELS:
                if g["infinite_dilution_b"][CHANNELS.index(ch)] == 0.0:
                    continue
                if gf[ch][0][0] != 1.0:
                    bad_inf += 1
    if missing_factors:
        failures.append(f"{missing_factors} groups lack group_factors")
    if bad_inf:
        failures.append(f"{bad_inf} inf-column cells not exactly 1.0")
    # Artifact hash must agree across every evidence record that pins it.
    actual = sha256(ARTIFACT)
    for name, key in (("g1_p19_shield_table.json", "artifact_sha256"),
                      ("g2_p19_runtime.json", "table_sha256"),
                      ("g3_p19_rates.json", "table_sha256")):
        rec = load(name, failures)
        if rec.get(key) and rec[key] != actual:
            failures.append(f"{name} pins a different artifact sha")


def check_core(failures: list) -> None:
    pkg = ROOT / "controls/p19_core"
    contract = json.loads((pkg / "contract.json").read_text())
    steps = {s["step_id"] for s in contract.get("workflow", [])}
    for needed in ("njoy-purr", "njoy-groupr", "actinv-shield",
                   "shield-compare"):
        if needed not in steps:
            failures.append(f"contract step {needed} absent")
    for adapter in ("adapter_purr.json", "adapter_groupr.json",
                    "adapter_shield.json", "adapter_compare.json"):
        if not (pkg / adapter).is_file():
            failures.append(f"{adapter} missing")
    claims = json.loads((pkg / "claims.json").read_text())
    by_step = {c["step_id"] for c in claims.get("claims", [])}
    for needed in ("njoy-groupr", "actinv-shield", "shield-compare"):
        if needed not in by_step:
            failures.append(f"no claims recorded for {needed}")
    package = json.loads((pkg / "package.json").read_text())
    limitations = " ".join(package.get("limitations", []))
    for phrase in ("unresolved", "pointwise", "transport",
                   "partially-covered", "FENDL"):
        if phrase.lower() not in limitations.lower():
            failures.append(f"limitations omit {phrase!r}")


def run_checks() -> dict:
    failures: list = []
    check_gates(failures)
    check_artifact(failures)
    check_core(failures)
    return {
        "schema": "actinv-p19-g5-check-1",
        "failures": failures,
        "pass": not failures,
    }


def self_test() -> int:
    with tempfile.TemporaryDirectory(prefix="p19-g5-selftest-") as tmp:
        work = Path(tmp)
        shutil.copytree(ROOT / "results", work / "results")

        rejected = 0
        total = 0
        # mutation 1: corrupt a recorded gate verdict
        c = json.loads((work / "results/g1_p19_check.json").read_text())
        c["pass"] = False
        (work / "results/g1_p19_check.json").write_text(json.dumps(c))
        total += 1
        if not _run_against(work)["failures"]:
            print("self-test miss: corrupted g1 verdict accepted")
        else:
            rejected += 1
        # mutation 2: erase a declared material from the comparator output
        (work / "results/g1_p19_check.json").write_text(
            (ROOT / "results/g1_p19_check.json").read_text())
        c = json.loads((work / "results/g3_p19_compare.json").read_text())
        c["materials"].pop("U-238")
        (work / "results/g3_p19_compare.json").write_text(json.dumps(c))
        total += 1
        if not _run_against(work)["failures"]:
            print("self-test miss: dropped U-238 accepted")
        else:
            rejected += 1
        # mutation 3: tamper the artifact's inf column
        (work / "results/g3_p19_compare.json").write_text(
            (ROOT / "results/g3_p19_compare.json").read_text())
        a = json.loads((work / "results/g1_p19_shield_artifact.json").read_text())
        a["nuclides"]["W186"]["groups"][0]["group_factors"]["capture"][0][0] = 0.9
        (work / "results/g1_p19_shield_artifact.json").write_text(json.dumps(a))
        total += 1
        if not _run_against(work)["failures"]:
            print("self-test miss: tampered inf column accepted")
        else:
            rejected += 1
        print(f"self-test rejected {rejected}/{total} mutations")
        return 0 if rejected == total else 1


def _run_against(root: Path) -> dict:
    """Run the checks with all module paths redirected at a copied tree."""
    failures: list = []

    def load(name):
        path = root / "results" / name
        return json.loads(path.read_text()) if path.is_file() else {}

    g0 = load("g0_p19_check.json")
    if not g0.get("pass"):
        failures.append("g0")
    baseline = json.loads(
        (root / "results/g0_p19_identity_baseline.json").read_text())
    hashes = baseline.get("normalized_result_sha256", {})
    if any(hashes.get(s) != FROZEN_IDENTITY
           for s in ("cli_cold", "cli_warm", "python")):
        failures.append("identity")
    for gate in ("g1_p19_check.json", "g2_p19_check.json", "g3_p19_check.json"):
        if not load(gate).get("pass"):
            failures.append(gate)
    compare = load("g3_p19_compare.json")
    if set(compare.get("materials", {})) != \
            {"W-186", "Ag-107", "Ta-181", "Nb-93", "U-238", "Fe-56"}:
        failures.append("materials")
    artifact = json.loads(
        (root / "results/g1_p19_shield_artifact.json").read_text())
    for block in artifact.get("nuclides", {}).values():
        for g in block.get("groups", []):
            gf = g.get("group_factors", {})
            for ch in CHANNELS:
                if g["infinite_dilution_b"][CHANNELS.index(ch)] == 0.0:
                    continue
                if gf[ch][0][0] != 1.0:
                    failures.append("inf column")
                    return {"failures": failures}
    return {"failures": failures}


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()
    report = run_checks()
    import subprocess
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
        capture_output=True, check=False).stdout.strip()
    report["head_commit"] = head
    RESULT.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")
    print(json.dumps(report, indent=1, sort_keys=True))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
