#!/usr/bin/env python3
"""P149 S3: the P17 identical-data method on long and irregular histories, and on a branching network.

Every code receives the same group cross sections, decay data, flux and schedule, as in
`g2_p17_identical_data.py`: ACTINV (`actinv run`), an independent dense-matrix exponential per step on each
nuclide's causal ancestor subspace, ALARA 2.9.2, and OpenMC 0.15.3 CRAM48 through `IndependentOperator`.
  H1     one year at constant flux, then cooling to 1 h, 1 d, 30 d, 1 y and 10 y;
  H2     twelve 2.5-day steps with flux multipliers 1, 0.5, 0, 2, 1, 0, 1, 0.25, 1, 0, 1, 1 (30 days), then cooling
         to 1 h, 1 d, 30 d and 1 y;
  H3_H1, H3_H2  the P17 network plus Cu-63, Cu-65 and Eu-151 under H1 and H2:
         Cu-63(n,g)Cu-64 and Cu-65(n,2n)Cu-64 from ALARA's FENDL-2 sample library, with Cu-64's beta-minus and
         EC/beta-plus branches from its FENDL-2 sample decay file; Eu-151(n,g)Eu-152 and Eu-151(n,g)Eu-152m1, which
         the sample library (Z <= 30) lacks: their group cross sections are Co-59's (n,g) ground and isomer records
         relabelled (identical inputs to every code; not Eu-151 physics), and the decay records of Eu-151, Eu-152,
         Eu-152m1 and their decay descendants are the ENDF/B-VIII.0 records, appended verbatim to the decay text.
ALARA reports only at shutdown and at the cooling times, so it is compared there; ACTINV, the dense reference and
OpenMC are compared at every step boundary. A nuclide is compared at a boundary when any code holds more than 1e-12
of the initial atoms there.

Usage: p149_s3_histories.py [--out results/p149/s3_histories.json] [--only H1 H2 H3_H1 H3_H2] [--work DIR]
Run with an interpreter that has OpenMC 0.15.3 (as for g2_p17_identical_data.py).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy.linalg import expm

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import chain as independent_chain  # noqa: E402
import g2_p17_identical_data as g2  # noqa: E402
from p9_fixtures import BIN, inventory, relative, sha256  # noqa: E402

ROOT = HERE.parent
# The OpenMC interpreter lacks mpmath; the 40-digit reference runs under the system interpreter.
MP_PYTHON = "/usr/bin/python3"
ENDF_DECAY = Path.home() / "nuclear-data/endfb-viii.0-decay/bulk/endf-b-viii-0_decay.dat"
YEAR = 365.25 * 86400.0
DAY = 86400.0
HISTORIES = {
    "H1": ([(YEAR, 1.0)], [3600.0, DAY, 30 * DAY, YEAR, 10 * YEAR]),
    "H2": ([(2.5 * DAY, m) for m in (1.0, 0.5, 0.0, 2.0, 1.0, 0.0, 1.0, 0.25, 1.0, 0.0, 1.0, 1.0)],
           [3600.0, DAY, 30 * DAY, YEAR]),
}
CASES = {"H1": ("H1", False), "H2": ("H2", False), "H3_H1": ("H1", True), "H3_H2": ("H2", True)}
BASE_RECORDS = list(g2.REACTION_RECORDS)
BASE_INITIAL = list(g2.INITIAL_KEYS)
H3_RECORDS = [
    (29063, 1020, 102, 29064, 0),
    (29065, 160, 16, 29064, 0),
    (63151, 1020, 102, 63152, 0),
    (63151, 1021, 102, 63152, 1),
]
H3_INITIAL = [(29063, 0), (29065, 0), (63151, 0)]
# Co-59 records relabelled as Eu-151: (template code, new header label).
EU_TEMPLATES = {1020: "EU151  (N,G  )EU152  ", 1021: "EU151  (N,G  )EU152M1"}
SYMBOLS = ("n H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Br Kr Rb Sr "
           "Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd").split()
REACTION_NAMES = {16: "(n,2n)", 102: "(n,gamma)", 103: "(n,p)"}
ALARA_ELEMENTS = {11023: "na", 26056: "fe", 27059: "co", 28058: "ni", 29063: "cu63", 29065: "cu65", 63151: "eu151"}


def configure(extended: bool) -> None:
    """Point the P17 module at the base or the H3 network."""
    g2.ATOMIC_SYMBOL = list(SYMBOLS)
    g2.REACTION_RECORDS = BASE_RECORDS + (H3_RECORDS if extended else [])
    g2.INITIAL_KEYS = BASE_INITIAL + (H3_INITIAL if extended else [])
    g2.TARGET_KEYS = sorted({(target, 0) for target, *_ in g2.REACTION_RECORDS})
    g2.INITIAL_TOTAL = g2.INITIAL_PER_TARGET * len(g2.INITIAL_KEYS)


def endf_sections(path: Path) -> dict[int, list[str]]:
    """Every material's lines (MF/MT records through its last line), keyed by MAT."""
    sections: dict[int, list[str]] = defaultdict(list)
    with path.open(errors="replace") as stream:
        for line in stream:
            try:
                material = int(line[66:70])
            except ValueError:
                continue
            if material > 0:
                sections[material].append(line.rstrip("\n"))
    return sections


