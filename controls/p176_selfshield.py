#!/usr/bin/env python3
"""P176 harness (protocols/ACTINV-P176_PROTOCOL.md): does ACTINV's optional self-shielding bring its 709-group capture
rates to OpenMC's continuous-energy tallies of P173, and does it leave the other P173 rows alone?

Nothing is tallied again. P173's OpenMC rates and tallied 709-group fluxes (results/p173/openmc_<model>.json, pinned by
hash) are reused. For each P173 region and each arm the harness writes one ordinary ACTINV problem (the region's tallied
flux as the spectrum, the model's OpenMC atom densities as the material, one 1 s irradiation step at flux multiplier 1,
`summary.depletion` on), runs `actinv run`, and reads the solver's own per-reaction loss rate from the depletion table
(`steps[].summary.depletion.nuclides[].channels[]`, kind "reaction", channel MT<n>). That is the collapsed rate per target
atom with the arm's self-shielding applied, the numbers the solver's diagonal carries.

Arms (fixed in ARMS below):
  A0  default: no `self_shielding` section. Control; its loss rates must reproduce P173's total-row rates (G1).
  A1  Bondarenko: `self_shielding.table` (built here by `actinv build-shielding` from the ENDF/B-VIII.1 evaluations of
      the models' nuclides) with `dilution: "composition"` (the documented default).
  A2  geometry: `self_shielding.geometry`, a foil of the tallied region's thickness, resonance files for every material
      nuclide whose evaluation has a resolved range, `reactions` left at the default ("all").
  A3  geometry as A2 with the thickness of the whole model body (first inner to last outer radius).

Score: q = loss_rate(arm) / loss_rate(A0) per (region, target, MT); R_ss = R_actinv(P173) * q;
r_ss = ln(R_ss / R_openmc) = r(P173) + ln q, with P173's standard error of r.

Modes (each its own process; none runs unless asked):
  table                      builds the Bondarenko table (results/p176/table.json holds its record)
  arm --arm A --model M      writes results/p176/arm_<A>_<M>.json (one ACTINV run per region of the model)
  assemble                   merges everything into results/p176_selfshield.json

P176_REHEARSAL=1: every output goes under target/p176-rehearsal*, the tallied flux is replaced by a flat 1.0 per group
(no OpenMC result enters), and nothing is scored or kept. It exists to find harness defects before the scored run.

Run each mode inside the bounded systemd scope AGENTS.md prescribes. Every mode refuses to start without an enforced
cgroup. Nothing here is run by the protocol author.
"""
import argparse
import hashlib
import json
import math
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "controls"))
if os.environ.get("P173_REHEARSAL") == "1":
    sys.exit("p176_selfshield: P173_REHEARSAL must not be set (P176 reads P173's scored records)")
import p173_ce_rates as p173  # noqa: E402  (constants and pure helpers; reads nothing at import)

HOME = Path.home()
DATA = HOME / "nuclear-data"
REHEARSAL = os.environ.get("P176_REHEARSAL") == "1"
OUT_DIR = ROOT / "target" / "p176-rehearsal" if REHEARSAL else ROOT / "results" / "p176"
RECORD = OUT_DIR / "record.json" if REHEARSAL else ROOT / "results" / "p176_selfshield.json"
WORK = ROOT / "target" / ("p176-rehearsal-work" if REHEARSAL else "p176-work")
# build-shielding checkpoints, keyed by source bytes, options and the shielding code (not the run), so the scored
# run reuses the rehearsal's per-evaluation results instead of repeating a multi-hour build; the table is the same
SHIELD_CACHE = ROOT / "target" / "p176-shield-cache"
SCHEMA = "actinv-p176-selfshield-1"
GIB = 1 << 30

ACTINV_BIN = Path(os.environ.get("ACTINV_BIN", ROOT / "target/release/actinv"))
# The release build of a38d1fa (crates tree f7b5e60e...), the build-shielding --profile fix; A0 tests it against P173's R_actinv.
ACTINV_BIN_SHA256 = "36af79228fc56c1116bdfca375b6050e906bd6901475bcbfd2c1fa60b712619c"
# SHA-256 of the Bondarenko table `actinv build-shielding` writes from the pinned evaluations with the pinned binary
# (deterministic: no RNG). The coordinator fills it in from the rehearsal's table before registration.
SHIELD_TABLE_SHA256 = "b8ccd5a45b0f6a4189b86d91aa43e1bca280bb3459ae3e4ae9192b56b7ad9f83"

ENDF_DIR = DATA / "endfb-viii.1-endf6/neutrons-version.VIII.1"
DECAY_PRIMARY = DATA / "endfb-viii.0-decay/bulk/endf-b-viii-0_decay.dat"
DECAY_FALLBACK = DATA / "jeff-3.3-decay/bulk/jeff-3-3_decay.dat"
P173_DIR = ROOT / "results" / "p173"
P173_RECORD = ROOT / "results" / "p173_ce_rates.json"
P173_VERDICT = ROOT / "results" / "p173_verdict.json"

