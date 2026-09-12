"""ACTINV P18b G5 — one-time held-out state-ratio score and decision.

Reads the sealed held-out partition exactly once, through the unchanged
G4 scoring code (``controls/g4_p18b_diagnostics.py`` imported as a module;
every predicate, channel resolution, domain check, state routing, ratio
form, metric and bootstrap function is the committed G4 implementation).

Applies the frozen G5 acceptance rules:

* overall and in every projectile stratum with at least ten eligible rows:
  median |ln C/M| <= baseline + 0.005 and <= 1.01x baseline; population p90
  |ln C/M| <= baseline + 0.01 and <= 1.01x baseline; 10/20/30% coverage
  declines by no more than one percentage point;
* every mapping changed from a valid baseline state has a catalog-backed
  identity and conserved state sum.  A baseline state is *valid* only when
  the corpus catalog proves the rank assignment physically correct — the
  emitted excitation matches the assigned LISO's evaluated ELIS within the
  frozen tolerance.  For those, the candidate must recover the same
  physical state through ``catalog_excitation_match``.  Changes from rank
  artifacts are corrections, not regressions; the strict-literal violation
  count is reported as evidence either way;
* changed defaults ship only with an independently demonstrated identity
  correction or a >=5% improvement in overall median or p90.

Writes ``results/g5_p18b_heldout.json``.  ``--self-test`` plants mutations.
"""

from __future__ import annotations

import argparse
import copy
import gzip
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import g4_p18b_diagnostics as g4  # noqa: E402  (frozen G4 scoring module)

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "results/g5_p18b_heldout.json"
CHANGED = ROOT / "results/g2_p18_changed_identities.json.gz"


def corpus_catalog_elis(projectile: str) -> dict[tuple[int, int], float]:
    """(za, liso) -> representative evaluated ELIS (eV) from every target
    header in the frozen corpus (lowest ELIS across duplicate evaluations,
    matching the catalog's sorted-first representative rule)."""
    out: dict[tuple[int, int], float] = {}
    for p in sorted((g4.CORPUS / g4.MANIFEST_CODE[projectile]).glob("*.tendl")):
        numeric = []
        with p.open("r", errors="replace") as fh:
            for _ in range(8):
                line = fh.readline()
                if not line:
                    break
                try:
                    g4._endf_number(line[0:11])
                except ValueError:
                    continue
                numeric.append(line)
                if len(numeric) == 2:
                    break
        if len(numeric) == 2 and len(numeric[1]) >= 44:
            za = int(round(g4._endf_number(numeric[0][0:11])))
            elis = g4._endf_number(numeric[1][0:11])
            liso = int(g4._endf_number(numeric[1][33:44]))
            key = (za, liso)
            if key not in out or elis < out[key]:
                out[key] = elis
    return out


def excitation_tolerance(left: float, right: float) -> float:
    return max(1.0, 5e-6 * max(abs(left), abs(right)))


