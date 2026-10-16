import json, copy, hashlib, sys, pathlib, math
ROOT = pathlib.Path("/home/connoravila/Documents/actinv")
OUT = ROOT/"target/p140/repro"
base = json.load(open(ROOT/"examples/fns_fe_5min.json"))
base["library"]["path"] = str(ROOT/base["library"]["path"])
for k in ("primary","fallback"): base["decay"][k] = str(ROOT/base["decay"][k])
cov = ROOT/"actinv-data/v1.1.0/uncertainty/tendl-2025-neutron-709g.cov.npz"
digest = hashlib.sha256(cov.read_bytes()).hexdigest()
def w(name, s): json.dump(s, open(OUT/f"{name}.json","w"), indent=1)
# F1: coupled, removal on Fe56, depletion
s = copy.deepcopy(base); s["options"]["mode"]="coupled"; s["summary"]={"depletion":True}
s["schedule"]=[{"dt":"1000 s","flux":0.0,"removal":{"Fe56":1e-3}}]
w("f1_removal", s)
# F2: trace (auto) + uncertainty + depletion
s = copy.deepcopy(base); s["summary"]={"depletion":True}
s["uncertainty"]={"covariance":{"path":str(cov),"sha256":digest},"responses":["heat.total"]}
w("f2_trace_unc", s)
s2 = copy.deepcopy(s); s2["options"]["mode"]="coupled"; w("f2_coupled_unc", s2)
# F4: trace depletion precision
s = copy.deepcopy(base); s["summary"]={"depletion":True}; w("f4_trace", s)
