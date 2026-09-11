#!/usr/bin/env python3
"""Aggregate P18b-G2 checkpoints and run the frozen official-checker sample.

The large source, runtime and official-tool checkpoints remain under ``target/``. The committed result contains only
their hashes, complete aggregate accounting and compact deterministic examples. No measurement input is opened.
"""

from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import struct
import subprocess
from typing import Any, Iterator

from p18b_decimal_corpus_oracle import verify_examples


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results/g2_p18b_corpus_classification.json"
CHECKPOINT_ROOT = Path(
    os.environ.get("ACTINV_P18B_G2_CHECKPOINT_ROOT", ROOT / "target/p18b-g2")
)
P18_CHECKPOINT_ROOT = Path(
    os.environ.get("ACTINV_P18_G2_CHECKPOINT_ROOT", ROOT / "target/p18-g2")
)
DATA_ROOT = Path(
    os.environ.get("ACTINV_P18_DATA_ROOT", "/home/connoravila/nuclear-data/tendl-2025")
)
PROTOCOL = ROOT / "protocols/ACTINV-P18b_PROTOCOL.md"
G0 = ROOT / "results/g0_p18b_seal.json"
G1 = ROOT / "results/g1_p18b_decimal_oracle.json"
G1_CHECK = ROOT / "results/g1_p18b_check.json"
P18_EVIDENCE = ROOT / "results/g2_p18_corpus_audit.json"
PROBE = ROOT / "crates/actinv-data/src/bin/p18b_corpus_probe.rs"
DECIMAL_CONTROL = ROOT / "controls/p18b_decimal_corpus_oracle.py"
OFFICIAL_CHECKPOINT = CHECKPOINT_ROOT / "official.jsonl"
OFFICIAL_OUTPUT_ROOT = CHECKPOINT_ROOT / "official-output"

PROTOCOL_SHA256 = "69076fa2656b239addbb15fbb4727caaa2c8ea37b3aa82a141f3a2b0b619eabe"
G0_SHA256 = "99648da5dc4d4209e2607ea16ca9d4e34127c64ce7430f9194c48370562271ad"
G1_SHA256 = "e258ae73302fce5ef63419e8bc24507e42707d14ca5bb1d4b60a0515197d3adc"
G1_CHECK_SHA256 = "650c17c22f88c0218c99444d2c75a2abdc902e2c4dda5ddc757e56ec9fa40d0d"
P18_EVIDENCE_SHA256 = "e20fba865c36131f27bce7ac110957336c55d9c8455e79a8ddac0edde66df9cb"
G0_SCHEMA = "actinv-p18b-g0-seal-1"
EPS_STANDARD = Decimal("0.001")
ADDRESS_SPACE_BYTES = 12_000_000 * 1024

REFERENCE_ROOT = Path(
    os.environ.get("ACTINV_P18B_REFERENCE_ROOT", ROOT / "target/p18b-reference")
)
IAEA_ROOT = REFERENCE_ROOT / "endf-utility-codes"
COMPILER = Path(
    os.environ.get(
        "ACTINV_P18B_GFORTRAN",
        REFERENCE_ROOT
        / "fortran-toolchain/sysroot/usr/bin/x86_64-linux-gnu-gfortran-15",
    )
)
COMPILER_PREFIX = os.environ.get(
    "ACTINV_P18B_GFORTRAN_PREFIX", "/usr/lib/gcc/x86_64-linux-gnu/15/"
)
CORPUS_TOOL_ROOT = REFERENCE_ROOT / "corpus-tools"
OFFICIAL_CHECKR = CORPUS_TOOL_ROOT / "checkr-official"
COMPATIBLE_CHECKR = CORPUS_TOOL_ROOT / "checkr-compatible"
OFFICIAL_FIZCON = CORPUS_TOOL_ROOT / "fizcon-official"
IAEA_COMMIT = "c2a6718bd831b5c8a6e975beb1946954b1d73c40"
IAEA_SOURCES = {
    "checkr/checkr.f": "739169c525663a3a80d62f8047243b6d3a0d2b36e05cf95a7336ae58363d684e",
    "fizcon/fizcon.f": "15eac8dbcc1f1c0b8825d9e2a487d7e26f4717ccacad373f226a01c721e7527e",
}
EXPECTED_CORPUS_BINARIES = {
    "checkr_official": "30870799488a8b03b1e4a4b4fc585934310f7d6e7afec304c982796cf17dbdb2",
    "checkr_compatible": "7dfed8f0c9bf8307094f17e766003dd864b7f46837f671fd86aafd641dc391c1",
    "fizcon_official": "105b52f03275a7243142491d68f76bb9db5ecfc20469bdb46a0857c43fb09fc6",
}