# Frozen inputs: name -> (path, SHA-256). The checker requires the record to hold exactly these.
PINNED = {
    "endf8_library": p173.PINNED["endf8_library"],
    "endf8_index": p173.PINNED["endf8_index"],
    "fispact_709_groups": p173.PINNED["fispact_709_groups"],
    "endf8_neutron_archive": (DATA / "endfb-viii.1-endf6/neutrons-version.VIII.1.tar.gz",
                              "decff90016bfb5c25c8d4c7fb8d81f94ff2f104853165f0fe6a21bf61c4164e4"),
    "decay_primary": (DECAY_PRIMARY, "6f04cf009086c179021f243a58dadc2d5bb078de5ba39c4fe46ccad77d228ddb"),
    "decay_fallback": (DECAY_FALLBACK, "850b8b7f85f8d88b6ad826c4cd341aaaffabd525c8ecf3c588a0ad437bf5d123"),
    "p173_record": (P173_RECORD, "f4b6aea267f492ad4242b5551f6709aba10d7f831007892575a54cfe11095ad1"),
    "p173_verdict": (P173_VERDICT, "36aeb818a9cc483f9c076a3199d6e8bd0ddab99a47ef0adb65432577f9a59f44"),
    "p173_openmc_S": (P173_DIR / "openmc_S.json", "1ff792834fc444c1d586df3a6bf6590b0587c4e6e40bd478fb04a10c00459cb5"),
    "p173_openmc_W": (P173_DIR / "openmc_W.json", "7f43329dd6e616ecd881eb7330bf37949351aeda21f50b4eac298b1e0b64eebb"),
    "p173_openmc_C": (P173_DIR / "openmc_C.json", "e2242072775f5158da32ceaf72435dfeb222f32204565bb0c5919038ba97b4d6"),
    "p173_openmc_K": (P173_DIR / "openmc_K.json", "d655d544041a0bbc3255279e067340a072e19e4baa996ea72812066e4d625681"),
    "p173_protocol": (ROOT / "protocols/ACTINV-P173_PROTOCOL.md",
                      "14b21e9d3761f870118816031d64422a66559532c44790c3093f7c5f7b8de1f8"),
    "p173_harness": (ROOT / "controls/p173_ce_rates.py",
                     "42df1b5469b73dd742ec2ce36bc89a7264400fe4710077f634ba3de502f9210e"),
    "p173_checker": (ROOT / "controls/check_p173.py",
                     "a04f201f6fb747ddb6fcba910c58a7bd3750fd9ee60a0e4c3799cbca94894b30"),
}

