#!/usr/bin/env python3
"""P53 G5 — independent checker for actinv-r2s-joint-1.

Re-derives every emitted number from the raw mesh ndjson + covariance
sidecar with its own implementation of the joint quadratic form:

  J_{c,p} = sum_n (P_{c,n}/A_{c,n}) * s_{c,n,p}      (banded nuclides)
  Var(T)  = Jᵀ Σ_joint J                            (sparse accumulate)
  rho_cc' = Cov(T_c,T_c') / (sigma_c sigma_c')

Only covariance components whose (target, MT) pair appears among the
selected covered rows can contribute, so the checker filters the sidecar
to those — but assembles each touched block completely, applies the P20
exclusion diagnosis identically, and removes excluded entries before the
quadratic form. The sparse covariance is J-independent, so mutation legs
reuse it and only recompute J + the quadratic form. Rejects planted
mutations.
"""
from __future__ import annotations

import json
import math
import os
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p53_artifacts as p53a  # noqa: E402

BIN = Path(os.environ.get("ACTINV_BIN", ROOT / "target/release/actinv"))
OUT = ROOT / "results/check_g5_p53.json"
MESH = ROOT / "results/p53_mesh.ndjson"
SPEC = ROOT / "results/p53_mesh_spec.json"
JOINT = ROOT / "results/p53_r2s_joint.ndjson"
EMIT_STEP = 4
REL_TOL = 1e-8


def close(a: float, b: float, tol: float = REL_TOL) -> bool:
    return abs(a - b) <= tol * max(abs(a), abs(b), 1e-300)


def load_cells(mesh_path: Path):
    cells = []
    with mesh_path.open() as fh:
        for line in fh:
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec["record"] == "cell":
                cells.append(rec)
            elif rec["record"] == "footer":
                break
    return cells


def extract_joint_inputs(mesh_path: Path, step: int):
    """Per-cell pooled J over (spectrum=c, covered row) and the nuclide
    sigma bookkeeping identical to the emit path's rules."""
    cells = load_cells(mesh_path)
    per_cell_sens = []   # list of dict row -> w*s accumulated
    sigma_i = []         # per-cell list of banded nuclide sigmas
    unbanded_share = []
    totals = []
    selected = set()
    for rec in cells:
        step_out = [s for s in rec["result"]["steps"]
                    if s["step"] == step][0]
        responses = step_out["uncertainty"]["responses"]
        sens: dict[int, float] = {}
        sigs = []
        unbanded = 0.0
        for entry in step_out["photon_source"]["by_nuclide"]:
            strength = sum(g["photons_s"] for g in entry["groups"])
            if not (math.isfinite(strength) and strength > 0):
                continue
            resp = responses.get(f"activity:{entry['nuclide']}")
            nominal = resp.get("nominal") if resp else None
            rel = None
            if resp and nominal and nominal > 0:
                su = resp.get("combined_standard_uncertainty",
                              resp.get("mf33_standard_uncertainty"))
                if su is not None and su >= 0:
                    rel = su / nominal
            if rel is None:
                unbanded += strength
                continue
            sigs.append(strength * rel)
            w = strength / nominal
            for s in resp.get("sensitivities", []):
                p = s["parameter"]
                if p.get("covariance_covered"):
                    selected.add(p["library_row"])
                v = s.get("value", 0.0)
                if v:
                    sens[p["library_row"]] = sens.get(p["library_row"], 0.0) \
                        + w * v
        per_cell_sens.append(sens)
        sigma_i.append(sigs)
        total = step_out["photon_source"]["total_photons_s"]
        totals.append(total)
        unbanded_share.append(unbanded / total if total > 0 else 0.0)
    return cells, per_cell_sens, sigma_i, unbanded_share, totals, selected


