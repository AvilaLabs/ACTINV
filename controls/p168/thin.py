"""Raw high-precision case files -> the committed references in results/p168/refs (plain Python, no mpmath).

Standard cases are copied with the precision note corrected. The pulse-train references keep, as `reference_steps`
(1-based schedule step index, on/off alternating): every step of the first 10 pulses, the end-of-pulse and
end-of-period steps of every 100th pulse, and the final step (4_pulse_train_0p1s_1e4); the 10^k checkpoints
(the two 1e6 cases, truncated at 1e3 pulses by the case set) or the 10^k checkpoints up to 1e5 pulses
(4_pulse_train_0p1s_1e5, derived from the 0.1 s 1e6 reference).
"""
import copy
import json
from decimal import Decimal, getcontext
from pathlib import Path

from p168 import cases

getcontext().prec = 60
PRECISION_NOTE = ("mpmath scaling-and-squaring Taylor expm; per-case working precision of 120 to 500 digits, cross-checked "
                  "at twice that (precision_cross_check_max_rel_diff); output 30 significant digits")
DERIVED = "4_pulse_train_0p1s_1e5"
DERIVED_FROM = "4_pulse_train_0p1s_1e6"
DERIVED_PULSES = 100_000


def fmt(value):
    return format(value, ".29e")


def dump(doc, path):
    Path(path).write_text(json.dumps(doc, indent=1) + "\n")


def kept_pulse_steps(n_pulses):
    """Sorted 1-based schedule step indices kept for an n-pulse train (step 2k-1 ends pulse k, step 2k its period)."""
    kept = set(range(1, min(n_pulses, 10) * 2 + 1))
    for pulse in range(100, n_pulses + 1, 100):
        kept.update((2 * pulse - 1, 2 * pulse))
    kept.add(2 * n_pulses)
    return sorted(kept)


def pulse_period(doc):
    train = doc["schedule"]["pulse_train"]
    return Decimal(train["on"]["dt_s"]), Decimal(train["on"]["dt_s"]) + Decimal(train["off"]["dt_s"])


def steps_from_checkpoints(doc, max_pulses):
    rows = []
    for row in doc["reference_checkpoints"]:
        n = row["pulse_count"]
        if n > max_pulses:
            continue
        rows.append({"step": 2 * n - 1, "t_end_s": row["t_end_of_pulse_s"], "label": f"end of pulse {n}",
                     "inventory_atoms_per_g": row["end_of_pulse"]})
        rows.append({"step": 2 * n, "t_end_s": row["t_end_of_period_s"], "label": f"end of period {n}",
                     "inventory_atoms_per_g": row["end_of_period"]})
    return rows


def steps_from_per_step(doc):
    names = doc["reference_per_step_columns"][:3]
    n_pulses = doc["schedule"]["pulse_train"]["n_pulses"]
    on, period = pulse_period(doc)
    rows = []
    for step in kept_pulse_steps(n_pulses):
        pulse, ending = divmod(step - 1, 2)
        t = pulse * period + (on if ending == 0 else period)
        rows.append({"step": step, "t_end_s": fmt(t), "label": f"end of {'period' if ending else 'pulse'} {pulse + 1}",
                     "inventory_atoms_per_g": dict(zip(names, doc["reference_per_step"][step - 1][:3]))})
    return rows


def thin_pulse(doc, name):
    doc = copy.deepcopy(doc)
    doc["case"] = name
    doc["conventions"] = {"precision": PRECISION_NOTE}
    if "reference_per_step" in doc:
        doc["reference_steps"] = steps_from_per_step(doc)
        doc["reference_steps_note"] = ("every step of the first 10 pulses, end of pulse and end of period of every 100th "
                                       "pulse, and the final step; values from plain stepping")
        del doc["reference_per_step"], doc["reference_per_step_columns"]
    else:
        limit = doc["schedule"]["pulse_train"]["n_pulses"]
        doc["reference_steps"] = steps_from_checkpoints(doc, limit)
        doc["reference_steps_note"] = "the 10^k pulse checkpoints (period-propagator powers)"
    return doc


def build_refs(raw_dir, out_dir):
    raw_dir, out_dir = Path(raw_dir), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for case in cases.CASES:
        name = case["name"]
        source = name if name != DERIVED else DERIVED_FROM
        doc = json.loads((raw_dir / f"case_{source}.json").read_text())
        if case["pulses"] is None:
            doc["conventions"]["precision"] = PRECISION_NOTE
        elif name == DERIVED:
            doc = thin_pulse(doc, name)
            train = doc["schedule"]["pulse_train"]
            train["n_pulses"], train["steps_total"] = DERIVED_PULSES, 2 * DERIVED_PULSES
            doc["reference_checkpoints"] = [r for r in doc["reference_checkpoints"] if r["pulse_count"] <= DERIVED_PULSES]
            doc["reference_steps"] = steps_from_checkpoints(doc, DERIVED_PULSES)
            doc["periodic_steady_state"]["fraction_of_steady_state_reached_after_all_pulses"] = (
                doc["reference_checkpoints"][-1]["fraction_of_periodic_steady_state_activity"])
            doc["description"] += " Truncated at 1e5 pulses (checkpoints of the 1e6 reference)."
        else:
            doc = thin_pulse(doc, name)
        dump(doc, out_dir / f"{name}.json")


def write_manifest(out_dir, manifest_path, mpmath_version):
    import hashlib
    files = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(Path(out_dir).glob("*.json"))}
    dump({"schema": "actinv-p168-refs-1", "mpmath_version": mpmath_version,
          "digits": {"working": "120 to 500 per case (each file's precision_cross_check_max_rel_diff), cross-checked at twice that",
                     "recorded": 30},
          "generator": "controls/p168/make_cases.py", "files": files}, manifest_path)
