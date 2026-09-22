#!/usr/bin/env python3
"""P42 G1 independent checker.

Re-derives the mechanism classification recorded in
results/g1_p42_mechanisms.json from the raw source files without
importing the classifier (controls/g1_p42_mechanisms.py). Implements
its own idx/DSV/ENDF/stdout parsers, re-hashes every sealed input,
re-runs the frozen classification rule per material context, recomputes
the case-evidence ratios, and rejects planted mutations.

Checks:
  1. sealed input digests recomputed == recorded
  2. census completeness: all 13 members, exactly one frozen class each
  3. per-member independent re-derivation matches recorded class
  4. trace integrity: half-lives, channel decompositions, decay-archive
     presence, case evidence re-verified
  5. mutation plants (class flip, member drop, trace tamper, vocab
     violation) must each fail
"""
import hashlib
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
WORK = Path.home() / "nuclear-data"
CASES = WORK / "p26b-work" / "p38-run" / "g2-cases-p39"
CENSUS = ROOT / "results" / "g1_p42_mechanisms.json"

NPZ = WORK / "p26b-work" / "p38-run" / "actinv_fendl32c_709_p39.npz"
INDEX = WORK / "p26b-work" / "p38-run" / "actinv_fendl32c_709_p39_index.json"
IDX = WORK / "p26b-work" / "g1-run" / "fendl32c_709.idx"
DSV = WORK / "alara-2.9.2" / "tools" / "ALARAJOYWrapper" / "cumulative_gendf_data.dsv"
DECAY = [ROOT / "actinv-data" / "v1.1.0" / "decay" / "endf-b-viii-0_decay.dat",
         ROOT / "actinv-data" / "v1.1.0" / "decay" / "jeff-3-3_decay.dat"]
P40_CLASSES = ROOT / "results" / "g2_p40_classes.json"
PROTOCOL = ROOT / "protocols" / "ACTINV-P42_PROTOCOL.md"

VOCAB = {"coverage_parent", "decay_file_gap", "decay_feed_missing",
         "burn_out", "conversion_content", "alara_heritage",
         "true_defect", "unresolved"}

EL = {22: "Ti", 23: "V", 24: "Cr", 25: "Mn", 26: "Fe", 27: "Co",
      28: "Ni", 29: "Cu", 41: "Nb", 42: "Mo", 45: "Rh", 47: "Ag",
      48: "Cd", 73: "Ta", 74: "W", 20: "Ca", 21: "Sc"}
Z_OF = {v: k for k, v in EL.items()}

EMIT = {
    "g": (0, 1), "p": (-1, 0), "a": (-2, -3), "2n": (0, -1),
    "2p": (-2, -1), "np": (-1, -1), "pn": (-1, -1), "d": (-1, -1),
    "t": (-1, -2), "h": (-2, -2), "3n": (0, -2), "4n": (0, -3),
    "nd": (-1, -1), "2np": (-1, -2), "n2p": (-2, -2), "nh": (-2, -3),
    "na": (-2, -4), "nt": (-1, -3), "pt": (-2, -3), "pd": (-2, -2),
    "pa": (-3, -4), "2a": (-4, -7), "da": (-3, -5), "3p": (-3, -2),
}

MAT_PARENTS = {
    "fe": {260540, 260560, 260570, 260580},
    "ag": {471070, 471090}, "co": {270590},
    "cr": {240500, 240520, 240530, 240540},
    "cu": {290630, 290650}, "mn": {250550},
    "mo": {420920, 420940, 420950, 420960, 420970, 420980, 421000},
    "nb": {410930}, "ta": {731800, 731810}, "v": {230500, 230510},
}
ABUND = {260540: 0.05845, 260560: 0.91754, 260570: 0.02119,
         260580: 0.00282, 471070: 0.51839, 471090: 0.48161,
         270590: 1.0, 240500: 0.04345, 240520: 0.83789,
         240530: 0.09501, 240540: 0.02365, 290630: 0.6915,
         290650: 0.3085, 250550: 1.0, 420920: 0.14649,
         420940: 0.09187, 420950: 0.15873, 420960: 0.16673,
         420970: 0.09582, 420980: 0.24292, 421000: 0.09744,
         410930: 1.0, 731800: 0.00012, 731810: 0.99988,
         230500: 0.0025, 230510: 0.9975}