def build_joint_covariance(lib_path: Path, cov_path: Path,
                           phis: list[list[float]], selected):
    """Sparse joint covariance over (spectrum, covered row) params —
    computed once; independent of J. Returns dict with entries (sparse
    map over param indices), covered rows, and the exclusion list."""
    import p11_covariance as pc

    activation = pc.load_activation(lib_path)
    rows, sig, bounds = activation["rows"], activation["sig"], \
        activation["bounds"]
    ngroups = len(bounds) - 1
    s_count = len(phis)

    with np.load(cov_path, allow_pickle=False) as z:
        descriptors = np.asarray(z["components"], dtype=np.int64)
        offsets = np.asarray(z["grid_offsets"], dtype=np.int64)
        grid_values = np.asarray(z["grid_values"], dtype=np.float64)
        stored_values = np.asarray(z["values"], dtype=np.float64)

    self_covered = set()
    for d in descriptors:
        t, mt, mt1 = int(d[0]), int(d[1]), int(d[2])
        if mt == mt1:
            self_covered.add((t, mt))
    covered = [r for r in sorted(selected)
               if rows[r, 4] != 10 and
               (int(rows[r, 0]), int(rows[r, 1])) in self_covered]
    n = len(covered)
    base = {(int(r[0]), int(r[1])): i for i, r in enumerate(rows)
            if int(r[2]) == -1}
    totals_flux = [float(sum(p)) for p in phis]

    by_key: dict[tuple[int, int], list[int]] = {}
    for p, row in enumerate(covered):
        by_key.setdefault((int(rows[row, 0]), int(rows[row, 1])), []) \
            .append(p)

    def vec(row, base_row, grid, relative, flux, total):
        out = np.zeros(len(grid) - 1)
        if total == 0.0:
            return out
        for g in range(ngroups):
            fg = flux[g]
            if fg == 0.0:
                continue
            low, high = bounds[g], bounds[g + 1]
            if relative:
                mult = sig[row, g]
            elif row == base_row:
                mult = 1.0
            elif sig[base_row, g] > 0.0:
                mult = sig[row, g] / sig[base_row, g]
            else:
                mult = 0.0  # base zero: Rust errors unless row is also
                # zero, which is the only case reachable here
            for k in range(len(grid) - 1):
                width = max(0.0, min(high, grid[k + 1]) - max(low, grid[k]))
                if width:
                    out[k] += fg / (high - low) / total * width * mult
        return out

    entries: dict[tuple[int, int], float] = {}
    vcache: dict[tuple, np.ndarray] = {}
    excluded_keys = []

    def getv(row, base_row, grid_idx, relative, s_idx, grid):
        key = (row, grid_idx, relative, s_idx)
        if key not in vcache:
            vcache[key] = vec(row, base_row, grid, relative,
                              phis[s_idx], totals_flux[s_idx])
        return vcache[key]

    for d in descriptors:
        t, mt, mt1, lb, kind, rg, cg, off, ln = (int(x) for x in d)
        if (t, mt) not in by_key or (t, mt1) not in by_key:
            continue
        grid_l = grid_values[offsets[rg]:offsets[rg + 1]].tolist()
        grid_r = grid_values[offsets[cg]:offsets[cg + 1]].tolist()
        vals = stored_values[off:off + ln]
        for lp in by_key[(t, mt)]:
            lrow = covered[lp]
            lb_row = base[(t, mt)]
            for rp in by_key[(t, mt1)]:
                rrow = covered[rp]
                rb_row = base[(t, mt1)]
                for sl in range(s_count):
                    lv = getv(lrow, lb_row, rg, kind == 1, sl, grid_l) \
                        if kind in (0, 1) else None
                    for sr in range(s_count):
                        if kind in (0, 1):  # Absolute / Relative
                            rv = getv(rrow, rb_row, cg, kind == 1,
                                      sr, grid_r)
                            m = np.asarray(vals).reshape(len(lv), len(rv))
                            v = float(lv @ m @ rv)
                        else:  # ShortRange8 (kind 8) / ShortRange9 (kind 9)
                            v = 0.0
                            for g in range(ngroups):
                                fl, fr = phis[sl][g], phis[sr][g]
                                if fl == 0.0 or fr == 0.0:
                                    continue
                                low, high = bounds[g], bounds[g + 1]
                                gw = high - low
                                lr_ = 1.0 if lrow == lb_row else (
                                    sig[lrow, g] / sig[lb_row, g]
                                    if sig[lb_row, g] > 0.0 else 0.0)
                                rr_ = 1.0 if rrow == rb_row else (
                                    sig[rrow, g] / sig[rb_row, g]
                                    if sig[rb_row, g] > 0.0 else 0.0)
                                for k in range(len(vals)):
                                    gl, gr = grid_l[k], grid_l[k + 1]
                                    width = max(0.0, min(high, gr)
                                                - max(low, gl))
                                    if width == 0.0:
                                        continue
                                    cw = gr - gl
                                    wl = fl * width / gw / totals_flux[sl]
                                    wr = fr * width / gw / totals_flux[sr]
                                    var = vals[k] * cw / width \
                                        if kind == 8 else \
                                        vals[k] * (1.0 - width / cw)
                                    v += wl * wr * lr_ * rr_ * var
                        if v != 0.0:
                            e = (sl * n + lp, sr * n + rp)
                            entries[e] = entries.get(e, 0.0) + v
                            if mt != mt1:
                                e2 = (sr * n + rp, sl * n + lp)
                                entries[e2] = entries.get(e2, 0.0) + v

    # Exclusion diagnosis on the assembled union blocks — the frozen P20
    # rule mirrors collapse_sparse_weighted_multi: ALL (target, mt_a,
    # mt_b) blocks are diagnosed on the complete entries map FIRST; only
    # then are excluded blocks' params removed (no cascade between blocks).
    excluded_keys = []
    mts_by_target: dict[int, list[int]] = {}
    for (t, mt) in by_key:
        mts_by_target.setdefault(t, []).append(mt)
    for t, mts in mts_by_target.items():
        smts = sorted(mts)
        for ai, mt_a in enumerate(smts):
            for mt_b in smts[ai:]:
                self_b = mt_a == mt_b
                if not self_b and not any(
                        int(dd[0]) == t and
                        min(int(dd[1]), int(dd[2])) == min(mt_a, mt_b) and
                        max(int(dd[1]), int(dd[2])) == max(mt_a, mt_b)
                        for dd in descriptors):
                    continue
                pa = [s * n + p for s in range(s_count)
                      for p in by_key[(t, mt_a)]]
                pb = [s * n + p for s in range(s_count)
                      for p in by_key[(t, mt_b)]]
                joint = sorted(set(pa + pb))
                sub = np.asarray([[entries.get((x, y), 0.0)
                                   for y in joint] for x in joint])
                sym = 0.5 * (sub + sub.T)
                max_asym = float(np.abs(sub - sub.T).max()) if sub.size \
                    else 0.0
                max_entry = float(np.abs(sub).max()) if sub.size else 0.0
                bad = None
                if max_asym > 1e-9 * max_entry and max_asym > 0.0:
                    bad = "asymmetric_block"
                else:
                    w = np.linalg.eigvalsh(sym) if sub.size else [0.0]
                    lmin, lmax = float(w[0]), float(w[-1])
                    if (lmax > 0.0 and lmin < -1e-10 * lmax) or \
                            (lmax <= 0.0 and lmin < -1e-30):
                        bad = "non_positive_semidefinite"
                if bad:
                    excluded_keys.append((t, mt_a, mt_b, bad))
    # removal phase — after ALL verdicts, matching the Rust two-phase rule
    for (t, mt_a, mt_b, _reason) in excluded_keys:
        if mt_a == mt_b:
            for pi in (s * n + p for s in range(s_count)
                       for p in by_key[(t, mt_a)]):
                for pj in (s * n + p for s in range(s_count)
                           for p in by_key[(t, mt_a)]):
                    entries.pop((pi, pj), None)
        else:
            for pi in (s * n + p for s in range(s_count)
                       for p in by_key[(t, mt_a)]):
                for pj in (s * n + p for s in range(s_count)
                           for p in by_key[(t, mt_b)]):
                    entries.pop((pi, pj), None)
                    entries.pop((pj, pi), None)

    return {"entries": entries, "covered": covered, "n": n,
            "excluded_keys": excluded_keys, "s_count": s_count}


