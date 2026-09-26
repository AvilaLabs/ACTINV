#!/usr/bin/env python3
"""P58 G2 — independent arithmetic: rebuild the spectrum-collapsed
covariance from the sidecar, recompute every per-parameter variance
share with plain folds, and compare all emitted isomer values at
machine precision. Mutations to the fixture must flip the numbers.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p58_case  # noqa: E402
import p58_fixture  # noqa: E402

ACTINV = ROOT / "target/debug/actinv"
OUT = ROOT / "results/g2_p58_exactness.json"

BUCKETS = ("isomer_product_channels", "isomer_target_channels",
           "isomer_decay_constants", "ground_channels")


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
    base_of_mt = {}  # (mt) -> row index of the zap==-1 loss row
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


def reference_shares(response: dict, sigma: np.ndarray,
                     covered: list) -> dict:
    """Naive left-to-right folds matching the emitted quadratic form."""
    s = np.array([c["value"] for c in covered])
    row_contr = np.zeros(len(covered))
    for i in range(len(covered)):
        acc = 0.0
        for j in range(len(covered)):
            acc += sigma[i, j] * s[j]
        row_contr[i] = acc
    shares = np.array([s[i] * row_contr[i] for i in range(len(covered))])
    xs_var = float(np.sum(shares))
    dec_var = 0.0
    for rec in response.get("decay_sensitivities", []):
        if rec["parameter"]["covered"]:
            dec_var += (rec["value"] *
                        rec["parameter"]["standard_uncertainty_s"]) ** 2
    yield_var = 0.0
    for rec in response.get("yield_sensitivities", []):
        if rec["parameter"]["covered"]:
            yield_var += (rec["value"] *
                          rec["parameter"]["standard_uncertainty"]) ** 2
    total = xs_var + dec_var + yield_var
    product = target_ = decay_iso = ground = 0.0
    for c, share in zip(covered, shares):
        p = c["parameter"]
        if p["LFS"] > 0:
            product += share
        elif p["target_LISO"] > 0:
            target_ += share
        else:
            ground += share
    for rec in response.get("decay_sensitivities", []):
        if rec["parameter"]["covered"]:
            v = (rec["value"] *
                 rec["parameter"]["standard_uncertainty_s"]) ** 2
            if rec["parameter"]["LISO"] > 0:
                decay_iso += v
            else:
                ground += v
    for rec in response.get("yield_sensitivities", []):
        if rec["parameter"]["covered"]:
            v = (rec["value"] *
                 rec["parameter"]["standard_uncertainty"]) ** 2
            if rec["parameter"]["product_LISO"] > 0:
                product += v
            else:
                ground += v
    return {"total": total,
            "isomer_product_channels": product / total,
            "isomer_target_channels": target_ / total,
            "isomer_decay_constants": decay_iso / total,
            "ground_channels": ground / total}


def run_case(name: str, mutate=None) -> tuple:
    fx = p58_fixture.build(tmp)
    res = p58_case.run(ACTINV, p58_case.spec(fx), tmp, name)
    return fx, res


checks = []
tmp = Path(tempfile.mkdtemp(prefix="p58_g2_", dir=ROOT / "target"))
fx, res = run_case("ref")
specdoc = p58_case.spec(fx)

responses = res["steps"][-1]["uncertainty"]["responses"]
for name, resp in responses.items():
    iso = resp["isomer"]
    covered = p58_case.covered_sensitivities(resp)
    sigma = collapsed_sigma(fx["library"], fx["covariance"], covered, specdoc)
    ref = reference_shares(resp, sigma, covered)
    check(f"total variance exact: {name}",
          iso["total_propagated_variance"] == ref["total"],
          f"{iso['total_propagated_variance']!r} vs {ref['total']!r}")
    for b in BUCKETS:
        check(f"share {b}: {name}",
              iso["variance_shares"][b] == ref[b] or
              abs(iso["variance_shares"][b] - ref[b]) < 1e-15,
              f"{iso['variance_shares'][b]!r} vs {ref[b]!r}")
    # ranked isomer table: each entry's variance_share reproduces exactly
    pos = {id(c["parameter"]): i for i, c in enumerate(covered)}
    for e in iso["top_isomer_channels"]:
        if e["channel"] != "cross_section_mf33":
            continue
        mt = e["parameter"]["MT"]
        lfs = e["parameter"]["LFS"]
        match = [i for i, c in enumerate(covered)
                 if c["parameter"]["MT"] == mt and
                 c["parameter"]["LFS"] == lfs]
        check(f"entry resolved: {name} {e['channel_label']}",
              len(match) == 1)
        if match:
            i = match[0]
            s = np.array([c["value"] for c in covered])
            acc = sum(sigma[i, j] * s[j] for j in range(len(covered)))
            share_val = s[i] * acc
            check(f"variance_share exact: {name} {e['channel_label']}",
                  e["variance_share"] == share_val,
                  f"{e['variance_share']!r} vs {share_val!r}")

# --- mutation sensitivity: perturb the isomer production cross-section --
import p11_fixtures  # noqa: E402
import shutil  # noqa: E402
mut = dict(fx)
lib2 = tmp / "act58_mut.npz"
shutil.copy(fx["library"], lib2)
d = np.load(lib2)
sig = d["sig"].copy()
sig[4] *= 1.5
np.savez(lib2, rows=d["rows"], sig=sig, bounds=d["bounds"])
mut["library"] = lib2
idx_src = Path(str(fx["library"])).with_name(
    Path(str(fx["library"])).stem + "_index.json")
idx = lib2.with_name(lib2.stem + "_index.json")
index = json.loads(idx_src.read_text())
index["sha256_npz"] = p58_case.sha(lib2)
p11_fixtures.write_json(idx, index)
cov_idx = Path(str(mut["covariance"])).with_name(
    Path(str(mut["covariance"])).stem + "_index.json")
cov_index = json.loads(cov_idx.read_text())
cov_index["activation_library"] = str(lib2)
cov_index["activation_library_sha256"] = p58_case.sha(lib2)
cov_index["activation_index"] = str(idx)
cov_index["activation_index_sha256"] = p58_case.sha(idx)
p11_fixtures.write_json(cov_idx, cov_index)
res_m = p58_case.run(ACTINV, p58_case.spec(mut), tmp, "mut")
ref_iso = responses["activity:Mn57m1"]["isomer"]["variance_shares"]
mut_iso = res_m["steps"][-1]["uncertainty"]["responses"]["activity:Mn57m1"][
    "isomer"]["variance_shares"]
check("isomer-share moves under mutation",
      ref_iso["isomer_product_channels"] != mut_iso["isomer_product_channels"]
      or ref_iso["isomer_decay_constants"] != mut_iso["isomer_decay_constants"],
      f"{ref_iso['isomer_product_channels']!r} -> "
      f"{mut_iso['isomer_product_channels']!r}")

failed = [c for c in checks if not c["pass"]]
OUT.write_text(json.dumps(
    {"pass": not failed, "n": len(checks), "failed": failed},
    indent=1) + "\n")
print(json.dumps({"pass": not failed, "n": len(checks),
                  "failed": [c["name"] for c in failed]}))
sys.exit(1 if failed else 0)
