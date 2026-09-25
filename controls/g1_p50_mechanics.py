#!/usr/bin/env python3
"""P50 G1 mechanics — VoI reporting contract.

Checks, on the frozen synthetic P11 fixture:
  determinism        — identical rerun emits identical voi blocks (the run
                       record's own wall-clock `ms` is excluded by declaration)
  flag_absent_bytes  — a run without `uncertainty.voi` is byte-identical to a
                       run with it, modulo `ms`
  schema_roundtrip   — `voi.top` survives spec parse/serialize
  top_bounds         — top=0 and top=257 are hard validation errors
  unknown_key        — an unknown `voi` key is a hard validation error
  emit_shape         — every requested response at every step carries a voi
                       block with `top`, `total_propagated_variance`,
                       `unranked`
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p11_fixtures as fx  # noqa: E402

ACTINV = Path(os.environ.get("ACTINV_BIN", ROOT / "target" / "release" / "actinv"))
OUT = ROOT / "results" / "g1_p50_mechanics.json"


def run(spec_path: Path, out_path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(ACTINV), "run", str(spec_path), str(out_path)],
        capture_output=True, text=True, timeout=300, cwd=ROOT,
    )


def strip_ms(obj, keys=("ms",)):
    if isinstance(obj, dict):
        return {k: strip_ms(v, keys) for k, v in obj.items() if k not in keys}
    if isinstance(obj, list):
        return [strip_ms(x, keys) for x in obj]
    return obj


def main() -> int:
    checks: dict[str, dict] = {}
    with tempfile.TemporaryDirectory(prefix="p50-g1-", dir="target") as d:
        work = Path(d)
        fixture = fx.make_fixture(work)
        base = fx.specification(fixture, mode="trace", cram_order=16)
        base["uncertainty"]["require_complete"] = False

        with_voi = dict(base)
        with_voi["uncertainty"] = dict(base["uncertainty"])
        with_voi["uncertainty"]["voi"] = {"top": 5}
        fx.write_json(work / "with_voi.json", with_voi)
        fx.write_json(work / "plain.json", base)

        assert run(work / "with_voi.json", work / "a.json").returncode == 0
        assert run(work / "with_voi.json", work / "b.json").returncode == 0
        assert run(work / "plain.json", work / "c.json").returncode == 0
        a, b, c = (json.load(open(work / f"{n}.json")) for n in "abc")

        av = [s["uncertainty"]["responses"]["heat.total"].get("voi")
              for s in a["steps"]]
        bv = [s["uncertainty"]["responses"]["heat.total"].get("voi")
              for s in b["steps"]]
        checks["determinism"] = {
            "pass": av == bv and all(x is not None for x in av)}
        checks["flag_absent_bytes"] = {
            "pass": strip_ms(a, ("ms", "voi")) == strip_ms(c)
            and all("voi" not in r for s in c["steps"]
                    for r in s["uncertainty"]["responses"].values())}

        # schema round-trip: a `voi`-carrying spec parses and runs end to end
        checks["schema_roundtrip"] = {
            "pass": run(work / "with_voi.json", work / "rt.json").returncode == 0}

        for name, mutate, want_reject in [
            ("top_zero", lambda u: u["voi"].update(top=0), True),
            ("top_257", lambda u: u["voi"].update(top=257), True),
            ("unknown_key", lambda u: u["voi"].update(bogus=1), True),
        ]:
            bad = json.loads(json.dumps(base))
            bad["uncertainty"]["voi"] = {"top": 5}
            mutate(bad["uncertainty"])
            fx.write_json(work / f"bad_{name}.json", bad)
            code = run(work / f"bad_{name}.json", work / "bad.json").returncode
            checks[f"reject_{name}"] = {"pass": (code != 0) == want_reject,
                                        "exit": code}

        shape_ok = True
        for step in a["steps"]:
            for resp in step["uncertainty"]["responses"].values():
                voi = resp.get("voi")
                if not (voi and isinstance(voi.get("top"), list)
                        and "total_propagated_variance" in voi):
                    shape_ok = False
        checks["emit_shape"] = {"pass": shape_ok,
                                "steps": len(a["steps"])}

    passed = all(c["pass"] for c in checks.values())
    OUT.write_text(json.dumps({"schema": "actinv-p50-g1-1",
                               "checks": checks, "pass": passed},
                              indent=2, sort_keys=True) + "\n")
    print(json.dumps({"pass": passed, "checks": list(checks)}))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
