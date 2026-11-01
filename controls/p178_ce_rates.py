#!/usr/bin/env python3
"""P178 harness: ACTINV's 709-group collapsed reaction rates against OpenMC 0.15.3 continuous-energy tallies on four
HELD-OUT models, i.e. materials and regions P173 never used. It is P173's harness (controls/p173_ce_rates.py) with the
model tables replaced; every algorithm (tallies, convergence iteration, collapse, rows, G1 check, assembly) is P173's,
imported and run unchanged, so P178's rates and P173's are the same quantity computed the same way.

Models (concentric shells; void inside the first listed shell, solid out to the body radius, vacuum outside; the
tallied regions are the listed shells, the material between and beyond them is untallied):
  E  Eurofer-97 steel                  regions 5-7, 15-18, 30-35 cm, body 45 cm
  I  Inconel 718                       regions 3-5, 12-15, 25-30 cm, body 35 cm
  H  tungsten heavy alloy W-7Ni-3Fe    regions 1-2, 4-6, 8-10 cm, body 12 cm
  B  pure (OFHC) copper                regions 3-5, 10-14 cm, body 18 cm
Same isotropic 14.1 MeV point source at the centre, same ENDF/B-VIII.1 HDF5 library and pinned inputs as P173.

How P173's code is reused: P173's functions read their model tables, output paths, seed and schedule from module
globals, so `install()` rebinds those globals in the imported p173 module (the tables are replaced, not edited in
place) and points `p173.__file__` at this file, so the OpenMC child processes P173 starts re-enter THIS script and
build the P178 models. The rehearsal switch crosses that process boundary as the environment variable
P178_REHEARSAL=1 (set by `--rehearsal`).

Three arms per row (rates per target atom per source particle, barn cm, as P173):
  R_openmc   OpenMC 0.15.3 continuous-energy tally.
  R_actinv   infinite dilution: sum_g sigma_g L_g from the ENDF/B-VIII.1 library (P173's collapse); r = ln(R_actinv/R_openmc).
  R_mixture  the rate ACTINV applies with `self_shielding.mixture`: sum_g sigma_g s_g L_g, the same library rows and
             the same OpenMC group flux L_g, with s_g the per-group factor of the target in the row's channel (mt 2
             elastic, 18/19 fission, 102 capture, every other mt the total) that the `actinv run` ledger reports at
             `ledger.shielding.applied_factors` (the solver's own factors, not recomputed here; 1 where the ledger lists
             no group); r_mix = ln(R_mixture/R_openmc). The run's material is the OpenMC model's own atom densities
             (OpenMC's element expansion, explicit nuclides, `atom_fraction` basis), at 293.6 K, its spectrum the
             region's OpenMC flux, and `evaluation_files` the raw ENDF/B-VIII.1 tapes (profile `endfb8`) of every
             material nuclide whose staged tape has a resolved resonance range; the nuclides without a usable tape are
             recorded and enter the solver's mixture total only through its analytic potential-scattering estimate.

Modes (as P173; each its own process, none runs unless asked; each refuses to start without an enforced cgroup):
  [--rehearsal] openmc --model {E,I,H,B}          results/p178/openmc_<model>.json
  [--rehearsal] actinv --model {E,I,H,B}          results/p178/actinv_<model>.json
  [--rehearsal] actinv-mixture --model {E,I,H,B}  results/p178/actinv_mixture_<model>.json (one `actinv run` per region)
  [--rehearsal] g1-check                          results/p178/g1_check.json
  [--rehearsal] assemble                          results/p178_ce_rates.json (rows carry r and r_mix)
  openmc-run, omc-xs (internal)            started by `openmc` with the OpenMC interpreter
--rehearsal writes everything under target/p178-rehearsal with 1000 particles per batch and at most 8 batches; it
produces plumbing output only, nothing it writes is scored or kept.

The P178 library, its index, the stage manifest and the `actinv` binary (`ACTINV_BIN_SHA256_P178`, checked against the
file at `ACTINV_BIN`) are pinned by SHA-256 (the placeholder "TO-BE-FILLED-AT-REGISTRATION" held them until
registration); every non-rehearsal mode refuses to run while a placeholder remains or a pinned file does not match.
"""
import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "controls"))
import p173_ce_rates as p173  # noqa: E402  (constants and machinery; reads nothing at import)

P173_HARNESS = Path(p173.__file__).resolve()

# --rehearsal sets this in the environment so the OpenMC child processes see it too.
REHEARSAL_ENV = "P178_REHEARSAL"


def rehearsal():
    return os.environ.get(REHEARSAL_ENV) == "1"


