#!/usr/bin/env python3
"""P184 checker (protocols/ACTINV-P184_PROTOCOL.md). It reads IRDFF-II, the TENDL-2025 sources and both sidecars with
its own code (nothing from p184_covariance.py) and recomputes every replaced matrix.

  G0  The protocol's SHA-256 is in protocols/protocol_hash.txt.
  G1  Pins, the 2,850 inputs against the v1.2.0 index, the build (registration commit, clean tree, bounded, exit 0).
  G2  The checker's own selection equals the record's.
  G3  Each replaced reaction holds exactly the recomputed LB=5 matrix and LB=8 blocks; LB=5 positive semidefinite.
  G4  Every other v1.2.0 component is in the new sidecar bit for bit, and nothing else is; index targets unchanged.
  G5  Both observation runs exit 0 and report every response at every step (values recorded, not gated).
  G6  CI green on the evidence commit (checked after the push, not here).

  python3 controls/check_p184.py              check results/p184_record.json and target/p184-work, write the verdict
  python3 controls/check_p184.py --no-write   recompute and compare with the committed verdict
  python3 controls/check_p184.py --selftest
"""
import argparse
import hashlib
import json
import subprocess
import sys
import zipfile
from collections import Counter
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
PROTOCOL = ROOT / "protocols" / "ACTINV-P184_PROTOCOL.md"
RECORD = ROOT / "results" / "p184_record.json"
VERDICT = ROOT / "results" / "p184_verdict.json"
WORK = ROOT / "target" / "p184-work"
SCHEMA = "actinv-p184-verdict-1"
PINS = {
    "irdff_zip": "225b25787f6d9b54a9c28ebf075ccc715f69358be181fed27d5cc315cf8744db",
    "irdff_endf": "162b1de760c6b297dc36567c7a1724aeabbe2ab9c02b5323a4bef604ae557554",
    "activation": "ec4c72bf598dc8ad3d533d9cfafdcf493e2d1f949a3e4db6251495659b68cc44",
    "cov_v120": "c19dec86b44ad5d90b66c9ab94d53e18641a1d354a89402a4da7986b6c530cde",
}
RESPONSES = ["Mn56", "Mn54", "Fe53", "Cr51", "Mn57"]
REL_TOL, ABS_TOL, EIG_TOL = 5e-7, 1e-12, 1e-6
ELEMENTS = ("n H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Br Kr Rb "
            "Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta "
            "W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm").split()


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


# ------------------------------------------------------------------------------------------------ ENDF-6, read-only
def real(field):
    field = field.strip().replace(" ", "")
    if not field:
        return 0.0
    if "e" in field or "E" in field:
        return float(field)
    cut = max(field.rfind("+"), field.rfind("-"))
    return float(field[:cut] + "e" + field[cut:]) if cut > 0 else float(field)


def integer(field):
    return int(field) if field.strip() else 0


class Tape:
    """Lines of one ENDF tape grouped by (MAT, MF, MT), SEND lines dropped."""

    def __init__(self, path):
        self.sections = {}
        with open(path, errors="replace") as f:
            for line in f:
                if len(line) < 75:
                    continue
                try:
                    mat, mf, mt = int(line[66:70]), int(line[70:72]), int(line[72:75])
                except ValueError:
                    continue
                if mt > 0:
                    self.sections.setdefault((mat, mf, mt), []).append(line)

    def keys(self, mf, mat=None):
        return sorted(k for k in self.sections if k[1] == mf and (mat is None or k[0] == mat))

    def find(self, mf, mt, mat=None):
        hits = [k for k in self.keys(mf, mat) if k[2] == mt]
        return self.sections[hits[0]] if len(hits) == 1 else None


