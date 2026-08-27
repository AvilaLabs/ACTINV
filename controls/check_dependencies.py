#!/usr/bin/env python3
"""Guard against the class of failure that broke CI twice: a control depending on something the CI environment does not
have. Scans every Python file CI executes (and everything they import from the repository) for third-party imports and
requires each to be declared in requirements-ci.txt. Constants belong in data/, not in an installed package."""
import os, re, sys, json, ast
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
CI_ENTRY = ["controls/g0_cram_coefficients.py", "controls/gen_cram.py", "controls/check_release_notes.py",
            "controls/ci_end_to_end.py", "controls/test_ci_result.py", "controls/g1_self_contained.py",
            "controls/tendl_build.py",
            "controls/g1_p8_canonical_rebin.py", "controls/g2_p8_openmc.py", "controls/g3_p8_mcnp.py",
            "controls/g4_p8_provenance.py", "controls/g5_p8_mesh_identity.py",
            "controls/g6_p8_scaling_regression.py", "controls/check_p8.py",
            "controls/g2_p9_fission_matrix.py", "controls/g3_p9_coupled_auto.py"]
STDLIB = set(sys.stdlib_module_names)
declared = {l.split("==")[0].split(">=")[0].strip().lower() for l in open(os.path.join(ROOT, "requirements-ci.txt"))
            if l.strip() and not l.startswith("#")}
local = {os.path.splitext(f)[0] for f in os.listdir(os.path.join(ROOT, "controls")) if f.endswith(".py")} | {"harness", "actinv"}
seen, undeclared = set(), {}
def scan(rel):
    if rel in seen: return
    seen.add(rel)
    path = os.path.join(ROOT, rel)
    if not os.path.exists(path): return
    tree = ast.parse(open(path).read(), rel)
    for node in ast.walk(tree):
        mods = []
        if isinstance(node, ast.Import): mods = [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0: mods = [node.module.split(".")[0]]
        for m in mods:
            if m in STDLIB or m in local: 
                if m in local and m != "actinv": scan(f"controls/{m}.py")
                continue
            if m.lower() not in declared: undeclared.setdefault(m, []).append(rel)
for e in CI_ENTRY: scan(e)
res = {"ci_entry_points": CI_ENTRY, "declared": sorted(declared), "undeclared": {k: sorted(set(v)) for k, v in undeclared.items()},
       "pass": not undeclared}
json.dump(res, open(os.path.join(ROOT, "results", "check_dependencies.json"), "w"), indent=1); print(json.dumps(res, indent=1))
sys.exit(0 if res["pass"] else 1)