OUT_DIR = ROOT / "target" / "p178-rehearsal" if rehearsal() else ROOT / "results" / "p178"
RECORD = OUT_DIR / "record.json" if rehearsal() else ROOT / "results" / "p178_ce_rates.json"
WORK_TAG = "p178-rehearsal" if rehearsal() else "p178"
SCHEMA = "actinv-p178-ce-rates-1"

# ------------------------------------------------------------------------------------------------------ run schedule
# P173's settings unchanged (100,000 particles per batch, 4..256 batches, relative SE target 0.5%, 2 threads, 6 GiB);
# only the seed base differs. Deep regions with threshold rows may stay above the target at 256 batches, as in P173;
# the record's `converged` flag reports that, a larger budget does not hide it.
PARTICLES_PER_BATCH = 1000 if rehearsal() else 100_000
BATCH_MAX = 8 if rehearsal() else 256
SEED_BASE = 178000  # P173 used 173000

# ------------------------------------------------------------------------------------------------------ materials
# Mass fractions by element, nominal specification values; OpenMC expands each element to its natural isotopes (with
# `cross_sections` given it drops isotopes absent from the library and renormalises; the library holds Ta180 and
# Ta180_m1, V50, V51, N14/N15, C12/C13, Ti46-50 and Nb93, so nothing is dropped here). `final_wo` normalises the sum.
MATERIALS = {
    # Eurofer-97 (reduced-activation ferritic-martensitic), Fe balance
    "eurofer97": {"label": "Eurofer-97", "density_g_cm3": 7.75,
                  "wo": {"Fe": 0.8904, "Cr": 0.090, "W": 0.011, "Mn": 0.004, "V": 0.002, "Ta": 0.0012,
                         "C": 0.0011, "N": 0.0003}},
    # Inconel 718, nominal mid-range; the listed values sum to 100.6 wt% and `final_wo` renormalises them
    "inconel718": {"label": "Inconel 718", "density_g_cm3": 8.19,
                   "wo": {"Ni": 0.525, "Cr": 0.19, "Fe": 0.185, "Nb": 0.051, "Mo": 0.0305, "Ti": 0.009,
                          "Al": 0.005, "Co": 0.005, "Mn": 0.002, "Si": 0.002, "Cu": 0.0015}},
    "w_heavy_alloy": {"label": "Tungsten heavy alloy W-7Ni-3Fe", "density_g_cm3": 17.0,
                      "wo": {"W": 0.90, "Ni": 0.07, "Fe": 0.03}},
    "copper": {"label": "Copper (OFHC)", "density_g_cm3": 8.96, "wo": {"Cu": 1.0}},
}

# ------------------------------------------------------------------------------------------------------ models
SHELLS = {"E": [(5, 7), (15, 18), (30, 35)], "I": [(3, 5), (12, 15), (25, 30)],
          "H": [(1, 2), (4, 6), (8, 10)], "B": [(3, 5), (10, 14)]}
# Outer radius of the solid body: the tallied regions end inside it, a final untallied shell fills the rest.
BODY_RADIUS = {"E": 45, "I": 35, "H": 12, "B": 18}
BASE_MODELS = ("E", "I", "H", "B")
MODEL_ORDER = BASE_MODELS
MODEL_MATERIAL = {"E": "eurofer97", "I": "inconel718", "H": "w_heavy_alloy", "B": "copper"}

