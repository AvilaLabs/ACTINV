#!/usr/bin/env python3
"""P140 gate H2 (protocols/ACTINV-P140_PROTOCOL.md): the interaction matrix of the P122-P139 parity additions.

Materials: natural Fe, natural W, SS316 (with Co, Nb and Ta impurities), natural U; plus a 35-step schedule for Fe and
SS316. Modes: trace where permitted, coupled (and, for the baseline only, the default `auto`). Option sets: baseline;
summary with depletion (and a filtered summary); Steiner-22 photons with a point source and bremsstrahlung;
power-driven steps with a kerma table; wall loading; partition; overrides; pathway routes; geometry self-shielding;
uncertainty with depletion; fission count; gas production; three maximal compatible combinations; and a set of probes
for the combinations the specification documents as refused.

Each run must either succeed or refuse with a message naming the conflicting options. A non-zero exit passes only
when the exit status is a plain error (1 or 2, not a panic or signal) and the message mentions at least one option
key involved in the case. On every successful run:

  finite        the result parses without NaN/Infinity tokens, holds no non-finite number and no null in a numeric
                field (serde writes NaN and infinity as null);
  inventory     no inventory row below -1e-12 of the step's tracked total;
  summary       summary totals equal the sum of their per-nuclide rows (when the table is unfiltered), the element
                rows sum to the totals, and the derived totals/dominant/scaled blocks agree, within 1e-12;
  depletion     each initial nuclide's channel shares sum to 1 within 1e-12;
  routes        each pathway_routes target satisfies total = sum(routes) + residual within 1e-12;
  repeat        a second run of the same spec is byte-identical apart from `"ms"`.

The baseline default-options runs (`auto`, all four materials and examples/fns_fe_5min.json itself) must also be
byte-identical to the REFERENCE binary's apart from `"ms"` (`reference_identical`), or, under P140 Amendment B,
`reference_compatible`: identical once `ms`, the ledger keys `spontaneous_fission_branches_to_leakage` and
`unmodelled_decay_branches_to_leakage` and, for W, SS316 and U only, `pathways` and `pathway_closure` are masked; the
spontaneous-fission count may only fall, by at most the unmodelled count (absent reads as 0, Amendment E); a
numeric `pathway_closure` may not grow.

Informational extras (cross-checks the protocol does not list: summary against the step's activity, depletion rate
sums, route totals against the inventory, honoured mode, negative sinks) are recorded per case and counted but do not
enter the verdict.

Usage:
  check_p140_matrix.py --reference REF --candidate CAND [--workdir target/p140-matrix]
      [--out results/p140_matrix.json] [--timeout 1800] [--only SUBSTR[,SUBSTR...]] [--keep]
      [--nuclear-data ~/nuclear-data] [--photon-response PATH]
  check_p140_matrix.py --self-check [--workdir DIR]     (builds every spec, validates them statically, runs no binary)

Runs are serial argv subprocesses from the repository root, each under --timeout. Run inside the required systemd
cgroup. Exit 0 only on PASS. A result of a passing case is deleted after its checks unless --keep is given.
"""
import argparse
import array
import ast
import copy
import hashlib
import json
import math
import os
import re
import struct
import subprocess
import sys
import time
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PROTOCOL = ROOT / "protocols" / "ACTINV-P140_PROTOCOL.md"
BASE_SPEC = ROOT / "examples" / "fns_fe_5min.json"
SPEC_RS = ROOT / "crates" / "actinv-core" / "src" / "spec.rs"
PATHWAYS_RS = ROOT / "crates" / "actinv-core" / "src" / "pathways.rs"
DATA_DIR = ROOT / "actinv-data" / "v1.1.0"
COVARIANCE = DATA_DIR / "uncertainty" / "tendl-2025-neutron-709g.cov.npz"
MS = re.compile(rb'"ms":\s*[-+0-9.eE]+')
TOL = 1e-12  # the protocol's tolerance for every sum and share
EXTRA_TOL = 1e-9  # tolerance of the informational cross-checks
EV_J = 1.602176634e-19
BARN_CM2 = 1.0e-24
AVOGADRO = 6.02214076e23
WALL_MW_M2 = 2.5e-4  # x 4.44e13 n cm^-2 s^-1 per MW m^-2 = 1.11e10, the base spectrum's total flux
MAX_LISTED = 12

ELEMENTS = ("H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Br Kr Rb Sr Y "
            "Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W "
            "Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm").split()
# Standard atomic weights (g/mol), used only to size the power targets near unit flux multiplier.
ATOMIC_MASS = {"Fe": 55.845, "Cr": 51.9961, "Ni": 58.6934, "Mo": 95.95, "Mn": 54.938, "Si": 28.0855, "C": 12.011,
               "Co": 58.9332, "Nb": 92.9064, "Ta": 180.9479, "W": 183.84, "U": 238.0289}

SHORT_SCHEDULE = [{"dt": "300 s", "flux": 1.0}, {"dt": "600 s", "flux": 0.0}, {"dt": "300 s", "flux": 0.5},
                  {"dt": "3600 s", "flux": 0.0}]
LONG_SCHEDULE = ([{"dt": "1 d", "flux": 1.0} for _ in range(30)]
                 + [{"dt": "1 h", "flux": 0.0}, {"dt": "1 d", "flux": 0.0}, {"dt": "30 d", "flux": 0.0},
                    {"dt": "1 y", "flux": 0.0}, {"dt": "10 y", "flux": 0.0}])

# ------------------------------------------------------------------------------ materials

SS316 = {"Cr": 17.0, "Ni": 12.0, "Mo": 2.5, "Mn": 2.0, "Si": 0.75, "C": 0.03, "Co": 0.05, "Nb": 0.01, "Ta": 0.01}
SS316_FE = round(100.0 - sum(SS316.values()), 6)

MATERIALS = {
    "fe": {
        "label": "natural Fe", "composition": {"Fe": 100.0}, "density": 7.874, "bulk": "Fe", "product": "Mn",
        "pathway_targets": ["Mn54", "Mn56", "Fe55", "Cr51"], "by_step": True,
        "brems": ["Mn56", "Fe59", "Co60"],
        "scale": [("Fe54", 102, 2.0)], "absolute": [("Fe54", 103, 0.25)], "half_life": [("Mn56", 2.6, "h")],
        "geometry": {"nuclides": ["Fe54", "Fe56", "Fe57", "Fe58"], "length_cm": 0.1},
    },
    "w": {
        "label": "natural W", "composition": {"W": 100.0}, "density": 19.3, "bulk": "W", "product": "Ta",
        "pathway_targets": ["W187", "W185", "W181", "Ta182"], "by_step": False,
        "brems": ["W185", "W187", "Ta182"],
        "scale": [("W186", 102, 1.5)], "absolute": [("W184", 102, 0.05)], "half_life": [("W187", 24.0, "h")],
        "geometry": {"nuclides": ["W180", "W182", "W183", "W184", "W186"], "length_cm": 0.01},
    },
    "ss316": {
        "label": "SS316 (Co 0.05, Nb 0.01, Ta 0.01 wt%)", "composition": dict({"Fe": SS316_FE}, **SS316),
        "density": 8.0, "bulk": "Fe", "product": "Mn",
        "pathway_targets": ["Mn54", "Mn56", "Co60", "Co58", "Cr51", "Fe55", "Ta182", "Nb94"], "by_step": False,
        "brems": ["Mn56", "Co60", "Fe59", "Mo99"],
        "scale": [("Co59", 102, 0.8)], "absolute": [("Fe54", 103, 0.25)], "half_life": [("Mn56", 2.6, "h")],
        "geometry": {"nuclides": ["Fe54", "Fe56", "Fe57", "Fe58", "Ta181"], "length_cm": 0.1},
    },
    "u": {
        "label": "natural U", "composition": {"U": 100.0}, "density": 19.05, "bulk": "U", "product": "Np",
        "pathway_targets": ["U237", "U239", "Np239", "Pu239"], "by_step": False,
        "brems": ["U239", "Np239", "Th234"],
        "scale": [("U238", 102, 1.1)], "absolute": [("U235", 102, 0.1)], "half_life": [("U239", 24.0, "min")],
        "geometry": {"nuclides": ["U234", "U235", "U238"], "length_cm": 0.1},
    },
}
for _key, _material in MATERIALS.items():
    _material["key"] = _key
MATERIAL_ORDER = ("fe", "w", "ss316", "u")
LONG_MATERIALS = ("fe", "ss316")


class Skip(Exception):
    """A case whose inputs are not present on this machine."""


# ------------------------------------------------------------------------------ small helpers


_SHA_CACHE = {}


def sha256_file(path):
    path = str(path)
    if path not in _SHA_CACHE:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                h.update(block)
        _SHA_CACHE[path] = h.hexdigest()
    return _SHA_CACHE[path]


def rel_gap(a, b):
    scale = max(abs(a), abs(b))
    return 0.0 if scale == 0.0 else abs(a - b) / scale


def clip(text, n=700):
    text = text.strip()
    return text if len(text) <= n else "..." + text[-n:]


def ascending_flux(spectrum):
    """Spec::ascending_flux: reverse when `descending`, then scale to `total`."""
    flux = [float(v) for v in spectrum["flux_per_group"]]
    if spectrum.get("descending"):
        flux.reverse()
    total = spectrum.get("total")
    if total is not None:
        s = sum(flux)
        flux = [v * (float(total) / s) for v in flux]
    return flux


def read_npy(archive, member, typecode):
    with archive.open(member) as handle:
        if handle.read(6) != b"\x93NUMPY":
            raise ValueError(f"{member} is not an npy member")
        major, _minor = handle.read(2)
        (length,) = struct.unpack("<H", handle.read(2)) if major == 1 else struct.unpack("<I", handle.read(4))
        header = ast.literal_eval(handle.read(length).decode("latin1").strip())
        count = 1
        for dim in header["shape"]:
            count *= dim
        want = 8 * count
        buffer = bytearray()
        while len(buffer) < want:
            chunk = handle.read(want - len(buffer))
            if not chunk:
                raise ValueError(f"{member} is truncated")
            buffer += chunk
    values = array.array(typecode)
    values.frombytes(bytes(buffer))
    return values, header["shape"]


