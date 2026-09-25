#!/usr/bin/env python3
"""P50 independent checker — re-derives every emitted VoI share from the
recorded sensitivities plus the pinned corpus covariance, using the
p11_covariance reference parser/collapse and a local re-implementation of the
exclusion rules (asymmetric or non-PSD blocks zeroed). Re-sorts, verifies
truncation, recomputes `unranked`, and rejects planted mutations.

Usage: check_g4_p50.py [--mutate 1|2|3]
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p50_artifacts as p50a  # noqa: E402
from p11_covariance import load_activation, read_sidecar  # noqa: E402


def collapse(sidecar: list[dict], activation: dict, flux: np.ndarray,
             selected: list[int]) -> dict:
    """Local reference collapse — same algorithm as p11_covariance.collapse
    plus the production zero-base rule (covariance.rs vector_for_grid:
    absolute-kind ratio is 0 where the base row's group sigma is 0 and the
    row's is 0; the corpus presents no nonzero-over-zero-base case, which a
    run would have rejected). Kept local so the shared p11_covariance
    reference file stays byte-stable across phases."""
    rows, sigma, bounds = activation["rows"], activation["sig"], activation["bounds"]
    total_flux = float(np.sum(flux))
    self_covered = {(item["target"], item["mt"])
                    for item in sidecar if item["mt"] == item["mt1"]}
    covered = [row for row in sorted(set(selected))
               if rows[row, 4] != 10
               and (rows[row, 0], rows[row, 1]) in self_covered]
    base = {(int(row[0]), int(row[1])): index
            for index, row in enumerate(rows) if row[2] == -1}
    matrix = np.zeros((len(covered), len(covered)), dtype=np.float64)
    positions: dict[tuple[int, int], list[int]] = {}
    for parameter, row_index in enumerate(covered):
        row = rows[row_index]
        positions.setdefault((int(row[0]), int(row[1])), []).append(parameter)

    def vector(row_index: int, base_index: int, grid: list[float],
               relative: bool) -> np.ndarray:
        result = np.zeros(len(grid) - 1)
        if total_flux == 0.0:
            return result
        for group, group_flux in enumerate(flux):
            if group_flux == 0.0:
                continue
            low, high = bounds[group:group + 2]
            if relative:
                multiplier = sigma[row_index, group]
            elif row_index == base_index:
                multiplier = 1.0
            elif sigma[base_index, group] > 0.0:
                multiplier = (sigma[row_index, group]
                              / sigma[base_index, group])
            elif sigma[row_index, group] == 0.0:
                multiplier = 0.0
            else:
                raise ValueError(
                    f"activation row {row_index} nonzero where base row "
                    f"{base_index} is zero")
            if multiplier == 0.0:
                continue
            for interval, (left, right) in enumerate(zip(grid, grid[1:])):
                width = max(0.0, min(high, right) - max(low, left))
                result[interval] += (group_flux / (high - low) / total_flux
                                     * width * multiplier)
        return result

    represented = {
        (item["target"], min(item["mt"], item["mt1"]), max(item["mt"], item["mt1"]))
        for item in sidecar
    }
    for item in sidecar:
        left_positions = positions.get((item["target"], item["mt"]), [])
        right_positions = positions.get((item["target"], item["mt1"]), [])
        for left_position in left_positions:
            left_row = covered[left_position]
            left_base = base[(item["target"], item["mt"])]
            for right_position in right_positions:
                right_row = covered[right_position]
                right_base = base[(item["target"], item["mt1"])]
                if item["kind"] in ("Absolute", "Relative"):
                    left = vector(left_row, left_base, item["row_grid"],
                                  item["kind"] == "Relative")
                    right = vector(right_row, right_base, item["column_grid"],
                                   item["kind"] == "Relative")
                    values = np.asarray(item["values"]).reshape(len(left),
                                                                len(right))
                    value = float(left @ values @ right)
                else:
                    value = 0.0
                    for group, group_flux in enumerate(flux):
                        if group_flux == 0.0:
                            continue
                        low, high = bounds[group:group + 2]
                        group_width = high - low
                        def ratio(row_i, base_i):
                            if row_i == base_i:
                                return 1.0
                            if sigma[base_i, group] > 0.0:
                                return sigma[row_i, group] / sigma[base_i, group]
                            if sigma[row_i, group] == 0.0:
                                return 0.0
                            raise ValueError("nonzero over zero base")
                        left_ratio = ratio(left_row, left_base)
                        right_ratio = ratio(right_row, right_base)
                        for factor, grid_left, grid_right in zip(
                                item["values"], item["row_grid"],
                                item["row_grid"][1:]):
                            width = max(0.0, min(high, grid_right)
                                        - max(low, grid_left))
                            if width == 0.0:
                                continue
                            covariance_width = grid_right - grid_left
                            weight = group_flux * width / group_width / total_flux
                            variance = (factor * covariance_width / width
                                        if item["kind"] == "ShortRange8"
                                        else factor * (1.0 - width / covariance_width))
                            value += (weight * weight * left_ratio
                                      * right_ratio * variance)
                matrix[left_position, right_position] += value
                if item["mt"] != item["mt1"]:
                    matrix[right_position, left_position] += value
    return {"row_indices": covered,
            "covariance_barn2": matrix.ravel().tolist()}

RESULT = ROOT / "results/p50_voi_result.json"
OUT = ROOT / "results/check_g4_p50.json"
RTOL = 1e-6


def spec_paths(spec: dict):
    return (ROOT / spec["library"]["path"],
            ROOT / spec["uncertainty"]["covariance"]["path"])


def excluded_block_matrix(matrix: np.ndarray, covered_targets: list,
                          by_mt_positions: dict, represented: set) -> np.ndarray:
    """Re-derive the exclusion rule on the assembled matrix: a self block or a
    cross block is excluded if its joint submatrix is asymmetric
    (max|B_ij−B_ji| > 1e-9·max|B|) or its symmetric part is non-PSD
    (λmin < −1e-10·λmax, or λmax ≤ 0 with λmin < −1e-30). Self-block exclusion
    zeroes the principal submatrix; cross-block exclusion zeroes cross terms.
    """
    matrix = matrix.copy()
    excluded = []
    by_target: dict[int, list[int]] = {}
    for (target, mt) in by_mt_positions:
        by_target.setdefault(target, []).append(mt)
    for target in sorted(by_target):
        mts = sorted(by_target[target])
        for a_index, mt_a in enumerate(mts):
            for mt_b in mts[a_index:]:
                is_self = mt_a == mt_b
                if not is_self and (target, min(mt_a, mt_b), max(mt_a, mt_b)) not in represented:
                    continue
                joint = list(by_mt_positions[(target, mt_a)])
                if not is_self:
                    joint += by_mt_positions[(target, mt_b)]
                joint = sorted(joint)
                sub = matrix[np.ix_(joint, joint)]
                max_entry = float(np.max(np.abs(sub))) if sub.size else 0.0
                max_asym = float(np.max(np.abs(sub - sub.T))) if sub.size else 0.0
                if max_asym > 1e-9 * max_entry and max_asym > 0.0:
                    excluded.append({"target": target, "mt": mt_a, "mt1": mt_b,
                                     "reason": "asymmetric_block"})
                    continue
                sym = 0.5 * (sub + sub.T)
                eig = np.linalg.eigvalsh(sym)
                lmin, lmax = (float(eig[0]), float(eig[-1])) if eig.size else (0.0, 0.0)
                if (lmax > 0.0 and lmin < -1e-10 * lmax) or (lmax <= 0.0 and lmin < -1e-30):
                    excluded.append({"target": target, "mt": mt_a, "mt1": mt_b,
                                     "reason": "non_positive_semidefinite"})
    for block in excluded:
        left = by_mt_positions[(block["target"], block["mt"])]
        if block["mt"] == block["mt1"]:
            matrix[np.ix_(left, left)] = 0.0
        else:
            right = by_mt_positions[(block["target"], block["mt1"])]
            matrix[np.ix_(left, right)] = 0.0
            matrix[np.ix_(right, left)] = 0.0
    return matrix, excluded


def rebuild(spec: dict, result: dict):
    """Per-target collapsed covariance blocks with exclusions applied,
    entirely from the pinned artifacts. MF=33 components only ever couple
    rows inside one target, so the global covariance is exactly
    block-diagonal per target — collapsing per target is exact and stays
    inside the memory scope. `selected` is the run's active row set, read
    from the emitted sensitivity parameters (the runtime's boundary)."""
    activation_path, covariance_path = spec_paths(spec)
    activation = load_activation(activation_path)
    flux = np.asarray(spec["spectrum"]["flux_per_group"], dtype=np.float64)
    # The library npz orders groups by ascending energy; a `descending` spec
    # lists flux high-energy-first — fold it into library order.
    if spec["spectrum"].get("descending"):
        flux = flux[::-1]
    rows = activation["rows"]
    selected = sorted({
        record["parameter"]["library_row"]
        for step in result["steps"]
        for response in step["uncertainty"]["responses"].values()
        for record in response["sensitivities"]})
    by_target_rows: dict[int, list[int]] = {}
    for row_index in selected:
        by_target_rows.setdefault(int(rows[row_index][0]), []).append(row_index)

    # blocks[row_index] -> (block_matrix, position_in_block, covered_row_order)
    blocks: dict[int, tuple] = {}
    excluded = []
    for target, target_rows in sorted(by_target_rows.items()):
        sidecar = read_sidecar(covariance_path, target=target)
        collapsed = collapse(sidecar, activation, flux, target_rows)
        covered = collapsed["row_indices"]
        n = len(covered)
        if n == 0:
            continue
        matrix = np.asarray(collapsed["covariance_barn2"]).reshape(n, n)
        by_mt_positions: dict[tuple[int, int], list[int]] = {}
        for position, row_index in enumerate(covered):
            row = rows[row_index]
            by_mt_positions.setdefault((int(row[0]), int(row[1])), []).append(position)
        represented = {
            (item["target"], min(item["mt"], item["mt1"]), max(item["mt"], item["mt1"]))
            for item in sidecar
        }
        matrix, ex = excluded_block_matrix(matrix, covered, by_mt_positions, represented)
        excluded.extend(ex)
        for position, row_index in enumerate(covered):
            blocks[row_index] = (matrix, position)
    return blocks, excluded


def share_map(step_response: dict, blocks: dict):
    """Expected per-parameter share, keyed by (spectrum, library_row, MT).
    `blocks[row_index] -> (block_matrix, position)` comes from `rebuild`.

    The Python reference collapse is single-spectrum; a multi-spectrum run's
    joint covariance carries cross-spectrum blocks this checker cannot
    rebuild, so it rejects those honestly rather than approximating.
    """
    sensitivities = step_response["sensitivities"]
    spectra = {r["parameter"]["spectrum"] for r in sensitivities}
    if spectra != {0}:
        raise RuntimeError("multi-spectrum run: checker cannot rebuild the "
                           "joint covariance")
    # Group covered records by block matrix (one per target).
    members: dict[int, list] = {}
    for record in sensitivities:
        p = record["parameter"]
        if not p["covariance_covered"]:
            continue
        row = p["library_row"]
        if row in blocks:
            members.setdefault(id(blocks[row][0]), []).append(record)
    out = {}
    for records in members.values():
        matrix = blocks[records[0]["parameter"]["library_row"]][0]
        s = np.zeros(matrix.shape[0])
        for record in records:
            s[blocks[record["parameter"]["library_row"]][1]] = record["value"]
        ws = matrix @ s
        for record in records:
            p = record["parameter"]
            pos = blocks[p["library_row"]][1]
            out[(p["spectrum"], p["library_row"], p["MT"])] = s[pos] * ws[pos]
    return out


def entry_key(entry: dict):
    p = entry["parameter"]
    return (p.get("spectrum", 0), p.get("library_row"), p.get("MT"))


def channel_of(entry: dict) -> str:
    return entry["channel"]


def diagonal_expected(step_response: dict, field: str, sigma_key: str):
    """share = (s·σ)² for the diagonal channels."""
    out = {}
    for record in step_response.get(field, []):
        p = record["parameter"]
        if p["covered"]:
            out[p["nuclide" if field == "decay_sensitivities" else "product_nuclide"]
                + f":{p.get('spectrum', 0)}"] = (record["value"] * p[sigma_key]) ** 2
    return out


def check_voi_table(voi: dict, step_response: dict, xs_shares: dict,
                    diag_shares: dict, problems: list, context: str):
    total = voi["total_propagated_variance"]
    emitted = voi["top"]

    # Every emitted MF=33 entry's share matches the independent computation.
    # Tolerance is relative to the share and the total propagated variance —
    # shares are variance terms, so a 1e-6·V floor keeps small-but-real
    # shares checkable without letting misalignment hide as "small".
    share_tol = RTOL * max(total, 1e-300) if total > 0 else RTOL
    for rank, entry in enumerate(emitted):
        share = entry["variance_share"]
        if entry["channel"] == "cross_section_mf33":
            key = entry_key(entry)
            expected = xs_shares.get(key)
            if expected is None:
                problems.append(f"{context}: ranked entry {rank} names a "
                                f"parameter outside the covered matrix")
            elif abs(expected - share) > share_tol + RTOL * abs(expected):
                problems.append(f"{context}: rank {rank} share {share:.6e} "
                                f"!= independent {expected:.6e}")
        else:
            p = entry["parameter"]
            name = p.get("nuclide") or p.get("product_nuclide")
            expected = diag_shares.get(f"{name}:{p.get('spectrum', 0)}")
            expected_share = (entry["sensitivity"]
                              * entry["standard_uncertainty"]) ** 2
            if abs(expected_share - share) > share_tol + RTOL * abs(share):
                problems.append(f"{context}: rank {rank} diagonal share "
                                f"{share:.6e} != (s·σ)² = {expected_share:.6e}")

    # Ordering: nonincreasing |share|; fallback |sensitivity| when V ≤ 0.
    ranked_by_share = total > 0.0 and np.isfinite(total)
    keys = ([abs(e["variance_share"]) for e in emitted] if ranked_by_share
            else [abs(e["sensitivity"]) for e in emitted])
    if keys != sorted(keys, reverse=True):
        problems.append(f"{context}: emitted order is not nonincreasing")

    # Truncation: emitted set must be the true top-|share| set.
    all_shares = list(xs_shares.values()) + list(diag_shares.values())
    if all_shares:
        threshold = (sorted((abs(x) for x in all_shares), reverse=True)
                     [min(len(emitted), len(all_shares)) - 1])
        for e in emitted:
            if abs(e["variance_share"]) < threshold - RTOL:
                problems.append(f"{context}: emitted entry below truncation "
                                f"threshold — not the true top set")

    # Share fractions and total consistency.
    for e in emitted:
        fraction = e["share_fraction"]
        if ranked_by_share:
            if fraction is None or abs(fraction - e["variance_share"] / total) > RTOL:
                problems.append(f"{context}: share_fraction inconsistent")
        elif fraction is not None:
            problems.append(f"{context}: share_fraction set on a zero/nonfinite total")

    # Unranked recomputation.
    expected_unranked = {}
    for record in step_response["sensitivities"]:
        p = record["parameter"]
        if record["value"] != 0.0 and (not p["covariance_covered"]
                                       or p["covariance_excluded"]):
            bucket = expected_unranked.setdefault("cross_section_mf33", [0, 0.0])
            bucket[0] += 1
            bucket[1] += record["value"] ** 2
    for field, channel in [("decay_sensitivities", "decay_constants"),
                           ("yield_sensitivities", "fission_yields")]:
        for record in step_response.get(field, []):
            if record["value"] != 0.0 and not record["parameter"]["covered"]:
                bucket = expected_unranked.setdefault(channel, [0, 0.0])
                bucket[0] += 1
                bucket[1] += record["value"] ** 2
    emitted_unranked = voi.get("unranked", {})
    for channel, (count, l2) in expected_unranked.items():
        got = emitted_unranked.get(channel)
        if not got or got["count"] != count \
                or abs(got["sensitivity_l2"] - l2 ** 0.5) > RTOL * max(l2 ** 0.5, 1e-300):
            problems.append(f"{context}: unranked[{channel}] mismatch "
                            f"(expected count={count}, emitted={got})")
    for channel in emitted_unranked:
        if channel not in expected_unranked:
            problems.append(f"{context}: fabricated unranked family {channel}")


def mutate(result: dict, which: int) -> dict:
    scratch = copy.deepcopy(result)
    for step in scratch["steps"]:
        for response in step["uncertainty"]["responses"].values():
            voi = response.get("voi")
            if not voi or len(voi["top"]) < 2:
                continue
            if which == 1:      # reversed ranking
                voi["top"].reverse()
            elif which == 2:    # zeroed share
                voi["top"][0]["variance_share"] = 0.0
            elif which == 3:    # dropped unranked family / fabricated coverage
                voi["unranked"] = {}
    return scratch


def verify(result: dict, spec: dict) -> list[str]:
    problems: list[str] = []
    blocks, excluded = rebuild(spec, result)
    emitted_excluded = {
        (b["target"], b["mt"], b["mt1"]) for b in
        result["steps"][0]["uncertainty"]["excluded_blocks"]}
    local_excluded = {(b["target"], b["mt"], b["mt1"]) for b in excluded}
    if emitted_excluded != local_excluded:
        problems.append("excluded_block set differs from independent diagnosis")

    tables = 0
    for index, step in enumerate(result["steps"]):
        for name, response in step["uncertainty"]["responses"].items():
            voi = response.get("voi")
            if voi is None:
                problems.append(f"step {index}/{name}: voi missing")
                continue
            tables += 1
            xs_shares = share_map(response, blocks)
            diag_shares = {
                **diagonal_expected(response, "decay_sensitivities",
                                    "standard_uncertainty_s"),
                **diagonal_expected(response, "yield_sensitivities",
                                    "standard_uncertainty"),
            }
            check_voi_table(voi, response, xs_shares, diag_shares,
                            problems, f"step {index}/{name}")
    if tables == 0:
        problems.append("no voi tables emitted")
    return problems


def main() -> int:
    mutate = None
    if "--mutate" in sys.argv:
        mutate = int(sys.argv[sys.argv.index("--mutate") + 1])

    seal = json.load(open(ROOT / "results/g0_p50_seals.json"))
    artifacts = p50a.verify()
    drift = {n: r["path"] for n, r in artifacts.items()
             if r["sha256"] != seal["artifacts"][n]["sha256"]}
    result = json.load(open(RESULT))
    spec = json.load(open(ROOT / seal["artifacts"]["demo_spec"]["path"]))
    if mutate:
        result = mutate(result, mutate)

    problems = verify(result, spec)
    if drift:
        problems.append(f"sealed artifact drift: {sorted(drift)}")

    record = {
        "schema": "actinv-p50-g4-1",
        "result": str(RESULT.relative_to(ROOT)),
        "mutate": mutate,
        "problems": problems,
        "pass": not problems if not mutate else bool(problems),
    }
    if mutate:
        record["pass"] = bool(problems)  # mutation must be rejected
    OUT.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"pass": record["pass"], "problems": len(problems),
                      "mutate": mutate}))
    return 0 if record["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
