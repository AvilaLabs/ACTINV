#!/usr/bin/env python3
"""Control on the CRAM coefficients themselves — the cheapest possible check that the generated Rust table is the
recorded one and that the rational approximation is what it claims to be.
  (a) every generated Rust constant equals the recorded JSON value exactly;
  (b) r(0) = 1 to 1e-15 — a state with no diagonal must be left unchanged (this is what P5 failed with hand-typed values);
  (c) r(z) approximates exp(z) on the negative real axis in ABSOLUTE terms (<= 1e-14 over z in [-50, 0]) and in
      relative terms only where exp(z) >= 1e-6, which is where the method is defined: CRAM's error does not vanish as z -> -infinity: the approximation
      floors at alpha0 (2.1e-16 for CRAM-16, 2.3e-47 for CRAM-48), so the achievable relative accuracy at depth z is
      bounded by alpha0/exp(z). Requiring 1e-9 relative therefore only makes sense while exp(z) >= ~1e-6. A nuclide with
      lambda*dt >> 35 is computed as ~alpha0*N0 rather than exactly zero, which is the origin of the small negative
      populations the solver zeroes and ledgers.
"""
import os, re, json, cmath
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."); RES = os.path.join(ROOT, "results")
SRC = os.path.join(ROOT, "data", "cram_coefficients.json")   # vendored: no control may read outside the clone
rec = json.load(open(SRC))
rs = open(os.path.join(ROOT, "crates", "actinv-core", "src", "cram_coeffs.rs")).read()
def pairs(name):
    body = rs.split(f"pub const {name}: [(f64, f64); ")[1].split("];")[0]
    return [(float(a), float(b)) for a, b in re.findall(r"\(([-\d.eE+]+), ([-\d.eE+]+)\)", body)]
def scalar(name): return float(rs.split(f"pub const {name}: f64 = ")[1].split(";")[0])
out = {}
for tag, key in (("CRAM16", "Cram16Solver"), ("CRAM48", "Cram48Solver")):
    c = rec[key]; th = pairs(f"{tag}_THETA"); al = pairs(f"{tag}_ALPHA"); a0 = scalar(f"{tag}_ALPHA0")
    exact = (a0 == c["alpha0"] and [t[0] for t in th] == c["theta_re"] and [t[1] for t in th] == c["theta_im"]
             and [a[0] for a in al] == c["alpha_re"] and [a[1] for a in al] == c["alpha_im"])
    def r(z):
        y = 1.0 + 0j
        for (tr, ti), (ar, ai) in zip(th, al): y = y + 2 * (complex(ar, ai) * (y / (z - complex(tr, ti)))).real
        return (y * a0).real
    r0 = r(0.0); worst_abs = 0.0; z_abs = None; worst_rel = 0.0; z_rel = None; floor = 0.0
    z = 0.0
    while z >= -50.0:
        e = cmath.exp(z).real; v = r(z); da = abs(v - e)
        if da > worst_abs: worst_abs, z_abs = da, z
        if e >= 1e-6:
            dr = da / e
            if dr > worst_rel: worst_rel, z_rel = dr, z
        z -= 0.05
    floor = abs(r(-745.0))   # the asymptotic floor: r(-inf) -> alpha0, while exp(-inf) = 0
    out[tag] = {"generated_equals_recorded": bool(exact), "r_at_0": r0, "r0_error": abs(r0 - 1.0),
                "worst_abs_vs_exp_on_[-50,0]": worst_abs, "at_z_abs": z_abs,
                "worst_rel_where_exp_ge_1e-6": worst_rel, "at_z_rel": z_rel,
                "asymptotic_floor_r(-745)": floor, "alpha0": a0,
                "pass": bool(exact and abs(r0 - 1.0) <= 1e-15 and worst_abs <= 1e-14 and worst_rel <= 1e-9)}
out["citation"] = rec["citation"]; out["pass"] = all(v["pass"] for k, v in out.items() if isinstance(v, dict))
json.dump(out, open(os.path.join(RES, "g0_cram_coefficients.json"), "w"), indent=1); print(json.dumps(out, indent=1))
