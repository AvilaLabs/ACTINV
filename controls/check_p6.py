#!/usr/bin/env python3
"""ACTINV P6 verdict: clean-clone build, CI control suite, wheel and binary, reproducibility, release notes, hygiene."""
import os, sys, json, glob, subprocess, tomllib
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."); RES = os.path.join(ROOT, "results")
def load(n):
    p = os.path.join(RES, n); return json.load(open(p)) if os.path.exists(p) else None
g = {}
ci = load("ci_end_to_end.json"); wb = load("g3_wheel_and_binary.json"); rp = load("g4_reproducibility.json"); rn = load("check_release_notes.json")
sc = load("g1_self_contained.json")
g["G1 self-contained clone"] = "UNSCORED" if sc is None else (("PASS" if sc["pass"] else "FAIL") + f" — {len(sc['steps'])} CI steps run from a fresh clone with HOME redirected; regeneration leaves the tree clean")
g["G2 CI control suite"] = "UNSCORED" if ci is None else (("PASS" if ci["pass"] else "FAIL") + f" — {ci['library_targets']}-target library from pinned data, deviation {ci['max_abs_deviation_cli_W_per_g']:.1e} W/g, CLI == Python")
g["G3 wheel and binary"] = "UNSCORED" if wb is None else (("PASS" if wb["pass"] else "FAIL") + f" — wheel and cargo-install binary both at {wb['max_deviation_from_recorded']:.1e} deviation")
g["G4 reproducibility"] = "UNSCORED" if rp is None else (("PASS" if rp["pass"] else "FAIL") + f" — independent builds give identical result JSON ({rp['bytes']} bytes) and certificates")
g["G5 release notes"] = "UNSCORED" if rn is None else (("PASS" if rn["pass"] else "FAIL") + f" — {rn['roadmap_rows']} known limitations carried from the roadmap, none added or dropped")
# G6 version and licence hygiene
meta = json.loads(subprocess.run(["cargo", "metadata", "--format-version", "1", "--no-deps"], cwd=ROOT, capture_output=True, text=True).stdout or "{}")
pkgs = {p["name"]: (p["version"], p.get("license")) for p in meta.get("packages", [])}
python_meta = json.loads(subprocess.run(["cargo", "metadata", "--format-version", "1", "--no-deps", "--manifest-path", os.path.join(ROOT, "python", "Cargo.toml")], cwd=ROOT, capture_output=True, text=True).stdout or "{}")
pkgs.update({p["name"]: (p["version"], p.get("license")) for p in python_meta.get("packages", [])})
with open(os.path.join(ROOT, "python", "pyproject.toml"), "rb") as stream:
    py_project = tomllib.load(stream)["project"]
files = [os.path.exists(os.path.join(ROOT, f)) for f in ("LICENSE-MIT", "LICENSE-APACHE", "CHANGELOG.md")]
versions = {version for version, _ in pkgs.values()} | {str(py_project.get("version", ""))}
common_version = next(iter(versions)) if len(versions) == 1 else None
try:
    version_parts = tuple(int(part) for part in common_version.split(".")) if common_version else ()
except ValueError:
    version_parts = ()
python_license = py_project.get("license", {})
if isinstance(python_license, dict):
    python_license = python_license.get("text")
ok6 = bool(
    pkgs
    and len(version_parts) == 3
    and version_parts >= (0, 1, 0)
    and all(license_name == "MIT OR Apache-2.0" for _, license_name in pkgs.values())
    and python_license == "MIT OR Apache-2.0"
    and all(files)
)
reported_version = common_version or "inconsistent"
g["G6 version and licence"] = ("PASS" if ok6 else "FAIL") + f" — {len(pkgs)} crates and Python at {reported_version} under MIT OR Apache-2.0; licence files and changelog present"
amended = os.path.exists(os.path.join(ROOT, "protocols", "ACTINV-P6_AMENDMENT_A.md"))
verdict = "UNSCORED" if any(v == "UNSCORED" for v in g.values()) else ("P6-FAIL" if any(v.startswith("FAIL") for v in g.values()) else ("P6-CONDITIONAL" if amended else "P6-PASS"))
out = {"gates": g, "repair_round": amended, "verdict": verdict}
json.dump(out, open(os.path.join(RES, "verdict_p6.json"), "w"), indent=1); print(json.dumps(out, indent=1))
sys.exit(0 if verdict.startswith("P6-PASS") or verdict.startswith("P6-COND") else (2 if verdict == "P6-FAIL" else 3))
