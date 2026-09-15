#!/usr/bin/env python3
"""P25c release build — full-corpus ``tendl-2025-patched`` artifact.

Runs one bounded directory build over the entire patched neutron corpus
(2,850 files) under the shipped 1.1.0 builder.  The builder fails closed
on any defective file; each failure is parsed from the error message,
ledgered with its source hash and failure detail, evicted to
``stage-failed/`` and the build retried.  The per-source checkpoint
cache makes retries incremental, so each round only rebuilds files not
yet checkpointed.

Resumable: rerun freely; staged, failed and cache state persist under
``target/p25c-release/``.  Writes ``results/p25c_release_build.json``
with the artifact/index hashes, the staged population and the complete
failure ledger.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
WORK = ROOT / "target" / "p25c-release"
STAGE = WORK / "stage"
FAILED = WORK / "stage-failed"
CACHE = WORK / "cache"
ACTINV = ROOT / "target" / "release" / "actinv"
PATCHED_ROOT = Path.home() / "nuclear-data" / "tendl-2025-patched" / "files" / "n"
PATCHED_MANIFEST = RESULTS / "g2_p25c_patched_manifest.sha256"

# P10-documented nonfinite repairs carried into the release source
# corpus.  The sealed tendl-2025-patched corpus equals the official
# archive outside the 44 enumerated ordinates, so it still carries the
# official Pb-208 NaN fields the strict builder fails closed on.  The
# shipped artifact instead builds the P10-pinned working file -- the
# exact bytes the data-v1.0.0 artifact used -- so the released source is
# (working corpus + P25c patch).  The sealed corpus is never modified.
P10_SOURCES = RESULTS / "g7_p10_neutron_sources.json"
WORKING_ROOT = Path.home() / "nuclear-data" / "tendl-2025" / "files" / "n-working"
CARRIED = {
    "n-Pb208.tendl": {
        "official_sha256":
            "32249bf71ee52a159ef8f94a4cb85d5c456aba13e1a4c4d9129c2304b6dc4137",
        "carried_sha256":
            "86788a14563ecdb844628a6a455864874ba5f5bca9b8142c10a59ea00df87c72",
    },
}

NPZ = WORK / "tendl-2025-patched-neutron-709g.npz"
RECORD = RESULTS / "p25c_release_build.json"
ROUND_TIMEOUT_S = 14400.0  # 4 h hard bound per build round

FAIL_RE = re.compile(r"(n-[A-Za-z]{1,3}\d{2,4}[a-z]?\.tendl)")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run_round() -> dict:
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            [str(ACTINV), "build-library", str(STAGE), str(NPZ),
             "--format", "tendl", "--projectile", "neutron",
             "--groups", "fispact-709", "--temperature-K", "293.6",
             "--workers", "2", "--cache", str(CACHE)],
            capture_output=True, text=True, timeout=ROUND_TIMEOUT_S,
            cwd=ROOT)
        return {"exit": proc.returncode,
                "seconds": round(time.monotonic() - t0, 3),
                "message": (proc.stderr.strip() + "\n"
                            + proc.stdout.strip())[-2000:]}
    except subprocess.TimeoutExpired:
        return {"exit": "timeout",
                "seconds": round(time.monotonic() - t0, 3),
                "message": f"timeout {ROUND_TIMEOUT_S}s"}


PROBE_TIMEOUT_S = 300.0  # per-file probe bound; timeouts stay staged
PROBE_WORKERS = 2  # shards; each shard runs --workers 1 (200% CPU cap)


def probe(src: Path, out: Path) -> dict:
    """Single-file bounded build sharing the directory-build cache.

    Equivalent verdict to the in-scan check: same builder, same options,
    same checkpoint cache.  A cache hit makes the probe near-instant, so
    probing every staged file costs real work only for files the
    directory rounds have not reached yet.
    """
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            [str(ACTINV), "build-library", str(src), str(out),
             "--format", "tendl", "--projectile", "neutron",
             "--groups", "fispact-709", "--temperature-K", "293.6",
             "--workers", "1", "--cache", str(CACHE)],
            capture_output=True, text=True, timeout=PROBE_TIMEOUT_S,
            cwd=ROOT)
        return {"exit": proc.returncode,
                "seconds": round(time.monotonic() - t0, 3),
                "message": (proc.stderr.strip() + "\n"
                            + proc.stdout.strip())[-800:]}
    except subprocess.TimeoutExpired:
        return {"exit": "timeout",
                "seconds": round(time.monotonic() - t0, 3),
                "message": f"probe timeout {PROBE_TIMEOUT_S}s"}


def evict(name: str, detail: dict) -> None:
    src = STAGE / name
    if not src.exists():
        return
    (FAILED / name).mkdir(exist_ok=True)
    (FAILED / name / "failure.json").write_text(
        json.dumps(detail, indent=1) + "\n")
    shutil.move(str(src), str(FAILED / name / name))


def probe_pass(manifest: dict) -> list[dict]:
    """Probe every staged file not already checkpointed; evict hard
    failures, keep timeouts.

    A probe timeout is not a builder rejection -- the file stays staged
    and the bounded directory round processes it.  Files whose source
    sha already has a cache checkpoint built successfully in a prior
    round and are skipped.  The probe list is split across
    ``PROBE_WORKERS`` shards running concurrently; each shard uses
    ``--workers 1`` so total demand stays within the cgroup CPU quota.
    Returns one row per file attempted.
    """
    cached_sources = set()
    for sidecar in CACHE.glob("*.json"):
        try:
            cached_sources.add(
                json.loads(sidecar.read_text())["source_sha256"])
        except (OSError, KeyError, ValueError):
            continue
    todo = []
    for i, path in enumerate(sorted(STAGE.iterdir()), 1):
        if not path.is_file():
            continue
        spec = CARRIED.get(path.name)
        known_sha = (spec["carried_sha256"] if spec
                     else manifest.get(path.name) or sha256(path))
        if known_sha not in cached_sources:
            todo.append(path.name)
    print(f"probe pass: {len(todo)} staged files lack checkpoints",
          flush=True)

    rows = []
    lock = threading.Lock()
    done = [0]

    def work(shard: int, names: list[str]) -> None:
        out = WORK / f"probe-out-{shard}.npz"
        for name in names:
            src = STAGE / name
            res = probe(src, out)
            out.unlink(missing_ok=True)
            if res["exit"] == 0:
                row = {**res, "file": name, "outcome": "ok"}
            elif res["exit"] == "timeout":
                row = {**res, "file": name, "outcome": "probe_timeout"}
                print(f"  probe {name}: timeout (left staged)",
                      flush=True)
            else:
                detail = {"file": name, "probe": True,
                          "message": res["message"],
                          "source_sha256": sha256(src)}
                evict(name, detail)
                row = {**res, "file": name, "outcome": "failed"}
                print(f"  probe {name}: evicted ({res['seconds']}s)",
                      flush=True)
            with lock:
                rows.append(row)
                done[0] += 1
                if done[0] % 25 == 0 or done[0] == len(todo):
                    failed = sum(
                        1 for r in rows if r["outcome"] == "failed")
                    print(f"probe progress {done[0]}/{len(todo)}: "
                          f"{failed} failed", flush=True)

    shards = [todo[s::PROBE_WORKERS] for s in range(PROBE_WORKERS)]
    threads = [
        threading.Thread(target=work, args=(s, shard), daemon=True)
        for s, shard in enumerate(shards)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return rows


def restore_stale_preevictions() -> int:
    """Return census-pre-evicted files to the stage for a real verdict.

    The P25 census ``file_failures`` list carries classes recorded under
    the earlier P18b builder lineage (e.g. ``tiny_absolute_discrepancy``,
    ``state_catalog_conflict``) that the shipped 1.1.0 builder accepts;
    G3's bounded builds on this binary proved several build fine.  The
    release artifact must contain exactly the files the shipped builder
    accepts, so stale predictions cannot veto them: each pre-evicted
    file is restaged and probed under the current builder.  Genuine
    failures get fresh failure entries with the current message.
    """
    restored = 0
    for d in sorted(FAILED.iterdir()):
        detail = d / "failure.json"
        payload = d / d.name
        if not detail.is_file() or not payload.is_file():
            continue
        try:
            info = json.loads(detail.read_text())
        except ValueError:
            continue
        if "pre_evicted_from" not in info:
            continue
        shutil.move(str(payload), str(STAGE / d.name))
        shutil.rmtree(d)
        restored += 1
    return restored


def carry_repairs() -> dict:
    """Stage the P10-pinned working-corpus bytes for carried repairs.

    Verifies both digests before copying: the sealed corpus file must be
    the expected official bytes, and the working source must match the
    P10-pinned working hash.  If the file was previously evicted, its
    failure ledger entry is annotated rather than erased.
    """
    evidence = json.loads(P10_SOURCES.read_text())
    changed = {e["name"]: e for e in evidence["working_manifest"]["changed"]}
    carried = {}
    for name, spec in CARRIED.items():
        record = changed.get(name)
        if record is None or record["official_sha256"] != spec["official_sha256"] \
                or record["working_sha256"] != spec["carried_sha256"]:
            raise SystemExit(f"P10 carried-repair record for {name} differs")
        sealed = PATCHED_ROOT / name
        if sha256(sealed) != spec["official_sha256"]:
            raise SystemExit(
                f"sealed corpus file {name} is not the expected official bytes")
        src = WORKING_ROOT / name
        if sha256(src) != spec["carried_sha256"]:
            raise SystemExit(
                f"carried source {name} differs from the P10-pinned working file")
        dst = STAGE / name
        if not dst.exists() or sha256(dst) != spec["carried_sha256"]:
            shutil.copyfile(src, dst)
        failure = FAILED / name / "failure.json"
        if failure.is_file():
            detail = json.loads(failure.read_text())
            detail["carried_repair"] = {
                "superseded": True,
                "carried_sha256": spec["carried_sha256"],
                "carried_source": str(src),
                "evidence": str(P10_SOURCES.relative_to(ROOT)),
            }
            failure.write_text(json.dumps(detail, indent=1) + "\n")
        carried[name] = {
            "official_sha256": spec["official_sha256"],
            "carried_sha256": spec["carried_sha256"],
        }
    return carried


def main() -> int:
    manifest = {}
    for line in PATCHED_MANIFEST.read_text().splitlines():
        d, n = line.split(None, 1)
        manifest[n.strip()] = d
    for d in (STAGE, FAILED, CACHE):
        d.mkdir(parents=True, exist_ok=True)

    # stage any patched-corpus file not already failed
    for name in sorted(manifest):
        src = PATCHED_ROOT / name
        if not src.is_file():
            raise SystemExit(f"manifest file missing: {name}")
        dst = STAGE / name
        if not dst.exists() and not (FAILED / name).exists():
            shutil.copyfile(src, dst)

    # Every staged file gets a real verdict from the shipped builder;
    # stale census predictions are restored to the stage first.
    restored = restore_stale_preevictions()
    if restored:
        print(f"restored {restored} stale census pre-evictions for "
              f"probing", flush=True)

    carried = carry_repairs()
    if carried:
        print(f"carried P10 repairs staged for {sorted(carried)}",
              flush=True)

    # Probe pass: single-file bounded builds over every staged file.
    # Cache hits are near-instant, so this reaches the un-scanned tail
    # without paying a full directory rescan per failure.  Idempotent:
    # prior probe verdicts persist via stage/failed/cache state.
    probes = probe_pass(manifest)
    prior = json.loads(RECORD.read_text()) if RECORD.is_file() else {}
    prior_probe = prior.get("probe_pass") or {}
    restored += prior.get("restored_stale_preevictions", 0)
    this_failed = sum(1 for r in probes if r["outcome"] == "failed")
    probe_summary = {
        "files_probed":
            len(probes) + prior_probe.get("files_probed", 0),
        "failed": this_failed + prior_probe.get("failed", 0),
        "probe_timeouts": sorted(
            set(prior_probe.get("probe_timeouts", []))
            | {r["file"] for r in probes
               if r["outcome"] == "probe_timeout"}),
        "passes": prior_probe.get("passes", 1 if prior_probe else 0) + 1,
    }
    print(f"probe pass done: {len(probes)} probed, "
          f"{this_failed} evicted this pass", flush=True)

    rounds = json.loads(RECORD.read_text()).get("rounds", []) \
        if RECORD.is_file() else []
    while True:
        res = run_round()
        res["round"] = len(rounds) + 1
        res["staged"] = len(list(STAGE.iterdir()))
        res["failed_so_far"] = len(list(FAILED.iterdir()))
        rounds.append(res)
        print(f"round {res['round']}: exit {res['exit']} "
              f"({res['seconds']}s) staged={res['staged']} "
              f"failed={res['failed_so_far']}", flush=True)
        if res["exit"] == 0:
            break
        m = FAIL_RE.search(res["message"])
        if res["exit"] == "timeout" or not m:
            rounds.append({"fatal": res["message"][-1500:]})
            break
        bad = m.group(1)
        src = STAGE / bad
        if not src.exists():
            rounds.append({"fatal": f"unparseable failure: {bad}",
                           "message": res["message"][-1500:]})
            break
        evict(bad, {"file": bad, "round": res["round"],
                    "message": res["message"][-800:],
                    "source_sha256": sha256(src)})
        print(f"  evicted {bad}", flush=True)

    index = NPZ.with_name(NPZ.stem + "_index.json")
    failure_ledger = {}
    for d in sorted(FAILED.iterdir()):
        f = d / "failure.json"
        if f.is_file():
            failure_ledger[d.name] = json.loads(f.read_text())

    # Ledger-derived count is authoritative for probe evictions; the
    # cumulative `failed` counter is a per-pass diagnostic.
    probe_summary["evicted"] = sum(
        1 for d in failure_ledger.values() if "probe" in d)

    idx = json.loads(index.read_text()) if index.is_file() else None
    record = {
        "schema": "actinv-p25c-release-build-1",
        "patched_manifest_sha256": sha256(PATCHED_MANIFEST),
        "carried_repairs": carried,
        "restored_stale_preevictions": restored,
        "probe_pass": probe_summary,
        "builder": str(ACTINV),
        "rounds": rounds,
        "staged_files": len(list(STAGE.iterdir())),
        "failed_files": len(failure_ledger),
        "failure_ledger": failure_ledger,
        "artifact": {
            "npz": str(NPZ),
            "npz_sha256": sha256(NPZ) if NPZ.is_file() else None,
            "index": str(index) if index.is_file() else None,
            "index_sha256": sha256(index) if index.is_file() else None,
            "index_targets": len(idx["targets"]) if idx else None,
            "n_rows": idx.get("n_rows") if idx else None,
            "catalog_liso_values": sorted(
                {e["liso"] for e in idx.get("state_catalog", [])})
            if idx else None,
        },
    }
    RECORD.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    print(json.dumps(record["artifact"], indent=1))
    return 0 if record["artifact"]["npz_sha256"] else 1


if __name__ == "__main__":
    sys.exit(main())
