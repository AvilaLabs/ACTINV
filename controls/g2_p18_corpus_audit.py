#!/usr/bin/env python3
"""Aggregate the bounded P18-G2 MF8/9/10 corpus checkpoints without reading measurements."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import gzip
import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Iterator


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results/g2_p18_corpus_audit.json"
CHANGED_IDENTITIES = ROOT / "results/g2_p18_changed_identities.json.gz"
CHECKPOINT_ROOT = Path(
    os.environ.get("ACTINV_P18_G2_CHECKPOINT_ROOT", ROOT / "target/p18-g2")
)
DATA_ROOT = Path(
    os.environ.get("ACTINV_P18_DATA_ROOT", "/home/connoravila/nuclear-data/tendl-2025")
)
PROTOCOL = ROOT / "protocols/ACTINV-P18_PROTOCOL.md"
AMENDMENT = ROOT / "protocols/ACTINV-P18_AMENDMENT_1.md"
G0 = ROOT / "results/g0_p18_seal.json"
G1 = ROOT / "results/g1_p18_state_identity.json"

PROTOCOL_SHA256 = "002afb038bbbf1ad0bdb34149971f8d3f33a3e2590c6d04ced87bb5ada046e09"
AMENDMENT_SHA256 = "8eb3f3bc657a49ebeff7cc5d7ca124cb4e4debbf094fee9d6417c01f740aa9e0"
G0_SHA256 = "e419e8eeb506c28440744e8a3ceb6922f1d066b8d673f534ce0567dc59f82af3"
G1_SHA256 = "e7f51a6f23523956c349a634253ff86e0d986c482a5416caa26a3025156bbeb8"

CORPORA = {
    "neutron": {
        "code": "n-working",
        "directory": "files/n-working",
        "manifest": "staging/TENDL-n-working.manifest.json",
        "manifest_sha256": "a6d17f996153d2671c0c51bfb6303e2a87a5af03e0696bfb34d668a31dbfb2a2",
        "frozen_manifest": "staging/TENDL-n.manifest.json",
        "frozen_manifest_sha256": "b578ab395c6c71d7727dfb0513e88effd96692862664a6279802638531239b67",
        "checkpoint": "neutron.jsonl",
        "profile": {"wall_seconds": 48.84, "peak_rss_bytes": 13_824 * 1024},
    },
    "proton": {
        "code": "p",
        "directory": "files/p",
        "manifest": "staging/TENDL-p.manifest.json",
        "manifest_sha256": "98a8bd55784c326b8696de91f494111326378e776a975a512e59806a8c9ec2ef",
        "checkpoint": "proton.jsonl",
        "profile": {"wall_seconds": 29.80, "peak_rss_bytes": 13_848 * 1024},
    },
    "deuteron": {
        "code": "d",
        "directory": "files/d",
        "manifest": "staging/TENDL-d.manifest.json",
        "manifest_sha256": "afb52c55b2a1babca998cc3d8af0f7004c64f85d160e3c5aabf16a05839355d9",
        "checkpoint": "deuteron.jsonl",
        "profile": {"wall_seconds": 37.99, "peak_rss_bytes": 19_308 * 1024},
    },
    "alpha": {
        "code": "a",
        "directory": "files/a",
        "manifest": "staging/TENDL-a.manifest.json",
        "manifest_sha256": "e3aaf11e60c46b43361796c2c297bab4fb714fe57ab26a315594f2b4799dfdbf",
        "checkpoint": "alpha.jsonl",
        "profile": {"wall_seconds": 21.15, "peak_rss_bytes": 12_916 * 1024},
    },
}

COMPARISON_FIELDS = (
    "pointwise_individual_comparisons",
    "pointwise_sum_comparisons",
    "collapsed_individual_comparisons",
    "collapsed_sum_comparisons",
    "mf9_comparisons",
    "mf10_comparisons",
)
VIOLATION_FIELDS = (
    "pointwise_individual_violations",
    "pointwise_sum_violations",
    "collapsed_individual_violations",
    "collapsed_sum_violations",
    "mf9_violations",
    "mf10_violations",
)
PREDECLARED_DECISIONS = {
    "total_fission_sentinel",
    "non_inventory_zap0",
    "ground_lfs0",
    "ground_lfs0_without_catalog",
    "catalog_excitation_match",
    "unspecified_lfs98_to_leakage",
    "missing_excitation_to_leakage",
    "no_catalog_excitation_match_to_leakage",
    "ambiguous_catalog_excitation_match",
    "ground_excitation_conflict",
    "negative_q_excitation_conflict",
    "mf8_q_excitation_conflict",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def excitation_tolerance(left_ev: float, right_ev: float) -> float:
    return max(1.0, 5e-6 * max(abs(left_ev), abs(right_ev)))


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def expected_files(projectile: str) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    config = CORPORA[projectile]
    manifest_path = DATA_ROOT / str(config["manifest"])
    require(sha256(manifest_path) == config["manifest_sha256"], f"{projectile} manifest hash")
    manifest = json.loads(manifest_path.read_text())
    rows = {}
    for row in manifest["files"]:
        digest = row.get("working_sha256", row.get("sha256"))
        require(isinstance(digest, str) and len(digest) == 64, f"{projectile} file digest")
        rows[row["name"]] = {"sha256": digest, "bytes": int(row["bytes"])}
    require(len(rows) == 2_850, f"{projectile} manifest target count")
    if projectile == "neutron":
        frozen = DATA_ROOT / str(config["frozen_manifest"])
        require(sha256(frozen) == config["frozen_manifest_sha256"], "frozen neutron manifest hash")
        require(manifest["regular_files"] == 2_850, "working neutron file count")
        require(manifest["byte_identical_files"] == 2_849, "working neutron identity count")
        require(len(manifest["repairs"]) == 2, "working neutron repair count")
    return rows, manifest


def iter_checkpoint(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            try:
                row = json.loads(line)
            except json.JSONDecodeError as error:
                raise AssertionError(f"{path.name}:{line_number}: invalid JSON: {error}") from error
            require(isinstance(row, dict), f"{path.name}:{line_number}: row is not an object")
            yield row


def checkpoint_header(projectile: str, path: Path) -> dict[str, Any]:
    rows = iter_checkpoint(path)
    try:
        header = next(rows)
    except StopIteration as error:
        raise AssertionError(f"empty checkpoint {path.name}") from error
    expected_groups = 709 if projectile == "neutron" else 162
    require(header.get("kind") == "header", f"{projectile} checkpoint header")
    require(header.get("schema") == "actinv-p18-corpus-probe-1", f"{projectile} probe schema")
    require(header.get("projectile") == projectile, f"{projectile} probe identity")
    require(header.get("groups") == expected_groups, f"{projectile} group count")
    require(header.get("start_after") is None, f"{projectile} checkpoint is not a complete pass")
    sources = {
        "probe": ROOT / "crates/actinv-data/src/bin/p18_corpus_probe.rs",
        "activation": ROOT / "crates/actinv-data/src/activation.rs",
        "groups": ROOT / "crates/actinv-data/src/groups.rs",
    }
    for name, source in sources.items():
        require(
            header.get(f"{name}_source_sha256") == sha256(source),
            f"{projectile} checkpoint used stale {name} source",
        )
    return header


def validate_checkpoints() -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    summaries: dict[str, Any] = {}
    observations: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for projectile, config in CORPORA.items():
        checkpoint = CHECKPOINT_ROOT / str(config["checkpoint"])
        require(checkpoint.is_file(), f"missing {projectile} checkpoint {checkpoint}")
        header = checkpoint_header(projectile, checkpoint)
        expected, manifest = expected_files(projectile)
        seen: set[str] = set()
        summary: dict[str, Any] = {
            "files": 0,
            "failed_files": 0,
            "targets": 0,
            "declarations": 0,
            "mf8_declarations": 0,
            "mf9_declarations": 0,
            "mf10_declarations": 0,
            "exact_duplicate_declarations": 0,
            "duplicate_or_descriptor_issues": 0,
            "missing_totals": 0,
            "violations": 0,
            **{field: 0 for field in COMPARISON_FIELDS},
            **{field: 0 for field in VIOLATION_FIELDS},
            "worst": None,
        }
        for row_number, row in enumerate(iter_checkpoint(checkpoint)):
            if row_number == 0:
                continue
            require(row.get("kind") == "file", f"{projectile} checkpoint row kind")
            name = row.get("file")
            require(isinstance(name, str) and name in expected, f"{projectile} unknown file {name!r}")
            require(name not in seen, f"{projectile} duplicate file {name}")
            seen.add(name)
            identity = expected[name]
            require(row.get("source_sha256") == identity["sha256"], f"{projectile}/{name} hash")
            require(row.get("bytes") == identity["bytes"], f"{projectile}/{name} bytes")
            targets = row.get("targets")
            require(isinstance(targets, list) and targets, f"{projectile}/{name} targets")
            summary["files"] += 1
            summary["failed_files"] += int(row.get("pass") is not True)
            for target in targets:
                summary["targets"] += 1
                observations[projectile].append(
                    {
                        "za": target["za"],
                        "lis": target["lis"],
                        "liso": target["liso"],
                        "elis_ev": target["elis_ev"],
                        "file": name,
                        "source_sha256": row["source_sha256"],
                    }
                )
                declarations = target["declarations"]
                summary["declarations"] += len(declarations)
                for declaration in declarations:
                    summary[f"mf{declaration['mf']}_declarations"] += 1
                summary["exact_duplicate_declarations"] += target[
                    "exact_duplicate_declarations"
                ]
                summary["duplicate_or_descriptor_issues"] += len(target["issues"])
                conservation = target["conservation"]
                summary["missing_totals"] += len(conservation["missing_totals"])
                summary["violations"] += conservation["violations"]
                for field in (*COMPARISON_FIELDS, *VIOLATION_FIELDS):
                    summary[field] += conservation[field]
                worst = conservation["worst"]
                if worst is not None and (
                    summary["worst"] is None
                    or worst["excess_b"] > summary["worst"]["excess_b"]
                ):
                    summary["worst"] = {
                        **worst,
                        "file": name,
                        "source_sha256": row["source_sha256"],
                        "target_za": target["za"],
                        "target_liso": target["liso"],
                    }
        require(seen == set(expected), f"{projectile} checkpoint file inventory")
        require(summary["files"] == 2_850, f"{projectile} checkpoint count")
        summary.update(
            {
                "manifest_sha256": config["manifest_sha256"],
                "file_manifest_sha256": manifest.get(
                    "working_file_manifest_sha256", manifest.get("file_manifest_sha256")
                ),
                "checkpoint_sha256": sha256(checkpoint),
                "checkpoint_bytes": checkpoint.stat().st_size,
                "probe_sources": {
                    key.removesuffix("_source_sha256"): value
                    for key, value in header.items()
                    if key.endswith("_source_sha256")
                },
                "profile": config["profile"],
                "inventory_pass": True,
            }
        )
        summaries[projectile] = summary
    return summaries, observations


def build_catalog(
    observations: list[dict[str, Any]], *, reverse: bool = False
) -> tuple[dict[int, list[dict[str, Any]]], list[str], dict[str, Any]]:
    grouped: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    source = reversed(observations) if reverse else observations
    issues: list[str] = []
    for observation in source:
        za = int(observation["za"])
        lis = int(observation["lis"])
        liso = int(observation["liso"])
        elis = float(observation["elis_ev"])
        if za <= 0 or lis < 0 or liso < 0 or elis < 0.0:
            issues.append(f"invalid target state ZA={za}/LIS={lis}/LISO={liso}/ELIS={elis}")
        if (liso == 0 and lis != 0) or (liso > 0 and (lis == 0 or liso > lis)):
            issues.append(f"inconsistent target ZA={za}/LIS={lis}/LISO={liso}")
        if liso == 0 and abs(elis) > excitation_tolerance(0.0, elis):
            issues.append(f"nonzero ground ELIS for ZA={za}: {elis}")
        grouped[(za, liso)].append(observation)
    catalog: dict[int, list[dict[str, Any]]] = defaultdict(list)
    duplicate_evaluations = 0
    for (za, liso), rows in sorted(grouped.items()):
        ordered = sorted(
            rows,
            key=lambda row: (
                float(row["elis_ev"]),
                int(row["lis"]),
                row["file"],
                row["source_sha256"],
            ),
        )
        low, high = ordered[0], ordered[-1]
        if abs(float(high["elis_ev"]) - float(low["elis_ev"])) > excitation_tolerance(
            float(low["elis_ev"]), float(high["elis_ev"])
        ):
            issues.append(
                f"conflicting catalog ZA={za}/LISO={liso}: "
                f"{low['elis_ev']} in {low['file']} vs {high['elis_ev']} in {high['file']}"
            )
        duplicate_evaluations += len(ordered) - 1
        catalog[za].append(
            {
                "za": za,
                "liso": liso,
                "lis": int(low["lis"]),
                "elis_ev": float(low["elis_ev"]),
                "file": low["file"],
                "source_sha256": low["source_sha256"],
                "evaluations": len(ordered),
            }
        )
    for states in catalog.values():
        states.sort(key=lambda state: state["liso"])
    normalized = [state for za in sorted(catalog) for state in catalog[za]]
    return dict(catalog), issues, {
        "nuclides": len(catalog),
        "states": len(normalized),
        "duplicate_evaluations": duplicate_evaluations,
        "sha256": hashlib.sha256(canonical_json(normalized)).hexdigest(),
    }


def declaration_excitation(declaration: dict[str, Any]) -> tuple[float | None, str | None]:
    if declaration["mf"] == 8:
        return float(declaration["elfs_ev"]), None
    qm = float(declaration["qm_ev"])
    qi = float(declaration["qi_ev"])
    derived = qm - qi
    if derived < -excitation_tolerance(0.0, derived):
        return None, "negative_q_excitation_conflict"
    derived = max(0.0, derived)
    for elfs in declaration["matching_mf8_elfs_ev"]:
        if abs(float(elfs) - derived) > excitation_tolerance(float(elfs), derived):
            return None, "mf8_q_excitation_conflict"
    return derived, None


def map_declaration(
    declaration: dict[str, Any], catalog: dict[int, list[dict[str, Any]]]
) -> dict[str, Any]:
    zap = int(declaration["zap"])
    raw_lfs = int(declaration["raw_lfs"])
    excitation, conflict = declaration_excitation(declaration)
    output: dict[str, Any] = {
        "decision": conflict,
        "canonical_liso": None,
        "excitation_ev": excitation,
        "catalog_elis_ev": None,
        "catalog_file": None,
        "catalog_source_sha256": None,
        "catalog_backed": False,
        "identity_backed": False,
        "identity_basis": None,
    }
    if conflict is not None:
        return output
    if zap == -1:
        output["decision"] = "total_fission_sentinel"
        return output
    if zap == 0:
        output["decision"] = "non_inventory_zap0"
        return output
    states = catalog.get(zap, [])
    if raw_lfs == 0:
        if excitation is not None and abs(excitation) > excitation_tolerance(0.0, excitation):
            output["decision"] = "ground_excitation_conflict"
            return output
        ground = next((state for state in states if state["liso"] == 0), None)
        output.update(
            {
                "decision": "ground_lfs0" if ground is not None else "ground_lfs0_without_catalog",
                "canonical_liso": 0,
                "catalog_elis_ev": None if ground is None else ground["elis_ev"],
                "catalog_file": None if ground is None else ground["file"],
                "catalog_source_sha256": None if ground is None else ground["source_sha256"],
                "catalog_backed": ground is not None,
                # ENDF defines LFS=0 as ground.  Unlike a positive state, this
                # identity does not require an excitation/catalog match.
                "identity_backed": True,
                "identity_basis": "endf_lfs0_ground",
            }
        )
        return output
    if raw_lfs == 98:
        output["decision"] = "unspecified_lfs98_to_leakage"
        return output
    if excitation is None:
        output["decision"] = "missing_excitation_to_leakage"
        return output
    matches = [
        state
        for state in states
        if state["liso"] > 0
        and abs(state["elis_ev"] - excitation)
        <= excitation_tolerance(state["elis_ev"], excitation)
    ]
    if not matches:
        output["decision"] = "no_catalog_excitation_match_to_leakage"
    elif len(matches) > 1:
        output["decision"] = "ambiguous_catalog_excitation_match"
    else:
        state = matches[0]
        output.update(
            {
                "decision": "catalog_excitation_match",
                "canonical_liso": state["liso"],
                "catalog_elis_ev": state["elis_ev"],
                "catalog_file": state["file"],
                "catalog_source_sha256": state["source_sha256"],
                "catalog_backed": True,
                "identity_backed": True,
                "identity_basis": "catalog_excitation_match",
            }
        )
    return output


def mapping_audit(
    projectile: str, catalog: dict[int, list[dict[str, Any]]]
) -> tuple[dict[str, Any], list[dict[str, Any]], set[str]]:
    checkpoint = CHECKPOINT_ROOT / str(CORPORA[projectile]["checkpoint"])
    accounting_hash = hashlib.sha256()
    decisions: Counter[str] = Counter()
    mf_counts: Counter[int] = Counter()
    declarations = 0
    accounted = 0
    emitted = 0
    catalog_backed_emitted = 0
    identity_backed_emitted = 0
    conflicts = 0
    conflict_examples: list[dict[str, Any]] = []
    changed: dict[tuple[Any, ...], dict[str, Any]] = {}
    changed_files: set[str] = set()
    all_files: set[str] = set()
    conservation_pass_files: set[str] = set()
    for row_number, row in enumerate(iter_checkpoint(checkpoint)):
        if row_number == 0:
            continue
        file_name = row["file"]
        all_files.add(file_name)
        if row.get("pass") is True:
            conservation_pass_files.add(file_name)
        for target in row["targets"]:
            levels: dict[tuple[int, int], set[int]] = defaultdict(set)
            for declaration in target["declarations"]:
                if declaration["zap"] > 0 and declaration["raw_lfs"] > 0:
                    levels[(declaration["mt"], declaration["zap"])].add(
                        declaration["raw_lfs"]
                    )
            old_maps = {
                identity: {raw: rank for rank, raw in enumerate(sorted(values), 1)}
                for identity, values in levels.items()
            }
            for declaration in target["declarations"]:
                declarations += 1
                mapping = map_declaration(declaration, catalog)
                decision = mapping["decision"]
                decisions[decision] += 1
                mf_counts[int(declaration["mf"])] += 1
                if decision in PREDECLARED_DECISIONS:
                    accounted += 1
                if mapping["canonical_liso"] is not None:
                    emitted += 1
                    catalog_backed_emitted += int(mapping["catalog_backed"])
                    identity_backed_emitted += int(mapping["identity_backed"])
                if decision in {
                    "ambiguous_catalog_excitation_match",
                    "ground_excitation_conflict",
                    "negative_q_excitation_conflict",
                    "mf8_q_excitation_conflict",
                }:
                    conflicts += 1
                    if len(conflict_examples) < 10:
                        conflict_examples.append(
                            {
                                "file": file_name,
                                "source_sha256": row["source_sha256"],
                                "target_za": target["za"],
                                "target_liso": target["liso"],
                                "mat": target["mat"],
                                "mf": declaration["mf"],
                                "mt": declaration["mt"],
                                "ordinal": declaration["ordinal"],
                                "zap": declaration["zap"],
                                "raw_lfs": declaration["raw_lfs"],
                                "qm_ev": declaration["qm_ev"],
                                "qi_ev": declaration["qi_ev"],
                                "matching_mf8_elfs_ev": declaration[
                                    "matching_mf8_elfs_ev"
                                ],
                                "decision": decision,
                            }
                        )
                identity = (int(declaration["mt"]), int(declaration["zap"]))
                raw = int(declaration["raw_lfs"])
                old_liso = 0 if raw == 0 else old_maps.get(identity, {}).get(raw)
                candidate = mapping["canonical_liso"]
                record = {
                    "projectile": projectile,
                    "file": file_name,
                    "source_sha256": row["source_sha256"],
                    "target_za": target["za"],
                    "target_liso": target["liso"],
                    "mat": target["mat"],
                    "mf": declaration["mf"],
                    "mt": declaration["mt"],
                    "ordinal": declaration["ordinal"],
                    "zap": declaration["zap"],
                    "lmf": declaration["lmf"],
                    "raw_lfs": raw,
                    "old_rank_liso": old_liso,
                    **mapping,
                }
                accounting_hash.update(canonical_json(record))
                accounting_hash.update(b"\n")
                if old_liso != candidate and old_liso is not None:
                    key = (
                        projectile,
                        file_name,
                        target["mat"],
                        declaration["mt"],
                        declaration["zap"],
                        raw,
                        old_liso,
                        candidate,
                        decision,
                    )
                    if key not in changed:
                        changed[key] = {
                            "projectile": projectile,
                            "file": file_name,
                            "source_sha256": row["source_sha256"],
                            "target_za": target["za"],
                            "target_liso": target["liso"],
                            "mat": target["mat"],
                            "mt": declaration["mt"],
                            "zap": declaration["zap"],
                            "raw_lfs": raw,
                            "old_rank_liso": old_liso,
                            "candidate_liso": candidate,
                            "decision": decision,
                            "excitation_ev": mapping["excitation_ev"],
                            "catalog_elis_ev": mapping["catalog_elis_ev"],
                            "catalog_file": mapping["catalog_file"],
                            "catalog_source_sha256": mapping["catalog_source_sha256"],
                            "mfs": [],
                            "declarations": 0,
                        }
                    changed[key]["declarations"] += 1
                    if declaration["mf"] not in changed[key]["mfs"]:
                        changed[key]["mfs"].append(declaration["mf"])
                    changed_files.add(file_name)
    changed_rows = sorted(
        changed.values(),
        key=lambda row: (
            row["projectile"],
            row["file"],
            row["mat"],
            row["mt"],
            row["zap"],
            row["raw_lfs"],
        ),
    )
    for row in changed_rows:
        row["mfs"].sort()
    unaffected_files = all_files - changed_files
    buildable_unaffected = unaffected_files & conservation_pass_files
    unaffected = sorted(
        buildable_unaffected,
        key=lambda name: hashlib.sha256(
            f"ACTINV-P18-G2-UNAFFECTED-v1\n{projectile}\n{name}".encode()
        ).hexdigest(),
    )[:16]
    return (
        {
            "declarations": declarations,
            "accounted": accounted,
            "emitted": emitted,
            "catalog_backed_emitted": catalog_backed_emitted,
            "identity_backed_emitted": identity_backed_emitted,
            "conflicts": conflicts,
            "conflict_examples": conflict_examples,
            "decision_counts": dict(sorted(decisions.items())),
            "mf_counts": {str(key): value for key, value in sorted(mf_counts.items())},
            "changed_identities": len(changed_rows),
            "changed_files": len(changed_files),
            "unaffected_files": len(unaffected_files),
            "conservation_pass_unaffected_files": len(buildable_unaffected),
            "unaffected_sample": unaffected,
            "accounting_sha256": accounting_hash.hexdigest(),
            "pass": accounted == declarations
            and conflicts == 0
            and emitted == identity_backed_emitted,
        },
        changed_rows,
        changed_files,
    )


def rust_fixture_tests() -> dict[str, Any]:
    cargo = os.environ.get("CARGO", str(Path.home() / ".cargo/bin/cargo"))
    completed = subprocess.run(
        [cargo, "test", "-p", "actinv-data", "builder::tests", "--", "--quiet"],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=300,
        check=False,
    )
    return {
        "command": "cargo test -p actinv-data builder::tests -- --quiet",
        "returncode": completed.returncode,
        "pass": completed.returncode == 0,
    }


def derive() -> tuple[dict[str, Any], bytes]:
    require(sha256(PROTOCOL) == PROTOCOL_SHA256, "P18 protocol hash")
    require(sha256(AMENDMENT) == AMENDMENT_SHA256, "P18 amendment hash")
    require(sha256(G0) == G0_SHA256, "P18 G0 hash")
    require(sha256(G1) == G1_SHA256, "P18 G1 hash")
    corpora, observations = validate_checkpoints()
    catalogs = {}
    mappings = {}
    all_changed = []
    for projectile in CORPORA:
        catalog, issues, catalog_summary = build_catalog(observations[projectile])
        reverse_catalog, reverse_issues, reverse_summary = build_catalog(
            observations[projectile], reverse=True
        )
        catalog_summary.update(
            {
                "issues": issues,
                "file_order_invariant": catalog == reverse_catalog
                and issues == reverse_issues
                and catalog_summary["sha256"] == reverse_summary["sha256"],
            }
        )
        catalogs[projectile] = catalog_summary
        mapping, changed, _ = mapping_audit(projectile, catalog)
        mappings[projectile] = mapping
        all_changed.extend(changed)
    all_changed.sort(
        key=lambda row: (
            row["projectile"],
            row["file"],
            row["mat"],
            row["mt"],
            row["zap"],
            row["raw_lfs"],
        )
    )
    changed_payload = canonical_json(all_changed)
    changed_compressed = gzip.compress(changed_payload, compresslevel=9, mtime=0)
    fixtures = rust_fixture_tests()
    conservation_pass = all(
        row["missing_totals"] == 0 and row["violations"] == 0
        for row in corpora.values()
    )
    declaration_pass = all(
        row["duplicate_or_descriptor_issues"] == 0 for row in corpora.values()
    )
    catalog_pass = all(
        not row["issues"] and row["file_order_invariant"] for row in catalogs.values()
    )
    mapping_pass = all(row["pass"] for row in mappings.values())
    complete = all(row["inventory_pass"] for row in corpora.values()) and fixtures["pass"]
    gate_pass = bool(
        complete and conservation_pass and declaration_pass and catalog_pass and mapping_pass
    )
    return {
        "schema": "actinv-p18-g2-corpus-audit-1",
        "gate": "P18-G2",
        "protocol_sha256": PROTOCOL_SHA256,
        "amendment_sha256": AMENDMENT_SHA256,
        "g0_sha256": G0_SHA256,
        "g1_sha256": G1_SHA256,
        "control_source_sha256": sha256(Path(__file__)),
        "implementation_source_sha256": {
            "activation.rs": sha256(ROOT / "crates/actinv-data/src/activation.rs"),
            "builder.rs": sha256(ROOT / "crates/actinv-data/src/builder.rs"),
            "groups.rs": sha256(ROOT / "crates/actinv-data/src/groups.rs"),
            "p18_corpus_probe.rs": sha256(
                ROOT / "crates/actinv-data/src/bin/p18_corpus_probe.rs"
            ),
        },
        "measurement_values_read": False,
        "heldout_values_read": False,
        "limits": {
            "pointwise_abs_b": 1e-12,
            "collapsed_abs_b": 1e-14,
            "relative": 5e-10,
            "process_virtual_memory_bytes": 12_000_000 * 1024,
            "single_array_bytes": 1 << 30,
        },
        "corpora": corpora,
        "catalogs": catalogs,
        "mappings": mappings,
        "changed_identities": {
            "artifact": str(CHANGED_IDENTITIES.relative_to(ROOT)),
            "count": len(all_changed),
            "canonical_sha256": hashlib.sha256(changed_payload).hexdigest(),
            "artifact_sha256": hashlib.sha256(changed_compressed).hexdigest(),
            "uncompressed_bytes": len(changed_payload),
            "compressed_bytes": len(changed_compressed),
        },
        "fixture_tests": fixtures,
        "checks": {
            "complete_inventory": complete,
            "duplicate_and_descriptor_consistency": declaration_pass,
            "catalog_consistency_and_file_order": catalog_pass,
            "declaration_accounting": mapping_pass,
            "state_partial_conservation": conservation_pass,
        },
        "audit_complete": complete,
        "gate_pass": gate_pass,
        "pass": gate_pass,
    }, changed_compressed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-write", action="store_true")
    arguments = parser.parse_args()
    result, changed_compressed = derive()
    if arguments.no_write:
        require(RESULT.is_file(), f"missing committed G2 result {RESULT}")
        require(
            CHANGED_IDENTITIES.is_file(),
            f"missing committed changed-identity artifact {CHANGED_IDENTITIES}",
        )
        require(
            CHANGED_IDENTITIES.read_bytes() == changed_compressed,
            "committed changed-identity artifact is not reproducible",
        )
        require(
            json.loads(RESULT.read_text()) == result,
            "committed G2 result is not reproducible",
        )
    else:
        CHANGED_IDENTITIES.write_bytes(changed_compressed)
        RESULT.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")
    display = {
        "schema": result["schema"],
        "checks": result["checks"],
        "corpora": {
            projectile: {
                "files": row["files"],
                "declarations": row["declarations"],
                "missing_totals": row["missing_totals"],
                "violations": row["violations"],
                "failed_files": row["failed_files"],
                "worst": row["worst"],
            }
            for projectile, row in result["corpora"].items()
        },
        "changed_identities": result["changed_identities"]["count"],
        "audit_complete": result["audit_complete"],
        "gate_pass": result["gate_pass"],
    }
    print(json.dumps(display, indent=1, sort_keys=True))
    # A completed, truthfully failed scientific audit is valid evidence. The independent checker decides the gate.
    return 0 if result["audit_complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
