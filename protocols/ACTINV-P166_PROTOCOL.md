# ACTINV-P166 — fission-product decay heat against the rest of the open CoNDERC fission tables

## Purpose

ACTINV's measured fission decay heat so far is P9: two U-235 thermal experiments from the IAEA CoNDERC fission
archive, the Dickens pulse (Oak Ridge) and the Yarnell 20,000 s irradiation (Los Alamos). Gauld's 2019 CoNDERC summary
report, which is open and already pinned by P9 as reference context, tabulates measured decay heat for further
experiments with their own facilities, detectors and fissile nuclides. This protocol scores ACTINV against every
measured point of those tables that the P9 model can represent. The question:

> With the P9 model unchanged (ENDF/B-VIII.0 independent yields at thermal energy, ENDF/B-VIII.0 decay data, constant
> fission rate), how close is ACTINV's calculated fission-product decay heat to the measured values of the IRT
> (Friesenhahn 1976 and 1979), UC Berkeley (Schrock 1978) and CEA Cadarache (Fiche 1976) experiments for U-233, U-235
> and Pu-239 thermal fission?

Accuracy is reported, not gated, as in P9. Whatever the numbers are, they are published in the handbook's validation
page and the competitive benchmark report. The protocol certifies the comparison, not a level of agreement.

What this does and does not add. It adds three laboratories, two fissile nuclides (U-233, Pu-239), irradiations from
1 s to 35 days, cooling times from 0.75 s to 1.5 × 10⁵ s, and separately measured beta and gamma heat. It tests
fission yields and decay data. It does not test activation cross sections, and it is not a second integral-activation
facility; that gap (FNS is the only activation corpus) stays open.

## Source and extraction (frozen before any ACTINV run on these data)

- **Report.** I. Gauld, *Data Compilation and Analysis of Fission Product Decay Heat Experiments*, IAEA CoNDERC summary
  report TAL-NAPC20190311-001 (December 2019), `~/nuclear-data/conderc-fission/references/Gauld_SummaryReport_2019.pdf`,
  SHA-256 `71f22abd8993f72656b00ae80bff02099bbb1bea8f8db4781e33b58f9d273f74` (the P9 pin).
- **Extraction.** `controls/p166_extract.py` reads Tables 1–11 through `pdftotext -layout` (poppler 26.01.0) and writes
  `results/p166/conderc_fission_tables.csv`, SHA-256
  `0160b82fedf141e71b81f7ff9ad1e26783ef77c999a22ff0b6d8db79759a0cb9`, 1,720 rows (one per measured point, channel and
  run), with `results/p166/extraction.json` as its report.
- **Transcription check.** Each table prints ORIGEN's calculated value and its ratio to the measurement (C/M). The
  extractor recomputes every printed ratio from the printed calculated and measured values and requires agreement
  within the rounding of the three printed numbers. All 800 printed ratios reproduce; one that did not would stop the
  extraction. Measured gamma and beta values have no printed ratio in Tables 2–7; they are scored only where gamma +
  beta equals the printed total within rounding. Sixteen rows fail that test (listed in `extraction.json`); their total
  is scored and their gamma and beta are not. Table 11's two rows with one empty measured column are pinned verbatim in
  the extractor, not inferred from token counts. In the Schrock tables a run with no value at a long cooling time is a
  blank on the left; the remaining values belong to the later runs.
- **Scope.**

  | Table | Dataset | Laboratory | Parent | Irradiation | Channels | Points |
  |---|---|---|---|---|---|---|
  | 1 | Friesenhahn 1976 | IRT (²⁵²Cf, water-moderated) | U-235 | 24 h | total, gamma, beta | 86 each |
  | 2 | Friesenhahn 1979 | IRT | Pu-239 | 1000 s | total (57), gamma, beta (55) | 167 |
  | 3 | Friesenhahn 1979 | IRT | Pu-239 | 24 h | total, gamma, beta | 74 each |
  | 4 | Friesenhahn 1979 | IRT | U-235 | 1000 s | total (72), gamma, beta (70) | 212 |
  | 5 | Friesenhahn 1979 | IRT | U-235 | 20,000 s | total (77), gamma, beta (73) | 223 |
  | 6 | Friesenhahn 1979 | IRT | U-235 | 24 h | total (81), gamma, beta (77) | 235 |
  | 7 | Friesenhahn 1979 | IRT | U-235 | 35 d | total (83), gamma, beta (79) | 241 |
  | 8 | Schrock 1978, runs 1 and 12 | UC Berkeley (GETR thermal column) | U-235 | 1 h | total | 25 |
  | 9 | Schrock 1978, runs 3, 4 and 9 | UC Berkeley | U-235 | 4 h | total | 58 |
  | 10 | Schrock 1978, runs 6 and 8 | UC Berkeley | U-235 | 22.35 h | total | 37 |
  | 11 | Fiche 1976 | CEA Cadarache (Zoé) | Pu-239, U-233 | pulse (1 s) | total | 21 + 21 |