MATERIALS = {"fe": MAT_PARENTS["fe"]}
for el, ps in MAT_PARENTS.items():
    if el != "fe":
        MATERIALS[f"fe_{el}"] = MAT_PARENTS["fe"] | ps

EXPECTED_MEMBERS = {"Ti55", "V55", "Ti53", "Cr57", "Mn59",
                    "Cr51", "Fe59", "Cr55", "Mn57", "Fe53",
                    "Cr56", "Mn58", "V52"}

FAILS = []


def fail(msg):
    FAILS.append(msg)
    print(f"FAIL {msg}")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sym_kza(s):
    m = re.match(r"([A-Z][a-z]?)(\d+)(?:m(\d+))?$", s)
    return Z_OF[m.group(1)] * 10000 + int(m.group(2)) * 10 + int(m.group(3) or 0)


def kza_sym(k):
    z, a, m = k // 10000, (k // 10) % 1000, k % 10
    return f"{EL.get(z, 'Z' + str(z))}{a}" + (f"m{m}" if m else "")


def load_sources():
    """Independent parsers: idx daughter table, DSV channel rows,
    artifact emission rows, decay-archive ZA presence."""
    # idx: (parent_kza, daughter_kza, emitted_label, half-life)
    edges, hl = {}, {}
    cur = None
    for line in open(IDX):
        f = line.split()
        if not f:
            continue
        if len(f) == 4 and line[0] not in " \t" and f[0].lstrip("-").isdigit():
            cur = int(f[0])
            try:
                hl[cur] = float(f[2])
            except ValueError:
                hl[cur] = None
        elif cur is not None and f[0].lstrip("-").isdigit():
            edges.setdefault(cur, []).append((int(f[0]),
                                             f[1] if len(f) > 1 else "?"))
    # DSV
    dsv = []
    for line in open(DSV):
        f = line.split()
        if len(f) < 20 or not f[0].isdigit() or not f[1].isdigit():
            continue
        vals = []
        for x in f[4:]:
            try:
                vals.append(float(x))
            except ValueError:
                vals.append(0.0)
        if len(vals) == 709:
            dsv.append((int(f[0]), int(f[1]), f[3], np.array(vals)))
    # artifact
    npz = np.load(NPZ)
    index = json.load(open(INDEX))
    targets = index["targets"]
    art_emit = {}
    for i, (t, mt, zap, lfs, lmf) in enumerate(npz["rows"]):
        art_emit.setdefault(int(zap), []).append(
            (int(targets[t]["za"]) * 10, int(mt), npz["sig"][i]))
    # decay archives: ZA*100 field at line start
    dec = []
    for dp in DECAY:
        s = set()
        for line in open(dp, errors="replace"):
            m = re.match(r"\s*(\d)\.(\d{6})[+-]\d", line[:13])
            if m:
                s.add(int(m.group(1) + m.group(2)) // 100)
        dec.append(s)
    return edges, hl, dsv, art_emit, dec


def mass_balanced(parent, daughter, labels):
    pz, pa = parent // 10000, (parent // 10) % 1000
    dz, da = daughter // 10000, (daughter // 10) % 1000
    for lab in labels.split(","):
        base = lab.rstrip("*")
        if base == "x" or base not in EMIT:
            continue
        dze, dae = EMIT[base]
        if pz + dze == dz and pa + dae == da:
            return True
    return False


def derive_class(member, edges, dsv, art_emit, dec, fns, parents):
    """Independent re-derivation of the frozen rule."""
    mk = sym_kza(member)
    zaid = (mk // 10000) * 1000 + (mk // 10) % 1000
    if not (zaid in dec[0] or zaid in dec[1]):
        return "decay_file_gap"
    real = [(p, lb, float(np.dot(xs, fns)))
            for p, d, lb, xs in dsv
            if d == mk and mass_balanced(p, d, lb)]
    phant = [(p, lb, float(np.dot(xs, fns)))
             for p, d, lb, xs in dsv
             if d == mk and not mass_balanced(p, d, lb)]
    emit = [(pk, mt, float(np.dot(s, fns[::-1])))
            for pk, mt, s in art_emit.get(zaid, [])]
    real_w = sum(v * ABUND.get(p, 0) for p, l, v in real if p in parents)
    ph_w = sum(v * ABUND.get(p, 0) for p, l, v in phant if p in parents)
    act_w = sum(v * ABUND.get(p, 0) for p, m, v in emit if p in parents)
    if act_w == 0 and real_w == 0 and ph_w > 0:
        return "alara_heritage"
    if ph_w > 0.5 * max(real_w, 1e-30):
        return "alara_heritage"
    by_pa, by_pc = {}, {}
    for p, l, v in real:
        if p in parents:
            by_pa[p] = by_pa.get(p, 0.0) + v
    for p, m, v in emit:
        if p in parents:
            by_pc[p] = by_pc.get(p, 0.0) + v
    for p in by_pa:
        if p in by_pc and max(by_pa[p], by_pc[p]) > 0 \
                and abs(by_pc[p] - by_pa[p]) / max(by_pa[p], by_pc[p]) > 0.02 \
                and by_pa[p] * ABUND.get(p, 0) > 0.01 * real_w:
            return "conversion_content"
    # phantom-fed decay parents within material
    for p, dl in edges.items():
        for d, e in dl:
            if d != mk or e != "*D":
                continue
            pr = sum(float(np.dot(xs, fns)) * ABUND.get(pp, 0)
                     for pp, dd, lb, xs in dsv
                     if dd == p and pp in parents
                     and mass_balanced(pp, dd, lb))
            ph = sum(float(np.dot(xs, fns)) * ABUND.get(pp, 0)
                     for pp, dd, lb, xs in dsv
                     if dd == p and pp in parents
                     and not mass_balanced(pp, dd, lb))
            if ph > 0 and ph > 0.1 * pr:
                return "alara_heritage"
    art_zaid = {pk // 10 for zl in art_emit.values() for pk, _, _ in zl}
    cov = [1 for p, l, v in real if p // 10 not in art_zaid
           and p in parents and v * ABUND.get(p, 0) > 1e-3]
    if cov:
        return "coverage_parent"
    if act_w > 0 and real_w > 0:
        return "conversion_content"
    return "unresolved"


def stdout_activity(case: Path, member: str, half_life_s: float):
    """Independent stdout parser: locate the Specific Activity table and
    match the isotope row by its printed t_1/2 (unique per member)."""
    text = (case / "case.stdout").read_text(errors="replace")
    seg = text.split("*** Specific Activity [Bq/g] ***")[1]
    lines = seg.splitlines()
    hdr = next(i for i, l in enumerate(lines)
               if l.strip().startswith("isotope"))
    for line in lines[hdr + 1:]:
        if line.lstrip().startswith("total") or "====" in line \
                and line.strip().startswith("="):
            if line.lstrip().startswith("total"):
                break
            continue
        f = line.split()
        if len(f) >= 4 and f[0].startswith("-"):
            try:
                t12 = float(f[1])
            except ValueError:
                continue
            if abs(t12 - half_life_s) / max(half_life_s, 1e-30) < 1e-3:
                return float(f[3])  # shutdown column
    return 0.0


def run_checks(census_path: Path) -> list[str]:
    global FAILS
    FAILS = []
    d = json.loads(Path(census_path).read_text())

    # 1. sealed input digests
    inputs = {"artifact_npz": NPZ, "artifact_index": INDEX,
              "alara_idx": IDX, "alara_dsv": DSV,
              "p40_classes": P40_CLASSES, "protocol": PROTOCOL,
              "decay_endf_b_viii_0": DECAY[0], "decay_jeff_3_3": DECAY[1]}
    for k, p in inputs.items():
        rec = d["input_sha256"].get(k)
        if rec != sha256(p):
            fail(f"input digest mismatch: {k}")

    # 2. completeness
    members = {c["member"] for c in d["census"]}
    if members != EXPECTED_MEMBERS:
        fail(f"member set mismatch: {sorted(members ^ EXPECTED_MEMBERS)}")
    if len(d["census"]) != 13:
        fail(f"census size {len(d['census'])} != 13")
    for c in d["census"]:
        if c["primary_class"] not in VOCAB:
            fail(f"{c['member']}: class {c['primary_class']} not in vocab")

    # 3+4. independent re-derivation
    edges, hl, dsv, art_emit, dec = load_sources()
    fns = np.loadtxt(CASES / "fe_ag100000wppm__fns_709__3600s" / "flux.txt")
    for c in d["census"]:
        m, mk = c["member"], c["kza"]
        # half-life check
        if hl.get(mk) is None or abs(c["alara_half_life_s"] - hl[mk]) > 1e-6 * max(hl[mk], 1):
            fail(f"{m}: half-life mismatch")
        # decay presence
        zaid = (mk // 10000) * 1000 + (mk // 10) % 1000
        for i, key in enumerate(("endf_b_viii_0", "jeff_3_3")):
            got = zaid in dec[i]
            if got != c["decay_archive_present"][key]:
                fail(f"{m}: decay presence mismatch {key}")
        # per-channel sigma.phi re-verification: multiset match of
        # DSV rows to this member (duplicates share (parent,label))
        for sec, physical in (("alara_real_channels", True),
                              ("alara_phantom_channels", False)):
            rec = sorted(ch["sig_phi_fns"] for ch in c[sec])
            got = sorted(float(np.dot(xs, fns))
                         for p, dd, lb, xs in dsv
                         if dd == mk
                         and mass_balanced(p, dd, lb) == physical)
            if len(rec) != len(got):
                fail(f"{m}: {sec} count {len(rec)} != {len(got)}")
            else:
                for a_, b_ in zip(rec, got):
                    if abs(a_ - b_) > 1e-6 * max(abs(b_), 1e-30):
                        fail(f"{m}: {sec} sigma.phi multiset differs "
                             f"({a_} vs {b_})")
                        break
            for ch in c[sec]:
                if mass_balanced(ch["parent_kza"], mk,
                                 ch["label"]) != physical:
                    fail(f"{m}: mass-balance flag wrong for "
                         f"{ch['parent']} {ch['label']}")
        # artifact rows: multiset of sigma.phi over rows emitting member
        rec = sorted(e["sig_phi_fns"] for e in c["actinv_emission_rows"])
        got = sorted(float(np.dot(s, fns[::-1]))
                     for pk, mt, s in art_emit.get(zaid, []))
        if len(rec) != len(got):
            fail(f"{m}: actinv_emission_rows {len(rec)} != {len(got)}")
        else:
            for a_, b_ in zip(rec, got):
                if abs(a_ - b_) > 1e-6 * max(abs(b_), 1e-30):
                    fail(f"{m}: actinv sigma.phi multiset differs")
                    break
        # re-derive class per material and match worst-case selection
        per_mat = {mat: derive_class(m, edges, dsv, art_emit, dec,
                                     fns, ps)
                   for mat, ps in MATERIALS.items()}
        if per_mat != c["per_material_class"]:
            diff = {k: (per_mat[k], c["per_material_class"][k])
                    for k in per_mat
                    if per_mat[k] != c["per_material_class"][k]}
            fail(f"{m}: per-material classes diverge {diff}")
        # case evidence re-verification
        for cn, ev in c["case_evidence"].items():
            cd = CASES / cn
            if not (cd / "out.json").exists():
                continue
            a = stdout_activity(cd, m, hl[mk])
            oj = json.loads((cd / "out.json").read_text())
            av = oj["steps"][0]["activity_Bq_per_g"].get(m, 0.0)
            if abs(a - ev["alara"]) > 1e-4 * max(abs(ev["alara"]), 1e-30):
                fail(f"{m} {cn}: alara {a} != recorded {ev['alara']}")
            if abs(av - ev["actinv"]) > 1e-9 * max(abs(ev["actinv"]), 1e-30):
                fail(f"{m} {cn}: actinv {av} != recorded {ev['actinv']}")
            # recorded ratio must be consistent with the two arms
            r = ev["ratio"]
            if a > 0:
                if not isinstance(r, float) or \
                        abs(r - av / a) > 1e-6 * max(abs(av / a), 1e-30):
                    fail(f"{m} {cn}: ratio {r} != actinv/alara")
            elif r not in (None, "actinv_only"):
                fail(f"{m} {cn}: ratio {r} inconsistent with alara=0")
        # primary class = per-material class of worst-divergence case
        worst, wmag = None, 0.0
        for cn, ev in c["case_evidence"].items():
            mag = abs(ev["actinv"] - ev["alara"])
            if mag > wmag:
                wmag, worst = mag, cn
        if worst is not None:
            mm = re.match(r"fe_([a-z]+)\d+wppm", worst)
            mat = "fe_" + mm.group(1) if mm else "fe"
            expected = per_mat.get(mat, "unresolved")
        else:
            best, bmat = 0.0, "fe"
            for mat, ps in MATERIALS.items():
                w = sum(v * ABUND.get(p, 0) for p, d, lb, xs in dsv
                        if d == mk and p in ps
                        for v in [float(np.dot(xs, fns))])
                if w > best:
                    best, bmat = w, mat
            expected = per_mat.get(bmat, "unresolved")
        if expected != c["primary_class"]:
            fail(f"{m}: primary_class {c['primary_class']} != "
                 f"re-derived {expected}")
    return FAILS


def mutations():
    """Every planted mutation must fail."""
    tmp = Path(tempfile.mkdtemp(prefix="p42g1-"))
    base = json.loads(CENSUS.read_text())
    results = {}

    # M1: flip a class
    mut = json.loads(json.dumps(base))
    mut["census"][0]["primary_class"] = "true_defect"
    p = tmp / "m1.json"; p.write_text(json.dumps(mut))
    results["class_flip"] = len(run_checks(p)) > 0

    # M2: drop a member
    mut = json.loads(json.dumps(base))
    mut["census"] = mut["census"][1:]
    p = tmp / "m2.json"; p.write_text(json.dumps(mut))
    results["member_drop"] = len(run_checks(p)) > 0

    # M3: tamper a channel sigma.phi
    mut = json.loads(json.dumps(base))
    if mut["census"][5]["alara_real_channels"]:
        mut["census"][5]["alara_real_channels"][0]["sig_phi_fns"] *= 10
    p = tmp / "m3.json"; p.write_text(json.dumps(mut))
    results["trace_tamper"] = len(run_checks(p)) > 0

    # M4: out-of-vocabulary class
    mut = json.loads(json.dumps(base))
    mut["census"][0]["primary_class"] = "invented_class"
    p = tmp / "m4.json"; p.write_text(json.dumps(mut))
    results["vocab_violation"] = len(run_checks(p)) > 0

    # M5: corrupt a case-evidence value (first nonzero entry)
    mut = json.loads(json.dumps(base))
    done = False
    for c in mut["census"]:
        for k, ev in c["case_evidence"].items():
            if ev["actinv"] > 0:
                ev["actinv"] *= 100
                done = True
                break
        if done:
            break
    p = tmp / "m5.json"; p.write_text(json.dumps(mut))
    results["case_tamper"] = len(run_checks(p)) > 0

    # M6: flip a per-material class while keeping primary
    mut = json.loads(json.dumps(base))
    mut["census"][0]["per_material_class"]["fe_cr"] = "unresolved"
    p = tmp / "m6.json"; p.write_text(json.dumps(mut))
    results["material_class_flip"] = len(run_checks(p)) > 0

    shutil.rmtree(tmp, ignore_errors=True)
    return results


def main():
    fails = run_checks(CENSUS)
    if fails:
        print(f"\nG1 CHECK FAIL ({len(fails)} findings)")
        return 1
    mut = mutations()
    bad = [k for k, v in mut.items() if not v]
    print("\nMutations:", json.dumps(mut))
    if bad:
        print(f"G1 CHECK FAIL: undetected mutations {bad}")
        return 1
    print("\nG1 CHECK PASS: 13 members, classes independently re-derived, "
          "all digests verified, 6/6 mutations rejected")
    return 0


if __name__ == "__main__":
    sys.exit(main())
