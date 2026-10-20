#!/usr/bin/env python3
"""P148 R5: classify every difference between a baseline produced by the pre-P148 binary and the same baseline
produced by the candidate.

Usage: p148_baselines.py OLD NEW [--label NAME] [--out FILE] | p148_baselines.py --selftest

OLD and NEW are JSON files, or directories whose JSON files are paired by relative path. The dropped nuclides are
the names that appear in an `inventory` on the old side only (none may appear on the new side only). Each
difference is one of:
  - pruning: inside a `rate_pruning` block or a `screen` certificate, or a pruned-state count;
  - drop_set: a row, key or list entry for a dropped nuclide; a populated or below-floor state count that moves by
    at most the number of dropped nuclides; a below-floor sum or bound that does not increase; `pathway_closure`
    equal to the old per-nuclide maximum over the nuclides that remain; the merged photon `lines` of a step,
    once the dropped nuclides' own lines (from `by_nuclide`) are removed from the old side;
  - hash: a SHA-256 or normalized-result hash field (these move whenever any content above moves);
  - bounded: a heat value whose change is within the document's `heat_W_per_g_bound`, or an activity value within
    its `activity_Bq_per_g_bound` (new side);
  - rounding: any other number whose change is within 1e-12 relative (solver rounding);
  - noise: an atoms value that is itself at most 1e-12 of the largest total atoms on both sides (the R2 slack), or
    any value describing a nuclide that is at most 1e-12 of its step's total atoms on both sides;
  - other: anything else. Any `other` difference fails R5.
Timing fields (`ms` and similar) are skipped.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

ROUNDING = 1e-12
PRUNING_KEYS = {"rate_pruning", "screen", "pruned_size", "pruned_states", "dropped_nodes", "kept_unbounded",
                "unbounded_components"}
HASH_MARKERS = ("sha256", "hash")
# Timing is not a result; these keys are skipped.
TIMING_KEYS = {"ms", "ms_total", "wall_s", "solve_wall_s", "elapsed_s"}
# Counts over the populated or below-floor states: they may move by at most the number of dropped nuclides.
SET_COUNTS = {"n_states_populated", "n_states_below_floor", "max_states_below_floor"}
# Sums or bounds over the below-floor states: removing states cannot increase them beyond rounding.
SET_SUMS = {"atoms_below_floor", "heat_bound_from_below_floor_W_per_g", "worst_heat_bound_fraction"}


def documents(root: Path) -> dict[str, Path]:
    return {str(p.relative_to(root)): p for p in sorted(root.rglob("*.json"))}


def walk(node):
    yield node
    if isinstance(node, dict):
        for value in node.values():
            yield from walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from walk(value)


def bounds(node) -> dict[str, float]:
    found = {"heat": 0.0, "activity": 0.0}
    for value in walk(node):
        if isinstance(value, dict):
            for key, name in (("heat_W_per_g_bound", "heat"), ("activity_Bq_per_g_bound", "activity")):
                if isinstance(value.get(key), (int, float)):
                    found[name] = max(found[name], float(value[key]))
    return found


def inventory_names(node) -> set[str]:
    names = set()
    for value in walk(node):
        if isinstance(value, dict) and isinstance(value.get("inventory"), list):
            names |= {r["nuclide"] for r in value["inventory"] if isinstance(r, dict) and "nuclide" in r}
    return names


def closure_gaps(result: dict) -> dict[str, float]:
    """Per-nuclide relative gap between listed chains and inventory, the quantity whose maximum is
    `pathway_closure` (from the reported chains, which keep every chain with fraction of at least 1e-6)."""
    gaps: dict[str, float] = {}
    for k, chains_by_nuclide in enumerate(result.get("pathways") or []):
        inventory = {r["nuclide"]: r["atoms_per_g"] for r in result["steps"][k]["inventory"]}
        largest = max(inventory.values(), default=0.0)
        for nuclide, atoms in inventory.items():
            if atoms <= 0.0:
                continue
            chains = chains_by_nuclide.get(nuclide)
            if chains is None:
                gap = 1.0 if atoms > 1e-12 * largest else 0.0
            else:
                gap = abs(sum(c["atoms_per_g"] for c in chains) - atoms) / atoms
            gaps[nuclide] = max(gaps.get(nuclide, 0.0), gap)
    return gaps


def noise_levels(node) -> tuple[float, list[set[str]]]:
    """Largest total atoms of any step, and for each top-level step the nuclides at or below 1e-12 of that step's
    total atoms (absent ones included): below solver resolution, so values describing them carry no information."""
    largest = 0.0
    for value in walk(node):
        if isinstance(value, dict) and isinstance(value.get("inventory"), list):
            largest = max(largest, sum(max(r.get("atoms_per_g", 0.0), 0.0) for r in value["inventory"]
                                       if isinstance(r, dict)))
    per_step = []
    for step in (node.get("steps") or []) if isinstance(node, dict) else []:
        rows = [r for r in step.get("inventory") or [] if isinstance(r, dict) and "atoms_per_g" in r]
        total = sum(max(r["atoms_per_g"], 0.0) for r in rows)
        per_step.append({r["nuclide"] for r in rows if r["atoms_per_g"] <= ROUNDING * total})
    return largest, per_step


class Context:
    def __init__(self, old, new):
        self.dropped = inventory_names(old) - inventory_names(new)
        self.added = inventory_names(new) - inventory_names(old)
        self.bounds = bounds(new)
        old_total, old_noise = noise_levels(old)
        new_total, new_noise = noise_levels(new)
        self.atoms_slack = ROUNDING * max(old_total, new_total)
        present = [inventory_names(step) for step in (new.get("steps") or [])] if isinstance(new, dict) else []
        # A nuclide is at noise level in a step when it is at or below 1e-12 of the total on both sides, or listed
        # on neither side's inventory for that step.
        self.noise = [(a | (inventory_names(new) - p)) & (b | (inventory_names(new) - p))
                      for a, b, p in zip(old_noise, new_noise, present)]
        self.found: dict[str, list[str]] = {}

    def noise_nuclide(self, path: tuple) -> bool:
        """True for a value under steps/<k>/... or pathways/<k>/... that names a noise-level nuclide of step k."""
        if len(path) < 3 or path[0] not in ("steps", "pathways") or not isinstance(path[1], int):
            return False
        k = path[1]
        return k < len(self.noise) and bool(set(map(str, path[2:])) & self.noise[k])

    def add(self, kind: str, path: tuple, detail: str) -> None:
        self.found.setdefault(kind, []).append(f"{'/'.join(map(str, path))}: {detail}")


def without_dropped(rows: list, dropped: set[str]) -> list:
    return [r for r in rows if not (isinstance(r, dict) and r.get("nuclide") in dropped)]


def classify(old, new, path: tuple, ctx: Context) -> None:
    # A list item takes its name from the nearest named key above it (a list of hashes is still hashes).
    key = next((str(part) for part in reversed(path) if not isinstance(part, int)), "")
    if path and (set(map(str, path)) & PRUNING_KEYS):
        if old != new:
            ctx.add("pruning", path, f"{json.dumps(old)[:80]} -> {json.dumps(new)[:80]}")
        return
    if isinstance(old, dict) and isinstance(new, dict):
        if isinstance(old.get("lines"), list) and isinstance(old.get("by_nuclide"), list) and ctx.dropped:
            # The merged list sums every nuclide's discrete lines by energy, X-ray lines included. Rows present only
            # on the old side (by energy) are the dropped nuclides' if their photons do not exceed the dropped
            # nuclides' total photon emission, activity times source photons per decay.
            emitted = sum((entry.get("activity_Bq_g") or 0.0) * (entry.get("source_photons_per_decay") or 0.0)
                          for entry in old["by_nuclide"]
                          if isinstance(entry, dict) and entry.get("nuclide") in ctx.dropped)
            remaining = collections.Counter(row.get("energy_eV") for row in new.get("lines") or [])
            kept, removed = [], 0.0
            for row in old["lines"]:
                if remaining[row.get("energy_eV")] > 0:
                    remaining[row.get("energy_eV")] -= 1
                    kept.append(row)
                else:
                    removed += row.get("photons_s_g") or 0.0
            if removed > emitted * (1 + ROUNDING):
                ctx.add("other", path + ("lines",), f"old-only lines carry {removed!r} photons/s/g, more than the "
                                                    f"dropped nuclides emit ({emitted!r})")
            if len(kept) != len(old["lines"]):
                ctx.add("drop_set", path + ("lines",), f"{len(old['lines']) - len(kept)} lines of dropped nuclides")
                old = {**old, "lines": kept}
        for name in sorted(set(old) | set(new), key=str):
            if name in TIMING_KEYS:
                continue
            if name in old and name in new:
                classify(old[name], new[name], path + (name,), ctx)
            elif name in ctx.dropped and name in old:
                ctx.add("drop_set", path, f"key {name!r} only in old")
            elif name in PRUNING_KEYS or name.endswith(("_W_per_g_bound", "_Bq_per_g_bound")):
                ctx.add("pruning", path, f"key {name!r} only in {'new' if name in new else 'old'}")
            else:
                ctx.add("other", path, f"key {name!r} only in {'new' if name in new else 'old'}")
        return
    if isinstance(old, list) and isinstance(new, list):
        trimmed = without_dropped(old, ctx.dropped)
        if len(trimmed) != len(old):
            ctx.add("drop_set", path, f"{len(old) - len(trimmed)} rows of dropped nuclides only in old")
            old = trimmed
        if len(old) != len(new):
            ctx.add("other", path, f"list length {len(old)} -> {len(new)}")
            return
        for i, (a, b) in enumerate(zip(old, new)):
            classify(a, b, path + (i,), ctx)
        return
    if old == new:
        return
    if any(m in key.lower() for m in HASH_MARKERS):
        ctx.add("hash", path, f"{str(old)[:16]}… -> {str(new)[:16]}…")
        return
    if not (isinstance(old, (int, float)) and isinstance(new, (int, float))) or isinstance(old, bool):
        ctx.add("other", path, f"{json.dumps(old)[:80]} -> {json.dumps(new)[:80]}")
        return
    old, new = float(old), float(new)
    change = abs(new - old)
    if key in SET_COUNTS and change <= len(ctx.dropped):
        ctx.add("drop_set", path, f"{old:g} -> {new:g}")
    elif key in SET_SUMS and new <= old * (1 + ROUNDING):
        ctx.add("drop_set", path, f"{old!r} -> {new!r}")
    elif change <= ROUNDING * max(abs(old), abs(new)):
        ctx.add("rounding", path, f"{old!r} -> {new!r}")
    elif max(abs(old), abs(new)) <= ctx.atoms_slack and any("atoms" in str(p) for p in path):
        ctx.add("noise", path, f"{old!r} -> {new!r} (slack {ctx.atoms_slack:.3e})")
    elif ctx.noise_nuclide(path):
        ctx.add("noise", path, f"{old!r} -> {new!r} (nuclide below 1e-12 of the step's atoms)")
    elif "heat" in "/".join(map(str, path)) and change <= ctx.bounds["heat"]:
        ctx.add("bounded", path, f"{old!r} -> {new!r} (heat bound {ctx.bounds['heat']:.3e})")
    elif "activity" in "/".join(map(str, path)) and change <= ctx.bounds["activity"]:
        ctx.add("bounded", path, f"{old!r} -> {new!r} (activity bound {ctx.bounds['activity']:.3e})")
    else:
        ctx.add("other", path, f"{old!r} -> {new!r}")


def compare_documents(old: dict, new: dict) -> dict:
    ctx = Context(old, new)
    if ctx.added:
        ctx.add("other", (), f"nuclides only in the new inventory: {sorted(ctx.added)}")
    if isinstance(old, dict) and "pathway_closure" in old and "pathway_closure" in new:
        gaps = closure_gaps(old)
        expected = max((g for n, g in gaps.items() if n not in ctx.dropped), default=0.0)
        old_closure, new_closure = old.pop("pathway_closure"), new.pop("pathway_closure")
        if old_closure != new_closure:
            same = abs(new_closure - expected) <= 1e-9 * max(1.0, abs(expected))
            ctx.add("drop_set" if same else "other", ("pathway_closure",),
                    f"{old_closure!r} -> {new_closure!r}; old maximum over remaining nuclides {expected!r}")
        old["pathway_closure"], new["pathway_closure"] = old_closure, new_closure
        classify({k: v for k, v in old.items() if k != "pathway_closure"},
                 {k: v for k, v in new.items() if k != "pathway_closure"}, (), ctx)
    else:
        classify(old, new, (), ctx)
    return {"dropped_nuclides": sorted(ctx.dropped), "bounds": ctx.bounds,
            "counts": {k: len(v) for k, v in ctx.found.items()},
            "examples": {k: v[:8] for k, v in ctx.found.items()}}


def compare(old_root: Path, new_root: Path) -> dict:
    if old_root.is_file() and new_root.is_file():
        old_docs, new_docs = {"document": old_root}, {"document": new_root}
    else:
        old_docs, new_docs = documents(old_root), documents(new_root)
    report = {"documents": {}, "only_old": sorted(set(old_docs) - set(new_docs)),
              "only_new": sorted(set(new_docs) - set(old_docs))}
    for name in sorted(set(old_docs) & set(new_docs)):
        report["documents"][name] = compare_documents(json.loads(old_docs[name].read_text()),
                                                      json.loads(new_docs[name].read_text()))
    report["other"] = sum(d["counts"].get("other", 0) for d in report["documents"].values())
    report["pass"] = report["other"] == 0 and not report["only_old"] and not report["only_new"]
    return report


def selftest() -> int:
    """Planted changes on a small result pair: the allowed ones must pass, every other one must be caught."""
    def result(drop: bool) -> dict:
        inventory = [{"nuclide": "Fe56", "atoms_per_g": 1.0e22}, {"nuclide": "Mn56", "atoms_per_g": 3.0e10},
                     {"nuclide": "Mn58", "atoms_per_g": 1.0e-12}]
        activity = {"Fe56": 0.0, "Mn56": 2.2e6, "Mn58": 1.0e-13}
        if not drop:
            inventory.append({"nuclide": "Cr55", "atoms_per_g": 1.0e-3})
            activity["Cr55"] = 1.9e-6
        return {"steps": [{"inventory": inventory, "activity_Bq_per_g": activity,
                           "heat_W_per_g": {"total": 1.0e-7}, "n_states_populated": len(inventory)}],
                "pathways": [{"Mn56": [{"from": "Fe56", "atoms_per_g": 3.0e10, "fraction": 1.0}],
                              "Mn58": [{"from": "Fe56", "atoms_per_g": 1.0e-12, "fraction": 1.0}]}],
                "pathway_closure": 0.0,
                "ledger": {"rate_pruning": {"dropped": [], "heat_W_per_g_bound": 1.0e-18,
                                            "activity_Bq_per_g_bound": 2.0e-6}},
                "certificate": {"normalized_result_sha256": "a" * 64}, "ms": 3.0,
                "repeats": {"normalized_result_sha256": ["a" * 64, "a" * 64]}, "tags": ["fe", "mn"]}

    def run(mutate) -> bool:
        old, new = result(False), result(True)
        new["ledger"]["rate_pruning"]["dropped"] = [{"nuclide": "Cr55", "atoms_per_g_bound": 2e-3}]
        new["certificate"]["normalized_result_sha256"] = "b" * 64
        new["repeats"]["normalized_result_sha256"] = ["b" * 64, "b" * 64]
        new["ms"] = 9.0
        mutate(new)
        return compare_documents(old, new)["counts"].get("other", 0) == 0

    allowed = {
        "drop and its consequences": lambda n: None,
        "heat within its bound": lambda n: n["steps"][0]["heat_W_per_g"].__setitem__("total", 1.0e-7 + 5e-19),
        "activity within its bound": lambda n: n["steps"][0]["activity_Bq_per_g"].__setitem__("Mn56", 2.2e6 + 1e-6),
        "rounding": lambda n: n["steps"][0]["inventory"][1].__setitem__("atoms_per_g", 3.0e10 * (1 + 1e-13)),
        "noise-level nuclide": lambda n: n["pathways"][0]["Mn58"][0].__setitem__("fraction", 0.9),
    }
    planted = {
        "heat beyond its bound": lambda n: n["steps"][0]["heat_W_per_g"].__setitem__("total", 1.0e-7 * (1 + 1e-6)),
        "activity beyond its bound": lambda n: n["steps"][0]["activity_Bq_per_g"].__setitem__("Mn56", 2.2e6 * 1.001),
        "kept atoms changed": lambda n: n["steps"][0]["inventory"][1].__setitem__("atoms_per_g", 3.0e10 * 1.01),
        "nuclide only in new": lambda n: n["steps"][0]["inventory"].append({"nuclide": "Co60", "atoms_per_g": 1.0}),
        "string changed": lambda n: n["pathways"][0]["Mn56"][0].__setitem__("from", "Fe57"),
        "count moved too far": lambda n: n["steps"][0].__setitem__("n_states_populated", 1),
        "closure changed": lambda n: n.__setitem__("pathway_closure", 0.5),
        "new key": lambda n: n.__setitem__("extra", 1),
        "item of a hash-free list changed": lambda n: n["tags"].__setitem__(0, "co"),
    }
    failures = [name for name, mutate in allowed.items() if not run(mutate)]
    failures += [name for name, mutate in planted.items() if run(mutate)]
    print(json.dumps({"allowed": sorted(allowed), "planted": sorted(planted), "failures": failures}, indent=1))
    return 1 if failures else 0


def main() -> int:
    if sys.argv[1:] == ["--selftest"]:
        return selftest()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("old", type=Path)
    ap.add_argument("new", type=Path)
    ap.add_argument("--label")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    report = {"label": args.label, **compare(args.old, args.new)}
    text = json.dumps(report, indent=1)
    if args.out:
        args.out.write_text(text + "\n")
    print(text if len(text) < 6000 else json.dumps({k: report[k] for k in ("label", "other", "pass")}))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
