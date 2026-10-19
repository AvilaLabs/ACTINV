#!/usr/bin/env python3
"""P148 checker: an independent recomputation of the rate-pruning bounds (R1) and their dominance over
exact or unpruned solutions (R2).

Subcommands:
  synthetic PROBE OUT   build the synthetic battery, run `prune_probe --synthetic`, recompute every edge,
                        component, bound and drop decision, and solve each case densely (scipy expm) with and
                        without pruning to test dominance.
  capture PROBE_JSON    R1 on one spec run written by `prune_probe SPEC OUT` (all captures and the ledger).
  selftest PROBE        planted errors in a probe record must be caught.

The recomputation is written from the protocol, not from the Rust source: strongly connected components by
Kosaraju, the condensation ordered by Kahn's algorithm, the bound inequalities as stated in P148 and
Amendment A.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import random
import subprocess
import sys
import tempfile
from collections import defaultdict, deque
from pathlib import Path

import numpy as np
from scipy.linalg import expm

INF = math.inf
EV = 1.602176634e-19
REL = 1e-10
SWEEP_TOLERANCE = 1e-12
MAX_SWEEPS = 1000
CERTIFICATE_ITERATIONS = 1000
SAMPLES_PER_STEP = 40


def num(v):
    return INF if v == "inf" else float(v)


# ---------------------------------------------------------------- recomputation


def recompute(cap: dict) -> dict:
    n = cap["n"]
    durations = cap["durations_s"]
    multipliers = cap["multipliers"]
    t_total = sum(durations)
    phi_max = max([0.0] + list(multipliers))
    lam = [0.0] * n
    for i, j, v in cap["decay"]:
        if i == j and v < 0:
            lam[j] += -v

    def offdiag(triplets):
        sums = defaultdict(float)
        for i, j, v in triplets:
            if i != j and v > 0:
                sums[(j, i)] += v
        return sums

    edges = []
    for (j, i), a in sorted(offdiag(cap["decay"]).items()):
        edges.append((j, i, a, a / lam[j] if lam[j] > 0 else INF))
    for steps, share in ((cap["feed"], INF), (cap["removal"], 1.0)):
        largest = defaultdict(float)
        for step in steps:
            for key, a in offdiag(step).items():
                largest[key] = max(largest[key], a)
        for (j, i), a in sorted(largest.items()):
            edges.append((j, i, a, share))
    if phi_max > 0:
        largest = {}
        for spectrum in cap["spectra"]:
            loss = defaultdict(float)
            for i, j, v in spectrum:
                if i == j and v < 0:
                    loss[j] += -v
            for (j, i), sigma in offdiag(spectrum).items():
                a = sigma * phi_max
                d = lam[j] + loss[j] * phi_max
                g = a / d if d > 0 else INF
                ra, rg = largest.get((j, i), (0.0, 0.0))
                largest[(j, i)] = (max(ra, a), max(rg, g))
        for (j, i), (a, g) in sorted(largest.items()):
            edges.append((j, i, a, g))
    edges = [e for e in edges if e[2] > 0]

    n0 = [max(0.0, float(v)) for v in cap["n0"]] + [0.0] * max(0, n - len(cap["n0"]))
    source = [v > 0 for v in n0]
    if not cap["rate_mode"]:
        succ = defaultdict(list)
        for j, i, _, _ in edges:
            succ[j].append(i)
        seen = list(source)
        q = deque(i for i in range(n) if source[i])
        while q:
            j = q.popleft()
            for i in succ[j]:
                if not seen[i]:
                    seen[i] = True
                    q.append(i)
        return {"edges": edges, "keep": [i for i in range(n) if seen[i]], "dropped": [], "shortfall": {},
                "lam": lam, "t_total": t_total}

    active = [True] * n
    th, po, fr, unb = bounds(n, edges, active, n0, [0.0] * n, [0.0] * n, lam, t_total)
    keep, dropped = [], []
    kept = [False] * n
    for i in range(n):
        if source[i] or po[i] >= cap["bmin"]:
            keep.append(i)
            kept[i] = True
        elif th[i] > 0:
            dropped.append((i, po[i], fr[i]))
    shortfall = {}
    if dropped:
        xa, xr = [0.0] * n, [0.0] * n
        for e in edges:
            j, i = e[0], e[1]
            if not kept[j] and kept[i] and th[j] > 0:
                xa[i] += transfer(e, t_total, po[j], th[j])
                xr[i] += e[2] * po[j]
        _, epo, _, _ = bounds(n, edges, kept, [0.0] * n, xa, xr, lam, t_total)
        shortfall = {i: epo[i] for i in range(n) if kept[i] and epo[i] > 0}
    return {"edges": edges, "throughput": th, "population": po, "inflow_rate": fr, "unbounded": sorted(unb),
            "keep": keep, "dropped": dropped, "shortfall": shortfall, "lam": lam, "t_total": t_total}


def transfer(e, t_total, pop, thr):
    if thr <= 0 or pop <= 0:
        return 0.0
    by_rate = e[2] * t_total * pop
    return by_rate if math.isinf(e[3]) else min(by_rate, e[3] * thr)


def cap_population(thr, n0, f, lam):
    return min(thr, n0 + f / lam) if lam > 0 else thr


def kosaraju(n, edges, active):
    succ, pred = defaultdict(list), defaultdict(list)
    for j, i, _, _ in edges:
        if active[j] and active[i]:
            succ[j].append(i)
            pred[i].append(j)
    order, seen = [], [False] * n
    for root in range(n):
        if not active[root] or seen[root]:
            continue
        stack = [(root, iter(succ[root]))]
        seen[root] = True
        while stack:
            v, it = stack[-1]
            for w in it:
                if not seen[w]:
                    seen[w] = True
                    stack.append((w, iter(succ[w])))
                    break
            else:
                stack.pop()
                order.append(v)
    comp = [-1] * n
    comps = []
    for root in reversed(order):
        if comp[root] != -1:
            continue
        members, stack = [], [root]
        comp[root] = len(comps)
        while stack:
            v = stack.pop()
            members.append(v)
            for w in pred[v]:
                if comp[w] == -1:
                    comp[w] = len(comps)
                    stack.append(w)
        comps.append(sorted(members))
    return comps, comp


def bounds(n, edges, active, n0, xa, xr, lam, t_total):
    comps, comp = kosaraju(n, edges, active)
    indeg = [0] * len(comps)
    down = defaultdict(set)
    for j, i, _, _ in edges:
        if active[j] and active[i] and comp[j] != comp[i] and comp[i] not in down[comp[j]]:
            down[comp[j]].add(comp[i])
            indeg[comp[i]] += 1
    ready = deque(sorted(c for c in range(len(comps)) if indeg[c] == 0))
    order = []
    while ready:
        c = ready.popleft()
        order.append(c)
        for d in sorted(down[c]):
            indeg[d] -= 1
            if indeg[d] == 0:
                ready.append(d)
    assert len(order) == len(comps)
    preds = defaultdict(list)
    for e in edges:
        if active[e[0]] and active[e[1]]:
            preds[e[1]].append(e)
    th, po, fr = [0.0] * n, [0.0] * n, [0.0] * n
    unbounded = set()
    for c in order:
        members = comps[c]
        b = {i: n0[i] + xa[i] for i in members}
        fe = {i: xr[i] for i in members}
        for i in members:
            for e in preds[i]:
                if comp[e[0]] != c:
                    b[i] += transfer(e, t_total, po[e[0]], th[e[0]])
                    fe[i] += e[2] * po[e[0]]
        if len(members) == 1:
            i = members[0]
            th[i], fr[i] = b[i], fe[i]
            po[i] = cap_population(th[i], n0[i], fr[i], lam[i])
            if not math.isfinite(th[i]):
                unbounded.add(i)
            continue
        internal = {i: [e for e in preds[i] if comp[e[0]] == c] for i in members}
        start = certificate(members, internal, b, fe, t_total)
        if start is None:
            for i in members:
                th[i] = po[i] = fr[i] = INF
                unbounded.add(i)
            continue
        t_cur = dict(start)
        p_cur = dict(start)
        for _ in range(MAX_SWEEPS):
            t_new, p_new, drop = {}, {}, 0.0
            for i in members:
                t = b[i] + sum(transfer(e, t_total, p_cur[e[0]], t_cur[e[0]]) for e in internal[i])
                f = fe[i] + sum(e[2] * p_cur[e[0]] for e in internal[i])
                t = min(t, t_cur[i])
                p = min(cap_population(t, n0[i], f, lam[i]), p_cur[i])
                for old, new in ((t_cur[i], t), (p_cur[i], p)):
                    if old > 0:
                        drop = max(drop, (old - new) / old)
                t_new[i], p_new[i] = t, p
            t_cur, p_cur = t_new, p_new
            if drop <= SWEEP_TOLERANCE:
                break
        for i in members:
            th[i], po[i] = t_cur[i], p_cur[i]
            fr[i] = fe[i] + sum(e[2] * p_cur[e[0]] for e in internal[i])
    return th, po, fr, unbounded


def certificate(members, internal, b, fe, t_total):
    if any(not math.isfinite(b[i]) or not math.isfinite(fe[i]) for i in members):
        return None

    def coefficient(e):
        r = e[2] * t_total
        return r if math.isinf(e[3]) else min(r, e[3])

    v = {i: 1.0 for i in members}
    for _ in range(CERTIFICATE_ITERATIONS):
        nxt = {i: 1.0 + sum(coefficient(e) * v[e[0]] for e in internal[i]) for i in members}
        if any(not math.isfinite(x) for x in nxt.values()):
            return None
        q = max((nxt[i] - 1.0) / v[i] for i in members)
        if q < 1.0:
            scale = max(b[i] / v[i] for i in members) / (1.0 - q)
            return {i: v[i] * scale for i in members}
        v = nxt
    return None


# ---------------------------------------------------------------- comparison


def close(a, b):
    if math.isinf(a) or math.isinf(b):
        return a == b
    return abs(a - b) <= REL * max(abs(a), abs(b)) or abs(a - b) <= 1e-300


def compare(cap: dict, mine: dict) -> list[str]:
    problems = []
    if sorted(cap["keep"]) != mine["keep"]:
        problems.append(f"keep differs: {sorted(set(cap['keep']) ^ set(mine['keep']))[:10]}")
    theirs = {d[0]: (num(d[1]), num(d[2])) for d in cap["dropped"]}
    ours = {d[0]: (d[1], d[2]) for d in mine["dropped"]}
    if set(theirs) != set(ours):
        problems.append(f"dropped set differs: {sorted(set(theirs) ^ set(ours))[:10]}")
    for i in set(theirs) & set(ours):
        for k, label in ((0, "population"), (1, "inflow")):
            if not close(theirs[i][k], ours[i][k]):
                problems.append(f"state {i} {label} {theirs[i][k]!r} vs {ours[i][k]!r}")
    if cap["rate_mode"]:
        for label in ("throughput", "population", "inflow_rate"):
            for i, (a, b) in enumerate(zip(map(num, cap[label]), mine[label])):
                if not close(a, b):
                    problems.append(f"{label}[{i}] {a!r} vs {b!r}")
                    break
        theirs_e = {i: num(e) for i, e in cap["kept_shortfall"]}
        if set(theirs_e) != set(mine["shortfall"]):
            problems.append(f"shortfall states differ: {sorted(set(theirs_e) ^ set(mine['shortfall']))[:10]}")
        for i in set(theirs_e) & set(mine["shortfall"]):
            if not close(theirs_e[i], mine["shortfall"][i]):
                problems.append(f"shortfall[{i}] {theirs_e[i]!r} vs {mine['shortfall'][i]!r}")
    their_edges = sorted((e[0], e[1], e[2], num(e[3])) for e in cap["edges"])
    our_edges = sorted(mine["edges"])
    if len(their_edges) != len(our_edges) or any(
        a[:2] != b[:2] or not close(a[2], b[2]) or not close(a[3], b[3]) for a, b in zip(their_edges, our_edges)
    ):
        problems.append("edge list differs")
    return problems


def response_bounds(cap: dict, mine: dict) -> dict:
    energy = cap.get("energy_ev") or [0.0] * cap["n"]
    lam = mine["lam"]
    da = sum(lam[i] * p for i, p, _ in mine["dropped"])
    dh = sum(lam[i] * energy[i] * EV * p for i, p, _ in mine["dropped"])
    ka = sum(lam[i] * e for i, e in mine["shortfall"].items())
    kh = sum(lam[i] * energy[i] * EV * e for i, e in mine["shortfall"].items())
    return {"dropped_activity_Bq_per_g_bound": da, "dropped_heat_W_per_g_bound": dh,
            "kept_activity_Bq_per_g_bound": ka, "kept_heat_W_per_g_bound": kh,
            "activity_Bq_per_g_bound": da + ka, "heat_W_per_g_bound": dh + kh}


# ---------------------------------------------------------------- synthetic battery


def chain_case(name, n, decay, spectra, n0, schedule, bmin, feed=None, removal=None, tags=(), energy=None):
    steps = len(schedule)
    return {"name": name, "n": n, "decay": decay, "spectra": spectra, "n0": n0, "schedule": schedule,
            "bmin": bmin, "feed": feed or [[] for _ in range(steps)], "removal": removal or [[] for _ in range(steps)],
            "rate_mode": True, "tags": list(tags), "energy_ev": energy or [1e6] * n}


def with_diag(spectrum_offdiag):
    """Adds each parent's loss diagonal: the sum of its outgoing reaction rates."""
    loss = defaultdict(float)
    for i, j, v in spectrum_offdiag:
        loss[j] += v
    return list(spectrum_offdiag) + [[j, j, -v] for j, v in sorted(loss.items())]