class LibraryFacts:
    """Group boundaries and the (target, MT) rows of the activation library, read without the engine."""

    def __init__(self, npz_path):
        assert sys.byteorder == "little"
        index = json.loads(Path(str(npz_path).replace(".npz", "_index.json")).read_text())
        self.targets = [(t["za"], t["liso"]) for t in index["targets"]]
        with zipfile.ZipFile(npz_path) as archive:
            bounds, _ = read_npy(archive, "bounds.npy", "d")
            rows, shape = read_npy(archive, "rows.npy", "q")
        self.bounds = list(bounds)
        self.mts = {}
        for i in range(shape[0]):
            target, mt = rows[5 * i], rows[5 * i + 1]
            self.mts.setdefault(self.targets[target], set()).add(mt)

    def has_reaction(self, name, mt):
        m = re.fullmatch(r"([A-Z][a-z]?)(\d+)(?:m(\d+))?", name)
        z = ELEMENTS.index(m.group(1)) + 1
        key = (z * 1000 + int(m.group(2)), int(m.group(3) or 0))
        return mt in self.mts.get(key, ())


# ------------------------------------------------------------------------------ context (data the specs refer to)


class Context:
    def __init__(self, args, workdir):
        self.workdir = workdir
        self.base = json.loads(BASE_SPEC.read_text())
        self.nuclear_data = Path(args.nuclear_data).expanduser()
        self.library_path = ROOT / self.base["library"]["path"]
        self.facts = LibraryFacts(self.library_path)
        self.notes = []
        self.kerma = self._write_kerma_table()
        self.covariance = {"path": str(COVARIANCE), "sha256": sha256_file(COVARIANCE)}
        response = Path(args.photon_response).expanduser() if args.photon_response else \
            self.nuclear_data / "photon-response" / "nist-xcom-all.json"
        self.response = None
        self.response_has_air = False
        if response.is_file():
            self.response = {"path": str(response), "sha256": sha256_file(response)}
            self.response_has_air = "air_mass_attenuation" in json.loads(response.read_text())
        else:
            self.notes.append(f"photon response {response} absent: photon cases run without one (doses are null)")
        self.resonance_dir = self.nuclear_data / "tendl-2025" / "files" / "n"
        self._resonance = {}

    # -- spec skeleton
    def spec_skeleton(self, title, schedule):
        spec = copy.deepcopy(self.base)
        spec["title"] = title
        spec["library"]["path"] = str(ROOT / spec["library"]["path"])
        for key in ("primary", "fallback"):
            if spec.get("decay", {}).get(key):
                spec["decay"][key] = str(ROOT / spec["decay"][key])
        spec["options"] = dict(spec.get("options", {}))
        spec["schedule"] = copy.deepcopy(schedule)
        return spec

    # -- kerma
    def _write_kerma_table(self):
        bounds = self.facts.bounds
        groups = len(bounds) - 1

        def row(base, phase):
            return [base * (1.0 + 0.5 * math.sin(0.11 * g + phase)) for g in range(groups)]

        targets = {el: row(2.0e6 + 1.0e4 * z, 0.37 * z) for z, el in enumerate(ELEMENTS, start=1)}
        targets["Fe56"] = row(3.0e6, 0.7)  # a nuclide row beats its element's row
        table = {
            "format": "actinv-kerma-table-1",
            "source": {"citation": "synthetic deterministic table for the P140 matrix checker (not evaluated data)",
                       "edition": "controls/check_p140_matrix.py Context._write_kerma_table", "url": "local"},
            "projectile": "neutron", "group_structure": "fispact-709", "boundaries_eV": bounds,
            "units": "kerma_barn_eV_per_group", "targets": targets,
            "damage_energy_targets": {"Fe56": row(4.0e5, 0.4), "Fe": row(3.5e5, 0.9)},
            "temperature_K": 293.6,
        }
        self.kerma_rows = targets
        path = self.workdir / "synthetic_kerma.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(table))
        return {"path": str(path), "sha256": sha256_file(path)}

    def power_w_per_g(self, material):
        """0.4 of the heating the initial material makes at unit flux multiplier, from the synthetic element rows."""
        flux = ascending_flux(self.base["spectrum"])
        heating = 0.0
        for element, wt in material["composition"].items():
            atoms = wt / 100.0 * AVOGADRO / ATOMIC_MASS[element]
            per_atom = sum(k * (f * BARN_CM2) for k, f in zip(self.kerma_rows[element], flux)) * EV_J
            heating += atoms * per_atom
        return float(f"{0.4 * heating:.4g}")

    # -- resonance files
    def resonance_files(self, material_key):
        """Hash-pinned TENDL-2025 resonance evaluations the solver can read, for the material's geometry set."""
        if material_key in self._resonance:
            return self._resonance[material_key]
        refs = []
        sys.path.insert(0, str(HERE))
        try:
            import check_p129 as p129
        except ImportError:
            p129 = None
        for nuclide in MATERIALS[material_key]["geometry"]["nuclides"]:
            m = re.fullmatch(r"([A-Z][a-z]?)(\d+)", nuclide)
            path = self.resonance_dir / f"n-{m.group(1)}{int(m.group(2)):03d}.tendl"
            if not path.is_file():
                self.notes.append(f"{material_key}: resonance file {path.name} absent")
                continue
            usable = True
            if p129 is not None:
                try:
                    parsed = p129.parse_mf2_file(path)
                    usable = any(r["lru"] == 1 and r["lrf"] in (1, 2, 3, 7)
                                 for iso in parsed["isotopes"] for r in iso["ranges"])
                except (ValueError, IndexError, KeyError, OSError) as error:
                    usable = False
                    self.notes.append(f"{material_key}: {path.name} not readable as MF=2 ({error})")
            if usable:
                refs.append({"path": str(path), "sha256": sha256_file(path)})
            else:
                self.notes.append(f"{material_key}: {path.name} has no resolved range the solver reads")
        self._resonance[material_key] = refs
        return refs


# ------------------------------------------------------------------------------ option sets
# Each option function edits the spec in place and returns the option keys involved (lowercase fragments that a
# refusal message would be expected to mention).


def opt_baseline(spec, m, ctx):
    return []


def opt_summary_depletion(spec, m, ctx):
    spec["material"]["mass_g"] = 10.0
    spec["material"]["density_g_cm3"] = m["density"]
    spec["summary"] = {"depletion": True, "dominant_top": 5}
    return ["summary", "depletion"]


def opt_summary_filtered(spec, m, ctx):
    spec["material"]["mass_g"] = 10.0
    spec["material"]["density_g_cm3"] = m["density"]
    spec["summary"] = {"dominant_top": 3, "min_atoms": 1.0e6, "include_stable": False}
    return ["summary", "min_atoms", "include_stable"]


def opt_photon(spec, m, ctx):
    photon = {"group_structure": "steiner-22",
              "point_source": {"distance_m": 1.0, "air_attenuation": bool(ctx.response_has_air)},
              "bremsstrahlung": {"nuclides": list(m["brems"])}}
    if ctx.response:
        photon["response"] = dict(ctx.response)
    spec["photon"] = photon
    return ["photon", "point_source", "bremsstrahlung", "steiner"]


def power_schedule(m, ctx, wall_step=False):
    """Flux-driven irradiation inside the power run is replaced by two power targets (W/g, then W/cm3), cooling gaps
    between, a flux-driven (or wall-loading) step and a closing cool-down."""
    p = ctx.power_w_per_g(m)
    p_cm3 = float(f"{0.8 * p * m['density']:.4g}")
    fifth = {"dt": "300 s", "wall_loading_MW_m2": WALL_MW_M2 / 2} if wall_step else {"dt": "300 s", "flux": 0.5}
    return [{"dt": "300 s", "power_W_per_g": p}, {"dt": "600 s", "flux": 0.0}, {"dt": "300 s", "power_W_cm3": p_cm3},
            {"dt": "600 s", "flux": 0.0}, fifth, {"dt": "1 h", "flux": 0.0}]


def opt_power(spec, m, ctx, wall_step=False):
    spec["material"]["density_g_cm3"] = m["density"]
    spec["kerma"] = {"table": dict(ctx.kerma), "top": 25}
    spec["schedule"] = power_schedule(m, ctx, wall_step)
    return ["power", "kerma"]


def opt_wall(spec, m, ctx):
    first = spec["schedule"][0]
    first.pop("flux", None)
    first["wall_loading_MW_m2"] = WALL_MW_M2
    return ["wall_loading"]


def opt_partition(spec, m, ctx):
    spec["schedule"][1]["partition"] = {m["product"]: 0.5}
    spec["schedule"][2]["partition"] = {m["bulk"]: 0.9}
    return ["partition"]


def make_overrides(m, absolute, scale=True, half_life=True):
    out = []
    if scale:
        out += [{"nuclide": n, "kind": "cross_section_scale", "mt": mt, "value": v} for n, mt, v in m["scale"]]
    if absolute:
        out += [{"nuclide": n, "kind": "cross_section", "mt": mt, "value": v} for n, mt, v in m["absolute"]]
    if half_life:
        out += [{"nuclide": n, "kind": "half_life", "value": v, "units": u} for n, v, u in m["half_life"]]
    return out


def opt_overrides(spec, m, ctx):
    spec["overrides"] = make_overrides(m, absolute=True)
    return ["override"]


def opt_overrides_safe(spec, m, ctx):
    """The overrides that combine with geometry self-shielding (no absolute cross section)."""
    spec["overrides"] = make_overrides(m, absolute=False)
    return ["override"]


def opt_overrides_half_life(spec, m, ctx):
    """The overrides that combine with uncertainty (half-life only)."""
    spec["overrides"] = make_overrides(m, absolute=False, scale=False)
    return ["override"]


def opt_overrides_abs_only(spec, m, ctx):
    spec["overrides"] = make_overrides(m, absolute=True, scale=False, half_life=False)
    return ["override"]