def evaluate(sens_maps, sigma_i, unbanded_share, totals, cov_state):
    """Quadratic form over the precomputed sparse covariance."""
    n = cov_state["n"]
    s_count = cov_state["s_count"]
    row_pos = {row: i for i, row in enumerate(cov_state["covered"])}
    j = np.zeros(s_count * n)
    for c, smap in enumerate(sens_maps):
        for row, w in smap.items():
            if row in row_pos:
                j[c * n + row_pos[row]] += w
    var_total = 0.0
    cov_buckets: dict[tuple[int, int], float] = {}
    for (l, r), v in cov_state["entries"].items():
        c_l, c_r = l // n, r // n
        contrib = j[l] * v * j[r]
        cov_buckets[(c_l, c_r)] = cov_buckets.get((c_l, c_r), 0.0) \
            + contrib
        var_total += contrib
    cell_var = [cov_buckets.get((c, c), 0.0) for c in range(s_count)]
    cell_sigma = [math.sqrt(max(v, 0.0)) for v in cell_var]
    rho = [[1.0 if i == jj and cell_sigma[i] > 0 else
            (cov_buckets.get((i, jj), 0.0) / (cell_sigma[i] * cell_sigma[jj])
             if cell_sigma[i] > 0 and cell_sigma[jj] > 0 else None)
            for jj in range(s_count)] for i in range(s_count)]
    return {
        "sigma_total_correlated": math.sqrt(max(var_total, 0.0)),
        "cell_sigma_correlated": cell_sigma,
        "rho": rho,
        "sigma_i": sigma_i,
        "total_photons": sum(totals),
        "unbanded_share": unbanded_share,
        "excluded_keys": cov_state["excluded_keys"],
    }


