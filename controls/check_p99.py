#!/usr/bin/env python3
"""P99 checker (protocols/ACTINV-P99_PROTOCOL.md): isomer labels without decay data --
no merged physical states.

    python3 controls/check_p99.py build    # fmt/clippy/test/release -> target/p99/g1_summary.json
    python3 controls/check_p99.py g2       # proton build WITH --decay, byte-identical -> g2_summary.json
    python3 controls/check_p99.py g3       # proton build WITHOUT --decay, G3(a)/(b)/(c) -> g3_summary.json
    python3 controls/check_p99.py verdict  # assembles G0-G4 -- run by the lead, not by a subagent

Run order: build, g2, g3, then verdict (verdict also needs G4's CI replay log, produced
separately -- it is not computed here).

Every cargo step below runs under the memory-capped systemd-run wrapper (CG). `actinv
build-library` invocations run under the same wrapper: the proton build keeps the whole
TENDL-2025 proton set (2853 files) and its 162-group cross sections resident.
"""
from __future__ import annotations

import gc
import hashlib
import json
import os
import re
import subprocess
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
MAIN = Path.home() / "Documents" / "actinv"
WORK = ROOT / "target" / "p99"
REF = WORK / "ref_actinv"
CAND = ROOT / "target" / "release" / "actinv"
PROTOCOL = ROOT / "protocols" / "ACTINV-P99_PROTOCOL.md"
VERDICT = ROOT / "results" / "p99_verdict.json"

PROTON_DATA = Path.home() / "nuclear-data" / "tendl-2025" / "files" / "p"
DECAY_PRIMARY = MAIN / "actinv-data" / "v1.1.0" / "decay" / "endf-b-viii-0_decay.dat"
DECAY_FALLBACK = MAIN / "actinv-data" / "v1.1.0" / "decay" / "jeff-3-3_decay.dat"

ENV = {"RAYON_NUM_THREADS": "1", "PATH": "/usr/bin:/bin", "HOME": str(Path.home())}

CG = ["systemd-run", "--user", "--scope", "-q", "-p", "MemoryMax=6G",
      "-p", "MemorySwapMax=0", "-p", "TasksMax=128", "-p", "CPUQuota=300%", "--"]


def cg_env() -> dict:
    """Environment for a CG (systemd-run --user --scope)-wrapped call: systemd-run itself needs
    DBUS_SESSION_BUS_ADDRESS/XDG_RUNTIME_DIR to reach the user bus, so this is the restricted
    ENV plus whatever the current session already has for those (falls back to the
    conventional /run/user/<uid> values when unset, e.g. under a detached/backgrounded shell)."""
    env = dict(ENV)
    uid = os.getuid()
    env["DBUS_SESSION_BUS_ADDRESS"] = os.environ.get("DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{uid}/bus")
    env["XDG_RUNTIME_DIR"] = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{uid}")
    return env

# The P99 unit tests added to crates/actinv-data/src/builder.rs's test module.
P99_UNIT_TESTS = (
    "p99_anchored_collision_rank_row_becomes_leakage_catalog_row_unchanged",
    "p99_cross_mt_rank_collision_without_anchor_leaks_all",
    "p99_agreeing_rank_row_in_shared_group_is_kept",
    "p99_excitation_less_rank_row_in_shared_group_becomes_leakage",
    "p99_same_evaluation_with_decay_data_is_untouched",
)

# Named builder-identity fields that legitimately differ between the reference and candidate
# binaries built from different source (the P99 patch itself): builder_fingerprint hashes
# builder.rs's own bytes among other source files (crates/actinv-data/src/builder.rs ::
# builder_fingerprint()), so it necessarily differs once builder.rs changes. Everything else in
# the index -- including sha256_npz -- must match when decay data is supplied (P99 G2).
G2_IDENTITY_FIELDS = {"builder_fingerprint"}


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def registered() -> bool:
    return f"{sha(PROTOCOL)}  protocols/{PROTOCOL.name}" in (ROOT / "protocols/protocol_hash.txt").read_text()


# ---------------------------------------------------------------------- build (G1)