# The ENDF/B-VIII.1 neutron evaluation of every nuclide in the four models (the union of P173's atom-density keys),
# file name in the NNDC archive and SHA-256. They feed the Bondarenko table (A1) and, where the evaluation has a resolved
# range, the resonance files of the geometry arms (A2, A3).
ENDF_FILES = {
    "Al27": ("n-013_Al_027.endf", "e2126a8b46300d68472d67462dec84831f8199d04c5e15ac6d1ed43a6819c366"),
    "C12": ("n-006_C_012.endf", "249b431cec84be40b957f8499862ceb4380e7d6054492b6500f4d3d255a907fb"),
    "C13": ("n-006_C_013.endf", "bacd7d45da01f3be41ce1272718918f26a0d99fae7cdb3fd9ed398e413603244"),
    "Ca40": ("n-020_Ca_040.endf", "3af67ce02f0e6fb431dc3a794c20675172499abf47e47e1b1518a300e3a533a2"),
    "Ca42": ("n-020_Ca_042.endf", "cc5e414c4801b5a6edda12485ea4e0e1dfec195c6a2a0305f167fbe0ea8164f2"),
    "Ca43": ("n-020_Ca_043.endf", "28ed345c52ddf9a6394728ef2c5eb51e7e59da55282d2a407e0e977fa6d8bf74"),
    "Ca44": ("n-020_Ca_044.endf", "d0864b7ee96a611118ebaf4c8bf0acd2293f581716e7669b1ee289c719d716a3"),
    "Ca46": ("n-020_Ca_046.endf", "48b2faf515dcdf18711d483e96d97597829dd882137553ba1c9e5a936e982948"),
    "Ca48": ("n-020_Ca_048.endf", "c765412a8f621cb7d932e4c8f9d82d30c0e0c7fc935eec792ec5a44112961adf"),
    "Co59": ("n-027_Co_059.endf", "942237d47dde605769760f85416dcb0eb327cc9dc0df751536bb6f653a39637b"),
    "Cr50": ("n-024_Cr_050.endf", "54ab4a4d4d662c649a99fc64c4956281808ed621cfc47a1f5522e11147511afd"),
    "Cr52": ("n-024_Cr_052.endf", "06141f64278b359a38ae0fedf03e6540dc7a600bbe4585ee472e060b042804dd"),
    "Cr53": ("n-024_Cr_053.endf", "1bb2fa048d98072ac2dcbd6bb044f00bf77a93d81522ecdd1b24a2acb71c7da9"),
    "Cr54": ("n-024_Cr_054.endf", "809a4cf8a7cc5a9a7b2b653659eb304a6ced447b7c92d22adaae55afe3af0ca3"),
    "Cu63": ("n-029_Cu_063.endf", "097fad045cb38459d080db1ade7996516392cbefc97a7098fac6127ada16c179"),
    "Cu65": ("n-029_Cu_065.endf", "69877b3471ff98836e5507c7cb59f6c05c505d56a196e6f01bf4d3681d87c51c"),
    "Eu151": ("n-063_Eu_151.endf", "b16933bed5886bc7b52b7ff3288522e8dcd239a7c2f8f19d2ad219ccb809f8bf"),
    "Eu153": ("n-063_Eu_153.endf", "eb43cdac4d5baf26a55f1cf00b1cbe482d8f1c0fd8c407cda81546211c6e5407"),
    "Fe54": ("n-026_Fe_054.endf", "ce6928f4becc1fa1307d60be0d5923fe36ca04da169e5aebc651bee2e183df86"),
    "Fe56": ("n-026_Fe_056.endf", "eca38d4ce177a463bbc872e50fef451dd4428adf86495ba69e71b4ebf65a7b19"),
    "Fe57": ("n-026_Fe_057.endf", "c9061f7390d46e0b0eb2f89e43b9b7ba9adc28de77d657bb0fca202c10d159dc"),
    "Fe58": ("n-026_Fe_058.endf", "7ae3601c641bf3693b7ed368afddd78c5b87be56be884e43dfda30b683cad06a"),
    "H1": ("n-001_H_001.endf", "3024d8f0db604b074e33ed8b05027eb9f5a738614041ba44c83f048e0d0bc1af"),
    "H2": ("n-001_H_002.endf", "869fe4e84547d124feff6d8eafd1158ac4e121c7ea05f8b20c547a63f5a97a8c"),
    "K39": ("n-019_K_039.endf", "fe87851a6e3fac220f9457a69fd105105fcc4128ebd4ea94d8c0f05f3d57972c"),
    "K40": ("n-019_K_040.endf", "10a20709232c4c134987447b737198ab777ce38418f89011e90872cde062e80a"),
    "K41": ("n-019_K_041.endf", "a4b61abb48ad9de3aa805f7c5fbf45cc07cc2c0f85c0b06bddddebb95e7f13f0"),
    "Mg24": ("n-012_Mg_024.endf", "2f17a01e8c2d05969d466874216b4f00d1d0f27f8debb890b5bfae883d5fd2ab"),
    "Mg25": ("n-012_Mg_025.endf", "4a0385671becea46b4b793c721ab3e900772412ef3620ff82c16dcf71fdbc22f"),
    "Mg26": ("n-012_Mg_026.endf", "6ce7bbd0c0782216a49a11a9eabd1f4813fb95c0d200b083b4735de4ecaf8503"),
    "Mn55": ("n-025_Mn_055.endf", "f5833e3f8e2be44668c9a8b9051da3a6642d35d9a7215b90166ef3f918c3153e"),
    "Mo100": ("n-042_Mo_100.endf", "8254145f8f6ac3845bab338032730f9a67b2ed417db382010dfbad8c93677084"),
    "Mo92": ("n-042_Mo_092.endf", "6b472daf4a676bcba7aa9e832da21a2b34fa51e1bae9b935d2d90ba8b1783c31"),
    "Mo94": ("n-042_Mo_094.endf", "ad57e72b805b30f01ee7f0ade12537be9ecc40e7a036e32f0685fdf271162b3c"),
    "Mo95": ("n-042_Mo_095.endf", "0126319f2202719e161e3d0da051ff6c9cdfa9cfc9e5664bbdf5f68df0d3d627"),
    "Mo96": ("n-042_Mo_096.endf", "fff580607f42ce64e27211cff6119be878cfe39b52f6979bfb04cc135da65307"),
    "Mo97": ("n-042_Mo_097.endf", "d9f56397314cdb842d30769f5cc406c0cbe46301af8ba11af879a1e81d50e99e"),
    "Mo98": ("n-042_Mo_098.endf", "8b0d6544bed1ac980dd2434c28a965e4fb57916e4c3d4a3a25fa70b444fd9102"),
    "N14": ("n-007_N_014.endf", "fd5c0c0beccefbcd0b57c49bcdf835449d2014e3740830377ce24760478c06d8"),
    "N15": ("n-007_N_015.endf", "e3cba9a1d7b462d73fd328b7a290bb8f4e166ab68c7d982a10f90259fdb7fe26"),
    "Na23": ("n-011_Na_023.endf", "903babdd22f612acfc034cf866108df1450378cf0dbb508e9706412d793bc815"),
    "Ni58": ("n-028_Ni_058.endf", "49d18433113e799c5946a637b59cf2881edf9afadee0ecd5a5a3d6146f91ae02"),
    "Ni60": ("n-028_Ni_060.endf", "80e3e7c62c33b0ce09d05814ef5812c49249e7025424e0d18d49356e89a06f11"),
    "Ni61": ("n-028_Ni_061.endf", "0aea193158167f308d9273f3b860f9c23a12275a82c62f843b2b6e606106c326"),
    "Ni62": ("n-028_Ni_062.endf", "20a1b831cd16cd42447b604e5aa986652a4d8c33485ab594e9e091346f9df7c8"),
    "Ni64": ("n-028_Ni_064.endf", "dbf2d9cca233f2c13455dfb2fe24f6e03758e5acaade2e0b1e0c2b7591ace76e"),
    "O16": ("n-008_O_016.endf", "850acd8a1c1f103a63864437281605abc8d4d3c6b8f444041f24514ad45a71c9"),
    "O17": ("n-008_O_017.endf", "92800e0dfe16f036d716af0a72c0e37b6b253f1836646f7319cbb26976498f84"),
    "O18": ("n-008_O_018.endf", "1631b91d1f67aeb853d150d47b490a204105bf76821db405c2766d2c5167ece3"),
    "P31": ("n-015_P_031.endf", "244c1afd4d0f701681daddb2226479ddca4633d6b98ed3b7f4e09c6a622dc4fd"),
    "S32": ("n-016_S_032.endf", "401858714791bac48adb7893f9d735b64eb9743f44a2c7e8e375db30d2856439"),
    "S33": ("n-016_S_033.endf", "e2ee2920788c91244b8939ce517ea7a2a189e4e43312b15c2674a281193b9f7a"),
    "S34": ("n-016_S_034.endf", "18c1f23215a8b5600efb0d7e4a44163f4ded687968c8679d4d76aba788a4feef"),
    "S36": ("n-016_S_036.endf", "61a9234bac19cfc759685691631b229598ba38f3eb9d3de31f89008c28de1f03"),
    "Si28": ("n-014_Si_028.endf", "bd5ff1ce566945c6dd6ecb7b0fca45f801b76183174f6da38035ed8dddb219e0"),
    "Si29": ("n-014_Si_029.endf", "6e2ebe3e1f45b7e18c054502195393ea3831c77f4e62ec009c487effde404318"),
    "Si30": ("n-014_Si_030.endf", "0199c129d55cbdcdc1feda5ba54d56fc1413fd66f26a3563432acb2f159418c2"),
    "W180": ("n-074_W_180.endf", "36b4ced7ade215b3da8d199db022a3641b808ded20e7d123b08fbf01e792bbc5"),
    "W182": ("n-074_W_182.endf", "a268105de7993d5e49de2867f640178c80beaa695da750d8be8cc532ab11af2f"),
    "W183": ("n-074_W_183.endf", "b857d50f3d83f5d69301dcb4fd06736c268b25be9c65ac1fad65d872bd79883f"),
    "W184": ("n-074_W_184.endf", "fbd83b4ce24904466de0b7b937420250e9c2f2e1210d841dfbd032234f26d408"),
    "W186": ("n-074_W_186.endf", "ca96629399e4c1f50c8feffe64daf93d131e171ed00597b41f3cc927adda9102"),
    "Zr90": ("n-040_Zr_090.endf", "a439a6820446be374740e067c6113e9163769b93c1650e5cf4930b834d4a5291"),
    "Zr91": ("n-040_Zr_091.endf", "9dcd2bcb42d9820c4c6682c14e2de7f794ad6836ad1c03c22ed389e066b82e4c"),
    "Zr92": ("n-040_Zr_092.endf", "3b175e8d52345751f69e94742b093a91bc180aaae4dbc1467df9ab12855589ad"),
    "Zr94": ("n-040_Zr_094.endf", "46f6d1f5a1e5651e04d459e05dd752a12f9172f08383966fd21a86044cb80e7e"),
    "Zr96": ("n-040_Zr_096.endf", "402954e4d44f3ab7a16900cb4be8e558de3d3745a95d8d32a1338bfee8564f28"),
}