def decay_with_diag(edges, lam):
    out = [list(e) for e in edges]
    for j, l in lam.items():
        out.append([j, j, -l])
    return out


def battery() -> list[dict]:
    cases = []
    # R4: converging paths.
    s, l = 1e-10, 1e-3
    cases.append(chain_case("r4_converging_pair", 4,
                            decay_with_diag([[3, 1, l], [3, 2, l]], {1: l, 2: l}),
                            [with_diag([[1, 0, s], [2, 0, s]])], [1e8, 0, 0, 0], [[1e4, 1.0, 0]], 1.5e2,
                            tags=["converging", "r4"]))
    cases.append(chain_case("converging_three_deep", 7,
                            decay_with_diag([[4, 1, l], [4, 2, l], [4, 3, l], [5, 4, 1e-4], [6, 5, 1e-2]],
                                            {1: l, 2: l, 3: l, 4: 1e-4, 5: 1e-2}),
                            [with_diag([[1, 0, s], [2, 0, s], [3, 0, s]])], [1e8, 0, 0, 0, 0, 0, 0],
                            [[1e4, 1.0, 0], [1e5, 0.0, 0]], 1.0, tags=["converging", "cooling"]))
    # Long linear chains.
    for length in (10, 30):
        lams = {k: 10 ** (-1 - (k % 5)) for k in range(1, length)}
        dec = decay_with_diag([[k + 1, k, lams[k]] for k in range(1, length - 1)], lams)
        cases.append(chain_case(f"linear_chain_{length}", length, dec, [with_diag([[1, 0, 1e-8]])],
                                [1e12] + [0] * (length - 1), [[3600, 1.0, 0], [86400, 0.0, 0]], 1e-3,
                                tags=["linear", "cooling"]))
    # Reaction cycle (n,g)/(n,2n) pair and an isomer pair with branch 1.
    cases.append(chain_case("reaction_cycle", 4, decay_with_diag([[3, 2, 1e-5]], {2: 1e-5}),
                            [with_diag([[1, 0, 1e-9], [2, 1, 1e-9], [1, 2, 1e-10]])], [1e15, 0, 0, 0],
                            [[1e5, 1.0, 0]], 1e3, tags=["cycle"]))
    cases.append(chain_case("isomer_pair", 3, decay_with_diag([[2, 1, 1e-4]], {1: 1e-4}),
                            [with_diag([[1, 0, 1e-12], [1, 2, 1e-13]])], [1e20, 0, 0],
                            [[1e6, 1.0, 0], [1e6, 0.0, 0]], 1e6, tags=["cycle", "isomer", "cooling"]))
    # Multiplicity above one (e.g. a gas or fission product yield).
    cases.append(chain_case("multiplicity", 4, decay_with_diag([[3, 2, 1e-3]], {2: 1e-3}),
                            [[[1, 0, 1e-9], [2, 0, 2.5e-9], [0, 0, -1e-9]]], [1e14, 0, 0, 0],
                            [[1e5, 1.0, 0]], 1e2, tags=["multiplicity"]))
    # Trace mode: unit state 0 with no loss feeds products.
    cases.append(chain_case("trace_unit", 5, decay_with_diag([[2, 1, 1e-2], [3, 2, 1e-6], [4, 3, 1e-1]],
                                                             {1: 1e-2, 2: 1e-6, 3: 1e-1}),
                            [[[1, 0, 1e5], [3, 0, 1e-3]]], [1.0, 0, 0, 0, 0],
                            [[300, 1.0, 0], [1e4, 0.0, 0]], 1e-2, tags=["trace", "cooling"]))
    # Coupled mode with a produced source.
    cases.append(chain_case("produced_source", 4, decay_with_diag([[3, 2, 1e-4]], {2: 1e-4}),
                            [with_diag([[1, 0, 1e-8], [2, 1, 1e-8]])], [1e16, 1e14, 0, 0],
                            [[1e6, 1.0, 0]], 1e4, tags=["produced_source"]))
    # Feed and removal schedules (sink is the last state).
    cases.append(chain_case("feed_and_removal", 5, decay_with_diag([[2, 1, 1e-3], [3, 2, 1e-5]], {1: 1e-3, 2: 1e-5}),
                            [with_diag([[2, 1, 1e-9]])], [1.0, 0, 0, 0, 0],
                            [[1e3, 1.0, 0], [1e3, 0.5, 0], [1e4, 0.0, 0]], 1e-1,
                            feed=[[[1, 0, 1e6]], [[1, 0, 2e6]], []],
                            removal=[[], [[4, 2, 1e-2], [2, 2, -1e-2]], [[4, 3, 1e-3], [3, 3, -1e-3]]],
                            tags=["feed", "removal", "cooling"]))
    # Two spectra.
    cases.append(chain_case("two_spectra", 4, decay_with_diag([[3, 1, 1e-3], [3, 2, 1e-3]], {1: 1e-3, 2: 1e-3}),
                            [with_diag([[1, 0, 1e-9]]), with_diag([[2, 0, 1e-9], [1, 0, 1e-11]])],
                            [1e12, 0, 0, 0], [[1e4, 1.0, 0], [1e4, 2.0, 1], [1e4, 0.0, 0]], 5.0,
                            tags=["spectra", "cooling"]))
    # No contraction: a unit-share decay cycle.
    cases.append(chain_case("no_contraction", 4, [[2, 1, 1.0], [1, 1, -1.0], [1, 2, 1.0], [2, 2, -1.0], [3, 2, 1e-30]],
                            [with_diag([[1, 0, 1e-20]])], [1.0, 0, 0, 0], [[1e3, 1.0, 0]], 1e10,
                            tags=["unbounded"]))
    # Seeded random networks.
    rng = random.Random(148)
    while len(cases) < 40:
        k = len(cases)
        n = rng.randint(6, 24)
        lams = {j: 10 ** rng.uniform(-8, 0) for j in range(1, n) if rng.random() < 0.8}
        dec = []
        for j in lams:
            targets = rng.sample(range(1, n), k=min(n - 1, rng.choice([1, 1, 2])))
            shares = [rng.random() for _ in targets]
            total = sum(shares)
            dec += [[t, j, lams[j] * w / total] for t, w in zip(targets, shares) if t != j]
        dec = decay_with_diag(dec, lams)
        reac = []
        for j in range(n):
            for _ in range(rng.choice([0, 1, 2])):
                t = rng.randrange(1, n)
                if t != j:
                    reac.append([t, j, 10 ** rng.uniform(-14, -6)])
        trace = rng.random() < 0.3
        if trace:
            spectra = [[[t, j, v * 1e10 if j == 0 else v] for t, j, v in reac if t != 0]]
            spectra[0] += [[j, j, -sum(v for t, jj, v in reac if jj == j and t != 0)] for j in range(1, n)]
            n0 = [1.0] + [0.0] * (n - 1)
        else:
            spectra = [with_diag([e for e in reac if e[0] != 0])]
            n0 = [10 ** rng.uniform(10, 20)] + [0.0] * (n - 1)
        sched = [[10 ** rng.uniform(1, 6), rng.choice([1.0, 1.0, 0.5, 2.0]), 0]]
        sched += [[10 ** rng.uniform(1, 7), 0.0, 0] for _ in range(rng.randint(0, 2))]
        cases.append(chain_case(f"random_{k}", n, dec, spectra, n0, sched, 10 ** rng.uniform(-6, 6),
                                tags=["random", "trace" if trace else "coupled"]))
    return cases


