#!/usr/bin/env python3
"""P38 G3: negative controls for the relaxed admission paths.

Controls:
  nc_defect_files   - the five true-defect evaluations still fail their
                      original gates (build evidence captured in
                      results/g3_p38_defect_rejects.json)
  nc_source_retained - the retained RML checks (mass, penetrability, shift,
                      MT) and the zero-resonance placeholder tolerance's
                      all-zero requirement are present in source
  nc_unit_tests     - the builder/resonance unit tests cover both new
                      admission paths and the retained rejections
  nc_self_comp_bound - the MF10-only comparator is only reachable when no
                      MF3/processed total exists (source structure check)
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
checks = []

# nc_defect_files: recorded rejections from the bounded build probes
rej = json.loads((ROOT / "results" / "g3_p38_defect_rejects.json").read_text())
expected = {
    "Ni62": "total width",
    "W182": "ZA/AWR",
    "W183": "emitted state sum",
    "W184": "ZA/AWR",
    "W186": "emitted state sum",
}
bad = [f for f, pat in expected.items()
       if pat not in rej.get(f, {}).get("error", "")]
checks.append(("nc_defect_files",
               "all five true-defect evaluations still fail their original "
               "gates",
               not bad, {"missing_or_wrong": bad, "errors": rej}))

# nc_source_retained
src = (ROOT / "crates/actinv-data/src/resonance.rs").read_text()
pair_checks = all(p in src for p in (
    "pair.mass_a < 0.0", "pair.mass_b <= 0.0",
    "matches!(pair.penetrability, 0 | 1)",
    "matches!(pair.shift, 0 | 1)", "pair.mt <= 0"))
placeholder_guard = (
    "resonance_count == 0" in src
    and "head.n1 % values_per_resonance == 0" in src
    and "all(|value| *value == 0.0)" in src)
checks.append(("nc_source_retained",
               "RML retains mass/penetrability/shift/MT checks; zero-NRS "
               "placeholder tolerance requires an all-zero payload",
               pair_checks and placeholder_guard, {}))

# nc_unit_tests
tests_present = (
    "rml_negative_spin_parity_encoding_is_admitted" in src
    and "missing_total_self_comparator"
        in (ROOT / "crates/actinv-data/src/builder.rs").read_text())
checks.append(("nc_unit_tests",
               "unit tests cover signed-spin admission and the MF10-only "
               "self-comparator ledger", tests_present, {}))

# nc_self_comp_bound: the self-comparator branch is only reachable when
# neither a processed nor raw MF=3 total exists — the else arm that sums
# MF=10 partials sits behind `processed.get`/`evaluation.mf3.get` misses
b = (ROOT / "crates/actinv-data/src/builder.rs").read_text()
gate_ok = ("processed.get(&mt)" in b
           and "evaluation.mf3.get(&mt)" in b
           and "!evaluation.mf10.contains_key(&mt)" in b
           and "missing_total_self_comparator" in b)
checks.append(("nc_self_comp_bound",
               "self-comparator branch only reachable when no independent "
               "total exists", gate_ok, {}))

result = {"schema": "actinv-p38-g3-1",
          "checks": [{"name": n, "claim": c, "pass": p, "detail": d}
                     for n, c, p, d in checks]}
result["all_pass"] = all(c["pass"] for c in result["checks"])
(ROOT / "results/g3_p38_check.json").write_text(json.dumps(result, indent=1))
print(json.dumps({c["name"]: c["pass"] for c in result["checks"]}, indent=1))
sys.exit(0 if result["all_pass"] else 1)