# ------------------------------------------------------------------------------------------------------ arms and rules
ARMS = {
    "A0": {"label": "default, self-shielding off (control)", "self_shielding": None},
    "A1": {"label": "Bondarenko table, dilution from the composition",
           "self_shielding": {"table": "built by `table`", "dilution": "composition"}},
    "A2": {"label": "geometry, foil of the tallied region's thickness",
           "self_shielding": {"geometry": {"shape": "foil", "length": "region", "reactions": "default (all)"}}},
    "A3": {"label": "geometry, foil of the whole model body's thickness",
           "self_shielding": {"geometry": {"shape": "foil", "length": "body", "reactions": "default (all)"}}},
}
ARM_ORDER = ("A0", "A1", "A2", "A3")
SCORED_ARMS = ("A1", "A2", "A3")
STEP = {"dt": "1 s", "flux": 1.0}
SUMMARY = {"depletion": True, "nuclides": False, "elements": False}
MATERIAL_ABS_R = 0.05
MATERIAL_SIGMAS = 3.0
G1_TOLERANCE = 1e-9
RUN_TIMEOUT_S = 3600
TABLE_TIMEOUT_S = 6 * 3600
NOT_SCORED_CODES = ("p173_not_scored", "arm_failed", "no_loss_row", "rate_zero")


def constants_digest():
    """SHA-256 of every constant that defines the arms, the spec each arm writes, the decision thresholds and the pinned
    evaluation files (hashed by G0)."""
    blob = {"arms": ARMS, "arm_order": ARM_ORDER, "step": STEP, "summary": SUMMARY, "material_abs_r": MATERIAL_ABS_R,
            "material_sigmas": MATERIAL_SIGMAS, "g1_tolerance": G1_TOLERANCE, "endf_files": ENDF_FILES,
            "p173_constants": p173.constants_digest(), "material_basis": "atoms_per_g = n[atom/b/cm] * 1e24 / density",
            "spectrum": "custom, ascending fispact-709 boundaries, P173 tallied group flux as is",
            "q": "loss_rate(arm) / loss_rate(A0)", "r_ss": "r(P173) + ln q", "se": "se_r of P173"}
    return hashlib.sha256(json.dumps(blob, sort_keys=True).encode()).hexdigest()


