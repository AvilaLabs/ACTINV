#!/usr/bin/env python3
"""P169 checker (protocols/ACTINV-P169_PROTOCOL.md): does different decay data close ACTINV's short-time fission-product
decay-heat bias? Reads results/p169_train.json and results/p169_test.json written by controls/p169_decay_swap.py.
G0-G4 are evaluated here; G5 (CI on the pushed evidence commit) is external.

  G0  The protocol's SHA-256 (and any protocols/ACTINV-P169_AMENDMENT_*.md) is in protocols/protocol_hash.txt.
  G1  Inputs and environment in both records: the P166 pins (decay, yields, archive, table CSV), the JEFF-3.3 decay file,
      the candidate-D override document, the Dickens and Yarnell files and P166's record; the pinned binary; the crates
      tree of d6f516b; a clean tracked tree; the bounded cgroup; the test record names the train record it was
      selected from.
  G2  Model fidelity and decay configuration: P166's per-run checks on every candidate; each candidate's certificate
      names exactly its registered decay files (A: VIII.0 only; B: JEFF-3.3 primary with the VIII.0 fallback; D:
      VIII.0 with the ten-nuclide override document, all ten applied); candidate A reproduces P166's recorded C/E on
      every train point to 1e-9 and P9's recorded geometric-mean total C/E on the held-out sets to 1e-4.
  G3  Coverage: every P166 CSV row scored exactly once per candidate with a finite positive C/E and positive sigma;
      the held-out counts (Dickens 32 x 3 channels, Yarnell 79 total); the test record holds A and the selected
      candidate only.
  G4  The selection is recomputed from the train points and equals the recorded one; the summary and allowed
      sentences are recomputed and written to results/p169_verdict.json (with --no-write, compared with it).

Verdict: P169-PASS if G0-G4 hold (G5 external). Whether the swap helps is reported, not gated.

Usage: check_p169.py [--train FILE] [--test FILE] [--no-write]
       check_p169.py --selftest
"""
import argparse
import copy
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "controls"))
import check_p166 as c166  # noqa: E402
import p169_decay_swap as h  # noqa: E402

PROTOCOL = ROOT / "protocols" / "ACTINV-P169_PROTOCOL.md"
HASH_LOG = ROOT / "protocols" / "protocol_hash.txt"
TRAIN = h.TRAIN_RECORD
TEST = h.TEST_RECORD
VERDICT = ROOT / "results" / "p169_verdict.json"
P166_RECORD = h.P166_RECORD

REPRODUCE_TRAIN = 1e-9
DESCRIPTION = {
    "A": "ENDF/B-VIII.0 decay data",
    "B": "JEFF-3.3 decay data (ENDF/B-VIII.0 for nuclides JEFF-3.3 lacks)",
    "D": "ENDF/B-VIII.0 decay data with JEFF-3.3 mean energies for ten short-lived nuclides",
}
INPUT_PINS = {**c166.PINNED, **h.PINNED}


def g0():
    if not PROTOCOL.is_file():
        return [f"missing {PROTOCOL.name}"]
    logged = HASH_LOG.read_text()
    problems = []
    for path in [PROTOCOL, *sorted(PROTOCOL.parent.glob("ACTINV-P169_AMENDMENT_*.md"))]:
        if f"{c166.sha256(path)}  protocols/{path.name}" not in logged:
            problems.append(f"{path.name} hash not in protocol_hash.txt")
    return problems


GUI_CRATE = "crates/actinv-gui/"


def changed_crate_paths(head):
    """Paths under crates/ that differ between the P166 source commit and `head`; None if git cannot tell."""
    try:
        return c166.git("diff", "--name-only", c166.SOURCE_COMMIT, head, "--", "crates").split()
    except Exception:
        return None