def step_matrix(case, k):
    n = case["n"]
    A = np.zeros((n, n))
    _, mult, s = case["schedule"][k]
    for i, j, v in case["decay"]:
        A[i, j] += v
    for i, j, v in case["spectra"][int(s)]:
        A[i, j] += v * mult
    for i, j, v in case["feed"][k] + case["removal"][k]:
        A[i, j] += v
    return A


def dense_history(case, keep=None):
    """Populations at SAMPLES_PER_STEP points per step (and t=0); unkept states removed."""
    n = case["n"]
    idx = list(range(n)) if keep is None else list(keep)
    x = np.array([float(case["n0"][i]) if i < len(case["n0"]) else 0.0 for i in idx])
    out = [np.zeros(n)]
    out[0][idx] = x
    for k, (dt, _, _) in enumerate(case["schedule"]):
        A = step_matrix(case, k)[np.ix_(idx, idx)]
        step = expm(A * (dt / SAMPLES_PER_STEP))
        for _ in range(SAMPLES_PER_STEP):
            x = step @ x
            full = np.zeros(n)
            full[idx] = x
            out.append(full)
    return np.array(out)


def dominance(case, cap, mine) -> list[str]:
    problems = []
    lam = mine["lam"]
    energy = case["energy_ev"]
    full = dense_history(case)
    pruned = dense_history(case, mine["keep"])
    scale = max(1.0, float(np.max(np.abs(full))))
    slack = 1e-9 * scale
    for i, p, _ in mine["dropped"]:
        peak = float(np.max(full[:, i]))
        if peak > p + slack:
            problems.append(f"dropped {i}: peak {peak:.6e} > bound {p:.6e}")
    for i in mine["keep"]:
        gap = float(np.max(full[:, i] - pruned[:, i]))
        bound = mine["shortfall"].get(i, 0.0)
        if gap > bound + slack:
            problems.append(f"kept {i}: shortfall {gap:.6e} > bound {bound:.6e}")
    rb = response_bounds({"energy_ev": energy, "n": case["n"]}, mine)
    lam_v, e_v = np.array(lam), np.array(energy) * EV
    da = np.max(np.abs(full @ lam_v - pruned @ lam_v))
    dh = np.max(np.abs(full @ (lam_v * e_v) - pruned @ (lam_v * e_v)))
    if da > rb["activity_Bq_per_g_bound"] + 1e-9 * max(1.0, float(np.max(full @ lam_v))):
        problems.append(f"activity change {da:.6e} > bound {rb['activity_Bq_per_g_bound']:.6e}")
    if dh > rb["heat_W_per_g_bound"] + 1e-9 * max(1e-300, float(np.max(full @ (lam_v * e_v)))):
        problems.append(f"heat change {dh:.6e} > bound {rb['heat_W_per_g_bound']:.6e}")
    return problems