def opt_overrides_scale_only(spec, m, ctx):
    spec["overrides"] = make_overrides(m, absolute=False, half_life=False)
    return ["override"]


def opt_pathways(spec, m, ctx):
    spec["pathways"] = {"targets": list(m["pathway_targets"]), "by_step": bool(m["by_step"])}
    return ["pathways"]


def opt_geometry(spec, m, ctx):
    refs = ctx.resonance_files(m["key"])
    if not refs:
        raise Skip("no readable resonance file for this material under " + str(ctx.resonance_dir))
    spec["material"]["density_g_cm3"] = m["density"]
    spec["self_shielding"] = {"geometry": {"shape": "foil", "length_cm": m["geometry"]["length_cm"],
                                           "density_g_cm3": m["density"], "resonance_files": refs}}
    return ["self_shielding", "geometry"]


def opt_uncertainty(spec, m, ctx):
    spec["uncertainty"] = {"covariance": dict(ctx.covariance), "responses": ["heat.total", "activity.total"]}
    return ["uncertainty"]


def opt_fission(spec, m, ctx):
    spec["options"]["fission"] = {"energy_per_fission_MeV": 200.0}
    return ["fission"]


def opt_gas(spec, m, ctx):
    spec["options"]["gas"] = True
    return ["gas"]


def opt_screen(spec, m, ctx):
    spec["options"]["screen"] = {"bmin_atoms_per_g": 1.0e-6}
    return ["screen"]


def opt_wall_and_power_same_step(spec, m, ctx):
    spec["material"]["density_g_cm3"] = m["density"]
    spec["kerma"] = {"table": dict(ctx.kerma)}
    spec["schedule"][0] = {"dt": "300 s", "power_W_per_g": ctx.power_w_per_g(m), "wall_loading_MW_m2": WALL_MW_M2}
    return ["power", "wall_loading", "kerma"]


def chain(*funcs):
    def apply(spec, m, ctx):
        keys = []
        for f in funcs:
            keys += f(spec, m, ctx)
        return keys
    return apply


def power_with_wall(spec, m, ctx):
    return opt_power(spec, m, ctx, wall_step=True)


# (name, schedule name, option function, modes). `all_*` are the maximal combinations the documented refusals allow:
# power-driven steps refuse uncertainty, pathways, fission and screen; partition refuses pathways and trace;
# cross-section overrides refuse uncertainty; an absolute cross section refuses self-shielding; gas refuses uncertainty.
OPTION_SETS = [
    ("baseline", "base", opt_baseline, ("auto", "trace", "coupled")),
    ("summary_depletion", "base", opt_summary_depletion, ("trace", "coupled")),
    ("summary_filtered", "base", opt_summary_filtered, ("trace", "coupled")),
    ("photon", "base", opt_photon, ("trace", "coupled")),
    ("power", "base", opt_power, ("trace", "coupled")),
    ("wall_loading", "base", opt_wall, ("trace", "coupled")),
    ("partition", "base", opt_partition, ("trace", "coupled")),
    ("overrides", "base", opt_overrides, ("trace", "coupled")),
    ("pathways", "base", opt_pathways, ("trace", "coupled")),
    ("geometry", "base", opt_geometry, ("trace", "coupled")),
    ("uncertainty", "short", chain(opt_uncertainty, opt_summary_depletion), ("trace", "coupled")),
    ("fission_count", "base", opt_fission, ("trace", "coupled")),
    ("gas", "base", opt_gas, ("trace", "coupled")),
    ("all_A", "base", chain(opt_summary_depletion, opt_photon, opt_wall, opt_overrides_safe, opt_pathways,
                            opt_geometry, opt_fission, opt_gas), ("trace", "coupled")),
    ("all_B", "base", chain(opt_summary_depletion, opt_photon, power_with_wall, opt_partition, opt_overrides_safe,
                            opt_geometry, opt_gas), ("trace", "coupled")),
    ("all_C", "short", chain(opt_summary_depletion, opt_photon, opt_wall, opt_pathways, opt_geometry, opt_fission,
                             opt_overrides_half_life, opt_uncertainty), ("trace", "coupled")),
]

# Combinations the specification documents as refused (Fe, coupled): the run must refuse naming both options.
PROBES = [
    ("refuse_power_pathways", "base", chain(opt_power, opt_pathways)),
    ("refuse_power_uncertainty", "base", chain(opt_power, opt_uncertainty)),
    ("refuse_power_fission", "base", chain(opt_power, opt_fission)),
    ("refuse_power_screen", "base", chain(opt_power, opt_screen)),
    ("refuse_partition_pathways", "base", chain(opt_partition, opt_pathways)),
    ("refuse_scale_override_uncertainty", "short", chain(opt_overrides_scale_only, opt_uncertainty)),
    ("refuse_abs_override_uncertainty", "short", chain(opt_overrides_abs_only, opt_uncertainty)),
    ("refuse_abs_override_geometry", "base", chain(opt_overrides_abs_only, opt_geometry)),
    ("refuse_gas_uncertainty", "short", chain(opt_gas, opt_uncertainty)),
    ("refuse_wall_power_same_step", "base", opt_wall_and_power_same_step),
]


# ------------------------------------------------------------------------------ documented conflicts


def static_conflicts(spec):
    """The refusals the specification documents for this spec: [(message, [key fragments])]. A case with one must
    refuse (or, if the build is more permissive, still satisfy every invariant)."""
    out = []
    sched = spec["schedule"]
    opts = spec.get("options", {})
    is_power = [("power_W_cm3" in s or "power_W_per_g" in s) for s in sched]
    power = any(is_power)
    partition = any("partition" in s for s in sched)
    unc = "uncertainty" in spec
    cross_section = [o for o in spec.get("overrides", []) if o["kind"] in ("cross_section", "cross_section_scale")]
    if power:
        if "kerma" not in spec:
            out.append(("power-driven step without a kerma section", ["power", "kerma"]))
        if any("power_W_cm3" in s for s in sched) and spec["material"].get("density_g_cm3") is None:
            out.append(("power_W_cm3 without material.density_g_cm3", ["power_w_cm3", "density"]))
        for label, present, key in (("uncertainty", unc, "uncertainty"), ("options.screen", "screen" in opts, "screen"),
                                    ("options.fission", "fission" in opts, "fission"),
                                    ("pathways", "pathways" in spec, "pathways")):
            if present:
                out.append((f"power-driven steps with {label}", ["power", key]))
    for step, driven in zip(sched, is_power):
        if "wall_loading_MW_m2" in step and (driven or step.get("flux", 0.0) != 0.0):
            out.append(("wall_loading_MW_m2 with a power target or flux on the same step", ["wall_loading", "power"]))
        if "flux" not in step and not driven and "wall_loading_MW_m2" not in step:
            out.append(("a step with no irradiation driver", ["flux"]))
    if partition:
        if "pathways" in spec:
            out.append(("partition with pathways", ["partition", "pathways"]))
        if opts.get("mode") == "trace":
            out.append(("partition with options.mode trace", ["partition", "trace"]))
    if cross_section and unc:
        out.append(("cross-section overrides with uncertainty", ["override", "uncertainty"]))
    if any(o["kind"] == "cross_section" for o in cross_section) and "self_shielding" in spec:
        out.append(("absolute cross-section override with self_shielding", ["override", "self_shielding"]))
    if opts.get("gas") and unc:
        out.append(("options.gas with uncertainty", ["gas", "uncertainty"]))
    return out


# ------------------------------------------------------------------------------ cases


class Case:
    def __init__(self, case_id, material, mode, optset, spec, keys, schedule_name):
        self.id = case_id
        self.material = material
        self.mode = mode
        self.optset = optset
        self.spec = spec
        self.keys = keys
        self.schedule_name = schedule_name
        self.skip = None
        self.compare_reference = False
        self.spec_path = None
        self.original_spec = None  # a spec file used as is (examples/fns_fe_5min.json)
        self.conflicts = static_conflicts(spec) if spec is not None else []

    def match_keys(self):
        keys = set(self.keys)
        for _message, fragments in self.conflicts:
            keys.update(fragments)
        if self.mode == "trace":
            keys.update(["trace", "options.mode"])
        return sorted(keys)


def schedule_for(name, ctx):
    return {"base": ctx.base["schedule"], "short": SHORT_SCHEDULE, "long": LONG_SCHEDULE}[name]


def make_case(ctx, case_id, material_key, material_label, mode, optset, schedule_name, func):
    m = MATERIALS[material_key]
    spec = ctx.spec_skeleton(f"P140 H2 {case_id}", schedule_for(schedule_name, ctx))
    spec["material"] = {"mass_g": 1.0, "basis": "wt_percent", "composition": copy.deepcopy(m["composition"])}
    spec["options"]["mode"] = mode
    try:
        keys = func(spec, m, ctx)
    except Skip as skip:
        case = Case(case_id, material_label, mode, optset, None, [], schedule_name)
        case.skip = str(skip)
        return case
    return Case(case_id, material_label, mode, optset, spec, keys, schedule_name)


def build_cases(ctx, only=None):
    cases = []
    example = Case("fns_fe_5min.example.baseline.auto", "natural Fe", "auto", "baseline", None, [], "base")
    example.original_spec = BASE_SPEC
    example.spec = json.loads(BASE_SPEC.read_text())
    example.compare_reference = True
    cases.append(example)
    for key in MATERIAL_ORDER:
        for name, schedule_name, func, modes in OPTION_SETS:
            for mode in modes:
                case = make_case(ctx, f"{key}.{name}.{mode}", key, MATERIALS[key]["label"], mode, name,
                                 schedule_name, func)
                case.compare_reference = name == "baseline" and mode == "auto"
                cases.append(case)
    for key in LONG_MATERIALS:
        for name, func in (("baseline", opt_baseline), ("summary_depletion", opt_summary_depletion)):
            for mode in ("trace", "coupled"):
                cases.append(make_case(ctx, f"{key}_long.{name}.{mode}", key, MATERIALS[key]["label"] + ", 35 steps",
                                       mode, name + "_long", "long", func))
    for name, schedule_name, func in PROBES:
        cases.append(make_case(ctx, f"fe.{name}.coupled", "fe", MATERIALS["fe"]["label"], "coupled", name,
                               schedule_name, func))
    if only:
        wanted = [piece for piece in only.split(",") if piece]
        cases = [c for c in cases if any(piece in c.id for piece in wanted)]
    return cases