def cmd_build() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    (ROOT / "target" / "preflight-tmp").mkdir(parents=True, exist_ok=True)
    base_env = dict(os.environ, CARGO_BUILD_JOBS="3", TMPDIR=str(ROOT / "target" / "preflight-tmp"))
    steps = [
        ("fmt", ["cargo", "fmt", "--check"]),
        ("clippy", ["cargo", "clippy", "-p", "actinv-core", "-p", "actinv-data", "-p", "actinv-cli",
                    "--all-targets", "--all-features", "--locked", "--", "-D", "warnings"]),
        ("test", ["cargo", "test", "--release", "-p", "actinv-core", "-p", "actinv-data"]),
        ("release", ["cargo", "build", "--release", "-p", "actinv-cli", "--bin", "actinv"]),
    ]
    build_log = []
    test_text = ""
    rc = {}
    for name, cmd in steps:
        p = subprocess.run(CG + cmd, cwd=ROOT, capture_output=True, text=True, env=base_env)
        build_log.append(f"=== {name} ===\n{p.stdout}\n{p.stderr}\n{name} rc={p.returncode}\n")
        rc[name] = p.returncode
        if name == "test":
            test_text = p.stdout + p.stderr
        print(name, "rc=", p.returncode, flush=True)
        if p.returncode != 0:
            break
    (WORK / "build.log").write_text("\n".join(build_log))
    (WORK / "test.txt").write_text(test_text)
    unit = {name: re.search(rf"^test \S*{re.escape(name)} \.\.\. ok$", test_text, re.M) is not None
            for name in P99_UNIT_TESTS}
    g1 = {
        "rc": rc,
        "pass": all(rc.get(k) == 0 for k in ("fmt", "clippy", "test", "release")) and all(unit.values()),
        "unit_test_ok": unit,
        "candidate_sha256": sha(CAND) if CAND.exists() else None,
        "reference_sha256": sha(REF) if REF.exists() else None,
    }
    (WORK / "g1_summary.json").write_text(json.dumps(g1, indent=1, sort_keys=True))
    print(json.dumps(g1, indent=1))
    return 0 if g1["pass"] else 1


# ---------------------------------------------------------------------------- G2

def strip_identity(doc):
    if isinstance(doc, dict):
        return {k: strip_identity(v) for k, v in doc.items() if k not in G2_IDENTITY_FIELDS}
    if isinstance(doc, list):
        return [strip_identity(v) for v in doc]
    return doc


