# ACTINV CB1 — archived competitive-benchmark close, 2026-08-28

**Protocol:** `protocols/ACTINV-CB1_PROTOCOL.md`
(`627990751a4730fe22e457ea2fa334fca25ae0eae7f463c8677e488e5dbb7398`).
**Verdict (`controls/check_cb1.py --full`): CB1-COMPLETE.** This is a reproducibility verdict for the initial
scorecard, not a claim that ACTINV wins every comparison.

| gate | result |
|---|---|
| G0 access/provenance | PASS — ACTINV, ALARA and OpenMC executed; FISPACT public-reference/documented-only and SCALE/ORIGEN unavailable cells are explicit |
| G1 identical operator | PASS — ACTINV/OpenMC/dense worst meaningful relative difference `4.18e-15`; absolute and schedule-split bounds pass |
| G2 identical processed data | PASS — ALARA reaction rate exact; worst shutdown inventory difference `4.12e-8` relative |
| G3 measurements | REPORTED — all 132 FNS experiments and 2,360 positive aligned pairs scored without a floor or nuclide exclusion; separate prior fission and FNG/ITER evidence retained |
| G4 performance | REPORTED — 30-sample kernel/startup/end-to-end rows and bounded mesh scaling re-derived; million-cell value explicitly remains unexecuted |
| G5 first use | PASS — clean ACTINV wheel/data/example and clean ALARA build/sample/diagnostic exercises complete |
| G6 capability | PASS — 17 axes × 5 products, dated/versioned official sources, unknown never converted to absent |
| G7 close | PASS — report tokens, all FNS/statistical arithmetic, protocol, required Rust gates, clean clone, end-to-end and legacy P10 control pass |

## Published scorecard checkpoint

The report/evidence checkpoint is commit
`121b35b01eb8a055b071efe7301d07e112269ad1` in
`https://github.com/AvilaLabs/ACTINV.git`. GitHub Actions run
[`33185710084`](https://github.com/AvilaLabs/ACTINV/actions/runs/33185710084) completed successfully for that exact
commit. It began at `2026-08-28T15:33:21Z` and completed at `2026-08-28T15:40:29Z`.

The closure pass additionally runs the four required Rust commands, the nested self-contained clone, the pinned
data-subset CLI/Python end-to-end calculation, and the P10 projectile/legacy-neutron result control. The first
end-to-end attempt was blocked by the local sandbox's multiprocessing socket policy; the identical permitted rerun
completed with ten targets, 536 rows, zero errors, exact CLI/Python identity, and a passing tolerance result. The P10
control reproduced the frozen normalized neutron hash
`0ed6be999d63820556d91ad73ab73fa7980f9b37dca8fcc00dd4c351f7cd1b1c`.

**Closure-CI repair.** The first final-closure run passed Rust/build stages and then exposed that the new session
checker required the earlier scorecard commit object to exist locally. GitHub's default one-commit shallow checkout
correctly lacked that ancestor even though its full SHA and successful run are recorded. The checker now validates a
strict 40-character SHA plus equality with the recorded successful workflow head, while the session still rehashes
every committed evidence file. This changes no benchmark value, interpretation, product source, or access claim.

## Scientific reading

ACTINV's numerical solver and identical-data result are strong. The FNS product-plus-data comparison is mixed rather
than a win: the public FISPACT-II 4.0/TENDL-2017 result has lower median point error and more experiments wholly within
30%, while ACTINV/TENDL-2025 has the slightly lower 90th-percentile point error and pooled bias closer to one. The data
versions differ, so no solver ranking is permitted.

The evidence supports ACTINV's intended niche as an open, easily installed, scriptable activation/R2S engine with
unusually explicit provenance. It also exposes real follow-up work: a lawful same-data FISPACT campaign, held-out FNS
diagnosis, finite-dilution self-shielding, lower data-load memory, broader uncertainty evidence, physical-unit domain
types, and expanded metamorphic tests. Feed/removal, reverse, damage, and additional projectiles remain demand-led
rather than automatic parity work.

The complete user-facing interpretation, exact tables, sources, and limits are in
`docs/COMPETITIVE_BENCHMARK.md`. No bulk nuclear data or licensed executable is committed.