def extended_inputs(paths: dict[str, Path], work: Path) -> tuple[dict[str, Path], dict]:
    """The sample activation text plus relabelled Eu-151 records, and the sample decay text plus ENDF/B-VIII.0
    records for Eu-151, Eu-152, Eu-152m1 and every decay descendant."""
    lines = paths["activation"].read_text().splitlines()
    added = []
    for code, label in EU_TEMPLATES.items():
        start = next(i for i, line in enumerate(lines) if re.match(rf"^\s*270590\s+{code}\s", line))
        count = int(lines[start].split()[2])
        end = start + 3
        values = 0
        while values < count:
            values += len(lines[end].split())
            end += 1
        header = lines[start]
        fields = re.match(r"^(\s*)270590(\s+\d+\s+\d+\s+)(.{21})(.*)$", header)
        added.append([f"{fields.group(1)}631510{fields.group(2)}{label}{fields.group(4)}", *lines[start + 1:end]])
    activation = work / "h3-activation.eaf"
    activation.write_text("\n".join(lines + [line for record in added for line in record]) + "\n")

    keys, records, *_ = independent_chain.build(ENDF_DECAY)
    by_key = {(int(round(records[m]["za"])), int(records[m]["liso"])): m for m in keys}
    wanted, frontier = set(), [(63151, 0), (63152, 0), (63152, 1)]
    while frontier:
        key = frontier.pop()
        if key in wanted or key not in by_key:
            continue
        wanted.add(key)
        z, mass = divmod(key[0], 1000)
        for mode in records[by_key[key]]["modes"]:
            daughter_z, daughter_mass = z, mass
            digits = independent_chain.rtyp_digits(mode["rtyp"])
            if any(d not in independent_chain.STEP for d in digits) or mode["br"] <= 0.0:
                continue
            for digit in digits:
                daughter_z += independent_chain.STEP[digit][0]
                daughter_mass += independent_chain.STEP[digit][1]
            frontier.append((daughter_z * 1000 + daughter_mass, int(round(mode["rfs"]))))
    missing = {(63151, 0), (63152, 0), (63152, 1)} - wanted
    if missing:
        raise RuntimeError(f"ENDF/B-VIII.0 decay records missing for {sorted(missing)}")
    sections = endf_sections(ENDF_DECAY)
    mend = f"{'':66}{0:4d}{0:2d}{0:3d}{0:5d}"
    appended = []
    for key in sorted(wanted):
        appended += sections[records[by_key[key]]["mat"]] + [mend]
    decay_lines = paths["decay"].read_text(errors="replace").splitlines()
    tend = max(i for i, line in enumerate(decay_lines) if line[66:70].strip() == "-1")
    decay = work / "h3-decay.endf"
    decay.write_text("\n".join(decay_lines[:tend] + appended + decay_lines[tend:]) + "\n")
    return ({**paths, "activation": activation, "decay": decay},
            {"activation_sha256": sha256(activation), "decay_sha256": sha256(decay),
             "eu_cross_sections": "Co-59 (n,g) ground and isomer records relabelled as Eu-151",
             "endf_b_viii0_decay_sha256": sha256(ENDF_DECAY),
             "appended_decay_nuclides": [g2.actinv_name(key) for key in sorted(wanted)]})