def run_synthetic(probe: str, out: Path) -> dict:
    cases = battery()
    with tempfile.TemporaryDirectory() as tmp:
        bat = Path(tmp) / "battery.json"
        res = Path(tmp) / "probe.json"
        bat.write_text(json.dumps({"cases": [{k: v for k, v in c.items() if k not in ("tags", "energy_ev")} |
                                             {"schedule": [[s[0], s[1]] for s in c["schedule"]]} for c in cases]}))
        subprocess.run([probe, "--synthetic", str(bat), str(res)], check=True)
        probe_out = json.loads(res.read_text())
    rows, coverage = [], defaultdict(int)
    for case, rec in zip(cases, probe_out["records"]):
        cap = rec["capture"]
        mine = recompute(cap)
        r1 = compare(cap, mine)
        r2 = dominance(case, cap, mine)
        for t in case["tags"]:
            coverage[t] += 1
        if mine.get("unbounded"):
            coverage["unbounded_found"] += 1
        if any(len(c) > 1 for c in kosaraju(case["n"], mine["edges"], [True] * case["n"])[0]):
            coverage["cyclic_component"] += 1
        if mine["shortfall"]:
            coverage["kept_shortfall"] += 1
        rows.append({"name": case["name"], "tags": case["tags"], "states": case["n"], "kept": len(mine["keep"]),
                     "dropped": len(mine["dropped"]), "r1_problems": r1, "r2_problems": r2})
    required = ["converging", "r4", "linear", "cycle", "isomer", "multiplicity", "trace", "produced_source",
                "feed", "removal", "spectra", "cooling", "unbounded", "unbounded_found", "cyclic_component",
                "kept_shortfall"]
    record = {
        "schema": "actinv-p148-synthetic-1",
        "cases": len(rows),
        "coverage": dict(sorted(coverage.items())),
        "missing_coverage": [t for t in required if coverage.get(t, 0) == 0],
        "rows": rows,
        "r1_pass": all(not r["r1_problems"] for r in rows),
        "r2_pass": all(not r["r2_problems"] for r in rows),
    }
    record["pass"] = record["r1_pass"] and record["r2_pass"] and not record["missing_coverage"] and len(rows) >= 30
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=1) + "\n")
    return record


