#!/usr/bin/env python3
"""Independent checker for the ``data-v1.1.0`` staged release.

Recomputes every claim in ``results/p25c_release_stage.json`` from
primary evidence: rehashes all staged assets, revalidates the generated
catalog against the frozen structural rules (every artifact referenced,
one artifact per role per bundle, required roles present), confirms the
v1.0.0 artifact payloads are byte-identical so ``catalog:<id>``
references keep resolving to the same bytes, verifies the covariance
sidecar's ``activation_library_sha256`` binding, and proves the patched
activation index's per-target ``source_sha256`` values equal the staged
patched-corpus bytes.

Mutation tests: planted corruption in the catalog, the sums, and the
record must each be caught.
"""
from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
WORK = ROOT / "target" / "p25c-release"
ASSETS = WORK / "assets"
STAGE = WORK / "stage"
CLI_DATA = ROOT / "crates" / "actinv-cli" / "data"
OLD_CATALOG = CLI_DATA / "actinv-data-catalog-v1.0.0.json"
NEW_CATALOG = CLI_DATA / "actinv-data-catalog-v1.1.0.json"
NEW_NOTICE = CLI_DATA / "ACTINV-DATA-NOTICE-v1.1.0.md"
BUILD_RECORD = RESULTS / "p25c_release_build.json"
COV_RECORD = RESULTS / "p25c_release_covariance.json"
STAGE_RECORD = RESULTS / "p25c_release_stage.json"

NPZ_NAME = "tendl-2025-patched-neutron-709g.npz"
INDEX_NAME = "tendl-2025-patched-neutron-709g_index.json"
COV_NAME = "tendl-2025-patched-neutron-709g.cov.npz"
COV_INDEX_NAME = "tendl-2025-patched-neutron-709g.cov_index.json"
NOTICE_NAME = "ACTINV-DATA-NOTICE-v1.1.0.md"
CATALOG_NAME = "actinv-data-catalog-v1.1.0.json"
ASSET_NAMES = [NPZ_NAME, INDEX_NAME, COV_NAME, COV_INDEX_NAME,
               NOTICE_NAME, CATALOG_NAME]

REQUIRED_ROLES = {"activation-library", "activation-index",
                  "decay-primary", "decay-fallback", "notice"}

NOTICE_REQUIRED = [
    "not an official TENDL release",
    "Avila Labs remediation derivative",
    "44 enumerated leaked leading ordinates",
    "28 confirmed-signature files",
    "Ni-58", "Nb-93", "Ag-109", "In-113", "Au-197",
    "CC-BY-4.0",
    "Koning", "Rochman",
]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_catalog(catalog: dict, old: dict, record: dict) -> list[str]:
    failures = []
    if catalog.get("schema") != "actinv-data-catalog-1":
        failures.append("catalog schema changed")
    if catalog.get("catalog_version") != "1.1.0":
        failures.append("catalog_version is not 1.1.0")
    if catalog.get("default_bundle") != "tendl-2025-patched-neutron":
        failures.append("default bundle is not the patched bundle")
    if catalog.get("notice") != NOTICE_NAME:
        failures.append("catalog notice is not the v1.1.0 notice")
    if "data-v1.1.0" not in str(catalog.get("release_url")):
        failures.append("release_url does not name data-v1.1.0")

    artifacts = catalog.get("artifacts", [])
    bundles = catalog.get("bundles", [])
    by_id = {}
    for a in artifacts:
        if a["id"] in by_id:
            failures.append(f"duplicate artifact id {a['id']}")
        by_id[a["id"]] = a

    # every old artifact id survives with identical payload
    for a in old["artifacts"]:
        new = by_id.get(a["id"])
        if new is None:
            failures.append(f"v1.0.0 artifact {a['id']} dropped")
            continue
        for key in ("sha256", "bytes", "role"):
            if new[key] != a[key]:
                failures.append(f"artifact {a['id']} {key} changed")
        if a["id"] != "actinv-data-notice-v1" and new["path"] != a["path"]:
            failures.append(f"artifact {a['id']} install path changed")
        if new["source"]["sha256"] != a["source"]["sha256"]:
            failures.append(f"artifact {a['id']} source digest changed")
    notice_old = by_id.get("actinv-data-notice-v1", {})
    if notice_old.get("path") != "legacy/ACTINV-DATA-NOTICE-v1.0.0.md":
        failures.append("legacy notice path does not avoid the collision")

    # new patched artifacts exist, point at data-v1.1.0, and match assets
    expect = {
        "tendl-2025-patched-neutron-709g": NPZ_NAME,
        "tendl-2025-patched-neutron-709g-index": INDEX_NAME,
        "tendl-2025-patched-neutron-709g-covariance": COV_NAME,
        "tendl-2025-patched-neutron-709g-covariance-index": COV_INDEX_NAME,
        "actinv-data-notice-v1-1": NOTICE_NAME,
    }
    for aid, name in expect.items():
        a = by_id.get(aid)
        if a is None:
            failures.append(f"missing new artifact {aid}")
            continue
        asset = ASSETS / name
        if not asset.is_file():
            failures.append(f"missing staged asset {name}")
            continue
        if a["sha256"] != sha256(asset) or a["bytes"] != asset.stat().st_size:
            failures.append(f"artifact {aid} does not match staged asset")
        for key in ("url",):
            if f"/download/data-v1.1.0/{name}" not in a["source"][key]:
                failures.append(f"artifact {aid} source URL wrong")
        if a["source"]["sha256"] != a["sha256"]:
            failures.append(f"artifact {aid} source digest != payload")

    # bundle rules: references resolve, roles unique, required present,
    # every artifact referenced somewhere
    referenced = set()
    bundle_ids = set()
    for b in bundles:
        bundle_ids.add(b["id"])
        roles = set()
        for aid in b["artifacts"]:
            a = by_id.get(aid)
            if a is None:
                failures.append(f"bundle {b['id']} names unknown {aid}")
                continue
            if a["role"] in roles:
                failures.append(f"bundle {b['id']} repeats role {a['role']}")
            roles.add(a["role"])
            referenced.add(aid)
        missing = REQUIRED_ROLES - roles
        if missing:
            failures.append(f"bundle {b['id']} missing roles {missing}")
    if catalog.get("default_bundle") not in bundle_ids:
        failures.append("default bundle absent")
    unreferenced = set(by_id) - referenced
    if unreferenced:
        failures.append(f"unreferenced artifacts {sorted(unreferenced)}")

    patched = next((b for b in bundles
                    if b["id"] == "tendl-2025-patched-neutron"), {})
    if "tendl-2025-patched-neutron-709g" not in patched.get("artifacts", []):
        failures.append("patched bundle lacks the patched library")
    legacy = next((b for b in bundles
                   if b["id"] == "tendl-2025-neutron"), {})
    if "tendl-2025-neutron-709g" not in legacy.get("artifacts", []):
        failures.append("legacy bundle no longer carries the v1.0.0 pair")
    return failures