def segments(history: str) -> tuple[list[tuple[float, float]], int, list[float]]:
    irradiation, cooling = HISTORIES[history]
    gaps = [cooling[0]] + [b - a for a, b in zip(cooling, cooling[1:])]
    return irradiation + [(gap, 0.0) for gap in gaps], len(irradiation), cooling


def dense_boundaries(network: dict, rates: dict, schedule: list[tuple[float, float]],
                     work: Path) -> tuple[np.ndarray, dict]:
    """State after every segment, each nuclide solved on its causal ancestor subspace (as `g2.dense_solution`).

    The exponentials are evaluated at 40 digits by `p149_mp_dense.py` under `MP_PYTHON`: in double precision a
    one-year step with millisecond isomers (rate times duration near 1e9) loses about 1e-8 relative accuracy.
    The double-precision scipy result is kept beside it so the difference is on record.
    """
    initial = g2.initial_state(network)
    matrices = [g2.transition_matrix(network, rates, multiplier)[:-1, :-1] for _d, multiplier in schedule]
    size = len(network["keys"])
    predecessors = [set() for _ in range(size)]
    for matrix in matrices:
        for row, column in zip(*np.nonzero(matrix)):
            if row != column:
                predecessors[row].add(column)
    double = np.zeros((len(schedule), size))
    blocks = []
    for target in range(size):
        ancestors, frontier = {target}, [target]
        while frontier:
            for parent in predecessors[frontier.pop()] - ancestors:
                ancestors.add(parent)
                frontier.append(parent)
        selected = sorted(ancestors)
        local = selected.index(target)
        state = initial[selected].copy()
        block = {"target": target, "local": local, "initial": [float(v) for v in state], "matrices": []}
        for step, ((duration, _m), matrix) in enumerate(zip(schedule, matrices)):
            sub = matrix[np.ix_(selected, selected)]
            block["matrices"].append(sub.tolist())
            state = expm(sub * duration) @ state
            double[step, target] = state[local]
        blocks.append(block)
    request, response = work / "mp_dense_in.json", work / "mp_dense_out.json"
    request.write_text(json.dumps({"durations": [d for d, _m in schedule], "blocks": blocks}))
    subprocess.run([MP_PYTHON, str(HERE / "p149_mp_dense.py"), str(request), str(response)],
                   check=True, timeout=7200)
    answer = json.loads(response.read_text())
    out = np.zeros((len(schedule), size))
    for target, series in answer["values"].items():
        out[:, int(target)] = [float(v) for v in series]
    evidence = {"mpmath": answer["mpmath"], "digits": answer["dps"], "check_digits": answer["check_dps"],
                "max_relative_40_vs_60_digits": answer["max_relative_check"],
                "max_relative_double_vs_40_digits": max(
                    (relative(float(double[k, i]), float(out[k, i])) for k in range(len(schedule)) for i in range(size)
                     if max(abs(double[k, i]), abs(out[k, i])) > g2.REPORTABLE_FRACTION * g2.INITIAL_TOTAL),
                    default=0.0)}
    return out, evidence


def alara_time(seconds: float) -> str:
    """A cooling time in the largest of ALARA's s, m, h, d units that writes it exactly and short enough.

    ALARA prints each cooling time as `sprintf("%7g %c   ")` into a 16-byte buffer (src/CoolingTime.C), so the `%g`
    text must be at most 10 characters and must read back as the same value. Its `y` unit is 364 days
    (`365/7` truncates, src/input_file_utils.C), so years are written in days.
    """
    for unit, scale in (("d", 86400.0), ("h", 3600.0), ("m", 60.0), ("s", 1.0)):
        value = seconds / scale
        text = f"{value:g}"
        if len(text) <= 10 and float(text) * scale == seconds:
            return f"{text} {unit}"
    raise ValueError(f"ALARA cannot label a cooling time of {seconds!r} s exactly")


