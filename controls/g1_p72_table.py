#!/usr/bin/env python3
"""P72 G1 — a declared `uncertainty.unmodeled_table` resolves to the same
banded emit as a hand-set `unmodeled_relative`, with the resolution
provenance recorded under `certificate.inputs.unmodeled_table`.

Checks: declared key resolves per_material; isotope composition keys
infer the element family; absent key uses spec `fallback` then the
table's `default`; sha/shape/mutual-exclusion defects are refused."""
from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

import p58_fixture
import p60_case

ROOT = Path(__file__).resolve().parent.parent
ACTINV = ROOT / "target/debug/actinv"
OUT = ROOT / "results/g1_p72_table.json"

checks = []


def check(name, ok, detail=""):
    checks.append({"name": name, "pass": bool(ok), "detail": detail})


tmp = Path(tempfile.mkdtemp(prefix="p72_g1_", dir=ROOT / "target"))
fx = p58_fixture.build(tmp)
base = p60_case.spec(fx)

table = {
    "schema": "actinv-unmodeled-table-1",
    "default": 0.5,
    "per_material": {"Fe": 0.31, "W": 0.9},
}
table_path = tmp / "u_table.json"
table_text = json.dumps(table, sort_keys=True)
table_path.write_text(table_text)
table_sha = hashlib.sha256(table_text.encode()).hexdigest()


def run_with(mutate, tag):
    sp = json.loads(json.dumps(base))
    mutate(sp)
    return p60_case.run(ACTINV, sp, tmp, tag)


def scrub(o):
    if isinstance(o, dict):
        return {k: scrub(v) for k, v in o.items() if k != "ms"}
    if isinstance(o, list):
        return [scrub(v) for v in o]
    return o


# A: hand-declared u — the reference emit
ref = run_with(lambda s: s["uncertainty"].__setitem__("unmodeled_relative", 0.31), "ref")

# B: table with declared key must resolve to the identical emit
tab = run_with(
    lambda s: s["uncertainty"].__setitem__(
        "unmodeled_table",
        {"path": str(table_path), "sha256": table_sha, "key": "Fe"},
    ),
    "tab",
)
steps_ref = scrub(ref["steps"])
steps_tab = [dict(s) for s in scrub(tab["steps"])]
check(
    "table-resolved bands byte-identical to declared u",
    steps_ref == steps_tab,
    "0.31 from per_material['Fe'] applied where unmodeled_relative lived",
)
prov = tab["certificate"]["inputs"].get("unmodeled_table")
check(
    "resolution provenance emitted",
    isinstance(prov, dict)
    and prov.get("resolved_unmodeled_relative") == 0.31
    and prov.get("key") == "Fe"
    and prov.get("key_source") == "declared"
    and prov.get("source") == "per_material"
    and prov.get("sha256") == table_sha,
    json.dumps(prov),
)

# C: no key — the Fe56 composition infers family "Fe"
inf = run_with(
    lambda s: s["uncertainty"].__setitem__(
        "unmodeled_table", {"path": str(table_path), "sha256": table_sha}
    ),
    "inf",
)
check(
    "composition-dominant key infers the family",
    scrub(inf["steps"]) == steps_ref
    and inf["certificate"]["inputs"]["unmodeled_table"]["key_source"]
    == "inferred",
)

# D: declared key missing → spec fallback wins over table default
fb = run_with(
    lambda s: s["uncertainty"].__setitem__(
        "unmodeled_table",
        {
            "path": str(table_path),
            "sha256": table_sha,
            "key": "Xx",
            "fallback": 0.31,
        },
    ),
    "fb",
)
check(
    "missing key falls to spec fallback",
    scrub(fb["steps"]) == steps_ref
    and fb["certificate"]["inputs"]["unmodeled_table"]["source"]
    == "spec_fallback",
)

# E: declared key missing, no fallback → the table's own default
df = run_with(
    lambda s: s["uncertainty"].__setitem__(
        "unmodeled_table",
        {"path": str(table_path), "sha256": table_sha, "key": "Xx"},
    ),
    "df",
)
check(
    "missing key with no fallback uses table default",
    df["certificate"]["inputs"]["unmodeled_table"]["source"] == "table_default"
    and df["certificate"]["inputs"]["unmodeled_table"][
        "resolved_unmodeled_relative"
    ]
    == 0.5,
)


def expect_err(sp, needle):
    f = tmp / "expect_err.spec.json"
    f.write_text(json.dumps(sp))
    p = subprocess.run(
        [str(ACTINV), "run", str(f), str(tmp / "expect_err.out.json")],
        capture_output=True,
        text=True,
        timeout=600,
        cwd=ROOT,
    )
    return p.returncode != 0 and needle in (p.stdout + p.stderr)


sp_bad = json.loads(json.dumps(base))
sp_bad["uncertainty"]["unmodeled_relative"] = 0.2
sp_bad["uncertainty"]["unmodeled_table"] = {
    "path": str(table_path),
    "sha256": table_sha,
    "key": "Fe",
}
check(
    "unmodeled_relative + unmodeled_table refused together",
    expect_err(sp_bad, "mutually exclusive"),
)
sp_sha = json.loads(json.dumps(base))
sp_sha["uncertainty"]["unmodeled_table"] = {
    "path": str(table_path),
    "sha256": "0" * 64,
    "key": "Fe",
}
check("table sha256 mismatch refused", expect_err(sp_sha, "SHA-256 mismatch"))
sp_nokey = json.loads(json.dumps(base))
table2 = {
    "schema": "actinv-unmodeled-table-1",
    "per_material": {"Fe": 0.31},
}
t2 = tmp / "u_table_nodefault.json"
t2.write_text(json.dumps(table2))
t2_sha = hashlib.sha256(
    (json.dumps(table2)).encode()
).hexdigest()
sp_nokey["uncertainty"]["unmodeled_table"] = {
    "path": str(t2),
    "sha256": t2_sha,
    "key": "Xx",
}
check(
    "missing key with no default anywhere refused",
    expect_err(sp_nokey, "no per_material entry"),
)

failed = [c for c in checks if not c["pass"]]
OUT.write_text(
    json.dumps({"pass": not failed, "n": len(checks), "checks": checks}, indent=1)
    + "\n"
)
for c in checks:
    print(("PASS" if c["pass"] else "FAIL"), c["name"], c["detail"])
print(f"{len(checks) - len(failed)}/{len(checks)} checks passed → {OUT}")
raise SystemExit(1 if failed else 0)