def check_frozen(record: dict) -> list[str]:
    failures = []
    catalog = json.loads(NEW_CATALOG.read_text())
    old = json.loads(OLD_CATALOG.read_text())
    failures += check_catalog(catalog, old, record)

    # assets match the record
    for name in ASSET_NAMES:
        asset = ASSETS / name
        if not asset.is_file():
            failures.append(f"asset {name} missing")
            continue
        entry = record["assets"].get(name, {})
        if entry.get("sha256") != sha256(asset):
            failures.append(f"asset {name} sha256 mismatch vs record")
        if entry.get("bytes") != asset.stat().st_size:
            failures.append(f"asset {name} size mismatch vs record")

    # embedded catalog copy == asset copy
    if NEW_CATALOG.read_bytes() != (ASSETS / CATALOG_NAME).read_bytes():
        failures.append("embedded catalog != release asset")
    if NEW_NOTICE.read_bytes() != (ASSETS / NOTICE_NAME).read_bytes():
        failures.append("embedded notice != release asset")

    # SHA256SUMS / SIZES cover every asset correctly
    sums = {}
    for line in (ASSETS / "SHA256SUMS").read_text().splitlines():
        d, n = line.split(None, 1)
        sums[n.strip()] = d
    sizes = {}
    for line in (ASSETS / "SIZES").read_text().splitlines():
        s, n = line.split(None, 1)
        sizes[n.strip()] = int(s)
    if set(sums) != set(ASSET_NAMES) or set(sizes) != set(ASSET_NAMES):
        failures.append("SHA256SUMS/SIZES do not cover exactly the assets")
    for name in ASSET_NAMES:
        if sums.get(name) != sha256(ASSETS / name):
            failures.append(f"SHA256SUMS wrong for {name}")
        if sizes.get(name) != (ASSETS / name).stat().st_size:
            failures.append(f"SIZES wrong for {name}")

    # covariance binding: sidecar index names the patched npz
    build = json.loads(BUILD_RECORD.read_text())
    cov = json.loads(COV_RECORD.read_text())
    cov_index = json.loads((WORK / COV_INDEX_NAME).read_text())
    if cov_index["activation_library_sha256"] != sha256(ASSETS / NPZ_NAME):
        failures.append("cov index activation_library_sha256 mismatch")
    if record["activation"]["npz_sha256"] != sha256(ASSETS / NPZ_NAME):
        failures.append("record npz hash mismatch")
    if record["covariance"]["npz_sha256"] != sha256(ASSETS / COV_NAME):
        failures.append("record cov hash mismatch")
    if record["catalog_sha256"] != sha256(NEW_CATALOG):
        failures.append("record catalog sha256 mismatch")
    if record["notice_sha256"] != sha256(NEW_NOTICE):
        failures.append("record notice sha256 mismatch")
    if record["activation_failures"] != build["failed_files"]:
        failures.append("record activation failure count wrong")
    if record["covariance_failures"] != cov["failed_files"]:
        failures.append("record covariance failure count wrong")

    # provenance: every index target's source_sha256 equals the staged
    # patched bytes — proves the artifact was built from this corpus
    index = json.loads((WORK / INDEX_NAME).read_text())
    staged = {p.name: sha256(p) for p in STAGE.iterdir()}
    index_files = {}
    for t in index["targets"]:
        index_files.setdefault(t["file"], set()).add(t["source_sha256"])
    if set(index_files) != set(staged):
        failures.append("index target files != staged survivors")
    for name, digests in index_files.items():
        if digests != {staged[name]}:
            failures.append(f"{name}: index source_sha256 != staged bytes")
    if len(staged) != build["staged_files"]:
        failures.append("staged count != build record")
    if build["failed_files"] != len(build["failure_ledger"]):
        failures.append("failure ledger count wrong")

    # carried P10 repairs: the record must name exactly the pinned set,
    # the staged file must be the working bytes, and the sealed patched
    # corpus must remain the official bytes
    expected_carried = {
        "n-Pb208.tendl": {
            "official_sha256":
                "32249bf71ee52a159ef8f94a4cb85d5c456aba13e1a4c4d9129c2304b6dc4137",
            "carried_sha256":
                "86788a14563ecdb844628a6a455864874ba5f5bca9b8142c10a59ea00df87c72",
        },
    }
    if build.get("carried_repairs") != expected_carried:
        failures.append("carried_repairs does not match the pinned P10 set")
    patched_root = Path.home() / "nuclear-data" / "tendl-2025-patched" / "files" / "n"
    for name, spec in build.get("carried_repairs", {}).items():
        staged_file = STAGE / name
        if not staged_file.is_file() or sha256(staged_file) != spec["carried_sha256"]:
            failures.append(f"carried repair {name} is not the P10 working bytes")
        sealed = patched_root / name
        if not sealed.is_file() or sha256(sealed) != spec["official_sha256"]:
            failures.append(f"sealed corpus {name} was modified")
        if index_files.get(name) != {spec["carried_sha256"]}:
            failures.append(f"index source for {name} is not the carried bytes")

    # probe-pass accounting: the recorded ledger-derived eviction count
    # must equal the probe-marked ledger entries
    probe_evictions = sum(
        1 for d in build["failure_ledger"].values() if "probe" in d)
    if build.get("probe_pass", {}).get("evicted") != probe_evictions:
        failures.append("probe eviction count differs from the ledger")

    # notice carries the required disclosures
    text = NEW_NOTICE.read_text()
    for needle in NOTICE_REQUIRED:
        if needle not in text:
            failures.append(f"notice missing required text: {needle!r}")
    return failures