def alara_input(schedule: list[tuple[float, float]], cooling: list[float], library_stem: str, element_library: str,
                flux_name: str) -> str:
    targets = [key for key in g2.INITIAL_KEYS]
    fraction = 1.0 / len(targets)
    mixture = "\n".join(
        f"    element {ALARA_ELEMENTS[za]}:p17 "
        f"{g2.INITIAL_PER_TARGET * (za % 1000) / (6.02e23 * fraction):.17e} {fraction:.17e}" for za, _s in targets)
    multipliers = sorted({m for _d, m in schedule if m > 0.0})
    names = {m: f"flux_{i}" for i, m in enumerate(multipliers)}
    fluxes = "\n".join(f"flux {names[m]} {flux_name} {m:.17e} 0 default" for m in multipliers)
    items = []
    for duration, multiplier in schedule:
        if multiplier > 0.0:
            items.append([duration, names[multiplier], 0.0])
        else:
            if not items:
                raise RuntimeError("a schedule may not open with a gap")
            items[-1][2] += duration
    lines = "\n".join(f"    {d:.17e} s {n} steady {delay:.17e} s" for d, n, delay in items)
    cooling_lines = "\n".join(f"    {alara_time(t)}" for t in cooling)
    return f"""geometry rectangular

volume
    1.0 zone_0
end

mat_loading
    zone_0 mix_0
end

mixture mix_0
{mixture}
end

element_lib {element_library}
data_library alaralib {library_stem}
{fluxes}

schedule total
{lines}
end

pulsehistory steady
    1 0 s
end

cooling
{cooling_lines}
end

output zone
    number_density
end

dump_file dump_files/p149-s3.dump
truncation 1e-24
"""