def cmd_g2() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    if not REF.exists() or not CAND.exists():
        sys.exit(f"reference ({REF}) or candidate ({CAND}) binary is missing")
    if not PROTON_DATA.exists():
        sys.exit(f"proton source not found: {PROTON_DATA}")
    builds = {}
    npz_paths = {}
    idx_paths = {}
    for tag, binary in (("ref", REF), ("cand", CAND)):
        out = WORK / f"g2_proton_{tag}.npz"
        idx = WORK / f"g2_proton_{tag}_index.json"
        out.unlink(missing_ok=True)
        idx.unlink(missing_ok=True)
        cmd = CG + [str(binary), "build-library", str(PROTON_DATA), str(out),
                    "--format", "tendl", "--projectile", "proton", "--groups", "fispact-162",
                    "--temperature-K", "0", "--workers", "3", "--continue-on-error", "true",
                    "--decay", str(DECAY_PRIMARY), "--decay-fallback", str(DECAY_FALLBACK)]
        p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, env=cg_env())
        builds[tag] = {"returncode": p.returncode, "stdout_tail": p.stdout[-800:], "stderr_tail": p.stderr[-800:]}
        npz_paths[tag] = out
        idx_paths[tag] = idx
        print("g2 build", tag, "rc=", p.returncode, flush=True)
    if builds["ref"]["returncode"] != 0 or builds["cand"]["returncode"] != 0:
        result = {"pass": False, "builds": builds}
        (WORK / "g2_summary.json").write_text(json.dumps(result, indent=1, sort_keys=True))
        print(json.dumps(result, indent=1))
        return 1

    npz_identical = npz_paths["ref"].exists() and npz_paths["cand"].exists() and \
        sha(npz_paths["ref"]) == sha(npz_paths["cand"])

    index_identical_apart_from_identity = False
    index_diff = None
    if idx_paths["ref"].exists() and idx_paths["cand"].exists():
        ref_idx = json.loads(idx_paths["ref"].read_text())
        cand_idx = json.loads(idx_paths["cand"].read_text())
        ref_stripped = strip_identity(ref_idx)
        cand_stripped = strip_identity(cand_idx)
        index_identical_apart_from_identity = ref_stripped == cand_stripped
        if not index_identical_apart_from_identity:
            # Surface a small, targeted diff rather than the whole ~280 MB doc: top-level keys
            # that differ, and (if targets differ) the first differing target's za/liso/file.
            top_diff = [k for k in ref_stripped.keys() | cand_stripped.keys()
                        if ref_stripped.get(k) != cand_stripped.get(k)]
            first_target_diff = None
            if ref_stripped.get("targets") != cand_stripped.get("targets"):
                for i, (rt, ct) in enumerate(zip(ref_stripped.get("targets", []), cand_stripped.get("targets", []))):
                    if rt != ct:
                        first_target_diff = {"index": i, "za": rt.get("za"), "liso": rt.get("liso"),
                                              "file": rt.get("file")}
                        break
            index_diff = {"top_level_keys_differing": top_diff, "first_target_diff": first_target_diff}
        del ref_idx, cand_idx, ref_stripped, cand_stripped
        gc.collect()

    ok = npz_identical and index_identical_apart_from_identity
    result = {
        "pass": ok,
        "npz_identical": npz_identical,
        "index_identical_apart_from_identity": index_identical_apart_from_identity,
        "identity_fields_excluded": sorted(G2_IDENTITY_FIELDS),
        "index_diff": index_diff,
        "builds": builds,
        "ref_npz_sha256": sha(npz_paths["ref"]) if npz_paths["ref"].exists() else None,
        "cand_npz_sha256": sha(npz_paths["cand"]) if npz_paths["cand"].exists() else None,
    }
    (WORK / "g2_summary.json").write_text(json.dumps(result, indent=1, sort_keys=True))
    print(json.dumps({k: v for k, v in result.items() if k != "builds"}, indent=1))
    return 0 if ok else 1


# ---------------------------------------------------------------------------- G3

def excitation_tolerance(a: float, b: float) -> float:
    return max(1.0, 5e-6 * max(abs(a), abs(b)))


def excitations_agree(a: float, b: float) -> bool:
    return abs(a - b) <= excitation_tolerance(a, b)


def find_violations(mappings: list) -> list:
    """Re-implements the P99 rule in Python, independently of the Rust builder: given one
    target's state_mappings (list of lightweight dicts with mt/zap/raw_lfs/
    mapping_excitation_eV/canonical_liso/decision), returns the list of mapping indices the
    rule flags as collisions -- i.e. the rows that must become
    `no_catalog_rank_collision_to_leakage`."""
    groups = defaultdict(list)
    for i, m in enumerate(mappings):
        zap = m["zap"]
        liso = m["canonical_liso"]
        if zap is not None and zap > 0 and liso is not None and liso > 0:
            groups[(zap, liso)].append(i)

    violations = []
    for idxs in groups.values():
        anchored = [i for i in idxs if mappings[i]["decision"] != "no_catalog_rank_mapped_lfs"]
        rank = [i for i in idxs if mappings[i]["decision"] == "no_catalog_rank_mapped_lfs"]
        if not rank:
            continue
        if anchored:
            for i in rank:
                e = mappings[i]["mapping_excitation_eV"]
                agrees = e is not None and any(
                    mappings[a]["mapping_excitation_eV"] is not None
                    and excitations_agree(e, mappings[a]["mapping_excitation_eV"])
                    for a in anchored
                )
                if not agrees:
                    violations.append(i)
        elif len(rank) >= 2:
            all_agree = True
            for pi in range(len(rank)):
                if not all_agree:
                    break
                for pj in range(pi + 1, len(rank)):
                    ea = mappings[rank[pi]]["mapping_excitation_eV"]
                    eb = mappings[rank[pj]]["mapping_excitation_eV"]
                    if ea is None or eb is None or not excitations_agree(ea, eb):
                        all_agree = False
                        break
            if not all_agree:
                violations.extend(rank)
    return violations


