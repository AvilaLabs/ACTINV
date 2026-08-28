#!/usr/bin/env python3
"""P10-G6: projectile identity across ordinary, Python and prepared mesh runs."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import resource
import struct
import subprocess
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"

from p9_fixtures import base_spec, make_fixture, sha256, write_json  # noqa: E402

BIN = Path(os.environ.get("ACTINV_BIN", ROOT / "target" / "release" / "actinv"))
WORK = Path(os.environ.get("ACTINV_P10_WORK", "/tmp/actinv-p10")) / "g6"
RESULT = ROOT / "results" / "g6_p10_projectile_runtime.json"
TABLES = ROOT / "results" / "tables" / "abundance_mass.json"
ADDRESS_SPACE_BYTES = 2 * 1024**3
PRE_P10_COMMIT = "e5421a0e30eb94303482bed2c4b9491b773244e6"
PRE_P10_NEUTRON_NORMALIZED_SHA256 = (
    "0ed6be999d63820556d91ad73ab73fa7980f9b37dca8fcc00dd4c351f7cd1b1c"
)
PRE_P12_TABLES_PROVENANCE = (
    "openmc.data.NATURAL_ABUNDANCE and openmc.data.atomic_mass (OpenMC 0.15.3; "
    "abundances per Meija et al., Pure Appl. Chem. 88 (2016); masses AME2020 via "
    "openmc mass data)"
)
EXPECTED_PROTOCOL_HASHES = {
    "protocol": "74273ec549d113b24367341d1f94f57d0070795d6e679b84a1921d64dbc85b27",
    "amendment_a": "e7fb61dc755f02675c92c57d2f13f6872a6087e24165b0b3fd128dc86df140fd",
    "amendment_b": "36fe887080b03af2851c00a92ebcd5fe93fa4f4bded69c37415ead2626f8cc23",
}


def limit_address_space() -> None:
    resource.setrlimit(resource.RLIMIT_AS, (ADDRESS_SPACE_BYTES, ADDRESS_SPACE_BYTES))


def command(
    arguments: list[str | Path], *, ok: bool = True, timeout: float = 120.0
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        [str(value) for value in arguments],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        preexec_fn=limit_address_space,
        check=False,
    )
    if ok and completed.returncode:
        raise RuntimeError(
            f"command failed ({completed.returncode}): {' '.join(map(str, arguments))}\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    if not ok and completed.returncode == 0:
        raise RuntimeError(f"command unexpectedly succeeded: {' '.join(map(str, arguments))}")
    return completed


def boundary_hash(boundaries: list[float]) -> str:
    digest = hashlib.sha256(b"ACTINV-GROUP-BOUNDARIES-v1\0")
    for value in boundaries:
        digest.update(struct.pack("<d", value))
    return digest.hexdigest()


def normalized(result: dict) -> dict:
    value = copy.deepcopy(result)
    value.pop("ms", None)
    value["entry_point"] = "normalized"
    value["certificate"]["entry_point"] = "normalized"
    return value


def scrub_legacy_result(value):
    if isinstance(value, dict):
        return {
            key: scrub_legacy_result(item)
            for key, item in value.items()
            if key != "ms"
        }
    if isinstance(value, list):
        return [scrub_legacy_result(item) for item in value]
    if isinstance(value, str):
        return value.replace(str(WORK), "<WORK>")
    return value


def canonical_result_hash(result: dict) -> str:
    value = copy.deepcopy(result)
    certificate = value.get("certificate", {})
    solver = certificate.get("solver")
    if not isinstance(solver, str) or not solver.startswith("actinv-core "):
        raise AssertionError(f"legacy neutron result has invalid solver identity {solver!r}")
    certificate["solver"] = "actinv-core <VERSION>"

    table_record = json.loads(TABLES.read_text())
    expected_provenance = table_record.get("source")
    provenance = certificate.get("tables_provenance")
    if not isinstance(expected_provenance, str) or not expected_provenance:
        raise AssertionError(f"{TABLES} has no nonempty source provenance")
    if provenance != expected_provenance:
        raise AssertionError(
            "legacy neutron tables provenance does not match the embedded-table "
            f"record: {provenance!r}, expected {expected_provenance!r}"
        )
    # P12-G2 replaces an indirect OpenMC attribution with the independently verified
    # Meija/AME2020 primary-source record. Assert the current record above, then map
    # only that provenance leaf to its frozen pre-P12 value for the P10 legacy hash.
    certificate["tables_provenance"] = PRE_P12_TABLES_PROVENANCE
    payload = json.dumps(
        scrub_legacy_result(value), sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def run_cli(name: str, spec: dict) -> tuple[dict, Path]:
    spec_path = WORK / f"{name}.json"
    result_path = WORK / f"{name}.result.json"
    write_json(spec_path, spec)
    command([BIN, "run", spec_path, result_path])
    return json.loads(result_path.read_text()), spec_path


def expect_failure(name: str, spec: dict, needle: str, action: str = "run") -> dict:
    path = WORK / f"reject-{name}.json"
    output = WORK / f"reject-{name}.result.json"
    if output.exists():
        output.unlink()
    write_json(path, spec)
    arguments = [BIN, action, path]
    if action == "run":
        arguments.append(output)
    completed = command(arguments, ok=False)
    message = completed.stdout + completed.stderr
    if needle not in message:
        raise AssertionError(f"{name}: expected {needle!r}, got {message!r}")
    if output.exists():
        raise AssertionError(f"{name}: failed run published {output}")
    return {
        "returncode_nonzero": True,
        "context": needle,
        "final_output_absent": True,
    }


def write_flux(path: Path, source: Path, total: float) -> None:
    records = [
        {
            "record": "header",
            "schema": "actinv-flux-1",
            "source": {
                "format": "synthetic",
                "path": str(source),
                "sha256": sha256(source),
            },
            "energy_boundaries_eV": [1.0, 3.0],
            "flux_units": "particles cm^-2 s^-1",
            "cell_count": 1,
        },
        {
            "record": "cell",
            "ordinal": 0,
            "id": "cell-0",
            "flux_per_group": [total],
            "flux_total": total,
        },
        {"record": "footer", "cell_count": 1, "flux_sum_over_cells": total},
    ]
    path.write_text("".join(json.dumps(record, sort_keys=True) + "\n" for record in records))


def charged_index(index: dict, projectile: str) -> dict:
    return {
        **index,
        "projectile": projectile,
        "temperature_K": 0.0,
        "groups": "synthetic-1",
        "group_boundary_sha256": boundary_hash([1.0, 3.0]),
    }


def checked_certificate(result: dict, projectile: str | None, label: str) -> dict:
    certificate = result["certificate"]
    expected = projectile
    for container_name, container in (
        ("result", result),
        ("ledger", result["ledger"]),
        ("certificate", certificate),
    ):
        actual = container.get("projectile")
        if actual != expected:
            raise AssertionError(
                f"{label}: {container_name} projectile {actual!r}, expected {expected!r}"
            )

    checks = {}
    inputs = certificate["inputs"]
    for key in (
        "library",
        "library_index",
        "decay_primary",
        "decay_fallback",
        "photon_response",
    ):
        item = inputs.get(key)
        if item:
            recomputed = sha256(Path(item["path"]))
            checks[key] = {
                "recorded": item["sha256"],
                "recomputed": recomputed,
                "match": item["sha256"] == recomputed,
            }
    for number, item in enumerate(inputs["fission_yields"]):
        recomputed = sha256(Path(item["path"]))
        checks[f"fission_yield_{number}"] = {
            "recorded": item["sha256"],
            "recomputed": recomputed,
            "match": item["sha256"] == recomputed,
        }
    if not checks or not all(value["match"] for value in checks.values()):
        raise AssertionError(f"{label}: a certificate input hash does not re-match")
    library_hash = checks["library"]["recomputed"]
    if certificate["library_sha256_declared"] != library_hash:
        raise AssertionError(f"{label}: declared library hash does not match")
    return checks


def checked_mesh_certificate(
    header: dict, projectile: str, canonical_flux: Path, source: Path
) -> dict:
    certificate = header["certificate"]
    if header.get("projectile") != projectile or certificate.get("projectile") != projectile:
        raise AssertionError(f"{projectile}: prepared mesh projectile is missing")
    if header["flux_units"] != "particles cm^-2 s^-1":
        raise AssertionError(f"{projectile}: prepared mesh flux units are wrong")
    flux_hash = sha256(canonical_flux)
    canonical = certificate["canonical_flux"]
    source_hash = sha256(source)
    upstream = certificate["upstream_source"]
    checks = {
        "canonical_declared": canonical["sha256_declared"] == flux_hash,
        "canonical_computed": canonical["sha256_computed"] == flux_hash,
        "upstream_source": upstream["sha256"] == source_hash,
        "upstream_path": upstream["path"] == str(source),
    }
    if not all(checks.values()):
        raise AssertionError(f"{projectile}: prepared mesh provenance mismatch {checks}")
    return {
        "canonical_flux_sha256": flux_hash,
        "upstream_source_sha256": source_hash,
        "checks": checks,
    }


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    initial_atoms = 2.5e20
    flux = 1.0e20
    duration = 2.0

    protocol_paths = {
        "protocol": ROOT / "protocols" / "ACTINV-P10_PROTOCOL.md",
        "amendment_a": ROOT / "protocols" / "ACTINV-P10_AMENDMENT_A.md",
        "amendment_b": ROOT / "protocols" / "ACTINV-P10_AMENDMENT_B.md",
    }
    protocol_hashes = {name: sha256(path) for name, path in protocol_paths.items()}
    if protocol_hashes != EXPECTED_PROTOCOL_HASHES:
        raise AssertionError(f"P10 protocol hashes changed: {protocol_hashes}")

    legacy_fixture = make_fixture(WORK)
    legacy_index = json.loads(legacy_fixture["index"].read_text())
    neutron_spec = base_spec(
        legacy_fixture,
        composition={"FE56": initial_atoms},
        basis="atoms_per_g",
        schedule=[{"dt": f"{duration} s", "flux": 1.0}],
        mode="coupled",
        total_flux=flux,
    )
    neutron_spec["fission_yields"] = {"files": [], "energy": "spectrum_average"}
    if "projectile" in neutron_spec or "projectile" in legacy_index:
        raise AssertionError("legacy neutron spec/index unexpectedly declares a projectile")
    neutron, neutron_spec_path = run_cli("legacy-neutron", neutron_spec)
    if "projectile" in neutron or "projectile" in neutron["ledger"] or "projectile" in neutron["certificate"]:
        raise AssertionError("legacy neutron result gained a serialized projectile field")
    if "fluence_n_cm2" not in neutron["steps"][0] or "fluence_particles_cm2" in neutron["steps"][0]:
        raise AssertionError("legacy neutron fluence schema changed")
    with (ROOT / "Cargo.toml").open("rb") as stream:
        workspace_version = tomllib.load(stream)["workspace"]["package"]["version"]
    expected_solver = f"actinv-core {workspace_version}"
    if neutron["certificate"].get("solver") != expected_solver:
        raise AssertionError(
            f"legacy neutron solver identity {neutron['certificate'].get('solver')!r}, expected {expected_solver!r}"
        )
    legacy_hash = canonical_result_hash(neutron)
    if legacy_hash != PRE_P10_NEUTRON_NORMALIZED_SHA256:
        raise AssertionError(
            f"legacy neutron result changed: {legacy_hash}, expected {PRE_P10_NEUTRON_NORMALIZED_SHA256}"
        )
    wrong_provenance = copy.deepcopy(neutron)
    wrong_provenance["certificate"]["tables_provenance"] += " [planted change]"
    try:
        canonical_result_hash(wrong_provenance)
    except AssertionError as error:
        if "does not match the embedded-table record" not in str(error):
            raise
    else:
        raise AssertionError("planted legacy tables-provenance change was accepted")
    certificate_hashes = {
        "legacy_neutron_cli": checked_certificate(neutron, None, "legacy-neutron-cli")
    }

    source = WORK / "synthetic-transport-source.txt"
    source.write_text("P10 deterministic charged-particle transport source\n")
    canonical_flux = WORK / "charged-flux.ndjson"
    write_flux(canonical_flux, source, flux)

    requested_extension = os.environ.get("ACTINV_PYTHON_LIBRARY")
    if requested_extension:
        module_spec = importlib.util.spec_from_file_location("actinv", requested_extension)
        if module_spec is None or module_spec.loader is None:
            raise RuntimeError(f"cannot load Python extension {requested_extension}")
        actinv = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(actinv)
    else:
        try:
            import actinv
        except ImportError as error:
            raise RuntimeError("P10-G6 requires a PyO3 module built from this checkout") from error
    extension_module = getattr(actinv, "actinv", actinv)
    extension_path = Path(extension_module.__file__)

    analytic_errors: dict[str, float] = {}
    entry_point_identity: dict[str, bool] = {}
    mesh_certificates = {}
    charged_specs = {}

    for projectile in ("proton", "deuteron", "alpha"):
        fixture = make_fixture(WORK / projectile)
        index_path = fixture["index"]
        index = charged_index(json.loads(index_path.read_text()), projectile)
        write_json(index_path, index)

        spec = base_spec(
            fixture,
            composition={"FE56": initial_atoms},
            basis="atoms_per_g",
            schedule=[{"dt": f"{duration} s", "flux": 1.0}],
            mode="coupled",
            total_flux=flux,
        )
        spec["title"] = f"P10 G6 synthetic {projectile}"
        spec["projectile"] = projectile
        spec["options"]["temperature_K"] = 0.0
        spec["fission_yields"] = {"files": [], "energy": "spectrum_average"}
        charged_specs[projectile] = spec

        cli, _ = run_cli(f"{projectile}-cli", spec)
        python_result = json.loads(actinv.run(json.dumps(spec)))

        mesh_spec = {
            "spec": "actinv-mesh-spec-1",
            "title": spec["title"],
            "projectile": projectile,
            "library": spec["library"],
            "decay": spec["decay"],
            "material": spec["material"],
            "flux": {"path": str(canonical_flux), "sha256": sha256(canonical_flux)},
            "schedule": spec["schedule"],
            "options": spec["options"],
            "fission_yields": spec["fission_yields"],
            "chunk_cells": 1,
            "threads": 1,
        }
        mesh_spec_path = WORK / f"{projectile}-mesh.json"
        mesh_result_path = WORK / f"{projectile}-mesh.ndjson"
        write_json(mesh_spec_path, mesh_spec)
        command([BIN, "mesh", mesh_spec_path, mesh_result_path])
        mesh_records = [json.loads(line) for line in mesh_result_path.read_text().splitlines()]
        mesh_header, mesh_cell = mesh_records[0], mesh_records[1]
        mesh_result = mesh_cell["result"]

        if normalized(cli) != normalized(python_result) or normalized(cli) != normalized(mesh_result):
            raise AssertionError(f"{projectile}: CLI, PyO3 and prepared mesh results differ")
        entry_point_identity[projectile] = True

        for label, result in (
            ("cli", cli),
            ("python", python_result),
            ("prepared_mesh", mesh_result),
        ):
            certificate_hashes[f"{projectile}_{label}"] = checked_certificate(
                result, projectile, f"{projectile}-{label}"
            )
        mesh_certificates[projectile] = checked_mesh_certificate(
            mesh_header, projectile, canonical_flux, source
        )

        step = cli["steps"][0]
        if "fluence_n_cm2" in step or step.get("fluence_particles_cm2") != flux * duration:
            raise AssertionError(f"{projectile}: charged fluence schema/value is wrong")
        inventory = {row["nuclide"]: row["atoms_per_g"] for row in step["inventory"]}
        rate = 4.0e-24 * flux
        decay = math.log(2.0) / 100.0
        # Synthetic Mn-56 beta decay returns to Fe-56, making a closed two-state system.
        expected_product = (
            initial_atoms
            * rate
            / (rate + decay)
            * (1.0 - math.exp(-(rate + decay) * duration))
        )
        expected_parent = initial_atoms - expected_product
        relative = max(
            abs(inventory["Fe56"] - expected_parent) / expected_parent,
            abs(inventory["Mn56"] - expected_product) / expected_product,
        )
        analytic_errors[projectile] = relative
        if relative > 2.0e-12:
            raise AssertionError(f"{projectile}: analytic inventory error {relative:.3e}")

    rejection_fixture = make_fixture(WORK / "rejections")
    rejection_index_path = rejection_fixture["index"]
    rejection_base_index = json.loads(rejection_index_path.read_text())
    charged = base_spec(
        rejection_fixture,
        composition={"FE56": initial_atoms},
        basis="atoms_per_g",
        schedule=[{"dt": f"{duration} s", "flux": 1.0}],
        mode="coupled",
        total_flux=flux,
    )
    charged["projectile"] = "proton"
    charged["options"]["temperature_K"] = 0.0
    charged["fission_yields"] = {"files": [], "energy": "spectrum_average"}
    valid_index = charged_index(rejection_base_index, "proton")
    write_json(rejection_index_path, valid_index)

    rejections = {}
    mismatch = copy.deepcopy(charged)
    mismatch["projectile"] = "deuteron"
    rejections["projectile_mismatch"] = expect_failure(
        "projectile-mismatch", mismatch, "does not match activation-library projectile"
    )
    write_json(rejection_index_path, rejection_base_index)
    rejections["missing_index_projectile"] = expect_failure(
        "missing-index-projectile",
        charged,
        "does not match activation-library projectile 'neutron'",
    )

    write_json(rejection_index_path, valid_index)
    group_mismatch = copy.deepcopy(charged)
    group_mismatch["spectrum"] = {
        "structure": "fispact-162",
        "flux_per_group": [1.0] + [0.0] * 161,
        "total": flux,
        "descending": False,
    }
    rejections["spec_index_group_mismatch"] = expect_failure(
        "spec-index-group-mismatch",
        group_mismatch,
        "does not match activation-library group structure",
    )

    bad_group_hash = {**valid_index, "group_boundary_sha256": "0" * 64}
    write_json(rejection_index_path, bad_group_hash)
    rejections["index_group_hash"] = expect_failure(
        "index-group-hash", charged, "group-boundary hash mismatch"
    )
    bad_group_name = {**valid_index, "groups": "fispact-162"}
    write_json(rejection_index_path, bad_group_name)
    rejections["index_group_boundaries"] = expect_failure(
        "index-group-boundaries",
        charged,
        "boundaries do not match declared group structure",
    )
    bad_temperature = {**valid_index, "temperature_K": 1.0}
    write_json(rejection_index_path, bad_temperature)
    rejections["index_temperature"] = expect_failure(
        "index-temperature", charged, "does not match library temperature"
    )
    bad_npz_hash = {**valid_index, "sha256_npz": "0" * 64}
    write_json(rejection_index_path, bad_npz_hash)
    rejections["index_library_hash"] = expect_failure(
        "index-library-hash", charged, "activation-library index hash mismatch"
    )

    write_json(rejection_index_path, valid_index)
    bad_declared_hash = copy.deepcopy(charged)
    bad_declared_hash["library"]["sha256"] = "0" * 64
    rejections["spec_library_hash"] = expect_failure(
        "spec-library-hash", bad_declared_hash, "SHA-256 mismatch"
    )
    hot = copy.deepcopy(charged)
    hot["options"]["temperature_K"] = 293.6
    rejections["charged_temperature"] = expect_failure(
        "charged-temperature", hot, "temperature_K: 0", "validate"
    )
    fission = copy.deepcopy(charged)
    fission["fission_yields"] = {
        "files": [{"path": "unused.endf", "sha256": "0" * 64}],
        "energy": "spectrum_average",
    }
    rejections["charged_fission"] = expect_failure(
        "charged-fission", fission, "not supported for proton", "validate"
    )
    unknown = copy.deepcopy(charged)
    unknown["projectile"] = "muon"
    rejections["unknown_projectile"] = expect_failure(
        "unknown-projectile", unknown, "unknown variant", "validate"
    )

    report = {
        "gate": "P10-G6",
        "address_space_limit_bytes": ADDRESS_SPACE_BYTES,
        "protocol_hashes": protocol_hashes,
        "binary_sha256": sha256(BIN),
        "python_extension": extension_path.name,
        "python_extension_sha256": sha256(extension_path),
        "control_sha256": sha256(Path(__file__)),
        "pre_p10_neutron": {
            "baseline_commit": PRE_P10_COMMIT,
            "normalized_result_sha256": legacy_hash,
            "expected_normalized_result_sha256": PRE_P10_NEUTRON_NORMALIZED_SHA256,
            "normalized_fields": [
                "top-level ms",
                "working path",
                "certificate.solver semantic version",
                "certificate.tables_provenance after exact embedded-table-record check",
            ],
            "current_solver": expected_solver,
            "current_tables_provenance": neutron["certificate"]["tables_provenance"],
            "wrong_tables_provenance_rejected": True,
            "spec_sha256": sha256(neutron_spec_path),
            "index_sha256": sha256(legacy_fixture["index"]),
            "library_sha256": sha256(legacy_fixture["library"]),
            "no_projectile_in_spec_index_or_result": True,
            "legacy_neutron_fluence_schema_unchanged": True,
            "pass": legacy_hash == PRE_P10_NEUTRON_NORMALIZED_SHA256,
        },
        "projectiles": list(analytic_errors),
        "max_analytic_relative_error": max(analytic_errors.values()),
        "analytic_tolerance": 2.0e-12,
        "entry_point_identity": entry_point_identity,
        "entry_points": ["cli", "python", "prepared_mesh"],
        "certificate_hashes": certificate_hashes,
        "mesh_certificates": mesh_certificates,
        "source_sha256": sha256(source),
        "canonical_flux_sha256": sha256(canonical_flux),
        "rejections": rejections,
        "pass": True,
    }
    RESULT.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