def g1_record(record, label, source_tree):
    problems = []
    for name, digest in INPUT_PINS.items():
        if record["inputs"].get(name, {}).get("sha256") != digest:
            problems.append(f"{label}: input {name} hash is not the pin")
    if "tables" in record and record["tables"]["sha256"] != c166.TABLES_SHA256:
        problems.append(f"{label}: table CSV hash is not the pin")
    environment = record["environment"]
    if environment.get("binary_sha256") != c166.BINARY_SHA256:
        problems.append(f"{label}: binary is not the pinned P163 release binary")
    if environment.get("crates_tree") != source_tree:
        # Amendment A: the CLI binary does not link actinv-gui, so a tree that differs only there is accepted.
        changed = changed_crate_paths(environment.get("git_head", ""))
        if changed is None or not changed or any(not path.startswith(GUI_CRATE) for path in changed):
            problems.append(f"{label}: crates tree differs from {c166.SOURCE_COMMIT}'s outside {GUI_CRATE}")
    if not environment.get("clean_tree"):
        problems.append(f"{label}: tracked tree was not clean")
    if not c166.cgroup_ok(environment.get("cgroup", {})):
        problems.append(f"{label}: harness cgroup is not the bounded scope")
    return problems


def g1(train, test, *, source_tree=None, train_sha=None):
    source_tree = source_tree if source_tree is not None else c166.git("rev-parse", f"{c166.SOURCE_COMMIT}:crates")
    problems = []
    if train.get("schema") != h.SCHEMA_TRAIN or train.get("smoke"):
        problems.append("train record: wrong schema or a smoke run")
    if test.get("schema") != h.SCHEMA_TEST:
        problems.append("test record: wrong schema")
    problems += g1_record(train, "train", source_tree) + g1_record(test, "test", source_tree)
    if test["train_record"]["sha256"] != train_sha:
        problems.append("test record was not selected from this train record")
    return problems


def expected_decay(candidate):
    """(primary sha, fallback sha or None, overrides sha or None) registered for the candidate."""
    endf, jeff = c166.PINNED["decay"], h.PINNED["jeff_decay"]
    return {"A": (endf, None, None), "B": (jeff, endf, None), "D": (endf, None, h.PINNED["overrides_D"])}[candidate]


def decay_problems(candidate, datasets):
    problems = []
    want = expected_decay(candidate)
    for name, dataset in datasets.items():
        inputs = dataset["model"]["certificate_inputs"]
        got = (inputs.get("decay_primary", {}).get("sha256"),
               (inputs.get("decay_fallback") or {}).get("sha256"),
               (inputs.get("decay_overrides") or {}).get("sha256"))
        if got != want:
            problems.append(f"{candidate} {name}: decay inputs {got} are not the registered {want}")
        ledger = dataset["model"].get("decay_ledger", {})
        applied = ledger.get("decay_overrides_applied") or []
        if len(applied) != (len(h.OVERRIDE_NUCLIDES) if candidate == "D" else 0):
            problems.append(f"{candidate} {name}: {len(applied)} overrides applied")
        if candidate != "B" and ledger.get("decay_nuclides_from_fallback"):
            problems.append(f"{candidate} {name}: nuclides taken from a fallback")
        if candidate == "B" and ledger.get("decay_nuclides_from_fallback") is None:
            problems.append(f"B {name}: fallback count missing from the ledger")
    return problems


def key(dataset, point):
    return (dataset, point["cooling_time_s"], point["channel"], point["run"])


def g2(train, test, p166):
    problems = []
    for label, record in (("train", train), ("test", test)):
        for candidate, body in record["candidates"].items():
            problems += [f"{label} {candidate}: {p}" for p in c166.g2({"datasets": body["datasets"]})]
            problems += [f"{label} {p}" for p in decay_problems(candidate, body["datasets"])]
    recorded = {key(name, point): point["C_over_E"] for name, dataset in p166["datasets"].items()
                for point in dataset["points"]}
    reproduced = {key(name, point): point["C_over_E"] for name, dataset in train["candidates"]["A"]["datasets"].items()
                  for point in dataset["points"]}
    if set(recorded) != set(reproduced):
        problems.append("train A: points are not P166's points")
    else:
        worst = max(abs(reproduced[k] / recorded[k] - 1.0) for k in recorded)
        if worst > REPRODUCE_TRAIN:
            problems.append(f"train A: C/E differs from P166's record by {worst:.3e}")
    for name, label in (("P9-Dickens-pulse", "Dickens"), ("P9-Yarnell-20000s", "Yarnell")):
        logs = [math.log(p["C_over_E"]) for p in test["candidates"]["A"]["datasets"][name]["points"]
                if p["channel"] == "total"]
        geomean = math.exp(math.fsum(logs) / len(logs)) if logs else float("nan")
        if not abs(geomean / h.P9_GEOMEAN_TOTAL[label] - 1.0) <= h.P9_REPRODUCTION_TOLERANCE:
            problems.append(f"test A {label}: geometric-mean total C/E {geomean} is not P9's "
                            f"{h.P9_GEOMEAN_TOTAL[label]}")
    return problems


