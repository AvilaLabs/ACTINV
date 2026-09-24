# FNS C/E gap diagnosis (2026-09-24)

Post-hoc decomposition of the standing "ACTINV trails FISPACT-II on the FNS
corpus" result (`results/cb2_fns.json`: median |ln C/E| 0.1392 vs 0.1053,
30%-coverage 59/132 vs 69/132). Method: re-join the sealed CB2 per-point record
(measured + published FISPACT-4.0/TENDL-2017 values) against the sealed P46
per-point run ledger (ACTINV on five corpora, `results/p46_run_ledger.jsonl`),
then decompose residuals per material, per experiment irradiation time, and per
nuclide from the stored run outputs. No new solves; all inputs are sealed
records. This is a diagnostic note, not a sealed verdict — it produces
hypotheses and named mechanisms, not an acceptance claim.

## Headline: the gap is an evaluation-generation artifact, not a solver deficit

FISPACT's published reference ran **TENDL-2017**; the CB2 ACTINV arm ran
**TENDL-2025**. Same-evaluation comparison (2,279 joined points):

| arm | median |ln C/E| | mean |ln C/E| |
|---|---|---|
| ACTINV + tendl-2017 | **0.1043** | 0.293 |
| FISPACT + tendl-2017 | 0.1053 | 0.291 |
| ACTINV + tendl-2025 | 0.1421 | 0.280 |
| ACTINV + eaf-2010 | 0.1404 | 0.267 |

On identical data ACTINV is at parity with FISPACT (marginally better on
median). The published deficit is almost entirely TENDL-2025 being a worse
FNS corpus than TENDL-2017 on this benchmark.

## Per-material confirmation (median |ln C/E|, measured-point pairs)

| material | ACTINV t2025 | ACTINV t2017 | FISPACT t2017 |
|---|---|---|---|
| W  | 0.611 | 0.397 | 0.373 |
| Pb | 0.539 | 0.215 | 0.219 |
| Mn | 0.282 | 0.085 | 0.084 |
| La | 0.568 | 0.378 | 0.362 |
| Lu | 0.633 | 0.459 | 0.435 |
| Cs | 0.463 | 0.269 | 0.267 |
| Yb | 0.473 | 0.275 | 0.286 |
| S  | 0.256 | 0.074 | 0.072 |
| Mo | ~0.17 (t2025) | — | 0.048 |

Every material where FISPACT "won" is closed or nearly closed by running
ACTINV on the same evaluation. ACTINV also wins outright where FISPACT has
catastrophic misses (per-material medians: In 3.82, Bi 1.46, Os 1.06, Ir 0.55
vs ACTINV 0.68/0.69/0.22/0.18).

## Where the deficit lives: 5-minute irradiation experiments

Within the bad materials the residual is concentrated in the short-irradiation
cases (dominated by minutes-half-life products); the 7-hour cases are
near-perfect on both codes. That isolates the mechanism to short-lived-channel
production cross sections — (n,p), (n,α) and isomeric (n,γ) branches — not to
decay data or long-lived inventory handling.

### Named channels (from stored per-nuclide inventories, first scored step)

- **Mn** — activity dominated by Cr-55 (Mn-55(n,p), T½≈3.5 min): TENDL-2025
  emits 2.78e6 Bq/g vs TENDL-2017's 2.09e6 (+33%); V-52 (n,α) +20%. The +0.30
  ln residual is the (n,p)/(n,α) channel regression upstream.
- **W** — dominated by the isomers W-185m1 and W-183m1 (W-184/W-182 (n,γ)
  isomeric branches). All three corpora and both solvers overpredict
  (~e^0.4–0.9); TENDL-2025 is worst (+29% on W-185m1 vs 2017). A shared
  isomer-branching weakness, worst in the newest evaluation.
- **Pb** — dominated by Pb-207m1 (Pb-208(n,2n) isomer). All codes underpredict
  the 5-min heat (~e^-0.8 ACTINV / ~e^-0.3 FISPACT); the 7-hour late-time tail
  underpredicts ~20× in **both** codes — a shared missing long-lived
  production channel (the Pb-210 → Po-210 chain is the candidate), not an
  ACTINV defect.

## Implications

1. The honest competitive claim is now: **solver parity with FISPACT-II on
   identical data**, with ACTINV strictly better on the tail (no catastrophic
   misses) and FISPACT tighter on a few moderate-residual materials.
2. TENDL-2025's FNS regression vs TENDL-2017 is upstream evidence worth
   reporting to the TENDL maintainers — same structure as the P25 defect
   census, quantified against measurements: ~8–10 materials, short-lived
   charged-particle-emission and isomeric branches, +20–33% channel excesses.
3. Default-corpus note: by FNS median, tendl-2017 (0.104) is the best shipped
   corpus; by pooled mean, eaf-2010 (0.267); the current default tendl-2025
   (0.142 median / 0.280 mean) is mid-pack on FNS but has the widest element
   coverage (113 vs 87/99) and is the current-generation evaluation. The
   corpus-per-material evidence table in `results/p46_eval_tables.json`
   already supports informed selection; no default change is argued here.
4. Parking item 4 is resolved to the extent a diagnosis can resolve it: the
   residual real questions are (a) the W isomer-branching overprediction
   shared across all evaluations and both solvers, and (b) the shared Pb
   late-time channel gap — both upstream-data questions, not solver physics.

## Provenance

- CB2 sealed record: `results/cb2_fns.json` (132 experiments, measured +
  FISPACT-4.0/TENDL-2017 published reference + ACTINV/TENDL-2025 arm).
- P46 sealed ledger: `results/p46_run_ledger.jsonl` (660 runs, five corpora)
  with per-point heat; stored outputs under `~/nuclear-data/p46-work/runs/`.
- Per-nuclide inventories read from the sealed-run `out.json` files
  (sha256 recorded per row in the ledger).
