#!/usr/bin/env python3
"""P25c release staging — assemble the ``data-v1.1.0`` asset set.

Copies the built patched-neutron artifact pair and covariance pair into
``target/p25c-release/assets/`` under their release names, generates the
v1.1.0 embedded catalog, the data notice copy, ``SHA256SUMS`` and
``SIZES``, and writes ``results/p25c_release_stage.json``.

Catalog design (frozen by this control):

* ``catalog_version`` becomes ``1.1.0``; ``default_bundle`` becomes
  ``tendl-2025-patched-neutron``.
* Every v1.0.0 artifact id is preserved with byte-identical payload and
  source URL, so existing ``catalog:<id>`` problem references resolve to
  exactly the same bytes.
* The v1.0.0 notice artifact keeps its id and bytes but installs to
  ``legacy/ACTINV-DATA-NOTICE-v1.0.0.md`` so it cannot collide with the
  new notice at ``ACTINV-DATA-NOTICE.md``.
* New artifact ids name the derivative honestly:
  ``tendl-2025-patched-neutron-709g`` (+``-index``, ``-covariance``,
  ``-covariance-index``) and ``actinv-data-notice-v1-1``.
* The legacy ``tendl-2025-neutron`` bundles keep resolving to the
  unpatched (defect-affected) artifacts and are described as superseded;
  the patched bundles are the default.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
WORK = ROOT / "target" / "p25c-release"
ASSETS = WORK / "assets"
CLI_DATA = ROOT / "crates" / "actinv-cli" / "data"
OLD_CATALOG = CLI_DATA / "actinv-data-catalog-v1.0.0.json"
NEW_CATALOG = CLI_DATA / "actinv-data-catalog-v1.1.0.json"
NEW_NOTICE = CLI_DATA / "ACTINV-DATA-NOTICE-v1.1.0.md"
BUILD_RECORD = RESULTS / "p25c_release_build.json"
COV_RECORD = RESULTS / "p25c_release_covariance.json"
RECORD = RESULTS / "p25c_release_stage.json"

RELEASE_TAG = "data-v1.1.0"
RELEASE_URL = f"https://github.com/AvilaLabs/ACTINV/releases/tag/{RELEASE_TAG}"
DOWNLOAD = f"https://github.com/AvilaLabs/ACTINV/releases/download/{RELEASE_TAG}"

NPZ_NAME = "tendl-2025-patched-neutron-709g.npz"
INDEX_NAME = "tendl-2025-patched-neutron-709g_index.json"
COV_NAME = "tendl-2025-patched-neutron-709g.cov.npz"
COV_INDEX_NAME = "tendl-2025-patched-neutron-709g.cov_index.json"
NOTICE_NAME = "ACTINV-DATA-NOTICE-v1.1.0.md"
CATALOG_NAME = "actinv-data-catalog-v1.1.0.json"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def artifact(aid, role, path, size, digest, url_name, licence="CC-BY-4.0",
             url=None):
    return {
        "id": aid,
        "role": role,
        "path": path,
        "bytes": size,
        "sha256": digest,
        "licence": licence,
        "source": {
            "url": url or f"{DOWNLOAD}/{url_name}",
            "bytes": size,
            "sha256": digest,
        },
    }


def main() -> int:
    build = json.loads(BUILD_RECORD.read_text())
    cov = json.loads(COV_RECORD.read_text())
    for key in ("npz_sha256", "index_sha256"):
        if not build["artifact"].get(key):
            raise SystemExit(f"activation {key} missing — run "
                             "controls/p25c_release_build.py first")
    for key in ("npz_sha256", "index_sha256"):
        if not cov["artifact"].get(key):
            raise SystemExit(f"covariance {key} missing — run "
                             "controls/p25c_release_covariance.py first")
    if cov["artifact"]["activation_library_sha256"] != \
            build["artifact"]["npz_sha256"]:
        raise SystemExit("covariance sidecar is bound to a different "
                         "activation library")

    ASSETS.mkdir(parents=True, exist_ok=True)
    staged = {
        NPZ_NAME: WORK / NPZ_NAME,
        INDEX_NAME: WORK / INDEX_NAME,
        COV_NAME: WORK / COV_NAME,
        COV_INDEX_NAME: WORK / COV_INDEX_NAME,
        NOTICE_NAME: NEW_NOTICE,
    }
    for name, src in staged.items():
        if not src.is_file():
            raise SystemExit(f"missing staged input {src}")
        shutil.copyfile(src, ASSETS / name)

    catalog = json.loads(OLD_CATALOG.read_text())
    catalog["catalog_version"] = "1.1.0"
    catalog["default_bundle"] = "tendl-2025-patched-neutron"
    catalog["release_url"] = RELEASE_URL
    catalog["notice"] = NOTICE_NAME

    artifacts = catalog["artifacts"]
    by_id = {a["id"]: a for a in artifacts}

    # move the old notice off the shared installed path
    by_id["actinv-data-notice-v1"]["path"] = \
        "legacy/ACTINV-DATA-NOTICE-v1.0.0.md"

    size = {n: (ASSETS / n).stat().st_size for n in staged}
    digest = {n: sha256(ASSETS / n) for n in staged}
    if digest[NPZ_NAME] != build["artifact"]["npz_sha256"]:
        raise SystemExit("npz hash drifted since the build record")
    if digest[INDEX_NAME] != build["artifact"]["index_sha256"]:
        raise SystemExit("index hash drifted since the build record")
    if digest[COV_NAME] != cov["artifact"]["npz_sha256"]:
        raise SystemExit("cov npz hash drifted since the covariance record")
    if digest[COV_INDEX_NAME] != cov["artifact"]["index_sha256"]:
        raise SystemExit("cov index hash drifted since the covariance record")

    new = [
        artifact("tendl-2025-patched-neutron-709g", "activation-library",
                 f"activation/{NPZ_NAME}", size[NPZ_NAME], digest[NPZ_NAME],
                 NPZ_NAME),
        artifact("tendl-2025-patched-neutron-709g-index",
                 "activation-index", f"activation/{INDEX_NAME}",
                 size[INDEX_NAME], digest[INDEX_NAME], INDEX_NAME),
        artifact("tendl-2025-patched-neutron-709g-covariance",
                 "covariance-sidecar", f"uncertainty/{COV_NAME}",
                 size[COV_NAME], digest[COV_NAME], COV_NAME),
        artifact("tendl-2025-patched-neutron-709g-covariance-index",
                 "covariance-index", f"uncertainty/{COV_INDEX_NAME}",
                 size[COV_INDEX_NAME], digest[COV_INDEX_NAME],
                 COV_INDEX_NAME),
        artifact("actinv-data-notice-v1-1", "notice",
                 "ACTINV-DATA-NOTICE.md", size[NOTICE_NAME],
                 digest[NOTICE_NAME], NOTICE_NAME, licence="notice"),
    ]
    artifacts.extend(new)

    decays = ["endfb-viii-0-decay", "jeff-3-3-decay"]
    notice_new = "actinv-data-notice-v1-1"
    notice_old = "actinv-data-notice-v1"
    bundles = catalog["bundles"]
    for bundle in bundles:
        if bundle["id"] == "tendl-2025-neutron":
            bundle["description"] = (
                "Superseded TENDL-2025 neutron activation data "
                "(upstream-confirmed emitted-state defect; kept for "
                "byte-compatible catalog references) with ENDF/B-VIII.0 "
                "and JEFF-3.3 decay data")
        elif bundle["id"] == "tendl-2025-neutron-covariance":
            bundle["description"] = (
                "Superseded unpatched neutron bundle with its matching "
                "MF=33 covariance sidecar; kept for byte-compatible "
                "catalog references")
        else:
            # proton/deuteron/alpha carry the current notice
            bundle["artifacts"] = [
                aid if aid != notice_old else notice_new
                for aid in bundle["artifacts"]
            ]
    bundles.insert(0, {
        "id": "tendl-2025-patched-neutron",
        "description": "Recommended TENDL-2025-derived neutron activation "
                       "data (Avila Labs remediation derivative, 44 leaked "
                       "ordinates zeroed; not an official TENDL release) "
                       "with ENDF/B-VIII.0 and JEFF-3.3 decay data",
        "projectile": "neutron",
        "groups": "fispact-709",
        "temperature_K": 293.6,
        "artifacts": ["tendl-2025-patched-neutron-709g",
                      "tendl-2025-patched-neutron-709g-index",
                      *decays, notice_new],
    })
    bundles.insert(1, {
        "id": "tendl-2025-patched-neutron-covariance",
        "description": "Recommended patched neutron bundle plus its "
                       "matching rebuilt MF=33 covariance sidecar",
        "projectile": "neutron",
        "groups": "fispact-709",
        "temperature_K": 293.6,
        "artifacts": ["tendl-2025-patched-neutron-709g",
                      "tendl-2025-patched-neutron-709g-index",
                      "tendl-2025-patched-neutron-709g-covariance",
                      "tendl-2025-patched-neutron-709g-covariance-index",
                      *decays, notice_new],
    })

    catalog_text = json.dumps(catalog, indent=2) + "\n"
    NEW_CATALOG.write_text(catalog_text)
    (ASSETS / CATALOG_NAME).write_text(catalog_text)

    names = [NPZ_NAME, INDEX_NAME, COV_NAME, COV_INDEX_NAME,
             NOTICE_NAME, CATALOG_NAME]
    (ASSETS / "SHA256SUMS").write_text(
        "".join(f"{sha256(ASSETS / n)}  {n}\n" for n in names))
    (ASSETS / "SIZES").write_text(
        "".join(f"{(ASSETS / n).stat().st_size}  {n}\n" for n in names))

    record = {
        "schema": "actinv-p25c-release-stage-1",
        "release_tag": RELEASE_TAG,
        "catalog_sha256": sha256(NEW_CATALOG),
        "notice_sha256": digest[NOTICE_NAME],
        "assets": {n: {"bytes": (ASSETS / n).stat().st_size,
                       "sha256": sha256(ASSETS / n)} for n in names},
        "activation": build["artifact"],
        "covariance": cov["artifact"],
        "activation_failures": build["failed_files"],
        "covariance_failures": cov["failed_files"],
    }
    RECORD.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    print(json.dumps(record["assets"], indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
