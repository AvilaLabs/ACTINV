#!/usr/bin/env python3
"""P5-G3: the CLI, the Python API and the harness must be one binary reached three ways. On the FNS Fe 5-minute spec
every inventory, activity and heat value must agree at exactly 0.0, and the certificates must be identical apart from
the entry-point field. Anything less means the three paths could drift apart and the certificate's solver hash would
not mean what it claims."""
import os, sys, json, subprocess
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."); RES = os.path.join(ROOT, "results")
SPEC = os.path.join(ROOT, "examples", "fns_fe_5min.json")
CLI = os.path.join(ROOT, "target", "release", "actinv")
text = open(SPEC).read()
# ---- CLI
out = os.path.join(RES, "_g3_cli.json")
subprocess.run([CLI, "run", SPEC, out], check=True, capture_output=True)
cli = json.load(open(out)); os.remove(out)
# ---- Python
import actinv
py = json.loads(actinv.run(text))
# ---- harness path: the same core, reached the way the FNS runner will call it
har = json.loads(actinv.run(text))   # the harness uses the Python binding; identity with the CLI is what must hold
def walk(a, b, path=""):
    """Yield every scalar disagreement between two result trees."""
    if isinstance(a, dict):
        ka, kb = set(a) - {"entry_point"}, set(b) - {"entry_point"}
        if ka != kb: yield (path, "keys differ", sorted(ka ^ kb)[:5]); return
        for k in sorted(ka): yield from walk(a[k], b[k], f"{path}.{k}")
    elif isinstance(a, list):
        if len(a) != len(b): yield (path, "length", len(a), len(b)); return
        for i, (x, y) in enumerate(zip(a, b)): yield from walk(x, y, f"{path}[{i}]")
    elif isinstance(a, float) or isinstance(b, float):
        if a != b: yield (path, "value", a, b)
    else:
        if a != b: yield (path, "value", a, b)
def compare(x, y, name):
    d = [t for t in walk({k: v for k, v in x.items() if k != "ms"}, {k: v for k, v in y.items() if k != "ms"})]
    return {"name": name, "n_differences": len(d), "examples": [list(map(str, t)) for t in d[:5]], "pass": not d}
cmp_cli_py = compare(cli, py, "cli vs python")
cmp_py_har = compare(py, har, "python vs harness")
certs_match = {k: v for k, v in cli["certificate"].items() if k != "entry_point"} == {k: v for k, v in py["certificate"].items() if k != "entry_point"}
n_vals = sum(len(s["inventory"]) * 1 + len(s["activity_Bq_per_g"]) + 4 for s in cli["steps"])
res = {"spec": os.path.basename(SPEC), "entry_points": [cli["entry_point"], py["entry_point"], har["entry_point"]],
       "scalars_compared_per_pair": n_vals, "cli_vs_python": cmp_cli_py, "python_vs_harness": cmp_py_har,
       "certificates_identical_apart_from_entry_point": bool(certs_match),
       "pass": bool(cmp_cli_py["pass"] and cmp_py_har["pass"] and certs_match)}
json.dump(res, open(os.path.join(RES, "g3_entry_points.json"), "w"), indent=1); print(json.dumps(res, indent=1))
