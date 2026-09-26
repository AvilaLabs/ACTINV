#!/usr/bin/env python3
"""P60 G5 — independent checker. Re-parses a produced run document and
re-derives every emitted design value from the fixture inputs (own
collapse, own Schur arithmetic, own ranking). Then proves the checks
catch planted mutations in each emitted field class.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p58_fixture  # noqa: E402
import p60_case  # noqa: E402

ACTINV = ROOT / "target/debug/actinv"
OUT = ROOT / "results/g5_p60_checker.json"

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


def audit(doc: dict, spec: dict, fx: dict) -> list:
    """Recompute every emitted design value; return found problems."""
    found = []
    rows, blocks, rowvec = load_sidecar(fx["library"], fx["covariance"], spec)
    for si, step in enumerate(doc["steps"]):
        for resp_name, resp in step["uncertainty"]["responses"].items():
            des = resp.get("design")
            if des is None:
                found.append(f"step{si} {resp_name}: design block absent")
                continue
            covered = [s for s in resp["sensitivities"]
                       if s["parameter"]["covariance_covered"]
                       and not s["parameter"]["covariance_excluded"]]
            svec = np.array([c["value"] for c in covered])
            n = len(covered)
            sig = np.zeros((n, n))
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
            sigma_s = np.zeros(n)
            for i in range(n):
                acc = 0.0
                for j in range(n):
                    acc += sig[i, j] * svec[j]
                sigma_s[i] = acc
            # the band's total uses the triple-product fold
            total = 0.0
            for i in range(n):
                for j in range(n):
                    total += svec[i] * sig[i, j] * svec[j]
            for rec in resp.get("decay_sensitivities", []):
                if rec["parameter"]["covered"]:
                    total += (rec["value"] *
                              rec["parameter"]["standard_uncertainty_s"]) ** 2
            for rec in resp.get("yield_sensitivities", []):
                if rec["parameter"]["covered"]:
                    total += (rec["value"] *
                              rec["parameter"]["standard_uncertainty"]) ** 2
            if des["total_propagated_variance"] != total:
                found.append(f"step{si} {resp_name}: total variance "
                             f"{des['total_propagated_variance']!r} vs "
                             f"{total!r}")
            ranked = total > 0.0 and np.isfinite(total)
            # per-parameter reductions
            expected_xs = {}
            for i, c in enumerate(covered):
                p = c["parameter"]
                if sig[i, i] > 0.0:
                    expected_xs[(p["MT"], p["LFS"], p["library_row"])] = {
                        "reduction": sigma_s[i] * sigma_s[i] / sig[i, i],
                        "share": svec[i] * sigma_s[i],
                    }
            for e in des["top_parameters"]:
                if e["channel"] == "cross_section_mf33":
                    p = e["parameter"]
                    key = (p["MT"], p["LFS"], p["library_row"])
                    exp = expected_xs.get(key)
                    if exp is None:
                        found.append(f"step{si} {resp_name}: unresolved xs "
                                     f"entry {key}")
                        continue
                    if e["variance_reduction"] != exp["reduction"]:
                        found.append(f"step{si} {resp_name} {key}: "
                                     f"reduction {e['variance_reduction']!r}"
                                     f" vs {exp['reduction']!r}")
                    if e["variance_share"] != exp["share"]:
                        found.append(f"step{si} {resp_name} {key}: share "
                                     f"{e['variance_share']!r} vs "
                                     f"{exp['share']!r}")
                else:
                    records = (resp.get("decay_sensitivities", [])
                               if e["channel"] == "decay_constants"
                               else resp.get("yield_sensitivities", []))
                    match = [r for r in records
                             if json.dumps(r["parameter"], sort_keys=True)
                             == json.dumps(e["parameter"], sort_keys=True)]
                    if len(match) != 1:
                        found.append(f"step{si} {resp_name}: unresolved "
                                     f"{e['channel']} entry")
                        continue
                    sk = ("standard_uncertainty_s"
                          if e["channel"] == "decay_constants"
                          else "standard_uncertainty")
                    red = (match[0]["value"]
                           * match[0]["parameter"][sk]) ** 2
                    if e["variance_reduction"] != red:
                        found.append(f"step{si} {resp_name}: diagonal "
                                     "reduction mismatch")
                    if e["reduction_at_half_uncertainty"] != 0.75 * red:
                        found.append(f"step{si} {resp_name}: half-"
                                     "uncertainty mismatch")
                if e["posterior_variance"] != total - e["variance_reduction"]:
                    found.append(f"step{si} {resp_name}: posterior mismatch")
                if ranked and e["share_of_total"] != \
                        e["variance_reduction"] / total:
                    found.append(f"step{si} {resp_name}: share_of_total "
                                 "mismatch")
            # ranking order reproduces
            emitted_red = [abs(e["variance_reduction"])
                           for e in des["top_parameters"]]
            if emitted_red != sorted(emitted_red, reverse=True):
                found.append(f"step{si} {resp_name}: parameter ranking "
                             "order mismatch")
            # target blocks: own Cholesky on the sub-block
            tgroups: dict = {}
            for i, c in enumerate(covered):
                tgroups.setdefault((c["parameter"]["target_ZA"],
                                    c["parameter"]["target_LISO"]),
                                   []).append(i)
            expected_blocks = {}
            for key, idx in tgroups.items():
                sub = np.array([[sig[a, b] for b in idx] for a in idx])
                rhs = np.array([sigma_s[i] for i in idx])
                try:
                    x = np.linalg.solve(sub, rhs)
                    expected_blocks[key] = float(
                        sum(rhs[i] * x[i] for i in range(len(idx))))
                except np.linalg.LinAlgError:
                    expected_blocks[key] = None
            seen = set()
            for r in des["top_reactions"]:
                key = (r["target_za"], r["target_liso"])
                seen.add(key)
                exp = expected_blocks.get(key, "absent")
                if exp == "absent":
                    found.append(f"step{si} {resp_name}: unknown block {key}")
                elif r["status"] == "emitted":
                    if exp is None:
                        found.append(f"step{si} {resp_name} {key}: emitted "
                                     "where checker found singular")
                    elif abs(r["variance_reduction"] - exp) > \
                            max(1e-12 * abs(exp), 1e-290):
                        found.append(f"step{si} {resp_name} {key}: block "
                                     f"reduction {r['variance_reduction']!r} "
                                     f"vs {exp!r}")
                elif exp is not None:
                    found.append(f"step{si} {resp_name} {key}: flagged "
                                 "ill_conditioned but checker solved it")
    return found


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="p60_g5_", dir=ROOT / "target"))
    fx = p58_fixture.build(tmp)
    specdoc = p60_case.spec(fx)
    doc = p60_case.run(ACTINV, specdoc, tmp, "ref")
    found = audit(doc, specdoc, fx)
    ok("reference document reproduces", not found, json.dumps(found[:6]))

    # Mutation legs: each planted edit must be caught.
    mut = json.loads(json.dumps(doc))
    r0 = mut["steps"][-1]["uncertainty"]["responses"]["heat.total"]
    e0 = r0["design"]["top_parameters"][0]
    e0["variance_reduction"] *= 2.0
    ok("reduction mutation caught", audit(mut, specdoc, fx),
       "mutation went undetected")

    mut = json.loads(json.dumps(doc))
    r0 = mut["steps"][-1]["uncertainty"]["responses"]["heat.total"]
    p = r0["design"]["top_parameters"]
    p[0], p[1] = p[1], p[0]
    ok("ranking-order mutation caught", audit(mut, specdoc, fx))

    mut = json.loads(json.dumps(doc))
    r0 = mut["steps"][-1]["uncertainty"]["responses"]["heat.total"]
    r0["design"]["top_reactions"][0]["variance_reduction"] *= 0.5
    ok("block-reduction mutation caught", audit(mut, specdoc, fx))

    mut = json.loads(json.dumps(doc))
    r0 = mut["steps"][-1]["uncertainty"]["responses"]["heat.total"]
    r0["design"]["top_parameters"][0]["posterior_variance"] *= 0.99
    ok("posterior mutation caught", audit(mut, specdoc, fx))

    out = {"pass": not problems and not found,
           "checks": len(checked), "problems": problems,
           "audit_findings": found}
    OUT.write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps({"pass": out["pass"], "checks": len(checked),
                      "problems": len(problems)}))
    return 0 if out["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
