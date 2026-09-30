#!/usr/bin/env python3
"""P86 G2 fallback: rerun the P85 runtime gates (G2, G3, G5) on the P86 build, when its binaries are
not byte-identical to the P85 candidate. Uses controls/check_p85.py unchanged, with its work
directory and verdict path redirected so that P85's own log and verdict are not overwritten.

    python3 controls/check_p86_rerun.py run
    python3 controls/check_p86_rerun.py check   # -> results/p86_p85_gates.json
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_p85 as p85  # noqa: E402

p85.WORK = p85.ROOT / "target" / "p86" / "p85gates"
p85.CACHES = p85.WORK / "cachedirs"
p85.VERDICT = p85.ROOT / "results" / "p86_p85_gates.json"

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in ("run", "check"):
        sys.exit(__doc__)
    p85.WORK.mkdir(parents=True, exist_ok=True)
    if sys.argv[1] == "run":
        p85.cmd_run()
    else:
        for name in ("build.log", "test.txt"):
            shutil.copy(p85.ROOT / "target" / "p86" / name, p85.WORK / name)
        p85.cmd_check()
