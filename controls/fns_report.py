#!/usr/bin/env python3
"""P2-G4 report: per-experiment C/E tables (ACTINV vs FISPACT-II/TENDL-2017 reference vs measurement), summary, figures,
diagnostics for the worst experiments. Reads results/fns/*.json; writes results/FNS_REPORT.md and results/fns_figures/."""
import os, sys, json, glob, math, numpy as np
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
RES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "results"); FIG = os.path.join(RES, "fns_figures"); os.makedirs(FIG, exist_ok=True)
recs = [json.load(open(f)) for f in sorted(glob.glob(os.path.join(RES, "fns", "*.json")))]
ok = [r for r in recs if not r.get("error")]; err = [r for r in recs if r.get("error")]
rows = []
nodata = [r for r in ok if not r.get("CE_actinv")]; ok = [r for r in ok if r.get("CE_actinv")]
for r in ok:
    sa = r["summary"]["actinv"]; sf = r["summary"].get("fispact", {}); d = r.get("disposition", {})
    rows.append((r["material"], r["experiment"], sa["geomean_CE"], sa["max_abs_lnCE"], sf.get("geomean_CE"), sf.get("max_abs_lnCE"), "AGREE-MEAS" if d.get("AGREE_MEAS") else ("AGREE-REF" if d.get("AGREE_REF") else "DISAGREE"), r["pruned_size"], r["ms_total"], len(r["ledger"].get("composition_isotopes_absent", [])), len(r["ledger"].get("products_no_decay_record", {})), len(r["ledger"].get("nuclides_without_decay_energy_data", []))))
