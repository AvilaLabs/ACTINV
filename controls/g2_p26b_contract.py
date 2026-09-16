#!/usr/bin/env python3
"""P26b G2: freeze the comparison-leg contract before any comparator run.

Builds results/g2_p26b_leg_contract.json:

  * re-derives the 1,016-case executable population from the frozen P26
    workload rules and verifies the list is identical to the frozen P26
    contract case list (independent regeneration, not a copy);
  * expands each case's material elements to required isotope parents using
    the embedded natural-abundance table (crates/actinv-data/src/tables.rs),
    checked against the G0-frozen contract_elements map;
  * evaluates per-leg executability against LIVE library coverage:
      identical_data     = ACTINV on the G1 FENDL-3.2c artifact
                           vs ALARA on the G1 FENDL-3.2c library
      product_plus_data  = ACTINV on the shipped v1.1.0 TENDL-2025-patched
                           artifact vs ALARA on the G1 FENDL-3.2c library
  * freezes spectra derivations, responses, tolerances, failure categories,
    timeouts, resource limits, decision fields and the diagnostic probe.

A case is contract_gap on a leg when at least one required isotope parent is
absent from either arm's library; the gap is recorded per case with the
missing isotope list.  Nothing here fabricates comparator output.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROTOCOL = ROOT / "protocols" / "ACTINV-P26b_PROTOCOL.md"
G0_SEALS = ROOT / "results" / "g0_p26b_seals.json"
G1_RECORD = ROOT / "results" / "g1_p26b_conversion.json"
P26_CONTRACT = ROOT / "results" / "g2_p26_contract.json"
TABLES_RS = ROOT / "crates" / "actinv-data" / "src" / "tables.rs"
ELELIB = Path.home() / "nuclear-data" / "alara-2.9.2" / "sample" / "data" / "myElelib"
OUT = ROOT / "results" / "g2_p26b_leg_contract.json"

P26B_WORK = Path.home() / "nuclear-data" / "p26b-work"
G1_RUN = P26B_WORK / "g1-run"
ALARA_LIB_BASE = G1_RUN / "fendl32c_709"
ACTINV_FENDL_NPZ = G1_RUN / "actinv_fendl32c_709.npz"
ACTINV_SHIPPED_NPZ = ROOT / "target" / "p25c-release" / "tendl-2025-patched-neutron-709g.npz"
DATA_CATALOG = ROOT / "target" / "p25c-release" / "assets" / "actinv-data-catalog-v1.1.0.json"
DECAY_PRIMARY = ROOT / "actinv-data" / "v1.0.0" / "decay" / "endf-b-viii-0_decay.dat"
DECAY_FALLBACK = ROOT / "actinv-data" / "v1.0.0" / "decay" / "jeff-3-3_decay.dat"

COOLING_TIMES_S = [0.0, 86400.0, 2592000.0, 31536000.0, 315360000.0]
# fispact-24 upper bounds ascending (eV); lower edge of group 0 is 0
FISPACT24_UPPER_BOUNDS_EV = [
    1e4, 2e4, 5e4, 1e5, 2e5, 3e5, 4e5, 6e5, 8e5, 1e6, 1.22e6, 1.44e6,
    1.66e6, 2e6, 2.5e6, 3e6, 4e6, 5e6, 6.5e6, 8e6, 1e7, 1.2e7, 1.4e7, 2e7,
]
RESPONSES = [
    "total_activity_bq_per_g",
    "decay_heat_w_per_g",
    "photon_source_per_group_24",
    "top5_nuclides_by_activity",
    "total_atoms_per_g",
    "product_atoms_per_g",
]

# Periodic-table Z for the 12 contract elements (deterministic, fixed).
ZTABLE = {"V": 23, "CR": 24, "MN": 25, "FE": 26, "CO": 27, "NI": 28,
          "CU": 29, "NB": 41, "MO": 42, "AG": 47, "TA": 73, "W": 74}
ZINV = {v: k for k, v in ZTABLE.items()}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for blk in iter(lambda: fh.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def sha256_json(obj) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def sha256_array(arr: list[float]) -> str:
    return hashlib.sha256(
        json.dumps(arr, separators=(",", ":")).encode()).hexdigest()


def git_head() -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                          capture_output=True, text=True, check=True).stdout.strip()


def parse_abundance_table() -> dict:
    """element -> [(A, liso, atom_fraction)] from the embedded table."""
    rows: dict[str, list[dict]] = {}
    pat = re.compile(r'\("([A-Za-z]{1,2})",\s*(\d+),\s*(\d+),\s*([0-9.eE+-]+),')
    for line in TABLES_RS.read_text().splitlines():
        m = pat.search(line)
        if m:
            sym, a, liso, frac = (m.group(1).upper(), int(m.group(2)),
                                  int(m.group(3)), float(m.group(4)))
            rows.setdefault(sym, []).append(
                {"A": a, "liso": liso, "atom_fraction": frac})
    return rows


def nuclide_name(sym: str, a: int, liso: int) -> str:
    s = f"{sym.title()}-{a}"
    if liso == 1:
        s += "m"
    elif liso > 1:
        s += f"m{liso}"
    return s


def parse_isotope(name: str):
    m = re.fullmatch(r"([A-Za-z]+)-(\d+)(m(\d*))?", name)
    if not m:
        raise ValueError(name)
    sym, a, iso = m.group(1).upper(), int(m.group(2)), m.group(3)
    liso = 0 if not iso else (int(iso[1:]) if len(iso) > 1 else 1)
    return sym, a, liso


def to_kza(sym: str, a: int, liso: int) -> int:
    return ZTABLE[sym] * 10000 + a * 10 + liso


def to_za(sym: str, a: int) -> int:
    return ZTABLE[sym] * 1000 + a


def kza_to_name(kza: int) -> str:
    liso = kza % 10
    a = (kza // 10) % 1000
    z = kza // 10000
    return nuclide_name(ZINV.get(z, f"Z{z}"), a, liso)


def alara_parents(lib_idx: Path) -> set[int]:
    parents = set()
    for line in lib_idx.read_text(errors="replace").splitlines():
        if line.startswith("\t") or not line.strip():
            continue
        tok = line.split()[0]
        # parent rows carry a KZA (Z*10000+A*10+liso, >= 10010); the two header
        # rows (-1 <n>; 0 <n>) are excluded by the bound
        if tok.isdigit() and int(tok) > 1000:
            parents.add(int(tok))
    return parents


def actinv_targets(index_json: Path) -> set[int]:
    idx = json.loads(index_json.read_text())
    return {int(t["za"]) for t in idx["targets"]}


sys.path.insert(0, str(ROOT / "controls"))
from g3_p26_headroom import collapse_to_709, fns_spectrum  # noqa: E402


def irdff_spectrum() -> list[float]:
    """Frozen P26 Amendment-1 rule (overlap-conserving collapse of MAT 9861
    MT=261 onto the NPZ 'bounds' edges), re-executed via the frozen G3 helper.
    Output is in ascending-energy group order."""
    return collapse_to_709()


def elelib_parse() -> dict:
    """element -> {density_g_cm3, isotopes: {A: atom_fraction}} from the ALARA
    element library.  Element lines: `<sym> <avg_mass> <Z> <rho> <n_iso>`;
    isotope continuation lines are indented `<A> <fraction_percent>`; `sym:A`
    mono-isotopic pseudo-elements are excluded (contract uses base elements)."""
    out = {}
    cur = None
    for line in ELELIB.read_text(errors="replace").splitlines():
        f = line.split()
        if not f:
            continue
        if line[0].isspace():
            if cur is not None and len(f) == 2:
                out[cur]["isotopes"][int(f[0])] = float(f[1]) / 100.0
            continue
        if ":" in f[0]:  # mono-isotopic pseudo-element, e.g. fe:56
            cur = None
            continue
        if len(f) == 5 and re.fullmatch(r"[a-z]{1,2}", f[0]) and f[2].isdigit():
            cur = f[0].upper()
            out[cur] = {"density_g_cm3": float(f[3]), "isotopes": {}}
    return out


def elelib_densities() -> dict:
    return {el: d["density_g_cm3"] for el, d in elelib_parse().items()}


def gen_cases() -> list[dict]:
    cases = []
    materials = {
        "fe": {"FE": 1.0},
        "fe_co100wppm": {"FE": 0.9999, "CO": 0.0001},
        "fe_nb100wppm": {"FE": 0.9999, "NB": 0.0001},
        "w": {"W": 1.0},
    }
    schedules = {"pulse_5min": 300.0, "cont_2y": 63072000.0}
    spectra = ["fns_709", "irdff_sp_mat9861_709"]
    for mat_name, comp in materials.items():
        for spec in spectra:
            for sched_name, irr_s in schedules.items():
                cases.append({
                    "case": f"{mat_name}__{spec}__{sched_name}",
                    "workload": "W-MATCMP",
                    "composition_wt_fraction": comp,
                    "spectrum": spec,
                    "irradiation_s": irr_s,
                })
    impurities = ["CO", "NB", "NI", "MO", "AG", "TA", "V", "CU", "CR", "MN"]
    concs = [1, 10, 100, 1000, 10000, 100000, 300, 3000, 30000, 30]
    durations = [60.0, 600.0, 3600.0, 86400.0, 2592000.0]
    for imp in impurities:
        for c in concs:
            for spec in spectra:
                for d in durations:
                    wf = c * 1e-6
                    cases.append({
                        "case": f"fe_{imp.lower()}{c}wppm__{spec}__{int(d)}s",
                        "workload": "W-CAMPAIGN",
                        "composition_wt_fraction": {"FE": 1.0 - wf, imp: wf},
                        "spectrum": spec,
                        "irradiation_s": d,
                    })
    return cases


def leg_status(arms: list[tuple[str, set[int], str, list[str]]]) -> dict:
    """arms: [(arm_name, coverage_set, code_system, required_isotopes)].
    Coverage evaluated in each arm's own code system against that arm's own
    required-isotope set (ACTINV expands via its abundance table; ALARA via
    its element library).  Missing isotopes are reported by name per arm."""
    missing = {}
    for arm_name, coverage, code, required in arms:
        miss = []
        for iso in required:
            sym, a, liso = parse_isotope(iso)
            key = to_kza(sym, a, liso) if code == "kza" else to_za(sym, a)
            if key not in coverage:
                miss.append(iso)
        if miss:
            missing[arm_name] = sorted(miss)
    out = {"status": "executable" if not missing else "contract_gap"}
    if missing:
        out["missing"] = missing
    return out


def main() -> int:
    abund = parse_abundance_table()
    seals = json.loads(G0_SEALS.read_text())
    g1 = json.loads(G1_RECORD.read_text())
    p26 = json.loads(P26_CONTRACT.read_text())

    # --- isotope expansion consistency vs G0 contract_elements
    # G0 stores {base: {EL: [isotopes]}, impurity: {EL: [isotopes]}}
    g0_nested = seals["executable_subset"]["contract_elements"]
    contract_elements = {el: sorted(isos) for grp in g0_nested.values()
                         for el, isos in grp.items()}
    derived = {el: sorted(nuclide_name(el, i["A"], i["liso"]) for i in isos)
               for el, isos in abund.items() if el in contract_elements}
    g0_map = contract_elements
    mismatches = {el: {"g0": g0_map.get(el), "derived": derived.get(el)}
                  for el in g0_map if g0_map.get(el) != derived.get(el)}
    if mismatches:
        raise RuntimeError(
            f"abundance-table expansion disagrees with G0 map: {mismatches}")

    # --- live coverage of each arm
    idx_path = Path(str(ALARA_LIB_BASE) + ".idx")
    alara_cov = alara_parents(idx_path)                                   # KZA
    actinv_fendl_cov = actinv_targets(
        Path(str(ACTINV_FENDL_NPZ).replace(".npz", "_index.json")))       # ZA
    actinv_shipped_cov = actinv_targets(
        Path(str(ACTINV_SHIPPED_NPZ).replace(".npz", "_index.json")))     # ZA

    # --- cases + per-leg status
    cases = gen_cases()
    p26_names = sorted(c["case"] for wl in p26["workloads"].values()
                       for c in wl["eligible_population"].get("cases", []))
    if p26_names != sorted(c["case"] for c in cases):
        raise RuntimeError("re-generated case list differs from frozen P26 contract")

    elelib = elelib_parse()

    counts = {"identical_data": {"executable": 0, "contract_gap": 0},
              "product_plus_data": {"executable": 0, "contract_gap": 0}}
    for c in cases:
        # ACTINV expands each material element to every natural isotope in its
        # embedded abundance table (including metastables such as Ta-180m).
        req_actinv = sorted({nuclide_name(el, i["A"], i["liso"])
                             for el in c["composition_wt_fraction"]
                             for i in abund[el]})
        # ALARA expands via its element library (ground states; e.g. ta -> Ta-181 only).
        req_alara = sorted({nuclide_name(el, a, 0)
                            for el in c["composition_wt_fraction"]
                            for a in elelib[el]["isotopes"]})
        c["required_isotopes"] = {"actinv": req_actinv, "alara": req_alara}
        id_status = leg_status([
            ("actinv_fendl32c_709", actinv_fendl_cov, "za", req_actinv),
            ("alara_fendl32c_709", alara_cov, "kza", req_alara),
        ])
        pp_status = leg_status([
            ("actinv_shipped_tendl2025", actinv_shipped_cov, "za", req_actinv),
            ("alara_fendl32c_709", alara_cov, "kza", req_alara),
        ])
        c["legs"] = {"identical_data": id_status, "product_plus_data": pp_status}
        for leg, st in (("identical_data", id_status),
                        ("product_plus_data", pp_status)):
            counts[leg][st["status"]] += 1

    # --- spectra (derivation + digests)
    sp_prov = p26["workloads"]["W-MATCMP"]["eligible_population"]["spectra"]
    fns = fns_spectrum()
    irdff_asc_raw = irdff_spectrum()
    # The P26 collapse preserves shape only (sum ~= 1).  At unit total flux,
    # product inventories sit ~1e-20 of root and ALARA's truncation-tolerance
    # machinery cannot resolve them without chain explosion; the meaningful
    # comparison is spectral shape at matched total flux.  IRDFF is therefore
    # normalized to the fns_709 total so both spectra exercise the same
    # solver depth under one truncation tolerance.  Both arms receive the
    # same scaled values; the sha256 below is of the scaled arrays.
    irdff_scale = sum(fns) / sum(irdff_asc_raw)
    irdff_asc = [v * irdff_scale for v in irdff_asc_raw]
    spectra = {
        "fns_709": {
            "source": sp_prov.get("fns_709", {}).get("source"),
            "order": "descending (fispact-709, index 0 = highest energy)",
            "groups": len(fns),
            "total_flux_n_cm2_s": sum(fns),
            "sha256": sha256_array(fns),
            "alara_flux_file_order": "descending (library group order)",
            "actinv_spec": {"descending": True, "total": sum(fns)},
        },
        "irdff_sp_mat9861_709": {
            "source": sp_prov.get("irdff_sp_mat9861_709", {}).get("source"),
            "derivation": sp_prov.get("irdff_sp_mat9861_709", {}).get("derivation"),
            "normalization": "frozen overlap-conserving collapse preserves shape "
                             "(raw sum ~= 1); scaled by %.12g to the fns_709 total "
                             "flux %.12g n/cm2/s so both spectra run at matched "
                             "total flux and ALARA's 1e-15 truncation resolves "
                             "equivalent chain depth (at unit flux the products "
                             "fall below any tractable truncation tolerance)"
                             % (irdff_scale, sum(fns)),
            "order": "ascending after collapse; reversed to descending for the ALARA flux file",
            "groups": len(irdff_asc),
            "total_flux_n_cm2_s": sum(irdff_asc),
            "sha256_ascending": sha256_array(irdff_asc),
            "sha256_descending": sha256_array(list(reversed(irdff_asc))),
            "sha256_ascending_unscaled": sha256_array(irdff_asc_raw),
            "actinv_spec": {"descending": False, "total": sum(irdff_asc)},
        },
    }

    # --- shipped-artifact identity
    catalog = json.loads(DATA_CATALOG.read_text())
    shipped_entry = next(a for a in catalog["artifacts"]
                         if a["id"] == "tendl-2025-patched-neutron-709g")
    v1_entry = next(a for a in catalog["artifacts"]
                    if a["id"] == "tendl-2025-neutron-709g")

    contract = {
        "schema": "actinv-p26b-g2-contract-1",
        "recorded_at_utc": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "recorded_at_commit": git_head(),
        "protocol": {"file": str(PROTOCOL.relative_to(ROOT)),
                     "sha256": sha256_file(PROTOCOL)},
        "parents": {
            "g0_seals": {"file": "results/g0_p26b_seals.json",
                          "sha256": sha256_file(G0_SEALS)},
            "g1_record": {"file": "results/g1_p26b_conversion.json",
                          "sha256": sha256_file(G1_RECORD)},
            "p26_contract": {"file": "results/g2_p26_contract.json",
                             "sha256": sha256_file(P26_CONTRACT)},
        },
        "libraries": {
            "alara_fendl32c_709": {
                "role": "ALARA arm for both legs",
                "built_by": "P26b G1 convert_lib (FENDL-3.2c pinned files)",
                "base": str(ALARA_LIB_BASE),
                "files": {ext: sha256_file(Path(str(ALARA_LIB_BASE) + ext))
                          for ext in (".idx", ".lib", ".gam", ".gdx")},
                "parent_isotopes": len(alara_cov),
            },
            "actinv_fendl32c_709": {
                "role": "identical_data ACTINV arm",
                "built_by": "P26b G1 ACTINV artifact build",
                "npz": str(ACTINV_FENDL_NPZ),
                "npz_sha256": sha256_file(ACTINV_FENDL_NPZ),
                "index_sha256": sha256_file(
                    Path(str(ACTINV_FENDL_NPZ).replace(".npz", "_index.json"))),
                "targets": len(actinv_fendl_cov),
            },
            "actinv_shipped_tendl2025": {
                "role": "product_plus_data ACTINV arm",
                "identity": "v1.1.0 catalog default_bundle "
                            "tendl-2025-patched-neutron",
                "catalog_sha256": sha256_file(DATA_CATALOG),
                "npz": str(ACTINV_SHIPPED_NPZ),
                "npz_sha256": sha256_file(ACTINV_SHIPPED_NPZ),
                "index_sha256": sha256_file(
                    Path(str(ACTINV_SHIPPED_NPZ).replace(".npz", "_index.json"))),
                "targets": len(actinv_shipped_cov),
                "catalog_sha256_expected": shipped_entry["sha256"],
                "note": "v1.1.0 catalog also ships the unpatched v1.0.0 "
                        "artifact (id tendl-2025-neutron-709g, sha256 "
                        f"{v1_entry['sha256']}); it covers all 36 contract "
                        "isotopes and is recorded for provenance only.",
            },
            "actinv_decay": {
                "primary": {"path": str(DECAY_PRIMARY),
                            "sha256": sha256_file(DECAY_PRIMARY)},
                "fallback": {"path": str(DECAY_FALLBACK),
                             "sha256": sha256_file(DECAY_FALLBACK)},
            },
            "alara_element_lib": {"path": str(ELELIB),
                                  "sha256": sha256_file(ELELIB)},
        },
        "element_densities_g_cm3": elelib_densities(),
        "isotope_expansion": {
            "actinv_source": "crates/actinv-data/src/tables.rs embedded abundance table",
            "tables_rs_sha256": sha256_file(TABLES_RS),
            "alara_source": "ALARA element library isotope expansion",
            "alara_elelib_sha256": sha256_file(ELELIB),
            "contract_elements_actinv": contract_elements,
            "contract_elements_alara": {
                el: sorted(nuclide_name(el, a, 0) for a in elelib[el]["isotopes"])
                for el in contract_elements},
            "note": "ACTINV expands elements via the abundance table (e.g. ta -> "
                    "{Ta-180m, Ta-181}); ALARA expands via myElelib (ta -> {Ta-181} "
                    "only).  Each arm is checked against its own required set.",
        },
        "spectra": spectra,
        "cooling_times_s": COOLING_TIMES_S,
        "photon_groups": {"structure": "fispact-24",
                          "upper_bounds_eV_ascending": FISPACT24_UPPER_BOUNDS_EV},
        "responses": RESPONSES,
        "response_semantics": {
            "total_atoms_per_g": "as reported by each tool: ALARA's "
                "number-density total counts every atom including the unburned "
                "bulk; ACTINV's counts populated (non-initial) states only - "
                "NOT like-for-like",
            "product_atoms_per_g": "like-for-like populated-products "
                "inventory: ACTINV reports total_atoms_per_g directly; ALARA "
                "is computed as the sum over isotope rows whose pre-irrad "
                "column is zero (products only)",
            "decay_heat_w_per_g": "ALARA total_heat vs ACTINV "
                "heat_W_per_g.total (alpha+beta+gamma)",
            "photon_source_per_group_24": "fispact-24 group totals: ALARA "
                "photsrc.out TOTAL rows vs ACTINV photon_source.groups[].photons_s_g",
        },
        "legs": {
            "identical_data": {
                "actinv_arm": "actinv_fendl32c_709",
                "alara_arm": "alara_fendl32c_709",
                "meaning": "both tools consume the same FENDL-3.2c "
                           "evaluations; pass/fail on frozen tolerances",
                "expected": counts["identical_data"],
            },
            "product_plus_data": {
                "actinv_arm": "actinv_shipped_tendl2025",
                "alara_arm": "alara_fendl32c_709",
                "meaning": "each tool with its standard data; differences "
                           "reported, never a solver verdict",
                "expected": counts["product_plus_data"],
            },
        },
        "cases": cases,
        "counts": {"total_cases": len(cases), "legs": counts},
        "tolerances": p26["tolerances"],
        "failure_categories": p26["failure_categories"],
        "measurement_rules": p26["measurement_rules"],
        "alara_input_recipe": {
            "geometry": "rectangular + volumes 1.0 z1 (point geometry has zero volume)",
            "mixture": "element <sym> <1/rho_element> <wt_fraction> per element -> exact 1 g, natural isotopic ratios from elelib",
            "flux": "flux f1 <file> 1.0 0 default; file lists group fluxes in library (descending) order",
            "schedule": "schedule s / <irr_s> s f1 pulse_once 0 s; pulsehistory 1 0 s",
            "output": "zone; units Bq g; specific_activity number_density total_heat photon_source(24g)",
            "cooling": "absolute post-shutdown times",
            "truncation": "1e-15 for all cases (uniform solver control; resolves "
                          "daughters at the campaign flux ~1e10 n/cm2/s; the IRDFF "
                          "spectrum is normalized to the fns_709 total so this single "
                          "tolerance captures equivalent chain depth for every case)",
            "isotope_identity": "text rows are anonymous (ALARA 2.9.2 symbol() display defect); "
                                "identity is recovered from alara.dmp KZAs aligned by sorted-kza "
                                "order + t_1/2 cross-check",
        },
        "diagnostic_probes": {
            "actinv_fendl_silent_bulk": {
                "case": "fe__fns_709__pulse_5min",
                "rationale": "P26b G1 established ACTINV can run on the FENDL "
                             "artifact with Fe-57 silently missing (incomplete "
                             "parent partition -> reduced answer, not an error). "
                             "One diagnostic run documents this behavior; it is "
                             "NOT a leg measurement and lives in the diagnostic "
                             "evidence partition.",
                "partition": "diagnostic",
            },
        },
        "decision_fields": {
            "executability": "per-case per-leg status (executable | contract_gap) "
                             "with missing-isotope lists; G3 verdict reads the census",
            "product_plus_data_responses": "per-case per-time response pairs; "
                             "differences reported against frozen response list",
            "identical_data_outcome": "all-gap census is itself the measured "
                             "executability result; no numeric comparison exists",
        },
        "evidence_partitions": {
            "p26b_qualifying": {"consumers": ["P26b G3 verdict"],
                                 "consumption": "once, at G3"},
            "diagnostic": {"consumers": ["probes, iteration"],
                            "consumption": "unlimited within P26b"},
        },
        "known_precedent_defects": {
            "p26_fns_spectrum_reversed": "the frozen P26 campaign specs wrote the "
                "example's descending flux_per_group without descending:true; ACTINV "
                "treated it as ascending.  P26b applies spectra correctly: "
                "descending:true for fns_709 specs, ascending collapse for "
                "irdff_sp_mat9861_709 (no flag), descending order for ALARA flux "
                "files.  Frozen P26 artifacts are not modified; this is recorded "
                "here as the precedent caveat.",
            "alara_292_symbol_display": "isotope names print as '-A' with the element "
                "symbol blank in this build (verified on stock sample3); identity "
                "is recovered via alara.dmp KZA alignment, cross-checked by t_1/2 "
                "and lambda*N vs printed activity.",
        },
    }
    OUT.write_text(json.dumps(contract, indent=1, sort_keys=True) + "\n")
    print(f"wrote {OUT.relative_to(ROOT)}")
    print(json.dumps(counts, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
