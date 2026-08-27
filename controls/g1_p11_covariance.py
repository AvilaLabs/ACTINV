#!/usr/bin/env python3
"""P11-G1: independent MF=33 parity, deterministic sidecar/cache, and fail-closed plants."""
from __future__ import annotations

import hashlib
import json
import os
import resource
import shutil
import subprocess
import tempfile
from pathlib import Path

from p11_covariance import compare_components, parse_mf33, read_sidecar


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results" / "g1_p11_covariance.json"
ACTINV = Path(os.environ.get("ACTINV_BIN", ROOT / "target" / "release" / "actinv"))
DUMP = Path(os.environ.get("ACTINV_DUMP", ROOT / "target" / "release" / "dump"))
SOURCE = Path(
    os.environ.get(
        "ACTINV_P11_TENDL_N",
        Path.home() / "nuclear-data" / "tendl-2025" / "files" / "n-working",
    )
)
PINNED = {
    "n-Fe056.tendl": "f33f867a4f9c4579a62954fe31dc6e70768ab2424dc8f282a122a93f156d2e1e",
    "n-Ni058.tendl": "26c8ae72a203187fae84f9a11c3997086cd70153386e04cf204b4122ab368916",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def run(arguments, *, ok=True, timeout=900):
    result = subprocess.run(
        [str(value) for value in arguments],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )
    if ok != (result.returncode == 0):
        raise RuntimeError(
            f"unexpected return code {result.returncode}: {' '.join(map(str, arguments))}\n"
            f"{result.stdout}{result.stderr}"
        )
    return result


def field(value) -> str:
    if isinstance(value, int):
        return f"{value:11d}"
    return f"{float(value):11.4E}"


def record(values, mat, mf, mt, sequence):
    return "".join(field(value) for value in values) + f"{mat:4d}{mf:2d}{mt:3d}{sequence:5d}"


def synthetic() -> list[str]:
    mat = 2631
    return [
        record([26056.0, 55.45, 0, 0, 0, 1], mat, 33, 102, 1),
        record([0.0, 0.0, 0, 102, 0, 1], mat, 33, 102, 2),
        record([0.0, 0.0, 1, 5, 6, 3], mat, 33, 102, 3),
        record([1.0, 2.0, 4.0, 0.01, 0.002, 0.04], mat, 33, 102, 4),
        record([0.0] * 6, mat, 33, 0, 5),
        record([0.0] * 6, 0, 0, 0, 6),
    ]


def replace_field(line: str, index: int, value) -> str:
    return line[: 11 * index] + field(value) + line[11 * (index + 1) :]


def plant_results(work: Path) -> dict:
    plants = {}
    base = synthetic()
    variants = {}
    numeric = base.copy()
    numeric[3] = numeric[3][:0] + " BADNUMBER " + numeric[3][11:]
    variants["numeric_field"] = numeric
    count = base.copy()
    count[2] = replace_field(count[2], 4, 7)
    variants["list_count"] = count
    order = base.copy()
    order[3] = replace_field(replace_field(order[3], 0, 2.0), 1, 1.0)
    variants["energy_order"] = order
    ls = base.copy()
    ls[2] = replace_field(ls[2], 2, 2)
    variants["ls_lt"] = ls
    unknown = base.copy()
    unknown[2] = replace_field(unknown[2], 3, 7)
    variants["unknown_lb"] = unknown
    nc = base.copy()
    nc[1] = replace_field(nc[1], 4, 1)
    variants["nc_component"] = nc
    foreign = base.copy()
    foreign[1] = replace_field(foreign[1], 2, 999)
    variants["foreign_mat"] = foreign
    wrong_reference = base.copy()
    wrong_reference[1] = replace_field(wrong_reference[1], 0, 4.0)
    variants["wrong_mf_reference"] = wrong_reference
    variants["truncation"] = base[:3] + base[4:]
    tail = base.copy()
    tail.insert(4, record([0.0] * 6, 2631, 33, 102, 99))
    variants["unconsumed_tail"] = tail
    for name, lines in variants.items():
        path = work / f"plant-{name}.endf"
        path.write_text("\n".join(lines) + "\n")
        result = run([DUMP, "covariance", path], ok=False)
        plants[name] = {
            "returncode": result.returncode,
            "has_context": "MF=33" in result.stderr or "covariance" in result.stderr,
            "pass": result.returncode != 0,
        }
    return plants


def main() -> None:
    missing = [str(binary) for binary in (ACTINV, DUMP) if not binary.exists()]
    if missing:
        raise SystemExit(f"missing binaries: {missing}")
    with tempfile.TemporaryDirectory(prefix="actinv-p11-g1-") as directory:
        work = Path(directory)
        inputs = work / "inputs"
        inputs.mkdir()
        for filename, expected in PINNED.items():
            source = SOURCE / filename
            if sha256(source) != expected:
                raise RuntimeError(f"pinned hash mismatch for {source}")
            shutil.copy2(source, inputs / filename)

        parser_parity = {}
        python_components = {}
        for filename in PINNED:
            path = inputs / filename
            reference = parse_mf33(path)
            observed = json.loads(run([DUMP, "covariance", path]).stdout)
            python_components[filename] = reference
            parser_parity[filename] = compare_components(reference, observed)

        activation = work / "activation.npz"
        run(
            [
                ACTINV,
                "build-library",
                inputs,
                activation,
                "--workers",
                "2",
                "--cache",
                work / "activation-cache",
            ]
        )
        covariance_cache = work / "covariance-cache"
        one = work / "one.cov.npz"
        four = work / "four.cov.npz"
        cached = work / "cached.cov.npz"
        first = run(
            [
                ACTINV,
                "build-covariance",
                inputs,
                activation,
                one,
                "--workers",
                "1",
                "--cache",
                covariance_cache,
            ]
        )
        run(
            [
                ACTINV,
                "build-covariance",
                inputs,
                activation,
                four,
                "--workers",
                "4",
                "--cache",
                work / "fresh-four-cache",
            ]
        )
        cached_result = run(
            [
                ACTINV,
                "build-covariance",
                inputs,
                activation,
                cached,
                "--workers",
                "4",
                "--cache",
                covariance_cache,
            ]
        )
        index = lambda path: path.with_name(path.stem + "_index.json")
        sidecar_parity = compare_components(
            python_components["n-Fe056.tendl"] + python_components["n-Ni058.tendl"],
            read_sidecar(one),
        )
        deterministic = {
            "one_vs_four_npz": one.read_bytes() == four.read_bytes(),
            "one_vs_cached_npz": one.read_bytes() == cached.read_bytes(),
            "one_vs_four_index": index(one).read_bytes() == index(four).read_bytes(),
            "one_vs_cached_index": index(one).read_bytes() == index(cached).read_bytes(),
            "cached_hits_two": "2 cache hits" in cached_result.stdout,
            "first_stdout": first.stdout.strip(),
        }

        # A byte-only source mutation changes one activation target identity. Rebuilding with the
        # activation cache must preserve the other target's covariance checkpoint.
        with (inputs / "n-Ni058.tendl").open("a") as stream:
            stream.write("\n")
        run(
            [
                ACTINV,
                "build-library",
                inputs,
                activation,
                "--workers",
                "2",
                "--cache",
                work / "activation-cache",
            ]
        )
        mutated = work / "mutated.cov.npz"
        mutation_result = run(
            [
                ACTINV,
                "build-covariance",
                inputs,
                activation,
                mutated,
                "--workers",
                "2",
                "--cache",
                covariance_cache,
            ]
        )
        mutation_isolation = {
            "cache_hits": 1 if "1 cache hits" in mutation_result.stdout else 0,
            "semantic_npz_identical": one.read_bytes() == mutated.read_bytes(),
        }

        plants = plant_results(work)
        activation_index = activation.with_name(activation.stem + "_index.json")
        original_index = json.loads(activation_index.read_text())
        duplicate_npz = work / "duplicate.npz"
        shutil.copy2(activation, duplicate_npz)
        duplicate_index = duplicate_npz.with_name(duplicate_npz.stem + "_index.json")
        duplicate_data = json.loads(json.dumps(original_index))
        duplicate_data["targets"].append(dict(duplicate_data["targets"][0]))
        duplicate_index.write_text(json.dumps(duplicate_data))
        duplicate_out = work / "duplicate.cov.npz"
        duplicate_result = run(
            [ACTINV, "build-covariance", inputs, duplicate_npz, duplicate_out], ok=False
        )
        plants["duplicate_target"] = {
            "returncode": duplicate_result.returncode,
            "published_pair": duplicate_out.exists() or index(duplicate_out).exists(),
            "pass": duplicate_result.returncode != 0
            and not duplicate_out.exists()
            and not index(duplicate_out).exists(),
        }

        mismatched_inputs = work / "mismatched-inputs"
        shutil.copytree(inputs, mismatched_inputs)
        with (mismatched_inputs / "n-Fe056.tendl").open("a") as stream:
            stream.write("\n")
        mismatch_out = work / "mismatch.cov.npz"
        mismatch_result = run(
            [ACTINV, "build-covariance", mismatched_inputs, activation, mismatch_out], ok=False
        )
        plants["activation_source_hash_mismatch"] = {
            "returncode": mismatch_result.returncode,
            "published_pair": mismatch_out.exists() or index(mismatch_out).exists(),
            "pass": mismatch_result.returncode != 0
            and not mismatch_out.exists()
            and not index(mismatch_out).exists(),
        }

        target_index = json.loads(index(one).read_text())
        counts = {
            item["file"]: {
                "sections": item["mf33_sections"],
                "components": item["components"],
                "lb_counts": item["lb_counts"],
            }
            for item in target_index["targets"]
        }
        peak_rss_bytes = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss * 1024
        output = {
            "pinned_sources": PINNED,
            "parser_parity": parser_parity,
            "sidecar_parity": sidecar_parity,
            "counts": counts,
            "determinism_and_cache": deterministic,
            "mutation_isolation": mutation_isolation,
            "plants": plants,
            "peak_child_rss_bytes": peak_rss_bytes,
            "allocation_limit_bytes": 1_000_000_000,
        }
        output["pass"] = bool(
            all(item["pass"] for item in parser_parity.values())
            and sidecar_parity["pass"]
            and counts["n-Fe056.tendl"]["components"] == 105
            and counts["n-Ni058.tendl"]["components"] == 104
            and all(deterministic[key] for key in deterministic if key != "first_stdout")
            and mutation_isolation == {"cache_hits": 1, "semantic_npz_identical": True}
            and len(plants) >= 12
            and all(item["pass"] for item in plants.values())
            and peak_rss_bytes < 2 * 1024**3
        )
    RESULT.write_text(json.dumps(output, indent=1) + "\n")
    print(json.dumps(output, indent=1))
    raise SystemExit(0 if output["pass"] else 1)


if __name__ == "__main__":
    main()