def g3(train, test, csv_rows):
    problems = []
    if set(train["candidates"]) != set(h.CANDIDATES):
        problems.append(f"train candidates {sorted(train['candidates'])} are not {list(h.CANDIDATES)}")
    for candidate, body in train["candidates"].items():
        problems += [f"train {candidate}: {p}" for p in c166.g3({"datasets": body["datasets"]}, csv_rows)]
    wanted = ["A"] if test["selected"] == "A" else ["A", test["selected"]]
    if sorted(test["candidates"]) != sorted(wanted):
        problems.append(f"test candidates {sorted(test['candidates'])} are not {wanted}")
    for candidate, body in test["candidates"].items():
        if set(body["datasets"]) != set(h.TEST_COUNTS):
            problems.append(f"test {candidate}: dataset set is not the registered one")
            continue
        for name, dataset in body["datasets"].items():
            counts = {}
            for point in dataset["points"]:
                counts[point["channel"]] = counts.get(point["channel"], 0) + 1
                ce = point["C_over_E"]
                if not (isinstance(ce, float) and math.isfinite(ce) and ce > 0 and point["sigma"] > 0):
                    problems.append(f"test {candidate} {name} t={point['cooling_time_s']}: C/E {ce}")
            if counts != h.TEST_COUNTS[name]:
                problems.append(f"test {candidate} {name}: counts {counts} are not {h.TEST_COUNTS[name]}")
    return problems


def split(points):
    return ([p for p in points if p["cooling_time_s"] < h.SHORT_S], [p for p in points if p["cooling_time_s"] >= h.SHORT_S])


def train_total(candidate_record):
    return [p for dataset in candidate_record["datasets"].values() for p in dataset["points"]
            if p["channel"] == "total"]


def origen_view(points):
    return [dict(p, ORIGEN_C_over_E=p["origen_calculated"] / p["measured"]) for p in points if p["origen_calculated"]]


def summary(train, test):
    selection = h.select(train)
    result = {"selection": selection, "train": {}, "test": {}}
    for candidate, body in train["candidates"].items():
        short, long = split(train_total(body))
        result["train"][candidate] = {"short": c166.stats(short, "C_over_E"), "long": c166.stats(long, "C_over_E")}
    short, long = split(origen_view(train_total(train["candidates"]["A"])))
    result["train"]["ORIGEN"] = {"short": c166.stats(short, "ORIGEN_C_over_E"),
                                 "long": c166.stats(long, "ORIGEN_C_over_E")}
    for candidate, body in test["candidates"].items():
        total = [p for d in body["datasets"].values() for p in d["points"] if p["channel"] == "total"]
        short, long = split(total)
        entry = {"all": c166.stats(total, "C_over_E"), "short": c166.stats(short, "C_over_E"),
                 "long": c166.stats(long, "C_over_E"),
                 "by_dataset": {name: {channel: c166.stats([p for p in d["points"] if p["channel"] == channel],
                                                           "C_over_E")
                                       for channel in sorted({p["channel"] for p in d["points"]})}
                                for name, d in body["datasets"].items()}}
        result["test"][candidate] = entry
    chosen = test["selected"]
    if chosen != "A":
        base = {key(n, p): abs(math.log(p["C_over_E"])) for n, d in test["candidates"]["A"]["datasets"].items()
                for p in d["points"] if p["channel"] == "total"}
        other = {key(n, p): abs(math.log(p["C_over_E"])) for n, d in test["candidates"][chosen]["datasets"].items()
                 for p in d["points"] if p["channel"] == "total"}
        result["test_paired"] = {"points": len(base), "selected_closer_fraction":
                                 sum(other[k] < base[k] for k in base) / len(base)}
    return result


