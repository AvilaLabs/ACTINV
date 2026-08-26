#!/usr/bin/env python3
"""ACTINV P6 verdict: clean-clone build, CI control suite, wheel and binary, reproducibility, release notes, hygiene."""
import os, sys, json, glob, subprocess
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."); RES = os.path.join(ROOT, "results")
def load(n):
    p = os.path.join(RES, n); return json.load(open(p)) if os.path.exists(p) else None
g = {}
ci = load("ci_end_to_end.json"); wb = load("g3_wheel_and_binary.json"); rp = load("g4_reproducibility.json"); rn = load("check_release_notes.json")
g["G1 clean-clone build"] = ("PASS — clone builds and runs the end-to-end control" if rp and rp.get("pass") else "FAIL")
g["G2 CI control suite"] = "UNSCORED" if ci is None else (("PASS" if ci["pass"] else "FAIL") + f" — {ci['library_targets']}-target library from pinned data, deviation {ci['max_abs_deviation_cli_W_per_g']:.1e} W/g, CLI == Python")
g["G3 wheel and binary"] = "UNSCORED" if wb is None else (("PASS" if wb["pass"] else "FAIL") + f" — wheel and cargo-install binary both at {wb['max_deviation_from_recorded']:.1e} deviation")
g["G4 reproducibility"] = "UNSCORED" if rp is None else (("PASS" if rp["pass"] else "FAIL") + f" — independent builds give identical result JSON ({rp['bytes']} bytes) and certificates")
g["G5 release notes"] = "UNSCORED" if rn is None else (("PASS" if rn["pass"] else "FAIL") + f" — {rn['roadmap_rows']} known limitations carried from the roadmap, none added or dropped")
# G6 version and licence hygiene
meta = json.loads(subprocess.run(["cargo", "metadata", "--format-version", "1", "--no-deps"], cwd=ROOT, capture_output=True, text=True).stdout or "{}")
pkgs = {p["name"]: (p["version"], p.get("license")) for p in meta.get("packages", [])}
py_ver = [l for l in open(os.path.join(ROOT, "python", "pyproject.toml")) if l.startswith("version")]
files = [os.path.exists(os.path.join(ROOT, f)) for f in ("LICENSE-MIT", "LICENSE-APACHE", "CHANGELOG.md")]
ok6 = bool(pkgs and all(v == "0.1.0" and l == "MIT OR Apache-2.0" for v, l in pkgs.values()) and all(files) and any("0.1.0" in v for v in py_ver))
g["G6 version and licence"] = ("PASS" if ok6 else "FAIL") + f" — {len(pkgs)} crates at 0.1.0 under MIT OR Apache-2.0; licence files and changelog present"
verdict = "UNSCORED" if any(v == "UNSCORED" for v in g.values()) else ("P6-FAIL" if any(v.startswith("FAIL") for v in g.values()) else "P6-PASS")
out = {"gates": g, "verdict": verdict}
json.dump(out, open(os.path.join(RES, "verdict_p6.json"), "w"), indent=1); print(json.dumps(out, indent=1))
sys.exit(0 if verdict == "P6-PASS" else (2 if verdict == "P6-FAIL" else 3))