def write_specs(cases, workdir):
    for case in cases:
        folder = workdir / "cases" / case.id
        folder.mkdir(parents=True, exist_ok=True)
        if case.original_spec is not None:
            case.spec_path = case.original_spec
        elif case.spec is not None:
            case.spec_path = folder / "spec.json"
            case.spec_path.write_text(json.dumps(case.spec))


# ------------------------------------------------------------------------------ result parsing and invariants


class NonFinite(ValueError):
    pass


def _reject_constant(token):
    raise NonFinite(f"non-finite JSON token {token}")


# Result fields that always hold a number. serde_json writes a non-finite f64 as `null`, so a null in one of them is
# a NaN or infinity that the token scan cannot see.
NUMERIC_KEYS = frozenset((
    "atoms_per_g", "atoms", "grams", "activity_Bq", "alpha_W", "beta_W", "gamma_W", "heat_W", "rate_per_s", "share",
    "removal_rate_per_s", "total_atoms_per_g", "paths_atoms_per_g", "residual_atoms_per_g", "leakage_atoms_per_g",
    "t_s", "flux_weighted_time_s", "pathway_closure", "atoms_start_per_g", "atoms_end_per_g", "initial_atoms_per_g"))
NUMERIC_MAPS = frozenset(("activity_Bq_per_g", "heat_W_per_g"))


def null_numbers(obj, limit=5):
    found = []
    stack = [("", obj)]
    while stack and len(found) < limit:
        path, node = stack.pop()
        if isinstance(node, dict):
            numeric_map = path.rsplit("/", 1)[-1] in NUMERIC_MAPS
            for key, value in node.items():
                if value is None and (key in NUMERIC_KEYS or numeric_map):
                    found.append(f"{path}/{key}")
                elif isinstance(value, (dict, list)):
                    stack.append((f"{path}/{key}", value))
        elif isinstance(node, list):
            stack.extend((f"{path}[{i}]", v) for i, v in enumerate(node) if isinstance(v, (dict, list)))
    return found


def load_result(data):
    """Parse a result, refusing NaN/Infinity tokens (parse_constant), numbers that overflow to infinity and nulls in
    numeric fields."""
    obj = json.loads(data.decode("utf-8"), parse_constant=_reject_constant)
    nulls = null_numbers(obj)
    if nulls:
        raise NonFinite(f"null in a numeric field (serde writes NaN and infinity as null): {nulls}")
    count = 0
    stack = [obj]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
        elif isinstance(node, float) and not math.isfinite(node):
            count += 1
    if count:
        paths = []
        walk = [("", obj)]
        while walk and len(paths) < 5:
            path, node = walk.pop()
            if isinstance(node, dict):
                walk.extend((f"{path}/{k}", v) for k, v in node.items())
            elif isinstance(node, list):
                walk.extend((f"{path}[{i}]", v) for i, v in enumerate(node))
            elif isinstance(node, float) and not math.isfinite(node):
                paths.append(path)
        raise NonFinite(f"{count} non-finite number(s), e.g. {paths}")
    return obj


class Recorder:
    """Collects comparisons: how many were made, the worst relative gap, and the first few that failed."""

    def __init__(self):
        self.n = 0
        self.worst = 0.0
        self.bad = []
        self.total_bad = 0

    def close(self, where, a, b, tol=TOL):
        self.n += 1
        try:
            gap = rel_gap(a, b)
        except TypeError:
            self.flag(where, f"not comparable: {a!r} vs {b!r}")
            return
        if gap == gap:
            self.worst = max(self.worst, gap)
        if not gap <= tol:
            self.total_bad += 1
            if len(self.bad) < MAX_LISTED:
                self.bad.append({"where": where, "a": a, "b": b, "relative_gap": gap})

    def flag(self, where, message):
        self.n += 1
        self.total_bad += 1
        if len(self.bad) < MAX_LISTED:
            self.bad.append({"where": where, "problem": message})

    def verdict(self, **extra):
        out = {"applicable": True, "pass": self.total_bad == 0, "checked": self.n,
               "worst_relative_gap": self.worst, "n_problems": self.total_bad, "problems": self.bad}
        out.update(extra)
        return out


def not_applicable(why):
    return {"applicable": False, "pass": True, "why": why}


def check_inventory(result):
    rec = Recorder()
    for si, step in enumerate(result["steps"], start=1):
        rows = step["inventory"]
        total = math.fsum(r["atoms_per_g"] for r in rows)
        floor = -TOL * abs(total)
        for r in rows:
            rec.n += 1
            if r["atoms_per_g"] < floor:
                rec.total_bad += 1
                if len(rec.bad) < MAX_LISTED:
                    rec.bad.append({"where": f"step {si} {r['nuclide']}", "atoms_per_g": r["atoms_per_g"],
                                    "step_tracked_total": total})
    return rec.verdict()


MODE_FIELDS = ("beta_minus_Bq", "ec_beta_plus_Bq", "isomeric_transition_Bq", "alpha_Bq", "neutron_Bq",
               "spontaneous_fission_Bq", "proton_Bq", "other_Bq")
SCALED_FIELDS = ("activity_Bq", "activity_Ci", "alpha_W", "beta_W", "gamma_W", "heat_W", "activity_excl_H3_Bq",
                 "heat_excl_H3_W")


def summary_options(spec):
    s = spec.get("summary")
    if s is None:
        return None
    return {"nuclides": s.get("nuclides", True), "elements": s.get("elements", True),
            "min_atoms": s.get("min_atoms", 0.0), "include_stable": s.get("include_stable", True),
            "depletion": s.get("depletion", False)}


def check_summary(result, spec):
    options = summary_options(spec)
    steps = result["steps"]
    if options is None:
        stray = [st["step"] for st in steps if "summary" in st]
        rec = Recorder()
        rec.n = 1
        if stray:
            rec.flag("steps[].summary", f"emitted without a summary section on steps {stray[:5]}")
        return rec.verdict(note="no summary requested")
    rec = Recorder()
    extras = Recorder()
    full_table = options["nuclides"] and options["min_atoms"] == 0 and options["include_stable"]
    mass = spec["material"].get("mass_g", 1.0)
    density = spec["material"].get("density_g_cm3")
    for si, step in enumerate(steps, start=1):
        s = step.get("summary")
        if s is None:
            rec.flag(f"step {si}", "summary block absent")
            continue
        totals = s["totals"]
        rows = s.get("nuclides")
        elements = s.get("elements")
        if (rows is None) == options["nuclides"]:
            rec.flag(f"step {si}", f"nuclide table presence disagrees with summary.nuclides={options['nuclides']}")
        if (elements is None) == options["elements"]:
            rec.flag(f"step {si}", f"element table presence disagrees with summary.elements={options['elements']}")
        w = f"step {si}"
        quantities = (("activity_Bq", "activity_Bq"), ("alpha_W", "alpha_W"), ("beta_W", "beta_W"),
                      ("gamma_W", "gamma_W"), ("grams", "mass_g"))
        if rows is not None:
            if full_table:
                for field, total_field in quantities:
                    rec.close(f"{w} sum(nuclides.{field}) vs totals.{total_field}",
                              math.fsum(r[field] for r in rows), totals[total_field])
                rec.close(f"{w} len(nuclides) vs totals.n_nuclides", float(len(rows)), float(totals["n_nuclides"]))
                rec.close(f"{w} sum(activity of non-H3) vs totals.activity_excl_H3_Bq",
                          math.fsum(r["activity_Bq"] for r in rows if (r["Z"], r["A"], r["LISO"]) != (1, 3, 0)),
                          totals["activity_excl_H3_Bq"])
                rec.close(f"{w} sum(heat of non-H3) vs totals.heat_excl_H3_W",
                          math.fsum(r["alpha_W"] + r["beta_W"] + r["gamma_W"] for r in rows
                                    if (r["Z"], r["A"], r["LISO"]) != (1, 3, 0)), totals["heat_excl_H3_W"])
                rec.close(f"{w} sum(activity with gamma_W > 0) vs totals.gamma_Bq",
                          math.fsum(r["activity_Bq"] for r in rows if r["gamma_W"] > 0.0), totals["gamma_Bq"])
            else:
                # a filtered table is a subset: it may not exceed the totals
                for field, total_field in quantities:
                    rec.n += 1
                    got = math.fsum(r[field] for r in rows)
                    if got > totals[total_field] * (1.0 + TOL):
                        rec.flag(f"{w} filtered sum(nuclides.{field})",
                                 f"{got!r} exceeds totals.{total_field} {totals[total_field]!r}")
                rec.n += 1
                if len(rows) > totals["n_nuclides"]:
                    rec.flag(f"{w} filtered table", "more rows than totals.n_nuclides")
        if elements is not None:
            for field, total_field in quantities:
                rec.close(f"{w} sum(elements.{field}) vs totals.{total_field}",
                          math.fsum(e[field] for e in elements), totals[total_field])
            if rows is not None and full_table:
                rec.close(f"{w} sum(elements.atoms) vs sum(nuclides.atoms)", math.fsum(e["atoms"] for e in elements),
                          math.fsum(r["atoms"] for r in rows))
        # derived totals
        rec.close(f"{w} heat_W vs alpha+beta+gamma", totals["heat_W"],
                  totals["alpha_W"] + totals["beta_W"] + totals["gamma_W"])
        rec.close(f"{w} activity_Ci vs activity_Bq/3.7e10", totals["activity_Ci"], totals["activity_Bq"] / 3.7e10)
        modes = totals["activity_by_mode"]
        rec.close(f"{w} sum(activity_by_mode) vs activity_Bq", math.fsum(modes[f] for f in MODE_FIELDS),
                  totals["activity_Bq"])
        rec.close(f"{w} alpha_Bq vs activity_by_mode.alpha_Bq", totals["alpha_Bq"], modes["alpha_Bq"])
        rec.close(f"{w} beta_Bq vs beta-,EC/beta+,IT modes", totals["beta_Bq"],
                  modes["beta_minus_Bq"] + modes["ec_beta_plus_Bq"] + modes["isomeric_transition_Bq"])
        dominant = s["dominant"]
        for key, field in (("activity", "activity_Bq"), ("heat", "heat_W"), ("alpha_heat", "alpha_W"),
                           ("beta_heat", "beta_W"), ("gamma_heat", "gamma_W")):
            rec.close(f"{w} dominant.{key}.total vs totals.{field}", dominant[key]["total"], totals[field])
        rec.close(f"{w} absolute.multiplier vs material.mass_g", totals["absolute"]["multiplier"], mass)
        for field in SCALED_FIELDS:
            rec.close(f"{w} absolute.{field}", totals["absolute"][field], totals["absolute"]["multiplier"] * totals[field])
        rec.n += 1
        if (density is None) != ("per_cm3" not in totals):
            rec.flag(f"{w} per_cm3", "presence disagrees with material.density_g_cm3")
        elif density is not None:
            rec.close(f"{w} per_cm3.multiplier vs material.density_g_cm3", totals["per_cm3"]["multiplier"], density)
            for field in SCALED_FIELDS:
                rec.close(f"{w} per_cm3.{field}", totals["per_cm3"][field], density * totals[field])
        # informational: the summary against the main output
        extras.close(f"{w} totals.activity_Bq vs sum(activity_Bq_per_g)", totals["activity_Bq"],
                     math.fsum(step["activity_Bq_per_g"].values()), EXTRA_TOL)
        extras.close(f"{w} totals.leakage_atoms vs leakage_atoms_per_g", totals["leakage_atoms"],
                     step["leakage_atoms_per_g"], EXTRA_TOL)
        extras.close(f"{w} totals.heat_W vs heat_W_per_g.total", totals["heat_W"], step["heat_W_per_g"]["total"],
                     EXTRA_TOL)
    verdict = rec.verdict(full_table=bool(full_table))
    verdict["informational"] = {"checked": extras.n, "n_mismatches": extras.total_bad, "worst": extras.worst,
                                "examples": extras.bad[:3]}
    return verdict