CORPORA = {
    "neutron": {
        "directory": "files/n-working",
        "manifest": "staging/TENDL-n-working.manifest.json",
        "manifest_sha256": "a6d17f996153d2671c0c51bfb6303e2a87a5af03e0696bfb34d668a31dbfb2a2",
        "predecessor": "neutron.jsonl",
        "predecessor_sha256": "9de033be0c68fce389f0832acc479edc474d7162075c96c8533751ccbb863b25",
        "checkpoint": "neutron.jsonl",
        "groups": 709,
        "temperature_k": 293.6,
    },
    "proton": {
        "directory": "files/p",
        "manifest": "staging/TENDL-p.manifest.json",
        "manifest_sha256": "98a8bd55784c326b8696de91f494111326378e776a975a512e59806a8c9ec2ef",
        "predecessor": "proton.jsonl",
        "predecessor_sha256": "bd962c9e25e2bf5e8c919202ab8eeacc1c78b6278ba2a31bd26d636fcccbaade",
        "checkpoint": "proton.jsonl",
        "groups": 162,
        "temperature_k": 0.0,
    },
    "deuteron": {
        "directory": "files/d",
        "manifest": "staging/TENDL-d.manifest.json",
        "manifest_sha256": "afb52c55b2a1babca998cc3d8af0f7004c64f85d160e3c5aabf16a05839355d9",
        "predecessor": "deuteron.jsonl",
        "predecessor_sha256": "6e328ff5749c7a9c8f475c109fdb6c871dd28bc14e00f82eed7836f5ea0a04dd",
        "checkpoint": "deuteron.jsonl",
        "groups": 162,
        "temperature_k": 0.0,
    },
    "alpha": {
        "directory": "files/a",
        "manifest": "staging/TENDL-a.manifest.json",
        "manifest_sha256": "e3aaf11e60c46b43361796c2c297bab4fb714fe57ab26a315594f2b4799dfdbf",
        "predecessor": "alpha.jsonl",
        "predecessor_sha256": "1da6f0954fc6e17abd0a518d6bf1c6bd15b3fa71b417fd72c0bc42be3fb432e0",
        "checkpoint": "alpha.jsonl",
        "groups": 162,
        "temperature_k": 0.0,
    },
}