def run_alara(work: Path, paths: dict, schedule, cooling, network: dict) -> tuple[dict, dict]:
    subset, _records = g2.extract_records(paths["activation"])
    activation = work / "p149-s3.eaf"
    activation.write_text("\n".join(subset) + "\n")
    convert = work / "convert-p149-s3"
    convert.write_text(f"convert_lib eaflib alaralib {activation.name} {paths['decay']} p149-s3-bin\n")
    conversion = subprocess.run([str(g2.ALARA_BIN), convert.name], cwd=work, timeout=600.0, capture_output=True, text=True)
    elements = work / "p149-s3-elements"
    additions = []
    for za, _state in g2.INITIAL_KEYS:
        mass = za % 1000
        additions += [f"{ALARA_ELEMENTS[za]}:p17 {mass:.17e} {za // 1000} 1.00000000000000000e+00 1",
                      f"    {mass} 1.00000000000000000e+02"]
    elements.write_text(paths["element"].read_text().rstrip() + "\n" + "\n".join(additions) + "\n")
    flux = work / "p149-s3-flux"
    g2.write_flux(flux, g2.flux_vector())
    run_input = work / "p149-s3-run"
    run_input.write_text(alara_input(schedule, cooling, "p149-s3-bin", elements.name, flux.name))
    (work / "dump_files").mkdir(exist_ok=True)
    run = subprocess.run([str(g2.ALARA_BIN), run_input.name], cwd=work, timeout=1800.0, capture_output=True, text=True)
    transcript = run.stdout + run.stderr
    (work / "alara-transcript.txt").write_text(transcript)
    evidence = {"conversion_returncode": conversion.returncode, "run_returncode": run.returncode,
                "run_input_sha256": sha256(run_input), "activation_subset_sha256": sha256(activation),
                "transcript_tail": transcript[-600:] if run.returncode else None}
    if conversion.returncode or run.returncode:
        return {}, evidence
    lines = transcript.splitlines()
    header = next(i for i, line in enumerate(lines) if "isotope" in line and "pre-irrad" in line)
    columns = 2 + len(cooling)  # shutdown, then each cooling time (after the pre-irradiation column)
    table, unlabeled = {}, []
    number = r"([^\s]+)"
    labelled = re.compile(rf"^\s*([a-z]{{1,2}}-\d+m?)\s+{number}\s+{number}" + rf"\s+{number}" * (columns - 1), re.I)
    bare = re.compile(rf"^\s*-(\d+)(m?)\s+{number}\s+{number}" + rf"\s+{number}" * (columns - 1), re.I)
    for line in lines[header + 2:]:
        match = labelled.match(line)
        if match:
            values = [float(v) for v in match.groups()[1:]]
            table[match.group(1).lower()] = {"half_life_s": values[0], "pre_irrad": values[1], "after": values[2:]}
            continue
        match = bare.match(line)
        if match:
            values = [float(v) for v in match.groups()[2:]]
            unlabeled.append({"mass": int(match.group(1)), "state": 1 if match.group(2) else 0,
                              "half_life_s": values[0], "pre_irrad": values[1], "after": values[2:]})
        elif table and not line.strip():
            break
    unused = set(network["keys"]) - {key for key in network["keys"] if g2.alara_name(key) in table}
    # ALARA lists isotopes by Z, then A, then state, so a row's nuclide is the lowest-Z unused nuclide of its mass
    # and state at or above the previous row's Z. Half-life and initial loading break ties and are cross-checks.
    previous_z, half_life_differs = 0, []
    for row in unlabeled:
        scored = []
        for key in unused:
            if key[0] % 1000 != row["mass"] or key[1] != row["state"] or key[0] // 1000 < previous_z:
                continue
            record = network["metadata"][key]
            expected = -1.0 if record["nst"] == 1 else record["half_life"]
            if (key in g2.INITIAL_KEYS) != (row["pre_irrad"] > 0.0):
                continue
            score = 0.0 if expected < 0.0 and row["half_life_s"] < 0.0 else relative(expected, row["half_life_s"])
            scored.append((score, key))
        if not scored:
            if row["pre_irrad"] > 0.0:
                raise RuntimeError(f"no initial nuclide matches ALARA row {row}")
            continue  # light particles, which ACTINV does not track
        best = min(score for score, _key in scored)
        if len(scored) > 1 and best > 5.0e-4:
            raise RuntimeError(f"ambiguous ALARA row {row}: {sorted(scored)}")
        score, key = min((s_, k) for s_, k in scored if s_ - best <= 1e-12) if len(scored) > 1 else scored[0]
        if score > 5.0e-4:
            half_life_differs.append({"nuclide": g2.actinv_name(key), "alara_half_life_s": row["half_life_s"],
                                      "record_half_life_s": network["metadata"][key]["half_life"]})
        previous_z = key[0] // 1000
        unused.remove(key)
        table[g2.alara_name(key)] = {k: row[k] for k in ("half_life_s", "pre_irrad", "after")}
    evidence["half_life_differs"] = half_life_differs
    evidence["rows"] = len(table)
    evidence["unlabeled_rows"] = len(unlabeled)
    # ALARA can print -nan or inf for a nuclide; every such row, compared or not, is recorded, and none is compared.
    evidence["nonfinite_rows"] = sorted(name for name, row in table.items()
                                        if not all(np.isfinite(v) for v in row["after"]))
    evidence["nonfinite_unlabeled_rows"] = [row | {"after": [v if np.isfinite(v) else repr(v) for v in row["after"]]}
                                            for row in unlabeled if not all(np.isfinite(v) for v in row["after"])]
    return table, evidence