# (target, MT, product), fixed before any run: capture rows for every nuclide with significant resonance capture in the
# material plus threshold rows as controls (the same convention as P173's REACTIONS).
REACTIONS = {
    "E": [("Fe56", 103, "Mn56"),    # threshold control, the dominant steel activation channel
          ("Fe54", 103, "Mn54"),    # threshold control, shutdown-dose nuclide
          ("Fe54", 107, "Cr51"),    # threshold (n,alpha) control
          ("Cr52", 16, "Cr51"),     # threshold (n,2n) control, 9 wt% Cr
          ("Cr50", 102, "Cr51"),    # capture, Cr-50 resonances
          ("Mn55", 102, "Mn56"),    # capture, strong resonances, 0.4 wt% Mn
          ("Mn55", 16, "Mn54"),     # threshold (n,2n) control
          ("Fe58", 102, "Fe59"),    # capture, Fe-58 resonances
          ("W186", 102, "W187"),    # capture, 1.1 wt% W, large resonances
          ("W184", 102, "W185"),    # capture, W resonances
          ("W182", 102, "W183"),    # capture, W-182 resonance integral
          ("Ta181", 102, "Ta182"),  # capture, very large resonance integral at 0.12 wt% Ta
          ("V51", 102, "V52"),      # capture, 0.2 wt% V
          ("N14", 103, "C14")],     # (n,p), C-14 production
    "I": [("Ni58", 103, "Co58"),    # threshold control, dominant Ni activation
          ("Ni60", 103, "Co60"),    # threshold control
          ("Ni58", 16, "Ni57"),     # threshold (n,2n) control
          ("Ni58", 102, "Ni59"),    # capture, 52 wt% Ni
          ("Ni62", 102, "Ni63"),    # capture, Ni-62 resonances
          ("Nb93", 102, "Nb94"),    # capture, 5 wt% Nb, large resonance integral
          ("Nb93", 16, "Nb92"),     # threshold (n,2n) control
          ("Mo98", 102, "Mo99"),    # capture, Mo resonances
          ("Mo92", 103, "Nb92"),    # threshold (n,p) control
          ("Co59", 102, "Co60"),    # capture, 0.5 wt% Co, Co-60 source
          ("Cr50", 102, "Cr51"),    # capture
          ("Fe58", 102, "Fe59"),    # capture
          ("Mn55", 102, "Mn56"),    # capture
          ("Fe54", 103, "Mn54"),    # threshold control
          ("Ti48", 103, "Sc48"),    # threshold (n,p) control, 0.9 wt% Ti
          ("Al27", 107, "Na24"),    # threshold (n,alpha) control
          ("Cu63", 102, "Cu64")],   # capture, 0.15 wt% Cu
    "H": [("W186", 102, "W187"),    # capture, 90 wt% W
          ("W184", 102, "W185"),    # capture
          ("W182", 102, "W183"),    # capture
          ("W183", 102, "W184"),    # capture, W-183 resonances
          ("W186", 16, "W185"),     # threshold (n,2n) control
          ("W182", 103, "Ta182"),   # threshold (n,p) control
          ("W183", 103, "Ta183"),   # threshold (n,p) control
          ("Ni58", 103, "Co58"),    # threshold control, 7 wt% Ni
          ("Ni58", 16, "Ni57"),     # threshold (n,2n) control
          ("Ni62", 102, "Ni63"),    # capture
          ("Fe58", 102, "Fe59"),    # capture, 3 wt% Fe
          ("Fe54", 103, "Mn54")],   # threshold control
    "B": [("Cu63", 102, "Cu64"),    # capture, Cu-63 resonances
          ("Cu65", 102, "Cu66"),    # capture, Cu-65 resonances
          ("Cu63", 16, "Cu62"),     # threshold (n,2n) control
          ("Cu65", 16, "Cu64"),     # threshold (n,2n) control
          ("Cu63", 107, "Co60"),    # threshold (n,alpha) control, Co-60 source
          ("Cu63", 103, "Ni63"),    # threshold (n,p) control
          ("Cu65", 103, "Ni65")],   # threshold (n,p) control
}
# Chemical symbols the new targets need, added to P173's table.
ELEMENT_Z = {**p173.ELEMENT_Z, "C": 6, "N": 7, "V": 23, "Sc": 21, "Ti": 22, "Nb": 41, "Ta": 73, "Si": 14}

_SHELLS_TO_MODEL = {tuple(map(tuple, s)): m for m, s in SHELLS.items()}
_original_layout = p173.layout


def layout(shells):
    """P173's layout, plus the body: for a P178 model a final untallied shell runs from the last region's outer radius to
    the body radius. Any other shell list is laid out exactly as P173 does."""
    base = _original_layout(shells)
    model = _SHELLS_TO_MODEL.get(tuple(map(tuple, shells)))
    if model is None:
        return base
    last = base[-1][1]
    body = BODY_RADIUS[model]
    return base + [(last, body, False)] if body > last else base


# ------------------------------------------------------------------------------------------------------ library, pins
PLACEHOLDER = "TO-BE-FILLED-AT-REGISTRATION"
P178_DATA = p173.DATA / "endfb-viii.1-p178"
# The corrected ENDF/B-VIII.1 library (rebuilt after P173/P172), replacing P173's.
ENDF8_NPZ = P178_DATA / "endfb81_all_709g.npz"
ENDF8_INDEX = P178_DATA / "endfb81_all_709g_index.json"
# The raw ENDF/B-VIII.1 tapes (one per nuclide, `<name>.endf`, metastables `<name>_m<N>.endf`) and their manifest.
STAGE_DIR = p173.DATA / "endfb-viii.1-p172" / "stage"
STAGE_MANIFEST = p173.DATA / "endfb-viii.1-p172" / "stage_manifest.json"
PINNED = {**p173.PINNED,
          "endf8_library": (ENDF8_NPZ, "a964a44ef106b692d170ba6085859ca2827ff967b3e9a6ddc60ca8fa9ac92051"),
          "endf8_index": (ENDF8_INDEX, "b5a4b668324a92bd96e8e90c5feb521a79ca8838e42a517ac080652074b7f41a"),
          "stage_manifest": (STAGE_MANIFEST, "d16b9233e7d8855cf9f08be3a1b5c79b945454f8f21d0f66996adc7a937f861e")}