def check_depletion(result, spec):
    options = summary_options(spec)
    if not options or not options["depletion"]:
        rec = Recorder()
        rec.n = 1
        stray = [st["step"] for st in result["steps"] if "depletion" in (st.get("summary") or {})]
        if stray:
            rec.flag("steps[].summary.depletion", f"emitted without summary.depletion on steps {stray[:5]}")
        return rec.verdict(applicable=bool(rec.total_bad), note="no depletion requested")
    rec = Recorder()
    extras = Recorder()
    expected_model = "trace_analytic" if result["mode"] == "trace" else "coupled"
    for si, step in enumerate(result["steps"], start=1):
        table = (step.get("summary") or {}).get("depletion")
        if table is None:
            rec.flag(f"step {si}", "depletion table absent")
            continue
        rec.n += 1
        if table["model"] != expected_model:
            rec.flag(f"step {si}", f"model {table['model']!r}, expected {expected_model!r} for mode {result['mode']}")
        rec.n += 1
        if not table["nuclides"]:
            rec.flag(f"step {si}", "no initial nuclides listed")
        for row in table["nuclides"]:
            where = f"step {si} {row['nuclide']}"
            channels = row["channels"]
            if not channels:
                rec.n += 1
                if row["removal_rate_per_s"] > 0.0:
                    rec.flag(where, "positive removal rate with no channels")
                continue
            rec.close(f"{where} sum(channel shares) vs 1", math.fsum(c["share"] for c in channels), 1.0)
            extras.close(f"{where} sum(channel rate_per_s) vs removal_rate_per_s",
                         math.fsum(c["rate_per_s"] for c in channels), row["removal_rate_per_s"], EXTRA_TOL)
    verdict = rec.verdict()
    verdict["informational"] = {"checked": extras.n, "n_mismatches": extras.total_bad, "worst": extras.worst,
                                "examples": extras.bad[:3]}
    return verdict


def check_routes(result, spec):
    routes = result.get("pathway_routes")
    if "pathways" not in spec:
        rec = Recorder()
        rec.n = 1
        if routes is not None:
            rec.flag("pathway_routes", "emitted without a pathways section")
        return rec.verdict(applicable=bool(rec.total_bad), note="no pathways requested")
    rec = Recorder()
    extras = Recorder()
    if routes is None:
        rec.flag("pathway_routes", "absent although the spec declares pathways")
        return rec.verdict()
    final = {r["nuclide"]: r["atoms_per_g"] for r in result["steps"][-1]["inventory"]}
    for target in routes["targets"]:
        w = target["target"]
        listed = math.fsum(p["atoms_per_g"] for p in target["paths"])
        total, paths, residual = target["total_atoms_per_g"], target["paths_atoms_per_g"], target["residual_atoms_per_g"]
        scale = max(abs(total), math.fsum(abs(p["atoms_per_g"]) for p in target["paths"]), abs(paths))
        rec.close(f"{w} total vs sum(routes)+residual", total, listed + residual)
        rec.close(f"{w} total vs paths_atoms_per_g+residual", total, paths + residual)
        rec.close(f"{w} paths_atoms_per_g vs sum(routes)", paths, listed)
        rec.close(f"{w} sum(generic) vs paths_atoms_per_g", math.fsum(g["atoms_per_g"] for g in target["generic"]),
                  paths)
        if total > 0.0:
            rec.close(f"{w} explained+residual fractions vs 1", target["explained_fraction"]
                      + target["residual_fraction"], 1.0)
        rec.n += 1
        if scale > 0.0 and abs(total - (listed + residual)) > TOL * scale:
            rec.flag(w, f"closure gap {abs(total - (listed + residual)):.3e} exceeds {TOL:g} of the largest term")
        extras.close(f"{w} total_atoms_per_g vs final inventory", total, final.get(w, 0.0), EXTRA_TOL)
    verdict = rec.verdict(n_targets=len(routes["targets"]))
    verdict["informational"] = {"checked": extras.n, "n_mismatches": extras.total_bad, "worst": extras.worst,
                                "examples": extras.bad[:3]}
    return verdict


def check_extras(result, case_mode):
    """Cross-checks the protocol does not list; recorded, never part of the verdict."""
    notes = {}
    if case_mode in ("trace", "coupled") and result.get("mode") != case_mode:
        notes["mode_not_honoured"] = f"requested {case_mode}, ran {result.get('mode')}"
    negative = []
    for step in result["steps"]:
        for key in ("leakage_atoms_per_g", "removed_atoms_per_g"):
            value = step.get(key)
            if value is not None and value < 0.0:
                negative.append(f"step {step['step']} {key}={value!r}")
        for name, value in step["activity_Bq_per_g"].items():
            if value < 0.0:
                negative.append(f"step {step['step']} activity {name}={value!r}")
                break
    if negative:
        notes["negative_values"] = negative[:5]
    return notes


def mask_ms(data):
    return MS.sub(b'"ms":0', data)


# P140 Amendment B: materials with radioactive bulk nuclides, whose pathway attribution repair 5 changes.
DECAY_FED_MATERIALS = ("w", "ss316", "u")
AMENDMENT_B_LEDGER = ("spontaneous_fission_branches_to_leakage", "unmodelled_decay_branches_to_leakage")


def reference_compatible(case_id, data_ref, data_cand):
    """(pass, problems) of the Amendment B comparison of a baseline result with the reference's."""
    ref, cand = json.loads(data_ref), json.loads(data_cand)
    problems = []
    sf_ref = ref.get("ledger", {}).get(AMENDMENT_B_LEDGER[0])
    sf_cand = cand.get("ledger", {}).get(AMENDMENT_B_LEDGER[0])
    # Amendment E: the key is emitted only when nonzero, so absence reads as 0.
    unmodelled = cand.get("ledger", {}).get(AMENDMENT_B_LEDGER[1], 0)
    if not isinstance(unmodelled, int):
        problems.append(f"candidate ledger lacks an integer {AMENDMENT_B_LEDGER[1]}: {unmodelled!r}")
    elif not (isinstance(sf_ref, int) and isinstance(sf_cand, int)):
        problems.append(f"spontaneous-fission counts not integers: reference {sf_ref!r}, candidate {sf_cand!r}")
    elif sf_cand > sf_ref or sf_ref - sf_cand > unmodelled:
        problems.append(f"spontaneous-fission count {sf_ref} -> {sf_cand} is not a fall of at most the "
                        f"unmodelled count {unmodelled}")
    decay_fed = case_id.split(".", 1)[0] in DECAY_FED_MATERIALS
    if decay_fed:
        c_ref, c_cand = ref.get("pathway_closure"), cand.get("pathway_closure")
        if isinstance(c_ref, (int, float)) and isinstance(c_cand, (int, float)) and c_cand > c_ref:
            problems.append(f"pathway_closure grew: {c_ref} -> {c_cand}")
    for doc in (ref, cand):
        doc.pop("ms", None)
        for key in AMENDMENT_B_LEDGER:
            doc.get("ledger", {}).pop(key, None)
        if decay_fed:
            doc.pop("pathways", None)
            doc.pop("pathway_closure", None)
    strip_ms(ref)
    strip_ms(cand)
    if ref != cand:
        diff = [k for k in sorted(set(ref) | set(cand)) if ref.get(k) != cand.get(k)]
        problems.append(f"differs outside the Amendment B fields in: {diff[:10]}")
    return not problems, problems


def strip_ms(node):
    if isinstance(node, dict):
        node.pop("ms", None)
        for value in node.values():
            strip_ms(value)
    elif isinstance(node, list):
        for value in node:
            strip_ms(value)


def first_difference(a, b):
    la, lb = a.splitlines(), b.splitlines()
    for i, (x, y) in enumerate(zip(la, lb), start=1):
        if x != y:
            return {"line": i, "a": x[:160].decode("utf-8", "replace"), "b": y[:160].decode("utf-8", "replace")}
    return {"line": min(len(la), len(lb)) + 1, "a": f"{len(la)} lines", "b": f"{len(lb)} lines"}


