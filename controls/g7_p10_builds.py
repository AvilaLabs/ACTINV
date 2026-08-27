#!/usr/bin/env python3
"""P10-G7 external full-build, provenance, checkpoint and profile verifier."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import struct


ROOT = Path(__file__).resolve().parents[1]
DATA = Path(
    os.environ.get("ACTINV_P10_DATA_ROOT", "/home/connoravila/nuclear-data/tendl-2025")
).expanduser()
EAF = Path(
    os.environ.get("ACTINV_EAF2010_ROOT", "/home/connoravila/nuclear-data/eaf-2010")
).expanduser()
RESULT = ROOT / "results/g7_p10_builds.json"
G709_SHA256 = "a9c2fc80ec0e3a58abb5dea269cd68720429a087e48ea703f1c42b6ba57ab875"
G162_SHA256 = "60d0553a39eea8bf219c3f64a7204c14563bf815fcf8c8a20cdd8f858ab4334c"

CONFIG = {
    "alpha": {
        "prefix": "alpha.n.p10",
        "cache": "alpha-n-p10",
        "profile": "alpha.n.p10",
        "profile_cache": "profile-alpha-n-p10",
        "profile_file": "a-Fe056.tendl",
        "source": DATA / "files/a",
        "manifest": DATA / "staging/TENDL-a.manifest.json",
        "archive": DATA / "archives/TENDL-a.tgz",
        "manifest_sha256": "e3aaf11e60c46b43361796c2c297bab4fb714fe57ab26a315594f2b4799dfdbf",
        "archive_sha256": "25520f6eb42ce024c065f85255277ed169b2f826e9fc24f5d093c99d5c60e018",
        "file_manifest_sha256": "ca8bd5ea75d3cc3590a9f4115d94ec54f2cc110a09275b782ace3d608b1b7c81",
        "format": "tendl",
        "projectile": "alpha",
        "groups": "fispact-162",
        "group_sha256": G162_SHA256,
        "temperature_K": 0.0,
        "targets": 2_850,
    },
    "proton": {
        "prefix": "proton.n.p10",
        "cache": "proton-n-p10",
        "profile": "proton.n.p10",
        "profile_cache": "profile-proton-n-p10",
        "profile_file": "p-Fe056.tendl",
        "source": DATA / "files/p",
        "manifest": DATA / "staging/TENDL-p.manifest.json",
        "archive": DATA / "archives/TENDL-p.tgz",
        "manifest_sha256": "98a8bd55784c326b8696de91f494111326378e776a975a512e59806a8c9ec2ef",
        "archive_sha256": "49340a03b0d9ac86598c6b710c0bc2ec0babd3fa0717a9ff1d75f042fccc5b0b",
        "file_manifest_sha256": "0458a6c20e0b2fbb23934d2672304d210ceef74b0fc2807e9d9271c9aacf6ffd",
        "format": "tendl",
        "projectile": "proton",
        "groups": "fispact-162",
        "group_sha256": G162_SHA256,
        "temperature_K": 0.0,
        "targets": 2_850,
    },
    "deuteron": {
        "prefix": "deuteron.n.p10",
        "cache": "deuteron-n-p10",
        "profile": "deuteron.n.p10",
        "profile_cache": "profile-deuteron-n-p10",
        "profile_file": "d-Fe056.tendl",
        "source": DATA / "files/d",
        "manifest": DATA / "staging/TENDL-d.manifest.json",
        "archive": DATA / "archives/TENDL-d.tgz",
        "manifest_sha256": "afb52c55b2a1babca998cc3d8af0f7004c64f85d160e3c5aabf16a05839355d9",
        "archive_sha256": "34f459aea0b5ac9c40820c88d898618f926ec3b52858a5393e42d57707ec5f1c",
        "file_manifest_sha256": "feaa774185fb215e45c6fdf6bb26670bfeae9e4263386cfcccd4b7abcd3fa47f",
        "format": "tendl",
        "projectile": "deuteron",
        "groups": "fispact-162",
        "group_sha256": G162_SHA256,
        "temperature_K": 0.0,
        "targets": 2_850,
    },
    "neutron": {
        "prefix": "neutron.n.p10",
        "cache": "neutron-n-p10",
        "profile": "neutron.n.p10",
        "profile_cache": "profile-neutron-n-p10",
        "profile_file": "n-Fe056.tendl",
        "source": DATA / "files/n-working",
        "manifest": DATA / "staging/TENDL-n-working.manifest.json",
        "official_manifest": DATA / "staging/TENDL-n.manifest.json",
        "archive": DATA / "archives/TENDL-n.tgz",
        "manifest_sha256": "a6d17f996153d2671c0c51bfb6303e2a87a5af03e0696bfb34d668a31dbfb2a2",
        "official_manifest_sha256": "b578ab395c6c71d7727dfb0513e88effd96692862664a6279802638531239b67",
        "archive_sha256": "e547527688506cbe09813364dcefa2aed11f474139bfa129d7cd4ca24fae21fa",
        "file_manifest_sha256": "b1ea3fe043ec243e2df0a3894206872c2ce18c3b4541c19b35029b3ed3e7b15c",
        "format": "tendl",
        "projectile": "neutron",
        "groups": "fispact-709",
        "group_sha256": G709_SHA256,
        "temperature_K": 293.6,
        "targets": 2_850,
    },
    "eaf": {
        "prefix": "eaf.n.p10",
        "cache": "eaf-n-p10",
        "profile": "eaf.n.p10",
        "profile_cache": "profile-eaf-n-p10",
        "profile_file": "n_2631_26-FE-56.dat",
        "source": EAF / "files",
        "file_manifest": DATA / "staging/EAF-2010-flat-files.sha256",
        "source_manifest": EAF / "MANIFEST_zips.sha256",
        "file_manifest_sha256": "87baeeef62650cdf8791bd3f198c906b1e6787eb7017a3ec4b02d4cee88bc15e",
        "source_manifest_sha256": "5cd73807a39dbc2793bcd87bf0fea23338178d38d80b5848bf6ce2e28d8e0e40",
        "format": "eaf",
        "projectile": "neutron",
        "groups": "fispact-709",
        "group_sha256": G709_SHA256,
        "temperature_K": 293.6,
        "targets": 816,
    },
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(4 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def index_path(library: Path) -> Path:
    return library.with_name(f"{library.stem}_index.json")


def parse_time(path: Path, *, workers: int, address_limit: int) -> dict[str, object]:
    text = path.read_text()
    elapsed_match = re.search(r"Elapsed \(wall clock\) time .*\):\s*(\S+)", text)
    rss_match = re.search(r"Maximum resident set size \(kbytes\):\s*(\d+)", text)
    swaps_match = re.search(r"Swaps:\s*(\d+)", text)
    exit_match = re.search(r"Exit status:\s*(\d+)", text)
    command_match = re.search(r'Command being timed: "(.*)"', text)
    command = command_match.group(1) if command_match else ""
    return {
        "file": path.name,
        "sha256": sha256(path),
        "elapsed": elapsed_match.group(1) if elapsed_match else None,
        "maximum_rss_kib": int(rss_match.group(1)) if rss_match else None,
        "swaps": int(swaps_match.group(1)) if swaps_match else None,
        "exit_status": int(exit_match.group(1)) if exit_match else None,
        "terminated": "Command terminated" in text or "non-zero status" in text,
        "command": command,
        "pass": all((elapsed_match, rss_match, swaps_match, exit_match, command_match))
        and int(swaps_match.group(1)) == 0
        and int(exit_match.group(1)) == 0
        and "Command terminated" not in text
        and "non-zero status" not in text
        and f"prlimit --as={address_limit}" in command
        and f"--workers {workers}" in command,
    }


def checkpoint_key(
    source_sha256: str,
    config: dict[str, object],
    fingerprint: str,
    grid_density: float,
) -> str:
    digest = hashlib.sha256()
    digest.update(b"ACTINV-TARGET-CHECKPOINT-v1\0")
    digest.update(source_sha256.encode())
    digest.update(b"\0")
    digest.update(str(config["format"]).encode())
    digest.update(b"\0")
    digest.update(str(config["projectile"]).encode())
    digest.update(struct.pack("<d", float(config["temperature_K"])))
    digest.update(struct.pack("<d", grid_density))
    digest.update(str(config["group_sha256"]).encode())
    digest.update(fingerprint.encode())
    return digest.hexdigest()


def command_matches(
    profile: dict[str, object],
    *,
    source: Path,
    output: Path,
    cache: Path,
    config: dict[str, object],
    workers: int,
) -> bool:
    try:
        tokens = shlex.split(str(profile["command"]))
        position = tokens.index("build-library")
        flags = {
            tokens[index]: tokens[index + 1]
            for index in range(position + 3, len(tokens) - 1, 2)
        }
    except (ValueError, IndexError):
        return False
    return (
        tokens[position + 1 : position + 3] == [str(source), str(output)]
        and flags.get("--format") == config["format"]
        and flags.get("--projectile") == config["projectile"]
        and flags.get("--groups") == config["groups"]
        and float(flags.get("--temperature-K", "nan"))
        == float(config["temperature_K"])
        and flags.get("--workers") == str(workers)
        and flags.get("--cache") == str(cache)
    )


def compact_manifest(entries: dict[str, str]) -> str:
    digest = hashlib.sha256()
    for name, value in sorted(entries.items(), key=lambda item: item[0].encode()):
        digest.update(f"{value}  {name}\n".encode())
    return digest.hexdigest()


def manifest_entries(name: str, config: dict[str, object]) -> tuple[dict[str, str], dict[str, object]]:
    if name == "eaf":
        lines = Path(config["file_manifest"]).read_text().splitlines()
        entries = {line[66:]: line[:64] for line in lines if len(line) >= 67}
        evidence = {
            "file_manifest_sha256": sha256(Path(config["file_manifest"])),
            "source_manifest_sha256": sha256(Path(config["source_manifest"])),
        }
        evidence["pass"] = (
            evidence["file_manifest_sha256"] == config["file_manifest_sha256"]
            and evidence["source_manifest_sha256"] == config["source_manifest_sha256"]
            and len(entries) == config["targets"]
            and compact_manifest(entries) == config["file_manifest_sha256"]
        )
        return entries, evidence

    manifest = json.loads(Path(config["manifest"]).read_text())
    hash_key = "working_sha256" if name == "neutron" else "sha256"
    entries = {item["name"]: item[hash_key] for item in manifest["files"]}
    evidence = {
        "detailed_manifest_sha256": sha256(Path(config["manifest"])),
        "file_manifest_sha256": compact_manifest(entries),
        "archive_sha256": sha256(Path(config["archive"])),
    }
    official_ok = True
    if name == "neutron":
        official = json.loads(Path(config["official_manifest"]).read_text())
        official_ok = (
            sha256(Path(config["official_manifest"])) == config["official_manifest_sha256"]
            and official["archive_sha256"] == config["archive_sha256"]
            and manifest["official_detailed_manifest_sha256"]
            == config["official_manifest_sha256"]
            and manifest["official_file_manifest_sha256"]
            == official["file_manifest_sha256"]
        )
        evidence["official_detailed_manifest_sha256"] = config["official_manifest_sha256"]
        evidence["repairs"] = manifest["repairs"]
    evidence["pass"] = (
        evidence["detailed_manifest_sha256"] == config["manifest_sha256"]
        and evidence["file_manifest_sha256"] == config["file_manifest_sha256"]
        and evidence["archive_sha256"] == config["archive_sha256"]
        and len(entries) == config["targets"]
        and official_ok
    )
    return entries, evidence


def verify_cache(
    path: Path,
    final_targets: list[dict[str, object]],
    config: dict[str, object],
    fingerprint: str,
    grid_density: float,
) -> dict[str, object]:
    json_paths = sorted(path.glob("*.json"), key=lambda item: item.name)
    npz_paths = {item.stem: item for item in path.glob("*.npz")}
    targets_by_source: dict[str, list[dict[str, object]]] = {}
    for target in final_targets:
        targets_by_source.setdefault(str(target["source_sha256"]), []).append(target)
    checkpoint_sources = []
    mismatches = []
    for metadata_path in json_paths:
        metadata = json.loads(metadata_path.read_text())
        payload = npz_paths.get(metadata_path.stem)
        source = metadata.get("source_sha256", "")
        checkpoint_sources.append(source)
        expected_key = checkpoint_key(source, config, fingerprint, grid_density)
        if (
            metadata.get("schema") != "actinv-target-checkpoint-2"
            or metadata.get("key") != metadata_path.stem
            or metadata_path.stem != expected_key
            or metadata.get("format") != config["format"]
            or metadata.get("projectile") != config["projectile"]
            or payload is None
            or sha256(payload) != metadata["npz_sha256"]
            or metadata.get("targets") != targets_by_source.get(source)
            or len(metadata.get("target_float_bits", []))
            != len(metadata.get("targets", []))
        ):
            mismatches.append(metadata_path.name)
    expected_sources = sorted(targets_by_source)
    return {
        "directory": path.name,
        "metadata_files": len(json_paths),
        "payload_files": len(npz_paths),
        "validation_mismatches": mismatches[:20],
        "source_hashes_unique": len(targets_by_source) == len(final_targets),
        "source_inventory_matches": sorted(checkpoint_sources) == expected_sources,
        "pass": len(json_paths) == len(final_targets)
        and len(npz_paths) == len(final_targets)
        and not mismatches
        and len(targets_by_source) == len(final_targets)
        and sorted(checkpoint_sources) == expected_sources,
    }


def verify_pair(name: str, config: dict[str, object], expected_fingerprint: str) -> dict[str, object]:
    full = DATA / "builds/full"
    fresh = full / f"{config['prefix']}.npz"
    cached = full / f"{config['prefix']}.cached.npz"
    fresh_index_path = index_path(fresh)
    cached_index_path = index_path(cached)
    fresh_index = json.loads(fresh_index_path.read_text())
    cached_index = json.loads(cached_index_path.read_text())
    grid_density = float(fresh_index.get("options", {}).get("grid_density", float("nan")))
    manifest, provenance = manifest_entries(name, config)
    targets = fresh_index["targets"]
    target_entries = {target["file"]: target["source_sha256"] for target in targets}
    source_mismatches = []
    for filename, expected_hash in manifest.items():
        path = Path(config["source"]) / filename
        if not path.is_file() or sha256(path) != expected_hash:
            source_mismatches.append(filename)
    ledgers = [line for target in targets for line in target["ledger"]]
    unsupported = [line for line in ledgers if "unsupported" in line.lower()]
    convergence = [line for line in ledgers if "converg" in line.lower()]
    fresh_npz_sha = sha256(fresh)
    cached_npz_sha = sha256(cached)
    fresh_index_sha = sha256(fresh_index_path)
    cached_index_sha = sha256(cached_index_path)
    cache = verify_cache(
        DATA / f"cache/{config['cache']}",
        targets,
        config,
        expected_fingerprint,
        grid_density,
    )
    times = {
        "fresh": parse_time(
            full / f"{config['prefix']}.fresh.time", workers=4, address_limit=4 * 1024**3
        ),
        "cached": parse_time(
            full / f"{config['prefix']}.cached.time", workers=4, address_limit=4 * 1024**3
        ),
    }
    times["fresh"]["command_matches"] = command_matches(
        times["fresh"],
        source=Path(config["source"]),
        output=fresh,
        cache=DATA / f"cache/{config['cache']}",
        config=config,
        workers=4,
    )
    times["cached"]["command_matches"] = command_matches(
        times["cached"],
        source=Path(config["source"]),
        output=cached,
        cache=DATA / f"cache/{config['cache']}",
        config=config,
        workers=4,
    )
    result = {
        "targets": len(targets),
        "rows": fresh_index["n_rows"],
        "npz_sha256": fresh_npz_sha,
        "index_sha256": fresh_index_sha,
        "builder_fingerprint": fresh_index["builder_fingerprint"],
        "group_boundary_sha256": fresh_index["group_boundary_sha256"],
        "source_hash_mismatches": source_mismatches,
        "unsupported_ledger_entries": unsupported,
        "convergence_ledger_entries": convergence,
        "provenance": provenance,
        "cache": cache,
        "profile": {},
        "times": times,
        "cached_identity": fresh_npz_sha == cached_npz_sha
        and fresh_index_sha == cached_index_sha,
    }
    profile = DATA / f"builds/profiles/{config['profile']}.npz"
    profile_index = json.loads(index_path(profile).read_text())
    profile_time = parse_time(
        DATA / f"builds/profiles/{config['profile']}.time",
        workers=1,
        address_limit=4 * 1024**3,
    )
    profile_time["command_matches"] = command_matches(
        profile_time,
        source=Path(config["source"]) / str(config["profile_file"]),
        output=profile,
        cache=DATA / f"cache/{config['profile_cache']}",
        config=config,
        workers=1,
    )
    result["profile"] = {
        "npz_sha256": sha256(profile),
        "index_sha256": sha256(index_path(profile)),
        "targets": len(profile_index["targets"]),
        "rows": profile_index["n_rows"],
        "builder_fingerprint": profile_index["builder_fingerprint"],
        "time": profile_time,
    }
    result["profile"]["pass"] = (
        result["profile"]["targets"] == 1
        and profile_index["targets"][0]["file"] == config["profile_file"]
        and result["profile"]["builder_fingerprint"] == expected_fingerprint
        and profile_index["schema"] == "actinv-library-index-1"
        and profile_index["format"] == config["format"]
        and profile_index["projectile"] == config["projectile"]
        and profile_index["groups"] == config["groups"]
        and profile_index["group_boundary_sha256"] == config["group_sha256"]
        and profile_index["temperature_K"] == config["temperature_K"]
        and profile_index["options"]["grid_density"] == 1.0
        and result["profile"]["npz_sha256"] == profile_index["sha256_npz"]
        and profile_index["n_rows"]
        == sum(target["n_rows"] for target in profile_index["targets"])
        and profile_index["targets"][0]["source_sha256"]
        == manifest[config["profile_file"]]
        and result["profile"]["time"]["pass"]
        and result["profile"]["time"]["command_matches"]
        and result["profile"]["time"]["maximum_rss_kib"] < 2 * 1024**2
    )
    result["pass"] = (
        fresh_index == cached_index
        and fresh_npz_sha == fresh_index["sha256_npz"]
        and cached_npz_sha == cached_index["sha256_npz"]
        and result["cached_identity"]
        and fresh_index["schema"] == "actinv-library-index-1"
        and fresh_index["format"] == config["format"]
        and fresh_index["projectile"] == config["projectile"]
        and fresh_index["groups"] == config["groups"]
        and fresh_index["group_boundary_sha256"] == config["group_sha256"]
        and fresh_index["temperature_K"] == config["temperature_K"]
        and fresh_index["builder_fingerprint"] == expected_fingerprint
        and grid_density == 1.0
        and len(targets) == config["targets"]
        and sum(target["n_rows"] for target in targets) == fresh_index["n_rows"]
        and len(target_entries) == len(targets)
        and target_entries == manifest
        and not source_mismatches
        and not unsupported
        and not convergence
        and provenance["pass"]
        and cache["pass"]
        and all(item["pass"] and item["command_matches"] for item in times.values())
        and all(item["maximum_rss_kib"] < 4 * 1024**2 for item in times.values())
        and result["profile"]["pass"]
    )
    return result


def main() -> None:
    hs278 = json.loads((ROOT / "results/g7_p10_hs278_kink.json").read_text())
    expected_fingerprint = hs278["isolated_build"]["builder_fingerprint"]
    builds = {
        name: verify_pair(name, config, expected_fingerprint)
        for name, config in CONFIG.items()
    }
    output = {
        "schema": "actinv-p10-g7-builds-1",
        "gate": "P10-G7",
        "builder_fingerprint": expected_fingerprint,
        "builds": builds,
        "pass": hs278["pass"] and all(build["pass"] for build in builds.values()),
    }
    RESULT.write_text(json.dumps(output, indent=1, sort_keys=True) + "\n")
    print(json.dumps(output, indent=1, sort_keys=True))
    raise SystemExit(0 if output["pass"] else 1)


if __name__ == "__main__":
    main()
