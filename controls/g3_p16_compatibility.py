#!/usr/bin/env python3
"""P16 G4: exact v1.0.1 compatibility across features and public entry points."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from pathlib import Path
import struct
import subprocess

from p9_fixtures import base_spec, make_fixture as make_p9_fixture, sha256, write_json
from p11_fixtures import make_fixture as make_p11_fixture
from p11_fixtures import specification as p11_specification


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results/g3_p16_compatibility.json"
WORK = Path(os.environ.get("ACTINV_P16_COMPAT_WORK", ROOT / "target/p16-compatibility-work"))
PYTHON = Path(
    os.environ.get("ACTINV_P16_PYTHON", "/home/connoravila/.venvs/w003env/bin/python3.12")
)
RUNNER = ROOT / "controls/p16_python_runner.py"
TABLE = ROOT / "controls/fixtures/p12_radiological_table.json"
BINARIES = {
    "release": Path(
        os.environ.get(
            "ACTINV_P16_RELEASE_BIN", ROOT / "target/p16-opening-target/release/actinv"
        )
    ),
    "candidate": Path(os.environ.get("ACTINV_BIN", ROOT / "target/release/actinv")),
}
PREPARED = {
    "release": Path(
        os.environ.get(
            "ACTINV_P16_RELEASE_PREPARED",
            ROOT / "target/p16-opening-target/release/prepared_probe",
        )
    ),
    "candidate": Path(
        os.environ.get(
            "ACTINV_PREPARED_PROBE", ROOT / "target/release/prepared_probe"
        )
    ),
}
EXTENSIONS = {
    "release": Path(
        os.environ.get(
            "ACTINV_P16_RELEASE_PYTHON_LIBRARY",
            ROOT / "target/p16-opening-python-target/release/libactinv.so",
        )
    ),
    "candidate": Path(
        os.environ.get(
            "ACTINV_PYTHON_LIBRARY", ROOT / "python/target/release/libactinv.so"
        )
    ),
}
THREAD_VARIABLES = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "RAYON_NUM_THREADS",
)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def environment(label: str) -> dict[str, str]:
    value = os.environ.copy()
    value["ACTINV_CACHE_DIR"] = str(WORK / f"{label}-cache")
    for name in THREAD_VARIABLES:
        value[name] = "1"
    return value


def command(
    arguments: list[str | Path],
    *,
    label: str,
    ok: bool = True,
    timeout: float = 180.0,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        [str(argument) for argument in arguments],
        cwd=ROOT,
        env=environment(label),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    if ok != (completed.returncode == 0):
        raise RuntimeError(
            f"unexpected command result {completed.returncode}: {' '.join(map(str, arguments))}\n"
            f"{completed.stdout}{completed.stderr[-4000:]}"
        )
    return completed


def normalized(value: dict) -> dict:
    value = copy.deepcopy(value)
    value.pop("ms", None)
    value.pop("entry_point", None)
    value.get("certificate", {}).pop("entry_point", None)
    return value


def canonical_sha256(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def boundary_hash(boundaries: list[float]) -> str:
    digest = hashlib.sha256(b"ACTINV-GROUP-BOUNDARIES-v1\0")
    for value in boundaries:
        digest.update(struct.pack("<d", value))
    return digest.hexdigest()


def charged_spec(work: Path) -> dict:
    fixture = make_p9_fixture(work)
    index_path = fixture["index"]
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index.update(
        {
            "projectile": "proton",
            "temperature_K": 0.0,
            "groups": "synthetic-1",
            "group_boundary_sha256": boundary_hash([1.0, 3.0]),
        }
    )
    write_json(index_path, index)
    value = base_spec(
        fixture,
        composition={"Fe56": 2.5e20},
        schedule=[{"dt": "2 s", "flux": 1.0}],
        mode="coupled",
        total_flux=1.0e20,
    )
    value["title"] = "P16 charged compatibility"
    value["projectile"] = "proton"
    value["options"]["temperature_K"] = 0.0
    value["fission_yields"] = {"files": [], "energy": "spectrum_average"}
    return value


def cases() -> dict[str, dict]:
    p9 = make_p9_fixture(WORK / "p9-fixture")
    trace = base_spec(
        p9,
        composition={"Fe56": 1.0e20},
        schedule=[{"dt": "10 s", "flux": 1.0}],
        mode="trace",
        total_flux=1.0e18,
    )
    trace["title"] = "P16 trace compatibility"
    coupled = base_spec(
        p9,
        composition={"U235": 1.0e20},
        schedule=[{"dt": "300 s", "flux": 1.0}],
        mode="coupled",
        total_flux=1.0e18,
    )
    coupled["title"] = "P16 coupled compatibility"

    p11 = make_p11_fixture(WORK / "p11-fixture")
    uncertainty = p11_specification(p11, mode="trace", cram_order=48)
    uncertainty["title"] = "P16 uncertainty compatibility"
    radiological = p11_specification(
        p11, mode="trace", cram_order=48, uncertainty=False
    )
    radiological["title"] = "P16 radiological compatibility"
    radiological["options"]["outputs"].append("radiological")
    radiological["radiological"] = {
        "table": {"path": str(TABLE), "sha256": sha256(TABLE)},
        "responses": [],
        "require_complete": False,
    }
    return {
        "trace": trace,
        "coupled": coupled,
        "charged_proton": charged_spec(WORK / "charged-fixture"),
        "uncertainty": uncertainty,
        "radiological": radiological,
    }


def run_case(label: str, name: str, spec_path: Path) -> dict[str, object]:
    case_work = WORK / label / name
    case_work.mkdir(parents=True, exist_ok=True)
    outputs = {
        "cli": case_work / "cli.json",
        "prepared": case_work / "prepared.json",
        "python": case_work / "python.json",
    }
    for output in outputs.values():
        output.unlink(missing_ok=True)
    command([BINARIES[label], "run", spec_path, outputs["cli"]], label=label)
    command([PREPARED[label], spec_path, outputs["prepared"]], label=label)
    command(
        [PYTHON, RUNNER, EXTENSIONS[label], spec_path, outputs["python"]],
        label=label,
    )
    values = {
        entry_point: normalized(json.loads(path.read_text(encoding="utf-8")))
        for entry_point, path in outputs.items()
    }
    identity = {
        "cli_prepared": values["cli"] == values["prepared"],
        "cli_python": values["cli"] == values["python"],
    }
    return {
        "values": values,
        "normalized_sha256": {
            entry_point: canonical_sha256(value)
            for entry_point, value in values.items()
        },
        "entry_point_identity": identity,
        "pass": all(identity.values()),
    }


def canonical_flux(path: Path) -> None:
    descriptor = path.with_suffix(".source.json")
    write_json(descriptor, {"fixture": "P16 compatibility exact-grid mesh"})
    records = [
        {
            "record": "header",
            "schema": "actinv-flux-1",
            "source": {
                "format": "p16-compatibility",
                "path": str(descriptor),
                "sha256": sha256(descriptor),
            },
            "energy_boundaries_eV": [1.0, 3.0],
            "flux_units": "n cm^-2 s^-1",
            "cell_count": 1,
        },
        {
            "record": "cell",
            "ordinal": 0,
            "id": "p16-cell",
            "flux_per_group": [1.0e18],
            "flux_total": 1.0e18,
        },
        {"record": "footer", "cell_count": 1, "flux_sum_over_cells": 1.0e18},
    ]
    path.write_text(
        "".join(json.dumps(record, separators=(",", ":")) + "\n" for record in records),
        encoding="utf-8",
    )


def mesh_comparison(trace: dict) -> dict[str, object]:
    flux = WORK / "compatibility.flux.ndjson"
    canonical_flux(flux)
    mesh_spec = copy.deepcopy(trace)
    mesh_spec.pop("spectrum")
    mesh_spec["spec"] = "actinv-mesh-spec-1"
    mesh_spec["flux"] = {"path": str(flux), "sha256": sha256(flux)}
    mesh_spec["chunk_cells"] = 1
    mesh_spec["threads"] = 1
    path = WORK / "mesh.json"
    write_json(path, mesh_spec)
    values = {}
    for label, binary in BINARIES.items():
        output = WORK / label / "mesh.ndjson"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.unlink(missing_ok=True)
        command([binary, "mesh", path, output], label=label)
        records = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
        values[label] = normalized(records[1]["result"])
    release_candidate = values["release"] == values["candidate"]
    return {
        "release_sha256": canonical_sha256(values["release"]),
        "candidate_sha256": canonical_sha256(values["candidate"]),
        "release_candidate_exact": release_candidate,
        "pass": release_candidate,
    }


def rejected_corpus(trace: dict) -> dict[str, object]:
    mutations = {}
    for name, edit, context in (
        ("mass", lambda value: value["material"].__setitem__("mass_g", 0.0), "material.mass_g"),
        (
            "temperature",
            lambda value: value["options"].__setitem__("temperature_K", -1.0),
            "temperature_K",
        ),
        (
            "threshold",
            lambda value: value["options"].__setitem__("bmin_atoms_per_g", -1.0),
            "bmin_atoms_per_g",
        ),
        (
            "group_flux",
            lambda value: value["spectrum"]["flux_per_group"].__setitem__(0, -1.0),
            "group fluxes",
        ),
        (
            "gamma_cutoff",
            lambda value: value.setdefault("photon", {}).__setitem__(
                "gamma_constant_cutoff_eV", -1.0
            ),
            "gamma_constant_cutoff_eV",
        ),
    ):
        planted = copy.deepcopy(trace)
        edit(planted)
        mutations[name] = (planted, context)

    rows = {}
    for name, (specification, context) in mutations.items():
        path = WORK / f"rejected-{name}.json"
        write_json(path, specification)
        by_binary = {}
        for label, binary in BINARIES.items():
            output = WORK / label / f"rejected-{name}.result.json"
            output.unlink(missing_ok=True)
            completed = command([binary, "run", path, output], label=label, ok=False)
            message = (completed.stdout + completed.stderr).strip()
            by_binary[label] = {
                "returncode": completed.returncode,
                "message": message,
                "context_present": context in message,
                "no_output": not output.exists(),
            }
        parity = by_binary["release"] == by_binary["candidate"]
        rows[name] = {
            "binaries": by_binary,
            "exact_rejection_parity": parity,
            "pass": parity
            and all(
                row["returncode"] != 0
                and row["context_present"]
                and row["no_output"]
                for row in by_binary.values()
            ),
        }
    return {"cases": rows, "pass": all(row["pass"] for row in rows.values())}


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    required = {**BINARIES, **{f"prepared_{k}": v for k, v in PREPARED.items()}, **{f"python_{k}": v for k, v in EXTENSIONS.items()}}
    missing = {name: str(path) for name, path in required.items() if not path.is_file()}
    if missing:
        raise FileNotFoundError(f"P16 compatibility executable missing: {missing}")
    specifications = cases()
    spec_paths = {}
    for name, specification in specifications.items():
        path = WORK / f"{name}.json"
        write_json(path, specification)
        spec_paths[name] = path

    raw_runs = {
        label: {
            name: run_case(label, name, spec_path)
            for name, spec_path in spec_paths.items()
        }
        for label in ("release", "candidate")
    }
    case_rows = {}
    for name in specifications:
        release = raw_runs["release"][name]
        candidate = raw_runs["candidate"][name]
        release_candidate = release["values"]["cli"] == candidate["values"]["cli"]
        case_rows[name] = {
            "release_entry_points": release["entry_point_identity"],
            "candidate_entry_points": candidate["entry_point_identity"],
            "release_sha256": release["normalized_sha256"],
            "candidate_sha256": candidate["normalized_sha256"],
            "release_candidate_exact": release_candidate,
            "pass": release["pass"] and candidate["pass"] and release_candidate,
        }

    mesh = mesh_comparison(specifications["trace"])
    rejected = rejected_corpus(specifications["trace"])
    planted = copy.deepcopy(raw_runs["candidate"]["trace"]["values"]["cli"])
    planted["steps"][0]["heat_W_per_g"]["total"] += 1.0
    comparator_plant_rejected = (
        planted != raw_runs["candidate"]["trace"]["values"]["cli"]
    )
    identities = {
        name: {
            "path": str(path),
            "bytes": path.stat().st_size,
            "sha256": file_sha256(path),
        }
        for name, path in required.items()
    }
    output = {
        "schema": "actinv-p16-compatibility-1",
        "gate": "P16-G4",
        "executables": identities,
        "cases": case_rows,
        "mesh": mesh,
        "rejected_input_corpus": rejected,
        "comparator_plant_rejected": comparator_plant_rejected,
    }
    output["pass"] = bool(
        all(row["pass"] for row in case_rows.values())
        and mesh["pass"]
        and rejected["pass"]
        and comparator_plant_rejected
    )
    RESULT.parent.mkdir(exist_ok=True)
    RESULT.write_text(json.dumps(output, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(output, indent=1, sort_keys=True))
    return 0 if output["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