# The release `actinv` binary every P178 mode runs (copied to target/p178-bin/actinv, passed as ACTINV_BIN). Kept apart
# from PINNED, whose entries are files at fixed paths; `install()` rebinds p173's binary pin to this one.
ACTINV_BIN_SHA256_P178 = "f4800d0c25196721a0ababfe039fc9eb24b0d12b65af5af5ccc756280a4b8827"
# Pins this script verifies itself (the others are P173's and are only recorded by assemble).
VERIFIED_PINS = ("endf8_library", "endf8_index", "stage_manifest")


def unregistered_pins():
    """Names of the pinned inputs (and "actinv_bin", the binary pin) still holding the registration placeholder."""
    names = [name for name, (_, sha) in PINNED.items() if sha == PLACEHOLDER]
    if ACTINV_BIN_SHA256_P178 == PLACEHOLDER:
        names.append("actinv_bin")
    return sorted(names)


def require_registered():
    """Scored modes only run with every pin filled in, and then only on files that match their pins. A rehearsal
    skips both checks."""
    if rehearsal():
        return
    missing = unregistered_pins()
    if missing:
        sys.exit(f"p178_ce_rates: refusing to run a scored mode: pinned SHA-256 not yet registered for {missing}")
    for name in VERIFIED_PINS:
        path, sha = PINNED[name]
        if p173.sha256_file(path) != sha:
            sys.exit(f"p178_ce_rates: {name} ({path}) does not match its pinned SHA-256")
    try:
        binary = p173.sha256_file(p173.ACTINV_BIN)
    except OSError as error:
        sys.exit(f"p178_ce_rates: cannot read the actinv binary {p173.ACTINV_BIN}: {error}")
    if binary != ACTINV_BIN_SHA256_P178:
        sys.exit(f"p178_ce_rates: the actinv binary ({p173.ACTINV_BIN}) does not match its pinned SHA-256")


def install():
    """Rebind the model tables, paths, seed, schedule, library and pinned inputs in the p173 module (idempotent)."""
    for name, value in {
            "ENDF8_NPZ": ENDF8_NPZ, "ENDF8_INDEX": ENDF8_INDEX, "PINNED": PINNED, "ACTINV_BIN_SHA256": ACTINV_BIN_SHA256_P178,
            "OUT_DIR": OUT_DIR, "RECORD": RECORD, "WORK_TAG": WORK_TAG, "SCHEMA": SCHEMA,
            "PARTICLES_PER_BATCH": PARTICLES_PER_BATCH, "BATCH_MAX": BATCH_MAX, "SEED_BASE": SEED_BASE,
            "MATERIALS": MATERIALS, "SHELLS": SHELLS, "BASE_MODELS": BASE_MODELS, "MODEL_ORDER": MODEL_ORDER,
            "MODEL_MATERIAL": MODEL_MATERIAL, "REACTIONS": REACTIONS, "ELEMENT_Z": ELEMENT_Z,
            "layout": layout, "__file__": str(Path(__file__).resolve())}.items():
        setattr(p173, name, value)


install()


def constants_digest():
    """SHA-256 of every constant that defines the P178 models, materials, reactions and schedule, and of P173's harness
    file (whose algorithms P178 runs) (hashed by G0). Independent of --rehearsal."""
    blob = {"materials": MATERIALS, "wo_normalised": {m: p173.final_wo(m) for m in MATERIALS},
            "shells": SHELLS, "body_radius": BODY_RADIUS, "reactions": REACTIONS, "element_z": ELEMENT_Z,
            "model_order": MODEL_ORDER, "model_material": MODEL_MATERIAL, "seed_base": SEED_BASE,
            "schema": "actinv-p178-ce-rates-1", "particles_per_batch": 100_000, "batch_start": p173.BATCH_START,
            "batch_max": 256, "rel_se_target": p173.REL_SE_TARGET, "source_energy_eV": p173.SOURCE_ENERGY_EV,
            "mt_score": p173.MT_SCORE, "g1_tolerance": p173.G1_TOLERANCE, "threads": p173.THREADS,
            "memory_gib": p173.MEMORY_GIB, "p173_harness_sha256": p173.sha256_file(P173_HARNESS),
            "p173_pinned_sha256": {k: v[1] for k, v in p173.PINNED.items()}}
    return hashlib.sha256(json.dumps(blob, sort_keys=True).encode()).hexdigest()