class Reader:
    def __init__(self, lines):
        self.lines, self.pos = lines, 0

    def cont(self):
        line = self.lines[self.pos]
        self.pos += 1
        return (real(line[0:11]), real(line[11:22]), integer(line[22:33]), integer(line[33:44]),
                integer(line[44:55]), integer(line[55:66]))

    def numbers(self, n):
        out = []
        while len(out) < n:
            line = self.lines[self.pos]
            self.pos += 1
            out += [real(line[11 * k:11 * k + 11]) for k in range(6)]
        return out[:n]

    def done(self):
        return self.pos == len(self.lines)


def tab1(lines):
    r = Reader(lines)
    r.cont()
    _, _, _, _, nr, npts = r.cont()
    r.numbers(2 * nr)
    xy = np.array(r.numbers(2 * npts)).reshape(-1, 2)
    return xy[:, 0], xy[:, 1]


def mf33(lines):
    """{'za', 'mtl', 'subs': [{'mat1', 'mt1', 'nc', 'blocks': [(l1, lb, ne, values)]}]} of one MF=33 section."""
    r = Reader(lines)
    za, _, _, mtl, _, nl = r.cont()
    subs = []
    for _ in range(nl):
        _, _, mat1, mt1, nc, ni = r.cont()
        if nc:
            return {"za": za, "mtl": mtl, "subs": None}
        blocks = []
        for _ in range(ni):
            _, _, l1, lb, nt, ne = r.cont()
            blocks.append((l1, lb, ne, r.numbers(nt)))
        subs.append({"mat1": mat1, "mt1": mt1, "blocks": blocks})
    return {"za": za, "mtl": mtl, "subs": subs if r.done() else None}


def irdff_self(section, mt):
    """(grid, dense C, [(lb8 energies, F)]) when the section is one self-covariance LB=5 LS=1 (+ LB=8), else None."""
    if section["subs"] is None or section["mtl"] or len(section["subs"]) != 1:
        return None
    sub = section["subs"][0]
    if sub["mat1"] or sub["mt1"] != mt:
        return None
    lb5 = [b for b in sub["blocks"] if b[1] == 5 and b[0] == 1]
    lb8 = [b for b in sub["blocks"] if b[1] == 8 and b[0] == 0]
    if len(lb5) != 1 or len(lb5) + len(lb8) != len(sub["blocks"]):
        return None
    _, _, ne, values = lb5[0]
    grid, m = np.array(values[:ne]), ne - 1
    c = np.zeros((m, m))
    k = ne
    for i in range(m):
        for j in range(i, m):
            c[i, j] = c[j, i] = values[k]
            k += 1
    short = []
    for _, _, npts, values in lb8:
        pairs = np.array(values).reshape(-1, 2)
        short.append((pairs[:, 0], pairs[:-1, 1]))
    return grid, c, short


def nuclide_file(za):
    z, a = divmod(za, 1000)
    return f"n-{ELEMENTS[z]}{a:03d}.tendl"


def library_totals(npz):
    """(bounds, {(ZA, MT): group cross sections}) for the reaction-total rows (ZAP = -1) of ground-state targets."""
    meta = json.loads(Path(npz).with_name(Path(npz).stem + "_index.json").read_text())
    ground = {i: t["za"] for i, t in enumerate(meta["targets"]) if t["liso"] == 0}
    with zipfile.ZipFile(npz) as z:
        rows = np.lib.format.read_array(z.open("rows.npy"))
        bounds = np.lib.format.read_array(z.open("bounds.npy"))
        sig = np.lib.format.read_array(z.open("sig.npy"))
    totals = {(ground[int(t)], int(mt)): sig[k] for k, (t, mt, zap) in enumerate(rows[:, :3])
              if zap == -1 and int(t) in ground}
    return bounds, totals


# ----------------------------------------------------------------------------------------------- the replacement
def antiderivative(x, y, e):
    """Integral of the lin-lin table from its first point to e (constant beyond the last point)."""
    areas = np.concatenate(([0.0], np.cumsum(0.5 * (y[1:] + y[:-1]) * np.diff(x))))
    if e <= x[0]:
        return 0.0
    if e >= x[-1]:
        return float(areas[-1])
    j = int(np.searchsorted(x, e, side="right") - 1)
    h = e - x[j]
    return float(areas[j] + h * y[j] + 0.5 * h * h * (y[j + 1] - y[j]) / (x[j + 1] - x[j]))