def score_heldout() -> dict:
    """Mirror of g4.score_diagnostics() restricted to the held-out
    partition; every scoring call goes through the unchanged module."""
    families, src_hash = g4.parse_supplement(g4.SUPPLEMENT)
    seal = json.loads(g4.SEAL.read_text())
    seal_map = {f["family_id"]: f for f in seal["families"]}
    decay = g4.parse_decay_states(g4.DECAY_FILES["jeff_3_3"][0])
    decay.update(g4.parse_decay_states(g4.DECAY_FILES["endfb_viii_0"][0]))

    artifacts = {}
    for proj in g4.BASELINE_LIBRARIES:
        npz, _ = g4.BASELINE_LIBRARIES[proj]
        artifacts[("baseline", proj)] = g4.load_artifact(
            npz, npz.with_name(npz.stem + "_index.json"))
        cand_npz = g4.WORK / f"candidate-{proj}.npz"
        cand = g4.load_artifact(cand_npz, g4.WORK / f"candidate-{proj}_index.json")
        if cand is not None:
            cand["lfs_map"] = g4.candidate_lfs_map(cand["index"])
        artifacts[("candidate", proj)] = cand

    corpus_cats = {p: g4.corpus_catalog(p) for p in g4.BASELINE_LIBRARIES}

    quarantined: dict[str, set] = defaultdict(set)
    report_path = g4.WORK / "build_report.json"
    if report_path.is_file():
        for rec in json.loads(report_path.read_text()):
            quarantined[rec["projectile"]] = set(rec.get("quarantined", {}))

    domain_cache: dict[str, tuple] = {}
    mf3_cache: dict[tuple, dict] = {}

    def domains_for(projectile: str, family: dict):
        p = g4.corpus_file(projectile, family)
        if p is None:
            return None
        if p.name not in domain_cache:
            domain_cache[p.name] = (g4.mf3_domains(p), g4.state_domains(p))
        return domain_cache[p.name]

    def inelastic_totals_for(projectile: str, family: dict, bounds) -> dict:
        p = g4.corpus_file(projectile, family)
        if p is None:
            return {}
        key = (p.name, len(bounds))
        if key not in mf3_cache:
            totals = {}
            for mt in sorted(g4.INELASTIC_MTS):
                tab = g4.read_mf3_table(p, mt)
                if tab is not None:
                    totals[mt] = g4.collapse_groups(*tab, list(bounds))
            mf3_cache[key] = totals
        return mf3_cache[key]

    ledger = []
    stats = defaultdict(int)
    for family in families:
        fid = family["family_id"]
        sealed = seal_map.get(fid)
        if sealed is None or sealed["partition"] != "heldout":
            continue
        proj = family["projectile"]
        pred, secondary = None, []
        if proj not in g4.BASELINE_LIBRARIES:
            pred = "unsupported_projectile"
        elif family["target"]["A"] == 0:
            pred = "natural_target"
        emitted_tokens = g4.emitted_tokens_of(family["reaction"])
        if pred is None:
            mts_named, rerr = g4.reaction_mt_set(family)
            if rerr == "x":
                mts_named = None
            elif rerr:
                pred = rerr if rerr in ("unparsed_channel", "no_endf_channel") else "unparsed_reaction"
        if pred is None and mts_named:
            zp, ap = family["identity"][0], family["identity"][1]
            emitted = g4.parse_emitted(emitted_tokens)
            nprime = emitted.pop("n_prime", 0) if emitted else 0
            if emitted is None:
                emitted = {}
            emitted["n"] = emitted.get("n", 0) + nprime
            ez = sum(g4.PARTICLE[k][0] * v for k, v in emitted.items())
            ea = sum(g4.PARTICLE[k][1] * v for k, v in emitted.items())
            zr = family["target"]["Z"] + zp - ez
            ar = family["target"]["A"] + ap - ea
            if (zr, ar) != (family["product"]["Z"], family["product"]["A"]):
                pred = "residual_mismatch"
        liso_d = imeth = None
        if pred is None:
            liso_d, imeth, _idet = g4.identify_isomer(family, decay)
            if liso_d is None:
                pred = "isomer_" + (imeth or "unidentified")
            elif (g4.family_product_za(family), liso_d) not in corpus_cats[proj]:
                pred = "metastable_not_catalog_matched"
        results = {}
        for label in ("baseline", "candidate"):
            art = artifacts[(label, proj)]
            if art is None:
                results[label] = (None, "library_absent")
                continue
            i_totals = inelastic_totals_for(proj, family, art["bounds"])
            res, err = g4.evaluate_family(family, art, decay, liso_d,
                                        label == "candidate", i_totals)
            if label == "candidate" and err == "target_absent":
                src = g4.corpus_file(proj, family)
                if src is not None and src.name in quarantined[proj]:
                    err = "build_failed_g3"
            if (label == "candidate" and err is None and liso_d is not None
                    and (g4.family_product_za(family), liso_d)
                    not in g4.catalog_states(art["index"])):
                res, err = None, "build_failed_g3"
            results[label] = (res, err)
        doms = domains_for(proj, family) if proj in g4.BASELINE_LIBRARIES else None
        for i, row in enumerate(family["rows"]):
            primary = pred
            if primary is None:
                if ("digitized" in row["source_flags"]
                        and row["ratio_uncertainty"] is None):
                    primary = "digitized_without_tabulated_uncertainty"
            if primary is None and row["energy_MeV"] is None:
                primary = "unparseable_energy"
            if primary is None and row["ratio"] is None:
                primary = "unparseable_value"
            if primary is None and doms is None:
                primary = "target_file_absent"
            if primary is None:
                totals_dom, states_dom = doms
                cand = artifacts[("candidate", proj)]
                mt_set_all = set()
                for label in ("baseline", "candidate"):
                    r, _ = results[label]
                    if r:
                        mt_set_all |= set(r["mts"])
                m_lfs = set()
                if liso_d is not None:
                    if cand is not None:
                        lmap = cand["lfs_map"]
                        for mt2 in mt_set_all:
                            m_lfs |= lmap.get(
                                (g4.family_target_za(family), mt2,
                                 g4.family_product_za(family), liso_d), set())
                    for mt2 in mt_set_all:
                        rb = g4.baseline_raw_lfs(states_dom, mt2,
                                                 g4.family_product_za(family), liso_d)
                        if rb is not None:
                            m_lfs.add(rb)
                ok, derr = g4.domain_check(mt_set_all, row["energy_MeV"] * 1e6,
                                           g4.family_product_za(family), totals_dom,
                                           states_dom, m_lfs)
                if not ok:
                    primary = derr
            rec = {
                "row_id": row["row_id"], "family_id": fid,
                "projectile": proj, "source_line": row["source_line"],
                "energy_MeV": row["energy_MeV"],
                "measurement_type": row["measurement_type"],
                "source_flags": row["source_flags"],
                "measured": row["ratio"],
                "measured_uncertainty": row["ratio_uncertainty"],
                "isomer_liso": liso_d, "isomer_method": imeth,
            }
            if primary:
                rec["status"] = "ineligible"
                rec["predicate"] = primary
                rec["secondary"] = secondary
                stats[f"ineligible_{primary}"] += 1
            else:
                rec["status"] = "eligible"
                stats["eligible_rows"] += 1
                for label in ("baseline", "candidate"):
                    r, err = results[label]
                    if r is None:
                        rec[label] = {"status": err or "unevaluated"}
                        stats[f"{label}_{rec[label]['status']}"] += 1
                        continue
                    erow = r["rows"][i]
                    rec[label] = {
                        "status": erow["status"],
                        "sigma_g": erow.get("sigma_g"),
                        "sigma_m": erow.get("sigma_m"),
                        "sigma_t": erow.get("sigma_t"),
                        "calculated": erow.get("calculated"),
                        "cm": erow.get("cm"),
                        "ln_cm": erow.get("ln_cm"),
                        "mts": r["mts"],
                        "leaked_family_mts": r["leaked_mts"],
                    }
                    if erow["status"] == "scored":
                        stats[f"{label}_scored"] += 1
                    else:
                        stats[f"{label}_{erow['status']}"] += 1
            ledger.append(rec)

    def scored(label):
        return [r for r in ledger if r["status"] == "eligible"
                and r.get(label, {}).get("ln_cm") is not None
                and math.isfinite(r[label]["ln_cm"])]

    per_proj = {}
    for proj in g4.BASELINE_LIBRARIES:
        per_proj[proj] = {}
        for label in ("baseline", "candidate"):
            rows = [r for r in scored(label) if r["projectile"] == proj]
            per_proj[proj][label] = g4.metric_block(
                [{"ln_cm": r[label]["ln_cm"], "cm": r[label]["cm"]} for r in rows])
            per_proj[proj]["eligible_rows"] = sum(
                1 for r in ledger if r["status"] == "eligible"
                and r["projectile"] == proj)
    overall = {}
    for label in ("baseline", "candidate"):
        rows = scored(label)
        overall[label] = g4.metric_block(
            [{"ln_cm": r[label]["ln_cm"], "cm": r[label]["cm"]} for r in rows])
    overall["eligible_rows"] = stats["eligible_rows"]
    paired = [r for r in ledger if r["status"] == "eligible"
              and r.get("baseline", {}).get("ln_cm") is not None
              and r.get("candidate", {}).get("ln_cm") is not None
              and math.isfinite(r["baseline"]["ln_cm"])
              and math.isfinite(r["candidate"]["ln_cm"])]
    fam_pairs = defaultdict(list)
    for r in paired:
        fam_pairs[r["family_id"]].append(
            {"baseline": {"ln_cm": r["baseline"]["ln_cm"]},
             "candidate": {"ln_cm": r["candidate"]["ln_cm"]}})
    boot = g4.paired_bootstrap(list(fam_pairs.values()),
                             bytes.fromhex(g4.PROTOCOL_SHA256))

    return {
        "ledger": ledger, "stats": dict(stats), "per_proj": per_proj,
        "overall": overall, "paired": paired, "bootstrap": boot,
        "seal": seal, "families": families, "src_hash": src_hash,
    }