- **Excluded, with the compiler's reasons.** Table 12, Johnson 1965 (Pu-239 fast fission in Dounreay): fission counts
  inferred rather than measured, unreported minor isotopes, a fast spectrum the P9 model does not represent, and the
  compiler's own statement that the experiment needs more information before it is a benchmark. Gunst 1975: no
  tabulated data and, per the compiler, insufficient irradiation history to model. The P9 Dickens and Yarnell sets are
  not rescored; their P9 numbers stand and are quoted beside these.

## Model (the P9 model, unchanged)

`controls/p166_harness.py`, per dataset and parent:

- A one-group library holding only the parent's MT=18 loss row and its fission-product marker (the P9 layout), with a
  nominal 1 b cross section and a 10²⁶ n cm⁻² s⁻¹ flux (100 fissions per parent atom per second; trace mode is
  linear in the flux, so only the parent background's share depends on it); trace mode holds the parent constant, so the fission rate is
  constant through the irradiation and the cross section cancels in the per-fission normalization. Fission products
  carry no reactions: neutron capture on fission products is not modelled (the compiler's ORIGEN models ran at the
  flux of a 6.242 × 10¹² fissions/s source, where it is also negligible).
- ENDF/B-VIII.0 independent yields (MF=8/MT=454) at the fixed energy 0.0253 eV for the parent:
  `nfy-092_U_233.endf` `8e7e7e1d174471d457bb66c52d66e188beb6bc40eead968136fe4ab59aa588cb`,
  `nfy-092_U_235.endf` `9e1320293a544fc03f33f804a15a9e3ccc3be026552ee6dbc03b8d3e24615e41`,
  `nfy-094_Pu_239.endf` `a2713aa6d0283b879040a07fa29d3e99b2d660b619c6d11a2a71aa9d5c5a2605`, from the archive
  `92c5371fdb21eecf4989f48828671b904186abc6386b3d7510c8fcee2ee5ffcf` (the P9 pin).
- ENDF/B-VIII.0 decay data as the only decay source,
  `6f04cf009086c179021f243a58dadc2d5bb078de5ba39c4fe46ccad77d228ddb` (the P9 configuration; products missing from it
  are ledgered as leakage).
- Pure parent nuclide. Sample impurities (U-238 and U-234 in the uranium, Pu-240 and Pu-241 in the plutonium) are not
  modelled; their fission share is below 0.1% in a thermal field except for unreported Pu-241 in the Fiche sample.
- The schedule is the registered irradiation (constant fission rate) followed by cooling to every measured time.
  A second run with the flux off throughout gives the parent's own decay heat, which is subtracted channel by channel.
- Normalization: Tables 1–10 are power divided by the constant fission rate (MeV per fission); Table 11 is power divided
  by the fissions of the 1 s irradiation (MeV s⁻¹ per fission), which is the compiler's ORIGEN model of the Fiche data.
- Binary: the ACTINV 1.7.0 release-profile build made for P163 from its registration commit d6f516b (ledger 275),
  `186cc2587bb61766b3b3c426c61cd44f00ed472401026dea2ca3dd595c8d1194`. The `crates/` tree of the evidence commit must
  equal d6f516b's (`git rev-parse d6f516b:crates`); this protocol changes no ACTINV source.
- Runs inside the bounded scope from AGENTS.md (6 GiB, no swap, 128 tasks, 2 CPUs).

## Development runs

Development runs before registration used only data P9 had already scored: `p166_harness.py --p9-reproduction`
re-scores P9's Yarnell 20,000 s set through this harness. The first try stopped because the one-group edges were
[0, 1] eV, which ACTINV now rejects; they are [1, 3] eV (immaterial with fixed-energy yields). At a flux of 10²⁰ the
geometric-mean C/E was 0.98451141684 over 79 points, against P9's recorded 0.98451141694, with a parent-background
fraction of 3 × 10⁻¹¹ and exact rate closure; at the registered 10²⁶ it was the same to eleven digits, with a background
fraction of 3 × 10⁻¹⁷. The flux was raised because Pu-239's own alpha heat would otherwise be about 2% of the Fiche
pulse signal at 10⁵ s. No ACTINV run has been made on any P166 dataset before this registration.

## Scoring and the registered summary

Per scored point: C/E, ln C/E, and the residual over the measured 1σ (the table's σ %, which for Friesenhahn 1979 is the
sum of the reported systematic and random components). For ORIGEN, the same statistics from the printed calculated
values over the same points, labelled as ORIGEN (SCALE 6.1.3) with ENDF/B-VII.0 yields and ENDF/B-VII.1 decay data.

`controls/check_p166.py` writes to `results/p166_verdict.json`, per dataset and channel, per parent and laboratory, and
pooled:

- point count; geometric-mean C/E; median, 90th-percentile and maximum |ln C/E|; the fraction of points within 1σ and
  within 2σ;
- the same for ORIGEN on the same points.

Pooled views, all registered now:

- **Primary:** every scored total-heat point (692 points: Tables 1–11).
- Gamma and beta separately (Friesenhahn only).
- By parent (U-233, U-235, Pu-239) and by laboratory.
- **Secondary:** the primary view with Schrock points restricted to 600–10,000 s cooling, the window in which the
  compiler reports that experiment as reliable.

The verdict file also carries the allowed sentences, each filled from the computed numbers and nothing else:

1. "Against {n} measured total decay-heat points from {k} fission decay-heat experiments at IRT, UC Berkeley and CEA
   Cadarache (U-233, U-235 and Pu-239 thermal fission), ACTINV with ENDF/B-VIII.0 yields and decay data has a median
   |ln C/E| of {m} and a geometric-mean C/E of {g}."
2. "On the same points, ORIGEN with ENDF/B-VII.0 yields and ENDF/B-VII.1 decay data (Gauld, 2019) has a median
   |ln C/E| of {m_o} and a geometric-mean C/E of {g_o}." This sentence compares two codes with two different data
   sets; it may only appear with sentence 1 and with the data named.
3. One sentence per parent: "For {parent}, ACTINV's median |ln C/E| is {m} over {n} points."
4. "ACTINV's separately measured gamma and beta heat (Friesenhahn) have median |ln C/E| of {m_g} and {m_b}."

Numbers in the sentences are rounded to three decimals. No sentence may say that ACTINV is more accurate than ORIGEN
or another code; the ORIGEN figures are context for the data, not a code ranking.

## Gates

- **G0.** This protocol's SHA-256 (and any amendment's) is in `protocols/protocol_hash.txt`.
- **G1.** Inputs and environment: the record's report, table CSV, yield, decay and archive hashes equal the pins above;
  when the report is present, re-extraction reproduces the CSV byte for byte and `extraction.json` reports 800 printed
  ratio checks, no failures and the 16 listed anomalies; the binary SHA-256 is the one above; the record's `crates`
  tree equals d6f516b's; the tracked tree was clean; the harness's cgroup shows at most 6 GiB, no swap, at most 128
  tasks and a CPU quota of at most 200%.
- **G2.** Model fidelity, per run: trace mode; one yield selection for the right parent at exactly 0.0253 eV, not
  clamped, with an effective yield sum within 10⁻⁶ of 2; relative fission-rate closure ≤ 10⁻⁹; every step at its
  registered cooling time to 10⁻⁹ relative; the parent background at most 10⁻⁶ of the heat in every channel.
- **G3.** Coverage: every CSV row is scored exactly once, with a finite positive C/E and a positive σ; per-dataset counts
  equal the table above; no point is dropped.
- **G4.** The summary and sentences are recomputed by the checker from the scored points and equal the verdict file.
- **G5.** CI green on the pushed evidence commit.

## Verdict

P166-PASS if G0–G5 hold, whatever the agreement. One repair round, registered as an amendment first.

## Not claimed

- Anything about activation cross sections or a second integral-activation facility.
- Fast fission (Johnson), long reactor irradiations with transmutation (Gunst), or any fissile nuclide other than
  U-233, U-235 and Pu-239.
- That ACTINV is more accurate than ORIGEN, FISPACT-II or any other code. The ORIGEN column uses older data.
- That the extracted values are the original laboratory reports; they are the compiler's tables, checked against the
  compiler's own printed ratios.