def compare_emitted(joint_path: Path, ref: dict) -> list[str]:
    problems = []
    recs = [json.loads(l) for l in joint_path.read_text().splitlines()
            if l.strip()]
    cells = [r for r in recs if r["record"] == "cell"]
    corr = [r for r in recs if r["record"] == "correlation"][0]
    footer = recs[-1]
    if not close(footer["sigma_total_correlated"],
                 ref["sigma_total_correlated"]):
        problems.append(
            f"sigma_correlated emitted {footer['sigma_total_correlated']} "
            f"!= recomputed {ref['sigma_total_correlated']}")
    if not close(footer["total_photons_s"], ref["total_photons"]):
        problems.append("total_photons_s mismatch")
    for i, (c, sig) in enumerate(
            zip(cells, ref["cell_sigma_correlated"])):
        if not close(c["sigma_photons_s_correlated"], sig):
            problems.append(f"cell {i} correlated sigma mismatch")
        if not close(c["coverage"]["unbanded_photon_share"],
                     ref["unbanded_share"][i]):
            problems.append(f"cell {i} unbanded share mismatch")
        indep = math.sqrt(sum(s * s for s in ref["sigma_i"][i]))
        if not close(c["sigma_photons_s_independent"], indep):
            problems.append(f"cell {i} independent sigma mismatch")
        consv = sum(ref["sigma_i"][i])
        if not close(c["sigma_photons_s_conservative"], consv):
            problems.append(f"cell {i} conservative sigma mismatch")
    for i in range(len(ref["rho"])):
        for jj in range(len(ref["rho"])):
            emitted, expected = corr["rho"][i][jj], ref["rho"][i][jj]
            if emitted is None or expected is None:
                if emitted is not expected:
                    problems.append(f"rho[{i}][{jj}] null mismatch")
            elif not close(emitted, expected, 1e-6):
                problems.append(f"rho[{i}][{jj}] emitted {emitted} "
                                f"!= recomputed {expected}")
    emitted_excl = {(b["target"], b["mt"], b["mt1"])
                    for b in corr.get("excluded_block_keys", [])}
    ref_excl = {(t, a, b) for (t, a, b, _) in ref["excluded_keys"]}
    if emitted_excl != ref_excl:
        problems.append(
            f"exclusion set mismatch: emitted {sorted(emitted_excl)} "
            f"vs recomputed {sorted(ref_excl)}")
    return problems