def sentences(result, test):
    chosen = result["selection"]["selected"]
    train, windows = result["train"], result["selection"]["windows"]
    out = []
    for candidate in h.CANDIDATES:
        w = windows[candidate]
        out.append(f"On the {w['short_points']} P166 train points with cooling time below {h.SHORT_S:.0f} s, "
                   f"{DESCRIPTION[candidate]} gives a median |ln C/E| of {w['short_median_abs_log']:.3f}; at "
                   f"{h.SHORT_S:.0f} s and above ({w['long_points']} points) it is {w['long_median_abs_log']:.3f}.")
    out.append(f"By the registered rule the selected decay data is {DESCRIPTION[chosen]}.")
    held, base = result["test"][chosen], result["test"]["A"]
    stat = lambda view, window, field: view[window][field]
    median, geomean = "median_abs_log_C_over_E", "geometric_mean_C_over_E"
    if chosen == "A":
        out.append(f"No candidate met the rule, so ENDF/B-VIII.0 decay data was kept; on the held-out Dickens and "
                   f"Yarnell U-235 total decay heat ({base['all']['points']} points) its median |ln C/E| is "
                   f"{stat(base, 'all', median):.3f} and its geometric-mean C/E is {stat(base, 'all', geomean):.3f}.")
    else:
        out.append(
            f"On the held-out Dickens and Yarnell U-235 total decay heat ({held['all']['points']} points), "
            f"{DESCRIPTION[chosen]} gives a median |ln C/E| of {stat(held, 'all', median):.3f} and a geometric-mean "
            f"C/E of {stat(held, 'all', geomean):.3f}, against {stat(base, 'all', median):.3f} and "
            f"{stat(base, 'all', geomean):.3f} for ENDF/B-VIII.0 decay data. Below {h.SHORT_S:.0f} s "
            f"({held['short']['points']} points) the medians are {stat(held, 'short', median):.3f} and "
            f"{stat(base, 'short', median):.3f}.")
    o = train["ORIGEN"]
    out.append(f"On the same train points, ORIGEN as printed by Gauld (2019) has a median |ln C/E| of "
               f"{o['short'][median]:.3f} below {h.SHORT_S:.0f} s and {o['long'][median]:.3f} at {h.SHORT_S:.0f} s "
               f"and above.")
    return out


def read_csv():
    return c166.read_csv()


def evaluate(train, test, csv_rows, p166, *, source_tree=None, train_sha=None):
    gates = {"G0": g0(), "G1": g1(train, test, source_tree=source_tree, train_sha=train_sha),
             "G2": g2(train, test, p166), "G3": g3(train, test, csv_rows)}
    result, lines = ({}, [])
    if not gates["G3"] and not gates["G2"]:
        expected = h.select(train)
        recorded = train.get("selection")
        gates["G4"] = []
        if recorded != json.loads(json.dumps(expected)):
            gates["G4"].append("train record's selection differs from the recomputed one")
        if test["selected"] != expected["selected"]:
            gates["G4"].append("test record's selected candidate is not the recomputed selection")
        if not gates["G4"]:
            result = summary(train, test)
            lines = sentences(result, test)
    else:
        gates["G4"] = ["summary not computed (coverage or model fidelity failed)"]
    return gates, result, lines


