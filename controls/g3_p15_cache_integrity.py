#!/usr/bin/env python3
"""P15 G3: fail-closed cache mutation, deletion, partial-publication and concurrency controls."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
from collections.abc import Callable

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from p11_fixtures import group_hash, make_fixture, sha256, write_json  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results/p15_cache_integrity.json"
ACTINV = Path(os.environ.get("ACTINV_BIN", ROOT / "target/release/actinv"))
THREADS = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "RAYON_NUM_THREADS",
)
FIXTURE_SIGMA = np.asarray(
    [
        [0.0, 0.2, 0.0, 0.4, 0.0],
        [0.0, 0.2, 0.0, 0.4, 0.0],
        [0.1, 0.0, 0.3, 0.0, 0.5],
        [0.1, 0.0, 0.3, 0.0, 0.5],
        [0.0, 0.0, 0.0, 0.0, 0.0],
    ],
    dtype="<f8",
)


def normalized(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    value.pop("ms", None)
    return value


def canonical_sha256(value: object, ephemeral_root: Path) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    encoded = encoded.replace(str(ephemeral_root).encode(), b"<WORK>")
    return hashlib.sha256(encoded).hexdigest()


def environment(cache: Path) -> dict[str, str]:
    value = os.environ.copy()
    value["ACTINV_CACHE_DIR"] = str(cache)
    for name in THREADS:
        value[name] = "1"
    return value


def run(spec: Path, cache: Path, output: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(ACTINV), "run", str(spec), str(output)],
        cwd=ROOT,
        env=environment(cache),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
        check=False,
    )


def cache_files(cache: Path) -> tuple[Path, Path]:
    prepared = list(cache.glob("prepared-v1/*/library.actp"))
    collapsed = list(cache.glob("prepared-v1/*/spectrum-*.actc"))
    if len(prepared) != 1 or len(collapsed) != 1:
        raise AssertionError(f"cache does not contain exactly one artifact pair: {prepared}, {collapsed}")
    return prepared[0], collapsed[0]


def fixture(work: Path) -> Path:
    files = make_fixture(work / "fixture")
    library = files["library"]
    rows = np.asarray(
        [
            [0, 102, -1, -1, 0],
            [0, 102, 25056, 0, 3],
            [0, 103, -1, -1, 0],
            [0, 103, 25057, 0, 3],
            [0, 104, 0, 0, 3],
        ],
        dtype="<i8",
    )
    sigma = FIXTURE_SIGMA
    bounds = [1.0, 2.0, 4.0, 8.0, 16.0, 32.0]
    np.savez(library, rows=rows, sig=sigma, bounds=np.asarray(bounds, dtype="<f8"))
    index = library.with_name(library.stem + "_index.json")
    write_json(
        index,
        {
            "schema": "actinv-library-index-1",
            "projectile": "neutron",
            "groups": "custom",
            "group_boundary_sha256": group_hash(bounds),
            "temperature_K": 293.6,
            "sha256_npz": sha256(library),
            "targets": [
                {
                    "file": "p15-synthetic.endf",
                    "source_sha256": "1" * 64,
                    "mat": 2631,
                    "za": 26056,
                    "liso": 0,
                    "awr": 55.454,
                    "ledger": [],
                }
            ],
        },
    )
    specification = {
        "spec": "actinv-spec-1",
        "title": "P15 cache integrity fixture",
        "library": {"path": str(library), "sha256": sha256(library)},
        "decay": {"primary": str(files["decay"])},
        "material": {
            "mass_g": 1.0,
            "basis": "atoms_per_g",
            "composition": {"Fe56": 1.0},
        },
        "spectrum": {
            "structure": "custom",
            "boundaries_eV": bounds,
            "flux_per_group": [0.0, 2.0, 3.0, 0.0, 5.0],
            "descending": False,
        },
        "schedule": [{"dt": "1 s", "flux": 1.0}],
        "options": {
            "mode": "trace",
            "prune": "none",
            "bmin_atoms_per_g": 0.0,
            "temperature_K": 293.6,
            "cram_order": 48,
            "outputs": ["inventory", "activity", "heat", "ledger", "certificate"],
        },
    }
    path = work / "spec.json"
    write_json(path, specification)
    return path


Mutation = Callable[[bytearray], bytearray]


def mutate_byte(offset: int) -> Mutation:
    def mutation(value: bytearray) -> bytearray:
        value[offset] ^= 1
        return value

    return mutation


def replace_u32(offset: int, value: int) -> Mutation:
    def mutation(data: bytearray) -> bytearray:
        struct.pack_into("<I", data, offset, value)
        return data

    return mutation


def replace_u64(offset: int, value: int) -> Mutation:
    def mutation(data: bytearray) -> bytearray:
        struct.pack_into("<Q", data, offset, value)
        return data

    return mutation


def truncate(data: bytearray) -> bytearray:
    return data[:-1]


def append_trailing(data: bytearray) -> bytearray:
    data.extend(b"x")
    return data


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="actinv-p15-g3-") as directory:
        work = Path(directory)
        spec = fixture(work)
        pristine_cache = work / "pristine-cache"
        reference_output = work / "reference.json"
        completed = run(spec, pristine_cache, reference_output)
        if completed.returncode:
            raise RuntimeError(completed.stderr)
        prepared, collapsed = cache_files(pristine_cache)
        prepared_relative = prepared.relative_to(pristine_cache)
        collapsed_relative = collapsed.relative_to(pristine_cache)
        pristine_prepared = prepared.read_bytes()
        pristine_collapsed = collapsed.read_bytes()
        reference_result = normalized(reference_output)

        warm_output = work / "warm.json"
        warm_run = run(spec, pristine_cache, warm_output)
        warm_reuse = {
            "returncode": warm_run.returncode,
            "result_identical": normalized(warm_output) == reference_result,
            "normalized_result_sha256": canonical_sha256(normalized(warm_output), work),
            "prepared_unchanged": prepared.read_bytes() == pristine_prepared,
            "collapsed_unchanged": collapsed.read_bytes() == pristine_collapsed,
        }

        collapsed_rows_offset = struct.unpack_from("<Q", pristine_collapsed, 64)[0]
        collapsed_values_offset = struct.unpack_from("<Q", pristine_collapsed, 72)[0]
        prepared_rows_offset = struct.unpack_from("<Q", pristine_prepared, 48)[0]
        prepared_values_offset = struct.unpack_from("<Q", pristine_prepared, 56)[0]
        plants: list[tuple[str, str, Mutation, tuple[str, ...]]] = [
            ("collapsed_magic", "collapsed", mutate_byte(0), ("magic",)),
            ("collapsed_schema_version", "collapsed", replace_u32(8, 2), ("schema version",)),
            ("collapsed_source_library", "collapsed", mutate_byte(112), ("source library",)),
            ("collapsed_source_index", "collapsed", mutate_byte(144), ("source index",)),
            ("collapsed_flux_hash", "collapsed", mutate_byte(176), ("flux spectrum",)),
            (
                "collapsed_row_descriptor",
                "collapsed",
                mutate_byte(collapsed_rows_offset),
                ("integrity trailer",),
            ),
            (
                "collapsed_selected_value",
                "collapsed",
                mutate_byte(collapsed_values_offset),
                ("integrity trailer",),
            ),
            ("collapsed_offset", "collapsed", replace_u64(72, 1), ("offsets or lengths",)),
            ("collapsed_declared_count", "collapsed", replace_u64(16, 6), ("offsets or lengths",)),
            ("collapsed_integrity_trailer", "collapsed", mutate_byte(-1), ("integrity trailer",)),
            ("collapsed_truncation", "collapsed", truncate, ("length",)),
            ("collapsed_trailing", "collapsed", append_trailing, ("length",)),
            ("prepared_magic", "prepared", mutate_byte(0), ("magic",)),
            ("prepared_schema_version", "prepared", replace_u32(8, 2), ("schema version",)),
            ("prepared_source_library", "prepared", mutate_byte(104), ("source library",)),
            ("prepared_source_index", "prepared", mutate_byte(136), ("source index",)),
            (
                "prepared_row_descriptor",
                "prepared",
                mutate_byte(prepared_rows_offset),
                ("integrity trailer",),
            ),
            (
                "prepared_selected_value",
                "prepared",
                mutate_byte(prepared_values_offset),
                ("integrity trailer",),
            ),
            ("prepared_offset", "prepared", replace_u64(56, 1), ("offsets or lengths",)),
            ("prepared_declared_count", "prepared", replace_u64(16, 6), ("dense value count",)),
            ("prepared_integrity_trailer", "prepared", mutate_byte(-1), ("integrity trailer",)),
            ("prepared_truncation", "prepared", truncate, ("length",)),
            ("prepared_trailing", "prepared", append_trailing, ("length",)),
        ]
        mutation_results: dict[str, object] = {}
        for ordinal, (name, target, mutation, expected_messages) in enumerate(plants):
            cache = work / f"plant-{ordinal}"
            shutil.copytree(pristine_cache, cache)
            if target == "prepared":
                (cache / collapsed_relative).unlink()
                artifact = cache / prepared_relative
                source = pristine_prepared
            else:
                artifact = cache / collapsed_relative
                source = pristine_collapsed
            planted = mutation(bytearray(source))
            artifact.write_bytes(planted)
            output = work / f"plant-{ordinal}.json"
            attempt = run(spec, cache, output)
            diagnostic = attempt.stderr + attempt.stdout
            mutation_results[name] = {
                "returncode": attempt.returncode,
                "diagnostic_class": any(message in diagnostic for message in expected_messages),
                "result_not_published": not output.exists(),
                "artifact_not_overwritten": artifact.read_bytes() == bytes(planted),
            }

        deleted_cache = work / "deleted-cache"
        shutil.copytree(pristine_cache, deleted_cache)
        shutil.rmtree(deleted_cache)
        deleted_output = work / "deleted.json"
        deleted_run = run(spec, deleted_cache, deleted_output)
        deleted_prepared, deleted_collapsed = cache_files(deleted_cache)
        deletion = {
            "returncode": deleted_run.returncode,
            "result_identical": normalized(deleted_output) == reference_result,
            "normalized_result_sha256": canonical_sha256(normalized(deleted_output), work),
            "prepared_identical": deleted_prepared.read_bytes() == pristine_prepared,
            "collapsed_identical": deleted_collapsed.read_bytes() == pristine_collapsed,
        }

        partial_cache = work / "partial-cache"
        partial_parent = partial_cache / prepared_relative.parent
        partial_parent.mkdir(parents=True)
        partial = partial_parent / ".library.actp.999.0.tmp"
        partial.write_bytes(b"interrupted")
        partial_output = work / "partial.json"
        partial_run = run(spec, partial_cache, partial_output)
        partial_prepared, partial_collapsed = cache_files(partial_cache)
        interrupted = {
            "returncode": partial_run.returncode,
            "partial_not_accepted": partial.read_bytes() == b"interrupted",
            "result_identical": normalized(partial_output) == reference_result,
            "normalized_result_sha256": canonical_sha256(normalized(partial_output), work),
            "prepared_identical": partial_prepared.read_bytes() == pristine_prepared,
            "collapsed_identical": partial_collapsed.read_bytes() == pristine_collapsed,
        }

        concurrent_cache = work / "concurrent-cache"
        concurrent_outputs = [work / "concurrent-a.json", work / "concurrent-b.json"]
        processes = [
            subprocess.Popen(
                [str(ACTINV), "run", str(spec), str(output)],
                cwd=ROOT,
                env=environment(concurrent_cache),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            for output in concurrent_outputs
        ]
        completed_concurrent = [process.communicate(timeout=60) for process in processes]
        concurrent_prepared, concurrent_collapsed = cache_files(concurrent_cache)
        concurrency = {
            "returncodes": [process.returncode for process in processes],
            "diagnostics": [
                "" if process.returncode == 0 else stderr[-1000:].replace(str(work), "<WORK>")
                for process, (_stdout, stderr) in zip(processes, completed_concurrent, strict=True)
            ],
            "results_identical": all(
                normalized(output) == reference_result for output in concurrent_outputs
            ),
            "normalized_result_sha256": [
                canonical_sha256(normalized(output), work) for output in concurrent_outputs
            ],
            "prepared_identical": concurrent_prepared.read_bytes() == pristine_prepared,
            "collapsed_identical": concurrent_collapsed.read_bytes() == pristine_collapsed,
        }

        result = {
            "schema": "actinv-p15-cache-integrity-1",
            "fixture": {
                "prepared_sha256": hashlib.sha256(pristine_prepared).hexdigest(),
                "collapsed_sha256": hashlib.sha256(pristine_collapsed).hexdigest(),
                "prepared_bytes": len(pristine_prepared),
                "collapsed_bytes": len(pristine_collapsed),
                "leading_zero_groups": bool(np.any(FIXTURE_SIGMA[:, 0] == 0.0)),
                "trailing_zero_groups": bool(np.any(FIXTURE_SIGMA[:, -1] == 0.0)),
                "internal_zero_groups": bool(np.any(FIXTURE_SIGMA[:, 1:-1] == 0.0)),
                "zero_only_row": bool(np.any(np.all(FIXTURE_SIGMA == 0.0, axis=1))),
                "normalized_result_sha256": canonical_sha256(reference_result, work),
            },
            "warm_reuse": warm_reuse,
            "mutation_plants": mutation_results,
            "deletion_recreation": deletion,
            "interrupted_publication": interrupted,
            "concurrent_preparation": concurrency,
        }
        result["pass"] = bool(
            warm_reuse["returncode"] == 0
            and all(value for key, value in warm_reuse.items() if key != "returncode")
            and all(all(value.values()) for value in mutation_results.values())
            and deletion["returncode"] == 0
            and deletion["result_identical"]
            and deletion["prepared_identical"]
            and deletion["collapsed_identical"]
            and all(value for key, value in interrupted.items() if key != "returncode")
            and interrupted["returncode"] == 0
            and concurrency["returncodes"] == [0, 0]
            and concurrency["results_identical"]
            and concurrency["prepared_identical"]
            and concurrency["collapsed_identical"]
        )
    RESULT.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=1, sort_keys=True))
    if not result["pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