def table_range(x, y):
    positive = np.flatnonzero(y > 0)
    return x[0], (x[positive[-1]] if positive.size else x[0])


def averages(x, y, grid, lo, hi):
    out = np.zeros(len(grid) - 1)
    for k in range(len(grid) - 1):
        a, b = max(grid[k], lo), min(grid[k + 1], hi)
        if b > a:
            out[k] = (antiderivative(x, y, b) - antiderivative(x, y, a)) / (b - a)
    return out


def histogram_averages(bounds, sigma, grid, lo, hi):
    """Mean of a group histogram (flat in energy within each group) over each interval of grid clipped to [lo, hi]."""
    cumulative = np.concatenate(([0.0], np.cumsum(sigma * np.diff(bounds))))

    def area(e):
        e = min(max(e, bounds[0]), bounds[-1])
        g = min(int(np.searchsorted(bounds, e, side="right") - 1), len(sigma) - 1)
        return cumulative[g] + sigma[g] * (e - bounds[g])
    out = np.zeros(len(grid) - 1)
    for k in range(len(grid) - 1):
        a, b = max(grid[k], lo), min(grid[k + 1], hi)
        if b > a:
            out[k] = (area(b) - area(a)) / (b - a)
    return out


def expected(irdff_xy, group, tendl_mf3_xy, grid, c_i, short):
    """C and the LB=8 blocks. group = (bounds, sigma) of the library's reaction-total row; TENDL-2025's MF=3 gives
    only the upper end of the range compared."""
    (xi, yi), (bounds, sigma) = irdff_xy, group
    ilo, ihi = table_range(xi, yi)
    nonzero = np.flatnonzero(sigma > 0)
    glo, ghi = (bounds[nonzero[0]], bounds[nonzero[-1] + 1]) if nonzero.size else (bounds[0], bounds[0])
    lo, hi = max(ilo, glo), min(ihi, ghi, table_range(*tendl_mf3_xy)[1])
    si, st = averages(xi, yi, grid, lo, hi), histogram_averages(bounds, sigma, grid, lo, hi)
    safe = np.where(st > 0, st, 1.0)
    r = np.where(st > 0, si / safe, 0.0)
    b = np.where(st > 0, (st - si) / safe, 0.0)
    c = r[:, None] * r[None, :] * c_i + b[:, None] * b[None, :]
    blocks = []
    for energies, f in short:
        k = np.clip(np.searchsorted(grid, 0.5 * (energies[:-1] + energies[1:]), side="right") - 1, 0, len(r) - 1)
        blocks.append((energies, f * r[k] ** 2))
    return c, blocks