def synthetic():
    """Passing synthetic records: A is 7% low below 1000 s, B 3% low, D 4% low but 3% high above 1000 s."""
    short_ce = {"A": 0.93, "B": 0.97, "D": 0.96}
    long_ce = {"A": 1.0, "B": 1.0, "D": 1.03}
    csv_rows, p166_datasets = [], {}
    cgroup = {"memory.max": str(6 * c166.GIB), "memory.swap.max": "0", "pids.max": "128", "cpu.max": "200000 100000"}

    def model(parent, candidate):
        primary, fallback, overrides = expected_decay(candidate)
        return {"mode": "trace", "relative_rate_closure": 0.0, "max_relative_time_error": 0.0,
                "max_background_fraction": 1e-12,
                "fission_yields": {"energy": "fixed", "selection": [{
                    "parent_ZA": c166.PARENT_ZA[parent], "clamped": False, "requested_energy_eV": c166.THERMAL_EV,
                    "lower_energy_eV": c166.THERMAL_EV, "upper_weight": 0.0, "effective_yield_sum": 1.9999998}]},
                "certificate_inputs": {"decay_primary": {"sha256": primary},
                                       "decay_fallback": {"sha256": fallback} if fallback else None,
                                       "decay_overrides": {"sha256": overrides} if overrides else None},
                "decay_ledger": {"decay_nuclides_from_fallback": 7 if candidate == "B" else 0,
                                 "decay_overrides_applied": ["x"] * len(h.OVERRIDE_NUCLIDES) if candidate == "D"
                                 else [], "max_leakage_fraction": 0.0}}

    def environment():
        return {"binary_sha256": c166.BINARY_SHA256, "crates_tree": "tree", "clean_tree": True, "cgroup": dict(cgroup)}

    pins = {name: {"sha256": digest} for name, digest in INPUT_PINS.items()}
    train = {"schema": h.SCHEMA_TRAIN, "smoke": False, "environment": environment(), "inputs": copy.deepcopy(pins),
             "tables": {"sha256": c166.TABLES_SHA256}, "candidates": {}}
    for candidate in h.CANDIDATES:
        datasets = {}
        for name, channels in c166.COUNTS.items():
            parent = name.split("-")[1]
            points = []
            for channel, count in channels.items():
                for index in range(count):
                    time = 25.0 * (index + 1) ** 1.5
                    if candidate == "A":
                        csv_rows.append({"dataset": name, "cooling_time_s": repr(time), "channel": channel, "run": ""})
                    ce = short_ce[candidate] if time < h.SHORT_S else long_ce[candidate]
                    points.append({"cooling_time_s": time, "channel": channel, "run": "", "measured": 2.0,
                                   "sigma": 0.15, "calculated": 2.0 * ce, "C_over_E": ce,
                                   "log_C_over_E": math.log(ce), "residual_over_sigma": 0.0,
                                   "origen_calculated": 1.9, "origen_c_over_m": 0.95})
            datasets[name] = {"parent": parent, "laboratory": "lab", "points": points, "model": model(parent, candidate)}
            if candidate == "A":
                p166_datasets[name] = {"points": copy.deepcopy(points)}
        train["candidates"][candidate] = {"datasets": datasets}
    train["selection"] = json.loads(json.dumps(h.select(train)))
    test = {"schema": h.SCHEMA_TEST, "environment": environment(), "inputs": copy.deepcopy(pins),
            "train_record": {"sha256": "trainsha"}, "selected": train["selection"]["selected"], "candidates": {}}
    for candidate in ("A", test["selected"]):
        datasets = {}
        for name, counts in h.TEST_COUNTS.items():
            label = "Dickens" if "Dickens" in name else "Yarnell"
            points = []
            for channel, count in counts.items():
                for index in range(count):
                    time = 3.0 * (index + 1) ** 2
                    ce = (h.P9_GEOMEAN_TOTAL[label] if candidate == "A" else 1.0) if channel == "total" else 1.0
                    points.append({"cooling_time_s": time, "channel": channel, "run": "", "measured": 1.0,
                                   "sigma": 0.1, "calculated": ce, "C_over_E": ce, "log_C_over_E": math.log(ce),
                                   "residual_over_sigma": 0.0})
            datasets[name] = {"parent": "U235", "laboratory": label, "points": points,
                              "model": model("U235", candidate)}
        test["candidates"][candidate] = {"datasets": datasets}
    return train, test, csv_rows, {"datasets": p166_datasets}


