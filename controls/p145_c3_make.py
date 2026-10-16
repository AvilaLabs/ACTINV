#!/usr/bin/env python3
"""P145 C3: writes the synthetic one-reaction covariance sidecars and the three problem/uncertainty spec pairs.

Reaction: Co-59(n,gamma) (MT102) on pure cobalt under a thermal Maxwellian spectrum (kT = 0.0253 eV, truncated at the
last group below 0.5 eV, 1e12 n/cm2/s). Every other Co-59 reaction has a zero group cross section in those groups
(the one exception, MT107, is 1e-20 b), so Co-60 alone sets the Table 2 Column 1 sum of the single target step.

Covariance: one `actinv-covariance-1` component, relative LB=5 over the grid GRID_EV, with every entry of the 4x4 block
equal to sigma2. That matrix is sigma2 * 1 1^T: rank one, relative variance sigma2 on every diagonal entry,
correlation 1 between all energy bins. Collapsed through any spectrum inside the grid it gives the relative variance
sigma2 on the one-group cross section of every Co-59 MT102 row, fully correlated, so the sampler draws one factor
exp(sigma z - sigma2/2), z standard normal, for all three rows (base, Co60 ground, Co60 isomer).

Closed form. Burn-up and Co-60 destruction are negligible, so the Co-60 inventory, and with it the Table 2 Column 1
normalized sum s, is s0 * F with F that lognormal factor and s0 the nominal sum. Then
    P(s > 1) = P(F > 1/s0) = Phi((ln s0 - sigma2/2) / sigma).
For a target probability p the nominal sum must be s0 = exp(sigma2/2 + z_p sigma), z_p = Phi^-1(p). The three
targets need three different s0 (above 1 for p = 0.9, near 1.2 for 0.5, below 1 for 0.1) at moderate sigma, so each
case has its own amount of cobalt.

Cases (sigma2, target P(s > 1)): p90 (0.16, 0.9), p50 (0.36, 0.5), p10 (0.64, 0.1).

HOW TO SET THE MASSES. The component mass scales s0 linearly (s0 = a * mass_g / VOLUME_CM3, a fixed by the
irradiation). Run `actinv waste uncertainty` once per case with the committed masses (or any nominal waste run), read
the nominal Table 2 Column 1 normalized sum s_obs, and set
    mass_g_new = mass_g_old * TARGET_S0[case] / s_obs
`python3 controls/p145_c3_make.py --targets` prints TARGET_S0. The MASS_G defaults below come from a hand estimate
(one-group sigma 32.96 b from the TENDL-2025 709-group library, N59 = N_A / 58.9332, lambda Co-60 = ln2 / 5.2714 y,
30 d irradiation, 1 d cooling); they are expected to be within a few percent, not exact. Commit the final masses
before any evidence run; none is tuned afterwards.

The output is deterministic: no timestamps in the NPZ members, flux weights rounded to 12 significant digits.

    python3 controls/p145_c3_make.py            # writes controls/p145_inputs/c3_*
    python3 controls/p145_c3_make.py --targets  # prints sigma2, z, target s0 and the current masses
    python3 controls/p145_c3_make.py --check-library PATH  # compares the group bounds formula with a library NPZ
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import struct
import sys
import zipfile
from pathlib import Path
from statistics import NormalDist

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
INPUTS = ROOT / "controls/p145_inputs"

# --- parameters ----------------------------------------------------------------------------------------------
# case -> (relative variance of the Co-59 MT102 one-group cross section, target P(normalized sum > 1))
CASES = {"p90": (0.16, 0.9), "p50": (0.36, 0.5), "p10": (0.64, 0.1)}
# Component mass in g (see HOW TO SET THE MASSES). The displaced volume is fixed.
MASS_G = {"p90": 12.95, "p50": 8.573, "p10": 3.537}
VOLUME_CM3 = 1000.0
SEED = {"p90": 145031, "p50": 145032, "p10": 145033}
SAMPLES = 4096
TARGET_STEP = 2
TOTAL_FLUX = 1.0e12  # n/cm2/s
KT_EV = 0.0253
THERMAL_GROUPS = 234  # fispact-709 groups 0..233 end at 0.4786301 eV
GRID_EV = [1.0e-5, 1.0e-2, 1.0e-1, 5.0e-1, 2.0e7]  # covariance energy grid, spans every thermal group
TARGET_INDEX = 528  # Co-59 in tendl-2025-neutron-709g_index.json (za 27059, liso 0)
MT = 102
LB_RELATIVE = 5
KIND_RELATIVE = 1
LIBRARY_SHA256 = "ec4c72bf598dc8ad3d533d9cfafdcf493e2d1f949a3e4db6251495659b68cc44"
OUTPUTS = ["inventory", "activity", "heat", "ledger", "certificate"]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def target_s0(sigma2: float, p: float) -> float:
    """Nominal normalized sum that puts P(s > 1) at p for relative variance sigma2."""
    return math.exp(sigma2 / 2.0 + NormalDist().inv_cdf(p) * math.sqrt(sigma2))


def closed_form(s0: float, sigma2: float) -> float:
    """P(s > 1) = Phi((ln s0 - sigma2/2) / sigma)."""
    return NormalDist().cdf((math.log(s0) - sigma2 / 2.0) / math.sqrt(sigma2))


def group_bounds(count: int) -> list[float]:
    """The first `count` + 1 fispact-709 group bounds in eV: 50 groups per decade from 1e-5, 7 significant digits."""
    return [float(f"{1.0e-5 * 10 ** (0.02 * i):.7g}") for i in range(count + 1)]


def thermal_weights() -> list[float]:
    """709 ascending group weights summing to 1: a Maxwellian flux E exp(-E/kT) integrated over each thermal group."""
    bounds = group_bounds(THERMAL_GROUPS)
    raw = []
    for low, high in zip(bounds, bounds[1:]):
        raw.append(KT_EV * ((low + KT_EV) * math.exp(-low / KT_EV) - (high + KT_EV) * math.exp(-high / KT_EV)))
    total = math.fsum(raw)
    weights = [float(f"{value / total:.12g}") for value in raw]
    return weights + [0.0] * (709 - THERMAL_GROUPS)


# --- covariance sidecar --------------------------------------------------------------------------------------

def npy_bytes(array: np.ndarray) -> bytes:
    buffer = io.BytesIO()
    np.lib.format.write_array(buffer, np.ascontiguousarray(array), version=(1, 0), allow_pickle=False)
    return buffer.getvalue()


def covariance_members(sigma2: float) -> dict[str, np.ndarray]:
    """The four arrays `actinv_data::covariance::read_npz` requires, for one rank-one relative component."""
    bins = len(GRID_EV) - 1
    components = np.array([[TARGET_INDEX, MT, MT, LB_RELATIVE, KIND_RELATIVE, 0, 0, 0, bins * bins]], dtype="<i8")
    return {
        "components.npy": components,
        "grid_offsets.npy": np.array([0, len(GRID_EV)], dtype="<i8"),
        "grid_values.npy": np.array(GRID_EV, dtype="<f8"),
        "values.npy": np.full(bins * bins, sigma2, dtype="<f8"),
    }


def write_covariance(path: Path, sigma2: float) -> str:
    """Write a deterministic NPZ (fixed member order and timestamps, stored); return its SHA-256."""
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, array in covariance_members(sigma2).items():
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = 0o600 << 16
            archive.writestr(info, npy_bytes(array))
    return sha256_bytes(path.read_bytes())


def read_npy(data: bytes) -> tuple[str, tuple[int, ...], bytes]:
    """Minimal .npy reader with the same acceptance rules as `library::read_npy_header`."""
    if data[:6] != b"\x93NUMPY":
        raise ValueError("not a .npy file")
    if data[6] == 1:
        length, start = struct.unpack("<H", data[8:10])[0], 10
    elif data[6] in (2, 3):
        length, start = struct.unpack("<I", data[8:12])[0], 12
    else:
        raise ValueError("unsupported .npy version")
    header = data[start:start + length].decode("utf-8")
    if "'descr': '<i8'" in header:
        dtype = "<i8"
    elif "'descr': '<f8'" in header:
        dtype = "<f8"
    else:
        raise ValueError(f"unsupported dtype in {header}")
    if "'fortran_order': False" not in header:
        raise ValueError("only C-order arrays are supported")
    shape_text = header.split("'shape':")[1].lstrip().removeprefix("(").split(")")[0]
    shape = tuple(int(field) for field in shape_text.split(",") if field.strip())
    if not shape:
        raise ValueError("scalar arrays are unsupported")
    return dtype, shape, data[start + length:]


def verify_covariance(path: Path) -> dict:
    """Re-implement `read_npz` + `CovarianceLibrary::validate` and return the decoded layout.

    Raises ValueError for anything the Rust reader would refuse.
    """
    required = ["components.npy", "grid_offsets.npy", "grid_values.npy", "values.npy"]
    with zipfile.ZipFile(path) as archive:
        names = [info.filename for info in archive.infolist()]
        if sorted(names) != sorted(required) or len(set(names)) != len(names):
            raise ValueError(f"unexpected members {names}")
        arrays = {}
        for name in required:
            dtype, shape, payload = read_npy(archive.read(name))
            count = math.prod(shape)
            if len(payload) != 8 * count:
                raise ValueError(f"{name}: payload length {len(payload)} != {8 * count}")
            fmt = "<" + ("q" if dtype == "<i8" else "d") * count
            arrays[name] = (dtype, shape, struct.unpack(fmt, payload))
    dtype, shape, flat = arrays["components.npy"]
    if dtype != "<i8" or len(shape) != 2 or shape[1] != 9:
        raise ValueError("components.npy must be <i8 with 9 columns")
    dtype, shape, offsets = arrays["grid_offsets.npy"]
    if dtype != "<i8" or len(shape) != 1 or not offsets:
        raise ValueError("grid_offsets.npy must be a nonempty 1-D <i8 array")
    dtype, shape, grid_values = arrays["grid_values.npy"]
    if dtype != "<f8" or len(shape) != 1:
        raise ValueError("grid_values.npy must be a 1-D <f8 array")
    dtype, shape, values = arrays["values.npy"]
    if dtype != "<f8" or len(shape) != 1:
        raise ValueError("values.npy must be a 1-D <f8 array")
    if offsets[0] != 0 or any(b <= a for a, b in zip(offsets, offsets[1:])) or offsets[-1] != len(grid_values):
        raise ValueError("grid offsets are inconsistent")
    grids = [list(grid_values[a:b]) for a, b in zip(offsets, offsets[1:])]
    for index, grid in enumerate(grids):
        if (len(grid) < 2 or any(not math.isfinite(v) or v < 0 for v in grid)
                or any(b <= a for a, b in zip(grid, grid[1:]))):
            raise ValueError(f"grid {index} is not strictly increasing and nonnegative")
    if any(not math.isfinite(v) for v in values):
        raise ValueError("nonfinite covariance value")
    components = []
    for row in range(len(flat) // 9):
        target, mt, mt1, lb, kind, row_grid, col_grid, offset, length = flat[9 * row:9 * row + 9]
        expected_kind = {0: 0, 8: 8, 9: 9}.get(lb, 1 if 1 <= lb <= 6 else None)
        if expected_kind is None or kind != expected_kind:
            raise ValueError(f"component {row}: LB {lb} with kind {kind}")
        if not (0 <= row_grid < len(grids) and 0 <= col_grid < len(grids)):
            raise ValueError(f"component {row}: grid index out of range")
        if kind in (0, 1):
            expected = (len(grids[row_grid]) - 1) * (len(grids[col_grid]) - 1)
        else:
            if row_grid != col_grid or mt != mt1:
                raise ValueError(f"component {row}: short-range block is not a self matrix")
            expected = len(grids[row_grid]) - 1
        if length != expected or offset + length > len(values):
            raise ValueError(f"component {row}: invalid value range {offset}+{length}, expected {expected}")
        components.append({"target": target, "mt": mt, "mt1": mt1, "lb": lb, "kind": kind,
                           "values": list(values[offset:offset + length])})
    return {"members": names, "grids": grids, "components": components}


# --- specs ---------------------------------------------------------------------------------------------------

def base_blocks() -> dict:
    """Library and decay blocks copied from the repository's FNS example so every P145 spec names the same data."""
    example = json.loads((ROOT / "examples/fns_fe_5min.json").read_text())
    if example["library"]["sha256"] != LIBRARY_SHA256:
        raise SystemExit("examples/fns_fe_5min.json names a different library than the covariance target index")
    return {"library": example["library"], "decay": example["decay"]}