def check_capture_file(path: Path) -> dict:
    data = json.loads(path.read_text())
    problems = []
    for k, cap in enumerate(data["captures"]):
        mine = recompute(cap)
        problems += [f"capture {k}: {p}" for p in compare(cap, mine)]
        if cap["rate_mode"] and k == len(data["captures"]) - 1:
            ledger = data["result"]["ledger"]["rate_pruning"]
            for key, value in response_bounds(cap, mine).items():
                theirs = ledger.get(key)
                if theirs is None or not close(float(theirs), value):
                    problems.append(f"ledger {key} {theirs!r} vs {value!r}")
    return {"path": str(path), "problems": problems, "pass": not problems}


def selftest(probe: str) -> int:
    cases = battery()[:12]
    with tempfile.TemporaryDirectory() as tmp:
        bat = Path(tmp) / "battery.json"
        res = Path(tmp) / "probe.json"
        bat.write_text(json.dumps({"cases": [{k: v for k, v in c.items() if k not in ("tags", "energy_ev")} |
                                             {"schedule": [[s[0], s[1]] for s in c["schedule"]]} for c in cases]}))
        subprocess.run([probe, "--synthetic", str(bat), str(res)], check=True)
        records = json.loads(res.read_text())["records"]
    failures = []
    clean = [compare(r["capture"], recompute(r["capture"])) for r in records]
    if any(clean):
        failures.append(f"clean records reported problems: {clean}")
    target = next(r for r in records if r["capture"]["dropped"])
    plants = {
        "population lowered": lambda c: c["dropped"][0].__setitem__(1, num(c["dropped"][0][1]) * 0.5),
        "drop removed": lambda c: c["dropped"].pop(),
        "edge share changed": lambda c: c["edges"][0].__setitem__(3, 0.123),
        "keep extended": lambda c: c["keep"].append(c["n"] - 1) if c["n"] - 1 not in c["keep"] else c["keep"].remove(c["n"] - 1),
    }
    for name, plant in plants.items():
        cap = copy.deepcopy(target["capture"])
        plant(cap)
        if not compare(cap, recompute(cap)):
            failures.append(f"plant not caught: {name}")
    # A case solved with the v1.0.0 maximum must fail R2 on the converging pair.
    r4 = next(c for c in battery() if c["name"] == "r4_converging_pair")
    legacy = recompute({**json.loads(json.dumps({"n": r4["n"], "decay": r4["decay"], "feed": r4["feed"],
                                                 "removal": r4["removal"], "spectra": r4["spectra"], "n0": r4["n0"],
                                                 "durations_s": [s[0] for s in r4["schedule"]],
                                                 "multipliers": [s[1] for s in r4["schedule"]],
                                                 "rate_mode": True, "bmin": r4["bmin"]}))})
    legacy["keep"] = [k for k in legacy["keep"] if k != 3]
    legacy["dropped"] = legacy["dropped"] + [(3, 100.0, 0.0)]
    if not dominance(r4, None, legacy):
        failures.append("R2 did not catch the v1.0.0 drop of the converging daughter")
    print(json.dumps({"selftest": "pass" if not failures else "fail", "failures": failures}, indent=1))
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    s = sub.add_parser("synthetic")
    s.add_argument("probe")
    s.add_argument("out", type=Path)
    c = sub.add_parser("capture")
    c.add_argument("files", nargs="+", type=Path)
    t = sub.add_parser("selftest")
    t.add_argument("probe")
    args = ap.parse_args()
    if args.command == "synthetic":
        rec = run_synthetic(args.probe, args.out)
        print(json.dumps({k: rec[k] for k in ("cases", "coverage", "missing_coverage", "r1_pass", "r2_pass", "pass")}, indent=1))
        for r in rec["rows"]:
            if r["r1_problems"] or r["r2_problems"]:
                print(r["name"], r["r1_problems"][:3], r["r2_problems"][:3])
        return 0 if rec["pass"] else 1
    if args.command == "capture":
        results = [check_capture_file(p) for p in args.files]
        for r in results:
            if not r["pass"]:
                print(r["path"], r["problems"][:5])
        print(json.dumps({"files": len(results), "pass": all(r["pass"] for r in results)}))
        return 0 if all(r["pass"] for r in results) else 1
    return selftest(args.probe)


if __name__ == "__main__":
    sys.exit(main())
