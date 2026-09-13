#!/usr/bin/env python3
"""P25-G0: opening, authority and seal.

Verifies, without importing any ACTINV production or audit module:

- the frozen P25 protocol digest and that the working tree descends
  from the P25 opening commit;
- every prior verdict file exists and carries its expected verdict
  string (26 verdicts, including honest failures ``P17-FAIL``,
  ``P18-FAIL`` and ``P18b-FAIL`` asserted verbatim — P25 never
  rewrites them);
- the four-corpus TENDL-2025 source manifest: all 11,400 files still
  hash to their sealed digests — the diagnosis population is provably
  the sealed one;
- the P18b session's pinned evidence digests still match the result
  files on disk;
- the P22 release-candidate record exists with ``release_ready`` true;
- the release boundary: workspace version is 1.1.0, and no ``v1.1*``
  tag exists — the release hold stands;
- the candidate pin: binary/module digests, version, head commit,
  rustc, kernel and cgroup limits.

Writes ``results/g0_p25_seals.json``.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import platform
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results/g0_p25_seals.json"
PROTOCOL = ROOT / "protocols/ACTINV-P25_PROTOCOL.md"
AMENDMENT_A = ROOT / "protocols/ACTINV-P25_AMENDMENT_A.md"
CORPUS_MANIFEST = ROOT / "results/p18b_source_manifest.json.gz"
SESSION_P18B = ROOT / "results/session_p18b.json"
P22_RC = ROOT / "results/g4_p22_release_candidate.json"
ACTINV = ROOT / "target/release/actinv"
PYMODULE = ROOT / "python/target/release/libactinv.so"

OPENING_COMMIT = "77dfaef8c1cb4067c8cc8e1b7c4a686123161ea7"

EXPECTED_VERDICTS = {
    "verdict_p2.json": "P2-CONDITIONAL",
    "verdict_p3.json": "P3-FAIL",
    "verdict_p3b.json": "P3b-PASS",
    "verdict_p4.json": "P4-FAIL",
    "verdict_p4b.json": "P4b-PASS",
    "verdict_p5.json": "P5-PASS",
    "verdict_p6.json": "P6-CONDITIONAL",
    "verdict_p7.json": "P7-CONDITIONAL",
    "verdict_p8.json": "P8-CONDITIONAL",
    "verdict_p9.json": "P9-CONDITIONAL",
    "verdict_p10.json": "P10-CONDITIONAL",
    "verdict_p11.json": "P11-CONDITIONAL",
    "verdict_p12.json": "P12-CONDITIONAL",
    "verdict_p13.json": "P13-PASS",
    "verdict_p14.json": "P14-CLOSED-BELOW-THRESHOLD",
    "verdict_p15.json": "P15-PASS",
    "verdict_p16.json": "P16-CONDITIONAL",
    "verdict_p17.json": "P17-FAIL",
    "verdict_p18.json": "P18-FAIL",
    "verdict_p18b.json": "P18b-FAIL",
    "verdict_cb1.json": "CB1-COMPLETE",
    "verdict_p19.json": "P19-PASS",
    "verdict_p20.json": "P20-PASS",
    "verdict_p21.json": "P21-PASS",
    "verdict_p22.json": "P22-PASS",
    "verdict_p23.json": "P23-PASS",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def command(args: list[str]) -> str:
    return subprocess.run(
        args, cwd=ROOT, text=True, capture_output=True, timeout=120
    ).stdout.strip()


def cgroup_record() -> dict[str, str | None]:
    record: dict[str, str | None] = {}
    for name in ("memory.max", "memory.swap.max", "cpu.max", "pids.max"):
        node = Path("/sys/fs/cgroup") / name
        record[name] = node.read_text().strip() if node.exists() else None
    record["self_cgroup"] = Path("/proc/self/cgroup").read_text().strip()
    return record


def corpus_seal() -> dict[str, object]:
    import gzip

    manifest = json.loads(gzip.open(CORPUS_MANIFEST, "rt").read())
    corpora = manifest["corpora"]
    out: dict[str, object] = {"manifest_sha256": sha256(CORPUS_MANIFEST)}
    mismatched: list[str] = []
    missing: list[str] = []
    counts: dict[str, int] = {}
    corpus_root = Path(
        "/home/connoravila/nuclear-data/tendl-2025/files"
    )
    # The sealed neutron population is the n-working tree: the manifest
    # records source_manifest "staging/TENDL-n-working.manifest.json".
    # files/n/ currently carries the official n-Pb208.tendl, whose MF=3
    # tables contain literal NaN tokens; the sealed corpus used the
    # recorded minimal repair (byte_identical: false, official_sha256
    # differs). n-Pb208 appears in zero held-out rows.
    code = {"neutron": "n-working", "proton": "p", "deuteron": "d", "alpha": "a"}
    for projectile, entry in corpora.items():
        files = entry["files"]
        counts[projectile] = len(files)
        for rec in files:
            path = corpus_root / code[projectile] / rec["name"]
            if not path.exists():
                missing.append(f"{projectile}/{rec['name']}")
                continue
            if sha256(path) != rec["source_sha256"]:
                mismatched.append(f"{projectile}/{rec['name']}")
    out["file_counts"] = counts
    out["missing"] = missing[:20]
    out["mismatched"] = mismatched[:20]
    out["missing_count"] = len(missing)
    out["mismatched_count"] = len(mismatched)
    out["pass"] = not missing and not mismatched
    return out


def main() -> None:
    verdicts: dict[str, dict[str, object]] = {}
    for name, expected in EXPECTED_VERDICTS.items():
        path = ROOT / "results" / name
        observed = None
        if path.exists():
            observed = json.loads(path.read_text(encoding="utf-8")).get("verdict")
        verdicts[name] = {"expected": expected, "observed": observed}

    # P18b session seals: every pinned evidence digest must still match.
    session = json.loads(SESSION_P18B.read_text(encoding="utf-8"))
    pinned = session["evidence_sha256"]
    p18b_seals: dict[str, dict[str, object]] = {}
    evidence_files = {
        "g0_check": "results/g0_p18b_check.json",
        "g1_check": "results/g1_p18b_check.json",
        "g2_check": "results/g2_p18b_check.json",
        "g3_check": "results/g3_p18b_check.json",
        "g4_diagnostics": "results/g4_p18b_diagnostics.json",
        "g5_heldout": "results/g5_p18b_heldout.json",
        "g5_run1_coverage_limited": "results/g5_p18b_heldout_run1_coverage_limited.json",
    }
    for name, rel in evidence_files.items():
        path = ROOT / rel
        actual = sha256(path) if path.exists() else None
        p18b_seals[name] = {
            "file": rel,
            "expected_sha256": pinned.get(name),
            "observed_sha256": actual,
            "matches": actual == pinned.get(name),
        }

    corpus = corpus_seal()

    rc = json.loads(P22_RC.read_text(encoding="utf-8"))
    rc_ok = bool(rc.get("pass")) and bool(
        (rc.get("decision") or {}).get("release_ready")
    )

    tags = [
        t for t in command(["git", "tag", "--list", "v1.1*"]).splitlines() if t
    ]
    workspace_version = re.search(
        r'version\s*=\s*"([^"]+)"',
        (ROOT / "Cargo.toml").read_text(encoding="utf-8"),
    ).group(1)
    release_boundary = workspace_version == "1.1.0" and rc_ok and not tags

    head = command(["git", "rev-parse", "HEAD"])
    is_descendant = (
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", OPENING_COMMIT, "HEAD"],
            cwd=ROOT,
        ).returncode
        == 0
    )
    candidate_sha = sha256(ACTINV) if ACTINV.exists() else None
    module_sha = sha256(PYMODULE) if PYMODULE.exists() else None
    version_run = subprocess.run(
        [str(ACTINV), "--version"], text=True, capture_output=True, timeout=60
    )
    rustc_run = subprocess.run(
        ["rustc", "-Vv"], text=True, capture_output=True, timeout=60
    )

    record = {
        "schema": "actinv-p25-seals-1",
        "protocol_sha256": sha256(PROTOCOL),
        "amendment_a_sha256": sha256(AMENDMENT_A) if AMENDMENT_A.exists() else None,
        "opening_commit": OPENING_COMMIT,
        "head_descends_from_opening": is_descendant,
        "candidate": {
            "actinv_binary_sha256": candidate_sha,
            "python_module_sha256": module_sha,
            "actinv_version": version_run.stdout.strip(),
            "rustc": rustc_run.stdout.splitlines()[0] if rustc_run.stdout else None,
            "kernel": platform.release(),
            "platform": platform.platform(),
            "head_commit": head,
            "cgroup": cgroup_record(),
        },
        "verdicts": verdicts,
        "p18b_evidence_seals": p18b_seals,
        "corpus_seal": corpus,
        "release_boundary": {
            "workspace_version": workspace_version,
            "p22_rc_passes": rc_ok,
            "v1_1_tags": tags,
            "hold_stands": release_boundary,
        },
        "p18b_session": {
            "protocol_sha256": session["protocol_sha256"],
            "opening_commit": session["opening_commit"],
            "verdict": session["verdict"],
            "audit": session["audit"],
        },
    }
    record["pass"] = bool(
        record["head_descends_from_opening"]
        and all(v["observed"] == v["expected"] for v in verdicts.values())
        and all(s["matches"] for s in p18b_seals.values())
        and corpus["pass"]
        and release_boundary
        and version_run.returncode == 0
        and session["verdict"] == "P18b-FAIL"
    )
    RESULT.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "pass": record["pass"],
        "verdicts_mismatched": [n for n, v in verdicts.items()
                                if v["observed"] != v["expected"]],
        "p18b_seals_broken": [n for n, s in p18b_seals.items()
                              if not s["matches"]],
        "corpus": {"missing": corpus["missing_count"],
                   "mismatched": corpus["mismatched_count"],
                   "counts": corpus["file_counts"]},
        "release_boundary": release_boundary,
    }, indent=2))
    raise SystemExit(0 if record["pass"] else 1)


if __name__ == "__main__":
    main()