def main() -> int:
    spec = json.loads(SPEC.read_text())
    flux_path = Path(spec["flux"]["path"])
    lib_path = Path(spec["library"]["path"])
    cov_path = Path(spec["uncertainty"]["covariance"]["path"])
    problems = []
    t0 = __import__("time").monotonic()

    cells_in, sens_maps, sigma_i, unbanded_share, totals, selected = \
        extract_joint_inputs(MESH, EMIT_STEP)
    phis = []
    with flux_path.open() as fh:
        for line in fh:
            rec = json.loads(line)
            if rec["record"] == "cell":
                phis.append(list(rec["flux_per_group"]))
    cov_state = build_joint_covariance(lib_path, cov_path, phis,
                                       selected)
    ref = evaluate(sens_maps, sigma_i, unbanded_share, totals,
                   cov_state)
    problems += compare_emitted(JOINT, ref)
    wall = __import__("time").monotonic() - t0

    mutations = {}
    with tempfile.TemporaryDirectory(prefix="p53-g5-", dir="target") as d:
        work = Path(d)
        # Mutation 1 must move JᵀΣJ by more than the comparison tolerance.
        # Doubling one small sensitivity is invisible on the corpus (≈3.8k
        # covarianced params share the variance); scale every sensitivity
        # under photon-emitting activity responses in cell 0 by 1.5.
        first_cell = load_cells(MESH)[0]
        step0 = [s for s in first_cell["result"]["steps"]
                 if s["step"] == EMIT_STEP][0]
        emitting = {
            e["nuclide"]
            for e in step0["photon_source"]["by_nuclide"]
            if sum(g["photons_s"] for g in e["groups"]) > 0.0
        }
        lines = MESH.read_text().splitlines()
        mutated = False
        for i, line in enumerate(lines):
            rec = json.loads(line)
            if rec["record"] == "cell":
                for st in rec["result"]["steps"]:
                    if st["step"] == EMIT_STEP:
                        n_mut = 0
                        for nuclide in sorted(emitting):
                            r = st["uncertainty"]["responses"].get(
                                f"activity:{nuclide}")
                            if r is None:
                                continue
                            for s in r.get("sensitivities", []):
                                if s.get("value"):
                                    s["value"] *= 1.5
                                    n_mut += 1
                        mutated = n_mut > 0
                    if mutated:
                        break
            if mutated:
                lines[i] = json.dumps(rec)
                break
        if mutated:
            mpath = work / "mesh_mut.ndjson"
            mpath.write_text("\n".join(lines) + "\n")
            _, sens_m, _, _, _, sel_m = extract_joint_inputs(
                mpath, EMIT_STEP)
            assert sel_m == selected, \
                "mutation must not change the covered-row set"
            ref_m = evaluate(sens_m, sigma_i, unbanded_share, totals,
                             cov_state)
            mutations["mutation1_changes_result"] = not close(
                ref_m["sigma_total_correlated"],
                ref["sigma_total_correlated"], 1e-6)
        else:
            problems.append("mutation 1 could not find a sensitivity")

        # mutation 2: tamper emitted sigma_correlated
        jlines = JOINT.read_text().splitlines()
        for i, line in enumerate(jlines):
            rec = json.loads(line)
            if rec["record"] == "footer":
                rec["sigma_total_correlated"] *= 1.5
                jlines[i] = json.dumps(rec)
                break
        jmut = work / "joint_mut.ndjson"
        jmut.write_text("\n".join(jlines) + "\n")
        mutations["tampered_footer_detected"] = bool(
            compare_emitted(jmut, ref))

        # mutation 3: tamper a rho entry
        jlines = JOINT.read_text().splitlines()
        for i, line in enumerate(jlines):
            rec = json.loads(line)
            if rec["record"] == "correlation":
                rec["rho"][0][1] = 0.5
                jlines[i] = json.dumps(rec)
                break
        jmut2 = work / "joint_mut2.ndjson"
        jmut2.write_text("\n".join(jlines) + "\n")
        mutations["tampered_rho_detected"] = bool(
            compare_emitted(jmut2, ref))

    if not mutations.get("mutation1_changes_result"):
        problems.append("mutation1_changes_result")
    problems.extend(k for k, v in mutations.items()
                    if k.endswith("_detected") and not v)

    result = {"pass": not problems,
              "checker_wall_s": wall,
              "mutations": mutations,
              "problems": problems}
    OUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