gm_a = np.array([x[2] for x in rows]); ml_a = np.array([x[3] for x in rows]); gm_f = np.array([x[4] for x in rows if x[4] is not None]); ml_f = np.array([x[5] for x in rows if x[5] is not None])
fin = np.isfinite(gm_a) & np.isfinite(ml_a); n_nonfinite = int((~fin).sum()); gm_a, ml_a = gm_a[fin], ml_a[fin]; gm_f, ml_f = gm_f[np.isfinite(gm_f)], ml_f[np.isfinite(ml_f)]
L = ["# ACTINV P2 — FNS decay-heat comparison (73 materials, %d experiments)" % len(recs), "",
     "ACTINV: EAF-2010 (709-group, flat-lethargy) + ENDF/B-VIII.0 decay + CRAM-16 (own solver). Reference: FISPACT-II with TENDL-2017 from the CoNDERC set. E: FNS measurement. Accuracy is REPORTED, not gated (protocol P2-G4).", "",
     f"- experiments run: {len(ok) + len(nodata)}; errors: {len(err)}; no matched measurement rows (ledgered): {len(nodata)} {[r['material'] + ' ' + r['experiment'] for r in nodata]}; non-finite ACTINV summaries: {n_nonfinite}", f"- median geometric-mean C/E: ACTINV {np.median(gm_a):.3f}, FISPACT-II {np.median(gm_f):.3f}",
     f"- median max|ln C/E|: ACTINV {np.median(ml_a):.3f}, FISPACT-II {np.median(ml_f):.3f}", f"- fraction of experiments with max|ln C/E| ≤ ln(1.3): ACTINV {np.mean(ml_a <= math.log(1.3)):.2f}, FISPACT-II {np.mean(ml_f <= math.log(1.3)):.2f}",
     f"- dispositions: " + ", ".join(f"{k} {sum(1 for x in rows if x[6] == k)}" for k in ("AGREE-MEAS", "AGREE-REF", "DISAGREE")), "",
     "| material | experiment | ACTINV gm C/E | ACTINV max\\|lnCE\\| | FISPACT gm C/E | FISPACT max\\|lnCE\\| | disposition | states | ms | absent iso | no-decay prod | no-E nuclides |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
for x in sorted(rows, key=lambda x: -x[3]): L.append(f"| {x[0]} | {x[1]} | {x[2]:.3f} | {x[3]:.3f} | {x[4] if x[4] is None else '%.3f' % x[4]} | {x[5] if x[5] is None else '%.3f' % x[5]} | {x[6]} | {x[7]} | {x[8]:.2f} | {x[9]} | {x[10]} | {x[11]} |")
# diagnostics: 6 worst by ACTINV max|lnCE| — top contributors ACTINV vs FISPACT at first and last time
L += ["", "## Diagnostics — six worst experiments (ACTINV max|ln C/E|)", ""]
for r in sorted(ok, key=lambda r: -r["summary"]["actinv"]["max_abs_lnCE"])[:6]:
    L += [f"### {r['material']} {r['experiment']}", f"C/E ACTINV: " + " ".join("%.2f" % c for c in r["CE_actinv"]), f"C/E FISPACT: " + (" ".join("%.2f" % c for c in r["CE_fispact"]) if "CE_fispact" in r else "n/a"),
          f"ACTINV top (first): {[(n, '%.3e' % v) for n, v in r['top_contributors_actinv']['first']]}", f"FISPACT top (first): {[(n, '%.3e' % v) for n, v in r.get('top_contributors_fispact', {}).get('first', [])]}",
          f"ACTINV top (last): {[(n, '%.3e' % v) for n, v in r['top_contributors_actinv']['last']]}", f"FISPACT top (last): {[(n, '%.3e' % v) for n, v in r.get('top_contributors_fispact', {}).get('last', [])]}",
          f"ledger: {json.dumps({k: (v if not isinstance(v, (list, dict)) else (len(v))) for k, v in r['ledger'].items()})}", ""]
if err: L += ["## Errors", ""] + [f"- {r['material']} {r['experiment']}: {r['error'][-200:]}" for r in err]
open(os.path.join(RES, "FNS_REPORT.md"), "w").write("\n".join(L)); print("\n".join(L[:12]))
# figures: summary scatter + per-experiment C/E curves (one multi-page grid)
fig, ax = plt.subplots(1, 2, figsize=(11, 4.5))
ax[0].scatter(ml_f, ml_a, s=14, alpha=0.7); lim = max(ml_a.max(), ml_f.max()) * 1.05; ax[0].plot([0, lim], [0, lim], "k--", lw=0.8); ax[0].set_xlabel("FISPACT-II/TENDL-2017 max|ln C/E|"); ax[0].set_ylabel("ACTINV max|ln C/E|"); ax[0].set_title("per experiment")
ax[1].hist([np.log(gm_a), np.log(gm_f)], bins=25, label=["ACTINV", "FISPACT-II"], alpha=0.7); ax[1].set_xlabel("ln(geometric-mean C/E)"); ax[1].legend(); ax[1].set_title("distribution over experiments")
fig.tight_layout(); fig.savefig(os.path.join(FIG, "summary.png"), dpi=130); plt.close(fig)
n = len(ok); cols = 6; rws = math.ceil(n / cols); fig, axs = plt.subplots(rws, cols, figsize=(3.0 * cols, 2.3 * rws)); axs = axs.ravel()
for k, r in enumerate(sorted(ok, key=lambda r: (r["material"], r["experiment"]))):
    a = axs[k]; t = np.array(r["cooling_cum_s"][:len(r["CE_actinv"])]); a.plot(t, r["CE_actinv"], "o-", ms=2.5, lw=0.8, label="ACTINV")
    if "CE_fispact" in r: a.plot(t, r["CE_fispact"], "s-", ms=2.5, lw=0.8, label="FISPACT")
    E = np.array(r["measured"]["heat_uW_g"]); S = np.array(r["measured"]["sigma_uW_g"]); a.fill_between(t, 1 - 2 * S / E, 1 + 2 * S / E, color="gray", alpha=0.25)
    a.axhline(1, color="k", lw=0.6); a.set_xscale("log"); a.set_title(f"{r['material']} {r['experiment']}", fontsize=8); a.tick_params(labelsize=6); a.set_ylim(0, 2.5)
for k in range(n, len(axs)): axs[k].axis("off")
axs[0].legend(fontsize=6); fig.tight_layout(); fig.savefig(os.path.join(FIG, "ce_all.png"), dpi=110); plt.close(fig); print("figures written")
