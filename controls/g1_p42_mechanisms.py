#!/usr/bin/env python3
"""P42 G1: mechanism census for the 13 P40-named divergence nuclides.

For every member of the two open P40 classes
(short_lived_products_absent_in_actinv + common_nuclide_magnitude) this
control produces exactly one primary class from the frozen P42 vocabulary,
backed by a hash-pinned trace assembled from:

  * ALARA compiled idx (nuclide -> daughter rows; "*D" = decay edge)
  * ALARA conversion DSV per-(parent,daughter,label) 709-group rows
  * ACTINV artifact (target, MT, ZAP) production rows
  * a mass-balance test per emitted-particle label: a row whose daughter
    cannot be reached by the labeled emission is a conversion phantom
    (ALARA heritage), carrying real cross-section to a false nuclide
  * FENDL-3.2c ENDF-6 ground-truth sigma.phi for the pivotal shared
    channels, independent of both arms
  * per-case ALARA-vs-ACTINV activity comparison on representative cases
  * pinned decay-archive presence for each member and its feed parents

Output: results/g1_p42_mechanisms.json
"""
import hashlib
import json
import re
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
WORK = Path.home() / "nuclear-data"
CASES = WORK / "p26b-work" / "p38-run" / "g2-cases-p39"
OUT = ROOT / "results" / "g1_p42_mechanisms.json"

NPZ = WORK / "p26b-work" / "p38-run" / "actinv_fendl32c_709_p39.npz"
INDEX = WORK / "p26b-work" / "p38-run" / "actinv_fendl32c_709_p39_index.json"
IDX = WORK / "p26b-work" / "g1-run" / "fendl32c_709.idx"
DSV = WORK / "alara-2.9.2" / "tools" / "ALARAJOYWrapper" / "cumulative_gendf_data.dsv"
ENDF_DIR = WORK / "fendl-3.2c" / "endf"
DECAY = [ROOT / "actinv-data" / "v1.1.0" / "decay" / "endf-b-viii-0_decay.dat",
         ROOT / "actinv-data" / "v1.1.0" / "decay" / "jeff-3-3_decay.dat"]
P40_CLASSES = ROOT / "results" / "g2_p40_classes.json"
PROTOCOL = ROOT / "protocols" / "ACTINV-P42_PROTOCOL.md"

EL = {1: "H", 2: "He", 3: "Li", 4: "Be", 5: "B", 6: "C", 7: "N", 8: "O",
      20: "Ca", 21: "Sc", 22: "Ti", 23: "V", 24: "Cr", 25: "Mn", 26: "Fe",
      27: "Co", 28: "Ni", 29: "Cu", 41: "Nb", 42: "Mo", 45: "Rh", 46: "Pd",
      47: "Ag", 48: "Cd", 73: "Ta", 74: "W"}
Z_OF = {v: k for k, v in EL.items()}

MEMBERS = {
    "short_lived_products_absent_in_actinv": ["Ti55", "V55", "Ti53", "Cr57", "Mn59"],
    "common_nuclide_magnitude": ["Cr51", "Fe59", "Cr55", "Mn57", "Fe53",
                                 "Cr56", "Mn58", "V52"],
}

# daughter = parent + n_in - emitted;  label -> (dZ, dA) daughter-minus-parent
EMIT = {
    "g": (0, 1), "p": (-1, 0), "a": (-2, -3), "2n": (0, -1),
    "2p": (-2, -1), "np": (-1, -1), "pn": (-1, -1), "d": (-1, -1),
    "t": (-1, -2), "h": (-2, -2), "3n": (0, -2), "4n": (0, -3),
    "nd": (-1, -1), "2np": (-1, -2), "n2p": (-2, -2), "nh": (-2, -3),
    "na": (-2, -4), "nt": (-1, -3), "pt": (-2, -3), "pd": (-2, -2),
    "pa": (-3, -4), "2a": (-4, -7), "da": (-3, -5), "3p": (-3, -2),
}

VOCAB = {"coverage_parent", "decay_file_gap", "decay_feed_missing",
         "burn_out", "conversion_content", "alara_heritage",
         "true_defect", "unresolved"}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def kza(z: int, a: int, m: int = 0) -> int:
    return z * 10000 + a * 10 + m


def kza_sym(k: int) -> str:
    z, a, m = k // 10000, (k // 10) % 1000, k % 10
    return f"{EL.get(z, 'Z' + str(z))}{a}" + (f"m{m}" if m else "")


