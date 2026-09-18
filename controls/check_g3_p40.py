#!/usr/bin/env python3
"""P40 G3: negative controls on the divergence classifier."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import g2_p40_classes as m  # noqa: E402
import g2_p26b_leg as leg  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
hl = leg.idx_parse(Path(str(leg.ALARA_LIB_BASE) + ".idx"))
checks = []

# nc_classify_isomer: metastable labels tag isomer_branching
checks.append(("nc_classify_isomer",
               "'XxNm1' names tag isomer_branching",
               m.classify("Mn58m1", hl) == "isomer_branching"
               and m.classify("Nb93m1", hl) == "isomer_branching", {}))

# nc_classify_quasi: finite t_half>1e15s tags quasi_stable_convention
checks.append(("nc_classify_quasi",
               "Mo98 (t_half=3.16e21s in idx) tags quasi_stable",
               m.classify("Mo98", hl) == "quasi_stable_convention",
               {"idx_t_half": hl.get(420980)}))

# nc_classify_other: ordinary radioactive nuclides tag other
checks.append(("nc_classify_other",
               "real radioactive nuclides (Ni59, t~76ky) tag other",
               m.classify("Ni59", hl) == "other"
               and m.classify("Mn56", hl) == "other", {}))

# nc_classify_stable_not_quasi: truly stable nuclides (hl=None) tag
# other, not quasi_stable — a stable nuclide WITH activity would be an
# inconsistency, not a convention
checks.append(("nc_stable_is_other",
               "stable nuclides (hl=None) are not quasi_stable",
               m.classify("Fe56", hl) == "other", {}))

# nc_zlabel: unresolved ALARA Z-labels resolve to canonical names
checks.append(("nc_zlabel_resolve",
               "'Z22-51'->Ti51, 'Z43-99m'->Tc99m1, 'Z1-3'->H3",
               m.resolve_zlabel("Z22-51") == "Ti51"
               and m.resolve_zlabel("Z43-99m") == "Tc99m1"
               and m.resolve_zlabel("Z1-3") == "H3", {}))

# nc_zlabel_isomer_classifies: resolved Z-label isomers tag correctly
checks.append(("nc_zlabel_isomer",
               "resolved Z-label isomers tag isomer_branching",
               m.classify(m.resolve_zlabel("Z43-99m"), hl)
               == "isomer_branching", {}))

# nc_kza: KZA encoding matches idx convention (Z*10000+A*10+LISO)
checks.append(("nc_kza_encoding",
               "kza_of('Mn58m1') == 250581 and kza_of('Mo98') == 420980",
               m.kza_of("Mn58m1") == 250581
               and m.kza_of("Mo98") == 420980, {}))

result = {"schema": "actinv-p40-g3-1",
          "checks": [{"name": n, "claim": c, "pass": p, "detail": d}
                     for n, c, p, d in checks]}
result["all_pass"] = all(c["pass"] for c in result["checks"])
(ROOT / "results/g3_p40_check.json").write_text(json.dumps(result, indent=1))
print(json.dumps({c["name"]: c["pass"] for c in result["checks"]}, indent=1))
sys.exit(0 if result["all_pass"] else 1)