def problem_doc(case: str) -> dict:
    blocks = base_blocks()
    return {
        "spec": "actinv-spec-1",
        "title": f"P145 C3 {case}: Co-59 under a thermal Maxwellian, 30 d irradiation then 1 d cooling",
        "projectile": "neutron",
        "library": blocks["library"],
        "decay": blocks["decay"],
        "material": {"mass_g": 1.0, "basis": "wt_percent", "composition": {"Co": 100.0}},
        "spectrum": {"structure": "fispact-709", "flux_per_group": thermal_weights(), "total": TOTAL_FLUX},
        "schedule": [{"dt": "30 d", "flux": 1.0}, {"dt": "1 d", "flux": 0.0}],
        "options": {"mode": "auto", "prune": "rate", "bmin_atoms_per_g": 1e-08, "temperature_K": 293.6,
                    "outputs": OUTPUTS},
    }


def uncertainty_doc(case: str, cov_name: str, cov_sha: str) -> dict:
    return {
        "schema": "actinv-waste-uncertainty-spec-1",
        "problem": f"c3_{case}_problem.json",
        "rules": "us-nrc-10cfr61.55-v1",
        "targets": [TARGET_STEP],
        "nuclide_properties": "from_decay_data",
        "components": [{"id": "co-block", "mass_g": MASS_G[case], "displaced_volume_cm3": VOLUME_CM3,
                        "waste_type": "general", "external_tritium": {"status": "not_applicable"}}],
        "sampling": {"channels": {"cross_section_mf33": True},
                     "covariance": {"path": cov_name, "sha256": cov_sha},
                     "samples": SAMPLES, "seed": SEED[case]},
        "confidence_level": 0.95,
    }


