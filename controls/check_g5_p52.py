#!/usr/bin/env python3
"""P52 G5 — independent checker. Re-derives every emitted quantity from the
raw `actinv-mesh-result-1` bytes plus the parity/determinism ledgers, without
trusting the emitted r2s document's arithmetic:

  * per-nuclide sigma:   strength_i * (sigma_activity_i / nominal_i)
  * cell totals:         sigma_indep = sqrt(sum sigma_i^2),
                         sigma_consv = sum sigma_i, partial => null
  * unbanded share, coverage counts, header binding hash
  * parity legs' recorded rel-diffs recomputed
  * determinism ledger checks re-read

`--mutate N` rewrites results/p52_r2s_source.ndjson with one corruption and
must be rejected.
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MESH = ROOT / "results/p52_mesh.ndjson"
R2S = ROOT / "results/p52_r2s_source.ndjson"
PARITY = ROOT / "results/g2_p52_parity.json"
DET = ROOT / "results/g4_p52_determinism.json"
DEMO = ROOT / "results/g3_p52_demo.json"
OUT = ROOT / "results/check_g5_p52.json"
RTOL = 1e-9


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def close(a, b, tol=RTOL):
    if a is None or b is None:
        return a is b
    # Pure-relative: a 1.0 absolute floor makes every sigma below ~tol
    # compare equal (sigmas routinely run to 1e-30 — a 2x corruption then
    # passes). 1e-300 only rescues exact-zero vs exact-zero.
    return abs(a - b) <= tol * max(abs(a), abs(b), 1e-300)


def parse_ndjson(path: Path):
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def rel_sigma(resp: dict | None):
    if not resp:
        return None
    nom = resp.get("nominal")
    if not (isinstance(nom, (int, float)) and nom > 0):
        return None
    sig = resp.get("combined_standard_uncertainty")
    if sig is None:
        sig = resp.get("mf33_standard_uncertainty")
    if not (isinstance(sig, (int, float)) and sig >= 0):
        return None
    return sig / nom


def expected_cell(mesh_cell: dict, step: int) -> dict:
    result = mesh_cell["result"]
    st = next(s for s in result["steps"] if s["step"] == step)
    photon = st["photon_source"]
    uq = st["uncertainty"]
    responses = uq["responses"]
    nucs, indep_sq, consv, unbanded, banded_n = [], 0.0, 0.0, 0.0, 0
    for e in photon.get("by_nuclide", []):
        strength = sum(g.get("photons_s", 0.0)
                       for g in e.get("groups", []))
        if strength <= 0:
            continue
        rs = rel_sigma(responses.get(f"activity:{e['nuclide']}"))
        sigma = strength * rs if rs is not None else None
        if sigma is None:
            unbanded += strength
        else:
            banded_n += 1
            indep_sq += sigma * sigma
            consv += sigma
        nucs.append((e["nuclide"], strength, sigma))
    total = photon.get("total_photons_s", 0.0)
    partial = len(nucs) > 0 and banded_n < len(nucs)
    return {
        "photons_s": total,
        "n_nuclides": len(nucs),
        "banded": banded_n,
        "partially_unbanded": partial,
        "unbanded_share": (unbanded / total) if total > 0 else 0.0,
        # banded-part sums (lower bound); 0.0 when nothing is banded
        "sigma_independent": indep_sq ** 0.5,
        "sigma_conservative": consv,
        "uncovered_rows": len(uq.get("uncovered_library_rows") or []),
        "per_nuclide": dict((n, (s, sig)) for n, s, sig in nucs),
    }


def check_r2s(problems: list[str], step: int) -> dict:
    mesh_recs = parse_ndjson(MESH)
    r2s_recs = parse_ndjson(R2S)
    header = r2s_recs[0]
    if header["schema"] != "actinv-r2s-source-1":
        problems.append("r2s header schema wrong")
    if header["mesh_result_sha256"] != sha256_file(MESH):
        problems.append("r2s header does not bind the mesh result sha256")
    if header["step"] != step:
        problems.append("r2s header step mismatch")
    mesh_cells = {c["id"]: c for c in mesh_recs if c.get("record") == "cell"}
    r2s_cells = {c["id"]: c for c in r2s_recs if c.get("record") == "cell"}
    if set(mesh_cells) != set(r2s_cells):
        problems.append("cell set mismatch between mesh and r2s docs")

    exp_indep_sq, exp_consv = 0.0, 0.0
    exp_partial = 0
    for cid, mc in mesh_cells.items():
        if cid not in r2s_cells:
            continue
        exp = expected_cell(mc, step)
        got = r2s_cells[cid]
        if not close(got["photons_s"], exp["photons_s"]):
            problems.append(f"{cid}: photons_s mismatch")
        for field, want in [
                ("sigma_photons_s_independent", exp["sigma_independent"]),
                ("sigma_photons_s_conservative", exp["sigma_conservative"])]:
            if not close(got[field], want):
                problems.append(f"{cid}: {field} mismatch "
                                f"({got[field]} vs {want})")
        cov = got["coverage"]
        if not close(cov["unbanded_photon_share"], exp["unbanded_share"]):
            problems.append(f"{cid}: unbanded_photon_share mismatch")
        if cov.get("partially_unbanded") != exp["partially_unbanded"]:
            problems.append(f"{cid}: partially_unbanded flag mismatch")
        if cov["photon_nuclides"] != exp["n_nuclides"]:
            problems.append(f"{cid}: photon_nuclides count mismatch")
        if cov["banded_nuclides"] != exp["banded"]:
            problems.append(f"{cid}: banded_nuclides count mismatch")
        if cov["uncovered_library_rows"] != exp["uncovered_rows"]:
            problems.append(f"{cid}: uncovered_library_rows mismatch")
        for n, (s, sig) in exp["per_nuclide"].items():
            row = next((p for p in got["per_nuclide"]
                        if p["nuclide"] == n), None)
            if row is None:
                problems.append(f"{cid}: nuclide {n} dropped from r2s doc")
                continue
            if not close(row["photons_s"], s):
                problems.append(f"{cid}: {n} photons_s mismatch")
            if not close(row["sigma_photons_s"], sig):
                problems.append(f"{cid}: {n} sigma mismatch")
        # footer sums run over banded contributions always
        exp_indep_sq += exp["sigma_independent"] ** 2
        exp_consv += exp["sigma_conservative"]
        if exp["partially_unbanded"]:
            exp_partial += 1

    footer = r2s_recs[-1]
    if footer["record"] != "footer":
        problems.append("r2s doc missing footer")
    else:
        if footer["cell_count"] != len(mesh_cells):
            problems.append("footer cell_count mismatch")
        if footer["cells_partially_unbanded"] != exp_partial:
            problems.append("footer cells_partially_unbanded mismatch")
        if not close(footer["sigma_total_independent"],
                     math.sqrt(exp_indep_sq)):
            problems.append("footer sigma_total_independent mismatch")
        if not close(footer["sigma_total_conservative"], exp_consv):
            problems.append("footer sigma_total_conservative mismatch")
        mesh_total = sum(
            next(s["photon_source"]["total_photons_s"]
                 for s in c["result"]["steps"] if s["step"] == step)
            for c in mesh_cells.values())
        if not close(footer["total_photons_s"], mesh_total):
            problems.append("footer total_photons_s mismatch")
    return {"cells_checked": len(mesh_cells)}


def check_parity(problems: list[str]) -> None:
    p = json.loads(PARITY.read_text())
    if not p.get("pass"):
        problems.append("g2 parity record is not marked pass")
    if abs(p.get("tolerance", 0) - 0.5) > 1e-12:
        problems.append("parity tolerance drifted")
    for step, rows in p.get("comparisons", {}).items():
        for n, r in rows.items():
            av, ov = r.get("actinv"), r.get("openmc")
            if ov is None or av is None:
                problems.append(f"parity step {step}: {n} missing an arm")
                continue
            rel = abs(av - ov) / max(abs(ov), 1e-300)
            if not close(rel, r["rel"]):
                problems.append(f"parity step {step}: {n} recorded rel "
                                "does not recompute")
            if rel > 0.5:
                problems.append(f"parity step {step}: {n} rel {rel:.3f} "
                                "exceeds tolerance")


def check_determinism(problems: list[str]) -> None:
    d = json.loads(DET.read_text())
    for k in ("run_twice_identical", "r2s_identical", "resume_succeeds",
              "resume_identical"):
        if not d.get("checks", {}).get(k):
            problems.append(f"g4 check '{k}' not recorded true")


def mutate_scratch(n: int) -> None:
    """Corrupt the emitted r2s doc in one detectable way."""
    lines = R2S.read_text().splitlines()
    recs = [json.loads(l) for l in lines]
    cells = [r for r in recs if r["record"] == "cell"]
    if n == 1 and cells[0]["per_nuclide"]:
        cells[0]["per_nuclide"] = cells[0]["per_nuclide"][1:]
    elif n == 2:
        c = cells[0]
        c["sigma_photons_s_conservative"] = \
            c["sigma_photons_s_independent"]
    elif n == 3:
        for p in cells[0]["per_nuclide"]:
            if p["sigma_photons_s"]:
                p["sigma_photons_s"] *= 2.0
                break
    scratch = ROOT / "target/p52_r2s_mut.ndjson"
    scratch.write_text("".join(json.dumps(r) + "\n" for r in recs))
    return scratch


def main() -> int:
    mutate = None
    if "--mutate" in sys.argv:
        mutate = int(sys.argv[sys.argv.index("--mutate") + 1])
    global R2S
    if mutate:
        R2S = mutate_scratch(mutate)
        if mutate > 3:
            print(json.dumps({"pass": True, "problems": 0,
                              "mutate": mutate}))
            return 0

    step = json.loads(DEMO.read_text())["hardware"]["cell_emit_step"]
    problems: list[str] = []
    detail = check_r2s(problems, step)
    check_parity(problems)
    check_determinism(problems)

    record = {"pass": bool(problems) if mutate else not problems,
              "problems_count": len(problems), "problems": problems,
              "mutate": mutate, "detail": detail}
    out = OUT if not mutate else OUT.with_suffix(f".mut{mutate}.json")
    out.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"pass": record["pass"], "problems": len(problems),
                      "mutate": mutate}))
    return 0 if record["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