# ------------------------------------------------------------------------------------------------------ mixture arm
MIXTURE_TEMPERATURE_K = 293.6  # the library's temperature (its index says 293.6)
MIXTURE_PROFILE = "endfb8"
CHANNELS = ("total", "elastic", "fission", "capture")  # the order of ledger.shielding.channels
# Decay data the run needs to load (it does not enter the shielding factors, which depend on cross sections only).
DECAY_PRIMARY = p173.DATA / "endfb-viii.0-decay" / "bulk" / "endf-b-viii-0_decay.dat"
DECAY_FALLBACK = p173.DATA / "jeff-3.3-decay" / "bulk" / "jeff-3-3_decay.dat"
RUN_TIMEOUT_S = 3600
_OPENMC_NUCLIDE = re.compile(r"^([A-Z][a-z]?)(\d+)(?:_m(\d+))?$")


def channel_of_mt(mt):
    """The shielded channel (index into CHANNELS) a library row of this MT takes: the solver's `channel_of_mt`."""
    return {2: 1, 18: 2, 19: 2, 102: 3}.get(mt, 0)


def actinv_nuclide(openmc_name):
    """OpenMC's 'Ta180_m1' -> ACTINV's 'Ta180m1'; ground states unchanged."""
    m = _OPENMC_NUCLIDE.match(openmc_name)
    if m is None:
        raise ValueError(f"unrecognised OpenMC nuclide name {openmc_name!r}")
    return f"{m.group(1)}{m.group(2)}" + (f"m{m.group(3)}" if m.group(3) else "")


def composition_from_openmc(atom_densities):
    """The run's material: OpenMC's own nuclide atom densities (its element expansion) as explicit nuclides with
    basis `atom_fraction`. Zero entries are dropped."""
    return {actinv_nuclide(name): float(d) for name, d in sorted(atom_densities.items()) if float(d) > 0}


def _endf_int(field):
    return int(field.strip() or 0)


def tape_resolved_check(text):
    """(ok, reason) for an ENDF-6 tape: whether the solver's mixture mode can take it. It reads MF=2 only: a single
    MT=151 section, one isotope, whose first energy range is resolved (LRU=1; ranges ascend, so a resolved range is
    the first). The solver's own checks (0 K, parseable ranges, RML validation) still apply at run time."""
    mf2_sections, head = set(), []
    for line in text.splitlines():
        if len(line) < 75 or line[70:72].strip() != "2":
            continue
        mt = line[72:75].strip()
        if mt in ("", "0"):
            continue
        mf2_sections.add(int(mt))
        if mt == "151" and len(head) < 3:
            head.append(line)
    if not mf2_sections:
        return False, "no MF=2 resonance section"
    if mf2_sections != {151}:
        return False, f"unsupported MF=2 sections {sorted(mf2_sections)}"
    if len(head) < 3:
        return False, "truncated MF=2/MT=151 section"
    if _endf_int(head[0][44:55]) != 1:
        return False, f"MF=2 lists {_endf_int(head[0][44:55])} isotopes"
    lru = _endf_int(head[2][22:33])
    if lru != 1:
        return False, f"first energy range is LRU={lru}, not resolved"
    return True, None


def select_tapes(nuclides, stage_dir=None, manifest_files=None):
    """For the OpenMC-named material nuclides: the staged raw tapes that qualify, as ([{nuclide, name, path, sha256}],
    {OpenMC name: reason} for those that do not). A tape whose bytes do not hash to the manifest's SHA-256 is an
    error."""
    stage_dir = Path(STAGE_DIR if stage_dir is None else stage_dir)
    if manifest_files is None:
        manifest_files = json.loads(STAGE_MANIFEST.read_text())["files"]
    tapes, skipped = [], {}
    for name in sorted(nuclides):
        filename = f"{name}.endf"
        entry = manifest_files.get(filename)
        path = stage_dir / filename
        if entry is None:
            skipped[name] = "not in the stage manifest"
            continue
        if not path.is_file():
            skipped[name] = "tape file missing"
            continue
        sha = p173.sha256_file(path)
        if sha != entry["sha256"]:
            raise ValueError(f"{path} hashes to {sha}, the stage manifest says {entry['sha256']}")
        ok, reason = tape_resolved_check(path.read_text(errors="replace"))
        if not ok:
            skipped[name] = reason
            continue
        tapes.append({"nuclide": actinv_nuclide(name), "name": name, "path": str(path), "sha256": sha})
    return tapes, skipped


