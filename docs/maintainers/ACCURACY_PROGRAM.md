# Accuracy program: beyond FISPACT-II parity

Owner direction, 2026-10-30: matching FISPACT-II's accuracy is not enough. ACTINV must be measurably more accurate
where that is achievable, and OpenMC users need a reason to choose it beyond speed.

## Where we stand

On clean benchmark experiments, ACTINV and FISPACT-II agree with measurement to about the measurement noise when
both use the same data:

| Evidence | ACTINV | Reference code or library on the same points |
|---|---|---|
| P17 IRDFF-II spectrum-averaged cross sections | median \|ln C/E\| 0.014–0.024 | — |
| P166 fission decay heat, 692 points | 0.046, geometric mean 0.960 | ORIGEN 0.042, 0.962 |
| P167 NPI Řež reaction rates, 29 points | 0.038, geometric mean 1.023 | ENDF/B-VIII.0 0.047, JEFF-3.3 0.051, JENDL-4 0.057, IRDFF-II 0.030 |

The noise floor limits scatter, not bias. Three gaps are open:

- **Bias.** Both ACTINV and ORIGEN are about 4% low on fission decay heat. In Řež, the residuals are 25% larger than
  the measurement uncertainties explain (root-mean-square z = 1.25).
- **Data.** FISPACT-II ships TENDL and EAF-2010. ACTINV can ship better data, if it can show the gain on held-out
  measurements.
- **Workflow error in real problems.** In shutdown-dose work, most of the error is outside the evaluated data:
  - group collapse of the spectrum, and resonance self-shielding;
  - flux tally noise;
  - mixed materials in mesh cells;
  - decay-gamma dose approximations;
  - OpenMC's reactor-oriented depletion chain, which misses activation reactions.

## Stages

Each stage is registered as a protocol before any scored run, as usual. A gain counts only on data that did not
choose or tune it.

1. **Limits map.** Push ACTINV into regimes where activation codes disagree, against closed-form solutions,
   ALARA 2.9.2 (built at `~/nuclear-data/alara-2.9.2-build`) or measurements:
   - very high fluence (W→Re/Os over a DEMO lifetime);
   - ns pulses and pulse trains;
   - 10⁹-year cooling and sub-ms times;
   - incident energies above 20 MeV;
   - very high flux;
   - looped actinide chains;
   - sequential charged-particle reactions;
   - ppb traces in kg-scale material.

   Output: where ACTINV holds, where it breaks, fixes for what breaks.
2. **Decay-heat bias.** Find why both ACTINV and ORIGEN are about 4% low. Candidates:
   - pandemonium-corrected (TAGS) decay data;
   - fission-yield library choice;
   - experimental normalisation.

   Choose the remedy on one set (for example P9 Dickens/Yarnell) and score it on the other (the P166 tables), or the
   reverse.
3. **Best-estimate activation library.** Per-reaction evaluation selection and/or a covariance-based (GLLS)
   adjustment to the integral data:
   - IRDFF-II SACS;
   - FNS;
   - Řež;
   - the fission tables.

   Validate leave-one-facility-out. Check every source evaluation's licence for redistribution before shipping.
4. **R2S workflow accuracy with OpenMC.** Continuous-energy reaction rates for the dominant reactions, within-group
   spectrum correction, and material-aware self-shielding. Score against a measured shutdown-dose experiment (SINBAD
   FNG once the licence arrives; the request was filed 2026-10-30), in comparison with OpenMC's own depletion R2S.
   Target claim, if earned: faster *and* closer to the measurement than OpenMC alone.

Stages 1–3 do not need SINBAD.

## Status

- 2026-10-30: program agreed. Research started:
  - code limits audit;
  - open data for the extreme regimes;
  - decay-heat bias diagnosis;
  - best-estimate library feasibility.
- 2026-10-30: research findings (unregistered, they only shape the protocols):
  - **FNS decay heat.** In the 21-experiment head-to-head (`results/FNS_ENDF8_HEADTOHEAD.md`):
    - TENDL-2017 has a median |ln C/E| of 0.086 with ACTINV and 0.085 with FISPACT-II.
    - Shipped TENDL-2025 has 0.141. Its bias is smaller, but its per-element misses are larger (Pb, Mn, W, S).
    - OpenMC depletion with ENDF/B-VIII.1 has 0.181.
    - A per-element library choice gains about 17% in-sample and about 6% held out (rough split).
  - **Fission decay heat.** The bias is at short cooling: 6–8% low below 100 s, about 0 after 1000 s. ACTINV and
    ORIGEN agree within 0.5% at every time, so the solver is not the cause. Part of it is the Schrock data. The rest
    is likely short-lived fission-product decay data (Rb-92/94/95, Br-87/88/89, Cs-142, Sb-134), where ENDF/B-VIII.0
    and JEFF-3.3 differ widely.
  - **Limits.**
    - Flux above an evaluation's top energy is dropped without a warning or a ledger field.
    - Self-shielding and the spectrum are fixed at the declared composition, as in FISPACT-II.
    - 10⁹-year cooling, ns pulses at ICF flux and 10¹⁶ n/cm²/s are untested.
    - The tungsten target is the measured Re/Os in HFR-irradiated W: Lloyd et al. 2019, open as UKAEA-CCFE-PR(19)42,
      with Re-187 1.05% measured against FISPACT-II's 1.34%.
  - **UKAEA-R(18)004.** It tabulates about 844 measured effective cross sections with TENDL-2017 C/E, but no
    spectra, and the extracts need UKAEA consent. Only Cf-252 and Jülich d-Be are reproducible from open sources.
    The owner is asking UKAEA for consent.
- Protocol numbers:

  | Protocol | Subject |
  |---|---|
  | P168 | limits map |
  | P169 | short-time fission decay heat, choosing on CoNDERC and scoring on Dickens/Yarnell |
  | P170 | FNS per-element library selection, choosing on the 2000 campaign and scoring on 1996 and 7-hour |
  | P171 | ACTINV against OpenMC depletion on FNS decay heat |