def dump(path: Path, doc: dict) -> None:
    path.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")


def generate() -> None:
    INPUTS.mkdir(parents=True, exist_ok=True)
    meta = {"schema": "actinv-p145-c3-cases-1", "reaction": "Co-59(n,gamma) MT102, target index 528",
            "closed_form": "P(s>1) = Phi((ln s0 - sigma2/2)/sigma)", "volume_cm3": VOLUME_CM3,
            "samples": SAMPLES, "target_step": TARGET_STEP, "confidence": 0.999, "cases": []}
    for case, (sigma2, p) in CASES.items():
        cov_name = f"c3_{case}.cov.npz"
        cov_sha = write_covariance(INPUTS / cov_name, sigma2)
        verify_covariance(INPUTS / cov_name)
        dump(INPUTS / f"c3_{case}_problem.json", problem_doc(case))
        dump(INPUTS / f"c3_{case}.json", uncertainty_doc(case, cov_name, cov_sha))
        meta["cases"].append({"id": case, "spec": f"c3_{case}.json", "sigma2": sigma2, "target_probability": p,
                              "target_s0": target_s0(sigma2, p), "component_mass_g": MASS_G[case],
                              "covariance": cov_name, "covariance_sha256": cov_sha})
        print(f"{case}: sigma2={sigma2} target P={p} target s0={target_s0(sigma2, p):.6f} mass={MASS_G[case]} g "
              f"cov sha256={cov_sha[:12]}")
    dump(INPUTS / "c3_cases.json", meta)


def check_library(path: str) -> None:
    """Compare the closed-form group bounds with a library NPZ (reads data; not run by the author)."""
    library = np.load(path)
    bounds = library["bounds"]
    mismatches = [i for i, value in enumerate(group_bounds(THERMAL_GROUPS)) if value != bounds[i]]
    print(f"group bound mismatches in the first {THERMAL_GROUPS + 1}: {len(mismatches)}")
    if mismatches or not (float(bounds[THERMAL_GROUPS]) < 0.5 <= float(bounds[THERMAL_GROUPS + 1])):
        raise SystemExit(1)


if __name__ == "__main__":
    if "--targets" in sys.argv:
        for case, (sigma2, p) in CASES.items():
            print(f"{case}: sigma2={sigma2} p={p} target_s0={target_s0(sigma2, p):.6f} mass_g={MASS_G[case]}")
    elif "--check-library" in sys.argv:
        check_library(sys.argv[sys.argv.index("--check-library") + 1])
    else:
        generate()