def close(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    if a.shape != b.shape:
        return False
    scale = max(float(np.max(np.abs(b))) if b.size else 0.0, 0.0)
    return bool(np.all(np.abs(a - b) <= REL_TOL * scale + ABS_TOL))


# ---------------------------------------------------------------------------------------------------- sidecars
def sidecar(path):
    """Components as dicts with grids and values (NumPy arrays), from the NPY members."""
    with zipfile.ZipFile(path) as z:
        def load(name):
            with z.open(name) as f:
                return np.lib.format.read_array(f)
        comp, offsets, grids, values = (load("components.npy"), load("grid_offsets.npy"), load("grid_values.npy"),
                                        load("values.npy"))
    out = []
    for row in comp:
        target, mt, mt1, lb, kind, rg, cg, off, n = (int(v) for v in row)
        out.append({"target": target, "mt": mt, "mt1": mt1, "lb": lb, "kind": kind,
                    "row_grid": grids[offsets[rg]:offsets[rg + 1]], "column_grid": grids[offsets[cg]:offsets[cg + 1]],
                    "values": values[off:off + n]})
    return out


def fingerprint(c):
    h = hashlib.sha256()
    for key in ("target", "mt", "mt1", "lb", "kind"):
        h.update(str(c[key]).encode() + b"\0")
    for key in ("row_grid", "column_grid", "values"):
        h.update(np.ascontiguousarray(c[key], dtype="<f8").tobytes() + b"\0")
    return h.hexdigest()


# ------------------------------------------------------------------------------------------------------- gates
def selection(irdff, sources, totals):
    """({(za, mt)} replaced, {(za, mt)} left out) by the protocol's rule."""
    replaced, left = set(), set()
    tendl_cache = {}
    for mat, _, mt in irdff.keys(33):
        sec = mf33(irdff.sections[(mat, 33, mt)])
        za = int(round(sec["za"]))
        key = (za, mt)
        path = sources.get(nuclide_file(za)) if za % 1000 else None
        if path is None:
            left.add(key)
            continue
        tape = tendl_cache.setdefault(path, Tape(path))
        if (tape.find(33, mt) is None or irdff_self(sec, mt) is None or irdff.find(3, mt, mat) is None
                or tape.find(3, mt) is None or (za, mt) not in totals):
            left.add(key)
        else:
            replaced.add(key)
    return replaced, left


def g3_g4(record, irdff, sources, library, old, new, old_index, new_index):
    target_of = {(t["za"], t["liso"]): t["target"] for t in old_index["targets"]}
    replaced = {(r["za"], r["mt"]) for r in record["stage"]["replaced"]}
    touched = {(target_of[(za, 0)], mt) for za, mt in replaced}
    g3_problems, eig_min = [], None
    by_target = {}
    for c in new:
        by_target.setdefault(c["target"], []).append(c)
    expected_new = Counter()
    irdff_mat = {}
    for mat, _, mt in irdff.keys(33):
        irdff_mat[(int(round(mf33(irdff.sections[(mat, 33, mt)])["za"])), mt)] = mat
    for za, mt in sorted(replaced):
        target = target_of[(za, 0)]
        mat = irdff_mat[(za, mt)]
        grid, c_i, short = irdff_self(mf33(irdff.sections[(mat, 33, mt)]), mt)
        tape = Tape(sources[nuclide_file(za)])
        bounds, totals = library
        c, blocks = expected(tab1(irdff.find(3, mt, mat)), (bounds, totals[(za, mt)]), tab1(tape.find(3, mt)), grid,
                             c_i, short)
        mine = [x for x in by_target.get(target, []) if x["mt"] == mt or x["mt1"] == mt]
        lb5 = [x for x in mine if x["lb"] == 5]
        lb8 = [x for x in mine if x["lb"] == 8]
        if len(lb5) != 1 or len(lb8) != len(blocks) or len(mine) != 1 + len(blocks):
            g3_problems.append(f"{za} MT={mt}: components {[(x['mt'], x['mt1'], x['lb']) for x in mine]}")
            continue
        x = lb5[0]
        m = len(grid) - 1
        ok = (x["mt"] == x["mt1"] == mt and x["kind"] == 1 and np.array_equal(x["row_grid"], grid)
              and np.array_equal(x["column_grid"], grid) and close(np.asarray(x["values"]).reshape(m, m), c))
        for (energies, f) in blocks:
            hit = [y for y in lb8 if np.array_equal(y["row_grid"], energies)]
            ok = ok and len(hit) == 1 and hit[0]["kind"] == 8 and close(hit[0]["values"], f)
        if not ok:
            g3_problems.append(f"{za} MT={mt}: values differ from the recomputation")
        ev = np.linalg.eigvalsh(np.asarray(x["values"]).reshape(m, m))
        ratio = ev.min() / ev.max() if ev.max() > 0 else 0.0
        eig_min = ratio if eig_min is None else min(eig_min, ratio)
        if ratio < -EIG_TOL:
            g3_problems.append(f"{za} MT={mt}: eigenvalue ratio {ratio:.3e}")
        for y in mine:
            expected_new[fingerprint(y)] += 1
    kept = Counter(fingerprint(c) for c in old if (c["target"], c["mt"]) not in touched
                   and (c["target"], c["mt1"]) not in touched)
    observed = Counter(fingerprint(c) for c in new)
    g4_problems = []
    missing, extra = kept - observed, observed - kept - expected_new
    if missing:
        g4_problems.append(f"{sum(missing.values())} v1.2.0 components missing or changed")
    if extra:
        g4_problems.append(f"{sum(extra.values())} components neither copied nor replaced")
    patched_files = {r["file"] for r in record["stage"]["replaced"]}
    counts = ("mf33_sections", "components", "lb_counts", "mf33_source_sha256")
    for a, b in zip(old_index["targets"], new_index["targets"]):
        strip = (lambda t: {k: v for k, v in t.items() if k not in counts}) if a["file"] in patched_files else dict
        if strip(a) != strip(b) or (a["file"] not in patched_files and "mf33_source_sha256" in b):
            g4_problems.append(f"index target {a['file']} differs")
    if len(old_index["targets"]) != len(new_index["targets"]):
        g4_problems.append("index target counts differ")
    return g3_problems, eig_min, g4_problems, len(kept), len(new)


def observations(record):
    runs, problems, table = record["observe"]["runs"], [], {}
    for name, run in runs.items():
        if run["exit"] != 0:
            problems.append(f"{name}: exit {run['exit']}")
            continue
        out = json.loads(Path(run["output"]).read_text())
        if sha256(run["output"]) != run["output_sha256"]:
            problems.append(f"{name}: output changed since the run")
        table[name] = extract(out, problems, name)
    return problems, table


def extract(out, problems, name):
    """{nuclide: [(time_s, relative standard deviation)]} from the run's uncertainty block."""
    rows = {}
    for step in out.get("steps", []):
        block = step.get("uncertainty") or {}
        for nuclide in RESPONSES:
            entry = (block.get("responses") or {}).get(f"activity:{nuclide}")
            if not isinstance(entry, dict) or entry.get("relative_standard_uncertainty") is None:
                problems.append(f"{name}: no activity:{nuclide} at t={step.get('t_s')}")
                continue
            rows.setdefault(nuclide, []).append((step.get("t_s"), entry["relative_standard_uncertainty"]))
    return rows


def check():
    record = json.loads(RECORD.read_text())
    gates = {}
    digest = sha256(PROTOCOL)
    gates["G0"] = {"pass": digest in (ROOT / "protocols" / "protocol_hash.txt").read_text(), "sha256": digest}

    stage, build = record["stage"], record["build"]
    registration = subprocess.run(["git", "log", "--diff-filter=A", "--format=%H", "--",
                                   "protocols/ACTINV-P184_PROTOCOL.md"], capture_output=True, text=True,
                                  cwd=ROOT).stdout.split()
    old_index = json.loads(Path(stage["cov_index"]["path"]).read_text())
    inputs = WORK / "stage" / "inputs"
    bad_inputs = [t["file"] for t in old_index["targets"] if sha256(inputs / t["file"]) != t["source_sha256"]]
    g1 = {
        "irdff": stage["irdff"]["sha256"] == PINS["irdff_endf"] and sha256(stage["irdff"]["path"]) == PINS["irdff_endf"],
        "pins": record["pins"] == PINS,
        "inputs": not bad_inputs and len(list(inputs.iterdir())) == len(old_index["targets"]) == 2850,
        "build_exit": build["exit"] == 0,
        "registration_commit": bool(registration) and build["git_head"] == registration[-1],
        "clean_tree": build["git_dirty"] == "" and record["observe"]["git_head"] == build["git_head"],
        "bounded": _bounded(build["cgroup"]) and _bounded(record["observe"]["cgroup"]),
        "sidecar_sha256": sha256(record["sidecar"]["path"]) == record["sidecar"]["sha256"] == build["sidecar_sha256"],
        "activation_in_command": PINS["activation"] == sha256(build["command"][3])
        and stage.get("activation", {}).get("sha256") == PINS["activation"],
    }
    gates["G1"] = {"pass": all(g1.values()), **g1, "bad_inputs": bad_inputs[:10]}

    irdff = Tape(stage["irdff"]["path"])
    sources = {p.name: p for p in inputs.iterdir()}
    activation = Path(build["command"][3])
    if sha256(activation) != PINS["activation"]:
        raise SystemExit("check_p184: the activation library is not the pinned file")
    library = library_totals(activation)
    replaced, left = selection(irdff, sources, library[1])
    rec_replaced = {(r["za"], r["mt"]) for r in stage["replaced"]}
    rec_left = {(s["za"], s["mt"]) for s in stage["skipped"]}
    gates["G2"] = {"pass": replaced == rec_replaced and left == rec_left, "replaced": len(replaced),
                   "left_out": len(left), "only_record_replaced": sorted(rec_replaced - replaced),
                   "only_checker_replaced": sorted(replaced - rec_replaced)}

    old_cov = activation.parent.parent / "uncertainty" / "tendl-2025-neutron-709g.cov.npz"
    if sha256(old_cov) != PINS["cov_v120"]:
        raise SystemExit("check_p184: the v1.2.0 sidecar is not the pinned file")
    new_path = Path(record["sidecar"]["path"])
    new_index = json.loads(new_path.with_name("p184.cov_index.json").read_text())
    g3, eig_min, g4, kept, total = g3_g4(record, irdff, sources, library, sidecar(old_cov), sidecar(new_path),
                                         old_index, new_index)
    gates["G3"] = {"pass": not g3 and bool(rec_replaced), "problems": g3[:20], "min_eigenvalue_ratio": eig_min}
    gates["G4"] = {"pass": not g4, "problems": g4[:20], "copied_components": kept, "new_components": total}

    g5, table = observations(record)
    gates["G5"] = {"pass": not g5 and set(table) == {"v1.2.0", "p184"}, "problems": g5[:20], "observations": table}
    gates["G6"] = {"pass": None, "note": "CI green on the evidence commit, checked after the push"}

    nuclides = len({za for za, _ in rec_replaced})
    verdict = {"schema": SCHEMA, "gates": gates, "replaced_reactions": len(rec_replaced), "target_nuclides": nuclides,
               "record_sha256": sha256(RECORD)}
    passed = all(gates[g]["pass"] for g in ("G0", "G1", "G2", "G3", "G4", "G5"))
    verdict["verdict"] = "P184-PASS (pending G6)" if passed else "P184-FAIL"
    if passed:
        verdict["allowed_sentences"] = sentences(len(rec_replaced), nuclides, table, record)
    return verdict


def _bounded(cg):
    try:
        quota, period = cg["cpu_max"].split()
        return (int(cg["memory_max"]) <= 6 << 30 and int(cg["memory_swap_max"]) == 0 and int(cg["pids_max"]) <= 128
                and int(quota) / int(period) <= 4.0 + 1e-9)
    except (TypeError, ValueError, KeyError, AttributeError):
        return False


def sentences(n, m, table, record):
    out = [f"In ACTINV's P184 sidecar, {n} TENDL-2025 reactions on {m} target nuclides take IRDFF-II's covariance, "
           "expressed about TENDL-2025's cross section and including the difference between the two evaluations; "
           "every other covariance is TENDL-2025's, unchanged."]
    shutdown = 300.0
    for nuclide in RESPONSES:
        before, after = table["v1.2.0"].get(nuclide, []), table["p184"].get(nuclide, [])
        cooled = [(t, a, b) for (t, a), (_, b) in zip(before, after) if t is not None and t > shutdown]
        if not cooled:
            continue
        t, a, b = min(cooled, key=lambda row: row[0])
        out.append(f"For FNS iron after 5 minutes' irradiation, the cross-section part of the relative standard "
                   f"deviation of the {nuclide} activity is {100 * a:.3g}% with TENDL-2025's covariances and "
                   f"{100 * b:.3g}% with P184's, at {t - shutdown:.0f} s after shutdown.")
    return out


# ---------------------------------------------------------------------------------------------------- selftest
def selftest():
    x, y = np.array([1.0, 2.0, 3.0, 3.0, 5.0]), np.array([0.0, 2.0, 4.0, 0.0, 0.0])
    assert abs(antiderivative(x, y, 2.5) - 2.25) < 1e-12 and abs(antiderivative(x, y, 9.0) - 4.0) < 1e-12
    assert table_range(x, y) == (1.0, 3.0)
    assert np.allclose(averages(x, y, np.array([1.0, 3.0, 5.0]), 1.0, 3.0), [2.0, 0.0])
    assert real(" 1.234567+5") == 1.234567e5 and real(" 2.50550e+ 4") == 25055.0 and real("-1.0-10") == -1e-10
    grid = np.array([1.0, 2.0, 3.0])
    c_i = np.array([[0.01, 0.002], [0.002, 0.04]])
    same = (np.array([1.0, 3.0]), np.array([1.0, 1.0]))
    flat = (np.array([0.5, 1.5, 2.5, 4.0]), np.array([1.0, 1.0, 1.0]))
    c, _ = expected(same, flat, same, grid, c_i, [])
    assert np.allclose(c, c_i), "identical means leave IRDFF-II's covariance"
    assert np.allclose(histogram_averages(np.array([0.0, 1.0, 3.0]), np.array([4.0, 1.0]), np.array([0.0, 2.0, 3.0]),
                                          0.0, 3.0), [2.5, 1.0])
    double = (np.array([0.5, 1.5, 2.5, 4.0]), np.array([2.0, 2.0, 2.0]))
    c, blocks = expected(same, double, same, grid, c_i, [(np.array([1.0, 1.5, 3.0]), np.array([0.1, 0.2]))])
    assert np.allclose(c, 0.25 * c_i + 0.25), "r = 1/2, b = 1/2 everywhere"
    assert np.allclose(blocks[0][1], [0.025, 0.05])
    assert close(c * (1 + 4e-7), c) and not close(c * (1 + 2e-6), c)
    base = {"target": 3, "mt": 103, "mt1": 103, "lb": 5, "kind": 1, "row_grid": grid, "column_grid": grid,
            "values": c_i.ravel()}
    nudged = dict(base, values=np.nextafter(c_i.ravel(), 1.0))
    assert fingerprint(base) == fingerprint(dict(base)) and fingerprint(base) != fingerprint(nudged)
    assert _bounded({"memory_max": str(6 << 30), "memory_swap_max": "0", "pids_max": "128", "cpu_max": "400000 100000"})
    assert not _bounded({"memory_max": "max", "memory_swap_max": "0", "pids_max": "128", "cpu_max": "400000 100000"})
    assert not _bounded({"memory_max": str(6 << 30), "memory_swap_max": "0", "pids_max": "128",
                         "cpu_max": "800000 100000"})
    print("check_p184 selftest: PASS")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        selftest()
        return 0
    verdict = check()
    text = json.dumps(verdict, indent=1, sort_keys=True) + "\n"
    if args.no_write:
        same = VERDICT.is_file() and VERDICT.read_text() == text
        print(f"{verdict['verdict']}; committed verdict {'matches' if same else 'DIFFERS'}")
        return 0 if same else 1
    VERDICT.write_text(text)
    for gate, body in verdict["gates"].items():
        print(gate, body["pass"])
    print(verdict["verdict"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