def load_light_targets(index_path: Path) -> list:
    """Parses the (~280 MB) index JSON once and immediately discards everything but the
    per-target identity and a slimmed state_mappings list (mt/zap/raw_lfs/
    mapping_excitation_eV/canonical_liso/decision only), to keep resident memory modest."""
    doc = json.loads(index_path.read_text())
    out = []
    for t in doc["targets"]:
        mappings = [
            {
                "mt": m["mt"],
                "zap": m["zap"],
                "raw_lfs": m["raw_lfs"],
                "mapping_excitation_eV": m.get("mapping_excitation_eV"),
                "canonical_liso": m.get("canonical_liso"),
                "decision": m["decision"],
            }
            for m in t.get("state_mappings", [])
        ]
        out.append({"za": t["za"], "liso": t["liso"], "file": t["file"], "state_mappings": mappings})
    del doc
    gc.collect()
    return out


def load_npz_rows(path: Path):
    with zipfile.ZipFile(path) as z:
        with z.open("rows.npy") as f:
            rows = np.lib.format.read_array(f)
        with z.open("sig.npy") as f:
            sig = np.lib.format.read_array(f)
    return rows, sig


def cmd_g3() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    if not REF.exists() or not CAND.exists():
        sys.exit(f"reference ({REF}) or candidate ({CAND}) binary is missing")
    if not PROTON_DATA.exists():
        sys.exit(f"proton source not found: {PROTON_DATA}")

    builds = {}
    npz_paths = {}
    idx_paths = {}
    for tag, binary in (("ref", REF), ("cand", CAND)):
        out = WORK / f"g3_proton_{tag}.npz"
        idx = WORK / f"g3_proton_{tag}_index.json"
        out.unlink(missing_ok=True)
        idx.unlink(missing_ok=True)
        # No --decay / --decay-fallback here: this is the build that exercises step-3 rank
        # mapping (P99's defect surface).
        cmd = CG + [str(binary), "build-library", str(PROTON_DATA), str(out),
                    "--format", "tendl", "--projectile", "proton", "--groups", "fispact-162",
                    "--temperature-K", "0", "--workers", "3", "--continue-on-error", "true"]
        p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, env=cg_env())
        builds[tag] = {"returncode": p.returncode, "stdout_tail": p.stdout[-800:], "stderr_tail": p.stderr[-800:]}
        npz_paths[tag] = out
        idx_paths[tag] = idx
        print("g3 build", tag, "rc=", p.returncode, flush=True)
    if builds["ref"]["returncode"] != 0 or builds["cand"]["returncode"] != 0:
        result = {"pass": False, "builds": builds}
        (WORK / "g3_summary.json").write_text(json.dumps(result, indent=1, sort_keys=True))
        print(json.dumps(result, indent=1))
        return 1

    # ---- index-only part: (a) candidate has no remaining violations, (b) the candidate's
    # leaked rows are exactly the reference's violating set.
    ref_targets = load_light_targets(idx_paths["ref"])
    cand_targets = load_light_targets(idx_paths["cand"])
    if len(ref_targets) != len(cand_targets):
        result = {"pass": False, "error": f"target count differs: ref={len(ref_targets)} cand={len(cand_targets)}"}
        (WORK / "g3_summary.json").write_text(json.dumps(result, indent=1, sort_keys=True))
        print(json.dumps(result, indent=1))
        return 1
    cand_by_key = {(t["za"], t["liso"]): t for t in cand_targets}
    # rows.npy's target_index column is the position in the index's own `targets` list, which
    # load_light_targets preserved in order -- build the lookup now, before the heavier
    # state_mappings-bearing lists are dropped below, rather than re-parsing either ~280 MB
    # index a second time.
    ref_tmap = {i: (t["za"], t["liso"]) for i, t in enumerate(ref_targets)}
    cand_tmap = {i: (t["za"], t["liso"]) for i, t in enumerate(cand_targets)}

    a_violations_in_candidate = 0
    b_mismatched_targets = []
    rows_rerouted = 0
    groups_affected = set()
    targets_affected = set()
    # Per target (za, liso), the set of (mt, zap, raw_lfs) identities the reference-side rule
    # flags as violations -- used again, unmodified, by the G3(c) multiset check below.
    violating_identity_by_target: dict = {}

    for rt in ref_targets:
        key = (rt["za"], rt["liso"])
        ct = cand_by_key.get(key)
        if ct is None:
            b_mismatched_targets.append({"za": rt["za"], "liso": rt["liso"], "error": "missing in candidate"})
            continue

        ref_violation_idxs = find_violations(rt["state_mappings"])
        ref_violating_rows = [rt["state_mappings"][i] for i in ref_violation_idxs]
        ref_violating_identities = {(m["mt"], m["zap"], m["raw_lfs"]) for m in ref_violating_rows}
        violating_identity_by_target[key] = ref_violating_identities

        cand_leaked_identities = {
            (m["mt"], m["zap"], m["raw_lfs"])
            for m in ct["state_mappings"]
            if m["decision"] == "no_catalog_rank_collision_to_leakage"
        }

        # (a): the candidate's own state_mappings must have zero remaining violations.
        a_violations_in_candidate += len(find_violations(ct["state_mappings"]))

        # (b): bijection between the reference-derived violating set and what the candidate
        # actually routed to leakage, for this target.
        if ref_violating_identities != cand_leaked_identities:
            b_mismatched_targets.append({
                "za": rt["za"], "liso": rt["liso"],
                "ref_only": sorted(ref_violating_identities - cand_leaked_identities),
                "cand_only": sorted(cand_leaked_identities - ref_violating_identities),
            })
        if ref_violating_identities:
            targets_affected.add(key)
            rows_rerouted += len(ref_violating_identities)
            for m in ref_violating_rows:
                # groups_affected is keyed by the collision group itself: this target plus the
                # product (zap, canonical LISO) the row collided over -- not the evaluated
                # target's own liso (rt["liso"]/`key[1]`, a different field entirely).
                groups_affected.add((key, m["zap"], m["canonical_liso"]))

    b_ok = not b_mismatched_targets
    a_ok = a_violations_in_candidate == 0
    del ref_targets, cand_targets, cand_by_key
    gc.collect()

    # ---- npz part: (c) per target, candidate rows == reference rows with exactly the
    # violating identities' rows relabeled to (zap 0, lfs 0, lmf -3), as multisets including
    # sigma bytes. We never try to align individual mapping entries to row *positions*
    # (ambiguous when an anchor and a colliding rank row share the same final (mt, zap, lfs,
    # lmf) tuple, which happens whenever they come from the same MT); instead we take the
    # symmetric difference of the two full per-target multisets and require it to be exactly a
    # bijection between "old tuple + sigma" (removed, only in ref) and "(mt, 0, 0, -3) + the
    # same sigma" (added, only in cand) -- sigma is preserved byte-for-byte through leakage
    # (only zap/lfs/lmf change), so it is the only information needed to pair the two sides
    # without positional assumptions.
    ref_rows, ref_sig = load_npz_rows(npz_paths["ref"])
    cand_rows, cand_sig = load_npz_rows(npz_paths["cand"])

    def per_target_multiset(rows: np.ndarray, sig: np.ndarray, tmap: dict) -> dict:
        buckets: dict = defaultdict(Counter)
        for i in range(rows.shape[0]):
            t_idx, mt, zap, lfs, lmf = (int(x) for x in rows[i])
            key = tmap.get(t_idx)
            if key is None:
                continue
            sig_bytes = sig[i].tobytes()
            buckets[key][(mt, zap, lfs, lmf, sig_bytes)] += 1
        return buckets

    ref_buckets = per_target_multiset(ref_rows, ref_sig, ref_tmap)
    cand_buckets = per_target_multiset(cand_rows, cand_sig, cand_tmap)
    del ref_rows, ref_sig, cand_rows, cand_sig
    gc.collect()

    c_mismatched_targets = []
    all_keys = set(ref_buckets) | set(cand_buckets)
    for key in all_keys:
        ref_c = ref_buckets.get(key, Counter())
        cand_c = cand_buckets.get(key, Counter())
        removed = ref_c - cand_c  # present (more often) in ref only
        added = cand_c - ref_c    # present (more often) in cand only
        expected_count = len(violating_identity_by_target.get(key, set()))
        removed_total = sum(removed.values())
        added_total = sum(added.values())
        if removed_total != expected_count or added_total != expected_count:
            c_mismatched_targets.append({
                "za": key[0], "liso": key[1],
                "expected_rerouted": expected_count,
                "removed_total": removed_total, "added_total": added_total,
            })
            continue
        if expected_count == 0:
            continue
        # Bijection check: every removed element's sigma must appear, with the same mt and the
        # same multiplicity, among the added elements at (mt, 0, 0, -3).
        removed_by_mt_sig = Counter()
        for (mt, _zap, _lfs, _lmf, sig_bytes), count in removed.items():
            removed_by_mt_sig[(mt, sig_bytes)] += count
        added_by_mt_sig = Counter()
        bad_added_shape = False
        for (mt, zap, lfs, lmf, sig_bytes), count in added.items():
            if (zap, lfs, lmf) != (0, 0, -3):
                bad_added_shape = True
            added_by_mt_sig[(mt, sig_bytes)] += count
        if bad_added_shape or removed_by_mt_sig != added_by_mt_sig:
            c_mismatched_targets.append({
                "za": key[0], "liso": key[1],
                "expected_rerouted": expected_count,
                "bijection_failed": True,
                "bad_added_shape": bad_added_shape,
            })

    c_ok = not c_mismatched_targets

    ok = a_ok and b_ok and c_ok
    result = {
        "pass": ok,
        "a_no_remaining_violations_in_candidate": {
            "pass": a_ok, "violation_count_in_candidate": a_violations_in_candidate,
        },
        "b_leaked_rows_match_reference_violating_set": {
            "pass": b_ok, "mismatched_targets_sample": b_mismatched_targets[:20],
            "n_mismatched_targets": len(b_mismatched_targets),
        },
        "c_npz_multiset_identical_after_simulated_relabel": {
            "pass": c_ok, "mismatched_targets_sample": c_mismatched_targets[:20],
            "n_mismatched_targets": len(c_mismatched_targets),
        },
        "rows_rerouted": rows_rerouted,
        "groups_affected": len(groups_affected),
        "targets_affected": len(targets_affected),
        "builds": builds,
        "ref_npz_sha256": sha(npz_paths["ref"]),
        "cand_npz_sha256": sha(npz_paths["cand"]),
    }
    (WORK / "g3_summary.json").write_text(json.dumps(result, indent=1, sort_keys=True))
    print(json.dumps({k: v for k, v in result.items() if k != "builds"}, indent=1))
    return 0 if ok else 1