# ------------------------------------------------------------------------------ running


def run_binary(binary, spec_path, out_path, timeout):
    started = time.monotonic()
    try:
        proc = subprocess.run([str(binary), "run", str(spec_path), str(out_path)], cwd=str(ROOT), capture_output=True,
                              text=True, errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired as error:
        stderr = error.stderr.decode("utf-8", "replace") if isinstance(error.stderr, bytes) else (error.stderr or "")
        return {"rc": None, "seconds": time.monotonic() - started, "timed_out": True, "stderr": stderr}
    return {"rc": proc.returncode, "seconds": time.monotonic() - started, "timed_out": False,
            "stderr": proc.stderr + proc.stdout}


def classify_refusal(case, run):
    """(outcome, detail) for a non-zero exit."""
    text = run["stderr"]
    lowered = text.lower()
    if run["timed_out"]:
        return "fail", "timed out"
    if run["rc"] not in (1, 2) or "panicked at" in lowered:
        return "fail", f"crash, not a refusal (exit status {run['rc']})"
    named = [key for key in case.match_keys() if key.lower() in lowered]
    if named:
        return "refused-ok", {"named": named}
    return "fail", {"problem": "refusal does not name any option involved", "expected_any_of": case.match_keys()}


def run_case(case, reference, candidate, args, workdir):
    folder = workdir / "cases" / case.id
    record = {"id": case.id, "material": case.material, "mode": case.mode, "option_set": case.optset,
              "schedule": case.schedule_name, "spec_sha256": sha256_file(case.spec_path) if case.spec_path else None,
              "documented_refusal": [message for message, _ in case.conflicts], "invariants": {}}
    if case.skip:
        record.update(outcome="skipped", message=case.skip, wall_s=0.0)
        return record
    started = time.monotonic()
    out_a, out_b, out_ref = folder / "result.a.json", folder / "result.b.json", folder / "result.ref.json"
    first = run_binary(candidate, case.spec_path, out_a, args.timeout)
    record["runs"] = [{"rc": first["rc"], "seconds": round(first["seconds"], 3)}]
    if first["rc"] != 0:
        outcome, detail = classify_refusal(case, first)
        record.update(outcome=outcome, message=clip(first["stderr"]), detail=detail)
        record["wall_s"] = round(time.monotonic() - started, 3)
        return record
    # success: the invariants
    data_a = out_a.read_bytes()
    record["result_bytes"] = len(data_a)
    invariants = record["invariants"]
    try:
        result = load_result(data_a)
        invariants["finite"] = {"applicable": True, "pass": True}
    except (NonFinite, ValueError) as error:
        invariants["finite"] = {"applicable": True, "pass": False, "problem": str(error)[:300]}
        result = None
    if result is not None:
        for name, check in (("inventory", lambda: check_inventory(result)),
                            ("summary", lambda: check_summary(result, case.spec)),
                            ("depletion", lambda: check_depletion(result, case.spec)),
                            ("routes", lambda: check_routes(result, case.spec))):
            try:
                invariants[name] = check()
            except (KeyError, TypeError, IndexError, ValueError) as error:
                invariants[name] = {"applicable": True, "pass": False,
                                    "problem": f"result has an unexpected shape: {type(error).__name__}: {error}"}
        record["result_mode"] = result.get("mode")
        record["n_steps"] = len(result.get("steps", []))
        extras = check_extras(result, case.mode)
        if extras:
            record["extras"] = extras
    second = run_binary(candidate, case.spec_path, out_b, args.timeout)
    record["runs"].append({"rc": second["rc"], "seconds": round(second["seconds"], 3)})
    if second["rc"] != 0:
        invariants["repeat"] = {"applicable": True, "pass": False,
                                "problem": f"the repeated run failed: {clip(second['stderr'], 300)}"}
    else:
        data_b = out_b.read_bytes()
        same = mask_ms(data_a) == mask_ms(data_b)
        invariants["repeat"] = {"applicable": True, "pass": same}
        if not same:
            invariants["repeat"]["first_difference"] = first_difference(mask_ms(data_a), mask_ms(data_b))
    if case.compare_reference:
        ref = run_binary(reference, case.spec_path, out_ref, args.timeout)
        record["runs"].append({"rc": ref["rc"], "seconds": round(ref["seconds"], 3), "binary": "reference"})
        if ref["rc"] != 0:
            invariants["reference_identical"] = {"applicable": True, "pass": False,
                                                 "problem": f"the reference run failed: {clip(ref['stderr'], 300)}"}
        else:
            data_ref = out_ref.read_bytes()
            same = mask_ms(data_a) == mask_ms(data_ref)
            if same:
                invariants["reference_identical"] = {"applicable": True, "pass": True}
            else:
                compatible, problems = reference_compatible(case.id, data_ref, data_a)
                invariants["reference_compatible"] = {
                    "applicable": True, "pass": compatible, "amendment": "P140 Amendment B",
                    "first_difference": first_difference(mask_ms(data_ref), mask_ms(data_a))}
                if problems:
                    invariants["reference_compatible"]["problems"] = problems
    ok = all(v["pass"] for v in invariants.values())
    record["outcome"] = "pass" if ok else "fail"
    if ok and case.conflicts:
        record["warning"] = "the specification documents a refusal for this combination but the run succeeded"
    record["wall_s"] = round(time.monotonic() - started, 3)
    if ok and not args.keep:
        for path in (out_a, out_b, out_ref):
            if path.exists():
                path.unlink()
    return record


def case_line(record):
    outcome = record["outcome"]
    tag = {"pass": "PASS", "refused-ok": "REFUSED-OK", "fail": "FAIL", "skipped": "SKIPPED"}[outcome]
    detail = ""
    if outcome == "fail":
        bad = [name for name, v in record.get("invariants", {}).items() if not v["pass"]]
        detail = " failed: " + ", ".join(bad) if bad else " " + json.dumps(record.get("detail", record.get("message")))[:200]
    elif outcome == "refused-ok":
        detail = " names " + ",".join(record["detail"]["named"])
    elif outcome == "skipped":
        detail = " " + record["message"]
    elif record.get("warning"):
        detail = " (documented refusal not raised)"
    return f"{tag:<10} {record['id']}  {record['wall_s']:.1f}s{detail}"


# ------------------------------------------------------------------------------ static validation (--self-check)


def rust_struct_fields(text, name):
    match = re.search(r"pub struct " + name + r"\s*\{(.*?)\n\}", text, re.S)
    if not match:
        raise ValueError(f"struct {name} not found")
    fields = set()
    lines = match.group(1).splitlines()
    skip_next = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#[serde(skip)]") or stripped == "#[serde(skip)]":
            skip_next = True
            continue
        m = re.match(r"\s{4}(?:pub\s+)?([A-Za-z_][A-Za-z_0-9]*)\s*:\s", line)
        if m:
            if not skip_next:
                fields.add(m.group(1))
            skip_next = False
    return fields


# JSON path -> (struct, file). "[]" marks a list of structs.
SCHEMA = {
    "": ("Spec", "spec"), "library": ("LibraryRef", "spec"), "decay": ("DecayRef", "spec"),
    "material": ("Material", "spec"), "spectrum": ("Spectrum", "spec"), "schedule[]": ("StepWire", "spec"),
    "schedule[].spectrum": ("Spectrum", "spec"), "options": ("Options", "spec"),
    "options.fission": ("FissionCountOptions", "spec"), "options.screen": ("ScreenOptions", "spec"),
    "photon": ("PhotonOptions", "spec"), "photon.response": ("HashedFileRef", "spec"),
    "photon.point_source": ("PointSourceOptions", "spec"),
    "photon.bremsstrahlung": ("BremsstrahlungOptions", "spec"), "summary": ("SummaryOptions", "spec"),
    "kerma": ("KermaOptions", "spec"), "kerma.table": ("HashedFileRef", "spec"),
    "uncertainty": ("UncertaintyOptions", "spec"), "uncertainty.covariance": ("HashedFileRef", "spec"),
    "self_shielding": ("SelfShieldingOptions", "spec"),
    "self_shielding.geometry": ("SelfShieldingGeometry", "spec"),
    "self_shielding.geometry.resonance_files[]": ("HashedFileRef", "spec"),
    "overrides[]": ("NamedOverride", "spec"), "pathways": ("PathwayOptions", "pathways"),
}


def validate_schema(spec, fields_by_struct, label):
    """Unknown or misplaced keys against the Rust structs (deny_unknown_fields). Returns a list of problems."""
    problems = []

    def visit(node, path):
        schema = SCHEMA.get(path)
        if schema is None:
            return
        if isinstance(node, list):
            return
        if not isinstance(node, dict):
            problems.append(f"{label}: {path or 'spec'} is not an object")
            return
        allowed = fields_by_struct[schema[0]]
        for key, value in node.items():
            if key not in allowed:
                problems.append(f"{label}: {path or 'spec'}.{key} is not a field of {schema[0]}")
                continue
            child = f"{path}.{key}" if path else key
            if isinstance(value, list):
                if f"{child}[]" in SCHEMA:
                    for item in value:
                        visit(item, f"{child}[]")
                        if f"{child}[].spectrum" in SCHEMA and isinstance(item, dict) and "spectrum" in item:
                            visit(item["spectrum"], f"{child}[].spectrum")
                        if child == "self_shielding.geometry.resonance_files":
                            pass
            else:
                visit(value, child)

    visit(spec, "")
    return problems


def rule_problems(case):
    """Structural rules of Spec::validate that can be checked without the engine."""
    spec, problems = case.spec, []
    for key in ("spec", "projectile", "library", "decay", "material", "spectrum", "schedule", "options"):
        if key not in spec:
            problems.append(f"missing {key}")
    if spec.get("spec") != "actinv-spec-1":
        problems.append("spec version")
    for path in (spec["library"]["path"], spec["decay"]["primary"], spec["decay"].get("fallback")):
        if path and not Path(path).is_file():
            problems.append(f"missing data file {path}")
    if len(spec["spectrum"]["flux_per_group"]) != 709:
        problems.append("spectrum is not 709 groups")
    composition = spec["material"]["composition"]
    if abs(sum(composition.values()) - 100.0) > 1e-9:
        problems.append(f"composition sums to {sum(composition.values())}")
    for key in composition:
        if key.capitalize() not in ELEMENTS:
            problems.append(f"composition key {key} is not an element symbol")
    for index, step in enumerate(spec["schedule"], start=1):
        if not re.fullmatch(r"[0-9.eE+-]+ ?(s|min|h|d|y)", step["dt"]):
            problems.append(f"step {index} dt {step['dt']!r}")
        drivers = [k for k in ("flux", "wall_loading_MW_m2", "power_W_cm3", "power_W_per_g") if k in step]
        if "flux" in drivers and len(drivers) > 1 and (step["flux"] != 0.0 or "wall_loading_MW_m2" in drivers):
            problems.append(f"step {index} has {drivers}")
        for element, kept in step.get("partition", {}).items():
            if element not in ELEMENTS or not 0.0 <= kept <= 1.0:
                problems.append(f"step {index} partition {element}={kept}")
    if spec["options"].get("mode") not in ("auto", "trace", "coupled"):
        problems.append("options.mode")
    return problems


def self_check_static(ctx, cases, workdir):
    spec_text = SPEC_RS.read_text()
    path_text = PATHWAYS_RS.read_text()
    fields = {}
    for key, (struct, source) in SCHEMA.items():
        fields[struct] = rust_struct_fields(spec_text if source == "spec" else path_text, struct)
    problems = []
    # the struct extraction itself: a few fields that must be there
    for struct, must in (("Spec", {"schedule", "overrides", "pathways", "kerma", "summary"}),
                         ("StepWire", {"dt", "flux", "partition", "power_W_per_g", "wall_loading_MW_m2"}),
                         ("PathwayOptions", {"targets", "by_step"}), ("SelfShieldingGeometry", {"resonance_files"}),
                         ("NamedOverride", {"nuclide", "kind", "mt", "value", "units"})):
        if not must <= fields[struct]:
            problems.append(f"struct parse of {struct} lost {sorted(must - fields[struct])}")
    seen = set()
    for case in cases:
        if case.id in seen:
            problems.append(f"duplicate case id {case.id}")
        seen.add(case.id)
        if case.skip:
            continue
        problems += validate_schema(case.spec, fields, case.id)
        problems += [f"{case.id}: {p}" for p in rule_problems(case)]
        probe = case.optset.startswith("refuse_")
        if probe and not case.conflicts:
            problems.append(f"{case.id}: a probe without a documented conflict")
        if not probe and any("trace" not in message for message, _ in case.conflicts):
            problems.append(f"{case.id}: unexpected documented conflict {[m for m, _ in case.conflicts]}")
        has_partition = any("partition" in step for step in case.spec["schedule"])
        if has_partition and case.mode == "trace" and not any("trace" in m for m, _ in case.conflicts):
            problems.append(f"{case.id}: partition with trace not recognised as a documented conflict")
    # data the options name
    for key in MATERIAL_ORDER:
        m = MATERIALS[key]
        for nuclide, mt, _ in m["scale"] + m["absolute"]:
            if not ctx.facts.has_reaction(nuclide, mt):
                problems.append(f"{key}: library has no {nuclide} MT={mt} row")
        for element in m["composition"]:
            if element not in ATOMIC_MASS:
                problems.append(f"{key}: no atomic mass for {element}")
            if element not in ctx.kerma_rows:
                problems.append(f"{key}: kerma table lacks {element}")
        if not m["composition"].get(m["bulk"]):
            problems.append(f"{key}: bulk element {m['bulk']} is not in the composition")
    if ctx.response:
        elements = json.loads(Path(ctx.response["path"]).read_text())["element_mass_attenuation"]
        for key in MATERIAL_ORDER:
            missing = [e for e in MATERIALS[key]["composition"] if e not in elements]
            if missing:
                problems.append(f"{key}: photon response lacks {missing}")
    table = json.loads(Path(ctx.kerma["path"]).read_text())
    if table["boundaries_eV"] != ctx.facts.bounds or len(table["boundaries_eV"]) != 710:
        problems.append("kerma table boundaries differ from the library's")
    for row in table["targets"].values():
        if len(row) != 709:
            problems.append("kerma row length")
            break
    return problems


def selftest_invariants():
    """Unit checks of the invariant code on a synthetic result: it must pass, and each perturbation must be caught."""
    problems = []

    def nuclide_row(name, z, a, liso, atoms, activity, alpha=0.0, beta=0.0, gamma=0.0, grams=None):
        return {"nuclide": name, "Z": z, "A": a, "LISO": liso, "atoms": atoms,
                "grams": grams if grams is not None else atoms * 1e-23, "activity_Bq": activity, "alpha_W": alpha,
                "beta_W": beta, "gamma_W": gamma}

    rows = [nuclide_row("Fe56", 26, 56, 0, 9.0e21, 0.0), nuclide_row("Mn56", 25, 56, 0, 3.0e10, 1.5e6, 0, 1e-7, 2e-7),
            nuclide_row("H3", 1, 3, 0, 1.0e12, 1.1e3, 0, 5e-12, 0.0)]
    totals_src = {k: math.fsum(r[k] for r in rows) for k in ("activity_Bq", "alpha_W", "beta_W", "gamma_W")}
    heat = totals_src["alpha_W"] + totals_src["beta_W"] + totals_src["gamma_W"]
    excl_act = rows[1]["activity_Bq"]
    excl_heat = rows[1]["beta_W"] + rows[1]["gamma_W"]
    elements = [{"Z": 1, "atoms": rows[2]["atoms"], "grams": rows[2]["grams"], "activity_Bq": rows[2]["activity_Bq"],
                 "alpha_W": 0.0, "beta_W": rows[2]["beta_W"], "gamma_W": 0.0},
                {"Z": 25, "atoms": rows[1]["atoms"], "grams": rows[1]["grams"], "activity_Bq": rows[1]["activity_Bq"],
                 "alpha_W": 0.0, "beta_W": rows[1]["beta_W"], "gamma_W": rows[1]["gamma_W"]},
                {"Z": 26, "atoms": rows[0]["atoms"], "grams": rows[0]["grams"], "activity_Bq": 0.0,
                 "alpha_W": 0.0, "beta_W": 0.0, "gamma_W": 0.0}]
    mass = 2.0

    def scaled(m):
        return {"multiplier": m, "activity_Bq": m * totals_src["activity_Bq"],
                "activity_Ci": m * totals_src["activity_Bq"] / 3.7e10, "alpha_W": m * totals_src["alpha_W"],
                "beta_W": m * totals_src["beta_W"], "gamma_W": m * totals_src["gamma_W"], "heat_W": m * heat,
                "activity_excl_H3_Bq": m * excl_act, "heat_excl_H3_W": m * excl_heat}

    modes = {f: 0.0 for f in MODE_FIELDS}
    modes["beta_minus_Bq"] = totals_src["activity_Bq"]
    totals = {"activity_Bq": totals_src["activity_Bq"], "activity_Ci": totals_src["activity_Bq"] / 3.7e10,
              "alpha_W": totals_src["alpha_W"], "beta_W": totals_src["beta_W"], "gamma_W": totals_src["gamma_W"],
              "heat_W": heat, "activity_by_mode": modes, "alpha_Bq": 0.0, "beta_Bq": totals_src["activity_Bq"],
              "gamma_Bq": rows[1]["activity_Bq"], "activity_excl_H3_Bq": excl_act, "heat_excl_H3_W": excl_heat,
              "mass_g": math.fsum(r["grams"] for r in rows), "leakage_atoms": 0.0, "n_nuclides": 3,
              "absolute": scaled(mass), "per_cm3": scaled(8.0)}
    dominant = {key: {"total": totals[field], "top": [], "n_for_90_percent": 1, "n_for_99_percent": 1}
                for key, field in (("activity", "activity_Bq"), ("heat", "heat_W"), ("alpha_heat", "alpha_W"),
                                   ("beta_heat", "beta_W"), ("gamma_heat", "gamma_W"))}
    depletion = {"model": "coupled", "nuclides": [
        {"nuclide": "Fe56", "removal_rate_per_s": 3.0, "channels": [
            {"channel": "MT102", "rate_per_s": 1.0, "share": 1.0 / 3.0}, {"channel": "MT103", "rate_per_s": 2.0,
                                                                          "share": 2.0 / 3.0}]}]}
    step = {"step": 1, "inventory": [{"nuclide": r["nuclide"], "atoms_per_g": r["atoms"]} for r in rows],
            "activity_Bq_per_g": {"Mn56": 1.5e6, "H3": 1.1e3}, "heat_W_per_g": {"total": heat},
            "leakage_atoms_per_g": 0.0,
            "summary": {"nuclides": rows, "totals": totals, "elements": elements, "dominant": dominant,
                        "depletion": depletion}}
    routes = {"targets": [{"target": "Mn56", "total_atoms_per_g": 3.0e10, "paths_atoms_per_g": 2.9e10,
                           "residual_atoms_per_g": 1.0e9, "residual_fraction": 1.0e9 / 3.0e10,
                           "explained_fraction": 2.9e10 / 3.0e10,
                           "paths": [{"atoms_per_g": 2.0e10}, {"atoms_per_g": 9.0e9}],
                           "generic": [{"atoms_per_g": 2.9e10}]}]}
    result = {"mode": "coupled", "steps": [step], "pathway_routes": routes}
    spec = {"material": {"mass_g": mass, "density_g_cm3": 8.0}, "summary": {"depletion": True}, "pathways": {}}
    for name, check in (("summary", check_summary), ("depletion", check_depletion), ("routes", check_routes)):
        verdict = check(result, spec)
        if not verdict["pass"] or not verdict.get("checked"):
            problems.append(f"selftest: clean synthetic {name} did not pass: {verdict}")
    if not check_inventory(result)["pass"]:
        problems.append("selftest: clean inventory did not pass")

    def perturbed(edit, check, label):
        copy_ = json.loads(json.dumps(result))
        edit(copy_)
        if check(copy_, spec)["pass"]:
            problems.append(f"selftest: {label} was not caught")

    perturbed(lambda r: r["steps"][0]["summary"]["totals"].__setitem__("activity_Bq", 1.5e6 * (1 + 1e-9) + 1.1e3),
              check_summary, "summary total off by 1e-9")
    perturbed(lambda r: r["steps"][0]["summary"]["elements"][1].__setitem__("activity_Bq", 1.6e6), check_summary,
              "element rows not summing")
    perturbed(lambda r: r["steps"][0]["summary"]["totals"]["absolute"].__setitem__("heat_W", 0.0), check_summary,
              "absolute block off")
    perturbed(lambda r: r["steps"][0]["summary"]["depletion"]["nuclides"][0]["channels"][0].__setitem__("share", 0.4),
              check_depletion, "depletion shares not summing to 1")
    perturbed(lambda r: r["pathway_routes"]["targets"][0].__setitem__("residual_atoms_per_g", 2.0e9), check_routes,
              "route closure off")
    perturbed(lambda r: r["pathway_routes"]["targets"][0]["paths"][0].__setitem__("atoms_per_g", 1.0e10),
              check_routes, "route list not matching paths_atoms_per_g")
    # a 1e-13 relative error must not trip the 1e-12 tolerance
    near = json.loads(json.dumps(result))
    near["steps"][0]["summary"]["totals"]["activity_Bq"] *= 1 + 1e-13
    near["steps"][0]["summary"]["totals"]["activity_Ci"] *= 1 + 1e-13
    near["steps"][0]["summary"]["dominant"]["activity"]["total"] *= 1 + 1e-13
    near["steps"][0]["summary"]["totals"]["activity_by_mode"]["beta_minus_Bq"] *= 1 + 1e-13
    near["steps"][0]["summary"]["totals"]["beta_Bq"] *= 1 + 1e-13
    # the absolute/per_cm3 blocks were built from the unperturbed totals, so 1e-13 stays inside 1e-12
    if not check_summary(near, spec)["pass"]:
        problems.append("selftest: a 1e-13 relative difference tripped the 1e-12 tolerance")
    negative = json.loads(json.dumps(result))
    negative["steps"][0]["inventory"][1]["atoms_per_g"] = -1.0e-3 * 9.0e21
    if check_inventory(negative)["pass"]:
        problems.append("selftest: negative inventory was not caught")
    tiny = json.loads(json.dumps(result))
    tiny["steps"][0]["inventory"][1]["atoms_per_g"] = -1.0e-14 * 9.0e21
    if not check_inventory(tiny)["pass"]:
        problems.append("selftest: a -1e-14 inventory tripped the -1e-12 floor")
    for bad in (b'{"a": NaN}', b'{"a": Infinity}', b'{"a": [-Infinity]}', b'{"a": 1e999}'):
        try:
            load_result(bad)
            problems.append(f"selftest: {bad!r} was accepted")
        except NonFinite:
            pass
    for bad in (b'{"steps": [{"inventory": [{"nuclide": "H1", "atoms_per_g": null}]}]}',
                b'{"steps": [{"activity_Bq_per_g": {"Mn56": null}}]}'):
        try:
            load_result(bad)
            problems.append(f"selftest: null number {bad!r} was accepted")
        except NonFinite:
            pass
    if load_result(b'{"steps": [{"summary": {"nuclides": [{"half_life_s": null, "atoms": 1.0}]}}]}') is None:
        problems.append("selftest: a legitimate null half-life was rejected")
    if load_result(b'{"a": [1.5, {"b": 2}]}') != {"a": [1.5, {"b": 2}]}:
        problems.append("selftest: ordinary JSON misparsed")
    if mask_ms(b'{\n  "ms": 12.5e-1,\n  "x": 1\n}') != mask_ms(b'{\n  "ms": 99,\n  "x": 1\n}'):
        problems.append("selftest: ms mask")
    if mask_ms(b'{"ms": 1, "x": 1}') == mask_ms(b'{"ms": 1, "x": 2}'):
        problems.append("selftest: ms mask hides a real difference")
    # refusal classification
    case = Case("t", "m", "coupled", "partition", {"schedule": [], "options": {}}, ["partition"], "base")
    for rc, text, want in ((1, "error: schedule partition cannot be combined with pathways", "refused-ok"),
                           (2, "error: unknown field `bogus`", "fail"), (101, "thread panicked at partition", "fail"),
                           (1, "thread 'main' panicked at partition", "fail")):
        got, _ = classify_refusal(case, {"rc": rc, "stderr": text, "timed_out": False})
        if got != want:
            problems.append(f"selftest: refusal {text!r} classified {got}, expected {want}")
    return problems


def self_check(args, workdir):
    ctx = Context(args, workdir)
    cases = build_cases(ctx, args.only)
    write_specs(cases, workdir)
    problems = self_check_static(ctx, cases, workdir)
    problems += selftest_invariants()
    width = max(len(c.id) for c in cases)
    for case in cases:
        tags = []
        if case.skip:
            tags.append(f"SKIP({case.skip})")
        if case.conflicts:
            tags.append("expect-refusal: " + "; ".join(m for m, _ in case.conflicts))
        if case.compare_reference:
            tags.append("vs-reference")
        print(f"{case.id:<{width}}  {case.material:<28} {case.mode:<8} {case.optset:<28} " + " ".join(tags))
    runs = sum(2 + (1 if c.compare_reference else 0) for c in cases if not c.skip)
    print(f"{len(cases)} cases ({sum(1 for c in cases if c.skip)} skipped here), at most {runs} solver runs; "
          f"specs in {workdir / 'cases'}")
    for note in ctx.notes:
        print(f"note: {note}")
    for problem in problems:
        print(f"PROBLEM: {problem}")
    print(f"P140 H2 self-check: {'FAIL' if problems else 'PASS'}")
    return 1 if problems else 0


# ------------------------------------------------------------------------------ main


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reference")
    ap.add_argument("--candidate")
    ap.add_argument("--workdir", default="target/p140-matrix")
    ap.add_argument("--out", default=None)
    ap.add_argument("--timeout", type=int, default=1800, help="seconds allowed per run")
    ap.add_argument("--only", default=None, help="run only cases whose id contains this text (comma separates)")
    ap.add_argument("--keep", action="store_true", help="keep the result files of passing cases")
    ap.add_argument("--nuclear-data", default="~/nuclear-data")
    ap.add_argument("--photon-response", default=None)
    ap.add_argument("--self-check", action="store_true")
    args = ap.parse_args(argv)
    workdir = (ROOT / args.workdir).resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    if args.self_check:
        return self_check(args, workdir)
    if not args.reference or not args.candidate:
        ap.error("--reference and --candidate are required")
    reference = str(Path(args.reference).resolve())
    candidate = str(Path(args.candidate).resolve())
    if args.out:
        out = (ROOT / args.out).resolve()
    elif args.only:
        out = workdir / "p140_matrix.partial.json"
    else:
        out = ROOT / "results" / "p140_matrix.json"

    ctx = Context(args, workdir)
    cases = build_cases(ctx, args.only)
    write_specs(cases, workdir)
    if not cases:
        print("no case matches --only", file=sys.stderr)
        return 2
    records = []
    started = time.monotonic()
    interrupted = False

    def write_record(final):
        counts = {}
        for r in records:
            counts[r["outcome"]] = counts.get(r["outcome"], 0) + 1
        invariants = {}
        for r in records:
            for name, v in r.get("invariants", {}).items():
                if v.get("applicable", True):
                    tally = invariants.setdefault(name, {"checked_runs": 0, "failed": 0})
                    tally["checked_runs"] += 1
                    tally["failed"] += 0 if v["pass"] else 1
        complete = final and len(records) == len(cases) and not args.only
        verdict_ok = bool(records) and len(records) == len(cases) and counts.get("fail", 0) == 0 \
            and counts.get("skipped", 0) == 0 and not interrupted
        record = {
            "protocol": "protocols/ACTINV-P140_PROTOCOL.md", "gate": "H2",
            "protocol_sha256": sha256_file(PROTOCOL), "reference": reference, "reference_sha256": sha256_file(reference),
            "candidate": candidate, "candidate_sha256": sha256_file(candidate),
            "checker_sha256": sha256_file(__file__), "selection": args.only, "complete_matrix": bool(complete),
            "timeout_s": args.timeout, "wall_s": round(time.monotonic() - started, 1),
            "counts": dict(sorted(counts.items()), total=len(records), planned=len(cases)),
            "invariants": invariants,
            "documented_refusals_not_raised": sum(1 for r in records if r.get("warning")),
            "extras_flagged": sum(1 for r in records if r.get("extras")
                                  or any(v.get("informational", {}).get("n_mismatches")
                                         for v in r.get("invariants", {}).values())),
            "notes": ctx.notes, "cases": records,
            "verdict": "PASS" if verdict_ok else "FAIL",
        }
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
        return record

    try:
        for case in cases:
            try:
                record = run_case(case, reference, candidate, args, workdir)
            except Exception as error:  # a checker fault is a failed case, not a lost matrix
                record = {"id": case.id, "material": case.material, "mode": case.mode, "option_set": case.optset,
                          "outcome": "fail", "invariants": {}, "wall_s": 0.0,
                          "message": f"checker error: {type(error).__name__}: {error}"}
            records.append(record)
            print(case_line(record), flush=True)
            write_record(False)
    except KeyboardInterrupt:
        interrupted = True
        print("interrupted", file=sys.stderr)
    record = write_record(True)
    counts = record["counts"]
    print(f"P140 H2: {record['verdict']} - {counts['total']} of {counts['planned']} cases: "
          f"{counts.get('pass', 0)} pass, {counts.get('refused-ok', 0)} refused-ok, {counts.get('fail', 0)} fail, "
          f"{counts.get('skipped', 0)} skipped; {record['documented_refusals_not_raised']} documented refusals not "
          f"raised; {record['extras_flagged']} cases with informational flags ({out})")
    return 0 if record["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