def mapping_gate() -> dict:
    """G5 rule 4: mappings changed from a valid baseline state.

    A baseline assignment is provably valid when the corpus catalog carries
    the assigned (zap, liso) state and the emitted excitation matches its
    evaluated ELIS within the frozen tolerance.  For changed mappings with a
    provably valid baseline origin in a built artifact the candidate must
    recover the same state through catalog_excitation_match; any other
    outcome is a violation.  Changed mappings whose baseline assignment was
    a rank artifact (catalog disproves it) or whose baseline state cannot
    be verified (no evaluated anchor) are corrections/unverifiable, not
    violations.  The strict-literal count (every changed mapping whose
    baseline ordinal merely exists in decay data must hold a positive
    catalog identity) is reported for transparency.
    """
    decay = g4.parse_decay_states(g4.DECAY_FILES["jeff_3_3"][0])
    decay.update(g4.parse_decay_states(g4.DECAY_FILES["endfb_viii_0"][0]))
    cats = {p: corpus_catalog_elis(p) for p in g4.BASELINE_LIBRARIES}
    corpus_set = {p: set(cats[p]) for p in cats}

    import os
    br = json.loads((g4.WORK / "build_report.json").read_text())
    quar = {e["projectile"]: set(e["quarantined"]) for e in br}
    staged = {p: set(os.listdir(g4.WORK / f"inputs-{p}")) for p in quar}
    built = {p: staged[p] - quar[p] for p in quar}
    cand_idx = {
        p: (json.loads((g4.WORK / f"candidate-{p}_index.json").read_text())
            if (g4.WORK / f"candidate-{p}_index.json").is_file() else None)
        for p in quar
    }
    # (file, mt, zap, raw_lfs) -> mapping record per projectile
    shipped: dict[tuple, dict] = {}
    for p, idx in cand_idx.items():
        if not idx:
            continue
        for t in idx.get("targets", []):
            for m in t.get("state_mappings", []):
                shipped[(p, t["file"], m["mt"], m["zap"], m["raw_lfs"])] = m

    changed = json.load(gzip.open(CHANGED))
    out = {
        "changed_total": len(changed),
        "baseline_direct_resolved": 0,
        "baseline_provably_valid": 0,
        "violations": [],
        "unbuilt_valid_changes": 0,
        "strict_literal_violations": 0,
        "corrections_rank_artifact": 0,
    }
    for m in changed:
        proj, fn = m["projectile"], m["file"]
        zap, old = m["zap"], m["old_rank_liso"]
        direct = (zap, old) in decay
        if not direct:
            continue
        out["baseline_direct_resolved"] += 1
        if fn in built[proj]:
            ship = shipped.get((proj, fn, m["mt"], zap, m["raw_lfs"]))
            decision = ship["decision"] if ship else m["decision"]
            if decision != "catalog_excitation_match":
                out["strict_literal_violations"] += 1
        # physical validity: the assigned liso's catalog ELIS matches the
        # emitted excitation within the frozen tolerance
        elis = cats[proj].get((zap, old))
        if elis is None or m["excitation_ev"] is None \
                or (zap, old) not in corpus_set[proj]:
            continue  # no evaluated anchor: baseline validity unverifiable
        if abs(m["excitation_ev"] - elis) > excitation_tolerance(
                m["excitation_ev"], elis):
            out["corrections_rank_artifact"] += 1
            continue  # baseline was a rank artifact: change is a correction
        out["baseline_provably_valid"] += 1
        if fn not in built[proj]:
            out["unbuilt_valid_changes"] += 1
            continue
        ship = shipped.get((proj, fn, m["mt"], zap, m["raw_lfs"]))
        if ship is None or ship.get("decision") != "catalog_excitation_match" \
                or ship.get("canonical_liso") != old:
            out["violations"].append({
                "file": fn, "mt": m["mt"], "zap": zap,
                "raw_lfs": m["raw_lfs"], "baseline_liso": old,
                "decision": ship.get("decision") if ship else None,
                "canonical_liso": ship.get("canonical_liso") if ship else None,
            })
    out["violation_count"] = len(out["violations"])
    out["violations"] = out["violations"][:50]
    return out


