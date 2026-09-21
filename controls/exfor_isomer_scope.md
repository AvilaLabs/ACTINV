# Isomer-split defect campaign — scope (draft)

## Why this axis

The FNS campaign proved decay-heat accuracy is library-bounded: ACTINV and
FISPACT agree per-nuclide to ~2%, so the only locally-winnable axis is
**finding and repairing library defects the frozen reference still carries**.
The exhausted screens covered cross-section *magnitude* anomalies. The
unexplored defect class is **isomeric splitting**: the m/g (and m1/m2)
branching fractions of production cross sections.

Two reasons it is the most promising remaining surface:

1. **Isomer splits are the weakest part of TENDL.** They come from TALYS
   spin-parity state-density modeling — approximate by construction — and are
   the least-constrained evaluated quantity. A real defect population plausibly
   exists; nothing in the pilot screened for it.
2. **Split errors change the decay-heat time course.** A wrong m/g ratio moves
   activity between half-lives — exactly the slope-dominated C/E failure shape
   in the pilot set, where metastables dominate: La (Cs136m1, Ba137m1,
   Cs135m1, Ba136m1, Cs134m1), In (In116m1, In112m1, In115m1), Dy (Dy165m1),
   Bi (Tl206m1), Pb (Pb204m1), Tb (Tb158m1). Six of the ten worst fails carry
   metastable dominants.

## Data (all local or public)

- **TENDL-2017 pointwise isomer cross sections**: already in the corpus
  (MF=10 rows per lfs) — `dump processed-xs` / raw ENDF reads give σm, σg(E).
- **EXFOR measured isomer ratios**: public, API reachable
  (`https://nds.iaea.org/exfor/x4dat?Target=..&Reaction=..&Quantity=SIG&op=c4`,
  `x4list` for dataset search; LANL `exfor_client` available if useful).
  Isomer-resolved measurements exist mostly at thermal and ~14 MeV for
  (n,γ), (n,2n), (n,n′) on many targets.
- **TENDL-2025 + IRDFF-II** as the documented-alternative source (repair
  provenance must be a *documented* eval or a provable bound violation —
  fitting to EXFOR is assimilation, a different claim class, flagged
  separately if used).

## Plan

1. **Harvest**: pull EXFOR datasets for isomer-resolved neutron reactions on
   all ~73 foil elements + a general sweep (bounded, ~few hundred datasets).
   Extract (target, reaction, E, σm/σg or σm, σg, uncertainty, dataset ID).
2. **Predict**: compute TENDL-2017 σm/σg at each measured energy from the
   pointwise evals (energy-interpolated, not collapsed — no spectrum needed).
3. **Classify**: C/E distribution per reaction; flag |log C/E| > log 2
   systematically, > log 10 as defect candidates. Cross-check each flag
   against TENDL-2025 (documented-fix = repairable) and IRDFF (if covered).
4. **Repair + verify**: same discipline as In-115 — patch, bitwise-record,
   re-run the affected scored experiment, holdout-check other cooling points.
5. **Dossier tie-back**: for the six pilot fails with metastable dominants,
   check whether the flagged splits are on their production channels — a
   flagged split on a dominant channel is a repair candidate for that
   experiment; a split that fixes nothing scores as breadth-only.

## Honest ceiling

- Still library-level: a perfect split repair is data curation — copyable in
  principle, but the frozen FISPACT-4 reference does not carry it, and the
  screening machinery that finds these is the product claim.
- Not every flagged split touches a benchmark: expect the repair→score delta
  to land on the six metastable-dominated fails at most; the rest is catalog
  breadth (a defect census across the library's isomer space).
- EXFOR caveats: heterogeneous energies/units, some datasets are ratio-of-
  ratios, uncertainties vary; curation effort is the real cost.

## Deliverables

- `results/exfor_isomer_cards.json` — measured vs predicted split table
- flagged-split ledger with per-row provenance (dataset IDs)
- repairs applied with scored-rerun deltas, same acceptance rule as In-115
- aggregate-metric update if any scored experiment moves
