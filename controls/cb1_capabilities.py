#!/usr/bin/env python3
"""CB1-G6: build the dated, source-linked competitive capability matrix."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results" / "cb1_capabilities.json"
RETRIEVED = "2026-08-28"
ALLOWED_STATUS = {"verified", "partial", "absent", "unverified", "not-applicable"}
ALLOWED_ACCESS = {
    "executed",
    "published-reference",
    "documented-only",
    "not-available",
    "not-applicable",
}


SOURCES = {
    "actinv_cargo": {
        "title": "ACTINV workspace metadata",
        "location": "Cargo.toml",
        "product_version": "ACTINV 1.0.0",
        "retrieved": RETRIEVED,
        "official": True,
    },
    "actinv_readme": {
        "title": "ACTINV README",
        "location": "README.md",
        "product_version": "ACTINV 1.0.0",
        "retrieved": RETRIEVED,
        "official": True,
    },
    "actinv_release_notes": {
        "title": "ACTINV v1.0 release notes",
        "location": "docs/RELEASE_NOTES_v1.0.md",
        "product_version": "ACTINV 1.0.0",
        "retrieved": RETRIEVED,
        "official": True,
    },
    "actinv_method": {
        "title": "ACTINV method",
        "location": "docs/METHOD.md",
        "product_version": "ACTINV 1.0.0",
        "retrieved": RETRIEVED,
        "official": True,
    },
    "actinv_first_use": {
        "title": "CB1 executed first-use control",
        "location": "results/cb1_first_use.json",
        "product_version": "ACTINV 1.0.0",
        "retrieved": RETRIEVED,
        "official": True,
    },
    "actinv_mesh": {
        "title": "CB1 executed mesh control",
        "location": "results/cb1_mesh_performance.json",
        "product_version": "ACTINV 1.0.0",
        "retrieved": RETRIEVED,
        "official": True,
    },
    "alara_repo": {
        "title": "ALARA official repository",
        "url": "https://github.com/svalinn/ALARA",
        "product_version": "ALARA 2.9.2",
        "retrieved": RETRIEVED,
        "official": True,
    },
    "alara_intro": {
        "title": "ALARA users' guide introduction",
        "url": "https://svalinn.github.io/ALARA/usersguide/introtext.html",
        "product_version": "ALARA 2.9.2 documentation",
        "retrieved": RETRIEVED,
        "official": True,
    },
    "alara_support": {
        "title": "ALARA support-file guide",
        "url": "https://svalinn.github.io/ALARA/usersguide/support.html",
        "product_version": "ALARA 2.9.2 documentation",
        "retrieved": RETRIEVED,
        "official": True,
    },
    "openmc_repo": {
        "title": "OpenMC official repository",
        "url": "https://github.com/openmc-dev/openmc",
        "product_version": "OpenMC 0.15.3",
        "retrieved": RETRIEVED,
        "official": True,
    },
    "openmc_install": {
        "title": "OpenMC installation guide",
        "url": "https://docs.openmc.org/en/stable/usersguide/install.html",
        "product_version": "OpenMC 0.15.3 stable documentation",
        "retrieved": RETRIEVED,
        "official": True,
    },
    "openmc_depletion": {
        "title": "OpenMC depletion and transmutation guide",
        "url": "https://docs.openmc.org/en/stable/usersguide/depletion.html",
        "product_version": "OpenMC 0.15.3 stable documentation",
        "retrieved": RETRIEVED,
        "official": True,
    },
    "openmc_tallies": {
        "title": "OpenMC tally guide",
        "url": "https://docs.openmc.org/en/v0.15.0/usersguide/tallies.html",
        "product_version": "OpenMC 0.15 series documentation",
        "retrieved": RETRIEVED,
        "official": True,
    },
    "fispact_main": {
        "title": "FISPACT-II official overview and release history",
        "url": "https://fispact.ukaea.uk/wiki/Main_Page",
        "product_version": "FISPACT-II 5.1",
        "retrieved": RETRIEVED,
        "official": True,
    },
    "fispact_license": {
        "title": "FISPACT-II user licences",
        "url": "https://fispact.ukaea.uk/wiki/User_licences",
        "product_version": "FISPACT-II 5.1 project documentation",
        "retrieved": RETRIEVED,
        "official": True,
    },
    "fispact_install": {
        "title": "Installing FISPACT-II",
        "url": "https://fispact.ukaea.uk/wiki/Installing_FISPACT-II",
        "product_version": "FISPACT-II release 5 documentation",
        "retrieved": RETRIEVED,
        "official": True,
    },
    "fispact_keywords": {
        "title": "FISPACT-II keywords",
        "url": "https://fispact.ukaea.uk/wiki/FISPACT-II_keywords",
        "product_version": "FISPACT-II 5.0/5.1 documentation",
        "retrieved": RETRIEVED,
        "official": True,
    },
    "fispact_data": {
        "title": "FISPACT-II nuclear-data downloads",
        "url": "https://fispact.ukaea.uk/wiki/Nuclear_data_downloads",
        "product_version": "FISPACT-II release 5 data documentation",
        "retrieved": RETRIEVED,
        "official": True,
    },
    "fispact_shielding": {
        "title": "FISPACT-II probability-table self-shielding",
        "url": "https://fispact.ukaea.uk/wiki/Probability_table_self-shielding",
        "product_version": "FISPACT-II 5.1 project documentation",
        "retrieved": RETRIEVED,
        "official": True,
    },
    "origen_overview": {
        "title": "ORIGEN overview",
        "url": "https://scale-manual.ornl.gov/6.3.2/origen/index.html",
        "product_version": "ORIGEN in SCALE 6.3.2 manual",
        "retrieved": RETRIEVED,
        "official": True,
    },
    "origen_module": {
        "title": "ORIGEN module reference",
        "url": "https://scale-manual.ornl.gov/6.3.2/origen/origen-module.html",
        "product_version": "ORIGEN in SCALE 6.3.2 manual",
        "retrieved": RETRIEVED,
        "official": True,
    },
    "origen_theory": {
        "title": "ORIGEN theory",
        "url": "https://scale-manual.ornl.gov/6.3.2/origen/origen-theory.html",
        "product_version": "ORIGEN in SCALE 6.3.2 manual",
        "retrieved": RETRIEVED,
        "official": True,
    },
    "scale_distribution": {
        "title": "RSICC SCALE 6.3.3 package record",
        "url": "https://rsicc.ornl.gov/codes/ccc/ccc8/ccc-860.html",
        "product_version": "SCALE 6.3.3 (includes ORIGEN)",
        "retrieved": RETRIEVED,
        "official": True,
    },
}


def cell(status: str, access: str, summary: str, *sources: str) -> dict[str, object]:
    return {
        "status": status,
        "evidence_access": access,
        "summary": summary,
        "sources": list(sources),
    }


PRODUCTS = [
    "ACTINV 1.0.0",
    "ALARA 2.9.2",
    "OpenMC 0.15.3",
    "FISPACT-II 5.1",
    "SCALE/ORIGEN 6.3.3",
]


AXES = [
    {
        "axis": "licence_and_access",
        "question": "Can a user lawfully obtain, inspect, and redistribute the software?",
        "cells": {
            PRODUCTS[0]: cell("verified", "executed", "Public source and binaries; MIT OR Apache-2.0 at the user's choice.", "actinv_cargo", "actinv_first_use"),
            PRODUCTS[1]: cell("verified", "executed", "Public source under the BSD 3-Clause licence.", "alara_repo"),
            PRODUCTS[2]: cell("verified", "executed", "Public source under the MIT/X licence.", "openmc_repo"),
            PRODUCTS[3]: cell("verified", "documented-only", "Controlled research/commercial licensing; no CB1 executable.", "fispact_license"),
            PRODUCTS[4]: cell("verified", "documented-only", "RSICC distribution restrictions apply; no CB1 executable.", "scale_distribution"),
        },
    },
    {
        "axis": "install_path",
        "question": "Is there a documented route from a clean machine to a first calculation?",
        "cells": {
            PRODUCTS[0]: cell("verified", "executed", "PyPI wheel plus one verified data-fetch command; standalone release binaries also exist.", "actinv_first_use", "actinv_readme"),
            PRODUCTS[1]: cell("verified", "executed", "Pinned public source configured, built, and installed with CMake in CB1.", "actinv_first_use", "alara_repo"),
            PRODUCTS[2]: cell("verified", "documented-only", "Conda, Docker, and source-build routes are documented; the Python API can be installed with pip from source.", "openmc_install"),
            PRODUCTS[3]: cell("verified", "documented-only", "Precompiled and source-build routes are documented after licensed distribution is obtained.", "fispact_install"),
            PRODUCTS[4]: cell("verified", "documented-only", "Distributed installers include executables and data; the complete installation is about 110 GB.", "scale_distribution"),
        },
    },
    {
        "axis": "projectiles_and_energy_domain",
        "question": "Which incident-particle activation domains are directly supported?",
        "cells": {
            PRODUCTS[0]: cell("partial", "executed", "Neutron, proton, deuteron, and alpha group activation are supported; gamma, triton, and helion are not in v1.0. Energy bounds follow the supplied evaluated library.", "actinv_release_notes"),
            PRODUCTS[1]: cell("partial", "documented-only", "The documented primary purpose is neutron-induced activation; the usable energy domain follows the converted group library.", "alara_intro"),
            PRODUCTS[2]: cell("partial", "documented-only", "Depletion is driven by neutron reaction rates and decay; OpenMC's broader transport particle support is not charged-particle activation support.", "openmc_depletion"),
            PRODUCTS[3]: cell("verified", "documented-only", "Group libraries are documented for neutron, proton, deuteron, gamma, alpha, triton, and helion projectiles, with library-dependent group ranges.", "fispact_data", "fispact_keywords"),
            PRODUCTS[4]: cell("partial", "documented-only", "ORIGEN directly documents neutron activation, fission, transmutation, and decay; user-supplied coefficients can extend a matrix but are not a verified charged-particle data pipeline.", "origen_overview", "origen_theory"),
        },
    },
    {
        "axis": "finite_dilution_self_shielding",
        "question": "Can composition-dependent resonance self-shielding be applied to activation rates?",
        "cells": {
            PRODUCTS[0]: cell("partial", "executed", "Resolved resonances and infinite-dilution unresolved averages are processed; finite-dilution probability-table/Bondarenko self-shielding is absent.", "actinv_method", "actinv_release_notes"),
            PRODUCTS[1]: cell("unverified", "documented-only", "No current official source located by CB1 establishes an in-code finite-dilution treatment.", "alara_intro"),
            PRODUCTS[2]: cell("verified", "documented-only", "Continuous-energy transport can apply unresolved-resonance probability tables before depletion reaction rates are formed.", "openmc_depletion"),
            PRODUCTS[3]: cell("verified", "documented-only", "Energy and spatial self-shielding corrections, including probability tables, are documented.", "fispact_shielding", "fispact_keywords"),
            PRODUCTS[4]: cell("verified", "documented-only", "Self-shielded cross sections can be supplied through COUPLE/xs_update before ORIGEN solves the inventory.", "origen_overview", "origen_module"),
        },
    },
    {
        "axis": "irradiation_schedules",
        "question": "Can nontrivial irradiation, decay, and pulse histories be represented?",
        "cells": {
            PRODUCTS[0]: cell("verified", "executed", "Arbitrary piecewise flux and cooling segments, including pulses, use the same solver path.", "actinv_release_notes"),
            PRODUCTS[1]: cell("verified", "executed", "Hierarchical arbitrary pulse schedules are documented and a ten-pulse case was executed.", "alara_intro", "actinv_first_use"),
            PRODUCTS[2]: cell("verified", "documented-only", "Time steps accept changing source rates and zero-source decay steps through multiple integrators.", "openmc_depletion"),
            PRODUCTS[3]: cell("verified", "documented-only", "TIME, FLUX, and PULSE controls cover changing irradiation and repeated pulses.", "fispact_keywords"),
            PRODUCTS[4]: cell("verified", "documented-only", "Time-dependent irradiation/decay cases and transition-matrix evolution are documented.", "origen_module", "origen_theory"),
        },
    },
    {
        "axis": "fission_yields",
        "question": "Are fission-product yields represented in inventory evolution?",
        "cells": {
            PRODUCTS[0]: cell("verified", "executed", "Hash-pinned independent yields feed the matrix with explicit mapped and leakage balances.", "actinv_release_notes", "actinv_method"),
            PRODUCTS[1]: cell("unverified", "documented-only", "CB1 did not locate a sufficiently explicit current official yield statement for the executed 2.9.2 interface.", "alara_repo"),
            PRODUCTS[2]: cell("verified", "documented-only", "Depletion chains include fission yields and configurable yield interpolation.", "openmc_depletion"),
            PRODUCTS[3]: cell("verified", "documented-only", "Neutron, spontaneous, and non-neutron fission-yield libraries plus USEFISSION are documented.", "fispact_data", "fispact_keywords"),
            PRODUCTS[4]: cell("verified", "documented-only", "Energy-dependent fission-yield resources are folded into the transition matrix.", "origen_overview", "origen_theory"),
        },
    },
    {
        "axis": "covariance_uncertainty",
        "question": "Can nuclear-data uncertainty be propagated to activation responses?",
        "cells": {
            PRODUCTS[0]: cell("partial", "executed", "MF=33 covariance is collapsed and propagated with local heat/activity sensitivities; full multi-source Monte Carlo covariance propagation is not implemented.", "actinv_release_notes", "actinv_method"),
            PRODUCTS[1]: cell("unverified", "documented-only", "No current official source located by CB1 establishes nuclear-data covariance propagation.", "alara_intro"),
            PRODUCTS[2]: cell("partial", "documented-only", "Transport tallies carry sampling uncertainty, but CB1 found no documented full nuclear-data covariance propagation through depletion.", "openmc_depletion"),
            PRODUCTS[3]: cell("verified", "documented-only", "Full-covariance sensitivity and uncertainty propagation, including Monte Carlo methods, is documented.", "fispact_main", "fispact_keywords"),
            PRODUCTS[4]: cell("partial", "documented-only", "Direct/adjoint ORIGEN sensitivities are documented; CB1 did not verify full covariance propagation for standalone ORIGEN.", "origen_module"),
        },
    },
    {
        "axis": "activation_responses",
        "question": "Which user-facing inventory, source, and radiological responses are available?",
        "cells": {
            PRODUCTS[0]: cell("verified", "executed", "Inventory, activity, alpha/beta/gamma heat, photon sources, pathways, dose proxies, clearance/waste, and ingestion/inhalation responses are available.", "actinv_readme", "actinv_release_notes"),
            PRODUCTS[1]: cell("verified", "documented-only", "Inventory/activity, decay heat, photon sources, activation trees, waste ratings, and clearance indices are documented.", "alara_intro", "alara_support"),
            PRODUCTS[2]: cell("partial", "documented-only", "Nuclide densities and reaction rates are native; broader transport tallies exist, but a dedicated activation radiological report is not documented.", "openmc_depletion", "openmc_tallies"),
            PRODUCTS[3]: cell("verified", "documented-only", "Inventories, heat, spectra, dose, clearance, pathways, gases, and material-damage observables are documented.", "fispact_main", "fispact_keywords"),
            PRODUCTS[4]: cell("verified", "documented-only", "Concentrations, activities, decay heat, and alpha/beta/neutron/gamma source spectra are documented.", "origen_overview"),
        },
    },
    {
        "axis": "transport_coupling",
        "question": "Can reaction rates be coupled to particle transport rather than supplied only as a fixed spectrum?",
        "cells": {
            PRODUCTS[0]: cell("partial", "executed", "Strict OpenMC/MCNP/FISPACT flux import and OpenMC/MCNP photon export support one-way R2S; ACTINV has no transport solver or feedback loop.", "actinv_readme", "actinv_release_notes"),
            PRODUCTS[1]: cell("partial", "documented-only", "Multi-point activation accepts externally generated fluxes; ALARA is not itself a particle-transport solver.", "alara_intro"),
            PRODUCTS[2]: cell("verified", "documented-only", "CoupledOperator updates depletion reaction rates with OpenMC transport, while IndependentOperator is also available.", "openmc_depletion"),
            PRODUCTS[3]: cell("partial", "documented-only", "External projectile spectra drive inventory calculations; no transport solver is established by the cited product documentation.", "fispact_main", "fispact_keywords"),
            PRODUCTS[4]: cell("verified", "documented-only", "ORIGEN is coupled inside SCALE TRITON and Polaris sequences and can also run standalone.", "origen_overview"),
        },
    },
    {
        "axis": "cli_and_programmatic_api",
        "question": "Are stable command-line and programmatic interfaces available?",
        "cells": {
            PRODUCTS[0]: cell("verified", "executed", "Standalone CLI, Python API, and Rust crates share the released core.", "actinv_readme", "actinv_cargo"),
            PRODUCTS[1]: cell("partial", "executed", "A command-line executable was run; CB1 found no documented stable library API.", "alara_repo", "actinv_first_use"),
            PRODUCTS[2]: cell("verified", "executed", "A Python API and executable transport interface are documented; CRAM was exercised through Python in CB1.", "openmc_repo", "openmc_depletion"),
            PRODUCTS[3]: cell("verified", "documented-only", "The traditional keyword interface and a beta C/C++/Fortran/Python API are documented.", "fispact_main", "fispact_keywords"),
            PRODUCTS[4]: cell("partial", "documented-only", "Structured SCALE module input is documented; CB1 did not verify a public stable language API for ORIGEN.", "origen_module"),
        },
    },
    {
        "axis": "determinism_and_provenance",
        "question": "Does a result carry machine-checkable identities for all material inputs?",
        "cells": {
            PRODUCTS[0]: cell("verified", "executed", "Every input is SHA-256 identified in a result certificate; ledgered omissions and deterministic interface identity are release gates.", "actinv_readme", "actinv_release_notes"),
            PRODUCTS[1]: cell("unverified", "documented-only", "CB1 found no official claim of per-result input hashing or a re-derivable provenance certificate.", "alara_repo"),
            PRODUCTS[2]: cell("unverified", "documented-only", "CB1 found no official claim of a per-result certificate hashing every depletion input.", "openmc_depletion"),
            PRODUCTS[3]: cell("unverified", "documented-only", "JSON output is documented, but CB1 found no official claim of per-input cryptographic provenance certificates.", "fispact_main"),
            PRODUCTS[4]: cell("unverified", "documented-only", "CB1 found no official claim of a per-result certificate hashing every ORIGEN input and library.", "origen_module"),
        },
    },
    {
        "axis": "spatial_or_mesh_operation",
        "question": "Can many spatial regions be calculated as a first-class workflow?",
        "cells": {
            PRODUCTS[0]: cell("verified", "executed", "Independent cells stream through a deterministic parallel mesh path; CB1 measured through 256 cells and labels the million-cell value as an extrapolation.", "actinv_mesh", "actinv_readme"),
            PRODUCTS[1]: cell("verified", "documented-only", "Multi-point 3-D solutions and several geometry forms are documented.", "alara_intro"),
            PRODUCTS[2]: cell("verified", "documented-only", "Geometry/material depletion and structured or unstructured mesh tallies are first-class transport workflows.", "openmc_depletion", "openmc_tallies"),
            PRODUCTS[3]: cell("unverified", "documented-only", "Parallel inventory partitioning is documented, but CB1 did not verify a first-class spatial mesh activation workflow.", "fispact_keywords"),
            PRODUCTS[4]: cell("partial", "documented-only", "Standalone ORIGEN has no spatial dependence; SCALE transport/depletion sequences supply spatially resolved coupling around it.", "origen_theory", "origen_overview"),
        },
    },
    {
        "axis": "continuous_feed_or_removal",
        "question": "Can material feeds, removals, or transfers alter the inventory during a run?",
        "cells": {
            PRODUCTS[0]: cell("absent", "executed", "The v1.0 public specification has no continuous feed, removal, or inter-material transfer model.", "actinv_readme", "actinv_release_notes"),
            PRODUCTS[1]: cell("unverified", "documented-only", "No current official source located by CB1 establishes continuous feed/removal.", "alara_intro"),
            PRODUCTS[2]: cell("verified", "documented-only", "Integrator transfer rates implement removal, feed, and destination-material transfer.", "openmc_depletion"),
            PRODUCTS[3]: cell("unverified", "documented-only", "No such keyword was established by the current official keyword inventory inspected by CB1.", "fispact_keywords"),
            PRODUCTS[4]: cell("verified", "documented-only", "Continuous nuclide feed and chemical removal are explicit standalone ORIGEN features.", "origen_overview", "origen_theory"),
        },
    },
    {
        "axis": "reverse_calculation",
        "question": "Can an inverse/reverse activation calculation be requested directly?",
        "cells": {
            PRODUCTS[0]: cell("absent", "executed", "No inverse or reverse activation solver is exposed in v1.0.", "actinv_readme", "actinv_release_notes"),
            PRODUCTS[1]: cell("verified", "documented-only", "Reverse calculation mode is an explicitly documented feature.", "alara_intro"),
            PRODUCTS[2]: cell("unverified", "documented-only", "No inverse activation workflow was established by the cited depletion guide.", "openmc_depletion"),
            PRODUCTS[3]: cell("unverified", "documented-only", "No reverse-calculation interface was established by the current official keyword inventory inspected by CB1.", "fispact_keywords"),
            PRODUCTS[4]: cell("unverified", "documented-only", "Adjoint sensitivity is not treated here as an inverse inventory workflow; no such workflow was established.", "origen_module"),
        },
    },
    {
        "axis": "damage_observables",
        "question": "Are displacement/damage observables available, beyond activation inventory?",
        "cells": {
            PRODUCTS[0]: cell("absent", "executed", "v1.0 does not calculate DPA, kerma, or PKA spectra.", "actinv_readme", "actinv_release_notes"),
            PRODUCTS[1]: cell("unverified", "documented-only", "No current official source located by CB1 establishes DPA, kerma, or PKA output.", "alara_intro"),
            PRODUCTS[2]: cell("verified", "documented-only", "A damage-energy tally (MT=444) is documented, although it is a transport rather than inventory observable.", "openmc_tallies"),
            PRODUCTS[3]: cell("verified", "documented-only", "DPA, kerma, displacement energies, and PKA spectra are documented.", "fispact_main", "fispact_keywords", "fispact_data"),
            PRODUCTS[4]: cell("unverified", "documented-only", "SCALE is broader than ORIGEN, but no standalone ORIGEN damage observable was established by the cited module documentation.", "origen_overview"),
        },
    },
    {
        "axis": "supported_operating_systems",
        "question": "Are supported installation routes documented across common desktop/server systems?",
        "cells": {
            PRODUCTS[0]: cell("verified", "executed", "Release wheels or binaries cover Linux, macOS, and Windows; Linux was executed in CB1.", "actinv_readme", "actinv_first_use"),
            PRODUCTS[1]: cell("partial", "executed", "Linux was built and executed; public source is portable C++, but CB1 did not verify current packaged Windows/macOS support.", "alara_repo", "actinv_first_use"),
            PRODUCTS[2]: cell("verified", "documented-only", "Conda covers Linux/macOS and Docker covers Linux/macOS/Windows; source/WSL routes are documented.", "openmc_install"),
            PRODUCTS[3]: cell("verified", "documented-only", "Linux/Unix/macOS and Windows installation routes and prebuilt executables are documented.", "fispact_install"),
            PRODUCTS[4]: cell("verified", "documented-only", "The current RSICC package lists 64-bit Linux, macOS, and Windows executables.", "scale_distribution"),
        },
    },
    {
        "axis": "compile_time_physical_units",
        "question": "Are incompatible physical quantities distinct types at the public implementation boundary?",
        "cells": {
            PRODUCTS[0]: cell("absent", "executed", "Physical units are encoded in field/type names and validation rules around f64 values, not zero-cost Rust unit newtypes.", "actinv_cargo", "actinv_release_notes"),
            PRODUCTS[1]: cell("unverified", "documented-only", "The guide places some unit consistency responsibility on the user; CB1 did not perform a full source-level type audit.", "alara_support"),
            PRODUCTS[2]: cell("unverified", "documented-only", "Not established by the official sources inspected for this product-level comparison.", "openmc_depletion"),
            PRODUCTS[3]: cell("unverified", "documented-only", "Not established by the official sources inspected for this product-level comparison.", "fispact_keywords"),
            PRODUCTS[4]: cell("unverified", "documented-only", "Not established by the official sources inspected for this product-level comparison.", "origen_module"),
        },
    },
]


def local_checks() -> dict[str, bool]:
    cargo = (ROOT / "Cargo.toml").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    release = (ROOT / "docs" / "RELEASE_NOTES_v1.0.md").read_text(encoding="utf-8")
    release_lower = release.lower()
    rust_sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "crates").glob("*/src/**/*.rs"))
    )
    alara = Path.home() / "nuclear-data" / "alara-2.9.2"
    alara_head = subprocess.run(
        ["git", "-C", str(alara), "rev-parse", "HEAD"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    alara_license = (alara / "LICENSE").read_text(encoding="utf-8") if (alara / "LICENSE").is_file() else ""
    return {
        "actinv_license": 'license = "MIT OR Apache-2.0"' in cargo,
        "actinv_install_commands": all(term in readme for term in ("pip install actinv", "actinv data fetch")),
        "actinv_limitations_named": all(
            term in release_lower
            for term in (
                "finite-dilution self-shielding",
                "triton, helion, and gamma activation",
                "not neutron/photon transport",
            )
        ),
        "actinv_public_spec_has_no_feed_or_removal": re.search(
            r"pub\s+(?:struct|enum)\s+(?:Feed|Removal|Transfer)\b", rust_sources
        )
        is None,
        "actinv_has_no_physical_quantity_newtypes": re.search(
            r"(?:pub\s+)?struct\s+(?:Barn|Energy|Temperature|Flux|Time|Mass|Activity|Dose|Power)\s*\(\s*(?:pub\s+)?f64\s*\)",
            rust_sources,
        )
        is None,
        "alara_source_pin": alara_head.returncode == 0
        and alara_head.stdout.strip() == "faa5b330460fe865e38fc788f1b792ea33d13d1b",
        "alara_bsd_3_clause_text": all(
            phrase in alara_license
            for phrase in (
                "Redistribution and use in source and binary forms",
                "Neither the name of the University of Wisconsin",
                'THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"',
            )
        ),
    }


def main() -> None:
    checks = local_checks()
    cell_count = 0
    matrix_valid = True
    for axis in AXES:
        matrix_valid &= set(axis["cells"]) == set(PRODUCTS)
        for row in axis["cells"].values():
            cell_count += 1
            matrix_valid &= row["status"] in ALLOWED_STATUS
            matrix_valid &= row["evidence_access"] in ALLOWED_ACCESS
            matrix_valid &= bool(row["sources"])
            matrix_valid &= all(source in SOURCES for source in row["sources"])
    sources_valid = all(
        source.get("official") is True
        and source.get("retrieved") == RETRIEVED
        and bool(source.get("product_version"))
        and bool(source.get("url") or source.get("location"))
        for source in SOURCES.values()
    )
    checks.update(
        {
            "all_axes_have_all_products": matrix_valid,
            "all_sources_are_versioned_dated_and_official": sources_valid,
            "matrix_has_expected_cell_count": cell_count == len(AXES) * len(PRODUCTS),
            "unknown_is_not_absent_for_competitors": all(
                row["status"] != "absent"
                for axis in AXES
                for product, row in axis["cells"].items()
                if product != PRODUCTS[0]
            ),
        }
    )
    output = {
        "schema": "actinv-cb1-capabilities-1",
        "retrieved": RETRIEVED,
        "products": PRODUCTS,
        "status_definitions": {
            "verified": "the complete named capability or condition is established by the cited evidence",
            "partial": "a meaningful subset is established, with the missing portion named",
            "absent": "source/executable inspection establishes that the capability is not exposed",
            "unverified": "CB1 found no sufficient official evidence; this is not evidence of absence",
            "not-applicable": "the axis does not apply to the product's role in CB1",
        },
        "sources": SOURCES,
        "axes": AXES,
        "checks": checks,
        "pass": all(checks.values()),
    }
    RESULT.write_text(json.dumps(output, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(output, indent=1, sort_keys=True))
    raise SystemExit(0 if output["pass"] else 1)


if __name__ == "__main__":
    main()