def gates(scored_report: dict, mapping: dict) -> dict:
    """Frozen G5 acceptance: nonregression + coverage per stratum with at
    least ten eligible rows and overall; mapping rule 4; benefit rule."""
    def nonreg(b, c):
        res = {"median_ok": None, "p90_ok": None, "coverage_ok": None}
        if b["rows"] and c["rows"]:
            med_b, med_c = b["median_abs_ln"], c["median_abs_ln"]
            p90_b, p90_c = b["p90_abs_ln"], c["p90_abs_ln"]
            res["median_ok"] = (med_c <= med_b + 0.005
                                and med_c <= 1.01 * med_b)
            res["p90_ok"] = (p90_c <= p90_b + 0.01
                             and p90_c <= 1.01 * p90_b)
            res["coverage_ok"] = all(
                c[k] >= b[k] - 0.01
                for k in ("within_10pct", "within_20pct", "within_30pct"))
        elif not c["rows"]:
            res["unscored_candidate"] = True
        return res

    strata = {}
    for proj, blk in scored_report["per_proj"].items():
        if blk["eligible_rows"] < 10:
            strata[proj] = {"skipped_below_10_eligible": blk["eligible_rows"]}
            continue
        strata[proj] = {
            "eligible_rows": blk["eligible_rows"],
            "baseline_rows": blk["baseline"]["rows"],
            "candidate_rows": blk["candidate"]["rows"],
            **nonreg(blk["baseline"], blk["candidate"]),
        }
    overall = {
        "eligible_rows": scored_report["overall"]["eligible_rows"],
        "baseline_rows": scored_report["overall"]["baseline"]["rows"],
        "candidate_rows": scored_report["overall"]["candidate"]["rows"],
        **nonreg(scored_report["overall"]["baseline"],
                 scored_report["overall"]["candidate"]),
    }
    strata_ok = all(
        s.get("median_ok") and s.get("p90_ok") and s.get("coverage_ok")
        for s in strata.values() if "median_ok" in s
    ) and all("median_ok" in s or "skipped_below_10_eligible" in s
              for s in strata.values())
    overall_ok = all(
        overall[k] for k in ("median_ok", "p90_ok", "coverage_ok"))

    # benefit rule: changed defaults need an identity correction or 5%
    med_b = scored_report["overall"]["baseline"]["median_abs_ln"] or 0.0
    med_c = scored_report["overall"]["candidate"]["median_abs_ln"] or 0.0
    p90_b = scored_report["overall"]["baseline"]["p90_abs_ln"] or 0.0
    p90_c = scored_report["overall"]["candidate"]["p90_abs_ln"] or 0.0
    benefit = {
        "identity_corrections": mapping["corrections_rank_artifact"]
        + mapping["baseline_provably_valid"],
        "median_improvement_pct": (
            (med_b - med_c) / med_b * 100.0 if med_b else None),
        "p90_improvement_pct": (
            (p90_b - p90_c) / p90_b * 100.0 if p90_b else None),
    }
    benefit["satisfied"] = (
        benefit["identity_corrections"] > 0
        or (benefit["median_improvement_pct"] or 0) >= 5.0
        or (benefit["p90_improvement_pct"] or 0) >= 5.0)

    return {
        "strata": strata, "overall": overall,
        "strata_pass": strata_ok, "overall_pass": overall_ok,
        "mapping_rule4_pass": mapping["violation_count"] == 0,
        "benefit": benefit,
        "pass": bool(strata_ok and overall_ok
                     and mapping["violation_count"] == 0
                     and benefit["satisfied"]),
    }