def build_mixture_spec(model, region, flux, atom_densities, tapes, bounds, library, decay):
    """The `actinv run` input for one region: its OpenMC flux as the spectrum, the model material as explicit
    nuclides, `self_shielding.mixture` over `tapes`, and the short unit-flux schedule that makes the run write its
    ledger. `library` is {path, sha256}; `decay` is {primary, fallback}."""
    return {
        "spec": "actinv-spec-1", "title": f"P178 mixture self-shielding, model {model} region {region}",
        "projectile": "neutron", "library": library, "decay": decay,
        "material": {"mass_g": 1.0, "basis": "atom_fraction", "composition": composition_from_openmc(atom_densities)},
        "spectrum": {"structure": "custom", "boundaries_eV": list(bounds), "flux_per_group": [float(v) for v in flux],
                     "descending": False},
        "self_shielding": {"mixture": {"evaluation_files": [{"path": t["path"], "sha256": t["sha256"]} for t in tapes],
                                       "profile": MIXTURE_PROFILE}},
        "schedule": [{"dt": "1 s", "flux": 1.0}],
        "options": {"mode": "auto", "prune": "rate", "bmin_atoms_per_g": 1e-8, "temperature_K": MIXTURE_TEMPERATURE_K,
                    "outputs": ["inventory", "activity", "heat", "ledger"]},
        "fission_yields": {"files": [], "energy": "spectrum_average"}}


def channel_scales(applied_nuclide, n_groups):
    """Four per-group scale lists (CHANNELS order) from the ledger's `applied_factors[nuclide]` ({group: [4 scales]};
    groups it omits are 1). None when the nuclide has no entry (not covered by the mixture)."""
    if applied_nuclide is None:
        return None
    scales = [[1.0] * n_groups for _ in CHANNELS]
    for group, row in applied_nuclide.items():
        g = int(group)
        if not 0 <= g < n_groups or len(row) != len(CHANNELS):
            raise ValueError(f"malformed applied factor for group {group}: {row}")
        for c, value in enumerate(row):
            scales[c][g] = float(value)
    return scales


def mixture_reaction(rows, sig, targets, selection, target, mt, flux, flux_sd, applied_nuclide):
    """The mixture-mode rate of one reaction in one region: the rows P173's `actinv_reaction` uses, each collapsed as
    sum_g sigma_g s_g L_g with s_g its channel's applied scale. A target the mixture does not cover is unscaled."""
    t = p173.ground_state_index(targets, target)
    if t is None:
        return {"status": "not_scored", "reason_code": "reaction_absent_actinv",
                "reason": f"{target} is not a ground-state target of the ACTINV library"}
    prod, tot = selection.get((t, mt), ([], []))
    use = prod or tot
    if not use:
        return {"status": "not_scored", "reason_code": "reaction_absent_actinv",
                "reason": f"the ACTINV library has no row for {target} MT {mt}"}
    n = len(flux)
    scales = channel_scales(applied_nuclide, n)
    terms, bounds_ = [], []
    for i in use:
        s = scales[channel_of_mt(rows[i][1])] if scales is not None else [1.0] * n
        terms.append(math.fsum(float(x) * g * float(f) for x, g, f in zip(sig[i], s, flux)))
        bounds_.append(math.fsum(float(x) * g * float(d) for x, g, d in zip(sig[i], s, flux_sd)))
    rate, bound = math.fsum(terms), math.fsum(bounds_)
    infinite = math.fsum(p173.collapse(sig[i], flux) for i in use)
    channel = channel_of_mt(mt)
    changed = [] if scales is None else [v for v in scales[channel] if v != 1.0]
    return {"status": "ok", "R_mixture": rate, "R_mixture_se_bound": bound, "R_infinite_dilution": infinite,
            "scale_effective": (rate / infinite if infinite > 0 else None), "channel": CHANNELS[channel],
            "target_covered": scales is not None, "groups_scaled": len(changed),
            "min_scale": min(changed) if changed else None, "rows": use}


def mixture_constants_digest():
    """SHA-256 of the constants that define the mixture arm (recorded beside the rates)."""
    blob = {"temperature_K": MIXTURE_TEMPERATURE_K, "profile": MIXTURE_PROFILE, "channels": list(CHANNELS),
            "channel_of_mt": {str(mt): channel_of_mt(mt) for mt in (2, 16, 18, 19, 102, 103, 107)},
            "schedule": [{"dt": "1 s", "flux": 1.0}], "basis": "atom_fraction", "schema": "actinv-p178-ce-rates-1-mixture",
            "stage_dir": "endfb-viii.1-p172/stage"}
    return hashlib.sha256(json.dumps(blob, sort_keys=True).encode()).hexdigest()


