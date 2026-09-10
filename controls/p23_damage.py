#!/usr/bin/env python3
"""P23 G3 producer: damage-observables battery.

Independent control: builds synthetic actinv-damage-table-1 tables and synthetic
ENDF-6 mini-corpus files itself, folds group damage-energy cross sections into
NRT dpa with its own arithmetic (no ACTINV production, audit or scoring module),
and re-collapses MF=3/MT=444 sections with its own lethargy integrator. ACTINV
results are compared at 1e-9 relative -- the fold is a plain double sum, so this
bound is conservative.

Amendment A applies: no local evaluation library carries MT=444, so the
mini-corpus is synthetic (declared interpolation laws, persisted under
results/g3_p23_corpus/ for the independent checker) plus a real TENDL-2025
subset that must honestly report zero coverage.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results/g3_p23_damage.json"
CORPUS = ROOT / "results/g3_p23_corpus"
ACTINV = Path(os.environ.get("ACTINV_BIN", ROOT / "target/release/actinv"))
EXAMPLE = ROOT / "examples/fns_fe_5min.json"
TENDL_N = Path(os.environ.get("TENDL_2025_N", Path.home() / "nuclear-data/tendl-2025/files/n"))
PYTHON_LIBRARY = ROOT / "python/target/release/libactinv.so"
GROUPS_JSON = ROOT / "crates/actinv-data/data/fispact_709_groups.json"
TOL = 1e-9
NA = 6.02214076e23

# Independent copy of the natural-abundance and isotopic-mass table the control needs.
ABUNDANCE = {
    "Fe": {54: (0.05845, 53.939608189), 56: (0.91754, 55.934935537),
           57: (0.02119, 56.93539195), 58: (0.00282, 57.933273575)},
    "Co": {59: (1.0, 58.9331950)},
}


def molar_mass(symbol: str) -> float:
    return sum(f * m for f, m in ABUNDANCE[symbol].values())


def atoms_of(symbol: str, mass_fraction: float, mass_g: float = 1.0) -> float:
    return mass_fraction * mass_g / molar_mass(symbol) * NA


def close(observed: float, expected: float, tol: float = TOL) -> bool:
    if not (math.isfinite(observed) and math.isfinite(expected)):
        return False
    return abs(observed - expected) <= tol * max(abs(expected), 1e-300)


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_sha256(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def normalized(result: dict) -> dict:
    """Same normalization as the G0 battery: strip timing, pin entry points."""
    value = dict(result)
    value.pop("ms", None)
    if "entry_point" in value:
        value["entry_point"] = "normalized"
    if isinstance(value.get("certificate"), dict):
        value["certificate"] = dict(value["certificate"])
        value["certificate"]["entry_point"] = "normalized"
    return value


def run_actinv(spec: dict, out: Path) -> tuple[dict | None, str, int]:
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump(spec, handle)
        spec_path = handle.name
    try:
        completed = subprocess.run(
            [str(ACTINV), "run", spec_path, str(out)],
            capture_output=True, text=True, timeout=600,
        )
        if completed.returncode != 0:
            return None, completed.stderr.strip(), completed.returncode
        return json.loads(out.read_text()), "", 0
    finally:
        Path(spec_path).unlink(missing_ok=True)


def library_bounds() -> np.ndarray:
    """Activation-library group boundaries (ascending) read straight from the npz."""
    spec = json.loads(EXAMPLE.read_text())
    npz = ROOT / spec["library"]["path"]
    import io
    with zipfile.ZipFile(npz) as zf:
        return np.load(io.BytesIO(zf.read("bounds.npy")))


def make_table(path: Path, targets: dict, boundaries) -> str:
    table = {
        "format": "actinv-damage-table-1",
        "source": {"citation": "P23 G3 control table", "edition": "synthetic", "url": "local"},
        "projectile": "neutron",
        "group_structure": "fispact-709",
        "boundaries_eV": list(boundaries),
        "units": "damage_energy_barn_eV_per_group",
        "targets": targets,
    }
    path.write_text(json.dumps(table))
    return sha256_of(path)


def damage_spec(table_path: Path, table_sha: str) -> dict:
    spec = json.loads(EXAMPLE.read_text())
    spec["title"] = "p23-g3 damage control"
    spec["library"]["path"] = str(ROOT / spec["library"]["path"])
    for role in ("primary", "fallback"):
        if spec["decay"].get(role):
            spec["decay"][role] = str(ROOT / spec["decay"][role])
    spec["schedule"] = [{"dt": "300.0 s", "flux": 1.0}]
    spec["damage"] = {
        "table": {"path": str(table_path), "sha256": table_sha},
        "displacement_energy_eV": {"Fe": 40.0},
    }
    spec["options"]["outputs"] = ["damage", "ledger", "certificate"]
    return spec


# ---- synthetic ENDF writer (same minimal grammar the production parser consumes)

def _rec(vals, mat, mf, mt, seq):
    return "".join(f"{v:>11}" for v in vals) + f"{mat:>4}{mf:>2}{mt:>3}{seq:>5}"


def _send(mat, mf):
    return _rec([""] * 6, mat, mf, 0, 99999)


def _mf1(za, awr, mat, liso=0, temp=293.6):
    return [
        _rec([str(za), f"{awr}", "0", "0", "0", "0"], mat, 1, 451, 1),
        _rec(["0", "0", "0", str(liso), "0", "0"], mat, 1, 451, 2),
        _rec(["0.999", "2e8", "1", "0", "10", "2025"], mat, 1, 451, 3),
        _rec([f"{temp}", "0", "0", "0", "0", "0"], mat, 1, 451, 4),
        _send(mat, 1),
    ]


def _mf3_444(za, awr, mat, xys, law):
    n = len(xys)
    lines = [
        _rec([str(za), f"{awr}", "0", "0", "0", "0"], mat, 3, 444, 1),
        _rec(["0", "0", "0", "0", "1", str(n)], mat, 3, 444, 2),
        _rec([str(n), str(law), "", "", "", ""], mat, 3, 444, 3),
    ]
    seq = 4
    for i in range(0, n, 3):
        fields = []
        for x, y in xys[i : i + 3]:
            fields += [f"{x:11.5E}", f"{y:11.5E}"]
        fields += [""] * (6 - len(fields))
        lines.append(_rec(fields, mat, 3, 444, seq))
        seq += 1
    lines.append(_send(mat, 3))
    return lines


def _close_tape(mat):
    return [_rec([""] * 6, mat, 0, 0, 0), _rec([""] * 6, -1, 0, 0, 0)]


def write_endf(path: Path, za, awr, mat, xys=None, law=2):
    lines = _mf1(za, awr, mat)
    if xys is not None:
        lines += _mf3_444(za, awr, mat, xys, law)
    lines += _close_tape(mat)
    path.write_text("\n".join(lines) + "\n")


# ---- independent lethargy collapse of a TAB1 section (ENDF laws 1,2,3,5 analytic)

def lethargy_segment(law: int, x1: float, x2: float, y1: float, y2: float,
                     a: float, b: float) -> float:
    r = (b - a) / a
    log_ratio = math.log1p(r)
    if law == 1:
        return y1 * log_ratio
    if law == 2:
        slope = (y2 - y1) / (x2 - x1)
        ya = y1 + slope * (a - x1)
        return ya * log_ratio + slope * a * (r - log_ratio)
    if law == 3:
        span = math.log1p((x2 - x1) / x1)
        ya = y1 + math.log1p((a - x1) / x1) / span * (y2 - y1)
        yb = y1 + math.log1p((b - x1) / x1) / span * (y2 - y1)
        return 0.5 * (ya + yb) * log_ratio
    if law == 5:
        power = math.log(y2 / y1) / math.log(x2 / x1)
        ya = y1 * math.exp(power * math.log(a / x1))
        return ya * ((b / a) ** power - 1.0) / power
    raise ValueError(f"unsupported INT={law}")


def collapse_row(xys, law: int, boundaries) -> list[float]:
    row = []
    for lo, hi in zip(boundaries[:-1], boundaries[1:]):
        a = max(lo, xys[0][0])
        b = min(hi, xys[-1][0])
        total = 0.0
        if b > a:
            for (x1, y1), (x2, y2) in zip(xys[:-1], xys[1:]):
                if x2 <= a or x1 >= b or x2 <= x1:
                    continue
                total += lethargy_segment(law, x1, x2, y1, y2, max(a, x1), min(b, x2))
        row.append(total / math.log(hi / lo))
    return row


def main() -> int:
    checks: dict[str, bool] = {}
    details: dict[str, object] = {}
    evidence: dict = {"corpus": {}, "tables": {}}

    def record(name: str, ok: bool, detail):
        checks[name] = bool(ok)
        details[name] = detail

    bounds = library_bounds()
    groups = len(bounds) - 1
    spec0 = json.loads(EXAMPLE.read_text())
    flux = np.asarray(spec0["spectrum"]["flux_per_group"], dtype=float)
    if spec0["spectrum"].get("descending"):
        flux = flux[::-1]
    # spec.flux_ascending() rescales to spectrum.total when set; mirror that
    if spec0["spectrum"].get("total"):
        flux = flux * (spec0["spectrum"]["total"] / flux.sum())
    total_flux = float(flux.sum())

    workdir = Path(tempfile.mkdtemp(prefix="p23-g3-"))

    # ---- mono-group-equivalent closed form: constant element row, pure Fe
    table_path = workdir / "const_fe.json"
    sha = make_table(table_path, {"Fe": [200.0] * groups}, bounds)
    spec = damage_spec(table_path, sha)
    result, err, code = run_actinv(spec, workdir / "const_out.json")
    record("table_runs", result is not None, {"stderr": err})
    if result is not None:
        damage = result["steps"][0]["damage"]
        atoms_fe = atoms_of("Fe", 1.0)
        energy_rate = 200.0 * 1e-24 * total_flux * atoms_fe
        dpa_rate = 0.8 * (energy_rate / atoms_fe) / (2.0 * 40.0)
        record(
            "constant_row_closed_form",
            close(damage["damage_energy_eV_per_g_s"], energy_rate)
            and close(damage["dpa_rate_per_s"], dpa_rate)
            and close(damage["dpa"], dpa_rate * 300.0)
            and close(damage["covered_atom_fraction"], 1.0),
            {
                "energy_rate": [damage["damage_energy_eV_per_g_s"], energy_rate],
                "dpa_rate": [damage["dpa_rate_per_s"], dpa_rate],
                "dpa": [damage["dpa"], dpa_rate * 300.0],
            },
        )
        evidence["tables"]["constant_fe"] = {
            "sha256": sha, "sigma_barn_eV": 200.0, "ed_eV": 40.0,
        }

    # ---- nuclide row over three flux-bearing groups only
    hot = np.argsort(flux)[-3:]
    sparse = np.zeros(groups)
    for g in hot:
        sparse[g] = 7.0 + 3.0 * g
    table_path = workdir / "sparse_fe56.json"
    sha = make_table(table_path, {"Fe56": sparse.tolist()}, bounds)
    spec = damage_spec(table_path, sha)
    result, err, code = run_actinv(spec, workdir / "sparse_out.json")
    atoms_fe56 = atoms_of("Fe", 1.0) * ABUNDANCE["Fe"][56][0]
    energy_rate = float((sparse * flux).sum()) * 1e-24 * atoms_fe56
    dpa_rate = 0.8 * (energy_rate / atoms_fe56) / (2.0 * 40.0)
    record(
        "nuclide_row_three_group_fold",
        result is not None
        and close(result["steps"][0]["damage"]["damage_energy_eV_per_g_s"], energy_rate)
        and close(result["steps"][0]["damage"]["elements"]["Fe"]["dpa_rate_per_s"], dpa_rate),
        {
            "energy_rate": [
                result["steps"][0]["damage"]["damage_energy_eV_per_g_s"] if result else None,
                energy_rate,
            ],
            "err": err,
        },
    )
    if result is not None:
        record(
            "partial_nuclide_coverage_honest",
            close(result["steps"][0]["damage"]["covered_atom_fraction"],
                  ABUNDANCE["Fe"][56][0])
            and set(result["ledger"]["damage"]["uncovered_targets"]) == {"Fe54", "Fe57", "Fe58"},
            {"uncovered": result["ledger"]["damage"]["uncovered_targets"],
             "fraction": result["steps"][0]["damage"]["covered_atom_fraction"]},
        )

    # ---- element row covers what nuclide rows leave: precedence check
    table_path = workdir / "mixed_fe.json"
    nuclide_row = np.zeros(groups); nuclide_row[hot[0]] = 100.0
    element_row = np.zeros(groups); element_row[hot[0]] = 200.0
    sha = make_table(
        table_path,
        {"Fe56": nuclide_row.tolist(), "Fe": element_row.tolist()},
        bounds,
    )
    spec = damage_spec(table_path, sha)
    result, err, code = run_actinv(spec, workdir / "mixed_out.json")
    a56 = atoms_of("Fe", 1.0) * ABUNDANCE["Fe"][56][0]
    a_rest = atoms_of("Fe", 1.0) - a56
    expected_energy = flux[hot[0]] * 1e-24 * (100.0 * a56 + 200.0 * a_rest)
    record(
        "element_row_covers_nuclide_remainder",
        result is not None
        and close(result["steps"][0]["damage"]["damage_energy_eV_per_g_s"], expected_energy),
        {
            "energy_rate": [
                result["steps"][0]["damage"]["damage_energy_eV_per_g_s"] if result else None,
                expected_energy,
            ],
            "err": err,
        },
    )

    # ---- uncovered target named; require_complete fails closed
    table_path = workdir / "fe_only.json"
    sha = make_table(table_path, {"Fe": [200.0] * groups}, bounds)
    spec = damage_spec(table_path, sha)
    spec["material"]["composition"] = {"FE": 95.0, "CO": 5.0}
    result, err, code = run_actinv(spec, workdir / "fe_co_out.json")
    record(
        "uncovered_targets_named",
        result is not None
        and result["ledger"]["damage"]["uncovered_targets"] == ["Co59"]
        and result["steps"][0]["damage"]["covered_atom_fraction"] < 1.0,
        {"uncovered": result["ledger"]["damage"]["uncovered_targets"] if result else err},
    )
    spec["damage"]["require_complete"] = True
    result, err, code = run_actinv(spec, workdir / "rc_out.json")
    record(
        "require_complete_fails_closed",
        code != 0 and "Co59" in err and "coverage" in err,
        {"code": code, "stderr": err[:300]},
    )

    # ---- missing displacement energy names the element; nuclide key rejected
    spec = damage_spec(table_path, sha)
    spec["damage"]["displacement_energy_eV"] = {}
    result, err, code = run_actinv(spec, workdir / "noed_out.json")
    record(
        "missing_displacement_energy_named",
        code != 0 and "Fe" in err and "displacement_energy_eV" in err,
        {"code": code, "stderr": err[:300]},
    )
    spec = damage_spec(table_path, sha)
    spec["damage"]["displacement_energy_eV"] = {"Fe56": 40.0}
    result, err, code = run_actinv(spec, workdir / "ednuclide_out.json")
    record(
        "nuclide_displacement_key_rejected",
        code != 0 and "not an element" in err,
        {"code": code, "stderr": err[:300]},
    )

    # ---- fed reservoir atoms displace too (cross-feature: G1 feed -> G3 fold)
    fed = damage_spec(table_path, sha)
    fed["schedule"] = [{"dt": "300.0 s", "flux": 1.0, "feed": {"Fe56": 1.0e18}}]
    result_fed, err, code = run_actinv(fed, workdir / "fed_out.json")
    record(
        "fed_reservoir_atoms_displace",
        result_fed is not None
        and close(
            result_fed["steps"][0]["damage"]["elements"]["Fe"]["atoms_per_g"],
            atoms_of("Fe", 1.0) + 3.0e20,
        ),
        {
            "atoms": result_fed["steps"][0]["damage"]["elements"]["Fe"]["atoms_per_g"]
            if result_fed else err,
            "expected": atoms_of("Fe", 1.0) + 3.0e20,
        },
    )

    # ---- coupled mode uses evolved target states
    spec = damage_spec(table_path, sha)
    spec["options"]["mode"] = "coupled"
    result, err, code = run_actinv(spec, workdir / "coupled_out.json")
    record(
        "coupled_mode_damage_block",
        result is not None
        and result["steps"][0]["damage"]["dpa_rate_per_s"] > 0.0
        and close(
            result["steps"][0]["damage"]["elements"]["Fe"]["atoms_per_g"],
            atoms_of("Fe", 1.0), 1e-6,
        ),
        {
            "err": err,
            "dpa_rate": result["steps"][0]["damage"]["dpa_rate_per_s"] if result else None,
        },
    )

    # ---- strict failures: noncanonical key, negative row, bad hash, bare token
    table_path = workdir / "noncanonical.json"
    sha = make_table(table_path, {"fe56": [200.0] * groups}, bounds)
    spec = damage_spec(table_path, sha)
    _, err, code = run_actinv(spec, workdir / "nc_out.json")
    record("noncanonical_target_rejected", code != 0 and "canonical" in err,
           {"code": code, "stderr": err[:300]})
    table_path = workdir / "negative.json"
    row = [200.0] * groups
    row[100] = -1.0
    sha = make_table(table_path, {"Fe": row}, bounds)
    spec = damage_spec(table_path, sha)
    _, err, code = run_actinv(spec, workdir / "neg_out.json")
    record("negative_row_rejected", code != 0 and "negative" in err,
           {"code": code, "stderr": err[:300]})
    spec = damage_spec(table_path, "0" * 64)
    _, err, code = run_actinv(spec, workdir / "hash_out.json")
    record("sha_mismatch_rejected", code != 0 and "sha-256" in err.lower(),
           {"code": code, "stderr": err[:300]})
    spec = damage_spec(table_path, sha)
    del spec["damage"]
    spec["options"]["outputs"] = ["damage"]
    _, err, code = run_actinv(spec, workdir / "tok_out.json")
    record("outputs_token_requires_section", code != 0 and "damage section" in err,
           {"code": code, "stderr": err[:300]})

    # ---- build-damage on the persisted synthetic mini-corpus; independent re-collapse
    CORPUS.mkdir(exist_ok=True)
    corpus_files = {
        "n-Fe056.tendl": dict(za=26056, awr=55.45, mat=2631,
                              xys=[(1e-5, 10.0), (1e3, 40.0), (1e5, 50.0),
                                   (1e6, 80.0), (2e7, 100.0)], law=2),
        "n-Ni058.tendl": dict(za=28058, awr=57.9, mat=2831,
                              xys=[(1e-5, 5.0), (1e2, 25.0), (1e5, 60.0), (2e7, 90.0)], law=5),
        "n-Co059.tendl": dict(za=27059, awr=58.93, mat=2731, xys=None, law=2),
    }
    for name, conf in corpus_files.items():
        write_endf(CORPUS / name, conf["za"], conf["awr"], conf["mat"],
                   conf.get("xys"), conf["law"])
        evidence["corpus"][name] = {"sha256": sha256_of(CORPUS / name)}
    built = ROOT / "results/g3_p23_damage_table.json"
    completed = subprocess.run(
        [str(ACTINV), "build-damage", str(CORPUS), str(built), "--projectile", "neutron"],
        capture_output=True, text=True, timeout=600,
    )
    record("build_damage_runs", completed.returncode == 0, completed.stderr[:300])
    if completed.returncode == 0:
        table = json.loads(built.read_text())
        evidence["built_table_sha256"] = sha256_of(built)
        expected_rows = {
            "Fe56": collapse_row(corpus_files["n-Fe056.tendl"]["xys"], 2, bounds),
            "Ni58": collapse_row(corpus_files["n-Ni058.tendl"]["xys"], 5, bounds),
        }
        rows_ok = set(table["targets"]) == set(expected_rows)
        diffs = {}
        if rows_ok:
            for name, expected in expected_rows.items():
                got = np.asarray(table["targets"][name])
                exp = np.asarray(expected)
                mask = np.abs(exp) > 0
                rel = np.abs(got - exp)[mask] / np.abs(exp)[mask]
                diffs[name] = float(rel.max()) if rel.size else 0.0
                rows_ok &= bool(rel.max(initial=0.0) <= TOL)
                rows_ok &= bool(np.allclose(got[~mask], 0.0))
        record("build_damage_recollapse", rows_ok, {"max_relative_diff": diffs})
        record(
            "build_damage_provenance",
            table["uncovered"] == ["Co59"]
            and {f["path"]: f["sha256"] for f in table["files"]}
            == {n: evidence["corpus"][n]["sha256"] for n in corpus_files},
            {"uncovered": table["uncovered"], "files": table["files"]},
        )

    # ---- real TENDL-2025 subset: honest zero coverage, complete provenance
    real_dir = workdir / "tendl_subset"
    real_dir.mkdir()
    real_files = []
    if TENDL_N.is_dir():
        for name in ["n-Fe056.tendl", "n-Co059.tendl", "n-Ni058.tendl"]:
            src = TENDL_N / name
            if src.exists():
                (real_dir / name).write_bytes(src.read_bytes())
                real_files.append(name)
    built = workdir / "tendl_damage.json"
    completed = subprocess.run(
        [str(ACTINV), "build-damage", str(real_dir), str(built), "--projectile", "neutron"],
        capture_output=True, text=True, timeout=600,
    )
    ok = completed.returncode == 0 and real_files
    detail = {"stderr": completed.stderr[:300], "files": real_files}
    if ok:
        table = json.loads(built.read_text())
        # canonical nuclide names from the TENDL filename (n-Fe056.tendl -> Fe56)
        expected_names = sorted(
            f"{''.join(ch for ch in n.split('-')[1].split('.')[0] if ch.isalpha())}"
            f"{int(''.join(ch for ch in n.split('-')[1].split('.')[0] if ch.isdigit()))}"
            for n in real_files
        )
        ok = (
            table["targets"] == {}
            and sorted(table["uncovered"]) == expected_names
            and {f["path"]: f["sha256"] for f in table["files"]}
            == {n: sha256_of(real_dir / n) for n in real_files}
        )
        detail.update(uncovered=table["uncovered"], expected=expected_names)
    record("tendl_2025_uncovered_honest", ok, detail)
    evidence["tendl_subset"] = {n: sha256_of(real_dir / n) for n in real_files}

    # ---- mesh parity: one cell carrying the same spectrum vs the single run
    single = json.loads((workdir / "const_out.json").read_text())
    table_path = workdir / "const_fe.json"
    sha = sha256_of(table_path)
    # the mesh cell path treats the imported per-group flux as absolute (no total rescale);
    # to compare damage blocks exactly, give the cell the already-rescaled spectrum
    values = flux[::-1] if spec0["spectrum"].get("descending") else flux
    fluxes = workdir / "fluxes"
    fluxes.write_text(
        "\n".join(" ".join(str(v) for v in values[i : i + 6])
                  for i in range(0, len(values), 6))
        + "\n1.0\np23 g3 cell\n",
        encoding="utf-8",
    )
    canonical_flux = workdir / "flux.ndjson"
    completed = subprocess.run(
        [str(ACTINV), "import-flux", "fispact", str(fluxes), str(canonical_flux),
         "--groups", str(GROUPS_JSON)],
        capture_output=True, text=True, timeout=120,
    )
    ok = completed.returncode == 0
    detail = {"stderr": completed.stderr[:300]}
    if ok:
        mesh_spec = {
            "spec": "actinv-mesh-spec-1",
            "title": "p23-g3 mesh parity",
            "library": {"path": str(ROOT / spec0["library"]["path"]),
                        "sha256": spec0["library"]["sha256"]},
            "decay": {r: str(ROOT / spec0["decay"][r]) for r in ("primary", "fallback")
                      if spec0["decay"].get(r)},
            "material": spec0["material"],
            "flux": {"path": str(canonical_flux), "sha256": sha256_of(canonical_flux)},
            "schedule": [{"dt": "300.0 s", "flux": 1.0}],
            "options": {"mode": "auto", "outputs": ["damage", "ledger"]},
            "damage": {"table": {"path": str(table_path), "sha256": sha},
                       "displacement_energy_eV": {"Fe": 40.0}},
        }
        mesh_spec_path = workdir / "mesh_spec.json"
        mesh_spec_path.write_text(json.dumps(mesh_spec))
        mesh_out = workdir / "mesh_out.ndjson"
        completed = subprocess.run(
            [str(ACTINV), "mesh", str(mesh_spec_path), str(mesh_out)],
            capture_output=True, text=True, timeout=600,
        )
        ok = completed.returncode == 0
        detail["stderr"] = completed.stderr[:300]
        if ok:
            cells = [
                json.loads(line)["result"]
                for line in mesh_out.read_text().splitlines()
                if json.loads(line).get("record") == "cell"
            ]
            ok = len(cells) == 1 and cells[0]["steps"][0]["damage"] == single["steps"][0]["damage"]
            detail["equal"] = ok
    record("mesh_single_cell_damage_parity", ok, detail)

    # ---- Python object API parity
    try:
        module_spec = importlib.util.spec_from_file_location("actinv", PYTHON_LIBRARY)
        module = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(module)
        spec = damage_spec(table_path, sha)
        py = json.loads(module.run(json.dumps(spec)))
        record(
            "python_damage_parity",
            py["steps"][0]["damage"] == single["steps"][0]["damage"],
            {},
        )
    except Exception as exc:
        record("python_damage_parity", False, {"error": str(exc)})

    # ---- identity: no damage section -> normalized result identical to the G0 baseline.
    # The battery resolved spec paths to absolute and serialized sort_keys; replicate both.
    identity_spec = json.loads(EXAMPLE.read_text())
    identity_spec["library"]["path"] = str(ROOT / identity_spec["library"]["path"])
    for role in ("primary", "fallback"):
        if identity_spec["decay"].get(role):
            identity_spec["decay"][role] = str(ROOT / identity_spec["decay"][role])
    spec_path = workdir / "identity_spec.json"
    spec_path.write_text(json.dumps(identity_spec, sort_keys=True) + "\n")
    completed = subprocess.run(
        [str(ACTINV), "run", str(spec_path), str(workdir / "identity_out.json")],
        cwd=ROOT, capture_output=True, text=True, timeout=600,
    )
    ok = completed.returncode == 0
    detail = {"stderr": completed.stderr[:300]}
    if ok:
        result = json.loads((workdir / "identity_out.json").read_text())
        baseline = json.loads(
            (ROOT / "results/g0_p23_identity_baseline.json").read_text()
        )["normalized_result_sha256"]["cli_cold"]
        observed = canonical_sha256(normalized(result))
        ok = observed == baseline
        detail.update(observed=observed, baseline=baseline)
    record("no_damage_identity", ok, detail)

    payload = {
        "schema": "actinv-p23-g3-damage-1",
        "gate": "G3",
        "phase": "P23",
        "binary": str(ACTINV),
        "tolerance": TOL,
        "checks": checks,
        "details": details,
        "evidence": evidence,
        "pass": len(failed := [n for n, ok in checks.items() if not ok]) == 0,
    }
    RESULT.write_text(json.dumps(payload, indent=1))
    failed = [name for name, check in checks.items() if not check]
    print(f"{len(checks) - len(failed)}/{len(checks)} checks pass")
    if failed:
        print("failed:", failed)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
