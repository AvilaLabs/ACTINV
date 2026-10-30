"""Error metrics of an ACTINV result against a committed reference (Decimal; no mpmath).

  M1  max_i |N_i - R_i| / max_i R_i over the reference nuclides (a nuclide missing from the result counts as N_i = 0).
  M2  max_i |N_i - R_i| / R_i over nuclides whose reference activity lambda_i R_i is at least ACTIVITY_FRACTION of the
      step's total reference activity (stable nuclides are excluded).
  M3  (trace case only) max_i |N_i - R_i| / R_i over every reference nuclide with R_i > 0.
"""
import math
import decimal
from decimal import Decimal, getcontext

from p168 import cases

getcontext().prec = 60
LN2 = Decimal(2).ln()
INF = float("inf")


class Reference:
    """A committed reference file: nuclide identities, decay constants and the kept steps."""

    def __init__(self, doc, scheduled_steps):
        self.name = doc["case"]
        self.keys = {n["name"]: (n["za"] // 1000, n["za"] % 1000, n["liso"]) for n in doc["nuclides"]}
        self.lam = {n["name"]: (LN2 / Decimal(n["half_life_s"]) if n["half_life_s"] else Decimal(0)) for n in doc["nuclides"]}
        self.steps = [{"step": s["step"], "label": s["label"], "t": Decimal(s["t_end_s"]),
                       "inv": {k: Decimal(v) for k, v in s["inventory_atoms_per_g"].items()}}
                      for s in doc["reference_steps"] if s["step"] <= scheduled_steps]


def _error(got, ref):
    """Relative error numerator |got - ref| as Decimal; infinite for a value that is not finite."""
    if isinstance(got, float) and not math.isfinite(got):
        return None
    return abs(Decimal(got) - ref)


M3_FLOOR = Decimal(cases.M3_FLOOR)


def _relative(e, r):
    """|got - ref| / ref as a float; infinite when not finite or beyond Decimal's exponent range."""
    if e is None:
        return INF
    try:
        return float(e / r)
    except decimal.Overflow:
        return INF


def step_metrics(reference, step, got, trace):
    """Metrics of one step. `got` maps nuclide name to atoms/g (float). Returns metric -> (value, nuclide) or None."""
    inv = step["inv"]
    ref_max = max(inv.values())
    errors = {name: _error(got.get(name, 0.0), r) for name, r in inv.items()}
    out = {"M1": None, "M2": None, "M3": None}
    best = (-1.0, None)
    for name, e in errors.items():
        value = INF if e is None else (float(e / ref_max) if ref_max > 0 else (0.0 if e == 0 else INF))
        if value > best[0]:
            best = (value, name)
    out["M1"] = best
    total = sum(reference.lam[n] * r for n, r in inv.items())
    cut = Decimal(cases.ACTIVITY_FRACTION) * total
    best2 = (0.0, None)
    best3 = (0.0, None)
    for name, e in errors.items():
        r = inv[name]
        if r <= 0:
            continue
        scored = reference.lam[name] > 0 and reference.lam[name] * r >= cut and total > 0
        if not (scored or (trace and r >= M3_FLOOR)):
            continue  # Amendment A: the relative error is formed only where M2 or M3 uses it
        rel = _relative(e, r)
        if scored and rel > best2[0]:
            best2 = (rel, name)
        if trace and r >= M3_FLOOR and rel > best3[0]:
            best3 = (rel, name)
    out["M2"] = best2
    out["M3"] = best3 if trace else None
    return out


def compare_run(reference, result_steps, trace, scheduled_steps):
    """Score `result_steps` (ACTINV `steps` without the t = 0 entry) at every kept reference step.

    Returns {"steps_compared", "time_error", "worst", "first_failure"}; raises ValueError when the result lacks steps
    or a reference nuclide's identity is absent from every step (it is then treated as zero, not an error)."""
    if len(result_steps) != scheduled_steps:
        raise ValueError(f"result has {len(result_steps)} steps, schedule has {scheduled_steps}")
    metrics = ("M1", "M2", "M3") if trace else ("M1", "M2")
    worst = {m: {"value": 0.0, "step": None, "label": None, "nuclide": None} for m in metrics}
    first, time_error = None, 0.0
    by_key = {key: name for name, key in reference.keys.items()}
    for step in reference.steps:
        raw = result_steps[step["step"] - 1]
        got = {}
        for entry in raw["inventory"]:
            name = by_key.get((entry["Z"], entry["A"], entry["LISO"]))
            if name is not None:
                got[name] = float(entry["atoms_per_g"])
        time_error = max(time_error, abs(float(raw["t_s"]) - float(step["t"])) / float(step["t"]))
        values = step_metrics(reference, step, got, trace)
        failing = None
        for m in metrics:
            value, nuclide = values[m]
            if value > worst[m]["value"] or worst[m]["step"] is None:
                worst[m] = {"value": value, "step": step["step"], "label": step["label"], "nuclide": nuclide}
            if value > cases.THRESHOLDS[m] and (failing is None or value / cases.THRESHOLDS[m] > failing[0]):
                failing = (value / cases.THRESHOLDS[m], m, value, nuclide)
        if failing is not None and first is None:
            first = {"metric": failing[1], "value": failing[2], "step": step["step"], "label": step["label"],
                     "nuclide": failing[3]}
    return {"steps_compared": len(reference.steps), "time_error": time_error, "worst": worst, "first_failure": first}


def status_of(worst):
    """holds / breaks from the worst values alone (what the checker re-derives)."""
    for metric, entry in worst.items():
        value = entry["value"]
        if not (value <= cases.THRESHOLDS[metric]):
            return "breaks"
    return "holds"


def classify_run(exit_status, timed_out, scored=None, reason=None):
    """The status part of a per-run record: fails-to-run for a timeout, nonzero exit or unreadable result."""
    if timed_out or exit_status != 0 or scored is None:
        return {"status": "fails-to-run", "reason": reason or ("timeout" if timed_out else f"exit status {exit_status}"),
                "worst": None, "first_failure": None}
    return {"status": status_of(scored["worst"]), "reason": None, "worst": scored["worst"],
            "first_failure": scored["first_failure"]}