def run_actinv_mixture_model(model, out):
    """Mode `actinv-mixture`: one `actinv run` per tallied region of `model` (see the module docstring), its ledger's
    applied factors folded into R_mixture for every listed reaction."""
    import numpy as np
    cg = p173.require_bounded()
    om = json.loads((OUT_DIR / f"openmc_{model}.json").read_text())
    final = om["final"]
    if final is None:
        sys.exit(f"p178_ce_rates: no OpenMC result for model {model}")
    lib = np.load(p173.ENDF8_NPZ)
    rows, sig, bounds = lib["rows"].tolist(), lib["sig"], [float(v) for v in lib["bounds"]]
    if bounds != p173.groups_ascending():
        sys.exit("p178_ce_rates: the library's group boundaries are not the fispact-709 boundaries")
    targets = json.loads(p173.ENDF8_INDEX.read_text())["targets"]
    selection = p173.library_selection(rows)
    atom_densities = final["atom_densities_atom_b_cm"]
    tapes, skipped = select_tapes(atom_densities)
    if not tapes:
        sys.exit(f"p178_ce_rates: no qualifying evaluation tape for any nuclide of model {model}")
    library = {"path": str(p173.ENDF8_NPZ), "sha256": p173.sha256_file(p173.ENDF8_NPZ)}
    decay = {"primary": str(DECAY_PRIMARY), "fallback": str(DECAY_FALLBACK)}
    work = ROOT / "target" / f"{WORK_TAG}-mixture-{model}"
    work.mkdir(parents=True, exist_ok=True)
    version = subprocess.run([str(p173.ACTINV_BIN), "--version"], capture_output=True, text=True,
                             timeout=60).stdout.strip()
    reaction_targets = sorted({t for t, _, _ in p173.model_spec(model)["reactions"]})
    regions = {}
    for region, flux in final["flux"].items():
        tag = region.replace(":", "_")
        spec_path, out_path = work / f"{tag}_spec.json", work / f"{tag}_result.json"
        spec = build_mixture_spec(model, region, flux["mean"], atom_densities, tapes, bounds, library, decay)
        p173.write_atomic(spec_path, spec)
        run = subprocess.run([str(p173.ACTINV_BIN), "run", str(spec_path), str(out_path)], capture_output=True,
                             text=True, timeout=RUN_TIMEOUT_S)
        (work / f"{tag}.stderr").write_text(run.stderr)
        if run.returncode != 0:
            sys.exit(f"p178_ce_rates: `actinv run` failed for {region}: {run.stderr[-600:]}")
        shielding = json.loads(out_path.read_text())["ledger"]["shielding"]
        if tuple(shielding.get("channels", ())) != CHANNELS or "mixture" not in shielding:
            sys.exit(f"p178_ce_rates: the ledger of {region} has no mixture self-shielding block")
        applied = shielding["applied_factors"]
        reactions = {p173.rate_key(t, mt): mixture_reaction(rows, sig, targets, selection, t, mt, flux["mean"],
                                                            flux["std_dev"], applied.get(t))
                     for t, mt, _ in p173.model_spec(model)["reactions"]}
        regions[region] = {
            "spec_sha256": p173.sha256_file(spec_path), "result_sha256": p173.sha256_file(out_path),
            "mixture": {k: shielding["mixture"][k] for k in ("profile", "temperature_K", "covered",
                                                              "background_estimated", "evaluations_unused",
                                                              "zero_k_clips")},
            "applied_factors": {t: applied[t] for t in reaction_targets if t in applied},
            "reactions": reactions}
    document = {"schema": SCHEMA + "-mixture", "model": model, "regions": regions,
                "tapes": [{k: t[k] for k in ("nuclide", "path", "sha256")} for t in tapes],
                "nuclides_without_tape": skipped,
                "composition_atom_densities_atom_b_cm": composition_from_openmc(atom_densities),
                "temperature_K": MIXTURE_TEMPERATURE_K, "profile": MIXTURE_PROFILE,
                "mixture_constants_sha256": mixture_constants_digest(),
                "library_sha256": library["sha256"], "index_sha256": p173.sha256_file(p173.ENDF8_INDEX),
                "stage_manifest_sha256": p173.sha256_file(STAGE_MANIFEST),
                "actinv_bin": str(p173.ACTINV_BIN), "actinv_bin_sha256": p173.sha256_file(p173.ACTINV_BIN),
                "actinv_version": version, "environment": p173.environment_record(cg)}
    p173.write_atomic(out, document)


