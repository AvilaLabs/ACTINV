#!/usr/bin/env python3
"""P58 G5 — independent checker. Re-parses a produced run document and
re-derives every emitted isomer value from the fixture inputs (own
collapse, own bucket arithmetic, own pathway aggregation). Then proves
the checks catch planted mutations in each emitted field class.
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p58_case  # noqa: E402
import p58_fixture  # noqa: E402

ACTINV = ROOT / "target/debug/actinv"
OUT = ROOT / "results/g5_p58_checker.json"

problems = []
checked = []


def ok(name, cond, detail=""):
    checked.append(name)
    if not cond:
        problems.append({"name": name, "detail": detail})


def load_sidecar(lib_path: Path, cov_path: Path, spec: dict):
    lib = np.load(lib_path)
    rows, sig, bounds = lib["rows"], lib["sig"], lib["bounds"]
    cov = np.load(cov_path)
    grid, values = cov["grid_values"], cov["values"]
    blocks = {}
    for r in cov["components"]:
        n = int(r[8])
        blocks[(int(r[1]), int(r[2]))] = \
            values[int(r[7]):int(r[7]) + n].reshape(int(np.sqrt(n)), -1)
    base = {}
    for i, row in enumerate(rows):
        if int(row[2]) == -1:
            base[int(row[1])] = i
    flux, total = spec["spectrum"]["flux_per_group"], spec["spectrum"]["total"]

    def vec(ri: int) -> np.ndarray:
        mt = int(rows[ri][1])
        b = base[mt]
        v = np.zeros(len(grid) - 1)
        for g, fg in enumerate(flux):
            if fg == 0.0:
                continue
            dens = fg / (bounds[g + 1] - bounds[g]) / total
            mult = 1.0 if ri == b else (
                sig[ri][g] / sig[b][g] if sig[b][g] > 0.0 else 0.0)
            for i in range(len(grid) - 1):
                w = max(min(bounds[g + 1], grid[i + 1])
                        - max(bounds[g], grid[i]), 0.0)
                v[i] += dens * w * mult
        return v

    return rows, blocks, vec


def isomerish(name: str) -> bool:
    return bool(re.search(r"\dm\d+$", name))


def audit(doc: dict, spec: dict, fx: dict) -> list:
    """Recompute every emitted isomer value; return found problems."""
    found = []
    rows, blocks, rowvec = load_sidecar(fx["library"], fx["covariance"],
                                        spec)
    steps = doc["steps"]
    shares = doc.get("isomer_pathway_shares")
    if not isinstance(shares, list) or len(shares) != len(steps):
        found.append("isomer_pathway_shares missing or mis-sized")
        return found
    for si, (step, share) in enumerate(zip(steps, shares)):
        per = (doc.get("pathways") or [])
        if si < len(per) and per[si] and share["status"] == "emitted":
            iso_atoms = ground_atoms = 0.0
            by_prod = {}
            for chains in per[si].values():
                for p in chains:
                    ground_atoms += p["atoms_per_g"]
                    if isomerish(p["first_product"]):
                        iso_atoms += p["atoms_per_g"]
                        by_prod[p["first_product"]] = \
                            by_prod.get(p["first_product"], 0.0) \
                            + p["atoms_per_g"]
            if share["atoms_through_isomer_products_per_g"] != iso_atoms:
                found.append(
                    f"step{si}: atoms_through_isomer_products mismatch "
                    f"{share['atoms_through_isomer_products_per_g']!r} "
                    f"vs {iso_atoms!r}")
            expect = iso_atoms / ground_atoms if ground_atoms > 0 else None
            if share["share"] != expect:
                found.append(f"step{si}: pathway share mismatch "
                             f"{share['share']!r} vs {expect!r}")
            top = share["top_isomer_products"]
            recomputed = sorted(by_prod.items(), key=lambda kv: -kv[1])[:10]
            if [t["first_product"] for t in top] != \
                    [k for k, _ in recomputed]:
                found.append(f"step{si}: top_isomer_products order "
                             "mismatch")
            for t, (k, v) in zip(top, recomputed):
                if t["atoms_per_g"] != v or t["share_of_isomer_flow"] != (
                        v / iso_atoms if iso_atoms > 0 else 0.0):
                    found.append(f"step{si}: isomer product {k} values "
                                 "mismatch")
        for resp_name, resp in step["uncertainty"]["responses"].items():
            iso = resp.get("isomer")
            if iso is None:
                found.append(f"step{si} {resp_name}: isomer block absent")
                continue
            covered = [s for s in resp["sensitivities"]
                       if s["parameter"]["covariance_covered"]
                       and not s["parameter"]["covariance_excluded"]]
            svec = np.array([c["value"] for c in covered])
            sig = np.zeros((len(covered), len(covered)))
            for i, ci in enumerate(covered):
                ri = ci["parameter"]["library_row"]
                for j, cj in enumerate(covered):
                    rj = cj["parameter"]["library_row"]
                    mi, mj = int(rows[ri][1]), int(rows[rj][1])
                    blk = blocks.get((mi, mj))
                    if blk is not None:
                        left, right = rowvec(ri), rowvec(rj)
                    else:
                        blk = blocks.get((mj, mi))
                        if blk is None:
                            continue
                        left, right = rowvec(rj), rowvec(ri)
                    acc = 0.0
                    for a in range(len(left)):
                        for b in range(len(right)):
                            acc += left[a] * blk[a, b] * right[b]
                    sig[i, j] = acc
            var_share = np.zeros(len(covered))
            for i in range(len(covered)):
                acc = 0.0
                for j in range(len(covered)):
                    acc += sig[i, j] * svec[j]
                var_share[i] = svec[i] * acc
            buckets = {"isomer_product_channels": 0.0,
                       "isomer_target_channels": 0.0,
                       "isomer_decay_constants": 0.0,
                       "ground_channels": 0.0}
            for c, v in zip(covered, var_share):
                p = c["parameter"]
                if p["LFS"] > 0:
                    buckets["isomer_product_channels"] += v
                elif p["target_LISO"] > 0:
                    buckets["isomer_target_channels"] += v
                else:
                    buckets["ground_channels"] += v
            total = float(np.sum(var_share))
            for rec in resp.get("decay_sensitivities", []):
                if rec["parameter"]["covered"]:
                    v = (rec["value"] *
                         rec["parameter"]["standard_uncertainty_s"]) ** 2
                    total += v
                    key = "isomer_decay_constants" \
                        if rec["parameter"]["LISO"] > 0 else "ground_channels"
                    buckets[key] += v
            for rec in resp.get("yield_sensitivities", []):
                if rec["parameter"]["covered"]:
                    v = (rec["value"] *
                         rec["parameter"]["standard_uncertainty"]) ** 2
                    total += v
                    key = "isomer_product_channels" \
                        if rec["parameter"]["product_LISO"] > 0 \
                        else "ground_channels"
                    buckets[key] += v
            # the band's total uses the triple-product fold, not sum(shares)
            total = 0.0
            for i in range(len(covered)):
                for j in range(len(covered)):
                    total += svec[i] * sig[i, j] * svec[j]
            for rec in resp.get("decay_sensitivities", []):
                if rec["parameter"]["covered"]:
                    total += (rec["value"] *
                              rec["parameter"]["standard_uncertainty_s"]) ** 2
            for rec in resp.get("yield_sensitivities", []):
                if rec["parameter"]["covered"]:
                    total += (rec["value"] *
                              rec["parameter"]["standard_uncertainty"]) ** 2
            if iso["total_propagated_variance"] != total:
                found.append(f"step{si} {resp_name}: total variance "
                             f"{iso['total_propagated_variance']!r} vs "
                             f"{total!r}")
            ranked = total > 0.0 and np.isfinite(total)
            for b, v in buckets.items():
                expect = v / total if ranked else None
                if iso["variance_shares"][b] != expect:
                    found.append(f"step{si} {resp_name} bucket {b}: "
                                 f"{iso['variance_shares'][b]!r} vs "
                                 f"{expect!r}")
            l2 = sum(c["value"] ** 2 for c in resp["sensitivities"]
                     if c["value"] != 0.0 and
                     (not c["parameter"]["covariance_covered"]
                      or c["parameter"]["covariance_excluded"]))
            l2 += sum(rec["value"] ** 2
                      for rec in resp.get("decay_sensitivities", [])
                      if rec["value"] != 0.0
                      and not rec["parameter"]["covered"])
            l2 += sum(rec["value"] ** 2
                      for rec in resp.get("yield_sensitivities", [])
                      if rec["value"] != 0.0
                      and not rec["parameter"]["covered"])
            if iso["variance_shares"]["unranked_l2_sensitivity"] != \
                    float(np.sqrt(l2)):
                found.append(f"step{si} {resp_name}: unranked L2 mismatch "
                             f"{iso['variance_shares']['unranked_l2_sensitivity']!r} "
                             f"vs {float(np.sqrt(l2))!r}")
            isomer_entries = []
            for c, v in zip(covered, var_share):
                p = c["parameter"]
                if p["LFS"] > 0 or p["target_LISO"] > 0:
                    isomer_entries.append(v)
            for rec in resp.get("decay_sensitivities", []):
                if rec["parameter"]["covered"] \
                        and rec["parameter"]["LISO"] > 0:
                    isomer_entries.append(
                        (rec["value"] *
                         rec["parameter"]["standard_uncertainty_s"]) ** 2)
            n_entries = len(iso["top_isomer_channels"])
            isomer_entries.sort(key=abs, reverse=True)
            emitted_vs = [e["variance_share"]
                          for e in iso["top_isomer_channels"]]
            if emitted_vs != isomer_entries[:n_entries]:
                found.append(f"step{si} {resp_name}: ranked table "
                             "variance_share mismatch")
            for e in iso["top_isomer_channels"]:
                if ranked and e["share_fraction"] != \
                        e["variance_share"] / total:
                    found.append(f"step{si} {resp_name}: share_fraction "
                                 "mismatch")
    return found


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="p58_g5_", dir=ROOT / "target"))
    fx = p58_fixture.build(tmp)
    specdoc = p58_case.spec(fx)
    doc = p58_case.run(ACTINV, specdoc, tmp, "ref")
    found = audit(doc, specdoc, fx)
    ok("reference document reproduces", not found, json.dumps(found[:6]))

    # Mutation legs: each planted edit must be caught.
    mut = json.loads(json.dumps(doc))
    r0 = mut["steps"][-1]["uncertainty"]["responses"]["heat.total"]
    vs = r0["isomer"]["variance_shares"]
    vs["isomer_product_channels"], vs["ground_channels"] = \
        vs["ground_channels"], vs["isomer_product_channels"]
    ok("bucket-label swap caught", audit(mut, specdoc, fx),
       "swap went undetected")

    mut = json.loads(json.dumps(doc))
    r0 = mut["steps"][-1]["uncertainty"]["responses"]["heat.total"]
    r0["isomer"]["top_isomer_channels"][0]["variance_share"] *= 2.0
    ok("variance_share mutation caught", audit(mut, specdoc, fx))

    mut = json.loads(json.dumps(doc))
    mut["isomer_pathway_shares"][0]["share"] = 0.999
    ok("pathway-share mutation caught", audit(mut, specdoc, fx))

    mut = json.loads(json.dumps(doc))
    mut["isomer_pathway_shares"][0]["top_isomer_products"][0][
        "first_product"] = "Mn56"
    ok("isomer-product rename caught", audit(mut, specdoc, fx))

    # audit findings on the reference doc count as problems
    out = {"pass": not problems and not found,
           "checks": len(checked), "problems": problems,
           "audit_findings": found}
    OUT.write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps({"pass": out["pass"], "checks": len(checked),
                      "problems": len(problems)}))
    return 0 if out["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
