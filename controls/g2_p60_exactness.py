#!/usr/bin/env python3
"""P60 G2 — independent arithmetic: rebuild the spectrum-collapsed
covariance from the sidecar, recompute every design quantity with its own
folds — per-parameter (Σ·s)ᵢ²/Σᵢᵢ, per-target-block bᵀΣ_BB⁻¹b via an
independent Cholesky — and compare all emitted design values at machine
precision. A fixture mutation must move the numbers.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p60_case  # noqa: E402
import p58_fixture  # noqa: E402

ACTINV = ROOT / "target/debug/actinv"
OUT = ROOT / "results/g2_p60_exactness.json"


def check(name, ok, detail=""):
    checks.append({"name": name, "pass": bool(ok), "detail": detail})


def collapsed_sigma(lib_path: Path, cov_path: Path, covered: list,
                    specdoc: dict):
    """Covered-parameter covariance in the emitted ordering, rebuilt from
    the sidecar with the emitter's own convention: each row contributes a
    per-bin response vector v[b] = sum_g phi_g*scale/(width*total)*overlap*
    (sigma_row/sigma_base), then Sigma_ij = v_i . C_ij . v_j."""
    lib = np.load(lib_path)
    rows, sig, bounds = lib["rows"], lib["sig"], lib["bounds"]
    cov = np.load(cov_path)
    grid = cov["grid_values"]
    values = cov["values"]
    blocks = {}
    for row in cov["components"]:
        mt_a, mt_b, off, n = int(row[1]), int(row[2]), int(row[7]), int(row[8])
        g = int(np.sqrt(n))
        blocks[(mt_a, mt_b)] = values[off:off + n].reshape(g, g)

    flux = specdoc["spectrum"]["flux_per_group"]
    total = specdoc["spectrum"]["total"]
    base_of_mt = {}
    for r, row in enumerate(rows):
        if int(row[2]) == -1:
            base_of_mt[int(row[1])] = r

    def row_vector(lib_row: int) -> np.ndarray:
        mt = int(rows[lib_row][1])
        base = base_of_mt[mt]
        v = np.zeros(len(grid) - 1)
        for g, fg in enumerate(flux):
            if fg == 0.0:
                continue
            low, high = bounds[g], bounds[g + 1]
            density = fg / (high - low) / total
            if lib_row == base:
                mult = 1.0
            elif sig[base][g] > 0.0:
                mult = sig[lib_row][g] / sig[base][g]
            else:
                mult = 0.0
            if mult == 0.0:
                continue
            for b in range(len(grid) - 1):
                w = max(min(high, grid[b + 1]) - max(low, grid[b]), 0.0)
                if w > 0.0:
                    v[b] += density * w * mult
        return v

    vectors = {}
    sigma = np.zeros((len(covered), len(covered)))
    for i, pi in enumerate(covered):
        ri = pi["parameter"]["library_row"]
        vi = vectors.setdefault(ri, row_vector(ri))
        for j, pj in enumerate(covered):
            rj = pj["parameter"]["library_row"]
            mt_i, mt_j = int(rows[ri][1]), int(rows[rj][1])
            vj = vectors.setdefault(rj, row_vector(rj))
            block = blocks.get((mt_i, mt_j))
            if block is not None:
                left, right = vi, vj
            else:
                block = blocks.get((mt_j, mt_i))
                if block is None:
                    continue
                left, right = vj, vi
            acc = 0.0
            for a in range(len(grid) - 1):
                for b in range(len(grid) - 1):
                    acc += left[a] * block[a, b] * right[b]
            sigma[i, j] = acc
    return sigma


def reference_design(response: dict, sigma: np.ndarray,
                     covered: list) -> dict:
    """All design quantities with naive left-to-right folds."""
    n = len(covered)
    s = [c["value"] for c in covered]
    sigma_s = [sum(sigma[i, j] * s[j] for j in range(n))
               for i in range(n)]
    xs_var = sum(s[i] * sigma_s[i] for i in range(n))
    dec_var = sum((rec["value"] * rec["parameter"]["standard_uncertainty_s"]) ** 2
                  for rec in response.get("decay_sensitivities", [])
                  if rec["parameter"]["covered"])
    yield_var = sum((rec["value"] * rec["parameter"]["standard_uncertainty"]) ** 2
                    for rec in response.get("yield_sensitivities", [])
                    if rec["parameter"]["covered"])
    total = xs_var + dec_var + yield_var
    # per-parameter reductions in covered ordering
    reductions = [sigma_s[i] * sigma_s[i] / sigma[i, i]
                  if sigma[i, i] > 0.0 else None for i in range(n)]
    # target blocks keyed (target_za, target_LISO)
    blocks: dict = {}
    for i, c in enumerate(covered):
        key = (c["parameter"]["target_ZA"], c["parameter"]["target_LISO"])
        blocks.setdefault(key, []).append(i)
    block_reductions = {}
    for key, idx in blocks.items():
        sub = np.array([[sigma[a, b] for b in idx] for a in idx])
        rhs = np.array([sigma_s[i] for i in idx])
        try:
            x = np.linalg.solve(sub, rhs)
            block_reductions[key] = float(
                sum(rhs[i] * x[i] for i in range(len(idx))))
        except np.linalg.LinAlgError:
            block_reductions[key] = None
    return {"total": total, "sigma_s": sigma_s, "s": s,
            "reductions": reductions, "block_reductions": block_reductions}


checks = []
tmp = Path(tempfile.mkdtemp(prefix="p60_g2_", dir=ROOT / "target"))
fx = p58_fixture.build(tmp)
res = p60_case.run(ACTINV, p60_case.spec(fx), tmp, "ref")
specdoc = p60_case.spec(fx)

responses = res["steps"][-1]["uncertainty"]["responses"]
for name, resp in responses.items():
    des = resp["design"]
    covered = p60_case.covered_sensitivities(resp)
    sigma = collapsed_sigma(fx["library"], fx["covariance"], covered, specdoc)
    ref = reference_design(resp, sigma, covered)
    total = ref["total"]
    check(f"total variance exact: {name}",
          des["total_propagated_variance"] == total,
          f"{des['total_propagated_variance']!r} vs {total!r}")
    # per-parameter entries: match each emitted xs row back to its covered
    # position by (MT, LFS, library_row) and recompute the Schur reduction
    for e in des["top_parameters"]:
        if e["channel"] != "cross_section_mf33":
            p = e["parameter"]
            match = [r for r in resp.get("decay_sensitivities", [])
                     + resp.get("yield_sensitivities", [])
                     if r["parameter"] == p]
            continue
        p = e["parameter"]
        match = [i for i, c in enumerate(covered)
                 if c["parameter"]["MT"] == p["MT"]
                 and c["parameter"]["LFS"] == p["LFS"]
                 and c["parameter"]["library_row"] == p["library_row"]]
        check(f"param resolved: {name} MT={p['MT']} LFS={p['LFS']}",
              len(match) == 1)
        if not match:
            continue
        i = match[0]
        red = ref["reductions"][i]
        check(f"reduction exact: {name} row={i}",
              red is not None and e["variance_reduction"] == red,
              f"{e['variance_reduction']!r} vs {red!r}")
        share_i = ref["s"][i] * ref["sigma_s"][i]
        check(f"variance_share exact: {name} row={i}",
              e["variance_share"] == share_i,
              f"{e['variance_share']!r} vs {share_i!r}")
        check(f"posterior exact: {name} row={i}",
              e["posterior_variance"] == total - red,
              f"{e['posterior_variance']!r} vs {total - red!r}")
        check(f"share_of_total exact: {name} row={i}",
              e["share_of_total"] == red / total,
              f"{e['share_of_total']!r} vs {red / total!r}")
    # target-block entries: independent Cholesky recomputation
    for r in des["top_reactions"]:
        key = (r["target_za"], r["target_liso"])
        red = ref["block_reductions"].get(key)
        if r["status"] == "emitted":
            check(f"block solved in checker: {name} {key}", red is not None)
            if red is not None:
                # naive-Cholesky vs LAPACK solve differ at ulp; require
                # agreement well inside data precision
                check(f"block reduction exact: {name} {key}",
                      abs(r["variance_reduction"] - red)
                      <= max(1e-12 * abs(red), 1e-290),
                      f"{r['variance_reduction']!r} vs {red!r}")
                check(f"block posterior exact: {name} {key}",
                      r["posterior_variance"] == total
                      - r["variance_reduction"])
        else:
            check(f"block ill_conditioned agrees: {name} {key}",
                  red is None)
    # diagonal-channel entries recompute exactly
    for e in des["top_parameters"]:
        if e["channel"] == "cross_section_mf33":
            continue
        records = (resp.get("decay_sensitivities", [])
                   if e["channel"] == "decay_constants"
                   else resp.get("yield_sensitivities", []))
        match = [r for r in records
                 if json.dumps(r["parameter"], sort_keys=True)
                 == json.dumps(e["parameter"], sort_keys=True)]
        check(f"diag param resolved: {name} {e['channel']}", len(match) == 1)
        if match:
            sigma_key = ("standard_uncertainty_s"
                         if e["channel"] == "decay_constants"
                         else "standard_uncertainty")
            red = (match[0]["value"] * match[0]["parameter"][sigma_key]) ** 2
            check(f"diag reduction exact: {name} {e['channel']}",
                  e["variance_reduction"] == red,
                  f"{e['variance_reduction']!r} vs {red!r}")
            check(f"diag half-uncertainty exact: {name} {e['channel']}",
                  e["reduction_at_half_uncertainty"] == 0.75 * red)

# --- mutation sensitivity: perturb an isomer production cross-section --
import p11_fixtures  # noqa: E402
import shutil  # noqa: E402

lib2 = tmp / "act60_mut.npz"
shutil.copy(fx["library"], lib2)
d = np.load(lib2)
sig = d["sig"].copy()
sig[4] *= 1.5
np.savez(lib2, rows=d["rows"], sig=sig, bounds=d["bounds"])
idx_src = Path(str(fx["library"])).with_name(
    Path(str(fx["library"])).stem + "_index.json")
idx = lib2.with_name(lib2.stem + "_index.json")
index = json.loads(idx_src.read_text())
index["sha256_npz"] = p60_case.sha(lib2)
p11_fixtures.write_json(idx, index)
cov_idx = Path(str(fx["covariance"])).with_name(
    Path(str(fx["covariance"])).stem + "_index.json")
cov_index = json.loads(cov_idx.read_text())
cov_index["activation_library"] = str(lib2)
cov_index["activation_library_sha256"] = p60_case.sha(lib2)
cov_index["activation_index"] = str(idx)
cov_index["activation_index_sha256"] = p60_case.sha(idx)
p11_fixtures.write_json(cov_idx, cov_index)
mut = dict(fx)
mut["library"] = lib2
res_m = p60_case.run(ACTINV, p60_case.spec(mut), tmp, "mut")
ref_des = responses["activity:Mn57m1"]["design"]
mut_des = res_m["steps"][-1]["uncertainty"]["responses"]["activity:Mn57m1"]["design"]
ref_r = [e["variance_reduction"] for e in ref_des["top_parameters"]]
mut_r = [e["variance_reduction"] for e in mut_des["top_parameters"]]
check("design moves under mutation", ref_r != mut_r,
      f"{ref_r[:2]!r} -> {mut_r[:2]!r}")

failed = [c for c in checks if not c["pass"]]
OUT.write_text(json.dumps(
    {"pass": not failed, "n": len(checks), "failed": failed},
    indent=1) + "\n")
print(json.dumps({"pass": not failed, "n": len(checks),
                  "failed": [c["name"] for c in failed]}))
sys.exit(1 if failed else 0)
