"""Exercise both CI install layouts with all network transports disabled."""
from pathlib import Path
import json
import sys
from unittest.mock import patch

ROOT = Path.cwd()
sys.path.insert(0, str(ROOT / "scripts"))
import fetch_ci_seed

source = ROOT / "target/p117-ci-seed"
with patch.object(fetch_ci_seed, "_open_release", side_effect=AssertionError("network disabled")):
    controls = fetch_ci_seed.install("controls", ROOT / "target/p117-controls-data", offline_root=source)
    fns = fetch_ci_seed.install("fns", ROOT / "target/p117-fns-data",
                              ROOT / "target/fns-iron/fns.zip", offline_root=source)
assert controls["files"] == 11 and controls["installed"] == 11
assert fns["files"] == 3 and fns["installed"] == 3
print(json.dumps({"controls": controls, "fns": fns, "network_disabled": True}, sort_keys=True, indent=2))
