#!/usr/bin/env python3
"""P18-G0 provenance verifier and dependent-value-free family seal."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "protocols/ACTINV-P18_PROTOCOL.md"
AMENDMENT = ROOT / "protocols/ACTINV-P18_AMENDMENT_1.md"
P17_HELDOUT = ROOT / "results/g5_p17_heldout.json"
CATALOG = ROOT / "crates/actinv-cli/data/actinv-data-catalog-v1.0.0.json"
P13_STAGE_EVIDENCE = ROOT / "results/g4_p13_release_stage.json"
SEAL = ROOT / "results/p18_family_seal.json"
RESULT = ROOT / "results/g0_p18_seal.json"
CHECKER = ROOT / "controls/check_g0_p18.py"

OPENING_COMMIT = "7a2d1f47b62155c0f7a22a4e0b9ec5d6e6730bc8"
P17_CLOSURE_RUN = 33_232_228_355
PROTOCOL_SHA256 = "002afb038bbbf1ad0bdb34149971f8d3f33a3e2590c6d04ced87bb5ada046e09"
AMENDMENT_SHA256 = "8eb3f3bc657a49ebeff7cc5d7ca124cb4e4debbf094fee9d6417c01f740aa9e0"
PAPER_SHA256 = "6cf1833e268b77177c647cc5504f08731e34ead00c8756b26bf0230a5b32b431"
SUPPLEMENT_SHA256 = "945e66f8904bb972662f5178e94e22a08ecb8006eefe1c2d9fbda66fe599763d"
MANUAL_SHA256 = "77a0fee413c3b1d5d74a161ed9fe7f77bbcbc58a654304851b7b2b400183d022"
PARTITION_SEED = "ACTINV-P18-HOLDOUT-v1"
EXPECTED_FAMILIES = 962
EXPECTED_ROWS = 12_313
DISCLOSED_LINES = [1, 140]

DATA_ROOT = Path(
    os.environ.get("ACTINV_P18_DATA_ROOT", "/home/connoravila/nuclear-data/tendl-2025")
)
RELEASE_STAGE = Path(
    os.environ.get("ACTINV_P18_RELEASE_STAGE", "/tmp/actinv-data-v1.0.0-stage")
)
SUPPLEMENT = Path(
    os.environ.get(
        "ACTINV_P18_SUPPLEMENT", "/tmp/actinv-p18-isomeric-ratios-supplement.txt"
    )
)
PAPER = Path(
    os.environ.get("ACTINV_P18_PAPER", "/tmp/actinv-p18-isomeric-ratios.pdf")
)
MANUAL = Path(os.environ.get("ACTINV_P18_ENDF_MANUAL", "/tmp/actinv-endf-manual-2024.pdf"))
DECAY_ROOT = Path(os.environ.get("ACTINV_P18_DECAY_ROOT", "/home/connoravila/nuclear-data"))

PROJECTILES = {
    (0, 0): "gamma",
    (0, 1): "neutron",
    (1, 1): "proton",
    (1, 2): "deuteron",
    (2, 3): "helion",
    (2, 4): "alpha",
}
SUPPORTED_PROJECTILES = {"neutron", "proton", "deuteron", "alpha"}
MANIFEST_CODE = {"neutron": "n", "proton": "p", "deuteron": "d", "alpha": "a"}
SYMBOLS = (
    "H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn "
    "Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce "
    "Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn "
    "Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr Rf Db Sg Bh Hs Mt Ds Rg Cn Nh Fl "
    "Mc Lv Ts Og"
).split()
RATIO_FORMS = {"G+M", "G+T", "M+T", "M/G", "G/M", "G/T", "M/T"}
REACTION = re.compile(r"^(\d+)([A-Z][a-z]?)\(([^()]*)\)(\d+)([A-Z][a-z]?)$")

ARCHIVE_HASHES = {
    "neutron": "e547527688506cbe09813364dcefa2aed11f474139bfa129d7cd4ca24fae21fa",
    "proton": "49340a03b0d9ac86598c6b710c0bc2ec0babd3fa0717a9ff1d75f042fccc5b0b",
    "deuteron": "34f459aea0b5ac9c40820c88d898618f926ec3b52858a5393e42d57707ec5f1c",
    "alpha": "25520f6eb42ce024c065f85255277ed169b2f826e9fc24f5d093c99d5c60e018",
}
MANIFEST_HASHES = {
    "neutron": "b578ab395c6c71d7727dfb0513e88effd96692862664a6279802638531239b67",
    "proton": "98a8bd55784c326b8696de91f494111326378e776a975a512e59806a8c9ec2ef",
    "deuteron": "afb52c55b2a1babca998cc3d8af0f7004c64f85d160e3c5aabf16a05839355d9",
    "alpha": "e3aaf11e60c46b43361796c2c297bab4fb714fe57ab26a315594f2b4799dfdbf",
}
LIBRARY_HASHES = {
    "neutron": "ec4c72bf598dc8ad3d533d9cfafdcf493e2d1f949a3e4db6251495659b68cc44",
    "proton": "0da7a35b37fd3b305ac2166ec092cdfb78123e76f8647d8808915e2c708d9790",
    "deuteron": "8050988981518cd63ac0c2ad76c6756370b154ea9f5a6d6435aa5f132b9d99ae",
    "alpha": "ead1141bfe07ec1a02055af014f8db0a49effe2fd60c29d181a505f7c6d10915",
}
LIBRARY_NAMES = {
    "neutron": "tendl-2025-neutron-709g.npz",
    "proton": "tendl-2025-proton-162g.npz",
    "deuteron": "tendl-2025-deuteron-162g.npz",
    "alpha": "tendl-2025-alpha-162g.npz",
}
DECAY_FILES = {
    "endfb_viii_0": (
        DECAY_ROOT / "endfb-viii.0-decay/bulk/endf-b-viii-0_decay.dat",
        "6f04cf009086c179021f243a58dadc2d5bb078de5ba39c4fe46ccad77d228ddb",
    ),
    "jeff_3_3": (
        DECAY_ROOT / "jeff-3.3-decay/bulk/jeff-3-3_decay.dat",
        "850b8b7f85f8d88b6ad826c4cd341aaaffabd525c8ecf3c588a0ad437bf5d123",
    ),
}
DISCLOSED_REACTIONS = {
    "86Kr(g,n)85Kr",
    "181Ta(g,n)180Ta",
    "35Cl(n,2n)34Cl",
    "39K(n,2n)38K",
    "45Sc(n,2n)44Sc",
}
PAPER_CASES = {"93Nb(n,a)90Y", "197Au(d,2n)197Hg"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def command(arguments: list[str]) -> str:
    completed = subprocess.run(
        arguments,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=120,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(f"command failed: {' '.join(arguments)}\n{completed.stderr[-2000:]}")
    return completed.stdout.strip()


def file_identity(path: Path, expected: str) -> dict[str, Any]:
    actual = sha256(path) if path.is_file() and not path.is_symlink() else None
    return {
        "bytes": path.stat().st_size if actual is not None else None,
        "expected_sha256": expected,
        "sha256": actual,
        "pass": actual == expected,
    }


def identity_fields(raw: bytes, line_number: int) -> tuple[int, int, int, int, int, int]:
    if len(raw) < 20:
        raise ValueError(f"line {line_number}: record is shorter than column 20")
    values = []
    for start in range(0, 18, 3):
        field = raw[start : start + 3]
        try:
            values.append(int(field.decode("ascii")))
        except (UnicodeDecodeError, ValueError) as error:
            raise ValueError(f"line {line_number}: invalid identity field {field!r}") from error
    return tuple(values)  # type: ignore[return-value]


def family_id(identity: tuple[int, int, int, int, int, int], reaction: str) -> str:
    zp, ap, zt, at, zr, ar = identity
    return f"{zp:03d}-{ap:03d}|{zt:03d}-{at:03d}|{zr:03d}-{ar:03d}|{reaction}"


def validate_reaction(
    reaction: str, identity: tuple[int, int, int, int, int, int], line_number: int
) -> tuple[str | None, str | None]:
    _, _, zt, at, zr, ar = identity
    if at == 0:
        return None, None
    match = REACTION.fullmatch(reaction)
    if match is None:
        raise ValueError(f"line {line_number}: isotope reaction does not match the frozen grammar")
    target_mass, target_symbol, _, product_mass, product_symbol = match.groups()
    if int(target_mass) != at or int(product_mass) != ar:
        raise ValueError(f"line {line_number}: reaction mass disagrees with identity fields")
    if not 1 <= zt <= len(SYMBOLS) or SYMBOLS[zt - 1] != target_symbol:
        raise ValueError(f"line {line_number}: target symbol disagrees with Z")
    if not 1 <= zr <= len(SYMBOLS) or SYMBOLS[zr - 1] != product_symbol:
        raise ValueError(f"line {line_number}: product symbol disagrees with Z")
    return target_symbol, product_symbol


def parse_p17_pairs() -> tuple[set[tuple[int, int]], str]:
    payload = json.loads(P17_HELDOUT.read_text(encoding="utf-8"))
    pairs: set[tuple[int, int]] = set()
    for row in payload["rows"]:
        mapping = row.get("heldout_evidence", {}).get("frozen_amendment_1_mapping")
        if not isinstance(mapping, dict):
            continue
        target = mapping.get("target_za")
        product = mapping.get("decay_product_za") or mapping.get("product_za")
        if isinstance(target, int) and isinstance(product, int):
            pairs.add((target, product))
    return pairs, sha256(P17_HELDOUT)


def target_sets() -> tuple[dict[str, set[str]], dict[str, dict[str, Any]]]:
    names: dict[str, set[str]] = {}
    identities: dict[str, dict[str, Any]] = {}
    for projectile, code in MANIFEST_CODE.items():
        path = DATA_ROOT / f"staging/TENDL-{code}.manifest.json"
        identities[projectile] = file_identity(path, MANIFEST_HASHES[projectile])
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("projectile") != projectile or len(payload.get("files", [])) != 2_850:
            raise ValueError(f"{path}: unexpected projectile or file count")
        names[projectile] = {item["name"] for item in payload["files"]}
    return names, identities


def family_preeligibility(
    family: dict[str, Any], targets: dict[str, set[str]]
) -> tuple[bool, str | None]:
    projectile = family["projectile"]
    if projectile not in SUPPORTED_PROJECTILES:
        return False, "unsupported_projectile"
    if family["target"]["A"] == 0:
        return False, "natural_target"
    symbol = family["target"]["symbol"]
    expected = f"{MANIFEST_CODE[projectile]}-{symbol}{family['target']['A']:03d}.tendl"
    if expected not in targets[projectile]:
        return False, "target_absent_from_frozen_tendl_corpus"
    return True, None


def parse_supplement(path: Path, source_hash: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    families: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    started = False
    expecting_reaction = False
    preamble_column20_r = 0
    delimiter_count = 0
    marker_counts: Counter[str] = Counter()
    measurement_counts: Counter[str] = Counter()
    line_count = 0

    with path.open("rb") as stream:
        for line_count, physical in enumerate(stream, 1):
            raw = physical.rstrip(b"\r\n")
            if raw == b"999999999999999999":
                delimiter_count += 1
                if current is not None:
                    if current["half_life_record_count"] != 1 or not current["rows"]:
                        raise ValueError(f"line {line_count}: incomplete family before delimiter")
                    families.append(current)
                    current = None
                started = True
                expecting_reaction = True
                continue

            marker = raw[19:20] if len(raw) >= 20 else b""
            if not started:
                if marker == b"R":
                    preamble_column20_r += 1
                continue
            if marker not in {b"R", b"H", b"D"}:
                raise ValueError(f"line {line_count}: invalid post-delimiter record marker {marker!r}")
            marker_text = marker.decode("ascii")
            marker_counts[marker_text] += 1
            identity = identity_fields(raw, line_count)

            if marker == b"R":
                if not expecting_reaction or current is not None:
                    raise ValueError(f"line {line_count}: reaction record is out of order")
                try:
                    reaction = raw[22:].decode("ascii").strip()
                except UnicodeDecodeError as error:
                    raise ValueError(f"line {line_count}: non-ASCII reaction identity") from error
                target_symbol, product_symbol = validate_reaction(reaction, identity, line_count)
                zp, ap, zt, at, zr, ar = identity
                projectile = PROJECTILES.get((zp, ap))
                if projectile is None:
                    raise ValueError(f"line {line_count}: unsupported source projectile identity {(zp, ap)}")
                current = {
                    "family_id": family_id(identity, reaction),
                    "source_line": line_count,
                    "reaction": reaction,
                    "projectile": projectile,
                    "projectile_identity": {"Z": zp, "A": ap},
                    "target": {"Z": zt, "A": at, "symbol": target_symbol},
                    "product": {"Z": zr, "A": ar, "symbol": product_symbol},
                    "half_life_record_count": 0,
                    "rows": [],
                }
                expecting_reaction = False
                continue

            if current is None or identity != (
                current["projectile_identity"]["Z"],
                current["projectile_identity"]["A"],
                current["target"]["Z"],
                current["target"]["A"],
                current["product"]["Z"],
                current["product"]["A"],
            ):
                raise ValueError(f"line {line_count}: H/D identity differs from active reaction")
            if marker == b"H":
                if current["half_life_record_count"] or current["rows"]:
                    raise ValueError(f"line {line_count}: half-life record is out of order")
                current["half_life_record_count"] = 1
                continue

            if not current["half_life_record_count"] or len(raw) < 136:
                raise ValueError(f"line {line_count}: data record is early or shorter than column 136")
            try:
                energy = raw[22:33].decode("ascii").strip()
                measurement = raw[122:125].decode("ascii").strip()
                exfor = raw[127:132].decode("ascii").strip()
            except UnicodeDecodeError as error:
                raise ValueError(f"line {line_count}: non-ASCII permitted metadata") from error
            try:
                energy_value = float(energy)
            except ValueError as error:
                raise ValueError(f"line {line_count}: invalid incident energy metadata") from error
            if not energy or not (energy_value >= 0.0 and energy_value < float("inf")):
                raise ValueError(f"line {line_count}: nonfinite or negative incident energy")
            if measurement not in RATIO_FORMS:
                raise ValueError(f"line {line_count}: unknown measurement type {measurement!r}")
            flag_bytes = raw[133:136]
            permitted = {0: b"!", 1: b"*", 2: b"+"}
            flags = []
            flag_names = ["digitized", "repeated_energy", "uncertainty_crosses_ratio_bounds"]
            for offset, value in enumerate(flag_bytes):
                if value == 32:
                    continue
                if bytes([value]) != permitted[offset]:
                    raise ValueError(f"line {line_count}: invalid source flag in column {134 + offset}")
                flags.append(flag_names[offset])
            row_key = (
                f"{source_hash}\n{current['family_id']}\n{line_count}\n{energy}\n"
                f"{measurement}\n{exfor}\n{','.join(flags)}"
            )
            row_id = f"p18-row-{line_count:05d}-{hashlib.sha256(row_key.encode()).hexdigest()[:16]}"
            current["rows"].append(
                {
                    "row_id": row_id,
                    "source_line": line_count,
                    "incident_energy_MeV": energy,
                    "measurement_type": measurement,
                    "exfor_entry": exfor,
                    "source_flags": flags,
                    "eligibility_after_unseal": "pending_frozen_state_and_value_predicates",
                }
            )
            measurement_counts[measurement] += 1

    if current is not None or not started or not expecting_reaction:
        raise ValueError("supplement ended without a final family delimiter")
    if len({family["family_id"] for family in families}) != len(families):
        raise ValueError("duplicate canonical reaction family")
    structure = {
        "physical_lines": line_count,
        "delimiters": delimiter_count,
        "preamble_column20_R_records": preamble_column20_r,
        "naive_global_column20_R_count": preamble_column20_r + marker_counts["R"],
        "post_delimiter_marker_counts": dict(sorted(marker_counts.items())),
        "measurement_type_counts": dict(sorted(measurement_counts.items())),
        "published_family_count": EXPECTED_FAMILIES,
        "resolved_family_count": len(families),
        "resolved_data_row_count": sum(len(family["rows"]) for family in families),
        "header_discrepancy_resolution": (
            "the sole pre-delimiter column-20 R is prose 'Reference:', not a reaction record"
        ),
    }
    return families, structure


def assign_partition(
    families: list[dict[str, Any]], targets: dict[str, set[str]], p17_pairs: set[tuple[int, int]]
) -> dict[str, Any]:
    pools: dict[str, list[dict[str, Any]]] = defaultdict(list)
    forced_counts: Counter[str] = Counter()
    for family in families:
        preeligible, reason = family_preeligibility(family, targets)
        family["preeligible"] = preeligible
        family["preeligibility_reason"] = reason
        target_za = family["target"]["Z"] * 1000 + family["target"]["A"]
        product_za = family["product"]["Z"] * 1000 + family["product"]["A"]
        forced_reason = None
        if family["reaction"] in DISCLOSED_REACTIONS:
            forced_reason = "amendment_1_disclosed_lines_1_140"
        elif family["reaction"] in PAPER_CASES:
            forced_reason = "paper_case_study"
        elif family["projectile"] == "neutron" and (target_za, product_za) in p17_pairs:
            forced_reason = "p17_exposed_target_product_pair"
        family["forced_diagnostic_reason"] = forced_reason
        if forced_reason is not None:
            forced_counts[forced_reason] += 1
        if not preeligible:
            family["partition"] = "ineligible"
        elif forced_reason is not None:
            family["partition"] = "diagnostic"
        else:
            digest_input = (
                f"{PARTITION_SEED}\n{family['projectile']}\n{family['family_id']}"
            ).encode("utf-8")
            family["partition_sha256"] = hashlib.sha256(digest_input).hexdigest()
            pools[family["projectile"]].append(family)

    strata: dict[str, Any] = {}
    for projectile in sorted(SUPPORTED_PROJECTILES):
        pool = sorted(pools.get(projectile, []), key=lambda item: item["partition_sha256"])
        heldout_count = len(pool) // 4
        if len(pool) >= 4:
            heldout_count = max(1, heldout_count)
        heldout_ids = {family["family_id"] for family in pool[:heldout_count]}
        for family in pool:
            family["partition"] = (
                "heldout" if family["family_id"] in heldout_ids else "diagnostic"
            )
        strata[projectile] = {
            "ranked_pool_families": len(pool),
            "heldout_families": heldout_count,
            "diagnostic_families": len(pool) - heldout_count,
        }

    for family in families:
        family["row_count"] = len(family["rows"])
        family["row_ids_sha256"] = hashlib.sha256(
            canonical_json([row["row_id"] for row in family["rows"]])
        ).hexdigest()
    partitions = Counter(family["partition"] for family in families)
    row_partitions = Counter(
        family["partition"] for family in families for _ in family["rows"]
    )
    return {
        "seed": PARTITION_SEED,
        "family_counts": dict(sorted(partitions.items())),
        "row_counts": dict(sorted(row_partitions.items())),
        "forced_diagnostic_counts": dict(sorted(forced_counts.items())),
        "strata": strata,
    }


def provenance(verify_github: bool) -> dict[str, Any]:
    archive_identities = {}
    manifest_identities = {}
    library_identities = {}
    for projectile, code in MANIFEST_CODE.items():
        archive_identities[projectile] = file_identity(
            DATA_ROOT / f"archives/TENDL-{code}.tgz", ARCHIVE_HASHES[projectile]
        )
        manifest_identities[projectile] = file_identity(
            DATA_ROOT / f"staging/TENDL-{code}.manifest.json", MANIFEST_HASHES[projectile]
        )
        library_identities[projectile] = file_identity(
            RELEASE_STAGE / LIBRARY_NAMES[projectile], LIBRARY_HASHES[projectile]
        )
    decay = {
        name: file_identity(path, expected) for name, (path, expected) in DECAY_FILES.items()
    }
    workflow: dict[str, Any]
    if verify_github:
        workflow = json.loads(
            command(
                [
                    "gh",
                    "run",
                    "view",
                    str(P17_CLOSURE_RUN),
                    "--repo",
                    "AvilaLabs/ACTINV",
                    "--json",
                    "databaseId,headSha,status,conclusion,url",
                ]
            )
        )
    else:
        workflow = {
            "databaseId": P17_CLOSURE_RUN,
            "headSha": OPENING_COMMIT,
            "status": "not_live_verified",
            "conclusion": None,
            "url": f"https://github.com/AvilaLabs/ACTINV/actions/runs/{P17_CLOSURE_RUN}",
        }
    opening_is_ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", OPENING_COMMIT, "HEAD"], cwd=ROOT, check=False
    ).returncode == 0
    production_paths = command(
        [
            "git",
            "diff",
            "--name-only",
            f"{OPENING_COMMIT}..HEAD",
            "--",
            "Cargo.toml",
            "Cargo.lock",
            "crates",
            "python",
        ]
    ).splitlines()
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    stage_evidence = json.loads(P13_STAGE_EVIDENCE.read_text(encoding="utf-8"))
    catalog_hashes = {item["id"]: item["sha256"] for item in catalog["artifacts"]}
    return {
        "opening_commit": OPENING_COMMIT,
        "opening_is_ancestor": opening_is_ancestor,
        "production_paths_changed_since_opening": production_paths,
        "protocol": file_identity(PROTOCOL, PROTOCOL_SHA256),
        "amendment": file_identity(AMENDMENT, AMENDMENT_SHA256),
        "paper": file_identity(PAPER, PAPER_SHA256),
        "supplement": file_identity(SUPPLEMENT, SUPPLEMENT_SHA256),
        "endf_manual": file_identity(MANUAL, MANUAL_SHA256),
        "raw_archives": archive_identities,
        "staging_manifests": manifest_identities,
        "released_libraries": library_identities,
        "decay_payloads": decay,
        "catalog_decay_hashes": {
            "endfb_viii_0": catalog_hashes.get("endfb-viii-0-decay"),
            "jeff_3_3": catalog_hashes.get("jeff-3-3-decay"),
        },
        "p13_release_stage_evidence_sha256": sha256(P13_STAGE_EVIDENCE),
        "p13_release_stage_pass": stage_evidence.get("pass") is True,
        "p17_closure_workflow": workflow,
    }


def fixed_record(marker: bytes, *, reaction: bytes = b"", secret: bytes = b"") -> bytes:
    raw = bytearray(b" " * 150)
    raw[0:18] = b"  0  1 17 35 17 34"
    raw[19:20] = marker
    if marker == b"R":
        raw[22 : 22 + len(reaction)] = reaction
    elif marker == b"D":
        raw[22:33] = b" 1.4800E+01"
        raw[33 : 33 + len(secret)] = secret
        raw[122:125] = b"M/T"
        raw[127:132] = b"11550"
        raw[133:136] = b"!  "
    return bytes(raw)


def mutation_plants() -> dict[str, bool]:
    secret = b"P18_DEPENDENT_VALUE_MUST_NOT_APPEAR"
    fixture = b"\n".join(
        [
            b"                   Reference: preamble prose with R in column 20",
            b"999999999999999999",
            fixed_record(b"R", reaction=b"35Cl(n,2n)34Cl"),
            fixed_record(b"H"),
            fixed_record(b"D", secret=secret),
            b"999999999999999999",
            b"",
        ]
    )
    with tempfile.TemporaryDirectory(prefix="actinv-p18-g0-") as temporary:
        path = Path(temporary) / "fixture.txt"
        path.write_bytes(fixture)
        families, structure = parse_supplement(path, hashlib.sha256(fixture).hexdigest())
    rendered = canonical_json({"families": families, "structure": structure})
    reordered = list(reversed(families))
    targets = {name: {"n-Cl035.tendl"} for name in SUPPORTED_PROJECTILES}
    first = assign_partition(json.loads(json.dumps(families)), targets, set())
    second = assign_partition(json.loads(json.dumps(reordered)), targets, set())
    duplicate_rejected = False
    try:
        with tempfile.TemporaryDirectory(prefix="actinv-p18-g0-duplicate-") as temporary:
            path = Path(temporary) / "duplicate.txt"
            path.write_bytes(fixture[:-19] + fixture[fixture.index(b"999999999999999999") :])
            parse_supplement(path, "0" * 64)
    except ValueError:
        duplicate_rejected = True
    return {
        "dependent_span_not_emitted": secret not in rendered,
        "leading_space_D_recognized": structure["post_delimiter_marker_counts"] == {
            "D": 1,
            "H": 1,
            "R": 1,
        },
        "preamble_R_not_a_family": structure["preamble_column20_R_records"] == 1
        and len(families) == 1,
        "reordered_partition_counts_identical": first == second,
        "duplicate_or_malformed_family_rejected": duplicate_rejected,
    }


def derive(verify_github: bool) -> tuple[dict[str, Any], dict[str, Any]]:
    source_hash = sha256(SUPPLEMENT)
    if source_hash != SUPPLEMENT_SHA256:
        raise ValueError("supplement hash differs from the frozen source")
    targets, target_manifest_identities = target_sets()
    p17_pairs, p17_hash = parse_p17_pairs()
    families, structure = parse_supplement(SUPPLEMENT, source_hash)
    partition = assign_partition(families, targets, p17_pairs)
    plants = mutation_plants()
    source = provenance(verify_github)
    source["target_manifest_second_parse"] = target_manifest_identities
    source["p17_heldout_evidence_sha256"] = p17_hash
    seal = {
        "schema": "actinv-p18-family-seal-1",
        "protocol_sha256": PROTOCOL_SHA256,
        "amendment_sha256": AMENDMENT_SHA256,
        "control_source_sha256": sha256(Path(__file__)),
        "checker_source_sha256": sha256(CHECKER),
        "source": {
            "url": "https://ars.els-cdn.com/content/image/1-s2.0-S0092640X23000116-mmc1.txt",
            "sha256": source_hash,
            "bytes": SUPPLEMENT.stat().st_size,
        },
        "disclosure_quarantine": {
            "physical_lines_inclusive": DISCLOSED_LINES,
            "reactions": sorted(DISCLOSED_REACTIONS),
        },
        "structure": structure,
        "partition": partition,
        "families": sorted(families, key=lambda item: item["family_id"]),
    }
    seal_hash = hashlib.sha256(canonical_json(seal)).hexdigest()
    workflow = source["p17_closure_workflow"]
    provenance_pass = (
        source["opening_is_ancestor"]
        and source["production_paths_changed_since_opening"] == []
        and all(source[name]["pass"] for name in ("protocol", "amendment", "paper", "supplement", "endf_manual"))
        and all(item["pass"] for item in source["raw_archives"].values())
        and all(item["pass"] for item in source["staging_manifests"].values())
        and all(item["pass"] for item in source["released_libraries"].values())
        and all(item["pass"] for item in source["decay_payloads"].values())
        and all(item["pass"] for item in source["target_manifest_second_parse"].values())
        and source["catalog_decay_hashes"]
        == {
            "endfb_viii_0": DECAY_FILES["endfb_viii_0"][1],
            "jeff_3_3": DECAY_FILES["jeff_3_3"][1],
        }
        and source["p13_release_stage_pass"]
        and workflow.get("databaseId") == P17_CLOSURE_RUN
        and workflow.get("headSha") == OPENING_COMMIT
        and workflow.get("status") == "completed"
        and workflow.get("conclusion") == "success"
    )
    structure_pass = (
        structure["resolved_family_count"] == EXPECTED_FAMILIES
        and structure["resolved_data_row_count"] == EXPECTED_ROWS
        and structure["post_delimiter_marker_counts"]
        == {"D": EXPECTED_ROWS, "H": EXPECTED_FAMILIES, "R": EXPECTED_FAMILIES}
        and structure["preamble_column20_R_records"] == 1
        and structure["naive_global_column20_R_count"] == EXPECTED_FAMILIES + 1
        and structure["delimiters"] == EXPECTED_FAMILIES + 1
    )
    disclosed = [family for family in families if family["reaction"] in DISCLOSED_REACTIONS]
    quarantine_pass = (
        {family["reaction"] for family in disclosed} == DISCLOSED_REACTIONS
        and all(family["partition"] != "heldout" for family in disclosed)
    )
    partitions_pass = (
        sum(partition["family_counts"].values()) == EXPECTED_FAMILIES
        and sum(partition["row_counts"].values()) == EXPECTED_ROWS
        and partition["family_counts"].get("heldout", 0) > 0
        and partition["family_counts"].get("diagnostic", 0) > 0
        and all(
            family["partition"] in {"diagnostic", "heldout", "ineligible"}
            for family in families
        )
    )
    checks = {
        "provenance": provenance_pass,
        "published_structure_reconciled": structure_pass,
        "disclosure_quarantine_exact": quarantine_pass,
        "partition_complete_and_disjoint": partitions_pass,
        "mutation_plants": all(plants.values()),
        "dependent_fields_absent": not any(
            forbidden in canonical_json(seal).lower()
            for forbidden in (b"sigma_g", b"sigma_m", b"sigma_t", b"measured_ratio", b"ratio_uncertainty")
        ),
    }
    result = {
        "schema": "actinv-p18-g0-seal-control-1",
        "gate": "P18-G0",
        "protocol_sha256": PROTOCOL_SHA256,
        "amendment_sha256": AMENDMENT_SHA256,
        "control_source_sha256": sha256(Path(__file__)),
        "checker_source_sha256": sha256(CHECKER),
        "source": source,
        "structure": structure,
        "partition": partition,
        "seal_sha256": seal_hash,
        "seal_families": len(families),
        "seal_rows": sum(len(family["rows"]) for family in families),
        "mutation_plants": plants,
        "checks": checks,
        "pass": all(checks.values()),
    }
    return seal, result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--verify-github", action="store_true")
    arguments = parser.parse_args()
    try:
        seal, result = derive(arguments.verify_github)
    except (KeyError, OSError, RuntimeError, TypeError, ValueError) as error:
        print(json.dumps({"schema": "actinv-p18-g0-seal-control-1", "pass": False, "error": str(error)}, indent=1))
        return 1
    if not arguments.no_write:
        SEAL.write_text(json.dumps(seal, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        RESULT.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=1, sort_keys=True))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