# ------------------------------------------------------------------- verdict

def cmd_verdict() -> int:
    """Assembles G0-G4. Not run by the implementing agent."""
    g1 = json.loads((WORK / "g1_summary.json").read_text())
    g2 = json.loads((WORK / "g2_summary.json").read_text())
    g3 = json.loads((WORK / "g3_summary.json").read_text())
    replay = WORK / "ci_replay_summary.log"
    steps = re.findall(r"^STEP (\d+) (\S+)$", replay.read_text(), re.M) if replay.exists() else []
    failed = [name for code, name in steps if code != "0"]
    g4 = {"pass": bool(steps) and not failed, "steps": len(steps), "failed": failed}
    verdict = {
        "protocol": "ACTINV-P99",
        "inputs": {
            "protocol_sha256": sha(PROTOCOL),
            "candidate_sha256": sha(CAND) if CAND.exists() else None,
            "reference_sha256": sha(REF) if REF.exists() else None,
        },
        "G0": {"pass": registered()},
        "G1": g1, "G2": g2, "G3": g3, "G4": g4,
    }
    verdict["pass"] = all(verdict[g].get("pass") for g in ("G0", "G1", "G2", "G3", "G4"))
    VERDICT.parent.mkdir(parents=True, exist_ok=True)
    VERDICT.write_text(json.dumps(verdict, indent=1, sort_keys=True) + "\n")
    print(json.dumps(verdict, indent=1))
    return 0


if __name__ == "__main__":
    fn = {"build": cmd_build, "g2": cmd_g2, "g3": cmd_g3, "verdict": cmd_verdict}
    if len(sys.argv) < 2 or sys.argv[1] not in fn:
        sys.exit(__doc__)
    sys.exit(fn[sys.argv[1]]())