def run_openmc(work: Path, network: dict, rates: dict, records: list[dict], schedule) -> tuple[np.ndarray, dict]:
    import openmc
    import openmc.deplete

    flux = g2.flux_vector()
    chain_object = openmc.deplete.Chain()
    key_index = {key: i for i, key in enumerate(network["keys"])}
    grouped = defaultdict(list)
    for target, code, mt, product, state in g2.REACTION_RECORDS:
        grouped[(target, mt)].append(((product, state), rates[(target, code)]))
    for key in network["keys"]:
        nuclide = openmc.deplete.Nuclide(g2.openmc_name(key))
        constant = network["decay_constants"][key]
        if constant > 0.0:
            nuclide.half_life = np.log(2.0) / constant
            for parent, daughter, value in network["decay_edges"]:
                if parent == key and value > 0.0:
                    nuclide.add_decay_mode("p149", g2.openmc_name(daughter) if daughter in key_index else None,
                                           value / constant)
        for (target, mt), products in sorted(grouped.items()):
            if key == (target, 0):
                total = sum(rate for _p, rate in products)
                for product, rate in products:
                    nuclide.add_reaction(REACTION_NAMES[mt], g2.openmc_name(product), 0.0, rate / total)
        chain_object.add_nuclide(nuclide)
    directory = work / "openmc"
    directory.mkdir()
    chain_path = directory / "chain.xml"
    chain_object.export_to_xml(chain_path)
    reactions = sorted({REACTION_NAMES[mt] for _t, _c, mt, _p, _s in g2.REACTION_RECORDS})
    mts = {name: mt for mt, name in REACTION_NAMES.items()}
    total_flux = float(np.sum(flux))
    by_id = g2.record_map(records)
    micro = np.zeros((len(g2.TARGET_KEYS), len(reactions), 1))
    for t, (target, _s) in enumerate(g2.TARGET_KEYS):
        for r, name in enumerate(reactions):
            selected = [by_id[(rt, code)]["sigma_high_to_low"] for rt, code, mt, *_ in g2.REACTION_RECORDS
                        if rt == target and mt == mts[name]]
            if selected:
                micro[t, r, 0] = float(np.dot(np.sum(selected, axis=0), flux) / total_flux)
    micro_xs = openmc.deplete.MicroXS(micro, [g2.openmc_name(k) for k in g2.TARGET_KEYS], reactions)
    operator = openmc.deplete.IndependentOperator.from_nuclides(
        volume=1.0, nuclides={g2.openmc_name(k): g2.INITIAL_PER_TARGET for k in g2.INITIAL_KEYS},
        flux=np.asarray([1.0]), micro_xs=micro_xs, chain_file=chain_path, nuc_units="atom/cm3",
        normalization_mode="source-rate")
    operator.output_dir = directory
    integrator = openmc.deplete.PredictorIntegrator(
        operator, [d for d, _m in schedule], source_rates=[total_flux * m for _d, m in schedule],
        timestep_units="s", solver="cram48")
    integrator.integrate(final_step=False, output=False, path="depletion_results.h5")
    results = openmc.deplete.Results(directory / "depletion_results.h5")
    material = operator.burnable_mats[0]
    out = np.zeros((len(schedule), len(network["keys"])))
    times = None
    for i, key in enumerate(network["keys"]):
        times, values = results.get_atoms(material, g2.openmc_name(key), nuc_units="atom/cm3", time_units="s")
        out[:, i] = values[1:len(schedule) + 1]
    return out, {"version": openmc.__version__, "chain_sha256": sha256(chain_path),
                 "final_time_s": float(times[len(schedule)])}


