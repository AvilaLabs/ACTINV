#!/usr/bin/env python3
"""P44 G2 frozen controls: synthetic corpus + real resume exercise the
frozen scoring rules from the sealed protocol."""
import json
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "results", "g2_p44_controls.json")
DATA = os.path.expanduser("~/nuclear-data")
WORK = os.path.join(DATA, "p44-work")
BANDS = os.path.join(WORK, "bands")

sys.path.insert(0, os.path.join(ROOT, "controls"))
import p44_band_coverage as sc  # noqa: E402
import p44_bands  # noqa: E402

controls = {}


def record(name, ok, detail=None):
    controls[name] = {"status": "pass" if ok else "fail", "detail": detail}


# ---- synthetic corpus: planted offsets inside/outside a known band ----
band = {"t_s": 100.0, "nominal": 1.0e-6, "lo": 8.0e-7, "hi": 1.2e-6}
inside = sc.point_outcome(band, 1.0e-6, 1e-8, "executed")
outside = sc.point_outcome(band, 1.5e-6, 1e-8, "executed")
record("planted_offsets",
       inside["band_only"] == "covered"
       and outside["band_only"] == "not_covered",
       {"inside": inside["band_only"], "outside": outside["band_only"]})

# sigma-only inclusion flips combined_sigma without touching band_only
# (|m-c| = 2.5e-7 > w = 2e-7; sigma 2e-7 widens to sqrt(8)e-7 = 2.83e-7)
sig_only = sc.point_outcome(band, 1.25e-6, 2.0e-7, "executed")
record("sigma_only_inclusion",
       sig_only["band_only"] == "not_covered"
       and sig_only["combined_sigma"] == "covered",
       {"band_only": sig_only["band_only"],
        "combined_sigma": sig_only["combined_sigma"]})

# ---- boundary: inclusive on lo, hi, and the combined widened edge ----
on_lo = sc.point_outcome(band, 8.0e-7, 1e-9, "executed")
on_hi = sc.point_outcome(band, 1.2e-6, 1e-9, "executed")
# combined edge: c=1.0e-6, w=2.0e-7; sigma such that
# sqrt(w^2+s^2) = 4.0e-7 -> s = sqrt(16-4)e-7 = sqrt(12)e-7
s_edge = math.sqrt(12.0) * 1e-7
on_comb = sc.point_outcome(band, 1.4e-6, s_edge, "executed")
record("boundary_inclusive",
       on_lo["band_only"] == "covered"
       and on_hi["band_only"] == "covered"
       and on_comb["combined_sigma"] == "covered",
       {"lo": on_lo["band_only"], "hi": on_hi["band_only"],
        "combined_edge": on_comb["combined_sigma"]})

# ---- denominator: failed band + zero prediction + undefined ----
failed = sc.point_outcome(None, 1.0e-6, 1e-8, "failed")
zero = sc.point_outcome({"t_s": 1, "nominal": 0.0, "lo": 0.0, "hi": 0.0},
                        1.0e-6, 1e-8, "executed")
record("denominator_failures",
       failed["band_only"] == "not_covered"
       and failed["reason"] == "band_unavailable"
       and zero["band_only"] == "not_covered"
       and zero["reason"] == "zero_prediction",
       {"failed": failed, "zero": zero})

# ---- arithmetic: aggregates re-derived by hand from planted records ----
def mk(mat, exp, outcomes):
    return {"material": mat, "experiment": exp,
            "time_unit_inferred": "s", "excluded_nonpoints": 0,
            "excluded_rows": [],
            "points": [{"row": i, "t_s": 100.0,
                        "measured_W_g": 1e-6, "sigma_W_g": 1e-8,
                        "bands": {bt: {"band_only": oc, "combined_sigma": oc,
                                       "reason": None, "band": None}
                                  for bt in sc.BAND_TYPES}}
                       for i, oc in enumerate(outcomes)]}

recs = [mk("X", "e_a", ["covered", "covered", "not_covered"]),
        mk("X", "e_b", ["not_covered"]),
        mk("Y", "e_a", ["covered"])]
agg = sc.aggregate(recs)
fo = agg["first_order.band_only"]
record("aggregate_arithmetic",
       fo["pooled"]["all"]["coverage"] == 3 / 5
       and fo["material"]["X"]["coverage"] == 2 / 4
       and fo["material"]["Y"]["coverage"] == 1.0
       and fo["experiment"]["X/e_a"]["coverage"] == 2 / 3
       and fo["experiment"]["X/e_b"]["coverage"] == 0.0
       and fo["experiment_type"]["e_a"]["coverage"] == 3 / 4,
       fo)

# ---- alignment: unit inference + undefined naming ----
cooling = [3600.0, 7200.0, 14400.0]
t_raw = [1.0, 2.0, 4.0]
heat = [1.0, 1.0, 1.0]
matched, excluded, unit = sc.align(cooling, t_raw, heat)
record("alignment_unit_inference",
       unit == "h" and len(matched) == 3 and not excluded,
       {"unit": unit, "matched": len(matched), "excluded": excluded})

# unmappable under every unit -> named undefined, never silent
t_raw_bad = [7.0, 13.0, 29.0]
matched2, excluded2, unit2 = sc.align(cooling, t_raw_bad, heat)
undef_named = all(e["reason"] == "no_cooling_step_within_2_percent"
                  for e in excluded2)
record("alignment_undefined_named",
       undef_named and len(matched2) == 0,
       {"unit": unit2, "matched": len(matched2), "excluded": excluded2})

# ---- resume: torn band record re-derived through the driver ----
rec_path = os.path.join(BANDS, "Fe__1996exp_5min.json")
rec = json.load(open(rec_path))
torn = json.loads(json.dumps(rec))
torn["sampled"] = {"status": "failed", "bands": [], "error": "planted"}
open(rec_path, "w").write(json.dumps(torn))
restored = p44_bands.produce("Fe", "1996exp_5min",
                             {"material": "Fe", "experiment": "1996exp_5min"})
open(rec_path, "w").write(json.dumps(restored, indent=1))
sa = restored.get("sampled") or {}
same_bands = (sa.get("bands") or []) == (rec["sampled"]["bands"])
record("torn_record_resumed",
       sa.get("status") == "executed" and same_bands,
       {"status": sa.get("status"),
        "bands_identical": same_bands})

doc = {"gate": "G2", "phase": "P44", "controls": controls,
       "pass": all(c["status"] == "pass" for c in controls.values())}
json.dump(doc, open(OUT, "w"), indent=2, sort_keys=True)
print(json.dumps(doc, indent=1))