# ------------------------------------------------------------------------------------------------------ utilities

def require_bounded():
    cg = p173.cgroup_record()
    if not p173.cgroup_ok(cg):
        sys.exit(f"p176_selfshield: refusing to run: cgroup is not an enforced bounded scope ({cg})")
    return cg


def require_binary():
    digest = p173.sha256_file(ACTINV_BIN)
    if digest != ACTINV_BIN_SHA256:
        sys.exit(f"p176_selfshield: {ACTINV_BIN} is not the pinned P173 binary ({digest})")


def require_pins():
    for name, (path, digest) in PINNED.items():
        if p173.sha256_file(path) != digest:
            sys.exit(f"p176_selfshield: input {name} ({path}) is not the pinned file")


def environment_record(cg):
    status = p173.git("status", "--porcelain", "--untracked-files=no")
    return {"git_head": p173.git("rev-parse", "HEAD"), "crates_tree": p173.git("rev-parse", "HEAD:crates"),
            "clean_tree": status == "", "cgroup": cg, "harness_sha256": p173.sha256_file(Path(__file__)),
            "groups_sha256": p173.sha256_file(p173.GROUPS), "constants_sha256": constants_digest(),
            "rehearsal": REHEARSAL, "python": platform.python_version(), "platform": platform.platform()}


def spec_digest(spec):
    """SHA-256 of the problem with every file path reduced to its file name, so the digest does not depend on where the
    checkout or the data directory is."""
    portable = json.loads(json.dumps(spec))
    portable["library"]["path"] = Path(portable["library"]["path"]).name
    portable["decay"] = {k: Path(v).name for k, v in portable["decay"].items()}
    shielding = portable.get("self_shielding", {})
    if "table" in shielding:
        shielding["table"]["path"] = Path(shielding["table"]["path"]).name
    for ref in shielding.get("geometry", {}).get("resonance_files", []):
        ref["path"] = Path(ref["path"]).name
    return hashlib.sha256(json.dumps(portable, sort_keys=True).encode()).hexdigest()


def run_cmd(cmd, timeout, cwd=None):
    """Run `cmd`; on timeout the child is killed and reaped (subprocess.run does both). Returns (returncode or None,
    stdout tail, stderr tail, wall seconds)."""
    env = dict(os.environ, ACTINV_CACHE_DIR=str(WORK / "cache"), PYTHONHASHSEED="0", RAYON_NUM_THREADS="2")
    start = time.time()
    try:
        done = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, timeout=timeout, cwd=cwd, env=env)
        return done.returncode, done.stdout[-2000:], done.stderr[-2000:], time.time() - start
    except subprocess.TimeoutExpired:
        return None, "", f"timed out after {timeout} s", time.time() - start


# ------------------------------------------------------------------------------------------------------ P173 data

def load_p173_data():
    """{model: {"flux": {region: [709 group fluxes, cm per source]}, "atoms": {nuclide: atom/b/cm}, "density": g/cm3}}
    from the pinned P173 OpenMC files. Under P176_REHEARSAL=1 the flux is a flat 1.0 per group instead."""
    bounds = p173.groups_ascending()
    data = {}
    for model in p173.MODEL_ORDER:
        final = json.loads((P173_DIR / f"openmc_{model}.json").read_text())["final"]
        if REHEARSAL:
            flux = {region: [1.0] * (len(bounds) - 1) for region in p173.regions_of(model)}
        else:
            flux = {region: list(final["flux"][region]["mean"]) for region in p173.regions_of(model)}
        data[model] = {"flux": flux, "atoms": dict(final["atom_densities_atom_b_cm"]),
                       "density": final["density_g_cm3"]}
    return data


def region_bounds(model, region):
    for r_in, r_out, tallied in p173.layout(p173.model_spec(model)["shells"]):
        if tallied and p173.region_name(model, r_in, r_out) == region:
            return r_in, r_out
    raise KeyError(region)


def body_bounds(model):
    lay = p173.layout(p173.model_spec(model)["shells"])
    return lay[0][0], lay[-1][1]


def foil_thickness_cm(arm, model, region):
    """Thickness of the foil an arm declares: the tallied region's (A2) or the whole solid body's (A3)."""
    r_in, r_out = region_bounds(model, region) if arm == "A2" else body_bounds(model)
    return float(r_out - r_in)


def mf2_first_range(lines):
    """(NIS, NER, LRU, LRF, EL, EH) of the first energy range of an ENDF-6 evaluation given as text lines (MF=2, MT=151):
    the HEAD's NIS, the first isotope's NER and the first range's LRU, LRF, EL, EH."""
    section = [ln for ln in lines if ln[70:72] == " 2" and ln[72:75] == "151"]
    if len(section) < 3:
        raise ValueError("no MF=2/MT=151 section")

    def field(line, i):
        return line[11 * i:11 * (i + 1)]

    def real(text):
        text = text.strip()
        m = re.fullmatch(r"([+-]?\d*\.?\d*)([+-]\d+)", text)
        return float(m.group(1) + "e" + m.group(2)) if m else float(text)
    return (int(field(section[0], 4)), int(field(section[1], 4)), int(field(section[2], 2)),
            int(field(section[2], 3)), real(field(section[2], 0)), real(field(section[2], 1)))