SOURCE_COUNT_FIELDS = (
    "comparisons",
    "pointwise_individual_comparisons",
    "pointwise_sum_comparisons",
    "collapsed_individual_comparisons",
    "collapsed_sum_comparisons",
    "mf9_comparisons",
    "mf10_comparisons",
    "p18_violations",
    "binary64_excesses",
    "binary64_standard_compatible_excesses",
)
FRACTION_COUNT_FIELDS = (
    "comparisons",
    "individual_comparisons",
    "sum_comparisons",
    "binary64_excesses",
    "binary64_standard_compatible_excesses",
)
CONTRACT_COUNT_FIELDS = (
    "product_tables",
    "missing_totals",
    "non_linlin_tables",
    "grid_contract_tables",
    "threshold_contract_tables",
)
RUNTIME_COUNT_FIELDS = (
    "vectors",
    "group_comparisons",
    "unchanged",
    "standard_compatible_excesses",
    "outside_standard_excesses",
    "raw_runtime_total_differences",
    "processed_reactions",
    "inelastic_constructed_loss_groups",
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise AssertionError(f"{path.name}:{line_number}: invalid JSON: {error}") from error
            require(isinstance(value, dict), f"{path.name}:{line_number}: row is not an object")
            yield value


def f64_from_bits(value: str) -> float:
    require(bool(re.fullmatch(r"[0-9a-f]{16}", value)), f"invalid binary64 bits {value!r}")
    return struct.unpack(">d", bytes.fromhex(value))[0]


def decimal_f64(bits: str) -> Decimal:
    return Decimal.from_float(f64_from_bits(bits))


def standard_compatible(total: Decimal, partial: Decimal) -> bool:
    if partial <= total:
        return True
    if total > 0:
        return partial - total <= EPS_STANDARD * total
    return partial <= EPS_STANDARD


def validate_source_example(
    example: dict[str, Any], contract_issues: list[dict[str, Any]]
) -> None:
    partial = decimal_f64(example["partial_bits"])
    total = decimal_f64(example["total_bits"])
    low = decimal_f64(example["partial_low_bits"])
    high = decimal_f64(example["total_high_bits"])
    tolerance = decimal_f64(example["p18_tolerance_bits"])
    require(all(value.is_finite() and value >= 0 for value in (partial, total, low, high, tolerance)), "source boundary finite/nonnegative")
    expected_standard = standard_compatible(total, partial)
    require(example["standard_compatible"] is expected_standard, "source standard boundary")
    expected_p18 = (
        f64_from_bits(example["partial_bits"])
        - f64_from_bits(example["total_bits"])
        - f64_from_bits(example["p18_tolerance_bits"])
        > 0.0
    )
    require(example["p18_violation"] is expected_p18, "source P18 boundary")
    require(partial > total, "retained source boundary must be an excess")
    primary = example["binary64_primary_class"]
    if primary in {"missing_total_or_grid_contract", "threshold_contract"}:
        matching_contract = any(
            issue["mf"] == example["mf"]
            and issue["mt"] == example["mt"]
            and issue["zap"] == example["zap"]
            and issue["decision"] == primary
            and (
                example["raw_lfs"] is None
                or issue["raw_lfs"] == example["raw_lfs"]
            )
            for issue in contract_issues
        )
        require(matching_contract, "source contract boundary")
    else:
        expected_class = (
            "definite_source_excess" if low > high else "printing_envelope_excess"
        )
        require(primary == expected_class, "source printing boundary")
    require(example["scope"] in {"pointwise", "collapsed", "multiplicity"}, "source boundary scope")
    if example["scope"] == "pointwise" or example["scope"] == "multiplicity":
        require(example["energy_bits"] is not None and example["group"] is None, "pointwise context")
    else:
        require(example["energy_bits"] is None and isinstance(example["group"], int), "group context")


def validate_runtime_example(example: dict[str, Any]) -> None:
    partial = decimal_f64(example["sum_bits"])
    total = decimal_f64(example["total_bits"])
    raw_total = decimal_f64(example["raw_total_bits"])
    require(all(value.is_finite() and value >= 0 for value in (partial, total, raw_total)), "runtime boundary finite/nonnegative")
    require(partial > total, "retained runtime boundary must be an excess")
    expected = (
        "standard_compatible_excess"
        if standard_compatible(total, partial)
        else "outside_standard_excess"
    )
    require(example["decision"] == expected, "runtime standard boundary")


def expected_manifest(projectile: str) -> dict[str, dict[str, Any]]:
    config = CORPORA[projectile]
    path = DATA_ROOT / str(config["manifest"])
    require(path.is_file(), f"missing {projectile} manifest")
    require(sha256(path) == config["manifest_sha256"], f"{projectile} manifest hash")
    payload = json.loads(path.read_text())
    output = {}
    for row in payload["files"]:
        digest = row.get("working_sha256", row.get("sha256"))
        output[row["name"]] = {"sha256": digest, "bytes": int(row["bytes"])}
    require(len(output) == 2_850, f"{projectile} manifest count")
    return output


def add_counts(destination: Counter[str], source: dict[str, Any], fields: tuple[str, ...]) -> None:
    for field in fields:
        value = source[field]
        require(isinstance(value, int) and value >= 0, f"invalid nonnegative count {field}")
        destination[field] += value


def class_changes(binary64: dict[str, Any], exact: dict[str, Any]) -> int:
    """Comparisons whose exact class differs from the binary64 label."""
    return sum(
        max(0, int(exact.get(name, 0)) - int(binary64.get(name, 0)))
        for name in exact
    )


def deterministic_examples(
    rows: list[dict[str, Any]], *, seed: str, limit: int = 12
) -> list[dict[str, Any]]:
    ranked = sorted(
        rows,
        key=lambda row: hashlib.sha256(seed.encode() + b"\0" + canonical(row)).hexdigest(),
    )
    return ranked[:limit]


def validate_header(projectile: str, header: dict[str, Any]) -> None:
    config = CORPORA[projectile]
    require(header.get("kind") == "header", f"{projectile} checkpoint header")
    require(header.get("schema") == "actinv-p18b-corpus-probe-2", f"{projectile} checkpoint schema")
    require(header.get("projectile") == projectile, f"{projectile} checkpoint identity")
    require(header.get("groups") == config["groups"], f"{projectile} group count")
    require(header.get("temperature_k") == config["temperature_k"], f"{projectile} temperature")
    require(header.get("processing_grid_density") == 1.0, f"{projectile} processing density")
    require(
        header.get("predecessor_checkpoint_sha256") == config["predecessor_sha256"],
        f"{projectile} predecessor identity",
    )
    sources = {
        "probe": PROBE,
        "activation": ROOT / "crates/actinv-data/src/activation.rs",
        "endf": ROOT / "crates/actinv-data/src/endf.rs",
        "groups": ROOT / "crates/actinv-data/src/groups.rs",
        "processing": ROOT / "crates/actinv-data/src/processing.rs",
        "resonance": ROOT / "crates/actinv-data/src/resonance.rs",
    }
    for name, path in sources.items():
        require(header.get(f"{name}_source_sha256") == sha256(path), f"{projectile} stale {name} source")


def validate_target_predecessor(
    projectile: str, file_name: str, target: dict[str, Any], predecessor: dict[str, Any]
) -> None:
    source = target["source"]
    old = predecessor["conservation"]
    for field in (
        "pointwise_individual_comparisons",
        "pointwise_sum_comparisons",
        "collapsed_individual_comparisons",
        "collapsed_sum_comparisons",
        "mf9_comparisons",
        "mf10_comparisons",
    ):
        require(source[field] == old[field], f"{projectile}/{file_name}/MAT={target['mat']}: {field}")
    require(source["comparisons"] == sum(source[field] for field in (
        "pointwise_individual_comparisons", "pointwise_sum_comparisons",
        "collapsed_individual_comparisons", "collapsed_sum_comparisons",
    )), f"{projectile}/{file_name}/MAT={target['mat']}: comparison accounting")
    require(source["p18_violations"] == old["violations"], f"{projectile}/{file_name}/MAT={target['mat']}: P18 violations")
    require(sum(source["binary64_primary_counts"].values()) == source["comparisons"], f"{projectile}/{file_name}: primary accounting")
    require(sum(source["p18_violations_by_binary64_primary"].values()) == source["p18_violations"], f"{projectile}/{file_name}: P18 class accounting")
    fractions = source["mf9_fractions"]
    require(fractions["individual_comparisons"] + fractions["sum_comparisons"] == fractions["comparisons"], f"{projectile}/{file_name}: MF9 fraction accounting")
    require(sum(fractions["binary64_primary_counts"].values()) == fractions["comparisons"], f"{projectile}/{file_name}: MF9 class accounting")
    runtime = target["runtime"]
    require(runtime["unchanged"] + runtime["standard_compatible_excesses"] + runtime["outside_standard_excesses"] == runtime["group_comparisons"], f"{projectile}/{file_name}: runtime accounting")
    require(target["pass"] is True, f"{projectile}/{file_name}/MAT={target['mat']}: incomplete target audit")
    for example in source["boundary_examples"]:
        validate_source_example(example, source["contracts"]["issues"])
    for example in fractions["boundary_examples"]:
        validate_source_example(example, source["contracts"]["issues"])
    for example in runtime["boundary_examples"]:
        validate_runtime_example(example)


def aggregate_projectile(projectile: str, p18: dict[str, Any]) -> dict[str, Any]:
    config = CORPORA[projectile]
    checkpoint = CHECKPOINT_ROOT / str(config["checkpoint"])
    decimal_path = CHECKPOINT_ROOT / f"{projectile}-decimal.jsonl"
    predecessor_path = P18_CHECKPOINT_ROOT / str(config["predecessor"])
    require(checkpoint.is_file(), f"missing {projectile} P18b checkpoint")
    require(decimal_path.is_file(), f"missing {projectile} decimal checkpoint")
    require(predecessor_path.is_file(), f"missing {projectile} P18 checkpoint")
    require(sha256(predecessor_path) == config["predecessor_sha256"], f"{projectile} P18 checkpoint hash")
    manifest = expected_manifest(projectile)
    new_rows = iter_jsonl(checkpoint)
    old_rows = iter_jsonl(predecessor_path)
    decimal_rows = iter_jsonl(decimal_path)
    header = next(new_rows)
    old_header = next(old_rows)
    decimal_header = next(decimal_rows)
    validate_header(projectile, header)
    require(old_header.get("kind") == "header" and old_header.get("projectile") == projectile, f"{projectile} P18 header")
    require(
        decimal_header.get("kind") == "header"
        and decimal_header.get("schema") == "actinv-p18b-decimal-corpus-1",
        f"{projectile} decimal checkpoint header",
    )
    require(
        decimal_header.get("probe_checkpoint_sha256") == sha256(checkpoint),
        f"{projectile} decimal checkpoint binds this probe checkpoint",
    )
    require(
        decimal_header.get("precision_digits") == [80, 120],
        f"{projectile} decimal precision digits",
    )

    source_counts: Counter[str] = Counter()
    fraction_counts: Counter[str] = Counter()
    contract_counts: Counter[str] = Counter()
    runtime_counts: Counter[str] = Counter()
    source_classes: Counter[str] = Counter()
    p18_classes: Counter[str] = Counter()
    fraction_classes: Counter[str] = Counter()
    files = 0
    targets = 0
    seen: set[str] = set()
    accounting = hashlib.sha256()
    source_examples: list[dict[str, Any]] = []
    fraction_examples: list[dict[str, Any]] = []
    runtime_examples: list[dict[str, Any]] = []
    contract_examples: list[dict[str, Any]] = []

    binary64_source_classes: Counter[str] = Counter()
    binary64_fraction_classes: Counter[str] = Counter()
    reclassified_source = 0
    reclassified_fractions = 0

    while True:
        new = next(new_rows, None)
        old = next(old_rows, None)
        dec = next(decimal_rows, None)
        require(
            (new is None) == (old is None) == (dec is None),
            f"{projectile} checkpoint row count differs",
        )
        if new is None:
            break
        require(new.get("kind") == old.get("kind") == dec.get("kind") == "file", f"{projectile} row kind")
        file_name = new.get("file")
        require(file_name == old.get("file") == dec.get("file") and file_name in manifest, f"{projectile} file order/identity")
        require(file_name not in seen, f"{projectile} duplicate file {file_name}")
        seen.add(file_name)
        identity = manifest[file_name]
        require(new.get("source_sha256") == old.get("source_sha256") == dec.get("source_sha256") == identity["sha256"], f"{projectile}/{file_name}: source hash")
        require(new.get("bytes") == old.get("bytes") == identity["bytes"], f"{projectile}/{file_name}: source bytes")
        old_targets = {row["mat"]: row for row in old["targets"]}
        dec_targets = {row["mat"]: row for row in dec["targets"]}
        require(len(old_targets) == len(old["targets"]), f"{projectile}/{file_name}: duplicate MAT")
        require({row["mat"] for row in new["targets"]} == set(old_targets) == set(dec_targets), f"{projectile}/{file_name}: target identity")
        require(new["pass"] is True and dec["pass"] is True, f"{projectile}/{file_name}: incomplete file audit")
        files += 1
        targets += len(new["targets"])
        for target in new["targets"]:
            validate_target_predecessor(projectile, file_name, target, old_targets[target["mat"]])
            source = target["source"]
            fractions = source["mf9_fractions"]
            contracts = source["contracts"]
            runtime = target["runtime"]
            exact = dec_targets[target["mat"]]
            exact_source = exact["source"]
            exact_fractions = exact["mf9_fractions"]
            require(exact["precision_digits"] == [80, 120] and exact["pass"] is True, f"{projectile}/{file_name}: incomplete decimal audit")
            # The oracle consumed the frozen bitmaps bit-by-bit; its totals must
            # match the probe's comparison and violation accounting exactly.
            require(exact_source["comparisons"] == source["comparisons"], f"{projectile}/{file_name}: source comparison count")
            require(exact_source["p18_violations"] == source["p18_violations"], f"{projectile}/{file_name}: source P18 count")
            require(exact_fractions["comparisons"] == fractions["comparisons"], f"{projectile}/{file_name}: fraction comparison count")
            require(exact_fractions["p18_violations"] == fractions["p18_violations"], f"{projectile}/{file_name}: fraction P18 count")
            require(sum(exact_source["primary_counts"].values()) == source["comparisons"], f"{projectile}/{file_name}: exact source accounting")
            require(sum(exact_fractions["primary_counts"].values()) == fractions["comparisons"], f"{projectile}/{file_name}: exact fraction accounting")
            reclassified_source += class_changes(
                source["binary64_primary_counts"], exact_source["primary_counts"]
            )
            reclassified_fractions += class_changes(
                fractions["binary64_primary_counts"], exact_fractions["primary_counts"]
            )
            add_counts(source_counts, source, SOURCE_COUNT_FIELDS)
            add_counts(fraction_counts, fractions, FRACTION_COUNT_FIELDS)
            add_counts(contract_counts, contracts, CONTRACT_COUNT_FIELDS)
            add_counts(runtime_counts, runtime, RUNTIME_COUNT_FIELDS)
            binary64_source_classes.update(source["binary64_primary_counts"])
            source_classes.update(exact_source["primary_counts"])
            p18_classes.update(exact_source["p18_violations_by_primary"])
            binary64_fraction_classes.update(fractions["binary64_primary_counts"])
            fraction_classes.update(exact_fractions["primary_counts"])
            source_counts["exact_excesses_outside_contracts"] += exact_source["exact_excesses_outside_contracts"]
            source_counts["standard_compatible_excesses_outside_contracts"] += exact_source["standard_compatible_excesses_outside_contracts"]
            fraction_counts["exact_excesses_outside_contracts"] += exact_fractions["exact_excesses_outside_contracts"]
            fraction_counts["standard_compatible_excesses_outside_contracts"] += exact_fractions["standard_compatible_excesses_outside_contracts"]
            context = {"file": file_name, "source_sha256": new["source_sha256"], "mat": target["mat"]}
            source_examples.extend({**context, **row} for row in source["boundary_examples"])
            fraction_examples.extend({**context, **row} for row in fractions["boundary_examples"])
            runtime_examples.extend({**context, **row} for row in runtime["boundary_examples"])
            contract_examples.extend({**context, **row} for row in contracts["issues"])
            accounting.update(canonical({
                "file": file_name,
                "source_sha256": new["source_sha256"],
                "mat": target["mat"],
                "source_sha256_accounting": source["comparison_sha256"],
                "fraction_sha256_accounting": fractions["comparison_sha256"],
                "runtime_sha256_accounting": runtime["comparison_sha256"],
            }))
            accounting.update(b"\n")

    require(seen == set(manifest), f"{projectile} complete source inventory")
    require(files == targets == 2_850, f"{projectile} file/target count")
    old_summary = p18["corpora"][projectile]
    require(source_counts["comparisons"] == sum(old_summary[field] for field in (
        "pointwise_individual_comparisons", "pointwise_sum_comparisons",
        "collapsed_individual_comparisons", "collapsed_sum_comparisons",
    )), f"{projectile} P18 comparison total")
    require(source_counts["p18_violations"] == old_summary["violations"], f"{projectile} P18 violation total")
    examples = {
        "source": deterministic_examples(
            source_examples, seed=f"P18b-G2-source-{projectile}"
        ),
        "mf9_fraction": deterministic_examples(
            fraction_examples, seed=f"P18b-G2-fraction-{projectile}"
        ),
        "runtime": deterministic_examples(
            runtime_examples, seed=f"P18b-G2-runtime-{projectile}"
        ),
        "contracts": deterministic_examples(
            contract_examples, seed=f"P18b-G2-contract-{projectile}"
        ),
    }
    group_path = ROOT / (
        "crates/actinv-data/data/fispact_709_groups.json"
        if projectile == "neutron"
        else "crates/actinv-data/data/fispact_162_groups.json"
    )
    decimal_oracle = verify_examples(
        data_directory=DATA_ROOT / str(config["directory"]),
        group_path=group_path,
        source_examples=examples["source"],
        fraction_examples=examples["mf9_fraction"],
        contract_examples=examples["contracts"],
    )
    return {
        "files": files,
        "targets": targets,
        "manifest_sha256": config["manifest_sha256"],
        "predecessor_checkpoint_sha256": config["predecessor_sha256"],
        "checkpoint_sha256": sha256(checkpoint),
        "checkpoint_bytes": checkpoint.stat().st_size,
        "decimal_checkpoint_sha256": sha256(decimal_path),
        "source": {
            **dict(sorted(source_counts.items())),
            # The authoritative classes are the 80/120-digit exact ones; the
            # probe's binary64 labels are retained as the preliminary record.
            "primary_counts": dict(sorted(source_classes.items())),
            "p18_violations_by_primary": dict(sorted(p18_classes.items())),
            "binary64_primary_counts": dict(sorted(binary64_source_classes.items())),
            "binary64_reclassified": reclassified_source,
        },
        "mf9_fractions": {
            **dict(sorted(fraction_counts.items())),
            "primary_counts": dict(sorted(fraction_classes.items())),
            "binary64_primary_counts": dict(sorted(binary64_fraction_classes.items())),
            "binary64_reclassified": reclassified_fractions,
        },
        "contracts": dict(sorted(contract_counts.items())),
        "runtime": dict(sorted(runtime_counts.items())),
        "accounting_sha256": accounting.hexdigest(),
        "examples": examples,
        "decimal_oracle": decimal_oracle,
        "pass": True,
    }


def normalize_report(text: str) -> str:
    kept = []
    in_backtrace = False
    for line in text.replace("\r\n", "\n").splitlines():
        if line.startswith("Failed to create stream fd:"):
            continue
        if line == "Error termination. Backtrace:":
            kept.append("Error termination. Backtrace: <NORMALIZED>")
            in_backtrace = True
            continue
        if in_backtrace:
            continue
        line = re.sub(
            r"^(At line [0-9]+ of file) .*[/\\](checkr|fizcon)\.f$",
            r"\1 <PINNED>/\2.f",
            line,
        )
        if "Run on " in line:
            line = re.sub(r"Run on .*?$", "Run on <NORMALIZED>", line)
        kept.append(line.rstrip())
    return "\n".join(kept).strip() + "\n"


def compile_corpus_tools() -> dict[str, Any]:
    require(COMPILER.is_file(), f"missing user-space Fortran compiler {COMPILER}")
    require(IAEA_ROOT.is_dir(), f"missing IAEA utility checkout {IAEA_ROOT}")
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=IAEA_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    require(revision.returncode == 0, f"could not identify IAEA checkout: {revision.stderr}")
    require(revision.stdout.strip() == IAEA_COMMIT, "IAEA utility checkout commit changed")
    for relative, expected in IAEA_SOURCES.items():
        require(sha256(IAEA_ROOT / relative) == expected, f"IAEA source changed: {relative}")
    version = subprocess.run(
        [str(COMPILER), "--version"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    require(version.returncode == 0, f"could not identify Fortran compiler: {version.stderr}")
    require(
        version.stdout.splitlines()[0]
        == "GNU Fortran (Ubuntu 15.2.0-16ubuntu1) 15.2.0",
        "Fortran compiler version changed",
    )
    CORPUS_TOOL_ROOT.mkdir(parents=True, exist_ok=True)
    builds = {
        "checkr_official": (
            OFFICIAL_CHECKR,
            IAEA_ROOT / "checkr/checkr.f",
            [f"-B{COMPILER_PREFIX}", "-std=legacy", "-O3"],
        ),
        "checkr_compatible": (
            COMPATIBLE_CHECKR,
            IAEA_ROOT / "checkr/checkr.f",
            [f"-B{COMPILER_PREFIX}", "-O3"],
        ),
        "fizcon_official": (
            OFFICIAL_FIZCON,
            IAEA_ROOT / "fizcon/fizcon.f",
            [f"-B{COMPILER_PREFIX}", "-O3"],
        ),
    }
    for name, (binary, source, flags) in builds.items():
        expected = EXPECTED_CORPUS_BINARIES[name]
        if not binary.is_file() or sha256(binary) != expected:
            build = subprocess.run(
                [str(COMPILER), *flags, "-o", str(binary), str(source)],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=600,
                check=False,
            )
            require(
                build.returncode == 0,
                f"could not compile {name}: {(build.stdout + build.stderr)[-2000:]}",
            )
        require(sha256(binary) == expected, f"{name} binary hash")
    return {
        "compiler": version.stdout.splitlines()[0],
        "compiler_prefix": "/usr/lib/gcc/x86_64-linux-gnu/15/",
        "iaea_commit": IAEA_COMMIT,
        "source_sha256": IAEA_SOURCES,
        "builds": {
            name: {
                "binary_sha256": EXPECTED_CORPUS_BINARIES[name],
                "flags": [
                    "-B<SYSTEM_GCC_15_PREFIX>" if flag.startswith("-B") else flag
                    for flag in flags
                ],
            }
            for name, (_, _, flags) in builds.items()
        },
    }


def limited_run(
    binary: Path, cwd: Path, input_text: str, timeout: int = 900
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(binary)],
        cwd=cwd,
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
        preexec_fn=lambda: resource.setrlimit(
            resource.RLIMIT_AS, (ADDRESS_SPACE_BYTES, ADDRESS_SPACE_BYTES)
        ),
    )


def normalized_tool_report(
    completed: subprocess.CompletedProcess[str], output: Path
) -> bytes:
    report = output.read_text(errors="replace") if output.is_file() else ""
    return normalize_report(completed.stdout + "\n" + completed.stderr + "\n" + report).encode()


def mf1_text_contains_comma(source: Path) -> bool:
    with source.open("rb") as stream:
        for raw in stream:
            line = raw.rstrip(b"\r\n")
            if len(line) < 75:
                continue
            try:
                mf = int(line[70:72])
                mt = int(line[72:75])
            except ValueError:
                continue
            if mf == 1 and mt == 451 and b"," in line[:66]:
                return True
    return False


def official_sample_rows() -> list[dict[str, Any]]:
    g0 = json.loads(G0.read_text())
    require(g0["schema"] == G0_SCHEMA, "G0 schema")
    rows = g0["official_checker_sample"]["rows"]
    require(len(rows) == 245, "official sample count")
    return rows


def write_gzip(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(payload, compresslevel=9, mtime=0))


def run_official_sample(limit: int | None = None) -> None:
    compile_corpus_tools()
    rows = official_sample_rows()
    stop = len(rows) if limit is None else min(len(rows), limit)
    completed: list[dict[str, Any]] = []
    if OFFICIAL_CHECKPOINT.is_file():
        completed = list(iter_jsonl(OFFICIAL_CHECKPOINT))
    require(len(completed) <= len(rows), "official checkpoint has excess rows")
    for index, expected in enumerate(rows[: len(completed)]):
        row = completed[index]
        require((row["projectile"], row["file"], row["source_sha256"]) == (expected["projectile"], expected["file"], expected["source_sha256"]), "official checkpoint order")
    OFFICIAL_CHECKPOINT.parent.mkdir(parents=True, exist_ok=True)
    with OFFICIAL_CHECKPOINT.open("a", encoding="utf-8") as checkpoint:
        for index, sample in enumerate(rows[len(completed) : stop], len(completed)):
            projectile = sample["projectile"]
            source = DATA_ROOT / str(CORPORA[projectile]["directory"]) / sample["file"]
            require(sha256(source) == sample["source_sha256"], f"official sample source {projectile}/{sample['file']}")
            work = OFFICIAL_OUTPUT_ROOT / f"{index:03d}-{projectile}-{Path(sample['file']).stem}"
            work.mkdir(parents=True, exist_ok=True)
            local_source = work / "source.endf"
            if not local_source.exists():
                local_source.symlink_to(source)
            outputs = {
                "checkr_official": work / "checkr-official.out",
                "checkr_compatible": work / "checkr-compatible.out",
                "fizcon_official": work / "fizcon-official.out",
            }
            for output in outputs.values():
                output.unlink(missing_ok=True)
            inputs = {
                "checkr_official": "source.endf\ncheckr-official.out\nY\nDONE\n",
                "checkr_compatible": "source.endf\ncheckr-compatible.out\nY\nDONE\n",
                "fizcon_official": (
                    "source.endf\nfizcon-official.out\nN\n\nN\nY\n0.001\nDONE\n"
                ),
            }
            runs = {
                "checkr_official": limited_run(
                    OFFICIAL_CHECKR, work, inputs["checkr_official"]
                ),
                "checkr_compatible": limited_run(
                    COMPATIBLE_CHECKR, work, inputs["checkr_compatible"]
                ),
                "fizcon_official": limited_run(
                    OFFICIAL_FIZCON, work, inputs["fizcon_official"]
                ),
            }
            reports = {
                name: normalized_tool_report(runs[name], output)
                for name, output in outputs.items()
            }
            for name, report in reports.items():
                write_gzip(work / f"{name}.normalized.txt.gz", report)
            combined = (
                reports["checkr_compatible"] + b"\n" + reports["fizcon_official"]
            ).decode(errors="replace")
            legacy_report = reports["checkr_official"].decode(errors="replace")
            comma_in_mf1_text = mf1_text_contains_comma(source)
            known_legacy_comma_failure = (
                runs["checkr_official"].returncode != 0
                and comma_in_mf1_text
                and "Fortran runtime error: End of file" in legacy_report
                and "At line 5712 of file <PINNED>/checkr.f" in legacy_report
            )
            legacy_outcome_explained = (
                runs["checkr_official"].returncode == 0
                or known_legacy_comma_failure
            )
            read_failures = (
                "ERROR READING",
                "UNEXPECTED END OF FILE",
                "INVALID RECORD IDENTIFICATION",
            )
            read_structure_ok = (
                runs["checkr_compatible"].returncode == 0
                and runs["fizcon_official"].returncode == 0
                and not any(marker in combined for marker in read_failures)
            )
            row = {
                "projectile": projectile,
                "file": sample["file"],
                "source_sha256": sample["source_sha256"],
                "reasons": sample["reasons"],
                "comma_in_mf1_text": comma_in_mf1_text,
                "tools": {
                    name: {
                        "returncode": runs[name].returncode,
                        "input_sha256": sha256_bytes(inputs[name].encode()),
                        "normalized_bytes": len(reports[name]),
                        "normalized_sha256": sha256_bytes(reports[name]),
                    }
                    for name in runs
                },
                "known_gfortran_legacy_comma_failure": known_legacy_comma_failure,
                "legacy_checkr_outcome_explained": legacy_outcome_explained,
                "mf9_sum_messages": combined.count("SUM OF MULTIPLICITIES EXCEEDED UNITY"),
                "mf10_sum_messages": combined.count("SUM OF CROSS SECTIONS EXCEEDED FILE 3"),
                "read_structure_ok": read_structure_ok,
                "pass": read_structure_ok and legacy_outcome_explained,
            }
            checkpoint.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
            checkpoint.flush()
            os.fsync(checkpoint.fileno())
            print(f"official {index + 1}/{len(rows)} {projectile}/{sample['file']}", flush=True)


def aggregate_official() -> dict[str, Any]:
    require(OFFICIAL_CHECKPOINT.is_file(), "missing official checker checkpoint")
    expected = official_sample_rows()
    observed = list(iter_jsonl(OFFICIAL_CHECKPOINT))
    require(len(observed) == len(expected) == 245, "official checker complete sample")
    counts: Counter[str] = Counter()
    by_projectile: dict[str, Counter[str]] = {name: Counter() for name in CORPORA}
    for source, row in zip(expected, observed, strict=True):
        projectile = row["projectile"]
        require((projectile, row["file"], row["source_sha256"]) == (source["projectile"], source["file"], source["source_sha256"]), "official sample identity")
        require(row["read_structure_ok"] is True, f"official source read {projectile}/{row['file']}")
        require(row["pass"] is True, f"official outcome unexplained {projectile}/{row['file']}")
        tools = row["tools"]
        require(
            tools["checkr_compatible"]["returncode"] == 0,
            f"compatible CHECKR failed {projectile}/{row['file']}",
        )
        require(
            tools["fizcon_official"]["returncode"] == 0,
            f"official FIZCON failed {projectile}/{row['file']}",
        )
        if tools["checkr_official"]["returncode"] == 0:
            counts["official_checkr_successes"] += 1
            by_projectile[projectile]["official_checkr_successes"] += 1
        else:
            require(
                row["known_gfortran_legacy_comma_failure"] is True,
                f"unexplained official CHECKR failure {projectile}/{row['file']}",
            )
            counts["official_checkr_legacy_comma_failures"] += 1
            by_projectile[projectile]["official_checkr_legacy_comma_failures"] += 1
        for field in ("mf9_sum_messages", "mf10_sum_messages"):
            counts[field] += row[field]
            by_projectile[projectile][field] += row[field]
        counts["files"] += 1
        by_projectile[projectile]["files"] += 1
    return {
        "files": counts["files"],
        "checkpoint_sha256": sha256(OFFICIAL_CHECKPOINT),
        "checkpoint_bytes": OFFICIAL_CHECKPOINT.stat().st_size,
        "tool_builds": {
            "compiler": "GNU Fortran (Ubuntu 15.2.0-16ubuntu1) 15.2.0",
            "iaea_commit": IAEA_COMMIT,
            "source_sha256": IAEA_SOURCES,
            "checkr_official": {
                "flags": ["-B<SYSTEM_GCC_15_PREFIX>", "-std=legacy", "-O3"],
                "binary_sha256": EXPECTED_CORPUS_BINARIES["checkr_official"],
            },
            "checkr_compatible": {
                "flags": ["-B<SYSTEM_GCC_15_PREFIX>", "-O3"],
                "binary_sha256": EXPECTED_CORPUS_BINARIES["checkr_compatible"],
                "purpose": (
                    "unchanged source without GNU legacy comma-input semantics"
                ),
            },
            "fizcon_official": {
                "flags": ["-B<SYSTEM_GCC_15_PREFIX>", "-O3"],
                "binary_sha256": EXPECTED_CORPUS_BINARIES["fizcon_official"],
            },
        },
        "official_checkr_successes": counts["official_checkr_successes"],
        "official_checkr_legacy_comma_failures": counts[
            "official_checkr_legacy_comma_failures"
        ],
        "mf9_sum_messages": counts["mf9_sum_messages"],
        "mf10_sum_messages": counts["mf10_sum_messages"],
        "by_projectile": {name: dict(sorted(values.items())) for name, values in by_projectile.items()},
        "all_read_structure_ok": True,
        "all_legacy_checkr_outcomes_explained": True,
        "pass": True,
    }


def derive() -> dict[str, Any]:
    require(sha256(PROTOCOL) == PROTOCOL_SHA256, "P18b protocol hash")
    require(sha256(G0) == G0_SHA256, "P18b G0 hash")
    require(sha256(G1) == G1_SHA256, "P18b G1 hash")
    require(sha256(G1_CHECK) == G1_CHECK_SHA256, "P18b G1 checker hash")
    require(sha256(P18_EVIDENCE) == P18_EVIDENCE_SHA256, "P18 predecessor evidence hash")
    p18 = json.loads(P18_EVIDENCE.read_text())
    corpora = {name: aggregate_projectile(name, p18) for name in CORPORA}
    result = {
        "schema": "actinv-p18b-g2-corpus-classification-1",
        "gate": "P18b-G2",
        "protocol_sha256": PROTOCOL_SHA256,
        "g0_sha256": G0_SHA256,
        "g1_sha256": G1_SHA256,
        "g1_check_sha256": G1_CHECK_SHA256,
        "p18_predecessor_evidence_sha256": P18_EVIDENCE_SHA256,
        "control_source_sha256": sha256(Path(__file__)),
        "decimal_control_source_sha256": sha256(DECIMAL_CONTROL),
        "probe_source_sha256": sha256(PROBE),
        "measurement_values_read": False,
        "heldout_values_read": False,
        "corpora": corpora,
        "official_checker_sample": aggregate_official(),
        "checks": {
            "complete_four_corpus_inventory": all(row["pass"] for row in corpora.values()),
            "p18_inventory_and_violations_reproduced": True,
            "mutually_exclusive_source_classification": True,
            "mf9_direct_fraction_classification": True,
            "runtime_comparator_classification": True,
            "decimal_80_120_boundary_agreement": all(
                row["decimal_oracle"]["pass"] for row in corpora.values()
            ),
            "official_checker_sample_complete": True,
            "measurement_quarantine": True,
        },
        "audit_complete": True,
    }
    result["pass"] = all(result["checks"].values())
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--run-official", action="store_true")
    parser.add_argument("--official-limit", type=int)
    arguments = parser.parse_args()
    if arguments.run_official:
        require(arguments.official_limit is None or arguments.official_limit > 0, "official limit must be positive")
        run_official_sample(arguments.official_limit)
        return 0
    result = derive()
    if arguments.no_write:
        require(RESULT.is_file(), f"missing committed result {RESULT}")
        require(json.loads(RESULT.read_text()) == result, "committed G2 result is not reproducible")
    else:
        RESULT.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")
    print(json.dumps(result, indent=1, sort_keys=True))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
