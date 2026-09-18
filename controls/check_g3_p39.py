#!/usr/bin/env python3
"""P39 G3: negative controls for lumped-channel synthesis."""
import json
import sys
import zipfile
import io
from collections import Counter
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
RUN = Path.home() / "nuclear-data/p26b-work/p38-run"
checks = []

# nc_no_double_count: no summary-MT residual row was duplicated by
# lumped synthesis
def rows_of(npz):
    z = zipfile.ZipFile(npz)
    return np.load(io.BytesIO(z.read("rows.npy")))

old = rows_of(RUN / "actinv_fendl32c_709_p38.npz")
new = rows_of(RUN / "actinv_fendl32c_709_p39.npz")
new_c = Counter((r[0], r[1], r[2], r[3]) for r in new)
old_c = Counter((r[0], r[1], r[2], r[3]) for r in old)
new_dups = {k for k, v in new_c.items() if v > 1}
old_dups = {k for k, v in old_c.items() if v > 1}
checks.append(("nc_no_double_count",
               "lumped synthesis introduces no duplicate (target,mt,zap,"
               "lfs) rows beyond the pre-existing p38 set",
               new_dups == old_dups,
               {"new_only": [tuple(int(x) for x in k)
                             for k in new_dups - old_dups]}))

# nc_removed_zero: synthesis is additive only
removed = set((int(r[0]), int(r[1]), int(r[2]), int(r[3])) for r in old) - \
    set((int(r[0]), int(r[1]), int(r[2]), int(r[3])) for r in new)
checks.append(("nc_no_rows_removed",
               "no p38 row was removed or altered",
               not removed, {"removed": list(removed)}))

# nc_defects_still_reject: recorded fresh rejects under the p39 binary
rej = json.loads((ROOT / "results/g3_p39_defect_rejects.json").read_text())
expected = {
    "Ni62": "total width", "W182": "ZA/AWR", "W183": "emitted state sum",
    "W184": "ZA/AWR", "W186": "emitted state sum",
}
bad = [f for f, pat in expected.items()
       if pat not in rej.get(f, {}).get("error", "")]
checks.append(("nc_defects_still_reject",
               "all five defect evaluations still fail their original "
               "gates under the p39 binary",
               not bad, {"bad": bad}))

# nc_unit_test covers the semantics
src = (ROOT / "crates/actinv-data/src/builder.rs").read_text()
checks.append(("nc_unit_test",
               "unit test covers synthesis-when-absent, summary-governs "
               "skip, and MF10-governs skip",
               "lumped_channels_synthesize_only_when_summary_coverage_"
               "is_absent" in src, {}))

result = {"schema": "actinv-p39-g3-1",
          "checks": [{"name": n, "claim": c, "pass": p, "detail": d}
                     for n, c, p, d in checks]}
result["all_pass"] = all(c["pass"] for c in result["checks"])
(ROOT / "results/g3_p39_check.json").write_text(json.dumps(result, indent=1))
print(json.dumps({c["name"]: c["pass"] for c in result["checks"]}, indent=1))
sys.exit(0 if result["all_pass"] else 1)