def has_resolved_range(path):
    """True when the evaluation's first range is a resolved range (LRU=1): ACTINV's geometry model refuses an evaluation
    without one. Evaluations of several isotopes are not expected (NIS must be 1)."""
    nis, _, lru, _, _, _ = mf2_first_range(Path(path).read_text().splitlines())
    if nis != 1:
        raise ValueError(f"{path}: NIS {nis}, one isotope expected")
    return lru == 1


def geometry_files(model, data, endf_dir=None):
    """[(nuclide, path, sha256)] for the nuclides of `model` whose evaluation has a resolved range, sorted by name."""
    endf_dir = ENDF_DIR if endf_dir is None else Path(endf_dir)
    out = []
    for nuclide in sorted(data[model]["atoms"]):
        name, digest = ENDF_FILES[nuclide]
        if has_resolved_range(endf_dir / name):
            out.append((nuclide, str(endf_dir / name), digest))
    return out


def build_spec(arm, model, region, data, table=None, endf_dir=None):
    """The ACTINV problem of one (arm, region). `table` is (path, sha256) of the Bondarenko table for A1."""
    d = data[model]
    bounds = p173.groups_ascending()
    spec = {
        "spec": "actinv-spec-1", "title": f"P176 {arm} {region}", "projectile": "neutron",
        "library": {"path": str(p173.ENDF8_NPZ), "sha256": PINNED["endf8_library"][1]},
        "decay": {"primary": str(DECAY_PRIMARY), "fallback": str(DECAY_FALLBACK)},
        "material": {"mass_g": 1.0, "basis": "atoms_per_g", "density_g_cm3": d["density"],
                     "composition": {nuc: n * 1e24 / d["density"] for nuc, n in sorted(d["atoms"].items())}},
        "spectrum": {"structure": "custom", "boundaries_eV": bounds, "flux_per_group": list(d["flux"][region]),
                     "descending": False},
        "schedule": [dict(STEP)],
        "summary": dict(SUMMARY),
    }
    if arm == "A1":
        path, digest = table
        spec["self_shielding"] = {"table": {"path": str(path), "sha256": digest}, "dilution": "composition"}
    elif arm in ("A2", "A3"):
        spec["self_shielding"] = {"geometry": {
            "shape": "foil", "length_cm": foil_thickness_cm(arm, model, region),
            "resonance_files": [{"path": path, "sha256": digest} for _, path, digest in
                                geometry_files(model, data, endf_dir)]}}
    elif arm != "A0":
        raise KeyError(arm)
    return spec


# ------------------------------------------------------------------------------------------------------ reading a result

def depletion_rates(result):
    """{(nuclide, MT): rate per target atom per s} from the one step that carries `summary.depletion`: every channel of
    kind "reaction" (the sum of the nuclide's loss rows of that MT, collapsed with the run's self-shielding)."""
    steps = [s for s in result.get("steps", []) if isinstance(s, dict) and (s.get("summary") or {}).get("depletion")]
    if len(steps) != 1:
        raise ValueError(f"{len(steps)} steps carry a depletion table, one expected")
    out = {}
    for nuclide in steps[0]["summary"]["depletion"].get("nuclides", []):
        for channel in nuclide.get("channels", []):
            if channel.get("kind") == "reaction" and isinstance(channel.get("mt"), int):
                out[(nuclide["nuclide"], channel["mt"])] = float(channel["rate_per_s"])
    return out


def shielding_summary(result):
    """The result ledger's `shielding` block with `applied_factors` reduced to a per-nuclide summary (groups touched and
    the smallest factor), and any other entry over 20 kB replaced by its size; None when the run had no shielding.
    Each factor row is (total, elastic, fission, capture); a fission factor of exactly 0 marks a nuclide without
    fission and is left out of the minimum."""
    block = (result.get("ledger") or {}).get("shielding")
    if not isinstance(block, dict):
        return None
    out = {k: v for k, v in block.items() if k != "applied_factors"}
    applied = block.get("applied_factors")
    if isinstance(applied, dict):
        summary = {}
        for nuclide, groups in applied.items():
            values = [f for row in (groups.values() if isinstance(groups, dict) else [])
                      for c, f in enumerate(row) if not (c == 2 and f == 0)]
            summary[nuclide] = {"groups": len(groups) if isinstance(groups, dict) else 0,
                                "min_factor": min(values) if values else None}
        out["applied_factors_summary"] = summary
    for key, value in list(out.items()):
        size = len(json.dumps(value))
        if size > 20000:
            out[key] = {"omitted_bytes": size}
    return out


def reaction_rates_of(result, model):
    """{rate_key: loss rate or None} for the model's registered reactions, from one result."""
    rates = depletion_rates(result)
    return {p173.rate_key(t, mt): rates.get((t, mt)) for t, mt, _ in p173.model_spec(model)["reactions"]}