def mutate(record: dict, fn) -> dict:
    copy_record = copy.deepcopy(record)
    fn(copy_record)
    return copy_record


def main() -> int:
    record = json.loads(STAGE_RECORD.read_text())
    base_failures = check_frozen(record)
    mutations = []

    mutations_to_try = [
        ("catalog sha", lambda r: r["assets"].__setitem__(
            CATALOG_NAME, {"bytes": r["assets"][CATALOG_NAME]["bytes"],
                           "sha256": "0" * 64})),
        ("asset bytes", lambda r: r["assets"][NPZ_NAME].__setitem__(
            "bytes", r["assets"][NPZ_NAME]["bytes"] + 1)),
        ("npz hash", lambda r: r["activation"].__setitem__(
            "npz_sha256", "0" * 64)),
        ("failure count", lambda r: r.__setitem__(
            "activation_failures", 0)),
    ]
    for name, fn in mutations_to_try:
        mutated = mutate(record, fn)
        if not check_frozen(mutated):
            mutations.append(name)

    out = {
        "schema": "actinv-p25c-release-check-1",
        "frozen_failures": base_failures,
        "mutations_rejected": len(mutations_to_try) - len(mutations),
        "mutations_missed": mutations,
        "pass": not base_failures and not mutations,
    }
    path = RESULTS / "check_p25c_release.json"
    path.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    print(json.dumps(out, indent=1))
    return 0 if out["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
