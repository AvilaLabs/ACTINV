"""Reference file -> ACTINV inputs: a synthetic ENDF-6 decay sublibrary (MF=8/MT=457), a one-group activation
library (.npz + _index.json, the P166 layout) when the case irradiates, and an actinv-spec-1 problem.json.

The ENDF writer follows the minimal_decay test of crates/actinv-data/src/decay.rs (daughters come from RTYP and the
isomer index in RFS); the library layout follows controls/p166_harness.py. Nothing here runs ACTINV.
"""
import hashlib
import json
from decimal import ROUND_HALF_EVEN, Decimal, getcontext
from pathlib import Path

from p168 import cases

getcontext().prec = 60
TEMPERATURE_K = 293.6
BOUNDS_EV = [1.0, 3.0]


def endf_f(value):
    """ENDF 11-column float (6 decimals, signed one- or two-digit exponent) of a decimal string or number."""
    x = Decimal(str(value))
    if x == 0:
        return "0.000000+0"
    e = x.adjusted()
    m = x.scaleb(-e).quantize(Decimal("0.000001"), rounding=ROUND_HALF_EVEN)
    if m >= 10:
        m, e = m / 10, e + 1
    text = f"{m:.6f}{'+' if e >= 0 else '-'}{abs(e)}"
    assert len(text) <= 11, text
    return text


def record(values, mat, mf, mt, seq):
    return "".join(f"{v:>11}" for v in values) + f"{mat:>4}{mf:>2}{mt:>3}{seq:>5}"


def daughter(za, rtyp):
    z, a = divmod(za, 1000)
    return {1: (z + 1, a), 2: (z - 1, a), 3: (z, a), 4: (z - 2, a - 4)}[rtyp]


def write_decay(case, path):
    nuclides = {n["name"]: n for n in case["nuclides"]}
    lines = []
    for mat, n in enumerate(case["nuclides"], start=1):
        modes = case["decays"].get(n["name"], [])
        stable = n["half_life_s"] is None
        for m in modes:  # the daughter must be what ACTINV derives from RTYP and RFS
            d = nuclides[m["to"]]
            assert daughter(n["za"], m["rtyp"]) == divmod(d["za"], 1000), (n["name"], m)
            assert d["liso"] == m["rfs"], (n["name"], m)
        head = [str(n["za"]), n["awr"], "1" if n["liso"] else "0", str(n["liso"]), "1" if stable else "0", "0"]
        lines.append(record(head, mat, 8, 457, 1))
        lines.append(record([endf_f(n["half_life_s"] or "0"), "0", "0", "0", "0", "0"], mat, 8, 457, 2))
        k = len(modes)
        lines.append(record(["0", "0", "0", "0", str(6 * k), str(k)], mat, 8, 457, 3))
        for seq, m in enumerate(modes, start=4):
            lines.append(record([endf_f(m["rtyp"]), endf_f(m["rfs"]), "0.000000+0", "0.000000+0",
                                 endf_f(m["branching"]), "0.000000+0"], mat, 8, 457, seq))
        lines.append(record(["", "", "", "", "", ""], mat, 8, 0, 99999))
    Path(path).write_text("\n".join(lines) + "\n")


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_library(case, out):
    import numpy as np  # only irradiating cases need a library
    nuclides = {n["name"]: n for n in case["nuclides"]}
    targets, rows, sigma = {}, [], []
    for r in case["reactions"]:
        t = targets.setdefault(r["from"], len(targets))
        rows.append([t, r["mt"], -1, -1, 0])
        sigma.append([float(r["loss_sigma_b"])])
        for p in r["products"]:
            d = nuclides[p["to"]]
            rows.append([t, r["mt"], d["za"], d["liso"], 0])
            sigma.append([float(p["sigma_b"])])
    npz = Path(out) / "lib.npz"
    np.savez(npz, rows=np.asarray(rows, dtype=np.int64), sig=np.asarray(sigma, dtype=np.float64),
             bounds=np.asarray(BOUNDS_EV, dtype=np.float64))
    index = {"groups": 1, "n_rows": len(rows), "temperature_K": TEMPERATURE_K, "sha256_npz": sha256(npz),
             "targets": [{"za": nuclides[name]["za"], "liso": nuclides[name]["liso"], "awr": float(nuclides[name]["awr"]),
                          "ledger": []} for name in targets]}
    (Path(out) / "lib_index.json").write_text(json.dumps(index, indent=1))
    return npz


def schedule_of(case, pulses):
    """The ACTINV schedule: explicit steps, a pulse train expanded to on/off pairs truncated at `pulses`."""
    s = case["schedule"]
    if isinstance(s, dict):
        train = s["pulse_train"]
        n = min(train["n_pulses"], pulses or train["n_pulses"])
        return [{"dt": train["on"]["dt_spec"], "flux": 1.0}, {"dt": train["off"]["dt_spec"], "flux": 0.0}] * n
    return [{"dt": x["dt_spec"], "flux": float(x["flux_multiplier"])} for x in s]


def generate(case, out, cram_order, mode, pulses=None):
    """Write decay.endf, lib.npz/lib_index.json (irradiating cases) and problem.json into `out`; returns a summary."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    write_decay(case, out / "decay.endf")
    schedule = schedule_of(case, pulses)
    decay_only = all(step["flux"] == 0 for step in schedule)
    spec = {"spec": "actinv-spec-1", "title": case["case"],
            "decay": {"primary": str((out / "decay.endf").resolve())},
            "material": {"mass_g": case["mass_g"], "basis": "atoms_per_g",
                         "composition": {k: float(Decimal(v)) for k, v in case["initial_atoms_per_g"].items()}},
            "schedule": schedule,
            "options": {"mode": mode, "prune": "none", "bmin_atoms_per_g": 0.0, "temperature_K": TEMPERATURE_K,
                        "cram_order": cram_order, "outputs": cases.OUTPUTS}}
    if not decay_only:
        npz = write_library(case, out)
        spec["library"] = {"path": str(npz.resolve()), "sha256": sha256(npz)}
        spec["spectrum"] = {"structure": "custom", "boundaries_eV": BOUNDS_EV, "flux_per_group": [1.0],
                            "total": float(Decimal(case["flux_n_cm2_s"])), "descending": False}
    problem = out / "problem.json"
    problem.write_text(json.dumps(spec, indent=1))
    return {"problem": problem, "decay_only": decay_only, "steps": len(schedule), "problem_sha256": sha256(problem)}