def run_case(name: str, work: Path) -> dict:
    history, extended = CASES[name]
    configure(extended)
    schedule, n_irradiation, cooling = segments(history)
    g2.SCHEDULE = schedule
    paths = g2.checked_paths()
    data_evidence = None
    if extended:
        paths, data_evidence = extended_inputs(paths, work)
    _subset, records = g2.extract_records(paths["activation"])
    network = g2.reachable_decay_network(paths["decay"])
    flux = g2.flux_vector()
    library, _index = g2.write_actinv_library(work, records)
    adapter, _keys = g2.write_stable_adapter(work, network)
    result = g2.run_actinv(work, paths, library, flux, adapter)
    rates = g2.collapsed_rates(records, flux)
    dense, dense_evidence = dense_boundaries(network, rates, schedule, work)
    openmc_values, openmc_evidence = run_openmc(work, network, rates, records, schedule)
    # ALARA takes the irradiation as its schedule and the cooling times from its own cooling block.
    alara, alara_evidence = run_alara(work, paths, schedule[:n_irradiation], cooling, network)
    threshold = g2.REPORTABLE_FRACTION * g2.INITIAL_TOTAL
    boundaries = []
    for step in range(len(schedule)):
        actinv_step = inventory(result["steps"][step])
        alara_column = (step - (n_irradiation - 1)) if step >= n_irradiation - 1 else None
        rows = {}
        for i, key in enumerate(network["keys"]):
            label = g2.actinv_name(key)
            values = {"actinv": float(actinv_step.get(label, 0.0)), "dense": float(dense[step, i]),
                      "openmc": float(openmc_values[step, i])}
            alara_value = None
            if alara and alara_column is not None:
                alara_value = float(alara.get(g2.alara_name(key), {}).get("after", [0.0] * 99)[alara_column])
                if np.isfinite(alara_value):
                    values["alara"] = alara_value
            if max(values.values()) <= threshold:
                continue
            rows[label] = values | {"actinv_dense": relative(values["actinv"], values["dense"]),
                                    "openmc_dense": relative(values["openmc"], values["dense"])}
            if "alara" in values:
                rows[label]["actinv_alara"] = relative(values["actinv"], values["alara"])
            elif alara_value is not None:
                rows[label]["alara_nonfinite"] = repr(alara_value)
        worst = {key: max(((row[key], label) for label, row in rows.items() if key in row), default=None)
                 for key in ("actinv_dense", "openmc_dense", "actinv_alara")}
        boundaries.append({"step": step, "t_s": result["steps"][step]["t_s"], "flux": schedule[step][1],
                           "alara_reported": bool(alara) and alara_column is not None, "compared": len(rows),
                           "worst": worst, "rows": rows})
    overall = {key: max((b["worst"][key] for b in boundaries if b["worst"][key] is not None), default=None)
               for key in ("actinv_dense", "openmc_dense", "actinv_alara")}
    return {"history": history, "extended_network": extended, "schedule": schedule, "cooling_s": cooling,
            "initial": [g2.actinv_name(k) for k in g2.INITIAL_KEYS], "network_nuclides": len(network["keys"]),
            "reaction_records": [list(r) for r in g2.REACTION_RECORDS], "extended_data": data_evidence,
            "actinv": {"mode": result["mode"], "result_steps": len(result["steps"]),
                       "products_unmapped_to_leakage": result["ledger"]["products_unmapped_to_leakage"],
                       "products_no_evaluated_decay_data": result["ledger"]["products_no_evaluated_decay_data"]},
            "dense_reference": dense_evidence, "alara_run": alara_evidence, "openmc": openmc_evidence,
            "maximum_relative": overall,
            "boundaries": boundaries}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=ROOT / "results/p149/s3_histories.json")
    ap.add_argument("--only", nargs="*", choices=list(CASES), default=list(CASES))
    ap.add_argument("--work", type=Path)
    args = ap.parse_args()
    root = (args.work or Path(tempfile.mkdtemp(prefix="p149-s3-", dir=str(ROOT / "target")))).resolve()
    record = json.loads(args.out.read_text()) if args.out.exists() else {}
    record.update({"schema": "actinv-p149-s3-histories-1", "protocol": "P149 S3",
                   "actinv_binary_sha256": sha256(BIN), "alara_binary_sha256": sha256(g2.ALARA_BIN),
                   "reportable_fraction_of_initial": g2.REPORTABLE_FRACTION})
    cases = record.setdefault("cases", {})
    for name in args.only:
        work = root / name
        work.mkdir(parents=True)
        cases[name] = run_case(name, work)
        print(f"{name}: {cases[name]['maximum_relative']}", file=sys.stderr, flush=True)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