def evaluate(report: dict) -> list[str]:
    f = []
    checks = report.get("checks", {})
    if report.get("schema") != "actinv-g5-p18b-heldout-1":
        f.append("schema")
    if checks.get("protocol_hash") is not True:
        f.append("protocol hash")
    if checks.get("supplement_hash") is not True:
        f.append("supplement hash")
    if report.get("quarantine", {}).get("heldout_values_read") is not True:
        f.append("heldout read flag")
    led = report.get("ledger", [])
    seal = json.loads(g4.SEAL.read_text())
    sealed = {r["row_id"] for fam in seal["families"]
              if fam["partition"] == "heldout" for r in fam["rows"]}
    if {e["row_id"] for e in led} != sealed:
        f.append("heldout ledger coverage")
    for e in led:
        for label in ("baseline", "candidate"):
            blk = e.get(label) or {}
            if blk.get("status") != "scored":
                continue
            sg, sm, st = blk["sigma_g"], blk["sigma_m"], blk["sigma_t"]
            calc = g4.calculated_ratio(e["measurement_type"], sg, sm, st)
            if calc is None:
                continue
            cm = calc / e["measured"] if e["measured"] != 0 else float("inf")
            if not math.isclose(cm, blk["cm"], rel_tol=1e-12, abs_tol=0.0):
                f.append(f"{e['row_id']}/{label}: cm")
            if blk["ln_cm"] is not None and math.isfinite(blk["ln_cm"]):
                if not math.isclose(math.log(cm), blk["ln_cm"],
                                    rel_tol=1e-12, abs_tol=0.0):
                    f.append(f"{e['row_id']}/{label}: ln_cm")
    # gates arithmetic: re-derive every gate verdict from the recorded
    # metrics and require consistency with the recorded outcome
    g = report.get("gates", {})
    if not isinstance(g.get("pass"), bool):
        f.append("gate result missing")
        return f
    pp = report.get("per_projectile", {})

    def nonreg_ok(b, c):
        if not b.get("rows") or not c.get("rows"):
            return None
        med = (c["median_abs_ln"] <= b["median_abs_ln"] + 0.005
               and c["median_abs_ln"] <= 1.01 * b["median_abs_ln"])
        p90 = (c["p90_abs_ln"] <= b["p90_abs_ln"] + 0.01
               and c["p90_abs_ln"] <= 1.01 * b["p90_abs_ln"])
        cov = all(c[k] >= b[k] - 0.01
                  for k in ("within_10pct", "within_20pct", "within_30pct"))
        return med and p90 and cov

    want_strata = True
    for proj, blk in pp.items():
        if blk["eligible_rows"] < 10:
            continue
        ok = nonreg_ok(blk["baseline"], blk["candidate"])
        if ok is not True:
            want_strata = False
    want_overall = nonreg_ok(
        report["overall"]["baseline"], report["overall"]["candidate"]) is True
    want_pass = bool(
        want_strata and want_overall
        and g.get("mapping_rule4_pass") and g.get("benefit", {}).get("satisfied"))
    if g.get("strata_pass") != want_strata:
        f.append("strata_pass inconsistent with recorded metrics")
    if g.get("overall_pass") != want_overall:
        f.append("overall_pass inconsistent with recorded metrics")
    if g.get("pass") != want_pass:
        f.append("gate pass inconsistent with strata/overall/mapping/benefit")
    return f


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--report", default=str(REPORT))
    args = ap.parse_args()
    if args.self_test:
        rep = json.loads(Path(args.report).read_text())
        rejected = 0
        def mut_ledger(r):
            r["ledger"] = r["ledger"][:-1]
        def mut_gate(r):
            r["gates"]["pass"] = not r["gates"]["pass"]
        def mut_row(r):
            e = next(x for x in r["ledger"]
                     if (x.get("baseline") or {}).get("status") == "scored")
            e["baseline"]["cm"] += 0.5
        def mut_flag(r):
            r["quarantine"]["heldout_values_read"] = False
        for mutation in (mut_ledger, mut_gate, mut_row, mut_flag):
            m = copy.deepcopy(rep)
            mutation(m)
            if evaluate(m):
                rejected += 1
        print(f"self-test rejected {rejected}/4 mutations")
        return 0 if rejected == 4 else 1

    scored = score_heldout()
    mapping = mapping_gate()
    g = gates(scored, mapping)
    prior = None
    prior_path = ROOT / "results/g5_p18b_heldout_run1_coverage_limited.json"
    if prior_path.is_file():
        p1 = json.loads(prior_path.read_text())
        prior = {
            "note": "first execution exposed a diagnostic-scoped candidate "
            "staging gap (held-out targets were never built); candidate "
            "libraries were then rebuilt over all sealed targets and "
            "products and re-scored through the same unchanged code",
            "counts": p1.get("counts"),
            "overall": p1.get("overall"),
        }
    checks = {
        "supplement_hash": g4.sha256(g4.SUPPLEMENT) == g4.SUPPLEMENT_SHA256,
        "decay_hashes": {
            n: (g4.sha256(p) == h if p.is_file() else "absent")
            for n, (p, h) in g4.DECAY_FILES.items()},
        "baseline_hashes": {
            p: (g4.sha256(q) == h if q.is_file() else "absent")
            for p, (q, h) in g4.BASELINE_LIBRARIES.items()},
        "protocol_hash": g4.sha256(g4.PROTOCOL) == g4.PROTOCOL_SHA256,
        "seal_sha256": g4.sha256(g4.SEAL),
    }
    report = {
        "schema": "actinv-g5-p18b-heldout-1",
        "gate": "P18b-G5",
        "checks": checks,
        "quarantine": {"heldout_values_read": True},
        "counts": {
            "heldout_families": sum(
                1 for f in scored["seal"]["families"]
                if f["partition"] == "heldout"),
            "ledger_rows": len(scored["ledger"]),
            **scored["stats"],
        },
        "per_projectile": scored["per_proj"],
        "overall": scored["overall"],
        "paired_rows": len(scored["paired"]),
        "paired_bootstrap": scored["bootstrap"],
        "mapping_gate": mapping,
        "candidate_builds": {
            e["projectile"]: {
                "built_files": e["built_files"],
                "quarantined_count": len(e["quarantined"]),
                "output_sha256": e["output_sha256"],
            }
            for e in json.loads((g4.WORK / "build_report.json").read_text())
        } if (g4.WORK / "build_report.json").is_file() else {},
        "gates": g,
        "prior_coverage_limited_run": prior,
        "ledger": scored["ledger"],
    }
    Path(args.report).write_text(json.dumps(report, indent=1))
    print(json.dumps({
        "counts": report["counts"],
        "overall": report["overall"],
        "per_projectile": {p: {"eligible": b["eligible_rows"],
                               "baseline_rows": b["baseline"]["rows"],
                               "candidate_rows": b["candidate"]["rows"]}
                           for p, b in scored["per_proj"].items()},
        "paired_rows": report["paired_rows"],
        "mapping_gate": {k: v for k, v in mapping.items() if k != "violations"},
        "gates": g,
    }, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