def selftest():
    """Synthetic records: one passing, then one defect per gate, plus the selection rule's edge cases."""
    train, test, csv_rows, p166 = synthetic()
    failures = []

    def run(label, mutate, gate):
        trial_train, trial_test = copy.deepcopy(train), copy.deepcopy(test)
        rows, reference = copy.deepcopy(csv_rows), copy.deepcopy(p166)
        mutate(trial_train, trial_test, rows, reference)
        gates, result, lines = evaluate(trial_train, trial_test, rows, reference, source_tree="tree",
                                        train_sha="trainsha")
        bad = [name for name in ("G1", "G2", "G3", "G4") if gates[name]]
        if gate is None and bad:
            failures.append(f"{label}: expected pass, failed {bad}: {[gates[b] for b in bad]}")
        if gate is not None and gate not in bad:
            failures.append(f"{label}: expected {gate} to fail, got {bad}")
        return result, lines

    result, lines = run("clean", lambda a, b, c, d: None, None)
    if result and result["selection"]["selected"] != "B":
        failures.append(f"clean: selected {result['selection']['selected']}, expected B")
    if result and abs(result["test"]["B"]["all"]["geometric_mean_C_over_E"] - 1.0) > 1e-12:
        failures.append("clean: held-out B geometric mean wrong")
    if result and abs(result["train"]["A"]["short"]["geometric_mean_C_over_E"] - 0.93) > 1e-12:
        failures.append("clean: train A short geometric mean wrong")
    if result and len(lines) != 6:
        failures.append(f"clean: {len(lines)} sentences")

    run("binary", lambda a, b, c, d: b["environment"].update(binary_sha256="0" * 64), "G1")
    run("jeff hash", lambda a, b, c, d: a["inputs"]["jeff_decay"].update(sha256="0" * 64), "G1")
    run("overrides hash", lambda a, b, c, d: b["inputs"]["overrides_D"].update(sha256="0" * 64), "G1")
    run("p9 file hash", lambda a, b, c, d: b["inputs"]["yarnell"].update(sha256="0" * 64), "G1")
    run("cgroup", lambda a, b, c, d: a["environment"]["cgroup"].update({"memory.max": "max"}), "G1")
    run("dirty", lambda a, b, c, d: a["environment"].update(clean_tree=False), "G1")
    global changed_crate_paths
    real_changed = changed_crate_paths
    try:
        changed_crate_paths = lambda head: ["crates/actinv-gui/src/web_app.rs"]
        run("tree differs in gui only", lambda a, b, c, d: [r["environment"].update(crates_tree="other")
                                                             for r in (a, b)], None)
        changed_crate_paths = lambda head: ["crates/actinv-gui/src/web_app.rs", "crates/actinv-core/src/run.rs"]
        run("tree differs in core", lambda a, b, c, d: a["environment"].update(crates_tree="other"), "G1")
        changed_crate_paths = lambda head: []
        run("tree differs, git silent", lambda a, b, c, d: b["environment"].update(crates_tree="other"), "G1")
    finally:
        changed_crate_paths = real_changed
    run("wrong train record", lambda a, b, c, d: b["train_record"].update(sha256="other"), "G1")
    run("smoke train", lambda a, b, c, d: a.update(smoke=True), "G1")
    run("B primary", lambda a, b, c, d: a["candidates"]["B"]["datasets"]["SC78-U235-1h"]["model"][
        "certificate_inputs"]["decay_primary"].update(sha256="0" * 64), "G2")
    run("B no fallback", lambda a, b, c, d: a["candidates"]["B"]["datasets"]["SC78-U235-1h"]["model"][
        "certificate_inputs"].update(decay_fallback=None), "G2")
    run("D overrides short", lambda a, b, c, d: a["candidates"]["D"]["datasets"]["SC78-U235-1h"]["model"][
        "decay_ledger"].update(decay_overrides_applied=["x"]), "G2")
    run("A has overrides", lambda a, b, c, d: a["candidates"]["A"]["datasets"]["SC78-U235-1h"]["model"][
        "decay_ledger"].update(decay_overrides_applied=["x"]), "G2")
    run("energy", lambda a, b, c, d: a["candidates"]["D"]["datasets"]["FI76-U233-pulse"]["model"][
        "fission_yields"]["selection"][0].update(requested_energy_eV=0.5), "G2")
    run("A drifts from P166", lambda a, b, c, d: d["datasets"]["SC78-U235-4h"]["points"][0].update(C_over_E=0.931), "G2")
    run("A drifts from P9", lambda a, b, c, d: [p.update(C_over_E=p["C_over_E"] * 1.01) for p in
                                                 b["candidates"]["A"]["datasets"]["P9-Yarnell-20000s"]["points"]], "G2")
    run("dropped train point", lambda a, b, c, d: a["candidates"]["B"]["datasets"]["SC78-U235-4h"]["points"].pop(), "G3")
    run("bad C/E", lambda a, b, c, d: a["candidates"]["D"]["datasets"]["SC78-U235-4h"]["points"][0].update(
        C_over_E=float("nan")), "G3")
    run("dropped held-out point", lambda a, b, c, d: b["candidates"]["A"]["datasets"]["P9-Dickens-pulse"][
        "points"].pop(), "G3")
    run("extra test candidate", lambda a, b, c, d: b["candidates"].update(
        D=copy.deepcopy(b["candidates"]["B"])), "G3")
    run("recorded selection wrong", lambda a, b, c, d: a["selection"].update(selected="D"), "G4")
    run("test selected wrong", lambda a, b, c, d: b.update(selected="D"), "G3")

    def make_a_best(a, b, c, d):
        for point in (p for ds in a["candidates"]["B"]["datasets"].values() for p in ds["points"]):
            point.update(C_over_E=0.931 if point["cooling_time_s"] < h.SHORT_S else 1.0)
        a["selection"] = json.loads(json.dumps(h.select(a)))
        b["selected"] = "A"
        b["candidates"] = {"A": b["candidates"]["A"]}
    result, lines = run("gain below minimum keeps A", make_a_best, None)
    if result and (result["selection"]["selected"] != "A" or "No candidate met the rule" not in " ".join(lines)):
        failures.append("gain below the minimum did not keep A")

    def margin_violation(a, b, c, d):
        for point in (p for ds in a["candidates"]["B"]["datasets"].values() for p in ds["points"]):
            if point["cooling_time_s"] >= h.SHORT_S:
                point.update(C_over_E=1.02)
        a["selection"] = json.loads(json.dumps(h.select(a)))
        b["selected"] = a["selection"]["selected"]
        b["candidates"] = {"A": b["candidates"]["A"]}
    result, _ = run("margin violation keeps A", margin_violation, None)
    if result and result["selection"]["selected"] != "A":
        failures.append(f"long-time margin violation selected {result['selection']['selected']}")
    # the rule on the exact edge: gain equal to MIN_GAIN is enough, a long-time worsening of LONG_MARGIN is allowed
    edge = copy.deepcopy(train)
    for candidate, short_log, long_log in (("A", 0.07, 0.0), ("B", 0.07 - h.MIN_GAIN, h.LONG_MARGIN)):
        for dataset in edge["candidates"][candidate]["datasets"].values():
            for point in dataset["points"]:
                point["C_over_E"] = math.exp(-(short_log if point["cooling_time_s"] < h.SHORT_S else long_log))
    if h.select(edge)["selected"] != "B":
        failures.append("selection rule does not accept the exact edge")
    sigma = copy.deepcopy(train["candidates"]["A"]["datasets"]["FI76-U233-pulse"]["points"][0])
    sigma.update(C_over_E=1.25, calculated=2.5, ORIGEN_C_over_E=None)
    band = c166.stats([sigma], "C_over_E")
    if band["within_1_sigma"] != 0.0 or band["within_2_sigma"] != 0.0:
        failures.append(f"sigma bands wrong: {band}")
    print("selftest:", "PASS" if not failures else "FAIL")
    for failure in failures:
        print("  " + failure)
    return not failures


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--train", type=Path, default=TRAIN)
    parser.add_argument("--test", type=Path, default=TEST)
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        sys.exit(0 if selftest() else 1)
    train, test = json.loads(args.train.read_text()), json.loads(args.test.read_text())
    p166 = json.loads(P166_RECORD.read_text())
    gates, result, lines = evaluate(train, test, read_csv(), p166, train_sha=c166.sha256(args.train))
    passed = all(not problems for problems in gates.values())
    verdict = {
        "protocol": "ACTINV-P169",
        "verdict": "P169-PASS (pending G5, CI on the evidence commit)" if passed else "P169-FAIL",
        "gates": {name: {"pass": not problems, "problems": problems} for name, problems in gates.items()},
        "summary": result,
        "allowed_sentences": lines,
    }
    for name, problems in gates.items():
        print(f"{name}: {'PASS' if not problems else 'FAIL'}")
        for problem in problems[:20]:
            print(f"  {problem}")
    if args.no_write:
        if VERDICT.is_file():
            recorded = json.loads(VERDICT.read_text())
            if recorded.get("summary") != json.loads(json.dumps(result)) or recorded.get(
                    "allowed_sentences") != lines:
                print("G4: FAIL (verdict file differs from the recomputed summary)")
                passed = False
    else:
        VERDICT.write_text(json.dumps(verdict, indent=1, sort_keys=True) + "\n")
    for line in lines:
        print("  " + line)
    print(verdict["verdict"] if passed else "P169-FAIL")
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