def sym_kza(s: str) -> int:
    mm = re.match(r"([A-Z][a-z]?)(\d+)(?:m(\d+))?$", s)
    return kza(Z_OF[mm.group(1)], int(mm.group(2)), int(mm.group(3) or 0))


def kza_zaid(k: int) -> int:
    return (k // 10000) * 1000 + (k // 10) % 1000


def parse_idx(path: Path):
    """ALARA idx: header 'kza nDaughters halfLife offset' at column 0,
    then tabbed 'daughterKZA emitted offset' rows."""
    edges, hl = {}, {}
    cur = None
    for line in open(path):
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
            edges.setdefault(cur, []).append(
                (int(f[0]), f[1] if len(f) > 1 else "?"))
    return edges, hl


def parse_dsv(path: Path):
    """ALARAJOYWrapper cumulative DSV rows:
    'parentKZA daughterKZA <mt-field> <emitted-label> <709 floats>'.
    MT fields may carry '*' overflow and are kept as leading digits."""
    out = []
    for line in open(path):
        f = line.split()
        if len(f) < 20 or not f[0].isdigit() or not f[1].isdigit():
            continue
        vals = []
        for x in f[4:]:
            try:
                vals.append(float(x))
            except ValueError:
                vals.append(0.0)
        if len(vals) != 709:
            continue
        m = re.match(r"\d+", f[2])
        out.append({"parent": int(f[0]), "daughter": int(f[1]),
                    "mt": int(m.group()) if m else None,
                    "label": f[3], "xs": np.array(vals)})
    return out


def emitted_ok(parent_kza: int, daughter_kza: int, labels: str) -> dict:
    """Mass-balance test: does ANY comma-label physically reach the
    daughter from the parent?"""
    pz, pa = parent_kza // 10000, (parent_kza // 10) % 1000
    dz, da = daughter_kza // 10000, (daughter_kza // 10) % 1000
    allowed = []
    for lab in labels.split(","):
        base = lab.rstrip("*")
        if base == "x":
            continue  # gas bookkeeping
        if base in EMIT:
            dz_e, da_e = EMIT[base]
            allowed.append({"label": lab,
                            "expected_kza": kza(pz + dz_e, pa + da_e)})
    ok = any(a["expected_kza"] % 10000 // 10 == da and
             a["expected_kza"] // 10000 == dz for a in allowed)
    return {"physical": ok, "allowed": [a["expected_kza"] for a in allowed]}


def _num(s):
    s = s.strip().replace(" ", "").replace("D", "E")
    if not re.match(r"^[+-]?\d", s):
        return None
    if "E" not in s and ("+" in s[1:] or "-" in s[1:]):
        s = re.sub(r"([+-])(\d+)$", r"E\1\2", s)
    try:
        return float(s)
    except ValueError:
        return None


def parse_endf_tab1(lines, i0):
    nr = int(float(_num(lines[i0 + 1][44:55]) or 0))
    np_ = int(float(_num(lines[i0 + 1][55:66]) or 0))
    vals, i = [], i0 + 2
    while len(vals) < 2 * nr + 2 * np_ and i < len(lines):
        l = lines[i].ljust(80)
        try:
            mf, mt = int(l[70:72]), int(l[72:75])
        except ValueError:
            break
        if mf == 0 or mt == 0:
            break
        for j in range(0, 66, 11):
            v = _num(l[j:j + 11])
            if v is not None:
                vals.append(v)
        i += 1
    xy = vals[2 * nr:2 * nr + 2 * np_]
    return np.array(xy[0::2]), np.array(xy[1::2])


def fendl_channel(endf_glob: str, mts, bounds, flux_asc):
    """Sum ENDF MF3 sections for the given MT list onto the group grid;
    returns sigma.phi against the case flux (ascending order)."""
    files = sorted(ENDF_DIR.glob(endf_glob))
    if not files:
        return None
    lines = open(files[0]).read().splitlines()
    gm = np.sqrt(bounds[:-1] * bounds[1:])
    total = np.zeros(len(gm))
    found = []
    for i, l in enumerate(lines):
        l2 = l.ljust(80)
        try:
            mf, mt = int(l2[70:72]), int(l2[72:75])
        except ValueError:
            continue
        if mf == 3 and mt in mts and mt not in found:
            found.append(mt)
            E, s = parse_endf_tab1(lines, i)
            if len(E) > 1:
                total += np.interp(gm, E, s, left=0, right=0)
    return {"file": files[0].name, "mts": found,
            "sig_phi": float(np.dot(total, flux_asc))}


def case_flux_asc(case: Path) -> np.ndarray:
    return np.loadtxt(case / "flux.txt")[::-1]


def alara_activity(case: Path, name: str, hl: dict):
    """First-cooling-time specific activity of `name`. Rows print as
    '-NN' under implicit element headers; disambiguate by the printed
    t_1/2 column, which is unique per member nuclide."""
    text = (case / "case.stdout").read_text(errors="replace")
    seg = text.split("*** Specific Activity [Bq/g] ***")[1]
    lines = seg.splitlines()
    hdr = next(i for i, l in enumerate(lines)
               if l.strip().startswith("isotope"))
    target = hl.get(sym_kza(name))
    for line in lines[hdr + 1:]:
        if line.lstrip().startswith("total"):
            break
        f = line.split()
        if len(f) >= 4 and f[0].startswith("-"):
            try:
                t12 = float(f[1])
            except ValueError:
                continue
            if target and abs(t12 - target) / max(target, 1e-30) < 1e-3:
                return float(f[3])  # shutdown column
    return 0.0


def actinv_activity(case: Path, name: str):
    d = json.loads((case / "out.json").read_text())
    return d["steps"][0]["activity_Bq_per_g"].get(name, 0.0)


def main() -> int:
    inputs = {"artifact_npz": NPZ, "artifact_index": INDEX,
              "alara_idx": IDX, "alara_dsv": DSV,
              "p40_classes": P40_CLASSES, "protocol": PROTOCOL,
              "decay_endf_b_viii_0": DECAY[0], "decay_jeff_3_3": DECAY[1]}
    digests = {k: sha256(v) for k, v in inputs.items()}

    edges, hl = parse_idx(IDX)
    dsv = parse_dsv(DSV)
    npz = np.load(NPZ)
    rows, sig, bounds = npz["rows"], npz["sig"], npz["bounds"]
    index = json.load(open(INDEX))
    targets = index["targets"]
    art_zaid = {t["za"] for t in targets}

    # decay-archive ZA presence (ENDF float field at line start carries
    # ZA*10+isomer as 'd.ddddddsd'; normalize to ZAID)
    decay_zas = {}
    for dp in DECAY:
        found = set()
        for line in open(dp, errors="replace"):
            mm = re.match(r"\s*(\d)\.(\d{6})[+-]\d", line[:13])
            if mm:
                found.add(int(mm.group(1) + mm.group(2)) // 100)
        decay_zas[dp.stem] = found

    # reference fluxes (DSV order = descending energy; artifact ascending)
    fns = np.loadtxt(CASES / "fe_ag100000wppm__fns_709__3600s" / "flux.txt")
    irdff = np.loadtxt(CASES / "fe_ag100000wppm__irdff_sp_mat9861_709__3600s"
                       / "flux.txt")
    fns_asc, irdff_asc = fns[::-1], irdff[::-1]

    # nuclide-level parent sets per corpus impurity element (KZA*10 form)
    MAT_PARENTS = {
        "fe": {260540, 260560, 260570, 260580},
        "ag": {471070, 471090}, "co": {270590},
        "cr": {240500, 240520, 240530, 240540},
        "cu": {290630, 290650}, "mn": {250550},
        "mo": {420920, 420940, 420950, 420960, 420970, 420980, 421000},
        "nb": {410930}, "ta": {731800, 731810}, "v": {230500, 230510},
    }
    all_parents = set().union(*MAT_PARENTS.values())
    # natural atom fractions for abundance weighting (corpus elements)
    ABUND = {260540: 0.05845, 260560: 0.91754, 260570: 0.02119,
             260580: 0.00282, 471070: 0.51839, 471090: 0.48161,
             270590: 1.0, 240500: 0.04345, 240520: 0.83789,
             240530: 0.09501, 240540: 0.02365, 290630: 0.6915,
             290650: 0.3085, 250550: 1.0, 420920: 0.14649,
             420940: 0.09187, 420950: 0.15873, 420960: 0.16673,
             420970: 0.09582, 420980: 0.24292, 421000: 0.09744,
             410930: 1.0, 731800: 0.00012, 731810: 0.99988,
             230500: 0.0025, 230510: 0.9975}
    # each corpus case material = fe base + one impurity element
    MATERIALS = {"fe": MAT_PARENTS["fe"]}
    for el, ps in MAT_PARENTS.items():
        if el != "fe":
            MATERIALS[f"fe_{el}"] = MAT_PARENTS["fe"] | ps

    def channels_to(member_kza):
        """All DSV production rows to member_kza, split by mass balance,
        flux-weighted under both reference spectra."""
        real, phantom = [], []
        for r in dsv:
            if r["daughter"] != member_kza:
                continue
            bal = emitted_ok(r["parent"], r["daughter"], r["label"])
            rec = {"parent": kza_sym(r["parent"]),
                   "parent_kza": r["parent"], "mt": r["mt"],
                   "label": r["label"],
                   "sig_phi_fns": float(np.dot(r["xs"], fns)),
                   "sig_phi_irdff": float(np.dot(r["xs"], irdff)),
                   "parent_in_artifact": (r["parent"] // 10) in art_zaid,
                   "parent_in_corpus": r["parent"] in all_parents}
            (real if bal["physical"] else phantom).append(rec)
        return real, phantom

    def actinv_emit(zaid):
        out = []
        for i, (t, mt, zap, lfs, lmf) in enumerate(rows):
            if int(zap) != zaid:
                continue
            tk = int(targets[t]["za"]) * 10
            out.append({"parent": kza_sym(tk), "mt": int(mt),
                        "parent_in_corpus": tk in all_parents,
                        "sig_phi_fns": float(np.dot(sig[i], fns_asc)),
                        "sig_phi_irdff": float(np.dot(sig[i], irdff_asc))})
        return out

    def decay_feeds(member_kza):
        """Decay parents of member_kza, each annotated with its own
        ALARA production decomposition (one level up)."""
        out = []
        for p, dl in edges.items():
            for d, e in dl:
                if d == member_kza and e == "*D":
                    pr, ph = channels_to(p)
                    out.append({
                        "parent": kza_sym(p), "parent_kza": p,
                        "parent_sig_phi_fns_real":
                            sum(c["sig_phi_fns"] for c in pr),
                        "parent_sig_phi_fns_phantom":
                            sum(c["sig_phi_fns"] for c in ph),
                        "parent_decay_present": {
                            "endf_b_viii_0": kza_zaid(p) in
                                decay_zas["endf-b-viii-0_decay"],
                            "jeff_3_3": kza_zaid(p) in
                                decay_zas["jeff-3-3_decay"]}})
        return out

    # per-case activity evidence: pick the case set that exercised each
    # member's divergence (FNS Fe+Ag for absent-class + magnitude, plus
    # Mn- and Cr-bearing and IRDFF variants)
    EVIDENCE_CASES = [
        "fe_ag100000wppm__fns_709__3600s",
        "fe_mn100000wppm__fns_709__3600s",
        "fe_cr100000wppm__fns_709__3600s",
        "fe_v100000wppm__fns_709__3600s",
        "fe_cr100000wppm__irdff_sp_mat9861_709__3600s",
    ]

    # FENDL-3.2c ground truth for the pivotal shared channels
    truth = {
        "Fe54_alpha_lumped_MT800_823": fendl_channel(
            "*Fe-54*", range(800, 850), bounds, fns_asc),
        "Fe54_MT16_2n": fendl_channel(
            "*Fe-54*", [16], bounds, fns_asc),
        "Mn55_alpha_lumped": fendl_channel(
            "*Mn-55*", range(800, 850), bounds, fns_asc),
    }

    def wsum(chans, parents, key="sig_phi_fns"):
        """Abundance-weighted sigma.phi over rows on `parents`."""
        return sum(c[key] * ABUND.get(c["parent_kza"], 0.0)
                   for c in chans if c["parent_kza"] in parents)

    def act_wsum(emitted, parents):
        return sum(e["sig_phi_fns"] * ABUND.get(sym_kza(e["parent"]), 0.0)
                   for e in emitted if sym_kza(e["parent"]) in parents)

    def feeds_in_material(feeds, parents):
        out = []
        for f in feeds:
            pr, ph = channels_to(f["parent_kza"])
            out.append({"parent": f["parent"],
                        "real_w": wsum(pr, parents),
                        "phantom_w": wsum(ph, parents)})
        return out

    def classify(member_kza, emitted, real, phantom, feeds, parents):
        """Deterministic primary-class assignment within one corpus
        material context (abundance-weighted FNS production)."""
        zaid = kza_zaid(member_kza)
        if not (zaid in decay_zas["endf-b-viii-0_decay"]
                or zaid in decay_zas["jeff_3_3_decay"]):
            return "decay_file_gap"
        real_w = wsum(real, parents)
        phantom_w = wsum(phantom, parents)
        act_w = act_wsum(emitted, parents)
        if act_w == 0 and real_w == 0 and phantom_w > 0:
            return "alara_heritage"  # ALARA-only via impossible rows
        if phantom_w > 0.5 * max(real_w, 1e-30):
            return "alara_heritage"  # phantom-dominated production
        # shared-channel content difference per corpus parent: same
        # physical daughter, >2% difference, channel carries >=1% of the
        # member's real production in this material
        by_pa, by_pc = {}, {}
        for c in real:
            if c["parent_kza"] in parents:
                by_pa[c["parent_kza"]] = by_pa.get(c["parent_kza"], 0.0) \
                    + c["sig_phi_fns"]
        for e in emitted:
            pk = sym_kza(e["parent"])
            if pk in parents:
                by_pc[pk] = by_pc.get(pk, 0.0) + e["sig_phi_fns"]
        for p in by_pa:
            if p not in by_pc:
                continue
            a_, c_ = by_pa[p], by_pc[p]
            if max(a_, c_) > 0 and abs(c_ - a_) / max(a_, c_) > 0.02 \
                    and a_ * ABUND.get(p, 0.0) > 0.01 * real_w:
                return "conversion_content"
        # phantom-fed decay chain within this material
        if any(f["phantom_w"] > 0 and f["phantom_w"] > 0.1 * f["real_w"]
               for f in feeds_in_material(feeds, parents)):
            return "alara_heritage"
        cov = [c for c in real if not c["parent_in_artifact"]
               and c["parent_kza"] in parents
               and c["sig_phi_fns"] * ABUND.get(c["parent_kza"], 0) > 1e-3]
        if cov:
            return "coverage_parent"
        if act_w > 0 and real_w > 0:
            return "conversion_content"
        return "unresolved"

    census = []
    for p40_class, members in MEMBERS.items():
        for sym in members:
            mk = sym_kza(sym)
            zaid = kza_zaid(mk)
            real, phantom = channels_to(mk)
            emitted = actinv_emit(zaid)
            feeds = decay_feeds(mk)
            own = [(kza_sym(d), e) for d, e in edges.get(mk, [])]
            per_case = {}
            for cname in EVIDENCE_CASES:
                cd = CASES / cname
                if not (cd / "out.json").exists():
                    continue
                a = alara_activity(cd, sym, hl)
                c = actinv_activity(cd, sym)
                per_case[cname] = {
                    "alara": a, "actinv": c,
                    "ratio": (c / a) if a else
                    (None if c == 0 else "actinv_only")}
            # per-material classification; primary class comes from the
            # material of the member's most-divergent observed case
            per_material = {m: classify(mk, emitted, real, phantom,
                                        feeds, ps)
                            for m, ps in MATERIALS.items()}
            def case_material(cn):
                mm = re.match(r"fe_([a-z]+)\d+wppm", cn)
                return ("fe_" + mm.group(1)) if mm else "fe"
            # worst observed case = largest absolute Bq/g difference
            worst, wmag = None, 0.0
            for cn, ev in per_case.items():
                mag = abs(ev["actinv"] - ev["alara"])
                if mag > wmag:
                    wmag, worst = mag, cn
            if worst is not None:
                cls = per_material.get(case_material(worst), "unresolved")
            else:
                # no divergent case: classify in the material carrying
                # the largest ALARA-side production of this member
                best, bmat = 0.0, "fe"
                for m, ps in MATERIALS.items():
                    w = wsum(real, ps) + wsum(phantom, ps)
                    if w > best:
                        best, bmat = w, m
                cls = per_material.get(bmat, "unresolved")
                worst = f"(no divergent case; dominant material {bmat})"
            census.append({
                "member": sym, "kza": mk, "p40_class": p40_class,
                "primary_class": cls,
                "alara_half_life_s": hl.get(mk),
                "alara_real_channels": real,
                "alara_phantom_channels": phantom,
                "alara_decay_feeds": feeds,
                "alara_own_daughters": own,
                "actinv_emission_rows": emitted,
                "decay_archive_present": {
                    "endf_b_viii_0":
                        zaid in decay_zas["endf-b-viii-0_decay"],
                    "jeff_3_3": zaid in decay_zas["jeff-3-3_decay"]},
                "per_material_class": per_material,
                "worst_case": worst,
                "case_evidence": per_case})
    out = {"schema": "g1_p42_mechanisms/1", "input_sha256": digests,
           "fendl_truth": truth,
           "vocabulary": sorted(VOCAB), "census": census}
    OUT.write_text(json.dumps(out, indent=1))
    print(json.dumps({"written": str(OUT), "members": len(census),
                      "classes": {c["member"]: c["primary_class"]
                                  for c in census}}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
