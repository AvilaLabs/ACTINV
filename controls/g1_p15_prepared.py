#!/usr/bin/env python3
"""P15 G1/G2: deterministic production preparation and independent bit identity."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import time
import zipfile

import numpy as np
from numpy.lib import format as npy_format


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results/p15_prepared_identity.json"
ACTINV = Path(os.environ.get("ACTINV_BIN", ROOT / "target/release/actinv"))
PROBE = Path(os.environ.get("ACTINV_PREPARED_PROBE", ROOT / "target/release/prepared_data_probe"))
DATA = Path(os.environ.get("ACTINV_DATA_ROOT", "/home/connoravila/nuclear-data"))
LIBRARY = Path(
    os.environ.get("ACTINV_LIBRARY", DATA / "tendl-2025/builds/full/neutron.n.p10.npz")
)
INDEX = Path(str(LIBRARY).removesuffix(".npz") + "_index.json")
DECAY_PRIMARY = Path(
    os.environ.get(
        "ACTINV_ENDF_DECAY", DATA / "endfb-viii.0-decay/bulk/endf-b-viii-0_decay.dat"
    )
)
DECAY_FALLBACK = Path(
    os.environ.get("ACTINV_JEFF_DECAY", DATA / "jeff-3.3-decay/bulk/jeff-3-3_decay.dat")
)
EXPECTED = {
    "activation_library": "ec4c72bf598dc8ad3d533d9cfafdcf493e2d1f949a3e4db6251495659b68cc44",
    "activation_index": "8bd19b4001c246758e739cd0067a0087e1ce5c2157438dae97bd52e1d3beb3fb",
    "decay_primary": "6f04cf009086c179021f243a58dadc2d5bb078de5ba39c4fe46ccad77d228ddb",
    "decay_fallback": "850b8b7f85f8d88b6ad826c4cd341aaaffabd525c8ecf3c588a0ad437bf5d123",
}
PREPARED_ALGORITHM = b"actinv-prepared-library-1\nnpz-f64-spans-v1\nsource-row-order-v1\n"
COLLAPSED_ALGORITHM = (
    b"actinv-collapsed-spectrum-1\nopening-collapse-order-v1\n"
    b"fission-spectrum-average-v1\n"
)
THREADS = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "RAYON_NUM_THREADS",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def npy_header(source: object) -> tuple[tuple[int, ...], bool, np.dtype]:
    version = npy_format.read_magic(source)
    if version == (1, 0):
        return npy_format.read_array_header_1_0(source)
    if version in {(2, 0), (3, 0)}:
        return npy_format.read_array_header_2_0(source)
    raise AssertionError(f"unsupported NPY version {version}")


def normalized_result(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    value.pop("ms", None)
    return value


def canonical_sha256(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def public_spec(path: Path) -> dict[str, object]:
    value = json.loads((ROOT / "examples/fns_fe_5min.json").read_text(encoding="utf-8"))
    value["library"] = {"path": str(LIBRARY), "sha256": EXPECTED["activation_library"]}
    value["decay"] = {"primary": str(DECAY_PRIMARY), "fallback": str(DECAY_FALLBACK)}
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    return value


def ascending_flux(specification: dict[str, object]) -> list[float]:
    spectrum = specification["spectrum"]
    values = [float(value) for value in spectrum["flux_per_group"]]
    if spectrum.get("descending", False):
        values.reverse()
    if spectrum.get("total") is not None:
        total = float(spectrum["total"])
        present = sum(values)
        if present > 0.0:
            scale = total / present
            values = [value * scale for value in values]
    return values


def run_cold(specification: Path, cache: Path, output: Path) -> float:
    environment = os.environ.copy()
    environment["ACTINV_CACHE_DIR"] = str(cache)
    for name in THREADS:
        environment[name] = "1"
    started = time.perf_counter_ns()
    completed = subprocess.run(
        [str(ACTINV), "run", str(specification), str(output)],
        cwd=ROOT,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=120,
        check=False,
    )
    elapsed_ms = (time.perf_counter_ns() - started) * 1.0e-6
    if completed.returncode:
        raise RuntimeError(f"cold preparation failed:\n{completed.stdout}\n{completed.stderr[-4000:]}")
    return elapsed_ms


def artifact_pair(cache: Path) -> tuple[Path, Path]:
    prepared = list(cache.glob("prepared-v1/*/library.actp"))
    collapsed = list(cache.glob("prepared-v1/*/spectrum-*.actc"))
    if len(prepared) != 1 or len(collapsed) != 1:
        raise AssertionError(
            f"expected one prepared/collapsed artifact in {cache}, got {prepared}, {collapsed}"
        )
    return prepared[0], collapsed[0]


def parse_headers(prepared: bytes, collapsed: bytes) -> dict[str, object]:
    if prepared[:8] != b"ACTPLB01" or collapsed[:8] != b"ACTCOL01":
        raise AssertionError("artifact magic differs")
    if hashlib.sha256(prepared[:-32]).digest() != prepared[-32:]:
        raise AssertionError("prepared integrity trailer differs")
    if hashlib.sha256(collapsed[:-32]).digest() != collapsed[-32:]:
        raise AssertionError("collapsed integrity trailer differs")
    p_version, p_header = struct.unpack_from("<II", prepared, 8)
    c_version, c_header = struct.unpack_from("<II", collapsed, 8)
    if (p_version, p_header, c_version, c_header) != (1, 224, 1, 288):
        raise AssertionError("artifact schema/header version differs")
    p_rows, p_groups, p_bounds = struct.unpack_from("<QQQ", prepared, 16)
    (
        p_rows_offset,
        p_values_offset,
        p_bounds_offset,
        p_value_count,
        p_dense_count,
        p_payload_end,
        p_artifact_len,
    ) = struct.unpack_from("<QQQQQQQ", prepared, 48)
    c_rows, c_groups, c_bounds = struct.unpack_from("<QQQ", collapsed, 16)
    (
        c_bounds_offset,
        c_flux_offset,
        c_rows_offset,
        c_values_offset,
        c_fission_offset,
        c_presence_offset,
        c_payload_end,
        c_artifact_len,
    ) = struct.unpack_from("<QQQQQQQQ", collapsed, 48)
    expected_p_values = 224 + p_rows * 40
    expected_p_bounds = expected_p_values + p_value_count * 8
    expected_p_end = expected_p_bounds + p_bounds * 8
    expected_c_flux = 288 + c_bounds * 8
    expected_c_rows = expected_c_flux + c_groups * 8
    expected_c_values = expected_c_rows + c_rows * 24
    expected_c_fission = expected_c_values + c_rows * 8
    expected_c_presence = expected_c_fission + c_rows * 8
    expected_c_end = expected_c_presence + c_rows
    if (
        (p_rows, p_groups, p_bounds) != (c_rows, c_groups, c_bounds)
        or p_bounds != p_groups + 1
        or p_dense_count != p_rows * p_groups
        or (p_rows_offset, p_values_offset, p_bounds_offset, p_payload_end, p_artifact_len)
        != (224, expected_p_values, expected_p_bounds, expected_p_end, len(prepared))
        or p_artifact_len != p_payload_end + 32
        or (
            c_bounds_offset,
            c_flux_offset,
            c_rows_offset,
            c_values_offset,
            c_fission_offset,
            c_presence_offset,
            c_payload_end,
            c_artifact_len,
        )
        != (
            288,
            expected_c_flux,
            expected_c_rows,
            expected_c_values,
            expected_c_fission,
            expected_c_presence,
            expected_c_end,
            len(collapsed),
        )
        or c_artifact_len != c_payload_end + 32
    ):
        raise AssertionError("artifact counts/offsets do not close exactly")
    library_digest = bytes.fromhex(EXPECTED["activation_library"])
    index_digest = bytes.fromhex(EXPECTED["activation_index"])
    if prepared[104:136] != library_digest or prepared[136:168] != index_digest:
        raise AssertionError("prepared source identities differ")
    if collapsed[112:144] != library_digest or collapsed[144:176] != index_digest:
        raise AssertionError("collapsed source identities differ")
    if collapsed[208:240] != prepared[-32:]:
        raise AssertionError("collapsed artifact does not bind the prepared integrity digest")
    if prepared[168:200] != hashlib.sha256(PREPARED_ALGORITHM).digest():
        raise AssertionError("prepared algorithm identity differs")
    if collapsed[240:272] != hashlib.sha256(COLLAPSED_ALGORITHM).digest():
        raise AssertionError("collapsed algorithm identity differs")
    if any(prepared[200:224]) or any(collapsed[272:288]):
        raise AssertionError("reserved header bytes are nonzero")
    return {
        "rows": int(p_rows),
        "groups": int(p_groups),
        "bounds": int(p_bounds),
        "prepared_rows_offset": int(p_rows_offset),
        "prepared_values_offset": int(p_values_offset),
        "prepared_bounds_offset": int(p_bounds_offset),
        "prepared_values": int(p_value_count),
        "dense_values": int(p_dense_count),
        "collapsed_bounds_offset": int(c_bounds_offset),
        "collapsed_flux_offset": int(c_flux_offset),
        "collapsed_rows_offset": int(c_rows_offset),
        "collapsed_values_offset": int(c_values_offset),
        "collapsed_fission_offset": int(c_fission_offset),
        "collapsed_presence_offset": int(c_presence_offset),
    }


def selection_hasher() -> hashlib._Hash:
    return hashlib.sha256()


def inspect_production(
    prepared_path: Path,
    collapsed_path: Path,
    selections: dict[str, set[int]],
) -> tuple[dict[str, object], dict[str, str]]:
    prepared = prepared_path.read_bytes()
    collapsed = collapsed_path.read_bytes()
    header = parse_headers(prepared, collapsed)
    rows = header["rows"]
    groups = header["groups"]
    p_rows_offset = header["prepared_rows_offset"]
    p_values_offset = header["prepared_values_offset"]
    c_rows_offset = header["collapsed_rows_offset"]
    flux = np.frombuffer(
        collapsed,
        dtype="<f8",
        count=groups,
        offset=header["collapsed_flux_offset"],
    )
    if hashlib.sha256(flux.tobytes()).digest() != collapsed[176:208]:
        raise AssertionError("collapsed flux hash differs from its stored bits")
    collapsed_values = np.frombuffer(
        collapsed,
        dtype="<f8",
        count=rows,
        offset=header["collapsed_values_offset"],
    )
    prepared_bounds = np.frombuffer(
        prepared,
        dtype="<f8",
        count=groups + 1,
        offset=header["prepared_bounds_offset"],
    )
    collapsed_bounds = np.frombuffer(
        collapsed,
        dtype="<f8",
        count=groups + 1,
        offset=header["collapsed_bounds_offset"],
    )
    nonzero_flux = np.flatnonzero(flux)
    first_flux = int(nonzero_flux[0]) if nonzero_flux.size else groups
    last_flux = int(nonzero_flux[-1]) + 1 if nonzero_flux.size else first_flux
    denominator = 0.0
    for value in flux:
        denominator += float(value)
    hashers = {name: selection_hasher() for name in selections}
    descriptor_mismatches = 0
    collapsed_row_mismatches = 0
    collapse_bit_mismatches = 0
    boundary_bit_mismatches = 0
    nonzero_values = 0
    retained_values = 0
    expected_value_index = 0
    with zipfile.ZipFile(LIBRARY) as archive:
        with archive.open("rows.npy") as source:
            row_shape, row_fortran, row_dtype = npy_header(source)
            source_rows = np.frombuffer(source.read(), dtype=row_dtype).reshape(row_shape)
        with archive.open("bounds.npy") as source:
            bounds_shape, bounds_fortran, bounds_dtype = npy_header(source)
            source_bounds = np.frombuffer(source.read(), dtype=bounds_dtype).reshape(bounds_shape)
        if row_fortran or bounds_fortran or row_shape != (rows, 5) or bounds_shape != (groups + 1,):
            raise AssertionError("source rows/bounds structure differs")
        boundary_bit_mismatches += int(
            np.count_nonzero(source_bounds.view("<u8") != prepared_bounds.view("<u8"))
        )
        boundary_bit_mismatches += int(
            np.count_nonzero(source_bounds.view("<u8") != collapsed_bounds.view("<u8"))
        )
        with archive.open("sig.npy") as source:
            sig_shape, sig_fortran, sig_dtype = npy_header(source)
            if sig_fortran or sig_shape != (rows, groups) or sig_dtype != np.dtype("<f8"):
                raise AssertionError("source sig structure differs")
            for base in range(0, rows, 512):
                count = min(512, rows - base)
                raw = source.read(count * groups * 8)
                if len(raw) != count * groups * 8:
                    raise AssertionError("source sig payload truncates")
                values = np.frombuffer(raw, dtype="<f8").reshape(count, groups)
                nonzero_values += int(np.count_nonzero(values.view("<u8")))
                products = values[:, first_flux:last_flux] * flux[first_flux:last_flux]
                ordered = np.empty((count, products.shape[1] + 1), dtype="<f8")
                ordered[:, 0] = 0.0
                ordered[:, 1:] = products
                numerators = np.add.accumulate(ordered, axis=1)[:, -1]
                expected_collapsed = np.where(denominator > 0.0, numerators / denominator, 0.0)
                collapse_bit_mismatches += int(
                    np.count_nonzero(
                        expected_collapsed.view("<u8")
                        != collapsed_values[base : base + count].view("<u8")
                    )
                )
                for local in range(count):
                    source_row = base + local
                    target, mt, zap, lfs, lmf, first, span_count, value_index = struct.unpack_from(
                        "<QiiiiIIQ", prepared, p_rows_offset + source_row * 40
                    )
                    c_target, c_mt, c_zap, c_lfs, c_lmf = struct.unpack_from(
                        "<Qiiii", collapsed, c_rows_offset + source_row * 24
                    )
                    source_identity = tuple(int(value) for value in source_rows[source_row])
                    if (target, mt, zap, lfs, lmf) != source_identity:
                        descriptor_mismatches += 1
                    if (c_target, c_mt, c_zap, c_lfs, c_lmf) != source_identity:
                        collapsed_row_mismatches += 1
                    if value_index != expected_value_index:
                        descriptor_mismatches += 1
                    expected_value_index += span_count
                    retained_values += span_count
                    bits = values[local].view("<u8")
                    nonzero_groups = np.flatnonzero(bits)
                    expected_first = int(nonzero_groups[0]) if nonzero_groups.size else 0
                    expected_span = (
                        int(nonzero_groups[-1] - nonzero_groups[0] + 1)
                        if nonzero_groups.size
                        else 0
                    )
                    if (first, span_count) != (expected_first, expected_span):
                        descriptor_mismatches += 1
                    if span_count:
                        prepared_start = p_values_offset + value_index * 8
                        source_start = (local * groups + first) * 8
                        if (
                            prepared[prepared_start : prepared_start + span_count * 8]
                            != raw[source_start : source_start + span_count * 8]
                        ):
                            descriptor_mismatches += 1
                    for name, targets in selections.items():
                        if target not in targets:
                            continue
                        hasher = hashers[name]
                        hasher.update(struct.pack("<QQiiii", source_row, target, mt, zap, lfs, lmf))
                        hasher.update(raw[local * groups * 8 : (local + 1) * groups * 8])
            if source.read(1):
                raise AssertionError("source sig payload has trailing bytes")
    if expected_value_index != header["prepared_values"]:
        descriptor_mismatches += 1
    independent = {
        "rows": rows,
        "groups": groups,
        "boundaries": groups + 1,
        "dense_sig_bytes": rows * groups * 8,
        "prepared_bytes": len(prepared),
        "collapsed_bytes": len(collapsed),
        "prepared_to_dense_fraction": len(prepared) / (rows * groups * 8),
        "nonzero_values": nonzero_values,
        "retained_values": retained_values,
        "descriptor_or_retained_bit_mismatches": descriptor_mismatches,
        "collapsed_row_mismatches": collapsed_row_mismatches,
        "boundary_bit_mismatches": boundary_bit_mismatches,
        "collapse_bit_mismatches": collapse_bit_mismatches,
        "prepared_sha256": hashlib.sha256(prepared).hexdigest(),
        "collapsed_sha256": hashlib.sha256(collapsed).hexdigest(),
    }
    return independent, {name: hasher.hexdigest() for name, hasher in hashers.items()}


def run_probe(
    artifact: Path,
    targets: set[int],
) -> dict[str, object]:
    target_text = ",".join(str(value) for value in sorted(targets)) if targets else "-"
    completed = subprocess.run(
        [
            str(PROBE),
            str(artifact),
            EXPECTED["activation_library"],
            EXPECTED["activation_index"],
            target_text,
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=120,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(f"prepared probe failed: {completed.stderr[-4000:]}")
    return json.loads(completed.stdout)


def main() -> None:
    files = {
        "activation_library": LIBRARY,
        "activation_index": INDEX,
        "decay_primary": DECAY_PRIMARY,
        "decay_fallback": DECAY_FALLBACK,
    }
    identities = {
        name: {
            "path": str(path),
            "bytes": path.stat().st_size,
            "expected_sha256": EXPECTED[name],
            "actual_sha256": sha256(path),
        }
        for name, path in files.items()
    }
    if any(value["actual_sha256"] != value["expected_sha256"] for value in identities.values()):
        raise AssertionError("production input identity differs")
    index = json.loads(INDEX.read_text(encoding="utf-8"))
    target_count = len(index["targets"])
    iron = {
        position
        for position, target in enumerate(index["targets"])
        if int(target["za"]) // 1000 == 26
    }
    selections = {
        "empty": set(),
        "iron": iron,
        "noncontiguous": {0, 17, target_count // 2, target_count - 1},
        "all": set(range(target_count)),
    }
    with tempfile.TemporaryDirectory(prefix="actinv-p15-g1-") as directory:
        work = Path(directory)
        spec_path = work / "public.json"
        specification = public_spec(spec_path)
        flux = ascending_flux(specification)
        first_output = work / "first.json"
        second_output = work / "second.json"
        first_ms = run_cold(spec_path, work / "cache-a", first_output)
        second_ms = run_cold(spec_path, work / "cache-b", second_output)
        first_prepared, first_collapsed = artifact_pair(work / "cache-a")
        second_prepared, second_collapsed = artifact_pair(work / "cache-b")
        prepared_identical = first_prepared.read_bytes() == second_prepared.read_bytes()
        collapsed_identical = first_collapsed.read_bytes() == second_collapsed.read_bytes()
        first_result = normalized_result(first_output)
        second_result = normalized_result(second_output)
        result_identical = first_result == second_result
        independent, expected_selection_hashes = inspect_production(
            first_prepared, first_collapsed, selections
        )
        stored_flux = np.frombuffer(
            first_collapsed.read_bytes(),
            dtype="<f8",
            count=independent["groups"],
            offset=288 + independent["boundaries"] * 8,
        )
        flux_bit_identity = all(
            struct.pack("<d", expected) == struct.pack("<d", actual)
            for expected, actual in zip(flux, stored_flux, strict=True)
        )
        probes = {name: run_probe(first_prepared, targets) for name, targets in selections.items()}
        selection_checks = {
            name: {
                **probe,
                "expected_sha256": expected_selection_hashes[name],
                "identity": probe["selection_sha256"] == expected_selection_hashes[name],
                "allocation_bound": probe["materialized_bytes"]
                <= probe["selected_payload_bytes"] + 16 * 1024**2,
            }
            for name, probe in probes.items()
        }
        evidence = {
            "schema": "actinv-p15-prepared-identity-1",
            "inputs": identities,
            "production": independent,
            "preparations": {
                "first_cold_ms": first_ms,
                "second_cold_ms": second_ms,
                "prepared_byte_identical": prepared_identical,
                "collapsed_byte_identical": collapsed_identical,
                "normalized_result_identical": result_identical,
                "normalized_result_sha256": {
                    "first": canonical_sha256(first_result),
                    "second": canonical_sha256(second_result),
                },
                "flux_bit_identity": flux_bit_identity,
            },
            "indexed_selections": selection_checks,
        }
        evidence["pass"] = bool(
            prepared_identical
            and collapsed_identical
            and result_identical
            and flux_bit_identity
            and independent["prepared_to_dense_fraction"] <= 0.35
            and independent["rows"] == 167_735
            and independent["groups"] == 709
            and independent["boundaries"] == 710
            and independent["descriptor_or_retained_bit_mismatches"] == 0
            and independent["collapsed_row_mismatches"] == 0
            and independent["boundary_bit_mismatches"] == 0
            and independent["collapse_bit_mismatches"] == 0
            and all(value["identity"] and value["allocation_bound"] for value in selection_checks.values())
        )
    RESULT.write_text(json.dumps(evidence, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(evidence, indent=1, sort_keys=True))
    if not evidence["pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