# ------------------------------------------------------------------------------------------------------ scoring

def build_ss_row(arm, p173_row, dep_a0, dep_arm, run_ok):
    """One P176 row from a P173 row, the A0 loss rate, the arm's loss rate (per atom per s, None when the solver's
    depletion table has no such channel) and whether the arm's run for the region succeeded."""
    key = {k: p173_row[k] for k in ("model", "region", "target", "mt", "reaction")}
    row = {"arm": arm, **key, "p173_status": p173_row["status"], "dep_rate_a0": dep_a0, "dep_rate_arm": dep_arm,
           "total_row_R_actinv": p173_row.get("total_row_R_actinv")}

    def not_scored(code, reason):
        return {**row, "status": "not_scored", "reason_code": code, "reason": reason}
    if p173_row["status"] != "scored":
        return not_scored("p173_not_scored", f"P173 did not score the row ({p173_row.get('reason_code')})")
    row.update(R_actinv=p173_row["R_actinv"], R_openmc=p173_row["R_openmc"], p173_r=p173_row["r"],
               se_r=p173_row["se_r"], p173_r_coll=p173_row["r_coll"], se_r_coll=p173_row["se_r_coll"],
               p173_r_proc=p173_row["r_proc"], p173_converged=bool(p173_row["converged"]))
    if not run_ok:
        return not_scored("arm_failed", "the arm's ACTINV run for this region failed")
    if dep_a0 is None or dep_arm is None:
        return not_scored("no_loss_row", "the solver's depletion table has no loss channel for this reaction")
    if not (dep_a0 > 0 and dep_arm > 0):
        return not_scored("rate_zero", "a loss rate is zero")
    q = dep_arm / dep_a0
    r_ss = p173_row["r"] + math.log(q)
    return {**row, "status": "scored", "reason_code": None, "reason": None, "q": q,
            "R_ss": p173_row["R_actinv"] * q, "r_ss": r_ss}


# ------------------------------------------------------------------------------------------------------ table mode

def build_table(out):
    cg = require_bounded()
    require_binary()
    require_pins()
    eval_dir = WORK / "eval"
    if eval_dir.exists():
        shutil.rmtree(eval_dir)
    eval_dir.mkdir(parents=True)
    for nuclide, (name, digest) in ENDF_FILES.items():
        target = eval_dir / name
        shutil.copyfile(ENDF_DIR / name, target)
        if p173.sha256_file(target) != digest:
            sys.exit(f"p176_selfshield: {name} is not the pinned evaluation")
    table = WORK / "shield_table.json"
    if table.exists():
        table.unlink()
    SHIELD_CACHE.mkdir(parents=True, exist_ok=True)
    rc, stdout, stderr, wall = run_cmd([ACTINV_BIN, "build-shielding", eval_dir, table, "--projectile", "neutron",
                                        "--groups", "fispact-709", "--profile", "endfb8", "--cache", SHIELD_CACHE],
                                       TABLE_TIMEOUT_S)
    document = {"schema": SCHEMA + "-table", "returncode": rc, "stdout_tail": stdout, "stderr_tail": stderr,
                "wall_s": wall, "environment": environment_record(cg)}
    if rc == 0 and table.is_file():
        content = json.loads(table.read_text())
        document.update(sha256=p173.sha256_file(table), path=str(table), nuclides=sorted(content["nuclides"]),
                        uncovered=sorted(content.get("uncovered", [])),
                        temperatures_K=content.get("temperatures_K"), sigma0_b=content.get("sigma0_b"),
                        evaluations=len(ENDF_FILES))
    p173.write_atomic(out, document)


def table_info():
    """(path, sha256) of the table `table` built, checked against the pin (outside the rehearsal)."""
    meta = json.loads((OUT_DIR / "table.json").read_text())
    if meta.get("returncode") != 0 or not meta.get("sha256"):
        sys.exit("p176_selfshield: the table build did not succeed")
    path = WORK / "shield_table.json"
    digest = p173.sha256_file(path)
    if digest != meta["sha256"]:
        sys.exit("p176_selfshield: the table on disk is not the one the table record describes")
    if not REHEARSAL and digest != SHIELD_TABLE_SHA256:
        sys.exit("p176_selfshield: the table is not the pinned one")
    return path, digest


# ------------------------------------------------------------------------------------------------------ arm mode