def add_mixture_to_rows(rows, mixtures):
    """Fold the mixture arm into P173-style assembled `rows` (in place): R_mixture, its bound, r_mix and its standard
    error, the effective scale and the target's coverage. `mixtures` is {model: the actinv_mixture_<model> document}.
    A row without a usable mixture rate keeps r_mix None and says why in `mixture_reason`."""
    for row in rows:
        row.update(R_mixture=None, R_mixture_se_bound=None, r_mix=None, se_r_mix=None, scale_effective=None,
                   mixture_target_covered=None, mixture_groups_scaled=None, mixture_min_scale=None,
                   mixture_status="not_scored", mixture_reason=None)
        if row["status"] != "scored":
            row["mixture_reason"] = "row not scored"
            continue
        res = mixtures[row["model"]]["regions"][row["region"]]["reactions"][p173.rate_key(row["target"], row["mt"])]
        if res.get("status") != "ok":
            row["mixture_reason"] = res.get("reason", "no mixture rate")
            continue
        rm = res["R_mixture"]
        row.update(R_mixture=rm, R_mixture_se_bound=res["R_mixture_se_bound"], scale_effective=res["scale_effective"],
                   mixture_target_covered=res["target_covered"], mixture_groups_scaled=res["groups_scaled"],
                   mixture_min_scale=res["min_scale"])
        if not rm > 0:
            row["mixture_reason"] = "the mixture rate is zero"
            continue
        row["r_mix"], row["se_r_mix"] = p173.log_ratio(rm, res["R_mixture_se_bound"], row["R_openmc"],
                                                       row["R_openmc_se"])
        row["mixture_status"] = "scored"


def assemble_p178(record_path):
    """P173's assembly (record rows with R_openmc, R_actinv, r), then the mixture arm added to every row and a
    `mixture_arm` block describing it."""
    mixtures = {m: json.loads((OUT_DIR / f"actinv_mixture_{m}.json").read_text()) for m in MODEL_ORDER}
    p173.assemble(record_path)
    document = json.loads(Path(record_path).read_text())
    add_mixture_to_rows(document["rows"], mixtures)
    # the mixture mode's own bounded cgroup, beside P173's records (checked by G1)
    document.setdefault("environment", {}).setdefault("cgroups", []).extend(
        {"where": f"actinv_mixture:{m}", "cgroup": d.get("environment", {}).get("cgroup")}
        for m, d in mixtures.items())
    document["mixture_arm"] = {
        "constants_sha256": mixture_constants_digest(), "p178_constants_sha256": constants_digest(),
        "p173_harness_sha256": p173.sha256_file(P173_HARNESS), "temperature_K": MIXTURE_TEMPERATURE_K,
        "profile": MIXTURE_PROFILE, "stage_manifest_sha256": p173.sha256_file(STAGE_MANIFEST),
        "models": {m: {"tapes": d["tapes"], "nuclides_without_tape": d["nuclides_without_tape"],
                       "actinv_bin_sha256": d["actinv_bin_sha256"], "actinv_version": d["actinv_version"],
                       "regions": {r: {k: v for k, v in rd.items() if k not in ("reactions", "applied_factors")}
                                   for r, rd in d["regions"].items()}}
                   for m, d in mixtures.items()}}
    p173.write_atomic(record_path, document)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rehearsal", action="store_true",
                    help="plumbing dry run: outputs under target/p178-rehearsal, small particle counts, nothing scored")
    sub = ap.add_subparsers(dest="mode", required=True)
    for name in ("openmc", "actinv", "actinv-mixture"):
        sub.add_parser(name).add_argument("--model", choices=list(MODEL_ORDER), required=True)
    sub.add_parser("g1-check")
    sub.add_parser("assemble")
    for name in ("openmc-run", "omc-xs"):
        sub.add_parser(name).add_argument("job")
    args = ap.parse_args(argv)
    if args.rehearsal and not rehearsal():
        # the module-level paths were fixed at import; re-exec so they and the child processes agree
        os.environ[REHEARSAL_ENV] = "1"
        os.execv(sys.executable, [sys.executable, str(Path(__file__).resolve()), *argv])
    if args.mode not in ("openmc-run", "omc-xs"):  # the children of `openmc` run under its checks
        require_registered()
    if args.mode == "openmc":
        p173.run_openmc_model(args.model, OUT_DIR / f"openmc_{args.model}.json")
    elif args.mode == "actinv":
        p173.run_actinv_model(args.model, OUT_DIR / f"actinv_{args.model}.json")
    elif args.mode == "actinv-mixture":
        run_actinv_mixture_model(args.model, OUT_DIR / f"actinv_mixture_{args.model}.json")
    elif args.mode == "g1-check":
        p173.run_g1_check(OUT_DIR / "g1_check.json")
    elif args.mode == "assemble":
        assemble_p178(RECORD)
    elif args.mode == "omc-xs":
        p173.omc_xs(args.job)
    else:
        p173.openmc_run(args.job)


if __name__ == "__main__":
    main()