def run_arm(arm, model, out):
    cg = require_bounded()
    require_binary()
    require_pins()
    data = load_p173_data()
    table = table_info() if arm == "A1" else None
    work = WORK / "runs"
    work.mkdir(parents=True, exist_ok=True)
    runs = []
    for region in p173.regions_of(model):
        stem = f"{arm}_{region.replace(':', '_')}"
        spec = build_spec(arm, model, region, data, table)
        spec_path, result_path = work / f"{stem}_spec.json", work / f"{stem}_result.json"
        spec_path.write_text(json.dumps(spec))
        if result_path.exists():
            result_path.unlink()
        entry = {"arm": arm, "model": model, "region": region, "spec_sha256": spec_digest(spec),
                 "flux_total_cm_per_source": math.fsum(spec["spectrum"]["flux_per_group"]), "ok": False}
        if arm in ("A2", "A3"):
            entry["foil_thickness_cm"] = spec["self_shielding"]["geometry"]["length_cm"]
            entry["resonance_files"] = [f["path"].rsplit("/", 1)[-1]
                                        for f in spec["self_shielding"]["geometry"]["resonance_files"]]
        rc, _, stderr, wall = run_cmd([ACTINV_BIN, "validate", spec_path], 600)
        entry["validate_returncode"] = rc
        if rc == 0:
            rc, _, stderr, wall = run_cmd([ACTINV_BIN, "run", spec_path, result_path], RUN_TIMEOUT_S)
        entry.update(run_returncode=rc, wall_s=wall, stderr_tail=stderr if rc != 0 else "")
        if rc == 0 and result_path.is_file():
            try:
                result = json.loads(result_path.read_text())
                entry["rates"] = reaction_rates_of(result, model)
                entry["shielding"] = shielding_summary(result)
                entry["ok"] = True
            except (ValueError, KeyError) as error:
                entry["error"] = str(error)
        runs.append(entry)
    p173.write_atomic(out, {"schema": SCHEMA + "-arm", "arm": arm, "model": model, "runs": runs,
                            "actinv_bin_sha256": p173.sha256_file(ACTINV_BIN),
                            "actinv_version": run_cmd([ACTINV_BIN, "--version"], 60)[1].strip(),
                            "environment": environment_record(cg)})


# ------------------------------------------------------------------------------------------------------ assembly

def assemble(record_path):
    cg = require_bounded()
    require_pins()
    p173_rows = {(r["model"], r["region"], r["target"], r["mt"]): r
                 for r in json.loads(P173_RECORD.read_text())["rows"]}
    table = json.loads((OUT_DIR / "table.json").read_text())
    runs, rows, a0_check, cgroups = [], [], [], [{"where": "table", "cgroup": table["environment"]["cgroup"]}]
    bin_digests, versions = set(), set()
    arm_docs = {}
    for arm in ARM_ORDER:
        for model in p173.MODEL_ORDER:
            doc = json.loads((OUT_DIR / f"arm_{arm}_{model}.json").read_text())
            arm_docs[(arm, model)] = doc
            cgroups.append({"where": f"arm:{arm}:{model}", "cgroup": doc["environment"]["cgroup"]})
            bin_digests.add(doc["actinv_bin_sha256"])
            versions.add(doc["actinv_version"])
            runs += doc["runs"]
    a0 = {(model, run["region"]): run for (arm, model), doc in arm_docs.items() if arm == "A0" for run in doc["runs"]}
    for arm in ARM_ORDER:
        for model in p173.MODEL_ORDER:
            by_region = {run["region"]: run for run in arm_docs[(arm, model)]["runs"]}
            for region in p173.regions_of(model):
                for target, mt, _ in p173.model_spec(model)["reactions"]:
                    key = p173.rate_key(target, mt)
                    p173_row = p173_rows[(model, region, target, mt)]
                    run, base = by_region[region], a0[(model, region)]
                    dep_arm = (run.get("rates") or {}).get(key)
                    dep_a0 = (base.get("rates") or {}).get(key)
                    if arm == "A0":
                        total = p173_row.get("total_row_R_actinv")
                        a0_check.append({"model": model, "region": region, "target": target, "mt": mt,
                                         "p173_total_row_rate_per_atom": None if total is None else total * 1e-24,
                                         "a0_loss_rate_per_atom": dep_a0, "a0_run_ok": base["ok"]})
                    else:
                        rows.append(build_ss_row(arm, p173_row, dep_a0, dep_arm, run["ok"] and base["ok"]))
    cgroups.append({"where": "assemble", "cgroup": cg})
    env = environment_record(cg)
    inputs = {name: {"path": str(path), "sha256": p173.sha256_file(path)} for name, (path, _) in PINNED.items()}
    document = {"schema": SCHEMA, "inputs": inputs,
                "environment": {**env, "cgroups": cgroups, "actinv_bin_sha256": sorted(bin_digests),
                                "actinv_version": sorted(versions)},
                "table": {k: table.get(k) for k in ("returncode", "sha256", "nuclides", "uncovered", "evaluations",
                                                     "temperatures_K")},
                "endf_files": {nuc: {"file": name, "sha256": digest} for nuc, (name, digest) in ENDF_FILES.items()},
                "runs": runs, "a0_check": {"tolerance": G1_TOLERANCE, "rows": a0_check}, "rows": rows}
    p173.write_atomic(record_path, document)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="mode", required=True)
    sub.add_parser("table")
    sub.add_parser("assemble")
    p = sub.add_parser("arm")
    p.add_argument("--arm", choices=list(ARM_ORDER), required=True)
    p.add_argument("--model", choices=list(p173.MODEL_ORDER), required=True)
    args = ap.parse_args(argv)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)
    if args.mode == "table":
        build_table(OUT_DIR / "table.json")
    elif args.mode == "arm":
        run_arm(args.arm, args.model, OUT_DIR / f"arm_{args.arm}_{args.model}.json")
    else:
        assemble(RECORD)


if __name__ == "__main__":
    main()
